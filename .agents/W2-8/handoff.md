# W2-8 handoff — constants freeze, PART A only

Dispatch base: `c7b2a1b` (main tip at dispatch). Main moved repeatedly
during this packet; rebased twice — first onto `59b9614` (coordinator
notice: W2-7f `3812bc0`, W2-9f `b9d4820`, W2-10 `8e89d03` had landed), then
again onto `92bc615` (W2-9g, no file overlap, clean auto-rebase, run on my
own initiative per the packet's "before writing the handoff, check main"
rule). **Final base: `92bc615`.** Main advanced once more to `913ae2c`
after this second rebase's verification pass — confirmed docs-only
(`docs/v2/00-README.md`, +2 lines, the lead log), no overlap with anything
in this packet, not rebased onto (chasing a continuously-moving docs-only
tip has no payoff). Commits on branch `W2-8`: `e3e74fc` ("feat(core): W2-8
constants freeze part A — relocate tunables into constants_v2, freeze
BENCHMARK_VERSION, rated-path pins") and `c6a0a8b` ("docs(agents): W2-8
handoff"). Two commits. Not pushed. Main not touched.

Scope: **PART A only** (constants freeze + prerequisites). PART B
(W2-8b: bundles→ranked-records scoring driver, 50-repo fixture snapshot,
anchors/sensitivity over v2 records, CI job) is a separate, concurrently
running packet — confirmed with the lead that it owns `cvi/core/pipeline.py`,
`tests/fixtures/snapshot_50/`, `tests/core/test_pipeline.py`, `tests/audit/*`,
`tests/snapshot/test_reproduce_fixture.py`, `.github/workflows/ci.yml`; none
of those paths are touched here.

## Diffstat (my own diff, rebased branch vs. final rebased base `92bc615`)

```
 cvi/core/aggregate.py                |  17 +-
 cvi/core/constants_v2.py             | 310 ++++++++++++++++++++++++++++++++---
 cvi/core/engine.py                   |  10 +-
 cvi/core/gates.py                    |  30 ++--
 cvi/core/matrix.py                   |  23 ++-
 cvi/core/params/accessibility.py     |  20 ++-
 cvi/core/params/criticality.py       |   7 +-
 cvi/core/params/curves.py            |  20 ++-
 cvi/core/params/governance.py        |  35 ++--
 cvi/core/params/responsiveness.py    |  30 ++--
 cvi/core/params/rigor.py             |  19 +--
 cvi/core/persona.py                  | 116 +++++--------
 cvi/core/tiebreak.py                 |  16 +-
 cvi/snapshot/populations.py          |   7 +-
 docs/v2/rfcs/CHANGELOG.md            |   9 +
 docs/v2/rfcs/README.md               |  90 ++++++++++
 tests/core/test_constants_hash.py    |  21 ++-
 tests/core/test_engine_rated_path.py | 248 ++++++++++++++++++++++++++++
 tests/core/test_frozen.py            | 159 ++++++++++++++++++
 19 files changed, 989 insertions(+), 198 deletions(-)
```

**Size deviation**: source (non-test, non-docs) diff is ~660 changed lines
against the packet's "~600 lines of diff excluding tests" budget. Reported,
not hidden: `constants_v2.py`'s 310-line change is inherently the
deliverable itself (dozens of new frozen constants — see relocation table);
`persona.py`'s 116 is largely doc-comment rewrite (the module docstring's
"ownership request to W2-8" prose, now that W2-8 has done it) plus the
merge-conflict resolution against W2-7f's landed `D-PERS-3` prose. No
functional line is longer than a one-line rebind (`NAME = constants_v2.NAME`
or a dict-key lookup at a `ParamDef` call site).

## Relocation table (symbol, from-module, value, in-hash)

| Symbol | From module | Value | In `constants_dict()` |
|---|---|---|---|
| `PERSONA_PRESETS` (5 preset dicts) | `cvi/core/persona.py` | balanced=`PILLAR_WEIGHTS`; first_timer/career_capital/senior_systems/ai_assisted per D-PERS-2 | yes |
| `SENIOR_SYSTEMS_MIN_ELEPHANT_FACTOR` | persona.py | 3.0 | yes |
| `FIRST_TIMER_MIN_DECIDED_PRS` | persona.py | 5 | yes |
| `FIRST_TIMER_MIN_MR_FIRST` | persona.py | 0.40 | yes |
| `FIRST_TIMER_MIN_NEWCOMER_ISSUES` | persona.py | 3.0 | yes |
| `CAREER_CAPITAL_MIN_CRITICALITY` | persona.py | 60.0 | yes |
| `CAREER_CAPITAL_MIN_COVERAGE` | persona.py | 0.80 | yes |
| `GATED_PERSONAS` | persona.py | `(ai_assisted, senior_systems, first_timer, career_capital)` | yes |
| `WEIGHT_SUM_TOLERANCE` | persona.py | 1e-6 | yes |
| `CEILING_A_MIN_COVERAGE` | `cvi/core/aggregate.py` | 0.80 | yes |
| `NO_CEILING_MIN_COVERAGE` | aggregate.py | 0.95 | yes |
| `EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE` (the rated threshold) | `cvi/core/gates.py` | 0.60 | yes |
| `COMPOSITE_DECIMALS` | `cvi/core/tiebreak.py` (3 `round(...,2)` sites) | 2 | yes |
| `MIN_POPULATION` | `cvi/snapshot/populations.py` | 30 | yes |
| `GATE4_VITALITY_WINDOW_DAYS` | gates.py | 180 | yes |
| `GATE5_MIN_N` | gates.py | 10 | yes |
| `GATE5_MIN_AGE_DAYS` | gates.py | 14 | yes |
| `GATE5_RESPONSE_DEADLINE_DAYS` | gates.py | 14 | yes |
| `GATE5_MAX_ZERO_RESPONSE_SHARE` | gates.py | 0.50 | yes |
| `GATE6_DECISION_WINDOW_DAYS` | gates.py | 365 | yes |
| `LEVERAGE_WEIGHTS` | `cvi/core/matrix.py` | criticality .50/rigor .30/governance .20 | yes |
| `FRICTION_WEIGHTS` | matrix.py | responsiveness .50/governance .30/accessibility .20 | yes |
| `QUADRANT_THRESHOLD` | matrix.py | 50.0 | yes |
| `MONO_TOLERANCE` | `cvi/core/params/curves.py` | 1e-9 | yes |
| `RATE_SLOPE` | curves.py | 125.0 | yes |
| `COHORT_RATE_SLOPE` | curves.py | 200.0 | yes |
| `TTFHR_KNEE_HOURS` | `cvi/core/params/responsiveness.py` | 12.0 | yes |
| `TTFHR_SLOPE_PER_DOUBLING` | responsiveness.py | 25.0 | yes |
| `GHOST_RATE_SLOPE` | responsiveness.py | 200.0 | yes |
| `SUPPLY_SLOPE` | `cvi/core/params/accessibility.py` | 10.0 | yes |
| `ELEPHANT_SLOPE` | `cvi/core/params/governance.py` | 25.0 | yes |
| `LEGAL_IP_SCORES` | governance.py | DCO 100/NONE 85/FOUNDATION_CLA 75/CORP_CLA 25 | yes |
| `PROMOTION_LADDER_SCORES` | governance.py | VERIFIED 100/DOCUMENTED 75/AD_HOC 40/CLOSED 10 | yes |
| `AI_POSTURE_SCORES` | `cvi/core/params/rigor.py` | nested dict, persona-conditional | yes |
| `PARAM_WEIGHTS` (18 entries) | every `ParamDef(weight=...)` call site across `params/*` | per-parameter within-pillar weight | yes |
| `PARAM_MIN_N` (18 entries, some `None`) | every `ParamDef(min_n=...)` call site | per-parameter minimum n | yes |
| `PIPELINE_DEFENSES_PROXY_MIN_N` | rigor.py (`_PIPELINE.proxy_min_n`) | 10 | yes |
| `REASON_NO_POPULATION` / `UNMEASURED_REASONS` (+1 member) | `cvi/core/engine.py` (`_REASON_NO_POPULATION`) / constants_v2 (already existed, missing the member) | `"no_population"` | yes |
| `BENCHMARK_VERSION` | constants_v2.py (already there) | `"2.0.0-dev"` → `"2.0.0-rc1"` | yes |

**Not relocated (deliberate, reasoning given inline in each module)**:
- `ParamDef.domain` tuples (18 of them) — not read by `_apply` at all,
  only by the `monotonicity_violations` property-test probe grid; changing
  one changes which grid points get sampled, never a computed score.
- `_REFERENCE_SAMPLE` in `curves.py` — the D-PARAM-MONO probe population
  (0..32), likewise test machinery, not a scoring input.
- `OSI_APPROVED_LICENSES` / `UNDETERMINED_SPDX` in `gates.py` — SPDX/OSI
  license-id vocabularies sourced from an external standard, not numeric
  tunables; the packet's own instruction scoped the grep-discovered set to
  "numeric tunables (thresholds, windows, minimum counts, weights, curve
  parameters)", and a license-id list update is not something the RFC
  procedure (rank-movement + sensitivity evidence) is built to gate.
- `GATE_1..GATE_6`, `REASON_*`, `ROUTED_OFF_PLATFORM` in gates.py; `QUADRANT_*`
  labels in matrix.py; `PERSONA_*`/`INPUT_*`/`REASON_*` identifiers in
  persona.py; `KIND_NUMERIC/ECDF/ENUM` in curves.py — identifier/label
  strings, explicitly out of scope per the packet text.
- `TIER_CUTOFFS`/`TIER_FLOOR`/`TIER_ORDER` in aggregate.py — not named in
  the packet's aggregate.py list (only the two ceiling thresholds and the
  rated threshold were named); the (cutoff, label) pairing would need
  splitting or the labels moved too, deferred.
- `POPULATIONS_DOCUMENT_VERSION` in populations.py — a document-format
  version tag (like `SCHEMA_VERSION`, itself already frozen separately),
  not a scoring tunable.

## `CONSTANTS_HASH`

`fb48552cbf13aececaccbb74c83967f7d594211bfad9f7b32f0d8d90945c4c2a`
(64 hex chars, confirmed by `test_constants_hash_is_sha256_hex`). Computed
as `sha256(canonical_json(constants_dict())).hexdigest()` — unchanged
across the rebase (main's own commits since `c7b2a1b` never touched
`constants_v2.py`). Pinned literally in `tests/core/test_frozen.py` as
`FROZEN_CONSTANTS_HASH`.

## Fail-before / after — `Unmeasured("no_population")`

**Before** (on base `c7b2a1b`, `cvi/core/models.py` + `cvi/core/constants_v2.py`
restored via `git show c7b2a1b:<path>` to an isolated scratch copy, not the
live worktree):
```
>>> Unmeasured('no_population')
RAISED BundleValidationError unmeasured.reason must be one of
('third_party_gap', 'collector_failure', 'insufficient_n', 'not_applicable'),
got 'no_population'
```
Confirmed structurally: `models.py`'s `Literal` type hint for
`Unmeasured.reason` already lists `"no_population"` (added by W2-5f), but
`constants_v2.UNMEASURED_REASONS` (checked at runtime by
`unmeasured_problems`) did not — a type hint the runtime check contradicted.

**After** (this packet, `tests/core/test_frozen.py::test_no_population_is_a_constructible_reason`):
```
$ pytest tests/core/test_frozen.py -v
tests/core/test_frozen.py::test_no_population_is_a_constructible_reason PASSED
```
Over-correction guard in the same file,
`test_unmeasured_still_rejects_an_unknown_reason`: `Unmeasured("not_a_real_reason")`
still raises `BundleValidationError` — the fix didn't widen the check into
accepting anything.

## Drift proof (A1: change 3 relocated values, run `test_frozen`, paste failures, restore)

Mutated in the worktree (scripted, three `str.replace` calls on
`cvi/core/constants_v2.py`): `PERSONA_PRESETS["first_timer"]["responsiveness"]`
0.35→0.36, `MIN_POPULATION` 30→31, `CEILING_A_MIN_COVERAGE` 0.80→0.81.
Reproduced identically before the first rebase, after the first rebase
(tree at `0f5c05c`, superseded), and after the second rebase (tree at
`e3e74fc`, final):

```
$ pytest tests/core/test_frozen.py -v
tests/core/test_frozen.py F.......F.F..F......                           [100%]

FAILED tests/core/test_frozen.py::test_constants_hash_is_pinned
  AssertionError: assert '78dda206d7fd...8649de87e5628' == 'fb48552cbf13...d8d90945c4c2a'
FAILED tests/core/test_frozen.py::test_frozen_table[CEILING_A_MIN_COVERAGE]
  AssertionError: CEILING_A_MIN_COVERAGE moved: 0.81 != 0.8
FAILED tests/core/test_frozen.py::test_frozen_table[MIN_POPULATION]
  AssertionError: MIN_POPULATION moved: 31 != 30
FAILED tests/core/test_frozen.py::test_frozen_table[PERSONA_PRESETS.first_timer]
  AssertionError: PERSONA_PRESETS.first_timer moved: {'responsiveness': 0.36, ...} != {'responsiveness': 0.35, ...}
4 failed, 16 passed in 0.03s
```
Each of the three mutated constants is individually named (the
parametrized `test_frozen_table` runs every row independently, unlike a
single function with sequential `assert`s, which would have stopped at the
first failure and hidden the other two). File restored via `cp` from a
saved pre-mutation copy; `diff -q` confirmed byte-identical; full suite
re-run green (see below) after each restore.

## A3: R4/R5/R6 mutant kills (`cvi/core/engine.py` rated path, lines ~319-325 post-rebase)

Fixtures: `FullyMeasured` (C=1.0, no ceiling) and `GovernanceUnmeasured`
(all 3 governance inputs `Unmeasured` → governance pillar score `None` →
C=0.80 exactly, ceiling "A", `leverage_coverage=0.80`≠`friction_coverage=0.70`
— both non-zero and distinct because governance carries 0.20 of
`LEVERAGE_WEIGHTS` but 0.30 of `FRICTION_WEIGHTS`).

Clean run: `pytest tests/core/test_engine_rated_path.py -v` → **2 passed**.

**R4** (`rated=True` → `rated=False` on the rated-path return only):
```
FAILED tests/core/test_engine_rated_path.py::test_rated_path_no_ceiling_case
  AssertionError: assert False is True  (record.rated)
FAILED tests/core/test_engine_rated_path.py::test_rated_path_ceiling_a_case_with_distinct_axis_coverages
  AssertionError: assert False is True  (record.rated)
2 failed
```

**R5** (`tier_ceiling=ceiling` → `tier_ceiling=None` on the rated-path return):
```
tests/core/test_engine_rated_path.py .F                                  [100%]
FAILED tests/core/test_engine_rated_path.py::test_rated_path_ceiling_a_case_with_distinct_axis_coverages
  AssertionError: assert None == 'A'  (record.tier_ceiling)
1 failed, 1 passed
```
(the no-ceiling case's expected value is also `None`, so only the
ceiling-A fixture catches this mutant — this is why the packet asked for
both cases.)

**R6** (`leverage_coverage=coords.leverage_coverage` / `friction_coverage=coords.friction_coverage`
→ both hardcoded `0.0`):
```
FAILED tests/core/test_engine_rated_path.py::test_rated_path_no_ceiling_case
  AssertionError: assert 0.0 == 1.0 ± 1.0e-06  (record.leverage_coverage)
FAILED tests/core/test_engine_rated_path.py::test_rated_path_ceiling_a_case_with_distinct_axis_coverages
  AssertionError: assert 0.0 == 0.8 ± 8.0e-07  (record.leverage_coverage)
2 failed
```

Each mutant applied via a scripted string-replace on a saved copy of
`engine.py`, restored via `cp` + `diff -q` after each run (never
`git checkout -- <path>` / `git stash`). All three reproduced identically
before and after the rebase.

## Full suite before/after

- Base `c7b2a1b`, before any edit: **1631 passed**, exit 0.
- After A1+A2+A3, before any rebase: **1653 passed** (+22: 20 in
  `test_frozen.py`, 2 in `test_engine_rated_path.py`; 2 pre-existing tests
  in `test_constants_hash.py` updated in place with root-cause notes, no
  net count change from those).
- After the first rebase, onto `59b9614`: **1668 passed**, exit 0 (main's
  own commits since `c7b2a1b` — W2-7f/W2-9f/W2-10 — added 15 tests of their
  own; this packet's own +22 reproduced identically on top; R4/R5/R6 and
  the drift proof were re-run and re-confirmed against this tree, pasted
  above).
- After the second rebase, onto `92bc615` (W2-9g added 6 more tests
  independently): **1674 passed**, exit 0 — the final state.

Verbatim final line:
```
1674 passed, 12 warnings in 55.22s
exit=0
```
`ruff check cvi/ scripts/ tests/`: clean except the 4 pre-existing errors
in `scripts/curate_dataset_llm.py` (untouched, per the packet's own
instruction not to touch that file).

## Rulings incorporated from the coordinator's mid-flight notices

1. Rebased twice. First `git rebase refs/remotes/origin-local/main` (main
   tip `59b9614` at the time) onto branch `W2-8`. One real conflict:
   `cvi/core/persona.py`'s module-docstring prose (both sides described the
   same "ownership request to W2-8" paragraph — W2-7f's copy said it was
   still outstanding, mine said it was done). Resolved by keeping my
   description (accurate: the relocation is now done) while preserving
   W2-7f's substantive D-PERS-3 content elsewhere in the file (untouched by
   the merge, no conflict there). `cvi/core/engine.py` and
   `cvi/core/tiebreak.py` auto-merged cleanly (W2-10's
   `ScoreRecord.repo`/`TieBreakInput` validation landed alongside my
   `_REASON_NO_POPULATION`/`COMPOSITE_DECIMALS` edits with no line overlap).
   Second rebase onto `92bc615` (W2-9g) was a clean auto-rebase, zero file
   overlap, no conflict. Both rebases re-verified: full suite green, the
   drift proof and the R4/R5/R6 mutant kills re-run and re-confirmed
   byte-for-byte identical to the pre-rebase results, `CONSTANTS_HASH`
   unchanged.
2. Did not touch `cvi/core/pipeline.py`, `tests/fixtures/snapshot_50/`,
   `tests/core/test_pipeline.py`, `tests/audit/*`,
   `tests/snapshot/test_reproduce_fixture.py`, `.github/workflows/ci.yml`
   (W2-8b's paths, per the coordinator's notice).
3. No `ScoreRecord` is constructed directly in the new tests (only via
   `cvi.core.engine.score(...)`), so W2-10's new required `repo=` field
   needed no test-side changes here.

## COULD NOT VERIFY

- Whether W2-8b's fixture snapshot / CI job will need the relocated
  `PARAM_WEIGHTS`/`PARAM_MIN_N` dicts surfaced anywhere beyond
  `cvi.core.constants_v2` — not this packet's scope to build that consumer.
- The RFC template's "rank movement on the fixture" and "anchors" sections
  reference `tests/fixtures/snapshot_50/` and the anchor set, both owned by
  W2-8b; the template is written to point at them but nothing in this
  packet exercises it end-to-end (no RFC has been filed yet — none needed:
  this packet is the freeze itself, not a post-freeze change).
- `docs/v2/02-RANKING-DESIGN.md` D-VER-4's anchor list was read but not
  re-run against real data in this packet (that requires the fixture
  snapshot, W2-8b); the RFC template names all six anchor relations from
  the design doc verbatim so a future RFC author has the checklist.

## Findings that belong to W2-8b or elsewhere (not fixed here, out of scope)

- The scoring driver (bundles → ranked `ScoreRecord`s) does not exist yet
  outside test fixtures; `cvi.core.engine.score` is a pure per-repo
  function with no batch/ranking wrapper. W2-8b's plan (bundles→ranked-
  records driver) is the natural home; it will need to call
  `cvi.core.tiebreak.assign_ranks` over `TieBreakInput`s built from
  `FeatureBundle.repo` (owner/name) and `cvi.core.tiebreak.ttfhr_p75_from_pr_window`
  — the "last non-bot commit date" field `TieBreakInput` still has no
  source for (an open ownership request recorded in `tiebreak.py`'s own
  docstring, not resolved by this packet — it needs a dated commit field
  through the bundle or record, which is bigger than a constants freeze).
- `cvi/audit/*`'s v1-DB coupling: not inspected in this packet (out of the
  A1-A3 scope); noted here only because the packet asked me to report
  anything I noticed — I did not go looking, so this is a non-finding, not
  a clean bill of health.
- `cvi/core/aggregate.py`'s `TIER_CUTOFFS`/`TIER_ORDER` and `gates.py`'s
  `OSI_APPROVED_LICENSES`/`UNDETERMINED_SPDX` are the two "found but
  deliberately not relocated" categories (see the relocation table's "Not
  relocated" section) — if a future packet decides these should also be
  hash-covered, that is a scope decision for the lead, not a defect in
  this one.

---

# Round 2 (REWORK, bounded to 5) — census additions

Review: 52 PASS / 16 FAIL / 3 UNDECIDED, every claim reproduced (see
`/tmp/claude-1000/-home-pctablet505-Projects/2a48e84a-2b94-4a2f-8bff-adbe955a4084/scratchpad/reviews/W2-8-review.md`
and `.agents/W2-8/review/item-0{1..9}-*.md`). Central finding: 10 scoring
tunables were readable but not in `CONSTANTS_HASH`; two of them (tier
cutoffs, OpenSSF Pike weights) move real scored values with the hash
unchanged.

**Rebase history this round**: round-1 commits were replayed onto
`9bb1c22` (`a626289` feat, `4f6ae94` handoff — content-identical to
round 1's `e3e74fc`/`7529818`, only the parent changed), then round 2's
commit `6c8619a` landed on top, then rebased once more onto the final
main tip `097142a` (W0-4's LLM-curation-script deletion + INT-W0-4 +
several docs-only lead-log commits — zero file overlap with anything
this packet touches, verified with `git diff --stat` before rebasing;
clean, no conflicts). **Final commits**: `a626289` (feat, round 1), `4f6ae94` (docs, round-1
handoff), `6c8619a` (fix, round 2 — this section). **Final base: `097142a`.**

## Completed census table

| Symbol | Module | Round-1 status | Round-2 disposition |
|---|---|---|---|
| `TIER_CUTOFFS` | `cvi/core/aggregate.py` | uncovered, MUST ADD | **covered** — moved to `constants_v2`, re-bound; `_FROZEN_TABLE` row added (★) |
| `TIER_ORDER` | `cvi/core/aggregate.py` | uncovered, MUST ADD | **covered** — moved, re-bound; `_FROZEN_TABLE` row added (★) |
| `OPENSSF_WEIGHTS` | `cvi/core/openssf.py` | uncovered, MUST ADD | **covered** — moved, re-bound; `_FROZEN_TABLE` row added (★) |
| `OPENSSF_THRESHOLDS` | `cvi/core/openssf.py` | uncovered, MUST ADD | **covered** — moved, re-bound; `_FROZEN_TABLE` row added (★) |
| `SIGNAL_FIELDS` | `cvi/core/openssf.py` | uncovered, MUST ADD | **covered** — moved, re-bound; `_FROZEN_TABLE` row added (★) |
| `TTFHR_MIN_N` | `cvi/derive/bundle.py` | duplicate of `PARAM_MIN_N`, uncovered | **deleted** — value (10) confirmed identical to `PARAM_MIN_N["ttfhr_hours"]` (10), no divergence; call site now reads `constants_v2.PARAM_MIN_N["ttfhr_hours"]` directly |
| `SUBSTANTIVE_REVIEW_MIN_N` | `cvi/derive/bundle.py` | duplicate, uncovered | **deleted** — value (10) confirmed identical to `PARAM_MIN_N["substantive_review_rate"]` (10); call site reads the registry directly |
| `TTM_P90_MIN_N` | `cvi/derive/bundle.py` | duplicate, uncovered | **deleted** — value (5) confirmed identical to `PARAM_MIN_N["ttm_p90_hours"]` (5); call site reads the registry directly |
| `OSI_APPROVED_LICENSES` | `cvi/core/gates.py` | uncovered, SHOULD ADD | **covered** — moved to `constants_v2` (as `tuple(sorted(...))` for JSON-native hashing; kept as `frozenset` at the gate call site for O(1) membership), re-bound; not in `_FROZEN_TABLE` (not ★-marked) |
| `MIN_COVERAGE` | `cvi/audit/anchors.py` | REPORT (W2-8b's code, 1 of 3 UNDECIDED) | **not-hashed-by-ruling** — a release gate, not a scored value; left in place; documented in `docs/v2/rfcs/README.md`'s new "Process constants" section; still RFC-gated |
| `TAU_RELEASE_THRESHOLD` | `cvi/audit/sensitivity.py` | REPORT | **not-hashed-by-ruling** — same treatment |
| `DEFAULT_PERTURBATION_PP` | `cvi/audit/sensitivity.py` | REPORT | **not-hashed-by-ruling** — same treatment |
| `UNDETERMINED_SPDX` | `cvi/core/gates.py` | 1 of 3 UNDECIDED (review's own census summary calls it "correctly excluded" elsewhere, contradicting its UNDECIDED list) | **excluded-with-reason, not addressed this round** — not in the coordinator's round-2 task list; three sentinel strings meaning "no SPDX id recorded" (`None`/`"NOASSERTION"`/`"OTHER"`), not itself a vocabulary that can drift the way `OSI_APPROVED_LICENSES` can; flagged for the lead to resolve the review's internal inconsistency |
| `TIER_FLOOR` | `cvi/core/aggregate.py` | correctly excluded (label, no threshold) | **unchanged** — stays in `aggregate.py` |
| `ParamDef.domain` ×18, `_REFERENCE_SAMPLE`, `better_order`, `persona_orders` | `cvi/core/params/*` | correctly excluded (verified: read only by the monotonicity probe/validation, never by `_apply`) | **unchanged** |
| `GATE_*`, `REASON_*`, `ROUTED_OFF_PLATFORM`, `QUADRANT_*`, `PERSONA_*`, `INPUT_*`, `KIND_*`, `CEILING_A`/`CEILING_B` | various | correctly excluded (identifier strings) | **unchanged** |

**M4 guard** (`tests/core/test_frozen.py::test_every_module_level_constant_is_hashed_or_declared_not_hashed`
+ `test_m4_fail_before_an_unregistered_constant_is_caught`): every
module-level `UPPER_CASE` name in `constants_v2.py` not starting with `_`
must be a `constants_dict()` key or in the new `_NOT_HASHED` tuple.
`_NOT_HASHED = ("CONSTANTS_HASH",)` — the only member; `BENCHMARK_VERSION`
is deliberately *not* exempted (documented decision: D-VER-1 ties a version
bump to a constants change, so hashing it too is correct, not redundant).

**M12** (a call site reverted to a same-value literal survives) is
documented as an enforcement limit in `docs/v2/rfcs/README.md`'s new
"Enforcement limits" section — not built as a linter, per the coordinator's
instruction.

## Re-run verification (all against the branch before the final rebase to
`097142a`; hash and full-suite count re-confirmed identical after)

**Drift proof, item 1** (`TIER_CUTOFFS` A cutoff `70.0 -> 60.0`):
```
FAILED tests/core/test_frozen.py::test_constants_hash_is_pinned
FAILED tests/core/test_frozen.py::test_frozen_table[TIER_CUTOFFS]
  AssertionError: TIER_CUTOFFS moved: ((90.0, 'S+'), (80.0, 'S'), (60.0, 'A'), (55.0, 'B'), (40.0, 'C'))
                  != ((90.0, 'S+'), (80.0, 'S'), (70.0, 'A'), (55.0, 'B'), (40.0, 'C'))
2 failed, 25 passed
```

**Drift proof, item 2** (`OPENSSF_WEIGHTS["contributor_count"]` `2.0 -> 3.0`):
```
FAILED tests/core/test_frozen.py::test_constants_hash_is_pinned
FAILED tests/core/test_frozen.py::test_frozen_table[OPENSSF_WEIGHTS]
  AssertionError: OPENSSF_WEIGHTS moved: {..., 'contributor_count': 3.0, ...} != {..., 'contributor_count': 2.0, ...}
2 failed, 25 passed
```

**Drift proof, item 4** (`OSI_APPROVED_LICENSES`: remove `"MIT"`):
```
MIT in set: False (156 members, was 157)
FAILED tests/core/test_frozen.py::test_constants_hash_is_pinned
1 failed, 26 passed
```
(Not `_FROZEN_TABLE`-named per the coordinator's ★-only scoping for that
table; the hash-pin test still catches it — the requirement was inclusion
in `constants_dict()`, not a named row.)

**Round-1 three-value proof, re-run** (`PERSONA_PRESETS["first_timer"]["responsiveness"]`
`0.35->0.36`, `MIN_POPULATION` `30->31`, `CEILING_A_MIN_COVERAGE` `0.80->0.81`):
```
FAILED tests/core/test_frozen.py::test_constants_hash_is_pinned
FAILED tests/core/test_frozen.py::test_frozen_table[CEILING_A_MIN_COVERAGE]
  AssertionError: CEILING_A_MIN_COVERAGE moved: 0.81 != 0.8
FAILED tests/core/test_frozen.py::test_frozen_table[MIN_POPULATION]
  AssertionError: MIN_POPULATION moved: 31 != 30
FAILED tests/core/test_frozen.py::test_frozen_table[PERSONA_PRESETS.first_timer]
  AssertionError: PERSONA_PRESETS.first_timer moved: {'responsiveness': 0.36, ...} != {'responsiveness': 0.35, ...}
4 failed, 23 passed
```
Identical to round 1's reproduction (same four failures, same names).

**M4 add/register/remove cycle** (file-level, not just the in-process test):
added `DUMMY_UNREGISTERED_CONSTANT: int = 999` to `constants_v2.py` ->
`test_every_module_level_constant_is_hashed_or_declared_not_hashed` FAILS
naming it (`module-level constants not registered ...: ['DUMMY_UNREGISTERED_CONSTANT']`)
and the mutant is caught in the full suite (2 failed / 1748 passed, vs 0
failed at baseline); added a `"DUMMY_UNREGISTERED_CONSTANT": ...` line to
`constants_dict()` -> the same test PASSES; both edits reverted, file
restored byte-identical (`diff -q` confirmed).

Each mutation applied via a saved-copy `cp`/restore cycle — never
`git checkout -- <path>` / `git stash` — with `diff -q` confirming
byte-identical restoration after every one.

**200-repo score-dump comparison** (reviewer's own script,
`score_identity.py`, copied verbatim from
`/home/pctablet505/Projects/cvi-scratch/W2-8-review/`): run against a
detached worktree at the round-1 landed commit (`db295bd`, "base") and the
round-2 working tree ("branch"). `diff -q scores_base_round1.json
scores_branch_round2.json` -> **identical** (both 35,049 lines). Confirms
round 2 changed no scored value for any repo — every census addition is a
relocation of an already-in-use value, not a value change.

**`test_frozen.py` old -> new pinned hash**:
```
fb48552cbf13aececaccbb74c83967f7d594211bfad9f7b32f0d8d90945c4c2a
  -> 391d10b91e86a230fa8c16e32f57f2bc5d0449c33dc3bcb6c3f96153ef83fed8
```

**Full suite, final** (branch rebased onto `097142a`):
```
1805 passed, 12 warnings in 71.58s
```
(1743 at the round-1 merge trial + 7 round-2 additions [5 `_FROZEN_TABLE`
rows + 2 M4 tests] = 1750 on the round-2 tree before this final rebase;
main's own commits between `9bb1c22` and `097142a` — W0-4's LLM-curation
deletion, INT-W0-4, several docs-only lead-log entries — added the
remaining 55 independently, none overlapping this packet's files.)

**ruff** `cvi/ scripts/ tests/`: **all checks passed** (the 4 previously
pre-existing `F401`s in `scripts/curate_dataset_llm.py` are moot — W0-4
deleted that file in the commits this round rebased onto).

## Findings not addressed this round (out of the coordinator's explicit scope)

- Review findings 9 ("procedure step 3 is unusable before release"), 10
  (no genesis `CHANGELOG.md` row), 11 (`2.0.0-rc1` divergence from the
  written packet goal / unrecorded lead ruling) were Warnings in the
  round-1 report but were not named in the round-2 dispatch's explicit
  task list (census items 1-5, the M4 guard, M12 documentation, and the
  RFC README premise sentence). Not fixed; flagged here so the lead can
  decide whether they belong in a round 3 or are accepted as-is.
- The review's own UNDECIDED list and its census summary disagree on
  `UNDETERMINED_SPDX` (see the census table above) — flagged, not resolved,
  since it wasn't in the round-2 task list either.

## COULD NOT VERIFY (round 2, in addition to round 1's list)

- Whether `cvi/audit/anchors.py`/`sensitivity.py` will need a code comment
  pointing back at `docs/v2/rfcs/README.md`'s new "Process constants"
  section — not this packet's file to touch (W2-8b/W2-13 territory per the
  work-plan's packet ownership).
