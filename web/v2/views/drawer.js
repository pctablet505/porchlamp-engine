// views/drawer.js — Screen C "Why this score" (06 §3). Section order is
// fixed by D-DEF-2 and never reordered: (1) identity + gate status, (2)
// composite, (3) pillar/evidence table, (4) persona view, (5) integrity,
// (6) playbook (stubbed). A gated repository (`gate_reason` set) renders
// section 1 (identity + the reason sentence) as always, and sections
// 3/4/5 (pillars/evidence, persona view,
// integrity) below it, all COLLAPSED (`<details>` with no `open`
// attribute) so "the reason sentence and nothing else above the fold" (06
// §3 C) still holds visually while the data is one click away instead of
// simply absent. Section 2 (composite) is OMITTED for a gated repo — no
// score/tier/quadrant was ever computed (`RepoDetailResponse.porchlamp` etc. are
// `None`); see `copy.js`'s comment above `DRAWER_SECTION_COMPOSITE`.
// Section 6 (playbook) is also omitted for gated —
// an action-recommendation stub has nothing to recommend for a repository
// disqualified before scoring. The persona section for a gated repo lists
// every known persona against the SAME repo-level gate reason (a gated
// repo has no per-persona `persona_composites`/`persona_gates` at all —
// `{}` on the wire — because disqualification happens before any persona
// lens is ever applied). An unrated repository (`unrated_reason` set)
// shows only its evidence table (section 3),
// since individual parameters may be partially measured even though
// composite coverage fell below the 0.60 floor.
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

/**
 * Locates a repo's record. The drawer's ONLY source is
 * `state.repoDetailByKey` — the result of `GET /v1/porchlamp/repos/{o}/{n}`
 * (`app.js::loadRepoDetail`, called by `openRepo` regardless of which
 * screen opened it) — so the drawer's content is identical from every
 * entry point. Matrix's own point list, a Compare chip's cache and the
 * paginated leaderboard's CURRENT PAGE are only partial views of a repo's
 * data and are never consulted here.
 */
export function findRow(state, owner, name) {
  const key = `${owner}/${name}`;
  return (state.repoDetailByKey && state.repoDetailByKey[key]) || null;
}

/**
 * `GET /v1/porchlamp/repos/{owner}/{name}` does not send `kind` on the wire at
 * all (`porchlamp/serve/contract.py`'s `RepoDetailResponse` is the flat row), so
 * `kind` is computed client-side from the same two fields the store's own
 * rule uses (`porchlamp.store.queries.list_not_ranked`'s docstring: "Gated
 * (eligible=0) and unrated (eligible=1, rated=0)") -- `!eligible` is gated
 * (disqualified before scoring, never rated), `eligible && !rated` is
 * unrated (passed eligibility, failed the coverage floor), anything else
 * is rated.
 */
export function classifyKind(found) {
  if (!found.eligible) return "gated";
  if (!found.rated) return "unrated";
  return "rated";
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
  const levCov = row.leverage_coverage != null ? Math.round(row.leverage_coverage * 100) + "%" : copy.MISSING_VALUE;
  const fricCov = row.friction_coverage != null ? Math.round(row.friction_coverage * 100) + "%" : copy.MISSING_VALUE;
  const lev = row.leverage != null ? Number(row.leverage).toFixed(1) : copy.MISSING_VALUE;
  const fric = row.friction != null ? Number(row.friction).toFixed(1) : copy.MISSING_VALUE;
  return (
    `<details open>` +
    `<summary>${escapeHtml(copy.DRAWER_SECTION_COMPOSITE)}</summary>` +
    `<p class="cell-numeric">Score ${row.porchlamp != null ? row.porchlamp.toFixed(2) : copy.MISSING_VALUE} · ${renderCoverageBadge(row.coverage)} · ${renderTierCell(row.tier, row.tier_ceiling)}</p>` +
    cappedNote +
    `<p>${renderQuadrant(row.quadrant)} — ${escapeHtml(copy.COORDINATES_LABEL)}: leverage ${lev} (coverage ${levCov}), friction ${fric} (coverage ${fricCov})</p>` +
    `</details>`
  );
}

function renderPillarRadar(row) {
  const cx = 150;
  const cy = 120;
  const r = 75;
  const n = PILLAR_ORDER.length;
  const angles = PILLAR_ORDER.map((_, i) => -Math.PI / 2 + (i * 2 * Math.PI) / n);

  const levels = [0.25, 0.5, 0.75, 1.0];
  const gridPolygons = levels
    .map((lvl) => {
      const pts = angles.map((a) => `${(cx + r * lvl * Math.cos(a)).toFixed(1)},${(cy + r * lvl * Math.sin(a)).toFixed(1)}`);
      return `<polygon points="${pts.join(" ")}" class="radar-grid" />`;
    })
    .join("");

  const spokes = angles
    .map((a) => {
      const x2 = (cx + r * Math.cos(a)).toFixed(1);
      const y2 = (cy + r * Math.sin(a)).toFixed(1);
      return `<line x1="${cx}" y1="${cy}" x2="${x2}" y2="${y2}" class="radar-spoke" />`;
    })
    .join("");

  const dataPoints = PILLAR_ORDER.map((p, i) => {
    const rawVal = row[`p_${p}`];
    const val = typeof rawVal === "number" ? Math.max(0, Math.min(100, rawVal)) : 0;
    const norm = val / 100;
    const a = angles[i];
    return {
      x: cx + r * norm * Math.cos(a),
      y: cy + r * norm * Math.sin(a),
      val: rawVal,
      pillar: p,
    };
  });

  const polyPoints = dataPoints.map((pt) => `${pt.x.toFixed(1)},${pt.y.toFixed(1)}`).join(" ");

  const labels = PILLAR_ORDER.map((p, i) => {
    const a = angles[i];
    const lx = cx + (r + 18) * Math.cos(a);
    const ly = cy + (r + 14) * Math.sin(a);
    const name = copy.PILLAR_NAMES[p] || p;
    const score = typeof row[`p_${p}`] === "number" ? row[`p_${p}`].toFixed(1) : copy.MISSING_VALUE;
    const anchor = Math.abs(Math.cos(a)) < 0.2 ? "middle" : Math.cos(a) > 0 ? "start" : "end";
    return `<text x="${lx.toFixed(1)}" y="${ly.toFixed(1)}" text-anchor="${anchor}" class="radar-label">${escapeHtml(name)} <tspan class="radar-score">${score}</tspan></text>`;
  }).join("");

  const markers = dataPoints
    .map((pt) => `<circle cx="${pt.x.toFixed(1)}" cy="${pt.y.toFixed(1)}" r="3" class="radar-point" />`)
    .join("");

  return (
    `<div class="radar-chart-container" aria-hidden="true">` +
    `<svg class="radar-chart" viewBox="0 0 300 240" role="img" aria-label="5-pillar radar visualization">` +
    gridPolygons +
    spokes +
    `<polygon points="${polyPoints}" class="radar-area" />` +
    markers +
    labels +
    `</svg></div>`
  );
}

function renderEvidenceTable(evidenceRows, evidenceError) {
  if (!evidenceRows) {
    if (evidenceError) {
      return (
        `<div class="error-banner" role="alert">${escapeHtml(copy.drawerErrorText(evidenceError.endpoint, evidenceError.status, evidenceError.detail))}` +
        ` <button type="button" class="icon-button" data-evidence-retry="true">${escapeHtml(copy.RETRY_LABEL)}</button></div>`
      );
    }
    return `<p>${escapeHtml(copy.DRAWER_LOADING_TEXT)}</p>`;
  }
  if (evidenceRows.length === 0) {
    return `<p class="empty-state">${escapeHtml(copy.DRAWER_NO_EVIDENCE_TEXT)}</p>`;
  }
  const byPillar = {};
  for (const row of evidenceRows) {
    (byPillar[row.pillar] = byPillar[row.pillar] || []).push(row);
  }
  const sections = PILLAR_ORDER.filter((p) => byPillar[p]).map((pillar) => {
    const rows = byPillar[pillar]
      .map((r) => {
        const paramLabel = escapeHtml(copy.PARAM_NAMES[r.param_id] || copy.paramName(r.param_id));
        if (r.unmeasured_reason) {
          return `<tr><td title="${escapeHtml(r.param_id)}">${paramLabel}</td><td colspan="6" class="cell-missing">${escapeHtml(copy.missingValueReason(r.unmeasured_reason))}</td></tr>`;
        }
        return (
          `<tr><td title="${escapeHtml(r.param_id)}">${paramLabel}</td>` +
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
    renderPillarRadar(row) +
    renderPillarSummary(row) +
    renderEvidenceTable(evidenceRows, evidenceError) +
    `</details>`
  );
}

/**
 * Shared by the "unrated" branch (always `open`) and the "gated" branch
 * (always collapsed): neither kind has any
 * `p_<pillar>`/`c_<pillar>` score to summarise (`RepoDetailResponse`
 * returns `None` for both — a gated/unrated repo never reaches
 * `porchlamp.core.engine`'s pillar computation), so unlike `renderPillarSection`
 * above (the "rated" case) this never calls `renderPillarSummary` — only
 * the evidence table, which is populated independently of `kind`
 * (individual parameters may still be measured/unmeasured with reasons).
 */
function renderEvidenceOnlyPillarSection(evidenceRows, evidenceError, open) {
  return (
    `<details${open ? " open" : ""}>` +
    `<summary>${escapeHtml(copy.DRAWER_SECTION_PILLARS)}</summary>` +
    renderEvidenceTable(evidenceRows, evidenceError) +
    `</details>`
  );
}

/**
 * A gated repository's `persona_gates`/`persona_composites` are
 * both `{}` on the wire (disqualification happens before ANY persona lens
 * is ever applied, so no per-persona gate code was ever computed) — this
 * section reads "all personas gated with the gate reason", so it lists
 * every known persona (`copy.PERSONA_NAMES`)
 * against the repo's own `gate_reason` sentence rather than reusing
 * `renderPersonaSection`'s per-persona `persona_gates` lookup, which would
 * render zero rows here.
 */
function renderPersonaSectionAllGated(row) {
  const reasonText = copy.gateReasonText(row.gate_reason);
  const rows = Object.keys(copy.PERSONA_NAMES)
    .map((persona) => {
      const name = copy.PERSONA_NAMES[persona];
      return `<tr><td>${escapeHtml(name)}</td><td colspan="3">${escapeHtml(reasonText)}</td></tr>`;
    })
    .join("");
  return (
    `<details>` +
    `<summary>${escapeHtml(copy.DRAWER_SECTION_PERSONAS)}</summary>` +
    `<div class="table-scroll"><table class="evidence-table"><thead><tr><th>Persona</th><th>Score</th><th>Tier</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table></div>` +
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
        `<td class="cell-numeric">${composite.porchlamp != null ? composite.porchlamp.toFixed(2) : copy.MISSING_VALUE}</td>` +
        `<td>${renderTierCell(composite.tier, composite.tier_ceiling)}</td>` +
        `<td>${composite.rated ? escapeHtml(copy.RATED_LABEL) : escapeHtml(copy.NOT_RATED_LABEL)}</td>` +
        `</tr>`
      );
    })
    .join("");
  return (
    `<details>` +
    `<summary>${escapeHtml(copy.DRAWER_SECTION_PERSONAS)}</summary>` +
    `<div class="table-scroll"><table class="evidence-table"><thead><tr><th>Persona</th><th>Score</th><th>Tier</th><th>Status</th></tr></thead><tbody>${rows}</tbody></table></div>` +
    `</details>`
  );
}

function renderIntegritySection(row) {
  const flags = row.suspicion && row.suspicion.length ? renderSuspicionIcon(row.suspicion) + " " + escapeHtml(row.suspicion.join(", ")) : escapeHtml(copy.SUSPICION_NONE_TEXT);
  const hashText = row.raw_hash ? `${escapeHtml(row.raw_hash.slice(0, 16))}…` : copy.MISSING_VALUE;
  return (
    `<details>` +
    `<summary>${escapeHtml(copy.DRAWER_SECTION_INTEGRITY)}</summary>` +
    `<p class="cell-numeric">${escapeHtml(copy.RAW_HASH_LABEL)}: ${hashText}</p>` +
    `<p class="cell-numeric">${escapeHtml(copy.SNAPSHOT_ID_LABEL)}: ${escapeHtml(row.snapshot_id || "")}</p>` +
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
    let body;
    // A 404 from `GET /v1/porchlamp/repos/{o}/{n}` arrives as
    // `state.repoDetailError` like any other non-2xx
    // (api.js/app.js::loadRepoDetail treat them the same way), but it is
    // a genuine not-found: retrying would 404 again, so it gets
    // `DRAWER_NOT_FOUND_TEXT` and no retry button. Every OTHER status
    // (500, timeout, a network failure) keeps the retry banner.
    if (state.repoDetailLoading) {
      body = `<p>${escapeHtml(copy.DRAWER_LOADING_TEXT)}</p>`;
    } else if (state.repoDetailError && state.repoDetailError.status !== 404) {
      const err = state.repoDetailError;
      body =
        `<div class="error-banner" role="alert">${escapeHtml(copy.drawerErrorText(err.endpoint, err.status, err.detail))}` +
        ` <button type="button" class="icon-button" data-repo-detail-retry="true">${escapeHtml(copy.RETRY_LABEL)}</button></div>`;
    } else {
      body = `<div class="empty-state">${escapeHtml(copy.DRAWER_NOT_FOUND_TEXT)}</div>`;
    }
    return (
      `<div class="drawer-backdrop" data-drawer-backdrop="true">` +
      `<div class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">` +
      `<div class="drawer-header"><h1 id="drawer-title">${escapeHtml(owner)}/${escapeHtml(name)}</h1>` +
      `<button type="button" class="icon-button" data-drawer-close="true">${escapeHtml(copy.DRAWER_CLOSE_LABEL)}</button></div>` +
      body +
      `</div></div>`
    );
  }

  // `found` IS the flat `RepoDetailResponse` -- `kind` is derived, `row`
  // is just a local alias so every render helper below (written against a
  // LeaderboardRow/NotRankedRow-shaped object, a strict SUBSET of this
  // flat shape's fields) can consume it directly.
  const kind = classifyKind(found);
  const row = found;
  const repoKey = `${row.owner}/${row.name}`;
  const compareBtn = `<button type="button" class="btn btn-secondary drawer-compare-btn" data-drawer-compare="${escapeHtml(repoKey)}">${escapeHtml(copy.DRAWER_COMPARE_LABEL)}</button>`;
  const closeButton = `<button type="button" class="icon-button" data-drawer-close="true">${escapeHtml(copy.DRAWER_CLOSE_LABEL)}</button>`;
  const headerActions = `<div class="drawer-actions">${compareBtn}${closeButton}</div>`;
  const identity = renderIdentity(kind, row);

  const evidenceRows = state.evidenceByRepo[state.repo];
  const evidenceError = evidenceRows ? null : state.evidenceError;

  if (kind === "gated") {
    // The gate sentence (in `identity` above) stays "the reason
    // sentence and nothing else above the fold" (06 §3 C) — every section
    // below is collapsed (`open` omitted) so nothing but the summary text
    // shows without a click. Evidence is fetched for a gated repo exactly
    // as for any other kind (`app.js::openRepo` calls `loadEvidence`
    // unconditionally, never gated on `kind`), so `evidenceRows` here is
    // the real fetched-or-loading-or-error state, same as every other
    // branch.
    const sections =
      renderEvidenceOnlyPillarSection(evidenceRows, evidenceError, false) +
      renderPersonaSectionAllGated(row) +
      renderIntegritySection(row);
    return (
      `<div class="drawer-backdrop" data-drawer-backdrop="true">` +
      `<div class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">` +
      `<div class="drawer-header">${identity}${headerActions}</div>` +
      sections +
      `</div></div>`
    );
  }

  let sections;
  if (kind === "unrated") {
    sections = renderEvidenceOnlyPillarSection(evidenceRows, evidenceError, true);
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
    `<div class="drawer-header">${identity}${headerActions}</div>` +
    sections +
    `</div></div>`
  );
}
