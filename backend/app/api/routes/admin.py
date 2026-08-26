"""Admin routes — merchant-scoped user management (admin and above)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import ROLE_LEVEL, get_current_user, require_minimum
from app.infrastructure.database import get_db
from app.infrastructure.models import User

router = APIRouter(prefix="/admin", tags=["admin"])


class InviteUserRequest(BaseModel):
    email: str
    full_name: str | None = None
    role: str = "viewer"

    @field_validator("role")
    @classmethod
    def _valid_role(cls, v: str) -> str:
        if v not in ROLE_LEVEL:
            raise ValueError(f"role must be one of {sorted(ROLE_LEVEL)}")
        return v


@router.post("/users", status_code=201)
def invite_user(
    body: InviteUserRequest,
    current_user: User = Depends(require_minimum("admin")),
    db: Session = Depends(get_db),
) -> dict:
    """Invite a teammate into THIS merchant. The invited person signs in via
    Supabase (or dev auth) with the same email and the row links automatically."""
    if ROLE_LEVEL[body.role] > ROLE_LEVEL[current_user.role]:
        raise HTTPException(403, f"cannot grant role above your own ({current_user.role})")
    if db.query(User).filter(User.email == body.email).one_or_none() is not None:
        raise HTTPException(409, "user with that email already exists")
    user = User(merchant_id=current_user.merchant_id, email=body.email.lower().strip(),
                full_name=body.full_name or body.email, role=body.role)
    db.add(user)
    db.commit()
    return {"id": str(user.id), "email": user.email, "role": user.role}


@router.get("/users")
def list_users(
    current_user: User = Depends(require_minimum("admin")),
    db: Session = Depends(get_db),
) -> dict:
    # Tenant isolation: ONLY the current user's merchant, always.
    rows = db.execute(
        select(User)
        .where(User.merchant_id == current_user.merchant_id)
        .order_by(User.created_at)
    ).scalars().all()
    return {
        "merchant_id": str(current_user.merchant_id),
        "users": [
            {
                "id": str(u.id),
                "email": u.email,
                "full_name": u.full_name,
                "role": u.role,
                "is_active": u.is_active,
            }
            for u in rows
        ],
    }
