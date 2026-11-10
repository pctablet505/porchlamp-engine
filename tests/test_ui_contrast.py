"""WCAG contrast test for `web/app/styles/tokens.css` (06 §5 checklist item 9).

Parses the actual CSS custom-property VALUES out of tokens.css (each themed
token holds both, `var(--on-light, <light>) var(--on-dark, <dark>)`, and
the two light rules, `:root[data-theme="light"]` and the
`prefers-color-scheme: light` one most light visitors get, turn the same
switches) and computes
real WCAG 2.1 relative-luminance contrast ratios — this is a test of the
DESIGN TOKENS, not of rendered pixels (a real browser is needed for that;
see the handoff's COULD NOT VERIFY list), but a wrong token value here is
exactly the kind of defect this test catches before it ever reaches a
screenshot.
"""

from __future__ import annotations

import re
from pathlib import Path

TOKENS_CSS = Path(__file__).resolve().parent.parent / "web" / "app" / "styles" / "tokens.css"

VAR_RE = re.compile(r"--([a-zA-Z0-9-]+)\s*:\s*([^;]+);")
#: A themed token as tokens.css writes it: its light value, then its dark value, each behind its theme's switch.
SWITCHED_RE = re.compile(r"var\(--on-light,\s*(.+?)\)\s+var\(--on-dark,\s*(.+)\)")
#: The two rules that give the light theme: a system that asks for light (unless the reader chose dark), and the
#: reader's own choice of light.
LIGHT_RULES = (':root:not([data-theme="dark"])', ':root[data-theme="light"]')


def extract_block(css: str, selector: str) -> str:
    """Returns the `{ ... }` body immediately following the first occurrence
    of `selector`, matching braces so nested rules don't truncate it early."""
    start = css.index(selector)
    brace_open = css.index("{", start)
    depth = 0
    for i in range(brace_open, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[brace_open + 1 : i]
    raise AssertionError(f"unbalanced braces after {selector!r}")


def parse_tokens(block: str) -> dict[str, str]:
    return {name: value.strip() for name, value in VAR_RE.findall(block)}


def resolve(value: str, tokens: dict[str, str]) -> str:
    """Resolves a `var(--x)` reference to its literal value (one level; no
    token in this file references another token more than one level deep)."""
    match = re.fullmatch(r"var\((--[a-zA-Z0-9-]+)\)", value.strip())
    if match:
        return tokens[match.group(1)[2:]]
    return value


def hex_to_rgb(value: str) -> tuple[float, float, float]:
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    r, g, b = (int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return r, g, b


def relative_luminance(hex_color: str) -> float:
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in hex_to_rgb(hex_color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    la, lb = relative_luminance(hex_a), relative_luminance(hex_b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def theme_tokens(css: str, side: int) -> dict[str, str]:
    """The bare `:root` block's tokens as one theme reads them: a themed token's light (1) or dark (2) value."""
    tokens = parse_tokens(extract_block(css, ":root {"))
    return {name: (m.group(side) if (m := SWITCHED_RE.fullmatch(value)) else value) for name, value in tokens.items()}


def load_themes() -> dict[str, dict[str, str]]:
    css = TOKENS_CSS.read_text()
    return {"dark": theme_tokens(css, 2), "light": theme_tokens(css, 1)}


THEMES = load_themes()

#: (text token, background token) pairs that render as actual readable
#: text (body copy, labels, values) — WCAG AA normal text needs >= 4.5:1.
TEXT_PAIRS = [
    ("text-primary", "bg-base"),
    ("text-primary", "bg-surface"),
    ("text-primary", "bg-surface-elevated"),
    ("text-secondary", "bg-base"),
    ("text-secondary", "bg-surface"),
    ("text-secondary", "bg-surface-elevated"),
    ("text-muted", "bg-base"),
    ("text-muted", "bg-surface"),
    ("text-muted", "bg-surface-elevated"),
]

#: Non-text graphical tokens (quadrant glyphs, focus ring) only need >= 3:1
#: against the surfaces they actually render on (WCAG 2.1 SC 1.4.11).
GRAPHIC_PAIRS = [
    ("text-muted", "bg-surface"),
    ("quad-sweet-spot", "bg-base"),
    ("quad-sweet-spot", "bg-surface"),
    ("quad-sweet-spot", "bg-surface-elevated"),
    ("quad-crucible", "bg-base"),
    ("quad-crucible", "bg-surface"),
    ("quad-crucible", "bg-surface-elevated"),
    ("quad-nursery", "bg-base"),
    ("quad-nursery", "bg-surface"),
    ("quad-nursery", "bg-surface-elevated"),
    ("quad-graveyard", "bg-base"),
    ("quad-graveyard", "bg-surface"),
    ("quad-graveyard", "bg-surface-elevated"),
    ("border-focus", "bg-surface"),
    # Screen F (redesign 2026-09-22): pillar-weight bars and the change log's
    # "Current" ring/dot, both drawn on the section card (--bg-surface).
    ("spec-bar", "bg-surface"),
    ("coverage-full", "bg-surface"),
]


#: Colours that paint text on the surfaces below: link colour (`a`) and the
#: quadrant labels (`.quad-*`). WCAG AA normal text needs >= 4.5:1.
LINK_AND_QUADRANT_TEXT_TOKENS = [
    "accent-cyan",
    "quad-sweet-spot",
    "quad-crucible",
    "quad-nursery",
    "quad-graveyard",
]
#: Includes the hovered and selected row surface, which these colours also paint on.
TEXT_SURFACES = ["bg-base", "bg-surface", "bg-surface-elevated", "bg-surface-hover"]
LIGHT_THEMES = ("light",)


def _get(theme: dict[str, str], name: str) -> str:
    return resolve(theme[name], theme)


class TestTokensParse:
    def test_both_themes_have_every_referenced_token(self) -> None:
        for theme_name, theme in THEMES.items():
            for a, b in TEXT_PAIRS + GRAPHIC_PAIRS:
                assert a in theme, f"{theme_name} theme missing --{a}"
                assert b in theme, f"{theme_name} theme missing --{b}"

    def test_hex_values_resolve(self) -> None:
        for theme in THEMES.values():
            assert _get(theme, "text-primary").startswith("#")
            assert _get(theme, "bg-base").startswith("#")


class TestTextContrastMeetsAA:
    @staticmethod
    def _cases():
        for theme_name, theme in THEMES.items():
            for text_tok, bg_tok in TEXT_PAIRS:
                yield theme_name, theme, text_tok, bg_tok

    def test_every_text_pair_clears_4_5_to_1(self) -> None:
        failures = []
        for theme_name, theme, text_tok, bg_tok in self._cases():
            ratio = contrast_ratio(_get(theme, text_tok), _get(theme, bg_tok))
            if ratio < 4.5:
                failures.append(f"{theme_name}: --{text_tok} on --{bg_tok} = {ratio:.2f} (< 4.5)")
        assert not failures, "AA text-contrast failures:\n" + "\n".join(failures)

    def test_4_5_is_a_real_load_bearing_threshold_not_a_number_nothing_ever_fails(self) -> None:
        # Mutation-battery follow-up (M16, survived): every REAL token pair
        # in tokens.css clears 4.5:1 with enough margin that weakening the
        # threshold to 2.0 changed no test outcome. This pins the boundary
        # itself with a synthetic pair whose ratio is independently known
        # to sit strictly between 2.0 and 4.5, so a future weakening of
        # either the threshold OR contrast_ratio's own math is caught here
        # even though no real design token happens to be marginal today.
        synthetic_ratio = contrast_ratio("#949494", "#FFFFFF")
        assert 2.0 < synthetic_ratio < 4.5, f"test fixture assumption broke: ratio was {synthetic_ratio}"
        assert not (synthetic_ratio >= 4.5), "a pair below AA must not be reported as passing"


class TestLinkAndQuadrantTextContrast:
    def test_link_and_quadrant_text_clear_4_5_to_1_in_the_light_theme(self) -> None:
        failures = []
        for theme_name in LIGHT_THEMES:
            theme = THEMES[theme_name]
            for fg_tok in LINK_AND_QUADRANT_TEXT_TOKENS:
                for bg_tok in TEXT_SURFACES:
                    ratio = contrast_ratio(_get(theme, fg_tok), _get(theme, bg_tok))
                    if ratio < 4.5:
                        failures.append(f"{theme_name}: --{fg_tok} on --{bg_tok} = {ratio:.2f} (< 4.5)")
        assert not failures, "AA link/quadrant text failures:\n" + "\n".join(failures)

    def test_both_light_rules_turn_the_same_switches_and_nothing_else(self) -> None:
        """The light theme is one palette: the system's light and the reader's light each turn the switches, so
        neither can hold a value of its own."""
        css = TOKENS_CSS.read_text()
        for selector in LIGHT_RULES:
            body = extract_block(css, selector)
            assert parse_tokens(body) == {"on-light": "initial", "on-dark": ""}, selector
            assert re.fullmatch(r"\s*(--on-(light|dark):[^;]*;\s*|color-scheme:\s*light;\s*){3}", body), selector

    def test_light_text_tokens_are_overridden_not_inherited_from_dark(self) -> None:
        for theme_name in LIGHT_THEMES:
            for tok in LINK_AND_QUADRANT_TEXT_TOKENS:
                assert THEMES[theme_name][tok] != THEMES["dark"][tok], f"--{tok} has no light value of its own"


class TestGraphicContrastMeets3to1:
    def test_every_graphic_pair_clears_3_to_1(self) -> None:
        failures = []
        for theme_name, theme in THEMES.items():
            for fg_tok, bg_tok in GRAPHIC_PAIRS:
                ratio = contrast_ratio(_get(theme, fg_tok), _get(theme, bg_tok))
                if ratio < 3.0:
                    failures.append(f"{theme_name}: --{fg_tok} on --{bg_tok} = {ratio:.2f} (< 3.0)")
        assert not failures, "3:1 graphic-contrast failures:\n" + "\n".join(failures)


class TestDarkIsTheDefault:
    def test_bare_root_has_no_data_theme_guard(self) -> None:
        # Dark-mode-first (docs/UI_UX_SPECIFICATION.md §3): the unconditional
        # `:root` block must be the dark palette, never behind a media query
        # or attribute selector.
        css = TOKENS_CSS.read_text()
        tokens = parse_tokens(extract_block(css, ":root {"))
        assert (tokens["on-light"], tokens["on-dark"]) == ("", "initial"), "bare :root sets the dark switch"
        assert THEMES["dark"]["bg-base"].lower() == "#090d16"


COMPONENTS_CSS = TOKENS_CSS.parent / "components.css"


def test_a_controls_placeholder_text_passes_aa_on_the_controls_background() -> None:
    """The search box's hint is text a reader must read. The browser's own grey placeholder is under AA on the control
    (measured in Chrome: 4.21:1 light, 3.17:1 dark), so components.css gives it a token colour, unfaded, that clears
    4.5:1 on the control's own background in every theme."""
    css = COMPONENTS_CSS.read_text(encoding="utf-8")
    rule = re.search(r"input\.control::placeholder\s*\{([^}]*)\}", css)
    assert rule, "input.control has no placeholder colour: the browser's default grey fails AA"
    color = re.search(r"(?<![-\w])color:\s*var\(--([a-z0-9-]+)\)", rule.group(1)).group(1)
    assert re.search(r"opacity:\s*1\s*;", rule.group(1)), "a browser that fades a placeholder must not fade this one"
    control = re.search(r"select\.control,\s*input\.control\s*\{([^}]*)\}", css).group(1)
    background = re.search(r"background:\s*var\(--([a-z0-9-]+)\)", control).group(1)
    for theme_name, theme in THEMES.items():
        ratio = contrast_ratio(_get(theme, color), _get(theme, background))
        assert ratio >= 4.5, f"{theme_name}: the placeholder's --{color} on --{background} = {ratio:.2f} (< 4.5)"
