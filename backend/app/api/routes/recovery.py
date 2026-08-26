"""Recovery case routes — list, detail, decide, execute, approve/reject."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_minimum
from app.domain.experiments.engine import route_decision
from app.domain.recovery.audit import record_audit
from app.domain.recovery.executor import approve_decision, execute_decision, reject_decision
from app.domain.recovery.next_best_action import decide
from app.infrastructure.database import get_db
from app.infrastructure.models import (
    AuditEvent, CandidateAction, Decision, Execution, FailureClassification,
    Message, Outcome, RecoveryCase, User,
)

router = APIRouter(prefix="/recovery", tags=["recovery"])

TERMINAL_STATES = {"recovered", "stopped", "closed"}


def _get_case(db: Session, case_id: str, user: User) -> RecoveryCase:
    try:
        import uuid as _uuid
        case = db.get(RecoveryCase, _uuid.UUID(case_id))
    except ValueError:
        case = None
    if case is None or str(case.merchant_id) != str(user.merchant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "case not found")
    return case


@router.get("/cases")
def list_cases(
    state: str | None = None,
    is_synthetic: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    q = select(RecoveryCase).where(RecoveryCase.merchant_id == user.merchant_id)
    if state is not None:
        q = q.where(RecoveryCase.state == state)
    if is_synthetic is not None:
        q = q.where(RecoveryCase.is_synthetic.is_(is_synthetic))
    total = db.execute(
        select(RecoveryCase.id).where(q.whereclause)
    ).scalars().all()  # cheap count for page math at hackathon scale
    rows = db.execute(
        q.order_by(RecoveryCase.opened_at.desc()).offset(offset).limit(limit)
    ).scalars().all()
    return {
        "total": len(total),
        "cases": [
            {"id": str(c.id), "state": c.state, "amount_paise": c.amount_paise,
             "is_synthetic": c.is_synthetic, "opened_at": c.opened_at.isoformat(),
             "customer_id": str(c.customer_id) if c.customer_id else None}
            for c in rows
        ],
    }


@router.get("/cases/{case_id}")
def case_detail(
    case_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    case = _get_case(db, case_id, user)
    classification = db.query(FailureClassification).filter(
        FailureClassification.risk_event_id == case.risk_event_id
    ).one_or_none()
    decisions = db.execute(
        select(Decision).where(Decision.case_id == case.id)
        .order_by(Decision.decided_at)
    ).scalars().all()
    decision_rows = []
    for d in decisions:
        candidates = db.execute(
            select(CandidateAction).where(CandidateAction.decision_id == d.id)
        ).scalars().all()
        executions = db.execute(
            select(Execution).where(Execution.decision_id == d.id)
        ).scalars().all()
        decision_rows.append({
            "id": str(d.id), "chosen_action": d.chosen_action,
            "action_params": d.action_params,
            "expected_recovery_paise": d.expected_recovery_paise,
            "confidence": float(d.confidence), "status": d.status,
            "model_version": d.model_version, "decided_at": d.decided_at.isoformat(),
            "explanation": d.explanation,
            "candidates": [
                {"action": c.action_type, "p_recovery": float(c.p_recovery),
                 "expected_value_paise": c.expected_value_paise,
                 "intervention_cost_paise": c.intervention_cost_paise,
                 "allowed_by_policy": c.allowed_by_policy,
                 "blocked_reason": c.blocked_reason}
                for c in candidates
            ],
            "executions": [
                {"id": str(e.id), "status": e.status, "result": e.result,
                 "error": e.error, "started_at": e.started_at.isoformat()}
                for e in executions
            ],
        })
    outcome = db.query(Outcome).filter(Outcome.case_id == case.id).one_or_none()
    messages = db.execute(
        select(Message).where(Message.case_id == case.id)
    ).scalars().all()
    audit = db.execute(
        select(AuditEvent).where(AuditEvent.case_id == case.id)
        .order_by(AuditEvent.created_at)
    ).scalars().all()
    return {
        "id": str(case.id), "state": case.state, "amount_paise": case.amount_paise,
        "is_synthetic": case.is_synthetic, "opened_at": case.opened_at.isoformat(),
        "retry_count": case.retry_count, "contact_count": case.contact_count,
        "next_action_at": case.next_action_at.isoformat() if case.next_action_at else None,
        "diagnosis": ({
            "primary_cause": classification.primary_cause,
            "confidence": float(classification.confidence),
            "model_version": classification.model_version,
            "rationale": classification.rationale,
        } if classification else None),
        "decisions": decision_rows,
        "messages": [{"channel": m.channel, "provider": m.provider,
                      "status": m.status, "template": m.template_key,
                      "sent_at": m.sent_at.isoformat() if m.sent_at else None}
                     for m in messages],
        "outcome": ({"outcome": outcome.outcome,
                     "amount_recovered_paise": outcome.amount_recovered_paise,
                     "source": outcome.source,
                     "observed_at": outcome.observed_at.isoformat()}
                    if outcome else None),
        "audit": [{"event_type": a.event_type, "actor_type": a.actor_type,
                   "payload": a.payload, "created_at": a.created_at.isoformat()}
                  for a in audit],
    }


@router.post("/cases/{case_id}/decide", status_code=status.HTTP_201_CREATED)
def decide_case(
    case_id: str,
    user: User = Depends(require_minimum("operator")),
    db: Session = Depends(get_db),
) -> dict:
    case = _get_case(db, case_id, user)
    try:
        decision, arm = route_decision(db, case)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return {
        "decision_id": str(decision.id),
        "chosen_action": decision.chosen_action,
        "status": decision.status,
        "expected_recovery_paise": decision.expected_recovery_paise,
        "model_version": decision.model_version,
        "experiment_arm": arm,
        "case_state": case.state,
        "explanation": decision.explanation,
    }


@router.post("/decisions/{decision_id}/execute")
def execute_endpoint(
    decision_id: str,
    user: User = Depends(require_minimum("operator")),
    db: Session = Depends(get_db),
) -> dict:
    decision = _get_decision(db, decision_id, user)
    try:
        execution = execute_decision(db, decision, actor=user)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return {"execution_id": str(execution.id), "status": execution.status,
            "result": execution.result, "error": execution.error,
            "case_state": db.get(RecoveryCase, decision.case_id).state}


@router.post("/decisions/{decision_id}/approve")
def approve_endpoint(
    decision_id: str,
    user: User = Depends(require_minimum("operator")),
    db: Session = Depends(get_db),
) -> dict:
    decision = _get_decision(db, decision_id, user)
    try:
        decision, execution = approve_decision(db, str(decision.id), user)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return {"decision_status": decision.status,
            "execution": ({"id": str(execution.id), "status": execution.status,
                           "result": execution.result} if execution else None)}


@router.post("/decisions/{decision_id}/reject")
def reject_endpoint(
    decision_id: str,
    user: User = Depends(require_minimum("operator")),
    db: Session = Depends(get_db),
) -> dict:
    decision = _get_decision(db, decision_id, user)
    try:
        reject_decision(db, str(decision.id), user)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return {"decision_status": "superseded",
            "case_state": db.get(RecoveryCase, decision.case_id).state}


def _get_decision(db: Session, decision_id: str, user: User) -> Decision:
    try:
        import uuid as _uuid
        decision = db.get(Decision, _uuid.UUID(decision_id))
    except ValueError:
        decision = None
    if decision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "decision not found")
    case = db.get(RecoveryCase, decision.case_id)
    if case is None or str(case.merchant_id) != str(user.merchant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "decision not found")
    return decision
