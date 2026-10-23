// views/project.js — a project, drawn once for two places: the side panel's body (mode "panel", inside the drawer
// dialog) and the project's own page (mode "page", which the export's Node prerender writes for crawlers).
//
// Pure: `renderProject(record, opts)` returns the markup. It reads no DOM and no locale (dates and numbers are
// formatted here), so every renderer prints the same bytes. Every printed number sits in `<span data-v="<id>">`
// (components/fill.js `hook`), the hook scripts/verify_static_export.py reads the written page back by; the words are
// copy.js's, and the sentences that carry a number are templates this view fills or the record's own `answer`.
//
// `record` is `{detail, evidence, answer?, standings?, similar?}`: `detail` is the API's repo row (the lens-projected
// row in the panel), `evidence` its evidence rows (absent while they load), the rest the export's extras for a
// project with a balanced rank (null otherwise, and always null from the dev API).
//
// `opts`:
//   mode          "page" | "panel"                       headings (h1/h2/h3 or h2/h3/h4), crumb, lede, Close, actions
//   recordFile    "data/<snapshot>/repos/<bucket>.json"  the Integrity link to the record's file
//   pageHref      the project's own page                 "Full page →" in the panel, only when the caller gives it
//   kind          "rated" | "unrated" | "gated" | "persona-gated"   what the active lens makes of the project;
//                 derived from `detail` (the balanced lens) when absent
//   persona       the lens whose gate `kind: "persona-gated"` names
//   badgeHtml     the README badge box (drawer.js renderMaintainerBadge, always read from the balanced row)
//   evidenceError the failed evidence request, shown with a retry button in place of the parameters
//   coverageFloor the methodology's coverage floor (copy.js unratedCoverageFloor), named by an unrated project's banner;
//                 null while the methodology loads, when the banner says the reason alone

import * as copy from "../copy.js";
import { renderCoverageBadge, renderQuadrant, renderTierCell } from "../components/badge.js";
import { PILLAR_ORDER } from "../components/table.js";
import { escapeHtml as e } from "../escape.js";
import { fill, hook } from "../components/fill.js";
import { projectPath } from "../routes.js";

// ---- formatting: JavaScript formats, the Python verifier recomputes every hook and compares ----

const fmtInt = (n) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
const pct0 = (fraction) => `${Math.round(fraction * 100)}%`;

/** `template` with each `{slot}` replaced by `values[slot]`, as text (fill.js's `fill` makes markup). */
function plain(template, values) {
  return template.replace(/\{([a-z_]+)\}/g, (_slot, key) => {
    if (!Object.prototype.hasOwnProperty.call(values, key)) throw new Error(`no value for the slot {${key}}`);
    return values[key];
  });
}

const ISO_DAY = /^(\d{4})-(\d{2})-(\d{2})/;

/** "17 April 2026" from an ISO date or timestamp (project_pages.long_date); text that is no date is returned as is. */
export function longDate(iso) {
  const day = ISO_DAY.exec(String(iso));
  if (!day) return String(iso);
  return `${Number(day[3])} ${copy.MONTHS[Number(day[2]) - 1]} ${Number(day[1])}`;
}

/** "17 April to 14 October 2026"; both years when they differ (project_pages.window_text). */
export function windowText(start, end) {
  const from = ISO_DAY.exec(String(start));
  const to = ISO_DAY.exec(String(end));
  if (!from || !to) return `${start} – ${end}`;
  const left = from[1] === to[1] ? `${Number(from[3])} ${copy.MONTHS[Number(from[2]) - 1]}` : longDate(start);
  return `${left} to ${longDate(end)}`;
}

/** `value * times / by`, rounded half up to `places` decimals, as text: exact on the decimal digits JavaScript prints
 *  for `value` (the shortest that round-trips, as Python's `repr` does), the rule of project_pages.reply_time_text and
 *  percent_text (`Decimal(repr(x)) * times / by` then `quantize(..., ROUND_HALF_UP)`). `toFixed` and a float quotient
 *  round the binary double instead, which differs on a printed half (3.85, 0.3445). For a value of 0 or more. */
function decimalHalfUp(value, times, by, places) {
  const printed = /e/i.test(String(value)) ? value.toFixed(20).replace(/0+$/, "") : String(value);
  const [whole, fraction = ""] = printed.split(".");
  const digits = BigInt(whole + fraction);
  const scale = 10n ** BigInt(fraction.length);
  const unit = 10n ** BigInt(places);
  const rounded = (2n * digits * times * unit + scale * by) / (2n * scale * by);
  const text = rounded.toString().padStart(places + 1, "0");
  return places ? `${text.slice(0, -places)}.${text.slice(-places)}` : text;
}

/** Hours with one decimal ("3.9", "148.3"), half up from the printed decimal. */
const hoursText = (hours) => decimalHalfUp(hours, 1n, 1n, 1);
/** Days with one decimal from hours ("6.2"; a trailing ".0" drops, as in Python's "3 days"). */
export const daysText = (hours) => String(Number(decimalHalfUp(hours, 1n, 24n, 1)));
/** A share (a fraction) as a percent with one decimal ("45.2"), half up from the printed decimal. */
const shareText = (fraction) => decimalHalfUp(fraction, 100n, 1n, 1);
/** A fraction as a whole percent, half up from its printed decimal (project_pages.percent_text: 0.6667 -> "67%"). */
export const percentWhole = (fraction) => `${decimalHalfUp(fraction, 100n, 1n, 0)}%`;

/** The median first reply, in words (project_pages.reply_time_text): hours under 48 ("3.9 hours"), else days
 *  ("6.2 days") and, as the key facts print it, the hours too ("6.2 days (148.3 hours)"). */
export function replyTimeText(hours) {
  const words = copy.REPLY_TIME_WORDS;
  if (hours < 48) {
    const shown = Number(hoursText(hours));
    if (shown === 0) return words.under_tenth_hour;
    return plain(shown === 1 ? words.hour : words.hours, { n: String(shown) });
  }
  return plain(words.days_and_hours, { days: daysText(hours), hours: hoursText(hours) });
}

const SHARE_PARAMS = new Set([
  "dr90_decision_rate", "mr_first_rate", "gr14_ghost_rate", "substantive_review_rate", "newcomer_conversion", "rr90_return_rate",
]);
const HOURS_PARAMS = new Set(["ttfhr_hours", "ttm_p90_hours"]);
const COUNT_PARAMS = new Set(["dependents_deduped", "elephant_factor", "newcomer_issue_supply"]);
const ENUM_LABELS = Object.freeze({
  legal_ip: copy.LEGAL_IP_LABELS,
  ai_posture: copy.AI_POSTURE_LABELS,
  promotion_ladder: copy.PROMOTION_LADDER_LABELS,
});

/** A parameter's value as the table prints it, with its unit (DESIGN: units in the parameter table). The shares and
 *  the hours the methodology states are the rows' values; the enums print their plain label; a code the engine does
 *  not define prints as it is. */
export function formatParameterValue(paramId, value) {
  if (value === null || value === undefined) return copy.MISSING_VALUE;
  const words = copy.PARAM_UNIT_WORDS;
  if (typeof value === "number" && Number.isFinite(value)) {
    if (HOURS_PARAMS.has(paramId)) {
      return plain(words.hours_and_days, { hours: hoursText(value), days: daysText(value) });
    }
    if (SHARE_PARAMS.has(paramId)) return plain(words.share, { percent: shareText(value) });
    if (paramId === "downloads_monthly" && Number.isInteger(value)) {
      return value >= 1e6
        ? plain(words.downloads_million, { count: (value / 1e6).toFixed(1) })
        : plain(words.downloads, { count: fmtInt(value) });
    }
    if (COUNT_PARAMS.has(paramId) && Number.isInteger(value)) return fmtInt(value);
  }
  const labels = ENUM_LABELS[paramId];
  if (labels && Object.prototype.hasOwnProperty.call(labels, value)) return labels[value];
  return copy.formatParameterValue(value);
}

/** The coverage ceiling band (`null`, "A" or "B") from a coverage fraction, as `PersonaComposite` does not always
 *  carry `tier_ceiling` (porchlamp/serve/app.py::_no_ceiling_or_band). */
export function deriveTierCeiling(coverage, explicitCeiling) {
  if (explicitCeiling !== undefined) return explicitCeiling;
  if (typeof coverage !== "number" || !Number.isFinite(coverage)) return null;
  if (coverage >= 0.9) return null;
  if (coverage >= 0.8) return "A";
  if (coverage >= 0.6) return "B";
  return null;
}

/** What the balanced lens makes of a row (the rule of drawer.js classifyKind and classifyKindForLens). */
function kindOf(d) {
  if (!d.eligible) return typeof d.gate_reason === "string" && d.gate_reason.startsWith("unrated:") ? "unrated" : "gated";
  return d.rated ? "rated" : "unrated";
}

const measuredRow = (row) => !!row && !row.unmeasured_reason && row.value !== null && row.value !== undefined;

// ---- the radar (moved from drawer.js) ----

/** The five pillars as a radar. Decorative: the table under it says the same in text, so it is `aria-hidden`.
 *  Labels are 13 px or more (views.css `.radar-label`) at the 280-unit width, which a phone's content column is wider than. */
export function renderPillarRadar(row) {
  const cx = 140;
  const cy = 112;
  const r = 54;
  const n = PILLAR_ORDER.length;
  const angles = PILLAR_ORDER.map((_, i) => -Math.PI / 2 + (i * 2 * Math.PI) / n);
  const at = (a, radius) => `${(cx + radius * Math.cos(a)).toFixed(1)},${(cy + radius * Math.sin(a)).toFixed(1)}`;

  const grid = [0.25, 0.5, 0.75, 1.0]
    .map((level) => `<polygon points="${angles.map((a) => at(a, r * level)).join(" ")}" class="radar-grid" />`)
    .join("");

  const points = PILLAR_ORDER.map((p, i) => {
    const raw = row[`p_${p}`];
    const measured = typeof raw === "number" && Number.isFinite(raw);
    return { pillar: p, a: angles[i], measured, norm: measured ? Math.max(0, Math.min(100, raw)) / 100 : 0 };
  });

  const spokes = points
    .map((pt) => {
      const [x2, y2] = at(pt.a, r).split(",");
      return `<line x1="${cx}" y1="${cy}" x2="${x2}" y2="${y2}" class="radar-spoke${pt.measured ? "" : " radar-spoke-unmeasured"}" />`;
    })
    .join("");

  const measured = points.filter((pt) => pt.measured);
  const area = measured.map((pt) => at(pt.a, r * pt.norm)).join(" ");
  const shape =
    measured.length >= 3
      ? `<polygon points="${area}" class="radar-area" />`
      : measured.length === 2
        ? `<polyline points="${area}" class="radar-area" />`
        : "";

  const labels = points
    .map((pt) => {
      const x = cx + (r + 10) * Math.cos(pt.a);
      const y = cy + (r + 10) * Math.sin(pt.a);
      const anchor = Math.abs(Math.cos(pt.a)) < 0.2 ? "middle" : Math.cos(pt.a) > 0 ? "start" : "end";
      const dy = Math.sin(pt.a) > 0.3 ? ' dy="0.7em"' : "";
      return `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}"${dy} text-anchor="${anchor}" class="radar-label">${e(copy.PILLAR_NAMES[pt.pillar] || pt.pillar)}</text>`;
    })
    .join("");

  const markers = points
    .map((pt) => {
      const [x, y] = at(pt.a, pt.measured ? r * pt.norm : r).split(",");
      return `<circle cx="${x}" cy="${y}" r="3" class="${pt.measured ? "radar-point" : "radar-point-unmeasured"}" data-pillar="${pt.pillar}" />`;
    })
    .join("");

  return (
    `<div class="radar-chart-container" aria-hidden="true">` +
    `<svg class="radar-chart" viewBox="0 0 280 215" focusable="false">${grid}${spokes}${shape}${markers}${labels}</svg>` +
    `</div>`
  );
}

// ---- sections ----

/** The heading tags of a mode: the page's title is the one h1; the panel's is an h2, because the list behind the
 *  dialog owns the h1. */
const headings = (mode) => (mode === "page" ? { title: "h1", section: "h2", group: "h3" } : { title: "h2", section: "h3", group: "h4" });

function ecosystemChip(ecosystem) {
  const pair = copy.ECOSYSTEMS.find(([key]) => key === ecosystem);
  const slug = copy.ECOSYSTEM_SLUGS[ecosystem];
  if (!pair || !slug) return `<span class="eco-chip project-eco">${e(ecosystem)}</span>`;
  return `<a class="eco-chip project-eco" href="/${e(slug)}/">${e(pair[1])}</a>`;
}

function headBlock(rec, mode, opts) {
  const d = rec.detail;
  const key = `${d.owner}/${d.name}`;
  const tag = headings(mode).title;
  const github = `<a class="btn btn-secondary drawer-github-link" href="${e(copy.drawerGithubUrl(d.owner, d.name))}" target="_blank" rel="noopener noreferrer">${e(copy.DRAWER_GITHUB_LABEL)}</a>`;
  // The page's Compare is a link that works before any script runs; the panel's is the app's button.
  const compare =
    mode === "page"
      ? `<a class="btn btn-secondary drawer-compare-btn" href="${e(copy.PROJECT_COMPARE_HREF + encodeURIComponent(key))}">${e(copy.DRAWER_COMPARE_LABEL)}</a>`
      : `<button type="button" class="btn btn-secondary drawer-compare-btn" data-drawer-compare="${e(key)}">${e(copy.DRAWER_COMPARE_LABEL)}</button>`;
  const close = mode === "panel" ? `<button type="button" class="icon-button" data-drawer-close="true">${e(copy.DRAWER_CLOSE_LABEL)}</button>` : "";
  const pageLink =
    mode === "panel" && opts.pageHref
      ? `<a class="drawer-page-link" href="${e(opts.pageHref)}" data-drawer-page="true">${e(copy.DRAWER_PAGE_LABEL)}</a>`
      : "";
  const crumb =
    mode === "page"
      ? `<nav class="project-crumb" aria-label="${e(copy.PROJECT_CRUMB_LABEL)}"><a href="/">${e(copy.PRIMARY_NAV_LEADERBOARD)}</a><span aria-hidden="true"> › </span><span aria-current="page">${e(key)}</span></nav>`
      : "";
  const lede =
    mode === "page"
      ? `<p class="project-lede">${fill(copy.PROJECT_LEDE, { date: hook("detail.snapshot_date", longDate(d.snapshot_id)) })}</p>`
      : "";
  const answer = rec.answer ? `<p class="project-answer" id="answer" data-v="answer">${e(rec.answer.join(" "))}</p>` : "";
  return (
    crumb +
    `<div class="project-head">` +
    `<div class="project-identity">` +
    (mode === "panel" ? `<p class="drawer-kicker">${e(copy.DRAWER_TITLE)}</p>` : "") +
    `<${tag} id="${mode === "page" ? "project-title" : "drawer-title"}" class="project-title"><span class="repo-owner">${e(d.owner)}/</span>${e(d.name)}</${tag}>` +
    `<p class="project-meta">${ecosystemChip(d.ecosystem)}${pageLink}</p>` +
    `</div>` +
    `<div class="drawer-actions">${github}${compare}${close}</div>` +
    `</div>` +
    (lede || answer ? `<div class="project-intro">${lede}${answer}</div>` : "")
  );
}

/** The sentence that stands in for the score of a project the lens does not rate. A static paragraph, not an alert:
 *  it is content present at load, not a live change. */
function bannerBlock(rec, kind, opts) {
  const d = rec.detail;
  const text =
    kind === "gated"
      ? copy.gateReasonText(d.gate_reason)
      : kind === "persona-gated"
        ? copy.personaGateReason(d.persona_gates && opts.persona ? d.persona_gates[opts.persona] : "")
        : copy.unratedReasonText(d.unrated_reason || d.gate_reason, d.coverage, opts.coverageFloor ?? null);
  return `<p class="gate-banner project-gate">${e(text)}</p>`;
}

const ofTotal = (rank, total, rankId, totalId) =>
  total === undefined || total === null
    ? hook(rankId, fmtInt(rank))
    : fill(copy.OF_TOTAL, { rank: hook(rankId, fmtInt(rank)), total: hook(totalId, fmtInt(total)) });

/** AI-04's key facts: a small table of row headers right after the answer. Ranked projects only. */
function keyFactsBlock(rec) {
  const d = rec.detail;
  const standings = rec.standings || null;
  const evidence = rec.evidence || [];
  const byParam = (id) => evidence.find((row) => row.param_id === id);
  const reply = byParam("ttfhr_hours");
  const ghost = byParam("gr14_ghost_rate");
  const windowRow = [reply, ghost].find((row) => measuredRow(row) && row.window_start && row.window_end);
  const row = (label, cell) => `<tr><th scope="row">${e(label)}</th><td>${cell}</td></tr>`;
  const missing = copy.MISSING_VALUE;

  const rows = [];
  if (d.rank !== null && d.rank !== undefined) {
    rows.push(row(copy.COLUMN_RANK, ofTotal(d.rank, standings && standings.balanced ? standings.balanced.total : null, "detail.rank", "standings.balanced.total")));
  }
  rows.push(row(copy.COLUMN_PORCHLAMP, d.porchlamp != null ? hook("detail.porchlamp", d.porchlamp.toFixed(2)) : missing));
  rows.push(row(copy.COLUMN_TIER, renderTierCell(d.tier, d.tier_ceiling)));
  rows.push(row(copy.KEY_FACT_LABELS.reply_time, measuredRow(reply) ? hook("facts.reply_time", replyTimeText(reply.value)) : missing));
  rows.push(row(copy.KEY_FACT_LABELS.no_reply_share, measuredRow(ghost) ? hook("facts.no_reply_share", percentWhole(ghost.value)) : missing));
  const first = standings ? standings.first_timer : null;
  if (first) {
    rows.push(
      row(
        copy.KEY_FACT_LABELS.first_timer_rank,
        first.rank !== null && first.rank !== undefined
          ? ofTotal(first.rank, first.total, "standings.first_timer.rank", "standings.first_timer.total")
          : first.reason
            ? hook("standings.first_timer.reason", first.reason)
            : missing,
      ),
    );
  }
  const eco = copy.ECOSYSTEMS.find(([key]) => key === d.ecosystem);
  rows.push(row(copy.ECOSYSTEM_LABEL, e(eco ? eco[1] : d.ecosystem)));
  rows.push(
    row(
      copy.PROJECT_COLUMNS.window,
      windowRow ? hook(`evidence.${windowRow.param_id}.window`, windowText(windowRow.window_start, windowRow.window_end)) : missing,
    ),
  );
  return (
    `<table class="project-table key-facts" id="key-facts"><caption>${e(copy.PROJECT_KEY_FACTS_CAPTION)}</caption>` +
    `<tbody>${rows.join("")}</tbody></table>`
  );
}

function compositeBlock(rec, mode, opts) {
  const d = rec.detail;
  const tag = headings(mode).section;
  const total = rec.standings && rec.standings.balanced ? rec.standings.balanced.total : null;
  const capped = !!d.tier_ceiling && d.tier === d.tier_ceiling;
  const show = (value, id, format) => (value != null ? hook(id, format(value)) : copy.MISSING_VALUE);
  const fact = (label, value) => `<div class="project-fact"><dt>${e(label)}</dt><dd>${value}</dd></div>`;
  const rank =
    d.rank !== null && d.rank !== undefined
      ? `<span class="project-figure">${hook("detail.rank", fmtInt(d.rank))}</span>` +
        (total !== null ? ` <span class="project-figure-of">${fill(copy.OF_TOTAL_SUFFIX, { total: hook("standings.balanced.total", fmtInt(total)) })}</span>` : "")
      : copy.MISSING_VALUE;
  const coordinates = fill(copy.COORDINATES_LINE, {
    label: e(copy.COORDINATES_LABEL),
    leverage: show(d.leverage, "detail.leverage", (x) => Number(x).toFixed(1)),
    leverage_coverage: show(d.leverage_coverage, "detail.leverage_coverage", pct0),
    friction: show(d.friction, "detail.friction", (x) => Number(x).toFixed(1)),
    friction_coverage: show(d.friction_coverage, "detail.friction_coverage", pct0),
  });
  return (
    `<section class="project-card project-lead" id="rating" aria-labelledby="rating-h">` +
    `<${tag} class="project-h2" id="rating-h">${e(copy.DRAWER_SECTION_COMPOSITE)}</${tag}>` +
    `<dl class="project-facts">` +
    fact(copy.COLUMN_RANK, rank) +
    fact(copy.COLUMN_PORCHLAMP, d.porchlamp != null ? `<span class="project-figure">${hook("detail.porchlamp", d.porchlamp.toFixed(2))}</span>` : copy.MISSING_VALUE) +
    fact(copy.COLUMN_TIER, renderTierCell(d.tier, d.tier_ceiling)) +
    fact(copy.COLUMN_COVERAGE, renderCoverageBadge(d.coverage, "detail.coverage")) +
    fact(copy.COLUMN_QUADRANT, renderQuadrant(d.quadrant)) +
    `</dl>` +
    `<p class="project-note">${coordinates}</p>` +
    (capped ? `<p class="project-note">${e(copy.TIER_CAPPED_EXPLANATION)}</p>` : "") +
    (opts.badgeHtml || "") +
    `</section>`
  );
}

function blackHoleBlock(rec, mode) {
  const d = rec.detail;
  const isBlackHole = d.gate_reason === "black_hole" || (Array.isArray(d.suspicion) && d.suspicion.includes("black_hole"));
  if (!isBlackHole) return "";
  const tag = headings(mode).section;
  const ev = d.black_hole_evidence;
  const note = (html) => `<p class="project-note">${html}</p>`;
  const rule = note(e(copy.BLACK_HOLE_RULE_TEXT));
  const exclusions = note(e(copy.BLACK_HOLE_EXCLUSIONS_TEXT));
  const recheck = note(e(copy.BLACK_HOLE_RECHECK_TEXT));
  const open = `<section class="project-card black-hole-evidence" data-black-hole-evidence="true" id="black-hole" aria-labelledby="bh-h"><${tag} class="project-h2" id="bh-h">${e(copy.BLACK_HOLE_EVIDENCE_HEADING)}</${tag}>`;
  const hasCounts = !!ev && typeof ev.unanswered === "number" && typeof ev.population_n === "number" && ev.population_n > 0;
  if (!hasCounts) return `${open}${rule}${exclusions}${recheck}</section>`;

  const measured = ev.rule_inputs_measured;
  const predicate = measured === false ? copy.BLACK_HOLE_STAT_PREDICATE_CLOSE_NOT_COUNTED : copy.BLACK_HOLE_STAT_PREDICATE;
  const stat =
    `<p class="black-hole-stat">${hook("bh.unanswered", String(ev.unanswered))} of ${hook("bh.population_n", String(ev.population_n))} ` +
    (typeof ev.unanswered_share === "number" ? `(${hook("bh.unanswered_share", shareText(ev.unanswered_share))}%) ` : "") +
    `${e(predicate)}</p>`;
  const wilson =
    typeof ev.wilson_lower_bound === "number" && typeof ev.threshold_share === "number" && typeof ev.min_n === "number"
      ? note(
          fill(copy.BLACK_HOLE_WILSON_LINE, {
            wilson: hook("bh.wilson_lower_bound", shareText(ev.wilson_lower_bound)),
            threshold: hook("bh.threshold_share", shareText(ev.threshold_share)),
            min_n: hook("bh.min_n", String(ev.min_n)),
          }),
        )
      : "";
  const window = ev.window_start && ev.window_end ? note(`${e(copy.PROJECT_COLUMNS.window)}: ${hook("bh.window", windowText(ev.window_start, ev.window_end))}`) : "";
  const source = ev.source ? note(`${e(copy.BLACK_HOLE_SOURCE_LEAD)} <span class="project-mono">${e(ev.source)}</span>`) : "";

  const x = ev.excluded || {};
  const labels = copy.BLACK_HOLE_EXCLUDED_LABELS;
  const parts = [];
  // Draft and self-closed counts exist only when the snapshot recorded those fields; otherwise their zeros are not measurements.
  const counted = measured === true ? ["draft", "self_closed"] : [];
  for (const id of [...counted, "new_account", "bot", "internal", "under_14d"]) {
    if (x[id] > 0) parts.push(`${hook(`bh.excluded.${id}`, String(x[id]))} ${e(labels[id])}`);
  }
  const excluded = parts.length ? note(`${e(copy.BLACK_HOLE_EXCLUDED_LEAD)} ${parts.join(", ")}`) : "";

  const prs = (Array.isArray(ev.counted_prs) ? ev.counted_prs : []).filter((pr) => Number.isInteger(pr.number) && pr.number > 0);
  const col = copy.PROJECT_COLUMNS;
  const prRows = prs
    .map((pr, i) => {
      const url = `https://github.com/${encodeURIComponent(d.owner)}/${encodeURIComponent(d.name)}/pull/${pr.number}`;
      return (
        `<tr><th scope="row"><a href="${e(url)}" target="_blank" rel="noopener noreferrer">#${hook(`bh.counted_prs.${i}.number`, String(pr.number))}</a></th>` +
        `<td data-label="${e(col.opened)}">${pr.created_at ? hook(`bh.counted_prs.${i}.created_at`, longDate(pr.created_at)) : copy.MISSING_VALUE}</td>` +
        `<td data-label="${e(col.pr_status)}">${e(copy.blackHolePrResponseText(Boolean(pr.answered_within_14d), pr.response_kind))}</td></tr>`
      );
    })
    .join("");
  const table = prRows
    ? `<div class="table-scroll black-hole-pr-scroll" tabindex="0" role="region" aria-label="${e(copy.BLACK_HOLE_EVIDENCE_HEADING)}">` +
      `<table class="project-table black-hole-pr-table"><thead><tr><th scope="col">${e(col.pr)}</th><th scope="col">${e(col.opened)}</th><th scope="col">${e(col.pr_status)}</th></tr></thead>` +
      `<tbody>${prRows}</tbody></table></div>`
    : "";
  return (
    open +
    stat +
    wilson +
    window +
    source +
    excluded +
    rule +
    exclusions +
    (measured === false ? note(e(copy.BLACK_HOLE_RULE_INPUTS_NOT_MEASURED_TEXT)) : "") +
    table +
    recheck +
    `</section>`
  );
}

/** The radar, and under it the same five pillars as a table: score out of 100 and how much was measured. The table
 *  is the radar's text alternative. */
function pillarsBlock(rec, mode) {
  const d = rec.detail;
  if (!PILLAR_ORDER.some((p) => d[`p_${p}`] != null)) return "";
  const tag = headings(mode).section;
  const col = copy.PROJECT_COLUMNS;
  const rows = PILLAR_ORDER.map((p) => {
    const score = d[`p_${p}`];
    return (
      `<tr><th scope="row">${e(copy.PILLAR_NAMES[p])}</th>` +
      `<td class="num" data-label="${e(col.score)}">${score != null ? hook(`detail.p_${p}`, score.toFixed(1)) : copy.MISSING_VALUE}</td>` +
      `<td class="num" data-label="${e(copy.COLUMN_COVERAGE)}">${hook(`detail.c_${p}`, pct0(d[`c_${p}`] || 0))}</td></tr>`
    );
  }).join("");
  return (
    `<section class="project-card" id="pillars" aria-labelledby="pillars-h">` +
    `<${tag} class="project-h2" id="pillars-h">${e(copy.PROJECT_PILLARS_HEADING)}</${tag}>` +
    renderPillarRadar(d) +
    `<table class="project-table pillar-table"><caption>${e(copy.PROJECT_PILLARS_CAPTION)}</caption>` +
    `<thead><tr><th scope="col">${e(col.pillar)}</th><th scope="col" class="num">${e(col.score)}</th><th scope="col" class="num">${e(copy.COLUMN_COVERAGE)}</th></tr></thead>` +
    `<tbody>${rows}</tbody></table>` +
    `</section>`
  );
}

function parametersBlock(rec, mode, opts) {
  const { section, group } = headings(mode);
  const col = copy.PROJECT_COLUMNS;
  let body;
  if (!rec.evidence) {
    // The rows are still on their way, or the request failed.
    body = opts.evidenceError
      ? `<div class="error-banner" role="alert">${e(copy.drawerErrorText(opts.evidenceError.endpoint, opts.evidenceError.status, opts.evidenceError.detail))}` +
        ` <button type="button" class="icon-button" data-evidence-retry="true">${e(copy.RETRY_LABEL)}</button></div>`
      : `<p class="project-note">${e(copy.DRAWER_LOADING_TEXT)}</p>`;
  } else if (rec.evidence.length === 0) {
    body = `<p class="project-note">${e(copy.DRAWER_NO_EVIDENCE_TEXT)}</p>`;
  } else {
    const byPillar = {};
    for (const row of rec.evidence) (byPillar[row.pillar] = byPillar[row.pillar] || []).push(row);
    body = PILLAR_ORDER.filter((p) => byPillar[p])
      .map((p) => {
        const rows = byPillar[p]
          .map((r) => {
            const id = `evidence.${r.param_id}`;
            const name =
              `<th scope="row" class="param-name"><span class="param-label">${e(copy.paramName(r.param_id))}</span>` +
              `<span class="param-meaning">${e(copy.PARAM_MEANINGS[r.param_id] || "")}</span></th>`;
            if (r.unmeasured_reason) {
              return `<tr class="param-unmeasured">${name}<td colspan="5" class="project-reason">${e(copy.missingValueReason(r.unmeasured_reason))}</td></tr>`;
            }
            const hasWindow = r.window_start && r.window_end;
            const source = r.source
              ? `${e(r.method || "")}<span class="project-mono" title="${e(r.source_sha256 || "")}">${e(r.source)}</span>`
              : copy.MISSING_VALUE;
            return (
              `<tr>${name}` +
              `<td class="param-value" data-label="${e(col.value)}">${r.value != null ? hook(`${id}.value`, formatParameterValue(r.param_id, r.value)) : copy.MISSING_VALUE}</td>` +
              `<td class="num" data-label="${e(col.score)}">${r.score != null ? hook(`${id}.score`, r.score.toFixed(1)) : copy.MISSING_VALUE}</td>` +
              `<td class="num${r.n ? "" : " is-empty"}" data-label="${e(col.count)}">${r.n ? hook(`${id}.n`, fmtInt(r.n)) : copy.MISSING_VALUE}</td>` +
              `<td class="param-window${hasWindow ? "" : " is-empty"}" data-label="${e(col.window)}">${hasWindow ? hook(`${id}.window`, windowText(r.window_start, r.window_end)) : copy.MISSING_VALUE}</td>` +
              `<td class="param-source" data-label="${e(col.source)}">${source}</td></tr>`
            );
          })
          .join("");
        return (
          `<${group} class="project-h3" id="parameters-${p}">${e(copy.PILLAR_NAMES[p])}</${group}>` +
          `<table class="project-table param-table"><thead><tr><th scope="col">${e(col.parameter)}</th><th scope="col">${e(col.value)}</th>` +
          `<th scope="col" class="num">${e(col.score)}</th><th scope="col" class="num">${e(col.count)}</th><th scope="col">${e(col.window)}</th>` +
          `<th scope="col">${e(col.source)}</th></tr></thead><tbody>${rows}</tbody></table>`
        );
      })
      .join("");
  }
  return (
    `<section class="project-card" id="parameters" aria-labelledby="parameters-h">` +
    `<${section} class="project-h2" id="parameters-h">${e(copy.PROJECT_PARAMETERS_HEADING)}</${section}>` +
    body +
    `</section>`
  );
}

function personaBlock(rec, mode) {
  const d = rec.detail;
  // A project disqualified before scoring has no lens at all: every row would repeat the banner's sentence.
  if (!d.eligible) return "";
  const tag = headings(mode).section;
  const col = copy.PROJECT_COLUMNS;
  const composites = d.persona_composites || {};
  const rows = Object.keys(copy.PERSONA_NAMES)
    .map((p) => {
      const name = copy.PERSONA_NAMES[p];
      const gate = (d.persona_gates || {})[p];
      const standing = rec.standings ? rec.standings[p] : null;
      const composite = composites[p];
      if (gate) return `<tr><th scope="row">${e(name)}</th><td colspan="4" class="project-reason">${e(copy.personaGateReason(gate))}</td></tr>`;
      if (!composite) return `<tr><th scope="row">${e(name)}</th><td colspan="4" class="project-reason">${e(copy.NOT_RATED_LABEL)}</td></tr>`;
      const rank =
        standing && standing.rank !== null && standing.rank !== undefined
          ? ofTotal(standing.rank, standing.total, `standings.${p}.rank`, `standings.${p}.total`)
          : copy.MISSING_VALUE;
      return (
        `<tr><th scope="row">${e(name)}</th>` +
        `<td class="num" data-label="${e(copy.COLUMN_RANK)}">${rank}</td>` +
        `<td class="num" data-label="${e(col.score)}">${composite.porchlamp != null ? hook(`detail.persona_composites.${p}.porchlamp`, composite.porchlamp.toFixed(2)) : copy.MISSING_VALUE}</td>` +
        `<td data-label="${e(copy.COLUMN_TIER)}">${renderTierCell(composite.tier, deriveTierCeiling(composite.coverage, composite.tier_ceiling))}</td>` +
        `<td data-label="${e(col.status)}">${e(composite.rated ? copy.RATED_LABEL : copy.NOT_RATED_LABEL)}</td></tr>`
      );
    })
    .join("");
  return (
    `<section class="project-card" id="lenses" aria-labelledby="lenses-h">` +
    `<${tag} class="project-h2" id="lenses-h">${e(copy.DRAWER_SECTION_PERSONAS)}</${tag}>` +
    `<table class="project-table persona-table"><thead><tr><th scope="col">${e(col.persona)}</th><th scope="col" class="num">${e(copy.COLUMN_RANK)}</th>` +
    `<th scope="col" class="num">${e(col.score)}</th><th scope="col">${e(copy.COLUMN_TIER)}</th><th scope="col">${e(col.status)}</th></tr></thead>` +
    `<tbody>${rows}</tbody></table>` +
    `</section>`
  );
}

function integrityBlock(rec, mode, opts, ranked) {
  const d = rec.detail;
  const tag = headings(mode).section;
  const key = `${d.owner}/${d.name}`;
  const flags = d.suspicion && d.suspicion.length ? e(d.suspicion.join(", ")) : e(copy.SUSPICION_NONE_TEXT);
  const issue = copy.ISSUE_BASE + encodeURIComponent(copy.ISSUE_TITLE_PREFIX + key);
  const cite = ranked
    ? `<p class="project-cite">${fill(copy.PROJECT_CITE_LINE, {
        year: hook("cite.year", String(d.snapshot_id).slice(0, 4)),
        project: e(key),
        snapshot: hook("detail.snapshot_id", d.snapshot_id),
        url: e(`${copy.SITE_ADDRESS}${projectPath(d.owner, d.name)}`),
      })}</p>`
    : "";
  return (
    `<section class="project-card" id="evidence" aria-labelledby="evidence-h">` +
    `<${tag} class="project-h2" id="evidence-h">${e(copy.DRAWER_SECTION_INTEGRITY)}</${tag}>` +
    `<dl class="project-dl">` +
    `<div><dt>${e(copy.SNAPSHOT_ID_LABEL)}</dt><dd>${hook("detail.snapshot_id", d.snapshot_id)}</dd></div>` +
    `<div><dt>${e(copy.RAW_HASH_LABEL)}</dt><dd class="project-mono project-hash">${d.raw_hash ? hook("detail.raw_hash", d.raw_hash) : copy.MISSING_VALUE}</dd></div>` +
    `</dl>` +
    `<p>${flags}</p>` +
    (opts.recordFile
      ? `<p>${e(copy.RECORD_FILE_LEAD)} <a href="/${e(opts.recordFile)}">${e(opts.recordFile)}</a>${e(copy.RECORD_FILE_KEY)} <code class="project-mono">${e(key)}</code>.</p>`
      : "") +
    `<p>${e(copy.METHOD_LINK_LEAD)} <a href="/methodology/">${e(copy.METHOD_LINK_LABEL)}</a>.</p>` +
    `<p id="correction"><a href="${e(issue)}">${e(copy.ISSUE_LINK_LABEL)}</a>${e(copy.ISSUE_LINK_TAIL)}</p>` +
    cite +
    `</section>`
  );
}

function similarBlock(rec, mode) {
  if (!rec.similar) return "";
  const tag = headings(mode).section;
  const items = rec.similar
    .map(
      (r, i) =>
        `<li><a href="${e(projectPath(r.owner, r.name))}">${e(r.owner)}/${e(r.name)}</a> ` +
        `<span class="project-muted">${fill(copy.SIMILAR_RANK, { rank: hook(`similar.${i}.rank`, fmtInt(r.rank)) })}</span></li>`,
    )
    .join("");
  return (
    `<section class="project-card" id="similar" aria-labelledby="similar-h">` +
    `<${tag} class="project-h2" id="similar-h">${e(copy.SIMILAR_HEADING)}</${tag}>` +
    (items ? `<p class="project-note">${e(copy.SIMILAR_LEAD)}</p><ul class="similar-list">${items}</ul>` : `<p>${e(copy.SIMILAR_NONE)}</p>`) +
    `</section>`
  );
}

/**
 * renderProject(record, opts) -> the markup of one project (see the options at the top of this file).
 *  page:  h1, breadcrumb and lede; every section a plain open card, readable without script; Compare is a link.
 *  panel: h2 inside the drawer dialog; Close, and "Full page →" when the caller gives `pageHref`.
 * The layout follows the view's own width (a CSS container), so the 520 px panel and a phone get the same stacked rows.
 */
export function renderProject(rec, opts = {}) {
  const mode = opts.mode === "page" ? "page" : "panel";
  const kind = opts.kind || kindOf(rec.detail);
  const ranked = kind === "rated";
  const badge = !ranked && opts.badgeHtml ? `<section class="project-card">${opts.badgeHtml}</section>` : "";
  const body =
    headBlock(rec, mode, opts) +
    (ranked ? `<div class="project-card key-facts-card">${keyFactsBlock(rec)}</div>` + compositeBlock(rec, mode, opts) : bannerBlock(rec, kind, opts) + badge) +
    blackHoleBlock(rec, mode) +
    pillarsBlock(rec, mode) +
    parametersBlock(rec, mode, opts) +
    `<div class="project-pair">${personaBlock(rec, mode)}${integrityBlock(rec, mode, opts, ranked)}</div>` +
    similarBlock(rec, mode);
  return `<div class="project-view is-${mode}">${body}</div>`;
}
