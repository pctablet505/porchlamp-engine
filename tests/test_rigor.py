"""Tests for Pillar 4 — Engineering Rigor & AI Alignment.

Owner test bar: exact-oracle assertions on the calibrated formulas from
``docs/ARCHITECTURE.md`` sections 4.1-4.3. Every asserted value is the
computed expected value, not a property a no-op implementation also
satisfies.

Oracle summary:
  4.1 Pipeline:    (scorecard / SCORECARD_MAX) * 100, clamped to [0, 100].
  4.2 AI posture:   AI_POSTURE_SCORES[posture]["ai"|"human"] — persona-conditional.
  4.3 Review depth: 100 * exp(-0.5 * ((mean - 3.5) / 1.5) ** 2).
  Pillar:           0.40*pipeline + 0.35*ai_policy + 0.25*review_depth,
                    clamped to [0, 100].

Spec-prose note (4.3): the spec claims the range [2.5, 4.5] "yields > 80
points". The exact value at BOTH endpoints is 100 * exp(-0.5 * (1/1.5)**2)
≈ 80.0737 — barely above 80, so the prose holds but only by ~0.07 pts.
Tests assert the exact computed value, not the prose's "> 80" rounding.
"""

import math

import pytest

from cvi.constants import (
    AI_POSTURE_SCORES,
    PARAM_WEIGHTS,
    PILLAR_WEIGHTS,
    REVIEW_DEPTH_OPTIMAL,
    REVIEW_DEPTH_SIGMA,
    SCORECARD_MAX,
)
from cvi.models import AIPosture, Ecosystem, RepoMeta
from cvi.pillars.rigor import (
    score_ai_posture,
    score_pipeline,
    score_review_depth,
    score_rigor,
)


def make_meta(**overrides) -> RepoMeta:
    """RepoMeta with pillar-4 fields set; defaults are the raw zero signals."""
    kwargs = dict(owner="acme", name="widget", ecosystem=Ecosystem.PYPI)
    kwargs.update(overrides)
    return RepoMeta(**kwargs)


def gauss(mean: float) -> float:
    """Exact oracle for 4.3, computed independently in the test."""
    return 100.0 * math.exp(-0.5 * ((mean - 3.5) / 1.5) ** 2)


# --------------------------------------------------------------------------- #
# 4.1 Automated Pipeline Defenses
# --------------------------------------------------------------------------- #
class TestScorePipeline:
    def test_zero(self):
        assert score_pipeline(0.0) == 0.0

    def test_half(self):
        assert score_pipeline(5.0) == 50.0

    def test_full_at_max(self):
        assert score_pipeline(10.0) == 100.0

    def test_above_max_clamps_to_100(self):
        # Spec: scorecard above 10 must not exceed 100.
        assert score_pipeline(12.0) == 100.0

    def test_far_above_max_stays_100(self):
        assert score_pipeline(99.0) == 100.0

    def test_negative_clamps_to_0(self):
        assert score_pipeline(-4.0) == 0.0

    def test_fractional(self):
        assert score_pipeline(7.5) == 75.0


# --------------------------------------------------------------------------- #
# 4.2 Calibrated AI Policy Posture
# --------------------------------------------------------------------------- #
class TestScoreAiPosture:
    def test_regulated_human(self):
        assert score_ai_posture(make_meta(ai_posture=AIPosture.REGULATED)) == 95.0

    def test_regulated_ai(self):
        assert score_ai_posture(make_meta(ai_posture=AIPosture.REGULATED), True) == 95.0

    def test_proscribed_human(self):
        assert score_ai_posture(make_meta(ai_posture=AIPosture.PROSCRIBED)) == 85.0

    def test_proscribed_ai(self):
        assert score_ai_posture(make_meta(ai_posture=AIPosture.PROSCRIBED), True) == 25.0

    def test_ad_hoc_human(self):
        assert score_ai_posture(make_meta(ai_posture=AIPosture.AD_HOC)) == 40.0

    def test_ad_hoc_ai(self):
        assert score_ai_posture(make_meta(ai_posture=AIPosture.AD_HOC), True) == 40.0

    def test_default_posture_is_ad_hoc(self):
        # RepoMeta default is AD_HOC; a bare meta must not KeyError or drift.
        assert score_ai_posture(make_meta()) == 40.0

    @pytest.mark.parametrize(
        ("posture", "is_ai", "expected"),
        [
            (AIPosture.REGULATED, False, 95.0),
            (AIPosture.REGULATED, True, 95.0),
            (AIPosture.PROSCRIBED, False, 85.0),
            (AIPosture.PROSCRIBED, True, 25.0),
            (AIPosture.AD_HOC, False, 40.0),
            (AIPosture.AD_HOC, True, 40.0),
        ],
    )
    def test_matches_constants_table(self, posture, is_ai, expected):
        # Independent oracle: the constants table itself, keyed by enum value.
        assert score_ai_posture(make_meta(ai_posture=posture), is_ai) == (
            AI_POSTURE_SCORES[posture.value]["ai" if is_ai else "human"]
        )
        assert expected == AI_POSTURE_SCORES[posture.value]["ai" if is_ai else "human"]


# --------------------------------------------------------------------------- #
# 4.3 Review Depth & Substantive Guidance
# --------------------------------------------------------------------------- #
class TestScoreReviewDepth:
    def test_optimum_is_100(self):
        assert score_review_depth(3.5) == 100.0

    @pytest.mark.parametrize(
        "mean", [0.0, 2.0, 2.5, 3.5, 4.5, 5.0, 8.0]
    )
    def test_exact_gaussian_oracle(self, mean):
        # Hand-computed exp values, asserted to 4 decimals.
        assert score_review_depth(mean) == pytest.approx(gauss(mean), abs=1e-4)

    def test_at_2_5_exact(self):
        # 100 * exp(-0.5 * (1/1.5)**2) ≈ 80.0737 — the spec's "> 80" claim
        # holds at the endpoint, but only by ~0.07 pts.
        assert score_review_depth(2.5) == pytest.approx(80.0737, abs=1e-4)

    def test_at_4_5_exact(self):
        assert score_review_depth(4.5) == pytest.approx(80.0737, abs=1e-4)

    def test_symmetry_one_sigma(self):
        # 3.5 - 1.5 and 3.5 + 1.5 must score identically.
        assert score_review_depth(2.0) == score_review_depth(5.0)

    def test_symmetry_two_sigma(self):
        assert score_review_depth(0.5) == score_review_depth(6.5)

    def test_far_tail_near_zero(self):
        # 8.0 is ~3 sigma out: 100 * exp(-0.5 * (4.5/1.5)**2) ≈ 1.1109.
        assert score_review_depth(8.0) == pytest.approx(1.1109, abs=1e-4)

    def test_negative_mean_scores_low_not_crash(self):
        # Negative means are impossible in practice but must not raise;
        # they fall on the far left tail.
        assert score_review_depth(-10.0) == pytest.approx(
            gauss(-10.0), abs=1e-4
        )
        assert score_review_depth(-10.0) < 1.0

    def test_monotone_on_left_side(self):
        # Approaching the optimum from below must strictly increase.
        assert score_review_depth(0.0) < score_review_depth(2.0) < score_review_depth(3.5)

    def test_monotone_on_right_side(self):
        assert score_review_depth(3.5) > score_review_depth(5.0) > score_review_depth(8.0)


# --------------------------------------------------------------------------- #
# Calibrated pillar combination
# --------------------------------------------------------------------------- #
class TestScoreRigor:
    def test_perfect_repo(self):
        # 0.40*100 + 0.35*95 + 0.25*100 = 98.25
        meta = make_meta(
            scorecard_score=10.0,
            ai_posture=AIPosture.REGULATED,
            mean_inline_comments=3.5,
        )
        assert score_rigor(meta) == pytest.approx(98.25, abs=1e-9)

    def test_all_zero_signals(self):
        # 0.40*0 + 0.35*40 (AD_HOC default) + 0.25*gauss(0.0)
        # = 14.0 + 0.25*6.572852861653045 = 15.643214154132561
        expected = 0.35 * 40.0 + 0.25 * gauss(0.0)
        assert score_rigor(make_meta()) == pytest.approx(expected, abs=1e-9)

    def test_proscribed_ai_persona(self):
        # 0.40*50 + 0.35*25 + 0.25*100 = 53.75
        meta = make_meta(
            scorecard_score=5.0,
            ai_posture=AIPosture.PROSCRIBED,
            mean_inline_comments=3.5,
        )
        assert score_rigor(meta, persona_is_ai=True) == pytest.approx(53.75, abs=1e-9)

    def test_proscribed_human_persona(self):
        # 0.40*50 + 0.35*85 + 0.25*100 = 74.75
        meta = make_meta(
            scorecard_score=5.0,
            ai_posture=AIPosture.PROSCRIBED,
            mean_inline_comments=3.5,
        )
        assert score_rigor(meta, persona_is_ai=False) == pytest.approx(74.75, abs=1e-9)

    def test_ad_hoc_worst_review_depth(self):
        # 0.40*0 + 0.35*40 + 0.25*gauss(0.0) = 15.643214154132561
        meta = make_meta(
            scorecard_score=0.0,
            ai_posture=AIPosture.AD_HOC,
            mean_inline_comments=0.0,
        )
        expected = 0.35 * 40.0 + 0.25 * gauss(0.0)
        assert score_rigor(meta) == pytest.approx(expected, abs=1e-9)

    def test_scorecard_above_max_clamps_in_pillar(self):
        # 12 clamps to 100 in 4.1: 0.40*100 + 0.35*95 + 0.25*100 = 98.25
        meta = make_meta(
            scorecard_score=12.0,
            ai_posture=AIPosture.REGULATED,
            mean_inline_comments=3.5,
        )
        assert score_rigor(meta) == pytest.approx(98.25, abs=1e-9)

    def test_mixed_signals(self):
        # 0.40*75 + 0.35*85 + 0.25*gauss(5.0)
        # = 30.0 + 29.75 + 0.25*60.653065971263345 = 64.91326649281584
        meta = make_meta(
            scorecard_score=7.5,
            ai_posture=AIPosture.PROSCRIBED,
            mean_inline_comments=5.0,
        )
        expected = 0.40 * 75.0 + 0.35 * 85.0 + 0.25 * gauss(5.0)
        assert score_rigor(meta) == pytest.approx(expected, abs=1e-9)

    def test_pipeline_weight_isolation(self):
        # Only 4.1 varies: 0.40*100 + 0.35*40 + 0.25*gauss(0.0)
        meta = make_meta(
            scorecard_score=10.0,
            ai_posture=AIPosture.AD_HOC,
            mean_inline_comments=0.0,
        )
        expected = 0.40 * 100.0 + 0.35 * 40.0 + 0.25 * gauss(0.0)
        assert score_rigor(meta) == pytest.approx(expected, abs=1e-9)

    def test_review_depth_weight_isolation(self):
        # Only 4.3 varies: 0.40*0 + 0.35*40 + 0.25*100 = 39.0
        meta = make_meta(
            scorecard_score=0.0,
            ai_posture=AIPosture.AD_HOC,
            mean_inline_comments=3.5,
        )
        assert score_rigor(meta) == pytest.approx(39.0, abs=1e-9)


# --------------------------------------------------------------------------- #
# Persona flip
# --------------------------------------------------------------------------- #
class TestPersonaFlip:
    def test_proscribed_delta_exact(self):
        # 85 - 25 = 60 parameter points; weighted delta = 0.35 * 60 = 21.0
        meta = make_meta(
            scorecard_score=5.0,
            ai_posture=AIPosture.PROSCRIBED,
            mean_inline_comments=3.5,
        )
        human = score_rigor(meta, persona_is_ai=False)
        ai = score_rigor(meta, persona_is_ai=True)
        assert human - ai == pytest.approx(21.0, abs=1e-9)

    def test_regulated_no_delta(self):
        meta = make_meta(
            scorecard_score=8.0,
            ai_posture=AIPosture.REGULATED,
            mean_inline_comments=4.0,
        )
        assert score_rigor(meta, persona_is_ai=False) == score_rigor(
            meta, persona_is_ai=True
        )

    def test_ad_hoc_no_delta(self):
        meta = make_meta(
            scorecard_score=3.0,
            ai_posture=AIPosture.AD_HOC,
            mean_inline_comments=2.0,
        )
        assert score_rigor(meta, persona_is_ai=False) == score_rigor(
            meta, persona_is_ai=True
        )

    def test_flip_only_moves_ai_policy_term(self):
        # With 4.1 and 4.3 held fixed, the flip must change the total by
        # exactly the weighted 4.2 delta and nothing else.
        meta = make_meta(
            scorecard_score=6.0,
            ai_posture=AIPosture.PROSCRIBED,
            mean_inline_comments=3.5,
        )
        base = 0.40 * 60.0 + 0.25 * 100.0  # 4.1 + 4.3, persona-invariant
        assert score_rigor(meta, persona_is_ai=False) == pytest.approx(
            base + 0.35 * 85.0, abs=1e-9
        )
        assert score_rigor(meta, persona_is_ai=True) == pytest.approx(
            base + 0.35 * 25.0, abs=1e-9
        )


# --------------------------------------------------------------------------- #
# Constants contract (guards against silent weight drift)
# --------------------------------------------------------------------------- #
class TestConstantsContract:
    def test_rigor_param_weights_sum_to_one(self):
        assert sum(PARAM_WEIGHTS["rigor"].values()) == pytest.approx(1.0, abs=1e-12)

    def test_rigor_param_weights_match_spec(self):
        assert PARAM_WEIGHTS["rigor"] == {
            "pipeline": 0.40,
            "ai_policy": 0.35,
            "review_depth": 0.25,
        }

    def test_pillar_weight_is_15_percent(self):
        assert PILLAR_WEIGHTS["rigor"] == 0.15

    def test_review_depth_constants_match_spec(self):
        assert REVIEW_DEPTH_OPTIMAL == 3.5
        assert REVIEW_DEPTH_SIGMA == 1.5
        assert SCORECARD_MAX == 10.0
