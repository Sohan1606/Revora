"""Razorpay webhook endpoint — verify, dedupe, persist, fast-ack, process async."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.ratelimit import rate_limit
from app.infrastructure.database import get_db, get_session_factory
from app.integrations.razorpay.processor import HANDLED_EVENTS, process_event, store_event
from app.integrations.razorpay.webhook import verify_webhook_signature

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger("revora.webhook")


MAX_WEBHOOK_BYTES = 1_048_576  # 1 MiB — Razorpay payloads are tiny; larger = abuse


@router.post("/razorpay")
async def razorpay_webhook(
    request: Request,
    background: BackgroundTasks,
    response: Response,
    db: Session = Depends(get_db),
    session_factory=Depends(get_session_factory),
    _: None = Depends(rate_limit(limit=120, window_seconds=60, scope="webhook")),
) -> dict:
    if int(request.headers.get("content-length") or 0) > MAX_WEBHOOK_BYTES:
        response.status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
        return {"status": "rejected", "reason": "payload too large"}

    settings = get_settings()
    if not settings.is_razorpay_webhook_configured:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "rejected",
                "reason": "webhook secret not configured (RAZORPAY_WEBHOOK_SECRET)"}

    raw = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")

    if not verify_webhook_signature(raw, signature, settings.RAZORPAY_WEBHOOK_SECRET):
        logger.warning("invalid webhook signature rejected", extra={"bytes": len(raw)})
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"status": "rejected", "reason": "invalid signature"}

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"status": "rejected", "reason": "invalid json"}

    event_id = payload.get("id")
    event_type = payload.get("event", "")
    if not event_id:
        response.status_code = status.HTTP_400_BAD_REQUEST
        return {"status": "rejected", "reason": "missing event id"}

    row = store_event(db, event_id=event_id, event_type=event_type,
                      payload=payload, signature_valid=True)
    if row is None:
        return {"status": "duplicate"}  # idempotent: already seen this exact event

    if event_type in HANDLED_EVENTS:
        background.add_task(process_event, event_id, session_factory)  # fast ack, async
    else:
        row.processing_status = "processed"  # acknowledged, not applicable
        db.commit()
    return {"status": "accepted"}
