"""The ecosystem filter offers exactly the ecosystems the engine ranks.

MEASURED defect (2026-10-12, live site): ``app.js`` hard-coded ``pypi, npm, cargo, go, maven``. The engine's
``Ecosystem`` is ``npm, pypi, crates, go, maven, systems``: "cargo" matched nothing, and the 483 ranked crates and 507
ranked systems repositories (30% of the board) could not be filtered at all.
"""

from __future__ import annotations

import re
from pathlib import Path

from porchlamp.models import Ecosystem

WEB = Path(__file__).resolve().parent.parent / "web" / "v2"


def _listed_values() -> list[str]:
    source = (WEB / "copy.js").read_text(encoding="utf-8")
    block = re.search(r"export const ECOSYSTEMS\s*=\s*Object\.freeze\(\[(.*?)\]\);", source, re.DOTALL)
    assert block, "copy.js must export ECOSYSTEMS as Object.freeze([...])"
    return re.findall(r'\[\s*"([a-z]+)"\s*,', block.group(1))


def test_the_filter_lists_every_engine_ecosystem_and_nothing_else():
    assert sorted(_listed_values()) == sorted(e.value for e in Ecosystem)


def test_no_script_hard_codes_an_ecosystem_list():
    for path in WEB.rglob("*.js"):
        if "tests" in path.parts:
            continue
        assert '"cargo"' not in path.read_text(encoding="utf-8"), f"{path.name} names the ecosystem 'cargo'"
