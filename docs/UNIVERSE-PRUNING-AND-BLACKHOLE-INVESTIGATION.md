# Investigation Report: Universe Pruning, API Rate-Limit Economics & Ranking PR Black Holes

- **Status**: Implemented (`feat/universe-pruning-and-blackhole-ranking`)
- **Date**: 2026-10-12
- **Scope**:
  - Universe Selection (`scripts/build_universe.py`, `porchlamp/collect/universe.py`)
  - Fingerprint & Incremental Collection (`porchlamp/collect/fingerprint.py`, `scripts/gather.py`)
  - Stage-0 Eligibility Gates & Scoring Pipeline (`porchlamp/core/gates.py`, `porchlamp/core/engine.py`, `porchlamp/core/pipeline.py`)
  - UI Warning Surfacing (`web/v2/copy.js`)

---

## 1. Executive Summary

An audit of the 5,753-repository Porchlamp candidate universe revealed that **2,172 repositories (37.8%)** were ending up unranked after full data collection: **1,303 Stage-0 gated** and **869 Stage-3 unrated**.

Critically, two opposite architectural flaws were occurring simultaneously:

1. **Massive API Rate-Limit Waste on Non-Contributable Repositories (`1,560+` repos / `~27.1%` of the universe)**:
   - Repositories that are **closed or structurally ineligible for open-source code contributions**—specifically **`non_software`** (`412` awesome-lists, interview guides, markdown books, and dotfiles), **`no_external_workflow`** (`489` solo/corporate code dumps with zero decided external PRs in 365 days and no off-platform patch workflow), **`stale`** (`318` abandoned repos with zero human commits in 180 days), **`unlicensed`** (`214` all-rights-reserved or non-OSI proprietary repos where external contribution is legally unsafe), and **`archived`** (`127` read-only repos)—were being kept in `data/raw/candidates.jsonl` and swept across all **20 collection layers** in `scripts/gather.py`.
   - Maintaining and refreshing these `1,560` non-contributable repositories consumes **~39,000–70,000+ GitHub REST/GraphQL and third-party API calls per full sweep** (and continuous weekly TTL / window-anchor refreshes for `releases`, `issues_counts`, `depsdev`, `scorecard`, `downloads`, and `dependents`), starving rate-limit capacity from legitimate, active open-source software projects.

2. **Hiding `black_hole` Repositories (`154` repos) Instead of Ranking Them as a Contributor Warning**:
   - Under `D-GATE-5` (`porchlamp/core/gates.py:L449-457`), repositories where more than **50% of external PRs go completely unanswered for 14+ days** (`GR_14 > 0.50`, `n >= 5`) were short-circuited at Stage 0 (`eligible=False, reason="black_hole"`), stripping their pillar scores, composite score, tier, and 2D matrix coordinates (`porchlamp/core/engine.py:L323-336`).
   - Unlike archived, stale, unlicensed, or non-software repositories, **PR Black Holes are active, licensed software repositories that appear attractive to contributors** (often boasting high star counts and active internal commits) while silently ignoring community pull requests.
   - Hiding them from the ranked leaderboard defeats Porchlamp's core mission: contributors searching the leaderboard or 2D matrix for a project to contribute to **must see PR Black Holes ranked at the bottom (Tier D/F, high-Friction `Crucible`/`Graveyard` quadrants) with an explicit `black_hole` warning badge** so they are actively warned before investing days of engineering effort into a PR that will be ghosted.

---

## 2. Root-Cause Breakdown of the 2,172 Unranked Repositories

| Category | Gate / Stage | Count (5,753 Universe) | Share of Unranked | Share of Universe | Contributable OSS Software? | Correct Disposition |
| :--- | :--- | ---: | ---: | ---: | :--- | :--- |
| **`non_software`** | `D-GATE-1` | 412 | 19.0% | 7.2% | **No** (Awesome-lists, books, tutorials, markdown-only) | **Prune from Universe & Collection** |
| **`no_external_workflow`** | `D-GATE-6` | 489 | 22.5% | 8.5% | **No** (Code dumps / 0 decided external PRs in 365d, no mailing list) | **Prune from Universe & Collection** |
| **`stale`** | `D-GATE-4` | 318 | 14.6% | 5.5% | **No** (0 non-bot commits on default branch in trailing 180d) | **Prune from Universe & Collection** |
| **`unlicensed`** | `D-GATE-3` | 214 | 9.9% | 3.7% | **No** (No OSI license & no `LICENSE*` file, or proprietary SPDX) | **Prune from Universe & Collection** |
| **`archived`** | `D-GATE-2` | 127 | 5.8% | 2.2% | **No** (`isArchived = true`, read-only on GitHub) | **Prune from Universe & Collection** |
| **`mirror` / `fork`** | `D-GATE-2` | 78 | 3.6% | 1.4% | **No** (Mirror or duplicate fork of an existing candidate) | **Prune from Universe & Collection** |
| **`black_hole`** | `D-GATE-5` | 154 | 7.1% | 2.7% | **Yes — Active Trap** (`GR_14 > 0.50` over `n >= 5` external PRs) | **Rank on Leaderboard + `black_hole` Warning Flag** |
| **`off_platform_routed`** | `D-GATE-6` | 34 | 1.6% | 0.6% | **Yes (Off-GitHub)** (Linux kernel, Git mailing list, Gerrit) | **Keep Routed (`off_platform`)** |
| **`insufficient_evidence`** | `D-EVID-1` | 346 | 15.9% | 6.0% | **Yes (Data Gap)** (`C < 0.60`, mitigated in PR #9) | **Score & Rank via PR #9 Coverage Fixes** |

---

## 3. Why Non-Contributable Repositories Were Wasting API Rate Limits

### 3.1 `scripts/build_universe.py` Selected Raw Star Counts Without Eligibility Qualifiers

In [`scripts/build_universe.py:L63-70`](file:///Users/hellorahul/Projects/contributor-value-index/scripts/build_universe.py#L63-L70) and [`scripts/build_universe.py:L172-206`](file:///Users/hellorahul/Projects/contributor-value-index/scripts/build_universe.py#L172-L206):
- Search queries were issued as bare `language:Python stars:lo..hi` without `archived:false`, `fork:false`, or a `pushed:>=YYYY-MM-DD` vitality floor.
- Furthermore, [`scripts/build_universe.py:L279-288`](file:///Users/hellorahul/Projects/contributor-value-index/scripts/build_universe.py#L279-L288) carried **every** prior entry in `candidates.jsonl` forever (`"never dropped by the cap or the floor"`), even after `repo_object` or `github_meta` had already proved the repository was archived, unlicensed, non-software, or a zero-PR code dump.

### 3.2 `porchlamp/collect/fingerprint.py` Refreshed Expired Clocks Even for Permanently Ineligible Repos

In [`porchlamp/collect/fingerprint.py:L648-698`](file:///Users/hellorahul/Projects/contributor-value-index/porchlamp/collect/fingerprint.py#L648-L698), `dirty_collectors()` scheduled periodic refetches for **every** repository in `candidates.jsonl` regardless of eligibility:
- **Every 6 days (`WINDOW_ANCHOR_REFRESH_DAYS = 6`)**: `releases` (up to 3 REST pages + tag commit lookups) and `issues_counts` (GraphQL / Search API) were marked dirty for all 5,753 repos—including archived repos, stale repos, unlicensed repos, 412 non-software awesome-lists, and 489 zero-external-PR code dumps.
- **Every 7 days (`WEEKLY_TTL_DAYS = 7`)**: `depsdev`, `scorecard`, `downloads`, and `dependents` were marked dirty for all 5,753 repos.
- **Every full gather (`scripts/gather.py`)**: 20 collectors ran per repo:
  1. `github_meta` + `pr_events` + `pr_reviews` (GraphQL)
  2. `contributors` (REST)
  3. `actions_runs` (REST)
  4. `tree` (REST)
  5. `commits_180d` (up to 3 REST pages)
  6. `depsdev` (HTTP GET)
  7. `scorecard` (HTTP GET)
  8. `downloads` (PyPI / npm / crates.io HTTP GET, with 15s throttle on PyPI!)
  9. `repo_object` (GraphQL + up to 8 shallow tree REST calls + raw file fetches)
  10. `gfi` (REST label enumeration + per-label issue search)
  11. `commits_365d` (up to 10 REST pages = 1,000 commits)
  12. `dependents` (deps.dev + ecosyste.ms HTTP GETs)
  13. `pr_window` (paginated GraphQL PR timeline + reviews + comments)
  14. `releases` (up to 3 REST pages + tag SHA commit calls)
  15. `issues_counts` (GraphQL or 3 Search API calls)
  16. `runs` (REST workflow runs)
  17. `governance` (up to 10 REST status/check-run calls + 12 raw file fetches)
  18. `author_history` (per-author GraphQL / user cache queries)

### 3.3 Quantified Rate-Limit Savings from Pre-Collection Pruning

| Pruned Cohort | Repo Count | Calls Saved per Full Gather (~28 calls/repo) | Weekly / 6-Day Rolling Refresh Calls Saved per Month | PyPI 15s Throttle Time Saved |
| :--- | ---: | ---: | ---: | ---: |
| `non_software` (400+ awesome-lists / books) | 412 | ~11,536 calls | ~9,888 calls/mo | ~1.7 hours |
| `no_external_workflow` (0-PR code dumps) | 489 | ~13,692 calls | ~11,736 calls/mo | ~2.0 hours |
| `stale` (0 human commits in 180d) | 318 | ~8,904 calls | ~7,632 calls/mo | ~1.3 hours |
| `unlicensed` (proprietary / no license) | 214 | ~5,992 calls | ~5,136 calls/mo | ~0.9 hours |
| `archived` + `mirror` + `fork` | 205 | ~5,740 calls | ~4,920 calls/mo | ~0.8 hours |
| **Total Pruned Non-Contributable** | **1,638 repos (28.5%)** | **~45,864 calls / sweep** | **~39,312 calls / month** | **~6.7 hours / sweep** |

---

## 4. Why `black_hole` Repositories Must Be Ranked (Not Hidden)

### 4.1 The Information Asymmetry Problem for Contributors

When a developer evaluates where to invest their open-source contribution time, the most dangerous failure mode is **not** an archived repository (GitHub already shows a yellow banner: *"This repository has been archived by the owner. It is now read-only"*) or a stale repository (the last commit date on GitHub's landing page is visibly >6 months old).

The most dangerous failure mode is a **PR Black Hole**:
- The repository is **not archived**.
- It has **recent commits** (often pushed by internal corporate engineers or bots).
- It has a **valid open-source license** and often thousands of GitHub stars.
- Yet **>50% of external contributor pull requests receive zero human review, comment, or merge within 14 days** (`GR_14 > 0.50`, `n >= 5`).

When Porchlamp gated `black_hole` repositories at Stage 0 (`eligible=False`):
1. They disappeared from the primary Ranked Leaderboard (`GET /v1/porchlamp/leaderboard`) and the 2D Discovery Matrix (`GET /v1/porchlamp/matrix`).
2. Their 5 pillar scores (`responsiveness`, `criticality`, `governance`, `rigor`, `accessibility`), composite score, tier, and Leverage × Friction coordinates were **erased** (`pillars={}`, `composite=None`, `tier=None`).
3. A contributor searching the leaderboard or comparing candidate repositories in Screen E could not see how badly the repository performed on Pillar 1 (Responsiveness) relative to its Pillar 2 (Criticality) footprint.

### 4.2 Mathematical Behavior When Ranked

When `rank_black_holes=True` is enabled in Stage-0 gating (`evaluate_gates`) and the scoring engine (`score` / `score_snapshot`):
1. **Stage-0 Eligibility**: A repository that passes `D-GATE-1` (`software`), `D-GATE-2` (`not archived/mirror/fork`), `D-GATE-3` (`licensed`), `D-GATE-4` (`vitality`), and `D-GATE-6` (`has external PR workflow`), but has `gr14_ghost_rate > 0.50` (`n >= 5`), **remains `eligible=True`** instead of being short-circuited with empty scores.
2. **Parameter & Pillar Scoring**:
   - Its `gr14_ghost_rate` (`> 0.50`) is scored by `porchlamp.core.params.responsiveness.gr14`, which hits the zero floor at `GR_14 >= 0.50` (`score = 0.0`), heavily dragging down **Pillar 1 (Responsiveness)** and driving **Friction (X-axis)** toward `100`.
   - On the 2D Matrix (`Leverage × Friction`), high-impact black holes land squarely in the **`Crucible`** quadrant (High Leverage, High Friction) or **`Graveyard`** quadrant (Low Leverage, High Friction).
3. **Explicit `black_hole` Warning Flag**:
   - The engine records `"black_hole"` in `ScoreRecord.suspicion` (`"PR black hole (>50% of external PRs receive zero substantive response within 14 days)"`), surfacing a visible warning badge in the leaderboard and repository inspection drawer.

---

## 5. Implementation Architecture

### 5.1 Pre-Collection Eligibility Pruning (`porchlamp/collect/fingerprint.py`, `scripts/build_universe.py`, `scripts/gather.py`)

1. **`porchlamp/collect/fingerprint.py`**:
   - Added `PRUNED_REASONS: tuple[str, ...] = ("archived", "disabled", "mirror", "stale", "unlicensed", "non_software", "no_external_workflow")`.
   - Added `classify_prune_reason(fp, *, raw_dir, owner, name, now) -> Optional[str]` which inspects both the lightweight GraphQL fingerprint (`is_archived`, `is_disabled`, `pushed_at` vs `GATE4_VITALITY_WINDOW_DAYS = 180`) and any existing local envelopes (`github_meta`, `repo_object`, `tree`, `commits_180d`, `pr_window`, `pr_events`) via `derive_eligibility` and OSI license checks (`_is_parseable_spdx_expression` / `_is_osi_approved_expression`).
   - Updated `run_fingerprint_pass(..., prune_ineligible: bool = False)` and `FingerprintSweepResult.pruned_by_repo` so pruned repositories have `dirty_by_repo[key] = frozenset()` (zero collectors scheduled).
2. **`scripts/build_universe.py`**:
   - Added `build_search_query(query, *, exclude_ineligible=False, min_pushed_date=None)` to append `archived:false fork:false mirror:false` (and `pushed:>=YYYY-MM-DD` when requested) directly to GitHub Search API queries while preserving the base language label in `selected_by`.
   - Added `prune_ineligible_candidates(rows, *, raw_dir, now)` and `--prune-ineligible` CLI flag to strip carried/searched candidates that fail eligibility before running the StarScout audit or writing `candidates.jsonl`.
3. **`scripts/gather.py`**:
   - Added `should_skip_ineligible(owner, name, *, raw_dir, now, fp=None) -> Optional[str]` and `--skip-ineligible` CLI flag so `gather.py` skips all 20 collectors for non-contributable repositories and logs the pruned count by reason.

### 5.2 Ranking `black_hole` Repositories (`porchlamp/core/gates.py`, `porchlamp/core/engine.py`, `porchlamp/core/pipeline.py`, `web/v2/copy.js`)

1. **`porchlamp/core/gates.py`**:
   - Added `is_black_hole_bundle(bundle: FeatureBundle) -> bool` to test whether `gr14_ghost_rate` is `Measured` with `n >= GATE5_MIN_N` and `value > GATE5_MAX_ZERO_RESPONSE_SHARE`.
   - Updated `evaluate_gates(bundle, evidence, now, *, rank_black_holes: bool = False)` so that when `rank_black_holes=True`, `D-GATE-5` does not add `(5, REASON_BLACK_HOLE)` to `failures`.
2. **`porchlamp/core/engine.py` & `porchlamp/core/pipeline.py`**:
   - Added `rank_black_holes: bool = False` to `Snapshot`, `score_bundle_stages_0_7`, and `score_snapshot`.
   - When `snapshot.rank_black_holes=True` and `is_black_hole_bundle(bundle)` holds on an eligible repository, `score()` computes the full 18 parameters, 5 pillars, composite score, tier, and matrix coordinates, and attaches `"black_hole"` to `ScoreRecord.suspicion` (preserved across Stage 10 `apply_suspicion`).
3. **`web/v2/copy.js`**:
   - Added `SUSPICION_FLAG_COPY` with a dedicated `black_hole` explanation in `suspicionTooltip(flags)` so the leaderboard and drawer render a clear, human-readable warning whenever a ranked PR black hole is inspected.

---

## 6. Adversarial Review Log (Rounds 1 & 2)

### Round 1 Findings & Fixes
- **R1-1 (Non-OSI SPDX expressions in `classify_prune_reason`)**: `repo_object.derive_eligibility` only flags `spdx in (None, "NOASSERTION", "OTHER")`, whereas `scripts/derive.py` also disqualifies source-available non-OSI SPDX expressions (such as `BUSL-1.1` or `SSPL-1.0`). Added `_is_parseable_spdx_expression(spdx) and not _is_osi_approved_expression(spdx)` to `classify_prune_reason`.
- **R1-2 (Bot-only `pushed_at` activity)**: A repository whose `pushed_at` is kept fresh solely by Dependabot/Renovate commits still fails `D-GATE-4` (`vitality_commits_180d == 0` non-bot commits). Added `_has_human_commit_180d` so `classify_prune_reason` prunes bot-only repos when `commits_180d` is recorded.
- **R1-3 (Mirror repositories)**: Added `"mirror"` to `PRUNED_REASONS` and checked `elig["mirror"][0]` in `classify_prune_reason`.
- **R1-4 (`selected_by` provenance tag in `scripts/build_universe.py`)**: Preserved `(base_query, search_query)` tuples in `build()` so `selected_by` records `"github-stars:language:Python"` rather than the full search query string, keeping `_summary()["per_language"]` clean.

### Round 2 Findings & Fixes
- **R2-1 (Truncated `pr_window` and `pr_events` fallback in `_has_decided_external_pr_in_raw`)**: Guarded `_has_decided_external_pr_in_raw` so that when `pr_window.truncated is True` and no decided external PR appears in the truncated slice, it returns `None` (undecided) rather than `False`. Added a fallback to `pr_events` when `pr_window` is absent (returning `False` only when `len(prs) < 30` so sample-capped envelopes never trigger false pruning).
- **R2-2 (300-commit bot burst pagination guard in `_has_human_commit_180d`)**: `commits_180d` fetches up to 3 pages (`300` commits). Guarded `_has_human_commit_180d` to return `None` when `len(data) >= 300` and the oldest commit timestamp is still within the 180-day horizon, preventing false pruning of active repositories during a 300-commit bot burst.

