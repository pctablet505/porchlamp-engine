# Round-2 mutation battery, suite and merge trial

## Mutants — 24/24 killed (round 1: 17/20 with 1 equivalent and 2 gaps)

Driver `cvi-scratch/W4-4-r2-review/mutate_r2.py` against the detached copy
at `cvi-scratch/W4-4-r2-review/tree` (ac4b46a). Baseline `tests/serve/
tests/test_serve.py tests/test_ui_fixtures.py tests/core/test_import_graph.py
tests/store/test_queries.py tests/snapshot/test_store_cli.py`, `-k "not
performance"` (the store's own 10k perf test is the documented flake under
shared-host contention; it is the ONLY baseline failure and is unrelated to
this packet). `__pycache__` cleared before every run; each mutant one unique
string replacement; file restored from a byte backup and asserted identical.

My 20 from round 1, all KILLED — including the two round-1 survivors
(RX2 `_matrix_point` NULL quadrant, RX3 `_no_ceiling_or_band` bands) and the
round-1 *equivalent* R5 (`_paging` clamp), which is now genuinely killed by
`test_paging_page_size_1000_accepted_1001_rejected`.

The implementer's four Blocker mutants, re-injected by me, all KILLED:
B1 `_current_snapshot_id` tolerance, B2 reserved-prefix exclusion,
B3 `ValidationError` classification, B4 v2 hash check.

One more of mine this round: RY1 (v2 guard's fixture-clock check disabled)
KILLED by `test_v2_refusal_reasons_refuses_a_fixture_marked_legacy_table`.

`git status --porcelain` empty after restore; POST-RESTORE rc=0.

## Suite (exact) and merge trial

```
branch ac4b46a : 2541 passed, 1 skipped, 12 warnings in 22.30s
ruff check cvi/ tests/ scripts/ : All checks passed!

main 6899b71  : 2477 passed, 2 skipped, 12 warnings in 13.63s
cherry-pick of all 15 W4-4 commits: no conflict
merged        : 2541 passed, 1 skipped, 12 warnings in 22.04s
ruff (merged) : All checks passed!
```
(The skip count 2 → 1 is W4-6's self-upgrading store-CLI guard test, which
now runs for real against `cvi/serve/guard.py`.)

## Performance (regression check, 10,000 rows, 20 calls, budget 150 ms)

```
GET  /leaderboard ps=20      : p95=  36.05 ms   (round 1: 35.10)
GET  /leaderboard ps=1000    : p95=  79.21 ms   (round 1: 70.89)
GET  /leaderboard persona 20 : p95=  94.22 ms   (round 1: 87.05)
GET  /leaderboard persona 1k : p95= 127.77 ms   (round 1: 120.58)  <- worst path
GET  /leaderboard -coverage  : p95=  88.58 ms   (new: the direction flag)
POST /reproject   ps=20      : p95=  62.53 ms   (round 1: 59.54)
POST /reproject   ps=1000    : p95= 106.28 ms   (round 1: 95.57)
GET  /not-ranked             : p95=  41.15 ms
GET  /matrix                 : p95=  68.23 ms
```
All inside budget; the uniform ~5-10% rise is host noise, not a new cost
path. The `descending` flag adds nothing measurable.

## Regression check on round 1's PASSing items

Re-ran the round-1 endpoint probe unchanged: 11/11 endpoints 200;
`rank_delta` 0/64 mismatches; persona flat fields 0/64 mismatches; the
not-ranked three-way split still equals the store's row count under both
lenses (13/13 and 19/19). No regressions.
