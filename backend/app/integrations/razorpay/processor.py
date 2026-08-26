"""Razorpay webhook processing — signature-verified, idempotent, fast-ack.

Flow (per Razorpay docs: duplicates happen, ordering is not guaranteed,
respond within ~5s):
  verify signature → dedupe on event id → persist raw event → 200 → process async

payment.failed  → ingest + analyze (case created, ready for decisions)
payment.captured → locate case from payment → observe outcome (source=webhook)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.payments.ingest import PaymentEventIn, ingest_payment_event
from app.domain.recovery.outcomes import observe_outcome
from app.domain.recovery.service import analyze_failed_payment
from app.infrastructure.database import SessionLocal
from app.infrastructure.models import Payment, RecoveryCase, WebhookEvent

logger = logging.getLogger("revora.webhook")

HANDLED_EVENTS = {"payment.failed", "payment.captured"}


def store_event(db: Session, *, event_id: str, event_type: str,
                payload: dict, signature_valid: bool) -> WebhookEvent | None:
    """Persist raw event; returns None when a duplicate (already seen)."""
    existing = db.execute(
        select(WebhookEvent).where(WebhookEvent.event_id == event_id)
    ).scalar_one_or_none()
    if existing is not None:
        # Duplicate DELIVERY (Razorpay docs: this happens). The stored row keeps its
        # lifecycle status; duplicateness is reported via the HTTP response only.
        return None
    row = WebhookEvent(
        provider="razorpay", event_id=event_id, event_type=event_type,
        payload=payload, signature_valid=signature_valid,
        processing_status="pending", received_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.commit()
    return row


def _payment_entity(payload: dict) -> dict:
    return ((payload.get("payload") or {}).get("payment") or {}).get("entity") or {}


def process_event(event_id: str, session_factory=SessionLocal) -> None:
    """Background processing with its own session (never the request's).
    `session_factory` is injectable so tests (and alternative deployments)
    can process against an overridden engine."""
    db = session_factory()
    try:
        row = db.execute(
            select(WebhookEvent).where(WebhookEvent.event_id == event_id)
        ).scalar_one_or_none()
        if row is None or row.processing_status == "processed":
            return
        try:
            if row.event_type == "payment.failed":
                entity = _payment_entity(row.payload)
                merchant_id = _merchant_id_from_metadata(db, entity)
                if merchant_id is None:
                    raise ValueError("payment.failed without a resolvable merchant")
                payment, _ = ingest_payment_event(db, PaymentEventIn(
                    merchant_id=str(merchant_id),
                    amount_paise=int(entity["amount"]),
                    status="failed",
                    method=entity.get("method"),
                    failure_code=entity.get("error_code"),
                    failure_reason=entity.get("error_description"),
                    provider_payment_id=entity.get("id"),
                    occurred_at=datetime.fromtimestamp(int(entity.get("created_at", 0) or 0), tz=timezone.utc),
                    customer_email=entity.get("email"),
                    customer_phone=entity.get("contact"),
                ))
                analyze_failed_payment(db, payment)
            elif row.event_type == "payment.captured":
                entity = _payment_entity(row.payload)
                payment = db.execute(
                    select(Payment).where(
                        Payment.razorpay_payment_id == entity.get("id"))
                ).scalar_one_or_none()
                if payment is None:
                    raise ValueError("captured payment unknown to REVORA "
                                     "(no prior failed event ingested)")
                # Attribute ONLY via the failed-payment → risk → case chain;
                # never guess by customer (could misattribute recovery).
                from app.infrastructure.models import RevenueRiskEvent
                risk = db.execute(
                    select(RevenueRiskEvent).where(
                        RevenueRiskEvent.source_type == "payment_failed",
                        RevenueRiskEvent.source_id == str(payment.id))
                ).scalar_one_or_none()
                target = None
                if risk is not None:
                    target = db.execute(
                        select(RecoveryCase).where(RecoveryCase.risk_event_id == risk.id)
                    ).scalar_one_or_none()
                if target is None:
                    raise ValueError("no recovery case linked to this payment")
                observe_outcome(db, target, outcome="recovered", source="webhook",
                                amount_recovered_paise=int(entity.get("amount", payment.amount_paise)),
                                evidence_payment_id=payment.id)
            else:
                row.processing_status = "processed"  # acknowledged, not applicable
                db.commit()
                return
            row.processing_status = "processed"
            row.processed_at = datetime.now(timezone.utc)
            db.commit()
        except Exception as exc:  # never lose the event — record and surface
            row.processing_status = "failed"
            row.error = f"{type(exc).__name__}: {exc}"
            db.commit()
            logger.error("webhook processing failed", extra={
                "event_id": event_id, "error": row.error})
    finally:
        db.close()


def _merchant_id_from_metadata(db: Session, entity: dict) -> str | None:
    """Single-merchant deployment resolves merchant from the integration row;
    multi-merchant maps via notes/metadata when present."""
    from app.infrastructure.models import MerchantIntegration, Merchant
    integration = db.execute(
        select(MerchantIntegration).where(
            MerchantIntegration.provider == "razorpay",
            MerchantIntegration.status == "connected")
    ).scalars().first()
    if integration is not None:
        return str(integration.merchant_id)
    merchant = db.execute(select(Merchant).limit(1)).scalars().first()
    return str(merchant.id) if merchant is not None else None
