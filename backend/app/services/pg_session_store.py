"""Postgres-backed session store (same public API as ``SessionStore``).

Each session is one row holding the whole ``SessionRecord`` as JSONB. The API
process keeps no session state in memory, so redeploys and several replicas are
safe. Every mutation locks its row (``FOR UPDATE``) so concurrent writers to the
same session cannot lose messages.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool, PoolTimeout

from app.errors import NotFoundError, SessionLimitError, StoreUnavailableError
from app.schemas import Message, SessionDetail, SessionSummary
from app.services.session_store import SessionRecord, SessionStore

logger = logging.getLogger("kbn.sessions.pg")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id text PRIMARY KEY,
    data jsonb NOT NULL,
    updated_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_updated_at_idx ON sessions (updated_at);
CREATE TABLE IF NOT EXISTS counters (
    key text NOT NULL,
    bucket bigint NOT NULL,
    n integer NOT NULL,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (key, bucket)
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PostgresSessionStore:
    def __init__(
        self,
        database_url: str,
        max_sessions: int = 100,
        max_messages: int = 500,
        connect_timeout: float = 10,
    ) -> None:
        self.max_sessions = max_sessions
        self.max_messages = max_messages
        self._schema_ready = False
        # Never block or crash startup: an unreachable database surfaces as
        # StoreUnavailableError per request and as an error in /health.
        self._pool = ConnectionPool(
            database_url,
            min_size=1,
            max_size=5,
            open=False,
            timeout=connect_timeout,
            check=ConnectionPool.check_connection,  # Neon drops idle connections
            kwargs={"connect_timeout": max(1, int(connect_timeout)), "prepare_threshold": None},
        )
        self._pool.open(wait=False)

    # ------------------------------------------------------------------ plumbing
    @contextmanager
    def _tx(self):
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    if not self._schema_ready:
                        cur.execute(SCHEMA)
                        conn.commit()
                        self._schema_ready = True
                    yield cur
        except (psycopg.OperationalError, psycopg.InterfaceError, PoolTimeout) as exc:
            logger.error("Session database unavailable: %s", exc)
            raise StoreUnavailableError("The conversation store is temporarily unavailable.") from exc

    def _fetch(self, cur, session_id: str, lock: bool = False) -> SessionRecord:
        cur.execute(
            "SELECT data FROM sessions WHERE id = %s" + (" FOR UPDATE" if lock else ""),
            (session_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise NotFoundError(
                f"Session '{session_id}' was not found.", details={"session_id": session_id}
            )
        return SessionRecord.model_validate(row[0])

    @staticmethod
    def _write(cur, record: SessionRecord) -> None:
        cur.execute(
            "UPDATE sessions SET data = %s, updated_at = %s WHERE id = %s",
            (Jsonb(record.model_dump(mode="json")), record.updated_at, record.id),
        )

    def _detail(self, record: SessionRecord) -> SessionDetail:
        summary = SessionStore._summary(record)
        return SessionDetail(**summary.model_dump(), messages=list(record.messages))

    # ------------------------------------------------------------------ public API
    def create(self, title: str | None = None, owner: str | None = None) -> SessionDetail:
        record = SessionRecord(
            id=str(uuid.uuid4()), title=(title or "New conversation")[:80], owner=owner
        )
        with self._tx() as cur:
            cur.execute("LOCK TABLE sessions IN SHARE ROW EXCLUSIVE MODE")
            cur.execute("SELECT count(*) FROM sessions")
            if cur.fetchone()[0] >= self.max_sessions:
                raise SessionLimitError(
                    f"The session limit of {self.max_sessions} has been reached. "
                    "Delete an old conversation and try again."
                )
            cur.execute(
                "INSERT INTO sessions (id, data, updated_at) VALUES (%s, %s, %s)",
                (record.id, Jsonb(record.model_dump(mode="json")), record.updated_at),
            )
        return self._detail(record)

    def owner_of(self, session_id: str) -> str | None:
        with self._tx() as cur:
            return self._fetch(cur, session_id).owner

    def list(self, owner: str | None = None) -> list[SessionSummary]:
        with self._tx() as cur:
            cur.execute(
                "SELECT data FROM sessions WHERE data->>'owner' IS NOT DISTINCT FROM %s "
                "ORDER BY updated_at DESC",
                (owner,),
            )
            return [SessionStore._summary(SessionRecord.model_validate(r[0])) for r in cur.fetchall()]

    def detail(self, session_id: str) -> SessionDetail:
        with self._tx() as cur:
            return self._detail(self._fetch(cur, session_id))

    def worker_key_for(self, session_id: str) -> str:
        with self._tx() as cur:
            return SessionStore.worker_key(self._fetch(cur, session_id))

    def recent_user_messages(self, session_id: str, limit: int = 3) -> list[str]:
        with self._tx() as cur:
            record = self._fetch(cur, session_id)
        return [m.content for m in record.messages if m.role == "user"][-limit:]

    def append_exchange(self, session_id: str, user_message: Message, assistant_message: Message) -> None:
        with self._tx() as cur:
            record = self._fetch(cur, session_id, lock=True)
            if record.title == "New conversation" and user_message.content.strip():
                title = " ".join(user_message.content.split())
                record.title = title[:60] + ("..." if len(title) > 60 else "")
            record.messages.append(user_message)
            record.messages.append(assistant_message)
            if len(record.messages) > self.max_messages:
                del record.messages[: len(record.messages) - self.max_messages]
            record.updated_at = _now()
            self._write(cur, record)

    def rename(self, session_id: str, title: str) -> SessionDetail:
        with self._tx() as cur:
            record = self._fetch(cur, session_id, lock=True)
            record.title = title.strip()[:80]
            record.updated_at = _now()
            self._write(cur, record)
        return self._detail(record)

    def clear(self, session_id: str) -> SessionDetail:
        with self._tx() as cur:
            record = self._fetch(cur, session_id, lock=True)
            record.messages = []
            record.generation += 1
            record.title = "New conversation"
            record.updated_at = _now()
            self._write(cur, record)
        return self._detail(record)

    def delete(self, session_id: str) -> None:
        with self._tx() as cur:
            self._fetch(cur, session_id, lock=True)
            cur.execute("DELETE FROM sessions WHERE id = %s", (session_id,))

    def count(self) -> int:
        with self._tx() as cur:
            cur.execute("SELECT count(*) FROM sessions")
            return cur.fetchone()[0]

    def purge_older_than(self, days: int) -> int:
        cutoff = _now() - timedelta(days=days)
        with self._tx() as cur:
            cur.execute("DELETE FROM sessions WHERE updated_at < %s", (cutoff,))
            return cur.rowcount

    # ------------------------------------------------------------------ shared counters
    def incr_counter(self, key: str, bucket: int, ttl_seconds: int, amount: int = 1) -> int:
        """Atomically add ``amount`` to a counter shared by every server instance."""
        expires = _now() + timedelta(seconds=ttl_seconds)
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO counters (key, bucket, n, expires_at) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (key, bucket) DO UPDATE SET n = counters.n + EXCLUDED.n, "
                "expires_at = GREATEST(counters.expires_at, EXCLUDED.expires_at) RETURNING n",
                (key, bucket, amount, expires),
            )
            return cur.fetchone()[0]

    def purge_counters(self) -> int:
        with self._tx() as cur:
            cur.execute("DELETE FROM counters WHERE expires_at < %s", (_now(),))
            return cur.rowcount

    def health(self) -> dict:
        try:
            return {"status": "ok", "kind": "postgres", "sessions": self.count()}
        except StoreUnavailableError as exc:
            return {"status": "error", "kind": "postgres", "detail": exc.message}

    def close(self) -> None:
        self._pool.close()

    # ------------------------------------------------------------------ test hooks
    def truncate_for_tests(self) -> None:
        with self._tx() as cur:
            cur.execute("TRUNCATE sessions, counters")

    def set_updated_at_for_tests(self, session_id: str, when: datetime) -> None:
        with self._tx() as cur:
            cur.execute("UPDATE sessions SET updated_at = %s WHERE id = %s", (when, session_id))
