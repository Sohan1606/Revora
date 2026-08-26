"""Model training — baseline (logistic regression) → calibrated XGBoost.

Takes labeled examples in, returns metrics + trainable artifacts. Pure-ish module:
no DB reads (callers build examples from rows). Held-out evaluation is mandatory
— no metric is ever reported on training data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    brier_score_loss, log_loss, precision_score, recall_score, roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline

MODEL_VERSION = "xgb_cal_v1"


@dataclass
class TrainingExample:
    vector: list[float]
    label: int  # 1 = recovered


@dataclass
class TrainingResult:
    model: object
    baseline: object
    metadata: dict = field(default_factory=dict)


def build_dataset(examples: list[TrainingExample], *, seed: int = 42):
    X = np.array([e.vector for e in examples], dtype=float)
    y = np.array([e.label for e in examples], dtype=int)
    return train_test_split(X, y, test_size=0.2, random_state=seed, stratify=y)


def evaluate(y_true: np.ndarray, p_hat: np.ndarray) -> dict:
    preds = (p_hat >= 0.5).astype(int)
    return {
        "auc": round(float(roc_auc_score(y_true, p_hat)), 4),
        "brier": round(float(brier_score_loss(y_true, p_hat)), 4),
        "log_loss": round(float(log_loss(y_true, p_hat, labels=[0, 1])), 4),
        "precision_at_0.5": round(float(precision_score(y_true, preds, zero_division=0)), 4),
        "recall_at_0.5": round(float(recall_score(y_true, preds, zero_division=0)), 4),
        "n": int(len(y_true)),
    }


def calibration_bins(y_true: np.ndarray, p_hat: np.ndarray, bins: int = 10) -> list[dict]:
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (p_hat >= lo) & (p_hat < hi) if i < bins - 1 else (p_hat >= lo) & (p_hat <= hi)
        if mask.sum() == 0:
            continue
        out.append({
            "bin": f"[{lo:.1f},{hi:.1f})",
            "n": int(mask.sum()),
            "predicted": round(float(p_hat[mask].mean()), 4),
            "actual": round(float(y_true[mask].mean()), 4),
        })
    return out


def train(examples: list[TrainingExample], *, seed: int = 42,
          xgb_params: dict | None = None) -> TrainingResult:
    X_tr, X_te, y_tr, y_te = build_dataset(examples, seed=seed)

    baseline = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=1.0))
    baseline.fit(X_tr, y_tr)
    base_metrics = evaluate(y_te, baseline.predict_proba(X_te)[:, 1])

    from xgboost import XGBClassifier  # lazy import keeps non-ML tests fast
    params = {"n_estimators": 250, "max_depth": 4, "learning_rate": 0.08,
              "subsample": 0.9, "colsample_bytree": 0.9, "eval_metric": "logloss",
              "random_state": seed, "n_jobs": 2}
    params.update(xgb_params or {})
    xgb = XGBClassifier(**params)
    calibrated = CalibratedClassifierCV(xgb, method="sigmoid", cv=3)
    calibrated.fit(X_tr, y_tr)
    model_metrics = evaluate(y_te, calibrated.predict_proba(X_te)[:, 1])

    metadata = {
        "version": MODEL_VERSION,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "feature_version": None,  # caller fills from features module
        "n_train": int(len(y_tr)), "n_test": int(len(y_te)),
        "baseline_logreg": base_metrics,
        "model_xgb_calibrated": model_metrics,
        "calibration_bins": calibration_bins(y_te, calibrated.predict_proba(X_te)[:, 1]),
        "seed": seed,
    }
    return TrainingResult(model=calibrated, baseline=baseline, metadata=metadata)
