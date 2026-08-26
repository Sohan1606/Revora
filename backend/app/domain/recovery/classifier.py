"""Deterministic failure root-cause classifier (rules v1).

Phase 8 adds an ML layer on top; the rules below remain the floor:
hard rules can never be overridden by a model (architecture invariant #2).

Mapping is keyword-based over (failure_code, failure_reason, method, attempts)
so it works with any provider's codes. Unmatched failures classify as `unknown`
with LOW confidence — never a guess.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.infrastructure.models import FailureClassification, Payment

MODEL_VERSION = "failure_rules_v1"

# Ordered rules — first match wins. (rule_id, cause, keywords, confidence)
_RULES: list[tuple[str, str, tuple[str, ...], float]] = [
    ("R_HARD_1", "hard_decline",
     ("do not honor", "blocked", "stolen", "fraudulent", "restricted", "permanently"),
     0.90),
    ("R_EXPIRED_1", "method_expired",
     ("expired card", "card expired", "card_has_expired", "instrument expired"),
     0.92),
    ("R_AUTH_1", "auth_required",
     ("authentication", "3ds", "3d-secure", "otp", "additional verification",
      "verification required"),
     0.88),
    ("R_NSF_1", "insufficient_funds_temporary",
     ("insufficient funds", "insufficient balance", "insufficient_credit", "nsf"),
     0.85),
    ("R_PROC_1", "processor_issue",
     ("gateway", "processor", "network", "timeout", "timed out",
      "issuer unavailable", "bank unavailable", "service unavailable"),
     0.80),
    ("R_INTENT_1", "customer_intent",
     ("cancelled by user", "cancelled by customer", "abandoned by user", "user dropped"),
     0.82),
]

# Exact failure_code hits (checked before keywords; highest confidence).
_CODE_MAP: dict[str, tuple[str, float]] = {
    "AUTHENTICATION_FAILED": ("auth_required", 0.96),
    "CARD_EXPIRED": ("method_expired", 0.97),
    "INSUFFICIENT_FUNDS": ("insufficient_funds_temporary", 0.94),
    "GATEWAY_ERROR": ("processor_issue", 0.90),
}

ATTEMPT_ESCALATION_THRESHOLD = 3  # repeated NSF ⇒ persistent


@dataclass
class ClassificationResult:
    primary_cause: str
    confidence: float
    rationale: dict[str, Any]


def classify_failure(
    *, failure_code: str | None, failure_reason: str | None,
    method: str | None = None, attempt_number: int = 1,
) -> ClassificationResult:
    code = (failure_code or "").strip().upper()
    reason = (failure_reason or "").strip().lower()
    haystack = " ".join(x for x in (code, reason) if x).lower()

    for known_code, (cause, conf) in _CODE_MAP.items():
        if code == known_code:
            return ClassificationResult(cause, conf, {"matched": f"code:{known_code}", "rule": "exact_code"})

    for rule_id, cause, keywords, conf in _RULES:
        hit = next((k for k in keywords if k in haystack), None)
        if hit:
            # Escalation: repeated insufficient-funds across attempts ⇒ persistent
            if cause == "insufficient_funds_temporary" and attempt_number >= ATTEMPT_ESCALATION_THRESHOLD:
                return ClassificationResult(
                    "insufficient_funds_persistent", min(conf + 0.03, 0.99),
                    {"matched": hit, "rule": rule_id, "escalated": "attempt_number >= 3"},
                )
            return ClassificationResult(cause, conf, {"matched": hit, "rule": rule_id})

    return ClassificationResult("unknown", 0.20, {"matched": None, "rule": "fallback"})


def classify_and_store(db: Session, *, risk_event_id: str) -> FailureClassification:
    """Classify the payment behind a risk event and persist the classification."""
    from app.infrastructure.models import Payment, RevenueRiskEvent

    risk = db.get(RevenueRiskEvent, risk_event_id)
    if risk is None:
        raise ValueError(f"risk event {risk_event_id} not found")

    payment = None
    if risk.source_type == "payment_failed":
        # source_id is stored as string; Payment.id is a UUID column.
        import uuid as _uuid

        try:
            payment = db.get(Payment, _uuid.UUID(risk.source_id))
        except ValueError:
            payment = None
    result = classify_failure(
        failure_code=payment.failure_code if payment else None,
        failure_reason=payment.failure_reason if payment else None,
        method=payment.method if payment else None,
        attempt_number=payment.attempt_number if payment else 1,
    )

    row = FailureClassification(
        risk_event_id=risk_event_id,
        primary_cause=result.primary_cause,
        confidence=result.confidence,
        model_version=MODEL_VERSION,
        rationale=result.rationale,
        classified_at=datetime.now(timezone.utc),
    )
    db.add(row)
    return row
