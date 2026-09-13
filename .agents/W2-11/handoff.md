# W2-11 handoff — ParamScore carries its raw value; aggregate takes weights

Branch `W2-11`. Dispatch base `9c0d7dc` ("docs(v2): lead log — W2-8 accepted
round 2"). Main was a fast-moving target for the whole packet (several other
packets landing concurrently) — most importantly **W2-8b landed on main**
(`f32f7ff`) partway through, which changes one of this packet's own
deliverables (see §3 below). Rebased three times, most recently onto
`91362a1` ("docs(agents): W1-7f handoff"); `git merge-base HEAD main ==
91362a1`, so the branch is fully caught up as of this handoff (main may well
have moved again since — re-rebase before merging). Three commits on
`W2-11`: `07d50f4` (the packet's own deliverables), `76bddac` (fallout from
W2-8b/W4-2 landing on main mid-task — see §3/§4 below) and `f59a709` (this
handoff). Worktree `/home/pctablet505/Projects/cvi-wt-W2-11`. Scratch
`/home/pctablet505/Projects/cvi-scratch/W2-11/` (`dump_scores.py`, the
before/after JSON dumps, and `fixed-backup/` — a copy of every fixed file,
used to restore byte-identical after each fail-before/mutant round).

## Diffstat (base `91362a1`, current `git merge-base HEAD main` → `HEAD`)

```
 cvi/audit/detectors_report.py            |  13 ++++
 cvi/audit/sensitivity.py                 |  54 +++++--------
 cvi/core/aggregate.py                    |  77 ++++++++++++++----
 cvi/core/engine.py                       |  11 ++-
 cvi/core/models.py                       |  25 +++++-
 cvi/core/params/curves.py                |  39 +++++++++-
 cvi/core/persona.py                      |  56 ++++++-------
 tests/audit/test_anchors_fixture.py      |  15 +++-
 tests/audit/test_detectors_report.py     |  43 ++++++++++
 tests/audit/test_sensitivity_fixture.py  |  15 ++--
 tests/core/test_aggregate.py             | 130 ++++++++++++++++++++++++++++++-
 tests/core/test_engine_v2.py             |  51 +++++++++++-
 tests/core/test_params_accessibility.py  |  66 +++++++++++++---
 tests/core/test_params_criticality.py    |  44 +++++++++--
 tests/core/test_params_governance.py     |  62 +++++++++++++--
 tests/core/test_params_responsiveness.py |  72 ++++++++++++++---
 tests/core/test_params_rigor.py          |  68 +++++++++++++---
 tests/core/test_persona_v2.py            |  55 +++++++++++++
 tests/snapshot/test_populations.py       |  11 ++-
 tests/store/test_reader.py               |   2 +-
 tests/store/test_roundtrip.py            |   6 +-
 tests/store/test_writer.py               |  14 ++--
 tests/verify/test_detectors.py           |  39 +++++++++-
 23 files changed, 810 insertions(+), 158 deletions(-)
```

`git status --short` on the worktree is clean — nothing outside this list
touched. **Not touched**: `cvi/core/pipeline.py`, `tests/fixtures/`,
`cvi/store/**` (implementation — three of its *tests* were fixed, see
below), `cvi/collect/**`, `scripts/`.

## 1. `ParamScore`'s resulting field list

```python
@dataclass(frozen=True)
class ParamScore:
    value: float | str | None       # NEW — the raw Measured.value, verbatim, before any curve
    score: float | None             # unchanged
    measured: bool                  # unchanged
    reason: str | None              # unchanged
    n: int                          # unchanged
    window_start: str | None        # NEW — copied from Measured.window_start
    window_end: str | None          # NEW — copied from Measured.window_end
    method: str | None              # unchanged
    source: str | None              # NEW — copied from Measured.source
    fetched_at: str | None          # NEW — copied from Measured.fetched_at
```

06 §4's evidence-endpoint row asks for `value, score, n, window, method,
source, fetched_at`; `score`/`n`/`method` already existed, so `value`,
`window_start`/`window_end` (the `window` pair), `source` and `fetched_at`
were the ones added — no default on any new field, so every existing
constructor site needed a real value (below).

**Population rule** (all five new fields, decided once and applied
consistently everywhere in `_apply`/`engine.py`/`detectors_report.py`):
copied from the `Measured` input whenever one exists — including the
`insufficient_n` and `no_population` outcomes, where a real measurement
*was* taken but the score computation still declined it — and `None` only
when the bundle input was `Unmeasured` (nothing was ever measured to
report). `method` keeps its pre-existing, narrower convention (`None`
whenever no score was computed) unchanged — that is a different fact
(which *method produced the score*) from the five evidence fields (what was
*measured*), and this packet did not touch it.

## 2. `aggregate.composite_score_raw` / `composite_coverage`

Both gained `*, weights: Mapping[str, float] = constants_v2.PILLAR_WEIGHTS`
(keyword-only, default the frozen balanced weights), validated on every
call by the new `aggregate.validate_pillar_weights` (moved here from
`persona.py`'s private `_validate_preset`, which now delegates to it and
wraps the message with the persona name). `persona.persona_composite` calls
the two `aggregate` functions with the persona's preset instead of carrying
its own copy of the weighted reduction. Property test
(`test_persona_composite_matches_the_pre_w2_11_inline_formula_bit_for_bit`,
`tests/core/test_persona_v2.py`): 2,000 random pillar vectors × the 5
persona presets, the old formula transcribed inline as the oracle — every
one matches `persona_composite`'s new (aggregate-delegating) result
bit-for-bit.

Score dump (`/home/pctablet505/Projects/cvi-scratch/W2-11/dump_scores.py`,
13 synthetic repos — 12 gate-eligible with a perturbed TTFHR each, 1
unrated) run against the pre-packet checkout and this branch: `cmp` reports
byte-identical (`before.json`/`after.json` in the scratch dir).

## 3. W2-8b landed on main mid-task — the packet's third deliverable target

The dispatch said: "Delete the private copies in `persona.py` and (if on
main by then, else note it for W2-8b's rebase) `cvi/audit/sensitivity.py`".
At dispatch, W2-8b was not on main and its `sensitivity.py` v2 additions
lived only in `cvi-wt-W2-8b`. **W2-8b landed on main (`f32f7ff`) before this
packet's rebase** — so `cvi/audit/sensitivity.py`'s own
`_weighted_composite_raw`/`_weighted_coverage` (byte-identical to the copy
the dispatch pointed at in the W2-8b worktree) arrived on `main` for real.
Deleted both here too, same pattern as `persona.py`:
`_perturbed_tie_break_input` now calls
`aggregate.composite_score_raw`/`composite_coverage` with the perturbed
weight vector. Fail-before: reverting only `sensitivity.py` to the
pre-delete version left every test green (the two hand-computed tests
`tests/audit/test_sensitivity_fixture.py::test_weighted_composite_and_coverage_hand_computed`
/ `test_weighted_composite_raw_is_none_when_every_pillar_unmeasured` import
straight from `cvi.core.aggregate`, not `cvi.audit.sensitivity`, so they
were rewritten first). Named mutant: dropping the `weights=` keyword on
the `composite_score_raw` call (silently reverting to the balanced weights
regardless of the perturbation) turns
`test_sensitivity_v2_arithmetic_on_a_rated_synthetic_snapshot` red (the
"responsiveness perturbation must move the rank" assertion fails) —
restored byte-identical after.

## 4. Rebase fallout: constructor sites that did not exist at dispatch

The same main advance that landed W2-8b also landed `cvi/store` (W4-2) and
a `cvi/audit/anchors.py` fix, both with `ParamScore(...)` construction
sites in their test suites that postdate this packet's original base commit
and so were not touched by the first commit. Fixed all of them (second
commit, `4619101`): `tests/store/test_reader.py` (1 site),
`tests/store/test_roundtrip.py` (3), `tests/store/test_writer.py` (7),
`tests/audit/test_anchors_fixture.py` (3). **No file under `cvi/store/`
itself was touched** — that package only ever reads `ParamScore` by
attribute, never constructs one.

## 5. Tests (deliverable 3)

- Every Measured `ParamScore` on the `EligibleFixture`/mixed-Measured-
  Unmeasured fixtures in `tests/core/test_engine_v2.py` has `value`/`n`/
  `window_start`/`window_end`/`source`/`fetched_at` equal to the bundle
  input's, and `value is None` for every Unmeasured one
  (`test_engine_end_to_end_every_field`,
  `test_unrated_repo_has_no_composite_or_tier_but_keeps_params_and_coverage`).
- Named mutant (`cvi/core/params/curves.py`'s successful-score branch,
  `value=value.n` instead of `value=value.value`): 27 tests across every
  `test_params_*.py` module plus `test_engine_v2.py` go red.
- `ParamScore(...)` omitting `value` → `TypeError` (dataclass with no
  default), asserted directly
  (`test_param_score_requires_value_no_default`, `tests/core/test_aggregate.py`).
- `composite_score_raw`/`composite_coverage` with weights not summing to 1
  → `ValueError`
  (`test_composite_score_raw_rejects_weights_not_summing_to_one`,
  `test_composite_coverage_rejects_weights_not_summing_to_one`); named
  mutant on `validate_pillar_weights`'s sum check (`if False and ...`) turns
  both red.
- Persona composites identical to before: the existing
  `tests/core/test_persona_v2.py` suite (43 tests, unchanged assertions)
  passes unchanged, plus the new 2,000×5 property test above.
- `cvi.audit.detectors_report._param_score_from_json`'s five new optional
  fields round-trip when present and default to `None` when absent
  (`tests/audit/test_detectors_report.py`, two new tests).

## Fail-before / named-mutant log

Every listed file was fail-before'd and mutant-tested individually, always
via `git show <base>:<path> > <path>` to reach the base content (never
`git stash`/`git checkout --`) and a saved copy in
`cvi-scratch/W2-11/fixed-backup/` to restore the fixed content afterward —
each restore verified `cmp`-identical to the backup before moving on.

| File | Fail-before (red on base) | Named mutant (red) | Restored |
|---|---|---|---|
| `cvi/core/models.py` + `cvi/core/params/curves.py` + `cvi/core/engine.py` | 86 tests fail (TypeError: missing 5 required args, across every `test_params_*`, `test_aggregate`, `test_engine_v2`, `test_detectors`, `test_populations`) | `value=value.n` in `curves.py`'s success branch → 27 tests red | `cmp` OK, all 3 files |
| `cvi/core/aggregate.py` | `test_aggregate.py` fails to collect (`ImportError: validate_pillar_weights`) | `if False and abs(total-1.0)>tol` disables the sum check → 2 tests red | `cmp` OK |
| `cvi/core/persona.py` (fixed) + `cvi/core/aggregate.py` (base) | `test_persona_v2.py` fails to collect (`AttributeError: module 'cvi.core.aggregate' has no attribute 'validate_pillar_weights'`) — proves `persona.py`'s new call site genuinely depends on the `aggregate.py` fix | — (covered by the aggregate.py mutant above; same call path) | `cmp` OK, both files |
| `cvi/audit/detectors_report.py` | 10 tests fail (`TypeError: missing 5 required positional arguments`) | not separately mutated — same construction pattern as `curves.py`'s, already proven by that mutant | `cmp` OK |
| `cvi/audit/sensitivity.py` | n/a (the two `_weighted_*` tests import from `cvi.core.aggregate` directly post-rewrite, so `sensitivity.py`'s own content doesn't gate them — see §3) | dropping `weights=` in `_perturbed_tie_break_input`'s `composite_score_raw` call → `test_sensitivity_v2_arithmetic_on_a_rated_synthetic_snapshot` red | `cmp` OK |

## Suite

```
$ cd /home/pctablet505/Projects/cvi-wt-W2-11
$ find . -name __pycache__ -type d -exec rm -rf {} +
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -B -m pytest -n 4 -p no:cacheprovider
2 failed, 2050 passed, 1 skipped, 12 warnings in 12.88s
```

(The passed count grew from 2007 to 2050 across the two later rebases —
W1-2f and W1-7f landing on main, unrelated to this packet; the same 2
failures, for the same reason, persisted through both.)

`ruff check cvi tests`: **All checks passed.**

## COULD NOT VERIFY / blocking finding

**`tests/snapshot/test_reproduce_fixture.py::test_reproduce_matches_expected_hash`
and `::test_fixture_rebuild_is_byte_identical` are red on this branch and
will be red on `main` once this lands — not a scoring defect, a landing-
order fixture-pin problem, same shape as the W2-8b review's own B-1.**

Root cause: `cvi.core.pipeline._to_plain`/`canonical_records_json` (owned by
W2-8b, in the explicitly out-of-scope `cvi/core/pipeline.py`) serializes
every `ScoreRecord` — `ParamScore` included — via a blind
`dataclasses.fields()` walk, no allow-list. Adding 5 required fields to
`ParamScore` therefore changes `records_hash_of(...)` for any snapshot,
including the one frozen in `tests/fixtures/snapshot_50/expected.json`
(built and pinned by W2-8b/W2-8c before this packet's fields existed).

This packet's own dispatch text says "Stay out of `cvi/core/pipeline.py`
and `tests/fixtures/` (W2-8b is re-pinning)" — written when W2-8b was still
in flight and expected to re-pin the fixture during its own rebase onto
this packet. **W2-8b landed on main before this packet finished**, so that
re-pin never happened for the `ParamScore` fields added here. Per the
instruction, `cvi/core/pipeline.py` and `tests/fixtures/` were left
untouched; `mcp__ccd_session__spawn_task` was used to flag a follow-up
(`task_0b46b79f`, "Rebuild snapshot_50 fixture hash for W2-11's ParamScore
fields") for whoever owns the fixture (W2-8b/W2-8c lineage) to regenerate
`tests/fixtures/snapshot_50/expected.json` on top of `main` + `W2-11` —
exactly the same "regenerate at landing" fix the W2-8b review applied to
its own B-1. A second, pre-existing pending task
(`task_b25c02c8`, "Regenerate snapshot_50 fixture after W1-2f dependents
fix") confirms this fixture already needed a rebuild for an unrelated
reason, so this is a known, tracked category of follow-up, not a novel
surprise.

Independent evidence this is purely structural, not a scoring regression:
every one of the other 2007 tests passes, including the sensitivity_v2/
anchors_v2 tests that also run against `ScoreRecord`/`ScoredSnapshot`
data; the standalone before/after score dump (§2 above) is byte-identical
for composite/coverage/tier/pillars/leverage/friction/quadrant across 13
synthetic repos.

No other COULD-NOT-VERIFY items.
