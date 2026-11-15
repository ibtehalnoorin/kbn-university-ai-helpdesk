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
from processor import MAX_CONTEXT_TOKENS, estimate_tokens, truncate_history

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
            if history and history[0]["role"] == "system":
                history[0] = {"role": "system", "content": system_prompt}
            else:
                history.insert(0, {"role": "system", "content": system_prompt})
            history = truncate_history(history, MAX_CONTEXT_TOKENS)
            messages = history + [{"role": "user", "content": user_prompt}]

            answer = self._complete(messages)

            history = messages + [{"role": "assistant", "content": answer}]
            with self._lock:
                self._histories[worker_key] = history
            tokens = estimate_tokens(history)
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
        if response.status_code >= 400:
            logger.error("Groq error %s: %s", response.status_code, response.text[:300])
            raise WorkerResultError(f"The language model returned an error ({response.status_code}).")
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise WorkerResultError("The language model returned an unexpected response.") from exc
        if not content or not str(content).strip():
            raise WorkerResultError("The language model returned an empty answer.")
        return str(content).strip()
