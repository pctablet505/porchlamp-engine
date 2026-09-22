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
  syncPager,
  renderTabs,
  renderCountsStrip,
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
  return `<div class="error-banner" role="alert">${escapeHtml(copy.errorBannerText(error.endpoint, error.status, error.detail))}</div>`;
}

function renderReasonChip(row) {
  const text = row.gate_reason
    ? copy.gateReasonText(row.gate_reason)
    : row.unrated_reason
      ? copy.unratedReasonText(row.unrated_reason, row.coverage)
      : copy.personaGateReason(row.persona_gate_reason);
  return `<span class="reason-chip">${escapeHtml(text)}</span>`;
}

function renderRow(row) {
  return (
    `<div class="notranked-row" data-owner="${escapeHtml(row.owner)}" data-name="${escapeHtml(row.name)}" tabindex="0" role="button" aria-label="Open ${escapeHtml(row.owner)}/${escapeHtml(row.name)}">` +
    `<span><span class="repo-owner">${escapeHtml(row.owner)}/</span><span class="repo-name">${escapeHtml(row.name)}</span><span class="eco-chip">${escapeHtml(row.ecosystem)}</span></span>` +
    renderReasonChip(row) +
    `</div>`
  );
}

/** Flattens the three groups into one paged sequence, each item tagged with its group label and the group's total count (for the heading). */
export function flattenGroups(notRanked) {
  const groups = [
    [copy.GATED_GROUP_TITLE, notRanked.gated],
    [copy.UNRATED_GROUP_TITLE, notRanked.unrated],
    ...(notRanked.persona_hidden && notRanked.persona_hidden.length
      ? [[copy.PERSONA_HIDDEN_GROUP_TITLE, notRanked.persona_hidden]]
      : []),
  ];
  const flat = [];
  for (const [label, rows] of groups) {
    for (const row of rows) flat.push({ label, groupTotal: rows.length, row });
  }
  return flat;
}

/** Renders one page's slice, inserting a group heading whenever the slice reaches a new group's first row. */
export function renderPageRows(flat, page, pageSize) {
  const start = (page - 1) * pageSize;
  const slice = flat.slice(start, start + pageSize);
  let html = "";
  let currentLabel = null;
  for (const item of slice) {
    if (item.label !== currentLabel) {
      currentLabel = item.label;
      html += `<h2 class="view-heading">${escapeHtml(item.label)} (${item.groupTotal})</h2>`;
    }
    html += renderRow(item.row);
  }
  return html;
}

export function shellShape(state) {
  const { notRankedLoading, notRankedError, notRanked } = state;
  if (notRankedLoading && !notRanked) return "loading";
  if (notRankedError && !notRanked) return "error";
  if (!notRanked || (notRanked.gated.length === 0 && notRanked.unrated.length === 0 && (!notRanked.persona_hidden || notRanked.persona_hidden.length === 0))) {
    return "empty";
  }
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
  return `${shellShape(state)}${state.leaderboard ? "-counted" : ""}`;
}

export function renderShell(state) {
  const { notRankedError, notRanked } = state;
  const shape = shellShape(state);
  const headerHtml = `<h1 id="notranked-heading" class="view-heading sr-only">${escapeHtml(copy.NAV_NOT_RANKED)}</h1>`;
  // The Ranked/Not-ranked sub-tabs (and the gated/unrated counts beside
  // them) must stay reachable from EITHER view — without this, switching
  // to Not-ranked would strand the user with no way back to Ranked.
  const leaderboardCounts = state.leaderboard ? state.leaderboard.counts : null;
  const tabsHtml =
    renderTabs(state.tab, leaderboardCounts, personaVisibleForState(state)) +
    renderCountsStrip(leaderboardCounts, personaDeltaForState(state));

  if (shape === "loading") return `<section aria-labelledby="notranked-heading">${headerHtml}${tabsHtml}${renderSkeleton()}</section>`;
  if (shape === "error") return `<section aria-labelledby="notranked-heading">${headerHtml}${tabsHtml}${renderErrorBanner(notRankedError)}</section>`;
  if (shape === "empty") {
    return `<section aria-labelledby="notranked-heading">${headerHtml}${tabsHtml}<div class="empty-state">${escapeHtml(copy.NOT_RANKED_EMPTY)}</div></section>`;
  }

  const flat = flattenGroups(notRanked);
  const pager = renderPager(state.notRankedPage, state.notRankedPageSize, flat.length, "notranked-pager");
  return (
    `<section aria-labelledby="notranked-heading">` +
    headerHtml +
    tabsHtml +
    `<div class="error-banner" role="alert" data-notranked-error="true" hidden></div>` +
    pager +
    `<div data-notranked-rows="true"></div>` +
    `</section>`
  );
}

/** Patches the not-ranked rows + pager for the current page — never rebuilds the shell. */
export function patchNotRankedBody(root, state) {
  const container = root.querySelector("[data-notranked-rows]");
  if (!container) return;
  const flat = flattenGroups(state.notRanked);
  const html = renderPageRows(flat, state.notRankedPage, state.notRankedPageSize);
  // This is a whole-body innerHTML
  // replacement, not the keyed patchRows() diff the leaderboard table uses.
  // Safe today because no interaction here holds DOM focus on a row while a
  // page rebuild can land under it (the client only ever paginates a
  // preloaded, already-fetched page of the not-ranked list). It becomes a
  // real correctness/focus concern once this list is server-paged like the
  // leaderboard (prefetch-next-page, page-size-change-preserves-row, etc.) —
  // at that point switch this to patchRows()-style keyed diffing too.
  if (container.innerHTML !== html) container.innerHTML = html;
  syncPager(root, state.notRankedPage, state.notRankedPageSize, flat.length, "notranked-pager");
  const errBanner = root.querySelector("[data-notranked-error]");
  if (errBanner) {
    if (state.notRankedError) {
      errBanner.hidden = false;
      errBanner.textContent = copy.errorBannerText(state.notRankedError.endpoint, state.notRankedError.status, state.notRankedError.detail);
    } else {
      errBanner.hidden = true;
    }
  }
}
