# Architecture v2 — target structure and migration path

## 1. Why restructure (what hurts now)

- Scripts are the pipeline (`scripts/gather.py`, `derive.py`, `verify.py`,
  `promote.py`, `run_all.py`) and are imported via `importlib` in tests; a
  second, dead ingestion stack lives in `cvi/ingest.py` (669 lines, never run).
- Bundles are untyped dicts; a dropped key becomes a silent default (this is
  how F5 and F7 stayed invisible behind a green suite).
- Missing values are sentinels (`0.0`, `50`, `None`) with different meanings per
  field; the sentinel policy is the largest scoring distortion (Q1).
- `cvi/lakehouse.py` mixes schema, writes, queries, seeding, deep-dive storage
  and a Python full-scan persona re-projection.
- Scoring reads the wall clock; nothing is reproducible.
- The synthetic catalog is a first-class module (`cvi/benchmark_data.py`,
  `cvi/benchmark.py`, `scripts/demo_app.py`) that can be served.

## 2. Target layout

```
cvi/
  core/                 pure, stdlib only, no I/O, no clock
    models.py           Measured/Unmeasured, ParamValue, GateResult, ScoreRecord, enums
    constants_v2.py     frozen constants + CONSTANTS_HASH + BENCHMARK_VERSION
    gates.py            D-GATE-1..6, D-EVID-1
    hygiene.py          actor typing, author eligibility, per-author caps, windows
    params/             one module per pillar; one function per parameter -> ParamValue
    normalize.py        mid-rank ECDF over frozen populations
    aggregate.py        renormalisation, coverage, composite, ceilings
    matrix.py           coordinates, quadrant
    persona.py          presets, persona gates, projection weights
    tiebreak.py         D-TIE-1 key, tie groups
    engine.py           score(bundle, populations, snapshot) -> ScoreRecord
  collect/              network only; every function returns an Envelope
    envelope.py         Envelope contract, write/read, hashing
    github_graphql.py   windowed PR query, author history, label list, actor types
    github_rest.py      repo object (archived, mirror_url, fork, license), commits w/ emails, issues, runs, tree, check-runs
    depsdev.py          dependents (package level), project metadata
    scorecard.py        OpenSSF scorecard
    registries.py       npm, crates, pypistats (throttled), BigQuery (optional)
    ratelimit.py        token bucket, ETag cache, circuit breaker (port from ingest.py, then delete it)
    checkpoint.py       envelope-exists-is-done + null-is-not-done
  derive/
    bundle.py           FeatureBundle (schema_version=2), load/dump/validate
    features/           one deriver per parameter input; each states its missingness class
  verify/
    crosscheck.py       second-source checks (monotone counters, licence)
    provenance.py       envelope hash checks, unverifiable-provenance rule
    live_sample.py      nightly canary
    detectors.py        §6 of 03-ANTI-MANIPULATION
    report.py           VerificationReport
  snapshot/
    manifest.py         freeze, Merkle root, manifest.json
    populations.py      build + freeze ECDF populations
    reproduce.py        rebuild + canonical dump hash compare
  store/
    schema.py           DDL v2
    migrations/         0001_init_v2.sql, ... (numbered, forward-only, tested)
    writer.py           upsert rows, params (long), populations, snapshots
    reader.py           leaderboard/matrix/repo/evidence queries; SQL persona projection with window functions
  serve/
    api.py              FastAPI app (existing endpoints + v2 fields + /evidence + /snapshot)
    static/             web UI (unchanged for now)
  audit/
    coverage.py         missingness/coverage report (the table in 01-FINDINGS)
    sensitivity.py      D-AGG-3 report
    anchors.py          D-VER-4 relational anchors
    ties.py             tie statistics
  cli.py                cvi universe|gather|derive|verify|snapshot|score|promote|serve|audit|reproduce
tests/
  core/ collect/ derive/ verify/ snapshot/ store/ serve/ audit/   (mirrors cvi/)
  fixtures/synthetic_catalog.py   (ex cvi/benchmark_data.py; never importable from cvi/)
  fixtures/snapshot_50/           (committed raw envelopes for reproduce-in-CI)
scripts/                thin shims calling cvi.cli (kept one release for compatibility, then removed)
```

Deleted: `cvi/ingest.py` (after `ratelimit.py` absorbs the limiter),
`cvi/benchmark.py`, `scripts/demo_app.py` (folded into a test helper),
`scripts/curate_dataset_llm.py`, `HANDOFF-DATASET-CURATION-AGENT.md`.

## 3. Core contracts

```python
@dataclass(frozen=True)
class Measured:
    value: float | int | str
    n: int                      # sample size behind the value (0 for scalars)
    window_start: str | None    # ISO date
    window_end: str | None
    method: str                 # "primary" | "proxy:<name>"
    source: str                 # envelope path
    source_hash: str            # sha256 of the envelope body
    fetched_at: str

@dataclass(frozen=True)
class Unmeasured:
    reason: Literal["third_party_gap", "collector_failure", "insufficient_n", "not_applicable"]
    detail: str = ""

ParamValue = Measured | Unmeasured

@dataclass(frozen=True)
class Envelope:
    source_url: str
    fetched_at: str
    http_status: int
    etag: str | None
    sha256: str
    collector_version: str
    data: Any

@dataclass
class FeatureBundle:            # data/derived/<key>.json, schema_version = 2
    schema_version: int
    repo: RepoIdentity          # owner, name, ecosystem, archived, mirror_url, fork, license_spdx, default_branch
    inputs: dict[str, ParamValue]   # keyed by parameter input id (e.g. "ttfhr_hours", "dependents_deduped")
    pr_window: PRWindow         # 180-day window, truncated flag, events
    cohorts: Cohorts            # decision cohort, newcomer cohort (sampled authors + histories)
    provenance_hash: str        # hash of all referenced envelope hashes

@dataclass(frozen=True)
class ScoreRecord:
    snapshot_id: str
    benchmark_version: str
    constants_hash: str
    raw_hash: str
    gate: GateResult            # eligible, reason, routed, undecided gates
    params: dict[str, ParamScore]   # score, measured flag, reason, n, method
    pillars: dict[str, PillarScore] # score, coverage
    composite: float | None
    coverage: float
    tier: str | None            # after ceiling
    leverage: float | None; friction: float | None; quadrant: str | None
    persona_gates: dict[str, str]
    persona_composites: dict[str, float]
    tie_key: tuple; tie_group: int | None; rank: int | None
    suspicion: list[str]
```

Rules: `core/` never imports `collect/`, `derive/`, `store/`; `derive/`
imports `core.models` only; `store/` imports `core` for enums and the tie key
and nothing else. A test enforces the import graph.

## 4. Data flow

```
universe.jsonl ─ gather ─► data/raw/<collector>/<key>.json (Envelope)
                 derive ─► data/derived/<key>.json (FeatureBundle v2, ParamValue per input)
                 verify ─► data/verified/<key>.json (bundle + VerificationReport; clean/flags)
        snapshot freeze ─► data/snapshots/<id>/manifest.json (+ populations.json)
                  score ─► ScoreRecord per repo (pure; needs bundle + populations + snapshot)
                promote ─► data/cvi.db (rows, params long table, populations, snapshots)
                  serve ─► API + UI (refuses DBs without snapshot_id / with foreign constants_hash)
              reproduce ─► rebuild from raw + manifest, compare canonical dump hash
```

`run_all` becomes `cvi run --universe ... --as-of ...` and is resumable through
the envelope checkpoint (existing behaviour, kept: a null registry value is not
"done").

## 5. Store schema v2

```sql
CREATE TABLE snapshots (
  id TEXT PRIMARY KEY, as_of TEXT NOT NULL, benchmark_version TEXT NOT NULL,
  constants_hash TEXT NOT NULL, code_commit TEXT NOT NULL, manifest_root TEXT NOT NULL,
  created_at TEXT NOT NULL);

CREATE TABLE repositories (
  id INTEGER PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES snapshots(id),
  owner TEXT NOT NULL, name TEXT NOT NULL, ecosystem TEXT NOT NULL,
  raw_hash TEXT NOT NULL, constants_hash TEXT NOT NULL,
  -- identity facts
  stars_audited INTEGER, forks INTEGER, license_spdx TEXT, archived INTEGER, mirror INTEGER, fork INTEGER,
  -- gates
  eligible INTEGER NOT NULL, gate_reason TEXT, routed TEXT, rated INTEGER NOT NULL, unrated_reason TEXT,
  -- pillars (NULL = unmeasured) and their coverage
  p_responsiveness REAL, c_responsiveness REAL, p_criticality REAL, c_criticality REAL,
  p_governance REAL, c_governance REAL, p_rigor REAL, c_rigor REAL, p_accessibility REAL, c_accessibility REAL,
  -- composite
  cvi REAL, coverage REAL NOT NULL, tier TEXT, tier_ceiling TEXT,
  leverage REAL, friction REAL, quadrant TEXT,
  -- ordering
  rank INTEGER, tie_group INTEGER, ttfhr_p75 REAL, last_human_commit_at TEXT,
  -- persona
  persona_gates TEXT NOT NULL DEFAULT '{}',   -- JSON {persona: reason}
  suspicion TEXT NOT NULL DEFAULT '[]',       -- JSON list
  deep_dive_json TEXT,
  UNIQUE(snapshot_id, owner, name));

CREATE TABLE params (                          -- long format: "why this score"
  repo_id INTEGER NOT NULL REFERENCES repositories(id), param_id TEXT NOT NULL,
  raw_value TEXT, score REAL, measured INTEGER NOT NULL, reason TEXT, n INTEGER,
  window_start TEXT, window_end TEXT, method TEXT, source TEXT, source_hash TEXT, fetched_at TEXT,
  PRIMARY KEY (repo_id, param_id));

CREATE TABLE populations (                     -- frozen ECDF inputs
  snapshot_id TEXT NOT NULL, ecosystem TEXT NOT NULL, param_id TEXT NOT NULL,
  sorted_values TEXT NOT NULL,                 -- JSON array
  fallback INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (snapshot_id, ecosystem, param_id));

CREATE INDEX idx_rank ON repositories(snapshot_id, rated, cvi DESC, coverage DESC);
CREATE INDEX idx_eco  ON repositories(snapshot_id, ecosystem, cvi DESC);
CREATE INDEX idx_name ON repositories(owner, name);
```

Migrations: numbered SQL files applied by `store/migrations/__init__.py` with a
`schema_version` row; a test applies all migrations to an empty database and to
a v1 database copy and asserts the resulting schema equals `schema.py`.
`RETURNING` is avoided so SQLite ≥ 3.35 is not required.

## 6. Queries

Persona projection is a linear expression over stored pillar columns with
per-pillar coverage renormalisation, so:

```sql
WITH w AS (SELECT :wr AS wr, :wc AS wc, :wg AS wg, :wi AS wi, :wa AS wa),
s AS (
  SELECT r.*,
    (COALESCE(wr*p_responsiveness,0)+COALESCE(wc*p_criticality,0)+COALESCE(wg*p_governance,0)
     +COALESCE(wi*p_rigor,0)+COALESCE(wa*p_accessibility,0))
    / NULLIF((CASE WHEN p_responsiveness IS NULL THEN 0 ELSE wr END)+ ... ,0) AS cvi_p
  FROM repositories r, w WHERE r.snapshot_id=:sid AND r.rated=1
    AND json_extract(r.persona_gates, '$.' || :persona) IS NULL)
SELECT s.*, RANK() OVER (ORDER BY ROUND(cvi_p,2) DESC NULLS LAST, coverage DESC NULLS LAST,
                         p_responsiveness DESC NULLS LAST, friction ASC NULLS LAST,
                         leverage DESC NULLS LAST, ttfhr_p75 ASC NULLS LAST,
                         last_human_commit_at DESC NULLS LAST,
                         lower(owner||'/'||name) ASC) AS rank_p
FROM s LIMIT :limit OFFSET :offset;
```

`NULLS LAST` on every tie-break column is mandatory (ruling 2026-09-13 after the
W2-6 review): SQLite sorts NULL first under `ASC` by default, which would put a
repository with an *unmeasured* friction ahead of every measured one — the
inverse of D-TIE-1. SQLite ≥ 3.30 is required for the syntax; the parity test
between this query and `cvi.core.tiebreak.tie_key` must include NULLs at more
than one tie level. The store also needs `RANK()` to yield strict ranks after
the full key, so `ROW_NUMBER()` over the same ORDER BY is used for `rank` and
a separate `DENSE_RANK() OVER (ORDER BY ROUND(cvi_p,2) DESC)` for `tie_group`.

No Python loop over all rows for any endpoint.

## 7. API v2 (additive)

- Every row gains `coverage`, `tier_ceiling`, `tie_group`, `snapshot_id`,
  `benchmark_version`, `suspicion`, `persona_gates`.
- `GET /v1/cvi/repos/{o}/{n}/evidence` → the `params` rows for the repo (value,
  score, measured, reason, n, window, method, source URL, fetched_at).
- `GET /v1/cvi/snapshot` → manifest summary (id, as_of, version, constants
  hash, root, counts of rated/gated/unrated, detector summary).
- `GET /v1/cvi/unrated` → gated and unrated repos with reasons.
- `reproject` keeps its contract; it is now the SQL in §6.

## 8. Determinism and time

- `core/` has no `datetime.now()`, `time.time()` or random; a test greps and
  fails on any occurrence.
- `as_of` comes from the snapshot; windows are computed from it.
- Iteration over dicts and directory listings is sorted before any arithmetic
  that could depend on order (ECDF populations, cohorts, caps).
- Floats are rounded once, at the end, to 2 decimals; comparisons for gates and
  ceilings use unrounded values.

## 9. Testing strategy (what replaces "100% coverage")

- Oracle tests per parameter with hand-computed values (kept).
- Monotonicity property test per curve (D-PARAM-MONO).
- Contract tests at each boundary: Envelope → derive, bundle schema v2
  validation, ScoreRecord → store.
- Reproduce-in-CI on `tests/fixtures/snapshot_50` (byte-identical dump hash).
- Anchor tests (D-VER-4) against the fixture snapshot, and against the full
  snapshot as a release gate.
- Mutation battery kept for `core/`.
- Import-graph test (§3), no-clock test (§8), no-fixture-in-serve test (§5 of
  `03`).

## 10. Migration path (strangler, no big bang)

1. Create `cvi/core/models.py` (Measured/Unmeasured) and `constants_v2.py`
   alongside the existing engine; nothing imports them yet (W2-1).
2. New collectors and derivers write bundle **v2** files under a scratch data
   dir; v1 files stay untouched until the sweep finishes (W1-*).
3. New engine in `cvi/core/` scores v2 bundles; old engine keeps serving the v1
   DB until W5-2 (W2-*).
4. Store v2 as a new schema with migrations; `serve` reads either until the
   v1 DB is retired (W4-2).
5. Scripts become shims; dead modules deleted in one packet with a reader
   sweep (W4-1).
6. Full-universe v2 run, manifest, release (W5-2).

## 11. Non-goals for v2

- Replacing SQLite with DuckDB/Parquet (the spec's lakehouse): not needed at
  10k repos; the store interface keeps SQL ANSI so it remains possible.
- Rewriting the UI; only additive fields (coverage badge, tie marks, evidence
  drawer) in W4-5.
- GHArchive streaming; the windowed GraphQL queries cover v2. GHArchive is the
  fallback if the author-history cost (W1-4) exceeds budget.
