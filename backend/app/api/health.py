"""Health API route."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Check Ollama, ChromaDB, worker and protected-file integrity",
)
async def health(request: Request) -> HealthResponse:
    return await request.app.state.health_service.check()
