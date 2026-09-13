# W3-1 handoff — author eligibility hygiene

Base: rebased twice during this packet — started from main `d4544ca` (W1-4
accepted), finished on `8a4846c` (W2-12 accepted, landed mid-packet).
Worktree `/home/pctablet505/Projects/cvi-wt-W3-1`, branch `W3-1`.

Dispatch: `docs/v2/05-WORK-PLAN.md` packet W3-1 ("D-IN-3 applied in
derivation of every rate parameter; `newcomer_burst` detector"), with the
W1-4 round-3 review's (`W1-4-r3-review.md`) four follow-ups (F-1..F-4) as
the first items, in the order the coordinator gave them.

## Diffstat (`8a4846c..HEAD`, this packet's own commits)

```
 cvi/collect/github_graphql.py                   | 211 ++++++++++++++----------
 cvi/core/constants_v2.py                        |  18 ++
 cvi/core/hygiene.py                             |  97 +++++++++++ (new file)
 cvi/core/models.py                              |  13 +-
 cvi/derive/bundle.py                            |   4 +-
 cvi/verify/detectors.py                         |  64 +++++--
 docs/v2/rfcs/CHANGELOG.md                       |   1 +
 docs/v2/rfcs/RFC-0004-din3-hygiene-constants.md |  65 ++++++++ (new file)
 tests/collect/test_pr_window.py                 | 138 +++++++++++++++-
 tests/core/test_frozen.py                       |  15 +-
 tests/core/test_hygiene.py                      | 105 ++++++++++++ (new file)
 tests/verify/test_detectors.py                  |  84 +++++++++-
 tests/fixtures/snapshot_50/{expected,manifest,populations}.json | 3 (hash-only)
 13 files changed, 701 insertions(+), 117 deletions(-)
```

## Item 1 — F-001: rows 5.2/5.3 tri-state gate

`_newcomer_cohort_authors` called `is_eligible_account(...)` directly,
bypassing the tri-state `_eligible` check rows 1.2/1.3 use — the round-3
review's exact repro: 9 of 20 newcomer authors with a failed eligibility
fetch (and those 9 are exactly the ones whose first PR really converted)
scored `newcomer_conversion` at `0.0` on the 11 remaining (measured,
non-converting) authors, against a true raw rate of `0.45`.

Fix: extracted the tri-state logic into one shared function,
`_account_eligibility(record, pr_created_at, fallback_account_at)`, which
both `_eligible` (rows 1.2/1.3) and `_newcomer_cohort_authors` (rows
5.2/5.3) call — the "one tri-state check every D-IN-3 rate uses" the
review's fix note asked for. Added the `collector_failed` gate to
`newcomer_conversion`/`rr90_return_rate` (rate AND count), taken over the
**newcomer roster's own fraction** (not the 1.2/1.3 union — the review's
fix note: "the fraction should be taken over that roster, not the
union"), with its own `newcomer_unmeasured_detail` field.

Fail-before (reverted `_eligible` field + gate to the pre-fix direct call,
reran the new test):

```
AssertionError: assert (0.0, 'derived') == (None, 'missing')
FAILED test_derive_cohort_fields_newcomer_conversion_gated_not_zero
```

Exactly reproduces the review's `0.0` bug. Reverted back; new test passes.
Over-correction guard:
`test_derive_cohort_fields_newcomer_conversion_not_gated_below_threshold`
(2 of 10 = 0.20, not gated, unmeasured author simply excluded from the
eligible pool — the pre-existing, correct behaviour for a small failure
share).

## Item 2 — F-002: `_author_status` bucket order

`_author_status` tested `history_unsampled` before the failure flags, so
an author who is both outside the search-cap sample AND has a failed
fetch was counted as a designed exclusion — `authors_excluded_unmeasured`
undercounted real failures (the round-2 pytest envelope's true 45 read as
38).

Fix: test `history_unmeasured`/`eligibility_unmeasured` first, then
`history_unsampled`.

Fail-before:

```
AssertionError: assert 'unsampled' == 'unmeasured'
FAILED test_author_status_unmeasured_takes_priority_over_unsampled
AssertionError: assert (0, 'verified') == (10, 'verified')
FAILED test_derive_cohort_fields_unmeasured_and_unsampled_overlap_still_gates
```

Both exactly reproduce the review's constructed case (10 of 20 authors
with both flags: `authors_excluded_unmeasured` read 0, not 10; the gate
never fired; DR_90 scored 1.0 on n=10 against a true raw 0.5). Reverted
back; both tests pass.

## Item 3 — F-003: pin the tri-state at every call site

Review mutant R4 (`_eligible(...) is True` → `is not False`) re-introduces
the exact round-2 defect and passed all 2059 tests at the time of the
review — no test forced the call site itself to distinguish "is True" from
"is not False" below the 20% gate threshold, which is the common case.

Added `test_eligible_call_site_excludes_unmeasured_below_gate_threshold`:
3 of 20 (0.15, under the gate) unmeasured authors whose PRs were never
decided. Fail-before (applied the exact R4 mutant at the `dr90_kept` call
site):

```
AssertionError: assert (0.85, 'derived') == (1.0, 'derived')
FAILED test_eligible_call_site_excludes_unmeasured_below_gate_threshold
```

Matches the review's own numbers (1.0/n=17 fixed vs 0.85/n=20 under R4)
exactly. Reverted; test passes. Also added
`test_derive_cohort_fields_unsampled_only_author_counted_separately`,
closing review mutant R8 (the accounting classifier silently skipping the
`unsampled` branch) — confirmed by applying that exact mutant and
observing the new test fail (`authors_excluded_unsampled` 0 instead of 1).

## Item 4 — F-004: the confidence encoding

The gate previously emitted confidence `"unmeasured"` — a fifth,
undocumented value outside `scripts/derive.py`'s `verified/derived/
missing/flag` vocabulary. Checked `docs/v2/02-RANKING-DESIGN.md`'s D-MISS-1:
its `Unmeasured.reason` vocabulary (`third_party_gap`, `collector_failure`,
`insufficient_n`, `not_applicable`, `no_population`) is a DIFFERENT layer
from `scripts/derive.py`'s confidence-string vocabulary — `cvi.derive.
bundle`'s own resolvers (`_plain_scalar_resolver` et al.) already establish
the pattern: confidence `"missing"` maps to `Unmeasured(reason=
missing_reason, detail=missing_detail)`. `collector_failure` is used
EXCLUSIVELY as an `Unmeasured.reason` (`grep -c '"collector_failure"'
cvi/derive/bundle.py` = 17, all `Unmeasured(reason=...)`), never as a
`scripts/derive.py` confidence string, anywhere in the codebase.

Fix: the gate now emits confidence `"missing"` (the documented value) for
`dr90_decision_rate[_pr_count]`, `mr_first_rate[_pr_count]`,
`newcomer_conversion[_author_count]`, `rr90_return_rate[_author_count]`.
The real, per-repo detail (`"<k> of <n> [newcomer ]authors unmeasured"`)
is carried in `cohort_unmeasured_detail`/`newcomer_unmeasured_detail`
(confidence `"verified"`, a real checked fact) for a future `cvi.derive.
bundle` resolver to read dynamically — today's `_plain_scalar_resolver`-
style pattern only supports a STATIC `missing_detail` per resolver
definition, so wiring the dynamic detail through is left as part of the
W2-9 wiring request (recorded below), not implemented here (bundle.py is
not this packet's owned file).

All four gated-value tests updated from `(None, "unmeasured")` to
`(None, "missing")`.

## Item 5 — D-IN-3 in derivation

**MR_first / 5.2 / 5.3**: already fully D-IN-3-adjusted via `cvi.collect.
github_graphql.derive_cohort_fields` (W1-4 rounds 1-3 plus this packet's
F-001/F-002/F-003 above). No further change needed.

**1.2 DR_90 via W2-12's new row**: checked main — **W2-12 landed mid-
packet** (`8a4846c docs(v2): lead log — W2-12 accepted`, tip advanced from
`d4544ca`; I rebased onto it). `cvi.derive.bundle._resolve_dr90_decision_rate`
computes DR_90 directly from the `pr_window` envelope (W1-3's 180-day
window) over **every** external PR in the cohort — no per-author
eligibility or cap at all, by explicit design: its own docstring says
"Author eligibility (D-IN-3) is not yet applied -- packet W3-1 -- so today
every external author counts."

**Not implemented here — ownership request, not a gap left silently.**
`cvi/derive/bundle.py` is not an owned file of this packet (W3-1 owns
`cvi/core/hygiene.py` and `cvi/verify/detectors.py` only), and wiring
D-IN-3 into `_resolve_dr90_decision_rate` is a materially different task
from applying it in `cvi.collect.github_graphql`: the `pr_window` envelope
W2-12 reads carries each PR's `author.createdAt` (account age is
checkable) but **no per-author event count at all** — that fact only
exists in the separate `author_history` envelope (W1-4). A correct fix
needs `_resolve_dr90_decision_rate` to also consult `author_history` when
present (via `cvi.core.hygiene.account_eligibility`, now ready) and fall
back to the current all-external-authors behaviour (with its documented
caveat) when it is absent — a resolver-shaped change with its own fixture/
mutation work, not a drop-in call. Recorded as a follow-up for whichever
packet next touches `_resolve_dr90_decision_rate` (W3-1f or W2-9), with
`cvi.core.hygiene.account_eligibility`/`apply_author_cap` ready to consume.

**`substantive_review_rate` (02 §4 row 4.3)**: NOT one of D-IN-3's four
named rate types. D-IN-3's own text: "For rate parameters (merge,
decision, conversion, return)" — MR_first, DR_90, newcomer_conversion,
rr90_return_rate. `substantive_review_rate` measures maintainer
responsiveness to a PR, not the PR author's own legitimacy (the sock-
puppet concern D-IN-3 exists for); a sock-puppet's PR receiving a
substantive review is still a real, measurable maintainer action,
unaffected by the author's eligibility. No change needed; confirmed by
reading D-IN-3's exact text, not inferred.

**Constants relocated (RFC-0004)**: `MIN_ACCOUNT_AGE_DAYS`,
`MIN_PUBLIC_EVENTS`, `AUTHOR_CAP_FRACTION` moved from
`cvi.collect.github_graphql` into `cvi.core.constants_v2`, alongside the
new `cvi.core.hygiene` module (`is_eligible_account`, `account_eligibility`
tri-state, `apply_author_cap` — the exact functions W1-4 wrote, promoted
so `cvi.derive.bundle`'s future resolvers can share them, per the
import-graph rule `cvi.core` may import only `cvi.core` + stdlib).
`cvi.collect.github_graphql` now imports both the constants and the
functions rather than defining local copies. No `BENCHMARK_VERSION` bump
(same reasoning as RFC-0001: the relocated constants have no scoring-path
consumer yet).

## Item 6 — `newcomer_burst` input

`NewcomerAuthor` (`cvi/core/models.py`) gains `account_created_at: str |
None = None`, threaded from the collector's already-fetched per-author
`user.createdAt` (`cvi.collect.github_graphql._newcomer_cohort_authors`
now puts it on the public author dict, which flows into the
`cohort_newcomer_authors` bundle field) through `cvi.derive.bundle`'s
`_cohorts_from` (parse) and `dump_bundle` (serialize) round-trip —
backward compatible: an old bundle document without the field parses with
`account_created_at=None` (unmeasured, never guessed).

`cvi.verify.detectors.newcomer_accounts_from_cohorts(cohorts)` builds real
`NewcomerAccountSample` tuples straight from `Cohorts.newcomer_authors`,
skipping any author whose `account_created_at` is `None`
(unmeasured-never-fires, same rule every other detector input follows).
`detect_newcomer_burst`/`run_detectors` themselves needed no change — they
already worked correctly on hand-built `NewcomerAccountSample` tuples; the
gap was that nothing built one from real data.

Fail-before (`newcomer_accounts_from_cohorts` mutated to always return
`()`):

```
AssertionError: assert 'newcomer_burst' in []
FAILED test_newcomer_burst_fires_through_run_detectors_on_a_real_bundle
KeyError: 'allmerged'
FAILED test_newcomer_accounts_from_cohorts_all_prs_merged_considers_followups
```

Reverted; both pass. New tests: the real-bundle fire, an over-correction
guard (all 5 accounts unmeasured → no fire), the unmeasured-skip test, and
the followups-considered test for `all_prs_merged`.

**Not implemented**: wiring `newcomer_accounts_from_cohorts`'s output into
a live `DetectorInputs` at a real call site (i.e. inside `cvi.core.engine`
or wherever `run_detectors` is eventually invoked) is W2-8/W2-9's wiring
request per the module's own R-10 note — `run_detectors` stays pure,
called by nothing in this packet.

## Item 7 — oracles and mutants

**Packet's own three acceptance oracles** (`docs/v2/05-WORK-PLAN.md`
W3-1), in `tests/core/test_hygiene.py` against the promoted
`cvi.core.hygiene` module directly:

- `test_apply_author_cap_six_first_timer_prs_capped_to_20_percent`: 6
  first-timer PRs from one author among 10 capped to `floor(0.20*10)=2`
  (its two oldest).
- `test_is_eligible_account_89_days_excluded_90_days_included`: the
  89-day boundary.
- The `newcomer_burst` oracle is item 6's real-bundle test above (5
  accounts within 7 days, all merged → flag).

**Mutants killed** (one at a time, `__pycache__` cleared, file restored
and byte-diffed clean after each, full packet file re-run):

| # | Mutant | Killed by |
|---|---|---|
| F-002 order | swap `_author_status`'s bucket order back to `unsampled` first | `test_author_status_unmeasured_takes_priority_over_unsampled`, `test_derive_cohort_fields_unmeasured_and_unsampled_overlap_still_gates` |
| F-001 direct call | `_newcomer_cohort_authors`'s `_eligible` back to a direct `is_eligible_account` call, no gate | `test_derive_cohort_fields_newcomer_conversion_gated_not_zero` |
| R4 (review-named) | `_eligible(...) is True` → `is not False` at the `dr90_kept` call site | `test_eligible_call_site_excludes_unmeasured_below_gate_threshold` |
| R8 (review-named) | `_author_status` skips the `unsampled` branch entirely | `test_derive_cohort_fields_unsampled_only_author_counted_separately` |
| hygiene | `newcomer_accounts_from_cohorts` short-circuited to always return `()` | `test_newcomer_burst_fires_through_run_detectors_on_a_real_bundle`, `test_newcomer_accounts_from_cohorts_all_prs_merged_considers_followups` |

All 5 killed. `diff -q` against the pre-mutation backup confirmed clean
(byte-identical) after every restore.

## RFC / hash

Two RFCs land in this packet (RFC-0001 was written in the prior packet
round; its hash below is the "before" for RFC-0004's own before/after,
unchanged by this packet):

- `docs/v2/rfcs/RFC-0004-din3-hygiene-constants.md`: relocates
  `MIN_ACCOUNT_AGE_DAYS` / `MIN_PUBLIC_EVENTS` / `AUTHOR_CAP_FRACTION`.
  No `BENCHMARK_VERSION` bump (no scoring-path consumer yet, same
  reasoning as RFC-0001). Renumbered from RFC-0002 to RFC-0004 during
  this packet's rebases onto main: RFC-0002 landed as W1-10's own
  StarScout relocation and RFC-0003 as W2-13's not-applicable rules,
  both ahead of this packet in landing order.
- `CONSTANTS_HASH`: `419091af843a5ab994b23a5776c0f0ce9073ed283b9f540b1d26eca7bcd4d7a9`
  -> `748be6e459b0d9eac279233dac75acd5ede7da9711590c84fd0317a84762cb6b`.
- `tests/core/test_frozen.py`: `FROZEN_CONSTANTS_HASH` and `_FROZEN_TABLE`
  updated (three new rows) in the same commit as the constants change.
- `tests/fixtures/snapshot_50/{manifest,populations,expected}.json`
  regenerated via `scripts/build_fixture_snapshot.py` — diff is exactly 3
  files, 1 line each (`constants_hash` x2, `records_hash` x1); every
  bundle file and every `raw_hash` is byte-identical to the pre-change
  fixture (confirmed by `git diff --stat`, no bundle file listed).
- Reviewer: not yet filled (same status as RFC-0001 before its own
  acceptance; the lead fills this at review per that precedent).

## Rebase note

Rebased twice: once cleanly onto `d4544ca` (no conflicts, my own starting
point after W1-4's acceptance), and again onto `8a4846c` after **W2-12
landed mid-packet** — also conflict-free (`git rebase 8a4846c` --
"Successfully rebased", one commit replayed). No file this packet touches
overlaps a file W2-12/W3-2f changed. `W1-10`/`W1-5f`/`W2-13` (named in the
dispatch as concurrent bundle.py editors) had not landed on main as of
this packet's final rebase — checked via `git log main --oneline`.

## Full suite and lint

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../python -B -m pytest tests/ -n 4 -p no:cacheprovider
[...]
2152 passed, 1 skipped, 12 warnings in 10.68s
EXIT=0

$ .../ruff check cvi/ scripts/ tests/
All checks passed!
```

## COULD NOT VERIFY

- Whether `_resolve_dr90_decision_rate`'s "every external author counts"
  gap should be closed by (a) consulting `author_history` when present,
  falling back to the current behaviour when absent, or (b) some other
  design the lead prefers (e.g. extending `pr_window`'s own collector to
  carry an event-count proxy so DR_90 never needs the separate,
  expensive `author_history` fetch at all) — an owner/lead design
  decision, not something this packet's oracles settle.
- Whether `cvi.verify.detectors.run_detectors`' real-data wiring (a live
  call site passing a populated `DetectorInputs`) is scheduled for W2-8,
  W2-9, or a new packet — carried from W3-2's own handoff, unchanged by
  this packet.
- `star_burst`/`ef_domain_mismatch`'s ownership requests (StarScout
  lockstep penalty, top-email-domain share) — untouched, not named in
  this packet's dispatch.
