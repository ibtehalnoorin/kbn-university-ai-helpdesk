"""Adapter around the existing KnowledgeBase / ChromaDB retrieval.

The protected ``kb_manager.py`` is imported and used unchanged. This module only
adds source metadata (file, URL, similarity) and thread-safe coordination.
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.config import PROJECT_ROOT, Settings
from app.errors import RetrievalError
from app.schemas import SourceReference

logger = logging.getLogger("kbn.retrieval")


@dataclass
class RetrievalResult:
    context: str = ""
    sources: list[SourceReference] = field(default_factory=list)
    result_count: int = 0
    query: str = ""
    n_results: int = 0
    duration_ms: int = 0


class RetrievalService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._kb = None
        self._lock = threading.RLock()
        self._source_files: dict[str, dict[str, str | None]] = {}
        self._documents: dict[str, str] = {}
        self._allowed_urls: set[str] = set()
        self._initialized = False
        self._count = 0
        self._cache: dict[tuple[str, int], tuple[float, RetrievalResult]] = {}

    def initialize(self) -> None:
        with self._lock:
            if self._initialized:
                return
            if not self.settings.chroma_path.exists():
                raise RetrievalError(
                    f"ChromaDB directory not found at {self.settings.chroma_path}",
                    details={"path": str(self.settings.chroma_path)},
                )

            # The protected KnowledgeBase uses the original relative path
            # "./local_vector_kb". Initialise it from the project root without
            # permanently changing the process working directory.
            original_cwd = os.getcwd()
            try:
                os.chdir(PROJECT_ROOT)
                from kb_manager import KnowledgeBase

                self._kb = KnowledgeBase()
                self._count = self._kb.collection.count()
            except Exception as exc:  # pragma: no cover - depends on local Chroma
                raise RetrievalError(
                    f"Unable to open the existing ChromaDB collection: {exc}"
                ) from exc
            finally:
                os.chdir(original_cwd)

            self._load_source_metadata()
            self._initialized = True
            logger.info(
                "ChromaDB ready: collection=%s chunks=%s path=%s",
                self.settings.collection_name,
                self._count,
                self.settings.chroma_path,
            )
            # Warm the embedding model once so the first user question does not
            # pay the model-load cost. Failures are non-fatal: retrieval will
            # surface a real error if the embedder is unavailable.
            try:
                self._kb._get_embedding("warmup")
            except Exception as exc:  # pragma: no cover - depends on local Ollama
                logger.warning("Embedding warmup failed: %s", exc)

    def _load_source_metadata(self) -> None:
        docs_dir = self.settings.knowledge_docs_path
        self._allowed_urls = {"https://www.kbn.university"}
        if not docs_dir.exists():
            return
        for path in sorted(docs_dir.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            head = text[:2048]
            match = re.search(r"^\s*#\s*Source:\s*(\S+)", head, flags=re.MULTILINE)
            url = match.group(1).strip() if match else None
            title = _title_for_source(path.name, url)
            self._source_files[path.name] = {"url": url, "title": title}
            # Read-only copy of the same source files that produced the Chroma
            # chunks. Used only to complete catalogue lists; never modified.
            self._documents[path.name] = text
            if url:
                self._allowed_urls.add(url.rstrip("/"))

    def _source_info(
        self,
        chunk_id: str,
        document: str,
        metadata: dict,
        score: float | None,
        distance: float | None,
    ) -> SourceReference:
        file_name = str(metadata.get("source") or "unknown")
        info = self._source_files.get(
            file_name, {"url": None, "title": _title_for_source(file_name, None)}
        )
        return SourceReference(
            id=chunk_id,
            title=str(info.get("title") or file_name),
            file=file_name,
            url=info.get("url"),
            chunk_index=metadata.get("chunk_index"),
            snippet=_clean_snippet(document),
            score=round(score, 4) if score is not None else None,
            distance=round(distance, 4) if distance is not None else None,
        )

    def retrieve(self, query: str, n_results: int | None = None) -> RetrievalResult:
        if not self._initialized or self._kb is None:
            raise RetrievalError("The knowledge base has not been initialised.")

        started = time.perf_counter()
        cache_key = (query.strip().lower(), int(n_results or self.settings.kb_n_results))
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                cached_at, cached_result = cached
                if time.monotonic() - cached_at <= self.settings.retrieval_cache_ttl_seconds:
                    return cached_result
                self._cache.pop(cache_key, None)

            try:
                count = self._kb.collection.count()
                if count == 0:
                    return self._remember(
                        cache_key,
                        RetrievalResult(query=query, duration_ms=_elapsed_ms(started)),
                    )
                n = min(n_results or self.settings.kb_n_results, count)

                # Single embedding + single Chroma query. The protected
                # ``query_kb`` is reused conceptually but calling it here and
                # then querying again would embed the same question twice.
                embedding = self._kb._get_embedding(query)
                raw = self._kb.collection.query(
                    query_embeddings=[embedding],
                    n_results=n,
                    include=["documents", "metadatas", "distances", "embeddings"],
                )
            except Exception as exc:
                logger.exception("Knowledge base retrieval failed")
                raise RetrievalError(f"Knowledge base retrieval failed: {exc}") from exc

            documents = (raw.get("documents") or [[]])[0]
            metadatas = (raw.get("metadatas") or [[]])[0]
            chunk_ids = (raw.get("ids") or [[]])[0]
            distances = (raw.get("distances") or [[]])[0]
            embeddings = (raw.get("embeddings") or [[]])[0]

            if not documents:
                return self._remember(
                    cache_key,
                    RetrievalResult(
                        query=query,
                        n_results=n,
                        duration_ms=_elapsed_ms(started),
                    ),
                )

            # Reproduce the exact context format used by kb_manager.query_kb.
            context = "\n\n--- LOCAL KNOWLEDGE BASE (RAG) ---\n"
            for index, document in enumerate(documents):
                context += f"[KB Snippet {index + 1}]: {document}\n"

            sources: list[SourceReference] = []
            seen: set[str] = set()
            for index, document in enumerate(documents):
                chunk_id = chunk_ids[index] if index < len(chunk_ids) else f"chunk_{index}"
                if chunk_id in seen:
                    continue
                seen.add(chunk_id)
                metadata = metadatas[index] if index < len(metadatas) and metadatas[index] else {}
                distance = distances[index] if index < len(distances) else None
                score = None
                if index < len(embeddings) and embeddings[index] is not None:
                    score = _cosine(embedding, embeddings[index])
                sources.append(
                    self._source_info(chunk_id, document, metadata, score, distance)
                )

            logger.info(
                "Retrieved %s chunks for query=%r in %sms",
                len(sources),
                query[:120],
                _elapsed_ms(started),
            )
            return self._remember(
                cache_key,
                RetrievalResult(
                    context=context,
                    sources=sources,
                    result_count=len(sources),
                    query=query,
                    n_results=n,
                    duration_ms=_elapsed_ms(started),
                ),
            )

    def _remember(
        self, key: tuple[str, int], result: RetrievalResult
    ) -> RetrievalResult:
        self._cache[key] = (time.monotonic(), result)
        while len(self._cache) > self.settings.retrieval_cache_max_entries:
            self._cache.pop(next(iter(self._cache)))
        return result

    def documents_for(self, sources: list[SourceReference]) -> dict[str, str]:
        """Full read-only text for the files that produced the retrieved chunks."""

        with self._lock:
            return {
                source.file: self._documents[source.file]
                for source in sources
                if source.file in self._documents
            }

    @property
    def allowed_urls(self) -> set[str]:
        return set(self._allowed_urls)

    def health(self) -> dict:
        with self._lock:
            if not self._initialized or self._kb is None:
                return {
                    "status": "error",
                    "connected": False,
                    "detail": "Knowledge base not initialised",
                }
            try:
                count = self._kb.collection.count()
            except Exception as exc:
                return {
                    "status": "error",
                    "connected": False,
                    "detail": str(exc),
                }
            return {
                "status": "ok",
                "connected": True,
                "path": str(self.settings.chroma_path),
                "collection": self.settings.collection_name,
                "indexed_chunks": count,
                "expected_chunks": self.settings.expected_indexed_chunks,
                "matches_expected": count == self.settings.expected_indexed_chunks,
                "embedding_model": self.settings.embedding_model,
            }

    def close(self) -> None:
        with self._lock:
            self._kb = None
            self._initialized = False


def _cosine(a: list[float], b: list[float]) -> float:
    if a is None or b is None or len(a) == 0 or len(b) == 0 or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if not norm_a or not norm_b:
        return 0.0
    return dot / (norm_a * norm_b)


def _clean_snippet(document: str, limit: int = 280) -> str:
    text = document or ""
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[#*_>`|]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return f"{text[:limit].rstrip()}..."
    return text


def _title_for_source(file_name: str, url: str | None) -> str:
    if url:
        cleaned = url.rstrip("/")
        if cleaned == "https://www.kbn.university":
            return "KBN University Home"
        path = cleaned.rsplit("/", 1)[-1]
        mapping = {
            "contact": "Contact & Location",
            "courses": "Academic Programs",
            "about": "About KBN University",
            "admissions": "Admissions",
            "phd-admissions": "Ph.D. Admissions",
        }
        if path in mapping:
            return mapping[path]
        return f"KBN University - {path.replace('-', ' ').title()}"
    stem = Path(file_name).stem.replace("_", " ").replace("-", " ").strip()
    return f"KBN University - {stem.title()}" if stem else file_name


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
