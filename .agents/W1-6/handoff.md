# W1-6 handoff — governance evidence: legal IP, promotion ladder, AI posture

Worktree `/home/pctablet505/Projects/cvi-wt-W1-6s`, branch `W1-6s`, base `e29fb79`.
Starting point: applied `git apply --3way` from
`/tmp/claude-1000/-home-pctablet505-Projects/2a48e84a-2b94-4a2f-8bff-adbe955a4084/scratchpad/inflight/W1-6/`
(`tracked.diff` + `untracked.tar`), resolved the two hook-placement conflicts
by hand (W1-8 landed on `scripts/gather.py`/`scripts/derive.py` after the
archived draft's base `d8d3189`), then read the draft critically against the
spec and reworked the parts that did not meet it (R-6). See "Deviations from
the archived draft" below for what changed and why.

## Diffstat

```
 cvi/collect/commits.py               |  25 +-
 cvi/collect/governance.py            | 630 +++++++++++++++++++++++++++++
 scripts/derive.py                    |  26 ++
 scripts/gather.py                    |  12 +
 tests/collect/test_depsdev.py        |   5 +
 tests/collect/test_governance_w16.py | 746 +++++++++++++++++++++++++++++++++++
 tests/collect/test_pr_window.py      |   3 +
 tests/test_backfill_downloads.py     |   4 +-
 tests/test_commits_ef.py             |  44 ++-
 tests/test_repo_object.py            |   3 +
 10 files changed, 1488 insertions(+), 10 deletions(-)
```

Source only (excludes tests): `cvi/collect/commits.py` + `cvi/collect/governance.py`
(new) + `scripts/derive.py` + `scripts/gather.py` = 689 insertions / 4 deletions,
~685 net new source lines. Packet size **L** (≤ 800) — no size deviation.

## Hook rule

Exactly one new call in `scripts/gather.py` (the `governance` collector
block) and one in `scripts/derive.py` (the governance-fields block),
appended at the **end** of each file's existing hook chain — after the
already-landed W1-8 `downloads_status`/`downloads` blocks, which is the true
end of the chain on `e29fb79` (W1-4 and W1-7 are not yet landed on this base).

## Ownership extension: `cvi/collect/commits.py` (pre-authorized by the dispatch)

The dispatch's own text anticipated this: "the W1-5 commits collector ...
stores author emails; if commit messages are not stored, extend that
collector minimally to record a per-commit boolean `signed_off` rather than
the message text." `shape_commit_rows` already receives `commit.message` in
the raw REST response (the same `repos/{o}/{r}/commits` listing W1-5 already
pages) but discarded it; it now also computes `signed_off: bool` (the
`Signed-off-by:` trailer) per row and keeps discarding the message text
itself. No new live call, no message text stored. `cvi.collect.governance`
reads this boolean from the already-collected `commits_365d` envelope for
3.1's sign-off share — it never fetches commits itself.

## Deviations from the archived draft (R-6: subtractive rework)

1. **Removed a duplicate live fetch and duplicated constants (R-2).** The
   archived `governance.py` re-fetched the trailing 365 d of commits *with
   messages* inside `collect_governance` (`_signoff_commits`, plus its own
   `SIGNOFF_WINDOW_DAYS=365`/`SIGNOFF_COMMIT_CAP=1000`/`SIGNOFF_PAGE_SIZE=100`
   — an exact re-statement of `cvi.collect.commits`'s
   `COMMIT_WINDOW_DAYS`/`COMMIT_CAP`/`COMMIT_PAGE_SIZE`), storing the message
   text in the envelope. This doubled every commit-listing call the pipeline
   already makes via W1-5, stored message text the spec never asks for, and
   violated R-2 (one definition per fact). Replaced with the commits.py
   extension above: `governance.py` now has a `signoff_share(c365_rows)`
   function that reads the already-collected `commits_365d` rows passed in
   from `scripts/derive.py` (which already loads that envelope for the EF/bus
   factor block above). Measured effect: the live torvalds/linux run below
   made **zero** extra commit-listing calls beyond the one `commits_365d`
   fetch itself.
2. **Dropped the AI-trailer commit-scanning path (out of this packet's
   collection scope).** The archived draft also scanned commit messages for
   an `Assisted-by`/`AI-generated` trailer convention (`_ai_trailer_count`,
   `AI_TRAILER_PATTERN`) to feed 4.2 REGULATED. The dispatch's own collection
   list for this packet is "check-run and status-context names ...,
   Signed-off-by share ..., presence and relevant text of GOVERNANCE.md /
   MAINTAINERS.md / CONTRIBUTING.md / SECURITY.md / an AI policy file, and
   association transitions" — no commit-trailer scanning, and doing it would
   need message text the extension above deliberately avoids storing.
   `classify_ai_posture` now takes `files` only; REGULATED is still reachable
   via AI-policy-file presence (confirmed live below: kubernetes/kubernetes's
   `AGENTS.md`). Documented as a scope decision in the module docstring, not
   a defect.
3. **Widened the "no evidence" default gate.** The archived
   `derive_governance_fields` returned the NONE/AD_HOC/AD_HOC default
   whenever the `governance` envelope alone was missing, discarding any real
   signoff-share evidence sitting in `commits_365d` (e.g. a governance
   collector failure with commits_365d intact). It now defaults only when
   **both** `governance` and `commits_365d` are absent — i.e. nothing at all
   has been collected — and derives a real value from whichever evidence
   exists otherwise. New test:
   `test_derive_repo_signoff_share_alone_without_governance_envelope`.
4. **Self-initiated ownership extension, not pre-authorized (R-10
   disclosure) — fixed a live-call regression the new hook caused in four
   other packets' "offline" unit tests.** `scripts/gather.py::gather_repo`
   now unconditionally calls `governance.collect_governance` for every repo.
   Four already-landed test files call `gather.gather_repo(...)` end-to-end
   with a `GATHER_DONE`-style fixture that predates this packet and does not
   mark `governance` done, so those tests started making real, unmocked live
   calls. Measured before fixing: `tests/collect/test_depsdev.py` (uses the
   real repo `pallets/flask`) went from **23.9 s → 117.0 s** for the same 24
   tests (≈76 extra live `gh api`/`raw.githubusercontent.com` calls against a
   real, unrelated, busy repository). Fixed by adding one
   `"governance": {...}` entry (and, in `test_depsdev.py`, the pre-existing
   `repo_object`/`commits_365d`/`pr_window` gaps too, since fixing them cost
   nothing more and they were making similar uncontrolled live calls against
   `pallets/flask`) to each fixture, mirroring the exact pattern already used
   for every other collector there. Files touched:
   `tests/collect/test_depsdev.py`, `tests/collect/test_pr_window.py`,
   `tests/test_repo_object.py`, `tests/test_backfill_downloads.py`,
   `tests/test_commits_ef.py` (my own file, same fix for consistency). No
   assertion in any of these files was changed — confirmed by diff and by
   the full pass in "Packet + regression test set" below.
5. **Provenance fallback.** `scripts/derive.py`'s hook now attaches the
   `commits_365d` envelope's source/`fetched_at` when no `governance`
   envelope exists but `commits_365d` does (supports point 3); otherwise
   unchanged from the archived draft (tied to the `governance` envelope).

## Fail-before proofs (R-1)

Restored pre-change files with `git show e29fb79:<path> > <path>` (module
absent for `cvi/collect/governance.py`, so it was removed instead), ran the
tests, then restored my versions from a scratch backup (byte-identical,
verified with `diff -q`) — never `git stash`, never `git checkout --`.

### Proof 1 — the whole W1-6 packet (governance module + both hooks)

Before (`scripts/derive.py`/`scripts/gather.py` restored to `e29fb79`,
`cvi/collect/governance.py` removed):

```
$ pytest tests/collect/test_governance_w16.py -p no:cacheprovider -q
ERROR collecting tests/collect/test_governance_w16.py
ImportError: cannot import name 'governance' from 'cvi.collect'
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
exit code: 2
```

After (my versions restored, verified byte-identical to the working copy):

```
$ pytest tests/collect/test_governance_w16.py -p no:cacheprovider -q
...............................................                          [100%]
47 passed in 1.32s
exit code: 0
```

### Proof 2 — `cvi/collect/commits.py`'s `signed_off` extension

Before (`cvi/collect/commits.py` restored to `e29fb79`):

```
$ pytest tests/test_commits_ef.py -p no:cacheprovider -q
...........F.FFF....F                                                    [100%]
FAILED tests/test_commits_ef.py::test_collect_stops_at_short_page_and_shapes_rows
  AssertionError: ... Right contains 1 more item: {'signed_off': False}
FAILED tests/test_commits_ef.py::test_shape_handles_unlinked_and_bot_authors
  AssertionError: ... Right contains 1 more item: {'signed_off': False}
FAILED tests/test_commits_ef.py::test_shape_records_signed_off_true_on_trailer
  KeyError: 'signed_off'
FAILED tests/test_commits_ef.py::test_shape_records_signed_off_false_without_trailer
  KeyError: 'signed_off'
FAILED tests/test_commits_ef.py::test_gather_hook_writes_commits_365d
  AssertionError: ... 'author_type': 'User'} != {..., 'signed_off': False}
exit code: 1
```

After (my `commits.py` restored, verified byte-identical to the working copy):

```
$ pytest tests/test_commits_ef.py -p no:cacheprovider -q
.....................                                                    [100%]
21 passed in 6.00s
exit code: 0
```

### Over-correction guards

* `test_derive_repo_v1_governance_fields_unchanged` — the v1 heuristic fields
  (`legal_ip_model`, `promotion_ladder`, `ai_posture`) keep their pre-change
  values; passes.
* The 15 pre-existing EF/bus-factor/vitality oracle tests in
  `tests/test_commits_ef.py` (unrelated to `signed_off`) are unaffected —
  they build rows via helpers that omit the new field, proving
  `records_from_rows`/`elephant_factor`/`bus_factor`/`is_vital` ignore it;
  all still pass (see the 21-passed run above).

## Packet + regression test set

```
$ pytest tests/collect/test_depsdev.py tests/collect/test_governance_w16.py \
    tests/collect/test_pr_window.py tests/core/test_sample200_load.py \
    tests/core/test_validation.py tests/snapshot/test_gather_envelope.py \
    tests/test_backfill_downloads.py tests/test_commits_ef.py \
    tests/test_derive_gfi.py tests/test_derive.py tests/test_repo_object.py \
    -p no:cacheprovider
210 passed in 25.01s
exit code: 0
```

(This is every test file that imports `scripts/derive.py`, `scripts/gather.py`
or `cvi.collect.commits` — the full blast radius of this packet's changes,
found by `grep -rl`.)

## Ruff

```
$ ruff check cvi/collect/governance.py cvi/collect/commits.py scripts/derive.py \
    scripts/gather.py tests/collect/test_depsdev.py tests/collect/test_pr_window.py \
    tests/test_repo_object.py tests/test_backfill_downloads.py \
    tests/test_commits_ef.py tests/collect/test_governance_w16.py
All checks passed!
```

`ruff check cvi/ scripts/ tests/` reports 23 pre-existing errors, all in
`scripts/curate_dataset_llm.py` (F401/RUF046) and `tests/test_perf_sla_stress.py`
/ `tests/test_webapp.py` (F401/RUF046/RUF002) — none in files this packet
touches; confirmed identical count (23) on the unmodified main checkout.

## Full suite

```
$ pytest tests/ -n 4 -p no:cacheprovider
1454 passed, 12 warnings in 13.02s
exit code: 0
```

12 warnings are pre-existing deprecation notices from `fastapi`/`starlette`/
`multiprocessing`, unrelated to this packet.

## Live-call log (torvalds/linux, kubernetes/kubernetes — 2 of 2 allowed)

Ran the real `cvi.collect.governance.collect_governance` and
`cvi.collect.commits.collect_commits_365d` (no injected fakes) via a thin
logging wrapper around the same `gh api`/`urllib` calls the library code
makes; envelopes written to
`/home/pctablet505/Projects/cvi-scratch/W1-6s/raw/{governance,commits_365d,pr_window}/`.
76 calls, 9,205,782 response bytes total.

```
torvalds/linux:
  gh api -X GET repos/torvalds/linux/pulls -f state=closed -f sort=created \
    -f direction=desc -f per_page=100 -f page=1
    -> rc=1, stdout=120B, stderr=25B ("Not Found": PRs disabled on this mirror)
  12x GET https://raw.githubusercontent.com/torvalds/linux/HEAD/<file>
    -> all 404, 14B bodies (no GOVERNANCE/MAINTAINERS/CONTRIBUTING/SECURITY/
       AI-policy file at repo root)
  10x gh api -X GET repos/torvalds/linux/commits -f per_page=100 \
    -f since=2025-09-12T10:19:05Z -f page=<1..10>
    -> rc=0 each, ~400-450KB/page, 1000 commit rows total (cap hit)
  gh api graphql (collect_pr_window) -> rc=0, 0 PRs in the 180 d window

kubernetes/kubernetes:
  gh api -X GET repos/kubernetes/kubernetes/pulls (page 1 only, 20 merged
    found on the first page)
    -> rc=0, stdout=2,671,130B
  40x gh api -X GET repos/kubernetes/kubernetes/commits/<sha>/{statuses,check-runs}
    (20 distinct merged-PR head SHAs x 2 endpoints)
    -> rc=0 each; check_names include "EasyCLA", "tide", and 28
       "pull-kubernetes-*"/"check-dependency-stats" Prow job names
  12x GET https://raw.githubusercontent.com/kubernetes/kubernetes/HEAD/<file>
    -> CONTRIBUTING.md 200 (525B), AGENTS.md 200 (1584B), the other 10 404
  gh api graphql (collect_pr_window, default page size)
    -> rc=1, "unexpected end of JSON input" (recorded, not hidden — a very
       active repo's 180 d PR page exceeded the default query budget)
  gh api graphql (collect_pr_window, page_size=25 retry)
    -> rc=0, 500 PRs, truncated=True
```

## Classification results (Measure)

### Live (both derived from the envelopes above via the real classifiers)

| Repo | 3.1 legal_ip | evidence | 3.3 promotion_ladder_v2 | 4.2 ai_posture_v2 |
|---|---|---|---|---|
| torvalds/linux | **DCO** | rule `signoff_share`; `{"count": 924, "total": 1000, "share": 0.924}` (≥ 0.5) | AD_HOC (`no_evidence`; PRs disabled, 0 PRs in the 180 d window) | AD_HOC (`no_evidence`; no governance/AI files present) |
| kubernetes/kubernetes | **FOUNDATION_CLA** | rule `foundation_cla_check`; check name `"EasyCLA"` present among 30 collected check names | AD_HOC (`no_evidence`; 0 external->MEMBER/COLLABORATOR transitions in 500 truncated 180 d PRs; CONTRIBUTING.md has no nomination wording) | **REGULATED** (`ai_policy_file`; `AGENTS.md` present — its body contains "disclose whether AI was used") |

These match the packet's acceptance criteria exactly: torvalds/linux -> DCO
with signoff share ≥ 0.5 pasted; kubernetes/kubernetes -> FOUNDATION_CLA with
the EasyCLA check name pasted.

### Sample (200-repo `sample-200`, before/after)

Before = read directly from the already-produced `sample-200/derived/*.json`
bundles (v1 deriver fields `legal_ip_model`/`promotion_ladder`/`ai_posture`;
**confirmed v1**: no `_v2` field, no `legal_ip`, no `governance` anywhere in
those bundles). 199 of 200 manifest repos have a bundle (1 missing,
pre-existing sample gap, not investigated — out of scope).

After = this packet's `derive_repo` run over a scratch copy of
`sample-200/raw` (`cvi-scratch/W1-6s/sample-after/raw`, copied per R-3, never
written back to `sample-200`), all 200 repos, 0 derive errors.

| Field | Before (v1, n=199) | After (v2, n=200) |
|---|---|---|
| 3.1 legal IP | `legal_ip_model`: DCO 196, FOUNDATION_CLA 3 | `legal_ip`: **NONE 200** |
| 3.3 promotion ladder | `promotion_ladder`: AD_HOC 199 (100%) | `promotion_ladder_v2`: AD_HOC 200 (100%) |
| 4.2 AI posture | `ai_posture`: AD_HOC 199 (100%) | `ai_posture_v2`: AD_HOC 200 (100%) |

The after-column is 100% default because `sample-200/raw` was frozen by
W0-1 before the `governance`, `commits_365d` (W1-5) and `pr_window` (W1-3)
collectors existed: zero of the 200 sampled repos carry any of those three
envelopes today. This is the measured, honest state of the frozen sample,
not an artifact — the unit oracle
(`test_derive_repo_no_governance_evidence_defaults_none_ad_hoc_ad_hoc`,
fail-before proof 1 above) and the two live repos above both confirm every
non-default classification path fires correctly once the evidence exists.
The v1->v2 legal-IP shift (DCO-by-default 196/199 -> NONE-by-evidence-absence
200/200) is exactly finding F7's "DCO defaults 99% of the time" replaced with
an honest missing-evidence default, per D-VER/O-1.

## COULD NOT VERIFY

* `kubernetes/kubernetes` `pr_window` at the collector's default GraphQL page
  size: `gh api graphql rc=1: unexpected end of JSON input`. Not retried
  beyond one smaller-page-size attempt (which succeeded, `page_size=25`) —
  3.3 live evidence for this repo is supplementary, not part of the packet's
  required acceptance (only 3.1 is specified for live proof).
* Whether `cvi/core/constants_v2.py`'s bare `promotion_ladder`/`ai_posture`
  `PARAM_INPUT_IDS` entries are meant to be filled from this packet's
  `_v2`-suffixed fields by a later integration packet — no such mapping
  exists yet in this tree; flagged for whichever packet wires
  `scripts/derive.py`'s output into the v2 `FeatureBundle`.
* The one `sample-200` manifest repo without a `derived/` bundle (before
  n=199 vs manifest n=200) — a pre-existing gap from W0-1, not investigated.

## Live-call budget

2 of 2 live repositories used (torvalds/linux, kubernetes/kubernetes), per
the packet's dispatch cap. All other tests use recorded-response fakes.
