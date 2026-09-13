// views/drawer.js — Screen C "Why this score" (06 §3). Section order is
// fixed by D-DEF-2 and never reordered: (1) identity + gate status, (2)
// composite, (3) pillar/evidence table, (4) persona view, (5) integrity,
// (6) playbook (stubbed). A gated repository (`gate_reason` set) renders
// ONLY section 1 — "the reason sentence and nothing else above the fold"
// (06 §3 C) — because a gated repository was disqualified before scoring
// and has no composite/pillar/persona/integrity data to show. An unrated
// repository (`unrated_reason` set, `06` §4: NotRankedRow carries no
// pillar/persona/integrity fields at all) still shows its evidence table
// (section 3) since individual parameters may be partially measured even
// though composite coverage fell below the 0.60 floor.
//
// Focus trap and Esc-to-close-and-restore-focus are DOM concerns handled by
// app.js; this module only produces markup.

import * as copy from "../copy.js";
import { renderCoverageBadge, renderQuadrant, renderTierCell, renderSuspicionIcon } from "../components/badge.js";
import { PILLAR_ORDER } from "../components/table.js";

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

/** Locates a repo's row across every place the store might hold it. */
export function findRow(state, owner, name) {
  const inLeaderboard = state.leaderboard && state.leaderboard.rows.find((r) => r.owner === owner && r.name === name);
  if (inLeaderboard) return { kind: "rated", row: inLeaderboard };
  if (state.notRanked) {
    const gated = state.notRanked.gated.find((r) => r.owner === owner && r.name === name);
    if (gated) return { kind: "gated", row: gated };
    const unrated = state.notRanked.unrated.find((r) => r.owner === owner && r.name === name);
    if (unrated) return { kind: "unrated", row: unrated };
  }
  return null;
}

function renderIdentity(kind, row) {
  const heading =
    `<div aria-label="${escapeHtml(copy.DRAWER_SECTION_IDENTITY)}">` +
    `<p class="drawer-kicker">${escapeHtml(copy.DRAWER_TITLE)}</p>` +
    `<h1 id="drawer-title">${escapeHtml(row.owner)}/${escapeHtml(row.name)}</h1>` +
    `<p class="eco-chip">${escapeHtml(row.ecosystem)}</p>` +
    `</div>`;
  if (kind === "gated") {
    return (
      heading +
      `<div class="gate-banner" role="alert">${escapeHtml(copy.gateReasonText(row.gate_reason))}</div>`
    );
  }
  if (kind === "unrated") {
    return (
      heading +
      `<div class="gate-banner" role="alert">${escapeHtml(copy.unratedReasonText(row.unrated_reason, row.coverage))}</div>`
    );
  }
  return heading;
}

function renderComposite(row) {
  const capped = !!row.tier_ceiling && row.tier === row.tier_ceiling;
  const cappedNote = capped
    ? `<p>${escapeHtml(copy.tierLabel(row.tier, row.tier_ceiling))}: bound by the coverage ceiling, not the score.</p>`
    : "";
  return (
    `<details open>` +
    `<summary>${escapeHtml(copy.DRAWER_SECTION_COMPOSITE)}</summary>` +
    `<p class="cell-numeric">CVI ${row.cvi.toFixed(2)} · ${renderCoverageBadge(row.coverage)} · ${renderTierCell(row.tier, row.tier_ceiling)}</p>` +
    cappedNote +
    `<p>${renderQuadrant(row.quadrant)} — ${escapeHtml(copy.COORDINATES_LABEL)}: leverage ${row.leverage ?? copy.MISSING_VALUE} (coverage ${row.leverage_coverage ?? copy.MISSING_VALUE}), friction ${row.friction ?? copy.MISSING_VALUE} (coverage ${row.friction_coverage ?? copy.MISSING_VALUE})</p>` +
    `</details>`
  );
}

function renderEvidenceTable(evidenceRows, evidenceError) {
  if (!evidenceRows) {
    if (evidenceError) {
      return (
        `<div class="error-banner" role="alert">${escapeHtml(copy.drawerErrorText(evidenceError.endpoint, evidenceError.status))}` +
        ` <button type="button" class="icon-button" data-evidence-retry="true">${escapeHtml(copy.EVIDENCE_RETRY_LABEL)}</button></div>`
      );
    }
    return `<p>${escapeHtml(copy.DRAWER_LOADING_TEXT)}</p>`;
  }
  const byPillar = {};
  for (const row of evidenceRows) {
    (byPillar[row.pillar] = byPillar[row.pillar] || []).push(row);
  }
  const sections = PILLAR_ORDER.filter((p) => byPillar[p]).map((pillar) => {
    const rows = byPillar[pillar]
      .map((r) => {
        if (r.unmeasured_reason) {
          return `<tr><td>${escapeHtml(r.param_id)}</td><td colspan="6" class="cell-missing">${escapeHtml(copy.missingValueReason(r.unmeasured_reason))}</td></tr>`;
        }
        return (
          `<tr><td>${escapeHtml(r.param_id)}</td>` +
          `<td class="cell-numeric">${escapeHtml(String(r.value))}</td>` +
          `<td class="cell-numeric">${r.score != null ? r.score.toFixed(1) : copy.MISSING_VALUE}</td>` +
          `<td class="cell-numeric">${r.n ?? copy.MISSING_VALUE}</td>` +
          `<td>${escapeHtml(r.window_start || "")}${r.window_start ? " – " : ""}${escapeHtml(r.window_end || "")}</td>` +
          `<td>${escapeHtml(r.method || copy.MISSING_VALUE)}</td>` +
          `<td title="${escapeHtml(r.source_sha256 || "")}">${escapeHtml(r.source || copy.MISSING_VALUE)}</td>` +
          `</tr>`
        );
      })
      .join("");
    return (
      `<h3>${escapeHtml(copy.PILLAR_NAMES[pillar] || pillar)}</h3>` +
      `<div class="table-scroll"><table class="evidence-table"><thead><tr>` +
      `<th>Parameter</th><th>Value</th><th>Score</th><th>n</th><th>Window</th><th>Method</th><th>Source</th>` +
      `</tr></thead><tbody>${rows}</tbody></table></div>`
    );
  });
  return sections.join("");
}

function renderPillarSummary(row) {
  const cells = PILLAR_ORDER.map((p) => {
    const score = row[`p_${p}`];
    const coverage = row[`c_${p}`];
    const scoreText = score != null ? score.toFixed(1) : copy.MISSING_VALUE;
    return `<span title="${escapeHtml(copy.PILLAR_NAMES[p])}: coverage ${Math.round((coverage || 0) * 100)}%">${escapeHtml(copy.PILLAR_NAMES[p])} ${scoreText}</span>`;
  });
  return `<p class="cell-numeric">${cells.join(" · ")}</p>`;
}

function renderPillarSection(row, evidenceRows, evidenceError) {
  return (
    `<details open>` +
    `<summary>${escapeHtml(copy.DRAWER_SECTION_PILLARS)}</summary>` +
    renderPillarSummary(row) +
    renderEvidenceTable(evidenceRows, evidenceError) +
    `</details>`
  );
}

function renderPersonaSection(row) {
  const rows = Object.entries(row.persona_composites || {})
    .map(([persona, composite]) => {
      const gateReason = row.persona_gates ? row.persona_gates[persona] : undefined;
      const name = copy.PERSONA_NAMES[persona] || persona;
      if (gateReason) {
        return `<tr><td>${escapeHtml(name)}</td><td colspan="3">${escapeHtml(copy.personaGateReason(gateReason))}</td></tr>`;
      }
      return (
        `<tr><td>${escapeHtml(name)}</td>` +
        `<td class="cell-numeric">${composite.cvi != null ? composite.cvi.toFixed(2) : copy.MISSING_VALUE}</td>` +
        `<td>${renderTierCell(composite.tier, null)}</td>` +
        `<td>${composite.rated ? escapeHtml(copy.RATED_LABEL) : escapeHtml(copy.NOT_RATED_LABEL)}</td>` +
        `</tr>`
      );
    })
    .join("");
  return (
    `<details>` +
    `<summary>${escapeHtml(copy.DRAWER_SECTION_PERSONAS)}</summary>` +
    `<div class="table-scroll"><table class="evidence-table"><thead><tr><th>Persona</th><th>CVI</th><th>Tier</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table></div>` +
    `</details>`
  );
}

function renderIntegritySection(row) {
  const flags = row.suspicion && row.suspicion.length ? renderSuspicionIcon(row.suspicion) + " " + escapeHtml(row.suspicion.join(", ")) : escapeHtml(copy.SUSPICION_NONE_TEXT);
  return (
    `<details>` +
    `<summary>${escapeHtml(copy.DRAWER_SECTION_INTEGRITY)}</summary>` +
    `<p class="cell-numeric">${escapeHtml(copy.RAW_HASH_LABEL)}: ${escapeHtml(row.raw_hash.slice(0, 16))}…</p>` +
    `<p class="cell-numeric">${escapeHtml(copy.SNAPSHOT_ID_LABEL)}: ${escapeHtml(row.snapshot_id)}</p>` +
    `<p>${flags}</p>` +
    `</details>`
  );
}

function renderPlaybookSection() {
  return (
    `<details>` +
    `<summary>${escapeHtml(copy.DRAWER_SECTION_PLAYBOOK)}</summary>` +
    `<p class="playbook-disclaimer">${escapeHtml(copy.PLAYBOOK_DISCLAIMER)}</p>` +
    `<p>${escapeHtml(copy.PLAYBOOK_STUB_TEXT)}</p>` +
    `</details>`
  );
}

export function renderDrawer(state) {
  if (!state.repo) return "";
  const [owner, name] = state.repo.split("/");
  const found = findRow(state, owner, name);

  if (!found) {
    return (
      `<div class="drawer-backdrop" data-drawer-backdrop="true">` +
      `<div class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">` +
      `<div class="drawer-header"><h1 id="drawer-title">${escapeHtml(owner)}/${escapeHtml(name)}</h1>` +
      `<button type="button" class="icon-button" data-drawer-close="true">${escapeHtml(copy.DRAWER_CLOSE_LABEL)}</button></div>` +
      (state.evidenceLoading ? `<p>${escapeHtml(copy.DRAWER_LOADING_TEXT)}</p>` : `<div class="empty-state">${escapeHtml(copy.emptyLeaderboardText(0, 0))}</div>`) +
      `</div></div>`
    );
  }

  const { kind, row } = found;
  const closeButton = `<button type="button" class="icon-button" data-drawer-close="true">${escapeHtml(copy.DRAWER_CLOSE_LABEL)}</button>`;
  const identity = renderIdentity(kind, row);

  if (kind === "gated") {
    return (
      `<div class="drawer-backdrop" data-drawer-backdrop="true">` +
      `<div class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">` +
      `<div class="drawer-header">${identity}${closeButton}</div>` +
      `</div></div>`
    );
  }

  const evidenceRows = state.evidenceByRepo[state.repo];
  const evidenceError = evidenceRows ? null : state.evidenceError;

  let sections;
  if (kind === "unrated") {
    sections = `<details open><summary>${escapeHtml(copy.DRAWER_SECTION_PILLARS)}</summary>${renderEvidenceTable(evidenceRows, evidenceError)}</details>`;
  } else {
    sections = (
      renderComposite(row) +
      renderPillarSection(row, evidenceRows, evidenceError) +
      renderPersonaSection(row) +
      renderIntegritySection(row) +
      renderPlaybookSection()
    );
  }

  return (
    `<div class="drawer-backdrop" data-drawer-backdrop="true">` +
    `<div class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">` +
    `<div class="drawer-header">${identity}${closeButton}</div>` +
    sections +
    `</div></div>`
  );
}
