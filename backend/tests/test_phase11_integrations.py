"""Phase 11 tests — Razorpay client (real HTTP contract via injected transport),
executor real-retry paths, config guards, Supabase JIT provisioning, invites,
public evidence endpoint. No real network, no credentials, no real money."""
from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.infrastructure.models  # noqa: F401
from app.core.config import get_settings
from app.domain.payments.ingest import PaymentEventIn, ingest_payment_event
from app.domain.recovery.executor import execute_decision
from app.domain.recovery.next_best_action import decide
from app.domain.recovery.service import analyze_failed_payment
from app.infrastructure.database import Base
from app.infrastructure.models import Merchant, User

M = uuid.uuid4()
DEV_SECRET = "d" * 64
SUPA_SECRET = "s" * 64


# ---------- Razorpay client contract ----------

class Recorder(httpx.BaseTransport):
    def __init__(self, responses: list[httpx.Response]):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)


def _client(transport) -> "RazorpayClient":
    from app.integrations.razorpay.client import RazorpayClient
    return RazorpayClient("rzp_test_ABC123", "secret123", transport=transport)


def test_client_not_configured_message_is_exact():
    from app.integrations.razorpay.client import (
        RazorpayClient, RazorpayNotConfiguredError,
    )
    with pytest.raises(RazorpayNotConfiguredError) as exc:
        RazorpayClient.from_settings(get_settings())  # env empty in tests
    assert "Razorpay integration not configured — add Razorpay Test Mode credentials to .env." == str(exc.value)


def test_client_refuses_live_keys():
    from app.integrations.razorpay.client import RazorpayClient, RazorpayLiveModeError

    class FakeSettings:
        RAZORPAY_KEY_ID = "rzp_live_DANGER"
        RAZORPAY_KEY_SECRET = "secret"
        RAZORPAY_BASE_URL = ""
    with pytest.raises(RazorpayLiveModeError):
        RazorpayClient.from_settings(FakeSettings())


def test_client_create_order_contract():
    rec = Recorder([httpx.Response(200, json={"id": "order_TEST123", "amount": 899900,
                                              "currency": "INR", "status": "created"})])
    client = _client(rec)
    order = client.create_order(amount_paise=899900, receipt="revora-retry-x",
                                notes={"case_id": "c1"})
    assert order["id"] == "order_TEST123"
    request = rec.requests[0]
    assert request.method == "POST" and str(request.url).endswith("/orders")
    # Basic auth from key_id:key_secret — never in query or body
    expected = base64.b64encode(b"rzp_test_ABC123:secret123").decode()
    assert request.headers["Authorization"] == f"Basic {expected}"
    body = json.loads(request.content)
    assert body == {"amount": 899900, "currency": "INR",
                    "receipt": "revora-retry-x", "notes": {"case_id": "c1"}}
    assert "secret" not in str(request.url)


def test_client_maps_api_errors_and_transport_failures():
    from app.integrations.razorpay.client import RazorpayAPIError

    rec = Recorder([httpx.Response(401, json={"error": {"description": "Invalid API keys"}})])
    with pytest.raises(RazorpayAPIError) as exc:
        _client(rec).ping()
    assert exc.value.status == 401 and "Invalid API keys" in exc.value.detail

    class Boom(httpx.BaseTransport):
        def handle_request(self, request):
            raise httpx.ConnectError("no network")

    with pytest.raises(RazorpayAPIError) as exc2:
        _client(Boom()).ping()
    assert exc2.value.status == 0 and "transport failure" in exc2.value.detail


def test_client_no_money_movement_surface():
    """The client must not even implement capture/refund-style money endpoints."""
    from app.integrations.razorpay.client import RazorpayClient
    for forbidden in ("capture", "refund", "transfer"):
        assert not hasattr(RazorpayClient, forbidden)


# ---------- executor real-retry paths ----------

@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    s = S()
    s.add(Merchant(id=M, name="M"))
    s.commit()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture()
def settings():
    s = get_settings()
    old = (s.RAZORPAY_KEY_ID, s.RAZORPAY_KEY_SECRET, s.MESSAGING_MODE,
           s.SUPABASE_JWT_SECRET, s.DEV_JWT_SECRET)
    s.SUPABASE_JWT_SECRET = ""  # start every test in clean dev mode
    yield s
    (s.RAZORPAY_KEY_ID, s.RAZORPAY_KEY_SECRET, s.MESSAGING_MODE,
     s.SUPABASE_JWT_SECRET, s.DEV_JWT_SECRET) = old
    from app.integrations.razorpay.client import reset_client_cache
    reset_client_cache()


def _retry_case(db, monkeypatch=None):
    """Insufficient-funds case with seeded retry history → engine picks retry."""
    from tests.test_phase7_logic import _seed_history
    _seed_history(db, cause="insufficient_funds_temporary", action="retry", recovered=38)
    payment, _ = ingest_payment_event(db, PaymentEventIn(
        merchant_id=str(M), amount_paise=899900, status="failed", method="upi",
        failure_reason="insufficient funds", provider_payment_id=f"pay_{uuid.uuid4().hex[:8]}",
        occurred_at=datetime.now(timezone.utc), customer_email="c@e.com", is_synthetic=True))
    case = analyze_failed_payment(db, payment).detection.case
    return case


def test_executor_retry_fails_closed_when_unconfigured(db, settings, monkeypatch):
    """Policy blocks 'retry' from being CHOSEN when unconfigured; the executor
    branch is defense-in-depth. Craft a retry decision directly to prove the
    executor still refuses to fake a payment."""
    from app.integrations.razorpay.client import reset_client_cache
    from app.infrastructure.models import Decision

    settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET = "", ""
    reset_client_cache()

    # sanity: with keys unset, NBA policy blocks the retry candidate outright
    case = _retry_case(db)
    result = decide(db, case)
    assert result.chosen_action != "retry"
    retry_cand = next(c for c in result.decision.candidates if c.action_type == "retry")
    assert retry_cand.allowed_by_policy is False

    # force a retry decision (simulating a policy misconfiguration) — executor must fail closed
    forced = Decision(
        case_id=case.id, chosen_action="retry", action_params={},
        expected_recovery_paise=0, confidence=0, explanation={},
        status="approved_by_policy", decided_at=datetime.now(timezone.utc),
    )
    db.add(forced)
    db.commit()
    execution = execute_decision(db, forced)
    assert execution.status == "failed"
    assert "razorpay_not_configured" in execution.error
    assert "add Razorpay Test Mode credentials to .env" in execution.error
    assert execution.result["real_action"] is True and execution.result["executed"] is False


def test_executor_retry_real_order_success(db, settings, monkeypatch):
    from app.integrations.razorpay import client as rp_module
    settings.RAZORPAY_KEY_ID = "rzp_test_FAKE"
    settings.RAZORPAY_KEY_SECRET = "fake"  # local-only; requests go to injected transport
    rp_module.reset_client_cache()

    recorder = Recorder([httpx.Response(200, json={
        "id": "order_TEST999", "amount": 899900, "currency": "INR", "status": "created"})])
    monkeypatch.setattr(rp_module, "get_client",
                        lambda: rp_module.RazorpayClient(
                            "rzp_test_FAKE", "fake", transport=recorder))

    case = _retry_case(db)
    result = decide(db, case)
    execution = execute_decision(db, result.decision)
    assert execution.status == "succeeded"
    assert execution.result["razorpay_order_id"] == "order_TEST999"
    assert execution.result["mode"] == "test"
    assert execution.result["real_action"] is True
    assert case.retry_count == 1 and case.state == "observing"
    sent = json.loads(recorder.requests[0].content)
    assert sent["amount"] == 899900 and sent["notes"]["source"] == "revora_retry"


def test_executor_retry_api_failure_returns_case_to_analysis(db, settings, monkeypatch):
    from app.integrations.razorpay import client as rp_module
    settings.RAZORPAY_KEY_ID = "rzp_test_FAKE"
    settings.RAZORPAY_KEY_SECRET = "fake"
    rp_module.reset_client_cache()
    recorder = Recorder([httpx.Response(502, json={"error": {"description": "Bad gateway"}})])
    monkeypatch.setattr(rp_module, "get_client",
                        lambda: rp_module.RazorpayClient(
                            "rzp_test_FAKE", "fake", transport=recorder))

    case = _retry_case(db)
    result = decide(db, case)
    execution = execute_decision(db, result.decision)
    assert execution.status == "failed"
    assert "razorpay_api_error" in execution.error
    assert case.state == "analyzed"  # retryable failure → re-analyzable, not dead
    assert case.retry_count == 0     # budget only consumed on success


# ---------- integration status endpoints + supabase provisioning ----------

@pytest.fixture()
def client(settings):
    settings.DEV_JWT_SECRET = DEV_SECRET
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = S()
    db.add_all([Merchant(id=M, name="M"),
                User(merchant_id=M, email="owner@t.io", full_name="O", role="owner")])
    db.commit()
    db.close()

    from app.infrastructure.database import get_db
    from app.main import create_app

    def _override():
        session = S()
        try:
            yield session
        finally:
            session.close()

    app = create_app()
    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        c.Session = S
        yield c
    engine.dispose()


def _hdr(client, email="owner@t.io"):
    token = client.post("/api/auth/dev/token", json={"email": email}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_razorpay_status_reports_unconfigured_clearly(client, settings):
    settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET = "", ""
    r = client.get("/api/integrations/razorpay", headers=_hdr(client))
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["message"] == ("Razorpay integration not configured — add Razorpay "
                               "Test Mode credentials to .env.")
    # verify endpoint fails closed with the same message
    r2 = client.post("/api/integrations/razorpay/verify", headers=_hdr(client))
    assert r2.status_code == 503
    assert "not configured" in r2.json()["detail"]


def test_public_evidence_labels_synthetic(client):
    r = client.get("/api/public/evidence")
    body = r.json()
    assert body["data_label"].startswith("synthetic evaluation corpus")
    assert body["has_data"] is False and body["metrics"] is None  # empty DB: says so honestly


def test_supabase_jit_provisioning(client, settings):
    settings.SUPABASE_JWT_SECRET = SUPA_SECRET
    now = datetime.now(timezone.utc)
    token = pyjwt.encode({"sub": "sup-jit-1", "aud": "authenticated",
                          "email": "new.user@example.in",
                          "exp": int((now + timedelta(hours=1)).timestamp())},
                         SUPA_SECRET, algorithm="HS256")
    # First sign-in → JIT: own merchant, owner role
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["role"] == "owner" and r.json()["email"] == "new.user@example.in"
    merchant_id = r.json()["merchant_id"]

    # Second sign-in → same user, no duplicate merchant
    r2 = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r2.json()["id"] == r.json()["id"] and r2.json()["merchant_id"] == merchant_id


def test_supabase_links_invited_user_by_email(client, settings):
    # owner invites an operator by email (dev-mode action)
    inv = client.post("/api/admin/users", headers=_hdr(client),
                      json={"email": "teammate@t.io", "full_name": "Teammate",
                            "role": "operator"})
    assert inv.status_code == 201

    settings.SUPABASE_JWT_SECRET = SUPA_SECRET
    now = datetime.now(timezone.utc)
    token = pyjwt.encode({"sub": "sup-link-9", "aud": "authenticated",
                          "email": "teammate@t.io",
                          "exp": int((now + timedelta(hours=1)).timestamp())},
                         SUPA_SECRET, algorithm="HS256")
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "operator"  # kept the invited role, joined owner's merchant
    assert body["merchant_id"] != ""


def test_invite_cannot_escalate_role(client):
    admin_hdr = _hdr(client)  # owner actually; role escalation guard check with admin= same
    r = client.post("/api/admin/users", headers=admin_hdr,
                    json={"email": "x@t.io", "role": "owner"})
    assert r.status_code == 201  # owner may grant owner
    db = client.Session()
    from app.infrastructure.models import User as U
    me = db.query(U).filter(U.email == "owner@t.io").one()
    me.role = "admin"
    db.commit()
    db.close()
    r2 = client.post("/api/admin/users", headers=_hdr(client),
                     json={"email": "y@t.io", "role": "owner"})
    assert r2.status_code == 403  # admin cannot grant owner
