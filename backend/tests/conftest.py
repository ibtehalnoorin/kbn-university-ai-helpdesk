"""Shared fixtures for the KBN University Helpdesk backend tests.

The LLM and ChromaDB are replaced with fakes so the API contract, grounding
guards and session isolation can be tested deterministically without waiting
for Qwen3:8B. Real end-to-end checks live in ``scripts/verify_live.py``.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("KBN_START_WORKER", "false")

import app.main as main_module  # noqa: E402
from app.config import Settings  # noqa: E402
from tests.fakes import FakeRetrieval, FakeWorker  # noqa: E402


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        start_worker=False,
        session_store_path=str(tmp_path / "sessions.json"),
        request_timeout_seconds=5,
        retrieval_timeout_seconds=5,
        # The API contract tests exercise the LLM path. The deterministic
        # extractive fast path has its own tests in ``test_fast_answers.py``.
        fast_answers_enabled=False,
    )


@pytest.fixture
def client(settings, monkeypatch):
    monkeypatch.setattr(main_module, "RetrievalService", FakeRetrieval)
    monkeypatch.setattr(main_module, "WorkerClient", FakeWorker)

    from fastapi.testclient import TestClient

    application = main_module.create_app(settings)
    with TestClient(application) as test_client:
        yield test_client
