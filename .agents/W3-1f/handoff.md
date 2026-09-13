# Handoff — W3-1f (follow-ups from the Opus review of W3-1)

Worktree `/home/pctablet505/Projects/cvi-wt-W3-1f`, branch `W3-1f`, started from the W3-1
branch tip `acdf885` (== main at that point). Review read:
`/tmp/claude-1000/-home-pctablet505-Projects/2a48e84a-2b94-4a2f-8bff-adbe955a4084/scratchpad/reviews/W3-1-review.md`.
Dispatch items, in the coordinator's own numbering: W-1 (DR_90 per-author cap), W-2 (method tag),
W-4/F-004 loader half (real resolvers for the three still-`_unmapped` author_history rates), and
four Notes items.

## 0. Diffstat (against the W3-1f branch point, `acdf885`)

```
 .agents/W3-1/handoff.md             |  17 +-
 cvi/collect/github_graphql.py       |  27 ++-
 cvi/derive/bundle.py                | 353 ++++++++++++++++++++++-----
 cvi/verify/detectors.py             |  13 +-
 tests/collect/test_pr_window.py     |  40 +--
 tests/derive/_fixtures.py           |  19 ++
 tests/derive/test_input_mappings.py | 468 +++++++++++++++++++++++++++++++++++-
 7 files changed, 827 insertions(+), 110 deletions(-)
```
`cvi/derive/bundle.py`: 1,433 → 1,782 lines (W2-9f's own count carried forward through W2-12/W3-1
was 1,433 at this branch point; +349, +24%). Not split: the growth is one new module-scope helper
(`_author_history_envelope`), one new eligibility predicate (`_dr90_author_eligible`), the
rewritten `_dr90_decision_cohort`/`_resolve_dr90_decision_rate` pair, and one new resolver factory
(`_author_history_rate_resolver`) — all load-bearing for this packet's four items, none of it
incidental. `cvi/collect/github_graphql.py` and `tests/collect/test_pr_window.py` are touched only
for the Notes-item-4 rename (below); no behavior in that module changed.

Stayed out of `cvi/store/**`, `web/**`, `cvi/serve/**`, `cvi/core/pipeline.py` — none touched.

## 1. Item W-1 — DR_90 per-author cap + eligibility-gate join

**The bug the review named (C2b):** `_resolve_dr90_decision_rate` (W2-12's live pr_window-based
DR_90) applied neither D-IN-3 mechanism at all — a sock puppet filing 6 of a 10-PR cohort, all
6 fast-merged, scored `r = 0.6` where the cap should give `0.3333`.

**Fix.** `cvi/derive/bundle.py`:
- `_dr90_decision_cohort(data, anchor, author_records=None)` now takes an optional
  `author_records` map (login → `author_history` record). When given, each cohort PR's author is
  checked with the new `_dr90_author_eligible` (tri-state: `True`/`False`/`None`, `None` — unknown
  — excluded from the population entirely, matching `cvi.core.hygiene.account_eligibility`'s own
  discipline). Whether or not the gate ran, survivors are always run through
  `cvi.core.hygiene.apply_author_cap` keyed on `(login, createdAt)` — both present on every
  `pr_window` PR node regardless of `author_history` — so the cap is unconditional and the gate is
  conditional, exactly matching the review's own diagnosis (C2c): "the CAP needs only
  `(login, created_at)`... it needs only a join that already exists".
- A PR node with no recorded `author` at all (a data-quality gap in some pre-cap-era fixtures)
  gets a synthetic singleton login (`f"__no_author_pr_{number}"`) rather than being silently
  lumped into one shared empty-string "author" — the latter would treat unrelated missing-author
  PRs as one giant sock puppet.
- `_dr90_author_eligible`'s `measured` flag combines BOTH of the collector's failure signals
  (`eligibility_unmeasured` OR `history_unmeasured`), mirroring
  `cvi.collect.github_graphql._account_eligibility` (F-001's "one D-IN-3 mechanism"); checking only
  one would silently read the other kind of fetch failure as a confirmed `True`/`False`. Unlike
  that collector-side helper, a login entirely ABSENT from `author_records` reads as unknown
  (`None`) here, not "confirmed ineligible on a zero event count" — this join crosses two
  independently-windowed envelopes (`author_history`'s 365 d cohort vs. this PR's own `pr_window`
  position), so a genuine membership miss is expected and must not silently score as "confirmed
  ineligible" on data that was simply never fetched (documented in the function's own docstring).

**A bug found and fixed while implementing this (not in the review, but load-bearing for it):**
`_dr90_decision_cohort` originally returned only the CAPPED cohort size, and
`_resolve_dr90_decision_rate` checked `PARAM_MIN_N` against that capped size. That is wrong two
ways at once: (a) it would make the packet's own oracle scenario (10 raw PRs, 6 kept after
capping) read `Unmeasured(insufficient_n)` instead of the intended `Measured(0.3333)`, since 6 < 10;
and (b) it made a population of exactly 3-10 independent single-PR authors read as "0 PRs" whenever
`apply_author_cap`'s own "populations under 5 have a cap of 0" rule fired on a raw count that had
nothing to do with concentration. Fixed by having `_dr90_decision_cohort` return BOTH sizes
(`decided, capped_cohort_size, raw_cohort_size, malformed, gate_applied`) and checking `min_n`
against `raw_cohort_size` (the population after the date window and, if applicable, the gate — but
before the cap), while reporting `capped_cohort_size`/the capped decided count in the `Measured`
value. `test_dr90_decision_rate_cohort_window_boundaries` (pre-existing, pinned) caught this
immediately once the new oracle fixtures existed to exercise the interaction.

**Tests** (`tests/derive/test_input_mappings.py`):
- `test_dr90_decision_rate_author_cap_sock_puppet` — the reviewer's own oracle: 6 of 10 cohort PRs
  from one login, all 6 fast-merged, 4 honest single-PR authors open. Cap keeps the sock's 2
  oldest (`floor(0.20*10)=2`); `r = 2/6 = 0.3333`, not `0.6`. Also pins `min_n` checked against the
  raw 10, not the capped 6.
- `test_dr90_decision_rate_author_cap_boundary` — 10 raw PRs, dominant author holds
  `allowed+1 = 3`; the cap drops exactly the most-recent 1, keeping 9 total, `r = 1/9 = 0.1111` —
  distinguishes "kept all 3" (`0.2`) and "dropped 2" (`0.0`) from the correct off-by-one-free
  answer.
- `test_dr90_decision_rate_eligibility_gate_applied_with_author_history` /
  `..._eligibility_gate_unapplied_without_author_history` — the SAME 14-PR fixture (10 real +
  4 throwaway authors, all single-PR so the cap is a no-op), scored once with an `author_history`
  envelope present (gate drops the 4 throwaway authors, `r=0.4`, `n=10`) and once without
  (`r=0.5714`, `n=14`) — proves the gate (not just the cap) changes the number, and pins both
  `method` values in the same pair (see item 2).
- `test_dr90_decision_rate_eligibility_unmeasured_author_excluded` — an 11th author's
  `eligibility_unmeasured` record is excluded from both numerator and denominator (tri-state
  discipline), leaving exactly `n=10` (still `Measured`, not `insufficient_n`) — pins that the
  exclusion is counted BEFORE the `min_n` check, not after.

**Mutants killed** (backup/mutate/rerun/restore, `diff -q` confirmed byte-identical after every
restore):
1. Cap removed (`kept_idx = tuple(range(len(entries)))`) → `test_..._sock_puppet` and
   `test_..._boundary` both fail with the exact expected wrong values (`0.6`/`0.2` instead of
   `0.3333`/`0.1111`).
2. Gate forced off (`gate_applied = False`) → `test_..._gate_applied_with_author_history` and
   `test_..._eligibility_unmeasured_author_excluded` both fail (`0.5455`/wrong `method` instead of
   the gated values).
3. `min_n` checked against the capped size again (the original bug, reintroduced deliberately) →
   3 tests fail, including the PRE-EXISTING pinned `test_dr90_decision_rate_cohort_window_boundaries`
   (`"6 external PRs...need >= 10"` instead of `"3 external PRs...need >= 10"`), plus both new cap
   oracles.

## 2. Item W-2 — method tag for gate-applied vs. gate-unapplied

`_DR90_METHOD_PRIMARY = constants_v2.METHOD_PRIMARY` (`"primary"`) when an `author_history`
envelope let the eligibility gate run; `_DR90_METHOD_GATE_UNAPPLIED = constants_v2.
PROXY_METHOD_PREFIX + "din3_unapplied"` (`"proxy:din3_unapplied"`) when it could not (matches the
existing `primary` | `proxy:<name>` vocabulary; this is not a D-MISS-4 proxy, so the docstring says
so explicitly). Covered by the same paired gate-applied/gate-unapplied tests in item 1 — both
values are pinned in the same assertions as the rate/`n`, not in a separate test. The fixture's own
DR_90 rows are confirmed unaffected: `scripts/build_fixture_snapshot.py` reproduces the exact same
`records_hash` (`7ac577015106445b778572bf951e2bd4ae3f3ec7b0fe90754835296764a00360`) before and after
this packet's changes — `git diff --stat -- tests/fixtures/snapshot_50/` is empty (sample-200 has
no `author_history` envelope for any repo that reaches DR_90's `Measured` path today, so every
sample-200 DR_90 row that resolves at all keeps the `proxy:din3_unapplied` method it already had
under the pre-packet `_DR90_METHOD` constant).

## 3. Item W-4 / F-004 loader half — real resolvers for the three remaining author_history rates

**The gap the review named (C1e):** `mr_first_rate`, `newcomer_conversion`, `rr90_return_rate` were
still constant `_unmapped(...)` rows, so `derive_cohort_fields`'s own F-004 fix (the collector emits
the documented `"missing"` confidence, never an invented value, when its unmeasured-fraction gate
fires) had no loader-side consumer at all — the gate's `collector_failure` was never actually
emitted for these three rows.

**Fix.** One new factory, `_author_history_rate_resolver(rate_field, count_field, detail_field, *,
unset_detail)`, wired into `INPUT_MAPPINGS` for all three rows:
- confidence `"derived"`: `Measured` from `meta[rate_field]`/`meta[count_field]`, gated by this
  input's own `PARAM_MIN_N` (5 for all three) — including the `count == 0` case (`_ratio` returns
  `None` for a zero-denominator cohort even under `"derived"`), which reads as `insufficient_n`,
  never `collector_failure` (a real, checked, empty cohort is not a collector defect).
- confidence `"missing"` (the collector's own gate fired): `Unmeasured(collector_failure, ...)`
  with the REAL, dynamic, per-repo detail from `meta[detail_field]` (`cohort_unmeasured_detail` for
  `mr_first_rate`; `newcomer_unmeasured_detail`, shared, for the other two — they are gated on the
  same sampled-newcomer-roster fraction in `derive_cohort_fields`, so sharing the field is
  correct, not a copy-paste slip).
- confidence absent (no `author_history` envelope collected for this repo at all): the STATIC
  `unset_detail`, byte-identical to each row's old `_unmapped(...)` string — an over-correction
  guard proving the resolver does not start emitting `collector_failure` with an empty detail, or
  crash, just because the field is genuinely absent.
- any other confidence value: the same "not recognized" fallback `_plain_scalar_resolver` uses.

**Tests** (`tests/derive/test_input_mappings.py`): full path coverage lives on `mr_first_rate`
(the shared factory code) — `test_mr_first_rate` (Measured), `..._insufficient_n`,
`..._zero_cohort_is_insufficient_n_not_collector_failure`, `..._collector_failure_gate_fired`,
`..._unset_when_no_author_history_collected`, `..._unrecognized_confidence`. `newcomer_conversion`
and `rr90_return_rate` get one test each proving their own field-name wiring (Measured + the
missing-gate detail + the unset fallback), since the factory logic itself is already proven once.
`_UNMAPPED_ROWS`/`test_unmapped_row_is_constant` (the "N rows with no landed producer, constant
regardless of content" parametrized test) is reduced from 5 rows to the 2 genuinely still-unmapped
ones (`gr14_ghost_rate`, `dx`) — leaving these three in that table would now be a false claim: they
DO branch on document content.

**Mutant killed:** reverted `mr_first_rate`'s `INPUT_MAPPINGS` row to `_unmapped(...)` → 5 of the
6 mr_first tests fail (the 6th, `..._unset_when_no_author_history_collected`, stays green by
design — that is exactly the unmapped fallback path). Restored, `diff -q` clean.

## 4. Notes items

- **Stale handoff hash pair / RFC number** (`.agents/W3-1/handoff.md`): updated the "RFC / hash"
  section's four `RFC-0002` references to `RFC-0004` (the renumbering this packet's own rebases
  produced was never back-filled into the handoff prose, only the RFC file itself), and the
  `CONSTANTS_HASH` pair from the pre-rebase `d2702ea9...→6610ed06...` to the delivered
  `419091af843a5ab994b23a5776c0f0ce9073ed283b9f540b1d26eca7bcd4d7a9 ->
  748be6e459b0d9eac279233dac75acd5ede7da9711590c84fd0317a84762cb6b` (matches
  `docs/v2/rfcs/RFC-0004-din3-hygiene-constants.md`'s own reviewer-verified pair, and the current
  `cvi.core.constants_v2.CONSTANTS_HASH` on disk). The in-source citations at
  `cvi/collect/github_graphql.py:500,932` already said `RFC-0004` — fixed by the lead at landing
  per `a2297e4` (review item W-3), nothing left to do there.
- **`NewcomerAccountSample` docstring contradiction** (`cvi/verify/detectors.py`): the class
  docstring said the current schema "does not carry" `account_created_at` and named itself "this
  packet's own named carrier... until that schema gains the field" — directly contradicting the
  module docstring 8 lines above, which already says `newcomer_burst` is CLOSED and
  `NewcomerAuthor` now carries the field. Rewrote the class docstring to state the CLOSED status
  and explain why the dataclass is kept anyway (its `all_prs_merged` field is a derived fact, not
  one any stored schema carries directly — `newcomer_accounts_from_cohorts` is what builds real
  instances from a landed `FeatureBundle.cohorts.newcomer_authors`).
- **"One name, two values"** (`meta["dr90_decision_rate"]` vs. the scored `dr90_decision_rate`
  input): renamed the collector's own D-IN-3-adjusted field (computed over `author_history`'s wider
  270 d decision cohort) to `dr90_decision_rate_author_history_cohort` /
  `dr90_decision_pr_count_author_history_cohort` in `cvi/collect/github_graphql.py`
  (`AUTHOR_HISTORY_BUNDLE_FIELDS` and both `derive_cohort_fields` branches that set it), with the
  corresponding 20 assertions in `tests/collect/test_pr_window.py` updated to match. **Picked
  rename over "make the resolver read it"** because the two numbers are genuinely different
  cohorts, not two computations of the same one: `_resolve_dr90_decision_rate` computes its own
  independent [anchor-180d, anchor-90d) cohort straight from `pr_window` (W2-12's D-IN-4
  amendment, deliberately cheaper — no `author_history` fetch required), and reading the
  collector's wider-window field instead would silently regress that decision back to the
  pre-D-IN-4 270 d cohort. Renaming preserves both: the resolver keeps the cheaper, already-wired
  computation, and the collector's own figure stays available, unambiguously named, for any future
  consumer that specifically wants it. Added a paragraph to `derive_cohort_fields`'s docstring
  recording this reasoning, and updated `_resolve_dr90_decision_rate`'s own "review N-3" docstring
  note to reference the new name. No `AUTHOR_HISTORY_BUNDLE_FIELDS` consumer outside
  `cvi/collect/github_graphql.py`/`tests/collect/test_pr_window.py` existed to update — confirmed
  by grep before renaming: every other `"dr90_decision_rate"` occurrence in the tree (`cvi/core/
  params/responsiveness.py`, `cvi/core/constants_v2.py`, `cvi/verify/detectors.py`,
  `cvi/derive/bundle.py`'s `INPUT_MAPPINGS`/`PARAM_MIN_N` lookups, every `bundle.inputs[...]`
  assertion) is the v2 scored input id, a different dict/namespace entirely, and is unaffected.

## 5. Rebase

`W3-1f`'s branch point (`acdf885`) is already an ancestor of local `main` (main has since gained
W4-3, W5-1a, and several lead-log commits, none touching the files this packet edits — confirmed
`git diff --stat W3-1f main -- <this packet's 7 files>` is empty before rebasing). No commits of
my own existed yet to replay, so the rebase onto main's new tip is committed as a pure fast-forward
of the branch pointer once this packet's own commit lands (see §6 for the resulting base sha).

## 6. Verification

- Ruff: `ruff check cvi/ scripts/ tests/` → `All checks passed!`
- Fixture: `scripts/build_fixture_snapshot.py` reproduces `records_hash
  7ac577015106445b778572bf951e2bd4ae3f3ec7b0fe90754835296764a00360` — byte-identical to the
  pre-packet snapshot; `git diff --stat -- tests/fixtures/snapshot_50/` is empty. No regeneration
  commit needed.
- Import graph / constants freeze: `tests/core/test_import_graph.py tests/core/test_frozen.py` —
  43 passed (unaffected; this packet adds no new constant and no new cross-layer import).
- Full suite: `tests/ -n 4 -p no:cacheprovider` → **2244 passed, 1 skipped, 12 warnings** (up from
  main's pre-packet 2,234 passed / 1 skipped by the net +10 tests this packet adds: +5 dr90,
  +6 mr_first_rate, +1 newcomer_conversion, +1 rr90_return_rate, -3 from shrinking the
  `_UNMAPPED_ROWS` parametrization from 5 to 2 rows).

## Round 2 — REWORK (Opus review, 16 PASS / 3 FAIL, bounded to B-1, X8, X11)

Review read: `/tmp/claude-1000/-home-pctablet505-Projects/2a48e84a-2b94-4a2f-8bff-adbe955a4084/scratchpad/reviews/W3-1f-review.md`. Items, records under `.agents/W3-1f/review/`.

### B-1 (Blocker) — checked one number, reported another

Round 1's fix checked `PARAM_MIN_N` against `raw_cohort` but reported `Measured(n=capped)`; `cvi.core.params.curves._apply` re-applies the *same* `min_n` against the reported `n`, so both cap oracles (sock-puppet `r=0.3333 n=6`, boundary `r=0.1111 n=9`) scored `insufficient_n` end to end — the anti-manipulation cap converted a manipulated repo into missing data instead of a corrected rate, exactly reversing what D-IN-3 is for.

**Ruling taken (the review's recommended option (a)):** check and report the SAME number — the CAPPED population — and fix the degenerate case at its source:

- `cvi/core/hygiene.py::apply_author_cap`: `allowed = max(1, int(AUTHOR_CAP_FRACTION * len(items)))`, dropping the old `if allowed <= 0: return ()` branch entirely. A small population now keeps everything **until the cap actually binds on real concentration** — three independent single-PR authors (nobody dominating) keep all three; four PRs all from one login are still capped down to that login's one-item allowance. Fail-before: `test_apply_author_cap_small_population_binds_when_one_author_dominates` (new) and the pre-existing `..._loses_everything` test (renamed/re-pointed) both failed against the pre-fix code with the exact wrong values quoted above; both pass now.
- `cvi/derive/bundle.py::_resolve_dr90_decision_rate`: `min_n` is checked against `cohort` (the capped size, the SAME number `Measured.n` reports), not `raw_cohort`. The detail string for the `insufficient_n` path now names BOTH numbers (`"{cohort} external PRs in the decision cohort ({raw_cohort} before the D-IN-3 per-author cap), need >= {min_n}"`) — diagnostic value for a human reading the bundle, since a capped rate below the floor is a real cap bind, not silently mistaken for "no evidence at all". `raw_cohort` is otherwise dead as a gate; it survives in the return tuple only to be named there.
- The two round-1 cap oracles are RESIZED so the capped population clears `PARAM_MIN_N=10`, per the review's own instruction: sock-puppet is now 20 raw PRs (12 sock, 8 honest; `floor(0.20*20)=4` capped, same 0.3333/0.6 pair as before at 2x scale); cap-boundary is now 20 raw PRs (dominant holds `allowed+1=5`, 15 honest; capped 19, `r=0.0526`).
- **New:** `test_dr90_decision_rate_cap_reduces_below_min_n` — a REAL sock puppet (9 of 12 raw PRs from one login) where the capped population (5) genuinely falls below `min_n` even after the fix — proves the safe failure direction still exists for an actually-too-small capped population, with the dual-count detail.
- **New, per the review's own instruction ("assert the ParamScore, not the resolver's return value"):** both cap-oracle tests now also call `cvi.core.params.dr90(bundle.inputs["dr90_decision_rate"])` directly and assert `measured=True`, `reason=None`, `n=<capped>` — the end-to-end path through the param layer the round-1 tests never exercised, which is exactly what let the original bug ship.

Mutants (backup/mutate/rerun/restore, `diff -q` byte-identical after every restore):
- Reintroduce the `raw_cohort` check → `test_dr90_decision_rate_cap_reduces_below_min_n` fails (reports `Measured(0.0, n=5)` where `Unmeasured(insufficient_n)` is correct) — this is precisely the round-1 bug's own failure mode, now caught.
- Reintroduce `apply_author_cap`'s old `allowed <= 0: return ()` branch → both new `test_hygiene.py` cap tests fail, plus the pre-existing `test_dr90_decision_rate_cohort_window_boundaries` (its 3-independent-author cohort would read `0 external PRs` instead of `3`).

### X8 — the `eligibility_unmeasured` / `history_unmeasured` coupling, decided

**Decision (relax it for DR_90):** `_dr90_author_eligible`'s `measured` now reads ONLY `eligibility_unmeasured`. DR_90's gate needs exactly the account's age and public-event count, both from the per-author eligibility fetch; `history_unmeasured` means the author's PRIOR-MERGE SEARCH failed, a fact only 1.3 (MR_first's "no prior merged PR") needs. `cvi.collect.github_graphql._account_eligibility` is correct to OR both flags — its one call site serves 1.2 AND 1.3 at once — but this bundle-side join serves 1.2 alone, so coupling in `history_unmeasured` was over-strict: it would exclude an author with perfectly good eligibility data from DR_90's population solely because an unrelated MR_first-only fetch failed for them.

New test: `test_dr90_decision_rate_ignores_history_unmeasured_when_eligibility_is_measured` — 10 clean eligible authors, one with `history_unmeasured=True` but `eligibility_unmeasured=False` and genuinely good account data; capped `n` stays 10 (not 9), proving the author is kept. Mutant (re-couple the two flags) → this test fails (`n=9`, `Unmeasured(insufficient_n)` instead of `Measured(n=10)`).

### X11 — the `ctx.referenced` provenance registration, pinned

Added one `bundle.provenance_hash == feature_provenance_hash({<envelope path>: <envelope hash>})` assertion to each of the three new resolvers' own Measured-path test (`test_mr_first_rate`, `test_newcomer_conversion`, `test_rr90_return_rate`) — each fixture's document has exactly one measured field with a real envelope, so the expected hash is computable independently and exactly. Mutant (drop `ctx.referenced[str(path)] = source_hash` in `_author_history_rate_resolver`) → all three assertions fail with a different `provenance_hash`.

### Notes riding along (cheap, explicitly sanctioned by the review's §10)

- **N-1** (stale handoff numbers): this file's suite line below is the freshly-measured one.
- **N-2** (wrong unit in the shared `insufficient_n` detail): `_author_history_rate_resolver` gained a `count_unit` parameter (default `"authors"`, matching 5.2/5.3's own unit); `mr_first_rate`'s wiring passes `count_unit="PRs"` (`mr_first_pr_count` counts PRs, not authors). The two `mr_first_rate` tests naming the old "authors" wording are updated to "PRs".

### Verification (round 2)

- `tests/core/test_hygiene.py`, `tests/derive/test_input_mappings.py`, `tests/collect/test_pr_window.py`, `tests/verify/` — all pass; `tests/derive/ tests/core/ tests/collect/ tests/verify/` together: **879 passed**.
- Ruff (`cvi/ scripts/ tests/`): `All checks passed!`
- Full suite (`tests/ -n 4 -p no:cacheprovider`): **2338 passed, 1 skipped, 12 warnings** on a quiet run. One noisy run showed `tests/store/test_queries.py::test_performance_list_ranked_and_reproject_p95_under_100ms_for_10k_rows` fail once under concurrent load (isolated: `1 passed in 4.06s`) — the SAME U-LOAD SLA-latency flakiness the review's own N-4 already recorded, not attributable to this packet.
- Fixture: unaffected by round 2 as well (no round-2 change touches `sample-200`'s own data path; the resized fixtures are unit-test-only).
