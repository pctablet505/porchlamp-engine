# RFC-0028: l4-cohort-and-eligibility

## Motivation

The contributor-history step (L4, `author_history`) runs on the phone one request at a time, and is the slowest step of a cycle (#89). Two cohort rules of D-IN-4 cost it time or lost newcomers:

1. **1.2 DR_90 looked up D-IN-3 eligibility for every outside author of its cohort.** On the 2026-10-31 data that is 73,357 lookups for the next cycle, in 9,169 calls at the current batch width, after the cache. But the score is `min(100, 125 r)`, so above a rate of 0.80 it is 100 whatever the exact rate. On a project far above that line, reading every author buys no score.
2. **5.2 and 5.3 lost part of their sample.** 20 newcomer candidates were drawn first and checked afterwards, so accounts that could never count, and authors whose search showed an older PR, used up slots. On the 31 fresh histories large enough to have a reserve, 5.2 read 15.3 authors on average and 5.3 reached its floor of 5 in 55% of draws.

## Method

### 1.2: sample only where a sample cannot change the score

From the stored `pr_window` alone, so that collect and derive answer alike (`porchlamp.core.gates`): take the cohort's outside authors, `N` of them, each with `m` PRs and `d` of them decided in 90 days; `r = sum(d) / sum(m)`. DR_90 counts PRs and a sample draws authors, so the error of a sample of `S` authors is the ratio estimator's, over the authors' own counts:

    V = sum((d - r m)^2) / ((N - 1) (sum(m) / N)^2)
    error = Z sqrt((1 - S / N) V / S)

A project samples when `N > DR90_SAMPLE_MIN_AUTHORS` and `r - error - DR90_SAMPLE_GATE_MARGIN >= 100 / RATE_SLOPE`. It then reads a `PR_SAMPLE_SEED` draw of `S = DR90_SAMPLE_AUTHORS` over the sorted logins. Every other project is counted in full. The margin is room for the D-IN-3 gate, which the rule cannot see: `r` is the rate before eligibility.

- **A record says what it looked up.** An `author_history` record that narrowed its lookups stores the digest of that band (`dr90_band_sha256`). 1.2 reads the band when the `pr_window` gives the same one, and refuses any other as `collector_failure`: it would be reading a fraction of the sample and report no failure.
- **A record that looked up everyone reads everyone.** A record with no digest, which is every record collected before this RFC, reads the full count. So no existing value moves.
- **The page says so.** A sampled value carries `method = sample:<S>_of_<N>_authors`, and the project page prints it as a plain sentence.

### 5.2 and 5.3: fill the sample

- Accounts under `MIN_ACCOUNT_AGE_DAYS` old at their oldest in-window PR, and actors that are not users, leave the pool before the draw. Both facts are on the window's PR nodes, so this costs no call. It is the same instant D-IN-3 applies to a newcomer's first PR, and `03-ANTI-MANIPULATION.md` section 4 already says it: "cohort membership requires account creation >= 90 d before the first PR".
- Besides the 20 primary candidates, up to `NEWCOMER_CANDIDATE_CAP - 20 = 15` reserve candidates are drawn, in draw order. When a search shows a primary candidate had a PR before the band, the reserve is searched and confirmed newcomers replace it, first drawn first, up to 20. A search that failed or found nothing proves nothing: that author stays, as `history_unmeasured`, and the 20% gate counts them.
- 1.3 keeps its 30 slots. The reserve authors searched that also sit in its cohort are read by 1.3 too (on average 5.3 per draw where the reserve runs).

## Change

| Constant | Before | After |
|---|---|---|
| `DR90_SAMPLE_MIN_AUTHORS` | - | 200 |
| `DR90_SAMPLE_AUTHORS` | - | 150 |
| `DR90_SAMPLE_Z` | - | 2.576 |
| `DR90_SAMPLE_GATE_MARGIN` | - | 0.05 |
| `SAMPLE_METHOD_PREFIX` | - | `"sample:"` |
| `NEWCOMER_SAMPLE_SIZE` | 20 (unhashed, `porchlamp.collect.github_graphql`) | 20 (hashed) |
| `NEWCOMER_CANDIDATE_CAP` | - | 35 |

`Measured.method` also accepts `sample:<name>`. The batch width of the eligibility query is not touched.

`CONSTANTS_HASH` moves:
`de2c71c1d7bad01ff749cd81560974ce5199352075feeeda6d805a8d52dd2332` ->
`093cefd6c2f45824a756d1a2fa95b2fe1aa857ad505357ad5cdfcbaff81a4182`
(pinned in `tests/core/test_frozen.py`, with seven new `_FROZEN_TABLE` rows).

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump, matching the RFC-0001..0027 pre-release convention).

## Evidence

All on the 2026-10-31 data, with the engine's own cohort and cap code. Errors are against the full count with the eligibility we hold (the record, or the user cache for projects whose history is cut). 2,000 draws per project. Saving: the phone's cache, projects in candidates order, cross-project reuse, the draw this rule makes.

**No value moves on this data.** The rebuild reads every existing record in full. Against the published 2026-10-31 snapshot, 0 of 92,916 parameter rows differ; only the constants hash, and the hashes that cover it, move.

**What the next cycle gets**, with the rules compared at their best settings. Composite effect is the per-project p99 at that project's coverage after L4; the budget is 0.34 composite points.

| Rule | Projects | Lookups saved | Calls | Phone hours at 6-8 s a call | Worst composite p99 | Projects over 0.34 |
|---|---|---|---|---|---|---|
| Full count | 0 | 0 | 0 | 0 | 0 | 0 |
| **This rule** | 56 | 11,136 | 1,392 | 2.3-3.1 | 0 | 0 |
| The same, plus a variance-targeted sample on `hermes-agent` | 57 | 14,633 | 1,829 | 3.0-4.1 | 0.24 | 0 |
| The same, on all 8 mid-rate projects above 500 authors | 64 | 16,056 | 2,007 | 3.3-4.5 | 0.30 | 0 |
| Variance-targeted for every project above 500 authors, margin 0.03 | 19 | 11,904 | 1,488 | 2.5-3.3 | 0.30 | 0 |
| Binomial `n0` as first proposed (D3-B), above 1,000 authors | 8 | 10,767 | 1,345 | 2.2-3.0 | 0.70 | 3 |
| Every project above 500 authors at 300 | 24 | 18,677 | 2,334 | 3.9-5.2 | 1.40 | 10 |

The phone's call time is an estimate from its measured 11-15 s at the old width of 15. L4 as a whole is 76-174 h.

**Why a sample of authors needs this care.** A simple random sample of 300 has a 95% margin of about 5.6 points on a share of authors. DR_90 counts PRs, and one author can open thousands, so on the 24 largest frames a 300-author sample was off by up to 13.1 points at p95, wider than 5.6 points on 7 of them, and the per-author cap read up to 2.6 points low. At the fixed seed, `BerriAI/litellm` read 14.6 points low (-1.1 composite points). That is why mid-rate projects are counted in full. The ratio variance predicts the error (simulated over predicted standard deviation: median 1.04, p90 1.18), and it is what keeps `stablyai/orca` (rate 0.907, a few prolific authors, p99 error 0.16 at 300) and `odoo/odoo` (0.942 raw, 0.897 eligible) out of the sample, where a threshold on the rate alone would not.

**The rule at work.** On the 56 projects it samples: per-project p95 rate error is 1.3 points at the median and 6.2 at the worst (`nesquena/hermes-webui`), p99 is 1.7 and 8.0. The displayed rate carries that error; the score does not. Across all draws, one project's score moved in 1 draw of 2,000 (`langgenius/dify`, at most 0.05 composite points). With the eligibility gate off, no project's p99 moves either. The gap between the rate before and after eligibility is under 0.005 on all 56.

**5.2 and 5.3**, on the 31 fresh histories with a reserve, 200 draws each: mean n 15.3 to 18.5, 5.2 rmse 0.0903 to 0.0756, P(5.3 n >= 5) 0.552 to 0.638, bias about 0 before and after. This does not fix 5.3: it stays under its floor in 36% of draws. The reserve costs about 3-5 phone hours; 1.3's rmse moves from 0.0612 to 0.0577. Unknown veterans are modelled at each project's measured share.

**Fixture** (`tests/fixtures/snapshot_50/`), regenerated with `scripts/build_fixture_snapshot.py --rewrite-expected`: `rated=0, gated=18, unrated=32` is unchanged and no repository changes outcome; only `ScoreRecord.constants_hash` moves.

**Not in this RFC.** The first draft also read D-IN-3 through earliest-PR queries bounded at PR creation. It was taken out before any cycle ran (branch `parked/rfc-0028-two-tier-eligibility`): live, a batch of 50 failed 9 of 9 times against GitHub's 10-second limit, it was no faster at the widths that work, and it changed who counts as eligible for 43 of 475 authors.

## Reviewer

Reviewed 2026-11-02 by rv-105 (opus-max): rebuild comparison against the published snapshot, mutation proof, and the live query test above. The rule was chosen on the owner's instruction of 2026-11-02 to take the optimized way.
