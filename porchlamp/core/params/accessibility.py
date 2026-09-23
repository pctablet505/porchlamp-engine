"""Pillar 5 — Accessibility parameter curves (02 section 4, parameters 5.1-5.4).

Each function takes the FeatureBundle input for its parameter
(``bundle.inputs[<input id>]``) and returns the calibrated ParamScore.
5.1 Newcomer-issue supply is the capped count curve; 5.2 Newcomer
conversion and 5.3 RR_90 are the cohort rate curves with a 5-author minimum;
5.4 DX is the identity on the 0-100 composite (0.5 runtime + 0.5 setup,
blended by the deriver) with a 5-run minimum.
"""
from __future__ import annotations

from typing import Tuple

from porchlamp.core import constants
from porchlamp.core.models import ParamScore, ParamValue
from porchlamp.core.params.curves import (
    KIND_NUMERIC,
    ParamDef,
    _apply,
    clamped_identity,
    cohort_rate_score,
)

#: 5.1 supply cap points: full marks at 10 open, unassigned, up-to-date
#: newcomer issues. Re-bound from ``porchlamp.core.constants`` under the original
#: name.
SUPPLY_SLOPE: float = constants.SUPPLY_SLOPE


def _supply_score(count: float) -> float:
    return min(100.0, SUPPLY_SLOPE * count)


_SUPPLY = ParamDef(
    input_id="newcomer_issue_supply",
    pillar="accessibility",
    weight=constants.PARAM_WEIGHTS["newcomer_issue_supply"],
    kind=KIND_NUMERIC,
    score=_supply_score,
    domain=(0.0, 20.0),
    higher_is_better=True,
)
_CONVERSION = ParamDef(
    input_id="newcomer_conversion",
    pillar="accessibility",
    weight=constants.PARAM_WEIGHTS["newcomer_conversion"],
    kind=KIND_NUMERIC,
    score=cohort_rate_score,
    min_n=constants.PARAM_MIN_N["newcomer_conversion"],
    domain=(0.0, 1.0),
    higher_is_better=True,
)
_RR90 = ParamDef(
    input_id="rr90_return_rate",
    pillar="accessibility",
    weight=constants.PARAM_WEIGHTS["rr90_return_rate"],
    kind=KIND_NUMERIC,
    score=cohort_rate_score,
    min_n=constants.PARAM_MIN_N["rr90_return_rate"],
    domain=(0.0, 1.0),
    higher_is_better=True,
)
_DX = ParamDef(
    input_id="dx",
    pillar="accessibility",
    weight=constants.PARAM_WEIGHTS["dx"],
    kind=KIND_NUMERIC,
    score=clamped_identity,
    min_n=constants.PARAM_MIN_N["dx"],
    domain=(0.0, 100.0),
    higher_is_better=True,
)

#: This pillar's registered curves, in 02 section 4 order.
DEFS: Tuple[ParamDef, ...] = (_SUPPLY, _CONVERSION, _RR90, _DX)


def newcomer_issue_supply(value: ParamValue) -> ParamScore:
    """5.1 Newcomer-issue supply — open, unassigned, up-to-date newcomer issues.

    Curve ``min(100, 10*count)``; no minimum sample (a repo-controlled
    absence is a measured zero). Takes the bundle input
    ``newcomer_issue_supply``."""
    return _apply(_SUPPLY, value)


def newcomer_conversion(value: ParamValue) -> ParamScore:
    """5.2 Newcomer conversion — cohort authors whose first PR merged within 180 d.

    Curve ``min(100, 200*r)``; minimum sample 5 cohort authors. Takes the
    bundle input ``newcomer_conversion``."""
    return _apply(_CONVERSION, value)


def rr90_return_rate(value: ParamValue) -> ParamScore:
    """5.3 RR_90 — converted authors with a second PR within 90 d.

    Curve ``min(100, 200*r)``; minimum sample 5 converted authors. Takes the
    bundle input ``rr90_return_rate``."""
    return _apply(_RR90, value)


def dx(value: ParamValue) -> ParamScore:
    """5.4 DX — 0.5 CI runtime + 0.5 setup signals, on 0-100.

    Identity clamped to [0, 100]; minimum sample 5 CI runs. Takes the bundle
    input ``dx``."""
    return _apply(_DX, value)
