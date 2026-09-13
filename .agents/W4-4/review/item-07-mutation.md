# Item 7 — mutation battery: their 10 re-injected + 6 (+3 extra) reviewer mutants

Driver `cvi-scratch/W4-4-review/mutate.py` / `mutate2.py` against the detached
copy at `cvi-scratch/W4-4-review/tree` (f6a398c). Baseline
`tests/serve/ tests/test_serve.py tests/test_ui_fixtures.py`, `__pycache__`
cleared before every run, each mutant a single unique string replacement,
file restored from a byte backup and asserted identical.

| # | Mutant | Result | Killing test(s) |
|---|---|---|---|
| M1 | drop `suspicion` from `_project_leaderboard_row` | KILLED | test_leaderboard_suspicion_and_stars_audited_round_trip |
| M2 | `_paging` off-by-one (`page * page_size`) | KILLED | test_leaderboard_balanced_default_shape_and_total (+4) |
| M3 | sort allow-list bypass (`stars` → `coverage`) | KILLED | test_leaderboard_sort_pillar_translation_and_allow_list |
| M4 | `_error_body` drops the `error` wrapper | KILLED | test_error_body_shape_is_consistent_across_404_422_503 (+7) |
| M5 | leaderboard persona default → `first_timer` | KILLED | test_leaderboard_balanced_default_shape_and_total (+3) |
| M6 | unknown-repo `/evidence` returns `[]` | KILLED | test_evidence_unknown_repo_is_404_with_consistent_body |
| M7 | `_run_served_db_guard` always returns `None` | KILLED | test_create_app_refuses_a_fixture_marked_v1_database |
| M8 | `tie_group or 0` | KILLED | test_leaderboard_tie_group_shared_by_ties_and_null_for_singletons (+1) |
| M9 | `stars_audited` hardcoded 999 | KILLED | test_leaderboard_suspicion_and_stars_audited_round_trip (+2) |
| M10 | methodology from a stub dict | KILLED | test_methodology_matches_the_real_pure_document_and_its_hash |
| R1 | persona projection swapped (flat = balanced) | KILLED | test_leaderboard_persona_projects_p_suffixed_fields_and_computes_delta |
| R2 | `rank_delta` sign inverted | KILLED | …computes_delta; test_leaderboard_persona_delta_is_null_when_balanced_rank_is_null |
| R3 | SPA fallback removed entirely | KILLED | test_v2_static_mount_serves_html_with_no_external_references |
| R4 | guard skipped (SystemExit swallowed) | KILLED | test_create_app_refuses_a_fixture_marked_v1_database |
| R5 | `_paging` clamps page_size so the store never rejects it | **SURVIVED (equivalent)** | `LeaderboardResponse._paging_is_sane` independently bounds page_size, so the 1001 case is still 422 |
| R6 | error body missing `endpoint` | KILLED | test_error_body_shape_is_consistent_across_404_422_503 (+3) |
| RX1 | `_classify_not_ranked` drops the `off_platform_routed` branch | KILLED | test_not_ranked_off_platform_routed_is_derived |
| RX2 | `_matrix_point` plots a point whose `quadrant` is NULL | **SURVIVED** | none — the independent quadrant check the docstring claims is untested |
| RX3 | `_no_ceiling_or_band` bands swapped (A ↔ B) | **SURVIVED** | none — the one arithmetic the app layer does itself is unasserted |

17/20 killed; 1 equivalent; 2 real detection gaps (RX2, RX3).
Restore proof: `git status --porcelain` empty, POST-RESTORE rc=0.
