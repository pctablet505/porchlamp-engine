# W2-5 handoff — aggregation, coverage, ceilings, composite, coordinates, engine

Worktree: `/home/pctablet505/Projects/cvi-wt-W2-5`, branch `W2-5`, base `e29fb79`.

## Diffstat (files staged for this commit only)

```
 cvi/core/aggregate.py        | 171 ++++++++++++++++
 cvi/core/engine.py           | 251 +++++++++++++++++++++++
 cvi/core/matrix.py           | 129 ++++++++++++
 tests/core/test_aggregate.py | 241 ++++++++++++++++++++++
 tests/core/test_engine_v2.py | 462 +++++++++++++++++++++++++++++++++++++++++++
 5 files changed, 1254 insertions(+)
```

Source total 551 lines (aggregate.py 171 + matrix.py 129 + engine.py 251);
test total 703 lines (241 + 462). Size deviation vs the packet's M class, in
the same pattern as W0-3 (754 vs cap 400), W2-1 (1,258 vs M), W2-3 (867 vs L
cap 800) and W5-1 (707 vs M): subtraction deferred, all four were ACCEPTED
with the same note.

## Files

Owned and committed:
- `cvi/core/aggregate.py` (new) — D-MISS-2/3, D-AGG-2: pillar renormalisation,
  composite coverage, rated/ceiling status, composite, tier cutoffs/ceiling.
- `cvi/core/matrix.py` (new) — D-MAT-1/2: v2 leverage/friction/quadrant
  coordinates. The v1 `cvi/matrix.py` is untouched.
- `cvi/core/engine.py` (new) — `score(bundle, populations, snapshot) ->
  ScoreRecord`, D-ORDER-1 stages 0, 2, 3, 5, 6, 7.
- `tests/core/test_aggregate.py` (new) — 35 tests, every pillar/coverage/
  ceiling/composite/tier acceptance oracle.
- `tests/core/test_engine_v2.py` (new) — 8 tests: the end-to-end oracle, the
  D-GATE never-calls-parameter-functions guard (+ its anti-vacuity sibling),
  the D-EVID-1 unrated case, and four direct `cvi.core.matrix` oracles
  (leverage/friction drop an unmeasured pillar with renormalised weights,
  all-unmeasured gives `None` axes, the 50.0 quadrant boundary).

Provided by W2-4, extracted from the packet's untracked tar, **not staged,
not part of this commit** (present in the worktree only so the packet's
code runs and its own tests pass):
- `cvi/core/normalize.py`
- `cvi/snapshot/populations.py`
- `tests/core/test_normalize_v2.py`
- `tests/snapshot/test_populations.py`

Not modified: `cvi/core/models.py`, `cvi/core/constants_v2.py`,
`cvi/core/gates.py`, `cvi/core/params/**` (W2-1/W2-2/W2-3, read-only
dependencies), `cvi/matrix.py`, `cvi/engine.py` (v1, untouched).

`ScoreRecord` already carried every field this packet needed (W2-1's
`cvi/core/models.py`); no additive fields were required.

## Design decisions worth flagging to the lead

1. **`Snapshot` is new, owned by `cvi/core/engine.py`.** 04 section 3 names
   no such type; the packet's `score(bundle, populations, snapshot)`
   signature needs a place for `as_of` (D-VER-3: no clock in `cvi.core`) and
   the stage-0 `GateEvidence` (external to the bundle: W1-9 tree markers,
   W1-5 vitality commits). `GateEvidence` is inherently per-repo, so the
   caller builds one `Snapshot` per (repo, snapshot) call, sharing
   `snapshot_id`/`as_of`. Defined in `engine.py`, not `models.py` (not in
   this packet's owned files).
2. **`PopulationLookup` is a local structural `Protocol`, not an import of
   `cvi.snapshot.populations.PopulationSet`.** `tests/core/
   test_import_graph.py` (W2-1, landed, not owned by this packet) asserts
   every file directly in `cvi/core/` imports nothing outside `cvi.core` +
   stdlib, via a plain AST scan — a `TYPE_CHECKING`-guarded import is caught
   by the scan just the same as a real one, so it cannot be used to route
   around this. `PopulationLookup` declares only the `.ecdf(ecosystem,
   param)` method `engine.py` calls; the real `PopulationSet` satisfies it
   structurally, and every caller (including both new test files) imports
   the concrete class directly since tests are outside the scanned set.
3. **Integration finding, W2-3 x W2-4, first wired here: dependents/downloads
   populations must be built in log space.** The three D-NORM-3 curves do
   not all query their `ecdf` the same way: `ttm_p90` queries
   `ecdf(p90_hours)` (raw), but `dependents`/`downloads` query
   `ecdf(log10(1 + raw_value))` (`cvi/core/params/criticality.py`).
   `cvi.snapshot.populations.PopulationMember.measured` is documented as
   "the raw measured value"; for the frozen ECDF to rank the same quantity
   the curve queries with, whoever builds the dependents/downloads
   *populations* must insert `log10(1 + raw)`, not the raw count — the
   `Population`/`PopulationSet` machinery itself is agnostic and will
   silently produce a miscalibrated (though still monotone, so the existing
   D-PARAM-MONO property test does not catch it) percentile if fed raw
   counts. Recorded in `engine.py`'s module docstring; both the engine
   oracle test and the Measure script below build these two populations in
   log space. This is a real cross-packet gap, not a hypothetical: it does
   not surface as a crash, only as a silently wrong ranking, so it is easy
   to miss. Flagging for whoever writes the real population-building driver
   and for `cvi/snapshot/populations.py`'s own docstring.
4. **Bug found and fixed during test construction: eager `ecdf` fetch on an
   `Unmeasured` ECDF-kind input.** `_score_params`'s first draft called
   `populations.ecdf(ecosystem, input_id)` unconditionally for the three
   ECDF-kind ids, before checking whether the bundle's value was even
   `Measured`. Since Python evaluates call arguments eagerly, this fetched
   (and could raise `LookupError` on) a population the curve was never
   going to consult (`cvi.core.params.curves._apply` short-circuits on
   `Unmeasured` before touching `ecdf`). Fixed: the population is now
   fetched only when `isinstance(value, Measured)`; a placeholder callable
   (never invoked) satisfies the three ECDF wrapper functions' required
   `ecdf` argument otherwise. `test_unrated_repo_has_no_composite_or_tier_
   but_keeps_params_and_coverage` is the regression guard (an empty
   `PopulationSet`, so the bug's old behaviour would raise `LookupError`
   immediately). This same shape recurred in the real sample (see Measure).
5. **`raw_hash = bundle.provenance_hash`, not a hash of the full serialized
   bundle.** `03-ANTI-MANIPULATION.md` line 85 says `raw_hash` is "hash of
   its bundle"; a hash of the *full* bundle document requires
   `cvi.derive.bundle.dump_bundle`, which `cvi/core/` may not import
   (04 section 3's import rule, enforced by the same AST-scan test).
   `provenance_hash` (hash of the referenced envelope hashes) is the
   closest fact already on the `FeatureBundle` object without crossing that
   boundary. Flagged for whoever wires the store layer (W4-2) if the
   stricter whole-bundle hash is actually required there.
6. **Tier/ceiling label vocabulary is bare letters** (`"S+" "S" "A" "B" "C"
   "D"`, ceiling `"B"`/`"A"`/`None`), not v1's `"Tier B"`-prefixed strings.
   D-MISS-3's own text writes "ceiling B" / "ceiling A" and the packet's
   acceptance oracle writes "score 92 with C = 0.85 -> tier A"; v1's cutoffs
   (90/80/70/55/40) and quadrant names ("Sweet Spot" etc.) are reused
   verbatim (D-MAT-1/2 say "kept"), but the `Tier`-prefix presentation
   string is a v1 UI convention I did not see restated for v2, so I did not
   carry it over.
7. **Quadrant rounds leverage/friction before the 50.0 comparison; coverage/
   tier-ceiling comparisons use unrounded values.** 04 section 8 says
   "comparisons for gates and ceilings use unrounded values"; D-MAT-1 says
   the quadrant's 50.0 threshold keeps "documented convention" — which is
   v1's own docstring, reproduced in `cvi/matrix.py`, stating explicitly
   that rounding happens *before* the quadrant comparison. Treated as a
   deliberate, named exception (quadrant is neither a gate nor a ceiling)
   rather than a conflict; both choices are commented in `cvi/core/
   matrix.py` and `cvi/core/aggregate.py`.

## Fail-before proof

`cvi/core/aggregate.py`, `cvi/core/matrix.py` and `cvi/core/engine.py` did
not exist before this packet, so the fail-before is the absent-module proof
(R-1): the three files were moved out of `cvi/core/` (with `__pycache__`
cleared) and the packet's test files run against their absence.

```
$ mv cvi/core/aggregate.py cvi/core/matrix.py cvi/core/engine.py <elsewhere>
$ find . -name __pycache__ -exec rm -rf {} +
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../python -m pytest tests/core/test_aggregate.py tests/core/test_engine_v2.py -p no:cacheprovider -q
==================================== ERRORS ====================================
________________ ERROR collecting tests/core/test_aggregate.py _________________
...
E   ModuleNotFoundError: No module named 'cvi.core.aggregate'
________________ ERROR collecting tests/core/test_engine_v2.py _________________
...
E   ModuleNotFoundError: No module named 'cvi.core.engine'
=========================== short test summary info ============================
ERROR tests/core/test_aggregate.py
ERROR tests/core/test_engine_v2.py
!!!!!!!!!!!!!!!!!!! Interrupted: 2 errors during collection !!!!!!!!!!!!!!!!!!!!
PYTEST_EXIT=2
```

Files restored (`git status --short` confirmed identical to before the
move); re-run below is the pass-after state. The four `cvi.core.matrix`
oracle tests (leverage/friction drop-out, all-unmeasured, the 50.0 quadrant
boundary) were added to `test_engine_v2.py` after this fail-before capture,
during a self-review against the packet's acceptance list; they fail the
identical way (the file's module-level `from cvi.core import ... matrix`
cannot collect at all without the module), so the capture above covers them
too -- confirmed by re-diffing: every test in the file, old and new, is
inside the same import scope that the recorded `ModuleNotFoundError`
already demonstrates fails first.

## Packet test set (pass-after)

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../python -m pytest tests/core/test_aggregate.py tests/core/test_engine_v2.py -p no:cacheprovider -v
collected 43 items

tests/core/test_aggregate.py ...................................         [ 81%]
tests/core/test_engine_v2.py ........                                    [100%]

============================== 43 passed in 0.04s ==============================
EXIT=0
```

(43 = 35 in `test_aggregate.py` + 8 in `test_engine_v2.py`, the latter
including the four `cvi.core.matrix` oracles added after an initial review
of my own coverage found the packet's "unmeasured pillar drops out of
leverage and friction" and "boundary 50.0" oracles were not yet exercised
directly — the end-to-end fixture alone never takes the unmeasured-pillar
branch, since every one of its 18 params is measured.)

## Over-correction guards (files this packet does not own, must stay green)

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../python -m pytest tests/core/test_aggregate.py tests/core/test_engine_v2.py tests/core/test_import_graph.py tests/test_no_clock.py -p no:cacheprovider -v
collected 55 items

tests/core/test_aggregate.py ...................................         [ 63%]
tests/core/test_engine_v2.py ........                                    [ 78%]
tests/core/test_import_graph.py ...                                      [ 83%]
tests/test_no_clock.py .........                                         [100%]

============================== 55 passed in 0.10s ==============================
EXIT=0
```

`test_import_graph.py` matters specifically here: it is the test that
forced decision #2 above (the `PopulationLookup` protocol instead of an
import of `cvi.snapshot.populations`).

## Ruff

Scoped to this packet's five files:

```
$ .../ruff check cvi/core/aggregate.py cvi/core/matrix.py cvi/core/engine.py tests/core/test_aggregate.py tests/core/test_engine_v2.py
All checks passed!
EXIT=0
```

Full sweep (`cvi/ scripts/ tests/`, the packet's Lint line): exit 1, 23
errors, all in three files that exist on `main` untouched by this packet
(`tests/test_adversarial_comparator_m3.py`, `tests/test_perf_sla_stress.py`,
`tests/test_webapp.py` — confirmed via `git show e29fb79:<path>`), all
pre-existing (F541 f-strings without placeholders, unused imports, one
RUF002 en-dash, one RUF046). Not in this packet's owned files; not touched
(R-10).

## Full suite

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../python -m pytest tests/ -n 4 -p no:cacheprovider
1469 passed, 12 warnings in 26.94s
EXIT=0
```

(First attempt doubled `-q` — the pyproject `addopts` already carries `-q`,
and adding a second `-q` on the command line suppressed pytest's own final
summary line entirely while still exiting 0; re-run with a single `-q`
worth of quiet shows the real count. Noting this so the reproducer does not
also get an apparently-summary-less green run and wonder if something is
wrong.) 12 warnings are pre-existing deprecation notices from
`starlette`/`fastapi` test fixtures, unrelated to this packet.

## Measure

Ran `python /home/pctablet505/Projects/cvi-scratch/W2-5/measure.py`
(PYTHONPATH = the worktree) over a copy of `sample-200` at
`/home/pctablet505/Projects/cvi-scratch/W2-5/sample-200`, loading through
the W2-1 `cvi.derive.bundle.load_bundle` loader and building populations
through W2-4's `cvi.snapshot.populations.build_populations`. Full verbatim
output:

```
MEASURE: bundles found=199 loaded=184 failed_to_load=15
  FAILED 1995parham__github-do-not-ban-us: v1 pr_events[2]: event.author_login must be a non-empty string
  FAILED aFarkas__html5shiv: v1 pr_events[26]: event.author_login must be a non-empty string
  FAILED airbnb__epoxy: v1 pr_events[28]: event.author_login must be a non-empty string
  FAILED aleju__imgaug: v1 pr_events[1]: event.author_login must be a non-empty string
  FAILED apple__darwin-xnu: v1 pr_events[0]: event.author_login must be a non-empty string
  FAILED be5invis__Sarasa-Gothic: v1 pr_events[22]: event.author_login must be a non-empty string; v1 pr_events[24]: event.author_login must be a non-empty string
  FAILED dtolnay__ryu: v1 pr_events[18]: event.author_login must be a non-empty string
  FAILED evil-huawei__evil-huawei: v1 pr_events[1]: event.author_login must be a non-empty string; v1 pr_events[2]: event.author_login must be a non-empty string
  FAILED getify__Functional-Light-JS: v1 pr_events[9]: event.author_login must be a non-empty string
  FAILED microsoft__JARVIS: v1 pr_events[2]: event.author_login must be a non-empty string
  FAILED microsoft__Windows-universal-samples: v1 pr_events[1]: event.author_login must be a non-empty string; v1 pr_events[2]: event.author_login must be a non-empty string
  FAILED screetsec__TheFatRat: v1 pr_events[8]: event.author_login must be a non-empty string; v1 pr_events[25]: event.author_login must be a non-empty string
  FAILED tbruyelle__RxPermissions: v1 pr_events[20]: event.author_login must be a non-empty string
  FAILED ufrisk__pcileech: v1 pr_events[14]: event.author_login must be a non-empty string
  FAILED zhihu__Matisse: v1 pr_events[20]: event.author_login must be a non-empty string; v1 pr_events[27]: event.author_login must be a non-empty string
MEASURE: stage-0 gate outcomes: {'archived': 7, 'black_hole': 54, 'eligible': 72, 'non_software': 16, 'stale': 34, 'unlicensed': 1}
MEASURE: populations built = 0 (ecosystem, param) pairs
MEASURE: could not score (no population for a measured ECDF input) = 20
  [20 repos, all downloads_monthly against crates/npm — see full log]
MEASURE: rated=0 unrated_or_gated=164
MEASURE: unrated/gated reasons: {'archived': 7, 'black_hole': 54, 'non_software': 16, 'stale': 34, 'unlicensed': 1, 'unrated: insufficient_evidence': 52}
MEASURE: coverage quantiles (all 164 loaded bundles): q05=0.0000 q25=0.0000 q50=0.0000 q75=0.0000 q95=0.0700
MEASURE: tier distribution BEFORE ceiling: {}
MEASURE: tier distribution AFTER ceiling: {}
MEASURE: quadrant counts: {}
```

**184 of 199 derived bundles load** through the W2-1 loader (15 fail — same
root cause the packet names for the previous W2-4 agent's finding: a
`PRWindowEvent` with an empty-string `author_login` in the v1 `pr_events`
data; the exact count, 15, matches).

**Rated = 0 of 164 scorable bundles, and this is a structural fact about
the sample, not a defect in this packet's code.** `cvi/derive/bundle.py`'s
`_load_v1` shim (`V1_FIELD_TO_INPUT`) maps only 5 of the 18 v2 parameter
inputs from v1-format data (`elephant_factor`, `legal_ip`,
`promotion_ladder`, `ai_posture`, `downloads_monthly`); the other 13 are
unconditionally `Unmeasured(reason="collector_failure")` for every v1-loaded
bundle (`NO_V2_COLLECTOR` names the specific v2 collectors, mostly W1
packets, that a real derive run would need). Hand-computed ceiling: with
only those 5 measured, governance reaches full coverage (1.0, all three of
its parameters are in the 5), rigor reaches 0.30 (only `ai_posture` of its
three), criticality reaches 0.25 (only `downloads_monthly` of its three),
responsiveness and accessibility reach 0.0 (none of their parameters are in
the 5): `C_max = .25*0 + .25*0.25 + .20*1.0 + .15*0.30 + .15*0 = 0.3075`,
below the 0.60 D-EVID-1 threshold *for every possible v1-loaded bundle,
regardless of the repo*. This is why `populations built = 0`: no member is
ever both gate-eligible and rated, so `build_populations` (W2-4) admits
nothing. `tests/core/test_engine_v2.py::test_engine_end_to_end_every_field`
proves the scoring path itself reaches a real tier (S+) and quadrant (Sweet
Spot) on a fully-measured bundle — the sample's zero-rated result is a
property of *this specific sample's provenance* (frozen at W0-1, before
most of W1 landed), not of stages 3/5/6/7. A re-derive with the now-landed
W1 collectors producing native `schema_version=2` bundles is needed before
this Measure step can show a non-degenerate tier/quadrant distribution;
recorded as `COULD NOT VERIFY` below rather than fabricated.

**20 of the 72 gate-eligible bundles could not be scored at all**: their
`downloads_monthly` is `Measured`, but since population-building found zero
rated members (see above), `PopulationSet.ecdf("crates"|"npm",
"downloads_monthly")` correctly raises `LookupError` (its own documented
contract) rather than returning a fabricated ranking. This surfaced a real
bug in `_score_params`'s first draft (item 4 above), fixed before this run;
what remains is the sample's structural zero-population state, not a code
defect — logged and skipped rather than crashing the Measure run.

**Stage-0 gate cross-check against the W2-2 lead-log figure** ("v2 sample:
55 black hole / 34 stale / 14 non_software / 7 archived"): this run's
54/34/16/7 (+1 unlicensed) is close but not identical. The difference is
attributable to this script's own `GateEvidence` reconstruction (`
manifest_found`/`license_file_present` from a plain scan of the `tree`
envelope's path list; `vitality_commits_180d` from the `commits_180d`
envelope, non-bot logins only; `fork_parent_in_universe` and
`off_platform_marker` always `None`/UNDECIDED) rather than whatever more
complete W1-9-sourced evidence the W2-2 implementer's own measurement used
— `cvi/core/gates.py` itself is unchanged, imported directly from `main`.
`stale` (34) and `archived` (7) match exactly, since those come straight
from `bundle.repo.archived`/real `commits_180d` counts with no scan
involved; `black_hole` and `non_software` are the two gates whose evidence
this script approximates.

**Coverage quantiles** (q05/q25/q50/q75/q95) = 0.0000 / 0.0000 / 0.0000 /
0.0000 / 0.0700, over the 164 bundles that completed scoring. 112 of the
164 are gate failures (coverage fixed at 0.0 by design — a gated repo never
reaches measurement); the remaining 52 are the eligible-but-unrated repos,
whose coverage tops out at 0.3075 for the reason above, which is why even
q95 is only 0.07.

**Tier distribution before ceilings: `{}`. Tier distribution after
ceilings: `{}`. Quadrant counts: `{}`** — all three are empty because a
tier/quadrant is only assigned to a rated repo (D-ORDER-1 stage 6/7 follow
stage 3), and rated = 0 for the structural reason above.

## Deviations

- Size: 1,190 added lines vs the packet's M class — see Diffstat section;
  consistent with every prior packet's own recorded size deviation.
- `raw_hash = bundle.provenance_hash` rather than a full-bundle content hash
  (decision #5 above).
- Tier/ceiling labels are bare letters, not v1's `"Tier X"` strings
  (decision #6 above).
- The Measure step's `GateEvidence` is a best-effort reconstruction from raw
  envelope scans, not the full W1-9 evidence pipeline (documented inline in
  `measure.py` and above); this affects only the Measure report's exact
  gate-reason counts, not the acceptance-oracle tests (which build their
  own exact `GateEvidence` by hand).

## COULD NOT VERIFY

- **A non-degenerate tier/quadrant distribution on real data.** The
  sample-200 bundles are 100% v1-format; the loader maps only 5 of 18 v2
  inputs, capping composite coverage at 0.3075 for every bundle (proof
  above) — 0 can ever be rated. Verifying tier/quadrant distributions on
  real data needs a re-derive of the sample with the now-landed W1
  collectors (native `schema_version=2` bundles), which is outside this
  packet's scope (W1 re-derive is not owned by W2-5).
- **The W2-2 lead-log's exact black_hole=55/non_software=14 figures.** This
  packet's own `GateEvidence` reconstruction (necessary only for the
  Measure step, not for any acceptance oracle) gives 54/16; `cvi/core/
  gates.py` is unchanged and the delta is attributable to evidence-input
  approximation, not to the gate logic — see the cross-check note above.
  Reproducing the W2-2 implementer's exact evidence-building script was not
  attempted (it was not committed; only `cvi/core/gates.py` and its test
  file landed).
- **Whether `raw_hash` must be a full-bundle content hash** (03's literal
  wording) rather than `provenance_hash`, given `cvi/core/` cannot import
  `cvi.derive.bundle.dump_bundle` to compute one (decision #5). No
  acceptance oracle in this packet's list constrains the value, so this is
  a design choice pending the store-layer packet (W4-2) confirming what it
  actually needs.
