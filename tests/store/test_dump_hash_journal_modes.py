"""Journal-mode safety of ``porchlamp.store.writer.snapshot_dump_hash``.

``snapshot_dump_hash`` must reduce a live store to a single snapshot's rows
and hash them no matter how the source database stores those bytes on disk.
The shipped implementation copied the ``.db`` file with ``shutil.copyfile``,
which (a) misses committed-but-uncheckpointed data in ``journal_mode=WAL``
(the copy can hold no tables at all -> ``no such table: params``) and (b) is
not guaranteed a consistent copy even in rollback-journal mode while another
connection writes. These tests pin the fix: a SQLite-guaranteed consistent
copy in any journal mode, whose hash is unchanged for an existing database.
"""
from __future__ import annotations

import sqlite3

from porchlamp.store import schema
from porchlamp.store.migrations import apply_migrations
from porchlamp.store.writer import snapshot_dump_hash, write_records, write_snapshot

from .helpers import make_rated_record, make_repo_write

#: Measured on the PRE-FIX code (``shutil.copyfile`` byte copy) by building a
#: store with these same helpers -- ``schema.connect`` + ``apply_migrations`` +
#: one ``snap-1`` snapshot holding one default ``make_rated_record`` -- and
#: calling ``snapshot_dump_hash(db_path, "snap-1")``. Hard-coded as the
#: regression guard: the fix must not move this value for an existing database.
SNAP1_DUMP_HASH = "a0dba53d517f210d3d9f3a7a279c11005c5fbae5d32ec1ac16bdac493d9deab2"


def _build_snap1(conn: sqlite3.Connection) -> None:
    """Create the schema and write the one-row ``snap-1`` snapshot that
    produces :data:`SNAP1_DUMP_HASH`."""
    write_snapshot(
        conn, id="snap-1", as_of="2026-09-13", benchmark_version="2.0.0-dev",
        constants_hash="c" * 64, code_commit="deadbeef", manifest_root="root" * 8,
        created_at="2026-09-13T00:00:00Z",
    )
    write_records(conn, "snap-1", [make_repo_write(make_rated_record("snap-1"))])
    conn.commit()


# --------------------------------------------------------------------- #
# 1. The hash is unchanged by the fix (regression guard)
# --------------------------------------------------------------------- #

def test_dump_hash_is_unchanged_for_an_existing_database(tmp_path) -> None:
    """The fix must not move the identity hash of an existing (default
    rollback-journal) database -- stored snapshot hashes depend on it."""
    db_path = tmp_path / "db.sqlite"
    conn = schema.connect(db_path)
    apply_migrations(conn, applied_at="2026-09-13T00:00:00Z")
    _build_snap1(conn)

    assert snapshot_dump_hash(db_path, "snap-1") == SNAP1_DUMP_HASH


# --------------------------------------------------------------------- #
# 2. It works in WAL mode (the fail-before case)
# --------------------------------------------------------------------- #

def test_dump_hash_works_in_wal_mode(tmp_path) -> None:
    """With the store in ``journal_mode=WAL`` and the writing connection left
    open, hashing must return a hash rather than raise. Before the fix the
    ``shutil.copyfile`` byte copy held none of the WAL-resident tables and
    raised ``sqlite3.OperationalError: no such table: params``."""
    db_path = tmp_path / "db.sqlite"
    conn = schema.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    apply_migrations(conn, applied_at="2026-09-13T00:00:00Z")
    _build_snap1(conn)  # connection stays open, nothing checkpointed

    dump_hash = snapshot_dump_hash(db_path, "snap-1")

    assert len(dump_hash) == 64
    assert all(char in "0123456789abcdef" for char in dump_hash)


# --------------------------------------------------------------------- #
# 3. WAL and rollback-journal modes agree on the hash
# --------------------------------------------------------------------- #

def test_dump_hash_agrees_across_wal_and_rollback_journal(tmp_path) -> None:
    """Identical logical content must hash identically regardless of the
    source journal mode -- otherwise the identity hash would depend on storage
    layout, not content."""
    rollback_path = tmp_path / "rollback.sqlite"
    rollback_conn = schema.connect(rollback_path)
    apply_migrations(rollback_conn, applied_at="2026-09-13T00:00:00Z")
    _build_snap1(rollback_conn)

    wal_path = tmp_path / "wal.sqlite"
    wal_conn = schema.connect(wal_path)
    wal_conn.execute("PRAGMA journal_mode=WAL")
    apply_migrations(wal_conn, applied_at="2026-09-13T00:00:00Z")
    _build_snap1(wal_conn)

    rollback_hash = snapshot_dump_hash(rollback_path, "snap-1")
    wal_hash = snapshot_dump_hash(wal_path, "snap-1")  # wal_conn still open

    assert wal_hash == rollback_hash


# --------------------------------------------------------------------- #
# 4. Un-checkpointed WAL data is included in the hash
# --------------------------------------------------------------------- #

def test_dump_hash_includes_uncheckpointed_wal_data(tmp_path) -> None:
    """Committed rows still resident in the ``-wal`` sidecar (never
    checkpointed, writing connection still open) must be reflected in the
    hash: the WAL-mode hash of identical content equals the known-good
    constant, proving the committed-but-uncheckpointed rows were captured."""
    db_path = tmp_path / "db.sqlite"
    conn = schema.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    apply_migrations(conn, applied_at="2026-09-13T00:00:00Z")
    _build_snap1(conn)  # committed, but left in the -wal (no checkpoint, conn open)

    wal_sidecar = db_path.with_name(db_path.name + "-wal")
    assert wal_sidecar.exists() and wal_sidecar.stat().st_size > 0

    # Hash while the committed rows live only in the sidecar: the result must
    # match the constant a fully-materialised rollback-mode database produces.
    assert snapshot_dump_hash(db_path, "snap-1") == SNAP1_DUMP_HASH
