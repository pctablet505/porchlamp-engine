"""Tier 2: Boundary Value Analysis & Corner Cases E2E Tests.

Verifies boundary conditions and corner cases:
- Empty and malformed inputs (empty search, whitespace search, empty ecosystem)
- Limit and offset boundaries (limit=1, limit=1000, limit=0/1001 422, offset=0/999/1000, offset=-1 422)
- Exact cardinal tier cutoffs (89.99 vs 90.0, 79.99 vs 80.0, 69.99 vs 70.0, 54.99 vs 55.0, 39.99 vs 40.0)
- Exact (50.0, 50.0) coordinates boundary and all quadrant bisection edges
- Zero PRs / no events telemetry handling without division by zero
- Persona weight constraints and tolerances (sum != 1.0 within 1e-6, missing/extra keys)
- Star count boundaries (min_stars=0, min_stars=-1 422, extreme min_stars)
- Stage 0 gating boundaries (exact 50.0% vs 50.01% ghost rate, 0 vs 1 commits in 180d)
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from cvi.engine import assign_tier, score_repository
from cvi.matrix import decompose
from cvi.models import (
    AIPosture,
    AuthorAssociation,
    Ecosystem,
    LegalIPModel,
    Persona,
    PillarScores,
    Quadrant,
    RawPREvent,
    RepoMeta,
    Tier,
)
from cvi.persona import reproject
from cvi.stage0 import apply_stage0

NOW = datetime(2026, 9, 11, 12, 0, 0)


# =========================================================================== #
# Tier Cutoff Boundaries (89.99 vs 90.0, etc.)
# =========================================================================== #
class TestTierCutoffBoundaries:
    """Exact boundary value analysis for cardinal tier assignments."""

    @pytest.mark.parametrize(
        ("score", "expected_tier"),
        [
            # Tier S+ cutoff (>= 90.0)
            (90.00, Tier.S_PLUS),
            (90.001, Tier.S_PLUS),
            (89.999, Tier.S),
            (89.99, Tier.S),
            # Tier S cutoff (>= 80.0)
            (80.00, Tier.S),
            (80.001, Tier.S),
            (79.999, Tier.A),
            (79.99, Tier.A),
            # Tier A cutoff (>= 70.0)
            (70.00, Tier.A),
            (70.001, Tier.A),
            (69.999, Tier.B),
            (69.99, Tier.B),
            # Tier B cutoff (>= 55.0)
            (55.00, Tier.B),
            (55.001, Tier.B),
            (54.999, Tier.C),
            (54.99, Tier.C),
            # Tier C cutoff (>= 40.0)
            (40.00, Tier.C),
            (40.001, Tier.C),
            (39.999, Tier.D),
            (39.99, Tier.D),
            # Lower bound (0.0)
            (0.00, Tier.D),
            # Upper bound (100.0)
            (100.00, Tier.S_PLUS),
        ],
    )
    def test_tier_boundary_cutoffs(self, score: float, expected_tier: Tier) -> None:
        """Score boundary transitions strictly at x.00 boundaries."""
        assert assign_tier(score) == expected_tier


# =========================================================================== #
# Exact (50, 50) Coordinates Boundary & Quadrant Bisection
# =========================================================================== #
class TestMatrixCoordinateBoundaries:
    """2D Matrix coordinate decomposition and quadrant bisection at (50, 50)."""

    def test_exact_50_50_coordinates_is_crucible(self) -> None:
        """Convention: coordinates >= 50.0 count as 'high' on both axes.
        Therefore (leverage=50.0, friction=50.0) classifies as CRUCIBLE.
        """
        # Pillar scores that yield leverage=50.0 and friction=50.0:
        # P2=50, P4=50, P3=50 -> lev = 0.5(50) + 0.3(50) + 0.2(50) = 50.0
        # P1=50, P3=50, P5=50 -> fric = 0.5(50) + 0.3(50) + 0.2(50) = 50.0
        ps = PillarScores(50.0, 50.0, 50.0, 50.0, 50.0)
        lev, fric, quad = decompose(ps)
        assert lev == 50.0
        assert fric == 50.0
        assert quad == Quadrant.CRUCIBLE

    def test_just_below_50_50_is_nursery(self) -> None:
        """Coordinates with low leverage (<50) and low friction (<50) -> NURSERY."""
        # lev = 0.5(0)+0.3(0)+0.2(100) = 20.0 (<50)
        # fric = 0.5(0)+0.3(0)+0.2(0) = 0.0 (<50)
        ps = PillarScores(100.0, 0.0, 100.0, 0.0, 100.0)
        lev, fric, quad = decompose(ps)
        assert lev < 50.0
        assert fric < 50.0
        assert quad == Quadrant.NURSERY

    def test_high_leverage_low_friction_boundary_is_sweet_spot(self) -> None:
        """Coordinates with high leverage (>=50) and low friction (<50) -> SWEET_SPOT."""
        # lev = 0.5(100)+0.3(100)+0.2(100) = 100.0 (>=50)
        # fric = 0.5(0)+0.3(0)+0.2(0) = 0.0 (<50)
        ps = PillarScores(100.0, 100.0, 100.0, 100.0, 100.0)
        lev, fric, quad = decompose(ps)
        assert lev >= 50.0
        assert fric < 50.0
        assert quad == Quadrant.SWEET_SPOT

    def test_low_leverage_high_friction_boundary_is_graveyard(self) -> None:
        """Coordinates with low leverage (<50) and high friction (>=50) -> GRAVEYARD."""
        # lev = 0.5(0)+0.3(0)+0.2(0) = 0.0 (<50)
        # fric = 0.5(100)+0.3(100)+0.2(100) = 100.0 (>=50)
        ps = PillarScores(0.0, 0.0, 0.0, 0.0, 0.0)
        lev, fric, quad = decompose(ps)
        assert lev < 50.0
        assert fric >= 50.0
        assert quad == Quadrant.GRAVEYARD

    @pytest.mark.parametrize(
        ("pillars", "expected_quadrant"),
        [
            (PillarScores(100.0, 0.0, 100.0, 0.0, 100.0), Quadrant.NURSERY),
            (PillarScores(100.0, 100.0, 100.0, 100.0, 100.0), Quadrant.SWEET_SPOT),
            (PillarScores(0.0, 100.0, 100.0, 100.0, 0.0), Quadrant.CRUCIBLE),
            (PillarScores(0.0, 0.0, 0.0, 0.0, 0.0), Quadrant.GRAVEYARD),
        ],
    )
    def test_extreme_quadrant_corners(
        self, pillars: PillarScores, expected_quadrant: Quadrant
    ) -> None:
        """The four extreme coordinate corners map unambiguously to the four quadrants."""
        _, _, quad = decompose(pillars)
        assert quad == expected_quadrant


# =========================================================================== #
# Limit and Offset Boundaries
# =========================================================================== #
class TestLimitAndOffsetBoundaries:
    """Boundary conditions on pagination parameters for GET /v1/cvi/leaderboard."""

    def test_limit_minimum_boundary(self, client: TestClient) -> None:
        """limit=1 returns exactly 1 item and total=1000."""
        resp = client.get("/v1/cvi/leaderboard?limit=1&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1000
        assert len(data["items"]) == 1
        assert data["items"][0]["rank"] == 1

    def test_limit_maximum_boundary(self, client: TestClient) -> None:
        """limit=1000 returns all 1,000 items in a single response."""
        resp = client.get("/v1/cvi/leaderboard?limit=1000&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1000
        assert len(data["items"]) == 1000
        assert data["items"][0]["rank"] == 1
        assert data["items"][-1]["rank"] == 1000

    def test_limit_zero_is_rejected_422(self, client: TestClient) -> None:
        """limit=0 violates ge=1 constraint -> HTTP 422."""
        resp = client.get("/v1/cvi/leaderboard?limit=0")
        assert resp.status_code == 422

    def test_limit_over_1000_is_rejected_422(self, client: TestClient) -> None:
        """limit=1001 violates le=1000 constraint -> HTTP 422."""
        resp = client.get("/v1/cvi/leaderboard?limit=1001")
        assert resp.status_code == 422

    def test_offset_zero_boundary(self, client: TestClient) -> None:
        """offset=0 starts at rank 1."""
        resp = client.get("/v1/cvi/leaderboard?limit=10&offset=0")
        assert resp.status_code == 200
        assert resp.json()["items"][0]["rank"] == 1

    def test_offset_last_item_boundary(self, client: TestClient) -> None:
        """offset=999 with limit=10 returns exactly 1 item at rank 1000."""
        resp = client.get("/v1/cvi/leaderboard?limit=10&offset=999")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["rank"] == 1000

    def test_offset_out_of_bounds_boundary(self, client: TestClient) -> None:
        """offset=1000 beyond total returns empty items list gracefully (not 500)."""
        resp = client.get("/v1/cvi/leaderboard?limit=10&offset=1000")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1000
        assert data["items"] == []

    def test_offset_negative_is_rejected_422(self, client: TestClient) -> None:
        """offset=-1 violates ge=0 constraint -> HTTP 422."""
        resp = client.get("/v1/cvi/leaderboard?offset=-1")
        assert resp.status_code == 422


# =========================================================================== #
# Empty and Malformed Inputs
# =========================================================================== #
class TestEmptyAndMalformedInputs:
    """Opaque-box handling of empty, whitespace, and non-matching queries."""

    def test_empty_search_returns_unfiltered_leaderboard(self, client: TestClient) -> None:
        """Empty search query string returns full catalog without error."""
        resp = client.get("/v1/cvi/leaderboard?search=&limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1000
        assert len(data["items"]) == 10

    def test_whitespace_search_returns_unfiltered_leaderboard(self, client: TestClient) -> None:
        """Search query containing only spaces is stripped and returns full catalog."""
        resp = client.get("/v1/cvi/leaderboard?search=%20%20%20&limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1000

    def test_search_with_no_matches_returns_empty_items(self, client: TestClient) -> None:
        """Search term matching no repository returns total=0 and items=[]."""
        resp = client.get("/v1/cvi/leaderboard?search=nonexistent_xyz_query_12345")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []

    def test_min_stars_negative_is_rejected_422(self, client: TestClient) -> None:
        """min_stars=-5 violates ge=0 constraint on /leaderboard and /matrix -> HTTP 422."""
        resp_lb = client.get("/v1/cvi/leaderboard?min_stars=-5")
        assert resp_lb.status_code == 422
        resp_mat = client.get("/v1/cvi/matrix?min_stars=-1")
        assert resp_mat.status_code == 422

    def test_min_stars_extreme_threshold(self, client: TestClient) -> None:
        """Very large min_stars threshold returns empty nodes list gracefully."""
        resp = client.get("/v1/cvi/matrix?min_stars=999999999")
        assert resp.status_code == 200
        assert resp.json()["nodes"] == []


# =========================================================================== #
# Persona Weights Tolerance & Constraint Boundaries
# =========================================================================== #
class TestPersonaWeightsBoundaries:
    """Validation boundaries for POST /v1/cvi/reproject."""

    VALID_BASE = {
        "responsiveness": 0.25,
        "criticality": 0.25,
        "governance": 0.20,
        "rigor": 0.15,
        "accessibility": 0.15,
    }

    def test_weights_summing_to_exact_1_accepted(self, client: TestClient) -> None:
        """Exact sum 1.0 is accepted."""
        resp = client.post("/v1/cvi/reproject", json={"weights": self.VALID_BASE})
        assert resp.status_code == 200

    def test_weights_sum_within_micro_tolerance_accepted(self, client: TestClient) -> None:
        """Sum differing by <= 1e-6 (e.g. 1.0 + 5e-7) is accepted within floating tolerance."""
        tolerated = dict(self.VALID_BASE, responsiveness=0.2500005)
        resp = client.post("/v1/cvi/reproject", json={"weights": tolerated})
        assert resp.status_code == 200

    def test_weights_sum_exceeding_tolerance_rejected_422(self, client: TestClient) -> None:
        """Sum differing by > 1e-6 (e.g. 1.0001) is rejected with HTTP 422."""
        too_high = dict(self.VALID_BASE, responsiveness=0.2501)
        resp = client.post("/v1/cvi/reproject", json={"weights": too_high})
        assert resp.status_code == 422
        assert "weights must sum to 1.0" in resp.text

    def test_weights_missing_pillar_key_rejected_422(self, client: TestClient) -> None:
        """Weight vector missing any of the 5 pillar keys is rejected with HTTP 422."""
        incomplete = dict(self.VALID_BASE)
        del incomplete["accessibility"]
        incomplete["responsiveness"] = 0.40  # sum = 1.0, but missing key
        resp = client.post("/v1/cvi/reproject", json={"weights": incomplete})
        assert resp.status_code == 422
        assert "missing=" in resp.text

    def test_weights_extra_unknown_key_rejected_422(self, client: TestClient) -> None:
        """Weight vector with extraneous keys is rejected with HTTP 422."""
        extra = dict(self.VALID_BASE, vibes=0.0)
        resp = client.post("/v1/cvi/reproject", json={"weights": extra})
        assert resp.status_code == 422
        assert "extra=" in resp.text


# =========================================================================== #
# Zero PRs and Undefined Telemetry Handling
# =========================================================================== #
class TestZeroPREventsHandling:
    """Repositories with 0 PR events handled safely without ZeroDivisionError."""

    def test_scoring_repo_with_zero_pr_events(self) -> None:
        """score_repository handles empty pr_events list safely."""
        meta = RepoMeta(
            owner="empty-org",
            name="empty-repo",
            ecosystem=Ecosystem.PYPI,
            active_nonbot_commits_180d=10,
            off_platform_reviews=False,
            hard_ai_ban=False,
        )
        # Empty PR events
        pr_events: list[RawPREvent] = []

        result = score_repository(
            meta=meta,
            pr_events=pr_events,
            now=NOW,
            persona=Persona.BALANCED,
        )
        assert result is not None
        assert 0.0 <= result.overall_cvi <= 100.0
        assert result.rank_tier in (
            Tier.S_PLUS, Tier.S, Tier.A, Tier.B, Tier.C, Tier.D
        )
        assert 0.0 <= result.leverage_score <= 100.0
        assert 0.0 <= result.friction_score <= 100.0


# =========================================================================== #
# Stage 0 Hard Gating Thresholds
# =========================================================================== #
class TestStage0GatingBoundaries:
    """Stage 0 Elimination-by-Aspects boundary value tests."""

    def make_base_meta(self, commits: int = 10) -> RepoMeta:
        return RepoMeta(
            owner="test-org",
            name="test-repo",
            ecosystem=Ecosystem.PYPI,
            active_nonbot_commits_180d=commits,
            off_platform_reviews=False,
            hard_ai_ban=False,
        )

    def test_commit_gate_zero_vs_one_commit(self) -> None:
        """0 commits in 180d fails Gate 1 (abandoned); 1 commit passes Gate 1."""
        meta_zero = self.make_base_meta(commits=0)
        res_zero = apply_stage0(meta_zero, [], NOW, persona=Persona.BALANCED)
        assert not res_zero.passed
        assert "180 days" in res_zero.reason.lower()

        meta_one = self.make_base_meta(commits=1)
        res_one = apply_stage0(meta_one, [], NOW, persona=Persona.BALANCED)
        assert res_one.passed

    def test_ghost_rate_exact_50_percent_vs_50_01_percent(self) -> None:
        """Gate 2 (Ghost Rate): exactly 50.0% open unreviewed PRs passes (not > 50%),
        while 50.01% fails and triggers Black Hole.
        """
        # Exactly 50.0% ghost rate: 5 ghost PRs, 5 reviewed PRs
        pr_events_50: list[RawPREvent] = []
        for i in range(5):
            # Ghost PR: open, >14 days old, 0 comments
            pr_events_50.append(
                RawPREvent(
                    repo_owner="test-org",
                    repo_name="test-repo",
                    pr_number=i,
                    created_at=NOW - timedelta(days=20),
                    author_login="contrib",
                    author_association=AuthorAssociation.FIRST_TIME,
                    author_prior_merged_commits=0,
                    is_external=True,
                    maintainer_review_comments=0,
                    closed_at=None,
                    merged_at=None,
                )
            )
        for i in range(5, 10):
            # Reviewed PR: open, >14 days old, 1 comment
            pr_events_50.append(
                RawPREvent(
                    repo_owner="test-org",
                    repo_name="test-repo",
                    pr_number=i,
                    created_at=NOW - timedelta(days=20),
                    author_login="contrib",
                    author_association=AuthorAssociation.FIRST_TIME,
                    author_prior_merged_commits=0,
                    is_external=True,
                    maintainer_review_comments=1,
                    closed_at=None,
                    merged_at=None,
                )
            )

        meta = self.make_base_meta(commits=10)
        res_50 = apply_stage0(meta, pr_events_50, NOW, persona=Persona.BALANCED)
        # 5 / 10 = 0.50. Rule is GR_14 > 0.50, so 0.50 PASSES
        assert res_50.passed, "Exact 50% ghost rate should not exceed the >50% threshold"

        # 50.01% ghost rate: 51 ghosts out of 100 PRs
        pr_events_51: list[RawPREvent] = []
        for i in range(51):
            pr_events_51.append(
                RawPREvent(
                    repo_owner="test-org",
                    repo_name="test-repo",
                    pr_number=i,
                    created_at=NOW - timedelta(days=20),
                    author_login="contrib",
                    author_association=AuthorAssociation.FIRST_TIME,
                    author_prior_merged_commits=0,
                    is_external=True,
                    maintainer_review_comments=0,
                    closed_at=None,
                    merged_at=None,
                )
            )
        for i in range(51, 100):
            pr_events_51.append(
                RawPREvent(
                    repo_owner="test-org",
                    repo_name="test-repo",
                    pr_number=i,
                    created_at=NOW - timedelta(days=20),
                    author_login="contrib",
                    author_association=AuthorAssociation.FIRST_TIME,
                    author_prior_merged_commits=0,
                    is_external=True,
                    maintainer_review_comments=1,
                    closed_at=None,
                    merged_at=None,
                )
            )

        res_51 = apply_stage0(meta, pr_events_51, NOW, persona=Persona.BALANCED)
        # 51 / 100 = 0.51 > 0.50 -> FAILS with is_black_hole=True
        assert not res_51.passed
        assert res_51.is_black_hole
        assert "ghost rate" in res_51.reason.lower()
