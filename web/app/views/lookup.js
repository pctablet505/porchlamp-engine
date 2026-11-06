// views/lookup.js — the personal lookup's page (/lookup/) and its result.
//
// Pure: `renderLookupPage()` is the <article> web/prerender.mjs draws at export, and `renderLookupResult(outcome)` is what
// lookup-page.js draws into it after a lookup. Everything GitHub returns is untrusted text (a pull request's title is written
// by whoever opened it): every value passes through `escapeHtml`.

import * as copy from "../copy.js";
import { fill } from "../components/fill.js";
import { escapeHtml as e } from "../escape.js";
import { LOOKUP_PR_LIMIT } from "../lookup.js";
import { percentWhole, replyTimeShort } from "./project.js";

/** The silence the report measures asking at. */
const ASKING_FROM_DAYS = 14;

const text = (template, values) => fill(template, Object.fromEntries(Object.entries(values).map(([k, v]) => [k, e(String(v))])));

/** The page: what it does, the form, an empty place for the result, and what it can and cannot see. */
export function renderLookupPage() {
  return (
    `<article class="report-page lookup-page" aria-labelledby="lookup-title">` +
    `<header class="report-head fpr-head">` +
    `<h1 class="view-heading fpr-h1" id="lookup-title">${e(copy.LOOKUP_H1)}</h1>` +
    `<p class="report-lede fpr-lede">${e(copy.LOOKUP_LEDE)}</p>` +
    `</header>` +
    `<section class="report-section" aria-label="${e(copy.LOOKUP_FORM_LABEL)}">` +
    `<form class="lookup-form" data-lookup-form="true" autocomplete="off">` +
    `<label for="lookup-login">${e(copy.LOOKUP_INPUT_LABEL)}</label>` +
    `<div class="lookup-row"><input class="control" id="lookup-login" name="login" type="text" spellcheck="false" autocapitalize="none" placeholder="${e(copy.LOOKUP_INPUT_PLACEHOLDER)}" required>` +
    `<button class="lookup-button" type="submit">${e(copy.LOOKUP_BUTTON)}</button></div>` +
    `</form>` +
    `<p class="report-note lookup-cost">${text(copy.LOOKUP_COST, { limit: LOOKUP_PR_LIMIT })}</p>` +
    `<noscript><p class="report-note">${e(copy.LOOKUP_NO_SCRIPT)}</p></noscript>` +
    `</section>` +
    `<div id="lookup-result" class="lookup-result" aria-live="polite"></div>` +
    `<section class="report-section" aria-labelledby="lookup-reads"><h2 class="report-h2" id="lookup-reads">${e(copy.LOOKUP_READS_HEADING)}</h2>` +
    `<ul class="report-list">${copy.LOOKUP_READS.map((line) => `<li>${e(line)}</li>`).join("")}</ul>` +
    `<p class="fpr-next"><a href="${e(copy.LOOKUP_REPORT_HREF)}">${e(copy.LOOKUP_REPORT_LINK)}</a></p></section>` +
    `</article>`
  );
}

export function renderLookupLoading() {
  return `<div class="empty-state" aria-busy="true">${e(copy.LIVE_CHECK_LOADING_TEXT)}</div>`;
}

function errorText(error) {
  if (error.kind === "rate_limited" && error.resetAt) {
    const clock = new Date(error.resetAt * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return text(copy.LOOKUP_ERRORS.rate_limited_until, { clock });
  }
  return copy.LOOKUP_ERRORS[error.kind] || copy.LOOKUP_ERRORS.http;
}

const projectHref = (repo) => `/projects/${repo.toLowerCase()}/`;

function statusLine(row) {
  const time = row.answerHours === null ? "" : replyTimeShort(row.answerHours);
  switch (row.status) {
    case "silent": {
      const days = Math.floor(row.silentDays);
      if (days < 1) return e(copy.LOOKUP_STATUS.silent_today);
      return days === 1 ? e(copy.LOOKUP_STATUS.silent_one) : text(copy.LOOKUP_STATUS.silent, { days });
    }
    case "merged": return row.answerHours === null ? e(copy.LOOKUP_STATUS.merged_plain) : text(copy.LOOKUP_STATUS.merged, { time });
    case "answered": return text(copy.LOOKUP_STATUS.answered, { time });
    case "closed": return text(copy.LOOKUP_STATUS.closed, { time });
    case "left": return e(copy.LOOKUP_STATUS.left);
    case "closed_quiet": return e(copy.LOOKUP_STATUS.closed_quiet);
    default: return e(copy.LOOKUP_STATUS.draft);
  }
}

function rowHtml(row) {
  const extra = [];
  if (row.status === "silent") {
    if (row.odds) {
      const template = row.group === "first" ? (row.odds.past ? copy.LOOKUP_ODDS_FIRST_PAST : copy.LOOKUP_ODDS_FIRST) : (row.odds.past ? copy.LOOKUP_ODDS_RETURNING_PAST : copy.LOOKUP_ODDS_RETURNING);
      extra.push(text(template, { share: percentWhole(Number(row.odds.share.toFixed(4))) }));
    }
    if (row.asked) extra.push(e(copy.LOOKUP_ASKED));
    else if (row.silentDays >= ASKING_FROM_DAYS) extra.push(e(copy.LOOKUP_NOT_ASKED));
    if (row.typicalHours !== null && row.typicalHours !== undefined) {
      extra.push(fill(copy.LOOKUP_PROJECT_TYPICAL, { time: e(replyTimeShort(row.typicalHours)), link: `<a href="${e(projectHref(row.repo))}">${e(copy.LOOKUP_PROJECT_LINK)}</a>` }));
    }
  }
  return (
    `<li class="lookup-item lookup-${e(row.status)}">` +
    `<p class="lookup-repo"><a href="${e(row.url)}" rel="noopener noreferrer nofollow">${e(row.repo)} #${e(String(row.number))}</a></p>` +
    `<p class="lookup-title-text">${e(row.title)}</p>` +
    `<p class="lookup-status">${statusLine(row)}</p>` +
    extra.map((line) => `<p class="lookup-extra">${line}</p>`).join("") +
    `</li>`
  );
}

/** The result of a lookup: `outcome` is what lookup.js `runLookup` returned. */
export function renderLookupResult(outcome) {
  if (!outcome.ok) return `<div class="error-banner" role="alert">${errorText(outcome.error)}</div>`;
  const r = outcome.result;
  if (!r.rows.length) return `<section class="report-section"><p>${text(copy.LOOKUP_NOTHING, { login: r.login })}</p></section>`;
  const parts = [text(r.read === 1 ? copy.LOOKUP_SUMMARY_ONE : copy.LOOKUP_SUMMARY, { login: r.login, count: r.read })];
  parts.push(r.silent ? text(copy.LOOKUP_SUMMARY_SILENT, { count: r.silent }) : e(copy.LOOKUP_SUMMARY_NONE_SILENT));
  const unasked = r.rows.some((row) => row.status === "silent" && !row.asked && row.silentDays >= ASKING_FROM_DAYS);
  return (
    `<section class="report-section" aria-labelledby="lookup-summary">` +
    `<h2 class="report-h2" id="lookup-summary">${parts.join(" ")}</h2>` +
    (r.typicalHours === null ? "" : `<div class="project-number"><p class="project-number-label">${e(copy.LOOKUP_TYPICAL_LABEL)}</p><p class="project-number-value">${e(replyTimeShort(r.typicalHours))}</p></div>`) +
    (r.partial ? `<p class="report-note">${text(copy.LOOKUP_PARTIAL, { read: r.read, found: Math.min(r.found, LOOKUP_PR_LIMIT) })}</p>` : "") +
    (unasked ? `<p class="report-note">${fill(copy.LOOKUP_ASKING_NOTE, { link: `<a href="${e(copy.LOOKUP_ASKING_HREF)}">${e(copy.LOOKUP_NOT_ASKED_LINK)}</a>` })}</p>` : "") +
    `<ol class="lookup-list">${r.rows.map((row) => rowHtml(row)).join("")}</ol>` +
    `</section>`
  );
}
