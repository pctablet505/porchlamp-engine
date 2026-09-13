# W1-7f handoff — crash guard, F2/F5 rulings, dedupe, 4.1 n, bodies-cache provenance

Worktree: `/home/pctablet505/Projects/cvi-wt-W1-7f`, branch `W1-7f`.
Base at dispatch: `4e917e1` (main tip when the packet was picked up).
Main kept moving fast during this session (a multi-agent swarm was landing in
parallel); rebased twice rather than chase it indefinitely:
1. Onto `bd0cec4` (31 commits ahead of dispatch, including W1-6 governance
   evidence, W2-8/W4-2/W2-8b/W2-8c) — 3 conflicts, resolved, see "W1-6
   interaction" below for the one substantive consequence.
2. Onto **`04d78b8`** (4 more commits: W1-2f, which also touched
   `cvi/derive/bundle.py` and `tests/derive/test_input_mappings.py`) — 0
   conflicts, verified with a fresh mutant re-run of item 5's guard
   (`n = len(prs or [])` still dies both the new and pre-existing test).
Final commit: `b15d4be` (`git log --oneline -1`); the fix commit itself is
`ee97ef9`. Main may well have advanced again by the time this is read —
that is expected in this session, not a sign anything here is stale; a
diff against the actual current tip will show at most the same three
small hook-tail conflicts as round 1, none of which touch this packet's
own logic.

## Diffstat (bd0cec4..ee97ef9 — unchanged by the second, conflict-free rebase)

```
 cvi/collect/envelope.py             |  11 ++
 cvi/collect/pike_signals.py         |  91 ++++++++++++---
 cvi/derive/bundle.py                |  19 ++++
 scripts/derive.py                   |  82 ++++++++++----
 scripts/gather.py                   |   5 +-
 tests/collect/test_w17_pike.py      | 217 +++++++++++++++++++++++++++++++++++-
 tests/derive/test_input_mappings.py |  86 ++++++++++++++
 tests/test_derive.py                |  72 +++++++++---
 8 files changed, 527 insertions(+), 56 deletions(-)
```

No collector query shape or points cost changed (rule complied with, nothing
to report against it).

## Items, in the packet's order

### 1. Crash guard (F1)

`cvi/collect/pike_signals.py`: `derive_w17_fields`'s `openssf_signals` branch
called bare `max(ats)` (line ~627 pre-fix) while its `pipeline_defenses`
sibling 13 lines below already guarded the same pattern — `ValueError` on an
all-`None` `ats` list. Fixed to `max(ats) if ats else None`, matching the
sibling.

Broader guard per the item's own wording ("every per-repo exception becomes a
recorded failure envelope ... and the hook returns"): `scripts/derive.py` now
wraps the whole `pike_mod.derive_w17_fields(...)` call in `try/except
Exception`. On any exception (not just F1's specific line), `openssf_signals`
and `pipeline_defenses` are set to a new v1 confidence sentinel
`"collector_failure"` with the exception type + message in a companion
`<field>_detail` meta key, and `derive_repo` continues — every other field
(before and after the hook, including W1-6's governance block right after) is
unaffected.

`cvi/derive/bundle.py`'s `_resolve_openssf_criticality` / `_resolve_pipeline_defenses`
gained an explicit branch for `confidence_value == "collector_failure"` that
reads the companion detail key, so the v2 `Unmeasured(reason="collector_failure",
detail=...)` names the actual exception, not the generic "confidence not
recognized" fallback text.

Tests: `test_derive_repo_pike_signals_survive_envelopes_with_no_fetched_at`
(F1 exact), `test_derive_repo_pike_hook_crash_is_collector_failure_not_a_lost_bundle`
(the broader guard: `pike.derive_w17_fields` monkeypatched to raise, asserts
`stars`/`dependents_count` still computed correctly), `test_gather_sweep_continues_after_one_repo_w17_collector_crash`
(gather-side: 3 repos, a fake transport raises on repo 2 only, repo 3's
releases/issues_counts/runs envelopes still exist — this property already
held via `gather_repo`'s existing per-collector try/except; the test locks it
in against regression), plus two bundle.py-level tests
(`test_openssf_criticality_collector_failure_carries_the_companion_detail`,
`test_openssf_criticality_collector_failure_without_detail_still_names_the_reason`,
`test_pipeline_defenses_collector_failure_carries_the_companion_detail`).

Mutants (all verified red, restored, suite re-confirmed green after each):
1. Revert `max(ats) if ats else None` → bare `max(ats)`:
   `test_derive_repo_pike_signals_survive_envelopes_with_no_fetched_at` dies
   (`assert None is not None` — the crash-guard wrapper catches the
   `ValueError` and correctly reports it as `collector_failure` instead of
   `derived`, so the test still catches the regression even though the crash
   itself is now contained).
2. Remove the `try/except` wrapper entirely (call `derive_w17_fields`
   unguarded): `test_derive_repo_pike_hook_crash_is_collector_failure_not_a_lost_bundle`
   dies with the raw `RuntimeError` propagating out of `derive_repo`.
3. Remove the explicit `confidence_value == "collector_failure"` branch in
   both `bundle.py` resolvers: all three bundle.py-level tests
   (`test_openssf_criticality_collector_failure_carries_the_companion_detail`,
   `..._without_detail_still_names_the_reason`,
   `test_pipeline_defenses_collector_failure_carries_the_companion_detail`)
   die — detail reverts to the generic "not recognized" text.

### 2. F2 ruling (empty vs. failed)

Audited all ten Pike signals plus 4.1's two proxy components for the
`None`-vs-`[]` (absent-envelope vs. successful-empty-fetch) distinction:

| Signal(s) | Before | Verdict |
|---|---|---|
| `updated_since_months`, `contributor_count`, `org_count`, `commit_frequency_weekly` | `if not c365: return None` conflated `c365 is None` (absent) with `c365 == []` (a real zero-commit repo) | **Bug — fixed** |
| `recent_releases_count` | `releases_in_window([], now) == 0` already | correct |
| `closed_issues_count_90d`, `updated_issues_count_90d`, `comment_frequency` | `int(0 or 0) == 0`, `comments_per_issue([]) == 0.0` (not `None`) already | correct |
| `dependents_count` | `int(0)` is not `None`, so `dep.get("deduped_count") is None` correctly stays `False` for a real zero | correct |
| `created_since_months` | always requires a real envelope; no "zero" case applies | N/A |
| 4.1 `code_review_rate` | `if not merged: return 0.0` already | correct |
| 4.1 `ci_pass_rate` | `None` for empty `runs`, converted to the documented "0 if no CI" by the caller | correct by design |

Fix: `derive_pike_signals` now distinguishes `c365 is None` (still `None` —
unmeasured) from `c365 == []` (a repo-controlled zero): contributor_count=0,
org_count=0, commit_frequency_weekly=0.0, and `updated_since_months` falls
back to `github_meta.pushed_at` (already an existing function argument).

Tests, both sides, 4 signals at once (they share one `OpenSSFSignals`):
`test_derive_pike_signals_zero_commits_is_measured_zero_via_pushed_at`
(positive — asserts all 4 fields), `test_derive_pike_signals_missing_commits_envelope_is_still_unmeasured`
(negative — `c365=None` stays `None` even with `pushed_at` present, proving
the fallback is keyed on the envelope's own presence, not merely on
`pushed_at`'s availability). The pre-existing
`test_derive_pike_signals_missing_any_source_is_none`'s last assertion (`c365=[]`
with **no** `pushed_at` in that fixture's `gh` dict) still correctly returns
`None` unchanged — its stale comment was corrected to explain why (no
`pushed_at` for the fallback to use), not touching the assertion.

Mutant: swap the `if c365: ... else: ...` bodies (so a non-empty c365 gets
the zero-fallback and an empty one gets `human_commit_stats`) →
`test_derive_pike_signals_zero_commits_is_measured_zero_via_pushed_at` dies
(`assert None is not None`); collaterally also kills
`test_derive_pike_signals_oracle_hand_built` and
`test_derive_repo_pike_signals_from_envelopes` (5 failures total).

### 3. F5 ruling (window-anchor staleness)

`collect_releases`/`collect_issue_counts` already write `window_anchor` (the
collector's own `now`) into their envelope `data` — confirmed unchanged.
Wired the derive-side check only, in `cvi.collect.pike_signals.derive_w17_fields`
(same module, `now` is already an explicit parameter there — no `as_of`
threading into `cvi/derive/bundle.py`'s `load()` was needed or added):
`window_anchor_stale(anchor_iso, now, max_days=WINDOW_STALENESS_DAYS=14)`
compares via `total_seconds()` (not `.days`, to avoid truncation ambiguity at
the boundary). A stale `releases` or `issues_counts` anchor sets
`openssf_signals` to `"collector_failure"` with detail
`"<collector> window anchored at <anchor>, as_of <now>"` via the same
companion-detail mechanism item 1 built, `bundle.py`'s resolver reused
unchanged.

Tests: 13 days (fresh), exactly 14 days (still fresh — the boundary), 15 days
(stale) — `test_derive_w17_fields_releases_window_{13,exactly_14,15}_days_...`.

Mutant: `>` → `>=` in the comparison → `test_derive_w17_fields_releases_window_exactly_14_days_is_still_fresh`
dies (`'collector_failure' == 'derived'` fails); the 13/15-day tests alone do
**not** catch this mutant (confirmed by running them in isolation against the
mutant — both still pass), which is exactly why the boundary test was added
beyond what the packet asked for.

### 4. Dedupe (F6)

Added `cvi.collect.envelope.raw_envelope_path(data_dir, collector, key)` — the
one `<data_dir>/raw/<collector>/<key>.json` formula. `scripts/derive.py`'s
`src()` and `cvi.collect.pike_signals`'s `env_path()` (inside
`derive_w17_fields`) both now call it instead of each defining their own
`str(data_dir / "raw" / ... )`. `scripts/gather.py` now imports `SLEEP_REST`
from `cvi.collect.pike_signals.SLEEP_CORE_SECONDS` instead of a second `0.15`
literal (kept the name `SLEEP_REST` to avoid touching gather.py's many
existing call sites; picked `pike_signals` as the canonical owner, not
`gather.py`, to avoid `cvi/` depending on `scripts/` — the wrong layering
direction).

Tests: `test_src_and_env_path_share_one_definition` (identity check: both
modules import the literal same function object) and
`test_src_and_env_path_both_route_through_the_shared_function` (behavioral:
monkeypatches the shared name in both modules with a recording spy, drives a
full `derive_repo` call, and asserts the spy actually saw a `src()`-only
collector ("tree") and an `env_path()`-only collector ("dependents") — proves
neither closure kept its own inline formula, which the identity check alone
cannot).

Mutant: revert both closures to their original inline `str(data_dir / "raw" /
...)` bodies (import left in place, unused) →
`test_src_and_env_path_both_route_through_the_shared_function` dies (`assert
'tree' in set()`); the identity-only test does **not** catch this mutant
(confirmed) — the reason both tests are kept.

### 5. Parameter 4.1 n (F7)

**Already correct on base** — no source fix was needed. W2-9f (`b9d4820`,
landed before this packet was dispatched) already made
`cvi.derive.bundle._resolve_pipeline_defenses` recompute `n` independently
from the `pr_window` envelope's own `prs` list
(`sum(1 for pr in prs if pr.get("mergedAt") is not None)`), matching
`cvi.collect.pike_signals.code_review_rate`'s own denominator definition
exactly (same filter, same field — verified by reading both side by side).
This already differs per fixture (existing test
`test_pipeline_defenses_proxy_method_counts_merged_prs` pins `n=3` of 5 PRs)
and is read correctly by `cvi.core.params.rigor.pipeline_defenses`'s
`_apply`/`_required_min_n` (`value.n`, `defn.proxy_min_n=10`).

What was missing was **test coverage tying the two together** end-to-end
(bundle.py's computed `n` actually reaching `cvi.core.params.rigor`'s 10-PR
floor) — F7's original review finding (`pike_signals.py`'s own meta output
never carried `n` at all) predates W2-9f's fix and no longer describes the
code. Added `test_pipeline_defenses_n_feeds_the_rigor_min_n_gate`: builds two
fixtures (9 and 10 merged PRs, one extra never-merged PR in both that must
never count), asserts `n` differs (9 vs 10) and that `cvi.core.params.rigor.pipeline_defenses`
reports `insufficient_n` at 9 and a real score at 10.

**COULD NOT VERIFY as a "fail-before" in the usual sense**: since the
underlying wiring was already correct, restoring `bundle.py` to the packet's
literal base (`bd0cec4`) does not fail this new test for lack of a fix — it
fails only because the two `collector_failure`-branch tests (item 1) also
live in the same file and reference code added by this packet. Isolated
fail-before confirmed via mutant instead (below), which is the honest
substitute here (per R-9: report the real number, not a fabricated
regression).

Mutant: in `cvi/derive/bundle.py`, change the proxy `n` computation from
`sum(1 for pr in (prs or []) if ... pr.get("mergedAt") is not None)` to
`len(prs or [])` (count every PR, not just merged ones) →
`test_pipeline_defenses_n_feeds_the_rigor_min_n_gate` dies (`assert (10 ==
9)` — both fixtures now report `n=10` since the extra open PR gets counted
too), and collaterally kills the pre-existing
`test_pipeline_defenses_proxy_method_counts_merged_prs` too (2 failures).

### 6. `scripts/derive.py` bodies-cache provenance

Confirmed the review's/W2-9f's finding precisely:
`legal_ip_model`/`ai_posture`/`promotion_ladder` recorded a raw
`https://raw.githubusercontent.com/...` URL as `source` even when the body
was genuinely cached locally under `raw/bodies/`.
`cvi.derive.bundle._resolve_envelope` rejects any `http(s)://` string
outright (by design, per INT-W0-4's ruling), so **every** real value of
these three fields was `Unmeasured(collector_failure, "v1 source is not an
envelope: https://...")` at the v2 layer regardless of caching — confirmed by
temporarily reverting the fix and observing `cvi/derive/bundle.py`'s existing
`_vocab_scalar_resolver` reject the URL exactly as documented.

Fixed by pointing `source` at the local bodies-cache envelope path (via a new
`_body_cache_path()` helper, the one place the cache-file formula is now
written) when that file exists, falling back to the `tree` envelope (also a
real, hashable source) only when the relevant body was never fetched.
`legal_ip_model` uses CONTRIBUTING.md's cache; `promotion_ladder` uses
CONTRIBUTING.md then CODE_OF_CONDUCT.md; `ai_posture` uses README.md (the file
its corpus already privileged via the original hardcoded URL).

**W1-6 interaction (discovered only after the mid-session rebase onto
`bd0cec4`; not knowable at dispatch time).** W1-6 (landed on main during this
session) added a governance-evidence block immediately after this one in
`scripts/derive.py` that **unconditionally overwrites** `ai_posture` and
`promotion_ladder` with its own evidence-based values and provenance — same
field names, later `b.set()` call always wins, regardless of whether a
`governance` envelope exists (confirmed: `cvi.collect.governance.derive_governance_fields`
always returns dict entries for both fields, and the caller always calls
`b.set` for them). `legal_ip_model` is untouched by that block (the
governance module writes a **different**, bare `legal_ip` field —
`cvi/derive/bundle.py`'s v2 `"legal_ip"` input now reads `meta["legal_ip"]`,
not `meta["legal_ip_model"]`, confirmed by reading `INPUT_MAPPINGS`).

Net effect: this fix's `ai_posture`/`promotion_ladder` provenance repair is
now **dead code** — computed, then always overwritten before the bundle is
returned, so it is unobservable in any output. `legal_ip_model`'s repair is
the only one that still matters, and it matters more than originally
scoped: per `cvi.collect.governance`'s own module docstring, `legal_ip_model`
is the field driving the **live v1 production scoring sweep** (00-README's
non-negotiable "never change a running sweep" — this fix does not change
its value, only its provenance, so the non-negotiable is respected).

Left the dead `ai_posture`/`promotion_ladder` provenance code in place rather
than reverting it: it is not wrong, costs nothing observable, and reverting
would be pure churn with no behavioral benefit and its own regression risk.
Flagging it here for whoever next touches this block.

Updated two tests that pinned the pre-fix URL behavior: the packet's own
`test_provenance_omits_sha256_for_non_envelope_source` (already retargeted
once, mid-session, by an unrelated main commit `0a4968c`, from `ai_posture`
to `legal_ip_model` specifically *because* of this same bug — my fix then
made that retargeted version fail too, so it now uses `owner`/`"cli"`, a
field with no possible envelope, as the exemplar instead of any
body-heuristic field). Renamed/retargeted the two new tests from
`ai_posture`-based to `legal_ip_model`-based:
`test_legal_ip_model_provenance_is_the_local_body_cache_not_a_url`,
`test_legal_ip_model_provenance_one_cached_one_missing` (the packet's
"one cached, one missing" fixture — CONTRIBUTING.md cached in one document,
absent in a second, sibling document).

Mutant: revert `legal_source`'s selection back to the hardcoded URL literal →
both `legal_ip_model` tests die (`'https://...' == '/tmp/.../CONTRIBUTING.md.json'`
fails).

## Rebase and the W1-6 conflict

31 commits landed on `main` between dispatch (`4e917e1`) and handoff
(`bd0cec4`) — far more than the packet anticipated ("INT-W0-4 has just
landed" turned into W1-6 rounds 2-4, W2-8 round 2, W4-2 rounds 1-3, W2-8b,
W2-8c phase 1, and more). `git rebase bd0cec4` produced three real conflicts,
all in the append-only hook tail where W1-6's governance block landed
immediately after this packet's W1-7 block:

- `scripts/derive.py`: both patches touched the same six lines around the
  `pike_mod.derive_w17_fields(...)` call. Resolved by keeping this packet's
  `try/except` wrapper and appending W1-6's governance block unchanged right
  after it (outside the `try`, since it does not depend on `w17_fields`).
- `scripts/gather.py`: both patches added an import line at the same spot
  (W2-8c's `from cvi.paths import data_root`, this packet's `SLEEP_REST`
  import). Kept both.
- `tests/test_derive.py`: `test_provenance_omits_sha256_for_non_envelope_source`
  had been retargeted on main (commit `0a4968c`) for the same class of bug
  this packet also found and fixed elsewhere; resolved as described in item
  6 above.

`cvi/collect/pike_signals.py`, `cvi/collect/envelope.py`, `cvi/derive/bundle.py`,
`tests/collect/test_w17_pike.py`, `tests/derive/test_input_mappings.py` all
merged with **zero** textual conflicts.

## Suite and ruff (post round-1-rebase, on `ee97ef9`; round 2 re-verified below)

Fail-before (all five touched source files restored to `bd0cec4` via `git
show bd0cec4:<path> > <path>`, new/touched test files left at HEAD):

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest \
    tests/collect/test_w17_pike.py tests/test_derive.py tests/derive/test_input_mappings.py \
    -p no:cacheprovider -o addopts=""
FAILED tests/collect/test_w17_pike.py::test_derive_pike_signals_zero_commits_is_measured_zero_via_pushed_at
FAILED tests/collect/test_w17_pike.py::test_derive_repo_pike_signals_survive_envelopes_with_no_fetched_at
FAILED tests/collect/test_w17_pike.py::test_derive_repo_pike_hook_crash_is_collector_failure_not_a_lost_bundle
FAILED tests/collect/test_w17_pike.py::test_derive_w17_fields_releases_window_15_days_is_stale
FAILED tests/collect/test_w17_pike.py::test_src_and_env_path_share_one_definition
FAILED tests/collect/test_w17_pike.py::test_src_and_env_path_both_route_through_the_shared_function
FAILED tests/test_derive.py::test_legal_ip_model_provenance_is_the_local_body_cache_not_a_url
FAILED tests/test_derive.py::test_legal_ip_model_provenance_one_cached_one_missing
FAILED tests/derive/test_input_mappings.py::test_openssf_criticality_collector_failure_carries_the_companion_detail
FAILED tests/derive/test_input_mappings.py::test_openssf_criticality_collector_failure_without_detail_still_names_the_reason
FAILED tests/derive/test_input_mappings.py::test_pipeline_defenses_collector_failure_carries_the_companion_detail
======================== 11 failed, 79 passed in 0.30s =========================
```

(The 13/14-day staleness tests and the gather-sweep test pass even
pre-fix — they exercise the "already correct" side of items 3 and 1
respectively, which is expected and by design; see items above for why.)

Restored (`git checkout HEAD -- <the five files>`, confirmed byte-identical
to the commit via `git diff HEAD --stat` = empty — no `git stash` / no
discarded work, since the five files were already safely committed):

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest tests/ -n 4 -p no:cacheprovider -o addopts=""
================ 2014 passed, 1 skipped, 12 warnings in 12.50s =================
```

The 1 skip (`tests/snapshot/test_reproduce_fixture.py:97`) is pre-existing
and unrelated (run-by-hand-once fixture-corruption note, not touched by this
packet).

```
$ ruff check cvi/collect/pike_signals.py cvi/collect/envelope.py cvi/derive/bundle.py \
    scripts/derive.py scripts/gather.py tests/collect/test_w17_pike.py \
    tests/test_derive.py tests/derive/test_input_mappings.py
All checks passed!
$ ruff check cvi/ scripts/ tests/
All checks passed!
```

### Round-2 rebase (onto `04d78b8`) re-verification

```
$ git rebase 04d78b8
Successfully rebased and updated refs/heads/W1-7f.     # 0 conflicts
$ ruff check cvi/ scripts/ tests/
All checks passed!
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest tests/ -n 4 -p no:cacheprovider -o addopts=""
================ 2041 passed, 1 skipped, 12 warnings in 11.26s =================
```

Item 5's mutant (`cvi/derive/bundle.py`'s proxy `n` computation ->
`len(prs or [])`) re-applied and re-verified on this final tree: both
`test_pipeline_defenses_n_feeds_the_rigor_min_n_gate` and the pre-existing
`test_pipeline_defenses_proxy_method_counts_merged_prs` die, restored,
suite re-confirmed green (chosen because W1-2f — the 4 commits between the
two rebase targets — also touched `cvi/derive/bundle.py`).

## Live-network attempts: 0

Every new/modified test uses `FakeRest`-style stubs or `monkeypatch.setattr`
on collector entry points / transport classes, matching the file's existing
convention — no `block_live_network` conftest fixture exists on this base (it
was not found anywhere in the repo; likely a description of a guard on
another in-flight branch, not yet on main — noted, not fabricated). While
extending `seed_gather_done` for the new gather-sweep test, discovered that
the **pre-existing** `test_gather_repo_w17_collectors_{ok,skip,fail}` tests
were already making live `gh api` calls (confirmed: real "HTTP 404" /
"Could not resolve to a Repository" responses from GitHub, ~23s wall time)
for `commits_365d` and `pr_window` — `seed_gather_done` never pre-seeded
those two collectors' envelopes. Fixed `seed_gather_done` itself (the shared
helper, so both the pre-existing and new tests benefit) to pre-seed
`commits_365d`/`dependents`/`pr_window` too; runtime for the whole file
dropped from ~23s to 0.1s. This was necessary to meet the packet's 0-attempts
rule for tests I touch (I touched `seed_gather_done`'s signature), and is a
byproduct fix, not one of the 6 numbered items — reported here for
visibility.

## Attempt counts

- Fail-before/fix/mutant cycles: 6 (one per item) + 1 extra boundary mutant
  for item 3 (the `>=` comparison, beyond what the packet named) + 1 extra
  behavioral-vs-identity mutant for item 4 (to show the identity-only test is
  insufficient on its own).
- Live repos touched: 0 (no `--fetch`/network path exercised; `_body()`'s
  live-fetch branch and `GhRestTransport`'s real `gh` calls are untouched by
  any test).
- Rebase conflicts resolved: 3 files (`scripts/derive.py`, `scripts/gather.py`,
  `tests/test_derive.py`), 0 unresolved.

## COULD NOT VERIFY

- Item 5: no genuine "fail-before" exists (see above) because the wiring was
  already correct on the packet's base; substituted a mutant-based
  verification, which is reported as such rather than presented as a real
  regression fix.
- The packet's cited `block_live_network` conftest guard does not exist on
  this base (grepped the full repo tree, `tests/conftest.py`, and
  `tests/collect/`; nothing named that or functionally equivalent — no
  `pytest-socket`, no autouse network-blocking fixture). Achieved the same
  guarantee (0 live attempts) through the file's existing fake-transport
  convention instead; flagging the discrepancy rather than claiming the named
  guard ran.
