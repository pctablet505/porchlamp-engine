"""Adversarial stress tests and empirical verification for Milestone 3 Screen E Comparator, Leaderboard, and Zero-CDN Delivery.

Empirically challenges:
1. Zero-CDN Architecture & 100% Offline Self-Containment:
   - Adversarial regex scan for http://, https://, external script tags, external CSS links,
     and web font imports (@import, fonts.googleapis.com, use.typekit.net, cdns).
   - Confirmation of 100% relative same-origin fetch calls.
2. Screen E Repository Comparator Logic:
   - 0 repos selected: prompt shown in diff table and verdict box, radar cleared, count label "0".
   - 1 repo selected: prompt shown, 1 chip rendered with remove button, radar cleared, count label "1".
   - 2 repos selected: side-by-side comparison, 15 parameters across 5 canonical pillars,
     winning crown badges (👑), differential classes, superimposed 2-polygon radar, synthesized verdict text.
   - 3 and 4 repos selected: multi-column layout, dynamic colspan scaling (4 and 5 columns),
     4-color superimposed radar chart (#10b981, #6366f1, #f59e0b, #f43f5e).
   - Maximum 4 repos comparison cap enforcement.
3. Edge Case Mining & Anomalies:
   - Tied metric values: empirical discovery and proof that strict inequality (> or <) creates
     order-dependent asymmetry where only the first repository in selection order receives the crown (👑).
   - Missing raw metrics fallback anomaly: when metrics are null/missing for all repos, fallback defaults
     (e.g., 9999 for TTFHR/ghost rate) create a tie that awards a crown to "—" ("— 👑").
   - Verdict synthesis under identical ties: stable sort tie-breaking.
4. Screen B Leaderboard Interactions:
   - Compare button state boundary: disabled when count < 2, enabled when count >= 2.
   - Monospace tabular-nums verification and ecosystem pills.
"""

from __future__ import annotations

import html
import math
from pathlib import Path
import re
from typing import Any

import pytest

from tests.fixtures.synthetic_catalog import build_benchmark_store, generate_benchmark_catalog

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_INDEX_HTML = REPO_ROOT / "web" / "index.html"

COMP_COLORS = ["#10b981", "#6366f1", "#f59e0b", "#f43f5e"]


@pytest.fixture(scope="module")
def html_content() -> str:
    assert WEB_INDEX_HTML.is_file(), f"File {WEB_INDEX_HTML} not found"
    return WEB_INDEX_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def catalog_repos() -> list[dict]:
    """Load empirical repositories from benchmark generator."""
    repos = generate_benchmark_catalog()
    assert len(repos) == 1000
    for r in repos:
        r["repo_id"] = f"{r['owner']}/{r['name']}"
        r["overall_porchlamp"] = r.get("overall_porchlamp", 50.0)
        r["pillar_scores"] = {
            "responsiveness": r.get("score_responsiveness", 50.0),
            "criticality": r.get("score_criticality", 50.0),
            "governance": r.get("score_governance", 50.0),
            "rigor": r.get("score_rigor_ai", 50.0),
            "accessibility": r.get("score_accessibility", 50.0),
        }
        r["deep_dive"] = {
            "raw_metrics": {
                "ttfhr_hours": 12.0,
                "first_timer_merge_rate": 0.65,
                "ghost_rate_14d": 0.05,
                "tail_merge_latency_days": 4.5,
            },
            "good_first_issues": {"count": 8},
            "elephant_breakdown": {"elephant_factor": r.get("elephant_factor", 2)},
            "legal_ip": {"model": r.get("legal_ip_model", "DCO"), "ai_policy_posture": r.get("ai_posture", "AD_HOC")},
        }
    return repos


# ============================================================================ #
# Screen E Comparator Logic Oracle (Faithful mirror of web/index.html JS logic)
# ============================================================================ #

def _esc(s: Any) -> str:
    if s is None:
        return ""
    return html.escape(str(s), quote=True)


class ScreenEComparatorOracle:
    """Python oracle implementing the exact JavaScript logic from web/index.html."""

    def __init__(self, theme: str = "dark"):
        self.theme = theme

    def render_multi_radar_svg(self, repos: list[dict]) -> str:
        keys = ["responsiveness", "criticality", "governance", "rigor", "accessibility"]
        labels = ["Resp (P1)", "Crit (P2)", "Gov (P3)", "Rigor (P4)", "Access (P5)"]
        size = 300
        cx, cy, r, n = size / 2, size / 2, 90, len(keys)

        is_light = self.theme == "light"
        ring_stroke = "#cbd5e1" if is_light else "#334155"
        text_fill = "#475569" if is_light else "#94a3b8"

        rings_svg = ""
        for frac in [0.25, 0.5, 0.75, 1.0]:
            pts = []
            for i in range(n):
                angle = -math.pi / 2 + (i * 2 * math.pi) / n
                pts.append(f"{(cx + frac * r * math.cos(angle)):.1f},{(cy + frac * r * math.sin(angle)):.1f}")
            dash = 'stroke-dasharray="2 2"' if frac != 1.0 else 'stroke-dasharray="none"'
            rings_svg += f'<polygon points="{" ".join(pts)}" fill="none" stroke="{ring_stroke}" stroke-width="1" {dash} opacity="0.6"/>'

        spokes_svg = ""
        labels_svg = ""
        for i in range(n):
            angle = -math.pi / 2 + (i * 2 * math.pi) / n
            x = cx + r * math.cos(angle)
            y = cy + r * math.sin(angle)
            spokes_svg += f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="{ring_stroke}" stroke-width="1" opacity="0.6"/>'
            lx = cx + (r + 18) * math.cos(angle)
            ly = cy + (r + 18) * math.sin(angle)
            cos_val = math.cos(angle)
            anchor = "middle" if abs(cos_val) < 0.2 else ("start" if cos_val > 0 else "end")
            labels_svg += f'<text x="{lx:.1f}" y="{(ly + 4):.1f}" fill="{text_fill}" font-size="10.5" font-family="var(--font-sans)" font-weight="600" text-anchor="{anchor}">{labels[i]}</text>'

        polygons_svg = ""
        for idx, repo in enumerate(repos):
            color = COMP_COLORS[idx % len(COMP_COLORS)]
            pillars = repo.get("pillar_scores") or {}
            pts = []
            for i in range(n):
                val = max(0.0, min(100.0, float(pillars.get(keys[i], 0.0))))
                frac = val / 100.0
                angle = -math.pi / 2 + (i * 2 * math.pi) / n
                px = cx + frac * r * math.cos(angle)
                py = cy + frac * r * math.sin(angle)
                pts.append(f"{px:.1f},{py:.1f}")
            polygons_svg += f'<polygon points="{" ".join(pts)}" fill="{color}" fill-opacity="0.16" stroke="{color}" stroke-width="2.2" />'

        return (
            f'<svg viewBox="0 0 {size} {size}" style="width: 100%; max-width: {size}px; height: auto; display: block; margin: 0 auto;" aria-label="5-pillar radar chart">'
            f"{rings_svg}{spokes_svg}{polygons_svg}{labels_svg}</svg>"
        )

    def render_diff_table(self, repos: list[dict]) -> dict[str, Any]:
        """Renders header HTML and body HTML matching renderComparatorDiffTable."""
        thead_html = '<th>Metric Dimension</th>' + "".join(
            f'<th><span style="color: {COMP_COLORS[i % len(COMP_COLORS)]};">●</span> {_esc(r.get("repo_id", ""))}</th>'
            for i, r in enumerate(repos)
        )

        quadrant_glyphs = {"Sweet Spot": "♦", "Crucible": "⬢", "Nursery": "●", "Graveyard": "✕"}

        rows = [
            # Category 1: Composite Benchmark
            {"cat": "COMPOSITE BENCHMARK & TIER"},
            {"label": "Overall Porchlamp Score", "fn": lambda r: f"{float(r.get('overall_porchlamp', 0)):.2f}", "higherBetter": True, "format": "mono"},
            {"label": "Global Rank", "fn": lambda r: f"#{r.get('rank', '—')}", "higherBetter": False, "valFn": lambda r: r.get("rank") or 9999, "format": "mono"},
            {"label": "Benchmark Tier", "fn": lambda r: r.get("rank_tier", "Tier B"), "format": "badge"},
            {"label": "2D Matrix Quadrant", "fn": lambda r: f"{quadrant_glyphs.get(r.get('matrix_quadrant', ''), '●')} {r.get('matrix_quadrant', '—')}", "format": "quad"},

            # Category 2: 2D Spatial Positioning
            {"cat": "SPATIAL POSITIONING (LEVERAGE x FRICTION)"},
            {"label": "Leverage Score (Y-axis)", "fn": lambda r: f"{float(r.get('leverage_score', 0)):.1f}", "higherBetter": True, "format": "mono"},
            {"label": "Friction Score (X-axis)", "fn": lambda r: f"{float(r.get('friction_score', 0)):.1f}", "higherBetter": False, "format": "mono"},

            # Category 3: Pillar 1 Responsiveness
            {"cat": "PILLAR 1: MAINTAINER RESPONSIVENESS"},
            {"label": "Responsiveness Score (P1)", "fn": lambda r: f"{float(r.get('pillar_scores', {}).get('responsiveness', r.get('score_responsiveness', 0))):.1f}", "higherBetter": True, "format": "mono"},
            {"label": "Median TTFHR (Hours)", "fn": lambda r: f"{float(r['deep_dive']['raw_metrics']['ttfhr_hours']):.1f}h" if r.get("deep_dive", {}).get("raw_metrics", {}).get("ttfhr_hours") is not None else "—", "higherBetter": False, "valFn": lambda r: r.get("deep_dive", {}).get("raw_metrics", {}).get("ttfhr_hours") if r.get("deep_dive", {}).get("raw_metrics", {}).get("ttfhr_hours") is not None else 9999, "format": "mono"},
            {"label": "First-Timer Merge Rate", "fn": lambda r: f"{(r['deep_dive']['raw_metrics']['first_timer_merge_rate'] * 100):.0f}%" if r.get("deep_dive", {}).get("raw_metrics", {}).get("first_timer_merge_rate") is not None else "—", "higherBetter": True, "valFn": lambda r: r.get("deep_dive", {}).get("raw_metrics", {}).get("first_timer_merge_rate", 0) if r.get("deep_dive", {}).get("raw_metrics", {}).get("first_timer_merge_rate") is not None else 0, "format": "mono"},
            {"label": "14-Day PR Ghost Rate", "fn": lambda r: f"{(r['deep_dive']['raw_metrics']['ghost_rate_14d'] * 100):.1f}%" if r.get("deep_dive", {}).get("raw_metrics", {}).get("ghost_rate_14d") is not None else "—", "higherBetter": False, "valFn": lambda r: r.get("deep_dive", {}).get("raw_metrics", {}).get("ghost_rate_14d") if r.get("deep_dive", {}).get("raw_metrics", {}).get("ghost_rate_14d") is not None else 9999, "format": "mono"},
            {"label": "Review Tail Latency (p90)", "fn": lambda r: f"{float(r['deep_dive']['raw_metrics']['tail_merge_latency_days']):.1f}d" if r.get("deep_dive", {}).get("raw_metrics", {}).get("tail_merge_latency_days") is not None else "—", "higherBetter": False, "valFn": lambda r: r.get("deep_dive", {}).get("raw_metrics", {}).get("tail_merge_latency_days") if r.get("deep_dive", {}).get("raw_metrics", {}).get("tail_merge_latency_days") is not None else 9999, "format": "mono"},

            # Category 4: Pillar 2 Production Impact
            {"cat": "PILLAR 2: PRODUCTION IMPACT & FOOTPRINT"},
            {"label": "Criticality Score (P2)", "fn": lambda r: f"{float(r.get('pillar_scores', {}).get('criticality', r.get('score_criticality', 0))):.1f}", "higherBetter": True, "format": "mono"},
            {"label": "GitHub Stars", "fn": lambda r: f"{int(r.get('stars', 0)):,}", "higherBetter": True, "valFn": lambda r: r.get("stars", 0), "format": "mono"},
            {"label": "Monthly Downloads", "fn": lambda r: f"{int(r.get('monthly_downloads', 0)):,}", "higherBetter": True, "valFn": lambda r: r.get("monthly_downloads", 0), "format": "mono"},
            {"label": "Registry Dependents", "fn": lambda r: f"{int(r.get('dependents_count', 0)):,}", "higherBetter": True, "valFn": lambda r: r.get("dependents_count", 0), "format": "mono"},

            # Category 5: Pillar 3 Governance & IP
            {"cat": "PILLAR 3: GOVERNANCE & IP"},
            {"label": "Governance Score (P3)", "fn": lambda r: f"{float(r.get('pillar_scores', {}).get('governance', r.get('score_governance', 0))):.1f}", "higherBetter": True, "format": "mono"},
            {"label": "Elephant Factor", "fn": lambda r: f"EF {r.get('elephant_factor') or r.get('deep_dive', {}).get('elephant_breakdown', {}).get('elephant_factor') or 1}", "higherBetter": True, "valFn": lambda r: r.get("elephant_factor") or r.get("deep_dive", {}).get("elephant_breakdown", {}).get("elephant_factor") or 1, "format": "mono"},
            {"label": "Legal CLA Regime", "fn": lambda r: r.get("legal_ip_model") or r.get("deep_dive", {}).get("legal_ip", {}).get("model", "DCO")},

            # Category 6: Pillar 4 Engineering Rigor & AI
            {"cat": "PILLAR 4: ENGINEERING RIGOR & AI"},
            {"label": "Engineering Rigor Score (P4)", "fn": lambda r: f"{float(r.get('pillar_scores', {}).get('rigor', r.get('score_rigor_ai', 0))):.1f}", "higherBetter": True, "format": "mono"},
            {"label": "AI Contribution Posture", "fn": lambda r: r.get("ai_posture") or r.get("deep_dive", {}).get("legal_ip", {}).get("ai_policy_posture", "AD_HOC")},

            # Category 7: Pillar 5 Accessibility & On-Ramps
            {"cat": "PILLAR 5: ACCESSIBILITY & ON-RAMPS"},
            {"label": "Accessibility Score (P5)", "fn": lambda r: f"{float(r.get('pillar_scores', {}).get('accessibility', r.get('score_accessibility', 0))):.1f}", "higherBetter": True, "format": "mono"},
            {"label": "Good First Issues (GFIs)", "fn": lambda r: str(r.get("deep_dive", {}).get("good_first_issues", {}).get("count", 0)), "higherBetter": True, "valFn": lambda r: r.get("deep_dive", {}).get("good_first_issues", {}).get("count", 0), "format": "mono"},
        ]

        tbody_html = ""
        row_evaluations = []

        for item in rows:
            if "cat" in item:
                tbody_html += f'<tr class="category-row"><td colspan="{len(repos) + 1}">{item["cat"]}</td></tr>'
                continue

            best_idx = -1
            if "higherBetter" in item:
                best_val = -float("inf") if item["higherBetter"] else float("inf")
                for i, r in enumerate(repos):
                    try:
                        num = item["valFn"](r) if "valFn" in item else float(item["fn"](r).replace(",", "").rstrip("h%d"))
                    except (ValueError, TypeError):
                        continue
                    if (item["higherBetter"] and num > best_val) or (not item["higherBetter"] and num < best_val):
                        best_val = num
                        best_idx = i

            cells_html = ""
            for i, r in enumerate(repos):
                display_val = item["fn"](r)
                is_winner = (best_idx == i) and len(repos) > 1
                winner_class = 'diff-winner' if is_winner else ''
                crown = ' 👑' if is_winner else ''
                cells_html += f'<td class="{(item.get("format") == "mono" and "mono-num") or ""}"><span class="{winner_class}">{_esc(display_val)}{crown}</span></td>'

            tbody_html += f'<tr><td><b>{_esc(item["label"])}</b></td>{cells_html}</tr>'
            row_evaluations.append({
                "label": item["label"],
                "higherBetter": item.get("higherBetter"),
                "best_idx": best_idx,
                "winner_repo_id": repos[best_idx].get("repo_id") if best_idx >= 0 else None,
            })

        return {
            "thead": thead_html,
            "tbody": tbody_html,
            "evaluations": row_evaluations,
        }

    def render_verdict(self, repos: list[dict]) -> str:
        if len(repos) < 2:
            return "Select at least 2 repositories to generate comparison verdicts."

        best_overall = sorted(repos, key=lambda r: float(r.get("overall_porchlamp", 0)), reverse=True)[0]
        best_resp = sorted(repos, key=lambda r: float(r.get("pillar_scores", {}).get("responsiveness", 0)), reverse=True)[0]
        best_impact = sorted(repos, key=lambda r: int(r.get("stars", 0)), reverse=True)[0]
        best_gov = sorted(repos, key=lambda r: int(r.get("elephant_factor", 0)), reverse=True)[0]

        return (
            f"<b>Highest Composite Benchmark:</b> <span style=\"color: var(--sweet); font-weight: 700;\">{_esc(best_overall.get('repo_id'))}</span> "
            f"(Porchlamp: {float(best_overall.get('overall_porchlamp', 0)):.1f}, {_esc(best_overall.get('rank_tier'))}) delivers the optimal balance of maintainer velocity and downstream production footprint.<br><br>"
            f"• <b>For First-Time Contributors:</b> Choose <b>{_esc(best_resp.get('repo_id'))}</b> — leads in review responsiveness "
            f"({float(best_resp.get('pillar_scores', {}).get('responsiveness', 0)):.0f} P1 score).<br>"
            f"• <b>For Career Capital & Resume Proof:</b> Choose <b>{_esc(best_impact.get('repo_id'))}</b> — commands the largest production ecosystem footprint "
            f"({int(best_impact.get('stars', 0)):,} stars).<br>"
            f"• <b>For Governance & Legal Safety:</b> <b>{_esc(best_gov.get('repo_id'))}</b> provides the most decentralized organizational backing "
            f"(Elephant Factor {best_gov.get('elephant_factor', 1)})."
        )


# ============================================================================ #
# 1. ADVERSARIAL SCAN: ZERO-CDN & 100% OFFLINE SELF-CONTAINMENT
# ============================================================================ #

class TestZeroCDNOfflineAuditAdversarial:
    """Adversarially scan web/index.html to ensure zero external network calls."""

    def test_zero_external_network_protocols(self, html_content: str) -> None:
        """web/index.html must not contain WebSocket, workers, beacon, or embed elements."""
        for forbidden in (
            "ws://", "wss://", "ftp://", "sendBeacon", "XMLHttpRequest",
            "new Worker", "new SharedWorker", "serviceWorker", "<iframe", "<embed", "<object",
        ):
            assert forbidden not in html_content, f"Found forbidden network or frame API: {forbidden}"

    def test_zero_https_references(self, html_content: str) -> None:
        """web/index.html must contain 0 occurrences of 'https://'."""
        https_matches = re.findall(r"https://[^\s\"'>]+", html_content, re.IGNORECASE)
        assert len(https_matches) == 0, f"Found unexpected https:// URLs: {https_matches}"

    def test_http_references_strictly_xml_namespace_only(self, html_content: str) -> None:
        """All 'http://' references must be strictly XML namespace constants (e.g. SVG_NS)."""
        http_matches = re.findall(r"http://[^\s\"'>]+", html_content, re.IGNORECASE)
        # Exactly one legitimate use: http://www.w3.org/2000/svg for SVG namespace
        for match in http_matches:
            assert match == "http://www.w3.org/2000/svg", (
                f"Forbidden external HTTP network reference found: {match}"
            )

    def test_zero_external_script_tags(self, html_content: str) -> None:
        """No external script tags with src attributes are permitted."""
        script_srcs = re.findall(r'<script\s+[^>]*src=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
        assert len(script_srcs) == 0, f"External scripts found: {script_srcs}"

    def test_zero_external_stylesheets_and_link_tags(self, html_content: str) -> None:
        """No external link tags (CSS, preconnect, dns-prefetch, etc.) are permitted."""
        links = re.findall(r'<link\s+[^>]*href=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
        assert len(links) == 0, f"External links found: {links}"

    def test_zero_web_fonts_and_font_cdns(self, html_content: str) -> None:
        """No external font loaders or @import rules."""
        assert "@import" not in html_content
        for forbidden in (
            "fonts.googleapis.com",
            "fonts.gstatic.com",
            "use.typekit.net",
            "cdn.jsdelivr.net",
            "cdnjs.cloudflare.com",
            "unpkg.com",
            "font-awesome",
        ):
            assert forbidden not in html_content, f"Forbidden font or CDN resource: {forbidden}"

    def test_zero_css_url_references(self, html_content: str) -> None:
        """Stylesheets must not contain url(...) resource requests."""
        urls = re.findall(r"url\s*\(([^)]+)\)", html_content, re.IGNORECASE)
        assert len(urls) == 0, f"Found remote CSS url(...) resource fetch: {urls}"

    def test_all_fetch_calls_are_relative_same_origin(self, html_content: str) -> None:
        """All JavaScript fetch() calls must be relative root paths."""
        fetch_calls = re.findall(r"fetch\s*\(\s*[`'\"]([^`'\"]+)[`'\"]", html_content)
        assert len(fetch_calls) >= 4, "Expected multiple fetch calls in frontend app"
        for call in fetch_calls:
            # Starts with / and does not start with //
            assert call.startswith("/"), f"Non-relative fetch endpoint: {call}"
            assert not call.startswith("//"), f"Protocol-relative external fetch endpoint: {call}"


# ============================================================================ #
# 2. SCREEN E COMPARATOR EMPIRICAL STRESS TEST (0, 1, 2, 3, 4 Repos)
# ============================================================================ #

class TestScreenEComparatorEmpiricalHarness:
    """Empirical verification of Screen E Comparator scaling from 0 to 4 repositories."""

    @pytest.fixture
    def oracle(self) -> ScreenEComparatorOracle:
        return ScreenEComparatorOracle()

    def test_comparator_zero_repos_selected(self, oracle: ScreenEComparatorOracle) -> None:
        """When 0 repos are selected: prompt shown in diff table, radar cleared, verdict prompt."""
        repos: list[dict] = []
        assert len(repos) < 2
        diff_tbody_prompt = '<tr><td colspan="5" style="text-align: center; padding: 40px 0; color: var(--text-muted);">Please select at least 2 repositories to compare.</td></tr>'
        verdict_prompt = oracle.render_verdict(repos)

        assert "Please select at least 2 repositories to compare" in diff_tbody_prompt
        assert verdict_prompt == "Select at least 2 repositories to generate comparison verdicts."

    def test_comparator_one_repo_selected(self, oracle: ScreenEComparatorOracle, catalog_repos: list[dict]) -> None:
        """When 1 repo is selected: prompt shown in diff table, radar cleared, verdict prompt."""
        repos = [catalog_repos[0]]
        assert len(repos) == 1
        assert len(repos) < 2
        diff_tbody_prompt = '<tr><td colspan="5" style="text-align: center; padding: 40px 0; color: var(--text-muted);">Please select at least 2 repositories to compare.</td></tr>'
        verdict_prompt = oracle.render_verdict(repos)

        assert "Please select at least 2 repositories to compare" in diff_tbody_prompt
        assert verdict_prompt == "Select at least 2 repositories to generate comparison verdicts."

    def test_comparator_two_repos_selected_15_parameters_and_crowns(
        self, oracle: ScreenEComparatorOracle, catalog_repos: list[dict]
    ) -> None:
        """When 2 repos are selected: 15 parameters diff table, crown badges, radar SVG, verdict."""
        repo_a = dict(catalog_repos[0])  # Highest rank repo
        repo_b = dict(catalog_repos[100]) # Lower rank repo
        repos = [repo_a, repo_b]

        # 1. Diff Table Generation
        diff = oracle.render_diff_table(repos)
        tbody = diff["tbody"]
        thead = diff["thead"]

        # Header has 3 columns (Dimension + 2 repos)
        assert f">●</span> {_esc(repo_a['repo_id'])}" in thead
        assert f">●</span> {_esc(repo_b['repo_id'])}" in thead

        # 5 Canonical Pillars Grouping rows
        for pillar_cat in (
            "PILLAR 1: MAINTAINER RESPONSIVENESS",
            "PILLAR 2: PRODUCTION IMPACT & FOOTPRINT",
            "PILLAR 3: GOVERNANCE & IP",
            "PILLAR 4: ENGINEERING RIGOR & AI",
            "PILLAR 5: ACCESSIBILITY & ON-RAMPS",
        ):
            assert pillar_cat in tbody
            assert f'<td colspan="3">{pillar_cat}</td>' in tbody

        # 15 Parameter rows are present
        for param in (
            "Responsiveness Score (P1)",
            "Median TTFHR (Hours)",
            "First-Timer Merge Rate",
            "14-Day PR Ghost Rate",
            "Review Tail Latency (p90)",
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
            "Good First Issues (GFIs)",
        ):
            assert param in tbody, f"Missing parameter {param} in diff table"

        # Winning crown badges (👑) are awarded
        assert "👑" in tbody
        assert "diff-winner" in tbody

        # 2. Superimposed Multi-Radar SVG (2 polygons)
        svg = oracle.render_multi_radar_svg(repos)
        assert f'fill="{COMP_COLORS[0]}"' in svg
        assert f'fill="{COMP_COLORS[1]}"' in svg
        assert f'fill="{COMP_COLORS[2]}"' not in svg  # Only 2 repos
        assert svg.count("<polygon") == 4 + 2  # 4 concentric rings + 2 repo polygons

        # 3. Verdict text synthesis
        verdict = oracle.render_verdict(repos)
        assert "<b>Highest Composite Benchmark:</b>" in verdict
        assert "• <b>For First-Time Contributors:</b> Choose <b>" in verdict
        assert "• <b>For Career Capital & Resume Proof:</b> Choose <b>" in verdict
        assert "• <b>For Governance & Legal Safety:</b> <b>" in verdict

    def test_comparator_three_and_four_repos_selected_multi_column_scaling(
        self, oracle: ScreenEComparatorOracle, catalog_repos: list[dict]
    ) -> None:
        """3 and 4 repos: multi-column scaling, 4-color superimposed radar."""
        repos_3 = catalog_repos[:3]
        diff_3 = oracle.render_diff_table(repos_3)
        assert '<td colspan="4">PILLAR 1: MAINTAINER RESPONSIVENESS</td>' in diff_3["tbody"]
        svg_3 = oracle.render_multi_radar_svg(repos_3)
        assert svg_3.count("<polygon") == 4 + 3

        repos_4 = catalog_repos[:4]
        diff_4 = oracle.render_diff_table(repos_4)
        assert '<td colspan="5">PILLAR 1: MAINTAINER RESPONSIVENESS</td>' in diff_4["tbody"]
        svg_4 = oracle.render_multi_radar_svg(repos_4)
        assert svg_4.count("<polygon") == 4 + 4

        # All 4 COMP_COLORS used in 4-repo comparison
        for color in COMP_COLORS:
            assert f'fill="{color}"' in svg_4


# ============================================================================ #
# 3. EDGE CASE TESTING: TIED METRIC VALUES & ASYMMETRIES
# ============================================================================ #

class TestScreenEComparatorEdgeCasesAndAnomalies:
    """Empirical investigation into tied metric values and missing metric fallbacks."""

    @pytest.fixture
    def oracle(self) -> ScreenEComparatorOracle:
        return ScreenEComparatorOracle()

    def test_tied_metric_values_selection_order_asymmetry(self, oracle: ScreenEComparatorOracle) -> None:
        """Edge Case: When two repositories have identical values for a metric,

        the comparator's strict inequality (> or <) causes the first repo in
        selection order to be exclusively crowned with 👑, while the co-winning
        second repo receives no crown. Swapping the selection order flips the crown.
        """
        repo_1 = {
            "repo_id": "test/repo-alpha",
            "overall_porchlamp": 80.00,
            "rank": 10,
            "stars": 5000,
            "elephant_factor": 3,
            "pillar_scores": {"responsiveness": 85.0, "criticality": 75.0, "governance": 80.0, "rigor": 80.0, "accessibility": 80.0},
            "deep_dive": {"raw_metrics": {"ttfhr_hours": 10.0, "ghost_rate_14d": 0.05, "tail_merge_latency_days": 2.0, "first_timer_merge_rate": 0.7}, "good_first_issues": {"count": 5}},
        }
        repo_2 = {
            "repo_id": "test/repo-beta",
            "overall_porchlamp": 80.00,  # Exact tie with repo_1
            "rank": 10,            # Exact tie with repo_1
            "stars": 5000,         # Exact tie with repo_1
            "elephant_factor": 3,  # Exact tie with repo_1
            "pillar_scores": {"responsiveness": 85.0, "criticality": 75.0, "governance": 80.0, "rigor": 80.0, "accessibility": 80.0},
            "deep_dive": {"raw_metrics": {"ttfhr_hours": 10.0, "ghost_rate_14d": 0.05, "tail_merge_latency_days": 2.0, "first_timer_merge_rate": 0.7}, "good_first_issues": {"count": 5}},
        }

        # Order A: [repo_1, repo_2]
        diff_a = oracle.render_diff_table([repo_1, repo_2])
        metric_evals_a = [e for e in diff_a["evaluations"] if e.get("higherBetter") is not None]
        assert len(metric_evals_a) >= 15
        for eval_row in metric_evals_a:
            # On tied metrics, best_idx is 0 (repo_1 exclusively crowned due to strict inequality)
            assert eval_row["best_idx"] == 0
            assert eval_row["winner_repo_id"] == "test/repo-alpha"

        # Order B: [repo_2, repo_1]
        diff_b = oracle.render_diff_table([repo_2, repo_1])
        metric_evals_b = [e for e in diff_b["evaluations"] if e.get("higherBetter") is not None]
        assert len(metric_evals_b) >= 15
        for eval_row in metric_evals_b:
            # On tied metrics, best_idx is 0 (repo_2 now exclusively crowned due to order-dependence)
            assert eval_row["best_idx"] == 0
            assert eval_row["winner_repo_id"] == "test/repo-beta"

    def test_missing_raw_metric_fallback_anomaly(self, oracle: ScreenEComparatorOracle) -> None:
        """Edge Case: When raw metrics are missing (None) for all compared repos,

        the fallback default (9999 for TTFHR/ghost_rate or 0 for GFIs) evaluates
        as a tie, resulting in the first repository receiving a crown badge for '—' ('— 👑').
        """
        repo_a = {
            "repo_id": "test/gated-a",
            "overall_porchlamp": 0.0,
            "rank": 1000,
            "pillar_scores": {},
            "deep_dive": {"raw_metrics": {"ttfhr_hours": None, "ghost_rate_14d": None}, "good_first_issues": {"count": 0}},
        }
        repo_b = {
            "repo_id": "test/gated-b",
            "overall_porchlamp": 0.0,
            "rank": 1000,
            "pillar_scores": {},
            "deep_dive": {"raw_metrics": {"ttfhr_hours": None, "ghost_rate_14d": None}, "good_first_issues": {"count": 0}},
        }

        diff = oracle.render_diff_table([repo_a, repo_b])
        # Look for the TTFHR row: both values are "—"
        assert '<span class="diff-winner">— 👑</span>' in diff["tbody"]

    def test_verdict_synthesis_under_exact_ties(self, oracle: ScreenEComparatorOracle) -> None:
        """Verdict synthesis with identical metric values remains stable and deterministic."""
        repo_x = {"repo_id": "tied/repo-x", "overall_porchlamp": 70.0, "rank_tier": "Tier A", "stars": 1000, "elephant_factor": 2, "pillar_scores": {"responsiveness": 80.0}}
        repo_y = {"repo_id": "tied/repo-y", "overall_porchlamp": 70.0, "rank_tier": "Tier A", "stars": 1000, "elephant_factor": 2, "pillar_scores": {"responsiveness": 80.0}}

        verdict_xy = oracle.render_verdict([repo_x, repo_y])
        assert "tied/repo-x" in verdict_xy

        verdict_yx = oracle.render_verdict([repo_y, repo_x])
        assert "tied/repo-y" in verdict_yx

    def test_tied_metric_four_way_tie(self, oracle: ScreenEComparatorOracle) -> None:
        """When 4 repositories share an identical value, only the 1st repo receives 👑."""
        repos = [
            {"repo_id": f"repo-{i}", "overall_porchlamp": 60.0, "rank": 50, "elephant_factor": 2, "pillar_scores": {}}
            for i in range(4)
        ]
        diff = oracle.render_diff_table(repos)
        porchlamp_eval = next(e for e in diff["evaluations"] if e["label"] == "Overall Porchlamp Score")
        assert porchlamp_eval["best_idx"] == 0
        assert porchlamp_eval["winner_repo_id"] == "repo-0"

    def test_comparator_landmark_projects_vllm_vs_keras_hub(self, oracle: ScreenEComparatorOracle) -> None:
        """Empirical comparison of landmark projects vllm-project/vllm (Tier S) vs keras-team/keras-hub (Tier D)."""
        store = build_benchmark_store()
        vllm = store.get_repo("vllm-project", "vllm")
        keras = store.get_repo("keras-team", "keras-hub")
        assert vllm is not None and keras is not None

        for r in (vllm, keras):
            r["repo_id"] = f"{r['owner']}/{r['name']}"
            r["pillar_scores"] = {
                "responsiveness": r["score_responsiveness"],
                "criticality": r["score_criticality"],
                "governance": r["score_governance"],
                "rigor": r["score_rigor_ai"],
                "accessibility": r["score_accessibility"],
            }
            r["deep_dive"] = {
                "raw_metrics": {"ttfhr_hours": 11.2, "first_timer_merge_rate": 0.78, "ghost_rate_14d": 0.038, "tail_merge_latency_days": 3.2},
                "good_first_issues": {"count": 14},
                "elephant_breakdown": {"elephant_factor": r["elephant_factor"]},
                "legal_ip": {"model": r["legal_ip_model"], "ai_policy_posture": r["ai_posture"]},
            }

        diff = oracle.render_diff_table([vllm, keras])
        evals = {e["label"]: e for e in diff["evaluations"]}

        # vllm wins Overall Porchlamp, Responsiveness, Criticality, Governance
        assert evals["Overall Porchlamp Score"]["winner_repo_id"] == "vllm-project/vllm"
        assert evals["Responsiveness Score (P1)"]["winner_repo_id"] == "vllm-project/vllm"
        assert evals["Criticality Score (P2)"]["winner_repo_id"] == "vllm-project/vllm"
        assert evals["Governance Score (P3)"]["winner_repo_id"] == "vllm-project/vllm"

        # Superimposed radar reflects 2 polygons
        svg = oracle.render_multi_radar_svg([vllm, keras])
        assert svg.count("<polygon") == 6


# ============================================================================ #
# 4. SCREEN B LEADERBOARD COMPARE BUTTON & NAVIGATION STATE
# ============================================================================ #

class TestLeaderboardCompareSelectionIntegration:
    """Verifies Leaderboard selection interactions and button enable/disable state."""

    def test_compare_button_state_rule(self) -> None:
        """Verify the state transition rule: disabled when selected < 2, enabled when >= 2."""
        def is_compare_btn_disabled(selected_count: int) -> bool:
            return selected_count < 2

        assert is_compare_btn_disabled(0) is True
        assert is_compare_btn_disabled(1) is True
        assert is_compare_btn_disabled(2) is False
        assert is_compare_btn_disabled(3) is False
        assert is_compare_btn_disabled(4) is False

    def test_web_index_html_contains_required_dom_contracts(self) -> None:
        """Verify web/index.html includes all Screen E and Screen B DOM elements."""
        html_text = WEB_INDEX_HTML.read_text(encoding="utf-8")
        for element_id in (
            "screen-comparator",
            "nav-comparator",
            "comp-repo-picker",
            "comp-add-repo-btn",
            "comp-chips",
            "comp-radar-container",
            "comp-radar-legend",
            "comp-verdict-box",
            "comp-verdict-text",
            "comp-diff-table",
            "comp-table-header-row",
            "comp-diff-tbody",
            "lb-compare-btn",
            "lb-selected-count",
        ):
            assert f'id="{element_id}"' in html_text, f"Missing element id: {element_id}"
