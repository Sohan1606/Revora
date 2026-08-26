"""Auth routes.

- GET  /api/auth/me        → current user (works in both auth modes)
- POST /api/auth/dev/token → LOCAL DEV ONLY: issue a signed JWT for an existing user.
  Disabled (404) whenever ENV != local or Supabase auth is configured.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.ratelimit import rate_limit
from app.core.security import DEV_TOKEN_TTL_MINUTES, issue_dev_token
from app.domain.recovery.audit import record_audit
from app.infrastructure.database import get_db
from app.infrastructure.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    settings = get_settings()
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "merchant_id": str(user.merchant_id) if user.merchant_id else None,
        "auth_mode": settings.auth_mode,
    }


class DevTokenRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def _basic_email(cls, v: str) -> str:
        if "@" not in v or len(v) > 320:
            raise ValueError("must be a valid email address")
        return v.lower().strip()


@router.post("/dev/token")
def dev_token(
    body: DevTokenRequest,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(rate_limit(limit=30, window_seconds=60, scope="dev_token")),
) -> dict:
    settings = get_settings()
    if settings.ENV != "local" or settings.auth_mode != "dev":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if not settings.DEV_JWT_SECRET:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "DEV_JWT_SECRET is not configured — generate one and put it in backend/.env",
        )

    user = db.query(User).filter(User.email == body.email).one_or_none()
    if user is None or not user.is_active:
        # Dev-only endpoint: being explicit beats enumeration paranoia here.
        # (Supabase mode — the production path — never routes through this code.)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "No active account for that email. Dev mode uses the seeded accounts "
            "(dev-owner@ / dev-admin@ / dev-operator@ / dev-viewer@revora.local). "
            "Production sign-in happens via Supabase Auth.",
        )

    token = issue_dev_token(
        subject=str(user.id),
        email=user.email,
        role=user.role,
        merchant_id=str(user.merchant_id) if user.merchant_id else None,
        secret=settings.DEV_JWT_SECRET,
    )
    record_audit(
        db,
        event_type="dev_token_issued",
        merchant_id=user.merchant_id,
        actor_type="user",
        actor_id=str(user.id),
        payload={"email": user.email, "note": "local dev tooling"},
    )
    db.commit()
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": DEV_TOKEN_TTL_MINUTES * 60,
        "note": "DEV-ONLY token issuer. Disabled outside ENV=local / when Supabase is configured.",
    }
