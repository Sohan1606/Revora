"""Feature schema for recovery-probability models.

Pure module: vectorize(plain dict, action) → numeric vector. No DB, no IO.
The schema is VERSIONED — model artifacts record the feature_version they were
trained on; a mismatch invalidates the artifact (refuses to serve).
"""
from __future__ import annotations

import math
from typing import Any

FEATURE_VERSION = "v1"

CAUSES = [
    "insufficient_funds_temporary", "insufficient_funds_persistent", "auth_required",
    "method_expired", "hard_decline", "processor_issue", "customer_intent", "unknown",
]
ACTIONS = [
    "wait", "retry", "request_method_update", "offer_alternative_method",
    "send_message", "escalate", "no_action",
]
METHODS = ["upi", "card", "netbanking", "other"]

_NUMERIC_FEATURES = [
    "log_amount", "attempt_number", "retry_count", "contact_count", "is_vip",
    "prior_payment_count", "prior_failed_count", "hour_of_day", "day_of_month",
]

FEATURES = (
    _NUMERIC_FEATURES
    + [f"method_{m}" for m in METHODS]
    + [f"cause_{c}" for c in CAUSES]
    + [f"action_{a}" for a in ACTIONS]
)


def default_features() -> dict[str, Any]:
    """Neutral feature dict for contexts where a field is unavailable (never guessed)."""
    return {
        "log_amount": 0.0, "attempt_number": 1, "retry_count": 0, "contact_count": 0,
        "is_vip": 0, "prior_payment_count": 0, "prior_failed_count": 0,
        "hour_of_day": 12, "day_of_month": 15, "method": "other",
    }


def vectorize(features: dict[str, Any], *, cause: str, action: str) -> list[float]:
    """Build the model input vector. Order is frozen by FEATURES (do not reorder)."""
    f = {**default_features(), **(features or {})}
    vec: list[float] = [
        float(f.get("log_amount") or 0.0),
        float(f.get("attempt_number") or 1),
        float(f.get("retry_count") or 0),
        float(f.get("contact_count") or 0),
        float(f.get("is_vip") or 0),
        float(f.get("prior_payment_count") or 0),
        float(f.get("prior_failed_count") or 0),
        float(f.get("hour_of_day") if f.get("hour_of_day") is not None else 12),
        float(f.get("day_of_month") if f.get("day_of_month") is not None else 15),
    ]
    method = str(f.get("method") or "other")
    vec += [1.0 if method == m else 0.0 for m in METHODS]
    vec += [1.0 if cause == c else 0.0 for c in CAUSES]
    vec += [1.0 if action == a else 0.0 for a in ACTIONS]
    assert len(vec) == len(FEATURES), "feature vector/schema mismatch"
    return vec


def log_amount_paise(amount_paise: int) -> float:
    return round(math.log10(max(amount_paise, 1)), 4)
