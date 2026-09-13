# R-05 Mutants, suite/merge, size, absence table

## Python mutation battery — 6/12 killed (driver: `scratch/W4-5b-review/mutants5b.py`)
Copy of `2fd21aa`; 119-test packet suite per mutant; tree restored GREEN afterwards.

| id | mutation | result |
|---|---|---|
| M1 | matrix `excluded_count` always 0 | KILLED |
| M2 | axis-caption threshold 0.05 → 0.99 (no caption ever) | KILLED |
| M3 | matrix plots rows with `quadrant is None` | **SURVIVED** |
| M4 | reproject numerator drops the D-MISS-2 `c_p > 0` guard | **SURVIVED (equivalent here)** |
| M5 | reproject coverage counts only measured pillars | **SURVIVED (equivalent here)** |
| M6 | reproject rates every row (`RATED_MIN_COVERAGE` 0.60 → 0.0) | **SURVIVED** |
| M7 | reproject `rank_delta` sign flipped | **SURVIVED** |
| M8 | `MatrixResponse` non-negative `excluded_count` guard removed | KILLED |
| M9 | a hardcoded number planted in `methodology.js` (control) | KILLED |
| M10 | a literal hidden in a braced template expression | **SURVIVED** |
| M11 | error body flattened out of `{error:{...}}` | KILLED (5 failures) |
| M12 | reproject D-TIE-1 order → owner-alphabetical | KILLED (7 failures) |

Survivor analysis:
- **M7 is the important one.** The only `ReprojectResponse.model_validate` in the suite
  (`test_ui_fixtures_w4_5b.py:123-129`) builds a row by hand with `balanced_rank = rank` and
  `rank_delta = 0` — the single value at which a sign flip is invisible — and the live `/reproject`
  response is never validated against the contract at all. The contract's own
  `rank_delta == balanced_rank - rank` validator would kill this mutant instantly if the parity test
  validated the real response. This is the same sign convention W4-4 was reported to have backwards.
- **M6**: no parity vector crosses the 0.60 rating floor. My own "tilted" vector
  (.05/.35/.05/.05/.50) drops exactly one row (72 vs 73) — adding it kills the mutant.
- **M3**: no test asserts that a row with unmeasured coordinates is excluded from the plot.
- **M4/M5**: equivalent on the current fixture (no row has `c_p > 0` with `p_p is None`, nor
  `c_p == 0` with `p_p` set). A single fixture row of that shape turns both into real tests.
- **M10**: the no-literal detector bypass (R-03 W-2).

## JS mutants — 6 hand-traced (Node absent: no node/deno/bun/qjs on this box)
| # | mutation | caught? |
|---|---|---|
| J1 | `normalizeWeights`: drop the `free.length === 0` re-pin | already broken — my property test catches it; no shipped test does |
| J2 | quadrant toggle: set `hidden[q] = false` instead of `delete` | NOT caught — `shellShape`'s key would stop changing and the toggle would silently stop re-rendering (the exact bug they fixed) |
| J3 | `toSvgY`: return `leverage` (drop the Y-flip) | NOT caught — nothing asserts the flip; the whole plot would mirror |
| J4 | `SVG_MARKER_BUDGET` 2000 → 0 (always canvas) | NOT caught — no test exercises either path |
| J5 | `computeVerdict`: count unmeasured params as wins | NOT caught — no runnable test |
| J6 | `parseCmpParam`: split on ";" instead of "," | NOT caught — no runnable test |
None of the six would be caught by an executable test; all six live in functions the handoff itself
calls "pure and hand-traceable". W4-5a's W-5 (no `web/v2/package.json` `{"type":"module"}`, so
`node --test` cannot resolve the `.js` imports) is still open and still the single highest-value fix:
it would make every one of these testable.

## Suite, merge trial, size
Branch suite at `2fd21aa` (clean worktree):
`env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest tests -p no:cacheprovider -n 4`
→ **2507 passed, 1 skipped, 12 warnings in 13.81s** — the claim is exact. (The handoff's own "2504"
predates commit 10's three new tests.)
Merge trial: `git worktree add --detach <scratch>/merge main` (main tip **4005d7d**), cherry-pick all
eleven commits — **clean, no conflicts** — then the full suite: **2507 passed, 1 skipped in 13.65s**.
Size: ~4,700 insertions. `methodology.json` 699 lines (15%); the four new views + two components
1,316; new tests 603; handoff 448; the rest is `contract.py` (+210), the fixture server (+305),
`app.js` (+597), `copy.js` (+248), CSS (+388). Subtractable: `postReproject`'s duplicated transport
(~25 lines, W-6) and `escapeHtml`, now copy-pasted into **twelve** files.

## Absence table (dispatch + the two addenda)
| Asked | Present? |
|---|---|
| Screens B/D/E/F, primary nav, keymap entries, fixture endpoints | YES — all four screens, `components/primarynav.js`, 14 documented shortcuts incl. R / arrows / C / W / M / 1–4, and matrix+methodology+reproject endpoints |
| SVG ≤ 2,000 / canvas above, identical encoding | YES for encoding (both paths draw all four shapes); NO for interaction above the budget (W-3) |
| Shape AND colour, size from `stars_audited` + NULL default & legend note | YES |
| Gated excluded + caption; axis-coverage caption from payload numbers | Excluded-count YES; the axis caption is a SENTENCE composed in Python, not numbers rendered by the UI (Blocker B-2) |
| Weight normalisation with locks, presets respecting locks, `w=` round-trip, "custom" everywhere, debounce ≤ 150 ms, greyed table | ALL YES except the sum invariant under four locks (Blocker B-1) |
| Compare 2–4 via chips + `cmp=`, direction from the methodology doc, verdict from measured-only, evidence on hover | YES |
| Methodology: every number from the payload, snapshot section, copyable reproduce command, footer link | YES (guard weaker than claimed, W-2) |
| API error `detail` parsed and surfaced (addendum) | YES |
| `tab=` in the URL for the five tabs | YES (`VALID_TABS`, six values incl. the `notranked` sub-tab) |
| Zero external requests / copy.js coverage / contrast | YES — all three existing glob-based tests cover the new files and pass |
| Drawer `findRow` (addendum: now IN scope) | NOT DONE — reported only (R-04 gives the minimal change; the server half already exists) |
