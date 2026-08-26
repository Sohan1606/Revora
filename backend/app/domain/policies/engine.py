"""Deterministic policy engine.

Hard rules ALWAYS override model recommendations (architecture invariant #2).
The active PolicyVersion's `definition` JSON is the source of truth; every
evaluation is auditable and reproducible from (definition, inputs).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.infrastructure.models import PolicyVersion, RecoveryCase

DEFAULT_POLICY_VERSION = "default-v1"

# All costs/frictions are policy PARAMETERS (merchant-tunable), not fabricated results.
DEFAULT_POLICY_DEF: dict[str, Any] = {
    "max_retries": 3,
    "max_contacts": 3,
    "min_actionable_amount_paise": 10000,  # ₹100 — below this, action EV can't justify cost
    "intervention_costs_paise": {
        "wait": 0, "retry": 0, "send_message": 25,
        "request_method_update": 100, "offer_alternative_method": 100,
        "escalate": 500, "no_action": 0,
    },
    "friction_scores": {  # fraction of amount at risk treated as customer-experience cost
        "wait": 0.0, "retry": 0.01, "send_message": 0.02,
        "request_method_update": 0.05, "offer_alternative_method": 0.10,
        "escalate": 0.0, "no_action": 0.0,
    },
    "require_approval_above_paise": {"offer_alternative_method": 1000000},  # ₹10,000
    "blocked_actions": [],
    "cause_blocks": {
        "hard_decline": ["retry"],
        "method_expired": ["retry"],      # retrying an expired instrument cannot succeed
        "customer_intent": ["retry"],     # customer deliberately stopped — do not force
    },
}


@dataclass(frozen=True)
class PolicyEvaluation:
    allowed: bool
    requires_approval: bool = False
    rule: str = "allowed"
    reason: str = ""


def ensure_default_policy(db: Session, merchant_id: str) -> PolicyVersion:
    """Idempotently create/return the active default policy version for a merchant."""
    existing = db.execute(
        select(PolicyVersion).where(
            PolicyVersion.merchant_id == merchant_id,
            PolicyVersion.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    policy = PolicyVersion(
        merchant_id=merchant_id,
        version=DEFAULT_POLICY_VERSION,
        definition=DEFAULT_POLICY_DEF,
        is_active=True,
    )
    db.add(policy)
    db.flush()
    return policy


def evaluate_action(
    definition: dict[str, Any],
    *,
    action: str,
    cause: str,
    case: RecoveryCase,
    amount_paise: int,
    razorpay_available: bool | None = None,
) -> PolicyEvaluation:
    """Evaluate ONE candidate action against hard constraints. Pure function of inputs."""
    if razorpay_available is None:
        razorpay_available = get_settings().is_razorpay_configured

    if action == "no_action":
        return PolicyEvaluation(True, rule="no_action_always_allowed")

    if action in set(definition.get("blocked_actions", [])):
        return PolicyEvaluation(False, rule="blocked_actions", reason=f"{action} globally blocked by merchant")

    if action in set(definition.get("cause_blocks", {}).get(cause, [])):
        return PolicyEvaluation(False, rule="cause_blocks", reason=f"{action} blocked for cause={cause}")

    if action == "retry":
        if case.retry_count >= int(definition.get("max_retries", 3)):
            return PolicyEvaluation(False, rule="max_retries", reason=f"retry_count {case.retry_count} >= max_retries")
        if not razorpay_available:
            return PolicyEvaluation(False, rule="integration_not_available", reason="retry requires Razorpay configuration")

    if action == "send_message" and case.contact_count >= int(definition.get("max_contacts", 3)):
        return PolicyEvaluation(False, rule="max_contacts", reason=f"contact_count {case.contact_count} >= max_contacts")

    if amount_paise < int(definition.get("min_actionable_amount_paise", 0)):
        return PolicyEvaluation(False, rule="min_actionable_amount", reason="amount below actionable threshold")

    threshold = int(definition.get("require_approval_above_paise", {}).get(action, 0) or 0)
    if threshold and amount_paise > threshold:
        return PolicyEvaluation(
            True, requires_approval=True, rule="require_approval_above",
            reason=f"{action} above ₹{threshold/100:g} requires human approval",
        )

    return PolicyEvaluation(True, rule="allowed")
