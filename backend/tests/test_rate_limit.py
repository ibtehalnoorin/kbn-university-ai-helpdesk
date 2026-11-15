"""Abuse protection: per-visitor rate limits and a daily LLM budget."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from starlette.requests import Request

import app.main as main_module
from app.config import Settings
from app.services.rate_limit import DailyBudget, SlidingWindowLimiter, client_ip
from tests.fakes import FakeRetrieval, FakeWorker

CLIENT = {"X-Client-Id": "client-a-0123456789"}


def _request(headers: dict[str, str], host: str = "10.0.0.9") -> Request:
    scope = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (host, 1234),
    }
    return Request(scope)


def test_limiter_blocks_after_limit_and_recovers_after_window():
    limiter = SlidingWindowLimiter(limit=2, window_seconds=60)
    assert limiter.allow("ip", now=0) and limiter.allow("ip", now=1)
    assert not limiter.allow("ip", now=2)
    assert limiter.allow("other", now=2)  # keys are independent
    assert limiter.retry_after("ip", now=2) == 58
    assert limiter.allow("ip", now=61)


def test_budget_resets_next_utc_day_and_zero_means_off():
    budget = DailyBudget(max_calls=2)
    day1 = datetime(2026, 11, 15, 23, 0, tzinfo=timezone.utc).timestamp()
    day2 = datetime(2026, 11, 16, 0, 1, tzinfo=timezone.utc).timestamp()
    assert budget.try_consume(day1) and budget.try_consume(day1)
    assert not budget.try_consume(day1)
    assert budget.try_consume(day2)
    assert not DailyBudget(max_calls=0).try_consume(day1)


def test_client_ip_uses_the_hop_added_by_the_trusted_proxy():
    spoofed = _request({"X-Forwarded-For": "6.6.6.6, 203.0.113.7"})
    assert client_ip(spoofed, trusted_hops=1) == "203.0.113.7"  # not the client-supplied first entry
    assert client_ip(spoofed, trusted_hops=0) == "10.0.0.9"  # ignore the header entirely
    assert client_ip(_request({}), trusted_hops=1) == "10.0.0.9"  # no header: socket address


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main_module, "RetrievalService", FakeRetrieval)
    monkeypatch.setattr(main_module, "WorkerClient", FakeWorker)

    def build(**overrides) -> TestClient:
        base = dict(start_worker=False, session_store_path=str(tmp_path / "s.json"), fast_answers_enabled=False)
        base.update(overrides)
        return TestClient(main_module.create_app(Settings(**base)))

    return build


def _chat(client, sid, message="What is 2 + 2?"):
    return client.post("/chat", json={"session_id": sid, "message": message}, headers=CLIENT)


def test_chat_returns_429_with_retry_after(make_client):
    with make_client(rate_limit_per_minute=2, trusted_proxy_hops=0) as client:
        sid = client.post("/sessions", json={}, headers=CLIENT).json()["id"]
        assert _chat(client, sid).status_code == 200
        assert _chat(client, sid, "And 3 + 3?").status_code == 200
        blocked = _chat(client, sid, "And 4 + 4?")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(blocked.headers["retry-after"]) <= 60


def test_session_creation_is_limited_per_hour(make_client):
    with make_client(rate_limit_sessions_per_hour=2, trusted_proxy_hops=0) as client:
        assert client.post("/sessions", json={}, headers=CLIENT).status_code == 201
        assert client.post("/sessions", json={}, headers=CLIENT).status_code == 201
        assert client.post("/sessions", json={}, headers=CLIENT).status_code == 429


def test_different_visitors_are_limited_independently(make_client):
    a = {"X-Client-Id": "visitor-a-0123456789", "X-Forwarded-For": "1.1.1.1"}
    b = {"X-Client-Id": "visitor-b-0123456789", "X-Forwarded-For": "2.2.2.2"}
    with make_client(rate_limit_sessions_per_hour=1, trusted_proxy_hops=1) as client:
        one = client.post("/sessions", json={}, headers=a)
        two = client.post("/sessions", json={}, headers=b)
        again = client.post("/sessions", json={}, headers=a)
    assert (one.status_code, two.status_code, again.status_code) == (201, 201, 429)


def test_fast_path_answer_does_not_consume_the_llm_budget(make_client):
    with make_client(daily_llm_budget=0, fast_answers_enabled=True, trusted_proxy_hops=0) as client:
        sid = client.post("/sessions", json={}, headers=CLIENT).json()["id"]
        fast = _chat(client, sid, "Where is KBN University located?")
        assert fast.status_code == 200 and fast.json()["fast_path"]
        llm = _chat(client, sid, "What is 2 + 2?")
    assert llm.status_code == 503
    assert llm.json()["error"]["code"] == "daily_limit_reached"


def test_message_over_max_chars_returns_422(make_client):
    with make_client(trusted_proxy_hops=0) as client:
        sid = client.post("/sessions", json={}, headers=CLIENT).json()["id"]
        too_long = _chat(client, sid, "x" * 1001)
        ok = _chat(client, sid, "y" * 1000)
    assert too_long.status_code == 422
    assert ok.status_code == 200
