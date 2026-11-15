"""Safe coordination of the existing single background LLM worker.

The protected ``processor.process_worker`` handles one task at a time and keeps
per-session history keyed by the session id it receives. We therefore:

* keep submission strictly FIFO;
* map each result to the matching pending request and verify the echoed id;
* give every public session/clear cycle its own worker key;
* reject concurrent requests for the same session at the service layer.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import threading
import time
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass
from queue import Empty

from app.config import Settings
from app.errors import (
    WorkerQueueFullError,
    WorkerResultError,
    WorkerTimeoutError,
    WorkerUnavailableError,
)
from app.services.worker_entry import run_worker

logger = logging.getLogger("kbn.worker")


@dataclass
class PendingRequest:
    worker_key: str
    query: str
    future: Future
    submitted_at: float


class WorkerClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._ctx = multiprocessing.get_context("spawn")
        self._process: multiprocessing.Process | None = None
        self._task_queue = None
        self._result_queue = None
        self._dispatcher: threading.Thread | None = None
        self._monitor: threading.Thread | None = None
        self._run_stop: threading.Event | None = None
        self._pending: deque[PendingRequest] = deque()
        self._pending_lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._shutdown = False

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        with self._lifecycle_lock:
            if self._shutdown:
                raise WorkerUnavailableError("The LLM worker is shutting down.")
            if self._process is not None and self._process.is_alive():
                return

            if self._run_stop is not None:
                self._run_stop.set()

            task_queue = self._ctx.Queue()
            result_queue = self._ctx.Queue()
            process = self._ctx.Process(
                target=run_worker,
                args=(task_queue, result_queue, self.settings.ollama_host),
                name="kbn-llm-worker",
                daemon=True,
            )
            process.start()

            run_stop = threading.Event()
            self._task_queue = task_queue
            self._result_queue = result_queue
            self._process = process
            self._run_stop = run_stop

            self._dispatcher = threading.Thread(
                target=self._dispatch_loop,
                args=(process, result_queue, run_stop),
                name="kbn-result-dispatcher",
                daemon=True,
            )
            self._dispatcher.start()
            self._monitor = threading.Thread(
                target=self._monitor_loop,
                args=(process, run_stop),
                name="kbn-worker-monitor",
                daemon=True,
            )
            self._monitor.start()
            logger.info(
                "LLM worker starting: pid=%s model=%s ollama=%s",
                process.pid,
                self.settings.llm_model,
                self.settings.ollama_host,
            )

    def _ensure_started(self) -> None:
        with self._lifecycle_lock:
            if self._shutdown:
                raise WorkerUnavailableError("The LLM worker is shutting down.")
            if self._process is None or not self._process.is_alive():
                if self._process is not None:
                    logger.warning("LLM worker is not alive; restarting it.")
                self.start()

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._shutdown = True
            if self._run_stop is not None:
                self._run_stop.set()
            self._fail_pending(WorkerUnavailableError("The LLM worker is shutting down."))

            process = self._process
            task_queue = self._task_queue
            result_queue = self._result_queue
            self._process = None
            self._task_queue = None
            self._result_queue = None

            if task_queue is not None:
                try:
                    task_queue.put(None)
                except Exception:
                    pass
            if result_queue is not None:
                try:
                    result_queue.put(None)
                except Exception:
                    pass

            if process is not None:
                process.join(timeout=5)
                if process.is_alive():
                    logger.warning("Terminating unresponsive LLM worker pid=%s", process.pid)
                    process.terminate()
                    process.join(timeout=3)
                try:
                    process.close()
                except Exception:
                    pass
            for queue in (task_queue, result_queue):
                if queue is not None:
                    try:
                        queue.close()
                        queue.join_thread()
                    except Exception:
                        pass
            logger.info("LLM worker stopped.")

    # ------------------------------------------------------------------ threads
    def _dispatch_loop(self, process, result_queue, run_stop: threading.Event) -> None:
        while not run_stop.is_set():
            try:
                result = result_queue.get(timeout=1.0)
            except Empty:
                if not process.is_alive():
                    break
                continue
            except (EOFError, OSError):
                break

            if result is None:
                break

            with self._pending_lock:
                pending = self._pending.popleft() if self._pending else None

            if pending is None:
                logger.error("Worker returned a result with no pending request: %r", str(result)[:200])
                continue
            if pending.future.done():
                logger.info("Discarding late worker result for %s", pending.worker_key)
                continue

            echoed = result.get("session_id")
            if echoed != pending.worker_key:
                message = (
                    "Worker session mismatch: expected "
                    f"{pending.worker_key!r}, received {echoed!r}"
                )
                logger.critical(message)
                pending.future.set_exception(WorkerResultError(message))
                continue

            if result.get("status") == "success":
                pending.future.set_result(result)
            else:
                pending.future.set_exception(
                    WorkerResultError(str(result.get("error") or "The LLM worker reported an error."))
                )

        self._fail_pending(WorkerUnavailableError("The LLM worker stopped unexpectedly."))

    def _monitor_loop(self, process, run_stop: threading.Event) -> None:
        while not run_stop.wait(2.0):
            if not process.is_alive():
                logger.error("LLM worker pid=%s exited unexpectedly.", process.pid)
                self._fail_pending(
                    WorkerUnavailableError(
                        "The LLM worker stopped unexpectedly. Start a new request to restart it."
                    )
                )
                return

    def _fail_pending(self, error: Exception) -> None:
        with self._pending_lock:
            pending = list(self._pending)
            self._pending.clear()
        for item in pending:
            if not item.future.done():
                item.future.set_exception(error)
        if pending:
            logger.error("Failed %s pending LLM request(s): %s", len(pending), error)

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
        self._ensure_started()
        payload = (worker_key, query, kb_knowledge, web_context, persona_name, system_prompt)
        with self._pending_lock:
            if len(self._pending) >= self.settings.max_pending_requests:
                raise WorkerQueueFullError(
                    "The local model queue is full. Please wait for current answers to finish."
                )
            future: Future = Future()
            pending = PendingRequest(
                worker_key=worker_key,
                query=query,
                future=future,
                submitted_at=time.perf_counter(),
            )
            self._pending.append(pending)
            try:
                self._task_queue.put(payload)
            except Exception as exc:
                self._pending.pop()
                raise WorkerUnavailableError(f"Could not dispatch to the LLM worker: {exc}") from exc
        logger.info(
            "Queued LLM request: worker_key=%s queue=%s query=%r",
            worker_key,
            len(self._pending),
            query[:100],
        )
        return future

    async def await_result(self, future: Future, timeout_seconds: float) -> dict:
        """Wait for a worker result without cancelling the underlying work.

        ``asyncio.wait`` on a plain wrapped future would let task cancellation
        propagate into the concurrent Future, which would break the background
        late-completion path. Cache one wrapper per Future, shield it, and keep
        the underlying future alive if the HTTP request is cancelled.
        """

        wrapped = getattr(future, "_kbn_asyncio_future", None)
        if wrapped is None:
            wrapped = asyncio.wrap_future(future)
            try:
                future._kbn_asyncio_future = wrapped  # type: ignore[attr-defined]
            except AttributeError:  # pragma: no cover - Future always allows attrs
                pass

        done, _ = await asyncio.wait(
            {asyncio.shield(wrapped)}, timeout=timeout_seconds
        )
        if not done:
            raise WorkerTimeoutError(
                f"The local model did not finish within {int(timeout_seconds)} seconds."
            )
        return done.pop().result()

    # ------------------------------------------------------------------ status
    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.is_alive()

    @property
    def pending_count(self) -> int:
        with self._pending_lock:
            return len(self._pending)

    def status(self) -> dict:
        return {
            "started": self._process is not None,
            "alive": self.alive,
            "pid": self._process.pid if self._process is not None else None,
            "pending": self.pending_count,
            "model": self.settings.llm_model,
            "timeout_seconds": self.settings.request_timeout_seconds,
        }
