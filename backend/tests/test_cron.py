"""Vercel Cron calls GET /internal/cron/purge with `Authorization: Bearer $CRON_SECRET`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from tests.fakes import FakeRetrieval, FakeWorker

SECRET = "s3cret-value-for-tests"


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "RetrievalService", FakeRetrieval)
    monkeypatch.setattr(main_module, "WorkerClient", FakeWorker)

    def build(**overrides) -> TestClient:
        base = dict(start_worker=False, session_store_path=str(tmp_path / "s.json"))
        base.update(overrides)
        return TestClient(main_module.create_app(Settings(**base)))

    return build


def test_cron_is_disabled_until_a_secret_is_configured(make_client):
    with make_client() as client:
        assert client.get("/internal/cron/purge", headers={"Authorization": "Bearer anything"}).status_code == 503


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": SECRET}])
def test_cron_rejects_missing_or_wrong_secret(make_client, headers):
    with make_client(cron_secret=SECRET) as client:
        assert client.get("/internal/cron/purge", headers=headers).status_code == 401


def test_cron_purges_expired_sessions_with_the_right_secret(make_client):
    with make_client(cron_secret=SECRET, session_retention_days=30) as client:
        store = client.app.state.session_store
        stale, fresh = store.create().id, store.create().id
        store._sessions[stale].updated_at = datetime.now(timezone.utc) - timedelta(days=45)
        response = client.get("/internal/cron/purge", headers={"Authorization": f"Bearer {SECRET}"})
        assert response.status_code == 200
        assert response.json() == {"sessions_removed": 1, "counters_removed": 0}
        assert [s.id for s in store.list()] == [fresh]


def test_cron_route_is_hidden_from_the_public_api_docs(make_client):
    with make_client(cron_secret=SECRET) as client:
        assert "/internal/cron/purge" not in client.get("/openapi.json").json()["paths"]
