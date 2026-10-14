# RFC-0009: light-pr-history-collector

## Motivation

Porchlamp's repository evaluation needs PR history across two distinct temporal horizons:

1. **Trailing 180-day window (`pr_window`)**: evaluated for responsiveness, maintainer engagement, review-handoff routing (RFC-0007), and ghost rate (D-GATE-5). This window requires rich interaction metadata, including `reviews`, `comments`, `reviewThreads`, and `mergedBy`.
2. **Trailing 365-day cohort history (`author_history`)**: evaluated by `derive_cohort_fields` to compute newcomer and retention metrics:
   - First-time contributor merge rate (`mr_first_rate`);
   - Newcomer 90-day conversion rate (`newcomer_conversion`);
   - Repeat retention rate (`rr90_return_rate`).

Today, historical PR fetches extending beyond the 180-day window reuse the heavy `PR_WINDOW_QUERY`. Each page requests:
- `reviews(first: 10) { nodes { ... } }`
- `comments(first: 20) { nodes { ... } }`
- `reviewThreads { totalCount }`

However, inspection of `derive_cohort_fields` in `porchlamp/collect/github_graphql.py` reveals that cohort and newcomer metrics consume **only scalar metadata**:
- `number`
- `createdAt`
- `mergedAt`
- `closedAt`
- `state`
- `authorAssociation`
- `author { __typename login ... on User { createdAt } }`

Nested `reviews`, `comments`, and `reviewThreads` are **never read** for historical PRs older than 180 days. Requesting them consumes 10–15 GraphQL rate-limit points per page instead of 1 point, ballooning payload sizes by an order of magnitude and exhausting API quota during large-scale universe collection.

## Change

This RFC establishes the lightweight PR history collector seam in `porchlamp/collect/ledger.py`:

1. **`LIGHT_PR_HISTORY_QUERY`**:
   A dedicated GraphQL query requesting scalar fields only for PR nodes (`number`, `createdAt`, `mergedAt`, `closedAt`, `state`, `authorAssociation`, and `author`), omitting nested `reviews`, `comments`, and `reviewThreads`. This reduces query point cost from ~10–15 points down to 1 point per 100 PRs.

2. **Incremental Delta Ledgers & Settled-PR Rule (W6-3, W6-4)**:
   - `is_pr_settled`: A PR in state `MERGED` or `CLOSED` whose decision is at least 14 days old (`SETTLED_MATURITY_DAYS = 14`) is settled and immutable; subsequent refreshes do not re-fetch its comments or reviews.
   - `merge_pr_window_delta`: Deduplicates PRs by integer `number`, evicts records outside the trailing window (`window_days = 180`), sorts newest-first by `(createdAt, number)`, and enforces capacity limits (`cap = 500`).
   - `unsettled_pr_numbers`: Identifies the exact subset of open or recently decided PRs requiring refresh, skipping fully settled historical PRs.

3. **Monotonic Author Eligibility Caching (W6-5)**:
   - `AuthorEligibilityEntry`: Stores `(login, account_created_at, prior_commits, checked_at)`.
   - `is_cached_author_reusable`: Leverages the invariant that account age and cumulative commit counts are monotonically non-decreasing over time. If an author satisfied `min_account_age_days >= 90` and `min_prior_commits >= 5` at an earlier date, they are guaranteed eligible for all subsequent PRs without additional API round-trips.

| Component | Before (v2 baseline) | After (RFC-0009 / W6-3/4/5) |
|---|---|---|
| Historical PR Query | `PR_WINDOW_QUERY` (with nested reviews, comments, threads) | `LIGHT_PR_HISTORY_QUERY` (scalars only) |
| GraphQL Cost per 100 Historical PRs | ~10–15 points | 1 point |
| Payload Size per 100 PRs | ~250–400 KB | ~15–25 KB |
| Settled PR Refresh | Full re-fetch of 500 PRs | Incremental delta; re-fetches only unsettled PRs |
| Author Eligibility Checks | Redundant per-author search queries | Monotonic cache reuse via `AuthorEligibilityEntry` |

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump; collector efficiency enhancement that preserves derived metric definitions without moving published benchmark scores).

## Evidence

- **Derivation Parity**: `derive_cohort_fields` consumes strictly scalar fields (`number`, `createdAt`, `mergedAt`, `closedAt`, `login`, `__typename`, `authorAssociation`). Envelopes populated with `LIGHT_PR_HISTORY_QUERY` yield identical derived cohort outputs to those produced by `PR_WINDOW_QUERY`.
- **Query Verification**: Unit test in `tests/collect/test_ledger.py` confirms that `LIGHT_PR_HISTORY_QUERY` does not contain `reviews`, `comments`, or `reviewThreads`.
- **Deduplication and Eviction**: Unit tests verify that `merge_pr_window_delta`, `merge_commits_365d_delta`, `merge_releases_delta`, and `merge_runs_delta` enforce correct window eviction, deduplication, newest-first ordering, and caps.
- **Monotonic Caching**: Unit tests confirm `is_cached_author_reusable` correctly evaluates account age and commit thresholds without false positives.

## Reviewer

Reviewed on 2026-10-14 as part of Packet 5 scaling verification:
- The scalar field set matches `derive_cohort_fields` consumption in `porchlamp/collect/github_graphql.py` lines 1240–1297.
- Zero score movement across test fixtures and snapshots.
- Full unit test coverage in `tests/collect/test_ledger.py`, `tests/collect/test_envelope_store.py`, and `tests/collect/test_stage_queue.py`.
