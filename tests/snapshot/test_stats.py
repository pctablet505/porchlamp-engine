"""``porchlamp.snapshot.stats``: the report's star figures and corpus totals, checked against values worked
out by hand on a nine-project snapshot (not recomputed by the code under test)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from porchlamp.snapshot import cli
from porchlamp.snapshot.stats import StatsError, compute_stats

AS_OF = "2026-10-13T00:00:00+00:00"
OLD, YOUNG = "2026-09-01T00:00:00Z", "2026-10-10T00:00:00Z"  # 42 and 3 days before AS_OF

#: name: (gate_reason, gate_undecided, rated, p_responsiveness, score, rank, ttfhr_p75, stars or None)
PROJECTS = {
    "a": (None, "[]", 1, 50.0, 70.0, 1, 10.0, 60_000),
    "b": (None, "[]", 1, 60.0, 60.0, 2, 20.0, 20_000),
    "c": (None, "[]", 1, 70.0, 50.0, 3, 30.0, 5_000),
    "d": ("black_hole", "[]", 0, None, None, None, None, 70_000),
    "e": ("black_hole", "[]", 0, None, None, None, None, 15_000),
    "f": ("unrated: no_external_workflow", "[]", 0, None, None, None, None, 2_000),
    "g": ("stale", "[]", 0, None, None, None, None, 90_000),  # gated before gate 5: not judged
    "h": (None, '["D-GATE-5"]', 0, None, None, None, None, 500),  # gate 5 undecided: not judged
    "i": (None, "[]", 0, None, None, None, None, None),  # judged, but no star count
}


def _event(login: str, created: str = OLD, *, external: bool = True, merged: str | None = None,
           reviewed: str | None = None) -> dict:
    return {"author_login": login, "is_external": external, "created_at": created,
            "merged_at": merged, "first_human_review_at": reviewed}


def _snapshot(tmp_path: Path) -> tuple[Path, Path]:
    db = tmp_path / "snapshot.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE snapshots (id TEXT, as_of TEXT)")
    conn.execute("INSERT INTO snapshots VALUES ('S1', ?)", (AS_OF,))
    conn.execute(
        "CREATE TABLE repositories (snapshot_id TEXT, owner TEXT, name TEXT, gate_reason TEXT, gate_undecided TEXT, "
        "rated INTEGER, p_responsiveness REAL, porchlamp REAL, rank INTEGER, ttfhr_p75 REAL)"
    )
    for name, (gate, undecided, rated, p, score, rank, ttfhr, _stars) in PROJECTS.items():
        conn.execute("INSERT INTO repositories VALUES ('S1', 'Org', ?, ?, ?, ?, ?, ?, ?, ?)",
                     (name, gate, undecided, rated, p, score, rank, ttfhr))
    conn.commit()
    conn.close()

    data = tmp_path / "data"
    meta, derived = data / "raw" / "github_meta", data / "derived"
    meta.mkdir(parents=True)
    derived.mkdir()
    for name, row in PROJECTS.items():
        if row[-1] is not None:
            (meta / f"Org__{name}.json").write_text(json.dumps({"data": {"stars": row[-1]}}), encoding="utf-8")
    # d: 10 outside human PRs old enough to judge, 6 unanswered (one answered after 15 days), 4 merged
    # within 14 days; a bot's PR and a 3-day-old PR are not judged. A black hole once bots are removed.
    d_events = [_event(f"u{k}") for k in range(5)]
    d_events.append(_event("u5", reviewed="2026-09-16T00:00:00Z"))
    d_events += [_event(f"u{k}", merged="2026-09-05T00:00:00Z") for k in range(6, 10)]
    d_events += [_event("dependabot[bot]"), _event("U0", YOUNG)]
    # a: 10 outside PRs, each reviewed after a day, plus a member's PR (not outside)
    a_events = [_event(f"v{k}", reviewed="2026-09-02T00:00:00Z") for k in range(10)]
    a_events.append(_event("maintainer", external=False))
    (derived / "Org__d.json").write_text(json.dumps({"pr_events": d_events}), encoding="utf-8")
    (derived / "Org__a.json").write_text(json.dumps({"pr_events": a_events}), encoding="utf-8")
    (derived / "Org__zz.json").write_text("{not json", encoding="utf-8")  # unreadable: not counted
    return db, data


def test_every_field_matches_the_hand_worked_values(tmp_path):
    db, data = _snapshot(tmp_path)
    stats = compute_stats(db, data)
    assert {k: v for k, v in stats.items() if k != "top100_stars_fate"} == {
        "db": str(db), "snapshot_id": "S1", "as_of": AS_OF, "score_column": "porchlamp",
        "repos": 9, "repos_with_stars": 8, "rated": 3,
        # judged: a, b, c, d, e, f, i; black holes d and e
        "gate5_decided": 7, "black_holes": 2, "black_hole_share": 0.2857,
        "by_stars": {  # i has no star count and sits in no band; nothing judged is under 1,000
            "[1000,10000)": {"n": 2, "black_holes": 0, "share": 0.0},
            "[10000,50000)": {"n": 2, "black_holes": 1, "share": 0.5},
            "[50000,inf)": {"n": 2, "black_holes": 1, "share": 0.5},
        },
        "rated_with_stars": 3,
        "spearman": {"p_responsiveness": {"n": 3, "rho": -1.0}, "porchlamp": {"n": 3, "rho": 1.0},
                     "ttfhr_p75": {"n": 3, "rho": -1.0}},
        "top100_overlap": 3, "top100_stars_min": -1,
        "bots_excluded": {"n": 2, "black_holes": 1, "share": 0.5, "by_stars": {"[50000,inf)": {"n": 2, "share": 0.5}}},
        "corpus": {"derived_repos": 2, "human_outside_prs": 21, "distinct_outside_authors": 20},
    }
    # most first; ties keep the order of the most-starred project that has them
    assert list(stats["top100_stars_fate"].items()) == [
        ("rated", 3), ("black_hole", 2), ("unrated", 2), ("stale", 1), ("unrated: no_external_workflow", 1)]


def test_as_of_moves_the_age_cut(tmp_path):
    """At an as_of 10 days after the PRs opened, none of them is old enough to judge."""
    db, data = _snapshot(tmp_path)
    stats = compute_stats(db, data, as_of="2026-09-11T00:00:00+00:00")
    assert stats["as_of"] == "2026-09-11T00:00:00+00:00"
    assert stats["bots_excluded"] == {"n": 0, "black_holes": 0, "share": None, "by_stars": {}}


def test_the_cli_prints_the_same_stats(tmp_path, capsys):
    db, data = _snapshot(tmp_path)
    assert cli.main(["stats", str(db), "--data-dir", str(data)]) == 0
    assert json.loads(capsys.readouterr().out) == compute_stats(db, data)


def test_a_database_with_an_unmerged_write_ahead_log_is_refused(tmp_path, capsys):
    db, data = _snapshot(tmp_path)
    db.with_name(db.name + "-wal").write_bytes(b"\0" * 32)
    with pytest.raises(StatsError, match="write-ahead log"):
        compute_stats(db, data)
    assert cli.main(["stats", str(db), "--data-dir", str(data)]) == 2
    assert "write-ahead log" in capsys.readouterr().out
