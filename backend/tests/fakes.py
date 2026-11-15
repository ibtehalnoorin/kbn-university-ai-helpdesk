"""Deterministic test doubles for the LLM worker and retrieval layer."""

from __future__ import annotations

from concurrent.futures import Future

from app.config import Settings
from app.schemas import SourceReference
from app.services.retrieval import RetrievalResult

VC_CONTEXT = (
    "\n\n--- LOCAL KNOWLEDGE BASE (RAG) ---\n"
    "[KB Snippet 1]: Prof. Ali Raza Moosvi\n### The Vice Chancellor\n"
    "Email : vc@kbn.university\n08472 - 245555,236041,228540\n\n"
    "[KB Snippet 2]: Khaja Bandanawaz University is located in Kalaburagi "
    "(Gulbarga), Karnataka, India. Established 23rd August 2018. 8+ Faculties. "
    "National Rank (IIRF) #27. Contact: contact@kbn.university\n"
)


class FakeRetrieval:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.context = VC_CONTEXT
        self.sources = [
            SourceReference(
                id="chunk-1",
                title="Contact & Location",
                file="kbn_clean_page_3.md",
                url="https://www.kbn.university/contact",
                chunk_index=0,
                snippet="Prof. Ali Raza Moosvi - The Vice Chancellor - vc@kbn.university",
                score=0.91,
                distance=0.18,
            )
        ]

    def initialize(self) -> None:
        return None

    def retrieve(self, query: str, n_results: int | None = None) -> RetrievalResult:
        return RetrievalResult(
            context=self.context,
            sources=list(self.sources),
            result_count=len(self.sources),
            query=query,
            n_results=n_results or self.settings.kb_n_results,
            duration_ms=1,
        )

    def documents_for(self, sources) -> dict[str, str]:
        return {}

    def catalogue_documents(self) -> dict[str, str]:
        return {}

    @property
    def allowed_urls(self) -> set[str]:
        return {"https://www.kbn.university"}

    def health(self) -> dict:
        return {
            "status": "ok",
            "connected": True,
            "path": "fake",
            "collection": self.settings.collection_name,
            "indexed_chunks": 38,
            "expected_chunks": 38,
            "matches_expected": True,
            "embedding_model": self.settings.embedding_model,
        }

    def close(self) -> None:
        return None


class FakeWorker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.answer = "The Vice Chancellor is Prof. Ali Raza Moosvi (vc@kbn.university)."
        self.submissions: list[dict] = []

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def status(self) -> dict:
        return {
            "started": True,
            "alive": True,
            "pid": 4321,
            "pending": 0,
            "model": self.settings.llm_model,
            "timeout_seconds": self.settings.request_timeout_seconds,
        }

    def submit(self, **kwargs) -> Future:
        self.submissions.append(kwargs)
        future: Future = Future()
        future.set_result(
            {
                "status": "success",
                "session_id": kwargs["worker_key"],
                "query": kwargs["query"],
                "answer": self.answer,
                "time": 1.25,
                "persona": kwargs["persona_name"],
                "tokens_used": 128,
                "max_tokens": 8192,
                "utilization": 1.56,
            }
        )
        return future

    async def await_result(self, future: Future, timeout_seconds: float) -> dict:
        return future.result()
