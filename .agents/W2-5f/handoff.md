# W2-5f handoff — rounding guards, raw ECDF populations, no_population, record contract

Worktree `/home/pctablet505/Projects/cvi-wt-W2-5f`, branch `W2-5f`, rebased
onto `main` at `a0dd646` (fast-forward, no conflicts). Source: the W2-5
review (`/home/pctablet505/Projects/cvi-scratch/lead-session-2026-09-12/reviews/W2-5-review.md`),
findings W-1..W-4, mutants M10/M12/M13.

## Diffstat

```
cvi/core/engine.py                    | 163 ++++++++++++++++++++--------
cvi/core/models.py                    |  46 +++++++-
cvi/core/params/criticality.py        |  37 ++++---
tests/core/test_engine_v2.py          | 195 ++++++++++++++++++++++++++++++++--
tests/core/test_params_criticality.py |  26 ++---
tests/snapshot/test_populations.py    | 102 +++++++++++++++++-
6 files changed, 480 insertions(+), 89 deletions(-)
```

`cvi/core/aggregate.py` and `cvi/core/matrix.py`: no diff (their arithmetic
was already correct pre-interruption; only untested — see mutants below).
`cvi/core/normalize.py`, `cvi/snapshot/populations.py` public API,
`cvi/core/constants_v2.py`, `cvi/core/params/responsiveness.py`,
`cvi/core/params/curves.py`, `cvi/core/gates.py`: untouched, confirmed by
`git diff --stat` against each.

## 1. Rounding guards (M10/M12/M13)

`engine.score` now calls `aggregate.composite_score(pillars)` for the stored
`composite` (was an inline `round(composite_raw, 2)` duplicate of the
function that already existed and was untested — the direct cause of M12
surviving in the review). `tier` is still derived from `composite_raw`
(unchanged, already correct). Two new tests added to
`tests/core/test_engine_v2.py`.

Baseline:
```
$ pytest tests/core/test_engine_v2.py tests/core/test_aggregate.py
49 passed in 0.08s
```

M10 (`tier_from_score(composite)` instead of `composite_raw`, planted at
`engine.py`'s tier line):
```
>       assert record.tier == "S"
E       AssertionError: assert 'S+' == 'S'
1 failed, 48 passed in 0.09s
M10_EXIT=1
```

M12 (`composite = composite_raw`, never rounded, planted at `engine.py`'s
composite line):
```
>       assert record.composite == pytest.approx(90.0)
E       assert 89.996 == 90.0 ± 9.0e-05
1 failed, 48 passed in 0.09s
M12_EXIT=1
```

M13 (`quadrant(lev_raw, fric_raw)` instead of `quadrant(lev, fric)`, planted
at `matrix.py`'s `coordinates`):
```
>       assert coords.quadrant == matrix.QUADRANT_SWEET_SPOT
E       AssertionError: assert 'Nursery' == 'Sweet Spot'
1 failed, 48 passed in 0.09s
M13_EXIT=1
```

Post-restore (each mutant restored from the in-memory good copy after its
own run, one at a time):
```
49 passed in 0.08s
POSTRESTORE_EXIT=0
```
`git diff --stat` after restoration is identical to the pre-mutation diff
(confirmed byte-for-byte via a second `git diff --stat` comparison).

## 2. Log-space transform deleted (D-NORM-3 amendment)

`cvi/core/params/criticality.py`: `_log_population_score` (queried
`ecdf(log10(1+value))`) deleted; `_DEPENDENTS`/`_DOWNLOADS` now use
`_population_score(value, ecdf) -> ecdf(value)` — the raw measured value,
matching what `cvi.snapshot.populations.build_populations` freezes. This was
not yet done on disk (only the design doc amendment had landed from the
2026-09-12 lead ruling); the `math` import and both function/curve
docstrings were removed/updated accordingly. `cvi/core/engine.py`'s module
docstring ("Integration finding") rewritten from a live discrepancy report
to a resolved-history note.

`tests/core/test_params_criticality.py`: `test_dependents_oracle` /
`test_downloads_oracle` rewritten from `log10(1+x)` semantics to raw
querying (same population, same expected percentiles at the values that
happen to equal the old log10 targets: 0, 1, 2/3, 4 -> now queried as the
raw counts 0, 1, 2/3, 4 directly).

New end-to-end test, `tests/snapshot/test_populations.py::TestEngineRawPopulationIntegration`
(additive, as scoped): `build_populations` -> `engine.score`, raw dependents
population `{10, 100, 1000, 10000}` built from `PopulationMember`s (not a
hand-built `Population` double — LENS-TQ-14).

Fail-before (criticality.py reverted to `a0dd646`, log10 transform present):
```
$ pytest tests/snapshot/test_populations.py::TestEngineRawPopulationIntegration
FAILED ...test_the_most_depended_on_package_scores_100
  assert 0.0 == 100.0  (Obtained: 0.0)
FAILED ...test_mid_rank_arithmetic_on_a_mixed_population_is_unchanged
  assert 0.0 == 62.5 ± 6.3e-05  (Obtained: 0.0)
2 failed in 0.07s
FAILBEFORE1_EXIT=1
```
Matches the task's prediction exactly: a repo whose dependents count (50000)
exceeds the whole raw population scores 0.0 under the old transform (not
100.0), because `log10(50001) ≈ 4.699` is far below every raw population
member (10..10000).

After the fix:
```
$ pytest tests/snapshot/test_populations.py::TestEngineRawPopulationIntegration
2 passed
```
Over-correction guard (same test file, second test): a value inside the
population (1000.0, tied with one member) still gets its exact mid-rank
percentile, `R(1000.0) = 100*(2+0.5)/4 = 62.5` — proves the raw-population
mid-rank arithmetic itself is undisturbed by deleting the transform.

## 3. `no_population` (W-3)

`cvi/core/engine.py`: the local `PopulationLookup` Protocol gained
`population(ecosystem, param) -> object | None` (already public on the real
`cvi.snapshot.populations.PopulationSet`, not modified). `_score_params` now
checks population existence before calling `.ecdf(...)`: a `Measured`
ECDF-kind input with no frozen population for its `(ecosystem, param)`
returns `ParamScore(score=None, measured=False, reason="no_population",
n=value.n, method=None)` directly, never reaching `.ecdf()`. An
`Unmeasured`-input ECDF field is unaffected (still routed through
`_unused_ecdf`, never consults `.population()` at all — proven by the
existing eager-fetch regression test).

`cvi/core/models.py`: `"no_population"` added to `Unmeasured.reason`'s
`Literal` (additive, per the packet's explicit scope). Not added to
`cvi.core.constants_v2.UNMEASURED_REASONS` (out of this packet's owned
files, and not needed: the engine never constructs a
`cvi.core.models.Unmeasured(reason="no_population")` — the reason is
assigned directly to a stage-2 `ParamScore`, whose `reason` field is a plain
`str | None` with no runtime enum check). Documented as a
`COULD NOT VERIFY`-adjacent scope note in the `Unmeasured` docstring itself,
naming the gap for whoever next revises `constants_v2`'s vocabulary.

Fail-before (`engine.py` reverted to `a0dd646`, eager `.ecdf()` call):
```
$ pytest tests/core/test_engine_v2.py::test_ecdf_params_missing_population_are_no_population_not_a_crash
E   LookupError: no population for ecosystem 'pypi', param 'dependents_deduped'
cvi/snapshot/populations.py:103: LookupError
1 failed in 0.09s
FAILBEFORE2_EXIT=1
```
New test asserts both cases in one bundle: `ttm_p90_hours` Unmeasured
(`third_party_gap`, passes through unchanged, population never consulted)
and `dependents_deduped`/`downloads_monthly` Measured with no population in
an empty `PopulationSet` (both come back `no_population`, with the real `n`
preserved — `n=42` on the one explicitly set to a non-default value).

## 4. Record contract (W-1)

`cvi/core/models.py`, `ScoreRecord` — six additive fields, all with
back-compat defaults so a hand-built fixture in another in-flight packet's
tests (W2-6/W2-7, unseen by this worktree) does not break:
- `rated: bool = False`
- `unrated_reason: str | None = None`
- `tier_ceiling: str | None = None`
- `leverage_coverage: float = 0.0`
- `friction_coverage: float = 0.0`
- `provenance_hash: str = ""`

`cvi/core/engine.py`: `gate` is now *always* the unmodified stage-0
`GateResult` from `evaluate_gates` — the previous `final_gate` (which
overwrote `eligible`/`reason` for the stage-3 unrated case) is deleted.
`rated`/`unrated_reason` carry the stage-3 outcome instead;
`unrated_reason` reuses `cvi.core.gates.REASON_INSUFFICIENT_EVIDENCE`
(imported, not re-literaled — one definition per fact). `tier_ceiling` is
the D-MISS-3 ceiling (`None`/`"B"`/`"A"`); `leverage_coverage`/
`friction_coverage` copy `Coordinates.leverage_coverage`/
`friction_coverage` (already present on `cvi/core/matrix.py`'s
`Coordinates` from before the interruption, just not copied into the
record). `score()` gained a `raw_hash: str | None = None` parameter
(additive — existing 3-positional-arg call sites are unaffected): when
given, it is stored as `raw_hash` verbatim (the caller's own hash — a
whole-bundle hash, once a deriver computes one, is out of this packet's
scope per the review's own follow-up split); when omitted, `raw_hash`
defaults to `bundle.provenance_hash` as before. `provenance_hash` is now
always stored as its own field regardless.

Existing test `test_unrated_repo_has_no_composite_or_tier_but_keeps_params_and_coverage`
updated: was asserting `gate.eligible is False` / `gate.reason ==
"unrated: insufficient_evidence"` (the bug); now asserts `gate.eligible is
True`, `gate.reason is None`, `rated is False`,
`unrated_reason == "unrated: insufficient_evidence"`.

New tests: `test_gated_and_unrated_records_are_distinguishable` (both
records, `gate`/`rated`/`unrated_reason` all asserted on each),
`test_score_defaults_raw_hash_to_bundle_provenance_hash_when_omitted`,
`test_score_accepts_a_caller_supplied_raw_hash_distinct_from_provenance_hash`.

## 5. Dead code

After (1), grepped every `aggregate.py` function for a caller outside its
own file/tests: `aggregate_pillars`, `composite_coverage`,
`composite_score_raw`, `composite_score`, `coverage_status`,
`tier_from_score`, `apply_ceiling` are all called from `cvi/core/engine.py`;
`_pillar_problems` is called from `aggregate_pillars` in the same file.
`composite_score` (previously dead per the review's L2-04/W-4 finding) is
now live. Nothing to delete.

## Test outputs

Packet tests:
```
$ pytest tests/core/test_engine_v2.py tests/core/test_aggregate.py tests/core/test_params_criticality.py tests/snapshot/test_populations.py
77 passed in 0.31s, exit 0
```

tests/core + tests/snapshot:
```
286 passed in 0.63s, exit 0
```

ruff, owned files:
```
$ ruff check cvi/core/engine.py cvi/core/models.py cvi/core/params/criticality.py \
    tests/core/test_engine_v2.py tests/core/test_aggregate.py tests/core/test_params_criticality.py \
    tests/snapshot/test_populations.py
All checks passed!
exit 0
```
`ruff check cvi/ scripts/ tests/` (repo-wide) reports 4 pre-existing errors,
all in `scripts/curate_dataset_llm.py` (unowned, untouched — same 4 errors
reproduce on `main` at `8760284`, confirmed).

Full suite:
```
$ pytest tests/ -n 4
1536 passed in 46.99s (also re-verified at 61.39s after the mutation/fail-before
restoration cycle), exit 0
```
`--collect-only` independently confirms 1536 tests collected, matching the
passed count (no silently-skipped/xfailed tests hiding in the total).

No-clock / import-graph guards (both re-run clean after all edits):
```
$ pytest tests/core/test_import_graph.py tests/test_no_clock.py
13 passed in 0.10s
```

## Added ScoreRecord fields

`rated: bool`, `unrated_reason: str | None`, `tier_ceiling: str | None`,
`leverage_coverage: float`, `friction_coverage: float`,
`provenance_hash: str` — all additive, all with defaults, existing field
names/order unchanged.

## Notes for W2-6 / W2-7

`engine.score`'s existing 3-positional-argument call shape
(`score(bundle, populations, snapshot)`) still works unchanged (`raw_hash`
is a trailing optional keyword). Every existing `ScoreRecord` field name and
`aggregate`/`matrix` function signature is untouched.
