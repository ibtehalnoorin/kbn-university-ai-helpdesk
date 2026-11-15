"""Pick the LLM client for the configured provider."""

from __future__ import annotations

from app.config import Settings
from app.services.groq_client import GroqWorkerClient
from app.services.worker_client import WorkerClient


def make_worker(settings: Settings, ollama_cls: type = WorkerClient):
    """Return a client exposing the ``WorkerClient`` surface for the provider."""
    if settings.llm_provider == "groq":
        return GroqWorkerClient(settings)
    return ollama_cls(settings)
