"""The report and methodology pages: ``scripts/export_static.py --stats`` writes them, ``porchlamp/serve/
report_pages.py`` renders them, and ``scripts/verify_static_export.py`` proves every number on them.

The fixture is ``tests/serve``'s synthetic snapshot with black-hole projects added (each with the raw
``pr_window`` envelope the API recomputes its evidence from) and a stats file built from the database.
"""
from __future__ import annotations

import copy
import csv
import importlib.util
import json
import re
import shutil
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))  # serve.helpers -> store.helpers

from porchlamp.serve import report_pages  # noqa: E402
from serve.helpers import build_snapshot, finalize, fresh_db  # noqa: E402

SNAPSHOT = "2026-10-14"  # the id the shipped sitemap names: its report address must exist in the fixture site
ECOSYSTEMS = ("pypi", "npm", "go", "systems", "crates", "maven")
#: Black-hole projects added per ecosystem: PyPI and npm go quiet most, as the report says.
BLACK_HOLES = {"pypi": 70, "npm": 60, "go": 6, "systems": 5, "crates": 5, "maven": 4}


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


export_static = _load_script("export_static")
verify_static_export = _load_script("verify_static_export")


def _load_guard():
    spec = importlib.util.spec_from_file_location("ui_url_guard", ROOT / "tests" / "test_ui_no_external_resources.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_guard()


def _envelope(unanswered: int, answered: int) -> dict:
    def pr(number: int, replied: bool) -> dict:
        comments = []
        if replied:
            comments = [{
                "createdAt": "2026-05-03T12:00:00Z", "authorAssociation": "MEMBER",
                "author": {"login": "maintainer", "__typename": "User"},
                "bodyText": "Thanks for the pull request, we will review it shortly.",
            }]
        return {
            "number": number, "createdAt": "2026-05-01T12:00:00Z", "authorAssociation": "NONE",
            "author": {"login": f"outsider-{number}", "__typename": "User", "createdAt": "2020-01-01T00:00:00Z"},
            "isDraft": False, "closedAt": None, "mergedAt": None,
            "comments": {"nodes": comments}, "reviews": {"nodes": []},
        }

    prs = [pr(i, False) for i in range(1, unanswered + 1)] + [pr(1000 + i, True) for i in range(answered)]
    return {"fetched_at": "2026-09-01T01:00:00Z", "data": {"window_anchor": "2026-09-01T00:00:00Z", "window_days": 180, "prs": prs}}


def _stats(conn: sqlite3.Connection, snapshot_id: str) -> dict:
    rows = conn.execute("SELECT ecosystem, gate_reason, gate_undecided FROM repositories WHERE snapshot_id = ?", (snapshot_id,)).fetchall()
    per_eco = report_pages.decided_counts(rows)
    judged = sum(j for j, _ in per_eco.values())
    black_holes = sum(b for _, b in per_eco.values())
    rated = conn.execute("SELECT COUNT(*) FROM repositories WHERE snapshot_id = ? AND rated = 1", (snapshot_id,)).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM repositories WHERE snapshot_id = ?", (snapshot_id,)).fetchone()[0]
    n0, n1, n2 = 1, judged // 2, judged // 3
    n3 = judged - n0 - n1 - n2
    bh1, bh2 = int(0.25 * n1), int(0.30 * n2)
    bh3 = black_holes - bh1 - bh2
    bands = {"[0,1000)": (n0, 0), "[1000,10000)": (n1, bh1), "[10000,50000)": (n2, bh2), "[50000,inf)": (n3, bh3)}
    return {
        "snapshot_id": snapshot_id, "rated": rated, "gate5_decided": judged, "black_holes": black_holes,
        "by_stars": {k: {"n": n, "black_holes": b, "share": round(b / n, 4)} for k, (n, b) in bands.items()},
        "spearman": {"porchlamp": {"n": rated, "rho": 0.1255}},
        "top100_overlap": 9, "top100_stars_fate": {"rated": 50, "black_hole": 25},
        "corpus": {"derived_repos": total, "human_outside_prs": 2123, "distinct_outside_authors": 1106},
    }


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """``(site dir, db, stats path, stats dict)``."""
    tmp = tmp_path_factory.mktemp("report")
    db, raw = tmp / "db.sqlite", tmp / "data" / "raw" / "pr_window"
    raw.mkdir(parents=True)
    conn = fresh_db(db, snapshot_id=SNAPSHOT)
    build_snapshot(conn, db, snapshot_id=SNAPSHOT)
    conn.execute(
        "UPDATE repositories SET ecosystem = CASE rowid % 6 WHEN 0 THEN 'pypi' WHEN 1 THEN 'npm' WHEN 2 THEN 'go' "
        "WHEN 3 THEN 'systems' WHEN 4 THEN 'crates' ELSE 'maven' END WHERE snapshot_id = ?", (SNAPSHOT,),
    )
    for eco, count in BLACK_HOLES.items():
        for i in range(count):
            conn.execute(
                "INSERT INTO repositories (snapshot_id, owner, name, ecosystem, raw_hash, constants_hash, eligible, gate_reason, "
                "rated, coverage, persona_gates, persona_composites, suspicion) VALUES (?, 'bh-org', ?, ?, ?, ?, 0, 'black_hole', 0, 0.0, '{}', '{}', '[]')",
                (SNAPSHOT, f"hole-{eco}-{i}", eco, f"{len(eco):02d}{i:062x}", "c" * 64),
            )
    conn.commit()
    holes = conn.execute("SELECT owner, name FROM repositories WHERE snapshot_id = ? AND gate_reason = 'black_hole'", (SNAPSHOT,)).fetchall()
    for owner, name in holes:
        (raw / f"{owner}__{name}.json").write_text(json.dumps(_envelope(11, 1)), encoding="utf-8")
    stats = _stats(conn, SNAPSHOT)
    finalize(conn, db, snapshot_id=SNAPSHOT)
    conn.close()
    stats_path = tmp / "stats.json"
    stats_path.write_text(json.dumps(stats), encoding="utf-8")

    patch = pytest.MonkeyPatch()
    patch.setenv("PORCHLAMP_DATA_ROOT", str(tmp / "data"))
    try:
        out = tmp / "site"
        export_static.export(db, out, stats_path)
    finally:
        patch.undo()
    return out, db, stats_path, stats, holes


def _page(site: Path, rel: str) -> str:
    return (site / rel).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _data_root(built, monkeypatch):
    """The API serves black-hole evidence only where it finds the raw ``pr_window`` envelopes."""
    monkeypatch.setenv("PORCHLAMP_DATA_ROOT", str(built[0].parent / "data"))


PAGES = ("black-holes/index.html", f"black-holes/{SNAPSHOT}/index.html", "methodology/index.html")


def test_the_export_writes_the_four_files_and_the_csv_lists_ranked_projects_only(built):
    site, db, _stats_path, _stats_dict, holes = built
    for rel in PAGES:
        assert (site / rel).stat().st_size > 1000, rel
    csv_path = site / "black-holes" / SNAPSHOT / f"porchlamp-{SNAPSHOT}-ranked.csv"
    with csv_path.open(newline="", encoding="utf-8") as handle:
        table = list(csv.reader(handle))
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rated = conn.execute("SELECT COUNT(*) FROM repositories WHERE rated = 1").fetchone()[0]
    conn.close()
    lenses = sorted(["balanced", "first_timer", "career_capital", "senior_systems", "ai_assisted"])
    assert table[0] == list(report_pages.CSV_COLUMNS) + [f"rank_{lens}" for lens in lenses]
    assert len(table) == rated + 1 and rated > 100
    listed = {(row[1], row[2]) for row in table[1:]}
    assert not listed & set(holes), "a black-hole project is listed in the CSV"
    # A lens that does not rank a project leaves its rank empty (the fixture has persona-gated rows).
    assert any("" in row[8:] for row in table[1:])
    # Names of black holes appear on no page.
    for rel in PAGES:
        assert "bh-org" not in _page(site, rel) and "hole-pypi" not in _page(site, rel)


def test_verify_report_proves_every_number_and_checks_more_than_thirty(built):
    site, db, stats_path, _stats_dict, _holes = built
    result = verify_static_export.verify_report(db, stats_path, site, log=lambda _line: None)
    assert result["errors"] == []
    assert result["checked"] > 30
    assert verify_static_export.check_sitemap(site) == []


@pytest.mark.parametrize("rel", PAGES[:2])
def test_a_planted_wrong_number_on_a_report_page_fails_verification_by_name(built, tmp_path, rel):
    site, db, stats_path, stats, _holes = built
    copy_dir = tmp_path / "site"
    shutil.copytree(site, copy_dir)
    page = (copy_dir / rel).read_text(encoding="utf-8")
    wrong = f"{stats['black_holes']:,}"
    paragraph = re.search(r'<p class="finding" id="finding-share">.*?</p>', page, re.S).group(0)
    assert wrong in paragraph
    (copy_dir / rel).write_text(page.replace(paragraph, paragraph.replace(wrong, f"{stats['black_holes'] + 1:,}", 1)), encoding="utf-8")
    result = verify_static_export.verify_report(db, stats_path, copy_dir, log=lambda _line: None)
    assert any(rel in error and "finding-share" in error and wrong in error for error in result["errors"]), result["errors"]


def test_a_planted_wrong_csv_cell_fails_verification(built, tmp_path):
    site, db, stats_path, _stats_dict, _holes = built
    copy_dir = tmp_path / "site"
    shutil.copytree(site, copy_dir)
    path = copy_dir / "black-holes" / SNAPSHOT / f"porchlamp-{SNAPSHOT}-ranked.csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    cells = lines[5].split(",")
    cells[4] = str(float(cells[4]) + 0.01)  # the score column
    lines[5] = ",".join(cells)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = verify_static_export.verify_report(db, stats_path, copy_dir, log=lambda _line: None)
    assert any(error.startswith("CSV row") for error in result["errors"]), result["errors"]


def test_the_sitemap_check_names_a_location_with_no_page(built, tmp_path):
    site, _db, _stats_path, _stats, _holes = built
    copy_dir = tmp_path / "site"
    shutil.copytree(site, copy_dir)
    shutil.rmtree(copy_dir / "methodology")
    problems = verify_static_export.check_sitemap(copy_dir)
    assert len(problems) == 1 and "https://porchlamp.org/methodology/" in problems[0]


def _inputs(built):
    """What ``build_facts`` reads, from the fixture export."""
    site, db, _stats_path, stats, _holes = built
    data = site / "data" / SNAPSHOT
    snapshot = json.loads((data / "snapshot.json").read_text(encoding="utf-8"))
    methodology = json.loads((data / "methodology.json").read_text(encoding="utf-8"))
    board = json.loads((data / "leaderboard-balanced.json").read_text(encoding="utf-8"))
    holes = []
    for bucket in (data / "repos").glob("*.json"):
        for record in json.loads(bucket.read_text(encoding="utf-8")).values():
            if record["detail"]["gate_reason"] == "black_hole":
                holes.append(record["detail"]["black_hole_evidence"])
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute("SELECT ecosystem, gate_reason, gate_undecided FROM repositories WHERE snapshot_id = ?", (SNAPSHOT,)).fetchall()
    rated = conn.execute("SELECT COUNT(*) FROM repositories WHERE rated = 1").fetchone()[0]
    conn.close()
    return dict(
        snapshot=snapshot, methodology=methodology, stats=copy.deepcopy(stats), db_rows=rows, db_rated=rated,
        api_ranked=board["total"], api_black_holes=len(holes), evidence=holes,
    )


def test_the_fixture_inputs_pass_every_guard(built):
    facts = report_pages.build_facts(**_inputs(built))
    assert facts.black_holes * 4 > facts.judged
    assert [e.key for e in facts.ecosystems][:2] == ["pypi", "npm"]
    assert facts.unanswered == 11 * facts.black_holes and facts.counted == 12 * facts.black_holes
    assert (str(facts.window_start), str(facts.window_end)) == ("2026-03-17", "2026-09-13")


def test_a_share_of_a_quarter_or_less_fails_the_export_guard(built):
    inputs = _inputs(built)
    # Drop black holes from the database's rows (and the stats) until the share is 25%.
    rows = [r for r in inputs["db_rows"] if r[1] != "black_hole"]
    judged_plain = report_pages.decided_counts(rows)
    base_judged = sum(j for j, _ in judged_plain.values())
    keep = base_judged // 3  # bh / (base + bh) = 25% exactly
    holes = [r for r in inputs["db_rows"] if r[1] == "black_hole"][:keep]
    inputs["db_rows"] = rows + holes
    inputs["stats"].update(gate5_decided=base_judged + keep, black_holes=keep)
    inputs["api_black_holes"] = keep
    inputs["evidence"] = inputs["evidence"][:keep]
    inputs["stats"]["by_stars"] = {
        "[0,1000)": {"n": 0, "black_holes": 0, "share": 0.0},
        "[1000,10000)": {"n": base_judged // 2, "black_holes": 0, "share": 0.0},
        "[10000,50000)": {"n": 1, "black_holes": 0, "share": 0.0},
        "[50000,inf)": {"n": base_judged + keep - base_judged // 2 - 1, "black_holes": keep, "share": round(keep / (base_judged + keep - base_judged // 2 - 1), 4)},
    }
    with pytest.raises(report_pages.ReportError, match="guard: 'more than 1 in 4'"):
        report_pages.build_facts(**inputs)


def test_a_star_band_below_the_one_before_it_fails_the_guard(built):
    inputs = _inputs(built)
    bands = inputs["stats"]["by_stars"]
    # Swap the two upper bands: the totals still add up, the order of their shares does not.
    bands["[50000,inf)"], bands["[10000,50000)"] = bands["[10000,50000)"], bands["[50000,inf)"]
    assert bands["[50000,inf)"]["share"] < bands["[10000,50000)"]["share"]
    with pytest.raises(report_pages.ReportError, match="guard: 'the black-hole share climbs at every step"):
        report_pages.build_facts(**inputs)


def test_pypi_and_npm_must_each_beat_the_other_four_ecosystems(built):
    inputs = _inputs(built)
    # Turn six maven projects into black holes, so Maven's share passes npm's.
    rows, extra = [], 0
    for row in inputs["db_rows"]:
        if row[0] == "maven" and row[1] is None and extra < 40:
            row, extra = ("maven", "black_hole", "[]"), extra + 1
        rows.append(row)
    assert extra == 40
    inputs["db_rows"] = rows
    inputs["stats"]["black_holes"] += extra
    inputs["stats"]["by_stars"]["[50000,inf)"]["black_holes"] += extra
    inputs["stats"]["by_stars"]["[50000,inf)"]["share"] = round(
        inputs["stats"]["by_stars"]["[50000,inf)"]["black_holes"] / inputs["stats"]["by_stars"]["[50000,inf)"]["n"], 4
    )
    inputs["api_black_holes"] += extra
    inputs["evidence"] = inputs["evidence"] + inputs["evidence"][:extra]
    with pytest.raises(report_pages.ReportError, match="guard: 'PyPI and npm go quiet more often"):
        report_pages.build_facts(**inputs)


def test_most_of_those_pull_requests_waited_needs_more_than_half(built):
    inputs = _inputs(built)
    for evidence in inputs["evidence"]:
        evidence["unanswered"], evidence["population_n"] = 6, 12  # exactly half
    with pytest.raises(report_pages.ReportError, match="guard: 'Most of those pull requests waited'"):
        report_pages.build_facts(**inputs)


def test_stats_for_another_snapshot_or_with_another_count_are_refused(built, tmp_path, monkeypatch):
    site, db, _stats_path, stats, _holes = built
    for label, change in (
        ("snapshot_id", {"snapshot_id": "2026-01-01"}),
        ("black_holes", {"black_holes": stats["black_holes"] + 1}),
        ("gate5_decided", {"gate5_decided": stats["gate5_decided"] - 1}),
        ("rated", {"rated": stats["rated"] + 1}),
    ):
        bad = tmp_path / f"stats-{label}.json"
        bad.write_text(json.dumps({**stats, **change}), encoding="utf-8")
        with pytest.raises(SystemExit) as refused:
            export_static.export(db, tmp_path / f"out-{label}", bad)
        message = str(refused.value)
        assert message.startswith("export: ") and ("snapshot" in message if label == "snapshot_id" else label in message), message


def test_method_sentences_are_checked_against_the_gate(built):
    inputs = _inputs(built)
    gate = next(g for g in inputs["methodology"]["gates"] if g["id"] == "D-GATE-5")
    gate["cutoffs"]["response_deadline_days"] = 21
    with pytest.raises(report_pages.ReportError, match="response_deadline_days"):
        report_pages.build_facts(**inputs)
    inputs = _inputs(built)
    gate = next(g for g in inputs["methodology"]["gates"] if g["id"] == "D-GATE-5")
    gate["criterion"] = gate["criterion"].replace("self-closed", "self-ended")
    with pytest.raises(report_pages.ReportError, match="self-closed"):
        report_pages.build_facts(**inputs)


def test_missing_evidence_stops_the_export_with_the_fix(built):
    inputs = _inputs(built)
    inputs["evidence"] = inputs["evidence"][:-1]
    with pytest.raises(report_pages.ReportError, match="PORCHLAMP_DATA_ROOT"):
        report_pages.build_facts(**inputs)


# ---- no outside resources: the existing URL guard, over the generated pages --------------------------

#: The guard allows only github.com links. A report page also prints its own address (the citation, the JSON-LD),
#: so this test adds the site's own origin to what the guard allows: any OTHER host is still a violation.
ALLOWED = guard.ALLOWED_URL_SUBSTRINGS + ("https://porchlamp.org/",)


def external_violations(text: str) -> list[str]:
    found = []
    scannable = guard.scannable_text(text)
    for match in guard.EXTERNAL_URL_RE.finditer(scannable):
        if not any(allowed in match.group(0) for allowed in ALLOWED):
            found.append(match.group(0))
    if re.search(r"<script[^>]*\ssrc=", scannable) or re.search(r"<link[^>]*rel=\"(?:stylesheet|preload)\"[^>]*href=\"https?:", scannable):
        found.append("an external script or stylesheet tag")
    return found


@pytest.mark.parametrize("rel", PAGES)
def test_pages_load_nothing_from_outside(built, rel):
    site = built[0]
    text = _page(site, rel)
    assert external_violations(text) == []
    assert "<script" not in text.replace('<script type="application/ld+json">', "")
    assert "@font-face" not in text and "fonts.googleapis" not in text


def test_a_script_planted_in_the_template_turns_the_guard_red(built, monkeypatch):
    inputs = _inputs(built)
    facts = report_pages.build_facts(**inputs)
    clean = report_pages.render_report(facts, frozen=False, exported_on=date(2026, 10, 17), source_db_sha256="a" * 64)
    assert external_violations(clean) == []
    original = report_pages._site_links
    monkeypatch.setattr(
        report_pages, "_site_links", lambda rel: original(rel) + '<script src="https://cdn.example.com/x.js"></script>'
    )
    planted = report_pages.render_report(facts, frozen=False, exported_on=date(2026, 10, 17), source_db_sha256="a" * 64)
    assert external_violations(planted), "the guard did not see the planted script"


# ---- structured data, canonicals, titles ---------------------------------------------------------------

def _ld(page: str) -> dict:
    return json.loads(re.search(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', page, re.S).group(1))


@pytest.mark.parametrize("rel", PAGES[:2])
def test_json_ld_matches_the_visible_text(built, rel):
    site = built[0]
    page = _page(site, rel)
    nodes = {node["@type"]: node for node in _ld(page)["@graph"]}
    article, dataset = nodes["Article"], nodes["Dataset"]
    h1 = re.search(r"<h1>(.*?)</h1>", page).group(1)
    assert article["headline"] == h1
    assert dataset["license"] == "https://creativecommons.org/licenses/by/4.0/"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}/\d{4}-\d{2}-\d{2}", dataset["temporalCoverage"])
    [download] = dataset["distribution"]
    assert download["encodingFormat"] == "text/csv"
    assert download["contentUrl"] == f"https://porchlamp.org/black-holes/{SNAPSHOT}/porchlamp-{SNAPSHOT}-ranked.csv"
    assert f'href="{download["contentUrl"].rsplit("/", 1)[1]}"' in page or f'href="{SNAPSHOT}/{download["contentUrl"].rsplit("/", 1)[1]}"' in page
    assert 50 <= len(dataset["description"]) <= 5000
    visible = re.search(r'id="finding-share">.*?\(([\d.]+)%\)', page, re.S).group(1)
    assert f"({visible}%)" in dataset["description"]
    assert article["author"] == {"@id": "https://porchlamp.org/#creator"}
    assert dataset["creator"] == {"@id": "https://porchlamp.org/#creator"}
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", article["datePublished"])


def test_each_page_is_its_own_canonical_and_the_titles_differ(built):
    site = built[0]
    expected = {
        PAGES[0]: "https://porchlamp.org/black-holes/",
        PAGES[1]: f"https://porchlamp.org/black-holes/{SNAPSHOT}/",
        PAGES[2]: "https://porchlamp.org/methodology/",
    }
    titles = []
    for rel, canonical in expected.items():
        page = _page(site, rel)
        assert re.findall(r'<link rel="canonical" href="([^"]*)"', page) == [canonical]
        titles.append(re.search(r"<title>(.*?)</title>", page).group(1))
    assert len(set(titles)) == 3
    assert titles[0] == "Open-source black holes: how many projects ignore outside pull requests (October 2026) | Porchlamp"
    assert titles[2] == "Porchlamp methodology: how open-source projects are measured and ranked | Porchlamp"


def test_the_report_copy_follows_the_brief(built):
    site = built[0]
    page = _page(site, PAGES[0])
    for ident in ("finding-share", "finding-stars", "finding-top100", "finding-correlation", "finding-prs"):
        assert re.search(rf'<p class="finding" id="{ident}">.*?<a class="anchor" href="#{ident}"', page, re.S), ident
    for heading in ("Key findings", "Do popular projects answer fewer pull requests?",
                    "Which package ecosystems answer outside pull requests?", "How we measured", "What this is not",
                    "Download the data", "Cite this report"):
        assert f">{heading}</h2>" in page, heading
    assert page.count("<details><summary>The numbers behind the chart</summary>") == 2
    assert page.count("<svg ") == 2 and page.count('role="img"') == 2
    assert "<title>" in re.search(r"<svg.*?</svg>", page, re.S).group(0)
    assert 'href="../methodology/"' in page and 'href="https://github.com/pctablet505/porchlamp"' in page
    frozen = _page(site, PAGES[1])
    assert 'href="../../methodology/"' in frozen


def test_methodology_page_lists_gates_pillars_lenses_parameters_and_hashes(built):
    site = built[0]
    page = _page(site, PAGES[2])
    methodology = json.loads((site / "data" / SNAPSHOT / "methodology.json").read_text(encoding="utf-8"))
    for gate in methodology["gates"]:
        assert f">{gate['id']}: " in page
    assert page.count("<details>") == 1 and "<summary>All 18 parameters</summary>" in page
    assert json.loads((site / "data" / SNAPSHOT / "snapshot.json").read_text(encoding="utf-8"))["constants_hash"] in page
    assert "python -m porchlamp.snapshot reproduce" in page
    assert ">None<" not in page and ">n/a<" in page  # a parameter with no minimum count reads n/a, not Python's None
    assert 'href="../black-holes/"' in page and 'href="../"' in page


def test_copy_js_names_cover_every_parameter_and_lens():
    param_names = report_pages.copy_js_map("PARAM_NAMES")
    persona_names = report_pages.copy_js_map("PERSONA_NAMES")
    from porchlamp.core import constants

    assert set(constants.PARAM_INPUT_IDS) <= set(param_names)
    assert set(constants.PERSONA_PRESETS) <= set(persona_names)
    assert set(report_pages.copy_js_map("PILLAR_NAMES")) == set(constants.PILLAR_NAMES)
    with pytest.raises(report_pages.ReportError):
        report_pages.copy_js_map("NO_SUCH_MAP")


def test_formatting_rounds_half_up_like_the_site():
    assert report_pages.pct1(1133, 4297) == "26.4"
    assert report_pages.pct1(117, 586) == "20.0"
    assert report_pages.pct0(467, 2190) == "21"
    assert report_pages.pct0(94, 244) == "39"
    assert report_pages.rho_text(0.1255) == "0.13"
    assert report_pages.band_range_text("[1000,10000)") == "1,000 to 10,000"
    assert report_pages.band_range_text("[50000,inf)") == "50,000 or more"
