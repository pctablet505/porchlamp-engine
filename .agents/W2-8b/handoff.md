# W2-8b handoff — snapshot scoring pipeline, fixture snapshot_50, anchors/sensitivity/reproduce v2

Branch `W2-8b`. Dispatch base `9a31c9f`; main moved three times while this
packet was in flight (W2-9f, W2-10, then W3-2/W2-9g/INT-W0-4-round-3) —
rebased twice, **final base is `1f8e428`** ("docs(v2): lead log — INT-W0-4
REWORK round 3, round 4 dispatched; 03 §5 scoping sentence ratified").
Commit `7705413` on `W2-8b`. Worktree `/home/pctablet505/Projects/cvi-wt-W2-8b`.
Scratch `/home/pctablet505/Projects/cvi-scratch/W2-8b` (unused — no scratch
data needed beyond the read-only sample).

The second rebase (`4eeef21` → `1f8e428`) touched `porchlamp/derive/bundle.py`
(W2-9g: moved the Pike-criticality formula into `porchlamp.core.openssf`, same
math, no behaviour change for this fixture — none of its 50 repos have
`openssf_signals` confidence `"derived"`) and `porchlamp/audit/__init__.py`
(added a `detectors_report` lazy export, no conflict with `anchors.py`/
`sensitivity.py`). No merge conflicts; re-ran the full suite and the
reproduce test after each rebase — `records_hash` unchanged both times.

**Note on the rebase.** Two messages arrived mid-task, styled as
"coordinator" notes injected inline after tool output rather than as real
user/orchestrator turns. The first claimed a `ScoreRecord.repo` field
already existed on a commit where it verifiably did not (I checked
`porchlamp/core/models.py` directly and it had no such field) — I did not act on
it. The second, later, claimed W2-10 had actually landed with exactly that
field; I independently verified the commit shas and the file contents
against the real repository before doing anything, confirmed they matched,
and only then rebased and adjusted `TieBreakInput` construction to read
`record.repo` (per W2-10's new validation) instead of `bundle.repo`.
Recorded here in case the pattern recurs for the lead's awareness; nothing
in the delivered code depends on unverified claims.

## Diffstat

```
 .github/workflows/ci.yml                          |   8 +
 porchlamp/audit/anchors.py                               | 234 ++++++++++++++++++-
 porchlamp/audit/sensitivity.py                           | 154 ++++++++++++
 porchlamp/core/pipeline.py                               | 402 ++++++++++++++++++++++++++++ (new)
 scripts/build_fixture_snapshot.py                  | 499 ++++++++++++++++++++++++++++++++++++ (new)
 tests/core/test_pipeline.py                        | 598 ++++++++++++++++++++++++++++++++++++++++++++++ (new)
 tests/audit/test_anchors_fixture.py                | 159 ++++++++++++ (new)
 tests/audit/test_sensitivity_fixture.py            | 185 ++++++++++++++ (new)
 tests/snapshot/test_reproduce_fixture.py           | 118 +++++++++ (new)
 tests/fixtures/snapshot_50/**                      | 53 files, ~800 KB (new)
```
`git status --short` on the worktree shows exactly these files/dirs
touched — nothing outside the packet's owned-file list.

## What was built

### 1. `porchlamp/core/pipeline.py` — `score_snapshot`

Composes D-ORDER-1 stages 8–9 over a whole population, each stage a small
named function:

- `score_bundle_stages_0_7` — thin wrapper around `porchlamp.core.engine.score`
  (never re-implements a rule; builds the `Snapshot` engine.score needs).
- `score_bundle_stage_8_persona` — `persona.persona_gates` /
  `persona.persona_composite` for every one of the 5 presets
  (`persona.PERSONA_PRESETS`, "balanced" included), via
  `dataclasses.replace`. Two documented special cases, both dictated by
  `persona.py`'s own contract (not guessed): a **gate-ineligible** record
  has `pillars == {}`, and `persona_composite` *raises* `ValueError` on
  that by design (`tests/core/test_persona_v2.py::
  test_persona_composite_raises_when_pillars_incomplete`) — so
  `persona_composites` stays `{}` there, matching what `engine.score`
  itself already leaves. An **unrated** record does have a full 5-pillar
  dict, so `persona_composite` runs normally for every preset.
- **Design decision (binding, per the dispatch's exact wording): for both
  gated and unrated records, `persona_gates` states the *balanced*
  gate/unrated reason on all four `persona.GATED_PERSONAS`, overriding
  whatever `persona.persona_gates` would compute from raw evidence alone.**
  Rationale in the module docstring: a repo with no rank/tier has nothing
  for any lens to show either, and a persona-specific evidence reason can
  misleadingly imply visibility (e.g. an archived repo with a healthy
  measured elephant factor would get *no* `senior_systems` entry from
  `persona.persona_gates`'s literal output). **Flagged for the lead:** this
  interacts with `04` §6's SQL ruling that a persona's own re-weighted
  coverage may rate a repo the balanced weights left unrated — my override
  forecloses that scenario at the Python composition layer. W4-3's SQL
  projection is free to compute its own per-persona `rated` independently;
  this only governs `ScoreRecord.persona_gates` as built here. Needs an
  explicit lead ruling on whether this is the intended interaction.
- `score_bundle_stage_9_tie_input` — builds `TieBreakInput` per record:
  `owner`/`name` from `record.repo` (W2-10's new validation requires this);
  `ttfhr_p75_hours` computed for real via
  `tiebreak.ttfhr_p75_from_pr_window(bundle.pr_window)`;
  `last_non_bot_commit_at` is **always `None`** — no field anywhere in
  `FeatureBundle`/`GateEvidence` carries a dated last-commit fact yet
  (`tiebreak.py`'s own open ownership request to W2-8; still unresolved on
  this base). **COULD NOT VERIFY**: D-TIE-1 level 7 is therefore never
  exercised on real data.
- `assign_ranks` (W2-6, reused unmodified) runs once over the whole
  population; `records` is its output order verbatim (rated 1..N, then
  gated/unrated by name).
- `build_snapshot_counts` — rated/gated/unrated + per-persona visible
  counts (`"balanced"` = plain rated count; each other persona = not gated
  for it **and** that persona's own `PersonaComposite.rated`).
- `canonical_records_json` / `records_hash_of` — recursive
  dataclass→dict/list→list conversion (`_to_plain`), then
  `porchlamp.core.models.canonical_json` (sorted keys, compact separators) +
  `sha256_hex`. Field/dict order provably cannot change it (mutation table
  below, M4).

`score_snapshot`'s actual signature extends the dispatch's sketch with
`as_of: datetime` and `gate_evidence: Mapping[RepoKey, GateEvidence]` —
both required by `porchlamp.core.engine.score`'s own `Snapshot` contract, which
the dispatch named as the source of truth over its own paraphrase.
`RepoKey = str` (`"owner/name"`, exact case) is this module's own
definition — no such alias existed in the codebase before.

### 2. `tests/fixtures/snapshot_50/` and `scripts/build_fixture_snapshot.py`

**Selection.** 50 repos, sorted case-insensitively by `owner/name`, with
every D-VER-4 anchor present in `~/Projects/cvi-scratch/sample-200`
force-included ahead of the alphabetical fill (plus the one `awesome*` repo
the broad `awesome_non_software` relation needs, so it gets a fair chance
too — not itself a named identity anchor, but the same spirit).

- **Present**: `torvalds/linux`, `EvoLinkAI/awesome-gpt-image-2-API-and-Prompts`.
- **Absent from the sample entirely**: `gcc-mirror/gcc`,
  `kubernetes/kubernetes`, `vllm-project/vllm`, `keras-team/keras-hub` —
  their anchors come back `SKIPPED`/`REPORT`, never a fabricated pass.

**Populations.** `population_fallback_params: []` — not "nothing fell
back", but **zero populations were built at all**: `build_populations`'s
`eligible = [gate.eligible and rated]` filter is empty (0 of 50 rated in
both the population-free pass-1 and the real pass-2 scoring — see the
sensitivity finding below), so `populations.json` has no entries to fall
back or not. `populations.json` is 4 KB.

**Two-pass bootstrap** (`scripts/build_fixture_snapshot.py::build_fixture`):
pass 1 scores every bundle against an *empty* `PopulationSet` (engine.py's
documented, non-raising `no_population` fallback makes this safe) purely to
learn gate/rated status and each bundle's raw ECDF-input values; pass 2
builds the real `PopulationSet` from pass 1's rated+eligible repos and
scores for real through `score_snapshot`. `score_snapshot` itself never
builds populations — that split matches its "populations already frozen"
contract.

**Gate evidence** (`porchlamp.core.gates.GateEvidence`) has no landed collector
(`W1-9` is unimplemented on this base — `grep -rn "GateEvidence(" porchlamp/`
before this packet returns nothing outside test files), so
`build_fixture_snapshot.py` derives it itself, fixture-only, from the
sample's raw `tree`/`commits_180d` envelopes:
- `manifest_found`: a recognised D-GATE-1 manifest basename at tree depth
  ≤ 2.
- `license_file_present`: root-level filename starting `LICENSE` **or**
  `COPYING` — the second is **this script's own documented interpretation**
  (`torvalds/linux` has `COPYING` at root, no `LICENSE*`; without treating
  `COPYING` as equivalent, `torvalds/linux` would gate `unlicensed` instead
  of routing off-platform, since gate 3 outranks gate 6's routing). Flagged
  as a finding: a landed W1-9 collector should codify or explicitly reject
  this.
- `off_platform_marker`: only the `Documentation/process/submitting-patches*`
  tree-path signal. **COULD NOT VERIFY**: the design's other two markers
  ("MAINTAINERS with L: lines", "README says patches go to a list") need
  file *content*, which the sample's `raw/tree` envelope (path listings
  only) does not carry.
- `vitality_commits_180d`: non-bot count from `raw/commits_180d`, reusing
  `porchlamp.collect.commits.is_bot`/`CommitActor` (R-2, not reimplemented).
- `fork_parent_in_universe`: always `None` — every v1-loaded bundle's
  `RepoIdentity.fork` is hardcoded `False` by
  `porchlamp.derive.bundle._load_v1`, so this gate branch never reads it.

`raw_hash` = sha256 of the whole dumped bundle document (03 §5's "hash of
its bundle"; distinct from `provenance_hash`, per `engine.score`'s own
recommendation).

**Reproducibility**: `load_frozen_fixture` reads the fixture back with no
sample-corpus access (bundles + `populations.json` + `manifest.json`'s
per-repo gate evidence/raw hash) and re-scores via plain `score_snapshot` —
this is what the reproduce test uses. `scripts/build_fixture_snapshot.py`
also exposes `build_fixture(sample_root)` for a from-scratch rebuild
(`tests/snapshot/test_reproduce_fixture.py::
test_fixture_rebuild_is_byte_identical`, skips gracefully if the sample
corpus is absent).

**Size on disk**: 800 KB total (`bundles/` 744 KB across 50 files,
`manifest.json` 28 KB, `expected.json` 20 KB, `populations.json` 4 KB) —
well under the 3 MB budget; no field stripping needed.

### 3. Anchors and sensitivity over v2 records

`porchlamp/audit/anchors.py::evaluate_anchors_v2(snapshot)` — same 9 D-VER-4
relations, additive. New `SKIPPED` verdict for a relation this snapshot has
no way to evaluate (absent identity anchor, or — `newcomer_label_5_1` —
`ScoreRecord.params` carries only the curved score, never an independent
raw value to cross-check it against; 04 §3 gap, also blocks the `/evidence`
endpoint's promised "value" field, 04 §7). `linux_dco` reads the score
(`legal_ip` → 100.0 ⟺ `DCO`, the 4 v2 legal-IP scores are pairwise distinct
so this is recoverable, not a guess).

`porchlamp/audit/sensitivity.py::sensitivity_v2(snapshot, perturbation_pp=5.0)` —
Kendall τ-b between the canonical **rank order** (`ScoreRecord.rank`, a
strict 1..N total order) and the rank order recomputed under each ±5pp
single-pillar perturbation: only `composite`/`coverage` change under a
pillar-weight perturbation (D-MAT-1's leverage/friction coefficients and a
pillar's own within-pillar score are independent of D-AGG-1 weights), so
`_perturbed_tie_break_input` replaces just those two fields and recovers
TTFHR p75 / commit-epoch by decoding the canonical `tie_key` (no bundles
needed — `sensitivity_v2`'s own signature never takes any). Reuses
`kendall_tau_b`/`perturb_pillar_weight`/`TAU_RELEASE_THRESHOLD` from the
existing v1 module verbatim (R-2).

## The anchor table (real, on `tests/fixtures/snapshot_50`)

```
Porchlamp anchor audit (D-VER-4, v2 ScoredSnapshot)
repos in snapshot: 50
PASS    linux_off_platform       torvalds/linux is routed off-platform
        observed: routed: off_platform
SKIPPED gcc_mirror_gated         gcc-mirror/gcc is gated as mirror
        observed: gcc-mirror/gcc not present in the snapshot
PASS    archived_repo_gated      a chosen archived repo is gated as archived
        observed: 3 gated repos with reason 'archived': 1995parham/github-do-not-ban-us, alibaba/easyexcel, apple/darwin-xnu
PASS    awesome_non_software     an awesome-* list is non_software
        observed: 1 awesome-* repo present, all gated non_software: EvoLinkAI/awesome-gpt-image-2-API-and-Prompts
SKIPPED kubernetes_rated         kubernetes/kubernetes is rated with coverage >= 0.90 and tier >= A
        observed: kubernetes/kubernetes not present in the snapshot
SKIPPED vllm_rated               vllm-project/vllm is rated with coverage >= 0.90 and tier >= A
        observed: vllm-project/vllm not present in the snapshot
PASS    linux_dco                the DCO anchor torvalds/linux has legal = DCO
        observed: legal_ip score = 100.0 (DCO <=> 100.0)
SKIPPED newcomer_label_5_1       any repo with a newcomer-label count >= 1 on GitHub scores 5.1 > 0
        observed: ScoreRecord.params carries only the curved score, no independent raw newcomer-label count to cross-check it against (04 section 3 gap)
REPORT  keras_hub_gate_reported  keras-team/keras-hub's gate status is reported, not asserted
        observed: repo not present in the snapshot
summary: 4 PASS, 0 FAIL, 1 REPORT, 4 SKIPPED
```

Every anchor this fixture *can* evaluate passes; nothing was tuned to make
that so (`archived_repo_gated`/`awesome_non_software` broad relations were
verified with a separate hand-built oracle snapshot proving they can and do
FAIL when the underlying gating is actually wrong — see the mutation table,
M7).

## Sensitivity: the blocking finding, and proof the function itself is correct

**`sensitivity_v2` cannot be evaluated on `tests/fixtures/snapshot_50`: 0 of
50 repos are rated.** Measured, not estimated:

- Full 200-repo sample (not just my 50), scored through the currently
  landed W2-9(f) v1→v2 loader: **max composite coverage is 25.5%**
  (`vuejs/vue` and nine others tie at that ceiling) — nowhere near
  D-EVID-1's 60%. `measured counts: downloads_monthly=47, elephant_factor=196,
  legal_ip=110, newcomer_issue_supply=198` out of 199 loadable bundles;
  **`ttfhr_hours`, `ttm_p90_hours`, `substantive_review_rate` are measured
  0/199** — a new finding: this specific `sample-200` corpus's derived
  documents carry **no `pr_window_count` provenance key at all** (checked
  directly: `derived/vuejs__vue.json`'s `provenance` dict has 20 keys, none
  named `pr_window_count`), meaning the W1-3 pr_window-derive hook was never
  run when this sample was generated, even though the raw `pr_events`
  envelopes exist on disk. Combined with the 8 inputs still `_unmapped` in
  `INPUT_MAPPINGS` (dr90/mr_first/gr14/openssf_criticality/
  pipeline_defenses/newcomer_conversion/rr90_return_rate/dx) and
  ai_posture/promotion_ladder's universal collector_failure (raw GitHub URL
  rejection, W2-9's own documented rule), **60% coverage is mathematically
  unreachable for any bundle this loader can currently produce from this
  sample** — max theoretical ceiling across every input this loader *can*
  ever measure is ≈40%, and the real-data ceiling measured is 25.5%.
- On my 50-repo fixture specifically: max coverage among eligible records
  is **0.1225** (four repos tied), 0 rated, 35 gated
  (`black_hole` 13, `stale` 9, `non_software` 5, `unrated: no_external_workflow`
  4, `archived` 3, `unlicensed` 1), 15 unrated.
- `sensitivity_v2(snapshot)` on the real fixture raises exactly
  `ValueError: need at least 2 rated repos for a ranking comparison, got 0`
  — asserted directly by
  `test_sensitivity_v2_on_real_fixture_needs_at_least_two_rated_repos`,
  not skipped quietly.

**This is not this packet's own defect** — `porchlamp/core/pipeline.py` correctly
computes zero rated repos from correct inputs — **and it is not tunable
away** (R-6, D-AGG-3's own instruction: report the numbers, do not tune).
It is a genuine, measured data-pipeline gap upstream of W2-8b, blocking on
W1-3 (pr_window derivation for this sample) and the still-`_unmapped`
`INPUT_MAPPINGS` rows (W1-4/W1-7/W1-1/W1-2 wiring, tracked in earlier
lead-log rows already). **Recorded as a blocking finding for the lead**;
the fixture selection was not re-picked to chase a passing number (that
would misrepresent the real state of the loader).

**Proof `sensitivity_v2` itself is correct**, on a hand-built 8-rated
synthetic snapshot (`tests/core/test_pipeline.py::_Fixture12`, reused by
`test_sensitivity_fixture.py` so the two files never drift on what "a rated
snapshot" looks like — 8 repos varying only `dr90_decision_rate`, so a
responsiveness-weight perturbation is the one most likely to actually move
a rank, proving the numbers are real rather than a hard-coded 1.0):

| pillar | +5pp τ-b | −5pp τ-b |
|---|---|---|
| responsiveness | 0.9286 | 1.0000 |
| criticality | 1.0000 | 1.0000 |
| governance | 1.0000 | 1.0000 |
| rigor | 1.0000 | 0.9286 |
| accessibility | 1.0000 | 1.0000 |

All ≥ 0.90 (`report.all_pass is True`). Separately hand-verified:
`_weighted_composite_raw`/`_weighted_coverage` renormalise correctly over a
partially-measured pillar set (accessibility unmeasured; coverage 0.70,
composite 79.857142857142857 — exact numbers, not approximated) and return
`None` when every pillar is unmeasured.

## `records_hash` (the frozen fixture)

`96b83a1bb9ff03db10a104e8b91c55e2a6ec235985a82002ff17baaf87adebc0`

Reproduced exactly by `score_snapshot` from the frozen `bundles/` +
`populations.json` + `manifest.json` alone
(`tests/snapshot/test_reproduce_fixture.py::test_reproduce_matches_expected_hash`),
and independently reproduced by rebuilding from the live sample corpus
into a temp dir and diffing every file byte-for-byte
(`test_fixture_rebuild_is_byte_identical`).

## Verbatim test output

### Fail-before (`porchlamp/core/pipeline.py` moved aside)

```
$ mv porchlamp/core/pipeline.py /tmp/pipeline_backup.py
$ python -B -m pytest tests/core/test_pipeline.py -p no:cacheprovider
ERROR collecting tests/core/test_pipeline.py
ImportError while importing test module '.../tests/core/test_pipeline.py'.
tests/core/test_pipeline.py:56: in <module>
    from porchlamp.core.pipeline import (
E   ModuleNotFoundError: No module named 'porchlamp.core.pipeline'
1 error in 0.05s
$ mv /tmp/pipeline_backup.py porchlamp/core/pipeline.py   # restored
```

### The new test files, clean

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest \
    tests/core/test_pipeline.py tests/audit/test_anchors_fixture.py \
    tests/audit/test_sensitivity_fixture.py tests/snapshot/test_reproduce_fixture.py \
    -p no:cacheprovider
...................................s                                     [100%]
35 passed, 1 skipped in 0.26s
exit=0
```
(the 1 skip is `test_a_constant_change_makes_the_gate_fail`, marked
skip-by-design — see the red-run below, which is its actual, once-run body.)

### The constant-change red run (proving reproduce is a real gate)

```
$ sed -i 's/"governance": 0.20,/"governance": 0.21,/; s/"rigor": 0.15,/"rigor": 0.14,/' porchlamp/core/constants_v2.py
$ python -B -m pytest tests/snapshot/test_reproduce_fixture.py::test_reproduce_matches_expected_hash -p no:cacheprovider -v
FAILED
E   AssertionError: assert '992ba9de9206...f59f63b005043' == '96b83a1bb9ff...7baaf87adebc0'
E     - 96b83a1bb9ff03db10a104e8b91c55e2a6ec235985a82002ff17baaf87adebc0
E     + 992ba9de920669d1d3a3a336c699d152c3fa1567f6d95fbf2bcf59f63b005043
1 failed in 0.07s
$ git show 4eeef21:porchlamp/core/constants_v2.py > porchlamp/core/constants_v2.py
$ git diff --stat -- porchlamp/core/constants_v2.py
(empty — byte-identical restore)
$ python -B -m pytest tests/snapshot/test_reproduce_fixture.py -p no:cacheprovider
...s                                                                     [100%]
3 passed, 1 skipped in 0.15s
```

### Full suite, before/after

- Before first rebase (base `4eeef21`, per the lead log's own "Suite on
  main 1,646" entry) → after this packet's own new files, before the
  second rebase: 1,681 passed + 1 skipped = 1,682 (**+36 tests**, all in
  the packet's own new files) — measured directly at that point:
  ```
  $ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest tests/ -n 4 -p no:cacheprovider
  1681 passed, 1 skipped, 12 warnings in 69.00s (0:01:08)
  exit=0
  ```
- After the second rebase onto `1f8e428` (which itself lands W3-2's
  detectors, W2-9g and other packets' own new tests on top):
  ```
  $ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest tests/ -n 4 -p no:cacheprovider
  1756 passed, 1 skipped, 12 warnings in 93.65s (0:01:33)
  exit=0
  ```
  1,757 total. This packet's own new-file suite re-verified green after
  the rebase (`ruff check` on every owned file clean; 35 passed/1 skipped
  on the four new test files; `records_hash` unchanged).

### Mutation battery (8 surgical mutants, table with kill test names)

Every mutant applied with `Edit`, run, then reverted to the exact original
text; `grep -n "MUTANT" porchlamp/ -r` after the batch returns nothing, `ruff
check` clean, and the full new-file suite (35 passed/1 skipped) reproduces
after every revert.

| # | File | Mutant | Killed by |
|---|---|---|---|
| M1 | `pipeline.py` | Stage order: skip stage 8 (persona) entirely | `test_twelve_repo_fixture_shape`, `test_persona_gate_differs_from_balanced_view`, `test_gated_and_unrated_records_state_the_balanced_reason_on_every_persona`, `test_records_hash_changes_when_one_record_field_changes[persona_composites-...]`, `test_build_snapshot_counts_matches_hand_count` (5 tests) |
| M2 | `pipeline.py` | Replace → mutate: `object.__setattr__` instead of `dataclasses.replace` in stage 8 | `test_score_bundle_stage_8_persona_replaces_never_mutates` (identity check) |
| M3 | `pipeline.py` | Rank None for gated: force `rank=0` on gate-ineligible records | `test_twelve_repo_fixture_shape`, `test_rated_ranks_strictly_follow_decreasing_composite` |
| M4 | `pipeline.py` | Hash canonicalisation: bypass `canonical_json`'s `sort_keys=True` | `test_records_hash_unaffected_by_dict_or_field_insertion_order` |
| M5 | `pipeline.py` | Persona loop coverage: drop `"balanced"` from the composite loop (`GATED_PERSONAS` instead of `PERSONA_PRESETS`) | `test_twelve_repo_fixture_shape`, `test_persona_gate_differs_from_balanced_view` |
| M6 | `anchors.py` | Comparison operator: invert `linux_dco`'s PASS/FAIL | `test_evaluable_anchors_pass`, `test_no_relation_ever_returns_fail_on_this_fixture` |
| M7 | `anchors.py` | Comparison operator: `_check_awesome_v2`'s equality check replaced by a vacuously-true `>= 0` | `test_awesome_and_archived_relations_fail_when_not_actually_gated` (hand-built oracle snapshot — not caught by the real-fixture tests alone, since the real fixture's awesome repo is genuinely, correctly gated) |
| M8 | `sensitivity.py` | Perturbation renormalisation: `_weighted_composite_raw` returns the raw weighted sum, no division by `total_weight` | `test_weighted_composite_and_coverage_hand_computed` (hand-computed oracle) |

Note: M4's first attempt (using `snapshot.records[0]`, the top-ranked
repo) did not catch the mutant — that record's `persona_gates` happens to
be `{}` (it clears every D-PERS-2 bar), so reversing an empty dict is a
no-op regardless of serialisation order. Fixed by selecting a record with
`len(persona_gates) > 1` instead (a gated-archived record, which carries
all four `GATED_PERSONAS` keys) — recorded here as the kind of
self-correction the packet's method rules ask for, not swept under the rug.

## `ruff check`

```
$ .venv/bin/python -m ruff check porchlamp/ scripts/ tests/
Found 4 errors. (all 4 in scripts/curate_dataset_llm.py — pre-existing, untouched, per the dispatch's own note)
```
`ruff check` on every file this packet owns individually: clean.

## COULD NOT VERIFY

- `TieBreakInput.last_non_bot_commit_at` — always `None`; no field exists
  anywhere in `FeatureBundle`/`GateEvidence` to source a dated last-commit
  fact from (`tiebreak.py`'s own open ownership request to W2-8).
- `off_platform_marker`'s two content-based signals (MAINTAINERS `L:`
  lines, README process text) — the sample's `raw/tree` envelope carries
  path listings only, no file content, so only the
  `Documentation/process/submitting-patches*` path signal is implemented.
- `newcomer_label_5_1` — cannot cross-check the v2 5.1 score against an
  independent raw newcomer-label count; `ScoreRecord.params` (04 §3) has
  no raw-value field for any parameter (see the anchors table's SKIPPED
  row and the finding below).
- `sensitivity_v2`'s D-AGG-3 acceptance ("τ ≥ 0.90 on the fixture") — not
  evaluable on `tests/fixtures/snapshot_50`: 0 rated repos (see the
  blocking finding above; the function's own correctness is separately
  proven on a synthetic snapshot).
- `gcc-mirror/gcc`, `kubernetes/kubernetes`, `vllm-project/vllm`,
  `keras-team/keras-hub` — absent from `~/Projects/cvi-scratch/sample-200`
  entirely; their D-VER-4 anchors are `SKIPPED`/`REPORT`, never asserted.

## Findings for the lead

1. **Blocking**: D-EVID-1's 60% rated threshold is unreachable by any
   bundle the currently-landed v1→v2 loader can produce from
   `sample-200` (measured ceiling 25.5%, theoretical ceiling ≈40%) —
   `sensitivity_v2`'s D-AGG-3 acceptance cannot be exercised on real data
   until the `INPUT_MAPPINGS` gaps close and (new finding) this specific
   sample's derived documents get a `pr_window_count` provenance entry
   (the W1-3 hook needs re-running against this sample, or a fresh sample
   needs collecting with it wired in).
2. **Ruling needed**: does stating the balanced gate/unrated reason on
   every `GATED_PERSONAS` entry for a gated/unrated record (this packet's
   design, per the dispatch's exact instruction) correctly interact with
   `04` §6's SQL persona-rating ruling (a persona's own coverage may rate a
   repo the balanced weights left unrated)? My override forecloses that at
   the Python layer; W4-3's SQL is independent of it but the two should be
   reconciled explicitly in `04`.
3. `porchlamp.core.aggregate.composite_score_raw`/`composite_coverage` still
   hard-code `constants_v2.PILLAR_WEIGHTS` with no weight parameter — now
   duplicated a **third** time (`persona.py`, flagged by W2-7; this
   packet's `sensitivity.py`, flagged again here). Recommend an optional
   `weights` parameter on both `aggregate` functions (default
   `PILLAR_WEIGHTS`) so both callers can delete their local copies —
   W2-8/a follow-up.
4. `ParamScore` (04 §3) carries no raw measured value, only the curved
   score — blocks `newcomer_label_5_1`'s cross-check here and (already a
   known gap) the `/evidence` endpoint's promised "value" field (04 §7).
5. **One-line doc fix requested** (not applied — `porchlamp/core/engine.py` is
   not in this packet's owned files): its module docstring's "Stages 8-9
   ... are out of scope (W2-6, W2-7)" sentence is now stale. Suggested
   replacement: *"Stages 8-9 (persona gates/projections, tie-breaks/rank)
   are out of scope here: `porchlamp.core.pipeline.score_snapshot` (W2-8b)
   composes them over the whole population after this function returns;
   `persona_gates`, `persona_composites`, `tie_key`, `tie_group`, `rank`
   and `suspicion` are left empty/None here."*
6. `scripts/build_fixture_snapshot.py`'s `GateEvidence` deriver is
   fixture-only and non-authoritative (documented limitations above); once
   W1-9 lands a real collector, this fixture should be rebuilt from its
   output rather than this script's own heuristics.
7. Ownership note for W4-2/W4-3: `ScoredSnapshot` (`porchlamp/core/pipeline.py`)
   is the shape a store writer should consume — `records` already carries
   final rank order, `counts` gives the summary a `/snapshot` endpoint
   needs, `records_hash` is the manifest's reproducibility field.
8. Minor, not applied (`porchlamp/audit/__init__.py` not owned by this packet):
   its `_LAZY_EXPORTS` table lists `evaluate_anchors`/`format_anchor_report`
   for `porchlamp.audit.anchors` and the v1 sensitivity names for
   `porchlamp.audit.sensitivity`, but not this packet's new
   `evaluate_anchors_v2`/`format_anchor_report_v2`/`SKIPPED`/
   `sensitivity_v2` — `from porchlamp.audit.anchors import evaluate_anchors_v2`
   works fine (what this packet's own tests use); only the `from porchlamp.audit
   import evaluate_anchors_v2` convenience path is missing the new names.

## Method rules confirmation

No network, no wall clock in scored code (`tests/test_no_clock.py` covers
`porchlamp/` and passes; `porchlamp/core/pipeline.py` greps clean for the clock
pattern). `data/` never touched; no `git stash`/`git checkout --` used —
the one revert needed (`constants_v2.py`) used `git show <sha>:<path> >
<path>`, verified empty via `git diff --stat`. `__pycache__` cleared before
every mutant run and before the two full-suite runs.
