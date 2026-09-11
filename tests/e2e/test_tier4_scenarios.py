"""Tier 4: Real-World Application Scenarios E2E Tests (Landmark Repos Ground Truth).

Verifies end-to-end behavior for authoritative open-source benchmark landmark projects:
1. vllm-project/vllm (PyPI Sweet Spot benchmark: high leverage, low friction, Tier S/S+, DCO, sub-12h TTFHR)
2. torvalds/linux (Systems Crucible benchmark: off-platform LKML mailing-list routing, not disqualified)
3. keras-team/keras-hub (PyPI Graveyard benchmark: Stage 0 Black Hole, 58.4% ghost rate, 0.0 CVI, Tier D)
4. facebook/react (npm ecosystem anchor: Tier S+, massive scale, 200k+ stars, high dependents)
5. kubernetes/kubernetes (Go foundational system: CNCF Foundation CLA, multi-vendor EF >= 4, Crucible)
6. gentoo/gentoo (Systems AI policy posture ban: Tier A under BALANCED, Stage 0 disqualified under AI_ASSISTED)
"""

from __future__ import annotations

from fastapi.testclient import TestClient


# =========================================================================== #
# Landmark 1: vllm-project/vllm (Sweet Spot Benchmark)
# =========================================================================== #
class TestLandmarkVLLMScenario:
    """vllm-project/vllm: The Sweet Spot benchmark repository."""

    def test_vllm_sweet_spot_scorecard_and_telemetry(self, client: TestClient) -> None:
        """vllm-project/vllm exhibits high leverage, low friction, Tier S/S+, and passes Stage 0."""
        resp = client.get("/v1/cvi/repos/vllm-project/vllm")
        assert resp.status_code == 200
        data = resp.json()

        assert data["owner"] == "vllm-project"
        assert data["name"] == "vllm"
        assert data["ecosystem"] == "pypi"
        assert data["matrix_quadrant"] == "Sweet Spot"
        assert data["rank_tier"] in ("Tier S", "Tier S+")
        assert data["overall_cvi"] >= 80.0
        assert data["is_stage0_disqualified"] == 0

        # Leverage Y >= 50.0, Friction X < 50.0
        assert data["leverage_score"] >= 50.0
        assert data["friction_score"] < 50.0

        # Multi-vendor governance and DCO / Foundation legal model
        assert data["elephant_factor"] >= 3
        assert data["legal_ip_model"] in ("DCO", "FOUNDATION_CLA")

    def test_vllm_leaderboard_positioning(self, client: TestClient) -> None:
        """vllm appears in the top tier on the PyPI leaderboard."""
        resp = client.get("/v1/cvi/leaderboard", params={"ecosystem": "pypi", "limit": 20})
        assert resp.status_code == 200
        items = resp.json()["items"]
        vllm_item = next((item for item in items if item["repo_id"] == "vllm-project/vllm"), None)
        assert vllm_item is not None
        assert vllm_item["quadrant"] == "Sweet Spot"
        assert vllm_item["rank"] <= 15


# =========================================================================== #
# Landmark 2: torvalds/linux (Crucible & Off-Platform Routing)
# =========================================================================== #
class TestLandmarkLinuxScenario:
    """torvalds/linux: The Crucible & Stage 0 off-platform mailing list benchmark."""

    def test_linux_off_platform_routing_not_penalized(self, client: TestClient) -> None:
        """torvalds/linux routes through off-platform review path and is NOT disqualified."""
        resp = client.get("/v1/cvi/repos/torvalds/linux")
        assert resp.status_code == 200
        data = resp.json()

        assert data["owner"] == "torvalds"
        assert data["name"] == "linux"
        assert data["ecosystem"] == "systems"
        assert data["is_stage0_disqualified"] == 0
        assert data["matrix_quadrant"] == "Crucible"

        # High leverage foundational system
        assert data["leverage_score"] >= 50.0
        # Friction is high because of strict gatekeeping / kernel mailing list process
        assert data["friction_score"] >= 50.0
        assert data["rank_tier"] in ("Tier A", "Tier B")

    def test_linux_systems_leaderboard_presence(self, client: TestClient) -> None:
        """torvalds/linux is discoverable on the Systems ecosystem leaderboard."""
        resp = client.get("/v1/cvi/leaderboard", params={"ecosystem": "systems", "search": "linux"})
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) >= 1
        linux = next(item for item in items if item["repo_id"] == "torvalds/linux")
        assert linux["is_gated"] == 0
        assert linux["quadrant"] == "Crucible"


# =========================================================================== #
# Landmark 3: keras-team/keras-hub (Stage 0 Black Hole Benchmark)
# =========================================================================== #
class TestLandmarkKerasHubScenario:
    """keras-team/keras-hub: The Graveyard & Stage 0 Black Hole benchmark."""

    def test_keras_hub_black_hole_short_circuit(self, client: TestClient) -> None:
        """keras-team/keras-hub triggers Stage 0 Black Hole due to excessive ghost rate (>50%)."""
        resp = client.get("/v1/cvi/repos/keras-team/keras-hub")
        assert resp.status_code == 200
        data = resp.json()

        assert data["owner"] == "keras-team"
        assert data["name"] == "keras-hub"
        assert data["ecosystem"] == "pypi"
        assert data["is_stage0_disqualified"] == 1
        assert data["matrix_quadrant"] == "Graveyard"
        assert data["rank_tier"] == "Tier D"
        assert data["overall_cvi"] == 0.0
        assert data["leverage_score"] == 0.0
        assert data["friction_score"] == 100.0
        assert "ghost" in data["disqualification_reason"].lower()

    def test_keras_hub_relegation_on_leaderboard(self, client: TestClient) -> None:
        """keras-hub appears at the bottom of the PyPI leaderboard with 0.0 score."""
        resp = client.get(
            "/v1/cvi/leaderboard", params={"ecosystem": "pypi", "search": "keras-hub"}
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        hub = items[0]
        assert hub["is_gated"] == 1
        assert hub["cvi"] == 0.0
        assert hub["tier"] == "Tier D"


# =========================================================================== #
# Landmark 4: facebook/react (npm Ecosystem Anchor)
# =========================================================================== #
class TestLandmarkReactScenario:
    """facebook/react: npm ecosystem anchor with massive downstream footprint."""

    def test_react_sweet_spot_tier_s_plus(self, client: TestClient) -> None:
        """facebook/react achieves Tier S+ in the Sweet Spot with massive downloads and dependents."""
        resp = client.get("/v1/cvi/repos/facebook/react")
        assert resp.status_code == 200
        data = resp.json()

        assert data["owner"] == "facebook"
        assert data["name"] == "react"
        assert data["ecosystem"] == "npm"
        assert data["matrix_quadrant"] == "Sweet Spot"
        assert data["rank_tier"] == "Tier S+"
        assert data["overall_cvi"] >= 90.0
        assert data["is_stage0_disqualified"] == 0

        # Massive scale metrics
        assert data["stars"] >= 200000
        assert data["monthly_downloads"] >= 50000000
        assert data["dependents_count"] >= 100000

    def test_react_leads_npm_leaderboard(self, client: TestClient) -> None:
        """facebook/react is among the top 5 repos on the npm leaderboard."""
        resp = client.get("/v1/cvi/leaderboard", params={"ecosystem": "npm", "limit": 10})
        assert resp.status_code == 200
        items = resp.json()["items"]
        react_item = next((item for item in items if item["repo_id"] == "facebook/react"), None)
        assert react_item is not None
        assert react_item["rank"] <= 5
        assert react_item["tier"] == "Tier S+"


# =========================================================================== #
# Landmark 5: kubernetes/kubernetes (Go Foundational System)
# =========================================================================== #
class TestLandmarkKubernetesScenario:
    """kubernetes/kubernetes: Go ecosystem foundation with multi-vendor CNCF governance."""

    def test_kubernetes_crucible_and_multi_vendor_governance(self, client: TestClient) -> None:
        """kubernetes/kubernetes is a high-leverage Crucible project with Elephant Factor >= 4."""
        resp = client.get("/v1/cvi/repos/kubernetes/kubernetes")
        assert resp.status_code == 200
        data = resp.json()

        assert data["owner"] == "kubernetes"
        assert data["name"] == "kubernetes"
        assert data["ecosystem"] == "go"
        assert data["matrix_quadrant"] == "Crucible"
        assert data["is_stage0_disqualified"] == 0

        # Multi-vendor foundation governance
        assert data["elephant_factor"] >= 4
        assert data["legal_ip_model"] == "FOUNDATION_CLA"
        assert data["leverage_score"] >= 50.0

    def test_kubernetes_on_go_leaderboard(self, client: TestClient) -> None:
        """kubernetes is ranked in the top tier of Go projects."""
        resp = client.get("/v1/cvi/leaderboard", params={"ecosystem": "go", "search": "kubernetes"})
        assert resp.status_code == 200
        items = resp.json()["items"]
        k8s = next(item for item in items if item["repo_id"] == "kubernetes/kubernetes")
        assert k8s["is_gated"] == 0
        assert k8s["quadrant"] == "Crucible"


# =========================================================================== #
# Landmark 6: gentoo/gentoo (Systems AI Policy Posture Ban)
# =========================================================================== #
class TestLandmarkGentooScenario:
    """gentoo/gentoo: Strict proscribed AI policy posture benchmark."""

    def test_gentoo_passes_balanced_persona(self, client: TestClient) -> None:
        """gentoo/gentoo passes under Persona.BALANCED as Tier A or Tier S."""
        resp = client.get(
            "/v1/cvi/leaderboard",
            params={"ecosystem": "systems", "search": "gentoo", "persona": "balanced"},
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        gentoo = next(item for item in items if item["repo_id"] == "gentoo/gentoo")
        assert gentoo["is_gated"] == 0
        assert gentoo["cvi"] >= 70.0
        assert gentoo["tier"] in ("Tier A", "Tier S")

    def test_gentoo_disqualified_under_ai_assisted_persona(self, client: TestClient) -> None:
        """gentoo/gentoo is disqualified under Persona.AI_ASSISTED due to its hard AI contribution ban."""
        resp = client.get(
            "/v1/cvi/leaderboard",
            params={"ecosystem": "systems", "search": "gentoo", "persona": "ai_assisted"},
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        gentoo = next(item for item in items if item["repo_id"] == "gentoo/gentoo")
        assert gentoo["is_gated"] == 1
        assert gentoo["cvi"] == 0.0
        assert gentoo["tier"] == "Tier D"
