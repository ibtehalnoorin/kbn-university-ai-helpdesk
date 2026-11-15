"""Admissions enquiries: visitors ask to be contacted; staff read the queue."""

from __future__ import annotations

import asyncio
import hmac
import logging

import httpx
from fastapi import APIRouter, Depends, Query, Request, status

from app.api.deps import limit_enquiries
from app.errors import AppError
from app.schemas import EnquiryReceipt, EnquiryRequest

logger = logging.getLogger("kbn.enquiries")
router = APIRouter(tags=["enquiries"])


class StaffAuthError(AppError):
    status_code = 401
    code = "unauthorized"


class StaffViewDisabledError(AppError):
    status_code = 503
    code = "staff_view_disabled"


def post_webhook(url: str, payload: dict, timeout: float = 5) -> None:
    httpx.post(url, json=payload, timeout=timeout).raise_for_status()


@router.post(
    "/enquiries",
    response_model=EnquiryReceipt,
    status_code=status.HTTP_201_CREATED,
    summary="Ask the admissions office to contact you",
    dependencies=[Depends(limit_enquiries)],
)
async def create_enquiry(payload: EnquiryRequest, request: Request) -> EnquiryReceipt:
    settings = request.app.state.settings
    row = await asyncio.to_thread(
        request.app.state.session_store.create_enquiry,
        payload.name,
        payload.contact,
        payload.topic,
        payload.message,
        payload.session_id,
    )
    if settings.enquiry_webhook_url:
        try:  # best effort: the enquiry is already saved, so a failing webhook must not lose it
            await asyncio.to_thread(post_webhook, settings.enquiry_webhook_url, row)
        except Exception:
            logger.exception("Could not forward enquiry %s to the webhook", row["id"])
    return EnquiryReceipt(id=row["id"])


@router.get("/internal/enquiries", include_in_schema=False)
async def list_enquiries(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict:
    settings = request.app.state.settings
    # KBN_ADMIN_TOKEN if set, otherwise the CRON_SECRET the deployment already has.
    token = settings.admin_token or settings.cron_secret
    if not token:
        raise StaffViewDisabledError("Set KBN_ADMIN_TOKEN (or CRON_SECRET) to enable the staff view.")
    supplied = request.headers.get("authorization", "")
    if not hmac.compare_digest(supplied.encode(), f"Bearer {token}".encode()):
        raise StaffAuthError("Invalid staff credentials.")
    rows = await asyncio.to_thread(request.app.state.session_store.list_enquiries, status_filter, limit)
    return {"count": len(rows), "enquiries": rows}
