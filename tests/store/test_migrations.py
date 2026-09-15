"""Migration tests: verifies migrations match ``schema.py`` on an empty database,
preserve legacy rows on a v1-shaped database, and execute atomically per file.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
from pathlib import Path

import pytest

from cvi.snapshot.reproduce import canonical_dump
from cvi.store import schema
from cvi.store.migrations import MIGRATIONS_DIR, apply_migrations, discover_migrations

V1_BASELINE = Path("/home/pctablet505/Projects/cvi-scratch/v1-baseline/cvi.db")


def _build_minimal_v1_db(path: Path) -> None:
    """A minimal v1-shaped ``repositories`` table (no ``snapshot_id``
    column -- the only fact the runner's legacy check reads) with a
    handful of rows, so the v1-preservation acceptance test never depends
    on an external fixture file being present on the machine."""
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE repositories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner VARCHAR(100) NOT NULL,
            name VARCHAR(100) NOT NULL,
            ecosystem VARCHAR(50) NOT NULL,
            overall_cvi REAL NOT NULL,
            UNIQUE(owner, name)
        )
        """
    )
    conn.executemany(
        "INSERT INTO repositories (owner, name, ecosystem, overall_cvi) VALUES (?, ?, ?, ?)",
        [("acme", "one", "python", 71.5), ("acme", "two", "go", 82.0), ("acme", "three", "rust", 55.25)],
    )
    conn.commit()
    conn.close()


def test_discover_migrations_finds_all_migration_files() -> None:
    """Verify discover_migrations discovers the expected migration files in order."""
    files = discover_migrations()
    assert [path.name for path in files] == ["0001_init.sql", "0002_indexes.sql"]


def test_apply_migrations_on_empty_db_matches_schema_py(tmp_path) -> None:
    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(db_path)
    applied = apply_migrations(conn, applied_at="2026-09-13T00:00:00Z")
    assert applied == [1, 2]

    reference = sqlite3.connect(":memory:")
    schema.create_schema(reference)

    assert schema.introspect_schema(conn) == schema.introspect_schema(reference)

    versions = conn.execute("SELECT version, applied_at FROM schema_migrations").fetchall()
    assert versions == [(1, "2026-09-13T00:00:00Z"), (2, "2026-09-13T00:00:00Z")]


def test_apply_migrations_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / "twice.db"
    conn = sqlite3.connect(db_path)
    first = apply_migrations(conn, applied_at="2026-09-13T00:00:00Z")
    second = apply_migrations(conn, applied_at="2026-09-13T01:00:00Z")
    assert first == [1, 2]
    assert second == []  # already applied, no-op, not re-recorded
    count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    assert count == 2


def test_apply_migrations_requires_explicit_applied_at(tmp_path) -> None:
    """R rules: no wall-clock read in ``cvi/`` -- ``applied_at`` must be
    supplied, never defaulted to ``datetime.now()``."""
    conn = sqlite3.connect(tmp_path / "x.db")
    with pytest.raises(ValueError):
        apply_migrations(conn, applied_at="")


def test_apply_migrations_raises_on_an_empty_migrations_directory(tmp_path) -> None:
    """An empty migrations directory is an error condition, not a no-op."""
    empty_dir = tmp_path / "empty_migrations"
    empty_dir.mkdir()
    conn = sqlite3.connect(tmp_path / "x.db")
    with pytest.raises(RuntimeError, match="no migration files"):
        apply_migrations(conn, applied_at="2026-09-13T00:00:00Z", migrations_dir=empty_dir)


# --------------------------------------------------------------------- #
# Atomicity
# --------------------------------------------------------------------- #

def _write_broken_migration(dst_dir: Path, break_after_n_statements: int) -> None:
    """A copy of ``0001_init.sql`` with a guaranteed syntax error
    inserted after the ``break_after_n_statements``-th real statement, so
    the failure point is controlled precisely rather than guessed at by
    string surgery on the real file."""
    from cvi.store.migrations import _split_statements

    original = (MIGRATIONS_DIR / "0001_init.sql").read_text(encoding="utf-8")
    statements = _split_statements(original)
    broken = statements[:break_after_n_statements] + ["CREATE TABLE BROKEN_SYNTAX ("] + statements[break_after_n_statements:]
    dst_dir.mkdir(parents=True, exist_ok=True)
    (dst_dir / "0001_init.sql").write_text(";\n".join(broken) + ";\n", encoding="utf-8")


@pytest.mark.parametrize("break_after_n_statements", [0, 2, 7])
def test_apply_migrations_is_atomic_on_a_v1_database(tmp_path, break_after_n_statements) -> None:
    """A migration that fails partway through leaves the database byte-for-byte
    as it was before the call, with the legacy rows intact."""
    db_path = tmp_path / "v1.db"
    _build_minimal_v1_db(db_path)
    before_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()
    before_rows = sqlite3.connect(db_path).execute("SELECT owner, name FROM repositories ORDER BY owner, name").fetchall()

    broken_dir = tmp_path / f"broken_{break_after_n_statements}"
    _write_broken_migration(broken_dir, break_after_n_statements)

    conn = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.OperationalError, match="incomplete input|syntax error"):
        apply_migrations(conn, applied_at="2026-09-13T00:00:00Z", migrations_dir=broken_dir)
    conn.close()

    after_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()
    assert after_hash == before_hash

    reopened = sqlite3.connect(db_path)
    tables = {
        row[0]
        for row in reopened.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert tables == {"repositories"}  # the v1 shape, unrenamed -- not even schema_migrations exists
    rows = reopened.execute("SELECT owner, name FROM repositories ORDER BY owner, name").fetchall()
    assert rows == before_rows


def test_apply_migrations_atomicity_does_not_lose_a_later_migration_file(tmp_path) -> None:
    """A two-file migrations directory where the SECOND file fails: the
    first file's changes stay committed (forward-only progress is still
    file-by-file); the second file's changes are fully rolled back and its
    version is never recorded."""
    two_files_dir = tmp_path / "two_files"
    two_files_dir.mkdir()
    shutil.copyfile(MIGRATIONS_DIR / "0001_init.sql", two_files_dir / "0001_init.sql")
    (two_files_dir / "0002_broken.sql").write_text(
        "CREATE TABLE later_ok (id INTEGER PRIMARY KEY);\nCREATE TABLE BROKEN_SYNTAX (;\n", encoding="utf-8"
    )

    db_path = tmp_path / "two_files.db"
    conn = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.OperationalError):
        apply_migrations(conn, applied_at="2026-09-13T00:00:00Z", migrations_dir=two_files_dir)

    versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()]
    assert versions == [1]  # migration 1 committed; migration 2 never recorded
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert "later_ok" not in tables  # migration 2's own first statement was rolled back too


# --------------------------------------------------------------------- #
# Legacy row preservation
# --------------------------------------------------------------------- #

def test_v1_shaped_database_migration_preserves_legacy_rows_synthetic(tmp_path) -> None:
    """Verifies legacy rows are preserved into legacy_repositories when
    migrating a v1-shaped table."""
    db_path = tmp_path / "v1.db"
    _build_minimal_v1_db(db_path)

    pre_dump = sorted(line for line in canonical_dump(db_path) if line.startswith("repositories "))

    conn = sqlite3.connect(db_path)
    applied = apply_migrations(conn, applied_at="2026-09-13T00:00:00Z")
    assert applied == [1, 2]

    legacy_row_count = conn.execute("SELECT COUNT(*) FROM legacy_repositories").fetchone()[0]
    assert legacy_row_count == 3

    post_dump_legacy_only = sorted(line for line in canonical_dump(db_path) if line.startswith("legacy_repositories "))
    renamed_pre_dump = sorted(line.replace("repositories ", "legacy_repositories ", 1) for line in pre_dump)
    assert post_dump_legacy_only == renamed_pre_dump

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


@pytest.mark.skipif(not V1_BASELINE.exists(), reason="v1 baseline fixture not present in this environment (opt-in extra)")
def test_v1_database_migration_preserves_legacy_rows_real_db_extra(tmp_path) -> None:
    """Supplementary check on the real 1,000-row v1 baseline DB (opt-in by
    availability -- the synthetic test above is what always runs)."""
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
    assert applied == [1, 2]

    legacy_row_count = conn.execute("SELECT COUNT(*) FROM legacy_repositories").fetchone()[0]
    assert legacy_row_count == pre_row_count

    post_dump_legacy_only = sorted(
        line for line in canonical_dump(copy_path) if line.startswith("legacy_repositories ")
    )
    renamed_pre_dump = sorted(
        line.replace("repositories ", "legacy_repositories ", 1) for line in pre_repositories_dump
    )
    assert post_dump_legacy_only == renamed_pre_dump

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


def test_migrating_a_create_schema_built_v2_db_is_a_noop_for_rename(tmp_path) -> None:
    """A database initialized via ``schema.create_schema`` without prior
    ``schema_migrations`` rows is not mistaken for a v1 database."""
    db_path = tmp_path / "from_scratch.db"
    conn = sqlite3.connect(db_path)
    schema.create_schema(conn)
    conn.execute(
        "INSERT INTO repositories (snapshot_id, owner, name, ecosystem, raw_hash, constants_hash, "
        "eligible, rated, coverage) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("snap-1", "acme", "kept", "python", "h" * 64, "c" * 64, 1, 1, 0.9),
    )
    conn.commit()

    applied = apply_migrations(conn, applied_at="2026-09-13T00:00:00Z")
    assert applied == [1, 2]

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert "legacy_repositories" not in tables
    row = conn.execute("SELECT owner, name FROM repositories WHERE snapshot_id='snap-1'").fetchone()
    assert row == ("acme", "kept")
