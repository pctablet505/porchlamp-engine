# Handoff — W2-9 (wire landed collector outputs into FeatureBundle v2)

Worktree `/home/pctablet505/Projects/cvi-wt-W2-9`, branch `W2-9`. Started from `5b88e08` (per the
original dispatch); rebased onto `e899583` (current `main` tip at hand-off time — one docs-only
lead-log commit ahead of the `a0dd646` the dispatch named; `git diff a0dd646 e899583 -- porchlamp/ tests/
scripts/` is empty, so this is the same base by content). Rebase was a trivial fast-forward: `porchlamp/
derive/bundle.py` and `tests/core/test_sample200_load.py` are untouched between `5b88e08` and
`e899583` (the packets that landed in between — HYG-1, W0-3f/g, W2-4/W2-4f, W1-7, W2-5 — touch
different files), so no conflicts.

This continues a session a system restart killed mid-task. Two files were already modified,
uncommitted, from the previous attempt (`porchlamp/derive/bundle.py` rewritten with the declarative
`INPUT_MAPPINGS` table; `tests/core/test_sample200_load.py` with the "gfi" collector added). I
committed that inherited work as-is (`git add -A && git commit`, no edits) before rebasing, to
avoid losing it, then continued on top.

## 0. The "known bug" — already fixed, verified, not re-broken

The dispatch's "known bug to fix first" said `ENVELOPE_HASHES` needed "gfi" reordered before
"github_meta" to match `feature_provenance_hash`'s internal `sorted()`. Reading the inherited file
first (per instruction): the dict literal **already has "gfi" before "github_meta"**, in the
correct alphabetical position — someone in the pre-restart session fixed it before the process was
killed, and it survived the restart (worktree state is on-disk, not in the killed process). I did
not re-order anything.

```
$ python -B -m pytest "tests/core/test_sample200_load.py::test_provenance_hash_matches_independent_recomputation" -p no:cacheprovider -v
tests/core/test_sample200_load.py .                                      [100%]
============================== 1 passed in 0.03s ===============================
```

Full `test_sample200_load.py`: 8/8 passed, independently, before I touched anything.

## 1. Diffstat (against `a0dd646`)

```
$ git diff --stat a0dd646 -- porchlamp/derive/bundle.py tests/core/test_sample200_load.py tests/derive/
 porchlamp/derive/bundle.py                  | 640 ++++++++++++++++++++++++++++------
 tests/core/test_sample200_load.py     |  31 +-
 tests/derive/_fixtures.py             | 126 +++++++
 tests/derive/test_input_mappings.py   | 485 ++++++++++++++++++++++++++
 tests/derive/test_malformed_events.py |  98 ++++++
 5 files changed, 1281 insertions(+), 99 deletions(-)
```

`porchlamp/derive/bundle.py` is now 1,010 lines. **Size deviation** vs whatever class this module was
budgeted at (W2-1 landed it at 1,258 lines already, itself a recorded M-class deviation) — matches
this project's established pattern (every packet touching this file has recorded and deferred a
size deviation rather than split it: W2-1, W0-3, W2-3, W5-1 lead-log rows all say "subtraction
deferred"). I did not split into a sibling module: the packet's own wording made that conditional
("one new sibling module **if** the mapping table is large") and the 18-row table plus its
resolvers reads as one coherent unit (one v1-to-v2 mapping concern), not two.

## 2. A real bug found in the inherited code, fixed, with a mutation-kill proof

While writing the malformed-event oracle test (deliverable 1/2), I found that the inherited
ghost-author-tolerance code had an over-correction defect: it unconditionally folded **every**
`pr_events` entry's raw timestamps into `pr_window.start`/`end` bounds before checking whether that
entry became a valid `PRWindowEvent` — not just the ghost-author ones the W2-9 feature targets. For
a bundle whose *only* event is malformed for an unrelated reason (e.g. missing `pr_number`), this:

1. leaves `events=()` (construction failed) but `start`/`end` non-null (timestamps folded in anyway),
2. which trips `PRWindow.__post_init__`'s "start/end must be null when no events are present"
   invariant and raises **that** `BundleValidationError` first,
3. discarding the actually-informative `problems` entry ("`v1 pr_events[0]: ...`") that would
   otherwise have explained *why* the bundle was rejected.

Fail-before (`tests/derive/test_malformed_events.py::test_other_malformed_event_shapes_still_reject_the_whole_bundle`,
against the inherited pre-fix code, restored via `cp` afterward):

```
E       AssertionError: Regex pattern did not match.
E         Expected regex: 'pr_events\[0\]'
E         Actual message: 'pr_window.start/end must be null when no events are present'
FAILED tests/derive/test_malformed_events.py::test_other_malformed_event_shapes_still_reject_the_whole_bundle
```

Fix: only fold an entry's timestamps into bounds when it (a) became a real `PRWindowEvent`, using
that event's own fields, or (b) was specifically the ghost-author case, using the raw entry's
fields. A genuinely malformed non-ghost entry contributes nothing to bounds — exactly the pre-W2-9
behaviour — so the informative per-event `problems` message survives to the final
`BundleValidationError`.

Mutant re-applied after the fix landed (`else: continue` → `else: bound_source = entry`, i.e. put
the bug back), `find . -name __pycache__ -exec rm -rf {} +` between each step:

```
$ python -B -m pytest tests/derive/test_malformed_events.py::test_other_malformed_event_shapes_still_reject_the_whole_bundle -p no:cacheprovider -v
FAILED ... AssertionError: assert Regex pattern did not match ...
M_GHOST_BOUNDS_RED_EXIT=1
<restore from backup>
$ python -B -m pytest tests/derive/ -p no:cacheprovider -v
============================== 23 passed in 0.06s ===============================
M_GHOST_BOUNDS_RESTORE_GREEN_EXIT=0
$ git diff --stat porchlamp/derive/bundle.py     # unchanged after restore, confirms clean restore
 porchlamp/derive/bundle.py | 31 ++++++++++++++++++++-----------
```

Two more mutants killed while hardening the min-n boundary tests (below), same restore-and-confirm
procedure:

- `_nearest_rank_p90`: `math.ceil` → `math.floor` — `test_ttm_p90_hours` failed
  (`value: 40.0 != 50.0`), restored, suite green.
- `_resolve_ttfhr_hours`: `n < TTFHR_MIN_N` → `n <= TTFHR_MIN_N` — **survived** against the original
  test (which used n=12/n=5, missing the n=10 boundary exactly); added an `n == 10` / `n == 9`
  boundary pair to `test_ttfhr_hours`, re-ran the same mutant, now killed
  (`Unmeasured(insufficient_n) ... != Measured`), restored, suite green. `test_ttm_p90_hours` and
  `test_substantive_review_rate` already hit their own min-n floor exactly in the positive fixture
  (n=5 and n=10 respectively) so no change was needed there.

## 3. Mapping table (`porchlamp/derive/bundle.py::INPUT_MAPPINGS`, unchanged from the inherited design)

| Input id | Reads | When absent |
|---|---|---|
| ttfhr_hours | `meta.pr_window_ttfhr_median_hours` + `.pr_window_external_measured_latency_count` (W1-3) | no pr_window envelope → collector_failure; n<10 → insufficient_n |
| dr90_decision_rate | (none landed) | collector_failure: needs `[now-270d,now-90d]`, outside the 180d window W1-3 collects |
| mr_first_rate | (none landed) | collector_failure: needs W1-4 author history |
| gr14_ghost_rate | (none landed) | collector_failure: needs a snapshot `as_of` the v1-to-v2 loader is never given |
| ttm_p90_hours | raw pr_window envelope: `derived[].{is_external,merged}` joined with `prs[].{createdAt,mergedAt}` by number (W1-3) | no envelope → collector_failure; n<5 → insufficient_n |
| dependents_deduped | `meta.dependents_count` when `confidence=="derived"` (W1-2 hook) | confidence verified/missing → collector_failure (raw usedByCount is not deduped) |
| openssf_criticality | (none landed) | collector_failure: W1-7 not landed |
| downloads_monthly | `meta.monthly_downloads`; `meta.downloads_status` (W1-8) refines the detail | third_party_gap, detail per downloads_status |
| legal_ip | `meta.legal_ip_model`, vocab-mapped | collector_failure |
| elephant_factor | `meta.elephant_factor` (W1-5 override already lands here) | collector_failure |
| promotion_ladder | `meta.promotion_ladder`, vocab-mapped | collector_failure |
| pipeline_defenses | (none landed) | collector_failure: W1-7 not landed |
| ai_posture | `meta.ai_posture` | collector_failure |
| substantive_review_rate | `meta.pr_window_substantive_review_rate` (W1-3); n from the raw envelope | no envelope → collector_failure; n<10 → insufficient_n |
| newcomer_issue_supply | `meta.active_unclaimed_gfis` (W1-1) | collector_failure |
| newcomer_conversion | (none landed) | collector_failure: needs the W1-4 newcomer cohort |
| rr90_return_rate | (none landed) | collector_failure: needs the W1-4 newcomer cohort |
| dx | (none landed) | collector_failure: v1 has CI runtime only, not the setup-signal half |

Malformed-event tolerance: `_pr_event_or_none` returns `None` (not a raised/rejected bundle) for a
`pr_events` entry with empty `author_login` (GitHub's spelling for a deleted/suspended account);
`dropped_event_count(bundle)` (new accessor, not a `FeatureBundle` field — see ownership request 1)
reports how many were dropped from a given bundle.

## 4. Deliverables

**1/2 — `tests/derive/` oracle + malformed-event tests.** `tests/derive/_fixtures.py` (helpers, not
a `conftest.py` — see the import-collision note below), `tests/derive/test_input_mappings.py` (one
test per `INPUT_MAPPINGS` row: 10 rows with a real resolver get a positive Measured fixture plus
1-3 distinct Unmeasured negative fixtures each, exact field-by-field; the 8 rows with no landed
producer get a parametrized "constant regardless of document content" test, proven by loading both
an empty document and one busy with unrelated data for every other row), `tests/derive/
test_malformed_events.py` (ghost-tolerance, `dropped_event_count`, the accessor's zero-default, and
the over-correction guard from §2).

```
$ python -B -m pytest tests/derive/ tests/core/test_sample200_load.py -p no:cacheprovider -v
tests/derive/test_input_mappings.py ...................                  [ 61%]
tests/derive/test_malformed_events.py ....                               [ 74%]
tests/core/test_sample200_load.py ........                               [100%]
============================== 31 passed in 0.04s ===============================
```

Note on `tests/derive/_fixtures.py` (not `conftest.py`): I first wrote the shared helpers as
`conftest.py`, then found empirically that pytest's per-directory conftest loading collides across
this repo's *other* `conftest.py` files when there is no `__init__.py` chain (three exist:
`tests/`, `tests/e2e/`, `tests/derive/`) — `from conftest import x` inside a test resolved to
whichever conftest module pytest happened to import first for the whole run, not the sibling one,
and broke silently only when run alongside `tests/e2e/`. Renamed to the non-conftest, non-`test_`
module `_fixtures.py` (no collision anywhere in the tree, confirmed) and imported normally; both
the full suite and `tests/derive/` + `tests/e2e/` + `tests/core/test_sample200_load.py` together
pass.

**3 — fail-before proofs (4, not just the required 3).** `/home/pctablet505/Projects/cvi-scratch/
W2-9/fail-before/run_proof.py`: dynamically imports `porchlamp/derive/bundle.py` as it existed at
`a0dd646` (`git show a0dd646:porchlamp/derive/bundle.py`) under a distinct module name alongside the
current worktree module, and runs the identical fixture (via the same `tests/derive/_fixtures.py`
helpers) through both.

```
$ python -B run_proof.py
PASS  ttfhr_hours: OLD (a0dd646) is Unmeasured: Unmeasured(reason='collector_failure', detail='v1 deriver does not compute TTFHR over the PR window')
PASS  ttfhr_hours: NEW (worktree) is Measured: Measured(value=6.5, n=12, ...)
PASS  dependents_deduped: OLD (a0dd646) is Unmeasured: Unmeasured(reason='collector_failure', detail='v1 dependents_count is not deduplicated by root organisation (v2 2.1)')
PASS  dependents_deduped: NEW (worktree) is Measured: Measured(value=42, n=0, ...)
PASS  newcomer_issue_supply: OLD (a0dd646) is Unmeasured: Unmeasured(reason='collector_failure', detail='v2 5.1 uses a 90-day window and alias labels; v1 counts 30-day GFI issues')
PASS  newcomer_issue_supply: NEW (worktree) is Measured: Measured(value=7, n=0, ...)
PASS  ghost-event: OLD (a0dd646) rejects the bundle: BundleValidationError: ('v1 pr_events[1]: event.author_login must be a non-empty string',)
PASS  ghost-event: NEW (worktree) loads and drops it: dropped_event_count=1, events=1
8/8 checks passed
EXIT=0
```

(Not committed to the repo: these are one-off demonstration runs, not permanent tests — mirrors
this project's existing convention of committed "over-correction guard" regression tests
(`tests/derive/` above) plus handoff-recorded fail-before proof runs, rather than shipping a
git-internals dependency inside the pytest suite. `tests/derive/test_input_mappings.py`'s 10 real
resolvers each already carry both a Measured and an Unmeasured assertion in the committed suite,
which is the permanent form of the same "before/after" evidence.)

**4 — full suite + ruff.**

```
$ ruff check porchlamp/derive/bundle.py tests/derive/ tests/core/test_sample200_load.py
All checks passed!

$ ruff check porchlamp/ scripts/ tests/
Found 4 errors.   # all 4 in scripts/curate_dataset_llm.py, pre-existing (last touched commit
                  # 578f059, unrelated to this packet); that file is deleted by W0-4 per
                  # docs/v2/00-README.md ("do not run it") — not mine to fix or touch.

$ python -B -m pytest tests/ -n 4 -p no:cacheprovider
1551 passed, 12 warnings in 58.98s
EXIT=0
```

**5 — Measure.** `/home/pctablet505/Projects/cvi-scratch/W2-9/fail-before/measure.py` over a copy
of sample-200 (`~/Projects/cvi-scratch/W2-9/measure/`, `chmod -R u+w`'d; sample-200 itself was never
written to). "BEFORE" uses the same dynamically-imported `a0dd646` module as the fail-before proofs;
"AFTER" is this worktree.

Bundles that load:

| Loader | Loaded | Failed |
|---|---|---|
| BEFORE (v1, `a0dd646`) | 184 of 199 | 15 (all 15: empty `author_login` in `pr_events`, confirmed by inspecting every failure's `BundleValidationError.problems`) |
| AFTER (W2-9) | **199 of 199** | 0 |

This matches the packet dispatch's stated baseline exactly ("184 of 199").

Per-input measured / unmeasured-by-reason, over the bundles that loaded with each loader:

| Input id | BEFORE | AFTER |
|---|---|---|
| ttfhr_hours | unmeasured:collector_failure=184 | unmeasured:collector_failure=199 |
| dr90_decision_rate | unmeasured:collector_failure=184 | unmeasured:collector_failure=199 |
| mr_first_rate | unmeasured:collector_failure=184 | unmeasured:collector_failure=199 |
| gr14_ghost_rate | unmeasured:collector_failure=184 | unmeasured:collector_failure=199 |
| ttm_p90_hours | unmeasured:collector_failure=184 | unmeasured:collector_failure=199 |
| dependents_deduped | unmeasured:collector_failure=184 | unmeasured:collector_failure=199 |
| openssf_criticality | unmeasured:collector_failure=184 | unmeasured:collector_failure=199 |
| downloads_monthly | measured=45, third_party_gap=139 | measured=47, third_party_gap=152 |
| legal_ip | measured=98, collector_failure=86 | measured=110, collector_failure=89 |
| elephant_factor | measured=181, collector_failure=3 | measured=196, collector_failure=3 |
| promotion_ladder | unmeasured:collector_failure=184 | unmeasured:collector_failure=199 |
| pipeline_defenses | unmeasured:collector_failure=184 | unmeasured:collector_failure=199 |
| ai_posture | unmeasured:collector_failure=184 | unmeasured:collector_failure=199 |
| substantive_review_rate | unmeasured:collector_failure=184 | unmeasured:collector_failure=199 |
| newcomer_issue_supply | unmeasured:collector_failure=184 | **measured=198**, collector_failure=1 |
| newcomer_conversion | unmeasured:collector_failure=184 | unmeasured:collector_failure=199 |
| rr90_return_rate | unmeasured:collector_failure=184 | unmeasured:collector_failure=199 |
| dx | unmeasured:collector_failure=184 | unmeasured:collector_failure=199 |

`downloads_monthly`/`legal_ip`/`elephant_factor` improve by count alone because AFTER loads 15 more
bundles (the ghost-event fix), not because their own resolvers changed behaviour on a bundle both
loaders already parsed. `newcomer_issue_supply` is the one row this sample can show a real
before/after delta for: it is entirely unmapped before W2-9 and is measured for essentially the
whole sample after (198/199 — the W1-1 GFI collector already ran for sample-200).

Sample-200 predates W1-2/W1-3/W1-4/W1-6/W1-7 (0 of 199 documents carry `pr_window_count` in
`provenance`, confirmed by grep), so `ttfhr_hours`/`ttm_p90_hours`/`dependents_deduped`/
`substantive_review_rate`/`promotion_ladder` cannot show a Measured count on the sample itself
regardless of loader. **Re-derived subset**: copied the raw `pr_window` envelope for the 5
sample-200 repos that also have one sitting in a landed-collector packet scratch dir (`W1-4/
pr_window/pytest-dev__pytest.json`, `W1-6/raw/pr_window/{numpy__numpy,tensorflow__tensorflow,
torvalds__linux}.json`, `W1-7-live/raw/pr_window/Significant-Gravitas__AutoGPT.json`) into the
measure copy, re-ran `scripts/derive.py`'s `derive_repo(..., fetch=False)` (pure function, no
network I/O — confirmed by reading it before calling) to get a v1 document carrying the real W1-3
summary fields, then loaded that document through both loaders:

| Repo | Input | BEFORE | AFTER |
|---|---|---|---|
| pytest-dev/pytest | ttfhr_hours | Unmeasured(collector_failure) | Measured(value=6.7326, n=270) |
| pytest-dev/pytest | ttm_p90_hours | Unmeasured(collector_failure) | Measured(value=369.5956, n=185) |
| pytest-dev/pytest | substantive_review_rate | Unmeasured(collector_failure) | Measured(value=0.1545, n=330) |
| numpy/numpy | ttfhr_hours | Unmeasured(collector_failure) | Measured(value=2.026, n=138) |
| numpy/numpy | ttm_p90_hours | Unmeasured(collector_failure) | Measured(value=173.7339, n=98) |
| numpy/numpy | substantive_review_rate | Unmeasured(collector_failure) | Measured(value=0.2695, n=167) |
| tensorflow/tensorflow | ttfhr_hours | Unmeasured(collector_failure) | Unmeasured(insufficient_n) |
| tensorflow/tensorflow | ttm_p90_hours | Unmeasured(collector_failure) | Measured(value=96.8133, n=220) |
| tensorflow/tensorflow | substantive_review_rate | Unmeasured(collector_failure) | Measured(value=0.0533, n=225) |
| torvalds/linux | ttfhr_hours | Unmeasured(collector_failure) | Unmeasured(insufficient_n) |
| torvalds/linux | ttm_p90_hours | Unmeasured(collector_failure) | Unmeasured(insufficient_n) |
| torvalds/linux | substantive_review_rate | Unmeasured(collector_failure) | Unmeasured(insufficient_n) |
| Significant-Gravitas/AutoGPT | ttfhr_hours | Unmeasured(collector_failure) | Measured(value=30.8575, n=41) |
| Significant-Gravitas/AutoGPT | ttm_p90_hours | Unmeasured(collector_failure) | Measured(value=196.0094, n=59) |
| Significant-Gravitas/AutoGPT | substantive_review_rate | Unmeasured(collector_failure) | Measured(value=0.4343, n=99) |

torvalds/linux is `count: 0` in its real pr_window envelope (no GitHub PRs — it is mailing-list
based), correctly landing on `insufficient_n` rather than `collector_failure`: the collector ran
and recorded a real zero, distinct from "never ran."

## 5. Ownership requests

1. **Min-n constants duplicated.** `TTFHR_MIN_N`/`SUBSTANTIVE_REVIEW_MIN_N`/`TTM_P90_MIN_N` are
   mirrored (not imported) from `porchlamp.core.params`'s 02 §4 thresholds, because `porchlamp.derive` is
   documented (04 §3) to import only `porchlamp.core.models`. Request: move these three into
   `porchlamp.core.constants_v2` (alongside `PARAM_INPUT_IDS`) so both packets read one definition and a
   future threshold change cannot silently diverge between the scorer and the loader.
2. **`dropped_event_count` is a module-level accessor over a private, unserialized instance
   attribute** (`_w2_9_dropped_event_count`, set via `setattr` since `porchlamp.core.models.FeatureBundle`
   is out of this packet's ownership), not a declared `FeatureBundle` field. It is lost on a
   dump/reload round trip (`dump_bundle` does not serialize it — confirmed, not asserted away).
   Request: promote it to a real `FeatureBundle` field (e.g. `dropped_event_count: int = 0`) in
   `porchlamp/core/models.py` when that module is next open, so it survives the v2 JSON round trip and
   downstream packets (W3-x integrity, audit) don't have to special-case this bundle attribute.
3. **`ai_posture` and `promotion_ladder` record a raw GitHub URL as `provenance[field].source`**
   (`scripts/derive.py`'s `body_src = f"https://raw.githubusercontent.com/{owner}/{name}/HEAD/
   CONTRIBUTING.md"`, used unconditionally for these two fields), never the local
   `raw/bodies/<owner>__<name>__CONTRIBUTING.md.json` cache file the same function already writes
   for that exact fetch. My resolver correctly refuses a URL as unverifiable (00-README's
   non-negotiable: "provenance is an envelope path... a bundle without that is unverifiable"),
   producing `collector_failure` for effectively 100% of the sample regardless of loader — this is
   why `promotion_ladder` measures 0/199 both before and after, and `ai_posture` likewise (the
   pre-existing `test_sample200_load.py` assertion `"not an envelope" in ai_posture.detail`
   independently confirms this is expected, not new). This is a one-line-per-field fix in
   `scripts/derive.py` (pass `src("bodies", f"{owner}__{name}__CONTRIBUTING.md")`-equivalent instead
   of `body_src`), squarely out of this packet's ownership (`scripts/derive.py`'s hook chain is
   explicitly not mine to edit). `legal_ip_model` already does this correctly (`body_src if
   "CONTRIBUTING.md" in paths else src("tree", key)`) — the fix is to make `ai_posture`/
   `promotion_ladder` consistent with it.
4. **`openssf_criticality` and `pipeline_defenses` say "W1-7 not yet landed"** in their `_unmapped`
   detail text, but W1-7 shows `ACCEPT_WITH_FOLLOWUP` in `docs/v2/00-README.md`'s lead log
   (`ed7abb2`) as of this hand-off. I did not wire these two rows: W1-7 landed on `main` after I had
   already rebased and was mid-implementation, wiring it now would mean reading and re-deriving
   against `porchlamp/collect/pike_signals.py`'s actual output shape (out of the time budget for this
   already-large diff), and the packet's file-ownership line does not name that collector. Flagging
   as a live follow-up rather than a silent gap: a next packet (or a W2-9 follow-up, "W2-9f") should
   wire `openssf_criticality`/`pipeline_defenses` from the now-landed W1-7 output and update these
   two detail strings.

## 6. COULD NOT VERIFY / UNDECIDED

None. Every deliverable was run and its output captured above; nothing was blocked.

## 7. Files touched

- `porchlamp/derive/bundle.py` — owned; the malformed-event bugfix in §2 on top of the inherited
  `INPUT_MAPPINGS` rewrite.
- `tests/core/test_sample200_load.py` — owned per packet scope (inherited edit, verified correct).
- `tests/derive/_fixtures.py`, `tests/derive/test_input_mappings.py`,
  `tests/derive/test_malformed_events.py` — new, owned.
- `.agents/W2-9/handoff.md` — this file.
- Nothing under `porchlamp/collect/`, `porchlamp/core/`, `scripts/derive.py`'s hook chain, or the schema
  registry was edited.
