"""Health reporting for Ollama, ChromaDB, the worker and protected files."""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from app.config import Settings
from app.schemas import HealthResponse, utc_now
from app.services.grounding import FALLBACK_MESSAGE
from app.services.integrity import verify_protected_manifest
from app.services.retrieval import RetrievalService
from app.services.worker_client import WorkerClient

logger = logging.getLogger("kbn.health")


async def check_ollama(settings: Settings) -> dict:
    started = time.perf_counter()
    url = f"{settings.ollama_host.rstrip('/')}/api/tags"
    try:
        async with httpx.AsyncClient(timeout=settings.ollama_check_timeout_seconds) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        return {
            "status": "error",
            "connected": False,
            "host": settings.ollama_host,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "detail": str(exc),
            "llm_model": settings.llm_model,
            "embedding_model": settings.embedding_model,
            "llm_model_available": False,
            "embedding_model_available": False,
        }

    names = [item.get("name", "") for item in payload.get("models", [])]
    llm_available = any(
        name == settings.llm_model or name.startswith(f"{settings.llm_model}:")
        for name in names
    )
    embedding_available = any(
        name == settings.embedding_model or name.startswith(f"{settings.embedding_model}:")
        for name in names
    )
    return {
        "status": "ok" if llm_available and embedding_available else "degraded",
        "connected": True,
        "host": settings.ollama_host,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "models": names,
        "llm_model": settings.llm_model,
        "embedding_model": settings.embedding_model,
        "llm_model_available": llm_available,
        "embedding_model_available": embedding_available,
    }


def check_groq(settings: Settings) -> dict:
    """Config-only check: no network call, so /health never spends free-tier quota."""
    has_key = bool(settings.groq_api_key)
    return {
        "provider": "groq",
        "status": "ok" if has_key else "error",
        "connected": has_key,
        "model": settings.groq_model,
        "detail": None if has_key else "KBN_GROQ_API_KEY is not set.",
    }


class HealthService:
    def __init__(
        self,
        settings: Settings,
        retrieval: RetrievalService,
        worker: WorkerClient,
    ) -> None:
        self.settings = settings
        self.retrieval = retrieval
        self.worker = worker

    async def check(self) -> HealthResponse:
        use_groq = self.settings.llm_provider == "groq"

        async def _llm_check() -> dict:
            return check_groq(self.settings) if use_groq else await check_ollama(self.settings)

        ollama_task = _llm_check()
        chroma_task = asyncio.to_thread(self.retrieval.health)
        integrity_task = asyncio.to_thread(
            verify_protected_manifest, self.settings.protected_manifest_file
        )
        ollama, chromadb, integrity = await asyncio.gather(
            ollama_task, chroma_task, integrity_task
        )
        worker = self.worker.status()

        llm_name = "llm" if use_groq else "ollama"
        ollama_ok = ollama.get("status") == "ok"
        chroma_ok = chromadb.get("status") == "ok"
        worker_ok = bool(worker.get("alive")) or not self.settings.start_worker
        integrity_ok = bool(integrity.get("ok"))

        if ollama_ok and chroma_ok and worker_ok and integrity_ok:
            status = "ok"
        elif chromadb.get("connected") or ollama.get("connected") or worker.get("alive"):
            status = "degraded"
        else:
            status = "error"

        return HealthResponse(
            status=status,
            app=self.settings.app_name,
            version=self.settings.app_version,
            environment=self.settings.environment,
            timestamp=utc_now(),
            components={
                llm_name: ollama,
                "chromadb": chromadb,
                "grounding": {
                    "status": "ok" if self.settings.strict_grounding else "degraded",
                    "strict_grounding": self.settings.strict_grounding,
                    "fallback_message": FALLBACK_MESSAGE,
                },
            },
            worker=worker,
            configuration={
                "llm_provider": self.settings.llm_provider,
                "llm_model": (
                    self.settings.groq_model if use_groq else self.settings.llm_model
                ),
                "embedding_model": self.settings.embedding_model,
                "chroma_path": str(self.settings.chroma_path),
                "collection": self.settings.collection_name,
                "expected_indexed_chunks": self.settings.expected_indexed_chunks,
                "kb_n_results": self.settings.kb_n_results,
                "request_timeout_seconds": self.settings.request_timeout_seconds,
                "retrieval_timeout_seconds": self.settings.retrieval_timeout_seconds,
                "web_fallback_enabled": self.settings.enable_web_fallback,
                "strict_grounding": self.settings.strict_grounding,
            },
            protected_files=integrity,
        )
