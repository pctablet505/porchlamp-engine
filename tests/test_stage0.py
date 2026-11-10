"""Phase 0 Elimination-by-Aspects (EBA) hard-gating tests.

Exact-oracle tests for ``porchlamp.stage0.apply_stage0``. Every assertion pins the
full ``Stage0Result`` (passed, reason, is_black_hole, is_off_platform,
is_ai_ban_disqualified) to a hand-computed expectation, so any drift in gate
order, threshold, or flag wiring fails loudly.

Gate order under test: 1 (maintenance) -> 2 (ghost rate) -> 4 (AI ban) ->
3 (off-platform routing). The first hard failure wins; off-platform is
routing, not a failure.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from porchlamp.constants import GHOST_RATE_GATE
from porchlamp.models import (
    AuthorAssociation,
    Ecosystem,
    RawPREvent,
    RepoMeta,
    Stage0Result,
)
from porchlamp.stage0 import apply_stage0

NOW = datetime(2026, 9, 11, 12, 0, 0)


def _meta(**kw) -> RepoMeta:
    base = dict(
        owner="acme",
        name="widget",
        ecosystem=Ecosystem.PYPI,
        active_nonbot_commits_180d=3,
        off_platform_reviews=False,
        hard_ai_ban=False,
    )
    base.update(kw)
    return RepoMeta(**base)


def _pr(
    days_open: float,
    *,
    external: bool = True,
    merged: bool = False,
    closed_unmerged: bool = False,
    review_comments: int = 0,
    pr_number: int = 1,
) -> RawPREvent:
    created = NOW - timedelta(days=days_open)
    return RawPREvent(
        repo_owner="acme",
        repo_name="widget",
        pr_number=pr_number,
        created_at=created,
        author_login="outsider",
        author_association=AuthorAssociation.NONE,
        is_external=external,
        merged_at=(created + timedelta(days=1)) if merged else None,
        closed_at=(created + timedelta(days=1)) if closed_unmerged else None,
        maintainer_review_comments=review_comments,
    )


def _assert_full(result: Stage0Result, **expected) -> None:
    """Assert every Stage0Result field equals the hand-computed expectation."""
    full = dict(
        passed=expected.get("passed", True),
        reason=expected.get("reason"),
        is_black_hole=expected.get("is_black_hole", False),
        is_off_platform=expected.get("is_off_platform", False),
        is_ai_ban_disqualified=expected.get("is_ai_ban_disqualified", False),
    )
    assert result.passed == full["passed"], f"passed: {result.passed!r} != {full['passed']!r}"
    assert result.reason == full["reason"], f"reason: {result.reason!r} != {full['reason']!r}"
    assert result.is_black_hole == full["is_black_hole"], f"is_black_hole: {result.is_black_hole!r} != {full['is_black_hole']!r}"
    assert result.is_off_platform == full["is_off_platform"], f"is_off_platform: {result.is_off_platform!r} != {full['is_off_platform']!r}"
    assert result.is_ai_ban_disqualified == full["is_ai_ban_disqualified"], f"is_ai_ban_disqualified: {result.is_ai_ban_disqualified!r} != {full['is_ai_ban_disqualified']!r}"


# --------------------------------------------------------------------------- #
# Gate 1: Maintenance Vitality
# --------------------------------------------------------------------------- #
def test_gate1_zero_nonbot_commits_disqualifies():
    r = apply_stage0(_meta(active_nonbot_commits_180d=0), [], NOW)
    _assert_full(
        r,
        passed=False,
        reason="no non-bot commit in trailing 180 days",
    )


def test_gate1_single_nonbot_commit_passes():
    r = apply_stage0(_meta(active_nonbot_commits_180d=1), [], NOW)
    _assert_full(r)


def test_gate1_many_nonbot_commits_passes():
    r = apply_stage0(_meta(active_nonbot_commits_180d=42), [], NOW)
    _assert_full(r)


# --------------------------------------------------------------------------- #
# Gate 2: Responsiveness Ceiling (GR_14)
# --------------------------------------------------------------------------- #
def test_gate2_ghost_rate_above_gate_is_black_hole():
    # 3 open external PRs > 14d old with zero maintainer review comments.
    prs = [_pr(20, pr_number=i) for i in range(1, 4)]
    r = apply_stage0(_meta(), prs, NOW)
    _assert_full(
        r,
        passed=False,
        reason="PR ghost rate > 50% (black hole)",
        is_black_hole=True,
    )


def test_gate2_ghost_rate_exactly_at_gate_passes():
    # 1 ghost / 2 open = 0.50 == GHOST_RATE_GATE; gate is "must be <= 0.50".
    assert GHOST_RATE_GATE == 0.50
    prs = [_pr(30, pr_number=1), _pr(5, pr_number=2)]
    r = apply_stage0(_meta(), prs, NOW)
    _assert_full(r)


def test_gate2_ghost_rate_just_above_gate_fails():
    # 2 ghosts / 3 open = 0.6667 > 0.50.
    prs = [_pr(30, pr_number=1), _pr(30, pr_number=2), _pr(5, pr_number=3)]
    r = apply_stage0(_meta(), prs, NOW)
    _assert_full(
        r,
        passed=False,
        reason="PR ghost rate > 50% (black hole)",
        is_black_hole=True,
    )


def test_gate2_pr_exactly_14_days_old_not_ghost():
    # now - created_at == 14d exactly; spec requires strictly > 14 days.
    prs = [_pr(14.0, pr_number=1)]
    r = apply_stage0(_meta(), prs, NOW)
    _assert_full(r)


def test_gate2_pr_just_over_14_days_is_ghost():
    prs = [_pr(14.0 + 1 / 24, pr_number=1)]  # 14d 1h
    r = apply_stage0(_meta(), prs, NOW)
    _assert_full(
        r,
        passed=False,
        reason="PR ghost rate > 50% (black hole)",
        is_black_hole=True,
    )


def test_gate2_open_pr_with_review_comment_not_ghost():
    prs = [_pr(30, review_comments=2, pr_number=1)]
    r = apply_stage0(_meta(), prs, NOW)
    _assert_full(r)


def test_gate2_merged_prs_excluded_from_open_population():
    prs = [_pr(30, merged=True, pr_number=i) for i in range(1, 6)]
    r = apply_stage0(_meta(), prs, NOW)
    _assert_full(r)


def test_gate2_closed_unmerged_prs_excluded_from_open_population():
    prs = [_pr(30, closed_unmerged=True, pr_number=i) for i in range(1, 6)]
    r = apply_stage0(_meta(), prs, NOW)
    _assert_full(r)


def test_gate2_internal_prs_excluded_from_population():
    # 4 open *internal* PRs > 14d, zero comments: external population empty.
    prs = [_pr(30, external=False, pr_number=i) for i in range(1, 5)]
    r = apply_stage0(_meta(), prs, NOW)
    _assert_full(r)


def test_gate2_mixed_population_one_ghost_of_two():
    # 1 open external ghost + 1 open external answered = 0.50 -> pass.
    prs = [_pr(30, pr_number=1), _pr(30, review_comments=1, pr_number=2)]
    r = apply_stage0(_meta(), prs, NOW)
    _assert_full(r)


def test_gate2_empty_pr_events_ghost_rate_zero():
    r = apply_stage0(_meta(), [], NOW)
    _assert_full(r)


# --------------------------------------------------------------------------- #
# Gate 4: Persona Constraint (hard AI ban)
# --------------------------------------------------------------------------- #
def test_gate4_hard_ai_ban_with_ai_persona_disqualifies():
    r = apply_stage0(_meta(hard_ai_ban=True), [], NOW, ai_persona=True)
    _assert_full(
        r,
        passed=False,
        reason="hard AI ban disqualifies AI-assisted persona",
        is_ai_ban_disqualified=True,
    )


def test_gate4_hard_ai_ban_with_human_persona_passes():
    r = apply_stage0(_meta(hard_ai_ban=True), [], NOW, ai_persona=False)
    _assert_full(r)


def test_gate4_no_hard_ai_ban_with_ai_persona_passes():
    r = apply_stage0(_meta(hard_ai_ban=False), [], NOW, ai_persona=True)
    _assert_full(r)


# --------------------------------------------------------------------------- #
# Gate 3: Off-Platform Reviews (routing, not disqualification)
# --------------------------------------------------------------------------- #
def test_gate3_off_platform_routes_but_passes():
    r = apply_stage0(_meta(off_platform_reviews=True), [], NOW)
    _assert_full(
        r,
        passed=True,
        reason="off-platform review workflow (specialized routing)",
        is_off_platform=True,
    )


def test_gate3_off_platform_not_set_leaves_flag_false():
    r = apply_stage0(_meta(off_platform_reviews=False), [], NOW)
    _assert_full(r)


# --------------------------------------------------------------------------- #
# Gate ordering: first hard failure wins
# --------------------------------------------------------------------------- #
def test_order_maintenance_beats_ghost_rate():
    prs = [_pr(30, pr_number=i) for i in range(1, 4)]
    r = apply_stage0(_meta(active_nonbot_commits_180d=0), prs, NOW)
    _assert_full(
        r,
        passed=False,
        reason="no non-bot commit in trailing 180 days",
    )


def test_order_ghost_rate_beats_ai_ban():
    prs = [_pr(30, pr_number=i) for i in range(1, 4)]
    r = apply_stage0(_meta(hard_ai_ban=True), prs, NOW, ai_persona=True)
    _assert_full(
        r,
        passed=False,
        reason="PR ghost rate > 50% (black hole)",
        is_black_hole=True,
    )


def test_order_ai_ban_beats_off_platform_routing():
    r = apply_stage0(_meta(hard_ai_ban=True, off_platform_reviews=True), [], NOW, ai_persona=True)
    _assert_full(
        r,
        passed=False,
        reason="hard AI ban disqualifies AI-assisted persona",
        is_ai_ban_disqualified=True,
    )


def test_off_platform_does_not_mask_black_hole():
    # Off-platform is routing, not a failure: a black-hole ghost rate still
    # disqualifies even when the repo is off-platform.
    prs = [_pr(30, pr_number=i) for i in range(1, 4)]
    r = apply_stage0(_meta(off_platform_reviews=True), prs, NOW)
    _assert_full(
        r,
        passed=False,
        reason="PR ghost rate > 50% (black hole)",
        is_black_hole=True,
    )


def test_all_gates_pass_clean():
    r = apply_stage0(_meta(), [], NOW)
    _assert_full(r)
