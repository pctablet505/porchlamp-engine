-- 0001_init_v2.sql -- Porchlamp v2 store schema (04 section 5; porchlamp/store/schema.py
-- is the same DDL as Python; tests/store/test_migrations.py asserts the two
-- stay structurally identical).
--
-- Legacy note: when this migration is applied to a v1 database (one whose
-- `repositories` table has no `snapshot_id` column), the runner
-- (porchlamp/store/migrations/__init__.py) renames that table to
-- `legacy_repositories` BEFORE running this file, so `CREATE TABLE
-- repositories` below always creates the fresh v2 table, never collides
-- with v1 data, and drops nothing (04 section 10 migration path).

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

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
);

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
    porchlamp REAL,
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
);

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
);

CREATE TABLE IF NOT EXISTS populations (
    snapshot_id TEXT NOT NULL,
    ecosystem TEXT NOT NULL,
    param_id TEXT NOT NULL,
    n INTEGER NOT NULL,
    fallback INTEGER NOT NULL DEFAULT 0,
    values_json TEXT NOT NULL,
    values_hash TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, ecosystem, param_id)
);

CREATE INDEX IF NOT EXISTS idx_rank ON repositories(snapshot_id, rated, porchlamp DESC, coverage DESC);
CREATE INDEX IF NOT EXISTS idx_eco ON repositories(snapshot_id, ecosystem, porchlamp DESC);
CREATE INDEX IF NOT EXISTS idx_name ON repositories(owner, name);
