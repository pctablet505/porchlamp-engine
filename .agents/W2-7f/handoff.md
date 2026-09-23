# W2-7f handoff — follow-ups from the Opus review of W2-7

Branch `W2-7f`, base `f3cbc0c` (main tip at packet start: "docs(v2): lead
log — W2-7 accepted; D-PERS-3, PersonaComposite shape, per-persona rated
rulings"). Worktree `/home/pctablet505/Projects/cvi-wt-W2-7f`.

Source: `/tmp/claude-1000/-home-pctablet505-Projects/2a48e84a-2b94-4a2f-8bff-adbe955a4084/scratchpad/reviews/W2-7-review.md`
(Warnings W-1..W-6, mutation survivors M12/M18/M19/M21/M22), and the rulings
at `docs/v2/02-RANKING-DESIGN.md` D-PERS-3 and `docs/v2/04-ARCHITECTURE.md`
section 3 (`PersonaComposite(porchlamp, coverage, tier, rated)`) / section 6
(per-persona rated).

## Diffstat

```
 porchlamp/core/models.py            |  23 ++++-
 porchlamp/core/persona.py           | 159 +++++++++++++++++++++++--------
 tests/core/test_persona_v2.py | 213 ++++++++++++++++++++++++++++++++++--------
 3 files changed, 317 insertions(+), 78 deletions(-)
```

`.agents/W2-7f/handoff.md` (this file) is new, not counted above.

## What changed

1. **Reason codes (W-1, D-PERS-3).** `porchlamp/core/persona.py`:
   - `_first_timer_qualifies`/`_career_capital_qualifies` (bool) replaced by
     `_first_timer_reason`/`_career_capital_reason` (`str | None`).
     `ai_assisted`/`senior_systems` (`_ai_assisted_gated`/
     `_senior_systems_gated`) are untouched, per D-PERS-3.
   - New `REASON_INSUFFICIENT_EVIDENCE_PREFIX = "insufficient_evidence:"`
     and `CRITICALITY_PILLAR = "criticality"` constants.
   - `_first_timer_reason`: `None` if either D-PERS-2 branch qualifies;
     else `insufficient_evidence:mr_first_rate` if `mr_first_rate` is
     `Unmeasured` (checked first), else `insufficient_evidence:
     newcomer_issue_supply` if that input is `Unmeasured`, else the
     substantive `high_barrier_to_entry` (both inputs were `Measured` and
     still failed).
   - `_career_capital_reason`: `insufficient_evidence:criticality` when the
     pillar is missing or its `score is None`; else the substantive
     `criticality_below_threshold` when a real score still fails the
     threshold/coverage bar.
   - `persona_gates` wired to the two reason functions; module docstring
     rewritten with a D-PERS-3 section.
2. **Purity test (W-2).** New
   `test_persona_gates_and_persona_composite_do_not_mutate_containers`:
   `copy.deepcopy` the record, call `persona_gates` once and
   `persona_composite` for every preset, then `assert record == before`
   (dataclass `==` walks into `persona_gates`/`suspicion`/
   `persona_composites`/`pillars`, none of which `frozen=True` protects).
3. **`PersonaComposite` (W-3).** `porchlamp/core/models.py`, additive only: new
   `@dataclass(frozen=True) class PersonaComposite(porchlamp: float | None,
   coverage: float, tier: str | None, rated: bool)`, placed between
   `PillarScore` and `ScoreRecord`; `ScoreRecord.persona_composites`
   annotation changed from `dict[str, float]` to `dict[str,
   PersonaComposite]`. `persona_composite` now returns a `PersonaComposite`
   (never a bare tuple); `rated` is `aggregate.coverage_status(coverage)`'s
   own `rated` bool (persona-weighted coverage >= 0.60, D-MISS-3 under the
   active weights — never copied from the balanced record, per `04` §6).
   Every test call site updated from tuple-unpacking to `.porchlamp`/`.coverage`/
   `.tier`/`.rated` field access.
4. **Ceiling and rounding tests (W-4).** Two new tests:
   `test_persona_composite_tier_uses_unrounded_composite_not_rounded`
   (all five pillars 69.996 → `porchlamp=70.0`, `tier="B"`, kills M18) and
   `test_persona_composite_applies_binding_ceiling_not_just_tier_by_score`
   (first_timer, accessibility unmeasured, coverage 0.70, raw 79.857142857…
   → `porchlamp=79.86`, `tier="B"`, kills M19).
5. Six existing tests updated for the new reason semantics (two first_timer
   boundary tests now assert `insufficient_evidence:<other input>`; the
   both-unmeasured test now asserts `insufficient_evidence:mr_first_rate`;
   two career_capital tests now assert `insufficient_evidence:criticality`;
   two career_capital "measured but failing" tests now assert
   `criticality_below_threshold` explicitly); one new substantive-reason
   first_timer test added. Net: 38 → 42 tests.

Function names kept: `persona_gates`, `persona_composite` (the two names
W4-2 depends on) are unchanged in name and (for `persona_gates`) return
shape; only `persona_composite`'s return type changes, per item 3's
explicit instruction.

## Verification

### Packet tests

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../.venv/bin/python -B -m pytest tests/core/test_persona_v2.py -p no:cacheprovider
..........................................                               [100%]
42 passed in 0.04s
```
exit=0

### tests/core

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../.venv/bin/python -B -m pytest tests/core -p no:cacheprovider
........................................................................ [ 30%]
........................................................................ [ 61%]
........................................................................ [ 91%]
....................                                                     [100%]
236 passed in 0.13s
```
exit=0 (includes `tests/core/test_import_graph.py` 3 passed)

### Full suite

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../.venv/bin/python -B -m pytest tests/ -n 4 -p no:cacheprovider
...
1577 passed, 12 warnings in 47.74s
```
exit=0. 1573 (main tip, W2-7 already landed) + 4 net new tests (42 - 38) =
1577 exactly; no test lost.

### Ruff

```
$ .../.venv/bin/ruff check porchlamp/core/persona.py tests/core/test_persona_v2.py porchlamp/core/models.py
All checks passed!
```
exit=0

```
$ .../.venv/bin/ruff check porchlamp/ scripts/ tests/ --output-format=concise
scripts/curate_dataset_llm.py:16:8: F401 `time` imported but unused
scripts/curate_dataset_llm.py:18:22: F401 `datetime.datetime` imported but unused
scripts/curate_dataset_llm.py:18:32: F401 `datetime.timezone` imported but unused
scripts/curate_dataset_llm.py:20:20: F401 `typing.Any` imported but unused
Found 4 errors.
```
exit=1 — same 4 pre-existing errors the W2-7 review already logged, in a
file this packet does not own; none in owned files.

## Mutant re-verification (all five review survivors, now killed)

Each mutant applied by hand to `porchlamp/core/persona.py`, `tests/core/test_persona_v2.py`
run, then the file restored from a saved copy and diffed byte-identical
before the next mutant (`diff persona.py.orig porchlamp/core/persona.py` printed
nothing after every round; final restoration confirmed the same way).

**M12** — `record.persona_gates.update(gates)` appended at the end of
`persona_gates`:
```
FAILED tests/core/test_persona_v2.py::test_persona_gates_and_persona_composite_do_not_mutate_containers
1 failed, 41 passed in 0.05s
```
KILLED.

**M21** — `record.suspicion.append("MUTANT_M21")` appended at the end of
`persona_gates`:
```
FAILED tests/core/test_persona_v2.py::test_persona_gates_and_persona_composite_do_not_mutate_containers
1 failed, 41 passed in 0.05s
```
KILLED.

**M22** — `record.persona_composites[persona] = composite` appended before
`persona_composite`'s rated return:
```
FAILED tests/core/test_persona_v2.py::test_persona_gates_and_persona_composite_do_not_mutate_containers
1 failed, 41 passed in 0.05s
```
KILLED.

**M18** — tier computed from the rounded composite
(`aggregate.tier_from_score(composite)` instead of `composite_raw`):
```
FAILED tests/core/test_persona_v2.py::test_persona_composite_tier_uses_unrounded_composite_not_rounded
AssertionError: assert 'A' == 'B'
1 failed, 41 passed in 0.05s
```
KILLED.

**M19** — `apply_ceiling` dropped (`tier = aggregate.tier_from_score(composite_raw)`):
```
FAILED tests/core/test_persona_v2.py::test_persona_composite_applies_binding_ceiling_not_just_tier_by_score
AssertionError: assert 'A' == 'B'
1 failed, 41 passed in 0.05s
```
KILLED.

After the last round, `porchlamp/core/persona.py` was restored and diffed clean
against the pre-mutation copy; `tests/core/test_persona_v2.py` re-run once
more: `42 passed in 0.04s`.

## Measure re-run — reason histogram

Same data and methodology as the W2-7 handoff/review (`~/Projects/cvi-scratch/sample-200`,
read-only; frozen populations `~/Projects/cvi-scratch/W2-4/populations.json`;
`porchlamp.core.engine.score` with `GateEvidence(manifest_found=None)` — every
other field left at its `None`/`False` default, the widest legitimate
gate-eligible set derivable without fabricating collector data; `as_of =
2026-09-13T00:00:00+00:00`). Gate-eligible = `record.params != {}` (stage-0
pass, same denominator the W2-7 handoff used). Script:
`/tmp/claude-1000/-home-pctablet505-Projects/2a48e84a-2b94-4a2f-8bff-adbe955a4084/scratchpad/measure_w2_7f.py`.

```
sample bundles: 199
loaded+validated: 184  (15 BundleValidationError/missing)
gate-eligible (stage-0, all GateEvidence None, anchor=False): 78

ai_assisted      gated=  0  visible= 78  of 78
senior_systems   gated= 35  visible= 43  of 78
    elephant_factor_below_3: 35
first_timer      gated= 78  visible=  0  of 78
    insufficient_evidence:mr_first_rate: 78
career_capital   gated= 78  visible=  0  of 78
    criticality_below_threshold: 20
    insufficient_evidence:criticality: 58
```

Matches the W2-7 handoff/review's gate counts exactly (78 eligible, 35
senior_systems, 78/78 first_timer and career_capital). The reason histogram
is the new evidence this packet adds: `first_timer` is
`insufficient_evidence:mr_first_rate` for all 78 (the `mr_first_rate`
collector, W1-4, is unwired at this base commit — C-2 in the W2-7 review —
so it is `Unmeasured` for every sample bundle; the substantive
`high_barrier_to_entry` reason from before this packet was therefore false
for 78/78 repos). `career_capital` splits 20/58: 20 records have the
criticality pillar partially measured (via `downloads_monthly`, one of the
five v1→v2-mapped inputs at this base commit) with a real score that still
fails the >=60/>=0.80 bar — the substantive `criticality_below_threshold`
is correct for those 20 — while the other 58 have the pillar wholly
unmeasured and now correctly report `insufficient_evidence:criticality`
instead of the same substantive-sounding string. `ai_assisted` (0/78,
unaffected — exclusion gate) and `senior_systems` (35/78,
`elephant_factor_below_3`, unaffected — exclusion gate, real measured
signal) are unchanged from the W2-7 baseline, as D-PERS-3 requires.

## COULD NOT VERIFY

- Whether W4-2 (the wiring packet) or any other in-flight packet already
  imports `persona_composite`'s return value as a 3-tuple — `grep -rln
  "core.persona" tests/ porchlamp/` (same check the W2-7 review ran) still
  returns only `porchlamp/core/persona.py` and its own test file at this base
  commit, so no such caller exists yet to break; not re-checked against a
  W4-2 worktree, which this packet does not have access to.
