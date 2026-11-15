"""Application configuration for the KBN University AI Helpdesk backend.

Core model / vector-store settings mirror the existing read-only project modules.
Environment variables are optional; every default matches the existing project.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# The existing modules live at the project root and use top-level imports.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KBN_",
        env_file=(str(PROJECT_ROOT / ".env"), str(PROJECT_ROOT / "backend" / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "KBN University AI Helpdesk"
    app_version: str = "1.0.0"
    environment: str = "development"
    log_level: str = "INFO"

    host: str = "127.0.0.1"
    port: int = 8000

    # Runtime connection for the existing Ollama client. The model names are
    # fixed by the protected modules; they are surfaced here for health/reporting.
    ollama_host: str = "http://127.0.0.1:11434"
    # The protected processor.py hardcodes qwen3:8b. The worker entry point
    # overrides that runtime value from these settings without editing the file.
    llm_model: str = "qwen3:8b"

    # LLM provider. "ollama" keeps the original local CPU worker; "groq" calls the
    # Groq OpenAI-compatible API (free tier) and is what production uses.
    llm_provider: Literal["ollama", "groq"] = "ollama"
    groq_api_key: str = ""
    groq_model: str = "qwen/qwen3.8-27b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_timeout_seconds: float = 30.0
    groq_max_parallel: int = 8
    embedding_provider: str = "onnx"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    chroma_dir: str = "./local_vector_kb"
    collection_name: str = "local_research_kb"
    # None = do not pin the index size (it grows when the site is re-crawled).
    expected_indexed_chunks: int | None = None

    # Runtime-only generation options injected by worker_entry.py. Keeping them
    # here leaves processor.py byte-identical and makes them easy to tune.
    llm_think: bool = False
    llm_temperature: float = 0.2
    llm_num_ctx: int = 4096
    llm_num_predict: int = 320
    llm_num_thread: int = 8
    llm_keep_alive: str = "30m"
    llm_warmup: bool = True

    # Record-grounded extractive fast path for common catalogue/contact
    # questions. Falls through to the LLM whenever it is not confident.
    fast_answers_enabled: bool = True
    retrieval_cache_ttl_seconds: float = 900.0
    retrieval_cache_max_entries: int = 256
    answer_cache_ttl_seconds: float = 900.0
    answer_cache_max_entries: int = 256

    # The CLI retrieves 2 snippets. The web helpdesk retrieves a slightly wider
    # window so named contacts (e.g. VC email) are not lost; retrieval still uses
    # the existing KnowledgeBase.query_kb implementation unchanged.
    kb_n_results: int = 5
    # qwen3:8b runs on CPU on this machine and has been measured at roughly
    # 20 minutes for a grounded answer. Keep the HTTP request generous; the
    # late-completion worker keeps waiting in the background after a timeout.
    request_timeout_seconds: float = 2400.0
    late_completion_timeout_seconds: float = 7200.0
    retrieval_timeout_seconds: float = 120.0
    web_fallback_timeout_seconds: float = 120.0
    ollama_check_timeout_seconds: float = 8.0

    max_pending_requests: int = 16

    # Abuse protection (in-memory, single instance).
    rate_limit_per_minute: int = 12
    rate_limit_sessions_per_hour: int = 20
    # Groq's free tier allows roughly 1K requests/day; stay under it. 0 = LLM off
    # (fast-path and fixed fallback answers still work).
    daily_llm_budget: int = 900
    # One client address may use at most this many LLM answers per day, so one person
    # (or script) cannot use up everyone's share.
    per_ip_daily_llm_calls: int = 100
    # Per-IP ceiling = per-visitor limit x this, so a campus sharing one address is fine
    # but rotating anonymous ids cannot bypass the limit.
    rate_limit_ip_multiplier: int = 10
    # Number of reverse proxies (Render, Cloudflare...) that append to X-Forwarded-For.
    trusted_proxy_hops: int = 1
    # Global safety valve only (retention and rate limits bound growth); a low cap would
    # lock every new visitor out once enough people had visited.
    max_sessions: int = 100_000
    max_messages_per_session: int = 500

    enable_web_fallback: bool = False
    strict_grounding: bool = True
    allow_general_questions: bool = True

    cors_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:4173,http://127.0.0.1:4173"
    )

    session_store_path: str = "backend/data/sessions.json"
    # When set, chats are stored in Postgres (free Neon/Supabase) instead of the JSON file.
    database_url: str = ""
    session_retention_days: int = 30
    database_connect_timeout_seconds: float = 5.0
    # "Talk to the admissions office": enquiries are stored, optionally forwarded to a webhook
    # (an email service, Google Sheet, Slack...), and read by staff with the admin token.
    admin_token: str = ""  # empty = the staff view is disabled
    enquiry_webhook_url: str = ""
    rate_limit_enquiries_per_hour: int = 5
    enquiry_retention_days: int = 180
    # Vercel Cron authenticates with `Authorization: Bearer $CRON_SECRET`. Empty = cron disabled.
    cron_secret: str = Field(default="", validation_alias=AliasChoices("KBN_CRON_SECRET", "CRON_SECRET"))
    protected_manifest_path: str = "protected_files.sha256"

    # Tests can start the API without spawning the LLM worker.
    start_worker: bool = True

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def session_store_file(self) -> Path:
        path = Path(self.session_store_path)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def protected_manifest_file(self) -> Path:
        path = Path(self.protected_manifest_path)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def chroma_path(self) -> Path:
        path = Path(self.chroma_dir)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def knowledge_docs_path(self) -> Path:
        return PROJECT_ROOT / "knowledge_docs"

    @property
    def frontend_dist_path(self) -> Path:
        return PROJECT_ROOT / "frontend" / "dist"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    # The ollama python client reads OLLAMA_HOST when it creates its client.
    os.environ.setdefault("OLLAMA_HOST", settings.ollama_host)
    return settings
