"""W4-2 acceptance: the writer (`04` section 5; packet deliverable 3)."""
from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from cvi.core.models import ParamScore
from cvi.snapshot.populations import Population, PopulationSet
from cvi.store.migrations import apply_migrations
from cvi.store.writer import (
    RepoWrite,
    finalize_snapshot_hash,
    repositories_snapshot_dump_hash,
    write_records,
    write_snapshot,
)

from .helpers import (
    make_bundle,
    make_gated_record,
    make_measured,
    make_rated_record,
    make_repo_write,
    make_tie,
)


def _fresh_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    apply_migrations(conn, applied_at="2026-09-13T00:00:00Z")
    return conn


def _write_one_snapshot(conn, snapshot_id: str = "snap-1") -> None:
    write_snapshot(
        conn, id=snapshot_id, as_of="2026-09-13", benchmark_version="2.0.0-dev",
        constants_hash="c" * 64, code_commit="deadbeef", manifest_root="root" * 8,
        created_at="2026-09-13T00:00:00Z",
    )


def test_write_snapshot_is_idempotent_and_never_clobbers_a_finalized_hash(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    conn.commit()
    finalize_snapshot_hash(conn, tmp_path / "db.sqlite", "snap-1", promoted_at="2026-09-13T01:00:00Z")

    # A second write_snapshot call for the same id must not erase the hash.
    _write_one_snapshot(conn)
    row = conn.execute("SELECT canonical_dump_hash, promoted_at FROM snapshots WHERE id='snap-1'").fetchone()
    assert row[0] is not None
    assert row[1] == "2026-09-13T01:00:00Z"

    count = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    assert count == 1


def test_write_records_round_trips_repo_params_and_populations(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)

    param_scores = {
        "ttfhr_hours": ParamScore(score=88.0, measured=True, reason=None, n=42, method="primary"),
        "dependents_deduped": ParamScore(score=None, measured=False, reason="insufficient_n", n=3, method=None),
    }
    record = make_rated_record("snap-1", params=param_scores)
    item = make_repo_write(
        "acme", "demo", record,
        inputs={
            "ttfhr_hours": make_measured(4.5, n=42),
            "dependents_deduped": make_measured(3, n=3),
        },
    )
    counts = write_records(conn, "snap-1", [item], populations=None)
    assert counts == (1, 2, 0)

    repo_row = conn.execute(
        "SELECT owner, name, ecosystem, cvi, coverage, rated FROM repositories WHERE snapshot_id='snap-1'"
    ).fetchone()
    assert repo_row == ("acme", "demo", "python", 82.5, 0.9, 1)

    param_rows = conn.execute(
        "SELECT param_id, score, measured, n FROM params ORDER BY param_id"
    ).fetchall()
    assert param_rows == [
        ("dependents_deduped", None, 0, 3),
        ("ttfhr_hours", 88.0, 1, 42),
    ]


def test_write_records_is_idempotent_on_snapshot_owner_name(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    record = make_rated_record("snap-1", composite=70.0)
    item = make_repo_write("acme", "demo", record)

    write_records(conn, "snap-1", [item])
    write_records(conn, "snap-1", [item])
    count = conn.execute("SELECT COUNT(*) FROM repositories").fetchone()[0]
    assert count == 1

    updated_record = replace(record, composite=91.0)
    updated_item = make_repo_write("acme", "demo", updated_record)
    write_records(conn, "snap-1", [updated_item])
    count_after = conn.execute("SELECT COUNT(*) FROM repositories").fetchone()[0]
    cvi = conn.execute("SELECT cvi FROM repositories").fetchone()[0]
    assert count_after == 1
    assert cvi == 91.0


def test_write_records_writes_no_params_for_a_gated_repo(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    record = make_gated_record("snap-1", gate_reason="archived")
    item = make_repo_write("acme", "old", record)
    counts = write_records(conn, "snap-1", [item])
    assert counts == (1, 0, 0)
    eligible, rated = conn.execute(
        "SELECT eligible, rated FROM repositories WHERE owner='acme' AND name='old'"
    ).fetchone()
    assert (eligible, rated) == (0, 0)


def test_write_records_rejects_a_snapshot_id_mismatch(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    record = make_rated_record("snap-OTHER")
    item = make_repo_write("acme", "demo", record)
    with pytest.raises(ValueError, match="snapshot_id"):
        write_records(conn, "snap-1", [item])


def test_write_records_writes_populations(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")
    _write_one_snapshot(conn)
    popset = PopulationSet(
        snapshot_id="snap-1",
        benchmark_version="2.0.0-dev",
        constants_hash="c" * 64,
        populations=(
            Population(ecosystem="python", param="ttfhr_hours", n=2, fallback=False, values={"a/b": 1.0, "c/d": 2.0}),
        ),
    )
    counts = write_records(conn, "snap-1", [], populations=popset)
    assert counts == (0, 0, 1)
    row = conn.execute("SELECT ecosystem, param_id, n, fallback FROM populations").fetchone()
    assert row == ("python", "ttfhr_hours", 2, 0)


def test_repositories_snapshot_dump_hash_scopes_to_one_snapshot(tmp_path) -> None:
    """Over-correction guard: the hash must depend only on the named
    snapshot's rows, not on the whole ``repositories`` table (the
    self-reference bug INT-W0-4's ``repositories_dump_hash`` docstring
    itself warns about, adapted here to the multi-snapshot v2 table)."""
    db_path = tmp_path / "db.sqlite"
    conn = _fresh_db(db_path)
    write_snapshot(
        conn, id="snap-A", as_of="2026-09-13", benchmark_version="2.0.0-dev",
        constants_hash="c" * 64, code_commit="deadbeef", manifest_root="r",
        created_at="2026-09-13T00:00:00Z",
    )
    write_snapshot(
        conn, id="snap-B", as_of="2026-09-13", benchmark_version="2.0.0-dev",
        constants_hash="c" * 64, code_commit="deadbeef", manifest_root="r",
        created_at="2026-09-13T00:00:00Z",
    )
    record_a = make_rated_record("snap-A", composite=50.0)
    write_records(conn, "snap-A", [make_repo_write("acme", "a-repo", record_a)])
    conn.commit()
    hash_before = repositories_snapshot_dump_hash(db_path, "snap-A")

    record_b = make_rated_record("snap-B", composite=99.0)
    write_records(conn, "snap-B", [make_repo_write("acme", "b-repo", record_b)])
    conn.commit()
    hash_after = repositories_snapshot_dump_hash(db_path, "snap-A")

    assert hash_before == hash_after  # snap-B's write must not change snap-A's hash

    record_a_changed = make_rated_record("snap-A", composite=51.0)
    write_records(conn, "snap-A", [make_repo_write("acme", "a-repo", record_a_changed)])
    conn.commit()
    hash_changed = repositories_snapshot_dump_hash(db_path, "snap-A")
    assert hash_changed != hash_after  # but snap-A's own change must


def test_finalize_snapshot_hash_stamps_hash_and_promoted_at(tmp_path) -> None:
    db_path = tmp_path / "db.sqlite"
    conn = _fresh_db(db_path)
    _write_one_snapshot(conn)
    record = make_rated_record("snap-1")
    write_records(conn, "snap-1", [make_repo_write("acme", "demo", record)])

    expected_hash = repositories_snapshot_dump_hash(db_path, "snap-1")
    returned_hash = finalize_snapshot_hash(conn, db_path, "snap-1", promoted_at="2026-09-13T02:00:00Z")
    assert returned_hash == expected_hash

    row = conn.execute("SELECT canonical_dump_hash, promoted_at FROM snapshots WHERE id='snap-1'").fetchone()
    assert row == (expected_hash, "2026-09-13T02:00:00Z")


def test_repo_write_rejects_mismatched_identity() -> None:
    record = make_rated_record("snap-1")
    tie = make_tie(record, "acme", "demo")
    bundle = make_bundle("acme", "other-name")
    with pytest.raises(ValueError, match="does not match"):
        RepoWrite(tie=tie, bundle=bundle)
