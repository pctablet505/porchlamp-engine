"""Pillar 4 — Rigor parameter curves (02 section 4, parameters 4.1-4.3).

Each function takes the FeatureBundle input for its parameter
(``bundle.inputs[<input id>]``) and returns the calibrated ParamScore.
4.1 Pipeline defenses is the identity on the 0-100 composite (Scorecard
mean or proxy blend, clamped), with its 10-merged-PR minimum sample applying
to the proxy method only; 4.2 AI posture is the persona-conditional lookup;
4.3 Substantive review rate replaces the v1 Gaussian with the monotone rate
curve ``min(100, 125*r)`` (D-PARAM-MONO).
"""
from __future__ import annotations

from typing import Dict, Tuple

from cvi.core.models import ParamScore, ParamValue
from cvi.core.params.curves import (
    KIND_ENUM,
    KIND_NUMERIC,
    ParamDef,
    _apply,
    clamped_identity,
    fill_rate_score,
)

#: 4.2 posture scores, persona-conditional (02 section 4, spec v1 table).
AI_POSTURE_SCORES: Dict[str, Dict[str, float]] = {
    "REGULATED": {"human": 95.0, "ai": 95.0},
    "PROSCRIBED": {"human": 85.0, "ai": 25.0},
    "AD_HOC": {"human": 40.0, "ai": 40.0},
}


def _ai_posture_score(posture: str, persona: str) -> float:
    table = AI_POSTURE_SCORES.get(posture)
    if table is None:
        raise ValueError(f"ai_posture: unknown posture {posture!r}; expected one of {sorted(AI_POSTURE_SCORES)}")
    return table[persona]


_PIPELINE = ParamDef(
    input_id="pipeline_defenses",
    pillar="rigor",
    weight=0.40,
    kind=KIND_NUMERIC,
    score=clamped_identity,
    proxy_min_n=10,
    domain=(0.0, 100.0),
    higher_is_better=True,
)
_AI_POSTURE = ParamDef(
    input_id="ai_posture",
    pillar="rigor",
    weight=0.30,
    kind=KIND_ENUM,
    score=_ai_posture_score,
    persona_orders={
        "human": ("AD_HOC", "PROSCRIBED", "REGULATED"),
        "ai": ("PROSCRIBED", "AD_HOC", "REGULATED"),
    },
)
_SRR = ParamDef(
    input_id="substantive_review_rate",
    pillar="rigor",
    weight=0.30,
    kind=KIND_NUMERIC,
    score=fill_rate_score,
    min_n=10,
    domain=(0.0, 1.0),
    higher_is_better=True,
)

#: This pillar's registered curves, in 02 section 4 order.
DEFS: Tuple[ParamDef, ...] = (_PIPELINE, _AI_POSTURE, _SRR)


def pipeline_defenses(value: ParamValue) -> ParamScore:
    """4.1 Pipeline defenses — Scorecard check mean or proxy blend, on 0-100.

    Identity clamped to [0, 100]; the 10-merged-PR minimum sample applies to
    the proxy method only. Takes the bundle input ``pipeline_defenses``."""
    return _apply(_PIPELINE, value)


def ai_posture(value: ParamValue, persona: str = "human") -> ParamScore:
    """4.2 AI policy posture — persona-conditional lookup.

    REGULATED 95/95, PROSCRIBED 85 (human) / 25 (ai), AD_HOC 40/40; no
    minimum sample. Takes the bundle input ``ai_posture``."""
    return _apply(_AI_POSTURE, value, persona=persona)


def substantive_review_rate(value: ParamValue) -> ParamScore:
    """4.3 Substantive review rate — decided PRs with a substantive review.

    The monotone v2 replacement of the v1 Gaussian: ``min(100, 125*r)``;
    minimum sample 10. Takes the bundle input ``substantive_review_rate``."""
    return _apply(_SRR, value)
