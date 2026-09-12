"""Tests for the CVI web front-end demo (web/index.html + scripts/demo_app.py).

The demo app (scripts/demo_app.py) seeds 26 deterministic fixture repos
into an OperationalStore; here it runs on an in-memory store (the default
``:memory:`` path) — no dependency on the demo's file-backed DB. The app
is built with ``cvi.api.create_app`` plus the ``web/`` directory mounted
at ``/`` exactly like ``demo_app.create_demo_app`` does.

Oracle style: exact expected values everywhere; a test that is easy to
pass is a defect.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import re

import pytest
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from cvi.api import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = REPO_ROOT / "web"
DEMO_APP_PATH = REPO_ROOT / "scripts" / "demo_app.py"

#: Total fixture repos seeded by the demo app.
EXPECTED_REPO_COUNT = 26

#: Quadrant totals over the whole table (no min_stars/gating applied —
#: store contract for count_by_quadrant).
EXPECTED_QUADRANT_COUNTS = {
    "Sweet Spot": 10,
    "Crucible": 4,
    "Nursery": 6,
    "Graveyard": 6,  # includes the gated black-hole row (leverage 0 / friction 100)
}

#: Repos with stars < 100, dropped by the API default min_stars=100.
SMALL_REPOS = {"seed/tiny-hook", "stale/stale-tool", "seed/seed-utils", "build/hello-build"}


def _load_demo_module():
    """Import scripts/demo_app.py by path (the scripts/ dir has no __init__)."""
    spec = importlib.util.spec_from_file_location("cvi_demo_app", DEMO_APP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def demo():
    return _load_demo_module()


@pytest.fixture()
def client(demo) -> TestClient:
    """API app + web/ mounted at /, around an in-memory fixture store.

    The store is built on starlette's ASGI portal thread (via
    ``client.portal.call``) because sqlite3 connections are thread-bound
    and TestClient dispatches requests onto that portal thread.
    """
    app = create_app()
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    client = TestClient(app)
    client.__enter__()
    app.state.store = client.portal.call(demo.build_fixture_store)  # in-memory
    try:
        yield client
    finally:
        client.__exit__(None, None, None)


# --------------------------------------------------------------------------- #
# GET / — the offline single-page app
# --------------------------------------------------------------------------- #
class TestIndexServed:
    def test_get_root_returns_app_html(self, client) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        html = resp.text
        assert "<!doctype html>" in html
        # The page drives all four API surfaces.
        for marker in (
            "CVI",
            'id="ecosystem"',
            'id="min_stars"',
            'id="exclude_black_holes"',
            "/v1/cvi/matrix",
            "/v1/cvi/repos/",
            "/v1/cvi/reproject",
            "/healthz",
        ):
            assert marker in html, f"missing {marker!r} in index.html"

    def test_page_documents_the_min_stars_default_difference(self, client) -> None:
        # Demo page default is 0; the API default (100) must be documented
        # on the page itself.
        html = client.get("/").text
        assert 'value="0"' in html
        assert "100" in html

    def test_page_is_fully_offline(self, client) -> None:
        # No CDN, no build step: no external resource tags, no https
        # assets, and every fetch() call is a same-origin relative path.
        # (The only "http://" literal in the file is the W3C SVG namespace
        # string in the inline <script> — a constant, not a fetch.)
        html = client.get("/").text
        assert "<script src" not in html
        assert "<link" not in html
        assert "@import" not in html
        assert "https://" not in html
        assert 'fetch("http' not in html
        assert "fetch('http" not in html


# --------------------------------------------------------------------------- #
# GET /v1/cvi/matrix — demo fixtures through the API
# --------------------------------------------------------------------------- #
class TestDemoMatrix:
    def test_default_params_yield_nodes_with_size(self, client) -> None:
        body = client.get("/v1/cvi/matrix").json()
        nodes = body["nodes"]
        assert len(nodes) >= 1
        assert any(node["size"] > 0 for node in nodes)
        # Exact count: 26 fixtures - 4 with stars < 100 - 1 gated black hole.
        assert len(nodes) == EXPECTED_REPO_COUNT - len(SMALL_REPOS) - 1
        # Every node carries the documented node shape.
        for node in nodes:
            assert {
                "repo_id", "owner", "name", "x", "y", "cvi",
                "tier", "quadrant", "size", "is_gated",
            } <= set(node)
            assert 0.0 <= node["x"] <= 100.0 and 0.0 <= node["y"] <= 100.0
            assert node["is_gated"] == 0  # default excludes black holes

    def test_quadrant_counts_cover_all_four_quadrants(self, client) -> None:
        body = client.get("/v1/cvi/matrix").json()
        assert body["quadrant_counts"] == EXPECTED_QUADRANT_COUNTS

    def test_black_hole_excluded_by_default_and_included_when_kept(self, client) -> None:
        default_ids = {n["repo_id"] for n in client.get("/v1/cvi/matrix").json()["nodes"]}
        assert "ghost/ghost-maint" not in default_ids
        kept = client.get("/v1/cvi/matrix", params={"exclude_black_holes": "false"}).json()
        assert len(kept["nodes"]) == EXPECTED_REPO_COUNT - len(SMALL_REPOS)
        ghost = next(n for n in kept["nodes"] if n["repo_id"] == "ghost/ghost-maint")
        assert ghost["is_gated"] == 1
        assert ghost["x"] == 100.0 and ghost["y"] == 0.0
        assert ghost["quadrant"] == "Graveyard"

    def test_min_stars_zero_includes_small_repos(self, client) -> None:
        body = client.get(
            "/v1/cvi/matrix",
            params={"min_stars": 0, "exclude_black_holes": "false"},
        ).json()
        ids = {n["repo_id"] for n in body["nodes"]}
        assert ids >= SMALL_REPOS
        assert len(ids) == EXPECTED_REPO_COUNT

    def test_ecosystem_filter(self, client) -> None:
        nodes = client.get("/v1/cvi/matrix", params={"ecosystem": "pypi"}).json()["nodes"]
        assert {n["owner"] + "/" + n["name"] for n in nodes} == {
            "helios/atlas-core", "forge/data-mill", "forge/pipelint",
            "monolith/monolith-db", "dust/dusted-lib",
        }

    def test_all_quadrants_present_in_default_matrix(self, client) -> None:
        quadrants = {n["quadrant"] for n in client.get("/v1/cvi/matrix").json()["nodes"]}
        assert quadrants == {"Sweet Spot", "Crucible", "Nursery", "Graveyard"}


# --------------------------------------------------------------------------- #
# GET /v1/cvi/repos/{owner}/{name} — deep-dive scorecard
# --------------------------------------------------------------------------- #
class TestRepoDeepDive:
    def test_seeded_repo_200_with_pillar_scores(self, client) -> None:
        resp = client.get("/v1/cvi/repos/lattice/lattice")
        assert resp.status_code == 200
        body = resp.json()
        assert body["owner"] == "lattice" and body["name"] == "lattice"
        assert body["ecosystem"] == "go"
        assert body["stars"] == 30500
        assert body["pillar_scores"] == {
            "responsiveness": 92.0,
            "criticality": 84.0,
            "governance": 82.0,
            "rigor": 78.0,
            "accessibility": 90.0,
        }
        assert body["matrix_quadrant"] == "Sweet Spot"
        assert body["leverage_score"] == 81.8 and body["friction_score"] == 11.4
        assert body["rank_tier"] == "Tier S"

    def test_gated_repo_deep_dive_shows_reason(self, client) -> None:
        body = client.get("/v1/cvi/repos/ghost/ghost-maint").json()
        assert body["is_stage0_disqualified"] == 1
        assert "ghost rate" in body["disqualification_reason"]
        # Gate short-circuits: stored pillar columns are the documented 0.0 defaults.
        assert body["pillar_scores"] == dict.fromkeys(("responsiveness", "criticality", "governance", "rigor", "accessibility"), 0.0)

    def test_missing_repo_404(self, client) -> None:
        assert client.get("/v1/cvi/repos/nobody/nothing").status_code == 404


# --------------------------------------------------------------------------- #
# POST /v1/cvi/reproject — persona re-projection
# --------------------------------------------------------------------------- #
class TestReproject:
    #: All weight on accessibility: micro-css (92) is the unique maximum, so
    #: it must land at new rank 1; the zero-pillar gated repo sinks to the
    #: bottom of the re-ranked list.
    WEIGHTS = {
        "responsiveness": 0.0,
        "criticality": 0.0,
        "governance": 0.0,
        "rigor": 0.0,
        "accessibility": 1.0,
    }

    def test_valid_weights_return_ranked_array_with_deltas(self, client) -> None:
        resp = client.post("/v1/cvi/reproject", json={"weights": self.WEIGHTS})
        assert resp.status_code == 200
        items = resp.json()
        assert isinstance(items, list)
        assert len(items) == EXPECTED_REPO_COUNT
        for item in items:
            assert {"repo_id", "owner", "name", "old_rank", "new_rank", "delta", "new_score"} <= set(item)
            # delta is defined as old_rank - new_rank.
            assert item["delta"] == item["old_rank"] - item["new_rank"]
        assert [item["new_rank"] for item in items] == list(range(1, EXPECTED_REPO_COUNT + 1))
        top, bottom = items[0], items[-1]
        assert top["repo_id"] == "micro/micro-css"
        assert top["new_score"] == 92.0
        assert bottom["repo_id"] == "ghost/ghost-maint"
        assert bottom["new_score"] == 0.0
        # The nursery repo jumping from the bottom half to #1 has a positive delta.
        assert top["delta"] > 0

    def test_invalid_weights_422_before_store_access(self, client) -> None:
        # Sum off by more than 1e-6 -> rejected.
        resp = client.post(
            "/v1/cvi/reproject",
            json={"weights": dict(self.WEIGHTS, accessibility=0.9)},
        )
        assert resp.status_code == 422
        # Extra key -> rejected.
        resp = client.post(
            "/v1/cvi/reproject",
            json={"weights": dict(self.WEIGHTS, vibes=0.0)},
        )
        assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# Deep-dive payload verification & CLI testing
# --------------------------------------------------------------------------- #
class TestDeepDivePayloadVerification:
    def test_page_includes_spec_features(self, client) -> None:
        html = client.get("/").text
        # Persona presets from Screen D
        for preset in ("balanced", "first_timer", "career", "systems", "ai"):
            assert f'data-preset="{preset}"' in html, f"missing preset {preset}"
        # 5-pillar radar and Kaplan-Meier references
        assert "5-pillar radar chart" in html
        assert "Kaplan-Meier survival curve" in html
        # Dual-encoding glyphs from spec 5.1
        for glyph in ("♦", "⬢", "●", "✕"):
            assert glyph in html, f"missing glyph {glyph}"
        # Governance & IP elements
        assert "Elephant Factor" in html
        assert "Legal CLA" in html

    def test_seeded_repo_has_full_deep_dive_payload(self, client) -> None:
        resp = client.get("/v1/cvi/repos/helios/atlas-core")
        assert resp.status_code == 200
        body = resp.json()
        assert "deep_dive" in body
        dd = body["deep_dive"]
        assert {
            "raw_metrics", "survival_curve", "elephant_breakdown",
            "legal_ip", "good_first_issues",
        } <= set(dd)
        # Kaplan-Meier survival curve starts at [0.0, 1.0] and has multiple steps
        curve = dd["survival_curve"]
        assert len(curve) >= 2
        assert curve[0] == [0.0, 1.0]
        # Legal IP and CLA hazard
        assert dd["legal_ip"]["model"] == "FOUNDATION_CLA"
        assert dd["legal_ip"]["cla_hazard"] == "low"
        # Elephant breakdown
        assert dd["elephant_breakdown"]["elephant_factor"] == 4
        # Raw metrics
        raw = dd["raw_metrics"]
        assert raw["ttfhr_hours"] is not None and raw["ttfhr_hours"] > 0
        assert raw["first_timer_merge_rate"] is not None
        assert raw["ghost_rate_14d"] == 0.0

    def test_page_includes_elevated_ui_spec_features(self, client) -> None:
        html = client.get("/").text
        # Top Navigation Tabs
        assert 'id="nav-matrix"' in html
        assert 'id="nav-leaderboard"' in html
        assert 'id="nav-comparator"' in html
        assert 'id="nav-configurator"' in html

        # Screen B: Benchmark Leaderboard Table
        assert 'id="leaderboard-table"' in html
        assert 'id="lb-search-input"' in html
        assert 'id="lb-quadrant-filter"' in html
        assert 'id="lb-compare-btn"' in html
        assert "Benchmark Leaderboard" in html

        # Screen E: Side-by-Side Comparator
        assert 'id="comp-repo-picker"' in html
        assert 'id="comp-diff-table"' in html
        assert 'id="comp-verdict-box"' in html
        assert "Side-by-Side Repository Comparator" in html

        # Screen C: Maintainer Improvement Playbook
        assert "Maintainer Improvement Playbook" in html

        # Theme toggle (Light/Dark mode)
        assert 'id="theme-toggle-btn"' in html
        assert 'data-theme="dark"' in html
        assert 'data-theme="light"' in html

        # Omnibar & keyboard shortcuts
        assert 'id="open-omnibar-btn"' in html
        assert 'id="omnibar-modal"' in html
        assert 'id="shortcuts-modal"' in html

    def test_leaderboard_endpoint(self, client) -> None:
        resp = client.get("/v1/cvi/leaderboard")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data and "items" in data
        items = data["items"]
        assert len(items) == EXPECTED_REPO_COUNT
        assert items[0]["rank"] == 1
        assert "pillar_scores" in items[0]
        assert "cvi" in items[0]
        # Filter by ecosystem
        resp_pypi = client.get("/v1/cvi/leaderboard", params={"ecosystem": "pypi"})
        assert resp_pypi.status_code == 200
        data_pypi = resp_pypi.json()
        pypi_items = data_pypi["items"]
        assert len(pypi_items) == 7
        assert all(item["ecosystem"] == "pypi" for item in pypi_items)


class TestDemoAppCLI:
    def test_create_demo_app_default_store(self, demo) -> None:
        app = demo.create_demo_app()
        assert app is not None
        assert app.state.store is not None

    def test_main_cli_argparse(self, demo, monkeypatch) -> None:
        called = {}

        def mock_run(app, host, port):
            called["host"] = host
            called["port"] = port

        monkeypatch.setattr("uvicorn.run", mock_run)
        demo.main(["--port", "8765", "--db", ":memory:"])
        assert called["host"] == "127.0.0.1"
        assert called["port"] == 8765


# --------------------------------------------------------------------------- #
# Milestone 3: Modern Dual-Theme Engineering UI Verification
# --------------------------------------------------------------------------- #
class TestDualThemeSystem:
    """Requirement 1: Dual Theme System (Dark Obsidian & Light Canvas, LocalStorage, WCAG 2.1 AA)."""

    def test_dark_mode_tokens(self, client: TestClient) -> None:
        html = client.get("/").text
        # Dark mode obsidian canvas #090D16, #0F172A surface, #1E293B border, #38BDF8 accent, #F8FAFC text
        assert "#090d16" in html.lower()
        assert "#0f172a" in html.lower()
        assert "#1e293b" in html.lower()
        assert "#38bdf8" in html.lower()
        assert "#f8fafc" in html.lower()

    def test_light_mode_tokens(self, client: TestClient) -> None:
        html = client.get("/").text
        # Light mode #F8FAFC base, #FFFFFF surface, #E2E8F0 border, #0284C7 accent, #0F172A text
        assert 'data-theme="light"' in html
        assert "#ffffff" in html.lower()
        assert "#e2e8f0" in html.lower()
        assert "#0284c7" in html.lower()

    def test_persistent_theme_toggle(self, client: TestClient) -> None:
        html = client.get("/").text
        assert 'id="theme-toggle-btn"' in html
        assert 'id="theme-icon"' in html
        assert "setTheme(" in html
        assert "localStorage.getItem" in html
        assert "localStorage.setItem" in html

    def test_wcag_21_aa_contrast_ratios(self) -> None:
        def lum(hex_code: str) -> float:
            c = hex_code.lstrip("#")
            rgb = [int(c[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]
            adj = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
            return 0.2126 * adj[0] + 0.7152 * adj[1] + 0.0722 * adj[2]

        def contrast(c1: str, c2: str) -> float:
            l1, l2 = lum(c1), lum(c2)
            return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)

        # Dark theme text contrast > 4.5:1 (WCAG AA requirement)
        cr_dark_text = contrast("#F8FAFC", "#090D16")
        assert cr_dark_text >= 4.5
        assert cr_dark_text >= 15.0  # Actually achieves AAA

        # Light theme text contrast > 4.5:1 (WCAG AA requirement)
        cr_light_text = contrast("#0F172A", "#F8FAFC")
        assert cr_light_text >= 4.5
        assert cr_light_text >= 14.0  # Actually achieves AAA

        # UI Graphical accents contrast > 3.0:1 (WCAG AA requirement for UI components)
        cr_dark_accent = contrast("#38BDF8", "#090D16")
        assert cr_dark_accent >= 3.0

        cr_light_accent = contrast("#0284C7", "#F8FAFC")
        assert cr_light_accent >= 3.0

    def test_chart_theme_adaptation(self, client: TestClient) -> None:
        html = client.get("/").text
        assert 'state.theme === "light"' in html


class TestNavigationShell:
    """Requirement 2: Top-Level 4-Tab Navigation Shell."""

    def test_four_navigation_tabs_exist(self, client: TestClient) -> None:
        html = client.get("/").text
        assert 'id="nav-matrix"' in html
        assert 'id="nav-leaderboard"' in html
        assert 'id="nav-comparator"' in html
        assert 'id="nav-configurator"' in html

    def test_four_screen_containers_exist(self, client: TestClient) -> None:
        html = client.get("/").text
        assert 'id="screen-matrix"' in html
        assert 'id="screen-leaderboard"' in html
        assert 'id="screen-comparator"' in html
        assert 'id="screen-configurator"' in html

    def test_view_switching_without_reload(self, client: TestClient) -> None:
        html = client.get("/").text
        assert "switchScreen(" in html
        assert 'document.querySelectorAll(".nav-tab")' in html
        assert 'document.querySelectorAll(".screen-view")' in html

    def test_keyboard_shortcuts_mapped(self, client: TestClient) -> None:
        html = client.get("/").text
        assert 'id="shortcuts-modal"' in html
        assert 'switchScreen("matrix")' in html
        assert 'switchScreen("leaderboard")' in html
        assert 'switchScreen("comparator")' in html
        assert 'switchScreen("configurator")' in html


class TestRankedLeaderboard:
    """Requirement 3: Screen B Ranked Leaderboard."""

    def test_leaderboard_table_structure(self, client: TestClient) -> None:
        html = client.get("/").text
        assert 'id="leaderboard-table"' in html
        assert 'id="leaderboard-tbody"' in html
        assert 'id="lb-search-input"' in html
        assert 'id="lb-quadrant-filter"' in html

    def test_page_size_options_include_all(self, client: TestClient) -> None:
        html = client.get("/").text
        assert 'id="lb-page-size"' in html
        assert 'value="1000"' in html

    def test_ecosystem_filter_pills(self, client: TestClient) -> None:
        html = client.get("/").text
        for eco in ("", "pypi", "npm", "crates", "go", "maven", "systems"):
            assert f'data-eco="{eco}"' in html

    def test_monospace_tabular_numbers(self, client: TestClient) -> None:
        html = client.get("/").text
        assert "tabular-nums" in html
        assert ".mono-num" in html

    def test_tier_badges_and_quadrant_indicators(self, client: TestClient) -> None:
        html = client.get("/").text
        assert "QUADRANT_GLYPHS" in html
        assert "QUADRANT_COLORS" in html
        assert "mini-bar-track" in html
        assert "mini-bar-fill" in html

    def test_comparison_selection_checkboxes(self, client: TestClient) -> None:
        html = client.get("/").text
        assert "lb-row-cb" in html
        assert 'id="lb-compare-btn"' in html
        assert 'id="lb-selected-count"' in html
        assert "selectedForCompare" in html
        assert "Maximum 4 repositories" in html


class TestRepositoryComparator:
    """Requirement 4: Screen E Repository Comparator (2–4 repositories)."""

    def test_comparator_layout_elements(self, client: TestClient) -> None:
        html = client.get("/").text
        assert 'id="comp-repo-picker"' in html
        assert 'id="comp-diff-table"' in html
        assert 'id="comp-radar-container"' in html
        assert 'id="comp-verdict-box"' in html
        assert 'id="comp-chips"' in html

    def test_superimposed_radar_chart(self, client: TestClient) -> None:
        html = client.get("/").text
        assert "renderMultiRadarSvg" in html
        assert "COMP_COLORS" in html
        for color in ("#10b981", "#6366f1", "#f59e0b", "#f43f5e"):
            assert color in html.lower()

    def test_differential_table_15_parameters_grouped(self, client: TestClient) -> None:
        html = client.get("/").text
        # Grouped under the 5 canonical pillars
        assert "PILLAR 1: MAINTAINER RESPONSIVENESS" in html
        assert "PILLAR 2: PRODUCTION IMPACT & FOOTPRINT" in html
        assert "PILLAR 3: GOVERNANCE & IP" in html
        assert "PILLAR 4: ENGINEERING RIGOR & AI" in html
        assert "PILLAR 5: ACCESSIBILITY & ON-RAMPS" in html

        # Differential parameters
        for param in (
            "Overall CVI Score",
            "Global Rank",
            "Benchmark Tier",
            "Leverage Score (Y-axis)",
            "Friction Score (X-axis)",
            "Responsiveness Score (P1)",
            "Median TTFHR",
            "First-Timer Merge Rate",
            "14-Day PR Ghost Rate",
            "Review Tail Latency",
            "Criticality Score (P2)",
            "GitHub Stars",
            "Monthly Downloads",
            "Registry Dependents",
            "Governance Score (P3)",
            "Elephant Factor",
            "Legal CLA Regime",
            "Engineering Rigor Score (P4)",
            "AI Contribution Posture",
            "Accessibility Score (P5)",
            "Good First Issues",
        ):
            assert param in html, f"Missing differential parameter {param}"

    def test_winning_badges_logic(self, client: TestClient) -> None:
        html = client.get("/").text
        assert "diff-winner" in html
        assert "👑" in html
        assert "higherBetter" in html

    def test_synthesized_recommendation_verdict(self, client: TestClient) -> None:
        html = client.get("/").text
        assert "renderComparatorVerdict" in html
        assert "Highest Composite Benchmark" in html
        assert "For First-Time Contributors" in html
        assert "For Career Capital & Resume Proof" in html
        assert "For Governance & Legal Safety" in html

    def test_comparator_minimum_selection_prompt(self, client: TestClient) -> None:
        html = client.get("/").text
        assert "Please select at least 2 repositories to compare" in html


class TestScorecardDrawerPlaybook:
    """Requirement 5: Scorecard Drawer Maintainer Improvement Playbook UI (Screen C)."""

    def test_slide_over_drawer_structure(self, client: TestClient) -> None:
        html = client.get("/").text
        assert 'id="drawer"' in html
        assert 'id="drawer-backdrop"' in html
        assert "openDeepDive" in html
        assert "closeDrawer" in html

    def test_projected_elevation_banner(self, client: TestClient) -> None:
        html = client.get("/").text
        assert "playbook-banner" in html
        assert "Projected Elevation:" in html
        assert "➔" in html

    def test_diagnostic_friction_bottleneck_cards(self, client: TestClient) -> None:
        html = client.get("/").text
        assert "playbook-container" in html
        assert "playbook-action" in html
        assert "playbook-action-header" in html
        assert "playbook-action-title" in html
        assert "playbook-action-badge" in html
        assert "playbook-target" in html

    def test_playbook_diagnostic_rules(self, client: TestClient) -> None:
        html = client.get("/").text
        assert "Reduce TTFHR" in html
        assert "Eliminate PR Abandonment" in html
        assert "Diversify maintainers: current Elephant Factor is" in html
        assert "Tag 3-5 beginner-friendly Good First Issues" in html
        assert "Improve branch protection and token permissions" in html
        assert "Maintain Benchmark Leadership" in html


class TestMatrixCanvasSVG:
    """Requirement 6: Screen A 2D Matrix SVG."""

    def test_matrix_canvas_and_bisectors(self, client: TestClient) -> None:
        html = client.get("/").text
        assert 'id="scatter"' in html
        assert "renderScatter" in html
        assert "xPx(50)" in html
        assert "yPx(50)" in html

    def test_matrix_quadrant_labels(self, client: TestClient) -> None:
        html = client.get("/").text
        for quad in ("Sweet Spot", "Crucible", "Nursery", "Graveyard"):
            assert quad in html
        assert "cell.name.toUpperCase()" in html

    def test_matrix_node_interactions(self, client: TestClient) -> None:
        html = client.get("/").text
        assert 'id="matrix-hover-card"' in html
        assert "showHoverCard" in html
        assert "hideHoverCard" in html
        assert "openDeepDive(node)" in html


class TestZeroCDNAssurance:
    """Requirement 7: Zero-CDN Architecture (100% self-contained)."""

    def test_no_external_scripts(self, client: TestClient) -> None:
        html = client.get("/").text
        for src in re.findall(r'<script\s+[^>]*src=["\']([^"\']+)["\']', html, re.I):
            assert not src.startswith("http://") and not src.startswith("https://")

    def test_no_external_stylesheets(self, client: TestClient) -> None:
        html = client.get("/").text
        for href in re.findall(r'<link\s+[^>]*href=["\']([^"\']+)["\']', html, re.I):
            assert not href.startswith("http://") and not href.startswith("https://")

    def test_no_external_fonts(self, client: TestClient) -> None:
        html = client.get("/").text
        assert "@import" not in html
        assert "fonts.googleapis.com" not in html
        assert "fonts.gstatic.com" not in html


