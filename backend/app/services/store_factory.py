"""Choose the session store: Postgres when KBN_DATABASE_URL is set, else a JSON file."""

from __future__ import annotations

from app.config import Settings
from app.services.pg_session_store import PostgresSessionStore
from app.services.session_store import SessionStore


def make_store(settings: Settings):
    if settings.database_url:
        return PostgresSessionStore(
            settings.database_url,
            max_sessions=settings.max_sessions,
            max_messages=settings.max_messages_per_session,
            connect_timeout=settings.database_connect_timeout_seconds,
        )
    return SessionStore(
        path=settings.session_store_file,
        max_sessions=settings.max_sessions,
        max_messages=settings.max_messages_per_session,
    )
