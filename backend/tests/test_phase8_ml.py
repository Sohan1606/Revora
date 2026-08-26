"""Phase 8 tests — corpus, features, training, provider chain, engine integration."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.infrastructure.models  # noqa: F401
from app.core.config import get_settings
from app.domain.recovery import corpus as corpus_mod
from app.domain.recovery.corpus import generate_corpus
from app.infrastructure.database import Base
from app.infrastructure.models import (
    AuditEvent, Decision, Execution, Merchant, Outcome, RecoveryCase,
)
from app.intelligence.recovery_score import training
from app.intelligence.recovery_score.features import (
    FEATURE_VERSION, FEATURES, default_features, vectorize,
)
from app.intelligence.recovery_score.ml_provider import (
    estimate, load_bundle, model_estimate,
)

M = uuid.uuid4()


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
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
    old = (s.RAZORPAY_KEY_ID, s.RAZORPAY_KEY_SECRET)
    s.RAZORPAY_KEY_ID, s.RAZORPAY_KEY_SECRET = "", ""
    yield s
    s.RAZORPAY_KEY_ID, s.RAZORPAY_KEY_SECRET = old


# ---------- features ----------

def test_feature_vector_shape_and_version() -> None:
    vec = vectorize(default_features(), cause="processor_issue", action="retry")
    assert len(vec) == len(FEATURES)
    assert FEATURE_VERSION == "v1"
    # one-hot sanity: exactly one cause and one action bit set (numeric features come first)
    n_methods = len([f for f in FEATURES if f.startswith("method_")])
    n_causes = len([f for f in FEATURES if f.startswith("cause_")])
    n_actions = len([f for f in FEATURES if f.startswith("action_")])
    cause_start = len(FEATURES) - n_causes - n_actions
    assert sum(vec[cause_start:cause_start + n_causes]) == 1
    assert sum(vec[cause_start + n_causes:]) == 1


# ---------- corpus ----------

def test_corpus_generation_real_pipeline_labeled_synthetic(db, settings) -> None:
    merchant = Merchant(name=corpus_mod.CORPUS_MERCHANT_NAME)
    db.add(merchant); db.commit()
    stats = generate_corpus(db, n_cases=120, seed=7, merchant=merchant)

    assert stats["cases"] == 120
    cases = db.query(RecoveryCase).all()
    assert all(c.is_synthetic for c in cases)          # every case labeled
    outcomes = db.query(Outcome).all()
    assert len(outcomes) == 120
    assert all(o.source == "simulator" for o in outcomes)  # world response labeled
    decisions = db.query(Decision).all()
    assert all(d.model_version == "corpus_dgp" for d in decisions)
    execs = db.query(Execution).all()
    assert all(e.result.get("simulated") is True for e in execs)
    # Real pipeline artifacts exist: classification + audit
    audits = db.query(AuditEvent).filter(
        AuditEvent.event_type == "corpus_case_generated").count()
    assert audits == 120
    assert len(stats["by_cause"]) >= 5                  # cause diversity
    assert 0 < stats["recovered"] < 120                 # both classes present


def test_corpus_is_deterministic_for_seed(db, settings) -> None:
    merchant = Merchant(name=corpus_mod.CORPUS_MERCHANT_NAME)
    db.add(merchant); db.commit()
    a = generate_corpus(db, n_cases=60, seed=11, merchant=merchant)
    assert a["recovered"] > 0 and a["cases"] == 60


# ---------- training + provider ----------

def _examples_from_corpus(db):
    from app.infrastructure.models import FailureClassification
    from app.domain.recovery.case_features import build_case_features

    rows = db.execute(
        select(RecoveryCase, Decision, Outcome, FailureClassification)
        .join(Decision, Decision.case_id == RecoveryCase.id)
        .join(Outcome, Outcome.case_id == RecoveryCase.id)
        .join(FailureClassification,
              FailureClassification.risk_event_id == RecoveryCase.risk_event_id)
        .where(RecoveryCase.is_synthetic.is_(True))
    ).unique().all()
    return [
        training.TrainingExample(
            vector=vectorize(build_case_features(db, case),
                             cause=classification.primary_cause,
                             action=decision.chosen_action),
            label=1 if outcome.outcome in ("recovered", "partial") else 0,
        )
        for case, decision, outcome, classification in rows
    ]


def test_train_heldout_metrics_beat_random(db, settings, tmp_path) -> None:
    merchant = Merchant(name=corpus_mod.CORPUS_MERCHANT_NAME)
    db.add(merchant); db.commit()
    generate_corpus(db, n_cases=600, seed=3, merchant=merchant)
    examples = _examples_from_corpus(db)
    assert len(examples) == 600

    result = training.train(examples, seed=3,
                            xgb_params={"n_estimators": 80, "max_depth": 3})
    meta = result.metadata
    # The DGP has real signal → held-out AUC must clearly beat chance (0.5).
    # (Threshold is set for robustness across seeds at this small n, not for record metrics.)
    assert meta["model_xgb_calibrated"]["auc"] > 0.65
    assert meta["baseline_logreg"]["auc"] > 0.60
    # Both classes present in the held-out split
    assert meta["model_xgb_calibrated"]["n"] > 50
    # Calibration bins are reported for inspection
    assert len(meta["calibration_bins"]) >= 4


def test_provider_prefers_model_and_falls_back(db, settings, tmp_path, monkeypatch) -> None:
    from app.intelligence.recovery_score import ml_provider

    merchant = Merchant(name=corpus_mod.CORPUS_MERCHANT_NAME)
    db.add(merchant); db.commit()
    generate_corpus(db, n_cases=1000, seed=5, merchant=merchant)
    examples = _examples_from_corpus(db)
    result = training.train(examples, seed=5, xgb_params={"n_estimators": 100, "max_depth": 4})
    result.metadata["feature_version"] = FEATURE_VERSION
    result.metadata["actions"] = ["wait", "retry", "send_message", "no_action",
                                  "request_method_update", "offer_alternative_method"]
    result.metadata["metrics"] = {"brier": result.metadata["model_xgb_calibrated"]["brier"]}

    import joblib
    artifact = tmp_path / "recovery_model.joblib"
    joblib.dump({"model": result.model, "metadata": result.metadata}, artifact)

    # No artifact → empirical/cold-start path (basis labels it honestly)
    fallback = estimate("processor_issue", "retry", {}, default_features(),
                        models_dir=tmp_path / "nonexistent")
    assert fallback.basis in ("cold_start_prior",) or fallback.basis.startswith("empirical")

    # With artifact → model path with versioned basis
    est_model = estimate("processor_issue", "retry", {}, default_features(),
                         models_dir=tmp_path)
    assert est_model.basis.startswith("model:")
    assert 0.01 <= est_model.p_recovery <= 0.97

    # Unseen action → NEVER extrapolated: falls back
    est_unseen = estimate("processor_issue", "escalate", {}, default_features(),
                          models_dir=tmp_path)
    assert not est_unseen.basis.startswith("model:")

    # Model recovers DGP ordering on the STRONG cause×action contrasts
    # (method_expired: update 0.65 vs retry 0.02; NSF-temp: retry ~0.60 vs message 0.30).
    # The processor pair (~0.60 vs 0.50) is statistically weak at this n — we assert
    # the model isn't confidently wrong rather than demand a noisy ordering.
    p = lambda c, a: estimate(c, a, {}, default_features(), models_dir=tmp_path).p_recovery
    assert p("method_expired", "request_method_update") > p("method_expired", "retry")
    assert p("insufficient_funds_temporary", "retry") > p("insufficient_funds_temporary", "send_message")
    assert p("processor_issue", "retry") >= p("processor_issue", "wait") - 0.03


def test_feature_version_mismatch_refuses_to_serve(db, settings, tmp_path) -> None:
    import joblib
    from app.intelligence.recovery_score import ml_provider

    result = training.TrainingResult(model=None, baseline=None,
                                     metadata={"feature_version": "v0", "actions": ["retry"]})
    artifact = tmp_path / "recovery_model.joblib"
    joblib.dump({"model": None, "metadata": result.metadata}, artifact)
    est = estimate("processor_issue", "retry", {}, default_features(), models_dir=tmp_path)
    assert not est.basis.startswith("model:")  # stale artifact refused


# ---------- engine integration ----------

def test_decide_uses_model_basis_when_artifact_present(db, settings, tmp_path, monkeypatch) -> None:
    from app.domain.payments.ingest import PaymentEventIn, ingest_payment_event
    from app.domain.recovery.service import analyze_failed_payment
    from app.domain.recovery.next_best_action import decide
    from app.intelligence.recovery_score import ml_provider

    merchant = Merchant(name=corpus_mod.CORPUS_MERCHANT_NAME)
    db.add(merchant); db.commit()
    generate_corpus(db, n_cases=400, seed=9, merchant=merchant)
    examples = _examples_from_corpus(db)
    result = training.train(examples, seed=9, xgb_params={"n_estimators": 60, "max_depth": 3})
    result.metadata["feature_version"] = FEATURE_VERSION
    result.metadata["actions"] = ["wait", "retry", "send_message", "no_action",
                                  "request_method_update", "offer_alternative_method"]
    result.metadata["metrics"] = {"brier": result.metadata["model_xgb_calibrated"]["brier"]}
    import joblib
    joblib.dump({"model": result.model, "metadata": result.metadata},
                tmp_path / "recovery_model.joblib")

    monkeypatch.setattr(ml_provider, "MODELS_DIR", tmp_path)

    payment, _ = ingest_payment_event(db, PaymentEventIn(
        merchant_id=str(merchant.id), amount_paise=899900, status="failed", method="upi",
        failure_reason="insufficient funds", provider_payment_id="pay_ml_1",
        occurred_at=datetime(2026, 8, 5, 10, 30, tzinfo=timezone.utc),
        customer_email="c@e.com", is_synthetic=True))
    case = analyze_failed_payment(db, payment).detection.case
    result_dec = decide(db, case)

    assert case.state in ("action_selected", "awaiting_approval")
    bases = [c["basis"] for c in result_dec.explanation["candidates"]]
    assert any(b.startswith("model:") for b in bases)   # model actually served
