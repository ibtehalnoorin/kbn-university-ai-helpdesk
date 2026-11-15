"""Scheduled maintenance, called by Vercel Cron (a serverless app has no daily timer)."""

from __future__ import annotations

import asyncio
import hmac

from fastapi import APIRouter, Request

from app.errors import AppError

router = APIRouter(tags=["internal"])


class CronAuthError(AppError):
    status_code = 401
    code = "unauthorized"


class CronDisabledError(AppError):
    status_code = 503
    code = "cron_disabled"


@router.get("/internal/cron/purge", include_in_schema=False)
async def purge(request: Request) -> dict:
    settings = request.app.state.settings
    if not settings.cron_secret:
        raise CronDisabledError("Set CRON_SECRET to enable scheduled cleanup.")
    supplied = request.headers.get("authorization", "")
    expected = f"Bearer {settings.cron_secret}"
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise CronAuthError("Invalid cron credentials.")
    store = request.app.state.session_store
    sessions = await asyncio.to_thread(store.purge_older_than, settings.session_retention_days)
    purge_counters = getattr(store, "purge_counters", None)
    counters = await asyncio.to_thread(purge_counters) if purge_counters else 0
    purge_enquiries = getattr(store, "purge_enquiries_older_than", None)
    enquiries = (
        await asyncio.to_thread(purge_enquiries, settings.enquiry_retention_days) if purge_enquiries else 0
    )
    return {"sessions_removed": sessions, "counters_removed": counters, "enquiries_removed": enquiries}
