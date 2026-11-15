"""Session store, busy-coordination and worker-client unit tests."""

from __future__ import annotations

import asyncio
import queue
import threading
from concurrent.futures import Future

import pytest

from app.errors import SessionBusyError, WorkerResultError, WorkerTimeoutError
from app.schemas import ChatRequest
from app.services.chat_service import ChatService
from app.services.session_store import SessionStore
from app.services.worker_client import PendingRequest, WorkerClient

from tests.fakes import FakeRetrieval


def test_session_clear_rotates_the_worker_generation(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.json")
    session = store.create()

    key_before = store.worker_key_for(session.id)
    store.clear(session.id)
    key_after = store.worker_key_for(session.id)

    assert key_before == f"{session.id}:g0"
    assert key_after == f"{session.id}:g1"
    assert key_before != key_after


def test_session_delete_and_limit(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.json", max_sessions=1)
    first = store.create()
    with pytest.raises(Exception):
        store.create()
    store.delete(first.id)
    second = store.create()
    assert second.id != first.id


def test_transcript_is_persisted_and_reloaded(tmp_path) -> None:
    path = tmp_path / "sessions.json"
    store = SessionStore(path)
    session = store.create()

    from app.schemas import Message

    user = Message(id="u1", role="user", content="Hello")
    assistant = Message(id="a1", role="assistant", content="Hi")
    store.append_exchange(session.id, user, assistant)

    reloaded = SessionStore(path)
    detail = reloaded.detail(session.id)
    assert detail.message_count == 2
    assert detail.title == "Hello"
    assert [message.content for message in detail.messages] == ["Hello", "Hi"]


class BlockingWorker:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.answer = "The Vice Chancellor is Prof. Ali Raza Moosvi."
        self.submissions: list[dict] = []

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def status(self) -> dict:
        return {"started": True, "alive": True, "pending": 0}

    def submit(self, **kwargs) -> Future:
        self.submissions.append(kwargs)
        future: Future = Future()
        future.set_result(
            {
                "status": "success",
                "session_id": kwargs["worker_key"],
                "answer": self.answer,
                "persona": kwargs["persona_name"],
                "time": 1,
                "tokens_used": 10,
                "max_tokens": 8192,
            }
        )
        return future

    async def await_result(self, future: Future, timeout_seconds: float) -> dict:
        await self.release.wait()
        return future.result()


@pytest.mark.asyncio
async def test_second_request_for_same_session_is_rejected(settings) -> None:
    store = SessionStore(settings.session_store_file)
    session = store.create()
    worker = BlockingWorker()
    service = ChatService(settings, store, FakeRetrieval(settings), worker)

    request = ChatRequest(
        session_id=session.id,
        message="Who is the Vice Chancellor of KBN University?",
    )
    first = asyncio.create_task(service.chat(request))
    await asyncio.sleep(0.1)

    assert service.is_busy(session.id) is True
    with pytest.raises(SessionBusyError):
        await service.chat(request)

    worker.release.set()
    response = await asyncio.wait_for(first, timeout=3)
    assert response.verification.status == "verified_kb"
    assert response.grounded is True
    assert service.is_busy(session.id) is False
    await service.shutdown()


@pytest.mark.asyncio
async def test_separate_sessions_can_be_processed_independently(settings) -> None:
    store = SessionStore(settings.session_store_file)
    first_session = store.create()
    second_session = store.create()
    service = ChatService(settings, store, FakeRetrieval(settings), BlockingWorker())
    service.worker.release.set()  # type: ignore[attr-defined]

    first = await service.chat(
        ChatRequest(session_id=first_session.id, message="Who is the Vice Chancellor?")
    )
    second = await service.chat(
        ChatRequest(session_id=second_session.id, message="What is the VC email?")
    )

    assert first.session_id != second.session_id
    assert first.grounded and second.grounded
    assert store.detail(first_session.id).message_count == 2
    assert store.detail(second_session.id).message_count == 2
    await service.shutdown()


@pytest.mark.asyncio
async def test_worker_timeout_does_not_cancel_the_underlying_future(settings) -> None:
    client = WorkerClient(settings)
    future: Future = Future()

    with pytest.raises(WorkerTimeoutError):
        await client.await_result(future, timeout_seconds=0.05)

    assert future.cancelled() is False

    future.set_result({"status": "success", "answer": "late"})
    result = await client.await_result(future, timeout_seconds=1)
    assert result["answer"] == "late"


def test_dispatcher_maps_results_in_fifo_order_and_checks_echo(settings) -> None:
    client = WorkerClient(settings)
    result_queue: queue.Queue = queue.Queue()
    stop = threading.Event()
    fake_process = type("Process", (), {"is_alive": lambda self: True})()

    first: Future = Future()
    second: Future = Future()
    client._pending.append(
        PendingRequest(worker_key="s1:g0", query="q1", future=first, submitted_at=0)
    )
    client._pending.append(
        PendingRequest(worker_key="s2:g0", query="q2", future=second, submitted_at=0)
    )

    thread = threading.Thread(
        target=client._dispatch_loop,
        args=(fake_process, result_queue, stop),
        daemon=True,
    )
    thread.start()

    result_queue.put({"status": "success", "session_id": "s1:g0", "answer": "one"})
    result_queue.put({"status": "success", "session_id": "s2:g0", "answer": "two"})

    assert first.result(timeout=2)["answer"] == "one"
    assert second.result(timeout=2)["answer"] == "two"

    stop.set()
    result_queue.put(None)
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_dispatcher_rejects_a_mismatched_echoed_session(settings) -> None:
    client = WorkerClient(settings)
    result_queue: queue.Queue = queue.Queue()
    stop = threading.Event()
    fake_process = type("Process", (), {"is_alive": lambda self: True})()

    future: Future = Future()
    client._pending.append(
        PendingRequest(worker_key="expected:g0", query="q", future=future, submitted_at=0)
    )

    thread = threading.Thread(
        target=client._dispatch_loop,
        args=(fake_process, result_queue, stop),
        daemon=True,
    )
    thread.start()
    result_queue.put({"status": "success", "session_id": "wrong:g0", "answer": "x"})

    with pytest.raises(WorkerResultError):
        future.result(timeout=2)

    stop.set()
    result_queue.put(None)
    thread.join(timeout=2)
