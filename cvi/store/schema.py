"""CVI v2 store schema (04 section 5, extended per 06 section 4).

The canonical, current-state schema as code: :data:`SCHEMA_STATEMENTS` is the
single Python-side definition of every table and index; :func:`create_schema`
executes it against a connection (used directly by fixtures/oracle tests, and
indirectly -- by producing byte-for-byte the same statements -- by
``cvi/store/migrations/0001_init_v2.sql``, which a forward-only migration
history must reach independently). ``tests/store/test_migrations.py`` is the
guard that keeps the two in sync (structural comparison via
:func:`introspect_schema`, not a string diff, so formatting differences are
not false positives).

Deviations from `04` section 5's draft, documented (R-2, one definition per
fact, no duplicated thresholds/shapes):

- ``repositories`` gains ``leverage_coverage``/``friction_coverage``
  (D-MAT-1 coordinate coverage, already on ``ScoreRecord`` -- W2-5 review
  W-1(c)) and ``persona_composites`` (JSON): both are in the `06` section 4
  UI row contract but missing from the draft table.
- ``benchmark_version`` is NOT duplicated onto ``repositories``: it lives
  once on ``snapshots``, joined via ``snapshot_id``
  (:func:`cvi.store.reader.get_row` performs the join).
- ``snapshots`` gains ``canonical_dump_hash``/``promoted_at`` so the table
  is a superset of branch INT-W0-4's v1 ``snapshot_meta`` (same column
  names; this table's ``id`` plays ``snapshot_meta.snapshot_id``'s role) --
  so the served-database guard can be repointed here later, unchanged.
- ``populations`` matches the row shape ``cvi.snapshot.populations.
  build_populations_table`` (already landed, W2-4/W2-9) actually emits --
  ``(snapshot_id, ecosystem, param_id, n, fallback, values_json,
  values_hash)`` -- not the draft's ``sorted_values``-only shape. That
  function's own docstring says "the schema and the migration are W4-2's";
  matching its real output, not inventing an incompatible second shape, is
  the one-definition-per-fact choice.

``schema_migrations`` (version, applied_at) is bookkeeping for the runner
(``cvi/store/migrations/__init__.py``), not named by `04` section 5, but is
part of "the schema" for the parity test's purposes (both paths create it).

No ``RETURNING`` clause anywhere in this module or the migrations (`04`
section 5: avoided so SQLite >= 3.35 is not required); a grep-style test in
``tests/store/test_schema.py`` enforces it over ``cvi/store`` (docstring
prose mentioning the word, like this one, is excluded from that check).
"""
from __future__ import annotations

import sqlite3
from typing import Dict, List, Tuple

#: Tables created by this schema, in dependency order (referenced-before-
#: referencing, so a fresh executescript can run them in this order too).
TABLE_NAMES: Tuple[str, ...] = (
    "schema_migrations",
    "snapshots",
    "repositories",
    "params",
    "populations",
)

SCHEMA_STATEMENTS: Tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS snapshots (
        id TEXT PRIMARY KEY,
        as_of TEXT NOT NULL,
        benchmark_version TEXT NOT NULL,
        constants_hash TEXT NOT NULL,
        code_commit TEXT NOT NULL,
        manifest_root TEXT NOT NULL,
        canonical_dump_hash TEXT,
        created_at TEXT NOT NULL,
        promoted_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS repositories (
        id INTEGER PRIMARY KEY,
        snapshot_id TEXT NOT NULL REFERENCES snapshots(id),
        owner TEXT NOT NULL,
        name TEXT NOT NULL,
        ecosystem TEXT NOT NULL,
        raw_hash TEXT NOT NULL,
        constants_hash TEXT NOT NULL,
        stars_audited INTEGER,
        forks INTEGER,
        license_spdx TEXT,
        archived INTEGER,
        mirror INTEGER,
        fork INTEGER,
        eligible INTEGER NOT NULL,
        gate_reason TEXT,
        routed TEXT,
        rated INTEGER NOT NULL,
        unrated_reason TEXT,
        p_responsiveness REAL,
        c_responsiveness REAL,
        p_criticality REAL,
        c_criticality REAL,
        p_governance REAL,
        c_governance REAL,
        p_rigor REAL,
        c_rigor REAL,
        p_accessibility REAL,
        c_accessibility REAL,
        cvi REAL,
        coverage REAL NOT NULL,
        tier TEXT,
        tier_ceiling TEXT,
        leverage REAL,
        leverage_coverage REAL,
        friction REAL,
        friction_coverage REAL,
        quadrant TEXT,
        rank INTEGER,
        tie_group INTEGER,
        ttfhr_p75 REAL,
        last_human_commit_at TEXT,
        persona_gates TEXT NOT NULL DEFAULT '{}',
        persona_composites TEXT NOT NULL DEFAULT '{}',
        suspicion TEXT NOT NULL DEFAULT '[]',
        deep_dive_json TEXT,
        UNIQUE(snapshot_id, owner, name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS params (
        repo_id INTEGER NOT NULL REFERENCES repositories(id),
        param_id TEXT NOT NULL,
        raw_value TEXT,
        score REAL,
        measured INTEGER NOT NULL,
        reason TEXT,
        n INTEGER,
        window_start TEXT,
        window_end TEXT,
        method TEXT,
        source TEXT,
        source_hash TEXT,
        fetched_at TEXT,
        PRIMARY KEY (repo_id, param_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS populations (
        snapshot_id TEXT NOT NULL,
        ecosystem TEXT NOT NULL,
        param_id TEXT NOT NULL,
        n INTEGER NOT NULL,
        fallback INTEGER NOT NULL DEFAULT 0,
        values_json TEXT NOT NULL,
        values_hash TEXT NOT NULL,
        PRIMARY KEY (snapshot_id, ecosystem, param_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rank ON repositories(snapshot_id, rated, cvi DESC, coverage DESC)",
    "CREATE INDEX IF NOT EXISTS idx_eco ON repositories(snapshot_id, ecosystem, cvi DESC)",
    "CREATE INDEX IF NOT EXISTS idx_name ON repositories(owner, name)",
)


def create_schema(conn: sqlite3.Connection) -> None:
    """Execute every statement in :data:`SCHEMA_STATEMENTS` against ``conn``.

    Idempotent (every statement is ``IF NOT EXISTS``); does not commit --
    the caller controls the transaction."""
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)


def introspect_schema(conn: sqlite3.Connection) -> Dict[str, object]:
    """A structural fingerprint of ``conn``'s schema, independent of DDL
    text formatting (`04` section 5's parity test: "the resulting schema
    equals ``schema.py``'s DDL, normalised").

    For each user table (``sqlite_%`` excluded): its sorted column tuples
    ``(name, type, notnull, dflt_value, pk)`` from ``PRAGMA table_info``, and
    the *multiset of column-sets* every index on it covers (auto-indexes
    from ``UNIQUE`` constraints included) -- keyed by table, not by the
    index name SQLite happens to assign, so two schemas built the same way
    in a different statement order still compare equal on substance. An
    expression-index column (``PRAGMA index_info`` reports ``cid=-1``,
    ``name=NULL`` -- the legacy v1 schema this module's own migration
    preserves has one, ``idx_cvi_search ON repositories (LOWER(owner),
    LOWER(name))``) is represented as a placeholder string keyed by its
    position, never a bare ``None`` (sorting a mix of ``str`` and ``None``
    raises ``TypeError`` -- the v1-migration test is what first found this).
    """
    tables: Dict[str, List[tuple]] = {}
    indexes: Dict[str, List[tuple]] = {}
    table_rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    for (table,) in table_rows:
        columns = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        tables[table] = sorted(
            (col[1], col[2], col[3], col[4], col[5]) for col in columns
        )
        index_rows = conn.execute(f'PRAGMA index_list("{table}")').fetchall()
        column_sets = []
        for index_row in index_rows:
            index_name = index_row[1]
            index_columns = conn.execute(f'PRAGMA index_info("{index_name}")').fetchall()
            ordered = sorted(index_columns, key=lambda row: row[0])  # seqno: always an int
            column_sets.append(
                tuple(row[2] if row[2] is not None else f"<expr:{row[1]}>" for row in ordered)
            )
        indexes[table] = sorted(column_sets)
    return {"tables": tables, "indexes": indexes}
