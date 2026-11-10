"""Reader basics and SQL/tie_key ordering parity tests.

Ordering fixtures test reader SQL ordering against stored column values.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from porchlamp.core.models import ParamScore
from porchlamp.core.tiebreak import TieBreakInput, assign_ranks
from porchlamp.store import reader, schema
from porchlamp.store.migrations import apply_migrations
from porchlamp.store.writer import _normalize_utc_z, write_records, write_snapshot

from .helpers import (
    make_gated_record,
    make_measured,
    make_rated_record,
    make_repo_write,
    make_unrated_record,
)


def _fresh_db(path: Path) -> sqlite3.Connection:
    conn = schema.connect(path)  # PRAGMA foreign_keys=ON
    apply_migrations(conn, applied_at="2026-09-13T00:00:00Z")
    write_snapshot(
        conn, id="snap-1", as_of="2026-09-13", benchmark_version="2.0.0-dev",
        constants_hash="c" * 64, code_commit="deadbeef", manifest_root="r",
        created_at="2026-09-13T00:00:00Z",
    )
    return conn


def test_get_row_get_evidence_get_snapshot(tmp_path) -> None:
    from porchlamp.core.models import PersonaComposite

    conn = _fresh_db(tmp_path / "db.sqlite")
    record = make_rated_record(
        "snap-1",
        params={"ttfhr_hours": ParamScore(value=90.0, score=90.0, measured=True, reason=None, n=10, window_start=None, window_end=None, method="primary", source="test", fetched_at="2026-09-01T00:00:00+00:00")},
        persona_gates={"ai_assisted": "hard_ban"},
        persona_composites={"balanced": PersonaComposite(porchlamp=82.5, coverage=0.9, tier="B", rated=True)},
        suspicion=["lockstep_review"],
    )
    item = make_repo_write(record, inputs={"ttfhr_hours": make_measured(3.0, n=10)})
    write_records(conn, "snap-1", [item])

    row = reader.get_row(conn, "snap-1", "acme", "demo")
    assert row["owner"] == "acme"
    assert row["porchlamp"] == 82.5
    assert row["benchmark_version"] == "2.0.0-dev"
    assert row["eligible"] is True
    assert row["rated"] is True
    assert row["persona_gates"] == {"ai_assisted": "hard_ban"}
    assert row["persona_composites"] == {"balanced": {"porchlamp": 82.5, "coverage": 0.9, "tier": "B", "rated": True}}
    assert row["suspicion"] == ["lockstep_review"]
    assert row["gate_undecided"] == []

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
    gated = make_repo_write(make_gated_record("snap-1", owner="zzz", name="last", gate_reason="archived"))
    unrated = make_repo_write(make_unrated_record("snap-1", owner="aaa", name="first"))
    rated = make_repo_write(make_rated_record("snap-1", owner="mmm", name="middle"))
    write_records(conn, "snap-1", [gated, unrated, rated])

    not_ranked = reader.list_not_ranked(conn, "snap-1")
    assert [(row["owner"], row["name"]) for row in not_ranked] == [("aaa", "first"), ("zzz", "last")]
    assert not_ranked[0]["unrated_reason"] == "unrated: insufficient_evidence"
    assert not_ranked[1]["gate_reason"] == "archived"
    assert not_ranked[1]["eligible"] is False


def _write_and_rank(conn, entries: list[TieBreakInput]) -> list[tuple[str, str]]:
    """Run ``assign_ranks`` (the Python authority), write the resulting
    ranked records, and directly stamp ``ttfhr_p75``/``last_human_commit_at``
    (via ``UPDATE``, normalising the commit date exactly as the writer
    would if it had a real producer -- see the module docstring) with the
    SAME values fed to each entry's ``TieBreakInput``, so the reader's SQL
    ordering is compared against the identical evidence ``tie_key`` used.
    Returns the (owner, name) order ``assign_ranks`` produced.

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
    overrides = []
    for record, _rank, _tie_group in ranked:
        source = by_raw_hash[record.raw_hash]
        python_order.append((source.owner, source.name))
        items.append(make_repo_write(record))
        overrides.append((source.owner, source.name, source.ttfhr_p75_hours, source.last_non_bot_commit_at))
    write_records(conn, "snap-1", items)
    for owner, name, ttfhr_p75, last_commit in overrides:
        conn.execute(
            "UPDATE repositories SET ttfhr_p75=?, last_human_commit_at=? "
            "WHERE snapshot_id='snap-1' AND owner=? AND name=?",
            (ttfhr_p75, _normalize_utc_z(last_commit), owner, name),
        )
    conn.commit()
    return python_order


def test_ranked_order_sql_parity_with_tie_key_nulls_at_multiple_levels(tmp_path) -> None:
    """Verify a naive ``ORDER BY`` (no ``NULLS LAST``) does not invert
    order when an ASC tie-break column is NULL. Puts NULLs at two different
    tie levels (friction, then leverage) on rows reaching those levels,
    along with a mixed-UTC-offset pair at level 7 (last commit)."""
    conn = _fresh_db(tmp_path / "db.sqlite")

    def rec(raw_hash, owner, name, composite, p_resp, friction, leverage):
        return make_rated_record(
            "snap-1", owner=owner, name=name, raw_hash=raw_hash, composite=composite, coverage=1.0,
            pillar_scores={"responsiveness": p_resp, "criticality": 80.0, "governance": 80.0,
                            "rigor": 80.0, "accessibility": 80.0},
            friction=friction, leverage=leverage,
        )

    entries = [
        TieBreakInput(rec("h1", "acme", "r1", 90.0, 90.0, 20.0, 80.0), "acme", "r1",
                      ttfhr_p75_hours=5.0, last_non_bot_commit_at="2026-01-01T00:00:00Z"),
        TieBreakInput(rec("h2", "acme", "r2", 90.0, 90.0, None, 80.0), "acme", "r2",
                      ttfhr_p75_hours=5.0, last_non_bot_commit_at="2026-01-01T00:00:00Z"),
        TieBreakInput(rec("h3", "acme", "r3", 85.0, 85.0, 10.0, None), "acme", "r3",
                      ttfhr_p75_hours=8.0, last_non_bot_commit_at="2026-01-02T00:00:00Z"),
        TieBreakInput(rec("h4", "acme", "r4", 85.0, 85.0, 10.0, 50.0), "acme", "r4",
                      ttfhr_p75_hours=None, last_non_bot_commit_at="2026-01-03T00:00:00Z"),
        TieBreakInput(rec("h5", "acme", "r5", 85.0, 85.0, 10.0, None), "acme", "r5",
                      ttfhr_p75_hours=6.0, last_non_bot_commit_at=None),
        # Tied all the way down to level 7, mixed UTC offsets.
        # r6's instant (18:00-05:00 = 23:00Z) is LATER than r7's (22:00Z),
        # so DESC puts r6 first -- exactly the comparison a naive TEXT sort
        # of the raw (non-normalised) strings would get backwards.
        TieBreakInput(rec("h6", "acme", "r6", 77.0, 77.0, 15.0, 40.0), "acme", "r6",
                      ttfhr_p75_hours=9.0, last_non_bot_commit_at="2026-02-01T18:00:00-05:00"),
        TieBreakInput(rec("h7", "acme", "r7", 77.0, 77.0, 15.0, 40.0), "acme", "r7",
                      ttfhr_p75_hours=9.0, last_non_bot_commit_at="2026-02-01T22:00:00Z"),
    ]

    python_order = _write_and_rank(conn, entries)

    # Concrete, non-vacuous checks (R-1: assert values, not just "some
    # order"): NULL friction sorts after a measured friction at an
    # otherwise-tied level; among the leverage-tied trio, the measured
    # leverage wins, then the lower (ASC) ttfhr breaks the remaining tie;
    # r6 (the later instant, once its offset is normalised) outranks r7.
    assert python_order == [
        ("acme", "r1"), ("acme", "r2"), ("acme", "r4"), ("acme", "r5"), ("acme", "r3"),
        ("acme", "r6"), ("acme", "r7"),
    ]

    sql_order = reader.ranked_order_sql(conn, "snap-1")
    sql_owner_name = [(row[0], row[1]) for row in sql_order]
    assert sql_owner_name == python_order

    # Fail-before proof: the SAME fixture under a naive ORDER BY (no NULLS
    # LAST) gives a DIFFERENT order -- proving this test is actually
    # sensitive to the fix, not vacuously passing regardless.
    naive_sql = """
        SELECT owner, name FROM repositories
        WHERE snapshot_id = 'snap-1' AND rated = 1
        ORDER BY ROUND(porchlamp, 2) DESC, coverage DESC, p_responsiveness DESC,
                 friction ASC, leverage DESC, ttfhr_p75 ASC,
                 last_human_commit_at DESC, lower(owner || '/' || name) ASC
    """
    naive_order = [(row[0], row[1]) for row in conn.execute(naive_sql).fetchall()]
    assert naive_order != python_order


def test_last_human_commit_at_mixed_offset_sorts_by_instant_not_text(tmp_path) -> None:
    """Two rows tied on everything through level 6, whose
    RAW (pre-normalisation) commit strings would sort backwards as TEXT
    (``'18:00-05:00'`` < ``'22:00Z'`` lexicographically, but the FIRST is
    the LATER instant) -- proves normalisation, not just NULLS LAST, is
    exercised."""
    conn = _fresh_db(tmp_path / "db.sqlite")
    later_instant_earlier_text = "2026-01-01T20:00:00-05:00"  # == 2026-01-02T01:00:00Z
    earlier_instant_later_text = "2026-01-02T00:00:00Z"
    # As raw TEXT, "2026-01-02..." > "2026-01-01...": a naive DESC sort by
    # the unnormalised string would rank `earlier_instant_later_text`
    # FIRST even though it names the earlier instant -- exactly backwards.
    assert earlier_instant_later_text > later_instant_earlier_text

    entries = [
        TieBreakInput(
            make_rated_record("snap-1", owner="acme", name="later-instant", raw_hash="m1", composite=70.0),
            "acme", "later-instant", ttfhr_p75_hours=1.0, last_non_bot_commit_at=later_instant_earlier_text,
        ),
        TieBreakInput(
            make_rated_record("snap-1", owner="acme", name="earlier-instant", raw_hash="m2", composite=70.0),
            "acme", "earlier-instant", ttfhr_p75_hours=1.0, last_non_bot_commit_at=earlier_instant_later_text,
        ),
    ]
    python_order = _write_and_rank(conn, entries)
    assert python_order == [("acme", "later-instant"), ("acme", "earlier-instant")]

    sql_order = [(row[0], row[1]) for row in reader.ranked_order_sql(conn, "snap-1")]
    assert sql_order == python_order

    stored = dict(conn.execute("SELECT name, last_human_commit_at FROM repositories WHERE snapshot_id='snap-1'").fetchall())
    assert stored["later-instant"] == "2026-01-02T01:00:00Z"
    assert stored["earlier-instant"] == "2026-01-02T00:00:00Z"
