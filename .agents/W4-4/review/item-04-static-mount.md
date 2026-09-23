# Item 4 — static mount, SPA fallback, API precedence (L3) — FAIL

`web/v2` is mounted at `/` with an index fallback; v1 at `/legacy` only when
the DB has a v1 shape. Measured with `TestClient`:

```
  /                            -> 200  text/html                     html=True
  /index.html                  -> 200  text/html                     html=True
  /app.js                      -> 200  text/javascript               html=False
  /some/deep/app/route         -> 200  text/html                     html=True   (SPA fallback, correct)
  /?persona=first_timer        -> 200  text/html                     html=True   (URL state reloads, correct)
  /v1/porchlamp/bogus                -> 200  text/html                     html=True   <-- API path swallowed
  /v1/porchlamp/leaderboard/extra    -> 200  text/html                     html=True   <-- API path swallowed
  /v1/porchlamp/                     -> 200  text/html                     html=True
  /healthz                     -> 200  text/html                     html=True   <-- still advertised by serve.py
  GET /v1/porchlamp/reproject        -> 200  text/html                     html=True   <-- method mismatch swallowed
  POST /v1/porchlamp/leaderboard     -> 405  {"error":{...}}                           (from StaticFiles, not the router)
```

Registered API routes do win over the mount (precedence holds); the failure
is that `Mount("/")` returns a FULL match for every other path, so both
unmatched API paths and partial (method) matches fall through to the SPA
fallback. `web/v2/api.js` treats 200 as success and then fails
`response.json()`, yielding `ApiError(endpoint, 200, <parse error>)`.
→ Blocker B-2.

Zero-external-request test: `tests/test_ui_no_external_resources.py` still
passes and mutant R3 (fallback removed) is killed by
`test_v2_static_mount_serves_html_with_no_external_references` — the mount
itself is covered; the API-path case is not.

`/legacy`: mounted only for a v1-shaped DB that passes the guard
(`/legacy/healthz -> 200 {"status":"ok"}`); NOT mounted after
`apply_migrations`, i.e. never on a real v2 store (see item 2).
