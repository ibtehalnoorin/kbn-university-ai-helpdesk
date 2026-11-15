"""Typed API schemas for the KBN University AI Helpdesk."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SourceReference(BaseModel):
    id: str
    title: str
    file: str
    url: str | None = None
    chunk_index: int | None = None
    snippet: str = ""
    score: float | None = None
    distance: float | None = None


class Verification(BaseModel):
    status: Literal[
        "verified_kb",
        "general_unverified",
        "unavailable",
        "web_unverified",
    ]
    label: str
    reason: str | None = None


class Message(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: datetime = Field(default_factory=utc_now)
    sources: list[SourceReference] = Field(default_factory=list)
    grounded: bool = False
    verification: Verification | None = None
    persona: str | None = None
    response_time_ms: int | None = None
    llm_time_seconds: float | None = None
    tokens_used: int | None = None
    max_tokens: int | None = None
    error: str | None = None
    # Transparency flags for the speed layer. ``fast_path`` names the
    # deterministic extractor used (if any); ``cached`` marks a repeat answer.
    fast_path: str | None = None
    cached: bool = False


MAX_MESSAGE_CHARS = 1000


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=8, max_length=80)
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)

    @field_validator("message")
    @classmethod
    def clean_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must not be empty")
        return value

    @field_validator("session_id")
    @classmethod
    def clean_session_id(cls, value: str) -> str:
        return value.strip()


class RetrievalInfo(BaseModel):
    attempted: bool = False
    used: bool = False
    query: str | None = None
    n_results: int | None = None
    result_count: int = 0
    duration_ms: int | None = None
    reason: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    user_message: Message
    assistant_message: Message
    sources: list[SourceReference] = Field(default_factory=list)
    grounded: bool = False
    verification: Verification
    persona: str
    response_time_ms: int
    retrieval: RetrievalInfo
    created_at: datetime = Field(default_factory=utc_now)
    fast_path: str | None = None
    cached: bool = False


class SessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=80)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class SessionRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=80)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be empty")
        return value


class SessionSummary(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    preview: str = ""


class SessionDetail(SessionSummary):
    messages: list[Message] = Field(default_factory=list)


class SessionList(BaseModel):
    sessions: list[SessionSummary]
    count: int


class DeleteSessionResponse(BaseModel):
    deleted: bool
    session_id: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "error"]
    app: str
    version: str
    environment: str
    timestamp: datetime
    components: dict[str, Any]
    worker: dict[str, Any]
    configuration: dict[str, Any]
    protected_files: dict[str, Any]
