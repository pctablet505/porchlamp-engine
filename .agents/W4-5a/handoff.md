# W4-5a handoff — UI v2 core (leaderboard, not-ranked, drawer, keymap)

Base: `43f9f4f`. Rebased onto `main` tip `acdf885` before this handoff.
Branch: `W4-5a`. Worktree: `~/Projects/cvi-wt-W4-5a`.

Commits (in order):

1. `44b1d67` — `feat(serve): W4-5a contract-first wire models + UI v2 fixtures`
2. `313c828` — `feat(web/v2): UI v2 core — leaderboard, not-ranked, drawer, keymap`
3. `c99e324` — `test(ui): W4-5a Python suite — fixture/contract, server, contrast, copy`

Diffstat vs `main`: 38 files changed, 15,692 insertions(+), 1 deletion(-)
(the one deletion is `pyproject.toml`'s old `[tool.setuptools] packages`
line, replaced by the merged list — see "pyproject.toml" below).

## IMPORTANT: two rounds of binding rework landed mid-packet

While this packet was in progress the lead reproduced the owner's verdict
("the UI is cool, the UX sucks, too slow and unresponsive, and not working
properly") on the first build and issued a rendering-architecture ruling
(U-12..U-19, now in `docs/v2/06-UX-DESIGN.md` §6.1). Immediately after, the
owner issued a second, verbatim instruction that **replaced** that ruling's
"no pagination" clause (U-13) with real pagination (20/50/100/500/1000,
default 20). **Everything described below is the FINAL state** — the
no-pagination architecture was built, verified, then superseded; nothing
from that intermediate state remains except the keyed-DOM-patch mechanism
underneath it (U-12), which both rounds kept.

## Where v1 lives (untouched)

- `web/index.html` — a single 3,289-line file, offline single-page app.
- `scripts/serve.py` mounts it at `/` via `StaticFiles(directory=web/)`
  alongside `porchlamp.api.create_app()`.
- `scripts/demo_app.py` seeds 26 fixture repos for it.
- `tests/test_webapp.py::TestIndexServed::test_page_is_fully_offline` is
  the existing zero-external-request check the dispatch asked me to find;
  it inspects `web/index.html` alone. I did **not** edit it (v1 is deleted
  whole by W4-1, not touched piecemeal) — `tests/test_ui_no_external_resources.py`
  extends the same rule to every file under `web/v2/`.

I built under `web/v2/` per the dispatch's instruction (v1 already occupies
`web/`). Nothing in `porchlamp/core/**`, `porchlamp/store/**`, `porchlamp/collect/**`,
`scripts/serve.py`, `web/index.html`, or `scripts/demo_app.py` was touched.

## Contract (`porchlamp/serve/contract.py`)

Pydantic models, free of I/O (no `porchlamp.store`/`porchlamp.collect`/`porchlamp.core`
imports), importable by both a future FastAPI app and the stdlib fixture
server:

- `LeaderboardRow`, `NotRankedRow`, `EvidenceRow`, `PersonaComposite`
- `SnapshotCounts`, `SnapshotSummary`
- `LeaderboardResponse`, `NotRankedResponse`
- `MatrixPoint`, `MatrixResponse`, `ReprojectRequest`, `ReprojectResponse`
  (contract-only additions per the W4-3 store-queries review fold-in below
  — **no UI in this packet renders them**; Matrix is W4-5b, Configure/
  custom-weights is W4-5c)
- `PILLARS`, `PERSONAS`, `LEADERBOARD_PAGE_SIZES` (20, 50, 100, 500, 1000),
  `DEFAULT_PAGE_SIZE` (20), `GateReason`, `UnratedReason`

### Mid-packet contract revisions (W4-3 store-queries review, folded in live)

The lead sent these after the contract was first built; all are applied,
fixtures regenerated, UI updated:

| Change | Where |
|---|---|
| `tie_group` → `int \| None` (`None` = singleton, opaque equality-only label) | `LeaderboardRow` |
| `stars_audited` → `int \| None` (no v2 producer until W1-10 lands) | `LeaderboardRow` |
| `total` field added | `SnapshotCounts` |
| `persona_visible_counts`, `code_commit`, `canonical_dump_hash` added | `SnapshotSummary` |
| `persona_gate_reason: str \| None` added; rule relaxed "exactly one" → "at least one of three" | `NotRankedRow` |
| `"unrated: insufficient_evidence"` (engine's own constant) accepted alongside the plain spelling; `copy.js.unratedReasonText` maps both to the same sentence | `UnratedReason` |
| `"fork"` added | `GateReason` |
| `"no_population"` added to `unmeasured_reason`; `measured: bool` field added (now the authoritative flag; the old field-presence check kept as a consistency guard) | `EvidenceRow` |
| `MatrixPoint`/`MatrixResponse`, `ReprojectRequest`/`ReprojectResponse` added | new models |

`raw_hash: str` on `LeaderboardRow` is **my own addition**, not in `06` §4's
row field list — `06` §3 Screen C item 5 ("integrity: raw_hash") needs it
somewhere, and W4-4's work-plan entry names a `repos/{o}/{n}` detail
endpoint this packet does not build, so the leaderboard row is the only
place left to carry it. Flagged in the contract's own docstring; W4-4
should revisit if it does build that detail endpoint instead.

## Fixtures (`tests/fixtures/ui/`)

`leaderboard.json` (73 rows: one tie group of 3, one deep tie of 2 resolved
only by owner/name at level 8, coverage bands full/A/B all present with
their ceilings, all four quadrants, one row gated per persona for all four
non-balanced personas, 3 suspicion-flagged rows, one row with an unmeasured
pillar, one row with `stars_audited: null`), `leaderboard-first_timer.json`
(same repos re-projected, 59/71 common rows moved rank),
`notranked.json` (7 gated — all `06`§3 A2 reasons plus `fork` — and 4
unrated, `persona_hidden: []`), `notranked-first_timer.json` (same gated/
unrated, `persona_hidden` populated with the rows `leaderboard.json` gates
under `first_timer`), 3 `evidence-*.json` (full / partial covering all 5
`unmeasured_reason` values / gated), `snapshot.json`.

Generator (not shipped — scratch tooling, kept for regenerating):
`~/Projects/cvi-scratch/W4-5a/gen_fixtures.py`. Re-run with:
```
PYTHONPATH=/home/pctablet505/Projects/cvi-wt-W4-5a \
  /home/pctablet505/Projects/contributor-value-index/.venv/bin/python \
  /home/pctablet505/Projects/cvi-scratch/W4-5a/gen_fixtures.py
```

## Fixture server

```
cd /home/pctablet505/Projects/cvi-wt-W4-5a
PYTHONPATH=$PWD /home/pctablet505/Projects/contributor-value-index/.venv/bin/python \
  scripts/serve_ui_fixtures.py --port 8899
```
Currently **running** on port 8899 (PID from this session) serving this
exact rebased tree — the lead can open `http://127.0.0.1:8899/` directly.
Stdlib `http.server.ThreadingHTTPServer`, no FastAPI. Endpoints:
`GET /v1/porchlamp/leaderboard?persona=&eco=&q=&sort=&page=&page_size=`,
`GET /v1/porchlamp/not-ranked?persona=`, `GET /v1/porchlamp/repos/{o}/{n}/evidence`,
`GET /v1/porchlamp/snapshot`, `GET /healthz`, everything else static from
`web/v2/`. `page_size` must be one of `porchlamp.serve.contract.LEADERBOARD_PAGE_SIZES`.

## Front end (`web/v2/`)

`index.html`, `app.js` (the only DOM-touching module), `state.js`, `api.js`,
`copy.js`, `perf-fixtures.js`, `components/{table,badge,sparkbar,keymap,patch}.js`,
`views/{leaderboard,notranked,drawer}.js`, `styles/{tokens,base,components,views}.css`.

**Deleted mid-packet:** `query.js` (client-side ecosystem/search/sort) —
made obsolete by the pagination ruling; sort/search/eco now always fetch
(only the server sees the whole result set with one page loaded).

### Rendering architecture (U-12..U-19, `06-UX-DESIGN.md` §6.1)

- **U-12 keyed, incremental DOM.** `components/patch.js`'s `patchRows` diffs
  a keyed item list against a container's live children: existing nodes are
  *moved* (`insertBefore`) not recreated on reorder, and a survivor's own
  update function only rewrites the specific `<td>`s whose computed HTML
  actually changed (cached via `dataset.rendered`). Header/footer/table
  skeleton render once per structural transition (`shellShape`/`renderShell`
  in `views/leaderboard.js` and `views/notranked.js`); everything else calls
  `patchTableBody`/`patchCardBody`/`patchNotRankedBody`.
- **U-13 windowing (amended by pagination).** One API page loads at a time;
  within that page, the visible slice + an 8-row buffer is windowed via
  spacer rows exactly as before — pagination and windowing solve different
  problems and this build keeps both. "No inner scroll box": `main` is a
  normal flow element (`base.css`), the **document** scrolls, and
  `computeVisibleRange`'s inputs come from `getBoundingClientRect()` on the
  table/card container, not a bounded container's own `scrollTop`.
- **U-14 amended.** Sort/search/ecosystem now fetch (reset to page 1,
  greyed table + thin progress bar via `.stale-table`/`.refetch-progress`,
  old page stays visible) — the original "client-side over the loaded rows"
  text assumed the whole list was loaded; with one page loaded, only the
  server can sort/filter the full set. Persona switch is the same
  fetch-and-grey pattern.
- **U-15 every request has an error state.** `api.js` wraps every `fetch`
  in a 5,000 ms `AbortController` timeout (`ApiError.status === "timeout"`);
  a leaderboard/not-ranked failure keeps the old page visible with a named
  endpoint+status banner; an evidence failure shows the same in the drawer
  with a **Retry** button (`data-evidence-retry`).
- **U-16 URL is the view.** Real query string (`?persona=&eco=&q=&sort=&page=&page_size=&repo=&tab=`),
  `history.pushState` for persona/eco/sort/page/page_size/tab/repo,
  `replaceState` while typing `q` (debounced 80 ms). `popstate` resets every
  URL-owned field to its default before re-applying whatever the new URL
  has (verified: back-button from `?persona=first_timer` to `/` correctly
  restores `balanced`, not a stuck value).
- **U-17 true announcements.** `state.countRankChanges(previousRows, nextRows)`
  counts rows whose `rank` differs, matched by `owner/name` — verified live:
  switching to `first_timer` announces *"Re-projected under First-timer: 59
  rows moved."*, not 0.
- **U-18 keyboard continuity.** `selectedRepoKey` (not an index) survives
  every patch; J/K move within the currently loaded page; `]`/`[` change
  page; Esc restores focus to the row that opened the drawer (via a
  re-query by key after the patch, since the original DOM node is gone).
- **U-19 measured, not asserted.** See "Timing numbers" below — measured
  live in a real browser (Chromium via the Claude Browser tool), not
  asserted from the (unexecuted, no Node) test file.

### Pagination (owner instruction, verbatim, 2026-09-13)

- Default page size 20; dropdown offers exactly 20/50/100/500/1000
  (`porchlamp.serve.contract.LEADERBOARD_PAGE_SIZES`, imported by both the
  fixture server and `state.js`).
- Pager: first/previous/`Page i of N (M rows)`/next/last
  (`views/leaderboard.js::renderPager`, shared with Not-ranked via a
  `dataPrefix` param — same component, `data-pager-*` vs.
  `data-notranked-pager-*` hooks).
- `page`/`page_size` in the URL via `pushState`.
- **Prefetch:** after a page renders, `app.js::prefetchNextPage` fetches
  `page+1` in the background into a single-slot cache
  (`state.nextPageCache`, keyed by a full request signature); verified
  live — clicking Next after a prefetch landed made **zero** network
  requests for that page (the one recorded request was the *following*
  page's background prefetch).
- **Page-size change keeps the current row in view:** anchors on the
  keyboard cursor row if one is selected, else the topmost windowed row of
  the current page; recomputes the target absolute index into the new page
  size (`app.js::goToPageSize`). Verified live for the small (73-row)
  fixture; the anchor math is exercised but a large real dataset would be
  needed to see it visually "hold position" mid-scroll — see COULD NOT
  VERIFY.
- **Not-ranked pagination is client-side**, not server-paged: the
  dispatch's contract didn't ask `NotRankedResponse` for `page`/`page_size`
  fields, and the real not-ranked list (gated+unrated) is inherently small.
  `views/notranked.js::flattenGroups` concatenates
  `[gated, unrated, personaHidden]` into one sequence tagged by group, and
  `renderPageRows` slices it per page, inserting a group heading whenever
  the slice reaches a new group's first row. If W4-4's real not-ranked
  list turns out large, this should move to server-side paging with the
  same fields the leaderboard uses.

### Screen A tie mark / tooltip (U-5)

`components/table.js::decidingTieLevel` compares two rows' `porchlamp`,
`coverage`, `p_responsiveness`, `friction`, `leverage` — levels 1, 2, 3, 4,
5 of D-TIE-1 — and falls back straight to level 8 (`owner`/`name`).
**Levels 6 (TTFHR p75) and 7 (last non-bot commit date) are not in the API
row contract at all** (`06` §4 never lists them), so the client cannot
derive them; if two rows are tied through level 5 and the real D-TIE-1
decision actually happened at level 6 or 7, this function will report
level 8 (owner/name) instead — an approximation baked into the client-side
derivation the dispatch explicitly asked for and explicitly asked to be
documented here. `isTieMate` treats `tie_group === null` as "never tied"
(two singletons must not appear tied just because they share `null`).

### `raw_hash` in the drawer

Displayed (truncated to 16 hex chars + `…`) in the Integrity section using
`copy.RAW_HASH_LABEL`.

## Node availability

**`node` is not installed on this box** (checked: no binary on `$PATH`,
none under `/usr/bin`, `/usr/local/bin`; `apt list --installed` shows
nothing). Per the dispatch's own fallback ("If Node is absent, keep the
render functions pure and testable and say so"), the render/patch/state
functions are pure and side-effect-free, and three files exist for when
Node becomes available:

- `web/v2/tests/fakedom.mjs` — a hand-rolled minimal DOM (createElement,
  classList, dataset, setAttribute/getAttribute, appendChild/insertBefore/
  removeChild, style, hidden) sufficient for `components/table.js` and
  `components/patch.js`, with every mutating operation recorded to
  `doc.mutationLog` **only when the value actually changes** (mirrors real
  DOM `classList`/guarded-`setAttribute` semantics).
- `web/v2/tests/unit.test.mjs` — `decidingTieLevel` at levels 1/2/8,
  `SORTABLE_COLUMNS` excludes every vanity column, the keymap handler table
  covers every documented key (with an `Esc`→`escape` alias), `parseSearch`/
  `serializeSearch` round-trip including page/page_size, `countRankChanges`,
  `copy.js` reason-text coverage, coverage-band boundaries, `tierLabel`
  capping logic.
- `web/v2/tests/render.perf.test.mjs` — 1,000-row (the max page size)
  initial-patch budget, 60 simulated scroll steps (container never
  rebuilt, rendered slice advances, frame budget), and the literal "a
  one-row change patches exactly one `<tr>`" check, via `fakedom.mjs` +
  real `MutationObserver`-equivalent bookkeeping.

**These files are traced by hand for correctness (I walked the
`patchRows` algorithm through every `add`/`remove`/`reorder`/`no-op`
scenario against the test's own assertions) but were never executed** —
run with `node --test web/v2/tests/*.test.mjs` once Node ≥ 18 is
available. **The numbers actually backing this packet's acceptance claim
are the real-browser numbers below**, not this file's (unexecuted) output.

Given Node's absence, `tests/test_ui_copy_usage.py` substitutes for the
dispatch's suggested "grep the tests for each key" check by grepping the
actual UI source (`app.js`, `state.js`, `components/*.js`, `views/*.js`)
instead of a `.test.mjs` file — the practical, available equivalent that
still catches an orphaned/dead copy string (it caught several real ones
mid-build: see "Bugs found by this packet's own tests" below).

## Timing numbers (U-19) — measured live, Chromium via the Claude Browser tool

All measured against the fixture server on port 8899, the `?perf=1000`
dev harness (synthesizes a 1,000-row page client-side — the max page
size), and a real `MutationObserver`.

| Measurement | Result | Budget |
|---|---|---|
| Synthesize 1,000 rows (`perf-fixtures.js`) | 5.7–6.5 ms | — |
| Initial windowed patch of 1,000 rows | 0.4–0.7 ms | `06` §7: first paint < 200 ms |
| 60 simulated scroll steps, max single patch | 9.2–12.1 ms | 16 ms (3× margin = 48 ms) — **passes**, well inside even the un-multiplied budget |
| 60 simulated scroll steps, average patch | 4.7–4.8 ms | — |
| Total DOM mutations across 60 scroll steps | 3,249–4,355 | (legitimate: new rows entering the window + spacer height changes; not "excessive" — verified by re-running the identical 60-step sequence with `scrollTop`/`scrollY` fixed at the end (2,400 or 2,373 depending on run), never reset to 0) |
| One-row data change → distinct `<tr>` elements mutated | **1** (exact) | U-19's literal requirement |

Also verified live: the coordinator's specific addendum ("forty
programmatic scroll steps... ended with `scrollTop = 0`") — 40 steps of 40
px each on the real page, `window.scrollY` ended at 1,600–2,400 depending
on run (never reset), and the rendered slice's first visible rank advanced
monotonically with it.

**Environment note:** this box's automated browser reports
`document.hasFocus() === false` even when a tab is explicitly fronted,
which throttles `requestAnimationFrame` indefinitely (confirmed directly:
a bare `requestAnimationFrame` callback never fired after 500 ms). The
shipped scroll handler races `requestAnimationFrame` against a 32 ms
`setTimeout` fallback so a real, focused user's browser still gets the
correct "sync to paint" behavior while this environment's scroll-driven
patches still fire (verified: dispatching real `scroll` events and waiting
150 ms produced the correctly advanced windowed slice without calling any
debug hook). A `window.__porchlampDebug = boot()` dev-only hook (never on any
user-facing path) was also added so `patchLeaderboardBody` could be
invoked synchronously for the more precise per-step timing numbers above.

## §8 acceptance checklist

| # | Item | Test |
|---|---|---|
| 1 | Every row shows coverage; C<0.95 shows its ceiling | `tests/test_ui_fixtures.py::TestLeaderboardFixtureInvariants::test_every_coverage_band_present_with_its_ceiling`; `components/badge.js::renderCoverageBadge` |
| 2 | Gated/unrated counts visible on Ranked, match Not-ranked | `tests/test_serve_ui_fixtures.py::TestLeaderboardEndpoint::test_counts_are_identical_across_filters`; `views/leaderboard.js::renderCountsStrip` rendered on both tabs |
| 3 | Vanity columns have no sort affordance | `web/v2/tests/unit.test.mjs::"SORTABLE_COLUMNS excludes every vanity/decorative column"` (unexecuted, see Node note); `components/table.js::SORTABLE_COLUMNS`/`renderColumnHeader` |
| 4 | Persona switch: balanced Porchlamp visible, deltas shown, hidden count = persona_gates count | live-verified announcement text above; `views/leaderboard.js::computeHiddenCount`/`renderCountsStrip`; `tests/test_ui_fixtures.py::TestLeaderboardFixtureInvariants::test_every_non_balanced_persona_has_a_gated_row` |
| 5 | Tie rows carry "=" and the tooltip names the deciding level | `web/v2/tests/unit.test.mjs` levels 1/2/8 (unexecuted, see Node note); `tests/test_ui_fixtures.py::test_exactly_one_tie_group_of_three_and_one_of_two` |
| 6 | Footer shows snapshot id, version, constants hash, manifest root | `app.js::syncFooter`; visually confirmed in screenshots taken during live verification |
| 7 | All four states render for leaderboard/drawer/not-ranked from fixtures | `views/leaderboard.js::renderSkeleton/renderErrorBanner/renderEmptyState`; live-verified loading/error/empty paths render (drawer's "Loading evidence…"/404+retry states live-verified) |
| 8 | Keymap works for every key; focus returns to the row on Esc | `components/keymap.js::KEY_ACTIONS`; `web/v2/tests/unit.test.mjs` (unexecuted); live-verified Esc-closes-and-restores-focus after the initial bug (stale element reference) was fixed |
| 9 | Contrast test passes for both themes | `tests/test_ui_contrast.py` (5 tests, **executed, passing**) |
| 10 | Zero external requests | `tests/test_ui_no_external_resources.py` (6 tests, **executed, passing**) |

Items 3, 5, 8's authoritative execution is the (unexecuted, Node-absent)
`.mjs` files — hand-traced for correctness, not run. Everything else in
this table was actually executed on this box.

## Bugs found and fixed by this packet's own tests (not just written after the fact)

- **Stray NUL bytes** silently corrupted one template-literal line in an
  early `app.js` draft (an encoding artifact from composing the message),
  making `file`/`grep` treat the file as binary; caught by manual
  inspection, fixed, and a permanent regression test added
  (`tests/test_ui_no_external_resources.py::test_files_are_valid_utf8_with_no_nul_bytes`).
- **`tests/test_ui_copy_usage.py`** caught several real dead/unused
  `copy.js` exports mid-build, each traced to an actual missing feature and
  fixed rather than just silenced: `personaHiddenDisclosure` (the U-4
  disclosure was never wired into the leaderboard — now is),
  `RAW_HASH_LABEL` (drawer never showed `raw_hash` — now does),
  `DRAWER_TITLE`/`DRAWER_SECTION_IDENTITY` (the "Why this score" framing
  and an accessible name for the identity region were missing — now
  added), `ROW_OPEN_HINT` (never surfaced as a `title` attribute — now
  is). `TIER_CAPPED_SUFFIX`/`COVERAGE_BAND_LABELS`/`TIE_LEVEL_LABELS`/
  `PERSONA_GATE_COPY`/`PERSONA_INSUFFICIENT_EVIDENCE_TEXT` were correctly
  internal-only and de-exported instead.
- **Drawer `aria-labelledby="drawer-title"` pointed at a nonexistent id**
  in the gated/unrated/not-found render branches (the `<h1>` never carried
  `id="drawer-title"` in those paths) — found while live-verifying focus
  behavior, fixed.
- **Focus-restore-on-close used a stale DOM element reference**: every
  state change replaces `main`'s row nodes (even for an unrelated field
  like opening the drawer), so storing the clicked row's *element* and
  calling `.focus()` on it later was a no-op after the element was
  recreated. Fixed to store the repo *key* and re-query the live element
  by `data-owner`/`data-name` after the close-triggered re-render.
- **Reported by the coordinator, reproduced and fixed:** the scroll
  handler's full re-render reset `scrollTop`/`scrollY` to 0 on every
  frame (root cause: `main.innerHTML = ...` was rebuilding the scroll
  container itself on every scroll tick). Fixed by never touching the
  container on scroll — only `patchTableBody`'s keyed diff of the
  `<tbody>`'s children.
- **`popstate` didn't reset `persona` to its default** when navigating
  back to a URL with no `persona` param (only `ecosystem`/`query`/`sort`/
  `tab`/`repo` were in the defaults object) — found while live-verifying
  back-button behavior, fixed.
- **Not-ranked view had no way back to Ranked**: `views/notranked.js`'s
  shell never rendered the tabs, so switching to Not-ranked stranded the
  user (only a URL edit or hard reload could get back). Found by screenshot
  during live verification, fixed by rendering the same
  `renderTabs`/`renderCountsStrip` on both shells.

## Deviations from the literal packet text (all reasoned, all reported)

- **"pages of 500" (original U-13 text)** is expressed in
  `app.js`/`scripts/serve_ui_fixtures.py` as the largest unit the fixture
  server's own page-size contract allows — moot now that the owner's
  pagination instruction fixed the allowed set to 20/50/100/500/1000
  directly; no adaptation needed in the final state.
- **True incremental "fetch more as you approach the loaded boundary"
  prefetching for a single list larger than one page** is NOT built —
  only the simpler "prefetch page N+1 after page N renders" the owner's
  instruction asked for. The original (superseded) U-13 wording asked for
  more (fetch ahead of scroll position within one unpaginated list); moot
  under the current pagination model, where each page is a bounded, single
  request.
- **Not-ranked pagination is client-side**, not a mirror of the
  leaderboard's server-side paging fields, for the reason given above.

## COULD NOT VERIFY (needs a real, focused desktop browser)

- Live `requestAnimationFrame`-driven scroll smoothness in a real, OS-
  focused tab (this box's automated browser cannot hold real OS focus —
  see the timing section; the underlying patch logic and the 32 ms
  fallback path are both verified, just not "does it feel smooth to a
  human scrolling with a real trackpad").
- The sticky app-header's exact rendered height across every viewport
  width where its controls wrap to a second line — `--header-height: 60px`
  is an estimated floor for the table `thead`'s sticky offset, not
  measured against the real header at every breakpoint.
- The < 768 px card-list breakpoint's visual layout (verified
  programmatically that `viewport.isMobile` correctly swaps in
  `createCardElement`/`patchCardBody` and that the shell rebuilds on a
  breakpoint-crossing resize event, but not eyeballed at a real narrow
  viewport).
- Dark/light theme toggle's visual appearance (contrast *values* are
  tested computationally against the token file; the toggle's live
  behavior — icon state, transition — was not screenshotted in both
  themes).
- `?` keymap overlay's visual layout and the drawer/keymap focus trap's
  exact Tab-cycling behavior in a real browser (the trap logic was
  written and is structurally sound but not stepped through key-by-key
  live).
- Page-size-change "keeps the current row in view" holding up
  *mid-scroll* on a dataset large enough that a page doesn't fit on one
  screen (the 73-row fixture never requires scrolling within a 20-row
  page, so the anchor math runs but its visual effect — the same row
  staying under the cursor — was only confirmed via the resulting page
  number, not a scroll-position screenshot).
- Real 5xx/timeout error banners beyond the one induced 404 (evidence) and
  one induced synthetic timeout (via a monkey-patched `fetch` in the
  browser) — no real 500 response was available to trigger from the
  fixture server.

## Rules followed

Stayed out of `porchlamp/core/**`, `porchlamp/store/**`, `porchlamp/collect/**`,
`scripts/serve.py`, `web/index.html`, `scripts/demo_app.py`. No network
calls, no CDN, no external fonts (system font stack only, confirmed by
`tests/test_ui_no_external_resources.py`). `PYTHONPATH=$PWD` used for
every Python command. Ruff clean on every Python file touched. Rebased
onto `main` before this handoff.
