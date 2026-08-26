"""Idempotent ingestion of normalized payment events.

Used by BOTH the Razorpay webhook handler (Phase 11) and the synthetic corpus
loader (Phase 8) — one code path, so synthetic and live data are processed by
identical logic. Synthetic events MUST pass is_synthetic=True (labeled, never
presented as live merchant data).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.models import Customer, Order, Payment


class PaymentEventIn(BaseModel):
    """Normalized payment event — provider-agnostic internal contract."""

    merchant_id: str
    amount_paise: int = Field(gt=0)
    currency: str = "INR"
    status: str = Field(pattern="^(created|authorized|captured|failed|refunded|partially_refunded)$")
    method: str | None = None
    failure_code: str | None = None
    failure_reason: str | None = None
    provider_payment_id: str | None = None
    attempt_number: int = Field(default=1, ge=1)
    occurred_at: datetime
    # Customer
    customer_email: str | None = None
    customer_phone: str | None = None
    customer_name: str | None = None
    # Order linkage (optional)
    order_ref: str | None = None
    order_status: str = "created"
    is_synthetic: bool = False

    @model_validator(mode="after")
    def _failure_fields_require_failed(self) -> "PaymentEventIn":
        if self.status != "failed" and (self.failure_code or self.failure_reason):
            raise ValueError("failure_code/failure_reason only valid when status=failed")
        if self.status == "failed" and not (self.failure_code or self.failure_reason):
            raise ValueError("failed payments must carry a failure code or reason")
        return self


def _get_or_create_customer(db: Session, ev: PaymentEventIn, merchant_uuid: uuid.UUID) -> Customer | None:
    if not (ev.customer_email or ev.customer_phone):
        return None
    stmt = select(Customer).where(
        Customer.merchant_id == merchant_uuid,
        Customer.email == ev.customer_email if ev.customer_email else Customer.phone == ev.customer_phone,
    )
    existing = db.execute(stmt).scalar_one_or_none()
    if existing:
        return existing
    customer = Customer(
        merchant_id=merchant_uuid,
        email=ev.customer_email,
        phone=ev.customer_phone,
        name=ev.customer_name,
    )
    db.add(customer)
    db.flush()
    return customer


def ingest_payment_event(db: Session, ev: PaymentEventIn) -> tuple[Payment, bool]:
    """Insert or update a payment. Returns (payment, created).

    Idempotency: dedupe on provider_payment_id when present.
    """
    # Coerce at the domain boundary: API payloads carry strings; columns carry UUID.
    try:
        merchant_uuid = uuid.UUID(str(ev.merchant_id))
    except ValueError as e:
        raise ValueError(f"merchant_id is not a valid UUID: {ev.merchant_id}") from e

    if ev.provider_payment_id:
        existing = db.execute(
            select(Payment).where(Payment.razorpay_payment_id == ev.provider_payment_id)
        ).scalar_one_or_none()
        if existing:
            # Update mutable status fields only; never duplicate the row.
            existing.status = ev.status
            existing.failure_code = ev.failure_code
            existing.failure_reason = ev.failure_reason
            existing.attempt_number = ev.attempt_number
            return existing, False

    customer = _get_or_create_customer(db, ev, merchant_uuid)

    order: Order | None = None
    if ev.order_ref:
        order = db.execute(
            select(Order).where(
                Order.merchant_id == merchant_uuid, Order.source == ev.order_ref
            )
        ).scalar_one_or_none()
        if not order:
            order = Order(
                merchant_id=merchant_uuid,
                customer_id=customer.id if customer else None,
                amount_paise=ev.amount_paise,
                currency=ev.currency,
                status=ev.order_status,
                source=ev.order_ref,
                is_synthetic=ev.is_synthetic,
            )
            db.add(order)
            db.flush()

    payment = Payment(
        merchant_id=merchant_uuid,
        order_id=order.id if order else None,
        customer_id=customer.id if customer else None,
        amount_paise=ev.amount_paise,
        currency=ev.currency,
        method=ev.method,
        status=ev.status,
        failure_code=ev.failure_code,
        failure_reason=ev.failure_reason,
        razorpay_payment_id=ev.provider_payment_id,
        attempt_number=ev.attempt_number,
        occurred_at=ev.occurred_at,
        is_synthetic=ev.is_synthetic,
    )
    db.add(payment)
    db.flush()
    return payment, True
