"""JWT primitives — HS256 only, real signatures, real expiry checks.

Two verification paths, never both at once (see Settings.auth_mode):
- supabase: verify with SUPABASE_JWT_SECRET, aud="authenticated" (Supabase access tokens)
- dev:      verify with DEV_JWT_SECRET, iss/aud=revora:local — LOCAL DEV ONLY

No user record is ever created from token claims alone; claims only identify a user
that already exists in our database (prevents token-forged account injection).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

DEV_ISSUER = "revora:local"
DEV_AUDIENCE = "revora:local"
SUPABASE_AUDIENCE = "authenticated"
DEV_TOKEN_TTL_MINUTES = 720
LEEWAY_SECONDS = 10


def issue_dev_token(
    *, subject: str, email: str, role: str, merchant_id: str | None, secret: str,
    ttl_minutes: int = DEV_TOKEN_TTL_MINUTES,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "email": email,
        "role": role,
        "merchant_id": merchant_id,
        "typ": "dev",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl_minutes)).timestamp()),
        "iss": DEV_ISSUER,
        "aud": DEV_AUDIENCE,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_dev_token(token: str, secret: str) -> dict[str, Any]:
    """Raise jwt.PyJWTError on any failure (signature, exp, iss, aud)."""
    return jwt.decode(
        token, secret, algorithms=["HS256"],
        audience=DEV_AUDIENCE, issuer=DEV_ISSUER, leeway=LEEWAY_SECONDS,
    )


def verify_supabase_token(token: str, secret: str) -> dict[str, Any]:
    """Verify a Supabase access token. Issuer not pinned (varies per project)."""
    return jwt.decode(
        token, secret, algorithms=["HS256"],
        audience=SUPABASE_AUDIENCE, leeway=LEEWAY_SECONDS,
        options={"verify_iss": False},
    )


def supabase_claims_via_provider(
    token: str, supabase_url: str, service_key: str, *, transport: Any = None,
) -> dict[str, Any] | None:
    """Provider-side token verification: ask Supabase itself if the token is
    valid (GET /auth/v1/user). Works for legacy HS256 projects AND the newer
    asymmetric signing-key system, where no local secret can verify tokens.
    Returns normalized claims {sub, email, ...} or None when invalid.
    `transport` is injectable for tests."""
    import httpx

    url = f"{supabase_url.rstrip('/')}/auth/v1/user"
    try:
        with httpx.Client(transport=transport, timeout=10.0) as client:
            response = client.get(
                url, headers={"apikey": service_key,
                              "Authorization": f"Bearer {token}"})
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    user = response.json() or {}
    if not user.get("id"):
        return None
    # normalize to the claim shape the rest of the system expects
    return {"sub": user["id"], "email": user.get("email"),
            "aud": SUPABASE_AUDIENCE}
