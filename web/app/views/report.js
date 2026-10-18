// views/report.js — the report page (/black-holes/), drawn from report.json alone.
//
// Pure: `renderReport(report, { sourceDbSha256 })` returns the <article>, so the Node prerender and the app draw
// the same bytes. Every printed number is a report.json display string inside `<span data-v="<id>">` (components/
// fill.js `hook`), the hook scripts/verify_static_export.py reads the written page back by. The words are copy.js's;
// a sentence that carries a number, or a claim the numbers decide, is a template in report.json that the view
// fills. The view types no number and words no claim.
//
// `shellShape` / `renderShell` are what the app calls for the /black-holes/ route: loading, error, or the body.

import { escapeHtml } from "../escape.js";
import { fill, hook } from "../components/fill.js";
import * as copy from "../copy.js";

const SLOT = /\{([a-z][a-z0-9_]*)\}/g;

/** `template` filled from `values`, each slot a hook of its own id. `links` turns a slot into a link; a slot that
 *  `values` lacks is an error (fill.js), so a template and its values cannot drift apart unseen. */
function fillValues(template, values, links = {}) {
  const slots = {};
  for (const [, id] of String(template).matchAll(SLOT)) {
    if (!Object.prototype.hasOwnProperty.call(values, id)) throw new Error(`report.json has no value for the slot {${id}}`);
    const inner = hook(id, values[id]);
    slots[id] = links[id] ? `<a href="${escapeHtml(links[id])}">${inner}</a>` : inner;
  }
  return fill(template, slots);
}

/** The axis: whole tens of percent, from 0 to the first ten at or above the largest share. Drawn from data. */
export function axisFor(rows) {
  const top = Math.max(...rows.map((r) => r.black_holes / r.judged));
  const steps = Math.max(1, Math.ceil(top * 10 - 1e-9));
  return { max: steps / 10, ticks: Array.from({ length: steps + 1 }, (_, i) => i * 10) };
}

/** A bar chart that is also its own data table: one row per group, real numbers in real cells (the text
 *  alternative), the bar and the axis hidden from assistive technology because the cells already say it.
 *  `name(row, i)` is the row's first-cell markup (hooked); `rowLink(row)` makes it a link. */
function barTable({ rows, path, firstCol, caption, name, rowLink }) {
  const axis = axisFor(rows);
  const tickMarks = axis.ticks
    .map((t) => `<span class="axis-tick" style="left:${(t / (axis.max * 100)) * 100}%">${t}%</span>`)
    .join("");
  const body = rows
    .map((r, i) => {
      const f = (r.black_holes / r.judged) / axis.max;
      const label = rowLink ? `<a href="${escapeHtml(rowLink(r))}">${name(r, i)}</a>` : name(r, i);
      return (
        `<tr role="row">` +
        `<th scope="row" role="rowheader" class="bar-label">${label}</th>` +
        `<td role="cell" class="num" data-label="${escapeHtml(copy.REPORT_COL_JUDGED)}">${hook(`${path}.${i}.judged`, r.judged_text)}</td>` +
        `<td role="cell" class="num" data-label="${escapeHtml(copy.REPORT_COL_BLACK_HOLES)}">${hook(`${path}.${i}.black_holes`, r.black_holes_text)}</td>` +
        `<td role="cell" class="bar-cell" style="--f:${f.toFixed(4)}">` +
        `<span class="bar-scale" style="--ticks:${axis.ticks.length - 1}"><span class="bar" aria-hidden="true"></span></span>` +
        `<span class="bar-value">${hook(`${path}.${i}.share`, r.share_text)}%</span>` +
        `</td>` +
        `</tr>`
      );
    })
    .join("");
  return (
    `<div class="bar-table-wrap"><table class="bar-table" role="table">` +
    `<caption class="sr-only">${escapeHtml(caption)}</caption>` +
    `<thead role="rowgroup"><tr role="row">` +
    `<th scope="col" role="columnheader">${escapeHtml(firstCol)}</th>` +
    `<th scope="col" role="columnheader" class="num">${escapeHtml(copy.REPORT_COL_JUDGED)}</th>` +
    `<th scope="col" role="columnheader" class="num">${escapeHtml(copy.REPORT_COL_BLACK_HOLES)}</th>` +
    `<th scope="col" role="columnheader" class="bar-head">${escapeHtml(copy.REPORT_COL_SHARE)}` +
    `<span class="axis" aria-hidden="true"><span class="bar-scale axis-scale">${tickMarks}</span></span></th>` +
    `</tr></thead><tbody role="rowgroup">${body}</tbody></table></div>`
  );
}

/** The <article>. `sourceDbSha256` is the manifest's digest (api.fetchManifest); null where there is no manifest
 *  (the dev API), and then the hashes part that prints it is left out. */
export function renderReport(report, { sourceDbSha256 = null } = {}) {
  const v = report.values;
  const s = report.sentences;
  const [lead, ...findings] = report.findings;
  const share = (report.counts.black_holes / report.counts.judged) * 100;
  const hashes = s.hashes
    .filter((part) => sourceDbSha256 || !part.includes("{source_db_sha256}"))
    .map((part) => fillValues(part, { ...v, source_db_sha256: sourceDbSha256 }))
    .join(" ");
  return (
    `<article class="report-page" aria-labelledby="report-title">` +
    // ---- Lead: the question, then the answer as the page's largest figure, with one chart.
    `<header class="report-head">` +
    `<h1 class="view-heading" id="report-title">${escapeHtml(copy.REPORT_H1)}</h1>` +
    `<p class="report-lede">${fillValues(s.lede, v)}</p>` +
    `<p class="report-intro">${fillValues(s.intro, v)}</p>` +
    `</header>` +
    `<section class="report-lead" id="${escapeHtml(lead.id)}" aria-labelledby="${escapeHtml(lead.id)}-lead">` +
    `<p class="report-figure" id="${escapeHtml(lead.id)}-lead">${fillValues(lead.lead, v)}</p>` +
    `<p class="report-figure-text">${fillValues(lead.text, v)}</p>` +
    `<div class="share-bar" aria-hidden="true">` +
    `<div class="share-track"><span class="share-fill" style="width:${share.toFixed(2)}%"></span></div>` +
    `<div class="share-legend">` +
    `<span class="share-key share-key-fill"><span class="share-swatch"></span>${hook("black_holes", v.black_holes)} ${escapeHtml(copy.REPORT_COL_BLACK_HOLES.toLowerCase())}</span>` +
    `<span class="share-key"><span class="share-swatch share-swatch-track"></span>${hook("judged", v.judged)} ${escapeHtml(copy.REPORT_COL_JUDGED.toLowerCase())}</span>` +
    `</div></div>` +
    `</section>` +
    // ---- The other four findings.
    `<section class="report-section" aria-labelledby="key-findings">` +
    `<h2 class="report-h2" id="key-findings">${escapeHtml(copy.REPORT_FINDINGS_HEADING)}</h2>` +
    `<div class="finding-grid">` +
    findings
      .map(
        (f) =>
          `<div class="finding" id="${escapeHtml(f.id)}"><h3 class="finding-lead">${fillValues(f.lead, v)}</h3>` +
          `<p>${fillValues(f.text, v)}</p></div>`
      )
      .join("") +
    `</div></section>` +
    // ---- Stars chart.
    `<section class="report-section" aria-labelledby="stars">` +
    `<h2 class="report-h2" id="stars">${escapeHtml(copy.REPORT_STARS_HEADING)}</h2>` +
    `<p class="report-answer">${fillValues(s.stars_answer, v)}</p>` +
    barTable({
      rows: report.bands,
      path: "bands",
      firstCol: copy.REPORT_COL_STARS,
      caption: s.stars_chart_summary,
      name: (r, i) => fill(copy.REPORT_BAND_ROW, { band: hook(`bands.${i}.label`, r.label) }),
    }) +
    (s.stars_not_charted ? `<p class="report-note">${fillValues(s.stars_not_charted, v)}</p>` : "") +
    `<p class="report-caveat">${escapeHtml(copy.REPORT_STARS_CAVEAT)}</p>` +
    `</section>` +
    // ---- Ecosystem chart.
    `<section class="report-section" aria-labelledby="ecosystems">` +
    `<h2 class="report-h2" id="ecosystems">${escapeHtml(copy.REPORT_ECOSYSTEMS_HEADING)}</h2>` +
    `<p class="report-answer">${fillValues(s.ecosystems_answer, v)} ${escapeHtml(copy.REPORT_ECOSYSTEMS_WITHIN)}</p>` +
    barTable({
      rows: report.ecosystems,
      path: "ecosystems",
      firstCol: copy.REPORT_COL_ECOSYSTEM,
      caption: s.ecosystems_chart_summary,
      name: (r, i) => hook(`ecosystems.${i}.name`, r.name),
      rowLink: (r) => r.path,
    }) +
    `<p class="report-caveat">${escapeHtml(copy.REPORT_ECOSYSTEMS_CAVEAT)}</p>` +
    `</section>` +
    // ---- Method and limits, side by side on a wide screen.
    `<div class="report-pair">` +
    `<section class="report-section" aria-labelledby="how-we-measured">` +
    `<h2 class="report-h2" id="how-we-measured">${escapeHtml(copy.REPORT_METHOD_HEADING)}</h2><ul class="report-list">` +
    s.method.map((t) => `<li>${fillValues(t, v)}</li>`).join("") +
    `<li>${escapeHtml(copy.REPORT_METHOD_EXCLUSIONS)}</li>` +
    `<li>${escapeHtml(copy.REPORT_METHOD_LINK_LEAD)} <a href="/methodology/">${escapeHtml(copy.REPORT_METHOD_LINK_LABEL)}</a>.</li>` +
    `</ul></section>` +
    `<section class="report-section" aria-labelledby="what-this-is-not">` +
    `<h2 class="report-h2" id="what-this-is-not">${escapeHtml(copy.REPORT_LIMITS_HEADING)}</h2><ul class="report-list">` +
    copy.LIMITS.map((t) => `<li>${escapeHtml(t)}</li>`).join("") +
    `<li>${escapeHtml(copy.REPORT_NAMES_NO_PROJECT_LEAD)} <a href="/">${escapeHtml(copy.REPORT_RANKINGS_LINK_LABEL)}</a>.</li>` +
    `</ul></section>` +
    `</div>` +
    // ---- Data and citation.
    `<div class="report-pair">` +
    `<section class="report-section" aria-labelledby="download">` +
    `<h2 class="report-h2" id="download">${escapeHtml(copy.REPORT_DOWNLOAD_HEADING)}</h2>` +
    `<p>${fillValues(s.download, v, { csv_name: report.urls.csv })}</p>` +
    `<p class="report-note">${escapeHtml(copy.REPORT_JSON_NOTE)}</p>` +
    `</section>` +
    `<section class="report-section" aria-labelledby="cite">` +
    `<h2 class="report-h2" id="cite">${escapeHtml(copy.REPORT_CITE_HEADING)}</h2>` +
    `<p class="report-cite">${fillValues(s.cite, v, { frozen_url: report.urls.frozen })}</p>` +
    `<p class="report-hashes">${hashes}</p>` +
    `</section>` +
    `</div>` +
    `</article>`
  );
}

/** Which shell the app has drawn: it rebuilds the page only when this changes. The digest arrives by a separate fetch
 *  (the manifest) and adds the hashes part the same way. */
export function shellShape(state) {
  if (state.reportLoading && !state.report) return "loading";
  if (state.reportError && !state.report) return "error";
  if (!state.report) return "empty";
  return `body:${state.sourceDbSha256 ? "db" : "nodb"}`;
}

export function renderShell(state) {
  const shape = shellShape(state);
  const heading = `<h1 class="view-heading">${escapeHtml(copy.REPORT_H1)}</h1>`;
  if (shape === "loading") return heading + `<div class="empty-state" aria-busy="true">${escapeHtml(copy.REPORT_LOADING_TEXT)}</div>`;
  if (shape === "error") {
    return (
      heading +
      `<div class="error-banner" role="alert">${escapeHtml(copy.reportErrorText(state.reportError.endpoint, state.reportError.status, state.reportError.detail))}` +
      ` <button type="button" class="icon-button" data-report-retry="true">${escapeHtml(copy.RETRY_LABEL)}</button></div>`
    );
  }
  if (shape === "empty") return heading;
  return renderReport(state.report, { sourceDbSha256: state.sourceDbSha256 || null });
}
