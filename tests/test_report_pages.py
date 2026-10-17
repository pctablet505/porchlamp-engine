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
    result = verify_static_export.verify_report(db, stats_path, site, evidence_files=_evidence_files(built), log=lambda _line: None)
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
    result = verify_static_export.verify_report(db, stats_path, copy_dir, evidence_files=_evidence_files(built), log=lambda _line: None)
    assert any(rel in error and "finding-share" in error and wrong in error for error in result["errors"]), result["errors"]


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
    with pytest.raises(report_pages.ReportError, match="--snapshot-dir"):
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
    assert (
        "To check a project, open https://porchlamp.org/projects/&lt;owner&gt;/&lt;name&gt;/ (lower case), "
        'or search on the <a href="../check/">check page</a>.' in page
    )


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


# ---- E3: one static page per ranked project, /check/ and the project sitemap ------------------------------------

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
    assert result["pages"] == len(_ranked(db)) and result["checked"] > 20 * result["pages"]
    assert verify_static_export.check_sitemap(site) == []


def _answers(site: Path) -> list[str]:
    return [
        re.search(r'<p class="answer" id="answer">(.*?)</p>', p.read_text(encoding="utf-8"), re.S).group(1)
        for p in _project_files(site)
    ]


def test_the_four_ways_a_reply_time_and_a_ghost_rate_are_worded_all_occur(built):
    answers = _answers(built[0])
    both = [a for a in answers if "got a first human reply in a median of" in a and "got none within 14 days." in a]
    reply_only = [a for a in answers if "median of" in a and "The share with no reply within 14 days has too small a sample to measure (" in a]
    ghost_only = [a for a in answers if a.startswith("The reply time has too small a sample to measure (")]
    neither = [a for a in answers if a.startswith("The reply time and the share with no reply within 14 days both have too small a sample")]
    assert both and reply_only and ghost_only and neither
    assert len(both) + len(reply_only) + len(ghost_only) + len(neither) == len(answers)
    assert all("not enough outside pull requests" not in a for a in answers)
    assert any("It is not ranked for first-time contributors: " in a for a in answers), "the unranked-lens wording occurs"
    assert all(re.search(r"It ranks [\d,]+ of [\d,]+ overall", a) for a in answers)
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
    first, rest = project_pages.answer_reply(LLAMA, evidence, MIN_N)
    assert first == "Outside pull requests to llama.cpp got a first human reply in a median of 4.1 hours (208 pull requests, 10 April to 7 October 2026)."
    assert rest == "The share with no reply within 14 days has too small a sample to measure (6 pull requests counted; 10 needed)."
    assert "not enough outside pull requests" not in first + rest


def test_the_other_unmeasured_wordings_use_each_parameters_own_count_and_minimum():
    answer = project_pages.answer_reply
    ghost_only = _evidence({"n": 1, "unmeasured_reason": "insufficient_n"}, {"value": 0.575, "n": 40, **WINDOW})
    assert answer(LLAMA, ghost_only, MIN_N) == (
        "The reply time has too small a sample to measure (1 pull request counted; 10 needed).",
        "58% of 40 outside pull requests got no reply within 14 days (10 April to 7 October 2026).",
    )
    neither = _evidence({"n": 0, "unmeasured_reason": "insufficient_n"}, {"n": 1, "unmeasured_reason": "insufficient_n"})
    assert answer(LLAMA, neither, MIN_N)[0] == (
        "The reply time and the share with no reply within 14 days both have too small a sample to measure (0 and 1 pull request counted; 10 needed)."
    )
    assert "(0 and 1 pull request counted; 10 and 5 needed)" in answer(LLAMA, neither, {"ttfhr_hours": 10, "gr14_ghost_rate": 5})[0]
    both = _evidence({"value": 6.0, "n": 12, **WINDOW}, {"value": 0.1, "n": 11, **WINDOW})
    assert answer(LLAMA, both, MIN_N) == (
        "Outside pull requests to llama.cpp got a first human reply in a median of 6 hours (12 pull requests, 10 April to 7 October 2026); 10% got none within 14 days.", "")
    with pytest.raises(report_pages.ReportError, match="minimum sample"):
        answer(LLAMA, both, {})


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
        answer = re.search(r'<p class="answer" id="answer">(.*?)</p>', text, re.S).group(1)
        assert len(answer) > 40
        visible = re.sub(r"<script.*?</script>", "", text, flags=re.S)
        body = re.search(r"<main.*?</main>", visible, re.S).group(0)
        body = re.sub(r'<h2 id="similar">.*?<h2 id="evidence">', "", body, flags=re.S)  # other projects' names are theirs
        shown = re.sub(r"<[^>]+>", " ", body).replace(rel.split("/", 1)[1].rstrip("/"), "")
        owner_name = re.search(r"<h1>(.*?)</h1>", text).group(1)
        shown = shown.replace(owner_name, "").replace(owner_name.split("/")[1], "")  # a project may be called "null"
        assert not re.search(r"\b(?:null|None|NaN|undefined)\b", shown), rel
        description = re.search(r'<meta name="description" content="([^"]*)"', text).group(1)
        assert 0 < len(description) < 160 and "None" not in description
        assert external_violations(text) == [] and "<script" not in visible
        ld = _ld(text)
        assert ld["@type"] == "BreadcrumbList" and [i["position"] for i in ld["itemListElement"]] == [1, 2]
        assert ld["itemListElement"][1]["item"] == canonical
    assert len(set(titles)) == len(titles) == len(_project_files(site))
    assert len(set(canonicals)) == len(canonicals)


def test_a_script_planted_in_a_project_page_turns_the_guard_red(built, monkeypatch):
    site = built[0]
    clean = _project_files(site)[0].read_text(encoding="utf-8")
    assert external_violations(clean) == []
    planted = clean.replace("</body>", '<script src="https://cdn.example.com/x.js"></script></body>')
    assert external_violations(planted), "the guard did not see the planted script"


def test_the_check_page_states_the_pattern_loads_one_local_script_and_lists_every_ranked_project(built):
    site, db, _stats_path, _stats, _holes = built
    text = _page(site, "check/index.html")
    assert "To check a project, open https://porchlamp.org/projects/&lt;owner&gt;/&lt;name&gt;/ (lower case)." in text
    assert re.findall(r'<script[^>]*\ssrc="([^"]*)"', text) == ["../check.js"]
    assert external_violations(text.replace('src="../check.js"', "")) == []
    listed = json.loads(_page(site, "check/projects.json"))
    assert sorted(listed) == sorted(f"{o}/{n}" for o, n in _ranked(db))
    assert (site / "check.js").is_file()
    assert external_violations((site / "check.js").read_text(encoding="utf-8")) == []


def test_no_two_ranked_projects_share_a_lower_case_address(built):
    ranked = _ranked(built[1])
    project_pages.check_no_collisions(ranked)
    with pytest.raises(report_pages.ReportError, match="share the address projects/acme/widget/"):
        project_pages.check_no_collisions([*ranked, ("Acme", "Widget"), ("acme", "WIDGET")])
    with pytest.raises(report_pages.ReportError, match="plain path segment"):
        project_pages.project_path("acme", "..")


def test_the_project_sitemap_lists_the_top_projects_by_rank_with_the_data_date(built):
    site, db, _stats_path, _stats, _holes = built
    text = _page(site, "sitemap-projects.xml")
    locs = re.findall(r"<loc>([^<]+)</loc>", text)
    assert len(locs) == min(len(_ranked(db)), project_pages.SITEMAP_PROJECTS_LIMIT)
    assert len(set(re.findall(r"<lastmod>([^<]+)</lastmod>", text))) == 1
    top_row = json.loads(_page(site, "check/projects.json"))[0]
    assert locs[0] == f"https://porchlamp.org/projects/{top_row.lower()}/"


def test_the_sitemap_limit_cuts_the_list_and_keeps_the_rank_order(project_index):
    index = project_index
    short = project_pages.sitemap_projects(index, date(2026, 10, 13), limit=3)
    assert re.findall(r"<loc>([^<]+)</loc>", short) == [project_pages.project_url(o, n) for o, n in index.keys[:3]]
    assert "<lastmod>2026-10-13</lastmod>" in short


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
    assert any(rel in e and "#answer" in e and rank in e and wrong in e for e in result["errors"]), result["errors"][:3]
    (copy_dir / rel).write_text(text, encoding="utf-8")  # restored: green again
    assert _verify_projects(db, copy_dir)["errors"] == []


def test_a_planted_wrong_parameter_pillar_and_lens_cell_each_fail_verification(built, tmp_path):
    site, db, *_ = built
    copy_dir = _copy_site(site, tmp_path)
    path = _project_files(copy_dir)[0]
    rel = path.relative_to(copy_dir).as_posix()
    text = path.read_text(encoding="utf-8")
    pillar = re.search(r'<th scope="row">Responsiveness</th><td>([^<]+)</td>', text)
    param = re.search(r'<td data-label="Count">(\d[\d,]*)</td>', text)
    lens = re.search(r"<td>Rank ([\d,]+) of", text)
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
    (6.0, "6 hours"), (1.5, "1.5 hours"), (4.0904, "4.1 hours"), (1.0, "1 hour"), (0.96, "1 hour"), (0.04, "under 0.1 hours"),
    (47.94, "47.9 hours"), (47.96, "48 hours"), (48.0, "2 days"), (76.8, "3.2 days"), (121.3189, "5.1 days"),
])
def test_reply_time_is_hours_under_48_and_days_after(hours, text):
    assert project_pages.reply_time_text(hours) == text


def test_numbers_are_rounded_the_way_the_drawer_does():
    assert project_pages.percent_text(0.575) == "58%" and project_pages.percent_text(0.6667) == "67%"
    assert project_pages.percent_text(0.0) == "0%" and project_pages.percent_text(1.0) == "100%"
    assert project_pages.js_fixed(65.25, 1) == "65.3" and project_pages.js_fixed(76.75, 1) == "76.8"
    assert project_pages.js_fixed(80.83, 2) == "80.83" and project_pages.js_fixed(0.285, 2) == "0.28"  # the double is below .285
    assert project_pages.coverage_percent(0.775) == "78%" and project_pages.coverage_percent(0.6) == "60%"
    fmt = project_pages.format_parameter_value
    assert [fmt(v) for v in (None, 0, 5, 5.0, 4.0904, 0.14040000000000002, 0.00001234, "DCO", 121.31894)] == [
        "—", "0", "5", "5", "4.0904", "0.1404", "0.00001234", "DCO", "121.3189"]
    assert project_pages.window_text("2026-04-10T13:41:50+00:00", "2026-10-07T00:00:00+00:00") == "10 April to 7 October 2026"
    assert project_pages.window_text("2025-12-30T00:00:00+00:00", "2026-06-01T00:00:00+00:00") == "30 December 2025 to 1 June 2026"


def test_the_meta_description_is_cut_cleanly_under_160_characters():
    cut = project_pages.meta_description
    short = "Outside pull requests to x got a first human reply in a median of 6 hours; 4% got none within 14 days."
    assert cut(short) == short
    long_name = "a" * 40
    with_bracket = (f"Outside pull requests to {long_name} got a first human reply in a median of 3.2 days "
                    "(1,234 pull requests, 10 April to 7 October 2026); 12% got none within 14 days.")
    assert len(with_bracket) >= 160
    assert cut(with_bracket) == f"Outside pull requests to {long_name} got a first human reply in a median of 3.2 days; 12% got none within 14 days."
    clause = f"Outside pull requests to {'b' * 60} got a first human reply in a median of 3.2 days (1,234 pull requests, 10 April to 7 October 2026); there were not enough outside pull requests to measure the share."
    assert cut(clause).endswith("3.2 days.") and len(cut(clause)) < 160
    word_cut = cut("word " * 60)
    assert len(word_cut) < 160 and word_cut.endswith("…") and " …" not in word_cut


def test_param_meanings_cover_exactly_the_methodology_parameters_and_copy_js_names_them():
    from porchlamp.core import constants

    assert set(project_pages.PARAM_MEANINGS) == set(constants.PARAM_INPUT_IDS)
    assert set(project_pages.PARAM_MEANINGS) <= set(report_pages.copy_js_map("PARAM_NAMES"))
    assert all(text.endswith(".") and "null" not in text.lower() for text in project_pages.PARAM_MEANINGS.values())


def test_the_drawer_wording_is_read_from_copy_js():
    copy = project_pages.Copy.load()
    assert copy.tier_label("B", "B") == "B (capped)" and copy.tier_label("A", "B") == "A" and copy.tier_label(None, None) == "—"
    assert copy.coverage_band(0.775) == "78% · Ceiling B" and copy.coverage_band(0.85) == "85% · Ceiling A"
    assert copy.coverage_band(0.97) == "97% · Full"
    assert copy.missing_value("insufficient_n") == "Not shown: Insufficient sample size"
    assert copy.persona_gate_reason("high_barrier_to_entry").startswith("This repository does not show a low enough barrier")
    assert copy.persona_gate_reason("insufficient_evidence:coverage") == "Not enough evidence to show under this lens."
    assert copy.tier_capped_explanation.startswith("Capped: this tier can go no higher")
    with pytest.raises(report_pages.ReportError, match="no wording in copy.js"):
        copy.persona_gate_reason("a_code_nobody_wrote_copy_for")


def test_a_reason_the_answer_has_no_wording_for_stops_the_export_instead_of_guessing(built):
    base = {"owner": "o", "name": "n"}
    evidence = [
        {"param_id": "ttfhr_hours", "unmeasured_reason": "collector_failure", "value": None, "n": 0, "window_start": None, "window_end": None},
        {"param_id": "gr14_ghost_rate", "unmeasured_reason": None, "value": 0.1, "n": 20, "window_start": "2026-04-10", "window_end": "2026-10-07"},
    ]
    with pytest.raises(report_pages.ReportError, match="collector_failure"):
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


@pytest.mark.parametrize("ident", ["finding-share", "finding-top100", "finding-stars", "finding-prs"])
def test_two_numbers_swapped_inside_a_finding_fail_verification(built, tmp_path, ident):
    """Every number still appears in its sentence, but in the other's place: the sentence is false. Both
    report pages carry the swap, as a swapped template would write it."""
    site, db, stats_path, _stats, _holes = built
    facts = report_pages.build_facts(**_inputs(built))
    b1, _b2, b3 = facts.bands
    a, b = {
        "finding-share": (f"{facts.black_holes:,}", f"{facts.judged:,}"),
        "finding-top100": (str(facts.top100_black_holes), str(facts.top100_overlap)),
        "finding-stars": (report_pages.pct0(b1.black_holes, b1.judged) + "%", report_pages.pct0(b3.black_holes, b3.judged) + "%"),
        "finding-prs": (f"{facts.unanswered:,}", f"{facts.counted:,}"),
    }[ident]
    assert a != b, "the fixture must give two different numbers to swap"
    copy_dir = tmp_path / "site"
    shutil.copytree(site, copy_dir)
    for rel in PAGES[:2]:
        page = (copy_dir / rel).read_text(encoding="utf-8")
        paragraph = re.search(rf'<p class="finding" id="{ident}">.*?</p>', page, re.S).group(0)
        planted = _swap_first(paragraph, a, b)
        assert planted != paragraph
        (copy_dir / rel).write_text(page.replace(paragraph, planted), encoding="utf-8")
    result = verify_static_export.verify_report(db, stats_path, copy_dir, evidence_files=_evidence_files(built), log=lambda _line: None)
    for rel in PAGES[:2]:
        assert any(error.startswith(f"{rel} #{ident}:") for error in result["errors"]), result["errors"]


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


def test_the_hand_written_facts_come_from_the_database_and_agree_with_the_stats(built):
    _site, db, _stats_path, stats, _holes = built
    facts = verify_static_export.hand_written_facts(db, stats)
    assert facts["total"] == facts["ranked"] + facts["gated"] + facts["unrated"] == stats["repos"]
    assert facts["ranked"] == stats["rated"] == facts["lens_ranked"]["balanced"]
    assert (facts["judged"], facts["black_holes"]) == (stats["gate5_decided"], stats["black_holes"])
    assert facts["below_floor"] <= facts["unrated"] and sum(facts["unrated_by_reason"].values()) == facts["unrated"]
    assert len(facts["top"]) == 20 and [row[0] for row in facts["top"]] == [str(n) for n in range(1, 21)]
