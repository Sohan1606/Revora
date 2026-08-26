"""Synthetic evaluation corpus generator.

IMPORTANT — what is real vs simulated here (declared in docs/ROADMAP.md):
- Ingestion, failure classification, risk detection, case creation: REAL domain pipeline.
- Action assignment: randomized (uniform over permissible actions) — exploration-style
  coverage of the action space, which is exactly what the experimentation engine does.
- The WORLD'S RESPONSE (does the customer pay?) is simulated by a documented
  data-generating process (DGP below) and recorded with source="simulator",
  is_synthetic=True on every row. It is never presented as live merchant data.

The DGP embeds learnable structure (cause×action effectiveness, payday timing,
VIP responsiveness, retry fatigue) so held-out metrics measure whether the ML
pipeline recovers known ground truth — honest evaluation, not theater.
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.domain.payments.ingest import PaymentEventIn, ingest_payment_event
from app.domain.recovery.audit import record_audit
from app.domain.recovery.outcomes import observe_outcome
from app.domain.recovery.service import analyze_failed_payment
from app.domain.recovery.state_machine import transition
from app.infrastructure.models import (
    Customer, Decision, Execution, Message, Merchant, RecoveryCase,
)
from app.intelligence.recovery_score.features import log_amount_paise

CORPUS_MERCHANT_NAME = "SYNTHETIC CORPUS MERCHANT (evaluation data)"

# Ground-truth P(recovery) by (cause, action). The model must LEARN these from data.
DGP: dict[tuple[str, str], float] = {
    ("insufficient_funds_temporary", "retry"): 0.55,
    ("insufficient_funds_temporary", "wait"): 0.35,
    ("insufficient_funds_temporary", "send_message"): 0.30,
    ("insufficient_funds_temporary", "no_action"): 0.30,
    ("insufficient_funds_persistent", "request_method_update"): 0.55,
    ("insufficient_funds_persistent", "offer_alternative_method"): 0.50,
    ("insufficient_funds_persistent", "send_message"): 0.25,
    ("method_expired", "request_method_update"): 0.65,
    ("method_expired", "offer_alternative_method"): 0.45,
    ("method_expired", "send_message"): 0.25,
    ("method_expired", "retry"): 0.02,
    ("auth_required", "send_message"): 0.55,
    ("auth_required", "offer_alternative_method"): 0.40,
    ("hard_decline", "send_message"): 0.20,
    ("hard_decline", "request_method_update"): 0.35,
    ("hard_decline", "retry"): 0.01,
    ("processor_issue", "retry"): 0.70,
    ("processor_issue", "wait"): 0.50,
    ("customer_intent", "send_message"): 0.15,
    ("customer_intent", "retry"): 0.02,
    ("unknown", "send_message"): 0.20,
    ("unknown", "no_action"): 0.10,
}

# cause → (failure_code, failure_reason) fed through the REAL classifier.
CAUSE_SIGNATURES = {
    "insufficient_funds_temporary": (None, "Issuer declined: insufficient funds"),
    "insufficient_funds_persistent": (None, "Issuer declined: insufficient funds"),  # attempt≥3 escalates
    "auth_required": ("AUTHENTICATION_FAILED", None),
    "method_expired": ("CARD_EXPIRED", None),
    "hard_decline": (None, "Transaction blocked by issuer — do not honor"),
    "processor_issue": ("GATEWAY_ERROR", None),
    "customer_intent": (None, "payment cancelled by user"),
    "unknown": ("WEIRD_99", "mystery decline"),
}

# cause → share of generated cases
CAUSE_MIX = {
    "insufficient_funds_temporary": 0.30, "insufficient_funds_persistent": 0.08,
    "auth_required": 0.12, "method_expired": 0.15, "hard_decline": 0.10,
    "processor_issue": 0.15, "customer_intent": 0.07, "unknown": 0.03,
}

# Actions the generator may assign per cause (mirrors NBA candidate space).
CORPUS_ACTIONS = {
    "insufficient_funds_temporary": ["wait", "retry", "send_message", "no_action"],
    "insufficient_funds_persistent": ["request_method_update", "offer_alternative_method", "send_message"],
    "auth_required": ["send_message", "offer_alternative_method"],
    "method_expired": ["request_method_update", "offer_alternative_method", "send_message", "retry"],
    "hard_decline": ["send_message", "request_method_update", "retry"],
    "processor_issue": ["wait", "retry"],
    "customer_intent": ["send_message", "retry"],
    "unknown": ["send_message", "no_action"],
}

MESSAGE_TEMPLATES = {"send_message": "recovery_notice",
                     "request_method_update": "update_payment_method",
                     "offer_alternative_method": "alternative_payment_offer"}


def _effective_p(base: float, *, action: str, features_like: dict) -> float:
    """Apply DGP modifiers — the structure the model should discover."""
    p = base
    if action == "retry":
        if features_like["hour_of_day"] in (9, 10, 11) or features_like["day_of_month"] <= 4:
            p += 0.20  # payday-window effect
        if features_like["attempt_number"] >= 3:
            p -= 0.15  # retry fatigue
    if action in ("send_message", "request_method_update", "offer_alternative_method"):
        if features_like["is_vip"]:
            p += 0.10  # VIPs respond more
    if action == "offer_alternative_method" and features_like["log_amount"] > 4.7:  # >₹50k
        p -= 0.05
    return min(max(p, 0.01), 0.97)


def generate_corpus(db: Session, *, n_cases: int, seed: int, merchant: Merchant) -> dict:
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)

    causes = list(CAUSE_MIX)
    weights = list(CAUSE_MIX.values())

    customers: list[Customer] = []
    for _ in range(max(50, n_cases // 6)):
        c = Customer(merchant_id=merchant.id,
                     email=f"synth-{uuid.uuid4().hex[:10]}@corpus.revora.local",
                     name="Synthetic Customer", is_vip=rng.random() < 0.15)
        db.add(c)
        customers.append(c)
    db.flush()

    stats = {"cases": 0, "recovered": 0, "by_cause": {}}
    for _ in range(n_cases):
        cause = rng.choices(causes, weights=weights)[0]
        attempt = rng.randint(3, 6) if cause == "insufficient_funds_persistent" else rng.randint(1, 3)
        occurred = now - timedelta(days=rng.randint(0, 119), hours=rng.randint(0, 23))
        amount = rng.choice([49900, 99900, 199900, 499900, 899900, 1499900, 2999900, 4999900])
        method = rng.choices(["upi", "card", "netbanking"], weights=[0.55, 0.30, 0.15])[0]
        code, reason = CAUSE_SIGNATURES[cause]
        customer = rng.choice(customers)

        payment, _ = ingest_payment_event(db, PaymentEventIn(
            merchant_id=str(merchant.id), amount_paise=amount, status="failed",
            method=method, failure_code=code, failure_reason=reason,
            provider_payment_id=f"pay_SYN_{uuid.uuid4().hex[:12]}",
            attempt_number=attempt, occurred_at=occurred,
            customer_email=customer.email, is_synthetic=True,
        ))
        analysis = analyze_failed_payment(db, payment)
        case = analysis.detection.case

        features_like = {
            "hour_of_day": occurred.hour, "day_of_month": occurred.day,
            "attempt_number": attempt, "is_vip": bool(customer.is_vip),
            "log_amount": log_amount_paise(amount),
        }

        actions = [a for a in CORPUS_ACTIONS[cause]
                   if not (a == "retry" and attempt > 3)]
        action = rng.choice(actions)

        # Real state-machine walk + real rows; only the outcome is simulated (labeled).
        transition(case, "action_selected")
        decision = Decision(
            case_id=case.id, chosen_action=action, action_params={},
            expected_recovery_paise=0, confidence=0.0,
            explanation={"corpus": True, "assigned_randomly": True},
            model_version="corpus_dgp", status="executed",
            decided_at=datetime.now(timezone.utc),
        )
        db.add(decision)
        db.flush()
        transition(case, "executed")

        execution = Execution(
            case_id=case.id, decision_id=decision.id, action_type=action,
            status="succeeded", attempt=1,
            idempotency_key=f"corpus:{decision.id}",
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            result={"corpus": True, "simulated": True},
        )
        db.add(execution)
        if action in MESSAGE_TEMPLATES:
            db.add(Message(case_id=case.id, channel="email", provider="simulated",
                           status="sent", template_key=MESSAGE_TEMPLATES[action],
                           sent_at=datetime.now(timezone.utc)))
            case.contact_count += 1
        if action == "retry":
            case.retry_count += 1
        if action == "no_action":
            transition(case, "stopped")
        else:
            transition(case, "observing")

        p = _effective_p(DGP[(cause, action)], action=action, features_like=features_like)
        recovered = rng.random() < p
        observe_outcome(
            db, case,
            outcome="recovered" if recovered else "not_recovered",
            source="simulator",
            amount_recovered_paise=amount if recovered else 0,
        )
        record_audit(db, event_type="corpus_case_generated", merchant_id=merchant.id,
                     case_id=case.id, payload={"cause": cause, "action": action,
                                               "ground_truth_p": round(p, 4)})
        stats["cases"] += 1
        stats["recovered"] += int(recovered)
        stats["by_cause"][cause] = stats["by_cause"].get(cause, 0) + 1
        db.commit()

    return stats
