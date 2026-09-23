# Handoff — W2-9g (bounded round: two lead findings on W2-9f)

Branch `W2-9f`, on top of `2a9cd91` (not rebased, per instruction — the lead cherry-picks).

## Diffstat

```
$ git diff --stat 2a9cd91
 porchlamp/core/openssf.py                 | 119 ++++++++++++++++++++++++++++++++
 porchlamp/derive/bundle.py                |  74 +++-----------------
 porchlamp/openssf.py                      |  68 ++++++------------
 tests/derive/test_input_mappings.py | 133 ++++++++++++++++++++++++++++++++++++
 tests/test_openssf.py               |  27 ++++++++
 5 files changed, 308 insertions(+), 113 deletions(-)
```

## Item 1 — TTM_p90 external-only filter (line 716)

Added `test_ttm_p90_hours_excludes_internal_unmerged_and_backwards_time_prs`
(`tests/derive/test_input_mappings.py`): 8 PRs by number — 201 internal+merged with
a ~960h latency, 202 external+derived.merged=False but a real ~524h-apart raw
`mergedAt` (isolates the `merged` flag from the separate raw-mergedAt-null guard;
a null `mergedAt` would be excluded either way and could not kill a mutant that
only drops the `merged` check), 203 external+merged with `mergedAt < createdAt`,
and 204-208 five clean external+merged PRs (latencies 6/12/18/24/30h). Asserts
`Measured(value=30.0, n=5, ...)` — proving n and the p90 come from the 5 clean
PRs alone.

First fixture attempt used a null `mergedAt` for PR 202, which the resolver's
separate `merged is None` guard already excludes regardless of the `merged`
flag — mutant 2 survived against it (39/39 passed). Fixed by giving PR 202 a
real, parseable `mergedAt` so only the `merged`-flag guard excludes it.

Mutant 1 (delete `or not info.get("is_external")`, line 716):
```
FAILED tests/derive/test_input_mappings.py::test_ttm_p90_hours_excludes_internal_unmerged_and_backwards_time_prs
AssertionError: assert Measured(value=960.0, n=8, ...) == Measured(value=30.0, n=5, ...)
```
Mutant 2 (delete `or not info.get("merged")`, against the fixed fixture):
```
FAILED tests/derive/test_input_mappings.py::test_ttm_p90_hours_excludes_internal_unmerged_and_backwards_time_prs
AssertionError: assert Measured(value=524.0, n=6, ...) == Measured(value=30.0, n=5, ...)
```
Restored via `git show 2a9cd91:porchlamp/derive/bundle.py > porchlamp/derive/bundle.py`;
`find . -name __pycache__ -exec rm -rf {} +` before and after each mutant;
`md5sum porchlamp/derive/bundle.py` matched the pre-mutation digest
(`11628e975911f3e723db91bfff2fba58`) after every restore;
`tests/derive/ tests/core/test_sample200_load.py` 39/39 green after each restore.

## Item 2 — Pike criticality: one function

`porchlamp/core/openssf.py` (new, stdlib-only): `SIGNAL_FIELDS` (OpenSSFSignals field
name -> alpha_i/T_i key), `OPENSSF_WEIGHTS`, `OPENSSF_THRESHOLDS`, and
`calculate_pike_criticality(signals: Mapping[str, float], weights=None,
thresholds=None) -> float`. Operates on a plain mapping keyed by the ten
`OpenSSFSignals` field names (`dataclasses.asdict(OpenSSFSignals(...))`'s exact
shape, and exactly the shape a derived document's `meta.openssf_signals` already
has) rather than importing `porchlamp.models.OpenSSFSignals` — `porchlamp.core` may import
nothing outside `porchlamp.core` + stdlib (docs/v2/04-ARCHITECTURE.md section 3;
`tests/core/test_import_graph.py` covers `porchlamp.core.*` generally, non-recursive
glob, so this flat new file is included automatically).

`porchlamp/openssf.py`: now a thin `OpenSSFSignals`-typed wrapper —
`calculate_pike_criticality(signals, weights=None, thresholds=None)` calls
`dataclasses.asdict(signals)` then delegates to `porchlamp.core.openssf.
calculate_pike_criticality`. Re-exports `OPENSSF_WEIGHTS`/`OPENSSF_THRESHOLDS`
from `porchlamp.core.openssf` (same values, same objects now — not a second copy).
`tests/test_openssf.py`'s three original tests pass unchanged (verified before
any other change: `3 passed`).

`porchlamp/derive/bundle.py`: `_resolve_openssf_criticality` now calls
`openssf.calculate_pike_criticality(signals)` (`from porchlamp.core import
constants_v2, models, openssf`) and `openssf.SIGNAL_FIELDS` for its own
ten-field presence/numeric-type validation (a different concern — validating a
possibly-malformed v1 document shape before computing anything — kept in
`bundle.py`, not moved). Deleted: `_OPENSSF_WEIGHTS`, `_OPENSSF_THRESHOLDS`,
`_OPENSSF_SIGNAL_FIELDS`, `_pike_criticality_score` (74 lines net removed from
the file despite the new import and docstring updates).

### Wiring test + an honest correction to the coordinator's stated mutant outcome

Added `test_openssf_criticality_matches_core_openssf_directly`
(`tests/derive/`), parametrized over three signal vectors (all-zero, the
existing mid-sized-repo vector, every signal exactly at its
`OPENSSF_THRESHOLDS` value) — asserts the bundle-derived value equals
`porchlamp.core.openssf.calculate_pike_criticality(signals)` called directly. 3/3
pass.

**Checked, not assumed, the requested mutant** (change one weight in
`porchlamp/core/openssf.py`, `contributor_count` 2.0 -> 3.0): it did **not** kill
`tests/test_openssf.py`'s three existing tests, and it could **not** have
killed the wiring test above either, for a structural reason found while
verifying rather than reported as a surprise:

- `tests/test_openssf.py`'s three tests each isolate one guard branch (the
  `saturation_denom <= 0` floor, the final `max(0.0, ...)` clamp, the
  `denominator == 0` early return) that is, by construction, invariant to the
  exact default weight/threshold *magnitudes* — none of the three asserts a
  positive, non-clamped, non-guard-triggering score against the real defaults.
  `test_zero_sum_weights_guard_returns_zero`'s own comment even cites the
  "8.5/10.5 ~ 0.809524" fact for the all-at-threshold signals it uses, but
  never asserts it.
- The wiring test compares the bundle's value to a **direct call to the same
  function** — by construction this can only ever detect a *wiring* defect
  (bundle.py silently reintroducing its own copy), never an internal-formula
  defect: both sides of the comparison read the identical (possibly mutated)
  module state and so can never disagree with each other on that account.

Fix (not just a report): added one independent-oracle test to **each** file —
`test_openssf_criticality_at_threshold_matches_hand_computed_score`
(`tests/derive/test_input_mappings.py`) and
`test_at_threshold_default_weights_score_matches_hand_computation`
(`tests/test_openssf.py`) — both asserting the all-at-threshold score against
`8.5 / 10.5` computed by hand (no Porchlamp call on that side at all): at every
signal exactly at its threshold, `saturation_denom == value` for every term,
so every normalized `S_i*` is exactly `log(1+T)/log(1+T) = 1.0` regardless of
`T`'s magnitude, and the score collapses to `sum(alpha_i) / sum(|alpha_i|) =
8.5 / 10.5` under the real defaults — a fact independent of any particular
weight's value, so a mutated weight changes the ratio and the assertion.

Mutant re-run with both new tests in place (`contributor_count` weight 2.0 ->
3.0 in `porchlamp/core/openssf.py`):
```
$ pytest tests/test_openssf.py
FAILED tests/test_openssf.py::test_at_threshold_default_weights_score_matches_hand_computation
assert 0.826087 == 0.809524
1 failed, 3 passed in 0.03s

$ pytest tests/derive/test_input_mappings.py -k openssf
FAILED tests/derive/test_input_mappings.py::test_openssf_criticality_at_threshold_matches_hand_computed_score
assert 0.8261 == 0.8095
1 failed, 6 passed, 24 deselected in 0.06s
```
Both suites go red, as the acceptance criterion asked — via the two new
independent-oracle tests, not via the parity/wiring tests (which are
correctly insensitive to this class of defect by design, and always will be).
Restored via the saved-good copy of `porchlamp/core/openssf.py`
(`md5sum` `0241a63abf10123b06d9ebe5defa2659` before and after); `tests/derive/
tests/core/ tests/test_openssf.py` 277/277 green after restore.

### Note for W2-8 (per instruction, not acted on)

`OPENSSF_WEIGHTS`/`OPENSSF_THRESHOLDS` (now canonical in `porchlamp/core/openssf.py`)
are tunables, not frozen v2 ranking constants — they are not covered by
`porchlamp.core.constants_v2.CONSTANTS_HASH`. I did not move them into
`constants_v2.py`; W2-8 owns that file's freeze process right now and should
decide whether/how these join it.

**Residual, out-of-scope duplicate spotted, not touched:** `porchlamp/constants.py`
still carries its own separate `OPENSSF_WEIGHTS`/`OPENSSF_THRESHOLDS` literal
dicts (same values, different objects), imported directly by
`tests/test_openssf.py` (`from porchlamp.constants import OPENSSF_THRESHOLDS,
OPENSSF_WEIGHTS`). `porchlamp/openssf.py` no longer reads from `porchlamp.constants` (it
reads `porchlamp.core.openssf` now), so this is dead weight rather than a live
double-source-of-truth, but it is a second physical copy of the same ten-key
dict a future edit could drift from the canonical one. Left as-is: touching
`porchlamp/constants.py` was not asked for and is a distinct file/decision from
`constants_v2.py`.

## Verification

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../python -B -m pytest tests/derive/ tests/core/ tests/test_openssf.py -p no:cacheprovider
277 passed in 0.29s
EXIT=0

$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../python -B -m pytest tests/ -n 4 -p no:cacheprovider
1617 passed, 12 warnings in 48.82s
EXIT=0
```
(1617 = W2-9f's 1611 + 6: the TTM_p90 mixed-fixture test, the 3-case openssf
parametrized wiring test, the two hand-computed independent-oracle tests.)

```
$ ruff check porchlamp/derive/bundle.py porchlamp/core/openssf.py porchlamp/openssf.py tests/derive/test_input_mappings.py tests/test_openssf.py
All checks passed!

$ ruff check porchlamp/ scripts/ tests/
Found 4 errors.   (all four in scripts/curate_dataset_llm.py — unowned, pre-existing, unchanged from W2-9/W2-9f)
```
