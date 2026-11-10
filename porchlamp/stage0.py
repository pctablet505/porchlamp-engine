"""Phase 0: Elimination-by-Aspects (EBA) hard gating (spec 2, Phase 0).

Non-compensatory gates: a single hard failure disqualifies the repository
before any scoring. Gates are evaluated in the fixed order
1 -> 2 -> 4 -> 4b -> 3; the first hard failure wins. Gate 3 (off-platform
reviews) is *routing*, not a failure: it passes the repository through to a
specialized workflow while flagging ``is_off_platform``.

Gate semantics (docs/ARCHITECTURE.md, Phase 0):

1. Maintenance Vitality — at least one non-bot commit in the trailing
   ``MAINTENANCE_WINDOW_DAYS`` (180) days.
2. Responsiveness Ceiling — external PR ghost rate GR_14 must be
   ``<= GHOST_RATE_GATE`` (0.50). GR_14 = (open external PRs older than 14
   days with zero maintainer review comments) / max(1, total open external
   PRs). Above the ceiling => Black Hole.
4. Persona Constraint — a repository with a hard generative-AI ban
   disqualifies AI-assisted persona views.
4b. Persona Constraint (spec 4, Stage 0 Constraint Gate) — the
   ``senior_systems`` persona is Multi-Vendor-only: repositories with
   ``elephant_factor < 3`` are hard-disqualified for senior_systems views
   (EF >= 3). The remaining spec-4 persona gates (first_timer "Low-Barrier
   Only", career_capital "High Tier Brand Only") are intentionally
   UNIMPLEMENTED: the spec does not define their concrete semantics, and
   they await owner definition. Do not guess them.
3. Off-Platform Reviews — mailing-list review workflows (e.g. torvalds/linux)
   are routed to specialized workflows instead of being penalized as
   unresponsive.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from .constants import GHOST_RATE_GATE
from .models import Persona, RawPREvent, RepoMeta, Stage0Result

# Ghost-rate lookback: an open external PR is a "ghost" when it has been open
# for more than 14 days with no maintainer review comment (spec Phase 0,
# GR_14).
_GHOST_AGE_DAYS: int = 14


def _ghost_rate_14(pr_events: list[RawPREvent], now: datetime) -> float:
    """Compute GR_14 over the open external PR population.

    Ghost = open external PR (not merged, not closed-unmerged) with
    ``now - created_at > 14 days`` and zero maintainer review comments.
    Rate = ghosts / max(1, total open external PRs).
    """
    open_external = [
        pr
        for pr in pr_events
        if pr.is_external and not pr.is_merged and not pr.is_closed_unmerged
    ]
    if not open_external:
        return 0.0
    ghost_horizon = now - timedelta(days=_GHOST_AGE_DAYS)
    ghosts = sum(
        1
        for pr in open_external
        if pr.created_at < ghost_horizon and pr.maintainer_review_comments == 0
    )
    return ghosts / len(open_external)


def apply_stage0(
    meta: RepoMeta,
    pr_events: list[RawPREvent],
    now: datetime,
    ai_persona: bool = False,
    persona: Optional[Persona] = None,
) -> Stage0Result:
    """Run the Phase 0 EBA gates; return the first hard failure or a pass.

    Gate order: 1 (maintenance) -> 2 (ghost rate) -> 4 (AI ban) -> 4b
    (senior_systems multi-vendor constraint) -> 3 (off-platform routing).
    Off-platform is not a failure, so it is evaluated last and only sets
    routing flags on an otherwise-passing repository.

    ``persona`` enables persona-specific Stage 0 constraints (spec 4). Only
    ``Persona.SENIOR_SYSTEMS`` is implemented (multi-vendor, EF >= 3);
    ``None`` (the default) disables every persona constraint.
    """
    # Gate 1: Maintenance Vitality.
    if meta.active_nonbot_commits_180d < 1:
        return Stage0Result(
            passed=False,
            reason="no non-bot commit in trailing 180 days",
        )

    # Gate 2: Responsiveness Ceiling (GR_14 <= GHOST_RATE_GATE).
    if _ghost_rate_14(pr_events, now) > GHOST_RATE_GATE:
        return Stage0Result(
            passed=False,
            reason="PR ghost rate > 50% (black hole)",
            is_black_hole=True,
        )

    # Gate 4: Persona Constraint (hard AI ban vs AI-assisted persona).
    if meta.hard_ai_ban and ai_persona:
        return Stage0Result(
            passed=False,
            reason="hard AI ban disqualifies AI-assisted persona",
            is_ai_ban_disqualified=True,
        )

    # Gate 4b: Persona Constraint (spec 4) — senior_systems is
    # Multi-Vendor-only: elephant_factor must be >= 3.
    if persona is Persona.SENIOR_SYSTEMS and meta.elephant_factor < 3:
        return Stage0Result(
            passed=False,
            reason="senior_systems multi-vendor gate: elephant factor (EF) must be >= 3",
        )

    # Gate 3: Off-Platform Reviews — routing, not disqualification.
    if meta.off_platform_reviews:
        return Stage0Result(
            passed=True,
            reason="off-platform review workflow (specialized routing)",
            is_off_platform=True,
        )

    return Stage0Result(passed=True, reason=None)
