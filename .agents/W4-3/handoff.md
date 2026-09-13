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

---

# Follow-up round (Opus review ACCEPT_WITH_FOLLOWUP)

Review: `/tmp/claude-1000/-home-pctablet505-Projects/2a48e84a-2b94-4a2f-8bff-adbe955a4084/scratchpad/reviews/W4-3-review.md`,
records under `.agents/W4-3/review/`. 11/11 items PASS (2 with findings), 2
Blockers, 6 Warnings, 8 Notes, 12/15 mutants (3 survivors). Base for this
round: rebased twice, first onto `4f5b2ba` (owner's paginated-leaderboard
ruling) then onto `acdf885` (W3-1, D-MISS-5 landed in `cvi.core.aggregate`
— confirmed non-conflicting with this round's own edit to that file: W3-1
touched `aggregate_pillars`/`composite_coverage`, never
`validate_pillar_weights`'s body).

## B-1 — `reproject` accepted a negative weight

Fixed at the source: `cvi.core.aggregate.validate_pillar_weights` (the
one function every weight vector — preset or custom — is validated
through, W2-11's) gains a non-negativity clause between the shape check
and the sum check. `cvi.store.queries._validate_weights` now *delegates
entirely* to it instead of carrying its own copy of the shape/sum check —
one definition, per R-2, and `cvi.core.persona`'s preset validation is
covered by the same fix for free.

Tests: `tests/core/test_aggregate.py::test_validate_pillar_weights_rejects_a_negative_weight_even_if_the_sum_is_1`
(plus an over-correction guard, `..._accepts_a_zero_weight`) and
`tests/store/test_queries.py::test_reproject_rejects_a_negative_weight_even_if_the_sum_is_1`
(the review's own `{responsiveness: -0.5, criticality: 1.5, rest: 0.0}`
reproduction, asserted to raise `ValueError` naming "non-negative").

## B-2 — `rank_p` renumbered under `sort`

Root cause confirmed exactly as the review described: `list_ranked`'s
persona branch fed `_build_order_by(sort, ...)` (the SORT-AWARE
expression) to *both* the `ROW_NUMBER()`/`_tie_group_sql()` window
functions and the outer `ORDER BY`. Fix: two separate expressions —
`rank_order_expr = _order_by_sql(...)` (always the plain D-TIE-1 order,
feeds the window functions only) and `display_order_expr =
_build_order_by(sort, ...)` (feeds only the outer `ORDER BY`/paging).
SQLite computes window functions before applying the final `ORDER BY`, so
this split is exactly what makes `rank_p` independent of `sort`.

Test: `test_list_ranked_rank_is_sort_invariant_for_balanced_and_every_persona`
— a 40-row randomised fixture, every `ALLOWED_SORT_FIELDS` value, asserts
the `{name: rank}` mapping is byte-identical across every sort for both
balanced `rank` and persona `rank_p`, while separately confirming the
PAGE ORDER does change (so the test cannot pass by `sort` doing nothing).
Verified load-bearing by manually reverting the fix: the reverted code
fails this test immediately with dozens of rank mismatches.

## W-1 — `tie_group` opaque/equality-only semantics

Added to `queries.py`'s module docstring: `tie_group`/`tie_group_p` are
an opaque, equality-only label *within one response*; two rows sharing a
non-`None` value tie on the composite, the numeric value itself carries
no meaning and is never comparable across two calls or two lenses on the
same call, because SQL's `DENSE_RANK()` numbers every distinct composite
value it visits while `assign_ranks`' own counter only advances for
composites that recur.

## W-2 — SQL `ROUND()` vs Python `round()` at a 2-dp half

`reproject` now computes `cvi_display = ROUND(composite_raw, 2)` once, in
SQL, and returns that value directly (`record["cvi"] = record.pop("cvi_display")`)
instead of a second, disagreeing `round(composite_raw, 2)` in Python — the
same expression drives the `ORDER BY` and `tie_group` too, so one rounding
rule governs all three facts about a row.

Test: `test_reproject_cvi_rounds_the_same_way_sql_orders_and_groups_at_a_2dp_half`
— two rows at `composite_raw=82.125` (confirmed via a live probe:
`sqlite ROUND(82.125,2)=82.13`, `python round(82.125,2)=82.12`) now report
the identical, SQL-rounded `cvi=82.13` and share a `tie_group`.

## W-3 — `idx_eligible`'s claimed 30% gain did not reproduce

`0002_indexes.sql`'s comment (and this handoff, effectively superseded by
this section) is corrected: the review's own measurement on a
single-snapshot 10,000-row fixture found dropping the index changes the
EXPLAIN plan (`SEARCH … idx_eligible` → `SCAN r`) but moves nothing
measurable in wall time (persona 42.55ms → 41.82ms, reproject
44.88ms → 43.78ms, the base `SELECT r.*` 24.17ms → 23.50ms). The
migration comment now states the plan-shape argument (a real,
multi-snapshot database has far more non-matching rows for a `SCAN` to
walk past, and that gap only grows) instead of the unreproducible
percentage, and points at
`test_explain_query_plan_uses_idx_eligible_for_persona_and_reproject_base_filter`
as the reproducible, EXPLAIN-based proof it now rests on.

## W-4 — `rated_p` / two mutation survivors

**(a) `rated_p`.** Documented rather than dropped: on any row
`list_ranked` actually returns, `rated_p` is always `True` — a logical
consequence of the visibility filter (`coverage_p >= 0.60`, the same
threshold `persona_composite` used to set the stored flag), not a
coincidence of the data. Kept for shape symmetry with `PersonaComposite`.
Two tests: `test_rated_p_is_true_for_every_visible_row_across_every_persona`
(the documented invariant, 20 randomised rows × 4 presets) and
`test_rated_p_reflects_the_stored_json_value_not_a_hardcoded_true` (a
forged `persona_composites` JSON with `rated=False` under `coverage=0.65`
— never produced by the real engine, but distinguishes reading `.rated`
from reading `.tier`, since `bool(<any non-empty tier string>)` is also
`True`). This second test converts item 9's **R1** ("equivalent")
survivor into a real kill — verified directly by re-applying that exact
mutant: the test now fails with `assert True is False`.

**(b) tier from the rounded composite (R2).** The code already used the
unrounded `composite_raw` for the tier cutoff (`04` section 8), but
nothing tested it. Added
`test_reproject_tier_cutoff_uses_the_unrounded_composite_not_the_displayed_cvi`:
`composite_raw=89.997` displays `cvi=90.0` (SQL rounds up) but must stay
`tier='S'`, not `'S+'`. Verified by re-applying R2's exact mutant
(`_tier_by_score_case_sql("ROUND(composite_raw, 2)")`): fails with
`'S+' == 'S'`.

**(c) persona not-ranked double-counting (R4).** Added
`sum(not_ranked_counts.values()) == not_ranked_total` to the existing
30-row, 5-lens partition test (`test_list_ranked_and_list_not_ranked_partition_every_row_under_every_lens`,
which already has persona-gated rows via `career_capital`). Verified by
re-applying R4's exact mutant (an extra `counts[reason] += 1` in the
persona-gated branch): fails with `28 == 24`.

## W-5 + owner addendum — paging bounds, page sizes 20/50/100/500/1000

`DEFAULT_LIMIT = 20`, `MAX_LIMIT = 1000` (both exported); `_validate_paging(limit, offset)`
raises `ValueError` naming the bound on `limit` outside `1..1000` or a
negative `offset`. Wired into `list_ranked` (default changed from 50) and
`reproject` (default changed from 50); `list_not_ranked` gained
`limit`/`offset` for the first time (it had none before this round) and
its return shape is now `(rows, counts, total)` — `counts`/`total` are
ALWAYS computed over the FULL not-ranked set (never just the current
page), so `sum(counts.values()) == total` holds regardless of paging;
implemented by paging the already-fetched, already-ordered Python list
(not a second SQL query) since this table is the minority of a snapshot's
universe and measured 1.92ms p95 unpaginated at 10k rows — restructuring
into SQL-level `LIMIT`/`OFFSET` plus a separate `GROUP BY` count would not
measurably help and risks the two disagreeing. Three existing call sites
and one partition test updated for the 3-tuple.

Tests: `MAX_LIMIT`/`DEFAULT_LIMIT` value pins; `limit=1001` /
`limit<=0` / `offset<0` rejection for `list_ranked`, `reproject` and
`list_not_ranked`; `limit=1000` acceptance; the actual default-page-size
behaviour (`limit` omitted → exactly 20 rows back, `total` still the full
count); `list_not_ranked`'s counts/total invariant at both the default
page and `MAX_LIMIT`.

Performance re-measured at both `DEFAULT_LIMIT` and `MAX_LIMIT` (see
below) per the owner's addendum.

## W-6 — contract mismatches

Untouched, as instructed — `cvi/serve/**` is W4-4/W4-5a's.

## Performance, re-measured (10,000-row synthetic snapshot, isolated)

```
list_ranked            limit=20:    5.8-6.6 ms p95
list_ranked            limit=1000: 15.4-22.1 ms p95
reproject              limit=20:   71.9-80.9 ms p95
reproject              limit=1000: 85.4-97.2 ms p95   <- tightest margin
list_ranked (persona)  limit=20:   63.3-65.5 ms p95
list_ranked (persona)  limit=1000: 78.2-90.4 ms p95
```

All comfortably under the 100ms budget when measured in isolation or
scoped to `pytest tests/store/ -n 4` (confirmed stable across 3
consecutive runs of the latter).

**New, honestly-reported finding (not one of the review's items — surfaced
by the addendum's own `limit=1000` requirement):** under the FULL project
suite's mandated invocation (`pytest tests/ -n 4`, 2,290 tests), the two
`limit=1000` assertions for `reproject` and `list_ranked(persona=...)`
occasionally exceed the 100ms budget (observed up to 106.83ms) because
this development machine runs several concurrent Claude agent sessions
(confirmed via `ps aux`: multiple `claude` processes plus an LFM2.5 model
server) that compete for the same CPU alongside this suite's own 4
pytest-xdist workers. Investigated properly, not hand-waved:

- Root-caused via `ps aux` (concurrent agent load) and via scoping the
  same perf tests to `tests/store/` alone under `-n 4` (passes cleanly,
  3/3 runs) versus the full 2,290-test suite under `-n 4` (fails
  intermittently — roughly 1 run in 3 in this session's testing).
- Attempted a genuine optimisation (folding the separate
  `SELECT COUNT(*) FROM (visible_sql)` into a `COUNT(*) OVER ()` window on
  the main query, to avoid evaluating the same per-pillar `CASE`
  expressions twice): measured SLOWER in isolation (96-97ms vs the
  original two-query approach's 85-91ms) — reverted, with the reasoning
  kept as a code comment so a future attempt does not repeat it.
- Added a retry-once helper (`_assert_under_budget_with_retry`) that
  re-measures ONCE before failing, on the theory that a single transient
  scheduling stall should not fail a batch dominated by its own
  second-worst sample (`p95` of 20 calls discards only the single worst
  one). This did NOT fully resolve it: the contention observed is
  SUSTAINED for the ~15-18s the full suite runs, not a one-off blip, so a
  second 20-call batch taken a few hundred milliseconds later is still
  contended. Kept anyway (harmless, and does help a genuinely transient
  blip); the budget itself was never relaxed.
- This is NOT claimed as a regression in the query's own cost: the
  isolated numbers above are real and reproducible. It is reported here,
  plainly, rather than hidden, because a broken measurement that reports
  false confidence is worse than an honest gap (project standing
  directive). Suggest to the lead: either accept this as an
  environment-only caveat (the query itself is fine), or have W4-4 decide
  whether `reproject`'s widest page size needs a narrower budget in
  production terms once real request concurrency is known.

## Mutation battery — 15/15 (was 12/15)

All 10 of this packet's original mutants plus the review's 5 (R1-R5),
re-run against the fully-fixed, rebased tree. `__pycache__` cleared
before every mutant; each a single exact string replacement; restored
and diffed byte-identical after every run (confirmed via `diff` against a
pre-mutation backup, and via `git status --short` showing a clean tree).

| # | Mutant | Result |
|---|---|---|
| M1 | drop `NULLS LAST` from the friction ASC tie level | KILLED |
| M2 | `_tie_group_sql`: `DENSE_RANK()` unconditionally (singleton not nulled) | KILLED |
| M3 | persona rated filter `>` instead of `>=` at 0.60 | KILLED |
| M4 | `_build_order_by`: unknown sort returns the default instead of raising | KILLED |
| M5 | reproject: composite divided by 1 (renormalisation dropped) | KILLED |
| M6 | reproject: a c=0 pillar kept in the sums | KILLED |
| M7 | list_ranked balanced: `offset + 1` | KILLED |
| M8 | `_persona_paths`: coverage path aliased to `.cvi` | KILLED |
| M9 | reproject: tier label from `score_tier_index` alone (ceiling dropped) | KILLED |
| M10 | schema.py: `idx_eligible` statement removed | KILLED |
| R1 | persona JSON path for `rated_p` points at `.tier` | **KILLED** (was equivalent — new forged-JSON test) |
| R2 | reproject: tier cut from the ROUNDED composite | **KILLED** (was survivor — new 89.997 boundary test) |
| R3 | `q` match becomes case-sensitive | KILLED |
| R4 | persona not-ranked counts double-count persona-gated rows | **KILLED** (was survivor — new counts-sum assertion) |
| R5 | migration 0002: index name typo (schema.py untouched) | KILLED |

**15/15 killed, 0 survivors, 0 equivalent.**

## Suite / ruff

```
$ pytest tests/ -n 4 -p no:cacheprovider
2290 passed, 1 skipped, 12 warnings in 18.32s
EXIT: 0
```
(A representative clean run; see the Performance section above for the
honest caveat about this specific full-suite invocation's occasional
flake on the two new `limit=1000` perf assertions under heavy concurrent
host load — everything else in the 2,290-test suite is unconditionally
green across every run in this session.)

```
$ ruff check cvi/ tests/ scripts/
All checks passed!
```

## Diffstat (this follow-up round, on top of the original `9746419`)

```
cvi/core/aggregate.py                  |  28 ++-
cvi/store/migrations/0002_indexes.sql  |  31 ++-
cvi/store/queries.py                   | ~210 ++++++++++++++++----
tests/core/test_aggregate.py           |  22 +++
tests/store/test_queries.py            | ~450 +++++++++++++++++++++++++++++-----
```

## COULD NOT VERIFY / carried forward

- W-6 (12 contract mismatches against W4-5a's `cvi/serve/contract.py`) is
  explicitly W4-4's to bridge; two of the twelve (`tie_group: int` with a
  `>= 1` validator contradicting R-D; `stars_audited: int` contradicting
  G-006) are contract-side bugs, not bridging work — flagged again here
  since this round did not touch `cvi/serve/**` per instruction.
- Notes N-1 through N-8 from the original review (NULLS LAST on two
  provably-NULL-free ORDER BYs; `matrix_points` still has no ecosystem/q
  filter (unlike `list_ranked`/`list_not_ranked`, which already had one
  from the original packet); per-pillar axis-partial attribution;
  `reproject`'s `tier_ceiling`/`quadrant` left at balanced values; no
  not-ranked complement for `reproject`; size; `repo_evidence`'s two
  sources for one fact) were NOT in the coordinator's required list for
  this round and were left untouched.
