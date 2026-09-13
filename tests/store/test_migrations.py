"""W4-2 acceptance: migrations reach the same schema as ``schema.py`` on an
empty database, and preserve every v1 row on a copy of the real v1 database
(`04` sections 5, 10).
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from cvi.snapshot.reproduce import canonical_dump
from cvi.store import schema
from cvi.store.migrations import apply_migrations, discover_migrations

V1_BASELINE = Path("/home/pctablet505/Projects/cvi-scratch/v1-baseline/cvi.db")


def test_discover_migrations_finds_0001() -> None:
    files = discover_migrations()
    assert [path.name for path in files] == ["0001_init_v2.sql"]


def test_apply_migrations_on_empty_db_matches_schema_py(tmp_path) -> None:
    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(db_path)
    applied = apply_migrations(conn, applied_at="2026-09-13T00:00:00Z")
    assert applied == [1]

    reference = sqlite3.connect(":memory:")
    schema.create_schema(reference)

    assert schema.introspect_schema(conn) == schema.introspect_schema(reference)

    versions = conn.execute("SELECT version, applied_at FROM schema_migrations").fetchall()
    assert versions == [(1, "2026-09-13T00:00:00Z")]


def test_apply_migrations_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / "twice.db"
    conn = sqlite3.connect(db_path)
    first = apply_migrations(conn, applied_at="2026-09-13T00:00:00Z")
    second = apply_migrations(conn, applied_at="2026-09-13T01:00:00Z")
    assert first == [1]
    assert second == []  # already applied, no-op, not re-recorded
    count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    assert count == 1


def test_apply_migrations_requires_explicit_applied_at(tmp_path) -> None:
    """R rules: no wall-clock read in ``cvi/`` -- ``applied_at`` must be
    supplied, never defaulted to ``datetime.now()``."""
    conn = sqlite3.connect(tmp_path / "x.db")
    with pytest.raises(ValueError):
        apply_migrations(conn, applied_at="")


@pytest.mark.skipif(not V1_BASELINE.exists(), reason="v1 baseline fixture not present in this environment")
def test_v1_database_migration_preserves_legacy_rows_and_adds_v2_tables(tmp_path) -> None:
    copy_path = tmp_path / "v1-copy.db"
    shutil.copyfile(V1_BASELINE, copy_path)

    pre_conn = sqlite3.connect(copy_path)
    pre_row_count = pre_conn.execute("SELECT COUNT(*) FROM repositories").fetchone()[0]
    pre_repositories_dump = sorted(
        line for line in canonical_dump(copy_path) if line.startswith("repositories ")
    )
    pre_conn.close()
    assert pre_row_count > 0

    conn = sqlite3.connect(copy_path)
    applied = apply_migrations(conn, applied_at="2026-09-13T00:00:00Z")
    assert applied == [1]

    # Nothing dropped: the v1 `repositories` table survives, renamed, with
    # every row and every value byte-identical (same canonical dump lines,
    # `repositories` -> `legacy_repositories` in the table-name column).
    legacy_row_count = conn.execute("SELECT COUNT(*) FROM legacy_repositories").fetchone()[0]
    assert legacy_row_count == pre_row_count

    post_dump_legacy_only = sorted(
        line for line in canonical_dump(copy_path) if line.startswith("legacy_repositories ")
    )
    renamed_pre_dump = sorted(
        line.replace("repositories ", "legacy_repositories ", 1) for line in pre_repositories_dump
    )
    assert post_dump_legacy_only == renamed_pre_dump

    # v2 tables created alongside, empty, nothing dropped.
    v2_tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert v2_tables == {
        "schema_migrations", "snapshots", "repositories", "params", "populations", "legacy_repositories",
    }
    assert conn.execute("SELECT COUNT(*) FROM repositories").fetchone()[0] == 0

    reference = sqlite3.connect(":memory:")
    schema.create_schema(reference)
    assert schema.introspect_schema(conn)["tables"]["repositories"] == schema.introspect_schema(reference)["tables"]["repositories"]


@pytest.mark.skipif(not V1_BASELINE.exists(), reason="v1 baseline fixture not present in this environment")
def test_v1_migration_is_a_noop_on_an_already_v2_database(tmp_path) -> None:
    """Over-correction guard: the legacy-rename check must not fire (and
    must not raise) once a database already has the v2 shape -- re-running
    migrations on an already-migrated v2 database must not rename the v2
    ``repositories`` table away."""
    db_path = tmp_path / "v2.db"
    conn = sqlite3.connect(db_path)
    apply_migrations(conn, applied_at="2026-09-13T00:00:00Z")
    conn.execute(
        "INSERT INTO snapshots (id, as_of, benchmark_version, constants_hash, code_commit, manifest_root, created_at) "
        "VALUES ('s1', '2026-09-13', '2.0.0-dev', 'c', 'deadbeef', 'root', '2026-09-13')"
    )
    conn.commit()
    # Re-running (simulating a second process pointed at the same, already
    # v2-shaped, database) must be a pure no-op.
    applied_again = apply_migrations(conn, applied_at="2026-09-13T02:00:00Z")
    assert applied_again == []
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert "legacy_repositories" not in tables
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
