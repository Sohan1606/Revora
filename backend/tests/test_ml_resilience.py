"""ML artifact resilience — a broken/incompatible model file must NEVER crash
decisions; the provider chain falls back to empirical/prior and poison-caches
so it doesn't retry the failed load on every request."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.infrastructure.models  # noqa: F401
from app.core.config import get_settings
from app.domain.payments.ingest import PaymentEventIn, ingest_payment_event
from app.domain.recovery.next_best_action import decide
from app.domain.recovery.service import analyze_failed_payment
from app.infrastructure.database import Base
from app.infrastructure.models import Merchant
from app.intelligence.recovery_score import ml_provider
from app.intelligence.recovery_score.features import default_features
from app.intelligence.recovery_score.ml_provider import estimate

M = uuid.uuid4()


def test_corrupt_artifact_falls_back(tmp_path):
    (tmp_path / "recovery_model.joblib").write_bytes(b"this is not a pickle " * 64)
    est = estimate("processor_issue", "retry", {}, default_features(), models_dir=tmp_path)
    assert not est.basis.startswith("model:")
    assert est.basis in ("cold_start_prior",) or est.basis.startswith("empirical")
    # poison-cached: a second call doesn't re-attempt the parse
    est2 = estimate("processor_issue", "retry", {}, default_features(), models_dir=tmp_path)
    assert est2.p_recovery == est.p_recovery


def test_malformed_bundle_structure_falls_back(tmp_path):
    import joblib
    joblib.dump({"not": "a bundle"}, tmp_path / "recovery_model.joblib")
    est = estimate("processor_issue", "wait", {}, default_features(), models_dir=tmp_path)
    assert not est.basis.startswith("model:")


def test_decide_survives_corrupt_artifact(tmp_path, monkeypatch):
    settings = get_settings()
    old = (settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
    settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET = "", ""
    monkeypatch.setattr(ml_provider, "MODELS_DIR", tmp_path)
    (tmp_path / "recovery_model.joblib").write_bytes(b"\x00garbage\x00")

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = S()
    db.add(Merchant(id=M, name="M"))
    db.commit()
    payment, _ = ingest_payment_event(db, PaymentEventIn(
        merchant_id=str(M), amount_paise=899900, status="failed", method="upi",
        failure_reason="insufficient funds", provider_payment_id="pay_res_1",
        occurred_at=datetime.now(timezone.utc),
        customer_email="c@e.com", is_synthetic=True))
    case = analyze_failed_payment(db, payment).detection.case

    result = decide(db, case)  # must NOT raise despite poisoned artifact
    assert case.state in ("action_selected", "awaiting_approval")
    assert result.decision.chosen_action  # a decision was still made
    db.close()
    engine.dispose()
    settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET = old
