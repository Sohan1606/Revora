"""Recovery-case state machine.

States:
    at_risk → analyzed → action_selected → awaiting_approval → executed → observing
            → recovered | stopped | escalated | closed

Rules:
- Transitions not in ALLOWED are rejected (raise InvalidTransition).
- `executed → analyzed` exists for RETRYABLE execution failures (e.g. Razorpay
  transport error): the case returns to analysis instead of dying.
- `closed` is reachable only from terminal states (recovered/stopped/escalated).
- Terminal states accept no further transitions (except → closed).
"""
from __future__ import annotations

from app.infrastructure.models import RecoveryCase

ALLOWED: dict[str, set[str]] = {
    "at_risk": {"analyzed", "closed"},
    "analyzed": {"action_selected", "closed"},
    "action_selected": {"awaiting_approval", "executed", "analyzed", "closed"},
    "awaiting_approval": {"executed", "action_selected", "closed"},
    "executed": {"observing", "stopped", "escalated", "analyzed", "closed"},
    "observing": {"recovered", "stopped", "escalated", "analyzed", "action_selected", "closed"},
    "recovered": {"closed"},
    "stopped": {"closed"},
    "escalated": {"action_selected", "analyzed", "closed"},
    "closed": set(),
}

TERMINAL = {"recovered", "stopped", "closed"}


class InvalidTransition(Exception):
    def __init__(self, case_id: str, current: str, attempted: str):
        self.case_id, self.current, self.attempted = case_id, current, attempted
        super().__init__(
            f"case {case_id}: illegal transition {current!r} → {attempted!r}"
        )


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED.get(current, set())


def transition(db_case: RecoveryCase, target: str) -> RecoveryCase:
    """Validate + apply a state transition in place. Caller commits."""
    current = db_case.state
    if not can_transition(current, target):
        raise InvalidTransition(str(db_case.id), current, target)
    db_case.state = target
    if target in TERMINAL and target != "closed":
        from datetime import datetime, timezone

        db_case.closed_at = datetime.now(timezone.utc)
    return db_case
