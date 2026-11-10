"""Concurrency-safe connection settings for ``porchlamp.store.schema.connect`` (D-02).

The v2 write path (a promote writing a whole snapshot in one transaction)
runs against the same file the API server holds read connections open on.
Without ``journal_mode = WAL`` a writer and a reader cannot proceed
concurrently, and with SQLite's default ``busy_timeout`` of 0 the loser of the
first conflict raises ``database is locked`` instead of waiting. These tests
lock in the three settings that make serve-while-promoting safe, and guard
that the change does not break an in-memory database."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from porchlamp.store import schema
from porchlamp.store.writer import write_records, write_snapshot

from .helpers import make_rated_record, make_repo_write


def _seed_snapshots_row(conn: sqlite3.Connection, snapshot_id: str = "snap-1") -> None:
    """Create the v2 schema and one committed ``snapshots`` row, so a later
    ``repositories`` insert satisfies the ``snapshot_id`` foreign key. Uses
    the real schema and writer, not a throwaway table."""
    schema.create_schema(conn)
    write_snapshot(
        conn, id=snapshot_id, as_of="2026-09-13", benchmark_version="2.0.0-dev",
        constants_hash="c" * 64, code_commit="deadbeef", manifest_root="r" * 8,
        created_at="2026-09-13T00:00:00Z",
    )


def test_file_database_is_in_wal_mode(tmp_path: Path) -> None:
    """A store opened at a real file is in WAL mode, so a promote's write
    transaction and the server's reads overlap instead of colliding."""
    conn = schema.connect(tmp_path / "porchlamp.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


def test_busy_timeout_is_set(tmp_path: Path) -> None:
    """A contending connection waits ``BUSY_TIMEOUT_MS`` instead of raising on
    the first conflict, which is the right behaviour for a snapshot write that
    takes seconds."""
    conn = schema.connect(tmp_path / "porchlamp.db")
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == schema.BUSY_TIMEOUT_MS
    finally:
        conn.close()


def test_reader_does_not_block_writer(tmp_path: Path) -> None:
    """With a write transaction open on one connection, a ``SELECT`` on a
    second connection to the same file succeeds. Under the old rollback-journal
    mode (the writer holding an exclusive lock, no busy_timeout) this raised
    ``sqlite3.OperationalError: database is locked``."""
    db_path = tmp_path / "porchlamp.db"

    writer = schema.connect(db_path)
    writer.isolation_level = None  # manual transaction control for the lock test
    _seed_snapshots_row(writer)  # committed snapshot row (FK parent)

    record = make_rated_record("snap-1", owner="acme", name="demo")
    writer.execute("BEGIN EXCLUSIVE")
    write_records(writer, "snap-1", [make_repo_write(record)])  # joins the ambient txn, does not commit

    reader = schema.connect(db_path)
    try:
        # The writer's row is uncommitted, so the reader sees the committed
        # state (zero repositories); the point is this SELECT does not raise.
        assert reader.execute("SELECT COUNT(*) FROM repositories").fetchone()[0] == 0
    finally:
        writer.execute("ROLLBACK")
        writer.close()
        reader.close()


def test_in_memory_connection_still_works() -> None:
    """``:memory:`` cannot host a WAL and reports ``memory`` mode -- that is
    correct, not a failure. Opening one must not raise and foreign-key
    enforcement must still be on."""
    conn = schema.connect(":memory:")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_wal_mode_conversion_is_reported(tmp_path: Path) -> None:
    """``PRAGMA journal_mode`` returns the resulting mode as a row; on a normal
    file that row must read ``wal`` (the statement can silently fail to change
    mode, so connect reads and the test asserts the real outcome)."""
    conn = schema.connect(tmp_path / "porchlamp.db")
    try:
        conn.execute("CREATE TABLE probe (x INTEGER)")
        conn.commit()
    finally:
        conn.close()
    reopened = sqlite3.connect(tmp_path / "porchlamp.db")  # plain connect: the mode is persistent per-database
    try:
        assert reopened.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        reopened.close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
