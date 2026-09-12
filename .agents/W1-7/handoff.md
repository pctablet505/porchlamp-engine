# W1-7 handoff — OpenSSF criticality signals computed locally; scorecard subset; review/CI proxies

Worktree: `/home/pctablet505/Projects/cvi-wt-W1-7s`, branch `W1-7s`, base `e29fb79`.
Implementation commit: `f0f359f`.

## Starting point and what changed vs. the in-flight draft

Applied `/tmp/claude-1000/.../scratchpad/inflight/W1-7/tracked.diff` onto `e29fb79`
with `git apply --3way` (base was `d8d3189`; two hunks conflicted against the
since-landed W1-8 hook block in `scripts/derive.py`/`scripts/gather.py` —
resolved by keeping W1-8's block first and appending the W1-7 block after it,
per the hook-chain-order rule). Copied in the five untracked files
(`gh_rest.py`, `releases.py`, `issues_counts.py`, `runs.py`,
`pike_signals.py`, `tests/collect/test_w17_pike.py`), read them critically,
and rewrote rather than kept as-is:

**Consolidated 5 collector modules into 1** (`cvi/collect/pike_signals.py`,
643 lines) — `github_graphql.py` (W1-3) and `commits.py` (W1-5) both bundle
collection + derivation for one packet's concern in a single file; the draft's
`gh_rest.py`/`releases.py`/`issues_counts.py`/`runs.py`/`pike_signals.py` split
was more surface than the packet needs and didn't match that precedent. The
REST transport, the three new collectors and the 2.2/4.1 derivation now live
in one module; `gh_rest.py` as a separate file was dropped entirely (folded
in), matching how `github_graphql.GhCliTransport` lives inside its own
collector module rather than a shared transport file.

**Three correctness fixes found on review, none of which were in the
dispatch's literal packet-summary text but all three are required by the
packet instructions given to me verbatim:**

1. **Method string.** The dispatch says `method = "primary" or
   "proxy:review_ci"` (matching `cvi.core.constants_v2.METHOD_PRIMARY` /
   `PROXY_METHOD_PREFIX`, the v2 vocabulary `measured_problems` validates
   against). The draft used the invented string `"scorecard:subset"` for the
   scorecard-subset branch. Fixed to `"primary"`. (The proxy string,
   `"proxy:review_ci"`, was already correct.)
2. **`is:closed` on the closed-issues search query.** GitHub's `closed:`
   qualifier matches on the issue's last-recorded close date regardless of
   its *current* state, so a reopened issue that was briefly closed inside
   the 90-day window would still match `is:issue closed:>=X` even though it
   is not, right now, a closed issue. `closed_issues_count_90d` is a
   current-state count (OpenSSF's criticality-score signal name), so the
   query now adds `is:closed` alongside `closed:>=`. (`updated:` is
   deliberately left with no state qualifier — it should count all issues
   touched in the window regardless of state.)
3. **Bot reviewers excluded from the code-review-rate proxy.** The draft's
   `has_non_author_approval` counted an APPROVED review from *any* login
   different from the PR author, including automated accounts (a CI bot that
   auto-approves certain PRs). `cvi.collect.github_graphql.is_bot_actor`
   (already public, W1-3) is now applied to the reviewer, matching the same
   codebase's existing bot-exclusion pattern for review-based signals
   (`_is_maintainer`, `substantive_review`). A new test
   (`test_has_non_author_approval_excludes_bot_reviewers`) covers it.

Also added three tests beyond the draft: `test_derive_pike_signals_missing_any_source_is_none`
(one assertion per one-of-five-envelope-absent case, including the
c365-present-but-empty "real zero-commit repo" case), and
`test_derive_repo_missing_dependents_leaves_openssf_missing` (the W1-2
dependents envelope specifically, end-to-end through `derive_repo`) — these
satisfy "with the missing signal named" per-prerequisite, since v1's `Bundle`
class (shared, in-flight-modified infrastructure I must not restructure) has
no reason/detail slot to store a free-text name in.

## Diffstat

```
 cvi/collect/pike_signals.py    | 643 ++++++++++++++++++++++++++++++++++++++
 scripts/derive.py              |  13 +
 scripts/gather.py              |  15 +
 tests/collect/test_w17_pike.py | 683 +++++++++++++++++++++++++++++++++++++++++
 4 files changed, 1354 insertions(+)
```
Non-test source: 671 lines (643 + 13 + 15), under the L-class informal
~800-line ceiling other packets have been measured against.

Hook rule: exactly one appended call in `scripts/gather.py` (after the W1-8
`downloads_status`/`RETRYABLE_ECOSYSTEMS` block) and one in `scripts/derive.py`
(after the W1-8 `downloads_status` block) — both are the last block before
`return status` / the `meta = {...}` assembly line respectively.

## Packet test set

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -m pytest tests/collect/test_w17_pike.py -p no:cacheprovider -v -o addopts=""
...
33 passed in 4.44s
```
Exit code: 0. (33 items: 7 releases, 2 issue-search-query, 2 issues-counts,
2 CI-runs, 6 pipeline-defenses/code-review-rate, 2 Pike-signal oracle, 6
`derive_repo` end-to-end, 3 `gather_repo` hook, plus the 3 fixed above.)

## Fail-before / restore proof (R-1)

```
$ git show e29fb79:scripts/derive.py > scripts/derive.py
$ git show e29fb79:scripts/gather.py > scripts/gather.py
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -m pytest tests/collect/test_w17_pike.py -p no:cacheprovider -o addopts=""
...
FAILED tests/collect/test_w17_pike.py::test_derive_repo_pike_signals_from_envelopes
FAILED tests/collect/test_w17_pike.py::test_derive_repo_without_scorecard_gets_proxy_review_ci
FAILED tests/collect/test_w17_pike.py::test_derive_repo_scorecard_subset_wins_over_proxy
FAILED tests/collect/test_w17_pike.py::test_derive_repo_partial_signals_leaves_openssf_missing
FAILED tests/collect/test_w17_pike.py::test_derive_repo_no_ci_runs_scores_zero_in_proxy
FAILED tests/collect/test_w17_pike.py::test_gather_repo_w17_collectors_ok
FAILED tests/collect/test_w17_pike.py::test_gather_repo_w17_collectors_skip
FAILED tests/collect/test_w17_pike.py::test_gather_repo_w17_collectors_fail
8 failed, 25 passed in 4.40s
```
Exit code: 1. Exactly the 8 tests that exercise the hook wiring (6
`derive_repo`/`gather_repo` end-to-end tests plus the 2 remaining gather-hook
tests) fail without it; the 25 tests of the pure collector/derivation
functions in `pike_signals.py` (which exists independently of the hook)
stay green, correctly isolating what the hook itself is responsible for.

Restored:
```
$ git show f0f359f:scripts/derive.py > scripts/derive.py
$ git show f0f359f:scripts/gather.py > scripts/gather.py
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -m pytest tests/collect/test_w17_pike.py -p no:cacheprovider -o addopts=""
...
33 passed in 4.69s
```
Exit code: 0. `git diff --stat` against the `f0f359f` tree: empty (byte-exact
restore).

## Over-correction guard

Pre-existing tests `tests/test_derive.py::test_full_bundle` (a fully-seeded
v1 bundle that still lacks `commits_365d`/`releases`/`issues_counts`/
`dependents`/`pr_window`) and `::test_missing_files` both assert
`m["openssf_signals"] is None` and pass unmodified in the full suite below —
the pre-existing "no signal unless all ten sources exist" contract is
unchanged for repos that don't carry the five new prerequisite envelopes.
My own `test_derive_repo_without_inputs_leaves_w17_fields_absent` covers the
same guard directly inside the packet test set.

## Ruff

```
$ .venv/bin/ruff check cvi/collect/pike_signals.py tests/collect/test_w17_pike.py scripts/derive.py scripts/gather.py
All checks passed!
```
Exit code: 0.

```
$ .venv/bin/ruff check cvi/ scripts/ tests/
Found 23 errors. [17 fixable]
```
Exit code: 1. All 23 are in 5 files I did not touch: `scripts/curate_dataset_llm.py`,
`tests/benchmark_sla_runner.py`, `tests/test_adversarial_comparator_m3.py`,
`tests/test_perf_sla_stress.py`, `tests/test_webapp.py` (F401/RUF001/RUF002/
RUF046/B023/SIM114/F541). Confirmed pre-existing, not a regression: the main
checkout has advanced to `970500a` ("chore(lint): HYG-1 clear 19 ruff
findings in legacy test files") *after* my branch point `e29fb79` — my
worktree is deliberately frozen at `e29fb79` per the dispatch and does not
carry that unrelated cleanup commit forward. `git log --oneline -1` on the
main checkout confirms `970500a` as HEAD, one commit ahead of my base.

## Full suite

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -m pytest tests/ -n 4 -p no:cacheprovider -o addopts=""
...
1437 passed, 12 warnings in 57.05s
```
Exit code: 0. (The 12 warnings are pre-existing deprecation notices from
`starlette`/`fastapi`/`multiprocessing`, unrelated to this packet.) Note: the
project's default `-q` addopts suppresses the pytest-xdist summary line
entirely in this environment on every invocation, including on an unmodified
checkout — a pre-existing environment quirk, not caused by this change;
`-o addopts=""` overrides it to get the summary line, and the runs above with
just `-p no:cacheprovider` were verified separately by exit code (0) and dot
count (no `F`/`E` markers).

## Live-call log (3 repos: pallets/flask, psf/requests, pallets/click)

Ran the real `gather.gather_repo(owner, name, "pypi", skip_pypistats=True)`
end-to-end (every collector, not just the three new ones) against 3 live
repositories, with a global `subprocess.run`/`urllib.request.urlopen` wrapper
recording every call and its response byte count. Output under
`/home/pctablet505/Projects/cvi-scratch/W1-7s/live/` (raw envelopes, derived
bundles, `call_log.json`, `results.json`); driver script and full log at
`/home/pctablet505/Projects/cvi-scratch/W1-7s/live_validate.py` and
`.../live_run.log`.

76 live calls total across all collectors for the 3 repos, 9,133,797 bytes,
**all 76 returned rc=0** (zero failures). The 15 calls specific to this
packet's three new collectors (5 per repo: releases page 1, closed-issues
search, updated-issues search, 30-issue sample search, default-branch runs):

```
rc=0 bytes= 180882  gh api -X GET repos/pallets/flask/releases -f per_page=100 -f page=1
rc=0 bytes=   3851  gh api -X GET search/issues -f q=repo:pallets/flask is:issue is:closed closed:>=2026-06-14 -f per_page=1
rc=0 bytes=   2660  gh api -X GET search/issues -f q=repo:pallets/flask is:issue updated:>=2026-06-14 -f per_page=1
rc=0 bytes= 106579  gh api -X GET search/issues -f q=repo:pallets/flask is:issue -f sort=updated -f order=desc -f per_page=30
rc=0 bytes= 358683  gh api -X GET repos/pallets/flask/actions/runs -f branch=main -f status=completed -f per_page=30
rc=0 bytes= 113807  gh api -X GET repos/psf/requests/releases -f per_page=100 -f page=1
rc=0 bytes=   3624  gh api -X GET search/issues -f q=repo:psf/requests is:issue is:closed closed:>=2026-06-14 -f per_page=1
rc=0 bytes=   3624  gh api -X GET search/issues -f q=repo:psf/requests is:issue updated:>=2026-06-14 -f per_page=1
rc=0 bytes= 127795  gh api -X GET search/issues -f q=repo:psf/requests is:issue -f sort=updated -f order=desc -f per_page=30
rc=0 bytes= 374165  gh api -X GET repos/psf/requests/actions/runs -f branch=main -f status=completed -f per_page=30
rc=0 bytes= 155466  gh api -X GET repos/pallets/click/releases -f per_page=100 -f page=1
rc=0 bytes=   4830  gh api -X GET search/issues -f q=repo:pallets/click is:issue is:closed closed:>=2026-06-14 -f per_page=1
rc=0 bytes=   4462  gh api -X GET search/issues -f q=repo:pallets/click is:issue updated:>=2026-06-14 -f per_page=1
rc=0 bytes= 164347  gh api -X GET search/issues -f q=repo:pallets/click is:issue -f sort=updated -f order=desc -f per_page=30
rc=0 bytes= 358375  gh api -X GET repos/pallets/click/actions/runs -f branch=main -f status=completed -f per_page=30
```
15 calls, 1,963,150 bytes. All 3 repos had fewer than 100 releases (no
`page=2` call issued; `releases_truncated=False` for all three), so the
tag-fallback path (zero-releases repos) was not exercised live -- it is
covered by `test_collect_releases_tag_fallback_when_no_releases`.

**End-to-end result** (`derive_repo` on the live envelopes): all three repos
produced a fully **measured** `openssf_signals` (all 10 signals present) and
`pipeline_defenses` via method **"primary"** (all three already carry an
OpenSSF Scorecard record):

| repo | dependents_count | recent_releases | closed_90d/updated_90d | comment_freq | pipeline_defenses | method | v1 scorecard_score |
|---|---|---|---|---|---|---|---|
| pallets/flask | 12,698 | 1 | 15/29 | 1.97 | 57.5 | primary | 7.1 |
| psf/requests | 181,538 | 5 | 15/46 | 3.37 | 70.0 | primary | 8.3 |
| pallets/click | 134,452 | 8 | 41/1619 | 4.57 | 75.0 | primary | 7.2 |

`flask`'s `dependents_count` (12,698) is byte-for-byte the same figure
`tests/collect/test_depsdev.py`'s docstring recorded for the same repo from
the W1-2 live capture (`"dependentCount": 12698`), an independent
cross-check that the collector reused here (`cvi.collect.depsdev`,
unmodified) is reading the same real upstream fact.

All three repos already carry a Scorecard record, so **the proxy method
(`"proxy:review_ci"`) was not observed live** in this 3-repo sample — it is
covered only by the deterministic unit oracles (`test_pipeline_defenses_proxy_review_ci`,
`test_pipeline_defenses_proxy_zero_ci_when_no_runs`,
`test_derive_repo_without_scorecard_gets_proxy_review_ci`,
`test_derive_repo_no_ci_runs_scores_zero_in_proxy`) plus the fail-before
contrast the acceptance criterion asks for: with the pre-change hooks
restored (see fail-before proof above), the same inputs that today produce
`pipeline_defenses = 58.0, method = "proxy:review_ci"`
(`test_derive_repo_without_scorecard_gets_proxy_review_ci`) instead leave
`scorecard_score` at v1's `missing` placeholder (`0.0`) — the packet's "v1's
0.0 for the same inputs" acceptance clause, reproduced by that test's
pre-existing assertion `meta["scorecard_score"] == 0.0` /
`confidence["scorecard_score"] == "missing"`, which stayed true both before
and after (it is asserting the *old* field's continued absence, not a
changed one).

## Measure

**Before** (frozen `~/Projects/cvi-scratch/sample-200`, read-only, via
`python -m cvi.audit.coverage --data-dir sample-200 --json`):
- `meta.openssf_signals` missing_rate = **1.0000** (200/200) — matches F8's
  100% exactly.
- `meta.scorecard_score` missing_rate = **0.3950** (79/200 missing, 121/200
  present). F8 cites 37.8% on the full 4,145-repo corpus; the ~1.7 pp gap is
  the same 200-repo sampling noise the W0-1 lead log already documented for
  other fields ("the same tool on all 4,145 collected repos matches 01 F8
  within 1.0 pp... the misses are 200-repo sampling noise") — not something
  I can resolve by re-measuring this 200-repo copy.

**After** (same 200 repos, raw envelopes copied to
`~/Projects/cvi-scratch/W1-7s/sample-regen/`, `derive_repo` re-run with the
new hook active over all 200; driver at `.../W1-7s/regen_measure.py`):
- `openssf_signals` present: **0/200**, missing_rate **1.0000** (unchanged).
  The frozen sample carries no `commits_365d` (W1-5), `dependents` (W1-2),
  `pr_window` (W1-3) envelopes at all (`ls raw/` shows only the 11 W0-1
  collectors) and my two new envelope types (`releases`, `issues_counts`)
  were not swept into it either (R-3/packet's 3-live-repo cap forbids a
  200-repo re-sweep). This mirrors W1-2's own accepted precedent exactly
  ("sample missing-rate stays 1.00 until re-sweep") — the packet's collector
  and derivation code is correct (proven above by 33 packet tests + 3 live
  repos with all ten signals measured), but this specific frozen sample
  cannot exercise it until wave-level re-collection happens.
- `pipeline_defenses` present: **120/200**, missing_rate **0.4000** (79
  scorecard-absent + 1 repo -- `SagerNet/sing-box` -- whose v1
  `scorecard_score` (4.4) came from the deps.dev fallback composite, not
  from a raw scorecard envelope's per-check breakdown, so `checks` was
  unavailable for the 5-check subset; verified by inspecting that repo's raw
  `scorecard` envelope, which is empty). This is 1 repo worse than the v1
  `scorecard_score` present-count (121) for a documented, non-bug reason:
  4.1 needs the five *named* checks, which only the Scorecard's own
  per-check payload carries, not deps.dev's single composite number.
- `pipeline_defenses` method split on this sample: **120 "primary" / 0
  "proxy:review_ci"** — the proxy needs both `pr_window` (W1-3) and `runs`
  (this packet), and the frozen sample carries neither (my "after" run
  reused the sample's unmodified `raw/`, adding no live fetches to it, per
  the packet's live-repo cap); the proxy path is proven correct only by the
  unit oracles listed in the live-call section, pending a wave-level
  re-sweep of this sample that adds both envelope types.
- Live 3-repo method split: **3 "primary" / 0 "proxy:review_ci"** (all three
  already have OpenSSF Scorecard records; see live-call section above for
  why the proxy branch wasn't observed live).

## Deviations

- Consolidated the in-flight draft's 5 collector modules into 1
  (`cvi/collect/pike_signals.py`), matching the `github_graphql.py`/
  `commits.py` one-module-per-packet-concern precedent, per the explicit
  instruction to merge modules that remove duplication.
- Fixed three items in the in-flight draft before accepting it (method
  string, `is:closed` qualifier, bot-reviewer exclusion) — see above.
- Did not touch `cvi/derive/bundle.py` (the v1-to-v2 `FeatureBundle` mapper):
  its `V1_FIELD_TO_INPUT`/`NO_V2_COLLECTOR` tables still route
  `openssf_criticality` and `pipeline_defenses` to a hard-coded
  `Unmeasured(collector_failure, ...)` regardless of what `scripts/derive.py`
  now computes, and its `_load_v1` value-type gate (`isinstance(value,
  (int, str))`) would reject a v2 `Measured` for these two inputs even if
  the tables were updated, because both values are floats. This file is not
  in W1-7's owned-files list ("`scripts/derive.py` (signals + pipeline
  block)" only) and every prior W1-N collector packet (W1-2, W1-3, W1-5,
  W1-8) landed with the same v1-only scope, leaving this exact bridging gap
  for each of *their* new v1 fields too (`dependents_deduped`,
  `substantive_review_rate`, the windowed responsiveness fields) — this is
  an existing, multi-packet-wide gap, not one this packet introduced or was
  asked to close. Flagged as an ownership request below rather than
  expanded into (R-10, R-7).
- Did not modify `cvi/audit/coverage.py`'s fixed `SPECS` tuple to add a
  `pipeline_defenses` row (also outside W1-7's owned files); the Measure
  numbers above for `pipeline_defenses` were computed with a throwaway
  driver (`~/Projects/cvi-scratch/W1-7s/regen_measure.py`) using the same
  present/missing definition `cvi.audit.coverage` uses (`confidence[field]
  != "missing"`), not by editing the shared tool.

## COULD NOT VERIFY

- Whether the exact 37.8% figure in F8/the dispatch text was measured with a
  tool-version or denominator I can exactly reproduce on the 200-repo
  sample; my own measurement of the same field gives 39.5% on this sample,
  which the W0-1 lead log's own precedent attributes to 200-repo sampling
  noise against the full 4,145-repo corpus, not a defect in either
  measurement.
- The `"proxy:review_ci"` method path against a real, currently-unscanned
  GitHub repository: all 3 of the live-validation repos already carry an
  OpenSSF Scorecard record, so the proxy branch was exercised only through
  the deterministic unit oracles and the fail-before contrast, not through
  a live fetch landing on that branch. A 4th live repo would resolve this
  but exceeds the packet's 3-live-repository bound.
- Full v2 `FeatureBundle` wiring for `openssf_criticality`/`pipeline_defenses`
  (see Deviations) — needs `cvi/derive/bundle.py` changes outside this
  packet's owned files; recorded as an ownership request for the lead
  (the same request applies to `dependents_deduped` from W1-2 and
  `substantive_review_rate` from W1-3, already latent on main).
