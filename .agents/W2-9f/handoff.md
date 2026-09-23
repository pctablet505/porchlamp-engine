# Handoff — W2-9f (follow-ups from the Opus review of W2-9)

Worktree `/home/pctablet505/Projects/cvi-wt-W2-9f`, branch `W2-9f`, started from main tip
`1fef88ee7413dc0ff3840307a3610c92f857a7db`. Review read:
`/tmp/claude-1000/-home-pctablet505-Projects/2a48e84a-2b94-4a2f-8bff-adbe955a4084/scratchpad/reviews/W2-9-review.md`.

## 0. Lineage correction (item 6)

W2-9's own handoff states "W2-1 landed it at 1,258 lines already" — false; the review's W6 finding
confirmed it (`git show e899583:porchlamp/derive/bundle.py | wc -l` = 566). The real lineage:
**566 (base, W2-1) → 1,010 (after W2-9) → 1,210 (after this packet, W2-9f)**. I did not edit W2-9's
own handoff (outside this packet's ownership, `.agents/W2-9f/` only); this note is the honest
number for my own work, not a correction of that file.

## 1. Diffstat (against the W2-9f branch point)

```
$ git diff --stat
 porchlamp/derive/bundle.py                | 262 ++++++++++++++++++++++++++++----
 tests/core/test_sample200_load.py   |  19 ++-
 tests/derive/test_input_mappings.py | 292 +++++++++++++++++++++++++++++++++---
 3 files changed, 518 insertions(+), 55 deletions(-)
```
`porchlamp/derive/bundle.py`: 1,010 → 1,210 lines (+200, +19.8%). Deviation acknowledged, not split:
two new resolvers (`_resolve_openssf_criticality`, `_resolve_pipeline_defenses`) plus their mirrored
constants account for most of the growth; same one-mapping-concern reasoning W2-9 gave.
`tests/core/test_sample200_load.py` is touched because item 1/3's reason changes are directly
observable on the pinned sample-200 fixture that file loads (not `tests/derive/`, but necessarily
in scope: leaving it red was not an option).

## 2. Item 1 (W1) — dependents_deduped reason consistency

`_resolve_dependents_deduped`'s `confidence == "missing"` branch (a deps.dev/ecosyste.ms absence,
class TP per `docs/v2/02-RANKING-DESIGN.md` §4 row 2.1) now returns `third_party_gap` instead of
`collector_failure` — matching `downloads_monthly`'s equivalent branch. The `confidence ==
"verified"` branch (raw, non-deduped `usedByCount` under the same v1 key — a local mismatch, not a
third-party absence) is unchanged at `collector_failure`.

Fail-before (test written against the code before this line changed):
```
FAILED tests/derive/test_input_mappings.py::test_dependents_deduped - AssertionError: assert 'collector_failure' == 'third_party_gap' ...
FAILED tests/core/test_sample200_load.py::test_unmeasured_inputs_have_documented_reasons - AssertionError: assert 'collector_failure' == 'third_party_gap' ...
```
Both green after the fix. `tests/derive/test_input_mappings.py::test_dependents_deduped`'s Negative
2 and `tests/core/test_sample200_load.py::test_unmeasured_inputs_have_documented_reasons` are the
tests (both branches — absence and local-mismatch — are separately asserted).

## 3. Item 2 (W2) — substantive_review_rate detection power (mutant M12)

New test `test_substantive_review_rate_excludes_internal_reviews_from_n`: 12 internal decided PRs +
4 external decided PRs → `n` must be 4 (external ∧ decided), not 16 (decided alone) → Unmeasured
(insufficient_n), not Measured(0.5, n=16).

M12 kill (drop the `is_external` half of the filter in `_resolve_substantive_review_rate`, restore
via a saved-good copy of `bundle.py` + `find . -name __pycache__ -exec rm -rf {} +` before and
after):
```
FAILED tests/derive/test_input_mappings.py::test_substantive_review_rate_excludes_internal_reviews_from_n
AssertionError: assert Measured(value=0.5, n=16, ...) == Unmeasured(reason='insufficient_n', detail='4 decided external PRs, need >= 10')
M12_KILL_EXIT=1
```
Restored; `md5sum porchlamp/derive/bundle.py` back to the pre-mutation digest
(`11628e975911f3e723db91bfff2fba58`); `tests/derive/` 30/30 green after restore.

## 4. Item 3 (W3) — the two W1-7 rows

Read `scripts/derive.py` (`derive_w17_fields` call site, `Bundle.set`/`Bundle.missing`) and
`porchlamp/collect/pike_signals.py` (`derive_pike_signals`, `pipeline_defenses`, `derive_w17_fields`) on
this tree to find the exact fields:

- **`openssf_criticality`** (2.2, class TP "only if a signal is missing"): reads
  `meta.openssf_signals` (the ten raw Pike signals, `confidence == "derived"` only when all ten
  source envelopes were available). `porchlamp.derive` may import only `porchlamp.core` + stdlib
  (`tests/core/test_import_graph.py`), so `porchlamp.openssf.calculate_pike_criticality` cannot be called
  directly; `_pike_criticality_score` mirrors its weights/thresholds/formula locally (same pattern,
  and the same R-2 tradeoff, as W2-9's own min-n constants). A `meta.openssf_signals` dict missing
  one of the ten fields (defensive: production never emits a partial dict) is the whole-2.2-
  Unmeasured rule, naming the missing signal.
- **`pipeline_defenses`** (4.1, "TP with proxy"): reads the pre-computed `meta.pipeline_defenses` /
  `meta.pipeline_defenses_method` ("primary" or "proxy:review_ci") that
  `porchlamp.collect.pike_signals.derive_w17_fields` already writes. `n` for the proxy method is the
  merged-PR count backing `code_review_rate`'s denominator, read from the pr_window envelope's own
  `prs` list (its provenance `source` is a list of paths, unlike every other row — the new
  `_resolved_source_paths` helper handles it for both new rows). Unlike every other v1 field,
  `scripts/derive.py` never calls `Bundle.missing` for this one — when 4.1 is unmeasurable by
  either method its confidence key is simply absent, not the string `"missing"`; the resolver
  treats both the same way.

Deleted both false `"(W1-7 not yet landed)"` detail strings.

Tests added: `test_openssf_criticality` (Measured, method "primary", n=0 — value cross-checked
against `porchlamp.openssf.calculate_pike_criticality` called directly as an independent oracle, not the
mirrored code under test), `test_openssf_criticality_missing_signal_names_it` (names `'org_count'`),
`test_openssf_criticality_absence_is_third_party_gap`; `test_pipeline_defenses_primary_method`,
`test_pipeline_defenses_proxy_method_counts_merged_prs` (5 recorded PRs, 3 merged → n=3),
`test_pipeline_defenses_absence_is_third_party_gap`.

**Measure re-run on the W2-9 re-derived subset** (copied read-only
`/home/pctablet505/Projects/cvi-scratch/W2-9/measure/` → writable
`/home/pctablet505/Projects/cvi-scratch/W2-9f/measure/`, `chmod -R u+w`, loaded all 199 documents
through the new loader):
```
total docs: 199 load errors: 0
openssf_criticality:  measured=0  third_party_gap=199
pipeline_defenses:    measured=0  third_party_gap=199
```
Both moved from the old constant `collector_failure` (8-of-8 unmapped rows) to the correct, more
informative `third_party_gap` for all 199 — but genuinely 0 Measured on this specific frozen
snapshot: its `derived/*.json` predate the W1-7 wiring being run against this raw copy
(`openssf_signals` confidence is `"missing"` and `pipeline_defenses`'s confidence key is entirely
absent in all 199 source documents, verified before touching any code). Its `raw/` also lacks the
`releases`/`issues_counts` collectors and has `actions_runs` where the hook expects `runs`, so
`openssf_signals` could not have been produced from this snapshot's raw data regardless. Sanity
check (not part of the reported Measure, and reverted immediately after): re-ran
`scripts/derive.py` (unedited, per ownership) against one repo's raw copy
(`0voice/interview_internal_reference`) — `pipeline_defenses` came back `confidence="derived",
value=-2.5, method="primary"` (the scorecard branch fires correctly against this tree's current
code); `openssf_signals` stayed `"missing"` (the raw releases/issues_counts gap is real, not a
resolver defect). The regenerated file was restored from the read-only original immediately after
(`diff -rq` confirmed identical) so the reported 199-document Measure reflects the untouched
subset. COULD NOT VERIFY a nonzero Measured count for either input on this specific subset — the
gap is upstream data availability, not this packet's mapping.

## 5. Item 4 (W4) — validating resolver for enum-valued rows

`ai_posture` moved from `_plain_scalar_resolver` (no vocabulary check) to `_vocab_scalar_resolver`
with an identity vocab (`{v: v for v in constants_v2.AI_POSTURE_VALUES}`): valid values pass through
unchanged, an out-of-set value is `Unmeasured(collector_failure, detail="v1 value ... has no v2
vocabulary mapping")`, never `Measured`. `_vocab_scalar_resolver`'s unused `input_id` parameter
(only ever passed to the old `_map_v1_value` for its problems-list message) was dropped.

New test `test_ai_posture_rejects_an_out_of_set_value`. Mutant kill (revert `ai_posture`'s
`INPUT_MAPPINGS` row to `_plain_scalar_resolver("ai_posture")`):
```
FAILED tests/derive/test_input_mappings.py::test_ai_posture_rejects_an_out_of_set_value
assert Measured(value='NOT_A_POSTURE', n=0, ...) == Unmeasured(reason='collector_failure', detail="v1 value 'NOT_A_POSTURE' for 'ai_posture' has no v2 vocabulary mapping")
```
Restored; digest matched; `tests/derive/` green after restore.

## 6. Item 5 (W5) — row-scoped resolver failures

`_map_v1_value` no longer appends to the bundle-wide `problems` list (the whole-bundle-rejecting
side effect); it now just returns the mapped value or `None`. `_vocab_scalar_resolver`'s existing
`mapped is None` branch already built the correct row-scoped `Unmeasured` detail — the only defect
was the parallel side effect that rejected the whole bundle regardless of what the function
returned. This fixes `legal_ip` and `promotion_ladder` (the only two callers) at once.

`test_legal_ip` / `test_promotion_ladder`'s old Negative-2 assertions (`pytest.raises
(BundleValidationError, ...)`) were rewritten to assert the row-scoped `Unmeasured` instead — that
was the bug being fixed, so the old assertion could not be kept. `test_legal_ip` gained a third
case matching the review's exact scenario (bogus enum **plus** the unresolvable
`raw.githubusercontent.com` URL source that 199 of 199 real sample-200 documents carry for this
field) — the bundle still loads.

Over-correction guard (new test `test_legal_ip_unmappable_enum_over_correction_guard`): a document
with no parseable `owner/name` repo identity, carrying the same unmappable enum, still raises
`BundleValidationError` — the row-scoped fix did not disable whole-bundle rejection generally.

Mutant kill (restore `_map_v1_value`'s old signature/body — append to `problems`, take a `problems`
param again — and its one call site):
```
FAILED tests/derive/test_input_mappings.py::test_legal_ip
FAILED tests/derive/test_input_mappings.py::test_promotion_ladder
porchlamp.core.models.BundleValidationError: v1 value 'ROGUE_RUNG' has no v2 vocabulary mapping
```
Restored; digest matched; `tests/derive/ tests/core/test_sample200_load.py` 38/38 green after
restore.

## 7. Verification (exit codes, final state)

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -B -m pytest tests/derive/ tests/core/test_sample200_load.py -p no:cacheprovider
............................................ [100%]
38 passed in 0.04s
EXIT=0

$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -B -m pytest tests/ -n 4 -p no:cacheprovider
1611 passed, 12 warnings in 54.78s
EXIT=0

$ /home/pctablet505/Projects/contributor-value-index/.venv/bin/ruff check porchlamp/derive/bundle.py tests/derive/ tests/core/test_sample200_load.py
All checks passed!
RUFF_OWNED_EXIT=0

$ /home/pctablet505/Projects/contributor-value-index/.venv/bin/ruff check porchlamp/ scripts/ tests/
Found 4 errors.   (all four in scripts/curate_dataset_llm.py — unowned, pre-existing per the
                   W2-9 review, the file W0-4 deletes)
```

W2-9's own count was 1,551 (full suite) / 31 (packet tests). This packet nets +60 full-suite tests
(1,611 − 1,551) and +7 packet tests (38 − 31): the M12 fixture, the ai_posture invalid-value test,
three openssf_criticality tests, three pipeline_defenses tests, the row-scoped legal_ip/
promotion_ladder rewrites (net +2 assertions inside existing tests, not new test functions), and
the over-correction guard.

## 8. Not touched (ownership boundaries respected)

`scripts/derive.py` (read-only, for field names and the one sanity-check invocation, immediately
reverted), `porchlamp/collect/pike_signals.py` (read-only), `porchlamp/core/*`, other collectors. No sibling
mapping module created (W2-9 didn't create one either; two new resolvers plus mirrored constants
still reads as one coherent v1-to-v2 mapping concern, same reasoning W2-9 gave for not splitting).
