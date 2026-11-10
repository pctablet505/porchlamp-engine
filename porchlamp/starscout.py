"""Phase 1: StarScout anti-gaming filter (spec 10.3, Algorithm 3).

Audits stargazer velocity to defeat black-market star farming:

* Throwaway accounts — stargazers with fewer than
  ``STARS_SCOUT_MIN_EVENTS`` (5) total platform events are discarded.
* Lockstep burst rings — when the recent-star count exceeds
  ``STARS_SCOUT_BURST_THRESHOLD`` (500) and the star-to-fork ratio exceeds
  ``STARS_SCOUT_FORK_RATIO`` (100:1), the valid-star count is scaled down by
  ``STARS_SCOUT_LOCKSTEP_PENALTY`` (0.30).

The five ``STARS_SCOUT_*`` thresholds live in ``porchlamp.core.constants``
(D-IN-5, RFC-0002, ``docs/v2/03-ANTI-MANIPULATION.md`` section 6
``star_burst``) -- the exact five values the spec names, registered where a
change to them is caught by the constants freeze. :func:`audit_star_velocity`
is the full entry point: it returns the de-biased count
:func:`filter_fraudulent_star_velocity` reports, plus whether the lockstep
penalty fired -- the caller-visible ``int`` alone cannot tell "0 valid
stars, no penalty" apart from "some valid stars, all zeroed by the
penalty", and ``porchlamp.collect.universe`` (universe entry, D-IN-5) needs that
boolean to record ``star_velocity_flag`` and feed the ``star_burst``
detector (03 section 6) real evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from .core.constants import (
    STARS_SCOUT_BURST_THRESHOLD,
    STARS_SCOUT_FORK_RATIO,
    STARS_SCOUT_LOCKSTEP_PENALTY,
    STARS_SCOUT_MIN_EVENTS,
    STARS_SCOUT_WINDOW_DAYS,
)
from .models import StargazerEvent


@dataclass(frozen=True)
class StarVelocityAudit:
    """The full StarScout audit result behind one
    :func:`filter_fraudulent_star_velocity` call.

    ``valid_stars`` is exactly what :func:`filter_fraudulent_star_velocity`
    returns. ``recent_stars`` is ``len(RecentStars)`` (step 2 of the
    algorithm) before the throwaway-account filter or the lockstep penalty
    -- the raw star-velocity count over the trailing window.
    ``fork_ratio`` is the ratio computed at step 4, or ``None`` when the
    burst-count gate (``len(RecentStars) > STARS_SCOUT_BURST_THRESHOLD``)
    never opened (the ratio was never computed, not zero). ``penalty_applied``
    is ``True`` iff step 4's lockstep penalty actually scaled ``valid_stars``
    down -- the one fact the plain ``int`` return cannot distinguish on its
    own: a repo with 100 real stars and no burst, and a repo with 334
    burst-inflated valid stars knocked down by the penalty
    (``int(334 * 0.30) == 100``), both return exactly ``100`` from
    :func:`filter_fraudulent_star_velocity`; only ``penalty_applied`` tells
    them apart.
    """

    valid_stars: int
    recent_stars: int
    fork_ratio: Optional[float]
    penalty_applied: bool


def audit_star_velocity(
    stargazer_events: list[StargazerEvent],
    repo_forks_delta_90d: int,
    now: datetime,
) -> StarVelocityAudit:
    """Run the StarScout algorithm (spec 10.3, Algorithm 3) and return the
    full audit, not just the de-biased count.

    1. Cutoff = now - STARS_SCOUT_WINDOW_DAYS (90) days.
    2. RecentStars = events with ``starred_at >= cutoff``.
    3. ValidStars = RecentStars with ``user_total_platform_events >=
       STARS_SCOUT_MIN_EVENTS`` (5); below that is a throwaway account.
    4. If ``len(RecentStars) > STARS_SCOUT_BURST_THRESHOLD`` (500):
       fork_ratio = ValidStars / max(1, repo_forks_delta_90d); if
       fork_ratio > STARS_SCOUT_FORK_RATIO (100.0), apply the lockstep
       penalty: ValidStars = int(ValidStars * STARS_SCOUT_LOCKSTEP_PENALTY).
    5. Return ValidStars (plus the audit trail above).
    """
    cutoff = now - timedelta(days=STARS_SCOUT_WINDOW_DAYS)
    recent_stars = [e for e in stargazer_events if e.starred_at >= cutoff]

    valid_stars = sum(
        1 for e in recent_stars if e.user_total_platform_events >= STARS_SCOUT_MIN_EVENTS
    )

    fork_ratio: Optional[float] = None
    penalty_applied = False
    if len(recent_stars) > STARS_SCOUT_BURST_THRESHOLD:
        fork_ratio = valid_stars / max(1, repo_forks_delta_90d)
        if fork_ratio > STARS_SCOUT_FORK_RATIO:
            valid_stars = int(valid_stars * STARS_SCOUT_LOCKSTEP_PENALTY)
            penalty_applied = True

    return StarVelocityAudit(
        valid_stars=valid_stars,
        recent_stars=len(recent_stars),
        fork_ratio=fork_ratio,
        penalty_applied=penalty_applied,
    )


def filter_fraudulent_star_velocity(
    stargazer_events: list[StargazerEvent],
    repo_forks_delta_90d: int,
    now: datetime,
) -> int:
    """Return the audited (de-biased) star count for the trailing 90 days.

    A thin wrapper over :func:`audit_star_velocity` that returns only
    ``valid_stars``. See that function's docstring for the algorithm; see
    :class:`StarVelocityAudit` for the richer result a caller needs when it
    must know whether the penalty fired.
    """
    return audit_star_velocity(stargazer_events, repo_forks_delta_90d, now).valid_stars
