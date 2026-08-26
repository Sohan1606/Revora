"""ML provider — loads the trained model artifact and serves calibrated estimates.

Chain of estimate providers (first that applies wins):
  1. trained model artifact  (if present, loadable, feature_version matches, action seen in training)
  2. empirical base rates    (>= MIN_SAMPLES historical outcomes for cause×action)
  3. cold-start prior        (explicitly labeled, low confidence)

Resilience rule: an unusable artifact (wrong xgboost/joblib version, corrupted
file, malformed pickle) must NEVER crash decisions — it logs one warning,
poison-caches that mtime, and the chain falls back to empirical/prior.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import joblib

from app.intelligence.recovery_score.base import (
    ActionStat, Estimate, MIN_SAMPLES, estimate_p_recovery,
)
from app.intelligence.recovery_score.features import (
    FEATURE_VERSION, vectorize,
)

logger = logging.getLogger("revora.ml")

MODELS_DIR = Path(__file__).resolve().parents[3] / "models"
ARTIFACT_NAME = "recovery_model.joblib"

_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}  # path → (mtime, bundle-or-None)
# None = artifact known-unusable at this mtime (poison marker, avoids re-parsing)


def load_bundle(models_dir: Path | str | None = None) -> Any | None:
    """Return {'model', 'metadata'} bundle, or None when absent/unusable. Cached."""
    directory = Path(models_dir) if models_dir else MODELS_DIR
    path = directory / ARTIFACT_NAME
    if not path.exists():
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    key = str(path)
    with _lock:
        cached = _cache.get(key)
        if cached and cached[0] == mtime:
            return cached[1]
    try:
        bundle = joblib.load(path)
        if not isinstance(bundle, dict) or "model" not in bundle or "metadata" not in bundle:
            raise ValueError("malformed bundle structure")
    except Exception as exc:  # version drift, corruption — never crash decisions
        logger.warning(
            "model artifact unusable, falling back to empirical estimates",
            extra={"error": f"{type(exc).__name__}: {str(exc)[:160]}"},
        )
        bundle = None
    with _lock:
        _cache[key] = (mtime, bundle)
    return bundle


def _bundle_usable(bundle: Any, *, action: str, features: dict | None) -> bool:
    meta = bundle.get("metadata", {})
    if meta.get("feature_version") != FEATURE_VERSION:
        return False
    if action not in set(meta.get("actions", [])):
        return False  # never extrapolate to unseen actions
    return features is not None


def model_estimate(bundle: Any, *, cause: str, action: str, features: dict) -> Estimate | None:
    """Serve a model estimate; returns None (→ fallback) if prediction fails."""
    try:
        p = float(bundle["model"].predict_proba(
            [vectorize(features, cause=cause, action=action)])[0][1])
    except Exception as exc:
        logger.warning("model prediction failed, falling back",
                       extra={"error": f"{type(exc).__name__}: {str(exc)[:160]}"})
        return None
    p = min(max(p, 0.01), 0.97)
    meta = bundle["metadata"]
    # Confidence derived from held-out Brier score (lower error → higher confidence)
    brier = float(meta.get("metrics", {}).get("brier", 0.25))
    confidence = min(max(1.0 - 2.0 * brier, 0.4), 0.9)
    return Estimate(round(p, 4), f"model:{meta['version']}", round(confidence, 3))


def estimate(
    cause: str, action: str,
    stats: dict[str, ActionStat] | None,
    features: dict | None = None,
    models_dir: Path | str | None = None,
) -> Estimate:
    """The provider chain used by the NBA engine. Never raises for artifact issues."""
    bundle = load_bundle(models_dir)
    if bundle is not None and _bundle_usable(bundle, action=action, features=features):
        model_est = model_estimate(bundle, cause=cause, action=action, features=features)
        if model_est is not None:
            return model_est
    return estimate_p_recovery(cause, action, stats or {})
