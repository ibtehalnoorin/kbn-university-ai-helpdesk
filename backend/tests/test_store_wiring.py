"""App wiring for the session store: Postgres selection, health, retention, outage."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from tests.fakes import FakeRetrieval, FakeWorker

DB_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="no TEST_DATABASE_URL")


def _app(tmp_path, monkeypatch, **overrides):
    monkeypatch.setattr(main_module, "RetrievalService", FakeRetrieval)
    monkeypatch.setattr(main_module, "WorkerClient", FakeWorker)
    base = dict(start_worker=False, session_store_path=str(tmp_path / "s.json"), database_url=DB_URL)
    base.update(overrides)
    return main_module.create_app(Settings(**base))


def test_postgres_store_is_used_when_database_url_is_set(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as client:
        created = client.post("/sessions", json={}).json()
        body = client.get("/health").json()
        assert body["components"]["store"]["kind"] == "postgres"
        assert body["components"]["store"]["status"] == "ok"
        client.delete(f"/sessions/{created['id']}")
    assert not (tmp_path / "s.json").exists()


def test_json_store_is_used_without_database_url(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch, database_url="")) as client:
        assert client.get("/health").json()["components"]["store"]["kind"] == "json"


def test_startup_purges_sessions_past_retention(tmp_path, monkeypatch):
    from app.services.pg_session_store import PostgresSessionStore
    from datetime import datetime, timedelta, timezone

    seed = PostgresSessionStore(DB_URL)
    seed.truncate_for_tests()
    stale = seed.create("old").id
    seed.set_updated_at_for_tests(stale, datetime.now(timezone.utc) - timedelta(days=45))
    seed.close()
    with TestClient(_app(tmp_path, monkeypatch, session_retention_days=30)) as client:
        assert client.get("/sessions").json()["count"] == 0
    check = PostgresSessionStore(DB_URL)
    try:
        assert check.count() == 0  # the stale row was removed from the database itself
    finally:
        check.close()


def test_database_outage_degrades_health_and_returns_503(tmp_path, monkeypatch):
    bad = "postgresql://nobody@127.0.0.1:1/none"
    with TestClient(_app(tmp_path, monkeypatch, database_url=bad, database_connect_timeout_seconds=1)) as client:
        health = client.get("/health").json()
        assert health["status"] == "degraded"
        assert health["components"]["store"]["status"] == "error"
        response = client.post("/sessions", json={})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "store_unavailable"
