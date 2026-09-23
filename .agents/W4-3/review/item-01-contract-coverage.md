# Item 1 — Contract coverage (L3)  — PASS with 1 Warning group

Method: every returned key dumped from a live store (`item1_contract.py`,
`item45.db`) and mapped to `06` §4 / `04` §7; then validated field-by-field
against W4-5a's `porchlamp/serve/contract.py` (loaded by path, `model_rebuild`).

06 §4 row contract: **every field is produced** by `list_ranked`.
`LeaderboardRow fields NOT produced by the store: []`.
`snapshot_summary` carries manifest_root, constants_hash, benchmark_version,
counts {rated,gated,unrated,total}, persona_visible_counts (all 5 presets),
detector_summary (json_each histogram; verified non-empty: `{'review_ring': 1,
'star_burst': 2}`), reproduce_command. `matrix_points` carries excluded_count,
axis_partial_count, axis_partial_share. `repo_evidence` carries every W2-11
evidence field (`W2-11 evidence fields missing: none`).

No Blocker: no UI field the store cannot produce.

Warnings: contract mismatches vs W4-5a (W-6) — see the report's mapping table.
Notes: N-3, N-4, N-5, N-8.
