# RFC-0021: commit-window-completeness

## Motivation

D-IN-4 (`docs/v2/02-RANKING-DESIGN.md` section 3, "Commits") collected at most the newest 1,000
commits of a project's trailing 365-day window (`COMMIT_CAP = 1000`, unhashed in
`porchlamp.collect.commits`), with no count of how many commits the 365-day window actually holds.
While `collect_commits_365d` recorded `truncated: true` when a record hit that cap,
`porchlamp.core.commit_domains.envelope_rows` explicitly ignored `truncated` and returned the rows
as if they were the whole 365-day window (issue #66 claims F2d and C2):

- **How many projects hit the 1,000-commit cap.** On the published 2026-10-21 snapshot (`4,501`
  eligible repositories with measured 3.2 Elephant Factor), `1,019` repositories (`22.6%`) have
  `n >= 900` non-bot org-attributed commits, `515` have `n >= 990`, and `230` sit at `n == 1,000`
  (`704` of the `1,019` are rated; on the 2026-10-17 snapshot the counts were `1,065` at `n >= 900`,
  `532` at `n >= 990`, and `239` at `n == 1,000`). Live GitHub default-branch commit counts over the
  trailing 365 days (queried 2026-10-22 across all `1,019` eligible `n >= 900` repositories, 51
  GraphQL points, 0 unresolved) show `995` of the `1,019` exceed 1,000 total commits: `min = 936`,
  `p10 = 1,143`, `p25 = 1,427`, `p50 = 2,122`, `p75 = 4,133`, `p90 = 8,444`, `p95 = 13,098`,
  `p99 = 34,153`, `max = 117,074` (`openclaw/openclaw`, followed by `ClickHouse/ClickHouse` at
  `114,060`, `Expensify/App` at `111,538`, `torvalds/linux` at `87,749`, and
  `NousResearch/hermes-agent` at `57,557`). Only `24` of the `1,019` fit within 1,000 commits;
  `547` exceed 2,000, `366` exceed 3,000, `205` exceed 5,000, `75` exceed 10,000, `26` exceed
  20,000, and `5` exceed 50,000.
- **Effect on 2.2 OpenSSF criticality (`commit_frequency_weekly`, `contributor_count`, `org_count`).**
  `porchlamp.collect.pike_signals.derive_pike_signals` computed `commit_frequency_weekly` as
  `stats["commits"] / 52`, where `stats["commits"]` is the count of non-bot rows in `commits_365d`.
  With at most 1,000 rows, `commit_frequency_weekly` was hard-capped at `1000 / 52 = 19.23`
  commits/week against a Pike saturation threshold `T_i = 1000` commits/week (`alpha_i = 1.0` out of
  `sum |alpha_i| = 10.5`), capping the sub-score `ln(1 + 19.23) / ln(1001) = 0.4353` and costing up
  to `(1 - 0.4353) / 10.5 * 100 = 5.38` points of 2.2 (`median = 1.00`, `p90 = 2.88`, `max = 5.38`
  across the `1,019` repositories if all commits are human). (Separately, the Pike formula's
  `updated_since_months` signal has `alpha_i = -1.0` and contributes `0` at best when
  `updated_since_months == 0`, so the formula's algebraic ceiling is `9.5 / 10.5 = 90.48`, not 100;
  on the 2026-10-21 snapshot across `5,441` eligible measured repositories, 2.2 has `p50 = 41.23`,
  `p90 = 53.64`, `p99 = 61.60`, `max = 68.71`.) Counting only the newest 1,000 commits also
  undercounts `contributor_count` (`alpha_i = 2.0`, `T_i = 5000`) and `org_count` (`alpha_i = 1.0`,
  `T_i = 10`).
- **Effect on 3.2 Elephant Factor, bus factor, 3.1 `Signed-off-by` share, and `top_email_domain_share`.**
  On a busy repository, the newest 1,000 commits cover only the most recent days or weeks (`23` days
  on `grafana/grafana`, `8` months on `cilium/cilium`, `7` months on `numpy/numpy`), measuring recent
  release-branch concentration rather than the 365-day window. By contrast, D-GATE-4 vitality is
  unaffected because it reads `commits_180d` (or needs only one human commit on page 1).
- **Incremental ledger deduplication key (C2(ii)).** `porchlamp.collect.ledger._commit_key` already
  preferred `c.get("sha")`, falling back to `(date, author_login, author_email, signed_off)`, but
  `porchlamp.collect.commits.shape_commit_rows` dropped `"sha"` from every shaped row, forcing every
  row onto the fallback tuple and collapsing same-second stacked or rebased commits by the same
  author. (`--incremental-ledgers` is experimental and off by default in `scripts/gather.py` and the
  phone runner, so this defect was latent in production.)

## Method

Measured before choosing:

- **Equivalence of GraphQL `history(since: $since) { totalCount }` and REST commit paging.** Across
  four repositories spanning 2,500 to 12,000 commits/year (`numpy/numpy`, `moby/moby`,
  `cilium/cilium`, `grafana/grafana`), GitHub's GraphQL `defaultBranchRef.target.history(since:
  $since).totalCount` (1 point per call) matches the exact commit count returned by paging REST
  `repos/{owner}/{name}/commits?per_page=100&since=$since`: `2,568`, `3,839`, `5,586`, and `11,767`.
  Deep REST pagination also works without degradation (`openclaw/openclaw` page `1,150` of `1,171`
  returned 100 commits in `1.14 s`; median REST page latency is `1.11 s`–`1.47 s`).
- **Whole window vs. newest 1,000 vs. evenly spaced page samples.** Comparing the full 365-day
  commit history against the newest 1,000 commits and `k`-page evenly spaced samples (`1 + floor(i *
  P / k)`):
  - `numpy/numpy` (`N = 2,568`, `P = 26` pages): whole year `EF = 3`, `bus = 3`, `contributors =
    232`, `orgs = 234`, `freq = 45.67/wk`; newest 1,000 gives `EF = 2`, `contributors = 102`,
    `freq = 18.13/wk` (costing `-1.23` points on frequency and `-1.83` on contributors in 2.2).
  - `moby/moby` (`N = 3,839`, `P = 39` pages): whole year `EF = 2`, `signoff = 0.6082`,
    `contributors = 119`, `orgs = 118`, `freq = 72.96/wk`; newest 1,000 gives `contributors = 38`,
    `freq = 19.10/wk` (`-1.80` frequency points, `-2.51` contributor points in 2.2).
  - `cilium/cilium` (`N = 5,586`, `P = 56` pages): whole year `EF = 2`, `bus = 13`, `signoff = 1.0`,
    `contributors = 347`, `orgs = 255`, `freq = 95.67/wk`; newest 1,000 gives `contributors = 135`,
    `orgs = 85`, `freq = 17.58/wk` (`-2.27` frequency points, `-2.10` contributor points in 2.2).
  - `grafana/grafana` (`N = 11,767`, `P = 118` pages): whole year `EF = 5`, `bus = 34`, `signoff =
    0.0108`, `contributors = 506`, `orgs = 419`, `freq = 211.62/wk`; newest 1,000 (spanning only 23
    days) drops `EF` from `5` to `3`, `contributors` to `162`, and `freq` to `18.52/wk` (`-3.29`
    frequency points, `-2.54` contributor points in 2.2). At `k = 50` evenly spaced pages (5,000
    commits), `EF` is `4`–`5` (scoring `100` under `min(100, 25 * EF)`), `bus` is `33`, `signoff` is
    `0.010`–`0.012`, `contributors` is `355`–`357`, and scaled `freq` is `210.36`–`212.80/wk` (within
    `0.6%` of the true `211.62/wk`).
- **Choosing `COMMIT_CAP = 5000`.** Across the `1,019` repositories with `n >= 900`, fetching every
  commit with no cap would take `44,721` pages (up from `10,190` at cap 1,000) and up to `1,171`
  pages for a single repository. Setting `COMMIT_CAP = 5000` collects the **entire** 365-day commit
  window for `814` of the `1,019` busy repositories (and all ~10,000 quieter repositories), leaving
  only `205` repositories above 5,000 commits to use the 50-page (`5,000`-commit) evenly spaced
  sample (`27,922` pages across the `1,019`, or `+17,732` REST calls per full cycle).

## Change

- **D-IN-4 (`commits_365d`) collector (`porchlamp.collect.commits`, `scripts/gather.py`).**
  - `shape_commit_rows` records `"sha": str(commit.get("sha") or "")` on every row, so
    `porchlamp.collect.ledger._commit_key` deduplicates by commit SHA and preserves same-second
    stacked or rebased commits.
  - When page 1 holds fewer than 100 commits, that page is the whole 365-day window (`total =
    len(rows)`, `sampled = False`, `truncated = False`) and no GraphQL count is needed.
  - When page 1 is full, `commit_total_fetcher` queries `COMMIT_TOTAL_QUERY` for GitHub's 365-day
    default-branch commit count `total`. If `total <= COMMIT_CAP` (`5,000`), the collector pages
    sequentially through the whole window (`sampled = False`). If `total > COMMIT_CAP`, it fetches
    the `k = 50` evenly spaced pages `sample_pages(total) = [1 + (i * P) // k for i in range(k)]`,
    deduplicating by `sha` in case a concurrent push shifts a boundary commit (`sampled = True`,
    `total = total`, `pages_total = P`, `sample_pages = pages`).
  - Incremental delta refresh (`_merged_with_delta`) runs only when existing rows are whole (not
    `truncated` and not `sampled`) and carry `sha`; if the delta exceeds `COMMIT_CAP` or existing
    rows are legacy SHA-less rows, collection falls back to a full fetch.
  - `can_derive_180d_from_365d` returns `False` when `sampled` is `True`, because sampled pages have
    gaps between them and are not the contiguous 180-day window.
- **Window classification (`porchlamp.core.commit_domains.commit_window`).**
  - Returns `CommitWindow(rows, total, sampled, cut)`:
    - A bare row list (v1 shape) is `cut = True` when `len(rows) >= LEGACY_COMMIT_CAP` (`1,000`) and
      whole (`total = len(rows)`) below it.
    - A window dict with `sampled: True` and an integer `total >= len(rows)` is `sampled = True,
      cut = False`; without a valid `total` it is `cut = True`.
    - A window dict with `truncated: True` (and not `sampled`) is `cut = True`.
    - Otherwise it is the whole window (`total = len(rows), sampled = False, cut = False`).
- **Readers (`scripts/derive.py`, `porchlamp.collect.pike_signals`, `porchlamp.collect.governance`,
  `porchlamp.derive.bundle`).**
  - On a `cut` record (stopped at the pre-RFC-0021 `LEGACY_COMMIT_CAP = 1000` without a sample or
    365-day total), every windowed commit measure refuses the record as
    `Unmeasured(reason="collector_failure", detail=COMMIT_WINDOW_CUT_DETAIL)` at both derive time
    and bundle-resolution time: 3.2 `elephant_factor` (and `bus_factor`), 2.2 `openssf_criticality`,
    3.1 `legal_ip` when its verdict rests on the `Signed-off-by` share (`rule` in
    `{"signoff_share", "no_evidence"}`; check- and foundation-based CLA/DCO verdicts stand), and
    `top_email_domain_share`. D-GATE-4 `vitality` and `last_non_bot_commit_at` continue to read page
    1's newest commits.
  - On a `sampled` record, 3.2 `elephant_factor`, `bus_factor`, 3.1 `signoff_share`, and
    `top_email_domain_share` read the evenly spaced sample unweighted, and 2.2
    `derive_pike_signals` scales `commit_frequency_weekly` by `window_total / len(c365)` (`human_rows
    * total / len(rows) / 52`).

| Constant | Before | After |
|---|---|---|
| `COMMIT_CAP` | *(absent; 1000 unhashed in `porchlamp.collect.commits`)* | 5000 |
| `LEGACY_COMMIT_CAP` | *(absent)* | 1000 |

`CONSTANTS_HASH` moves:
`93e44701a8a9862e242e0e1f93e7f082848fbc1f222d68bdcd221600192bac8c` ->
`b81c8bc48501e62976493eda447ce11944a2d7d53a3cd4a126f71cb766c7ff05`
(pinned in `tests/core/test_frozen.py`, with `_FROZEN_TABLE` rows for `COMMIT_CAP` and
`LEGACY_COMMIT_CAP`).

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump, matching the RFC-0001..0013
pre-release convention).

## Evidence

- **Fixture** (`tests/fixtures/snapshot_50/`): its bundles are pre-derived and none of its
  repositories has a `commits_365d` envelope cut at 1,000 rows (`rated=0, gated=18, unrated=32`
  unchanged). Scoring it produces byte-identical records apart from `ScoreRecord.constants_hash`;
  `records_hash` moves `177ceb43c7f173c6f3e2b6d00a9af6e8833cd6bcc7bc8d4595a41a1dfd04dcc0` ->
  `2bdc7e4c55957a97ffe90575c5365d5769cd0ba8eb618f2016e7b37cc444241c` (updated via
  `scripts/build_fixture_snapshot.py --rewrite-expected` and in `tests/snapshot/test_store_cli.py`).
- **Sensitivity** (D-AGG-3): `tests/audit/test_sensitivity_fixture.py` passes; no weight or curve
  constant changes.
- **Anchors** (D-VER-4): `tests/audit/test_anchors_fixture.py` passes on the fixture. On the
  existing pre-RFC-0021 disk envelopes (collected with the 1,000 cap and no `total`), the `995`
  eligible repositories whose `commits_365d` envelopes are `truncated: true` have 3.2 `elephant_factor`,
  2.2 `openssf_criticality`, and signoff-based 3.1 `legal_ip` withheld as `collector_failure` until
  `commits_365d` is re-collected with this collector (matching RFC-0013's release-order rule).
- **Rank movement**: 0 repos change rank on the fixture snapshot. On live 365-day histories for busy
  repositories (`numpy/numpy`, `moby/moby`, `cilium/cilium`, `grafana/grafana`), replacing the
  newest-1,000 slice with the whole window or 50-page sample restores `+3.06` to `+5.83` points on
  2.2 OpenSSF criticality (`+1.23` to `+3.29` from `commit_frequency_weekly` and `+1.83` to `+2.54`
  from `contributor_count`) and restores `grafana/grafana`'s 3.2 Elephant Factor from `3` (score 75)
  to `4`–`5` (score 100).

## Reviewer

Reviewed by independent adversarial reviewer (#66 PR, 2026-10-23).

