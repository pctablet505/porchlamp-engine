# RFC-0022: pr-reply-node-caps

## Motivation

D-IN-4 (`docs/v2/02-RANKING-DESIGN.md` section 3) and RFC-0013 record a PR window's replies and
reviews via `PR_WINDOW_QUERY` (when the window holds at most `PR_CAP = 2000` PRs) or
`_pr_replies_batch_query` (on the `PR_SAMPLE_CAP = 1000` random sample above `PR_CAP`). Both
queries requested only `reviews(first: 10)` and `comments(first: 20)` without `totalCount` or
`pageInfo`, and `PR_WINDOW_QUERY` / `PR_WINDOW_LIGHT_QUERY` requested
`timelineItems(itemTypes: [READY_FOR_REVIEW_EVENT, CLOSED_EVENT], last: 5)`:

- **Truncated review and comment threads.** When automated review bots (such as
  `copilot-pull-request-reviewer`, which posts a separate `COMMENTED` review on every push) and the
  PR author's own replies fill the first 10 review nodes or the first 20 comment nodes, any human
  maintainer review, approval or substantive comment after node 10 / 20 was omitted from the
  envelope, with no flag that the thread had been cut. Readers of `reviews` and `comments` — 1.1
  TTFHR, 1.4 GR_14 and D-GATE-5 (`porchlamp.core.gates`), 4.3 `substantive_review_rate`, the 4.1
  `pipeline_defenses` review proxy (`proxy:review_coverage` in `porchlamp.collect.pike_signals`),
  `closed_unread` and D-GATE-6's hand-off notice check — treated the 10-review, 20-comment prefix
  as the entire PR thread.
- **Measured impact on a 1,242-PR live sample (issue #66, claim C3).** Across 1,242 external PRs
  fetched with up to 100 reviews, 100 comments and 100 filtered timeline items per PR across 11
  repositories (`apache/doris`, `apache/flink`, `home-assistant/core`, `istio/istio`,
  `kubernetes/kubernetes`, `pallets/flask`, `psf/requests`, `pytorch/pytorch`, `rust-lang/rust`,
  `sharkdp/bat`, `vercel/next.js`; `encode/httpx` had 0 external PRs in the sampled date slices):
  - **93 of 1,242 PRs (7.49%)** exceed 10 reviews or 20 comments: 64 exceed 10 reviews (maximum
    59, on `home-assistant/core`) and 44 exceed 20 comments (maximum 148, on `apache/doris`), for
    94 cut connection lists in total.
  - **First D-IN-2 response missed or delayed on 4 PRs:** 2 PRs in `home-assistant/core`
    (`#178667`, 15 reviews, where `copilot-pull-request-reviewer` and the author occupied the first
    10 review slots before a maintainer `APPROVED` at review 12; and `#181835`, 11 reviews, where
    Copilot and the author occupied the first 10 review slots before a `MEMBER` `APPROVED` at
    review 11) had no D-IN-2 response in the `(10, 20)` prefix (`ttfhr_n` on `home-assistant/core`
    104 -> 106; sample total 698 -> 700). Both PRs were merged within hours, so D-GATE-5 and GR_14
    counted the merge itself as a response (`gate5_flips = 0` across all 1,040 D-GATE-5-eligible
    PRs in the sample). Two further PRs in `home-assistant/core` (`#178579`, `#181761`) had an
    earlier D-IN-2 review after review 10 while a later comment in the first 20 comments had been
    taken as the first response.
  - **4.3 `substantive_review_rate` flips on 5 of 895 decided external PRs:** 3 of 91 in
    `apache/doris` (`0.1538 -> 0.1868`) and 2 of 108 in `home-assistant/core`
    (`0.3333 -> 0.3519`).
  - **4.1 `proxy:review_coverage` non-author `APPROVED` review missed on 31 of 507 merged PRs
    (6.11%):** 14 of 80 in `apache/doris`, 7 of 90 in `apache/flink`, 7 of 93 in
    `home-assistant/core`, 2 of 68 in `istio/istio`, and 1 of 36 in `vercel/next.js`.
- **Sub-claims verified against GitHub GraphQL:**
  - *Review ordering:* `pullRequest.reviews` is ordered by review creation (`createdAt`, when a
    pending review was started). Across the 1,242 PRs, `comments` `createdAt` is monotone on all
    1,242 (`nonmonotone_comments = 0`) and `reviews` `createdAt` is non-monotone on 5
    (`nonmonotone_reviews = 5`, where a pending review was started before an earlier review and
    submitted later). Reading the connection until whole (up to `PR_REPLY_NODE_CAP`) collects every
    review regardless of pending-review creation order.
  - *`timelineItems(itemTypes: [READY_FOR_REVIEW_EVENT, CLOSED_EVENT], last: 5)`:* GitHub applies
    `itemTypes` on the server before taking `last: 5` (`filteredCount` vs `totalCount`). Across the
    1,242 PRs, unfiltered `timelineItems.totalCount` exceeded 5 on 474 PRs (up to 397 on
    `apache/doris`), while filtered `READY_FOR_REVIEW_EVENT` and `CLOSED_EVENT` nodes never
    exceeded 5 (maximum 5, on `vercel/next.js`), producing 0 diffs on `ready_for_review_at`,
    `closed_by_author` or `closed_by_maintainer_at` and 0 D-GATE-5 flips. Raising `last: 5` to
    `last: 100` (`PR_REPLY_PAGE_SIZE`) in both `PR_WINDOW_QUERY` and `PR_WINDOW_LIGHT_QUERY` costs
    0 additional GraphQL points and removes the 5-event ceiling when a PR is reopened and closed
    repeatedly.

## Method

Measured on live GitHub GraphQL before choosing:

- **Distribution of thread lengths.** Across the 1,242 sampled external PRs, median reviews per PR
  is 0.5–2.0 and median comments per PR is 3.0–9.0; 1,149 of 1,242 (92.51%) fit completely inside
  `reviews(first: 10)` and `comments(first: 20)`. Only 15 of 1,242 (1.21%) exceed `(30, 50)`, 1 of
  1,242 (0.08%, `apache/doris` with 148 comments) exceeds `(100, 100)`, and 0 of 1,242 exceed 300
  nodes (`PR_REPLY_NODE_CAP = 300`).
- **Point cost and phone wall time.**
  - Adding `totalCount pageInfo { hasNextPage endCursor }` (`_REPLY_PAGE_FIELDS`) to `reviews` and
    `comments` and raising `timelineItems(..., last: 5)` to `last: 100` keeps `PR_WINDOW_QUERY` at
    1 point per 25-PR page (and 1 point at 10 PRs/page on heavy-query backoff) and
    `PR_WINDOW_LIGHT_QUERY` at 2 points per 100-PR page.
  - A follow-up query by PR number (`PrRepliesFollowUp`) requesting up to `PR_REPLY_PAGE_SIZE = 100`
    additional nodes per cut connection across an aliased batch of up to `PR_REPLIES_BATCH_SIZE = 25`
    PRs costs 1 GraphQL point per batch. Across the 1,242 sampled PRs, only the 93 PRs (94 cut
    connections) with `hasNextPage == True` after the initial `(10, 20)` read need a follow-up —
    4 batch calls (4 points total) instead of inflating every page for the 92.51% of PRs that
    already fit in `(10, 20)`.
  - On 5 repositories timed across 4 pages of 25 PRs each (`kubernetes/kubernetes`, `apache/doris`,
    `pytorch/pytorch`, `home-assistant/core`, `vercel/next.js`), follow-up batches for the cut PRs
    on those 100-PR slices (9, 10, 1, 4 and 6 PRs) each cost 1 point and completed in 1.43 s,
    3.77 s, 1.03 s, 1.14 s and 1.21 s with 0 errors.
  - On an ABBA-interleaved 6-page (150-PR) live run on `apache/doris`, 25 of 150 PRs had cut reply
    connections after the initial page read; `_fetch_reply_followups` resolved all 25 (`cut_after = 0`)
    in 1 `PrRepliesFollowUp` call costing 1 GraphQL point in 6.98 s wall time, with median page
    time 5.18 s (before) vs 6.02 s (after) and 0 mismatches between recorded node counts and
    GitHub's `totalCount`.

## Change

- **D-IN-4, collector (`porchlamp.collect.github_graphql`).**
  - `PR_WINDOW_QUERY` and `_pr_replies_batch_query` request
    `totalCount pageInfo { hasNextPage endCursor }` alongside `nodes` on `reviews(first: 10)` and
    `comments(first: 20)` (`PR_REVIEWS_FIRST = 10`, `PR_COMMENTS_FIRST = 20`).
  - `PR_WINDOW_QUERY` and `PR_WINDOW_LIGHT_QUERY` request
    `timelineItems(itemTypes: [READY_FOR_REVIEW_EVENT, CLOSED_EVENT], last: 100)`
    (`PR_REPLY_PAGE_SIZE = 100`, was `last: 5`).
  - After paging the window (and after `_apply_reply_sample` when the window is sampled),
    `_fetch_reply_followups` reads on by PR number every PR whose replies are recorded
    (`replies_recorded` is not `False`) and whose `reviews` or `comments` connection has
    `pageInfo.hasNextPage == True` and fewer than `PR_REPLY_NODE_CAP` (300) recorded nodes. Each
    round issues aliased `PrRepliesFollowUp` queries in batches of `PR_REPLIES_BATCH_SIZE = 25`
    with the existing `[25, 10, 5, 1]` heavy-query backoff (`_in_aliased_batches`), requesting up
    to `PR_REPLY_PAGE_SIZE = 100` nodes (`min(100, PR_REPLY_NODE_CAP - len(nodes))`) after
    `endCursor` for each cut connection, appending the returned nodes oldest-first and replacing
    `pageInfo` and `totalCount`. Rounds stop when no connection remains to advance or after
    `ceil(PR_REPLY_NODE_CAP / PR_REPLY_PAGE_SIZE) = 3` rounds; a connection that returns no nodes
    while reporting `hasNextPage == True` or lacks `pageInfo` is marked stalled and left cut.
    Follow-up point cost is added to `PrWindowResult.cost_points`.
  - In `collect_pr_window`, `sample_size` counts the PRs in the drawn sample
    (`pr.get(REPLIES_RECORDED_KEY) is not False`), unaffected by whether a sampled PR's thread stays
    cut past `PR_REPLY_NODE_CAP`.
- **D-IN-4, readers (`porchlamp.core.gates`, `porchlamp.collect.github_graphql`, `porchlamp.collect.pike_signals`, `porchlamp.derive.bundle`).**
  - `reply_connection_cut(connection)` and `replies_cut(pr)` report whether a PR node's recorded
    `reviews` or `comments` connection has `pageInfo.hasNextPage is True`. Connections recorded
    before RFC-0022 carry no `pageInfo` and read as whole.
  - `reply_sampled_prs(prs)` computes `maintainer_logins(nodes)` over all PR nodes in the window
    and excludes any PR whose replies remain cut (`replies_cut(pr)`) and whose recorded prefix holds
    no D-IN-2 substantive response (`first_substantive_response(pr, maintainers) is None`). A cut
    PR with no recorded response is withheld from every reply measure (1.1, 1.4, D-GATE-5, 4.3, the
    4.1 review proxy, D-GATE-6 hand-off notices, `closed_unread`) so a thread longer than
    `PR_REPLY_NODE_CAP` is never counted as unanswered or unreviewed from an incomplete prefix.
  - When a cut PR remains in `reply_sampled_prs` because its recorded prefix holds a D-IN-2
    response, each downstream measure withholds the PR if the unrecorded suffix could reverse that
    measure's outcome:
    - **1.1 TTFHR** (`derive_pr_window`): withheld (`ttfhr_hours = None`) when `best_kind == "comment"`
      and `reviews` is cut, or `best_kind == "review"` and `comments` is cut, since the other cut
      connection could hold an earlier response.
    - **1.4 GR_14 and D-GATE-5** (`GhostRatePR.replies_cut`, `evaluate_gate5_window`): withheld from
      `counted_prs` / `population_n` when `not answered_within_14d and pr.replies_cut`, since the
      unrecorded part of the thread may contain a response within the 14 d deadline.
    - **4.1 `code_review_rate` and `_resolve_pipeline_defenses` (`proxy:review_ci`)**: a merged PR
      whose `reviews` connection is cut and has no recorded non-author approval is withheld from the
      denominator and `Measured.n` rather than counted as unapproved.
    - **4.3 `substantive_review_rate`** (`derive_pr_window`, `is_excluded_from_review_denominator`,
      `_resolve_substantive_review_rate`): a decided PR whose replies are cut and whose recorded
      nodes hold no substantive review or D-IN-2 maintainer comment (`>= 80` effective chars or `1+`
      inline review comments) has `substantive_review = None` and is excluded from the denominator
      and `Measured.n`.

| Constant | Before | After |
|---|---|---|
| `PR_REPLY_NODE_CAP` | *(absent; `reviews(first: 10)` and `comments(first: 20)` unhashed in `porchlamp.collect.github_graphql`, with no follow-up)* | 300 |

`CONSTANTS_HASH` moves:
`b81c8bc48501e62976493eda447ce11944a2d7d53a3cd4a126f71cb766c7ff05` ->
`4a6529ef5ca3c97c443442774440b4947ebc849265132fc16a61e3a60f231a2a`
(pinned in `tests/core/test_frozen.py`, with a `_FROZEN_TABLE` row for `PR_REPLY_NODE_CAP`).

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump, matching the RFC-0001..0013
pre-release convention).

## Evidence

- **Fixture** (`tests/fixtures/snapshot_50/`): holds no `pr_window` envelopes and its bundles are
  already derived, so 0 of 50 repositories change rank, composite, tier, or gate outcome (median
  rank shift 0.0, p95 rank shift 0.0). Only `ScoreRecord.constants_hash` moves, moving
  `records_hash` (`2bdc7e4c55957a97ffe90575c5365d5769cd0ba8eb618f2016e7b37cc444241c` ->
  `b4b8f4635ff7287294a2507dfd04104245ee37ef96b5449b352bd1245335ecaa` in `expected.json` and
  `tests/snapshot/test_store_cli.py`) and `merkle_root`
  (`faefcb98a07a5f9b85ab2b368889e3da2be4c3a4405875f7ca23251de685d999` ->
  `c2882572cde049437243f353bfaecdf5748e4bf9679950449baec776dd8b4ad4` in `manifest.json`), with
  `manifest.json` and `populations.json` restamped by
  `scripts/build_fixture_snapshot.py --rewrite-expected`.
- **Sensitivity** (D-AGG-3): no weight or curve constant changes;
  `tests/audit/test_sensitivity_fixture.py` passes unchanged.
- **Anchors** (D-VER-4): all anchor relations in `tests/audit/test_anchors_fixture.py` pass
  unchanged.
- **Live GitHub sample (1,242 external PRs across 11 repositories):**

  | Repository | PRs | >10 rev or >20 com | >10 rev | >20 com | >300 | Missed 1st resp | Delayed 1st resp | TTFHR n (old -> new) | 4.3 flips | 4.3 share (old -> new) | 4.1 merged approval flips |
  |---|---|---|---|---|---|---|---|---|---|---|---|
  | `apache/doris` | 120 | 30 | 16 | 26 | 0 | 0 | 0 | 118 -> 118 | 3 / 91 | 0.1538 -> 0.1868 | 14 / 80 |
  | `apache/flink` | 120 | 11 | 11 | 0 | 0 | 0 | 0 | 77 -> 77 | 0 / 96 | 0.4479 -> 0.4479 | 7 / 90 |
  | `home-assistant/core` | 120 | 13 | 13 | 0 | 0 | 2 | 2 | 104 -> 106 | 2 / 108 | 0.3333 -> 0.3519 | 7 / 93 |
  | `istio/istio` | 105 | 7 | 5 | 2 | 0 | 0 | 0 | 90 -> 90 | 0 / 76 | 0.3026 -> 0.3026 | 2 / 68 |
  | `kubernetes/kubernetes` | 120 | 13 | 11 | 3 | 0 | 0 | 0 | 47 -> 47 | 0 / 69 | 0.4928 -> 0.4928 | 0 / 44 |
  | `pallets/flask` | 97 | 0 | 0 | 0 | 0 | 0 | 0 | 45 -> 45 | 0 / 97 | 0.0206 -> 0.0206 | 0 / 2 |
  | `psf/requests` | 100 | 0 | 0 | 0 | 0 | 0 | 0 | 30 -> 30 | 0 / 89 | 0.0899 -> 0.0899 | 0 / 10 |
  | `pytorch/pytorch` | 120 | 3 | 3 | 0 | 0 | 0 | 0 | 32 -> 32 | 0 / 80 | 0.2875 -> 0.2875 | 0 / 0 |
  | `rust-lang/rust` | 120 | 15 | 4 | 13 | 0 | 0 | 0 | 96 -> 96 | 0 / 93 | 0.5161 -> 0.5161 | 0 / 74 |
  | `sharkdp/bat` | 100 | 0 | 0 | 0 | 0 | 0 | 0 | 11 -> 11 | 0 / 18 | 0.0556 -> 0.0556 | 0 / 10 |
  | `vercel/next.js` | 120 | 1 | 1 | 0 | 0 | 0 | 0 | 48 -> 48 | 0 / 78 | 0.1923 -> 0.1923 | 1 / 36 |
  | **Total** | **1,242** | **93** | **64** | **44** | **0** | **2** | **2** | **698 -> 700** | **5 / 895** | — | **31 / 507** |

- **Full-universe rebuild:** `COULD NOT VERIFY: full-universe rebuild (raw data/raw/ envelopes are not on this machine, and existing envelopes were collected before pageInfo was requested on reviews and comments)`.

## Reviewer

Reviewed by independent adversarial reviewer (#66 PR, 2026-10-23).

