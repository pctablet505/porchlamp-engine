# Item 9 — Mutation battery (L3) — 12/15 killed, 3 survivors

Driver: `cvi-scratch/W4-3-review/mutate.py` against the detached copy at
`cvi-scratch/W4-3-review/tree` (63772a4). Baseline `tests/store/` +
`tests/core/test_import_graph.py`, `__pycache__` cleared before every run,
each mutant a single unique string replacement, file restored from a byte
backup and asserted identical afterwards.

| # | Mutant | Result | Killing test(s) |
|---|---|---|---|
| M1 | drop NULLS LAST from the friction ASC tie level | KILLED | test_sql_tie_break_parity_across_balanced_and_every_persona_200_rows_with_nulls; test_ranked_order_sql_parity_with_tie_key_nulls_at_multiple_levels |
| M2 | `_tie_group_sql`: DENSE_RANK unconditionally (singleton not nulled) | KILLED | test_reproject_and_persona_tie_group_nulls_singleton_and_shares_non_singleton; test_reproject_parity_with_inline_oracle_...; test_sql_tie_break_parity_... |
| M3 | persona rated filter `>` instead of `>=` at 0.60 | KILLED | test_list_ranked_and_list_not_ranked_partition_every_row_under_every_lens; test_list_ranked_persona_rated_boundary_is_inclusive_at_exactly_0_60 |
| M4 | `_build_order_by`: unknown sort returns the default | KILLED | test_list_ranked_sort_validation_names_the_allowed_set |
| M5 | reproject: composite divided by 1 | KILLED | test_reproject_renormalises_over_measured_pillars_only (+10 more) |
| M6 | reproject: a c=0 pillar kept in the sums | KILLED | test_reproject_excludes_a_pillar_whose_coverage_is_zero_even_if_a_score_is_stored |
| M7 | list_ranked balanced: `offset + 1` | KILLED | test_list_ranked_balanced_basic_order_and_total (+8 more) |
| M8 | `_persona_paths`: coverage path aliased to `.cvi` | KILLED | test_list_ranked_persona_rated_boundary_is_inclusive_at_exactly_0_60 |
| M9 | reproject: tier label from score_tier_index alone | KILLED | test_reproject_applies_ceiling_b_band; test_reproject_applies_ceiling_a_band; test_reproject_parity_... |
| M10 | schema.py: `idx_eligible` statement removed | KILLED | test_apply_migrations_on_empty_db_matches_schema_py; test_indexes_match_04_section_5 |
| R1 | persona JSON path for `rated_p` points at `.tier` | **SURVIVED** (equivalent on the returned set) | — |
| R2 | reproject: tier cut from the ROUNDED composite | **SURVIVED** | — |
| R3 | `q` match becomes case-SENSITIVE | KILLED | test_list_ranked_q_search_is_case_insensitive_substring_on_owner_or_name |
| R4 | persona not-ranked counts double-count persona-gated rows | **SURVIVED** | — |
| R5 | migration 0002: index name typo (schema.py untouched) | KILLED | test_explain_query_plan_uses_idx_eligible_for_persona_and_reproject_base_filter |

All ten of the implementer's mutants re-confirmed KILLED with named tests.

Survivor analysis:
- **R1 is EQUIVALENT on the returned set** and that is itself the finding:
  `rated_p` is invariably `True` for every row `list_ranked` can return
  (proven for all four presets: `distinct rated_p values on the returned set:
  {True}`, `tier_p None present: False`), because the visibility filter already
  requires `coverage_p >= 0.60`. The field carries no information.
- **R2 is NOT equivalent** — demo: pillar score 89.997 with a single-pillar
  weight vector returns `cvi=90.0, tier='S'`; under the mutant it becomes
  `'S+'`. `04` §8's "tier comparisons never use the rounded display value" is
  unenforced for `reproject`.
- **R4 is NOT equivalent** — 10 persona-gated rows exist under career_capital
  in the item-5 fixture; nothing asserts `sum(counts.values()) == len(rows)`
  for the persona branch (only the balanced branch is covered, by
  test_list_not_ranked_balanced_reasons_and_counts_match_list_ranked_complement).

Restore proof: `git status --porcelain` empty; POST-RESTORE rc=0.
