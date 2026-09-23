// views/compare.js — Screen E (06 §3 E): 2-4 repositories side by side.
// Direction per parameter ("the better value marked") is read from the
// LOADED methodology document's `parameters[].value_domain`, never
// hardcoded here (06 §3 E: "direction per parameter from the methodology
// document"). A verdict is generated ONLY from parameters measured on
// EVERY compared repository, and names the ones that were not.

import * as copy from "../copy.js";
import { renderCoverageBadge, renderTierCell } from "../components/badge.js";
import { COMPARE_MIN, COMPARE_MAX } from "../state.js";

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

/**
 * A parameter's comparison direction, read from its methodology entry's
 * `value_domain` — never a per-parameter hardcoded rule:
 * - numeric/ecdf: `"higher"` or `"lower"` from `higher_is_better`.
 * - enum: `{ kind: "enum-order", order }`, `order` = `persona_orders[persona]`
 *   (falling back to `persona_orders.balanced`, then the domain's own
 *   `values_worst_to_best` when neither persona-conditional list exists) —
 *   worst-to-best, so a HIGHER index is better.
 * - anything else (an unrecognised domain shape): `null` — no direction
 *   claim is made, and the parameter is excluded from the "better" mark
 *   (never a silent, wrong guess).
 */
export function parameterDirection(paramEntry, persona) {
  const domain = paramEntry.value_domain || {};
  if (domain.kind === "numeric" || domain.kind === "ecdf") {
    return domain.higher_is_better ? "higher" : "lower";
  }
  if (domain.kind === "enum") {
    if (domain.persona_orders) {
      const order = domain.persona_orders[persona] || domain.persona_orders.balanced || Object.values(domain.persona_orders)[0];
      return { kind: "enum-order", order: order || [] };
    }
    if (domain.values_worst_to_best) return { kind: "enum-order", order: domain.values_worst_to_best };
  }
  return null;
}

/**
 * The single repo key with the "better" raw value for one parameter, or
 * `null` when fewer than 2 repos have a measured value, the direction is
 * unknown, or the best value is itself tied between >= 2 repos (no single
 * winner — never break a tie arbitrarily).
 */
export function betterRepoKey(paramEntry, valuesByRepoKey, persona) {
  const direction = parameterDirection(paramEntry, persona);
  let measured = Object.entries(valuesByRepoKey).filter(([, v]) => v !== null && v !== undefined);
  if (measured.length < 2 || !direction) return null;

  let rank;
  if (direction === "higher") rank = (v) => v;
  else if (direction === "lower") rank = (v) => -v;
  else if (direction.kind === "enum-order") {
    rank = (v) => direction.order.indexOf(v);
    // A value absent from the enum's own worst-to-best order (`indexOf`
    // returns -1) is unknown, not "worse than the worst" — ranking it as
    // -1 would silently mark every other repo's real (known) value as
    // "better" purely because this one could not be placed on the scale.
    // Treated the same as an unmeasured value: excluded from comparison.
    measured = measured.filter(([, v]) => rank(v) !== -1);
    if (measured.length < 2) return null;
  } else return null;

  measured.sort((a, b) => rank(b[1]) - rank(a[1]));
  const bestRank = rank(measured[0][1]);
  const tiedForBest = measured.filter(([, v]) => rank(v) === bestRank);
  return tiedForBest.length === 1 ? tiedForBest[0][0] : null;
}

/** `{ owner/name -> rawValue|null }` for one `param_id`, across the compared repos' evidence. */
export function valuesForParam(paramId, evidenceByRepo) {
  const out = {};
  for (const [key, evidence] of Object.entries(evidenceByRepo)) {
    const row = (evidence || []).find((e) => e.param_id === paramId);
    out[key] = row && row.measured ? row.value : null;
  }
  return out;
}

/**
 * The verdict line (06 §3 E: "generated ONLY from parameters measured on
 * all compared repos and naming the unmeasured ones"). Ties a "win" to
 * whichever repo has the "better" mark on the most such parameters;
 * ties in win count report as tied, never an arbitrary pick.
 */
export function computeVerdict(methodologyParams, evidenceByRepo, repoKeys, persona) {
  const measuredEverywhere = [];
  const notMeasuredEverywhere = [];
  const wins = Object.fromEntries(repoKeys.map((k) => [k, 0]));

  for (const paramEntry of methodologyParams) {
    const values = valuesForParam(paramEntry.input_id, evidenceByRepo);
    const measuredOnAll = repoKeys.every((k) => values[k] !== null && values[k] !== undefined);
    if (!measuredOnAll) {
      notMeasuredEverywhere.push(paramEntry.input_id);
      continue;
    }
    measuredEverywhere.push(paramEntry.input_id);
    const winner = betterRepoKey(paramEntry, values, persona);
    if (winner) wins[winner] += 1;
  }

  const maxWins = Math.max(0, ...Object.values(wins));
  const leaders = Object.entries(wins).filter(([, w]) => w === maxWins && maxWins > 0).map(([k]) => k);

  return {
    measuredCount: measuredEverywhere.length,
    totalParams: methodologyParams.length,
    notMeasuredEverywhere,
    text:
      measuredEverywhere.length === 0
        ? copy.compareVerdictNoDataText()
        : leaders.length === 1
          ? copy.compareVerdictText(leaders[0], measuredEverywhere.length, methodologyParams.length)
          : copy.compareVerdictTieText(measuredEverywhere.length, methodologyParams.length),
  };
}

function renderChips(compareRepos) {
  const chips = compareRepos
    .map(
      (key) =>
        `<span class="compare-chip" data-key="${escapeHtml(key)}">${escapeHtml(key)}` +
        `<button type="button" data-compare-remove="${escapeHtml(key)}" aria-label="${escapeHtml(copy.COMPARE_REMOVE_LABEL)}: ${escapeHtml(key)}">×</button></span>`
    )
    .join("");
  return `<div class="compare-chips" data-compare-chips="true">${chips}</div>`;
}

/**
 * The query-dependent PART of the picker only — the results `<ul>` and/or
 * the "max reached" caption. Deliberately excludes the `<input>` itself,
 * so this can be patched into `[data-compare-results-host]` (a SIBLING of
 * the input, never an ancestor) on every keystroke without the input
 * ever being touched. See `patchResults` / `shellKey` below and 06 §6.1 (U-12).
 */
function renderPickerResults(state) {
  const atMax = state.compareRepos.length >= COMPARE_MAX;
  const results = state.compareSearchResults
    .map((r) => {
      const key = `${r.owner}/${r.name}`;
      return (
        `<li><button type="button" data-compare-add="${escapeHtml(key)}" aria-label="${escapeHtml(copy.COMPARE_ADD_LABEL)}: ${escapeHtml(key)}">` +
        `${escapeHtml(key)} <span class="eco-chip">${escapeHtml(r.ecosystem)}</span></button></li>`
      );
    })
    .join("");
  return (
    (atMax ? `<p class="matrix-caption">${escapeHtml(copy.COMPARE_MAX_TEXT)}</p>` : "") +
    (results ? `<ul class="compare-results" data-compare-results="true">${results}</ul>` : "")
  );
}

/**
 * The STATIC part of the picker: the search `<input>` itself plus an
 * empty results-host container. Rendered only as part of the shell (see
 * `shellKey`/`renderShell`) — NEVER re-rendered while the input has focus:
 * `value="${state.compareQuery}"` is read from state only at shell-CREATION
 * time (tab entry or a chip added/removed, both of which reset or
 * intentionally replace the query — see `state.js::COMPARE_ADD_REPO`),
 * never on every keystroke. The input owns its own value/caret thereafter;
 * state follows the input via the `COMPARE_SET_QUERY` dispatch on its own
 * `input` event, not the reverse.
 */
function renderPickerShell(state) {
  const atMax = state.compareRepos.length >= COMPARE_MAX;
  return (
    `<div class="compare-picker">` +
    `<input type="search" class="control" data-compare-search="true" value="${escapeHtml(state.compareQuery)}" ` +
    `placeholder="${escapeHtml(copy.COMPARE_SEARCH_PLACEHOLDER)}" aria-label="${escapeHtml(copy.COMPARE_SEARCH_LABEL)}" ` +
    `${atMax ? "disabled" : ""}>` +
    `<div data-compare-results-host="true">${renderPickerResults(state)}</div>` +
    `</div>`
  );
}

/** Patches ONLY the results/suggestions list — the keystroke path. Never
 * touches `main.innerHTML` or any ancestor of `[data-compare-search]`. */
export function patchResults(main, state) {
  const host = main.querySelector("[data-compare-results-host]");
  if (host) host.innerHTML = renderPickerResults(state);
}

/** Patches ONLY the comparison table host — a per-repo-data-change path,
 * also never touching the picker/input. No-op when the shell shape isn't
 * "table" (the host doesn't exist in the empty/partial-min shapes). */
export function patchTable(main, state) {
  const host = main.querySelector("[data-compare-table-host]");
  if (host) host.innerHTML = renderTable(state);
}

function repoIdentityCell(key, data) {
  if (!data || data.loading) return `<td>${escapeHtml(copy.COMPARE_LOADING_TEXT)}</td>`;
  if (data.error) {
    return `<td class="error-banner" role="alert">${escapeHtml(copy.compareErrorText(data.error.endpoint, data.error.status, data.error.detail))}</td>`;
  }
  return `<td><span class="repo-name">${escapeHtml(key)}</span></td>`;
}

function gateStatusCell(data) {
  if (!data || data.loading || data.error) return `<td>${copy.MISSING_VALUE}</td>`;
  if (data.kind === "gated") return `<td>${escapeHtml(copy.gateReasonText(data.row.gate_reason))}</td>`;
  if (data.kind === "unrated") return `<td>${escapeHtml(copy.unratedReasonText(data.row.unrated_reason, data.row.coverage))}</td>`;
  return `<td>${escapeHtml(copy.COMPARE_RATED_STATUS)}</td>`;
}

function compositeCell(data) {
  if (!data || data.loading || data.error || data.kind !== "rated" || !data.row) return `<td>${copy.MISSING_VALUE}</td>`;
  const porchlampVal = data.row.porchlamp != null ? data.row.porchlamp.toFixed(2) : copy.MISSING_VALUE;
  return `<td class="cell-numeric">${porchlampVal} · ${renderTierCell(data.row.tier, data.row.tier_ceiling)}</td>`;
}

function coverageCell(data) {
  if (!data || data.loading || data.error) return `<td>${copy.MISSING_VALUE}</td>`;
  return `<td>${renderCoverageBadge(data.row ? data.row.coverage : null)}</td>`;
}

function pillarScoreCell(data, pillar) {
  if (!data || data.loading || data.error || !data.row) return `<td>${copy.MISSING_VALUE}</td>`;
  const score = data.row[`p_${pillar}`];
  if (score == null) return `<td class="cell-missing">${copy.MISSING_VALUE}</td>`;
  return `<td class="cell-numeric">${score.toFixed(1)}</td>`;
}

function parameterRow(paramEntry, repoKeys, evidenceByRepo, persona) {
  const values = valuesForParam(paramEntry.input_id, evidenceByRepo);
  const winner = betterRepoKey(paramEntry, values, persona);
  const paramLabel = escapeHtml(copy.paramName(paramEntry.input_id));
  const cells = repoKeys
    .map((key) => {
      const raw = values[key];
      const evidenceRows = evidenceByRepo[key] || [];
      const evidenceRow = evidenceRows.find((e) => e.param_id === paramEntry.input_id);
      if (raw === null || raw === undefined) {
        const reason = evidenceRow ? copy.missingValueReason(evidenceRow.unmeasured_reason) : copy.missingValueReason();
        return `<td class="cell-missing" tabindex="0" title="${escapeHtml(reason)}"><span class="unmeasured-badge">${escapeHtml(copy.COMPARE_UNMEASURED_LABEL)}</span></td>`;
      }
      const isBetter = winner === key;
      const title = evidenceRow
        ? copy.compareEvidenceSourceTitle(evidenceRow.source, evidenceRow.fetched_at)
        : "";
      const mark = isBetter
        ? `<span class="compare-better-mark" title="${escapeHtml(copy.compareBetterTitle(paramEntry.input_id))}" aria-label="${escapeHtml(copy.compareBetterTitle(paramEntry.input_id))}">${copy.COMPARE_BETTER_MARK}</span>`
        : "";
      return `<td class="cell-numeric${isBetter ? " compare-better-cell" : ""}" tabindex="0" title="${escapeHtml(title)}">${escapeHtml(String(raw))} ${mark}</td>`;
    })
    .join("");
  return `<tr><th scope="row" title="${escapeHtml(paramEntry.input_id)}">${paramLabel}</th>${cells}</tr>`;
}

export function renderTable(state) {
  const repoKeys = state.compareRepos;
  const dataByRepo = state.compareDataByRepo;
  const evidenceByRepo = Object.fromEntries(repoKeys.map((k) => [k, (dataByRepo[k] || {}).evidence || []]));
  const headerCells = repoKeys.map((k) => `<th scope="col">${escapeHtml(k)}</th>`).join("");

  const methodologyParams = state.methodology ? state.methodology.parameters : [];
  const paramRows = methodologyParams.map((p) => parameterRow(p, repoKeys, evidenceByRepo, state.persona)).join("");

  const pillarRows = Object.keys(copy.PILLAR_NAMES)
    .map((pillar) => `<tr><th scope="row">${escapeHtml(copy.PILLAR_NAMES[pillar])}</th>${repoKeys.map((k) => pillarScoreCell(dataByRepo[k], pillar)).join("")}</tr>`)
    .join("");

  const allLoaded = repoKeys.every((k) => dataByRepo[k] && !dataByRepo[k].loading && !dataByRepo[k].error);
  let verdictHtml = "";
  if (allLoaded && state.methodology && repoKeys.length >= COMPARE_MIN) {
    const verdict = computeVerdict(methodologyParams, evidenceByRepo, repoKeys, state.persona);
    const unmeasuredNote = copy.compareUnmeasuredNote(verdict.notMeasuredEverywhere);
    verdictHtml =
      `<div class="compare-verdict"><h2>${escapeHtml(copy.COMPARE_VERDICT_HEADING)}</h2>` +
      `<p>${escapeHtml(verdict.text)}</p>` +
      (unmeasuredNote ? `<p class="matrix-caption">${escapeHtml(unmeasuredNote)}</p>` : "") +
      `</div>`;
  }

  return (
    `<div class="table-scroll"><table class="compare-table">` +
    `<thead><tr><th scope="col">${escapeHtml(copy.COMPARE_ROW_IDENTITY)}</th>${headerCells}</tr></thead>` +
    `<tbody>` +
    `<tr><th scope="row">${escapeHtml(copy.COMPARE_ROW_GATE_STATUS)}</th>${repoKeys.map((k) => gateStatusCell(dataByRepo[k])).join("")}</tr>` +
    `<tr><th scope="row">${escapeHtml(copy.COMPARE_ROW_COMPOSITE)}</th>${repoKeys.map((k) => compositeCell(dataByRepo[k])).join("")}</tr>` +
    `<tr><th scope="row">${escapeHtml(copy.COMPARE_ROW_COVERAGE)}</th>${repoKeys.map((k) => coverageCell(dataByRepo[k])).join("")}</tr>` +
    pillarRows +
    paramRows +
    `</tbody></table></div>` +
    verdictHtml
  );
}

export function shellShape(state) {
  if (state.compareRepos.length === 0) return "empty";
  if (state.compareRepos.length < COMPARE_MIN) return "partial-min";
  return "table";
}

/**
 * The shell-rebuild decision key — deliberately depends on ONLY
 * `shellShape` (which is itself a pure function of `compareRepos.length`)
 * and `compareRepos` itself (order/membership, for the chip row). It must
 * NEVER depend on `compareQuery` or `compareSearchResults` (a keystroke)
 * or on `compareDataByRepo` (a per-repo fetch landing) — both of those are
 * patched into their own host containers (`patchResults`/`patchTable`)
 * without ever touching `main.innerHTML`, so a change to either must never
 * be reflected in this key or the focused search input would be destroyed
 * and recreated on every keystroke. This is the "what to patch" decision:
 * unchanged key on a keystroke -> patch results only; changed key (a chip
 * added/removed, or entering the tab) -> rebuild the shell.
 */
export function shellKey(state) {
  return `${shellShape(state)}:${state.compareRepos.join(",")}`;
}

export function renderShell(state) {
  const heading = `<h1 class="view-heading">${escapeHtml(copy.COMPARE_HEADING)}</h1>`;
  const chips = renderChips(state.compareRepos);
  const picker = renderPickerShell(state);
  const shape = shellShape(state);
  if (shape === "empty") {
    return heading + chips + picker + `<div class="empty-state">${escapeHtml(copy.COMPARE_EMPTY_TEXT)}</div>`;
  }
  if (shape === "partial-min") {
    return heading + chips + picker + `<div class="empty-state">${escapeHtml(copy.COMPARE_MIN_TEXT)}</div>`;
  }
  // Empty host: content is patched in immediately after by `patchTable`
  // (called unconditionally right after every shell (re)build, in
  // addition to every subsequent state change) — never embedded inline
  // here, so a table-only data change (a per-repo fetch landing) never
  // needs to touch this function or the picker/input above it.
  return heading + chips + picker + `<div data-compare-table-host="true"></div>`;
}
