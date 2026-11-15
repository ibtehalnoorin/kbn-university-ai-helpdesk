"""Health reporting for Ollama, ChromaDB, the worker and protected files."""

from __future__ import annotations

import asyncio
import hashlib
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


GROQ_PROBE_TTL = 300  # seconds; /health pings must not hammer Groq
GROQ_PROBE_RETRY = 30  # seconds before trying again after a network failure
_groq_cache: dict[tuple[str, str], tuple[float, int | None]] = {}


def probe_groq(settings: Settings) -> int | None:
    """Ask Groq to list models: proves the key works without using chat quota. None = unreachable."""
    try:
        response = httpx.get(
            f"{settings.groq_base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            timeout=5,
        )
        return response.status_code
    except httpx.HTTPError:
        return None


def check_groq(settings: Settings) -> dict:
    """Is a key set, and does Groq accept it? The answer is cached for a few minutes."""
    base = {"provider": "groq", "model": settings.groq_model}
    if not settings.groq_api_key:
        return {**base, "status": "error", "connected": False, "verified": False,
                "detail": "KBN_GROQ_API_KEY is not set."}
    cache_key = (settings.groq_base_url, hashlib.sha256(settings.groq_api_key.encode()).hexdigest()[:16])
    now = time.monotonic()
    cached = _groq_cache.get(cache_key)
    if cached and cached[0] > now:
        code = cached[1]
    else:
        code = probe_groq(settings)
        _groq_cache[cache_key] = (now + (GROQ_PROBE_TTL if code is not None else GROQ_PROBE_RETRY), code)
    if code in (401, 403):
        return {**base, "status": "error", "connected": False, "verified": False,
                "detail": f"Groq rejected the API key (HTTP {code}). Create a new key and update KBN_GROQ_API_KEY."}
    if code is None:
        return {**base, "status": "ok", "connected": True, "verified": False,
                "detail": "Could not reach Groq to verify the key right now."}
    return {**base, "status": "ok", "connected": True, "verified": code == 200, "detail": None}


class HealthService:
    def __init__(
        self,
        settings: Settings,
        retrieval: RetrievalService,
        worker: WorkerClient,
        store=None,
    ) -> None:
        self.settings = settings
        self.retrieval = retrieval
        self.worker = worker
        self.store = store

    async def check(self) -> HealthResponse:
        use_groq = self.settings.llm_provider == "groq"

        async def _llm_check() -> dict:
            if use_groq:
                return await asyncio.to_thread(check_groq, self.settings)
            return await check_ollama(self.settings)

        ollama_task = _llm_check()
        chroma_task = asyncio.to_thread(self.retrieval.health)
        integrity_task = asyncio.to_thread(
            verify_protected_manifest, self.settings.protected_manifest_file
        )
        store_task = (
            asyncio.to_thread(self.store.health)
            if self.store is not None
            else asyncio.sleep(0, {"status": "ok", "kind": "none"})
        )
        ollama, chromadb, integrity, store = await asyncio.gather(
            ollama_task, chroma_task, integrity_task, store_task
        )
        worker = self.worker.status()

        llm_name = "llm" if use_groq else "ollama"
        ollama_ok = ollama.get("status") == "ok"
        chroma_ok = chromadb.get("status") == "ok"
        worker_ok = bool(worker.get("alive")) or not self.settings.start_worker
        integrity_ok = bool(integrity.get("ok"))
        store_ok = store.get("status") == "ok"

        if ollama_ok and chroma_ok and worker_ok and integrity_ok and store_ok:
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
                "store": store,
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
