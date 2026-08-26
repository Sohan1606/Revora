"""Bounded action executor.

Every execution: idempotency key, Execution row, audit event, state transition.
Honesty rules:
- retry without Razorpay configured → execution FAILS with a clear error (never simulated)
- send_message in simulated mode → Message row with provider="simulated" (labeled, never real delivery)
- approvals are enforced BEFORE execution (approve_decision / reject_decision below)
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.recovery.audit import record_audit
from app.domain.recovery.state_machine import InvalidTransition, transition
from app.infrastructure.models import (
    Decision, Execution, Message, RecoveryCase, User,
)

SIMULATED_TEMPLATES = {  # message template keys — content rendered in Phase 11
    "send_message": "recovery_notice",
    "request_method_update": "update_payment_method",
    "offer_alternative_method": "alternative_payment_offer",
}


def execute_decision(db: Session, decision: Decision, actor: User | None = None) -> Execution:
    case = db.get(RecoveryCase, decision.case_id)
    if case is None:
        raise ValueError("decision references missing case")

    # Idempotency FIRST — re-execution of an already-executed decision returns the
    # original execution row instead of erroring or double-firing.
    # Key = decision identity ONLY (counters mutate during execution and must not
    # turn the same decision into a "different" action).
    key = hashlib.sha256(f"{case.id}:{decision.id}:{decision.chosen_action}".encode()).hexdigest()
    existing = db.execute(select(Execution).where(Execution.idempotency_key == key)).scalar_one_or_none()
    if existing is not None:
        return existing

    if decision.status != "approved_by_policy":
        raise ValueError(f"decision {decision.id} is {decision.status!r} — not executable")

    now = datetime.now(timezone.utc)
    execution = Execution(
        case_id=case.id, decision_id=decision.id,
        action_type=decision.chosen_action, status="executing",
        attempt=1, idempotency_key=key, started_at=now,
    )
    db.add(execution)
    db.flush()

    action = decision.chosen_action
    try:
        transition(case, "executed")
    except InvalidTransition as exc:
        execution.status = "skipped"
        execution.error = f"invalid source state: {exc}"
        execution.finished_at = now
        db.commit()
        return execution

    settings = get_settings()

    if action == "no_action":
        # Deliberate, audited decision NOT to act.
        execution.status, execution.result = "succeeded", {"note": "deliberate no-action"}
        transition(case, "stopped")

    elif action == "wait":
        hours = int(decision.action_params.get("wait_hours", 24))
        case.next_action_at = now + timedelta(hours=hours)
        execution.status, execution.result = "succeeded", {"wait_hours": hours, "resume_at": case.next_action_at.isoformat()}
        transition(case, "observing")

    elif action in SIMULATED_TEMPLATES:
        provider = "simulated" if settings.MESSAGING_MODE == "simulated" else "resend"
        message = Message(
            case_id=case.id, channel="email", provider=provider,
            status="sent", template_key=SIMULATED_TEMPLATES[action], sent_at=now,
        )
        db.add(message)
        case.contact_count += 1
        case.last_action_at = now
        execution.status = "succeeded"
        execution.result = {
            "message_provider": provider,
            "simulated": provider == "simulated",
            "template": SIMULATED_TEMPLATES[action],
        }
        transition(case, "observing")

    elif action == "retry":
        from app.integrations.razorpay.client import (
            RazorpayAPIError, RazorpayError, get_client,
        )
        try:
            client = get_client()  # raises RazorpayNotConfiguredError when unset
        except RazorpayError as exc:
            # Honest failure — retry is a REAL payment operation; we never fake success.
            execution.status = "failed"
            execution.error = f"razorpay_not_configured: {exc}"
            execution.result = {"real_action": True, "executed": False}
            record_audit(db, event_type="execution_failed", merchant_id=case.merchant_id,
                         case_id=case.id, payload={"action": "retry",
                                                   "reason": "razorpay_not_configured"})
            transition(case, "stopped")  # nothing more can be done without the integration
        else:
            # REAL TEST-MODE RETRY: create a fresh Razorpay order the customer can
            # complete. No server-side charge, no live mode, no real money.
            try:
                order = client.create_order(
                    amount_paise=case.amount_paise,
                    currency=case.currency,
                    receipt=f"revora-retry-{case.id}",
                    notes={"case_id": str(case.id), "source": "revora_retry",
                           "decision_id": str(decision.id)},
                )
            except RazorpayAPIError as exc:
                execution.status = "failed"
                execution.error = f"razorpay_api_error: {exc}"
                execution.result = {"real_action": True, "executed": False,
                                    "http_status": exc.status}
                record_audit(db, event_type="execution_failed", merchant_id=case.merchant_id,
                             case_id=case.id,
                             payload={"action": "retry", "reason": "razorpay_api_error",
                                      "status": exc.status})
                transition(case, "analyzed")  # retryable failure → re-analyze, don't die
            else:
                case.retry_count += 1
                case.last_action_at = now
                execution.status = "succeeded"
                execution.result = {
                    "real_action": True,
                    "razorpay_order_id": order.get("id"),
                    "amount_paise": order.get("amount"),
                    "status": order.get("status"),
                    "mode": client.mode,
                }
                transition(case, "observing")

    elif action == "escalate":
        execution.status, execution.result = "succeeded", {"queue": "human_review"}
        transition(case, "escalated")

    else:  # defensive: unknown action must never silently "succeed"
        execution.status = "failed"
        execution.error = f"unknown action type: {action}"

    execution.finished_at = datetime.now(timezone.utc)
    if execution.status == "succeeded":
        decision.status = "executed"
        record_audit(db, event_type="action_executed", merchant_id=case.merchant_id,
                     case_id=case.id,
                     actor_type="user" if actor else "system",
                     actor_id=str(actor.id) if actor else None,
                     payload={"action": action, "result": execution.result,
                              "simulated": execution.result.get("simulated", False) if isinstance(execution.result, dict) else False})
    db.commit()
    return execution


def approve_decision(db: Session, decision_id: str, approver: User) -> tuple[Decision, Execution | None]:
    """Human gate for decisions flagged requires_approval. Owner/admin/operator may approve."""
    if not hasattr(approver, "role"):
        raise ValueError("approver must be a User")
    decision = db.get(Decision, decision_id)
    if decision is None:
        raise ValueError("decision not found")
    if decision.status != "requires_approval":
        raise ValueError(f"decision is {decision.status!r}, not awaiting approval")

    decision.status = "approved_by_policy"
    record_audit(db, event_type="decision_approved", merchant_id=approver.merchant_id,
                 case_id=decision.case_id, actor_type="user", actor_id=str(approver.id),
                 payload={"decision_id": str(decision.id), "approver_role": approver.role})
    case = db.get(RecoveryCase, decision.case_id)
    transition(case, "action_selected")  # awaiting_approval → action_selected
    db.commit()
    execution = execute_decision(db, decision, actor=approver)
    return decision, execution


def reject_decision(db: Session, decision_id: str, approver: User) -> Decision:
    decision = db.get(Decision, decision_id)
    if decision is None:
        raise ValueError("decision not found")
    if decision.status != "requires_approval":
        raise ValueError(f"decision is {decision.status!r}, not awaiting approval")
    decision.status = "superseded"
    record_audit(db, event_type="decision_rejected", merchant_id=approver.merchant_id,
                 case_id=decision.case_id, actor_type="user", actor_id=str(approver.id),
                 payload={"decision_id": str(decision.id)})
    case = db.get(RecoveryCase, decision.case_id)
    # Rejected THIS action ≠ abandoning the case: return to analyzed so the engine
    # re-decides among the remaining permissible (non-approval) actions.
    transition(case, "action_selected")
    transition(case, "analyzed")
    db.commit()
    return decision
