"""Chat orchestration: RAG -> grounding guard -> existing Qwen worker -> verification."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from helpers import classify_intent

from app.config import Settings
from app.errors import (
    DailyLimitError,
    RetrievalError,
    SessionBusyError,
    WorkerResultError,
    WorkerTimeoutError,
)
from app.schemas import (
    ChatRequest,
    ChatResponse,
    Message,
    RetrievalInfo,
    SourceReference,
    Verification,
)
from app.services.fast_answers import try_fast_answer
from app.services.grounding import (
    FALLBACK_MESSAGE,
    answer_uses_records,
    assess_retrieval,
    build_retrieval_query,
    grounding_policy,
    general_policy,
    is_followup,
    is_refusal,
    is_university_query,
    verify_answer,
)
from app.services.retrieval import RetrievalResult, RetrievalService
from app.services.session_store import SessionStore, new_message_id
from app.services.rate_limit import current_client_key
from app.services.worker_client import WorkerClient

logger = logging.getLogger("kbn.chat")

VERIFICATION_LABELS = {
    "verified_kb": "Verified against KBN University records",
    "general_unverified": "General knowledge - not verified against KBN University records",
    "unavailable": "No verified KBN University information found",
    "web_unverified": "Web-sourced - not verified against KBN University records",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _verification(status: str, reason: str | None = None) -> Verification:
    return Verification(status=status, label=VERIFICATION_LABELS[status], reason=reason)


def _user_message(content: str) -> Message:
    return Message(id=new_message_id(), role="user", content=content, created_at=_now())


def _assistant_message(
    *,
    content: str,
    sources: list[SourceReference],
    grounded: bool,
    verification: Verification,
    persona: str,
    response_time_ms: int,
    llm_time_seconds: float | None = None,
    tokens_used: int | None = None,
    max_tokens: int | None = None,
    fast_path: str | None = None,
    cached: bool = False,
) -> Message:
    return Message(
        id=new_message_id(),
        role="assistant",
        content=content,
        created_at=_now(),
        sources=sources,
        grounded=grounded,
        verification=verification,
        persona=persona,
        response_time_ms=response_time_ms,
        llm_time_seconds=llm_time_seconds,
        tokens_used=tokens_used,
        max_tokens=max_tokens,
        fast_path=fast_path,
        cached=cached,
    )


class ChatService:
    def __init__(
        self,
        settings: Settings,
        store: SessionStore,
        retrieval: RetrievalService,
        worker: WorkerClient,
        budget=None,
    ) -> None:
        self.budget = budget
        self.settings = settings
        self.store = store
        self.retrieval = retrieval
        self.worker = worker
        self._locks: dict[str, asyncio.Lock] = {}
        self._inflight: set[str] = set()
        self._background_tasks: set[asyncio.Task] = set()
        # Exact standalone questions only; follow-ups depend on session history.
        self._answer_cache: dict[str, tuple[float, dict]] = {}

    async def _submit(self, **kwargs):
        """Submit to the LLM, spending one unit of the daily budget (fast paths never get here).

        Budget calls may hit Postgres, so they run in a thread to keep the event loop free.
        """
        key = current_client_key.get()
        if self.budget is not None and not await asyncio.to_thread(self.budget.try_consume, None, key):
            raise DailyLimitError(
                "The assistant has reached its daily limit. Please try again tomorrow, "
                "or contact the university office directly."
            )
        try:
            return self.worker.submit(**kwargs)
        except Exception:
            if self.budget is not None:
                await asyncio.to_thread(self.budget.refund, key)  # nothing was sent to the model
            raise

    def is_busy(self, session_id: str) -> bool:
        lock = self._locks.get(session_id)
        return session_id in self._inflight or (lock is not None and lock.locked())

    # ------------------------------------------------------------------ public
    async def chat(self, request: ChatRequest) -> ChatResponse:
        # Existence (404) is checked by the route's ownership check and by the history read below.
        lock = self._locks.setdefault(request.session_id, asyncio.Lock())
        if lock.locked() or request.session_id in self._inflight:
            raise SessionBusyError(
                "This conversation is already waiting for an answer. "
                "Please wait for it to finish before sending another message."
            )

        keep_inflight = False
        async with lock:
            if request.session_id in self._inflight:
                raise SessionBusyError(
                    "This conversation is already waiting for an answer."
                )
            self._inflight.add(request.session_id)
            late = {"value": False}
            try:
                return await self._handle(request, late)
            except WorkerTimeoutError:
                keep_inflight = bool(late["value"])
                raise
            except asyncio.CancelledError:
                keep_inflight = bool(late["value"])
                raise
            finally:
                if not keep_inflight:
                    self._inflight.discard(request.session_id)

    async def shutdown(self) -> None:
        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)

    # ------------------------------------------------------------------ core
    async def _handle(self, request: ChatRequest, late: dict) -> ChatResponse:
        started = time.perf_counter()
        session_id = request.session_id
        message = request.message
        history = self.store.recent_user_messages(session_id, limit=3)
        history_text = " ".join(history)[-2000:]

        university = is_university_query(message) or (
            is_followup(message) and is_university_query(history_text)
        )

        if not university:
            return await self._handle_general(request, started, late)

        retrieval_query = build_retrieval_query(message, history)
        retrieval_started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(self.retrieval.retrieve, retrieval_query),
                timeout=self.settings.retrieval_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise RetrievalError("Knowledge base retrieval timed out.") from exc
        retrieval_ms = int((time.perf_counter() - retrieval_started) * 1000)

        decision = assess_retrieval(message, retrieval_query, result.context)
        retrieval_info = RetrievalInfo(
            attempted=True,
            used=decision.allowed,
            query=retrieval_query,
            n_results=result.n_results,
            result_count=result.result_count,
            duration_ms=retrieval_ms,
            reason=decision.reason,
        )

        cache_key = self._cache_key(message)
        if cache_key:
            cached_payload = self._cache_get(cache_key)
            if cached_payload is not None:
                return self._complete_cached(
                    session_id=session_id,
                    user_content=message,
                    started=started,
                    payload=cached_payload,
                    retrieval_info=retrieval_info,
                )

        if not decision.allowed:
            logger.info("Grounding guard blocked query=%r reason=%s", message[:120], decision.reason)
            if self.settings.enable_web_fallback:
                web_context, web_sources = await self._retrieve_web(message)
                if web_context.strip():
                    return await self._answer_with_web(
                        request=request,
                        started=started,
                        web_context=web_context,
                        web_sources=web_sources,
                        retrieval_info=retrieval_info,
                        late=late,
                    )
            return self._complete_fallback(
                session_id=session_id,
                user_content=message,
                started=started,
                retrieval_info=retrieval_info,
                reason=decision.reason,
            )

        if self.settings.fast_answers_enabled:
            fast = try_fast_answer(
                message,
                result.context,
                result.sources,
                self.retrieval.documents_for(result.sources),
            )
            if fast is not None:
                supported, reason = verify_answer(
                    fast.answer, result.context, self.retrieval.allowed_urls
                )
                if supported:
                    logger.info(
                        "Fast record-grounded answer: intent=%s query=%r",
                        fast.intent,
                        message[:120],
                    )
                    return self._complete_fast(
                        session_id=session_id,
                        user_content=message,
                        started=started,
                        answer=fast.answer,
                        intent=fast.intent,
                        sources=result.sources,
                        retrieval_info=retrieval_info,
                    )
                logger.warning(
                    "Fast answer rejected by verification (%s); using the LLM.", reason
                )

        persona, base_prompt = classify_intent(message)
        system_prompt = f"{base_prompt}\n\n{grounding_policy()}"
        worker_key = self.store.worker_key_for(session_id)
        future = await self._submit(
            worker_key=worker_key,
            query=message,
            kb_knowledge=result.context,
            web_context="",
            persona_name=persona,
            system_prompt=system_prompt,
        )

        try:
            worker_result = await self.worker.await_result(
                future, timeout_seconds=self.settings.request_timeout_seconds
            )
        except WorkerTimeoutError:
            late["value"] = True
            self._schedule_finalize(
                session_id=session_id,
                future=future,
                user_content=message,
                started=started,
                university=True,
                context=result.context,
                sources=result.sources,
                retrieval_info=retrieval_info,
                persona=persona,
            )
            raise
        except asyncio.CancelledError:
            late["value"] = True
            self._schedule_finalize(
                session_id=session_id,
                future=future,
                user_content=message,
                started=started,
                university=True,
                context=result.context,
                sources=result.sources,
                retrieval_info=retrieval_info,
                persona=persona,
            )
            raise

        return self._complete_grounded(
            session_id=session_id,
            user_content=message,
            started=started,
            worker_result=worker_result,
            context=result.context,
            sources=result.sources,
            retrieval_info=retrieval_info,
            persona=persona,
        )

    async def _handle_general(self, request: ChatRequest, started: float, late: dict) -> ChatResponse:
        persona, base_prompt = classify_intent(request.message)
        system_prompt = f"{base_prompt}\n\n{general_policy()}"
        worker_key = self.store.worker_key_for(request.session_id)
        future = await self._submit(
            worker_key=worker_key,
            query=request.message,
            kb_knowledge="",
            web_context="",
            persona_name=persona,
            system_prompt=system_prompt,
        )
        retrieval_info = RetrievalInfo(
            attempted=False,
            used=False,
            reason="General question; no KBN University retrieval required.",
        )
        try:
            worker_result = await self.worker.await_result(
                future, timeout_seconds=self.settings.request_timeout_seconds
            )
        except WorkerTimeoutError:
            late["value"] = True
            self._schedule_finalize(
                session_id=request.session_id,
                future=future,
                user_content=request.message,
                started=started,
                university=False,
                context="",
                sources=[],
                retrieval_info=retrieval_info,
                persona=persona,
            )
            raise
        except asyncio.CancelledError:
            late["value"] = True
            self._schedule_finalize(
                session_id=request.session_id,
                future=future,
                user_content=request.message,
                started=started,
                university=False,
                context="",
                sources=[],
                retrieval_info=retrieval_info,
                persona=persona,
            )
            raise

        return self._complete_general(
            session_id=request.session_id,
            user_content=request.message,
            started=started,
            worker_result=worker_result,
            retrieval_info=retrieval_info,
            persona=persona,
        )

    # ------------------------------------------------------------------ speed layer
    def _cache_key(self, message: str) -> str | None:
        text = message.strip()
        if re.match(
            r"^(and|what about|how about|also|tell me more|more about)\b", text, re.I
        ):
            return None
        if len(text.split()) <= 8 and re.search(
            r"\b(it|its|he|him|his|she|her|they|them|their|this|that)\b", text, re.I
        ):
            return None
        return re.sub(r"\s+", " ", text.lower())

    def _cache_get(self, key: str) -> dict | None:
        item = self._answer_cache.get(key)
        if item is None:
            return None
        stored_at, payload = item
        if time.monotonic() - stored_at > self.settings.answer_cache_ttl_seconds:
            self._answer_cache.pop(key, None)
            return None
        return payload

    def _cache_put(self, key: str | None, assistant: Message) -> None:
        if not key:
            return
        self._answer_cache[key] = (
            time.monotonic(),
            {
                "content": assistant.content,
                "sources": [source.model_dump(mode="json") for source in assistant.sources],
                "grounded": assistant.grounded,
                "verification": (
                    assistant.verification.model_dump(mode="json")
                    if assistant.verification
                    else None
                ),
                "persona": assistant.persona or "KBN University Helpdesk",
                "fast_path": assistant.fast_path,
            },
        )
        while len(self._answer_cache) > self.settings.answer_cache_max_entries:
            self._answer_cache.pop(next(iter(self._answer_cache)))

    def _complete_cached(
        self,
        *,
        session_id: str,
        user_content: str,
        started: float,
        payload: dict,
        retrieval_info: RetrievalInfo,
    ) -> ChatResponse:
        sources = [
            SourceReference.model_validate(source) for source in payload.get("sources", [])
        ]
        verification = (
            Verification.model_validate(payload["verification"])
            if payload.get("verification")
            else _verification("verified_kb")
        )
        assistant = _assistant_message(
            content=str(payload.get("content") or ""),
            sources=sources,
            grounded=bool(payload.get("grounded")),
            verification=verification,
            persona=str(payload.get("persona") or "KBN University Helpdesk"),
            response_time_ms=int((time.perf_counter() - started) * 1000),
            fast_path=payload.get("fast_path"),
            cached=True,
        )
        user_message = _user_message(user_content)
        self.store.append_exchange(session_id, user_message, assistant)
        return ChatResponse(
            session_id=session_id,
            user_message=user_message,
            assistant_message=assistant,
            sources=sources,
            grounded=assistant.grounded,
            verification=verification,
            persona=assistant.persona or "KBN University Helpdesk",
            response_time_ms=assistant.response_time_ms or 0,
            retrieval=retrieval_info,
            created_at=_now(),
            fast_path=payload.get("fast_path"),
            cached=True,
        )

    def _complete_fast(
        self,
        *,
        session_id: str,
        user_content: str,
        started: float,
        answer: str,
        intent: str,
        sources: list[SourceReference],
        retrieval_info: RetrievalInfo,
    ) -> ChatResponse:
        verification = _verification(
            "verified_kb",
            "Answer extracted directly from the retrieved KBN University records.",
        )
        assistant = _assistant_message(
            content=answer,
            sources=sources,
            grounded=True,
            verification=verification,
            persona="KBN University Helpdesk",
            response_time_ms=int((time.perf_counter() - started) * 1000),
            fast_path=intent,
        )
        user_message = _user_message(user_content)
        self.store.append_exchange(session_id, user_message, assistant)
        self._cache_put(self._cache_key(user_content), assistant)
        return ChatResponse(
            session_id=session_id,
            user_message=user_message,
            assistant_message=assistant,
            sources=sources,
            grounded=True,
            verification=verification,
            persona="KBN University Helpdesk",
            response_time_ms=assistant.response_time_ms or 0,
            retrieval=retrieval_info,
            created_at=_now(),
            fast_path=intent,
        )

    # ------------------------------------------------------------------ completion
    def _complete_grounded(
        self,
        *,
        session_id: str,
        user_content: str,
        started: float,
        worker_result: dict,
        context: str,
        sources: list[SourceReference],
        retrieval_info: RetrievalInfo,
        persona: str,
    ) -> ChatResponse:
        answer = str(worker_result.get("answer") or "").strip()
        persona = str(worker_result.get("persona") or persona)

        if is_refusal(answer):
            answer = FALLBACK_MESSAGE
            verification = _verification(
                "unavailable",
                "The model reported that the verified records do not contain the answer.",
            )
            final_sources: list[SourceReference] = []
            grounded = False
        else:
            ok, reason = verify_answer(answer, context, self.retrieval.allowed_urls)
            if not ok:
                logger.warning(
                    "Answer verification rejected a grounded response: %s | answer=%r",
                    reason,
                    answer[:300],
                )
                answer = FALLBACK_MESSAGE
                verification = _verification("unavailable", reason)
                final_sources = []
                grounded = False
            elif not answer_uses_records(answer, context, user_content):
                # Passed the fact checks only because it makes no checkable claims
                # (e.g. an off-topic follow-up). Do not present it as record-backed.
                verification = _verification(
                    "general_unverified",
                    "The answer does not rely on the university records.",
                )
                final_sources = []
                grounded = False
            else:
                verification = _verification("verified_kb", reason)
                final_sources = sources
                grounded = True

        response_time_ms = int((time.perf_counter() - started) * 1000)
        llm_time = worker_result.get("time")
        tokens_used = worker_result.get("tokens_used")
        max_tokens = worker_result.get("max_tokens")
        assistant = _assistant_message(
            content=answer,
            sources=final_sources,
            grounded=grounded,
            verification=verification,
            persona=persona,
            response_time_ms=response_time_ms,
            llm_time_seconds=float(llm_time) if llm_time is not None else None,
            tokens_used=int(tokens_used) if tokens_used is not None else None,
            max_tokens=int(max_tokens) if max_tokens is not None else None,
        )
        user_message = _user_message(user_content)
        self.store.append_exchange(session_id, user_message, assistant)
        self._cache_put(self._cache_key(user_content), assistant)
        return ChatResponse(
            session_id=session_id,
            user_message=user_message,
            assistant_message=assistant,
            sources=final_sources,
            grounded=grounded,
            verification=verification,
            persona=persona,
            response_time_ms=response_time_ms,
            retrieval=retrieval_info,
            created_at=_now(),
        )

    def _complete_general(
        self,
        *,
        session_id: str,
        user_content: str,
        started: float,
        worker_result: dict,
        retrieval_info: RetrievalInfo,
        persona: str,
    ) -> ChatResponse:
        answer = str(worker_result.get("answer") or "").strip()
        persona = str(worker_result.get("persona") or persona)
        verification = _verification(
            "general_unverified",
            "This answer is general knowledge, not KBN University record data.",
        )
        response_time_ms = int((time.perf_counter() - started) * 1000)
        llm_time = worker_result.get("time")
        tokens_used = worker_result.get("tokens_used")
        max_tokens = worker_result.get("max_tokens")
        assistant = _assistant_message(
            content=answer,
            sources=[],
            grounded=False,
            verification=verification,
            persona=persona,
            response_time_ms=response_time_ms,
            llm_time_seconds=float(llm_time) if llm_time is not None else None,
            tokens_used=int(tokens_used) if tokens_used is not None else None,
            max_tokens=int(max_tokens) if max_tokens is not None else None,
        )
        user_message = _user_message(user_content)
        self.store.append_exchange(session_id, user_message, assistant)
        return ChatResponse(
            session_id=session_id,
            user_message=user_message,
            assistant_message=assistant,
            sources=[],
            grounded=False,
            verification=verification,
            persona=persona,
            response_time_ms=response_time_ms,
            retrieval=retrieval_info,
            created_at=_now(),
        )

    async def _answer_with_web(
        self,
        *,
        request: ChatRequest,
        started: float,
        web_context: str,
        web_sources: list[SourceReference],
        retrieval_info: RetrievalInfo,
        late: dict,
    ) -> ChatResponse:
        persona, base_prompt = classify_intent(request.message)
        system_prompt = (
            f"{base_prompt}\n\n"
            "The following context was scraped from the live web. Use it carefully. "
            "Do not invent identifiers, dates, fees or policies. If the web context does "
            "not answer the question, say so.\n"
        )
        worker_key = self.store.worker_key_for(request.session_id)
        future = await self._submit(
            worker_key=worker_key,
            query=request.message,
            kb_knowledge="",
            web_context=web_context,
            persona_name=persona,
            system_prompt=system_prompt,
        )
        try:
            worker_result = await self.worker.await_result(
                future, timeout_seconds=self.settings.request_timeout_seconds
            )
        except (WorkerTimeoutError, asyncio.CancelledError):
            late["value"] = True
            self._schedule_finalize(
                session_id=request.session_id,
                future=future,
                user_content=request.message,
                started=started,
                university=True,
                context=web_context,
                sources=web_sources,
                retrieval_info=retrieval_info,
                persona=persona,
                web=True,
            )
            raise

        answer = str(worker_result.get("answer") or "").strip()
        persona = str(worker_result.get("persona") or persona)
        ok, reason = verify_answer(answer, web_context, self.retrieval.allowed_urls)
        if not ok:
            answer = FALLBACK_MESSAGE
            verification = _verification("unavailable", reason)
            final_sources: list[SourceReference] = []
        else:
            verification = _verification("web_unverified", reason)
            final_sources = web_sources
        response_time_ms = int((time.perf_counter() - started) * 1000)
        llm_time = worker_result.get("time")
        assistant = _assistant_message(
            content=answer,
            sources=final_sources,
            grounded=False,
            verification=verification,
            persona=persona,
            response_time_ms=response_time_ms,
            llm_time_seconds=float(llm_time) if llm_time is not None else None,
            tokens_used=worker_result.get("tokens_used"),
            max_tokens=worker_result.get("max_tokens"),
        )
        user_message = _user_message(request.message)
        self.store.append_exchange(request.session_id, user_message, assistant)
        return ChatResponse(
            session_id=request.session_id,
            user_message=user_message,
            assistant_message=assistant,
            sources=final_sources,
            grounded=False,
            verification=verification,
            persona=persona,
            response_time_ms=response_time_ms,
            retrieval=retrieval_info,
            created_at=_now(),
        )

    def _complete_fallback(
        self,
        *,
        session_id: str,
        user_content: str,
        started: float,
        retrieval_info: RetrievalInfo,
        reason: str | None,
    ) -> ChatResponse:
        verification = _verification("unavailable", reason)
        response_time_ms = int((time.perf_counter() - started) * 1000)
        assistant = _assistant_message(
            content=FALLBACK_MESSAGE,
            sources=[],
            grounded=False,
            verification=verification,
            persona="KBN University Helpdesk",
            response_time_ms=response_time_ms,
        )
        user_message = _user_message(user_content)
        self.store.append_exchange(session_id, user_message, assistant)
        self._cache_put(self._cache_key(user_content), assistant)
        return ChatResponse(
            session_id=session_id,
            user_message=user_message,
            assistant_message=assistant,
            sources=[],
            grounded=False,
            verification=verification,
            persona="KBN University Helpdesk",
            response_time_ms=response_time_ms,
            retrieval=retrieval_info,
            created_at=_now(),
        )

    # ------------------------------------------------------------------ timeout completion
    def _schedule_finalize(
        self,
        *,
        session_id: str,
        future,
        user_content: str,
        started: float,
        university: bool,
        context: str,
        sources: list[SourceReference],
        retrieval_info: RetrievalInfo,
        persona: str,
        web: bool = False,
    ) -> None:
        task = asyncio.create_task(
            self._finalize_after_timeout(
                session_id=session_id,
                future=future,
                user_content=user_content,
                started=started,
                university=university,
                context=context,
                sources=sources,
                retrieval_info=retrieval_info,
                persona=persona,
                web=web,
            )
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _finalize_after_timeout(
        self,
        *,
        session_id: str,
        future,
        user_content: str,
        started: float,
        university: bool,
        context: str,
        sources: list[SourceReference],
        retrieval_info: RetrievalInfo,
        persona: str,
        web: bool,
    ) -> None:
        try:
            worker_result = await self.worker.await_result(
                future,
                timeout_seconds=max(
                    self.settings.late_completion_timeout_seconds,
                    self.settings.request_timeout_seconds,
                ),
            )
            if university:
                if web:
                    answer = str(worker_result.get("answer") or "").strip()
                    ok, reason = verify_answer(answer, context, self.retrieval.allowed_urls)
                    if ok:
                        verification = _verification("web_unverified", reason)
                        final_sources = sources
                    else:
                        answer = FALLBACK_MESSAGE
                        verification = _verification("unavailable", reason)
                        final_sources = []
                else:
                    answer = str(worker_result.get("answer") or "").strip()
                    if is_refusal(answer):
                        answer = FALLBACK_MESSAGE
                        verification = _verification(
                            "unavailable",
                            "The model reported that the records do not contain the answer.",
                        )
                        final_sources = []
                    else:
                        ok, reason = verify_answer(answer, context, self.retrieval.allowed_urls)
                        if ok:
                            verification = _verification("verified_kb", reason)
                            final_sources = sources
                        else:
                            answer = FALLBACK_MESSAGE
                            verification = _verification("unavailable", reason)
                            final_sources = []
            else:
                answer = str(worker_result.get("answer") or "").strip()
                verification = _verification(
                    "general_unverified",
                    "This answer is general knowledge, not KBN University record data.",
                )
                final_sources = []

            response_time_ms = int((time.perf_counter() - started) * 1000)
            assistant = _assistant_message(
                content=answer,
                sources=final_sources,
                grounded=verification.status == "verified_kb",
                verification=verification,
                persona=str(worker_result.get("persona") or persona),
                response_time_ms=response_time_ms,
                llm_time_seconds=worker_result.get("time"),
                tokens_used=worker_result.get("tokens_used"),
                max_tokens=worker_result.get("max_tokens"),
            )
            self.store.append_exchange(session_id, _user_message(user_content), assistant)
            logger.info("Stored late-completing answer for session=%s", session_id)
        except Exception as exc:
            logger.error("Could not finalize timed-out LLM request: %s", exc)
        finally:
            self._inflight.discard(session_id)

    # ------------------------------------------------------------------ web fallback
    async def _retrieve_web(self, query: str) -> tuple[str, list[SourceReference]]:
        try:
            from researcher import get_search_and_scrape_content

            text = await asyncio.wait_for(
                get_search_and_scrape_content(query, max_results_per_backend=3),
                timeout=self.settings.web_fallback_timeout_seconds,
            )
        except Exception as exc:
            logger.warning("Web fallback failed: %s", exc)
            return "", []

        sources: list[SourceReference] = []
        for index, match in enumerate(re.finditer(r"--- SOURCE:\s*(\S+)\s*---", text)):
            url = match.group(1)
            parsed = urlparse(url)
            title = parsed.netloc + (parsed.path.rstrip("/") or "")
            sources.append(
                SourceReference(
                    id=f"web_{index}",
                    title=title,
                    file=url,
                    url=url,
                    snippet="",
                )
            )
        return text, sources
