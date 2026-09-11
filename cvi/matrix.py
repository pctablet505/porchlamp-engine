"""Phase 4: 2D Matrix Coordinate Decomposition (spec 2, Phase 4; spec 1).

Decomposes the five calibrated pillar scores into two orthogonal coordinates:

- **Leverage** (Y axis): ``0.50*P2 + 0.30*P4 + 0.20*P3``
  (P2 = criticality, P4 = rigor, P3 = governance)
- **Friction** (X axis): ``0.50*(100-P1) + 0.30*(100-P3) + 0.20*(100-P5)``
  (P1 = responsiveness, P3 = governance, P5 = accessibility)

Both coordinates are rounded to 2 decimals (spec 9.3: ``leverage_score`` /
``friction_score`` are stored at 2 decimals).

Quadrant classification (spec 1, 2D Discovery Matrix):

- SWEET_SPOT: high leverage + low friction
- CRUCIBLE:   high leverage + high friction
- NURSERY:    low leverage + low friction
- GRAVEYARD:  low leverage + high friction

Boundary convention (documented): a coordinate exactly at
``QUADRANT_THRESHOLD`` (50.0) counts as **high** for both axes, i.e. the
comparison is ``>= threshold``. Consequently ``(50.0, 50.0) -> CRUCIBLE``.
Rounding to 2 decimals happens *before* the quadrant comparison, so a raw
coordinate of 49.999 that rounds to 50.0 is treated as high.
"""

from __future__ import annotations

from .constants import FRICTION_WEIGHTS, LEVERAGE_WEIGHTS, QUADRANT_THRESHOLD
from .models import PillarScores, Quadrant


def leverage_score(pillar_scores: PillarScores) -> float:
    """Leverage coordinate (Y): ``0.50*P2 + 0.30*P4 + 0.20*P3``, 2 decimals."""
    raw = (
        LEVERAGE_WEIGHTS["criticality"] * pillar_scores.criticality
        + LEVERAGE_WEIGHTS["rigor"] * pillar_scores.rigor
        + LEVERAGE_WEIGHTS["governance"] * pillar_scores.governance
    )
    return round(raw, 2)


def friction_score(pillar_scores: PillarScores) -> float:
    """Friction coordinate (X): ``0.50*(100-P1) + 0.30*(100-P3) + 0.20*(100-P5)``, 2 decimals."""
    raw = (
        FRICTION_WEIGHTS["responsiveness"] * (100.0 - pillar_scores.responsiveness)
        + FRICTION_WEIGHTS["governance"] * (100.0 - pillar_scores.governance)
        + FRICTION_WEIGHTS["accessibility"] * (100.0 - pillar_scores.accessibility)
    )
    return round(raw, 2)


def _quadrant(leverage: float, friction: float) -> Quadrant:
    """Classify (leverage, friction) into a quadrant.

    A coordinate exactly at ``QUADRANT_THRESHOLD`` counts as high (``>=``).
    """
    high_leverage = leverage >= QUADRANT_THRESHOLD
    high_friction = friction >= QUADRANT_THRESHOLD
    if high_leverage and not high_friction:
        return Quadrant.SWEET_SPOT
    if high_leverage and high_friction:
        return Quadrant.CRUCIBLE
    if not high_leverage and not high_friction:
        return Quadrant.NURSERY
    return Quadrant.GRAVEYARD


def decompose(pillar_scores: PillarScores) -> tuple[float, float, Quadrant]:
    """Return ``(leverage, friction, quadrant)`` for the given pillar scores."""
    leverage = leverage_score(pillar_scores)
    friction = friction_score(pillar_scores)
    return leverage, friction, _quadrant(leverage, friction)
