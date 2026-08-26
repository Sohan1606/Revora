"""Phase 4 tests — ingestion, detection, classification, state machine."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.infrastructure.models  # noqa: F401
from app.domain.payments.ingest import PaymentEventIn, ingest_payment_event
from app.domain.recovery.classifier import classify_failure
from app.domain.recovery.detector import detect_from_failed_payment
from app.domain.recovery.service import analyze_failed_payment
from app.domain.recovery.state_machine import InvalidTransition, transition
from app.infrastructure.database import Base
from app.infrastructure.models import (
    AuditEvent, FailureClassification, Merchant, RecoveryCase, RevenueRiskEvent,
)

MERCHANT = "11111111-1111-1111-1111-111111111111"


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = S()
    session.add(Merchant(id=uuid.UUID(MERCHANT), name="Test Merchant"))
    session.commit()
    yield session
    session.close()
    engine.dispose()


def _failed_event(**kw) -> PaymentEventIn:
    base = dict(
        merchant_id=MERCHANT,
        amount_paise=899900,
        status="failed",
        method="upi",
        failure_code=None,
        failure_reason="Issuer declined: insufficient funds",
        provider_payment_id="pay_TEST_0001",
        occurred_at=datetime.now(timezone.utc),
        customer_email="cust@example.com",
        is_synthetic=True,
    )
    base.update(kw)
    return PaymentEventIn(**base)


# ---------- ingestion ----------

def test_ingest_creates_customer_order_payment(db) -> None:
    payment, created = ingest_payment_event(db, _failed_event(order_ref="order_TEST_1"))
    assert created is True
    assert payment.customer_id is not None
    assert payment.order_id is not None
    assert payment.is_synthetic is True


def test_ingest_is_idempotent_on_provider_id(db) -> None:
    _, c1 = ingest_payment_event(db, _failed_event())
    same, c2 = ingest_payment_event(
        db, _failed_event(status="captured", failure_code=None, failure_reason=None)
    )
    assert (c1, c2) == (True, False)
    assert same.status == "captured"  # status updated, no duplicate row


def test_ingest_rejects_failure_fields_on_success(db) -> None:
    with pytest.raises(Exception):
        _failed_event(status="captured")  # carries failure_reason while captured
    with pytest.raises(Exception):
        _failed_event(failure_code=None, failure_reason=None)  # failed w/o reason


# ---------- classification ----------

def test_classifier_exact_code() -> None:
    r = classify_failure(failure_code="CARD_EXPIRED", failure_reason=None)
    assert (r.primary_cause, r.confidence) == ("method_expired", 0.97)


def test_classifier_keyword_nsf() -> None:
    r = classify_failure(failure_code=None, failure_reason="INSUFFICIENT FUNDS in account")
    assert r.primary_cause == "insufficient_funds_temporary"


def test_classifier_nsf_escalates_after_repeated_attempts() -> None:
    r = classify_failure(
        failure_code=None, failure_reason="insufficient funds", attempt_number=4
    )
    assert r.primary_cause == "insufficient_funds_persistent"


def test_classifier_unknown_is_honest() -> None:
    r = classify_failure(failure_code="WEIRD_99", failure_reason="mystery decline")
    assert r.primary_cause == "unknown"
    assert r.confidence <= 0.5


# ---------- detection ----------

def test_detection_creates_case_and_audit(db) -> None:
    payment, _ = ingest_payment_event(db, _failed_event())
    result = detect_from_failed_payment(db, payment)
    db.commit()
    assert result.created is True
    assert result.case.state == "at_risk"
    audits = db.execute(select(AuditEvent).where(AuditEvent.case_id == result.case.id)).scalars().all()
    assert len(audits) == 1 and audits[0].event_type == "risk_detected"


def test_detection_idempotent(db) -> None:
    payment, _ = ingest_payment_event(db, _failed_event())
    first = detect_from_failed_payment(db, payment)
    second = detect_from_failed_payment(db, payment)
    db.commit()
    assert first.created is True and second.created is False
    assert first.case.id == second.case.id
    cases = db.execute(select(RecoveryCase)).scalars().all()
    risks = db.execute(select(RevenueRiskEvent)).scalars().all()
    assert len(cases) == 1 and len(risks) == 1


def test_detection_rejects_non_failed_payment(db) -> None:
    payment, _ = ingest_payment_event(
        db, _failed_event(status="captured", failure_code=None, failure_reason=None)
    )
    with pytest.raises(ValueError):
        detect_from_failed_payment(db, payment)


# ---------- service + state machine ----------

def test_analyze_full_flow(db) -> None:
    payment, _ = ingest_payment_event(db, _failed_event())
    result = analyze_failed_payment(db, payment)
    assert result.cause == "insufficient_funds_temporary"
    assert result.detection.case.state == "analyzed"
    classification = db.execute(select(FailureClassification)).scalars().one()
    assert classification.model_version == "failure_rules_v1"
    # Re-analysis must not duplicate anything
    again = analyze_failed_payment(db, payment)
    assert again.detection.created is False
    assert again.cause == result.cause
    assert len(db.execute(select(FailureClassification)).scalars().all()) == 1


def test_state_machine_legal_and_illegal(db) -> None:
    payment, _ = ingest_payment_event(db, _failed_event())
    result = detect_from_failed_payment(db, payment)
    case = result.case
    for target in ["analyzed", "action_selected", "executed", "observing", "recovered"]:
        transition(case, target)
    with pytest.raises(InvalidTransition):
        transition(case, "executed")  # terminal: no further transitions except closed
    transition(case, "closed")
    assert case.closed_at is not None


def test_state_machine_no_skip(db) -> None:
    payment, _ = ingest_payment_event(db, _failed_event())
    case = detect_from_failed_payment(db, payment).case
    with pytest.raises(InvalidTransition):
        transition(case, "recovered")  # cannot jump at_risk → recovered
