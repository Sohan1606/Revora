"""Domain statistics — empirical recovery base rates computed from REAL stored outcomes."""
from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.infrastructure.models import (
    Decision, ExperimentAssignment, FailureClassification, Outcome, RecoveryCase,
)
from app.intelligence.recovery_score.base import ActionStat


def recovery_stats_by_action(db: Session, cause: str) -> dict[str, ActionStat]:
    """Per-action (attempts, recovered) for a failure cause, from actual decisions+outcomes.

    Excludes cases enrolled in an experiment CONTROL arm (they intentionally receive
    baseline treatment — including them would bias treatment estimates).
    """
    control_case_ids = select(ExperimentAssignment.case_id).where(
        ExperimentAssignment.arm == "control"
    ).scalar_subquery()

    rows = db.execute(
        select(
            Decision.chosen_action,
            func.count(Outcome.id),
            func.sum(case((Outcome.outcome == "recovered", 1), else_=0)),
        )
        .join(RecoveryCase, RecoveryCase.id == Decision.case_id)
        .join(Outcome, Outcome.case_id == RecoveryCase.id)
        .join(
            FailureClassification,
            FailureClassification.risk_event_id == RecoveryCase.risk_event_id,
        )
        .where(
            FailureClassification.primary_cause == cause,
            RecoveryCase.id.not_in(control_case_ids),
        )
        .group_by(Decision.chosen_action)
    ).all()

    return {
        action: ActionStat(attempts=int(n or 0), recovered=int(k or 0))
        for action, n, k in rows
    }
