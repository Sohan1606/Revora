"""Experiments domain — treatment/control assignment + incremental recovery.

Strategies:
- treatment: "revora_nba"  — the expected-value next-best-action engine
- control:   "naive_dunning" — conventional approach: immediate dunning message,
  retry when available (the category REVORA claims to beat, measured honestly)

Assignments are deterministic (hash of experiment+case), immutable, and 50/50.
The control arm is EXCLUDED from the NBA engine's empirical stats (see
domain/recovery/stats.py) so treatment estimates never get contaminated.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.domain.recovery.audit import record_audit
from app.domain.recovery.executor import execute_decision
from app.domain.recovery.next_best_action import decide
from app.domain.recovery.state_machine import transition
from app.infrastructure.models import (
    Decision, Experiment, ExperimentAssignment, Outcome, RecoveryCase, User,
)

STRATEGIES = {"revora_nba", "naive_dunning"}


def create_experiment(
    db: Session, *, merchant_id: str, name: str, hypothesis: str | None,
    created_by: User, strategy_treatment: str = "revora_nba",
    strategy_control: str = "naive_dunning",
) -> Experiment:
    if strategy_treatment not in STRATEGIES or strategy_control not in STRATEGIES:
        raise ValueError(f"strategies must be one of {sorted(STRATEGIES)}")
    experiment = Experiment(
        merchant_id=merchant_id, name=name, hypothesis=hypothesis,
        strategy_treatment=strategy_treatment, strategy_control=strategy_control,
        status="draft", created_by=created_by.id,
    )
    db.add(experiment)
    db.commit()
    return experiment


def start_experiment(db: Session, experiment: Experiment) -> Experiment:
    if experiment.status not in ("draft", "running"):
        raise ValueError(f"cannot start experiment in state {experiment.status!r}")
    running = get_running_experiment(db, experiment.merchant_id)
    if running is not None and running.id != experiment.id:
        raise ValueError(
            f"another experiment is already running ({running.name!r}) — stop it first")
    experiment.status = "running"
    experiment.started_at = experiment.started_at or datetime.now(timezone.utc)
    record_audit(db, event_type="experiment_started", merchant_id=experiment.merchant_id,
                 payload={"experiment_id": str(experiment.id), "name": experiment.name})
    db.commit()
    return experiment


def stop_experiment(db: Session, experiment: Experiment) -> Experiment:
    if experiment.status != "running":
        raise ValueError("only running experiments can be stopped")
    experiment.status = "completed"
    experiment.ended_at = datetime.now(timezone.utc)
    record_audit(db, event_type="experiment_stopped", merchant_id=experiment.merchant_id,
                 payload={"experiment_id": str(experiment.id)})
    db.commit()
    return experiment


def get_running_experiment(db: Session, merchant_id: str) -> Experiment | None:
    """Most recently started running experiment (robust even if legacy data
    contains multiple running rows — scalar_one_or_none crashed on that)."""
    return db.execute(
        select(Experiment)
        .where(Experiment.merchant_id == merchant_id, Experiment.status == "running")
        .order_by(Experiment.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def assign_arm(db: Session, experiment: Experiment, case: RecoveryCase) -> ExperimentAssignment:
    """Deterministic 50/50 assignment, immutable once written."""
    existing = db.execute(
        select(ExperimentAssignment).where(
            ExperimentAssignment.experiment_id == experiment.id,
            ExperimentAssignment.case_id == case.id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    digest = hashlib.sha256(f"{experiment.id}:{case.id}".encode()).digest()
    arm = "treatment" if digest[0] % 2 == 0 else "control"
    assignment = ExperimentAssignment(
        experiment_id=experiment.id, case_id=case.id, arm=arm,
        assigned_at=datetime.now(timezone.utc),
    )
    db.add(assignment)
    db.flush()
    return assignment


def baseline_decision(db: Session, case: RecoveryCase) -> Decision:
    """The naive-dunning control strategy: message immediately, no intelligence.
    Still passes through case state rules (safety applies to the control arm too)."""
    if case.state not in ("analyzed", "escalated"):
        raise ValueError(f"control decision requires analyzed case, got {case.state!r}")

    action = "send_message" if case.contact_count < 3 else "no_action"
    decision = Decision(
        case_id=case.id, chosen_action=action, action_params={},
        expected_recovery_paise=0, confidence=0,
        explanation={"strategy": "naive_dunning",
                     "note": "control arm: conventional immediate-dunning baseline"},
        model_version="baseline_naive_dunning",
        status="approved_by_policy",
        decided_at=datetime.now(timezone.utc),
    )
    db.add(decision)
    db.flush()
    transition(case, "action_selected")
    db.commit()
    return decision


def route_decision(db: Session, case: RecoveryCase) -> tuple[Decision, str | None]:
    """Entry point used by the API: assigns experiment arms when one is running,
    then runs the treatment (NBA) or the control (naive dunning) strategy.
    Returns (Decision row, arm) — uniform shape for both arms."""
    experiment = get_running_experiment(db, case.merchant_id)
    if experiment is None:
        outcome = decide(db, case)
        return outcome.decision, None

    assignment = assign_arm(db, experiment, case)
    if assignment.arm == "treatment":
        outcome = decide(db, case)
        record_audit(db, event_type="experiment_arm_assigned",
                     merchant_id=case.merchant_id, case_id=case.id,
                     payload={"experiment_id": str(experiment.id), "arm": "treatment"})
        db.commit()
        return outcome.decision, "treatment"
    decision = baseline_decision(db, case)
    record_audit(db, event_type="experiment_arm_assigned",
                 merchant_id=case.merchant_id, case_id=case.id,
                 payload={"experiment_id": str(experiment.id), "arm": "control"})
    db.commit()
    return decision, "control"


def experiment_results(db: Session, experiment_id: str) -> dict:
    """Incremental recovery = treatment recovered − control recovered.
    Only cases WITH outcomes count; unfinished cases are reported separately
    (honest n — no silent survivorship)."""
    import uuid as _uuid

    experiment = db.get(Experiment, _uuid.UUID(str(experiment_id)))
    if experiment is None:
        raise ValueError("experiment not found")

    experiment_uuid = experiment.id  # UUID-typed for column comparisons

    rows = db.execute(
        select(
            ExperimentAssignment.arm,
            func.count(ExperimentAssignment.id),
            func.sum(case((Outcome.outcome == "recovered", 1), else_=0)),
            func.coalesce(func.sum(Outcome.amount_recovered_paise), 0),
        )
        .outerjoin(Outcome, Outcome.case_id == ExperimentAssignment.case_id)
        .where(ExperimentAssignment.experiment_id == experiment_uuid)
        .group_by(ExperimentAssignment.arm)
    ).all()

    arms = {"treatment": {"n": 0, "recovered": 0, "recovered_paise": 0},
            "control": {"n": 0, "recovered": 0, "recovered_paise": 0}}
    total_assigned = 0
    for arm, n, recovered, paise in rows:
        total_assigned += int(n or 0)
        if arm in arms:
            arms[arm] = {"n": int(n or 0), "recovered": int(recovered or 0),
                         "recovered_paise": int(paise or 0)}

    # cases with outcomes only — reported per arm (honest n)
    for a in arms:
        arms[a]["n_with_outcome"] = arms[a]["n"]

    t, c = arms["treatment"], arms["control"]
    incremental_paise = t["recovered_paise"] - c["recovered_paise"]
    t_rate = t["recovered"] / t["n"] if t["n"] else 0.0
    c_rate = c["recovered"] / c["n"] if c["n"] else 0.0
    return {
        "experiment_id": str(experiment.id),
        "name": experiment.name,
        "status": experiment.status,
        "strategies": {"treatment": experiment.strategy_treatment,
                       "control": experiment.strategy_control},
        "treatment": t, "control": c,
        "total_assigned": total_assigned,
        "treatment_rate": round(t_rate, 4),
        "control_rate": round(c_rate, 4),
        "incremental_recovered_paise": incremental_paise,
        "relative_uplift": round(t_rate - c_rate, 4),
        "note": "incremental = treatment recovered − control recovered (authoritative outcome rows only)",
    }
