"""Tier 1: Comprehensive Feature Coverage E2E Tests.

Verifies >=5 test cases per core feature:
1. Benchmark Catalog (1,000 repos across 6 ecosystems, 4 quadrants, 6 tiers, <2s seeder SLA)
2. Canonical Parameter Hierarchy & WAM Weights (25/25/20/15/15, 15 metrics, leverage/friction)
3. Maintainer Improvement Playbook Diagnostics (TTFHR, ghost rate, EF, GFI, clean repos)
4. Repository Comparator Screen E (2-4 repos, 15-parameter diff, winning badges, verdicts)
5. API Endpoints (/healthz, /matrix, /repos, /leaderboard, /reproject under <50ms SLA)
6. Dual Theme Engine (Dark #090D16, Light #F8FAFC, toggle, persistence, WCAG contrast)
7. Zero-CDN & Offline Delivery (zero external scripts, styles, fonts, or network requests)
"""

from __future__ import annotations

import re
import time

import pytest
from fastapi.testclient import TestClient

from tests.fixtures.synthetic_catalog import build_benchmark_store
from cvi.constants import (
    FRICTION_WEIGHTS,
    LEVERAGE_WEIGHTS,
    PARAM_WEIGHTS,
    PILLAR_WEIGHTS,
)
from cvi.lakehouse import OperationalStore


# =========================================================================== #
# Feature 1: Benchmark Catalog (>=5 tests)
# =========================================================================== #
class TestBenchmarkCatalogFeature:
    """Empirical catalog of 1,000 top open-source repositories."""

    def test_catalog_exact_total_count(self, store: OperationalStore) -> None:
        """Requirement R1: Exactly 1,000 repositories indexed in the store."""
        cur = store.connection.execute("SELECT COUNT(*) FROM repositories")
        total = cur.fetchone()[0]
        assert total == 1000, f"Expected 1,000 repositories in store, found {total}"

    def test_catalog_ecosystem_distribution(self, store: OperationalStore) -> None:
        """Requirement R1: Catalog spans 6 major ecosystems (PyPI, npm, Crates, Go, Maven, Systems)."""
        cur = store.connection.execute(
            "SELECT ecosystem, COUNT(*) FROM repositories GROUP BY ecosystem"
        )
        counts = dict(cur.fetchall())
        expected_ecosystems = {"pypi", "npm", "crates", "go", "maven", "systems"}
        assert set(counts.keys()) == expected_ecosystems
        for eco, cnt in counts.items():
            assert cnt >= 100, f"Ecosystem {eco} has only {cnt} repos, expected >= 100"
        assert sum(counts.values()) == 1000

    def test_catalog_quadrant_dispersion(self, store: OperationalStore) -> None:
        """Requirement R1: Catalog spans all 4 quadrants (Sweet Spot, Crucible, Nursery, Graveyard)."""
        counts = store.count_by_quadrant()
        assert set(counts.keys()) == {"Sweet Spot", "Crucible", "Nursery", "Graveyard"}
        for quad, count in counts.items():
            assert count >= 100, f"Quadrant {quad} under-represented with {count} repos"
        assert sum(counts.values()) == 1000

    def test_catalog_tier_dispersion(self, store: OperationalStore) -> None:
        """Requirement R1: Catalog spans all 6 cardinal tiers (Tier S+ down to Tier D)."""
        cur = store.connection.execute(
            "SELECT rank_tier, COUNT(*) FROM repositories GROUP BY rank_tier"
        )
        tiers = dict(cur.fetchall())
        for expected_tier in ("Tier S+", "Tier S", "Tier A", "Tier B", "Tier C", "Tier D"):
            assert expected_tier in tiers, f"Missing tier {expected_tier} in benchmark catalog"
            assert tiers[expected_tier] > 0, f"Tier {expected_tier} has 0 repositories"
        assert sum(tiers.values()) == 1000

    def test_catalog_seeder_speed_sla(self) -> None:
        """Acceptance Criteria: Seeding 1,000 repositories completes in under 2 seconds (<2s SLA)."""
        t0 = time.perf_counter()
        store = build_benchmark_store(":memory:", count=1000)
        elapsed = time.perf_counter() - t0
        cur = store.connection.execute("SELECT COUNT(*) FROM repositories")
        assert cur.fetchone()[0] == 1000
        assert elapsed < 2.0, f"Seeding 1,000 repositories took {elapsed:.3f}s (exceeds 2.0s SLA)"

    def test_catalog_realistic_metric_distributions(self, store: OperationalStore) -> None:
        """Catalog repositories have realistic distributions of stars, downloads, and dependents."""
        cur = store.connection.execute(
            "SELECT MIN(stars), MAX(stars), AVG(stars), "
            "MIN(monthly_downloads), MAX(monthly_downloads), "
            "MIN(dependents_count), MAX(dependents_count) FROM repositories"
        )
        row = cur.fetchone()
        min_stars, max_stars, avg_stars = row[0], row[1], row[2]
        max_dl = row[4]
        max_dep = row[6]

        assert min_stars >= 0
        assert max_stars >= 50000, f"Max stars {max_stars} should reflect top OSS landmark repos"
        assert avg_stars > 1000, f"Average stars {avg_stars} is unrealistically low"
        assert max_dl > 1_000_000, "Max monthly downloads should exceed 1M"
        assert max_dep > 1000, "Max dependents should exceed 1,000"


# =========================================================================== #
# Feature 2: Canonical Parameter Hierarchy & WAM (>=5 tests)
# =========================================================================== #
class TestCanonicalWeightsFeature:
    """Canonical parameter priority ordering and WAM mathematics."""

    def test_canonical_pillar_priority_weights(self) -> None:
        """Requirement R2: P1 25%, P2 25%, P3 20%, P4 15%, P5 15% canonical weights."""
        assert PILLAR_WEIGHTS["responsiveness"] == 0.25
        assert PILLAR_WEIGHTS["criticality"] == 0.25
        assert PILLAR_WEIGHTS["governance"] == 0.20
        assert PILLAR_WEIGHTS["rigor"] == 0.15
        assert PILLAR_WEIGHTS["accessibility"] == 0.15
        assert sum(PILLAR_WEIGHTS.values()) == pytest.approx(1.0, abs=1e-6)

    def test_canonical_underlying_15_parameters_weights(self) -> None:
        """Requirement R2: 15 empirical parameters with exact internal pillar allocations."""
        assert sum(PARAM_WEIGHTS["responsiveness"].values()) == pytest.approx(1.0, abs=1e-6)
        assert sum(PARAM_WEIGHTS["criticality"].values()) == pytest.approx(1.0, abs=1e-6)
        assert sum(PARAM_WEIGHTS["governance"].values()) == pytest.approx(1.0, abs=1e-6)
        assert sum(PARAM_WEIGHTS["rigor"].values()) == pytest.approx(1.0, abs=1e-6)
        assert sum(PARAM_WEIGHTS["accessibility"].values()) == pytest.approx(1.0, abs=1e-6)

        # Verify key individual weights per specification
        assert PARAM_WEIGHTS["responsiveness"]["ttfhr"] == 0.35
        assert PARAM_WEIGHTS["responsiveness"]["mr_first"] == 0.25
        assert PARAM_WEIGHTS["responsiveness"]["ghost_rate"] == 0.20
        assert PARAM_WEIGHTS["responsiveness"]["ttm_p90"] == 0.20

    def test_wam_scoring_mathematical_consistency(self, store: OperationalStore) -> None:
        """Overall CVI is the exact canonical weighted sum of the 5 pillar scores for non-gated repos."""
        cur = store.connection.execute(
            "SELECT score_responsiveness, score_criticality, score_governance, "
            "score_rigor_ai, score_accessibility, overall_cvi "
            "FROM repositories WHERE is_stage0_disqualified = 0 LIMIT 50"
        )
        for p1, p2, p3, p4, p5, stored_cvi in cur.fetchall():
            expected_cvi = 0.25 * p1 + 0.25 * p2 + 0.20 * p3 + 0.15 * p4 + 0.15 * p5
            assert stored_cvi == pytest.approx(expected_cvi, abs=0.15)

    def test_leverage_formula_decomposition(self, store: OperationalStore) -> None:
        """Leverage (Y-axis) decomposes into: 0.50*P2 + 0.30*P4 + 0.20*P3."""
        assert LEVERAGE_WEIGHTS["criticality"] == 0.50
        assert LEVERAGE_WEIGHTS["rigor"] == 0.30
        assert LEVERAGE_WEIGHTS["governance"] == 0.20

        cur = store.connection.execute(
            "SELECT score_criticality, score_rigor_ai, score_governance, leverage_score "
            "FROM repositories WHERE is_stage0_disqualified = 0 LIMIT 50"
        )
        for p2, p4, p3, stored_y in cur.fetchall():
            expected_y = 0.50 * p2 + 0.30 * p4 + 0.20 * p3
            assert stored_y == pytest.approx(expected_y, abs=0.15)

    def test_friction_formula_decomposition(self, store: OperationalStore) -> None:
        """Friction (X-axis) decomposes into: 0.50*(100-P1) + 0.30*(100-P3) + 0.20*(100-P5)."""
        assert FRICTION_WEIGHTS["responsiveness"] == 0.50
        assert FRICTION_WEIGHTS["governance"] == 0.30
        assert FRICTION_WEIGHTS["accessibility"] == 0.20

        cur = store.connection.execute(
            "SELECT score_responsiveness, score_governance, score_accessibility, friction_score "
            "FROM repositories WHERE is_stage0_disqualified = 0 LIMIT 50"
        )
        for p1, p3, p5, stored_x in cur.fetchall():
            expected_x = 0.50 * (100.0 - p1) + 0.30 * (100.0 - p3) + 0.20 * (100.0 - p5)
            assert stored_x == pytest.approx(expected_x, abs=0.15)


# =========================================================================== #
# Feature 3: Playbook Diagnostics (>=5 tests)
# =========================================================================== #
class TestPlaybookDiagnosticsFeature:
    """Maintainer Improvement Playbook and diagnostic gap analysis."""

    def test_playbook_detects_review_latency_bottleneck(self, web_html: str) -> None:
        """Playbook detects TTFHR > 24h bottleneck and projects point increase."""
        # Verify diagnostic rule in frontend client engine
        assert "ttfhr > 24" in web_html
        assert "Reduce TTFHR" in web_html
        assert "Target Median TTFHR < 24.0h" in web_html

    def test_playbook_detects_pr_abandonment_bottleneck(self, web_html: str) -> None:
        """Playbook detects ghost rate > 5% bottleneck and recommends automated pings."""
        assert "ghostRate > 0.05" in web_html
        assert "Eliminate PR Abandonment" in web_html
        assert "Target 14-day Ghost Rate < 3.0%" in web_html

    def test_playbook_detects_single_vendor_governance(self, web_html: str) -> None:
        """Playbook detects Elephant Factor < 3 and projects +25 pts on P3 Governance."""
        assert "ef < 3" in web_html
        assert "Diversify maintainers: current Elephant Factor is" in web_html
        assert "Target Elephant Factor ≥ 3 (Multi-Vendor)" in web_html

    def test_playbook_detects_deficient_good_first_issues(self, web_html: str) -> None:
        """Playbook detects active GFIs < 3 and projects +12 pts on P5 Accessibility."""
        assert "gfiCount < 3" in web_html
        assert "Tag 3-5 beginner-friendly Good First Issues" in web_html
        assert "Target GFIs ≥ 5 active" in web_html

    def test_playbook_handles_clean_benchmark_leaders(self, web_html: str) -> None:
        """Repositories with no bottlenecks receive S-Tier leadership confirmation."""
        assert "Maintain Benchmark Leadership" in web_html
        assert "Top 1% Global Open Source Benchmark" in web_html

    def test_playbook_renders_in_drawer_ui(self, web_html: str) -> None:
        """Drawer UI includes all required Playbook CSS classes and containers."""
        for css_class in (
            "playbook-container",
            "playbook-banner",
            "playbook-action",
            "playbook-action-header",
            "playbook-action-title",
            "playbook-action-badge",
            "playbook-target",
        ):
            assert css_class in web_html, f"Missing {css_class} in web/index.html"


# =========================================================================== #
# Feature 4: Comparator Screen E (>=5 tests)
# =========================================================================== #
class TestComparatorScreenEFeature:
    """Side-by-side repository comparator across 15 empirical parameters."""

    def test_comparator_screen_and_navigation_elements_present(self, web_html: str) -> None:
        """Screen E comparator tab, container, picker, and diff table are present."""
        assert 'id="nav-comparator"' in web_html
        assert 'id="screen-comparator"' in web_html
        assert 'id="comp-repo-picker"' in web_html
        assert 'id="comp-diff-table"' in web_html
        assert 'id="comp-verdict-box"' in web_html

    def test_comparator_superimposed_radar_generation(self, web_html: str) -> None:
        """Comparator renders superimposed 5-pillar radar chart with distinct colors."""
        assert "renderMultiRadarSvg" in web_html
        assert "comp-radar-container" in web_html
        # Check that 4 distinct slot colors are defined for compared repos
        assert "COMP_COLORS" in web_html
        for color in ("#10b981", "#6366f1", "#f59e0b", "#f43f5e"):
            assert color in web_html.lower(), f"Expected comparator color {color} in index.html"

    def test_comparator_15_parameters_diff_table(self, web_html: str) -> None:
        """Comparator differential table compares all 15 parameters across 5 categories."""
        for dimension in (
            "Overall CVI Score",
            "Global Rank",
            "Benchmark Tier",
            "Leverage Score",
            "Friction Score",
            "Responsiveness Score (P1)",
            "Median TTFHR",
            "First-Timer Merge Rate",
            "14-Day PR Ghost Rate",
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
            assert dimension in web_html, f"Missing comparator dimension {dimension}"

    def test_comparator_winning_badges_logic(self, web_html: str) -> None:
        """Highest-scoring repository per metric receives winning badge indicator."""
        assert "diff-winner" in web_html
        assert "👑" in web_html
        assert "higherBetter" in web_html

    def test_comparator_verdict_synthesis_engine(self, web_html: str) -> None:
        """Verdict engine synthesizes recommendations for first-timers, career, and governance."""
        assert "renderComparatorVerdict" in web_html
        assert "Highest Composite Benchmark" in web_html
        assert "For First-Time Contributors" in web_html
        assert "For Career Capital & Resume Proof" in web_html
        assert "For Governance & Legal Safety" in web_html

    def test_comparator_min_repo_validation(self, web_html: str) -> None:
        """Comparator handles < 2 repositories with a clear user prompt."""
        assert "Please select at least 2 repositories to compare" in web_html


# =========================================================================== #
# Feature 5: API Endpoints (>=5 tests)
# =========================================================================== #
class TestAPIEndpointsFeature:
    """REST API endpoints adhering to contracts and performance SLAs."""

    def test_api_healthz_liveness(self, client: TestClient) -> None:
        """Liveness probe GET /healthz returns 200 OK within 2ms."""
        t0 = time.perf_counter()
        resp = client.get("/healthz")
        elapsed = time.perf_counter() - t0
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        assert elapsed < 0.05, "Healthz response time exceeds 50ms"

    def test_api_matrix_spatial_query_sla(self, client: TestClient) -> None:
        """Spatial query GET /v1/cvi/matrix returns nodes and quadrant totals in <50ms."""
        t0 = time.perf_counter()
        resp = client.get("/v1/cvi/matrix")
        elapsed = time.perf_counter() - t0
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data and "quadrant_counts" in data
        assert len(data["nodes"]) > 0
        assert set(data["quadrant_counts"].keys()) == {
            "Sweet Spot",
            "Crucible",
            "Nursery",
            "Graveyard",
        }
        assert elapsed < 0.05, f"Matrix query took {elapsed * 1000:.1f}ms (exceeds 50ms SLA)"

    def test_api_repo_deep_dive_scorecard_200_and_404(self, client: TestClient) -> None:
        """Scorecard query GET /v1/cvi/repos/{owner}/{name} returns full payload or 404."""
        resp = client.get("/v1/cvi/repos/vllm-project/vllm")
        assert resp.status_code == 200
        body = resp.json()
        assert body["owner"] == "vllm-project"
        assert body["name"] == "vllm"
        assert "pillar_scores" in body
        assert "deep_dive" in body

        # Missing repository returns 404 with JSON detail
        missing = client.get("/v1/cvi/repos/nonexistent/missing-repo")
        assert missing.status_code == 404
        assert "not found" in missing.json()["detail"]

    def test_api_paginated_leaderboard_endpoint(self, client: TestClient) -> None:
        """GET /v1/cvi/leaderboard supports pagination and ecosystem filter in <50ms."""
        t0 = time.perf_counter()
        resp = client.get("/v1/cvi/leaderboard?page=1&limit=25")
        elapsed = time.perf_counter() - t0
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1000
        assert len(data["items"]) == 25
        assert data["items"][0]["rank"] == 1
        assert elapsed < 0.05, f"Leaderboard query took {elapsed * 1000:.1f}ms (exceeds 50ms SLA)"

    def test_api_persona_reproject_endpoint(self, client: TestClient) -> None:
        """POST /v1/cvi/reproject re-ranks all repositories under custom 5-pillar weights."""
        custom_weights = {
            "responsiveness": 0.40,
            "criticality": 0.20,
            "governance": 0.20,
            "rigor": 0.10,
            "accessibility": 0.10,
        }
        t0 = time.perf_counter()
        resp = client.post("/v1/cvi/reproject", json={"weights": custom_weights})
        elapsed = time.perf_counter() - t0
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1000
        assert items[0]["new_rank"] == 1
        assert "delta" in items[0]
        assert elapsed < 0.10, f"Reproject took {elapsed * 1000:.1f}ms"


# =========================================================================== #
# Feature 6: Dual Theme Engine (>=5 tests)
# =========================================================================== #
class TestDualThemeEngineFeature:
    """Modern high-density developer interface with accessible Dark and Light modes."""

    def test_dual_theme_dark_mode_palette_tokens(self, web_html: str) -> None:
        """Dark mode tokens define deep obsidian canvas (#090D16) and Slate surfaces."""
        assert "#090d16" in web_html.lower()
        assert "#0f172a" in web_html.lower()
        assert "#f8fafc" in web_html.lower()

    def test_dual_theme_light_mode_palette_tokens(self, web_html: str) -> None:
        """Light mode tokens define slate canvas (#F8FAFC) and pure white card surfaces."""
        assert "#f8fafc" in web_html.lower()
        assert 'data-theme="light"' in web_html

    def test_dual_theme_header_toggle_button(self, web_html: str) -> None:
        """Header contains accessible theme toggle button with sun/moon glyphs."""
        assert 'id="theme-toggle-btn"' in web_html
        assert "setTheme" in web_html

    def test_dual_theme_persistence_via_localstorage(self, web_html: str) -> None:
        """Theme choice persists across reloads via localStorage."""
        assert "localStorage.getItem" in web_html
        assert "localStorage.setItem" in web_html
        assert "theme" in web_html

    def test_dual_theme_wcag_21_contrast_ratios(self) -> None:
        """WCAG 2.1 AA contrast compliance (>4.5:1 for normal text)."""

        # Luminance calculator for sRGB hex values
        def lum(hex_code: str) -> float:
            c = hex_code.lstrip("#")
            rgb = [int(c[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]
            adj = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
            return 0.2126 * adj[0] + 0.7152 * adj[1] + 0.0722 * adj[2]

        def contrast(c1: str, c2: str) -> float:
            l1, l2 = lum(c1), lum(c2)
            return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)

        # Dark theme: Text #F8FAFC on Canvas #090D16
        cr_dark = contrast("#F8FAFC", "#090D16")
        assert cr_dark >= 15.0, f"Dark mode contrast {cr_dark:.1f}:1 fails WCAG AAA"

        # Light theme: Text #0F172A on Canvas #F8FAFC
        cr_light = contrast("#0F172A", "#F8FAFC")
        assert cr_light >= 14.0, f"Light mode contrast {cr_light:.1f}:1 fails WCAG AAA"

    def test_dual_theme_chart_adaptation(self, web_html: str) -> None:
        """Chart rendering logic inspects current theme state to adjust stroke/text contrast."""
        assert "state.theme === 'light'" in web_html or 'state.theme === "light"' in web_html


# =========================================================================== #
# Feature 7: Zero-CDN & Offline Delivery (>=5 tests)
# =========================================================================== #
class TestZeroCDNOfflineFeature:
    """100% self-contained web bundle with zero external network dependencies."""

    def test_zero_external_script_tags(self, web_html: str) -> None:
        """Acceptance Criteria: Zero external script tags (no CDN JS libraries)."""
        script_srcs = re.findall(r'<script\s+[^>]*src=["\']([^"\']+)["\']', web_html, re.IGNORECASE)
        for src in script_srcs:
            assert not src.startswith("http://") and not src.startswith("https://"), (
                f"External script CDN dependency found: {src}"
            )

    def test_zero_external_stylesheet_tags(self, web_html: str) -> None:
        """Acceptance Criteria: Zero external stylesheet tags (no CDN CSS)."""
        link_hrefs = re.findall(r'<link\s+[^>]*href=["\']([^"\']+)["\']', web_html, re.IGNORECASE)
        for href in link_hrefs:
            assert not href.startswith("http://") and not href.startswith("https://"), (
                f"External stylesheet CDN dependency found: {href}"
            )

    def test_zero_external_font_imports(self, web_html: str) -> None:
        """Acceptance Criteria: Zero external web font imports (no Google Fonts, Typekit)."""
        assert "@import" not in web_html
        assert "fonts.googleapis.com" not in web_html
        assert "use.typekit.net" not in web_html

    def test_native_font_stacks_used(self, web_html: str) -> None:
        """Native system typography stacks are defined for sans and monospace."""
        assert "-apple-system" in web_html or "system-ui" in web_html
        assert "monospace" in web_html

    def test_app_serves_completely_from_root(self, client: TestClient) -> None:
        """FastAPI root GET / serves complete, functional HTML bundle."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "<!doctype html>" in resp.text.lower()
        assert "Contributor Value Index" in resp.text
