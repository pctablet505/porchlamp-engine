<img src="web/v2/favicon.svg" width="72" height="72" alt="Porchlamp's symbol: a porch lamp with its light on">

# Porchlamp

**Find the projects that leave the light on.**

A porch lamp left on tells a visitor they are expected. Porchlamp ranks
open-source projects the same way: by what happens to a stranger's pull
request, not by how many stars the project has.

## Stars don't review pull requests

Measured on the 2026-09-23 snapshot of 5,755 projects:

- **About half of active projects are black holes.** Of the 2,714 licensed,
  maintained software projects that received at least ten outside pull requests
  in six months, 1,337 (49.3%) left most of them with no human reply, review or
  merge for two weeks. Counting only pull requests opened by people, not bots,
  gives 49.9%.
- **More stars, more silence.** The black-hole share rises with popularity: 44%
  of projects with 1k–10k stars, 53% at 10k–50k, and 66% at 50k or more.
- **Stars don't predict the ranking.** Across the 1,315 ranked projects the rank
  correlation between stars and the Porchlamp score is −0.04, and 4 of the 100
  most-starred projects make the top 100.

## Where the numbers come from

We followed 113,140 pull requests from 61,513 outside contributors to 5,755
projects over six months: who got an answer, how fast, how many reached a
decision, whether first-time contributors came back, and how much other software
depends on each project. Black holes, and projects that are archived,
unmaintained, unlicensed or not software, are listed but never ranked. The rest
are scored on five pillars and re-weighted for five kinds of contributor. Every
number traces back to a recorded GitHub response and its content hash.

## How the ranking works

The Porchlamp platform scores open-source repositories on **five evaluation pillars** —
Responsiveness (25%), Production Criticality (25%), Governance & Safety (20%),
Engineering Rigor & AI Alignment (15%), Accessibility & DX (15%) — through an
explicit 5-phase pipeline:

1. **Stage 0: EBA hard gates** — 180-day maintenance vitality, external PR ghost
   rate (GR_14) ≤ 50% (Black Hole), hard-AI-ban vs AI-assisted persona, and the
   spec-§4 `senior_systems` multi-vendor constraint (Elephant Factor ≥ 3);
   off-platform review workflows (e.g. `torvalds/linux`) are routed, not failed.
2. **Phase 1: anti-gaming de-biasing** — bot/service-account stripping from
   review latency, StarScout star-fraud filtering (throwaway accounts, lockstep
   burst rings), dependency sanitization.
3. **Phase 2: within-ecosystem ECDF normalization** — raw signals mapped to
   [0, 100] percentiles per ecosystem (empty population → neutral 50.0),
   neutralizing npm vs Crates/Go scale skew.
4. **Phase 3: multi-pillar scoring** — the 16 calibrated parameters of spec §3
   combined by Weighted Additive Model (no rank reversal).
5. **Phase 4/5: 2D matrix + persona projection** — pillar scores decompose into
   **Leverage** (Y: criticality/rigor/governance) and **Friction**
   (X: responsiveness/governance/accessibility) coordinates (≥ 50.0 counts as
   high on that axis), classify the repo into one of four quadrants
   (**Sweet Spot / Crucible / Nursery / Graveyard**), and the composite is
   projected under five persona weight presets (balanced, first-timer, career
   capital, senior systems, AI-assisted).

The scored index is served over REST: a 2D discovery matrix with quadrant count
badges, per-repo scorecard deep-dives, and dynamic persona re-projection with
rank-shift deltas. Full specification: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
(HLD + LLD); UI/UX: [docs/UI_UX_SPECIFICATION.md](docs/UI_UX_SPECIFICATION.md).

## Quickstart

```bash
cd /home/pctablet505/Projects/contributor-value-index

# venv: .venv — Python 3.12 with pytest + pytest-xdist, fastapi + httpx and
# uvicorn installed (pyproject extras: `dev` = test deps, `server` = uvicorn).
# Gotcha: some shells export PYTEST_ADDOPTS (here observed `-n 6`, previously
# `-n 6 -n 6`), which pollutes the explicit `-n auto` — always run pytest with
# it unset:
env -u PYTEST_ADDOPTS .venv/bin/python -m pytest tests/ -n auto -p no:cacheprovider
```

On 2026-09-23 the suite ran 2,774 passed, 1 skipped (a manual procedure) and
1 xfailed (a documented reproject limitation). CI runs on Python 3.12.

## Module Map

| Module | Role |
|---|---|
| `porchlamp/__init__.py` | Package entry: pure-stdlib scoring core (no I/O) |
| `porchlamp/models.py` | Data contracts: `RawPREvent`, `OpenSSFSignals`, `RepoMeta`, `PorchlampCompositeResult`, `PillarScores`, `Stage0Result`, plus all enums |
| `porchlamp/constants.py` | Every tuning constant: pillar/parameter weights, thresholds, tier cutoffs, persona presets, OpenSSF α/T tables, bot markers |
| `porchlamp/stage0.py` | Phase 0 EBA gates: maintenance, ghost-rate Black Hole, AI ban, `senior_systems` multi-vendor EF ≥ 3 (off-platform = routing, not failure) |
| `porchlamp/starscout.py` | Phase 1 StarScout fraud filter: throwaway accounts + 100:1 lockstep burst rings |
| `porchlamp/normalize.py` | Phase 2 ECDF percentile rank (empty population → neutral 50.0) |
| `porchlamp/openssf.py` | Rob Pike OpenSSF criticality C (10 signals, log saturation, clamped [0, 1]) |
| `porchlamp/pillars/__init__.py` | Pillar package: one calibrated `score_*` entry point per module ([0, 100]) |
| `porchlamp/pillars/responsiveness.py` | Pillar 1: bot-filtered TTFHR, first-timer merge rate, GR_14, TTM_p90 tail risk |
| `porchlamp/pillars/criticality.py` | Pillar 2: dependents, OpenSSF C, filtered downloads (ECDF of log10(x+1)) |
| `porchlamp/pillars/governance.py` | Pillar 3: legal IP regime (DCO/CLA), Elephant Factor, promotion ladder |
| `porchlamp/pillars/rigor.py` | Pillar 4: Scorecard pipeline, persona-conditional AI posture, review-depth Gaussian |
| `porchlamp/pillars/accessibility.py` | Pillar 5: unclaimed-GFI freshness, 90-day newcomer return rate, CI/DX friction |
| `porchlamp/matrix.py` | Phase 4 leverage/friction decomposition + quadrant classification (≥ 50.0 = high) |
| `porchlamp/persona.py` | Phase 5 persona weight presets, custom-weight `reproject`, `rank_deltas` |
| `porchlamp/engine.py` | `score_repository`: wires the 5-phase pipeline; tier assignment; DI seam `pillar_scorers` |
| `porchlamp/lakehouse.py` | `OperationalStore` (SQLite `repositories` table per spec 11.2) + matrix/leaderboard/quadrant queries |
| `porchlamp/api.py` | FastAPI app (`create_app`): matrix, leaderboard, repo deep-dive, reproject, healthz |
| `porchlamp/collect/` | Collectors: one network fetch per signal (GitHub GraphQL, commits, governance, labels, deps.dev, ecosyste.ms), each returning a content-hashed envelope (`envelope.py`); rate limiting, ETag delta-hydration and circuit breaking in `ratelimit.py` |
| `porchlamp/deepdive.py` | Spec-12.2 deep-dive payload: raw metrics, Kaplan–Meier survival curve, legal-IP/EF/GFI detail, and Maintainer Improvement Playbook |
| `tests/fixtures/synthetic_catalog.py` | Synthetic 1,000-repository benchmark catalog across 6 ecosystems and 4 quadrants, with a store seeder, for tests (was `benchmark_data.py`) |
| `web/index.html` | High-density developer UI: Light/Dark mode, 2D matrix, ranked leaderboard, side-by-side comparator, scorecard drawer |
| `scripts/demo_app.py` | Demo server: seeds 1,000 benchmark repos by default; UI at `http://127.0.0.1:8000/`, API under `/v1/` |

## API Reference

Base URL: `http://127.0.0.1:8000` (FastAPI app from `porchlamp/api.py: create_app`).

**`GET /healthz`** — liveness probe.

```bash
curl http://127.0.0.1:8000/healthz
# {"status":"ok"}
```

**`GET /v1/porchlamp/leaderboard`** — paginated benchmark ranked leaderboard.

Query params: `ecosystem` (optional), `persona` (default `balanced`), `min_stars` (default `0`), `limit` (default `100`, max `1000`), `offset` (default `0`), `search` (fuzzy name/owner match).

```bash
curl "http://127.0.0.1:8000/v1/porchlamp/leaderboard?limit=10&ecosystem=pypi"
# {"total":175,"page":1,"limit":10,"items":[{"rank":1,"name":"transformers",...}]}
```

**`GET /v1/porchlamp/matrix`** — scatter nodes + quadrant count badges.

Query params: `ecosystem` (optional), `domain` (reserved, no-op), `persona`
(reserved, no-op — 2D coordinates are persona-invariant; default `balanced`,
unknown values are a 422), `min_stars` (default **100**, inclusive
`stars >= min_stars`), `exclude_black_holes` (default **true**).
`quadrant_counts` reflects the ecosystem-filtered table (no `min_stars`/gating
applied), so filter badges show totals.

```bash
curl "http://127.0.0.1:8000/v1/porchlamp/matrix?ecosystem=pypi&min_stars=0"
# {"nodes":[{"repo_id":"owner/name","owner":...,"name":...,"x":friction,
#  "y":leverage,"porchlamp":...,"tier":...,"quadrant":...,"size":stars,
#  "is_gated":0}, ...],
#  "quadrant_counts":{"Sweet Spot":n,"Crucible":n,"Nursery":n,"Graveyard":n}}
```

**`GET /v1/porchlamp/repos/{owner}/{name}`** — full stored row plus nested
`pillar_scores` (the five pillar keys) for radar charts; 404 JSON body when the
repo is missing.

```bash
curl http://127.0.0.1:8000/v1/porchlamp/repos/vllm-project/vllm
```

**`POST /v1/porchlamp/reproject`** — re-rank every repo under custom weights.

Body: `{"weights": {responsiveness, criticality, governance, rigor,
accessibility}}` — exactly the 5 pillar keys, summing to **1.0 within 1e-6**,
validated **before** any store access (invalid payloads get a 422 JSON error
without touching the DB). Response: re-ranked array of
`{repo_id, owner, name, old_rank, new_rank, delta, new_score, x, y, quadrant}`,
where `delta = old_rank − new_rank` (positive = moved up) and ties are broken
by position in the old ranking.

```bash
curl -X POST http://127.0.0.1:8000/v1/porchlamp/reproject \
  -H 'Content-Type: application/json' \
  -d '{"weights":{"responsiveness":0.35,"criticality":0.20,"governance":0.15,"rigor":0.15,"accessibility":0.15}}'
```

## Demo

```bash
cd /home/pctablet505/Projects/contributor-value-index
.venv/bin/python scripts/demo_app.py
# → http://127.0.0.1:8000   (demo page at /, Porchlamp API under /v1/)
# PORT=8765 ... to override the port; --db PATH for a file-backed store
```

The demo seeds 26 fictional, fully deterministic repositories (six ecosystems,
all four quadrants, one gated black-hole repo, two off-platform repos) with
pillar scores pinned through the `pillar_scorers` DI seam, so the stage-0
gates and the Phase-4/5 math run for real. The UI is a 2D scatter of the
discovery matrix (leverage vs friction, quadrant shading, gated-repo markers)
with a deep-dive scorecard drawer per node and a persona re-projection panel
that POSTs custom weights to `/v1/porchlamp/reproject` and renders rank-shift
deltas.

## Testing Philosophy

- **Oracle-first:** expected values are hand-computed from the spec formulas
  (exact decimals, boundary cases, clamps) — no snapshot tests, so a wrong
  formula fails loudly instead of matching itself.
- **Dependency-injection seams:** `porchlamp.engine.score_repository` accepts
  `pillar_scorers` (partial dict keyed by pillar name) to swap any pillar
  scorer; `porchlamp.api.create_app(store=...)` injects any `OperationalStore`;
  `porchlamp.ingest` takes injectable httpx transports, clocks, and jitter for
  deterministic tests.
- **Mutation battery protocol:** each mutant is a surgical single-line change
  (e.g. `log10(c+1)` → `log10(c+2)`, `fork_ratio > 100` → `>= 100`) that a
  specific oracle test must kill. Batteries run on isolated `/tmp` copies of
  the repo, record the original bytes, and write-back/verify restoration after
  each mutant — never `git checkout` or any restore of the working tree.

## Known Spec Inconsistencies

Documented; where engine semantics diverge from the spec text, the engine is
authoritative:

- **vllm ground truth:** composite 88.8 → **Tier S** in the engine (the S+
  cutoff is ≥ 90.0 and 88.8 < 90.0), although spec §14 labels it "Tier S+".
- **keras-hub ground truth:** 46.8 is the *pre-gate* composite; as a Stage-0
  Black Hole the repo is gated to `overall_porchlamp = 0.0` / **Tier D** / GRAVEYARD
  in the engine.
- **46.8 → Tier C**, not Tier B (the B cutoff is ≥ 55.0).
- **Tier cutoffs are an implementation decision:** 90/80/70/55/40 select
  S+/S/A/B/C (below 40 → D); the spec names the tiers but never fixes numeric
  cutoffs.
- **Spec audit result: 0 blockers / 9 minor / 1 cosmetic.** Known wiring
  gaps — StarScout is not wired into the engine pipeline (standalone module);
  Phase-1 dependency sanitization (root-org grouping of dependents) is not
  implemented (dependents enter the ECDF as raw counts); and the spec-§4
  persona-specific Stage-0 constraint rows for first-timer ("Low-Barrier
  Only") and career-capital ("High Tier Brand Only") are not implemented (the
  senior-systems multi-vendor EF ≥ 3 gate is, as gate 4b).
- **Parameter count:** spec §3's title and WAM formula say "15 parameters",
  but its table defines 16 (Pillar 1 has four); the engine implements all 16.

## License

This project is licensed under the Apache 2.0 License. See [LICENSE](LICENSE) for details.
