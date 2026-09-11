"""Unit tests for cvi.playbook: Maintainer Improvement Playbook Engine.

Verifies:
- Accurate diagnostic detection of the 8 canonical friction bottlenecks.
- Exact WAM point gains (delta_cvi), parameter score changes (delta_param_score), and friction changes (delta_friction).
- Priority ordering (descending by delta_cvi).
- Stage 0 Black Hole recovery mechanics.
- Potential CVI, projected tier, and projected quadrant computations.
- Zero-bottleneck and edge case behavior.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import pytest

from cvi.models import (
    AIPosture,
    AuthorAssociation,
    Ecosystem,
    LegalIPModel,
    PillarScores,
    RawPREvent,
    RepoMeta,
    Tier,
)
from cvi.playbook import generate_maintainer_playbook

NOW = datetime(2026, 9, 11, 12, 0, 0)


def _make_meta(**overrides) -> RepoMeta:
    """Helper to create healthy RepoMeta baseline."""
    base = {
        "owner": "test-org",
        "name": "test-repo",
        "ecosystem": Ecosystem.PYPI,
        "stars": 10000,
        "forks": 1500,
        "monthly_downloads": 1000000,
        "dependents_count": 5000,
        "elephant_factor": 4,
        "legal_ip_model": LegalIPModel.DCO,
        "ai_posture": AIPosture.REGULATED,
        "scorecard_score": 9.5,
        "active_nonbot_commits_180d": 200,
        "off_platform_reviews": False,
        "hard_ai_ban": False,
        "active_unclaimed_gfis": 15,
        "mean_inline_comments": 3.5,
        "rr_90": 0.6,
        "dx_ci_minutes": 15.0,
    }
    base.update(overrides)
    return RepoMeta(**base)


def _make_pr(
    pr_number: int,
    created_days_ago: float,
    *,
    author_login: str = "contrib",
    is_external: bool = True,
    merged: bool = True,
    closed: bool = False,
    response_latency_hours: float = 6.0,
    comments: int = 3,
    prior_commits: int = 0,
) -> RawPREvent:
    created = NOW - timedelta(days=created_days_ago)
    merged_at = created + timedelta(hours=2) if merged else None
    closed_at = created + timedelta(hours=2) if (merged or closed) else None
    return RawPREvent(
        repo_owner="test-org",
        repo_name="test-repo",
        pr_number=pr_number,
        created_at=created,
        author_login=author_login,
        author_association=AuthorAssociation.FIRST_TIME if prior_commits == 0 else AuthorAssociation.MEMBER,
        author_prior_merged_commits=prior_commits,
        closed_at=closed_at,
        merged_at=merged_at,
        is_external=is_external,
        response_latency_hours=response_latency_hours,
        maintainer_review_comments=comments,
    )


def test_zero_bottlenecks_clean_repo() -> None:
    """Healthy repo with zero bottlenecks produces empty actions and no point gain."""
    meta = _make_meta()
    prs = [
        _make_pr(1, 2.0, response_latency_hours=6.0, comments=3, merged=True),
        _make_pr(2, 4.0, response_latency_hours=8.0, comments=2, merged=True),
    ]
    playbook = generate_maintainer_playbook(meta, prs, now=NOW)
    assert playbook["actions"] == []
    assert playbook["total_point_gain"] == 0.0
    assert playbook["potential_cvi"] == playbook["current_cvi"]
    assert playbook["projected_tier"] == playbook["current_tier"]
    assert playbook["projected_quadrant"] == playbook["current_quadrant"]


def test_review_latency_friction_ttfhr_over_24h() -> None:
    """TTFHR > 24h triggers Review Latency Friction bottleneck."""
    meta = _make_meta()
    # Latencies of 48h -> p_curr = 100 - 25 * log2(48/12) = 50.0.
    # Target score = 100.0 => delta_p = 50.0
    # delta_cvi = 0.25 * 0.35 * 50.0 = 4.375 -> 4.38
    # delta_friction = -0.50 * 0.35 * 50.0 = -8.75
    prs = [
        _make_pr(1, 5.0, response_latency_hours=48.0, comments=2, merged=True),
        _make_pr(2, 6.0, response_latency_hours=48.0, comments=2, merged=True),
    ]
    playbook = generate_maintainer_playbook(meta, prs, now=NOW)
    actions = playbook["actions"]
    assert len(actions) == 1
    a = actions[0]
    assert "TTFHR > 24h" in a["bottleneck"]
    assert a["delta_param_score"] == pytest.approx(50.0, abs=0.1)
    assert a["delta_cvi"] == pytest.approx(4.38, abs=0.02)
    assert a["delta_friction"] == pytest.approx(-8.75, abs=0.02)


def test_pr_ghost_rate_over_10_percent() -> None:
    """GR_14 > 10% (but <= 50%) triggers PR Ghost Rate bottleneck."""
    meta = _make_meta()
    # 1 ghost PR (>14d, unmerged, 0 comments) out of 4 total external PRs = 25% ghost rate.
    # p_curr = 100 - 0.25 * 200 = 50.0. delta_p = 50.0.
    # delta_cvi = 0.25 * 0.20 * 50 = 2.50.
    # delta_friction = -0.50 * 0.20 * 50 = -5.00.
    ghost = RawPREvent(
        repo_owner="test-org",
        repo_name="test-repo",
        pr_number=1,
        created_at=NOW - timedelta(days=20),
        author_login="ghost-author",
        author_association=AuthorAssociation.NONE,
        author_prior_merged_commits=0,
        closed_at=None,
        merged_at=None,
        is_external=True,
        response_latency_hours=None,
        maintainer_review_comments=0,
    )
    clean_prs = [
        _make_pr(2, 2.0, response_latency_hours=6.0, comments=2, merged=False, closed=False),
        _make_pr(3, 3.0, response_latency_hours=8.0, comments=2, merged=False, closed=False),
        _make_pr(4, 4.0, response_latency_hours=10.0, comments=2, merged=False, closed=False),
    ]
    playbook = generate_maintainer_playbook(meta, [ghost] + clean_prs, now=NOW)
    actions = [a for a in playbook["actions"] if "PR Ghost Rate" in a["bottleneck"]]
    assert len(actions) == 1
    a = actions[0]
    assert a["delta_param_score"] == pytest.approx(50.0, abs=0.1)
    assert a["delta_cvi"] == pytest.approx(2.50, abs=0.02)
    assert a["delta_friction"] == pytest.approx(-5.00, abs=0.02)


def test_stage0_black_hole_over_50_percent() -> None:
    """GR_14 > 50% triggers Stage 0 Black Hole with massive recovery CVI."""
    meta = _make_meta()
    # 3 ghosts and 1 clean open PR => GR_14 = 75% > 50% (Black Hole).
    ghosts = [
        RawPREvent(
            repo_owner="test-org",
            repo_name="test-repo",
            pr_number=i,
            created_at=NOW - timedelta(days=25),
            author_login=f"ghost-{i}",
            author_association=AuthorAssociation.NONE,
            author_prior_merged_commits=0,
            closed_at=None,
            merged_at=None,
            is_external=True,
            response_latency_hours=None,
            maintainer_review_comments=0,
        )
        for i in range(1, 4)
    ]
    clean = _make_pr(4, 2.0, response_latency_hours=6.0, comments=2, merged=False)
    clean.closed_at = None

    playbook = generate_maintainer_playbook(meta, ghosts + [clean], now=NOW)
    assert playbook["current_cvi"] == 0.0
    assert playbook["current_tier"] == Tier.D.value
    assert playbook["current_quadrant"] == "Graveyard"

    bh_actions = [a for a in playbook["actions"] if "Stage 0 Black Hole" in a["bottleneck"]]
    assert len(bh_actions) == 1
    bh = bh_actions[0]
    assert bh["delta_cvi"] > 40.0
    assert bh["delta_friction"] < 0.0
    assert playbook["potential_cvi"] > 50.0
    assert playbook["projected_tier"] != Tier.D.value


def test_elephant_factor_bottleneck() -> None:
    """EF <= 2 triggers Low Elephant Factor bottleneck."""
    meta_ef1 = _make_meta(elephant_factor=1)
    # EF=1 -> score = 25.0 -> delta_p = 75.0.
    # delta_cvi = 0.20 * 0.35 * 75 = 5.25.
    # delta_friction = -0.30 * 0.35 * 75 = -7.875 -> -7.88.
    playbook1 = generate_maintainer_playbook(meta_ef1, [], now=NOW)
    a1 = next(a for a in playbook1["actions"] if "Elephant Factor" in a["bottleneck"])
    assert a1["delta_param_score"] == pytest.approx(75.0, abs=0.1)
    assert a1["delta_cvi"] == pytest.approx(5.25, abs=0.02)
    assert a1["delta_friction"] == pytest.approx(-7.88, abs=0.02)

    meta_ef2 = _make_meta(elephant_factor=2)
    # EF=2 -> score = 50.0 -> delta_p = 50.0.
    # delta_cvi = 0.20 * 0.35 * 50 = 3.50.
    # delta_friction = -0.30 * 0.35 * 50 = -5.25.
    playbook2 = generate_maintainer_playbook(meta_ef2, [], now=NOW)
    a2 = next(a for a in playbook2["actions"] if "Elephant Factor" in a["bottleneck"])
    assert a2["delta_param_score"] == pytest.approx(50.0, abs=0.1)
    assert a2["delta_cvi"] == pytest.approx(3.50, abs=0.02)
    assert a2["delta_friction"] == pytest.approx(-5.25, abs=0.02)


def test_corporate_cla_relicensing_hazards() -> None:
    """Corporate CLA Unilateral and Reciprocal trigger relicensing hazard actions."""
    meta_uni = _make_meta(legal_ip_model=LegalIPModel.CORP_CLA_UNILATERAL)
    # Score = 10.0 -> delta_p = 90.0.
    # delta_cvi = 0.20 * 0.35 * 90 = 6.30.
    # delta_friction = -0.30 * 0.35 * 90 = -9.45.
    pb_uni = generate_maintainer_playbook(meta_uni, [], now=NOW)
    a_uni = next(a for a in pb_uni["actions"] if "Unilateral" in a["bottleneck"])
    assert a_uni["delta_param_score"] == pytest.approx(90.0, abs=0.1)
    assert a_uni["delta_cvi"] == pytest.approx(6.30, abs=0.02)
    assert a_uni["delta_friction"] == pytest.approx(-9.45, abs=0.02)

    meta_rec = _make_meta(legal_ip_model=LegalIPModel.CORP_CLA_RECIPROCAL)
    # Score = 40.0 -> delta_p = 60.0.
    # delta_cvi = 0.20 * 0.35 * 60 = 4.20.
    # delta_friction = -0.30 * 0.35 * 60 = -6.30.
    pb_rec = generate_maintainer_playbook(meta_rec, [], now=NOW)
    a_rec = next(a for a in pb_rec["actions"] if "Reciprocal" in a["bottleneck"])
    assert a_rec["delta_param_score"] == pytest.approx(60.0, abs=0.1)
    assert a_rec["delta_cvi"] == pytest.approx(4.20, abs=0.02)
    assert a_rec["delta_friction"] == pytest.approx(-6.30, abs=0.02)


def test_deficient_gfis_under_10() -> None:
    """Active unclaimed GFIs < 10 triggers Deficient Good First Issues."""
    meta = _make_meta(active_unclaimed_gfis=4)
    # Score = 4/10 * 100 = 40.0 -> delta_p = 60.0.
    # delta_cvi = 0.15 * 0.40 * 60 = 3.60.
    # delta_friction = -0.20 * 0.40 * 60 = -4.80.
    pb = generate_maintainer_playbook(meta, [], now=NOW)
    a = next(a for a in pb["actions"] if "Good First Issues" in a["bottleneck"])
    assert a["delta_param_score"] == pytest.approx(60.0, abs=0.1)
    assert a["delta_cvi"] == pytest.approx(3.60, abs=0.02)
    assert a["delta_friction"] == pytest.approx(-4.80, abs=0.02)


def test_first_timer_merge_friction() -> None:
    """MR_first < 80% among decided first-timers triggers merge friction."""
    meta = _make_meta()
    # 1 merged, 3 closed without merge => MR_first = 1/4 = 25%.
    # Score = 25% * 125 = 31.25. delta_p = 68.75.
    # delta_cvi = 0.25 * 0.25 * 68.75 = 4.296875 -> 4.30.
    # delta_friction = -0.50 * 0.25 * 68.75 = -8.59375 -> -8.59.
    prs = [
        _make_pr(1, 2.0, prior_commits=0, merged=True),
        _make_pr(2, 3.0, prior_commits=0, merged=False, closed=True),
        _make_pr(3, 4.0, prior_commits=0, merged=False, closed=True),
        _make_pr(4, 5.0, prior_commits=0, merged=False, closed=True),
    ]
    pb = generate_maintainer_playbook(meta, prs, now=NOW)
    a = next(a for a in pb["actions"] if "First-Timer Merge Friction" in a["bottleneck"])
    assert a["delta_param_score"] == pytest.approx(68.75, abs=0.1)
    assert a["delta_cvi"] == pytest.approx(4.30, abs=0.02)
    assert a["delta_friction"] == pytest.approx(-8.59, abs=0.02)


def test_weak_pipeline_defenses_under_8() -> None:
    """OpenSSF Scorecard < 8.0 targets 9.0 with delta_friction = 0.0."""
    meta = _make_meta(scorecard_score=5.0)
    # Score = 50.0. Target score = 90.0 => delta_p = 40.0.
    # delta_cvi = 0.15 * 0.40 * 40 = 2.40.
    # delta_friction = 0.0 (Rigor not in friction).
    pb = generate_maintainer_playbook(meta, [], now=NOW)
    a = next(a for a in pb["actions"] if "Pipeline Defenses" in a["bottleneck"])
    assert a["delta_param_score"] == pytest.approx(40.0, abs=0.1)
    assert a["delta_cvi"] == pytest.approx(2.40, abs=0.02)
    assert a["delta_friction"] == 0.0


def test_playbook_action_prioritization_and_tier_projection() -> None:
    """All detected bottlenecks are sorted descending by delta_cvi and prioritize properly."""
    meta = _make_meta(
        elephant_factor=1,  # +5.25
        legal_ip_model=LegalIPModel.CORP_CLA_UNILATERAL,  # +6.30
        active_unclaimed_gfis=2,  # +4.80
        scorecard_score=4.0,  # +3.00
    )
    pb = generate_maintainer_playbook(meta, [], now=NOW)
    actions = pb["actions"]
    assert len(actions) == 4
    # Priorities should be strictly 1, 2, 3, 4
    assert [a["priority"] for a in actions] == [1, 2, 3, 4]
    # Ordering must be descending by delta_cvi
    cvis = [a["delta_cvi"] for a in actions]
    assert cvis == sorted(cvis, reverse=True)
    # Unilateral CLA (+6.30) should be Priority 1
    assert "Unilateral" in actions[0]["bottleneck"]
    # Elephant factor (+5.25) should be Priority 2
    assert "Elephant Factor" in actions[1]["bottleneck"]
    # Total point gain is sum of action delta_cvis
    assert pb["total_point_gain"] == pytest.approx(sum(cvis), abs=0.01)
    assert pb["potential_cvi"] >= pb["current_cvi"]


def test_playbook_potential_cvi_capped_at_100() -> None:
    """Potential CVI is strictly capped at 100.0."""
    meta = _make_meta(
        elephant_factor=1,
        legal_ip_model=LegalIPModel.CORP_CLA_UNILATERAL,
    )
    # Artificially provide high initial CVI
    pb = generate_maintainer_playbook(
        meta,
        [],
        pillar_scores=PillarScores(95, 95, 95, 95, 95),
        overall_cvi=96.0,
        now=NOW,
    )
    assert pb["potential_cvi"] <= 100.0
    assert pb["projected_tier"] == Tier.S_PLUS.value


def test_playbook_bot_filtering_in_ttfhr() -> None:
    """Bot reviews are ignored in TTFHR and do not cause false latency bottleneck."""
    meta = _make_meta()
    bot_pr = _make_pr(1, 2.0, author_login="dependabot[bot]", response_latency_hours=120.0)
    human_pr = _make_pr(2, 3.0, author_login="alice", response_latency_hours=8.0)
    pb = generate_maintainer_playbook(meta, [bot_pr, human_pr], now=NOW)
    # TTFHR for alice is 8.0h <= 24h, so no Review Latency Friction bottleneck
    latency_actions = [a for a in pb["actions"] if "TTFHR > 24h" in a["bottleneck"]]
    assert len(latency_actions) == 0


def test_playbook_with_provided_zero_cvi_and_pillars() -> None:
    """Providing explicit 0.0 CVI and zero pillars for black hole behaves correctly."""
    meta = _make_meta()
    ghost = RawPREvent(
        repo_owner="test-org",
        repo_name="test-repo",
        pr_number=1,
        created_at=NOW - timedelta(days=20),
        author_login="ghost-author",
        author_association=AuthorAssociation.NONE,
        author_prior_merged_commits=0,
        closed_at=None,
        merged_at=None,
        is_external=True,
        response_latency_hours=None,
        maintainer_review_comments=0,
    )
    pb = generate_maintainer_playbook(
        meta,
        [ghost],
        pillar_scores=PillarScores(0, 0, 0, 0, 0),
        overall_cvi=0.0,
        now=NOW,
    )
    assert pb["current_cvi"] == 0.0
    assert pb["current_tier"] == Tier.D.value
    assert pb["current_quadrant"] == "Graveyard"

