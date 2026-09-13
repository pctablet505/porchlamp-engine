"""CVI v2 store migration runner (04 sections 5, 10).

Numbered, forward-only ``NNNN_description.sql`` files in this directory are
applied in order and recorded in ``schema_migrations``; a file already
recorded is skipped. No ``RETURNING`` (SQLite >= 3.35 not required, `04`
section 5).

Legacy handling (04 section 10 migration path item 4, "``serve`` reads
either until the v1 DB is retired"): migration ``0001`` creates a table
named ``repositories``. When this runner is pointed at an existing v1
database (``cvi.lakehouse``'s schema: a ``repositories`` table with no
``snapshot_id`` column), it renames that table to ``legacy_repositories``
*before* applying ``0001``, so the v1 rows are preserved unchanged under
the new name and ``0001``'s ``CREATE TABLE IF NOT EXISTS repositories``
then creates the empty v2 table with no name collision and no dropped data.
On an empty database (no pre-existing ``repositories`` table at all) this
check is a no-op. The check runs once, keyed to migration ``0001``
specifically (the only migration that could ever collide with the v1
table name); later migrations never repeat it.

F-001 (REWORK round 1): each migration file's statements -- the legacy
rename included -- run inside ONE explicit ``BEGIN``/``COMMIT``, with
``rollback()`` on any exception, so a failure partway through a file
undoes everything that file did, the rename included; ``executescript()``
is never used (it commits any pending transaction before it runs, which is
exactly what made the previous version's failure non-atomic).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Sequence

MIGRATIONS_DIR = Path(__file__).resolve().parent

#: The one migration version this runner checks for a v1-shaped
#: ``repositories`` table before applying (see module docstring).
_LEGACY_RENAME_VERSION = 1
_LEGACY_TABLE = "repositories"
_LEGACY_TABLE_RENAMED = "legacy_repositories"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _is_v1_shaped(conn: sqlite3.Connection) -> bool:
    """True iff an existing ``repositories`` table predates the v2 schema
    (no ``snapshot_id`` column: `04` section 5's v2 table always has one)."""
    columns = {row[1] for row in conn.execute('PRAGMA table_info("repositories")').fetchall()}
    return "snapshot_id" not in columns


def discover_migrations(migrations_dir: Path | None = None) -> Sequence[Path]:
    """Every ``NNNN_*.sql`` file in ``migrations_dir`` (default: this
    directory), sorted by version number."""
    directory = migrations_dir or MIGRATIONS_DIR
    return tuple(sorted(directory.glob("[0-9]" * 4 + "_*.sql"), key=lambda p: p.name))


def _version_of(path: Path) -> int:
    return int(path.name.split("_", 1)[0])


def _strip_line_comments(sql_text: str) -> str:
    """Drop everything from ``--`` to end of line on every line -- a
    migration file's header comment is prose and may contain a semicolon
    of its own (this file's does), which a naive split on ``;`` would
    mistake for a statement boundary."""
    return "\n".join(line[: line.find("--")] if "--" in line else line for line in sql_text.splitlines())


def _split_statements(sql_text: str) -> list[str]:
    """Split a migration file into individually-executable statements on
    ``;``, after stripping line comments -- safe for this schema's DDL (no
    string literal in it contains a semicolon or ``--``; the only string
    literals are ``DEFAULT '{}'``/``'[]'``)."""
    stripped = _strip_line_comments(sql_text)
    return [chunk.strip() for chunk in stripped.split(";") if chunk.strip()]


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    """Versions already recorded in ``schema_migrations``; empty set (not an
    error) when the table itself does not exist yet."""
    if not _table_exists(conn, "schema_migrations"):
        return set()
    return {row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}


def apply_migrations(conn: sqlite3.Connection, applied_at: str, migrations_dir: Path | None = None) -> list[int]:
    """Apply every not-yet-applied migration in order; return the versions applied.

    ``applied_at`` is required and stored verbatim per migration (no
    wall-clock read in this module, R rules): the caller supplies it, same
    pattern as a snapshot's ``created_at``.

    F-001: each file's statements run inside their OWN explicit
    transaction (``BEGIN`` ... ``COMMIT``, ``rollback()`` on any
    exception, then re-raise) -- a failure on the k-th statement of a file
    leaves the database exactly as it was before that file started
    (checksummed by the caller's own test), including the legacy rename.
    Migrations already committed by an earlier file in the same call stay
    committed (forward-only progress is still made file-by-file).

    F-010: raises ``RuntimeError`` if no migration files are found at all --
    an empty directory is a packaging/deployment defect, not "nothing to
    do".
    """
    if not isinstance(applied_at, str) or not applied_at:
        raise ValueError(f"applied_at must be a non-empty string, got {applied_at!r}")
    files = discover_migrations(migrations_dir)
    if not files:
        raise RuntimeError(f"no migration files found under {migrations_dir or MIGRATIONS_DIR}")
    done = applied_versions(conn)
    newly_applied: list[int] = []
    for path in files:
        version = _version_of(path)
        if version in done:
            continue
        statements = _split_statements(path.read_text(encoding="utf-8"))
        conn.execute("BEGIN")
        try:
            if version == _LEGACY_RENAME_VERSION and _table_exists(conn, _LEGACY_TABLE) and _is_v1_shaped(conn):
                conn.execute(f'ALTER TABLE "{_LEGACY_TABLE}" RENAME TO "{_LEGACY_TABLE_RENAMED}"')
            for statement in statements:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, applied_at),
            )
        except Exception:
            conn.rollback()
            raise
        conn.commit()
        newly_applied.append(version)
    return newly_applied
