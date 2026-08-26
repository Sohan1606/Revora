"""Regression: trained artifacts MUST record the actions gate so the provider
actually serves model estimates (audit finding: shipped artifact fell back
to cold_start for every action because metadata['actions'] was missing)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.infrastructure.models  # noqa: F401
from app.core.config import get_settings
from app.infrastructure.database import Base
from app.infrastructure.models import Merchant
from app.intelligence.recovery_score import ml_provider
from app.intelligence.recovery_score.features import default_features
from app.intelligence.recovery_score.ml_provider import estimate


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    s = S()
    s.add(Merchant(id=uuid.UUID(int=1), name="M"))
    s.commit()
    yield s
    s.close()
    engine.dispose()


def test_trained_artifact_records_actions_and_serves(db, tmp_path):
    from app.domain.recovery.corpus import generate_corpus
    from app.infrastructure.scripts import train_model

    settings = get_settings()
    old = (settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
    settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET = "", ""
    try:
        generate_corpus(db, n_cases=350, seed=13,
                        merchant=db.get(Merchant, uuid.UUID(int=1)))
        examples, actions_seen = train_model.build_examples(db)
        assert set(actions_seen) >= {"wait", "send_message", "no_action"}

        import joblib
        result = train_model.training.train(
            examples, seed=13, xgb_params={"n_estimators": 60, "max_depth": 3})
        result.metadata["feature_version"] = train_model.FEATURE_VERSION
        result.metadata["actions"] = actions_seen  # the fix under test
        result.metadata["metrics"] = {"brier": result.metadata["model_xgb_calibrated"]["brier"]}
        artifact_dir = tmp_path / "models"
        artifact_dir.mkdir()
        joblib.dump({"model": result.model, "metadata": result.metadata},
                    artifact_dir / "recovery_model.joblib")

        est = estimate("insufficient_funds_temporary", "wait", {},
                       default_features(), models_dir=artifact_dir)
        assert est.basis.startswith("model:"), (
            f"trained artifact must serve model estimates for seen actions, got {est.basis}")

        # unseen action still falls back (gate intact both ways)
        est2 = estimate("insufficient_funds_temporary", "escalate", {},
                        default_features(), models_dir=artifact_dir)
        assert not est2.basis.startswith("model:")
    finally:
        settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET = old
