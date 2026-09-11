"""Phase 5 Persona-Adaptive Leaderboard Projection tests (spec 2, Phase 5; spec 4).

Exact-oracle tests for ``cvi.persona``. Every assertion pins the computed
value to a hand-computed expectation, so any drift in weight wiring,
validation, rounding, or rank-delta tie-breaking fails loudly.

Documented conventions under test:
- ``reproject``/``persona_composite``: WAM composite ``sum(w_j * pillar_j)``,
  rounded to 2 decimals. Weights must cover exactly the 5 pillar keys and sum
  to 1.0 within 1e-6, else ``ValueError``.
- ``rank_deltas``: both rankings are score-descending, 1-based; ties are
  broken by position in ``scores_before`` (earlier = better rank), applied to
  BOTH the old and the new ranking. ``delta = old_rank - new_rank``
  (positive = moved up).
"""

from __future__ import annotations

import pytest

from cvi.constants import PERSONA_PRESETS
from cvi.models import Persona, PillarScores
from cvi.persona import (
    PILLAR_KEYS,
    persona_composite,
    rank_deltas,
    reproject,
    validate_persona_preset,
)


def _ps(p1=0.0, p2=0.0, p3=0.0, p4=0.0, p5=0.0) -> PillarScores:
    return PillarScores(
        responsiveness=p1,
        criticality=p2,
        governance=p3,
        rigor=p4,
        accessibility=p5,
    )


def _weights(**overrides: float) -> dict[str, float]:
    base = {
        "responsiveness": 0.25,
        "criticality": 0.25,
        "governance": 0.20,
        "rigor": 0.15,
        "accessibility": 0.15,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# reproject: weight validation
# --------------------------------------------------------------------------- #
def test_reproject_rejects_four_keys():
    w = _weights()
    del w["accessibility"]
    with pytest.raises(ValueError, match="exactly the 5 pillar keys"):
        reproject(_ps(80, 90, 70, 60, 50), w)


def test_reproject_rejects_six_keys():
    w = _weights(extra="stars")
    with pytest.raises(ValueError, match="exactly the 5 pillar keys"):
        reproject(_ps(80, 90, 70, 60, 50), w)


def test_reproject_rejects_wrong_key_name_even_if_five_keys():
    w = _weights()
    del w["governance"]
    w["gov"] = 0.20
    with pytest.raises(ValueError, match="exactly the 5 pillar keys"):
        reproject(_ps(80, 90, 70, 60, 50), w)


def test_reproject_rejects_sum_outside_tolerance_low():
    # 0.25 + 0.25 + 0.20 + 0.15 + 0.14999 = 0.99999; |0.99999 - 1| = 1e-5 > 1e-6.
    w = _weights(accessibility=0.14999)
    with pytest.raises(ValueError, match="sum to 1.0"):
        reproject(_ps(80, 90, 70, 60, 50), w)


def test_reproject_rejects_sum_outside_tolerance_high():
    # 0.25 + 0.25 + 0.20 + 0.15 + 0.15001 = 1.00001; deviation 1e-5 > 1e-6.
    w = _weights(accessibility=0.15001)
    with pytest.raises(ValueError, match="sum to 1.0"):
        reproject(_ps(80, 90, 70, 60, 50), w)


def test_reproject_accepts_sum_inside_tolerance():
    # 0.25 + 0.25 + 0.20 + 0.15 + 0.1500001 = 1.0000001; deviation 1e-7 <= 1e-6.
    w = _weights(accessibility=0.1500001)
    # 0.25*80 + 0.25*90 + 0.20*70 + 0.15*60 + 0.1500001*50
    # = 20 + 22.5 + 14 + 9 + 7.500005 = 73.000005 -> 73.0
    assert reproject(_ps(80, 90, 70, 60, 50), w) == 73.0


def test_reproject_rejects_sum_just_outside_tolerance():
    # 0.25 + 0.25 + 0.20 + 0.15 + 0.149999 = 0.999999; deviation 1e-6 is NOT
    # <= 1e-6? It IS == 1e-6 -> accepted. Use 0.1499989 instead:
    # 0.25 + 0.25 + 0.20 + 0.15 + 0.1499989 = 0.9999989; deviation 1.1e-6 > 1e-6.
    w = _weights(accessibility=0.1499989)
    with pytest.raises(ValueError, match="sum to 1.0"):
        reproject(_ps(80, 90, 70, 60, 50), w)


# --------------------------------------------------------------------------- #
# reproject: exact-oracle composites
# --------------------------------------------------------------------------- #
def test_reproject_exact_hand_computed():
    # 0.25*80 + 0.25*90 + 0.20*70 + 0.15*60 + 0.15*50
    # = 20 + 22.5 + 14 + 9 + 7.5 = 73.0
    assert reproject(_ps(80, 90, 70, 60, 50), _weights()) == 73.0


def test_reproject_exact_hand_computed_half_half():
    # 0.5*80 + 0.5*90 = 85.0
    w = _weights(responsiveness=0.5, criticality=0.5, governance=0.0, rigor=0.0, accessibility=0.0)
    assert reproject(_ps(80, 90, 70, 60, 50), w) == 85.0


def test_reproject_rounds_to_two_decimals():
    # 0.2*(10.111 + 20.222 + 30.333 + 40.444 + 50.555) = 0.2*151.665 = 30.333 -> 30.33
    w = _weights(responsiveness=0.2, criticality=0.2, governance=0.2, rigor=0.2, accessibility=0.2)
    assert reproject(_ps(10.111, 20.222, 30.333, 40.444, 50.555), w) == 30.33


def test_reproject_all_zero_pillars_is_zero():
    assert reproject(_ps(0, 0, 0, 0, 0), _weights()) == 0.0


def test_reproject_all_100_pillars_is_100():
    assert reproject(_ps(100, 100, 100, 100, 100), _weights()) == 100.0


# --------------------------------------------------------------------------- #
# persona_composite: all 5 personas, hand-computed
# --------------------------------------------------------------------------- #
def test_persona_composite_balanced_hand_computed():
    # 0.25*80 + 0.25*90 + 0.20*70 + 0.15*60 + 0.15*50 = 73.0
    assert persona_composite(_ps(80, 90, 70, 60, 50), Persona.BALANCED) == 73.0


def test_persona_composite_first_timer_hand_computed():
    # 0.35*80 + 0.10*90 + 0.15*70 + 0.10*60 + 0.30*50
    # = 28 + 9 + 10.5 + 6 + 15 = 68.5
    assert persona_composite(_ps(80, 90, 70, 60, 50), Persona.FIRST_TIMER) == 68.5


def test_persona_composite_career_capital_hand_computed():
    # 0.20*80 + 0.35*90 + 0.15*70 + 0.10*60 + 0.20*50
    # = 16 + 31.5 + 10.5 + 6 + 10 = 74.0
    assert persona_composite(_ps(80, 90, 70, 60, 50), Persona.CAREER_CAPITAL) == 74.0


def test_persona_composite_senior_systems_hand_computed():
    # 0.15*80 + 0.35*90 + 0.25*70 + 0.20*60 + 0.05*50
    # = 12 + 31.5 + 17.5 + 12 + 2.5 = 75.5
    assert persona_composite(_ps(80, 90, 70, 60, 50), Persona.SENIOR_SYSTEMS) == 75.5


def test_persona_composite_ai_assisted_hand_computed():
    # 0.20*80 + 0.20*90 + 0.15*70 + 0.35*60 + 0.10*50
    # = 16 + 18 + 10.5 + 21 + 5 = 70.5
    assert persona_composite(_ps(80, 90, 70, 60, 50), Persona.AI_ASSISTED) == 70.5


def test_persona_composite_matches_reproject_with_preset():
    ps = _ps(80, 90, 70, 60, 50)
    for persona in Persona:
        assert persona_composite(ps, persona) == reproject(ps, PERSONA_PRESETS[persona])


def test_persona_composites_distinguish_personas():
    # Same pillars, different personas -> different composites (weights differ).
    ps = _ps(80, 90, 70, 60, 50)
    values = [persona_composite(ps, p) for p in Persona]
    assert len(set(values)) == len(Persona)


# --------------------------------------------------------------------------- #
# rank_deltas
# --------------------------------------------------------------------------- #
def test_rank_deltas_known_permutation():
    # before: a=90(1), b=80(2), c=70(3), d=60(4), e=50(5)
    # after:  b=90(1), e=80(2), c=70(3), d=60(4), a=50(5)
    before = [("a", 90.0), ("b", 80.0), ("c", 70.0), ("d", 60.0), ("e", 50.0)]
    after = {"a": 50.0, "b": 90.0, "c": 70.0, "d": 60.0, "e": 80.0}
    assert rank_deltas(before, after) == {"a": -4, "b": +1, "c": 0, "d": 0, "e": +3}


def test_rank_deltas_jump_from_5_to_1_is_plus_4():
    before = [("a", 90.0), ("b", 80.0), ("c", 70.0), ("d", 60.0), ("e", 50.0)]
    after = {"a": 90.0, "b": 80.0, "c": 70.0, "d": 60.0, "e": 95.0}
    deltas = rank_deltas(before, after)
    assert deltas["e"] == 4
    # everyone else shifts down by one
    assert deltas["a"] == -1
    assert deltas["b"] == -1
    assert deltas["c"] == -1
    assert deltas["d"] == -1


def test_rank_deltas_ties_keep_relative_before_order():
    # Old ranking has NO ties (a=1, b=2, c=3). In the new snapshot a and b
    # tie at 80; the tie-break is position in scores_before, so a (earlier)
    # takes rank 2 and b rank 3. If the tie-break were reversed (or absent),
    # a and b's deltas would swap.
    before = [("a", 90.0), ("b", 80.0), ("c", 70.0)]
    after = {"a": 80.0, "b": 80.0, "c": 95.0}
    # new order: c(1), a(2), b(3)
    assert rank_deltas(before, after) == {"a": -1, "b": -1, "c": +2}


def test_rank_deltas_tie_break_is_position_not_score_or_name():
    # Same tie as above but with b listed FIRST in scores_before: b now wins
    # the tie. Proves the tie-break key is list position, not repo id or any
    # other property.
    before = [("b", 80.0), ("a", 90.0), ("c", 70.0)]
    after = {"a": 80.0, "b": 80.0, "c": 95.0}
    # old ranks from scores: a=1, b=2, c=3; new order: c(1), b(2), a(3)
    assert rank_deltas(before, after) == {"a": -2, "b": 0, "c": +2}


def test_rank_deltas_single_repo_is_zero():
    assert rank_deltas([("solo", 42.0)], {"solo": 42.0}) == {"solo": 0}


def test_rank_deltas_single_repo_score_change_still_zero():
    # One repo cannot move relative to itself.
    assert rank_deltas([("solo", 42.0)], {"solo": 99.0}) == {"solo": 0}


def test_rank_deltas_no_change_is_all_zero():
    before = [("a", 90.0), ("b", 80.0), ("c", 70.0)]
    after = {"a": 90.0, "b": 80.0, "c": 70.0}
    assert rank_deltas(before, after) == {"a": 0, "b": 0, "c": 0}


def test_rank_deltas_before_need_not_be_presorted():
    # scores_before is NOT in score order; the old ranking is computed from
    # its scores (ties broken by list position), not from list order.
    before = [("c", 70.0), ("a", 90.0), ("b", 80.0)]
    after = {"a": 90.0, "b": 80.0, "c": 70.0}
    # old ranks: a=1, b=2, c=3; new ranks: a=1, b=2, c=3 -> all zero.
    assert rank_deltas(before, after) == {"a": 0, "b": 0, "c": 0}


def test_rank_deltas_rejects_missing_repo_in_after():
    before = [("a", 90.0), ("b", 80.0)]
    with pytest.raises(ValueError, match="same repo ids"):
        rank_deltas(before, {"a": 90.0})


def test_rank_deltas_rejects_extra_repo_in_after():
    before = [("a", 90.0)]
    with pytest.raises(ValueError, match="same repo ids"):
        rank_deltas(before, {"a": 90.0, "z": 10.0})


def test_rank_deltas_rejects_duplicate_repo_ids_in_before():
    before = [("a", 90.0), ("a", 80.0)]
    with pytest.raises(ValueError, match="duplicate"):
        rank_deltas(before, {"a": 90.0})


def test_rank_deltas_result_covers_all_repos():
    before = [("a", 90.0), ("b", 80.0), ("c", 70.0), ("d", 60.0), ("e", 50.0)]
    after = {"a": 50.0, "b": 90.0, "c": 70.0, "d": 60.0, "e": 80.0}
    deltas = rank_deltas(before, after)
    assert set(deltas) == {"a", "b", "c", "d", "e"}
    assert all(isinstance(v, int) for v in deltas.values())


# --------------------------------------------------------------------------- #
# validate_persona_preset
# --------------------------------------------------------------------------- #
def test_validate_persona_preset_all_five_pass():
    for persona in Persona:
        validate_persona_preset(persona)  # must not raise


def test_validate_persona_preset_sums_to_one():
    for persona in Persona:
        total = sum(PERSONA_PRESETS[persona].values())
        assert abs(total - 1.0) <= 1e-6, f"{persona} preset sums to {total}"


def test_validate_persona_preset_covers_all_pillar_keys():
    for persona in Persona:
        assert set(PERSONA_PRESETS[persona]) == set(PILLAR_KEYS)


def test_validate_persona_preset_rejects_bad_weights():
    # The helper validates any weight dict via the same rule: prove the
    # underlying check fires on a broken preset-shaped dict.
    from cvi.persona import _validate_weights

    with pytest.raises(ValueError, match="exactly the 5 pillar keys"):
        _validate_weights({"responsiveness": 1.0})
    with pytest.raises(ValueError, match="sum to 1.0"):
        _validate_weights(
            {
                "responsiveness": 0.5,
                "criticality": 0.5,
                "governance": 0.5,
                "rigor": 0.0,
                "accessibility": 0.0,
            }
        )


# --------------------------------------------------------------------------- #
# reproject: weight-sum tolerance boundary at exactly 1e-6 deviation
# (gap-closing)
# --------------------------------------------------------------------------- #
def test_reproject_weight_sum_boundary_at_exactly_1e6_deviation():
    # Pin the weight-sum tolerance boundary exactly as the current production
    # code behaves (IEEE-754 asymmetry around 1.0):
    #   fl(1.0 + 1e-6) = 1.000001   -> |1.000001 - 1.0| = 9.999999999177334e-07
    #                   <= 1e-6 -> accepted
    #   fl(1.0 - 1e-6) = 0.999999   -> |0.999999 - 1.0| = 1.0000000000287557e-06
    #                   >  1e-6 -> rejected (the low-side deviation rounds
    #                   up past the tolerance)
    #   fl(1.0 + 1.1e-6) = 1.0000011 -> deviation 1.09999999997612e-06
    #                   > 1e-6 -> rejected
    # Each dict carries the whole sum in one key so that
    # sum(weights.values()) equals the target float exactly.
    ps = _ps(80, 90, 70, 60, 50)

    def weights_total(total: float) -> dict[str, float]:
        return {
            "responsiveness": total,
            "criticality": 0.0,
            "governance": 0.0,
            "rigor": 0.0,
            "accessibility": 0.0,
        }

    w_plus = weights_total(1.0 + 1e-6)
    assert sum(w_plus.values()) == 1.0 + 1e-6
    # 1.000001 * 80 = 80.00008 -> rounded to 80.0
    assert reproject(ps, w_plus) == 80.0

    w_minus = weights_total(1.0 - 1e-6)
    assert sum(w_minus.values()) == 1.0 - 1e-6
    with pytest.raises(ValueError, match="sum to 1.0"):
        reproject(ps, w_minus)

    w_plus_1_1 = weights_total(1.0 + 1.1e-6)
    assert sum(w_plus_1_1.values()) == 1.0 + 1.1e-6
    with pytest.raises(ValueError, match="sum to 1.0"):
        reproject(ps, w_plus_1_1)


# --------------------------------------------------------------------------- #
# rank_deltas: old-ranking tie-break (gap-closing)
# --------------------------------------------------------------------------- #
def test_rank_deltas_old_ranking_tie_broken_by_before_position_not_name():
    # Pin the OLD-ranking tie-break in rank_deltas: scores_before contains a
    # tie (zeta and alpha both 40.0). zeta appears earlier in scores_before
    # (index 0) than alpha (index 1), yet "alpha" < "zeta" alphabetically,
    # and the new snapshot breaks the tie (alpha climbs to 45.0). A
    # name-ordered (or position-ignoring) old ranking would change the
    # zeta/alpha deltas.
    before = [("zeta", 40.0), ("alpha", 40.0), ("mid", 80.0)]
    after = {"zeta": 40.0, "alpha": 45.0, "mid": 80.0}
    # old ranks: mid=1; the 40.0 tie is broken by position in scores_before:
    #   zeta (earlier) = 2, alpha = 3.
    # new ranks: mid=1, alpha=2 (45.0), zeta=3 (40.0).
    # deltas: zeta 2-3=-1, alpha 3-2=+1, mid 1-1=0.
    # (a name-order old tie-break would give both zeta and alpha delta 0)
    assert rank_deltas(before, after) == {"zeta": -1, "alpha": 1, "mid": 0}
