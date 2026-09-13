# Round-2 notes

**N-1** A method mismatch on a REAL endpoint returns 404 `"no route matches
'/v1/cvi/leaderboard'"` rather than 405 `"method not allowed"` — the
reserved-prefix check fires before Starlette's partial match. The detail is
untrue (the route exists; the method does not). Cosmetic.

**N-2 (not W4-4's, found here)** `import cvi.store.writer` as the FIRST
`cvi` import raises:
```
ImportError: cannot import name 'RepoWrite' from partially initialized module
cvi.store.writer (most likely due to a circular import)
```
Cycle: `cvi.store.writer` → `cvi.snapshot.populations` →
`cvi/snapshot/__init__.py` → `cvi.snapshot.store_cmd` → `cvi.store.writer`.
Reproduced identically on **main** (`930e596` and `6899b71`), introduced by
W4-6's `ed20c5b`, not by this branch. The suite never sees it because
something always imports `cvi.snapshot` first. Any script that imports the
writer first (mine did) breaks. Route to W4-6/W4-1 hygiene: import
`store_cmd` lazily inside `cvi/snapshot/__init__.py`'s CLI entry, or import
`cvi.store.writer` inside `store_cmd`'s function bodies.

**N-3** `LeaderboardResponse._paging_is_sane` failures are now 500s (they are
`ValidationError`s). Unreachable today — the store's `_validate_paging`
rejects out-of-range `page_size` with a 422 first — but if a future endpoint
skips the store validator, a caller-input error would surface as a server
fault. Worth a comment at the validator.

**N-4** Round-1 notes still open and unchanged (not in the bounded rework):
`raw_hash` still required on `LeaderboardRow` (fixture regeneration);
`MatrixResponse` still has no `axis_partial_count`/`axis_partial_share`;
`/not-ranked` still unpaged by contract; persona `tier_ceiling` is still the
stored balanced value while `/reproject`'s is recomputed; `web/v2/api.js`
still discards `error.detail` (W-2, W4-5b's).
