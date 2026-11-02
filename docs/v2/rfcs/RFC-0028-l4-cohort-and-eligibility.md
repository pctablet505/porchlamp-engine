# RFC-0028: l4-cohort-and-eligibility

## Motivation

During L4 (`author_history`) collection on `porchlamp-phone` (#89), three bottlenecks and methodological imperfections were identified across the `D-IN-3` eligibility and `D-IN-4` cohort-sampling steps:

1. **Newcomer Sample Attrition (`Q9`)**: `_select_newcomer_sample` previously drew at most 20 candidates from `[anchor - 365 d, anchor - 180 d]` before checking whether their account was `< 90` days old (`MIN_ACCOUNT_AGE_DAYS`), a non-`User` actor, or a repeat contributor whose per-author search later revealed a PR opened strictly before `anchor - 365 d`. On active repositories where 40-65% of authors in the `[365 d, 180 d]` band had prior PRs, the confirmed newcomer cohort (`5.2 newcomer_conversion` and `5.3 rr90_return_rate`) frequently shrank to 7-12 authors, dropping below `PARAM_MIN_N = 10` (`insufficient_n`).
2. **Mega-Repository `DR_90` Author Explosion (`Q11`)**: While `1.3 MR_first` capped per-author searches at `AUTHOR_SEARCH_CAP = 50`, `1.2 DR_90` required `D-IN-3` eligibility lookups for every distinct outside author in the `[anchor - 180 d, anchor - 90 d)` band. On mega-repositories (`500-2,500+` distinct outside authors in 90 days), querying every band author in 8-login batches dominated L4 wall-clock time without adding statistical precision beyond a 300-author sample (`95%` margin of error `<= +-2.6%`).
3. **Unbounded 365-Day `contributionCalendar` Eligibility Queries (`Q2b`)**: `_eligibility_batch_query` queried `contributionsCollection.contributionCalendar.totalContributions` for every author, which forced GitHub's GraphQL backend to compute a 365-day calendar grid (limiting batch width to `ELIGIBILITY_BATCH_SIZE = 8` to avoid 10-second `502` timeouts) and evaluated contributions over the trailing year prior to `now` rather than prior to the author's PR creation timestamp (`D-IN-3`).

## Method

1. **Pre-Filtered Newcomer Candidate Pool & Reserve Promotion (`Q9`)**:
   - `_newcomer_band_candidates` excludes non-`User` actors and accounts younger than `MIN_ACCOUNT_AGE_DAYS = 90` at their oldest in-window PR at `0` GraphQL cost using `author.__typename` and `author.createdAt` already carried on the window PR nodes.
   - `_select_newcomer_candidates` draws a primary seeded sample of `NEWCOMER_SAMPLE_SIZE = 20` and a deterministic reserve sample of up to `NEWCOMER_CANDIDATE_CAP - 20 = 15` (`NEWCOMER_CANDIDATE_CAP = 35`).
   - `collect_author_history` keeps `MR_first`'s 30-slot decision-cohort search budget (`AUTHOR_SEARCH_CAP - 20 = 30`) intact; only when one or more primary newcomer candidates is disqualified by a prior PR before `anchor - 365 d` are the reserve candidates searched and promoted into `sampled_authors` up to `NEWCOMER_SAMPLE_SIZE = 20`.
2. **Deterministic `DR_90` Band Author Cap (`Q11`)**:
   - When a repository has more than `DR90_AUTHOR_CAP_TRIGGER = 500` distinct outside authors in `[anchor - 180 d, anchor - 90 d)`, `porchlamp.core.gates.dr90_band_logins` and `porchlamp.derive.bundle._dr90_decision_cohort` deterministically sample `DR90_AUTHOR_SAMPLE_CAP = 300` distinct authors using `PR_SAMPLE_SEED`. Repositories with `<= 500` distinct `DR_90` band authors are unchanged.
3. **Two-Tier PR-Time-Bounded `D-IN-3` Eligibility (`Q2b`)**:
   - Tier 1 (`_eligibility_tier1_batch_query`, `ELIGIBILITY_TIER1_BATCH_SIZE = 50`) queries `createdAt`, `pullRequests(first: 5, orderBy: CREATED_AT ASC)`, and `issues(first: 5, orderBy: CREATED_AT ASC)` for 50 logins per batch, immediately settling accounts that have `>= 5` public PR/issue events on or before their PR timestamp (or are `< 90` days old at PR creation).
   - Tier 2 (`_eligibility_tier2_batch_query`, width `ELIGIBILITY_BATCH_SIZE = 8`) escalates only ambiguous accounts (`< 5` prior PR/issue events and `>= 90` days old), querying scalar `contributionsCollection(from:, to:)` counts (`totalCommitContributions`, `totalIssueContributions`, `totalPullRequestContributions`, `totalPullRequestReviewContributions`, `restrictedContributionsCount`) bounded to the author's PR creation timestamp without requesting `contributionCalendar`.

## Change

- Added five hashed constants to `porchlamp.core.constants`:
  - `NEWCOMER_SAMPLE_SIZE = 20` (relocated from `porchlamp.collect.github_graphql`)
  - `NEWCOMER_CANDIDATE_CAP = 35`
  - `DR90_AUTHOR_CAP_TRIGGER = 500`
  - `DR90_AUTHOR_SAMPLE_CAP = 300`
  - `ELIGIBILITY_TIER1_BATCH_SIZE = 50`
- Updated `porchlamp.core.gates.dr90_band_logins` and `porchlamp.derive.bundle._dr90_decision_cohort` to apply `DR90_AUTHOR_CAP_TRIGGER` / `DR90_AUTHOR_SAMPLE_CAP`.
- Updated `porchlamp.collect.github_graphql` with `_newcomer_band_candidates`, `_select_newcomer_candidates`, `_eligibility_tier1_batch_query`, `_eligibility_tier2_batch_query`, `_resolve_tier1_eligibility_node`, `_resolve_tier2_eligibility_node`, and `fetch_two_tier_eligibility_batch`.

| Constant | Before | After |
|---|---|---|
| `NEWCOMER_SAMPLE_SIZE` | 20 (unhashed) | 20 (hashed) |
| `NEWCOMER_CANDIDATE_CAP` | - | 35 |
| `DR90_AUTHOR_CAP_TRIGGER` | - | 500 |
| `DR90_AUTHOR_SAMPLE_CAP` | - | 300 |
| `ELIGIBILITY_TIER1_BATCH_SIZE` | - | 50 |

`CONSTANTS_HASH` moves:
`de2c71c1d7bad01ff749cd81560974ce5199352075feeeda6d805a8d52dd2332` ->
`53d310de9fae44586159118e9d2251f7aeb59de551187c7a79825a498aa03c9e`
(pinned in `tests/core/test_frozen.py`, with five new `_FROZEN_TABLE` rows).

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump, matching the RFC-0001..0027 pre-release convention).

## Evidence

- **Newcomer Cohort Retention (`Q9`)**: Pre-filtering `< 90d` and non-`User` actors at 0 GraphQL cost and promoting confirmed newcomers from up to 35 candidates (`20` primary + `15` reserve) recovers full `20`-author confirmed newcomer cohorts on repositories with up to `43%` repeat-contributor attrition without reducing `MR_first`'s 30-slot decision-cohort search budget.
- **Mega-Repo `DR_90` Cost Bound (`Q11`)**: Capping `> 500` distinct `DR_90` band authors at a deterministic sample of `300` bounds per-repo `DR_90` eligibility lookups on the tail (`0.8%` of ranked repos) while maintaining a 95% margin of error `<= +-2.6%` (`sqrt(0.25 * (1/300) * (1 - 300/600)) = 2.04%` at `N = 600`).
- **Fixture** (`tests/fixtures/snapshot_50/`), regenerated with `scripts/build_fixture_snapshot.py --rewrite-expected`: `rated=0, gated=18, unrated=32` is unchanged and no repository changes outcome; only `ScoreRecord.constants_hash` moves (`records_hash`: `6fa702d8e7a1796fc586050fa603420bcb27f9b45e4a405cff8981f90624ae50` -> `15a6f6dd374a650696602fc59f44da3307315498a429edef7900abfab6a4d29f`).

## Reviewer

Approved by project owner in `#89` (`Q2b`, `Q9`, `Q11`).
