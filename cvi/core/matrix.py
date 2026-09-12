"""CVI v2 coordinates: leverage, friction, quadrant (D-MAT-1, 02 section 8).

Stage 7 of D-ORDER-1. This is the v2 replacement for the top-level v1
``cvi/matrix.py``, which stays untouched (packet W2-5 owns this module
only). The weights and the quadrant names are unchanged from v1 (D-MAT-1:
"documented convention kept" -- the convention being v1's own docstring,
reproduced below); what changes is that an unmeasured pillar now drops out
of the coordinate under the same D-MISS-2 renormalisation, and the
coordinate carries its own coverage.

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
from typing import Mapping

from cvi.core.models import PillarScore

#: D-MAT-1 leverage weights (kept from v1's LEVERAGE_WEIGHTS).
LEVERAGE_WEIGHTS: dict[str, float] = {
    "criticality": 0.50,
    "rigor": 0.30,
    "governance": 0.20,
}
#: D-MAT-1 friction weights (kept from v1's FRICTION_WEIGHTS).
FRICTION_WEIGHTS: dict[str, float] = {
    "responsiveness": 0.50,
    "governance": 0.30,
    "accessibility": 0.20,
}
#: D-MAT-1 quadrant boundary; a coordinate exactly at the threshold is high.
QUADRANT_THRESHOLD: float = 50.0

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


def _renormalised(weights: Mapping[str, float], values: Mapping[str, float | None]) -> tuple[float | None, float]:
    """``Sum(w_i * v_i) / Sum(w_i)`` over measured (non-``None``) entries;
    coverage is ``Sum(w_i measured) / Sum(w_i all)``. D-MAT-1: an unmeasured
    pillar drops out of the coordinate under the same renormalisation as
    D-MISS-2. ``None`` when every contributing pillar is unmeasured."""
    total_weight = sum(weights.values())
    measured_weight = sum(weight for name, weight in weights.items() if values[name] is not None)
    coverage = measured_weight / total_weight if total_weight > 0.0 else 0.0
    if measured_weight <= 0.0:
        return None, coverage
    numerator = sum(weight * values[name] for name, weight in weights.items() if values[name] is not None)
    return numerator / measured_weight, coverage


def leverage(pillars: Mapping[str, PillarScore]) -> tuple[float | None, float]:
    """D-MAT-1 leverage and its coverage, unrounded. ``pillars`` must carry
    all five frozen pillar names."""
    values = {name: pillars[name].score for name in LEVERAGE_WEIGHTS}
    return _renormalised(LEVERAGE_WEIGHTS, values)


def friction(pillars: Mapping[str, PillarScore]) -> tuple[float | None, float]:
    """D-MAT-1 friction and its coverage, unrounded. ``pillars`` must carry
    all five frozen pillar names."""
    values = {
        name: (100.0 - pillars[name].score) if pillars[name].score is not None else None
        for name in FRICTION_WEIGHTS
    }
    return _renormalised(FRICTION_WEIGHTS, values)


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
