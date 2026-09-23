# Architecture v2 — target structure and migration path

## 1. Why restructure (what hurts now)

- Scripts are the pipeline (`scripts/gather.py`, `derive.py`, `verify.py`,
  `promote.py`, `run_all.py`) and are imported via `importlib` in tests; a
  second, dead ingestion stack lives in `porchlamp/ingest.py` (669 lines, never run).
- Bundles are untyped dicts; a dropped key becomes a silent default (this is
  how F5 and F7 stayed invisible behind a green suite).
- Missing values are sentinels (`0.0`, `50`, `None`) with different meanings per
  field; the sentinel policy is the largest scoring distortion (Q1).
- `porchlamp/lakehouse.py` mixes schema, writes, queries, seeding, deep-dive storage
  and a Python full-scan persona re-projection.
- Scoring reads the wall clock; nothing is reproducible.
- The synthetic catalog is a first-class module (`porchlamp/benchmark_data.py`,
  `porchlamp/benchmark.py`, `scripts/demo_app.py`) that can be served.

## 2. Target layout

**Updated to reality by W4-1** (`.agents/W4-1/inventory.md`): most of this
tree already exists at its target path, built directly in place by
W0–W4-6 rather than written under an old tree and moved later. Rows below
are the real, current file names; a trailing note flags anywhere the
original target (still visible in git history) named something that was
never built, or that got built under a different, better name.

```
porchlamp/
  core/                 pure, stdlib only, no I/O, no clock (enforced by
                         tests/core/test_import_graph.py)
    models.py           Measured/Unmeasured, ParamValue, GateResult, ScoreRecord, enums
    constants.py        frozen constants + CONSTANTS_HASH + BENCHMARK_VERSION
    gates.py            D-GATE-1..6, D-EVID-1
    hygiene.py          actor typing, author eligibility, per-author caps, windows
    params/             one module per pillar; one function per parameter -> ParamValue
    normalize.py        mid-rank ECDF over frozen populations
    openssf.py          Rob Pike 10-signal OpenSSF criticality calculation
    aggregate.py        renormalisation, coverage, composite, ceilings
    matrix.py           coordinates, quadrant
    persona.py          presets, persona gates, projection weights
    tiebreak.py         D-TIE-1 key, tie groups
    commit_domains.py   commit-domain classification shared by collect/derive
    methodology.py      rendered-methodology support (scripts/render_methodology.py)
    pipeline.py         core.pipeline orchestration incl. not_ranked_class (NB-1)
    engine.py           score(bundle, populations, snapshot) -> ScoreRecord
  collect/              network + rate-limiting; every collector function
                         returns an Envelope (envelope.py's contract)
    envelope.py         Envelope contract, write/read, hashing
    github_graphql.py   windowed PR query, author history, label list, actor types
    repo_object.py      repo object (archived, mirror_url, fork, license)
    commits.py          commits w/ emails, commit-domain classification
    labels.py           issue/PR label collection
    pike_signals.py     OpenSSF-signal-shaped collection (not a scorecard API
                         call -- see porchlamp.core.openssf for the scoring side)
    depsdev.py          dependents (package level), project metadata
    ecosystemes.py      per-ecosystem registry helpers (npm/crates/pypi/...)
    universe.py         candidate universe filtering; owns envelope-checkpoint
                         logic inline (no separate checkpoint.py)
    governance.py        governance-signal collection
    ratelimit.py        token bucket, ETag cache, circuit breaker -- moved
                         from porchlamp/ingest.py verbatim (W4-1); stdlib + httpx
                         only, no porchlamp.* imports (tests/core/test_import_graph.py)
    (not built: `github_rest.py` as a single file -- split across
    repo_object.py/commits.py/labels.py/pike_signals.py instead, each
    independently tested; `scorecard.py`, `registries.py`, `checkpoint.py`
    -- no dedicated collector module exists for these, W1-10 lead-log row)
  derive/
    bundle.py           FeatureBundle (schema_version=2), load/dump/validate,
                         every INPUT_MAPPINGS resolver (2000+ lines --
                         `features/` per-parameter split not done; W4-1
                         flagged this out of its own scope, real but larger
                         than a "package move")
    gate_evidence.py    derives GateEvidence (manifest, vitality, license) from raw envelopes
  verify/
    detectors.py        §6 of 03-ANTI-MANIPULATION
    live_sample.py      nightly canary (tests-only: no script/cron wired yet)
    pipeline_hook.py     wires verify into core.pipeline
    (not built: `crosscheck.py`, `provenance.py`, `report.py` -- the v1
    crosscheck logic this doc anticipated moving here still lives inline
    in scripts/verify.py, the real, still-necessary v1 bridge)
  snapshot/
    manifest.py         freeze, Merkle root, manifest.json
    populations.py      build + freeze ECDF populations
    reproduce.py        rebuild + canonical dump hash compare
    fixture_io.py       shared reader for on-disk v2 snapshot directories
    build_cmd.py        `python -m porchlamp.snapshot build` -- build v2 snapshot directory from data/
    store_cmd.py        W4-6: `python -m porchlamp.snapshot store` -- score a
                         frozen snapshot into porchlamp.store
    cli.py, __main__.py `python -m porchlamp.snapshot` entry point
  store/
    schema.py           DDL v2
    migrations/         0001_init_v2.sql, 0002_indexes.sql (numbered,
                         forward-only, tested; declared as package data)
    writer.py           upsert rows, params (long), populations, snapshots
    reader.py           leaderboard/matrix/repo/evidence queries
    queries.py          shared SQL/decoding (ORDER_BY_SQL, row_to_dict) --
                         reader.py and writer.py both reuse this (R-2)
  serve/
    app.py              FastAPI app (`porchlamp.serve.app.create_app`) -- v2 at
                         `/`, v1 at `/legacy/` when a v1 shape exists,
                         `/healthz` (named `api.py` in the original target;
                         built as `app.py` instead)
    contract.py         response/request pydantic models (leaderboard,
                         matrix, evidence, snapshot, methodology, ...)
    guard.py            the served-DB guard (v1 fixture-clock + hash check,
                         v2 tamper check) -- not in the original target at
                         all; W4-4 REWORK moved it here from scripts/serve.py
                         so porchlamp/serve never imports scripts.* (enforced by
                         tests/core/test_import_graph.py)
    (web UI lives at the repo-root `web/` directory, not `porchlamp/serve/static/`)
  audit/
    coverage.py         missingness/coverage report (the table in 01-FINDINGS)
    sensitivity.py      D-AGG-3 report (imports porchlamp.constants.PILLAR_WEIGHTS,
                         a v1 table with no v2 equivalent yet -- flagged,
                         W4-1 inventory section 7)
    anchors.py          D-VER-4 relational anchors
    ties.py             tie statistics
    detectors_report.py  W4-6-era reporting module (tests-only: waits on a
                         `porchlamp.cli audit` subcommand that does not exist)
    forward.py           forward-looking audit report (tests-only, same wait)
    methodology_history.py  methodology-version history for the audit trail
  (cli.py does NOT exist and is not being built: the "scripts become thin
  shims calling porchlamp.cli" plan is DESCOPED, lead ruling 2026-09-13 -- every
  scripts/*.py file holds 5-18 real top-level functions, not "argparse +
  one call into a library"; scripts stay entry points, see below)
tests/
  core/ collect/ derive/ verify/ snapshot/ store/ serve/ audit/   (mirrors porchlamp/)
  fixtures/synthetic_catalog.py   (ex porchlamp/benchmark_data.py, moved W4-1;
                                    never importable from porchlamp/ OR scripts/,
                                    enforced by tests/core/test_import_graph.py)
  fixtures/snapshot_50/           (committed raw envelopes for reproduce-in-CI)
  helpers/demo_app.py             (ex scripts/demo_app.py, moved W4-1)
scripts/                real entry points (NOT thin shims -- see the cli.py
                         note above): gather.py, derive.py, verify.py,
                         promote.py, run_all.py, serve.py, build_universe.py,
                         backfill_downloads.py, render_methodology.py,
                         make_sample.py, plus the dev-only
                         build_fixture_snapshot.py. demo_app.py is now a
                         one-release deprecation shim (W4-1) over
                         tests/helpers/demo_app.py; serve_ui_fixtures.py is
                         an explicit W4-4 stand-in scheduled for retirement
                         once the lead verifies v2 end-to-end on the real
                         API (in flight on a concurrent packet as of W4-1 --
                         not touched here).
```

Deleted (W4-1, landed): `porchlamp/ingest.py` (rate limiter moved to
`porchlamp/collect/ratelimit.py`, `apply_raw_metrics` moved to
`porchlamp/lakehouse.py`, the GHArchive stack and `ingest_and_score` were dead
code with no successor -- deleted outright; a one-release deprecation
shim held the module path briefly, then was deleted once the inventory
confirmed no in-repo caller remained), `porchlamp/benchmark.py` (a pure
re-export shim over `porchlamp/benchmark_data.py`), `porchlamp/benchmark_data.py`
(moved to `tests/fixtures/synthetic_catalog.py`).

Not deleted, still v1, per `04` §10 step 3 ("old engine keeps serving the
v1 DB until W5-2"): `porchlamp/models.py`, `porchlamp/constants.py`, `porchlamp/stage0.py`,
`porchlamp/starscout.py`, `porchlamp/normalize.py`, `porchlamp/openssf.py`, `porchlamp/pillars/*`,
`porchlamp/matrix.py`, `porchlamp/persona.py`, `porchlamp/engine.py`, `porchlamp/lakehouse.py`,
`porchlamp/api.py`, `porchlamp/deepdive.py`, `porchlamp/playbook.py`, `web/index.html` --
these are the only live v1 scoring/serving path (`scripts/promote.py`,
`scripts/serve.py` still import them) and retire together at W5-2, not
piecemeal. `porchlamp/provenance.py`/`porchlamp/paths.py` are shared utilities used by
both v1 and v2 code and are not v1-specific despite living at the `porchlamp/`
top level.

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
    persona_gates: dict[str, str]        # reason codes; unmeasured-driven gating uses insufficient_evidence:<input>
    persona_composites: dict[str, PersonaComposite]   # PersonaComposite(porchlamp, coverage, tier, rated) per persona (ruling 2026-09-13)
    tie_key: tuple; tie_group: int | None; rank: int | None
    suspicion: list[str]
```

Rules: `core/` never imports `collect/`, `derive/`, `store/`; `derive/`
imports `core.models` only; `store/` imports `core` (enums, records, the tie key) and `snapshot` (manifest, canonical hashes; amended 2026-09-13, W4-2)
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

`run_all` becomes `porchlamp run --universe ... --as-of ...` and is resumable through
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
  porchlamp REAL, coverage REAL NOT NULL, tier TEXT, tier_ceiling TEXT,
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

CREATE INDEX idx_rank ON repositories(snapshot_id, rated, porchlamp DESC, coverage DESC);
CREATE INDEX idx_eco  ON repositories(snapshot_id, ecosystem, porchlamp DESC);
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
    / NULLIF((CASE WHEN p_responsiveness IS NULL THEN 0 ELSE wr END)+ ... ,0) AS porchlamp_p
  FROM repositories r, w WHERE r.snapshot_id=:sid AND r.rated=1
    AND json_extract(r.persona_gates, '$.' || :persona) IS NULL)
SELECT s.*, RANK() OVER (ORDER BY ROUND(porchlamp_p,2) DESC NULLS LAST, coverage DESC NULLS LAST,
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
between this query and `porchlamp.core.tiebreak.tie_key` must include NULLs at more
than one tie level. The store also needs `RANK()` to yield strict ranks after
the full key, so `ROW_NUMBER()` over the same ORDER BY is used for `rank` and
a separate `DENSE_RANK() OVER (ORDER BY ROUND(porchlamp_p,2) DESC)` for `tie_group`.

Per-persona rating (ruling 2026-09-13 after the W2-7 review): `rated` under a
persona is recomputed from the persona-weighted coverage (D-MISS-3 applies
under the active weights), never copied from the balanced `rated` flag; the
query above therefore filters on `porchlamp_p IS NOT NULL AND coverage_p >= 0.60`
where `coverage_p` is computed from the pillar coverages with the persona
weights, and applies the tier ceiling from `coverage_p`.

No Python loop over all rows for any endpoint.

## 7. API v2 (additive)

- Every row gains `coverage`, `tier_ceiling`, `tie_group`, `snapshot_id`,
  `benchmark_version`, `suspicion`, `persona_gates`.
- `GET /v1/porchlamp/repos/{o}/{n}/evidence` → the `params` rows for the repo (value,
  score, measured, reason, n, window, method, source URL, fetched_at).
- `GET /v1/porchlamp/snapshot` → manifest summary (id, as_of, version, constants
  hash, root, counts of rated/gated/unrated, detector summary).
- `GET /v1/porchlamp/unrated` → gated and unrated repos with reasons.
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

1. Create `porchlamp/core/models.py` (Measured/Unmeasured) and `constants_v2.py`
   alongside the existing engine; nothing imports them yet (W2-1).
2. New collectors and derivers write bundle **v2** files under a scratch data
   dir; v1 files stay untouched until the sweep finishes (W1-*).
3. New engine in `porchlamp/core/` scores v2 bundles; old engine keeps serving the v1
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
