# RFC-0015: external-pr-landing

## Motivation

Several major open-source projects land pull requests by committing the change themselves and closing
the pull request without clicking GitHub's merge button, so GitHub records the pull request as
`CLOSED` with `mergedAt = null` (`docs/v2/02-RANKING-DESIGN.md` section 4, issue #60, audit claim F4):

- `pytorch/pytorch`'s merge bot (`pytorchmergebot`) and Meta's Phabricator export bot push a commit
  that closes the pull request and apply the label `Merged`.
- `openjdk/jdk`'s Skara bot (`openjdk[bot]`) pushes the commit, closes the pull request and applies
  the label `integrated`.
- `freebsd/freebsd-src`, `JetBrains/intellij-community`, `curl/curl`,
  `notepad-plus-plus/notepad-plus-plus`, `official-stockfish/Stockfish` and `odoo/odoo` commit the
  patch to the default branch with a closing reference (`Closes #N`) or apply a `merged`/`Merged`
  label when closing.

Every merge-based measure that read `mergedAt` alone counted every one of those landed pull requests
as a rejection:

- On the 2026-10-21 (and 2026-10-17) snapshots:
  - `notepad-plus-plus/notepad-plus-plus` (rated, tier C, rank 2,163 on 2026-10-21 / 1,726 on
    2026-10-17, coverage 0.83) scored `dr90_decision_rate = 0.99` (`n = 97`) alongside
    `mr_first_rate = 0.0` (`n = 79`), `ttm_p90_hours = Unmeasured(insufficient_n, n = 0)` and
    `persona_gates["first_timer"] = "high_barrier_to_entry"`.
  - `freebsd/freebsd-src` (rated, tier C, rank 3,108 on 2026-10-21 / 2,554 on 2026-10-17, coverage
    0.925) scored `dr90_decision_rate = 0.634` (`n = 153`) alongside `mr_first_rate = 0.0`
    (`n = 78`) and `persona_gates["first_timer"] = "high_barrier_to_entry"`.
  - `JetBrains/intellij-community` (rated, tier D, rank 3,668 on 2026-10-21 / 3,129 on 2026-10-17,
    coverage 0.8825) scored `dr90_decision_rate = 0.739` (`n = 46`) alongside
    `mr_first_rate = 0.0` (`n = 48`), `ttm_p90_hours = Unmeasured(insufficient_n, n = 0)`,
    `newcomer_conversion = 0.0` (`n = 9`) and `rr90_return_rate = Unmeasured(insufficient_n, n = 0)`.
  - `official-stockfish/Stockfish` (rated, tier B, rank 1,028 on 2026-10-21 / 1,284 on 2026-10-17,
    coverage 0.78) scored `dr90_decision_rate = 0.984` (`n = 124`) alongside
    `ttm_p90_hours = Unmeasured(insufficient_n, n = 0)`.
  - `openjdk/jdk` (rated, tier D, rank 3,356 on 2026-10-21, coverage 0.695) scored
    `ttm_p90_hours = Unmeasured(insufficient_n, n = 0)`.

The decision measures (1.2 `DR_90`, 1.4 `GR_14` and D-GATE-5) are unaffected because a close was
already counted as a decision.

## Method

Measured on 2026-10-22 across 2,700 live closed-unmerged pull requests (100 newest `CLOSED` pull
requests with `mergedAt == null` per repository across 27 repositories):

- **Signal coverage on landing repositories (excluding author self-closes):**

  | Repository | Non-self-closed `n` | Both (`C&L`) | Commit closer only (`C-only`) | Label only (`L-only`) | Neither | Landed (`is_landed`) |
  |---|---|---|---|---|---|---|
  | `pytorch/pytorch` | 64 | 39 (`Merged`) | 0 | 0 | 25 | 39 (61%) |
  | `facebook/buck2` | 48 | 39 (`Merged`) | 0 | 0 | 9 | 39 (81%) |
  | `facebook/rocksdb` | 99 | 86 (`Merged`) | 0 | 0 | 13 | 86 (87%) |
  | `facebookresearch/faiss` | 99 | 78 (`Merged`) | 0 | 0 | 21 | 78 (79%) |
  | `react/react-native` | 81 | 72 (`Merged`) | 0 | 0 | 9 | 72 (89%) |
  | `openjdk/jdk` | 41 | 0 | 0 | 37 (`integrated`) | 4 | 37 (90%) |
  | `freebsd/freebsd-src` | 91 | 2 (`merged`) | 3 | 19 (`merged`) | 67 | 24 (26%) |
  | `JetBrains/intellij-community` | 100 | 1 (`Merged`) | 69 | 6 (`Merged`) | 24 | 76 (76%) |
  | `curl/curl` | 40 | 0 | 34 | 0 | 6 | 34 (85%; 91/100 all authors) |
  | `notepad-plus-plus/notepad-plus-plus` | 95 | 0 | 69 | 0 | 26 | 69 (73%) |
  | `official-stockfish/Stockfish` | 76 | 0 | 51 | 0 | 25 | 51 (67%) |
  | `odoo/odoo` | 99 | 0 | 16 | 0 | 83 | 16 (16%) |

- **Default-branch ancestry (`GET /repos/{owner}/{name}/compare/{sha}...{default_branch}`):**
  55 sampled closing commits (5 per project across all 11 projects with commit closers) were
  checked against each repository's default branch. All 55 / 55 (100%) returned `status` in
  `{"ahead", "identical"}` with `behind_by == 0`: GitHub records a `ClosedEvent` with a `Commit`
  closer only when the closing commit reaches the default branch, which requires write access.
- **False-positive check on ordinary GitHub-merge repositories (1,200 closed-unmerged PRs across 12
  repositories: `kubernetes/kubernetes`, `vercel/next.js`, `apache/airflow`, `systemd/systemd`,
  `numpy/numpy`, `home-assistant/core`, `psf/requests`, `ansible/ansible`, `nodejs/node`,
  `openssl/openssl`, `django/django`, `pallets/flask`):**
  **0 / 1,200 (0.0%)** carry a `Commit` closer or a label in `LANDING_LABELS` (`{"integrated",
  "merged"}`). None of the 2,700 sampled PRs truncated at `labels(first: 10)` (`trunc10 == 0`), and
  no `ClosedEvent.closer` had `__typename == "PullRequest"`.
- **Why comment-body matching is rejected:**
  - `AUTHOR_WINDOW_QUERY` and `LIGHT_PR_HISTORY_QUERY` record no comments.
  - Loose comment patterns (`"applied"`, `"merged in/into/to/as"`, `"landed in"`, `"cherry-pick"`)
    matched 31 / 100 closed-unmerged PRs in `nodejs/node`, 15 / 100 in `openssl/openssl`, 3 / 100
    in `systemd/systemd`, and 6 / 8 sampled occurrences of `"applied"` in ordinary repositories where
    the word was used in ordinary review prose rather than to announce a landing.
  - Note on F4's cited repositories: `pallets/flask` (0 / 100 commit closers or landing labels; uses
    standard GitHub merges) and `tmux/tmux` (0 / 99 commit closers or landing labels; 73 / 99 carry
    maintainer comment replies such as `"Applied, thanks"` without closing-commit references or
    labels) are not modified by `is_landed`.
- **Query cost probe (`pytorch/pytorch`, 2026-10-22):**
  - `AUTHOR_WINDOW_QUERY` (`first: 100`): 1 point -> 2 points per page with `PR_LANDING_FIELDS`.
  - `LIGHT_PR_HISTORY_QUERY` (`first: 100`): 1 point -> 2 points per page with `PR_LANDING_FIELDS`.
  - `AuthorPrsSearchBatch` (25 aliases, `first: 30`): 1 point as-is, but adding `PR_LANDING_FIELDS`
    across 25 search aliases fails with HTTP `502 Bad Gateway` at GitHub's GraphQL gateway. The
    per-author search query is therefore kept unchanged; `_author_pr_history` preserves the 365-day
    window node's `labels` and `timelineItems` when deduplicating against search entries (`{**seen[number], **pr}`).

## Change

- **Core definition (`porchlamp/core/landing.py`):**
  - `is_landed(pr)` is true when a pull-request node has no `mergedAt`, a parseable `closedAt`, and
    either its latest `ClosedEvent` in `timelineItems` has `closer.__typename == "Commit"` or one of
    its `labels` (whole name, lower-cased) is in `constants.LANDING_LABELS`.
  - `merged_or_landed_at(pr)` returns `mergedAt` when it parses, else `closedAt` when `is_landed(pr)`
    is true, else `None`.
  - `landing_recorded(pr)` reports whether a pull-request node carries both `labels` and
    `timelineItems` mappings (false on envelopes collected before these fields were added).
- **Collectors and derivers:**
  - `PR_WINDOW_QUERY` already selects `PR_LANDING_FIELDS` (since #63); `AUTHOR_WINDOW_QUERY`
    (`porchlamp.collect.github_graphql`) and `LIGHT_PR_HISTORY_QUERY` (`porchlamp.collect.ledger`)
    now carry `PR_LANDING_FIELDS`, and `fetch_light_pr_history` preserves `labels` and
    `timelineItems` on each shaped entry when present.
  - `_author_pr_history`, `_no_prior_merged`, `_newcomer_cohort_authors` and `derive_cohort_fields`
    in `porchlamp.collect.github_graphql` read `landing.merged_or_landed_at` for 1.3 `MR_first` (and
    the `first_timer` persona gate that reads `mr_first_rate`), 5.2 `newcomer_conversion` and 5.3
    `RR_90`, and emit `author_history_landing_recorded`, `mr_first_landed_pr_count`,
    `newcomer_conversion_landed_author_count` and `rr90_return_landed_author_count`.
  - `derive_pr_window_landing_fields` (`porchlamp.collect.github_graphql`) and `scripts/derive.py`
    record `pr_window_landing_recorded` and `pr_window_landed_external_count`.
  - `code_review_rate` (`porchlamp.collect.pike_signals`) and `_resolve_pipeline_defenses`
    (`porchlamp.derive.bundle`) count landed PRs via `landing.merged_or_landed_at` in the 4.1
    `proxy:review_ci` denominator and `min_n` sample count.
  - `_merged_external_latency_hours` and `_resolve_ttm_p90_hours` (`porchlamp.derive.bundle`) include
    landed external PRs at their `closedAt` latency for 1.5 `TTM_p90`, and `_landing_note` appends
    landing provenance to `insufficient_n` details for 1.3, 1.5, 5.2 and 5.3.

| Constant | Before | After |
|---|---|---|
| `LANDING_LABELS` | *(absent)* | `frozenset({"integrated", "merged"})` |

`CONSTANTS_HASH` moves:
`4116987bb0ea32861181dff41647832e4fbc9fae75dc59fbd7436ac534e96d6b` ->
`5a406aecf99b4939c18d7453b7b73bd3f9af0cd460140489568f0b23f91d4365`
(pinned in `tests/core/test_frozen.py` with a `_FROZEN_TABLE` row for `LANDING_LABELS`).

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump, matching the pre-release convention).

## Evidence

- **Fixture** (`tests/fixtures/snapshot_50/`): holds pre-derived bundles with no `pr_window` or
  `author_history` envelopes, so no score or rank moves (`rated = 0`, `gated = 18`, `unrated = 32`).
  Only `records_hash` moves (`0337d4f09687d1df7b3d30967cf31b0984828f330e160c9047eb016d01bdacba` ->
  `e711e47a4a702c46df02ea13ea8ec3b5fbded0bc08e7bd1aa8f5fb5356a0ef28` in `expected.json` and
  `tests/snapshot/test_store_cli.py`), with `manifest.json` and `populations.json` restamped via
  `scripts/build_fixture_snapshot.py --rewrite-expected`.
- **Sensitivity** (D-AGG-3): no weight or curve constant changes; `tests/audit/test_sensitivity_fixture.py`
  passes unchanged.
- **Anchors** (D-VER-4): `tests/audit/test_anchors_fixture.py` passes unchanged (`torvalds/linux`
  routed off-platform, `gcc-mirror/gcc` unrated, all present anchor relations hold).
- **Recorded data vs. next collection cycle**: existing `author_history` envelopes on disk were
  fetched before `AUTHOR_WINDOW_QUERY` and `LIGHT_PR_HISTORY_QUERY` included `PR_LANDING_FIELDS`
  (`landing_recorded(pr) == False`), so `is_landed(pr)` evaluates `False` on legacy author-history
  nodes and `_landing_note` explicitly states `"the record shows no landing outside GitHub's merge
  (fetched before RFC-0015's fields)"` when a merge-based rate is `insufficient_n`. Full cohort
  recovery for 1.3, 5.2 and 5.3 takes effect as `author_history` envelopes are refreshed with the
  updated query; `pr_window` envelopes collected since #63 already carry `PR_LANDING_FIELDS` and
  immediately credit landed external PRs in 1.5 `TTM_p90` and the 4.1 review proxy upon re-derivation.
