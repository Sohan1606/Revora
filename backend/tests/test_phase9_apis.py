"""Phase 9 tests — product APIs: RBAC, tenant isolation, decisions over HTTP,
webhooks (signature + idempotency), experiments, policies, simulator, analytics."""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.infrastructure.models  # noqa: F401
from app.core.config import get_settings
from app.domain.payments.ingest import PaymentEventIn, ingest_payment_event
from app.domain.recovery.service import analyze_failed_payment
from app.infrastructure.database import Base
from app.infrastructure.models import Merchant, User

M1, M2 = uuid.uuid4(), uuid.uuid4()
DEV_SECRET = "t" * 64
WEBHOOK_SECRET = "whsec_test_0123456789abcdef"


@pytest.fixture()
def settings():
    s = get_settings()
    old = (s.DEV_JWT_SECRET, s.SUPABASE_JWT_SECRET, s.RAZORPAY_KEY_ID,
           s.RAZORPAY_KEY_SECRET, s.RAZORPAY_WEBHOOK_SECRET, s.MESSAGING_MODE)
    s.DEV_JWT_SECRET, s.SUPABASE_JWT_SECRET = DEV_SECRET, ""
    s.RAZORPAY_KEY_ID, s.RAZORPAY_KEY_SECRET = "", ""
    s.RAZORPAY_WEBHOOK_SECRET = WEBHOOK_SECRET
    s.MESSAGING_MODE = "simulated"
    yield s
    (s.DEV_JWT_SECRET, s.SUPABASE_JWT_SECRET, s.RAZORPAY_KEY_ID,
     s.RAZORPAY_KEY_SECRET, s.RAZORPAY_WEBHOOK_SECRET, s.MESSAGING_MODE) = old


@pytest.fixture()
def client(settings):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = S()
    db.add_all([Merchant(id=M1, name="M1"), Merchant(id=M2, name="M2"),
                User(merchant_id=M1, email="owner@t.io", full_name="O", role="owner"),
                User(merchant_id=M1, email="op@t.io", full_name="P", role="operator"),
                User(merchant_id=M1, email="viewer@t.io", full_name="V", role="viewer"),
                User(merchant_id=M2, email="owner2@t.io", full_name="O2", role="owner")])
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
    app.dependency_overrides[get_session_factory] = lambda: S  # background tasks use test engine
    with TestClient(app) as c:
        c.engine = engine
        c.Session = S
        yield c
    engine.dispose()


def _hdr(client, email):
    token = client.post("/api/auth/dev/token", json={"email": email}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _seed_case(client, *, email="owner@t.io", reason="insufficient funds"):
    S = client.Session
    db = S()
    payment, _ = ingest_payment_event(db, PaymentEventIn(
        merchant_id=str(M1), amount_paise=899900, status="failed", method="upi",
        failure_reason=reason, provider_payment_id=f"pay_{uuid.uuid4().hex[:8]}",
        occurred_at=datetime.now(timezone.utc),
        customer_email="c@e.com", is_synthetic=True))
    case = analyze_failed_payment(db, payment).detection.case
    case_id = str(case.id)
    db.close()
    return case_id


# ---------- cases list/detail + isolation ----------

def test_cases_require_auth(client):
    assert client.get("/api/recovery/cases").status_code == 401


def test_cases_tenant_isolation(client):
    case_id = _seed_case(client)  # belongs to M1
    r = client.get("/api/recovery/cases", headers=_hdr(client, "owner2@t.io"))
    assert r.status_code == 200 and r.json()["total"] == 0
    r2 = client.get(f"/api/recovery/cases/{case_id}", headers=_hdr(client, "owner2@t.io"))
    assert r2.status_code == 404  # cross-tenant detail denied


def test_case_detail_shape(client):
    case_id = _seed_case(client)
    r = client.get(f"/api/recovery/cases/{case_id}", headers=_hdr(client, "viewer@t.io"))
    assert r.status_code == 200
    body = r.json()
    assert body["diagnosis"]["primary_cause"] == "insufficient_funds_temporary"
    assert body["is_synthetic"] is True
    assert body["audit"][0]["event_type"] == "risk_detected"


# ---------- decide/execute over HTTP ----------

def test_decide_rbac_and_flow(client):
    case_id = _seed_case(client)
    # viewer cannot decide
    r = client.post(f"/api/recovery/cases/{case_id}/decide", headers=_hdr(client, "viewer@t.io"))
    assert r.status_code == 403
    # operator can
    r = client.post(f"/api/recovery/cases/{case_id}/decide", headers=_hdr(client, "op@t.io"))
    assert r.status_code == 201
    body = r.json()
    assert body["chosen_action"] in ("wait", "send_message")
    decision_id = body["decision_id"]
    candidates = body["explanation"]["candidates"]
    assert len(candidates) >= 3  # populated evidence, not an empty table
    # executing requires operator
    r = client.post(f"/api/recovery/decisions/{decision_id}/execute", headers=_hdr(client, "op@t.io"))
    assert r.status_code == 200
    assert r.json()["status"] == "succeeded"
    # double execute is idempotent
    r2 = client.post(f"/api/recovery/decisions/{decision_id}/execute", headers=_hdr(client, "op@t.io"))
    assert r2.json()["execution_id"] == r.json()["execution_id"]
    # deciding again from a terminal-ish state fails cleanly
    r3 = client.post(f"/api/recovery/cases/{case_id}/decide", headers=_hdr(client, "op@t.io"))
    assert r3.status_code == 409


# ---------- experiments ----------

def test_experiment_lifecycle_and_incremental(client):
    owner = _hdr(client, "owner@t.io")
    op = _hdr(client, "op@t.io")
    r = client.post("/api/experiments", headers=owner,
                    json={"name": "NBA vs naive dunning", "hypothesis": "EV selection beats immediate dunning"})
    assert r.status_code == 201
    exp_id = r.json()["id"]
    # viewer cannot create
    assert client.post("/api/experiments", headers=_hdr(client, "viewer@t.io"),
                       json={"name": "nope"}).status_code == 403
    assert client.post(f"/api/experiments/{exp_id}/start", headers=owner).status_code == 200

    # run 10 cases through the running experiment via the decide endpoint
    arms = {"treatment": 0, "control": 0}
    for _ in range(10):
        case_id = _seed_case(client)
        r = client.post(f"/api/recovery/cases/{case_id}/decide", headers=op)
        assert r.status_code == 201
        arm = r.json()["experiment_arm"]
        assert arm in arms
        arms[arm] += 1

    results = client.get(f"/api/experiments/{exp_id}", headers=owner).json()
    assert results["total_assigned"] == 10
    assert results["treatment"]["n"] + results["control"]["n"] == 10
    assert "incremental_recovered_paise" in results
    client.post(f"/api/experiments/{exp_id}/stop", headers=owner)
    # after stop, decide runs without arms
    case_id = _seed_case(client)
    r = client.post(f"/api/recovery/cases/{case_id}/decide", headers=op)
    assert r.json()["experiment_arm"] is None


# ---------- policies ----------

def test_policy_create_validate_activate(client):
    owner = _hdr(client, "owner@t.io")
    bad = client.post("/api/policies", headers=owner,
                      json={"version": "bad", "definition": {"max_retries": 1}})
    assert bad.status_code == 422
    good = client.post("/api/policies", headers=owner, json={
        "version": "tight-v2",
        "definition": {
            "max_retries": 1, "max_contacts": 2, "min_actionable_amount_paise": 50000,
            "intervention_costs_paise": {"wait": 0, "retry": 0, "send_message": 25,
                                         "request_method_update": 100,
                                         "offer_alternative_method": 100, "escalate": 500,
                                         "no_action": 0},
            "friction_scores": {"wait": 0.0, "retry": 0.01, "send_message": 0.02,
                                "request_method_update": 0.05,
                                "offer_alternative_method": 0.10, "escalate": 0.0,
                                "no_action": 0.0},
            "require_approval_above_paise": {"offer_alternative_method": 1000000},
            "blocked_actions": [], "cause_blocks": {"hard_decline": ["retry"]},
        }})
    assert good.status_code == 201
    policy_id = good.json()["id"]
    # activating swaps the active version
    r = client.post(f"/api/policies/{policy_id}/activate", headers=owner)
    assert r.status_code == 200 and r.json()["is_active"] is True
    listing = client.get("/api/policies", headers=owner).json()["policies"]
    assert sum(1 for p in listing if p["is_active"]) == 1
    # new policy takes effect: ₹500 (<₹500 min) forces no_action
    case_id = _seed_case(client)
    S = client.Session
    db = S()
    from app.infrastructure.models import RecoveryCase
    db.get(RecoveryCase, uuid.UUID(case_id)).amount_paise = 40000
    db.commit()
    db.close()
    r = client.post(f"/api/recovery/cases/{case_id}/decide", headers=_hdr(client, "op@t.io"))
    assert r.json()["chosen_action"] == "no_action"


# ---------- webhooks ----------

def _signed(payload: dict) -> tuple[bytes, dict]:
    raw = json.dumps(payload).encode()
    sig = hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"x-razorpay-signature": sig, "Content-Type": "application/json"}


def test_webhook_rejects_bad_signature(client):
    payload = {"id": "evt_1", "event": "payment.failed", "payload": {}}
    raw, _ = _signed(payload)
    r = client.post("/api/webhooks/razorpay", content=raw,
                    headers={"x-razorpay-signature": "deadbeef",
                             "Content-Type": "application/json"})
    assert r.status_code == 400


def test_webhook_payment_failed_and_idempotent(client):
    # connect merchant integration so the processor can resolve the merchant
    S = client.Session
    db = S()
    from app.infrastructure.models import MerchantIntegration
    db.add(MerchantIntegration(merchant_id=M1, provider="razorpay", status="connected"))
    db.commit()
    db.close()

    payload = {"id": "evt_100", "event": "payment.failed",
               "payload": {"payment": {"entity": {
                   "id": "pay_WH_1", "amount": 299900, "currency": "INR",
                   "status": "failed", "method": "upi",
                   "error_code": None, "error_description": "insufficient funds",
                   "email": "wh@customer.io", "created_at": 1756000000}}}}
    raw, headers = _signed(payload)
    r = client.post("/api/webhooks/razorpay", content=raw, headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "accepted"
    # duplicate delivery → single logical event
    r2 = client.post("/api/webhooks/razorpay", content=raw, headers=headers)
    assert r2.json()["status"] == "duplicate"

    # background task ran synchronously in TestClient? verify state directly.
    db = client.Session()
    from app.infrastructure.models import RecoveryCase, WebhookEvent
    event = db.query(WebhookEvent).filter(WebhookEvent.event_id == "evt_100").one()
    assert event.signature_valid is True
    assert event.processing_status == "processed"
    cases = db.query(RecoveryCase).all()
    assert len(cases) == 1 and cases[0].state == "analyzed"
    assert cases[0].is_synthetic is False  # webhook data is LIVE, not synthetic
    db.close()


def test_webhook_captured_recovers_case(client):
    test_webhook_payment_failed_and_idempotent(client)
    payload = {"id": "evt_101", "event": "payment.captured",
               "payload": {"payment": {"entity": {"id": "pay_WH_1", "amount": 299900,
                                                  "status": "captured",
                                                  "created_at": 1756000100}}}}
    raw, headers = _signed(payload)
    r = client.post("/api/webhooks/razorpay", content=raw, headers=headers)
    assert r.json()["status"] == "accepted"
    db = client.Session()
    from app.infrastructure.models import Outcome, WebhookEvent
    assert db.query(WebhookEvent).filter(WebhookEvent.event_id == "evt_101").one().processing_status == "processed"
    outcome = db.query(Outcome).one()
    assert outcome.source == "webhook" and outcome.outcome == "recovered"
    assert outcome.amount_recovered_paise == 299900
    db.close()


# ---------- simulator + analytics ----------

def test_simulator_scenario_full_trace(client):
    r = client.post("/api/simulator/scenarios/expired_card/run",
                    headers=_hdr(client, "op@t.io"), json={"amount_paise": 499900})
    assert r.status_code == 201
    body = r.json()
    assert body["simulation"] is True
    assert body["cause"] == "method_expired"
    assert body["decision"]["action"] in ("request_method_update", "offer_alternative_method")
    assert body["execution"]["result"]["simulated"] is True
    assert body["outcome"]["source"] == "simulator"
    # candidate evidence must be POPULATED (regression: empty table after refactor)
    candidates = body["decision"]["explanation"]["candidates"]
    assert len(candidates) >= 3, f"candidates missing from simulator trace: {candidates}"
    fields = {"action", "p_recovery", "ev_paise", "cost_paise", "policy"}
    assert all(fields <= set(c) for c in candidates)
    assert any(c["policy"] != "allowed" for c in candidates)  # something IS blocked (rule name shown)


def test_simulator_unknown_scenario(client):
    r = client.post("/api/simulator/scenarios/nope/run",
                    headers=_hdr(client, "op@t.io"), json={})
    assert r.status_code == 404


def test_control_center_separates_synthetic_from_live(client):
    client.post("/api/simulator/scenarios/insufficient_funds/run",
                headers=_hdr(client, "op@t.io"), json={})
    r = client.get("/api/control-center", headers=_hdr(client, "viewer@t.io"))
    body = r.json()
    assert set(body["summary"].keys()) == {"live", "synthetic"}
    assert body["summary"]["synthetic"]["cases_total"] >= 1
    assert body["summary"]["live"]["cases_total"] == 0  # nothing live blended in


def test_action_performance(client):
    client.post("/api/simulator/scenarios/processor_issue/run",
                headers=_hdr(client, "op@t.io"), json={})
    r = client.get("/api/analytics/action-performance", headers=_hdr(client, "owner@t.io"))
    assert r.status_code == 200
    actions = {a["action"]: a for a in r.json()["actions"]}
    assert "wait" in actions or "send_message" in actions
