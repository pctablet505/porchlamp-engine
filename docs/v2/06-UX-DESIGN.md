# UX design v2 — the product surface, its states, and the front-end architecture

Owner directive 2026-09-13: "a better architecture and design with a flawless
UX". This document is the binding brief for packets W4-4 (API v2) and W4-5
(UI v2). It sits on top of `docs/UI_UX_SPECIFICATION.md` (design tokens,
typography, breakpoints, the four screens' visual language) and replaces its
interaction semantics wherever the two disagree. Decision ids are `U-*`.

## 0. What "flawless" means here

A ranking product fails its users in four ways: it shows a number without
saying where it came from; it hides the repositories it refused to rank; it
lets a vanity metric steer the eye; and it changes meaning when the user
changes a control. The UX below is designed against those four failures
first and against visual polish second. Polish is required too, but a
beautiful page that hides a gate is a worse product than a plain one that
explains it.

## 1. Principles

| Id | Principle | Consequence in the UI |
|---|---|---|
| **U-1 Every number carries its evidence** | No score, rank or tier is ever shown without its coverage and a one-click path to the parameter-level evidence (value, n, window, source, fetched_at, method). | Coverage badge on every row and card; "Why this score" drawer on every repository; evidence rows link to the envelope path. |
| **U-2 Gates are visible, never hidden** | Gated (mirror, archived, unlicensed, stale, black hole) and unrated (insufficient evidence, no external workflow) repositories are listed in a dedicated section with a plain-English reason. The default leaderboard shows rated repos only, with the excluded counts always visible. | A "Not ranked (N)" tab beside the leaderboard; reason chips; the count never disappears when filters change. |
| **U-3 No vanity steering** | Stars, forks and downloads are never sortable and never colour-coded. Audited stars appear once, as the matrix marker size and in the identity strip. | Column headers for vanity fields have no sort affordance; tooltips say "audited, not scored". |
| **U-4 Persona is a lens, not a different truth** | Switching persona re-projects the same evidence under different weights; the balanced score stays visible; rank deltas are shown; repositories the persona gates out are listed with the gate reason. | Persona switcher in the header; a delta column; a "hidden by this lens (N)" disclosure. |
| **U-5 Ties are honest** | Rows that tie on the composite share a visible "=" mark; hovering or focusing the rank shows the tie-break level that separated them. | Tie group marker; rank tooltip listing the D-TIE-1 level. |
| **U-6 Reproducible on every page** | Snapshot id, benchmark version, constants hash and manifest root are shown in the footer of every view and on the Methodology page with the reproduce command. | Footer strip; Methodology page. |
| **U-7 Never fabricate a state** | Loading, empty, error and partial-data states are explicit and distinct; a partial payload renders what it has and names what is missing. | Skeleton rows on load; empty-state copy that says why; error banner with the failing endpoint; unmeasured cells render as "—" with the reason on focus. |
| **U-8 Keyboard and screen reader first** | Every interaction has a keyboard path; re-projection announces itself; focus never lands nowhere. | Keymap in §5; `aria-live` region for rank changes; focus management on drawer open/close. |

## 2. Information architecture

```
Header: brand · snapshot chip (id, version) · persona switcher · ecosystem scope · search (/) · theme toggle
Primary tabs: Leaderboard (A) · Matrix (B) · Compare (E) · Configure weights (D) · Methodology (F)
Leaderboard has two sub-tabs: Ranked (default) · Not ranked (gated + unrated)
Drawer (C): repository detail, opened from A, B, E; URL-addressable
Footer: snapshot id · benchmark version · constants hash · manifest root · "verify" link to F
```

URL state (U-9): each view has its own path (`web/app/routes.js`: `/`, `/not-ranked/`, `/matrix/`, `/compare/`,
`/weights/`, `/methodology/`, and the drawer at `/projects/<owner>/<name>/`, where a reload shows a ranked
project's own page), with the fields that view reads in the query (`persona eco q sort page page_size w cmp`);
every view is shareable and reload-safe; persona and ecosystem changes rewrite the URL. The old `?tab=` and
`?repo=` links redirect to these addresses.

Two pages load no app and carry one small script of their own: the first-pull-requests report at
`/first-pull-requests/` (slider and hover text; it reads whole without the script) and the personal lookup at `/lookup/`,
which contacts GitHub only when the visitor presses its button.

## 3. Screens

### A. Leaderboard (default)

Columns, in order: rank (with "=" tie mark), repository (owner/name, ecosystem
chip, audited-star size hint), Porchlamp (2 decimals), coverage badge (percentage,
colour by band: ≥95 full, 80–95 ceiling A, 60–80 ceiling B), tier (with
ceiling shown as "A (capped)" when the ceiling bound), quadrant glyph+label,
five pillar mini-bars with per-pillar coverage, persona delta (only when a
non-balanced persona is active), suspicion flags (icon with reason list).

Interactions: sort only on rank, Porchlamp, coverage, tier, pillars; search by
owner/name; ecosystem scope; page size 20 (default)/50/100/500/1000 with next/previous page; row click or Enter opens
the drawer; J/K move; "=" rows share a subtle background band.

States: loading (skeleton rows, no numbers); empty ("No rated repositories
match. N gated and M unrated are listed under Not ranked."); error (banner
naming the endpoint and status; last good table stays visible, greyed);
partial (rows render, missing fields show "—" with reason on focus).

### A2. Not ranked

Two groups: Gated (reason chip: mirror / archived / unlicensed / stale /
black hole / non-software) and Unrated (insufficient evidence with the
coverage value; no external workflow; off-platform routed). Each row links
to the drawer, where the gate reason is the first thing shown. Copy for each
reason lives in one map (U-10) and is reused by the drawer.

### B. Matrix

Scatter of friction (x) vs leverage (y), quadrant washes and labels, marker
size = audited stars, marker shape+colour by quadrant (colour-blind safe).
Gated repos are not plotted; a caption states how many are excluded. Hover
card: name, Porchlamp, coverage, tier; click opens the drawer. Zoom/pan with keys
and wheel; a "reset view" control. Axis coverage caption when a pillar was
unmeasured for many rows ("friction computed without accessibility for 12%").

### C. Repository drawer ("Why this score")

Order of sections is fixed and reflects D-DEF-2: (1) identity strip and gate
status (if gated: the reason sentence and nothing else above the fold); (2)
composite: Porchlamp, coverage, tier with ceiling explanation, quadrant with
coordinates and their coverages; (3) pillar table: score, coverage, and per
parameter: value, score, n, window, method, source (envelope path,
fetched_at) — unmeasured parameters show the reason; (4) persona view: gates
for each persona with reasons, persona composites; (5) integrity: raw_hash,
snapshot id, suspicion flags with detector names; (6) playbook (v1 feature,
kept): the actions and their projected deltas, labelled "model, not a
promise". Keyboard: Esc closes and returns focus to the originating row;
sections are `<details>` groups navigable by Tab.

README badge (inside section 2): when the repository's balanced record is
rated at tier A or above with no suspicion flag, the composite section ends
with a badge for the project's README: a static image from `web/v2/badges/`
naming the tier ("A (capped)" when the ceiling bound), its coverage band
(U-1) and, in the Sweet Spot, the quadrant, with Markdown that links it to
the project's address (`/projects/<owner>/<name>/`) and alt text carrying the score, the exact
coverage and the snapshot id. It is always the balanced rating, whatever lens
is active (U-4). The image does not change when a later snapshot changes the
rating; the drawer behind the link is the record.

#### Repositories outside the snapshot: live check

A drawer opened for a repository the snapshot does not hold (a real 404 from
the detail fetch, after checking whether the snapshot already holds that
`owner/name` under different casing) says so — "owner/name is not in the
{snapshot id} snapshot, so Porchlamp has no score for it" — and offers one
button, "Check it live on GitHub". A Ranked or Not-ranked search that finds
nothing and is itself a repository name (`owner/name`, or a pasted github.com
address) offers "Look up owner/name", which opens that drawer.

Nothing is requested until the visitor presses the button. Then their own
browser (`web/v2/livecheck.js`) asks `api.github.com` for four public answers —
the repository, its recursive file tree, one page of default-branch commits
from the vitality window, and the 30 newest pull requests across all states —
after a free `GET /rate_limit` that stops before spending anything when fewer
than five of the 60 unauthenticated requests an hour GitHub allows the
visitor's network are left (four requests, plus one reserved in case a renamed
repository costs a GitHub redirect). No credentials, no referrer, no token, no
Porchlamp server and no write: the project runs and pays for nothing, and the
only allowance spent is the visitor's.

The panel then shows GitHub's facts (stars labelled as unaudited, language,
dates, SPDX id) and the verdict of each eligibility gate public data can
decide — D-GATE-1..4 and D-GATE-6 — with the snapshot's own rules: the rule
constants mirror the engine's and `tests/test_ui_livecheck_port.py` fails when
one drifts; the SPDX, bot, manifest, licence-file, marker, vitality-commit and
decided-PR readings are pinned by case files the Python functions produced
(`web/v2/tests/cases/spdx_osi.json`, `gate_rules.json`). A fork's parent is
looked up in the snapshot itself, never on GitHub. Where one page, the 30-PR
sample, an unreadable/empty endpoint or a truncated tree cannot settle a gate,
it reads "Can't tell" — never a guessed pass or fail — and when five or more
outside human PRs are present (or the 30-PR sample is full) a D-GATE-6 pass
notes that PR-comment review hand-off to another tool is not checked live.
D-GATE-5 (black hole) is always "Not checked live": it needs the replies and
reviews on every outside pull request. The result is never a score, a tier or
a rank, and is labelled "not part of the verified snapshot"; it is kept in
memory until the page reloads, so reopening the drawer costs nothing.
Everything GitHub returns is escaped, and the page's Content-Security-Policy
(`index.html` meta and `porchlamp/serve/security.py`) allows `connect-src` to
`api.github.com` and nowhere else.

### D. Configure weights

Five sliders with lock toggles and proportional normalisation (from the
spec), presets for the five personas, live rank deltas in a side list,
URL-serialised weights. Rule U-11: a custom weight vector is labelled
"custom" everywhere it is applied and is never called a persona.

### E. Compare

Two to four repositories side by side: identity, gate status, composite and
coverage, the 18 parameters with the better value marked, and the evidence
source for each cell on hover. A "verdict" line is generated only from
measured parameters and says which parameters were unmeasured on either
side.

### F. Methodology and snapshot

The published methodology (parameters, windows, minimum n, curves, weights,
tie-breakers, universe criteria) rendered from the frozen constants module so
it cannot drift from the code; the current snapshot's id, as_of, manifest
root, constants hash, counts of rated/gated/unrated, detector summary, and
the exact reproduce command.

## 4. Data contract the UI consumes (API v2, from `04` §7)

Row: `rank, tie_group, owner, name, ecosystem, porchlamp, coverage, tier,
tier_ceiling, quadrant, leverage, friction, leverage_coverage,
friction_coverage, p_* and c_* for the five pillars, persona_gates (JSON),
persona_composites (JSON), suspicion (JSON), stars_audited, snapshot_id,
benchmark_version`. Gated/unrated rows carry `gate_reason` or
`unrated_reason` and no rank. `GET /v1/porchlamp/repos/{o}/{n}/evidence` returns the
`params` long rows. `GET /v1/porchlamp/snapshot` returns the manifest summary.
`POST /v1/porchlamp/reproject` keeps its contract. Any field the UI needs beyond
these is an API change (W4-4), never a client-side derivation.

## 5. Interaction and accessibility standards

- Keymap: `/` search, `J`/`K` rows, `Enter` open, `Esc` close, `1–4`
  quadrant toggles on the matrix, `P` persona menu, `T` theme, `?` keymap
  overlay. All documented in the overlay.
- Focus: visible focus ring on every control; drawer traps focus while open
  and restores it on close; skip link to main content.
- Announcements: an `aria-live="polite"` region announces "Re-projected under
  <persona>: N rows moved" and "Sorted by <column>".
- Contrast: WCAG 2.1 AA for text and 3:1 for graphics in both themes; tokens
  from `docs/UI_UX_SPECIFICATION.md`; a test computes contrast from the
  token values.
- Motion: transitions ≤ 200 ms and disabled under `prefers-reduced-motion`.
- Responsive: the four breakpoints from the spec; below 768 px the
  leaderboard becomes a card list and the drawer a full-screen sheet.

## 6. Front-end architecture (W4-5)

- Zero CDN, zero build step: vanilla ES modules under `web/`: `app.js`
  (bootstrap, router), `state.js` (single store with subscribe, URL sync),
  `api.js` (fetch wrappers with typed errors), `views/{leaderboard,
  notranked, matrix, drawer, configure, compare, methodology}.js`,
  `components/{table,badge,sparkbar,radar,scatter,keymap}.js`,
  `copy.js` (every user-facing sentence, including the gate/unrated reason
  map U-10), `styles/{tokens,base,components,views}.css`.
- Rendering: functions from state to DOM string per view, subtree
  replacement on change; the leaderboard windows rows (renders only the
  visible slice plus a buffer) so 10,000 rows stay under 16 ms per frame.
- Charts are inline SVG generated by the components; shapes and colours are
  both encoded.
- Theme: CSS variables; honours `prefers-color-scheme`, toggle persisted in
  localStorage under one key; painted background on `body`.
- Tests: DOM-level tests (jsdom-free: a small Python-driven headless check via
  the existing FastAPI test client for HTML, plus a Node-free JS test runner
  is not available, so the JS modules keep pure functions separable and the
  Python suite tests the served HTML and the JSON contracts). Every state in
  §3 has a test that renders it from a fixture payload and asserts the copy.

### 6.1 Rendering rules (binding; added 2026-09-13 after the owner's verdict on the first W4-5a build)

The first build rewrote header, table, footer and drawer with `innerHTML` on
every state change and on every scroll frame. The owner's verdict: "the UI is
cool, the UX sucks, too slow and unresponsive, and not working properly." The
lead reproduced it: the table could not be scrolled (each frame recreated the
container and reset `scrollTop`), focus landed on `<body>` after every change,
the URL never updated, a 404 left the drawer on "Loading…" forever. These rules
replace the "subtree replacement" sentence in §6:

- **U-12 Keyed, incremental DOM.** The header, footer and table skeleton render
  once. Rows are keyed by `owner/name` and patched in place (add, remove, move,
  update only the cells whose values changed). Never `innerHTML` on an
  ancestor of the focused element; never a full render on scroll or resize.
  Focus, the current row and the scroll position survive every state change.
- **U-13 Paginated list (owner instruction 2026-09-13, supersedes the
  earlier "no pagination" draft).** The leaderboard is paginated: default
  page size 20; a page-size dropdown offering 20, 50, 100, 500 and 1000; a
  next-page / previous-page control (plus first/last and a page indicator
  "Page i of N (M rows)"). Page size and page number live in the URL
  (`&page=&page_size=`). There is no inner scroll box: the page itself
  scrolls, the header row is sticky, and for page sizes of 500 and 1000
  only the visible rows plus a buffer are in the DOM (virtualised within
  the page) so scrolling stays at frame rate. Changing the page size keeps
  the current first row visible (recompute the page so that row stays on
  screen). The same controls apply to the Not-ranked list. The API is
  fetched one page at a time (`limit`/`offset`), with the next page
  prefetched once the current one renders. (Amended 2026-10-12.) The list
  has a second pager below it, with the page buttons and indicator but no
  page-size select, so a reader at the bottom of a long page goes on without
  scrolling back up; any page change scrolls the top of the list back under
  the sticky header. The Not-ranked list on the static site is the whole
  list (the API's cut at `max_rows` does not apply: `fetchNotRankedFull`),
  and Configure weights loads every page of its ranking, not the first 1000
  rows.
- **U-14 Instant local interactions.** Sort, search and ecosystem filter run
  client-side over the loaded rows with a visible response under 50 ms
  (search debounced ≤ 100 ms). A persona switch is a fetch: the current table
  stays visible and greyed with a thin progress indicator until the new
  payload lands, then rows are patched; payloads are cached per persona.
- **U-15 Every request has an error state.** A 404, a 5xx or a 5 s timeout
  renders the error block naming the endpoint and status, with a retry;
  "Loading…" is never a resting state.
- **U-16 URL is the view.** The view's path plus the fields it reads (U-9) via
  `history.pushState` (replaceState for `q` while typing); `popstate` restores;
  any URL reloads to the same view.
- **U-17 Announcements are true.** "N rows moved" is the count of rows whose
  rank differs between the previous and the new payload.
- **U-18 Keyboard continuity.** After any patch, J/K continue from the current
  row; Esc returns focus to the row that opened the drawer.
- **U-19 Measured, not asserted.** A Node test renders 10,000 synthetic rows,
  simulates 60 scroll steps and asserts no patch exceeds the 16 ms frame
  budget (3× margin, numbers reported) and that a one-row state change
  mutates exactly one `<tr>`. The lead verifies U-12..U-18 in a real browser
  before any acceptance.

## 7. Performance budgets

| Path | Budget |
|---|---|
| Leaderboard first paint (10k rows, windowed) | < 200 ms after payload |
| Reprojection (server) | < 100 ms p95 for 10k rows |
| Reprojection (render) | < 50 ms |
| Drawer open | < 100 ms with evidence payload cached per repo |
| Matrix render | < 150 ms for 10k markers |

## 8. Acceptance checklist for W4-5 (each item is a test)

1. Every row shows coverage; a row with C < 0.95 shows its ceiling.
2. Gated and unrated counts are visible on the ranked tab and match the
   Not-ranked tab.
3. Vanity columns have no sort affordance.
4. Persona switch: balanced Porchlamp stays visible, deltas appear, hidden count
   matches `persona_gates`.
5. Tie rows carry "=" and the rank tooltip names the deciding level.
6. Footer shows snapshot id, version, constants hash, manifest root.
7. All four states render for the leaderboard, drawer and matrix from
   fixtures.
8. Keymap works for every key in §5; focus returns to the row on Esc.
9. Contrast test passes for both themes.
10. Zero external requests (existing test). One opt-in exception: the live
    check (§3 C) contacts `api.github.com` only after its button is pressed;
    no other module may name that origin (`tests/test_ui_no_external_resources.py`).
