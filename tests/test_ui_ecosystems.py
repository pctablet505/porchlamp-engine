"""The ecosystem filter offers exactly the ecosystems the engine ranks.

MEASURED defect (2026-10-12, live site): ``app.js`` hard-coded ``pypi, npm, cargo, go, maven``. The engine's
``Ecosystem`` is ``npm, pypi, crates, go, maven, systems``: "cargo" matched nothing, and the 483 ranked crates and 507
ranked systems repositories (30% of the board) could not be filtered at all.
"""

from __future__ import annotations

import re
from pathlib import Path

from porchlamp.models import Ecosystem
from porchlamp.serve import report_pages

WEB = Path(__file__).resolve().parent.parent / "web" / "app"


def _listed_options() -> list[tuple[str, str]]:
    """``[(value, label)]`` of ``ECOSYSTEMS``; a label written ``ECOSYSTEM_NAMES.<key>`` is resolved through the map."""
    source = (WEB / "copy.js").read_text(encoding="utf-8")
    block = re.search(r"export const ECOSYSTEMS\s*=\s*Object\.freeze\(\[(.*?)\]\);", source, re.DOTALL)
    assert block, "copy.js must export ECOSYSTEMS as Object.freeze([...])"
    names = report_pages.copy_js_map("ECOSYSTEM_NAMES")
    options = []
    for value, reference, literal in re.findall(r'\[\s*"([a-z]+)"\s*,\s*(?:ECOSYSTEM_NAMES\.([a-z]+)|"([^"]*)")\s*\]', block.group(1)):
        options.append((value, names[reference] if reference else literal))
    return options


def _listed_values() -> list[str]:
    return [value for value, _label in _listed_options()]


def test_the_filter_lists_every_engine_ecosystem_and_nothing_else():
    assert sorted(_listed_values()) == sorted(e.value for e in Ecosystem)


def test_each_filter_label_is_the_ecosystem_lists_plain_name():
    names = report_pages.copy_js_map("ECOSYSTEM_NAMES")
    assert sorted(names) == sorted(e.value for e in Ecosystem)
    assert names == {"crates": "Rust", "pypi": "Python", "npm": "JavaScript", "go": "Go", "maven": "Java", "systems": "C and C++"}
    assert dict(_listed_options()) == names


def test_no_script_hard_codes_an_ecosystem_list():
    for path in WEB.rglob("*.js"):
        if "tests" in path.parts:
            continue
        assert '"cargo"' not in path.read_text(encoding="utf-8"), f"{path.name} names the ecosystem 'cargo'"
