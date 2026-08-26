"""Recovery orchestration service — the Detect → Diagnose spine.

Phase 7 adds the next-best-action engine and policy gate on top of this;
Phase 8 replaces the probability provider with a trained model.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.domain.recovery.classifier import classify_and_store
from app.domain.recovery.detector import DetectionResult, detect_from_failed_payment
from app.domain.recovery.state_machine import transition
from app.infrastructure.models import Payment, RecoveryCase


@dataclass
class AnalyzeResult:
    detection: DetectionResult
    cause: str
    confidence: float


def analyze_failed_payment(db: Session, payment: Payment) -> AnalyzeResult:
    """Detect → create case → classify root cause → state at_risk→analyzed."""
    detection = detect_from_failed_payment(db, payment)

    if detection.case.state == "at_risk":
        classification = classify_and_store(db, risk_event_id=detection.risk_event.id)
        transition(detection.case, "analyzed")
        db.commit()
        return AnalyzeResult(detection, classification.primary_cause, float(classification.confidence))

    # Already analyzed — return existing classification without duplicating work.
    from sqlalchemy import select

    from app.infrastructure.models import FailureClassification

    existing = db.execute(
        select(FailureClassification).where(
            FailureClassification.risk_event_id == detection.risk_event.id
        )
    ).scalar_one()
    return AnalyzeResult(detection, existing.primary_cause, float(existing.confidence))
