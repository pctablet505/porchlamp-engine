"""Top-level Porchlamp scoring engine (spec 2, 5-phase pipeline; spec 7 state machine).

Wires the Phase 0 EBA gate, the five Phase 3 pillar scorers, the Phase 4
2D-matrix decomposition, and the Phase 5 persona projection into a single
entry point, :func:`score_repository`, returning the spec 9.3
:class:`~porchlamp.models.PorchlampCompositeResult` contract.

This module is importable with zero side effects: it only imports sibling
modules and defines functions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from .constants import TIER_CUTOFFS
from .matrix import decompose
from .models import (
    PorchlampCompositeResult,
    PillarScores,
    Persona,
    RawPREvent,
    RepoMeta,
    Tier,
)
from .persona import persona_composite
from .pillars.accessibility import score_accessibility
from .pillars.criticality import score_criticality
from .pillars.governance import score_governance
from .pillars.rigor import score_rigor
from .pillars.responsiveness import score_responsiveness
from .stage0 import apply_stage0

#: The five pillar names, in pipeline order (spec 2, Phase 3).
PILLAR_NAMES: tuple[str, ...] = (
    "responsiveness",
    "criticality",
    "governance",
    "rigor",
    "accessibility",
)


def assign_tier(porchlamp: float) -> Tier:
    """Map a Porchlamp composite in [0, 100] to its tier (spec 9.3).

    Boundary convention: a score exactly at a cutoff belongs to the higher
    tier (``>=``): 90.0 -> S+, 80.0 -> S, 70.0 -> A, 55.0 -> B, 40.0 -> C,
    everything below 40.0 -> D. Out-of-domain values below 0.0 also map to
    Tier D rather than raising.
    """
    for cutoff, tier_name in TIER_CUTOFFS:
        if porchlamp >= cutoff:
            return Tier(tier_name)
    return Tier.D


def score_repository(
    meta: RepoMeta,
    pr_events: list[RawPREvent],
    now: datetime,
    persona: Persona = Persona.BALANCED,
    ttm_p90_population: Optional[list[float]] = None,
    dependents_population: Optional[list[float]] = None,
    downloads_population: Optional[list[float]] = None,
    pillar_scorers: Optional[dict[str, Callable[..., float]]] = None,
) -> PorchlampCompositeResult:
    """Score one repository through the full 5-phase pipeline (spec 2, 7).

    Phase 0 — EBA gate via :func:`porchlamp.stage0.apply_stage0`, called as
    ``apply_stage0(meta, pr_events, now, ai_persona=..., persona=...)`` with
    ``ai_persona=True`` iff ``persona is Persona.AI_ASSISTED``; the
    ``persona`` argument additionally enforces persona-specific Stage 0
    constraints (spec 4, currently the senior_systems multi-vendor EF >= 3
    gate). A hard gate
    failure short-circuits the pipeline: no pillar scorer is ever called,
    and the result carries ``is_stage0_disqualified=True``,
    ``overall_porchlamp=0.0``, ``rank_tier=Tier.D``, the zero-pillar decomposition
    (leverage 0.0 / friction 100.0 / GRAVEYARD), and the gate's reason.
    Off-platform routing (gate 3) is *not* a failure: the repository is
    scored normally with ``stage0.is_off_platform`` flagged.

    Phase 3 — the five pillar scores. Each pillar uses its default scorer
    unless ``pillar_scorers`` supplies a replacement for that pillar name
    (partial dicts are allowed; unlisted pillars keep their defaults).
    Replacements are called with exactly the same arguments as the defaults
    — this is the dependency-injection seam for testing:

    * ``responsiveness(pr_events, ttm_p90_population or [], now)``
    * ``criticality(meta, dependents_population or [], downloads_population or [])``
    * ``governance(meta)``
    * ``rigor(meta, persona_is_ai=<persona is AI_ASSISTED>)``
    * ``accessibility(meta)``

    Phase 4 — :func:`porchlamp.matrix.decompose` -> (leverage, friction, quadrant).
    Phase 5 — :func:`porchlamp.persona.persona_composite` under ``persona``.
    """
    ai_persona = persona is Persona.AI_ASSISTED
    stage0_result = apply_stage0(meta, pr_events, now, ai_persona=ai_persona, persona=persona)

    if not stage0_result.passed:
        zero = PillarScores(0.0, 0.0, 0.0, 0.0, 0.0)
        _, _, quadrant = decompose(zero)
        return PorchlampCompositeResult(
            owner=meta.owner,
            name=meta.name,
            ecosystem=meta.ecosystem,
            overall_porchlamp=0.0,
            rank_tier=Tier.D,
            matrix_quadrant=quadrant,
            leverage_score=0.0,
            friction_score=100.0,
            is_stage0_disqualified=True,
            disqualification_reason=stage0_result.reason,
            pillar_scores=None,
            stage0=stage0_result,
        )

    ttm_pop = ttm_p90_population if ttm_p90_population is not None else []
    dep_pop = dependents_population if dependents_population is not None else []
    dl_pop = downloads_population if downloads_population is not None else []
    scorers = pillar_scorers or {}

    # (pillar name, args, kwargs, default scorer) in PillarScores field order.
    calls: list[tuple[str, tuple, dict, Callable[..., float]]] = [
        ("responsiveness", (pr_events, ttm_pop, now), {}, score_responsiveness),
        ("criticality", (meta, dep_pop, dl_pop), {}, score_criticality),
        ("governance", (meta,), {}, score_governance),
        ("rigor", (meta,), {"persona_is_ai": ai_persona}, score_rigor),
        ("accessibility", (meta,), {}, score_accessibility),
    ]
    pillar_scores = PillarScores(
        *[
            (scorers[name] if name in scorers else default)(*args, **kwargs)
            for name, args, kwargs, default in calls
        ]
    )

    leverage, friction, quadrant = decompose(pillar_scores)
    overall = persona_composite(pillar_scores, persona)
    return PorchlampCompositeResult(
        owner=meta.owner,
        name=meta.name,
        ecosystem=meta.ecosystem,
        overall_porchlamp=overall,
        rank_tier=assign_tier(overall),
        matrix_quadrant=quadrant,
        leverage_score=leverage,
        friction_score=friction,
        is_stage0_disqualified=False,
        disqualification_reason=None,
        pillar_scores=pillar_scores,
        stage0=stage0_result,
    )
