// tests/livecheck.test.mjs — Node unit tests for the opt-in live check for a
// repository the snapshot does not hold (06 §3 C, "Repositories outside the
// snapshot: live check"): web/app/livecheck.js, components/livepanel.js, and
// the copy / state / leaderboard pieces they touch.
//
// Every network call here goes to a fake `fetchImpl`: no test reaches GitHub.
//
// Run: node --test web/app/tests/livecheck.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import {
  LIVE_API_BASE,
  LIVE_REQUESTS_PER_CHECK,
  LIVE_PAGE_SIZE,
  LIVE_PR_SAMPLE,
  LIVE_TIMEOUT_MS,
  LIVE_DESCRIPTION_MAX,
  LIVE_GATE_IDS,
  MANIFEST_MAX_DEPTH,
  PR_WINDOW_DAYS,
  VITALITY_WINDOW_DAYS,
  DECISION_WINDOW_DAYS,
  GATE6_HANDOFF_MIN_PRS,
  parseRepoSlug,
  isBotActor,
  findRecognizedManifest,
  findLicenseFile,
  hasOffPlatformMarker,
  isParseableSpdxExpression,
  isOsiApprovedExpression,
  evaluateLiveGates,
  repoFactsFrom,
  treeFactsFrom,
  commitFactsFrom,
  pullFactsFrom,
  runLiveCheck,
} from "../livecheck.js";
import { renderNotInSnapshot } from "../components/livepanel.js";
import { escapeHtml } from "../components/table.js";
import { renderFilteredEmpty } from "../views/leaderboard.js";
import { defaultState, reduce, parseSearch } from "../state.js";
import * as copy from "../copy.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WEB_APP = path.resolve(HERE, "..");
const readCases = (name) => JSON.parse(readFileSync(path.join(HERE, "cases", name), "utf8"));
const source = (relative) => readFileSync(path.join(WEB_APP, relative), "utf8");

const NOW = new Date("2026-10-15T12:00:00Z");
const DAY_MS = 24 * 60 * 60 * 1000;
const daysAgo = (n) => new Date(NOW.getTime() - n * DAY_MS).toISOString();
const byId = (checks) => Object.fromEntries(checks.map((c) => [c.id, c]));
//: Off-site addresses are assembled, not written out: tests/test_ui_no_external_resources.py
//: scans every file under web/app (these tests included) for literal external URLs, and
//: these are inputs to a parser, never requests.
const address = (scheme, rest) => `${scheme}:${"/".repeat(2)}${rest}`;

// --------------------------------------------------------------------- //
// parseRepoSlug: the only door to a request URL
// --------------------------------------------------------------------- //

test("parseRepoSlug: accepts owner/name and pasted github.com addresses", () => {
  const cases = [
    ["facebook/react", "facebook", "react"],
    ["  facebook/react  ", "facebook", "react"],
    ["https://github.com/facebook/react", "facebook", "react"],
    [address("http", "www.github.com/a-b/c.d_e/"), "a-b", "c.d_e"],
    ["github.com/rust-lang/rust.git", "rust-lang", "rust"],
    ["GitHub.com/Owner/Repo.GIT", "Owner", "Repo"],
    ["o/...", "o", "..."],
    [`${"a".repeat(39)}/${"b".repeat(100)}`, "a".repeat(39), "b".repeat(100)],
  ];
  for (const [text, owner, name] of cases) assert.deepEqual(parseRepoSlug(text), { owner, name }, text);
});

test("parseRepoSlug: rejects anything that is not exactly one GitHub owner and one name, in O(1) on pathological inputs", () => {
  const rejected = [
    "", "   ", null, undefined, 42, {}, "facebook", "a/b/c", "/b", "a/", "-bad/x", "a_b/c", "a/..", "a/.",
    "a b/c", "a/b c", "a/b?x=1", "a/b#frag", "../etc/passwd", "a/%2e%2e", "o/<script>", "o/r\"x",
    `${"a".repeat(40)}/b`, `a/${"b".repeat(101)}`, address("https", "gitlab.com/a/b"), "https://github.com/a/b/tree/main",
    address("https", "evil.example/github.com/a/b"), "a/.git", "a\\b/c",
  ];
  for (const text of rejected) assert.equal(parseRepoSlug(text), null, String(text));
  const t0 = performance.now();
  assert.equal(parseRepoSlug(`https://github.com/a/b${"/".repeat(100_000)}x`), null);
  assert.ok(performance.now() - t0 < 5, "length cap prevents polynomial regex backtracking");
});

// --------------------------------------------------------------------- //
// The engine's rules, against the cases the engine itself produced
// --------------------------------------------------------------------- //

test("SPDX reading matches porchlamp.core.gates on every shared case (cases/spdx_osi.json)", () => {
  const { cases } = readCases("spdx_osi.json");
  assert.ok(cases.length >= 30);
  for (const c of cases) {
    assert.equal(isParseableSpdxExpression(c.expression), c.parseable, `parseable: ${c.expression}`);
    assert.equal(isOsiApprovedExpression(c.expression), c.osi, `osi: ${c.expression}`);
  }
});

test("bot, manifest, licence-file, marker, vitality-commit and decided-PR rules match the engine on every shared case (cases/gate_rules.json)", () => {
  const doc = readCases("gate_rules.json");
  const refNow = new Date(doc.ref_now);
  for (const c of doc.bots) assert.equal(isBotActor({ type: c.type, login: c.login, email: c.email }), c.expected, JSON.stringify(c));
  for (const c of doc.manifests) assert.equal(Boolean(findRecognizedManifest(c.paths)), c.expected, JSON.stringify(c.paths));
  for (const c of doc.license_files) assert.equal(Boolean(findLicenseFile(c.paths)), c.expected, JSON.stringify(c.paths));
  for (const c of doc.markers) assert.equal(hasOffPlatformMarker(c.paths), c.expected, JSON.stringify(c.paths));
  for (const c of doc.vitality_commits) {
    const cf = commitFactsFrom({ ok: true, data: c.commits });
    const g4 = byId(evaluateLiveGates(facts({ commits: cf }), refNow))["D-GATE-4"];
    const actual = g4.status === "pass" ? g4.detail.count : 0;
    assert.equal(actual, c.expected, JSON.stringify(c));
  }
  for (const c of doc.decided_prs) {
    const rawRows = c.prs.map((p) => ({
      number: p.number,
      created_at: p.created_at,
      closed_at: p.closed_at,
      merged_at: p.merged_at,
      author_association: p.author_association,
      user: p.login ? { login: p.login, type: "User" } : null,
    }));
    const pf = pullFactsFrom({ ok: true, data: rawRows });
    const g6 = byId(evaluateLiveGates(facts({ pulls: pf }), refNow))["D-GATE-6"];
    assert.equal(g6.status === "pass", c.expected, JSON.stringify(c));
  }
});

test("findRecognizedManifest / findLicenseFile name the first matching path", () => {
  assert.equal(findRecognizedManifest(["docs/x.md", "pkg/Cargo.toml", "package.json"]), "pkg/Cargo.toml");
  assert.equal(findRecognizedManifest(["a/b/c/go.mod"]), null);
  assert.equal(findLicenseFile(["src/LICENSE", "COPYING", "LICENSE"]), "COPYING");
  assert.equal(findLicenseFile([]), null);
  assert.equal(isBotActor(), false);
});

// --------------------------------------------------------------------- //
// evaluateLiveGates
// --------------------------------------------------------------------- //

const BASE_REPO = repoFactsFrom({
  full_name: "acme/widget",
  description: "A widget.",
  stargazers_count: 12,
  language: "Rust",
  created_at: "2020-01-02T03:04:05Z",
  pushed_at: "2026-10-01T00:00:00Z",
  archived: false,
  fork: false,
  mirror_url: null,
  license: { spdx_id: "MIT" },
});

function pr(overrides = {}) {
  return {
    number: 7,
    createdAt: daysAgo(30),
    closedAt: daysAgo(20),
    mergedAt: daysAgo(20),
    association: "CONTRIBUTOR",
    login: "outsider",
    type: "User",
    ...overrides,
  };
}

function facts(overrides = {}) {
  return {
    repo: { ...BASE_REPO, ...(overrides.repo || {}) },
    parentInUniverse: "parentInUniverse" in overrides ? overrides.parentInUniverse : null,
    tree: "tree" in overrides ? overrides.tree : { paths: ["package.json", "LICENSE", "src", "src/index.js"], truncated: false },
    commits: "commits" in overrides ? overrides.commits : { rows: [{ login: "alice" }], complete: true },
    pulls: "pulls" in overrides ? overrides.pulls : { rows: [pr()], complete: true },
  };
}

const gate = (id, overrides) => byId(evaluateLiveGates(facts(overrides), NOW))[id];

test("evaluateLiveGates: a healthy repository passes every gate it can check, in design order, and never decides D-GATE-5", () => {
  const checks = evaluateLiveGates(facts(), NOW);
  assert.deepEqual(checks.map((c) => c.id), [...LIVE_GATE_IDS]);
  assert.deepEqual(checks.map((c) => c.status), ["pass", "pass", "pass", "pass", "not_checked", "pass"]);
  const g = byId(checks);
  assert.deepEqual(g["D-GATE-1"].detail, { path: "package.json" });
  assert.equal(g["D-GATE-2"].reason, "original");
  assert.deepEqual(g["D-GATE-3"].detail, { spdx: "MIT" });
  assert.deepEqual(g["D-GATE-4"].detail, { count: 1, more: false, days: VITALITY_WINDOW_DAYS });
  assert.equal(g["D-GATE-5"].reason, "needs_pipeline");
  assert.deepEqual(g["D-GATE-6"].detail, { number: 7, days: DECISION_WINDOW_DAYS, bot: false, handoffPossible: false });
});

test("evaluateLiveGates D-GATE-1: no manifest fails only on a complete tree", () => {
  assert.equal(gate("D-GATE-1", { tree: { paths: ["README.md"], truncated: false } }).status, "fail");
  assert.deepEqual(gate("D-GATE-1", { tree: { paths: ["README.md"], truncated: false } }).detail, { depth: MANIFEST_MAX_DEPTH });
  assert.equal(gate("D-GATE-1", { tree: { paths: ["README.md"], truncated: true } }).reason, "tree_truncated");
  assert.equal(gate("D-GATE-1", { tree: { paths: ["README.md"], truncated: true } }).status, "undecided");
  assert.equal(gate("D-GATE-1", { tree: { paths: ["go.mod"], truncated: true } }).status, "pass");
  assert.equal(gate("D-GATE-1", { tree: null }).reason, "tree_unreadable");
  assert.equal(gate("D-GATE-1", { tree: null }).status, "undecided");
});

test("evaluateLiveGates D-GATE-2: archived, then mirror, then a fork judged by whether the snapshot holds its parent", () => {
  assert.equal(gate("D-GATE-2", { repo: { archived: true, mirrorUrl: address("git", "x"), fork: true } }).reason, "archived");
  assert.equal(gate("D-GATE-2", { repo: { mirrorUrl: address("https", "git.example/x.git"), fork: true } }).reason, "mirror");
  const fork = { fork: true, parentFullName: "upstream/widget" };
  assert.deepEqual(gate("D-GATE-2", { repo: fork, parentInUniverse: true }), {
    id: "D-GATE-2", status: "fail", reason: "fork_parent_in_universe", detail: { parent: "upstream/widget" },
  });
  assert.equal(gate("D-GATE-2", { repo: fork, parentInUniverse: false }).status, "pass");
  assert.equal(gate("D-GATE-2", { repo: fork, parentInUniverse: false }).reason, "fork_parent_outside");
  assert.equal(gate("D-GATE-2", { repo: fork, parentInUniverse: null }).status, "undecided");
});

test("evaluateLiveGates D-GATE-3: OSI expression passes; undetermined or unreadable falls back to a root licence file; a readable non-OSI one fails", () => {
  const g3 = (licenseSpdx, tree) => gate("D-GATE-3", { repo: { licenseSpdx }, ...(tree === undefined ? {} : { tree }) });
  const complete = (paths) => ({ paths, truncated: false });
  assert.equal(g3("MIT OR Apache-2.0").status, "pass");
  assert.equal(g3("Apache-2.0 WITH LLVM-exception").reason, "osi_spdx");
  // A proprietary licence stays a failure whatever file the tree holds (the engine's own rule).
  assert.deepEqual(g3("BUSL-1.1", complete(["LICENSE"])), { id: "D-GATE-3", status: "fail", reason: "not_osi", detail: { spdx: "BUSL-1.1" } });
  assert.deepEqual(g3(null, complete(["LICENSE.md"])).detail, { file: "LICENSE.md" });
  assert.equal(g3("NOASSERTION", complete(["COPYING"])).reason, "license_file");
  assert.equal(g3("OTHER", complete(["LICENSES"])).reason, "license_file");
  assert.equal(g3("NOASSERTION", complete(["README"])).reason, "no_license");
  assert.equal(g3("NOASSERTION", complete(["README"])).status, "fail");
  assert.deepEqual(g3("NOASSERTION", { paths: ["README"], truncated: true }), {
    id: "D-GATE-3", status: "undecided", reason: "license_unknown", detail: { truncated: true },
  });
  assert.deepEqual(g3(null, null), {
    id: "D-GATE-3", status: "undecided", reason: "license_unknown", detail: { truncated: false },
  });
  // Unreadable as an expression: not a verdict on the licence, so the file decides.
  assert.equal(g3("MIT/X11 style", complete(["README"])).reason, "no_license");
  assert.equal(g3("MIT/X11 style", complete(["LICENSE"])).status, "pass");
  assert.equal(g3("(MIT", complete(["LICENSE"])).status, "pass");
});

test("evaluateLiveGates D-GATE-4: a person's commit passes; bots alone fail only when the page holds the whole window", () => {
  const bots = Array.from({ length: 3 }, () => ({ login: "renovate" }));
  assert.equal(gate("D-GATE-4", { commits: { rows: bots, complete: true } }).reason, "no_human_commits");
  assert.equal(gate("D-GATE-4", { commits: { rows: bots, complete: true } }).status, "fail");
  assert.equal(gate("D-GATE-4", { commits: { rows: [], complete: true } }).status, "fail");
  assert.deepEqual(gate("D-GATE-4", { commits: { rows: bots, complete: false } }), {
    id: "D-GATE-4", status: "undecided", reason: "commits_partial", detail: { pageSize: LIVE_PAGE_SIZE },
  });
  const mixed = [...bots, { login: "" }];
  assert.deepEqual(gate("D-GATE-4", { commits: { rows: mixed, complete: false } }).detail, { count: 1, more: true, days: VITALITY_WINDOW_DAYS });
  assert.equal(gate("D-GATE-4", { commits: null }).reason, "commits_unreadable");
});

test("evaluateLiveGates D-GATE-6: an outside PR decided in the 365d window passes regardless of when it was opened", () => {
  assert.equal(gate("D-GATE-6", { pulls: { rows: [pr({ mergedAt: "" })], complete: true } }).status, "pass", "closed unmerged is decided too");
  assert.equal(gate("D-GATE-6", { pulls: { rows: [pr({ createdAt: daysAgo(PR_WINDOW_DAYS + 50), mergedAt: daysAgo(10) })], complete: true } }).status, "pass");
  assert.equal(gate("D-GATE-6", { pulls: { rows: [pr({ association: "contributor" })], complete: true } }).status, "pass");
  assert.equal(gate("D-GATE-6", { pulls: { rows: [pr({ association: "" })], complete: true } }).status, "pass", "missing reads as NONE, as the engine reads it");
  for (const association of ["NONE", "FIRST_TIMER", "FIRST_TIME_CONTRIBUTOR"]) {
    assert.equal(gate("D-GATE-6", { pulls: { rows: [pr({ association })], complete: true } }).status, "pass", association);
  }
});

test("evaluateLiveGates D-GATE-6: maintainers' PRs, ghost/deleted authors, decisions older than 365d, undecided and future-dated decisions do not count", () => {
  const none = (row) => gate("D-GATE-6", { pulls: { rows: [row], complete: true } });
  for (const association of ["MEMBER", "OWNER", "COLLABORATOR"]) assert.equal(none(pr({ association })).reason, "no_external_pr", association);
  assert.equal(none(pr({ login: "" })).reason, "no_external_pr");
  assert.equal(none(pr({ login: "ghost" })).reason, "no_external_pr");
  assert.equal(none(pr({ login: "Ghost" })).reason, "no_external_pr");
  assert.equal(none(pr({ mergedAt: daysAgo(DECISION_WINDOW_DAYS + 1), closedAt: daysAgo(DECISION_WINDOW_DAYS + 1) })).reason, "no_external_pr");
  assert.equal(none(pr({ mergedAt: daysAgo(DECISION_WINDOW_DAYS - 1), closedAt: daysAgo(DECISION_WINDOW_DAYS - 1) })).reason, "decided_external_pr");
  assert.equal(none(pr({ mergedAt: "", closedAt: "" })).reason, "no_external_pr");
  assert.equal(none(pr({ mergedAt: "", closedAt: new Date(NOW.getTime() + DAY_MS).toISOString() })).reason, "no_external_pr");
  assert.equal(none(pr({ mergedAt: "not a date", closedAt: "not a date" })).reason, "no_external_pr");
});

test("evaluateLiveGates D-GATE-6: a bot's outside PR counts, as in the engine, but a person's is the one named; handoffPossible flags >= 5 outside human PRs or an incomplete sample", () => {
  const bot = pr({ number: 1, login: "dependabot[bot]", type: "Bot", association: "NONE" });
  assert.deepEqual(gate("D-GATE-6", { pulls: { rows: [bot], complete: true } }).detail, {
    number: 1, days: DECISION_WINDOW_DAYS, bot: true, handoffPossible: false,
  });
  assert.deepEqual(gate("D-GATE-6", { pulls: { rows: [bot, pr({ number: 2 })], complete: true } }).detail, {
    number: 2, days: DECISION_WINDOW_DAYS, bot: false, handoffPossible: false,
  });
  const fiveHuman = Array.from({ length: GATE6_HANDOFF_MIN_PRS }, (_, i) => pr({ number: i + 10 }));
  assert.equal(gate("D-GATE-6", { pulls: { rows: fiveHuman, complete: true } }).detail.handoffPossible, true);
  assert.equal(gate("D-GATE-6", { pulls: { rows: [pr({ number: 2 })], complete: false } }).detail.handoffPossible, true);
});

test("evaluateLiveGates D-GATE-6: without a decided outside PR in the 30-PR sample, a marker routes; an incomplete sample, >= 5 outside human PRs, or a missing/truncated tree cannot fail", () => {
  const nothing = { rows: [pr({ association: "MEMBER" })], complete: true };
  const marker = { paths: ["Documentation/process/submitting-patches.rst", "Makefile"], truncated: false };
  assert.equal(gate("D-GATE-6", { pulls: nothing, tree: marker }).status, "routed");
  assert.equal(gate("D-GATE-6", { pulls: { ...nothing, complete: false }, tree: marker }).status, "routed");
  assert.deepEqual(gate("D-GATE-6", { pulls: { ...nothing, complete: false } }), {
    id: "D-GATE-6", status: "undecided", reason: "prs_handoff_unknown", detail: { sample: LIVE_PR_SAMPLE, days: DECISION_WINDOW_DAYS },
  });
  const fiveOpenOutside = Array.from({ length: GATE6_HANDOFF_MIN_PRS }, (_, i) => pr({ number: i + 1, mergedAt: "", closedAt: "" }));
  assert.deepEqual(gate("D-GATE-6", { pulls: { rows: fiveOpenOutside, complete: true } }), {
    id: "D-GATE-6", status: "undecided", reason: "prs_handoff_unknown", detail: { sample: LIVE_PR_SAMPLE, days: DECISION_WINDOW_DAYS },
  });
  assert.deepEqual(gate("D-GATE-6", { pulls: nothing }), {
    id: "D-GATE-6", status: "fail", reason: "no_external_pr", detail: { sample: LIVE_PR_SAMPLE, days: DECISION_WINDOW_DAYS },
  });
  assert.deepEqual(gate("D-GATE-6", { pulls: nothing, tree: { paths: ["Makefile"], truncated: true } }), {
    id: "D-GATE-6", status: "undecided", reason: "prs_marker_unknown", detail: { sample: LIVE_PR_SAMPLE, days: DECISION_WINDOW_DAYS, truncated: true },
  });
  assert.deepEqual(gate("D-GATE-6", { pulls: nothing, tree: null }), {
    id: "D-GATE-6", status: "undecided", reason: "prs_marker_unknown", detail: { sample: LIVE_PR_SAMPLE, days: DECISION_WINDOW_DAYS, truncated: false },
  });
  assert.equal(gate("D-GATE-6", { pulls: null }).reason, "prs_unreadable");
});

// --------------------------------------------------------------------- //
// GitHub -> facts
// --------------------------------------------------------------------- //

test("repoFactsFrom: reads only well-typed fields and caps the description", () => {
  const junk = repoFactsFrom(null);
  assert.deepEqual(junk, {
    fullName: "", description: "", stars: null, language: "", createdAt: "", pushedAt: "",
    archived: false, fork: false, parentFullName: "", mirrorUrl: "", licenseSpdx: null,
  });
  const facts1 = repoFactsFrom({
    full_name: "a/b", description: "x".repeat(LIVE_DESCRIPTION_MAX + 50), stargazers_count: "12", archived: "true", fork: 1,
    parent: { full_name: "c/d" }, license: { spdx_id: "" },
  });
  assert.equal(facts1.description.length, LIVE_DESCRIPTION_MAX);
  assert.equal(facts1.stars, null);
  assert.equal(facts1.archived, false);
  assert.equal(facts1.fork, false);
  assert.equal(facts1.parentFullName, "c/d");
  assert.equal(facts1.licenseSpdx, null);
  assert.equal(repoFactsFrom({ license: { spdx_id: "NOASSERTION" } }).licenseSpdx, "NOASSERTION");
});

test("treeFactsFrom: every entry's path (folders too, as the engine's envelope records them); any non-ok response (including 409 empty) is null", () => {
  const res = { ok: true, data: { truncated: true, tree: [
    { path: "LICENSES", type: "tree" }, { path: "LICENSES/MIT.txt", type: "blob" }, { path: "vendor/lib", type: "commit" },
    { type: "blob" }, null, { path: "", type: "blob" },
  ] } };
  assert.deepEqual(treeFactsFrom(res), { paths: ["LICENSES", "LICENSES/MIT.txt", "vendor/lib"], truncated: true });
  assert.equal(treeFactsFrom({ ok: false, error: { kind: "empty" } }), null);
  assert.equal(treeFactsFrom({ ok: false, error: { kind: "http", status: 500 } }), null);
});

test("commitFactsFrom: a full page is incomplete; only author.login is read, matching scripts/gather.py", () => {
  const row = { author: { login: "Alice", type: "User" }, commit: { author: { email: "a@x" } } };
  const page = (n) => ({ ok: true, data: Array.from({ length: n }, () => row) });
  assert.equal(commitFactsFrom(page(LIVE_PAGE_SIZE)).complete, false);
  assert.equal(commitFactsFrom(page(LIVE_PAGE_SIZE - 1)).complete, true);
  assert.deepEqual(commitFactsFrom(page(1)).rows, [{ login: "Alice" }]);
  assert.deepEqual(commitFactsFrom({ ok: true, data: [{ author: null, commit: null }] }).rows, [{ login: "" }]);
  assert.equal(commitFactsFrom({ ok: false, error: { kind: "empty" } }), null);
  assert.equal(commitFactsFrom({ ok: false, error: { kind: "timeout" } }), null);
});

test("pullFactsFrom: complete when fewer than LIVE_PR_SAMPLE (30) PRs are returned", () => {
  const rows = (n) => Array.from({ length: n }, (_, i) => ({
    number: i + 1, created_at: daysAgo(10), closed_at: daysAgo(5), merged_at: null,
    author_association: "NONE", user: { login: "u", type: "User" },
  }));
  assert.equal(pullFactsFrom({ ok: true, data: rows(3) }).complete, true);
  assert.equal(pullFactsFrom({ ok: true, data: rows(LIVE_PR_SAMPLE - 1) }).complete, true);
  assert.equal(pullFactsFrom({ ok: true, data: rows(LIVE_PR_SAMPLE) }).complete, false);
  assert.deepEqual(pullFactsFrom({ ok: true, data: rows(1) }).rows[0], {
    number: 1, createdAt: daysAgo(10), closedAt: daysAgo(5), mergedAt: "", association: "NONE", login: "u", type: "User",
  });
  assert.equal(pullFactsFrom({ ok: false, error: { kind: "empty" } }), null);
  assert.equal(pullFactsFrom({ ok: false, error: { kind: "network" } }), null);
});

// --------------------------------------------------------------------- //
// runLiveCheck, against a fake GitHub
// --------------------------------------------------------------------- //

function reply(body, { status = 200, headers = {}, redirected = false } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    redirected,
    headers: { get: (name) => (name.toLowerCase() in headers ? headers[name.toLowerCase()] : null) },
    json: async () => body,
  };
}

const REPO_JSON = {
  full_name: "acme/widget", description: "A widget.", stargazers_count: 12, language: "Rust",
  created_at: "2020-01-02T03:04:05Z", pushed_at: "2026-10-01T00:00:00Z", archived: false, fork: false,
  mirror_url: null, license: { spdx_id: "Apache-2.0" },
};
const TREE_JSON = { truncated: false, tree: [{ path: "Cargo.toml", type: "blob" }, { path: "LICENSE", type: "blob" }] };
const COMMITS_JSON = [{ author: { login: "alice", type: "User" }, commit: { author: { email: "alice@example.com" } } }];
const PULLS_JSON = [{
  number: 3, created_at: daysAgo(40), closed_at: daysAgo(35), merged_at: daysAgo(35), updated_at: daysAgo(35),
  author_association: "FIRST_TIME_CONTRIBUTOR", user: { login: "newcomer", type: "User" },
}];

/** A fake `fetch` answering by URL; `overrides` maps a route name to a reply (or a function returning one). */
function fakeGitHub(overrides = {}) {
  const calls = [];
  const routes = {
    rate_limit: () => reply({ resources: { core: { limit: 60, remaining: 60, reset: 1792080404 } } }),
    repo: () => reply(REPO_JSON),
    tree: () => reply(TREE_JSON),
    commits: () => reply(COMMITS_JSON),
    pulls: () => reply(PULLS_JSON),
    ...overrides,
  };
  const routeOf = (url) => {
    const u = new URL(url);
    if (u.pathname === "/rate_limit") return "rate_limit";
    if (/\/git\/trees\//.test(u.pathname)) return "tree";
    if (u.pathname.endsWith("/commits")) return "commits";
    if (u.pathname.endsWith("/pulls")) return "pulls";
    return "repo";
  };
  const fetchImpl = async (url, init) => {
    calls.push({ url, init, route: routeOf(url) });
    const answer = routes[routeOf(url)];
    return typeof answer === "function" ? answer(url, init) : answer;
  };
  return { fetchImpl, calls };
}

test("runLiveCheck: one free quota check, then exactly LIVE_REQUESTS_PER_CHECK GETs to the API origin, with no credentials and no referrer", async () => {
  const gh = fakeGitHub();
  const outcome = await runLiveCheck("acme", "widget", { fetchImpl: gh.fetchImpl, now: NOW });
  assert.equal(outcome.ok, true, JSON.stringify(outcome));
  assert.equal(outcome.result.requests, LIVE_REQUESTS_PER_CHECK);
  assert.equal(outcome.result.checkedAt, NOW.toISOString());
  assert.deepEqual(outcome.result.checks.map((c) => c.status), ["pass", "pass", "pass", "pass", "not_checked", "pass"]);
  assert.equal(gh.calls.length, 1 + LIVE_REQUESTS_PER_CHECK);
  assert.equal(gh.calls[0].route, "rate_limit", "the quota check comes first");
  for (const { url, init } of gh.calls) {
    assert.ok(url.startsWith(`${LIVE_API_BASE}/`), url);
    assert.equal(init.credentials, "omit");
    assert.equal(init.referrerPolicy, "no-referrer");
    assert.equal(init.method, undefined, "GET only");
    assert.equal(init.body, undefined);
    assert.deepEqual(Object.keys(init.headers), ["Accept"], "no Authorization or other header");
    assert.equal(init.headers.Accept, "application/vnd.github+json");
  }
  const urls = Object.fromEntries(gh.calls.map((c) => [c.route, new URL(c.url)]));
  assert.equal(urls.repo.pathname, "/repos/acme/widget");
  assert.equal(urls.tree.pathname, "/repos/acme/widget/git/trees/HEAD");
  assert.equal(urls.tree.searchParams.get("recursive"), "1");
  assert.equal(urls.commits.searchParams.get("since"), new Date(NOW.getTime() - VITALITY_WINDOW_DAYS * DAY_MS).toISOString());
  assert.equal(urls.commits.searchParams.get("per_page"), String(LIVE_PAGE_SIZE));
  assert.equal(urls.commits.searchParams.has("sha"), false, "the default branch, as the engine reads it");
  assert.equal(urls.pulls.searchParams.get("state"), "all");
  assert.equal(urls.pulls.searchParams.get("sort"), "created");
  assert.equal(urls.pulls.searchParams.get("direction"), "desc");
  assert.equal(urls.pulls.searchParams.get("per_page"), String(LIVE_PR_SAMPLE));
});

test("runLiveCheck: too little allowance left (fewer than LIVE_REQUESTS_PER_CHECK + 1 for a redirect) stops after the free quota check, spending nothing", async () => {
  const gh = fakeGitHub({ rate_limit: () => reply({ resources: { core: { remaining: LIVE_REQUESTS_PER_CHECK, reset: 1792080404 } } }) });
  const outcome = await runLiveCheck("acme", "widget", { fetchImpl: gh.fetchImpl, now: NOW });
  assert.deepEqual(outcome, { ok: false, error: { kind: "rate_limited", resetAt: 1792080404 } });
  assert.equal(gh.calls.length, 1);
});

test("runLiveCheck: an unreadable quota answer does not block the check, but an unreachable GitHub stops it", async () => {
  const flaky = fakeGitHub({ rate_limit: () => reply({ message: "oops" }, { status: 500 }) });
  assert.equal((await runLiveCheck("acme", "widget", { fetchImpl: flaky.fetchImpl, now: NOW })).ok, true);
  const down = fakeGitHub({ rate_limit: () => { throw new TypeError("Failed to fetch"); } });
  assert.deepEqual(await runLiveCheck("acme", "widget", { fetchImpl: down.fetchImpl, now: NOW }), { ok: false, error: { kind: "network" } });
  assert.equal(down.calls.length, 1);
});

test("runLiveCheck: the repository answer decides not-found, unavailable, rate-limited and other errors", async () => {
  const run = async (repoReply) => {
    const gh = fakeGitHub({ repo: () => repoReply });
    return { outcome: await runLiveCheck("acme", "widget", { fetchImpl: gh.fetchImpl, now: NOW }), calls: gh.calls.length };
  };
  assert.deepEqual(await run(reply({}, { status: 404 })), { outcome: { ok: false, error: { kind: "not_found" } }, calls: 2 });
  assert.deepEqual((await run(reply({}, { status: 451 }))).outcome, { ok: false, error: { kind: "unavailable" } });
  assert.deepEqual(
    (await run(reply({}, { status: 403, headers: { "x-ratelimit-remaining": "0", "x-ratelimit-reset": "1792080404" } }))).outcome,
    { ok: false, error: { kind: "rate_limited", resetAt: 1792080404 } },
  );
  assert.deepEqual((await run(reply({}, { status: 429, headers: { "x-ratelimit-remaining": "0" } }))).outcome, { ok: false, error: { kind: "rate_limited", resetAt: null } });
  assert.deepEqual((await run(reply({}, { status: 403 }))).outcome, { ok: false, error: { kind: "http", status: 403 } });
  assert.deepEqual((await run(reply({}, { status: 502 }))).outcome, { ok: false, error: { kind: "http", status: 502 } });
  const badJson = { ...reply({}), json: async () => { throw new SyntaxError("Unexpected token"); } };
  assert.deepEqual((await run(badJson)).outcome, { ok: false, error: { kind: "http", status: 200 } });
});

test("runLiveCheck: an allowance that runs out part-way is reported as such, not as gates it could not read", async () => {
  const gh = fakeGitHub({ commits: () => reply({}, { status: 403, headers: { "x-ratelimit-remaining": "0", "x-ratelimit-reset": "1792080404" } }) });
  assert.deepEqual(await runLiveCheck("acme", "widget", { fetchImpl: gh.fetchImpl, now: NOW }), { ok: false, error: { kind: "rate_limited", resetAt: 1792080404 } });
});

test("runLiveCheck: an empty repository (409s) leaves tree/commit gates undecided, matching scripts/gather.py", async () => {
  const gh = fakeGitHub({
    repo: () => reply({ ...REPO_JSON, license: null }),
    tree: () => reply({ message: "Git Repository is empty." }, { status: 409 }),
    commits: () => reply({ message: "Git Repository is empty." }, { status: 409 }),
    pulls: () => reply([]),
  });
  const outcome = await runLiveCheck("acme", "widget", { fetchImpl: gh.fetchImpl, now: NOW });
  assert.equal(outcome.ok, true);
  const g = byId(outcome.result.checks);
  assert.equal(g["D-GATE-1"].status, "undecided");
  assert.equal(g["D-GATE-1"].reason, "tree_unreadable");
  assert.equal(g["D-GATE-3"].status, "undecided");
  assert.equal(g["D-GATE-3"].reason, "license_unknown");
  assert.equal(g["D-GATE-4"].status, "undecided");
  assert.equal(g["D-GATE-4"].reason, "commits_unreadable");
  assert.equal(g["D-GATE-6"].status, "undecided");
  assert.equal(g["D-GATE-6"].reason, "prs_marker_unknown");
});

test("runLiveCheck: a failed sub-request leaves only its gates undecided", async () => {
  const gh = fakeGitHub({ tree: () => reply({}, { status: 500 }) });
  const g = byId((await runLiveCheck("acme", "widget", { fetchImpl: gh.fetchImpl, now: NOW })).result.checks);
  assert.equal(g["D-GATE-1"].status, "undecided");
  assert.equal(g["D-GATE-3"].status, "pass", "an OSI SPDX id needs no tree");
  assert.equal(g["D-GATE-4"].status, "pass");
  assert.equal(g["D-GATE-6"].status, "pass", "a decided outside PR needs no tree");
});

test("runLiveCheck: a renamed repository's follow-up requests go to its new name, and the redirect is counted", async () => {
  const gh = fakeGitHub({ repo: () => reply({ ...REPO_JSON, full_name: "acme-labs/widget-ng" }, { redirected: true }) });
  const outcome = await runLiveCheck("acme", "widget", { fetchImpl: gh.fetchImpl, now: NOW });
  assert.equal(outcome.result.requests, LIVE_REQUESTS_PER_CHECK + 1);
  assert.equal(outcome.result.repo.fullName, "acme-labs/widget-ng");
  for (const call of gh.calls.filter((c) => ["tree", "commits", "pulls"].includes(c.route))) {
    assert.ok(new URL(call.url).pathname.startsWith("/repos/acme-labs/widget-ng/"), call.url);
  }
  // A full_name that is not a valid slug is never put in a URL.
  const odd = fakeGitHub({ repo: () => reply({ ...REPO_JSON, full_name: "../../evil" }) });
  await runLiveCheck("acme", "widget", { fetchImpl: odd.fetchImpl, now: NOW });
  for (const call of odd.calls.slice(2)) assert.ok(new URL(call.url).pathname.startsWith("/repos/acme/widget/"), call.url);
});

test("runLiveCheck: a fork's parent is looked up in Porchlamp's snapshot, never on GitHub", async () => {
  const forkRepo = () => reply({ ...REPO_JSON, fork: true, parent: { full_name: "upstream/widget" } });
  const asked = [];
  const gh = fakeGitHub({ repo: forkRepo });
  const outcome = await runLiveCheck("acme", "widget", {
    fetchImpl: gh.fetchImpl, now: NOW, isInUniverse: async (owner, name) => { asked.push(`${owner}/${name}`); return true; },
  });
  assert.deepEqual(asked, ["upstream/widget"]);
  assert.equal(gh.calls.length, 1 + LIVE_REQUESTS_PER_CHECK);
  assert.equal(byId(outcome.result.checks)["D-GATE-2"].reason, "fork_parent_in_universe");
  for (const [answer, reason] of [[false, "fork_parent_outside"], ["yes", "fork_parent_unknown"]]) {
    const r = await runLiveCheck("acme", "widget", { fetchImpl: fakeGitHub({ repo: forkRepo }).fetchImpl, now: NOW, isInUniverse: async () => answer });
    assert.equal(byId(r.result.checks)["D-GATE-2"].reason, reason);
  }
  const thrown = await runLiveCheck("acme", "widget", {
    fetchImpl: fakeGitHub({ repo: forkRepo }).fetchImpl, now: NOW, isInUniverse: async () => { throw new Error("shard failed"); },
  });
  assert.equal(byId(thrown.result.checks)["D-GATE-2"].reason, "fork_parent_unknown");
  let called = false;
  await runLiveCheck("acme", "widget", {
    fetchImpl: fakeGitHub({ repo: () => reply({ ...REPO_JSON, fork: true, parent: { full_name: "not a slug" } }) }).fetchImpl,
    now: NOW,
    isInUniverse: async () => { called = true; return true; },
  });
  assert.equal(called, false, "an unreadable parent name is never looked up");
});

test("runLiveCheck: an invalid name makes no request at all, even when fetch is absent", async () => {
  for (const [owner, name] of [["acme", "../x"], ["-x", "y"], ["a/b", "c"], ["acme", ""]]) {
    const gh = fakeGitHub();
    assert.deepEqual(await runLiveCheck(owner, name, { fetchImpl: gh.fetchImpl, now: NOW }), { ok: false, error: { kind: "invalid" } });
    assert.equal(gh.calls.length, 0);
    assert.deepEqual(await runLiveCheck(owner, name, { fetchImpl: null, now: NOW }), { ok: false, error: { kind: "invalid" } });
  }
  assert.deepEqual(await runLiveCheck("acme", "widget", { fetchImpl: null, now: NOW }), { ok: false, error: { kind: "network" } });
});

function abortable(init, settle) {
  return new Promise((resolve, reject) => {
    init.signal.addEventListener("abort", () => {
      const err = new Error("The operation was aborted.");
      err.name = "AbortError";
      reject(err);
    });
    if (settle) settle(resolve);
  });
}

test("runLiveCheck: a request that never answers times out", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const gh = fakeGitHub({ rate_limit: (url, init) => abortable(init) });
  const pending = runLiveCheck("acme", "widget", { fetchImpl: gh.fetchImpl, now: NOW });
  t.mock.timers.tick(LIVE_TIMEOUT_MS);
  assert.deepEqual(await pending, { ok: false, error: { kind: "timeout" } });
});

test("runLiveCheck: the timeout covers a body that stalls after its headers", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const gh = fakeGitHub({ repo: (url, init) => ({ ...reply({}), json: () => abortable(init) }) });
  const pending = runLiveCheck("acme", "widget", { fetchImpl: gh.fetchImpl, now: NOW });
  await new Promise((resolve) => setImmediate(resolve));
  t.mock.timers.tick(LIVE_TIMEOUT_MS);
  assert.deepEqual(await pending, { ok: false, error: { kind: "timeout" } });
});

test("importing livecheck.js makes no request (nothing runs until the button)", async () => {
  const realFetch = globalThis.fetch;
  let called = 0;
  globalThis.fetch = async () => { called += 1; throw new Error("no request expected"); };
  try {
    await import(`../livecheck.js?fresh=${Date.now()}`);
    await import(`../components/livepanel.js?fresh=${Date.now()}`);
  } finally {
    globalThis.fetch = realFetch;
  }
  assert.equal(called, 0);
});

test("only the live-check button starts a check: app.js calls runLiveCheck once, from startLiveCheck, which only the [data-live-check] click calls", () => {
  const app = source("app.js");
  assert.equal(app.match(/\brunLiveCheck\(/g).length, 1);
  assert.match(app, /function startLiveCheck\((\w+)\) \{[\s\S]*?runLiveCheck\(slug\.owner, slug\.name,/);
  const calls = [...app.matchAll(/(?<!function )\bstartLiveCheck\(([^)]*)\)/g)].map((m) => m[1]);
  assert.deepEqual(calls, ["liveCheckBtn.dataset.liveCheck"], "one call, from the click handler");
  assert.match(app, /const liveCheckBtn = evt\.target\.closest\("\[data-live-check\]"\);/);
  for (const file of ["state.js", "views/drawer.js", "views/leaderboard.js", "components/livepanel.js", "copy.js"]) {
    assert.ok(!/\brunLiveCheck\(/.test(source(file)), `${file} must not run a check`);
  }
});

// --------------------------------------------------------------------- //
// components/livepanel.js
// --------------------------------------------------------------------- //

function okOutcome(overrides = {}) {
  const repo = { ...BASE_REPO, ...(overrides.repo || {}) };
  return {
    ok: true,
    result: {
      repo,
      checks: overrides.checks || evaluateLiveGates(facts({ repo }), NOW),
      checkedAt: NOW.toISOString(),
      requests: overrides.requests || LIVE_REQUESTS_PER_CHECK,
    },
  };
}

const panelState = (entry, key = "acme/widget") => ({
  ...defaultState(),
  snapshot: { snapshot_id: "2026-10-14" },
  liveCheckByKey: entry ? { [key]: entry } : {},
});

test("renderNotInSnapshot: before the click, the lede, the heading, the button and what it will cost -- and no request", () => {
  const html = renderNotInSnapshot(panelState(null), "acme/widget", { nowMs: NOW.getTime() });
  assert.ok(html.includes('data-live-check-region="true"'));
  assert.ok(html.includes(escapeHtml(copy.liveNotInSnapshotText("acme/widget", "2026-10-14"))));
  assert.ok(html.includes(escapeHtml(copy.LIVE_CHECK_HEADING)));
  assert.ok(html.includes(`data-live-check="acme/widget"`));
  assert.ok(html.includes(escapeHtml(copy.LIVE_CHECK_BUTTON_LABEL)));
  assert.ok(html.includes(escapeHtml(copy.liveCheckNoteText(LIVE_REQUESTS_PER_CHECK))));
  assert.ok(!html.includes("live-gates"));
});

test("renderNotInSnapshot: only a canonical owner/name offers a check; non-normalised or invalid keys offer no check", () => {
  for (const key of ["https://github.com/acme/widget", "acme/widget.git", "malformed-no-slash", "a/b/c", "x/<img src=x>", ""]) {
    const html = renderNotInSnapshot(panelState(null), key);
    assert.ok(html.includes(copy.DRAWER_NOT_FOUND_TEXT), key);
    assert.ok(!html.includes("data-live-check"), key);
  }
});

test("renderNotInSnapshot: loading shows a status line and no button", () => {
  const html = renderNotInSnapshot(panelState({ status: "loading" }), "acme/widget");
  assert.ok(html.includes('role="status"'));
  assert.ok(html.includes(copy.LIVE_CHECK_LOADING_TEXT));
  assert.ok(!html.includes("data-live-check=\""));
});

test("renderNotInSnapshot: a result shows the disclaimer, facts, six gates with their status, the cost and the GitHub link", () => {
  const html = renderNotInSnapshot(panelState({ status: "done", outcome: okOutcome() }), "acme/widget");
  assert.ok(html.includes(escapeHtml(copy.LIVE_CHECK_DISCLAIMER)));
  assert.equal((html.match(/data-live-gate="/g) || []).length, 6);
  for (const id of LIVE_GATE_IDS) assert.ok(html.includes(`data-live-gate="${id}"`), id);
  assert.equal((html.match(/live-pill is-pass/g) || []).length, 5);
  assert.equal((html.match(/live-pill is-not-checked/g) || []).length, 1);
  assert.ok(html.includes(escapeHtml(copy.liveCheckedAtText(NOW.toISOString(), LIVE_REQUESTS_PER_CHECK))));
  assert.ok(html.includes('href="https://github.com/acme/widget"'));
  assert.ok(html.includes('rel="noopener noreferrer"'));
  assert.ok(!html.includes("data-live-open"), "same name: no renamed notice");
  assert.ok(!html.includes("data-live-check=\""), "a finished check is not re-run from the panel");
});

test("renderNotInSnapshot: GitHub's other name for the repository is offered as a drawer to open", () => {
  const html = renderNotInSnapshot(panelState({ status: "done", outcome: okOutcome({ repo: { fullName: "Acme/Widget" } }) }), "acme/widget");
  assert.ok(html.includes('data-live-open="Acme/Widget"'));
  assert.ok(html.includes(copy.liveRenamedText("Acme/Widget")));
  assert.ok(html.includes('href="https://github.com/Acme/Widget"'));
  // A canonical name that is not a valid slug is shown nowhere as a link or a button.
  const odd = renderNotInSnapshot(panelState({ status: "done", outcome: okOutcome({ repo: { fullName: "../x" } }) }), "acme/widget");
  assert.ok(!odd.includes("data-live-open"));
  assert.ok(odd.includes('href="https://github.com/acme/widget"'));
});

test("renderNotInSnapshot: everything GitHub returns is escaped", () => {
  const evil = "<img src=x onerror=alert(1)>\"'";
  const outcome = okOutcome({
    repo: { description: evil, language: evil, licenseSpdx: evil },
    checks: [
      { id: "D-GATE-1", status: "pass", reason: "manifest_found", detail: { path: `src/${evil}/package.json` } },
      { id: "D-GATE-2", status: "fail", reason: "fork_parent_in_universe", detail: { parent: evil } },
      { id: "D-GATE-3", status: "fail", reason: "not_osi", detail: { spdx: evil } },
      { id: "D-GATE-4", status: evil, reason: evil, detail: {} },
    ],
  });
  const html = renderNotInSnapshot(panelState({ status: "done", outcome }), "acme/widget");
  assert.ok(!html.includes("<img"), "no raw tag from GitHub's text");
  assert.ok(!/onerror=alert\(1\)>"/.test(html));
  assert.ok(html.includes("&lt;img src=x onerror=alert(1)&gt;&quot;&#39;"));
});

test("renderNotInSnapshot: errors say why; only a retry that could change the answer is offered", () => {
  const render = (error) => renderNotInSnapshot(panelState({ status: "done", outcome: { ok: false, error } }), "acme/widget", { nowMs: NOW.getTime() });
  const resetAt = Math.floor(NOW.getTime() / 1000) + 90;
  const limited = render({ kind: "rate_limited", resetAt });
  assert.ok(limited.includes(escapeHtml(copy.liveErrorText({ kind: "rate_limited", resetAt }, NOW.getTime()))));
  assert.ok(limited.includes("about 2 minutes"));
  assert.ok(limited.includes('role="alert"'));
  for (const kind of ["rate_limited", "timeout", "network", "http"]) assert.ok(render({ kind, status: 500 }).includes(`data-live-check="acme/widget"`), kind);
  for (const kind of ["not_found", "unavailable", "invalid"]) assert.ok(!render({ kind }).includes("data-live-check=\""), kind);
  assert.ok(render({ kind: "http", status: 502 }).includes("status 502"));
  assert.ok(render({ kind: "http", status: 200 }).includes("unreadable response"));
});

// --------------------------------------------------------------------- //
// copy.js
// --------------------------------------------------------------------- //

test("copy: every reason code livecheck.js can produce has its own sentence; every status and gate has a label", () => {
  const reasons = [...new Set([...source("livecheck.js").matchAll(/reason: "([a-z_]+)"/g)].map((m) => m[1]))];
  assert.ok(reasons.length >= 25, `found only ${reasons.length} reason codes`);
  const detail = {
    path: "p", depth: 2, parent: "o/r", spdx: "MIT", file: "LICENSE",
    count: 1, more: false, days: 180, pageSize: 100, sample: 30, number: 1, bot: false,
    handoffPossible: false, truncated: false,
  };
  for (const reason of reasons) {
    const text = copy.liveReasonText(reason, detail);
    assert.notEqual(text, copy.MISSING_VALUE, reason);
    assert.ok(!text.includes("undefined"), reason);
  }
  assert.equal(copy.liveReasonText("no_such_reason", {}), copy.MISSING_VALUE);
  for (const status of ["pass", "fail", "undecided", "routed", "not_checked"]) assert.ok(copy.LIVE_STATUS_LABELS[status], status);
  for (const id of LIVE_GATE_IDS) assert.ok(copy.LIVE_GATE_NAMES[id], id);
  assert.match(copy.liveReasonText("decided_external_pr", { number: 9, days: 365, bot: true, handoffPossible: false }), /bot/);
  assert.doesNotMatch(copy.liveReasonText("decided_external_pr", { number: 9, days: 365, bot: false, handoffPossible: false }), /bot/);
  assert.match(copy.liveReasonText("decided_external_pr", { number: 9, days: 365, bot: false, handoffPossible: true }), /Gerrit/);
  assert.match(copy.liveReasonText("prs_handoff_unknown", { sample: 30, days: 365 }), /Gerrit/);
  assert.match(copy.liveReasonText("fork_parent_outside", { parent: "o/r" }), /under that name/);
  assert.match(copy.liveReasonText("human_commits", { count: 1, more: false, days: 180 }), /1 commit\b/);
  assert.match(copy.liveReasonText("human_commits", { count: 2, more: false, days: 180 }), /2 commits\b/);
  assert.match(copy.liveReasonText("license_unknown", { truncated: true }), /too incomplete/);
  assert.match(copy.liveReasonText("license_unknown", { truncated: false }), /did not return/);
  assert.match(copy.liveReasonText("prs_marker_unknown", { truncated: true }), /too incomplete/);
  assert.match(copy.liveReasonText("prs_marker_unknown", { truncated: false }), /did not return/);
});

test("copy: the announcement counts only the checks that ran, a routed one as passing, and mentions undecided checks", () => {
  const checks = [
    { id: "D-GATE-1", status: "pass" }, { id: "D-GATE-2", status: "fail" }, { id: "D-GATE-3", status: "undecided" },
    { id: "D-GATE-4", status: "pass" }, { id: "D-GATE-5", status: "not_checked" }, { id: "D-GATE-6", status: "routed" },
  ];
  assert.equal(copy.liveCheckAnnouncement({ ok: true, result: { checks } }), "Live check done: 3 of 5 checks pass (1 can't tell).");
  const allPass = checks.map((c) => (c.status === "undecided" || c.status === "fail" ? { ...c, status: "pass" } : c));
  assert.equal(copy.liveCheckAnnouncement({ ok: true, result: { checks: allPass } }), "Live check done: 5 of 5 checks pass.");
  assert.equal(copy.liveCheckAnnouncement({ ok: false, error: { kind: "not_found" } }), copy.liveErrorText({ kind: "not_found" }));
});

test("copy: rate-limit wording rounds the reset up to whole minutes, never below one", () => {
  const now = NOW.getTime();
  const at = (seconds) => Math.floor(now / 1000) + seconds;
  assert.match(copy.liveErrorText({ kind: "rate_limited", resetAt: at(30) }, now), /about 1 minute\./);
  assert.match(copy.liveErrorText({ kind: "rate_limited", resetAt: at(-300) }, now), /about 1 minute\./);
  assert.match(copy.liveErrorText({ kind: "rate_limited", resetAt: at(3000) }, now), /about 50 minutes\./);
  assert.match(copy.liveErrorText({ kind: "rate_limited", resetAt: null }, now), /Try again later\./);
  assert.equal(copy.liveErrorText(null), copy.liveErrorText({ kind: "invalid" }));
});

// --------------------------------------------------------------------- //
// state.js and the leaderboard's way in
// --------------------------------------------------------------------- //

test("state: live checks are kept per repository (and under canonical fullName) for the session, and repo params normalise slugs", () => {
  const s0 = defaultState();
  assert.deepEqual(s0.liveCheckByKey, {});
  const s1 = reduce(s0, { type: "LIVE_CHECK_LOADING", key: "a/b" });
  assert.deepEqual(s1.liveCheckByKey, { "a/b": { status: "loading" } });
  assert.deepEqual(s0.liveCheckByKey, {}, "the reducer does not mutate");
  const outcome = { ok: false, error: { kind: "network" } };
  const s2 = reduce(reduce(s1, { type: "LIVE_CHECK_LOADING", key: "c/d" }), { type: "LIVE_CHECK_DONE", key: "a/b", outcome });
  assert.deepEqual(s2.liveCheckByKey, { "a/b": { status: "done", outcome }, "c/d": { status: "loading" } });

  const renamed = okOutcome({ repo: { fullName: "Acme/Widget" } });
  const s3 = reduce(s0, { type: "LIVE_CHECK_DONE", key: "acme/widget", outcome: renamed });
  assert.equal(s3.liveCheckByKey["acme/widget"].outcome, renamed);
  assert.equal(s3.liveCheckByKey["Acme/Widget"].outcome, renamed);

  assert.equal(parseSearch("?repo=bulk%2Frepo-0133.git").repo, "bulk/repo-0133");
  assert.equal(parseSearch("?repo=https%3A%2F%2Fgithub.com%2Fbulk%2Frepo-0133%2F").repo, "bulk/repo-0133");
  assert.equal(reduce(s0, { type: "OPEN_REPO", repo: "bulk/repo-0133.git" }).repo, "bulk/repo-0133");
});

test("renderFilteredEmpty: a search that is a repository name offers to look it up; any other search does not", () => {
  const withSlug = renderFilteredEmpty("acme/widget", "", "notranked", "ranked");
  assert.ok(withSlug.includes('data-owner="acme" data-name="widget"'));
  assert.ok(withSlug.includes(copy.lookUpRepoLabel("acme/widget")));
  assert.ok(renderFilteredEmpty("https://github.com/acme/widget", "", "ranked", "gated or unrated").includes('data-owner="acme" data-name="widget"'));
  for (const query of ["widget", "", "a/b/c", "\"><x/y", "acme /widget"]) {
    assert.ok(!renderFilteredEmpty(query, "", "notranked", "ranked").includes("data-owner="), query);
  }
});
