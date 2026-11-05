// The personal lookup: its reading of one pull request, its odds, and its requests, with GitHub stood in for at the network.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { LIVE_API_BASE } from "../livecheck.js";
import { LOOKUP_PR_LIMIT, commentKind, commentLength, oddsAt, parseLogin, readPullRequest, runLookup, summarize } from "../lookup.js";
import { renderLookupPage, renderLookupResult } from "../views/lookup.js";

const cases = (name) => JSON.parse(readFileSync(new URL(`./cases/${name}.json`, import.meta.url), "utf8"));
const NOW = Date.parse("2026-11-05T12:00:00Z");
const DAY = 24 * 60 * 60 * 1000;
const ago = (days) => new Date(NOW - days * DAY).toISOString();
const SUBSTANTIVE = "Thanks for sending this, I will review the change this week.";

function item(overrides = {}) {
  return {
    number: 7, title: "Fix the thing", html_url: "https://github.com/acme/widget/pull/7", state: "open", draft: false,
    created_at: ago(20), closed_at: null, author_association: "NONE", user: { login: "Ann", type: "User" },
    repository_url: `${LIVE_API_BASE}/repos/acme/widget`, pull_request: { merged_at: null }, ...overrides,
  };
}
const said = (login, at, body = SUBSTANTIVE, association = "MEMBER", type = "User") => ({ event: "commented", user: { login, type }, created_at: at, body, author_association: association });

test("commentKind says what the engine says on every shared case", () => {
  for (const c of cases("comment_kinds")) assert.equal(commentKind(c.text), c.kind, c.text);
});

test("commentLength says what the engine says on every shared case", () => {
  for (const c of cases("comment_lengths")) assert.equal(commentLength(c.text), c.length, c.text);
});

test("a login comes from what people type, and nothing else reaches a request", () => {
  assert.equal(parseLogin("  @pctablet505 "), "pctablet505");
  assert.equal(parseLogin("https://github.com/pctablet505/"), "pctablet505");
  assert.equal(parseLogin("a/b"), null);
  assert.equal(parseLogin("-bad"), null);
  assert.equal(parseLogin("x".repeat(40)), null);
  assert.equal(parseLogin(""), null);
  assert.equal(parseLogin(null), null);
});

test("a pull request nobody answered is silent, for as long as it has waited", () => {
  const row = readPullRequest(item(), [], NOW);
  assert.equal(row.status, "silent");
  assert.equal(row.silentDays, 20);
  assert.equal(row.answerHours, null);
  assert.equal(row.group, "first");
  assert.equal(row.repo, "acme/widget");
});

test("only the project's people answer: not the author, not a bot, not a passer-by, not a one-word comment", () => {
  const events = [
    said("ann", ago(19)),
    said("mergify[bot]", ago(18)),
    said("stale", ago(18), SUBSTANTIVE, "MEMBER"),
    said("passerby", ago(17), SUBSTANTIVE, "NONE"),
    said("maya", ago(16), "LGTM"),
  ];
  assert.equal(readPullRequest(item(), events, NOW).status, "silent");
  const answered = readPullRequest(item(), [...events, said("maya", ago(15))], NOW);
  assert.equal(answered.status, "answered");
  assert.equal(answered.answerHours, 5 * 24);
});

test("a review in any state answers, by the project's people", () => {
  const review = { event: "reviewed", user: { login: "maya", type: "User" }, submitted_at: ago(19), author_association: "COLLABORATOR", state: "commented", body: "" };
  assert.equal(readPullRequest(item(), [review], NOW).answerHours, 24);
});

test("an author's own review of their pull request answers nothing, whatever GitHub calls them", () => {
  const review = { event: "reviewed", user: { login: "Ann", type: "User" }, submitted_at: ago(19), author_association: "OWNER", state: "commented", body: "" };
  assert.equal(readPullRequest(item(), [review], NOW).status, "silent");
});

test("a merge answers and says merged; a close by someone else answers and says closed; a close by the author says left", () => {
  const merged = readPullRequest(item({ state: "closed", pull_request: { merged_at: ago(18) } }), [{ event: "merged", actor: { login: "maya", type: "User" }, created_at: ago(18) }], NOW);
  assert.deepEqual([merged.status, merged.answerHours], ["merged", 48]);
  const closed = readPullRequest(item({ state: "closed" }), [{ event: "closed", actor: { login: "maya", type: "User" }, created_at: ago(17) }], NOW);
  assert.deepEqual([closed.status, closed.answerHours], ["closed", 72]);
  const left = readPullRequest(item({ state: "closed" }), [{ event: "closed", actor: { login: "ann", type: "User" }, created_at: ago(17) }], NOW);
  assert.deepEqual([left.status, left.answerHours], ["left", null]);
  const botClosed = readPullRequest(item({ state: "closed" }), [{ event: "closed", actor: { login: "stale[bot]", type: "Bot" }, created_at: ago(17) }], NOW);
  assert.equal(botClosed.status, "left", "a bot's close answers nobody");
  const repliedThenClosed = readPullRequest(item({ state: "closed" }), [said("maya", ago(19)), { event: "closed", actor: { login: "maya", type: "User" }, created_at: ago(17) }], NOW);
  assert.equal(repliedThenClosed.status, "answered", "the reply came first");
});

test("the clock of a draft starts when it was marked ready, and a draft has no clock yet", () => {
  assert.equal(readPullRequest(item({ draft: true }), [], NOW).status, "draft");
  const events = [said("maya", ago(19)), { event: "ready_for_review", created_at: ago(10) }];
  const row = readPullRequest(item(), events, NOW);
  assert.equal(row.status, "silent", "the reply came before the clock");
  assert.equal(row.silentDays, 10);
});

test("asking is the author's own request after the first hour", () => {
  const early = new Date(Date.parse(ago(20)) + 30 * 60 * 1000).toISOString();
  assert.equal(readPullRequest(item(), [said("ann", early, "Friendly ping", "NONE")], NOW).asked, false);
  assert.equal(readPullRequest(item(), [said("ann", ago(5), "Friendly ping", "NONE")], NOW).asked, true);
  assert.equal(readPullRequest(item(), [said("ann", ago(5), "Rebased on main.", "NONE")], NOW).asked, false);
});

test("a contributor's pull request reads on the returning curve", () => {
  assert.equal(readPullRequest(item({ author_association: "CONTRIBUTOR" }), [], NOW).group, "returning");
  assert.equal(readPullRequest(item({ author_association: "FIRST_TIME_CONTRIBUTOR" }), [], NOW).group, "first");
});

const CURVE = [{ days: 0, first: 0.3, returning: 0.6 }, { days: 10, first: 0.2, returning: 0.4 }, { days: 30, first: 0.1, returning: 0.2 }];

test("the odds read the curve in a straight line between its points, and say when a wait is past its end", () => {
  assert.deepEqual(oddsAt(CURVE, 0, "first"), { share: 0.3, past: false });
  assert.ok(Math.abs(oddsAt(CURVE, 5, "first").share - 0.25) < 1e-9);
  assert.ok(Math.abs(oddsAt(CURVE, 20, "returning").share - 0.3) < 1e-9);
  assert.deepEqual(oddsAt(CURVE, 30, "first"), { share: 0.1, past: false });
  assert.deepEqual(oddsAt(CURVE, 45, "first"), { share: 0.1, past: true });
});

test("silent ones come first, longest first, and the typical answer is the median", () => {
  const rows = [
    { status: "merged", answerHours: 1, silentDays: null }, { status: "silent", answerHours: null, silentDays: 3 },
    { status: "answered", answerHours: 5, silentDays: null }, { status: "silent", answerHours: null, silentDays: 9 },
  ];
  const out = summarize(rows);
  assert.deepEqual(out.rows.map((r) => r.silentDays), [9, 3, null, null]);
  assert.equal(out.silent, 2);
  assert.equal(out.typicalHours, 3);
  assert.equal(summarize([rows[1]]).typicalHours, null);
});

/** GitHub at the network: `routes` maps a URL's start to `{status, body}`; every request is recorded. */
function github(routes) {
  const seen = [];
  const fetchImpl = async (url) => {
    seen.push(String(url));
    const key = Object.keys(routes).find((start) => String(url).includes(start));
    if (!key) throw new Error(`no route for ${url}`);
    const { status = 200, body = {}, headers = {} } = typeof routes[key] === "function" ? routes[key](url) : routes[key];
    return { ok: status < 400, status, redirected: false, headers: new Map(Object.entries(headers)), json: async () => body };
  };
  return { fetchImpl, seen };
}
const quota = (core = 60, search = 10) => ({ body: { resources: { core: { remaining: core, reset: 1 }, search: { remaining: search, reset: 2 } } } });

test("a lookup asks GitHub once per pull request, skips the account's own and inside ones, and draws the odds from the curve", async () => {
  const net = github({
    "/rate_limit": quota(),
    "/search/issues": { body: { items: [item(), item({ number: 8, author_association: "MEMBER" }), item({ number: 9, author_association: "CONTRIBUTOR", repository_url: `${LIVE_API_BASE}/repos/acme/other` })] } },
    "/issues/7/timeline": { body: [] },
    "/issues/9/timeline": { body: [said("maya", ago(19))] },
  });
  const asked = [];
  const out = await runLookup("Ann", { fetchImpl: net.fetchImpl, now: new Date(NOW), curve: CURVE, projectReply: async (o, n) => (asked.push(`${o}/${n}`), 2.5) });
  assert.equal(out.ok, true);
  assert.equal(out.result.read, 2);
  assert.equal(out.result.found, 2);
  assert.equal(out.result.partial, false);
  assert.equal(net.seen.length, 4, "the free quota read, one search, two timelines");
  assert.ok(net.seen[1].includes(encodeURIComponent("type:pr author:Ann -user:Ann")));
  const silent = out.result.rows[0];
  assert.equal(silent.status, "silent");
  assert.ok(Math.abs(silent.odds.share - 0.15) < 1e-9);
  assert.equal(silent.typicalHours, 2.5);
  assert.deepEqual(asked, ["acme/widget"], "only a silent one reads its project's typical reply");
  assert.equal(out.result.rows[1].odds, undefined);
});

test("an account's later pull request to the same project reads as returning, whatever GitHub's association says", async () => {
  const older = item({ number: 1, created_at: ago(40) });
  const newer = item({ number: 2, created_at: ago(20) });
  const net = github({ "/rate_limit": quota(), "/search/issues": { body: { items: [newer, older] } }, "/timeline": { body: [] } });
  const out = await runLookup("ann", { fetchImpl: net.fetchImpl, now: new Date(NOW) });
  const group = Object.fromEntries(out.result.rows.map((row) => [row.number, row.group]));
  assert.deepEqual(group, { 1: "first", 2: "returning" });
});

test("a lookup reads at most its limit, and no more than the allowance left", async () => {
  const many = Array.from({ length: 40 }, (_, i) => item({ number: i + 100 }));
  const full = github({ "/rate_limit": quota(), "/search/issues": { body: { items: many } }, "/timeline": { body: [] } });
  assert.equal((await runLookup("ann", { fetchImpl: full.fetchImpl, now: new Date(NOW) })).result.read, LOOKUP_PR_LIMIT);
  const low = github({ "/rate_limit": quota(3), "/search/issues": { body: { items: many } }, "/timeline": { body: [] } });
  const out = await runLookup("ann", { fetchImpl: low.fetchImpl, now: new Date(NOW) });
  assert.equal(out.result.read, 3);
  assert.equal(out.result.partial, true);
});

test("a lookup stops before spending anything when the allowance is gone", async () => {
  const core = github({ "/rate_limit": quota(0) });
  assert.deepEqual(await runLookup("ann", { fetchImpl: core.fetchImpl }), { ok: false, error: { kind: "rate_limited", resetAt: 1 } });
  assert.equal(core.seen.length, 1);
  const search = github({ "/rate_limit": quota(60, 0) });
  assert.deepEqual(await runLookup("ann", { fetchImpl: search.fetchImpl }), { ok: false, error: { kind: "rate_limited", resetAt: 2 } });
});

test("an unknown account, a bad name and a dead network each say so, and a bad name asks nothing", async () => {
  const none = github({ "/rate_limit": quota(), "/search/issues": { status: 422 } });
  assert.deepEqual(await runLookup("nobody-here", { fetchImpl: none.fetchImpl }), { ok: false, error: { kind: "no_account" } });
  const bad = github({});
  assert.deepEqual(await runLookup("a/b", { fetchImpl: bad.fetchImpl }), { ok: false, error: { kind: "invalid" } });
  assert.equal(bad.seen.length, 0);
  const dead = await runLookup("ann", { fetchImpl: async () => { throw new TypeError("offline"); } });
  assert.deepEqual(dead, { ok: false, error: { kind: "network" } });
});

test("a limit hit part-way keeps what was read and says it is partial", async () => {
  const many = Array.from({ length: 8 }, (_, i) => item({ number: i + 1 }));
  const net = github({
    "/rate_limit": quota(),
    "/search/issues": { body: { items: many } },
    "/timeline": (url) => (/issues\/[1-5]\//.test(url) ? { body: [] } : { status: 403, headers: { "x-ratelimit-remaining": "0", "x-ratelimit-reset": "9" } }),
  });
  const out = await runLookup("ann", { fetchImpl: net.fetchImpl, now: new Date(NOW) });
  assert.equal(out.ok, true);
  assert.equal(out.result.read, 5);
  assert.equal(out.result.partial, true);
});

test("the page makes no request and holds the form; its result escapes what GitHub says", () => {
  const page = renderLookupPage();
  assert.ok(page.includes('data-lookup-form="true"'));
  assert.ok(!page.includes("<script"));
  const html = renderLookupResult({
    ok: true,
    result: {
      login: "<b>x</b>", read: 1, found: 1, silent: 1, typicalHours: null, partial: false,
      rows: [{ repo: "acme/widget", number: 7, title: "<img src=x onerror=alert(1)>", url: "https://github.com/acme/widget/pull/7", status: "silent", group: "first", answerHours: null, silentDays: 16.4, asked: false, odds: { share: 0.1234, past: false }, typicalHours: 0.5 }],
    },
  });
  assert.ok(!html.includes("<img"));
  assert.ok(!html.includes("<b>x</b>"));
  assert.ok(html.includes("silent for 16 days"));
  assert.ok(html.includes("about 12% were merged"));
  assert.ok(html.includes("You haven&#39;t asked on this one"));
  assert.ok(html.includes('href="/first-pull-requests/#asking"'));
  assert.ok(html.includes("30 minutes"));
  assert.ok(html.includes('href="/projects/acme/widget/"'));
});

test("a wait too short to ask about, and a wait where the author asked, say nothing about asking or say so", () => {
  const row = { repo: "a/b", number: 1, title: "t", url: "https://github.com/a/b/pull/1", status: "silent", group: "returning", answerHours: null, silentDays: 2, asked: false, odds: null, typicalHours: null };
  const result = (r) => renderLookupResult({ ok: true, result: { login: "x", read: 1, found: 1, silent: 1, typicalHours: null, partial: false, rows: [r] } });
  assert.ok(!result(row).includes("asked on this one"));
  assert.ok(result({ ...row, silentDays: 9, asked: true }).includes("You&#39;ve asked on this one."));
  assert.ok(result({ ...row, odds: { share: 0.05, past: true } }).includes("Past two months of silence, 5% or fewer"));
});

test("errors read in words, with the reset time when GitHub gave one", () => {
  assert.ok(renderLookupResult({ ok: false, error: { kind: "no_account" } }).includes("no account by that name"));
  assert.ok(renderLookupResult({ ok: false, error: { kind: "rate_limited", resetAt: null } }).includes("within the hour"));
  assert.ok(renderLookupResult({ ok: false, error: { kind: "rate_limited", resetAt: 1790000000 } }).includes("resets at"));
  assert.ok(renderLookupResult({ ok: false, error: { kind: "unavailable" } }).includes("couldn't answer"));
});
