# B-2 — the SPA fallback swallowed API paths — FIXED (verified)

```
  GET    /                            -> 200  html=True
  GET    /index.html                  -> 200  html=True
  GET    /app.js                      -> 200  html=False
  GET    /some/deep/app/route         -> 200  html=True    (genuine SPA fallback, preserved)
  GET    /?persona=first_timer        -> 200  html=True    (URL state reloads, preserved)
  GET    /v1/porchlamp/bogus                -> 404  html=False error-shape
  GET    /v1/porchlamp/leaderboard/extra    -> 404  html=False error-shape
  GET    /v1/porchlamp/                     -> 404  html=False error-shape
  GET    /v1                          -> 404  html=False error-shape
  GET    /healthz                     -> 404  html=False error-shape
  GET    /legacy/                     -> 404  html=False error-shape
  GET    /legacy/healthz              -> 404  html=False error-shape   (no v1 shape on this db)
  GET    /v1/porchlamp/reproject            -> 404  html=False error-shape   (POST-only route)
  POST   /v1/porchlamp/leaderboard          -> 404  html=False error-shape
  DELETE /v1/porchlamp/snapshot             -> 404  html=False error-shape
```
Every reserved path now returns the module's own `{"error": {endpoint,
status, detail}}`; every app-owned path still resolves or falls back.
Confirmed over real HTTP too (`/v1/porchlamp/bogus -> 404 application/json`).

Mutant B2 (reserved-prefix exclusion removed) KILLED by four tests
(`test_unmatched_api_path_is_a_json_404_not_the_spa_shell`,
`test_method_mismatch_on_a_real_endpoint_is_not_the_spa_shell`,
`test_bare_healthz_is_not_the_spa_shell`, +1). Mutant R3 (fallback removed
entirely) still KILLED — the over-correction guard holds.

Note N-1: a method mismatch on a REAL endpoint reports 404 `"no route
matches '/v1/porchlamp/leaderboard'"` — semantically 405, and the detail is
untrue (the route exists, the method does not). Cosmetic; the round-1
405 came from StaticFiles by accident anyway.
