"""The report and methodology pages: ``scripts/export_static.py --stats`` writes them, ``porchlamp/serve/
report_pages.py`` renders them, and ``scripts/verify_static_export.py`` proves every number on them.

The fixture is ``tests/serve``'s synthetic snapshot with black-hole projects added (each with the raw
``pr_window`` envelope the API recomputes its evidence from) and the stats file ``porchlamp.snapshot.stats``
computes from the database, a star count per project and a derived bundle per project.
"""
from __future__ import annotations

import copy
import csv
import importlib.util
import html
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))  # serve.helpers -> store.helpers

from porchlamp.serve import report_pages  # noqa: E402
from porchlamp.snapshot.stats import compute_stats  # noqa: E402
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
            "isDraft": False, "timelineItems": {"nodes": []}, "closedAt": None, "mergedAt": None,
            "comments": {"nodes": comments}, "reviews": {"nodes": []},
        }

    prs = [pr(i, False) for i in range(1, unanswered + 1)] + [pr(1000 + i, True) for i in range(answered)]
    return {"fetched_at": "2026-09-01T01:00:00Z", "data": {"window_anchor": "2026-09-01T00:00:00Z", "window_days": 180, "prs": prs}}


def _pin_black_holes(conn: sqlite3.Connection, snapshot_dir: Path, holes: list, envelope: dict) -> None:
    """The stored-snapshot files the evidence reader checks: a manifest naming each black hole's bundle, a bundle
    whose ``gr14_ghost_rate`` pins the raw envelope's hash, and the bundle's hash as the stored ``raw_hash``."""
    from porchlamp.collect.envelope import envelope_hash
    from porchlamp.core.models import canonical_json, sha256_hex

    (snapshot_dir / "bundles").mkdir(parents=True)
    entries = []
    for owner, name in holes:
        bundle = {"inputs": {"gr14_ghost_rate": {
            "kind": "measured", "source": f"raw/pr_window/{owner}__{name}.json",
            "source_hash": envelope_hash(envelope["data"]),
        }}}
        bundle_file = f"bundles/{owner}__{name}.json"
        (snapshot_dir / bundle_file).write_text(json.dumps(bundle), encoding="utf-8")
        entries.append({"owner": owner, "name": name, "bundle_file": bundle_file})
        conn.execute(
            "UPDATE repositories SET raw_hash = ? WHERE snapshot_id = ? AND owner = ? AND name = ?",
            (sha256_hex(canonical_json(bundle)), SNAPSHOT, owner, name),
        )
    (snapshot_dir / "manifest.json").write_text(json.dumps({"snapshot_id": SNAPSHOT, "repos": entries}), encoding="utf-8")
    conn.commit()


def _write_stats_inputs(db: Path, data: Path, snapshot_id: str) -> None:
    """What ``porchlamp.snapshot stats`` reads besides the database: a GitHub metadata envelope with a star
    count for every project, and a derived bundle with two outside pull requests for every project.

    Stars are dealt so that the black-hole share climbs with stars, as the report says: one judged project
    under 1,000 stars, then half, a third and the rest of the judged projects in the three charted bands,
    with a quarter, then 30%, then the remaining black holes. Projects that are not judged get under 1,000."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT owner, name, gate_reason, gate_undecided FROM repositories WHERE snapshot_id = ? ORDER BY id", (snapshot_id,)
    ).fetchall()
    conn.close()
    decided = [r for r in rows if r[2] in (None, "black_hole", "unrated: no_external_workflow")
               and "D-GATE-5" not in json.loads(r[3] or "[]")]
    holes = iter([r for r in decided if r[2] == "black_hole"])
    plain = iter([r for r in decided if r[2] != "black_hole"])
    judged, n_holes = len(decided), sum(r[2] == "black_hole" for r in decided)
    n1, n2 = judged // 2, judged // 3
    n3 = judged - 1 - n1 - n2
    bh1, bh2 = int(0.25 * n1), int(0.30 * n2)
    stars: dict[tuple[str, str], int] = {}
    for low, n, bh in ((500, 1, 0), (1_000, n1, bh1), (10_000, n2, bh2), (50_000, n3, n_holes - bh1 - bh2)):
        for i in range(n):
            owner, name, *_ = next(holes) if i < bh else next(plain)
            stars[(owner, name)] = low + i
    meta, derived = data / "raw" / "github_meta", data / "derived"
    meta.mkdir(parents=True, exist_ok=True)
    derived.mkdir(parents=True, exist_ok=True)
    for index, (owner, name, *_) in enumerate(rows):
        count = stars.get((owner, name), 100 + index % 800)
        (meta / f"{owner}__{name}.json").write_text(json.dumps({"data": {"stars": count}}), encoding="utf-8")
        events = [{"author_login": f"outsider-{index}-{k}", "is_external": True, "created_at": "2026-05-01T12:00:00Z",
                   "merged_at": None, "first_human_review_at": None} for k in range(2)]
        (derived / f"{owner}__{name}.json").write_text(json.dumps({"pr_events": events}), encoding="utf-8")


def _add_evidence(conn: sqlite3.Connection, snapshot_id: str) -> None:
    """Every rated project gets a row for each of the 18 parameters, as a production record has. The reply time
    and the 14-day ghost rate cycle through the four cases a project page words: both measured, only the reply
    time, only the ghost rate, neither (a too-small sample keeps its value but is not shown)."""
    from porchlamp.core import constants

    window = ("2026-04-10T13:41:50+00:00", "2026-10-07T13:41:50+00:00")
    repos = conn.execute(
        "SELECT id FROM repositories WHERE snapshot_id = ? AND rated = 1 ORDER BY id", (snapshot_id,)
    ).fetchall()
    for position, (repo_id,) in enumerate(repos):
        have_reply, have_ghost = position % 4 in (0, 1), position % 4 in (0, 2)
        for param_id in constants.PARAM_INPUT_IDS:
            conn.execute("DELETE FROM params WHERE repo_id = ? AND param_id = ?", (repo_id, param_id))
            if param_id == "ttfhr_hours":
                row = (6.0 + position * 7.3, 100.0, 1, None, 40 + position, *window) if have_reply else (None, None, 0, "insufficient_n", 3, None, None)
            elif param_id == "gr14_ghost_rate":
                row = (0.2 + position / 1000, 60.0, 1, None, 25 + position, *window) if have_ghost else (0.5, None, 0, "insufficient_n", 4, *window)
            elif param_id in ("legal_ip", "ai_posture", "promotion_ladder"):
                row = ("DCO" if param_id == "legal_ip" else "AD_HOC", 70.0, 1, None, 0, None, None)
            elif param_id == "dependents_deduped":
                row = (None, None, 0, "not_applicable", 0, None, None)
            else:
                row = (0.5 + position / 10000, 50.0, 1, None, 12, None, None)
            value, score, measured, reason, n, start, end = row
            conn.execute(
                "INSERT INTO params (repo_id, param_id, raw_value, score, measured, reason, n, window_start, window_end) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (repo_id, param_id, None if value is None else json.dumps(value), score, measured, reason, n, start, end),
            )
    conn.commit()


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
    _pin_black_holes(conn, tmp / "snapshot", holes, _envelope(11, 1))
    _add_evidence(conn, SNAPSHOT)
    finalize(conn, db, snapshot_id=SNAPSHOT)
    conn.close()
    _write_stats_inputs(db, tmp / "data", SNAPSHOT)
    stats = compute_stats(db, tmp / "data")
    stats_path = tmp / "stats.json"
    stats_path.write_text(json.dumps(stats), encoding="utf-8")

    out = tmp / "site"
    export_static.export(db, out, stats_path, snapshot_dir=tmp / "snapshot", data_dir=tmp / "data")
    return out, db, stats_path, stats, holes


def _page(site: Path, rel: str) -> str:
    return (site / rel).read_text(encoding="utf-8")


def _evidence_files(built):
    """The files the export read the black-hole evidence from: the stored snapshot and the data root."""
    from porchlamp.serve.app import PinnedEvidenceFiles

    return PinnedEvidenceFiles(built[0].parent / "snapshot", built[0].parent / "data")


@pytest.fixture(scope="module")
def report_record(built):
    """The report's record: the shipped templates with the values ``verify_report`` works out from the stats file and the
    database, as the verifier's later checks get it (the methodology's and the home's finding lines)."""
    site, db, stats_path, *_ = built
    result = verify_static_export.verify_report(db, stats_path, site, evidence_files=_evidence_files(built), log=lambda _line: None)
    assert result["errors"] == [] and result["record"] is not None, result["errors"][:3]
    return result["record"]


PAGES =("black-holes/index.html", f"black-holes/{SNAPSHOT}/index.html")
#: The methodology is the app's own view at its address (owner, 2026-10-17): the export writes the app's page there.
METHODOLOGY = "methodology/index.html"


def test_the_export_writes_the_report_pages_and_the_csv_lists_ranked_projects_only(built):
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
    result = verify_static_export.verify_report(db, stats_path, site, evidence_files=_evidence_files(built), log=lambda _line: None)
    assert result["errors"] == []
    assert result["checked"] > 30
    assert verify_static_export.check_sitemap(site) == []


def _fill(template: str, values: dict) -> str:
    """A report.json template as the view fills it, as plain text."""
    return re.sub(r"\{([a-z][a-z0-9_]*)\}", lambda m: values[m.group(1)], template)


@pytest.mark.parametrize("rel", PAGES[:2])
def test_a_planted_wrong_number_on_a_report_page_fails_verification_by_name(built, tmp_path, rel):
    site, db, stats_path, stats, _holes = built
    copy_dir = tmp_path / "site"
    shutil.copytree(site, copy_dir)
    page = (copy_dir / rel).read_text(encoding="utf-8")
    right = f"{stats['black_holes']:,}"
    sentence = re.search(r'<p class="report-figure-text">.*?</p>', page, re.S).group(0)
    hook = f'<span data-v="black_holes">{right}</span>'
    assert hook in sentence
    planted = sentence.replace(hook, f'<span data-v="black_holes">{stats["black_holes"] + 1:,}</span>')
    (copy_dir / rel).write_text(page.replace(sentence, planted), encoding="utf-8")
    result = verify_static_export.verify_report(db, stats_path, copy_dir, evidence_files=_evidence_files(built), log=lambda _line: None)
    assert any(error.startswith(f"{rel}: hook 'black_holes' shows") and right in error for error in result["errors"]), result["errors"]
    assert any(error.startswith(f"{rel} #finding-share sentence") for error in result["errors"]), result["errors"]


def test_a_changed_word_on_a_report_page_fails_verification_as_a_redraw(built, tmp_path):
    """A copy.js word no sentence check reads: only the re-draw of the report from the values the verifier worked out
    (ENG-17) sees it, and the frozen copy no longer matches the latest."""
    site, db, stats_path, *_ = built
    copy_dir = tmp_path / "site"
    shutil.copytree(site, copy_dir)
    caveat = report_pages.copy_js_string("REPORT_STARS_CAVEAT")
    page = (copy_dir / PAGES[0]).read_text(encoding="utf-8")
    assert page.count(caveat) == 1
    (copy_dir / PAGES[0]).write_text(page.replace(caveat, caveat.replace("roughly fixed", "about fixed")), encoding="utf-8")
    errors = verify_static_export.verify_report(db, stats_path, copy_dir, evidence_files=_evidence_files(built), log=lambda _line: None)["errors"]
    assert sorted(error.split(":")[0] for error in errors) == [
        "latest and frozen draw the same report", "the report is what the prerender draws from the values worked out here (bytes)",
    ], errors


def test_the_checker_api_serves_the_black_hole_evidence_the_export_wrote(built):
    """The request grid compares the site's files with the API's own answers. The export read each black hole's
    evidence from the stored snapshot's files, so the grid's API must read the same files: without them its
    answer carries no evidence and every black hole would be a mismatch."""
    site, db, _stats_path, _stats, holes = built
    owner, name = holes[0]
    exported = None
    for bucket in sorted(site.glob("data/*/repos/*.json")):
        records = json.loads(bucket.read_text(encoding="utf-8"))
        if f"{owner}/{name}" in records:
            exported = records[f"{owner}/{name}"]["detail"]["black_hole_evidence"]
    assert exported is not None, "the export wrote the black hole's evidence"
    grid = verify_static_export.Grid(db, _evidence_files(built))
    answer = grid.client.get(f"/v1/porchlamp/repos/{owner}/{name}").json()
    assert answer["black_hole_evidence"] == exported


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
    result = verify_static_export.verify_report(db, stats_path, copy_dir, evidence_files=_evidence_files(built), log=lambda _line: None)
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
    assert (facts.one_in - 1) * facts.black_holes <= facts.judged < facts.one_in * facts.black_holes
    assert [e.key for e in facts.ecosystems][:2] == ["pypi", "npm"]
    assert facts.unanswered == 11 * facts.black_holes and facts.counted == 12 * facts.black_holes
    assert (str(facts.window_start), str(facts.window_end)) == ("2026-03-17", "2026-09-13")


def test_a_share_of_exactly_a_quarter_is_more_than_1_in_5_never_1_in_4(built):
    """The headline's N is computed: the smallest whole number with N * black_holes > judged. At exactly 25% the
    true claim is "more than 1 in 5"; "more than 1 in 4" would be false."""
    inputs = _inputs(built)
    # Drop black holes from the database's rows (and the stats) until the share is 25%.
    rows = [r for r in inputs["db_rows"] if r[1] != "black_hole"]
    # 25% exactly needs the judged projects that are not black holes to be a multiple of 3: drop up to two judged
    # PyPI rows (a higher PyPI share keeps the ecosystem guard true).
    surplus = sum(j for j, _ in report_pages.decided_counts(rows).values()) % 3
    for _ in range(surplus):
        rows.remove(next(r for r in rows if r[0] == "pypi" and report_pages.decided_counts([r])))
    judged_plain = report_pages.decided_counts(rows)
    base_judged = sum(j for j, _ in judged_plain.values())
    assert base_judged % 3 == 0
    keep = base_judged // 3  # bh / (base + bh) = 25% exactly
    holes = [r for r in inputs["db_rows"] if r[1] == "black_hole"][:keep]
    inputs["db_rows"] = rows + holes
    inputs["stats"].update(gate5_decided=base_judged + keep, black_holes=keep)
    inputs["api_black_holes"] = keep
    inputs["evidence"] = inputs["evidence"][:keep]
    # Star bands whose shares still rise at every step (the report's band guard), adding up to the same totals.
    n1, n2 = base_judged // 2, 4
    n3, bh3 = base_judged + keep - n1 - n2, keep - 2
    inputs["stats"]["by_stars"] = {
        "[0,1000)": {"n": 0, "black_holes": 0, "share": 0.0},
        "[1000,10000)": {"n": n1, "black_holes": 1, "share": round(1 / n1, 4)},
        "[10000,50000)": {"n": n2, "black_holes": 1, "share": 0.25},
        "[50000,inf)": {"n": n3, "black_holes": bh3, "share": round(bh3 / n3, 4)},
    }
    assert 1 / n1 < 0.25 < bh3 / n3
    facts = report_pages.build_facts(**inputs)
    assert facts.black_holes * 4 == facts.judged
    assert facts.one_in == 5
    record = report_pages.report_json(facts)
    assert _fill(record["findings"][0]["lead"], record["values"]) == "More than 1 in 5."


def test_a_star_band_below_the_one_before_it_fails_the_guard(built):
    inputs = _inputs(built)
    bands = inputs["stats"]["by_stars"]
    # Swap the two upper bands: the totals still add up, the order of their shares does not.
    bands["[50000,inf)"], bands["[10000,50000)"] = bands["[10000,50000)"], bands["[50000,inf)"]
    assert bands["[50000,inf)"]["share"] < bands["[10000,50000)"]["share"]
    with pytest.raises(report_pages.ReportError, match="guard: 'the black-hole share climbs at every step"):
        report_pages.build_facts(**inputs)


def test_python_and_javascript_must_each_beat_the_other_four_ecosystems(built):
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
    with pytest.raises(
        report_pages.ReportError,
        match=r"guard: 'Python and JavaScript projects go quiet more often than Rust, Go, Java or C and C\+\+ projects\.' is false",
    ):
        report_pages.build_facts(**inputs)


@pytest.mark.parametrize("rho", [0.6, -0.6, 0.2, -0.2])
def test_stars_barely_predict_an_answer_needs_a_rank_correlation_under_two_tenths(built, rho):
    """"Barely" is a very weak correlation, under 0.2 either way (Evans' scale); at 0.2 the sentence would overstate it,
    and the export stops for new words instead of choosing them."""
    inputs = _inputs(built)
    inputs["stats"]["spearman"]["porchlamp"]["rho"] = rho
    with pytest.raises(
        report_pages.ReportError,
        match=r"guard: 'Stars barely predict an answer' needs a rank correlation under 0\.2 either way, got .*: the sentence "
        r"needs new words, and new words need the owner",
    ):
        report_pages.build_facts(**inputs)


@pytest.mark.parametrize("rho", [0.19, -0.19])
def test_a_rank_correlation_just_under_two_tenths_keeps_stars_barely_predict_an_answer(built, rho):
    inputs = _inputs(built)
    inputs["stats"]["spearman"]["porchlamp"]["rho"] = rho
    assert report_pages.build_facts(**inputs).rho == rho


def test_fame_is_no_guarantee_needs_a_black_hole_among_the_most_starred(built):
    inputs = _inputs(built)
    assert inputs["stats"]["top100_stars_fate"]["black_hole"] > 0
    inputs["stats"]["top100_stars_fate"]["black_hole"] = 0
    with pytest.raises(report_pages.ReportError, match="guard: 'Fame is no guarantee' needs a black hole among the 100 most-starred"):
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
    with pytest.raises(report_pages.ReportError, match="--snapshot-dir"):
        report_pages.build_facts(**inputs)


# ---- no outside resources: the existing URL guard, over the generated pages --------------------------

#: The guard allows only github.com links. A report page also prints its own address (the citation, the JSON-LD),
#: so this test adds the site's own origin to what the guard allows: any OTHER host is still a violation.
ALLOWED = guard.ALLOWED_URL_SUBSTRINGS + ("https://porchlamp.org/",)


#: An address that is not on the site: it has a scheme (``https:``, ``data:``) or starts with ``//``.
OFF_SITE = re.compile(r"^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//)")


def script_srcs(text: str) -> list[str]:
    """The ``src`` of every script tag, in page order."""
    return re.findall(r"<script[^>]*\ssrc=[\"']([^\"']*)[\"']", text)


def inline_scripts(text: str) -> list[str]:
    """The opening tag of every script that has no ``src``: only the JSON-LD block may be one."""
    return [tag for tag in re.findall(r"<script\b[^>]*>", text) if not re.search(r"\ssrc=", tag)]


def external_violations(text: str) -> list[str]:
    found = []
    scannable = guard.scannable_text(text)
    for match in guard.EXTERNAL_URL_RE.finditer(scannable):
        if not any(allowed in match.group(0) for allowed in ALLOWED):
            found.append(match.group(0))
    stylesheets = re.findall(r"<link[^>]*rel=[\"'](?:stylesheet|preload)[\"'][^>]*href=[\"']([^\"']*)[\"']", scannable)
    if any(OFF_SITE.match(src) for src in script_srcs(scannable) + stylesheets):
        found.append("an external script or stylesheet tag")
    return found


@pytest.mark.parametrize("rel", PAGES)
def test_pages_load_nothing_from_outside(built, rel):
    site = built[0]
    text = _page(site, rel)
    assert external_violations(text) == []
    # the latest is the app's page, which the app takes over; the frozen copy loads no app
    assert script_srcs(text) == list(report_pages.PAGE_SCRIPTS["app" if rel == PAGES[0] else "page"])
    assert inline_scripts(text) == ['<script type="application/ld+json">']
    assert "@font-face" not in text and "fonts.googleapis" not in text


def test_a_script_planted_in_the_template_turns_the_guard_red(built, monkeypatch):
    inputs = _inputs(built)
    facts = report_pages.build_facts(**inputs)
    clean = report_pages.report_page(facts, frozen=False, exported_on=date(2026, 10, 17))
    assert external_violations(clean) == []
    original = report_pages.app_page
    monkeypatch.setattr(
        report_pages,
        "app_page",
        lambda *a, **k: original(*a, **k).replace("</body>", '<script src="https://cdn.example.com/x.js"></script></body>'),
    )
    planted = report_pages.report_page(facts, frozen=False, exported_on=date(2026, 10, 17))
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
    h1 = html.unescape(re.search(r'<h1 class="view-heading" id="report-title">(.*?)</h1>', page).group(1))
    assert article["headline"] == h1 == report_pages.copy_js_string("REPORT_H1")
    assert dataset["license"] == "https://creativecommons.org/licenses/by/4.0/"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}/\d{4}-\d{2}-\d{2}", dataset["temporalCoverage"])
    [download] = dataset["distribution"]
    assert download["encodingFormat"] == "text/csv"
    assert download["contentUrl"] == f"https://porchlamp.org/black-holes/{SNAPSHOT}/porchlamp-{SNAPSHOT}-ranked.csv"
    assert f'href="{download["contentUrl"].removeprefix("https://porchlamp.org")}"' in page
    assert 50 <= len(dataset["description"]) <= 5000
    visible = re.search(r'<p class="report-figure-text">.*?\(<span data-v="share">([\d.]+)</span>%\)', page, re.S).group(1)
    assert f"({visible}%)" in dataset["description"]
    assert article["author"] == {"@id": "https://porchlamp.org/#creator"}
    assert dataset["creator"] == {"@id": "https://porchlamp.org/#creator"}
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", article["datePublished"])


def test_each_page_is_its_own_canonical_and_the_titles_differ(built):
    site = built[0]
    expected = {
        PAGES[0]: "https://porchlamp.org/black-holes/",
        PAGES[1]: f"https://porchlamp.org/black-holes/{SNAPSHOT}/",
        METHODOLOGY: "https://porchlamp.org/methodology/",
    }
    titles = []
    for rel, canonical in expected.items():
        page = _page(site, rel)
        assert re.findall(r'<link rel="canonical" href="([^"]*)"', page) == [canonical]
        titles.append(re.search(r"<title>(.*?)</title>", page).group(1))
    assert len(set(titles)) == 3
    assert titles[0] == "Open-source black holes: do projects answer pull requests? (2026 data) | Porchlamp"
    assert titles[1] == f"Open-source black holes: do projects answer pull requests? (snapshot {SNAPSHOT}) | Porchlamp"
    assert titles[2] == "Porchlamp methodology: how open-source projects are measured and ranked | Porchlamp"


def test_the_report_follows_the_approved_design(built):
    """The design (report-design/SPEC.md): the lead figure, then four findings as cards, each chart a real table
    with its caption, no "numbers behind the chart" disclosure and no "#" anchors, the old ids kept, the shorter
    intro, the frozen copy linked from the cite, and the same report on the frozen copy."""
    site = built[0]
    page = _page(site, PAGES[0])
    main = re.search(r"<main.*?</main>", page, re.S).group(0)  # the chrome draws its own icons outside <main>
    assert re.search(r'<section class="report-lead" id="finding-share" aria-labelledby="finding-share-lead">', main)
    for ident in ("finding-stars", "finding-top100", "finding-correlation", "finding-prs"):
        assert f'<div class="finding" id="{ident}"><h3 class="finding-lead">' in main, ident
    for ident in ("key-findings", "stars", "ecosystems", "how-we-measured", "what-this-is-not", "download", "cite"):
        assert f' id="{ident}"' in main, ident
    for name in ("REPORT_FINDINGS_HEADING", "REPORT_STARS_HEADING", "REPORT_ECOSYSTEMS_HEADING", "REPORT_METHOD_HEADING",
                 "REPORT_LIMITS_HEADING", "REPORT_DOWNLOAD_HEADING", "REPORT_CITE_HEADING"):
        assert f">{report_pages.copy_js_string(name)}</h2>" in main, name
    assert "<details" not in main and 'class="anchor"' not in main and "<svg " not in main
    assert main.count('<table class="bar-table" role="table"><caption class="sr-only">') == 2
    intro = re.search(r'<p class="report-intro">(.*?)</p>', main).group(1)
    assert intro == "You send a small fix to a project you use every day. Two weeks later, nobody has replied."
    assert "normal experience" not in page
    for href in ("/methodology/", "/", f"/black-holes/{SNAPSHOT}/", f"/black-holes/{SNAPSHOT}/porchlamp-{SNAPSHOT}-ranked.csv"):
        assert f'href="{href}"' in main, href
    assert 'href="https://github.com/pctablet505/porchlamp"' in page  # the footer's data link
    frozen = _page(site, PAGES[1])
    assert re.search(r"<main.*?</main>", frozen, re.S).group(0) == main.replace(' data-prerendered="report"', "")


def test_report_json_is_the_facts_record_and_holds_only_what_the_page_prints(built):
    """The export's report.json is ``report_json`` of the facts; every value is a hook the page prints, and every
    hook the page prints is a value, a chart cell or the database digest (from the manifest, the one source)."""
    site = built[0]
    facts = report_pages.build_facts(**_inputs(built))
    record = json.loads((site / "data" / SNAPSHOT / "report.json").read_text(encoding="utf-8"))
    assert record == report_pages.report_json(facts)
    hooks = dict(re.findall(r'<span data-v="([^"]+)">([^<]*)</span>', re.search(r"<main.*?</main>", _page(site, PAGES[0]), re.S).group(0)))
    assert {k: v for k, v in hooks.items() if "." not in k and k != "source_db_sha256"} == record["values"]
    manifest = json.loads((site / "data" / SNAPSHOT / "manifest.json").read_text(encoding="utf-8"))
    assert hooks["source_db_sha256"] == manifest["source_db_sha256"] and "source_db_sha256" not in record["values"]
    charts = {k for k in hooks if "." in k}
    assert charts == {f"bands.{i}.{f}" for i in range(3) for f in ("label", "judged", "black_holes", "share")} | {
        f"ecosystems.{i}.{f}" for i in range(6) for f in ("name", "judged", "black_holes", "share")}
    assert [row["name"] for row in record["ecosystems"]] == [report_pages.ECOSYSTEM_NAMES[row["key"]] for row in record["ecosystems"]]


def test_the_frozen_copy_carries_its_own_stylesheet_and_no_app(built):
    """ENG-14: the frozen copy links one stylesheet beside it, the site's stylesheets joined at export, so a later
    restyle of the app cannot alter a page that is never drawn again; it loads no app to redraw it."""
    site = built[0]
    frozen = _page(site, PAGES[1])
    assert re.findall(r'<link rel="stylesheet" href="([^"]*)">', frozen) == [f"/black-holes/{SNAPSHOT}/styles.css"]
    assert "/app.js" not in frozen and "data-prerendered" not in frozen
    styles = (site / "black-holes" / SNAPSHOT / "styles.css").read_text(encoding="utf-8")
    hrefs = re.findall(r'<link rel="stylesheet" href="([^"]*)">', (site / "404.html").read_text(encoding="utf-8"))
    assert hrefs == ["/styles/tokens.css", "/styles/base.css", "/styles/components.css", "/styles/views.css"]
    assert styles == "".join(f"/* {h} */\n" + (site / h.lstrip("/")).read_text(encoding="utf-8").rstrip("\n") + "\n" for h in hrefs)
    assert ".report-page" in styles and ".bar-table" in styles


def test_the_export_ships_what_the_carried_frozen_copies_link_and_the_verifier_names_a_missing_file(built, tmp_path):
    """A publish carries the older frozen copies from the published tree, never drawn again, and mirrors the export: the
    export ships every file they link from the site root (``export_static.CARRIED_FROZEN_ASSETS``), and the verifier
    names one that is missing. /black-holes/2026-10-14/ links favicon.svg and styles/tokens.css, base.css and report.css,
    and uses base.css's .seo-fallback rules, which no page the export writes uses; the copy drawn here, carried once the
    snapshot moves on, links only listed files outside its own folder."""
    site, db, stats_path, *_ = built
    carried = set(export_static.CARRIED_FROZEN_ASSETS)
    assert {"favicon.svg", "styles/tokens.css", "styles/base.css", "styles/report.css"} <= carried
    assert sorted(asset for asset in carried if not (site / asset).is_file()) == []
    base = _page(site, "styles/base.css")
    assert ".seo-fallback {" in base and ".seo-fallback-table-wrap {" in base
    linked = {ref.lstrip("/") for ref in re.findall(r'(?:href|src)="(/[^"#?]*\.[a-z]+)"', _page(site, PAGES[1]))
              if not ref.startswith(f"/black-holes/{SNAPSHOT}/")}
    assert linked and linked <= carried, linked
    copy_dir = _copy_site(site, tmp_path)
    (copy_dir / "styles" / "report.css").unlink()
    errors = verify_static_export.verify_report(db, stats_path, copy_dir, evidence_files=_evidence_files(built), log=lambda _line: None)["errors"]
    assert errors == [
        "the files the carried frozen copies link (export_static.CARRIED_FROZEN_ASSETS), missing: expected [], found ['styles/report.css']"
    ], errors


# ---- the words: the published report, but for the owner's approved changes ---------------------------------------

#: The export's report.json for the published snapshot 2026-10-17. web/app/tests/unit.test.mjs draws the view from it;
#: the test below pins it to ``report_json`` of that snapshot's facts.
REPORT_FIXTURE = ROOT / "tests" / "fixtures" / "report" / "report.json"
#: The published database's sha256, which the report prints from the manifest.
PUBLISHED_DB_SHA256 = "ff1d7a10f0c746e0a64ade264a85129aec9459674fcc6c0ce0b67f1a3215a6e1"
#: What the published 2026-10-17 report said (porchlamp.org/black-holes/2026-10-17/, pages repo adac7c57), sentence by
#: sentence, as the Python renderer the report view replaces printed it.
PUBLISHED_REPORT = {
    "finding-share lead": "More than 1 in 6.",
    "finding-share": "In Porchlamp's 2026-10-17 snapshot, 720 of 3,613 active open-source projects (19.9%) left most "
    "outside pull requests with no human reply, review or merge within 14 days.",
    "finding-stars lead": "More stars, more silence.",
    "finding-stars": "The share rises with stars: 16% of projects with 1,000 to 10,000 stars, 25% at 10,000 to 50,000, and "
    "28% at 50,000 or more.",
    "finding-top100 lead": "Fame is no guarantee.",
    "finding-top100": "18 of the 100 most-starred projects are black holes, and only 9 of them reach Porchlamp's top 100.",
    "finding-correlation lead": "Stars barely predict an answer.",
    "finding-correlation": "Across 4,322 ranked projects, the rank correlation between stars and the Porchlamp score is 0.09.",
    "finding-prs lead": "Most of those pull requests waited.",
    "finding-prs": "Inside the black-hole projects, 38,671 of 49,188 counted outside pull requests got no human reply within "
    "14 days.",
    "lede": "The state of open-source black holes: how often a stranger's pull request goes unanswered, in the Porchlamp "
    "snapshot of 17 October 2026.",
    "intro": "You send a small fix to a project you use every day. Two weeks later, nobody has replied. At more than 1 in 6 "
    "active open-source projects, that is the normal experience.",
    "stars_answer": "They answer less often, on average. The black-hole share climbs at every step up in stars.",
    "stars_not_charted": "Not charted: 1 judged project with fewer than 1,000 stars.",
    "ecosystems_answer": "PyPI and npm projects go quiet more often than Rust, Go, Java or systems projects.",
    "method 0": "We followed 615,200 pull requests from 148,801 outside contributors to 11,000 GitHub projects, from 20 "
    "April to 17 October 2026.",
    "method 1": "A project is judged only when it is licensed, maintained software with at least 10 counted outside pull "
    "requests.",
    "method 2": "Another 604 licensed, maintained projects with at least 10 outside pull requests on record are not judged "
    "yet: those records were fetched before drafts and closes were recorded, and the rules need both.",
    "method 3": "It is a black hole when, with 95% confidence, more than half of those pull requests got no substantive "
    "reply, review or merge within 14 days.",
    "download": "porchlamp-2026-10-17-ranked.csv: the 4,322 ranked projects with rank, owner, name, ecosystem, score, tier, "
    "coverage, quadrant and the rank in each lens. Released under CC BY 4.0.",
    "cite": "Porchlamp (2026). Stars don't review pull requests: the state of open-source black holes. Snapshot "
    "2026-10-17. https://porchlamp.org/black-holes/2026-10-17/",
    "hashes": f"Data licence: CC BY 4.0. Snapshot database SHA-256: {PUBLISHED_DB_SHA256}. Methodology constants hash: "
    "6e4f3e8e1d0c0fcd829d9c8c386a65179eadb5f936595f96e9eba98712d55b94.",
    "stars_chart_summary": "Black-hole share by stars: 16% of 2,065 projects with 1,000 to 10,000 stars; 25% of 1,307 "
    "projects with 10,000 to 50,000 stars; 28% of 240 projects with 50,000 or more stars.",
    "ecosystems_chart_summary": "Black-hole share by package ecosystem, highest first: PyPI 27.3% of 814; npm 20.8% of "
    "1,078; crates.io (Rust) 16.6% of 488; Go 16.5% of 516; systems (C, C++ and Zig) 15.8% of 476; Maven (Java) 13.7% "
    "of 241.",
}
#: The owner's approved changes (2026-10-17): the intro ends at "nobody has replied." (the lead card states the
#: figure), and every ecosystem carries its plain name from copy.js's one list.
#: Approved with RFC-0026 (2026-10-24, merged as #75): a black hole ignores outside pull requests, so the sentences that
#: count them say what is counted: no human reply, review, merge or close inside the deadline or since.
APPROVED_CHANGES = {
    "finding-share": "In Porchlamp's 2026-10-17 snapshot, 720 of 3,613 active open-source projects (19.9%) left most "
    "outside pull requests with no human reply, review, merge or close within 14 days or since.",
    "method 3": "It is a black hole when, with 95% confidence, more than half of those pull requests got no substantive "
    "reply, review, merge or close within 14 days or since.",
    "intro": "You send a small fix to a project you use every day. Two weeks later, nobody has replied.",
    "ecosystems_answer": "Python and JavaScript projects go quiet more often than Rust, Go, Java or C and C++ projects.",
    "ecosystems_chart_summary": "Black-hole share by package ecosystem, highest first: Python 27.3% of 814; JavaScript "
    "20.8% of 1,078; Rust 16.6% of 488; Go 16.5% of 516; C and C++ 15.8% of 476; Java 13.7% of 241.",
}


def _published_facts() -> report_pages.Facts:
    """The facts of the published snapshot 2026-10-17, as its export read them."""
    ecosystems = (("pypi", 814, 222), ("npm", 1078, 224), ("crates", 488, 81), ("go", 516, 85), ("systems", 476, 75),
                  ("maven", 241, 33))
    bands = (("[1000,10000)", 2065, 329), ("[10000,50000)", 1307, 323), ("[50000,inf)", 240, 68))
    facts = report_pages.Facts(
        snapshot_id="2026-10-17", snapshot_date=date(2026, 10, 17), benchmark_version="2.0.0-rc1",
        constants_hash="6e4f3e8e1d0c0fcd829d9c8c386a65179eadb5f936595f96e9eba98712d55b94",
        manifest_root="f2eb5fc971de3fc0840b3bd1e828f229879a093ed03bfce8980cf0cec503da03",
        canonical_dump_hash="e84ab8e5181fe6f1f0f7483ad08d1021917b1761bcbe4fdc7ec507b1acd6706b",
        code_commit="663e9c781ae31b7424ecb104bd7d6560f21e3e67",
        reproduce_command="python -m porchlamp.snapshot reproduce 2026-10-17",
        judged=3613, black_holes=720,
        ecosystems=tuple(report_pages.EcoRow(key, report_pages.ECOSYSTEM_NAMES[key], n, holes) for key, n, holes in ecosystems),
        bands=tuple(report_pages.BandRow(key, report_pages.band_range_text(key), n, holes) for key, n, holes in bands),
        below_bands_judged=1, lowest_band_text="1,000", ranked=4322, unanswered=38671, counted=49188,
        window_start=date(2026, 4, 20), window_end=date(2026, 10, 17), rho=0.089, top100_overlap=9, top100_black_holes=18,
        corpus_prs=615200, corpus_authors=148801, corpus_projects=11000, undecided_rule_inputs=604, min_n=10,
        deadline_days=14, ci_percent=95,
    )
    report_pages.check_wording_guards(facts)
    return facts


def test_the_report_fixture_is_report_json_of_the_published_snapshot():
    assert json.loads(REPORT_FIXTURE.read_text(encoding="utf-8")) == report_pages.report_json(_published_facts())


def test_the_report_says_what_the_published_report_said_but_for_the_approved_changes():
    """report.json's templates, filled with its values, word every sentence as the published report did, except where
    the owner approved a change; the view adds only copy.js's number-free words."""
    record = report_pages.report_json(_published_facts())
    values = {**record["values"], "source_db_sha256": PUBLISHED_DB_SHA256}
    sentences = record["sentences"]
    said = {}
    for finding in record["findings"]:
        said[f"{finding['id']} lead"] = _fill(finding["lead"], values)
        said[finding["id"]] = _fill(finding["text"], values)
    for key in ("lede", "intro", "stars_answer", "stars_not_charted", "ecosystems_answer", "download", "cite"):
        said[key] = _fill(sentences[key], values)
    said.update({f"method {i}": _fill(text, values) for i, text in enumerate(sentences["method"])})
    said["hashes"] = " ".join(_fill(part, values) for part in sentences["hashes"])
    said["stars_chart_summary"] = sentences["stars_chart_summary"]
    said["ecosystems_chart_summary"] = sentences["ecosystems_chart_summary"]
    assert said == {**PUBLISHED_REPORT, **APPROVED_CHANGES}
    assert all(PUBLISHED_REPORT[key] != text for key, text in APPROVED_CHANGES.items())


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
    assert report_pages.rho_text(-0.0024) == "0.00"       # a value that rounds to zero is not "-0.00"
    assert report_pages.rho_text(-0.0049) == "0.00"
    assert report_pages.rho_text(-0.005) == "-0.01"
    assert report_pages.rho_text(-0.0856) == "-0.09"
    assert report_pages.band_range_text("[1000,10000)") == "1,000 to 10,000"
    assert report_pages.band_range_text("[50000,inf)") == "50,000 or more"


# ---- E3: one page per ranked project (drawn by the app's project view at export), /check/, the project sitemap ---

from porchlamp.serve import project_pages  # noqa: E402


def _ranked(db: Path) -> list[tuple[str, str]]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute("SELECT owner, name FROM repositories WHERE rated = 1 ORDER BY owner, name").fetchall()
    conn.close()
    return rows


def _project_files(site: Path) -> list[Path]:
    return sorted((site / "projects").rglob("index.html"))


def _verify_projects(db: Path, site: Path) -> dict:
    return verify_static_export.verify_projects(db, site, log=lambda _line: None)


def test_every_ranked_project_has_one_page_and_nothing_else_has(built):
    site, db, _stats_path, _stats_dict, holes = built
    ranked = _ranked(db)
    assert len(ranked) > 100
    expected = {f"projects/{o.lower()}/{n.lower()}/index.html" for o, n in ranked}
    found = {p.relative_to(site).as_posix() for p in _project_files(site)}
    assert found == expected
    assert not any("bh-org" in path for path in found), "a black-hole project has no page"


def test_verify_projects_proves_every_value_on_every_page(built):
    site, db, _stats_path, _stats_dict, _holes = built
    result = _verify_projects(db, site)
    assert result["errors"] == []
    assert result["pages"] == len(_ranked(db)) and result["checked"] > 60 * result["pages"]
    assert verify_static_export.check_sitemap(site) == []


def _answers(site: Path) -> list[str]:
    return [
        html.unescape(re.search(r'<p class="project-answer" id="answer" data-v="answer">(.*?)</p>', p.read_text(encoding="utf-8"), re.S).group(1))
        for p in _project_files(site)
    ]


def test_the_four_ways_a_reply_time_and_a_ghost_rate_are_worded_all_occur(built):
    answers = _answers(built[0])
    both = [a for a in answers if "got a first human reply in a median of" in a and "got none within 14 days." in a]
    reply_only = [a for a in answers if "median of" in a and "The share with no reply within 14 days has too small a sample to measure (" in a]
    ghost_only = [a for a in answers if re.match(r"For [^ ]+/[^ ]+, the reply time has too small a sample to measure \(", a)]
    neither = [a for a in answers if re.match(r"For [^ ]+/[^ ]+, the reply time and the share with no reply within 14 days both have too small a sample", a)]
    assert both and reply_only and ghost_only and neither
    assert len(both) + len(reply_only) + len(ghost_only) + len(neither) == len(answers)
    assert all("not enough outside pull requests" not in a for a in answers)
    assert any("It is not ranked for first-time contributors: " in a for a in answers), "the unranked-lens wording occurs"
    assert any(re.search(r"For first-time contributors it ranks [\d,]+ of [\d,]+ \(the (top|bottom) \d+%\)\.", a) for a in answers)
    assert all(re.search(r"It ranks [\d,]+ of [\d,]+ overall\.", a) for a in answers)
    # the counts and the minimum are the record's and the methodology's: the fixture's unmeasured samples are 3 and 4, 10 needed
    assert all("(3 pull requests counted; 10 needed)" in a for a in ghost_only)
    assert all("(4 pull requests counted; 10 needed)" in a for a in reply_only)
    assert all("(3 and 4 pull requests counted; 10 needed)" in a for a in neither)


MIN_N = {"ttfhr_hours": 10, "gr14_ghost_rate": 10}


def _evidence(reply: dict, ghost: dict) -> list[dict]:
    base = {"unmeasured_reason": None, "value": None, "n": 0, "window_start": None, "window_end": None}
    return [{"param_id": "ttfhr_hours", **base, **reply}, {"param_id": "gr14_ghost_rate", **base, **ghost}]


LLAMA = {"owner": "ggml-org", "name": "llama.cpp"}
WINDOW = {"window_start": "2026-04-10T13:41:50+00:00", "window_end": "2026-10-07T13:41:50+00:00"}


def test_a_measured_reply_time_beside_a_small_ghost_sample_names_both_counts_and_never_says_not_enough_requests():
    """llama.cpp: 208 pull requests for the reply time, 6 for the 14-day share (10 needed). The old wording said
    'not enough outside pull requests' next to 208."""
    evidence = _evidence({"value": 4.0904, "n": 208, **WINDOW}, {"value": 0.667, "n": 6, "unmeasured_reason": "insufficient_n", **WINDOW})
    reply = project_pages.answer_reply(LLAMA, evidence, MIN_N)
    assert reply.first == (
        "Outside pull requests to ggml-org/llama.cpp got a first human reply in a median of 4.1 hours "
        "(208 pull requests, 10 April to 7 October 2026).")
    assert reply.rest == "The share with no reply within 14 days has too small a sample to measure (6 pull requests counted; 10 needed)."
    assert reply.short == "Outside pull requests got a first human reply in a median of 4.1 hours."
    assert "not enough outside pull requests" not in reply.first + reply.rest


def test_the_other_unmeasured_wordings_name_the_project_and_use_each_parameters_own_count_and_minimum():
    answer = project_pages.answer_reply
    ghost_only = _evidence({"n": 1, "unmeasured_reason": "insufficient_n"}, {"value": 0.575, "n": 40, **WINDOW})
    assert answer(LLAMA, ghost_only, MIN_N) == project_pages.Reply(
        "For ggml-org/llama.cpp, the reply time has too small a sample to measure (1 pull request counted; 10 needed).",
        "58% of 40 outside pull requests got no reply within 14 days (10 April to 7 October 2026).",
        "The reply time has too small a sample to measure.",
    )
    neither = _evidence({"n": 0, "unmeasured_reason": "insufficient_n"}, {"n": 1, "unmeasured_reason": "insufficient_n"})
    assert answer(LLAMA, neither, MIN_N) == project_pages.Reply(
        "For ggml-org/llama.cpp, the reply time and the share with no reply within 14 days both have too small a sample to "
        "measure (0 and 1 pull request counted; 10 needed).",
        "",
        "The reply time and the share with no reply within 14 days both have too small a sample to measure.",
    )
    assert "(0 and 1 pull request counted; 10 and 5 needed)" in answer(LLAMA, neither, {"ttfhr_hours": 10, "gr14_ghost_rate": 5}).first
    both = _evidence({"value": 6.0, "n": 12, **WINDOW}, {"value": 0.1, "n": 11, **WINDOW})
    assert answer(LLAMA, both, MIN_N) == project_pages.Reply(
        "Outside pull requests to ggml-org/llama.cpp got a first human reply in a median of 6 hours (12 pull requests, "
        "10 April to 7 October 2026); 10% got none within 14 days.",
        "",
        "Outside pull requests got a first human reply in a median of 6 hours; 10% got none within 14 days.",
    )
    with pytest.raises(report_pages.ReportError, match="minimum sample"):
        answer(LLAMA, both, {})


def test_a_value_whose_pr_record_does_not_cover_the_window_says_so_and_names_no_count():
    """RFC-0013: a reply value refused because the collector does not hold the project's whole PR record for the
    window (``collector_failure``) is worded as such, beside each state of the other value; it has no count to name."""
    answer = project_pages.answer_reply
    gone = {"n": None, "unmeasured_reason": "collector_failure"}
    reason = "we do not hold a complete record of its pull requests for the window"
    assert answer(LLAMA, _evidence(gone, gone), MIN_N) == project_pages.Reply(
        f"For ggml-org/llama.cpp, the reply time and the share with no reply within 14 days are not measured: {reason}.",
        "",
        "The reply time and the share with no reply within 14 days are not measured.",
    )
    assert answer(LLAMA, _evidence({"value": 6.0, "n": 12, **WINDOW}, gone), MIN_N) == project_pages.Reply(
        "Outside pull requests to ggml-org/llama.cpp got a first human reply in a median of 6 hours (12 pull requests, "
        "10 April to 7 October 2026).",
        f"The share with no reply within 14 days is not measured: {reason}.",
        "Outside pull requests got a first human reply in a median of 6 hours.",
    )
    assert answer(LLAMA, _evidence(gone, {"value": 0.575, "n": 40, **WINDOW}), MIN_N) == project_pages.Reply(
        f"For ggml-org/llama.cpp, the reply time is not measured: {reason}.",
        "58% of 40 outside pull requests got no reply within 14 days (10 April to 7 October 2026).",
        "The reply time is not measured.",
    )
    assert answer(LLAMA, _evidence(gone, {"n": 3, "unmeasured_reason": "insufficient_n"}), MIN_N).rest == (
        "The share with no reply within 14 days has too small a sample to measure (3 pull requests counted; 10 needed).")
    small_then_gone = answer(LLAMA, _evidence({"n": 4, "unmeasured_reason": "insufficient_n"}, gone), MIN_N)
    assert small_then_gone == project_pages.Reply(
        "For ggml-org/llama.cpp, the reply time has too small a sample to measure (4 pull requests counted; 10 needed).",
        f"The share with no reply within 14 days is not measured: {reason}.",
        "The reply time has too small a sample to measure.",
    )


def test_the_verifier_words_every_reply_state_as_the_page_does():
    """The verifier works out the reply answer on its own: for every pair of states (measured, a too-small sample, an
    incomplete PR record), its three sentences are the page's."""
    states = {
        "measured": {"value": 6.0, "n": 12, **WINDOW},
        "small": {"n": 4, "unmeasured_reason": "insufficient_n"},
        "incomplete": {"n": None, "unmeasured_reason": "collector_failure"},
    }
    for reply_state, reply in states.items():
        for ghost_state, ghost in states.items():
            if ghost_state == "measured":
                ghost = {**ghost, "value": 0.1}
            evidence = _evidence(reply, ghost)
            page = project_pages.answer_reply(LLAMA, evidence, MIN_N)
            assert verify_static_export._expected_reply(LLAMA, evidence, MIN_N) == (page.first, page.rest, page.short), (
                reply_state, ghost_state)


@pytest.mark.parametrize("rank, total, band", [
    # the ends: rank 1 and rank = total
    (1, 2132, "the top 1%"), (2132, 2132, "the bottom 1%"), (3, 4322, "the top 1%"),
    # rank = total / 2 is the upper half's last; with an odd total, the halves meet between two ranks
    (1066, 2132, "the top 50%"), (1067, 2132, "the bottom 50%"), (1065, 2131, "the top 50%"), (1066, 2131, "the bottom 51%"),
    # rounded up: 21 of 2,132 is 0.98% (top 1%), 22 is 1.03% (top 2%); 2,039 is the 94th from the bottom, 4.4%
    (21, 2132, "the top 1%"), (22, 2132, "the top 2%"), (2039, 2132, "the bottom 5%"),
])
def test_a_rank_is_placed_in_the_top_or_bottom_share_rounded_up_so_the_claim_holds(rank, total, band):
    """The top N% in the upper half (rank <= total / 2), else the bottom N%, N rounded up so the claim always holds:
    2,039 of 2,132 is the 94th from the bottom, 4.4% of the list, so "the bottom 4%" would be false."""
    assert project_pages.rank_band(rank, total) == band


def test_the_first_timer_sentence_gives_the_rank_and_its_share_or_the_reason():
    standing = project_pages.Standing
    assert project_pages.first_timer_sentence(standing("first_timer", 2039, 2132, None)) == (
        "For first-time contributors it ranks 2,039 of 2,132 (the bottom 5%).")
    assert project_pages.first_timer_sentence(standing("first_timer", None, 2132, "Not enough evidence to show under this lens.")) == (
        "It is not ranked for first-time contributors: not enough evidence to show under this lens.")
    assert project_pages.rank_sentence(standing("balanced", 2531, 4322, None)) == "It ranks 2,531 of 4,322 overall."


def test_the_head_description_names_the_project_and_its_rank_in_at_most_155_characters():
    describe, standing = project_pages.head_description, project_pages.Standing("balanced", 2531, 4322, None)
    both = project_pages.Reply("", "", "Outside pull requests got a first human reply in a median of 6.2 days; 34% got none within 14 days.")
    assert describe("keras-team/keras", standing, both) == (
        "keras-team/keras ranks 2,531 of 4,322. Outside pull requests got a first human reply in a median of 6.2 days; "
        "34% got none within 14 days.")
    long_name = f"{'a' * 30}/{'b' * 30}"
    clause = describe(long_name, standing, both)
    assert clause == f"{long_name} ranks 2,531 of 4,322. Outside pull requests got a first human reply in a median of 6.2 days."
    longer = f"{'a' * 60}/{'b' * 40}"
    cut = describe(longer, standing, both)
    assert len(cut) <= 155 and cut.startswith(f"{longer} ranks 2,531 of 4,322.") and cut.endswith("\u2026") and " \u2026" not in cut
    for reply in (both, project_pages.Reply("", "", "The reply time has too small a sample to measure.")):
        for name in ("o/n", long_name, longer):
            text = describe(name, standing, reply)
            assert len(text) <= 155 and text.startswith(f"{name} ranks 2,531 of 4,322."), text
    with pytest.raises(report_pages.ReportError, match="needs a ruling"):
        describe("x" * 150, standing, both)


def test_the_export_writes_the_answer_standings_and_similar_projects_into_ranked_records_only(built, project_index):
    site, db, *_ = built
    ranked = set(_ranked(db))
    records = {}
    for path in (site / "data" / SNAPSHOT / "repos").glob("*.json"):
        records.update(json.loads(path.read_text(encoding="utf-8")))
    with_extras = {tuple(key.split("/", 1)) for key, record in records.items() if "answer" in record}
    assert with_extras == ranked and len(records) > len(ranked)
    for key in sorted(with_extras)[:40]:
        record = records["/".join(key)]
        assert set(record) == {"detail", "evidence", "answer", "standings", "similar"}
        standings = {lens: project_index.standing(lens, record["detail"]) for lens in project_pages.LENS_ORDER}
        assert record["standings"] == {lens: {"rank": s.rank, "total": s.total, "reason": s.reason} for lens, s in standings.items()}
        assert record["answer"][-2] == project_pages.rank_sentence(standings["balanced"])
        assert record["answer"][-1] == project_pages.first_timer_sentence(standings["first_timer"])
        assert record["similar"] == [{"owner": r["owner"], "name": r["name"], "rank": r["rank"]} for r in project_index.similar(*key)]
    assert all(set(record) == {"detail", "evidence"} for key, record in records.items() if tuple(key.split("/", 1)) not in ranked)


#: Three real records of the 2026-10-17 export (vllm and keras ranked, 3b1b/manim a black hole), each with its record file.
PROJECT_FIXTURES = ROOT / "tests" / "fixtures" / "project"
_RENDER_BOTH_MODES = """
import { readFileSync } from "node:fs";
import { renderProject } from "%(view)s";
const out = {};
for (const path of JSON.parse(process.argv[1])) {
  const { recordFile, ...record } = JSON.parse(readFileSync(path, "utf8"));
  out[path] = { page: renderProject(record, { mode: "page", recordFile }), panel: renderProject(record, { mode: "panel", recordFile }) };
}
process.stdout.write(JSON.stringify(out));
"""


def _drawn_in_both_modes() -> dict[Path, dict[str, str]]:
    """Each fixture record as views/project.js draws it in page and in panel mode, through Node."""
    import subprocess

    paths = sorted(PROJECT_FIXTURES.glob("*.json"))
    script = _RENDER_BOTH_MODES % {"view": (ROOT / "web" / "app" / "views" / "project.js").as_uri()}
    ran = subprocess.run([export_static.find_node(), "--input-type=module", "-e", script, json.dumps([str(p) for p in paths])],
                         capture_output=True, text=True, check=False)
    assert ran.returncode == 0, ran.stderr[-1500:]
    return {Path(path): modes for path, modes in json.loads(ran.stdout).items()}


def _expected_hooks(record: dict, mode: str) -> dict[str, str]:
    words, labels = verify_static_export._project_copy()
    standings = {lens: (s["rank"], s["total"], s["reason"]) for lens, s in record["standings"].items()} if record.get("standings") else None
    answer = " ".join(record["answer"]) if record.get("answer") else None
    return verify_static_export._expected_project_hooks(
        record["detail"], record["evidence"], standings, record.get("similar"), answer, words, labels, mode=mode)


def _holed(**evidence) -> dict:
    return {"gate_reason": "black_hole", "black_hole_evidence": {
        "unanswered": 12, "population_n": 15, "unanswered_share": 0.8, "wilson_lower_bound": 0.4, "threshold_share": 0.5,
        "min_n": 10, **evidence}}


def test_the_verifier_expects_the_ignored_count_and_share_hooks_only_when_the_evidence_has_them():
    # RFC-0026: the card prints how many counted PRs no maintainer ever touched
    hooks = verify_static_export._black_hole_hooks(_holed(ignored=9, ignored_share=0.6))
    assert hooks["bh.ignored"] == "9" and hooks["bh.ignored_share"] == "60.0"
    assert hooks["bh.wilson_lower_bound"] == "40.0"
    assert "bh.ignored" not in verify_static_export._black_hole_hooks(_holed())
    assert "bh.ignored" not in verify_static_export._black_hole_hooks(_holed(ignored=9))


def test_the_project_view_prints_each_value_as_the_verifier_formats_it_in_page_and_panel_mode():
    drawn = _drawn_in_both_modes()
    assert {p.name for p in drawn} == {"vllm-project__vllm.json", "keras-team__keras.json", "3b1b__manim.json"}
    for path, modes in drawn.items():
        record = json.loads(path.read_text(encoding="utf-8"))
        for mode, markup in modes.items():
            ledger = verify_static_export._Ledger()
            verify_static_export._compare_hooks(ledger, f"{path.name} {mode}", verify_static_export.read_page(markup).hooks, _expected_hooks(record, mode))
            assert ledger.errors == [] and ledger.checked > 40, (ledger.errors[:5], ledger.checked)
    # the black hole's card is among them
    assert "bh.unanswered_share" in _expected_hooks(json.loads((PROJECT_FIXTURES / "3b1b__manim.json").read_text(encoding="utf-8")), "panel")

    # the comparison can fail: one wrong value in the panel is named
    path = PROJECT_FIXTURES / "keras-team__keras.json"
    markup = drawn[path]["panel"]
    planted = re.sub(r'(data-v="evidence\.ttfhr_hours\.value">)([^<]+)', lambda m: m.group(1) + m.group(2).replace("148.3", "148.4"), markup, count=1)
    assert planted != markup
    ledger = verify_static_export._Ledger()
    verify_static_export._compare_hooks(ledger, "keras panel", verify_static_export.read_page(planted).hooks,
                                        _expected_hooks(json.loads(path.read_text(encoding="utf-8")), "panel"))
    assert [e for e in ledger.errors if "'evidence.ttfhr_hours.value'" in e and "148.4" in e], ledger.errors


def test_project_pages_have_a_self_canonical_a_unique_title_an_answer_and_no_placeholder_text(built):
    site = built[0]
    titles, canonicals = [], []
    for path in _project_files(site):
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(site).parent.as_posix() + "/"
        canonical = re.search(r'<link rel="canonical" href="([^"]*)"', text).group(1)
        assert canonical == f"https://porchlamp.org/{rel}"
        canonicals.append(canonical)
        titles.append(re.search(r"<title>(.*?)</title>", text, re.S).group(1))
        answer = re.search(r'<p class="project-answer" id="answer" data-v="answer">(.*?)</p>', text, re.S).group(1)
        assert len(answer) > 40
        visible = re.sub(r"<script.*?</script>", "", text, flags=re.S)
        body = re.search(r"<main.*?</main>", visible, re.S).group(0)
        body = re.sub(r'<section class="project-card" id="similar".*?</section>', "", body, flags=re.S)  # other projects' names are theirs
        owner_name = html.unescape(re.sub(r"<[^>]+>", "", re.search(r'<h1 id="project-title"[^>]*>(.*?)</h1>', text, re.S).group(1)))
        shown = html.unescape(re.sub(r"<[^>]+>", " ", body)).replace(owner_name, "").replace(owner_name.split("/")[1], "")
        assert not re.search(r"\b(?:null|None|NaN|undefined)\b", shown), rel  # a project may be called "null"
        description = html.unescape(re.search(r'<meta name="description" content="([^"]*)"', text).group(1))
        assert 0 < len(description) <= 155 and description.startswith(f"{owner_name} ranks ") and "None" not in description
        # The shared theme script, then one more: Back and Forward between the app's entries at this address load the
        # app (the page is also the project's side panel); nothing else runs, and nothing loads from outside.
        assert script_srcs(text) == list(report_pages.PAGE_SCRIPTS["project"]), rel
        assert external_violations(text.replace('<script type="module" src="/back-forward.js"></script>', "")) == []
        assert "<script" not in visible
        ld = _ld(text)
        assert ld["@type"] == "BreadcrumbList" and [i["position"] for i in ld["itemListElement"]] == [1, 2]
        assert ld["itemListElement"][1]["item"] == canonical
    assert len(set(titles)) == len(titles) == len(_project_files(site))
    assert len(set(canonicals)) == len(canonicals)


def test_a_script_planted_in_a_project_page_turns_the_guard_red(built, monkeypatch):
    site = built[0]
    page = _project_files(site)[0].read_text(encoding="utf-8")
    own = '<script type="module" src="/back-forward.js"></script>'  # the page's own script, after the theme script
    clean = page.replace(own, "")
    assert own in page and external_violations(clean) == []
    planted = clean.replace("</body>", '<script src="https://cdn.example.com/x.js"></script></body>')
    assert external_violations(planted), "the guard did not see the planted script"


def test_the_check_address_is_a_stub_that_sends_a_reader_to_the_rankings(built):
    """``/check/`` is a stub (the header's search finds a project): a meta refresh and the canonical address ``/``, the
    404 page's link to the rankings, no script, no search index beside it, and no place in the sitemap."""
    site = built[0]
    text = _page(site, "check/index.html")
    assert re.findall(r'<meta http-equiv="refresh" content="([^"]*)">', text) == ["0; url=/"]
    assert re.findall(r'<link rel="canonical" href="([^"]*)">', text) == ["https://porchlamp.org/"]
    assert "<script" not in text and script_srcs(text) == []
    assert f'<a href="/">{report_pages.copy_js_string("NOT_FOUND_HOME_LINK")}</a>' in text
    assert not (site / "check" / "projects.json").exists() and not (site / "check.js").exists()
    assert "https://porchlamp.org/check/" not in _page(site, "sitemap-pages.xml")


def test_a_script_planted_in_the_check_stub_or_the_stub_put_in_the_sitemap_turns_the_verifier_red(built, tmp_path):
    site = _copy_site(built[0], tmp_path)
    stub = site / "check" / "index.html"
    stub.write_text(stub.read_text(encoding="utf-8").replace("</body>", '<script src="/app.js"></script>\n</body>'), encoding="utf-8")
    sitemap = site / "sitemap-pages.xml"
    loc = "  <url>\n    <loc>https://porchlamp.org/check/</loc>\n  </url>\n"
    sitemap.write_text(sitemap.read_text(encoding="utf-8").replace("</urlset>", loc + "</urlset>"), encoding="utf-8")
    errors = _verify_projects(built[1], site)["errors"]
    assert [error for error in errors if error.startswith("check stub")] == [
        "check stub scripts: expected [], found ['<script src=\"/app.js\">']",
        "check stub is out of sitemap-pages.xml: expected False, found True",
    ], errors[:5]


def test_no_two_ranked_projects_share_a_lower_case_address(built):
    ranked = _ranked(built[1])
    project_pages.check_no_collisions(ranked)
    with pytest.raises(report_pages.ReportError, match="share the address projects/acme/widget/"):
        project_pages.check_no_collisions([*ranked, ("Acme", "Widget"), ("acme", "WIDGET")])
    with pytest.raises(report_pages.ReportError, match="plain path segment"):
        project_pages.project_path("acme", "..")


#: Names in GitHub's alphabet (letters, digits, `-`, `_` and `.`, any case) that the app and the export must spell the
#: same: upper and mixed case, dots (a leading one, and two together), `_`, `-` and digits.
ADDRESS_EDGE_NAMES = [
    ("Keras-Team", "Keras"), ("socket.io", "socket.io-client"), ("a_b", "c-d.e"), ("acme", ".github"),
    ("x", "y..z"), ("MiXeD-09", "Under_Score.JS"),
]


def _in_the_app(pairs: list[tuple[str, str]], expression: str) -> list:
    """`expression` for each (owner, name) `[o, n]`, run on the app's route module (``web/app/routes.js``, as `r`)."""
    script = (
        'const r = await import(process.argv[1]); const { readFileSync } = await import("node:fs");'
        f'process.stdout.write(JSON.stringify(JSON.parse(readFileSync(0, "utf8")).map(([o, n]) => {expression})));'
    )
    ran = subprocess.run(
        [export_static.find_node(), "--input-type=module", "-e", script, (ROOT / "web" / "app" / "routes.js").as_uri()],
        input=json.dumps(pairs), capture_output=True, text=True, timeout=120, check=True,
    )
    return json.loads(ran.stdout)


def _exported_ranked(site: Path) -> list[tuple[str, str]]:
    """Every ranked project an export holds, in its own spelling: the rows of its balanced list."""
    [board] = site.glob("data/*/leaderboard-balanced.json")
    return [(row["owner"], row["name"]) for row in json.loads(board.read_text(encoding="utf-8"))["rows"]]


def test_the_app_and_the_export_give_every_ranked_project_the_same_address(built):
    """One address rule in two languages: the app's (``routes.js``) and the export's (``project_pages.project_path``)
    agree on every ranked project of an export, each at the page the export wrote, and on the edge cases above. Only
    `built[0]`, the site, is read, so a real export can be checked by calling this with ``(site,)``."""
    site = built[0]
    ranked = _exported_ranked(site)
    assert len(ranked) > 100, site
    pairs = ranked + ADDRESS_EDGE_NAMES
    export_paths = ["/" + project_pages.project_path(owner, name) for owner, name in pairs]
    differ = [(p, e, a) for p, e, a in zip(pairs, export_paths, _in_the_app(pairs, "r.projectPath(o, n)"), strict=True) if e != a]
    assert differ == [], differ[:5]
    unwritten = [path for path in export_paths[: len(ranked)] if not (site / path.lstrip("/") / "index.html").is_file()]
    assert unwritten == [], unwritten[:5]


#: Name parts outside GitHub's alphabet: parentheses, a space, a letter outside ASCII, and the two dot segments.
NAMES_GITHUB_CANNOT_HOLD = [("a(b)", "c"), ("x", "y)"), ("a b", "c"), ("\u00c4", "c"), (".", "x"), ("x", "..")]


def test_neither_the_app_nor_the_export_gives_a_page_to_a_name_github_cannot_hold():
    """Outside GitHub's alphabet the two sides do not spell an address alike, but neither gives the name a page: the
    export refuses it, and the address the app would make for it routes to the app's not-found view."""
    for owner, name in NAMES_GITHUB_CANNOT_HOLD:
        with pytest.raises(report_pages.ReportError, match="plain path segment"):
            project_pages.project_path(owner, name)
    routed = _in_the_app(NAMES_GITHUB_CANNOT_HOLD, "r.matchRoute(r.projectPath(o, n)).route.id")
    assert routed == ["notfound"] * len(NAMES_GITHUB_CANNOT_HOLD)


def test_the_project_sitemap_lists_every_ranked_project_by_rank_with_the_data_date(built, project_index):
    site, db, _stats_path, _stats, _holes = built
    text = _page(site, "sitemap-projects.xml")
    locs = re.findall(r"<loc>([^<]+)</loc>", text)
    assert len(locs) == len(_ranked(db))
    assert locs == [project_pages.project_url(o, n) for o, n in project_index.keys]
    assert len(set(re.findall(r"<lastmod>([^<]+)</lastmod>", text))) == 1
    owner, name = project_index.keys[0]
    assert locs[0] == f"https://porchlamp.org/projects/{owner.lower()}/{name.lower()}/"


def test_the_project_sitemap_has_no_cap_beyond_a_thousand_projects():
    """Search engines find a project page through the sitemap, so every ranked project is listed, in rank order."""
    count = 1_200
    rows = [{"owner": f"owner{i}", "name": f"repo{i}", "rank": i, "ecosystem": "python"} for i in range(1, count + 1)]
    boards = {lens: {"rows": rows, "total": count} for lens in project_pages.LENS_ORDER}
    index = project_pages.ProjectIndex(boards, project_pages.Copy.load())
    text = project_pages.sitemap_projects(index, date(2026, 10, 13))
    locs = re.findall(r"<loc>([^<]+)</loc>", text)
    assert locs == [project_pages.project_url(f"owner{i}", f"repo{i}") for i in range(1, count + 1)]
    assert re.findall(r"<lastmod>([^<]+)</lastmod>", text) == ["2026-10-13"] * count


@pytest.fixture(scope="module")
def project_index(built):
    """The leaderboards of the built fixture, as the exporter holds them, and the index the pages are built from."""
    from fastapi.testclient import TestClient

    from porchlamp.serve.app import create_app

    db = built[1]
    client = TestClient(create_app(str(db), web_dir=str(db.parent / "__none__")))
    boards = {}
    for lens in project_pages.LENS_ORDER:
        rows, page_no = [], 1
        while True:
            body = client.get("/v1/porchlamp/leaderboard", params={"persona": lens, "page": page_no, "page_size": 1000}).json()
            rows.extend(body["rows"])
            if len(rows) >= body["total"] or not body["rows"]:
                break
            page_no += 1
        boards[lens] = {"rows": rows, "total": body["total"]}
    return project_pages.ProjectIndex(boards, project_pages.Copy.load())


# ---- the verifier catches a wrong number, a missing page and a phantom sitemap address ---------------------------

def _copy_site(site: Path, tmp_path: Path) -> Path:
    target = tmp_path / "site"
    shutil.copytree(site, target)
    return target


def test_a_planted_wrong_number_on_a_project_page_fails_verification_naming_page_and_number(built, tmp_path):
    site, db, *_ = built
    copy_dir = _copy_site(site, tmp_path)
    rel = _project_files(copy_dir)[0].relative_to(copy_dir).as_posix()
    text = (copy_dir / rel).read_text(encoding="utf-8")
    rank = re.search(r"It ranks ([\d,]+) of", text).group(1)
    wrong = f"{int(rank.replace(',', '')) + 1:,}"
    (copy_dir / rel).write_text(text.replace(f"It ranks {rank} of", f"It ranks {wrong} of", 1), encoding="utf-8")
    result = _verify_projects(db, copy_dir)
    assert any(rel in e and "'answer'" in e and rank in e and wrong in e for e in result["errors"]), result["errors"][:3]
    (copy_dir / rel).write_text(text, encoding="utf-8")  # restored: green again
    assert _verify_projects(db, copy_dir)["errors"] == []


def test_a_planted_wrong_parameter_pillar_and_lens_cell_each_fail_verification(built, tmp_path):
    site, db, *_ = built
    copy_dir = _copy_site(site, tmp_path)
    path = _project_files(copy_dir)[0]
    rel = path.relative_to(copy_dir).as_posix()
    text = path.read_text(encoding="utf-8")
    pillar = re.search(r'<span data-v="detail.p_responsiveness">([^<]+)</span>', text)
    param = re.search(r'<span data-v="evidence\.[a-z0-9_]+\.n">(\d[\d,]*)</span>', text)
    lens = re.search(r'<span data-v="standings\.career_capital\.rank">([\d,]+)</span>', text) or re.search(
        r'<span data-v="standings\.[a-z_]+\.rank">([\d,]+)</span>', text)
    for what, match in (("pillar", pillar), ("count", param), ("lens", lens)):
        bad = text.replace(match.group(0), match.group(0).replace(match.group(1), "999", 1), 1)
        path.write_text(bad, encoding="utf-8")
        errors = _verify_projects(db, copy_dir)["errors"]
        assert any(rel in e for e in errors), (what, errors[:2])
    path.write_text(text, encoding="utf-8")
    assert _verify_projects(db, copy_dir)["errors"] == []


def test_a_missing_project_page_and_an_extra_one_fail_verification(built, tmp_path):
    site, db, *_ = built
    copy_dir = _copy_site(site, tmp_path)
    victim = _project_files(copy_dir)[0]
    saved = victim.read_text(encoding="utf-8")
    victim.unlink()
    errors = _verify_projects(db, copy_dir)["errors"]
    assert any("project pages in the export" in e for e in errors) and any("is missing" in e for e in errors)
    victim.write_text(saved, encoding="utf-8")
    extra = copy_dir / "projects" / "stranger" / "repo"
    extra.mkdir(parents=True)
    (extra / "index.html").write_text(saved, encoding="utf-8")
    assert any("is not a ranked project" in e for e in _verify_projects(db, copy_dir)["errors"])


def test_a_sitemap_address_with_no_page_fails_the_sitemap_check_and_the_project_list_check(built, tmp_path):
    site, db, *_ = built
    copy_dir = _copy_site(site, tmp_path)
    sitemap = copy_dir / "sitemap-projects.xml"
    original = sitemap.read_text(encoding="utf-8")
    sitemap.write_text(original.replace("</urlset>", "  <url><loc>https://porchlamp.org/projects/ghost/none/</loc></url>\n</urlset>"), encoding="utf-8")
    problems = verify_static_export.check_sitemap(copy_dir)
    assert len(problems) == 1 and "projects/ghost/none/" in problems[0] and "sitemap-projects.xml" in problems[0]
    assert any("sitemap-projects.xml lists" in e for e in _verify_projects(db, copy_dir)["errors"])
    sitemap.write_text(original, encoding="utf-8")
    assert verify_static_export.check_sitemap(copy_dir) == []
    (copy_dir / "sitemap-pages.xml").unlink()
    assert any("sitemap-pages.xml" in p for p in verify_static_export.check_sitemap(copy_dir))


# ---- the words and the numbers ---------------------------------------------------------------------------------

@pytest.mark.parametrize("hours, text", [
    (6.0, "6 hours"), (1.5, "1.5 hours"), (4.0904, "4.1 hours"), (1.0, "1 hour"), (0.96, "58 minutes"), (0.402, "24 minutes"),
    (0.04, "2 minutes"), (0.0166, "1 minute"), (0.001, "under a minute"), (0.999, "1 hour"),
    (47.94, "47.9 hours"), (47.96, "48 hours"), (48.0, "2 days"), (76.8, "3.2 days"), (121.3189, "5.1 days"),
])
def test_reply_time_is_hours_under_48_and_days_after(hours, text):
    assert project_pages.reply_time_text(hours) == text


def test_numbers_are_rounded_the_way_the_sentences_print_them():
    assert project_pages.percent_text(0.575) == "58%" and project_pages.percent_text(0.6667) == "67%"
    assert project_pages.percent_text(0.0) == "0%" and project_pages.percent_text(1.0) == "100%"
    assert project_pages.js_fixed(65.25, 1) == "65.3" and project_pages.js_fixed(76.75, 1) == "76.8"
    assert project_pages.js_fixed(80.83, 2) == "80.83" and project_pages.js_fixed(0.285, 2) == "0.28"  # the double is below .285
    assert project_pages.window_text("2026-04-10T13:41:50+00:00", "2026-10-07T00:00:00+00:00") == "10 April to 7 October 2026"
    assert project_pages.window_text("2025-12-30T00:00:00+00:00", "2026-06-01T00:00:00+00:00") == "30 December 2025 to 1 June 2026"


def test_param_meanings_in_copy_js_cover_exactly_the_methodology_parameters():
    from porchlamp.core import constants

    meanings = report_pages.copy_js_map("PARAM_MEANINGS")
    assert set(meanings) == set(constants.PARAM_INPUT_IDS)
    assert set(meanings) <= set(report_pages.copy_js_map("PARAM_NAMES"))
    assert all(text.endswith(".") and "null" not in text.lower() for text in meanings.values())


def test_the_lens_reasons_and_the_tier_label_are_read_from_copy_js():
    copy = project_pages.Copy.load()
    assert copy.tier_label("B", "B") == "B (capped)" and copy.tier_label("A", "B") == "A" and copy.tier_label(None, None) == "—"
    assert copy.persona_gate_reason("high_barrier_to_entry").startswith("This repository does not show a low enough barrier")
    assert copy.persona_gate_reason("insufficient_evidence:coverage") == "Not enough evidence to show under this lens."
    with pytest.raises(report_pages.ReportError, match="no wording in copy.js"):
        copy.persona_gate_reason("a_code_nobody_wrote_copy_for")


def test_a_reason_the_answer_has_no_wording_for_stops_the_export_instead_of_guessing(built):
    base = {"owner": "o", "name": "n"}
    evidence = [
        {"param_id": "ttfhr_hours", "unmeasured_reason": "third_party_gap", "value": None, "n": 0, "window_start": None, "window_end": None},
        {"param_id": "gr14_ghost_rate", "unmeasured_reason": None, "value": 0.1, "n": 20, "window_start": "2026-04-10", "window_end": "2026-10-07"},
    ]
    with pytest.raises(report_pages.ReportError, match="third_party_gap"):
        project_pages.answer_reply(base, evidence, MIN_N)
    evidence[0].update(unmeasured_reason=None, value=3.0, n=30, window_start=None)
    with pytest.raises(report_pages.ReportError, match="no window"):
        project_pages.answer_reply(base | {"name": "n"}, evidence, MIN_N)


def test_a_lens_without_a_rank_and_without_a_reason_stops_the_export(project_index):
    index = project_index
    hidden = next(k for k in index.keys if k not in index.rank["first_timer"])
    detail = {"owner": hidden[0], "name": hidden[1], "persona_gates": {}}
    with pytest.raises(report_pages.ReportError, match="carries no reason"):
        index.standing("first_timer", detail)


def test_similar_projects_are_the_five_nearest_in_the_same_ecosystem(project_index):
    index = project_index
    owner, name = index.keys[len(index.keys) // 2]
    ecosystem = index.balanced[len(index.keys) // 2]["ecosystem"]
    near = index.similar(owner, name)
    assert len(near) == 5 and all(row["ecosystem"] == ecosystem for row in near)
    assert (owner, name) not in [(r["owner"], r["name"]) for r in near]
    here = len(index.keys) // 2
    places = sorted(i for i, row in enumerate(index.balanced) if row["ecosystem"] == ecosystem and i != here)
    assert [index.balanced[i] for i in sorted(sorted(places, key=lambda i: (abs(i - here), i))[:5])] == near


def _swap_first(text: str, a: str, b: str) -> str:
    """``text`` with the first stand-alone ``a`` and the first stand-alone ``b`` exchanged."""
    def first(token: str) -> re.Match:
        match = re.search(r"(?<![\d,.])" + re.escape(token) + r"(?![\d%]|[,.]\d)", text)
        assert match, (token, text)
        return match
    ma, mb = sorted((first(a), first(b)), key=lambda m: m.start())
    return text[:ma.start()] + mb.group(0) + text[ma.end():mb.start()] + ma.group(0) + text[mb.end():]


@pytest.mark.parametrize("ident, a, b", [
    ("finding-share", "black_holes", "judged"),
    ("finding-top100", "top100_black_holes", "top100_overlap"),
    ("finding-stars", "band1_share", "band3_share"),
    ("finding-prs", "unanswered", "counted"),
])
def test_two_numbers_swapped_inside_a_finding_fail_verification(built, tmp_path, ident, a, b):
    """Two hooks change places inside a finding, each still holding its own value (as a swapped template would draw
    them): every hook agrees with its id, but the sentence is false, and the whole-sentence check names it."""
    site, db, stats_path, _stats, _holes = built
    copy_dir = tmp_path / "site"
    shutil.copytree(site, copy_dir)
    for rel in PAGES[:2]:
        page = (copy_dir / rel).read_text(encoding="utf-8")
        where = r'<p class="report-figure-text">.*?</p>' if ident == "finding-share" else rf'<div class="finding" id="{ident}">.*?</div>'
        sentence = re.search(where, page, re.S).group(0)
        ha, hb = (re.search(rf'<span data-v="{name}">[^<]*</span>', sentence).group(0) for name in (a, b))
        assert ha != hb
        planted = sentence.replace(ha, "\x00").replace(hb, ha).replace("\x00", hb)
        (copy_dir / rel).write_text(page.replace(sentence, planted), encoding="utf-8")
    result = verify_static_export.verify_report(db, stats_path, copy_dir, evidence_files=_evidence_files(built), log=lambda _line: None)
    for rel in PAGES[:2]:
        assert any(error.startswith(f"{rel} #{ident}") for error in result["errors"]), result["errors"]
        assert not any(error.startswith(f"{rel}: hook") for error in result["errors"]), "each hook still holds its own value"


def test_a_stats_file_that_its_producer_does_not_reproduce_fails_verification(built, tmp_path):
    """The star figures are recomputed from the database and the data root, not taken on the file's word."""
    site, db, _stats_path, stats, _holes = built
    planted = copy.deepcopy(stats)
    planted["spearman"]["p_responsiveness"]["rho"] = 0.5 if planted["spearman"]["p_responsiveness"]["rho"] != 0.5 else 0.25
    planted["top100_stars_min"] += 1
    path = tmp_path / "stats.json"
    path.write_text(json.dumps(planted), encoding="utf-8")
    result = verify_static_export.verify_report(db, path, site, evidence_files=_evidence_files(built), log=lambda _line: None)
    named = {error.split(" (")[0] for error in result["errors"]}
    assert named == {"stats file spearman", "stats file top100_stars_min"}, result["errors"]


def test_projects_left_undecided_for_their_records_get_their_own_measurement_bullet(built):
    """The stats file's gate5_undecided_rule_inputs is printed after the "judged only when" bullet, in the words the
    verifier rebuilds on its own; none gives no bullet; a stats file without the count is refused."""
    inputs = _inputs(built)

    def bullets(count):
        inputs["stats"]["gate5_undecided_rule_inputs"] = count
        record = report_pages.report_json(report_pages.build_facts(**inputs))
        return record, [_fill(t, record["values"]) for t in record["sentences"]["method"]]

    record, with_count = bullets(1234)
    assert len(with_count) == 4 and with_count[2] == (
        "Another 1,234 licensed, maintained projects with at least 10 outside pull requests on record are not judged "
        "yet: those records were fetched before drafts and closes were recorded, and the rules need both."
    )
    assert record["values"]["undecided_rule_inputs"] == "1,234"
    record, without = bullets(0)
    assert len(without) == 3 and not any("not judged yet" in bullet for bullet in without)
    assert "undecided_rule_inputs" not in record["values"]
    del inputs["stats"]["gate5_undecided_rule_inputs"]
    with pytest.raises(report_pages.ReportError, match="gate5_undecided_rule_inputs"):
        report_pages.build_facts(**inputs)


def test_projects_whose_pr_record_is_cut_get_their_own_measurement_bullet(built):
    """The stats file's gate5_undecided_cut_record is printed after the rule-inputs bullet, with the one cap the cut
    records stopped at and the window's start; each number is a slot, so the view prints it as a hook. None gives no
    bullet; a stats file without the count, or whose cut records name no single cap, is refused."""
    inputs = _inputs(built)

    def bullets(rule_inputs, cut, caps):
        inputs["stats"].update(
            gate5_undecided_rule_inputs=rule_inputs, gate5_undecided_cut_record=cut, gate5_undecided_cut_record_caps=caps
        )
        facts = report_pages.build_facts(**inputs)
        record = report_pages.report_json(facts)
        return facts, record, [_fill(t, record["values"]) for t in record["sentences"]["method"]]

    facts, record, both = bullets(1234, 868, [500])
    start = f"{facts.window_start.day} {facts.window_start:%B}"
    assert len(both) == 5 and both[2].startswith("Another 1,234 licensed, maintained projects")
    assert both[3] == (
        f"We could not judge 868 busy projects yet: we hold only the newest 500 pull requests of each, and they do not "
        f"reach back to {start}."
    )
    assert (record["values"]["undecided_cut"], record["values"]["pr_cap"]) == ("868", "500")
    assert all(f"{{{slot}}}" in record["sentences"]["method"][3] for slot in ("undecided_cut", "pr_cap", "window_start"))
    _facts, record, alone = bullets(0, 2001, [300])
    assert len(alone) == 4 and alone[2].startswith("We could not judge 2,001 busy projects yet: we hold only the newest 300 ")
    facts, _record, one = bullets(0, 1, [500])
    assert one[2] == (
        f"We could not judge 1 busy project yet: we hold only its newest 500 pull requests, and they do not "
        f"reach back to {facts.window_start.day} {facts.window_start:%B}."
    )
    _facts, record, none = bullets(0, 0, [])
    assert len(none) == 3 and not any("could not judge" in bullet for bullet in none)
    assert "undecided_cut" not in record["values"] and "pr_cap" not in record["values"]
    for caps in ([], [500, 1000], [None], [None, 500], None):
        with pytest.raises(report_pages.ReportError, match="gate5_undecided_cut_record_caps"):
            bullets(0, 7, caps)
    del inputs["stats"]["gate5_undecided_cut_record"]
    with pytest.raises(report_pages.ReportError, match="gate5_undecided_cut_record"):
        report_pages.build_facts(**inputs)


def test_projects_whose_pr_window_is_sampled_get_their_own_measurement_bullet(built):
    """RFC-0013: the stats file's pr_window_sampled is printed before the black-hole definition, with the one cap and
    sample size the sampled records name; each number is a slot. None gives no bullet; a stats file without the
    count, or whose sampled records name no single pair, is refused."""
    inputs = _inputs(built)

    def bullets(sampled, pairs):
        inputs["stats"].update(gate5_undecided_rule_inputs=0, gate5_undecided_cut_record=0,
                               gate5_undecided_cut_record_caps=[], pr_window_sampled=sampled,
                               pr_window_sampled_caps=pairs)
        record = report_pages.report_json(report_pages.build_facts(**inputs))
        return record, [_fill(t, record["values"]) for t in record["sentences"]["method"]]

    record, method = bullets(299, [[2000, 1000]])
    assert len(method) == 4 and method[2] == (
        "299 projects had more than 2,000 pull requests in that time. For each, we read replies and reviews on an "
        "even random sample of 1,000 of them; every pull request counts for decisions and merges."
    )
    assert method[3].startswith("It is a black hole when")
    assert (record["values"]["sampled"], record["values"]["full_max"], record["values"]["sample_size"]) == (
        "299", "2,000", "1,000")
    assert all(f"{{{slot}}}" in record["sentences"]["method"][2] for slot in ("sampled", "full_max", "sample_size"))
    _record, one = bullets(1, [[2000, 1000]])
    assert one[2].startswith("1 project had more than 2,000 pull requests")
    record, none = bullets(0, [])
    assert len(none) == 3 and not any("random sample" in bullet for bullet in none)
    assert not {"sampled", "full_max", "sample_size"} & set(record["values"])
    for pairs in ([], [[2000, 1000], [5000, 1000]], [[2000]], [[2000, None]], None):
        with pytest.raises(report_pages.ReportError, match="pr_window_sampled_caps"):
            bullets(7, pairs)
    del inputs["stats"]["pr_window_sampled"]
    with pytest.raises(report_pages.ReportError, match="pr_window_sampled"):
        report_pages.build_facts(**inputs)


def test_the_verifier_words_the_cut_record_bullet_as_the_report_does(built):
    """The verifier works out the cut-record bullet on its own (its hooks and its sentence, from the stats file's count
    and cap): for the same numbers, its values are report.json's and its method sentences the filled templates, with
    the bullet and without it."""
    inputs = _inputs(built)
    for rule_inputs, cut, caps, sampled, pairs in (
        (1234, 868, [500], 0, []), (0, 2001, [300], 0, []), (0, 0, [], 0, []), (0, 0, [], 299, [[2000, 1000]]),
        (0, 0, [], 1, [[2000, 1000]]),
    ):
        inputs["stats"].update(
            gate5_undecided_rule_inputs=rule_inputs, gate5_undecided_cut_record=cut, gate5_undecided_cut_record_caps=caps,
            pr_window_sampled=sampled, pr_window_sampled_caps=pairs,
        )
        f = report_pages.build_facts(**inputs)
        record = report_pages.report_json(f)
        hooks = verify_static_export.report_hooks(
            sid=f.snapshot_id, judged=f.judged, black_holes=f.black_holes, deadline=f.deadline_days, min_n=f.min_n,
            confidence=f.ci_percent, bands=[(b.key, {"n": b.judged, "black_holes": b.black_holes}) for b in f.bands],
            below=f.below_bands_judged, lowest=int(f.lowest_band_text.replace(",", "")),
            top100_black_holes=f.top100_black_holes, top100_overlap=f.top100_overlap, ranked=f.ranked, rho=f.rho,
            unanswered=f.unanswered, counted=f.counted,
            corpus={"human_outside_prs": f.corpus_prs, "distinct_outside_authors": f.corpus_authors,
                    "derived_repos": f.corpus_projects},
            start=f.window_start, end=f.window_end, undecided=f.undecided_rule_inputs,
            ecosystems=[(e.key, e.judged, e.black_holes) for e in f.ecosystems], names=dict(report_pages.ECOSYSTEM_NAMES),
            constants_hash=f.constants_hash, db_sha="0" * 64,
            undecided_cut=inputs["stats"]["gate5_undecided_cut_record"],
            pr_cap=(inputs["stats"]["gate5_undecided_cut_record_caps"] or [None])[0],
            sampled=sampled, full_max=(pairs or [[None, None]])[0][0], sample_size=(pairs or [[None, None]])[0][1],
        )
        values = {k: v for k, v in hooks.items() if "." not in k and k != "source_db_sha256"}
        assert values == record["values"], (cut, caps)
        method = verify_static_export._report_sentences(hooks, dict(report_pages.ECOSYSTEM_NAMES))["method"]
        assert method == [_fill(t, record["values"]) for t in record["sentences"]["method"]], (cut, caps)
        assert any("could not judge" in sentence for sentence in method) == bool(cut)
        assert any("random sample" in sentence for sentence in method) == bool(sampled)


@pytest.mark.parametrize("rho,printed", [(-0.0024, "0.00"), (0.0049, "0.00"), (-0.005, "-0.01"), (-0.0856, "-0.09"), (0.1255, "0.13")])
def test_report_and_verifier_print_the_correlation_the_same_and_never_as_negative_zero(built, rho, printed):
    inputs = _inputs(built)
    inputs["stats"]["spearman"]["porchlamp"]["rho"] = rho
    f = report_pages.build_facts(**inputs)
    hooks = verify_static_export.report_hooks(
        sid=f.snapshot_id, judged=f.judged, black_holes=f.black_holes, deadline=f.deadline_days, min_n=f.min_n,
        confidence=f.ci_percent, bands=[(b.key, {"n": b.judged, "black_holes": b.black_holes}) for b in f.bands],
        below=f.below_bands_judged, lowest=int(f.lowest_band_text.replace(",", "")),
        top100_black_holes=f.top100_black_holes, top100_overlap=f.top100_overlap, ranked=f.ranked, rho=f.rho,
        unanswered=f.unanswered, counted=f.counted,
        corpus={"human_outside_prs": f.corpus_prs, "distinct_outside_authors": f.corpus_authors, "derived_repos": f.corpus_projects},
        start=f.window_start, end=f.window_end, undecided=f.undecided_rule_inputs,
        ecosystems=[(e.key, e.judged, e.black_holes) for e in f.ecosystems], names=dict(report_pages.ECOSYSTEM_NAMES),
        constants_hash=f.constants_hash, db_sha="0" * 64,
    )
    assert report_pages.report_json(f)["values"]["rho"] == printed == hooks["rho"]


def test_the_report_meta_description_says_what_is_counted_not_unanswered_for_two_weeks():
    description = report_pages.report_description(_published_facts())
    assert "no human reply, review, merge or close within two weeks or since" in description
    assert "unanswered for" not in description


def test_every_black_hole_sentence_says_a_close_counts_as_a_touch():
    """RFC-0026: a project is a black hole when no maintainer replied, reviewed, merged or closed, so the sentences that
    count black holes name all four."""
    record = report_pages.report_json(_published_facts())
    texts = [finding["text"] for finding in record["findings"] if finding["id"] == "finding-share"] + list(record["sentences"]["method"])
    said = [text for text in texts if "black hole" in text or "left most" in text]
    assert said and all("merge or close" in text for text in said), said


# ---- a published project that is no longer ranked keeps its address, with a page that says why -------------------

#: Projects of the fixture that no leaderboard ranks: an archived one, an unrated one, and a black hole.
STUBBED = (("gated-org", "gated-archived"), ("unrated-org", "unrated-plain-0"), ("bh-org", "hole-pypi-0"))
#: A page a past export wrote for a project this snapshot does not hold at all, and for one it still ranks.
GONE = ("ghost-org", "gone-repo")


def _previously_published(root: Path, keys) -> Path:
    for owner, name in keys:
        page = root / "projects" / owner.lower() / name.lower() / "index.html"
        page.parent.mkdir(parents=True)
        page.write_text("<!doctype html><title>published before</title>", encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def delisted(built, tmp_path_factory):
    """``(site, previous site, ranked project that was published before)``: the fixture exported again with the pages a
    past export wrote for the three STUBBED projects, a project the snapshot does not hold, and a ranked one."""
    _site, db, stats_path, _stats, _holes = built
    tmp = tmp_path_factory.mktemp("delisted")
    ranked = _ranked(db)[0]
    previous = _previously_published(tmp / "previous", [*STUBBED, GONE, ranked])
    out = tmp / "site"
    export_static.export(
        db, out, stats_path, snapshot_dir=built[0].parent / "snapshot", data_dir=built[0].parent / "data", previous_site=previous,
    )
    return out, previous, ranked


def _stub_path(site: Path, key) -> Path:
    return site / "projects" / key[0].lower() / key[1].lower() / "index.html"


def test_a_published_project_that_is_no_longer_ranked_keeps_its_address_with_a_page_that_says_why(delisted):
    site, _previous, ranked = delisted
    for key in STUBBED:
        assert _stub_path(site, key).is_file(), key
    assert not _stub_path(site, GONE).exists(), "a project the snapshot does not hold gets no page"
    full = _stub_path(site, ranked).read_text(encoding="utf-8")
    assert "It ranks" in full and "not ranked in the Porchlamp snapshot" not in full, "a ranked project keeps its full page"
    archived = _stub_path(site, STUBBED[0]).read_text(encoding="utf-8")
    assert '<p class="gate-banner project-gate">Archived on GitHub — no active development to evaluate.</p>' in archived
    assert 'id="project-title"' in archived and "gated-archived" in archived
    assert 'class="project-answer"' not in archived and "It ranks" not in archived
    assert 'Not ranked in the Porchlamp snapshot of <span data-v="detail.snapshot_date">14 October 2026</span>.' in archived


def test_a_stub_head_names_the_project_the_reason_and_the_snapshot_and_is_indexable(delisted):
    site, _previous, _ranked_key = delisted
    for key, reason in zip(STUBBED, ("archived on GitHub — no active development to evaluate.",
                                     "not enough measured evidence to rank.", None)):
        text = _stub_path(site, key).read_text(encoding="utf-8")
        who = f"{key[0]}/{key[1]}"
        assert re.search(r"<title>(.*?)</title>", text).group(1) == f"{who}: not ranked in the Porchlamp snapshot of 14 October 2026 | Porchlamp"
        assert f'<link rel="canonical" href="https://porchlamp.org/projects/{key[0].lower()}/{key[1].lower()}/">' in text
        assert 'content="index, follow' in text
        description = html.unescape(re.search(r'<meta name="description" content="([^"]*)"', text).group(1))
        assert description.startswith(f"{who} is not ranked: ") and len(description) <= 155
        if reason:
            assert description == f"{who} is not ranked: {reason}"
        assert f'<meta property="og:description" content="{html.escape(description)}">' in text


def test_a_stub_is_not_in_the_project_sitemap_and_the_ranked_pages_stay_exactly_the_ranked_projects(built, delisted):
    site, _previous, _ranked_key = delisted
    sitemap = (site / "sitemap-projects.xml").read_text(encoding="utf-8")
    for key in STUBBED:
        assert f"/projects/{key[0].lower()}/{key[1].lower()}/" not in sitemap
    assert sitemap.count("<loc>") == len(_ranked(built[1]))


def test_a_black_hole_stub_prints_its_counted_evidence_and_an_unrated_one_its_coverage(delisted):
    site, _previous, _ranked_key = delisted
    hole = _stub_path(site, STUBBED[2]).read_text(encoding="utf-8")
    assert 'data-black-hole-evidence="true"' in hole and 'data-v="bh.unanswered"' in hole and 'data-v="bh.population_n"' in hole
    unrated = _stub_path(site, STUBBED[1]).read_text(encoding="utf-8")
    assert re.search(r'<p class="gate-banner project-gate">Not enough measured evidence to rank \(coverage \d+%, ', unrated)


def _verify_stubs(built, site, previous, **kwargs):
    return verify_static_export.verify_projects(
        built[1], site, evidence_files=_evidence_files(built), previous_site=previous, log=lambda _line: None, **kwargs)


def test_verify_projects_proves_every_stub_and_counts_it_beside_the_ranked_pages(built, delisted):
    site, previous, _ranked_key = delisted
    result = _verify_stubs(built, site, previous)
    assert result["errors"] == []
    assert result["pages"] == len(_ranked(built[1])) + len(STUBBED)


def _plant(site: Path, tmp_path: Path) -> Path:
    return _copy_site(site, tmp_path)


def test_a_missing_stub_fails_verification_by_the_path_a_past_export_published(built, delisted, tmp_path):
    site, previous, _ranked_key = delisted
    copy_dir = _plant(site, tmp_path)
    _stub_path(copy_dir, STUBBED[0]).unlink()
    errors = _verify_stubs(built, copy_dir, previous)["errors"]
    assert any("projects/gated-org/gated-archived" in e and "published before" in e for e in errors), errors[:3]


def test_a_stub_for_a_project_the_snapshot_does_not_hold_or_a_ranked_one_fails_verification(built, delisted, tmp_path):
    site, previous, ranked = delisted
    copy_dir = _plant(site, tmp_path)
    stub = _stub_path(copy_dir, STUBBED[0]).read_text(encoding="utf-8")
    ghost = _stub_path(copy_dir, GONE)
    ghost.parent.mkdir(parents=True)
    ghost.write_text(stub, encoding="utf-8")
    assert any("ghost-org/gone-repo" in e and "not a project in the snapshot" in e for e in _verify_stubs(built, copy_dir, previous)["errors"])
    ghost.unlink()
    ranked_page = _stub_path(copy_dir, ranked)
    saved = ranked_page.read_text(encoding="utf-8")
    ranked_page.write_text(stub, encoding="utf-8")
    assert any(f"projects/{ranked[0].lower()}/{ranked[1].lower()}" in e for e in _verify_stubs(built, copy_dir, previous)["errors"])
    ranked_page.write_text(saved, encoding="utf-8")
    assert _verify_stubs(built, copy_dir, previous)["errors"] == []


@pytest.mark.parametrize("old,new,what", [
    ("Archived on GitHub — no active development to evaluate.", "Archived on GitHub.", "banner"),
    ("is not ranked: archived on GitHub", "is not ranked: a fine project", "description"),
    ("14 October 2026 | Porchlamp</title>", "15 October 2026 | Porchlamp</title>", "title"),
])
def test_a_planted_wrong_reason_or_head_on_a_stub_fails_verification_by_page(built, delisted, tmp_path, old, new, what):
    site, previous, _ranked_key = delisted
    copy_dir = _plant(site, tmp_path)
    path = _stub_path(copy_dir, STUBBED[0])
    text = path.read_text(encoding="utf-8")
    assert old in text, what
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    errors = _verify_stubs(built, copy_dir, previous)["errors"]
    assert any("projects/gated-org/gated-archived" in e for e in errors), (what, errors[:3])
    path.write_text(text, encoding="utf-8")
    assert _verify_stubs(built, copy_dir, previous)["errors"] == []


def test_a_planted_wrong_number_on_a_stub_fails_verification_naming_page_and_hook(built, delisted, tmp_path):
    site, previous, _ranked_key = delisted
    copy_dir = _plant(site, tmp_path)
    path = _stub_path(copy_dir, STUBBED[2])
    text = path.read_text(encoding="utf-8")
    shown = re.search(r'<span data-v="bh.unanswered">(\d+)</span>', text).group(1)
    path.write_text(text.replace(f'data-v="bh.unanswered">{shown}<', f'data-v="bh.unanswered">{int(shown) + 1}<', 1), encoding="utf-8")
    errors = _verify_stubs(built, copy_dir, previous)["errors"]
    assert any("projects/bh-org/hole-pypi-0" in e and "bh.unanswered" in e for e in errors), errors[:3]
    path.write_text(text, encoding="utf-8")
    assert _verify_stubs(built, copy_dir, previous)["errors"] == []


def test_a_stub_in_the_project_sitemap_fails_verification(built, delisted, tmp_path):
    site, previous, _ranked_key = delisted
    copy_dir = _plant(site, tmp_path)
    sitemap = copy_dir / "sitemap-projects.xml"
    sitemap.write_text(sitemap.read_text(encoding="utf-8").replace(
        "</urlset>", "  <url><loc>https://porchlamp.org/projects/gated-org/gated-archived/</loc></url>\n</urlset>"), encoding="utf-8")
    assert any("sitemap-projects.xml lists" in e for e in _verify_stubs(built, copy_dir, previous)["errors"])


def test_previous_pages_without_stats_stop_the_export_instead_of_dropping_the_stubs(built, tmp_path):
    _site, db, _stats_path, _stats, _holes = built
    previous = _previously_published(tmp_path / "previous", [STUBBED[0]])
    with pytest.raises(SystemExit, match="previous-site needs the stats"):
        export_static.export(db, tmp_path / "out", None, previous_site=previous)


def test_an_export_without_previous_pages_says_that_projects_no_longer_ranked_get_no_page(built, tmp_path, capsys):
    _site, db, stats_path, _stats, _holes = built
    export_static.export(db, tmp_path / "out", stats_path, snapshot_dir=built[0].parent / "snapshot", data_dir=built[0].parent / "data")
    assert "no --previous-site" in capsys.readouterr().err


def test_the_stub_description_is_cut_at_a_word_and_never_into_the_name():
    long_name = "o" * 20 + "/" + "n" * 30
    short = project_pages.delisted_description("keras-team/keras", "Archived on GitHub — no active development to evaluate.")
    assert short == "keras-team/keras is not ranked: archived on GitHub — no active development to evaluate."
    cut = project_pages.delisted_description(long_name, "More than half of outside pull requests got no human reply, review or merge on GitHub within 14 days.")
    assert len(cut) <= 155 and cut.startswith(f"{long_name} is not ranked: more than half") and cut.endswith("…")
    assert cut[:-1].rsplit(" ", 1)[0] != cut[:-1], "cut at a word"
    with pytest.raises(report_pages.ReportError, match="needs a ruling"):
        project_pages.delisted_description("o" * 80 + "/" + "n" * 80, "x")


def test_every_reason_a_project_is_not_ranked_for_has_a_sentence_and_an_unknown_one_stops_the_export():
    for code in ("mirror", "archived", "unlicensed", "stale", "black_hole", "non_software", "fork", "no_external_workflow",
                 "insufficient_evidence", "off_platform_routed"):
        assert project_pages.delisted_reason_text(code), code
    with pytest.raises(report_pages.ReportError, match="has no wording"):
        project_pages.delisted_reason_text("made_up")
