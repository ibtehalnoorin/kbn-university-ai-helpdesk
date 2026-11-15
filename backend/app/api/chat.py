"""Chat API route."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.deps import limit_chat, require_owner
from app.services.rate_limit import client_ip, current_client_key
from app.schemas import ChatRequest, ChatResponse

router = APIRouter(tags=["chat"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Ask the KBN University AI Helpdesk",
    dependencies=[Depends(limit_chat)],
    responses={
        409: {"description": "The session is already waiting for an answer."},
        502: {"description": "The local LLM worker returned an error."},
        503: {"description": "The knowledge base or LLM worker is unavailable."},
        504: {"description": "The local model did not finish before the timeout."},
    },
)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    require_owner(request, payload.session_id)
    token = current_client_key.set(client_ip(request, request.app.state.settings.trusted_proxy_hops))
    try:
        return await request.app.state.chat_service.chat(payload)
    finally:
        current_client_key.reset(token)
