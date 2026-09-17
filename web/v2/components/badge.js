// components/badge.js — coverage badge, tier cell, quadrant glyph+label,
// suspicion icon. All pure `(data) -> string` functions.

import * as copy from "../copy.js";

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

/** Coverage badge, colour-coded by band (06 §3 Screen A). */
export function renderCoverageBadge(coverage) {
  if (coverage === null || coverage === undefined) {
    return `<span class="cell-missing" tabindex="0" title="${escapeHtml(copy.missingValueReason())}">${copy.MISSING_VALUE}</span>`;
  }
  const band = copy.coverageBandForValue(coverage);
  const cls = band === "full" ? "badge-coverage-full" : band === "A" ? "badge-coverage-a" : band === "B" ? "badge-coverage-b" : "badge-coverage-below";
  const text = copy.coverageBadgeText(coverage);
  return `<span class="badge ${cls}"><span class="badge-dot" aria-hidden="true"></span>${escapeHtml(text)}</span>`;
}

/** Tier pill, with "(capped)" suffix when the coverage ceiling is binding. */
export function renderTierCell(tier, tierCeiling) {
  if (!tier) {
    return `<span class="cell-missing" tabindex="0" title="${escapeHtml(copy.missingValueReason())}">${copy.MISSING_VALUE}</span>`;
  }
  const capped = !!tierCeiling && tier === tierCeiling;
  const label = copy.tierLabel(tier, tierCeiling);
  return `<span class="tier-pill${capped ? " tier-capped" : ""}">${escapeHtml(label)}</span>`;
}

/** Quadrant glyph + label — colour-blind-safe dual encoding (spec §5.1). */
export function renderQuadrant(quadrant) {
  if (!quadrant) {
    return `<span class="cell-missing" tabindex="0" title="${escapeHtml(copy.missingValueReason())}">${copy.MISSING_VALUE}</span>`;
  }
  const glyph = copy.QUADRANT_GLYPHS[quadrant] || "?";
  const label = copy.QUADRANT_LABELS[quadrant] || quadrant;
  const cls = `quad-${quadrant.toLowerCase().replace(/\s+/g, "-")}`;
  return `<span class="${cls}"><span class="quadrant-glyph" aria-hidden="true">${glyph}</span>${escapeHtml(label)}</span>`;
}

/** Suspicion icon with the detector-name reason list on hover/focus. */
export function renderSuspicionIcon(flags) {
  if (!flags || flags.length === 0) return "";
  const tooltip = copy.suspicionTooltip(flags);
  return (
    `<span class="suspicion-icon" tabindex="0" role="img" aria-label="${escapeHtml(copy.SUSPICION_LABEL)}: ${escapeHtml(flags.join(", "))}" title="${escapeHtml(tooltip)}">⚠</span>`
  );
}
