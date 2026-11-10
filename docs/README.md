# Porchlamp Documentation Index

This directory contains the canonical v2 specification, generated methodology reference, architecture guide, RFC change log, and historical v1 baseline specifications for **Porchlamp** (`porchlamp-engine`).

---

## 1. Canonical v2 Reference (Active)

| Document | Purpose |
|---|---|
| [`METHODOLOGY-v2.md`](METHODOLOGY-v2.md) | **Code-generated v2 methodology.** Rendered directly from `porchlamp.core.constants`, `porchlamp.core.params`, `porchlamp.verify.detectors`, and `docs/v2/rfcs/CHANGELOG.md` via `scripts/render_methodology.py` (enforced byte-for-byte by `tests/test_render_methodology.py`). |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | **Visual overview of v2:** pipeline, package boundaries, scoring, matrix and personas, with the v1 prototype design kept as an appendix. |
| [`v2/00-README.md`](v2/00-README.md) | **Master v2 design index & decision ledger.** Index of all `D-*` design decisions (`D-GATE-*`, `D-EVID-*`, `D-MISS-*`, `D-NORM-*`, `D-PERS-*`, `D-TIE-*`, `D-VER-*`). |
| [`v2/02-RANKING-DESIGN.md`](v2/02-RANKING-DESIGN.md) | Mathematical specification of the 6 hard gates, 18 parameters across 5 pillars, ECDF normalisation, coverage ceilings, 2D matrix, and 5 persona lenses. |
| [`v2/03-ANTI-MANIPULATION.md`](v2/03-ANTI-MANIPULATION.md) | Bot stripping (`D-IN-1`), substantive response filter (`D-IN-2`), author hygiene & sybil resistance (`D-IN-3`), and gaming detectors (`porchlamp/verify/detectors.py`). |
| [`v2/04-ARCHITECTURE.md`](v2/04-ARCHITECTURE.md) | Target package layout, the `Envelope` and `FeatureBundle` contracts, the store schema, queries, API v2 and the migration path. |
| [`v2/06-UX-DESIGN.md`](v2/06-UX-DESIGN.md) | Specification for the `web/app/` interface (Leaderboard, Not ranked, 2D Matrix, Repository drawer, Configure weights, Compare, Methodology). |
| [`v2/07-BFS-COLLECTION-TASK.md`](v2/07-BFS-COLLECTION-TASK.md) | Universe discovery and multi-layer collection orchestration (`L0..L4`). |
| [`v2/08-SUSTAINABLE-COLLECTION.md`](v2/08-SUSTAINABLE-COLLECTION.md) | Sweep-to-delta collection design (`D-COL-*`, wave W6). |

---

## 2. Governance, RFCs & Empirical Validation (`docs/v2/`)

| Path | Purpose |
|---|---|
| [`v2/rfcs/README.md`](v2/rfcs/README.md) | Procedure for modifying frozen scoring constants (`CONSTANTS_HASH` & `BENCHMARK_VERSION` governance). |
| [`v2/rfcs/CHANGELOG.md`](v2/rfcs/CHANGELOG.md) | Chronological log of approved RFCs and `CONSTANTS_HASH` transitions. |
| [`v2/01-FINDINGS-2026-09-12.md`](v2/01-FINDINGS-2026-09-12.md) | Audit of the v1 prototype that motivated the v2 redesign. Its section D answers the questions in the deleted `HANDOFF-REVIEW-2026-09-12.md` (`git show 70175fc:HANDOFF-REVIEW-2026-09-12.md`). |
| [`v2/05-WORK-PLAN.md`](v2/05-WORK-PLAN.md) | Waves and packets (`W0`–`W6`) and the test oracle rules. |
| [`v2/09-AUDIT-TRIAGE-2026-09-17.md`](v2/09-AUDIT-TRIAGE-2026-09-17.md) | Deep-review triage and resolution record for v2 release readiness. |
| [`v2/validation/`](v2/validation/) | Frozen pre-registration of the forward outcome experiment (`D-VAL-1`). |
| [`REVIEW_GUIDELINES.md`](REVIEW_GUIDELINES.md) | **Binding.** Definition of done and the review checklist for every change, human or agent. |

---

## 3. Historical v1 Baseline Specifications

These files document the original v1 prototype, retained for historical reference (its UI and API were removed on 2026-10-17):

- [`BENCHMARK_SPECIFICATION.md`](BENCHMARK_SPECIFICATION.md) — v1.0.0 16-parameter mathematical specification
- [`UI_UX_SPECIFICATION.md`](UI_UX_SPECIFICATION.md) — v1.0.0 frontend specification
