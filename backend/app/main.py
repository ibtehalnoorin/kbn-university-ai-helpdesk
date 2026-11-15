"""FastAPI application factory and lifespan wiring."""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import chat as chat_routes
from app.api import health as health_routes
from app.api import sessions as session_routes
from app.config import PROJECT_ROOT, Settings, get_settings
from app.errors import AppError
from app.logging_config import configure_logging
from app.services.chat_service import ChatService
from app.services.rate_limit import DailyBudget, SlidingWindowLimiter
from app.services.health_service import HealthService
from app.services.retrieval import RetrievalService
from app.services.store_factory import make_store
from app.services.worker_client import WorkerClient
from app.services.worker_factory import make_worker

logger = logging.getLogger("kbn.api")


async def _purge_once(store, days: int) -> None:
    try:
        removed = await asyncio.to_thread(store.purge_older_than, days)
        if removed:
            logger.info("Retention purge removed %s session(s) older than %s days", removed, days)
    except Exception:
        logger.exception("Retention purge failed")


async def _retention_loop(store, days: int) -> None:
    """Purge old conversations once a day (the first purge runs at startup)."""
    while True:
        await asyncio.sleep(24 * 3600)
        await _purge_once(store, days)


def create_app(settings_override: Settings | None = None) -> FastAPI:
    startup_settings = settings_override or get_settings()
    configure_logging(startup_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = startup_settings
        # The protected modules use top-level imports and the relative
        # "./local_vector_kb" path, so run from the original project root.
        os.chdir(PROJECT_ROOT)

        retrieval = RetrievalService(settings)
        await asyncio.to_thread(retrieval.initialize)

        store = make_store(settings)
        if settings.environment == "production" and not settings.database_url:
            logger.warning(
                "KBN_DATABASE_URL is not set: conversations are kept on local disk and are "
                "lost whenever this instance restarts or sleeps. Set a Postgres URL."
            )
        await _purge_once(store, settings.session_retention_days)
        purge_task = asyncio.create_task(_retention_loop(store, settings.session_retention_days))
        worker = make_worker(settings, ollama_cls=WorkerClient)
        worker_start_error: str | None = None
        if settings.start_worker:
            try:
                worker.start()
            except Exception as exc:  # keep the API up for diagnostics
                worker_start_error = str(exc)
                logger.exception("Could not start the LLM worker at startup")

        chat_service = ChatService(
            settings,
            store,
            retrieval,
            worker,
            budget=DailyBudget(settings.daily_llm_budget, per_key_max=settings.per_ip_daily_llm_calls),
        )
        mult = settings.rate_limit_ip_multiplier
        app.state.chat_limiter = SlidingWindowLimiter(settings.rate_limit_per_minute, 60)
        app.state.chat_limiter_ip = SlidingWindowLimiter(settings.rate_limit_per_minute * mult, 60)
        app.state.session_limiter = SlidingWindowLimiter(settings.rate_limit_sessions_per_hour, 3600)
        app.state.session_limiter_ip = SlidingWindowLimiter(
            settings.rate_limit_sessions_per_hour * mult, 3600
        )
        health_service = HealthService(settings, retrieval, worker, store)

        app.state.settings = settings
        app.state.retrieval = retrieval
        app.state.session_store = store
        app.state.worker = worker
        app.state.chat_service = chat_service
        app.state.health_service = health_service
        app.state.worker_start_error = worker_start_error
        app.state.started_at = time.time()

        logger.info(
            "%s v%s started | collection=%s | model=%s | worker=%s",
            settings.app_name,
            settings.app_version,
            settings.collection_name,
            settings.llm_model,
            worker.status(),
        )
        try:
            yield
        finally:
            logger.info("Shutting down %s", settings.app_name)
            purge_task.cancel()
            await chat_service.shutdown()
            worker.stop()
            retrieval.close()
            close = getattr(store, "close", None)
            if close:
                close()

    app = FastAPI(
        title=startup_settings.app_name,
        version=startup_settings.app_version,
        description=(
            "FastAPI backend for the KBN University AI Helpdesk. It reuses the "
            "existing local Qwen3:8B, ChromaDB knowledge base and RAG/worker "
            "modules without modifying them."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=startup_settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_logging(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "Unhandled request error | %s %s | request_id=%s",
                request.method,
                request.url.path,
                request_id,
            )
            raise
        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-ID"] = request_id
        if request.url.path != "/health":
            logger.info(
                "%s %s -> %s in %sms | request_id=%s",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
                request_id,
            )
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        headers = {"Retry-After": str(exc.retry_after)} if hasattr(exc, "retry_after") else None
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": jsonable_encoder(exc.details),
                }
            },
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "The request did not pass validation.",
                    "details": jsonable_encoder(exc.errors()),
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        logger.exception(
            "Unhandled exception | %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected server error occurred.",
                    "details": None,
                }
            },
        )

    app.include_router(chat_routes.router)
    app.include_router(health_routes.router)
    app.include_router(session_routes.router)

    dist = startup_settings.frontend_dist_path
    if dist.exists() and (dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")
    else:

        @app.get("/", include_in_schema=False)
        async def root():
            return {
                "app": startup_settings.app_name,
                "version": startup_settings.app_version,
                "docs": "/docs",
                "health": "/health",
                "frontend": "Run the Vite dev server in ./frontend, or build it to ./frontend/dist.",
            }

    return app


app = create_app()
