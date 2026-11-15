"""Application error types and safe HTTP error responses."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code or self.status_code
        self.code = code or self.code
        self.details = details


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ValidationAppError(AppError):
    status_code = 422
    code = "validation_error"


class SessionBusyError(AppError):
    status_code = 409
    code = "session_busy"


class SessionLimitError(AppError):
    status_code = 429
    code = "session_limit_reached"


class RetrievalError(AppError):
    status_code = 503
    code = "retrieval_unavailable"


class WorkerUnavailableError(AppError):
    status_code = 503
    code = "llm_worker_unavailable"


class WorkerQueueFullError(AppError):
    status_code = 503
    code = "llm_worker_queue_full"


class WorkerTimeoutError(AppError):
    status_code = 504
    code = "llm_timeout"


class WorkerResultError(AppError):
    status_code = 502
    code = "llm_error"


class StoreUnavailableError(AppError):
    status_code = 503
    code = "store_unavailable"


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"

    def __init__(self, message: str, retry_after: int = 60, **kwargs) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class DailyLimitError(AppError):
    status_code = 503
    code = "daily_limit_reached"
