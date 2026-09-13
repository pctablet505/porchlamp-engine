# W3-2f handoff — wire cross-snapshot detectors into `score_snapshot`

Branch `W3-2f`. Dispatch base `c05b2c1`; main moved four times while this
packet was in flight (W1-2f landed and its fixture regenerated, a docs-only
W4-5a lead-log entry, then W2-11 landed and re-pinned the fixture again for
its own reason — `ParamScore` gaining a raw-value field) — rebased three
times, **final base `1f097dc`** ("docs(v2): lead log — W2-11 accepted;
fixture re-pinned"). **Final commit `d264dff`** on `W3-2f` (the original
delivery was `979c8ac` + handoff `e59941c`; `d264dff` is a correction commit
— see "Coordinator correction" below). Worktree
`/home/pctablet505/Projects/cvi-wt-W3-2f`. Scratch
`/home/pctablet505/Projects/cvi-scratch/W3-2f/` (unused — no scratch data
needed).

## Coordinator correction (post-delivery, before this handoff's final form)

The first delivery (`979c8ac`) read the dispatch's "`previous=None` -> all
`suspicion == []`" literally, gating **every** detector off — including the
seven single-snapshot ones (`closed_unread`, `label_spam`,
`speed_without_decisions`, `merges_without_return`, `newcomer_burst`,
`ef_domain_mismatch`, `star_burst`) — whenever `score_snapshot` had no
`previous` snapshot at all. The coordinator relayed a lead ruling: that
line was meant for the *cross-snapshot* detectors only (`param_jump`, and
conceptually `source_drift`, though see below) — a first-ever snapshot
must still carry the single-snapshot flags.

**Fix** (`d264dff`, in `cvi/core/pipeline.py::apply_suspicion`): the gate
is now `if detectors is None: return tuple(records)` — the `previous is
None` half is gone. `previous` is simply forwarded to the `detectors`
callable unchanged; which detectors fire on a first-ever snapshot is
entirely up to the callable, and `cvi.verify.detectors.run_detectors`
already gets this right on its own (only `detect_param_jump` reads
`previous` and short-circuits when it is `None`; the other eight ignore it
and evaluate the current record/inputs regardless) — this fix simply lets
that existing, already-tested behaviour reach `score_snapshot` instead of
being pre-empted by an extra blanket gate above it.

**On `source_drift`**: the ruling's own wording groups it with the
cross-snapshot detectors, but `cvi.verify.detectors.detect_source_drift`'s
actual signature never reads its `previous` argument at all -- it only
reads `inputs.source_drift_held` (the W3-3 live-sample canary's per-run
hold verdict, a fact about *this* run, not a comparison between two scored
records). No special-casing was added for it: `cvi/verify/
pipeline_hook.py`'s adapter already never populates `source_drift_held`
(an ownership-request field, module docstring), so `source_drift` cannot
fire through this composition regardless of `previous` -- vacuously
consistent with the ruling either way, and precisely `tests/verify/
test_pipeline_hook.py::test_previous_none_still_fires_single_snapshot_detectors`'s
fired-set assertion (`{closed_unread, label_spam}`, `source_drift` absent)
already pins this. Flagging in case a future caller sources
`source_drift_held` from something other than this adapter and needs an
explicit ruling on whether it should behave like `param_jump` (needs a
real previous run) or like the other single-snapshot detectors.

Corrected: two tests that encoded the old blanket-suppression rule
(renamed/rewritten to assert the new one), one new real end-to-end test in
`tests/verify/test_pipeline_hook.py`, one new mutant (M5, table below), and
the affected docstrings in `cvi/core/pipeline.py`. `cvi/verify/
pipeline_hook.py` itself needed **no** code change — `build_detectors`'s
`_run` closure already handled `previous=None` correctly (defensive code
written for the "repo absent from `previous`" case, coincidentally exactly
right for "no `previous` at all" too); it had simply never been reached
with `previous=None` before, since the old outer gate intercepted first.

## Diffstat (`1f097dc..d264dff`, both commits combined)

```
 .agents/W3-2f/handoff.md           | 272 +++++++++++++++++++++++++++++++ (new)
 cvi/core/pipeline.py               | 162 +++++++++++++++++--
 cvi/verify/pipeline_hook.py        | 127 +++++++++++++++++ (new)
 tests/core/test_pipeline.py        | 218 +++++++++++++++++++++++++
 tests/verify/test_pipeline_hook.py | 319 +++++++++++++++++++++++++++++++++++++ (new)
 5 files changed, 1088 insertions(+), 10 deletions(-)
```
`git status --short` on the worktree shows exactly these five files (the
handoff document plus the four packet-owned code/test files) — nothing
outside the packet's owned files, and none of the forbidden paths
(`cvi/audit/anchors.py`, `cvi/core/{aggregate,persona,models,params/*}.py`,
`cvi/store/**`, `web/**`/`cvi/serve/**`, `cvi/collect/**`/`cvi/derive/**`)
touched. (`cvi/core/tiebreak.py` was touched only transiently, for the M4
mutation demonstration below, and restored via `git show` before any
commit — it carries no diff in the final commit.)

## The import-graph question, answered

`tests/core/test_import_graph.py::test_core_imports_nothing_outside_core_and_stdlib`
and `::test_importing_core_loads_no_other_cvi_package` both restrict every
`cvi/core/*.py` module (which includes `cvi/core/pipeline.py`) to `cvi.core`
+ stdlib imports only — checked directly against the base commit before
writing anything (`python -c "import ast; ..."` against the test file, then
confirmed by running it). **The lead's preferred plain import
(`cvi.core.pipeline` importing `cvi.verify.detectors.run_detectors`
directly) is not available.** So:

- `cvi/core/pipeline.py::score_snapshot` gained a `detectors:
  DetectorsCallable | None = None` composition seam
  (`Callable[[Sequence[ScoreRecord], ScoredSnapshot | None],
  Mapping[RepoKey, Sequence[str]]]`) plus `previous: ScoredSnapshot | None
  = None`, per the dispatch's own fallback instruction.
- `cvi/verify/pipeline_hook.py` (new, outside `cvi/core`) is the one place
  that builds the real callable: `detector_inputs_from_bundle()` adapts a
  `FeatureBundle` to a `DetectorInputs`, and `build_detectors(bundles)`
  closes over the *current* bundles to return the `(records, previous) ->
  {repo_key: [names]}` closure `score_snapshot` calls.

## What was built

### 1. `cvi/core/pipeline.py`

- `repo_key()` now accepts a `FeatureBundle` **or** a `ScoreRecord` (both
  carry `repo: RepoIdentity`) — one definition per fact (R-2) rather than a
  second `_record_repo_key` restating `f"{x.repo.owner}/{x.repo.name}"`; the
  suspicion-wiring seam needs to key *records*, the existing callers still
  key bundles, and `ScoreRecord.repo` is always copied from the bundle it
  was scored from (`cvi.core.engine.score`), so the two never disagree.
- `DetectorsCallable` (module-level type alias) and `apply_suspicion(records,
  previous, detectors)` (new function, "stage 10"): folds `detectors`'s
  per-repo flag lists onto `ScoreRecord.suspicion` via `dataclasses.replace`
  — **sorted and de-duplicated by this function**, not trusted from the
  callable (mutation table, M1). Returns `records` completely unchanged
  when either `previous is None` or `detectors is None` — a stricter gate
  than any individual detector's own `previous is None` handling (only
  `detect_param_jump` reads `previous` at all; every other detector ignores
  it and would happily fire on the very first snapshot if called directly
  via `run_detectors`). This is a deliberate simplification specific to
  this wiring point, not a restatement of `run_detectors`'s own contract:
  `03` §6 frames the whole apparatus as *cross-snapshot*, and the packet's
  own acceptance line is explicit — "`previous=None` → all `suspicion ==
  []`" — so the very first snapshot ever engages none of it, even the
  detectors that do not themselves need a previous record.
- `score_snapshot(..., previous=None, detectors=None)`: `apply_suspicion`
  runs **after** `assign_ranks` (stage 9) on the already-ranked `records`
  tuple, never before — this is the structural guarantee that suspicion
  cannot leak into `rank`/`tie_key`/`tie_group` (mutation table, M4).
- `SnapshotCounts` gained `suspicion_counts: Mapping[str, int] = {}`
  (default, so the pre-existing `tests/audit/test_anchors_fixture.py`
  construction site — `SnapshotCounts(rated=..., persona_visible={})`,
  no `suspicion_counts` kwarg — keeps working unchanged). Built generically
  in `build_snapshot_counts` from whatever names actually appear across
  `records`' `suspicion` lists — `cvi.core` has no fixed nine-name universe
  to zero-fill from (it cannot import `cvi.verify.detectors.DETECTOR_NAMES`
  either), so an untriggered detector is simply absent from the dict, not
  zero.

### 2. `cvi/verify/pipeline_hook.py` (new)

- `detector_inputs_from_bundle(bundle)`: the one adapter from a real
  `FeatureBundle` to a `DetectorInputs` — reuses
  `cvi.verify.detectors.closed_unread_share_from_pr_window` rather than
  recomputing the closed-unread share a second way. Populates
  `newcomer_issue_count` / `elephant_factor` from `bundle.inputs` when
  `Measured`, and `closed_unread_share`/`closed_unread_n` from
  `bundle.pr_window`. **There was no existing bundle-to-`DetectorInputs`
  adapter to reuse** — I checked: the only other `DetectorInputs`
  construction site, `cvi.audit.detectors_report._row_from_json`, builds
  from an interim JSON row format (`--current`/`--previous` files), not
  from a real `FeatureBundle` — so this is the one definition of it, not a
  duplicate. The four fields no landed collector/canary sources yet
  (`top_email_domain_share`, `newcomer_accounts`,
  `star_lockstep_penalty_applied`, `source_drift_held` — three are W3-2's
  own documented ownership requests to W3-1/W1-5f/W1-10, the fourth is
  W3-3's canary output) are left at their `DetectorInputs` default
  (`None`/`()`), same "not collected" rule the rest of the module already
  documents.
- `build_detectors(bundles)`: the `DetectorsCallable` factory. Builds
  `{repo_key: DetectorInputs}` once from `bundles`, then returns a closure
  matching `previous.records` by `repo_key` (`None` for a repo absent from
  `previous` or when `previous` is `None`) and calling
  `cvi.verify.detectors.run_detectors` per current record.

## Acceptance checklist (against this packet's own dispatch, items 1-4)

1. **`score_snapshot(..., previous=...)`, suspicion via `dataclasses.replace`,
   sorted/deduped, empty when none, silent on missing previous/repo** — done
   (`apply_suspicion`). **`suspicion` participates in `records_hash`** — it
   already did (`_to_plain` walks every dataclass field generically, no
   special-casing); proved directly by
   `test_records_hash_changes_when_a_detector_fires`. **`ScoredSnapshot.counts`
   gains a per-detector histogram** — `SnapshotCounts.suspicion_counts`.
2. **Rank/tie-break never depends on suspicion** —
   `test_score_snapshot_suspicion_never_affects_rank_or_tie_group` (uses the
   `_Fixture12` tie pair `tiea/x`/`tieb/y`, real ranks 1-8, not a trivial
   always-`None` unrated case) proves identical `rank`/`tie_group` across two
   runs differing only in the detector flags supplied.
3. **Tests**: the 6-repo synthetic fixture is in
   `tests/verify/test_pipeline_hook.py` (real bundles, real
   `run_detectors`/`build_detectors`, not a stub) — `param_jump`,
   `closed_unread`, `label_spam` chosen as the three sourceable-input
   detectors (see "Which three detectors, and why" below); `previous=None`
   → all `[]`; a repo absent from `previous` (`closed/repo`, deliberately
   given the same dr90 jump as `jump/repo` so `param_jump` would fire if it
   *had* a previous record) fires only `closed_unread`, never `param_jump`;
   purity (`test_composition_does_not_mutate_bundles_or_previous_snapshot`).
   `tests/core/test_pipeline.py` covers the `apply_suspicion`/`score_snapshot`
   seam itself with hand-written stub callables (identity when
   previous/detectors is `None`, repo-key-not-position wiring, sort/dedup,
   the rank invariance above, the histogram). Mutants: below.
4. **Fixture**: `tests/fixtures/snapshot_50/expected.json`'s `records_hash`
   is **unchanged** — `tests/snapshot/test_reproduce_fixture.py::
   test_reproduce_matches_expected_hash` still passes unmodified (it calls
   `score_snapshot` with the new defaults, `previous=None`/`detectors=None`,
   exactly like every existing caller — `scripts/build_fixture_snapshot.py`
   never passes either). No regeneration needed, and none was done —
   `bundles/`, `manifest.json`, `expected.json`, `populations.json` are all
   untouched (see the diffstat: only the four files above changed).

## Which three detectors, and why (packet's "if its input exists" test)

`star_burst` needs `DetectorInputs.star_lockstep_penalty_applied`, one of
the three named ownership requests `cvi.verify.detectors`'s own module
docstring says no landed schema field carries yet ("No v2 schema field
carries this fact today") — so it **cannot** fire through a real
`FeatureBundle` regardless of content, and is not one of the three. I used
`label_spam` instead (`newcomer_issue_supply` raw value + `newcomer_conversion`
score, both sourceable from a bundle today), alongside `param_jump` and
`closed_unread` as the dispatch suggested. The same reasoning excludes
`newcomer_burst`, `ef_domain_mismatch` and `source_drift` from ever firing
through `cvi/verify/pipeline_hook.py`'s adapter — confirmed empirically:
`test_exactly_three_detectors_fire_on_the_named_repos` asserts the fired-name
set is exactly `{param_jump, closed_unread, label_spam}`.

## Mutation battery (5/5 killed)

All five re-run in full against the *corrected* code (after the coordinator
ruling, commit `d264dff`) — M1-M4 were first proven on the original
delivery (`979c8ac`) and re-proven here since `apply_suspicion`'s body
changed; M5 is new, added for the correction itself. Clean
`find . -name __pycache__ -exec rm -rf {} +` before each. Restored via
`git show <branch HEAD at the time>:<path> > <path>` (the branch's own
committed content — never `git stash`/`git checkout --`), re-verified green
(and `git status --short` empty) after each restore.

| # | Mutant | Where | Result | Killed by |
|---|---|---|---|---|
| M1 | drop `sorted(set(...))`, use plain `list(...)` | `apply_suspicion` | RED | `test_score_snapshot_sorts_and_dedupes_detector_names` |
| M2 | hardcode `detectors(records, None)` regardless of the real `previous` ("previous ignored") | `apply_suspicion` | RED | `test_exactly_three_detectors_fire_on_the_named_repos` (real `build_detectors`; `param_jump` stops firing on `jump/repo` even though a real `previous` was supplied) |
| M3 | positional pairing (`zip(records, suspicion_by_key.values())`) instead of `repo_key` lookup | `apply_suspicion` | RED | `test_score_snapshot_wires_suspicion_by_repo_key_not_position` |
| M4 | apply suspicion **before** `assign_ranks` (`cvi/core/pipeline.py`) **and** fold `entry.record.suspicion` into `tie_key()` ahead of the name tiebreak (`cvi/core/tiebreak.py`) | both files, paired | RED | `test_score_snapshot_suspicion_never_affects_rank_or_tie_group` |
| M5 | reinstate the old, incorrect gate: `if previous is None or detectors is None: return tuple(records)` | `apply_suspicion` | RED | `test_score_snapshot_previous_none_still_applies_single_snapshot_flags`, `test_apply_suspicion_is_identity_only_when_detectors_is_none`, `test_previous_none_still_fires_single_snapshot_detectors` (all three, independently) |

M2's mutant changed shape from the first delivery: the original code's
top-level gate had a `previous is None` clause to mutate directly (dropping
it was M2 then); the corrected code has no such clause left to drop, so M2
now targets the actual forwarding of `previous` into the `detectors` call
instead — the same underlying hazard ("previous ignored"), reached the only
way still possible once the fix landed.

M4 needed touching two files together: `apply_suspicion` running strictly
after `assign_ranks` in the real code means `tie_key()` never sees a
populated `suspicion` at all, so mutating `tie_key()` alone (leaving the
call order intact) does **not** reproduce a leak — confirmed by trying that
first and getting green (not documented as a numbered mutant since it
never went red; recorded here so the next reviewer does not repeat it).
The real, paired mutant — pre-rank suspicion application *and* a `tie_key()`
that reads it — flips `tiea/x`'s rank from 4 to 5 (pasted below), which is
exactly the invariant `test_score_snapshot_suspicion_never_affects_rank_or_tie_group`
exists to catch. `tiebreak.py` is not in this packet's forbidden-file list
(checked against the dispatch); the edit was reverted with `git show` like
every other mutant, and `git status --short` was empty afterward.

M5 is the direct regression guard for the coordinator's correction: it is
byte-for-byte the bug the first delivery shipped, and it is killed three
independent ways (a stub-level identity test, a stub-level end-to-end
`score_snapshot` test, and a real-bundle `build_detectors` test) — the same
defect could not have survived through any of the three call paths this
packet exercises.

Failures pasted verbatim (each reproduced by re-applying the one-line edit
described in the table, clearing `__pycache__` first):

M1:
```
    assert by_key["zzz/second"].suspicion == ["closed_unread", "star_burst"]
E   AssertionError: assert ['star_burst'...losed_unread'] == ['closed_unre... 'star_burst']
E   At index 0 diff: 'star_burst' != 'closed_unread'
E   Left contains one more item: 'closed_unread'
```

M2:
```
    assert by_key[KEY_PARAM_JUMP].suspicion == ["param_jump"]
E   AssertionError: assert [] == ['param_jump']
E   Right contains one more item: 'param_jump'
```

M3:
```
    assert by_key["aaa/first"].suspicion == ["star_burst"]
E   AssertionError: assert ['label_spam'] == ['star_burst']
E   At index 0 diff: 'label_spam' != 'star_burst'
```

M4:
```
    for key in baseline_by_key:
>       assert detectored_by_key[key].rank == baseline_by_key[key].rank, key
E       AssertionError: tiea/x
E       assert 5 == 4
```

M5 (all three killers shown):
```
# test_score_snapshot_previous_none_still_applies_single_snapshot_flags
    assert all(record.suspicion == ["closed_unread"] for record in fired.records)
E   assert False

# test_apply_suspicion_is_identity_only_when_detectors_is_none
    assert all(record.suspicion == ["param_jump"] for record in result)
E   assert False

# test_previous_none_still_fires_single_snapshot_detectors
    assert by_key[KEY_CLOSED_UNREAD].suspicion == ["closed_unread"]
E   AssertionError: assert [] == ['closed_unread']
E   Right contains one more item: 'closed_unread'
```

## Suite / ruff (final, on `d264dff`, rebased onto `1f097dc`)

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest -n 4 -p no:cacheprovider
2066 passed, 1 skipped, 12 warnings in 12.55s
```

Zero failures. The two `tests/audit/test_anchors_fixture.py` failures the
dispatch flagged as pre-existing ("known on main right now and being fixed
elsewhere") were already gone by the first rebase (main's `f4f8c7f`,
"chore(fixtures): regenerate snapshot_50 after W1-2f", landed the fix
concurrently with this packet) and stayed gone through the later two
rebases. Nothing in this packet touched `cvi/audit/anchors.py` or the
fixture. `tests/snapshot/test_reproduce_fixture.py::
test_reproduce_matches_expected_hash` and `tests/core/test_import_graph.py`
(4/4) both still pass on the final base, confirmed separately after the
last rebase.

```
$ .venv/bin/python -m ruff check .
All checks passed!
```

`tests/core/test_import_graph.py` (4/4) and
`tests/snapshot/test_reproduce_fixture.py` (3 passed, 1 skipped — the
skipped one is that file's own by-hand constant-mutation demonstration,
unrelated to this packet) both pass.

## COULD NOT VERIFY / open items

- `DetectorInputs.top_email_domain_share`, `.newcomer_accounts`,
  `.star_lockstep_penalty_applied` remain unsourced — same three ownership
  requests W3-2's own handoff already raised (owners: W1-5f, W3-1, W1-10).
  `detector_inputs_from_bundle` cannot populate them until those land;
  `star_burst`, `newcomer_burst`, `ef_domain_mismatch` stay permanently
  silent through this composition until then (not a defect of this
  packet — a direct consequence of the missing inputs).
- `source_drift_held` is the W3-3 canary's own per-run output, not a bundle
  fact; `build_detectors` leaves it `None` (never fires) since nothing in
  this packet's scope has that report in hand. Whichever caller eventually
  wires W3-3's `LiveSampleReport` into a real snapshot run will need to
  extend `detector_inputs_from_bundle` (or pass a second per-repo map into
  `build_detectors`) to populate it — not attempted here, out of scope.
- No real CLI/script calls `score_snapshot(..., previous=..., detectors=
  build_detectors(...))` yet — this packet wires the seam and its
  composition, per the dispatch's file ownership, but does not add a
  `cvi.cli` snapshot subcommand or cron job that threads a real previous
  snapshot through. Flagging for whichever packet owns the real snapshot
  driver (likely wave W4/W5).
