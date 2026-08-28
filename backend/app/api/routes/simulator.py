"""Scenario simulator — run labeled synthetic scenarios end-to-end (demo lab).

Everything created here is is_synthetic=true and every simulated world response
is source="simulator". This is the declared simulation surface — nothing here
touches live merchant metrics.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_minimum
from app.domain.experiments.engine import route_decision
from app.domain.payments.ingest import PaymentEventIn, ingest_payment_event
from app.domain.recovery.executor import execute_decision
from app.domain.recovery.outcomes import observe_outcome
from app.domain.recovery.service import analyze_failed_payment
from app.infrastructure.database import get_db
from app.infrastructure.models import User

router = APIRouter(prefix="/simulator", tags=["simulator"])

SCENARIOS = {
    "insufficient_funds": {"failure_reason": "Issuer declined: insufficient funds",
                           "failure_code": None, "method": "upi"},
    "expired_card": {"failure_reason": None, "failure_code": "CARD_EXPIRED",
                     "method": "card"},
    "hard_decline": {"failure_reason": "Transaction blocked by issuer — do not honor",
                     "failure_code": None, "method": "card"},
    "processor_issue": {"failure_reason": None, "failure_code": "GATEWAY_ERROR",
                        "method": "upi"},
    "auth_required": {"failure_reason": None, "failure_code": "AUTHENTICATION_FAILED",
                      "method": "card"},
    "customer_cancelled": {"failure_reason": "payment cancelled by user",
                           "failure_code": None, "method": "upi"},
}


class ScenarioRun(BaseModel):
    amount_paise: int = Field(default=899900, gt=0, le=100000000)
    customer_email: str = "demo.customer@simulator.revora.local"
    outcome: str = Field(default="random", pattern="^(random|recovered|not_recovered)$")


@router.post("/scenarios/{scenario}/run", status_code=status.HTTP_201_CREATED)
def run_scenario(
    scenario: str,
    body: ScenarioRun,
    user: User = Depends(require_minimum("operator")),
    db: Session = Depends(get_db),
) -> dict:
    if scenario not in SCENARIOS:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"unknown scenario; available: {sorted(SCENARIOS)}")
    if user.merchant_id is None:
        raise HTTPException(400, "user has no merchant")

    cfg = SCENARIOS[scenario]
    payment, _ = ingest_payment_event(db, PaymentEventIn(
        merchant_id=str(user.merchant_id), amount_paise=body.amount_paise,
        status="failed", method=cfg["method"],
        failure_code=cfg["failure_code"], failure_reason=cfg["failure_reason"],
        provider_payment_id=f"pay_SIM_{random.randint(10**9, 10**10)}",
        occurred_at=datetime.now(timezone.utc),
        customer_email=body.customer_email, is_synthetic=True,
    ))
    analysis = analyze_failed_payment(db, payment)
    case = analysis.detection.case

    decision, arm = route_decision(db, case)
    execution = None
    if decision.status == "approved_by_policy":
        execution = execute_decision(db, decision)

    from app.api.routes.recovery import candidates_payload
    decision_explanation = {**decision.explanation,
                            "candidates": candidates_payload(db, decision.id)}

    rng = random.Random(f"{case.id}")
    recovered = (body.outcome == "recovered" or
                 (body.outcome == "random" and rng.random() < 0.6))
    outcome = None
    if execution is not None and execution.status == "succeeded":
        outcome = observe_outcome(db, case,
                                  outcome="recovered" if recovered else "not_recovered",
                                  source="simulator",
                                  amount_recovered_paise=case.amount_paise if recovered else 0)

    return {
        "scenario": scenario,
        "simulation": True,  # explicit label — this is the demo lab
        "case_id": str(case.id),
        "cause": analysis.cause,
        "case_state": case.state,
        "decision": {"id": str(decision.id), "action": decision.chosen_action,
                     "status": decision.status,
                     "expected_recovery_paise": decision.expected_recovery_paise,
                     "model_version": decision.model_version,
                     "explanation": decision_explanation},
        "execution": ({"status": execution.status, "result": execution.result,
                       "error": execution.error} if execution else None),
        "outcome": ({"outcome": outcome.outcome,
                     "amount_recovered_paise": outcome.amount_recovered_paise,
                     "source": outcome.source} if outcome else None),
        "experiment_arm": arm,
    }
