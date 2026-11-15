"""Session management API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import client_id, limit_new_sessions, require_owner
from app.errors import SessionBusyError
from app.schemas import (
    DeleteSessionResponse,
    SessionCreateRequest,
    SessionDetail,
    SessionList,
    SessionRenameRequest,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _chat(request: Request):
    return request.app.state.chat_service


@router.post(
    "",
    response_model=SessionDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a conversation session",
    dependencies=[Depends(limit_new_sessions)],
)
async def create_session(
    request: Request,
    payload: SessionCreateRequest | None = None,
) -> SessionDetail:
    return request.app.state.session_store.create(
        payload.title if payload else None, owner=client_id(request)
    )


@router.get("", response_model=SessionList, summary="List conversation sessions")
async def list_sessions(request: Request) -> SessionList:
    sessions = request.app.state.session_store.list(owner=client_id(request))
    return SessionList(sessions=sessions, count=len(sessions))


@router.get("/{session_id}", response_model=SessionDetail, summary="Get a session transcript")
async def get_session(session_id: str, request: Request) -> SessionDetail:
    require_owner(request, session_id)
    return request.app.state.session_store.detail(session_id)


@router.patch("/{session_id}", response_model=SessionDetail, summary="Rename a session")
async def rename_session(
    session_id: str,
    payload: SessionRenameRequest,
    request: Request,
) -> SessionDetail:
    require_owner(request, session_id)
    return request.app.state.session_store.rename(session_id, payload.title)


@router.delete(
    "/{session_id}",
    response_model=DeleteSessionResponse,
    summary="Delete a session and its transcript",
)
async def delete_session(session_id: str, request: Request) -> DeleteSessionResponse:
    require_owner(request, session_id)
    if _chat(request).is_busy(session_id):
        raise SessionBusyError(
            "This conversation is still waiting for an answer and cannot be deleted."
        )
    request.app.state.session_store.delete(session_id)
    return DeleteSessionResponse(deleted=True, session_id=session_id)


@router.post(
    "/{session_id}/clear",
    response_model=SessionDetail,
    summary="Clear a conversation while keeping the session",
)
async def clear_session(session_id: str, request: Request) -> SessionDetail:
    require_owner(request, session_id)
    if _chat(request).is_busy(session_id):
        raise SessionBusyError(
            "This conversation is still waiting for an answer and cannot be cleared."
        )
    return request.app.state.session_store.clear(session_id)
