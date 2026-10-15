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
// Section 2 ends with the README badge box (`renderMaintainerBadge`) when the
// repository's balanced record earns one (copy.js `maintainerBadgeSpec`): the
// badge is the balanced rating whatever lens is active (U-4), and its
// Markdown points at the public site's address, which app.js passes in.
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

/**
 * Persona-aware classification for the drawer when a non-balanced lens is
 * active (`state.persona !== "balanced"`). Preserves `classifyKind(found)`
 * verbatim for balanced and for the static contract parity test.
 */
export function classifyKindForLens(found, persona) {
  if (!found.eligible) {
    if (typeof found.gate_reason === "string" && found.gate_reason.startsWith("unrated:")) {
      return "unrated";
    }
    return "gated";
  }
  if (persona && persona !== "balanced") {
    if (found.persona_gates && found.persona_gates[persona]) return "persona-gated";
    const pc = found.persona_composites && found.persona_composites[persona];
    if (pc) return pc.rated ? "rated" : "unrated";
  }
  return classifyKind(found);
}

/**
 * Derives the coverage ceiling band (`null`, `"A"`, or `"B"`) from a persona's
 * coverage fraction when `PersonaComposite` does not carry `tier_ceiling`
 * explicitly on the wire (matching `porchlamp/serve/app.py::_no_ceiling_or_band`).
 */
export function deriveTierCeiling(coverage, explicitCeiling) {
  if (explicitCeiling !== undefined) return explicitCeiling;
  if (typeof coverage !== "number" || !Number.isFinite(coverage)) return null;
  if (coverage >= 0.95) return null;
  if (coverage >= 0.8) return "A";
  if (coverage >= 0.6) return "B";
  return null;
}

/**
 * Projects the active persona's composite fields (`porchlamp`, `coverage`,
 * `tier`, `tier_ceiling`, `rated`) onto a shallow copy of `found` when a
 * non-balanced persona lens is active and has a `persona_composites` entry.
 */
export function projectRowForLens(found, persona) {
  if (!persona || persona === "balanced" || !found.persona_composites) return found;
  const pc = found.persona_composites[persona];
  if (!pc) return found;
  return {
    ...found,
    porchlamp: pc.porchlamp,
    coverage: pc.coverage,
    tier: pc.tier,
    tier_ceiling: deriveTierCeiling(pc.coverage, pc.tier_ceiling),
    rated: pc.rated,
  };
}

function renderIdentity(kind, row, persona) {
  const heading =
    `<div class="drawer-identity" aria-label="${escapeHtml(copy.DRAWER_SECTION_IDENTITY)}">` +
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
  if (kind === "persona-gated") {
    const gateCode = row.persona_gates && persona ? row.persona_gates[persona] : "";
    return (
      heading +
      `<div class="gate-banner" role="alert">${escapeHtml(copy.personaGateReason(gateCode))}</div>`
    );
  }
  if (kind === "unrated") {
    return (
      heading +
      `<div class="gate-banner" role="alert">${escapeHtml(copy.unratedReasonText(row.unrated_reason || row.gate_reason, row.coverage))}</div>`
    );
  }
  return heading;
}

/**
 * The README badge box at the end of the Composite section: a preview of the
 * image, the Markdown to paste and a copy button (app.js copies the
 * `[data-badge-markdown]` text). `canonicalRow` is the balanced record, never
 * the lens projection (U-4); `baseUrl` is the public site's address.
 */
export function renderMaintainerBadge(canonicalRow, baseUrl = "") {
  const spec = copy.maintainerBadgeSpec(canonicalRow);
  if (!spec) return "";
  return (
    `<div class="maintainer-badge-box" data-maintainer-badge="${escapeHtml(spec.id)}">` +
    `<div class="maintainer-badge-head">` +
    `<span class="identity-label">${escapeHtml(copy.MAINTAINER_BADGE_HEADING)}</span>` +
    `<button type="button" class="reproduce-copy" data-badge-copy="true">${escapeHtml(copy.MAINTAINER_BADGE_COPY_LABEL)}</button>` +
    `</div>` +
    `<img class="maintainer-badge-preview" src="${escapeHtml(spec.file)}" alt="${escapeHtml(spec.alt)}" height="20">` +
    `<p class="maintainer-badge-note">${escapeHtml(copy.MAINTAINER_BADGE_NOTE)}</p>` +
    `<pre class="maintainer-badge-snippet" data-badge-markdown="true">${escapeHtml(copy.maintainerBadgeMarkdown(canonicalRow, baseUrl))}</pre>` +
    `</div>`
  );
}

function renderComposite(row, canonicalRow, baseUrl) {
  const capped = !!row.tier_ceiling && row.tier === row.tier_ceiling;
  const cappedNote = capped
    ? `<p>${escapeHtml(copy.TIER_CAPPED_EXPLANATION)}</p>`
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
    renderMaintainerBadge(canonicalRow, baseUrl) +
    `</details>`
  );
}

export function renderPillarRadar(row) {
  const cx = 190;
  const cy = 130;
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

  const dataPoints = PILLAR_ORDER.map((p, i) => {
    const rawVal = row[`p_${p}`];
    const measured = typeof rawVal === "number" && Number.isFinite(rawVal);
    const val = measured ? Math.max(0, Math.min(100, rawVal)) : null;
    const norm = measured ? val / 100 : 0;
    const a = angles[i];
    return {
      x: cx + r * norm * Math.cos(a),
      y: cy + r * norm * Math.sin(a),
      outerX: cx + r * Math.cos(a),
      outerY: cy + r * Math.sin(a),
      val: rawVal,
      measured,
      pillar: p,
    };
  });

  const spokes = angles
    .map((a, i) => {
      const x2 = (cx + r * Math.cos(a)).toFixed(1);
      const y2 = (cy + r * Math.sin(a)).toFixed(1);
      const unmeasuredCls = dataPoints[i].measured ? "" : " radar-spoke-unmeasured";
      return `<line x1="${cx}" y1="${cy}" x2="${x2}" y2="${y2}" class="radar-spoke${unmeasuredCls}" />`;
    })
    .join("");

  const measuredPoints = dataPoints.filter((pt) => pt.measured);
  const polyPoints = measuredPoints.map((pt) => `${pt.x.toFixed(1)},${pt.y.toFixed(1)}`).join(" ");
  const polygonHtml =
    measuredPoints.length >= 3
      ? `<polygon points="${polyPoints}" class="radar-area" />`
      : measuredPoints.length === 2
        ? `<polyline points="${polyPoints}" class="radar-area" />`
        : "";

  const labels = PILLAR_ORDER.map((p, i) => {
    const a = angles[i];
    const lx = cx + (r + 18) * Math.cos(a);
    const ly = cy + (r + 14) * Math.sin(a);
    const name = copy.PILLAR_NAMES[p] || p;
    const score = dataPoints[i].measured ? row[`p_${p}`].toFixed(1) : copy.MISSING_VALUE;
    const anchor = Math.abs(Math.cos(a)) < 0.2 ? "middle" : Math.cos(a) > 0 ? "start" : "end";
    const dyAttr = Math.sin(a) > 0.3 ? ' dy="0.7em"' : "";
    return `<text x="${lx.toFixed(1)}" y="${ly.toFixed(1)}"${dyAttr} text-anchor="${anchor}" class="radar-label">${escapeHtml(name)} <tspan class="radar-score">${score}</tspan></text>`;
  }).join("");

  const markers = dataPoints
    .map((pt) =>
      pt.measured
        ? `<circle cx="${pt.x.toFixed(1)}" cy="${pt.y.toFixed(1)}" r="3" class="radar-point" data-pillar="${pt.pillar}" />`
        : `<circle cx="${pt.outerX.toFixed(1)}" cy="${pt.outerY.toFixed(1)}" r="3" class="radar-point-unmeasured" data-pillar="${pt.pillar}" />`
    )
    .join("");

  return (
    `<div class="radar-chart-container" aria-hidden="true">` +
    `<svg class="radar-chart" viewBox="-10 10 400 230" role="img" aria-label="5-pillar radar visualization">` +
    gridPolygons +
    spokes +
    polygonHtml +
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
          `<td class="cell-numeric">${r.value != null ? escapeHtml(copy.formatParameterValue(r.value)) : copy.MISSING_VALUE}</td>` +
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
 * Shared by the "unrated" branch (always `open` when no pillar scores exist)
 * and the "gated" branch (always collapsed).
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
  const composites = row.persona_composites || {};
  const canonicalKeys = Object.keys(copy.PERSONA_NAMES);
  const extraKeys = Object.keys(composites).filter((k) => !canonicalKeys.includes(k));
  const orderedKeys = [...canonicalKeys.filter((k) => k in composites), ...extraKeys];
  const isUnratedGate = typeof row.gate_reason === "string" && row.gate_reason.startsWith("unrated:");
  const fallbackReason =
    row.unrated_reason || isUnratedGate
      ? copy.unratedReasonText(row.unrated_reason || row.gate_reason, row.coverage)
      : row.gate_reason
        ? copy.gateReasonText(row.gate_reason)
        : copy.NOT_RATED_LABEL;
  const rows = orderedKeys.length
    ? orderedKeys
        .map((persona) => {
          const composite = composites[persona];
          const gateReason = row.persona_gates ? row.persona_gates[persona] : undefined;
          const name = copy.PERSONA_NAMES[persona] || persona;
          if (gateReason) {
            return `<tr><td>${escapeHtml(name)}</td><td colspan="3">${escapeHtml(copy.personaGateReason(gateReason))}</td></tr>`;
          }
          const ceiling = deriveTierCeiling(composite.coverage, composite.tier_ceiling);
          const unratedStatus =
            row.unrated_reason || isUnratedGate
              ? copy.unratedReasonText(row.unrated_reason || row.gate_reason, composite.coverage ?? row.coverage)
              : copy.NOT_RATED_LABEL;
          return (
            `<tr><td>${escapeHtml(name)}</td>` +
            `<td class="cell-numeric">${composite.porchlamp != null ? composite.porchlamp.toFixed(2) : copy.MISSING_VALUE}</td>` +
            `<td>${renderTierCell(composite.tier, ceiling)}</td>` +
            `<td>${composite.rated ? escapeHtml(copy.RATED_LABEL) : escapeHtml(unratedStatus)}</td>` +
            `</tr>`
          );
        })
        .join("")
    : canonicalKeys
        .map((persona) => {
          const gateReason = row.persona_gates ? row.persona_gates[persona] : undefined;
          const name = copy.PERSONA_NAMES[persona] || persona;
          const text = gateReason ? copy.personaGateReason(gateReason) : fallbackReason;
          return `<tr><td>${escapeHtml(name)}</td><td colspan="3">${escapeHtml(text)}</td></tr>`;
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

export function renderBlackHoleEvidence(row, evidenceRows) {
  if (!row) return "";
  const isBlackHole =
    row.gate_reason === "black_hole" ||
    (Array.isArray(row.suspicion) && row.suspicion.includes("black_hole"));
  if (!isBlackHole) return "";

  const evidence = row.black_hole_evidence;
  let unanswered = null;
  let populationN = null;
  let unansweredShare = null;
  let wilsonLowerBound = null;
  let windowStart = null;
  let windowEnd = null;
  let source = null;
  let excluded = null;
  let countedPrs = [];

  let thresholdShare = 0.5;
  let minN = 10;

  if (evidence) {
    unanswered = evidence.unanswered;
    populationN = evidence.population_n;
    unansweredShare = evidence.unanswered_share;
    wilsonLowerBound = evidence.wilson_lower_bound;
    if (typeof evidence.threshold_share === "number") thresholdShare = evidence.threshold_share;
    if (typeof evidence.min_n === "number") minN = evidence.min_n;
    windowStart = evidence.window_start;
    windowEnd = evidence.window_end;
    source = evidence.source;
    excluded = evidence.excluded;
    countedPrs = Array.isArray(evidence.counted_prs) ? evidence.counted_prs : [];
  } else if (Array.isArray(evidenceRows)) {
    const grRow = evidenceRows.find((r) => r.param_id === "gr14_ghost_rate");
    if (grRow && typeof grRow.value === "number" && typeof grRow.n === "number" && grRow.n >= minN) {
      populationN = grRow.n;
      unanswered = Math.round(grRow.value * populationN);
      unansweredShare = unanswered / populationN;
      const z = 1.644853;
      const denom = 1 + (z * z) / populationN;
      const center = unansweredShare + (z * z) / (2 * populationN);
      const spread = z * Math.sqrt((unansweredShare * (1 - unansweredShare) + (z * z) / (4 * populationN)) / populationN);
      const computedWilson = Math.max(0, (center - spread) / denom);
      if (computedWilson > thresholdShare) {
        wilsonLowerBound = computedWilson;
        windowStart = grRow.window_start;
        windowEnd = grRow.window_end;
        source = grRow.source;
      } else {
        unanswered = null;
        populationN = null;
        unansweredShare = null;
      }
    }
  }

  const hasData = typeof unanswered === "number" && typeof populationN === "number" && populationN > 0;

  let bodyHtml = "";
  if (hasData) {
    const effectiveShare =
      typeof unansweredShare === "number" ? unansweredShare : unanswered / populationN;
    let effectiveWilson = wilsonLowerBound;
    if (typeof effectiveWilson !== "number") {
      const z = 1.644853;
      const denom = 1 + (z * z) / populationN;
      const center = effectiveShare + (z * z) / (2 * populationN);
      const spread = z * Math.sqrt((effectiveShare * (1 - effectiveShare) + (z * z) / (4 * populationN)) / populationN);
      effectiveWilson = Math.max(0, (center - spread) / denom);
    }
    const pct = (effectiveShare * 100).toFixed(1);
    const wPct = (effectiveWilson * 100).toFixed(1);
    const threshPct = (thresholdShare * 100).toFixed(1);

    const statHtml = `<p class="black-hole-stat">${escapeHtml(unanswered)} of ${escapeHtml(populationN)} (${escapeHtml(pct)}%) external non-bot PRs had no maintainer response, merge, or close within 14 days</p>`;
    const wilsonHtml = `<p class="black-hole-wilson">95% Wilson lower confidence bound: ${escapeHtml(wPct)}% (threshold: &gt; ${escapeHtml(threshPct)}%, min n = ${escapeHtml(minN)})</p>`;

    let windowHtml = "";
    if (windowStart && windowEnd) {
      windowHtml = `<p class="black-hole-window">${escapeHtml(windowStart)} – ${escapeHtml(windowEnd)}</p>`;
    }
    let sourceHtml = "";
    if (source) {
      sourceHtml = `<p class="black-hole-source">Source: ${escapeHtml(source)}</p>`;
    }

    let excludedCountsHtml = "";
    if (excluded) {
      const parts = [];
      // Draft and self-closed counts exist only when the envelope recorded
      // the RFC-0008 fields; otherwise their zeros are not measurements.
      if (evidence.rule_inputs_measured === true) {
        if (excluded.draft > 0) parts.push(`${excluded.draft} draft`);
        if (excluded.self_closed > 0) parts.push(`${excluded.self_closed} author self-closed`);
      }
      if (excluded.new_account > 0) parts.push(`${excluded.new_account} new account (<90d)`);
      if (excluded.bot > 0) parts.push(`${excluded.bot} bot`);
      if (excluded.internal > 0) parts.push(`${excluded.internal} internal/maintainer`);
      if (excluded.under_14d > 0) parts.push(`${excluded.under_14d} open <14d`);
      if (parts.length > 0) {
        excludedCountsHtml = `<p class="black-hole-excluded-counts">Excluded: ${escapeHtml(parts.join(", "))}</p>`;
      }
    }

    let prTableHtml = "";
    const validPrs = countedPrs.filter(
      (pr) => typeof pr.number === "number" && Number.isInteger(pr.number) && pr.number > 0
    );
    if (validPrs.length > 0) {
      const prRows = validPrs
        .map((pr) => {
          const num = Number(pr.number);
          const prUrl = `https://github.com/${encodeURIComponent(row.owner)}/${encodeURIComponent(row.name)}/pull/${num}`;
          const statusText = escapeHtml(
            copy.blackHolePrResponseText(Boolean(pr.answered_within_14d), pr.response_kind)
          );
          return (
            `<tr>` +
            `<td><a href="${escapeHtml(prUrl)}" target="_blank" rel="noopener noreferrer">#${num}</a></td>` +
            `<td>${escapeHtml(pr.created_at || "")}</td>` +
            `<td>${statusText}</td>` +
            `</tr>`
          );
        })
        .join("");
      prTableHtml =
        `<div class="table-scroll black-hole-pr-scroll"><table class="evidence-table black-hole-pr-table"><thead><tr>` +
        `<th>PR</th><th>Opened</th><th>14-day status</th>` +
        `</tr></thead><tbody>${prRows}</tbody></table></div>`;
    }

    bodyHtml =
      statHtml +
      wilsonHtml +
      windowHtml +
      sourceHtml +
      `<p class="black-hole-exclusions">${escapeHtml(copy.BLACK_HOLE_EXCLUSIONS_TEXT)}</p>` +
      (evidence && evidence.rule_inputs_measured === false
        ? `<p class="black-hole-not-measured">${escapeHtml(copy.BLACK_HOLE_RULE_INPUTS_NOT_MEASURED_TEXT)}</p>`
        : "") +
      excludedCountsHtml +
      prTableHtml +
      `<p class="black-hole-recheck">${escapeHtml(copy.BLACK_HOLE_RECHECK_TEXT)}</p>`;
  } else {
    bodyHtml =
      `<p class="black-hole-exclusions">${escapeHtml(copy.BLACK_HOLE_EXCLUSIONS_TEXT)}</p>` +
      `<p class="black-hole-recheck">${escapeHtml(copy.BLACK_HOLE_RECHECK_TEXT)}</p>`;
  }

  return (
    `<div class="black-hole-evidence" data-black-hole-evidence="true">` +
    `<h3>${escapeHtml(copy.BLACK_HOLE_EVIDENCE_HEADING)}</h3>` +
    `<p class="black-hole-rule">${escapeHtml(copy.BLACK_HOLE_RULE_TEXT)}</p>` +
    bodyHtml +
    `</div>`
  );
}

/**
 * `opts.siteBaseUrl` is the public site's address for the README badge's
 * Markdown; app.js reads it from index.html, since this module never reads
 * the DOM.
 */
export function renderDrawer(state, opts = {}) {
  if (!state.repo) return "";
  const [owner, name] = state.repo.split("/");
  const found = owner && name ? findRow(state, owner, name) : null;

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
    const fallbackTitle = name ? `${escapeHtml(owner)}/${escapeHtml(name)}` : escapeHtml(state.repo);
    return (
      `<div class="drawer-backdrop" data-drawer-backdrop="true">` +
      `<div class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">` +
      `<div class="drawer-header"><h1 id="drawer-title">${fallbackTitle}</h1>` +
      `<button type="button" class="icon-button" data-drawer-close="true">${escapeHtml(copy.DRAWER_CLOSE_LABEL)}</button></div>` +
      body +
      `</div></div>`
    );
  }

  // `found` IS the flat `RepoDetailResponse` -- `kind` is derived under the
  // active persona lens (`classifyKindForLens`), and `row` projects the lens's
  // composite score/coverage/tier when a non-balanced lens is active on a tab
  // that exposes the persona selector (`ranked`, `notranked`, `compare`).
  const lensActiveOnTab =
    !state.tab || state.tab === "ranked" || state.tab === "notranked" || state.tab === "compare";
  const activePersona = lensActiveOnTab ? state.persona : "balanced";
  const kind = classifyKindForLens(found, activePersona);
  const row = projectRowForLens(found, activePersona);
  const repoKey = `${row.owner}/${row.name}`;
  const githubUrl = copy.drawerGithubUrl(row.owner, row.name);
  const githubLink = `<a class="btn btn-secondary drawer-github-link" href="${escapeHtml(githubUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(copy.DRAWER_GITHUB_LABEL)}</a>`;
  const compareBtn = `<button type="button" class="btn btn-secondary drawer-compare-btn" data-drawer-compare="${escapeHtml(repoKey)}">${escapeHtml(copy.DRAWER_COMPARE_LABEL)}</button>`;
  const closeButton = `<button type="button" class="icon-button" data-drawer-close="true">${escapeHtml(copy.DRAWER_CLOSE_LABEL)}</button>`;
  const headerActions = `<div class="drawer-actions">${githubLink}${compareBtn}${closeButton}</div>`;
  const identity = renderIdentity(kind, row, activePersona);

  const evidenceRows = state.evidenceByRepo[state.repo];
  const evidenceError = evidenceRows ? null : state.evidenceError;

  const isBlackHole =
    row.gate_reason === "black_hole" ||
    (Array.isArray(row.suspicion) && row.suspicion.includes("black_hole"));
  const blackHoleEvidenceHtml = isBlackHole ? renderBlackHoleEvidence(row, evidenceRows) : "";

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
      blackHoleEvidenceHtml +
      sections +
      `</div></div>`
    );
  }

  let sections;
  if (kind === "persona-gated") {
    const hasPillarScores = PILLAR_ORDER.some((p) => row[`p_${p}`] != null);
    sections =
      renderMaintainerBadge(found, opts.siteBaseUrl) +
      (hasPillarScores
        ? `<details><summary>${escapeHtml(copy.DRAWER_SECTION_PILLARS)}</summary>${renderPillarRadar(row)}${renderPillarSummary(row)}${renderEvidenceTable(evidenceRows, evidenceError)}</details>`
        : renderEvidenceOnlyPillarSection(evidenceRows, evidenceError, false)) +
      renderPersonaSection(row) +
      renderIntegritySection(row);
  } else if (kind === "unrated") {
    const hasPillarScores = PILLAR_ORDER.some((p) => row[`p_${p}`] != null);
    sections =
      renderMaintainerBadge(found, opts.siteBaseUrl) +
      (hasPillarScores
        ? renderPillarSection(row, evidenceRows, evidenceError)
        : renderEvidenceOnlyPillarSection(evidenceRows, evidenceError, true)) +
      renderPersonaSection(row) +
      renderIntegritySection(row);
  } else {
    sections = (
      renderComposite(row, found, opts.siteBaseUrl) +
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
    blackHoleEvidenceHtml +
    sections +
    `</div></div>`
  );
}
