// components/table.js — the leaderboard table: column header, per-row cell
// markup, the client-side D-TIE-1 level derivation, and real DOM element
// builders (`createRowElement`, `updateRowElement`) used by `patchRows`
// instead of `innerHTML` on the table body. `rowCells` is the single
// source of truth for a row's per-cell HTML so the "create" and "update"
// paths can never drift apart.

import * as copy from "../copy.js";
import { renderCoverageBadge, renderQuadrant, renderSuspicionIcon, renderTierCell } from "./badge.js";
import { renderSparkbarGroup } from "./sparkbar.js";
import { projectPath } from "../routes.js";
import { daysText, percentWhole, replyTimeText } from "../views/project.js";
import { escapeHtml } from "../escape.js";

// web/prerender.mjs escapes the page heads it writes with escape.js's escape, which it imports from here.
export { escapeHtml };

export const PILLAR_ORDER = ["responsiveness", "criticality", "governance", "rigor", "accessibility"];

/** Columns Screen A allows sorting on (U-3: vanity columns never sortable). */
export const SORTABLE_COLUMNS = new Set([
  "rank",
  "porchlamp",
  "coverage",
  "tier",
  "p_responsiveness",
  "p_criticality",
  "p_governance",
  "p_rigor",
  "p_accessibility",
]);

/**
 * The sort-encoding contract (documented here, in copy.js, and in
 * scripts/serve_ui_fixtures.py's module docstring — all three MUST
 * agree): `sort=<field>` means that field's "best first" direction —
 * DESCENDING for every score/coverage-like field (higher is better),
 * ASCENDING for `rank`/`tier` (rank 1 and tier S are already "best"). A
 * leading `-` (`sort=-<field>`) reverses that default. Clicking an
 * unsorted column header applies the field's default (best-first)
 * direction; clicking the ALREADY-active column's header toggles the `-`
 * prefix. This one function is the single source of truth for what
 * direction a given `sort` value actually produces, so the `aria-sort`
 * label rendered on the header can never disagree with the real order.
 */
export function sortDirectionLabel(sortField) {
  const field = sortField.startsWith("-") ? sortField.slice(1) : sortField;
  const reversed = sortField.startsWith("-");
  const bestFirstAscending = field === "rank" || field === "tier" || field === "first_reply"; // the fastest reply first
  const effectiveDescending = bestFirstAscending ? reversed : !reversed;
  return effectiveDescending ? "descending" : "ascending";
}

/** Human-readable column label for a `sort` field (`p_rigor` -> "Rigor", `porchlamp` -> "Score", etc.). */
export function sortColumnLabel(sortField) {
  const field = String(sortField || "").replace(/^-/, "");
  if (field.startsWith("p_")) {
    const pillar = field.slice(2);
    return copy.PILLAR_NAMES[pillar] || pillar;
  }
  const col = COLUMNS.find((c) => c.key === field);
  return col ? col.label : field;
}

export const ROW_HEIGHT_PX = 40;

const COLUMNS = [
  { key: "rank", label: copy.COLUMN_RANK, sortable: true },
  { key: "repository", label: copy.COLUMN_REPOSITORY, sortable: false },
  { key: "porchlamp", label: copy.COLUMN_PORCHLAMP, sortable: true },
  { key: "coverage", label: copy.COLUMN_COVERAGE, sortable: true },
  { key: "tier", label: copy.COLUMN_TIER, sortable: true },
  { key: "first_reply", label: copy.COLUMN_FIRST_REPLY, sortable: false },
  { key: "no_reply_14d", label: copy.paramName("gr14_ghost_rate"), sortable: false },
  { key: "quadrant", label: copy.COLUMN_QUADRANT, sortable: false },
  { key: "pillars", label: copy.COLUMN_PILLARS, sortable: false },
  { key: "delta", label: copy.COLUMN_DELTA, sortable: false, personaOnly: true },
  { key: "suspicion", label: copy.COLUMN_SUSPICION, sortable: false },
  { key: "stars", label: copy.COLUMN_STARS, sortable: false, vanity: true },
];

export function rowKey(row) {
  return `${row.owner}/${row.name}`;
}

/**
 * D-TIE-1's ordering key, restricted to the fields the API row contract
 * (06 §4) actually sends. Levels 6 (TTFHR p75) and 7 (last non-bot commit
 * date) are store-only and never reach the client, so this function can
 * only place a difference at levels 1, 2, 3, 4, 5 or 8. Returns the first
 * level at which `a` and `b` differ; two distinct rows always differ by
 * level 8 (owner/name is a natural key) if nothing earlier does.
 */
export function decidingTieLevel(a, b) {
  const round2 = (v) => (v === null || v === undefined ? v : Math.round(v * 100) / 100);
  const levels = [
    [1, round2(a.porchlamp), round2(b.porchlamp)],
    [2, a.coverage, b.coverage],
    [3, a.p_responsiveness, b.p_responsiveness],
    [4, a.friction, b.friction],
    [5, a.leverage, b.leverage],
  ];
  for (const [level, va, vb] of levels) {
    const na = va === null || va === undefined ? Number.NEGATIVE_INFINITY : va;
    const nb = vb === null || vb === undefined ? Number.NEGATIVE_INFINITY : vb;
    if (na !== nb) return level;
  }
  return 8; // owner/name is a natural key: two distinct rows always differ here if nothing else did
}

export function isTieMate(row, previousRow) {
  // `tie_group === null` means "no tie" — two singleton rows must never be
  // treated as tied just because they share the value `null`.
  return !!previousRow && row.tie_group !== null && previousRow.tie_group === row.tie_group;
}

/**
 * The no-reply column's header: "No reply in {deadline_days} days", the deadline from the report (`report.values`);
 * without a report (the dev API, an export without --stats) the parameter's own name.
 */
export function noReplyColumnLabel(report) {
  const days = report && report.values ? report.values.deadline_days : null;
  return days ? copy.COLUMN_NO_REPLY.replace("{deadline_days}", days) : copy.paramName("gr14_ghost_rate");
}

/** `labels.noReply` is the no-reply column's header (`noReplyColumnLabel`); without it, the parameter's name. */
export function renderColumnHeader(sortField, showPersonaDelta, labels = {}) {
  const cells = COLUMNS.filter((c) => !c.personaOnly || showPersonaDelta)
    .map((col) => {
      // data-col pairs a <th> with its <td> (which already carries
      // data-cell), so a column can be shown/hidden as a unit. Without it
      // only sortable columns were addressable, and hiding a body cell
      // without its header shifts every column after it.
      if (col.key === "pillars") {
        const pillarButtons = PILLAR_ORDER.map((p) => {
          const field = `p_${p}`;
          const isActive = sortField.replace(/^-/, "") === field;
          const dir = isActive ? sortDirectionLabel(sortField) : null;
          const ariaSort = dir ? ` aria-sort="${dir}"` : "";
          const arrow = dir ? `<span class="sort-arrow" aria-hidden="true">${dir === "ascending" ? "↑" : "↓"}</span>` : "";
          const shortLabel = copy.PILLAR_SHORT_NAMES[p] || p;
          const tooltip = copy.pillarSortTooltip(p);
          return (
            `<button type="button" class="pillar-sort-btn${isActive ? " is-active" : ""}" ` +
            `data-sortable="true" data-sort-field="${field}" title="${escapeHtml(tooltip)}" ` +
            `aria-label="${escapeHtml(tooltip)}"${ariaSort}>${escapeHtml(shortLabel)}${arrow}</button>`
          );
        }).join(`<span class="pillar-sort-sep" aria-hidden="true">·</span>`);
        return `<th data-col="${col.key}"><span class="pillars-col-header" aria-label="${escapeHtml(col.label)}">${pillarButtons}</span></th>`;
      }
      if (!col.sortable) {
        const tooltip = col.vanity ? ` title="${escapeHtml(copy.VANITY_TOOLTIP)}"` : "";
        const label = col.key === "no_reply_14d" && labels.noReply ? labels.noReply : col.label;
        return `<th data-col="${col.key}"${tooltip}>${escapeHtml(label)}</th>`;
      }
      const isActive = sortField.replace(/^-/, "") === col.key;
      const dir = isActive ? sortDirectionLabel(sortField) : null;
      const ariaSort = dir ? ` aria-sort="${dir}"` : "";
      const activeCls = isActive ? ` class="is-active"` : "";
      const arrow = dir ? `<span class="sort-arrow" aria-hidden="true">${dir === "ascending" ? "↑" : "↓"}</span>` : "";
      return (
        `<th data-col="${col.key}"${activeCls} data-sortable="true" data-sort-field="${col.key}" tabindex="0" role="button"` +
        `${ariaSort}>${escapeHtml(col.label)}${arrow}</th>`
      );
    })
    .join("");
  return `<tr>${cells}</tr>`;
}

/**
 * Updates `aria-sort`, `.is-active`, and `.sort-arrow` (`↑`/`↓`) in place on
 * an existing `<thead>` without replacing `thead.innerHTML` (preserving focus).
 */
export function syncHeaderSortDom(thead, sortField, doc = thead?.ownerDocument) {
  if (!thead || typeof thead.querySelectorAll !== "function") return;
  const activeField = String(sortField || "rank").replace(/^-/, "");
  const dir = sortDirectionLabel(sortField || "rank");
  for (const el of thead.querySelectorAll("[data-sort-field]")) {
    const field = el.dataset?.sortField || (typeof el.getAttribute === "function" ? el.getAttribute("data-sort-field") : null);
    const isActive = field === activeField;
    el.classList?.toggle("is-active", isActive);
    const existingArrow = typeof el.querySelector === "function" ? el.querySelector(".sort-arrow") : null;
    if (!isActive) {
      if (typeof el.hasAttribute === "function" ? el.hasAttribute("aria-sort") : true) {
        el.removeAttribute?.("aria-sort");
      }
      if (existingArrow) existingArrow.remove();
      continue;
    }
    if (el.getAttribute?.("aria-sort") !== dir) el.setAttribute?.("aria-sort", dir);
    const glyph = dir === "ascending" ? "↑" : "↓";
    if (existingArrow) {
      if (existingArrow.textContent !== glyph) existingArrow.textContent = glyph;
    } else if (doc && typeof doc.createElement === "function" && typeof el.appendChild === "function") {
      const span = doc.createElement("span");
      span.className = "sort-arrow";
      span.setAttribute("aria-hidden", "true");
      span.textContent = glyph;
      el.appendChild(span);
    }
  }
}

function pillarBarsHtml(row) {
  const pillars = PILLAR_ORDER.map((p) => ({
    id: p,
    label: copy.PILLAR_NAMES[p],
    score: row[`p_${p}`],
    coverage: row[`c_${p}`],
  }));
  return renderSparkbarGroup(pillars);
}

function deltaHtml(row) {
  if (typeof row.rank_delta !== "number") return `<span class="delta-flat">${copy.MISSING_VALUE}</span>`;
  if (row.rank_delta > 0) return `<span class="delta-up">▲${row.rank_delta}</span>`;
  if (row.rank_delta < 0) return `<span class="delta-down">▼${Math.abs(row.rank_delta)}</span>`;
  return `<span class="delta-flat">—</span>`;
}

/** A count with commas between the thousands (`1,234`), the same bytes in every locale: the export prints it too. */
export function groupThousands(n) {
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** The median first reply in short words (project.js `replyTimeText` under 48 hours: "3.9 hours", "under 0.1 hours";
 *  else days: "6.2 days"), rounded half up as the project page rounds it. */
export function replyTimeShort(hours) {
  if (hours < 48) return replyTimeText(hours);
  return copy.REPLY_TIME_WORDS.days.replace("{n}", daysText(hours));
}

/** "{n} pull requests": the sample a median comes from. */
export function replySampleText(n) {
  return copy.REPLY_SAMPLE_TEXT.replace("{n}", groupThousands(n));
}

/** The first-reply cell: the median (`—` when it is not measured), its sample as the title, and on a fastest-reply
 *  list (`top`) the sample as a second line too. */
export function firstReplyHtml(row, top = false) {
  const measured = typeof row.first_reply_hours === "number";
  const value = measured ? escapeHtml(replyTimeShort(row.first_reply_hours)) : copy.MISSING_VALUE;
  if (typeof row.first_reply_n !== "number") return value;
  const sample = escapeHtml(replySampleText(row.first_reply_n));
  return `<span title="${sample}">${value}</span>` + (top && measured ? `<span class="reply-sample">${sample}</span>` : "");
}

/** The no-reply cell: the 14-day no-reply share as a whole percent (project.js `percentWhole`), `—` when it is not
 *  measured. */
export function noReplyHtml(row) {
  return typeof row.no_reply_14d_share === "number" ? escapeHtml(percentWhole(row.no_reply_14d_share)) : copy.MISSING_VALUE;
}

/**
 * The single source of truth for a row's per-cell HTML, in column order
 * (respecting `showPersonaDelta`). Both `createRowElement` (new nodes) and
 * `updateRowElement` (cell-level patch of existing nodes) call this, so the
 * two paths cannot render different markup for the same data.
 */
export function rowCells(row, opts) {
  const { previousRow = null, showPersonaDelta = false, top = false } = opts || {};
  const tied = isTieMate(row, previousRow);
  const tieMark = tied
    ? `<span class="tie-mark" tabindex="0" title="${escapeHtml(copy.tieTooltip(decidingTieLevel(row, previousRow)))}">${copy.TIE_MARK}</span>`
    : "";

  const cells = [
    { key: "rank", html: `${row.rank}${tieMark}`, numeric: true },
    {
      key: "repository",
      html: repoLineHtml(row, showPersonaDelta),
    },
    { key: "porchlamp", html: row.porchlamp != null ? row.porchlamp.toFixed(2) : copy.MISSING_VALUE, numeric: true },
    { key: "coverage", html: renderCoverageBadge(row.coverage) },
    { key: "tier", html: renderTierCell(row.tier, row.tier_ceiling) },
    { key: "first_reply", html: firstReplyHtml(row, top), numeric: true },
    { key: "no_reply_14d", html: noReplyHtml(row), numeric: true },
    { key: "quadrant", html: renderQuadrant(row.quadrant) },
    { key: "pillars", html: pillarBarsHtml(row) },
  ];
  if (showPersonaDelta) cells.push({ key: "delta", html: deltaHtml(row), numeric: true });
  cells.push({ key: "suspicion", html: renderSuspicionIcon(row.suspicion) });
  cells.push({
    key: "stars",
    html: row.stars_audited == null ? copy.MISSING_VALUE : groupThousands(row.stars_audited),
    numeric: true,
  });
  return { cells, tied };
}

/**
 * The repository name as a real link to the project's own page (D8), so a crawler, a middle click and "open in
 * a new tab" reach it. A plain click is taken over by `handleRepoLinkClick` and still opens the drawer.
 *
 * Project pages exist only for projects ranked under the balanced lens. Under another lens (`onLens` true) a
 * row has such a page only when it carries a `balanced_rank`; any other row shows its name as plain text.
 */
export function repoLinkHtml(row, onLens = false) {
  const name =
    `<span class="repo-owner">${escapeHtml(row.owner)}/</span><span class="repo-name">${escapeHtml(row.name)}</span>`;
  const hasProjectPage = !onLens || (row.balanced_rank !== null && row.balanced_rank !== undefined);
  if (!hasProjectPage) return name;
  return `<a class="repo-link" href="${escapeHtml(projectPath(row.owner, row.name))}">${name}</a>`;
}

/**
 * The name line of a row and a card: the name (`repoLinkHtml`), which shortens with "…" when the line is too narrow,
 * and the ecosystem's chip beside it, always whole (styles/components.css `.repo-line`).
 */
export function repoLineHtml(row, onLens = false) {
  return (
    `<span class="repo-line"><span class="repo-line-name">${repoLinkHtml(row, onLens)}</span>` +
    `<span class="eco-chip">${escapeHtml(copy.ecosystemName(row.ecosystem))}</span></span>`
  );
}

/** A click that is only "go here" in this tab: the left button, no modifier key. */
export function isPlainLeftClick(evt) {
  return evt.button === 0 && !evt.ctrlKey && !evt.metaKey && !evt.shiftKey && !evt.altKey;
}

/**
 * Listens on the row or card (before the page's delegated click handler sees the event). A plain left click on
 * the repository link does not navigate: the event goes on to open the drawer, as it always did. A ctrl, cmd,
 * shift or alt click follows the link in a new tab or window, and is kept from opening the drawer too.
 */
export function handleRepoLinkClick(evt) {
  const link = evt.target && typeof evt.target.closest === "function" ? evt.target.closest("a.repo-link") : null;
  if (!link) return;
  if (isPlainLeftClick(evt)) evt.preventDefault();
  else evt.stopPropagation();
}

function rowAriaLabel(row) {
  return `Open ${escapeHtml(row.owner)}/${escapeHtml(row.name)}`;
}

/** Builds a brand-new `<tr>` DOM node (only for a key not already present). */
export function createRowElement(doc, row, opts) {
  const { selected = false } = opts || {};
  const { cells, tied } = rowCells(row, opts);
  const tr = doc.createElement("tr");
  tr.dataset.owner = row.owner;
  tr.dataset.name = row.name;
  tr.tabIndex = 0;
  tr.setAttribute("role", "button");
  tr.setAttribute("aria-label", rowAriaLabel(row));
  tr.setAttribute("title", copy.ROW_OPEN_HINT);
  tr.className = [tied ? "row-tied" : "", selected ? "row-selected" : ""].filter(Boolean).join(" ");
  tr.addEventListener?.("click", handleRepoLinkClick); // the hand-made documents of some tests have no listeners
  for (const cell of cells) {
    const td = doc.createElement("td");
    td.dataset.cell = cell.key;
    if (cell.numeric) td.classList.add("cell-numeric");
    td.innerHTML = cell.html;
    td.dataset.rendered = cell.html;
    tr.appendChild(td);
  }
  return tr;
}

/**
 * Patches an EXISTING `<tr>` in place: only a `<td>` whose computed HTML
 * actually changed gets `innerHTML` reassigned; row-level state (tie band,
 * keyboard selection) is a classList toggle, never a markup rewrite.
 */
export function updateRowElement(tr, row, opts) {
  const { selected = false } = opts || {};
  const { cells, tied } = rowCells(row, opts);
  tr.classList.toggle("row-tied", tied);
  tr.classList.toggle("row-selected", selected);
  const label = rowAriaLabel(row);
  if (tr.getAttribute("aria-label") !== label) tr.setAttribute("aria-label", label);

  const tds = tr.children;
  for (let i = 0; i < cells.length; i++) {
    const td = tds[i];
    if (!td) continue; // defensive: showPersonaDelta toggled columns, handled by full rebuild upstream
    if (td.dataset.rendered !== cells[i].html) {
      td.innerHTML = cells[i].html;
      td.dataset.rendered = cells[i].html;
    }
  }
}

/** A spacer `<tr>` that reserves scroll height for the rows above/below the rendered window. */
export function createSpacerElement(doc, key, colCount, heightPx = 0) {
  const tr = doc.createElement("tr");
  tr.dataset.key = key;
  tr.setAttribute("aria-hidden", "true");
  const td = doc.createElement("td");
  td.colSpan = colCount;
  tr.appendChild(td);
  // A created node must be fully in sync with its item. patch.js calls
  // `updateItem` only on nodes that SURVIVE a patch, never on ones it just
  // created, so a spacer built without its height got that height written on
  // the NEXT patch instead -- which is why render.perf.test.mjs's U-19
  // assertion saw three <tr>s touched by a one-row change: the changed row
  // plus both spacers receiving their first height. Measured after a warm-up
  // patch the same scenario touches exactly one <tr>, so the windowing itself
  // was always correct; this closes the deferred write.
  updateSpacerElement(tr, heightPx);
  return tr;
}

export function updateSpacerElement(tr, heightPx) {
  const next = `${heightPx}px`;
  if (tr.style.height !== next) tr.style.height = next;
  // Guarded like the write above it: an unconditional assignment counts as a
  // DOM mutation even when the value is unchanged. (This was not the cause of
  // the U-19 failure -- see createSpacerElement -- but an unguarded write in a
  // function whose whole purpose is minimal surgery is still wrong.)
  const hidden = heightPx <= 0;
  if (tr.hidden !== hidden) tr.hidden = hidden;
}

export function columnCount(showPersonaDelta) {
  return COLUMNS.filter((c) => !c.personaOnly || showPersonaDelta).length;
}

/**
 * Computes the [start, end) slice of `rows` to actually render, given the
 * scroll position — the windowing behind the 10k-row performance budget.
 */
export function computeVisibleRange(scrollTop, viewportHeight, totalRows, bufferRows = 8, itemHeight = ROW_HEIGHT_PX) {
  const firstVisible = Math.floor(scrollTop / itemHeight);
  const visibleCount = Math.ceil(viewportHeight / itemHeight);
  const start = Math.max(0, firstVisible - bufferRows);
  const end = Math.min(totalRows, firstVisible + visibleCount + bufferRows);
  return { start, end };
}

/** Estimated card height for the < 768px windowed card list (06 §5). */
export const CARD_HEIGHT_PX = 192;

// --- The < 768px card list (06 §5). `renderCard`/`renderCardList` are the
// pure string form (used by tests and any non-windowed caller);
// `createCardElement`/`updateCardElement` above are the DOM builders
// app.js patches through, on the same keyed-diff path as the desktop
// table (the keyed-diff path applies at every breakpoint). ---

function cardInnerHtml(row, opts) {
  const { previousRow = null, showPersonaDelta = false, top = false, noReplyLabel = null } = opts || {};
  const tied = isTieMate(row, previousRow);
  const tieMark = tied
    ? `<span class="tie-mark" title="${escapeHtml(copy.tieTooltip(decidingTieLevel(row, previousRow)))}">${copy.TIE_MARK}</span>`
    : "";
  const html =
    `<div class="leaderboard-card-header">` +
    `<span class="cell-numeric">#${row.rank}${tieMark}</span>` +
    `<span>${renderCoverageBadge(row.coverage)}</span>` +
    `</div>` +
    `<div>${repoLineHtml(row, showPersonaDelta)}</div>` +
    `<div class="cell-numeric">${copy.COLUMN_PORCHLAMP} ${row.porchlamp != null ? row.porchlamp.toFixed(2) : copy.MISSING_VALUE} · ${renderTierCell(row.tier, row.tier_ceiling)}</div>` +
    `<div class="leaderboard-card-replies">${escapeHtml(copy.COLUMN_FIRST_REPLY)} ` +
    `<span data-card-field="first_reply">${firstReplyHtml(row, top)}</span> · ` +
    `${escapeHtml(noReplyLabel || copy.paramName("gr14_ghost_rate"))} <span data-card-field="no_reply_14d">${noReplyHtml(row)}</span></div>` +
    `<div class="leaderboard-card-line-4">${renderQuadrant(row.quadrant)} ${pillarBarsHtml(row)}${showPersonaDelta ? ` ${deltaHtml(row)}` : ""}</div>` +
    `<div>${renderSuspicionIcon(row.suspicion)}</div>`;
  return { html, tied };
}

export function renderCard(row, opts) {
  const { html, tied } = cardInnerHtml(row, opts);
  return (
    `<div class="leaderboard-card${tied ? " row-tied" : ""}" data-owner="${escapeHtml(row.owner)}" data-name="${escapeHtml(row.name)}" tabindex="0" role="button" aria-label="Open ${escapeHtml(row.owner)}/${escapeHtml(row.name)}">` +
    html +
    `</div>`
  );
}

/** DOM builders for the < 768px card list, windowed via `patchRows` exactly
 * like the desktop table (the keyed-diff path applies at every breakpoint). */
export function createCardElement(doc, row, opts) {
  const { selected = false } = opts || {};
  const div = doc.createElement("div");
  div.dataset.owner = row.owner;
  div.dataset.name = row.name;
  div.tabIndex = 0;
  div.setAttribute("role", "button");
  div.setAttribute("aria-label", rowAriaLabel(row));
  div.setAttribute("title", copy.ROW_OPEN_HINT);
  const { html, tied } = cardInnerHtml(row, opts);
  div.className = ["leaderboard-card", tied ? "row-tied" : "", selected ? "row-selected" : ""].filter(Boolean).join(" ");
  div.addEventListener?.("click", handleRepoLinkClick);
  div.innerHTML = html;
  div.dataset.rendered = html;
  return div;
}

export function updateCardElement(div, row, opts) {
  const { selected = false } = opts || {};
  const { html, tied } = cardInnerHtml(row, opts);
  div.classList.toggle("row-tied", tied);
  div.classList.toggle("row-selected", selected);
  if (div.dataset.rendered !== html) {
    div.innerHTML = html;
    div.dataset.rendered = html;
  }
}

export function createCardSpacerElement(doc, key) {
  const div = doc.createElement("div");
  div.dataset.key = key;
  div.setAttribute("aria-hidden", "true");
  return div;
}

export function updateCardSpacerElement(div, heightPx) {
  const next = `${heightPx}px`;
  if (div.style.height !== next) div.style.height = next;
  div.hidden = heightPx <= 0;
}

export function renderCardList(rows, opts) {
  return (
    `<div class="leaderboard-cards">` +
    rows
      .map((row, i) => renderCard(row, { previousRow: i > 0 ? rows[i - 1] : null, showPersonaDelta: opts.showPersonaDelta }))
      .join("") +
    `</div>`
  );
}
