"""Phase 5+6 tests — JWT auth (dev + supabase paths), RBAC, tenant isolation."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest

from app.core.config import get_settings
from app.infrastructure.models import Merchant, User

M1 = uuid.uuid4()
M2 = uuid.uuid4()
DEV_SECRET = "d" * 64
SUPA_SECRET = "s" * 64


@pytest.fixture()
def settings():
    s = get_settings()
    old = (s.DEV_JWT_SECRET, s.SUPABASE_JWT_SECRET, s.ENV)
    s.DEV_JWT_SECRET, s.SUPABASE_JWT_SECRET = DEV_SECRET, ""
    yield s
    s.DEV_JWT_SECRET, s.SUPABASE_JWT_SECRET, s.ENV = old


@pytest.fixture()
def seeded(db):
    db.add_all(
        [
            Merchant(id=M1, name="Merchant One"),
            Merchant(id=M2, name="Merchant Two"),
            User(merchant_id=M1, email="owner1@t.io", full_name="O1", role="owner"),
            User(merchant_id=M1, email="admin1@t.io", full_name="A1", role="admin"),
            User(merchant_id=M1, email="operator1@t.io", full_name="P1", role="operator"),
            User(merchant_id=M1, email="viewer1@t.io", full_name="V1", role="viewer"),
            User(merchant_id=M2, email="owner2@t.io", full_name="O2", role="owner"),
        ]
    )
    db.commit()
    return db


def _get_token(client, email: str) -> str:
    r = client.post("/api/auth/dev/token", json={"email": email})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------- dev token issuance ----------

def test_dev_token_roundtrip(client, seeded, settings) -> None:
    token = _get_token(client, "viewer1@t.io")
    r = client.get("/api/auth/me", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "viewer1@t.io"
    assert body["role"] == "viewer"
    assert body["auth_mode"] == "dev"
    assert body["merchant_id"] == str(M1)


def test_dev_token_unknown_user_no_enumeration(client, seeded, settings) -> None:
    r = client.post("/api/auth/dev/token", json={"email": "nobody@t.io"})
    assert r.status_code == 401


def test_dev_token_invalid_email_rejected(client, settings) -> None:
    r = client.post("/api/auth/dev/token", json={"email": "not-an-email"})
    assert r.status_code == 422


# ---------- verification failures ----------

def test_missing_token_401(client, settings) -> None:
    assert client.get("/api/auth/me").status_code == 401


def test_garbage_token_401(client, seeded, settings) -> None:
    r = client.get("/api/auth/me", headers=_auth("garbage.token.here"))
    assert r.status_code == 401


def test_wrong_signature_401(client, seeded, settings) -> None:
    token = _get_token(client, "owner1@t.io")
    # Re-sign same claims with a different secret
    claims = pyjwt.decode(token, DEV_SECRET, algorithms=["HS256"],
                          audience="revora:local", issuer="revora:local")
    claims.pop("exp")
    forged = pyjwt.encode(claims, "a" * 48, algorithm="HS256")
    r = client.get("/api/auth/me", headers=_auth(forged))
    assert r.status_code == 401


def test_expired_token_401(client, seeded, settings) -> None:
    user = seeded.query(User).filter(User.email == "owner1@t.io").one()
    now = datetime.now(timezone.utc)
    expired = pyjwt.encode(
        {"sub": str(user.id), "email": user.email, "typ": "dev",
         "iat": int((now - timedelta(hours=25)).timestamp()),
         "exp": int((now - timedelta(hours=1)).timestamp()),
         "iss": "revora:local", "aud": "revora:local"},
        DEV_SECRET, algorithm="HS256",
    )
    assert client.get("/api/auth/me", headers=_auth(expired)).status_code == 401


def test_token_for_deleted_user_401(client, seeded, settings) -> None:
    """Claims alone must never authenticate — the user row must exist (no injection)."""
    ghost = pyjwt.encode(
        {"sub": str(uuid.uuid4()), "typ": "dev",
         "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
         "iss": "revora:local", "aud": "revora:local"},
        DEV_SECRET, algorithm="HS256",
    )
    assert client.get("/api/auth/me", headers=_auth(ghost)).status_code == 401


def test_inactive_user_401(client, seeded, settings) -> None:
    user = seeded.query(User).filter(User.email == "viewer1@t.io").one()
    token_while_active = _get_token(client, "viewer1@t.io")
    user.is_active = False
    seeded.commit()

    # 1) issuance refused for inactive user (also: no account-enumeration difference)
    r = client.post("/api/auth/dev/token", json={"email": "viewer1@t.io"})
    assert r.status_code == 401
    # 2) token issued BEFORE deactivation no longer authenticates
    assert client.get("/api/auth/me", headers=_auth(token_while_active)).status_code == 401


# ---------- RBAC ----------

def test_rbac_users_endpoint_matrix(client, seeded, settings) -> None:
    url = "/api/admin/users"
    for email, expected in [
        ("owner1@t.io", 200), ("admin1@t.io", 200),
        ("operator1@t.io", 403), ("viewer1@t.io", 403),
    ]:
        r = client.get(url, headers=_auth(_get_token(client, email)))
        assert r.status_code == expected, f"{email}: {r.status_code} != {expected}"


def test_tenant_isolation(client, seeded, settings) -> None:
    """Merchant-2 owner must see ONLY merchant-2 users — never merchant-1 rows."""
    r = client.get("/api/admin/users", headers=_auth(_get_token(client, "owner2@t.io")))
    assert r.status_code == 200
    body = r.json()
    assert body["merchant_id"] == str(M2)
    emails = [u["email"] for u in body["users"]]
    assert emails == ["owner2@t.io"]
    assert "owner1@t.io" not in emails


# ---------- Supabase path (proves prod mode without a Supabase account) ----------

def test_supabase_mode_uses_supabase_claims(client, seeded, settings) -> None:
    settings.SUPABASE_JWT_SECRET = SUPA_SECRET
    user = seeded.query(User).filter(User.email == "admin1@t.io").one()
    user.supabase_user_id = "sup-auth-123"
    seeded.commit()

    now = datetime.now(timezone.utc)
    supa_token = pyjwt.encode(
        {"sub": "sup-auth-123", "aud": "authenticated", "email": user.email,
         "exp": int((now + timedelta(minutes=10)).timestamp())},
        SUPA_SECRET, algorithm="HS256",
    )
    r = client.get("/api/auth/me", headers=_auth(supa_token))
    assert r.status_code == 200
    assert r.json()["email"] == "admin1@t.io"
    assert r.json()["auth_mode"] == "supabase"


def test_supabase_mode_disables_dev_token_endpoint(client, seeded, settings) -> None:
    settings.SUPABASE_JWT_SECRET = SUPA_SECRET
    r = client.post("/api/auth/dev/token", json={"email": "owner1@t.io"})
    assert r.status_code == 404


def test_dev_secret_rejected_in_supabase_mode(client, seeded, settings) -> None:
    settings.SUPABASE_JWT_SECRET = SUPA_SECRET
    dev_token = _get_token_disabled(client)  # endpoint is 404 in supabase mode
    if dev_token:  # pragma: no cover
        pytest.fail("dev endpoint should be disabled")
    # Craft a dev-signed token directly; it must NOT verify against the supabase secret
    now = datetime.now(timezone.utc)
    token = pyjwt.encode(
        {"sub": str(uuid.uuid4()), "typ": "dev", "aud": "revora:local",
         "iss": "revora:local",
         "exp": int((now + timedelta(hours=1)).timestamp())},
        DEV_SECRET, algorithm="HS256",
    )
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 401


def _get_token_disabled(client) -> str | None:
    r = client.post("/api/auth/dev/token", json={"email": "owner1@t.io"})
    return r.json().get("access_token") if r.status_code == 200 else None
