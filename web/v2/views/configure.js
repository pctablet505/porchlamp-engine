// views/configure.js — Screen D (06 §3 D): five weight sliders with lock
// toggles and proportional normalisation, the five persona PRESET buttons
// (a starting point only — U-11: the result is immediately "custom", never
// a persona), a live rank-delta side list computed from `/reproject`, and
// the re-projected leaderboard reusing `components/table.js` (the same
// table component the balanced leaderboard uses, paginated CLIENT-side
// since `ReprojectResponse` returns the whole eligible set in one call —
// see `scripts/serve_ui_fixtures.py`'s own docstring for why).

import * as copy from "../copy.js";
import { PILLAR_IDS, PERSONA_PRESET_WEIGHTS, PAGE_SIZES } from "../state.js";
import { renderColumnHeader, createRowElement, updateRowElement, rowKey, SORTABLE_COLUMNS } from "../components/table.js";
import { patchRows } from "../components/patch.js";

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

/**
 * Proportional normalisation (06 §3 D): `changedPillar` takes `newValue`
 * (clamped to [0,1]); every LOCKED pillar (and `changedPillar` itself,
 * which was just explicitly set) is held fixed; every remaining ("free")
 * pillar rescales so the five weights sum to exactly 1 — a free pillar's
 * SHARE of the remaining budget is proportional to its OWN current
 * weight (equal shares if every free pillar was already at 0). A slider
 * at 0 is allowed both as `newValue` and as a rescaled result.
 *
 * Property this function must hold for every input (exercised by
 * `web/v2/tests/unit.test.mjs`): `sum(normalizeWeights(...)) === 1` (within
 * float epsilon), every LOCKED pillar's value is byte-identical to the
 * input, and no returned value is negative.
 */
export function normalizeWeights(weights, locks, changedPillar, newValue) {
  if (locks && locks[changedPillar]) return { ...weights };
  if (!Number.isFinite(newValue)) return { ...weights };
  const clamped = Math.max(0, Math.min(1, newValue));
  const next = { ...weights, [changedPillar]: clamped };

  const fixed = new Set([changedPillar, ...PILLAR_IDS.filter((p) => locks && locks[p])]);
  const fixedSum = PILLAR_IDS.filter((p) => fixed.has(p)).reduce((s, p) => s + next[p], 0);
  const free = PILLAR_IDS.filter((p) => !fixed.has(p));

  if (free.length === 0) {
    // Every OTHER pillar is locked: the dragged one has no freedom at all
    // -- its value is fully determined by the four locked ones (whatever
    // remains of the sum-to-1 budget), never the user's raw drag value.
    // This corrects a drag in EITHER direction, so the total is always 1.
    const lockedSum = fixedSum - next[changedPillar];
    next[changedPillar] = Math.max(0, 1 - lockedSum);
    return next;
  }

  let remaining = 1 - fixedSum;
  if (remaining < 0) {
    const lockedSum = fixedSum - next[changedPillar];
    next[changedPillar] = Math.max(0, 1 - lockedSum);
    remaining = 0;
  }

  const freeCurrentSum = free.reduce((s, p) => s + weights[p], 0);
  for (const p of free) {
    next[p] = freeCurrentSum > 1e-9 ? (weights[p] / freeCurrentSum) * remaining : remaining / free.length;
  }
  return next;
}


const TIER_ORDER_BEST_FIRST = { S: 0, A: 1, B: 2, C: 3, D: 4, F: 5 };

/**
 * Client-side sort for the already-fully-loaded `/reproject` row set (no
 * fetch — every row is already in memory, unlike the leaderboard's
 * server-side sort). Mirrors `scripts/serve_ui_fixtures.py`'s "best first"
 * defaults: rank/tier ascending, every score/coverage field descending. A
 * leading "-" on `sort` reverses the field's own natural direction.
 */
export function sortReprojectRows(rows, sort) {
  const field = sort.startsWith("-") ? sort.slice(1) : sort;
  const reverseFlag = sort.startsWith("-");
  if (!SORTABLE_COLUMNS.has(field)) return rows;
  const bestFirstAscending = field === "rank" || field === "tier";
  const sorted = [...rows].sort((a, b) => {
    let av, bv;
    if (field === "tier") {
      av = TIER_ORDER_BEST_FIRST[a.tier] ?? 5;
      bv = TIER_ORDER_BEST_FIRST[b.tier] ?? 5;
    } else {
      av = a[field];
      bv = b[field];
      av = av === null || av === undefined ? (field === "rank" ? Infinity : -Infinity) : av;
      bv = bv === null || bv === undefined ? (field === "rank" ? Infinity : -Infinity) : bv;
    }
    const cmp = av < bv ? -1 : av > bv ? 1 : 0;
    return bestFirstAscending !== reverseFlag ? cmp : -cmp;
  });
  return sorted;
}

/**
 * Applies a persona PRESET (a starting point only, U-11) while respecting
 * any locked pillar: locked pillars keep their CURRENT value untouched,
 * and the preset's proportions among the free pillars are rescaled so the
 * five weights still sum to exactly 1 — the same invariant
 * `normalizeWeights` holds for a single-slider drag, generalised to a
 * preset that can change every free pillar at once. With no locks at all
 * this reduces to the preset exactly (lockedSum=0, remaining=1, and the
 * preset's own weights already sum to 1).
 */
export function applyPresetRespectingLocks(weights, locks, preset) {
  const lockedSum = PILLAR_IDS.filter((p) => locks && locks[p]).reduce((s, p) => s + weights[p], 0);
  const free = PILLAR_IDS.filter((p) => !locks || !locks[p]);
  const remaining = Math.max(0, 1 - lockedSum);
  const presetFreeSum = free.reduce((s, p) => s + preset[p], 0);
  const next = { ...weights };
  for (const p of free) {
    next[p] = presetFreeSum > 1e-9 ? (preset[p] / presetFreeSum) * remaining : remaining / free.length;
  }
  return next;
}

export function weightsSum(weights) {
  return PILLAR_IDS.reduce((s, p) => s + (weights[p] || 0), 0);
}

/** Top N movers up and down (by `balanced_rank - rank`) from a `ReprojectResponse.rows`, for the live delta side list. */
export function topMovers(rows, limit = 5) {
  const withDelta = rows.map((r) => ({ row: r, delta: r.balanced_rank - r.rank }));
  const up = withDelta.filter((d) => d.delta > 0).sort((a, b) => b.delta - a.delta).slice(0, limit);
  const down = withDelta.filter((d) => d.delta < 0).sort((a, b) => a.delta - b.delta).slice(0, limit);
  return { up, down };
}

function renderSlider(pillar, weights, locks, unlockedCount) {
  const pct = Math.round(weights[pillar] * 1000) / 10;
  const pillarLabel = escapeHtml(copy.PILLAR_NAMES[pillar]);
  const disabled = !!(locks && locks[pillar]) || (unlockedCount !== undefined && unlockedCount <= 1);
  return (
    `<div class="weight-slider-row">` +
    // Both a `<label for>` AND an explicit `aria-label` carry the pillar
    // name: the label association gives the input its computed accessible
    // name, and the attribute satisfies audits that read `aria-label`
    // directly. `aria-valuetext` carries the live percentage a screen
    // reader announces on every value change.
    `<label class="weight-slider-label" for="weight-${pillar}" id="weight-${pillar}-label">${pillarLabel}</label>` +
    `<input type="range" id="weight-${pillar}" class="weight-slider" min="0" max="1" step="any" ` +
    `value="${weights[pillar]}" data-weight-slider="${pillar}" ${disabled ? "disabled" : ""} ` +
    `aria-label="${pillarLabel}" aria-labelledby="weight-${pillar}-label" aria-valuetext="${pillarLabel}: ${pct}%">` +
    `<output class="weight-slider-value" for="weight-${pillar}">${pct}%</output>` +
    `<button type="button" class="icon-button weight-lock-toggle" data-weight-lock="${pillar}" ` +
    `aria-pressed="${!!(locks && locks[pillar])}" aria-label="${escapeHtml(copy.CONFIGURE_LOCK_LABEL)} ${pillarLabel}">` +
    `${locks && locks[pillar] ? "🔒" : "🔓"}</button>` +
    `</div>`
  );
}

function renderPresets() {
  const buttons = Object.keys(PERSONA_PRESET_WEIGHTS)
    .map(
      (persona) =>
        `<button type="button" class="icon-button" data-weight-preset="${persona}">${escapeHtml(copy.PERSONA_NAMES[persona])}</button>`
    )
    .join("");
  return `<div class="weight-presets"><span class="field-label">${escapeHtml(copy.CONFIGURE_PRESETS_LABEL)}</span>${buttons}</div>`;
}

export function renderControls(state) {
  const sum = weightsSum(state.weights);
  const unlockedCount = PILLAR_IDS.filter((p) => !state.weightLocks[p]).length;
  const sliders = PILLAR_IDS.map((p) => renderSlider(p, state.weights, state.weightLocks, unlockedCount)).join("");
  const sumOffBy1 = Math.abs(sum - 1) > 0.005;
  return (
    `<div class="weight-controls">` +
    `<div class="weight-sliders">${sliders}</div>` +
    renderPresets() +
    `<p class="weight-sum${sumOffBy1 ? " weight-sum-warning" : ""}" data-weight-sum="true">${escapeHtml(copy.configureSumText(sum))}</p>` +
    `<p class="custom-label-note">${escapeHtml(copy.CONFIGURE_CUSTOM_LABEL)}</p>` +
    `</div>`
  );
}

function renderDeltaEntry(entry) {
  return `<li>${escapeHtml(copy.configureDeltaText(entry.row))}</li>`;
}

export function renderDeltaList(reproject) {
  const heading = `<h2>${escapeHtml(copy.CONFIGURE_DELTA_HEADING)}</h2>`;
  if (!reproject) return heading + `<p class="empty-state">${escapeHtml(copy.CONFIGURE_DELTA_EMPTY)}</p>`;
  const { up, down } = topMovers(reproject.rows);
  if (up.length === 0 && down.length === 0) return heading + `<p class="empty-state">${escapeHtml(copy.CONFIGURE_DELTA_EMPTY)}</p>`;
  return (
    heading +
    `<div class="delta-list" data-delta-list="true">` +
    `<h3>${escapeHtml(copy.CONFIGURE_TOP_MOVERS_UP)}</h3><ul>${up.map(renderDeltaEntry).join("") || "<li>—</li>"}</ul>` +
    `<h3>${escapeHtml(copy.CONFIGURE_TOP_MOVERS_DOWN)}</h3><ul>${down.map(renderDeltaEntry).join("") || "<li>—</li>"}</ul>` +
    `</div>`
  );
}

export function renderPager(page, pageSize, total) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const sizeOptions = PAGE_SIZES.map((n) => `<option value="${n}"${n === pageSize ? " selected" : ""}>${n}</option>`).join("");
  return (
    `<div class="pager">` +
    `<label>${escapeHtml(copy.PAGE_SIZE_LABEL)} <select class="control" data-configure-pager-size-select="true">${sizeOptions}</select></label>` +
    `<button type="button" data-configure-pager-prev="true"${page <= 1 ? " disabled" : ""}>&larr;</button>` +
    `<span>${escapeHtml(copy.pagerStatusText(page, totalPages, total))}</span>` +
    `<button type="button" data-configure-pager-next="true"${page >= totalPages ? " disabled" : ""}>&rarr;</button>` +
    `</div>`
  );
}

export function renderTableShell() {
  return (
    `<table class="leaderboard-table" data-configure-table="true">` +
    `<thead><tr data-configure-thead="true"></tr></thead>` +
    `<tbody data-configure-tbody="true"></tbody>` +
    `</table>`
  );
}

export function patchConfigureTable(main, doc, state) {
  const rows = state.reproject ? sortReprojectRows(state.reproject.rows, state.configureSort || "rank") : [];
  const totalPages = Math.max(1, Math.ceil(rows.length / state.configurePageSize));
  const page = Math.min(state.configurePage, totalPages);
  const start = (page - 1) * state.configurePageSize;
  const pageRows = rows.slice(start, start + state.configurePageSize);

  const thead = main.querySelector("[data-configure-thead]");
  if (thead) thead.innerHTML = renderColumnHeader(state.configureSort || "rank", false);

  // Unlike the leaderboard's leaderboard.js, this table is never
  // windowed within a page: `/reproject` returns the WHOLE eligible set
  // in one call (no server-side paging fields at all — see
  // scripts/serve_ui_fixtures.py's own docstring), so pagination here is
  // purely a client-side slice of an already-small in-memory array; a
  // full page (up to 1000 rows, the same ceiling the leaderboard allows)
  // patches through `patchRows` in one pass without a separate windowing
  // layer.
  const tbody = main.querySelector("[data-configure-tbody]");
  if (tbody) {
    const previousRowByKey = new Map();
    for (let i = 1; i < pageRows.length; i++) previousRowByKey.set(rowKey(pageRows[i]), pageRows[i - 1]);
    patchRows(
      tbody,
      pageRows,
      rowKey,
      (row) => createRowElement(doc, row, { previousRow: previousRowByKey.get(rowKey(row)) || null, showPersonaDelta: false }),
      (node, row) => updateRowElement(node, row, { previousRow: previousRowByKey.get(rowKey(row)) || null, showPersonaDelta: false })
    );
  }

  const pagerHost = main.querySelector("[data-configure-pager-host]");
  if (pagerHost) pagerHost.innerHTML = renderPager(page, state.configurePageSize, rows.length);

  const deltaHost = main.querySelector("[data-configure-delta-host]");
  if (deltaHost) deltaHost.innerHTML = renderDeltaList(state.reproject);

  const table = main.querySelector("[data-configure-table]");
  if (table) table.classList.toggle("stale-table", !!state.reprojectRefetching);
  const progress = main.querySelector("[data-configure-progress]");
  if (progress) progress.hidden = !state.reprojectRefetching;
}

export function shellShape(state) {
  if (state.reprojectLoading && !state.reproject) return "loading";
  if (state.reprojectError && !state.reproject) return "error";
  if (state.reproject && state.reproject.rows.length === 0) return "empty";
  return "body";
}

export function renderShell(state) {
  const shape = shellShape(state);
  const heading = `<h1 class="view-heading">${escapeHtml(copy.CONFIGURE_HEADING)}</h1>`;
  const controls = renderControls(state);
  if (shape === "loading") {
    return heading + controls + `<div class="empty-state" aria-busy="true">${escapeHtml(copy.CONFIGURE_LOADING_TEXT)}</div>`;
  }
  if (shape === "error") {
    return (
      heading +
      controls +
      `<div class="error-banner" role="alert">${escapeHtml(copy.configureErrorText(state.reprojectError.endpoint, state.reprojectError.status, state.reprojectError.detail))}` +
      ` <button type="button" class="icon-button" data-configure-retry="true">${escapeHtml(copy.CONFIGURE_RETRY_LABEL)}</button></div>`
    );
  }
  if (shape === "empty") {
    return heading + controls + `<div class="empty-state">${escapeHtml(copy.CONFIGURE_EMPTY_TEXT)}</div>`;
  }
  return (
    heading +
    controls +
    `<div class="refetch-progress" data-configure-progress="true" hidden></div>` +
    `<div class="configure-layout">` +
    `<div class="configure-table-col">${renderTableShell()}<div data-configure-pager-host="true"></div></div>` +
    `<aside class="configure-delta-col" data-configure-delta-host="true"></aside>` +
    `</div>`
  );
}
