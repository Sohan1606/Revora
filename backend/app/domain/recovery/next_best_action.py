"""Next-Best-Action engine.

For each recovery case: generate candidate actions for the diagnosed cause,
estimate P(recovery) per candidate (empirical stats / cold-start prior),
compute expected value, apply the deterministic policy gate, and persist
EVERY candidate evaluated (decision evidence — the audit trail requirement).

Expected value (paise) = P(recovery) × amount − intervention_cost − friction×amount

`no_action` and `wait` are always valid candidates (architecture invariant #6).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.domain.policies.engine import (
    DEFAULT_POLICY_DEF, PolicyEvaluation, ensure_default_policy, evaluate_action,
)
from app.domain.recovery.case_features import build_case_features
from app.domain.recovery.stats import recovery_stats_by_action
from app.domain.recovery.state_machine import transition
from app.infrastructure.models import (
    CandidateAction, Decision, FailureClassification, RecoveryCase,
)
from app.intelligence.recovery_score.base import MODEL_VERSION
from app.intelligence.recovery_score.ml_provider import estimate as estimate_recovery

# Deterministic cause → candidate actions mapping (no_action appended always).
# Deliberately includes actions the policy will BLOCK (e.g. retry under hard_decline):
# blocked candidates are persisted as evidence of bounded decision-making.
CAUSE_ACTIONS: dict[str, list[str]] = {
    "insufficient_funds_temporary": ["wait", "retry", "send_message"],
    "insufficient_funds_persistent": ["request_method_update", "offer_alternative_method", "send_message"],
    "auth_required": ["send_message", "offer_alternative_method"],
    "method_expired": ["request_method_update", "offer_alternative_method", "retry"],
    "hard_decline": ["send_message", "request_method_update", "retry"],
    "processor_issue": ["wait", "retry"],
    "customer_intent": ["send_message", "retry"],
    "unknown": ["send_message"],
}

WAIT_HOURS_BY_CAUSE = {
    "insufficient_funds_temporary": 36,   # retry window after typical salary/limit cycles
    "processor_issue": 6,                 # transient infra degradation window
}


@dataclass
class DecisionOutcome:
    decision: Decision
    chosen_action: str
    requires_approval: bool
    expected_recovery_paise: int
    explanation: dict


def decide(db: Session, case: RecoveryCase) -> DecisionOutcome:
    if case.state not in ("analyzed", "escalated"):
        raise ValueError(f"case {case.id}: cannot decide from state {case.state!r}")

    classification = db.query(FailureClassification).filter(
        FailureClassification.risk_event_id == case.risk_event_id
    ).one()
    cause = classification.primary_cause

    policy_version = ensure_default_policy(db, case.merchant_id)
    definition = policy_version.definition or DEFAULT_POLICY_DEF
    costs: dict = definition["intervention_costs_paise"]
    frictions: dict = definition["friction_scores"]

    stats = recovery_stats_by_action(db, cause)
    case_features = build_case_features(db, case)

    # Human override is respected: actions whose decision a human REJECTED for this
    # case are excluded from re-proposal (they remain visible in the candidate log).
    rejected_actions = {
        d.chosen_action
        for d in db.query(Decision).filter(
            Decision.case_id == case.id, Decision.status == "superseded"
        ).all()
    }

    candidate_actions = [
        a for a in CAUSE_ACTIONS.get(cause, ["send_message"]) if a not in rejected_actions
    ]

    candidates: list[tuple[str, float, str, float, int, int, float, PolicyEvaluation]] = []
    for action in [*candidate_actions, "no_action"]:
        est = estimate_recovery(cause, action, stats, case_features)
        cost = int(costs.get(action, 0))
        friction = float(frictions.get(action, 0.0))
        ev = int(est.p_recovery * case.amount_paise - cost - friction * case.amount_paise)
        # no_action realizes nothing by definition — organic recovery is NOT our doing.
        if action == "no_action":
            ev = 0
        pol = evaluate_action(definition, action=action, cause=cause, case=case,
                              amount_paise=case.amount_paise)
        candidates.append((action, est.p_recovery, est.basis, est.confidence, ev,
                           cost, friction, pol))

    allowed = [c for c in candidates if c[7].allowed and not c[7].requires_approval]
    approval = [c for c in candidates if c[7].allowed and c[7].requires_approval]
    # Consider ALL permissible candidates together (approval-gated included):
    # the economically best action must win even if it needs a human gate.
    # Order matters for ties: plain-allowed candidates come first, so equal EV
    # resolves toward the action that needs no approval.
    pool = allowed + approval or [c for c in candidates if c[0] == "no_action"]
    best = max(pool, key=lambda c: c[4])

    now = datetime.now(timezone.utc)
    decision = Decision(
        case_id=case.id,
        policy_version_id=policy_version.id,
        chosen_action=best[0],
        action_params=({"wait_hours": WAIT_HOURS_BY_CAUSE.get(cause, 24)}
                       if best[0] == "wait" else {}),
        expected_recovery_paise=best[4],
        confidence=best[3],
        explanation={
            "cause": cause,
            "ev_formula": "p*amount - cost - friction*amount",
            "chosen_basis": best[2],
            "policy_version": policy_version.version,
            "excluded_rejected_actions": sorted(rejected_actions),
        },
        model_version=(best[2] if best[2].startswith("model:") else MODEL_VERSION),
        status=("requires_approval" if best[7].requires_approval else "approved_by_policy"),
        decided_at=now,
    )
    db.add(decision)
    db.flush()

    for action, p, basis, conf, ev, cost, friction, pol in candidates:
        db.add(CandidateAction(
            decision_id=decision.id,
            action_type=action,
            params={"wait_hours": WAIT_HOURS_BY_CAUSE.get(cause, 24)} if action == "wait" else {},
            p_recovery=p,
            expected_value_paise=ev,
            intervention_cost_paise=cost,
            friction_score=friction,
            allowed_by_policy=pol.allowed,
            blocked_reason=None if pol.allowed else f"{pol.rule}: {pol.reason}",
        ))

    transition(case, "action_selected")
    if decision.status == "requires_approval":
        transition(case, "awaiting_approval")
    db.commit()

    return DecisionOutcome(
        decision=decision,
        chosen_action=decision.chosen_action,
        requires_approval=decision.status == "requires_approval",
        expected_recovery_paise=decision.expected_recovery_paise,
        explanation={
            **decision.explanation,
            "candidates": [
                {"action": a, "ev_paise": ev, "p": p, "basis": basis,
                 "policy": ("allowed" if pol.allowed else pol.rule)}
                for a, p, basis, conf, ev, cost, friction, pol in candidates
            ],
        },
    )
