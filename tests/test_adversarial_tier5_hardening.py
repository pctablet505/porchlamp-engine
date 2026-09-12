"""Milestone 4 Phase 2: White-Box Adversarial Coverage Hardening (Tier 5).

Adversarial stress-testing suite targeting subtle edge cases, boundary conditions,
numerical stability, corrupt/null inputs, deep pagination combinations,
Kaplan-Meier survival ties, and StarScout anti-gaming thresholds.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import pytest

from cvi.constants import (
    PERSONA_PRESETS,
)
from cvi.deepdive import _survival_curve
from cvi.engine import assign_tier
from cvi.lakehouse import OperationalStore
from cvi.matrix import decompose
from cvi.models import (
    AuthorAssociation,
    Ecosystem,
    LegalIPModel,
    OpenSSFSignals,
    Persona,
    PillarScores,
    Quadrant,
    RawPREvent,
    RepoMeta,
    StargazerEvent,
    Tier,
)
from cvi.normalize import compute_ecosystem_percentile
from cvi.openssf import calculate_pike_criticality
from cvi.persona import (
    rank_deltas,
    reproject,
    validate_persona_preset,
)
from cvi.pillars.accessibility import (
    score_dx,
    score_gfi,
)
from cvi.pillars.criticality import (
    _log10_plus_one,
)
from cvi.pillars.governance import (
    score_decentralization,
)
from cvi.pillars.responsiveness import (
    score_ghost_rate,
    score_mr_first,
    score_ttfhr,
    score_ttm_p90,
)
from cvi.pillars.rigor import (
    score_review_depth,
)
from cvi.playbook import generate_maintainer_playbook
from cvi.starscout import filter_fraudulent_star_velocity


class TestNumericalExtremesAndDivisionByZero:
    """Stress test numerical boundaries, floating point extremes, and zero division guards."""

    def test_compute_ecosystem_percentile_extremes(self) -> None:
        # Empty population returns neutral fallback 50.0
        assert compute_ecosystem_percentile(10.0, []) == 50.0

        # 1-element population
        assert compute_ecosystem_percentile(5.0, [5.0]) == 100.0
        assert compute_ecosystem_percentile(4.99, [5.0]) == 0.0

        # Uniform population (10,000 identical items)
        pop_uniform = [42.0] * 10_000
        assert compute_ecosystem_percentile(42.0, pop_uniform) == 100.0
        assert compute_ecosystem_percentile(41.9, pop_uniform) == 0.0

        # Extreme values: -1e308 and +1e308
        pop_extreme = [-1e308, 0.0, 1e308]
        assert compute_ecosystem_percentile(-1e308, pop_extreme) == 33.33
        assert compute_ecosystem_percentile(0.0, pop_extreme) == 66.67
        assert compute_ecosystem_percentile(1e308, pop_extreme) == 100.0
        assert compute_ecosystem_percentile(-2e308, pop_extreme) == 0.0
        assert compute_ecosystem_percentile(2e308, pop_extreme) == 100.0

    def test_log10_plus_one_guards(self) -> None:
        assert _log10_plus_one(-1000) == 0.0
        assert _log10_plus_one(-1) == 0.0
        assert _log10_plus_one(0) == 0.0
        assert round(_log10_plus_one(9), 4) == 1.0
        assert round(_log10_plus_one(99), 4) == 2.0
        # 64-bit maximum integer
        large_int = 2**63 - 1
        assert _log10_plus_one(large_int) > 18.0

    def test_score_ttfhr_extremes(self) -> None:
        now = datetime(2026, 9, 11, 12, 0, 0)
        # Empty PR events -> 50.0
        assert score_ttfhr([]) == 50.0

        # Only bot PRs -> 50.0
        bot_pr = RawPREvent(
            repo_owner="test",
            repo_name="repo",
            pr_number=1,
            created_at=now,
            author_login="dependabot[bot]",
            author_association=AuthorAssociation.NONE,
            is_external=True,
            response_latency_hours=1.0,
        )
        assert score_ttfhr([bot_pr]) == 50.0

        # None latencies -> 50.0
        none_pr = RawPREvent(
            repo_owner="test",
            repo_name="repo",
            pr_number=2,
            created_at=now,
            author_login="contributor",
            author_association=AuthorAssociation.NONE,
            is_external=True,
            response_latency_hours=None,
        )
        assert score_ttfhr([none_pr]) == 50.0

        # Negative latency (clock skew) -> latency <= 12.0 -> 100.0
        neg_pr = RawPREvent(
            repo_owner="test",
            repo_name="repo",
            pr_number=3,
            created_at=now,
            author_login="contributor",
            author_association=AuthorAssociation.NONE,
            is_external=True,
            response_latency_hours=-5.0,
        )
        assert score_ttfhr([neg_pr]) == 100.0

        # Massive latency -> 0.0 floored
        huge_pr = RawPREvent(
            repo_owner="test",
            repo_name="repo",
            pr_number=4,
            created_at=now,
            author_login="contributor",
            author_association=AuthorAssociation.NONE,
            is_external=True,
            response_latency_hours=1e9,
        )
        assert score_ttfhr([huge_pr]) == 0.0

    def test_score_mr_first_extremes(self) -> None:
        now = datetime(2026, 9, 11, 12, 0, 0)
        # 0 decided first-timers -> neutral 50.0
        assert score_mr_first([]) == 50.0

        # Returning author decided PR -> still 0 first timers -> 50.0
        returning_pr = RawPREvent(
            repo_owner="test",
            repo_name="repo",
            pr_number=1,
            created_at=now,
            author_login="alice",
            author_association=AuthorAssociation.NONE,
            author_prior_merged_commits=5,
            is_external=True,
            merged_at=now + timedelta(hours=2),
        )
        assert score_mr_first([returning_pr]) == 50.0

        # Decided first-timer, merged -> 100.0
        ft_merged = RawPREvent(
            repo_owner="test",
            repo_name="repo",
            pr_number=2,
            created_at=now,
            author_login="bob",
            author_association=AuthorAssociation.NONE,
            author_prior_merged_commits=0,
            is_external=True,
            merged_at=now + timedelta(hours=2),
        )
        assert score_mr_first([ft_merged]) == 100.0

        # Decided first-timer, closed unmerged -> 0.0
        ft_closed = RawPREvent(
            repo_owner="test",
            repo_name="repo",
            pr_number=3,
            created_at=now,
            author_login="charlie",
            author_association=AuthorAssociation.NONE,
            author_prior_merged_commits=0,
            is_external=True,
            closed_at=now + timedelta(hours=2),
        )
        assert score_mr_first([ft_closed]) == 0.0

    def test_score_ghost_rate_boundary(self) -> None:
        now = datetime(2026, 9, 11, 12, 0, 0)
        # Empty -> 50.0
        assert score_ghost_rate([], now) == 50.0

        # Exactly 14 days old: now - created_at == 14 days -> NOT a ghost
        pr_exact_14d = RawPREvent(
            repo_owner="test",
            repo_name="repo",
            pr_number=1,
            created_at=now - timedelta(days=14),
            author_login="alice",
            author_association=AuthorAssociation.NONE,
            is_external=True,
            maintainer_review_comments=0,
            closed_at=None,
        )
        assert score_ghost_rate([pr_exact_14d], now) == 100.0

        # 14 days + 1 second -> IS a ghost
        pr_ghost = RawPREvent(
            repo_owner="test",
            repo_name="repo",
            pr_number=2,
            created_at=now - timedelta(days=14, seconds=1),
            author_login="bob",
            author_association=AuthorAssociation.NONE,
            is_external=True,
            maintainer_review_comments=0,
            closed_at=None,
        )
        # 1 ghost out of 1 external -> ghost rate 1.0 -> 100 - 150*1.0 = -50 -> 0.0
        assert score_ghost_rate([pr_ghost], now) == 0.0

    def test_score_ttm_p90_extremes(self) -> None:
        now = datetime(2026, 9, 11, 12, 0, 0)
        # No merged PRs -> 50.0
        assert score_ttm_p90([], [10.0, 20.0]) == 50.0

        # Negative merge latency (clock drift: merged_at earlier than created_at)
        skewed_pr = RawPREvent(
            repo_owner="test",
            repo_name="repo",
            pr_number=1,
            created_at=now,
            author_login="alice",
            author_association=AuthorAssociation.NONE,
            is_external=True,
            merged_at=now - timedelta(hours=1),
        )
        score = score_ttm_p90([skewed_pr], [-10.0, 0.0, 10.0])
        assert 0.0 <= score <= 100.0

    def test_calculate_pike_criticality_extremes(self) -> None:
        zero_signals = OpenSSFSignals(
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
        score = calculate_pike_criticality(zero_signals)
        assert 0.0 <= score <= 1.0

        # Zero weights edge case
        assert calculate_pike_criticality(zero_signals, weights={"param": 0.0}) == 0.0

        # Negative values in signals
        neg_signals = OpenSSFSignals(
            created_since_months=-10.0,
            updated_since_months=-5.0,
            contributor_count=-1,
            org_count=-1,
            commit_frequency_weekly=-2.0,
            recent_releases_count=-5,
            closed_issues_count_90d=-10,
            updated_issues_count_90d=-10,
            comment_frequency=-1.0,
            dependents_count=-100,
        )
        score_neg = calculate_pike_criticality(neg_signals)
        assert 0.0 <= score_neg <= 1.0

    def test_score_review_depth_gaussian_extremes(self) -> None:
        # Exactly optimal 3.5 -> 100.0
        assert score_review_depth(3.5) == 100.0
        # Far right tail (1,000 comments) -> underflow to 0.0 without OverflowError
        assert score_review_depth(1000.0) == 0.0
        # Far left tail (-1,000 comments)
        assert score_review_depth(-1000.0) == 0.0

    def test_score_dx_ci_minutes_boundaries(self) -> None:
        assert score_dx(-10.0) == 50.0
        assert score_dx(0.0) == 50.0
        assert score_dx(10.0) == 100.0
        assert score_dx(20.0) == 100.0
        assert score_dx(25.0) == 75.0
        assert score_dx(40.0) == 0.0
        assert score_dx(100.0) == 0.0

    def test_score_gfi_and_decentralization_boundaries(self) -> None:
        assert score_gfi(-5) == 0.0
        assert score_gfi(0) == 0.0
        assert score_gfi(5) == 50.0
        assert score_gfi(10) == 100.0
        assert score_gfi(100) == 100.0

        assert score_decentralization(-2) == 0.0
        assert score_decentralization(0) == 0.0
        assert score_decentralization(1) == 25.0
        assert score_decentralization(2) == 50.0
        assert score_decentralization(3) == 75.0
        assert score_decentralization(4) == 100.0
        assert score_decentralization(10) == 100.0

    def test_assign_tier_boundaries_and_special_floats(self) -> None:
        assert assign_tier(100.0) == Tier.S_PLUS
        assert assign_tier(90.0) == Tier.S_PLUS
        assert assign_tier(89.999) == Tier.S
        assert assign_tier(80.0) == Tier.S
        assert assign_tier(79.999) == Tier.A
        assert assign_tier(70.0) == Tier.A
        assert assign_tier(69.999) == Tier.B
        assert assign_tier(55.0) == Tier.B
        assert assign_tier(54.999) == Tier.C
        assert assign_tier(40.0) == Tier.C
        assert assign_tier(39.999) == Tier.D
        assert assign_tier(0.0) == Tier.D
        assert assign_tier(-100.0) == Tier.D
        assert assign_tier(200.0) == Tier.S_PLUS
        assert assign_tier(float("inf")) == Tier.S_PLUS
        assert assign_tier(-float("inf")) == Tier.D
        assert assign_tier(float("nan")) == Tier.D

    def test_matrix_decomposition_boundary(self) -> None:
        # All pillars 50.0 -> lev=50.0, fric=50.0 -> Crucible
        ps_50 = PillarScores(50.0, 50.0, 50.0, 50.0, 50.0)
        lev, fric, quad = decompose(ps_50)
        assert lev == 50.0
        assert fric == 50.0
        assert quad == Quadrant.CRUCIBLE

        # Rounding boundary: 49.999 rounds to 50.00 -> Crucible
        ps_round = PillarScores(50.001, 49.999, 50.0, 50.0, 50.0)
        lev, fric, quad = decompose(ps_round)
        assert lev == 50.0
        assert fric == 50.0
        assert quad == Quadrant.CRUCIBLE

        # Direct coordinates without pillar_scores
        assert decompose(leverage=50.0, friction=50.0) == Quadrant.CRUCIBLE
        assert decompose(leverage=50.0, friction=49.99) == Quadrant.SWEET_SPOT
        assert decompose(leverage=49.99, friction=49.99) == Quadrant.NURSERY
        assert decompose(leverage=49.99, friction=50.0) == Quadrant.GRAVEYARD

        with pytest.raises(ValueError, match="Must provide either pillar_scores"):
            decompose()


class TestPersonaReProjectionAndWeightsHardening:
    """Stress test dynamic weight projection, float tolerances, and rank delta stabilization."""

    def test_persona_preset_self_checks(self) -> None:
        for p in Persona:
            validate_persona_preset(p)

    def test_reproject_weight_validation_failures(self) -> None:
        ps = PillarScores(80.0, 80.0, 80.0, 80.0, 80.0)
        valid = dict(PERSONA_PRESETS[Persona.BALANCED])

        # Missing key
        missing = dict(valid)
        del missing["responsiveness"]
        with pytest.raises(ValueError, match="weights must contain exactly the 5 pillar keys"):
            reproject(ps, missing)

        # Extra key
        extra = dict(valid)
        extra["bogus"] = 0.1
        with pytest.raises(ValueError, match="weights must contain exactly the 5 pillar keys"):
            reproject(ps, extra)

        # Sum drift > 1e-6
        bad_sum = dict(valid)
        bad_sum["responsiveness"] += 1e-5
        with pytest.raises(ValueError, match="weights must sum to 1.0 within 1e-6"):
            reproject(ps, bad_sum)

        # Sum drift within 1e-6 succeeds
        tol_sum = dict(valid)
        tol_sum["responsiveness"] += 5e-7
        tol_sum["accessibility"] -= 5e-7
        assert isinstance(reproject(ps, tol_sum), float)

    def test_rank_deltas_stress_and_ties(self) -> None:
        # Duplicate repo IDs
        with pytest.raises(ValueError, match="duplicate repo ids"):
            rank_deltas([("a", 10.0), ("a", 20.0)], {"a": 15.0})

        # Mismatched IDs
        with pytest.raises(ValueError, match="inputs must cover the same repo ids"):
            rank_deltas([("a", 10.0)], {"b": 10.0})

        # 500 repos with many tied scores
        n = 500
        before_scores = [(f"repo_{i:03d}", float(i % 10)) for i in range(n)]
        after_scores = {f"repo_{i:03d}": float((i * 3) % 10) for i in range(n)}
        deltas = rank_deltas(before_scores, after_scores)
        assert len(deltas) == n
        # Sum of ranks before == Sum of ranks after, so sum of deltas must equal 0
        assert sum(deltas.values()) == 0


class TestSQLiteCorruptDataAndNullFields:
    """Stress test OperationalStore against corrupt inputs, SQL injections, and null data."""

    def test_sql_injection_and_like_wildcards_in_search(self) -> None:
        store = OperationalStore(":memory:")
        ps = PillarScores(70.0, 70.0, 70.0, 70.0, 70.0)
        from cvi.models import CVICompositeResult

        res = CVICompositeResult(
            owner="safe_org",
            name="cool_repo_100%",
            ecosystem=Ecosystem.PYPI,
            overall_cvi=70.0,
            rank_tier=Tier.A,
            matrix_quadrant=Quadrant.SWEET_SPOT,
            leverage_score=70.0,
            friction_score=30.0,
            is_stage0_disqualified=False,
        )
        store.upsert_composite(res, ps)

        # Literal percent search matches exact repo
        items, count = store.leaderboard(search="100%")
        assert count == 1
        assert items[0]["name"] == "cool_repo_100%"

        # Percent as wildcard should NOT match every repo when escaped
        items, count = store.leaderboard(search="%")
        assert count == 1  # only cool_repo_100% contains literal '%'

        # SQL injection strings
        injections = [
            "' OR '1'='1",
            "'; DROP TABLE repositories; --",
            '" OR ""="',
            "\\",
            "\\\\",
            "%",
            "_",
            "a" * 2000,
        ]
        for inj in injections:
            items, count = store.leaderboard(search=inj)
            assert isinstance(count, int)
            assert isinstance(items, list)

        # Store still intact
        assert store.get_repo("safe_org", "cool_repo_100%") is not None
        store.close()

    def test_deep_dive_json_null_and_missing(self) -> None:
        store = OperationalStore(":memory:")
        assert store.get_repo("non", "existent") is None
        assert store.upsert_deep_dive("non", "existent", {"key": "val"}) == 0
        store.close()

    def test_leaderboard_pagination_extreme_offsets(self) -> None:
        store = OperationalStore(":memory:")
        ps = PillarScores(60.0, 60.0, 60.0, 60.0, 60.0)
        from cvi.models import CVICompositeResult

        for i in range(10):
            res = CVICompositeResult(
                owner=f"org_{i}",
                name=f"repo_{i}",
                ecosystem=Ecosystem.PYPI,
                overall_cvi=50.0 + i,
                rank_tier=Tier.B,
                matrix_quadrant=Quadrant.SWEET_SPOT,
                leverage_score=60.0,
                friction_score=40.0,
                is_stage0_disqualified=False,
            )
            store.upsert_composite(res, ps)

        # Deep offset beyond total count
        items, total = store.leaderboard(offset=1000, limit=50)
        assert total == 10
        assert items == []

        # Negative offset and limit sanitized to 0
        items, total = store.leaderboard(offset=-10, limit=-5)
        assert total == 10
        assert len(items) == 0

        # Persona re-projection with deep offset
        items, total = store.leaderboard(persona=Persona.FIRST_TIMER, offset=100, limit=20)
        assert total == 10
        assert items == []
        store.close()


class TestPlaybookAllBottlenecksAndEdgeCases:
    """Stress test Maintainer Improvement Playbook with 0 and all 8 simultaneous bottlenecks."""

    def test_playbook_clean_leader_zero_bottlenecks(self) -> None:
        now = datetime(2026, 9, 11, 12, 0, 0)
        meta = RepoMeta(
            owner="leader",
            name="top_project",
            ecosystem=Ecosystem.PYPI,
            elephant_factor=4,
            legal_ip_model=LegalIPModel.DCO,
            active_unclaimed_gfis=15,
            scorecard_score=9.5,
            active_nonbot_commits_180d=500,
        )
        pr = RawPREvent(
            repo_owner="leader",
            repo_name="top_project",
            pr_number=1,
            created_at=now - timedelta(hours=5),
            author_login="contributor",
            author_association=AuthorAssociation.NONE,
            is_external=True,
            response_latency_hours=2.0,
            merged_at=now - timedelta(hours=1),
            maintainer_review_comments=3,
        )
        pb = generate_maintainer_playbook(meta, [pr], now=now)
        assert pb["actions"] == []
        assert pb["total_point_gain"] == 0.0
        assert pb["potential_cvi"] == pb["current_cvi"]

    def test_playbook_all_eight_bottlenecks_simultaneously(self) -> None:
        now = datetime(2026, 9, 11, 12, 0, 0)
        meta = RepoMeta(
            owner="struggling",
            name="friction_hub",
            ecosystem=Ecosystem.PYPI,
            elephant_factor=1,  # Bottleneck 4: EF <= 2
            legal_ip_model=LegalIPModel.CORP_CLA_UNILATERAL,  # Bottleneck 5: CLA Unilateral
            active_unclaimed_gfis=2,  # Bottleneck 6: GFIs < 10
            scorecard_score=4.0,  # Bottleneck 8: Scorecard < 8.0
            active_nonbot_commits_180d=10,
        )
        # Create PRs triggering:
        # Bottleneck 1: TTFHR > 24h
        # Bottleneck 2: Ghost Rate > 10%
        # Bottleneck 3: Black Hole > 50% (60% ghost rate)
        # Bottleneck 7: First-timer MR < 80% (0% merged)
        prs: list[RawPREvent] = []
        # 6 ghost PRs (> 14 days old, unclosed, 0 comments)
        for i in range(6):
            prs.append(
                RawPREvent(
                    repo_owner="struggling",
                    repo_name="friction_hub",
                    pr_number=i + 1,
                    created_at=now - timedelta(days=20),
                    author_login=f"ghost_user_{i}",
                    author_association=AuthorAssociation.NONE,
                    is_external=True,
                    maintainer_review_comments=0,
                    closed_at=None,
                    response_latency_hours=48.0,  # > 24h
                )
            )
        # 4 closed unmerged first-timer PRs
        for i in range(4):
            prs.append(
                RawPREvent(
                    repo_owner="struggling",
                    repo_name="friction_hub",
                    pr_number=i + 10,
                    created_at=now - timedelta(days=5),
                    author_login=f"closed_user_{i}",
                    author_association=AuthorAssociation.NONE,
                    author_prior_merged_commits=0,
                    is_external=True,
                    maintainer_review_comments=2,
                    closed_at=now - timedelta(days=2),
                    merged_at=None,
                    response_latency_hours=36.0,
                )
            )

        pb = generate_maintainer_playbook(meta, prs, now=now)
        # Exactly 8 actions
        assert len(pb["actions"]) == 8
        priorities = [a["priority"] for a in pb["actions"]]
        assert priorities == list(range(1, 9))

        # Deltas are positive
        assert pb["total_point_gain"] > 0.0
        assert pb["potential_cvi"] <= 100.0

        # Verify sorted descending by delta_cvi
        deltas = [a["delta_cvi"] for a in pb["actions"]]
        assert deltas == sorted(deltas, reverse=True)


class TestStarScoutAntiGamingAdversarial:
    """Stress test StarScout anti-gaming algorithm against burst rings and sybil accounts."""

    def test_burst_ring_and_throwaway_scenarios(self) -> None:
        now = datetime(2026, 9, 11, 12, 0, 0)
        # 600 stargazers in trailing 90 days
        # 100 throwaways (< 5 events), 500 valid
        events: list[StargazerEvent] = []
        for i in range(100):
            events.append(
                StargazerEvent(
                    user_login=f"bot_{i}",
                    starred_at=now - timedelta(days=10),
                    user_total_platform_events=2,
                )
            )
        for i in range(500):
            events.append(
                StargazerEvent(
                    user_login=f"real_{i}",
                    starred_at=now - timedelta(days=10),
                    user_total_platform_events=20,
                )
            )

        # Low forks delta -> burst penalty triggers
        # Total recent stars = 600 (> 500)
        # Valid stars before penalty = 500
        # Fork ratio = 500 / 1 = 500 > 100
        # Valid stars after lockstep penalty = int(500 * 0.30) = 150
        audited = filter_fraudulent_star_velocity(events, repo_forks_delta_90d=1, now=now)
        assert audited == 150

        # Normal forks delta (e.g. 50 forks) -> ratio = 500 / 50 = 10 <= 100 -> no penalty
        audited_normal = filter_fraudulent_star_velocity(events, repo_forks_delta_90d=50, now=now)
        assert audited_normal == 500


class TestSurvivalCurveKaplanMeierAdversarial:
    """Stress test Kaplan-Meier empirical survival curve with ties, censoring, and 0 events."""

    def test_empty_and_tied_survival_curve(self) -> None:
        now = datetime(2026, 9, 11, 12, 0, 0)
        # Empty events
        curve = _survival_curve([], now)
        assert curve == [[0.0, 1.0]]

        # 50 PRs all resolved at the exact same hour (ties handling)
        prs = [
            RawPREvent(
                repo_owner="test",
                repo_name="repo",
                pr_number=i,
                created_at=now - timedelta(days=5),
                author_login=f"user_{i}",
                author_association=AuthorAssociation.NONE,
                is_external=True,
                closed_at=now - timedelta(days=3),
            )
            for i in range(50)
        ]
        curve_tied = _survival_curve(prs, now)
        assert len(curve_tied) >= 2
        assert curve_tied[0] == [0.0, 1.0]
        # At day 2.0 all 50 fail, survival drops to 0.0
        assert curve_tied[-1][0] == 2.0
        assert curve_tied[-1][1] == 0.0

        # Thinning down to <= 20 points
        many_prs = [
            RawPREvent(
                repo_owner="test",
                repo_name="repo",
                pr_number=i,
                created_at=now - timedelta(days=100),
                author_login=f"user_{i}",
                author_association=AuthorAssociation.NONE,
                is_external=True,
                closed_at=now - timedelta(days=100 - (i + 1)),
            )
            for i in range(80)
        ]
        curve_thinned = _survival_curve(many_prs, now)
        assert len(curve_thinned) <= 20
        assert curve_thinned[0] == [0.0, 1.0]


class TestPaginationMonotonicityUnderAllPersonas:
    """Empirical verification of rank sequence and monotonic score degradation under all 5 personas."""

    @pytest.mark.parametrize("persona", list(Persona))
    def test_persona_leaderboard_monotonicity(self, persona: Persona) -> None:
        from cvi.benchmark import generate_benchmark_catalog

        store = OperationalStore(":memory:")
        catalog = generate_benchmark_catalog()
        store.seed_benchmark_catalog(catalog)

        # Iterate through pages 0..1000 with limit 100
        all_items = []
        page_size = 100
        total_fetched = 0
        for offset in range(0, 1000, page_size):
            items, total = store.leaderboard(persona=persona, limit=page_size, offset=offset)
            assert total == 1000
            assert len(items) == page_size
            all_items.extend(items)
            total_fetched += len(items)

        assert total_fetched == 1000

        # Verify strict score monotonicity: item[i].overall_cvi >= item[i+1].overall_cvi
        for i in range(len(all_items) - 1):
            curr_cvi = all_items[i]["overall_cvi"]
            next_cvi = all_items[i + 1]["overall_cvi"]
            assert curr_cvi >= next_cvi, (
                f"Monotonicity violation under {persona.value}: "
                f"Rank #{i + 1} ({curr_cvi}) < Rank #{i + 2} ({next_cvi})"
            )

        store.close()


class TestConcurrentPersonaReprojectionStress:
    """Execute concurrent threads issuing simultaneous persona re-projection and deep pagination."""

    def test_concurrent_persona_deep_offsets(self) -> None:
        import concurrent.futures
        import tempfile
        from cvi.benchmark import generate_benchmark_catalog

        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            init_store = OperationalStore(tmp.name)
            catalog = generate_benchmark_catalog()
            init_store.seed_benchmark_catalog(catalog)
            init_store.close()

            personas = list(Persona)
            offsets = [0, 50, 100, 500, 900, 950, 990, 1000, 2000]
            searches = [None, "react", "linux", "vllm", "100%", "core", "nonexistent"]

            errors: list[str] = []

            def worker(thread_id: int) -> int:
                count = 0
                thread_store = OperationalStore(tmp.name)
                try:
                    for step in range(25):
                        p = personas[(thread_id + step) % len(personas)]
                        off = offsets[(thread_id * 2 + step) % len(offsets)]
                        q = searches[(thread_id + step) % len(searches)]
                        items, total = thread_store.leaderboard(
                            persona=p,
                            offset=off,
                            limit=25,
                            search=q,
                        )
                        assert isinstance(total, int)
                        assert isinstance(items, list)
                        assert len(items) <= 25
                        count += 1
                except Exception as exc:
                    errors.append(f"Thread {thread_id} step {step} error: {exc}")
                finally:
                    thread_store.close()
                return count

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(worker, i) for i in range(8)]
                results = [f.result() for f in concurrent.futures.as_completed(futures)]

            assert not errors, f"Concurrent query errors encountered: {errors}"
            assert sum(results) == 200
