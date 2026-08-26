"""Auth + RBAC dependencies.

- get_current_user: Bearer token → verified claims → EXISTING user row (401 otherwise).
- require_minimum(role): role-hierarchy gate (owner > admin > operator > viewer).

Tenant isolation rule for all downstream queries: filter by current_user.merchant_id.
"""
from __future__ import annotations

import uuid
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import verify_dev_token, verify_supabase_token
from app.infrastructure.database import get_db
from app.infrastructure.models import User

_bearer = HTTPBearer(auto_error=False)

ROLE_LEVEL = {"viewer": 1, "operator": 2, "admin": 3, "owner": 4}


def _resolve_supabase_user(db: Session, claims: dict[str, Any]) -> "User | None":
    """Resolve a verified Supabase JWT to a REVORA user.

    Order: (1) existing link by supabase_user_id, (2) link by verified email
    (admin-invited teammate signs in), (3) JIT-provision a NEW merchant with the
    signer as owner (self-serve onboarding). Every provisioning is audited.
    """
    sub = claims.get("sub")
    if not sub:
        return None

    user = db.query(User).filter(User.supabase_user_id == sub).one_or_none()
    if user is not None:
        return user

    email = (claims.get("email") or "").lower().strip() or None
    if email:
        invited = db.query(User).filter(User.email == email).one_or_none()
        if invited is not None:
            invited.supabase_user_id = sub  # claim the invited row
            db.commit()
            return invited

    # JIT: first sign-in creates an isolated merchant with this user as owner.
    from app.domain.recovery.audit import record_audit
    from app.infrastructure.models import Merchant

    merchant = Merchant(name=email or f"merchant-{sub[:8]}")
    db.add(merchant)
    db.flush()
    user = User(
        merchant_id=merchant.id,
        email=email or f"sup-{sub[:12]}@supabase.local",
        full_name=(claims.get("user_metadata") or {}).get("full_name") or "Supabase User",
        role="owner",
        supabase_user_id=sub,
    )
    db.add(user)
    record_audit(db, event_type="user_jit_provisioned", merchant_id=merchant.id,
                 actor_type="system", payload={"sub": sub, "email": email})
    db.commit()
    return user


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    token = credentials.credentials
    settings = get_settings()

    try:
        if settings.auth_mode == "supabase":
            claims = verify_supabase_token(token, settings.SUPABASE_JWT_SECRET)
            user = _resolve_supabase_user(db, claims)
        else:
            if not settings.DEV_JWT_SECRET:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Auth not configured: set DEV_JWT_SECRET (local) or SUPABASE_JWT_SECRET",
                )
            claims = verify_dev_token(token, settings.DEV_JWT_SECRET)
            try:
                user = db.get(User, uuid.UUID(claims.get("sub", "")))
            except ValueError:
                user = None
    except jwt.PyJWTError as exc:  # any JWT verification failure
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from exc

    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    return user


def require_minimum(minimum_role: str):
    """Dependency factory: allow the given role and everything above it."""
    if minimum_role not in ROLE_LEVEL:
        raise ValueError(f"unknown role: {minimum_role}")

    def _dependency(user: User = Depends(get_current_user)) -> User:
        if ROLE_LEVEL[user.role] < ROLE_LEVEL[minimum_role]:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Requires role {minimum_role} or above (you are {user.role})",
            )
        return user

    return _dependency
