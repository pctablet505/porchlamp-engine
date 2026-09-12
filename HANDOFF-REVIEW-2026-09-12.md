# CVI — Comprehensive Handoff for External Review
**Date:** 2026-09-12 · **Repo:** `/home/pctablet505/Projects/contributor-value-index` · **HEAD:** `3691421` (pushed to `origin/main`) · **Python:** 3.12 (`.venv`) · **Tests:** 853 test functions / 45 files, full suite exit=0

**Purpose of this document:** a self-contained brief for a reviewing model that will (a) audit the design and implementation, (b) assign ~3 months of follow-on work, and (c) answer open questions where owner sign-off is required. Every claim below is tagged **[VERIFIED]** (reproducible evidence exists) or **[CLAIMED]** (asserted but not independently proven).

---

## 1. Problem Statement

Rank open-source repositories by **contributor value** — how good a repo is *as a place to contribute* — not by popularity. The original codebase shipped with ~900 fabricated benchmark repos (`cvi/benchmark_data.py`: hand-typed tuples with pillar scores pinned backwards to force desired quadrants/tiers). The redo mandate:

1. Build a **criteria-based universe** (top-N per criterion/ecosystem → union, 1k–10k repos).
2. Gather data from **independent sources**, cross-verify, and enforce: **only verified data enters SQLite**.
3. Score through the spec pipeline and serve results through a store-backed API + web UI, decoupled from synthetic fixtures.

## 2. Solution Design (as implemented)

### 2.1 Five-phase pipeline (spec: `docs/ARCHITECTURE.md`)
```
Candidate → Phase 0 EBA gates → Phase 1 bot-strip/StarScout → Phase 2 within-ecosystem ECDF
→ Phase 3 five-pillar WAM → Phase 4 2D matrix (leverage × friction) → Phase 5 persona projection
→ CVI 0-100, Tier A-D, Quadrant {Sweet Spot, Crucible, Graveyard, Nursery}
```

**Phase 0 gates** (`cvi/stage0.py`, order 1→2→4→4b→3, first failure wins) **[VERIFIED]**:
- G1 maintenance: ≥1 non-bot commit in trailing 180d.
- G2 black hole: 14-day external-PR ghost rate ≤ 0.50.
- G4 persona: hard AI ban vs AI-assisted persona; G4b senior_systems requires elephant_factor ≥ 3.
- G3 off-platform reviews: *routing*, not disqualification.

**Pillar weights** (`cvi/constants.py`, all transcribed from spec): responsiveness .25, criticality .25, governance .20, rigor .15, accessibility .15. Within-pillar params, matrix weights (leverage = .50 crit + .30 rigor + .20 gov; friction = .50 resp + .30 gov + .20 access), tier cutoffs, StarScout thresholds, and `BOT_LOGIN_MARKERS` all live in the same file.

### 2.2 Data plane (the redo)
```
build_universe.py → data/raw/candidates.jsonl        (5,753 candidates, 6 ecosystems)
gather.py         → data/raw/<collector>/<owner__name>.json  (envelope: fetched_at, source, data)
derive.py         → data/derived/<key>.json          (bundle: meta, pr_events, confidence, provenance)
verify.py         → data/verified/<key>.json         (bundle + cross-source report {checks, flags, clean})
promote.py        → data/cvi.db                      (CLEAN only; two-pass ECDF populations; deep-dive materialized)
run_all.py        → per-repo gather→derive→verify, checkpointed resume; promotes once at end (non-skip pass)
serve.py          → production entrypoint: opens store, no seeding, mounts web UI at /
```
Sources: GitHub REST via `gh` CLI (5k/hr), deps.dev + securityscorecards.dev (keyless), per-ecosystem registries (npm bulk, crates.io UA-required, pypistats ~10/min, Maven/Go have no download counts).

### 2.3 Verification gate semantics (post-fix, current)
- **stars/forks**: GitHub (live) vs deps.dev (cache). Monotonic counters → flag **only** cache > live by >10% (impossible direction). Cache-below = expected staleness. **[VERIFIED: 13/14 observed mismatches were viral repos with cache<live]**
- **scorecard**: **advisory, recorded but non-gating** — deps.dev's `scorecard_overall` is its own cache of the *same* OpenSSF tool the bundle fetches live; disagreement is one tool's cache drift, not a two-source contradiction.
- **license**: GitHub vs deps.dev SPDX (case/underscore-normalized), skipped for NOASSERTION/OTHER.
- **required-confidence**: `stars`/`forks` must be `verified` in the bundle.
- `clean = not gating_flags`.

### 2.4 Serving plane
- `cvi/api.py`: FastAPI, all endpoints `async def`, single shared `OperationalStore` on `app.state`. Endpoints: `/healthz`, `/v1/cvi/matrix` (nodes + quadrant_counts), `/v1/cvi/leaderboard` (page/page_size/offset, ecosystem, persona re-projection, search), `/v1/cvi/repos/{o}/{n}` (row + pillar_scores + optional nested `deep_dive` from `deep_dive_json`), `POST /v1/cvi/reproject` (custom weights, validated 422 before store touch).
- `cvi/lakehouse.py`: `OperationalStore` (SQLite, 26 spec columns + additive `deep_dive_json` via idempotent `ensure_deep_dive_column`). Leaderboard selects explicit `_COLUMNS` (never the blob); persona re-projection recomputes CVI/tier/gating in Python over all matching rows.
- `web/index.html`: single-file UI (dual theme, matrix scatter, leaderboard, deep-dive panels incl. survival curve + maintainer playbook, comparator).

## 3. Implementation Status

| Component | State | Evidence |
|---|---|---|
| Universe build | ✅ DONE | 5,753 candidates; checkpointed; `.git`-suffix bug fixed |
| gather/derive/verify/promote | ✅ DONE | e2e smoke on real repos; promote 3,170 repos to temp DB |
| Scoring engine | ✅ DONE | discriminates on real data: CVI stdev 31.9, median 56.8; tiers A396/B1278/C206/D1290; quadrants Sweet1681/Grave1298/Cruc166/Nurs25 **[VERIFIED on 3,170 real repos]** |
| Deep-dive payloads | ✅ DONE | 3,170 scanned: 0 anomalies (monotonic survival curves, potential_cvi ≤ 100, no NaN) **[VERIFIED]** |
| API + UI on real data | ✅ DONE | live curl round-trip incl. reproject (esphome rank 22→2 under responsiveness weighting) **[VERIFIED]** |
| Test suite | ✅ 853 tests, exit=0 **[VERIFIED]** | one wall-clock SLA test flaked under load; since recalibrated by parallel agent (`fae92cd`) |
| **Full-universe sweep** | 🔄 IN FLIGHT | 3,658/5,753 derived (~20/min, ETA ~100 min at time of writing); checkpointed |
| **Downloads backfill** | ⏳ PENDING | 2,818 of 3,615 cached downloads are null (pypistats ~4/min is the long pole; pypi slice alone could be ~12h) |
| **Final `data/cvi.db`** | ⏳ PENDING | after backfill: `verify.py --all` → `promote.py` (auto at end of non-skip pass) |

### Key empirical findings **[VERIFIED]**
- **40.7% of top-starred repos fail Stage 0** (1,290/3,170): 1,283 genuine black holes (ghost>50% with real PR traffic), 7 genuinely stale (e.g. `wg/wrk` last commit 2021, confirmed against GitHub). Headline: *popular ≠ maintainer-friendly*.
- Verified-clean rate after verifier fixes: 2,998/3,012 (99.5%). Remaining flags: 13 transient GitHub-fetch misses (self-heal on rerun) + 1 real anomaly (`iperov/DeepFaceLab` deps.dev stars 46,651 > GitHub 19,284 — impossible direction; likely wrong repo mapping in deps.dev).
- OpenSSF has genuinely not scanned ~39% of the universe (live 404s confirmed on a sample) — see Q1.

## 4. Verified vs Claimed (honesty ledger)

**[VERIFIED]** — everything in §3 tables marked so; plus: all 9 of this session's fixes have fail-before regression tests (each confirmed to fail with the fix reverted); ECDF inclusivity/empty/monotonicity locked by `tests/test_normalize.py`; promote→serve→UI round-trip on 3,170 real repos; all API endpoints `async def` (shared sqlite conn never leaves the event-loop thread).

**[CLAIMED]** — needs external scrutiny:
1. **StarScout anti-gaming** (`cvi/starscout.py`, 56 lines): implemented per spec but **never exercised on real burst-farming data**; thresholds (500-star burst, 100× fork ratio) are spec-transcribed, not calibrated.
2. **Persona re-projection correctness** for non-balanced personas: unit-tested, but persona *semantics* diverge from Stage-0 (see Q3).
3. **Kaplan-Meier survival curve** implementation: deterministic + shape-tested; not validated against a reference KM implementation.
4. **Playbook ROI deltas** (linear additivity of per-action `delta_cvi`): plausible heuristic, not a model; interactions between actions ignored.
5. **ECDF populations from the verified universe itself**: self-referential (percentile within the scored set). Fine for ranking, but "top 5% of repos" claims are universe-relative — the universe is top-starred repos only, so cross-ecosystem comparisons inherit selection bias.
6. **`deep_dive_json` staleness**: materialized at promote time; no invalidation if raw data changes later.
7. **SLA numbers** (p50<25ms etc.): machine-specific, single box, no warm-up protocol.
8. **Bot stripping**: `BOT_LOGIN_MARKERS` substring match is spec-defined but over-matches (see Q2).

## 5. Known Issues & Deferred Decisions (owner sign-off required)

1. **Sentinel inconsistency (largest scoring distortion):** missing `scorecard_score`/`active_unclaimed_gfis`/`rr_90` default to **0.0 = worst**, while missing `dx_ci_minutes` defaults to **neutral 50**. Since OpenSSF hasn't scanned ~39% of the universe, those repos get a spurious "worst security posture" on the rigor pillar (pipeline = scorecard/10×100 → 0). Options: (a) neutral-50 for genuinely-absent telemetry, (b) renormalize pillar weights over observed params, (c) keep worst-default as a conservative "unknown is guilty" stance. **This is a value judgment, not a bug — pick deliberately.**
2. **`BOT_LOGIN_MARKERS` substring over-match:** `"stale"` matches `Staley`, `"actions"` matches any login containing it, `"claude"` matches human named Claude. Spec-defined; changing it shifts scores universe-wide.
3. **Persona-gate conflation:** leaderboard AI-persona gating uses `ai_posture == PROSCRIBED`, but Stage-0 gate 4 uses `hard_ai_ban` (no DB column). A repo with `hard_ai_ban=True, ai_posture=AD_HOC` is gated by the engine but not the leaderboard, and vice versa. The re-projection path also *overwrites* `is_stage0_disqualified` with persona-gate status (two meanings, one field).
4. **`matrix.decompose` has no clamp** on pillar-score overload (DI seam exists); quadrant assignment on exactly-50.0 coordinates is tie-broken by convention.
5. **OpenSSF zero-denominator → 0.0 (worst)** in the criticality engine — same value-judgment family as #1.
6. **Load-flaky SLA tests**: recalibrated to p50 + 100ms headroom; still machine-specific.
7. **`RETURNING id`** requires SQLite ≥ 3.35 (undocumented requirement).
8. **`SELECT *` in `get_repo`** intentionally pulls the blob (only consumer that needs it) — fine, but note it.

## 6. Refactoring Guidance (where this codebase will hurt in 6 months)

1. **`cvi/lakehouse.py` (622 lines) is a god-module**: schema + writes + queries + persona re-projection + seeding + deep-dive storage. Split into `schema.py` / `writer.py` / `query.py` (or adopt a thin repository-pattern layer). The persona re-projection currently loads *all* matching rows into Python — fine at 3k, wrong at 100k; design the SQL/window-function path now.
2. **Two parallel ingestion stacks**: `cvi/ingest.py` (669 lines, the original fetcher, never run in the redo) vs `scripts/gather.py` (the one actually used). Pick one; delete or absorb the other. Same for `scripts/demo_app.py` (synthetic seeding) vs `scripts/serve.py` (production) — demo_app is now a fixture generator, rename or fold into tests.
3. **Scripts-as-modules**: `verify.py`/`promote.py`/`build_universe.py` are loaded via `importlib.util.spec_from_file_location` in tests. Promote them to a real `cvi.pipeline` package (or `cvi/data/`) with proper imports; the current pattern hides circular-import risk and breaks static analysis.
4. **Bundle schema is implicit dicts** (`meta`/`pr_events`/`confidence`/`provenance`). Define pydantic/dataclass models with versions (`schema_version` field) and validate at load; today a derive-bug that drops a key fails late or silently defaults.
5. **`web/index.html` is a ~3.3k-line single file** (UI + styles + logic). If the UI grows further, extract to modules (no framework needed) and add DOM-level tests; `tests/test_webapp.py` currently string-matches HTML.
6. **Config drift**: `BENCHMARK_NOW` duplicated in `playbook.py`/`benchmark_data.py`; `0.50` black-hole threshold hard-coded in playbook instead of importing `GHOST_RATE_GATE`; `_ghost_rate_14` imported cross-module as a private. Centralize.
7. **No migration story** for the SQLite schema (additive `ALTER TABLE` only, hand-rolled). Adopt a migration tool before the schema changes a third time.
8. **Provenance is stored but not surfaced**: `data/raw` envelopes carry `fetched_at`/`source`, bundles carry per-field provenance — the API/UI expose none of it. "Show me why this score" is the natural next feature and the data already exists.

## 7. Better-Approach Candidates (for the reviewer to arbitrate)

1. **Verification model**: current = pairwise tolerances between two snapshots. Alternative = explicit *staleness-aware* reconciliation: store `observed_at` per source, treat disagreement as a function of (Δt, growth rate), and emit a *confidence* rather than a boolean. The monotonic-counter insight (this session's asymmetric fix) is the first step; generalize it.
2. **Downloads long pole**: pypistats ~4/min dominates backfill (~12h). Options: (a) accept and run overnight, (b) drop pypi downloads to ECDF-neutral (treat as missing → see Q1), (c) switch to PyPI BigQuery public dataset for bulk. Reviewer's call given the 3-month horizon.
3. **Universe selection bias**: candidates are top-starred/registry-top. Delisted/abandoned-but-usable repos are invisible. If the product question is "where should I contribute", the current universe is defensible; if it's "how healthy is open source", it is not.
4. **Scorecard as advisory**: correct for *verification*, but the *scoring* side still worst-defaults missing scorecards (Q1). Consider replacing the scorecard pipeline sub-score with a repo-observable proxy (branch protection via GitHub API, required reviews) that exists for 100% of repos.
5. **Persona re-projection in SQL** (window functions + per-persona weight columns) instead of Python full-table scans.
6. **Idempotent promote with content-hash**: rows keyed by (owner,name) upsert; no way to tell which rows reflect which raw-data vintage. Add `raw_data_hash` + `promoted_at` per row; enables incremental re-promote and staleness display.
7. **CI**: ruff gate-1 exists; add the full pytest suite + a nightly "re-verify N random repos against live sources" canary (catches source drift like the deps.dev anomaly automatically).

## 8. Questions for the Reviewing Model (answer these before assigning work)

1. **Missing-telemetry policy** (biggest scoring decision): neutral-50, weight-renormalization, or worst-default for absent scorecard/GFI/RR? Affects ~39% of repos on rigor, likely 10-30% on accessibility. What's the principled choice, and should it be persona-dependent?
2. **Bot markers**: keep spec substring list or move to exact-suffix/regex matching (`-bot$`, `[bot]$`)? Universe-wide score impact either way.
3. **Persona gating**: unify on `hard_ai_ban` (add DB column) or on `ai_posture`? Should persona gating mutate `is_stage0_disqualified` or a separate `persona_gated` field?
4. **Is the 40.7% Stage-0 disqualification rate a *finding to publish* or a *problem to fix*?** (i.e., does the product surface black holes prominently, or should the default view exclude them?)
5. **Universe scope**: freeze at 5,753 top-repos, or expand toward the original 1k–10k criteria union including non-top repos? What criteria would the reviewer add?
6. **Ground truth**: the original spec had pinned anchors (e.g. `torvalds/linux` → gated). What *computed* anchors should exist now? (Suggest: linux→gated, kubernetes→Tier A, a known-black-hole→Tier D, hashbrown→Tier A.) Which repos would the reviewer nominate as validation fixtures?
7. **Validation of the index itself**: CVI has no outcome label. Candidate proxies: contributor-retention (RR_90 is internal), future star growth, PR-merge-rate for *new* contributors. Which would the reviewer design as the 3-month validation experiment?
8. **Scale target**: is 10k repos the ceiling, or should the architecture target 100k+ (decides the SQL re-projection refactor priority)?
9. **Deep-dive freshness**: promote-time snapshot vs on-demand rebuild vs TTL — which serving model?
10. **What is the ship definition?** (public URL? paper? dataset release?) — determines whether SLA/UX or data-quality work dominates the 3 months.

## 9. Suggested 3-Month Skeleton (for the reviewer to overwrite)

- **Month 1 — correctness & trust:** resolve Q1–Q3; sentinel policy + re-promote; provenance surfaced in API/UI; bundle schema models + versioning; ingestion-stack consolidation; nightly re-verify canary.
- **Month 2 — validation & scale:** ground-truth anchor suite (Q6); outcome-proxy experiment (Q7); SQL persona re-projection; universe expansion decision (Q5); downloads backfill strategy (Q2 of §7).
- **Month 3 — product surface:** deep-dive freshness model (Q9); UI modularization; migration tooling; load testing at target scale (Q8); release packaging (dataset + API + UI).

## 10. Reproduction Commands

```bash
cd /home/pctablet505/Projects/contributor-value-index
# tests (canonical; result line can hide under warnings — redirect and check exit):
env -u PYTEST_ADDOPTS .venv/bin/python -m pytest tests/ -n auto -p no:cacheprovider -q
# lint gate:
.venv/bin/ruff check cvi/ scripts/ tests/
# sweep progress:
ls data/derived | wc -l        # of 5,753;  tail -1 /tmp/sweep.log
# after sweep: re-verify + promote (new code applies advisory/asymmetric fixes + deep-dive):
.venv/bin/python scripts/verify.py --all
.venv/bin/python scripts/promote.py --db data/cvi.db
# serve production UI on promoted store:
.venv/bin/python scripts/serve.py --db data/cvi.db --port 8123
```

## 11. Session Commit Ledger (all pushed)

| Commit | What | Fail-before test |
|---|---|---|
| `8adf8dc` | ttm_p90 corrupt-negative-latency guard; add `test_normalize.py` | 2 tests |
| `81c1321` | `serve.py` production entrypoint (no synthetic seed) | `test_serve.py` (4) |
| `d0e8724` | scorecard cross-check → advisory | updated `test_verify.py` |
| `c749c69` | strip `.git` from crates.io repo URLs | `test_crates_top_strips_git_suffix` |
| `efde108` | stars/forks gate: flag only cache>live (monotonic insight) | 2 tests |
| `a738f81` | playbook black-hole double-count (baseline must be 0) | 1 test |
| `c6f0ebf` | ecosystem filter LOWER() across all 3 endpoints | cross-endpoint test |
| `0749e6d` | escape LIKE `%`/`_` in search | wildcard test |
| `469c0a8` | promote materializes spec-12.2 deep-dive | `test_promote.py` (2) |
| `07bde2c` | leaderboard explicit-column SELECT (no blob reads) | blob-exclusion test |

Parallel agent (same window, integrated): m1 playbook engine + lakehouse optimizations, m2 leaderboard/deep-dive API, m3 dual-theme UI + comparator, m4 tier-5 hardening + SLA suites, `fae92cd` SLA recalibration.

---
*Prepared by the working agent. Every [VERIFIED] tag has a reproducible command or test in §10/§3; challenge any of them.*
