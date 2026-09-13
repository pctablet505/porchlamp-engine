# I-05 Tests — PASS (65) with real gaps
Executed: `env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest tests/test_ui_fixtures.py tests/test_serve_ui_fixtures.py tests/test_ui_contrast.py tests/test_ui_copy_usage.py tests/test_ui_no_external_resources.py -p no:cacheprovider` -> **65 passed in 0.71s** (claim of 65 confirmed).

Contrast test: I recomputed WCAG ratios with my own implementation and agree on all six spot pairs
(dark text-muted/elevated 4.87; dark text-primary/base 18.57; light text-muted/surface 5.98; dark
quad-nursery/elevated 6.81; dark border-focus/surface 8.33; light quad-crucible/elevated 5.74).
The test's *scope* is wrong in one place: `.quad-*` colours the quadrant LABEL TEXT
(`badge.js` renderQuadrant), but the pairs list them as GRAPHIC (3:1). At 4.5:1 eight combinations
fail — worst dark Crucible on elevated 3.27, light Sweet Spot on elevated 3.44. `--accent-warning`
(`.suspicion-icon`, `.delta-down`) and `--accent-cyan` (every `<a>`) are not in either list;
accent-cyan is never redefined for light and measures 2.32:1 on the light canvas.

Node tests: `node`/`deno`/`bun`/`qjs`/`d8` are all absent (verified) — the handoff's claim holds.
Read for correctness: the assertions are sound (I hand-traced the patchRows add/remove/reorder/no-op
expectations and they match the algorithm). Two blocking problems as written:
(a) there is NO `package.json` anywhere in the repo, so `import ... from "../components/table.js"`
    resolves `.js` as CommonJS and throws `SyntaxError: Unexpected token 'export'` on the stated
    target (Node >= 18) — they only run on Node >= 22.7 where module-syntax detection is on by
    default. One-line fix: `web/v2/package.json` = `{"type":"module"}`.
(b) `render.perf.test.mjs` re-implements `patchWindow` (l.36-67) instead of importing
    `views/leaderboard.js::patchTableBody`, so the SHIPPED windowing function is not under test at
    all; a wrong spacer height or key there is invisible to the suite.
Also: `unit.test.mjs:192` pins `personaGateReason("hard_ai_ban")` — the spelling the engine does not
produce, so the test protects the wrong vocabulary.

§8 fail-before analysis: item 1 (coverage/ceiling) real (fixture invariant would fail); item 2 real
(counts test fails on a filtered-counts mutant — killed as M5); item 3 real but unexecuted (Node) —
the Python side kills the equivalent mutant (M1) through the server's allow-list; item 4 NOT
TESTABLE (no delta can render, see B-6); item 5 partial (the tie fixture shape is tested; the tooltip
LEVEL is only covered at levels 1/2/8 by the unexecuted Node test — levels 3/4/5 have no test);
item 6 no test at all (footer is asserted only by a screenshot claim); item 7 partial (no test
renders the drawer/not-ranked states; the not-ranked shell crash B-1 proves it); item 8 unexecuted.
