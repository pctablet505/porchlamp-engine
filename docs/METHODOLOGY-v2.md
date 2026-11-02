<!-- GENERATED FILE. Do not hand-edit.
Regenerate with: PYTHONPATH=$PWD .venv/bin/python scripts/render_methodology.py
Source: porchlamp.audit.methodology_history.methodology_with_history (`docs/v2/06-UX-DESIGN.md` section 3 F).
tests/test_render_methodology.py fails if this file drifts from a fresh render. -->

# Porchlamp v2 Methodology

**Benchmark version:** `2.0.0-rc1`
**Schema version:** `2`
**Constants hash:** `53d310de9fae44586159118e9d2251f7aeb59de551187c7a79825a498aa03c9e`

Every number below is read directly from `porchlamp.core.constants` and the
`porchlamp.core.params` registry (or, for detectors and the two audit release
bars, their own frozen module) -- this page cannot drift from the code
that scores a repository, because it is rendered from the same values.

## Reproducing a snapshot

```
porchlamp reproduce <snapshot dir>
```

Every score this document describes is reproducible byte-for-byte from its
snapshot directory with the command above (D-VER-3): the same
`benchmark_version`, `constants_hash` and `code_commit` recorded in the
snapshot manifest are what a reader checks against this page.

## Pillars

| Pillar | Weight |
|---|---|
| responsiveness | 0.25 |
| criticality | 0.25 |
| governance | 0.2 |
| rigor | 0.15 |
| accessibility | 0.15 |

## Parameters

### Responsiveness (pillar weight 0.25)

| Input id | Weight within pillar | Min n | Window (days) | Curve | Value domain | Proxy |
|---|---|---|---|---|---|---|
| `ttfhr_hours` | 0.3 | 10 | 180 | 100 if the median hours to first substantive human response is <= 12; else max(0, 100 - 25 * log2(t / 12)) | [0, 256], lower is better | — |
| `dr90_decision_rate` | 0.25 | 10 | 180 | min(100, 125 * r) | [0, 1], higher is better | — |
| `mr_first_rate` | 0.2 | 5 | 180 | min(100, 125 * r) | [0, 1], higher is better | — |
| `gr14_ghost_rate` | 0.15 | 10 | 180 | max(0, 100 - 200 * g) | [0, 1], lower is better | — |
| `ttm_p90_hours` | 0.1 | 5 | 180 | 100 - ECDF_mid(p90), mid-rank ECDF over the frozen ecosystem population (D-NORM-1) | [0, 1000], lower is better | — |

### Criticality (pillar weight 0.25)

| Input id | Weight within pillar | Min n | Window (days) | Curve | Value domain | Proxy |
|---|---|---|---|---|---|---|
| `dependents_deduped` | 0.4 | — | — | ECDF_mid(value) on the raw measured value, no transform on either side (D-NORM-1/D-NORM-3) | [0, 1e+06], higher is better | — |
| `openssf_criticality` | 0.35 | — | — | 100 * C, the locally computed OpenSSF/Pike criticality score C in [0, 1] | [0, 1], higher is better | at least 8 of 10 signals present |
| `downloads_monthly` | 0.25 | — | — | ECDF_mid(value) on the raw measured value, no transform on either side (D-NORM-1/D-NORM-3) | [0, 1e+09], higher is better | — |

### Governance (pillar weight 0.2)

| Input id | Weight within pillar | Min n | Window (days) | Curve | Value domain | Proxy |
|---|---|---|---|---|---|---|
| `legal_ip` | 0.35 | — | — | evidence-based regime lookup; see this parameter's value_domain.score_table | DCO=100, NONE=85, FOUNDATION_CLA=75, CORP_CLA=25 | — |
| `elephant_factor` | 0.35 | 30 | — | min(100, 25 * EF) | [1, 10], higher is better | — |
| `promotion_ladder` | 0.3 | — | — | evidence-based rung lookup; see this parameter's value_domain.score_table | VERIFIED=100, DOCUMENTED=75, AD_HOC=40, CLOSED=10 | — |

### Rigor (pillar weight 0.15)

| Input id | Weight within pillar | Min n | Window (days) | Curve | Value domain | Proxy |
|---|---|---|---|---|---|---|
| `pipeline_defenses` | 0.4 | — | — | identity, clamped to [0, 100] (Scorecard check mean, or the proxy blend when absent) | [0, 100], higher is better | min n 10 when the method is a proxy |
| `ai_posture` | 0.3 | — | — | persona-conditional posture lookup; see this parameter's value_domain.score_table | REGULATED (human=95, ai=95); PROSCRIBED (human=85, ai=25); AD_HOC (human=40, ai=40) | — |
| `substantive_review_rate` | 0.3 | 10 | 180 | min(100, 125 * r) | [0, 1], higher is better | — |

### Accessibility (pillar weight 0.15)

| Input id | Weight within pillar | Min n | Window (days) | Curve | Value domain | Proxy |
|---|---|---|---|---|---|---|
| `newcomer_issue_supply` | 0.35 | — | — | min(100, 10 * count) | [0, 20], higher is better | — |
| `newcomer_conversion` | 0.35 | 5 | — | min(100, 200 * r) | [0, 1], higher is better | — |
| `rr90_return_rate` | 0.15 | 5 | — | min(100, 200 * r) | [0, 1], higher is better | — |
| `dx` | 0.15 | 5 | — | identity, clamped to [0, 100] (0.5 CI runtime score + 0.5 setup-signal score, or setup-signal score proxy when CI runtime has fewer than min_n commits) | [0, 100], higher is better | min n 0 when the method is a proxy |

## Gates

| Id | Name | Criterion | Cutoffs | Reason code(s) |
|---|---|---|---|---|
| `D-GATE-1` | Software project | repository tree contains a recognised build/package manifest at depth <= 2, or the repository is a curated anchor | — | `non_software` |
| `D-GATE-2` | Not archived, not a mirror, not a fork | archived == false, mirror_url is null, fork == false (a fork is allowed only if its parent is absent from the universe) | — | `archived`, `mirror`, `fork` |
| `D-GATE-3` | Licensed | SPDX id present and OSI-approved; an undetermined SPDX id falls back to a LICENSE* file search before the gate fails | — | `unlicensed` |
| `D-GATE-4` | Vitality | >= 1 commit by a non-bot User on the default branch in the trailing window | vitality_window_days=180 | `stale` |
| `D-GATE-5` | Not a black hole | over external PRs by non-bot authors (D-IN-1) opened in the trailing PR window and at least the minimum age, the 95% one-sided Wilson lower confidence bound (z = 1.6448536269514722) on the ignored share must not exceed the ceiling (RFC-0026: the ignored share is the share of those PRs that no maintainer reply, review, merge or close touched, within the deadline or since, when the data was read; a project that answers late is slow, not a black hole, and its 14-day unanswered share is scored by 1.4 GR_14, not gated) (a minimum population of 10 is required, else the gate is UNDECIDED rather than failed, and a repository whose PR records lack the fields the exclusions below read is UNDECIDED, neither passed nor failed: the missing fields move the share both ways); the raw share is the gr14_ghost_rate parameter, computed for such a repository without the rules those fields feed (the draft and self-close exclusions, and a maintainer close counted as a response). The gate judges only a PR record that covers the whole window. The collector records every PR opened in the window; when there are more than 2,000, it records replies and reviews on an even random sample of 1,000 of them, drawn by PR number with a fixed seed, and the gate uses the sample's share and size (RFC-0013). A record that stops short of the window's start is UNDECIDED, never judged on the newest weeks as if they were the window; the time the record starts (the cut-off) is recorded with the gate's evidence (RFC-0011). A reply or review counts when it is substantive (D-IN-2: any review, or a comment whose text, without markup, is at least 40 characters long, each Chinese, Japanese or Korean letter counting 3 times, since those scripts say in one letter about what Latin text says in three) by the repository's own people: an account that is not the PR's author, not a bot (D-IN-1), and is a member, owner or collaborator, or merged a PR in the recorded window, or closed a PR someone else opened there. Merging or closing another author's PR takes repository access, whatever association GitHub shows: org membership can be private, and committers who land changes outside GitHub show as contributors. A merge or maintainer close within the deadline counts on its own. A comment or review that follows a reply template, the first 2 words of its text once links, mentions and numbers are removed, which one account posted on at least 20 PRs of the window, on at least 80% of them within 60 seconds of the PR opening, is automation running under a person's account and does not count as a reply (RFC-0025); the same account's other replies still count. Five false-positive exclusions apply (RFC-0008, RFC-0010): draft PRs are excluded until marked ready for review (clock starts at ready_at); author self-closed PRs with no earlier maintainer response are excluded only when the author closed them within the 14-day response deadline from clock start, and a later self-close with no response counts as unanswered; accounts under 90 days old (MIN_ACCOUNT_AGE_DAYS) are excluded; maintainer closes within 14 days count as responses; and the 14-day observation age is judged from clock start against min(now, observed_at) | min_n=10, min_age_days=14, response_deadline_days=14, max_zero_response_share=0.5 | `black_hole` |
| `D-GATE-6` | PR workflow | >= 1 external PR (bots included: the gate asks whether the repository works through GitHub pull requests at all) decided (merged or closed) in the trailing decision window, read from the recorded PR window, the 365-day author history and the 30 newest PRs, or off-platform markers present (routed, not failed); neither means unrated: no_external_workflow, but only when those records hold the repository's whole PR history (the 30 newest were fewer than 30, and no window or history read beside them is truncated): otherwise the gate is UNDECIDED, because a PR opened before the records may have been decided inside the window. Each record shows decisions up to its own fetch. RFC-0027: a repository whose complete PR window holds fewer than 3 outside PRs (external, non-bot authors) opened in the trailing 180 days is also unrated: no_external_workflow, whatever it decided, because there is too little outside traffic to contribute to or to measure (routed instead when it works off-platform; UNDECIDED when that is unknown). A repository also routes off-platform, whatever its decided PRs, when it hands review off: among its outside PRs (external, non-bot authors in the recorded PR window) at least 5, and at least 50% of them, carry a notice, a comment by an account other than the PR's author that contains "has been imported to gerrit for code review", "welcome to gitgitgadget", "import it to our internal repository for further review", "this pull request has been imported" or "has imported this pull request" (any letter case) or is written by an account whose login starts with "gitgitgadget". D-GATE-5 is then undecided for it, never failed | decision_window_days=365, min_outside_prs=3, handoff_min_prs=5, handoff_min_share=0.5 | `unrated: no_external_workflow` |
| `D-EVID-1` | Evidence sufficiency | composite coverage must reach the rated minimum and every required pillar must have at least one measured parameter (RFC-0024), else listed unranked | min_composite_coverage=0.6, required_pillars=responsiveness | `unrated: insufficient_evidence` |

## Missingness

### Reasons

| Reason | Semantics |
|---|---|
| `third_party_gap` | A third party (a registry, OpenSSF, deps.dev) never provided the fact; no maintainer action can close this gap. |
| `collector_failure` | The collector attempted to measure this input and failed (a fetch error, a stale window anchor); a retry can still measure it. |
| `insufficient_n` | The input was measured but its sample size fell below the parameter's minimum n; the measurement is recorded but scored as unmeasured. |
| `not_applicable` | The parameter is undefined for this repository's class by a closed, design-level rule (D-MISS-5); excluded from both the numerator and the denominator of coverage. |
| `no_population` | The parameter is ECDF-normalised and no rated, measured repository exists in its ecosystem or globally at this snapshot to build a population from. |

### Coverage thresholds

| Threshold | Value |
|---|---|
| unrated_below | 0.6 |
| ceiling_b_below | 0.8 |
| ceiling_a_below | 0.9 |

### Not-applicable rules (D-MISS-5)

| Parameter | Bundle fact | Value |
|---|---|---|
| `dependents_deduped` | `ecosystem` | `systems` |
| `downloads_monthly` | `ecosystem` | `go` |
| `downloads_monthly` | `ecosystem` | `maven` |
| `downloads_monthly` | `ecosystem` | `systems` |

OpenSSF no-dependents-signal proxy minimum: 8 of 10 signals present.

## Tie-breakers

Composite is rounded to 2 decimal place(s) before comparison.

| Level | Field | Direction | Note |
|---|---|---|---|
| 1 | composite | descending | rounded to 2 decimal place(s) before comparison |
| 2 | coverage | descending | — |
| 3 | responsiveness_pillar_score | descending | — |
| 4 | friction | ascending | — |
| 5 | leverage | descending | — |
| 6 | ttfhr_p75_hours | ascending | p75, not the median: consistency, not just the typical case |
| 7 | last_non_bot_commit_at | descending | — |
| 8 | owner_name_lowercase | ascending | deterministic last resort; never stars, forks or downloads |

## Personas

### Presets

| Persona | responsiveness | criticality | governance | rigor | accessibility |
|---|---|---|---|---|---|
| balanced | 0.25 | 0.25 | 0.2 | 0.15 | 0.15 |
| first_timer | 0.35 | 0.1 | 0.15 | 0.1 | 0.3 |
| career_capital | 0.2 | 0.35 | 0.15 | 0.1 | 0.2 |
| senior_systems | 0.15 | 0.35 | 0.25 | 0.2 | 0.05 |
| ai_assisted | 0.2 | 0.2 | 0.15 | 0.35 | 0.1 |

### Gates

| Persona | Description | Thresholds |
|---|---|---|
| ai_assisted | Hard AI ban (exclusion gate): fires only when ai_posture is measured PROSCRIBED with non-empty evidence. | — |
| senior_systems | Multi-vendor (exclusion gate): fires only on a measured elephant factor below the minimum. | min_elephant_factor=3 |
| first_timer | Low barrier (inclusion gate): shown when decided first-timer PRs in the window reach the minimum count and MR_first reaches its minimum rate, or the newcomer-issue-supply count reaches its minimum; an unmeasured input never satisfies an inclusion gate (D-PERS-3). | min_decided_prs=5, min_mr_first=0.4, min_newcomer_issues=3 |
| career_capital | High-tier brand (inclusion gate): shown when the criticality pillar score and its coverage both reach their minimums. | min_criticality=60, min_coverage=0.8 |

## Detectors

| Name | Trigger | Direction | Thresholds |
|---|---|---|---|
| `param_jump` | any parameter score moves by more than threshold_points between consecutive snapshots, with n at least min_n on both snapshots | magnitude | threshold_points=40, min_n=10 |
| `speed_without_decisions` | TTFHR score >= ttfhr_score_min and DR_90 score <= dr90_score_max | compound | ttfhr_score_min=90, dr90_score_max=40 |
| `merges_without_return` | MR_first score >= mr_first_score_min and (conversion or return) score <= conversion_or_return_score_max, with n at least min_n | compound | mr_first_score_min=90, conversion_or_return_score_max=20, min_n=10 |
| `closed_unread` | share of outside PRs (an external author association and a non-bot author, D-IN-1) opened in the trailing PR window that were closed unmerged with no substantive response ever recorded (D-IN-2, as the D-GATE-5 criterion words it) >= share_min, over at least min_n outside PRs; a repository with no recorded PR window is not measured | above | share_min=0.3, min_n=10 |
| `newcomer_burst` | at least min_accounts eligible-looking, all-merged first-timer accounts created within any window_days-day span | compound | min_accounts=5, window_days=7 |
| `star_burst` | the StarScout lockstep penalty was applied to this repo at universe entry | boolean | — |
| `label_spam` | newcomer-labelled issue count >= issue_count_min and newcomer conversion score <= conversion_score_max | compound | issue_count_min=50, conversion_score_max=10 |
| `ef_domain_mismatch` | top commit email-domain share >= domain_share_min while elephant factor >= ef_min | compound | domain_share_min=0.5, ef_min=4 |
| `source_drift` | the live-sample canary currently holds this repo's row | boolean | — |

## Not-hashed process constants

These three values gate whether a *snapshot* clears the bar for release,
not any per-repository score; they are process constants, not scoring
inputs, and are therefore not part of `CONSTANTS_HASH`
(`docs/v2/rfcs/README.md` "Process constants"). A change to any of them
still goes through the RFC procedure below.

| Name | Module | Value | What it gates |
|---|---|---|---|
| `MIN_COVERAGE` | `porchlamp.audit.anchors` | 0.9 | D-VER-4 anchor-check minimum rating coverage (a release gate, not a per-repo score) |
| `TAU_RELEASE_THRESHOLD` | `porchlamp.audit.sensitivity` | 0.9 | D-AGG-3's Kendall tau-b release bar for a +/-5pp weight perturbation |
| `DEFAULT_PERTURBATION_PP` | `porchlamp.audit.sensitivity` | 5 | the +/-5pp weight-perturbation size the sensitivity report tests at |

## Changing a constant: the RFC procedure

Every constant `porchlamp.core.constants` defines is frozen (D-VER-1/2): a
change requires an RFC filed under `docs/v2/rfcs/`, a re-pinned
`CONSTANTS_HASH`, a `BENCHMARK_VERSION` bump and a named, independent
reviewer. The full procedure, its two documented enforcement limits, and
the pre-release version-bump rule live in
[`docs/v2/rfcs/README.md`](v2/rfcs/README.md); every change below followed it.

### RFC log

| Date | RFC | Summary | Hash before -> after |
|---|---|---|---|
| 2026-11-02 | RFC-0028 | Add `NEWCOMER_SAMPLE_SIZE = 20` (relocated from `porchlamp.collect.github_graphql`), `NEWCOMER_CANDIDATE_CAP = 35`, `DR90_AUTHOR_CAP_TRIGGER = 500`, `DR90_AUTHOR_SAMPLE_CAP = 300`, and `ELIGIBILITY_TIER1_BATCH_SIZE = 50` (D-IN-3 / D-IN-4, #89 Q2b / Q9 / Q11): pre-filter `<90d` and non-`User` newcomer candidates at 0 GraphQL cost and search up to 15 reserve newcomer candidates when primary candidates have prior PRs before `anchor - 365d`; deterministically sample 300 distinct `DR_90` band authors when a repository exceeds 500; and add two-tier PR-time-bounded eligibility queries (`AuthorEligibilityTier1Batch` at width 50 + scalar `AuthorEligibilityTier2Batch` at width 8). | `de2c71c1d7bad01ff749cd81560974ce5199352075feeeda6d805a8d52dd2332` -> `53d310de9fae44586159118e9d2251f7aeb59de551187c7a79825a498aa03c9e` |
| 2026-10-24 | RFC-0027 | Add `GATE6_MIN_OUTSIDE_PRS = 3` (D-GATE-6): a repository whose complete PR window holds fewer than 3 outside PRs (external, non-bot authors) opened in the trailing 180 days is `unrated: no_external_workflow` whatever it decided, routed instead when it works off-platform and undecided when that is unknown. `GateEvidence` gains `outside_prs_180d`; the collector's early prune applies the same floor to a recorded window. | `861c2259fbee531957b102e4dbfa11f5c5ab532d9cd75a3df21fe1e2c203fcf7` -> `de2c71c1d7bad01ff749cd81560974ce5199352075feeeda6d805a8d52dd2332` |
| 2026-10-24 | RFC-0026 | D-GATE-5 judges the *ignored* share instead of the 14-day unanswered share: a counted outside PR is ignored when it was not answered in 14 days and no maintainer reply, review, merge or close touched it, in time or since, when the data was read. A project that answers late is slow, not a black hole: 1.4 GR_14 (unchanged) scores it and it stays rankable. `GateEvidence` gains `pr_ignored` and `pr_ignored_n`; the evidence panel and the project card show the ignored count. 553 of the 845 black holes on the 2026-10-23 data (evaluated at 2026-10-22) stay, 292 are released. | `861c2259fbee531957b102e4dbfa11f5c5ab532d9cd75a3df21fe1e2c203fcf7` -> `861c2259fbee531957b102e4dbfa11f5c5ab532d9cd75a3df21fe1e2c203fcf7` |
| 2026-10-24 | RFC-0025 | Add `AUTOMATION_MIN_PRS = 20`, `AUTOMATION_FAST_SECONDS = 60`, `AUTOMATION_FAST_SHARE = 0.80` and `AUTOMATION_TEMPLATE_WORDS = 2` (D-IN-2): a reply template (the first 2 words of a review or comment, names, numbers and links removed) that one account posted on at least 20 PRs of a window, on at least 80% of them within 60 s of the PR opening, is automation posted from a person's account and answers no PR; the account's other replies still count. `porchlamp.core.gates.maintainer_logins` returns `Maintainers`, carrying the window's `automated` pairs, so every reply measure applies the rule. 15 of 10,998 projects change on the 2026-10-23 data (handsontable 0.002 h -> 10.95 h median first reply). | `d8c067b9e928b138beb68c9909d605b5737a1885275c786e0fbe8d9050b8a1c2` -> `861c2259fbee531957b102e4dbfa11f5c5ab532d9cd75a3df21fe1e2c203fcf7` |
| 2026-10-23 | RFC-0024 | D-EVID-1 requires at least one measured parameter in every `EVIDENCE_GATE_REQUIRED_PILLARS = ("responsiveness",)` pillar in addition to `C >= 0.60`, so a repository whose answers to outside pull requests were never observed is `unrated: insufficient_evidence` rather than ranked on its other four pillars alone (`3,746 -> 3,471` rated on 2026-10-21, removing 275 empty-responsiveness repos including 64 with 100% 14-day zero-response share over `n >= 3` PRs; Kendall `tau = 1.0` among remaining rated repos). D-MISS-3 lowers `NO_CEILING_MIN_COVERAGE` from `0.95` to `0.90` so at most one missing parameter in any pillar (`C in [0.90, 0.975]`, e.g. rank #1 `servo/rust-smallvec` at `C = 0.9375`) does not cap a repository at Tier A once parameter compression is lifted, while two missing parameters remain capped (`449` rated repos on 2026-10-21 move from `tier_ceiling = "A"` to `None`). | `4a6529ef5ca3c97c443442774440b4947ebc849265132fc16a61e3a60f231a2a` -> `d8c067b9e928b138beb68c9909d605b5737a1885275c786e0fbe8d9050b8a1c2` |
| 2026-10-23 | RFC-0023 | D-UNI-2's star criteria search a language only when its projects have an ecosystem the benchmark models: Kotlin, Scala and Groovy join Java as `maven` (Maven Central), Zig, Vim Script and LLVM join C and C++ as `systems` (no package registry, D-MISS-5); C#, Ruby, PHP, Dart, Swift, Lua and Clojure wait for their registries to be modeled. One star floor for every language. A registry list's downloads are recorded as `downloads_at_selection`, never as `stars_at_selection`. Process class: `SEARCH_CRITERIA` is not hashed. Dry run 2026-10-22: at most 220 repos clear the 4,234-star floor in the added languages (apache/spark, neovim/neovim, llvm/llvm-project among them); they enter when the owner raises `--max-total`. | `4a6529ef5ca3c97c443442774440b4947ebc849265132fc16a61e3a60f231a2a` -> `4a6529ef5ca3c97c443442774440b4947ebc849265132fc16a61e3a60f231a2a` |
| 2026-10-23 | RFC-0022 | Add `PR_REPLY_NODE_CAP = 300` (D-IN-4: each PR's `reviews(first: 10)` and `comments(first: 20)` record `totalCount` and `pageInfo`, and every cut list on a PR whose replies the record holds is read on by PR number in pages of 100 until whole or 300 nodes; `timelineItems` records the newest 100 ready-for-review and close events, was 5; a PR whose replies stay cut with no recorded D-IN-2 response is withheld from the reply measures rather than counted as unanswered). On a 1,242-PR live sample across 11 repos: 93 PRs (7.49%) exceeded 10 reviews or 20 comments, 0 exceeded 300; reading on recovered 2 missed first responses (`ttfhr_n` 698 -> 700), 5 of 895 substantive reviews (4.3) and 31 of 507 merged-PR approvals (4.1 proxy). | `b81c8bc48501e62976493eda447ce11944a2d7d53a3cd4a126f71cb766c7ff05` -> `4a6529ef5ca3c97c443442774440b4947ebc849265132fc16a61e3a60f231a2a` |
| 2026-10-23 | RFC-0021 | D-IN-4 records every default-branch commit of the 365-day window up to `COMMIT_CAP = 5000` and above it 50 evenly spaced pages of 100 (`sampled: true`) with GitHub's 365-day count (`total`), keeping each commit's `sha` for ledger deduplication; 2.2 scales `commit_frequency_weekly` by `total`, and a record cut at `LEGACY_COMMIT_CAP = 1000` without a sample or total is `Unmeasured(collector_failure)` for 3.2 EF, 2.2, 3.1's sign-off regime and `top_email_domain_share`. | `93e44701a8a9862e242e0e1f93e7f082848fbc1f222d68bdcd221600192bac8c` -> `b81c8bc48501e62976493eda447ce11944a2d7d53a3cd4a126f71cb766c7ff05` |
| 2026-10-23 | RFC-0020 | Add `DX_PROXY_MIN_N = 0` and `METHOD_PROXY_DX_SETUP_SIGNALS = "proxy:setup_signals_only"` for parameter 5.4 (`dx`): when `actions_runs` has fewer than `PARAM_MIN_N["dx"]` (`5`) pull-request commits with a finished GitHub Actions run and setup-signal sources (`tree`, `CONTRIBUTING.md`, `README.md`, `governance`) were collected, 5.4 scores the setup-signals rubric (`0/40/70/100`) under `method = "proxy:setup_signals_only"` instead of discarding setup evidence as `insufficient_n`. Converts 1,071 `insufficient_n` rated repos on the 2026-10-21 snapshot (measured 4,371 -> 5,442 of 5,448). | `3bc4aac040af2937e438e39e3e18ffbbfad6ddf7aeea7a1911f6e6013105226d` -> `93e44701a8a9862e242e0e1f93e7f082848fbc1f222d68bdcd221600192bac8c` |
| 2026-10-23 | RFC-0019 | Fix parameter 4.2 (`ai_posture`) classification in `porchlamp.collect.governance`: expand `POLICY_FILES` from 10 to 14 paths (adding PR templates), evaluate `PROSCRIBED` before `REGULATED`, expand `AI_BAN_PATTERNS` with qualifier and non-code-object guards, and detect prose AI contribution rules and PR-template AI disclosure checkboxes for `REGULATED`. On the 256-repo AI-mentioning governance corpus: `0 -> 30 PROSCRIBED`, `90 -> 165 REGULATED`, `166 -> 61 AD_HOC`, `0/63` labelled sentence corpus mismatches. No hashed constant added or changed. | `3bc4aac040af2937e438e39e3e18ffbbfad6ddf7aeea7a1911f6e6013105226d` -> `3bc4aac040af2937e438e39e3e18ffbbfad6ddf7aeea7a1911f6e6013105226d` |
| 2026-10-23 | RFC-0018 | Relocate `SUBSTANTIVE_REVIEW_BODY_CHARS = 80` into `porchlamp.core.constants` and add `SUBSTANTIVE_REVIEW_BOILERPLATE_MIN_PRS = 3` for parameter 4.3 (`substantive_review_rate`): credit non-boilerplate D-IN-2 maintainer issue comments and review bodies of `>= 80` effective characters (`gates.comment_length`, CJK weight 3), filter normalized review/comment templates repeated on `>= 3` distinct PRs in the window, and exclude never-ready drafts and unanswered within-14d author self-closes from the decided-external denominator. On the 25-repo live study: median rate `16.5% -> 31.8%` (median score `20.6 -> 39.7`). | `a48306f15d2c9e7603ca00ae25f34e176fbb1421b270a072f9db645f1eb5e1a3` -> `3bc4aac040af2937e438e39e3e18ffbbfad6ddf7aeea7a1911f6e6013105226d` |
| 2026-10-23 | RFC-0017 | Relocate `FOUNDATION_ORGS` into `porchlamp.core.constants` (adding `seleniumhq` and `trinodb`, dropping the `*-foundation` suffix heuristic from v2 `legal_ip`), add `eclipsefdn/eca` to `FOUNDATION_CHECK_PATTERNS`, and order `classify_legal_ip` as `foundation_cla_check` > `foundation_owner_cla_check` / `corp_cla_check` > `dco_check` > `signoff_share` > `foundation_owner` > `no_evidence` so measured DCO evidence beats a bare `foundation_owner` fallback without a CLA check. On the 2026-10-21 corpus + live probes: 4 Apache repos flip `FOUNDATION_CLA -> DCO`, 6 `*-foundation` repos flip `FOUNDATION_CLA -> NONE`, and `SeleniumHQ/selenium` and `trinodb/trino` flip `CORP_CLA -> FOUNDATION_CLA`. | `85df224143be2f7ee4a94f610b104b22f2c6a82f0ad35ddbb47ef9fb78ece50f` -> `a48306f15d2c9e7603ca00ae25f34e176fbb1421b270a072f9db645f1eb5e1a3` |
| 2026-10-23 | RFC-0016 | Replace 3.3 `promotion_ladder` `VERIFIED`'s unreachable `PullRequest.authorAssociation` transition check (`0 / 4,918` on the 2026-10-21 corpus because GitHub evaluates `authorAssociation` at query time) with `observed_promotions(prs)`: a non-bot D-IN-3 age-eligible (`>= MIN_ACCOUNT_AGE_DAYS`) contributor whose PR was merged by another non-bot human, who had no prior merge or non-author close action, and who at least `PROMOTION_MIN_OUTSIDE_DAYS = 30` days later merged or closed another author's PR (D-IN-2). A `pr_window` cut short of 180 d now refuses every 3.3 rung (`VERIFIED` included). On a 16-repo live validation sample: 4 of 16 repos classify `VERIFIED` (vs 0/16 before). | `5a406aecf99b4939c18d7453b7b73bd3f9af0cd460140489568f0b23f91d4365` -> `85df224143be2f7ee4a94f610b104b22f2c6a82f0ad35ddbb47ef9fb78ece50f` |
| 2026-10-23 | RFC-0015 | Add `LANDING_LABELS = frozenset({"integrated", "merged"})` and `porchlamp.core.landing` (`is_landed`, `merged_or_landed_at`, `landing_recorded`, `PR_LANDING_FIELDS`): a closed, unmerged PR whose latest close event names a commit as its `closer` or that carries a label in `LANDING_LABELS` (whole name, lower-cased) was landed outside GitHub's merge and counts as merged at its `closedAt` for 1.3 `MR_first` (and its no-prior-merge predicate), 1.5 `TTM_p90`, 5.2 `newcomer_conversion`, 5.3 `RR_90` and the 4.1 review proxy (`AUTHOR_WINDOW_QUERY` and `LIGHT_PR_HISTORY_QUERY` now record `PR_LANDING_FIELDS`; decision measures 1.2 `DR_90`, 1.4 `GR_14` and D-GATE-5 are unchanged). No rank movement on the fixture (no `pr_window` or `author_history` envelopes in its corpus). | `4116987bb0ea32861181dff41647832e4fbc9fae75dc59fbd7436ac534e96d6b` -> `5a406aecf99b4939c18d7453b7b73bd3f9af0cd460140489568f0b23f91d4365` |
| 2026-10-23 | RFC-0014 | Every `pr_window` consumer (1.1 TTFHR, 1.2 DR_90, 1.5 TTM_p90, 4.3 substantive review rate, D-GATE-6 review hand-off and fingerprint pruning) classifies outside PRs (`porchlamp.core.gates.is_outside_pr`: external `authorAssociation` and non-bot author) from the recorded `prs` nodes at derive time instead of reading the envelope's stored `derived` list, excluding bot-authored PRs from 1.1, 1.2, 1.5 and 4.3 as D-GATE-5 and `closed_unread` already do (#57, #66 F3). Add 14 verified User-typed service accounts (`autheliabot`, `backstage-service`, `ciliumbot`, `crate-jenkins`, `elasticsearchmachine`, `hlsjs-ci`, `istio-testing`, `mattermost-build`, `servo-bot`, `sqla-tester`, `tencent-adm`, `ultralyticsassistant`, `webkit-early-warning-system`, `yoshi-approver`) to `BOT_LOGINS` (95 -> 109). No rank movement on the fixture (no `pr_window` envelopes in its corpus). | `be7023c09aa5514472d2434d33527449db5a490152f0a3e1ea2defaf052ab91a` -> `4116987bb0ea32861181dff41647832e4fbc9fae75dc59fbd7436ac534e96d6b` |
| 2026-10-18 | RFC-0013 | D-IN-4 records every PR of the 180-day window, every field up to `PR_CAP = 2000` and above it replies and reviews on an even random sample of `PR_SAMPLE_CAP = 1000` drawn by PR number with `PR_SAMPLE_SEED` (was the newest 500, unhashed); the author history records the whole 365 days. Every window measure, the 4.1 proxy and 3.3's absence-based rungs are unmeasured on a record cut short of its window, and the cohort rates on a history cut short of their cohort. On the 2026-10-17 data, collected with the 500 cap: judged 2,745 -> 2,745, black holes 638 -> 638, rated 4,396 -> 3,219; not to be published before the records are collected again. | `6f95d75a0aa5427288d3e7e8eadc5b9477d3a1816d636ab848b9defc7d6cea5a` -> `be7023c09aa5514472d2434d33527449db5a490152f0a3e1ea2defaf052ab91a` |
| 2026-10-17 | RFC-0012 | D-IN-1's exact login list moves into the hashed constants as `BOT_LOGINS`, the one list for PR responses, maintainer evidence, PR authors and commits (it was two unhashed copies, in `porchlamp.core.commit_domains` and `porchlamp.collect.github_graphql`). It keeps the 20 generic logins and adds 75 service accounts GitHub types as users (`rustbot`, `k8s-ci-robot`, `flinkbot` and others), each named like a bot and behaving like one in the recorded data. On the 2026-10-17 data: judged 2,745 -> 2,745, black holes 632 -> 638. | `5dc93c2f7bd97ebe46185da3eab34916d2f203f366c4157b964b96b4a20792c7` -> `6f95d75a0aa5427288d3e7e8eadc5b9477d3a1816d636ab848b9defc7d6cea5a` |
| 2026-10-17 | RFC-0011 | D-GATE-5 judges only a PR record that covers the whole window: a record cut at the collector's PR cap before the window start leaves the gate UNDECIDED, its cut-off recorded with the gate evidence. D-IN-2 counts the repository's own people, one definition in `porchlamp.core.gates`: a member, owner or collaborator, or an account that merged a PR or closed another author's PR in the record; bots never. Add `SUBSTANTIVE_COMMENT_MIN_CHARS = 40` (relocated) and `SUBSTANTIVE_COMMENT_CJK_WEIGHT = 3` (a Chinese, Japanese or Korean letter counts three characters), and Meta's two import notices to `GATE6_HANDOFF_NOTICES`. On the 2026-10-17 data: judged 3,613 -> 2,745, black holes 720 -> 632. | `6e4f3e8e1d0c0fcd829d9c8c386a65179eadb5f936595f96e9eba98712d55b94` -> `5dc93c2f7bd97ebe46185da3eab34916d2f203f366c4157b964b96b4a20792c7` |
| 2026-10-17 | RFC-0010 | D-GATE-5 author self-close exclusion (RFC-0008 rule 2) applies only to a close within `GATE5_RESPONSE_DEADLINE_DAYS` (14) of clock start; a later self-close with no response counts as unanswered (owner ruling 2026-10-17). No constant added or changed, so the hash does not move. Not measurable on the recorded data (no close actor recorded). | `6e4f3e8e1d0c0fcd829d9c8c386a65179eadb5f936595f96e9eba98712d55b94` -> `6e4f3e8e1d0c0fcd829d9c8c386a65179eadb5f936595f96e9eba98712d55b94` |
| 2026-10-14 | RFC-0008 | Add `GATE5_WILSON_Z = 1.6448536269514722` (D-GATE-5: 95% one-sided Wilson lower confidence bound on zero-response share, plus 5 false-positive reduction rules: draft exclusion/ready clock start, author self-close exclusion, <90d account exclusion, maintainer close within 14d as response). No rank movement on the fixture (no `pr_window` envelopes in its corpus). | `aca719a922264ba5edb6adcdab6c0c08eaff7a315d0e5818a567509e5b0d3ba9` -> `6e4f3e8e1d0c0fcd829d9c8c386a65179eadb5f936595f96e9eba98712d55b94` |
| 2026-10-13 | RFC-0007 | Add `GATE6_HANDOFF_MIN_PRS = 5`, `GATE6_HANDOFF_MIN_SHARE = 0.5`, `GATE6_HANDOFF_NOTICES` and `GATE6_HANDOFF_RELAY_LOGIN_PREFIXES` (D-GATE-6: a repository whose own notices send outside PRs to another review system, e.g. Gerrit or a mailing list, is routed off-platform and is not failed at D-GATE-5). No rank movement on the fixture (no `pr_window` envelopes in its corpus); on the recorded data six repositories hand off. | `aa6c4cd6f702fdc738cafb68022eb2613491657a9cb74c180fc2a1bd1236e662` -> `aca719a922264ba5edb6adcdab6c0c08eaff7a315d0e5818a567509e5b0d3ba9` |
| 2026-10-12 | RFC-0006 | Add `("downloads_monthly", "ecosystem", "go")`, `("downloads_monthly", "ecosystem", "maven")`, and `("downloads_monthly", "ecosystem", "systems")` to `NOT_APPLICABLE_RULES` (D-MISS-5: ecosystems with no public monthly-downloads registry). | `748be6e459b0d9eac279233dac75acd5ede7da9711590c84fd0317a84762cb6b` -> `aa6c4cd6f702fdc738cafb68022eb2613491657a9cb74c180fc2a1bd1236e662` |
| 2026-09-13 | RFC-0004 | Relocate D-IN-3's `MIN_ACCOUNT_AGE_DAYS = 90` / `MIN_PUBLIC_EVENTS = 5` / `AUTHOR_CAP_FRACTION = 0.20` out of `porchlamp.collect.github_graphql` into `porchlamp.core.constants_v2` / `porchlamp.core.hygiene` (W3-1). No rank movement (no scoring-path consumer yet). | `419091af843a5ab994b23a5776c0f0ce9073ed283b9f540b1d26eca7bcd4d7a9` -> `748be6e459b0d9eac279233dac75acd5ede7da9711590c84fd0317a84762cb6b` |
| 2026-09-13 | RFC-0003 | Add `NOT_APPLICABLE_RULES` (D-MISS-5's closed not_applicable rule list), `OPENSSF_PROXY_MIN_SIGNALS = 8` (row 2.2's `proxy:no_dependents_signal` minimum), and relocate `WINDOW_STALENESS_DAYS = 14` from `porchlamp.collect.pike_signals` (W1-7f follow-up). No rank movement on the fixture (0 rated repos; `records_hash` moves only from the new hashed constants and `PillarScore.applicable`'s structural addition). | `446c6a25db4e8c1f9d528f98a332d52bdf5ba570f07774bd674d9c816fdd09d0` -> `419091af843a5ab994b23a5776c0f0ce9073ed283b9f540b1d26eca7bcd4d7a9` |
| 2026-09-13 | RFC-0002 | Relocate the five `STARS_SCOUT_*` universe-entry thresholds (D-IN-5 star-velocity filter, W1-10) from the unhashed `porchlamp.constants` into `porchlamp.core.constants_v2`; values unchanged. No rank movement (D-IN-5: stars never enter the composite). | `d2702ea93a7ad9eb22696942dd3994d63f066ff2bf2e68e52abfc8235433570e` -> `446c6a25db4e8c1f9d528f98a332d52bdf5ba570f07774bd674d9c816fdd09d0` |
| 2026-09-13 | RFC-0001 | Add `AUTHOR_UNMEASURED_MAX_FRACTION = 0.20` (D-MISS-1 collector-failure gate for row 1.2/1.3, W1-4 round-2 review B-2/B-3); relocated out of `porchlamp.collect.github_graphql`, closing the census escape. No rank movement (no scoring-path consumer yet). | `391d10b91e86a230fa8c16e32f57f2bc5d0449c33dc3bcb6c3f96153ef83fed8` -> `d2702ea93a7ad9eb22696942dd3994d63f066ff2bf2e68e52abfc8235433570e` |
| 2026-09-13 | genesis | Initial v2 constants freeze (packet W2-8 round 2, `d7664f2`): relocated every scored tunable into `porchlamp.core.constants_v2` and computed `CONSTANTS_HASH` for the first time. No RFC — the procedure and this changelog begin governing changes only after this commit. | *(none)* -> `391d10b91e86a230fa8c16e32f57f2bc5d0449c33dc3bcb6c3f96153ef83fed8` |
