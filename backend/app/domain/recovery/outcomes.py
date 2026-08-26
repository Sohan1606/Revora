"""Outcome observation — links recovery claims to authoritative events only.

Sources: webhook (payment.captured), provider_api (poll), simulator (labeled).
Never a client-side claim (security rule).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.domain.recovery.audit import record_audit
from app.domain.recovery.state_machine import can_transition, transition
from app.infrastructure.models import Outcome, RecoveryCase

VALID_OUTCOMES = {"recovered", "not_recovered", "partial", "expired", "stopped"}
VALID_SOURCES = {"webhook", "provider_api", "simulator"}

_STATE_BY_OUTCOME = {
    "recovered": "recovered",
    "partial": "recovered",
    "not_recovered": "stopped",
    "expired": "stopped",
    "stopped": "stopped",
}


def observe_outcome(
    db: Session, case: RecoveryCase, *, outcome: str, source: str,
    amount_recovered_paise: int = 0, evidence_payment_id: str | None = None,
) -> Outcome:
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"invalid outcome {outcome!r}")
    if source not in VALID_SOURCES:
        raise ValueError(f"invalid source {source!r}")
    if outcome in ("recovered", "partial") and amount_recovered_paise <= 0:
        raise ValueError("recovered/partial outcomes require a positive amount")
    if outcome in ("not_recovered", "expired", "stopped") and amount_recovered_paise != 0:
        raise ValueError("non-recovery outcomes must carry zero amount")

    existing = db.query(Outcome).filter(Outcome.case_id == case.id).one_or_none()
    if existing is not None:
        return existing  # outcomes are terminal — first authoritative event wins

    if case.state not in ("observing", "executed", "stopped", "escalated",
                          "analyzed", "action_selected", "awaiting_approval"):
        raise ValueError(f"case in state {case.state!r} cannot receive an outcome")

    row = Outcome(
        case_id=case.id,
        outcome=outcome,
        amount_recovered_paise=amount_recovered_paise,
        observed_at=datetime.now(timezone.utc),
        source=source,
        evidence_payment_id=evidence_payment_id,
    )
    db.add(row)

    target = _STATE_BY_OUTCOME[outcome]
    if can_transition(case.state, target):
        transition(case, target)
    else:
        # e.g. an organic outcome arriving after a deliberate stop: the OUTCOME row is the
        # source of truth for metrics; case state closes out legally.
        if can_transition(case.state, "closed"):
            transition(case, "closed")
    record_audit(db, event_type="outcome_observed", merchant_id=case.merchant_id,
                 case_id=case.id, payload={"outcome": outcome, "source": source,
                                           "amount_recovered_paise": amount_recovered_paise})
    db.commit()
    return row
