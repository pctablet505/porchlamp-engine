"""``scripts/verify_static_export.py``'s check of the first-pull-requests report, on a small site drawn by the real view: it passes on
the page as built, and a wrong word, number, bar or head on the page or in its data file makes it fail."""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from porchlamp.serve import first_pull_requests_page as page_module

ROOT = Path(__file__).resolve().parent.parent
SID = "2026-11-02"
RENDER = """
import { readFileSync } from "node:fs";
import { renderFirstPullRequests } from "%(views)s/first_pull_requests.js";
import { renderLookupPage } from "%(views)s/lookup.js";
const record = JSON.parse(readFileSync(process.argv[2], "utf8"));
console.log(JSON.stringify({ report: renderFirstPullRequests(record), lookup: renderLookupPage() }));
"""


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify = _load("verify_static_export_for_report", ROOT / "scripts" / "verify_static_export.py")
stats_tests = _load("first_pull_requests_page_tests", ROOT / "tests" / "test_first_pull_requests_page.py")
NODE = verify.find_node()
pytestmark = pytest.mark.skipif(NODE is None, reason="no node on this machine")
MAIN = re.compile(r'(<main class="app-main" id="app-main" tabindex="-1">)[\s\S]*?(\n *</main>)')


def build(site: Path) -> tuple[Path, str, Path]:
    """A site holding the report, its data file, the lookup and the named projects' pages. Returns the stats file, the as_of and the page."""
    stats = stats_tests.good_stats()
    stats_path = site / "stats.json"
    site.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(json.dumps(stats), encoding="utf-8")
    record = page_module.report_json(stats, snapshot_id=SID, as_of=stats["as_of"])
    data = site / "data" / SID
    data.mkdir(parents=True)
    data_file = data / page_module.DATA_FILE
    data_file.write_text(json.dumps(record), encoding="utf-8")
    script = site / "render.mjs"
    script.write_text(RENDER % {"views": (ROOT / "web" / "app" / "views").as_uri()}, encoding="utf-8")
    drawn = json.loads(subprocess.run([NODE, str(script), str(data_file)], capture_output=True, text=True, check=True).stdout)
    report_html = page_module.report_page(record, exported_on=date(2026, 11, 2))
    assert MAIN.search(report_html)
    target = site / "first-pull-requests" / "index.html"
    target.parent.mkdir()
    target.write_text(MAIN.sub(lambda m: m.group(1) + drawn["report"] + m.group(2), report_html), encoding="utf-8")
    lookup = site / "lookup" / "index.html"
    lookup.parent.mkdir()
    lookup.write_text(MAIN.sub(lambda m: m.group(1) + drawn["lookup"] + m.group(2), page_module.lookup_page()), encoding="utf-8")
    for row in record["projects"]:
        project = site / row["path"].strip("/") / "index.html"
        project.parent.mkdir(parents=True)
        project.write_text("project", encoding="utf-8")
    return stats_path, stats["as_of"], target


def errors_of(site: Path, stats_path: Path, as_of: str, *, data_dir: Path | None = None) -> list[str]:
    return verify.verify_first_pull_requests(site, stats_path, sid=SID, as_of=as_of, data_dir=data_dir, log=lambda *_: None)["errors"]


def test_the_page_as_built_passes(tmp_path: Path) -> None:
    site = tmp_path / "site"
    stats_path, as_of, _ = build(site)
    assert errors_of(site, stats_path, as_of) == []


def test_the_recount_from_the_data_root_runs_and_a_different_file_fails_it(tmp_path: Path) -> None:
    site = tmp_path / "site"
    stats_path, as_of, _ = build(site)
    root = tmp_path / "root" / "raw"
    for layer in ("pr_window", "author_history", "github_meta"):
        (root / layer).mkdir(parents=True)
    errors = errors_of(site, stats_path, as_of, data_dir=tmp_path / "root")
    assert any("what the data root gives" in error for error in errors)


PAGE_PLANTS = [
    ("the title's sentence", "one person gives most of the first answers. Newcomers", "one person gives all of the first answers. Newcomers"),
    ("the lede", "They aren&#39;t.", "They are."),
    ("a caveat", "bystander effect", "observer effect"),
    ("a method line", "found 57 real asks", "found 59 real asks"),
    ("a not-covered line", "When that person stops.", "When she stops."),
    ("a tile's words", "where the job is shared", "where the job is split"),
    ("a section label", "Where newcomers get merged", "Where newcomers do best"),
    ("the head's title", "<title>In most open source", "<title>In all open source"),
    ("the head's description", 'one person gave most first answers, and more newcomers got merged there.">', 'one person gave all first answers, and more newcomers got merged there.">'),
    ("the slider's list", 'data-first="33%|', 'data-first="66%|'),
]


@pytest.mark.parametrize("name, old, new", PAGE_PLANTS, ids=[p[0] for p in PAGE_PLANTS])
def test_a_wrong_word_on_the_page_fails(tmp_path: Path, name: str, old: str, new: str) -> None:
    site = tmp_path / "site"
    stats_path, as_of, target = build(site)
    text = target.read_text(encoding="utf-8")
    assert old in text, name
    target.write_text(text.replace(old, new), encoding="utf-8")
    assert errors_of(site, stats_path, as_of), name


def test_a_sentence_changed_in_both_the_page_and_its_data_file_fails(tmp_path: Path) -> None:
    site = tmp_path / "site"
    stats_path, as_of, target = build(site)
    old, new = "Newcomers get merged more often there.", "Newcomers do best there."
    target.write_text(target.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    data_file = site / "data" / SID / page_module.DATA_FILE
    data_file.write_text(data_file.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    assert errors_of(site, stats_path, as_of)


def test_a_bar_of_the_wrong_height_fails(tmp_path: Path) -> None:
    site = tmp_path / "site"
    stats_path, as_of, target = build(site)
    text = target.read_text(encoding="utf-8")
    heights = re.findall(r"--f:[0-9.]+", text)
    assert len(heights) == 25
    target.write_text(text.replace(heights[14], "--f:0.0100", 1), encoding="utf-8")
    assert errors_of(site, stats_path, as_of)


def test_a_printed_number_of_the_wrong_value_fails(tmp_path: Path) -> None:
    site = tmp_path / "site"
    stats_path, as_of, target = build(site)
    text = target.read_text(encoding="utf-8")
    old = '<span data-v="one_merged">56%</span>'
    assert old in text
    target.write_text(text.replace(old, '<span data-v="one_merged">65%</span>'), encoding="utf-8")
    assert errors_of(site, stats_path, as_of)


def test_a_missing_page_is_an_error_and_not_a_crash(tmp_path: Path) -> None:
    site = tmp_path / "site"
    stats_path, as_of, target = build(site)
    shutil.rmtree(target.parent)
    assert any("is missing" in error for error in errors_of(site, stats_path, as_of))
