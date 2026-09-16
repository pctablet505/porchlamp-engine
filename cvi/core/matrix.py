"""CVI v2 coordinates: leverage, friction, quadrant (D-MAT-1, 02 section 8).

Stage 7 of D-ORDER-1. This is the v2 counterpart of the top-level v1
``cvi/matrix.py``, which remains separate. The weights and the quadrant
names are unchanged from v1 (D-MAT-1: "documented convention kept" -- the
convention being v1's own docstring, reproduced below); what differs is
that an unmeasured pillar drops out of the coordinate under the same
D-MISS-2 renormalisation, and the coordinate carries its own coverage.

Leverage (Y axis): ``0.50*criticality + 0.30*rigor + 0.20*governance``.
Friction (X axis): ``0.50*(100-responsiveness) + 0.30*(100-governance) +
0.20*(100-accessibility)``. Quadrant threshold 50.0, ``>=`` counts as high
on both axes (kept from v1: SWEET_SPOT/CRUCIBLE/NURSERY/GRAVEYARD, so
``(50.0, 50.0) -> Crucible``). Leverage and friction are rounded to 2
decimals (04 section 8) *before* the quadrant comparison -- the specific v1
convention D-MAT-1 keeps ("Rounding to 2 decimals happens before the
quadrant comparison, so a raw coordinate of 49.999 that rounds to 50.0 is
treated as high"), which is a deliberate exception to the general 04
section 8 rule that gate/ceiling comparisons use unrounded values: a
quadrant is neither a gate nor a ceiling, and D-MAT-1 names the v1 rounded-
comparison behaviour specifically as kept.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from cvi.core import aggregate, constants
from cvi.core.models import PillarScore

#: D-MAT-1 leverage weights (kept from v1's LEVERAGE_WEIGHTS), re-bound
#: from ``cvi.core.constants`` under the original name.
LEVERAGE_WEIGHTS: dict[str, float] = constants.LEVERAGE_WEIGHTS
#: D-MAT-1 friction weights (kept from v1's FRICTION_WEIGHTS).
FRICTION_WEIGHTS: dict[str, float] = constants.FRICTION_WEIGHTS
#: D-MAT-1 quadrant boundary; a coordinate exactly at the threshold is high.
QUADRANT_THRESHOLD: float = constants.QUADRANT_THRESHOLD

#: D-MAT-1 quadrant labels (kept from v1's Quadrant enum values).
QUADRANT_SWEET_SPOT: str = "Sweet Spot"
QUADRANT_CRUCIBLE: str = "Crucible"
QUADRANT_NURSERY: str = "Nursery"
QUADRANT_GRAVEYARD: str = "Graveyard"


@dataclass(frozen=True)
class Coordinates:
    """One repo's stage-7 coordinates (D-MAT-1): leverage, friction, the
    quadrant they place the repo in, and each axis's own coverage (the
    measured share of that axis's contributing pillar weight)."""

    leverage: float | None
    leverage_coverage: float
    friction: float | None
    friction_coverage: float
    quadrant: str | None


def _renormalised(
    weights: Mapping[str, float],
    pillars: Mapping[str, PillarScore],
    transform: Callable[[float], float],
) -> tuple[float | None, float]:
    """``Sum(w_i * transform(score_i)) / Sum(w_i)`` over measured contributing
    pillars, and that axis's coverage, both over the APPLICABLE ones.

    Two renormalisations, over the same denominator, matching what
    :mod:`cvi.core.aggregate` does for the composite:

    - D-MISS-5: a contributing pillar that is ``applicable=False`` (all of its
      parameters are ``not_applicable`` for this repo -- a fact about the
      world, not a gap in the data) leaves BOTH sums entirely, exactly as
      :func:`cvi.core.aggregate.composite_coverage` drops it from its own
      denominator. Leaving its weight in the denominator would cap this
      axis's coverage below 1.0 for a repo that is, on the evidence that can
      exist for it, fully measured.
    - D-MAT-1/D-MISS-2: an unmeasured pillar (``score is None``) drops out of
      the VALUE's renormalisation, so the coordinate is the weighted mean of
      the pillars that were measured.

    ``coverage`` is the measured SHARE of applicable contributing weight --
    ``Sum(w_i * c_i) / Sum(w_i)``, weighing each pillar by its own
    ``PillarScore.coverage`` rather than by whether it has any score at all.
    A pillar 10% measured contributes 0.10 of its weight here, not all of it:
    "this axis is fully covered" must mean the parameters under it were
    measured, not merely that one of them was.

    ``None`` for the value when every applicable contributing pillar is
    unmeasured; ``0.0`` coverage when none is applicable.
    """
    applicable = [name for name in weights if pillars[name].applicable]
    total_weight = sum(weights[name] for name in applicable)
    if total_weight <= 0.0:
        return None, 0.0
    coverage = round(
        sum(weights[name] * pillars[name].coverage for name in applicable) / total_weight,
        aggregate.RATIO_DECIMALS,
    )
    measured = [(weights[name], pillars[name].score) for name in applicable if pillars[name].score is not None]
    measured_weight = sum(weight for weight, _ in measured)
    if measured_weight <= 0.0:
        return None, coverage
    value = sum(weight * transform(score) for weight, score in measured) / measured_weight
    return round(value, aggregate.RATIO_DECIMALS), coverage


def leverage(pillars: Mapping[str, PillarScore]) -> tuple[float | None, float]:
    """D-MAT-1 leverage and its coverage, unrounded. ``pillars`` must carry
    all five frozen pillar names."""
    return _renormalised(LEVERAGE_WEIGHTS, pillars, lambda score: score)


def friction(pillars: Mapping[str, PillarScore]) -> tuple[float | None, float]:
    """D-MAT-1 friction and its coverage, unrounded. ``pillars`` must carry
    all five frozen pillar names."""
    return _renormalised(FRICTION_WEIGHTS, pillars, lambda score: 100.0 - score)


def quadrant(leverage_value: float | None, friction_value: float | None) -> str | None:
    """D-MAT-1 quadrant from *already-rounded* leverage/friction (module
    docstring: the kept v1 convention rounds before this comparison).
    ``None`` when either axis is not computable (its contributing pillars
    are all unmeasured)."""
    if leverage_value is None or friction_value is None:
        return None
    high_leverage = leverage_value >= QUADRANT_THRESHOLD
    high_friction = friction_value >= QUADRANT_THRESHOLD
    if high_leverage and not high_friction:
        return QUADRANT_SWEET_SPOT
    if high_leverage and high_friction:
        return QUADRANT_CRUCIBLE
    if not high_leverage and not high_friction:
        return QUADRANT_NURSERY
    return QUADRANT_GRAVEYARD


def coordinates(pillars: Mapping[str, PillarScore]) -> Coordinates:
    """The full stage-7 :class:`Coordinates` for one repo's pillar scores
    (D-MAT-1). ``pillars`` must carry all five frozen pillar names (the
    :func:`cvi.core.aggregate.aggregate_pillars` output)."""
    lev_raw, lev_coverage = leverage(pillars)
    fric_raw, fric_coverage = friction(pillars)
    lev = None if lev_raw is None else round(lev_raw, 2)
    fric = None if fric_raw is None else round(fric_raw, 2)
    return Coordinates(
        leverage=lev,
        leverage_coverage=lev_coverage,
        friction=fric,
        friction_coverage=fric_coverage,
        quadrant=quadrant(lev, fric),
    )
