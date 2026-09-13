# W2-13 handoff — D-MISS-5 coverage exclusion, not-applicable rule list, criticality proxy

- Base: `8a4846c` (local `main` tip; W2-12 landed, RFC-0001 landed via W1-4 round 3.5)
- Branch: `W2-13`, worktree `~/Projects/cvi-wt-W2-13`
- Commits: `8433fb7` (implementation), `f95ff99` (RFC-0002 + changelog + fixture re-pin)
- Final HEAD: `f95ff994935844ac5847e51d7cd604c10f294f08`

## Diffstat (`8a4846c..HEAD`)

```
 cvi/collect/pike_signals.py                        | 141 +++++++++----
 cvi/core/aggregate.py                              |  62 ++++--
 cvi/core/constants_v2.py                           |  32 +++
 cvi/core/engine.py                                 |  43 +++-
 cvi/core/models.py                                 |  12 +-
 cvi/core/openssf.py                                |  26 ++-
 cvi/derive/bundle.py                               | 134 ++++++++++--
 docs/v2/rfcs/CHANGELOG.md                          |   1 +
 docs/v2/rfcs/RFC-0002-not-applicable-rules-and-criticality-proxy.md | 108 ++++++++++
 tests/collect/test_w17_pike.py                     |  92 ++++++---
 tests/core/test_aggregate.py                       |  91 +++++++++
 tests/core/test_engine_v2.py                       |  34 ++++
 tests/core/test_frozen.py                          |  19 +-
 tests/derive/test_input_mappings.py                | 225 +++++++++++++++++++++
 tests/fixtures/snapshot_50/expected.json           |   2 +-
 tests/fixtures/snapshot_50/manifest.json           |   2 +-
 tests/fixtures/snapshot_50/populations.json        |   2 +-
 17 files changed, 913 insertions(+), 113 deletions(-)
```

## RFC numbering note

The packet dispatch guessed `RFC-0003` (anticipating RFC-0001 from W1-4 and
RFC-0002 from W1-10 both landing first). At dispatch time neither had
landed on the worktree's base (`1f097dc`); by the time of this handoff,
only RFC-0001 (W1-4, `AUTHOR_UNMEASURED_MAX_FRACTION`) had landed on `main`
(W1-10 is still in flight). Checked the next free number on `main` per the
packet's own instruction and used **RFC-0002**, not RFC-0003.

Also: `origin/main` is a stale, unrelated older lineage (147 commits behind
local `main`, itself an ancestor of local `main` — not a divergent branch).
Rebased onto local `main`, not `origin/main`.

## 1. Constants

`cvi/core/constants_v2.py` gains three constants (all registered in
`constants_dict()`, rows added to `_FROZEN_TABLE`):

- `NOT_APPLICABLE_RULES: tuple[tuple[str, str, str], ...] = (("dependents_deduped", "ecosystem", "systems"),)`
- `OPENSSF_PROXY_MIN_SIGNALS: int = 8`
- `WINDOW_STALENESS_DAYS: int = 14` (relocated from `cvi/collect/pike_signals.py`, imported back there as `constants_v2.WINDOW_STALENESS_DAYS`)

RFC: `docs/v2/rfcs/RFC-0002-not-applicable-rules-and-criticality-proxy.md`.
Changelog: `docs/v2/rfcs/CHANGELOG.md` new top row.

`CONSTANTS_HASH`: `d2702ea93a7ad9eb22696942dd3994d63f066ff2bf2e68e52abfc8235433570e`
(RFC-0001's value, this packet's rebase base) ->
`9673c16a46d3aebf371cfc91ad1546c93bd47db535550996249c6c10145f444a`.
`test_every_module_level_constant_is_hashed_or_declared_not_hashed` passes
(no new unregistered module-level constant).

## 2. Rule enforcement

- `cvi/derive/bundle.py::_resolve_dependents_deduped` now loops
  `constants_v2.NOT_APPLICABLE_RULES` (matching on `rule_param ==
  "dependents_deduped"` and `fact_name == "ecosystem"`) instead of an
  inline `if ecosystem == "systems"`. Same detail text, same behaviour —
  verified: every existing `test_dependents_deduped_*` test in
  `tests/derive/test_input_mappings.py` passes unchanged.
- `cvi/core/engine.py`: `_score_params` now downgrades any
  `Unmeasured(not_applicable)` bundle input whose `(parameter, fact,
  value)` does not match a `NOT_APPLICABLE_RULES` row to
  `Unmeasured(collector_failure, detail="not_applicable claimed outside
  the rule list")`, via `_not_applicable_is_permitted` +
  `_BUNDLE_FACT_RESOLVERS` (currently one resolver: `"ecosystem" ->
  bundle.repo.ecosystem`). Both directions tested:
  `test_not_applicable_permitted_by_rule_list_passes_through`,
  `test_not_applicable_outside_rule_list_is_downgraded_to_collector_failure`
  (covers both "wrong fact value" and "wrong parameter" ways to be outside
  the list).

## 3. Coverage (D-MISS-5)

`cvi/core/aggregate.py::aggregate_pillars`: `not_applicable` parameters are
now excluded from BOTH sides of pillar coverage first
(`_is_not_applicable`), then the existing D-MISS-2 renormalisation runs
over the applicable subset. `PillarScore` (`cvi/core/models.py`) gains
`applicable: bool = True` — `False` only when a pillar's entire applicable
weight is 0 (every parameter in it not_applicable).
`composite_coverage` sums `W_p * c_p` only over pillars with
`applicable=True`, divided by the sum of THEIR weights (renormalised, same
pattern as D-MISS-2) — with every pillar applicable (the default, and
every existing fixture) this denominator is 1.0 and the arithmetic is
byte-identical to before.

Oracles (`tests/core/test_aggregate.py`):
- `test_composite_coverage_systems_repo_end_to_end_reaches_one`: `C = 1.00`
  (was 0.90, `< 0.95`) for a hypothetical systems repo with everything else
  measured.
- `test_composite_coverage_third_party_gap_end_to_end_unchanged`: `C = 0.90`
  unchanged for a pypi repo with a deps.dev gap.
- `test_composite_coverage_excludes_wholly_not_applicable_pillar_from_denominator`:
  one whole pillar not_applicable -> `C` over the remaining four,
  renormalised.
- `test_composite_coverage_all_pillars_not_applicable_is_zero`: defensive
  floor.

Every pre-existing coverage test in `test_aggregate.py` passes unchanged
(none of their fixtures use `not_applicable`).

## 4. Criticality proxy

- `cvi/collect/pike_signals.py::derive_pike_signals`: no longer hard-requires
  `dep["deduped_count"]`. Returns `tuple[dict[str, int|float], frozenset[str]]
  | None` (the nine-or-ten present signals, keyed by `OpenSSFSignals` field
  name, plus which are missing) instead of `Optional[OpenSSFSignals]`; still
  `None` when any of `gh`/`c365`/`rel`/`iss` is unmeasurable (unchanged hard
  requirements). `derive_w17_fields` reconstructs `OpenSSFSignals(**signals)`
  under the existing `openssf_signals` key ONLY when `missing` is empty (v1's
  `asdict()`/`scripts/promote.py`'s `OpenSSFSignals(**sig)` round trip is
  therefore completely unaffected); when only dependents is missing, two new
  additive fields carry the partial set: `openssf_signals_partial` (the nine
  present signals) and `openssf_signals_partial_missing` (`["dependents_count"]`).
  `openssf_signals` itself stays at v1's pre-existing "missing" placeholder in
  that case — verified end to end in
  `test_derive_repo_missing_dependents_leaves_openssf_missing`.
- `cvi/core/openssf.py::calculate_pike_criticality` gains a keyword-only
  `missing: Optional[frozenset[str]] = None` that excludes named alpha_i/T_i
  keys from both the numerator and denominator sums (renormalising over
  present signals); `None`/empty is the pre-W2-13 behaviour, byte-identical.
- `cvi/derive/bundle.py::_resolve_openssf_criticality`: when the full
  ten-signal set was never emitted, tries `_resolve_openssf_criticality_proxy`
  before falling to the generic `third_party_gap` gap. That helper reuses
  `_resolve_dependents_deduped(ctx)`'s own resolution (one source for "why is
  the dependents signal unusable"): `collector_failure` ->
  `Unmeasured(collector_failure)` (a retry can still measure it);
  `not_applicable`/`third_party_gap` with `>= OPENSSF_PROXY_MIN_SIGNALS` (8)
  present -> `Measured(method="proxy:no_dependents_signal")`, provenance
  `derived`; fewer than 8 -> `Unmeasured(insufficient_n, detail="k of 10
  signals")`.

Tests (`tests/derive/test_input_mappings.py`, `tests/collect/test_w17_pike.py`):
- `test_openssf_criticality_proxy_when_dependents_not_applicable` /
  `..._third_party_gap`: both qualifying reasons get the proxy.
- `test_openssf_criticality_proxy_hand_computed_oracle`: independent oracle —
  every present signal at its threshold collapses each `S_i*` to 1.0, so the
  renormalised score is `sum(alpha_i excl. dependents) / sum(|alpha_i| excl.
  dependents) = 6.5 / 8.5 = 0.7647` (compare with the existing 8.5/10.5 = 0.8095
  all-ten oracle in the same file).
- `test_openssf_criticality_proxy_dependents_collector_failure_stays_unmeasured`:
  the third branch.
- `test_openssf_criticality_proxy_insufficient_signals`: a hand-built 7-of-10
  case -> `insufficient_n, "7 of 10 signals"`.
- `test_openssf_criticality_proxy_not_reached_when_dependents_measured`:
  over-correction guard (a hand-built doc with both a measured
  `dependents_count` AND a partial companion falls through, not shortcut).
- `test_openssf_criticality_pike_signals_and_openssf_containers_agree`: feeds
  `cvi.collect.pike_signals.derive_pike_signals`' real output through
  `cvi.core.openssf.calculate_pike_criticality(..., missing=...)` and confirms
  it matches the bundle-derived proxy value end to end ("both containers
  agree").
- `tests/collect/test_w17_pike.py`: `derive_pike_signals` return-shape tests
  updated (dict + `frozenset`, not `OpenSSFSignals` attribute access);
  `test_derive_pike_signals_missing_dependents_is_a_nine_signal_partial`
  (new); `test_derive_repo_missing_dependents_leaves_openssf_missing` updated
  to assert both v1's unaffected `openssf_signals=None` AND the new partial
  fields.

## 5. Mutants (all red as expected, restored via `git show HEAD:<path> > <path>`)

1. **not_applicable kept in the coverage denominator**
   (`aggregate.py`: `applicable_entries` includes not_applicable) — 4 tests
   failed: `test_pillar_not_applicable_parameter_excluded_from_both_sides`,
   `test_pillar_wholly_not_applicable_is_itself_not_applicable`,
   `test_composite_coverage_systems_repo_end_to_end_reaches_one`,
   `test_composite_coverage_excludes_wholly_not_applicable_pillar_from_denominator`.
2. **the rule list ignored** (`engine.py::_not_applicable_is_permitted`
   hardcoded `return True`) — 1 failed:
   `test_not_applicable_outside_rule_list_is_downgraded_to_collector_failure`
   (`dependents_deduped` case; `AssertionError: 'not_applicable' ==
   'collector_failure'`).
3. **the rule key compared on the wrong fact**
   (`engine.py`: compared `fact_name == expected_value` instead of
   `resolver(bundle) == expected_value`) — 1 failed:
   `test_not_applicable_permitted_by_rule_list_passes_through`
   (`AssertionError: 'collector_failure' == 'not_applicable'`).
4. **`OPENSSF_PROXY_MIN_SIGNALS` 8 -> 7** — 2 failed:
   `test_constants_hash_is_pinned` (hash `6d822d50...` != pinned
   `9673c16a...`) and `test_frozen_table[OPENSSF_PROXY_MIN_SIGNALS]`
   (`7 != 8`). Confirms the freeze catches it.
5. **the `>=` flipped** (`bundle.py`: `present_count >
   OPENSSF_PROXY_MIN_SIGNALS` instead of `<`) — 5 failed: both
   not_applicable/third_party_gap proxy tests, the hand-computed oracle,
   the insufficient-signals test (now fails a different assertion — the
   flipped comparison makes 9-of-10 ALSO gate as insufficient), and the
   pike_signals/openssf agreement test.
6. **renormalisation dropped** (`openssf.py`: `missing = frozenset()`
   hardcoded, ignoring the caller's value) — 4 failed with `KeyError:
   'dependents_count'` (the dict comprehension building `raw` no longer
   skips the missing field, and the partial signals dict genuinely lacks
   that key) — a crash is still a caught mutant.
7. **`WINDOW_STALENESS_DAYS` changed with the hash/table left unchanged**
   (`constants_v2.py`: `14 -> 20`, `test_frozen.py` untouched) — 2 failed:
   `test_constants_hash_is_pinned` and
   `test_frozen_table[WINDOW_STALENESS_DAYS]`. Before this packet this
   constant lived outside `constants_dict()` and this mutation would have
   been invisible to every test in the suite; now it is caught by name.

Every mutant restored with `git show HEAD:<path> > <path>` (never `git
stash`/`git checkout --`); `git status --short` empty after each
restoration, confirmed after mutant 7 with a full-suite + ruff run.

## 6. Fixture

`scripts/build_fixture_snapshot.py` run once (against
`~/Projects/cvi-scratch/sample-200`, read-only, unmodified):

- Counts: `rated=0, gated=35, unrated=15` — **unchanged** from before this
  packet (same as after RFC-0001/W2-12).
- `records_hash`: `bc865a8c409b7562b3e1aa8bef3f69780838f057adbc7bf27421aba073000443`
  (moved from whatever W2-12 pinned, due to `CONSTANTS_HASH` and the new
  `PillarScore.applicable` structural field appearing on every record).
- Per-repo coverage for the 10 `systems`-ecosystem repos (checked all 10,
  reporting two): `torvalds/linux` and `apache/thrift` are both `coverage
  = 0.0525` **before and after** — unchanged, because in this specific,
  heavily-gapped fixture their criticality pillar's OTHER two parameters
  (2.2, 2.3) are ALSO unmeasured for reasons unrelated to D-MISS-5 (a
  0-numerator ratio: `0/0.60 = 0/1.00 = 0.0` either way), and 2.2's new
  `proxy:no_dependents_signal` path never fires against this specific
  sample because its underlying v1 derived documents predate this
  packet's `pike_signals.py` change (no `openssf_signals_partial` field on
  disk). This is a real, reproduced finding, recorded in RFC-0002's
  Evidence section and above in item 4/6's own description — NOT a defect
  in this packet's code: the mechanism is proven correct at the unit level
  (`test_composite_coverage_systems_repo_end_to_end_reaches_one`'s isolated
  `C = 1.00` oracle) and will activate once a future packet re-derives the
  sample corpus.
- Reproduce tests pass:
  `tests/snapshot/test_reproduce_fixture.py::test_reproduce_matches_expected_hash`
  and `::test_fixture_rebuild_is_byte_identical` both green (verified in the
  full-suite run below).

## Suite / ruff

```
env -u PYTEST_ADDOPTS PYTHONPATH=$PWD /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -B -m pytest -n 4 -p no:cacheprovider
2149 passed, 1 skipped in ~11s

PYTHONPATH=$PWD /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -m ruff check cvi/ tests/ scripts/
All checks passed!
```

## Not touched (per packet scope)

`cvi/store/**`, `web/**`, `cvi/serve/**`, `cvi/core/pipeline.py`,
`cvi/collect/{github_graphql,depsdev}.py`, `scripts/build_universe.py`. In
`cvi/derive/bundle.py` only `_resolve_dependents_deduped` and
`_resolve_openssf_criticality` (plus the new
`_resolve_openssf_criticality_proxy` helper beside it) were touched —
W2-12's `dr90_decision_rate` row and every other row are untouched (the
rebase auto-merged `bundle.py` with no conflicts, confirming the row
boundaries held).

## Final commit / hash summary

- Commit: `f95ff994935844ac5847e51d7cd604c10f294f08`
- Base: `8a4846c01fe702c3db405118e98a154d38590808`
- `CONSTANTS_HASH`: `d2702ea93a7ad9eb22696942dd3994d63f066ff2bf2e68e52abfc8235433570e` -> `9673c16a46d3aebf371cfc91ad1546c93bd47db535550996249c6c10145f444a`
- Suite: 2149 passed, 1 skipped; ruff clean
- Mutants: 7/7 caught (each pasted above, each restored, tree clean after)
- Fixture: rated=0/gated=35/unrated=15 unchanged; records_hash `bc865a8c409b7562b3e1aa8bef3f69780838f057adbc7bf27421aba073000443`

## Rebase addendum (after W1-10 landed as RFC-0002)

W1-10 landed on `main` (`783656a`/`469d681`) while this packet was in
review, claiming RFC-0002 for the StarScout `STARS_SCOUT_*` relocation.
Rebased onto the new `main` tip; `cvi/core/constants_v2.py` and
`cvi/derive/bundle.py` auto-merged with zero conflicts (row/section
boundaries held); resolved `tests/core/test_frozen.py` and
`docs/v2/rfcs/CHANGELOG.md` by hand (kept both sides); renamed this
packet's RFC file to `RFC-0003-not-applicable-rules-and-criticality-proxy.md`
(title, hash chain, records_hash reference updated) and re-chained the
changelog row after RFC-0002 instead of RFC-0001. Fixture regenerated on
the rebased base by hand (never picked a side on the three metadata
files) — diff confirmed minimal: only `constants_hash`/`records_hash`
moved, zero bundle content changed.

- Final commit: `cc5b1d60e35451bb5ecfa41ed92951e09c1cce86` (branch `W2-13`)
- Rebased base: `469d681` (local `main` tip after W1-10)
- `CONSTANTS_HASH`: `446c6a25db4e8c1f9d528f98a332d52bdf5ba570f07774bd674d9c816fdd09d0` (W1-10/RFC-0002) -> `419091af843a5ab994b23a5776c0f0ce9073ed283b9f540b1d26eca7bcd4d7a9` (this packet, RFC-0003)
- Fixture `records_hash`: `a0b4df4043cbd398db5036f852a2456be07f50daa79e61237060a5385dd49d2a` (W1-10's) -> `099264a0d53ad847e8b4ad0bdbf4e7c1e8b4e42d56ae46e5beda605463779271`; counts `rated=0, gated=35, unrated=15` unchanged
- Completeness test (`test_every_module_level_constant_is_hashed_or_declared_not_hashed`): passes
- All 7 mutants re-run against the rebased HEAD: all 7 still caught, identically (same failing tests, same assertion messages); tree verified clean (`git status --short` empty) after each restoration via `git show HEAD:<path> > <path>`
- Suite: `2211 passed, 1 skipped in 10.28s`, exit=0
- Ruff: `All checks passed!`
