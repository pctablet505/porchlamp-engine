// lookup.js — the personal lookup (/lookup/): one GitHub account's recent pull requests to other people's projects, each
// placed on the report's waiting curve (first-pull-requests.json).
//
// When a visitor presses the button, THEIR browser asks GitHub's API: one search, then one timeline per pull request. No
// server, no token, no write, nothing stored; it spends the visitor's own unauthenticated allowance, read first with the
// free `GET /rate_limit`. Nothing runs on load, so the site still makes no external request on its own (06 §8 item 10).
//
// The reply rule is D-IN-2's as far as one pull request can show it (porchlamp.core.gates.first_substantive_response), and
// the rule constants below mirror the engine's, each named next to its Python source; tests/test_lookup_port.py fails if one
// drifts, and web/app/tests/cases/comment_kinds.json pins `commentKind` for both sides. It cannot see what takes a whole
// project's record: who holds access without GitHub saying so, and which replies follow an automated template. The page
// says so. Every function except `runLookup` is pure.

import { EXTERNAL_ASSOCIATIONS, GITHUB_ADDRESS_RE, LIVE_API_BASE, OWNER_RE, githubGet, isBotActor } from "./livecheck.js";

//: Pull requests read per lookup, one timeline each, and the search page they are picked from (some are the account's own).
export const LOOKUP_PR_LIMIT = 20;
export const LOOKUP_SEARCH_PAGE = 50;
//: Timelines read at once, so a burst does not trip GitHub's secondary limit.
const LOOKUP_BATCH = 5;
//: Timeline events per request (GitHub's maximum page): events arrive oldest first, so the first answer is on the first page.
const LOOKUP_TIMELINE_PAGE = 100;
const LOOKUP_TIMEOUT_MS = 8000;

//: porchlamp.core.gates.MAINTAINER_ASSOCIATIONS.
export const MAINTAINER_ASSOCIATIONS = Object.freeze(["COLLABORATOR", "MEMBER", "OWNER"]);
//: porchlamp.core.constants.SUBSTANTIVE_COMMENT_MIN_CHARS and SUBSTANTIVE_COMMENT_CJK_WEIGHT.
export const SUBSTANTIVE_COMMENT_MIN_CHARS = 40;
export const SUBSTANTIVE_COMMENT_CJK_WEIGHT = 3;
//: porchlamp.snapshot.first_pull_requests.ASK_AFTER_HOURS.
export const ASK_AFTER_HOURS = 1;
//: porchlamp.snapshot.first_pull_requests.TOOL_COMMENT_RE.
export const TOOL_COMMENT_RE = /(^\s*\/|^\s*i have read|^\s*i hereby|^\s*recheck\b|^\s*rerun\b|^\s*retest\b|@(coderabbit(ai)?|copilot|dependabot|renovate|claassistant|sourcery(-ai)?|codecov|gemini(-code-assist)?|cursor|greptile(ai)?|claude|codex)\b|automated review|review pass|ensemble review|^\s*\W*\s*automated|^\s*(thanks|thank you|thx)\b)/i;
//: porchlamp.snapshot.first_pull_requests.ASK_COMMENT_RE.
export const ASK_COMMENT_RE = /(\bping(ing)?\b|\bbump(ing)?\b|\bptal\b|any (update|updates|news|chance|interest|thoughts|feedback)\b|status (on|of) this|gentle (reminder|follow|nudge)|friendly (reminder|nudge)|please (review|take a look|have a look|merge)|(can|could|would) (someone|anyone|somebody|you|a maintainer)\b.{0,40}(review|look|merge|check|approve|help)|(can|could) i get\b|(get|got) this (reviewed|merged)|take a look|have a look|when you (have|get) (a|the) (moment|chance|time|window)|what('s| is) (the )?(status|next)|still (waiting|relevant)|what do you think)/i;
//: porchlamp.snapshot.first_pull_requests.MENTION_RE.
export const MENTION_RE = /(^|\s)@[a-z0-9][a-z0-9-]+/i;

//: Associations that can read as a first pull request on the curve: GitHub shows no earlier contribution to the project.
const FIRST_ASSOCIATIONS = new Set(["FIRST_TIMER", "FIRST_TIME_CONTRIBUTOR", "NONE"]);
const OUTSIDE = new Set(EXTERNAL_ASSOCIATIONS);
const MAINTAINERS = new Set(MAINTAINER_ASSOCIATIONS);
const HOUR_MS = 60 * 60 * 1000;
const DAY_MS = 24 * HOUR_MS;
//: porchlamp.core.gates._CJK_LETTERS.
const CJK_LETTERS = /[\u1100-\u11ff\u3040-\u30ff\u3130-\u318f\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\uff66-\uff9f\u{20000}-\u{3134f}]/gu;

/** A GitHub login from what a visitor typed ("@name", a profile address), or null. Nothing else reaches a request URL. */
export function parseLogin(text) {
  if (typeof text !== "string" || text.length > 256) return null;
  const login = text.trim().replace(GITHUB_ADDRESS_RE, "").replace(/^@/, "").replace(/\/+$/, "");
  return OWNER_RE.test(login) ? login : null;
}

/** porchlamp.core.gates.comment_length. */
export function commentLength(text) {
  return [...text].length + (SUBSTANTIVE_COMMENT_CJK_WEIGHT - 1) * (text.match(CJK_LETTERS) || []).length;
}

/** porchlamp.snapshot.first_pull_requests.comment_kind: what an author's comment on their own pull request does. */
export function commentKind(text) {
  if (typeof text !== "string" || !text || TOOL_COMMENT_RE.test(text)) return "tool";
  if (ASK_COMMENT_RE.test(text)) return "ask";
  return MENTION_RE.test(text) ? "mention" : "other";
}

const when = (text) => {
  const ms = typeof text === "string" ? Date.parse(text) : NaN;
  return Number.isNaN(ms) ? null : ms;
};

function isOwnPeople(event, author) {
  const person = event.user || event.actor || {};
  const login = String(person.login || "").toLowerCase();
  if (!login || login === author || isBotActor({ type: person.type, login })) return false;
  return MAINTAINERS.has(String(event.author_association || "").toUpperCase());
}

/**
 * One pull request from its search result and its timeline, at `nowMs`. `status` is "draft", "merged", "answered" (open or
 * closed by its author after an answer), "closed" (closed by the project with no earlier answer), "left" (closed by its
 * author with none), "closed_quiet" (closed with none, by a bot or by nobody the timeline shows) or "silent"; `answerHours` the wait for the first answer, `silentDays` how long a silent one has waited.
 * `sentBefore` says the account's search results hold an earlier one to the same project, which makes this one returning.
 */
export function readPullRequest(item, events, nowMs, { sentBefore = false } = {}) {
  const author = String((item.user && item.user.login) || "").toLowerCase();
  const created = when(item.created_at);
  const repo = String(item.repository_url || "").split("/repos/")[1] || "";
  const base = { repo, number: item.number, title: String(item.title || ""), url: String(item.html_url || ""), group: !sentBefore && FIRST_ASSOCIATIONS.has(String(item.author_association || "").toUpperCase()) ? "first" : "returning" };
  let ready = null;
  let closedByProject = null;
  let closedByAuthor = false;
  const answers = [];
  const authorComments = [];
  for (const event of events) {
    if (!event || typeof event !== "object") continue;
    const at = when(event.created_at || event.submitted_at);
    if (at === null) continue;
    if (event.event === "ready_for_review") ready = ready === null ? at : Math.max(ready, at);
    else if (event.event === "reviewed" && isOwnPeople(event, author)) answers.push(at);
    else if (event.event === "commented") {
      const login = String(((event.user || event.actor || {}).login) || "").toLowerCase();
      if (login === author) authorComments.push({ at, body: event.body });
      else if (isOwnPeople(event, author) && commentLength(String(event.body || "")) >= SUBSTANTIVE_COMMENT_MIN_CHARS) answers.push(at);
    } else if (event.event === "merged") answers.push(at);
    else if (event.event === "closed") {
      const actor = event.actor || {};
      const login = String(actor.login || "").toLowerCase();
      closedByAuthor = login === author;
      if (login && login !== author && !isBotActor({ type: actor.type, login })) {
        closedByProject = at;
        answers.push(at);
      }
    }
  }
  const merged = when(item.pull_request && item.pull_request.merged_at);
  if (merged !== null) answers.push(merged);
  if (item.draft === true) return { ...base, status: item.state === "closed" ? "closed_quiet" : "draft", answerHours: null, silentDays: null, asked: false };
  const clock = ready === null ? created : Math.max(created, ready);
  const first = answers.filter((at) => at >= clock).sort((a, b) => a - b)[0];
  const answerHours = first === undefined ? null : (first - clock) / HOUR_MS;
  const asked = authorComments.some((c) => (c.at - clock) / HOUR_MS > ASK_AFTER_HOURS && commentKind(c.body) === "ask");
  let status;
  if (merged !== null) status = "merged";
  else if (answerHours !== null) status = item.state === "closed" && first === closedByProject ? "closed" : "answered";
  else if (item.state !== "closed") status = "silent";
  else status = closedByAuthor ? "left" : "closed_quiet";
  return { ...base, status, answerHours, silentDays: status === "silent" ? (nowMs - clock) / DAY_MS : null, asked };
}

/**
 * The share merged after `days` of silence, from the report's waiting curve (`rows` of `{days, first, returning}`), read in
 * a straight line between its points: `{share, past}`, `past` when the wait is longer than the curve's last point.
 */
export function oddsAt(rows, days, group) {
  const key = group === "first" ? "first" : "returning";
  const last = rows[rows.length - 1];
  if (days >= last.days) return { share: last[key], past: days > last.days };
  for (let i = 1; i < rows.length; i += 1) {
    if (days <= rows[i].days) {
      const [a, b] = [rows[i - 1], rows[i]];
      return { share: a[key] + ((b[key] - a[key]) * (days - a.days)) / (b.days - a.days), past: false };
    }
  }
  return { share: last[key], past: false };
}

/** The median of a non-empty list. */
function median(values) {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/** The rows in reading order, silent first and longest first, then the rest as GitHub gave them, with their counts. */
export function summarize(rows) {
  const silent = rows.filter((r) => r.status === "silent").sort((a, b) => b.silentDays - a.silentDays);
  const answered = rows.map((r) => r.answerHours).filter((h) => h !== null);
  return {
    rows: [...silent, ...rows.filter((r) => r.status !== "silent")],
    silent: silent.length,
    typicalHours: answered.length ? median(answered) : null,
  };
}

/**
 * One lookup: `{ ok: true, result: { login, rows, silent, typicalHours, read, found, skipped } }` or `{ ok: false, error }`
 * (livecheck.js `githubGet`'s kinds, plus "invalid" and "no_account"). Never throws. `curve` is the report's waiting curve,
 * or null. `projectReply(owner, name)` answers a project's typical first reply from the site's own files (`{hours}` or null).
 */
export async function runLookup(text, { fetchImpl, now = new Date(), curve = null, projectReply = async () => null } = {}) {
  const login = parseLogin(text);
  if (!login) return { ok: false, error: { kind: "invalid" } };
  const fetchFn = fetchImpl !== undefined ? fetchImpl : typeof fetch === "function" ? fetch : null;
  if (typeof fetchFn !== "function") return { ok: false, error: { kind: "network" } };
  const get = (url) => githubGet(fetchFn, url, LOOKUP_TIMEOUT_MS);
  try {
    let allowance = LOOKUP_PR_LIMIT;
    const quota = await get(`${LIVE_API_BASE}/rate_limit`);
    if (quota.ok) {
      const resources = quota.data && quota.data.resources;
      const [core, search] = [resources && resources.core, resources && resources.search];
      if (search && search.remaining === 0) return { ok: false, error: { kind: "rate_limited", resetAt: search.reset ?? null } };
      if (core && typeof core.remaining === "number") {
        if (core.remaining < 1) return { ok: false, error: { kind: "rate_limited", resetAt: core.reset ?? null } };
        allowance = Math.min(LOOKUP_PR_LIMIT, core.remaining);
      }
    } else if (quota.error.kind === "network" || quota.error.kind === "timeout") {
      return { ok: false, error: quota.error };
    }
    const query = encodeURIComponent(`type:pr author:${login} -user:${login}`);
    const found = await get(`${LIVE_API_BASE}/search/issues?q=${query}&sort=created&order=desc&per_page=${LOOKUP_SEARCH_PAGE}`);
    if (!found.ok) {
      // GitHub answers a search for an account that does not exist as an invalid query.
      if (found.error.kind === "http" && found.error.status === 422) return { ok: false, error: { kind: "no_account" } };
      return { ok: false, error: found.error };
    }
    const items = (Array.isArray(found.data && found.data.items) ? found.data.items : []).filter((item) => OUTSIDE.has(String(item.author_association || "").toUpperCase()));
    const picked = items.slice(0, allowance);
    const earliest = new Map();
    for (const item of items) {
      const at = when(item.created_at);
      if (at !== null && (!earliest.has(item.repository_url) || at < earliest.get(item.repository_url))) earliest.set(item.repository_url, at);
    }
    const rows = [];
    let limited = null;
    let failed = null;
    for (let i = 0; i < picked.length && !limited; i += LOOKUP_BATCH) {
      const batch = picked.slice(i, i + LOOKUP_BATCH);
      const answers = await Promise.all(
        batch.map((item) => {
          const repo = String(item.repository_url || "").split("/repos/")[1] || "";
          return get(`${LIVE_API_BASE}/repos/${repo.split("/").map(encodeURIComponent).join("/")}/issues/${Number(item.number)}/timeline?per_page=${LOOKUP_TIMELINE_PAGE}`);
        })
      );
      answers.forEach((answer, j) => {
        if (answer.ok && Array.isArray(answer.data)) rows.push(readPullRequest(batch[j], answer.data, now.getTime(), { sentBefore: when(batch[j].created_at) > earliest.get(batch[j].repository_url) }));
        else if (!answer.ok && answer.error.kind === "rate_limited") limited = answer.error;
        else if (!answer.ok) failed = failed || answer.error;
      });
    }
    if (!rows.length && items.length) return { ok: false, error: limited || failed || { kind: "http" } };
    const totals = summarize(rows);
    for (const row of totals.rows) {
      if (row.status !== "silent") continue;
      const [owner, name] = row.repo.split("/");
      row.odds = curve ? oddsAt(curve, row.silentDays, row.group) : null;
      row.typicalHours = (await projectReply(owner, name)) ?? null;
    }
    return { ok: true, result: { login, ...totals, read: rows.length, found: items.length, partial: rows.length < Math.min(items.length, LOOKUP_PR_LIMIT) } };
  } catch {
    return { ok: false, error: { kind: "network" } };
  }
}
