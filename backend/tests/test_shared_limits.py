"""Rate limits and the LLM budget must be shared across server instances (Vercel scales out)."""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.services.rate_limit import SharedDailyBudget, SharedWindowLimiter
from tests.fakes import FakeRetrieval, FakeWorker

DB_URL = os.environ.get("TEST_DATABASE_URL")
CLIENT = {"X-Client-Id": "client-a-0123456789"}


class FakeCounterStore:
    """Dict-backed stand-in for the Postgres counters (shared by several limiter objects)."""

    def __init__(self) -> None:
        self.counts: dict[tuple[str, int], int] = {}
        self._lock = threading.Lock()

    def incr_counter(self, key: str, bucket: int, ttl_seconds: int, amount: int = 1) -> int:
        with self._lock:
            self.counts[(key, bucket)] = self.counts.get((key, bucket), 0) + amount
            return self.counts[(key, bucket)]


def test_window_limiter_is_shared_by_two_instances():
    store = FakeCounterStore()
    a = SharedWindowLimiter(store, limit=3, window_seconds=60, name="chat")
    b = SharedWindowLimiter(store, limit=3, window_seconds=60, name="chat")  # another server copy
    assert a.allow("ip", now=1000) and b.allow("ip", now=1001) and a.allow("ip", now=1002)
    assert not b.allow("ip", now=1003) and not a.allow("ip", now=1004)
    assert b.allow("other-ip", now=1004)  # keys are independent
    assert a.allow("ip", now=1000 + 61)  # next window
    assert 1 <= a.retry_after("ip", now=1003) <= 60


def test_daily_budget_is_shared_and_refundable():
    store = FakeCounterStore()
    a = SharedDailyBudget(store, max_calls=3, per_key_max=2)
    b = SharedDailyBudget(store, max_calls=3, per_key_max=2)
    day1 = datetime(2026, 11, 15, 12, tzinfo=timezone.utc).timestamp()
    day2 = datetime(2026, 11, 16, 1, tzinfo=timezone.utc).timestamp()
    assert a.try_consume(day1, key="x") and b.try_consume(day1, key="x")
    assert not a.try_consume(day1, key="x")  # per-key cap reached across both instances
    assert b.try_consume(day1, key="y")
    assert not a.try_consume(day1, key="z")  # global cap of 3 reached
    a.refund(key="y", now=day1)
    assert b.try_consume(day1, key="z")
    assert a.try_consume(day2, key="x")  # new UTC day
    assert not SharedDailyBudget(store, max_calls=0).try_consume(day1)


pg = pytest.mark.skipif(not DB_URL, reason="no TEST_DATABASE_URL")


@pg
def test_postgres_counter_is_atomic_under_concurrency():
    from app.services.pg_session_store import PostgresSessionStore

    store = PostgresSessionStore(DB_URL)
    try:
        store.truncate_for_tests()
        results: list[int] = []
        threads = [
            threading.Thread(target=lambda: results.append(store.incr_counter("k", 7, ttl_seconds=60)))
            for _ in range(20)
        ]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert sorted(results) == list(range(1, 21))  # no lost updates
        assert store.incr_counter("k", 7, ttl_seconds=60, amount=-1) == 19
    finally:
        store.close()


@pg
def test_expired_counters_are_purged():
    from app.services.pg_session_store import PostgresSessionStore

    store = PostgresSessionStore(DB_URL)
    try:
        store.truncate_for_tests()
        store.incr_counter("old", 1, ttl_seconds=-5)  # already expired
        store.incr_counter("fresh", 1, ttl_seconds=600)
        assert store.purge_counters() == 1
        assert store.incr_counter("fresh", 1, ttl_seconds=600) == 2
    finally:
        store.close()


@pytest.fixture
def two_instances(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "RetrievalService", FakeRetrieval)
    monkeypatch.setattr(main_module, "WorkerClient", FakeWorker)

    def build(**overrides):
        base = dict(start_worker=False, session_store_path=str(tmp_path / "s.json"), database_url=DB_URL,
                    fast_answers_enabled=False, trusted_proxy_hops=0)
        base.update(overrides)
        return TestClient(main_module.create_app(Settings(**base)))

    return build


@pg
def test_rate_limit_holds_across_two_app_instances(two_instances):
    from app.services.pg_session_store import PostgresSessionStore

    seed = PostgresSessionStore(DB_URL)
    seed.truncate_for_tests()
    seed.close()
    with two_instances(rate_limit_sessions_per_hour=2) as a, two_instances(rate_limit_sessions_per_hour=2) as b:
        codes = [a.post("/sessions", json={}, headers=CLIENT).status_code,
                 b.post("/sessions", json={}, headers=CLIENT).status_code,
                 a.post("/sessions", json={}, headers=CLIENT).status_code]
    assert codes == [201, 201, 429]  # the third is refused even though it hit instance a


@pg
def test_llm_budget_holds_across_two_app_instances(two_instances):
    from app.services.pg_session_store import PostgresSessionStore

    seed = PostgresSessionStore(DB_URL)
    seed.truncate_for_tests()
    seed.close()
    with two_instances(daily_llm_budget=1, per_ip_daily_llm_calls=100, rate_limit_per_minute=100) as a, \
            two_instances(daily_llm_budget=1, per_ip_daily_llm_calls=100, rate_limit_per_minute=100) as b:
        sid = a.post("/sessions", json={}, headers=CLIENT).json()["id"]
        first = a.post("/chat", json={"session_id": sid, "message": "What is 2 + 2?"}, headers=CLIENT)
        second = b.post("/chat", json={"session_id": sid, "message": "What is 3 + 3?"}, headers=CLIENT)
    assert first.status_code == 200
    assert second.status_code == 503 and second.json()["error"]["code"] == "daily_limit_reached"
