# Item 6 — Performance, plans, migration (L3) — PASS budget; Warning on the claim

`item6_perf.py`, 10,000-row single-snapshot DB, p95 over 20 calls after a warm-up:

    list_ranked balanced   limit=50: p95=  6.04 ms  median=  5.99 ms
    list_ranked persona    limit=50: p95= 42.55 ms  median= 41.72 ms
    reproject              limit=50: p95= 44.88 ms  median= 44.07 ms
    list_not_ranked balanced (all) : p95=  1.92 ms  median=  1.79 ms
    snapshot_summary               : p95= 26.25 ms  median= 26.18 ms
    matrix_points balanced         : p95= 13.31 ms  median= 12.49 ms

All under `06` §7's 100 ms p95 budget. EXPLAIN QUERY PLAN with the index:
    persona base filter:   SEARCH r USING INDEX idx_eligible (snapshot_id=? AND eligible=?)
    reproject base filter: SEARCH r USING COVERING INDEX idx_eligible (snapshot_id=? AND eligible=?)
    list_ranked balanced:  SEARCH r USING COVERING INDEX idx_rank (snapshot_id=? AND rated=?)

Without the index (measured on a copy, fresh connection):
    persona plan: SCAN r ; reproject plan: SCAN r
    list_ranked persona p95=41.82 med=40.62
    reproject           p95=43.78 med=43.12
    base SELECT r.* WHERE eligible=1: 24.17 -> 23.50 ms p95
    COUNT(*) WHERE eligible=1:         0.16 -> 0.78 ms p95
-> the plan improves, the wall clock does not. Warning W-3.

Migrations: discover_migrations -> ['0001_init_v2.sql','0002_indexes.sql'].
Fresh v2 DB: applied=[1,2], schema_migrations=[(1,ts),(2,ts)], indexes
['idx_eco','idx_eligible','idx_name','idx_rank'], introspect_schema(migrated)
== introspect_schema(create_schema): True. v1-shaped copy: applied=[1,2],
legacy_repositories preserved (2 rows), same index set; second run applied=[],
schema_migrations count stays 2.
