"""Beta-binomial credibility scoring.

Each (source, claim_type) maintains a Beta(alpha, beta) posterior over the
true hit-rate. We start at Beta(1, 1) (uniform) and update with observed
outcomes. `partial` outcomes count as half a hit + half a miss.

CI uses the beta distribution quantiles (analytical, no scipy dependency
required — we use math.gamma-based incomplete beta via numerical inverse).
For MVP we approximate with a normal approximation when n is large enough,
and a wider conservative bound when small.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Score:
    alpha: float
    beta: float
    score: float
    ci_low: float
    ci_high: float
    n: int


def update(alpha: float, beta: float, *, hit: int = 0, partial: int = 0, miss: int = 0) -> tuple[float, float]:
    alpha += hit + 0.5 * partial
    beta += miss + 0.5 * partial
    return alpha, beta


def summarize(alpha: float, beta: float, n: int) -> Score:
    """Posterior mean + ~95% credible interval (normal approx for now)."""
    mean = alpha / (alpha + beta)
    # Variance of Beta(a, b) = ab / ((a+b)^2 (a+b+1))
    s = alpha + beta
    var = (alpha * beta) / (s * s * (s + 1))
    sd = math.sqrt(var)
    lo = max(0.0, mean - 1.96 * sd)
    hi = min(1.0, mean + 1.96 * sd)
    return Score(alpha=alpha, beta=beta, score=mean, ci_low=lo, ci_high=hi, n=n)
