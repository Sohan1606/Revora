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

# Provider-verification cache: token-hash → (expires_at, claims). Bounded + TTL'd
# so the fast local path remains the common case and the provider is asked at
# most once per token per 5 minutes per process.
_provider_cache: dict[str, tuple[float, dict]] = {}
_PROVIDER_TTL_SECONDS = 300
_PROVIDER_CACHE_MAX = 512


def resolve_supabase_claims(token: str, settings=None) -> dict | None:
    """Verify a Supabase access token by the best available method.

    1. Local HS256 verification (fast; requires a correct SUPABASE_JWT_SECRET —
       legacy projects).
    2. Provider-side verification via GET /auth/v1/user (works for the newer
       asymmetric signing-key system too), cached for 5 minutes.

    Returns claims dict or None when the token is invalid by both paths."""
    import hashlib
    import time

    from app.core.security import supabase_claims_via_provider, verify_supabase_token

    settings = settings or get_settings()

    if settings.SUPABASE_JWT_SECRET:
        try:
            return verify_supabase_token(token, settings.SUPABASE_JWT_SECRET)
        except jwt.PyJWTError:
            pass  # fall through to provider verification

    if not (settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY):
        return None

    key = hashlib.sha256(token.encode()).hexdigest()
    now = time.monotonic()
    cached = _provider_cache.get(key)
    if cached and cached[0] > now:
        return cached[1]

    claims = supabase_claims_via_provider(
        token, settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
    if claims is not None:
        if len(_provider_cache) >= _PROVIDER_CACHE_MAX:
            _provider_cache.clear()
        _provider_cache[key] = (now + _PROVIDER_TTL_SECONDS, claims)
    return claims


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
            claims = resolve_supabase_claims(token, settings)
            if claims is None:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
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
