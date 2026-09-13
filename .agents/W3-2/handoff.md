# W3-2 handoff — cross-snapshot detectors and suspicion flags

Worktree `/home/pctablet505/Projects/cvi-wt-W3-2`, branch `W3-2`, base `fba693d`.
Scratch data: `/home/pctablet505/Projects/cvi-scratch/W3-2/` (copy of
`sample-200`, `chmod -R u+w`, never written to under the main checkout).

## Diffstat

```
$ git diff --stat cvi/audit/__init__.py
 cvi/audit/__init__.py | 14 +++++++++++++-
 1 file changed, 13 insertions(+), 1 deletion(-)

$ wc -l cvi/verify/detectors.py cvi/audit/detectors_report.py \
        tests/verify/test_detectors.py tests/audit/test_detectors_report.py
  499 cvi/verify/detectors.py            (new)
  254 cvi/audit/detectors_report.py      (new)
  577 tests/verify/test_detectors.py     (new)
  277 tests/audit/test_detectors_report.py (new)
 1607 total
```

5 files, 4 new, 1 modified. No file outside this packet's ownership touched.

**Size deviation** (R-9, reported not hidden): `cvi/verify/detectors.py` is
499 source lines against the packet's size class M (<= 400, per `00-README.md`'s
precedent table). Matches this project's repeated pattern (W0-3 754 vs M400,
W2-1 1258, W2-3 867 vs L800, W1-7 ...) — subtraction deferred, not a
blocker. Most of the excess is docstring: the module carries three
ownership-request write-ups (`newcomer_burst`, `star_burst`,
`ef_domain_mismatch`) that the lead needs verbatim to dispatch follow-up
packets, plus a per-detector rationale for the unmeasured-never-fires rule
the acceptance criteria require tested and documented.

## Files

- `cvi/verify/detectors.py` (new) — 9 pure `detect_*` predicates (03 section
  6 table), `DetectorInputs`/`NewcomerAccountSample` dataclasses,
  `closed_unread_share_from_pr_window` helper, `run_detectors`.
- `cvi/audit/detectors_report.py` (new) — I/O layer: interim JSON row
  format, `load_rows`, `build_report`, `format_report`, `main` (`python -m
  cvi.audit.detectors_report --current <rows.json> [--previous <rows.json>]
  [--json]`).
- `cvi/audit/__init__.py` (modified) — registered `cvi.audit.detectors_report`
  in `_LAZY_EXPORTS` (the W5-1f pattern for `forward`), docstring updated.
- `tests/verify/test_detectors.py` (new) — 52 tests.
- `tests/audit/test_detectors_report.py` (new) — 12 tests.

## Fail-before proof #1: module does not exist

```
$ python -B -m pytest tests/verify/_fail_before_probe.py -p no:cacheprovider
ImportError while importing test module '.../tests/verify/_fail_before_probe.py'.
E   ModuleNotFoundError: No module named 'cvi.verify.detectors'
=========================== short test summary info ===========================
ERROR tests/verify/_fail_before_probe.py
1 error in 0.04s
```
(probe file created, run, then deleted — not part of the deliverable.)

## Fail-before proof #2: the oracle actually pins the literal threshold

An earlier draft of `test_param_jump_fires_over_40_points_both_n_ok` built
its fixture from the imported constant (`previous_score = current_score -
PARAM_JUMP_THRESHOLD_POINTS`) instead of a bare literal. Setting
`PARAM_JUMP_THRESHOLD_POINTS = 50.0` and rerunning `-k param_jump` still
passed all 7 tests — the oracle had silently become "whatever the constant
says" rather than "the spec's 40". Caught before commit; every oracle test
now uses bare literals (40.0, 90.0, 20.0, 10, 0.30, 5, 7 days, 50, 10.0,
0.5, 4.0) plus a dedicated `test_frozen_thresholds_match_03_section_6` that
pins each constant. Re-running the same mutation now fails correctly:

```
$ sed -i 's/PARAM_JUMP_THRESHOLD_POINTS: float = 40.0/= 50.0/' cvi/verify/detectors.py
$ python -B -m pytest tests/verify/test_detectors.py -p no:cacheprovider -k "param_jump or frozen_thresholds"
FF.......
FAILED tests/verify/test_detectors.py::test_frozen_thresholds_match_03_section_6 - assert 50.0 == 40.0
FAILED tests/verify/test_detectors.py::test_param_jump_fires_over_40_points_both_n_ok - AssertionError: assert False is True
2 failed, 7 passed, 43 deselected in 0.04s
```
Reverted; full packet suite green again (below).

## Packet test set

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest \
    tests/verify/test_detectors.py tests/audit/test_detectors_report.py tests/audit/test_wiring.py \
    -p no:cacheprovider
...................................................................      [100%]
67 passed in 0.05s
```
(67 = 52 detector tests + 12 report tests + 3 pre-existing generic wiring tests; `test_wiring.py`'s pre-existing
generic registry tests pass unmodified against the new entry — 3 of those
67 are the *repository's* wiring tests, not owned by this packet, run here
only to confirm the registration slots in cleanly.)

## ruff

```
$ .venv/bin/ruff check cvi/verify/detectors.py cvi/audit/detectors_report.py \
    cvi/audit/__init__.py tests/verify/test_detectors.py tests/audit/test_detectors_report.py
All checks passed!

$ .venv/bin/ruff check cvi/ scripts/ tests/
Found 4 errors.  (all pre-existing, in scripts/curate_dataset_llm.py — the
file 00-README.md's non-negotiables say W0-4 deletes; unrelated to this
packet, not touched.)
```

## Full suite

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest tests/ -n 4 -p no:cacheprovider
1645 passed, 12 warnings in 42.42s
```
Warnings are pre-existing (fastapi/starlette deprecation notices, a
multiprocessing fork warning in an unrelated adversarial test) — none from
this packet's files.

## Acceptance checklist (05-WORK-PLAN §W3-2 + the dispatch's own list)

- One oracle per detector at its exact threshold, both sides: done for all
  9 (param_jump 40pt/n10, speed_without_decisions 90/40, merges_without_return
  90/20/n10 via both conversion and return, closed_unread 0.30,
  newcomer_burst 5 accounts/7-day half-open window, star_burst bool,
  label_spam 50/10, ef_domain_mismatch 0.5/4, source_drift bool).
- Unmeasured-never-fires: one dedicated test per input each detector reads
  (ParamScore.measured=False/score=None, and every DetectorInputs field
  `None`) — 15+ such tests across the suite, one per (detector, input) pair
  that can be unmeasured.
- `previous=None`: `test_param_jump_no_fire_when_previous_is_none` and
  `test_run_detectors_previous_none_only_same_snapshot_detectors_fire`
  (same-snapshot detectors still fire; the only cross-snapshot one does not).
- Determinism: `test_run_detectors_sorted_and_input_order_independent`
  (sorted output; rebuilding the same facts through a reversed params dict
  and a differently-ordered `DetectorInputs()` call gives the identical
  list).
- Report test on a 6-record fixture: `test_build_report_six_record_fixture_counts`
  and `test_format_report_renders_counts_and_flagged_repos` in
  `tests/audit/test_detectors_report.py` — 6 repos, one clean hit per
  detector family reachable without the ownership-request fields
  (speed_without_decisions, merges_without_return, label_spam,
  ef_domain_mismatch, closed_unread+source_drift together on one repo) plus
  one healthy repo, asserting the exact rendered counts and flagged-repo
  lists.
- Over-correction guard: `test_run_detectors_healthy_record_flags_nothing`
  — a repo with strong-but-plausible, mildly-improving scores and clean raw
  facts triggers no detector.

## Measure: detectors over the sample scored by the landed engine

Ran via a scratch script (`~/Projects/cvi-scratch/W3-2/measure_detectors.py`,
not a deliverable) that loads every `derived/*.json` bundle in the scratch
sample copy through the real `cvi.derive.bundle.load_bundle`, scores each
with the real `cvi.core.engine.score()` (`GateEvidence(manifest_found=None)`
— no W1-9 evidence wiring in this packet's scope, so most identity gates
come back `UNDECIDED` rather than failing; a population stub whose
`population()` always returns `None`, since no real cross-repo
population-building pass exists yet outside the unbuilt v2 snapshot
pipeline — every ECDF-kind input therefore scores
`ParamScore(measured=False, reason="no_population")`, which is the
engine's own documented behaviour for that case, not a fabrication), then
calls `run_detectors(record, record, inputs)` per the packet's Measure
instruction (`previous = current`, so `param_jump` cannot fire).

```
bundles scanned: 199 (load errors: 15)
gate-eligible: 78, rated: 0
detector histogram (previous = current record; param_jump cannot fire):
  param_jump: 0
  speed_without_decisions: 0
  merges_without_return: 0
  closed_unread: 51 -> AtsushiSakai__PythonRobotics, EpicGames__lore,
    EvanLi__Github-Ranking, EvoLinkAI__awesome-gpt-image-2-API-and-Prompts,
    Exrick__xmall, Fincept-Corporation__FinceptTerminal,
    KeygraphHQ__shannon, Kuberwastaken__claurst, LMAX-Exchange__disruptor,
    Masterminds__glide, ... (+41 more; 51/184 loaded bundles = 27.7%)
  newcomer_burst: 0
  star_burst: 0
  label_spam: 0
  ef_domain_mismatch: 0
  source_drift: 0
```

**Say so rather than guess (as the packet asks):** every detector but
`closed_unread` shows zero not because the sample is clean but because its
inputs are unmeasured on this sample copy. Diagnostic (also scratch, not a
deliverable):

```
total loaded: 184, gate-eligible: 78
  ttfhr_hours: raw Measured in 0/184 bundles (collector_failure: 184)
  dr90_decision_rate: raw Measured in 0/184 bundles (collector_failure: 184)
  mr_first_rate: raw Measured in 0/184 bundles (collector_failure: 184)
  newcomer_conversion: raw Measured in 0/184 bundles (collector_failure: 184)
  rr90_return_rate: raw Measured in 0/184 bundles (collector_failure: 184)
  newcomer_issue_supply: raw Measured in 0/184 bundles (collector_failure: 184)
  elephant_factor: raw Measured in 181/184 bundles (76 gate-eligible; 3 collector_failure)
```

So: `speed_without_decisions`, `merges_without_return`, `label_spam` read
inputs that are `Unmeasured(collector_failure)` on every one of the 184
bundles in this old-format `sample-200` copy (`_load_v1` maps 5 of 18
inputs from the pre-W2-9 derived documents; the PR-window-derived rate
parameters are not among them for this particular sample snapshot) — zero
fires is the unmeasured-never-fires rule working as designed, not a clean
bill of health. `ef_domain_mismatch`'s EF half *is* measured (181/184) but
its domain-share half is a named ownership-request field this packet
leaves `None` (see below), so it cannot fire regardless of EF.
`newcomer_burst`, `star_burst`, `source_drift` are zero for the same
ownership-request reason. `closed_unread` is the one detector whose input
(`pr_window`) genuinely is populated in this sample format, and it found
real signal: 51 of 184 repos (27.7%) have >= 30% of their external PRs
closed unmerged with no substantive response ever recorded — spot-checked
by hand on `AtsushiSakai__PythonRobotics` (12/30 = 0.40, `PRWindowEvent`
data inspected directly, matches the function's output exactly).
`gate-eligible: 78, rated: 0` — no repo clears D-EVID-1's 0.60 composite
coverage on this sample given the above collector gaps plus the always-
`no_population` ECDF inputs; not a detector concern (detectors read
`ScoreRecord.params`, populated regardless of `rated`), reported for
context.

## Ownership requests (to the lead / W2-8 / relevant packets)

1. **Wire `run_detectors` into the engine.** `cvi/core/engine.py` does not
   call `run_detectors`; `ScoreRecord.suspicion` is left `[]` by every
   caller today. Per this packet's own instructions, `cvi/core/engine.py`
   is not touched here (R-10). The caller needs a previous-snapshot
   `ScoreRecord` and a `DetectorInputs` built per-repo — natural home is
   wherever W2-8 (or a promote/freeze step) already iterates repos with
   both snapshots in hand.
2. **`newcomer_burst`** needs each sampled newcomer-cohort account's
   *account creation date*. `Cohorts`/`NewcomerAuthor`
   (`cvi/core/models.py`) records first-PR/follow-up PR timestamps but not
   when the GitHub account itself was created. Add
   `account_created_at: str` to `NewcomerAuthor` (04 section 3) and a
   mapped input id in `cvi/derive/bundle.py`'s `INPUT_MAPPINGS` (W2-9's
   table) so a real caller builds `NewcomerAccountSample` tuples from the
   bundle instead of this packet's parallel dataclass.
3. **`star_burst`** needs whether the StarScout lockstep penalty
   (`cvi/starscout.py`, v1 algorithm, D-IN-5) was applied at universe
   entry. No v2 schema field carries this (`RepoIdentity` has no
   stars-related field). Expose it as a stored per-repo boolean from the
   universe-build step (`scripts/build_universe.py`).
4. **`ef_domain_mismatch`** needs the top commit email-domain's share of a
   repo's human commits. `cvi.collect.commits.elephant_factor` (W1-5)
   computes the *count* of orgs reaching 50% of commits but never the top
   org's own share, and no bundle input carries it. Have the W1-5
   collector or its deriver hook additionally compute and store the
   top-domain share alongside `elephant_factor`.
5. **Input-mapping gap for the old sample.** The Measure run above shows
   `ttfhr_hours`, `dr90_decision_rate`, `mr_first_rate`,
   `newcomer_conversion`, `rr90_return_rate`, `newcomer_issue_supply` are
   `Unmeasured(collector_failure)` on all 184 loadable bundles in
   `sample-200`'s current derived documents. This may already be within
   W2-9's known scope ("the v1 loader maps 5 of 18 inputs") or may need a
   fresh sample re-derive once W1-3/W1-4/W1-6 are fully landed and wired —
   flagging so it is not mistaken for a clean-signal finding when someone
   next reads a detector histogram off this sample.

`closed_unread` and `source_drift` are **not** ownership requests:
`closed_unread` is fully computed today from `FeatureBundle.pr_window`
(`closed_unread_share_from_pr_window`); `source_drift` is a plain
pass-through of the W3-3 canary's per-repo hold verdict for whichever
caller has that `LiveSampleReport` in hand.

## COULD NOT VERIFY

- Whether "conversion or return" in `merges_without_return` and
  "conversion" in `label_spam` mean the parameter's 0-100 *score* or a raw
  rate: implemented as **score**, consistent with `speed_without_decisions`'s
  explicit "TTFHR score >= 90" wording in the same table and with all
  threshold magnitudes being round numbers on a 0-100 scale. Documented in
  each function's docstring; no way to confirm against a second source
  since `03-ANTI-MANIPULATION.md` section 6 states the row exactly as
  quoted, without the word "score" on that particular row.
- The exact `closed_unread` denominator (all external PRs in the window
  vs. only those already closed): implemented as **all external PRs in the
  180-day window** (matching the shape of the neighbouring GR_14/D-GATE-5
  "share of external PRs ... with no substantive response" definitions in
  `02-RANKING-DESIGN.md`), tested and documented in
  `closed_unread_share_from_pr_window`'s docstring.
- The `newcomer_burst` "within any 7-day span" boundary: implemented as the
  half-open window `[t, t+7d)` (exactly 7 days apart does not fire),
  tested both sides. No second source to confirm inclusive vs. exclusive.
- Whether `rated: 0` on the Measure sample reflects a real property of
  these 78 gate-eligible repos or an artifact of this packet's own
  no-op population stub and `GateEvidence(manifest_found=None)` choices
  (neither is part of this packet's scope to fix — the real snapshot
  pipeline that builds cross-repo populations and wires W1-9 evidence is
  unbuilt, W4-x "not started"). Reported as context only, not relied on by
  any detector (all 9 read `ScoreRecord.params`, which is populated
  identically whether or not the repo ends up `rated`).
