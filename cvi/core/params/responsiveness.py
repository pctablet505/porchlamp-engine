"""Pillar 1 — Responsiveness parameter curves (02 section 4, parameters 1.1-1.5).

Each function takes the FeatureBundle input for its parameter
(``bundle.inputs[<input id>]``) and returns the calibrated ParamScore:
02 section 4 curve, D-MISS-1 unmeasured pass-through, minimum-n rule.
1.5 TTM_p90 is ECDF-normalised (D-NORM-3): the second argument is the
frozen-population mid-rank ECDF (D-NORM-1, 0-100 scale).
"""
from __future__ import annotations

import math
from typing import Callable, Tuple

from cvi.core.models import ParamScore, ParamValue
from cvi.core.params.curves import KIND_ECDF, KIND_NUMERIC, ParamDef, _apply, fill_rate_score

#: 1.1 TTFHR knee (02 section 4): full marks at 12 h or less of median
#: opening-to-first-substantive-response time.
TTFHR_KNEE_HOURS: float = 12.0
#: 1.1 TTFHR decay: points lost per doubling of the median past the knee.
TTFHR_SLOPE_PER_DOUBLING: float = 25.0
#: 1.4 GR_14: points lost per unit of ghost rate.
GHOST_RATE_SLOPE: float = 200.0


def _ttfhr_score(hours: float) -> float:
    if hours <= TTFHR_KNEE_HOURS:
        return 100.0
    return max(0.0, 100.0 - TTFHR_SLOPE_PER_DOUBLING * math.log2(hours / TTFHR_KNEE_HOURS))


def _ghost_rate_score(rate: float) -> float:
    return max(0.0, 100.0 - GHOST_RATE_SLOPE * rate)


def _ttm_p90_score(p90_hours: float, ecdf: Callable[[float], float]) -> float:
    return 100.0 - ecdf(p90_hours)


_TTFHR = ParamDef(
    input_id="ttfhr_hours",
    pillar="responsiveness",
    weight=0.30,
    kind=KIND_NUMERIC,
    score=_ttfhr_score,
    min_n=10,
    domain=(0.0, 256.0),
    higher_is_better=False,
)
_DR90 = ParamDef(
    input_id="dr90_decision_rate",
    pillar="responsiveness",
    weight=0.25,
    kind=KIND_NUMERIC,
    score=fill_rate_score,
    min_n=10,
    domain=(0.0, 1.0),
    higher_is_better=True,
)
_MR_FIRST = ParamDef(
    input_id="mr_first_rate",
    pillar="responsiveness",
    weight=0.20,
    kind=KIND_NUMERIC,
    score=fill_rate_score,
    min_n=5,
    domain=(0.0, 1.0),
    higher_is_better=True,
)
_GR14 = ParamDef(
    input_id="gr14_ghost_rate",
    pillar="responsiveness",
    weight=0.15,
    kind=KIND_NUMERIC,
    score=_ghost_rate_score,
    min_n=10,
    domain=(0.0, 1.0),
    higher_is_better=False,
)
_TTM_P90 = ParamDef(
    input_id="ttm_p90_hours",
    pillar="responsiveness",
    weight=0.10,
    kind=KIND_ECDF,
    score=_ttm_p90_score,
    min_n=5,
    domain=(0.0, 1000.0),
    higher_is_better=False,
)

#: This pillar's registered curves, in 02 section 4 order.
DEFS: Tuple[ParamDef, ...] = (_TTFHR, _DR90, _MR_FIRST, _GR14, _TTM_P90)


def ttfhr(value: ParamValue) -> ParamScore:
    """1.1 TTFHR — median hours to first substantive human response.

    100 at 12 h or less, then ``max(0, 100 - 25*log2(t/12))``; minimum
    sample 10 external PRs in the window. Takes the bundle input
    ``ttfhr_hours``."""
    return _apply(_TTFHR, value)


def dr90(value: ParamValue) -> ParamScore:
    """1.2 DR_90 — share of the decision cohort merged or closed within 90 d.

    Curve ``min(100, 125*r)``; minimum sample 10. Takes the bundle input
    ``dr90_decision_rate``."""
    return _apply(_DR90, value)


def mr_first(value: ParamValue) -> ParamScore:
    """1.3 MR_first — merged/decided among external first-time authors.

    Curve ``min(100, 125*r)``; minimum sample 5. Takes the bundle input
    ``mr_first_rate``."""
    return _apply(_MR_FIRST, value)


def gr14(value: ParamValue) -> ParamScore:
    """1.4 GR_14 — ghost rate of the 14-day window.

    Curve ``max(0, 100 - 200*g)``; minimum sample 10. Takes the bundle input
    ``gr14_ghost_rate``."""
    return _apply(_GR14, value)


def ttm_p90(value: ParamValue, ecdf: Callable[[float], float]) -> ParamScore:
    """1.5 TTM_p90 — nearest-rank p90 merge latency, ecosystem ECDF.

    Curve ``100 - ECDF_mid(p90)`` with the frozen-population mid-rank ECDF
    (D-NORM-1, 0-100 scale) as second argument; minimum sample 5 merged
    PRs. Takes the bundle input ``ttm_p90_hours``."""
    return _apply(_TTM_P90, value, ecdf=ecdf)
