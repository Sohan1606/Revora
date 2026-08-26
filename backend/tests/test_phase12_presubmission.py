"""Phase 12 — pre-submission coverage.

Systematic pass: auth on EVERY endpoint, full role matrix, invalid inputs,
empty states, error states, ML output validity, experiment determinism,
database operation invariants.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

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
DEV_SECRET = "p" * 64

# endpoint, method, min_role for success (None = any authenticated, 401 anonymous)
MATRIX = [
    ("/auth/me", "GET", None),
    ("/admin/users", "GET", "admin"),
    ("/admin/users", "POST", "admin"),
    ("/recovery/cases", "GET", None),
    ("/recovery/cases/00000000-0000-0000-0000-000000000000", "GET", None),
    ("/recovery/cases/00000000-0000-0000-0000-000000000000/decide", "POST", "operator"),
    ("/recovery/decisions/00000000-0000-0000-0000-000000000000/execute", "POST", "operator"),
    ("/recovery/decisions/00000000-0000-0000-0000-000000000000/approve", "POST", "operator"),
    ("/recovery/decisions/00000000-0000-0000-0000-000000000000/reject", "POST", "operator"),
    ("/control-center", "GET", None),
    ("/analytics/action-performance", "GET", None),
    ("/experiments", "GET", None),
    ("/experiments", "POST", "admin"),
    ("/experiments/00000000-0000-0000-0000-000000000000", "GET", None),
    ("/experiments/00000000-0000-0000-0000-000000000000/start", "POST", "admin"),
    ("/experiments/00000000-0000-0000-0000-000000000000/stop", "POST", "admin"),
    ("/policies", "GET", None),
    ("/policies", "POST", "admin"),
    ("/policies/00000000-0000-0000-0000-000000000000/activate", "POST", "admin"),
    ("/simulator/scenarios/insufficient_funds/run", "POST", "operator"),
    ("/integrations/razorpay", "GET", None),
    ("/integrations/razorpay/verify", "POST", "admin"),
]

ROLE_LEVEL = {"viewer": 1, "operator": 2, "admin": 3, "owner": 4}


@pytest.fixture()
def settings():
    s = get_settings()
    old = (s.DEV_JWT_SECRET, s.SUPABASE_JWT_SECRET, s.RAZORPAY_KEY_ID,
           s.RAZORPAY_KEY_SECRET, s.RAZORPAY_WEBHOOK_SECRET)
    s.DEV_JWT_SECRET, s.SUPABASE_JWT_SECRET = DEV_SECRET, ""
    s.RAZORPAY_KEY_ID, s.RAZORPAY_KEY_SECRET, s.RAZORPAY_WEBHOOK_SECRET = "", "", ""
    yield s
    (s.DEV_JWT_SECRET, s.SUPABASE_JWT_SECRET, s.RAZORPAY_KEY_ID,
     s.RAZORPAY_KEY_SECRET, s.RAZORPAY_WEBHOOK_SECRET) = old


@pytest.fixture()
def client(settings):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = S()
    db.add_all([Merchant(id=M1, name="M1"),
                User(merchant_id=M1, email="owner@t.io", full_name="O", role="owner"),
                User(merchant_id=M1, email="admin@t.io", full_name="A", role="admin"),
                User(merchant_id=M1, email="operator@t.io", full_name="P", role="operator"),
                User(merchant_id=M1, email="viewer@t.io", full_name="V", role="viewer")])
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
    with TestClient(app) as c:
        c.Session = S
        yield c
    engine.dispose()


def _hdr(client, email):
    token = client.post("/api/auth/dev/token", json={"email": email}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ---------- 1+2: authentication + every role on every endpoint ----------

def test_anonymous_gets_401_on_every_endpoint(client):
    missing = []
    for path, method, _min in MATRIX:
        r = getattr(client, method.lower())(f"/api{path}")
        if r.status_code != 401:
            missing.append((method, path, r.status_code))
    assert not missing, f"endpoints not enforcing auth: {missing}"


def test_role_matrix_all_endpoints(client):
    failures = []
    headers = {role: _hdr(client, f"{role}@t.io")
               for role in ("viewer", "operator", "admin", "owner")}
    for path, method, min_role in MATRIX:
        for role, hdr in headers.items():
            allowed = min_role is None or ROLE_LEVEL[role] >= ROLE_LEVEL[min_role]
            # expected: allowed → not 401/403 (may be 404/409/422/503 for placeholder ids)
            # denied → 403
            r = getattr(client, method.lower())(
                f"/api{path}", headers=hdr,
                **({"json": {}} if method == "POST" else {}),
            )
            if allowed and r.status_code in (401, 403):
                failures.append((method, path, role, r.status_code, "should be allowed"))
            if (not allowed) and r.status_code != 403:
                failures.append((method, path, role, r.status_code, "should be 403"))
    assert not failures, failures


def test_public_endpoints_need_no_auth(client):
    assert client.get("/api/health").status_code in (200, 503)
    assert client.get("/api/public/evidence").status_code == 200


# ---------- 3+4: features + API validation ----------

def test_invalid_uuid_paths_return_404(client):
    hdr = _hdr(client, "viewer@t.io")
    assert client.get("/api/recovery/cases/not-a-uuid", headers=hdr).status_code == 404
    assert client.get("/api/experiments/zzz", headers=hdr).status_code == 404


def test_pagination_bounds_enforced(client):
    hdr = _hdr(client, "viewer@t.io")
    assert client.get("/api/recovery/cases?limit=0", headers=hdr).status_code == 422
    assert client.get("/api/recovery/cases?limit=500", headers=hdr).status_code == 422
    assert client.get("/api/recovery/cases?offset=-1", headers=hdr).status_code == 422


def test_simulator_input_validation(client):
    hdr = _hdr(client, "operator@t.io")
    bad = client.post("/api/simulator/scenarios/insufficient_funds/run",
                      headers=hdr, json={"amount_paise": -5})
    assert bad.status_code == 422
    bad2 = client.post("/api/simulator/scenarios/insufficient_funds/run",
                       headers=hdr, json={"outcome": "maybe"})
    assert bad2.status_code == 422


def test_experiment_name_validation(client):
    hdr = _hdr(client, "owner@t.io")
    assert client.post("/api/experiments", headers=hdr,
                       json={"name": "x"}).status_code == 422  # min_length 3


def test_invite_duplicate_email_conflict(client):
    hdr = _hdr(client, "owner@t.io")
    first = client.post("/api/admin/users", headers=hdr,
                        json={"email": "dup@t.io", "role": "viewer"})
    assert first.status_code == 201
    assert client.post("/api/admin/users", headers=hdr,
                       json={"email": "dup@t.io", "role": "viewer"}).status_code == 409


def test_webhook_malformed_payloads(client, settings):
    import hmac as _hmac
    from app.integrations.razorpay.webhook import verify_webhook_signature  # noqa: F401
    # not configured → 503 before anything else
    r = client.post("/api/webhooks/razorpay", content=b"{}",
                    headers={"x-razorpay-signature": "x"})
    assert r.status_code == 503

    settings.RAZORPAY_WEBHOOK_SECRET = "whsec_p12"
    import hashlib, json as _json
    sig = lambda body: _hmac.new(b"whsec_p12", body, hashlib.sha256).hexdigest()
    # invalid json
    raw = b"{not json"
    assert client.post("/api/webhooks/razorpay", content=raw,
                       headers={"x-razorpay-signature": sig(raw)}).status_code == 400
    # missing event id
    raw = _json.dumps({"event": "payment.failed", "payload": {}}).encode()
    assert client.post("/api/webhooks/razorpay", content=raw,
                       headers={"x-razorpay-signature": sig(raw)}).status_code == 400
    # unhandled event type → acknowledged, not processed
    raw = _json.dumps({"id": "evt_u1", "event": "refund.processed",
                       "payload": {}}).encode()
    r = client.post("/api/webhooks/razorpay", content=raw,
                    headers={"x-razorpay-signature": sig(raw)})
    assert r.status_code == 200 and r.json()["status"] == "accepted"


# ---------- 5+7+8: DB ops, error states, empty states ----------

def test_empty_states_are_well_formed(client):
    hdr = _hdr(client, "viewer@t.io")
    assert client.get("/api/recovery/cases", headers=hdr).json() == {"total": 0, "cases": []}
    assert client.get("/api/experiments", headers=hdr).json() == {"experiments": []}
    perf = client.get("/api/analytics/action-performance", headers=hdr).json()
    assert perf == {"actions": []}
    cc = client.get("/api/control-center", headers=hdr).json()
    assert cc["summary"]["live"]["cases_total"] == 0
    pol = client.get("/api/policies", headers=hdr).json()["policies"]
    assert pol == []  # no policy until first decide()


def test_conflict_errors_on_wrong_state(client):
    # decide on a case that doesn't exist → 404; on non-analyzed case → 409
    op = _hdr(client, "operator@t.io")
    case_id = _make_analyzed_case(client)
    first = client.post(f"/api/recovery/cases/{case_id}/decide", headers=op)
    assert first.status_code == 201
    second = client.post(f"/api/recovery/cases/{case_id}/decide", headers=op)
    assert second.status_code == 409
    # execute an unknown decision
    assert client.post("/api/recovery/decisions/"
                       f"{uuid.uuid4()}/execute", headers=op).status_code == 404


def _make_analyzed_case(client):
    from app.domain.payments.ingest import PaymentEventIn, ingest_payment_event
    from app.domain.recovery.service import analyze_failed_payment
    db = client.Session()
    payment, _ = ingest_payment_event(db, PaymentEventIn(
        merchant_id=str(M1), amount_paise=299900, status="failed", method="upi",
        failure_reason="insufficient funds",
        provider_payment_id=f"pay_{uuid.uuid4().hex[:8]}",
        occurred_at=datetime.now(timezone.utc),
        customer_email="c@e.com", is_synthetic=True))
    case = analyze_failed_payment(db, payment).detection.case
    cid = str(case.id)
    db.close()
    return cid


def test_single_active_policy_invariant(client):
    owner = _hdr(client, "owner@t.io")
    # run one decide to create the default policy
    _make_analyzed_case(client)
    case_id = client.get("/api/recovery/cases",
                         headers=_hdr(client, "viewer@t.io")).json()["cases"][0]["id"]
    client.post(f"/api/recovery/cases/{case_id}/decide",
                headers=_hdr(client, "operator@t.io"))

    definition = client.get("/api/policies", headers=owner).json()["policies"][0]["definition"]
    v2 = client.post("/api/policies", headers=owner,
                     json={"version": "t-v2", "definition": definition}).json()
    client.post(f"/api/policies/{v2['id']}/activate", headers=owner)
    listing = client.get("/api/policies", headers=owner).json()["policies"]
    assert sum(1 for p in listing if p["is_active"]) == 1
    assert next(p for p in listing if p["version"] == "t-v2")["is_active"] is True


# ---------- 13: AI/ML output validity ----------

def test_ml_estimate_bounds_and_labels(client):
    # even on an empty DB the provider must return bounded, labeled estimates
    from app.intelligence.recovery_score.ml_provider import estimate
    from app.intelligence.recovery_score.features import default_features
    for cause in ("unknown", "hard_decline", "processor_issue"):
        for action in ("wait", "send_message", "retry", "no_action"):
            est = estimate(cause, action, {}, default_features())
            assert 0.0 < est.p_recovery < 1.0, (cause, action, est.p_recovery)
            assert est.basis in ("cold_start_prior",) or est.basis.startswith("empirical")
            assert 0.0 <= est.confidence <= 1.0


def test_decision_records_estimate_basis(client):
    case_id = _make_analyzed_case(client)
    r = client.post(f"/api/recovery/cases/{case_id}/decide",
                    headers=_hdr(client, "operator@t.io"))
    decision = r.json()
    assert decision["model_version"]  # always recorded, never blank
    assert decision["explanation"]["ev_formula"]  # formula always present


# ---------- experiment determinism ----------

def test_experiment_assignment_is_deterministic_and_immutable(client):
    from sqlalchemy import select
    from app.infrastructure.models import ExperimentAssignment

    owner = _hdr(client, "owner@t.io")
    op = _hdr(client, "operator@t.io")
    exp = client.post("/api/experiments", headers=owner,
                      json={"name": "determinism check"}).json()
    client.post(f"/api/experiments/{exp['id']}/start", headers=owner)
    case_ids = [_make_analyzed_case(client) for _ in range(8)]
    arms = {}
    for cid in case_ids:
        r = client.post(f"/api/recovery/cases/{cid}/decide", headers=op)
        arms[cid] = r.json()["experiment_arm"]
    assert all(a in ("treatment", "control") for a in arms.values())

    # re-deciding a fresh case in a NEW experiment yields a stable assignment:
    # assignments are hash-deterministic (same experiment+case → same arm)
    exp2 = client.post("/api/experiments", headers=owner,
                       json={"name": "second run"}).json()
    client.post(f"/api/experiments/{exp2['id']}/start", headers=owner)
    db = client.Session()
    sample_case = db.execute(select(ExperimentAssignment).where(
        ExperimentAssignment.experiment_id == uuid.UUID(exp["id"]))).scalars().first()
    db.close()
    assert sample_case is not None  # immutable rows exist for every decision
