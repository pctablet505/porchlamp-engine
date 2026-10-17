// components/sparkbar.js — five-pillar mini-bars with per-pillar coverage
// (06 §3 Screen A). An unmeasured pillar (score null) renders as a dashed
// zero-height placeholder instead of a bar, per U-7 ("partial: missing
// fields render '—' with the reason on focus" — here the sparkbar itself
// carries the "reason on focus" via its title attribute).

import * as copy from "../copy.js";

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

/**
 * @param {{id: string, label: string, score: number|null, coverage: number}[]} pillars
 */
export function renderSparkbarGroup(pillars) {
  const bars = pillars
    .map((p) => {
      const unmeasured = p.score === null || p.score === undefined;
      const heightPct = unmeasured ? 4 : Math.max(4, Math.round(p.score));
      const coveragePct = Math.round((p.coverage || 0) * 100);
      const title = unmeasured
        ? `${p.label}: ${copy.missingValueReason()} (coverage ${coveragePct}%)`
        : `${p.label}: ${p.score.toFixed(1)} (coverage ${coveragePct}%)`;
      return (
        `<span class="sparkbar" data-unmeasured="${unmeasured}" style="height:${heightPct}%" ` +
        `tabindex="0" role="img" aria-label="${escapeHtml(title)}" title="${escapeHtml(title)}"></span>`
      );
    })
    .join("");
  return `<span class="sparkbar-group" role="group" aria-label="${escapeHtml(copy.COLUMN_PILLARS)}">${bars}</span>`;
}
