"""Signup endpoint (open demo account creation via Supabase, provider-verified)."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.infrastructure.models  # noqa: F401
from app.api import deps
from app.core.config import get_settings
from app.infrastructure.database import Base
from app.infrastructure.models import Merchant

M1 = uuid.uuid4()


@pytest.fixture()
def client(monkeypatch):
    s = get_settings()
    old = (s.DEV_JWT_SECRET, s.SUPABASE_JWT_SECRET, s.SUPABASE_URL,
           s.SUPABASE_SERVICE_ROLE_KEY)
    s.DEV_JWT_SECRET, s.SUPABASE_JWT_SECRET = "", ""
    s.SUPABASE_URL, s.SUPABASE_SERVICE_ROLE_KEY = "https://s.co", "svc"
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = S()
    db.add(Merchant(id=M1, name="M1"))
    db.commit()
    db.close()

    from app.infrastructure.database import get_db, get_session_factory
    from app.main import create_app

    def _override():
        session = S()
        try:
            yield session
        finally:
            session.close()

    app = create_app()
    app.dependency_overrides[get_db] = _override
    app.dependency_overrides[get_session_factory] = lambda: S
    deps._provider_cache.clear()

    from app.core import security

    def fake_provider(token, url, key, transport=None):
        if token and token.startswith("good-"):
            return {"sub": f"sup-{token[-3:]}", "email": "judge@example.in",
                    "aud": "authenticated"}
        return None

    monkeypatch.setattr(security, "supabase_claims_via_provider", fake_provider)

    with TestClient(app) as c:
        c.Session = S
        yield c
    deps._provider_cache.clear()
    (s.DEV_JWT_SECRET, s.SUPABASE_JWT_SECRET, s.SUPABASE_URL,
     s.SUPABASE_SERVICE_ROLE_KEY) = old


def test_signup_creates_account_and_session(client, monkeypatch):
    from app.api.routes import auth as auth_route
    monkeypatch.setattr(auth_route, "_supabase_signup",
                        lambda e, p: (True, "good-abc", ""))
    r = client.post("/api/auth/signup",
                    json={"email": "judge@example.in", "password": "hunter22"})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["role"] == "owner"  # JIT workspace


def test_signup_duplicate_conflict(client, monkeypatch):
    from app.api.routes import auth as auth_route
    monkeypatch.setattr(auth_route, "_supabase_signup",
                        lambda e, p: (False, None, "An account with that email already exists — sign in instead."))
    r = client.post("/api/auth/signup",
                    json={"email": "judge@example.in", "password": "hunter22"})
    assert r.status_code == 409
    assert "sign in instead" in r.json()["detail"]


def test_signup_with_email_confirmation_required(client, monkeypatch):
    from app.api.routes import auth as auth_route
    monkeypatch.setattr(auth_route, "_supabase_signup", lambda e, p: (True, None, ""))
    monkeypatch.setattr(auth_route, "_supabase_password_grant",
                        lambda e, p: (False, "Invalid email or password."))
    r = client.post("/api/auth/signup",
                    json={"email": "judge@example.in", "password": "hunter22"})
    assert r.status_code == 403
    assert "Confirm email" in r.json()["detail"]  # exact operator instruction


def test_signup_is_404_in_dev_mode(client):
    s = get_settings()
    old = (s.SUPABASE_URL, s.SUPABASE_SERVICE_ROLE_KEY, s.SUPABASE_JWT_SECRET)
    s.SUPABASE_URL, s.SUPABASE_SERVICE_ROLE_KEY, s.SUPABASE_JWT_SECRET = "", "", ""
    r = client.post("/api/auth/signup",
                    json={"email": "x@y.io", "password": "hunter22"})
    assert r.status_code == 404
    (s.SUPABASE_URL, s.SUPABASE_SERVICE_ROLE_KEY, s.SUPABASE_JWT_SECRET) = old


def test_signup_input_validation(client):
    assert client.post("/api/auth/signup",
                       json={"email": "nope", "password": "hunter22"}).status_code == 422
    assert client.post("/api/auth/signup",
                       json={"email": "a@b.io", "password": "123"}).status_code == 422
