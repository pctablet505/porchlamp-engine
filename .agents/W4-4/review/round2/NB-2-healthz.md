# NB-2 (lead addendum 2) — `/healthz` is a JSON 404 — OPEN at ac4b46a

```
  GET /healthz -> 404 {"error":{"endpoint":"/healthz","status":404,
                       "detail":"no route matches '/healthz'"}}
```
`healthz` is in `_API_RESERVED_EXACT`, so B-2's fix correctly stops it
returning the SPA shell — but no route replaces it. v1's own
`/legacy/healthz` still answers `{"status":"ok"}` when a v1 shape is
mounted. `scripts/serve.py`'s banner no longer advertises `/healthz`
(round-1 note N-2 closed), so nothing currently lies about it; a real
route is being added per the addendum.

Acceptance: `GET /healthz -> 200` with a small JSON body, on both a v2-only
and a v1+v2 database, plus a test; keep the reserved-path 404 for every
other unmatched `/v1/*` and `/legacy/*` path (mutant B2 must stay dead).
