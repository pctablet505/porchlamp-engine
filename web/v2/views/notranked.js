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

import * as copy from "../copy.js";
import {
  renderPager,
  renderFilteredEmpty,
  syncPager,
  renderTabs,
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
    `<span><span class="repo-owner">${escapeHtml(row.owner)}/</span><span class="repo-name">${escapeHtml(row.name)}</span><span class="eco-chip">${escapeHtml(row.ecosystem)}</span></span>` +
    renderReasonChip(row) +
    `</div>`
  );
}

function filterRows(rows, ecosystem, query) {
  if (!rows) return [];
  const eco = ecosystem ? ecosystem.trim().toLowerCase() : "";
  const q = query ? query.trim().toLowerCase() : "";
  if (!eco && !q) return rows;
  return rows.filter((r) => {
    if (eco && String(r.ecosystem || "").toLowerCase() !== eco) return false;
    if (q && !`${r.owner}/${r.name}`.toLowerCase().includes(q)) return false;
    return true;
  });
}

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
  const shape = shellShape(state);
  const filterSuffix = shape === "empty" ? `:${state.ecosystem || ""}:${state.query || ""}` : "";
  return `${shape}${state.leaderboard ? "-counted" : ""}${filterSuffix}`;
}

export function renderShell(state) {
  const { notRankedError, notRanked } = state;
  const shape = shellShape(state);
  const headerHtml = `<h1 id="notranked-heading" class="view-heading sr-only">${escapeHtml(copy.NAV_NOT_RANKED)}</h1>`;
  // The Ranked/Not-ranked sub-tabs (and the gated/unrated counts beside
  // them) must stay reachable from EITHER view — without this, switching
  // to Not-ranked would strand the user with no way back to Ranked.
  const leaderboardCounts = state.leaderboard ? state.leaderboard.counts : null;
  const flat = notRanked ? flattenGroups(notRanked, { ecosystem: state.ecosystem, query: state.query }) : null;
  const notRankedCount = flat ? flat.length : null;
  const tabsHtml =
    renderOnboardingBanner() +
    renderTabs(state.tab, leaderboardCounts, personaVisibleForState(state), notRankedCount) +
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
  const notRankedCountEl = root.querySelector('[data-tab="notranked"] .count');
  if (notRankedCountEl && notRankedCountEl.textContent !== String(flat.length)) {
    notRankedCountEl.textContent = String(flat.length);
  }
  const rankedCount = personaVisibleForState(state);
  const rankedCountEl = root.querySelector('[data-tab="ranked"] .count');
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
