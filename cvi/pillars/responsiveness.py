"""Pillar 1 — Responsiveness & Review Lifecycle (weight 25%).

Four parameter scorers, each calibrated to [0, 100], combined with the
within-pillar weights from ``cvi.constants.PARAM_WEIGHTS["responsiveness"]``
(ttfhr 0.35, mr_first 0.25, ghost_rate 0.20, ttm_p90 0.20).

All four scorers operate on a population of :class:`~cvi.models.RawPREvent`
rows. The single entry point :func:`score_responsiveness` returns the
weighted pillar score in [0, 100].

Edge-case decisions (this module's documented choices where the spec leaves
room for interpretation):

1.1 TTFHR — bot filtering
    * Bot/service-account stripping is applied ONLY to TTFHR, matching the
      spec's "Bot-Filtered Human TTFHR" and Phase 1's "strip ... from review
      latency metrics". The other three parameters use the raw external
      population (no bot strip).
    * A PR is a bot if ``author_login`` contains ANY ``BOT_LOGIN_MARKERS``
      substring, case-insensitively (both sides lower-cased). E.g.
      ``dependabot[bot]`` and ``github-actions`` are both dropped.
    * The median is the standard median: the middle value for an odd count,
      the mean of the two middle values for an even count (computed on the
      ascending-sorted latencies).
    * "No qualifying PRs" means: no external PR with a non-None
      ``response_latency_hours`` that survives bot filtering. That yields the
      neutral 50.0. (An empty ``pr_events`` list, an all-bot population, and a
      population whose only external PRs have ``response_latency_hours=None``
      all fall in this bucket.)
    * The decay only ever runs for ``TTFHR > base``; at or below the 12h base
      the score is a flat 100.0, so a 0h / negative latency can never reach
      the ``log2`` branch (no domain error).

1.2 MR_first — first-timer merge rate
    * Population = external PRs that are *decided* (``merged_at`` or
      ``closed_at`` is not None) AND are first-timers
      (``author_prior_merged_commits == 0``).
    * ``ratio = merged / decided`` is computed *within* that first-timer
      population (the spec's "merged / decided among first-timers"). Returning
      authors' merges are ignored entirely.
    * "No decided external PRs -> 50.0" is a special case of the real guard:
      if there are no decided external *first-timer* PRs the ratio is
      undefined and we return the neutral 50.0. This also covers the case
      where decided external PRs exist but none are first-timers.

1.3 GR_14 — ghost rate
    * "Open" is taken literally as ``closed_at is None`` (the spec's exact
      wording). A merged PR whose ``closed_at`` is unset therefore counts as
      open here; in practice merged PRs are recent and fail the age test, so
      they are not ghosts.
    * A ghost is an external PR that is open AND strictly older than 14 days
      (``(now - created_at) > 14d``; a PR created *exactly* 14 days ago is NOT
      a ghost) AND has ``maintainer_review_comments == 0``.
    * The denominator is ALL external PRs (ghosts / total external), not just
      open ones. No external PRs -> 50.0.

1.4 TTM_p90 — merge latency tail risk
    * Merge latency (hours) = ``(merged_at - created_at)`` over external
      *merged* PRs (``merged_at is not None``).
    * The 90th percentile uses the **nearest-rank** method (no linear
      interpolation): sort ascending, ``rank = ceil(0.90 * N)`` (1-indexed),
      and return the value at that rank. For N = 10 this is the 9th-smallest
      value; for N = 1 it is the only value.
    * ``score = 100.0 - compute_ecosystem_percentile(p90, population)``. An
      empty ``ttm_p90_population`` makes the ECDF return its 50.0 neutral
      fallback, so the parameter scores 50.0. No merged external PRs -> 50.0.

Weighting
    The pillar score is the weighted sum of the four parameter scores using
    ``PARAM_WEIGHTS["responsiveness"]``, clamped to [0, 100].
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from ..constants import (
    BOT_LOGIN_MARKERS,
    GHOST_RATE_MULTIPLIER,
    MR_FIRST_MULTIPLIER,
    PARAM_WEIGHTS,
    TTFHR_BASE_HOURS,
    TTFHR_DECAY,
)
from ..models import RawPREvent
from ..normalize import compute_ecosystem_percentile

_PILLAR = "responsiveness"
_NEUTRAL: float = 50.0
_GHOST_WINDOW_DAYS: int = 14
_TTM_P90: float = 0.90


def _clamp(value: float) -> float:
    """Clamp a score into the calibrated [0, 100] range."""
    return max(0.0, min(100.0, value))


def _is_bot(login: str) -> bool:
    """True if ``login`` contains any bot/service-account marker.

    Case-insensitive substring match against every ``BOT_LOGIN_MARKERS`` entry.
    """
    lowered = login.lower()
    return any(marker.lower() in lowered for marker in BOT_LOGIN_MARKERS)


def _median(values: list[float]) -> float:
    """Standard median: middle value (odd) or mean of the two middle (even)."""
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0


def _p90_nearest_rank(values: list[float]) -> float:
    """90th percentile via the nearest-rank method (no linear interpolation).

    ``rank = ceil(0.90 * N)`` (1-indexed); return the value at that rank in the
    ascending-sorted list. See the module docstring (1.4) for the rationale.
    """
    s = sorted(values)
    n = len(s)
    rank = math.ceil(_TTM_P90 * n)
    rank = max(1, min(rank, n))
    return float(s[rank - 1])


def score_ttfhr(pr_events: list[RawPREvent]) -> float:
    """1.1 — Bot-filtered human TTFHR (weight 0.35).

    Median of ``response_latency_hours`` over external, non-bot PRs that have a
    non-None latency. 100.0 at or below the 12h base; decays 25 pts per
    doubling above the base, floored at 0.0. No qualifying PRs -> 50.0 neutral.
    """
    latencies = [
        p.response_latency_hours
        for p in pr_events
        if p.is_external
        and p.response_latency_hours is not None
        and not _is_bot(p.author_login)
    ]
    if not latencies:
        return _NEUTRAL
    ttfhr = _median(latencies)
    if ttfhr <= TTFHR_BASE_HOURS:
        return 100.0
    return _clamp(100.0 - TTFHR_DECAY * math.log2(ttfhr / TTFHR_BASE_HOURS))


def score_mr_first(pr_events: list[RawPREvent]) -> float:
    """1.2 — First-timer merge rate MR_first (weight 0.25).

    Over external, decided (merged or closed) first-timer PRs, the ratio of
    merged to decided. An 80% ratio scores 100.0 (x125, capped). No decided
    external first-timer PRs -> 50.0 neutral.
    """
    decided_first_timers = [
        p
        for p in pr_events
        if p.is_external
        and (p.merged_at is not None or p.closed_at is not None)
        and p.is_first_timer
    ]
    if not decided_first_timers:
        return _NEUTRAL
    merged = sum(1 for p in decided_first_timers if p.merged_at is not None)
    ratio = merged / len(decided_first_timers)
    return _clamp(ratio * MR_FIRST_MULTIPLIER)


def score_ghost_rate(pr_events: list[RawPREvent], now: datetime) -> float:
    """1.3 — PR ghost rate GR_14 (weight 0.20).

    Fraction of ALL external PRs that are open (``closed_at is None``),
    strictly older than 14 days, and have zero maintainer review comments.
    0% -> 100.0; >= 50% -> 0.0 (x200, floored). No external PRs -> 50.0 neutral.
    """
    external = [p for p in pr_events if p.is_external]
    if not external:
        return _NEUTRAL
    window = timedelta(days=_GHOST_WINDOW_DAYS)
    ghosts = sum(
        1
        for p in external
        if p.closed_at is None
        and (now - p.created_at) > window
        and p.maintainer_review_comments == 0
    )
    gr = ghosts / len(external)
    return _clamp(100.0 - gr * GHOST_RATE_MULTIPLIER)


def score_ttm_p90(
    pr_events: list[RawPREvent], ttm_p90_population: list[float]
) -> float:
    """1.4 — PR merge latency tail risk TTM_p90 (weight 0.20).

    Nearest-rank 90th percentile of merge latency hours over external merged
    PRs, mapped to the ecosystem via ECDF: ``score = 100 - R_pct(p90)``. No
    merged external PRs -> 50.0 neutral.
    """
    merged_external = [
        p for p in pr_events if p.is_external and p.merged_at is not None
    ]
    if not merged_external:
        return _NEUTRAL
    latencies = [
        (p.merged_at - p.created_at).total_seconds() / 3600.0
        for p in merged_external
    ]
    # Out-of-domain telemetry (merged_at < created_at) yields a negative
    # latency that would sort to the bottom and, if dominant, drive the p90
    # negative -> ECDF percentile 0 -> a spurious perfect 100. Drop corrupt
    # samples; if none survive, fall back to the neutral 50.0.
    valid = [lat for lat in latencies if lat >= 0.0]
    if not valid:
        return _NEUTRAL
    p90 = _p90_nearest_rank(valid)
    return _clamp(100.0 - compute_ecosystem_percentile(p90, ttm_p90_population))


def score_responsiveness(
    pr_events: list[RawPREvent],
    ttm_p90_population: list[float],
    now: datetime,
) -> float:
    """Calibrated Pillar 1 score in [0, 100].

    Weighted combination of the four parameter scorers using
    ``PARAM_WEIGHTS["responsiveness"]`` (ttfhr 0.35, mr_first 0.25,
    ghost_rate 0.20, ttm_p90 0.20), clamped to [0, 100].
    """
    weights = PARAM_WEIGHTS[_PILLAR]
    score = (
        weights["ttfhr"] * score_ttfhr(pr_events)
        + weights["mr_first"] * score_mr_first(pr_events)
        + weights["ghost_rate"] * score_ghost_rate(pr_events, now)
        + weights["ttm_p90"] * score_ttm_p90(pr_events, ttm_p90_population)
    )
    return _clamp(score)
