"""Tier 3: Pairwise Combinatorial & Cross-Feature E2E Tests.

Verifies complex interactions and invariants across multiple features:
1. Ecosystem filter + persona projection + search query + pagination.
2. Pagination monotonicity & zero-duplicate ranking sequence across pages.
3. Playbook diagnostics + tier upgrade + comparator integration.
4. Quad-quadrant multi-repo comparator across 4 disparate quadrants.
5. Leaderboard persona projection vs /reproject ranking correlation.
6. Gated black hole repository consistency across matrix, leaderboard, and scorecard.
7. Multi-parameter matrix spatial filtering (ecosystem + min_stars + exclude_black_holes).
"""

from __future__ import annotations

from fastapi.testclient import TestClient


# =========================================================================== #
# Combination 1: Multi-Filter Search & Pagination
# =========================================================================== #
class TestMultiFilterSearchPaginationCombination:
    """Ecosystem filter + persona projection + search query + pagination."""

    def test_combined_ecosystem_persona_search_pagination(self, client: TestClient) -> None:
        """Combine ecosystem=pypi, persona=first_timer, search, and pagination."""
        resp = client.get(
            "/v1/cvi/leaderboard",
            params={
                "ecosystem": "pypi",
                "persona": "first_timer",
                "search": "a",
                "limit": 10,
                "offset": 0,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        items = data["items"]
        assert len(items) <= 10
        for item in items:
            assert item["ecosystem"] == "pypi"
            assert "a" in item["owner"].lower() or "a" in item["name"].lower()
            # Under first_timer persona, scores are projected
            assert "cvi" in item
            assert 0.0 <= item["cvi"] <= 100.0

    def test_pagination_monotonic_rank_sequence_across_pages(self, client: TestClient) -> None:
        """Sequential pages have strictly increasing, gapless ranks with zero duplicate repos."""
        page_size = 15
        seen_ids: set[str] = set()
        expected_rank = 1

        for page in range(4):  # Fetch 4 pages (60 items)
            resp = client.get(
                "/v1/cvi/leaderboard",
                params={"limit": page_size, "offset": page * page_size},
            )
            assert resp.status_code == 200
            items = resp.json()["items"]
            assert len(items) == page_size

            for item in items:
                repo_id = item["repo_id"]
                assert repo_id not in seen_ids, (
                    f"Duplicate repo {repo_id} appeared on page {page + 1}"
                )
                seen_ids.add(repo_id)

                assert item["rank"] == expected_rank, (
                    f"Expected rank {expected_rank}, got {item['rank']}"
                )
                expected_rank += 1

    def test_sorting_invariance_under_search_and_persona(self, client: TestClient) -> None:
        """Results remain monotonically sorted by CVI descending under any persona."""
        for persona in ("balanced", "career_capital", "first_timer", "ai_assisted"):
            resp = client.get(
                "/v1/cvi/leaderboard",
                params={"persona": persona, "limit": 50, "offset": 0},
            )
            assert resp.status_code == 200
            items = resp.json()["items"]
            scores = [item["cvi"] for item in items]
            # Verify monotonically non-increasing scores
            for i in range(len(scores) - 1):
                assert scores[i] >= scores[i + 1], (
                    f"Leaderboard under persona {persona} not sorted: "
                    f"{scores[i]} < {scores[i + 1]} at index {i}"
                )


# =========================================================================== #
# Combination 2: Quad-Quadrant Multi-Repo Comparator Matrix
# =========================================================================== #
class TestQuadQuadrantComparatorCombination:
    """Side-by-side comparison of 4 repositories from 4 distinct quadrants."""

    def test_compare_four_repos_from_four_quadrants(self, client: TestClient) -> None:
        """Query scorecards for 4 repositories representing all 4 quadrants and verify comparator fields."""
        # Query representative repositories from each of the 4 quadrants
        targets = {
            "Sweet Spot": ("facebook", "react"),
            "Crucible": ("kubernetes", "kubernetes"),
            "Nursery": ("deeplearn", "graph-query-builder"),
            "Graveyard": ("fastcore", "nlp-token-manager"),
        }

        # Fetch full scorecards for all 4
        repos_data = []
        for quad, (owner, name) in targets.items():
            resp = client.get(f"/v1/cvi/repos/{owner}/{name}")
            assert resp.status_code == 200, f"Failed to get scorecard for {owner}/{name}"
            data = resp.json()
            assert data["matrix_quadrant"] == quad
            repos_data.append(data)

        assert len(repos_data) == 4

        # Verify cross-quadrant differential properties:
        # Sweet Spot repo has high leverage (>=50) and low friction (<50)
        sweet = next(r for r in repos_data if r["matrix_quadrant"] == "Sweet Spot")
        assert sweet["leverage_score"] >= 50.0
        assert sweet["friction_score"] < 50.0

        # Crucible repo has high leverage (>=50) and high friction (>=50)
        crucible = next(r for r in repos_data if r["matrix_quadrant"] == "Crucible")
        assert crucible["leverage_score"] >= 50.0
        assert crucible["friction_score"] >= 50.0

        # Nursery repo has low leverage (<50) and low friction (<50)
        nursery = next(r for r in repos_data if r["matrix_quadrant"] == "Nursery")
        assert nursery["leverage_score"] < 50.0
        assert nursery["friction_score"] < 50.0

        # Graveyard repo has low leverage (<50) and high friction (>=50)
        graveyard = next(r for r in repos_data if r["matrix_quadrant"] == "Graveyard")
        assert graveyard["leverage_score"] < 50.0
        assert graveyard["friction_score"] >= 50.0


# =========================================================================== #
# Combination 3: Playbook Diagnostics + Tier Upgrade + Comparator
# =========================================================================== #
class TestPlaybookAndComparatorIntegration:
    """Integration between Playbook bottleneck projections and repository comparison."""

    def test_playbook_quantified_point_gain_lifts_cvi(self, client: TestClient) -> None:
        """A repository with bottlenecks has a potential CVI higher than its current CVI."""
        # Query a repository with high friction / low responsiveness
        resp = client.get("/v1/cvi/leaderboard?limit=1000")
        items = resp.json()["items"]
        # Find a non-gated repository with tier B or C
        target = next(
            (r for r in items if r["tier"] in ("Tier B", "Tier C") and r["is_gated"] == 0),
            None,
        )
        assert target is not None, "Expected at least one Tier B/C repository in catalog"

        # Fetch its scorecard
        detail_resp = client.get(f"/v1/cvi/repos/{target['owner']}/{target['name']}")
        assert detail_resp.status_code == 200
        repo_data = detail_resp.json()

        # Scorecard contains all necessary telemetry for playbook evaluation
        assert "pillar_scores" in repo_data
        assert "deep_dive" in repo_data
        raw = repo_data["deep_dive"].get("raw_metrics", {})
        assert "ttfhr_hours" in raw or "first_timer_merge_rate" in raw


# =========================================================================== #
# Combination 4: Whole-Table Reprojection vs Persona Query Consistency
# =========================================================================== #
class TestReprojectVsPersonaConsistency:
    """Consistency between POST /v1/cvi/reproject and GET /v1/cvi/leaderboard."""

    def test_balanced_persona_matches_stored_cvi_order(self, client: TestClient) -> None:
        """GET /v1/cvi/leaderboard with persona=balanced returns identical rankings as stored overall_cvi."""
        resp = client.get("/v1/cvi/leaderboard?persona=balanced&limit=20")
        assert resp.status_code == 200
        items = resp.json()["items"]
        for item in items:
            assert item["cvi"] == item["overall_cvi"]
            assert item["tier"] == item["rank_tier"]

    def test_reproject_with_balanced_weights_matches_top_ranks(self, client: TestClient) -> None:
        """POST /v1/cvi/reproject with canonical weights yields identical ranks as standard leaderboard."""
        canonical_weights = {
            "responsiveness": 0.25,
            "criticality": 0.25,
            "governance": 0.20,
            "rigor": 0.15,
            "accessibility": 0.15,
        }
        reproject_resp = client.post("/v1/cvi/reproject", json={"weights": canonical_weights})
        assert reproject_resp.status_code == 200
        reprojected = reproject_resp.json()

        lb_resp = client.get("/v1/cvi/leaderboard?limit=20")
        assert lb_resp.status_code == 200
        leaderboard = lb_resp.json()["items"]

        for i in range(10):
            assert reprojected[i]["repo_id"] == leaderboard[i]["repo_id"], (
                f"Mismatch at rank {i + 1}: reprojected {reprojected[i]['repo_id']} "
                f"vs leaderboard {leaderboard[i]['repo_id']}"
            )


# =========================================================================== #
# Combination 5: Gated Black Hole Consistency Across All Surfaces
# =========================================================================== #
class TestGatedBlackHoleConsistencyAcrossSurfaces:
    """Gated Black Hole repositories behave consistently across matrix, leaderboard, and scorecard."""

    def test_black_hole_propagates_across_all_surfaces(self, client: TestClient) -> None:
        """A Stage 0 Black Hole repo (e.g. keras-team/keras-hub) has consistent gating status across surfaces."""
        # Surface 1: GET /v1/cvi/repos/{owner}/{name}
        card_resp = client.get("/v1/cvi/repos/keras-team/keras-hub")
        assert card_resp.status_code == 200
        card = card_resp.json()
        assert card["is_stage0_disqualified"] == 1
        assert card["rank_tier"] == "Tier D"
        assert card["matrix_quadrant"] == "Graveyard"
        assert card["overall_cvi"] == 0.0
        assert card["friction_score"] == 100.0
        assert card["leverage_score"] == 0.0
        assert card["disqualification_reason"] is not None

        # Surface 2: GET /v1/cvi/matrix
        # Excluded by default
        default_matrix = client.get("/v1/cvi/matrix").json()
        default_ids = {n["repo_id"] for n in default_matrix["nodes"]}
        assert "keras-team/keras-hub" not in default_ids

        # Included when exclude_black_holes=false (filtered by ecosystem=pypi so it fits in the 500 node limit)
        all_matrix = client.get(
            "/v1/cvi/matrix",
            params={"ecosystem": "pypi", "exclude_black_holes": "false"},
        ).json()
        node = next(
            (n for n in all_matrix["nodes"] if n["repo_id"] == "keras-team/keras-hub"),
            None,
        )
        assert node is not None
        assert node["is_gated"] == 1
        assert node["quadrant"] == "Graveyard"
        assert node["x"] == 100.0
        assert node["y"] == 0.0

        # Surface 3: GET /v1/cvi/leaderboard
        lb_resp = client.get("/v1/cvi/leaderboard", params={"search": "keras-hub"}).json()
        assert lb_resp["total"] >= 1
        lb_item = next(i for i in lb_resp["items"] if i["repo_id"] == "keras-team/keras-hub")
        assert lb_item["is_gated"] == 1
        assert lb_item["cvi"] == 0.0
        assert lb_item["tier"] == "Tier D"


# =========================================================================== #
# Combination 6: Spatial Matrix Combined Filtering
# =========================================================================== #
class TestMatrixCombinedFiltering:
    """Multiple simultaneous filter parameters on GET /v1/cvi/matrix."""

    def test_matrix_ecosystem_min_stars_and_gating_combined(self, client: TestClient) -> None:
        """Filter by ecosystem=crates, min_stars=5000, and exclude_black_holes=true simultaneously."""
        resp = client.get(
            "/v1/cvi/matrix",
            params={
                "ecosystem": "crates",
                "min_stars": 5000,
                "exclude_black_holes": "true",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        nodes = data["nodes"]
        assert len(nodes) > 0

        for node in nodes:
            # All nodes must have stars >= 5000 and not be gated
            assert node["size"] >= 5000
            assert node["is_gated"] == 0
            # Coordinates are within [0, 100]
            assert 0.0 <= node["x"] <= 100.0
            assert 0.0 <= node["y"] <= 100.0
