"""Repository deep-dive scorecard payload builder (spec 12.2).

Builds the stored payload for ``GET /v1/porchlamp/repos/{owner}/{name}`` from
:class:`~porchlamp.models.RepoMeta` plus a population of
:class:`~porchlamp.models.RawPREvent` rows. Pure and deterministic: no I/O, no
network, no clock reads — every value is derived from the arguments.

Payload shape (spec 12.2):

- ``raw_metrics`` — TTFHR (hours), first-timer merge rate, ghost rate
  (14d), tail merge latency (days, p90). Computed with the *same
  semantics* as :mod:`porchlamp.pillars.responsiveness` (bot-filtered TTFHR
  median, first-timer merge ratio, 14d ghost rate, nearest-rank p90
  merge latency). The pillar's helper functions (``_median``,
  ``_p90_nearest_rank``, ``_is_bot``) are private, so they are
  replicated here verbatim rather than reaching into the pillar's
  module internals; the calibration step (mapping to [0, 100]) is
  deliberately *not* applied — this payload carries raw values.
  Undefined metrics (no qualifying PRs) are ``None``.
- ``survival_curve`` — Kaplan-Meier points ``[t_days, S]`` over
  external-PR open durations. Event time is ``closed_at - created_at``
  when closed, else ``merged_at - created_at`` when merged; PRs still
  open are right-censored at ``now - created_at``. Convention: at each
  distinct failure time ``t_i``, ``d_i`` = failures at ``t_i``,
  ``n_i`` = PRs with observed time ``>= t_i`` (a PR censored exactly at
  ``t_i`` is still in the risk set at ``t_i``);
  ``S(t_i) = S(t_{i-1}) * (1 - d_i / n_i)``, ``S(0) = 1.0``. The curve
  always starts at ``[0.0, 1.0]`` and carries at most ~20 points
  (evenly-spaced, endpoints-preserving deterministic thinning when more).
  Durations and survivals are rounded to 6 decimals.
- ``elephant_breakdown`` — the scalar ``elephant_factor`` from the store;
  the per-organization corporate commit-share distribution requires the
  GraphQL corporate-contributor feed, so it is a documented placeholder
  marked ``"source": "pending_feed"`` rather than fabricated data.
- ``legal_ip`` — model, CLA hazard level, S_legal score and AI policy
  posture from ``meta``. Hazard maps the governance pillar's S_legal
  semantics (:data:`porchlamp.constants.LEGAL_IP_SCORES` via
  :func:`porchlamp.pillars.governance.score_legal_ip`): DCO / FOUNDATION_CLA
  (>= 75) -> "low", CORP_CLA_RECIPROCAL (40) -> "medium",
  CORP_CLA_UNILATERAL (10) -> "high".
- ``good_first_issues`` — the stored active-unclaimed-GFI count; the
  curated item list (titles, labels, creation dates, URLs) needs the
  issues API feed, so it is a documented placeholder marked
  ``"source": "pending_feed"``.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional

from .constants import BOT_LOGIN_MARKERS
from .models import PillarScores, RawPREvent, RepoMeta
from .pillars.governance import score_legal_ip
from .playbook import generate_maintainer_playbook

__all__ = ["build_deep_dive"]

_SECONDS_PER_DAY = 86400.0
_GHOST_WINDOW_DAYS = 14
_SURVIVAL_MAX_POINTS = 20


def _round6(value: float) -> float:
    """Round to 6 decimals for a stable, compact JSON payload."""
    return round(value, 6)


# Replicated verbatim from porchlamp/pillars/responsiveness.py (private there),
# so raw-metric semantics stay in lockstep with the pillar.
def _is_bot(login: str) -> bool:
    """True if ``login`` contains any bot/service-account marker."""
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
    """90th percentile via nearest-rank: ``ceil(0.90 * N)`` (1-indexed)."""
    s = sorted(values)
    n = len(s)
    rank = math.ceil(0.90 * n)
    rank = max(1, min(rank, n))
    return float(s[rank - 1])


def _raw_metrics(
    pr_events: list[RawPREvent], now: datetime
) -> dict[str, Optional[float]]:
    """Raw operational metrics (spec 12.2), pillar-1 semantics.

    ``None`` whenever the pillar's neutral-50 guard would apply (no
    qualifying PRs), i.e. the raw value is undefined.
    """
    external = [p for p in pr_events if p.is_external]

    # TTFHR: bot-filtered human median (responsiveness 1.1 population).
    latencies = [
        p.response_latency_hours
        for p in pr_events
        if p.is_external
        and p.response_latency_hours is not None
        and not _is_bot(p.author_login)
    ]
    ttfhr_hours = _round6(_median(latencies)) if latencies else None

    # First-timer merge rate: merged / decided within decided external
    # first-timers (responsiveness 1.2 population).
    decided_first_timers = [
        p
        for p in external
        if (p.merged_at is not None or p.closed_at is not None)
        and p.is_first_timer
    ]
    if decided_first_timers:
        merged = sum(1 for p in decided_first_timers if p.merged_at is not None)
        first_timer_merge_rate = _round6(merged / len(decided_first_timers))
    else:
        first_timer_merge_rate = None

    # Ghost rate GR_14: open > 14d with zero maintainer review comments,
    # over ALL external PRs (responsiveness 1.3 population).
    if external:
        window = timedelta(days=_GHOST_WINDOW_DAYS)
        ghosts = sum(
            1
            for p in external
            if p.closed_at is None
            and (now - p.created_at) > window
            and p.maintainer_review_comments == 0
        )
        ghost_rate_14d = _round6(ghosts / len(external))
    else:
        ghost_rate_14d = None

    # Tail merge latency: nearest-rank p90 of (merged_at - created_at)
    # over external merged PRs, reported in days (responsiveness 1.4
    # population, hours -> days).
    merged_external = [p for p in external if p.merged_at is not None]
    if merged_external:
        merge_latencies_hours = [
            max(0.0, (p.merged_at - p.created_at).total_seconds() / 3600.0)
            for p in merged_external
        ]
        tail_merge_latency_days = _round6(_p90_nearest_rank(merge_latencies_hours) / 24.0)
    else:
        tail_merge_latency_days = None

    return {
        "ttfhr_hours": ttfhr_hours,
        "first_timer_merge_rate": first_timer_merge_rate,
        "ghost_rate_14d": ghost_rate_14d,
        "tail_merge_latency_days": tail_merge_latency_days,
    }


def _survival_curve(
    pr_events: list[RawPREvent], now: datetime
) -> list[list[float]]:
    """Kaplan-Meier points ``[t_days, S]`` over external-PR open durations.

    Still-open PRs are right-censored at ``now``. Returns
    ``[[0.0, 1.0]]`` when there are no external PRs or no failures.
    """
    # (observed time in days, is_failure); still-open PRs are censored.
    observations: list[tuple[float, bool]] = []
    for p in pr_events:
        if not p.is_external:
            continue
        end = p.closed_at if p.closed_at is not None else p.merged_at
        if end is not None:
            observations.append(
                (max(0.0, (end - p.created_at).total_seconds() / _SECONDS_PER_DAY), True)
            )
        else:
            observations.append(
                (max(0.0, (now - p.created_at).total_seconds() / _SECONDS_PER_DAY), False)
            )

    points: list[list[float]] = [[0.0, 1.0]]
    if observations:
        survival = 1.0
        for t in sorted({d for d, failed in observations if failed}):
            at_risk = sum(1 for d, _ in observations if d >= t)
            failures = sum(1 for d, failed in observations if failed and d == t)
            survival *= 1.0 - failures / at_risk
            points.append([_round6(t), _round6(survival)])

    if len(points) > _SURVIVAL_MAX_POINTS:
        points = _thin_points(points, _SURVIVAL_MAX_POINTS)
    return points


def _thin_points(points: list[list[float]], max_points: int) -> list[list[float]]:
    """Deterministically thin to ``max_points`` total (incl. t=0 origin).

    Keeps the ``[0.0, 1.0]`` origin plus an evenly-spaced subsample of the
    event points that always includes the first and last event. For
    ``K > max_points - 1`` event points the step ``> 1`` guarantees the
    rounded indices are strictly increasing (no collisions).
    """
    head, events = points[0], points[1:]
    keep = max_points - 1
    step = (len(events) - 1) / (keep - 1)
    chosen = [events[round(i * step)] for i in range(keep)]
    return [head] + chosen


def _cla_hazard(model_value: str, s_legal: float) -> str:
    """Map the governance pillar's S_legal score to a CLA hazard level.

    ``s_legal >= 75`` (DCO, FOUNDATION_CLA) -> "low";
    ``>= 40`` (CORP_CLA_RECIPROCAL) -> "medium";
    below 40 (CORP_CLA_UNILATERAL) -> "high".
    """
    if s_legal >= 75.0:
        return "low"
    if s_legal >= 40.0:
        return "medium"
    return "high"


def build_deep_dive(
    meta: RepoMeta,
    pr_events: list[RawPREvent],
    now: datetime,
    pillar_scores: Optional[PillarScores] = None,
    overall_porchlamp: Optional[float] = None,
) -> dict:
    """Build the spec 12.2 deep-dive payload for one repository.

    Pure function of its arguments — deterministic, no I/O, no network.
    Feed-dependent fields (corporate commit-share distribution, curated
    GFI items) are honest placeholders marked ``"source": "pending_feed"``.
    Includes maintainer_playbook diagnostic gap analysis.
    """
    s_legal = score_legal_ip(meta.legal_ip_model)
    return {
        "raw_metrics": _raw_metrics(pr_events, now),
        "survival_curve": _survival_curve(pr_events, now),
        "elephant_breakdown": {
            "elephant_factor": meta.elephant_factor,
            "distribution": None,
            "source": "pending_feed",
            "note": (
                "Per-organization corporate commit-share distribution "
                "requires the GraphQL corporate-contributor feed (spec 3.2); "
                "only the scalar elephant factor is available in the "
                "operational store, so the breakdown is a documented "
                "placeholder, not fabricated data."
            ),
        },
        "legal_ip": {
            "model": meta.legal_ip_model.value,
            "cla_hazard": _cla_hazard(meta.legal_ip_model.value, s_legal),
            "s_legal_score": s_legal,
            "ai_policy_posture": meta.ai_posture.value,
        },
        "good_first_issues": {
            "count": meta.active_unclaimed_gfis,
            "items": [],
            "source": "pending_feed",
            "note": (
                "Curated unclaimed GFI items (titles, labels, creation "
                "dates, URLs) need the issues API feed; only the stored "
                "active-unclaimed count is available here."
            ),
        },
        "maintainer_playbook": generate_maintainer_playbook(
            meta,
            pr_events,
            pillar_scores=pillar_scores,
            overall_porchlamp=overall_porchlamp,
            now=now,
        ),
    }
