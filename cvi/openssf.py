"""Canonical OpenSSF (Rob Pike) criticality engine (spec 2.2, 10.1).

Computes the multi-signal criticality score C in [0.0, 1.0]:

    C = sum(alpha_i * S_i*) / sum(|alpha_i|)
    S_i* = log(1 + max(0, S_i)) / log(1 + max(S_i, T_i))

A negative weight (``updated_since``) yields an inverted penalty for
abandoned codebases. The result is clamped to [0, 1].
"""

from __future__ import annotations

import math
from typing import Optional

from .constants import OPENSSF_THRESHOLDS, OPENSSF_WEIGHTS
from .models import OpenSSFSignals


def _signals_to_raw(signals: OpenSSFSignals) -> dict[str, float]:
    return {
        "created_since": signals.created_since_months,
        "updated_since": signals.updated_since_months,
        "contributor_count": signals.contributor_count,
        "org_count": signals.org_count,
        "commit_frequency": signals.commit_frequency_weekly,
        "recent_releases_count": signals.recent_releases_count,
        "closed_issues_count": signals.closed_issues_count_90d,
        "updated_issues_count": signals.updated_issues_count_90d,
        "comment_frequency": signals.comment_frequency,
        "dependents_count": signals.dependents_count,
    }


def calculate_pike_criticality(
    signals: OpenSSFSignals,
    weights: Optional[dict[str, float]] = None,
    thresholds: Optional[dict[str, float]] = None,
) -> float:
    """Return the Rob Pike criticality score in [0.0, 1.0] (spec 10.1)."""
    weights = weights if weights is not None else OPENSSF_WEIGHTS
    thresholds = thresholds if thresholds is not None else OPENSSF_THRESHOLDS
    raw = _signals_to_raw(signals)

    denominator = sum(abs(a) for a in weights.values())
    if denominator == 0:
        return 0.0

    numerator = 0.0
    for param, alpha in weights.items():
        value = raw.get(param, 0.0)
        t = thresholds.get(param, 0.0)
        non_negative = max(0.0, value)
        saturation_denom = max(value, t)
        if saturation_denom <= 0:
            normalized = 0.0
        else:
            normalized = math.log(1.0 + non_negative) / math.log(1.0 + saturation_denom)
        numerator += alpha * normalized

    unbounded = numerator / denominator
    return max(0.0, min(1.0, unbounded))
