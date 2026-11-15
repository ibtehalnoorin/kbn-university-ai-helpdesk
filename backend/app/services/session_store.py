"""Persistent session and transcript store.

The existing worker keeps its own LLM history in memory. This store keeps the
user-visible transcript and assigns each clear/delete a new worker generation so
old model context can never leak into a cleared session.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from app.errors import NotFoundError, SessionLimitError
from app.schemas import Message, SessionDetail, SessionSummary

logger = logging.getLogger("kbn.sessions")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_message_id() -> str:
    return f"msg_{uuid.uuid4().hex}"


class SessionRecord(BaseModel):
    id: str
    title: str = "New conversation"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    generation: int = 0
    messages: list[Message] = Field(default_factory=list)


class SessionStore:
    def __init__(self, path: Path, max_sessions: int = 100, max_messages: int = 500) -> None:
        self.path = path
        self.max_sessions = max_sessions
        self.max_messages = max_messages
        self._sessions: dict[str, SessionRecord] = {}
        self._lock = threading.RLock()
        self._load()

    # ------------------------------------------------------------------ load/save
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            for raw in payload.get("sessions", []):
                record = SessionRecord.model_validate(raw)
                self._sessions[record.id] = record
            logger.info("Loaded %s session(s) from %s", len(self._sessions), self.path)
        except Exception as exc:
            logger.warning("Could not load session store %s: %s", self.path, exc)
            backup = self.path.with_suffix(f"{self.path.suffix}.corrupt")
            try:
                os.replace(self.path, backup)
                logger.warning("Moved unreadable session store to %s", backup)
            except OSError:
                pass

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "saved_at": _now().isoformat(),
                "sessions": [record.model_dump(mode="json") for record in self._sessions.values()],
            }
            temp = self.path.with_suffix(f"{self.path.suffix}.tmp")
            temp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp, self.path)
        except Exception as exc:
            logger.error("Could not persist session store: %s", exc)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def worker_key(record: SessionRecord) -> str:
        return f"{record.id}:g{record.generation}"

    @staticmethod
    def _summary(record: SessionRecord) -> SessionSummary:
        preview = ""
        if record.messages:
            preview = record.messages[-1].content.replace("\n", " ").strip()[:90]
        return SessionSummary(
            id=record.id,
            title=record.title,
            created_at=record.created_at,
            updated_at=record.updated_at,
            message_count=len(record.messages),
            preview=preview,
        )

    def _get_record(self, session_id: str) -> SessionRecord:
        record = self._sessions.get(session_id)
        if record is None:
            raise NotFoundError(
                f"Session '{session_id}' was not found.",
                details={"session_id": session_id},
            )
        return record

    # ------------------------------------------------------------------ public API
    def create(self, title: str | None = None) -> SessionDetail:
        with self._lock:
            if len(self._sessions) >= self.max_sessions:
                raise SessionLimitError(
                    f"The session limit of {self.max_sessions} has been reached. "
                    "Delete an old conversation and try again."
                )
            record = SessionRecord(
                id=str(uuid.uuid4()),
                title=(title or "New conversation")[:80],
            )
            self._sessions[record.id] = record
            self._save()
            return self.detail(record.id)

    def list(self) -> list[SessionSummary]:
        with self._lock:
            records = sorted(
                self._sessions.values(),
                key=lambda item: item.updated_at,
                reverse=True,
            )
            return [self._summary(record) for record in records]

    def detail(self, session_id: str) -> SessionDetail:
        with self._lock:
            record = self._get_record(session_id)
            summary = self._summary(record)
            return SessionDetail(
                **summary.model_dump(),
                messages=[message.model_copy(deep=True) for message in record.messages],
            )

    def worker_key_for(self, session_id: str) -> str:
        with self._lock:
            return self.worker_key(self._get_record(session_id))

    def recent_user_messages(self, session_id: str, limit: int = 3) -> list[str]:
        with self._lock:
            record = self._get_record(session_id)
            return [
                message.content
                for message in record.messages
                if message.role == "user"
            ][-limit:]

    def append_exchange(self, session_id: str, user_message: Message, assistant_message: Message) -> None:
        with self._lock:
            record = self._get_record(session_id)
            if record.title == "New conversation" and user_message.content.strip():
                title = " ".join(user_message.content.split())
                record.title = title[:60] + ("..." if len(title) > 60 else "")
            record.messages.append(user_message)
            record.messages.append(assistant_message)
            if len(record.messages) > self.max_messages:
                del record.messages[: len(record.messages) - self.max_messages]
            record.updated_at = _now()
            self._save()

    def rename(self, session_id: str, title: str) -> SessionDetail:
        with self._lock:
            record = self._get_record(session_id)
            record.title = title.strip()[:80]
            record.updated_at = _now()
            self._save()
            return self.detail(session_id)

    def clear(self, session_id: str) -> SessionDetail:
        with self._lock:
            record = self._get_record(session_id)
            record.messages = []
            record.generation += 1
            record.title = "New conversation"
            record.updated_at = _now()
            self._save()
            return self.detail(session_id)

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._get_record(session_id)
            del self._sessions[session_id]
            self._save()

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def purge_older_than(self, days: int) -> int:
        """Delete sessions untouched for ``days`` days; return how many were removed."""
        cutoff = _now() - timedelta(days=days)
        with self._lock:
            stale = [sid for sid, rec in self._sessions.items() if rec.updated_at < cutoff]
            for sid in stale:
                del self._sessions[sid]
            if stale:
                self._save()
            return len(stale)

    def health(self) -> dict:
        return {"status": "ok", "kind": "json", "sessions": self.count()}
