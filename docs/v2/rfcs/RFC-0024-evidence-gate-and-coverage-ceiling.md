# RFC-0024: evidence-gate-and-coverage-ceiling

## Motivation

Two related properties of the evidence-sufficiency gate (`D-EVID-1`, `docs/v2/02-RANKING-DESIGN.md` section 2) and the coverage ceiling bands (`D-MISS-3`, section 6) were audited against the `2026-10-21` (5,436 eligible non-routed repositories; 3,746 rated, 1,690 `unrated: insufficient_evidence`) and `2026-10-17` (5,594 eligible non-routed repositories) snapshots:

1. **Unmeasured responsiveness in rated repositories (`D-EVID-1`, Claim F7).**
   - Under `D-DEF-2`, contributor value is `P(decision) × E[impact | merged] / friction`, where `P(decision)` is measured by the Responsiveness pillar (`W_responsiveness = 0.25`).
   - Because `D-MISS-2` renormalises the composite over measured pillars (`C = Σ_p W_p · c_p`), a repository with `c_responsiveness = 0.0` (all five responsiveness parameters `Unmeasured`) can still reach composite coverage up to `0.75 ≥ 0.60` from the other four pillars alone and receive a balanced rank and tier (`ceiling B`).
   - On `2026-10-21`, **275 rated repositories** have `c_responsiveness = 0.0` (`pillars["responsiveness"].score is None`): 11 in Tier B (best rank #561), 137 in Tier C, and 127 in Tier D. On `2026-10-17`, **271 rated repositories** have `c_responsiveness = 0.0`.
   - Among those 275 rated repositories on `2026-10-21`, 270 have all five responsiveness parameters `Unmeasured(insufficient_n)` (median `gr14_ghost_rate` sample size `n = 3`, max `9`, so `D-GATE-5` is `UNDECIDED`). **92** of them have a raw 14-day zero-response share `> 0.50` over `n ≥ 3` external PRs, and **64** have a `100%` 14-day zero-response share (`n ≥ 3` outside PRs, every single one unanswered within 14 days; e.g. `moka-rs/moka` at rank #914 with 5/5 unanswered, `SecretFlow/spu` at rank #921 with 5/5 unanswered). Ranking those repositories on Criticality, Governance, Rigor, and Accessibility alone inverts `D-DEF-2`.
   - Conversely, relaxing `D-EVID-1` so `Unmeasured(insufficient_n)` leaves the coverage denominator (audit option `a2`) would admit **+1,159** low-volume repositories on `2026-10-21`, **905 (78.1%)** of which have an empty Responsiveness pillar (`c_responsiveness = 0.0`). In a masking experiment on all **437** fully measured repositories (`C = 1.0`) on `2026-10-21`—applying empirical missingness masks from rated and unrated repositories—the `a2` entrant patterns produce median Kendall `τ = 0.483`, composite `p95 |error| = 18.08` points, and a **64.3%** tier-flip rate, whereas empty-responsiveness patterns among currently rated repositories produce `τ = 0.580`, `p95 |error| = 14.37` points, and a **53.1%** tier-flip rate. Both violate `D-EVID-1`'s anti-noise bar.

2. **Single-parameter missingness and the no-ceiling coverage threshold (`D-MISS-3`, Claim F2).**
   - On `2026-10-21`, the rated tier distribution is `S+: 0, S: 0, A: 46, B: 1,349, C: 1,824, D: 527` (`3,746` total), and `3,105 / 3,746` (`82.9%`) carry a non-null `tier_ceiling` (`1,629` in `[0.80, 0.95)` with ceiling `A`; `1,476` in `[0.60, 0.80)` with ceiling `B`).
   - On `2026-10-21` pre-fix data, the absence of `S` and `S+` repositories is caused by parameter score compression in pillars 2–5 (`max(composite) = 79.42 < 80.0` at rank #1 `servo/rust-smallvec`), not by `tier_ceiling`: ceiling `A` binds on **0** repositories today, and ceiling `B` binds on **7** repositories (`0.19%`, capped from `A` to `B`).
   - However, once collector and derivation fixes in sibling workstreams (WS2 collector completeness, WS4 `3.3 promotion_ladder`, WS5 `4.3 substantive_review_rate`, WS6a `2.2 openssf_criticality` and `5.1 newcomer_issue_supply`) lift composite scores into `[80, 100]`, `NO_CEILING_MIN_COVERAGE = 0.95` caps any repository missing even a **single** parameter in most pillars at Tier `A`:
     - Across the 18 parameters, global composite weights `W_p · w_i` range from `0.025` (`1.5 ttm_p90_hours`) to `0.100` (`2.1 dependents_deduped`), with 13 of 18 parameters carrying global weight `> 0.05`:
       - **Responsiveness** (`W = 0.25`): `1.1 ttfhr_hours` (`0.075`), `1.2 dr90_decision_rate` (`0.0625`), `1.3 mr_first_rate` (`0.050`), `1.4 gr14_ghost_rate` (`0.0375`), `1.5 ttm_p90_hours` (`0.025`).
       - **Criticality** (`W = 0.25`): `2.1 dependents_deduped` (`0.100`), `2.2 openssf_criticality` (`0.0875`), `2.3 downloads_monthly` (`0.0625`, when applicable).
       - **Governance** (`W = 0.20`): `3.1 legal_ip` (`0.070`), `3.2 elephant_factor` (`0.070`), `3.3 promotion_ladder` (`0.060`).
       - **Rigor** (`W = 0.15`): `4.1 pipeline_defenses` (`0.060`), `4.2 ai_posture` (`0.0375`), `4.3 substantive_review_rate` (`0.0525`).
       - **Accessibility** (`W = 0.15`): `5.1 newcomer_issue_supply` (`0.0525`), `5.2 newcomer_conversion` (`0.0525`), `5.3 rr90_return_rate` (`0.0225`), `5.4 dx` (`0.0225`).
     - A single unmeasured parameter therefore drops composite coverage `C` into `[0.9000, 0.9750]`. For example, rank #1 `servo/rust-smallvec` (`composite = 79.42`, 17 of 18 parameters measured, only `1.2 dr90_decision_rate` `insufficient_n` with `n = 4 < 5`) has `C = 0.9375 < 0.95` and is capped at `ceiling = "A"`.
     - In the single-parameter masking experiment on the 437 fully measured repositories, masking any single parameter produces a tight composite error (`mean |error| = 0.55–3.68` points, `p95 ≤ 4.81`), whereas masking two parameters drops `C` below `0.90` for all substantive pairs.
     - Setting `NO_CEILING_MIN_COVERAGE = 0.90` permits at most **one** unmeasured parameter in any pillar (`C ≥ 0.9000`) to qualify for an uncapped tier (`S` / `S+`), while any two missing parameters outside the smallest auxiliary pairs remain in `[0.80, 0.90)` (`ceiling A`).

## Change

1. **`D-EVID-1` (`EVIDENCE_GATE_REQUIRED_PILLARS`)**: A repository is rated only if `coverage >= EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE` (`0.60`) **and** every pillar in `EVIDENCE_GATE_REQUIRED_PILLARS = ("responsiveness",)` has at least one measured parameter (`pillars[p].coverage > 0` and `pillars[p].score is not None`). The same required-pillar check applies in `porchlamp.core.gates.evidence_sufficiency`, `porchlamp.core.aggregate.coverage_status`, `porchlamp.core.persona.persona_composite`, `porchlamp.audit.sensitivity`, and the SQLite reprojection query (`porchlamp.store.queries.reproject_leaderboard`).
2. **`D-MISS-3` (`NO_CEILING_MIN_COVERAGE`)**: Lower `NO_CEILING_MIN_COVERAGE` from `0.95` to `0.90`. Coverage in `[0.90, 1.00]` has no tier ceiling (`None`); `[0.80, 0.90)` has ceiling `A`; `[0.60, 0.80)` has ceiling `B`.

| Constant | Before | After |
|---|---|---|
| `EVIDENCE_GATE_REQUIRED_PILLARS` | *(absent)* | `("responsiveness",)` |
| `NO_CEILING_MIN_COVERAGE` | `0.95` | `0.90` |

`CONSTANTS_HASH` moves:
`4a6529ef5ca3c97c443442774440b4947ebc849265132fc16a61e3a60f231a2a` ->
`d8c067b9e928b138beb68c9909d605b5737a1885275c786e0fbe8d9050b8a1c2`
(pinned in `tests/core/test_frozen.py`, with `_FROZEN_TABLE` rows for both constants).

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump, matching the RFC-0001..0013 pre-release convention).

## Evidence

- **Exact baseline reproduction (`2026-10-21` and `2026-10-17`)**:
  - Recomputing all `5,436` eligible non-routed repositories on `2026-10-21` and `5,594` on `2026-10-17` from stored parameter scores through `aggregate_pillars`, `composite_coverage`, `coverage_status`, `composite_score_raw`, `tier_from_score`, `apply_ceiling`, `leverage`, `friction`, `quadrant`, and `rank_records` yields **0 mismatches** across all 22 stored fields.
- **Impact of `EVIDENCE_GATE_REQUIRED_PILLARS = ("responsiveness",)` (`D-EVID-1`)**:
  - **`2026-10-21` snapshot**:
    - Rated repositories: `3,746 -> 3,471` (`-275`, moved to `unrated: insufficient_evidence`).
    - Tier breakdown of the 275 removed repositories: Tier B: `11` (best old rank `#561`), Tier C: `137`, Tier D: `127` (0 in Tier A/S/S+).
    - Relative ordering among the `3,471` remaining rated repositories: **Kendall `τ = 1.0000`** (no composite score changes; `2,911` repositories shift strictly upward in rank by a median of `15` places, `p95 = 207`, `max = 275`).
    - Persona rated counts (under each persona's own coverage): `balanced -275`, `first_timer -4`, `career_capital -680`, `senior_systems -1,073`, `ai_assisted -439`.
  - **`2026-10-17` snapshot**:
    - Rated repositories: `4,396 -> 4,125` (`-271` balanced).
- **Impact of `NO_CEILING_MIN_COVERAGE = 0.90` (`D-MISS-3`)**:
  - **`2026-10-21` snapshot (pre-WS2/WS4/WS5/WS6a data)**:
    - `449` rated repositories with `0.90 <= C < 0.95` move from `tier_ceiling = "A"` to `tier_ceiling = None` (full coverage band rises from `641` to `1,090` of `3,471` rated repositories, or `17.1% -> 31.4%`; ceiling `A` band moves from `1,629` to `1,180`).
    - `0` repositories change `tier` on pre-fix `2026-10-21` data because `max(composite) = 79.42 < 80.0`.
  - **Post-compression what-if on `2026-10-21`**:
    - Under realistic WS4/WS5/WS6a parameter decompression (`max(composite) = 82.73`), `4` repositories clear `>= 80.0` (Tier S), all `4` of which have `0.90 <= C < 0.95` (including rank #1 `servo/rust-smallvec` at `C = 0.9375`) and would have been held at Tier `A` under `0.95` but reach Tier `S` under `0.90`.
- **Fixture (`tests/fixtures/snapshot_50/`)**:
  - Regenerated with `scripts/build_fixture_snapshot.py --rewrite-expected`. Because all 50 repositories in the fixture have `rated=0, gated=18, unrated=32`, 0 of 50 repositories change rank, composite, tier, or gate outcome; only `ScoreRecord.constants_hash` moves (`records_hash`: `b4b8f4635ff7287294a2507dfd04104245ee37ef96b5449b352bd1245335ecaa` -> `aebc001b33f82296df3c8b1d166faf9496b84d7babf207856f9f05a4f8a1c910`; `merkle_root`: `c2882572cde049437243f353bfaecdf5748e4bf9679950449baec776dd8b4ad4` -> `3ba128e51f3480f9458efe59c2ac39d328d74fa97416337ae169e5bd4cfb8fc7`).
  - Sensitivity (`tests/audit/test_sensitivity_fixture.py`) and anchor checks (`tests/audit/test_anchors_fixture.py`) construct explicit measured responsiveness inputs on their synthetic/anchor test fixtures and pass (`τ >= 0.90` across all 5 pillars; all 8 anchor invariants pass).

## Reviewer

Reviewed by independent verification agent (WS3, 2026-10-23).
