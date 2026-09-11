"""Phase 1: StarScout anti-gaming filter (spec 10.3, Algorithm 3).

Audits stargazer velocity to defeat black-market star farming:

* Throwaway accounts — stargazers with fewer than
  ``STARS_SCOUT_MIN_EVENTS`` (5) total platform events are discarded.
* Lockstep burst rings — when the recent-star count exceeds
  ``STARS_SCOUT_BURST_THRESHOLD`` (500) and the star-to-fork ratio exceeds
  ``STARS_SCOUT_FORK_RATIO`` (100:1), the valid-star count is scaled down by
  ``STARS_SCOUT_LOCKSTEP_PENALTY`` (0.30).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .constants import (
    STARS_SCOUT_BURST_THRESHOLD,
    STARS_SCOUT_FORK_RATIO,
    STARS_SCOUT_LOCKSTEP_PENALTY,
    STARS_SCOUT_MIN_EVENTS,
    STARS_SCOUT_WINDOW_DAYS,
)
from .models import StargazerEvent


def filter_fraudulent_star_velocity(
    stargazer_events: list[StargazerEvent],
    repo_forks_delta_90d: int,
    now: datetime,
) -> int:
    """Return the audited (de-biased) star count for the trailing 90 days.

    1. Cutoff = now - STARS_SCOUT_WINDOW_DAYS (90) days.
    2. RecentStars = events with ``starred_at >= cutoff``.
    3. ValidStars = RecentStars with ``user_total_platform_events >=
       STARS_SCOUT_MIN_EVENTS`` (5); below that is a throwaway account.
    4. If ``len(RecentStars) > STARS_SCOUT_BURST_THRESHOLD`` (500):
       fork_ratio = ValidStars / max(1, repo_forks_delta_90d); if
       fork_ratio > STARS_SCOUT_FORK_RATIO (100.0), apply the lockstep
       penalty: ValidStars = int(ValidStars * STARS_SCOUT_LOCKSTEP_PENALTY).
    5. Return ValidStars.
    """
    cutoff = now - timedelta(days=STARS_SCOUT_WINDOW_DAYS)
    recent_stars = [e for e in stargazer_events if e.starred_at >= cutoff]

    valid_stars = sum(
        1 for e in recent_stars if e.user_total_platform_events >= STARS_SCOUT_MIN_EVENTS
    )

    if len(recent_stars) > STARS_SCOUT_BURST_THRESHOLD:
        fork_ratio = valid_stars / max(1, repo_forks_delta_90d)
        if fork_ratio > STARS_SCOUT_FORK_RATIO:
            valid_stars = int(valid_stars * STARS_SCOUT_LOCKSTEP_PENALTY)

    return valid_stars
