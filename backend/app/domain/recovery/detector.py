"""Revenue-risk detection: failed payment → RevenueRiskEvent + RecoveryCase.

Idempotent on (source_type, source_id) — reprocessing the same payment can never
create a second case.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.recovery.audit import record_audit
from app.infrastructure.models import Payment, RecoveryCase, RevenueRiskEvent


@dataclass
class DetectionResult:
    risk_event: RevenueRiskEvent
    case: RecoveryCase
    created: bool


def detect_from_failed_payment(db: Session, payment: Payment) -> DetectionResult:
    if payment.status != "failed":
        raise ValueError("detect_from_failed_payment requires a failed payment")

    source_id = str(payment.id)
    existing = db.execute(
        select(RevenueRiskEvent).where(
            RevenueRiskEvent.source_type == "payment_failed",
            RevenueRiskEvent.source_id == source_id,
        )
    ).scalar_one_or_none()

    if existing is not None:
        case = db.execute(
            select(RecoveryCase).where(RecoveryCase.risk_event_id == existing.id)
        ).scalar_one_or_none()
        if case is not None:
            return DetectionResult(existing, case, created=False)
        raise RuntimeError(
            f"risk event {existing.id} exists without a case — data integrity violation"
        )

    now = datetime.now(timezone.utc)
    risk = RevenueRiskEvent(
        merchant_id=payment.merchant_id,
        source_type="payment_failed",
        source_id=source_id,
        amount_paise=payment.amount_paise,
        currency=payment.currency,
        detected_at=now,
        status="case_created",
        is_synthetic=payment.is_synthetic,
    )
    db.add(risk)
    db.flush()

    case = RecoveryCase(
        merchant_id=payment.merchant_id,
        risk_event_id=risk.id,
        customer_id=payment.customer_id,
        amount_paise=payment.amount_paise,
        currency=payment.currency,
        state="at_risk",
        opened_at=now,
        is_synthetic=payment.is_synthetic,
    )
    db.add(case)
    db.flush()

    record_audit(
        db,
        event_type="risk_detected",
        merchant_id=payment.merchant_id,
        case_id=case.id,
        actor_type="system",
        payload={
            "source_type": "payment_failed",
            "payment_id": source_id,
            "amount_paise": payment.amount_paise,
            "currency": payment.currency,
            "is_synthetic": payment.is_synthetic,
        },
    )
    return DetectionResult(risk, case, created=True)
