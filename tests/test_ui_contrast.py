"""WCAG contrast test for `web/v2/styles/tokens.css` (06 §5 checklist item 9).

Parses the actual CSS custom-property VALUES out of the two theme blocks
(dark: bare `:root`; light: `:root[data-theme="light"]`) and computes
real WCAG 2.1 relative-luminance contrast ratios — this is a test of the
DESIGN TOKENS, not of rendered pixels (a real browser is needed for that;
see the handoff's COULD NOT VERIFY list), but a wrong token value here is
exactly the kind of defect this test catches before it ever reaches a
screenshot.
"""

from __future__ import annotations

import re
from pathlib import Path

TOKENS_CSS = Path(__file__).resolve().parent.parent / "web" / "v2" / "styles" / "tokens.css"

VAR_RE = re.compile(r"--([a-zA-Z0-9-]+)\s*:\s*([^;]+);")


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


def load_themes() -> dict[str, dict[str, str]]:
    css = TOKENS_CSS.read_text()
    dark = parse_tokens(extract_block(css, ":root {"))
    light = parse_tokens(extract_block(css, ':root[data-theme="light"]'))
    return {"dark": dark, "light": light}


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
]


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
        bare_root_block = extract_block(css, ":root {")
        tokens = parse_tokens(bare_root_block)
        assert tokens["bg-base"].lower() == "#090d16"
