// copy.js — every user-facing sentence in the Porchlamp v2 UI lives here (06 §6).
// Nothing user-facing is a string literal anywhere else in web/app/: views
// and components import names from this module and interpolate data into
// the templates defined here, never write new prose inline.
//
// tests/test_ui_copy_usage.py enumerates every exported name in this file (via a
// source-level parse, not execution) and asserts each one is referenced
// somewhere under web/app/, so no string here is dead/orphaned copy.
// The document titles of the views are in routes.js, with the addresses they name.

import { projectPath } from "./routes.js";

export const BRAND_NAME = "Porchlamp";
/** The slogan, beside the brand in the header (hidden on narrow screens). */
export const BRAND_TAGLINE = "Find the projects that leave the light on.";

export const NAV_LEADERBOARD = "Leaderboard";
/** The leaderboard view's own heading (visually hidden): the name people search for. */
export const LEADERBOARD_HEADING = "Porchlamp rankings";
export const NAV_NOT_RANKED = "Not ranked";

export const TAB_RANKED = "Ranked";
export const TAB_NOT_RANKED = "Not ranked";

export const SEARCH_PLACEHOLDER = "Search owner/repo…";
export const SEARCH_LABEL = "Search repositories";
export const THEME_TOGGLE_LABEL = "Toggle light/dark theme";
/** The snapshot chip's hover text: the exact benchmark version (a release-candidate tag today) beside the id. */
export function snapshotChipTitle(snapshotId, benchmarkVersion) {
  return `${snapshotId} · benchmark ${benchmarkVersion}`;
}
export const PERSONA_LABEL = "Persona";
export const ECOSYSTEM_LABEL = "Ecosystem";
export const ECOSYSTEM_ALL = "All ecosystems";
/**
 * Each ecosystem's name wherever the site names one, in plain language (owner, 2026-10-17): the app's filter
 * (ECOSYSTEMS, below), and the report and the pages porchlamp/serve/ writes, whose Python reads this map
 * (report_pages.ECOSYSTEM_NAMES). Ids, addresses and data keys keep the engine's codes (porchlamp.models.Ecosystem).
 */
const ECOSYSTEM_NAMES = Object.freeze({
  npm: "JavaScript",
  pypi: "Python",
  crates: "Rust",
  go: "Go",
  maven: "Java",
  systems: "C and C++",
});
/** The filter's options, in the engine's own vocabulary (porchlamp.models.Ecosystem); tests/test_ui_ecosystems.py pins the two together. */
export const ECOSYSTEMS = Object.freeze([
  ["npm", ECOSYSTEM_NAMES.npm],
  ["pypi", ECOSYSTEM_NAMES.pypi],
  ["crates", ECOSYSTEM_NAMES.crates],
  ["go", ECOSYSTEM_NAMES.go],
  ["maven", ECOSYSTEM_NAMES.maven],
  ["systems", ECOSYSTEM_NAMES.systems],
]);
/** An ecosystem's plain name, from the engine's code (a row's `ecosystem`); the code itself where the list has none. */
export function ecosystemName(code) {
  return ECOSYSTEM_NAMES[code] || code;
}

export const PERSONA_NAMES = Object.freeze({
  balanced: "Balanced",
  first_timer: "First-timer",
  career_capital: "Career capital",
  senior_systems: "Senior systems",
  ai_assisted: "AI-assisted",
});

export const COLUMN_RANK = "Rank";
export const COLUMN_REPOSITORY = "Repository";
export const COLUMN_PORCHLAMP = "Score";
export const COLUMN_COVERAGE = "Coverage";
export const COLUMN_TIER = "Tier";
export const COLUMN_QUADRANT = "Quadrant";
export const COLUMN_PILLARS = "Pillars";
export const COLUMN_DELTA = "Δ rank";
export const COLUMN_SUSPICION = "Flags";
export const COLUMN_STARS = "Audited stars";
export const SORT_LABEL = "Sort by";
export const SORT_DIRECTION_TOGGLE_LABEL = "Toggle sort direction";

// U-3: vanity columns (audited stars) are never sortable; this is the
// tooltip that says so wherever such a column header appears.
export const VANITY_TOOLTIP = "audited, not scored";
// The "Audited stars" column is hidden while no row on screen has an audit,
// rather than showing a dash on every row.
export function hasAuditedStars(rows) {
  return (rows || []).some((r) => r && r.stars_audited != null);
}

export const PILLAR_NAMES = Object.freeze({
  responsiveness: "Responsiveness",
  criticality: "Criticality",
  governance: "Governance",
  rigor: "Rigor",
  accessibility: "Accessibility",
});

const PARAM_NAMES = Object.freeze({
  ttfhr_hours: "Time to First Response",
  dr90_decision_rate: "Decision Rate (90d)",
  mr_first_rate: "First-timer Merge Rate",
  gr14_ghost_rate: "PR Ghost Rate (14d)",
  ttm_p90_hours: "Time to Merge (p90)",
  dependents_deduped: "Dependents",
  openssf_criticality: "OpenSSF Criticality",
  downloads_monthly: "Monthly Downloads",
  legal_ip: "Legal IP Regime",
  elephant_factor: "Elephant Factor",
  promotion_ladder: "Promotion Ladder",
  pipeline_defenses: "Pipeline Defenses",
  ai_posture: "AI Policy Posture",
  substantive_review_rate: "Substantive Review Rate",
  newcomer_issue_supply: "Newcomer Issue Supply",
  newcomer_conversion: "Newcomer Conversion",
  rr90_return_rate: "Newcomer Return Rate",
  dx: "Developer Experience",
  first_timer_merge_rate: "First-timer Merge Rate",
  pr_ghost_rate_14d: "PR Ghost Rate (14d)",
  legal_ip_regime: "Legal IP Regime",
  scorecard_pipeline: "Scorecard Pipeline",
  ai_policy_posture: "AI Policy Posture",
  active_gfis: "Good First Issues",
  newcomer_return_rate_90d: "Newcomer Return Rate (90d)",
  bus_factor: "Bus Factor",
  archived_flag: "Archived Flag",
});

export function paramName(paramId) {
  return PARAM_NAMES[paramId] || paramId;
}

export const QUADRANT_LABELS = Object.freeze({
  "Sweet Spot": "Sweet Spot",
  Crucible: "Crucible",
  Nursery: "Nursery",
  Graveyard: "Graveyard",
});

// Color-blind-safe dual encoding (shape + color), spec §5.1.
export const QUADRANT_GLYPHS = Object.freeze({
  "Sweet Spot": "♦",
  Crucible: "⬡",
  Nursery: "●",
  Graveyard: "✕",
});

const TIER_CAPPED_SUFFIX = " (capped)"; // private: only tierLabel needs the literal
/** Why a tier says "(capped)": a statement about the data, never a lower grade. Shown on the pill and in the drawer. */
export const TIER_CAPPED_EXPLANATION =
  "Capped: this tier can go no higher until more of the project's inputs are measured. It reflects how much data there is, not how good the score is.";
export function tierLabel(tier, tierCeiling) {
  if (!tier) return MISSING_VALUE;
  const capped = tierCeiling && tier === tierCeiling;
  return capped ? `${tier}${TIER_CAPPED_SUFFIX}` : tier;
}

const COVERAGE_BAND_LABELS = Object.freeze({
  full: "Full",
  A: "Ceiling A",
  B: "Ceiling B",
});
export function coverageBandForValue(coverage) {
  if (coverage >= 0.95) return "full";
  if (coverage >= 0.8) return "A";
  if (coverage >= 0.6) return "B";
  return "below";
}
export function coverageBadgeText(coverage) {
  const pct = Math.round(coverage * 100);
  const band = coverageBandForValue(coverage);
  const label = COVERAGE_BAND_LABELS[band] || "Below floor";
  return `${pct}% · ${label}`;
}

// U-10: the gate/unrated reason map (06 §3 A2), reused by the drawer.
// "fork" is not in the documented U-10 set but is a valid GateReason on
// the wire.
const GATE_REASON_COPY = Object.freeze({
  mirror: "Mirror of another repository — not an independent project.",
  archived: "Archived on GitHub — no active development to evaluate.",
  unlicensed: "No open-source license — contribution and reuse are not legally clear.",
  stale: "No qualifying human activity in the scoring window.",
  black_hole:
    "More than half of outside pull requests got no human reply, review or merge on GitHub within 14 days.",
  non_software: "Not a software repository (docs, data, or list only).",
  fork: "A fork of another repository, not an independent project.",
});
// A project record carries the engine's own spelling of an unrated reason
// ("unrated: off_platform_routed", porchlamp/core/gates.py); the Not-ranked
// list sends it bare ("off_platform_routed"). Both read the same sentence.
function bareUnratedReason(reason) {
  return typeof reason === "string" ? reason.replace(/^unrated: /, "") : reason;
}
export function gateReasonText(reason) {
  // "unrated: no_external_workflow" is a gate reason on the record, worded
  // as an unrated outcome.
  return GATE_REASON_COPY[reason] || UNRATED_REASON_COPY[bareUnratedReason(reason)] || reason;
}

const UNRATED_REASON_COPY = Object.freeze({
  insufficient_evidence: "Not enough measured evidence to rank",
  no_external_workflow: "No external contribution workflow was found to measure.",
  off_platform_routed:
    "Contribution activity happens off-platform and cannot be measured here.",
});
export function unratedReasonText(reason, coverage) {
  const bare = bareUnratedReason(reason);
  if (bare === "insufficient_evidence") {
    const pct = typeof coverage === "number" ? `${Math.round(coverage * 100)}%` : "unknown";
    return `${UNRATED_REASON_COPY[bare]} (coverage ${pct}, below the 60% floor).`;
  }
  return UNRATED_REASON_COPY[bare] || reason;
}

export const RATED_GROUP_TITLE = "Rated";
export const GATED_GROUP_TITLE = "Gated";
export const UNRATED_GROUP_TITLE = "Unrated";
export const PERSONA_HIDDEN_GROUP_TITLE = "Hidden by this lens";
export const NOT_RANKED_EMPTY = "No gated or unrated repositories to show.";

// Persona gate reasons (02-RANKING-DESIGN.md D-PERS-2/3), reused by the
// leaderboard's persona-hidden disclosure and the drawer's persona section.
// Keys are the engine's own gate codes (porchlamp/core/persona.py:185-188); an
// unknown code falls through to personaGateReason's `|| code`.
const PERSONA_GATE_COPY = Object.freeze({
  ai_hard_ban: "This repository has an explicit ban on AI-assisted contributions.",
  elephant_factor_below_3:
    "Fewer than 3 organizations account for most commits (not multi-vendor).",
  high_barrier_to_entry:
    "This repository does not show a low enough barrier for a first-time contributor.",
  criticality_below_threshold:
    "This repository's production-criticality signal is not yet high enough for career-capital ranking.",
});
// D-PERS-3's exact rendering for any inclusion-gate "unmeasured input"
// reason code (the API sends "insufficient_evidence:<input id>").
const PERSONA_INSUFFICIENT_EVIDENCE_TEXT = "Not enough evidence to show under this lens.";
export function personaGateReason(code) {
  if (!code) return "";
  if (code.startsWith("insufficient_evidence:")) {
    return PERSONA_INSUFFICIENT_EVIDENCE_TEXT;
  }
  const bare = bareUnratedReason(code);
  return PERSONA_GATE_COPY[code] || GATE_REASON_COPY[code] || UNRATED_REASON_COPY[bare] || code;
}
export function personaHiddenDisclosure(count) {
  return `Hidden by this lens (${count})`;
}
/**
 * The other direction of U-4. A lens re-weights coverage, so it can clear
 * the rated floor for repos the balanced view cannot rate at all — 209 of
 * them under `ai_assisted` on snapshot 2026-09-21-L3gp. Saying only
 * "hidden" made that case unsayable.
 */
export function personaExtraDisclosure(count) {
  return `Also visible in this lens (${count})`;
}

// U-5: tie marks and the rank tooltip naming the deciding D-TIE-1 level.
// Levels 6 (TTFHR p75) and 7 (last non-bot commit date) are store-only
// fields not part of the API row contract (06 §4 does not list them), so
// the client can only ever derive levels 1, 2, 3, 4, 5 and 8 — see
// components/table.js's decidingTieLevel.
export const TIE_MARK = "=";
const TIE_LEVEL_LABELS = Object.freeze({
  1: "the composite score",
  2: "coverage",
  3: "the responsiveness pillar",
  4: "friction",
  5: "leverage",
  8: "owner/name (last resort)",
});
export function tieTooltip(level) {
  const label = TIE_LEVEL_LABELS[level] || `tie-break level ${level}`;
  return `Tied on composite score; separated from the row above by ${label}.`;
}

export function emptyLeaderboardText(gatedCount, unratedCount) {
  return `No rated repositories match. ${gatedCount} gated and ${unratedCount} unrated are listed under Not ranked.`;
}

/** The ecosystem filter's label for a value (the value itself when it is not one of ECOSYSTEMS). */
function ecosystemLabel(value) {
  const found = ECOSYSTEMS.find(([v]) => v === value);
  return found ? found[1] : value;
}

/** "No ranked repositories match “q” in go.": says what was searched, so an empty list is never a mystery. */
export function emptyFilteredText(query, ecosystem, what = "ranked") {
  const q = query ? `\u201c${query}\u201d` : "";
  const eco = ecosystem ? `in ${ecosystemLabel(ecosystem)}` : "";
  if (q) return `No ${what} repositories match ${q}${eco ? ` ${eco}` : ""}.`;
  return `No ${what} repositories ${eco}.`;
}
export const CLEAR_FILTERS_LABEL = "Clear filters";
export function searchNotRankedLabel(query) {
  return `Search Not ranked for \u201c${query}\u201d`;
}
export function searchRankedLabel(query) {
  return `Search Ranked for \u201c${query}\u201d`;
}
/** Offered when an empty search is a repository name: opens its drawer, which says whether the snapshot has it. */
export function lookUpRepoLabel(repoKey) {
  return `Look up ${repoKey}`;
}
export const NOT_RANKED_TRUNCATED = "This list is cut short. Search by owner or name to find a specific project.";

// The API's error body carries a `detail` string (`{"error": {endpoint,
// status, detail}}`) explaining WHAT went wrong, not just that it did —
// appended after the endpoint and status wherever it is available
// (`ApiError.detail`, `web/app/api.js`).
function appendSentence(text) {
  const trimmed = text.trim();
  return /[.!?]$/.test(trimmed) ? trimmed : `${trimmed}.`;
}
export function errorBannerText(endpoint, status, detail) {
  const base = `Could not reach ${endpoint} (status ${status}).`;
  const withDetail = detail ? `${base} ${appendSentence(detail)}` : base;
  return `${withDetail} Showing the last good data below.`;
}

const UNMEASURED_REASON_NAMES = Object.freeze({
  third_party_gap: "Third-party data unavailable",
  collector_failure: "Collector failure",
  insufficient_n: "Insufficient sample size",
  not_applicable: "Not applicable",
  no_population: "No reference population",
});

export const MISSING_VALUE = "—";
export function missingValueReason(reason) {
  if (!reason) return "Not measured for this repository.";
  const label = UNMEASURED_REASON_NAMES[reason] || reason;
  return `Not shown: ${label}`;
}

/**
 * Formats a numeric or string parameter value for display in the drawer and
 * comparison table, rounding IEEE-754 floating-point noise (e.g.
 * 0.14040000000000002 -> 0.1404) without altering integers or strings.
 */
export function formatParameterValue(value) {
  if (value === null || value === undefined) return MISSING_VALUE;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return MISSING_VALUE;
    if (Object.is(value, -0) || value === 0) return "0";
    if (Number.isInteger(value)) return String(value);
    if (Math.abs(value) < 0.0001) return String(Number(value.toPrecision(4)));
    return String(Math.round(value * 10000) / 10000);
  }
  return String(value);
}

export const LOADING_LABEL = "Loading…";
export const LOADING_ROW_SR_TEXT = "Loading repositories…";

const SUSPICION_FLAG_COPY = Object.freeze({
  black_hole:
    "Black hole (>50% of external non-bot PRs received zero response within 14 days and 95% one-sided Wilson lower confidence bound > 50% over n >= 10 external PRs)",
});
export const SUSPICION_LABEL = "Suspicion flags";
export function suspicionTooltip(flags) {
  if (!flags || flags.length === 0) return "";
  const formatted = flags.map((f) => (SUSPICION_FLAG_COPY[f] ? `${f} (${SUSPICION_FLAG_COPY[f]})` : f));
  return `Flagged by: ${formatted.join(", ")}`;
}

export const BLACK_HOLE_EVIDENCE_HEADING = "How this Black hole status was determined";
export const BLACK_HOLE_RULE_TEXT =
  "Black hole rule: A repository is classified as a Black hole only when at least 10 eligible external non-bot PRs were observed for 14+ days in the 180-day window and the 95% one-sided Wilson lower confidence bound on the 14-day unanswered share exceeds 50%.";
export const BLACK_HOLE_EXCLUSIONS_TEXT =
  "False-positive protections: Draft PRs (until marked ready for review), PRs closed by their own author before any maintainer response, and PRs from accounts under 90 days old are excluded. A maintainer review, >= 40-character comment, merge, or PR close within 14 days counts as a response.";
/** What the "no response" count counted, ending the sentence that starts with "N of M (P%)". */
export const BLACK_HOLE_STAT_PREDICATE =
  "external non-bot PRs had no maintainer response, merge, or close within 14 days";
/** The same, when this snapshot did not record a maintainer close: only a review, comment or merge was counted. */
export const BLACK_HOLE_STAT_PREDICATE_CLOSE_NOT_COUNTED =
  "external non-bot PRs had no maintainer review, comment or merge within 14 days (a close by a maintainer was not counted)";
export const BLACK_HOLE_RULE_INPUTS_NOT_MEASURED_TEXT =
  "The data of this snapshot was collected before drafts, self-closed pull requests and closes by a maintainer were recorded, so those two exclusions were not applied and a maintainer close was not counted as a response.";
export const BLACK_HOLE_RECHECK_TEXT =
  "Automatic recovery: Porchlamp re-evaluates the 180-day PR window whenever repository activity moves. If this project reviews patches off-GitHub (mailing list, Gerrit, Phabricator), open a re-check issue to have it judged by the off-GitHub review rule.";

const BLACK_HOLE_RESPONSE_KIND_LABELS = Object.freeze({
  review_or_comment: "review or comment",
  merged: "merged",
  closed_by_maintainer: "closed by maintainer",
});

export function blackHolePrResponseText(answeredWithin14d, responseKind) {
  if (!answeredWithin14d) return "No response within 14d";
  if (!responseKind || responseKind === "none") return "Answered";
  const label = BLACK_HOLE_RESPONSE_KIND_LABELS[responseKind] || String(responseKind).replace(/_/g, " ");
  return `Answered (${label})`;
}



export const ROW_OPEN_HINT = "Press Enter to view why this score";

// Pagination copy: a page-size dropdown (20/50/100/500/1000) plus
// first/previous/next/last controls and a "Page i of N (M rows)"
// indicator, on both the Ranked and Not-ranked lists.
export const PAGE_SIZE_LABEL = "Rows per page";
export const PAGER_FIRST_LABEL = "First page";
export const PAGER_PREV_LABEL = "Previous page";
export const PAGER_NEXT_LABEL = "Next page";
export const PAGER_LAST_LABEL = "Last page";
export function pagerStatusText(page, totalPages, totalRows) {
  return `Page ${page} of ${totalPages} (${totalRows} rows)`;
}

export function reprojectedAnnouncement(personaName, count) {
  return `Re-projected under ${personaName}: ${count} rows moved.`;
}
// Sort encoding contract (mirrored exactly by
// components/table.js's sortDirectionLabel and
// scripts/serve_ui_fixtures.py's docstring): `sort=<field>` means that
// field's own "best first" direction — descending for every score/
// coverage-like field, ascending for rank/tier. A leading "-" reverses it.
// First click on an unsorted column applies the default; a second click
// on the SAME column toggles the "-" prefix.
export function sortedAnnouncement(columnLabel, direction) {
  return direction ? `Sorted by ${columnLabel} (${direction}).` : `Sorted by ${columnLabel}.`;
}

export const KEYMAP_TITLE = "Keyboard shortcuts";
export const KEYMAP_CLOSE_LABEL = "Close";
export const KEYMAP_ENTRIES = Object.freeze([
  { keys: "/", action: "Focus search" },
  { keys: "J / K", action: "Move to next / previous row or marker" },
  { keys: "Enter", action: "Open the selected row's or marker's drawer" },
  { keys: "Esc", action: "Close the drawer or an overlay" },
  { keys: "] / [", action: "Next / previous page" },
  { keys: "1 – 4", action: "Toggle a matrix quadrant (Sweet Spot / Crucible / Nursery / Graveyard)" },
  { keys: "R", action: "Reset the matrix zoom/pan view" },
  { keys: "↑ ↓ ← →", action: "On the matrix: move focus to the nearest marker in that direction" },
  { keys: "L", action: "Go to Leaderboard" },
  { keys: "B", action: "Go to Platform Matrix" },
  { keys: "C", action: "Go to Compare" },
  { keys: "W", action: "Go to Configure weights" },
  { keys: "M", action: "Go to Methodology" },
  { keys: "P", action: "Open the persona menu" },
  { keys: "T", action: "Toggle light/dark theme" },
  { keys: "?", action: "Open this keyboard shortcuts overlay" },
]);

export const DRAWER_TITLE = "Why this score";
// A project that was disqualified before scoring (GATED) has no score, tier or quadrant, so its page and panel
// draw the reason sentence and no Composite (views/project.js).
export const DRAWER_SECTION_COMPOSITE = "Composite";
export const DRAWER_SECTION_PERSONAS = "Persona view";
export const DRAWER_SECTION_INTEGRITY = "Integrity";
export const DRAWER_CLOSE_LABEL = "Close";
export const DRAWER_COMPARE_LABEL = "Compare";
/** The link from a ranked project's side panel to its own page (the same address, loaded as a page). */
export const DRAWER_PAGE_LABEL = "Full page →";
export const DRAWER_NO_EVIDENCE_TEXT = "No detailed evidence telemetry recorded for this repository.";
export const DRAWER_LOADING_TEXT = "Loading evidence…";
export const DRAWER_NOT_FOUND_TEXT = "This repository could not be found in the current snapshot.";

// --------------------------------------------------------------------- //
// Live check: an opt-in look at public GitHub data for a repository the
// snapshot does not hold (06 §3 C; web/app/livecheck.js). Every sentence
// names only what the check itself read -- never a score, a rank or a tier.
// --------------------------------------------------------------------- //
export function liveNotInSnapshotText(repoKey, snapshotId) {
  return snapshotId
    ? `${repoKey} is not in the ${snapshotId} snapshot, so Porchlamp has no score for it.`
    : `${repoKey} is not in this snapshot, so Porchlamp has no score for it.`;
}
export const LIVE_CHECK_HEADING = "Live check from GitHub";
export const LIVE_CHECK_BUTTON_LABEL = "Check it live on GitHub";
export function liveCheckNoteText(requests) {
  return `Your browser asks GitHub directly: ${requests} of the 60 hourly requests GitHub allows your network without signing in (plus a free allowance check, and 1 more if the repository was renamed). Nothing is sent to Porchlamp.`;
}
export const LIVE_CHECK_LOADING_TEXT = "Asking GitHub\u2026";
export const LIVE_CHECK_DISCLAIMER =
  "Not a Porchlamp score, and not part of the verified snapshot: these are the eligibility checks your browser can run on public GitHub data right now. Scoring and the black-hole check need the full pipeline.";
export function liveCheckedAtText(isoTime, requests) {
  const when = typeof isoTime === "string" && isoTime.length >= 16 ? `${isoTime.slice(0, 10)} ${isoTime.slice(11, 16)} UTC` : MISSING_VALUE;
  return `Checked ${when}, spending ${requests} of your hourly requests to api.github.com (plus a free allowance check).`;
}
export function liveRenamedText(fullName) {
  return `GitHub knows this repository as ${fullName}.`;
}
export function liveOpenCanonicalLabel(fullName) {
  return `Open ${fullName}`;
}
//: The gate names the Methodology page shows (methodology.json `gates[].name`).
export const LIVE_GATE_NAMES = Object.freeze({
  "D-GATE-1": "Software project",
  "D-GATE-2": "Not archived, not a mirror, not a fork",
  "D-GATE-3": "Licensed",
  "D-GATE-4": "Vitality",
  "D-GATE-5": "Not a black hole",
  "D-GATE-6": "PR workflow",
});
export const LIVE_STATUS_LABELS = Object.freeze({
  pass: "Passes",
  fail: "Fails",
  undecided: "Can't tell",
  routed: "Reviewed off GitHub",
  not_checked: "Not checked live",
});
/** One sentence per `livecheck.js::evaluateLiveGates` reason code, from the facts in `detail`. */
export function liveReasonText(reason, detail = {}) {
  switch (reason) {
    case "manifest_found":
      return `Found ${detail.path}, a recognised build or package manifest.`;
    case "no_manifest":
      return `No recognised build or package manifest within ${detail.depth} folders of the top level.`;
    case "tree_truncated":
      return "GitHub sent only part of the file list, and no manifest was in that part.";
    case "tree_unreadable":
      return "GitHub did not return the file list.";
    case "original":
      return "Not archived, not a mirror and not a fork.";
    case "archived":
      return "Archived on GitHub.";
    case "mirror":
      return "A mirror of a repository hosted elsewhere.";
    case "fork_parent_in_universe":
      return detail.parent
        ? `A fork of ${detail.parent}, which Porchlamp already covers.`
        : "A fork of a repository Porchlamp already covers.";
    case "fork_parent_outside":
      return detail.parent
        ? `A fork of ${detail.parent}, which this snapshot does not hold under that name, so the fork counts on its own.`
        : "A fork whose original repository this snapshot does not hold under that name, so the fork counts on its own.";
    case "fork_parent_unknown":
      return detail.parent
        ? `A fork of ${detail.parent}; could not tell whether Porchlamp covers the original.`
        : "A fork; GitHub did not name the original repository, so the check could not tell whether Porchlamp covers it.";
    case "osi_spdx":
      return `${detail.spdx}, an OSI-approved license.`;
    case "license_file":
      return `GitHub could not name the license, but ${detail.file} is at the top level.`;
    case "not_osi":
      return `${detail.spdx} is not an OSI-approved license.`;
    case "no_license":
      return "No license GitHub recognises, and no LICENSE or COPYING file at the top level.";
    case "license_unknown":
      return detail.truncated
        ? "GitHub could not name the license, and the file list was too incomplete to look for a license file."
        : "GitHub could not name the license, and GitHub did not return the file list to look for a license file.";
    case "human_commits": {
      const noun = detail.count === 1 && !detail.more ? "commit" : "commits";
      return `${detail.count}${detail.more ? "+" : ""} ${noun} by people on the default branch in the last ${detail.days} days.`;
    }
    case "no_human_commits":
      return `No commits by people on the default branch in the last ${detail.days} days.`;
    case "commits_partial":
      return `The ${detail.pageSize} most recent commits are all by bots, and the check reads no further.`;
    case "commits_unreadable":
      return "GitHub did not return the commit history.";
    case "needs_pipeline":
      return "Needs the replies and reviews on every outside pull request, which only the full pipeline reads.";
    case "decided_external_pr": {
      const caveat = detail.handoffPossible
        ? " (Passes unless PR comments hand review off to another tool such as Gerrit, which is not checked live.)"
        : "";
      return detail.bot
        ? `Pull request #${detail.number}, from a bot account outside the maintainer team, was merged or closed in the last ${detail.days} days; the snapshot's rule counts bot pull requests too.${caveat}`
        : `Pull request #${detail.number}, from an outside contributor, was merged or closed in the last ${detail.days} days.${caveat}`;
    }
    case "off_platform_marker":
      return `None of the ${detail.sample || 30} newest pull requests is an outside one decided in the last ${detail.days || 365} days, and the project's patch-submission guide sends contributions to another review process.`;
    case "no_external_pr":
      return `None of the ${detail.sample || 30} newest pull requests is from an outside contributor and merged or closed in the last ${detail.days || 365} days.`;
    case "prs_handoff_unknown":
      return `None of the ${detail.sample || 30} newest pull requests is an outside one decided in the last ${detail.days || 365} days, and whether PR comments hand review off to another tool such as Gerrit is not checked live.`;
    case "prs_marker_unknown":
      return detail.truncated
        ? `None of the ${detail.sample || 30} newest pull requests is an outside one decided in the last ${detail.days || 365} days, and the file list was too incomplete to look for another review process.`
        : `None of the ${detail.sample || 30} newest pull requests is an outside one decided in the last ${detail.days || 365} days, and GitHub did not return the file list to look for another review process.`;
    case "prs_unreadable":
      return "GitHub did not return the pull requests.";
    default:
      return MISSING_VALUE;
  }
}
export const LIVE_FACT_LABELS = Object.freeze({
  stars: "Stars (GitHub's count, not audited)",
  language: "Main language",
  created: "Created",
  pushed: "Last push",
  license: "License (SPDX)",
});
/** Why a live check could not run; `nowMs` turns a rate-limit reset into minutes. */
export function liveErrorText(error, nowMs = Date.now()) {
  const kind = error && error.kind;
  if (kind === "rate_limited") {
    if (error.resetAt) {
      const minutes = Math.max(1, Math.ceil((error.resetAt * 1000 - nowMs) / 60000));
      return `GitHub's hourly allowance for your network is used up. It resets in about ${minutes} minute${minutes === 1 ? "" : "s"}.`;
    }
    return "GitHub's hourly allowance for your network is used up. Try again later.";
  }
  if (kind === "not_found") return "GitHub has no public repository by this name.";
  if (kind === "unavailable") return "GitHub has made this repository unavailable.";
  if (kind === "timeout") return "GitHub did not answer in time.";
  if (kind === "network") return "Could not reach GitHub.";
  if (kind === "http") {
    return typeof error.status === "number" && error.status >= 400
      ? `GitHub answered with an error (status ${error.status}).`
      : "GitHub returned an unreadable response.";
  }
  return "This is not a repository name GitHub accepts.";
}
export function liveCheckAnnouncement(outcome) {
  if (!outcome || !outcome.ok) return liveErrorText(outcome && outcome.error);
  // D-GATE-5 is never decided live: counting it would read as a failure that never happened.
  const run = outcome.result.checks.filter((c) => c.status !== "not_checked");
  const passed = run.filter((c) => c.status === "pass" || c.status === "routed").length;
  const undecided = run.filter((c) => c.status === "undecided").length;
  if (undecided > 0) {
    return `Live check done: ${passed} of ${run.length} checks pass (${undecided} can't tell).`;
  }
  return `Live check done: ${passed} of ${run.length} checks pass.`;
}
export function drawerErrorText(endpoint, status, detail) {
  const base = `Could not load evidence from ${endpoint} (status ${status}).`;
  return detail ? `${base} ${appendSentence(detail)}` : base;
}
// Shared by every retryable fetch banner (evidence 404, snapshot
// 5xx/timeout — U-15: every request has an error state, and every
// error state offers a way back).
export const RETRY_LABEL = "Retry";  // every retry button in the app: the footer,
// the evidence panel and the repo-detail panel all offer the same action (U-15).
export const RAW_HASH_LABEL = "Raw hash";
export const SNAPSHOT_ID_LABEL = "Snapshot";
export const SUSPICION_NONE_TEXT = "No suspicion flags on this repository.";
export const RATED_LABEL = "Rated";
export const NOT_RATED_LABEL = "Not rated under this lens";
export const COORDINATES_LABEL = "Coordinates";

export const FOOTER_SNAPSHOT_LABEL = "Snapshot";
export const FOOTER_VERSION_LABEL = "Benchmark version";
export const FOOTER_CONSTANTS_HASH_LABEL = "Constants hash";
export const FOOTER_MANIFEST_ROOT_LABEL = "Manifest root";

export const SKIP_LINK_LABEL = "Skip to main content";

// --- An address no view answers (404.html runs the app there) -------------------------------------------
export const NOT_FOUND_HEADING = "Page not found";
export function notFoundText(path) {
  return `Porchlamp has no page at ${path}.`;
}
export const NOT_FOUND_SEARCH_LABEL = "Search the ranked projects";
export const NOT_FOUND_SEARCH_BUTTON = "Search";
export const NOT_FOUND_HOME_LINK = "See the rankings";
export const MAIN_LANDMARK_LABEL = "Porchlamp leaderboard and repository detail";


export function methodologyPercentText(fraction) {
  if (fraction === null || fraction === undefined) return MISSING_VALUE;
  return `${Math.round(fraction * 100)}%`;
}
export function methodologyIntText(value) {
  return value === null || value === undefined ? MISSING_VALUE : String(value);
}
export function methodologyWindowDaysText(windowDays) {
  return windowDays === null || windowDays === undefined ? MISSING_VALUE : `${windowDays} days`;
}
export function methodologyDomainRangeText(domain) {
  if (!domain || (domain.kind !== "numeric" && domain.kind !== "ecdf")) return "";
  const direction = domain.higher_is_better ? "higher is better" : "lower is better";
  return `[${domain.domain_min}, ${domain.domain_max}] · ${direction}`;
}
export function methodologyEnumOrderText(domain) {
  if (!domain || domain.kind !== "enum") return "";
  if (domain.values_worst_to_best) return `worst → best: ${domain.values_worst_to_best.join(" → ")}`;
  if (domain.persona_orders) {
    return Object.entries(domain.persona_orders)
      .map(([persona, order]) => `${persona}: worst → best: ${order.join(" → ")}`)
      .join("; ");
  }
  return "";
}


export const METHODOLOGY_MANIFEST_ROOT_LABEL = "Manifest root";
export const METHODOLOGY_CONSTANTS_HASH_LABEL = "Constants hash";
export const METHODOLOGY_CODE_COMMIT_LABEL = "Code commit";
export const METHODOLOGY_CANONICAL_DUMP_HASH_LABEL = "Canonical dump hash";
export const METHODOLOGY_OPENSSF_PROXY_MIN_SIGNALS_LABEL = "OpenSSF proxy minimum signals";

export function apiErrorAnnouncement(endpoint, status) {
  return errorBannerText(endpoint, status);
}

// --------------------------------------------------------------------- //
// Primary navigation (06 §2 — "Primary tabs: Leaderboard (A) ·
// Matrix (B) · Compare (E) · Configure weights (D) · Methodology (F)").
// Distinct from TAB_RANKED/TAB_NOT_RANKED above, which are the
// Leaderboard screen's own Ranked/Not-ranked SUB-tabs.
// --------------------------------------------------------------------- //
export const PRIMARY_NAV_LEADERBOARD = "Leaderboard";
export const PRIMARY_NAV_MATRIX = "Matrix";
export const PRIMARY_NAV_COMPARE = "Compare";
export const PRIMARY_NAV_CONFIGURE = "Configure weights";
export const PRIMARY_NAV_METHODOLOGY = "Methodology";
export const PRIMARY_NAV_LABEL = "Primary";

// --------------------------------------------------------------------- //
// Screen B: Matrix
// --------------------------------------------------------------------- //
export const MATRIX_HEADING = "Matrix";
export const MATRIX_AXIS_X_LABEL = "Friction";
export const MATRIX_AXIS_Y_LABEL = "Leverage";
export function matrixSvgAriaLabel(xLabel, yLabel) {
  return `${xLabel}/${yLabel} matrix`;
}
export const MATRIX_LOADING_TEXT = "Loading the matrix…";
export const MATRIX_EMPTY_TEXT = "No rated repositories to plot.";
export function matrixErrorText(endpoint, status, detail) {
  const base = `Could not load the matrix from ${endpoint} (status ${status}).`;
  return detail ? `${base} ${appendSentence(detail)}` : base;
}
export function matrixExcludedCaption(excludedCount) {
  return `${excludedCount} ${excludedCount === 1 ? "repository is" : "repositories are"} not ranked and not plotted.`;
}

// The caption SENTENCE is composed here, not on the wire (06 §6: "every
// sentence in copy.js") — the API carries
// `axis_partial_count`/`axis_partial_share` numbers, never prose.
// `axisName` is "leverage"/"friction"; `entry` is one
// `MatrixResponse.axis_coverage` value (`{pillar, axis_partial_count,
// axis_partial_share}`) or `undefined`/`null` (no caption needed).
export function matrixAxisCoverageCaption(axisName, entry) {
  if (!entry) return null;
  const pillarLabel = PILLAR_NAMES[entry.pillar] || entry.pillar;
  const pct = Math.round(entry.axis_partial_share * 100);
  return `${axisName} computed without ${pillarLabel} for ${pct}% of plotted repositories.`;
}
export const MATRIX_UNAUDITED_STARS_NOTE =
  "Marker size is audited stars, radius 4\u201312px on a square-root scale; repositories with no audited-star figure are drawn at a default 6px marker (unaudited).";
export const MATRIX_NO_STARS_NOTE = "Every dot is the same size: no project in this snapshot has an audited star count.";
// The axes are core/matrix.py's D-MAT-1: leverage = 0.50 criticality + 0.30 rigor + 0.20 governance; friction =
// 0.50 (100 - responsiveness) + 0.30 (100 - governance) + 0.20 (100 - accessibility); the split is 50 on both.
export const MATRIX_HOW_TO_READ =
  "Each dot is a ranked project: select one to see why it scored as it did, or use the arrow keys to move between neighbouring dots. Leverage rises with criticality, rigor and governance. Friction rises when responses are slow, governance is weak and newcomers get little help. Both axes split at 50.";
export const MATRIX_STAGE_LABEL = "Use the arrow keys to move between projects and Enter to open one.";
export const QUADRANT_DESCRIPTIONS = Object.freeze({
  "Sweet Spot": "High leverage, low friction",
  Crucible: "High leverage, high friction",
  Nursery: "Low leverage, low friction",
  Graveyard: "Low leverage, high friction",
});
export const MATRIX_RESET_VIEW_LABEL = "Reset view";
export const MATRIX_ZOOM_IN_LABEL = "Zoom in";
export const MATRIX_ZOOM_OUT_LABEL = "Zoom out";
export function matrixQuadrantToggleLabel(quadrantName) {
  return `Toggle ${quadrantName}`;
}
export const MATRIX_OPEN_HINT = "Press Enter to view why this score";
export const MATRIX_SPOTLIGHT_HEADING = "Quadrant breakdown & top repositories";
// The quadrant buttons are toggles (aria-pressed), exactly like the legend chips and keys 1-4: they show or hide.
export const MATRIX_SPOTLIGHT_SUBTITLE =
  "Select a quadrant to show or hide it on the plot, or pick a repository to see why it scored as it did.";
// Worded only from the D-MAT-1 axes above (leverage: criticality, rigor, governance; friction: slow responses,
// weak governance, little newcomer help) -- no claim the formula does not make.
export const MATRIX_QUADRANT_GUIDANCE = Object.freeze({
  "Sweet Spot":
    "Critical, rigorous projects that also respond quickly and help newcomers: an outside contribution here is likely to be answered and to matter.",
  Crucible:
    "High-leverage projects where slow responses, weak governance or little help for newcomers make an outside contribution harder to land.",
  Nursery:
    "Lower-leverage projects that respond quickly and help newcomers: a gentle place for a first contribution.",
  Graveyard:
    "Lower-leverage projects where responses are slow, governance is weak or newcomers get little help.",
});
export const MATRIX_TOP_REPOS_LABEL = "Highest Porchlamp score";
export const MATRIX_QUADRANT_EMPTY = "No repositories in this quadrant match the current filter.";
export function matrixQuadrantCountText(count, total) {
  const pct = total > 0 ? Math.round((count / total) * 100) : 0;
  return `${count} ${count === 1 ? "repo" : "repos"} · ${pct}%`;
}
export function matrixPlottedCountText(visibleCount, totalRated) {
  if (visibleCount === totalRated) {
    return `${totalRated} rated ${totalRated === 1 ? "repository" : "repositories"} plotted`;
  }
  return `Showing ${visibleCount} of ${totalRated} rated ${totalRated === 1 ? "repository" : "repositories"}`;
}

// --------------------------------------------------------------------- //
// Screen D: Configure weights
// --------------------------------------------------------------------- //
export const CONFIGURE_HEADING = "Configure weights";
// U-11: a custom weight vector is labelled "custom" everywhere it is
// applied and is NEVER called a persona.
export const CONFIGURE_CUSTOM_LABEL = "Custom";
export const CONFIGURE_LOCK_LABEL = "Lock";
const CONFIGURE_SUM_LABEL = "Sum"; // private: only configureSumText needs the literal
export function configureSumText(sum) {
  return `${CONFIGURE_SUM_LABEL}: ${(sum * 100).toFixed(0)}%`;
}
export const CONFIGURE_PRESETS_LABEL = "Start from a persona preset";
export const CONFIGURE_DELTA_HEADING = "Rank deltas vs balanced";
export const CONFIGURE_DELTA_EMPTY = "No re-projection yet — adjust a slider to see rank deltas.";
export const CONFIGURE_TOP_MOVERS_UP = "Top movers up";
export const CONFIGURE_TOP_MOVERS_DOWN = "Top movers down";
export function configureDeltaText(row) {
  if (row.balanced_rank === null || row.balanced_rank === undefined) {
    return `• ${row.owner}/${row.name}: new → ${row.rank}`;
  }
  const delta = row.balanced_rank - row.rank;
  const sign = delta > 0 ? "▲" : delta < 0 ? "▼" : "•";
  return `${sign} ${row.owner}/${row.name}: ${row.balanced_rank} → ${row.rank}`;
}
export const CONFIGURE_LOADING_TEXT = "Re-projecting…";
export const CONFIGURE_EMPTY_TEXT = "No repositories are rated under this weight vector.";
export function configureErrorText(endpoint, status, detail) {
  const base = `Could not reproject from ${endpoint} (status ${status}).`;
  return detail ? `${base} ${appendSentence(detail)}` : base;
}
export const CONFIGURE_RETRY_LABEL = "Retry";

// --------------------------------------------------------------------- //
// Screen E: Compare
// --------------------------------------------------------------------- //
export const COMPARE_HEADING = "Compare";
export const COMPARE_SEARCH_PLACEHOLDER = "Add a repository (owner/repo)…";
export const COMPARE_SEARCH_LABEL = "Search repositories to compare";
export const COMPARE_ADD_LABEL = "Add to comparison";
export const COMPARE_REMOVE_LABEL = "Remove from comparison";
export const COMPARE_CLEAR_ALL_LABEL = "Clear all";
export const COMPARE_EMPTY_TEXT = "Add 2 to 4 repositories to compare them side by side.";
export const COMPARE_MIN_TEXT = "Add at least one more repository (2 minimum).";
export const COMPARE_MAX_TEXT = "You can compare up to 4 repositories at a time.";
export const COMPARE_LOADING_TEXT = "Loading comparison…";
export function compareErrorText(endpoint, status, detail) {
  const base = `Could not load a compared repository from ${endpoint} (status ${status}).`;
  return detail ? `${base} ${appendSentence(detail)}` : base;
}
export const COMPARE_BETTER_MARK = "★";
export function compareBetterTitle(paramLabel) {
  return `Better ${paramLabel}`;
}
export function compareEvidenceSourceTitle(source, fetchedAt) {
  if (!source) return missingValueReason();
  return fetchedAt ? `${source} (fetched ${fetchedAt})` : source;
}
export const COMPARE_VERDICT_HEADING = "Verdict";
export function compareVerdictText(winnerKey, measuredCount, totalParams) {
  return `Based on ${measuredCount} of ${totalParams} parameters measured on every compared repository, ${winnerKey} leads.`;
}
export function compareVerdictTieText(measuredCount, totalParams) {
  return `Based on ${measuredCount} of ${totalParams} parameters measured on every compared repository, the comparison is tied.`;
}
export function compareVerdictNoDataText() {
  return "No parameter is measured on every compared repository — a verdict cannot be computed.";
}
/** A compared repository with NO measured parameter (e.g. a gated one) is left out of the verdict, by name. */
export function compareExcludedReposText(repoKeys) {
  const names =
    repoKeys.length <= 2 ? repoKeys.join(" and ") : `${repoKeys.slice(0, -1).join(", ")} and ${repoKeys[repoKeys.length - 1]}`;
  return repoKeys.length === 1
    ? `${names} has no measured parameters in this snapshot, so the verdict leaves it out.`
    : `${names} have no measured parameters in this snapshot, so the verdict leaves them out.`;
}
export function compareSubsetVerdictText(winnerKey, measuredCount, totalParams, subsetCount) {
  return `Based on ${measuredCount} of ${totalParams} parameters measured on the other ${subsetCount} repositories, ${winnerKey} leads.`;
}
export function compareSubsetTieText(measuredCount, totalParams, subsetCount) {
  return `Based on ${measuredCount} of ${totalParams} parameters measured on the other ${subsetCount} repositories, the comparison is tied.`;
}
export function compareUnmeasuredNote(paramLabels) {
  if (paramLabels.length === 0) return "";
  return `Not compared (unmeasured on at least one repository): ${paramLabels.join(", ")}.`;
}
export const COMPARE_ROW_IDENTITY = "Repository";
export const COMPARE_ROW_GATE_STATUS = "Gate status";
export const COMPARE_ROW_COMPOSITE = "Score";
export const COMPARE_ROW_COVERAGE = "Coverage";
export const COMPARE_RATED_STATUS = "Rated";
export const COMPARE_UNMEASURED_LABEL = "Unmeasured";
export const COMPARE_PRESETS_HEADING = "Quick-start comparisons";
export const COMPARE_PRESETS_SUBTITLE =
  "Load a set of well-known projects to compare, or search above to build your own.";
// Examples only: each description says what the projects ARE, never how they rank, which quadrant they sit in or
// whether they are gated. Those are per-snapshot facts the comparison itself shows; a hard-coded "top-ranked" or
// "black hole" label goes false on the next snapshot. Every repository here is in the 2026-10-14 universe.
export const COMPARE_PRESETS = Object.freeze([
  {
    id: "rust_foundations",
    label: "Rust foundations",
    description: "The Rust compiler, the Tokio async runtime and the PyO3 Python bindings.",
    repos: ["rust-lang/rust", "tokio-rs/tokio", "PyO3/pyo3"],
  },
  {
    id: "python_data",
    label: "Python data stack",
    description: "Array computing, DataFrames and machine learning: NumPy, pandas and scikit-learn.",
    repos: ["numpy/numpy", "pandas-dev/pandas", "scikit-learn/scikit-learn"],
  },
  {
    id: "web_frameworks",
    label: "JavaScript web frameworks",
    description: "Next.js, Vue and Svelte, side by side.",
    repos: ["vercel/next.js", "vuejs/core", "sveltejs/svelte"],
  },
]);
export const COMPARE_SUGGESTIONS_HEADING = "Or add one of these";
export const COMPARE_SUGGESTIONS = Object.freeze([
  "tokio-rs/tokio",
  "scikit-learn/scikit-learn",
  "kubernetes/kubernetes",
  "vercel/next.js",
  "prometheus/prometheus",
  "astral-sh/uv",
]);
export const COMPARE_CARDS_HEADING = "Side-by-side summary";
export const COMPARE_WINS_LABEL = "Parameter wins";
export function compareWinScoreText(wins, totalMeasured) {
  return `${wins} / ${totalMeasured} won`;
}
export const COMPARE_DIRECTION_HIGHER = "↑ higher is better";
export const COMPARE_DIRECTION_LOWER = "↓ lower is better";
export const COMPARE_DIRECTION_ENUM = "scale";

// --------------------------------------------------------------------- //
// Screen F: Methodology and snapshot
// --------------------------------------------------------------------- //
export const METHODOLOGY_HEADING = "Methodology and snapshot";
/** "Why Porchlamp", above the methodology: the one idea, the finding behind it, and where the numbers come from. */
export const WHY_HEADING = "Why Porchlamp";
export const WHY_KICKERS = Object.freeze([
  "The Idea",
  "The Finding",
  "The Evidence",
]);
//: "The Finding", a template: report.json's values fill the slots (views/leaderboard.js `fillFromReport`), each a hook of its own id.
//: Without a report the paragraph is left out.
export const WHY_FINDING_TEMPLATE =
  "Stars do not review pull requests. In our measurements more than 1 in {one_in} active projects leave most pull requests from outside contributors without a human reply, review or merge for {deadline_days} days, and the more stars a project has, the more likely it is to be one of them. We call these projects black holes; they are listed under Not ranked, never ranked.";
export const WHY_PARAGRAPHS = Object.freeze([
  "A porch lamp left on tells a visitor they are expected. Porchlamp ranks open-source projects the same way: by what happens to a stranger's pull request, not by how many stars the project has.",
  WHY_FINDING_TEMPLATE,
  "Every number comes from public GitHub activity: how fast a person answered outside contributors, how many of their pull requests reached a decision, whether first-time contributors came back, and how much other software depends on the project.",
]);
export const METHODOLOGY_LOADING_TEXT = "Loading the methodology…";
export function methodologyErrorText(endpoint, status, detail) {
  const base = `Could not load the methodology from ${endpoint} (status ${status}).`;
  return detail ? `${base} ${appendSentence(detail)}` : base;
}
export const METHODOLOGY_EMPTY_TEXT = "No methodology document is available for this snapshot.";
export const METHODOLOGY_SECTION_PILLARS = "Pillars";
export const METHODOLOGY_SECTION_PARAMETERS = "Parameters";
export const METHODOLOGY_SECTION_GATES = "Gates";
export const METHODOLOGY_SECTION_MISSINGNESS = "Missingness";
export const METHODOLOGY_SECTION_TIE_BREAK = "Tie-breakers";
export const METHODOLOGY_SECTION_PERSONAS = "Personas";
export const METHODOLOGY_SECTION_DETECTORS = "Detectors";
export const METHODOLOGY_SECTION_PROCESS_CONSTANTS = "Process constants";
export const METHODOLOGY_SECTION_RFC_LOG = "Change log";
export const METHODOLOGY_SECTION_SNAPSHOT = "Snapshot";
export const METHODOLOGY_SECTION_LIMITS = "Limits";
export const METHODOLOGY_COL_WEIGHT = "Weight";
export const METHODOLOGY_COL_MIN_N = "Min n";
export const METHODOLOGY_COL_WINDOW = "Window";
export const METHODOLOGY_COL_CURVE = "Curve";
export const METHODOLOGY_COL_PILLAR = "Pillar";
export const METHODOLOGY_COL_REASONS = "Reasons";
export const METHODOLOGY_COL_DIRECTION = "Direction";
export const METHODOLOGY_COL_MODULE = "Module";
export const METHODOLOGY_COL_VALUE = "Value";
export const METHODOLOGY_COL_GATES = "Gates";
export const METHODOLOGY_COL_HASH_CHANGE = "Hash change";
export const METHODOLOGY_COL_PARAMETER = "Parameter";
export const METHODOLOGY_COL_NAME = "Name";
export const METHODOLOGY_COL_DOMAIN = "Domain";
export const METHODOLOGY_COL_PROXY = "Proxy";
export const METHODOLOGY_COL_CUTOFFS = "Cutoffs";
export const METHODOLOGY_COL_THRESHOLDS = "Thresholds";
export const METHODOLOGY_COL_PERSONA = "Persona";
export const METHODOLOGY_REPRODUCE_LABEL = "Reproduce command";
export const METHODOLOGY_COPY_LABEL = "Copy";
export const METHODOLOGY_COPIED_LABEL = "Copied";
// The lines below are templates: each {slot} is filled with a value from the payload, inside the hook the
// verifier reads it back from (components/fill.js).
export const METHODOLOGY_BENCHMARK_LINE = "Benchmark {benchmark_version} · schema {schema_version} · constants hash {constants_hash}…";
export const METHODOLOGY_SNAPSHOT_LINE = "{snapshot_id} · as of {as_of}";
/** How a project's own record is found: its page's address, or the search on the check page ({check_page}). */
export const METHODOLOGY_CHECK_LINE =
  "To check a project, open https://porchlamp.org/projects/<owner>/<name>/ (lower case), or search on the {check_page}.";
export const METHODOLOGY_CHECK_LINK_LABEL = "check page";
/** The digest of the database file this snapshot was published from (the export's manifest.json). */
export const METHODOLOGY_DB_SHA256_LABEL = "Snapshot database SHA-256";
/** The first characters of a digest, where a line names one without printing all of it. */
export function shortHashText(hash) {
  return String(hash).slice(0, 12);
}
export const METHODOLOGY_NO_PROXY_TEXT = "No proxy path.";
export function methodologyProxyText(proxy) {
  const parts = [];
  if (typeof proxy.proxy_min_n === "number") parts.push(`min n ${proxy.proxy_min_n}`);
  if (typeof proxy.proxy_min_signals === "number") parts.push(`min signals ${proxy.proxy_min_signals}`);
  return parts.length ? parts.join(", ") : METHODOLOGY_NO_PROXY_TEXT;
}

// --- Screen F document layout (redesign 2026-09-22) ----------------------
export const METHODOLOGY_INDEX_LABEL = "On this page";
/**
 * One sentence under each section heading saying what the section answers.
 * Keys are the section keys in views/methodology.js. The detectors line is
 * sourced, not assumed: docs/v2/03 section 6 ("flags on the row ... never
 * silent penalties") and porchlamp/core/pipeline.py (suspicion is folded on AFTER
 * assign_ranks, "so suspicion can never influence rank/tie_key").
 */
export const METHODOLOGY_SECTION_LEDES = Object.freeze({
  snapshot: "The build this page describes, and the command that reproduces it.",
  gates: "Checks a repository must pass to be ranked. A repository that fails one carries the gate's reason instead of a rank.",
  pillars: "The five pillars of the composite score, and the share of it each one carries.",
  parameters: "The measured inputs, grouped by pillar: how each is scored, how much evidence it needs, and over what window.",
  missingness: "What a missing input means, and how much coverage a repository needs before it is rated.",
  personas: "Alternative weightings of the same five pillars, and the gates that decide which repositories each persona shows.",
  tie_break: "When two composites are equal, these fields separate them, in this order.",
  detectors: "Integrity checks run against the previous snapshot. They flag a row for review; they never change a score or a rank.",
  process_constants: "Constants of the release process, not of any score, so they sit outside the constants hash.",
  rfc_log: "The history of the scored constants, newest first, with the constants hash each change produced.",
});
export const METHODOLOGY_DETECTOR_SUMMARY_LABEL = "Detector flags in this snapshot";
export const METHODOLOGY_DETECTOR_SUMMARY_NONE = "No detector flagged a row in this snapshot.";
export function methodologyDetectorCountText(count) {
  return `${count} ${count === 1 ? "row" : "rows"}`;
}
/** Human labels for the payload's coverage-threshold keys; an unknown key renders as itself. */
export const METHODOLOGY_COVERAGE_THRESHOLD_LABELS = Object.freeze({
  unrated_below: "Unrated below",
  ceiling_b_below: "Tier ceiling B below",
  ceiling_a_below: "Tier ceiling A below",
});
export const METHODOLOGY_REASONS_LABEL = "What a missing input means";
export const METHODOLOGY_NOT_APPLICABLE_LABEL = "Not-applicable rules";
export const METHODOLOGY_NOT_APPLICABLE_WHEN = "does not apply when";
export const METHODOLOGY_PERSONA_GATES_LABEL = "Persona gates";
export const METHODOLOGY_ENUM_ORDER_LABEL = "Worst to best";
export const METHODOLOGY_RFC_CURRENT_TAG = "Current";
export const METHODOLOGY_RFC_CURRENT_TITLE = "This change produced the constants hash this page is rendered from.";
export const METHODOLOGY_HASH_TO = "to";
export function methodologyPillarShareText(weight) {
  return `${methodologyPercentText(weight)} of the composite`;
}

// --- Corpus strip (how much of the snapshot is rated at all) -------------
/** One segment's share of the snapshot, to a single decimal. */
export function corpusShareText(percent) {
  return `${percent.toFixed(1)}%`;
}
/** The meter's accessible name: the whole split in one sentence. */
export function corpusMeterLabel(counts) {
  return (
    `${counts.rated} rated, ${counts.gated} gated, ${counts.unrated} unrated ` +
    `of ${counts.total} repositories in this snapshot`
  );
}

// --- Outbound repository link, pillar sorting & onboarding strip ---------
export const DRAWER_GITHUB_LABEL = "View on GitHub ↗";
export function drawerGithubUrl(owner, name) {
  return `https://github.com/${encodeURIComponent(owner)}/${encodeURIComponent(name)}`;
}
export const PILLAR_SHORT_NAMES = Object.freeze({
  responsiveness: "Resp",
  criticality: "Crit",
  governance: "Gov",
  rigor: "Rigor",
  accessibility: "Acc",
});
export function pillarSortTooltip(pillar) {
  return `Sort by ${PILLAR_NAMES[pillar] || pillar}`;
}
export const ONBOARDING_SUBTITLE =
  "Evidence-based open-source repository ranking across 5 pillars: Responsiveness, Criticality, Governance, Rigor, and Accessibility.";
export const ONBOARDING_METHODOLOGY_LINK = "How scores & gates work →";

// --- Link to the static report page (scripts/export_static.py --stats) ---------------------------------
// From the site root, since the app runs at every view's address. A real <a href> element: a crawler that
// runs the app finds the finding and the link to its page without a click. (The methodology page's address
// is in routes.js.)
//: A template: report.json's values fill the slots, each a hook of its own id (views/leaderboard.js `fillFromReport`).
//: Without a report the line is left out and only the link below it is drawn.
export const FINDINGS_LINE = "More than 1 in {one_in} active projects leave most strangers' pull requests unanswered for {deadline_days} days.";
export const FINDINGS_LINK_LABEL = "Read the findings";
/** The home's links to the ten landing pages (views/leaderboard.js `renderBrowseLinks`): the nav's name and the two lines' labels. */
export const HOME_BROWSE_LABEL = "Browse";
export const HOME_BROWSE_ECOSYSTEMS_LABEL = "Browse by ecosystem:";
export const HOME_BROWSE_LENSES_LABEL = "Browse by contributor lens:";
export const REPORT_PAGE_HREF = "/black-holes/";
export const FOOTER_REPORT_LABEL = "Report";
export const FOOTER_METHODOLOGY_LABEL = "Methodology";
/** The footer's last link: the published data and code. */
export const FOOTER_DATA_LABEL = "Data on GitHub";
export const FOOTER_DATA_HREF = "https://github.com/pctablet505/porchlamp";

// --- Pages the app does not draw (scripts/export_static.py --stats writes them), linked from its views ----------
/** Each lens's own page; the balanced lens is the rankings themselves. porchlamp/serve/report_pages.py LENS_SLUGS
 *  names the same addresses (tests/test_site_chrome.py). */
export const LENS_PAGE_HREFS = Object.freeze({
  first_timer: "/first-timers/",
  career_capital: "/career/",
  senior_systems: "/senior-systems/",
  ai_assisted: "/ai-assisted/",
});
export const CHECK_PAGE_HREF = "/check/";

/**
 * What the numbers cannot tell a reader. The report prints them under "What this is not" and the methodology under
 * METHODOLOGY_SECTION_LIMITS: one list, read by porchlamp/serve/report_pages.py for the report (copy_js_list).
 */
export const LIMITS = Object.freeze([
  "Not a verdict on the people who maintain these projects. Review time is scarce, and popular projects receive far more pull requests than anyone can read.",
  "GitHub only. Projects that review on Gerrit or a mailing list are left undecided, not failed.",
  "One six-month window. The numbers will change at the next snapshot.",
]);

// --- The report (/black-holes/, views/report.js) -----------------------------------------------------------------
// The words that carry no number. A sentence with a number, or a claim the numbers decide, is a template in the
// export's report.json, written by porchlamp/serve/report_pages.py next to the guard that keeps it true.
export const REPORT_H1 = "Stars don't review pull requests";
export const REPORT_FINDINGS_HEADING = "Key findings";
export const REPORT_STARS_HEADING = "Do popular projects answer fewer pull requests?";
export const REPORT_STARS_CAVEAT =
  "One likely reason: review time stays roughly fixed while attention grows, so the queue grows faster than volunteers can clear it. The data shows the rise, not its cause.";
export const REPORT_ECOSYSTEMS_HEADING = "Which package ecosystems answer outside pull requests?";
export const REPORT_ECOSYSTEMS_WITHIN = "Each share is computed within its own ecosystem.";
export const REPORT_ECOSYSTEMS_CAVEAT =
  "Size is not controlled for. Bigger projects go quiet more often in general, so part of a gap can come from size.";
export const REPORT_METHOD_HEADING = "How we measured";
export const REPORT_METHOD_EXCLUSIONS =
  "Bots, drafts, pull requests their authors closed, brand-new accounts and the project's own members are not counted.";
export const REPORT_METHOD_LINK_LEAD = "Every parameter, gate and weight:";
export const REPORT_METHOD_LINK_LABEL = "Porchlamp methodology";
export const REPORT_LIMITS_HEADING = "What this is not";
export const REPORT_NAMES_NO_PROJECT_LEAD =
  "This page names no project. Each project's record, with the pull requests behind it, is on";
export const REPORT_RANKINGS_LINK_LABEL = "the Porchlamp rankings";
export const REPORT_DOWNLOAD_HEADING = "Download the data";
export const REPORT_JSON_NOTE =
  "The full records, including every project that was not ranked and why, are in the site's JSON data.";
export const REPORT_CITE_HEADING = "Cite this report";
// The two charts are tables: their column heads, and a star band's row name.
export const REPORT_COL_STARS = "Stars";
export const REPORT_COL_ECOSYSTEM = "Ecosystem";
export const REPORT_COL_JUDGED = "Projects judged";
export const REPORT_COL_BLACK_HOLES = "Black holes";
export const REPORT_COL_SHARE = "Share";
export const REPORT_BAND_ROW = "{band} stars";
export const REPORT_LOADING_TEXT = "Loading the report…";
export function reportErrorText(endpoint, status, detail) {
  const base = `Could not load the report from ${endpoint} (status ${status}).`;
  return detail ? `${base} ${appendSentence(detail)}` : base;
}

// --- Maintainer README badge (Screen C, inside the Composite section) -----
// Offered when a repository's canonical (balanced) record is rated at tier A
// or above with no suspicion flag. The image is one of the static files in
// web/app/badges/; the Markdown links it to the repository's drawer, which is
// the part that stays current when a later snapshot changes the rating.
export const MAINTAINER_BADGE_HEADING = "Maintainer README badge";
export const MAINTAINER_BADGE_NOTE =
  "The Balanced rating from this snapshot, for the project's README. The image does not change when the rating does; its link always opens the current rating and its evidence.";
export const MAINTAINER_BADGE_COPY_LABEL = "Copy Markdown";
export const MAINTAINER_BADGE_COPIED_LABEL = "Copied";

// U-1 on the image itself: a badge names its tier, "(capped)" when the
// ceiling binds, and the coverage band behind it (D-MISS-3's bands).
const BADGE_COVERAGE_TEXT = Object.freeze({ full: "coverage 95–100%", A: "coverage 80–95%" });
// The tier states a badge image exists for, as tier|coverage band|ceiling.
// S+ and S never carry a binding ceiling: a ceiling caps the tier at A or B
// (porchlamp/core/aggregate.py apply_ceiling), so a capped badge is always A.
const BADGE_TIER_FILES = Object.freeze({
  "S+|full|open": "tier-s-plus",
  "S|full|open": "tier-s",
  "A|full|open": "tier-a",
  "A|A|capped": "tier-a-capped",
});
const BADGE_QUADRANT = "Sweet Spot";

/**
 * The README badge a repository's record earns, or null. Pass the balanced
 * record, never a persona projection (U-4): a lens must not mint a badge the
 * balanced rating does not support. Tier A or above is required; the Sweet
 * Spot quadrant marks a badge but never earns one. MEASURED on snapshot
 * 2026-10-14: "Sweet Spot or tier A+" offered a badge to 1,412 of 3,833
 * rated repositories, 214 of them tier C; tier A+ with no flag is 91.
 * `imageText` is what the image says; `alt` adds the score, the exact
 * coverage and the snapshot id (U-1, U-6) for the README's alt text and title.
 */
export function maintainerBadgeSpec(row) {
  if (!row || !row.eligible || !row.rated) return null;
  if (Array.isArray(row.suspicion) && row.suspicion.length > 0) return null;
  if (typeof row.porchlamp !== "number" || typeof row.coverage !== "number") return null;
  const band = coverageBandForValue(row.coverage);
  const effectiveCeiling =
    row.tier_ceiling !== undefined ? row.tier_ceiling : band === "A" || band === "B" ? band : null;
  const capped = !!effectiveCeiling && row.tier === effectiveCeiling;
  const tierFile = BADGE_TIER_FILES[`${row.tier}|${band}|${capped ? "capped" : "open"}`];
  if (!tierFile) return null;
  const sweetSpot = row.quadrant === BADGE_QUADRANT;
  const quadrantText = `${QUADRANT_GLYPHS[BADGE_QUADRANT]} ${QUADRANT_LABELS[BADGE_QUADRANT]}`;
  const tierText = `${COLUMN_TIER} ${tierLabel(row.tier, effectiveCeiling)}`;
  const headline = sweetSpot ? `${quadrantText} · ${tierText}` : tierText;
  // Characters that would end a Markdown alt text or link title early never reach the snippet.
  const snapshot = row.snapshot_id ? String(row.snapshot_id).replace(/["[\]\\\r\n]/g, "") : "";
  const alt = [
    `${BRAND_NAME}: ${headline}`,
    `${COLUMN_PORCHLAMP} ${row.porchlamp.toFixed(2)}`,
    `${COLUMN_COVERAGE} ${coverageBadgeText(row.coverage)}`,
    snapshot ? `${SNAPSHOT_ID_LABEL} ${snapshot}` : "",
  ].filter(Boolean).join(" · ");
  const id = `${sweetSpot ? "sweet-spot-" : ""}${tierFile}`;
  return { id, file: `badges/${id}.svg`, imageText: `${headline} · ${BADGE_COVERAGE_TEXT[band]}`, alt };
}

/**
 * The README Markdown: the badge image, linked to the project's own page on
 * the public site (a README written before that page existed links the
 * drawer, `?repo=`, which still opens). `baseUrl` is that site's address
 * (app.js reads it from index.html), so a badge copied from a local server
 * still points at the public one.
 */
export function maintainerBadgeMarkdown(row, baseUrl = "") {
  const spec = maintainerBadgeSpec(row);
  if (!spec) return "";
  const base = !baseUrl || baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;
  // routes.js's address starts at the site root; the base names that root.
  const target = `${base}${projectPath(row.owner, row.name).slice(1)}`;
  return `[![${spec.alt}](${base}${spec.file})](${target} "${spec.alt}")`;
}

// --- The project view (views/project.js)
// The words of a project's page and side panel. A sentence that carries a measured number is a template whose
// {slots} the view fills inside hooks (components/fill.js); the sentences of the answer come from the record
// (`answer`), written by Python. Flat `Object.freeze({key: "text"})` maps and plain string constants, so the
// verifier can read them from this file.

/** What each parameter measures (porchlamp/serve/project_pages.py PARAM_MEANINGS, moved here). */
export const PARAM_MEANINGS = Object.freeze({
  ttfhr_hours: "Median hours from an outside pull request opening to its first substantive human reply.",
  dr90_decision_rate: "Share of outside pull requests that were merged or closed within 90 days.",
  mr_first_rate: "Share of first-time outside authors' decided pull requests that were merged.",
  gr14_ghost_rate: "Share of outside pull requests with no maintainer response, merge or close within 14 days.",
  ttm_p90_hours: "Hours within which 90% of merged outside pull requests were merged.",
  dependents_deduped: "Packages in the registry that depend on this project, counted once each.",
  openssf_criticality: "OpenSSF criticality score from 0 to 1, computed from the project's public activity signals.",
  downloads_monthly: "Downloads from the package registry in a month.",
  legal_ip: "The contributor licence regime: DCO, none, a foundation CLA or a corporate CLA.",
  elephant_factor: "The fewest organisations that account for half of the last year's commits.",
  promotion_ladder: "How openly a contributor can become a maintainer, from closed to verified.",
  pipeline_defenses: "How well the project's build and review pipeline defends against bad changes, 0 to 100.",
  ai_posture: "Whether the project has a written policy on AI-assisted contributions.",
  substantive_review_rate: "Share of decided pull requests that received a substantive review.",
  newcomer_issue_supply: "Open, unassigned, up-to-date issues marked for newcomers.",
  newcomer_conversion: "Share of first-time authors whose first pull request merged within 180 days.",
  rr90_return_rate: "Share of those merged first-time authors who sent a second pull request within 90 days.",
  dx: "Developer experience, 0 to 100: half CI run time, half setup signals.",
});

/** The page's lede; {date} is the snapshot date. */
export const PROJECT_LEDE = "What happens to a stranger's pull request here, in the Porchlamp snapshot of {date}.";

export const PROJECT_PILLARS_HEADING = "Five pillars";
export const PROJECT_PILLARS_CAPTION = "Score out of 100, and how much of the pillar was measured";
export const PROJECT_PARAMETERS_HEADING = "The measured parameters";
export const PROJECT_KEY_FACTS_CAPTION = "Key facts";
export const PROJECT_CRUMB_LABEL = "Breadcrumb";
export const PROJECT_COMPARE_HREF = "/compare/?cmp=";

/** Table column heads of the view. The rank and coverage heads are COLUMN_RANK and COLUMN_COVERAGE. */
export const PROJECT_COLUMNS = Object.freeze({
  pillar: "Pillar",
  parameter: "Parameter",
  value: "Value",
  score: "Score",
  count: "Count",
  window: "Window",
  source: "Source",
  persona: "Persona",
  status: "Status",
  pr: "PR",
  opened: "Opened",
  pr_status: "14-day status",
});

/** The key-facts rows that have no label elsewhere. The others are COLUMN_RANK, COLUMN_PORCHLAMP, COLUMN_TIER,
 *  ECOSYSTEM_LABEL and the window's column head. */
export const KEY_FACT_LABELS = Object.freeze({
  reply_time: "Median first reply",
  no_reply_share: "14-day no-reply share",
  first_timer_rank: "First-timer rank",
});

/** A rank beside its total; the Composite card prints the total after the big rank figure. */
export const OF_TOTAL = "{rank} of {total}";
export const OF_TOTAL_SUFFIX = "of {total}";
/** The Composite card's note on the two coordinates (COORDINATES_LABEL names them). */
export const COORDINATES_LINE =
  "{label}: leverage {leverage} (coverage {leverage_coverage}), friction {friction} (coverage {friction_coverage})";

export const SIMILAR_HEADING = "Similar projects";
export const SIMILAR_LEAD = "The ranked projects nearest this one in the same ecosystem.";
export const SIMILAR_NONE = "No other ranked project in this ecosystem.";
export const SIMILAR_RANK = "(rank {rank})";

export const RECORD_FILE_LEAD = "The full record is in";
export const RECORD_FILE_KEY = ", under the key";
export const METHOD_LINK_LEAD = "How every number is measured:";
export const METHOD_LINK_LABEL = "Porchlamp methodology";
export const ISSUE_LINK_LABEL = "Open an issue about this record";
export const ISSUE_LINK_TAIL = " and say what looks wrong.";
export const ISSUE_TITLE_PREFIX = "Porchlamp record: ";
export const ISSUE_BASE = "https://github.com/pctablet505/porchlamp/issues/new?title=";

/** One cite line at the end of Integrity. */
export const SITE_ADDRESS = "https://porchlamp.org";
export const PROJECT_CITE_LINE = "Porchlamp ({year}). {project}. Snapshot {snapshot}. {url}. CC BY 4.0.";

/** The black-hole evidence block's own sentences (moved from views/drawer.js, unchanged). */
export const BLACK_HOLE_WILSON_LINE = "95% Wilson lower confidence bound: {wilson}% (threshold: > {threshold}%, min n = {min_n})";
export const BLACK_HOLE_SOURCE_LEAD = "Source:";
export const BLACK_HOLE_EXCLUDED_LEAD = "Excluded:";
export const BLACK_HOLE_EXCLUDED_LABELS = Object.freeze({
  draft: "draft",
  self_closed: "author self-closed",
  new_account: "new account (<90d)",
  bot: "bot",
  internal: "internal/maintainer",
  under_14d: "open <14d",
});

/** Month names for the view's dates: it formats them itself, with no locale API, so every renderer prints the
 *  same bytes (porchlamp/serve/project_pages.py long_date and window_text, in JavaScript). */
export const MONTHS = Object.freeze([
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
]);

/** The path of each ecosystem's landing page (porchlamp/serve/report_pages.py ECOSYSTEM_SLUGS). */
export const ECOSYSTEM_SLUGS = Object.freeze({
  pypi: "python",
  npm: "javascript",
  go: "go",
  systems: "c-cpp",
  crates: "rust",
  maven: "java",
});

/** The units of the parameter table. {slots} are filled by views/project.js formatParameterValue. */
export const PARAM_UNIT_WORDS = Object.freeze({
  hours_and_days: "{hours} hours ({days} days)",
  share: "{percent}%",
  downloads_million: "{count} million downloads a month",
  downloads: "{count} downloads a month",
});

/** The median first reply in the key facts (porchlamp/serve/project_pages.py reply_time_text). */
export const REPLY_TIME_WORDS = Object.freeze({
  under_tenth_hour: "under 0.1 hours",
  hour: "{n} hour",
  hours: "{n} hours",
  days: "{n} days",
  days_and_hours: "{days} days ({hours} hours)",
});

// The plain label of each enum code a parameter can hold. Each states what the engine's classifier looks for, in
// factual words and without judgement: how a CLA scores is under the owner's review. The comment beside a label
// is its definition (the code list is porchlamp/core/constants.py:510-516).

/** legal_ip, classified by porchlamp/collect/governance.py classify_legal_ip. */
export const LEGAL_IP_LABELS = Object.freeze({
  DCO: "Sign-off on commits (DCO)", // governance.py:786 a check named "dco"; :788-789 or the Signed-off-by share of commits is at least SIGNOFF_THRESHOLD (:111, 0.5)
  NONE: "No CLA or sign-off found", // governance.py:790 none of the checks below, no sign-off evidence
  FOUNDATION_CLA: "Foundation CLA", // governance.py:780-782 a foundation CLA check (:148) or an owner on the foundation list
  CORP_CLA: "Company CLA", // governance.py:783-784 a CLA check (:132) that is not foundation-branded
});

/** ai_posture, classified by porchlamp/collect/governance.py classify_ai_posture. */
export const AI_POSTURE_LABELS = Object.freeze({
  REGULATED: "Written AI policy", // governance.py:911-912 a policy file of AI_POLICY_FILES (:116) is present
  PROSCRIBED: "AI ban stated in a written policy", // docs/v2/02-RANKING-DESIGN.md:165 an explicit ban sentence in a policy file; governance.py:24-31 the collector never returns it
  AD_HOC: "No written AI policy", // governance.py:913 no policy file found
});

/** promotion_ladder, classified by porchlamp/collect/governance.py classify_promotion_ladder. */
export const PROMOTION_LADDER_LABELS = Object.freeze({
  VERIFIED: "An outside contributor became a member or collaborator", // governance.py:865 a promotion seen in the pull request window (PROMOTION_TARGET_ASSOCIATIONS, :166)
  DOCUMENTED: "A written path to becoming a maintainer", // governance.py:891 a governance file names a nomination process (NOMINATION_PATTERN, :169)
  AD_HOC: "No written path to becoming a maintainer", // governance.py:892 no promotion seen and no nomination text found
  CLOSED: "Merged pull requests all came from one organisation", // governance.py:887 no outside merge, and every merged author belongs to one email-domain organisation
});
