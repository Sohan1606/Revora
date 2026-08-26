"""Build the plain feature dict for a recovery case from real DB rows.

Domain-side companion to intelligence.features (which stays pure): all DB access
happens here, all math happens there.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infrastructure.models import Customer, Payment, RecoveryCase, RevenueRiskEvent
from app.intelligence.recovery_score.features import log_amount_paise


def build_case_features(db: Session, case: RecoveryCase) -> dict[str, Any]:
    risk = db.get(RevenueRiskEvent, case.risk_event_id)
    payment: Payment | None = None
    if risk is not None and risk.source_type == "payment_failed":
        try:
            payment = db.get(Payment, uuid.UUID(risk.source_id))
        except ValueError:
            payment = None

    customer = db.get(Customer, case.customer_id) if case.customer_id else None

    prior_payment_count = prior_failed_count = 0
    if case.customer_id is not None:
        row = db.execute(
            select(
                func.count(func.distinct(Payment.id)).filter(Payment.status == "captured"),
                func.count(func.distinct(Payment.id)).filter(Payment.status == "failed"),
            ).where(Payment.customer_id == case.customer_id)
        ).one()
        prior_payment_count, prior_failed_count = int(row[0] or 0), int(row[1] or 0)

    occurred = payment.occurred_at if payment is not None else None
    return {
        "log_amount": log_amount_paise(case.amount_paise),
        "attempt_number": payment.attempt_number if payment is not None else 1,
        "retry_count": case.retry_count,
        "contact_count": case.contact_count,
        "is_vip": 1 if (customer is not None and customer.is_vip) else 0,
        "prior_payment_count": prior_payment_count,
        "prior_failed_count": prior_failed_count,
        "hour_of_day": occurred.hour if occurred is not None else 12,
        "day_of_month": occurred.day if occurred is not None else 15,
        "method": (payment.method if payment is not None else "other") or "other",
    }
