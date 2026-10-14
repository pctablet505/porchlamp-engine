# RFC-0008: black-hole-false-positive-rules

## Motivation

D-GATE-5 (`docs/v2/02-RANKING-DESIGN.md` section 2) fails a repository as a
black hole when more than half of its outside PRs receive no substantive maintainer
response on GitHub within 14 days. Under the original implementation, D-GATE-5 judged
the raw sample proportion $\hat{p} = \text{unanswered} / n > 0.50$ over any $n \ge 10$,
and counted every external PR opened in the 180-day window without accounting for draft
states, author self-closures, newly created accounts, or triage closures.

On small-to-moderate sample sizes ($n \in [10, 25]$), this produced false-positive
black-hole disqualifications for active repositories. Five false-positive reduction
rules are established here to eliminate these distortions:

1. **Rule 1 (Draft PR exclusion and review clock start)**:
   Contributors frequently open draft pull requests for work-in-progress experiments or
   preliminary CI checks. Maintainers are not expected to review draft PRs until they
   are marked ready for review. Draft PRs that are never marked ready are excluded
   from the D-GATE-5 population (`excluded_draft`). When a PR is marked ready for review
   (`readyForReviewAt`), the 14-day response clock begins at
   $\max(\text{createdAt}, \text{readyForReviewAt})$ rather than `createdAt`. The 14-day
   observation window requirement is judged against this adjusted clock start.

2. **Rule 2 (Author self-closed PR exclusion)**:
   Contributors sometimes open PRs by mistake, duplicate an existing PR, or resolve
   their own problem and close the PR without maintainer involvement. If maintainers
   never responded and the author closed the PR themselves, the lack of maintainer
   response is benign. An author self-closed PR (`closed_by_author = True`) that was
   not answered within 14 days and has no substantive maintainer response at or before
   `closedAt` is excluded from the population (`excluded_self_closed`).

3. **Rule 3 (Account-age hygiene)**:
   In alignment with D-IN-3 contributor hygiene (`MIN_ACCOUNT_AGE_DAYS = 90`), PRs
   authored by accounts created less than 90 days prior to PR creation are excluded
   from the population (`excluded_new_account`), mitigating Sybil and spam noise.

4. **Rule 4 (Maintainer close within 14 days counts as response)**:
   Maintainers frequently triage invalid, out-of-scope, or duplicate PRs by closing
   them promptly. A PR closed by a maintainer (`closed_by_maintainer_at`) within 14 days
   of clock start is counted as answered within the deadline (alongside merges and
   substantive reviews/comments).

5. **Statistical confidence via Wilson lower confidence bound**:
   A raw proportion of $6 / 10 = 0.60$ carries substantial sampling uncertainty.
   To prevent false-positive gating due to small-sample noise, D-GATE-5 evaluates
   the one-sided 95% Wilson lower confidence bound ($z_{0.95} = 1.6448536269514722$,
   `GATE5_WILSON_Z`):
   $$L = \frac{\hat{p} + \frac{z^2}{2n} - z \sqrt{\frac{\hat{p}(1 - \hat{p}) + \frac{z^2}{4n}}{n}}}{1 + \frac{z^2}{n}}$$
   A repository fails D-GATE-5 as a black hole only when $n \ge 10$ and $L > 0.50$.
   For $n = 10$, $6/10$ ($L = 0.352$) passes, while $8/10$ ($L = 0.541$) fails.

## Change

| Constant | Before | After |
|---|---|---|
| `GATE5_WILSON_Z` | *(absent)* | `1.6448536269514722` |

`CONSTANTS_HASH` moves:
`aca719a922264ba5edb6adcdab6c0c08eaff7a315d0e5818a567509e5b0d3ba9` ->
`6e4f3e8e1d0c0fcd829d9c8c386a65179eadb5f936595f96e9eba98712d55b94`
(pinned in `tests/core/test_frozen.py`, with one `_FROZEN_TABLE` row for `GATE5_WILSON_Z`).

Consumers: `porchlamp.core.gates` (`wilson_lower_bound_from_share`, `wilson_lower_bound`,
`is_black_hole_counts`, `is_black_hole_bundle`, `evaluate_gates`, `evaluate_gate5_window`),
`porchlamp.collect.github_graphql` (`PR_WINDOW_QUERY`, `derive_pr_window`,
`ghost_rate_prs_from_window`), `scripts/derive.py`, and `porchlamp.core.methodology`.

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump, matching the `RFC-0001..0007`
pre-release convention).

## Evidence

- **Sensitivity** (D-AGG-3): unchanged on `tests/fixtures/snapshot_50/` (0 of 50 repos rated
  before and after). The fixture's source corpus (`cvi-scratch/sample-200`) holds no
  `pr_window` envelopes, so ghost rate is unmeasured for all 50 and the rule cannot move any of them.
- **Anchors** (D-VER-4): `torvalds/linux` remains `routed: off_platform`, `rated: false`; all
  anchor checks pass.
- **Rank movement**: none on `tests/fixtures/snapshot_50/` (`counts`, `rank_order`, and every
  record unchanged; `records_hash` updates only for the new `CONSTANTS_HASH`, rewritten by
  `scripts/build_fixture_snapshot.py --rewrite-expected`).
- **Mathematical bounds**:
  - $n = 10, \text{unanswered} = 6 \implies \hat{p} = 0.60, L = 0.352 \le 0.50$ (protected from false positive).
  - $n = 10, \text{unanswered} = 8 \implies \hat{p} = 0.80, L = 0.541 > 0.50$ (disqualified as black hole).
  - $n = 20, \text{unanswered} = 14 \implies \hat{p} = 0.70, L = 0.516 > 0.50$ (disqualified as black hole).

## Reviewer

Reviewed on 2026-10-14 per the frozen Porchlamp Scaling Plan (Packet 3: `RFC-0008`
Black-Hole False-Positive Reduction Rules in `D-GATE-5`).
Verification confirmed clean ruff checks, full test coverage across all five false-positive rules,
Wilson lower confidence bound mathematical properties, and fixture snapshot reproducibility.
