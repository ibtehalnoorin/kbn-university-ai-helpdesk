"""Postgres-backed session store (same public API as ``SessionStore``).

Each session is one row holding the whole ``SessionRecord`` as JSONB. The API
process keeps no session state in memory, so redeploys and several replicas are
safe. Every mutation locks its row (``FOR UPDATE``) so concurrent writers to the
same session cannot lose messages.
"""

from __future__ import annotations

import functools
import logging
import time
import uuid
import weakref
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
CREATE INDEX IF NOT EXISTS counters_expires_idx ON counters (expires_at);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _retry_if_connection_went_stale(method):
    """Retry once when a pooled connection turned out to be dead (Neon suspends idle ones).

    Cheaper than checking every connection on every checkout, which costs a round trip each time.
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except StoreUnavailableError as exc:
            if not getattr(exc, "retryable", False):
                raise
            return method(self, *args, **kwargs)

    return wrapper


class PostgresSessionStore:
    def __init__(
        self,
        database_url: str,
        max_sessions: int = 100,
        max_messages: int = 500,
        connect_timeout: float = 10,
        idle_check_seconds: float = 20,
    ) -> None:
        self.max_sessions = max_sessions
        self.max_messages = max_messages
        self.idle_check_seconds = idle_check_seconds
        self._schema_ready = False
        self._last_used: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
        # Never block or crash startup: an unreachable database surfaces as
        # StoreUnavailableError per request and as an error in /health.
        self._pool = ConnectionPool(
            database_url,
            min_size=1,
            max_size=5,
            open=False,
            timeout=connect_timeout,
            check=self._check_if_idle,
            # autocommit: a single statement is one round trip, not BEGIN + statement + COMMIT.
            kwargs={
                "connect_timeout": max(1, int(connect_timeout)),
                "prepare_threshold": None,
                "autocommit": True,
            },
        )
        self._pool.open(wait=False)

    # ------------------------------------------------------------------ plumbing
    def _check_if_idle(self, conn) -> None:
        """Only verify connections that sat idle long enough for the server to have dropped them."""
        last = self._last_used.get(conn)
        if last is not None and time.monotonic() - last < self.idle_check_seconds:
            return
        ConnectionPool.check_connection(conn)

    @contextmanager
    def _tx(self, transaction: bool = False):
        """Yield a cursor. Plain statements autocommit; pass ``transaction=True`` for multi-statement atomicity."""
        try:
            with self._pool.connection() as conn:
                try:
                    with conn.cursor() as cur:
                        if not self._schema_ready:
                            cur.execute(SCHEMA)
                            self._schema_ready = True
                        if transaction:
                            with conn.transaction():
                                yield cur
                        else:
                            yield cur
                finally:
                    self._last_used[conn] = time.monotonic()
        except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
            logger.error("Session database unavailable: %s", exc)
            error = StoreUnavailableError("The conversation store is temporarily unavailable.")
            error.retryable = True  # a stale connection: one retry usually gets a fresh one
            raise error from exc
        except PoolTimeout as exc:
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
    @_retry_if_connection_went_stale
    def create(self, title: str | None = None, owner: str | None = None) -> SessionDetail:
        record = SessionRecord(
            id=str(uuid.uuid4()), title=(title or "New conversation")[:80], owner=owner
        )
        with self._tx() as cur:  # one round trip; the cap is a safety valve, not an exact quota
            cur.execute(
                "INSERT INTO sessions (id, data, updated_at) SELECT %s, %s, %s "
                "WHERE (SELECT count(*) FROM sessions) < %s",
                (record.id, Jsonb(record.model_dump(mode="json")), record.updated_at, self.max_sessions),
            )
            if cur.rowcount == 0:
                raise SessionLimitError(
                    f"The session limit of {self.max_sessions} has been reached. "
                    "Delete an old conversation and try again."
                )
        return self._detail(record)

    @_retry_if_connection_went_stale
    def owner_of(self, session_id: str) -> str | None:
        with self._tx() as cur:
            return self._fetch(cur, session_id).owner

    @_retry_if_connection_went_stale
    def list(self, owner: str | None = None) -> list[SessionSummary]:
        with self._tx() as cur:
            cur.execute(
                "SELECT data FROM sessions WHERE data->>'owner' IS NOT DISTINCT FROM %s "
                "ORDER BY updated_at DESC",
                (owner,),
            )
            return [SessionStore._summary(SessionRecord.model_validate(r[0])) for r in cur.fetchall()]

    @_retry_if_connection_went_stale
    def detail(self, session_id: str) -> SessionDetail:
        with self._tx() as cur:
            return self._detail(self._fetch(cur, session_id))

    @_retry_if_connection_went_stale
    def worker_key_for(self, session_id: str) -> str:
        with self._tx() as cur:
            return SessionStore.worker_key(self._fetch(cur, session_id))

    @_retry_if_connection_went_stale
    def recent_user_messages(self, session_id: str, limit: int = 3) -> list[str]:
        with self._tx() as cur:
            record = self._fetch(cur, session_id)
        return [m.content for m in record.messages if m.role == "user"][-limit:]

    def _mutate(self, session_id: str, mutate) -> SessionRecord:
        """Read-modify-write without locks: 2 round trips, retried if another writer got in between.

        The UPDATE only applies if ``updated_at`` is still what we read, so concurrent writers to
        the same session cannot overwrite each other (the loser re-reads and tries again).
        """
        for attempt in range(40):
            with self._tx() as cur:
                cur.execute("SELECT data, updated_at FROM sessions WHERE id = %s", (session_id,))
                row = cur.fetchone()
                if row is None:
                    raise NotFoundError(
                        f"Session '{session_id}' was not found.", details={"session_id": session_id}
                    )
                record, seen = SessionRecord.model_validate(row[0]), row[1]
                mutate(record)
                record.updated_at = _now()
                cur.execute(
                    "UPDATE sessions SET data = %s, updated_at = %s WHERE id = %s AND updated_at = %s",
                    (Jsonb(record.model_dump(mode="json")), record.updated_at, record.id, seen),
                )
                if cur.rowcount == 1:
                    return record
            time.sleep(min(0.005 * (attempt + 1), 0.05))  # brief backoff before re-reading
        raise StoreUnavailableError("The conversation is being updated too often. Please retry.")

    @_retry_if_connection_went_stale
    def append_exchange(self, session_id: str, user_message: Message, assistant_message: Message) -> None:
        def mutate(record: SessionRecord) -> None:
            if record.title == "New conversation" and user_message.content.strip():
                title = " ".join(user_message.content.split())
                record.title = title[:60] + ("..." if len(title) > 60 else "")
            record.messages.append(user_message)
            record.messages.append(assistant_message)
            if len(record.messages) > self.max_messages:
                del record.messages[: len(record.messages) - self.max_messages]

        self._mutate(session_id, mutate)

    @_retry_if_connection_went_stale
    def rename(self, session_id: str, title: str) -> SessionDetail:
        def mutate(record: SessionRecord) -> None:
            record.title = title.strip()[:80]

        return self._detail(self._mutate(session_id, mutate))

    @_retry_if_connection_went_stale
    def clear(self, session_id: str) -> SessionDetail:
        def mutate(record: SessionRecord) -> None:
            record.messages = []
            record.generation += 1
            record.title = "New conversation"

        return self._detail(self._mutate(session_id, mutate))

    @_retry_if_connection_went_stale
    def delete(self, session_id: str) -> None:
        with self._tx() as cur:
            cur.execute("DELETE FROM sessions WHERE id = %s RETURNING id", (session_id,))
            if cur.fetchone() is None:
                raise NotFoundError(
                    f"Session '{session_id}' was not found.", details={"session_id": session_id}
                )

    @_retry_if_connection_went_stale
    def count(self) -> int:
        with self._tx() as cur:
            cur.execute("SELECT count(*) FROM sessions")
            return cur.fetchone()[0]

    @_retry_if_connection_went_stale
    def purge_older_than(self, days: int) -> int:
        cutoff = _now() - timedelta(days=days)
        with self._tx() as cur:
            cur.execute("DELETE FROM sessions WHERE updated_at < %s", (cutoff,))
            return cur.rowcount

    # ------------------------------------------------------------------ shared counters
    @_retry_if_connection_went_stale
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

    @_retry_if_connection_went_stale
    def incr_counters(self, items: "list[tuple[str, int, int, int]]") -> "list[int]":
        """Increment several counters in ONE round trip; returns the new values in the order given.

        ``items`` are ``(key, bucket, ttl_seconds, amount)`` and the (key, bucket) pairs must differ.
        """
        now = _now()
        keys = [i[0] for i in items]
        buckets = [i[1] for i in items]
        amounts = [i[3] for i in items]
        expires = [now + timedelta(seconds=i[2]) for i in items]
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO counters (key, bucket, n, expires_at) "
                "SELECT * FROM unnest(%s::text[], %s::bigint[], %s::int[], %s::timestamptz[]) "
                "ON CONFLICT (key, bucket) DO UPDATE SET n = counters.n + EXCLUDED.n, "
                "expires_at = GREATEST(counters.expires_at, EXCLUDED.expires_at) "
                "RETURNING key, bucket, n",
                (keys, buckets, amounts, expires),
            )
            result = {(k, b): n for k, b, n in cur.fetchall()}
        return [result[(k, b)] for k, b in zip(keys, buckets)]

    @_retry_if_connection_went_stale
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
