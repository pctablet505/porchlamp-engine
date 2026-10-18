// views/compare.js — Screen E (06 §3 E): 2-4 repositories side by side.
// Direction per parameter ("the better value marked") is read from the
// LOADED methodology document's `parameters[].value_domain`, never
// hardcoded here (06 §3 E: "direction per parameter from the methodology
// document"). A verdict is generated ONLY from parameters measured on
// EVERY compared repository, and names the ones that were not.

import * as copy from "../copy.js";
import { renderCoverageBadge, renderTierCell } from "../components/badge.js";
import { COMPARE_MIN, COMPARE_MAX } from "../state.js";
import { classifyKindForLens, projectRowForLens, deriveTierCeiling } from "./drawer.js";

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
export function valuesForParam(paramId, evidenceByRepo, repoKeys = null) {
  const out = {};
  const keys = Array.isArray(repoKeys) ? repoKeys : Object.keys(evidenceByRepo);
  for (const key of keys) {
    const evidence = evidenceByRepo[key];
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
    const values = valuesForParam(paramEntry.input_id, evidenceByRepo, repoKeys);
    const measuredOnAll = repoKeys.every((k) => values[k] !== null && values[k] !== undefined);
    if (!measuredOnAll) {
      notMeasuredEverywhere.push(paramEntry.input_id);
      continue;
    }
    measuredEverywhere.push(paramEntry.input_id);
    const winner = betterRepoKey(paramEntry, values, persona);
    if (winner && Object.prototype.hasOwnProperty.call(wins, winner)) wins[winner] += 1;
  }

  const maxWins = Math.max(0, ...Object.values(wins));
  const leaders = Object.entries(wins).filter(([, w]) => w === maxWins && maxWins > 0).map(([k]) => k);

  // When at least one compared repository has NO measured parameter (e.g. a
  // gated one), `measuredEverywhere` is empty. If >= 2 compared repositories
  // DO have measured parameters, compute the verdict across that subset
  // instead -- still only from parameters measured on every repository in it
  // (06 §3 E) -- and name the repositories it leaves out
  // (`excludedRepoKeys`) so the line never reads as a verdict on them.
  let subsetText = "";
  let effectiveWins = wins;
  let effectiveMeasuredCount = measuredEverywhere.length;
  let excludedRepoKeys = [];
  let subsetNotMeasured = [];
  const activeRepoKeys = repoKeys.filter((k) =>
    methodologyParams.some((p) => {
      const v = valuesForParam(p.input_id, evidenceByRepo, [k])[k];
      return v !== null && v !== undefined;
    })
  );
  if (measuredEverywhere.length === 0 && activeRepoKeys.length >= 2) {
    const subsetWins = Object.fromEntries(repoKeys.map((k) => [k, null]));
    for (const k of activeRepoKeys) subsetWins[k] = 0;
    let subsetMeasuredCount = 0;
    const subsetSkipped = [];
    for (const paramEntry of methodologyParams) {
      const subValues = valuesForParam(paramEntry.input_id, evidenceByRepo, activeRepoKeys);
      const onAllSub = activeRepoKeys.every((k) => subValues[k] !== null && subValues[k] !== undefined);
      if (!onAllSub) {
        subsetSkipped.push(paramEntry.input_id);
        continue;
      }
      subsetMeasuredCount += 1;
      const w = betterRepoKey(paramEntry, subValues, persona);
      if (w && subsetWins[w] !== null && subsetWins[w] !== undefined) subsetWins[w] += 1;
    }
    if (subsetMeasuredCount > 0) {
      const maxSub = Math.max(0, ...activeRepoKeys.map((k) => subsetWins[k] || 0));
      const subLeaders = activeRepoKeys.filter((k) => subsetWins[k] === maxSub && maxSub > 0);
      subsetText =
        subLeaders.length === 1
          ? copy.compareSubsetVerdictText(subLeaders[0], subsetMeasuredCount, methodologyParams.length, activeRepoKeys.length)
          : copy.compareSubsetTieText(subsetMeasuredCount, methodologyParams.length, activeRepoKeys.length);
      effectiveWins = subsetWins;
      effectiveMeasuredCount = subsetMeasuredCount;
      excludedRepoKeys = repoKeys.filter((k) => !activeRepoKeys.includes(k));
      subsetNotMeasured = subsetSkipped;
    }
  }

  return {
    measuredCount: measuredEverywhere.length,
    totalParams: methodologyParams.length,
    notMeasuredEverywhere,
    wins,
    effectiveWins,
    effectiveMeasuredCount,
    subsetText,
    excludedRepoKeys,
    subsetNotMeasured,
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
  const clearBtn =
    compareRepos.length > 0
      ? `<button type="button" class="link-button compare-clear-btn" data-compare-clear="true">${escapeHtml(copy.COMPARE_CLEAR_ALL_LABEL)}</button>`
      : "";
  return `<div class="compare-chips" data-compare-chips="true">${chips}${clearBtn}</div>`;
}

function renderCompareOnboarding(compareRepos) {
  const presets = copy.COMPARE_PRESETS.map(
    (preset) =>
      `<button type="button" class="compare-preset-card" data-compare-preset="${escapeHtml(preset.repos.join(","))}">` +
      `<span class="compare-preset-title">${escapeHtml(preset.label)}</span>` +
      `<span class="compare-preset-desc">${escapeHtml(preset.description)}</span>` +
      `<span class="compare-preset-repos">${preset.repos.map((r) => `<code class="spec-chip">${escapeHtml(r)}</code>`).join("")}</span>` +
      `</button>`
  ).join("");

  const availableSuggestions = copy.COMPARE_SUGGESTIONS.filter((r) => !compareRepos.includes(r));
  const suggestions = availableSuggestions
    .map(
      (repo) =>
        `<button type="button" class="compare-quick-chip" data-compare-quick-add="${escapeHtml(repo)}">` +
        `<span aria-hidden="true">+</span> ${escapeHtml(repo)}</button>`
    )
    .join("");

  return (
    `<section class="compare-onboarding" aria-label="${escapeHtml(copy.COMPARE_PRESETS_HEADING)}">` +
    `<div class="compare-presets-block">` +
    `<h2 class="compare-onboarding-title">${escapeHtml(copy.COMPARE_PRESETS_HEADING)}</h2>` +
    `<p class="compare-onboarding-sub">${escapeHtml(copy.COMPARE_PRESETS_SUBTITLE)}</p>` +
    `<div class="compare-presets-grid">${presets}</div>` +
    `</div>` +
    (suggestions
      ? `<div class="compare-suggestions-block">` +
        `<h3 class="compare-suggestions-title">${escapeHtml(copy.COMPARE_SUGGESTIONS_HEADING)}</h3>` +
        `<div class="compare-suggestions-row">${suggestions}</div>` +
        `</div>`
      : "") +
    `</section>`
  );
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
        `${escapeHtml(key)} <span class="eco-chip">${escapeHtml(copy.ecosystemName(r.ecosystem))}</span></button></li>`
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
    return (
      `<td class="error-banner" role="alert">${escapeHtml(copy.compareErrorText(data.error.endpoint, data.error.status, data.error.detail))}` +
      ` <button type="button" class="icon-button" data-compare-retry="${escapeHtml(key)}">${escapeHtml(copy.RETRY_LABEL)}</button></td>`
    );
  }
  const [owner, name] = key.split("/");
  return (
    `<td><button type="button" class="link-button repo-name" ` +
    `data-owner="${escapeHtml(owner)}" data-name="${escapeHtml(name)}">${escapeHtml(key)}</button></td>`
  );
}

function resolveCompareLens(data, persona) {
  if (!data || !data.row) return { kind: data ? data.kind : null, row: null };
  const hasExplicitFlags = typeof data.row.eligible === "boolean" || typeof data.row.rated === "boolean";
  let kind = data.kind;
  if (hasExplicitFlags) {
    kind = classifyKindForLens(data.row, persona) || data.kind;
  } else if (persona && persona !== "balanced" && data.row.persona_gates && data.row.persona_gates[persona]) {
    kind = "persona-gated";
  }
  const row = projectRowForLens(data.row, persona);
  return { kind, row };
}

function gateStatusCell(data, persona = "balanced", floor = null) {
  if (!data || data.loading || data.error) return `<td>${copy.MISSING_VALUE}</td>`;
  const { kind, row } = resolveCompareLens(data, persona);
  if (kind === "gated") return `<td><span class="compare-status-pill is-gated">${escapeHtml(copy.gateReasonText(row.gate_reason))}</span></td>`;
  if (kind === "persona-gated") {
    const rawReason = row.persona_gates && persona ? row.persona_gates[persona] : "";
    return `<td><span class="compare-status-pill is-gated">${escapeHtml(copy.personaGateReason(rawReason))}</span></td>`;
  }
  if (kind === "unrated") return `<td><span class="compare-status-pill is-unrated">${escapeHtml(copy.unratedReasonText(row.unrated_reason || row.gate_reason, row.coverage, floor))}</span></td>`;
  return `<td><span class="compare-status-pill is-rated">${escapeHtml(copy.COMPARE_RATED_STATUS)}</span></td>`;
}

function compositeCell(data, persona = "balanced") {
  if (!data || data.loading || data.error || !data.row) return `<td>${copy.MISSING_VALUE}</td>`;
  const { kind, row } = resolveCompareLens(data, persona);
  if (kind !== "rated" || !row) return `<td>${copy.MISSING_VALUE}</td>`;
  const porchlampVal = row.porchlamp != null ? row.porchlamp.toFixed(2) : copy.MISSING_VALUE;
  const ceiling = deriveTierCeiling(row.coverage, row.tier_ceiling);
  return `<td class="cell-numeric">${porchlampVal} · ${renderTierCell(row.tier, ceiling)}</td>`;
}

function coverageCell(data, persona = "balanced") {
  if (!data || data.loading || data.error) return `<td>${copy.MISSING_VALUE}</td>`;
  const { row } = resolveCompareLens(data, persona);
  return `<td>${renderCoverageBadge(row ? row.coverage : null)}</td>`;
}

function pillarScoreCell(data, pillar, isBest = false, pillarLabel = "") {
  if (!data || data.loading || data.error || !data.row) return `<td>${copy.MISSING_VALUE}</td>`;
  const score = data.row[`p_${pillar}`];
  if (score == null) return `<td class="cell-missing">${copy.MISSING_VALUE}</td>`;
  const pct = Math.max(0, Math.min(100, Math.round(score)));
  const betterTitle = copy.compareBetterTitle(pillarLabel || pillar);
  const mark = isBest
    ? ` <span class="compare-better-mark" title="${escapeHtml(betterTitle)}" aria-label="${escapeHtml(betterTitle)}">${copy.COMPARE_BETTER_MARK}</span>`
    : "";
  return (
    `<td class="cell-numeric${isBest ? " compare-better-cell" : ""}">` +
    `<div class="compare-cell-metric">` +
    `<span class="compare-cell-val">${score.toFixed(1)}${mark}</span>` +
    `<span class="compare-inline-bar" aria-hidden="true"><span class="compare-inline-bar-fill" style="width:${pct}%"></span></span>` +
    `</div></td>`
  );
}

function directionBadgeHtml(direction) {
  if (direction === "higher") {
    return `<span class="compare-dir-badge">${escapeHtml(copy.COMPARE_DIRECTION_HIGHER)}</span>`;
  }
  if (direction === "lower") {
    return `<span class="compare-dir-badge">${escapeHtml(copy.COMPARE_DIRECTION_LOWER)}</span>`;
  }
  if (direction && direction.kind === "enum-order") {
    return `<span class="compare-dir-badge">${escapeHtml(copy.COMPARE_DIRECTION_ENUM)}</span>`;
  }
  return "";
}

function parameterRow(paramEntry, repoKeys, evidenceByRepo, persona) {
  const values = valuesForParam(paramEntry.input_id, evidenceByRepo, repoKeys);
  const winner = betterRepoKey(paramEntry, values, persona);
  const direction = parameterDirection(paramEntry, persona);
  const humanParamName = copy.paramName(paramEntry.input_id);
  const paramLabel = escapeHtml(humanParamName);
  const dirBadge = directionBadgeHtml(direction);
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
      const betterTitle = copy.compareBetterTitle(humanParamName);
      const mark = isBetter
        ? `<span class="compare-better-mark" title="${escapeHtml(betterTitle)}" aria-label="${escapeHtml(betterTitle)}">${copy.COMPARE_BETTER_MARK}</span>`
        : "";
      const formattedRaw = copy.formatParameterValue(raw);
      return `<td class="cell-numeric${isBetter ? " compare-better-cell" : ""}" tabindex="0" title="${escapeHtml(title)}">${escapeHtml(formattedRaw)} ${mark}</td>`;
    })
    .join("");
  return (
    `<tr><th scope="row" title="${escapeHtml(paramEntry.input_id)}">` +
    `<div class="compare-param-th"><span>${paramLabel}</span>${dirBadge}</div>` +
    `</th>${cells}</tr>`
  );
}

function renderSummaryCards(repoKeys, dataByRepo, persona, verdict, floor = null) {
  const pillarIds = Object.keys(copy.PILLAR_NAMES);
  const cards = repoKeys
    .map((key) => {
      const data = dataByRepo[key];
      const [owner, name] = key.split("/");
      if (!data || data.loading) {
        return (
          `<article class="compare-repo-card is-loading">` +
          `<h3 class="compare-card-repo">${escapeHtml(key)}</h3>` +
          `<p class="compare-card-status">${escapeHtml(copy.COMPARE_LOADING_TEXT)}</p>` +
          `</article>`
        );
      }
      if (data.error || !data.row) {
        return (
          `<article class="compare-repo-card is-error">` +
          `<h3 class="compare-card-repo">${escapeHtml(key)}</h3>` +
          `<p class="compare-card-status">${copy.MISSING_VALUE}</p>` +
          `</article>`
        );
      }
      const { kind, row } = resolveCompareLens(data, persona);
      const isRated = kind === "rated" && row && typeof row.porchlamp === "number";
      const scoreHtml = isRated ? row.porchlamp.toFixed(2) : copy.MISSING_VALUE;
      const ceiling = row ? deriveTierCeiling(row.coverage, row.tier_ceiling) : null;
      const tierHtml = isRated ? renderTierCell(row.tier, ceiling) : "";
      const covHtml = renderCoverageBadge(row ? row.coverage : null);
      const quadName = isRated && row.quadrant ? row.quadrant : null;
      const quadCls = quadName ? `quad-${quadName.toLowerCase().replace(/\s+/g, "-")}` : "";
      const quadHtml = quadName
        ? `<span class="compare-card-quad ${quadCls}">${copy.QUADRANT_GLYPHS[quadName] || ""} ${escapeHtml(copy.QUADRANT_LABELS[quadName] || quadName)}</span>`
        : "";
      let statusBadge = "";
      if (kind === "gated") {
        statusBadge = `<span class="compare-status-pill is-gated">${escapeHtml(copy.gateReasonText(row.gate_reason))}</span>`;
      } else if (kind === "persona-gated") {
        const rawReason = row.persona_gates && persona ? row.persona_gates[persona] : "";
        statusBadge = `<span class="compare-status-pill is-gated">${escapeHtml(copy.personaGateReason(rawReason))}</span>`;
      } else if (kind === "unrated") {
        statusBadge = `<span class="compare-status-pill is-unrated">${escapeHtml(copy.unratedReasonText(row.unrated_reason || row.gate_reason, row.coverage, floor))}</span>`;
      } else {
        statusBadge = `<span class="compare-status-pill is-rated">${escapeHtml(copy.COMPARE_RATED_STATUS)}</span>`;
      }

      const winCount = verdict && verdict.effectiveWins ? verdict.effectiveWins[key] : null;
      const winTotal = verdict ? verdict.effectiveMeasuredCount : 0;
      const winHtml =
        winCount !== null && winCount !== undefined && winTotal > 0
          ? `<div class="compare-card-wins"><span class="compare-card-wins-label">${escapeHtml(copy.COMPARE_WINS_LABEL)}</span><strong class="compare-card-wins-val">${escapeHtml(copy.compareWinScoreText(winCount, winTotal))}</strong></div>`
          : "";

      const miniPillars = pillarIds
        .map((p) => {
          const val = data.row[`p_${p}`];
          const pct = typeof val === "number" ? Math.max(0, Math.min(100, Math.round(val))) : 0;
          const text = typeof val === "number" ? val.toFixed(0) : copy.MISSING_VALUE;
          return (
            `<div class="compare-mini-pillar">` +
            `<span class="compare-mini-pillar-name">${escapeHtml(copy.PILLAR_SHORT_NAMES[p] || p)}</span>` +
            `<span class="compare-mini-pillar-bar" aria-hidden="true"><span class="compare-mini-pillar-fill" style="width:${pct}%"></span></span>` +
            `<span class="compare-mini-pillar-val">${escapeHtml(text)}</span>` +
            `</div>`
          );
        })
        .join("");

      return (
        `<article class="compare-repo-card${isRated ? " is-rated" : " is-not-rated"}">` +
        `<div class="compare-card-head">` +
        `<button type="button" class="link-button repo-name compare-card-title" data-owner="${escapeHtml(owner)}" data-name="${escapeHtml(name)}">${escapeHtml(key)}</button>` +
        statusBadge +
        `</div>` +
        `<div class="compare-card-hero">` +
        `<div class="compare-card-score-block">` +
        `<span class="compare-card-score-label">${escapeHtml(copy.COMPARE_ROW_COMPOSITE)}</span>` +
        `<span class="compare-card-score-num">${escapeHtml(scoreHtml)}</span>` +
        `</div>` +
        `<div class="compare-card-badges">${tierHtml}${covHtml}${quadHtml}</div>` +
        `</div>` +
        winHtml +
        `<div class="compare-card-pillars">${miniPillars}</div>` +
        `</article>`
      );
    })
    .join("");

  return (
    `<section class="compare-cards-section" aria-label="${escapeHtml(copy.COMPARE_CARDS_HEADING)}">` +
    `<div class="compare-cards-grid">${cards}</div>` +
    `</section>`
  );
}

export function renderTable(state) {
  const repoKeys = state.compareRepos;
  const dataByRepo = state.compareDataByRepo;
  const persona = state.persona || "balanced";
  const evidenceByRepo = Object.fromEntries(repoKeys.map((k) => [k, (dataByRepo[k] || {}).evidence || []]));
  const headerCells = repoKeys.map((k) => {
    const [owner, name] = k.split("/");
    return (
      `<th scope="col"><button type="button" class="link-button repo-name" ` +
      `data-owner="${escapeHtml(owner)}" data-name="${escapeHtml(name)}">${escapeHtml(k)}</button></th>`
    );
  }).join("");

  const methodologyParams = state.methodology ? state.methodology.parameters : [];
  const paramsByPillar = {};
  for (const p of methodologyParams) {
    const pillar = p.pillar || "other";
    (paramsByPillar[pillar] = paramsByPillar[pillar] || []).push(p);
  }
  const orderedPillars = [
    ...Object.keys(copy.PILLAR_NAMES).filter((p) => paramsByPillar[p]),
    ...Object.keys(paramsByPillar).filter((p) => !copy.PILLAR_NAMES[p]),
  ];
  const paramRows = orderedPillars
    .map((pillar) => {
      const groupHeading =
        `<tr class="compare-section-row"><th scope="colgroup" colspan="${repoKeys.length + 1}">` +
        `${escapeHtml(copy.PILLAR_NAMES[pillar] || pillar)}</th></tr>`;
      const rows = (paramsByPillar[pillar] || [])
        .map((p) => parameterRow(p, repoKeys, evidenceByRepo, persona))
        .join("");
      return groupHeading + rows;
    })
    .join("");

  const pillarRows = Object.keys(copy.PILLAR_NAMES)
    .map((pillar) => {
      const pillarLabel = copy.PILLAR_NAMES[pillar];
      const scores = repoKeys
        .map((k) => {
          const d = dataByRepo[k];
          const v = d && d.row && typeof d.row[`p_${pillar}`] === "number" ? d.row[`p_${pillar}`] : null;
          return [k, v];
        })
        .filter(([, v]) => v !== null);
      let bestPillarKey = null;
      if (scores.length >= 2) {
        const maxVal = Math.max(...scores.map(([, v]) => v));
        const tied = scores.filter(([, v]) => v === maxVal);
        if (tied.length === 1) bestPillarKey = tied[0][0];
      }
      const cells = repoKeys
        .map((k) => pillarScoreCell(dataByRepo[k], pillar, bestPillarKey === k, pillarLabel))
        .join("");
      return `<tr class="compare-pillar-row"><th scope="row">${escapeHtml(pillarLabel)}</th>${cells}</tr>`;
    })
    .join("");

  const allLoaded = repoKeys.every((k) => dataByRepo[k] && !dataByRepo[k].loading && !dataByRepo[k].error);
  let verdict = null;
  let verdictHtml = "";
  if (allLoaded && state.methodology && repoKeys.length >= COMPARE_MIN) {
    verdict = computeVerdict(methodologyParams, evidenceByRepo, repoKeys, persona);
    // A subset verdict replaces the "cannot be computed" line rather than
    // contradicting it: the result first, then which repository it leaves
    // out, then only the parameters skipped within the subset.
    const usesSubset = Boolean(verdict.subsetText);
    const unmeasuredIds = usesSubset ? verdict.subsetNotMeasured : verdict.notMeasuredEverywhere;
    const unmeasuredNote = copy.compareUnmeasuredNote(unmeasuredIds.map((id) => copy.paramName(id)));
    const mainLine = usesSubset
      ? `<p class="compare-verdict-subset">${escapeHtml(verdict.subsetText)}</p>` +
        `<p class="compare-verdict-main">${escapeHtml(copy.compareExcludedReposText(verdict.excludedRepoKeys))}</p>`
      : `<p class="compare-verdict-main">${escapeHtml(verdict.text)}</p>`;
    verdictHtml =
      `<div class="compare-verdict"><h2>${escapeHtml(copy.COMPARE_VERDICT_HEADING)}</h2>` +
      mainLine +
      (unmeasuredNote ? `<p class="matrix-caption">${escapeHtml(unmeasuredNote)}</p>` : "") +
      `</div>`;
  }

  // The insufficient-evidence reason names the methodology's coverage floor (compare loads the methodology).
  const floor = copy.unratedCoverageFloor(state.methodology);
  const summaryCardsHtml = renderSummaryCards(repoKeys, dataByRepo, persona, verdict, floor);

  const statusRowHtml = !allLoaded
    ? `<tr><th scope="row">${escapeHtml(copy.COMPARE_ROW_IDENTITY)}</th>${repoKeys.map((k) => repoIdentityCell(k, dataByRepo[k])).join("")}</tr>`
    : "";

  return (
    verdictHtml +
    summaryCardsHtml +
    `<div class="table-scroll"><table class="compare-table">` +
    `<thead><tr><th scope="col">${escapeHtml(copy.COMPARE_ROW_IDENTITY)}</th>${headerCells}</tr></thead>` +
    `<tbody>` +
    statusRowHtml +
    `<tr><th scope="row">${escapeHtml(copy.COMPARE_ROW_GATE_STATUS)}</th>${repoKeys.map((k) => gateStatusCell(dataByRepo[k], persona, floor)).join("")}</tr>` +
    `<tr><th scope="row">${escapeHtml(copy.COMPARE_ROW_COMPOSITE)}</th>${repoKeys.map((k) => compositeCell(dataByRepo[k], persona)).join("")}</tr>` +
    `<tr><th scope="row">${escapeHtml(copy.COMPARE_ROW_COVERAGE)}</th>${repoKeys.map((k) => coverageCell(dataByRepo[k], persona)).join("")}</tr>` +
    pillarRows +
    paramRows +
    `</tbody></table></div>`
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
    return (
      heading +
      chips +
      picker +
      `<div class="empty-state">${escapeHtml(copy.COMPARE_EMPTY_TEXT)}</div>` +
      renderCompareOnboarding(state.compareRepos)
    );
  }
  if (shape === "partial-min") {
    return (
      heading +
      chips +
      picker +
      `<div class="empty-state">${escapeHtml(copy.COMPARE_MIN_TEXT)}</div>` +
      renderCompareOnboarding(state.compareRepos)
    );
  }
  // Empty host: content is patched in immediately after by `patchTable`
  // (called unconditionally right after every shell (re)build, in
  // addition to every subsequent state change) — never embedded inline
  // here, so a table-only data change (a per-repo fetch landing) never
  // needs to touch this function or the picker/input above it.
  return heading + chips + picker + `<div data-compare-table-host="true"></div>`;
}
