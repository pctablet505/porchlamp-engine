# W3-2f handoff — wire cross-snapshot detectors into `score_snapshot`

Branch `W3-2f`. Dispatch base `c05b2c1`; main moved three times while this
packet was in flight (W1-2f landed, the `snapshot_50` fixture was
regenerated for it, then a docs-only lead-log entry on W4-5a) — rebased
twice, **final base `ee69900`** ("docs(v2): lead log — owner UX verdict on
W4-5a; rendering architecture ruling (06 §6 amended)" — docs-only, no
conflict). Commit **`b207eae`** on `W3-2f` (handoff itself is `3c569d1`).
Worktree `/home/pctablet505/Projects/cvi-wt-W3-2f`. Scratch
`/home/pctablet505/Projects/cvi-scratch/W3-2f/` (unused — no scratch data
needed).

## Diffstat (`ee69900..b207eae`)

```
 cvi/core/pipeline.py               | 153 ++++++++++++++++++--
 cvi/verify/pipeline_hook.py        | 127 +++++++++++++++++ (new)
 tests/core/test_pipeline.py        | 201 ++++++++++++++++++++++++++
 tests/verify/test_pipeline_hook.py | 281 +++++++++++++++++++++++++++++++++++++ (new)
 4 files changed, 752 insertions(+), 10 deletions(-)
```
`git status --short` on the worktree shows exactly these four files —
nothing outside the packet's owned files, and none of the forbidden paths
(`cvi/audit/anchors.py`, `cvi/core/{aggregate,persona,models,params/*}.py`,
`cvi/store/**`, `web/**`/`cvi/serve/**`, `cvi/collect/**`/`cvi/derive/**`)
touched.

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

## Mutation battery (4/4 killed)

Run before either rebase, against the branch's first commit (content
identical after both rebases — neither touched these files, no conflicts).
Clean `find . -name __pycache__ -exec rm -rf {} +` before each. Restored
via `git show <that commit>:<path> > <path>` (the branch's own committed
content — never `git stash`/`git checkout --`), re-verified green (and
`git status --short` empty) after each restore.

| # | Mutant | Where | Result before fix | Killed by |
|---|---|---|---|---|
| M1 | drop `sorted(set(...))`, use plain `list(...)` | `apply_suspicion` | RED | `test_score_snapshot_sorts_and_dedupes_detector_names` |
| M2 | drop the `previous is None` half of the gate (`if detectors is None:` only) | `apply_suspicion` | RED | `test_score_snapshot_previous_none_leaves_all_suspicion_empty` |
| M3 | positional pairing (`zip(records, suspicion_by_key.values())`) instead of `repo_key` lookup | `apply_suspicion` | RED | `test_score_snapshot_wires_suspicion_by_repo_key_not_position` |
| M4 | apply suspicion **before** `assign_ranks` (`cvi/core/pipeline.py`) **and** fold `entry.record.suspicion` into `tie_key()` ahead of the name tiebreak (`cvi/core/tiebreak.py`) | both files, paired | RED | `test_score_snapshot_suspicion_never_affects_rank_or_tie_group` |

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
    assert all(record.suspicion == [] for record in snapshot.records)
E   assert False
E    +  where False = all(<generator object ...>)
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

## Suite / ruff (final, on `b207eae`, rebased onto `ee69900`)

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest -n 4 -p no:cacheprovider
2038 passed, 1 skipped, 12 warnings in 9.24s
```

Zero failures — the two `tests/audit/test_anchors_fixture.py` failures the
dispatch flagged as pre-existing ("known on main right now and being fixed
elsewhere") are **gone** after the rebase: main's `f4f8c7f`
("chore(fixtures): regenerate snapshot_50 after W1-2f") landed the fix
concurrently with this packet. Nothing in this packet touched
`cvi/audit/anchors.py` or the fixture.

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
