// views/methodology.js — Screen F (06 §3 F): the published methodology
// (rendered from `GET /v1/cvi/methodology`, itself rendered from the
// frozen constants module server-side — this file never re-derives or
// restates a domain number) plus the current snapshot (`state.snapshot`,
// already loaded for the footer by every screen).
//
// BINDING RULE (06 §3 F: "every number comes from the payload"): this file
// must contain no numeric literal that stands in for a methodology fact
// (a cutoff, a weight, a coverage threshold, a count). Every number shown
// on this screen is read off `state.methodology`/`state.snapshot` and
// formatted through a `copy.methodology*` helper — see
// `tests/test_ui_methodology_no_literals.py` for the enforcement and its
// own documented allowance list.

import * as copy from "../copy.js";

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

function renderPillars(pillars) {
  const rows = pillars
    .map((p) => `<tr><td>${escapeHtml(copy.PILLAR_NAMES[p.name] || p.name)}</td><td class="cell-numeric">${escapeHtml(copy.methodologyPercentText(p.weight))}</td></tr>`)
    .join("");
  return (
    `<details open><summary>${escapeHtml(copy.METHODOLOGY_SECTION_PILLARS)}</summary>` +
    `<table class="evidence-table"><thead><tr><th>${escapeHtml(copy.METHODOLOGY_COL_PILLAR)}</th><th>${escapeHtml(copy.METHODOLOGY_COL_WEIGHT)}</th></tr></thead><tbody>${rows}</tbody></table>` +
    `</details>`
  );
}

function domainText(domain) {
  if (!domain) return "";
  return domain.kind === "enum" ? copy.methodologyEnumOrderText(domain) : copy.methodologyDomainRangeText(domain);
}

function renderParameters(parameters) {
  const rows = parameters
    .map(
      (p) =>
        `<tr><td>${escapeHtml(p.input_id)}</td><td>${escapeHtml(copy.PILLAR_NAMES[p.pillar] || p.pillar)}</td>` +
        `<td class="cell-numeric">${escapeHtml(copy.methodologyPercentText(p.weight_within_pillar))}</td>` +
        `<td class="cell-numeric">${escapeHtml(copy.methodologyIntText(p.min_n))}</td>` +
        `<td class="cell-numeric">${escapeHtml(copy.methodologyWindowDaysText(p.window_days))}</td>` +
        `<td>${escapeHtml(p.curve)}</td>` +
        `<td>${escapeHtml(domainText(p.value_domain))}</td>` +
        `<td>${escapeHtml(p.proxy ? copy.methodologyProxyText(p.proxy) : copy.METHODOLOGY_NO_PROXY_TEXT)}</td></tr>`
    )
    .join("");
  return (
    `<details><summary>${escapeHtml(copy.METHODOLOGY_SECTION_PARAMETERS)}</summary>` +
    `<div class="table-scroll"><table class="evidence-table"><thead><tr>` +
    `<th>${escapeHtml(copy.METHODOLOGY_COL_ID)}</th><th>${escapeHtml(copy.METHODOLOGY_COL_PILLAR)}</th><th>${escapeHtml(copy.METHODOLOGY_COL_WEIGHT)}</th>` +
    `<th>${escapeHtml(copy.METHODOLOGY_COL_MIN_N)}</th><th>${escapeHtml(copy.METHODOLOGY_COL_WINDOW)}</th>` +
    `<th>${escapeHtml(copy.METHODOLOGY_COL_CURVE)}</th><th>${escapeHtml(copy.METHODOLOGY_COL_DOMAIN)}</th><th>${escapeHtml(copy.METHODOLOGY_COL_PROXY)}</th>` +
    `</tr></thead><tbody>${rows}</tbody></table></div></details>`
  );
}

function renderGates(gates) {
  const rows = gates
    .map((g) => {
      const cutoffs = Object.entries(g.cutoffs || {}).map(([k, v]) => `${k}: ${v}`).join(", ");
      return (
        `<tr><td>${escapeHtml(g.id)}</td><td>${escapeHtml(g.name)}</td><td>${escapeHtml(g.criterion)}</td>` +
        `<td>${escapeHtml(g.reasons.join(", "))}</td><td>${escapeHtml(cutoffs)}</td></tr>`
      );
    })
    .join("");
  return (
    `<details><summary>${escapeHtml(copy.METHODOLOGY_SECTION_GATES)}</summary>` +
    `<div class="table-scroll"><table class="evidence-table"><thead><tr><th>${escapeHtml(copy.METHODOLOGY_COL_ID)}</th><th>${escapeHtml(copy.METHODOLOGY_COL_NAME)}</th>` +
    `<th>${escapeHtml(copy.METHODOLOGY_COL_CRITERION)}</th><th>${escapeHtml(copy.METHODOLOGY_COL_REASONS)}</th><th>${escapeHtml(copy.METHODOLOGY_COL_CUTOFFS)}</th></tr></thead>` +
    `<tbody>${rows}</tbody></table></div></details>`
  );
}

function renderMissingness(missingness) {
  const reasons = missingness.reasons
    .map((r) => `<li><strong>${escapeHtml(r.reason)}</strong>: ${escapeHtml(r.semantics)}</li>`)
    .join("");
  const thresholds = missingness.coverage_thresholds;
  const thresholdItems = Object.entries(thresholds)
    .map(([label, value]) => `<li>${escapeHtml(label)}: ${escapeHtml(copy.methodologyPercentText(value))}</li>`)
    .join("");
  const rules = missingness.not_applicable_rules
    .map((r) => `<li>${escapeHtml(r.parameter)}: ${escapeHtml(r.bundle_fact)} = ${escapeHtml(r.value)}</li>`)
    .join("");
  return (
    `<details><summary>${escapeHtml(copy.METHODOLOGY_SECTION_MISSINGNESS)}</summary>` +
    `<ul>${reasons}</ul><ul>${thresholdItems}</ul>` +
    `<p>${escapeHtml(copy.METHODOLOGY_OPENSSF_PROXY_MIN_SIGNALS_LABEL)}: ${escapeHtml(copy.methodologyIntText(missingness.openssf_proxy_min_signals))}</p>` +
    `<ul>${rules}</ul></details>`
  );
}

function renderTieBreak(tieBreak) {
  const rows = tieBreak.levels
    .map(
      (level) =>
        `<tr><td>${escapeHtml(level.field)}</td><td>${escapeHtml(level.direction)}</td><td>${escapeHtml(level.note || "")}</td></tr>`
    )
    .join("");
  return (
    `<details><summary>${escapeHtml(copy.METHODOLOGY_SECTION_TIE_BREAK)}</summary>` +
    `<p>${escapeHtml(copy.methodologyTieBreakDecimalsText(tieBreak.composite_decimals))}</p>` +
    `<table class="evidence-table"><thead><tr><th>${escapeHtml(copy.METHODOLOGY_COL_FIELD)}</th>` +
    `<th>${escapeHtml(copy.METHODOLOGY_COL_DIRECTION)}</th><th>${escapeHtml(copy.METHODOLOGY_COL_NOTE)}</th></tr></thead>` +
    `<tbody>${rows}</tbody></table></details>`
  );
}

function renderPersonas(personas) {
  const pillarIds = Object.keys(copy.PILLAR_NAMES);
  const presetRows = Object.entries(personas.presets)
    .map(([persona, weights]) => {
      const cells = pillarIds.map((p) => `<td class="cell-numeric">${escapeHtml(copy.methodologyPercentText(weights[p]))}</td>`).join("");
      return `<tr><td>${escapeHtml(copy.PERSONA_NAMES[persona] || persona)}</td>${cells}</tr>`;
    })
    .join("");
  const gateItems = personas.gates
    .map(
      (g) =>
        `<li><strong>${escapeHtml(copy.PERSONA_NAMES[g.persona] || g.persona)}</strong>: ${escapeHtml(g.description)}` +
        (Object.keys(g.thresholds).length
          ? ` (${Object.entries(g.thresholds).map(([k, v]) => `${k}: ${v}`).join(", ")})`
          : "") +
        `</li>`
    )
    .join("");
  const headerCells = pillarIds.map((p) => `<th>${escapeHtml(copy.PILLAR_NAMES[p])}</th>`).join("");
  return (
    `<details><summary>${escapeHtml(copy.METHODOLOGY_SECTION_PERSONAS)}</summary>` +
    `<table class="evidence-table"><thead><tr><th>${escapeHtml(copy.METHODOLOGY_COL_PERSONA)}</th>${headerCells}</tr></thead><tbody>${presetRows}</tbody></table>` +
    `<ul>${gateItems}</ul></details>`
  );
}

function renderDetectors(detectors) {
  const rows = detectors
    .map((d) => {
      const thresholds = Object.entries(d.thresholds || {}).map(([k, v]) => `${k}: ${v}`).join(", ");
      return `<tr><td>${escapeHtml(d.name)}</td><td>${escapeHtml(d.trigger)}</td><td>${escapeHtml(d.direction)}</td><td>${escapeHtml(thresholds)}</td></tr>`;
    })
    .join("");
  return (
    `<details><summary>${escapeHtml(copy.METHODOLOGY_SECTION_DETECTORS)}</summary>` +
    `<div class="table-scroll"><table class="evidence-table"><thead><tr><th>${escapeHtml(copy.METHODOLOGY_COL_NAME)}</th>` +
    `<th>${escapeHtml(copy.METHODOLOGY_COL_TRIGGER)}</th><th>${escapeHtml(copy.METHODOLOGY_COL_DIRECTION)}</th><th>${escapeHtml(copy.METHODOLOGY_COL_THRESHOLDS)}</th></tr></thead>` +
    `<tbody>${rows}</tbody></table></div></details>`
  );
}

function renderProcessConstants(constants) {
  const rows = constants
    .map(
      (c) =>
        `<tr><td>${escapeHtml(c.name)}</td><td>${escapeHtml(c.module)}</td>` +
        `<td class="cell-numeric">${escapeHtml(String(c.value))}</td><td>${escapeHtml(c.gates)}</td></tr>`
    )
    .join("");
  return (
    `<details><summary>${escapeHtml(copy.METHODOLOGY_SECTION_PROCESS_CONSTANTS)}</summary>` +
    `<table class="evidence-table"><thead><tr><th>${escapeHtml(copy.METHODOLOGY_COL_NAME)}</th><th>${escapeHtml(copy.METHODOLOGY_COL_MODULE)}</th>` +
    `<th>${escapeHtml(copy.METHODOLOGY_COL_VALUE)}</th><th>${escapeHtml(copy.METHODOLOGY_COL_GATES)}</th></tr></thead>` +
    `<tbody>${rows}</tbody></table></details>`
  );
}

function renderRfcLog(rfcLog) {
  const rows = rfcLog
    .map(
      (r) =>
        `<tr><td>${escapeHtml(r.date)}</td><td>${escapeHtml(r.rfc)}</td><td>${escapeHtml(r.summary)}</td>` +
        `<td class="cell-numeric">${escapeHtml(r.hash_before || "")} → ${escapeHtml(r.hash_after || "")}</td></tr>`
    )
    .join("");
  return (
    `<details><summary>${escapeHtml(copy.METHODOLOGY_SECTION_RFC_LOG)}</summary>` +
    `<div class="table-scroll"><table class="evidence-table"><thead><tr>` +
    `<th>${escapeHtml(copy.METHODOLOGY_COL_DATE)}</th><th>${escapeHtml(copy.METHODOLOGY_COL_RFC)}</th>` +
    `<th>${escapeHtml(copy.METHODOLOGY_COL_SUMMARY)}</th><th>${escapeHtml(copy.METHODOLOGY_COL_HASH_CHANGE)}</th>` +
    `</tr></thead><tbody>${rows}</tbody></table></div></details>`
  );
}

function renderSnapshot(snapshot) {
  if (!snapshot) return "";
  return (
    `<details open id="methodology" data-methodology-anchor="true"><summary>${escapeHtml(copy.METHODOLOGY_SECTION_SNAPSHOT)}</summary>` +
    `<p>${escapeHtml(copy.methodologySnapshotLine(snapshot.snapshot_id, snapshot.as_of))}</p>` +
    `<p>${escapeHtml(copy.METHODOLOGY_MANIFEST_ROOT_LABEL)}: <code>${escapeHtml(snapshot.manifest_root)}</code></p>` +
    `<p>${escapeHtml(copy.METHODOLOGY_CONSTANTS_HASH_LABEL)}: <code>${escapeHtml(snapshot.constants_hash)}</code></p>` +
    `<p>${escapeHtml(copy.methodologyCountsLine(snapshot.counts))}</p>` +
    `<p>${escapeHtml(copy.METHODOLOGY_CODE_COMMIT_LABEL)}: <code>${escapeHtml(snapshot.code_commit)}</code></p>` +
    `<p>${escapeHtml(copy.METHODOLOGY_CANONICAL_DUMP_HASH_LABEL)}: <code>${escapeHtml(snapshot.canonical_dump_hash)}</code></p>` +
    `<p>${escapeHtml(copy.METHODOLOGY_REPRODUCE_LABEL)}:</p>` +
    `<pre class="reproduce-command" data-reproduce-command="true">${escapeHtml(snapshot.reproduce_command)}</pre>` +
    `<button type="button" class="icon-button" data-methodology-copy="true">${escapeHtml(copy.METHODOLOGY_COPY_LABEL)}</button>` +
    `</details>`
  );
}

export function shellShape(state) {
  if (state.methodologyLoading && !state.methodology) return "loading";
  if (state.methodologyError && !state.methodology) return "error";
  if (!state.methodology) return "empty";
  // The Snapshot section only has content once `state.snapshot` itself has
  // loaded (a SEPARATE fetch, kicked off by app.js independently of
  // methodology's own) -- appending its presence to the shape key is what
  // makes `syncView` rebuild the shell when it lands after the methodology
  // payload already did, instead of the two payloads racing and the
  // snapshot section silently never appearing (a real bug caught during
  // live-browser verification, see the handoff).
  return `body:${state.snapshot ? "snap" : "nosnap"}`;
}

export function renderShell(state) {
  const shape = shellShape(state);
  const heading = `<h1 class="view-heading">${escapeHtml(copy.METHODOLOGY_HEADING)}</h1>`;
  if (shape === "loading") return heading + `<div class="empty-state" aria-busy="true">${escapeHtml(copy.METHODOLOGY_LOADING_TEXT)}</div>`;
  if (shape === "error") {
    return heading + `<div class="error-banner" role="alert">${escapeHtml(copy.methodologyErrorText(state.methodologyError.endpoint, state.methodologyError.status, state.methodologyError.detail))}</div>`;
  }
  if (shape === "empty") return heading + `<div class="empty-state">${escapeHtml(copy.METHODOLOGY_EMPTY_TEXT)}</div>`;

  const doc = state.methodology;
  return (
    heading +
    `<p>${escapeHtml(copy.methodologyBenchmarkLine(doc.benchmark_version, doc.constants_hash, doc.schema_version))}</p>` +
    renderSnapshot(state.snapshot) +
    renderPillars(doc.pillars) +
    renderParameters(doc.parameters) +
    renderGates(doc.gates) +
    renderMissingness(doc.missingness) +
    renderTieBreak(doc.tie_break_levels) +
    renderPersonas(doc.personas) +
    renderDetectors(doc.detectors) +
    renderProcessConstants(doc.not_hashed_process_constants) +
    renderRfcLog(doc.rfc_log)
  );
}
