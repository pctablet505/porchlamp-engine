"""Tests for Pillar 5 — Accessibility, DX & Newcomer On-Ramps.

Owner test bar: exact-oracle assertions on the calibrated formulas from
``docs/ARCHITECTURE.md`` sections 5.1-5.3. Every asserted value is the
computed expected value, not a property a no-op implementation also
satisfies.

Oracle summary:
  5.1 GFI:    min(100, gfis / 10 * 100); negative counts guarded to 0.
  5.2 RR_90:  min(100, rr * 200); rr guarded to [0, 1].
  5.3 DX:     <= 0 min -> 50.0 neutral (unknown sentinel);
              (0, 20]  -> 100.0;
              > 20     -> 100 - (m - 20) * 5, floored at 0.0.
  Pillar:     0.40*gfi + 0.35*rr + 0.25*dx, clamped to [0, 100].

Weight-isolation tests (``TestScoreAccessibility``) pin each parameter
weight individually: e.g. with only the GFI parameter at 100 and the DX
parameter at its 50.0 neutral, the pillar score is 0.40*100 + 0.25*50 =
52.5 — a different weight assignment yields a different value.
"""

from porchlamp.models import Ecosystem, RepoMeta
from porchlamp.pillars.accessibility import (
    score_accessibility,
    score_dx,
    score_gfi,
    score_return_rate,
)


def make_meta(**overrides) -> RepoMeta:
    """RepoMeta with pillar-5 fields set; defaults are the raw zero signals."""
    kwargs = dict(owner="acme", name="widget", ecosystem=Ecosystem.PYPI)
    kwargs.update(overrides)
    return RepoMeta(**kwargs)


# --------------------------------------------------------------------------- #
# 5.1 Freshness of Unclaimed GFIs
# --------------------------------------------------------------------------- #
class TestScoreGfi:
    def test_full_at_target(self):
        assert score_gfi(10) == 100.0

    def test_half_target(self):
        assert score_gfi(5) == 50.0

    def test_zero(self):
        assert score_gfi(0) == 0.0

    def test_cap_above_target(self):
        assert score_gfi(25) == 100.0

    def test_negative_guarded_to_zero(self):
        assert score_gfi(-3) == 0.0


# --------------------------------------------------------------------------- #
# 5.2 90-Day Newcomer Return Rate
# --------------------------------------------------------------------------- #
class TestScoreReturnRate:
    def test_half_rate_full(self):
        assert score_return_rate(0.5) == 100.0

    def test_quarter_rate(self):
        assert score_return_rate(0.25) == 50.0

    def test_zero(self):
        assert score_return_rate(0.0) == 0.0

    def test_full_rate(self):
        assert score_return_rate(1.0) == 100.0

    def test_above_one_guarded(self):
        assert score_return_rate(1.5) == 100.0

    def test_negative_guarded(self):
        assert score_return_rate(-0.25) == 0.0


# --------------------------------------------------------------------------- #
# 5.3 Developer Experience & Setup Friction
# --------------------------------------------------------------------------- #
class TestScoreDx:
    def test_fast_build_full(self):
        assert score_dx(10) == 100.0

    def test_at_target_full(self):
        assert score_dx(20) == 100.0

    def test_under_target_full(self):
        assert score_dx(15) == 100.0

    def test_unknown_zero_neutral(self):
        # 0 is the "unknown / not measured" sentinel, NOT an instant build.
        assert score_dx(0) == 50.0

    def test_negative_neutral(self):
        assert score_dx(-5) == 50.0

    def test_decay_first_minute(self):
        assert score_dx(21) == 95.0

    def test_decay_midpoint(self):
        assert score_dx(30) == 50.0

    def test_decay_floor(self):
        assert score_dx(40) == 0.0

    def test_beyond_floor_stays_zero(self):
        assert score_dx(60) == 0.0


# --------------------------------------------------------------------------- #
# Calibrated pillar combination
# --------------------------------------------------------------------------- #
class TestScoreAccessibility:
    def test_perfect_repo(self):
        meta = make_meta(active_unclaimed_gfis=10, rr_90=0.5, dx_ci_minutes=10)
        assert score_accessibility(meta) == 100.0

    def test_all_zero_signals(self):
        # gfis=0 -> 0, rr=0 -> 0, dx=0 -> 50.0 neutral: 0.25 * 50 = 12.5
        assert score_accessibility(make_meta()) == 12.5

    def test_gfi_weight_isolation(self):
        # 0.40*100 + 0.35*0 + 0.25*50 = 52.5
        meta = make_meta(active_unclaimed_gfis=10, rr_90=0.0, dx_ci_minutes=0)
        assert score_accessibility(meta) == 52.5

    def test_return_rate_weight_isolation(self):
        # 0.40*0 + 0.35*100 + 0.25*50 = 47.5
        meta = make_meta(active_unclaimed_gfis=0, rr_90=0.5, dx_ci_minutes=0)
        assert score_accessibility(meta) == 47.5

    def test_dx_weight_isolation(self):
        # 0.40*0 + 0.35*0 + 0.25*100 = 25.0
        meta = make_meta(active_unclaimed_gfis=0, rr_90=0.0, dx_ci_minutes=10)
        assert score_accessibility(meta) == 25.0

    def test_mixed_signals(self):
        # 0.40*50 + 0.35*100 + 0.25*50 = 67.5
        # Parameter values deliberately unequal: an equal-value mix (all 50.0)
        # would be invariant to any weight assignment summing to 1.0.
        meta = make_meta(active_unclaimed_gfis=5, rr_90=0.5, dx_ci_minutes=30)
        assert score_accessibility(meta) == 67.5
