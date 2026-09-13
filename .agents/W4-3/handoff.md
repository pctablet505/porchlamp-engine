# W4-3 handoff — SQL reprojection and the read paths the API needs

Base: `1f097dc` (main tip after W2-11 landed; rebased from the dispatch
base `e798984`, which predated W2-11's `ParamScore` evidence fields).
Commit: `ddab27a` on branch `W4-3` (worktree `~/Projects/cvi-wt-W4-3`).

## What was delivered

`cvi/store/queries.py` (new, 882 lines): every read path F-013 (W4-2
review) asked for, in SQL, no Python loop over rows (`04` section 6).

### Query signatures

```python
list_ranked(conn, snapshot_id, *, persona="balanced", ecosystem=None,
            q=None, sort=None, limit=50, offset=0) -> (rows, total)

list_not_ranked(conn, snapshot_id, *, persona="balanced", ecosystem=None,
                q=None) -> (rows, counts)

snapshot_summary(conn, snapshot_id) -> dict | None

matrix_points(conn, snapshot_id, *, persona="balanced") -> dict

repo_evidence(conn, snapshot_id, owner, name) -> dict | None

reproject(conn, snapshot_id, weights, *, limit=50, offset=0) -> (rows, total)
```

### Field-naming contract (the design decision W4-4 needs)

Every row carries the STORED, balanced `06` section 4 fields under their
own names (`rank`, `tie_group`, `cvi`, `coverage`, `tier`, `rated`, plus
every `p_*`/`c_*`, `persona_gates`, `persona_composites`, `suspicion`,
`stars_audited`, `snapshot_id`, `benchmark_version`, …) — `NULL`/`False`
exactly when the balanced record has no rank (gated or unrated).

- `list_ranked`/`matrix_points` under a non-`"balanced"` `persona`
  ADDITIONALLY attach `cvi_p`, `coverage_p`, `tier_p`, `rated_p` (read
  from the stored `persona_composites` JSON, never recomputed) and, for
  `list_ranked` only, `rank_p`/`tie_group_p` (computed here — no stored
  column exists for a cross-row, order-dependent fact). The plain
  `rank`/`cvi`/`coverage`/`tier`/`rated` on such a row stay the BALANCED
  values (per the ruling: a repo can be persona-rated while
  balanced-unrated, so `list_ranked(persona="balanced")` and
  `list_ranked(persona=<preset>)` on the same row can legitimately show
  different `total`s).
- `reproject` (U-11: never a persona) returns the CUSTOM projection under
  the plain names (this IS the requested view) plus `balanced_rank`/
  `balanced_cvi`/`balanced_coverage`/`balanced_tier` for the side-by-side
  comparison `06` section 3 D asks for, and `lens: "custom"` always.

W4-4 decides what subset of this reaches the JSON API; nothing here was
narrowed to guess at that shape ahead of time.

### R-D ruling (singleton tie groups are `NULL`)

`_tie_group_sql(composite_expr)`: `DENSE_RANK() OVER (ORDER BY
ROUND(composite_expr, 2) DESC)`, `NULL`'d out when
`COUNT(*) OVER (PARTITION BY ROUND(composite_expr, 2)) = 1`. One
definition (R-2), used by `list_ranked`'s persona branch and by
`reproject` — balanced `list_ranked` reads the STORED `tie_group` column
and never calls this function.

**Numbering caveat for anyone parity-testing this against
`cvi.core.tiebreak.assign_ranks`:** `DENSE_RANK()` assigns a new number
to EVERY distinct composite value it visits (tied or not); `assign_ranks`'
own counter only advances for composite values that actually recur. The
two schemes agree on WHICH rows share a group, never on the group's
numeric label — `tests/store/test_queries.py`'s `_group_membership`
helper compares group MEMBERSHIP, not raw ids, for exactly this reason.

### Per-persona rated (W2-7 review ruling, `04` section 6)

`cvi_p`/`coverage_p`/`tier_p`/`rated_p` are read verbatim from the stored
`persona_composites` JSON (written by `cvi.core.persona.persona_composite`
at score time) — never recomputed here. The VISIBILITY filter
(`_persona_visible_where_sql`, shared by `list_ranked`, its complement in
`list_not_ranked`, `_count_persona_visible` and `matrix_points`) is
`eligible = 1 AND persona_gates[persona] IS NULL AND cvi_p IS NOT NULL
AND coverage_p >= 0.60` — the literal predicate `04` section 6's ruling
text specifies, which is why `rated_p`'s own stored boolean is exposed as
data but the FILTER re-derives the same fact from `coverage_p` directly.

### `reproject` (the one view with nothing to read)

The only query that has no precomputed JSON to consult — `weights` is
arbitrary (`_validate_weights`: exactly the 5 pillar names, sums to 1.0
within `constants_v2.WEIGHT_SUM_TOLERANCE`). Recomputes, in SQL, from the
stored `p_*`/`c_*` columns:

- composite: `Sum(w_p * p_p) / Sum(w_p)` over pillars with `c_p > 0 AND
  p_p IS NOT NULL` (D-MISS-2) — the `p_p IS NOT NULL` half is a defensive
  guard against a hypothetical row where a coverage>0 pillar's score is
  somehow NULL (never true of real writer output, `cvi.core.aggregate.
  aggregate_pillars` guarantees the two agree; guards against poisoning
  the whole sum to NULL if that invariant were ever violated).
- coverage: `Sum(w_p * c_p)` over all 5 pillars (D-MISS-3's `C`).
- rated iff coverage >= 0.60; tier via `TIER_CUTOFFS` on the UNROUNDED
  composite, then `MIN(score-tier, ceiling)` by `TIER_ORDER` position
  (D-MAT-2) — `_tier_by_score_case_sql`/`_ceiling_index_case_sql`/
  `_tier_label_case_sql`, all derived from `constants_v2` at import time
  (never a second, hand-copied cutoff table).
- rank/tie_group: D-TIE-1 with the custom composite/coverage at levels
  1-2, `_tie_group_sql`'s R-D rule for `tie_group`.

Gate status (`eligible`) never changes under a reprojection — only
eligible repos are candidates, matching D-PERS-1's separation generalised
to weights.

## Indexes (deliverable 7)

`cvi/store/migrations/0002_indexes.sql` adds
`idx_eligible ON repositories(snapshot_id, eligible)` (mirrored in
`schema.py`'s `SCHEMA_STATEMENTS` for `introspect_schema` parity). EXPLAIN
QUERY PLAN on a 10,000-row single-snapshot synthetic DB:

```
-- list_ranked, balanced (unchanged by this packet — idx_rank already covers it)
(10, 0, 46, 'SEARCH s USING INDEX sqlite_autoindex_snapshots_1 (id=?)')
(16, 0, 60, 'SEARCH r USING INDEX idx_rank (snapshot_id=? AND rated=?)')
(105, 0, 0, 'USE TEMP B-TREE FOR ORDER BY')

-- persona base filter (list_ranked persona branch / snapshot_summary's
-- per-persona counts) -- BEFORE 0002: idx_eco (snapshot_id=?) only,
-- eligible checked row-by-row. AFTER 0002:
(4, 0, 60, 'SEARCH r USING INDEX idx_eligible (snapshot_id=? AND eligible=?)')

-- reproject base filter -- same before/after story:
(3, 0, 60, 'SEARCH r USING INDEX idx_eligible (snapshot_id=? AND eligible=?)')

-- list_ranked with an ecosystem filter (unaffected, uses idx_rank):
(4, 0, 60, 'SEARCH r USING INDEX idx_rank (snapshot_id=? AND rated=?)')
(88, 0, 0, 'USE TEMP B-TREE FOR ORDER BY')
```

Measured effect of `idx_eligible` on the raw base-filter SELECT alone
(10,000-row single-snapshot DB, 20-call median): **34ms -> 24ms** (about
30% faster). The full `list_ranked(persona=...)` call (base filter +
JSON-path evaluation + windowed ORDER BY) improved from roughly 73ms to
**64.22ms p95** with the index in place (see Performance below).

**Documented, not addressed:** the persona projection's
`json_extract(persona_composites, '$.' || :persona || '.cvi')` predicates
and `reproject`'s per-pillar `CASE` expressions cannot be indexed. A JSON
path expression index in SQLite must be a FIXED expression at `CREATE
INDEX` time; `:persona` varies per call (five presets today, and this
module deliberately never special-cases any one of them), so no
expression index can match every call's path, and the measured cost is
JSON parsing PER MATCHING ROW, not the seek to find those rows — an index
narrows which rows are visited, it cannot skip evaluating a function
against the rows it does visit. The full rationale is in the migration
file's own comment.

## Performance (deliverable 7, 10,000-row synthetic snapshot, p95 over 20 calls)

```
list_ranked (balanced), limit=50 offset=0:            5.86 ms p95
list_ranked (persona="career_capital"), limit=50:     62.89 ms p95
reproject, limit=50 offset=0:                         70.60 ms p95
```

All three under the 100ms budget (`06` section 7). The persona and
reproject paths are the slower ones because their base filter cannot be
narrowed by anything more selective than `idx_eligible` within one
snapshot (`snapshot_id` equality does nothing to reduce work when EVERY
row in the table belongs to that one snapshot, which is this benchmark's
deliberately worst case — a real multi-snapshot database benefits from
`idx_eligible` more, not less, as older snapshots accumulate). Profiled
with `cProfile`: the cost is inside `sqlite3.Connection.execute` itself
(SQLite evaluating `json_extract`/the pillar `CASE` expressions per row),
not the Python-side row decoding (`row_to_dict` + `json.loads` on 10,000
rows: ~11ms total, negligible next to the ~60-70ms SQL execution time).

Reproduce: `pytest tests/store/test_queries.py -k performance -s` (numbers
print to stdout).

## Tie-break parity (deliverable 8)

`test_sql_tie_break_parity_across_balanced_and_every_persona_200_rows_with_nulls`
(`tests/store/test_queries.py`): a 200-row synthetic snapshot with —

- rows 0-2: identical pillar scores (a composite tie under ANY weight
  vector, so persona-invariant by construction);
- row 3: a distinctive profile far from every other row (a
  persona-invariant singleton);
- rows 4-5: a SECOND identical-pillar pair, one with `friction = NULL`
  and one measured — forces the sort to actually REACH the friction ASC
  level for a real tie (with ~194 near-continuous random composites, a
  bare NULL-vs-measured comparison at level 4 would otherwise never be
  exercised, since nothing above it ties);
- 194 further rows with random pillar scores and NULLs (on roughly 1-in-11
  to 1-in-19 rows each) at friction, leverage, ttfhr_p75 and the commit
  date.

For balanced and every one of the four preset personas, `list_ranked`'s
SQL order (and, for personas, `rank_p`/`tie_group_p`'s group membership)
equals `cvi.core.tiebreak.assign_ranks` run on the same evidence, exactly.
A negative control (the pre-ruling clause, `friction ASC` with its
`NULLS LAST` dropped) gives a DIFFERENT order on this same fixture,
proving the fixture is load-bearing, not vacuous.

## Reprojection parity (deliverable 6)

`test_reproject_parity_with_inline_oracle_over_300_rows_and_20_weight_vectors`:
a 300-row synthetic snapshot (written through the real writer,
`~15%` of each pillar unmeasured per row) and 20 random weight vectors.
`_reproject_oracle` is written INLINE from `02` sections 5, 7-8 (D-MISS-2
renormalisation, D-MISS-3 coverage/tier/ceiling) — it imports neither
`cvi.core.aggregate` nor `cvi.core.persona` (W2-11 was changing both
concurrently; this packet's dispatch says to stay out of them). For every
trial: `reproject`'s SQL `(owner, name, rank, tier)` sequence and
`cvi` values equal the oracle's exactly; `tie_group` is compared by group
MEMBERSHIP (see the R-D numbering caveat above).

## Mutation battery (deliverable, ≥10 required)

Baseline: `tests/store/` + `tests/core/test_import_graph.py`, 106 tests,
`__pycache__` cleared before every mutant and every restore. Each mutant
applied as a single, targeted string replacement, tests run, then the
file restored byte-identical from a pre-mutation backup (diffed against
the backup after every restore to confirm).

| # | Mutant | Result |
|---|---|---|
| M1 | Drop `NULLS LAST` from the friction ASC tie level | **KILLED** (2 failed) |
| M2 | `_tie_group_sql`: `DENSE_RANK()` unconditionally, singleton not nulled | **KILLED** (3 failed) |
| M3 | Persona rated filter: `>` instead of `>=` at the 0.60 threshold | **KILLED** (2 failed) |
| M4 | `_build_order_by`: unknown `sort` returns the default instead of raising `ValueError` | **KILLED** (1 failed) |
| M5 | `reproject`: composite divided by `1` instead of the renormalised weight sum | **KILLED** (11 failed) |
| M6 | `reproject`: a pillar with `c = 0` kept in the sum (guard reduced to `p IS NOT NULL` alone) | **KILLED** (1 failed) |
| M7 | `list_ranked` balanced branch: `offset + 1` instead of `offset` | **KILLED** (9 failed) |
| M8 | `_persona_paths`: `"coverage"` path aliased to the `.cvi` sub-path | **KILLED** (1 failed) |
| M9 | `reproject`: tier label from `score_tier_index` alone, ceiling never applied | **KILLED** (3 failed) |
| M10 | `schema.py`'s `idx_eligible` statement commented out (migration file left intact) | **KILLED** (2 failed) |

**10/10 killed, 0 survivors, 0 equivalent.**

Reproduce: `/tmp/mutate.py` (kept out of the repo — a throwaway driver
script) applied each mutant as an exact string replacement in
`cvi/store/queries.py`/`schema.py`; anyone re-running this battery should
write an equivalent one-off script rather than expect it checked in.

## Rules compliance

- No `RETURNING` anywhere in `queries.py` (grep-clean).
- Every `ORDER BY` in every SQL string carries `NULLS LAST` on every
  column except the always-non-NULL final `lower(owner || '/' || name)
  ASC` level (matching the existing `_ORDER_BY_SQL`/reader.py
  convention).
- No wall clock (`tests/test_no_clock.py`, 11 tests, green).
- Import graph: `cvi/store/queries.py` imports only `cvi.core.constants_v2`,
  `cvi.core.gates` (one constant, `REASON_INSUFFICIENT_EVIDENCE`) and
  `cvi.core.params` (the `PARAMS` pillar lookup for `repo_evidence`) plus
  stdlib — never `cvi.core.aggregate`/`cvi.core.persona` (the packet's
  "stay out of `cvi/core/**`" plus its own reasoning: this module reads
  ALREADY-aggregated store columns, it never re-derives a pillar from raw
  parameters). `tests/core/test_import_graph.py`'s store-boundary check
  was widened by ONE line (`or name.startswith("cvi.store")`) so
  `reader.py` can import the moved `ORDER_BY_SQL`/`row_to_dict` from
  `queries.py` instead of carrying a second, drifting copy — this is an
  INTRA-store import, never a new external dependency; the docstring on
  that test explains why it does not weaken `04` section 3's rule (which
  is about crossing the package boundary).
- Ruff clean over the whole tree (`ruff check cvi/ tests/ scripts/`).
- Stayed out of `cvi/core/**` (source, not tests — `tests/core/
  test_import_graph.py`'s one-line widening is the only touch there and
  is about `cvi/store`'s own boundary, not core logic), `cvi/collect/**`,
  `cvi/derive/**`, `web/**`, `cvi/serve/**`, `scripts/serve.py`.

## Suite

```
$ pytest tests/ -n 4 -p no:cacheprovider
2092 passed, 1 skipped, 12 warnings in 11.55s
EXIT: 0
```

The two `tests/audit/test_anchors_fixture.py` failures noted as
pre-existing/known-in-flight in the packet were already fixed on main by
`2041cd8` (landed between this packet's dispatch base `e798984` and the
rebase target `1f097dc`) — confirmed absent after the rebase; nothing
left to report there.

```
$ ruff check cvi/ tests/ scripts/
All checks passed!
```

## COULD NOT VERIFY / ownership notes

- `matrix_points`'s persona projection reprojects `cvi`/`coverage`/`tier`
  but NOT `leverage`/`friction`/their coverages (D-MAT-1's coordinates are
  computed from the balanced pillar renormalisation only; no `02`/`04`
  decision defines a persona-specific matrix coordinate). Documented in
  the function's own docstring; flagging here in case W4-4/06's product
  intent turns out to want one.
- `reproject`'s tier-cutoff/ceiling SQL (`_tier_by_score_case_sql`/
  `_ceiling_index_case_sql`) is built from `constants_v2.TIER_CUTOFFS`/
  `CEILING_A_MIN_COVERAGE`/`NO_CEILING_MIN_COVERAGE` at IMPORT TIME (a
  Python-side string built once when the module loads, then reused per
  call) — correct today, but if a future packet makes these tunable
  per-request or per-snapshot, this module needs to know. Not a defect
  against this packet's D-VER-2 (constants live in one frozen module;
  nothing here overrides them), just a coupling worth naming.
- `snapshot_summary`'s `reproduce_command` string
  (`"python -m cvi.snapshot reproduce <id>"`) is built from the
  invocation literally written in `cvi/snapshot/cli.py`'s own module
  docstring (`cvi/cli.py`, the unified CLI `02`/`04` describe, does not
  exist yet) — this string will need updating once that CLI lands.
