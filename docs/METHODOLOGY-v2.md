<!-- GENERATED FILE. Do not hand-edit.
Regenerate with: PYTHONPATH=$PWD .venv/bin/python scripts/render_methodology.py
Source: porchlamp.audit.methodology_history.methodology_with_history (`docs/v2/06-UX-DESIGN.md` section 3 F).
tests/test_render_methodology.py fails if this file drifts from a fresh render. -->

# Porchlamp v2 Methodology

**Benchmark version:** `2.0.0-rc1`
**Schema version:** `2`
**Constants hash:** `6e4f3e8e1d0c0fcd829d9c8c386a65179eadb5f936595f96e9eba98712d55b94`

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
| `dx` | 0.15 | 5 | — | identity, clamped to [0, 100] (0.5 CI runtime score + 0.5 setup-signal score) | [0, 100], higher is better | — |

## Gates

| Id | Name | Criterion | Cutoffs | Reason code(s) |
|---|---|---|---|---|
| `D-GATE-1` | Software project | repository tree contains a recognised build/package manifest at depth <= 2, or the repository is a curated anchor | — | `non_software` |
| `D-GATE-2` | Not archived, not a mirror, not a fork | archived == false, mirror_url is null, fork == false (a fork is allowed only if its parent is absent from the universe) | — | `archived`, `mirror`, `fork` |
| `D-GATE-3` | Licensed | SPDX id present and OSI-approved; an undetermined SPDX id falls back to a LICENSE* file search before the gate fails | — | `unlicensed` |
| `D-GATE-4` | Vitality | >= 1 commit by a non-bot User on the default branch in the trailing window | vitality_window_days=180 | `stale` |
| `D-GATE-5` | Not a black hole | over external PRs by non-bot authors (D-IN-1) opened in the trailing PR window and at least the minimum age, the 95% one-sided Wilson lower confidence bound (z = 1.6448536269514722) on the zero-response share must not exceed the ceiling (a minimum population of 10 is required, else the gate is UNDECIDED rather than failed, and a repository whose PR records lack the fields the exclusions below read is UNDECIDED, neither passed nor failed: the missing fields move the share both ways); the raw share is the gr14_ghost_rate parameter, computed for such a repository without the rules those fields feed (the draft and self-close exclusions, and a maintainer close counted as a response). A reply or review counts when it is substantive (D-IN-2: any review, or a comment of 40+ characters, by a non-author, non-bot member, owner or collaborator, or by an account that merged a PR in the window, since org membership can be private); a merge or maintainer close within the deadline counts on its own. Five false-positive exclusions apply (RFC-0008, RFC-0010): draft PRs are excluded until marked ready for review (clock starts at ready_at); author self-closed PRs with no earlier maintainer response are excluded only when the author closed them within the 14-day response deadline from clock start, and a later self-close with no response counts as unanswered; accounts under 90 days old (MIN_ACCOUNT_AGE_DAYS) are excluded; maintainer closes within 14 days count as responses; and the 14-day observation age is judged from clock start against min(now, observed_at) | min_n=10, min_age_days=14, response_deadline_days=14, max_zero_response_share=0.5 | `black_hole` |
| `D-GATE-6` | PR workflow | >= 1 external PR (bots included: the gate asks whether the repository works through GitHub pull requests at all) decided (merged or closed) in the trailing decision window, read from the recorded PR window, the 365-day author history and the 30 newest PRs, or off-platform markers present (routed, not failed); neither means unrated: no_external_workflow, but only when those records hold the repository's whole PR history (the 30 newest were fewer than 30): otherwise the gate is UNDECIDED, because a PR opened before the records may have been decided inside the window. A repository also routes off-platform, whatever its decided PRs, when it hands review off: among its outside PRs (external, non-bot authors in the recorded PR window) at least 5, and at least 50% of them, carry a notice, a comment by an account other than the PR's author that contains "has been imported to gerrit for code review", "welcome to gitgitgadget" or "import it to our internal repository for further review" (any letter case) or is written by an account whose login starts with "gitgitgadget". D-GATE-5 is then undecided for it, never failed | decision_window_days=365, handoff_min_prs=5, handoff_min_share=0.5 | `unrated: no_external_workflow` |
| `D-EVID-1` | Evidence sufficiency | composite coverage must reach the rated minimum, else listed unranked | min_composite_coverage=0.6 | `unrated: insufficient_evidence` |

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
| ceiling_a_below | 0.95 |

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
| `closed_unread` | share of external PRs closed unmerged with no substantive response ever recorded >= share_min | above | share_min=0.3 |
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
| 2026-10-17 | RFC-0010 | D-GATE-5 author self-close exclusion (RFC-0008 rule 2) applies only to a close within `GATE5_RESPONSE_DEADLINE_DAYS` (14) of clock start; a later self-close with no response counts as unanswered (owner ruling 2026-10-17). No constant added or changed, so the hash does not move. Not measurable on the recorded data (no close actor recorded). | `6e4f3e8e1d0c0fcd829d9c8c386a65179eadb5f936595f96e9eba98712d55b94` -> `6e4f3e8e1d0c0fcd829d9c8c386a65179eadb5f936595f96e9eba98712d55b94` |
| 2026-10-14 | RFC-0008 | Add `GATE5_WILSON_Z = 1.6448536269514722` (D-GATE-5: 95% one-sided Wilson lower confidence bound on zero-response share, plus 5 false-positive reduction rules: draft exclusion/ready clock start, author self-close exclusion, <90d account exclusion, maintainer close within 14d as response). No rank movement on the fixture (no `pr_window` envelopes in its corpus). | `aca719a922264ba5edb6adcdab6c0c08eaff7a315d0e5818a567509e5b0d3ba9` -> `6e4f3e8e1d0c0fcd829d9c8c386a65179eadb5f936595f96e9eba98712d55b94` |
| 2026-10-13 | RFC-0007 | Add `GATE6_HANDOFF_MIN_PRS = 5`, `GATE6_HANDOFF_MIN_SHARE = 0.5`, `GATE6_HANDOFF_NOTICES` and `GATE6_HANDOFF_RELAY_LOGIN_PREFIXES` (D-GATE-6: a repository whose own notices send outside PRs to another review system, e.g. Gerrit or a mailing list, is routed off-platform and is not failed at D-GATE-5). No rank movement on the fixture (no `pr_window` envelopes in its corpus); on the recorded data six repositories hand off. | `aa6c4cd6f702fdc738cafb68022eb2613491657a9cb74c180fc2a1bd1236e662` -> `aca719a922264ba5edb6adcdab6c0c08eaff7a315d0e5818a567509e5b0d3ba9` |
| 2026-10-12 | RFC-0006 | Add `("downloads_monthly", "ecosystem", "go")`, `("downloads_monthly", "ecosystem", "maven")`, and `("downloads_monthly", "ecosystem", "systems")` to `NOT_APPLICABLE_RULES` (D-MISS-5: ecosystems with no public monthly-downloads registry). | `748be6e459b0d9eac279233dac75acd5ede7da9711590c84fd0317a84762cb6b` -> `aa6c4cd6f702fdc738cafb68022eb2613491657a9cb74c180fc2a1bd1236e662` |
| 2026-09-13 | RFC-0004 | Relocate D-IN-3's `MIN_ACCOUNT_AGE_DAYS = 90` / `MIN_PUBLIC_EVENTS = 5` / `AUTHOR_CAP_FRACTION = 0.20` out of `porchlamp.collect.github_graphql` into `porchlamp.core.constants_v2` / `porchlamp.core.hygiene` (W3-1). No rank movement (no scoring-path consumer yet). | `419091af843a5ab994b23a5776c0f0ce9073ed283b9f540b1d26eca7bcd4d7a9` -> `748be6e459b0d9eac279233dac75acd5ede7da9711590c84fd0317a84762cb6b` |
| 2026-09-13 | RFC-0003 | Add `NOT_APPLICABLE_RULES` (D-MISS-5's closed not_applicable rule list), `OPENSSF_PROXY_MIN_SIGNALS = 8` (row 2.2's `proxy:no_dependents_signal` minimum), and relocate `WINDOW_STALENESS_DAYS = 14` from `porchlamp.collect.pike_signals` (W1-7f follow-up). No rank movement on the fixture (0 rated repos; `records_hash` moves only from the new hashed constants and `PillarScore.applicable`'s structural addition). | `446c6a25db4e8c1f9d528f98a332d52bdf5ba570f07774bd674d9c816fdd09d0` -> `419091af843a5ab994b23a5776c0f0ce9073ed283b9f540b1d26eca7bcd4d7a9` |
| 2026-09-13 | RFC-0002 | Relocate the five `STARS_SCOUT_*` universe-entry thresholds (D-IN-5 star-velocity filter, W1-10) from the unhashed `porchlamp.constants` into `porchlamp.core.constants_v2`; values unchanged. No rank movement (D-IN-5: stars never enter the composite). | `d2702ea93a7ad9eb22696942dd3994d63f066ff2bf2e68e52abfc8235433570e` -> `446c6a25db4e8c1f9d528f98a332d52bdf5ba570f07774bd674d9c816fdd09d0` |
| 2026-09-13 | RFC-0001 | Add `AUTHOR_UNMEASURED_MAX_FRACTION = 0.20` (D-MISS-1 collector-failure gate for row 1.2/1.3, W1-4 round-2 review B-2/B-3); relocated out of `porchlamp.collect.github_graphql`, closing the census escape. No rank movement (no scoring-path consumer yet). | `391d10b91e86a230fa8c16e32f57f2bc5d0449c33dc3bcb6c3f96153ef83fed8` -> `d2702ea93a7ad9eb22696942dd3994d63f066ff2bf2e68e52abfc8235433570e` |
| 2026-09-13 | genesis | Initial v2 constants freeze (packet W2-8 round 2, `d7664f2`): relocated every scored tunable into `porchlamp.core.constants_v2` and computed `CONSTANTS_HASH` for the first time. No RFC — the procedure and this changelog begin governing changes only after this commit. | *(none)* -> `391d10b91e86a230fa8c16e32f57f2bc5d0449c33dc3bcb6c3f96153ef83fed8` |
