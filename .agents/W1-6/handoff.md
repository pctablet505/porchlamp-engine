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
 porchlamp/collect/commits.py               |  25 +-
 porchlamp/collect/governance.py            | 630 +++++++++++++++++++++++++++++
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

Source only (excludes tests): `porchlamp/collect/commits.py` + `porchlamp/collect/governance.py`
(new) + `scripts/derive.py` + `scripts/gather.py` = 689 insertions / 4 deletions,
~685 net new source lines. Packet size **L** (≤ 800) — no size deviation.

## Hook rule

Exactly one new call in `scripts/gather.py` (the `governance` collector
block) and one in `scripts/derive.py` (the governance-fields block),
appended at the **end** of each file's existing hook chain — after the
already-landed W1-8 `downloads_status`/`downloads` blocks, which is the true
end of the chain on `e29fb79` (W1-4 and W1-7 are not yet landed on this base).

## Ownership extension: `porchlamp/collect/commits.py` (pre-authorized by the dispatch)

The dispatch's own text anticipated this: "the W1-5 commits collector ...
stores author emails; if commit messages are not stored, extend that
collector minimally to record a per-commit boolean `signed_off` rather than
the message text." `shape_commit_rows` already receives `commit.message` in
the raw REST response (the same `repos/{o}/{r}/commits` listing W1-5 already
pages) but discarded it; it now also computes `signed_off: bool` (the
`Signed-off-by:` trailer) per row and keeps discarding the message text
itself. No new live call, no message text stored. `porchlamp.collect.governance`
reads this boolean from the already-collected `commits_365d` envelope for
3.1's sign-off share — it never fetches commits itself.

## Deviations from the archived draft (R-6: subtractive rework)

1. **Removed a duplicate live fetch and duplicated constants (R-2).** The
   archived `governance.py` re-fetched the trailing 365 d of commits *with
   messages* inside `collect_governance` (`_signoff_commits`, plus its own
   `SIGNOFF_WINDOW_DAYS=365`/`SIGNOFF_COMMIT_CAP=1000`/`SIGNOFF_PAGE_SIZE=100`
   — an exact re-statement of `porchlamp.collect.commits`'s
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
absent for `porchlamp/collect/governance.py`, so it was removed instead), ran the
tests, then restored my versions from a scratch backup (byte-identical,
verified with `diff -q`) — never `git stash`, never `git checkout --`.

### Proof 1 — the whole W1-6 packet (governance module + both hooks)

Before (`scripts/derive.py`/`scripts/gather.py` restored to `e29fb79`,
`porchlamp/collect/governance.py` removed):

```
$ pytest tests/collect/test_governance_w16.py -p no:cacheprovider -q
ERROR collecting tests/collect/test_governance_w16.py
ImportError: cannot import name 'governance' from 'porchlamp.collect'
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

### Proof 2 — `porchlamp/collect/commits.py`'s `signed_off` extension

Before (`porchlamp/collect/commits.py` restored to `e29fb79`):

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
or `porchlamp.collect.commits` — the full blast radius of this packet's changes,
found by `grep -rl`.)

## Ruff

```
$ ruff check porchlamp/collect/governance.py porchlamp/collect/commits.py scripts/derive.py \
    scripts/gather.py tests/collect/test_depsdev.py tests/collect/test_pr_window.py \
    tests/test_repo_object.py tests/test_backfill_downloads.py \
    tests/test_commits_ef.py tests/collect/test_governance_w16.py
All checks passed!
```

`ruff check porchlamp/ scripts/ tests/` reports 23 pre-existing errors, all in
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

Ran the real `porchlamp.collect.governance.collect_governance` and
`porchlamp.collect.commits.collect_commits_365d` (no injected fakes) via a thin
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
* Whether `porchlamp/core/constants_v2.py`'s bare `promotion_ladder`/`ai_posture`
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
 porchlamp/collect/governance.py            | 233 ++++++++++++++++++++++++--------
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
Unmeasured — `porchlamp.derive.bundle._load_v1` maps a `"missing"` confidence to
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
`porchlamp.collect.envelope`, the same helper every other per-item hash in this
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
$ ruff check porchlamp/collect/governance.py porchlamp/collect/commits.py scripts/derive.py \
    scripts/gather.py tests/conftest.py tests/collect/test_governance_w16.py \
    tests/collect/test_depsdev.py tests/collect/test_pr_window.py \
    tests/test_repo_object.py tests/test_backfill_downloads.py \
    tests/test_commits_ef.py tests/test_derive.py
All checks passed!
$ ruff check porchlamp/ scripts/ tests/
Found 4 errors.   # all four in scripts/curate_dataset_llm.py, a file this packet never touches (pre-existing, same as round 1's Nit N3)
```

## COULD NOT VERIFY (round 2)

* Whether a `_row_sequence`-style guard is also warranted on other
  `commits_365d` consumers outside this packet (`porchlamp/collect/commits.py`
  itself, `porchlamp/pillars/`); not swept, since W8's scope is this packet's own
  `governance.py` functions.
* `porchlamp/derive/bundle.py`'s `V1_FIELD_TO_INPUT` table reads `legal_ip_model`
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

---

# Round 3 (REWORK response)

Base: rebased branch `W1-6s` from `f924b90` onto main tip `3e53358` (clean, no
conflicts), then again onto `1f8e428` before the final commit (see below).
Review: `/tmp/claude-1000/-home-pctablet505-Projects/2a48e84a-2b94-4a2f-8bff-adbe955a4084/scratchpad/reviews/W1-6-r2-review.md`,
item records in `.agents/W1-6/review/round2/` (untracked, left as delivered).

Diffstat (round-3 content only):

```
 porchlamp/collect/governance.py            | 181 ++++++++++++++++++++---------------
 porchlamp/derive/bundle.py                 |  28 +++++-
 tests/collect/test_governance_w16.py | 156 +++++++++++++++++++++++++++++-
 tests/collect/test_w17_pike.py       |  13 ++-
 4 files changed, 294 insertions(+), 84 deletions(-)
```

## BB1 + BB2 — table-driven ai_posture, corpus-driven, no ad hoc regex

**Deleted**: the round-2 line-level `_is_negated_ban_line` guard and its three
helper patterns (`NEGATION_PATTERN`, `_AI_TERM_PATTERN`, `_BAN_VERB_PATTERN`),
and the six-pattern `AI_BAN_PATTERNS` pile entirely (including the bare
`\b(no|not|never|without)\s+<AI>` alternation, which had no ban-verb
requirement at all -- round 2 had already half-fixed this by removing one
bad clause; round 3
removes the whole mechanism).

**Replaced with**: `classify_ai_posture` now evaluates one SENTENCE at a time
(`_split_sentences`: split on `.` `;` `:` and newlines) against two small,
named tables (documented in the module docstring, `governance.py:162-192`):

* `BAN_TABLE` (11 rows): `banned`, `forbidden`, `prohibited`, `disallowed`,
  `rejected` (bare polarity words, active or passive inflection);
  `negated_permission` (`not allowed/accepted/permitted/welcome/encouraged`
  -- a negated permission word IS a ban); `must_not`; `do_not`; `never`;
  `will_be_closed`; `passive_generated_by_ai` (BB2's own example: `<must|
  shall|may|can> not be <generated|written|authored|created|produced> <by|
  with|using> AI`).
* `PERMISSION_TABLE` (8 rows): `negated_ban_verb` (a negator -- not/never/
  nothing/no/do not/does not/don't/doesn't -- within up to 3 words of a
  ban-polarity verb, never crossing "and"/"but"/"or" -- this is the general
  form of round 2's three named false positives, generalized so it also
  catches "nothing in this document BANS..." and "there is no rule
  FORBIDDING..." without also swallowing "...is not permitted AND is
  BANNED." two-clause sentences); bare `allowed`/`permitted`/`accepted`/
  `welcome`/`encouraged` (excluded right after `not`/`never`, since that
  exact two-word phrase is itself a BAN_TABLE row); `may_use`; `no_x_required`.

PROSCRIBED iff the sentence has an AI reference, a BAN_TABLE match, and no
PERMISSION_TABLE match (`_proscribed_sentence`). `_ban_match` now returns
the first PROSCRIBED sentence across all governance/AI-policy files, so a
ban in one sentence is never shadowed by a denial in a different sentence.

**34-sentence labelled corpus** (`PROSCRIBED_CORPUS`, `tests/collect/
test_governance_w16.py`), 18 positive / 16 negative, containing verbatim:
round 1's three negated-ban false positives; the dispatch's passive-voice
positive control and four extra named probes; BB1's five round-1-correct
sentences round 2 regressed; the round-2 review's own five-a-side probe
corpus; and real sentences already used as governance/AI-policy fixture
bodies elsewhere in this file (CONTRIBUTING.md/AI.md/AGENTS.md prose this
packet already ships).

```
$ pytest -p no:cacheprovider "tests/collect/test_governance_w16.py::test_proscribed_corpus_size_and_balance" "tests/collect/test_governance_w16.py::test_proscribed_sentence_corpus"
..................................                                       [100%]
34 passed in 0.06s
```

**Row-deletion mutants, each demonstrated two ways** -- a standing test in
the file (runtime `gov.BAN_TABLE`/`gov.PERMISSION_TABLE` monkeypatch,
restored in a `finally`) and a real source-edit-and-restore against the
full parametrized corpus test (pasted below, `git status --short` empty
after every restore):

```
$ sed -i '/("banned", r"\\b(ban|bans|banned|banning)\\b"),/d' porchlamp/collect/governance.py
$ pytest -p no:cacheprovider "tests/collect/test_governance_w16.py::test_proscribed_sentence_corpus" -v
tests/collect/test_governance_w16.py::test_proscribed_sentence_corpus[AI contributions are banned.-True] FAILED
AssertionError: 'AI contributions are banned.': expected proscribed=True, got False
1 failed, 33 passed in 0.08s
```

```
$ sed -i '221,226d' porchlamp/collect/governance.py   # the negated_ban_verb permission row
$ pytest -p no:cacheprovider "tests/collect/test_governance_w16.py::test_proscribed_sentence_corpus" -v
9 failed, 24 passed in 0.13s
  ...[We do not ban AI-generated contributions.-False]
  ...[Nothing here forbids using AI tooling.-False]
  ...[AI tools are not banned-False]
  ...[we never reject AI-assisted work-False]
  ...[AI usage is not restricted here, and we do not ban Copilot.-False]
  ...[There is no rule forbidding AI-assisted commits.-False]
  ...[We have never prohibited AI tooling.-False]
  ...[Nothing in this document bans LLM usage.-False]
  ...[AI is not a banned topic in discussions.-False]
```

**Round-2 -> round-3 re-derivation over the full 34-sentence corpus**
(`classify_ai_posture` on a one-file `CONTRIBUTING.md` body per sentence;
round-2 module loaded standalone via `git show c5af7f1:...`):

| sentence | round 2 | round 3 | expected |
|---|---|---|---|
| This project has no AI policy yet. | AD_HOC | AD_HOC | not proscribed |
| We do not ban AI-generated contributions. | AD_HOC | AD_HOC | not proscribed |
| Nothing here forbids using AI tooling. | AD_HOC | AD_HOC | not proscribed |
| contributions must not be generated by AI | AD_HOC | **PROSCRIBED** | proscribed |
| AI tools are not banned | AD_HOC | AD_HOC | not proscribed |
| no AI disclosure required | AD_HOC | AD_HOC | not proscribed |
| we never reject AI-assisted work | AD_HOC | AD_HOC | not proscribed |
| We do not accept AI-generated PRs | PROSCRIBED | PROSCRIBED | proscribed |
| AI-generated pull requests are banned; do not open them. | AD_HOC | **PROSCRIBED** | proscribed |
| Do not submit AI-generated code; such contributions are prohibited. | AD_HOC | **PROSCRIBED** | proscribed |
| AI-generated code is not permitted and is banned. | AD_HOC | **PROSCRIBED** | proscribed |
| Do not submit AI code; AI is prohibited. | AD_HOC | **PROSCRIBED** | proscribed |
| AI contributions are never accepted and are forbidden. | AD_HOC | **PROSCRIBED** | proscribed |
| AI-generated pull requests will be closed. | AD_HOC | **PROSCRIBED** | proscribed |
| (remaining 20 corpus sentences) | unchanged | unchanged | matches |

7 of 34 flip AD_HOC -> PROSCRIBED (exactly BB1's five regressions + BB2's
named miss + the new `will_be_closed` row); the other 27 are unchanged --
zero new false positives introduced. `probe_b3_round1.out` (reviewer's
scratch) independently confirms the same round-1/round-2 deltas on the
5-sentence BB1 subset.

**"12 fixtures"**: no artifact named "12 fixture repos" exists in this
project (searched the review, its item records, and its scratch outputs);
`GOVERNANCE_FILES + AI_POLICY_FILES` is the only literal "12" in this
packet, so the corpus above draws on real bodies from that file set instead
(disclosed, not a COULD-NOT-VERIFY -- the corpus and the five-sample-repo
table below jointly satisfy the "re-derive and paste before/after" ask).

**Five sample repos** -- their integration table is under BB3 below (the
scenario bodies are governance-file text, not ban-detection edge cases, so
none of the five changes classification between round 2 and round 3).

## BB3 — wire the three parameters into the v2 scorer

Edited only the three named `INPUT_MAPPINGS` rows in `porchlamp/derive/bundle.py`
(970-1010 range):

* `ai_posture` -- unchanged (already correct: an identity vocab over the
  bare `ai_posture` field).
* `promotion_ladder` -- now `_vocab_scalar_resolver("promotion_ladder",
  {**V1_LADDER_VALUES, **{v: v for v in constants_v2.PROMOTION_LADDER_VALUES}})`:
  accepts the v2 vocabulary (VERIFIED/DOCUMENTED/AD_HOC/CLOSED) as identity
  AND keeps `V1_LADDER_VALUES` as a legacy fallback for any not-yet-re-derived
  v1-only document.
* `legal_ip` -- now `_vocab_scalar_resolver("legal_ip", {v: v for v in
  constants_v2.LEGAL_IP_VALUES})`: reads THIS packet's own bare `legal_ip`
  field (envelope path + sha256 provenance already correct, B2), with no
  fallback to `legal_ip_model` -- deleted that read from the row entirely,
  per the dispatch. `V1_LEGAL_IP_VALUES` (bundle.py:66) is now unused by
  this row; left defined (edit scope was "only the three rows").

**Consequence, measured and reported, not silently patched**: three tests
outside this packet's ownership assert the OLD `legal_ip_model` wiring and
now fail --

```
FAILED tests/derive/test_input_mappings.py::test_legal_ip
  assert Unmeasured(reason='collector_failure',
    detail="v1 confidence None for 'legal_ip' is not recognized")
  == Measured(value='CORP_CLA', ...)   # asserted the v1 legal_ip_model wiring
FAILED tests/core/test_sample200_load.py::test_measured_inputs_carry_envelope_provenance
  assert isinstance(Unmeasured(...), Measured)   # same root cause
FAILED tests/core/test_sample200_load.py::test_provenance_hash_matches_independent_recomputation
  assert '83b52939...' == '9d03c1ad...'          # provenance_hash shifts because legal_ip's
                                                  # referenced envelope changed
```

`test_promotion_ladder` (same file) is unaffected -- the merge is additive,
so the v1 spellings it exercises (`VERIFIED_PROMOTION`, `ROGUE_RUNG`) still
resolve exactly as before. `test_ai_posture*` is untouched (no row change).
All three failures trace to one root cause (the deleted `legal_ip_model`
fallback) and are the direct, intended, and dispatch-authorized consequence
of BB3 ("that is a finding to report, not a reason to keep it") -- not
touched here; they belong to W2-9/INT-W0-4's ownership.

**Integration proof, real collector + stubbed transports, zero network**
(`cvi-scratch/W1-6-r3/integration_with_governance.py`, adapted from the
reviewer's own script, same five `sample-200` repos by github_meta-key
order, same five scenarios):

| repo / scenario | before (round 2) | after (round 3) |
|---|---|---|
| `0voice__interview_internal_reference` (DCO + nomination doc) | legal_ip Unmeasured(collector_failure); **promotion_ladder Unmeasured** (`'DOCUMENTED' has no v2 vocabulary mapping`); ai_posture Measured('AD_HOC') | legal_ip **Measured('DCO')**; promotion_ladder **Measured('DOCUMENTED')**; ai_posture Measured('AD_HOC') |
| `1995parham__github-do-not-ban-us` (EasyCLA + REGULATED) | legal_ip Unmeasured; promotion_ladder Measured('AD_HOC'); ai_posture Measured('REGULATED') | legal_ip **Measured('FOUNDATION_CLA')**; promotion_ladder Measured('AD_HOC'); ai_posture Measured('REGULATED') |
| `AtsushiSakai__PythonRobotics` (corp CLA + ban) | legal_ip Unmeasured; promotion_ladder Measured('AD_HOC'); ai_posture Measured('PROSCRIBED') | legal_ip **Measured('CORP_CLA')**; promotion_ladder Measured('AD_HOC'); ai_posture Measured('PROSCRIBED') |
| `EpicGames__lore` (no evidence) | legal_ip Unmeasured; promotion_ladder Measured('AD_HOC'); ai_posture Measured('AD_HOC') | legal_ip **Measured('NONE')**; promotion_ladder Measured('AD_HOC'); ai_posture Measured('AD_HOC') |
| `EvanLi__Github-Ranking` (observed transition) | legal_ip Unmeasured; **promotion_ladder Unmeasured** (`'VERIFIED' has no v2 vocabulary mapping`); ai_posture Measured('AD_HOC') | legal_ip **Measured('DCO')**; promotion_ladder **Measured('VERIFIED')**; ai_posture Measured('AD_HOC') |

**The FOUNDATION_CLA repo (`1995parham__github-do-not-ban-us`) now shows
`Measured('FOUNDATION_CLA')`, not `Measured('DCO')`** -- the exact defect
named in the dispatch. Per-file sha256 recomputed independently over the
five scenarios' bodies: `files checked=4 mismatches=0`.

sample-200's own 197-repo census is unaffected either way (`legal_ip` 109/88,
`promotion_ladder` 0/197, `ai_posture` 0/197, identical before/after): the
raw tree predates `governance`/`pr_window`/`commits_365d` entirely, so every
row is `Unmeasured(collector_failure)` regardless of the wiring fix -- this
matches the round-2 review's own finding and needs live collection to move
(out of scope this round; **note**: `/home/pctablet505/Projects/cvi-scratch/sample-200`
was found renamed to `sample-200.RENAMED` partway through this round by
another concurrent process in this shared environment, not by this
packet -- it reappeared before the final full-suite run below).

## B4 leaks (WW2) + W6 (item 5 residual)

**WW2** -- `test_governance_w16.py`'s own three `gather_repo` tests now
request `block_live_network` and seed `releases`/`issues_counts`/`runs`
(W1-7) with the correct new-shape `gfi` record (the old bare `[]` fails
`is_new_gfi_envelope` and re-triggers a live `labels` call).

**WW3** -- `test_w17_pike.py`'s three `gather_repo` tests now request
`block_live_network` and seed `commits_365d`/`dependents`/`pr_window`/
`governance` (all four leaks measured empirically before the fix, not just
W1-6's own new `…/pulls` one -- the dispatch asked for 0 across both files,
not only the newly-introduced leak).

Per-file attempt counts, plain pytest, guard-only (no external harness):

```
$ pytest -p no:cacheprovider tests/collect/test_governance_w16.py::test_gather_repo_governance_status_ok tests/collect/test_governance_w16.py::test_gather_repo_governance_status_skip tests/collect/test_governance_w16.py::test_gather_repo_governance_status_fail
...                                                                       [100%]
3 passed in 0.07s        (0 attempts)

$ pytest -p no:cacheprovider tests/collect/test_w17_pike.py::test_gather_repo_w17_collectors_ok tests/collect/test_w17_pike.py::test_gather_repo_w17_collectors_skip tests/collect/test_w17_pike.py::test_gather_repo_w17_collectors_fail
...                                                                       [100%]
3 passed in 0.06s        (0 attempts)

$ pytest -p no:cacheprovider tests/collect/test_governance_w16.py   # whole file
........................................................................ [ 66%]
.....................................                                    [100%]
109 passed in 0.09s

$ pytest -p no:cacheprovider tests/collect/test_w17_pike.py         # whole file
.................................                                        [100%]
33 passed in 0.09s
```

(Reproduced the empirical leak set before fixing: 5 attempts per
`test_w17_pike.py` gather_repo test -- `…/commits`, `api.deps.dev`,
`packages.ecosyste.ms`, `gh api graphql`, `…/pulls` -- matching the
review's count of 4 pre-existing + 1 new.)

## W6 (item 5 residual)

Added `test_collect_governance_per_file_sha256_matches_independent_recomputation`:
12 distinct fixture bodies (all of `GOVERNANCE_FILES + AI_POLICY_FILES`),
sha256 recomputed via a plain `hashlib.sha256` call (never importing the
collector's own `sha256_hex`, so a bug shared by both sides could not cancel
out). Mutant M8 (`sha256_hex(body)` -> `sha256_hex(fname)`) now dies:

```
$ sed -i 's/sha256_hex(body)/sha256_hex(fname)/' porchlamp/collect/governance.py
$ pytest -p no:cacheprovider tests/collect/test_governance_w16.py
FAILED tests/collect/test_governance_w16.py::test_collect_governance_per_file_sha256_matches_independent_recomputation
AssertionError: GOVERNANCE.md
assert 'b60c6a93e9f7...' == 'ff6dbb0409c9...'
1 failed, 71 passed
```
(restored; `git status --short` empty)

## Full suite, ruff, rebase

```
$ git -C /home/pctablet505/Projects/contributor-value-index log --oneline -1
fb1f30b docs(v2): lead log — W2-8b received with rulings R-1..R-3; fixture is unrated end to end (W2-8c to refresh)
$ git rebase fb1f30b          # two hops, both clean: 3e53358 -> 1f8e428 -> fb1f30b
Successfully rebased and updated refs/heads/W1-6s.   (clean, no conflicts, no manual resolution)

$ pytest tests -p no:cacheprovider -n 4
FAILED tests/core/test_sample200_load.py::test_measured_inputs_carry_envelope_provenance
FAILED tests/core/test_sample200_load.py::test_provenance_hash_matches_independent_recomputation
FAILED tests/derive/test_input_mappings.py::test_legal_ip
3 failed, 1830 passed, 12 warnings in 10.98s
```

All three failures are the single, dispatch-authorized BB3 consequence
above; the total grew from 1761 to 1830 across the rebase hops purely from
other packets landing on main in the interim (nothing in this packet's own
diff), and the same 3-failure signature reproduced immediately after the
final rebase. During the round, `cvi-scratch/sample-200` was twice observed
renamed to `sample-200.RENAMED` by an unrelated concurrent process in this
shared environment, which makes the two `test_sample200_load.py` failures
above read as 8 skips instead ("sample-200 not present at ...", that file's
own pre-existing marker) -- external, not a regression from this packet;
it had reappeared by the time of the final rebase run pasted above.

```
$ ruff check porchlamp/collect/governance.py porchlamp/derive/bundle.py tests/conftest.py \
    tests/collect/test_governance_w16.py tests/collect/test_w17_pike.py
All checks passed!
$ ruff check porchlamp/ scripts/ tests/
Found 4 errors.   # all four in scripts/curate_dataset_llm.py, pre-existing, untouched
```

## COULD NOT VERIFY (round 3)

* The literal "12 fixture repos" artifact named in the dispatch -- not
  found anywhere in this project (repo, review, or scratch); interpreted as
  the 12 `GOVERNANCE_FILES + AI_POLICY_FILES` file-name set and covered via
  the 34-sentence corpus instead (disclosed above).
* Whether `porchlamp/lakehouse.py`'s v1 ingest path (flagged NOT CHECKED by the
  round-2 review) reads the new `promotion_ladder` v2 spellings correctly --
  out of this packet's owned files, not swept this round either.
* `hard_ai_ban` (WW6, `scripts/derive.py:377`, computed from the v1 enum
  before the W1-6 block overwrites `ai_posture`) -- unresolved; not named in
  this round's dispatch, left for a future round or the lead's ruling on
  WW6 (also ties to the `porchlamp/models.py:196` `promotion_ladder: PromotionLadder`
  type-vocabulary question).
* Round-1/round-2's carried-over open items (WW4 pr_window-failure-still-
  measured, WW5 evidence-richness, the sample-200 manifest gap) -- unchanged,
  not named in this round's dispatch.

## Live-call budget (round 3)

0 live repositories used. The five-sample-repo integration proof used the
real `collect_governance` with a fully stubbed `StubApi` transport and a
stubbed `fetch_file` callable (zero network, per-call count logged and
pasted above).

---

# Round 3b (lead-authorized test updates)

Lead authorization: update the three out-of-ownership tests that asserted
the old `legal_ip_model` wiring, to the new contract. One commit on top of
`563afa0`.

## `tests/derive/test_input_mappings.py::test_legal_ip`

Rewritten to mirror `test_ai_posture`'s own shape exactly (same envelope,
same identity-vocab pattern): writes a `governance` envelope with a bare
`legal_ip` field, asserts `Measured` from it. Two negative cases kept
(`confidence="missing"` -> Unmeasured; an out-of-vocabulary value -> the
row-scoped W2-9f Unmeasured, never a whole-bundle rejection). The third
negative case (a `raw.githubusercontent.com` URL source) is dropped: it
was specifically about `legal_ip_model`'s old real-world provenance shape,
which the new governance-envelope-sourced field can never produce (its
source is always a local `governance/`/`commits_365d/` envelope path, never
a raw GitHub URL) -- `test_ai_posture`, on the identical pattern, was never
written with that case either, so this is parity, not a lost guard.

## `tests/core/test_sample200_load.py`

Both fixed against the SAME pinned fixture (`EvanLi__Github-Ranking`),
which predates the governance collector -- the frozen document carries
`legal_ip_model` (old) but never wrote `legal_ip` (new), so its confidence
for the new field is genuinely absent, not merely "missing":

* `test_measured_inputs_carry_envelope_provenance`: the `legal_ip` block
  (previously asserting `Measured('DCO', source=.../tree/...)`) moved to
  `test_unmeasured_inputs_have_documented_reasons`, where it now asserts
  `Unmeasured(reason="collector_failure", detail="v1 confidence None for
  'legal_ip' is not recognized")` -- the literal value `INPUT_MAPPINGS`
  produces for this fixture, verified by direct interpreter reproduction
  before writing the assertion (`cvi-scratch/W1-6-r3/` session output).
  **Old -> new: Measured inputs on this fixture go from 3 to 2**
  (`elephant_factor`, `newcomer_issue_supply` remain; `legal_ip` is why).
* `test_provenance_hash_matches_independent_recomputation`: the referenced-
  envelope list now excludes `"tree"` (one line, `if collector != "tree"`),
  with a docstring note explaining why. Recomputation method (`json.dumps`
  with `sort_keys=True`, `sha256` over the same `ENVELOPE_HASHES` pins) is
  byte-for-byte unchanged, per instruction -- only the SET of envelopes
  fed into it changed, because `"tree"` was referenced only via the deleted
  `legal_ip_model` read. Verified independently before writing the
  assertion: recomputing with `"tree"` excluded reproduces
  `bundle.provenance_hash` exactly; including it does not.
  **Old -> new: 6 referenced envelopes -> 5** (`ENVELOPE_HASHES` itself is
  untouched -- it still pins `"tree"`'s hash for documentation, just no
  longer feeds the recomputed payload).

## Full suite (real output, exit code, twice)

```
$ pytest tests -p no:cacheprovider -n 4
1833 passed, 12 warnings in 8.65s
$ pytest tests -p no:cacheprovider -n 4   # re-run, stability check
1833 passed, 12 warnings in 8.71s
```

0 failed, both runs, exit 0. `cvi-scratch/sample-200` was present (not
renamed) for both runs.

## Ruff

```
$ ruff check tests/derive/test_input_mappings.py tests/core/test_sample200_load.py
All checks passed!
```

---

# Round 4 — De-scope

Base: rebased branch `W1-6s` from `e7f0120` onto main tip `3bbbc01` (>= the
dispatched `91fed25`), clean, no conflicts. Review:
`/tmp/claude-1000/-home-pctablet505-Projects/2a48e84a-2b94-4a2f-8bff-adbe955a4084/scratchpad/reviews/W1-6-r3-review.md`,
item records in `.agents/W1-6/review/round3/`.

Lead decision (per the reviewer's own recommendation): PROSCRIBED detection
in `classify_ai_posture` is structurally unsound after three rounds of
pattern/table tuning (each fixed one false-positive class while
introducing another of the opposite polarity — round 3's own root-cause
finding: co-occurrence of an AI token and a negation/ban token cannot
separate "Do not submit AI-generated code" from "Do not worry if you used
AI", nor "AI is prohibited" from "undisclosed AI use is prohibited"; both
need the ban's *object*, never bound in any round). De-scoped by
subtraction, not reworked a fourth time. PROSCRIBED detection is re-planned
as packet **W1-6b**: a corpus sampled and hand-labelled from real
`CONTRIBUTING.md`/`AGENTS.md`/`AI.md` bodies already in the raw store (not
invented sentences — round 3's review Note NT1), a rule binding the ban
expression to its object, a sentence-driven REGULATED path (NT2: today
REGULATED is file-presence-only; zero sentences drive it), and a
parametrized row-deletion sweep as the standing gate (CR3: round 3's own
corpus pinned only 7 of 19 table rows).

Also fixed before the de-scope work (unrelated pre-existing collision,
surfaced only by rebasing onto a newer main tip):
`tests/test_derive.py::test_provenance_omits_sha256_for_non_envelope_source`
asserted `ai_posture`'s URL provenance, which the packet's own already-
accepted B2/W7 changes overwrite; retargeted to `legal_ip_model`, a field
this packet never touches, on the same fixture (committed separately,
`4659099`, before the de-scope commit below).

## What was deleted (`porchlamp/collect/governance.py`)

* `BAN_TABLE` (11 rows), `PERMISSION_TABLE` (8 rows) — the entire
  round-3 table-driven ban-detection scheme.
* `AI_REFERENCE_PATTERN`, `_split_sentences` (used by nothing else —
  swept the whole tree, confirmed), `_proscribed_sentence`, `_ban_match`.
* The `"ban"` key from `classify_ai_posture`'s evidence dict (nothing
  computes a ban anymore, so there is nothing to report there).
* Every module-docstring paragraph and comment block describing the
  ban-detection design (round 1 through round 3's table redesign),
  replaced with one paragraph naming the de-scope, its reason, the
  `ai_assisted` persona consequence, and the W1-6b pointer.

`classify_ai_posture` is now REGULATED (an `AI_POLICY_FILES` entry present)
else AD_HOC — never PROSCRIBED; no code path returns it.
`porchlamp.core.constants_v2.AI_POSTURE_VALUES` is untouched (still
`REGULATED`/`PROSCRIBED`/`AD_HOC`) — only the producer is narrowed, per the
dispatch ("the vocabulary stays").

**Net diff, `porchlamp/collect/governance.py`: 35 insertions, 143 deletions
(-108 lines).**

## Tests (`tests/collect/test_governance_w16.py`)

Deleted: `test_ai_posture_proscribed_ban_in_contributing`,
`_proscribed_ban_in_ai_policy_file`, `_proscribed_beats_policy_file`
(round 1); `_files_with_contributing_body` and its four round-2 B3 tests
(`_no_ai_policy_yet_does_not_derive_proscribed`,
`_do_not_ban_ai_contributions_does_not_derive_proscribed`,
`_nothing_forbids_ai_does_not_derive_proscribed`,
`_negation_does_not_blind_a_real_ban_in_the_same_document`); the entire
round-3 corpus block (`PROSCRIBED_CORPUS`,
`test_proscribed_corpus_size_and_balance`, `test_proscribed_sentence_corpus`,
`test_proscribed_corpus_dies_when_a_ban_table_row_is_deleted`,
`test_proscribed_corpus_dies_when_a_permission_table_row_is_deleted`).

Kept unchanged: `test_ai_posture_regulated_policy_file_present` (REGULATED
via file presence — the path the reviewer confirmed exists and is
exercised). Fixed: `test_ai_posture_ad_hoc_default` (dropped the
now-nonexistent `ev["ban"]` key, asserted `"ban" not in ev` instead).

Added, per the dispatch's item 2:

* `test_ai_posture_ordinary_contributing_prose_is_ad_hoc_never_proscribed`
  — parametrized over the round-3 review's own nine CONTRIBUTING.md
  sentences (item 1, section 2.3: seven of nine had mis-derived PROSCRIBED
  under the deleted scheme). **All nine now yield AD_HOC** — none is
  REGULATED, because CONTRIBUTING.md is not in `AI_POLICY_FILES` and
  REGULATED no longer reads body content at all, only file presence.
* `test_ai_posture_explicit_ai_policy_section_is_regulated` — a real
  `AI_POLICY.md` (a genuine `AI_POLICY_FILES` member) with an explicit "AI
  usage policy" section yields REGULATED.
* `test_ai_posture_never_proscribed_over_all_twelve_files` — all
  `GOVERNANCE_FILES + AI_POLICY_FILES` (12 files) present at once, each
  carrying one of the nine cycled sentences: asserts `!= "PROSCRIBED"`
  (structurally unreachable) and `== "REGULATED"` (the `AI_POLICY_FILES`
  entries' presence, exactly as it would for any other body text).

```
$ pytest -p no:cacheprovider tests/collect/test_governance_w16.py
........................................................................ [ 94%]
....                                                                     [100%]
76 passed in 0.08s
```

Net diff: 68 insertions, 164 deletions.

## M8 pin (`tests/derive/test_input_mappings.py`)

Added `test_promotion_ladder_accepts_both_v2_identity_and_legacy_v1_spellings`:
seven cases (each of the four v2 spellings VERIFIED/DOCUMENTED/AD_HOC/CLOSED
plus three of the four legacy v1 spellings VERIFIED_PROMOTION/
DOCUMENTED_PATHWAY/CLOSED_SINGLE_FIRM — AD_HOC needs no separate legacy
case, both vocabularies spell it identically) written into the v1
`promotion_ladder` field and loaded through `INPUT_MAPPINGS`, asserting the
exact `Measured` value, source and hash. `test_promotion_ladder` (the
pre-existing test) only ever feeds legacy v1 spellings, so it cannot catch
M8 by itself — this new test is what does.

**M8 re-injected and re-run (red):**

```
$ python3 -c "... replace the two-vocab merge with _vocab_scalar_resolver('promotion_ladder', V1_LADDER_VALUES) ..."   # M8, re-injected
$ pytest -p no:cacheprovider "tests/derive/test_input_mappings.py::test_promotion_ladder_accepts_both_v2_identity_and_legacy_v1_spellings"
FAILED tests/derive/test_input_mappings.py::test_promotion_ladder_accepts_both_v2_identity_and_legacy_v1_spellings
AssertionError: 'VERIFIED' -> expected Measured('VERIFIED')
assert Unmeasured(reason='collector_failure', detail="v1 value 'VERIFIED' for
  'promotion_ladder' has no v2 vocabulary mapping") == Measured(value='VERIFIED', ...)
1 failed in 0.05s
```

Restored (`git diff --stat porchlamp/derive/bundle.py` empty afterward); re-run
green, `32 passed`.

## Full suite, ruff (real output, twice, both exit 0)

```
$ pytest tests -p no:cacheprovider -n 4
1856 passed, 12 warnings in 9.31s
$ pytest tests -p no:cacheprovider -n 4   # re-run, stability check
1856 passed, 12 warnings in 9.30s (line varies by ~0.01s; count identical)
```

```
$ ruff check porchlamp/collect/governance.py tests/collect/test_governance_w16.py tests/derive/test_input_mappings.py
All checks passed!
```

## Diffstat, this round (three files)

```
 porchlamp/collect/governance.py            |  35 insertions(+), 143 deletions(-)
 tests/collect/test_governance_w16.py |  68 insertions(+), 164 deletions(-)
 tests/derive/test_input_mappings.py  |  37 insertions(+),   0 deletions(-)
```

## Re-plan pointer

**Packet W1-6b** (4.2 `ai_posture` PROSCRIBED detection, standalone unit):
corpus sampled and hand-labelled from real `CONTRIBUTING.md`/`AGENTS.md`/
`AI.md` bodies already in the raw store; a rule binding the ban expression
to the AI reference as its grammatical object (not bare co-occurrence); a
sentence-driven REGULATED path so a disclosure requirement has somewhere to
land; a parametrized row-deletion sweep as the standing gate, run over
every row of whatever table replaces this round's deleted ones. Until
W1-6b lands, the `ai_assisted` persona's hard-ban gate cannot fire — no
repository can score the PROSCRIBED regime through this collector.

## COULD NOT VERIFY (round 4)

* Round-3's remaining Warnings not named in this round's dispatch (CW3
  undiagnostic Unmeasured detail, CW4 no legal_ip URL-provenance negative,
  CW5/CW6/CW7 = round-2's WW4/WW5/WW6 carried forward unchanged) —
  untouched, not part of this round's scope.
