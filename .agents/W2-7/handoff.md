# W2-7 handoff — persona gates and persona composites

Worktree: `/home/pctablet505/Projects/cvi-wt-W2-7`, branch `W2-7`, base `a0dd646`.

## Diffstat (files staged for this commit only)

```
 porchlamp/core/persona.py            | 333 +++++++++++++++++++++
 tests/core/test_persona_v2.py  | 587 ++++++++++++++++++++++++++++++++++++++
 2 files changed, 920 insertions(+)
```

Source 333 lines vs the packet's S class (<= 150): a real deviation, in the
same pattern every landed W2/W0/W5 packet in this project has recorded
(W0-3 754 vs 400, W2-1 1,258 vs M, W2-3 867 vs L-cap 800, W2-5 551 vs M,
W5-1 707 vs M — all ACCEPTED with the note, subtraction deferred). Most of
the excess is the module docstring (94 lines): D-PERS-1/2's rationale, the
`ai_policy_evidence` field gap, and two documented architectural
work-arounds (the import-graph isolation forcing a transcribed persona
preset table, and `aggregate.py`'s hard-coded balanced weights forcing a
one-line duplicate reduction) — content the review protocol has asked
other packets to *add*, not cut, when it was missing. Flagging for the
lead rather than trimming rationale a reviewer would otherwise have to ask
for.

## Files

Owned and committed:
- `porchlamp/core/persona.py` (new) — `persona_gates(record, bundle_inputs) ->
  dict[persona, reason]` (D-PERS-2) and
  `persona_composite(record, persona) -> (porchlamp, coverage, tier)` (D-AGG-4).
  `porchlamp/persona.py` (v1) is untouched.
- `tests/core/test_persona_v2.py` (new) — 38 tests: preset-vector checks,
  every gate's boundary on both sides, unmeasured handling per gate, the
  D-PERS-1 fail-before/contrast pair, persona-composite arithmetic for all
  five presets, the coverage-differs-by-weight oracle, the unrated/
  all-unmeasured/unknown-persona/incomplete-pillars edge cases, and the
  balanced-persona over-correction guard against `porchlamp.core.aggregate`.

Not modified (R-10): `porchlamp/core/engine.py`, `porchlamp/core/aggregate.py`,
`porchlamp/core/models.py`, `porchlamp/core/constants_v2.py`, `porchlamp/core/gates.py`,
`porchlamp/core/params/**`, `porchlamp/persona.py`, `porchlamp/constants.py`.

## Design notes and the two structural conflicts

**`ai_policy_evidence` has no bundle field.** D-PERS-2 gates `ai_assisted`
on `ai_posture == PROSCRIBED with ai_policy_evidence non-empty`, but the
FeatureBundle v2 contract (04 section 3) has no field of that name — only
`ai_posture`'s own `Measured`/`Unmeasured` status. Every `Measured` value
already carries non-empty envelope provenance
(`porchlamp.core.models.measured_problems` requires a non-empty `source`), so
`persona.py` reads a `Measured("PROSCRIBED", ...)` as the "with evidence"
case and `Unmeasured` (any reason) as "without evidence". This is a
documented interpretation, not a guess: tested both sides
(`test_ai_assisted_gated_when_proscribed_with_evidence` /
`test_ai_assisted_not_gated_without_evidence`). **Ownership request →
architecture:** if a distinct evidence artifact (e.g. the policy-file path
or an `Assisted-by`/ban-sentence excerpt) is meant to exist separately from
measuredness, 04 section 3 needs a field for it and W1-6's collector needs
to populate it; until then this reading is the only one the data model
supports.

**One-definition-per-constant vs. the import-graph test.** The dispatch
says to reuse `porchlamp/constants.py`'s `PERSONA_PRESETS` or
`porchlamp/core/constants_v2.py`, never a second copy. Neither is directly usable:
`constants_v2.py` has no `PERSONA_PRESETS` and W2-7 does not own that file
(R-10); importing `porchlamp.constants` (v1) from `porchlamp/core/persona.py` fails
`tests/core/test_import_graph.py::test_core_imports_nothing_outside_core_and_stdlib`,
which scans every file under `porchlamp/core/*.py`. Resolution: the `"balanced"`
preset is read directly off `constants_v2.PILLAR_WEIGHTS` (already the one
frozen definition, zero duplication); the other four preset vectors are
transcribed once into `persona.py` and pinned byte-equal to
`porchlamp.constants.PERSONA_PRESETS` by
`test_non_balanced_presets_match_v1_constants_exactly` — an equality test
standing in for the cross-package import the architecture forbids, so the
two cannot silently drift. **Ownership request → W2-8** (constants freeze):
relocate all five preset vectors into `constants_v2.py` and delete both
copies.

**`aggregate.composite_score_raw`/`composite_coverage` take no weights
parameter.** They hard-code `constants_v2.PILLAR_WEIGHTS` (the balanced
weights) with no injection point, so `persona_composite` cannot call them
for a non-balanced persona. `porchlamp.core.aggregate.coverage_status`/
`tier_from_score`/`apply_ceiling` (weight-agnostic — they take a computed
float, not the weights) *are* reused verbatim; only the two weighted-sum
reductions themselves (`Sum(w_p*pillar_p)/Sum(w_p)`,
`Sum(w_p*c_p)`) are necessarily written again, generalised over an
arbitrary weight vector. `test_persona_composite_equals_aggregate_functions_when_balanced`
proves the generalisation is not a second, drifting definition: it must
agree with `porchlamp.core.aggregate`'s own functions bit-for-bit when the
persona is `"balanced"`, on both a rated and an unrated hand-built record.
**Ownership request → W2-8:** give `composite_score_raw`/`composite_coverage`
an optional `weights` parameter (default `constants_v2.PILLAR_WEIGHTS`) so
this module can call them instead of duplicating the reduction.

**Gate thresholds and reason strings have no v2 home either**, so they are
defined once in `persona.py` (3.0, 5, 0.40, 3.0, 60.0, 0.80,
`REASON_AI_HARD_BAN` etc.) following the precedent `porchlamp/core/gates.py`'s own
module docstring already sets for its not-yet-relocated thresholds.
**Ownership request → W2-8** to relocate alongside the presets.

## Fail-before

New module: no test exists before this packet, so the whole suite fails on
`ModuleNotFoundError`. Reproduced by temporarily moving `persona.py` aside:

```
$ mv porchlamp/core/persona.py /tmp/.../persona.py.bak
$ python -B -m pytest tests/core/test_persona_v2.py -p no:cacheprovider -q
==================================== ERRORS ====================================
________________ ERROR collecting tests/core/test_persona_v2.py ________________
ImportError while importing test module '.../tests/core/test_persona_v2.py'.
Traceback:
  tests/core/test_persona_v2.py:29: in <module>
    from porchlamp.core.persona import (
E   ModuleNotFoundError: No module named 'porchlamp.core.persona'
=========================== short test summary info ============================
ERROR tests/core/test_persona_v2.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.29s
Exit: 2
```
(module restored before continuing)

**D-PERS-1's own fail-before is the still-live v1 defect, not a deleted
module.** `test_v1_lakehouse_mutates_the_gated_row` exercises the real,
unmodified `porchlamp.lakehouse.OperationalStore.leaderboard` (the same scenario
`tests/test_lakehouse.py::test_leaderboard_persona_constraints` already
covers) and asserts it overwrites `is_stage0_disqualified` to `1` and zeroes
`overall_porchlamp`/`rank_tier` on the returned row for an `ai_assisted` view of a
PROSCRIBED repo — this test **passes**, because the defect is real and
untouched (`porchlamp/lakehouse.py` is out of scope for W2-7, R-10).
`test_persona_gates_and_composite_do_not_mutate_record` builds the v2
equivalent (a rated `ScoreRecord`, `composite=70.3`), calls
`persona_gates`/`persona_composite` for the same conceptual case, and
asserts `record.composite`/`coverage`/`tier`/`gate.eligible` are read back
unchanged — the direct contrast the packet asked for.

## Packet test output

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../.venv/bin/python -B -m pytest tests/core/test_persona_v2.py -p no:cacheprovider -q
......................................                                   [100%]
38 passed
```

Import-graph guard (this module must not break it):
```
$ python -B -m pytest tests/core/test_import_graph.py -p no:cacheprovider -q
...                                                                       [100%]
3 passed
```

Full `tests/core/`:
```
$ python -B -m pytest tests/core/ -p no:cacheprovider -q
........................................................................ [ 93%]
................                                                         [100%]
232 passed
```

Ruff (scoped to owned files, and the same repo-wide command the packet
names — the 4 pre-existing `scripts/curate_dataset_llm.py` findings are
untouched, unrelated code awaiting W0-4's deletion, confirmed via
`git status --short` showing no changes to that file):
```
$ .venv/bin/ruff check porchlamp/core/persona.py tests/core/test_persona_v2.py
All checks passed!
$ .venv/bin/ruff check porchlamp/ scripts/ tests/
Found 4 errors.   # all in scripts/curate_dataset_llm.py, pre-existing, not owned by W2-7
```

Full suite:
```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../.venv/bin/python -B -m pytest tests/ -n 4 -p no:cacheprovider
1566 passed, 12 warnings in 50.32s
```
(base commit `a0dd646`'s own suite, measured by moving `persona.py`/
`test_persona_v2.py` aside and rerunning: 1,528 passed. 1,528 + 38 new
tests = 1,566, exactly this packet's addition.)

## Measure

Ran the current engine over all 199 `sample-200` derived bundles
(`~/Projects/cvi-scratch/sample-200`, read-only; the frozen populations at
`~/Projects/cvi-scratch/W2-4/populations.json`), then `persona_gates` on
every record that cleared stage 0. Since W2-7 owns no gate-evidence
collector, `GateEvidence` fields the bundle itself does not carry
(`manifest_found`, `license_file_present`, `fork_parent_in_universe`,
`vitality_commits_180d`, `off_platform_marker`) are left `None`
("not collected" — D-GATE's own semantics record this as `UNDECIDED`, never
a failure), giving the widest legitimate gate-eligible set derivable
without fabricating collector data.

```
sample bundles: 199
loaded+validated: 184  (15 BundleValidationError, pre-existing v1-loader
                        mapping issues unrelated to this packet)
gate-eligible (stage-0 pass under permissive/undecided evidence): 78
rated (composite assigned): 0

ai_assisted:     gated=0  visible=78  of 78
senior_systems:  gated=35 visible=43  of 78
    elephant_factor_below_3: 35
first_timer:     gated=78 visible=0   of 78
    high_barrier_to_entry: 78
career_capital:  gated=78 visible=0   of 78
    criticality_below_threshold: 78
```

Zero rated records is expected and already tracked (lead log, W2-9
dispatched): the base commit's `porchlamp/derive/bundle.py` v1 loader maps only 5
of the 18 v2 input ids (`elephant_factor`, `legal_ip`, `promotion_ladder`,
`ai_posture`, `downloads_monthly`); the packet's own text anticipates this
("gates read inputs, not the composite, so this works even if few or no
records are 'rated'"), and "gate-eligible" above is computed from
`record.params` being non-empty (stage-0 pass), not from
`record.gate.eligible`, because `porchlamp.core.engine.score`'s stage-3 path
deliberately overwrites `gate.eligible`/`gate.reason` with the
`"unrated: insufficient_evidence"` outcome (a W2-5 lead-log ruling: "the
unrated path must keep the stage-0 GateResult so gated and unrated stay
distinguishable" — the distinguishing signal is `params != {}`, not
`gate.eligible`).

Per-persona readings, verified against the raw bundle inputs directly
(`~/Projects/cvi-scratch/scratchpad/debug3.py`-style spot check):
- `senior_systems` (35/78, real signal): `elephant_factor` is `Measured`
  for 181/184 loaded bundles; 132 of those raw values are below 3.
- `ai_assisted` (0/78): `ai_posture` is `Unmeasured` for **all** 184 loaded
  bundles in this sample — the 0% gate rate reflects a total absence of
  measured AI-posture evidence in `sample-200` today, not a verified-clean
  reading. The gate's PROSCRIBED-with-evidence path is exercised only by
  the unit tests here.
- `first_timer` (78/78) and `career_capital` (78/78): both 100% because
  `mr_first_rate`, `newcomer_issue_supply`, `dependents_deduped` and
  `openssf_criticality` are `Unmeasured("collector_failure", ...)` for
  every sample bundle at this base commit (the same pre-W2-9 mapping gap).
  These two gates cannot show a non-trivial reading on `sample-200` until
  W2-9 lands; the code path (unmeasured inputs satisfy neither branch,
  D-MISS-1) is exercised by the unit tests, not idle here.

## Ownership requests (to the lead / W2-8)

1. Relocate all five persona preset weight vectors from `porchlamp/constants.py`
   and this module's transcription into `porchlamp/core/constants_v2.py`; delete
   both copies, keeping the equality test as a migration check until then.
2. Relocate this module's gate thresholds/reason strings into
   `constants_v2.py` alongside the presets (same R-2 reasoning
   `porchlamp/core/gates.py` already flags for its own thresholds).
3. Give `porchlamp.core.aggregate.composite_score_raw`/`composite_coverage` an
   optional `weights` parameter (default `constants_v2.PILLAR_WEIGHTS`) so
   `persona_composite` can call them instead of a parallel one-line
   reduction.
4. If a distinct `ai_policy_evidence` artifact is intended (separate from
   `ai_posture`'s Measured/Unmeasured status), 04 section 3 needs a
   FeatureBundle field for it and a W1-6 collector to populate it.
5. W2-9's bundle-input-mapping fix is a hard prerequisite for a
   non-degenerate `first_timer`/`career_capital` Measure reading on real
   data (already tracked; not a new finding).
6. `persona_gates`/`persona_composites` are not yet wired onto
   `ScoreRecord` by any caller (`porchlamp.core.engine.score` still returns them
   empty, by design — W2-7 does not own `engine.py`, R-10). A future
   packet (W2-8 or a wiring packet) should call
   `persona_gates(record, bundle.inputs)` and
   `{p: persona_composite(record, p) for p in GATED_PERSONAS + (PERSONA_BALANCED,)}`
   per scored record and populate those two fields.

## COULD NOT VERIFY

- Whether a distinct `ai_policy_evidence` bundle field was intended by the
  design (vs. the Measured/Unmeasured reading used here) — no such field
  exists in the current FeatureBundle v2 contract or any landed collector;
  see ownership request 4.
- The `ai_assisted` gate's behaviour on a real PROSCRIBED-with-evidence
  repo, on live data: `sample-200` has zero measured `ai_posture` values at
  this base commit (see Measure); the gate logic itself is exercised by
  the unit tests only.
- The 15 `BundleValidationError`s during `load_bundle` on `sample-200`
  bundles were not diagnosed (out of scope, `porchlamp/derive/bundle.py` is not
  owned by W2-7); they reduce the Measure denominator from 199 to 184 but
  do not affect any acceptance oracle.
