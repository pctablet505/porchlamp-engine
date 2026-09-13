# W4-5b handoff — UI v2 remaining screens (Matrix, Configure, Compare, Methodology)

Base: `W4-5a` tip `026bb32` at dispatch time; rebased onto `main` tip `d112aa2`
(W4-5a landed with its fix rounds) partway through — see "Rebase" below.
Branch: `W4-5b`. Worktree: `~/Projects/cvi-wt-W4-5b`.

Commits (in order, all on `W4-5b`, HEAD `2fd21aa`):

1. `733284d` — `feat(serve,scripts): W4-5b backend — matrix/methodology/reproject fixture endpoints`
2. `58a3618` — `feat(web/v2): W4-5b shared plumbing — primary nav, state/api/copy additions, app.js wiring`
3. `431eebb` — `feat(web/v2): W4-5b Screen B — Matrix (scatter, quadrants, zoom/pan)`
4. `d0a63e1` — `feat(web/v2): W4-5b Screen D — Configure weights (sliders, presets, deltas)`
5. `e9b4b33` — `feat(web/v2): W4-5b Screen E — Compare (chips, 18-parameter table, verdict)`
6. `a3193a1` — `feat(web/v2): W4-5b Screen F — Methodology and snapshot (zero hardcoded numbers)`
7. `b2f85a1` — `fix(serve): W4-5b post-rebase — rank_delta for reprojected rows`
8. `e5856d1` — `fix(web/v2): W4-5b live-browser verification fixes (Matrix/Configure/Methodology)`
9. `b1a8715` — `docs(W4-5b): handoff — screens B/D/E/F, rebase, live-browser fixes, COULD NOT VERIFY`
10. `2fd21aa` — `fix(web/v2,serve): W4-4 review addendum — surface the API's error detail`

Base sha: `d112aa2` (main, post-rebase). Diffstat `d112aa2..HEAD`: **23 files
changed, ~4700 insertions(+), ~56 deletions(-)** (commit 10 landed after
this file's own commit 9, hence the slight recount).

## Addendum (post-handoff): W4-4 review — API error `detail`

The W4-4 review found `web/v2/api.js` never read the real API's structured
error body (`{"error": {endpoint, status, detail}}`), discarding `detail`
entirely. Folded in as commit `2fd21aa`: `scripts/serve_ui_fixtures.py`'s
`_send_error_json` now sends that exact nested shape; `api.js` parses it
(`ApiError.detail`, tolerating a bare-string `error` too) for both `getJson`
and `postReproject`, and also treats a 2xx response with a non-JSON
Content-Type as an error naming the endpoint; every `copy.js` error-text
function gained an optional `detail` argument rendered as its own sentence
after the endpoint+status, wired through every banner/drawer call site
(leaderboard/notranked/drawer — a third argument on an existing call,
"wiring" per the dispatch's own restriction — plus matrix/configure/
compare/methodology, all mine). New test:
`tests/test_serve_ui_fixtures_w4_5b.py::TestErrorBodyShape` (3 tests,
including a real `--fail snapshot` 500 asserting the detail text appears).
Live-browser verified: a monkey-patched 503 with a synthetic detail
rendered exactly as expected in Screen B's error banner. Also confirmed
(no change needed): `views/configure.js`'s client-side sort already used
the exact `sort=p_<pillar>`/`-p_<pillar>`, `rank/cvi/coverage/tier`-with-`-`
-for-ascending-of-score encoding, since it imports and reuses
`components/table.js::SORTABLE_COLUMNS` rather than redeclaring it.

## Rebase (mid-packet)

Built 6 commits against `W4-5a`'s pre-review tip (`026bb32`). Mid-packet the
coordinator reported W4-5a landed on `main` (`d112aa2`) with its own 4-item
UX fix round (keyed-patch fixes, `focusRowByKey`/`resolveOpenTargetKey`,
evidence fixture renaming, persona-gate vocabulary). Ran
`git rebase --onto d112aa2 026bb32 W4-5b`; only `web/v2/app.js` conflicted
(three hunks — `syncView`'s notranked branch, the footer click handler, the
`cursor-down`/`cursor-up` case), all merged keeping both sides (the fix
round's `isInputFocused` guard / `notRankedRebuildKey` rename, and every
W4-5b addition). `copy.js`, `state.js`, `styles/components.css` auto-merged
clean. Full suite green after the rebase merge, before any new fixes.

**One real contract consequence of the rebase**: the fix round added
`balanced_rank`/`rank_delta` directly to `LeaderboardRow` (Blocker B-6, the
persona-delta column) with a cross-field validator
(`rank_delta == balanced_rank - rank` whenever `balanced_rank` is set).
`reproject_rows()` already set `balanced_rank` but not `rank_delta` — fixed
in `b2f85a1` (also updated two hand-constructed test fixtures).

## Read first (as instructed) — confirmed and applied

`docs/v2/06-UX-DESIGN.md` §3 B/D/E/F, §4, §5, §6/§6.1, §7, §8; `docs/v2/
00-README.md`'s W4-5a rows (owner UX verdict, PASS/FAIL list — the four
FAILs were W4-5a's own to fix, not mine) and the W4-4 row (API being built
in parallel — never landed during this packet; the fixture server remains
the UI's only backend); `.agents/W4-5a/handoff.md`; `web/v2/**`;
`cvi/core/methodology.py` + `cvi/audit/methodology_history.py`;
`cvi/store/queries.py::reproject`; `docs/UI_UX_SPECIFICATION.md`.

## Backend (`cvi/serve/contract.py`, `scripts/serve_ui_fixtures.py`)

### Contract additions (models were missing)

- `MethodologyResponse` + 14 nested models (`MethodologyPillar`,
  `MethodologyParameter`, `MethodologyGate`, `MethodologyMissingness*`,
  `MethodologyTieBreak*`, `MethodologyPersona*`, `MethodologyDetector`,
  `MethodologyProcessConstant`, `MethodologyRfcLogEntry`) mirroring
  `cvi.core.methodology.MethodologyDocument` /
  `cvi.audit.methodology_history.methodology_with_history` field-for-field.
  Validated against the real fixture (see below) — every field name and
  nullability matched on the first pass except needing `MethodologyProxyInfo`
  to allow either sub-field alone.
- `ReprojectRow` (`LeaderboardRow` + `balanced_rank`/`balanced_tie_group`/
  `balanced_cvi`/`balanced_coverage`/`balanced_tier`): `cvi.store.queries.
  reproject` already returns these five balanced-comparison fields but
  `ReprojectResponse` had no field for them — Screen D's "live rank deltas
  vs balanced" side list needs both figures from one response.
  `ReprojectResponse.rows` changed from `list[LeaderboardRow]` to
  `list[ReprojectRow]`.
- `MatrixResponse` gained an `excluded_count >= 0` guard (every other count
  model in the file already had one; this one didn't).

### `scripts/serve_ui_fixtures.py` additions

- `GET /v1/cvi/matrix` — `MatrixPoint`s straight from the loaded leaderboard
  rows (each already carries D-MAT-1 coordinates); `excluded_count` = the
  snapshot's `gated` count; `axis_coverage_caption` names the worst-missing
  contributing pillar per axis at a documented 5% threshold
  (`AXIS_CAPTION_MIN_MISSING_FRACTION`).
- `GET /v1/cvi/methodology` — serves `tests/fixtures/ui/methodology.json`
  verbatim (see "Methodology fixture" below for why it's a static file here
  rather than a live call).
- `POST /v1/cvi/reproject` — `reproject_rows()` mirrors `cvi.store.queries.
  reproject`'s D-MISS-2 renormalisation / D-MAT-2 tier+ceiling / D-TIE-1
  ordering in pure Python over the same fixture rows. **Documented gap**:
  D-TIE-1 levels 6 (TTFHR p75) and 7 (last non-bot commit date) are skipped
  — the API row contract never carries those two fields at all (same
  limitation `components/table.js::decidingTieLevel` already documents for
  the client-side tie tooltip).

### Methodology fixture generation (deviation, reasoned)

`06`/the dispatch's literal text: "methodology.json generated at startup by
calling `cvi.audit.methodology_history.methodology_with_history(...)`."
This branch (based on `W4-5a`, now rebased onto `main`) DOES have
`cvi.audit.methodology_history` available post-rebase — but the fixture file
itself was generated **before** the rebase, via a one-off scratch script
(`~/Projects/cvi-scratch/W4-5b/gen_methodology_fixture.py`, not shipped)
that ran the real function against the **main checkout's editable install**
(no `PYTHONPATH` override) rather than in-process inside
`scripts/serve_ui_fixtures.py`. This matches the exact precedent every other
fixture in this server already follows (`leaderboard.json`, `snapshot.json`,
etc. are all pre-generated static files the server loads once at
`FixtureStore.__init__`, not computed live) — the content is still 100% the
real function's output, never hand-typed. Regenerate with:

```
cd /home/pctablet505/Projects/contributor-value-index
.venv/bin/python /home/pctablet505/Projects/cvi-scratch/W4-5b/gen_methodology_fixture.py
```

(writes directly to this worktree's `tests/fixtures/ui/methodology.json`).
Now that the rebase has landed `cvi.audit.methodology_history` into this
branch's own tree, a future packet could switch `serve_ui_fixtures.py` to
call it in-process at server startup instead — left as-is here since the
static-file pattern is what every sibling fixture already does and changing
it wasn't necessary to unblock Screen F.

## Frontend (`web/v2/`)

Same architecture as W4-5a: pure render functions, keyed patching where data
volume warrants it (`components/patch.js::patchRows`), `copy.js` for every
user-facing string (including the new screens' — enforced by the existing
`tests/test_ui_copy_usage.py`, which caught 5 real dead exports and one
missing-copy-import file mid-build, both fixed rather than silenced), URL =
state, explicit loading/empty/error/partial states per screen.

### Primary nav (`components/primarynav.js`)

New — the Leaderboard/Matrix/Compare/Configure weights/Methodology bar (06
§2), rendered once into its own `#app-primary-nav` node (index.html, between
header and main) and synced via `aria-selected` only. Distinct from
`views/leaderboard.js`'s own `renderTabs` (the Leaderboard screen's
Ranked/Not-ranked SUB-tabs, untouched, "wiring only" per the dispatch).
`tab=` in the URL carries BOTH levels (`ranked`/`notranked` map to the
"Leaderboard" primary entry; `matrix`/`compare`/`configure`/`methodology`
are the other four) — one URL param, matching the dispatch's "with `tab=`
in the URL" instruction literally rather than inventing a second param.

### `w=` and `cmp=` URL params (documented choice, `state.js`)

- `w=`: five comma-separated pillar-weight fractions in `PILLAR_IDS` order
  (responsiveness, criticality, governance, rigor, accessibility), e.g.
  `w=0.6,0.133,0.107,0.08,0.08`. Round-trips through `parseWeightsParam`/
  `serializeWeightsParam`; a malformed or non-summing-to-1 value falls back
  to `BALANCED_WEIGHTS` rather than throwing.
- `cmp=`: comma-separated `owner/name` pairs, e.g.
  `cmp=riverline/httpkit,ivywood/template-cli-71` (06 §3 E's own example
  shape). Not URL-encoded per pair (`/` inside owner/name is not defended
  against, matching the existing `repo=owner/name` param's own
  simplification).

### B. Matrix (`components/scatter.js`, `views/matrix.js`)

Inline SVG scatter (friction x, leverage y), quadrant washes + labels,
colour-blind-safe dual encoding (diamond=Sweet Spot, hexagon=Crucible,
circle=Nursery, cross=Graveyard, using the existing `--quad-*` tokens),
marker size sqrt-scaled from `stars_audited` (`null` → `DEFAULT_RADIUS`,
"unaudited" legend caption). Gated repos excluded with an "N gated excluded"
caption; `axis_coverage_caption` rendered when present. Hover card
(name/CVI/coverage/tier) on hover AND keyboard focus. Click/Enter opens the
drawer (reuses the existing `[data-owner][data-name]` delegated handler —
no new wiring needed there). Zoom via wheel + `+`/`-` buttons, pan via
arrow keys when the stage itself (not a marker) has focus; a Reset view
control; `1`-`4` toggle a quadrant. Keyboard focus moves between markers via
Tab (native DOM order) or arrow keys (spatial nearest-neighbour,
`nearestMarkerInDirection`) when a marker has focus.

**Performance** (06 §7, measured live via `?perfmatrix=N`, Chromium/Claude
Browser tool, reusing `perf-fixtures.js::synthesizeRows` — its rows already
carry leverage/friction/quadrant/stars_audited, a superset of `MatrixPoint`,
so no second synthetic generator was needed):

| N (markers) | Path | Render time |
|---|---|---|
| 2,000 | SVG | 0.5 ms |
| 5,000 | Canvas | 1.8 ms |
| 10,000 | Canvas | 3.2 ms |

All far inside the 150 ms budget (47x margin at 10k). `SVG_MARKER_BUDGET =
2000`; above it, `drawMatrixCanvas` paints every marker on a single
`<canvas>` 2D context (one draw call per marker) with an SVG overlay
(`renderFocusOverlaySvg`) for only the focused/hovered marker, so
keyboard/hover interaction keeps working past the budget without paying the
per-node DOM cost for the rest.

### D. Configure weights (`views/configure.js`)

Five sliders + lock toggles with `normalizeWeights` (proportional
normalisation: locked weights fixed, the others rescale to sum to 1, zero
allowed) debounced ≤150 ms to `POST /v1/cvi/reproject` with the greyed-table
pattern (U-14). Five persona preset buttons (`applyPresetRespectingLocks` —
see fixes below) labelled and always relabelled "Custom" immediately after
(U-11). Live "Rank deltas vs balanced" side list (`topMovers`, top 5 up/down
by `balanced_rank - rank`). The reprojected leaderboard reuses
`components/table.js`'s row renderer (`createRowElement`/`updateRowElement`
via `patchRows`, with a real `previousRow` lookup for correct tie-mark
display) — paginated **client-side** (documented: `/reproject` returns the
whole eligible set in one call, no server-side paging fields at all) and,
since the whole set is already in memory, given a real client-side sort
(`sortReprojectRows`) so the reused sortable header is never a dead
affordance.

### E. Compare (`views/compare.js`)

2-4 repositories via search (debounced across the leaderboard `q=` and
not-ranked lists), chips (`cmp=`), side-by-side identity/gate-status/
composite/coverage, the 18 parameters with the better RAW value marked.
Direction per parameter (`parameterDirection`) is read from the loaded
methodology document's `value_domain` (`higher_is_better` for numeric/ecdf;
a worst-to-best enum order, persona-conditional for `ai_posture`) — **never
hardcoded**, per the dispatch. `betterRepoKey` declines to mark a winner on
a tie or with fewer than two measured values. The verdict
(`computeVerdict`) is generated **only** from parameters measured on every
compared repository and names the ones that weren't, via a win-count; a
tied win count reports as tied. Evidence source shows on hover/focus per
cell. States: empty (0 repos), partial-min (1 repo — below the 2 minimum),
and per-repo loading/error while a chip's identity+evidence fetch is in
flight (a 404 evidence fetch — most repos have no dedicated evidence
fixture — is tolerated as an empty evidence set, not a failed comparison).

### F. Methodology and snapshot (`views/methodology.js`)

Renders `GET /v1/cvi/methodology` (pillars, parameters, gates, missingness,
tie-breakers, personas, detectors, process constants, RFC log) and the
already-loaded `state.snapshot` (id, as_of, manifest root, constants hash,
counts, the exact reproduce command in a copyable `<pre>` with a
clipboard-copy button). The footer's "verify" link now routes here
(`app.js::goToTab("methodology")`) instead of a bare same-page anchor jump.

**Binding rule enforced literally** ("every number comes from the payload"):
every formatting decision (percentages, decimal places, hash display) is
pushed into a `copy.methodology*` helper in `copy.js`, so the view file
itself carries **no numeric literal standing in for a methodology fact**.
`tests/test_ui_methodology_no_literals.py` extracts every genuine numeric
literal token from the file's live code (comments, the shared `escapeHtml`
boilerplate, and template-literal STATIC text stripped, while still scanning
inside `${...}` expressions) and asserts none remain — plus a synthetic
positive/negative control (`TestDetectorItself`, 5 cases) proving the
detector actually detects an injected literal rather than always passing on
this one file. Live-browser verified: every one of the 18 parameters', 7
gates', 5 personas', 9 detectors' real numbers rendered correctly from the
real fixture document (see "Live-browser verification" below).

## Keymap (`components/keymap.js`, `copy.js`)

Added: `matrix-reset-view` (`R`), `matrix-focus-{up,down,left,right}`
(Arrow keys — dual-purpose: pan the stage when the stage itself has focus,
or move to the nearest marker in that direction when a marker has focus,
documented in `views/matrix.js`'s own module docstring), `nav-compare`/
`nav-configure`/`nav-methodology` (`C`/`W`/`M`). `1`-`4` (already reserved by
W4-5a as placeholders) now do real work (`MATRIX_TOGGLE_QUADRANT`, only when
`state.tab === "matrix"`). All documented in `copy.KEYMAP_ENTRIES` (the `?`
overlay) — live-verified rendering all 15 rows correctly.

## Tests

- `tests/test_ui_fixtures_w4_5b.py` — contract validation for the three new
  payloads (matrix, reproject, methodology), including exact-value checks
  (18 parameters, 8 tie-break levels, 5 personas, 9 detectors, 3 process
  constants — house style: shape-only tests are a defect).
- `tests/test_serve_ui_fixtures_w4_5b.py` — endpoint behavior + **reproject
  parity against a hand oracle** across 6 weight vectors (including two
  zero-pillar ones), a deliberately differently-styled (more literal,
  loop-heavy) independent reimplementation so a shared bug would have to be
  the exact same bug, not the same code shape; matrix exclusion/caption unit
  tests; methodology passthrough.
- `tests/test_ui_methodology_no_literals.py` — the no-numeric-literal grep
  (7 tests: 1 on the real file, 6 synthetic detector self-tests).
- One existing test updated for the `ReprojectRow` field addition
  (`test_ui_fixtures.py`), two more for the post-rebase `rank_delta` field.
- New JS files are picked up automatically by the existing
  `tests/test_ui_copy_usage.py` (glob-based, no edit needed) and
  `tests/test_ui_no_external_resources.py` (glob-based) — both pass;
  `test_ui_copy_usage.py` caught 5 real dead `copy.js` exports and one
  missing-`copy.js`-import file (`components/scatter.js`) mid-build, each
  traced to an actual gap and fixed rather than silenced (see below).
- No new `tests/test_ui_contrast.py` token pairs were needed — every new
  screen reuses the existing `--quad-*`/`--bg-*`/`--text-*` tokens rather
  than introducing new colour tokens.

**Suite**: `env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m
pytest -n 4 -p no:cacheprovider` → **2504 passed, 1 skipped**. Ruff clean
on every Python file touched.

**Node**: still absent on this box (confirmed again). No `web/v2/tests/
*.test.mjs` additions for the new pure functions (`normalizeWeights`,
`applyPresetRespectingLocks`, `nearestMarkerInDirection`, `computeVerdict`,
`betterRepoKey`, `sortReprojectRows`) — matching W4-5a's own documented
fallback, these functions are pure and hand-traceable, and (unlike W4-5a)
every one of them was also exercised **live** in a real Chromium tab via
direct DOM/state inspection (see below), which is strictly more than a
hand-trace.

## Live-browser verification (Chromium via the Claude Browser tool)

Fixture server on **port 8902** (not 8899 — reserved for the lead's W4-5a
session per the dispatch):

```
cd /home/pctablet505/Projects/cvi-wt-W4-5b
PYTHONPATH=$PWD /home/pctablet505/Projects/contributor-value-index/.venv/bin/python \
  scripts/serve_ui_fixtures.py --port 8902
```

All four new screens loaded and driven live, with **six real bugs found and
fixed** during this pass (all in commit `e5856d1`, full descriptions there):
marker radii ~10x too large (viewBox-unit vs. pixel confusion), arrow-key
matrix navigation using a leverage sign backwards from the screen's Y-flip,
quadrant toggle never touching the rendered SVG, the >2000-marker canvas
path never actually being painted, the Methodology Snapshot section racing
against its own fetch and sometimes never appearing, an unhandled promise
rejection on a denied Clipboard permission, and Configure's persona presets
ignoring weight locks. Each was found by genuinely interacting with the
running app (state/DOM inspection via `javascript_tool`, not just code
review), not asserted from reading the source.

**Environment quirk** (same class as W4-5a's own documented one): this
sandboxed browser's `computer{action:"hover"}` does **not** generate a real
`mouseover` DOM event (confirmed: `elementFromPoint` shows the cursor
correctly over the target element, but no `mouseover` fires) — hover-card/
marker-focus behavior was instead verified by dispatching a real
`MouseEvent('mouseover', {bubbles:true})`, which DOES exercise the exact
same listener a real user's hover would. Keyboard `focus()`, `click`
(`MouseEvent('click')`), and `keydown` dispatch all worked correctly via the
`computer` tool directly (Matrix tab click, primary-nav clicks, and slider
drags via `form_input` were all driven through real automation, not just
JS dispatch).

Verified live and working: primary nav (all 5 tabs, `aria-selected`
correct); Matrix (73-point fixture renders all 4 quadrants at correct
sizes; hover card + marker-focus highlight; quadrant toggle add/remove
markers + chip `aria-pressed`; arrow-key spatial navigation walks a
consistent trail; click-to-open-drawer); Configure (slider drag →
`normalizeWeights` → debounced reproject → table + delta-list update, sum
always 100%; lock + preset interaction now correct; pagination/sort);
Compare (search → add chip → per-repo fetch → table with correct
better-value marks and evidence-source titles → correct verdict text
naming unmeasured parameters); Methodology (every section's real numbers,
all `<details>` expand/collapse, copy button no longer throws); keymap
overlay (`?`) renders all 15 documented shortcuts; `C`/`W`/`M`/`1`-`4`
keyboard navigation; footer "Verify" link routes to Methodology; drawer
opens correctly from the Leaderboard (regression check post-rebase).

## §8 acceptance checklist — new-screen mapping

| # | Item | Evidence for B/D/E/F |
|---|---|---|
| 1 | Every row shows coverage; C<0.95 shows ceiling | Configure's reused table (same `renderCoverageBadge`/`renderTierCell`); Compare's Coverage row; Matrix hover card |
| 2 | Gated/unrated counts visible, match Not-ranked | Matrix's "N gated excluded" caption = `snapshot.counts.gated` (`test_serve_ui_fixtures_w4_5b.py::TestMatrixEndpoint`) |
| 3 | Vanity columns have no sort affordance | Configure's reused header (`SORTABLE_COLUMNS`, unchanged) |
| 4 | Persona switch: deltas shown, hidden count matches | N/A to B/D/E/F directly (A/A2's own item); Configure's OWN delta list is the D-specific analogue (`topMovers`, live-verified) |
| 5 | Tie rows carry "=" and tooltip | Configure's reused tie-mark logic with a real `previousRow` lookup (fixed from a naive `null` in the first draft) |
| 6 | Footer shows snapshot id/version/hash/root | Unchanged (app.js `syncFooter`, live-verified present on every new screen too) |
| 7 | All four states render from fixtures | `shellShape`/`renderShell` per screen (loading/error/empty/body — Compare additionally has "partial-min"); each has a dedicated Python contract/behavior test; live-verified for Matrix/Configure/Compare/Methodology |
| 8 | Keymap works for every key; focus returns on Esc | `1`-`4`/`R`/arrows/`C`/`W`/`M` all live-verified firing the correct action; Esc-closes-keymap live-verified |
| 9 | Contrast test passes for both themes | No new tokens introduced — existing `tests/test_ui_contrast.py` unchanged and still passing |
| 10 | Zero external requests | `tests/test_ui_no_external_resources.py` (glob-based, covers the new files automatically) — passing |

Packet-specific requirements: matrix scatter shape+colour encoding, size
mapping, NULL-stars legend note, SVG/canvas switch at budget — all
implemented, tested (Python contract + live browser), and the perf budget
measured with real numbers (table above). Weight normalisation with locks
(sum stays 1, locked unchanged, zero allowed) — `normalizeWeights`,
live-verified via direct slider manipulation. Compare verdict from
measured-only params — `computeVerdict`, live-verified against the two
repos that actually carry evidence fixtures. Methodology zero-literal
rendering — enforced by a dedicated grep test with a self-test, and
live-verified against the real payload.

## Rules followed

`PYTHONPATH=$PWD` for every Python command. Stayed out of `cvi/**` except
`cvi/serve/contract.py` additions (named above, both new models). Stayed out
of `views/{leaderboard,notranked,drawer}.js` beyond the same kind of
"wiring" the dispatch anticipated (app.js calls into their existing exports
unchanged; the one drawer-adjacent finding below is reported, not fixed, for
exactly that reason). Never touched `data/`. No network, no CDN — confirmed
by the existing external-resources test. Did not start a server on 8899 —
used 8902, stated above and in every command shown. Committed on `W4-5b` in
logical commits (one per screen, plus backend/plumbing/post-rebase-fix
commits — the shared `app.js` wiring for all four screens is one commit
rather than split per screen, since it is genuinely cross-cutting router
infrastructure like `state.js`/`copy.js`, not screen-owned code; noted as a
reasoned deviation from a literal "one commit per screen" reading).

## Found but NOT fixed (reported instead, per scope)

**Drawer row lookup breaks for repos outside the loaded leaderboard page**
(spawned as a background task, `task_8ffa02ff`, also summarized here):
`views/drawer.js::findRow` only searches `state.leaderboard.rows` (the
CURRENTLY LOADED PAGE, default 20 of 73 rows) and `state.notRanked`. This
was correct when the drawer could only be opened from Screen A (where the
clicked row is always on the rendered page) but Matrix/Compare/Configure
each maintain their OWN separate full-or-partial row data
(`state.matrix.points`, `state.compareDataByRepo`, `state.reproject.rows`)
that `findRow` never consults. Reproduce: load `?tab=matrix` fresh (so
`state.leaderboard` never loads a page containing every repo) and click a
marker whose repo isn't on page 1 of the default-sorted leaderboard — the
drawer opens with the right title but renders `copy.emptyLeaderboardText(0,
0)` (a leaderboard-empty-state sentence, wrong both semantically and
factually) instead of the repo's real data. Root cause and a design-neutral
fix sketch are in the spawned task; not fixed here because it requires
changing `views/drawer.js`'s own row-resolution logic, which the dispatch
reserves for the W4-5a lineage, and a shortcut (splicing a fetched row into
`state.leaderboard.rows`) would risk corrupting that field's pagination
semantics for the actual Leaderboard screen.

## COULD NOT VERIFY (needs a real, focused desktop browser — the lead verifies)

- Real mouse-hover-driven (as opposed to keyboard-focus-driven) marker
  highlighting and hover-card display in an actual pointer-driven browser —
  this sandbox's synthetic hover does not fire a native `mouseover` (see
  above); the underlying handler is proven correct via direct event
  dispatch, matching a real hover exactly, but "does it feel right to a
  human moving a real mouse" is unverified.
- Real drag interaction on the weight sliders (verified via `form_input`
  setting the value + the resulting `input` event, and via direct state
  inspection after — not an actual click-and-drag gesture).
- The Compare search input's caret position across a full re-render on
  every keystroke: `COMPARE_SET_QUERY` triggers a full `main.innerHTML`
  rebuild of the Compare screen (the picker's results list changes on every
  keystroke) with a refocus of the search input afterward — refocus is
  live-verified working (focus never lands on `<body>`), but the exact
  caret position within the input after a rebuild was not checked pixel-
  by-pixel; a minor UX papercut if the browser resets it to the end,
  never a data-loss bug.
- `?perfmatrix=` canvas-path visual correctness at a real 1x display DPI
  (screenshots here are taken through the sandboxed pane's own scaling —
  the pixel-count check confirming real paint output is DPI-independent,
  but a crisp/blurry rendering comparison was not done).
- Below-768px card-list / mobile layout for the four new screens (none of
  them have a dedicated mobile treatment beyond the existing responsive
  CSS breakpoints already in `styles/*.css` — not exercised at a real
  narrow viewport).
- Dark/light theme toggle's visual appearance on the four new screens
  specifically (the shared tokens are already contrast-tested; the new
  screens' own layouts were only ever viewed in the default theme during
  this session).
- The exact behavior of two DIFFERENT concurrent browser sessions
  (mine on 8902, the lead's on 8899) sharing the SAME automated-browser tab
  pool: a tab I opened was observed to later report a DIFFERENT origin
  (8899) than I navigated it to, apparently due to tab-id reuse/renumbering
  across the shared environment — I stopped using that tab and opened a
  dedicated new one for the rest of this session's testing, but did not
  investigate further since it is outside this packet's scope; flagged here
  in case it recurs for the lead's own verification pass.

## Addendum 2: follow-on round — lead browser FAILs + Opus code review Blockers

Commits `70b4254`→rebased to `314ce2c`. Rebased onto `main` tip `3ed230f`
(W4-4/W4-6 landed) — 12-commit rebase, zero conflicts. Full suite post-rebase:
**2521 passed, 2 skipped**. Ruff clean.

Lead's four live-browser FAIL items, the three Opus review Blockers
(B-1/B-2/B-3), and review items (a)/(b) are all fixed — see commit
`314ce2c`'s own message for the complete, itemised account (each item:
what was wrong, the fix, and how it was verified — live-browser dispatch,
a hand-traced+live-verified property test, or a mutation-killed Python
test). Reviewer records are committed at `.agents/W4-5b/review/R-01..R-05`.

Also fixed while addressing the above (found along the way, low-risk):
a `?repo=owner/name` URL never dispatched the evidence/record fetch at
boot or via `popstate` restore (only the click path did) — new pure
`state.js::repoToLoadFromState`, wired into both paths, live-verified.
Several of the review's non-blocking notes (N-1/N-2 doc + focus-overlay-
size fixes, W-5's enum-ranking bug in Compare, M3/M6 test-quality gaps)
were also closed since they were quick and directly adjacent to code
already being edited.

**Deferred, not fixed this round** (documented as a reasoned scope
boundary, matching the "REWORK bounded to 3" framing): W-2 (the
methodology.js no-literal grep guard has two theoretical bypasses —
braced template expressions, quoted strings — though the file itself is
clean today); W-4 (Compare's search input still rebuilds `main.innerHTML`
per keystroke, a U-12 violation the implementer already flagged as
COULD-NOT-VERIFY; focus-restore is confirmed working, residual risk is
caret position only); W-6 (`api.js::postReproject` duplicates ~25 lines of
`getJson`'s request/error-handling shape — a pure refactor, no behavior
change); W-7 (`methodology.json` is a pre-generated static fixture, not
diffed against a live `methodology_with_history()` call in CI, so it
could silently drift — now that the module IS importable in-tree
post-rebase, this is more feasible than when W4-5b started).

Fixture server kept on **port 8902** throughout, restarted after each
batch of changes; final live pass (fresh tab, zero prior state) confirms
zero console errors on load and correct rendering of all five primary
tabs.

## Round 3 (final, bounded to exactly three items)

Base at dispatch: `adfec73` (round-2 tip, rebased onto `3ed230f`). Read
first: the round-2 review (`/tmp/.../scratchpad/reviews/W4-5b-r2-review.md`
plus `.agents/W4-5b/review/round2/R2-01..R2-04`, committed this round in
`bea2806` — they were sitting untracked in the worktree since round 2).
Delivered exactly the three items the round-2 review scoped: **NB-1**
(Blocker), **N1**, **N3** — plus one unplanned, unavoidable item the
mandatory main-rebase surfaced (below).

### NB-1 (Blocker) — Compare's per-keystroke `main.innerHTML` — commit `897d322`

`compareQuery` was baked into the Compare shell's rendered HTML, so
`app.js::syncView`'s `html !== currentCompareShape` string-compare fired
on every keystroke and destroyed/recreated the focused search `<input>`
(06 §6.1 U-12's exact prohibition). Fixed in `views/compare.js`:
`renderPickerShell` (the `<input>` + an empty `[data-compare-results-host]`
container, rendered only as part of the shell) is now separate from
`renderPickerResults` (the query-dependent results list, patched into that
host on every keystroke); `shellKey(state)` — the new rebuild-decision
function — is deliberately a pure function of ONLY `shellShape` and
`compareRepos`, never `compareQuery`/`compareSearchResults`/
`compareDataByRepo`. The comparison TABLE moved out of the inline shell
string into its own `[data-compare-table-host]`, patched by `patchTable`,
so a per-repo data fetch landing doesn't force a shell rebuild either.
`app.js::syncView`'s compare branch now compares `shellKey` (matching
every other screen's shape-string pattern) instead of the rendered HTML
string.

**Live-verified** (Chromium via the Claude Browser tool, fixture server on
8902, real `computer{action:"type"}` keystrokes plus direct DOM
inspection): tagged the search input with a marker attribute, typed 3
characters — marker survived every keystroke (same node, never
recreated); set the caret to a mid-string position (`setSelectionRange(1,
1)`) and typed one more character — it inserted AT the caret (`"riv"` ->
`"rXiv"`) with the caret landing right after it, not appended at the end.
Contrast case also verified: adding a chip DOES swap in a new input node
(shell rebuild, as intended) and resets the query. Full add-two-repos ->
table + verdict sequence re-verified working through `patchTable`.

### N1 — ship an executable guard for the B-1 normaliser fix — commits `6c35a5a`, part of `979baa6`

`web/v2/package.json` (`{"type": "module"}`) — the missing piece blocking
`node --test web/v2/tests` for three rounds (the `.js` SOURCE files'
bare `import`/`export` need a package.json declaring ESM somewhere up the
tree; `.mjs` test files are unaffected by this but their imports aren't).
`web/v2/tests/cases/normalize_weights.json` — 6 `normalizeWeights` + 3
`applyPresetRespectingLocks` EXACT-VALUE cases (not just properties)
covering every branch (full-lock snap-back = the exact B-1 repro, plain
proportional split, the overflow correction, the free-pillars-at-0 equal
split, both clamp directions) — asserted identically by BOTH
`web/v2/tests/unit.test.mjs` (the real JS) and the new
`tests/test_ui_weights_port.py` (a faithful, docstring-annotated Python
port, plus the round-2 reviewer's own lock-subset/random-walk property
test folded in, plus a fail-before self-test proving the four-locked case
is actually load-bearing). **Live-verified in a real V8 engine** (not just
Python): dynamic `import('/views/configure.js')` + `fetch('/tests/cases/
normalize_weights.json')` against the running fixture server (Node itself
still absent) — all 6 + 3 cases produce zero failures against the REAL
implementation, not just the port.

### N3 — repo-detail 404 path untested (and dead-coded) — commits `7e71d3e`, part of `979baa6`

`tests/test_serve_ui_fixtures.py::TestRepoDetailEndpoint` (new): a known
repo resolves, an unknown one is a genuine 404 with the nested error body,
a malformed path is 404 not 500. Writing the RENDER-side test surfaced a
real bug the 404 test alone wouldn't have: `views/drawer.js::renderDrawer`
routed EVERY `repoDetailError` — a 404 included, since `api.js`/
`app.js::loadRepoDetail` treat every non-2xx the same way — through the
retry-error banner, making `copy.DRAWER_NOT_FOUND_TEXT` dead code for the
one case it names. Fixed: the not-found branch now checks
`status !== 404` before choosing the retry banner. **Live-verified**: `GET
/v1/cvi/repos/nobody/nothing` returns 404 with the expected body; `?repo=
nobody%2Fnothing` renders the drawer with exactly the not-found copy and
no retry button (real Chromium tab, real navigation, `#app-drawer-root`
inspected directly); all three `renderDrawer` cases (404 / non-404 /
loading) also re-run against the real module via dynamic `import()`.

### Live-execution technique used across all three items (worth noting for future rounds)

Node remains absent on this box, but `web/v2/views/*.js` and
`web/v2/api.js`/`state.js`/`copy.js` carry NO `node:`-prefixed imports —
only `web/v2/tests/*.test.mjs` does (`node:test`, `node:assert/strict`).
That means the real application modules ARE plain, dependency-free ES
modules the fixture server already serves as static files, so dynamic
`import('/views/compare.js')` (etc.) inside a live Chromium tab (via the
Claude Browser tool's `javascript_tool`) executes the REAL, unmodified
source in a real engine — strictly stronger evidence than a hand-trace,
and closes a real gap the round-2 review's own N1 finding named (an
unexecuted test proves nothing about a live regression). This does not
make `unit.test.mjs` itself executable (its `node:*` imports still need
real Node, hence N1's `package.json` fix for whenever that exists) — it's
a verification technique for THIS session, not a new CI capability.
`tests/fakedom.mjs` (W4-5a's hand-built minimal DOM) was considered for a
real DOM-level NB-1 test but does not parse `innerHTML` strings into
elements at all (confirmed by reading its source) — it only backs the
keyed-patch tests earlier in `unit.test.mjs`, so it cannot exercise
`main.innerHTML = ...` diffing; the live-Chromium technique above is what
actually closed that gap for this round's evidence.

### Unplanned item: rebase fallout — two branches' independent contract.py changes collided silently — commit `28f6783`

Rebasing onto main (`752d5ec` -> current tip, per the dispatch's "752d5ec
or later" — pulled in W4-4's real `cvi/serve/app.py`/`guard.py` for the
FIRST time onto this branch) succeeded with **zero textual conflicts**
across all 18 commits. That silence was misleading: `tests/serve/
test_api_v2.py` (main's own 50-test suite for the real API) went from
50 passed to 7 failed the moment this branch's own contract.py commits
landed on top, because the merge auto-combined two independently-designed
additions to `cvi/serve/contract.py` with no textual overlap but a real
semantic collision. Diagnosed (pristine `main` in a scratch worktree
confirmed 50/50 passing — this was NOT a pre-existing failure) and fixed
all three root causes, none touching `cvi/store/**` or any of this
round's three dispatched items:

1. **Duplicate `class RepoDetailResponse`** in `contract.py`: W4-4's own
   (a flat, all-nullable model matching its real served response) and
   this branch's (a `{kind, row}` discriminated union, never actually
   imported/validated against anywhere — the fixture server builds that
   shape as a plain dict) shared the same name. Python binds whichever
   definition is LATER in the file, so `app.py`'s real route validated
   against the WRONG one and 500'd on every `/repos/{owner}/{name}` call.
   Renamed this branch's to `RepoDetailFixtureResponse` (kept as
   documentation of the fixture/frontend's current shape, not deleted);
   fixed the resulting duplicate `__all__` entry; updated `web/v2/api.js`'s
   doc comment.
2. **`/matrix`'s stale `axis_coverage_caption` kwarg** in `app.py`: this
   branch's own round-2 B-2 fix renamed the field to
   `axis_coverage: dict[str, MatrixAxisCoverage]`; `app.py`'s
   `get_matrix()` (written before that fix existed, per its OWN docstring
   item 11 explicitly handing the numbers field to W4-5b) still passed the
   old name, and `StrictModel`'s `extra="forbid"` turned that into a 500.
   Fixed the kwarg name; the VALUE stays `{}` (an honest gap, documented —
   `cvi.store.queries.matrix_points` only returns one combined count/share,
   not the per-axis/per-pillar breakdown `MatrixAxisCoverage` needs; wiring
   a real value needs that query extended, out of this round's scope).
3. **`_reproject_row` returned a bare `LeaderboardRow`** where
   `ReprojectRow` (a `LeaderboardRow` SUBCLASS this branch added) was
   required — a parent-class instance never satisfies a subclass-typed
   field, so every `/reproject` call 500'd on the four missing
   `balanced_tie_group`/`balanced_cvi`/`balanced_coverage`/`balanced_tier`
   fields (which `cvi.store.queries.reproject` already returns on
   `record` — just never wired through). Fixed by widening the shared
   `_project_leaderboard_row` result into a real `ReprojectRow`.

Also fixed the one adjacent test-scope gap the rebase surfaced:
`test_ui_fixtures_validate_against_the_api_contract_models` (main's own
fixture/contract drift guard) had no mapping for `methodology.json` (a
fixture that didn't exist when that test was written) — added, validated
against `MethodologyResponse`.

**Live end-to-end verification** against a real `create_app()` instance
(not the fixture server): `/v1/cvi/matrix` (200), `POST /v1/cvi/reproject`
(200, full `ReprojectRow` with all five balanced_* fields), `GET /v1/cvi/
repos/{owner}/{name}` (200 known repo, 404 unknown repo with the nested
error body) — script and output in this round's commit message.

### Suite, ruff, rebase

Rebased onto `main` tip `1e88a85` (a descendant of the dispatch's
`752d5ec`) via `git rebase --onto main 3ed230f W4-5b` — 18 commits, zero
conflicts (the semantic collision above was invisible to git, only to the
test suite). Full suite: `env -u PYTEST_ADDOPTS PYTHONPATH=$PWD
/home/pctablet505/Projects/contributor-value-index/.venv/bin/python -B -m
pytest tests -p no:cacheprovider -n 4` -> **2602 passed, 1 skipped**
(main's pre-rebase count was 2521 passed, 2 skipped; +11 new tests this
round, +7 fixed-and-now-passing from the reconciliation, and one
pre-existing "waiting on W4-4" skip now runs for real since W4-4 has
landed). Ruff clean on every file touched (`ruff check .` — whole repo).

### Rules followed

`PYTHONPATH=$PWD` for every Python command. Never started a server on
8899/8902/8791 for ad-hoc testing (a spare smoke-test script ran in-process
via `TestClient`, no server at all); 8902 was already running this
worktree's tree (pid confirmed via `readlink /proc/<pid>/cwd`) throughout
this round's editing, then explicitly restarted at the very end from this
commit (below) so it serves the final, committed state. Never `git stash`/
`git checkout --`. Never touched `data/` or main's working tree. No
network, no CDN.

### Final state

Commit `28f6783` (HEAD), base `1e88a85` (main). Diffstat `1e88a85..HEAD`
(this round's own 6 commits, on top of the 12 pre-existing ones this
branch already carried through the rebase):

```
 9 files changed, ~900 insertions(+), ~30 deletions(-)
```

(`.agents/W4-5b/review/round2/*` (4 files, docs), `cvi/serve/app.py`,
`cvi/serve/contract.py`, `tests/serve/test_api_v2.py`,
`tests/test_serve_ui_fixtures.py`, `tests/test_ui_weights_port.py` (new),
`web/v2/api.js`, `web/v2/app.js`, `web/v2/package.json` (new),
`web/v2/tests/cases/normalize_weights.json` (new), `web/v2/tests/
unit.test.mjs`, `web/v2/views/compare.js`, `web/v2/views/drawer.js`.)

Suite: **2602 passed, 1 skipped**. Ruff: clean. Fixture server: **port
8902**, restarted from this exact committed tree after the final commit
(see the command in this handoff's own "Live-browser verification"
section above — same invocation, same port).

**COULD NOT VERIFY** (unchanged from round 2, still genuinely needing a
real desktop browser): real mouse-hover-driven highlighting (this
sandbox's synthetic hover still doesn't fire a native `mouseover`);
real click-and-drag on the weight sliders; `?perfmatrix=` canvas rendering
at real 1x DPI; below-768px mobile layout; dark/light theme toggle's
visual appearance on the four new screens specifically. NB-1's own
caret/node-identity claim, previously in this list, is now RESOLVED (see
above) — the one item round 2 asked to close by this exact criterion is
no longer a COULD-NOT-VERIFY.
