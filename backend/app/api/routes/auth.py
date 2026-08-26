"""Auth routes.

- GET  /api/auth/me        → current user (works in both auth modes)
- POST /api/auth/login     → PRODUCTION sign-in: Supabase password grant
  (server-side only; the service key never reaches the browser). 404 in dev mode.
- POST /api/auth/dev/token → LOCAL DEV ONLY: issue a signed JWT for an existing user.
  Disabled (404) whenever ENV != local or Supabase auth is configured.
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

import app.api.deps as deps
from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.ratelimit import rate_limit
from app.core.security import (
    DEV_TOKEN_TTL_MINUTES, issue_dev_token, verify_supabase_token,
)
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


# ---------------------------------------------------------------- production


class SupabaseLoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=6, max_length=128)

    @field_validator("email")
    @classmethod
    def _basic_email(cls, v: str) -> str:
        if "@" not in v or len(v) > 320:
            raise ValueError("must be a valid email address")
        return v.lower().strip()


def _supabase_password_grant(email: str, password: str) -> tuple[bool, str]:
    """Server-side Supabase password grant. Returns (ok, access_token | error).
    The service key stays server-side; passwords are forwarded over HTTPS only
    and never stored or logged."""
    settings = get_settings()
    url = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/token?grant_type=password"
    try:
        response = httpx.post(
            url,
            json={"email": email, "password": password},
            headers={
                "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
                "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
            },
            timeout=10.0,
        )
    except httpx.HTTPError:
        return False, "Authentication provider unreachable — try again shortly."
    if response.status_code != 200:
        return False, "Invalid email or password."
    token = (response.json() or {}).get("access_token")
    if not token:
        return False, "Invalid email or password."
    return True, token


@router.post("/login")
def supabase_login(
    body: SupabaseLoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(rate_limit(limit=10, window_seconds=60, scope="login")),
) -> dict:
    settings = get_settings()
    if settings.auth_mode != "supabase":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    ok, token_or_error = _supabase_password_grant(body.email, body.password)
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, token_or_error)

    # Validate the issued token against OUR configured secret and resolve the
    # user (first login JIT-provisions merchant + owner — audited).
    try:
        claims = verify_supabase_token(token_or_error, settings.SUPABASE_JWT_SECRET)
    except Exception:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Auth misconfigured: SUPABASE_JWT_SECRET does not match the provider.",
        )
    user = deps._resolve_supabase_user(db, claims)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password.")

    record_audit(db, event_type="login_succeeded", merchant_id=user.merchant_id,
                 actor_type="user", actor_id=str(user.id),
                 payload={"email": user.email, "mode": "supabase"})
    db.commit()
    return {
        "access_token": token_or_error,
        "token_type": "bearer",
        "expires_in": 3600,
        "auth_mode": "supabase",
    }


# ---------------------------------------------------------------- local dev


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
    record_audit(db, event_type="dev_token_issued", merchant_id=user.merchant_id,
                 actor_type="user", actor_id=str(user.id),
                 payload={"email": user.email, "note": "local dev tooling"})
    db.commit()
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": DEV_TOKEN_TTL_MINUTES * 60,
        "note": "DEV-ONLY token issuer. Disabled outside ENV=local / when Supabase is configured.",
    }
