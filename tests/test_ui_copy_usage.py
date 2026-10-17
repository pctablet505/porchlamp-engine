"""Every user-facing string lives in `web/app/copy.js` and is actually used.

06 §6: "copy.js (every user-facing sentence, ...); nothing user-facing is a
string literal elsewhere." The dispatch's acceptance test for this is
explicitly satisfiable as: "a test that parses copy.js's exported keys and
greps the tests for each key". Node is not available on this box (see
so there is no `web/app/tests/*.test.mjs` to
grep for usage yet; this test greps the actual UI SOURCE
(`web/app/{app.js,components,views}`) instead — the practical, available
substitute that still catches the real defect class (an orphaned string
nothing renders, or a hand-written literal bypassing copy.js entirely).
"""

from __future__ import annotations

import re
from pathlib import Path

WEB_APP = Path(__file__).resolve().parent.parent / "web" / "app"
COPY_JS = WEB_APP / "copy.js"

EXPORT_RE = re.compile(r"^export\s+(?:const|function)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)

#: Source files that are allowed to REFERENCE copy.js (everything except
#: copy.js itself and the test/perf-fixture files, which carry no
#: user-facing copy).
CONSUMER_GLOBS = ["app.js", "state.js", "components/*.js", "views/*.js"]


def exported_names() -> list[str]:
    text = COPY_JS.read_text()
    names = EXPORT_RE.findall(text)
    assert names, "no exported names found in copy.js — the parser regex is broken"
    return names


def consumer_source() -> str:
    chunks = []
    for pattern in CONSUMER_GLOBS:
        for path in WEB_APP.glob(pattern):
            chunks.append(path.read_text())
    return "\n".join(chunks)


class TestCopyExportsAreAllUsed:
    def test_every_exported_name_is_referenced_by_ui_source(self) -> None:
        source = consumer_source()
        unused = [name for name in exported_names() if f"copy.{name}" not in source]
        assert not unused, f"copy.js exports never referenced by any view/component: {unused}"

    def test_no_duplicate_exports(self) -> None:
        names = exported_names()
        assert len(names) == len(set(names)), "copy.js has a duplicate export name"

    def test_at_least_40_exports(self) -> None:
        # A loose floor: this packet's copy.js is a large, deliberately
        # centralized string table (headers, gate/unrated/persona reason
        # maps, tie-level labels, states, footer, keymap). A much smaller
        # count would mean strings leaked back out into the views.
        assert len(exported_names()) >= 40


class TestNoHandWrittenUserFacingStringsOutsideCopy:
    """A light heuristic, not exhaustive: every view/component file must
    IMPORT copy.js — a file rendering user-facing markup without it is a
    smell that strings were hand-written instead."""

    def test_every_view_and_component_imports_copy(self) -> None:
        offenders = []
        for pattern in ["components/*.js", "views/*.js"]:
            for path in WEB_APP.glob(pattern):
                if path.name == "patch.js":
                    continue  # a pure DOM-diffing utility, no copy of its own
                text = path.read_text()
                if 'from "../copy.js"' not in text:
                    offenders.append(str(path.relative_to(WEB_APP)))
        assert not offenders, f"files rendering markup without importing copy.js: {offenders}"
