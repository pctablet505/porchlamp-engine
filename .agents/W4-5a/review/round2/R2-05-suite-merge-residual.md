# R2-05 Suite, merge trial, residual findings

Branch suite at `c52ab45` (worktree clean at run time):
`env -u PYTEST_ADDOPTS PYTHONPATH=$PWD /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -B -m pytest tests -p no:cacheprovider -n 4`
-> **2312 passed, 1 skipped, 12 warnings in 12.12s** — the claim is exact.
Packet tests: **78 passed in 0.73s** (claim exact; +13 over round 1).

Merge trial onto the current main tip **`0635ead`** (past `b2a0acf`; W1-5f and the W4-3 p95 ruling
landed since round 1):
```
git worktree add --detach <scratch>/merge2 main
git cherry-pick 44b1d67 313c828 c99e324 026bb32 c52ab45     # clean, no conflicts
-> 2453 passed, 1 skipped, 12 warnings in 13.84s
```
No W4-3 p95 flake this time — the lead's 2x-under-xdist tolerance ruling landed on main.

## Residual findings (round 2)
- **N-7 (new, minor)** `pushUrl(state, {force: true})` on Enter/blur bypasses the URL-unchanged
  guard, so focusing and blurring the search box N times stacks N identical history entries; the back
  button then needs N presses to leave the page. Gate the forced push on "the last sync for this
  query was a replace".
- **M16 survivor** the new threshold test pins `contrast_ratio`'s arithmetic, not the 4.5 comparison
  (see R2-02).
- **N1 survivor** the "both or neither" half of the delta validator has no negative test.
- Carried, unchanged and still not done: **W-5** (Node tests cannot execute — no
  `web/v2/package.json` `{"type":"module"}`; `render.perf.test.mjs` still tests its own copy of
  `patchTableBody`), **W-6** (contrast pairs classify `.quad-*` as graphics although they paint the
  quadrant LABEL — 8 combinations below 4.5; `--accent-cyan` links measure 2.32 in light because the
  light palette never redefines the accents), **W-9** (the first row of a tie group carries neither
  the "=" mark nor the band), **N-1** (no per-persona payload cache, U-14), **N-2** (Not-ranked
  page/page_size absent from the URL; `]`/`[` page the leaderboard from that tab), **N-3** (dev perf
  harness, `window.__porchlampDebug`, dead `renderCard`/`renderCardList`, `escapeHtml` duplicated 8×),
  **N-4** (the two light-palette blocks can drift; only one is parsed), **N-5** (`drawer.js:196`
  reuses the empty-leaderboard sentence as the repo-not-found message, and a persona-hidden row
  opened from Not-ranked always lands there — `findRow` never searches `persona_hidden`).
- Round-2 code adds three pure functions extracted expressly for testability
  (`sortDirectionLabel`, `shouldRestoreFocusAfterRebuild`, `urlSyncMode`) that no runnable test
  exercises; fixing W-5 would convert all three into real coverage for B-3/B-4/B-5.
