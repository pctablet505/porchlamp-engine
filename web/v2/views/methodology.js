// views/methodology.js — Screen F (06 §3 F): the published methodology
// (rendered from `GET /v1/porchlamp/methodology`, itself rendered from the
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
//
// LAYOUT (redesign 2026-09-22). This screen is a reference document, so it
// is laid out as one: an "On this page" index beside one <section> per
// topic, each with a real <h2>, in the order the rating pipeline runs
// (eligibility, scoring, missing data, lenses, ordering, integrity, release,
// history). The index and the body are both built from `sectionList`, so
// they cannot drift apart.
//
// MEASURED defect this replaces: every list was one generic
// `.evidence-table` whose cells were ALL `white-space: nowrap` except the
// first column, which had `word-break: break-word`. One long prose cell (a
// curve, a gate criterion, a change-log summary) then forced its table to
// 1,545 / 2,092 / 3,079 px, and the auto table layout squeezed the only
// breakable column -- the first -- to ~21 px, stacking every identifier one
// character per line (rows 132-163 px tall at a 1440 px viewport). Tables
// now type every cell (identifier / number / prose / meta), and the three
// prose-heavy lists (gates, detectors, change log) are entry lists.

import * as copy from "../copy.js";
import { renderCountsStrip } from "./leaderboard.js";

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]
  ));
}

const SECTION_ID_PREFIX = "methodology-";

/**
 * Payload prose with its `backtick` spans set as code. Escapes FIRST, so the
 * only markup this can ever emit is its own <code> pair: a payload string
 * carrying `<script>` stays inert text.
 */
export function inlineCodeHtml(text) {
  // \x60 is a backtick; spelled as an escape so this file has no bare
  // backtick outside a template literal (the literal scanner in
  // tests/test_ui_methodology_no_literals.py pairs them).
  return escapeHtml(text).replace(/\x60([^\x60]+)\x60/g, "<code>$1</code>");
}

/** The anchor a detector's definition carries; the snapshot's detector
 * summary links to it, so both sides must build it the same way. */
function detectorAnchorId(name) {
  return SECTION_ID_PREFIX + "detector-" + name;
}

function hasEntries(obj) {
  return Boolean(obj && Object.keys(obj).length);
}

/** A table cell that carries its column label, so the narrow-screen
 * layout (views.css `.spec-table-stack`) can print it beside the value. */
function labelledCell(cls, label, html) {
  return `<td class="${cls}" data-label="${escapeHtml(label)}">${html}</td>`;
}

function factRow(label, valuesHtml) {
  return `<div class="spec-fact"><dt>${escapeHtml(label)}</dt><dd>${valuesHtml}</dd></div>`;
}

function chips(values) {
  return values.map((v) => `<code class="spec-chip">${escapeHtml(v)}</code>`).join("");
}

function pairChips(obj) {
  return Object.entries(obj || {})
    .map(([k, v]) => `<code class="spec-chip">${escapeHtml(k)}<span class="spec-chip-value">${escapeHtml(v)}</span></code>`)
    .join("");
}

function scoreSpan(score) {
  if (score === undefined || score === null) return "";
  return `<span class="spec-chip-value">${escapeHtml(score)}</span>`;
}

function ladderHtml(caption, levels, scoreOf) {
  const chipsHtml = levels.map((level) => `<code class="spec-chip">${escapeHtml(level)}${scoreSpan(scoreOf(level))}</code>`).join("");
  return `<span class="enum-caption">${escapeHtml(caption)}</span><span class="enum-ladder">${chipsHtml}</span>`;
}

/**
 * A parameter's value domain. Numeric domains are one line of text. Enum
 * domains are a ladder, worst to best, each level with the score it maps to
 * from `value_domain.score_table`: the curve text of every enum parameter
 * says "see this parameter's value_domain.score_table", and before this
 * redesign the page printed only the level order, never the scores.
 * Persona-conditional enums get one ladder per persona order.
 */
function domainHtml(domain) {
  if (!domain) return "";
  if (domain.kind !== "enum") return escapeHtml(copy.methodologyDomainRangeText(domain));
  const table = domain.score_table || {};
  if (domain.values_worst_to_best) {
    return ladderHtml(copy.METHODOLOGY_ENUM_ORDER_LABEL, domain.values_worst_to_best, (level) => table[level]);
  }
  if (domain.persona_orders) {
    return Object.entries(domain.persona_orders)
      .map(([persona, order]) => ladderHtml(persona, order, (level) => (table[level] || {})[persona]))
      .join("");
  }
  return escapeHtml(copy.methodologyEnumOrderText(domain));
}

function proxyHtml(proxy) {
  if (proxy) return escapeHtml(copy.methodologyProxyText(proxy));
  // Most parameters have no proxy path; a column of repeated sentences was
  // noise. A dash for the eye, the sentence for a screen reader.
  return `<span class="is-muted" aria-hidden="true">${escapeHtml(copy.MISSING_VALUE)}</span><span class="sr-only">${escapeHtml(copy.METHODOLOGY_NO_PROXY_TEXT)}</span>`;
}

// ------------------------------------------------------------ Snapshot

/**
 * One identity field: a caption-weight label beside its monospace value.
 * Nobody READS a manifest root -- they look one up or copy it -- so the value
 * gets `user-select: all` (one click selects the whole digest).
 */
function identityRow(label, value) {
  if (value === null || value === undefined || value === "") return "";
  return (
    `<div class="identity-row">` +
    `<dt class="identity-label">${escapeHtml(label)}</dt>` +
    `<dd class="identity-value"><code>${escapeHtml(value)}</code></dd>` +
    `</div>`
  );
}

/**
 * The snapshot's detector flag counts (06 section 3 F lists "detector
 * summary" as Screen F content; before this redesign the payload carried it
 * and nothing rendered it). Largest first; a flag whose detector is defined
 * below links to that definition.
 */
function detectorSummary(summary, detectors) {
  const entries = Object.entries(summary || {}).sort(([, a], [, b]) => b - a);
  const defined = new Set((detectors || []).map((d) => d.name));
  const items = entries
    .map(([name, count]) => {
      const id = detectorAnchorId(name);
      const label = defined.has(name)
        ? `<a href="#${escapeHtml(id)}" data-doc-jump="${escapeHtml(id)}"><code>${escapeHtml(name)}</code></a>`
        : `<code>${escapeHtml(name)}</code>`;
      return `<li class="detector-flag">${label}<span class="detector-flag-count">${escapeHtml(copy.methodologyDetectorCountText(count))}</span></li>`;
    })
    .join("");
  const body = entries.length
    ? `<ul class="detector-flags" role="list">${items}</ul>`
    : `<p class="doc-muted">${escapeHtml(copy.METHODOLOGY_DETECTOR_SUMMARY_NONE)}</p>`;
  return `<div class="snapshot-detectors"><p class="identity-label">${escapeHtml(copy.METHODOLOGY_DETECTOR_SUMMARY_LABEL)}</p>${body}</div>`;
}

function snapshotBody(snapshot, doc) {
  return (
    `<p class="snapshot-line">${escapeHtml(copy.methodologySnapshotLine(snapshot.snapshot_id, snapshot.as_of))}</p>` +
    // Reuses the leaderboard's corpus meter rather than recomputing shares
    // here: a percentage needs a literal this file may not contain.
    renderCountsStrip(snapshot.counts, null) +
    `<dl class="identity-grid">` +
    identityRow(copy.METHODOLOGY_MANIFEST_ROOT_LABEL, snapshot.manifest_root) +
    identityRow(copy.METHODOLOGY_CONSTANTS_HASH_LABEL, snapshot.constants_hash) +
    identityRow(copy.METHODOLOGY_CANONICAL_DUMP_HASH_LABEL, snapshot.canonical_dump_hash) +
    identityRow(copy.METHODOLOGY_CODE_COMMIT_LABEL, snapshot.code_commit) +
    `</dl>` +
    detectorSummary(snapshot.detector_summary, doc.detectors) +
    `<div class="reproduce">` +
    `<div class="reproduce-head"><p class="identity-label">${escapeHtml(copy.METHODOLOGY_REPRODUCE_LABEL)}</p>` +
    `<button type="button" class="reproduce-copy" data-methodology-copy="true">${escapeHtml(copy.METHODOLOGY_COPY_LABEL)}</button></div>` +
    `<pre class="reproduce-command" data-reproduce-command="true">${escapeHtml(snapshot.reproduce_command)}</pre>` +
    `</div>`
  );
}

// ------------------------------------------------------------ Gates

function gatesBody(gates) {
  const items = gates
    .map((g) => {
      const facts = [factRow(copy.METHODOLOGY_COL_REASONS, chips(g.reasons || []))];
      if (hasEntries(g.cutoffs)) facts.push(factRow(copy.METHODOLOGY_COL_CUTOFFS, pairChips(g.cutoffs)));
      return (
        `<li class="spec-entry">` +
        `<div class="spec-entry-head"><code class="spec-entry-id">${escapeHtml(g.id)}</code>` +
        `<h3 class="spec-entry-title">${escapeHtml(g.name)}</h3></div>` +
        `<div class="spec-entry-body"><p class="spec-prose">${inlineCodeHtml(g.criterion)}</p>` +
        `<dl class="spec-facts">${facts.join("")}</dl></div>` +
        `</li>`
      );
    })
    .join("");
  return `<ul class="spec-entries" role="list">${items}</ul>`;
}

// ------------------------------------------------------------ Pillars

function pillarsBody(pillars) {
  // Bars are scaled to the heaviest pillar in CSS (`--w / --wmax`), so the
  // ratio needs no literal here; the printed percentage carries the value.
  const heaviest = Math.max(...pillars.map((p) => p.weight));
  const rows = pillars
    .map(
      (p) =>
        `<tr><th scope="row">${escapeHtml(copy.PILLAR_NAMES[p.name] || p.name)}</th>` +
        `<td><div class="pillar-weight">` +
        `<span class="pillar-bar" aria-hidden="true"><span class="pillar-bar-fill" style="--w:${escapeHtml(p.weight)}"></span></span>` +
        `<span class="pillar-weight-value">${escapeHtml(copy.methodologyPercentText(p.weight))}</span>` +
        `</div></td></tr>`
    )
    .join("");
  return (
    `<table class="pillar-weights" style="--wmax:${escapeHtml(heaviest)}">` +
    `<thead><tr><th scope="col">${escapeHtml(copy.METHODOLOGY_COL_PILLAR)}</th><th scope="col">${escapeHtml(copy.METHODOLOGY_COL_WEIGHT)}</th></tr></thead>` +
    `<tbody>${rows}</tbody></table>`
  );
}

// ------------------------------------------------------------ Parameters

/**
 * Parameters grouped by pillar, in the payload's own pillar order. A
 * parameter naming a pillar the pillar list does not carry still gets a
 * group (after the known ones), so no parameter is ever dropped.
 */
export function parameterGroups(parameters, pillars) {
  const order = pillars.map((p) => p.name);
  for (const p of parameters) if (!order.includes(p.pillar)) order.push(p.pillar);
  const weightOf = new Map(pillars.map((p) => [p.name, p.weight]));
  return order
    .map((name) => ({
      pillar: name,
      label: copy.PILLAR_NAMES[name] || name,
      weight: weightOf.has(name) ? weightOf.get(name) : null,
      parameters: parameters.filter((p) => p.pillar === name),
    }))
    .filter((g) => g.parameters.length);
}

function parametersBody(parameters, pillars) {
  const columns = [
    { label: copy.METHODOLOGY_COL_ID, cls: "col-id" },
    { label: copy.METHODOLOGY_COL_WEIGHT, cls: "num" },
    { label: copy.METHODOLOGY_COL_MIN_N, cls: "num" },
    { label: copy.METHODOLOGY_COL_WINDOW, cls: "num" },
    { label: copy.METHODOLOGY_COL_CURVE, cls: "col-prose" },
    { label: copy.METHODOLOGY_COL_DOMAIN, cls: "col-domain" },
    { label: copy.METHODOLOGY_COL_PROXY, cls: "col-meta" },
  ];
  const head = columns.map((c) => `<th scope="col" class="${c.cls}">${escapeHtml(c.label)}</th>`).join("");
  const bodies = parameterGroups(parameters, pillars)
    .map((g) => {
      const share = g.weight === null ? "" : `<span class="spec-group-share">${escapeHtml(copy.methodologyPillarShareText(g.weight))}</span>`;
      const rows = g.parameters
        .map(
          (p) =>
            `<tr>` +
            `<th scope="row" class="cell-id"><code>${escapeHtml(p.input_id)}</code></th>` +
            labelledCell("num", copy.METHODOLOGY_COL_WEIGHT, escapeHtml(copy.methodologyPercentText(p.weight_within_pillar))) +
            labelledCell("num", copy.METHODOLOGY_COL_MIN_N, escapeHtml(copy.methodologyIntText(p.min_n))) +
            labelledCell("num", copy.METHODOLOGY_COL_WINDOW, escapeHtml(copy.methodologyWindowDaysText(p.window_days))) +
            labelledCell("cell-prose", copy.METHODOLOGY_COL_CURVE, inlineCodeHtml(p.curve)) +
            labelledCell("cell-meta", copy.METHODOLOGY_COL_DOMAIN, domainHtml(p.value_domain)) +
            labelledCell(p.proxy ? "cell-meta" : "cell-meta is-empty", copy.METHODOLOGY_COL_PROXY, proxyHtml(p.proxy)) +
            `</tr>`
        )
        .join("");
      return (
        `<tbody data-pillar="${escapeHtml(g.pillar)}">` +
        `<tr class="spec-group"><th scope="rowgroup" colspan="${columns.length}">${escapeHtml(g.label)}${share}</th></tr>` +
        rows +
        `</tbody>`
      );
    })
    .join("");
  return `<div class="table-scroll"><table class="spec-table spec-table-stack spec-table-parameters"><thead><tr>${head}</tr></thead>${bodies}</table></div>`;
}

// ------------------------------------------------------------ Missingness

function stat(label, value) {
  return `<div class="spec-stat"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`;
}

function missingnessBody(missingness) {
  // The thresholds lead: "unrated below" is the number that decides whether a
  // repository appears in the ranking at all.
  const thresholds = Object.entries(missingness.coverage_thresholds || {})
    .sort(([, a], [, b]) => a - b)
    .map(([key, value]) => stat(copy.METHODOLOGY_COVERAGE_THRESHOLD_LABELS[key] || key, copy.methodologyPercentText(value)))
    .join("");
  const proxy = stat(copy.METHODOLOGY_OPENSSF_PROXY_MIN_SIGNALS_LABEL, copy.methodologyIntText(missingness.openssf_proxy_min_signals));
  const reasons = missingness.reasons
    .map((r) => `<div class="spec-def"><dt><code>${escapeHtml(r.reason)}</code></dt><dd>${inlineCodeHtml(r.semantics)}</dd></div>`)
    .join("");
  const rules = (missingness.not_applicable_rules || [])
    .map(
      (r) =>
        `<li><code>${escapeHtml(r.parameter)}</code> ${escapeHtml(copy.METHODOLOGY_NOT_APPLICABLE_WHEN)} ` +
        `<code>${escapeHtml(r.bundle_fact)} = ${escapeHtml(r.value)}</code></li>`
    )
    .join("");
  return (
    `<dl class="spec-stats">${thresholds}${proxy}</dl>` +
    `<h3 class="doc-subhead">${escapeHtml(copy.METHODOLOGY_REASONS_LABEL)}</h3>` +
    `<dl class="spec-defs">${reasons}</dl>` +
    (rules ? `<h3 class="doc-subhead">${escapeHtml(copy.METHODOLOGY_NOT_APPLICABLE_LABEL)}</h3><ul class="spec-rules">${rules}</ul>` : "")
  );
}

// ------------------------------------------------------------ Personas

function personasBody(personas) {
  const pillarIds = Object.keys(copy.PILLAR_NAMES);
  const head =
    `<th scope="col">${escapeHtml(copy.METHODOLOGY_COL_PERSONA)}</th>` +
    pillarIds.map((p) => `<th scope="col" class="num">${escapeHtml(copy.PILLAR_NAMES[p])}</th>`).join("");
  const rows = Object.entries(personas.presets)
    .map(
      ([persona, weights]) =>
        `<tr><th scope="row">${escapeHtml(copy.PERSONA_NAMES[persona] || persona)}</th>` +
        pillarIds.map((p) => `<td class="num">${escapeHtml(copy.methodologyPercentText(weights[p]))}</td>`).join("") +
        `</tr>`
    )
    .join("");
  const gates = (personas.gates || [])
    .map((g) => {
      const facts = hasEntries(g.thresholds)
        ? `<dl class="spec-facts">${factRow(copy.METHODOLOGY_COL_THRESHOLDS, pairChips(g.thresholds))}</dl>`
        : "";
      return (
        `<li class="spec-entry">` +
        `<div class="spec-entry-head"><h4 class="spec-entry-title">${escapeHtml(copy.PERSONA_NAMES[g.persona] || g.persona)}</h4></div>` +
        `<div class="spec-entry-body"><p class="spec-prose">${inlineCodeHtml(g.description)}</p>${facts}</div>` +
        `</li>`
      );
    })
    .join("");
  return (
    `<div class="table-scroll"><table class="spec-table spec-table-personas"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>` +
    (gates ? `<h3 class="doc-subhead">${escapeHtml(copy.METHODOLOGY_PERSONA_GATES_LABEL)}</h3><ul class="spec-entries" role="list">${gates}</ul>` : "")
  );
}

// ------------------------------------------------------------ Tie-breakers

function tieBreakBody(tieBreak) {
  // A real sequence (level order is the comparison order), so it is an <ol>
  // and its numbers are CSS counters -- never literals in this file.
  const steps = tieBreak.levels
    .map(
      (level) =>
        `<li class="spec-step"><div class="spec-step-main"><code class="spec-step-field">${escapeHtml(level.field)}</code>` +
        `<span class="spec-tag">${escapeHtml(level.direction)}</span></div>` +
        (level.note ? `<p class="spec-step-note">${inlineCodeHtml(level.note)}</p>` : "") +
        `</li>`
    )
    .join("");
  return `<ol class="spec-steps" role="list">${steps}</ol>`;
}

// ------------------------------------------------------------ Detectors

function detectorsBody(detectors) {
  const items = detectors
    .map((d) => {
      const facts = [factRow(copy.METHODOLOGY_COL_DIRECTION, `<span class="spec-tag">${escapeHtml(d.direction)}</span>`)];
      if (hasEntries(d.thresholds)) facts.push(factRow(copy.METHODOLOGY_COL_THRESHOLDS, pairChips(d.thresholds)));
      return (
        `<li class="spec-entry" id="${escapeHtml(detectorAnchorId(d.name))}" tabindex="-1">` +
        `<div class="spec-entry-head"><h3 class="spec-entry-title"><code>${escapeHtml(d.name)}</code></h3></div>` +
        `<div class="spec-entry-body"><p class="spec-prose">${inlineCodeHtml(d.trigger)}</p>` +
        `<dl class="spec-facts">${facts.join("")}</dl></div>` +
        `</li>`
      );
    })
    .join("");
  return `<ul class="spec-entries" role="list">${items}</ul>`;
}

// ------------------------------------------------------------ Process constants

function processConstantsBody(constants) {
  const rows = constants
    .map(
      (c) =>
        `<tr><th scope="row" class="cell-id"><code>${escapeHtml(c.name)}</code></th>` +
        labelledCell("num", copy.METHODOLOGY_COL_VALUE, escapeHtml(String(c.value))) +
        labelledCell("cell-prose", copy.METHODOLOGY_COL_GATES, inlineCodeHtml(c.gates)) +
        labelledCell("cell-id is-muted", copy.METHODOLOGY_COL_MODULE, `<code>${escapeHtml(c.module)}</code>`) +
        `</tr>`
    )
    .join("");
  return (
    `<div class="table-scroll"><table class="spec-table spec-table-stack spec-table-constants"><thead><tr>` +
    `<th scope="col" class="col-id">${escapeHtml(copy.METHODOLOGY_COL_NAME)}</th>` +
    `<th scope="col" class="num">${escapeHtml(copy.METHODOLOGY_COL_VALUE)}</th>` +
    `<th scope="col" class="col-prose">${escapeHtml(copy.METHODOLOGY_COL_GATES)}</th>` +
    `<th scope="col" class="col-id">${escapeHtml(copy.METHODOLOGY_COL_MODULE)}</th>` +
    `</tr></thead><tbody>${rows}</tbody></table></div>`
  );
}

// ------------------------------------------------------------ Change log

/**
 * The change log newest first, each entry marked `current` when the hash it
 * produced is the constants hash this page is rendered from. An equality of
 * two payload values, not a derived fact: when no entry matches (constants
 * changed without a logged RFC), no entry is marked.
 */
export function changeLogEntries(rfcLog, constantsHash) {
  return [...rfcLog].reverse().map((r) => ({ ...r, current: Boolean(constantsHash) && r.hash_after === constantsHash }));
}

function hashCell(hash) {
  if (!hash) return `<span class="is-muted">${escapeHtml(copy.MISSING_VALUE)}</span>`;
  // Visually shortened by CSS only: the full digest stays in the DOM, so a
  // click (user-select: all) or a copy takes all of it.
  return `<code class="spec-hash" title="${escapeHtml(hash)}">${escapeHtml(hash)}</code>`;
}

function rfcLogBody(rfcLog, constantsHash) {
  const items = changeLogEntries(rfcLog, constantsHash)
    .map((r) => {
      const tag = r.current
        ? `<span class="spec-tag spec-tag-current" title="${escapeHtml(copy.METHODOLOGY_RFC_CURRENT_TITLE)}">${escapeHtml(copy.METHODOLOGY_RFC_CURRENT_TAG)}</span>`
        : "";
      const hashChange =
        hashCell(r.hash_before) +
        `<span class="spec-hash-arrow" aria-hidden="true">→</span><span class="sr-only"> ${escapeHtml(copy.METHODOLOGY_HASH_TO)} </span>` +
        hashCell(r.hash_after);
      return (
        `<li class="spec-entry${r.current ? " is-current" : ""}">` +
        `<div class="spec-entry-head"><h3 class="spec-entry-title">${escapeHtml(r.rfc)}</h3>` +
        `<time class="spec-entry-date" datetime="${escapeHtml(r.date)}">${escapeHtml(r.date)}</time>${tag}</div>` +
        `<div class="spec-entry-body"><p class="spec-prose">${inlineCodeHtml(r.summary)}</p>` +
        `<dl class="spec-facts">${factRow(copy.METHODOLOGY_COL_HASH_CHANGE, hashChange)}</dl></div>` +
        `</li>`
      );
    })
    .join("");
  return `<ol class="spec-entries" role="list">${items}</ol>`;
}

// ------------------------------------------------------------ Document

/**
 * Every section in reading order: pipeline order (who is eligible, how they
 * are scored, what missing data does, the persona lenses, how ties break,
 * integrity flags, release constants, history). The ONE list both the index
 * and the body are built from.
 */
function sectionList(doc, snapshot) {
  const list = [];
  if (snapshot) {
    list.push({ key: "snapshot", label: copy.METHODOLOGY_SECTION_SNAPSHOT, count: null, body: () => snapshotBody(snapshot, doc) });
  }
  list.push(
    { key: "gates", label: copy.METHODOLOGY_SECTION_GATES, count: doc.gates.length, body: () => gatesBody(doc.gates) },
    { key: "pillars", label: copy.METHODOLOGY_SECTION_PILLARS, count: doc.pillars.length, body: () => pillarsBody(doc.pillars) },
    { key: "parameters", label: copy.METHODOLOGY_SECTION_PARAMETERS, count: doc.parameters.length, body: () => parametersBody(doc.parameters, doc.pillars) },
    { key: "missingness", label: copy.METHODOLOGY_SECTION_MISSINGNESS, count: doc.missingness.reasons.length, body: () => missingnessBody(doc.missingness) },
    { key: "personas", label: copy.METHODOLOGY_SECTION_PERSONAS, count: Object.keys(doc.personas.presets).length, body: () => personasBody(doc.personas) },
    { key: "tie_break", label: copy.METHODOLOGY_SECTION_TIE_BREAK, count: doc.tie_break_levels.levels.length, body: () => tieBreakBody(doc.tie_break_levels) },
    { key: "detectors", label: copy.METHODOLOGY_SECTION_DETECTORS, count: doc.detectors.length, body: () => detectorsBody(doc.detectors) },
    { key: "process_constants", label: copy.METHODOLOGY_SECTION_PROCESS_CONSTANTS, count: doc.not_hashed_process_constants.length, body: () => processConstantsBody(doc.not_hashed_process_constants) },
    { key: "rfc_log", label: copy.METHODOLOGY_SECTION_RFC_LOG, count: doc.rfc_log.length, body: () => rfcLogBody(doc.rfc_log, doc.constants_hash) }
  );
  return list;
}

/** The table of contents: `{id, label, count}` per section, in page order. */
export function sectionIndex(doc, snapshot) {
  return sectionList(doc, snapshot).map(({ key, label, count }) => ({ id: SECTION_ID_PREFIX + key, label, count }));
}

function countBadge(count, cls) {
  return typeof count === "number" ? `<span class="${cls}">${escapeHtml(copy.methodologyIntText(count))}</span>` : "";
}

function renderIndex(entries) {
  const items = entries
    .map(
      (e) =>
        `<li><a class="doc-index-link" href="#${e.id}" data-doc-jump="${e.id}">` +
        `<span>${escapeHtml(e.label)}</span>${countBadge(e.count, "doc-index-count")}</a></li>`
    )
    .join("");
  return (
    `<nav class="doc-index" aria-label="${escapeHtml(copy.METHODOLOGY_INDEX_LABEL)}">` +
    `<p class="doc-index-title" aria-hidden="true">${escapeHtml(copy.METHODOLOGY_INDEX_LABEL)}</p>` +
    `<ul class="doc-index-list" role="list">${items}</ul></nav>`
  );
}

function renderSection(section) {
  const id = SECTION_ID_PREFIX + section.key;
  const lede = copy.METHODOLOGY_SECTION_LEDES[section.key];
  return (
    `<section class="doc-section" id="${id}" aria-labelledby="${id}-title">` +
    `<div class="doc-section-head">` +
    // tabindex=-1: the index moves focus here after it scrolls, so keyboard
    // and screen-reader users continue from the section they chose.
    `<h2 class="doc-section-title" id="${id}-title" tabindex="-1">${escapeHtml(section.label)}</h2>` +
    countBadge(section.count, "section-count") +
    `</div>` +
    (lede ? `<p class="doc-section-lede">${escapeHtml(lede)}</p>` : "") +
    section.body() +
    `</section>`
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
  const sections = sectionList(doc, state.snapshot);
  return (
    `<div class="doc-page">` +
    `<div class="doc-page-head">${heading}` +
    `<p class="doc-lede">${escapeHtml(copy.methodologyBenchmarkLine(doc.benchmark_version, doc.constants_hash, doc.schema_version))}</p></div>` +
    `<section class="why-card" aria-labelledby="why-porchlamp-title">` +
    `<h2 class="why-title" id="why-porchlamp-title">${escapeHtml(copy.WHY_HEADING)}</h2>` +
    copy.WHY_PARAGRAPHS.map((p) => `<p>${escapeHtml(p)}</p>`).join("") +
    `</section>` +
    `<div class="doc-layout" id="methodology">` +
    renderIndex(sectionIndex(doc, state.snapshot)) +
    `<div class="doc-body">${sections.map(renderSection).join("")}</div>` +
    `</div></div>`
  );
}
