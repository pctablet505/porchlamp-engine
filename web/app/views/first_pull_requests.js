// views/first_pull_requests.js — the first-pull-requests report (/first-pull-requests/), drawn from
// first-pull-requests.json alone.
//
// Pure: `renderFirstPullRequests(report)` returns the <article>, and web/prerender.mjs writes it into the page at export.
// The app does not load there; first-pull-requests-page.js adds the slider and the hover text, and the page reads whole
// without it. Every printed number is a first-pull-requests.json display string inside `<span data-v="<id>">`
// (components/fill.js `hook`), which scripts/verify_static_export.py reads back. A sentence that carries a number, or a
// claim the numbers decide, is a template in the file; the other words are copy.js's. The charts are drawn from the rows:
// positions in percent, so the text keeps its size at any width.

import * as copy from "../copy.js";
import { hook } from "../components/fill.js";
import { escapeHtml as e } from "../escape.js";
import { fillValues } from "./report.js";

/** The waiting curve's reading the page opens on, before the reader moves the slider. */
export const OPENING_DAYS = 14;

/** `template` with each `{slot}` replaced by `values[slot]`, as text. */
function words(template, values) {
  return template.replace(/\{([a-z_]+)\}/g, (_slot, key) => {
    if (!Object.prototype.hasOwnProperty.call(values, key)) throw new Error(`no value for the slot {${key}}`);
    return values[key];
  });
}

const at = (fraction) => `${(fraction * 100).toFixed(2)}%`;

/** The first multiple of `step` at or above `top`. */
export function roundUp(top, step) {
  return Math.ceil(top / step - 1e-9) * step;
}

/** The waiting curve's x (0 to 1): days on a straight line. */
export function curveX(days, lastDay) {
  return days / lastDay;
}

/** The answered-by curve's x (0 to 1): hours on a log scale, from the first mark to the last. */
export function answeredX(hours, firstHour, lastHour) {
  return (Math.log(hours) - Math.log(firstHour)) / (Math.log(lastHour) - Math.log(firstHour));
}

/** A line chart: a stretched SVG for the lines, and HTML in percent for what has to keep its size. `series` is
 *  `[{cls, points: [{x, y, tip, i}]}]`, x and y from 0 to 1, y up; `ticks` the axis labels. */
function lineChart({ label, series, yTicks, xTicks, guide = null, names = [] }) {
  const lines = series
    .map((s) => {
      const points = s.points.map((p) => `${(p.x * 100).toFixed(2)},${((1 - p.y) * 100).toFixed(2)}`).join(" ");
      return `<polyline class="fpr-line fpr-line-${s.cls}" points="${points}" vector-effect="non-scaling-stroke"></polyline>`;
    })
    .join("");
  const grid = yTicks
    .map((t) => `<span class="fpr-grid${t.y === 0 ? " fpr-grid-axis" : ""}" style="top:${at(1 - t.y)}"></span><span class="fpr-tick-y" style="top:${at(1 - t.y)}">${e(t.label)}</span>`)
    .join("");
  const ticks = xTicks
    .map((t) => `<span class="fpr-tick-x${t.wide ? " fpr-wide" : ""}${t.x === 0 ? " fpr-tick-start" : t.x === 1 ? " fpr-tick-end" : ""}" style="left:${at(t.x)}">${e(t.label)}</span>`)
    .join("");
  const dots = series
    .flatMap((s) =>
      s.points.map(
        (p) =>
          `<span class="fpr-dot fpr-dot-${s.cls}" style="left:${at(p.x)};top:${at(1 - p.y)}"` +
          `${p.i === undefined ? "" : ` data-fpr-i="${p.i}"`} data-tip="${e(p.tip)}"></span>`
      )
    )
    .join("");
  const line = guide === null ? "" : `<span class="fpr-guide" data-fpr-guide="true" style="left:${at(guide)}"></span>`;
  const named = names
    .map((n) => `<span class="fpr-line-label fpr-key fpr-key-${n.cls}" style="left:${n.left};top:${n.top};transform:${n.transform}">${e(n.text)}</span>`)
    .join("");
  return (
    `<figure class="fpr-chart" role="img" aria-label="${e(label)}"><div class="fpr-plot">${grid}` +
    `<svg class="fpr-lines" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">${lines}</svg>` +
    `${line}${dots}${ticks}${named}</div></figure>`
  );
}

/** Where the two curves name their lines: in the plot's empty space, one above both lines and one below, for the shape these
 *  curves have. The browser test pins them clear of every dot, line and tick on the report's record. */
const WAITING_NAMES = Object.freeze([
  { cls: "returning", text: copy.FIRST_PRS_LINE_RETURNING, left: "8%", top: "5%", transform: "translate(0, -50%)" },
  { cls: "first", text: copy.FIRST_PRS_LINE_FIRST, left: "36%", top: "94%", transform: "translate(-50%, -50%)" },
]);
const ANSWERED_NAMES = Object.freeze([
  { cls: "returning", text: copy.FIRST_PRS_LINE_RETURNING, left: "30%", top: "42%", transform: "translate(-50%, -50%)" },
  { cls: "first", text: copy.FIRST_PRS_LINE_FIRST, left: "100%", top: "90%", transform: "translate(-100%, -50%)" },
]);

/** The answered curve keeps its dot at two months but not the label: "2 mo" beside "3 mo" touches at every width under 860 px. */
const NO_TICK_HOURS = 1440;

/** The waiting curve, its slider and its readout. Without a script the slider stays hidden and the opening reading stands. */
function waitingCurve(report) {
  const rows = report.curve;
  const opening = rows.findIndex((r) => r.days === OPENING_DAYS);
  if (opening < 0) throw new Error(`first-pull-requests.json has no reading at ${OPENING_DAYS} days`);
  const last = rows[rows.length - 1].days;
  const top = roundUp(Math.max(...rows.map((r) => r.returning)), 0.2);
  const point = (r, i, key, name) => ({
    x: curveX(r.days, last), y: r[key] / top, i,
    tip: words(copy.FIRST_PRS_TIP_CURVE, { days: r.label, group: name, share: r[`${key}_text`], count: r[`${key}_n_text`] }),
  });
  const yTicks = [];
  for (let v = 0; v <= top + 1e-9; v += 0.2) yTicks.push({ y: v / top, label: `${Math.round(v * 100)}%` });
  const xTicks = copy.FIRST_PRS_CURVE_TICKS.map(([days, label, wide]) => ({ x: curveX(days, last), label, wide }));
  const list = (key) => e(rows.map((r) => r[key]).join("|"));
  const said = rows.map((r) => words(copy.FIRST_PRS_SAID, { days: r.label, first: r.first_text, returning: r.returning_text }));
  return (
    `<h3 class="fpr-h3">${e(copy.FIRST_PRS_CURVE_HEADING)}</h3>` +
    `<div class="fpr-read" data-fpr-curve="true" data-labels="${list("label")}" data-first="${list("first_text")}" data-returning="${list("returning_text")}" data-said="${e(said.join("|"))}" ` +
    `data-x="${e(rows.map((r) => at(curveX(r.days, last))).join("|"))}">` +
    `<div class="fpr-slide" data-fpr-slide="true" hidden>` +
    `<label for="fpr-days">${e(copy.FIRST_PRS_SLIDER_LABEL)} <b data-fpr-label="true">${e(rows[opening].label)}</b></label>` +
    `<input id="fpr-days" type="range" min="0" max="${rows.length - 1}" step="1" value="${opening}" aria-valuetext="${e(said[opening])}">` +
    `</div>` +
    `<p class="fpr-silent-for" data-fpr-silent-for="true">${e(copy.FIRST_PRS_SLIDER_LABEL)} <b>${e(rows[opening].label)}</b></p>` +
    `<div class="fpr-figure"><b data-fpr-first="true">${hook(`curve.${opening}.first_text`, rows[opening].first_text)}</b><span class="fpr-key fpr-key-first">${e(copy.FIRST_PRS_READOUT_FIRST_MERGED)}</span></div>` +
    `<div class="fpr-figure"><b data-fpr-returning="true">${hook(`curve.${opening}.returning_text`, rows[opening].returning_text)}</b><span class="fpr-key fpr-key-returning">${e(copy.FIRST_PRS_READOUT_RETURNING_MERGED)}</span></div>` +
    `</div>` +
    lineChart({
      label: copy.FIRST_PRS_CURVE_LABEL,
      guide: curveX(rows[opening].days, last),
      series: [
        { cls: "returning", points: rows.map((r, i) => point(r, i, "returning", copy.FIRST_PRS_READOUT_RETURNING)) },
        { cls: "first", points: rows.map((r, i) => point(r, i, "first", copy.FIRST_PRS_READOUT_FIRST)) },
      ],
      yTicks,
      xTicks,
      names: WAITING_NAMES,
    })
  );
}

function answeredCurve(report) {
  const rows = report.answered;
  const [low, high] = [rows[0].hours, rows[rows.length - 1].hours];
  const point = (r, key, name) => ({
    x: answeredX(r.hours, low, high), y: r[key],
    tip: words(copy.FIRST_PRS_TIP_ANSWERED, { when: r.label, group: name, share: r[`${key}_text`] }),
  });
  return lineChart({
    label: copy.FIRST_PRS_ANSWERED_LABEL,
    series: [
      { cls: "returning", points: rows.map((r) => point(r, "returning", copy.FIRST_PRS_READOUT_RETURNING)) },
      { cls: "first", points: rows.map((r) => point(r, "first", copy.FIRST_PRS_READOUT_FIRST)) },
    ],
    yTicks: [0, 0.25, 0.5, 0.75, 1].map((y) => ({ y, label: `${y * 100}%` })),
    xTicks: rows.filter((r) => r.hours !== NO_TICK_HOURS).map((r) => ({ x: answeredX(r.hours, low, high), label: r.label, wide: !copy.FIRST_PRS_ANSWERED_NARROW_TICKS.includes(r.hours) })),
    names: ANSWERED_NAMES,
  });
}

/** Where a gap sits on the range chart's axis (0 to 1), for an axis in whole fives of points around zero. */
export function rangeAxis(rows) {
  const low = Math.floor(Math.min(0, ...rows.map((r) => r.low)) / 0.05 + 1e-9) * 0.05;
  const high = roundUp(Math.max(...rows.map((r) => r.high)), 0.05);
  const ticks = [];
  for (let v = low; v <= high + 1e-9; v += 0.05) ticks.push(Math.round(v * 100));
  return { low, high, ticks, x: (v) => (v - low) / (high - low) };
}

function askingChart(rows) {
  const axis = rangeAxis(rows);
  const grid = axis.ticks
    .map((t) => {
      const x = at(axis.x(t / 100));
      return `<span class="fpr-range-grid${t === 0 ? " fpr-grid-axis" : ""}" style="left:${x}"></span><span class="fpr-range-tick" style="left:${x}">${t > 0 ? `+${t}` : String(t).replace("-", "−")}</span>`;
    })
    .join("");
  const body = rows
    .map((r, i) => {
      const tip = words(copy.FIRST_PRS_TIP_ASKING, { label: r.label, count: r.count_text, projects: r.projects_text });
      return (
        `<div class="fpr-range-row" data-tip="${e(tip)}">` +
        `<p class="fpr-range-label"><b>${hook(`asking.${i}.gap_text`, r.gap_text)} ${e(copy.FIRST_PRS_POINTS)}</b> ${e(r.label)}` +
        ` <span class="fpr-range-span">(${hook(`asking.${i}.low_text`, r.low_text)} ${e(copy.FIRST_PRS_TO)} ${hook(`asking.${i}.high_text`, r.high_text)})</span></p>` +
        `<div class="fpr-range-track" aria-hidden="true"><span class="fpr-range-line" style="left:${at(axis.x(r.low))};width:${at(axis.x(r.high) - axis.x(r.low))}"></span>` +
        `<span class="fpr-range-dot${r.gap > 0.01 ? "" : " fpr-range-dot-none"}" style="left:${at(axis.x(r.gap))}"></span></div>` +
        `</div>`
      );
    })
    .join("");
  return `<div class="fpr-range">${grid}${body}</div><div class="fpr-range-axis" aria-hidden="true"></div>`;
}

/** Columns in one row: each its value, its bar, its label. `rows` is `[{value, height (0 to 1), off, label, tip, wideValue, wideLabel}]`. */
function columns(rows, name, label = null) {
  const said = label === null ? "" : ` role="img" aria-label="${e(label)}"`;
  return (
    `<div class="fpr-cols fpr-cols-${name}"${said} style="--cols:${rows.length}">` +
    rows
      .map(
        (r) =>
          `<div class="fpr-col" data-tip="${e(r.tip)}">` +
          `<span class="fpr-col-value${r.wideValue ? " fpr-wide" : ""}">${r.value}</span>` +
          `<span class="fpr-col-bar${r.off ? " fpr-col-bar-off" : ""}" style="--f:${r.height.toFixed(4)}"></span>` +
          `<span class="fpr-col-label${r.wideLabel ? " fpr-hide-narrow" : ""}">${e(r.label)}</span>` +
          `</div>`
      )
      .join("") +
    `</div>`
  );
}

/** The share answered and the share merged in the typical project, side by side on one scale, by who answers there. */
function bandCharts(rows) {
  const top = roundUp(Math.max(...rows.flatMap((b) => [b.answered, b.merged])), 0.2);
  const chart = (key, heading) =>
    `<div class="fpr-pair-item"><h3 class="fpr-h3">${e(heading)}</h3>` +
    columns(
      rows.map((b, i) => ({
        value: hook(`who_bands.${i}.${key}_text`, b[`${key}_text`]), height: b[key] / top, off: !b.top, label: b.label,
        tip: words(copy.FIRST_PRS_TIP_BAND, { band: b.label, share: b[`${key}_text`], count: b.projects_text }),
      })),
      "band"
    ) +
    `</div>`;
  return (
    `<div class="fpr-pair">${chart("answered", copy.FIRST_PRS_BAND_ANSWERED)}${chart("merged", copy.FIRST_PRS_BAND_MERGED)}</div>` +
    `<p class="fpr-axis-title">${e(copy.FIRST_PRS_HIST_AXIS)}</p>`
  );
}

function table(head, rows, label) {
  return (
    `<div class="fpr-table-wrap"><table class="fpr-table" style="--nums:${head.length - 1}"><caption class="sr-only">${e(label)}</caption><thead><tr>` +
    head.map((h, i) => `<th scope="col"${i ? ' class="num"' : ""}>${e(h)}</th>`).join("") +
    `</tr></thead><tbody>${rows.join("")}</tbody></table></div>`
  );
}

/** A list item whose words up to the first full stop are its lead, set bold. */
function leadItem(template, values) {
  const stop = template.indexOf(". ");
  if (stop < 0) throw new Error(`a list item needs a lead sentence: ${template}`);
  return `<li><b>${e(template.slice(0, stop + 1))}</b> ${fillValues(template.slice(stop + 2), values)}</li>`;
}

function section(id, kicker, heading, body) {
  return (
    `<section class="report-section fpr-section" id="${id}" aria-labelledby="${id}-heading">` +
    `<p class="fpr-kicker">${e(kicker)}</p><h2 class="report-h2 fpr-h2" id="${id}-heading">${heading}</h2>${body}</section>`
  );
}

/** The <article>. */
export function renderFirstPullRequests(report) {
  const v = report.values;
  const s = report.sentences;
  const say = (template) => fillValues(template, v);
  const para = (template, cls = "") => `<p${cls ? ` class="${cls}"` : ""}>${say(template)}</p>`;
  const topWeek = roundUp(Math.max(...report.weekdays.map((d) => d.share)), 0.2);
  const topHist = Math.max(...report.histogram.map((b) => b.projects));
  const tiles = s.tiles
    .map(
      (t) =>
        `<div class="fpr-tile"><p class="fpr-tile-label">${e(t.label)}</p><p class="fpr-tile-value">${hook(t.value, v[t.value])}</p>` +
        `<p class="fpr-tile-text">${say(t.text)}</p></div>`
    )
    .join("");
  return (
    `<article class="report-page fpr-page" aria-labelledby="fpr-title">` +
    `<header class="report-head fpr-head">` +
    `<p class="fpr-kicker">${e(copy.FIRST_PRS_EYEBROW)} · ${say(copy.FIRST_PRS_DATA_THROUGH)}</p>` +
    `<h1 class="view-heading fpr-h1" id="fpr-title">${say(s.h1)}</h1>` +
    `<p class="report-lede fpr-lede">${say(s.lede)}</p>` +
    `</header>` +
    section(
      "who-answers", copy.FIRST_PRS_WHO_KICKER, say(s.who_heading),
      `<h3 class="fpr-h3">${e(copy.FIRST_PRS_HIST_HEADING)}</h3>` +
        columns(
          report.histogram.map((b, i) => ({
            value: hook(`histogram.${i}.projects_text`, b.projects_text), height: b.projects / topHist, off: !b.top, label: b.label,
            wideValue: !b.top, wideLabel: !b.top && i % 3 !== 0,
            tip: words(copy.FIRST_PRS_TIP_HISTOGRAM, { band: b.label, count: b.projects_text }),
          })),
          "hist",
          words(copy.FIRST_PRS_HIST_LABEL, { bands: report.histogram.map((b) => words(copy.FIRST_PRS_HIST_BAND, { band: b.label, count: b.projects_text })).join(", ") })
        ) +
        `<p class="fpr-axis-title">${e(copy.FIRST_PRS_HIST_AXIS)}</p>` + para(s.who_text) + para(s.who_note, "report-note")
    ) +
    `<div class="fpr-tiles">${tiles}</div>` +
    section(
      "newcomers-do-best", copy.FIRST_PRS_BEST_KICKER, say(s.best_heading),
      para(s.best_text) + bandCharts(report.who_bands) + para(s.control_text) + para(s.best_note, "report-note") +
        `<p class="report-note">${e(copy.FIRST_PRS_BEST_CAVEAT)}</p>`
    ) +
    section(
      "waiting", copy.FIRST_PRS_WAIT_KICKER, say(s.wait_heading),
      para(s.wait_text) + para(s.late_text) + waitingCurve(report) + para(s.wait_note, "report-note") +
        `<p class="fpr-next"><a href="${e(copy.FIRST_PRS_LOOKUP_HREF)}">${e(copy.FIRST_PRS_LOOKUP_LINK)}</a></p>`
    ) +
    section(
      "asking", copy.FIRST_PRS_ASK_KICKER, say(s.ask_heading),
      para(s.ask_text) + `<h3 class="fpr-h3">${e(copy.FIRST_PRS_ASK_CHART_HEADING)}</h3>` + askingChart(report.asking) +
        para(s.ask_note, "report-note") + para(s.merges_text) + `<p class="report-note">${e(copy.FIRST_PRS_ASK_CAVEAT)}</p>`
    ) +
    section(
      "first-one", copy.FIRST_PRS_FIRST_KICKER, say(s.first_heading),
      para(s.first_text) + `<h3 class="fpr-h3">${e(copy.FIRST_PRS_ANSWERED_HEADING)}</h3>` + answeredCurve(report) + para(s.first_note, "report-note")
    ) +
    section(
      "weekends", copy.FIRST_PRS_WEEK_KICKER, say(s.week_heading),
      para(s.week_text) + `<h3 class="fpr-h3">${e(copy.FIRST_PRS_WEEK_CHART_HEADING)}</h3>` +
        columns(
          report.weekdays.map((d, i) => ({
            value: hook(`weekdays.${i}.share_text`, d.share_text), height: d.share / topWeek, off: d.weekend, label: d.day,
            tip: words(copy.FIRST_PRS_TIP_WEEKDAY, { day: d.day, share: d.share_text, count: d.count_text }),
          })),
          "week"
        ) +
        para(s.week_note, "report-note")
    ) +
    section(
      "where-to-start", copy.FIRST_PRS_FAST_KICKER, say(s.fast_heading),
      para(s.fast_text) +
        table(
          copy.FIRST_PRS_PROJECT_COLUMNS,
          report.projects.map(
            (p, i) =>
              `<tr><th scope="row"><a href="${e(p.path)}">${e(p.name)}</a><span class="fpr-what">${e(p.what)}</span></th>` +
              `<td class="num">${hook(`projects.${i}.median_text`, p.median_text)}</td><td class="num">${hook(`projects.${i}.within_a_day_text`, p.within_a_day_text)}</td>` +
              `<td class="num">${hook(`projects.${i}.merged_text`, p.merged_text)}</td><td class="num">${hook(`projects.${i}.count_text`, p.count_text)}</td></tr>`
          ),
          copy.FIRST_PRS_PROJECTS_CAPTION
        )
    ) +
    section(
      "all-numbers", copy.FIRST_PRS_NUMBERS_KICKER, e(copy.FIRST_PRS_NUMBERS_HEADING),
      `<p>${e(copy.FIRST_PRS_NUMBERS_TEXT)}</p>` +
        table(
          copy.FIRST_PRS_CURVE_COLUMNS,
          report.curve.map(
            (r, i) =>
              `<tr><th scope="row">${e(r.label)}</th><td class="num">${hook(`curve.${i}.first_exact`, r.first_exact)}</td><td class="num">${hook(`curve.${i}.first_n_text`, r.first_n_text)}</td>` +
              `<td class="num">${hook(`curve.${i}.returning_exact`, r.returning_exact)}</td><td class="num">${hook(`curve.${i}.returning_n_text`, r.returning_n_text)}</td></tr>`
          ),
          copy.FIRST_PRS_CURVE_CAPTION
        ) +
        table(
          copy.FIRST_PRS_ANSWERED_COLUMNS,
          report.answered.map(
            (r, i) =>
              `<tr><th scope="row">${e(r.label)}</th><td class="num">${hook(`answered.${i}.first_text`, r.first_text)}</td><td class="num">${hook(`answered.${i}.returning_text`, r.returning_text)}</td></tr>`
          ),
          copy.FIRST_PRS_ANSWERED_CAPTION
        )
    ) +
    `<div class="report-pair">` +
    `<section class="report-section" aria-labelledby="how-measured"><h2 class="report-h2" id="how-measured">${e(copy.FIRST_PRS_METHOD_HEADING)}</h2>` +
    `<ul class="report-list">${s.method.map((t) => leadItem(t, v)).join("")}</ul></section>` +
    `<section class="report-section" aria-labelledby="not-covered"><h2 class="report-h2" id="not-covered">${e(copy.FIRST_PRS_NOT_COVERED_HEADING)}</h2>` +
    `<ul class="report-list">${s.not_covered.map((t) => leadItem(t, v)).join("")}</ul></section></div>` +
    `<section class="report-section" aria-labelledby="cite"><h2 class="report-h2" id="cite">${e(copy.FIRST_PRS_CITE_HEADING)}</h2>` +
    `<p class="report-cite">${say(s.cite)}</p>` +
    `<p class="fpr-next"><a href="${e(report.data_file)}">${e(copy.FIRST_PRS_DATA_LABEL)}</a></p></section>` +
    `<div class="fpr-tip" id="fpr-tip" role="presentation" hidden></div>` +
    `</article>`
  );
}
