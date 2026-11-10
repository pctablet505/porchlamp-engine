"""Pillar 5 — Accessibility, DX & Newcomer On-Ramps (weight 15%).

Three parameter scorers, each calibrated to [0, 100], combined with the
within-pillar weights from ``porchlamp.constants.PARAM_WEIGHTS["accessibility"]``:

5.1 Freshness of Unclaimed GFIs (weight 0.40)
    Count of unassigned ``good-first-issue`` / ``help-wanted`` issues updated
    within the trailing 30 days. Linear ramp to full marks at
    ``GFI_TARGET`` (10) active unclaimed GFIs:

        score = min(100, gfis / GFI_TARGET * 100)

5.2 90-Day Newcomer Return Rate RR_90 (weight 0.35)
    Fraction of first-time successful contributors who submit a second PR
    within 90 days. A 50% return rate achieves full marks:

        score = min(100, rr_90 * RR90_MULTIPLIER)

5.3 Developer Experience & Setup Friction (weight 0.25)
    Reproducible devcontainer/CI execution runtime. The spec awards full
    marks for runtimes "under 20 minutes" (``DX_CI_MINUTES_TARGET``) but
    prescribes no decay curve beyond the target. Interpretation documented
    here (this module's choice, not the spec's):

    - ``dx_ci_minutes <= 0``: the 0.0 value is the "unknown / not measured"
      sentinel in the data contract, so it is NOT treated as an instant
      build. Unknown friction scores the neutral 50.0.
    - ``0 < dx_ci_minutes <= 20``: full marks (100.0).
    - ``dx_ci_minutes > 20``: linear decay of 5 points per minute above the
      target, floored at 0.0 (i.e. 40 minutes or more scores 0.0).

All outputs are clamped to [0, 100]; inputs are guarded (``rr_90`` to
[0, 1], GFI counts to >= 0) so out-of-domain raw telemetry cannot push a
score outside the calibrated range.
"""

from __future__ import annotations

from ..constants import DX_CI_MINUTES_TARGET, GFI_TARGET, PARAM_WEIGHTS, RR90_MULTIPLIER
from ..models import RepoMeta

_PILLAR = "accessibility"
_NEUTRAL: float = 50.0
_DX_DECAY_PER_MINUTE: float = 5.0


def _clamp(value: float) -> float:
    """Clamp a score into the calibrated [0, 100] range."""
    return max(0.0, min(100.0, value))


def score_gfi(active_unclaimed_gfis: int) -> float:
    """5.1 — Freshness of unclaimed GFIs.

    Linear in the count of active unclaimed GFIs, capped at full marks for
    ``GFI_TARGET`` (10) or more. Negative counts are guarded to 0.
    """
    count = max(0, active_unclaimed_gfis)
    return _clamp(count / GFI_TARGET * 100.0)


def score_return_rate(rr_90: float) -> float:
    """5.2 — 90-day newcomer return rate.

    ``rr_90`` is a fraction in [0, 1]; a 50% return rate scores 100.
    Out-of-domain values are guarded into [0, 1] before scaling.
    """
    rate = max(0.0, min(1.0, rr_90))
    return _clamp(rate * RR90_MULTIPLIER)


def score_dx(dx_ci_minutes: float) -> float:
    """5.3 — Developer experience & setup friction.

    See the module docstring for the documented interpretation:
    ``<= 0`` minutes is the unknown sentinel and scores the neutral 50.0;
    runtimes up to ``DX_CI_MINUTES_TARGET`` (20) score 100.0; beyond the
    target the score decays 5 points per minute, floored at 0.0.
    """
    if dx_ci_minutes <= 0:
        return _NEUTRAL
    if dx_ci_minutes <= DX_CI_MINUTES_TARGET:
        return 100.0
    return _clamp(100.0 - (dx_ci_minutes - DX_CI_MINUTES_TARGET) * _DX_DECAY_PER_MINUTE)


def score_accessibility(meta: RepoMeta) -> float:
    """Calibrated Pillar 5 score in [0, 100].

    Weighted combination of the three parameter scorers using
    ``PARAM_WEIGHTS["accessibility"]`` (gfi 0.40, return_rate 0.35, dx 0.25).
    """
    weights = PARAM_WEIGHTS[_PILLAR]
    score = (
        weights["gfi"] * score_gfi(meta.active_unclaimed_gfis)
        + weights["return_rate"] * score_return_rate(meta.rr_90)
        + weights["dx"] * score_dx(meta.dx_ci_minutes)
    )
    return _clamp(score)
