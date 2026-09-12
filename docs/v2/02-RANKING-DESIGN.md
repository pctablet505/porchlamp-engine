# Ranking design v2 — the process, the parameters, their order, weights and tie-breakers

Decision ids (`D-*`) are stable; packets cite them. Rationale follows each
decision. Numbers in this document are decisions, not estimates, unless marked
"to be measured".

## 0. What the score means and the ordering principle

**D-DEF-1.** CVI estimates the expected value of one hour a contributor invests
in a repository, decomposed as

```
value ≈ P(decision) · P(merge | decision) · Impact(merge) · RightsRetained · Growth − OnboardingCost
```

Responsiveness measures `P(decision)` and its latency; the merge-rate family
measures `P(merge | decision)`; criticality measures `Impact`; governance
measures `RightsRetained` and `Growth`; accessibility measures `OnboardingCost`.

**D-DEF-2 (ordering principle).** Factors that multiply the value toward zero
are evaluated *before* and *above* factors that add to it. That is why
`P(decision)` is a non-compensatory gate (a repo that never answers has zero
value regardless of impact) and why responsiveness keeps the largest weight
among the friction terms. Within a pillar, parameters are ordered by how close
they sit to an outcome the contributor experiences (a decision, a merge, a
release) versus a declaration the maintainer can make for free (a label, a
sentence in CONTRIBUTING.md). Outcome-anchored parameters get the larger weight.

## 1. Stage order

**D-ORDER-1.** Fixed sequence per snapshot:

| Stage | Name | Output |
|---|---|---|
| 0 | Identity & eligibility gates (persona-independent) | `eligible`, `gate_reason`, `routed` |
| 1 | Input hygiene | bot-typed actors, eligible authors, windowed samples |
| 2 | Measurement | per parameter: `Measured(value, n, window, method, source)` or `Unmeasured(reason)` |
| 3 | Evidence gate | `rated` / `unrated` from coverage |
| 4 | Normalization | ECDF populations from rated repos only, frozen |
| 5 | Pillar aggregation | pillar score + pillar coverage |
| 6 | Composite | balanced CVI, coverage, tier with ceiling |
| 7 | Coordinates & quadrant | leverage, friction, quadrant |
| 8 | Persona gates & projections | `persona_gates`, per-persona composite |
| 9 | Ordering | strict rank after tie-breakers, `tie_group` |

Why this order: ECDF populations must be built from rated repos only (stage 4
after stage 3); otherwise black holes and unmeasured zeros define the
percentiles (this is what F3 does today). Persona gates come after scoring so
that a persona-gated repo keeps its balanced score and is hidden per view, not
zeroed.

## 2. Gates (stage 0)

All gates are evaluated; the *first* failing gate in this order is the reason.
A gate whose input is insufficient returns `UNDECIDED`, which never fails a
repo but is recorded.

| Id | Gate | Criterion | Outcome | Why |
|---|---|---|---|---|
| **D-GATE-1** | Software project | Repository tree contains a recognised build/package manifest (`pyproject.toml`, `setup.py`, `setup.cfg`, `package.json`, `Cargo.toml`, `go.mod`, `pom.xml`, `build.gradle*`, `CMakeLists.txt`, `Makefile`, `meson.build`, `configure.ac`, `*.cabal`, `mix.exs`, `Gemfile`) at depth ≤ 2, OR it is a curated anchor | Fail → **excluded from universe** (`non_software`) | Removes lists, courses, paper collections (the pollution named in the curation handoff) without hand curation |
| **D-GATE-2** | Not archived, not a mirror, not a fork | `archived == false`, `mirror_url is null`, `fork == false` (forks allowed only if the parent is absent from the universe) | Fail → gated `archived` / `mirror` / `fork` | Mirrors and archives accept no PRs; today they read as black holes (F11) |
| **D-GATE-3** | Licensed | SPDX id present and OSI-approved (`NOASSERTION`/`OTHER` → look for `LICENSE*` file; none → fail) | Fail → gated `unlicensed` | Contributing to unlicensed code is a legal trap; no pillar can compensate |
| **D-GATE-4** | Vitality | ≥ 1 commit by a non-bot `User` on the default branch in the trailing 180 d | Fail → gated `stale` | Spec gate 1, with bot typing from D-IN-1 |
| **D-GATE-5** | Not a black hole | Over external PRs *opened* in the trailing 180 d that are ≥ 14 d old (`n ≥ 10` required, else `UNDECIDED`): share with zero substantive human response within 14 d of opening ≤ 0.50 | Fail → gated `black_hole` | Spec gate 2 with a window denominator and a minimum n; fixes F4/F6 |
| **D-GATE-6** | PR workflow | ≥ 1 external PR decided in the trailing 365 d, OR off-platform markers present (`Documentation/process/submitting-patches*`, `MAINTAINERS` with `L:` mailing-list lines, README says patches go to a list/Gerrit/Phabricator) | Off-platform → **routed**, scored with responsiveness *unmeasured*; neither → `unrated: no_external_workflow` | Spec gate 3 made detectable |
| **D-EVID-1** | Evidence sufficiency | Composite coverage `C ≥ 0.60` (see D-MISS-3) | Fail → `unrated: insufficient_evidence` (listed, not ranked) | A rank built on 40% of the weight is noise presented as order |

Gated and unrated repos are stored and shown with their reason; they have no
rank and no tier.

## 3. Input hygiene (stage 1)

**D-IN-1 Bot identity.** An actor is a bot if GraphQL `__typename == "Bot"`, or
the login ends with `[bot]`, or the login is in an exact-match list kept in the
frozen constants (`dependabot`, `renovate`, `github-actions`, `codecov`,
`stale`, `google-cla`, `cla-bot`, `easycla`, `mergify`, `pre-commit-ci`,
`allcontributors`, `semantic-release`, `imgbot`, `netlify`, `vercel`,
`sonarcloud`, `snyk-bot`, `greenkeeper`, `copilot`, `claude`). Substring
matching is removed (F12).

**D-IN-2 Substantive human response.** A PR received a substantive response
when a non-author, non-bot account whose `authorAssociation` is `MEMBER`,
`OWNER` or `COLLABORATOR` posted a review (any state) or a comment of ≥ 40
characters after the author's own last comment or at any time. Reactions,
label changes and assignment do not count. Why: "thanks, will look" from a
maintainer is a response; a bot's "CLA signed" is not; a drive-by comment from
another outsider is not a maintainer decision signal.

**D-IN-3 Eligible author.** For rate parameters (merge, decision, conversion,
return) a PR author counts only if the account is ≥ 90 days old at PR creation
and has ≥ 5 public events, mirroring StarScout's throwaway rule. Per-author cap:
one author contributes at most 20% of any rate's numerator and denominator
(excess PRs from one author are dropped, most recent first). Why: sock-puppet
first-timers are the cheapest way to inflate merge and return rates.

**D-IN-4 Windows and caps.**

| Input | Window | Cap | Note |
|---|---|---|---|
| Pull requests (all states) | opened in trailing 180 d | 500 newest, `truncated` flag stored | uniform window replaces "30 newest" (F4) |
| Decision-rate cohort | opened in [now−270 d, now−90 d] | – | every PR in the cohort has had 90 d to be decided |
| Newcomer cohorts | first PR in [now−365 d, now−180 d] | 20 authors sampled per repo | gives 180 d to convert and 90 d to return |
| Commits | trailing 365 d (EF, bus factor), trailing 180 d (vitality) | 1,000 | email domains collected |
| Issues with newcomer labels | open, updated in trailing 90 d | 200 | label aliases per repo (D-PARAM 5.1) |
| CI runs | last 30 completed runs on the default branch | 30 | `run_timing.runDurationMs` only |
| Stargazers | trailing 90 d | – | universe entry only (StarScout) |

**D-IN-5 StarScout scope.** Stars never enter the composite. StarScout runs
once at universe entry (stars-based criteria) and sets `stars_audited`; the UI
`size` uses audited stars.

## 4. Parameter set v2 (stage 2)

18 parameters. Columns: within-pillar weight, global weight, minimum n
(below it the parameter is `Unmeasured(insufficient_n)`), curve, missingness
class (`TP` third-party gap → unmeasured; `RC` repo-controlled → measured zero;
`N` needs minimum sample).

### Pillar 1 — Responsiveness (25%)

| Id | Parameter | Definition | w | global | min n | Curve | Class |
|---|---|---|---|---|---|---|---|
| 1.1 | TTFHR | median hours from opening to first substantive human response (D-IN-2), external PRs in window | .30 | 7.5 | 10 | 100 if ≤ 12 h; else `max(0, 100 − 25·log2(t/12))` | N |
| 1.2 | DR_90 decision rate **(new)** | share of external PRs in the decision cohort that were merged or closed within 90 d | .25 | 6.25 | 10 | `min(100, 125·r)` | N |
| 1.3 | MR_first | merged / decided among external PRs whose author had no prior merged PR in the repo (author history from W1-4) | .20 | 5.0 | 5 | `min(100, 125·r)` | N |
| 1.4 | GR_14 ghost rate | share of external PRs in window, ≥ 14 d old, with no substantive human response within 14 d | .15 | 3.75 | 10 | `max(0, 100 − 200·g)` | N |
| 1.5 | TTM_p90 | nearest-rank p90 of merge latency (h) over merged external PRs in window; ecosystem ECDF | .10 | 2.5 | 5 | `100 − ECDF_mid(p90)` | N |

Why DR_90 is new and above MR_first: a decision (even a rejection with
feedback) is the outcome the contributor is owed; merge rate alone rewards
repos that only decide the easy PRs. Why TTFHR keeps the top weight: it is the
best-supported predictor of newcomer retention in the literature the spec
cites, and with a 180-day window it has enough samples (F4).

### Pillar 2 — Criticality (25%)

| Id | Parameter | Definition | w | global | min n | Curve | Class |
|---|---|---|---|---|---|---|---|
| 2.1 | Dependents | count of dependent packages, deduplicated by root organisation, excluding dependents owned by the repo's own org | .40 | 10.0 | – | `ECDF_mid(log10(1+d))` | TP |
| 2.2 | OpenSSF criticality | Pike score computed locally from the 10 signals (all derivable: age, last update, contributors, org count from email domains, commit frequency, releases/yr, closed and updated issues 90 d, comments per issue, dependents) | .35 | 8.75 | signals complete | `100·C` | TP (only if a signal is missing) |
| 2.3 | Downloads | monthly registry downloads | .25 | 6.25 | – | `ECDF_mid(log10(1+m))` | TP (maven, systems: unmeasured) |

Systems ecosystem: dependents and downloads have no registry. Packet W1-2
measures repology package-count as a proxy; until measured, criticality for
systems is OpenSSF criticality only and coverage reflects it.

### Pillar 3 — Governance (20%)

| Id | Parameter | Definition | w | global | min n | Curve | Class |
|---|---|---|---|---|---|---|---|
| 3.1 | Legal IP | evidence-based regime: **DCO** = DCO check run on recent PRs or ≥ 50% of window commits carry `Signed-off-by`; **FOUNDATION_CLA** = EasyCLA/ASF/Eclipse/CNCF CLA check or owner in the foundation list; **CORP_CLA** = cla-assistant/google-cla/other CLA bot present; **NONE** = no CLA and no DCO evidence | .35 | 7.0 | – | DCO 100 · NONE 85 · FOUNDATION_CLA 75 · CORP_CLA_RECIPROCAL 40 (curated override only) · CORP_CLA 25 | RC |
| 3.2 | Elephant factor | orgs by commit-author email domain over 365 d; personal domains (gmail, outlook, proton, users.noreply…) count as one org each per author; EF = min orgs reaching 50% of commits | .35 | 7.0 | 30 commits | `min(100, 25·EF)` | N |
| 3.3 | Promotion ladder | **VERIFIED** (100): ≥ 1 account whose association moved external → MEMBER/COLLABORATOR within 24 months (observable in the PR window); **DOCUMENTED** (75): GOVERNANCE.md / MAINTAINERS.md / CONTRIBUTING.md names a maintainer nomination process; **AD_HOC** (40): none; **CLOSED** (10): every merged PR in window authored by one org and no external merges | .30 | 6.0 | – | enum | RC |

Why NONE scores 85 (owner decision O-1): with no CLA the inbound licence equals
the outbound one and relicensing needs every contributor's consent, the same
protection DCO gives; DCO adds provenance, so it stays above. Today 99% of repos
default to DCO=100 (F7).

### Pillar 4 — Rigor (15%)

| Id | Parameter | Definition | w | global | min n | Curve | Class |
|---|---|---|---|---|---|---|---|
| 4.1 | Pipeline defenses | when Scorecard exists: mean of checks {Code-Review, CI-Tests, Branch-Protection, Maintained, Dangerous-Workflow} × 10; when absent: proxy = 0.6·code-review rate (merged PRs in window with ≥ 1 approving review by a non-author) ×100 + 0.4·CI pass rate on the default branch ×100 (0 if no CI); `method` stored | .40 | 6.0 | 10 merged PRs (proxy) | linear | TP with proxy |
| 4.2 | AI policy posture | REGULATED (policy file or `Assisted-by`/`AI-generated` trailer convention), PROSCRIBED (explicit ban sentence in a policy file), AD_HOC otherwise; persona-conditional scores as spec | .30 | 4.5 | – | REGULATED 95/95 · PROSCRIBED 25 (AI) / 85 (human) · AD_HOC 40 | RC |
| 4.3 | Substantive review rate **(replaces the Gaussian)** | share of decided external PRs that received ≥ 1 review with inline comments or a review body ≥ 80 chars | .30 | 4.5 | 10 | `min(100, 125·r)` | N |

Why the Gaussian goes: a curve that penalises "too much review" is not
monotone, cannot be improved by doing the right thing past 3.5 comments, and
was fed by issue-comment totals (F8). A monotone rate is Goodhart-safer and
measurable from review threads.

### Pillar 5 — Accessibility (15%)

| Id | Parameter | Definition | w | global | min n | Curve | Class |
|---|---|---|---|---|---|---|---|
| 5.1 | Newcomer-issue supply | open, unassigned issues carrying a newcomer label (canonical: `good first issue`, `help wanted`, plus per-repo aliases matching `/good.?first|beginner|easy|newcomer|first.?timers?|help.?wanted|starter/i` from the repo's label list), updated within 90 d; capped | .35 | 5.25 | – | `min(100, 10·count)` | RC |
| 5.2 | Newcomer conversion **(new)** | share of newcomer-cohort authors (D-IN-4) whose first PR was merged within 180 d | .35 | 5.25 | 5 authors | `min(100, 200·r)` | N |
| 5.3 | RR_90 return rate | of newcomer-cohort authors with a merged first PR, share with a second PR within 90 d | .15 | 2.25 | 5 authors | `min(100, 200·r)` | N |
| 5.4 | DX | 0.5·runtime score (median of last 30 CI runs: 100 if ≤ 20 min, −5/min after, floor 0; unmeasured if no `run_timing`) + 0.5·setup signals (devcontainer or Dockerfile or flake.nix; CONTRIBUTING.md with a setup/development section; README with install/build section: 0/1/2/3 → 0/40/70/100) | .15 | 2.25 | 5 runs | as stated | RC (signals), TP (runtime) |

Why conversion outranks return: a newcomer whose first PR is merged got the
value; returning is a second-order effect and needs the most expensive
collection.

**D-PARAM-MONO.** Every curve is monotone non-decreasing in "better for the
contributor" over its domain. A property test asserts it for every parameter
(packet W2-3). Non-monotone curves are not admitted.

## 5. Missingness (stages 2–3)

**D-MISS-1 Taxonomy.** Every parameter value is one of:
- `Measured(value, n, window_start, window_end, method, source_url, fetched_at)`
- `Unmeasured(reason)` with `reason ∈ {third_party_gap, collector_failure,
  insufficient_n, not_applicable}`.

Repo-controlled absences are **measured zeros**: no newcomer labels → 5.1 = 0;
no CI workflows → CI pass rate 0; no policy file → AD_HOC. Third-party gaps
(OpenSSF never scanned, registry has no downloads, deps.dev has no data) and
collector failures are **unmeasured**. Why: a maintainer must be able to
improve every measured zero; nobody can improve a third party's coverage, and
scoring it as worst (today) punishes the wrong actor, while scoring it neutral
rewards hiding.

**D-MISS-2 Renormalisation.** Pillar score = Σ w_i·s_i over measured
parameters / Σ w_i over measured parameters. Pillar coverage c_p = Σ w_i
(measured) / Σ w_i (all). If no parameter in a pillar is measured, the pillar is
unmeasured and drops out of the composite the same way.

**D-MISS-3 Coverage and ceilings.** Composite coverage `C = Σ_p W_p·c_p` under
the active weights. `C < 0.60` → unrated. `0.60 ≤ C < 0.80` → tier ceiling B.
`0.80 ≤ C < 0.95` → ceiling A. `C ≥ 0.95` → no ceiling. Coverage is stored and
displayed next to every score. Why ceilings: a repo cannot be certified elite
on partial evidence, and the ceiling gives the maintainer and the third parties
a reason to close the gap.

**D-MISS-4 Proxies.** A parameter may define one proxy method (4.1 does). The
`method` field records which was used; proxies never mark `Measured` as
`verified` in provenance, they are `derived`.

## 6. Normalization (stage 4)

**D-NORM-1 Mid-rank ECDF.** `R(x) = 100 · (#{x_k < x} + 0.5·#{x_k = x}) / N`.
Why: the spec's `≤` form gives every member of an all-tied population 100
(F3); mid-rank gives 50 and is the standard tie treatment.

**D-NORM-2 Population.** Per ecosystem, over rated repos for which the parameter
is measured, at the same snapshot; frozen and stored with the snapshot
(`populations` table). Minimum population 30, else fall back to the global
population and flag `population_fallback`.

**D-NORM-3 Parameters normalised by ECDF.** 1.5, 2.1, 2.3 only. All others use
absolute calibrated curves (a maintainer target that does not move when the
universe changes).

## 7. Aggregation and composite (stages 5–6)

**D-AGG-1 Pillar weights.** Balanced: responsiveness .25, criticality .25,
governance .20, rigor .15, accessibility .15 (mandated by R2; unchanged in v2;
owner decision O-2 for any v3 change). Persona presets as in the spec §4.

**D-AGG-2 Composite.** `CVI = Σ_p W_p·pillar_p` over measured pillars,
renormalised as in D-MISS-2, rounded to 2 decimals after all arithmetic.

**D-AGG-3 Sensitivity requirement.** Each version publishes a sensitivity
report (packet W0-5): Kendall τ between the canonical ranking and each ranking
under ±5 pp perturbation of one pillar weight (redistributed proportionally).
Release requires τ ≥ 0.90 for every perturbation among rated repos; otherwise
the ranking is too weight-sensitive to be called a standard.

**D-AGG-4 Persona projection is linear.** Per-persona composites are computed in
SQL from stored pillar scores and per-pillar coverage; no Python full scan.

## 8. Coordinates, quadrants, tiers (stage 7)

**D-MAT-1.** Leverage `= .50·crit + .30·rigor + .20·gov`; friction
`= .50·(100−resp) + .30·(100−gov) + .20·(100−access)`, using renormalised pillar
scores; unmeasured pillars drop out of the coordinate with the same
renormalisation. Quadrant threshold 50.0, `≥` counts as high (documented
convention kept). Coordinates carry their own coverage.

**D-MAT-2 Tiers.** Cutoffs 90/80/70/55/40 on the composite (kept, O-5), then
the coverage ceiling from D-MISS-3 is applied: `tier = min(tier_by_score,
ceiling)`. Gated and unrated repos have no tier.

## 9. Personas and persona gates (stage 8)

**D-PERS-1 Separation.** `is_stage0_disqualified` is persona-independent and
comes only from §2. Persona gates are stored as `persona_gates = {persona:
reason}` and applied as filters in persona views; they never change the stored
balanced score.

**D-PERS-2 Definitions.**

| Persona | Gate | Definition |
|---|---|---|
| ai_assisted | Hard AI ban | `ai_posture == PROSCRIBED` with `ai_policy_evidence` non-empty |
| senior_systems | Multi-vendor | EF ≥ 3 (measured) |
| first_timer | Low barrier | (decided first-timer PRs in window ≥ 5 and MR_first ≥ 0.40) or 5.1 count ≥ 3 |
| career_capital | High-tier brand | criticality pillar ≥ 60 and its coverage ≥ 0.80 |

The two previously undefined gates now have concrete semantics; they are
filters (D-PERS-1), so a wrong threshold hides rather than zeroes.

## 10. Ordering and tie-breakers (stage 9)

**D-TIE-1.** Rated repos are ordered by composite (2 decimals) descending, then
by the following, in order:

1. coverage `C` descending (more evidence wins: rewards transparency, never
   hiding);
2. responsiveness pillar descending (the contributor-first pillar, D-DEF-2);
3. friction ascending;
4. leverage descending;
5. TTFHR p75 ascending (consistency, not just the median);
6. date of last non-bot commit descending;
7. lowercase `owner/name` ascending (deterministic last resort).

Stars, forks and downloads are never tie-breakers (vanity and gameable).

**D-TIE-2.** Rank is strict 1..N after tie-breaking; rows that tie on the
composite share a `tie_group` id so the UI can mark them. Gated and unrated
repos are listed after rated ones, unranked, ordered by name.

## 11. Versioning and reproducibility

**D-VER-1.** `benchmark_version` (semver): weights or curve constants change →
minor; parameter set, gates or missingness policy change → major; collector
fix that changes values → patch with a re-snapshot.

**D-VER-2.** All constants live in one frozen module; `constants_hash =
sha256(canonical JSON)` is written into every stored row and the snapshot
manifest. A test asserts the hash matches the value recorded for the current
version.

**D-VER-3.** A snapshot is `(as_of, manifest_root, benchmark_version,
constants_hash, code_commit)`. Scoring reads `as_of` from the snapshot; no
`datetime.now()` in scoring code (test-enforced). `cvi reproduce <snapshot>`
must yield a byte-identical database.

**D-VER-4 Anchors.** Relational, computed from real data at every version:
`torvalds/linux` routed off-platform; `gcc-mirror/gcc` gated `mirror`; a chosen
archived repo gated `archived`; an `awesome-*` list `non_software`;
`kubernetes/kubernetes` and `vllm-project/vllm` rated with `C ≥ 0.90` and tier
≥ A; the DCO anchor `torvalds/linux` legal = DCO; any repo whose newcomer-label
count is ≥ 1 on GitHub scores 5.1 > 0. `keras-team/keras-hub`'s gate status is
reported, not asserted.

## 12. Validation (owner decision O-6)

**D-VAL-1 Forward outcome experiment.** At the first v2 snapshot `T`, freeze
the ranking. At `T + 90 d`, collect for every rated repo the external PRs
opened in `(T, T + 60 d]` by eligible first-timer authors. Labels: decision
within 30 d; merge within 90 d. Report Spearman correlation and AUC between
each pillar / the composite computed at `T` and the label rates. Success
criteria are stated before collection: responsiveness pillar Spearman ≥ 0.40
with the decision label; composite Spearman ≥ 0.30 with the merge label. A
failed criterion is published as such and drives v2.1 weights.

## 13. Universe (owner decision O-4)

**D-UNI-1.** v2.0 universe = the 5,753 candidates minus D-GATE-1 exclusions.
**D-UNI-2.** v2.1 entry criteria = union of top-N by audited stars per
language (current), top-N by monthly downloads per registry, top-N by
dependents per registry, plus anchors; every candidate records its
`selected_by` criteria. Selection bias is documented on the methodology page:
percentiles are relative to this universe.

## 14. What changed versus the spec, and why

| Spec | v2 | Reason |
|---|---|---|
| 30 newest PRs | 180-day window, cap 500 | F4 |
| Ghost rate over open PRs, no minimum | windowed denominator, n ≥ 10, `UNDECIDED` below | F6 |
| `≤` ECDF | mid-rank ECDF over rated & measured population | F3 |
| Missing → 0.0 or 50 by accident | taxonomy + renormalisation + coverage ceilings | F8, Q1 |
| Substring bot list | typed actors + exact list | F12 |
| Review-depth Gaussian | substantive review rate (monotone) | D-PARAM-MONO |
| MR_first with `prior_merged` never derived | author history sampled; DR_90 added | F5 |
| RR_90 over the same sample | newcomer cohorts with conversion + return | F5 |
| GFI label AND-query | alias OR-queries, 90-day freshness | F2 |
| Elephant by login | by email-domain org | F9 |
| Legal/ladder/AI by keywords with best-case defaults | evidence-based with NONE/AD_HOC defaults | F7 |
| No mirror/archive/licence/software gates | D-GATE-1..3 | F11, pollution |
| Ties by `(owner, name)` | D-TIE-1 hierarchy with coverage first | 118 distinct values / 981 rows |
| Persona gate mutates `is_stage0_disqualified` | separate `persona_gates` | Q3 |
| Pinned numeric anchors | relational anchors | D-VER-4 |
