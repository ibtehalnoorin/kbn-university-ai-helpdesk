"""Regression tests for the whole-branch review findings (public-launch hardening)."""

from __future__ import annotations

import logging
import os

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.errors import WorkerQueueFullError, WorkerResultError
from app.services.chat_service import ChatService
from app.services.groq_client import GroqWorkerClient
from app.services.rate_limit import DailyBudget
from tests.fakes import FakeRetrieval, FakeWorker

CLIENT = {"X-Client-Id": "client-a-0123456789"}
DB_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "RetrievalService", FakeRetrieval)
    monkeypatch.setattr(main_module, "WorkerClient", FakeWorker)

    def build(**overrides) -> TestClient:
        base = dict(start_worker=False, session_store_path=str(tmp_path / "s.json"), fast_answers_enabled=False)
        base.update(overrides)
        return TestClient(main_module.create_app(Settings(**base)))

    return build


# ---- 1. the session cap must not lock out new visitors -------------------------
def test_more_than_a_hundred_visitors_can_each_start_a_session(make_client):
    with make_client(rate_limit_sessions_per_hour=10_000, trusted_proxy_hops=0) as client:
        codes = {
            client.post("/sessions", json={}, headers={"X-Client-Id": f"visitor-{i:04d}-xxxxxxxx"}).status_code
            for i in range(105)
        }
    assert codes == {201}


# ---- 2. one visitor must not drain the shared LLM budget -----------------------
def test_daily_budget_has_a_per_key_cap_and_refunds():
    budget = DailyBudget(max_calls=3, per_key_max=2)
    assert budget.try_consume(key="a") and budget.try_consume(key="a")
    assert not budget.try_consume(key="a")  # a hit its own cap
    assert budget.try_consume(key="b")  # b still has room
    assert not budget.try_consume(key="c")  # global cap of 3 reached
    budget.refund(key="a")
    assert budget.try_consume(key="a")  # refund freed one global and one per-key unit


def test_failed_submit_refunds_the_budget():
    class Stub:
        calls = 0

        def submit(self, **kwargs):
            Stub.calls += 1
            if Stub.calls == 1:
                raise WorkerQueueFullError("full")
            return "future"

    service = ChatService(Settings(start_worker=False), None, None, Stub(), budget=DailyBudget(1))
    with pytest.raises(WorkerQueueFullError):
        service._submit(query="q")
    assert service._submit(query="q") == "future"  # the failed attempt did not cost a unit


def test_one_client_address_cannot_drain_the_daily_budget(make_client):
    with make_client(per_ip_daily_llm_calls=1, trusted_proxy_hops=1, rate_limit_per_minute=100) as client:
        sid = client.post("/sessions", json={}, headers=CLIENT).json()["id"]

        def ask(ip, text):
            return client.post(
                "/chat",
                json={"session_id": sid, "message": text},
                headers={**CLIENT, "X-Forwarded-For": ip},
            )

        assert ask("1.1.1.1", "What is 2 + 2?").status_code == 200
        blocked = ask("1.1.1.1", "What is 3 + 3?")
        other = ask("2.2.2.2", "What is 4 + 4?")
    assert blocked.status_code == 503 and blocked.json()["error"]["code"] == "daily_limit_reached"
    assert other.status_code == 200


# ---- 3 & 4. Groq token use and truncated replies -------------------------------
def _groq(handler, **overrides) -> GroqWorkerClient:
    settings = Settings(llm_provider="groq", groq_api_key="k", groq_model="m", start_worker=False, **overrides)
    return GroqWorkerClient(settings, transport=httpx.MockTransport(handler))


def _ask(client, key="s", query="q", kb="ctx"):
    import asyncio

    future = client.submit(
        worker_key=key, query=query, kb_knowledge=kb, web_context="", persona_name="p", system_prompt="SYS"
    )
    return asyncio.run(client.await_result(future, 5))


def _reply(content, finish="stop", status=200):
    return httpx.Response(status, json={"choices": [{"message": {"content": content}, "finish_reason": finish}]})


def test_history_keeps_questions_and_answers_but_not_old_retrieved_context():
    bodies = []

    def handler(request):
        import json

        bodies.append(json.loads(request.content)["messages"])
        return _reply("an answer")

    client = _groq(handler)
    _ask(client, query="first question", kb="OLD-CONTEXT-BLOCK")
    _ask(client, query="second question", kb="NEW-CONTEXT-BLOCK")
    second = str(bodies[1])
    assert "first question" in second and "an answer" in second
    assert "OLD-CONTEXT-BLOCK" not in second and "NEW-CONTEXT-BLOCK" in second


def test_history_is_capped_so_requests_stay_small():
    sizes = []

    def handler(request):
        import json

        sizes.append(sum(len(m["content"]) for m in json.loads(request.content)["messages"]))
        return _reply("short answer")

    client = _groq(handler)
    for i in range(40):
        _ask(client, query=f"{i} " + "x" * 1500, kb="ctx")
    assert max(sizes) < 4 * 3200  # about 3.2K tokens including the current question and context


def test_413_clears_history_and_asks_the_user_to_start_over():
    import json

    seen = []

    def handler(request):
        messages = json.loads(request.content)["messages"]
        seen.append(messages)
        return httpx.Response(413, json={}) if len(seen) == 2 else _reply("fine")

    client = _groq(handler)
    _ask(client, query="one")
    with pytest.raises(WorkerResultError) as info:
        _ask(client, query="two")
    assert "too long" in str(info.value).lower()
    _ask(client, query="three")
    assert [m["role"] for m in seen[2]] == ["system", "user"]  # history was dropped


def test_length_finish_reason_trims_to_the_last_complete_sentence():
    client = _groq(lambda r: _reply("The VC is Prof. X. The email is vc@kbn.university. The phone num", finish="length"))
    assert _ask(client)["answer"] == "The VC is Prof. X. The email is vc@kbn.university."


def test_length_finish_reason_without_a_sentence_is_an_error():
    client = _groq(lambda r: _reply("a very long unfinished run", finish="length"))
    with pytest.raises(WorkerResultError):
        _ask(client)


# ---- 6. stale pooled Postgres connections (Neon suspends when idle) ------------
@pytest.mark.skipif(not DB_URL, reason="no TEST_DATABASE_URL")
def test_store_recovers_after_the_server_drops_pooled_connections():
    import psycopg

    from app.services.pg_session_store import PostgresSessionStore

    store = PostgresSessionStore(DB_URL)
    try:
        store.count()  # opens a pooled connection
        with psycopg.connect(DB_URL, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid()"
            )
        assert isinstance(store.count(), int)
    finally:
        store.close()


# ---- 7. visitors behind one shared address (campus Wi-Fi) ----------------------
def test_visitors_sharing_one_ip_do_not_share_one_tiny_limit(make_client):
    with make_client(rate_limit_sessions_per_hour=1, trusted_proxy_hops=0) as client:
        first = client.post("/sessions", json={}, headers={"X-Client-Id": "student-one-0123456789"})
        second = client.post("/sessions", json={}, headers={"X-Client-Id": "student-two-0123456789"})
        again = client.post("/sessions", json={}, headers={"X-Client-Id": "student-one-0123456789"})
    assert (first.status_code, second.status_code, again.status_code) == (201, 201, 429)


def test_rotating_client_ids_cannot_bypass_the_per_ip_ceiling(make_client):
    with make_client(rate_limit_sessions_per_hour=1, trusted_proxy_hops=0) as client:
        codes = [
            client.post("/sessions", json={}, headers={"X-Client-Id": f"rotating-{i:04d}-xxxxxxxx"}).status_code
            for i in range(14)
        ]
    assert codes[:10] == [201] * 10 and 429 in codes[10:]  # ceiling is 10x the per-visitor limit


# ---- 5. misconfiguration is loud -----------------------------------------------
def test_production_without_a_database_logs_a_warning(make_client, caplog):
    with caplog.at_level(logging.WARNING, logger="kbn.api"):
        with make_client(environment="production", database_url=""):
            pass
    assert any("KBN_DATABASE_URL" in record.message for record in caplog.records)


# ---- found with a real Groq key: model output quirks ------------------------------
def test_exotic_unicode_spaces_in_answers_are_normalised():
    # gpt-oss models emit U+202F (narrow no-break space) inside names and emails,
    # which would defeat the exact-text verification against the retrieved records.
    text = "Prof. Ali Raza Moosvi, email​ vc@kbn.university"
    client = _groq(lambda r: _reply(text))
    assert _ask(client)["answer"] == "Prof. Ali Raza Moosvi, email vc@kbn.university"


def test_default_groq_model_is_one_that_free_accounts_can_use():
    assert Settings(start_worker=False).groq_model == "qwen/qwen3.8-27b"


# ---- found with a real Groq key: "Verified" badge on an answer that uses no records ----
def test_answer_that_uses_no_record_terms_is_not_verified(make_client):
    # "What is 2 + 2?" is <= 6 words, so after a university question it is treated as a
    # follow-up and takes the grounded path. "4" contains nothing the records can support.
    with make_client(trusted_proxy_hops=0) as client:
        sid = client.post("/sessions", json={}, headers=CLIENT).json()["id"]
        first = client.post("/chat", json={"session_id": sid, "message": "Who is the Vice Chancellor of KBN University?"}, headers=CLIENT).json()
        assert first["verification"]["status"] == "verified_kb" and first["sources"]  # real answers keep the badge
        client.app.state.worker.answer = "4"
        second = client.post("/chat", json={"session_id": sid, "message": "What is 2 + 2?"}, headers=CLIENT).json()
    assert second["verification"]["status"] == "general_unverified"
    assert second["sources"] == [] and second["grounded"] is False
    assert second["assistant_message"]["content"] == "4"


def test_answer_uses_records_detects_overlap_with_the_context():
    from app.services.grounding import answer_uses_records
    from tests.fakes import VC_CONTEXT

    assert answer_uses_records("The Vice Chancellor is Prof. Ali Raza Moosvi.", VC_CONTEXT, "who leads kbn")
    assert not answer_uses_records("4", VC_CONTEXT, "What is 2 + 2?")
    assert not answer_uses_records("The capital of France is Paris.", VC_CONTEXT, "What is the capital of France?")
