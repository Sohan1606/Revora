"""Experiment routes — create, start/stop, results (incremental recovery)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_minimum
from app.domain.experiments.engine import (
    create_experiment, experiment_results, start_experiment, stop_experiment,
)
from app.infrastructure.database import get_db
from app.infrastructure.models import Experiment, User

router = APIRouter(prefix="/experiments", tags=["experiments"])


class ExperimentCreate(BaseModel):
    name: str = Field(min_length=3, max_length=255)
    hypothesis: str | None = None


@router.post("", status_code=status.HTTP_201_CREATED)
def create_endpoint(
    body: ExperimentCreate,
    user: User = Depends(require_minimum("admin")),
    db: Session = Depends(get_db),
) -> dict:
    experiment = create_experiment(db, merchant_id=user.merchant_id,
                                   name=body.name, hypothesis=body.hypothesis,
                                   created_by=user)
    return {"id": str(experiment.id), "name": experiment.name,
            "status": experiment.status}


@router.get("")
def list_experiments(
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
) -> dict:
    rows = db.execute(
        select(Experiment).where(Experiment.merchant_id == user.merchant_id)
        .order_by(Experiment.created_at.desc())
    ).scalars().all()
    return {"experiments": [
        {"id": str(e.id), "name": e.name, "status": e.status,
         "strategies": {"treatment": e.strategy_treatment, "control": e.strategy_control},
         "started_at": e.started_at.isoformat() if e.started_at else None,
         "ended_at": e.ended_at.isoformat() if e.ended_at else None}
        for e in rows
    ]}


@router.get("/{experiment_id}")
def get_endpoint(
    experiment_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    experiment = _get(db, experiment_id, user)
    return experiment_results(db, str(experiment.id))


@router.post("/{experiment_id}/start")
def start_endpoint(
    experiment_id: str,
    user: User = Depends(require_minimum("admin")),
    db: Session = Depends(get_db),
) -> dict:
    experiment = _get(db, experiment_id, user)
    try:
        start_experiment(db, experiment)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return {"id": str(experiment.id), "status": experiment.status}


@router.post("/{experiment_id}/stop")
def stop_endpoint(
    experiment_id: str,
    user: User = Depends(require_minimum("admin")),
    db: Session = Depends(get_db),
) -> dict:
    experiment = _get(db, experiment_id, user)
    try:
        stop_experiment(db, experiment)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return {"id": str(experiment.id), "status": experiment.status}


def _get(db: Session, experiment_id: str, user: User) -> Experiment:
    import uuid as _uuid
    try:
        experiment = db.get(Experiment, _uuid.UUID(experiment_id))
    except ValueError:
        experiment = None
    if experiment is None or str(experiment.merchant_id) != str(user.merchant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "experiment not found")
    return experiment
