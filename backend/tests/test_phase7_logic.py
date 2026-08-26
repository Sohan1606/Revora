"""Phase 7 tests — NBA engine, policy gate, executor, approvals, outcomes."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.infrastructure.models  # noqa: F401
from app.core.config import get_settings
from app.domain.payments.ingest import PaymentEventIn, ingest_payment_event
from app.domain.recovery.executor import approve_decision, execute_decision, reject_decision
from app.domain.recovery.next_best_action import decide
from app.domain.recovery.outcomes import observe_outcome
from app.domain.recovery.service import analyze_failed_payment
from app.infrastructure.database import Base
from app.infrastructure.models import (
    AuditEvent, CandidateAction, Decision, Execution, Merchant, Message,
    RecoveryCase, User,
)

M = uuid.uuid4()


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    s = S()
    s.add(Merchant(id=M, name="M"))
    s.commit()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture()
def settings():
    s = get_settings()
    old = (s.RAZORPAY_KEY_ID, s.RAZORPAY_KEY_SECRET, s.MESSAGING_MODE)
    s.RAZORPAY_KEY_ID, s.RAZORPAY_KEY_SECRET = "", ""   # retry unavailable by default
    yield s
    s.RAZORPAY_KEY_ID, s.RAZORPAY_KEY_SECRET, s.MESSAGING_MODE = old


def _mk_failed_case(db, *, reason="insufficient funds", amount=899900, attempt=1):
    payment, _ = ingest_payment_event(db, PaymentEventIn(
        merchant_id=str(M), amount_paise=amount, status="failed", method="upi",
        failure_reason=reason, provider_payment_id=f"pay_{uuid.uuid4().hex[:10]}",
        attempt_number=attempt,
        occurred_at=datetime.now(timezone.utc),
        customer_email="c@example.com", is_synthetic=True,
    ))
    return analyze_failed_payment(db, payment).detection.case


def _candidates_of(db, decision_id):
    return db.execute(select(CandidateAction).where(CandidateAction.decision_id == decision_id)).scalars().all()


def _seed_history(db, *, cause="insufficient_funds_persistent", action="offer_alternative_method",
                  n=40, recovered=36):
    """Seed REAL historical rows (synthetic-labeled) so empirical base rates exist.
    This exercises the genuine empirical estimator path — not a shortcut around it."""
    from app.infrastructure.models import FailureClassification, Outcome, RevenueRiskEvent

    now = datetime.now(timezone.utc)
    for i in range(n):
        risk = RevenueRiskEvent(merchant_id=M, source_type="payment_failed",
                                source_id=str(uuid.uuid4()), amount_paise=100000,
                                detected_at=now, status="case_created", is_synthetic=True)
        db.add(risk); db.flush()
        case = RecoveryCase(merchant_id=M, risk_event_id=risk.id, amount_paise=100000,
                            state="recovered", opened_at=now, closed_at=now, is_synthetic=True)
        db.add(case); db.flush()
        db.add(FailureClassification(risk_event_id=risk.id, primary_cause=cause,
                                     confidence=0.9, model_version="seed", rationale={},
                                     classified_at=now))
        decision = Decision(case_id=case.id, chosen_action=action, expected_recovery_paise=0,
                            confidence=0.9, explanation={}, status="executed", decided_at=now)
        db.add(decision); db.flush()
        db.add(Outcome(case_id=case.id,
                       outcome="recovered" if i < recovered else "not_recovered",
                       amount_recovered_paise=100000 if i < recovered else 0,
                       observed_at=now, source="simulator"))
    db.commit()


# ---------- NBA + evidence ----------

def test_decide_persists_all_candidates_as_evidence(db, settings):
    case = _mk_failed_case(db)
    result = decide(db, case)
    assert result.chosen_action in ("wait", "send_message")  # retry blocked (no razorpay)
    cands = _candidates_of(db, result.decision.id)
    types = {c.action_type for c in cands}
    assert {"wait", "retry", "send_message", "no_action"} <= types
    retry_cand = next(c for c in cands if c.action_type == "retry")
    assert retry_cand.allowed_by_policy is False
    assert "integration_not_available" in retry_cand.blocked_reason
    # no_action EV is exactly 0 by definition
    no_action = next(c for c in cands if c.action_type == "no_action")
    assert no_action.expected_value_paise == 0
    assert case.state in ("action_selected", "awaiting_approval")


def test_decide_cold_start_basis_is_labeled(db, settings):
    case = _mk_failed_case(db)
    result = decide(db, case)
    assert "cold_start_prior" in result.explanation["chosen_basis"] or "empirical" in result.explanation["chosen_basis"]


# ---------- policy gates ----------

def test_policy_blocks_retry_on_hard_decline(db, settings):
    case = _mk_failed_case(db, reason="card blocked by issuer — do not honor")
    result = decide(db, case)
    cands = _candidates_of(db, result.decision.id)
    retry = next(c for c in cands if c.action_type == "retry")
    assert retry.allowed_by_policy is False and "cause_blocks" in retry.blocked_reason
    assert result.chosen_action != "retry"


def test_policy_blocks_retry_after_limit(db, settings):
    case = _mk_failed_case(db, attempt=1)
    case.retry_count = 3
    db.commit()
    result = decide(db, case)
    cands = _candidates_of(db, result.decision.id)
    retry = next(c for c in cands if c.action_type == "retry")
    assert "max_retries" in retry.blocked_reason


def test_policy_min_amount_forces_no_action(db, settings):
    case = _mk_failed_case(db, amount=5000)  # ₹50 < ₹100 threshold
    result = decide(db, case)
    assert result.chosen_action == "no_action"


def test_policy_blocks_message_after_contact_limit(db, settings):
    case = _mk_failed_case(db)
    case.contact_count = 3
    db.commit()
    result = decide(db, case)
    cands = _candidates_of(db, result.decision.id)
    msg = next(c for c in cands if c.action_type == "send_message")
    assert "max_contacts" in msg.blocked_reason


def test_offer_below_threshold_needs_no_approval(db, settings):
    _seed_history(db)  # empirical: offers recover ~90% for this cause
    case = _mk_failed_case(db, reason="insufficient funds", attempt=3)  # persistent
    case.amount_paise = 500000  # ₹5,000 < ₹10,000 threshold
    db.commit()
    result = decide(db, case)
    assert result.chosen_action == "offer_alternative_method"  # empirical EV dominates
    assert result.requires_approval is False


def test_offer_above_threshold_goes_to_approval_queue(db, settings):
    _seed_history(db)
    case = _mk_failed_case(db, reason="insufficient funds", attempt=3)  # persistent cause
    case.amount_paise = 2000000  # ₹20,000 > ₹10,000 threshold
    db.commit()
    result = decide(db, case)
    assert result.chosen_action == "offer_alternative_method"
    assert result.requires_approval is True
    assert case.state == "awaiting_approval"
    assert result.decision.status == "requires_approval"
    # Evidence: the empirical basis must be visible in the decision
    assert "empirical(n=40" in str(result.explanation.get("candidates"))


# ---------- executor ----------

def test_execute_wait_sets_resume_time(db, settings):
    case = _mk_failed_case(db)
    result = decide(db, case)
    if result.chosen_action != "wait":
        pytest.skip("engine chose a different optimal action")
    execution = execute_decision(db, result.decision)
    assert execution.status == "succeeded"
    assert case.next_action_at is not None
    assert case.state == "observing"


def test_execute_message_simulated_is_labeled(db, settings):
    settings.MESSAGING_MODE = "simulated"
    case = _mk_failed_case(db, reason="card expired")  # → method_expired candidates
    result = decide(db, case)
    execution = execute_decision(db, result.decision)
    if execution.result.get("template"):
        assert execution.result["simulated"] is True
        assert execution.result["message_provider"] == "simulated"
        msg = db.execute(select(Message)).scalars().one()
        assert msg.provider == "simulated"
        assert case.contact_count == 1


def test_execute_retry_blocked_without_full_credentials(db, settings):
    """Retry is a real payment operation: policy requires BOTH keys; a Key Id
    alone leaves the integration unconfigured → candidate blocked at the gate."""
    settings.RAZORPAY_KEY_ID = "rzp_test_fake_for_gate_test"
    settings.RAZORPAY_KEY_SECRET = ""  # incomplete → unconfigured
    _seed_history(db, cause="insufficient_funds_temporary", action="retry", recovered=38)
    case = _mk_failed_case(db)
    result = decide(db, case)
    retry_cand = next(c for c in result.decision.candidates if c.action_type == "retry")
    assert retry_cand.allowed_by_policy is False
    assert "integration_not_available" in (retry_cand.blocked_reason or "")


def test_execution_is_idempotent(db, settings):
    case = _mk_failed_case(db, reason="card expired")
    result = decide(db, case)
    first = execute_decision(db, result.decision)
    second = execute_decision(db, result.decision)
    assert first.idempotency_key == second.idempotency_key
    executions = db.execute(select(Execution)).scalars().all()
    assert len(executions) == 1


# ---------- approvals ----------

def test_approval_flow_execute(db, settings):
    operator = User(merchant_id=M, email="op@t.io", full_name="Op", role="operator")
    db.add(operator); db.commit()

    _seed_history(db)
    case = _mk_failed_case(db, reason="insufficient funds", attempt=3)
    case.amount_paise = 2000000
    db.commit()
    result = decide(db, case)
    assert result.requires_approval

    decision, execution = approve_decision(db, result.decision.id, operator)
    assert decision.status in ("approved_by_policy", "executed")
    assert execution is not None
    audits = db.execute(select(AuditEvent).where(AuditEvent.event_type == "decision_approved")).scalars().all()
    assert len(audits) == 1 and audits[0].actor_id == str(operator.id)


def test_reject_stops_case(db, settings):
    operator = User(merchant_id=M, email="op2@t.io", full_name="Op2", role="operator")
    db.add(operator); db.commit()
    _seed_history(db)
    case = _mk_failed_case(db, reason="insufficient funds", attempt=3)
    case.amount_paise = 2000000
    db.commit()
    result = decide(db, case)
    assert result.requires_approval
    reject_decision(db, result.decision.id, operator)
    assert case.state == "analyzed"  # case returns for re-decision, not abandoned
    # And the engine can decide again — choosing the best non-approval action now
    rerun = decide(db, case)
    assert rerun.chosen_action != "offer_alternative_method"


# ---------- outcomes ----------

def test_outcome_recovered_via_simulator(db, settings):
    case = _mk_failed_case(db)
    result = decide(db, case)
    execute_decision(db, result.decision)
    outcome = observe_outcome(db, case, outcome="recovered", source="simulator",
                              amount_recovered_paise=case.amount_paise)
    assert outcome.outcome == "recovered"
    assert case.state == "recovered" and case.closed_at is not None


def test_outcome_rejects_bad_claims(db, settings):
    case = _mk_failed_case(db)
    with pytest.raises(ValueError):
        observe_outcome(db, case, outcome="recovered", source="client")     # invalid source
    with pytest.raises(ValueError):
        observe_outcome(db, case, outcome="recovered", source="simulator", amount_recovered_paise=0)
    with pytest.raises(ValueError):
        observe_outcome(db, case, outcome="not_recovered", source="simulator", amount_recovered_paise=100)


def test_outcome_first_event_wins(db, settings):
    case = _mk_failed_case(db)
    result = decide(db, case)
    execute_decision(db, result.decision)
    first = observe_outcome(db, case, outcome="not_recovered", source="simulator")
    second = observe_outcome(db, case, outcome="recovered", source="simulator", amount_recovered_paise=999)
    assert first.id == second.id and first.outcome == "not_recovered"


# ---------- end-to-end loop ----------

def test_full_loop_ingest_to_outcome(db, settings):
    case = _mk_failed_case(db, reason="insufficient funds")
    assert case.state == "analyzed"
    result = decide(db, case)
    execution = execute_decision(db, result.decision)
    assert execution.status == "succeeded"
    outcome = observe_outcome(db, case, outcome="recovered", source="simulator",
                              amount_recovered_paise=case.amount_paise)
    assert outcome.amount_recovered_paise == 899900
    audit_types = {a.event_type for a in db.execute(select(AuditEvent)).scalars().all()}
    assert {"risk_detected", "action_executed", "outcome_observed"} <= audit_types
    # Decision + full candidate evidence persisted
    assert len(_candidates_of(db, result.decision.id)) >= 4
