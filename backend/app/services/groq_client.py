"""Groq-backed LLM client with the same public surface as ``WorkerClient``.

``ChatService`` talks to the LLM only through ``submit`` / ``await_result`` /
``status``. This class keeps that contract but calls the Groq OpenAI-compatible
HTTP API from a thread pool, so many sessions can be answered in parallel and
nothing depends on a local Ollama process.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

import httpx

from app.config import Settings
from app.errors import (
    WorkerQueueFullError,
    WorkerResultError,
    WorkerUnavailableError,
)
from app.services.worker_client import WorkerClient
from processor import MAX_CONTEXT_TOKENS, estimate_tokens

logger = logging.getLogger("kbn.groq")

BUSY_MESSAGE = "The assistant is busy right now. Please try again in a minute."


class GroqWorkerClient:
    # Pure asyncio helper that does not touch ``self``; reused, not duplicated.
    await_result = WorkerClient.await_result

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self._http = httpx.Client(
            base_url=settings.groq_base_url.rstrip("/"),
            timeout=settings.groq_timeout_seconds,
            transport=transport,
        )
        self._pool = ThreadPoolExecutor(
            max_workers=settings.groq_max_parallel, thread_name_prefix="kbn-groq"
        )
        self._histories: dict[str, list[dict]] = {}
        self._lock = threading.Lock()
        self._pending = 0
        self._stopped = False

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._http.close()

    @property
    def alive(self) -> bool:
        return not self._stopped

    @property
    def pending_count(self) -> int:
        with self._lock:
            return self._pending

    def status(self) -> dict:
        return {
            "provider": "groq",
            "started": True,
            "alive": self.alive,
            "pid": None,
            "pending": self.pending_count,
            "model": self.settings.groq_model,
            "timeout_seconds": self.settings.request_timeout_seconds,
        }

    # ------------------------------------------------------------------ requests
    def submit(
        self,
        *,
        worker_key: str,
        query: str,
        kb_knowledge: str,
        web_context: str,
        persona_name: str,
        system_prompt: str,
    ) -> Future:
        if self._stopped:
            raise WorkerUnavailableError("The language model client is shutting down.")
        if not self.settings.groq_api_key:
            raise WorkerUnavailableError("No Groq API key is configured (KBN_GROQ_API_KEY).")
        with self._lock:
            if self._pending >= self.settings.max_pending_requests:
                raise WorkerQueueFullError(BUSY_MESSAGE)
            self._pending += 1
        try:
            return self._pool.submit(
                self._run, worker_key, query, kb_knowledge, web_context, persona_name, system_prompt
            )
        except Exception:
            with self._lock:
                self._pending -= 1
            raise

    def _run(self, worker_key, query, kb_knowledge, web_context, persona_name, system_prompt) -> dict:
        try:
            started = time.time()
            context = "\n\n".join(part for part in (kb_knowledge, web_context) if part)
            user_prompt = (
                f"User Query: {query}\n\nRetrieved Context & Knowledge:\n{context}"
                if context.strip()
                else f"User Query: {query}"
            )
            with self._lock:
                history = list(self._histories.get(worker_key, []))
            messages = (
                [{"role": "system", "content": system_prompt}]
                + history
                + [{"role": "user", "content": user_prompt}]
            )

            try:
                answer = self._complete(messages)
            except _PayloadTooLarge:
                with self._lock:
                    self._histories.pop(worker_key, None)
                raise WorkerResultError(
                    "That conversation got too long for the assistant. Please start a new conversation."
                ) from None

            # Keep only the bare question and answer. Replaying every earlier retrieved
            # context block would burn the free tier's tokens-per-minute budget.
            history = _trim_history(history + [
                {"role": "user", "content": query},
                {"role": "assistant", "content": answer},
            ])
            with self._lock:
                self._histories[worker_key] = history
            tokens = estimate_tokens(messages) + estimate_tokens([{"content": answer}])
            return {
                "status": "success",
                "session_id": worker_key,
                "query": query,
                "answer": answer,
                "time": time.time() - started,
                "persona": persona_name,
                "tokens_used": tokens,
                "max_tokens": MAX_CONTEXT_TOKENS,
                "utilization": min(100.0, tokens / MAX_CONTEXT_TOKENS * 100),
            }
        finally:
            with self._lock:
                self._pending -= 1

    def _complete(self, messages: list[dict]) -> str:
        payload = {
            "model": self.settings.groq_model,
            "messages": messages,
            "temperature": self.settings.llm_temperature,
            "max_tokens": self.settings.llm_num_predict,
        }
        try:
            response = self._http.post(
                "/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.groq_api_key}"},
            )
        except httpx.HTTPError as exc:
            logger.warning("Groq request failed: %s", exc)
            raise WorkerResultError("The language model could not be reached.") from exc
        if response.status_code == 429:
            logger.warning("Groq rate limit hit")
            raise WorkerQueueFullError(BUSY_MESSAGE)
        if response.status_code == 413:
            raise _PayloadTooLarge()
        if response.status_code >= 400:
            logger.error("Groq error %s: %s", response.status_code, response.text[:300])
            raise WorkerResultError(f"The language model returned an error ({response.status_code}).")
        try:
            choice = response.json()["choices"][0]
            content = choice["message"]["content"]
            finish = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise WorkerResultError("The language model returned an unexpected response.") from exc
        if not content or not str(content).strip():
            raise WorkerResultError("The language model returned an empty answer.")
        answer = str(content).strip()
        if finish == "length":
            answer = _cut_to_last_sentence(answer)
        return answer


class _PayloadTooLarge(Exception):
    """Groq answered 413: the request exceeded the model's token limits."""


HISTORY_TOKEN_CAP = 1500


def _trim_history(history: list[dict]) -> list[dict]:
    """Drop the oldest question/answer pairs until the history fits the token cap."""
    while len(history) > 2 and estimate_tokens(history) > HISTORY_TOKEN_CAP:
        history = history[2:]
    return history


def _cut_to_last_sentence(text: str) -> str:
    """The model hit max_tokens: keep only complete sentences, or fail if there are none."""
    end = max(text.rfind(mark) for mark in (". ", "! ", "? ", ".\n", "!\n", "?\n"))
    if end < 0 and text[-1:] in ".!?":
        return text
    if end < 40:
        raise WorkerResultError(
            "The assistant could not finish that answer. Please ask a more specific question."
        )
    return text[: end + 1].strip()
