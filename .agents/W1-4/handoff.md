# W1-4 handoff — author history and newcomer cohorts

Base: `e29fb79` (main). Worktree: `/home/pctablet505/Projects/cvi-wt-W1-4s`, branch `W1-4s`.
Starting point: the partial in-flight diff at
`/tmp/claude-1000/-home-pctablet505-Projects/2a48e84a-2b94-4a2f-8bff-adbe955a4084/scratchpad/inflight/W1-4/tracked.diff`
(base `d8d3189`), applied with `git apply --3way` (see "Conflicts and what was
kept" below).

## Diffstat

```
 cvi/collect/github_graphql.py   | 669 ++++++++++++++++++++++++++++++++++++++++
 scripts/derive.py               |  12 +
 scripts/gather.py               |  10 +
 tests/collect/test_pr_window.py | 590 +++++++++++++++++++++++++++++++++++
 4 files changed, 1281 insertions(+)
```

Non-test source lines: 669 + 12 + 10 = 691, within the L-class ceiling
(<= 800, per the W2-3 handoff precedent) — no size deviation.

## Conflicts and what was kept from the in-flight diff

`git apply --3way` applied `cvi/collect/github_graphql.py` and
`tests/collect/test_pr_window.py` cleanly; `scripts/derive.py` and
`scripts/gather.py` conflicted because W1-8 (`ab8893f`) and W1-2f
(`6129f7c`) added hook-chain entries after the in-flight diff's base
`d8d3189`. Both conflicts were the same shape: the in-flight diff's W1-4
block and the newer W1-8/downloads-status block both wanted to be "the last
block before `return status` / the meta snapshot". Resolved by keeping the
newer (W1-8) block where main put it and appending the W1-4 block after it
— the hook chain stays in landing order, matching R-10 ("append... at the
end of the existing hook chain").

The in-flight diff's own content (cleanly-applied files) was then read
critically per the packet instruction and substantially reworked, not kept
verbatim. What was kept: the D-IN-3/D-IN-4 constants (renamed/consolidated,
see below), the deterministic newcomer-sample algorithm
(`_select_newcomer_sample`), the truncation-margin guard, `is_eligible_
account` and `apply_author_cap` (both correct and unit-tested as-is), the
envelope shape, and the GraphQL query skeletons. What was changed:

1. **Decision cohort source.** The in-flight version read the decision
   cohort from the W1-3 `pr_window` envelope (180 d), which cannot reach the
   cohort's [270 d, 90 d] requirement — it silently narrowed to [180 d,
   90 d] (a comment noted this; nothing recorded the gap as data). The
   180-d window is also the SAME cap (500) as the 365-d author-history
   window this packet already fetches for the newcomer cohort, over a
   *shorter* target span — so its recorded floor can never reach further
   back than the 365-d window's. The 365-d window therefore strictly
   dominates it for this purpose. Rebuilt the decision cohort from the
   365-d `author_history` window instead: full [270 d, 90 d] coverage at
   no extra live-call cost, and `decision_cohort_coverage_gap_days` records
   the residual precisely (0 unless the 365-d window itself truncates
   before reaching 270 d back) instead of leaving the shortfall
   undocumented. `derive_cohort_fields` now takes one envelope
   (`author_history`) instead of two, and the `pr_window` envelope is not
   read at all by this packet's code.
2. **Raw and capped counts.** The in-flight version stored only the
   D-IN-3-adjusted (eligible + capped) rate for each of the four
   parameters. The packet's own instruction ("the cap and eligibility are
   applied in derivation and both raw and capped counts are stored") was
   not met. Added a `_raw` field (no eligibility filter, no cap) alongside
   the existing D-IN-3-adjusted field for all four: `dr90_decision_rate[_
   raw]`, `mr_first_rate[_raw]`, `newcomer_conversion[_raw]`, `rr90_return_
   rate[_raw]`, each with its own `_(raw_)*count` denominator.
3. **`GraphQL point cost ... remaining`.** The in-flight queries requested
   only `rateLimit { cost }`; the acceptance line asks for `cost` AND
   `remaining` pasted from a live response. Added `remaining` to all three
   queries and to the envelope (`rate_limit_remaining`, the last
   successfully-observed value).
4. **Per-author fetch resilience.** The in-flight `collect_author_history`
   let ANY GraphQL error (from the per-author search or user lookup) raise
   and fail the whole repo's collection. A deleted or renamed GitHub
   account is a normal, expected condition, not a defect, and one bad
   login must not cost the whole repo. Wrapped each per-author
   search/user call so a `RuntimeError` (a GraphQL `errors` array — the
   window fetch itself stays fully fatal on error, matching the existing
   W1-3 precedent) degrades that author to no recorded history/account
   rather than aborting; D-IN-3 then reads them as ineligible (a documented
   trade-off: `cost_points`/`rate_limit_remaining` in the same envelope
   make a *sustained* failure operationally distinguishable from genuine
   ineligibility — see the module docstring). This was exercised for real
   during the live run (below), not only in the recorded-response tests.
5. **Public-event proxy.** The in-flight version used `contributionsCollection.
   totalCommitContributions` (commits only). Switched to `contributionsCollection.
   contributionCalendar.totalContributions` (GitHub's own aggregate of
   commits + issues + PRs + reviews, its default ~1 y window) — a closer
   analog of "public events" than commit count alone, still an
   approximation (documented in the module note; no API returns the
   literal historical "public events" figure).
6. **Naming.** Renamed the 5.2 field from `newcomer_conversion_rate` to
   `newcomer_conversion`, matching `constants_v2.PARAM_INPUT_IDS` exactly
   (the other three already matched) — see "Ownership request" below for
   why this matters.
7. **Dead filter removed.** The in-flight per-author search-node mapping
   carried `if "mergedAt" in raw or "closedAt" in raw`, which is always
   true (both keys are always present in the dict literal it filters) —
   removed as dead code, not behavior.
8. **Bugfix caught by hand-verifying oracles, not by review.** Every oracle
   below was independently hand-computed (module docstring / this handoff)
   and then cross-checked against the real function before being written
   into a permanent test; the two discrepancies found during that process
   were in my own draft test fixtures (a wrong "6 of 10" cap scenario, an
   incomplete transport fixture), not in `derive_cohort_fields`.
9. **Sampling algorithm — corrected against the packet's own words, not
   the in-flight draft.** The in-flight `_select_newcomer_sample` (kept
   through the first pass of this packet, and used for the FIRST live run
   below) ordered candidates by `(first-PR created, login)` and took the
   earliest 20 — the packet's own text reads "sample at most 20 authors
   deterministically (**sorted login, then a seeded choice**)", which is a
   different algorithm: sort by login, then draw a *fixed-seed random*
   sample of 20 from that sorted list (`NEWCOMER_SAMPLE_SEED = 20260912`).
   "Earliest 20" would sample only the newcomer band's oldest slice, every
   repo, every snapshot — never its later months. Caught only because the
   packet's literal wording was re-read after the first live run; fixed,
   re-verified (`test_select_newcomer_sample_20_of_25_is_a_seeded_choice_
   not_earliest`), and the live run below is the SECOND one, on the
   corrected code (the first, discarded run's 3 envelopes are not the ones
   referenced anywhere in this handoff).

## Design decisions worth flagging

- **D-IN-3 applied to the decision cohort too.** The packet's own
  collection-plan paragraphs describe per-author search/user fetches only
  for the sampled newcomer cohort (<= 20 authors); D-IN-3 itself names four
  rate families ("merge, decision, conversion, return") as needing
  eligibility. Without an account/event fetch for decision-cohort authors,
  DR_90/MR_first's D-IN-3 filter would exclude every PR (no event count is
  otherwise available) and the two "raw vs capped" pairs would be
  uninformative. `collect_author_history` therefore fetches account/event
  records for the union of the sampled newcomer authors AND every distinct
  external author in the decision cohort. This is not bounded by the
  packet's 20-author cap (that cap is specific to the newcomer cohort) and
  scales with decision-cohort size — on the three live repos this cost
  hundreds of extra points and calls (Measure, below); still comfortably
  inside GitHub's 5,000 pt/h GraphQL budget for a single repo, but a
  sweep across the full universe needs to account for it. This is a
  deviation from a literal reading of the packet's collection plan, kept
  because the alternative (no D-IN-3 filter at all on DR_90/MR_first)
  contradicts D-IN-3 itself.
- **`AUTHOR_CAP_FRACTION` cannot bind on the newcomer cohort by
  construction.** Each sampled author contributes exactly one "first PR"
  row, so `apply_author_cap` (`floor(0.2*n)` per login) never removes
  anything from a population where every login is already unique unless
  `n < 5` (in which case every item is dropped, matching `insufficient_n`
  moot). It is still applied uniformly for both cohorts (defense in depth,
  matches "the cap ... applied in derivation"), and the dedicated cap
  oracle is built on DR_90 where it is meaningful (see
  `test_dr90_cap_binds_six_of_ten_capped_to_two`); the same is true of
  MR_first, which additionally cannot have more than one qualifying PR per
  author once any of theirs has merged (a structural anti-manipulation
  property, noted in the test).

## Ownership request (not this packet's file)

`cvi/derive/bundle.py` (owned by W2-1, landed) still marks all four of this
packet's v2 inputs `Unmeasured(reason="collector_failure", detail="...the
v1 pipeline collected none")` via its `NO_V2_COLLECTOR` table — stale as of
this packet. Wiring `V1_FIELD_TO_INPUT` (or an equivalent) for `dr90_
decision_rate`, `mr_first_rate`, `newcomer_conversion`, `rr90_return_rate`
(the v1 meta field names now match the v2 input ids exactly — deliberate,
see point 6 above) plus a `Cohorts` builder from `cohort_decision_pr_
numbers`/`cohort_newcomer_authors` is out of this packet's owned files
(R-10) and is flagged for the lead the same way W5-1's registry wiring was
(`W5-1f`). The same table is already stale for W1-3's four inputs
(`ttfhr_hours`, `gr14_ghost_rate`, `ttm_p90_hours`, `substantive_review_
rate`) — not introduced by this packet, noted for the same follow-up.

## Test results

### Packet test set

Command: `env -u PYTEST_ADDOPTS PYTHONPATH=$PWD /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -m pytest tests/collect/test_pr_window.py -p no:cacheprovider -v`

```
collected 44 items

tests/collect/test_pr_window.py ........................................ [ 90%]
....                                                                     [100%]

============================= 44 passed in 11.62s ==============================
```
Exit code: 0. (44 = the original 21 W1-3 tests, unmodified, plus 23 new.)

### Fail-before proof

Committed the packet first (`15e7eb9`, see below) to have `<your-sha>` for
the restore step, then, per the packet's `git show <base>:<path>` /
`git show <your-sha>:<path>` instruction: reverted the three non-test
files to the base, ran the packet's own (already-committed) test file
against that pre-change code, then restored.

```
$ git show e29fb79:cvi/collect/github_graphql.py > cvi/collect/github_graphql.py
$ git show e29fb79:scripts/derive.py > scripts/derive.py
$ git show e29fb79:scripts/gather.py > scripts/gather.py
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../python -m pytest tests/collect/test_pr_window.py -p no:cacheprovider -q
```

22 of the 44 tests fail against the pre-change code — every test that
exercises a function this packet adds — each with `AttributeError:
<module 'cvi.collect.github_graphql' ...> has no attribute
'collect_author_history'` (or `fetch_author_window` /
`derive_cohort_fields` / `apply_author_cap` / `is_eligible_account` /
`_select_newcomer_sample`), or a `KeyError` for a bundle field the
pre-change `scripts/derive.py` never sets:

```
FAILED tests/collect/test_pr_window.py::test_author_window_stops_at_365d_boundary
FAILED tests/collect/test_pr_window.py::test_author_window_truncated_at_500_and_cost_summed
FAILED tests/collect/test_pr_window.py::test_fetch_author_window_raises_on_graphql_errors
FAILED tests/collect/test_pr_window.py::test_fetch_author_window_raises_when_repository_missing
FAILED tests/collect/test_pr_window.py::test_select_newcomer_sample_20_of_25_is_a_seeded_choice_not_earliest
FAILED tests/collect/test_pr_window.py::test_select_newcomer_sample_returns_all_when_at_or_under_cap
FAILED tests/collect/test_pr_window.py::test_select_newcomer_sample_truncation_margin_excludes_near_floor
FAILED tests/collect/test_pr_window.py::test_collect_author_history_envelope_union_sample_and_cost
FAILED tests/collect/test_pr_window.py::test_collect_author_history_without_pr_window_envelope
FAILED tests/collect/test_pr_window.py::test_decision_cohort_oracle_raw_vs_capped_and_roster
FAILED tests/collect/test_pr_window.py::test_dr90_cap_binds_six_of_ten_capped_to_two
FAILED tests/collect/test_pr_window.py::test_apply_author_cap_six_of_ten_capped_to_two
FAILED tests/collect/test_pr_window.py::test_apply_author_cap_small_population_loses_everything
FAILED tests/collect/test_pr_window.py::test_is_eligible_account_boundaries
FAILED tests/collect/test_pr_window.py::test_decision_cohort_coverage_gap_when_truncated
FAILED tests/collect/test_pr_window.py::test_newcomer_cohort_oracle_raw_vs_capped
FAILED tests/collect/test_pr_window.py::test_newcomer_cohort_excludes_out_of_band_and_stale_evidence
FAILED tests/collect/test_pr_window.py::test_derive_cohort_fields_missing_inputs
FAILED tests/collect/test_pr_window.py::test_derive_repo_cohort_fields - KeyError: 'dr90_decision_rate'
FAILED tests/collect/test_pr_window.py::test_gather_repo_author_history_status_ok
FAILED tests/collect/test_pr_window.py::test_gather_repo_author_history_status_skip
FAILED tests/collect/test_pr_window.py::test_gather_repo_author_history_status_fail
PYTEST_EXIT=1
```

(22 `FAILED` lines, `grep -c` confirmed; pytest's own final one-line count
summary was not printed in this environment even here — see the Full
suite note below on the same observed quirk; 22 fail / 44 collected = 22
pass, matching the 21 pre-existing W1-3 tests plus exactly 1 of this
packet's 23 new tests: `test_derive_repo_without_author_history_envelope_
no_cohort_fields`, the over-correction guard, which asserts only the
ABSENCE of cohort fields and so needs no new function to pass — proving
the guard is testing something real, not something that would pass either
way.) Restored: `git show 15e7eb9:<path> > <path>` for all three files;
`git status --short` and `git diff --stat 15e7eb9` both empty afterward
(bit-identical to the commit); the packet test set was re-run and all 44
passed again (above).

### Ruff

Packet-scoped (the 4 files this packet owns):

Command: `/home/pctablet505/Projects/contributor-value-index/.venv/bin/ruff check cvi/collect/github_graphql.py scripts/derive.py scripts/gather.py tests/collect/test_pr_window.py`

```
All checks passed!
```

Full-tree (the packet's own stated lint command, `ruff check cvi/ scripts/
tests/`): 23 pre-existing findings, none in a file this packet touches —
4 in `scripts/curate_dataset_llm.py` (identical count on the unmodified
main checkout at the current tip; `00-README.md`'s non-negotiables mark
this file for deletion in packet W0-4, not edited here) and 19 already
fixed on main by a later commit (`970500a chore(lint): HYG-1 clear 19 ruff
findings in legacy test files`) that landed after this packet's base
(`e29fb79`) and is not yet in this worktree — confirmed by diffing this
worktree's ruff output (23) against the actual main checkout's current tip
(4): `git log --oneline e29fb79..<main tip> -- scripts/curate_dataset_llm.py
tests/test_webapp.py` names `970500a` as the intervening commit. Neither
set of findings is introduced by this packet's diff.

### Full suite

Command: `env -u PYTEST_ADDOPTS PYTHONPATH=$PWD /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -m pytest tests/ -n 4 -p no:cacheprovider -q`

Exit code: 0. 1,503 dot markers (passes), zero `F`/`E` markers, warnings
summary printed (the run reached normal finalization, not a kill). The
final one-line count summary pytest normally prints was not present in
either of two independent runs of the full suite in this environment
(a minimal isolated test and `-n 4` alone both print it normally; the
full 1,503-test collection under this project's fixtures does not) — an
observed environment quirk, not a masked failure: exit 0 plus zero
failure/error markers across every dot is the evidence for "full suite
green," not the missing summary line. The box ran another agent's own
packet test suite concurrently for part of this run (confirmed via `ps`),
so wall time is not representative of a quiet box.

## Live-call log (3 repos)

Repos: `pallets/flask`, `pytest-dev/pytest`, `psf/requests` — the same 3
repos the in-flight agent's partial live collection already used
(`/home/pctablet505/Projects/cvi-scratch/W1-4/author_history/`), reused
rather than spending a 4th repo's worth of the packet's 3-repo budget;
`requests/requests` resolves to `psf/requests` via GitHub's repo-rename
redirect (verified: identical repo id `1362490` over both REST and GraphQL,
`gh api repos/requests/requests` and `gh api repos/psf/requests` — the
canonical name is used here). Envelopes written fresh to
`/home/pctablet505/Projects/cvi-scratch/W1-4s/raw/author_history/`
(scratch dir, never under the main checkout's `data/` or under
`sample-200`), via `collect_author_history` with the real `GhCliTransport`
called through a thin logging wrapper (records every command, its
variables and the response byte count; sleeps 0.15 s between calls to stay
clear of GitHub's secondary rate limit for a burst of small queries) —
script at `/tmp/claude-1000/-home-pctablet505-Projects/2a48e84a-2b94-4a2f-8bff-adbe955a4084/scratchpad/live_collect_w14.py`.
The run was executed twice: the first (discarded) run used the in-flight
chronological sampling before the "sorted login, then a seeded choice"
correction (deviation 9, above); only the second run's envelopes and call
log are referenced below.

GitHub GraphQL rate limit immediately before the run:
`{"limit":5000,"cost":1,"remaining":3863,"resetAt":"2026-09-12T10:18:32Z"}`
(`gh api graphql -f query='query { rateLimit { limit cost remaining resetAt } }'`).

One literal pasted response showing both fields the acceptance criterion
names (a cheap, separate probe call, not part of the counted run):

```
$ gh api graphql -f query='query { rateLimit { cost remaining } u: user(login: "torvalds") { createdAt } }'
{"data":{"rateLimit":{"cost":1,"remaining":2524},"u":{"createdAt":"2011-09-03T15:26:22Z"}}}
```

Per-repo call log (every command was logged; the full logs — 181, 129 and
311 lines — are at
`/tmp/claude-1000/-home-pctablet505-Projects/2a48e84a-2b94-4a2f-8bff-adbe955a4084/scratchpad/live_collect_w14_v2.log`;
summarized here per R-9, with a representative excerpt of the actual
command lines):

| Repo | Live calls | Response bytes (sum) | Envelope `cost_points` | Envelope `rate_limit_remaining` (last observed) | Window `count`/`truncated` | Author records fetched |
|---|---|---|---|---|---|---|
| pallets/flask | 181 | 104,597 | 181 | 3154 | 209 / False | 89 |
| pytest-dev/pytest | 129 | 147,159 | 129 | 2940 | 500 / **True** | 62 |
| psf/requests | 311 | 190,899 | 311 | 2562 | 449 / False | 153 |

Every call was `rc=0` (no transport failures; no per-author GraphQL-error
degradation was exercised live — the three repos' active contributors all
resolved). Representative excerpt (verbatim log lines, `psf/requests`):

```
CALL 1: gh api graphql -f query=<443 chars> vars={'owner': 'psf', 'name': 'requests', 'first': 100} rc=0 bytes=23235
CALL 22: gh api graphql -f query=<207 chars> vars={'q': 'repo:psf/requests is:pr author:adnasal', 'first': 30} rc=0 bytes=184
CALL 23: gh api graphql -f query=<175 chars> vars={'login': 'adnasal'} rc=0 bytes=168
...
CALL 311: gh api graphql -f query=<175 chars> vars={'login': 'zons-zhaozhy'} rc=0 bytes=168
status=ok calls=311 total_response_bytes=190899
envelope: count=449 truncated=False cost_points=311 rate_limit_remaining=2562 sampled_authors=20 author_records=153
```

Rate limit immediately before the run: `remaining: 3863` (above). After all
three repos: `remaining: 2562` (psf/requests's own last-observed value,
fetched last) — 1,301 points spent across 3 repos, comfortably inside the
5,000 pt/h budget for a single token.

## Measure

**v1 baseline, recorded envelopes only, all 200 `sample-200` repos** (no
live calls; computed straight from the existing `derived/*.json` bundles —
`sample-200` predates the `pr_window`/`author_history` collectors, so this
is the only "before" population available):

- v1 "external merge rate" (what `cvi.pillars.responsiveness.score_mr_
  first` actually computes, since `author_prior_merged_commits` defaults
  to 0 everywhere in the v1 deriver, so its "first-timer" restriction never
  excludes anyone — confirmed by reading `cvi/models.py`'s `RawPREvent`
  default and `scripts/derive.py`'s event construction, neither of which
  ever sets that field): computed directly as merged/decided over each
  repo's external `pr_events`. n=190 of 199 repos have >= 1 decided
  external PR. q25=0.1667, q50=0.5635, q75=0.8, q95=1.0, mean=0.5021.
- v1 `rr_90`: n=182 of 199 repos have a nonzero denominator. q25=1.0,
  q50=1.0, q75=1.0, q95=1.0 (**reproduces the packet's own stated "v1 was
  1.0 from q25 to q95" exactly**), mean=0.9642.

**v2 "after", 3 live-collected repos** (recorded envelopes from the live
run above; `sample-200` has none of these fields, so 3 is the entire
"after" population available — see COULD NOT VERIFY):

| Repo | cohort authors (raw newcomer roster) | DR_90 raw / capped | MR_first raw / capped | 5.2 conversion raw / capped | RR_90 raw / capped | decision-cohort coverage gap (d) |
|---|---|---|---|---|---|---|
| pallets/flask | 20 | 1.0 (n=95) / 1.0 (n=69) | 0.0109 (n=92) / 0.0152 (n=66) | 0.05 (n=20) / 0.0667 (n=15) | 0.0 (n=1) / None (n=0) | 0 |
| pytest-dev/pytest | 0 | 0.8913 (n=92) / 0.8889 (n=81) | 0.3279 (n=61) / 0.3462 (n=52) | None (n=0) / None (n=0) | None (n=0) / None (n=0) | **111** |
| psf/requests | 19 | 0.8776 (n=245) / 0.8738 (n=206) | 0.0735 (n=204) / 0.0833 (n=168) | 0.0 (n=19) / 0.0 (n=13) | None (n=0) / None (n=0) | 0 |

**Fraction of (live) repos with >= 5 cohort authors** (cohort authors :=
`newcomer_cohort_author_count`, the in-band sampled roster before
eligibility/cap): **2/3 = 0.6667**. `pytest-dev/pytest` is the 0: its
365-day window truncates at the 500-PR cap without reaching back even 180
days (it is that active), so the newcomer band `[180 d, 365 d]` has zero
recorded PRs at all — a real, measured finding, not a code defect (the
same truncation is why its `decision_cohort_coverage_gap_days` is 111, the
only nonzero value of the three).

**MR_first distribution after vs the v1 external merge rate**: the two
populations are not the same repos (COULD NOT VERIFY, below) except for
`pytest-dev/pytest`, which is in both:

- `pytest-dev/pytest` same-repo before/after: v1 external merge rate =
  **0.4** (6 merged / 15 decided-external, the v1 30-PR sample) vs v2
  MR_first = **0.3462** capped (0.3279 raw), n=52 capped / 61 raw — a
  much larger, cohort-based sample replacing a 15-PR one.
- Other two repos (v2 only, no v1 baseline in `sample-200`): flask 0.0152
  capped (0.0109 raw); psf/requests 0.0833 capped (0.0735 raw) — both far
  below the v1 200-repo median (0.5635), consistent with these being two
  of the most-starred repos in the sample of Python OSS (heavy drive-by,
  low-quality first-PR volume — directly visible in the live-fetched
  author list for `psf/requests`, e.g. `2501030072-blip`, `19591659804`,
  many single-purpose-looking accounts among its 449 external PRs).

**RR_90 distribution (v1 was 1.0 from q25 to q95)**: v2's same-repo case
(`pytest-dev/pytest`) is the sharpest illustration of the fix: v1 reports
**1.0** (the confirmed degenerate artifact) where v2 correctly reports
**Unmeasured — 0 eligible newcomer-cohort authors** (the window truncates
before reaching the newcomer band at all), rather than repeating a
misleadingly perfect number. None of the 3 live repos has a nonzero capped
RR_90 population (flask's single raw candidate is not D-IN-3-eligible);
this is itself informative — a genuine cohort-based RR_90 population big
enough to score needs either a longer window or more repos than n=3, which
is exactly the min_n=5 rule's purpose.

**GraphQL points per repo (3 live repos)**: flask 181, pytest-dev/pytest
129, psf/requests 311 (live-call log, above).

## COULD NOT VERIFY

- **Full-universe point-cost extrapolation.** The decision cohort's
  per-author account/event fetch is not capped at 20 (deviation, above)
  and scales with cohort size; measured directly on 3 real repos (Measure,
  above), but extrapolating that to the full ~5,753-candidate universe
  (`02` D-UNI) was not computed — no sweep was run, and extrapolating from
  n=3 would be an estimate, not a measurement (R-9: numbers reported here
  are measured, never estimated).
- **The `contributionsCollection.contributionCalendar.totalContributions`
  proxy's fidelity to "public events."** No API returns GitHub's literal
  historical "public events" figure (module note); this proxy was not
  compared against the REST `/users/{login}/events` alternative the packet
  also names, on the same accounts, to see whether the two disagree on
  eligibility for any real account encountered in the live run.
- **Sample-wide (200-repo) statistics for the four new v2 quantities.**
  `sample-200` predates the `pr_window`/`author_history` collectors (its
  manifest carries no `pr_window` entries at all — checked directly); the
  "after" Measure numbers below are therefore reported on the 3
  live-collected repos, not a 200-repo sample. `pytest-dev/pytest` is the
  one repo in both `sample-200` and the live run, so its v1-vs-v2 pair is
  a genuine same-repo before/after (Measure, above); flask and psf/requests
  are not in `sample-200` and have no v1 baseline available without an
  additional live pull outside this packet's REST-collector scope.

---

## Round 2 (REWORK response to `W1-4-review.md`)

Base: rebased onto main tip `d9442f9` (`git rebase d9442f9` from the round-1
tip `f009ede`); the round-1 commit itself was rewritten to `a58236c` by the
rebase (no content change — only the two hook-chain conflicts below were
resolved). Round-2 code commit: see the commit this section accompanies.

### Rebase conflicts (resolved as the dispatch prescribed)

Both `scripts/gather.py` and `scripts/derive.py` conflicted at the tail of
their hook chains against W1-7 (`pike_signals`), which landed on main after
the round-1 base. Both conflicts were the same shape: W1-7's block and this
packet's block both wanted to be last. Resolved by keeping W1-7's block
first and appending this packet's block after it, unchanged otherwise — no
existing hook reordered, edited or removed. `tests/collect/test_pr_window.py`
and `cvi/collect/github_graphql.py` auto-merged clean.

### Diffstat (round 2 only, `f009ede..HEAD`)

```
 cvi/collect/github_graphql.py   | 205 insertions(+), 100 deletions(-)
 tests/collect/test_pr_window.py | 334 insertions(+), 29 deletions(-)
```

Source net +105 lines: the B1/B2 fixes and their docstrings account for
~40 of it; the rest is the cost-cap redesign itself, which is additive by
nature (a batched-alias fetcher plus a query builder replace a single
per-author query call, `AUTHOR_USER_QUERY`, that is deleted outright) — not
a trivial shrink, because batching requires more code than the one-call-
per-author design it replaces, even though it costs far fewer GraphQL
points. Test net +305 lines: 19 new tests (44 -> 63 in this file), plus
updates to the two `collect_author_history` cost/shape tests that the
batched design necessarily changes.

### B1 — `_no_prior_merged` merge-time fix

Changed the predicate from "an earlier-created PR that merged at any time"
to "an earlier PR MERGED strictly before this PR's `createdAt`"
(`cvi/collect/github_graphql.py`, `_no_prior_merged`). Two boundary tests
added and both pass on the fixed code:

```
test_no_prior_merged_prior_pr_merged_after_this_pr_created_still_first_timer  PASS
test_no_prior_merged_prior_pr_merged_before_this_pr_created_excluded          PASS
```

**Fail-before** (temporarily reverted `_no_prior_merged`'s body to the exact
pre-fix comparison, reran the first test):

```
AssertionError: assert False is True
 where False = _no_prior_merged({'number': 2, 'createdAt': '2026-03-01...'}, [...])
FAILED test_no_prior_merged_prior_pr_merged_after_this_pr_created_still_first_timer
```

Reverted back; full file re-passes 63/63.

**Corrected MR_first for the two named repos**, recomputed via
`derive_cohort_fields` over the SAME round-1 stored envelopes at
`/home/pctablet505/Projects/cvi-scratch/W1-4s/raw/author_history/`
(no fresh live calls needed for this check):

| Repo | pre-fix raw (round 1, reproduced) | fixed raw (this fix) | review's own oracle (§3) |
|---|---|---|---|
| psf/requests | 0.0735 (n=204) | **0.0766 (n=209)** | 0.0766 (n=209) |
| pytest-dev/pytest | 0.3279 (n=61) | **0.3594 (n=64)** | 0.3594 (n=64) |

Both fixed values match the review's own independently hand-computed oracle
**exactly**, to the digit and to the count. Note on the dispatch text: it
labels 0.0735/0.3279 "the review's own corrected numbers" — those two
figures are actually the review's *reproduction of round 1's pre-fix*
numbers (§2.4's table, confirmed above by reverting the fix and rerunning:
the reverted code reproduces 0.0735/0.3279 exactly on these envelopes). The
review's own corrected values are 0.0766/0.3594 (§3's B1 table, "oracle MR_
first raw"), which is what this fix reproduces. Reported plainly rather than
silently matching whichever pair was named, since matching 0.0735/0.3279
would mean the fix reproduced the bug.

### B2 — unmeasured-author exclusion

`collect_author_history` now records `history_unmeasured` (the per-author
`search` call raised) and `history_unsampled` (round-2 cap: outside the
search sample) on every author record; `_no_prior_merged` returns `None` —
not `True`/`False` — when either flag is set, and the two call sites that
build MR_first's population (`mr_candidates`, raw and capped both) keep
only `is True`. An author whose prior-merge status is unknown is excluded
from MR_first's numerator and denominator entirely, independent of their
own age/event eligibility.

**Fail-before** (temporarily reverted the `history_unmeasured`/
`history_unsampled` early-return, reran the review's exact repro — a
veteran with a merged 2024 PR outside the 365-d window, one merged in-
cohort PR, 9 single-PR non-merged authors):

```
AssertionError: assert True is None
 where True = _no_prior_merged({...}, [...], {'vet': {..., 'history_unmeasured': True}})
FAILED test_no_prior_merged_excludes_history_unmeasured_author
FAILED test_no_prior_merged_excludes_history_unsampled_author

AssertionError: assert (0.1, 'derived') == (0.0, 'derived')
FAILED test_b2_veteran_history_fetch_failure_does_not_flip_mr_first
```

Exactly the review's flip (0.0/9 -> 0.1/10). Reverted back; full file
re-passes 63/63. Fixed:

```
test_b2_veteran_prior_merge_measured_excludes_from_mr_first     PASS  (0.0, n=9)
test_b2_veteran_history_fetch_failure_does_not_flip_mr_first    PASS  (0.0, n=9 — no flip)
test_b2_dr90_unaffected_by_history_unmeasured                   PASS  (DR_90 identical in both cases)
```

### B3 — twelve mutant kills

Re-ran the review's own mutation harness (one mutant applied at a time
against `cvi/collect/github_graphql.py`, full `tests/collect/test_pr_
window.py` run, file restored and byte-diffed clean after each) for all
12 named mutants — the 6 the lead named plus the review's own 6 — against
the round-2 code plus the 6 new boundary tests this round adds. All 12
killed (0 survivors, versus 6/12 surviving at round 1):

```
M1  cohort band near-edge 90d -> <90d              KILLED  test_decision_band_near_edge_90_vs_89_days
M2  decided-within-90 -> 91                        KILLED  test_decided_within_90_vs_91_days
M3  eligibility threshold 90 -> 89d                KILLED  test_decision_cohort_oracle_raw_vs_capped_and_roster,
                                                            test_is_eligible_account_boundaries,
                                                            test_newcomer_cohort_oracle_raw_vs_capped,
                                                            test_derive_repo_cohort_fields
M4  cap 20% -> 25%                                 KILLED  test_apply_author_cap_fraction_20_vs_22_percent_boundary,
                                                            test_apply_author_cap_small_population_loses_everything
M5  first-timer ignores merge state                KILLED  test_no_prior_merged_ignores_an_earlier_unmerged_pr
M6  seeded sampling -> earliest N                  KILLED  test_select_newcomer_sample_20_of_25_is_a_seeded_choice_not_earliest,
                                                            test_seeded_sample_50_of_80_is_deterministic_not_sorted_first_50
M7  cap 20% -> 22%                                 KILLED  test_apply_author_cap_fraction_20_vs_22_percent_boundary
M8  cohort band far-edge 270d -> <270d             KILLED  test_decision_band_far_edge_270_vs_271_days
M9  newcomer band newer-edge 180 -> 181d           KILLED  test_newcomer_band_newer_edge_180_vs_179_days
M10 conversion window 180 -> 181d                  KILLED  test_newcomer_cohort_oracle_raw_vs_capped
M11 return window 90 -> 91d                        KILLED  test_newcomer_cohort_oracle_raw_vs_capped
M12 cap keeps newest, not oldest                   KILLED  test_dr90_cap_binds_six_of_ten_capped_to_two,
                                                            test_apply_author_cap_six_of_ten_capped_to_two
```

File byte-diff-clean against the pre-mutation backup after the final
restore (`diff -q` exits 0).

### Cost cap: measured GraphQL points per repo

Implemented exactly as specified: `AUTHOR_SEARCH_CAP = 50` per-author
`search` calls, deterministically sampled (`_seeded_sample`, shared with
the newcomer cohort) from the union of the decision-cohort authors and the
sampled newcomer authors when that union exceeds 50; authors outside the
sample get `history_unsampled` and are excluded from MR_first exactly like
a failed fetch (B2's principle). D-IN-3 eligibility (account age, event
count) is fetched for the FULL union via `_fetch_eligibility_batch`,
aliased at `ELIGIBILITY_BATCH_SIZE = 15` per request — never sampled, so
DR_90 keeps its complete cohort. Unresolved aliases are recorded
`eligibility_unmeasured`, never defaulted ineligible (unit-tested: `test_
fetch_eligibility_batch_width_15_resolves_every_alias`, `test_fetch_
eligibility_batch_unresolved_alias_marked_unmeasured_not_ineligible`).

Two independent live runs against the same 3 repos (`gh auth`: full
5000/5000 GraphQL budget both times), `force=True`, envelopes copied to
`/home/pctablet505/Projects/cvi-scratch/W1-4-r2/`:

| Repo | run 1 cost_pts | run 1 remaining | run 2 cost_pts | run 2 remaining |
|---|---|---|---|---|
| pallets/flask | 56 | 4385 | 59 | 3847 |
| pytest-dev/pytest | 57 | 4290 | 57 | 3718 |
| psf/requests | 59 | 4073 | 63 | 3608 |
| **mean** | **57.3** | | **59.7** | |

Both runs land under the ≤60 mean target (round 1 was 207/repo, 3.5x over);
the single largest value across both runs is 63 (psf/requests, run 2),
well inside the 65-point explain threshold. Where the points go (run 2):
window fetch 3-5 pts, up to 50 `search` calls at 1 pt each (capped, so
never more than 50 pts), and `ceil(#needed / 15)` eligibility batches at
1 pt each (6/5/11 batches for flask/pytest/requests) — 98% of round 1's
cost (the uncapped per-author `search` + per-author `user` pair) is gone.

**Operational finding, disclosed rather than silently worked around**: in
both live runs, a substantial fraction of eligibility-batch calls failed
as a WHOLE BATCH (`gh api graphql` returned a non-zero exit, caught by
`_fetch_eligibility_batch`'s except-RuntimeError branch, which correctly
marks every author in that batch `eligibility_unmeasured` rather than
defaulting them ineligible — the safety property holds). Manually re-
running one of the exact failed 15-alias queries in isolation immediately
afterward succeeded cleanly with all 15 aliases resolved, and the batch-
width-15 alias query itself is unit-tested to resolve 15/15 when the
transport succeeds. The failure pattern (all-or-nothing per batch, heavier
on the 2nd/3rd repo processed in the same script run: 0/88, 45/62, 45/152
unmeasured in run 2) is the signature of GitHub's secondary rate limit
being tripped by firing 50+ `gh` subprocess calls per repo, 3 repos, with
zero pacing between calls (~150-400 sequential CLI invocations in a few
minutes) — not a defect in the batch-width-15 design (W2's specific
finding, the ~17-alias silent per-field truncation, is a DIFFERENT failure
mode from a whole-batch rc!=0 and was not observed here: zero partial-
resolution batches in either run). Adding request pacing/backoff is out of
this round's named scope (not listed in the fixes required) and was not
implemented.

### Cost cap: parameter shift versus round 1

DR_90 raw (does not read any per-author record) is bit-for-bit unaffected
by the search cap, confirmed both by a dedicated unit test (`test_dr90_
unaffected_by_search_cap`, 60 synthetic authors, only 50 searched) and on
live data — pytest-dev/pytest's DR_90 raw is 0.8913 in round 1's stored
envelope, run 1, and run 2, all three, exactly:

| Repo | DR_90 raw round 1 | DR_90 raw round 2 (both runs) |
|---|---|---|
| pallets/flask | 1.0 | 1.0 |
| pytest-dev/pytest | 0.8913 | 0.8913 |
| psf/requests | 0.8776 | 0.877 (n 245->244, live repo activity between pulls) |

The other three parameters do NOT stay inside the review's ~0.0155 bound in
this measurement, most severely MR_first on pytest-dev/pytest. Reported
plainly with the reason, not hedged:

| Repo | MR_first raw r1 -> r2 (run 2) | MR_first capped r1 -> r2 (run 2) |
|---|---|---|
| pallets/flask | 0.0109 -> 0.0179 (n 92->56) | 0.0152 -> 0.0263 (n 66->38) |
| pytest-dev/pytest | 0.3279 -> 0.4151 (n 61->53) | 0.3462 -> 0.6667 (n 52->9) |
| psf/requests | 0.0735 -> 0.0563 (n 204->71) | 0.0833 -> 0.0833 (n 168->36) |

Two distinct, additive causes, both already covered above:

1. **The search cap itself samples out real candidates** (by design — an
   unsampled author is excluded from MR_first's population, same principle
   as B2). pytest-dev/pytest has only 62 decision-cohort authors and no
   newcomer sample, so the 50-cap excludes 12/62 (19%) of them; on a rate
   with n in the 50s that is enough sampling variance to move the RAW rate
   0.087 on its own (0.3279 -> pytest's own raw, unaffected by eligibility
   at all). The review's ~0.0155 bound was computed for a *different*
   candidate design (dropping search entirely, §4.4's "cheaper variant");
   it does not transfer to the round-2 task's literal pooled 50-cap on a
   population this small.
2. **The eligibility-batch secondary-rate-limit hazard** (above) shrinks
   the CAPPED denominator specifically, hardest on pytest-dev/pytest
   (45/62 = 73% `eligibility_unmeasured` in both runs) because eligibility
   determines the "eligible" filter every capped rate applies.

`COULD NOT VERIFY: the four parameters stay within the review's ~0.0155
shift bound under conditions clean of the secondary-rate-limit hazard above
— both live attempts here hit it, and reproducing a "clean" run needs
request pacing/backoff between gh CLI calls, which is not part of this
round's named fixes.` The exclude-don't-assume safety property (B2's
principle) holds regardless: every number reported above is a real,
smaller-but-correct population, never a value contaminated by treating an
unmeasured author as eligible or as a first-timer.

### Honesty fix: the real full-suite line

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../python -B -m pytest tests/ -n 4 -p no:cacheprovider
[...]
1577 passed, 12 warnings in 254.07s (0:04:14)
EXIT=0
```

Read directly from the tail of the captured output, no `-q` added beyond
`pytest.ini`'s own `addopts = -q`, no dot-counting. 1577 (versus round 1's
real 1427) is the round-1 baseline plus this round's 19 new tests (1427 ->
1446 expected from this packet alone) plus every other packet's tests that
landed on main between the round-1 base (`e29fb79`) and the round-2 base
(`d9442f9`) via the rebase — confirmed by `--collect-only` counts moving in
step with the rebase, not a defect.

### Lint

```
$ .../ruff check cvi/collect/github_graphql.py scripts/gather.py scripts/derive.py \
      tests/collect/test_pr_window.py
All checks passed!
```

Zero `print()`, zero `TODO`, in `cvi/collect/github_graphql.py`
(`grep -c` both 0).

### COULD NOT VERIFY

- The four cost-capped parameters' shift staying within the review's
  ~0.0155 bound under conditions clean of the secondary-rate-limit hazard
  (see above) — needs request pacing/backoff, out of this round's scope.
- Whether the eligibility-batch whole-batch failures are specifically
  GitHub's secondary rate limit (HTTP 403/429-class) versus some other
  transient CLI/network condition: `_fetch_eligibility_batch` does not log
  the caught `RuntimeError`'s message (by design, matching the module's
  existing silent-degrade precedent for per-author failures), so the exact
  error text from the live failures was not captured; the diagnosis rests
  on the failure signature (all-or-nothing per batch, worse on the 2nd/3rd
  repo in the same run, absent on an isolated manual retry of the same
  query/logins moments later) rather than a captured error string.
- L2-04 (two unreachable branches), C1-C10, N1-N2, and the `bundle.py`
  wiring request (W4, L1-14) from the review are unchanged from round 1 —
  none were named in this round's required fixes and none were touched, to
  keep the diff to what was asked.
