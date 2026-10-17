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
EARLY = "2026-04-01T00:00:00Z"  # before AS_OF's 180-day PR window opens (2026-04-16)

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


def _pr(login: str, created: str = OLD, *, association: str = "CONTRIBUTOR", typename: str = "User") -> dict:
    """One recorded ``pr_window`` node, with the fields the corpus count reads."""
    return {"createdAt": created, "authorAssociation": association,
            "author": {"__typename": typename, "login": login}}


def _event(login: str) -> dict:
    """One v1 ``pr_events`` entry: the 30-newest sample the corpus must not count."""
    return {"author_login": login, "is_external": True, "created_at": OLD,
            "merged_at": None, "first_human_review_at": None}


def _snapshot(tmp_path: Path, *, h_population: int = 12) -> tuple[Path, Path]:
    """The nine projects; ``h_population`` is the population of h's measured gate-5 share."""
    db = tmp_path / "snapshot.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE snapshots (id TEXT, as_of TEXT)")
    conn.execute("INSERT INTO snapshots VALUES ('S1', ?)", (AS_OF,))
    conn.execute(
        "CREATE TABLE repositories (id INTEGER PRIMARY KEY, snapshot_id TEXT, owner TEXT, name TEXT, gate_reason TEXT, "
        "gate_undecided TEXT, rated INTEGER, p_responsiveness REAL, porchlamp REAL, rank INTEGER, ttfhr_p75 REAL)"
    )
    conn.execute("CREATE TABLE params (repo_id INTEGER, param_id TEXT, measured INTEGER, n INTEGER)")
    for name, (gate, undecided, rated, p, score, rank, ttfhr, _stars) in PROJECTS.items():
        cur = conn.execute(
            "INSERT INTO repositories (snapshot_id, owner, name, gate_reason, gate_undecided, rated, p_responsiveness, "
            "porchlamp, rank, ttfhr_p75) VALUES ('S1', 'Org', ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, gate, undecided, rated, p, score, rank, ttfhr))
        population = h_population if name == "h" else 20
        conn.execute("INSERT INTO params VALUES (?, 'gr14_ghost_rate', 1, ?)", (cur.lastrowid, population))
    conn.commit()
    conn.close()

    data = tmp_path / "data"
    meta, derived, window = data / "raw" / "github_meta", data / "derived", data / "raw" / "pr_window"
    meta.mkdir(parents=True)
    derived.mkdir()
    window.mkdir()
    for name, row in PROJECTS.items():
        if row[-1] is not None:
            (meta / f"Org__{name}.json").write_text(json.dumps({"data": {"stars": row[-1]}}), encoding="utf-8")
    # d: 10 outside PRs by u0..u9 and one more by U0 (u0 again), all in the window; not counted: two bots
    # (a Bot account, and renovate on the D-IN-1 login list), a member's PR, and one opened before the window.
    d_prs = [_pr(f"u{k}") for k in range(10)] + [_pr("U0", YOUNG)]
    d_prs += [_pr("dependabot", typename="Bot"), _pr("renovate"), _pr("lead", association="MEMBER"), _pr("early1", EARLY)]
    # a: 10 outside PRs by v0..v9, plus the owner's
    a_prs = [_pr(f"v{k}") for k in range(10)] + [_pr("maintainer", association="OWNER")]
    for name, prs in (("d", d_prs), ("a", a_prs)):
        (window / f"Org__{name}.json").write_text(json.dumps({"data": {"prs": prs}}), encoding="utf-8")
    (window / "Org__b.json").write_text("{not json", encoding="utf-8")  # unreadable: no window recorded
    # the derived bundles' 30-newest samples hold other PRs, which the corpus must not count
    (derived / "Org__d.json").write_text(json.dumps({"pr_events": [_event("renovate"), _event("x1")]}), encoding="utf-8")
    (derived / "Org__a.json").write_text(json.dumps({"pr_events": [_event("x2")]}), encoding="utf-8")
    (derived / "Org__b.json").write_text(json.dumps({"pr_events": [_event("x3")]}), encoding="utf-8")
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
        # d's and a's windows lack the rule inputs and the cut flag, but both are decided on gate 5
        "gate5_undecided_rule_inputs": 0, "gate5_undecided_cut_record": 0,
        # 11 outside PRs to d (u0..u9, then U0 = u0) and 10 to a; b has no readable window
        "corpus": {"derived_repos": 3, "pr_windows": 2, "human_outside_prs": 21, "distinct_outside_authors": 20},
    }
    # most first; ties keep the order of the most-starred project that has them
    assert list(stats["top100_stars_fate"].items()) == [
        ("rated", 3), ("black_hole", 2), ("unrated", 2), ("stale", 1), ("unrated: no_external_workflow", 1)]


def test_as_of_moves_the_pr_window(tmp_path):
    """At an as_of of 11 September the window runs from 15 March: early1's PR is in it, U0's (10 October) is not."""
    db, data = _snapshot(tmp_path)
    stats = compute_stats(db, data, as_of="2026-09-11T00:00:00+00:00")
    assert stats["as_of"] == "2026-09-11T00:00:00+00:00"
    assert stats["corpus"] == {"derived_repos": 3, "pr_windows": 2, "human_outside_prs": 21, "distinct_outside_authors": 21}


def _h_window(data: Path, prs: list[dict]) -> None:
    """h's derived bundle and its recorded PR window, with the collector's own classifications."""
    from porchlamp.collect.github_graphql import derive_pr_window

    (data / "derived" / "Org__h.json").write_text(json.dumps({"pr_events": []}), encoding="utf-8")
    (data / "raw" / "pr_window" / "Org__h.json").write_text(
        json.dumps({"data": {"prs": prs, "derived": derive_pr_window(prs)}}), encoding="utf-8")


def test_a_gate_5_undecided_project_whose_window_lacks_the_rule_inputs_is_counted(tmp_path):
    """h passed gates 1 to 4 and is undecided on gate 5 with a measured share over 12 PRs: counted while its
    window's PRs lack isDraft and the timeline, not once they carry them."""
    db, data = _snapshot(tmp_path)
    _h_window(data, [_pr("w1")])
    assert compute_stats(db, data)["gate5_undecided_rule_inputs"] == 1
    _h_window(data, [{**_pr("w1"), "isDraft": False, "timelineItems": {"nodes": []}}])
    assert compute_stats(db, data)["gate5_undecided_rule_inputs"] == 0


def test_a_gate_5_undecided_project_whose_record_stops_short_of_the_window_is_counted(tmp_path):
    """h passed gates 1 to 4 and is undecided on gate 5 over 12 PRs. With the rule inputs recorded, it is
    counted as cut while its record stopped at the collector's PR cap after the window opened (16 April), and
    not once the record is uncut. A cut record that also lacks the rule inputs is counted for those only."""
    db, data = _snapshot(tmp_path)
    recorded = {**_pr("w1"), "isDraft": False, "timelineItems": {"nodes": []}}

    def record(prs: list[dict], **fields: object) -> None:
        _h_window(data, prs)
        path = data / "raw" / "pr_window" / "Org__h.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["data"].update(window_anchor=AS_OF, window_days=180, **fields)
        path.write_text(json.dumps(document), encoding="utf-8")

    def counts() -> tuple[int, int]:
        stats = compute_stats(db, data)
        return stats["gate5_undecided_cut_record"], stats["gate5_undecided_rule_inputs"]

    record([recorded], truncated=True)
    assert counts() == (1, 0)
    record([recorded], truncated=False)
    assert counts() == (0, 0)
    record([_pr("w1")], truncated=True)
    assert counts() == (0, 1)


def test_an_undecided_project_below_the_minimum_population_is_not_counted(tmp_path):
    """Over 9 PRs gate 5 is undecided whatever the records hold, so the missing fields did not leave it out."""
    db, data = _snapshot(tmp_path, h_population=9)
    _h_window(data, [_pr("w1")])
    assert compute_stats(db, data)["gate5_undecided_rule_inputs"] == 0


def test_an_undecided_project_that_hands_review_off_is_not_counted(tmp_path):
    """Five outside PRs, each with a Gerrit import notice: review is handed off, so gate 5 is undecided anyway."""
    db, data = _snapshot(tmp_path)
    notice = {"author": {"login": "gopherbot", "__typename": "User"}, "authorAssociation": "NONE",
              "bodyText": "This change has been imported to Gerrit for code review.", "createdAt": OLD}
    prs = [{**_pr(f"w{k}"), "number": k + 1, "comments": {"nodes": [notice]}, "reviews": {"nodes": []}}
           for k in range(5)]
    _h_window(data, prs)
    assert compute_stats(db, data)["gate5_undecided_rule_inputs"] == 0


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
