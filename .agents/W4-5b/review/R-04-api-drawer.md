# R-04 api.js error handling, and the drawer `findRow` fix (check 5)

## api.js — PASS
- Non-2xx: `extractErrorDetail` parses `{"error": {endpoint, status, detail}}` and tolerates the bare
  `{"error": "..."}` form; `detail` is attached to `ApiError` and passed as the message.
- A 2xx with a non-JSON Content-Type becomes an `ApiError` naming the endpoint and the actual type
  ("expected application/json, got text/html") — checked BEFORE parsing, so it is not reported as a
  parse failure. Both `getJson` and `postReproject` do this.
- Details are surfaced: every `copy.*ErrorText(endpoint, status, detail)` takes the third argument and
  all five screens' banners pass it.
- Verified with the server's own `--fail` switch (their `TestErrorBodyShape`, 3 tests); mutant M11
  (flattening the error body) kills 5 tests.
- **W-6** `postReproject` duplicates ~25 lines of `getJson`'s timeout/content-type/parse block; one
  shared `request(method, endpoint, body)` would remove it.

## The drawer `findRow` defect — the minimal change (lead ruled it IN scope)
`views/drawer.js::findRow` searches only `state.leaderboard.rows` (the loaded page) and
`state.notRanked`, so a repo opened from Matrix/Compare/Configure that is not on the current
leaderboard page renders `copy.emptyLeaderboardText(0, 0)` — a leaderboard-empty sentence, wrong both
semantically and factually.

**The server half already exists.** `scripts/serve_ui_fixtures.py:399` already implements
`find_repo_detail(owner, name)` and the handler at line 606 already serves
`GET /v1/cvi/repos/{owner}/{name}` → `{"kind": "rated"|"gated"|"unrated", "row": {...}}`, added under
the lead's own 2026-09-13 drawer-content-parity ruling. W4-4's API carries the same endpoint
(`RepoDetailResponse`). So the fix is client-side only:

1. `api.js`: `export function fetchRepoDetail(owner, name)` → `getJson('/v1/cvi/repos/<o>/<n>')`
   (4 lines, mirroring `fetchEvidence`).
2. `state.js`: a `repoDetailByKey: {}` cache plus `REPO_DETAIL_{LOADING,OK,ERROR}` cases, exactly the
   shape `evidenceByRepo` already uses (~10 lines).
3. `app.js::openRepo`: after `OPEN_REPO`, if `findRow(state, owner, name)` returns `null`, dispatch
   the fetch (one `if`, ~5 lines) — the drawer already re-renders on every dispatch.
4. `views/drawer.js::findRow`: one final fallback before `return null` —
   `const cached = state.repoDetailByKey?.[`${owner}/${name}`]; if (cached) return cached;` — the
   cached value is already `{kind, row}`, the exact shape `findRow` returns (2 lines).
5. The not-found branch keeps its place for a genuine 404, but should render a repo-not-found
   sentence rather than `emptyLeaderboardText(0, 0)` (new `copy.` string).
Nothing in the leaderboard's own pagination state is touched, which is the risk the implementer
correctly refused to take.
