"""Pillar 2 — Criticality parameter curves (02 section 4, parameters 2.1-2.3).

Each function takes the FeatureBundle input for its parameter
(``bundle.inputs[<input id>]``) and returns the calibrated ParamScore.
2.1 Dependents and 2.3 Downloads are ECDF-normalised (D-NORM-3) over
``log10(1 + x)``: the second argument is the frozen-population mid-rank ECDF
(D-NORM-1, 0-100 scale). 2.2 OpenSSF criticality is the absolute linear
calibration ``100 * C``. None of the three has a minimum-n rule; a missing
signal or missing registry is an ``Unmeasured`` third-party gap.
"""
from __future__ import annotations

import math
from typing import Callable, Tuple

from cvi.core.models import ParamScore, ParamValue
from cvi.core.params.curves import KIND_ECDF, KIND_NUMERIC, ParamDef, _apply


def _log_population_score(value: float, ecdf: Callable[[float], float]) -> float:
    return ecdf(math.log10(1.0 + value))


def _pike_score(criticality: float) -> float:
    return 100.0 * criticality


_DEPENDENTS = ParamDef(
    input_id="dependents_deduped",
    pillar="criticality",
    weight=0.40,
    kind=KIND_ECDF,
    score=_log_population_score,
    domain=(0.0, 1e6),
    higher_is_better=True,
)
_OPENSF = ParamDef(
    input_id="openssf_criticality",
    pillar="criticality",
    weight=0.35,
    kind=KIND_NUMERIC,
    score=_pike_score,
    domain=(0.0, 1.0),
    higher_is_better=True,
)
_DOWNLOADS = ParamDef(
    input_id="downloads_monthly",
    pillar="criticality",
    weight=0.25,
    kind=KIND_ECDF,
    score=_log_population_score,
    domain=(0.0, 1e9),
    higher_is_better=True,
)

#: This pillar's registered curves, in 02 section 4 order.
DEFS: Tuple[ParamDef, ...] = (_DEPENDENTS, _OPENSF, _DOWNLOADS)


def dependents(value: ParamValue, ecdf: Callable[[float], float]) -> ParamScore:
    """2.1 Dependents — deduplicated dependent packages, registry ECDF.

    Curve ``ECDF_mid(log10(1 + d))`` with the frozen-population mid-rank ECDF
    (D-NORM-1, 0-100 scale) as second argument; no minimum sample. Takes the
    bundle input ``dependents_deduped``."""
    return _apply(_DEPENDENTS, value, ecdf=ecdf)


def openssf_criticality(value: ParamValue) -> ParamScore:
    """2.2 OpenSSF criticality — locally computed Pike score.

    Curve ``100 * C``; no minimum sample (all ten signals must be present or
    the input is Unmeasured). Takes the bundle input
    ``openssf_criticality``."""
    return _apply(_OPENSF, value)


def downloads(value: ParamValue, ecdf: Callable[[float], float]) -> ParamScore:
    """2.3 Downloads — monthly registry downloads, registry ECDF.

    Curve ``ECDF_mid(log10(1 + m))`` with the frozen-population mid-rank ECDF
    (D-NORM-1, 0-100 scale) as second argument; no minimum sample. Takes the
    bundle input ``downloads_monthly``."""
    return _apply(_DOWNLOADS, value, ecdf=ecdf)
