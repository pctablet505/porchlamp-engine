"""W4-2 acceptance: reader basics + the SQL/tie_key ordering parity test
(`04` sections 5, 6; `02` section 10)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from cvi.core.models import ParamScore
from cvi.core.tiebreak import TieBreakInput, assign_ranks
from cvi.store import reader
from cvi.store.migrations import apply_migrations
from cvi.store.writer import RepoWrite, write_records, write_snapshot

from .helpers import (
    make_bundle,
    make_gated_record,
    make_measured,
    make_rated_record,
    make_repo_write,
    make_unrated_record,
)


def _fresh_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    apply_migrations(conn, applied_at="2026-09-13T00:00:00Z")
    write_snapshot(
        conn, id="snap-1", as_of="2026-09-13", benchmark_version="2.0.0-dev",
        constants_hash="c" * 64, code_commit="deadbeef", manifest_root="r",
        created_at="2026-09-13T00:00:00Z",
    )
    return conn


def test_get_row_get_evidence_get_snapshot(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    record = make_rated_record(
        "snap-1",
        params={"ttfhr_hours": ParamScore(score=90.0, measured=True, reason=None, n=10, method="primary")},
        persona_gates={"ai_assisted": "hard_ban"},
        persona_composites={"balanced": 82.5},
        suspicion=["lockstep_review"],
    )
    item = make_repo_write("acme", "demo", record, inputs={"ttfhr_hours": make_measured(3.0, n=10)})
    write_records(conn, "snap-1", [item])

    row = reader.get_row(conn, "snap-1", "acme", "demo")
    assert row["owner"] == "acme"
    assert row["cvi"] == 82.5
    assert row["benchmark_version"] == "2.0.0-dev"
    assert row["eligible"] is True
    assert row["rated"] is True
    assert row["persona_gates"] == {"ai_assisted": "hard_ban"}
    assert row["persona_composites"] == {"balanced": 82.5}
    assert row["suspicion"] == ["lockstep_review"]

    assert reader.get_row(conn, "snap-1", "acme", "missing") is None

    evidence = reader.get_evidence(conn, "snap-1", "acme", "demo")
    assert len(evidence) == 1
    assert evidence[0]["param_id"] == "ttfhr_hours"
    assert evidence[0]["score"] == 90.0
    assert evidence[0]["measured"] is True
    assert evidence[0]["raw_value"] == 3.0
    assert evidence[0]["n"] == 10

    snapshot = reader.get_snapshot(conn, "snap-1")
    assert snapshot["benchmark_version"] == "2.0.0-dev"
    assert reader.get_snapshot(conn, "no-such-snapshot") is None


def test_list_not_ranked_includes_gated_and_unrated_ordered_by_name(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    gated = make_repo_write("zzz", "last", make_gated_record("snap-1", gate_reason="archived"))
    unrated = make_repo_write("aaa", "first", make_unrated_record("snap-1"))
    rated = make_repo_write("mmm", "middle", make_rated_record("snap-1"))
    write_records(conn, "snap-1", [gated, unrated, rated])

    not_ranked = reader.list_not_ranked(conn, "snap-1")
    assert [(row["owner"], row["name"]) for row in not_ranked] == [("aaa", "first"), ("zzz", "last")]
    assert not_ranked[0]["unrated_reason"] == "unrated: insufficient_evidence"
    assert not_ranked[1]["gate_reason"] == "archived"
    assert not_ranked[1]["eligible"] is False


def _write_and_rank(conn, entries: list[TieBreakInput]) -> list[tuple[str, str]]:
    """Run ``assign_ranks`` (the Python authority) and write the resulting
    ranked records; return the (owner, name) order it produced.

    ``assign_ranks`` returns ``(record, rank, tie_group)`` triples with no
    direct back-pointer to the source ``TieBreakInput``; each fixture below
    gives every entry a unique ``raw_hash`` (never read by any tie-break
    comparison) purely so this helper can recover which output record came
    from which input entry -- the same "stash identity in raw_hash" trick
    ``tests/core/test_tiebreak.py`` documents and uses for the same reason.
    """
    ranked = assign_ranks(entries)
    by_raw_hash = {entry.record.raw_hash: entry for entry in entries}
    python_order: list[tuple[str, str]] = []
    items = []
    for record, _rank, _tie_group in ranked:
        source = by_raw_hash[record.raw_hash]
        python_order.append((source.owner, source.name))
        items.append(RepoWrite(
            tie=TieBreakInput(
                record=record, owner=source.owner, name=source.name,
                ttfhr_p75_hours=source.ttfhr_p75_hours,
                last_non_bot_commit_at=source.last_non_bot_commit_at,
                stars=source.stars, forks=source.forks, downloads=source.downloads,
            ),
            bundle=make_bundle(source.owner, source.name),
        ))
    write_records(conn, "snap-1", items)
    return python_order


def test_ranked_order_sql_parity_with_tie_key_nulls_at_multiple_levels(tmp_path) -> None:
    """The W2-6 review finding: a naive ``ORDER BY`` (no ``NULLS LAST``)
    inverts the order when an ASC tie-break column is NULL. This fixture
    puts NULLs at two different tie levels (friction, then leverage) on
    rows that actually reach those levels, so the parity check is real."""
    conn = _fresh_db(tmp_path / "db.sqlite")

    def rec(raw_hash, composite, p_resp, friction, leverage):
        return make_rated_record(
            "snap-1", raw_hash=raw_hash, composite=composite, coverage=1.0,
            pillar_scores={"responsiveness": p_resp, "criticality": 80.0, "governance": 80.0,
                            "rigor": 80.0, "accessibility": 80.0},
            friction=friction, leverage=leverage,
        )

    entries = [
        TieBreakInput(rec("h1", 90.0, 90.0, 20.0, 80.0), "acme", "r1",
                      ttfhr_p75_hours=5.0, last_non_bot_commit_at="2026-01-01T00:00:00Z"),
        TieBreakInput(rec("h2", 90.0, 90.0, None, 80.0), "acme", "r2",
                      ttfhr_p75_hours=5.0, last_non_bot_commit_at="2026-01-01T00:00:00Z"),
        TieBreakInput(rec("h3", 85.0, 85.0, 10.0, None), "acme", "r3",
                      ttfhr_p75_hours=8.0, last_non_bot_commit_at="2026-01-02T00:00:00Z"),
        TieBreakInput(rec("h4", 85.0, 85.0, 10.0, 50.0), "acme", "r4",
                      ttfhr_p75_hours=None, last_non_bot_commit_at="2026-01-03T00:00:00Z"),
        TieBreakInput(rec("h5", 85.0, 85.0, 10.0, None), "acme", "r5",
                      ttfhr_p75_hours=6.0, last_non_bot_commit_at=None),
    ]

    python_order = _write_and_rank(conn, entries)

    # Concrete, non-vacuous checks (R-1: assert values, not just "some
    # order"): NULL friction sorts after a measured friction at an
    # otherwise-tied level; among the leverage-tied trio, the measured
    # leverage wins, then the lower (ASC) ttfhr breaks the remaining tie.
    assert python_order == [("acme", "r1"), ("acme", "r2"), ("acme", "r4"), ("acme", "r5"), ("acme", "r3")]

    sql_order = reader.ranked_order_sql(conn, "snap-1")
    sql_owner_name = [(row[0], row[1]) for row in sql_order]
    assert sql_owner_name == python_order

    # Fail-before proof: the SAME fixture under a naive ORDER BY (no NULLS
    # LAST) gives a DIFFERENT order -- proving this test is actually
    # sensitive to the fix, not vacuously passing regardless.
    naive_sql = """
        SELECT owner, name FROM repositories
        WHERE snapshot_id = 'snap-1' AND rated = 1
        ORDER BY ROUND(cvi, 2) DESC, coverage DESC, p_responsiveness DESC,
                 friction ASC, leverage DESC, ttfhr_p75 ASC,
                 last_human_commit_at DESC, lower(owner || '/' || name) ASC
    """
    naive_order = [(row[0], row[1]) for row in conn.execute(naive_sql).fetchall()]
    assert naive_order != python_order


def test_ranked_order_sql_tie_group_dense_rank(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    entries = [
        TieBreakInput(make_rated_record("snap-1", raw_hash="g1", composite=80.0), "acme", "a"),
        TieBreakInput(make_rated_record("snap-1", raw_hash="g2", composite=80.0), "acme", "b"),
        TieBreakInput(make_rated_record("snap-1", raw_hash="g3", composite=70.0), "acme", "c"),
    ]
    _write_and_rank(conn, entries)
    rows = reader.ranked_order_sql(conn, "snap-1")
    by_name = {row[1]: row[3] for row in rows}
    assert by_name["a"] == by_name["b"]  # tied composite -> same tie_group
    assert by_name["c"] != by_name["a"]
