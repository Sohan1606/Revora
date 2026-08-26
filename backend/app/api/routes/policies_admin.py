"""Policy routes — view versions, create new versions (admin+), activate."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_minimum
from app.domain.policies.engine import DEFAULT_POLICY_DEF
from app.domain.recovery.audit import record_audit
from app.infrastructure.database import get_db
from app.infrastructure.models import PolicyVersion, User

router = APIRouter(prefix="/policies", tags=["policies"])

REQUIRED_KEYS = {"max_retries", "max_contacts", "min_actionable_amount_paise",
                 "intervention_costs_paise", "friction_scores",
                 "require_approval_above_paise", "blocked_actions", "cause_blocks"}
VALID_ACTIONS = set(DEFAULT_POLICY_DEF["intervention_costs_paise"])


class PolicyCreate(BaseModel):
    version: str = Field(min_length=1, max_length=50)
    definition: dict


@router.get("")
def list_policies(
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
) -> dict:
    rows = db.execute(
        select(PolicyVersion).where(PolicyVersion.merchant_id == user.merchant_id)
        .order_by(PolicyVersion.created_at.desc())
    ).scalars().all()
    return {"policies": [
        {"id": str(p.id), "version": p.version, "is_active": p.is_active,
         "definition": p.definition,
         "created_at": p.created_at.isoformat(),
         "activated_at": p.activated_at.isoformat() if p.activated_at else None}
        for p in rows
    ]}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_policy(
    body: PolicyCreate,
    user: User = Depends(require_minimum("admin")),
    db: Session = Depends(get_db),
) -> dict:
    _validate_definition(body.definition)
    policy = PolicyVersion(merchant_id=user.merchant_id, version=body.version,
                           definition=body.definition, is_active=False)
    db.add(policy)
    db.commit()
    return {"id": str(policy.id), "version": policy.version, "is_active": False}


@router.post("/{policy_id}/activate")
def activate_policy(
    policy_id: str,
    user: User = Depends(require_minimum("admin")),
    db: Session = Depends(get_db),
) -> dict:
    import uuid as _uuid
    try:
        policy = db.get(PolicyVersion, _uuid.UUID(policy_id))
    except ValueError:
        policy = None
    if policy is None or str(policy.merchant_id) != str(user.merchant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "policy not found")

    for other in db.execute(
        select(PolicyVersion).where(PolicyVersion.merchant_id == user.merchant_id,
                                    PolicyVersion.is_active.is_(True))
    ).scalars():
        other.is_active = False
    policy.is_active = True
    policy.activated_at = datetime.now(timezone.utc)
    record_audit(db, event_type="policy_activated", merchant_id=user.merchant_id,
                 actor_type="user", actor_id=str(user.id),
                 payload={"policy_version": policy.version, "policy_id": str(policy.id)})
    db.commit()
    return {"id": str(policy.id), "version": policy.version, "is_active": True}


def _validate_definition(definition: dict) -> None:
    missing = REQUIRED_KEYS - set(definition or {})
    if missing:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"policy definition missing keys: {sorted(missing)}")
    for key in ("max_retries", "max_contacts", "min_actionable_amount_paise"):
        if not isinstance(definition[key], int) or definition[key] < 0:
            raise HTTPException(422, f"{key} must be a non-negative integer")
    for action in definition["intervention_costs_paise"]:
        if action not in VALID_ACTIONS:
            raise HTTPException(422, f"unknown action in costs: {action}")
    if not isinstance(definition["blocked_actions"], list):
        raise HTTPException(422, "blocked_actions must be a list")
