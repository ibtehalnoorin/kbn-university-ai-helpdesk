"""LLM provider selection and provider-aware health."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.services import health_service as health_module
from app.services.groq_client import GroqWorkerClient
from app.services.worker_factory import make_worker
from tests.fakes import FakeRetrieval, FakeWorker


def _groq_settings(tmp_path, **overrides) -> Settings:
    base = dict(
        llm_provider="groq",
        groq_api_key="test-key",
        start_worker=False,
        session_store_path=str(tmp_path / "sessions.json"),
    )
    base.update(overrides)
    return Settings(**base)


def test_factory_returns_groq_client_when_configured(tmp_path):
    worker = make_worker(_groq_settings(tmp_path), ollama_cls=FakeWorker)
    assert isinstance(worker, GroqWorkerClient)


def test_factory_returns_ollama_worker_by_default(tmp_path):
    settings = Settings(start_worker=False, session_store_path=str(tmp_path / "s.json"))
    assert isinstance(make_worker(settings, ollama_cls=FakeWorker), FakeWorker)


@pytest.fixture
def groq_app(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "RetrievalService", FakeRetrieval)

    async def forbidden(settings):  # the Ollama host must never be contacted
        raise AssertionError("check_ollama called for the groq provider")

    monkeypatch.setattr(health_module, "check_ollama", forbidden)

    def build(**overrides):
        return TestClient(main_module.create_app(_groq_settings(tmp_path, **overrides)))

    return build


def test_health_skips_ollama_when_provider_is_groq(groq_app):
    with groq_app() as client:
        body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "ollama" not in body["components"]
    assert body["components"]["llm"]["provider"] == "groq"
    assert body["components"]["llm"]["status"] == "ok"
    assert body["configuration"]["llm_model"] == "llama-3.1-8b-instant"


def test_health_degraded_when_groq_key_missing(groq_app):
    with groq_app(groq_api_key="") as client:
        body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert body["components"]["llm"]["status"] == "error"
