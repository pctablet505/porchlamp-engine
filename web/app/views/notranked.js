// views/notranked.js — Screen A2 (06 §3): two (or three, under a
// non-balanced persona) groups — Gated, Unrated, and Persona-hidden — each
// row a reason chip from the U-10 map, opening the drawer with the
// gate/unrated reason shown first.
//
// Pagination here is CLIENT-SIDE (the whole list is fetched once; this
// endpoint has no server-side paging fields the way the leaderboard
// does): the combined [gated..., unrated...,
// personaHidden...] sequence is sliced for the current page, and a group
// heading renders whenever the page's slice reaches that group's first row.
//
// A list by reason (routes.js "notranked-*", `/not-ranked/<reason>/<page>/`) is a page of this view too: every
// balanced not-ranked project with that reason, A to Z, 200 a page, one plain sentence each, with a line of links to
// every page of the list. Its shell holds the whole page (`renderShell`); web/prerender.mjs draws the same shell into
// the exported page, and the app takes it over without drawing it again (`adoptReasonListBoard`).

import * as copy from "../copy.js";
import { fill, hook } from "../components/fill.js";
import { groupThousands } from "../components/table.js";
import { routePath, routeTitle, routesOfView } from "../routes.js";
import { TITLE_SITE_SUFFIX, filterNotRankedRows, notRankedReasonRows, notRankedTabCount, reasonListPage } from "../state.js";
import {
  renderPager,
  renderFilteredEmpty,
  syncPager,
  renderTabs,
  tabHrefs,
  renderCountsStrip,
  renderOnboardingBanner,
  personaDeltaForState,
  personaVisibleForState,
} from "./leaderboard.js";

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

export function renderSkeleton(rowCount = 8) {
  const rows = Array.from({ length: rowCount }, () => `<div class="skeleton-row"></div>`).join("");
  return `<div aria-hidden="true">${rows}</div><span class="sr-only">${escapeHtml(copy.LOADING_ROW_SR_TEXT)}</span>`;
}

export function renderErrorBanner(error) {
  return (
    `<div class="error-banner" role="alert">${escapeHtml(copy.errorBannerText(error.endpoint, error.status, error.detail))}` +
    ` <button type="button" class="icon-button" data-notranked-retry="true">${escapeHtml(copy.RETRY_LABEL)}</button></div>`
  );
}

function renderReasonChip(row) {
  const text = row.gate_reason
    ? copy.gateReasonText(row.gate_reason)
    : row.unrated_reason
      ? copy.unratedReasonText(row.unrated_reason, row.coverage)
      : copy.personaGateReason(row.persona_gate_reason);
  return `<span class="reason-chip">${escapeHtml(text)}</span>`;
}

function renderRow(row, selected = false) {
  return (
    `<div class="notranked-row${selected ? " row-selected" : ""}" data-owner="${escapeHtml(row.owner)}" data-name="${escapeHtml(row.name)}" tabindex="0" role="button" aria-label="Open ${escapeHtml(row.owner)}/${escapeHtml(row.name)}">` +
    `<span><span class="repo-owner">${escapeHtml(row.owner)}/</span><span class="repo-name">${escapeHtml(row.name)}</span><span class="eco-chip">${escapeHtml(copy.ecosystemName(row.ecosystem))}</span></span>` +
    renderReasonChip(row) +
    `</div>`
  );
}

const filterRows = filterNotRankedRows;

/** Flattens the three groups into one paged sequence, each item tagged with its group label and the group's total count (for the heading). */
export function flattenGroups(notRanked, opts = {}) {
  if (!notRanked) return [];
  const { ecosystem = "", query = "" } = opts || {};
  const gated = filterRows(notRanked.gated, ecosystem, query);
  const unrated = filterRows(notRanked.unrated, ecosystem, query);
  const personaHidden = filterRows(notRanked.persona_hidden, ecosystem, query);
  const groups = [
    [copy.GATED_GROUP_TITLE, gated],
    [copy.UNRATED_GROUP_TITLE, unrated],
    ...(personaHidden.length ? [[copy.PERSONA_HIDDEN_GROUP_TITLE, personaHidden]] : []),
  ];
  const flat = [];
  for (const [label, rows] of groups) {
    for (const row of rows) flat.push({ label, groupTotal: rows.length, row });
  }
  return flat;
}

/** Renders one page's slice, inserting a group heading whenever the slice reaches a new group's first row. */
export function renderPageRows(flat, page, pageSize, selectedRepoKey = null) {
  const start = (page - 1) * pageSize;
  const slice = flat.slice(start, start + pageSize);
  let html = "";
  let currentLabel = null;
  for (const item of slice) {
    if (item.label !== currentLabel) {
      currentLabel = item.label;
      html += `<h2 class="view-heading">${escapeHtml(item.label)} (${item.groupTotal})</h2>`;
    }
    const key = `${item.row.owner}/${item.row.name}`;
    html += renderRow(item.row, selectedRepoKey === key);
  }
  return html;
}

export function shellShape(state) {
  const { notRankedLoading, notRankedError, notRanked } = state;
  if (notRankedLoading && !notRanked) return "loading";
  if (notRankedError && !notRanked) return "error";
  if (!notRanked) return "empty";
  const flat = flattenGroups(notRanked, { ecosystem: state.ecosystem, query: state.query });
  if (flat.length === 0) return "empty";
  return "table";
}

/**
 * The shell also embeds the Ranked/Not-ranked tabs (with their counts), so
 * a rebuild is needed once `state.leaderboard` first arrives even though
 * `shellShape`'s own return value did not change. This stays a SEPARATE
 * function rather than being folded into `shellShape`'s return string, so
 * every `shellShape(state) === "..."` equality check in this module and in
 * app.js keeps matching the bare shape (`"empty"`, not
 * `"empty-counted"`).
 */
export function rebuildKey(state) {
  // A list by reason is drawn whole: its shell is drawn again whenever what it shows changes.
  if (reasonListPage(state)) return `reason:${renderReasonListShell(state)}`;
  const shape = shellShape(state);
  const filterSuffix = shape === "empty" ? `:${state.ecosystem || ""}:${state.query || ""}` : "";
  return `${shape}${state.leaderboard ? "-counted" : ""}${filterSuffix}`;
}

export function renderShell(state) {
  if (reasonListPage(state)) return renderReasonListShell(state);
  const { notRankedError, notRanked } = state;
  const shape = shellShape(state);
  const headerHtml = `<h1 id="notranked-heading" class="view-heading sr-only">${escapeHtml(copy.NAV_NOT_RANKED)}</h1>`;
  // The Ranked/Not-ranked sub-tabs (and the gated/unrated counts beside
  // them) must stay reachable from EITHER view — without this, switching
  // to Not-ranked would strand the user with no way back to Ranked.
  const leaderboardCounts = state.leaderboard ? state.leaderboard.counts : null;
  const flat = notRanked ? flattenGroups(notRanked, { ecosystem: state.ecosystem, query: state.query }) : null;
  const tabsHtml =
    renderOnboardingBanner() +
    renderTabs(state.tab, leaderboardCounts, personaVisibleForState(state), notRankedTabCount(state), tabHrefs(state)) +
    renderCountsStrip(leaderboardCounts, personaDeltaForState(state));

  if (shape === "loading") return `<section aria-labelledby="notranked-heading">${headerHtml}${tabsHtml}${renderSkeleton()}</section>`;
  if (shape === "error") return `<section aria-labelledby="notranked-heading">${headerHtml}${tabsHtml}${renderErrorBanner(notRankedError)}</section>`;
  if (shape === "empty") {
    const q = (state.query || "").trim();
    const empty =
      q || state.ecosystem
        ? renderFilteredEmpty(q, state.ecosystem || "", "ranked", "gated or unrated")
        : `<div class="empty-state">${escapeHtml(copy.NOT_RANKED_EMPTY)}</div>`;
    return `<section aria-labelledby="notranked-heading">${headerHtml}${tabsHtml}${empty}</section>`;
  }

  const totalPages = Math.max(1, Math.ceil(flat.length / state.notRankedPageSize));
  const page = Math.min(Math.max(1, state.notRankedPage), totalPages);
  const pager = renderPager(page, state.notRankedPageSize, flat.length, "notranked-pager");
  const bottomPager = renderPager(page, state.notRankedPageSize, flat.length, "notranked-pager", { bottom: true });
  return (
    `<section aria-labelledby="notranked-heading">` +
    headerHtml +
    tabsHtml +
    `<div class="error-banner" role="alert" data-notranked-error="true" hidden></div>` +
    (notRanked.truncated ? `<p class="matrix-caption">${escapeHtml(copy.NOT_RANKED_TRUNCATED)}</p>` : "") +
    renderReasonListsLine(state) +
    pager +
    `<div data-notranked-rows="true"></div>` +
    bottomPager +
    `</section>`
  );
}

/** Patches the not-ranked rows + pager for the current page — never rebuilds the shell. */
export function patchNotRankedBody(root, state) {
  const container = root.querySelector("[data-notranked-rows]");
  if (!container) return;
  const flat = flattenGroups(state.notRanked, { ecosystem: state.ecosystem, query: state.query });
  const totalPages = Math.max(1, Math.ceil(flat.length / state.notRankedPageSize));
  const page = Math.min(Math.max(1, state.notRankedPage), totalPages);
  const html = renderPageRows(flat, page, state.notRankedPageSize, state.selectedRepoKey);
  if (container.innerHTML !== html) container.innerHTML = html;
  syncPager(root, page, state.notRankedPageSize, flat.length, "notranked-pager");
  const notRankedCountEl = root.querySelector('[data-route-tab="notranked"] .count');
  const notRankedCount = notRankedTabCount(state);
  if (notRankedCountEl && typeof notRankedCount === "number" && notRankedCountEl.textContent !== String(notRankedCount)) {
    notRankedCountEl.textContent = String(notRankedCount);
  }
  const rankedCount = personaVisibleForState(state);
  const rankedCountEl = root.querySelector('[data-route-tab="ranked"] .count');
  if (rankedCountEl && typeof rankedCount === "number" && rankedCountEl.textContent !== String(rankedCount)) {
    rankedCountEl.textContent = String(rankedCount);
  }
  const strip = root.querySelector("[data-counts-strip]");
  if (strip && state.leaderboard) {
    const replaced = renderCountsStrip(state.leaderboard.counts, personaDeltaForState(state));
    if (strip.outerHTML !== replaced) strip.outerHTML = replaced;
  }
  const errBanner = root.querySelector("[data-notranked-error]");
  if (errBanner) {
    if (state.notRankedError) {
      errBanner.hidden = false;
      errBanner.innerHTML =
        `${escapeHtml(copy.errorBannerText(state.notRankedError.endpoint, state.notRankedError.status, state.notRankedError.detail))}` +
        ` <button type="button" class="icon-button" data-notranked-retry="true">${escapeHtml(copy.RETRY_LABEL)}</button>`;
    } else {
      errBanner.hidden = true;
    }
  }
}

// --- The lists by reason --------------------------------------------------------------------------------------------

//: The lists by reason, in the route table's order: `{ id, reason }`.
export const REASON_LISTS = Object.freeze(
  routesOfView("notranked")
    .filter((route) => route.fixed && route.fixed.reason)
    .map((route) => Object.freeze({ id: route.id, reason: route.fixed.reason }))
);

/** A list's name with its first letter in capitals, as a link in the "Lists by reason" line names it. */
function capitalised(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** The report's value `id` as text, or null where there is no report or it lacks the value. */
function reportValue(report, id) {
  const values = report && report.values;
  return values && values[id] !== undefined && values[id] !== null && values[id] !== "" ? String(values[id]) : null;
}

/** One sentence: `template` with `hooks` as hooks (`list.<id>`) and `texts` as plain text; `{ html, text }`. */
function sentence(template, hooks = {}, texts = {}) {
  const slots = {};
  const plain = {};
  for (const [id, value] of Object.entries(hooks)) {
    slots[id] = hook(`list.${id}`, value);
    plain[id] = value;
  }
  for (const [id, value] of Object.entries(texts)) {
    slots[id] = escapeHtml(value);
    plain[id] = value;
  }
  return { html: fill(template, slots), text: String(template).replace(/\{([a-z][a-z0-9_]*)\}/g, (_m, id) => plain[id]) };
}

/** The intro's first sentence: the reason's own (the copy module's), the black-hole one with the report's deadline as a hook. */
function reasonSentence(reason, report) {
  const deadline = reportValue(report, "deadline_days");
  if (reason === "black_hole" && deadline !== null) {
    return sentence(copy.NOT_RANKED_LIST_REASONS.black_hole, { deadline_days: deadline });
  }
  const own = reason === "black_hole" ? null : copy.NOT_RANKED_LIST_REASONS[reason];
  return sentence(own || copy.gateReasonText(reason));
}

/**
 * The head of a list by reason: `{ heading, sentences }`, the heading and each sentence `{ html, text }`. The intro
 * (the reason, the count, and on the black-hole list the test, from the report) waits for the not-ranked list; the
 * test needs the report. Null at any other address.
 */
export function reasonListHead(state) {
  const at = reasonListPage(state);
  if (!at) return null;
  // The heading is the list's title without its page part and the site's name.
  const text = routeTitle(at.route.id, { list: copy.NOT_RANKED_LIST_NAMES[at.reason], page_of: "" }).replace(TITLE_SITE_SUFFIX, "");
  const heading = { html: escapeHtml(text), text };
  if (at.total === null) return { heading, sentences: [] };
  const sentences = [
    reasonSentence(at.reason, state.report),
    sentence(copy.NOT_RANKED_LIST_COUNT, { total: groupThousands(at.total) }, { page_size: groupThousands(at.pageSize) }),
  ];
  const minN = reportValue(state.report, "min_n");
  const ci = reportValue(state.report, "ci_percent");
  if (at.reason === "black_hole" && reportValue(state.report, "deadline_days") !== null && minN !== null && ci !== null) {
    sentences.push(sentence(copy.NOT_RANKED_LIST_TEST, { min_n: minN, ci_percent: ci }));
  }
  return { heading, sentences };
}

/** The description of a list by reason's page: its intro's first two sentences; null where it has none. */
export function reasonListDescription(state) {
  const head = reasonListHead(state);
  return head && head.sentences.length >= 2 ? `${head.sentences[0].text} ${head.sentences[1].text}` : null;
}

/** A row's whole text: "owner/name (Ecosystem): sentence". A black hole with its numbers and the report's deadline
 *  says how many counted pull requests went unanswered; any other row says its reason, as the drawer does. */
export function reasonRowText(row, report = null) {
  const unanswered = typeof row.unanswered === "number" ? row.unanswered : null;
  const counted = typeof row.counted === "number" ? row.counted : null;
  const deadline = reportValue(report, "deadline_days");
  let said;
  if (row.gate_reason === "black_hole" && unanswered !== null && counted !== null && deadline !== null) {
    said = copy.NOT_RANKED_LIST_ROW_BLACK_HOLE.replace("{unanswered}", groupThousands(unanswered))
      .replace("{counted}", groupThousands(counted))
      .replace("{deadline_days}", deadline);
  } else {
    said = row.gate_reason ? copy.gateReasonText(row.gate_reason) : copy.unratedReasonText(row.unrated_reason, row.coverage);
  }
  return copy.NOT_RANKED_LIST_ROW.replace("{repo}", `${row.owner}/${row.name}`)
    .replace("{ecosystem}", copy.ecosystemName(row.ecosystem))
    .replace("{sentence}", said);
}

/** The line of links to every page of the list, the page shown marked; drawn above and below the rows. */
function reasonPagesLine(at) {
  const links = [];
  for (let page = 1; page <= at.pages; page++) {
    links.push(
      `<a href="${escapeHtml(routePath(at.route.id, { page }))}" class="onboarding-link"${page === at.page ? ` aria-current="page"` : ""}>${page}</a>`
    );
  }
  const label = fill(copy.NOT_RANKED_LIST_PAGES_LABEL, { page_size: escapeHtml(groupThousands(at.pageSize)) });
  return `<div class="list-pages" data-list-pages="true"><span>${label}</span> ${links.join(" ")}</div>`;
}

/**
 * The "Lists by reason" line, on every not-ranked view: each list's name, with its count from the balanced list, a
 * plain link to its first page. A list with no projects has no page (the export writes none), so it is left out.
 * Under another lens the loaded list is that lens's: the counts are not known, and every name stands without one.
 */
export function renderReasonListsLine(state) {
  const balanced = state.notRanked && state.persona === "balanced" && !state.notRanked.truncated ? state.notRanked : null;
  const items = [];
  for (const { id, reason } of REASON_LISTS) {
    const name = capitalised(copy.NOT_RANKED_LIST_NAMES[reason]);
    const count = balanced ? notRankedReasonRows(balanced, reason).length : null;
    if (count === 0) continue;
    const label = count === null ? name : copy.NOT_RANKED_LISTS_ITEM.replace("{list}", name).replace("{count}", groupThousands(count));
    items.push(`<a href="${escapeHtml(routePath(id, { page: 1 }))}" class="onboarding-link">${escapeHtml(label)}</a>`);
  }
  return `<p class="reason-lists" data-reason-lists="true"><span>${escapeHtml(copy.NOT_RANKED_LISTS_LABEL)}</span> ${items.join(" · ")}</p>`;
}

/** The rows of a page of a list by reason: an ordered list numbered from the page's first position. */
function reasonRowsHtml(at, report) {
  const items = at.rows
    .map((row) => `<li data-owner="${escapeHtml(row.owner)}" data-name="${escapeHtml(row.name)}">${escapeHtml(reasonRowText(row, report))}</li>`)
    .join("");
  return `<ol class="reason-list" start="${at.first}" data-reason-rows="true">${items}</ol>`;
}

/** The shell of a list by reason: its head, the tabs and counts as this view draws them, the lists line, and its page
 *  of rows between two pages lines; the skeleton or the error until the not-ranked list has loaded. */
function renderReasonListShell(state) {
  const at = reasonListPage(state);
  const head = reasonListHead(state);
  const intro = head.sentences.map((s) => s.html).join(" ");
  const leaderboardCounts = state.leaderboard ? state.leaderboard.counts : null;
  const headHtml =
    `<div class="list-head" data-list-head="true">` +
    `<h1 id="notranked-heading" class="view-heading">${head.heading.html}</h1>` +
    (intro ? `<p class="list-intro" data-list-intro="true">${intro}</p>` : "") +
    `</div>`;
  const tabsHtml =
    renderOnboardingBanner() +
    renderTabs(state.tab, leaderboardCounts, personaVisibleForState(state), notRankedTabCount(state), tabHrefs(state)) +
    renderCountsStrip(leaderboardCounts, personaDeltaForState(state));
  let body;
  if (at.rows) {
    const pages = reasonPagesLine(at);
    body = renderReasonListsLine(state) + pages + reasonRowsHtml(at, state.report) + pages;
  } else if (state.notRankedError) {
    body = renderReasonListsLine(state) + renderErrorBanner(state.notRankedError);
  } else {
    body = renderReasonListsLine(state) + renderSkeleton();
  }
  return `<section aria-labelledby="notranked-heading">${headHtml}${tabsHtml}${body}</section>`;
}

/** The key of the list by reason a page shows (its route and page), or null at any other address. */
export function reasonListKey(state) {
  const at = reasonListPage(state);
  return at ? `${at.route.id}:${at.page}` : null;
}

/** A list by reason drawn for the export: the shell, marked with its key (`reasonListKey`) for the app. */
export function renderPrerenderedReasonList(state) {
  const key = reasonListKey(state);
  if (!key || !state.notRanked) throw new Error("the prerendered list by reason needs its address and the not-ranked list");
  return renderReasonListShell(state).replace(
    `<section aria-labelledby="notranked-heading">`,
    `<section aria-labelledby="notranked-heading" data-prerendered-board="${escapeHtml(key)}">`
  );
}

/**
 * Takes over a list by reason the export drew into `root` for `state`: the board's mark removed, and nothing redrawn
 * when the page holds what the app would draw (`renderShell`, both read back through the document's own serializer);
 * drawn again, whole, when the app's data says otherwise (a report that failed to load). Returns `false`,
 * touching nothing, when the page shows another list or page, or `state` is not at a list by reason; the caller then
 * draws the shell. `doc` makes the probe that reads the app's markup.
 */
export function adoptReasonListBoard(root, state, doc) {
  const board = root.querySelector("[data-prerendered-board]");
  const key = reasonListKey(state);
  if (!board || !key || board.getAttribute("data-prerendered-board") !== key) return false;
  board.removeAttribute("data-prerendered-board");
  // Without the not-ranked list (it failed to load) the page keeps the list the export read from the same files.
  if (!state.notRanked) return true;
  const html = renderReasonListShell(state);
  const probe = doc.createElement("div");
  probe.innerHTML = html;
  if (probe.innerHTML !== board.outerHTML) root.innerHTML = html;
  return true;
}
