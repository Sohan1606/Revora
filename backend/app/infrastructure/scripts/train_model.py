"""CLI: train the recovery-probability model from the corpus and save the artifact.

Run from backend/:
  python -m app.infrastructure.scripts.generate_corpus --n 3000 --seed 42
  python -m app.infrastructure.scripts.train_model --seed 42

Writes models/recovery_model.joblib + models/evaluation_report.json.
ALL metrics come from the 20% held-out split — never from training data.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
from sqlalchemy import select

from app.core.config import get_settings
from app.domain.recovery.case_features import build_case_features
from app.infrastructure.database import Base, SessionLocal, engine
import app.infrastructure.models  # noqa: F401
from app.infrastructure.models import (
    Decision, Outcome, RecoveryCase,
)
from app.intelligence.recovery_score import training
from app.intelligence.recovery_score.features import FEATURE_VERSION, FEATURES, vectorize
from app.intelligence.recovery_score.ml_provider import ARTIFACT_NAME

MODELS_DIR = Path(__file__).resolve().parents[3] / "models"


def build_examples(db) -> tuple[list["training.TrainingExample"], list[str]]:
    """One training example per (case, executed decision, outcome) — synthetic corpus only.
    Returns (examples, sorted unique actions seen) — the actions list gates serving:
    the provider refuses to predict actions the model wasn't trained on.
    Payment/customer features are resolved inside build_case_features (UUID-safe).
    """
    from app.infrastructure.models import FailureClassification

    rows = db.execute(
        select(RecoveryCase, Decision, Outcome, FailureClassification)
        .join(Decision, Decision.case_id == RecoveryCase.id)
        .join(Outcome, Outcome.case_id == RecoveryCase.id)
        .join(FailureClassification,
              FailureClassification.risk_event_id == RecoveryCase.risk_event_id)
        .where(RecoveryCase.is_synthetic.is_(True),
               Decision.status == "executed")
    ).unique().all()

    examples = []
    actions_seen: set[str] = set()
    for case, decision, outcome, classification in rows:
        features = build_case_features(db, case)
        label = 1 if outcome.outcome in ("recovered", "partial") else 0
        actions_seen.add(decision.chosen_action)
        examples.append(training.TrainingExample(
            vector=vectorize(features, cause=classification.primary_cause,
                             action=decision.chosen_action),
            label=label,
        ))
    return examples, sorted(actions_seen)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-examples", type=int, default=200)
    parser.add_argument("--xgb-depth", type=int, default=2)
    parser.add_argument("--xgb-n", type=int, default=500)
    parser.add_argument("--xgb-lr", type=float, default=0.05)
    args = parser.parse_args()

    settings = get_settings()
    if settings.ENV not in ("local", "staging"):
        print("REFUSED: training is for local/staging evaluation only.")
        return 1

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        examples, actions_seen = build_examples(db)
        if len(examples) < args.min_examples:
            print(f"REFUSED: only {len(examples)} examples (< {args.min_examples}). "
                  "Run generate_corpus first.")
            return 1

        print(f"Training on {len(examples)} labeled examples "
              f"(80/20 stratified split, seed={args.seed})…")
        result = training.train(
            examples, seed=args.seed,
            xgb_params={"n_estimators": args.xgb_n, "max_depth": args.xgb_depth,
                        "learning_rate": args.xgb_lr, "subsample": 0.8},
        )
        result.metadata["feature_version"] = FEATURE_VERSION
        result.metadata["feature_count"] = len(FEATURES)
        result.metadata["training_data"] = "synthetic corpus (is_synthetic=true, labeled)"
        result.metadata["actions"] = actions_seen  # serving gate: unseen actions fall back

        MODELS_DIR.mkdir(exist_ok=True)
        joblib.dump({"model": result.model, "metadata": result.metadata},
                    MODELS_DIR / ARTIFACT_NAME)
        report_path = MODELS_DIR / "evaluation_report.json"
        report_path.write_text(json.dumps(result.metadata, indent=2))

        base_m = result.metadata["baseline_logreg"]
        model_m = result.metadata["model_xgb_calibrated"]
        print("\n=== HELD-OUT EVALUATION (20% split — honest metrics) ===")
        print(f"{'metric':<20}{'logreg baseline':>17}{'xgb calibrated':>16}")
        for key in ("auc", "brier", "log_loss", "precision_at_0.5", "recall_at_0.5"):
            print(f"{key:<20}{base_m[key]:>17}{model_m[key]:>16}")
        print(f"{'n_test':<20}{base_m['n']:>17}{model_m['n']:>16}")
        print(f"\nArtifact: {MODELS_DIR / ARTIFACT_NAME}")
        print(f"Report:   {report_path}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
