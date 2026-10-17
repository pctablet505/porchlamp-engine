"""``scripts/verify_static_export.py``'s check of the hand-written numbers in ``web/v2/index.html`` (the static
article, its head and the top-20 table), ``web/v2/llms.txt`` and ``web/v2/feed.xml``.

``tests/fixtures/shipped-text-snapshot-facts.json`` holds the facts of the snapshot those files name, as
``hand_written_facts(<snapshot database>, <stats file>)`` read them from that snapshot's database and stats file.
When the files move to a new snapshot, regenerate it the same way from the new snapshot's database.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("verify_static_export", ROOT / "scripts" / "verify_static_export.py")
verify_static_export = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verify_static_export)

SHIPPED = ROOT / "web" / "v2"
FACTS = json.loads((ROOT / "tests" / "fixtures" / "shipped-text-snapshot-facts.json").read_text(encoding="utf-8"))
FILES = ("index.html", "llms.txt", "feed.xml")
#: The two lines that call every unrated project a coverage shortfall, and a true wording of each.
COVERAGE_LINES = {
    "llms.txt": ("`1,377` **Unrated:** Repositories passing Stage-0 gates but with `<60%` measured parameter coverage.",
                 "`1,377` **Unrated:** Repositories passing Stage-0 gates but not ranked: `1,067` with `<60%` measured "
                 "parameter coverage, `302` with no outside pull-request workflow, `8` that review off GitHub."),
    "feed.xml": ("and 1,377 unrated (&lt;60% parameter coverage).",
                 "and 1,377 unrated (1,067 with &lt;60% parameter coverage, 302 with no outside pull-request workflow, "
                 "8 that review off GitHub)."),
}


def _site(tmp_path: Path, edits: tuple[tuple[str, str, str], ...] = ()) -> Path:
    """A copy of the three shipped files with ``(file, old, new)`` edits applied in order."""
    site = tmp_path / "site"
    site.mkdir()
    for name in FILES:
        shutil.copy(SHIPPED / name, site / name)
    for name, old, new in edits:
        text = (site / name).read_text(encoding="utf-8")
        assert text.count(old) == 1, (name, old)
        (site / name).write_text(text.replace(old, new), encoding="utf-8")
    return site


def test_the_shipped_files_agree_with_their_snapshot_except_the_two_coverage_lines(tmp_path):
    result = verify_static_export.check_hand_written(_site(tmp_path), FACTS)
    assert result["checked"] > 60
    assert sorted(error.split(":")[0] for error in result["errors"]) == ["feed.xml", "llms.txt"], result["errors"]
    for error in result["errors"]:
        assert "coverage below 60%" in error and "gives 1,377" in error and "1,067 projects" in error, error


def test_the_corrected_coverage_lines_pass(tmp_path):
    site = _site(tmp_path, CORRECTED)
    assert verify_static_export.check_hand_written(site, FACTS)["errors"] == []


CORRECTED = tuple((name, old, new) for name, (old, new) in COVERAGE_LINES.items())
PLANTS = {
    "home-top-20-score": ("index.html", "<td>81.16</td>", "<td>81.17</td>", "index.html top-20 table"),
    "llms-top-20-tier": ("llms.txt", "Score `81.16` · Coverage `65%` · Tier `B (capped)`",
                                  "Score `81.16` · Coverage `65%` · Tier `B`", "llms.txt top-20 list"),
    "home-judged-and-black-holes-swapped": (
        "index.html", "Of the 4,297 licensed, maintained software projects that received at least ten outside pull "
        "requests in six months, 1,133 (26.4%)", "Of the 1,133 licensed, maintained software projects that received at "
        "least ten outside pull requests in six months, 4,297 (26.4%)", "index.html: black-hole share"),
    "llms-star-bands-swapped": ("llms.txt", "`21%` at 1k\u201310k stars, `31%` at 10k\u201350k stars, and `39%` at 50k+",
                               "`39%` at 1k\u201310k stars, `31%` at 10k\u201350k stars, and `21%` at 50k+", "llms.txt: star bands"),
    "feed-lens-count": ("feed.xml", "(1,968 Ranked Repositories)", "(1,969 Ranked Repositories)", "feed.xml: first-timer lens"),
    "home-more-than-1-in-3-at-26.4%": ("index.html", "More than 1 in 4 active", "More than 1 in 3 active",
                                    "index.html: more than 1 in N"),
    "feed-constants-hash-prefix": ("feed.xml", "constants_hash 6e4f3e8e1d0c…", "constants_hash 6e4f3e8e1d0d…", "feed.xml: hashes"),
    "home-article-corpus": ("index.html", "We followed 212,390 pull requests", "We followed 212,930 pull requests",
                            "index.html: corpus"),
    "llms-statement-dropped": ("llms.txt", "Universe Breakdown (11,000 Total)", "Universe Breakdown",
                            "llms.txt: universe total: statement not found"),
}


@pytest.mark.parametrize("plant", sorted(PLANTS))
def test_a_planted_wrong_hand_written_number_fails_by_name(tmp_path, plant):
    """On the corrected copy (no other error), one planted defect gives exactly one error, naming it."""
    name, old, new, expected = PLANTS[plant]
    errors = verify_static_export.check_hand_written(_site(tmp_path, (*CORRECTED, (name, old, new))), FACTS)["errors"]
    assert len(errors) == 1 and errors[0].startswith(expected), errors


def test_every_statement_is_found_in_the_shipped_files():
    """No check is dead: each statement pattern matches the shipped text at least once."""
    import re

    for name in FILES:
        text = verify_static_export._flatten(name, (SHIPPED / name).read_text(encoding="utf-8"))
        for what, pattern, _expected in verify_static_export._hand_written_claims(name, FACTS):
            assert re.search(pattern, text), (name, what)
