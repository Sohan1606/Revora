"""Provider-side Supabase token verification (new signing-key projects where no
local HS256 secret can verify tokens). Covers: claims via /auth/v1/user, the
resolve chain (local-first → provider fallback → cache), and end-to-end login
using provider verification only."""
from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.infrastructure.models  # noqa: F401
from app.api import deps
from app.core.config import get_settings
from app.core.security import supabase_claims_via_provider
from app.infrastructure.database import Base
from app.infrastructure.models import Merchant

M1 = uuid.uuid4()


# ---------- pure provider-verification function (mocked transport) ----------

def _user_endpoint_transport(user: dict | None):
    class T(httpx.BaseTransport):
        def handle_request(self, request):
            if user is None:
                return httpx.Response(401, json={"message": "Invalid JWT"})
            return httpx.Response(200, json=user)
    return T()


def test_provider_claims_ok():
    t = _user_endpoint_transport({"id": "sup-123", "email": "x@y.io"})
    claims = supabase_claims_via_provider("tok", "https://s.co", "svc", transport=t)
    assert claims == {"sub": "sup-123", "email": "x@y.io", "aud": "authenticated"}


def test_provider_claims_invalid_token():
    t = _user_endpoint_transport(None)
    assert supabase_claims_via_provider("bad", "https://s.co", "svc", transport=t) is None


def test_provider_claims_transport_failure():
    class Boom(httpx.BaseTransport):
        def handle_request(self, request):
            raise httpx.ConnectError("down")
    assert supabase_claims_via_provider("tok", "https://s.co", "svc",
                                        transport=Boom()) is None


# ---------- resolve chain: local secret absent → provider fallback ----------

def test_resolve_falls_back_to_provider(monkeypatch):
    from app.core import security

    s = get_settings()
    old = (s.SUPABASE_JWT_SECRET, s.SUPABASE_URL, s.SUPABASE_SERVICE_ROLE_KEY)
    s.SUPABASE_JWT_SECRET, s.SUPABASE_URL, s.SUPABASE_SERVICE_ROLE_KEY = (
        "", "https://s.co", "svc")
    deps._provider_cache.clear()
    monkeypatch.setattr(
        security, "supabase_claims_via_provider",
        lambda token, url, key, transport=None: {"sub": "sup-9", "email": "z@y.io",
                                                 "aud": "authenticated"})
    claims = deps.resolve_supabase_claims("provider-token")
    assert claims["sub"] == "sup-9"
    # cached: second call does not re-hit the provider
    monkeypatch.setattr(
        security, "supabase_claims_via_provider",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should be cached")))
    assert deps.resolve_supabase_claims("provider-token")["sub"] == "sup-9"
    deps._provider_cache.clear()
    (s.SUPABASE_JWT_SECRET, s.SUPABASE_URL, s.SUPABASE_SERVICE_ROLE_KEY) = old


def test_resolve_returns_none_when_no_methods_available():
    s = get_settings()
    old = (s.SUPABASE_JWT_SECRET, s.SUPABASE_URL, s.SUPABASE_SERVICE_ROLE_KEY)
    s.SUPABASE_JWT_SECRET, s.SUPABASE_URL, s.SUPABASE_SERVICE_ROLE_KEY = "", "", ""
    assert deps.resolve_supabase_claims("any-token") is None
    (s.SUPABASE_JWT_SECRET, s.SUPABASE_URL, s.SUPABASE_SERVICE_ROLE_KEY) = old


# ---------- end-to-end login with provider-only verification ----------

@pytest.fixture()
def client(monkeypatch):
    s = get_settings()
    old = (s.DEV_JWT_SECRET, s.SUPABASE_JWT_SECRET, s.SUPABASE_URL,
           s.SUPABASE_SERVICE_ROLE_KEY)
    s.DEV_JWT_SECRET, s.SUPABASE_JWT_SECRET = "", ""   # supabase mode, NO local secret
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

    def fake_provider(token, url, key, transport=None):
        if token == "good-provider-token":
            return {"sub": "sup-prov-1", "email": "prov@example.in",
                    "aud": "authenticated"}
        return None

    from app.core import security as security_module
    monkeypatch.setattr(security_module, "supabase_claims_via_provider", fake_provider)

    with TestClient(app) as c:
        c.Session = S
        yield c
    deps._provider_cache.clear()
    (s.DEV_JWT_SECRET, s.SUPABASE_JWT_SECRET, s.SUPABASE_URL,
     s.SUPABASE_SERVICE_ROLE_KEY) = old


def test_login_with_provider_verification_only(client, monkeypatch):
    from app.api.routes import auth as auth_route
    monkeypatch.setattr(auth_route, "_supabase_password_grant",
                        lambda e, p: (True, "good-provider-token"))
    r = client.post("/api/auth/login",
                    json={"email": "prov@example.in", "password": "hunter22"})
    assert r.status_code == 200, r.text

    # the SAME provider-verified token authenticates every other endpoint
    me = client.get("/api/auth/me",
                    headers={"Authorization": "Bearer good-provider-token"})
    assert me.status_code == 200
    assert me.json()["role"] == "owner"          # JIT provisioned
    assert me.json()["email"] == "prov@example.in"


def test_rejected_token_fails_closed_everywhere(client, monkeypatch):
    from app.api.routes import auth as auth_route
    monkeypatch.setattr(auth_route, "_supabase_password_grant",
                        lambda e, p: (True, "expired-provider-token"))
    r = client.post("/api/auth/login",
                    json={"email": "prov@example.in", "password": "hunter22"})
    assert r.status_code == 503                  # unverifiable → refused
    me = client.get("/api/auth/me",
                    headers={"Authorization": "Bearer expired-provider-token"})
    assert me.status_code == 401
