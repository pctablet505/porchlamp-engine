// views/leaderboard.js — Screen A (06 §3). `renderShell` builds the STATIC
// structure (tabs, counts, pager, table skeleton with thead + empty tbody)
// ONCE per structural transition (loading/error/empty/normal, a page-size
// change, or switching away from and back to this tab) — never on scroll,
// never on a per-row data change. `patchTableBody`/`patchCardBody` are the
// functions called on those frequent updates: they diff the CURRENT PAGE's
// row list into the persistent `<tbody>`/card container via `patchRows`
// (components/patch.js).
//
// Pagination: one API page is loaded at a time (20/50/100/500/
// 1000 rows, default 20); for the two large page sizes the loaded page is
// STILL windowed (only the visible slice plus a buffer exists in the DOM)
// — pagination and windowing solve different problems and both apply.

import * as copy from "../copy.js";
import {
  rowKey,
  rowCells,
  isTieMate,
  renderCard,
  handleRepoLinkClick,
  columnCount,
  createRowElement,
  updateRowElement,
  createSpacerElement,
  updateSpacerElement,
  createCardElement,
  updateCardElement,
  createCardSpacerElement,
  updateCardSpacerElement,
  computeVisibleRange,
  renderColumnHeader,
  renderCardList,
  sortDirectionLabel,
  PILLAR_ORDER,
  ROW_HEIGHT_PX,
  CARD_HEIGHT_PX,
} from "../components/table.js";
import { patchRows } from "../components/patch.js";
import { fill, hook } from "../components/fill.js";
import { PAGE_SIZES, hrefFor, requestSignature } from "../state.js";
import { routePath } from "../routes.js";
import { parseRepoSlug } from "../livecheck.js";

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

export function renderSkeleton(rowCount = 12) {
  const rows = Array.from({ length: rowCount }, () => `<div class="skeleton-row"></div>`).join("");
  return `<div aria-hidden="true">${rows}</div><span class="sr-only">${escapeHtml(copy.LOADING_ROW_SR_TEXT)}</span>`;
}

export function renderErrorBanner(error) {
  return (
    `<div class="error-banner" role="alert">${escapeHtml(copy.errorBannerText(error.endpoint, error.status, error.detail))}` +
    ` <button type="button" class="icon-button" data-leaderboard-retry="true">${escapeHtml(copy.RETRY_LABEL)}</button></div>`
  );
}

/**
 * An empty list. With no search and no ecosystem filter it is the plain count message; with one it names what was
 * searched and offers the ways out: clear the filters, or look in the other list (`otherList` is "notranked" or
 * "ranked" and picks that button's hook).
 */
export function renderEmptyState(gatedCount, unratedCount, { query = "", ecosystem = "", otherList = "notranked" } = {}) {
  const q = query.trim();
  if (!q && !ecosystem) return `<div class="empty-state">${escapeHtml(copy.emptyLeaderboardText(gatedCount, unratedCount))}</div>`;
  return renderFilteredEmpty(q, ecosystem, otherList, otherList === "notranked" ? "ranked" : "gated or unrated");
}

export function renderFilteredEmpty(query, ecosystem, otherList, what) {
  // A search that is a repository name gets one more way out: open its drawer
  // (the generic [data-owner][data-name] click), which shows the record when the
  // snapshot holds it under another list, else offers the opt-in live check.
  const slug = query ? parseRepoSlug(query) : null;
  const actions =
    `<button type="button" class="link-button" data-clear-filters="true">${escapeHtml(copy.CLEAR_FILTERS_LABEL)}</button>` +
    (query
      ? `<button type="button" class="link-button" data-search-${otherList}="true">${escapeHtml(
          otherList === "notranked" ? copy.searchNotRankedLabel(query) : copy.searchRankedLabel(query)
        )}</button>`
      : "") +
    (slug
      ? `<button type="button" class="link-button" data-owner="${escapeHtml(slug.owner)}" data-name="${escapeHtml(slug.name)}">${escapeHtml(
          copy.lookUpRepoLabel(`${slug.owner}/${slug.name}`)
        )}</button>`
      : "");
  return (
    `<div class="empty-state"><p>${escapeHtml(copy.emptyFilteredText(query, ecosystem, what))}</p>` +
    `<p class="empty-actions">${actions}</p></div>`
  );
}

/**
 * How many rows the ACTIVE LENS ranks. Not `counts.rated`.
 *
 * MEASURED 2026-09-21. A persona view is a re-weighted projection
 * (`porchlamp.core.persona`: `persona_composite` is "a separate re-weighted
 * projection"), and the store admits a row on
 * `persona_composites[<persona>].coverage >= 0.60` — that persona's own
 * weights — not on the balanced `rated` flag. So a persona that
 * down-weights the pillars a sweep has not collected yet ranks MORE repos
 * than balanced, not fewer: snapshot 2026-09-21-L3gp measured
 * `counts.rated = 94` with `ai_assisted = 303` and `senior_systems = 98`.
 * Treating persona-visible as a subset of balanced-rated broke two things
 * at once — the Ranked tab showed 94 over a table paging 303 rows, and the
 * U-4 disclosure below went negative and silently vanished.
 *
 * Falls back to `counts.rated` when the snapshot has not arrived yet or the
 * persona is unknown, which is exactly the balanced case.
 */
export function personaVisibleCount(counts, snapshot, persona) {
  if (!counts) return null;
  if (persona === "balanced" || !snapshot || !snapshot.persona_visible_counts) return counts.rated;
  const visible = snapshot.persona_visible_counts[persona];
  return typeof visible === "number" ? visible : counts.rated;
}

/**
 * U-4's lens disclosure, in BOTH directions — the one place this is
 * computed (`renderShell` and `app.js::syncCountsStrip` both call it, so
 * they cannot drift apart as they previously did).
 *
 * Returns `{ hidden: n }` when the lens ranks fewer repos than balanced,
 * `{ extra: n }` when it ranks more, and `null` when the counts agree or
 * no lens is active.
 */
export function personaDelta(counts, snapshot, persona) {
  const visible = personaVisibleCount(counts, snapshot, persona);
  if (visible === null || persona === "balanced" || !snapshot) return null;
  const diff = visible - counts.rated;
  if (diff === 0) return null;
  return diff < 0 ? { hidden: -diff } : { extra: diff };
}

/**
 * The corpus strip: how much of the snapshot is actually rated, as a
 * proportional meter plus the lens disclosure.
 *
 * This replaced two bare `Gated: N` / `Unrated: N` spans. On a
 * mid-collection snapshot the split IS the headline — 94 rated of 5755 is
 * the first thing a reader needs in order to read the table below at all —
 * and as flat caption text it was the least legible element on the screen.
 *
 * Colour never carries a segment's identity on its own: every segment has
 * a visible label and count beside the meter, which is also the relief the
 * palette's sub-3:1 contrast on the muted segments requires. The two
 * not-rated segments are deliberately NEUTRAL and separated by lightness
 * rather than hue — an amber/green pair here measured only ΔE 5.7 for
 * protanopia (below even the 6-8 floor band), while one accent plus two
 * lightness-separated neutrals measures ΔE 12.3 dark / 14.2 light. Do not
 * "brighten" these into status hues without re-running that check.
 */
export function renderCountsStrip(counts, delta) {
  if (!counts) return "";
  const total = counts.total || 0;
  const pct = (n) => (total > 0 ? (n * 100) / total : 0);
  const segments = [
    { key: "rated", label: copy.RATED_GROUP_TITLE, value: counts.rated },
    { key: "gated", label: copy.GATED_GROUP_TITLE, value: counts.gated },
    { key: "unrated", label: copy.UNRATED_GROUP_TITLE, value: counts.unrated },
  ];
  const bar = segments
    .map((s) => `<span class="corpus-seg corpus-seg-${s.key}" style="flex-grow:${pct(s.value)}"></span>`)
    .join("");
  const legend = segments
    .map(
      (s) =>
        `<span class="corpus-key">` +
        `<span class="corpus-swatch corpus-seg-${s.key}" aria-hidden="true"></span>` +
        `${escapeHtml(s.label)}` +
        `<b class="corpus-count" data-v="counts.${s.key}">${s.value}</b>` +
        `<span class="corpus-pct" data-v="counts.${s.key}.share">${escapeHtml(copy.corpusShareText(pct(s.value)))}</span>` +
        `</span>`
    )
    .join("");
  const disclosure = delta
    ? `<span class="corpus-lens" data-hidden-count="true">${escapeHtml(
        delta.hidden ? copy.personaHiddenDisclosure(delta.hidden) : copy.personaExtraDisclosure(delta.extra)
      )}</span>`
    : "";
  return (
    `<div class="counts-strip" data-counts-strip="true">` +
    `<div class="corpus-meter" role="img" aria-label="${escapeHtml(copy.corpusMeterLabel(counts))}">${bar}</div>` +
    `<div class="corpus-legend">${legend}${disclosure}</div>` +
    `</div>`
  );
}

/**
 * The Leaderboard's Ranked and Not ranked lists: two real links to their addresses, `aria-current` on the one
 * shown. `hrefs` carries the addresses for the reader's state (`tabHrefs`); without it, each list's plain path.
 */
export function renderTabs(activeTab, counts, visibleCount, notRankedCount = null, hrefs = null) {
  // The Ranked count must be what the table under it actually pages, which
  // under a lens or active filter is NOT counts.rated — see personaVisibleCount.
  const rated = counts
    ? typeof visibleCount === "number"
      ? visibleCount
      : counts.rated
    : typeof visibleCount === "number"
      ? visibleCount
      : "";
  const notRanked =
    typeof notRankedCount === "number"
      ? notRankedCount
      : counts
        ? counts.gated + counts.unrated
        : "";
  const href = (tab) => escapeHtml((hrefs && hrefs[tab]) || routePath(tab));
  const current = (tab) => (activeTab === tab ? ` aria-current="page"` : "");
  return (
    `<div class="tabs">` +
    `<a class="tab" href="${href("ranked")}" data-route-tab="ranked"${current("ranked")}>` +
    `${escapeHtml(copy.TAB_RANKED)}<span class="count">${rated}</span></a>` +
    `<a class="tab" href="${href("notranked")}" data-route-tab="notranked"${current("notranked")}>` +
    `${escapeHtml(copy.TAB_NOT_RANKED)}<span class="count">${notRanked}</span></a>` +
    `</div>`
  );
}

/** The addresses of the Ranked and Not ranked lists from where the reader is (`hrefFor`). */
export function tabHrefs(state) {
  return { ranked: hrefFor(state, "ranked"), notranked: hrefFor(state, "notranked") };
}

const MOBILE_SORT_FIELDS = [
  ["rank", () => copy.COLUMN_RANK],
  ["porchlamp", () => copy.COLUMN_PORCHLAMP],
  ["coverage", () => copy.COLUMN_COVERAGE],
  ["tier", () => copy.COLUMN_TIER],
  ...PILLAR_ORDER.map((p) => [`p_${p}`, () => `${copy.COLUMN_PILLARS}: ${copy.PILLAR_NAMES[p] || p}`]),
];

/**
 * Compact sort bar rendered above the `< 768px` card list so mobile users
 * have full sort field + direction parity with desktop's `<th>` headers.
 */
export function renderMobileSortBar(sort = "rank") {
  const safeSort = sort || "rank";
  const activeField = safeSort.replace(/^-/, "");
  const dir = sortDirectionLabel(safeSort);
  const arrow = dir === "ascending" ? "↑" : "↓";
  const options = MOBILE_SORT_FIELDS.map(
    ([field, getLabel]) =>
      `<option value="${field}"${field === activeField ? " selected" : ""}>${escapeHtml(getLabel())}</option>`
  ).join("");
  return (
    `<div class="mobile-sort-bar" data-mobile-sort-bar="true">` +
    `<label>${escapeHtml(copy.SORT_LABEL)} ` +
    `<select class="control" data-mobile-sort-select="true" aria-label="${escapeHtml(copy.SORT_LABEL)}">${options}</select></label>` +
    `<button type="button" class="icon-button" data-mobile-sort-dir="true" ` +
    `aria-label="${escapeHtml(copy.SORT_DIRECTION_TOGGLE_LABEL)}" ` +
    `title="${escapeHtml(copy.SORT_DIRECTION_TOGGLE_LABEL)} (${dir})">${arrow}</button>` +
    `</div>`
  );
}

export function syncMobileSortBar(root, sort = "rank") {
  const safeSort = sort || "rank";
  const activeField = safeSort.replace(/^-/, "");
  const dir = sortDirectionLabel(safeSort);
  const arrow = dir === "ascending" ? "↑" : "↓";
  const select = root.querySelector("[data-mobile-sort-select]");
  if (select && select.value !== activeField) select.value = activeField;
  const dirBtn = root.querySelector("[data-mobile-sort-dir]");
  if (dirBtn) {
    if (dirBtn.textContent !== arrow) dirBtn.textContent = arrow;
    dirBtn.setAttribute("title", `${copy.SORT_DIRECTION_TOGGLE_LABEL} (${dir})`);
  }
}

/**
 * The pager UI: page-size dropdown (20/50/100/500/1000) plus
 * first/previous/next/last controls and a "Page i of N (M rows)"
 * indicator. Shared between the Ranked and Not-ranked lists via the
 * `dataPrefix` param, which namespaces the `data-*` hooks app.js binds to
 * (`pager` for the leaderboard, `notranked-pager` for the not-ranked list).
 * The list has one pager above it and one below it (`bottom: true`), so a
 * reader at the end of a long page can go on without scrolling back up.
 */
export function renderPager(page, pageSize, total, dataPrefix = "pager", { bottom = false } = {}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const sizeOptions = PAGE_SIZES.map(
    (n) => `<option value="${n}"${n === pageSize ? " selected" : ""}>${n}</option>`
  ).join("");
  // The pager below the list repeats the buttons and the status, not the page-size select: the one above stays the
  // only place that setting lives.
  const sizeSelect = bottom
    ? ""
    : `<label>${escapeHtml(copy.PAGE_SIZE_LABEL)} ` +
      `<select class="control" data-${dataPrefix}-size-select="true">${sizeOptions}</select></label>`;
  return (
    `<div class="pager${bottom ? " pager-bottom" : ""}">` +
    sizeSelect +
    `<button type="button" data-${dataPrefix}-first="true" aria-label="${escapeHtml(copy.PAGER_FIRST_LABEL)}"${page <= 1 ? " disabled" : ""}>&laquo;</button>` +
    `<button type="button" data-${dataPrefix}-prev="true" aria-label="${escapeHtml(copy.PAGER_PREV_LABEL)}"${page <= 1 ? " disabled" : ""}>&larr;</button>` +
    `<span data-${dataPrefix}-status="true">${escapeHtml(copy.pagerStatusText(page, totalPages, total))}</span>` +
    `<button type="button" data-${dataPrefix}-next="true" aria-label="${escapeHtml(copy.PAGER_NEXT_LABEL)}"${page >= totalPages ? " disabled" : ""}>&rarr;</button>` +
    `<button type="button" data-${dataPrefix}-last="true" aria-label="${escapeHtml(copy.PAGER_LAST_LABEL)}"${page >= totalPages ? " disabled" : ""}>&raquo;</button>` +
    `</div>`
  );
}

/** The one-time structural shell for a given (loading/error/empty/normal) state. `viewport.isMobile` picks the table vs. card-list shell (06 §5: below 768px the leaderboard is a card list). */
/**
 * U-4's lens disclosure for a whole app state: `{hidden:n}`, `{extra:n}` or
 * `null`. Thin wrapper over `personaDelta` so the leaderboard shell, the
 * not-ranked shell and `app.js::syncCountsStrip` all read ONE
 * implementation — app.js's comment already claimed the logic was "kept in
 * one place" while in fact holding a second copy that had drifted.
 */
export function personaDeltaForState(state) {
  const counts = state.leaderboard ? state.leaderboard.counts : null;
  return personaDelta(counts, state.snapshot, state.persona);
}

/** Rows the active lens (and any active ecosystem/query filter) ranks, for a whole app state. */
export function personaVisibleForState(state) {
  if (
    state.leaderboard &&
    typeof state.leaderboard.total === "number" &&
    Boolean(state.ecosystem || (state.query && state.query.trim()))
  ) {
    return state.leaderboard.total;
  }
  const counts = state.leaderboard ? state.leaderboard.counts : null;
  return personaVisibleCount(counts, state.snapshot, state.persona);
}

export function renderOnboardingBanner() {
  return (
    `<div class="onboarding-banner" data-onboarding-banner="true">` +
    `<span>${escapeHtml(copy.ONBOARDING_SUBTITLE)}</span> ` +
    `<a href="${escapeHtml(routePath("methodology"))}" class="onboarding-link" data-route-tab="methodology" data-onboarding-methodology="true">${escapeHtml(copy.ONBOARDING_METHODOLOGY_LINK)}</a>` +
    `</div>`
  );
}

const SLOT = /\{([a-z][a-z0-9_]*)\}/g;

/**
 * `template` (a text from the copy module with `{slot}`s) filled from `report.values`, each slot a hook of its own id, as the
 * report view prints the same values (views/report.js). `null` when there is no report, or its values lack a slot
 * the template names: the sentence is left out rather than drawn with a gap or a guess.
 */
export function fillFromReport(template, report) {
  const values = report && report.values;
  if (!values) return null;
  const slots = {};
  for (const [, id] of String(template).matchAll(SLOT)) {
    if (!Object.prototype.hasOwnProperty.call(values, id)) return null;
    slots[id] = hook(id, values[id]);
  }
  return fill(template, slots);
}

/**
 * One visible line under the onboarding banner: the headline finding, filled from the report, and a link to the
 * report page. Without a report (the dev API serves none; an export without `--stats` has none) only the link.
 */
export function renderFindingsLine(report = null) {
  const finding = fillFromReport(copy.FINDINGS_LINE, report);
  return (
    `<p class="onboarding-banner" data-findings-line="true">` +
    (finding ? `<span data-findings-text="true">${finding}</span> ` : "") +
    `<a href="${escapeHtml(copy.REPORT_PAGE_HREF)}" class="onboarding-link" data-findings-link="true">${escapeHtml(copy.FINDINGS_LINK_LABEL)}</a>` +
    `</p>`
  );
}

/**
 * A report that arrives after the shell is drawn: puts the finding into the line in place, or, where the line already
 * has it, rewrites only a value that differs. Never rebuilds the shell, so nothing under it moves or loses focus.
 */
export function syncFindingsLine(root, report) {
  const line = root.querySelector("[data-findings-line]");
  const finding = line ? fillFromReport(copy.FINDINGS_LINE, report) : null;
  if (!finding) return;
  const text = line.querySelector("[data-findings-text]");
  if (!text) {
    line.insertAdjacentHTML("afterbegin", `<span data-findings-text="true">${finding}</span> `);
    return;
  }
  for (const [, id] of String(copy.FINDINGS_LINE).matchAll(SLOT)) {
    const slot = text.querySelector(`[data-v="${id}"]`);
    if (slot && slot.textContent !== report.values[id]) slot.textContent = report.values[id];
  }
}

//: The landing pages the home links, in the order the lines print them: ecosystems, then contributor lenses.
const BROWSE_ECOSYSTEMS = Object.freeze(["crates", "pypi", "npm", "go", "maven", "systems"]);
const BROWSE_LENSES = Object.freeze(["first_timer", "career_capital", "senior_systems", "ai_assisted"]);

/** The home's links to the ten landing pages, as real paths, so a reader and a crawler without JavaScript reach each one. */
export function renderBrowseLinks() {
  const ecosystemNames = Object.fromEntries(copy.ECOSYSTEMS);
  const link = (href, label) => `<a href="${escapeHtml(href)}" class="onboarding-link">${escapeHtml(label)}</a>`;
  // A middle dot between the names, as the views separate items: "C and C++" and "Career capital" are names of their own.
  const line = (label, links) => `<div><span>${escapeHtml(label)}</span> ${links.join(" · ")}</div>`;
  return (
    `<nav class="onboarding-banner" aria-label="${escapeHtml(copy.HOME_BROWSE_LABEL)}" data-home-browse="true">` +
    line(copy.HOME_BROWSE_ECOSYSTEMS_LABEL, BROWSE_ECOSYSTEMS.map((key) => link(`/${copy.ECOSYSTEM_SLUGS[key]}/`, ecosystemNames[key]))) +
    line(copy.HOME_BROWSE_LENSES_LABEL, BROWSE_LENSES.map((lens) => link(copy.LENS_PAGE_HREFS[lens], copy.PERSONA_NAMES[lens]))) +
    `</nav>`
  );
}

/** The shell's head, the same in every shape and in the prerendered board: banner, findings, tabs, counts. */
function shellHeader(state) {
  const counts = state.leaderboard ? state.leaderboard.counts : null;
  return (
    renderOnboardingBanner() +
    renderFindingsLine(state.report) +
    renderBrowseLinks() +
    renderTabs(state.tab, counts, personaVisibleForState(state), null, tabHrefs(state)) +
    renderCountsStrip(counts, personaDeltaForState(state))
  );
}

/** The controls a page of rows sits between, shared by the shell and the prerendered board so the two cannot differ. */
function boardParts(state) {
  const total = state.leaderboard.total;
  return {
    showPersonaDelta: state.persona !== "balanced",
    sortBar: renderMobileSortBar(state.sort),
    pager: renderPager(state.page, state.pageSize, total, "pager"),
    progressAndError:
      `<div class="refetch-progress" data-refetch-progress="true" hidden></div>` +
      `<div class="error-banner" role="alert" data-leaderboard-error="true" hidden></div>`,
    bottomPager: renderPager(state.page, state.pageSize, total, "pager", { bottom: true }),
  };
}

function tableOpenTag(extraClass = "") {
  return `<table class="leaderboard-table${extraClass}" role="table" aria-label="${escapeHtml(copy.NAV_LEADERBOARD)}">`;
}

function shellSection(header, body, attrs = "") {
  return (
    `<section aria-labelledby="leaderboard-heading"${attrs}>` +
    `<h1 id="leaderboard-heading" class="view-heading sr-only">${escapeHtml(copy.LEADERBOARD_HEADING)}</h1>` +
    header +
    body +
    `</section>`
  );
}

export function renderShell(state, viewport = { isMobile: false }) {
  const { leaderboardLoading, leaderboardError, leaderboard } = state;
  const counts = leaderboard ? leaderboard.counts : null;
  const header = shellHeader(state);

  let body;
  if (leaderboardLoading && !leaderboard) {
    body = renderSkeleton();
  } else if (leaderboardError && !leaderboard) {
    body = renderErrorBanner(leaderboardError);
  } else if (leaderboard && leaderboard.rows.length === 0) {
    body = renderEmptyState(counts.gated, counts.unrated, { query: state.query || "", ecosystem: state.ecosystem || "" });
  } else if (leaderboard) {
    const { showPersonaDelta, sortBar, pager, progressAndError, bottomPager } = boardParts(state);
    body = viewport.isMobile
      ? sortBar +
        pager +
        progressAndError +
        `<div class="leaderboard-cards" data-leaderboard-cards="true"></div>` +
        bottomPager
      : pager +
        progressAndError +
        tableOpenTag() +
        `<thead>${renderColumnHeader(state.sort, showPersonaDelta)}</thead>` +
        `<tbody data-leaderboard-tbody="true"></tbody>` +
        `</table>` +
        bottomPager;
  } else {
    body = "";
  }

  return shellSection(header, body);
}

// --- The prerendered board: the first page of the ranked view, drawn into the exported page ---------------
//
// web/prerender.mjs draws it into `/`; the app takes it over (`adoptPrerenderedBoard`) with nothing redrawn. The page
// cannot know the reader's width, so the board holds both layouts the app draws, the table (from 768px) and the cards
// with their sort bar (below), and the stylesheet hides the one the width does not use. Every row, card and spacer is
// what components/table.js's DOM builders make for the same data (attributes in the order they set them, `data-key`
// last, as `patchRows` adds it), so the app finds each node in place and patches it rather than building another.

//: Marks the board while it is prerendered; the stylesheet hides one layout under it, `adoptPrerenderedBoard` removes it.
export const PRERENDERED_BOARD_CLASS = "prerendered-board";

const SPACER_STYLE = `style="height: 0px;" hidden=""`;

function classAttr(...names) {
  return names.filter(Boolean).join(" ");
}

/** The `<tr>` `createRowElement` builds for `row`, with the `data-key` `patchRows` gives it. */
export function renderRowHtml(row, opts) {
  const { selected = false } = opts || {};
  const { cells, tied } = rowCells(row, opts);
  const cellHtml = cells
    .map(
      (cell) =>
        `<td data-cell="${escapeHtml(cell.key)}"${cell.numeric ? ` class="cell-numeric"` : ""} data-rendered="${escapeHtml(cell.html)}">${cell.html}</td>`
    )
    .join("");
  return (
    `<tr data-owner="${escapeHtml(row.owner)}" data-name="${escapeHtml(row.name)}" tabindex="0" role="button" ` +
    `aria-label="${escapeHtml(`Open ${escapeHtml(row.owner)}/${escapeHtml(row.name)}`)}" title="${escapeHtml(copy.ROW_OPEN_HINT)}" ` +
    `class="${classAttr(tied && "row-tied", selected && "row-selected")}" data-key="${escapeHtml(rowKey(row))}">${cellHtml}</tr>`
  );
}

/** The card `createCardElement` builds for `row`, with its `data-key`. */
export function renderCardHtml(row, opts) {
  const { selected = false, previousRow = null } = opts || {};
  const drawn = renderCard(row, opts);
  const inner = drawn.slice(drawn.indexOf(">") + 1, drawn.length - "</div>".length);
  return (
    `<div data-owner="${escapeHtml(row.owner)}" data-name="${escapeHtml(row.name)}" tabindex="0" role="button" ` +
    `aria-label="${escapeHtml(`Open ${escapeHtml(row.owner)}/${escapeHtml(row.name)}`)}" title="${escapeHtml(copy.ROW_OPEN_HINT)}" ` +
    `class="${classAttr("leaderboard-card", isTieMate(row, previousRow) && "row-tied", selected && "row-selected")}" ` +
    `data-rendered="${escapeHtml(inner)}" data-key="${escapeHtml(rowKey(row))}">${inner}</div>`
  );
}

/**
 * The ranked view with its first page drawn in, for the export. `state` is `defaultState()` with the page the app
 * would fetch for it (`leaderboard`), the snapshot and the report. The board's `data-prerendered-board` is the key of
 * what it shows (`requestSignature`: lens, ecosystem, search, sort, page, page size).
 */
export function renderPrerenderedBoard(state) {
  const { leaderboard } = state;
  if (!leaderboard || !Array.isArray(leaderboard.rows) || leaderboard.rows.length === 0) {
    throw new Error("the prerendered board needs a page of rows");
  }
  const { rows } = leaderboard;
  const { showPersonaDelta, sortBar, pager, progressAndError, bottomPager } = boardParts(state);
  const optsAt = (i) => ({ previousRow: i > 0 ? rows[i - 1] : null, showPersonaDelta, selected: false });
  const tableSpacer = (name) =>
    `<tr data-key="${name}" aria-hidden="true" ${SPACER_STYLE}><td colspan="${columnCount(showPersonaDelta)}"></td></tr>`;
  const cardSpacer = (name) => `<div data-key="${name}" aria-hidden="true" ${SPACER_STYLE}></div>`;
  const body =
    sortBar +
    pager +
    progressAndError +
    tableOpenTag(copy.hasAuditedStars(rows) ? "" : " stars-unaudited") +
    `<thead>${renderColumnHeader(state.sort, showPersonaDelta)}</thead>` +
    `<tbody data-leaderboard-tbody="true">` +
    tableSpacer("__spacer-top__") +
    rows.map((row, i) => renderRowHtml(row, optsAt(i))).join("") +
    tableSpacer("__spacer-bottom__") +
    `</tbody></table>` +
    `<div class="leaderboard-cards" data-leaderboard-cards="true">` +
    cardSpacer("__spacer-top__") +
    rows.map((row, i) => renderCardHtml(row, optsAt(i))).join("") +
    cardSpacer("__spacer-bottom__") +
    `</div>` +
    bottomPager;
  const key = requestSignature({ ...state, tab: "ranked" });
  return shellSection(shellHeader(state), body, ` class="${PRERENDERED_BOARD_CLASS}" data-prerendered-board="${escapeHtml(key)}"`);
}

/** The key of the board a page carries (what it shows), or `null` where the page has none. */
export function prerenderedBoardKey(root) {
  const board = root.querySelector("[data-prerendered-board]");
  return board ? board.getAttribute("data-prerendered-board") : null;
}

/**
 * Takes the prerendered board over for `state` and `viewport`, leaving the DOM `renderShell` would draw: removes the
 * layout this width does not use and the board's marks, and puts the click listener on each row or card. Returns
 * `false`, touching nothing, when the board shows something else than `state` asks for (its key differs) or `state`
 * has no page of rows to show. The caller then patches the rows in place (`patchTableBody` / `patchCardBody`),
 * which finds every node by its `data-key`: no row is replaced.
 */
export function adoptPrerenderedBoard(root, state, viewport) {
  const board = root.querySelector("[data-prerendered-board]");
  if (!board || board.getAttribute("data-prerendered-board") !== requestSignature(state)) return false;
  if (!shellShape(state, viewport).startsWith("table")) return false;
  const kept = board.querySelector(viewport.isMobile ? "[data-leaderboard-cards]" : "[data-leaderboard-tbody]");
  const dropped = viewport.isMobile
    ? [board.querySelector(".leaderboard-table")]
    : [board.querySelector("[data-leaderboard-cards]"), board.querySelector("[data-mobile-sort-bar]")];
  if (!kept || dropped.some((el) => !el)) return false;
  for (const el of dropped) el.remove();
  board.removeAttribute("class");
  board.removeAttribute("data-prerendered-board");
  for (const el of Array.from(kept.children)) {
    if (el.getAttribute("data-owner") !== null) el.addEventListener("click", handleRepoLinkClick);
  }
  return true;
}

/**
 * Updates an already-rendered pager's status text and button
 * disabled-states in place (never a shell rebuild — a page/pageSize change
 * is not a structural shape change, see `shellShape`). `root` is the
 * element containing the `data-${dataPrefix}-*` hooks (usually `main`).
 */
export function syncPager(root, page, pageSize, total, dataPrefix = "pager") {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const text = copy.pagerStatusText(page, totalPages, total);
  for (const status of root.querySelectorAll(`[data-${dataPrefix}-status]`)) {
    if (status.textContent !== text) status.textContent = text;
  }
  const sizeSelect = root.querySelector(`[data-${dataPrefix}-size-select]`);
  if (sizeSelect && sizeSelect.value !== String(pageSize)) sizeSelect.value = String(pageSize);
  const atFirst = page <= 1;
  const atLast = page >= totalPages;
  for (const [selector, disabled] of [
    [`[data-${dataPrefix}-first]`, atFirst],
    [`[data-${dataPrefix}-prev]`, atFirst],
    [`[data-${dataPrefix}-next]`, atLast],
    [`[data-${dataPrefix}-last]`, atLast],
  ]) {
    for (const el of root.querySelectorAll(selector)) {
      if (el.disabled !== disabled) el.disabled = disabled;
    }
  }
}

/**
 * After a page change: how far to scroll (a negative number, up) to bring the pager above the list back into view
 * just under the sticky header, or `null` when it is already visible. `anchorTop` is the pager's top edge in the
 * viewport, `stickyHeight` the room the sticky header takes (0 when it is not sticky).
 */
export function scrollDeltaToReveal(anchorTop, stickyHeight, gap = 8) {
  const target = stickyHeight + gap;
  return anchorTop >= target ? null : anchorTop - target;
}

/**
 * The page to show instead, when `page` is past the end of a non-empty result: the last page. `null` when the page
 * has rows, or when nothing matched at all (the empty state is then the right answer).
 */
export function lastPageWhenPastEnd(leaderboard, page, pageSize) {
  if (!leaderboard || !Array.isArray(leaderboard.rows) || leaderboard.rows.length > 0) return null;
  if (typeof leaderboard.total !== "number" || !(leaderboard.total > 0)) return null;
  const lastPage = Math.max(1, Math.ceil(leaderboard.total / pageSize));
  return page > lastPage ? lastPage : null;
}

/** Which of the four structural shapes `renderShell` would produce — used by app.js to decide whether a full shell replace is needed at all. Includes the mobile/desktop breakpoint since that changes the shell's markup (table vs. card-list container). */
export function shellShape(state, viewport = { isMobile: false }) {
  const breakpoint = viewport.isMobile ? "mobile" : "desktop";
  if (state.leaderboardLoading && !state.leaderboard) return "loading";
  if (state.leaderboardError && !state.leaderboard) return "error";
  if (state.leaderboard && state.leaderboard.rows.length === 0) return "empty";
  if (state.leaderboard) {
    const personaLens = state.persona === "balanced" ? "balanced" : "lens";
    return `table-${breakpoint}-${personaLens}`;
  }
  return "none";
}

/**
 * Patches the persistent `<tbody>` in place for a windowed slice of `rows`.
 * Never touches the shell, the scroll container, or unrelated rows.
 */
export function patchTableBody(tbody, doc, rows, range, opts) {
  const { showPersonaDelta, selectedRepoKey } = opts;
  const { start, end } = range;
  const colCount = columnCount(showPersonaDelta);
  const topHeight = start * ROW_HEIGHT_PX;
  const bottomHeight = Math.max(0, rows.length - end) * ROW_HEIGHT_PX;

  // Carrying the absolute index alongside each row (rather than deriving it
  // later with Array#indexOf) keeps one patch O(window size), never O(total
  // rows) — the difference that matters at the 10,000-row budget.
  const windowed = [{ kind: "spacer-top", height: topHeight }];
  for (let i = start; i < end; i++) windowed.push({ kind: "row", row: rows[i], index: i });
  windowed.push({ kind: "spacer-bottom", height: bottomHeight });

  patchRows(
    tbody,
    windowed,
    (item) => (item.kind === "row" ? rowKey(item.row) : `__${item.kind}__`),
    (item) =>
      item.kind === "row"
        ? createRowElement(doc, item.row, {
            previousRow: item.index > 0 ? rows[item.index - 1] : null,
            showPersonaDelta,
            selected: selectedRepoKey === rowKey(item.row),
          })
        : createSpacerElement(doc, `__${item.kind}__`, colCount, item.height),
    (node, item) => {
      if (item.kind !== "row") {
        updateSpacerElement(node, item.height);
        return;
      }
      const previousRow = item.index > 0 ? rows[item.index - 1] : null;
      updateRowElement(node, item.row, {
        previousRow,
        showPersonaDelta,
        selected: selectedRepoKey === rowKey(item.row),
      });
    }
  );
}

/** Same windowed keyed-patch as `patchTableBody`, for the < 768px card list. */
export function patchCardBody(container, doc, rows, range, opts) {
  const { showPersonaDelta, selectedRepoKey } = opts;
  const { start, end } = range;
  const topHeight = start * CARD_HEIGHT_PX;
  const bottomHeight = Math.max(0, rows.length - end) * CARD_HEIGHT_PX;

  const windowed = [{ kind: "spacer-top", height: topHeight }];
  for (let i = start; i < end; i++) windowed.push({ kind: "row", row: rows[i], index: i });
  windowed.push({ kind: "spacer-bottom", height: bottomHeight });

  patchRows(
    container,
    windowed,
    (item) => (item.kind === "row" ? rowKey(item.row) : `__${item.kind}__`),
    (item) =>
      item.kind === "row"
        ? createCardElement(doc, item.row, {
            previousRow: item.index > 0 ? rows[item.index - 1] : null,
            showPersonaDelta,
            selected: selectedRepoKey === rowKey(item.row),
          })
        : createCardSpacerElement(doc, `__${item.kind}__`),
    (node, item) => {
      if (item.kind !== "row") {
        updateCardSpacerElement(node, item.height);
        return;
      }
      const previousRow = item.index > 0 ? rows[item.index - 1] : null;
      updateCardElement(node, item.row, {
        previousRow,
        showPersonaDelta,
        selected: selectedRepoKey === rowKey(item.row),
      });
    }
  );
}

export { renderCardList };
