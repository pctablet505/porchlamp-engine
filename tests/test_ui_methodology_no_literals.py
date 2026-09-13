"""Screen F must never hardcode a methodology fact (06 §3 F: "every number
comes from the payload") — `views/methodology.js`'s own module docstring
states the rule; this test enforces it by finding every genuine numeric
LITERAL TOKEN in the file's live code (comments, string/template STATIC
text, and the shared `escapeHtml` boilerplate excluded) and asserting none
remain.

Distinguishing a real numeric literal from an incidental digit character
(an `h1` tag name, a `06 §3 F` doc citation in a comment, the `&#39;`
HTML-entity string inside `escapeHtml`) needs more than a bare `\\d+`
search — see `extract_numeric_literals`'s docstring for exactly what is
stripped before scanning, and `TestDetectorItself` for a synthetic
positive/negative control proving the detector actually detects an
injected literal and does not just always pass on this particular file
(the "synthetic guard tests the fixture" failure mode).
"""

from __future__ import annotations

import re
from pathlib import Path

METHODOLOGY_JS = Path(__file__).resolve().parent.parent / "web" / "v2" / "views" / "methodology.js"

#: A numeric literal token: a run of digits (optionally with one decimal
#: point) NOT immediately preceded by an identifier character (letter,
#: digit, `_`, `$`) or a `.` (so `h1`'s "1", or a property-access chain,
#: never match) — JS identifiers cannot start with a digit, but CAN
#: contain one after the first character (`h1`, `td2`), so excluding any
#: digit run glued to a preceding identifier character is exactly the
#: right boundary.
_NUMERIC_LITERAL_RE = re.compile(r"(?<![A-Za-z0-9_$.])\d+(?:\.\d+)?")

_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_ESCAPE_HTML_FN_RE = re.compile(r"function escapeHtml\(value\)\s*\{.*?\n\}\n", re.DOTALL)
#: Splits a template literal into its STATIC text and `${...}` expression
#: parts (non-greedy; this file nests no `${` inside another `${`).
_TEMPLATE_EXPR_RE = re.compile(r"\$\{([^{}]*)\}")


def strip_comments(source: str) -> str:
    source = _BLOCK_COMMENT_RE.sub("", source)
    source = _LINE_COMMENT_RE.sub("", source)
    return source


def strip_escape_html_boilerplate(source: str) -> str:
    # The identical `escapeHtml` helper (with its `&#39;` HTML-entity
    # string) is copy-pasted verbatim across every web/v2/ file — a
    # utility, never a methodology fact; excluded once, by name, rather
    # than papering over it with a broader "ignore short strings" rule.
    return _ESCAPE_HTML_FN_RE.sub("", source)


def template_literal_expressions_only(source: str) -> str:
    """Keeps ONLY the `${...}` expression contents of every backtick
    template literal in `source` (dropping the static HTML text between
    them, which is markup, never a live numeric literal) plus every plain
    single/double-quoted string is simply dropped whole (this file uses
    none for anything but object keys, which this test does not need)."""
    out_parts = []
    i = 0
    while i < len(source):
        ch = source[i]
        if ch == "`":
            end = source.index("`", i + 1)
            template_body = source[i + 1 : end]
            for match in _TEMPLATE_EXPR_RE.finditer(template_body):
                out_parts.append(match.group(1))
            i = end + 1
        elif ch in "'\"":
            end = source.index(ch, i + 1)
            i = end + 1
        else:
            out_parts.append(ch)
            i += 1
    return "".join(out_parts)


def extract_numeric_literals(source: str) -> list[str]:
    cleaned = strip_comments(source)
    cleaned = strip_escape_html_boilerplate(cleaned)
    cleaned = template_literal_expressions_only(cleaned)
    return _NUMERIC_LITERAL_RE.findall(cleaned)


class TestMethodologyViewHasNoNumericLiterals:
    def test_no_numeric_literal_in_methodology_js(self) -> None:
        source = METHODOLOGY_JS.read_text(encoding="utf-8")
        found = extract_numeric_literals(source)
        assert not found, (
            f"views/methodology.js contains numeric literal(s) {found} — "
            "every number on Screen F must come from the methodology/snapshot "
            "payload via a copy.methodology* formatter, never a hardcoded value "
            "(06 §3 F)."
        )


class TestDetectorItself:
    """Proves the detector actually detects, and does not merely always
    pass on this one file (synthetic guards must test the fixture)."""

    def test_detects_a_bare_literal_in_live_code(self) -> None:
        snippet = "export function f(x) { return x >= 90.0 ? 'S+' : 'D'; }"
        assert extract_numeric_literals(snippet) == ["90.0"]

    def test_detects_a_literal_inside_a_template_expression(self) -> None:
        snippet = "const s = `value: ${x + 12}`;"
        assert extract_numeric_literals(snippet) == ["12"]

    def test_ignores_a_doc_comment_citation(self) -> None:
        snippet = "// 06 §3 F: every number comes from the payload\nexport const x = 1;"
        # The comment's "06"/"3" are stripped; the real `= 1` literal remains.
        assert extract_numeric_literals(snippet) == ["1"]

    def test_ignores_digits_glued_to_an_identifier(self) -> None:
        snippet = "const el = `<h1 class=\"x\">${copy.HEADING}</h1>`;"
        assert extract_numeric_literals(snippet) == []

    def test_ignores_html_entity_strings_inside_escape_html_boilerplate(self) -> None:
        snippet = (
            "function escapeHtml(value) {\n"
            "  return String(value).replace(/[&<>\"']/g, (ch) => (\n"
            '    { "&": "&amp;", "<": "&lt;", ">": "&gt;", \'"\': "&quot;", "\'": "&#39;" }[ch]\n'
            "  ));\n"
            "}\n"
        )
        assert extract_numeric_literals(snippet) == []

    def test_ignores_static_template_text_outside_expressions(self) -> None:
        snippet = "const s = `<td class=\"cell-numeric\">${copy.x(v)}</td>`;"
        assert extract_numeric_literals(snippet) == []
