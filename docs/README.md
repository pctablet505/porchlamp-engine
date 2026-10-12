# Porchlamp Documentation Index

This directory contains the canonical v2 specification, generated methodology reference, architecture guide, RFC change log, and historical v1 baseline specifications for **Porchlamp** (`contributor-value-index`).

---

## 1. Canonical v2 Reference (Active)

| Document | Purpose |
|---|---|
| [`METHODOLOGY-v2.md`](METHODOLOGY-v2.md) | **Code-generated v2 methodology.** Rendered directly from `porchlamp.core.constants`, `porchlamp.core.params`, `porchlamp.verify.detectors`, and `docs/v2/rfcs/CHANGELOG.md` via `scripts/render_methodology.py` (enforced byte-for-byte by `tests/test_render_methodology.py`). |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | **Visual overview of v2:** pipeline, package boundaries, scoring, matrix and personas, with the v1 prototype design kept as an appendix. |
| [`v2/00-README.md`](v2/00-README.md) | **Master v2 design index & decision ledger.** Index of all `D-*` design decisions (`D-GATE-*`, `D-EVID-*`, `D-MISS-*`, `D-NORM-*`, `D-PERS-*`, `D-TIE-*`, `D-VER-*`). |
| [`v2/02-RANKING-DESIGN.md`](v2/02-RANKING-DESIGN.md) | Mathematical specification of the 6 hard gates, 18 parameters across 5 pillars, ECDF & spline normalisation, coverage ceilings, 2D matrix, and 5 persona lenses. |
| [`v2/03-ANTI-MANIPULATION.md`](v2/03-ANTI-MANIPULATION.md) | Bot stripping (`D-IN-1`), substantive response filter (`D-IN-2`), author hygiene & sybil resistance (`D-IN-3`), and gaming detectors (`porchlamp/verify/detectors.py`). |
| [`v2/04-ARCHITECTURE.md`](v2/04-ARCHITECTURE.md) | Detailed package boundaries, `RawEnvelope` and `FeatureBundle` schemas, SQLite migrations, and static export parity verification. |
| [`v2/06-UX-DESIGN.md`](v2/06-UX-DESIGN.md) | Specification for the `web/v2/` interface (Leaderboard, 2D Matrix, Compare, Configure Weights, Methodology) and `web/static/api.js`. |
| [`v2/07-BFS-COLLECTION-TASK.md`](v2/07-BFS-COLLECTION-TASK.md) | Universe discovery and multi-layer collection orchestration (`L1..L4, LK`). |
| [`v2/08-SUSTAINABLE-COLLECTION.md`](v2/08-SUSTAINABLE-COLLECTION.md) | Termux / Android phone relay architecture (`phone/`), adaptive rate limiting, and HMAC-authenticated LAN transfer (`scripts/pull_from_phone.py`). |

---

## 2. Governance, RFCs & Empirical Validation (`docs/v2/`)

| Path | Purpose |
|---|---|
| [`v2/rfcs/README.md`](v2/rfcs/README.md) | Procedure for modifying frozen scoring constants (`CONSTANTS_HASH` & `BENCHMARK_VERSION` governance). |
| [`v2/rfcs/CHANGELOG.md`](v2/rfcs/CHANGELOG.md) | Chronological log of approved RFCs and `CONSTANTS_HASH` transitions. |
| [`v2/01-FINDINGS-2026-09-12.md`](v2/01-FINDINGS-2026-09-12.md) | Audit of the v1 prototype that motivated the v2 redesign. |
| [`v2/05-WORK-PLAN.md`](v2/05-WORK-PLAN.md) | Phased engineering implementation record (`P0`–`P6`) and test oracle rules. |
| [`v2/09-AUDIT-TRIAGE-2026-09-17.md`](v2/09-AUDIT-TRIAGE-2026-09-17.md) | Deep-review triage and resolution record for v2 release readiness. |
| [`v2/validation/`](v2/validation/) | Snapshot validation reports and empirical audit outputs. |

---

## 3. Animations (`docs/assets/animations/`)

Three GIFs illustrate the architecture; the Markdown stays the source of truth. They are rendered from `~/Projects/animation-studio-work/porchlamp/animations.html`, which lives outside this repository (`render.py --repo porchlamp`).

- [`anim-1-snapshot-pipeline.gif`](assets/animations/anim-1-snapshot-pipeline.gif): collection to the published site
- [`anim-2-gates-and-pillars.gif`](assets/animations/anim-2-gates-and-pillars.gif): Stage 0 gates, pillars and coverage ceilings
- [`anim-3-matrix-and-personas.gif`](assets/animations/anim-3-matrix-and-personas.gif): leverage × friction matrix and persona lenses

Agents: the GIFs are excluded via `.claudeignore`; read the Markdown instead.

---

## 4. Historical v1 Baseline Specifications

These files document the original v1 prototype (retained for historical reference alongside the `/legacy/` API compatibility layer):

- [`BENCHMARK_SPECIFICATION.md`](BENCHMARK_SPECIFICATION.md) — v1.0.0 16-parameter mathematical specification
- [`UI_UX_SPECIFICATION.md`](UI_UX_SPECIFICATION.md) — v1.0.0 frontend specification
- [`REVIEW_GUIDELINES.md`](REVIEW_GUIDELINES.md) — Code and methodology review checklist
