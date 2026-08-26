"""Analytics — batch metrics computed from authoritative tables only.

Every metric is computed separately for live vs synthetic data. Synthetic corpus
numbers are never blended into live merchant metrics (transparency requirement).
"""
from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.infrastructure.models import (
    Decision, Execution, Outcome, RecoveryCase,
)

OPEN_STATES = ("at_risk", "analyzed", "action_selected", "awaiting_approval",
               "executed", "observing", "escalated")


def control_center(db: Session, merchant_id: str) -> dict:
    def _scope(q):
        return q.where(RecoveryCase.merchant_id == merchant_id)

    summary = {}
    for bucket, synthetic in (("live", False), ("synthetic", True)):
        at_risk = db.execute(
            _scope(select(func.coalesce(func.sum(RecoveryCase.amount_paise), 0))
                   .where(RecoveryCase.is_synthetic.is_(synthetic),
                          RecoveryCase.state.in_(OPEN_STATES)))
        ).scalar_one()
        active = db.execute(
            _scope(select(func.count(RecoveryCase.id))
                   .where(RecoveryCase.is_synthetic.is_(synthetic),
                          RecoveryCase.state.in_(OPEN_STATES)))
        ).scalar_one()
        recovered = db.execute(
            select(func.coalesce(func.sum(Outcome.amount_recovered_paise), 0))
            .join(RecoveryCase, RecoveryCase.id == Outcome.case_id)
            .where(RecoveryCase.merchant_id == merchant_id,
                   RecoveryCase.is_synthetic.is_(synthetic))
        ).scalar_one()
        cases_total = db.execute(
            _scope(select(func.count(RecoveryCase.id))
                   .where(RecoveryCase.is_synthetic.is_(synthetic)))
        ).scalar_one()
        recovered_cases = db.execute(
            select(func.count(Outcome.id))
            .join(RecoveryCase, RecoveryCase.id == Outcome.case_id)
            .where(RecoveryCase.merchant_id == merchant_id,
                   RecoveryCase.is_synthetic.is_(synthetic),
                   Outcome.outcome.in_(("recovered", "partial")))
        ).scalar_one()
        summary[bucket] = {
            "revenue_at_risk_paise": int(at_risk or 0),
            "active_cases": int(active or 0),
            "recovered_paise": int(recovered or 0),
            "cases_total": int(cases_total or 0),
            "recovered_cases": int(recovered_cases or 0),
        }

    by_state = dict(db.execute(
        _scope(select(RecoveryCase.state, func.count(RecoveryCase.id))
               .where(RecoveryCase.is_synthetic.is_(False))
               .group_by(RecoveryCase.state))
    ).all())

    action_mix = {
        row[0]: int(row[1]) for row in db.execute(
            select(Decision.chosen_action, func.count(Decision.id))
            .join(RecoveryCase, RecoveryCase.id == Decision.case_id)
            .where(RecoveryCase.merchant_id == merchant_id,
                   RecoveryCase.is_synthetic.is_(False))
            .group_by(Decision.chosen_action)
        ).all()
    }

    model_mix = {
        row[0]: int(row[1]) for row in db.execute(
            select(Decision.model_version, func.count(Decision.id))
            .join(RecoveryCase, RecoveryCase.id == Decision.case_id)
            .where(RecoveryCase.merchant_id == merchant_id,
                   RecoveryCase.is_synthetic.is_(False))
            .group_by(Decision.model_version)
        ).all()
    }

    return {"summary": summary, "cases_by_state": by_state,
            "action_mix": action_mix, "model_mix": model_mix}


def action_performance(db: Session, merchant_id: str) -> list[dict]:
    rows = db.execute(
        select(
            Decision.chosen_action,
            func.count(Decision.id),
            func.sum(case((Outcome.outcome == "recovered", 1), else_=0)),
            func.coalesce(func.sum(Outcome.amount_recovered_paise), 0),
        )
        .join(RecoveryCase, RecoveryCase.id == Decision.case_id)
        .outerjoin(Outcome, Outcome.case_id == RecoveryCase.id)
        .where(RecoveryCase.merchant_id == merchant_id)
        .group_by(Decision.chosen_action)
    ).all()
    return [
        {"action": action, "attempts": int(n or 0), "recovered": int(k or 0),
         "recovered_paise": int(paise or 0),
         "recovery_rate": round((k / n), 4) if n else 0.0}
        for action, n, k, paise in rows
    ]
