# Item 2 — the served-DB guard (L3) — narrow check PASS, effect ABSENT on v2

Reuse, not duplication: `scripts/serve.py` gained `open_or_exit` (the three
inline lines `main()` already ran) and `cvi.serve.app._run_served_db_guard`
calls it. `main()`'s guard is now literally `store = open_or_exit(args.db)`.
Message format untouched — a v1-shaped DB with no `snapshot_meta` raises the
identical `SystemExit` from both entrypoints (verbatim in the report).
`tests/test_serve.py` 15/15 still pass unmodified.

But the guard cannot run against a v2 database at all:
- fresh/migrated v2 DB → `OperationalStore.__init__` raises
  `sqlite3.OperationalError` → `_run_served_db_guard` returns `None` → guard
  skipped, `/legacy` not mounted.
- after `apply_migrations` on a v1 DB (the documented `04` §10 path, tables
  `legacy_repositories` + v2 `repositories`): `/legacy` mounted = False.
- a finalised v2 store edited after `finalize_snapshot_hash` (stored
  `3440e9517831891d…`, recomputed `00fb9313d54bb18c…`) is **served**: 200,
  top row `cvi=100.0`. `cvi/serve/app.py` never calls `snapshot_dump_hash`
  (grep: 0 occurrences).

→ Blocker B-4. The dispatch's literal instruction (reuse serve.py's guard)
was met; `03` §5's protection nonetheless does not exist for the only
database shape this API can serve.
