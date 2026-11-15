"""Database round trips are the latency on Vercel (function and Neon can be continents apart).

A proxy adds delay to every packet, so "how many round trips" becomes a timing assertion.
Postgres-gated: needs TEST_DATABASE_URL.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.schemas import Message
from app.services.session_store import new_message_id
from tests.fakes import FakeRetrieval, FakeWorker

DB_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="no TEST_DATABASE_URL")

ONE_WAY = 0.06
RTT = 2 * ONE_WAY  # 120 ms per round trip


class LatencyProxy:
    def __init__(self, upstream_host: str, upstream_port: int, one_way_delay: float) -> None:
        self.upstream = (upstream_host, upstream_port)
        self.delay = one_way_delay
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(16)
        self.port = self.server.getsockname()[1]
        self.closed = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while not self.closed:
            try:
                client, _ = self.server.accept()
            except OSError:
                return
            upstream = socket.create_connection(self.upstream)
            for src, dst in ((client, upstream), (upstream, client)):
                threading.Thread(target=self._pump, args=(src, dst), daemon=True).start()

    def _pump(self, src, dst) -> None:
        try:
            while data := src.recv(65536):
                time.sleep(self.delay)
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.close()
                except OSError:
                    pass

    def close(self) -> None:
        self.closed = True
        self.server.close()


@pytest.fixture
def slow_url():
    parts = urlsplit(DB_URL)
    proxy = LatencyProxy(parts.hostname, parts.port or 5432, ONE_WAY)
    netloc = f"{parts.username}@127.0.0.1:{proxy.port}"
    yield urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    proxy.close()


@pytest.fixture
def slow_store(slow_url):
    from app.services.pg_session_store import PostgresSessionStore

    store = PostgresSessionStore(slow_url)
    store.truncate_for_tests()
    store.count()  # connect and create the schema outside the timed section
    store.count()
    yield store
    store.close()


def _timed(fn) -> float:
    started = time.perf_counter()
    fn()
    return time.perf_counter() - started


def _msg(role: str, text: str) -> Message:
    return Message(id=new_message_id(), role=role, content=text)


def test_a_simple_read_costs_one_round_trip(slow_store):
    sid = slow_store.create(owner="owner-aaaaaaaaaaaaaaaa").id
    slow_store.owner_of(sid)  # warm
    assert _timed(lambda: slow_store.owner_of(sid)) < 1.6 * RTT  # was ~4 (check + BEGIN + query + COMMIT)
    assert _timed(lambda: slow_store.incr_counter("k", 1, 60)) < 1.6 * RTT


def test_several_counters_are_incremented_in_one_round_trip(slow_store):
    items = [("chat-ip:1.2.3.4", 5, 120, 1), ("chat:abc", 5, 120, 1), ("budget:all", 9, 3600, 1)]
    slow_store.incr_counters(items)  # warm
    started = time.perf_counter()
    result = slow_store.incr_counters(items)
    assert time.perf_counter() - started < 1.6 * RTT
    assert result == [2, 2, 2]  # values in the order given


def test_concurrent_appends_to_one_session_are_not_lost(slow_store):
    sid = slow_store.create().id
    threads = [threading.Thread(target=lambda i=i: slow_store.append_exchange(sid, _msg("user", f"q{i}"), _msg("assistant", f"a{i}")))
               for i in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(slow_store.detail(sid).messages) == 24  # row locking still serialises writers


def test_a_fast_chat_stays_within_ten_round_trips(slow_url, tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "RetrievalService", FakeRetrieval)
    monkeypatch.setattr(main_module, "WorkerClient", FakeWorker)
    settings = Settings(start_worker=False, session_store_path=str(tmp_path / "s.json"), database_url=slow_url,
                        trusted_proxy_hops=0, rate_limit_per_minute=1000, rate_limit_sessions_per_hour=1000)
    headers = {"X-Client-Id": "roundtrip-test-0123456789"}
    with TestClient(main_module.create_app(settings)) as client:
        sid = client.post("/sessions", json={}, headers=headers).json()["id"]
        client.post("/chat", json={"session_id": sid, "message": "Where is KBN University located?"}, headers=headers)  # warm
        elapsed = _timed(lambda: client.post("/chat", json={"session_id": sid, "message": "Where is KBN University located?"}, headers=headers))
    assert elapsed < 10 * RTT  # was about 19 round trips (about 2.3 s at this latency)
