"""Phase 4 2D Matrix Coordinate Decomposition tests (spec 2, Phase 4; spec 1).

Exact-oracle tests for ``porchlamp.matrix``. Every assertion pins the full
``(leverage, friction, quadrant)`` triple to a hand-computed expectation, so
any drift in weight wiring, inversion (100 - P), rounding, or the quadrant
boundary convention fails loudly.

Boundary convention under test: a coordinate exactly at QUADRANT_THRESHOLD
(50.0) counts as **high** for both axes (``>=``), so ``(50.0, 50.0)`` is
CRUCIBLE. Rounding to 2 decimals happens *before* the quadrant comparison.
"""

from __future__ import annotations

from porchlamp.constants import FRICTION_WEIGHTS, LEVERAGE_WEIGHTS, QUADRANT_THRESHOLD
from porchlamp.matrix import decompose, friction_score, leverage_score
from porchlamp.models import PillarScores, Quadrant


def _ps(p1=0.0, p2=0.0, p3=0.0, p4=0.0, p5=0.0) -> PillarScores:
    return PillarScores(
        responsiveness=p1,
        criticality=p2,
        governance=p3,
        rigor=p4,
        accessibility=p5,
    )


# --------------------------------------------------------------------------- #
# Constants sanity (spec 2, Phase 4)
# --------------------------------------------------------------------------- #
def test_constants_match_spec():
    assert LEVERAGE_WEIGHTS == {"criticality": 0.50, "rigor": 0.30, "governance": 0.20}
    assert FRICTION_WEIGHTS == {"responsiveness": 0.50, "governance": 0.30, "accessibility": 0.20}
    assert QUADRANT_THRESHOLD == 50.0


# --------------------------------------------------------------------------- #
# decompose: exact-oracle cases
# --------------------------------------------------------------------------- #
def test_decompose_all_100_is_sweet_spot():
    # Leverage = 0.5*100 + 0.3*100 + 0.2*100 = 100.0
    # Friction = 0.5*0 + 0.3*0 + 0.2*0 = 0.0
    assert decompose(_ps(100, 100, 100, 100, 100)) == (100.0, 0.0, Quadrant.SWEET_SPOT)


def test_decompose_all_0_is_graveyard():
    # Leverage = 0.0; Friction = 0.5*100 + 0.3*100 + 0.2*100 = 100.0
    assert decompose(_ps(0, 0, 0, 0, 0)) == (0.0, 100.0, Quadrant.GRAVEYARD)


def test_decompose_mixed_hand_computed():
    # P1=80, P2=90, P3=70, P4=60, P5=50
    # Leverage = 0.5*90 + 0.3*60 + 0.2*70 = 45 + 18 + 14 = 77.0
    # Friction = 0.5*(100-80) + 0.3*(100-70) + 0.2*(100-50) = 10 + 9 + 10 = 29.0
    assert decompose(_ps(80, 90, 70, 60, 50)) == (77.0, 29.0, Quadrant.SWEET_SPOT)


def test_decompose_mixed_hand_computed_crucible():
    # P1=20, P2=90, P3=70, P4=60, P5=50
    # Leverage = 0.5*90 + 0.3*60 + 0.2*70 = 77.0
    # Friction = 0.5*80 + 0.3*30 + 0.2*50 = 40 + 9 + 10 = 59.0
    assert decompose(_ps(20, 90, 70, 60, 50)) == (77.0, 59.0, Quadrant.CRUCIBLE)


def test_decompose_mixed_hand_computed_nursery():
    # P1=80, P2=10, P3=30, P4=20, P5=90
    # Leverage = 0.5*10 + 0.3*20 + 0.2*30 = 5 + 6 + 6 = 17.0
    # Friction = 0.5*20 + 0.3*70 + 0.2*10 = 10 + 21 + 2 = 33.0
    assert decompose(_ps(80, 10, 30, 20, 90)) == (17.0, 33.0, Quadrant.NURSERY)


def test_decompose_mixed_hand_computed_graveyard():
    # P1=20, P2=10, P3=30, P4=20, P5=90
    # Leverage = 17.0 (as above)
    # Friction = 0.5*80 + 0.3*70 + 0.2*10 = 40 + 21 + 2 = 63.0
    assert decompose(_ps(20, 10, 30, 20, 90)) == (17.0, 63.0, Quadrant.GRAVEYARD)


def test_decompose_governance_feeds_both_axes():
    # P3 appears in BOTH formulas with opposite signs:
    # P1=50, P2=50, P3=0, P4=50, P5=50
    # Leverage = 0.5*50 + 0.3*50 + 0.2*0 = 40.0
    # Friction = 0.5*50 + 0.3*100 + 0.2*50 = 25 + 30 + 10 = 65.0
    assert decompose(_ps(50, 50, 0, 50, 50)) == (40.0, 65.0, Quadrant.GRAVEYARD)


# --------------------------------------------------------------------------- #
# Quadrant boundary convention (>= 50.0 counts as high)
# --------------------------------------------------------------------------- #
def test_boundary_exactly_50_50_is_crucible():
    # All pillars exactly 50.0 -> leverage = 50.0, friction = 50.0.
    # Documented convention: a coordinate exactly at the threshold counts as
    # HIGH for both axes (>= 50.0), so (50.0, 50.0) -> CRUCIBLE.
    assert decompose(_ps(50.0, 50.0, 50.0, 50.0, 50.0)) == (
        50.0,
        50.0,
        Quadrant.CRUCIBLE,
    )


def test_boundary_just_above_50_leverage_just_below_50_friction_is_sweet_spot():
    # All pillars 50.01 -> leverage = 50.01 (high), friction = 49.99 (low,
    # because friction inverts the pillars) -> SWEET_SPOT.
    assert decompose(_ps(50.01, 50.01, 50.01, 50.01, 50.01)) == (
        50.01,
        49.99,
        Quadrant.SWEET_SPOT,
    )


def test_boundary_leverage_just_below_friction_just_above_is_graveyard():
    # All pillars 49.99 -> leverage = 49.99 (low), friction = 50.01 (high).
    # (friction inverts: low pillars => high friction)
    assert decompose(_ps(49.99, 49.99, 49.99, 49.99, 49.99)) == (
        49.99,
        50.01,
        Quadrant.GRAVEYARD,
    )


def test_boundary_leverage_50_friction_49_99_is_sweet_spot():
    # P1=50.05, P2=50.01, P3=49.99, P4=50.01, P5=50.05
    # Leverage = 0.5*50.01 + 0.3*50.01 + 0.2*49.99 = 25.005 + 15.003 + 9.998 = 50.006 -> 50.01
    # Friction = 0.5*49.95 + 0.3*50.01 + 0.2*49.95 = 24.975 + 15.003 + 9.99 = 49.968 -> 49.97
    assert decompose(_ps(50.05, 50.01, 49.99, 50.01, 50.05)) == (
        50.01,
        49.97,
        Quadrant.SWEET_SPOT,
    )


def test_boundary_leverage_49_99_friction_50_01_is_graveyard():
    # P1=49.99, P2=49.99, P3=49.99, P4=49.99, P5=49.99 -> leverage 49.99, friction 50.01
    assert decompose(_ps(49.99, 49.99, 49.99, 49.99, 49.99)) == (
        49.99,
        50.01,
        Quadrant.GRAVEYARD,
    )


def test_boundary_rounding_before_quadrant_check():
    # Raw leverage = 0.5*49.999 + 0.3*49.999 + 0.2*49.999 = 49.999 -> rounds to 50.0
    # => counts as HIGH leverage even though the raw value is below 50.
    # P1=50.01, P3=49.999, P5=50.01 -> friction = 0.5*49.99 + 0.3*50.001 + 0.2*49.99
    #   = 24.995 + 15.0003 + 9.998 = 49.9933 -> 49.99 (low)
    assert decompose(_ps(50.01, 49.999, 49.999, 49.999, 50.01)) == (
        50.0,
        49.99,
        Quadrant.SWEET_SPOT,
    )


# --------------------------------------------------------------------------- #
# Public helpers
# --------------------------------------------------------------------------- #
def test_leverage_score_helper_matches_decompose():
    ps = _ps(80, 90, 70, 60, 50)
    lev, fri, _ = decompose(ps)
    assert leverage_score(ps) == lev == 77.0
    assert friction_score(ps) == fri == 29.0


def test_leverage_score_ignores_responsiveness_and_accessibility():
    # P1 and P5 must not leak into leverage.
    a = _ps(0, 90, 70, 60, 0)
    b = _ps(100, 90, 70, 60, 100)
    assert leverage_score(a) == leverage_score(b) == 77.0


def test_friction_score_ignores_criticality_and_rigor():
    # P2 and P4 must not leak into friction.
    a = _ps(80, 0, 70, 0, 50)
    b = _ps(80, 100, 70, 100, 50)
    assert friction_score(a) == friction_score(b) == 29.0


def test_helpers_round_to_two_decimals():
    # 0.5*33.333 + 0.3*66.667 + 0.2*50 = 16.6665 + 20.0001 + 10 = 46.6666 -> 46.67
    assert leverage_score(_ps(0, 33.333, 50.0, 66.667, 0)) == 46.67
    # 0.5*(100-33.333) + 0.3*(100-66.667) + 0.2*(100-50) = 33.3335 + 10.0001 + 10 = 53.3336 -> 53.33
    assert friction_score(_ps(33.333, 0, 66.667, 0, 50.0)) == 53.33


def test_decompose_returns_floats_not_ints():
    lev, fri, quad = decompose(_ps(100, 100, 100, 100, 100))
    assert isinstance(lev, float)
    assert isinstance(fri, float)
    assert isinstance(quad, Quadrant)


def test_decompose_all_quadrants_reachable():
    seen = {
        decompose(_ps(100, 100, 100, 100, 100))[2],
        decompose(_ps(0, 100, 100, 100, 0))[2],
        decompose(_ps(100, 0, 0, 0, 100))[2],
        decompose(_ps(0, 0, 0, 0, 0))[2],
    }
    assert seen == {
        Quadrant.SWEET_SPOT,
        Quadrant.CRUCIBLE,
        Quadrant.NURSERY,
        Quadrant.GRAVEYARD,
    }


def test_decompose_by_coordinates():
    assert decompose(leverage=60.0, friction=40.0) == Quadrant.SWEET_SPOT
    assert decompose(leverage=60.0, friction=60.0) == Quadrant.CRUCIBLE
    assert decompose(leverage=40.0, friction=40.0) == Quadrant.NURSERY
    assert decompose(leverage=40.0, friction=60.0) == Quadrant.GRAVEYARD


def test_decompose_no_args_raises_value_error():
    import pytest

    with pytest.raises(ValueError, match="Must provide either pillar_scores or both"):
        decompose()


def test_decompose_partial_coordinates_raises_value_error():
    import pytest

    with pytest.raises(ValueError, match="Must provide either pillar_scores or both"):
        decompose(leverage=60.0)
    with pytest.raises(ValueError, match="Must provide either pillar_scores or both"):
        decompose(friction=40.0)

