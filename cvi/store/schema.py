"""CVI v2 store schema (04 section 5, extended per 06 section 4).

Deviations from the `04` section 5 draft, and the full rationale for each,
are recorded in ``.agents/W4-2/handoff.md`` (REWORK round 1, F-014: kept
out of this docstring to hold the size class). In one line: ``repositories``
gains ``leverage_coverage``/``friction_coverage``/``persona_composites``/
``gate_undecided`` (06 section 4 fields the draft omits); ``benchmark_version``
is joined from ``snapshots``, never duplicated; ``snapshots`` is a superset
of INT-W0-4's ``snapshot_meta``; ``populations`` matches
``cvi.snapshot.populations.build_populations_table``'s real row shape.

:data:`SCHEMA_STATEMENTS` is the single Python-side DDL definition;
:func:`create_schema` executes it directly (fixtures/oracle tests);
``cvi/store/migrations/0001_init_v2.sql`` reaches the same schema
independently (forward-only history), and
``tests/store/test_migrations.py`` keeps the two in sync via
:func:`introspect_schema` (structural, not textual, comparison).

No ``RETURNING`` clause anywhere in this module or the migrations (`04`
section 5: avoided so SQLite >= 3.35 is not required); a grep-style test in
``tests/store/test_schema.py`` enforces it over ``cvi/store`` (docstring
prose mentioning the word, like this one, is excluded from that check).

G-006 ruling (REWORK round 3): ``repositories.stars_audited`` has no v2
producer yet -- W1-10 owns the StarScout-audited value that will fill it.
The writer stores ``NULL`` there, never a raw, unaudited star count under
this column's audited-sounding name (that would be fabrication, not a
placeholder); the column stays nullable for exactly that reason, and the
UI renders "--" for it per `06` U-7 until W1-10 lands.
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
        gate_undecided TEXT NOT NULL DEFAULT '[]',
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


def connect(path) -> sqlite3.Connection:
    """The one place a caller should open a connection to a v2 store
    database (G-004, REWORK round 3): sets ``PRAGMA foreign_keys = ON``
    before returning it. SQLite disables foreign-key enforcement by
    default, so every ``REFERENCES`` clause in this schema
    (``params.repo_id``, ``repositories.snapshot_id``) is decorative on a
    plain ``sqlite3.connect()`` -- an orphan row is silently accepted and
    only ``PRAGMA foreign_key_check`` would ever find it. Deliberately NOT
    used by ``cvi.store.writer.snapshot_dump_hash``'s internal scratch
    copy: that helper's whole job is a destructive ``DELETE``/``DROP``
    scoping of a throwaway copy, and ``DROP TABLE snapshots`` while
    ``repositories`` rows still reference it fails under FK enforcement
    (verified) -- enforcing integrity on a copy being intentionally torn
    apart for hashing would break the very operation, not protect it."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


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
