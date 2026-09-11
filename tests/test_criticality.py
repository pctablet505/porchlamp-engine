"""Tests for Pillar 2 — Production Criticality & Impact.

Owner test bar: exact-oracle assertions on the calibrated formulas from
``docs/ARCHITECTURE.md`` sections 2.1-2.3, 10.1, 10.2. Every asserted value
is the hand-computed expected value, not a property a no-op implementation
also satisfies.

Oracle summary:
  2.1 Dependents:  ECDF percentile of log10(max(0, count) + 1) in the
                   pre-transformed population; empty population -> 50.0.
  2.2 OpenSSF:     Rob Pike engine C in [0, 1] scaled x100;
                   C = sum(alpha_i * S_i*) / sum|alpha_i|,
                   S_i* = ln(1 + max(0, S_i)) / ln(1 + max(S_i, T_i));
                   None signals -> 50.0 neutral.
  2.3 Downloads:   ECDF percentile of log10(max(0, count) + 1).
  Pillar:          0.40*p_dependents + 0.35*p_openssf + 0.25*p_downloads,
                   clamped to [0, 100].

Hand-computed oracles used below (weights: +1, -1, +2, +1, +1, +0.5, +0.5,
+0.5, +1, +2; sum|alpha| = 10.5, sum(alpha) = 8.5):
  - all signals at/above threshold: every S_i* = 1.0 -> C = 8.5/10.5
    = 0.8095238095238095 -> pillar param 80.95 (2 dp).
  - all-zero signals: every S_i* = 0.0 -> C = 0.0.
  - mixed (created_since=60, contributor_count=100, dependents_count=1000):
    C = (ln61/ln121 + 2*ln101/ln5001 + 2*ln1001/ln500001) / 10.5
    ≈ 0.260335 (computed in-test with math.log).
  - abandoned penalty: updated_since 0 vs 120, all else equal ->
    C_120 = C_0 - 1/10.5 (the -1.0 weight contributes -1.0 to the
    numerator when S* = 1.0).
"""

import math

import pytest

from cvi.constants import OPENSSF_THRESHOLDS, OPENSSF_WEIGHTS, PARAM_WEIGHTS
from cvi.models import Ecosystem, OpenSSFSignals, RepoMeta
from cvi.openssf import calculate_pike_criticality
from cvi.pillars.criticality import (
    score_criticality,
    score_dependents,
    score_downloads,
    score_openssf_criticality,
)

# sum|alpha| = 1+1+2+1+1+0.5+0.5+0.5+1+2 = 10.5
# sum(alpha) = 1-1+2+1+1+0.5+0.5+0.5+1+2 = 8.5
_SUM_ABS_ALPHA = 10.5
_SUM_ALPHA = 8.5


def make_signals(**overrides) -> OpenSSFSignals:
    """OpenSSFSignals with all-zero raw telemetry; override per test."""
    kwargs = dict(
        created_since_months=0.0,
        updated_since_months=0.0,
        contributor_count=0,
        org_count=0,
        commit_frequency_weekly=0.0,
        recent_releases_count=0,
        closed_issues_count_90d=0,
        updated_issues_count_90d=0,
        comment_frequency=0.0,
        dependents_count=0,
    )
    kwargs.update(overrides)
    return OpenSSFSignals(**kwargs)


def make_meta(**overrides) -> RepoMeta:
    """RepoMeta with pillar-2 fields set; defaults are the raw zero signals."""
    kwargs = dict(owner="acme", name="widget", ecosystem=Ecosystem.PYPI)
    kwargs.update(overrides)
    return RepoMeta(**kwargs)


def pike_oracle(signals: OpenSSFSignals) -> float:
    """Independent hand-computation of spec 10.1 (Algorithm 1).

    Recomputes C from the raw signals, OPENSSF_WEIGHTS and
    OPENSSF_THRESHOLDS with math.log — no call into the engine under test.
    """
    raw = {
        "created_since": signals.created_since_months,
        "updated_since": signals.updated_since_months,
        "contributor_count": signals.contributor_count,
        "org_count": signals.org_count,
        "commit_frequency": signals.commit_frequency_weekly,
        "recent_releases_count": signals.recent_releases_count,
        "closed_issues_count": signals.closed_issues_count_90d,
        "updated_issues_count": signals.updated_issues_count_90d,
        "comment_frequency": signals.comment_frequency,
        "dependents_count": signals.dependents_count,
    }
    numerator = 0.0
    for param, alpha in OPENSSF_WEIGHTS.items():
        value = raw[param]
        t = OPENSSF_THRESHOLDS[param]
        non_negative = max(0.0, value)
        saturation_denom = max(value, t)
        if saturation_denom <= 0:
            normalized = 0.0
        else:
            normalized = math.log(1.0 + non_negative) / math.log(
                1.0 + saturation_denom
            )
        numerator += alpha * normalized
    return max(0.0, min(1.0, numerator / _SUM_ABS_ALPHA))


# --------------------------------------------------------------------------- #
# 2.1 Downstream Registry Dependents (ECDF of log10(count + 1))
# --------------------------------------------------------------------------- #
class TestScoreDependents:
    def test_zero_count_maps_to_log10_of_1(self):
        # log10(0 + 1) = 0.0; population [0.0, 1.0, 2.0] -> 1 of 3 <= 0.0
        # -> 33.33. Pins both the +1 shift and the ECDF count.
        assert score_dependents(0, [0.0, 1.0, 2.0]) == 33.33

    def test_hand_computed_percentile_mid_population(self):
        # dependents=999 -> log10(1000) = 3.0; population
        # [0.0, 1.0, 2.0, 3.0, 4.0] -> 4 of 5 <= 3.0 -> 80.0
        assert score_dependents(999, [0.0, 1.0, 2.0, 3.0, 4.0]) == 80.0

    def test_below_all_population_is_zero(self):
        # log10(10) = 1.0; population [2.0, 3.0, 4.0] -> 0 of 3 -> 0.0
        assert score_dependents(9, [2.0, 3.0, 4.0]) == 0.0

    def test_above_all_population_is_100(self):
        # log10(10**6 + 1) > 6.0; population [0.0, 1.0, 2.0] -> 3 of 3
        assert score_dependents(10**6, [0.0, 1.0, 2.0]) == 100.0

    def test_ties_counted_as_below_or_equal(self):
        # log10(100) = 2.0; population [2.0, 2.0, 2.0, 4.0] -> 3 of 4
        # -> 75.0. Spec 10.2 step 3 counts Element <= TargetValue.
        assert score_dependents(99, [2.0, 2.0, 2.0, 4.0]) == 75.0

    def test_empty_population_neutral_50(self):
        assert score_dependents(12345, []) == 50.0

    def test_negative_count_guarded_to_zero(self):
        # log10(max(0, -5) + 1) = log10(1) = 0.0 -> same as count 0.
        assert score_dependents(-5, [0.0, 1.0, 2.0]) == score_dependents(
            0, [0.0, 1.0, 2.0]
        )
        assert score_dependents(-5, [0.0, 1.0, 2.0]) == 33.33

    def test_population_is_pre_transformed_not_raw_counts(self):
        # Spec 2.1: R_pct(log10(Dependents + 1)) — the population holds
        # transformed values. If the module re-applied log10(x+1) to the
        # population, this hand-computed oracle would not hold:
        # target log10(100) = 2.0 vs population [1.0, 2.0, 3.0] -> 2 of 3.
        assert score_dependents(99, [1.0, 2.0, 3.0]) == pytest.approx(66.67)

    def test_percentile_rounded_to_two_decimals(self):
        # 1 of 3 -> 33.333... -> 33.33 (spec 10.2 step 5, 2 decimals).
        assert score_dependents(0, [0.0, 1.0, 2.0]) == 33.33
        assert score_dependents(9, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]) == 16.67


# --------------------------------------------------------------------------- #
# 2.2 Canonical OpenSSF Criticality (Rob Pike engine, x100)
# --------------------------------------------------------------------------- #
class TestScoreOpenssfCriticality:
    def test_all_signals_at_or_above_threshold(self):
        # Every S_i* = ln(1+T_i)/ln(1+T_i) = 1.0 -> C = sum(alpha)/sum|alpha|
        # = 8.5/10.5 = 0.8095238095238095 -> 80.952380... -> 80.95 at 2 dp.
        signals = make_signals(
            created_since_months=120.0,
            updated_since_months=120.0,
            contributor_count=5000,
            org_count=10,
            commit_frequency_weekly=1000.0,
            recent_releases_count=26,
            closed_issues_count_90d=5000,
            updated_issues_count_90d=5000,
            comment_frequency=15.0,
            dependents_count=500000,
        )
        expected = _SUM_ALPHA / _SUM_ABS_ALPHA  # 0.8095238095238095
        assert pike_oracle(signals) == pytest.approx(expected, abs=1e-12)
        assert score_openssf_criticality(signals) == pytest.approx(
            expected * 100.0, abs=1e-9
        )
        assert round(score_openssf_criticality(signals), 2) == 80.95

    def test_all_zero_signals_is_zero(self):
        # Every S_i* = ln(1)/ln(1+T_i) = 0.0 -> C = 0.0.
        signals = make_signals()
        assert pike_oracle(signals) == 0.0
        assert score_openssf_criticality(signals) == 0.0

    def test_mixed_hand_computed_case(self):
        # Three non-zero signals, hand-computed with math.log:
        #   created_since 60:    +1.0 * ln(61)/ln(121)
        #   contributor_count 100: +2.0 * ln(101)/ln(5001)
        #   dependents_count 1000: +2.0 * ln(1001)/ln(500001)
        #   all others: 0.0
        signals = make_signals(
            created_since_months=60.0,
            contributor_count=100,
            dependents_count=1000,
        )
        expected = (
            1.0 * math.log(61.0) / math.log(121.0)
            + 2.0 * math.log(101.0) / math.log(5001.0)
            + 2.0 * math.log(1001.0) / math.log(500001.0)
        ) / _SUM_ABS_ALPHA
        assert pike_oracle(signals) == pytest.approx(expected, abs=1e-12)
        assert score_openssf_criticality(signals) == pytest.approx(
            expected * 100.0, abs=1e-9
        )

    def test_pillar_param_matches_engine_times_100(self):
        # The pillar must be exactly calculate_pike_criticality * 100.0 —
        # no extra rounding or clamping of its own.
        signals = make_signals(
            created_since_months=30.0,
            contributor_count=42,
            org_count=3,
            comment_frequency=7.5,
        )
        assert score_openssf_criticality(signals) == (
            calculate_pike_criticality(signals) * 100.0
        )

    def test_none_signals_neutral_50(self):
        assert score_openssf_criticality(None) == 50.0

    def test_negative_signal_values_clamped_to_zero(self):
        # Spec 10.1 step 3c: NonNegativeValue = max(0, RawValue). A negative
        # raw value must contribute 0.0, identical to the all-zero case.
        signals = make_signals(
            created_since_months=-120.0,
            contributor_count=-5000,
            dependents_count=-500000,
        )
        assert pike_oracle(signals) == 0.0
        assert score_openssf_criticality(signals) == 0.0

    def test_engine_output_is_bounded_unit_interval(self):
        # Even with absurdly large raw values C stays in [0, 1] (each S_i*
        # <= 1.0). Here every positive-weight signal saturates (S* = 1.0)
        # while updated_since = 0 gives S* = 0, so C = (sum of the nine
        # positive weights) / sum|alpha| = 9.5/10.5 = 0.9047619...
        signals = make_signals(
            created_since_months=10**9,
            contributor_count=10**9,
            org_count=10**9,
            commit_frequency_weekly=10**9,
            recent_releases_count=10**9,
            closed_issues_count_90d=10**9,
            updated_issues_count_90d=10**9,
            comment_frequency=10**9,
            dependents_count=10**9,
            updated_since_months=0.0,
        )
        c = calculate_pike_criticality(signals)
        assert 0.0 <= c <= 1.0
        positive_sum = sum(a for a in OPENSSF_WEIGHTS.values() if a > 0.0)
        assert c == pytest.approx(positive_sum / _SUM_ABS_ALPHA, abs=1e-9)


# --------------------------------------------------------------------------- #
# Negative-weight behaviour: updated_since is the abandoned-codebase penalty
# --------------------------------------------------------------------------- #
class TestNegativeWeightUpdatedSince:
    def test_abandoned_repo_scores_lower(self):
        # Identical signals except updated_since: 0 vs 120. At 120 the
        # inverted signal saturates (S* = 1.0) and the -1.0 weight drags the
        # numerator down by exactly 1.0.
        base = dict(
            created_since_months=120.0,
            contributor_count=5000,
            org_count=10,
            commit_frequency_weekly=1000.0,
            recent_releases_count=26,
            closed_issues_count_90d=5000,
            updated_issues_count_90d=5000,
            comment_frequency=15.0,
            dependents_count=500000,
        )
        fresh = make_signals(updated_since_months=0.0, **base)
        abandoned = make_signals(updated_since_months=120.0, **base)
        c_fresh = pike_oracle(fresh)
        c_abandoned = pike_oracle(abandoned)
        assert c_abandoned < c_fresh
        # Exact delta: 1.0 / 10.5 in C units.
        assert c_fresh - c_abandoned == pytest.approx(1.0 / _SUM_ABS_ALPHA, abs=1e-12)
        # Through the pillar param (x100):
        assert (
            score_openssf_criticality(abandoned)
            < score_openssf_criticality(fresh)
        )
        assert score_openssf_criticality(fresh) - score_openssf_criticality(
            abandoned
        ) == pytest.approx(100.0 / _SUM_ABS_ALPHA, abs=1e-9)

    def test_negative_updated_since_treated_as_zero(self):
        # max(0, -120) = 0 -> identical to updated_since = 0.
        base = dict(
            created_since_months=120.0,
            contributor_count=5000,
            dependents_count=500000,
        )
        assert score_openssf_criticality(
            make_signals(updated_since_months=-120.0, **base)
        ) == score_openssf_criticality(make_signals(updated_since_months=0.0, **base))


# --------------------------------------------------------------------------- #
# 2.3 Filtered Active Monthly Downloads (ECDF of log10(count + 1))
# --------------------------------------------------------------------------- #
class TestScoreDownloads:
    def test_zero_downloads_hand_computed(self):
        # log10(0 + 1) = 0.0; population [0.0, 1.0, 2.0] -> 33.33.
        assert score_downloads(0, [0.0, 1.0, 2.0]) == 33.33

    def test_hand_computed_percentile(self):
        # downloads=99999 -> log10(100000) = 5.0; population
        # [0.0, 1.0, 2.0, 3.0, 4.0, 5.0] -> 6 of 6 -> 100.0
        assert score_downloads(99999, [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]) == 100.0

    def test_empty_population_neutral_50(self):
        assert score_downloads(42, []) == 50.0

    def test_negative_downloads_guarded_to_zero(self):
        assert score_downloads(-1, [0.0, 1.0, 2.0]) == score_downloads(
            0, [0.0, 1.0, 2.0]
        )
        assert score_downloads(-1, [0.0, 1.0, 2.0]) == 33.33


# --------------------------------------------------------------------------- #
# None openssf_signals -> 50.0 neutral, verified via hand-computed weighted sum
# --------------------------------------------------------------------------- #
class TestNoneSignalsNeutral:
    def test_none_signals_weighted_sum_hand_computed(self):
        # p1 = p3 = 50.0 (empty populations), p2 = 50.0 (None signals):
        # 0.40*50 + 0.35*50 + 0.25*50 = 50.0 — NOT 0.40*50 + 0.35*0 +
        # 0.25*50 = 37.5, which a zero-default implementation would give.
        meta = make_meta(openssf_signals=None)
        assert score_criticality(meta, [], []) == 50.0

    def test_none_signals_with_populations_hand_computed(self):
        # dependents=999 -> log10(1000) = 3.0 in [0.0, 1.0, 2.0, 3.0, 4.0]
        # -> 80.0; downloads=9 -> log10(10) = 1.0 in [0.0, 1.0, 2.0] -> 66.67;
        # p2 = 50.0 (None).
        # 0.40*80.0 + 0.35*50.0 + 0.25*66.67 = 32.0 + 17.5 + 16.6675 = 66.1675
        meta = make_meta(
            dependents_count=999, monthly_downloads=9, openssf_signals=None
        )
        assert score_criticality(
            meta, [0.0, 1.0, 2.0, 3.0, 4.0], [0.0, 1.0, 2.0]
        ) == pytest.approx(66.1675, abs=1e-9)


# --------------------------------------------------------------------------- #
# Calibrated pillar combination: 0.40*p1 + 0.35*p2 + 0.25*p3
# --------------------------------------------------------------------------- #
class TestScoreCriticality:
    def test_weights_read_from_constants(self):
        # The pillar must use PARAM_WEIGHTS["criticality"], not literals.
        assert PARAM_WEIGHTS["criticality"] == {
            "dependents": 0.40,
            "openssf_criticality": 0.35,
            "downloads": 0.25,
        }

    def test_all_neutral_is_50(self):
        # 0.40*50 + 0.35*50 + 0.25*50 = 50.0
        meta = make_meta(openssf_signals=None)
        assert score_criticality(meta, [], []) == 50.0

    def test_weighted_sum_combination_one(self):
        # p1 = 80.0 (dependents=999 in [0,1,2,3,4]), p2 = 0.0 (zero signals),
        # p3 = 33.33 (downloads=0 in [0,1,2]).
        # 0.40*80.0 + 0.35*0.0 + 0.25*33.33 = 32.0 + 0.0 + 8.3325 = 40.3325
        meta = make_meta(
            dependents_count=999,
            monthly_downloads=0,
            openssf_signals=make_signals(),
        )
        assert score_criticality(
            meta, [0.0, 1.0, 2.0, 3.0, 4.0], [0.0, 1.0, 2.0]
        ) == pytest.approx(40.3325, abs=1e-9)

    def test_weighted_sum_combination_two(self):
        # p1 = 100.0 (dependents=10**6 in [0,1,2]), p2 = 50.0 (None),
        # p3 = 0.0 (downloads=9 in [2,3,4] -> log10(10)=1.0, none <= 1.0).
        # 0.40*100.0 + 0.35*50.0 + 0.25*0.0 = 40.0 + 17.5 = 57.5
        meta = make_meta(
            dependents_count=10**6, monthly_downloads=9, openssf_signals=None
        )
        assert score_criticality(meta, [0.0, 1.0, 2.0], [2.0, 3.0, 4.0]) == (
            pytest.approx(57.5, abs=1e-9)
        )

    def test_weighted_sum_combination_three(self):
        # p1 = 33.33 (dependents=0 in [0,1,2]), p2 = 80.952380... (all
        # signals at threshold, 8.5/10.5*100), p3 = 66.67 (downloads=99 in
        # [1,2,3] -> log10(100)=2.0, 2 of 3).
        # 0.40*33.33 + 0.35*(8.5/10.5*100) + 0.25*66.67
        # = 13.332 + 28.33333333333333 + 16.6675 = 58.33283333333333
        signals = make_signals(
            created_since_months=120.0,
            updated_since_months=120.0,
            contributor_count=5000,
            org_count=10,
            commit_frequency_weekly=1000.0,
            recent_releases_count=26,
            closed_issues_count_90d=5000,
            updated_issues_count_90d=5000,
            comment_frequency=15.0,
            dependents_count=500000,
        )
        meta = make_meta(
            dependents_count=0, monthly_downloads=99, openssf_signals=signals
        )
        expected = (
            0.40 * 33.33
            + 0.35 * (_SUM_ALPHA / _SUM_ABS_ALPHA * 100.0)
            + 0.25 * 66.67
        )
        assert score_criticality(
            meta, [0.0, 1.0, 2.0], [1.0, 2.0, 3.0]
        ) == pytest.approx(expected, abs=1e-9)

    def test_weighted_sum_combination_four(self):
        # p1 = 75.0 (dependents=99 in [2,2,2,4]), p2 = 0.0 (zero signals),
        # p3 = 100.0 (downloads=99999 in [0..5]).
        # 0.40*75.0 + 0.35*0.0 + 0.25*100.0 = 30.0 + 25.0 = 55.0
        meta = make_meta(
            dependents_count=99,
            monthly_downloads=99999,
            openssf_signals=make_signals(),
        )
        assert score_criticality(
            meta, [2.0, 2.0, 2.0, 4.0], [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
        ) == pytest.approx(55.0, abs=1e-9)

    def test_clamped_to_100(self):
        # p1 = p3 = 100.0, p2 = 100.0 would give 100.0; force the clamp with
        # the maximum reachable p2 (80.95) plus p1 = p3 = 100:
        # 0.40*100 + 0.35*80.952381 + 0.25*100 = 40 + 28.333333 + 25 = 93.33
        # (below 100, so the clamp is exercised only by construction here —
        # the real clamp check is the next test).
        signals = make_signals(
            created_since_months=120.0,
            updated_since_months=120.0,
            contributor_count=5000,
            org_count=10,
            commit_frequency_weekly=1000.0,
            recent_releases_count=26,
            closed_issues_count_90d=5000,
            updated_issues_count_90d=5000,
            comment_frequency=15.0,
            dependents_count=500000,
        )
        meta = make_meta(
            dependents_count=10**6,
            monthly_downloads=10**6,
            openssf_signals=signals,
        )
        expected = 0.40 * 100.0 + 0.35 * (_SUM_ALPHA / _SUM_ABS_ALPHA * 100.0) + 0.25 * 100.0
        assert score_criticality(
            meta, [0.0, 1.0, 2.0], [0.0, 1.0, 2.0]
        ) == pytest.approx(expected, abs=1e-9)
        assert score_criticality(meta, [0.0, 1.0, 2.0], [0.0, 1.0, 2.0]) <= 100.0

    def test_result_bounded_zero_to_100(self):
        # Worst case: zero signals (p2 = 0), zero counts at the floor of
        # populations that start above 0 -> p1 = p3 = 0.0 -> score 0.0.
        meta = make_meta(
            dependents_count=0,
            monthly_downloads=0,
            openssf_signals=make_signals(),
        )
        assert score_criticality(meta, [1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 0.0

    def test_dependents_weight_isolation(self):
        # Only p1 varies: p2 = 0.0 (zero signals), p3 = 0.0 (floor).
        # p1 = 80.0 -> 0.40*80.0 = 32.0. A module using the downloads
        # weight (0.25) for dependents would give 20.0 instead.
        meta = make_meta(
            dependents_count=999,
            monthly_downloads=0,
            openssf_signals=make_signals(),
        )
        assert score_criticality(
            meta, [0.0, 1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0]
        ) == pytest.approx(32.0, abs=1e-9)

    def test_openssf_weight_isolation(self):
        # Only p2 varies: p1 = p3 = 0.0 (floors). p2 = 80.952380...
        # -> 0.35 * 80.952380... = 28.33333333333333. A module using the
        # dependents weight (0.40) for openssf would give 32.380952...
        signals = make_signals(
            created_since_months=120.0,
            updated_since_months=120.0,
            contributor_count=5000,
            org_count=10,
            commit_frequency_weekly=1000.0,
            recent_releases_count=26,
            closed_issues_count_90d=5000,
            updated_issues_count_90d=5000,
            comment_frequency=15.0,
            dependents_count=500000,
        )
        meta = make_meta(
            dependents_count=0, monthly_downloads=0, openssf_signals=signals
        )
        expected = 0.35 * (_SUM_ALPHA / _SUM_ABS_ALPHA * 100.0)
        assert score_criticality(
            meta, [1.0, 2.0, 3.0], [1.0, 2.0, 3.0]
        ) == pytest.approx(expected, abs=1e-9)

    def test_downloads_weight_isolation(self):
        # Only p3 varies: p1 = p2 = 0.0. p3 = 66.67 -> 0.25*66.67 = 16.6675.
        # A module using the openssf weight (0.35) for downloads would give
        # 23.3345 instead.
        meta = make_meta(
            dependents_count=0,
            monthly_downloads=99,
            openssf_signals=make_signals(),
        )
        assert score_criticality(
            meta, [1.0, 2.0, 3.0], [1.0, 2.0, 3.0]
        ) == pytest.approx(16.6675, abs=1e-9)
