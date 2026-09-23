"""Gap-closing oracle tests for the canonical OpenSSF criticality engine.

Spec 14's verification matrix names this suite ``test_openssf_criticality``;
the file hosts it per the test-suite layout. Exact-oracle assertions for
``porchlamp.openssf.calculate_pike_criticality``: every asserted value is
hand-computed against the default ``OPENSSF_WEIGHTS`` /
``OPENSSF_THRESHOLDS`` (sum|alpha| = 10.5, sum(alpha) = 8.5), so a drift in
the saturation guard, the [0, 1] clamp, or the zero-weight guard fails
loudly.
"""

from __future__ import annotations

from porchlamp.constants import OPENSSF_THRESHOLDS, OPENSSF_WEIGHTS
from porchlamp.models import OpenSSFSignals
from porchlamp.openssf import calculate_pike_criticality


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


def test_negative_value_and_negative_threshold_saturation_guard():
    # saturation_denom = max(value, T) must take the guard branch
    # (normalized = 0.0) whenever it is <= 0, not just == 0. Injecting a
    # negative dependents_count threshold (-2.0) with a negative raw value
    # (-500000) gives saturation_denom = -2.0. Every other signal is 0 ->
    # normalized 0.0, so C = 0.0 / 10.5 clamped to 0.0. An "== 0" guard
    # would instead evaluate ln(1 + (-2.0)) = ln(-1.0) and raise ValueError.
    thresholds = {**OPENSSF_THRESHOLDS, "dependents_count": -2.0}
    signals = make_signals(dependents_count=-500000)
    assert calculate_pike_criticality(signals, thresholds=thresholds) == 0.0


def test_negative_unbounded_clamped_to_zero():
    # updated_since is the only non-zero signal, exactly at its threshold ->
    # S* = ln(121)/ln(121) = 1.0 with weight -1.0, so numerator = -1.0 and
    # the unbounded C = -1.0/10.5 ~ -0.095238. The clamp floor must lift
    # that to exactly 0.0; without the max(0.0, ...) floor the engine would
    # return the negative value.
    signals = make_signals(updated_since_months=120.0)
    assert calculate_pike_criticality(signals) == 0.0


def test_zero_sum_weights_guard_returns_zero():
    # A zero-sum weights dict makes sum|alpha| = 0.0; the guard must return
    # 0.0 rather than 1.0 or crash. The signals are all-at-threshold (which
    # would score 8.5/10.5 ~ 0.809524 under the default weights) so the 0.0
    # result can only come from the guard.
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
    zero_weights = dict.fromkeys(OPENSSF_WEIGHTS, 0.0)
    assert calculate_pike_criticality(signals, weights=zero_weights) == 0.0


def test_at_threshold_default_weights_score_matches_hand_computation():
    # The suite above asserts values against
    # real default OPENSSF_WEIGHTS/OPENSSF_THRESHOLDS -- every other test
    # here isolates a guard branch (the saturation floor, the clamp, the
    # zero-weights early return) that is, by construction, invariant to the
    # exact tunable magnitudes, so none of them can catch a wrong default
    # weight or threshold. At every signal exactly at its threshold,
    # saturation_denom == value for every term, so every normalized S_i* is
    # exactly log(1+T)/log(1+T) = 1.0 regardless of T; the score collapses
    # to sum(alpha_i) / sum(|alpha_i|) = 8.5 / 10.5 (this file's own cited
    # constants, see test_zero_sum_weights_guard_returns_zero's comment) =
    # 0.809523... -> a value computed by hand, not by calling any Porchlamp code.
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
    assert round(calculate_pike_criticality(signals), 6) == round(8.5 / 10.5, 6)
