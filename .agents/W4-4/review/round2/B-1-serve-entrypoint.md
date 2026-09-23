# B-1 — `scripts/serve.py` could not serve the v2 app — FIXED (verified)

Reviewer's own reproduction, real subprocess + real HTTP from `/tmp`
(foreign cwd), not `create_app` in-process.

**On a v2 store** (`porchlamp.snapshot store tests/fixtures/snapshot_50`):
```
$ cd /tmp && PYTHONPATH=<worktree> python <worktree>/scripts/serve.py --db real.db --port 8794
INFO:     Uvicorn running on http://127.0.0.1:8794
Porchlamp serve: no v1 data at this path (a v2-only database, or nothing promoted yet)
Porchlamp serve: http://127.0.0.1:8794/ (API under /v1/porchlamp, legacy under /legacy)

  /v1/porchlamp/snapshot                 -> 200 application/json
  /v1/porchlamp/leaderboard?page_size=2  -> 200 application/json
  /v1/porchlamp/not-ranked               -> 200 application/json
  /v1/porchlamp/bogus                    -> 404 application/json  {"error":{...}}
  /                                -> 200 text/html
```
Round 1 died here with `sqlite3.OperationalError: no such column: overall_porchlamp`
before uvicorn started. The banner no longer advertises `/healthz`.

**On a v1 store** (promoted, guard-passing):
```
Porchlamp serve: 0 verified repos from 'v1pass.db' (snapshot snap-v1)
  /v1/porchlamp/leaderboard  -> 503 {"error":{...,"detail":"no snapshot has been written to this database yet"}}
  /v1/porchlamp/snapshot     -> 503 (same)
  /legacy/healthz      -> 200 {"status":"ok"}
  /                    -> 200 text/html
```
Round 1 returned a bare plain-text `500 Internal Server Error` here.

Mutant B1 (`_current_snapshot_id`'s `OperationalError` tolerance removed)
KILLED by `test_create_app_serves_v1_porchlamp_endpoints_as_503_not_500_on_a_legitimate_v1_db`.
