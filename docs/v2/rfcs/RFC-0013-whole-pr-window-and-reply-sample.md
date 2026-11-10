# RFC-0013: whole-pr-window-and-reply-sample

## Motivation

D-IN-4 (`docs/v2/02-RANKING-DESIGN.md` section 3) recorded at most the newest 500 PRs of a
project's 180-day window (`PR_CAP = 500`, unhashed in `porchlamp.collect.github_graphql`), and
at most the newest 500 PRs of its 365-day author history. On the 2026-10-17 data:

- 1,245 of 10,998 PR windows stop at the cap. Live GitHub counts (issue #51) put 2,593,812 PRs
  in those 1,245 windows: a median of 1,062 each, and 94,069 for `NousResearch/hermes-agent`.
  The 500 recorded PRs cover a median 69 days of the 180; `pytorch/pytorch`'s cover 6.
- 1,791 author histories stop at their cap; 1,238 of them hold no PR older than 180 days, so
  the newcomer band (5.2, 5.3) has nothing to count.
- RFC-0011 already leaves D-GATE-5 UNDECIDED on a cut record. The other window measures (1.1
  TTFHR, 1.2 DR_90, 1.4 GR_14, 1.5 TTM_p90, 4.3, the 4.1 review proxy) never read
  `truncated`: on a cut record they were computed on the newest weeks as if those were the
  window, and the cohort rates (1.3, 5.2, 5.3) on whatever part of their band was recorded.

## Method

Measured before choosing (the plan and its evidence are in the issue's task folder):

- **Cost of every PR with every field.** One 25-PR page of the PR-window query costs 1 point
  and takes 3.59 s on the phone that collects. Every PR of the 1,245 windows would cost 79,510
  more points a cycle (about 79 more hours on the phone), and one record would reach 510 MB.
- **What the measures read.** Decisions, merges, closes and D-IN-2's maintainer list (who merged
  or closed someone else's PR) are PR-level fields: a light query lists 100 PRs a page for
  1 point. Only the reply measures need replies and reviews: 1.1, 1.4 and D-GATE-5, 4.3,
  closed-unread, the D-GATE-6 hand-off notices and the 4.1 review proxy.
- **The choice.** Every PR of the window, every field, up to 2,000 PRs: 10,699 of the 10,998
  windows are then whole exactly as before, only without the cut. Above 2,000 (299 windows on
  the 2026-10-17 counts), every PR with its PR-level fields, and replies and reviews on an
  equal-probability sample of 1,000. That is about 50,900 more points a cycle; the records grow
  from 3.4 GB to about 7.2 GB for the busy set.
- **The sample.** A PR is in it when `sha256("<seed>:<owner>/<name>:<number>")` is among the
  1,000 smallest of its window's (bottom-k): a simple random sample without replacement, so the
  existing medians, shares and Wilson bound run on it unchanged, with no weights. It is drawn
  by PR number with a fixed seed, so a rerun over the same PRs draws the same sample, a PR keeps
  its key as the window slides, and two projects' samples are independent. About 470 of a
  busy project's 1,000 are outside PRs at least 14 days old, which puts D-GATE-5's bound within
  about ±0.04 of the whole window's.
- **Rejected:**
  - every PR with every field and no bound (510 MB records, about 79 more phone hours a cycle);
  - raising the cap alone, to 2,000 or 5,000 (299 or 86 of the busiest stay cut, `pytorch` among
    them);
  - a sample by date slices through GitHub search (each slice is a cluster of consecutive PRs, so
    every measure needs weights, and search counts miss whole projects: 7 of the 1,245 answer 0);
  - a sample without the light list (DR_90, TTM_p90 and the maintainer list would come from the
    sample, and occasional mergers would be missed, so their replies would not count).

## Change

- **D-IN-4, the PR window.** The collector records every PR opened in the window. Up to `PR_CAP`
  it records every field; above it, every PR's PR-level fields, and replies and reviews on the
  `PR_SAMPLE_CAP` sample (`sampled: true`, `sample_size`, `sample_seed`, `sample_cap`, and
  `replies_recorded: false` on each PR outside the sample). `truncated` is true only for a
  record that stops short of the window, which a collector run asked to keep no sample does.
- **D-IN-4, the author history.** Every PR opened in the trailing 365 days, bounded only for
  safety at 250,000 (the busiest measured holds about 153,000; a process bound, not hashed).
- **Readers.** Every window measure (1.1, 1.2, 1.4, 1.5, 4.3, the 4.1 proxy, closed-unread, the
  3.3 ladder's CLOSED, DOCUMENTED and AD_HOC rungs) is `Unmeasured(collector_failure)` on a
  record cut short of the window it reads; GR_14 is judged against the window it is computed
  over (`gr14_window_start`). 1.3 is unmeasured on a history that does not reach 270 days back,
  and 5.2 and 5.3 on one that does not reach 365. The reply measures read the sample; 1.2, 1.5,
  D-GATE-6, 3.3 and D-IN-2's maintainers read every PR.
- **Collection.** A page that fails with a query-weight error (HTTP 502 or 504, a cut-off body,
  a timeout) is read again in pages of 10 from the same cursor, and paging goes on at its own
  size; the phone no longer fetches a whole project again at page size 10.

| Constant | Before | After |
|---|---|---|
| `PR_CAP` | *(absent; 500 unhashed in `porchlamp.collect.github_graphql`)* | 2000 |
| `PR_SAMPLE_CAP` | *(absent)* | 1000 |
| `PR_SAMPLE_SEED` | *(absent)* | 20260912 |

`CONSTANTS_HASH` moves:
`6f95d75a0aa5427288d3e7e8eadc5b9477d3a1816d636ab848b9defc7d6cea5a` ->
`be7023c09aa5514472d2434d33527449db5a490152f0a3e1ea2defaf052ab91a`
(pinned in `tests/core/test_frozen.py`, with a `_FROZEN_TABLE` row for each constant).

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump, matching the RFC-0001..0012
pre-release convention).

## Evidence

- **Fixture** (`tests/fixtures/snapshot_50/`): it holds no `pr_window` envelopes and its bundles are
  already derived, so nothing in it is cut or sampled. Scoring it with the old and the new code gives
  byte-identical records apart from `ScoreRecord.constants_hash`. Only `records_hash` moves
  (`97b96c1c...` -> `27b0ef6f...`, in `expected.json` and `tests/snapshot/test_store_cli.py`), with
  `manifest.json` and `populations.json` restamped by
  `scripts/build_fixture_snapshot.py --rewrite-expected`.
- **Sensitivity** (D-AGG-3): not run. No weight or curve constant changes.
- **Anchors** (D-VER-4): `torvalds/linux` (routed off-platform) and `gcc-mirror/gcc` (unrated,
  coverage 0.5375) are unchanged. `kubernetes/kubernetes` (coverage 0.79 -> 0.5475) and
  `vllm-project/vllm` (0.775 -> 0.51) lose their rating on the 2026-10-17 data, whose records of
  both are cut; see below.
- **Recorded data**: the 2026-10-17 data, derived, built, stored and summarised on a scratch copy,
  as of 2026-10-17T05:35Z, with RFC-0012's code (before) and with this change (after). These
  records were collected with the 500 cap, so they show what the refusal does to cut records, not
  what the whole windows will show:

  | | before | after |
  |---|---|---|
  | judged on D-GATE-5 | 2,745 | 2,745 |
  | black holes | 638 (23.24%) | 638 (23.24%) |
  | undecided on D-GATE-5 for a cut record | 868 | 868 |
  | rated | 4,396 | 3,219 |
  | unrated | 1,305 | 2,482 |
  | gated | 5,299 | 5,299 |
  | rated among the 100 most-starred | 71 | 16 |

  - D-GATE-5 does not move: RFC-0011 already leaves it undecided on a cut record.
  - 1,177 projects lose their rating, none gains one. 1,170 of them have both records cut, 6 only
    the PR window, 1 only the author history. The inputs that become unmeasured among them: 3.3
    (1,176), 4.3 (1,169), 1.5 (1,158), 1.1 (1,116), 1.4 (1,031), the 4.1 proxy (659), 1.2 (355),
    1.3 (337) and 5.2 (1).
  - The issue's plan estimated 288 ranked projects. That estimate refused 1.1, 1.2, 1.4, 1.5 and
    4.3 only; this change also withholds 3.3's absence-based rungs and the 4.1 proxy on a cut
    record, by the same rule.
  - `pytorch/pytorch` stays unrated (coverage 0.5875 -> 0.4075), each refused input naming its
    record's cut.
- **Release order.** A snapshot built with this change from records collected with the 500 cap
  unrates the 1,177 above, so none may be published. The whole windows come from the next
  collection cycle with this collector; its effect is measured on that data before any publish.
