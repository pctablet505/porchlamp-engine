"""No rule that targets a table cell may lower that cell's min-content width.

MEASURED defect (2026-09-22, Chrome, 1440 px): ``.evidence-table
td:first-child { word-break: break-word }`` -- with every other cell
``white-space: nowrap`` -- let the auto table layout squeeze the first column
to ~21 px, stacking identifiers one character per line. On Screen F that was
the parameters, gates and change-log tables (rows 132-163 px tall; tables
1,545-3,079 px wide); in the repository drawer, every evidence table (cells
up to 255 px tall, "Decision Rate (90d)" one letter per line).

``word-break: break-all | break-word`` and ``overflow-wrap: anywhere`` all
make every character a soft-wrap opportunity *for min-content sizing*, which
is exactly what an auto-layout table shrinks a column to. ``overflow-wrap:
break-word`` still breaks a word that would overflow, but does not count
those breaks toward min-content, so it cannot collapse a column.

Scope, stated honestly: this is a static check of the stylesheets. It
flags a collapsing declaration on any selector that NAMES a cell anywhere --
the cell itself (``td``/``th``, or one of Screen F's cell-role classes) or
something inside one (``.cell-prose code``): a descendant's min-content is
part of its cell's. It cannot see layout, so it does not police rules that
name no cell at all (``.app-main code``, ``.cell-numeric``'s app-wide
``break-all``) even though they can reach into a cell. The layout itself was
verified in a browser at 390-1920 px.
"""

from __future__ import annotations

import re
from pathlib import Path

STYLES = Path(__file__).resolve().parent.parent / "web" / "app" / "styles"

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
#: An innermost rule block: a selector list and a declaration body with no
#: nested braces (so rules inside @media blocks are found too).
_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_CELL_TYPE_RE = re.compile(r"(?<![\w-])(td|th)(?![\w-])")
#: Screen F's cell-role classes (views/methodology.js) -- always on a td/th.
_CELL_ROLE_RE = re.compile(r"\.(cell-id|cell-prose|cell-meta)(?![\w-])|\.spec-table[^,]*\s\.num(?![\w-])")
_COLLAPSING_DECL_RE = re.compile(
    r"(?:^|;)\s*(word-break\s*:\s*(?:break-all|break-word)|overflow-wrap\s*:\s*anywhere|word-wrap\s*:\s*anywhere)\s*(?:;|$)",
    re.IGNORECASE,
)


def targets_table_cell(selector: str) -> bool:
    """True when the selector names a table cell anywhere: the styled element
    is a cell, or sits inside one (its min-content is part of the cell's)."""
    return bool(_CELL_TYPE_RE.search(selector) or _CELL_ROLE_RE.search(selector))


def collapsing_rules(css: str) -> list[tuple[str, str]]:
    """(selector, declaration) pairs where a cell-targeting rule lowers min-content."""
    css = _COMMENT_RE.sub("", css)
    found = []
    for selectors, body in _RULE_RE.findall(css):
        if selectors.strip().startswith("@"):
            continue
        cell_selectors = [s.strip() for s in selectors.split(",") if targets_table_cell(s)]
        if not cell_selectors:
            continue
        for match in _COLLAPSING_DECL_RE.finditer(body):
            for sel in cell_selectors:
                found.append((sel, match.group(1)))
    return found


class TestStylesheetsNeverCollapseATableColumn:
    def test_no_cell_rule_lowers_min_content(self) -> None:
        offenders = []
        for path in sorted(STYLES.glob("*.css")):
            for selector, decl in collapsing_rules(path.read_text(encoding="utf-8")):
                offenders.append(f"{path.name}: `{selector}` has `{decl}`")
        assert not offenders, (
            "a table cell must wrap with `overflow-wrap: break-word`, never a rule that "
            "lowers min-content (it lets the auto table layout squeeze the column to one "
            "character):\n" + "\n".join(offenders)
        )

    def test_the_scan_actually_reads_the_real_stylesheets(self) -> None:
        names = {p.name for p in STYLES.glob("*.css")}
        assert {"components.css", "views.css"} <= names
        # The project view's first-column cell rule, where the drawer's parameter table (this defect's home) now lives,
        # is a cell rule the scan sees.
        css = _COMMENT_RE.sub("", (STYLES / "views.css").read_text(encoding="utf-8"))
        assert any(
            targets_table_cell(s) for sels, _ in _RULE_RE.findall(css) for s in sels.split(",") if "project-table td:first-child" in s
        )


class TestDetectorItself:
    """Synthetic controls: the check must flag the real defect's exact shape and
    pass the fix, or it proves nothing (a detector that never fires passes)."""

    def test_flags_the_measured_defect(self) -> None:
        css = ".evidence-table td:first-child,\n.evidence-table th:first-child {\n  white-space: normal;\n  max-width: 120px;\n  word-break: break-word;\n}\n"
        assert len(collapsing_rules(css)) == 2

    def test_flags_break_all_and_anywhere_inside_media_queries(self) -> None:
        css = "@media (max-width: 600px) {\n  .t td { word-break: break-all; }\n  .spec-table .cell-prose { overflow-wrap: anywhere; }\n}\n"
        assert {sel for sel, _ in collapsing_rules(css)} == {".t td", ".spec-table .cell-prose"}

    def test_passes_the_fix(self) -> None:
        css = ".evidence-table td:first-child { white-space: normal; overflow-wrap: break-word; }\n"
        assert collapsing_rules(css) == []

    def test_flags_a_collapsing_rule_on_something_inside_a_cell(self) -> None:
        # A descendant's min-content is part of its cell's: break-all on the
        # <code> in a prose cell collapses that column as surely as on the cell.
        css = ".spec-table .cell-prose code { word-break: break-all; }\n.x td span { overflow-wrap: anywhere; }\n"
        assert {sel for sel, _ in collapsing_rules(css)} == {".spec-table .cell-prose code", ".x td span"}

    def test_ignores_rules_that_name_no_cell(self) -> None:
        css = (
            ".app-main code, .app-main pre { word-break: break-all; }\n"
            ".identity-value code { overflow-wrap: anywhere; }\n"
            ".thin-table-note { word-break: break-all; }\n"
            ".width-td { word-break: break-all; }\n"
        )
        assert collapsing_rules(css) == []
