# W2-4 Handoff — Mid-rank ECDF and frozen populations (D-NORM-1..3)

- Branch: `W2-4`, base `6651386` (W2-2 gates v2), commit below + handoff commit.
- Owns (R-10): `porchlamp/core/normalize.py` (new), `porchlamp/snapshot/populations.py` (new),
  `tests/core/test_normalize_v2.py` (new), `tests/snapshot/test_populations.py` (new).
  Nothing else touched. No live repos: unit oracles + frozen-sample measure only.

## Diffstat

```
 porchlamp/core/normalize.py              |  58 +++++++++
 porchlamp/snapshot/populations.py        | 234 +++++++++++++++++++++++++++++++++++++
 tests/core/test_normalize_v2.py    |  72 ++++++++++++
 tests/snapshot/test_populations.py | 213 +++++++++++++++++++++++++++++++++
 4 files changed, 577 insertions(+)
```

Size deviation: source is 292 lines (58 + 234) against the S class (<= 150).
Most of it is dataclass fields, validation and docstrings on the typed public
API (R-8); no logic beyond D-NORM-1..3. Flagged, not trimmed.

## Design (02 section 6)

- `porchlamp.core.normalize` (leaf, stdlib only — import-graph test holds):
  `mid_rank_ecdf_score(value, population)` is D-NORM-1
  `R(x) = 100 * (#{x_k < x} + 0.5 * #{x_k = x}) / N` on the 0-100 scale
  (bisect on a sorted copy: below-minimum -> 0.0, above-maximum -> 100.0,
  all-tied -> 50.0); `mid_rank_ecdf(population)` pre-sorts once and returns
  the O(log N) query closure — the callback shape W2-3's KIND_ECDF curves
  (1.5, 2.1, 2.3) take. Empty population or non-numeric input raises
  ValueError (the v1 `porchlamp.normalize` silent-50.0 empty fallback is not
  reproduced: an empty population is a data condition the engine records,
  not a score).
- `porchlamp.snapshot.populations`:
  - `PopulationMember` = one repo's stage-4 input: repo key, ecosystem,
    stage-0 `GateResult` (W2-2), stage-3 `rated` bool (D-EVID-1), and the raw
    measured values keyed by param input id.
  - `build_populations(members, snapshot_id)`: a repo enters a population iff
    `gate.eligible and rated` and the input is measured (D-ORDER-1 stage 4
    after stage 3). Per (ecosystem, param) with >= 1 measured repo the
    effective population is the ecosystem's own values when it numbers >=
    `MIN_POPULATION` (30, D-NORM-2), else the global population (all
    ecosystems) with `fallback=True` (`population_fallback`). `n` is the
    effective population size (`len(values)`). Deterministic: (ecosystem,
    param) order, repo keys sorted. Duplicate repo key / empty snapshot_id /
    invalid member raise ValueError.
  - `PopulationSet.population(ecosystem, param)` / `.ecdf(ecosystem, param)`:
    lookup + the frozen D-NORM-1 mid-rank ECDF of the effective population;
    `LookupError` when the pair has no measured repo anywhere.
  - `write_populations(popset, path)` freezes `populations.json` (house
    format: sorted keys, indent 2, trailing newline; records snapshot_id,
    benchmark_version, constants_hash in force at freeze time,
    min_population, and one entry per population with effective values +
    fallback flag).
  - `build_populations_table(popset)`: plain rows
    `(snapshot_id, ecosystem, param, n, fallback, values_json, values_hash)`
    for the store's `populations` table; canonical JSON + SHA-256 of the
    effective population. W4-2 owns the schema and the migration; this
    module never touches `porchlamp/store/`.
- D-NORM-3 is not re-stated here: the caller (W2-5 engine) passes measured
  values for the ECDF params it reads as `kind == KIND_ECDF` from the W2-3
  `PARAMS` registry (one definition per fact).
- `MIN_POPULATION: int = 30` is defined once in `porchlamp/snapshot/populations.py`
  because `porchlamp.core.constants_v2` is owned by W2-1 and frozen by W2-8.
  **Flag for W2-8 freeze: relocate `MIN_POPULATION` (D-NORM-2) into the
  frozen constants module when the constants freeze lands.** Same pattern as
  W2-2's gate thresholds in `gates.py`.
- Test file naming: `tests/core/test_normalize_v2.py` (not
  `tests/core/test_normalize.py`) — the test tree has no `__init__.py` files,
  so a same-basename file next to the v1 `tests/test_normalize.py` breaks
  pytest collection with an import-file mismatch (measured: first full-suite
  run errored on exactly this, 1375 passed + 1 error; renamed -> green).

## R-1 fail-before (verbatim; exit code 1)

Seed state: `porchlamp/core/normalize.py` = v1 `<=` semantics
(`count(x <= v)/N*100`, empty -> 50.0), `porchlamp/snapshot/populations.py` = naive
builder (no gate/rated filter, no fallback, ecosystem-only values, no
validation). Final test files unchanged.

Command: `env -u PYTEST_ADDOPTS /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -m pytest tests/core/test_normalize_v2.py tests/snapshot/test_populations.py -p no:cacheprovider`
(full output: `/tmp/porchlamp-w2-4-fail-before-final.txt`)

```
FF.F.FFFF.FFFF.FFFF.FF                                                   [100%]
=================================== FAILURES ===================================
______________________ test_all_tied_population_scores_50 ______________________

    def test_all_tied_population_scores_50():
        population = [42.0, 42.0, 42.0, 42.0, 42.0, 42.0, 42.0]
        for value in population:
>           assert normalize.mid_rank_ecdf_score(value, population) == 50.0
E           assert 100.0 == 50.0
E            +  where 100.0 = <function mid_rank_ecdf_score at 0x7144e7ae6340>(42.0, [42.0, 42.0, 42.0, 42.0, 42.0, 42.0, ...])
E            +    where <function mid_rank_ecdf_score at 0x7144e7ae6340> = normalize.mid_rank_ecdf_score

tests/core/test_normalize_v2.py:19: AssertionError
```

```
=========================== short test summary info ============================
FAILED tests/core/test_normalize_v2.py::test_all_tied_population_scores_50 - ...
FAILED tests/core/test_normalize_v2.py::test_mixed_ties_oracle - assert 25.0 ...
FAILED tests/core/test_normalize_v2.py::test_all_distinct_members_score_by_mid_rank
FAILED tests/core/test_normalize_v2.py::test_empty_population_raises - Failed...
FAILED tests/core/test_normalize_v2.py::test_rejects_non_numeric_values - Fai...
FAILED tests/core/test_normalize_v2.py::test_integer_values_coerce_to_float
FAILED tests/snapshot/test_populations.py::TestMinPopulationFallback::test_nine_below_min_is_flagged
FAILED tests/snapshot/test_populations.py::TestMinPopulationFallback::test_fallback_uses_the_global_population
FAILED tests/snapshot/test_populations.py::TestMembership::test_gated_repos_are_excluded_from_populations
FAILED tests/snapshot/test_populations.py::TestMembership::test_gated_repos_do_not_count_toward_the_boundary
FAILED tests/snapshot/test_populations.py::TestMembership::test_unrated_repos_are_excluded_from_populations
FAILED tests/snapshot/test_populations.py::TestSetIntegrity::test_duplicate_repo_key_raises
FAILED tests/snapshot/test_populations.py::TestSetIntegrity::test_snapshot_id_must_be_nonempty
FAILED tests/snapshot/test_populations.py::TestSetIntegrity::test_no_measured_values_means_no_populations
FAILED tests/snapshot/test_populations.py::TestSetIntegrity::test_ecdf_of_frozen_population_uses_the_effective_population
FAILED tests/snapshot/test_populations.py::TestFreeze::test_write_populations_document
FAILED tests/snapshot/test_populations.py::TestFreeze::test_store_table_rows
17 failed, 5 passed in 0.07s
```

Exit code: 1 (read directly, no pipe). The 5 passes are the over-correction
guards (N=30 boundary, range clamps, closure/pointwise identity,
routed-repo inclusion, determinism) — the naive seed already satisfies them.

## After (verbatim; exit code 0)

Same command, final implementation:

```
......................                                                   [100%]
22 passed in 0.03s
```

Exit code: 0. Full output: `/tmp/porchlamp-w2-4-after.txt`.

## Full canonical suite

Baseline (clean worktree at `6651386`, `/tmp/porchlamp-w2-4-base`,
`env -u PYTEST_ADDOPTS /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -m pytest tests/ -n auto -p no:cacheprovider`):

```
1360 passed, 68 warnings in 28.34s
```

After (same invocation in `/tmp/cvi-wt-W2-4`):

```
1382 passed, 68 warnings in 25.96s
```

Exit code: 0 (read directly, no pipe). 1382 = 1360 + 22 new tests
(8 in `tests/core/test_normalize_v2.py` + 14 in
`tests/snapshot/test_populations.py`). Failure-set identity: both runs have
an empty failure set. The known load-flake
`tests/e2e/test_tier1_features.py::TestAPIEndpointsFeature::test_api_matrix_spatial_query_sla`
passed in both runs; no isolation re-run was needed.

## Ruff (zero new findings)

Command: `env -u PYTEST_ADDOPTS /home/pctablet505/Projects/contributor-value-index/.venv/bin/ruff check porchlamp/core/normalize.py porchlamp/snapshot/populations.py tests/core/test_normalize_v2.py tests/snapshot/test_populations.py`

```
All checks passed!
```

Exit code: 0. (First draft had 2 RUF015 findings in the test file, fixed
with `next(...)`; re-run clean.)

## Measure (sample, measured 2026-09-12)

Sample copied to `~/Projects/cvi-scratch/W2-4/`, `PORCHLAMP_DATA_DIR` exported to it
(`/home/pctablet505/Projects/cvi-scratch/W2-4`). Reproduce:
`PORCHLAMP_DATA_DIR=... /home/pctablet505/Projects/contributor-value-index/.venv/bin/python ~/Projects/cvi-scratch/W2-4/measure_populations.py`
(worktree `/tmp/cvi-wt-W2-4` on `sys.path`). Full output:
`~/Projects/cvi-scratch/W2-4/measure-output.txt`.

Inputs: v2 bundles via `porchlamp.derive.bundle` v1-mapping over the sample's 199
derived docs; stage-0 `GateResult` via W2-2's `evaluate_gates` with
`GateEvidence` from the sample's raw `tree`/`github_meta` envelopes (W1-9
`derive_eligibility`, same construction as W2-2's `measure_gates.py`);
`as_of = 2026-09-12T05:26:57.089407+00:00` (`manifest.json.generated_at`);
ECDF param set read from the W2-3 `PARAMS` registry (`kind == KIND_ECDF`:
`ttm_p90_hours`, `dependents_deduped`, `downloads_monthly`).

Sample integrity (measured, cross-checks W2-2's handoff measure exactly):
manifest lists 200 repos; 199 derived docs; 15 repos fail the strict v2
bundle load (v1 recorded an empty `author_login` — deleted-account events,
same 15 as W2-2); 184 repos gated; gate reasons: 67 `None` (1
`off_platform` routed), 55 `black_hole`, 34 `stale`, 14 `non_software`,
7 `archived`, 6 `unrated: no_external_workflow`, 1 `unlicensed`; undecided
recordings `{'D-GATE-1': 1, 'D-GATE-3': 1, 'D-GATE-5': 68, 'D-GATE-6': 1}` —
identical to the W2-2 handoff numbers.

Rated status (stage 3, D-EVID-1) is not computable on the sample: composite
coverage is W2-5's and the v2 engine does not exist yet. Stated assumption,
not a guess: gate-eligible repos are passed `rated=True`, so the sizes below
are the measured-input sizes of the 67 gate-eligible repos
(per ecosystem: crates 16, go 14, maven 7, npm 10, pypi 10, systems 10).

Measured-input counts per ECDF param over the 67 gate-eligible repos:

```
  ttm_p90_hours (1.5): measured=0 | collector_failure=67
  dependents_deduped (2.1): measured=0 | collector_failure=67
  downloads_monthly (2.3): measured=20 | third_party_gap=47
```

Population sizes per ecosystem/param (frozen via
`build_populations` -> `write_populations` to
`~/Projects/cvi-scratch/W2-4/populations.json`; `n` = effective population,
`FLAGGED` = `population_fallback` because the ecosystem's own population < 30):

```
  ecosystem  param                   n  fallback
  crates     downloads_monthly      20  FLAGGED
  npm        downloads_monthly      20  FLAGGED
  (all other ecosystem/param pairs: no population — no measured repo)
```

- 1.5 `ttm_p90_hours`: UNDECIDED/U-DEP — no population in any ecosystem:
  0 of 67 gate-eligible repos measured it (W2-1 v1-mapping reason: "v2
  parameter; the v1 pipeline collected no merge-latency distribution";
  needs W1-3 windowed PR collection).
- 2.1 `dependents_deduped`: UNDECIDED/U-DEP — no population in any
  ecosystem: 0 of 67 (W2-1 reason: "v1 dependents_count is not deduplicated
  by root organisation (v2 2.1)"; needs W1-2 root-org dedup).
- 2.3 `downloads_monthly`: global population 20 (ecosystem-own measured
  counts: crates 13, npm 7; go 0 / maven 0 / pypi 0 / systems 0, all
  `third_party_gap` — 47 gaps total). Both crates (13) and npm (7) are below
  `MIN_POPULATION` 30, so both fall back to the global population of 20 and
  carry `population_fallback`. Store table: 2 rows
  (`crates`, `npm`; `values_hash` prefix `da15bf760d3bced2`, identical —
  both effective populations are the same global 20).

## COULD NOT VERIFY

- The full D-NORM-2 pipeline on real v2 data (rated sets from a W2-5 engine,
  ECDF-normalised scores through W2-3 curves): W2-5/W2-6 not landed; the
  sample measure above is the furthest computable.
- The store `populations` table schema: W4-2 owns it; `build_populations_table`
  was exercised only by its own oracle rows, not against a migration.
- `populations.json` consumed by the `porchlamp snapshot` CLI: no CLI wiring exists
  yet (W4-1 owns the snapshot surface); the file was written and re-parsed by
  the test oracle only.
