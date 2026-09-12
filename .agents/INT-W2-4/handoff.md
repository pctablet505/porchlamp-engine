# INT-W2-4 Handoff — integrate completed W2-4 (mid-rank ECDF, frozen populations) onto main

- Worktree: `/home/pctablet505/Projects/cvi-wt-INT-W2-4`, branch `INT-W2-4`, base `e29fb79` (main).
- Source: the four untracked files from `/tmp/cvi-wt-W2-4` (base `6651386`), also archived at
  `/tmp/claude-1000/-home-pctablet505-Projects/2a48e84a-2b94-4a2f-8bff-adbe955a4084/scratchpad/inflight/W2-4/untracked.tar`.
  Copied in **unchanged**: SHA-256 of each of the four files matches across the live
  `/tmp/cvi-wt-W2-4` tree, the tar archive, and this worktree (verified below). No public
  API change to `cvi.core.normalize` or `cvi.snapshot.populations` (R-10; W2-5 builds
  against them in parallel).
- PYTHONPATH check: `import cvi; print(cvi.__file__)` printed
  `/home/pctablet505/Projects/cvi-wt-INT-W2-4/cvi/__init__.py` (worktree, not main).

## Diffstat

```
 cvi/core/normalize.py              |  58 +++++++++
 cvi/snapshot/populations.py        | 234 +++++++++++++++++++++++++++++++++++++
 tests/core/test_normalize_v2.py    |  72 ++++++++++++
 tests/snapshot/test_populations.py | 213 +++++++++++++++++++++++++++++++++
 4 files changed, 577 insertions(+)
```

`git status --porcelain` before staging showed exactly these four `??` entries and nothing
else — the packet touched no other file (R-10).

## Step 1 — files copied in unchanged (byte identity)

```
sha256sum /tmp/cvi-wt-W2-4/cvi/core/normalize.py <extracted-tar-copy>
c94306a4f859fde5d91754a3a31bc62772f8bbc1dd66b2e1fdaa13924676f072  (both)
sha256sum /tmp/cvi-wt-W2-4/cvi/snapshot/populations.py <extracted-tar-copy>
c6d57fdba12f90d69648dc0d639db3686aa681879f5173ffef772323437dd81d  (both)
sha256sum /tmp/cvi-wt-W2-4/tests/core/test_normalize_v2.py <extracted-tar-copy>
c8355acfda793301d5c4f4973744b89bd891b8d50ead04eab257f02309ec0ad6  (both)
sha256sum /tmp/cvi-wt-W2-4/tests/snapshot/test_populations.py <extracted-tar-copy>
5b1fa7e4307c217ac747da4830e8177ea0093939006b1724547f8862e20be229  (both)
```

Then copied verbatim from the tar extraction into this worktree (no edits).

### Packet test set (as copied)

Command:
`env -u PYTEST_ADDOPTS PYTHONPATH=$PWD /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -m pytest tests/core/test_normalize_v2.py tests/snapshot/test_populations.py -p no:cacheprovider -v`

```
collected 22 items

tests/core/test_normalize_v2.py ........                                 [ 36%]
tests/snapshot/test_populations.py ..............                        [100%]

============================== 22 passed in 0.07s ==============================
```

Exit code: 0 (read directly).

## Step 2 — acceptance oracles verified (exact values, not shapes)

Read both test files in full. Every oracle the packet's Acceptance line and my dispatch
require is present and already asserts exact numeric/boolean/set values — **no oracle was
missing or shape-only; no edits made to either test file**:

| Requirement | Test | Assertion style |
|---|---|---|
| All-tied → 50.0 | `test_all_tied_population_scores_50` | `== 50.0` for every member of `[42.0]*7` |
| Mixed-tie hand oracle | `test_mixed_ties_oracle` | `== 12.5 / 50.0 / 87.5` on `[1,2,2,3]`, formula in a comment |
| Below-min 0.0 / above-max 100.0 | `test_outside_population_range_clamps_to_0_and_100` | `== 0.0`, `== 100.0` |
| `fallback=True` at N<30, global population actually used | `test_fallback_uses_the_global_population` | `fallback is True`, `n == 69`, `values` set equals the pypi∪go union, `values["org/r1"] == 1.0` |
| Gated repos excluded | `test_gated_repos_are_excluded_from_populations` | `"evil/void" not in pop.values`, `n == 30` |
| Unrated repos excluded | `test_unrated_repos_are_excluded_from_populations` | `"low/evidence" not in pop.values`, `n == 30` |
| Deterministic ordering | `test_build_is_deterministic_under_input_ordering` | full-table equality, original vs. `random.Random(20260912).shuffle`d input |

Extra oracles present beyond the minimum (over-correction guards / boundary cases): `N==30`
exactly not flagged; gated repos excluded from the N<30 boundary count; a `routed`
(off-platform) repo *does* count (distinguishes routed from ineligible); empty measured
map → no population + `LookupError` on `.ecdf()`; duplicate key / empty `snapshot_id`
raise `ValueError`; the frozen population's ECDF uses the **effective** (fallback) values,
hand-computed (`R(3.0) = 100*(2+0.5*1)/3 = 83 1/3`); `write_populations` / store-table rows
assert exact `n`, `fallback`, `values`, and hash equality.

### Fail-before, independently reproduced (not just cited from the implementer)

I did not trust the implementer's paste alone. I wrote my own "before" seeds from the
handoff's description — v1 `<=` semantics for `normalize.py` (empty→50.0, no type
checks), and a naive `populations.py` (no gate/rated filter, no fallback, no validation)
— swapped them into **my own worktree** (never the main checkout), ran the packet tests,
then restored the real files and re-ran to confirm green. `git show` cannot supply a
pre-change version because both are new files (nothing to check out).

Swap-in command: copied the two real files aside, wrote the two naive seeds in their
place, ran:
`env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -m pytest tests/core/test_normalize_v2.py tests/snapshot/test_populations.py -p no:cacheprovider -v`

```
tests/core/test_normalize_v2.py FF.F.FFF                                 [ 36%]
tests/snapshot/test_populations.py F.FFFF.FF.F.FF                        [100%]
...
assert 100.0 == 50.0   # test_all_tied_population_scores_50 (v1 <= form)
assert 25.0 == 12.5    # test_mixed_ties_oracle
assert 20.0 == 10.0    # test_all_distinct_members_score_by_mid_rank
Failed: DID NOT RAISE ValueError   # test_empty_population_raises
Failed: DID NOT RAISE ValueError   # test_rejects_non_numeric_values
assert 75.0 == 50.0    # test_integer_values_coerce_to_float
assert False is True   # test_nine_below_min_is_flagged (naive: no fallback)
assert False is True   # test_fallback_uses_the_global_population
assert 'evil/void' not in {...}   # test_gated_repos_are_excluded_from_populations (naive: no gate filter)
assert False is True   # test_gated_repos_do_not_count_toward_the_boundary
assert 'low/evidence' not in {...}  # test_unrated_repos_are_excluded_from_populations (naive: no rated filter)
Failed: DID NOT RAISE ValueError   # test_duplicate_repo_key_raises
Failed: DID NOT RAISE ValueError   # test_snapshot_id_must_be_nonempty
assert 100.0 == ((100.0*2.5)/3.0)  # test_ecdf_of_frozen_population_uses_the_effective_population
assert 29 == 69        # test_write_populations_document
assert (29 == 69)      # test_store_table_rows

16 failed, 6 passed in 0.07s
```

Exit code: 1 (read directly). The 6 passes on the naive seed are guard cases that any
reasonable implementation satisfies (range clamp at extremes, closure/pointwise identity,
N==30 boundary, empty-measured no-population, the routed-repo inclusion, and
build-order determinism — a naive dict comprehension is order-insensitive too). This is a
different exact failure count than the implementer's own paste (17 failed / 5 passed)
because my naive seed is a hand reconstruction from the handoff's prose, not their literal
original file, but it **fails every acceptance oracle in the table above** by the same
mechanism the packet exists to fix — which is what R-1 requires.

Restored the two real files (`diff` against the pre-swap backups: no output, byte-identical
restore) and re-ran the same command: `22 passed in 0.03s`, exit 0.

**Independent v1 confirmation (the actual production module, not a seed):** the packet's
own Acceptance line names `cvi.normalize.compute_ecosystem_percentile` (v1, still live in
this repo at `cvi/normalize.py`) as the thing being fixed. Ran it directly, unmodified:

```python
from cvi.normalize import compute_ecosystem_percentile
population = [42.0]*7
for v in population:
    print(compute_ecosystem_percentile(v, population))
# -> 100.0 (x7)
```

Confirms finding F3 against the real v1 code, not a paraphrase: v1's `<=` form scores
every member of an all-tied population 100.0; D-NORM-1 mid-rank scores 50.0.

## Step 3 — full verification

### `tests/core tests/snapshot`

```
env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -m pytest tests/core tests/snapshot -p no:cacheprovider
........................................................................ [ 33%]
........................................................................ [ 67%]
.....................................................................    [100%]
213 passed in 0.31s
```
Exit code: 0.

### Import-graph test (`tests/core/test_import_graph.py`)

```
collected 3 items
tests/core/test_import_graph.py ...                                      [100%]
3 passed in 0.05s
```
Exit code: 0. `cvi/core/normalize.py` is a bare-stdlib leaf inside `cvi/core/` (covered by
`test_core_imports_nothing_outside_core_and_stdlib`, a `CORE_DIR.glob("*.py")` scan);
`cvi/snapshot/populations.py` imports only `cvi.core.*` (constants_v2, models, normalize)
plus stdlib/json/pathlib/dataclasses — consistent with the layering but outside this
test's own scope (it only globs `cvi/core/*.py` plus `cvi/derive/bundle.py`).

### Full suite

```
env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -m pytest tests/ -n 4 -p no:cacheprovider
1426 passed, 12 warnings in 24.38s
```
Exit code: 0.

Delta check against main at `e29fb79` with the four packet files absent: ran the identical
`pytest tests/ -n 4 -p no:cacheprovider` against the main checkout itself (read-only —
no file writes there beyond `__pycache__`, gitignored, consistent with 17 other such
directories already present from the other agents sharing this box) →
`1404 passed, 12 warnings in 29.68s`, exit 0. `1426 - 1404 = 22`, exactly the two new test
files' count. Failure-set identity: both runs report zero failures.

### Ruff

Packet's four files only:
```
ruff check cvi/core/normalize.py cvi/snapshot/populations.py tests/core/test_normalize_v2.py tests/snapshot/test_populations.py
All checks passed!
```
Exit code: 0. No `print()`, no `TODO` in the two source files (`grep` exit 1 = no match).

Full lint command per the environment spec (`ruff check cvi/ scripts/ tests/`) exits 1
with 23 pre-existing findings, **none in the four packet files** — confirmed by grepping
the ruff output for the packet's paths (no match) and listing every flagged file:
`scripts/curate_dataset_llm.py` (marked for deletion by W0-4 per `00-README.md`),
`tests/benchmark_sla_runner.py`, `tests/test_adversarial_comparator_m3.py`,
`tests/test_perf_sla_stress.py`, `tests/test_webapp.py`. This is pre-existing repo-wide
lint debt, not introduced by this packet, and outside R-10 ownership.

## Step 4 — Measure

Copied `/home/pctablet505/Projects/cvi-scratch/sample-200` fresh into
`/home/pctablet505/Projects/cvi-scratch/INT-W2-4/` (confirmed byte-identical
`manifest.json` to `sample-200` before any script ran). Adapted the previous agent's
`measure_populations.py` (copied from `/home/pctablet505/Projects/cvi-scratch/W2-4/`) by
retargeting `WORKTREE` to `/home/pctablet505/Projects/cvi-wt-INT-W2-4` and
`CVI_DATA_DIR` to my scratch copy; added a small tail block (own-measured /
third_party_gap per ecosystem for `downloads_monthly`) that the previous agent's saved
script no longer reproduces but whose numbers its own `measure-output.txt` and handoff
prose already state — same `members` data, no new computation path.

Command:
`CVI_DATA_DIR=/home/pctablet505/Projects/cvi-scratch/INT-W2-4 PYTHONPATH=/home/pctablet505/Projects/cvi-wt-INT-W2-4 .venv/bin/python /home/pctablet505/Projects/cvi-scratch/INT-W2-4/measure_populations.py`
Exit code: 0. Full output: `/home/pctablet505/Projects/cvi-scratch/INT-W2-4/measure-output.txt`.

```
as_of = 2026-09-12T05:26:57.089407+00:00  (manifest generated_at)
min_population = 30
ECDF params (D-NORM-3, from PARAMS registry) = ['ttm_p90_hours', 'dependents_deduped', 'downloads_monthly']

derived docs: 199
bundle load errors: 15  (deleted-account author_login gaps, same 15 as W2-2's handoff)
gate-eligible members: 67  (crates 16, go 14, maven 7, npm 10, pypi 10, systems 10)

v2 gate reasons: 67 None, 55 black_hole, 34 stale, 14 non_software, 7 archived,
6 unrated: no_external_workflow, 1 unlicensed
v2 routed: {'off_platform': 1}
v2 undecided recordings: {'D-GATE-1': 1, 'D-GATE-3': 1, 'D-GATE-5': 68, 'D-GATE-6': 1}

measured-input counts per ECDF param (gate-eligible repos):
  ttm_p90_hours (1.5): measured=0 | collector_failure=67
  dependents_deduped (2.1): measured=0 | collector_failure=67
  downloads_monthly (2.3): measured=20 | third_party_gap=47

population sizes per ecosystem/param (n = effective population, FLAGGED = population_fallback):
  crates  downloads_monthly  n=20  FLAGGED
  npm     downloads_monthly  n=20  FLAGGED
  (all other ecosystem/param pairs: no population -- no measured repo)

store table rows: 2
  ('W2-4-sample', 'crates', 'downloads_monthly', n=20, fallback=True, values_hash=da15bf760d3bced2...)
  ('W2-4-sample', 'npm',    'downloads_monthly', n=20, fallback=True, values_hash=da15bf760d3bced2...)

ecosystem-OWN measured counts for downloads_monthly (gate-eligible):
  crates own_measured=13 | third_party_gap=3
  go      own_measured=0 | third_party_gap=14
  maven   own_measured=0 | third_party_gap=7
  npm    own_measured=7 | third_party_gap=3
  pypi    own_measured=0 | third_party_gap=10
  systems own_measured=0 | third_party_gap=10
```

**Match against the previous agent's numbers:** `diff` of my `measure-output.txt` against
`/home/pctablet505/Projects/cvi-scratch/W2-4/measure-output.txt` differs in exactly the two
expected lines (the data-dir path and the frozen-file path — different scratch
directories); every reported number is identical. The frozen `populations.json` files are
**byte-identical**: both SHA-256
`f130bf14ae065e36935a54e377036a2aec16459e5a6c2cad195c6fde7e7b8248`. This holds even though
my worktree bases on `e29fb79` (current main tip) and the original packet based on
`6651386` — the intervening packets (W1-1/2/2f/3/5/8/9, W2-3, W3-3, W0-gate fixes) do not
change any input this measurement reads.

Two ECDF params (1.5 `ttm_p90_hours`, 2.1 `dependents_deduped`) have **no population in
any ecosystem** on the sample — 0 of 67 gate-eligible repos measure them (v1-mapping gaps:
1.5 needs W1-3's windowed PR/merge-latency collection wired into the v1→v2 bridge; 2.1
needs W1-2's root-org dependent dedup wired in — both collectors have landed on main but
`cvi.derive.bundle`'s v1-mapping does not yet read them for these two input ids; that
wiring is outside this packet's and the original W2-4 packet's ownership). Param 2.3
(`downloads_monthly`) populates: crates (own 13) and npm (own 7) both fall back to the
20-member global population since both are under `MIN_POPULATION` (30).

## Deviations

- **Recorded, not fixed (per dispatch):** source is 292 lines (58 `normalize.py` + 234
  `populations.py`) against the S size class (<=150). Nearly all of it is dataclass
  fields, input validation, and docstrings on a typed public API (R-8); no logic beyond
  D-NORM-1..3. Same deviation the original implementer flagged; not trimmed here per
  instruction (touching the two modules' internals is out of scope for an integration
  packet that must not change their public API).
- `MIN_POPULATION` lives in `cvi/snapshot/populations.py` rather than
  `cvi.core.constants_v2` (owned by W2-1, frozen by W2-8). Flagged by the implementer for
  relocation when W2-8 lands; not this packet's job to move it.
- Measure script: extended the previous agent's saved `measure_populations.py` with one
  small tail block (per-ecosystem own-measured / `third_party_gap` counts for
  `downloads_monthly`) to fully reproduce every line of the referenced
  `measure-output.txt`; the saved script as copied did not include it, though the numbers
  were already in that agent's handoff prose. No new computation path — same `members`
  and `bundle.inputs` data the rest of the script already builds.

## COULD NOT VERIFY

(Carried forward from the implementer's handoff; still true after integration onto
`e29fb79` — nothing landed since `6651386` changes any of these.)

- The full D-NORM-2 pipeline on real v2 data normalised through W2-3's ECDF curves with a
  rated set from an actual engine: W2-5 (aggregation/coverage/engine) and W2-6
  (tie-breaks) are not landed; the sample measure above is the furthest computable today.
- The store `populations` table schema and its migration: W4-2 owns it;
  `build_populations_table` is exercised only by its own test oracles, not against a real
  migration or a live SQLite table.
- `populations.json` consumption by a `cvi snapshot` CLI: no such CLI wiring exists yet
  (W4-1 owns the snapshot surface); the file is written and re-parsed only by the test
  oracle and by this handoff's measure script.
- Whether `ttm_p90_hours` (1.5) and `dependents_deduped` (2.1) will populate once
  `cvi.derive.bundle`'s v1-mapping is extended to read the already-landed W1-2/W1-3
  collector outputs: outside this packet's ownership (R-10); not measured here.
