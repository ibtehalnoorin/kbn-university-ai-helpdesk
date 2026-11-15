"""Chat API route."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.schemas import ChatRequest, ChatResponse

router = APIRouter(tags=["chat"])


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="Ask the KBN University AI Helpdesk",
    responses={
        409: {"description": "The session is already waiting for an answer."},
        502: {"description": "The local LLM worker returned an error."},
        503: {"description": "The knowledge base or LLM worker is unavailable."},
        504: {"description": "The local model did not finish before the timeout."},
    },
)
async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    return await request.app.state.chat_service.chat(payload)
