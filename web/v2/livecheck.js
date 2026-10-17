// livecheck.js — the opt-in live check for a repository the snapshot does not
// hold (06 §3 C, "Repositories outside the snapshot").
//
// What it is: when a visitor presses the button, THEIR browser asks
// api.github.com for a few public facts and runs the eligibility gates that
// public data can decide -- D-GATE-1..4 and D-GATE-6 -- with the snapshot's
// own rules. What it is not: a Porchlamp score, a rank, or part of the
// verified snapshot. Scoring and the black-hole check (D-GATE-5) need every
// outside pull request's replies and reviews, which only the pipeline reads.
//
// Why it costs the project nothing: no server, no token, no workflow, no
// write. A check spends the VISITOR's own unauthenticated GitHub allowance
// (`LIVE_REQUESTS_PER_CHECK` of 60 an hour, checked first with the free
// `GET /rate_limit`), and nothing runs until they press the button, so the
// site still makes no external request on its own (06 §8 item 10).
//
// Fidelity: the rule constants below MIRROR the engine's, each named next to
// its Python source; tests/test_ui_livecheck_port.py fails if one drifts, and
// web/v2/tests/cases/spdx_osi.json pins the licence-expression reading for
// both the Python gate and this port. Every function except `runLiveCheck`
// is pure.

//: The only external origin this module (or any module) contacts.
export const LIVE_API_BASE = "https://api.github.com";
//: GitHub requests one check spends; `GET /rate_limit` is free and not counted,
//: and a renamed repository costs 1 more for the 301 redirect (reserved in the pre-check).
export const LIVE_REQUESTS_PER_CHECK = 4;
//: Rows per commits request (GitHub's maximum page size).
export const LIVE_PAGE_SIZE = 100;
//: scripts/gather.py::PR_SAMPLE -- the 30 newest pull requests by creation, any state.
export const LIVE_PR_SAMPLE = 30;
//: Per-request timeout; the recursive tree of a very large repository is several MB.
export const LIVE_TIMEOUT_MS = 8000;
export const LIVE_TREE_TIMEOUT_MS = 15000;
//: Longest repository description shown (GitHub allows 350 characters).
export const LIVE_DESCRIPTION_MAX = 350;
//: Longest string `parseRepoSlug` inspects ("https://github.com/" + 39 + "/" + 100 + ".git/" = 164).
export const MAX_REPO_SLUG_INPUT = 256;

//: porchlamp.core.constants.PR_WINDOW_DAYS.
export const PR_WINDOW_DAYS = 180;
//: porchlamp.core.constants.GATE4_VITALITY_WINDOW_DAYS.
export const VITALITY_WINDOW_DAYS = 180;
//: porchlamp.core.constants.GATE6_DECISION_WINDOW_DAYS.
export const DECISION_WINDOW_DAYS = 365;
//: porchlamp.core.constants.GATE6_HANDOFF_MIN_PRS and GATE6_HANDOFF_MIN_SHARE.
export const GATE6_HANDOFF_MIN_PRS = 5;
export const GATE6_HANDOFF_MIN_SHARE = 0.5;
//: porchlamp.derive.gate_evidence.MANIFEST_MAX_DEPTH ("/" separators from the root).
export const MANIFEST_MAX_DEPTH = 2;
//: porchlamp.derive.gate_evidence.RECOGNIZED_MANIFEST_BASENAMES.
export const MANIFEST_BASENAMES = Object.freeze([
  "BUILD", "BUILD.bazel", "CMakeLists.txt", "Cargo.toml", "GNUmakefile", "Gemfile",
  "Kbuild", "MODULE.bazel", "Makefile", "Package.swift", "SConstruct", "WORKSPACE",
  "WORKSPACE.bazel", "build.sbt", "build.zig", "build.zig.zon", "composer.json", "conanfile.py",
  "conanfile.txt", "configure.ac", "default.nix", "deps.edn", "dune-project", "flake.nix",
  "go.mod", "meson.build", "mix.exs", "package.json", "pom.xml", "project.clj",
  "pubspec.yaml", "pyproject.toml", "setup.cfg", "setup.py", "stack.yaml", "vcpkg.json",
]);
//: porchlamp.derive.gate_evidence.RECOGNIZED_MANIFEST_PREFIXES.
export const MANIFEST_PREFIXES = Object.freeze(["build.gradle"]);
//: porchlamp.derive.gate_evidence.RECOGNIZED_MANIFEST_SUFFIXES.
export const MANIFEST_SUFFIXES = Object.freeze([".cabal", ".csproj", ".fsproj", ".sln", ".rockspec", ".gemspec", ".podspec"]);
//: porchlamp.derive.gate_evidence._has_off_platform_marker's path substring (compared lower-cased).
export const OFF_PLATFORM_MARKER = "documentation/process/submitting-patches";
//: porchlamp.core.gates.UNDETERMINED_SPDX, minus None (an absent licence is handled as null).
export const UNDETERMINED_SPDX = Object.freeze(["NOASSERTION", "OTHER"]);
//: porchlamp.core.constants.OSI_APPROVED_LICENSES (D-GATE-3).
export const OSI_APPROVED_LICENSES = Object.freeze([
  "0BSD", "AAL", "AFL-1.1", "AFL-1.2", "AFL-2.0",
  "AFL-2.1", "AFL-3.0", "AGPL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later",
  "ALGLIB-Documentation", "APL-1.0", "APSL-1.0", "APSL-1.1", "APSL-1.2",
  "APSL-2.0", "Apache-1.1", "Apache-2.0", "Artistic-1.0", "Artistic-1.0-Perl",
  "Artistic-1.0-cl8", "Artistic-2.0", "BSD-1-Clause", "BSD-2-Clause", "BSD-2-Clause-Patent",
  "BSD-3-Clause", "BSD-3-Clause-LBNL", "BSD-3-Clause-Open-MPI", "BSD-ask-to-endorse", "BSL-1.0",
  "BlueOak-1.0.0", "CAL-1.0", "CAL-1.0-Combined-Work-Exception", "CATOSL-1.1", "CDDL-1.0",
  "CDDL-1.1", "CECILL-2.1", "CERN-OHL-P-2.0", "CERN-OHL-S-2.0", "CERN-OHL-W-2.0",
  "CNRI-Python", "CNRI-Python-GPL-Compatible", "CPAL-1.0", "CPL-1.0", "CUA-OPL-1.0",
  "ECL-1.0", "ECL-2.0", "EFL-1.0", "EFL-2.0", "EPL-1.0",
  "EPL-2.0", "EUDatagrid", "EUPL-1.1", "EUPL-1.2", "Entessa",
  "Fair", "Frameworx-1.0", "GPL-1.0", "GPL-2.0", "GPL-2.0+",
  "GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0", "GPL-3.0+", "GPL-3.0-only",
  "GPL-3.0-or-later", "GPL-3.0-with-GCC-exception", "HPND", "ICU", "IPA",
  "IPL-1.0", "ISC", "Intel", "Jam", "LGPL-2.0",
  "LGPL-2.0+", "LGPL-2.0-only", "LGPL-2.0-or-later", "LGPL-2.1", "LGPL-2.1+",
  "LGPL-2.1-only", "LGPL-2.1-or-later", "LGPL-3.0", "LGPL-3.0+", "LGPL-3.0-only",
  "LGPL-3.0-or-later", "LPL-1.0", "LPL-1.02", "LPPL-1.3c", "LiLiQ-P-1.1",
  "LiLiQ-R-1.1", "LiLiQ-Rplus-1.1", "MIT", "MIT-0", "MIT-Modern-Variant",
  "MPL-1.0", "MPL-1.1", "MPL-2.0", "MPL-2.0-no-copyleft-exception", "MS-PL",
  "MS-RL", "MirOS", "Motosoto", "MulanPSL-2.0", "Multics",
  "NASA-1.3", "NCSA", "NGPL", "NIST-Software", "NPOSL-3.0",
  "NTP", "Naumen", "Nokia", "OCLC-2.0", "OFL-1.1",
  "OFL-1.1-RFN", "OFL-1.1-no-RFN", "OGTSL", "OLDAP-2.8", "OLFL-1.3",
  "OSC-1.0", "OSET-PL-2.1", "OSL-1.0", "OSL-2.0", "OSL-2.1",
  "OSL-3.0", "PHP-3.0", "PHP-3.01", "PostgreSQL", "Python-2.0",
  "Python-2.0.1", "QPL-1.0", "RPL-1.1", "RPL-1.5", "RPSL-1.0",
  "RSCPL", "SISSL", "SPL-1.0", "SimPL-2.0", "Sleepycat",
  "UCL-1.0", "UPL-1.0", "Unicode-3.0", "Unicode-DFS-2016", "Unlicense",
  "VSL-1.0", "W3C", "W3C-20150513", "Watcom-1.0", "WordNet",
  "Xnet", "ZPL-2.0", "ZPL-2.1", "Zlib", "curl",
  "eCos-2.0", "wxWindows",
]);
//: porchlamp.core.commit_domains.KNOWN_BOT_LOGINS (= porchlamp.collect.github_graphql.BOT_LOGINS), D-IN-1.
export const KNOWN_BOT_LOGINS = Object.freeze([
  "allcontributors", "cla-bot", "claude", "codecov", "copilot", "dependabot",
  "easycla", "github-actions", "google-cla", "greenkeeper", "imgbot", "mergify",
  "netlify", "pre-commit-ci", "renovate", "semantic-release", "snyk-bot", "sonarcloud",
  "stale", "vercel",
]);
//: porchlamp.collect.github_graphql.EXTERNAL_ASSOCIATIONS -- a PR author who is not a maintainer.
export const EXTERNAL_ASSOCIATIONS = Object.freeze(["CONTRIBUTOR", "FIRST_TIMER", "FIRST_TIME_CONTRIBUTOR", "NONE"]);

//: The gates in their fixed design order (porchlamp.core.gates.GATE_DECISION_IDS).
export const LIVE_GATE_IDS = Object.freeze(["D-GATE-1", "D-GATE-2", "D-GATE-3", "D-GATE-4", "D-GATE-5", "D-GATE-6"]);

const DAY_MS = 24 * 60 * 60 * 1000;
const BOT_LOGIN_SET = new Set(KNOWN_BOT_LOGINS);
const OSI_SET = new Set(OSI_APPROVED_LICENSES.map((id) => id.toLowerCase()));
const UNDETERMINED_SET = new Set(UNDETERMINED_SPDX);
const EXTERNAL_SET = new Set(EXTERNAL_ASSOCIATIONS);

// --------------------------------------------------------------------- //
// Input
// --------------------------------------------------------------------- //

//: GitHub's own rules: an account login is 1-39 letters, digits or hyphens and
//: does not start with a hyphen; a repository name is 1-100 of [A-Za-z0-9._-].
const OWNER_RE = /^[A-Za-z0-9][A-Za-z0-9-]{0,38}$/;
const NAME_RE = /^[A-Za-z0-9._-]{1,100}$/;
//: A pasted repository address ("https://github.com/o/r", "github.com/o/r.git").
//: (`[/]` rather than an escaped slash right before the closing one, so the
//: address scanner in tests/test_ui_no_external_resources.py sees no double
//: slash followed by the flag letter.)
const GITHUB_ADDRESS_RE = /^(?:https?:\/\/)?(?:www\.)?github\.com[/]/i;

/**
 * `{ owner, name }` for "owner/name" (or a pasted github.com address), else
 * `null`. Nothing that fails this ever reaches a request URL, so a crafted
 * search can never become a path traversal or a request to another endpoint.
 */
export function parseRepoSlug(text) {
  if (typeof text !== "string" || text.length > MAX_REPO_SLUG_INPUT) return null;
  let trimmed = text.trim().replace(GITHUB_ADDRESS_RE, "");
  while (trimmed.endsWith("/")) trimmed = trimmed.slice(0, -1);
  trimmed = trimmed.replace(/\.git$/i, "");
  const parts = trimmed.split("/");
  if (parts.length !== 2) return null;
  const [owner, name] = parts;
  if (!OWNER_RE.test(owner) || !NAME_RE.test(name) || name === "." || name === "..") return null;
  return { owner, name };
}

// --------------------------------------------------------------------- //
// The engine's rules, ported (pure)
// --------------------------------------------------------------------- //

/** D-IN-1, as porchlamp.core.commit_domains.is_bot: a Bot type, a "[bot]" or known login, or a bot email local part. */
export function isBotActor({ type = "", login = "", email = "" } = {}) {
  if (type === "Bot") return true;
  const low = String(login || "").toLowerCase();
  if (low.endsWith("[bot]") || BOT_LOGIN_SET.has(low)) return true;
  const mail = String(email || "").trim().toLowerCase();
  const local = mail.includes("@") ? mail.slice(0, mail.lastIndexOf("@")) : "";
  return local.endsWith("[bot]") || BOT_LOGIN_SET.has(local);
}

/** D-GATE-1, as gate_evidence._has_recognized_manifest: the first recognised manifest at depth <= 2, else null. */
export function findRecognizedManifest(paths) {
  for (const path of paths) {
    if (path.split("/").length - 1 > MANIFEST_MAX_DEPTH) continue;
    const base = path.slice(path.lastIndexOf("/") + 1);
    if (
      MANIFEST_BASENAMES.includes(base) ||
      MANIFEST_PREFIXES.some((p) => base.startsWith(p)) ||
      MANIFEST_SUFFIXES.some((s) => base.endsWith(s))
    ) {
      return path;
    }
  }
  return null;
}

/** D-GATE-3's fallback, as gate_evidence._has_license_file: a root-level LICENSE* or COPYING* file, else null. */
export function findLicenseFile(paths) {
  for (const path of paths) {
    if (path.includes("/")) continue;
    const upper = path.toUpperCase();
    if (upper.startsWith("LICENSE") || upper.startsWith("COPYING")) return path;
  }
  return null;
}

/** D-GATE-6, as gate_evidence._has_off_platform_marker. */
export function hasOffPlatformMarker(paths) {
  return paths.some((path) => path.toLowerCase().includes(OFF_PLATFORM_MARKER));
}

const SPDX_EXPRESSION_RE = /^[A-Za-z0-9.\-+()\s]+$/;
const SPDX_OPERATORS = new Set(["AND", "OR", "WITH"]);

/**
 * A port of porchlamp.core.gates._eval_spdx_expression: WITH > AND > OR with
 * parentheses; `true`/`false` for whether the expression grants an id in
 * `approved` (lower-cased), `null` when it is not a readable expression.
 */
function evalSpdxExpression(expression, approved) {
  const tokens = expression.trim().match(/[()]|[A-Za-z0-9.\-+]+/g) || [];
  if (!tokens.length) return null;
  let pos = 0;
  const upper = (i) => tokens[i].toUpperCase();

  function parsePrimary() {
    if (pos >= tokens.length) return null;
    const token = tokens[pos];
    if (token === "(") {
      pos += 1;
      const inner = parseOr();
      if (inner === null || pos >= tokens.length || tokens[pos] !== ")") return null;
      pos += 1;
      return inner;
    }
    if (token === ")" || SPDX_OPERATORS.has(token.toUpperCase())) return null;
    const licId = token.replace(/\++$/, "");
    if (!licId) return null;
    pos += 1;
    if (pos < tokens.length && upper(pos) === "WITH") {
      pos += 1;
      if (pos >= tokens.length) return null;
      const exception = tokens[pos];
      if (exception === "(" || exception === ")" || SPDX_OPERATORS.has(exception.toUpperCase())) return null;
      pos += 1;
    }
    return approved.has(licId.toLowerCase());
  }

  function parseAnd() {
    const left = parsePrimary();
    if (left === null) return null;
    let result = left;
    while (pos < tokens.length && upper(pos) === "AND") {
      pos += 1;
      const right = parsePrimary();
      if (right === null) return null;
      result = result && right;
    }
    return result;
  }

  function parseOr() {
    const left = parseAnd();
    if (left === null) return null;
    let result = left;
    while (pos < tokens.length && upper(pos) === "OR") {
      pos += 1;
      const right = parseAnd();
      if (right === null) return null;
      result = result || right;
    }
    return result;
  }

  const value = parseOr();
  if (value === null || pos !== tokens.length) return null;
  return value;
}

/** porchlamp.core.gates._is_parseable_spdx_expression. */
export function isParseableSpdxExpression(spdx) {
  if (typeof spdx !== "string" || !spdx.trim() || !SPDX_EXPRESSION_RE.test(spdx)) return false;
  return evalSpdxExpression(spdx, new Set()) !== null;
}

/** porchlamp.core.gates._is_osi_approved_expression: AND needs every id approved, OR needs one. */
export function isOsiApprovedExpression(spdx) {
  if (typeof spdx !== "string" || !spdx.trim() || !SPDX_EXPRESSION_RE.test(spdx)) return false;
  return evalSpdxExpression(spdx, OSI_SET) === true;
}

/**
 * The live verdict for each gate, in design order, from facts the browser
 * fetched. Each entry is `{ id, status, reason, detail }`: status is
 * "pass" | "fail" | "undecided" | "routed" | "not_checked", reason a code
 * copy.js words, detail the facts the sentence names. Mirrors
 * porchlamp.core.gates.evaluate_gates, with an unreadable or incomplete
 * input recorded "undecided" exactly where the engine records UNDECIDED,
 * never guessed into a pass or a fail.
 */
export function evaluateLiveGates(facts, now) {
  const { repo, parentInUniverse = null, tree = null, commits = null, pulls = null } = facts;
  const nowMs = now.getTime();
  const checks = [];

  // D-GATE-1: a recognised manifest at depth <= 2 (UNDECIDED on a truncated tree without one).
  if (!tree) checks.push({ id: "D-GATE-1", status: "undecided", reason: "tree_unreadable", detail: {} });
  else {
    const manifest = findRecognizedManifest(tree.paths);
    if (manifest) checks.push({ id: "D-GATE-1", status: "pass", reason: "manifest_found", detail: { path: manifest } });
    else if (tree.truncated) checks.push({ id: "D-GATE-1", status: "undecided", reason: "tree_truncated", detail: {} });
    else checks.push({ id: "D-GATE-1", status: "fail", reason: "no_manifest", detail: { depth: MANIFEST_MAX_DEPTH } });
  }

  // D-GATE-2: not archived, not a mirror, not a fork whose parent the universe already holds.
  if (repo.archived) checks.push({ id: "D-GATE-2", status: "fail", reason: "archived", detail: {} });
  else if (repo.mirrorUrl) checks.push({ id: "D-GATE-2", status: "fail", reason: "mirror", detail: {} });
  else if (repo.fork) {
    const parent = repo.parentFullName || "";
    if (parentInUniverse === true) checks.push({ id: "D-GATE-2", status: "fail", reason: "fork_parent_in_universe", detail: { parent } });
    else if (parentInUniverse === false) checks.push({ id: "D-GATE-2", status: "pass", reason: "fork_parent_outside", detail: { parent } });
    else checks.push({ id: "D-GATE-2", status: "undecided", reason: "fork_parent_unknown", detail: { parent } });
  } else checks.push({ id: "D-GATE-2", status: "pass", reason: "original", detail: {} });

  // D-GATE-3: an OSI-approved SPDX expression; an undetermined or unreadable
  // one falls back to a root LICENSE*/COPYING* file; a readable non-OSI one fails.
  const spdx = repo.licenseSpdx;
  if (isOsiApprovedExpression(spdx)) checks.push({ id: "D-GATE-3", status: "pass", reason: "osi_spdx", detail: { spdx } });
  else if (spdx === null || UNDETERMINED_SET.has(spdx) || !isParseableSpdxExpression(spdx)) {
    const file = tree ? findLicenseFile(tree.paths) : null;
    if (file) checks.push({ id: "D-GATE-3", status: "pass", reason: "license_file", detail: { file } });
    else if (!tree || tree.truncated) {
      checks.push({ id: "D-GATE-3", status: "undecided", reason: "license_unknown", detail: { truncated: Boolean(tree && tree.truncated) } });
    } else checks.push({ id: "D-GATE-3", status: "fail", reason: "no_license", detail: {} });
  } else checks.push({ id: "D-GATE-3", status: "fail", reason: "not_osi", detail: { spdx } });

  // D-GATE-4: >= 1 non-bot commit on the default branch in the trailing window.
  // Mirrors scripts/gather.py:336-338 + gate_evidence._vitality_commit_count,
  // which record and inspect `author_login` only (unlinked commits have login "").
  if (!commits) checks.push({ id: "D-GATE-4", status: "undecided", reason: "commits_unreadable", detail: {} });
  else {
    const human = commits.rows.filter((c) => !isBotActor({ login: c.login })).length;
    if (human > 0) {
      checks.push({ id: "D-GATE-4", status: "pass", reason: "human_commits", detail: { count: human, more: !commits.complete, days: VITALITY_WINDOW_DAYS } });
    } else if (commits.complete) {
      checks.push({ id: "D-GATE-4", status: "fail", reason: "no_human_commits", detail: { days: VITALITY_WINDOW_DAYS } });
    } else {
      checks.push({ id: "D-GATE-4", status: "undecided", reason: "commits_partial", detail: { pageSize: LIVE_PAGE_SIZE } });
    }
  }

  // D-GATE-5: never decided here -- it needs replies and reviews on every outside PR.
  checks.push({ id: "D-GATE-5", status: "not_checked", reason: "needs_pipeline", detail: {} });

  // D-GATE-6: >= 1 external PR decided in the trailing 365 d among the 30
  // newest PRs by creation (scripts/gather.py::PR_SAMPLE; the snapshot also reads its
  // recorded PR window and author history, gate_evidence._decided_external_pr_in).
  // Without one, an off-platform marker routes the repo, and only a complete PR list
  // fails it, as only the whole PR history does in the snapshot. Deleted/ghost accounts
  // are dropped as in porchlamp/derive/bundle.py::_pr_event_or_none.
  // Review hand-off notices in PR comments (gate_evidence._review_handoff:
  // >= GATE6_HANDOFF_MIN_PRS outside human PRs and >= GATE6_HANDOFF_MIN_SHARE)
  // are not fetched live; when the repo can have >= GATE6_HANDOFF_MIN_PRS
  // outside human PRs, `handoffPossible` qualifies the pass copy.
  if (!pulls) checks.push({ id: "D-GATE-6", status: "undecided", reason: "prs_unreadable", detail: {} });
  else {
    const decidedAfter = nowMs - DECISION_WINDOW_DAYS * DAY_MS;
    const isExternal = (pr) => EXTERNAL_SET.has(String(pr.association || "NONE").toUpperCase());
    const validRows = pulls.rows.filter((pr) => Boolean(pr.login) && pr.login.toLowerCase() !== "ghost");
    const outsideHumanCount = validRows.filter((pr) => isExternal(pr) && !isBotActor(pr)).length;
    const handoffPossible = !pulls.complete || outsideHumanCount >= GATE6_HANDOFF_MIN_PRS;
    const qualifying = validRows.filter((pr) => {
      if (!isExternal(pr)) return false;
      const when = Date.parse(pr.mergedAt || pr.closedAt || "");
      return Number.isFinite(when) && when >= decidedAfter && when <= nowMs;
    });
    const decided = qualifying.find((pr) => !isBotActor(pr)) || qualifying[0];
    const marker = !tree ? null : hasOffPlatformMarker(tree.paths) ? true : tree.truncated ? null : false;
    if (decided) {
      checks.push({
        id: "D-GATE-6",
        status: "pass",
        reason: "decided_external_pr",
        detail: {
          number: decided.number,
          days: DECISION_WINDOW_DAYS,
          bot: isBotActor(decided),
          handoffPossible,
        },
      });
    } else if (marker === true) {
      checks.push({ id: "D-GATE-6", status: "routed", reason: "off_platform_marker", detail: { sample: LIVE_PR_SAMPLE, days: DECISION_WINDOW_DAYS } });
    } else if (marker === false && handoffPossible) {
      checks.push({ id: "D-GATE-6", status: "undecided", reason: "prs_handoff_unknown", detail: { sample: LIVE_PR_SAMPLE, days: DECISION_WINDOW_DAYS } });
    } else if (marker === false) {
      checks.push({ id: "D-GATE-6", status: "fail", reason: "no_external_pr", detail: { sample: LIVE_PR_SAMPLE, days: DECISION_WINDOW_DAYS } });
    } else {
      checks.push({
        id: "D-GATE-6",
        status: "undecided",
        reason: "prs_marker_unknown",
        detail: { sample: LIVE_PR_SAMPLE, days: DECISION_WINDOW_DAYS, truncated: Boolean(tree && tree.truncated) },
      });
    }
  }
  return checks;
}

// --------------------------------------------------------------------- //
// GitHub -> facts (pure)
// --------------------------------------------------------------------- //

const str = (value) => (typeof value === "string" ? value : "");
const num = (value) => (typeof value === "number" && Number.isFinite(value) ? value : null);

/** The repository facts the panel shows and the gates read, from `GET /repos/{o}/{r}`. */
export function repoFactsFrom(data) {
  const d = data && typeof data === "object" ? data : {};
  const license = d.license && typeof d.license === "object" ? d.license : null;
  const parent = d.parent && typeof d.parent === "object" ? d.parent : null;
  return {
    fullName: str(d.full_name),
    description: str(d.description).slice(0, LIVE_DESCRIPTION_MAX),
    stars: num(d.stargazers_count),
    language: str(d.language),
    createdAt: str(d.created_at),
    pushedAt: str(d.pushed_at),
    archived: d.archived === true,
    fork: d.fork === true,
    parentFullName: parent ? str(parent.full_name) : "",
    mirrorUrl: str(d.mirror_url),
    licenseSpdx: license && typeof license.spdx_id === "string" && license.spdx_id ? license.spdx_id : null,
  };
}

/**
 * `{ paths, truncated }` from a recursive tree, else null (including a 409
 * empty repository, which has no tree envelope in scripts/gather.py and
 * therefore yields UNDECIDED in gate_evidence.gate_evidence_for). Every
 * entry's path -- folders and submodules too -- is kept as the engine's own
 * tree envelope records them, so a root `LICENSES/` folder counts here as it does there.
 */
export function treeFactsFrom(res) {
  if (!res.ok) return null;
  const entries = res.data && Array.isArray(res.data.tree) ? res.data.tree : [];
  const paths = entries.filter((e) => e && typeof e.path === "string" && e.path).map((e) => e.path);
  return { paths, truncated: !!(res.data && res.data.truncated) };
}

/**
 * `{ rows: [{ login }], complete }` from one page of commits, or null.
 * Mirrors scripts/gather.py:336-338, which records `author_login` only.
 */
export function commitFactsFrom(res) {
  if (!res.ok) return null;
  const rows = (Array.isArray(res.data) ? res.data : []).map((c) => {
    const author = c && c.author && typeof c.author === "object" ? c.author : {};
    return { login: str(author.login) };
  });
  return { rows, complete: rows.length < LIVE_PAGE_SIZE };
}

/**
 * `{ rows, complete }` from the `LIVE_PR_SAMPLE` newest pull requests by
 * creation date (any state), matching scripts/gather.py:231-260, or null.
 */
export function pullFactsFrom(res) {
  if (!res.ok) return null;
  const list = Array.isArray(res.data) ? res.data : [];
  const rows = list.map((pr) => {
    const user = pr && pr.user && typeof pr.user === "object" ? pr.user : {};
    return {
      number: num(pr && pr.number),
      createdAt: str(pr && pr.created_at),
      closedAt: str(pr && pr.closed_at),
      mergedAt: str(pr && pr.merged_at),
      association: str(pr && pr.author_association),
      login: str(user.login),
      type: str(user.type),
    };
  });
  return { rows, complete: rows.length < LIVE_PR_SAMPLE };
}

// --------------------------------------------------------------------- //
// The network (the only impure part)
// --------------------------------------------------------------------- //

/**
 * One GET to api.github.com: `{ ok: true, data, redirected }` or `{ ok: false,
 * error }` with `error.kind` "rate_limited" (with `resetAt`, epoch seconds),
 * "not_found", "empty" (409, a repository with no commits), "unavailable"
 * (451), "timeout", "network" or "http" (with `status`). Never throws.
 * No credentials and no referrer leave the browser. The timeout covers the
 * body too: a large recursive tree keeps streaming after its headers.
 */
async function githubGet(fetchImpl, url, timeoutMs) {
  const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
  const failed = (err, fallback) => ({ ok: false, error: err && err.name === "AbortError" ? { kind: "timeout" } : fallback });
  try {
    let response;
    try {
      response = await fetchImpl(url, {
        headers: { Accept: "application/vnd.github+json" },
        credentials: "omit",
        referrerPolicy: "no-referrer",
        signal: controller ? controller.signal : undefined,
      });
    } catch (err) {
      return failed(err, { kind: "network" });
    }
    if (response.ok) {
      try {
        // `redirected`: GitHub answered a renamed repository's old name with a
        // 301, which counts against the visitor's allowance like any request.
        return { ok: true, data: await response.json(), redirected: response.redirected === true };
      } catch (err) {
        return failed(err, { kind: "http", status: response.status });
      }
    }
    const header = (name) => (response.headers && typeof response.headers.get === "function" ? response.headers.get(name) : null);
    if ((response.status === 403 || response.status === 429) && header("x-ratelimit-remaining") === "0") {
      const reset = Number(header("x-ratelimit-reset"));
      return { ok: false, error: { kind: "rate_limited", resetAt: Number.isFinite(reset) && reset > 0 ? reset : null } };
    }
    if (response.status === 404) return { ok: false, error: { kind: "not_found" } };
    if (response.status === 409) return { ok: false, error: { kind: "empty" } };
    if (response.status === 451) return { ok: false, error: { kind: "unavailable" } };
    return { ok: false, error: { kind: "http", status: response.status } };
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function parentInUniverseOf(isInUniverse, parentFullName) {
  const slug = parseRepoSlug(parentFullName);
  if (!slug) return null;
  try {
    const answer = await isInUniverse(slug.owner, slug.name);
    return answer === true || answer === false ? answer : null;
  } catch {
    return null;
  }
}

/**
 * Run one live check. `{ ok: true, result: { repo, checks, checkedAt,
 * requests } }` or `{ ok: false, error }` (see `githubGet`, plus "invalid").
 * Never throws. `LIVE_REQUESTS_PER_CHECK` counted requests (one more when
 * GitHub redirects a renamed repository's old name), after a free quota
 * check that stops BEFORE spending any when fewer than
 * `LIVE_REQUESTS_PER_CHECK + 1` are left, so even a renamed repository
 * never leaves the visitor with half a result for part of their allowance.
 *
 * `isInUniverse(owner, name)` answers D-GATE-2's fork question from
 * Porchlamp's own snapshot (true / false / null for "could not tell").
 */
export async function runLiveCheck(owner, name, { fetchImpl, now = new Date(), isInUniverse = async () => null } = {}) {
  const slug = parseRepoSlug(`${owner}/${name}`);
  if (!slug) return { ok: false, error: { kind: "invalid" } };
  const fetchFn = fetchImpl !== undefined ? fetchImpl : (typeof fetch === "function" ? fetch : null);
  if (typeof fetchFn !== "function") return { ok: false, error: { kind: "network" } };
  const get = (url, ms = LIVE_TIMEOUT_MS) => githubGet(fetchFn, url, ms);
  const repoUrl = ({ owner: o, name: n }) => `${LIVE_API_BASE}/repos/${encodeURIComponent(o)}/${encodeURIComponent(n)}`;
  try {
    const quota = await get(`${LIVE_API_BASE}/rate_limit`);
    if (quota.ok) {
      const core = quota.data && quota.data.resources && quota.data.resources.core;
      if (core && typeof core.remaining === "number" && core.remaining < LIVE_REQUESTS_PER_CHECK + 1) {
        return { ok: false, error: { kind: "rate_limited", resetAt: num(core.reset) } };
      }
    } else if (quota.error.kind === "network" || quota.error.kind === "timeout") {
      return { ok: false, error: quota.error };
    }

    const meta = await get(repoUrl(slug));
    if (!meta.ok) return { ok: false, error: meta.error };
    const repo = repoFactsFrom(meta.data);
    // The rest goes to the name GitHub answered with: a renamed repository's
    // old name would cost a redirect -- a counted request -- on every call.
    const base = repoUrl(parseRepoSlug(repo.fullName) || slug);
    const since = new Date(now.getTime() - VITALITY_WINDOW_DAYS * DAY_MS).toISOString();

    // HEAD and an absent `sha` both mean the default branch, the one the
    // engine reads, with no branch name to encode.
    const [treeRes, commitsRes, pullsRes, parentInUniverse] = await Promise.all([
      get(`${base}/git/trees/HEAD?recursive=1`, LIVE_TREE_TIMEOUT_MS),
      get(`${base}/commits?since=${encodeURIComponent(since)}&per_page=${LIVE_PAGE_SIZE}`),
      get(`${base}/pulls?state=all&sort=created&direction=desc&per_page=${LIVE_PR_SAMPLE}`),
      repo.fork ? parentInUniverseOf(isInUniverse, repo.parentFullName) : Promise.resolve(null),
    ]);
    // The allowance ran out part-way (another tab, a shared network): say so
    // rather than show gates as "can't tell" for a reason the reader can't see.
    const limited = [treeRes, commitsRes, pullsRes].find((r) => !r.ok && r.error.kind === "rate_limited");
    if (limited) return { ok: false, error: limited.error };

    const facts = {
      repo,
      parentInUniverse,
      tree: treeFactsFrom(treeRes),
      commits: commitFactsFrom(commitsRes),
      pulls: pullFactsFrom(pullsRes),
    };
    return {
      ok: true,
      result: {
        repo,
        checks: evaluateLiveGates(facts, now),
        checkedAt: now.toISOString(),
        requests: LIVE_REQUESTS_PER_CHECK + (meta.redirected ? 1 : 0),
      },
    };
  } catch {
    return { ok: false, error: { kind: "network" } };
  }
}
