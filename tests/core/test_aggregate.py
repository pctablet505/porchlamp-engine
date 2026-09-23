"""Pillar renormalisation, coverage, ceilings and composite arithmetic
(02 sections 5, 7; D-MISS-2/3, D-AGG-2, D-MAT-2's tier/ceiling arithmetic).

Oracles are hand-computed and asserted as exact values, not shapes: each
docstring below shows the arithmetic.
"""
from __future__ import annotations

import pytest

from porchlamp.core import constants
from porchlamp.core.aggregate import (
    CEILING_A,
    CEILING_B,
    TIER_FLOOR,
    aggregate_pillars,
    apply_ceiling,
    composite_coverage,
    composite_score,
    composite_score_raw,
    coverage_status,
    tier_from_score,
    validate_pillar_weights,
)
from porchlamp.core.models import ParamScore, PillarScore
from porchlamp.core.params import PARAMS


def _measured(score: float, *, n: int = 100, method: str = "primary") -> ParamScore:
    return ParamScore(
        value=score,
        score=score,
        measured=True,
        reason=None,
        n=n,
        window_start=None,
        window_end=None,
        method=method,
        source="test",
        fetched_at="2026-09-01T00:00:00+00:00",
    )


def _unmeasured(reason: str = "insufficient_n") -> ParamScore:
    return ParamScore(
        value=None,
        score=None,
        measured=False,
        reason=reason,
        n=0,
        window_start=None,
        window_end=None,
        method=None,
        source=None,
        fetched_at=None,
    )


def _not_applicable() -> ParamScore:
    """D-MISS-5: a stage-2 score this module trusts as ``not_applicable``
    (the closed-list guard that may only assign this reason lives in
    ``porchlamp.core.engine``, upstream of ``aggregate_pillars``)."""
    return _unmeasured(reason="not_applicable")


def full_params(overrides: dict[str, ParamScore] | None = None) -> dict[str, ParamScore]:
    """All 18 params measured at score 50.0, with ``overrides`` applied.
    A neutral baseline the pillar-level oracles override selectively."""
    base = {input_id: _measured(50.0) for input_id in constants.PARAM_INPUT_IDS}
    if overrides:
        base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# aggregate_pillars: D-MISS-2 renormalisation and coverage.
# --------------------------------------------------------------------------- #


def test_pillar_with_two_of_three_measured_renormalises_exactly() -> None:
    """Governance has three params: legal_ip (w=.35), elephant_factor
    (w=.35), promotion_ladder (w=.30). With elephant_factor unmeasured:
    score = (.35*100 + .30*40) / (.35+.30) = 47.0 / 0.65 = 940/13
    coverage = 0.65 / 1.00 = 0.65
    """
    params = full_params(
        {
            "legal_ip": _measured(100.0),
            "elephant_factor": _unmeasured(),
            "promotion_ladder": _measured(40.0),
        }
    )
    pillars = aggregate_pillars(params)
    governance = pillars["governance"]
    assert governance.score == pytest.approx(940.0 / 13.0)
    assert governance.coverage == pytest.approx(0.65)


def test_pillar_fully_measured_has_coverage_one() -> None:
    params = full_params()
    pillars = aggregate_pillars(params)
    for pillar in constants.PILLAR_NAMES:
        assert pillars[pillar].coverage == pytest.approx(1.0)
        assert pillars[pillar].score == pytest.approx(50.0)


def test_pillar_with_nothing_measured_is_unmeasured_and_drops_out() -> None:
    """D-MISS-2: "If no parameter in a pillar is measured, the pillar is
    unmeasured". Rigor has three params (4.1, 4.2, 4.3); mark all three
    unmeasured."""
    rigor_ids = [input_id for input_id, defn in PARAMS.items() if defn.pillar == "rigor"]
    assert len(rigor_ids) == 3
    params = full_params({input_id: _unmeasured() for input_id in rigor_ids})
    pillars = aggregate_pillars(params)
    assert pillars["rigor"].score is None
    assert pillars["rigor"].coverage == pytest.approx(0.0)


def test_pillar_not_applicable_parameter_excluded_from_both_sides() -> None:
    """D-MISS-5: dependents_deduped (w=.40) not_applicable, the other two
    criticality params (openssf_criticality w=.35, downloads_monthly w=.25)
    measured at 50.0. Coverage excludes the not_applicable weight from BOTH
    sides: coverage = 0.60 / 0.60 = 1.00 (not 0.60 / 1.00), and the pillar
    is still applicable=True (2 of 3 params are)."""
    params = full_params({"dependents_deduped": _not_applicable()})
    pillars = aggregate_pillars(params)
    criticality = pillars["criticality"]
    assert criticality.coverage == pytest.approx(1.0)
    assert criticality.score == pytest.approx(50.0)
    assert criticality.applicable is True


def test_pillar_third_party_gap_is_unchanged_from_not_applicable() -> None:
    """Over-correction guard / D-MISS-5's "other four reasons unchanged":
    third_party_gap (unlike not_applicable) still counts fully against
    coverage -- 0.60 / 1.00 = 0.60."""
    params = full_params({"dependents_deduped": _unmeasured(reason="third_party_gap")})
    pillars = aggregate_pillars(params)
    criticality = pillars["criticality"]
    assert criticality.coverage == pytest.approx(0.60)
    assert criticality.applicable is True


def test_pillar_wholly_not_applicable_is_itself_not_applicable() -> None:
    """D-MISS-5: when every parameter in a pillar is not_applicable, the
    pillar itself is inapplicable (not merely 0% covered) -- score is None
    (as any wholly-unmeasured pillar already was) but applicable is now
    False, so composite_coverage excludes it from its own denominator
    rather than counting it as 0 coverage against the pillar's full weight.
    (Hypothetical: today's only NOT_APPLICABLE_RULES row assigns
    not_applicable to a single parameter, 2.1; this exercises the
    aggregate-level mechanism independent of which/how many parameters a
    future rule might cover.)"""
    criticality_ids = [input_id for input_id, defn in PARAMS.items() if defn.pillar == "criticality"]
    assert len(criticality_ids) == 3
    params = full_params({input_id: _not_applicable() for input_id in criticality_ids})
    pillars = aggregate_pillars(params)
    assert pillars["criticality"].score is None
    assert pillars["criticality"].coverage == pytest.approx(0.0)
    assert pillars["criticality"].applicable is False


def test_aggregate_pillars_requires_exactly_the_eighteen_ids() -> None:
    params = full_params()
    del params["dx"]
    with pytest.raises(ValueError, match="dx"):
        aggregate_pillars(params)
    params = full_params()
    params["not_a_param"] = _measured(1.0)
    with pytest.raises(ValueError, match="not_a_param"):
        aggregate_pillars(params)


# --------------------------------------------------------------------------- #
# composite_coverage: D-MISS-3 C = Sum_p(W_p * c_p).
# --------------------------------------------------------------------------- #


def test_composite_coverage_arithmetic() -> None:
    """Balanced weights .25/.25/.20/.15/.15; hand-picked clean coverages:
    C = .25*1.0 + .25*0.5 + .20*0.65 + .15*1.0 + .15*0.0
      = 0.25 + 0.125 + 0.13 + 0.15 + 0.0 = 0.655
    """
    pillars = {
        "responsiveness": PillarScore(score=80.0, coverage=1.0),
        "criticality": PillarScore(score=80.0, coverage=0.5),
        "governance": PillarScore(score=80.0, coverage=0.65),
        "rigor": PillarScore(score=80.0, coverage=1.0),
        "accessibility": PillarScore(score=None, coverage=0.0),
    }
    assert composite_coverage(pillars) == pytest.approx(0.655)


def test_composite_coverage_systems_repo_end_to_end_reaches_one() -> None:
    """D-MISS-5 oracle: a systems repo with everything else measured
    reaches C = 1.00 with no ceiling. Full pipeline through
    aggregate_pillars, not a hand-built PillarScore mapping."""
    params = full_params({"dependents_deduped": _not_applicable()})
    pillars = aggregate_pillars(params)
    assert composite_coverage(pillars) == pytest.approx(1.00)


def test_composite_coverage_third_party_gap_end_to_end_unchanged() -> None:
    """The other oracle: a pypi repo with a deps.dev third_party_gap keeps
    the gap counted -- C is 0.90."""
    params = full_params({"dependents_deduped": _unmeasured(reason="third_party_gap")})
    pillars = aggregate_pillars(params)
    assert composite_coverage(pillars) == pytest.approx(0.90)


def test_composite_coverage_excludes_wholly_not_applicable_pillar_from_denominator() -> None:
    """D-MISS-5: one whole pillar not_applicable (criticality, W=.25) ->
    composite coverage is computed over the remaining four pillars, weights
    renormalised: C = (.25*1 + .20*1 + .15*1 + .15*1) / (.25+.20+.15+.15)
    = 0.75 / 0.75 = 1.00 (every remaining pillar fully covered), not
    0.75, which is what an unconditional Sum_p(W_p*c_p) gives by treating
    the excluded pillar as 0 coverage against its weight."""
    criticality_ids = [input_id for input_id, defn in PARAMS.items() if defn.pillar == "criticality"]
    params = full_params({input_id: _not_applicable() for input_id in criticality_ids})
    pillars = aggregate_pillars(params)
    assert pillars["criticality"].applicable is False
    assert composite_coverage(pillars) == pytest.approx(1.00)


def test_composite_coverage_all_pillars_not_applicable_is_zero() -> None:
    """Defensive floor: every pillar inapplicable (a construction no real
    rule list produces today) returns 0.0 rather than dividing by zero."""
    pillars = {pillar: PillarScore(score=None, coverage=0.0, applicable=False) for pillar in constants.PILLAR_NAMES}
    assert composite_coverage(pillars) == 0.0


# --------------------------------------------------------------------------- #
# coverage_status: D-EVID-1 unrated boundary + D-MISS-3 ceiling bands.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("coverage", "expected"),
    [
        (0.59, (False, None)),
        (0.60, (True, CEILING_B)),
        (0.79, (True, CEILING_B)),
        (0.80, (True, CEILING_A)),
        (0.94, (True, CEILING_A)),
        (0.95, (True, None)),
        (1.00, (True, None)),
    ],
)
def test_coverage_status_boundaries(coverage: float, expected: tuple[bool, str | None]) -> None:
    assert coverage_status(coverage) == expected


# --------------------------------------------------------------------------- #
# composite_score: D-AGG-2, rounded once at the end.
# --------------------------------------------------------------------------- #


def test_composite_score_renormalises_over_measured_pillars_only() -> None:
    """Accessibility unmeasured; the remaining four weights are
    .25+.25+.20+.15 = 0.85.
    raw = (.25*80 + .25*80 + .20*80 + .15*80) / 0.85 = 68.0 / 0.85 = 80.0
    """
    pillars = {
        "responsiveness": PillarScore(score=80.0, coverage=1.0),
        "criticality": PillarScore(score=80.0, coverage=1.0),
        "governance": PillarScore(score=80.0, coverage=1.0),
        "rigor": PillarScore(score=80.0, coverage=1.0),
        "accessibility": PillarScore(score=None, coverage=0.0),
    }
    assert composite_score_raw(pillars) == pytest.approx(80.0)
    assert composite_score(pillars) == pytest.approx(80.0)


def test_composite_score_none_when_every_pillar_unmeasured() -> None:
    pillars = {pillar: PillarScore(score=None, coverage=0.0) for pillar in constants.PILLAR_NAMES}
    assert composite_score_raw(pillars) is None
    assert composite_score(pillars) is None


def test_composite_score_rounds_the_final_sum_not_a_per_pillar_value() -> None:
    """rigor = 100/7 = 14.285714... repeating; the other four pillars are
    zero. raw = .15 * 100/7 = 15/7 = 2.142857142857143 (full precision, not
    the 2-decimal-rounded 14.29 a per-pillar-rounding bug would combine);
    composite_score rounds this raw sum once, to 2.14."""
    pillars = {
        "responsiveness": PillarScore(score=0.0, coverage=1.0),
        "criticality": PillarScore(score=0.0, coverage=1.0),
        "governance": PillarScore(score=0.0, coverage=1.0),
        "rigor": PillarScore(score=100.0 / 7.0, coverage=1.0),
        "accessibility": PillarScore(score=0.0, coverage=1.0),
    }
    raw = 0.15 * (100.0 / 7.0)
    assert composite_score_raw(pillars) == pytest.approx(raw)
    assert composite_score_raw(pillars) == pytest.approx(15.0 / 7.0)
    assert composite_score(pillars) == round(raw, 2)
    assert composite_score(pillars) == 2.14


# --------------------------------------------------------------------------- #
# tier_from_score / apply_ceiling: D-MAT-2.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("composite", "expected"),
    [
        (100.0, "S+"),
        (90.0, "S+"),
        (89.99, "S"),
        (80.0, "S"),
        (79.99, "A"),
        (70.0, "A"),
        (69.99, "B"),
        (55.0, "B"),
        (54.99, "C"),
        (40.0, "C"),
        (39.99, TIER_FLOOR),
        (0.0, TIER_FLOOR),
    ],
)
def test_tier_from_score_cutoffs(composite: float, expected: str) -> None:
    assert tier_from_score(composite) == expected


def test_score_92_with_coverage_085_yields_tier_a() -> None:
    """Combined oracle: raw tier for 92 is S+ (>=90); coverage 0.85
    gives ceiling A (0.80 <= C < 0.95); the worse of the two is A."""
    tier = tier_from_score(92.0)
    assert tier == "S+"
    rated, ceiling = coverage_status(0.85)
    assert (rated, ceiling) == (True, "A")
    assert apply_ceiling(tier, ceiling) == "A"


@pytest.mark.parametrize(
    ("tier", "ceiling", "expected"),
    [
        ("S+", None, "S+"),
        ("S+", "A", "A"),
        ("S+", "B", "B"),
        ("A", "B", "B"),
        ("B", "A", "B"),
        ("C", "A", "C"),
        ("D", "B", "D"),
    ],
)
def test_apply_ceiling_takes_the_worse_tier(tier: str, ceiling: str | None, expected: str) -> None:
    assert apply_ceiling(tier, ceiling) == expected


# --------------------------------------------------------------------------- #
# ParamScore.value: required, no default.
# --------------------------------------------------------------------------- #


def test_param_score_requires_value_no_default() -> None:
    """A constructor site that forgets ``value`` must fail loudly: the
    field is required and has no default."""
    with pytest.raises(TypeError, match="value"):
        ParamScore(  # type: ignore[call-arg]
            score=50.0,
            measured=True,
            reason=None,
            n=10,
            window_start=None,
            window_end=None,
            method="primary",
            source="test",
            fetched_at="2026-09-01T00:00:00+00:00",
        )


# --------------------------------------------------------------------------- #
# composite_score_raw / composite_coverage: the ``weights`` keyword --
# default is the frozen balanced weights (arithmetic proved above); a
# caller-supplied vector must be validated and used instead of the
# hard-coded balanced weights.
# --------------------------------------------------------------------------- #

_CUSTOM_WEIGHTS = {
    "responsiveness": 0.40,
    "criticality": 0.10,
    "governance": 0.10,
    "rigor": 0.10,
    "accessibility": 0.30,
}


def test_composite_score_raw_honours_a_custom_weight_vector() -> None:
    """All five pillars measured at a distinct score; hand-computed against
    ``_CUSTOM_WEIGHTS`` (which sums to 1.0), not the balanced default:
    raw = .40*10 + .10*20 + .10*30 + .10*40 + .30*50 = 4+2+3+4+15 = 28.0
    -- the balanced-weight result would be .25*10+.25*20+.20*30+.15*40+.15*50
    = 2.5+5+6+6+7.5 = 27.0, a different number, so this proves ``weights``
    is actually used, not silently ignored."""
    pillars = {
        "responsiveness": PillarScore(score=10.0, coverage=1.0),
        "criticality": PillarScore(score=20.0, coverage=1.0),
        "governance": PillarScore(score=30.0, coverage=1.0),
        "rigor": PillarScore(score=40.0, coverage=1.0),
        "accessibility": PillarScore(score=50.0, coverage=1.0),
    }
    assert composite_score_raw(pillars) == pytest.approx(27.0)
    assert composite_score_raw(pillars, weights=_CUSTOM_WEIGHTS) == pytest.approx(28.0)


def test_composite_coverage_honours_a_custom_weight_vector() -> None:
    """Same five pillars, coverage-only: C = .40*1.0+.10*0.5+.10*0.0+.10*1.0+.30*0.0 = 0.55."""
    pillars = {
        "responsiveness": PillarScore(score=10.0, coverage=1.0),
        "criticality": PillarScore(score=20.0, coverage=0.5),
        "governance": PillarScore(score=30.0, coverage=0.0),
        "rigor": PillarScore(score=40.0, coverage=1.0),
        "accessibility": PillarScore(score=50.0, coverage=0.0),
    }
    assert composite_coverage(pillars, weights=_CUSTOM_WEIGHTS) == pytest.approx(0.55)


def test_composite_score_raw_rejects_weights_not_summing_to_one() -> None:
    bad_weights = dict(_CUSTOM_WEIGHTS)
    bad_weights["responsiveness"] = 0.50  # now sums to 1.10
    pillars = {pillar: PillarScore(score=50.0, coverage=1.0) for pillar in constants.PILLAR_NAMES}
    with pytest.raises(ValueError, match="must sum to 1.0"):
        composite_score_raw(pillars, weights=bad_weights)


def test_composite_coverage_rejects_weights_not_summing_to_one() -> None:
    bad_weights = dict(_CUSTOM_WEIGHTS)
    bad_weights["responsiveness"] = 0.50  # now sums to 1.10
    pillars = {pillar: PillarScore(score=50.0, coverage=1.0) for pillar in constants.PILLAR_NAMES}
    with pytest.raises(ValueError, match="must sum to 1.0"):
        composite_coverage(pillars, weights=bad_weights)


def test_composite_score_raw_rejects_weights_missing_a_pillar() -> None:
    incomplete = {k: v for k, v in _CUSTOM_WEIGHTS.items() if k != "accessibility"}
    pillars = {pillar: PillarScore(score=50.0, coverage=1.0) for pillar in constants.PILLAR_NAMES}
    with pytest.raises(ValueError, match="must cover exactly"):
        composite_score_raw(pillars, weights=incomplete)


def test_validate_pillar_weights_accepts_the_frozen_balanced_default() -> None:
    """No exception -- the balanced weights are always a valid vector."""
    validate_pillar_weights(constants.PILLAR_WEIGHTS)


def test_validate_pillar_weights_rejects_an_extra_pillar() -> None:
    extra = dict(constants.PILLAR_WEIGHTS)
    extra["extra_pillar"] = 0.0
    with pytest.raises(ValueError, match="must cover exactly"):
        validate_pillar_weights(extra)


def test_validate_pillar_weights_rejects_a_negative_weight_even_if_the_sum_is_1(tmp_path=None) -> None:
    """A negative weight offset by a larger positive one can still sum to
    exactly 1.0 -- validation must still raise, naming the offending
    pillar, not just check the sum."""
    negative = dict(constants.PILLAR_WEIGHTS)
    negative["responsiveness"] = -0.5
    negative["criticality"] = negative["criticality"] + 0.5 + constants.PILLAR_WEIGHTS["responsiveness"]
    assert abs(sum(negative.values()) - 1.0) < 1e-9  # sanity: the sum check alone would not catch this
    with pytest.raises(ValueError, match="non-negative"):
        validate_pillar_weights(negative)


def test_validate_pillar_weights_accepts_a_zero_weight() -> None:
    """Over-correction guard: zero is not negative -- a pillar dropped to
    zero weight (never scored under this vector) must still validate."""
    zeroed = dict(constants.PILLAR_WEIGHTS)
    dropped = zeroed.pop("accessibility")
    zeroed["accessibility"] = 0.0
    zeroed["responsiveness"] = zeroed["responsiveness"] + dropped
    validate_pillar_weights(zeroed)  # must not raise
