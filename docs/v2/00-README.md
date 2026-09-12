# CVI v2 — design and delivery plan (2026-09-12)

This directory is the binding design for the next iteration of the Contributor
Value Index. Where it conflicts with `docs/ARCHITECTURE.md` §2–§4 or
`docs/BENCHMARK_SPECIFICATION.md`, this directory wins; the old documents remain
the origin of the calibration curves, the UI design, and the persona presets.

Written by the directing model on 2026-09-12 after auditing the code, the spec,
the live derived data (3,762 bundles), the served database, and GitHub/deps.dev
endpoints. Implementation is delegated to local agents through `05-WORK-PLAN.md`.

## Files

| File | What it is | Who reads it |
|---|---|---|
| `01-FINDINGS-2026-09-12.md` | Audit results, evidence-tagged; what the current ranking actually computes; answers to the handoff's ten questions | Lead, owner |
| `02-RANKING-DESIGN.md` | **The ranking process v2**: stage order, gates, 18 parameters with windows and minimum sample sizes, missingness policy, normalization, weights, tie-breakers, persona gates, versioning, validation. Every decision has an id `D-*` that packets cite | Everyone; packets cite sections |
| `03-ANTI-MANIPULATION.md` | Threat model, per-parameter cost-to-fake table, input hygiene, pipeline integrity (hash manifests, reproducibility), detectors, process controls | Lead, reviewers, integrity packets |
| `04-ARCHITECTURE.md` | Target package layout, core contracts (`Measured`/`Unmeasured`, envelopes, snapshots), store schema v2, API v2, migration path | Architecture packets, reviewers |
| `05-WORK-PLAN.md` | Waves and packets: goal, evidence, cited decisions, file ownership, steps, acceptance (fail-before tests), measurements to report, forbidden actions, model routing, dependencies | Local agents (one packet each), lead |

## How a local agent uses this directory

1. You receive **one packet id** (for example `W1-1`). Open `05-WORK-PLAN.md`,
   read only that packet and the "Rules for every packet" section.
2. Read only the `D-*` decisions the packet cites in `02-RANKING-DESIGN.md`
   (and the `03`/`04` sections it names). Do not read the whole directory.
3. Work in your own worktree with your own scratch data directory (rule R-3).
4. Review happens under the hub review protocol:
   `/home/pctablet505/Projects/kimi-colab-context/data/context/shared/process/review-protocol/00-README.md`
   (levels `20-LEVELS.md`, lenses `30-LENSES.md`, templates `60-TEMPLATES/`).
   Your verdicts are `PASS`, `FAIL`, `UNDECIDED` per item; the lead decides
   `ACCEPT`/`REWORK`.

## Non-negotiables (owner rules, restated)

- Only verified data enters SQLite. Provenance is a fetched URL plus a content
  hash; a bundle without that is unverifiable and never promoted.
- No fabricated repositories and no fabricated telemetry. The LLM-curation
  path (`scripts/curate_dataset_llm.py`, `HANDOFF-DATASET-CURATION-AGENT.md`)
  is deleted in packet `W0-4`; do not run it.
- Every behavioural change ships a test that fails before the change and an
  over-correction guard that the correct path is unchanged.
- Never edit `data/` in place, and never change the behaviour of a running
  sweep. `scripts/run_all.py --all --skip-downloads` (pid 173791, started
  09:08 on 2026-09-12) is still writing `data/raw`, `data/derived`,
  `data/verified`. Use a copied sample under a scratch directory.
- Agents report diffs; the lead commits serially. Commit author email is the
  noreply address (see `docs/REVIEW_GUIDELINES.md` §5).

## Owner decisions requested (do not block on these; defaults are stated)

| # | Decision | Default in this plan | Where |
|---|---|---|---|
| O-1 | Legal-IP score for "no CLA and no DCO" | 85 (between DCO 100 and foundation CLA 75) | `02` D-PARAM 3.1 |
| O-2 | Pillar weights 25/25/20/15/15 | Unchanged (R2 mandates them); within-pillar composition changes; a v3 question | `02` D-AGG-1 |
| O-3 | Delete the LLM-curation script and its handoff | Delete (integrity violation of the only-verified rule) | `05` W0-4 |
| O-4 | Universe size for v2.0 | Freeze at the 5,753 candidates minus non-software; expand criteria at v2.1 | `02` D-UNI |
| O-5 | Tier cutoffs | Absolute 90/80/70/55/40 kept; coverage ceilings added | `02` D-MAT-2 |
| O-6 | Forward validation experiment | Start at the first v2 snapshot; re-collect 90 days later | `02` D-VAL |
| O-7 | Model swap for review windows | Flash-Next implements and reviews; swap to the 27B for adversarial passes at wave gates | `05` §0.2 |

## Status

| Wave | State | Landed packets |
|---|---|---|
| W0 Freeze & instrument | not started | – |
| W1 Data truth | not started | – |
| W2 Scoring v2 | not started | – |
| W3 Integrity | not started | – |
| W4 Store / API / architecture | not started | – |
| W5 Validation & release | not started | – |

The lead updates this table; agents never edit it.
