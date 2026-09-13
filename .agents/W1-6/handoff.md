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

---

# Round 2 (REWORK response)

Base: rebased branch `W1-6s` from `3844e79` onto main tip `a789466` (W1-7
landed in the interim). Rebase conflicts at the tail of `scripts/gather.py`
and `scripts/derive.py` resolved as instructed: W1-7's block kept first,
W1-6's block appended after, both hook chains intact.

Review: `/tmp/claude-1000/-home-pctablet505-Projects/2a48e84a-2b94-4a2f-8bff-adbe955a4084/scratchpad/reviews/W1-6-review.md`.
All four Blockers and W1/W6/W7/W8 fixed. Diffstat:

```
 cvi/collect/governance.py            | 233 ++++++++++++++++++++++++--------
 scripts/derive.py                    |   9 +-
 tests/collect/test_depsdev.py        |  16 ++-
 tests/collect/test_governance_w16.py | 251 +++++++++++++++++++++++++++++++----
 tests/collect/test_pr_window.py      |  17 ++-
 tests/conftest.py                    | 120 +++++++++++++++++
 tests/test_backfill_downloads.py     |  29 +++-
 tests/test_commits_ef.py             |  13 +-
 tests/test_derive.py                 |  10 +-
 tests/test_repo_object.py            |  19 ++-
 10 files changed, 607 insertions(+), 110 deletions(-)
```

## B1 — narrow CLA matching

Replaced the bare `FOUNDATION_CHECK_PATTERNS` (`easycla`, `\basf\b`, `apache`,
`cncf`, `eclipse`) with a shared `CLA_CHECK_PATTERNS` gate (`easycla`,
`cla-assistant`, `cla-check`, `license/cla`, `cla/`, a bare `cla` token
bounded by non-alphanumerics) that both the foundation and corp branches now
require. `FOUNDATION_CHECK_PATTERNS` is now `easycla` or
`(asf|apache|cncf|eclipse)[\s_/-]*cla` — every alternative already satisfies
`CLA_CHECK_PATTERNS`, so a bare foundation-name substring never matches
anywhere. `foundation_owner` stays a fully independent branch (unaffected,
so `test_legal_ip_foundation_owner_without_checks` is unchanged).

Six-case test (parametrized, DCO must win alongside each false positive):

```
$ pytest -p no:cacheprovider "tests/collect/test_governance_w16.py::test_legal_ip_foundation_substrings_never_shadow_a_real_dco_check"
......                                                                   [100%]
6 passed in 0.06s
```

Positive/over-correction controls (EasyCLA, cla-assistant, ASF CLA,
apache-cla, cncf-cla, eclipse-cla/validation, foundation-owner-alone):

```
$ pytest -p no:cacheprovider tests/collect/test_governance_w16.py -k foundation
..........                                                               [100%]
10 passed, 61 deselected in 0.06s
```

Live-repo recomputation from the round-1 stored envelopes
(`/home/pctablet505/Projects/cvi-scratch/W1-6s/raw/`), network patched to
raise (`cvi-scratch/W1-6-r2/recheck_live.py`):

```
torvalds__linux
  legal_ip='DCO' conf='derived' rule='signoff_share' signoff={'count': 924, 'total': 1000, 'share': 0.924}
kubernetes__kubernetes
  legal_ip='FOUNDATION_CLA' conf='derived' rule='foundation_cla_check' check_names=[...31 names incl. 'EasyCLA'...]
  ai_posture='REGULATED' conf='derived'
NO NETWORK WAS USED (connect/Popen patched to raise)
```

Same outputs as round 1 (DCO 924/1000; kubernetes FOUNDATION_CLA via
EasyCLA) under the tightened matcher — over-correction guard holds.

## B2 — Unmeasured(collector_failure) vs measured zero

`derive_governance_fields` now computes confidence per parameter instead of
one blanket `"derived"`: `"missing"` (this v1-format document's spelling of
Unmeasured — `cvi.derive.bundle._load_v1` maps a `"missing"` confidence to
`Unmeasured(reason="collector_failure")`, the W1-7 F2 template) fires only
when the relevant envelope(s) were never fetched:

* `legal_ip`: `"missing"` iff `gov is None and c365 is None`.
* `promotion_ladder`: `"missing"` iff `prw is None and gov is None`
  (VERIFIED/CLOSED both need `prw`; DOCUMENTED alone needs `gov`'s files).
* `ai_posture`: `"missing"` iff `gov is None` (its only file source).

Six-case test (fetch-failure -> Unmeasured, fetch-success+no-evidence ->
measured, for all three parameters):

```
$ pytest -p no:cacheprovider tests/collect/test_governance_w16.py -k "unmeasured or measured_zero"
......                                                                   [100%]
6 passed, 65 deselected in 0.06s
```

`test_derive_repo_no_governance_evidence_defaults_none_ad_hoc_ad_hoc` was
updated (round 1 asserted `confidence == "derived"` for the no-envelope
case, which was precisely the bug) to assert `confidence == "missing"` and
the richer per-parameter reason strings.

## B3 — negation-aware AI-ban detection

Deleted the bare `\b(no|not|never|without)\s+(ai|...)` pattern outright (no
ban verb at all — the direct source of two of the three misfires). Added a
line-level negation guard (`_is_negated_ban_line`): a negation token
(`not`, `n't`, `no`, `nothing`, `never`, `will not`) plus an AI-reference
term plus a ban-verb term (`ban`/`forbid`/`prohibit`/`disallow` family) in
the same line means the line denies a ban, not states one; `_ban_match`
skips such a line and keeps scanning the rest of the corpus.

Four-case test (three repros must NOT derive PROSCRIBED, one positive
control for a genuine ban in the same corpus as a negated line):

```
$ pytest -p no:cacheprovider tests/collect/test_governance_w16.py -k "does_not_derive_proscribed or negation_does_not_blind"
....                                                                     [100%]
4 passed, 67 deselected in 0.06s
```

Existing positive-ban tests (`...proscribed_ban_in_contributing` = "We do
not accept AI-generated code.", `...proscribed_beats_policy_file` = "...are
not permitted.") still pass unchanged: neither contains a ban-verb term
from the negation guard's own vocabulary, so the guard never fires on them.

## B4 — structural network guard

(a) `collect_governance`'s `api=`/`fetch_file=` injectable transports
already existed (round 1); no change needed there.
(b) Added `block_live_network`, one shared opt-in fixture in
`tests/conftest.py` (not autouse — a file changes nothing unless a test
requests it). It patches `socket.socket.connect`/`connect_ex`/
`create_connection` and `subprocess.Popen.__init__` to raise on any
non-loopback target, **and records every blocked attempt, asserting zero at
teardown** — this second half is load-bearing: `gather_repo`'s per-collector
`except Exception` swallows the raise and records `status[c]="fail"`, so a
test that never asserts that status would otherwise still pass with the
call blocked and unnoticed (verified by an actual negative control, below).
`@pytest.mark.live_network` opts a test back into real access; none needed
it.

Replaced the four/five ad hoc local patches: extended each of
`tests/collect/test_depsdev.py`, `tests/collect/test_pr_window.py`,
`tests/test_repo_object.py`, `tests/test_backfill_downloads.py`,
`tests/test_commits_ef.py`'s existing "mark collectors done" seed
dicts/tuples to cover every collector `gather_repo` invokes (`dependents`,
`commits_365d`, `pr_window`, `releases`/`issues_counts`/`runs`, and the
W1-1 `gfi` new-shape record so `needs_gather` doesn't re-trigger a live
`labels` call) — the same technique round 1 already used for `governance`,
applied completely instead of partially — and added `block_live_network`
to the 12 `gather_repo`-calling test functions across those five files.

Zero-live-calls proof (plain pytest, fixture-only, no external harness):

```
$ pytest -p no:cacheprovider tests/collect/test_depsdev.py tests/collect/test_pr_window.py tests/test_repo_object.py tests/test_backfill_downloads.py tests/test_commits_ef.py
........................................................................ [ 66%]
.....................................                                    [100%]
109 passed in 6.55s
```

**Live-call count: 0, down from 40** (the review's measured baseline,
`test_depsdev.py` 3 + `test_pr_window.py` 12 + `test_repo_object.py` 10 +
`test_backfill_downloads.py` 13 + `test_commits_ef.py` 2). Zero attempts
were recorded by `block_live_network` across all 12 guarded tests; the
guard's assert-on-leftover-attempts fired on nothing.

**Negative control (guard detection power, genuinely re-executed):**
removed the `"dependents"` seed line from `test_commits_ef.py`'s hook test
and re-ran it alone:

```
E   AssertionError: 5 live network attempt(s) blocked during this test (caught
    internally, so no other assertion saw it): ["socket.create_connection
    ('api.deps.dev', 443)", "socket.create_connection ('packages.ecosyste.ms',
    443)", ...3 more gh api attempts...]
1 passed, 1 error in 2.60s
```

The test itself (`assert st["commits_365d"] == "ok"`) still passed — proving
the exact structural gap B4 names (a swallowed leak is otherwise invisible)
— but the fixture's teardown assertion failed the run anyway. File
restored; `git status --short` printed nothing extra. This negative control
also caught a genuine gap of my own first pass (the `releases`/
`issues_counts`/`runs` W1-7 collectors were not yet seeded in
`test_commits_ef.py`), which is now fixed and included in the 0-count
above.

## W6 — governance envelope now writes sha256

`collect_governance` now records `files[fname] = {"present": True,
"sha256": sha256_hex(body), "body": body}` (imports `sha256_hex` from
`cvi.collect.envelope`, the same helper every other per-item hash in this
codebase would use). Verified directly against a live collector run:
production keys now equal the fixture keys exactly, `['body', 'present',
'sha256']`, closing the LENS-TQ-14 field-delta the review measured. No test
changes needed — the ten pre-existing tests that assert a `sha256` key were
already correct.

## W7 — bare field names matching PARAM_INPUT_IDS

Renamed `promotion_ladder_v2`/`promotion_ladder_v2_evidence`/
`ai_posture_v2`/`ai_posture_v2_evidence` to `promotion_ladder`/
`promotion_ladder_evidence`/`ai_posture`/`ai_posture_evidence`. Verified:

```
written fields: ['ai_posture', 'ai_posture_evidence', 'legal_ip',
'legal_ip_evidence', 'promotion_ladder', 'promotion_ladder_evidence']
promotion_ladder in PARAM_INPUT_IDS: True
ai_posture in PARAM_INPUT_IDS: True
legal_ip in PARAM_INPUT_IDS: True
any _v2 suffix left: False
```

**Consequence swept, not deferred:** `promotion_ladder`/`ai_posture` are
*also* the exact names of the pre-existing v1 regex-heuristic fields
(`scripts/derive.py:371-373`, computed earlier in `derive_repo`'s hook
chain). This packet's block runs at the tail and, writing to the same bare
keys, deliberately supersedes those heuristic values — that replacement,
not addition, is what 02 §4 3.3/4.2 (evidence-based classification) asks
for; `legal_ip_model` is the only v1 field with a genuinely distinct name
and is untouched. Measured blast radius of the rename, empirically (full
suite before/after): **exactly one test outside this packet**,
`tests/test_derive.py::test_full_bundle`, which asserted the old heuristic
values `REGULATED`/`DOCUMENTED_PATHWAY` for a fixture that seeds no
governance/pr_window/commits_365d envelope. Updated its two assertions to
the correct post-fix behavior: `AD_HOC` with confidence `"missing"` (B2)
for both, since no governance evidence exists for this fixture. Also
updated `test_derive_repo_v1_governance_fields_unchanged`'s docstring
(unchanged assertions — the heuristic default and the Unmeasured default
both happen to spell `AD_HOC` in that no-evidence fixture) to state
accurately that the source is now the evidence-based classifier, not the
regex heuristic. Grepped the whole tree for every other reader of the old
`_v2`-suffixed names: none found outside historical `docs/v2/00-README.md`
lead-log prose (left untouched — it is a record of what was found/
dispatched, not a code reader).

## W8 — c365 type/crash fix

`_orgs_by_login` and `classify_promotion_ladder`'s `c365` parameter
retyped `Optional[Sequence[Mapping[str, Any]]]` (was `Optional[Mapping]`/
`Optional[Any]`). Added `_row_sequence()`: a dict-shaped `c365` (iterates
over its keys, not row mappings) is now treated as "no rows" everywhere
(`_orgs_by_login`, `signoff_share`, and transitively `classify_promotion_ladder`
and `derive_governance_fields`) instead of raising `AttributeError` on the
first `row.get(...)`.

```
$ pytest -p no:cacheprovider tests/collect/test_governance_w16.py -k dict_shaped
....                                                                     [100%]
4 passed, 67 deselected in 0.06s
```

## W1 — SIGNOFF_THRESHOLD actually read

`count * 2 >= total` replaced with `(count / total) >= SIGNOFF_THRESHOLD`
(guarded by the existing `total > 0` check). Test changes the constant
itself via `monkeypatch.setattr(gov, "SIGNOFF_THRESHOLD", ...)`, not the
per-value inputs, so the lead's named mutant ("drop the threshold to 0.0")
is now caught by construction:

```
$ pytest -p no:cacheprovider tests/collect/test_governance_w16.py -k signoff_threshold_is_read
.                                                                        [100%]
1 passed, 70 deselected in 0.06s
```

## Full suite (real output, twice, both exit 0)

```
$ pytest tests/ -n 4 -p no:cacheprovider
1609 passed, 12 warnings in 14.59s
$ pytest tests/ -n 4 -p no:cacheprovider   # re-run, stability check
1609 passed, 12 warnings in 14.43s
```

(1585 passed on the same command immediately after the rebase, before any
round-2 fix; +24 net new tests added this round.)

## Ruff

```
$ ruff check cvi/collect/governance.py cvi/collect/commits.py scripts/derive.py \
    scripts/gather.py tests/conftest.py tests/collect/test_governance_w16.py \
    tests/collect/test_depsdev.py tests/collect/test_pr_window.py \
    tests/test_repo_object.py tests/test_backfill_downloads.py \
    tests/test_commits_ef.py tests/test_derive.py
All checks passed!
$ ruff check cvi/ scripts/ tests/
Found 4 errors.   # all four in scripts/curate_dataset_llm.py, a file this packet never touches (pre-existing, same as round 1's Nit N3)
```

## COULD NOT VERIFY (round 2)

* Whether a `_row_sequence`-style guard is also warranted on other
  `commits_365d` consumers outside this packet (`cvi/collect/commits.py`
  itself, `cvi/pillars/`); not swept, since W8's scope is this packet's own
  `governance.py` functions.
* `cvi/derive/bundle.py`'s `V1_FIELD_TO_INPUT` table reads `legal_ip_model`
  (the v1 heuristic), not `legal_ip`, for the `legal_ip` v2 input id: this
  packet's own `legal_ip` field is still orphaned from that specific wiring
  path even after W7, because `legal_ip` has no bare-name collision with a
  v1 field to resolve by superseding (unlike `promotion_ladder`/
  `ai_posture`), so W7's literal instruction did not reach it. Whether the
  table gets a third mapping entry is a decision for whichever packet wires
  the v2 scorer, matching round 1's existing COULD-NOT-VERIFY on the same
  open question.
* The two items carried over unresolved from round 1 (the one `sample-200`
  manifest gap, and the `kubernetes/kubernetes` `pr_window` default-page-size
  GraphQL error) — unchanged, not part of round 2's dispatch.

## Live-call budget (round 2)

0 live repositories used. Both live-repo checks reused round 1's stored
envelopes (`/home/pctablet505/Projects/cvi-scratch/W1-6s/raw/`), with the
network patched to raise, proving no live call happened during
recomputation.
