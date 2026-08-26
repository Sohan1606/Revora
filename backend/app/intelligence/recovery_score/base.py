"""Intelligence: recovery-probability estimation.

Contract honored: pure functions — statistics in, estimates out. No DB access here.
The domain layer computes statistics from real stored outcomes and passes them in.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

# Documented cold-start prior. Explicit, versioned, conservative — never presented
# as an empirical result. Once >= MIN_SAMPLES historical outcomes exist for a
# cause/action pair, empirical base rates take over automatically.
COLD_START_PRIOR = 0.30
MIN_SAMPLES = 30
MODEL_VERSION = "empirical_v1"


@dataclass(frozen=True)
class ActionStat:
    attempts: int
    recovered: int


@dataclass(frozen=True)
class Estimate:
    p_recovery: float
    basis: str  # "empirical(n=..., k=...)" | "cold_start_prior"
    confidence: float


class ProbabilityProvider(Protocol):
    def estimate(self, cause: str, action: str, stats: Mapping[str, ActionStat]) -> Estimate: ...


def estimate_p_recovery(cause: str, action: str, stats: Mapping[str, ActionStat]) -> Estimate:
    stat = stats.get(action)
    if stat is None or stat.attempts < MIN_SAMPLES:
        return Estimate(COLD_START_PRIOR, "cold_start_prior", 0.25)
    rate = stat.recovered / stat.attempts
    rate = min(max(rate, 0.02), 0.97)
    n = stat.attempts
    confidence = min(0.50 + 0.40 * (n / (n + 60)), 0.90)  # grows with sample size
    return Estimate(rate, f"empirical(n={n}, k={stat.recovered})", round(confidence, 3))
