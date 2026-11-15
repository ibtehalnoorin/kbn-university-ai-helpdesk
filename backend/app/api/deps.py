"""Request helpers shared by the API routes: visitor identity and ownership checks."""

from __future__ import annotations

import re

from fastapi import Request

from app.errors import NotFoundError, ValidationAppError

_CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{16,80}$")


def client_id(request: Request) -> str | None:
    """The browser's anonymous id from ``X-Client-Id`` (None when absent)."""
    value = request.headers.get("x-client-id")
    if value is None:
        return None
    if not _CLIENT_ID.match(value):
        raise ValidationAppError("X-Client-Id must be 16-80 letters, digits, '-' or '_'.")
    return value


def require_owner(request: Request, session_id: str) -> None:
    """404 unless the session is the caller's (ownerless legacy/dev sessions stay open)."""
    owner = request.app.state.session_store.owner_of(session_id)
    if owner is not None and owner != client_id(request):
        raise NotFoundError(
            f"Session '{session_id}' was not found.", details={"session_id": session_id}
        )
