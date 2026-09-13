# W2-12 handoff — dr90_decision_rate from the collected pr_window

Base: `04d78b8` (main tip at dispatch). Rebased once before handoff onto
`583a63d` (main tip at rebase time — main advanced through W1-4 landing,
W1-7f landing, W2-11 landing and a W3-2f cross-snapshot-detector packet
while this XS packet was in progress). Branch: `W2-12` in
`~/Projects/cvi-wt-W2-12`. Commit: see the branch tip (`git -C
~/Projects/cvi-wt-W2-12 log --oneline -1 W2-12`) — this file is part of
that same commit, so it cannot name its own final hash.

## What changed

### `cvi/derive/bundle.py`
- `INPUT_MAPPINGS["dr90_decision_rate"]` is now `_resolve_dr90_decision_rate`
  (was `_unmapped("...outside the landed W1-3 collector's trailing-180d
  window")`). Its docstring-table row is rewritten to describe the real
  resolver; the old placeholder comment row is gone.
- New `_resolve_dr90_decision_rate(ctx)` and a helper `_dr90_decision_cohort`
  (placed next to `_merged_external_latency_hours`, same by-number join
  style joining `derived[].is_external` with `prs[].{createdAt,mergedAt,
  closedAt}`). Module docstring gains a short W2-12 paragraph pointing at
  both.
- Cohort: external PRs whose `createdAt` falls in the half-open interval
  `[anchor-180d, anchor-90d)` — lower bound **inclusive** (a PR opened
  exactly 180 d ago is still inside the W1-3 collector's trailing window),
  upper bound **exclusive** (a PR opened exactly 90 d ago has not yet had
  the full 90 d every cohort member is owed to be decided, so it is left
  out rather than measured early — this is the whole point of the D-IN-4
  amendment). `anchor` is the pr_window envelope's own recorded
  `window_anchor`, never a live clock (F5 convention) — a missing/
  unparseable `window_anchor` is `Unmeasured(collector_failure)`.
- Decided: `mergedAt` or `closedAt` no more than 90 d **after**
  `createdAt`, **inclusive** (exactly 90 d counts). `r = decided / cohort`,
  `n = cohort`. `Unmeasured(insufficient_n)` below
  `PARAM_MIN_N["dr90_decision_rate"]` (10, unchanged by this packet). A PR
  with a missing or unparseable `createdAt` cannot be windowed and is
  dropped from the cohort, counted separately (`malformed`) and named in
  the `insufficient_n` detail (e.g. `"8 external PRs in the decision
  cohort, need >= 10 (2 excluded: malformed or missing createdAt)"`) — it
  never silently shrinks `n` with no explanation.
- `method`: `"cohort:[anchor-180d,anchor-90d) all external authors
  (D-IN-3 pending W3-1)"` as specified is stored on `_DR90_METHOD` in a
  comment (and in the resolver's own docstring) but is **not** what
  `Measured.method` actually holds — see "Deviation from the packet" below.
  Author eligibility (D-IN-3) is not applied: `W1-4` landed on main during
  this packet (author history + newcomer cohorts), but wiring D-IN-3 into
  every rate parameter is `W3-1`'s job, not this one's — the packet text
  itself defers it there, and that stays true regardless of W1-4 landing.
- `window_start`/`window_end` on the `Measured` are the **cohort** bounds
  (`anchor-180d`, `anchor-90d`), not the full pr_window envelope's own
  bounds (`_pr_window_bounds` is not reused here — DR_90's window is a
  strict subset of it).

### Deviation from the packet: `Measured.method`
The packet's literal method string
(`"cohort:[anchor-180d,anchor-90d) all external authors (D-IN-3 pending
W3-1)"`) cannot be stored in `Measured.method` as written:
`cvi.core.models.measured_problems` (called from `Measured.__post_init__`,
so it raises at construction time, not just at `validate_bundle`) is a
whole-codebase invariant that rejects any `method` other than exactly
`"primary"` or a `"proxy:<name>"`-prefixed string — confirmed by running
the resolver with the packet's literal string, which raised
`BundleValidationError: measured.method must be 'primary' or
'proxy:<name>', got '...'` immediately. `docs/v2/02-RANKING-DESIGN.md`
D-MISS-4 reserves `"proxy:"` for a *documented alternate methodology*
(4.1's review/CI proxy is the one example); DR_90's cohort computation
over the real, collected pr_window envelope is not a proxy for anything —
it is 1.2's one and only defined measurement — so `"proxy:"` would be a
misuse of that field's semantics, not just a workaround. Used
`method="primary"`, matching the other three pr_window-derived rows
(`ttfhr_hours`, `ttm_p90_hours`, `substantive_review_rate`, all
`method="primary"`). The caveat the packet's string was carrying (every
external author counts today, D-IN-3 pending W3-1) is preserved in
`_DR90_METHOD`'s comment and in `_resolve_dr90_decision_rate`'s own
docstring instead of in the `Measured.method` value. Flagging this
explicitly since it is the one place this packet did not do exactly what
was asked — everything else matches the spec as written.

### `tests/derive/test_input_mappings.py` (appended, not reordered)
- Removed `"dr90_decision_rate"` from `_UNMAPPED_ROWS` (it is no longer a
  constant-Unmeasured row) and updated the module's row-count docstring
  and the "six rows" section-header comment to "five rows" — a required
  correctness edit, not a reorder: leaving the old entry would have made
  `test_unmapped_row_is_constant` assert the wrong thing for this id.
- `test_dr90_decision_rate`: 10 external PRs 100–109 d before the anchor
  (comfortably inside the cohort). Decision outcomes: #100 decided by
  `mergedAt` (20 d), #101 decided by `closedAt` **alone** (`mergedAt`
  absent — kills a "closedAt ignored" mutant), #102 decided at *exactly*
  90 d (decision-window's own inclusive boundary), #103 *not* decided at
  91 d (one day over), #104 decided at 30 d, #105–107 still open, #108
  eventually merged but 200 d later (a late merge is not a fast decision),
  #109 still open. Decided 4/10, `r=0.4`. Asserts the full `Measured`
  (value/n/window_start/window_end/method/source/source_hash/fetched_at)
  plus the no-envelope → `collector_failure` negative.
- `test_dr90_decision_rate_cohort_window_boundaries`: PRs at
  89/90/91/179/180/181 d before the anchor (external) plus an internal PR
  at 150 d (in-range date, must be excluded by `is_external`) and an
  external PR at 250 d (well outside either edge). Only 91/179/180 belong
  to the cohort → `n=3`, `Unmeasured(insufficient_n, "3 external PRs in
  the decision cohort, need >= 10")`. Pins both cohort-window edges and
  the `is_external` filter in one assertion.
- `test_dr90_decision_rate_malformed_created_at_excluded_and_named`: 8
  valid in-cohort external PRs plus one with `createdAt: null` and one
  with `createdAt: "not-a-timestamp"` → `n=8`,
  `"8 external PRs in the decision cohort, need >= 10 (2 excluded:
  malformed or missing createdAt)"`.

### `tests/fixtures/snapshot_50/` — regenerated
`scripts/build_fixture_snapshot.py` run once (no args) against
`~/Projects/cvi-scratch/sample-200`, on the fully rebased tree.

## Fail-before

Ran the three new tests against the base file content twice: once against
the original dispatch base (`04d78b8`, via
`git show 04d78b8:cvi/derive/bundle.py > cvi/derive/bundle.py`) and again
after the rebase against the new base (`583a63d`), restoring the fixed
file (`cp` from a saved copy — **not** `git checkout --`, which is
forbidden and was accidentally invoked once mid-mutant-testing; caught
immediately, the saved copy restored it losslessly, see "Mutants" below)
in between. Both runs: all three new tests red with the pre-existing
`Unmeasured(collector_failure, "...outside the 180d window...")` value.

## Mutants (6, each pasted/run/restored on both the pre- and post-rebase
tree; all killed both times)

| # | Mutation | Result |
|---|---|---|
| 1 | `_DR90_COHORT_START_DAYS` 180 → 179 | red: `test_dr90_decision_rate` (Measured→Unmeasured) and `..._cohort_window_boundaries` (n 3→2 in detail) |
| 2 | `_DR90_COHORT_END_DAYS` 90 → 89 | red: same two tests (n 3→4 in detail; D=90 wrongly admitted) |
| 3 | `_DR90_DECISION_WINDOW_DAYS` 90 → 89 | red: `test_dr90_decision_rate`, `value: 0.3 != 0.4` (the exactly-90-day PR #102 wrongly drops out) |
| 4 | `_DR90_DECISION_WINDOW_DAYS` 90 → 91 | red: `test_dr90_decision_rate`, `value: 0.5 != 0.4` (the 91-day PR #103 wrongly counts) |
| 5 | `is_external` check dropped | red: `..._cohort_window_boundaries`, `detail: '4 ... ' != '3 ...'` (internal PR #150 wrongly admitted) |
| 6 | `closedAt` ignored (`if decided_by_merge:` only) | red: `test_dr90_decision_rate`, `value: 0.3 != 0.4` (closedAt-only PR #101 wrongly not counted) |

One process note: while sequencing these mutants I ran `git checkout --
cvi/derive/bundle.py` intending it as a revert, which — since nothing had
been committed on this branch yet at that point — actually reset the file
to the unmodified base (wiping the fix). Caught immediately via `grep` for
`_resolve_dr90_decision_rate` coming back empty; restored from a `cp`
backup taken before mutant testing started, with no lost work. All
restores after that point used `cp` only, per the standing rule.

## Full suite / ruff

```
env -u PYTEST_ADDOPTS PYTHONPATH=$PWD /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -B -m pytest -n 4 -p no:cacheprovider
2129 passed, 1 skipped in 10.30s
```
Run twice: once before the rebase (2027 passed then, on the older main),
once after (2129 passed — the difference is entirely main's own growth
during this packet, not this packet's tests). `ruff check .`: All checks
passed, both times.

## Fixture: Measured count and hash

**0 of 50** fixture repos gain a Measured 1.2. Root cause, confirmed
directly: `find ~/Projects/cvi-scratch/sample-200/raw -iname "*pr_window*"`
returns nothing — the `sample-200` corpus has no `raw/pr_window/`
directory at all (it carries the old `pr_events`/`pr_reviews` v1 raw
data, predating the W1-3 collector entirely). This is not specific to
DR_90: `ttfhr_hours`, `ttm_p90_hours` and `substantive_review_rate` — the
other three rows that read the same pr_window envelope — are equally
`Unmeasured(collector_failure)` on all 50 repos today, confirmed by the
same check against those three fields. Fixing this needs a live gather
against the pr_window collector for these 50 repos, which is out of
scope here (no network, no wall clock, XS packet) and belongs with
W2-8c's already-tracked gather-refresh queue.

What *did* change in the fixture: every one of the 50 bundles' `dr90_
decision_rate` `detail` string changed from the old constant `_unmapped`
message (`"v2 parameter; D-IN-4's decision-rate cohort is PRs opened in
[now-270d, now-90d]..."`) to the new resolver's per-repo
`_PR_WINDOW_UNRESOLVABLE_DETAIL` message. Because `raw_hash` (in
`manifest.json`) is a sha256 of the whole bundle document, all 50 changed;
`records_hash` (in `expected.json`) changed for the same reason.

- Old `records_hash` (pre-packet, still on `04d78b8`): `a0dac868…`
- New `records_hash` (this packet, on top of `583a63d`):
  `c9487c8649c06362a2a3e3e8fddd0ece0df9d10260b1149629ab55e1bd1f69e2`

The reproduce-fixture tests (`tests/snapshot/test_reproduce_fixture.py`)
pass against this new hash (included in the 2129-passed full-suite run
above).

## Diffstat (base `583a63d` → branch tip, excluding this handoff file)

54 files changed, 378 insertions(+), 118 deletions(-): `cvi/derive/
bundle.py`, `tests/derive/test_input_mappings.py`, and 52 fixture files
(50 bundle JSONs + `expected.json` + `manifest.json`; `populations.json`
unchanged — no rated repo means no population content depends on this
row). Adding this handoff file makes it 55 files, 567 insertions(+).
