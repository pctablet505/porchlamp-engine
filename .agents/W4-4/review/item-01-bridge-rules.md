# Item 1 — endpoint projection vs the bridge rulings (L3)

Reviewer fixture: 77 rows / 64 rated, written through the real writer with
real `assign_ranks` ranks and `finalize_snapshot_hash`
(`cvi-scratch/W4-4-review/fixture.py`), driven through `TestClient`.

| # | Bridge rule | Result | Evidence |
|---|---|---|---|
| 1 | persona rows use the `_p` projection in the flat fields | PASS | 64/64 rows: flat `cvi/coverage/tier/tie_group` == `cvi_p/coverage_p/tier_p/tie_group_p`; 61 differ from the balanced value |
| 2 | `balanced_rank` + `rank_delta = rank − rank_p` | PASS | 0/64 mismatches vs an independent recomputation; balanced lens leaves both `None`; e.g. `bulk/repo-029 rank=2 balanced_rank=28 rank_delta=26` |
| 3 | stage-6 gate → UNRATED, `no_external_workflow` | PASS | `unrated_reasons=['no_external_workflow', 'off_platform_routed', 'unrated: insufficient_evidence']`; gated group holds only the 7 universal reasons |
| 4 | `off_platform_routed` derived from `routed` | PASS | present; mutant RX1 (branch removed) KILLED |
| 5 | not-ranked reason split per group | PASS | rows: gated 7 / unrated 6 / persona_hidden 6; API row total == store not-ranked total (13 balanced, 19 persona) |
| 5b | the group **counts** match the groups | **FAIL** | `counts.gated=8` vs 7 gated rows; `counts.unrated=5` vs 6 unrated rows; under a persona `counts.rated=64` vs `leaderboard.total=58` → W-1 |
| 6 | engine persona-gate codes pass through | PASS | `persona_gate_reasons=['criticality_below_threshold']` |
| 7 | `sort=p_<pillar>` with an optional `-` | PARTIAL | `p_rigor` 200, bare `rigor` 422, `stars` 422; but `-coverage` orders identically to `coverage` → W-3 |
| 8 | page_size 1..1000 | PASS | 1/20/1000 accepted; 0 and 1001 → 422 naming the bound; page=0 → 422 |
| 9 | evidence `pillar` non-null | PASS for real data; **ruling not implemented** | an unknown `param_id` yields **422**, not the 500 the ruling states → B-3 |
| 10 | matrix caption numbers | ABSENT (deferred, self-flagged) | `axis_coverage_caption={}`; `MatrixResponse` has no numeric field → N-6 |
| 11 | `raw_hash` on the detail endpoint | PASS (half-applied) | present on `RepoDetailResponse`; still required on `LeaderboardRow` → N-7 |
| 12 | matrix excluded accounting | PASS | app points 63 + excluded 14 == 77 rows (store 64/13; the NULL-coordinate row is folded in) |

Also verified: `stars_audited` round-trips (12 rows with values, 52 NULL);
`/snapshot` passes `snapshot_summary` through field-for-field; the gated,
stage-6-gated and rated detail views all render.
