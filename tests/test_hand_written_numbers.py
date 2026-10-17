"""``scripts/verify_static_export.py``'s check of the hand-written numbers in ``web/app/index.html`` (the static
article, its head and the top-20 table), ``web/app/llms.txt``, ``web/app/feed.xml`` and the app's finding line in
``web/app/copy.js``.

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

SHIPPED = ROOT / "web" / "app"
FACTS = json.loads((ROOT / "tests" / "fixtures" / "shipped-text-snapshot-facts.json").read_text(encoding="utf-8"))
FILES = verify_static_export.HAND_WRITTEN_FILES
_N = lambda n: f"{n:,}"  # noqa: E731
_UNRATED = _N(FACTS["unrated"])
_REASONS = FACTS["unrated_by_reason"]
_BELOW, _NOEXT, _OFFP = (_N(_REASONS.get(k, 0)) for k in (
    "unrated: insufficient_evidence", "unrated: no_external_workflow", "unrated: off_platform_routed"))
#: The coverage wording the shipped files used to have: every unrated project called a coverage shortfall.
FALSE_COVERAGE_LINES = (
    ("llms.txt", f"`{_UNRATED}` **Unrated:** Repositories passing Stage-0 gates but not ranked: `{_BELOW}` with `<60%` "
                 f"measured parameter coverage, `{_NOEXT}` with no outside pull-request workflow, `{_OFFP}` that review off GitHub.",
     f"`{_UNRATED}` **Unrated:** Repositories passing Stage-0 gates but with `<60%` measured parameter coverage."),
    ("feed.xml", f"and {_UNRATED} unrated ({_BELOW} with &lt;60% parameter coverage, {_NOEXT} with no outside "
                 f"pull-request workflow, {_OFFP} that review off GitHub).",
     f"and {_UNRATED} unrated (&lt;60% parameter coverage)."),
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
        assert "coverage below 60%" in error and f"gives {_UNRATED}" in error and f"{_BELOW} projects" in error, error


_TOP = FACTS["top"][0]  # rank, repo, ecosystem, score, coverage, tier, quadrant
_JUDGED, _HOLES, _SHARE = _N(FACTS["judged"]), _N(FACTS["black_holes"]), FACTS["share"]
_B1, _B2, _B3 = FACTS["band_percents"]
_PRS = _N(FACTS["corpus"]["human_outside_prs"])
_FIRST = FACTS["lens_ranked"]["first_timer"]
_ONE_IN = FACTS["judged"] // FACTS["black_holes"] + 1
_TOP_BAND_ONE_IN = FACTS["top_band"]["n"] // FACTS["top_band"]["black_holes"] + 1


def _off_by_a_cent(score: str) -> str:
    return f"{float(score) + 0.01:.2f}"


#: name: (file, the shipped text, the planted text, the start of the one error it must give)
PLANTS = {
    "home-top-20-score": ("index.html", f"<td>{_TOP[3]}</td>", f"<td>{_off_by_a_cent(_TOP[3])}</td>",
                          "index.html top-20 table"),
    "llms-top-20-tier": ("llms.txt", f"Score `{_TOP[3]}` · Coverage `{_TOP[4]}` · Tier `{_TOP[5]}`",
                         f"Score `{_TOP[3]}` · Coverage `{_TOP[4]}` · Tier `{_TOP[5]}x`", "llms.txt top-20 list"),
    "home-judged-and-black-holes-swapped": (
        "index.html", f"Of the {_JUDGED} licensed, maintained software projects that received at least ten outside pull "
        f"requests in six months, {_HOLES} ({_SHARE}%)", f"Of the {_HOLES} licensed, maintained software projects that "
        f"received at least ten outside pull requests in six months, {_JUDGED} ({_SHARE}%)", "index.html: black-hole share"),
    "llms-star-bands-swapped": ("llms.txt", f"`{_B1}%` at 1k\u201310k stars, `{_B2}%` at 10k\u201350k stars, and `{_B3}%` at 50k+",
                                f"`{_B3}%` at 1k\u201310k stars, `{_B2}%` at 10k\u201350k stars, and `{_B1}%` at 50k+",
                                "llms.txt: star bands"),
    "feed-lens-count": ("feed.xml", f"({_N(_FIRST)} Ranked Repositories)", f"({_N(_FIRST + 1)} Ranked Repositories)",
                        "feed.xml: first-timer lens"),
    "home-more-than-1-in-n-one-too-small": ("index.html", f"More than 1 in {_ONE_IN} active",
                                            f"More than 1 in {_ONE_IN - 1} active", "index.html: more than 1 in N"),
    "home-most-starred-one-too-small": ("index.html", f"more than 1 in {_TOP_BAND_ONE_IN} of the most-starred",
                                        f"more than 1 in {_TOP_BAND_ONE_IN - 1} of the most-starred",
                                        "index.html: most-starred band, more than 1 in N"),
    "app-finding-line-one-too-small": ("copy.js", f'FINDINGS_LINE = "More than 1 in {_ONE_IN} active',
                                       f'FINDINGS_LINE = "More than 1 in {_ONE_IN - 1} active', "copy.js: more than 1 in N"),
    "feed-constants-hash-prefix": ("feed.xml", f"constants_hash {FACTS['constants_hash'][:12]}…",
                                   f"constants_hash {FACTS['constants_hash'][:11]}{'0' if FACTS['constants_hash'][11] != '0' else '1'}…",
                                   "feed.xml: hashes"),
    "home-article-corpus": ("index.html", f"We followed {_PRS} pull requests",
                            f"We followed {_N(FACTS['corpus']['human_outside_prs'] + 540)} pull requests", "index.html: corpus"),
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
