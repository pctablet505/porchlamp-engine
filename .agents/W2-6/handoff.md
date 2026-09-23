# W2-6 handoff — tie-break key, strict ranks and tie groups

Worktree: `/home/pctablet505/Projects/cvi-wt-W2-6`, branch `W2-6`, base `a0dd646`.

## Diffstat (new files, this commit only)

```
 porchlamp/core/tiebreak.py        | 262 +++++++++++++++++++++++++++++
 tests/core/test_tiebreak.py | 475 +++++++++++++++++++++++++++++++++++++++++++
 2 files changed, 737 insertions(+)
```

Source total 262 lines vs the packet's S class cap (150 lines of source,
`05` section 2). Deviation, same pattern as every prior W2 packet (W2-1
1,258 vs M, W2-3 867 vs L cap 800, W2-4 292 vs S, W2-5 1,190 vs M): the
module carries three concerns the packet's own acceptance list requires
together (the tie-break key, the p75 statistic it needs and nowhere else
computes, and the ownership-request documentation of why); subtraction
deferred, not attempted, per the accepted precedent for every packet above.

## Files

- `porchlamp/core/tiebreak.py` (new) — `tie_key`, `assign_ranks`,
  `ttfhr_p75_from_pr_window`, `TieBreakInput` (D-TIE-1/2, `02` section 10).
- `tests/core/test_tiebreak.py` (new) — 19 tests: the 8-row D-TIE-1 oracle,
  the SQL `ORDER BY` parity check, unmeasured-after-measured per level
  (parametrized over 5 levels), gated/unrated placement, rank/tie_group
  strictness, input-order independence, the deliberately-broken-key
  fail-before-oracle proof, `ttfhr_p75_from_pr_window` (3 cases).

Not modified: `porchlamp/core/engine.py`, `porchlamp/core/aggregate.py`,
`porchlamp/core/matrix.py`, `porchlamp/core/models.py` (packet's own forbidden list).

## Ownership requests -> W2-8 (also embedded in `porchlamp/core/tiebreak.py`'s
module docstring)

`ScoreRecord` (`04` section 3) reserves `tie_key`/`tie_group`/`rank` for
this packet to fill in, but the record itself carries none of the D-TIE-1
inputs needed to compute them for a real repo:

1. **Repo identity (`owner`/`name`).** Not on `ScoreRecord` anywhere; `04`
   section 3 puts these on `FeatureBundle.repo` only. `TieBreakInput`
   (this module's own type) carries them alongside the record; a caller
   that already has the bundle in hand (as `porchlamp.core.engine.score` does)
   reads `bundle.repo.owner`/`bundle.repo.name`. Not a blocker for this
   packet, but the real design gap is that `ScoreRecord` cannot be joined
   back to a repo without external bookkeeping — W2-8 should decide
   whether identity belongs on the record itself.
2. **TTFHR p75.** Not one of the 18 scored parameters (1.1 TTFHR is the
   *median*) and not carried anywhere. This packet *can* compute it today
   from `FeatureBundle.pr_window` (`ttfhr_p75_from_pr_window`, tested
   directly), so it is not blocked — but a caller must remember to compute
   and pass it; it does not arrive "for free" with the record or bundle.
3. **Date of the last non-bot commit — genuinely blocked.** No field
   anywhere in `FeatureBundle` or `GateEvidence` carries a *date*; W1-5's
   vitality evidence (`GateEvidence.vitality_commits_180d`) is a trailing
   180-day *count*. Every caller today must pass `None`
   (`TieBreakInput.last_non_bot_commit_at`), which this module correctly
   treats as unmeasured (sorts after every real date) — confirmed live on
   the sample below: 0 of 122 synthetic rows have this field measured.
   W2-8 (or whichever packet owns commit-collection evidence) needs to
   wire a dated field through the bundle or the record before this tie
   level can ever discriminate two real rows.
4. **SQL `NULLS FIRST`/`NULLS LAST`.** `04` section 6's `ORDER BY` snippet
   names no `NULLS FIRST`/`NULLS LAST` clause. SQLite's own default (NULLs
   first for `ASC`, last for `DESC`) does **not** match D-TIE-1's
   "unmeasured sorts after measured regardless of that level's own
   direction" for the three `ASC` tie levels (friction, TTFHR p75) —
   whoever implements the real query (W4-2) needs explicit `NULLS LAST` on
   every `ASC` tie-break column and `NULLS LAST` on every `DESC` one too,
   to match this module's behaviour exactly. The parity test in this
   packet is scoped to a fully-measured 8-row case specifically to avoid
   asserting a SQL NULL-ordering behaviour that section 6 does not itself
   specify.

## Fail-before proof

`porchlamp/core/tiebreak.py` did not exist before this packet.

```
$ mv porchlamp/core/tiebreak.py <elsewhere>
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../python -B -m pytest tests/core/test_tiebreak.py -p no:cacheprovider
==================================== ERRORS ====================================
_________________ ERROR collecting tests/core/test_tiebreak.py _________________
ImportError while importing test module '.../tests/core/test_tiebreak.py'.
tests/core/test_tiebreak.py:28: in <module>
    from porchlamp.core.tiebreak import (
E   ModuleNotFoundError: No module named 'porchlamp.core.tiebreak'
=========================== short test summary info ============================
ERROR tests/core/test_tiebreak.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.06s
$ mv <elsewhere>/tiebreak.py porchlamp/core/tiebreak.py   # restored
```

Second fail-before proof, for the oracle itself rather than the module's
mere existence (R-1: "the oracle failing against a deliberately wrong key
order" per the dispatch): `test_wrong_key_order_is_caught_by_the_oracle`
builds a key with no negation on the descending levels and no
measured-before-unmeasured encoding, sorts the same 8 rows with it, and
asserts the result differs from `_EXPECTED_ORDER` — passing, which proves
the 8-row oracle is sensitive to exactly the two defect classes `tie_key`
guards against, not vacuously true regardless of implementation.

## Packet test set (pass-after)

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../python -B -m pytest tests/core/test_tiebreak.py -p no:cacheprovider -v
collected 19 items
tests/core/test_tiebreak.py ...................                          [100%]
============================== 19 passed in 0.02s ==============================
EXIT=0
```

## Over-correction guards (files this packet does not own, must stay green)

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../python -B -m pytest tests/core/test_tiebreak.py tests/core/test_import_graph.py tests/test_no_clock.py tests/core/test_engine_v2.py tests/core/test_aggregate.py -p no:cacheprovider -v
collected 75 items
tests/core/test_tiebreak.py ...................                          [ 25%]
tests/core/test_import_graph.py ...                                      [ 29%]
tests/test_no_clock.py ..........                                        [ 42%]
tests/core/test_engine_v2.py ........                                    [ 53%]
tests/core/test_aggregate.py ...................................         [100%]
============================== 75 passed in 0.10s ==============================
EXIT=0
```

`test_import_graph.py` matters specifically: `porchlamp/core/tiebreak.py` imports
only `porchlamp.core.models` plus `math`/`dataclasses`/`datetime`/`typing` (stdlib)
— confirmed by this guard, not just by inspection.

## Ruff

```
$ .../ruff check porchlamp/core/tiebreak.py tests/core/test_tiebreak.py
All checks passed!
EXIT=0

$ .../ruff check porchlamp/ scripts/ tests/
Found 4 errors.
```

The 4 errors are pre-existing, all in `scripts/curate_dataset_llm.py`
(unused imports; that script is owned by W0-4, which deletes it entirely
per `00-README.md`'s non-negotiables) — confirmed via `git status
--porcelain` showing only my two new files as changes against `a0dd646`.
Not touched (R-10).

## Full suite

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../python -B -m pytest tests/ -n 4 -p no:cacheprovider
1547 passed, 12 warnings in 61.20s
EXIT=0
```

12 warnings are the same pre-existing `starlette`/`fastapi` test-fixture
deprecation notices every prior packet's handoff has recorded; unrelated to
this packet.

## Measure

Script: `~/Projects/cvi-scratch/W2-6/measure_tiebreak.py` (adapted from
W2-5's own `~/Projects/cvi-scratch/W2-5/measure.py`, same load/gate
reconstruction — see that script's docstring for the documented gate-
evidence caveats, reproduced unchanged here), run over
`~/Projects/cvi-scratch/W2-6/sample-200` (a fresh copy of the W0-1 sample)
with `PYTHONPATH` set to this worktree.

```
MEASURE: bundles found=199 loaded=184 failed_to_load=15
MEASURE: populations built = 0 (ecosystem, param) pairs
MEASURE (real engine): rated=0 of 164 scored (20 skipped: no population for a measured ECDF input)
MEASURE (real engine): tie-group size distribution (rated rows only): {}
MEASURE (synthetic, coverage forced to 1.0): 122 of 184 loaded bundles have at least one measured pillar
MEASURE (synthetic): composite range: max=100.00 min=0.00, 3 distinct values over 122 rows
MEASURE (synthetic): composite value counts: {0.0: 24, 44.44: 21, 100.0: 77}
MEASURE (synthetic): tie-group size distribution: {21: 1, 24: 1, 77: 1}
MEASURE (synthetic): TTFHR p75 measured for 100 of 122 rows (last_non_bot_commit_at measured for 0: no source field exists yet -- ownership request)
```

**Real engine: 0 rated, exactly as W2-5's own Measure step found and
explained** (`~/Projects/cvi-scratch/W2-5/measure.py`'s handoff, same root
cause reproduced here on the same sample and the current, unchanged
`porchlamp/derive/bundle.py`): the v1-format sample-200 bundles' loader
(`_load_v1`) maps only 5 of the 18 v2 parameter inputs, capping composite
coverage at 0.3075 for every bundle regardless of repo — below the 0.60
D-EVID-1 threshold always, so `build_populations` admits nothing and no
repo is ever rated. This is a structural fact about this specific sample's
provenance (frozen pre-W1), not a defect in this packet's tie-break code
— confirmed by checking `git log` on `porchlamp/derive/bundle.py` (last touched
before W2-9's in-flight rewrite, which per the lead log's PAUSE entry has
not landed on `main`) and by observing the number matches W2-5's own
figure (0 of ~164) exactly, on the same code path. Per this packet's own
Measure instruction, this is reported as observed rather than assumed.

**Synthetic set (real pillar values, coverage forced to 1.0, gate forced
eligible, clearly labelled).** 122 of 184 loaded bundles have at least one
measured pillar (the other 62 have none of the 5 v1-mapped inputs measured
at all, so `composite_score_raw` returns `None` — excluded, not fabricated
as a 0). The composite is heavily quantized (only 3 distinct values over
122 rows) because the 5 v1-mapped inputs are mostly enum-valued (legal_ip,
promotion_ladder, ai_posture each take a handful of discrete scores) —
this is a real property of what those 5 inputs' curves can produce, not an
artifact of the tie-break code. The resulting tie groups are large (77,
24 and 21 rows respectively, one group per distinct composite) — a
concrete, real-data illustration of exactly the scenario D-TIE-1's seven
sub-levels exist to resolve (the packet's own "Why": 118 distinct
composite values over 981 rows in the full live sample). TTFHR p75 is
measured for 100 of the 122 rows (computed for real from each bundle's own
`pr_window`); last-commit-date is measured for 0 (ownership request #3
above) — so on this sample, every tie within one of the three composite
groups is resolved by coverage (tied at the forced 1.0, so this level
never discriminates here), responsiveness/friction/leverage (real pillar
values, do discriminate), then TTFHR p75 for the 100 rows that have it,
then name for the rest. A full re-derive with the landed W1 collectors
(native schema_version=2 bundles, W2-9's scope) is needed to see a
non-degenerate *real-engine* tie-group distribution; this synthetic result
is the closest available substitute today, exactly as this packet's
dispatch anticipates.

## COULD NOT VERIFY

- **A non-degenerate tie-group distribution from the real, unmodified
  engine.** Real-engine rated=0 on this sample (see above); this is
  identical to W2-5's own finding on the same sample and code path, not a
  new defect. Needs a re-derive with W2-9's landed collectors, outside
  this packet's scope.
- **The exact tie-group distribution the full 3,762-repo live corpus would
  show.** Only the 200-repo (184-loaded) sample was available under this
  packet's scratch-data rule (R-3); the packet's own cited motivation
  ("118 distinct values / 981 rows") is a different, larger measurement
  this packet did not re-run.
- **Whether `ScoreRecord` should carry repo identity directly** (ownership
  request #1) — a W2-8 design decision, not something this packet's
  acceptance list constrains.

---

## Round 2 (rework after review)

Review: `scratchpad/reviews/W2-6-review.md` (REWORK: Blocker B1 + Warnings
W1-W4, five surviving mutants). Rebased onto `main` tip `7d2316d` first
(only docs changed since base `a0dd646`; `git rebase main` succeeded with no
conflicts). New head after this round's commit is below.

### Diffstat

```
 porchlamp/core/tiebreak.py        |  23 +++-
 tests/core/test_tiebreak.py | 329 +++++++++++++++++++++++++++++++++++++++-----
 2 files changed, 315 insertions(+), 37 deletions(-)
```

### B1 — `_commit_epoch` timezone fix

`_commit_epoch` no longer builds a naive `datetime`: an offset-less ISO
string now gets `timezone.utc` attached before `.timestamp()`, and a
non-ISO string raises `ValueError` naming the value instead of propagating
`fromisoformat`'s own message.

**Fail-before**, measured on the pre-fix code under `TZ=Asia/Kolkata`
(exact command from the review, re-run before touching the source):

```
$ TZ=Asia/Kolkata PYTHONPATH=$PWD .../python -c "
from porchlamp.core.tiebreak import _commit_epoch
print(_commit_epoch('2026-06-01'))
print(_commit_epoch('2026-06-01T00:00:00Z'))
"
1780252200.0
1780272000.0
```

A 5.5-hour shift between the two string forms of the same instant, matching
the review's numbers exactly. A two-row `assign_ranks` case built from the
same two commit-date strings then confirmed the order-flip on the pre-fix
code before the fix was reapplied:

```
TZ=Asia/Kolkata (pre-fix) -> b-row,a-row
TZ=UTC          (pre-fix) -> a-row,b-row
```

**TZ subprocess test** (`test_commit_epoch_orders_identically_under_different_host_timezones`,
real subprocesses, `TZ=Asia/Kolkata` and `TZ=UTC`, on the fixed code):

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../python -B -m pytest tests/core/test_tiebreak.py -p no:cacheprovider -k "orders_identically_under_different_host_timezones" -v
collected 27 items / 26 deselected / 1 selected
tests/core/test_tiebreak.py .                                            [100%]
======================= 1 passed, 26 deselected in 0.15s =======================
EXIT=0
```

Both timezones now give `a-row,b-row` (a tie at the fixed commit level
falling through to name order) — identical, as required.

### W1 — p75 nearest-rank pinned at N=3

`test_ttfhr_p75_nearest_rank_at_n_equals_3_pins_ceil_not_floor`: 3 external
responded events (hours 1, 2, 3); `ceil(0.75*3)=3` -> `3.0`, the
`floor`-mutant gives `2.0`.

### W2 + W3 — composite/tie-group rounding pinned at 2 decimals

`test_tie_key_composite_pinned_at_two_decimals`: a1 composite=91.24
coverage=0.50, a2 composite=91.238 coverage=0.60 — both round to 91.24 at
level 1, so coverage (level 2) decides and a2 ranks first; a 3-decimal or
unrounded key resolves level 1 alone and inverts the order.

`test_tie_group_boundary_pinned_at_two_decimals`: 88.0 and 88.004 (both
round to 88.0) share a tie_group; 88.006 (rounds to 88.01) forms its own,
different, non-`None` group; a 70.5 singleton gets `tie_group is None`. All
asserted by `tie_group` value, never by a crash.

### W4 — SQL parity extended with NULLs, negative control, renamed

`docs/v2/04-ARCHITECTURE.md` §6 (current, post-ruling-2026-09-13) specifies
`NULLS LAST` on every tie column, `ROW_NUMBER()` for `rank_p`, and a
separate `DENSE_RANK() OVER (ORDER BY ROUND(porchlamp_p,2) DESC)` for `tie_group`
— transcribed verbatim into `_CORRECTED_ORDER_BY`. The old 8-row test is
renamed `test_sql_order_by_agrees_with_tie_key_on_the_fully_measured_eight_row_case`
(it cannot exercise NULL-ordering; the name no longer implies it does). A
new `test_sql_order_by_with_nulls_needs_nulls_last_and_matches_tie_key`
builds the review's 9-row case (composite 88.0 / coverage 0.70 throughout,
NULLs at responsiveness/friction/leverage/ttfhr_p75/commit-date — four
different ASC/DESC tie levels) and asserts both directions:

- `_CORRECTED_ORDER_BY` reproduces `tie_key`'s order exactly, with
  `ROW_NUMBER()` giving a strict `1..9`.
- `_NAIVE_ORDER_BY` (no `NULLS LAST` anywhere) does **not** match — the
  negative control proving the clause is load-bearing.

### Also fixed this round (not in the original four, required by item 6)

`test_unrated_ordering_distinguishes_forward_from_reversed_name` kills M21
(the surviving `_name_key` reversal mutant, review's Consider C2): the
existing gated/unrated test's two names happen to be invariant under
reversal, so a new pair (`"ax"` vs `"xa"`) whose relative order flips under
reversal was added.

One line in `_commit_epoch`'s new docstring originally read
``:meth:`datetime.timestamp` `` and tripped `tests/test_no_clock.py`'s
`time\.time` regex as a substring match (`...da**time.time**stamp...`) —
purely a docstring false positive, no wall-clock read added. Reworded to
avoid the substring; full suite confirmed green after.

### Five surviving mutants re-run (all killed)

```
M11 (tie-group 3dp):        1 failed, 26 passed — test_tie_group_boundary_pinned_at_two_decimals
M17 (tie-key 3dp):          1 failed, 26 passed — test_tie_key_composite_pinned_at_two_decimals
M18 (tie-key no-rounding):  1 failed, 26 passed — test_tie_key_composite_pinned_at_two_decimals
M21 (unrated name-key):     1 failed, 26 passed — test_unrated_ordering_distinguishes_forward_from_reversed_name
M22 (p75 ceil->floor):      1 failed, 26 passed — test_ttfhr_p75_nearest_rank_at_n_equals_3_pins_ceil_not_floor
```

Each mutant applied via `sed`/an in-place edit to a saved-good copy, run
against `tests/core/test_tiebreak.py` alone, then the file restored and
verified byte-identical to the pre-mutant copy (`diff` exit 0) before the
next mutant.

### Packet tests

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../python -B -m pytest tests/core/test_tiebreak.py -p no:cacheprovider -v
collected 27 items
tests/core/test_tiebreak.py ...........................                  [100%]
============================== 27 passed in 0.17s ==============================
EXIT=0
```

### tests/core

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../python -B -m pytest tests/core -p no:cacheprovider
221 passed in 0.25s
EXIT=0
```

### Full suite

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../python -B -m pytest tests/ -n 4 -p no:cacheprovider
1562 passed, 12 warnings in 53.75s
EXIT=0
```

(1562 = the review's reproduced 1547, at base `a0dd646`, plus 7 tests from
`W0-3g`'s fix commit that landed on `main` since the review and arrived via
this round's rebase (confirmed: its own handoff records "1450 passed …
base 1443 + 7 new"), plus 8 new tests this round in `test_tiebreak.py`
(19 -> 27: 3 for B1, 1 for W1, 2 for W2/W3, 1 for W4's new NULL case, 1 for
M21's new pair — the renamed 8-row test is not new). 12 warnings are the
same pre-existing starlette/fastapi deprecation notices.)

### Ruff

```
$ .../ruff check porchlamp/core/tiebreak.py tests/core/test_tiebreak.py
All checks passed!
EXIT=0

$ .../ruff check porchlamp/ scripts/ tests/
Found 4 errors.
EXIT=1
```

The 4 errors are the same pre-existing `scripts/curate_dataset_llm.py`
unused imports the review already confirmed pre-existing and out of this
packet's owned files (`git diff --name-only` shows only the two owned
files changed).
