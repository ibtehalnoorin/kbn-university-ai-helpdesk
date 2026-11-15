"""Request helpers shared by the API routes: visitor identity and ownership checks."""

from __future__ import annotations

import re

from fastapi import Request

from app.errors import NotFoundError, RateLimitedError, ValidationAppError
from app.services.rate_limit import check_limits
from app.services.rate_limit import client_ip as _client_ip

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


def _limit(request: Request, limiter_name: str, what: str) -> None:
    """Per-visitor limit (anonymous id) plus a higher per-address ceiling.

    The visitor limit keeps people on a shared campus address from throttling each
    other; the ceiling stops one address from rotating ids to dodge the limit.
    """
    state = request.app.state
    ip = _client_ip(request, state.settings.trusted_proxy_hops)
    visitor = client_id(request) or ip
    retry_after = check_limits(
        [(getattr(state, limiter_name + "_ip"), ip), (getattr(state, limiter_name), visitor)]
    )
    if retry_after is not None:
        raise RateLimitedError(
            f"Too many {what}. Please wait a moment and try again.", retry_after=retry_after
        )


def limit_chat(request: Request) -> None:
    _limit(request, "chat_limiter", "questions")


def limit_enquiries(request: Request) -> None:
    _limit(request, "enquiry_limiter", "enquiries")


def limit_new_sessions(request: Request) -> None:
    _limit(request, "session_limiter", "new conversations")
