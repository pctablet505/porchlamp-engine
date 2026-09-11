"""Pillar 2 — Production Criticality & Impact (weight 25%).

Three parameter scorers, each calibrated to [0, 100], combined with the
within-pillar weights from ``cvi.constants.PARAM_WEIGHTS["criticality"]``
(dependents 0.40, openssf_criticality 0.35, downloads 0.25):

2.1 Downstream Registry Dependents (weight 0.40, spec 2.1)
    Ecosystem ECDF percentile of ``log10(dependents_count + 1)``. The
    log10 compresses the heavy-tailed dependent counts before the
    within-ecosystem percentile mapping (spec 10.2).

2.2 Canonical OpenSSF Criticality Score C (weight 0.35, spec 2.2, 10.1)
    The Rob Pike multi-signal criticality engine scaled to [0, 100]:
    ``calculate_pike_criticality(signals) * 100.0``.

2.3 Filtered Active Monthly Downloads (weight 0.25, spec 2.3)
    Ecosystem ECDF percentile of ``log10(monthly_downloads + 1)``.

Edge cases (documented behaviour):
- ``dependents_count`` / ``monthly_downloads`` of 0 map to ``log10(1) = 0``
  and score the ECDF percentile of 0 in the population — the floor of the
  distribution, not automatically 0 points.
- Negative counts are guarded to 0 before the log (``log10(0)`` is a
  domain error); out-of-domain raw telemetry cannot crash the pillar.
- An empty population returns the neutral 50.0 fallback from
  ``cvi.normalize.compute_ecosystem_percentile`` (spec 10.2, step 1).
- ``meta.openssf_signals is None`` (telemetry not yet fetched) scores the
  neutral 50.0 for parameter 2.2 instead of 0.0: absence of data is not
  evidence of low criticality.
- The ECDF helper rounds percentiles to 2 decimals; the weighted pillar
  sum is clamped to [0, 100] but not rounded.
"""

from __future__ import annotations

import math
from typing import Iterable, Optional

from ..constants import PARAM_WEIGHTS
from ..models import OpenSSFSignals, RepoMeta
from ..normalize import compute_ecosystem_percentile
from ..openssf import calculate_pike_criticality

_PILLAR = "criticality"
_NEUTRAL: float = 50.0


def _clamp(value: float) -> float:
    """Clamp a score into the calibrated [0, 100] range."""
    return max(0.0, min(100.0, value))


def _log10_plus_one(count: int) -> float:
    """``log10(max(0, count) + 1)``; the +1 keeps count=0 finite (log10(1)=0)."""
    return math.log10(max(0, count) + 1)


def score_dependents(dependents_count: int, population: Iterable[float]) -> float:
    """2.1 — Downstream registry dependents, ECDF percentile in [0, 100]."""
    return compute_ecosystem_percentile(_log10_plus_one(dependents_count), population)


def score_openssf_criticality(signals: Optional[OpenSSFSignals]) -> float:
    """2.2 — Rob Pike criticality C in [0, 100]; ``None`` -> 50.0 neutral."""
    if signals is None:
        return _NEUTRAL
    return calculate_pike_criticality(signals) * 100.0


def score_downloads(monthly_downloads: int, population: Iterable[float]) -> float:
    """2.3 — Filtered active monthly downloads, ECDF percentile in [0, 100]."""
    return compute_ecosystem_percentile(_log10_plus_one(monthly_downloads), population)


def score_criticality(
    meta: RepoMeta,
    dependents_population: list[float],
    downloads_population: list[float],
) -> float:
    """Calibrated Pillar 2 score in [0, 100].

    ``0.40 * p_dependents + 0.35 * p_openssf + 0.25 * p_downloads`` with
    the weights read from ``PARAM_WEIGHTS["criticality"]``.
    """
    weights = PARAM_WEIGHTS[_PILLAR]
    p_dependents = score_dependents(meta.dependents_count, dependents_population)
    p_openssf = score_openssf_criticality(meta.openssf_signals)
    p_downloads = score_downloads(meta.monthly_downloads, downloads_population)
    score = (
        weights["dependents"] * p_dependents
        + weights["openssf_criticality"] * p_openssf
        + weights["downloads"] * p_downloads
    )
    return _clamp(score)
