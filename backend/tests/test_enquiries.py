"""'Talk to the admissions office': visitors leave an enquiry; staff read it; nothing leaks."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from tests.fakes import FakeRetrieval, FakeWorker

DB_URL = os.environ.get("TEST_DATABASE_URL")
CLIENT = {"X-Client-Id": "enquiry-visitor-0123456789"}
ADMIN = {"Authorization": "Bearer staff-token-for-tests-0123"}
VALID = {
    "name": "Asha Khan",
    "contact": "asha@example.com",
    "topic": "admissions",
    "message": "Is the B.Sc. Nursing seat list out yet, and what documents do I bring?",
    "consent": True,
}


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "RetrievalService", FakeRetrieval)
    monkeypatch.setattr(main_module, "WorkerClient", FakeWorker)

    def build(**overrides) -> TestClient:
        base = dict(start_worker=False, session_store_path=str(tmp_path / "s.json"), trusted_proxy_hops=0,
                    admin_token="staff-token-for-tests-0123")
        base.update(overrides)
        return TestClient(main_module.create_app(Settings(**base)))

    return build


def test_a_visitor_can_leave_an_enquiry_and_staff_can_read_it(make_client):
    with make_client() as client:
        created = client.post("/enquiries", json=VALID, headers=CLIENT)
        assert created.status_code == 201
        reference = created.json()["id"]
        assert created.json()["received"] is True
        listed = client.get("/internal/enquiries", headers=ADMIN).json()
    assert listed["count"] == 1
    row = listed["enquiries"][0]
    assert row["id"] == reference and row["name"] == "Asha Khan" and row["contact"] == "asha@example.com"
    assert row["topic"] == "admissions" and "Nursing" in row["message"] and row["status"] == "new"


@pytest.mark.parametrize("change", [
    {"consent": False},
    {"message": "too short"},
    {"contact": "not a contact"},
    {"topic": "gossip"},
    {"name": ""},
    {"message": "x" * 1001},
])
def test_invalid_enquiries_are_rejected(make_client, change):
    with make_client() as client:
        assert client.post("/enquiries", json={**VALID, **change}, headers=CLIENT).status_code == 422
        assert client.get("/internal/enquiries", headers=ADMIN).json()["count"] == 0


@pytest.mark.parametrize("contact", ["asha@example.com", "+91 98450 12345", "9845012345", "08472-236041"])
def test_email_and_phone_contacts_are_accepted(make_client, contact):
    with make_client() as client:
        assert client.post("/enquiries", json={**VALID, "contact": contact}, headers=CLIENT).status_code == 201


def test_staff_view_needs_the_admin_token(make_client):
    with make_client() as client:
        client.post("/enquiries", json=VALID, headers=CLIENT)
        assert client.get("/internal/enquiries").status_code == 401
        assert client.get("/internal/enquiries", headers={"Authorization": "Bearer wrong"}).status_code == 401
    with make_client(admin_token="") as client:
        assert client.get("/internal/enquiries", headers=ADMIN).status_code == 503  # disabled until configured


def test_enquiries_are_rate_limited_per_visitor(make_client):
    with make_client(rate_limit_enquiries_per_hour=2) as client:
        codes = [client.post("/enquiries", json=VALID, headers=CLIENT).status_code for _ in range(3)]
        other = client.post("/enquiries", json=VALID, headers={"X-Client-Id": "someone-else-0123456789"}).status_code
    assert codes == [201, 201, 429] and other == 201


def test_enquiry_is_forwarded_to_the_configured_webhook_and_a_failure_does_not_lose_it(make_client, monkeypatch, tmp_path):
    import app.api.enquiries as enquiries_api

    sent = []
    monkeypatch.setattr(enquiries_api, "post_webhook", lambda url, payload, timeout=5: sent.append((url, payload)))
    with make_client(enquiry_webhook_url="https://hooks.example.test/kbn") as client:
        assert client.post("/enquiries", json=VALID, headers=CLIENT).status_code == 201
    assert sent and sent[0][0] == "https://hooks.example.test/kbn"
    assert sent[0][1]["name"] == "Asha Khan" and sent[0][1]["topic"] == "admissions" and "id" in sent[0][1]

    def boom(url, payload, timeout=5):
        raise RuntimeError("webhook down")

    monkeypatch.setattr(enquiries_api, "post_webhook", boom)
    with make_client(enquiry_webhook_url="https://hooks.example.test/kbn", session_store_path=str(tmp_path / "second.json")) as client:
        assert client.post("/enquiries", json=VALID, headers=CLIENT).status_code == 201  # still accepted
        assert client.get("/internal/enquiries", headers=ADMIN).json()["count"] == 1  # and kept


def test_old_enquiries_are_purged_after_the_retention_period(make_client):
    with make_client(enquiry_retention_days=30) as client:
        store = client.app.state.session_store
        client.post("/enquiries", json=VALID, headers=CLIENT)
        client.post("/enquiries", json={**VALID, "name": "Old Enquirer"}, headers=CLIENT)
        old = [e for e in store.list_enquiries() if e["name"] == "Old Enquirer"][0]
        store._enquiries_by_id()[old["id"]]["created_at"] = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        assert store.purge_enquiries_older_than(30) == 1
        assert [e["name"] for e in store.list_enquiries()] == ["Asha Khan"]


def test_staff_view_falls_back_to_the_existing_cron_secret_when_no_admin_token_is_set(make_client):
    # So a deployment needs no extra secret: the same CRON_SECRET it already has opens the staff view.
    bearer = {"Authorization": "Bearer the-existing-cron-secret-0123"}
    with make_client(admin_token="", cron_secret="the-existing-cron-secret-0123") as client:
        client.post("/enquiries", json=VALID, headers=CLIENT)
        assert client.get("/internal/enquiries", headers=bearer).json()["count"] == 1
        assert client.get("/internal/enquiries", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get("/internal/enquiries").status_code == 401


def test_an_explicit_admin_token_wins_over_the_cron_secret(make_client):
    with make_client(admin_token="staff-token-for-tests-0123", cron_secret="the-existing-cron-secret-0123") as client:
        assert client.get("/internal/enquiries", headers=ADMIN).status_code == 200
        assert client.get("/internal/enquiries", headers={"Authorization": "Bearer the-existing-cron-secret-0123"}).status_code == 401
