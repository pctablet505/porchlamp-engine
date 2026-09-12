"""Pillar 3 — Governance parameter curves (02 section 4, parameters 3.1-3.3).

Each function takes the FeatureBundle input for its parameter
(``bundle.inputs[<input id>]``) and returns the calibrated ParamScore.
3.1 Legal IP and 3.3 Promotion ladder are evidence-based enum lookups over
the frozen vocabularies (constants_v2); 3.2 Elephant factor is the linear
calibration ``min(100, 25*EF)`` with a 30-commit minimum sample.
"""
from __future__ import annotations

from typing import Dict, Tuple

from cvi.core.models import ParamScore, ParamValue
from cvi.core.params.curves import KIND_ENUM, KIND_NUMERIC, ParamDef, _apply

#: 3.1 regime scores (02 section 4, owner decision O-1: NONE 85).
LEGAL_IP_SCORES: Dict[str, float] = {
    "DCO": 100.0,
    "NONE": 85.0,
    "FOUNDATION_CLA": 75.0,
    "CORP_CLA": 25.0,
}
#: 3.3 rung scores (02 section 4).
PROMOTION_LADDER_SCORES: Dict[str, float] = {
    "VERIFIED": 100.0,
    "DOCUMENTED": 75.0,
    "AD_HOC": 40.0,
    "CLOSED": 10.0,
}
#: 3.2 EF slope (02 section 4): points per organisation at the 50% commit share.
ELEPHANT_SLOPE: float = 25.0


def _lookup(table: Dict[str, float], input_id: str, value: str) -> float:
    try:
        return table[value]
    except KeyError:
        raise ValueError(f"{input_id}: unknown value {value!r}; expected one of {sorted(table)}") from None


def _legal_ip_score(regime: str) -> float:
    return _lookup(LEGAL_IP_SCORES, "legal_ip", regime)


def _elephant_score(ef: float) -> float:
    return min(100.0, ELEPHANT_SLOPE * ef)


def _promotion_ladder_score(rung: str) -> float:
    return _lookup(PROMOTION_LADDER_SCORES, "promotion_ladder", rung)


_LEGAL_IP = ParamDef(
    input_id="legal_ip",
    pillar="governance",
    weight=0.35,
    kind=KIND_ENUM,
    score=_legal_ip_score,
    better_order=("CORP_CLA", "FOUNDATION_CLA", "NONE", "DCO"),
)
_ELEPHANT = ParamDef(
    input_id="elephant_factor",
    pillar="governance",
    weight=0.35,
    kind=KIND_NUMERIC,
    score=_elephant_score,
    min_n=30,
    domain=(1.0, 10.0),
    higher_is_better=True,
)
_LADDER = ParamDef(
    input_id="promotion_ladder",
    pillar="governance",
    weight=0.30,
    kind=KIND_ENUM,
    score=_promotion_ladder_score,
    better_order=("CLOSED", "AD_HOC", "DOCUMENTED", "VERIFIED"),
)

#: This pillar's registered curves, in 02 section 4 order.
DEFS: Tuple[ParamDef, ...] = (_LEGAL_IP, _ELEPHANT, _LADDER)


def legal_ip(value: ParamValue) -> ParamScore:
    """3.1 Legal IP — evidence-based licence/IP regime.

    DCO 100, NONE 85 (owner decision O-1), FOUNDATION_CLA 75, CORP_CLA 25;
    no minimum sample. Takes the bundle input ``legal_ip``."""
    return _apply(_LEGAL_IP, value)


def elephant_factor(value: ParamValue) -> ParamScore:
    """3.2 Elephant factor — minimum orgs reaching 50% of 365 d commits.

    Curve ``min(100, 25*EF)``; minimum sample 30 commits. Takes the bundle
    input ``elephant_factor``."""
    return _apply(_ELEPHANT, value)


def promotion_ladder(value: ParamValue) -> ParamScore:
    """3.3 Promotion ladder — VERIFIED 100, DOCUMENTED 75, AD_HOC 40, CLOSED 10.

    No minimum sample. Takes the bundle input ``promotion_ladder``."""
    return _apply(_LADDER, value)
