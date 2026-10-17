"""``scripts/verify_static_export.py``'s check of the hand-written numbers in ``web/v2/index.html`` (the static
article, its head and the top-20 table), ``web/v2/llms.txt``, ``web/v2/feed.xml`` and the app's finding line in
``web/v2/copy.js``.

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
FILES = verify_static_export.HAND_WRITTEN_FILES
#: The coverage wording the shipped files used to have: every unrated project called a coverage shortfall.
FALSE_COVERAGE_LINES = (
    ("llms.txt", "`1,438` **Unrated:** Repositories passing Stage-0 gates but not ranked: `1,113` with `<60%` measured "
                 "parameter coverage, `317` with no outside pull-request workflow, `8` that review off GitHub.",
     "`1,438` **Unrated:** Repositories passing Stage-0 gates but with `<60%` measured parameter coverage."),
    ("feed.xml", "and 1,438 unrated (1,113 with &lt;60% parameter coverage, 317 with no outside pull-request workflow, "
                 "8 that review off GitHub).",
     "and 1,438 unrated (&lt;60% parameter coverage)."),
)


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


def test_the_shipped_files_agree_with_their_snapshot(tmp_path):
    result = verify_static_export.check_hand_written(_site(tmp_path), FACTS)
    assert result["checked"] > 60
    assert result["errors"] == []


def test_a_line_that_calls_every_unrated_project_a_coverage_shortfall_fails(tmp_path):
    result = verify_static_export.check_hand_written(_site(tmp_path, FALSE_COVERAGE_LINES), FACTS)
    assert sorted(error.split(":")[0] for error in result["errors"]) == ["feed.xml", "llms.txt"], result["errors"]
    for error in result["errors"]:
        assert "coverage below 60%" in error and "gives 1,438" in error and "1,113 projects" in error, error


PLANTS = {
    "home-top-20-score": ("index.html", "<td>81.65</td>", "<td>81.66</td>", "index.html top-20 table"),
    "llms-top-20-tier": ("llms.txt", "Score `81.65` · Coverage `61%` · Tier `B (capped)`",
                                  "Score `81.65` · Coverage `61%` · Tier `B`", "llms.txt top-20 list"),
    "home-judged-and-black-holes-swapped": (
        "index.html", "Of the 4,042 licensed, maintained software projects that received at least ten outside pull "
        "requests in six months, 720 (17.8%)", "Of the 720 licensed, maintained software projects that received at "
        "least ten outside pull requests in six months, 4,042 (17.8%)", "index.html: black-hole share"),
    "llms-star-bands-swapped": ("llms.txt", "`16%` at 1k\u201310k stars, `19%` at 10k\u201350k stars, and `28%` at 50k+",
                               "`28%` at 1k\u201310k stars, `19%` at 10k\u201350k stars, and `16%` at 50k+", "llms.txt: star bands"),
    "feed-lens-count": ("feed.xml", "(2,095 Ranked Repositories)", "(2,096 Ranked Repositories)", "feed.xml: first-timer lens"),
    "home-more-than-1-in-5-at-17.8%": ("index.html", "More than 1 in 6 active", "More than 1 in 5 active",
                                     "index.html: more than 1 in N"),
    "home-most-starred-more-than-1-in-3-at-28.2%": ("index.html", "more than 1 in 4 of the most-starred",
                                                  "more than 1 in 3 of the most-starred",
                                                  "index.html: most-starred band, more than 1 in N"),
    "app-finding-line-more-than-1-in-5": ("copy.js", 'FINDINGS_LINE = "More than 1 in 6 active',
                                          'FINDINGS_LINE = "More than 1 in 5 active', "copy.js: more than 1 in N"),
    "feed-constants-hash-prefix": ("feed.xml", "constants_hash 6e4f3e8e1d0c…", "constants_hash 6e4f3e8e1d0d…", "feed.xml: hashes"),
    "home-article-corpus": ("index.html", "We followed 212,390 pull requests", "We followed 212,930 pull requests",
                            "index.html: corpus"),
    "llms-statement-dropped": ("llms.txt", "Universe Breakdown (11,000 Total)", "Universe Breakdown",
                            "llms.txt: universe total: statement not found"),
}


@pytest.mark.parametrize("plant", sorted(PLANTS))
def test_a_planted_wrong_hand_written_number_fails_by_name(tmp_path, plant):
    """On the shipped files (no other error), one planted defect gives exactly one error, naming it."""
    name, old, new, expected = PLANTS[plant]
    errors = verify_static_export.check_hand_written(_site(tmp_path, ((name, old, new),)), FACTS)["errors"]
    assert len(errors) == 1 and errors[0].startswith(expected), errors


def test_every_statement_is_found_in_the_shipped_files():
    """No check is dead: each statement pattern matches the shipped text at least once."""
    import re

    for name in FILES:
        text = verify_static_export._flatten(name, (SHIPPED / name).read_text(encoding="utf-8"))
        for what, pattern, _expected in verify_static_export._hand_written_claims(name, FACTS):
            assert re.search(pattern, text), (name, what)
