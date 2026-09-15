"""Unit tests for :mod:`cvi.verify.live_sample`.

The canary re-fetches GitHub metadata and the PR window head for a sample of
rated rows and holds from promotion any row whose live values drift beyond
tolerance from the recorded verified bundle.
All live traffic goes through the ``Transport`` boundary, which these tests
fake; no test touches the network.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pytest

from cvi.verify.live_sample import (
    LiveFacts,
    LiveSampleReport,
    RatedRow,
    compare_fields,
    default_seed,
    load_rated_rows,
    main,
    parse_live_payload,
    recorded_facts_from_bundle,
    run_live_sample,
    select_sample,
)
from cvi.verify import live_sample as ls


# --------------------------------------------------------------------------- #
# Fixture builders                                                            #
# --------------------------------------------------------------------------- #


def _row(owner: str, ecosystem: str, overall_cvi: float,
         stars: int = 0, forks: int = 0) -> RatedRow:
    return RatedRow(owner=owner, name=owner, ecosystem=ecosystem,
                    stars=stars, forks=forks, overall_cvi=overall_cvi)


def _store_db(path: Path, rows: list[RatedRow]) -> Path:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE repositories ("
        "id INTEGER PRIMARY KEY, owner VARCHAR(100) NOT NULL, "
        "name VARCHAR(100) NOT NULL, ecosystem VARCHAR(50) NOT NULL, "
        "stars INTEGER NOT NULL, forks INTEGER NOT NULL, "
        "overall_cvi REAL NOT NULL, UNIQUE(owner, name))")
    for row in rows:
        conn.execute(
            "INSERT INTO repositories (owner, name, ecosystem, stars, forks, "
            "overall_cvi) VALUES (?, ?, ?, ?, ?, ?)",
            (row.owner, row.name, row.ecosystem, row.stars, row.forks,
             row.overall_cvi))
    conn.commit()
    conn.close()
    return path


def _pr_event(number: int, closed: Optional[str] = None,
              merged: Optional[str] = None) -> dict[str, Any]:
    return {"pr_number": number, "created_at": "2026-09-01T00:00:00+00:00",
            "closed_at": closed, "merged_at": merged, "is_external": True,
            "author_login": "contrib"}


def _bundle_doc(stars: Optional[int], forks: Optional[int],
                pr_events: list[dict[str, Any]],
                owner: str = "org", name: str = "repo",
                ecosystem: str = "pypi") -> dict[str, Any]:
    meta: dict[str, Any] = {"owner": owner, "name": name, "ecosystem": ecosystem}
    if stars is not None:
        meta["stars"] = stars
    if forks is not None:
        meta["forks"] = forks
    return {"bundle": {"meta": meta, "pr_events": pr_events},
            "report": {"clean": True, "verified_at": "2026-09-01T00:00:00+00:00"}}


def _write_verified(data_dir: Path, owner: str, name: str, doc: dict[str, Any]) -> None:
    p = data_dir / "verified" / f"{owner}__{name}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc), encoding="utf-8")


class FakeTransport:
    """Recorded-response transport: canned live facts, call log, error injection."""

    def __init__(self) -> None:
        self.facts: dict[tuple[str, str], LiveFacts] = {}
        self.errors: dict[tuple[str, str], Exception] = {}
        self.calls: list[tuple[str, str]] = []

    def set(self, owner: str, name: str, stars: int, forks: int,
            open_pr_count: int, latest_pr_number: Optional[int]) -> None:
        self.facts[(owner, name)] = LiveFacts(stars=stars, forks=forks,
                                              open_pr_count=open_pr_count,
                                              latest_pr_number=latest_pr_number)

    def fail(self, owner: str, name: str, exc: Exception) -> None:
        self.errors[(owner, name)] = exc

    def fetch_live_facts(self, owner: str, name: str) -> LiveFacts:
        self.calls.append((owner, name))
        if (owner, name) in self.errors:
            raise self.errors[(owner, name)]
        try:
            return self.facts[(owner, name)]
        except KeyError:
            raise RuntimeError(
                f"fake: no recorded live facts for {owner}/{name}") from None


# --------------------------------------------------------------------------- #
# Sample selection                                                            #
# --------------------------------------------------------------------------- #


def _two_ecosystem_population() -> list[RatedRow]:
    rows = [_row(f"a{i:03d}", "A", round(100.0 - i * 0.5, 2)) for i in range(1, 21)]
    rows += [_row(f"b{i:03d}", "B", round(50.0 - i * 0.1, 2)) for i in range(1, 61)]
    return rows


def test_select_sample_top50_plus_random_exact() -> None:
    sample = select_sample(_two_ecosystem_population(), seed=1)
    expected = [f"a{i:03d}" for i in range(1, 21)]
    expected += [f"b{i:03d}" for i in range(1, 51)]
    expected.append("b053")  # seed-1 random draw lands outside the top-50 set
    assert [row.owner for row in sample] == expected


def test_select_sample_seed_deterministic_and_seed_sensitive() -> None:
    pop = _two_ecosystem_population()
    s1 = {row.owner for row in select_sample(pop, seed=1)}
    s1_again = {row.owner for row in select_sample(pop, seed=1)}
    s3 = {row.owner for row in select_sample(pop, seed=3)}
    assert s1 == s1_again
    assert s1 != s3  # seed 3's random draw lands on b056, not b053
    assert "b056" in s3 and "b056" not in s1


def test_select_sample_empty_population() -> None:
    assert select_sample([], seed=1) == ()


def test_select_sample_small_population_keeps_every_row() -> None:
    rows = [_row(f"s{i:03d}", "solo", round(90.0 - i * 0.1, 2)) for i in range(1, 21)]
    sample = select_sample(rows, seed=42)
    assert len(sample) == 20  # top-50 covers all rows; random adds nothing new
    assert [row.owner for row in sample] == [f"s{i:03d}" for i in range(1, 21)]


def test_select_sample_rank_order_is_composite_desc() -> None:
    rows = [_row("low", "A", 10.0), _row("high", "A", 99.0), _row("mid", "A", 50.0)]
    sample = select_sample(rows, seed=7)
    assert [row.owner for row in sample] == ["high", "mid", "low"]


# --------------------------------------------------------------------------- #
# Field comparison rules                                                      #
# --------------------------------------------------------------------------- #


def _recorded(stars: Optional[int] = 1000, forks: Optional[int] = 100,
              open_pr_count: int = 20,
              latest: Optional[int] = 500) -> Any:
    return ls.RecordedFacts(stars=stars, forks=forks, open_pr_count=open_pr_count,
                            latest_pr_number=latest, bundle_path="/tmp/v.json")


def test_compare_fields_all_ok_within_tolerance() -> None:
    checks = compare_fields(
        _recorded(),
        LiveFacts(stars=1200, forks=100, open_pr_count=22, latest_pr_number=505))
    assert all(c.ok for c in checks)
    assert [c.field for c in checks] == ["stars", "forks", "latest_pr_number",
                                         "open_pr_count"]


@pytest.mark.parametrize("live", [900, 901])
def test_stars_drop_within_monotone_tolerance_ok(live: int) -> None:
    checks = compare_fields(_recorded(stars=1000),
                            LiveFacts(stars=live, forks=100, open_pr_count=20,
                                     latest_pr_number=500))
    assert checks[0].ok  # floor for recorded=1000 is 900 exactly


def test_stars_drop_beyond_monotone_tolerance_drift() -> None:
    checks = compare_fields(_recorded(stars=1000),
                            LiveFacts(stars=899, forks=100, open_pr_count=20,
                                     latest_pr_number=500))
    assert not checks[0].ok
    assert "stars" in checks[0].detail


def test_forks_drop_beyond_monotone_tolerance_drift() -> None:
    checks = compare_fields(_recorded(stars=1000, forks=1000),
                            LiveFacts(stars=1000, forks=800, open_pr_count=20,
                                     latest_pr_number=500))
    assert not checks[1].ok


def test_stars_growth_never_drifts() -> None:
    checks = compare_fields(_recorded(stars=100),
                            LiveFacts(stars=99999, forks=100, open_pr_count=20,
                                     latest_pr_number=500))
    assert checks[0].ok


@pytest.mark.parametrize("live,ok", [(499, False), (500, True), (501, True)])
def test_latest_pr_number_monotone_no_tolerance(live: Optional[int], ok: bool) -> None:
    checks = compare_fields(_recorded(latest=500),
                            LiveFacts(stars=1000, forks=100, open_pr_count=20,
                                     latest_pr_number=live))
    assert checks[2].ok is ok


@pytest.mark.parametrize("recorded,live,ok", [
    (100, 50, True),    # diff 50 == max(5, 0.5*100): boundary stays ok
    (100, 49, False),   # diff 51 exceeds the tolerance
    (0, 5, True),       # absolute floor: diff 5 == OPEN_PR_ABS_TOL
    (0, 6, False),      # diff 6 exceeds the floor
    (8, 0, False),      # diff 8 > max(5, 4)=5: drift
    (8, 1, False),      # diff 7 > 5: drift
    (8, 10, True),      # diff 2 <= 5: ok
])
def test_open_pr_count_tolerances(recorded: int, live: int, ok: bool) -> None:
    checks = compare_fields(_recorded(open_pr_count=recorded),
                            LiveFacts(stars=1000, forks=100, open_pr_count=live,
                                     latest_pr_number=500))
    assert checks[3].ok is ok


def test_compare_fields_recorded_absent_is_single_source_ok() -> None:
    checks = compare_fields(_recorded(stars=None, forks=None, latest=None),
                            LiveFacts(stars=1000, forks=100, open_pr_count=20,
                                     latest_pr_number=500))
    assert checks[0].ok and "absent" in checks[0].detail
    assert checks[1].ok and checks[2].ok


# --------------------------------------------------------------------------- #
# Recorded facts from verified bundles                                        #
# --------------------------------------------------------------------------- #


def test_recorded_facts_from_bundle_exact() -> None:
    doc = _bundle_doc(250044, 51329, [
        _pr_event(37607), _pr_event(37605), _pr_event(37603, closed="2026-09-02T00:00:00Z"),
        _pr_event(37500, merged="2026-09-02T00:00:00Z")])
    facts = recorded_facts_from_bundle(doc, Path("/data/verified/o__r.json"))
    assert facts.stars == 250044
    assert facts.forks == 51329
    assert facts.open_pr_count == 2
    assert facts.latest_pr_number == 37607
    assert facts.bundle_path == "/data/verified/o__r.json"


def test_recorded_facts_empty_pr_events() -> None:
    doc = _bundle_doc(100, 5, [])
    facts = recorded_facts_from_bundle(doc, Path("/v.json"))
    assert facts.open_pr_count == 0
    assert facts.latest_pr_number is None


def test_recorded_facts_nothing_recorded_is_none() -> None:
    assert recorded_facts_from_bundle({"bundle": {"meta": {}, "pr_events": []}},
                                      Path("/v.json")) is None


# --------------------------------------------------------------------------- #
# parse_live_payload (recorded GitHub GraphQL responses)                      #
# --------------------------------------------------------------------------- #


def _gh_payload(stars: int, forks: int, nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return {"data": {"r": {"stargazerCount": stars, "forkCount": forks,
                           "pullRequests": {"nodes": nodes}}}}


def _node(number: int, closed: Optional[str] = None,
          merged: Optional[str] = None) -> dict[str, Any]:
    return {"number": number, "closedAt": closed, "mergedAt": merged}


def test_parse_live_payload_exact() -> None:
    payload = _gh_payload(250311, 51400, [
        _node(37620), _node(37619), _node(37618, closed="2026-09-12T00:00:00Z"),
        _node(37500, closed="2026-09-02T00:00:00Z", merged="2026-09-02T00:00:00Z")])
    facts = parse_live_payload(payload)
    assert facts.stars == 250311
    assert facts.forks == 51400
    assert facts.open_pr_count == 2
    assert facts.latest_pr_number == 37620


def test_parse_live_payload_no_prs() -> None:
    payload = _gh_payload(10, 2, [])
    facts = parse_live_payload(payload)
    assert facts.open_pr_count == 0
    assert facts.latest_pr_number is None


def test_parse_live_payload_missing_repo_raises() -> None:
    with pytest.raises(RuntimeError, match="no repository"):
        parse_live_payload({"data": {"r": None}})


def test_parse_live_payload_graphql_errors_raise() -> None:
    with pytest.raises(RuntimeError, match="errors"):
        parse_live_payload({"errors": [{"message": "rate limited"}], "data": None})


# --------------------------------------------------------------------------- #
# run_live_sample: drift holds the row, clean promotes                        #
# --------------------------------------------------------------------------- #


def _run(pop: list[RatedRow], fake: FakeTransport, data_dir: Path, seed: int = 1,
         max_live: Optional[int] = None) -> LiveSampleReport:
    return run_live_sample(pop, fake, data_dir, seed, max_live=max_live)


def test_run_no_drift_all_promoted(tmp_path: Path) -> None:
    rows = [_row("org", "pypi", 90.0, stars=1000, forks=100),
            _row("org2", "pypi", 80.0, stars=2000, forks=200)]
    fake = FakeTransport()
    fake.set("org", "org", 1100, 100, 2, 501)
    fake.set("org2", "org2", 2000, 210, 0, 900)
    _write_verified(tmp_path, "org", "org",
                    _bundle_doc(1000, 100, [_pr_event(500)], owner="org", name="org"))
    _write_verified(tmp_path, "org2", "org2",
                    _bundle_doc(2000, 200, [_pr_event(900), _pr_event(899)],
                                owner="org2", name="org2"))
    report = _run(rows, fake, tmp_path)
    assert report.verdicts[0].verdict == "ok"
    assert report.verdicts[1].verdict == "ok"
    assert report.held == ()
    assert [v.owner for v in report.promoted] == ["org", "org2"]
    assert report.drift_count == 0
    assert report.live_requests == 2
    assert fake.calls == [("org", "org"), ("org2", "org2")]


def test_run_stars_drift_in_recorded_response_holds_row(tmp_path: Path) -> None:
    rows = [_row("org", "pypi", 90.0, stars=250000, forks=50000),
            _row("org2", "pypi", 80.0, stars=2000, forks=200)]
    fake = FakeTransport()
    fake.set("org", "org", 200000, 50000, 1, 500)   # 20% below recorded: drift
    fake.set("org2", "org2", 2000, 200, 1, 900)
    _write_verified(tmp_path, "org", "org",
                    _bundle_doc(250000, 50000, [_pr_event(500)], owner="org", name="org"))
    _write_verified(tmp_path, "org2", "org2",
                    _bundle_doc(2000, 200, [_pr_event(900)], owner="org2", name="org2"))
    report = _run(rows, fake, tmp_path)
    drifted = report.verdicts[0]
    assert drifted.verdict == "source_drift"
    assert [c.field for c in drifted.checks if not c.ok] == ["stars"]
    assert report.verdicts[1].verdict == "ok"
    assert [v.owner for v in report.held] == ["org"]
    assert [v.owner for v in report.promoted] == ["org2"]
    assert report.drift_count == 1


def test_run_latest_pr_backwards_holds_row(tmp_path: Path) -> None:
    rows = [_row("org", "pypi", 90.0)]
    fake = FakeTransport()
    fake.set("org", "org", 1000, 100, 1, 850)  # recorded latest is 900
    _write_verified(tmp_path, "org", "org",
                    _bundle_doc(1000, 100, [_pr_event(900)], owner="org", name="org"))
    report = _run(rows, fake, tmp_path)
    assert report.verdicts[0].verdict == "source_drift"
    assert [c.field for c in report.verdicts[0].checks if not c.ok] == ["latest_pr_number"]
    assert report.held == report.verdicts


def test_run_open_pr_disagreement_holds_row(tmp_path: Path) -> None:
    rows = [_row("org", "pypi", 90.0)]
    fake = FakeTransport()
    fake.set("org", "org", 1000, 100, 10, 500)  # recorded open is 80
    _write_verified(tmp_path, "org", "org",
                    _bundle_doc(1000, 100, [_pr_event(500 - i) for i in range(80)],
                                owner="org", name="org"))
    report = _run(rows, fake, tmp_path)
    assert report.verdicts[0].verdict == "source_drift"
    assert [c.field for c in report.verdicts[0].checks if not c.ok] == ["open_pr_count"]


def test_run_missing_record_holds_without_live_fetch(tmp_path: Path) -> None:
    rows = [_row("org", "pypi", 90.0), _row("org2", "pypi", 80.0)]
    fake = FakeTransport()
    fake.set("org2", "org2", 2000, 200, 1, 900)
    _write_verified(tmp_path, "org2", "org2",
                    _bundle_doc(2000, 200, [_pr_event(900)], owner="org2", name="org2"))
    report = _run(rows, fake, tmp_path)
    assert report.verdicts[0].verdict == "record_missing"
    assert [v.owner for v in report.held] == ["org"]
    assert fake.calls == [("org2", "org2")]  # no fetch for the unrecorded row
    assert report.live_requests == 1


def test_run_fetch_failure_holds_row(tmp_path: Path) -> None:
    rows = [_row("org", "pypi", 90.0)]
    fake = FakeTransport()
    fake.fail("org", "org", RuntimeError("gh api rc=1: 403 rate limited"))
    _write_verified(tmp_path, "org", "org",
                    _bundle_doc(1000, 100, [_pr_event(500)], owner="org", name="org"))
    report = _run(rows, fake, tmp_path)
    assert report.verdicts[0].verdict == "live_fetch_failed"
    assert "rate limited" in report.verdicts[0].detail
    assert report.held == report.verdicts
    assert report.drift_count == 0  # a fetch failure is not a source disagreement


def test_run_max_live_truncates_ranked_sample(tmp_path: Path) -> None:
    rows = [_row(f"r{i:03d}", "solo", round(100.0 - i * 0.5, 2), stars=10, forks=1)
            for i in range(1, 61)]
    fake = FakeTransport()
    for i in (1, 2, 3):
        fake.set(f"r{i:03d}", f"r{i:03d}", 10, 1, 0, 1)
        _write_verified(tmp_path, f"r{i:03d}", f"r{i:03d}",
                        _bundle_doc(10, 1, [_pr_event(1)],
                                    owner=f"r{i:03d}", name=f"r{i:03d}"))
    report = _run(rows, fake, tmp_path, seed=42, max_live=3)
    assert [v.owner for v in report.verdicts] == ["r001", "r002", "r003"]
    assert fake.calls == [("r001", "r001"), ("r002", "r002"), ("r003", "r003")]
    assert report.population == 60


def test_run_report_counts_mixed(tmp_path: Path) -> None:
    rows = [_row(f"o{i}", "pypi", 90.0 - i) for i in range(5)]
    fake = FakeTransport()
    for i in range(2):  # ok
        fake.set(f"o{i}", f"o{i}", 100, 10, 1, 10)
        _write_verified(tmp_path, f"o{i}", f"o{i}",
                        _bundle_doc(100, 10, [_pr_event(10)], owner=f"o{i}", name=f"o{i}"))
    fake.set("o2", "o2", 50, 10, 1, 10)  # stars drift
    _write_verified(tmp_path, "o2", "o2",
                    _bundle_doc(100, 10, [_pr_event(10)], owner="o2", name="o2"))
    # o3: no bundle; o4: fetch failure
    fake.fail("o4", "o4", RuntimeError("boom"))
    _write_verified(tmp_path, "o4", "o4",
                    _bundle_doc(100, 10, [_pr_event(10)], owner="o4", name="o4"))
    report = _run(rows, fake, tmp_path)
    assert report.drift_count == 1
    assert report.live_requests == 4  # every verdict except record_missing
    assert len(report.held) == 3
    assert len(report.promoted) == 2


def test_report_to_dict_round_trip(tmp_path: Path) -> None:
    rows = [_row("org", "pypi", 90.0)]
    fake = FakeTransport()
    fake.set("org", "org", 900, 100, 20, 500)
    _write_verified(tmp_path, "org", "org",
                    _bundle_doc(1000, 100, [_pr_event(500)], owner="org", name="org"))
    report = _run(rows, fake, tmp_path)
    doc = json.loads(json.dumps(report.to_dict()))
    assert doc["held"] == ["org/org"]
    assert doc["verdicts"][0]["verdict"] == "source_drift"
    assert doc["verdicts"][0]["checks"][0]["recorded"] == 1000
    assert doc["verdicts"][0]["checks"][0]["live"] == 900


# --------------------------------------------------------------------------- #
# Store loading, seed, CLI                                                    #
# --------------------------------------------------------------------------- #


def test_load_rated_rows_rank_order(tmp_path: Path) -> None:
    rows = [_row("b", "A", 50.0, stars=5, forks=1),
            _row("a", "A", 90.0, stars=9, forks=2),
            _row("c", "B", 90.0, stars=7, forks=3)]
    path = _store_db(tmp_path / "cvi.db", rows)
    loaded = load_rated_rows(str(path))
    assert [r.owner for r in loaded] == ["a", "c", "b"]  # cvi desc, then owner
    assert loaded[0].stars == 9 and loaded[0].forks == 2


def test_default_seed_is_utc_date() -> None:
    assert default_seed(datetime(2026, 9, 12, 23, 59, tzinfo=timezone.utc)) == 20260912


def test_cli_main_held_exit_code_and_verdicts_file(tmp_path: Path, capsys) -> None:
    rows = [_row("ok1", "pypi", 90.0, stars=100, forks=10),
            _row("ok2", "pypi", 80.0, stars=200, forks=20),
            _row("drift", "pypi", 70.0, stars=300, forks=30)]
    db = _store_db(tmp_path / "cvi.db", rows)
    fake = FakeTransport()
    fake.set("ok1", "ok1", 100, 10, 1, 10)
    fake.set("ok2", "ok2", 200, 20, 2, 20)
    fake.set("drift", "drift", 200, 30, 3, 30)  # 33% below recorded 300
    pr_events = {"ok1": [_pr_event(10)],
                 "ok2": [_pr_event(20, closed="2026-09-02T00:00:00Z")],
                 "drift": [_pr_event(30)]}
    for owner in ("ok1", "ok2", "drift"):
        _write_verified(tmp_path, owner, owner,
                        _bundle_doc({"ok1": 100, "ok2": 200, "drift": 300}[owner],
                                    {"ok1": 10, "ok2": 20, "drift": 30}[owner],
                                    pr_events[owner], owner=owner, name=owner))
    out = tmp_path / "canary" / "verdicts-42.json"
    rc = main(["--live-sample", "--db", str(db), "--data-dir", str(tmp_path),
               "--seed", "42", "--out", str(out)], transport=fake)
    assert rc == 1  # a held row fails the run
    printed = capsys.readouterr().out
    assert "drift/drift" in printed and "source_drift" in printed
    assert "live requests: 3" in printed
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["held"] == ["drift/drift"]
    assert doc["promoted"] == ["ok1/ok1", "ok2/ok2"]
    assert doc["drift_count"] == 1


def test_cli_main_all_ok_exit_zero(tmp_path: Path, capsys) -> None:
    rows = [_row("ok1", "pypi", 90.0, stars=100, forks=10)]
    db = _store_db(tmp_path / "cvi.db", rows)
    fake = FakeTransport()
    fake.set("ok1", "ok1", 101, 10, 1, 10)
    _write_verified(tmp_path, "ok1", "ok1",
                    _bundle_doc(100, 10, [_pr_event(10)], owner="ok1", name="ok1"))
    rc = main(["--live-sample", "--db", str(db), "--data-dir", str(tmp_path),
               "--seed", "42"], transport=fake)
    printed = capsys.readouterr().out
    assert rc == 0
    assert "ok1/ok1" in printed and "ok" in printed
    verdicts = tmp_path / "canary" / "verdicts-42.json"
    assert json.loads(verdicts.read_text(encoding="utf-8"))["held"] == []


def test_cli_main_missing_db_is_config_error(tmp_path: Path, capsys) -> None:
    rc = main(["--live-sample", "--db", str(tmp_path / "nope.db"),
               "--data-dir", str(tmp_path), "--seed", "42"], transport=FakeTransport())
    assert rc == 2
    assert "no such store" in capsys.readouterr().out


def test_cli_main_missing_seed_is_config_error(tmp_path: Path, capsys) -> None:
    rows = [_row("ok1", "pypi", 90.0, stars=100, forks=10)]
    db = _store_db(tmp_path / "cvi.db", rows)
    rc = main(["--live-sample", "--db", str(db), "--data-dir", str(tmp_path)],
              transport=FakeTransport())
    assert rc == 2
    assert "--seed is required" in capsys.readouterr().out
