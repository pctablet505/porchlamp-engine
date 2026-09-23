# I-09 Size and subtractability
16,119 insertions decompose as: fixtures JSON **9,791 (61%)**; shipped web/v2 JS 2,733; CSS 974;
unexecuted Node tests 672; Python (contract 471 + server 325 + 5 test files 776) 1,576; handoff 427;
index.html 24. Reviewable code is ~5.3k lines, not 16k — the size is dominated by a 73-row fixture
set that the reviewer's own invariants found internally sound.

Subtractable now:
- `escapeHtml` is redefined verbatim in EIGHT files (app.js, table.js, badge.js, sparkbar.js,
  keymap.js, leaderboard.js, notranked.js, drawer.js) — ~48 lines; one shared module.
- `components/table.js:265 renderCard` / `:315 renderCardList` are dead: the only reference is
  leaderboard.js re-exporting them (`:31`, `:283`); nothing calls either, and no executable test
  reaches them. ~25 lines.
- `app.js:288 replaceUrl` — dead (and its absence is Blocker B-5).
- The `?perf=` harness (`app.js` ~l.739-767), `perf-fixtures.js` (91 lines) and
  `window.__porchlampDebug = boot()` (`app.js:783`) ship dev-only code and a global handle to every user.
  Keep the generator for the Node test; gate the harness and the global out of the served bundle.
- `render.perf.test.mjs:36-67 patchWindow` duplicates `patchTableBody` (~30 lines) — import it.
NOT subtractable but untested: `tokens.css` repeats the light palette verbatim in two blocks
(`@media (prefers-color-scheme: light)` and `[data-theme="light"]`, ~26 declarations each). CSS needs
both selectors; the test should assert the two blocks are token-for-token identical, or they will
drift silently (only the `[data-theme]` copy is parsed today).
