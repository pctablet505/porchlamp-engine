// views/leaderboard.js — Screen A (06 §3). `renderShell` builds the STATIC
// structure (tabs, counts, pager, table skeleton with thead + empty tbody)
// ONCE per structural transition (loading/error/empty/normal, a page-size
// change, or switching away from and back to this tab) — never on scroll,
// never on a per-row data change. `patchTableBody`/`patchCardBody` are the
// functions called on those frequent updates: they diff the CURRENT PAGE's
// row list into the persistent `<tbody>`/card container via `patchRows`
// (components/patch.js).
//
// Pagination (owner instruction 2026-09-13, replacing an earlier
// no-pagination ruling): one API page is loaded at a time (20/50/100/500/
// 1000 rows, default 20); for the two large page sizes the loaded page is
// STILL windowed exactly like the table always was (only the visible slice
// plus a buffer exists in the DOM) — pagination and windowing solve
// different problems and this packet keeps both.

import * as copy from "../copy.js";
import {
  rowKey,
  createRowElement,
  updateRowElement,
  createSpacerElement,
  updateSpacerElement,
  createCardElement,
  updateCardElement,
  createCardSpacerElement,
  updateCardSpacerElement,
  computeVisibleRange,
  columnCount,
  renderColumnHeader,
  renderCardList,
  ROW_HEIGHT_PX,
  CARD_HEIGHT_PX,
} from "../components/table.js";
import { patchRows } from "../components/patch.js";
import { PAGE_SIZES } from "../state.js";

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
  return `<div class="error-banner" role="alert">${escapeHtml(copy.errorBannerText(error.endpoint, error.status, error.detail))}</div>`;
}

export function renderEmptyState(gatedCount, unratedCount) {
  return `<div class="empty-state">${escapeHtml(copy.emptyLeaderboardText(gatedCount, unratedCount))}</div>`;
}

/**
 * U-4: "hidden by this lens (N)" disclosure. `hiddenCount` is
 * `counts.rated - snapshot.persona_visible_counts[persona]` — computed by
 * the caller, since it needs both the leaderboard and snapshot payloads.
 */
export function renderCountsStrip(counts, hiddenCount) {
  if (!counts) return "";
  const hiddenSpan =
    typeof hiddenCount === "number" && hiddenCount > 0
      ? `<span>${escapeHtml(copy.personaHiddenDisclosure(hiddenCount))}</span>`
      : "";
  return (
    `<div class="counts-strip" data-counts-strip="true">` +
    `<span>${escapeHtml(copy.GATED_GROUP_TITLE)}: ${counts.gated}</span>` +
    `<span>${escapeHtml(copy.UNRATED_GROUP_TITLE)}: ${counts.unrated}</span>` +
    hiddenSpan +
    `</div>`
  );
}

export function renderTabs(activeTab, counts) {
  const rated = counts ? counts.rated : "";
  const notRankedCount = counts ? counts.gated + counts.unrated : "";
  return (
    `<div class="tabs" role="tablist">` +
    `<button type="button" class="tab" role="tab" aria-selected="${activeTab === "ranked"}" data-tab="ranked">` +
    `${escapeHtml(copy.TAB_RANKED)}<span class="count">${rated}</span></button>` +
    `<button type="button" class="tab" role="tab" aria-selected="${activeTab === "notranked"}" data-tab="notranked">` +
    `${escapeHtml(copy.TAB_NOT_RANKED)}<span class="count">${notRankedCount}</span></button>` +
    `</div>`
  );
}

/**
 * The pager UI: page-size dropdown (20/50/100/500/1000) plus
 * first/previous/next/last controls and a "Page i of N (M rows)"
 * indicator. Shared between the Ranked and Not-ranked lists (owner
 * instruction: "the same controls on the Not-ranked list") via the
 * `dataPrefix` param, which namespaces the `data-*` hooks app.js binds to
 * (`pager` for the leaderboard, `notranked-pager` for the not-ranked list).
 */
export function renderPager(page, pageSize, total, dataPrefix = "pager") {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const sizeOptions = PAGE_SIZES.map(
    (n) => `<option value="${n}"${n === pageSize ? " selected" : ""}>${n}</option>`
  ).join("");
  return (
    `<div class="pager">` +
    `<label>${escapeHtml(copy.PAGE_SIZE_LABEL)} ` +
    `<select class="control" data-${dataPrefix}-size-select="true">${sizeOptions}</select></label>` +
    `<button type="button" data-${dataPrefix}-first="true" aria-label="${escapeHtml(copy.PAGER_FIRST_LABEL)}"${page <= 1 ? " disabled" : ""}>&laquo;</button>` +
    `<button type="button" data-${dataPrefix}-prev="true" aria-label="${escapeHtml(copy.PAGER_PREV_LABEL)}"${page <= 1 ? " disabled" : ""}>&larr;</button>` +
    `<span data-${dataPrefix}-status="true">${escapeHtml(copy.pagerStatusText(page, totalPages, total))}</span>` +
    `<button type="button" data-${dataPrefix}-next="true" aria-label="${escapeHtml(copy.PAGER_NEXT_LABEL)}"${page >= totalPages ? " disabled" : ""}>&rarr;</button>` +
    `<button type="button" data-${dataPrefix}-last="true" aria-label="${escapeHtml(copy.PAGER_LAST_LABEL)}"${page >= totalPages ? " disabled" : ""}>&raquo;</button>` +
    `</div>`
  );
}

/** The one-time structural shell for a given (loading/error/empty/normal) state. `viewport.isMobile` picks the table vs. card-list shell (06 §5: below 768px the leaderboard is a card list). */
/** N for U-4's "hidden by this lens (N)" disclosure, or `undefined` under the balanced lens / before the snapshot has loaded. */
export function computeHiddenCount(state) {
  const counts = state.leaderboard ? state.leaderboard.counts : null;
  if (!counts || state.persona === "balanced" || !state.snapshot) return undefined;
  const visible = state.snapshot.persona_visible_counts[state.persona];
  return typeof visible === "number" ? counts.rated - visible : undefined;
}

export function renderShell(state, viewport = { isMobile: false }) {
  const { leaderboardLoading, leaderboardError, leaderboard } = state;
  const counts = leaderboard ? leaderboard.counts : null;
  const header = renderTabs(state.tab, counts) + renderCountsStrip(counts, computeHiddenCount(state));

  let body;
  if (leaderboardLoading && !leaderboard) {
    body = renderSkeleton();
  } else if (leaderboardError && !leaderboard) {
    body = renderErrorBanner(leaderboardError);
  } else if (leaderboard && leaderboard.rows.length === 0) {
    body = renderEmptyState(counts.gated, counts.unrated);
  } else if (leaderboard) {
    const showPersonaDelta = state.persona !== "balanced";
    const progressAndError =
      `<div class="refetch-progress" data-refetch-progress="true" hidden></div>` +
      `<div class="error-banner" role="alert" data-leaderboard-error="true" hidden></div>`;
    const pager = renderPager(state.page, state.pageSize, leaderboard.total, "pager");
    body = viewport.isMobile
      ? pager + progressAndError + `<div class="leaderboard-cards" data-leaderboard-cards="true"></div>`
      : pager +
        progressAndError +
        `<table class="leaderboard-table" role="table" aria-label="${escapeHtml(copy.NAV_LEADERBOARD)}">` +
        `<thead>${renderColumnHeader(state.sort, showPersonaDelta)}</thead>` +
        `<tbody data-leaderboard-tbody="true"></tbody>` +
        `</table>`;
  } else {
    body = "";
  }

  return (
    `<section aria-labelledby="leaderboard-heading">` +
    `<h1 id="leaderboard-heading" class="view-heading sr-only">${escapeHtml(copy.NAV_LEADERBOARD)}</h1>` +
    header +
    body +
    `</section>`
  );
}

/**
 * Updates an already-rendered pager's status text and button
 * disabled-states in place (never a shell rebuild — a page/pageSize change
 * is not a structural shape change, see `shellShape`). `root` is the
 * element containing the `data-${dataPrefix}-*` hooks (usually `main`).
 */
export function syncPager(root, page, pageSize, total, dataPrefix = "pager") {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const status = root.querySelector(`[data-${dataPrefix}-status]`);
  if (status) {
    const text = copy.pagerStatusText(page, totalPages, total);
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
    const el = root.querySelector(selector);
    if (el && el.disabled !== disabled) el.disabled = disabled;
  }
}

/** Which of the four structural shapes `renderShell` would produce — used by app.js to decide whether a full shell replace is needed at all. Includes the mobile/desktop breakpoint since that changes the shell's markup (table vs. card-list container). */
export function shellShape(state, viewport = { isMobile: false }) {
  const breakpoint = viewport.isMobile ? "mobile" : "desktop";
  if (state.leaderboardLoading && !state.leaderboard) return "loading";
  if (state.leaderboardError && !state.leaderboard) return "error";
  if (state.leaderboard && state.leaderboard.rows.length === 0) return "empty";
  if (state.leaderboard) return `table-${breakpoint}`;
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
          })
        : createSpacerElement(doc, `__${item.kind}__`, colCount),
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
