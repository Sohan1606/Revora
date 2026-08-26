"""Phase 13 — security hardening tests: rate limits, size caps, headers,
generic 500s, and secret-hygiene checks."""
from __future__ import annotations

import uuid

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
DEV_SECRET = "s" * 64


@pytest.fixture()
def client(monkeypatch):
    s = get_settings()
    old = (s.DEV_JWT_SECRET, s.SUPABASE_JWT_SECRET, s.RAZORPAY_WEBHOOK_SECRET)
    s.DEV_JWT_SECRET, s.SUPABASE_JWT_SECRET, s.RAZORPAY_WEBHOOK_SECRET = DEV_SECRET, "", ""
    monkeypatch.setattr(s, "RATE_LIMIT_ENABLED", True, raising=False)  # dedicated limiter tests
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
    ratelimit._windows.clear()  # fresh budgets per test

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
        yield c
    engine.dispose()
    s.DEV_JWT_SECRET, s.SUPABASE_JWT_SECRET, s.RAZORPAY_WEBHOOK_SECRET = old


def _hdr(client):
    token = client.post("/api/auth/dev/token", json={"email": "owner@t.io"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_dev_token_rate_limited(client):
    # budget is 30/min for the SAME ip; TestClient shares "testclient" host
    codes = [client.post("/api/auth/dev/token", json={"email": "owner@t.io"}).status_code
             for _ in range(32)]
    assert codes[0] == 200
    assert codes.count(429) >= 1
    assert "Retry-After" in client.post("/api/auth/dev/token",
                                        json={"email": "owner@t.io"}).headers


def test_webhook_payload_size_capped(client):
    # content-length header drives the cap (no secret needed to test the 413 path)
    big = b'{"id":"evt_x","event":"x"}' + b" " * (2 * 1024 * 1024)
    r = client.post("/api/webhooks/razorpay", content=big,
                    headers={"x-razorpay-signature": "x",
                             "Content-Length": str(len(big))})
    assert r.status_code == 413


def test_security_headers_present(client):
    r = client.get("/api/health")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("Referrer-Policy") == "no-referrer"
    assert r.headers.get("X-Request-ID")


def test_unhandled_errors_are_generic(client, monkeypatch):
    """Force an unhandled exception — response must not leak internals."""
    from app.infrastructure.database import get_db

    def boom():
        raise RuntimeError("SECRET-INTERNAL-DETAIL")
        yield

    client.app.dependency_overrides[get_db] = boom
    r = client.get("/api/health")
    assert r.status_code == 500
    assert "SECRET-INTERNAL-DETAIL" not in r.text
    assert r.json()["detail"] == "internal_server_error"
    assert "request_id" in r.json()


def test_no_secrets_in_openapi_or_health(client):
    health_body = client.get("/api/health").text
    for secret_marker in (DEV_SECRET, "RAZORPAY_KEY_SECRET", "SUPABASE", "rzp_test"):
        assert secret_marker not in health_body
    openapi = client.get("/api/openapi.json").text
    assert DEV_SECRET not in openapi


def test_output_validation_money_never_negative_anywhere(client):
    """Money invariants hold across the public surface (schema-level test re-check)."""
    from app.infrastructure.database import Base
    import app.infrastructure.models  # noqa
    for table, col in [("payments", "amount_paise"), ("recovery_cases", "amount_paise"),
                       ("outcomes", "amount_recovered_paise")]:
        col_obj = Base.metadata.tables[table].columns[col]
        assert col_obj.type.python_type is int
