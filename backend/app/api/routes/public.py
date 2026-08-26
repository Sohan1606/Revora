"""Public, non-sensitive endpoints for the landing page.

Returns ONLY aggregates computed from the labeled synthetic evaluation corpus.
No merchant data, no auth required, nothing that could leak tenant information.
The landing page must never display invented statistics — if there is no corpus,
it says so.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.domain.recovery.analytics import control_center
from app.infrastructure.database import get_db

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/evidence")
def public_evidence(db: Session = Depends(get_db)) -> dict:
    """Synthetic-corpus aggregates for the public landing page.

    Any merchant's corpus numbers are aggregated only from rows where
    is_synthetic=true, and every payload repeats the label.
    """
    from app.domain.recovery.analytics import action_performance
    from sqlalchemy import select, func
    from app.infrastructure.models import Merchant, RecoveryCase

    # aggregate synthetic metrics across all merchants (labeled corpus only)
    from app.domain.recovery.analytics import OPEN_STATES
    from app.infrastructure.models import Outcome

    at_risk = db.execute(
        select(func.coalesce(func.sum(RecoveryCase.amount_paise), 0))
        .where(RecoveryCase.is_synthetic.is_(True),
               RecoveryCase.state.in_(OPEN_STATES))
    ).scalar_one()
    recovered = db.execute(
        select(func.coalesce(func.sum(Outcome.amount_recovered_paise), 0))
        .join(RecoveryCase, RecoveryCase.id == Outcome.case_id)
        .where(RecoveryCase.is_synthetic.is_(True))
    ).scalar_one()
    cases_total = db.execute(
        select(func.count(RecoveryCase.id)).where(RecoveryCase.is_synthetic.is_(True))
    ).scalar_one()
    merchants_with_corpus = db.execute(
        select(func.count(func.distinct(RecoveryCase.merchant_id)))
        .where(RecoveryCase.is_synthetic.is_(True))
    ).scalar_one()

    has_data = cases_total > 0
    return {
        "data_label": "synthetic evaluation corpus (labeled, not live merchant data)",
        "has_data": has_data,
        "metrics": {
            "cases_total": int(cases_total or 0),
            "revenue_at_risk_paise": int(at_risk or 0),
            "recovered_paise": int(recovered or 0),
        } if has_data else None,
        "note": ("Aggregates computed from the labeled synthetic evaluation corpus. "
                 "Live merchant metrics require authentication." ),
    }
