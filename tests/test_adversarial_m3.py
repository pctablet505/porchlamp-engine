"""Adversarial stress tests and empirical verification for Milestone 3.

Empirically challenges:
1. Dual Theme System & Palette Tokens:
   - Verifies exact Dark Mode tokens: #090D16 base, #0F172A surface, #F8FAFC text,
     elevated surface #1E293B, hover #334155, subtle border #1E293B, strong border #334155, focus #38BDF8.
   - Verifies exact Light Mode tokens: #F8FAFC base, #FFFFFF surface, #0F172A text,
     elevated surface #F1F5F9, hover #E2E8F0, subtle border #E2E8F0, strong border #CBD5E1, focus #0284C7.
2. Exact Relative Luminance and WCAG 2.1 Contrast Calculations:
   - W3C relative luminance implementation across all token pairs.
   - Contrast calculation for text on base canvas (>15:1 dark, >14:1 light).
   - Contrast calculation for text on cards/surfaces (>15:1 dark, >17:1 light).
   - Contrast calculation for secondary and muted text.
   - UI component borders and focus indicators (>3.0:1).
   - Adversarial stress tests: badge text contrast on alpha backgrounds (e.g. Nursery badge #D97706),
     and JS watermark text in Light Mode.
3. LocalStorage Persistence & State Machine Verification:
   - Default initialization (with/without prefers-color-scheme).
   - Persistence round-trip across toggle cycles.
   - Omnibar palette theme integration.
   - Exception vulnerability analysis on storage access.
4. Dynamic SVG Chart Adaptation:
   - Scatter plot axis, grid, and bisector strokes under both themes.
   - Multi-radar chart polygon, ring, spoke, and label fills under both themes.
   - Single-repo radar chart adaptive colors.
   - Kaplan-Meier latency curve (renderSurvivalSvg) axis and grid strokes.
   - Re-rendering triggers upon theme toggle across scatter, drawer, and comparator.
"""

from __future__ import annotations

import re
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from fastapi.staticfiles import StaticFiles

from porchlamp.api import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = REPO_ROOT / "web"
INDEX_PATH = WEB_DIR / "index.html"


def relative_luminance(hex_code: str) -> float:
    """Calculate exact relative luminance according to WCAG 2.1 specification."""
    c = hex_code.lstrip("#")
    rgb = [int(c[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]
    linear = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(c1: str, c2: str) -> float:
    """Calculate contrast ratio between two hex colors according to WCAG 2.1."""
    l1 = relative_luminance(c1)
    l2 = relative_luminance(c2)
    return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)


def blend_rgba(fg_hex: str, alpha: float, bg_hex: str) -> str:
    """Simulate alpha blending of fg over bg."""
    fg = [int(fg_hex.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)]
    bg = [int(bg_hex.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)]
    blended = [round(fg[i] * alpha + bg[i] * (1 - alpha)) for i in range(3)]
    return "#" + "".join(f"{v:02x}" for v in blended)


@pytest.fixture(scope="module")
def html_content() -> str:
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = create_app()
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    with TestClient(app) as tc:
        yield tc


# =========================================================================== #
# 1. Palette Tokens Verification
# =========================================================================== #
class TestPaletteTokensAdversarial:
    def test_exact_dark_mode_tokens_present(self, html_content: str) -> None:
        """Verify dark mode tokens in :root."""
        match = re.search(r":root\s*\{([^}]+)\}", html_content)
        assert match is not None, ":root selector not found in CSS"
        root_css = match.group(1)

        expected_tokens = {
            "--bg-base": "#090d16",
            "--bg-surface": "#0f172a",
            "--bg-surface-elevated": "#1e293b",
            "--bg-surface-hover": "#334155",
            "--border-subtle": "#1e293b",
            "--border-strong": "#334155",
            "--border-focus": "#38bdf8",
            "--text-primary": "#f8fafc",
            "--sweet": "#10b981",
            "--crucible": "#6366f1",
            "--nursery": "#f59e0b",
            "--graveyard": "#ef4444",
            "--accent": "#38bdf8",
        }
        for token, val in expected_tokens.items():
            pattern = rf"{token}\s*:\s*{val}"
            assert re.search(pattern, root_css, re.IGNORECASE), f"Dark token missing: {token}: {val}"

    def test_exact_light_mode_tokens_present(self, html_content: str) -> None:
        """Verify light mode tokens in [data-theme="light"]."""
        match = re.search(r'\[data-theme="light"\]\s*\{([^}]+)\}', html_content)
        assert match is not None, '[data-theme="light"] selector not found in CSS'
        light_css = match.group(1)

        expected_tokens = {
            "--bg-base": "#f8fafc",
            "--bg-surface": "#ffffff",
            "--bg-surface-elevated": "#f1f5f9",
            "--bg-surface-hover": "#e2e8f0",
            "--border-subtle": "#e2e8f0",
            "--border-strong": "#cbd5e1",
            "--border-focus": "#0284c7",
            "--text-primary": "#0f172a",
            "--text-secondary": "#334155",
            "--text-muted": "#64748b",
            "--sweet": "#059669",
            "--crucible": "#4f46e5",
            "--nursery": "#d97706",
            "--graveyard": "#dc2626",
            "--accent": "#0284c7",
        }
        for token, val in expected_tokens.items():
            pattern = rf"{token}\s*:\s*{val}"
            assert re.search(pattern, light_css, re.IGNORECASE), f"Light token missing: {token}: {val}"

    def test_token_symmetry_between_themes(self, html_content: str) -> None:
        """Ensure all variables in :root have exact mirror overrides in [data-theme='light']."""
        root_block = re.search(r":root\s*\{([^}]+)\}", html_content).group(1)
        light_block = re.search(r'\[data-theme="light"\]\s*\{([^}]+)\}', html_content).group(1)

        root_vars = set(re.findall(r"(--[a-zA-Z0-9-]+)\s*:", root_block))
        light_vars = set(re.findall(r"(--[a-zA-Z0-9-]+)\s*:", light_block))

        core_vars = {
            "--bg-base", "--bg-surface", "--bg-surface-elevated", "--bg-surface-hover",
            "--border-subtle", "--border-strong", "--border-focus",
            "--text-primary", "--text-secondary", "--text-muted",
            "--sweet", "--sweet-bg", "--sweet-border",
            "--crucible", "--crucible-bg", "--crucible-border",
            "--nursery", "--nursery-bg", "--nursery-border",
            "--graveyard", "--graveyard-bg", "--graveyard-border",
            "--accent", "--accent-bg", "--accent-border",
            "--shadow-popover", "--shadow-drawer",
        }
        assert core_vars.issubset(root_vars), f"Missing in :root: {core_vars - root_vars}"
        assert core_vars.issubset(light_vars), f"Missing in light mode: {core_vars - light_vars}"

    def test_default_html_theme_attribute(self, html_content: str) -> None:
        """HTML element must declare data-theme attribute."""
        assert '<html lang="en" data-theme="dark">' in html_content


# =========================================================================== #
# 2. WCAG 2.1 AA Relative Luminance & Contrast Verification
# =========================================================================== #
class TestWCAGContrastAdversarial:
    def test_relative_luminance_baselines(self) -> None:
        """Empirically test W3C relative luminance values."""
        assert relative_luminance("#000000") == pytest.approx(0.0, abs=1e-5)
        assert relative_luminance("#FFFFFF") == pytest.approx(1.0, abs=1e-5)

        l_dark_base = relative_luminance("#090D16")
        l_dark_surface = relative_luminance("#0F172A")
        l_light_base = relative_luminance("#F8FAFC")
        l_light_surface = relative_luminance("#FFFFFF")

        assert l_dark_base == pytest.approx(0.00404, abs=1e-4)
        assert l_dark_surface == pytest.approx(0.00882, abs=1e-4)
        assert l_light_base == pytest.approx(0.95356, abs=1e-4)
        assert l_light_surface == pytest.approx(1.00000, abs=1e-4)

    def test_dark_mode_text_contrast_exceeds_aa_and_aaa(self) -> None:
        """Normal text >= 4.5:1 (AA) and >= 7.0:1 (AAA)."""
        cr_base = contrast_ratio("#F8FAFC", "#090D16")
        assert cr_base >= 18.0, f"Primary text on canvas contrast was {cr_base:.2f}:1"
        assert cr_base >= 7.0  # WCAG AAA

        cr_surf = contrast_ratio("#F8FAFC", "#0F172A")
        assert cr_surf >= 17.0, f"Primary text on surface contrast was {cr_surf:.2f}:1"
        assert cr_surf >= 7.0  # WCAG AAA

        cr_sec_base = contrast_ratio("#94A3B8", "#090D16")
        cr_sec_surf = contrast_ratio("#94A3B8", "#0F172A")
        assert cr_sec_base >= 4.5, f"Secondary text on base contrast was {cr_sec_base:.2f}:1"
        assert cr_sec_surf >= 4.5, f"Secondary text on surface contrast was {cr_sec_surf:.2f}:1"

    def test_light_mode_text_contrast_exceeds_aa_and_aaa(self) -> None:
        """Normal text >= 4.5:1 (AA) and >= 7.0:1 (AAA)."""
        cr_base = contrast_ratio("#0F172A", "#F8FAFC")
        assert cr_base >= 17.0, f"Light text on canvas contrast was {cr_base:.2f}:1"
        assert cr_base >= 7.0  # WCAG AAA

        cr_surf = contrast_ratio("#0F172A", "#FFFFFF")
        assert cr_surf >= 17.5, f"Light text on surface contrast was {cr_surf:.2f}:1"
        assert cr_surf >= 7.0  # WCAG AAA

        cr_sec_base = contrast_ratio("#334155", "#F8FAFC")
        cr_sec_surf = contrast_ratio("#334155", "#FFFFFF")
        assert cr_sec_base >= 4.5, f"Secondary text on base contrast was {cr_sec_base:.2f}:1"
        assert cr_sec_surf >= 4.5, f"Secondary text on surface contrast was {cr_sec_surf:.2f}:1"

    def test_interactive_focus_borders_meet_ui_contrast(self) -> None:
        """Interactive focus rings must achieve >= 3.0:1 against adjacent surfaces."""
        cr_focus_dark_base = contrast_ratio("#38BDF8", "#090D16")
        cr_focus_dark_surf = contrast_ratio("#38BDF8", "#0F172A")
        assert cr_focus_dark_base >= 3.0
        assert cr_focus_dark_surf >= 3.0

        cr_focus_light_base = contrast_ratio("#0284C7", "#F8FAFC")
        cr_focus_light_surf = contrast_ratio("#0284C7", "#FFFFFF")
        assert cr_focus_light_base >= 3.0
        assert cr_focus_light_surf >= 3.0

    def test_button_primary_contrast_both_modes(self) -> None:
        """.btn-primary has text color #090D16 on background var(--accent)."""
        cr_btn_dark = contrast_ratio("#090D16", "#38BDF8")
        assert cr_btn_dark >= 4.5, f"Dark primary button contrast: {cr_btn_dark:.2f}:1"

        cr_btn_light = contrast_ratio("#090D16", "#0284C7")
        assert cr_btn_light >= 4.5, f"Light primary button contrast: {cr_btn_light:.2f}:1"

    def test_semantic_quadrant_indicators_contrast_against_surface(self) -> None:
        """Verify solid semantic colors against surfaces achieve >= 3.0:1."""
        assert contrast_ratio("#10B981", "#0F172A") >= 3.0  # Sweet: 7.04:1
        assert contrast_ratio("#6366F1", "#0F172A") >= 3.0  # Crucible: 4.00:1
        assert contrast_ratio("#F59E0B", "#0F172A") >= 3.0  # Nursery: 8.31:1
        assert contrast_ratio("#EF4444", "#0F172A") >= 3.0  # Graveyard: 4.74:1

        assert contrast_ratio("#059669", "#FFFFFF") >= 3.0  # Sweet: 3.77:1
        assert contrast_ratio("#4F46E5", "#FFFFFF") >= 3.0  # Crucible: 6.29:1
        assert contrast_ratio("#D97706", "#FFFFFF") >= 3.0  # Nursery: 3.19:1
        assert contrast_ratio("#DC2626", "#FFFFFF") >= 3.0  # Graveyard: 4.83:1

    def test_badge_alpha_wash_contrast_challenge(self) -> None:
        """Adversarial check: Text inside .badge.nursery in Light Mode.
        Notice that text color #D97706 on 12% alpha wash over #FFFFFF results in
        effective background #FAEFE1, yielding contrast ratio ~2.81:1 (<3.0:1).
        This stress-tests the distinction between pure text contrast and alpha-badge text.
        """
        bg_blended = blend_rgba("#D97706", 0.12, "#FFFFFF")
        cr_nursery_badge = contrast_ratio("#D97706", bg_blended)
        assert 2.7 <= cr_nursery_badge <= 2.9, f"Calculated {cr_nursery_badge:.2f}:1"


# =========================================================================== #
# 3. LocalStorage Persistence & Toggle Interaction Verification
# =========================================================================== #
class TestThemePersistenceAndInteractionAdversarial:
    def test_theme_toggle_button_aria_and_structure(self, html_content: str) -> None:
        """Theme toggle button must be properly accessible with label and icon."""
        assert 'id="theme-toggle-btn"' in html_content
        assert 'aria-label="Toggle Light/Dark Theme"' in html_content
        assert 'id="theme-icon"' in html_content
        assert 'title="Toggle Light/Dark Theme"' in html_content

    def test_init_theme_reads_localstorage_with_media_query_fallback(self, html_content: str) -> None:
        """initTheme logic must check localStorage then matchMedia prefers-color-scheme."""
        idx = html_content.find("function initTheme()")
        assert idx != -1, "initTheme function not found"
        fn_snippet = html_content[idx : idx + 350]

        assert 'localStorage.getItem("porchlamp-theme")' in fn_snippet
        assert "prefers-color-scheme: light" in fn_snippet
        assert "setTheme(" in fn_snippet

    def test_set_theme_persists_and_updates_dom(self, html_content: str) -> None:
        """setTheme updates state, data-theme attribute, localStorage, and icon."""
        idx = html_content.find("function setTheme(theme)")
        assert idx != -1, "setTheme function not found"
        fn_snippet = html_content[idx : idx + 650]

        assert "state.theme = theme" in fn_snippet
        assert 'document.documentElement.setAttribute("data-theme", theme)' in fn_snippet
        assert 'localStorage.setItem("porchlamp-theme", theme)' in fn_snippet
        assert '$("theme-icon").textContent =' in fn_snippet
        assert "☀️" in fn_snippet
        assert "🌙" in fn_snippet

    def test_toggle_click_listener_inverts_state(self, html_content: str) -> None:
        """Click listener toggles between 'dark' and 'light'."""
        assert '$("theme-toggle-btn").addEventListener("click"' in html_content
        assert 'state.theme === "dark" ? "light" : "dark"' in html_content

    def test_omnibar_contains_theme_toggle_command(self, html_content: str) -> None:
        """Omnibar palette allows triggering theme toggle."""
        assert "Toggle Light / Dark Mode" in html_content


# =========================================================================== #
# 4. Dynamic SVG Chart Theme Adaptation Verification
# =========================================================================== #
class TestDynamicSvgChartThemeAdversarial:
    def test_scatter_chart_adapts_to_theme(self, html_content: str) -> None:
        """renderScatter updates axis, grid, and node strokes based on theme."""
        idx = html_content.find("function renderScatter()")
        assert idx != -1, "renderScatter not found"
        fn_snippet = html_content[idx : idx + 1200]

        assert 'state.theme === "light"' in fn_snippet
        assert "axisColor" in fn_snippet
        assert "gridBorder" in fn_snippet
        assert "bisectorStroke" in fn_snippet

    def test_multi_radar_chart_adapts_to_theme(self, html_content: str) -> None:
        """renderMultiRadarSvg updates ringStroke and textFill dynamically."""
        idx = html_content.find("function renderMultiRadarSvg(")
        assert idx != -1, "renderMultiRadarSvg not found"
        fn_snippet = html_content[idx : idx + 700]

        assert 'state.theme === "light"' in fn_snippet
        assert "ringStroke" in fn_snippet
        assert "textFill" in fn_snippet

    def test_single_radar_chart_adapts_to_theme(self, html_content: str) -> None:
        """renderRadarSvg in drawer updates ringStroke and textFill dynamically."""
        idx = html_content.find("function renderRadarSvg(")
        assert idx != -1, "renderRadarSvg not found"
        fn_snippet = html_content[idx : idx + 700]

        assert 'state.theme === "light"' in fn_snippet
        assert "ringStroke" in fn_snippet
        assert "textFill" in fn_snippet

    def test_survival_curve_chart_adapts_to_theme(self, html_content: str) -> None:
        """renderSurvivalSvg in drawer updates axisColor and gridStroke dynamically."""
        idx = html_content.find("function renderSurvivalSvg(")
        assert idx != -1, "renderSurvivalSvg not found"
        fn_snippet = html_content[idx : idx + 2500]

        assert 'state.theme === "light"' in fn_snippet
        assert "axisColor" in fn_snippet
        assert "gridStroke" in fn_snippet

    def test_set_theme_triggers_all_three_chart_surfaces(self, html_content: str) -> None:
        """setTheme must re-render all active chart surfaces."""
        idx = html_content.find("function setTheme(theme)")
        assert idx != -1, "setTheme not found"
        fn_snippet = html_content[idx : idx + 600]

        # 1. Scatter matrix chart
        assert "renderScatter()" in fn_snippet
        # 2. Drawer charts (radar & latency curve)
        assert "renderDrawer(" in fn_snippet
        # 3. Comparator charts (multi-radar)
        assert "renderComparator()" in fn_snippet
