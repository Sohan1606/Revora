"""Production login (Supabase password grant, server-side).
Regression for the deployment-blocking gap: the frontend had no production
sign-in path — /auth/login reuses the existing JWT verification + JIT
provisioning machinery entirely."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.infrastructure.models  # noqa: F401
from app.core.config import get_settings
from app.infrastructure.database import Base
from app.infrastructure.models import Merchant, User

M1 = uuid.uuid4()
DEV_SECRET = "l" * 64
SUPA_SECRET = "z" * 64


@pytest.fixture()
def client():
    s = get_settings()
    old = (s.DEV_JWT_SECRET, s.SUPABASE_JWT_SECRET, s.SUPABASE_URL,
           s.SUPABASE_SERVICE_ROLE_KEY)
    s.DEV_JWT_SECRET, s.SUPABASE_JWT_SECRET = DEV_SECRET, ""
    s.SUPABASE_URL, s.SUPABASE_SERVICE_ROLE_KEY = "", ""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = S()
    db.add_all([Merchant(id=M1, name="M1"),
                User(merchant_id=M1, email="owner@t.io", full_name="O", role="owner")])
    db.commit()
    db.close()

    from app.infrastructure.database import get_db, get_session_factory
    from app.main import create_app
    from app.core import ratelimit
    ratelimit._windows.clear()

    def _override():
        session = S()
        try:
            yield session
        finally:
            session.close()

    app = create_app()
    app.dependency_overrides[get_db] = _override
    app.dependency_overrides[get_session_factory] = lambda: S
    with TestClient(app) as c:
        c.Session = S
        yield c
    engine.dispose()
    (s.DEV_JWT_SECRET, s.SUPABASE_JWT_SECRET, s.SUPABASE_URL,
     s.SUPABASE_SERVICE_ROLE_KEY) = old


def _supa_token(secret: str, sub: str, email: str) -> str:
    now = datetime.now(timezone.utc)
    return pyjwt.encode({"sub": sub, "aud": "authenticated", "email": email,
                         "exp": int((now + timedelta(hours=1)).timestamp())},
                        secret, algorithm="HS256")


def test_login_is_404_in_dev_mode(client):
    r = client.post("/api/auth/login", json={"email": "owner@t.io", "password": "secret1"})
    assert r.status_code == 404


def test_login_success_provisions_and_returns_supabase_token(client, monkeypatch):
    from app.api.routes import auth as auth_route
    s = get_settings()
    s.SUPABASE_URL = "https://fake.supabase.co"
    s.SUPABASE_SERVICE_ROLE_KEY = "svc"
    s.SUPABASE_JWT_SECRET = SUPA_SECRET
    token = _supa_token(SUPA_SECRET, "sup-login-1", "new.person@example.in")
    monkeypatch.setattr(auth_route, "_supabase_password_grant",
                        lambda e, p: (True, token))

    r = client.post("/api/auth/login",
                    json={"email": "new.person@example.in", "password": "hunter22"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"] == token and body["auth_mode"] == "supabase"

    # token works against the normal auth surface; JIT created merchant + owner
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["role"] == "owner"

    # audit recorded
    from app.infrastructure.models import AuditEvent
    db = client.Session()
    kinds = [a.event_type for a in db.query(AuditEvent).all()]
    db.close()
    assert "login_succeeded" in kinds and "user_jit_provisioned" in kinds


def test_login_rejects_bad_credentials(client, monkeypatch):
    from app.api.routes import auth as auth_route
    s = get_settings()
    s.SUPABASE_URL = "https://fake.supabase.co"
    s.SUPABASE_SERVICE_ROLE_KEY = "svc"
    s.SUPABASE_JWT_SECRET = SUPA_SECRET
    monkeypatch.setattr(auth_route, "_supabase_password_grant",
                        lambda e, p: (False, "Invalid email or password."))
    r = client.post("/api/auth/login",
                    json={"email": "owner@t.io", "password": "wrongpwd"})
    assert r.status_code == 401


def test_login_rejects_token_signed_with_wrong_secret(client, monkeypatch):
    """Defense in depth: even if Supabase returned a token, it must verify
    against OUR configured secret before being accepted."""
    from app.api.routes import auth as auth_route
    s = get_settings()
    s.SUPABASE_URL = "https://fake.supabase.co"
    s.SUPABASE_SERVICE_ROLE_KEY = "svc"
    s.SUPABASE_JWT_SECRET = SUPA_SECRET
    forged = _supa_token("attacker-secret", "sup-evil", "evil@x.io")
    monkeypatch.setattr(auth_route, "_supabase_password_grant",
                        lambda e, p: (True, forged))
    r = client.post("/api/auth/login",
                    json={"email": "evil@x.io", "password": "hunter22"})
    assert r.status_code == 503  # auth misconfiguration surfaced, not accepted


def test_login_input_validation(client):
    s = get_settings()
    s.SUPABASE_URL = "https://fake.supabase.co"
    s.SUPABASE_SERVICE_ROLE_KEY = "svc"
    s.SUPABASE_JWT_SECRET = SUPA_SECRET
    assert client.post("/api/auth/login",
                       json={"email": "not-an-email", "password": "hunter22"}).status_code == 422
    assert client.post("/api/auth/login",
                       json={"email": "a@b.io", "password": "short"}).status_code == 422
