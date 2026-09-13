// copy.js — every user-facing sentence in the CVI v2 UI lives here (06 §6).
// Nothing user-facing is a string literal anywhere else in web/v2/: views
// and components import names from this module and interpolate data into
// the templates defined here, never write new prose inline.
//
// tests/test_ui_copy.py enumerates every exported name in this file (via a
// source-level parse, not execution — Node is not available on this box,
// see .agents/W4-5a/handoff.md) and asserts each one is referenced
// somewhere under web/v2/, so no string here is dead/orphaned copy.

export const BRAND_NAME = "Contributor Value Index";

export const NAV_LEADERBOARD = "Leaderboard";
export const NAV_NOT_RANKED = "Not ranked";

export const TAB_RANKED = "Ranked";
export const TAB_NOT_RANKED = "Not ranked";

export const SEARCH_PLACEHOLDER = "Search owner/repo…";
export const SEARCH_LABEL = "Search repositories";
export const THEME_TOGGLE_LABEL = "Toggle light/dark theme";
export const PERSONA_LABEL = "Persona";
export const ECOSYSTEM_LABEL = "Ecosystem";
export const ECOSYSTEM_ALL = "All ecosystems";

export const PERSONA_NAMES = Object.freeze({
  balanced: "Balanced",
  first_timer: "First-timer",
  career_capital: "Career capital",
  senior_systems: "Senior systems",
  ai_assisted: "AI-assisted",
});

export const COLUMN_RANK = "Rank";
export const COLUMN_REPOSITORY = "Repository";
export const COLUMN_CVI = "CVI";
export const COLUMN_COVERAGE = "Coverage";
export const COLUMN_TIER = "Tier";
export const COLUMN_QUADRANT = "Quadrant";
export const COLUMN_PILLARS = "Pillars";
export const COLUMN_DELTA = "Δ rank";
export const COLUMN_SUSPICION = "Flags";
export const COLUMN_STARS = "Audited stars";

// U-3: vanity columns (audited stars) are never sortable; this is the
// tooltip that says so wherever such a column header appears.
export const VANITY_TOOLTIP = "audited, not scored";

export const PILLAR_NAMES = Object.freeze({
  responsiveness: "Responsiveness",
  criticality: "Criticality",
  governance: "Governance",
  rigor: "Rigor",
  accessibility: "Accessibility",
});

export const QUADRANT_LABELS = Object.freeze({
  "Sweet Spot": "Sweet Spot",
  Crucible: "Crucible",
  Nursery: "Nursery",
  Graveyard: "Graveyard",
});

// Color-blind-safe dual encoding (shape + color), spec §5.1.
export const QUADRANT_GLYPHS = Object.freeze({
  "Sweet Spot": "♦",
  Crucible: "⬢",
  Nursery: "●",
  Graveyard: "✕",
});

const TIER_CAPPED_SUFFIX = " (capped)"; // private: only tierLabel needs the literal
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
// "fork" is a W4-3 store-queries review addition (2026-09-13), not in the
// originally documented U-10 set but a valid GateReason on the wire.
const GATE_REASON_COPY = Object.freeze({
  mirror: "Mirror of another repository — not an independent project.",
  archived: "Archived on GitHub — no active development to evaluate.",
  unlicensed: "No open-source license — contribution and reuse are not legally clear.",
  stale: "No qualifying human activity in the scoring window.",
  black_hole:
    "Pull requests go in and never come out — reviews and merges have stalled.",
  non_software: "Not a software repository (docs, data, or list only).",
  fork: "A fork of another repository, not an independent project.",
});
export function gateReasonText(reason) {
  return GATE_REASON_COPY[reason] || reason;
}

// Both spellings of the evidence-floor reason resolve to the same
// sentence: the engine's own constant is "unrated: insufficient_evidence"
// (W4-3 review, 2026-09-13); "insufficient_evidence" is kept for any
// producer that sends the plain form.
const UNRATED_REASON_COPY = Object.freeze({
  insufficient_evidence: "Not enough measured evidence to rank",
  "unrated: insufficient_evidence": "Not enough measured evidence to rank",
  no_external_workflow: "No external contribution workflow was found to measure.",
  off_platform_routed:
    "Contribution activity happens off-platform and cannot be measured here.",
});
export function unratedReasonText(reason, coverage) {
  if (reason === "insufficient_evidence" || reason === "unrated: insufficient_evidence") {
    const pct = typeof coverage === "number" ? `${Math.round(coverage * 100)}%` : "unknown";
    return `${UNRATED_REASON_COPY[reason]} (coverage ${pct}, below the 60% floor).`;
  }
  return UNRATED_REASON_COPY[reason] || reason;
}

export const GATED_GROUP_TITLE = "Gated";
export const UNRATED_GROUP_TITLE = "Unrated";
export const PERSONA_HIDDEN_GROUP_TITLE = "Hidden by this lens";
export const NOT_RANKED_EMPTY = "No gated or unrated repositories to show.";

// Persona gate reasons (02-RANKING-DESIGN.md D-PERS-2/3), reused by the
// leaderboard's persona-hidden disclosure and the drawer's persona section.
const PERSONA_GATE_COPY = Object.freeze({
  hard_ai_ban: "This repository has an explicit ban on AI-assisted contributions.",
  single_vendor:
    "Fewer than 3 organizations account for most commits (not multi-vendor).",
  low_barrier_not_met:
    "This repository does not show a low enough barrier for a first-time contributor.",
  brand_tier_not_met:
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
  return PERSONA_GATE_COPY[code] || code;
}
export function personaHiddenDisclosure(count) {
  return `Hidden by this lens (${count})`;
}

// U-5: tie marks and the rank tooltip naming the deciding D-TIE-1 level.
// Levels 6 (TTFHR p75) and 7 (last non-bot commit date) are store-only
// fields not part of the API row contract (06 §4 does not list them), so
// the client can only ever derive levels 1, 2, 3, 4, 5 and 8 — see
// components/table.js's decidingTieLevel and the handoff's stated gap.
export const TIE_MARK = "=";
const TIE_LEVEL_LABELS = Object.freeze({
  1: "the composite CVI",
  2: "coverage",
  3: "the responsiveness pillar",
  4: "friction",
  5: "leverage",
  8: "owner/name (last resort)",
});
export function tieTooltip(level) {
  const label = TIE_LEVEL_LABELS[level] || `tie-break level ${level}`;
  return `Tied on composite CVI; separated from the row above by ${label}.`;
}

export function emptyLeaderboardText(gatedCount, unratedCount) {
  return `No rated repositories match. ${gatedCount} gated and ${unratedCount} unrated are listed under Not ranked.`;
}

export function errorBannerText(endpoint, status) {
  return `Could not reach ${endpoint} (status ${status}). Showing the last good data below.`;
}

export const MISSING_VALUE = "—";
export function missingValueReason(reason) {
  return reason ? `Not shown: ${reason}` : "Not measured for this repository.";
}

export const LOADING_LABEL = "Loading…";
export const LOADING_ROW_SR_TEXT = "Loading repositories…";

export const SUSPICION_LABEL = "Suspicion flags";
export function suspicionTooltip(flags) {
  if (!flags || flags.length === 0) return "";
  return `Flagged by: ${flags.join(", ")}`;
}

export const ROW_OPEN_HINT = "Press Enter to view why this score";

// Owner instruction 2026-09-13 (replacing the earlier no-pagination
// ruling): a page-size dropdown (20/50/100/500/1000) plus
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
export function sortedAnnouncement(columnLabel) {
  return `Sorted by ${columnLabel}.`;
}

export const KEYMAP_TITLE = "Keyboard shortcuts";
export const KEYMAP_CLOSE_LABEL = "Close";
export const KEYMAP_ENTRIES = Object.freeze([
  { keys: "/", action: "Focus search" },
  { keys: "J / K", action: "Move to next / previous row" },
  { keys: "Enter", action: "Open the selected row's drawer" },
  { keys: "Esc", action: "Close the drawer or an overlay" },
  { keys: "] / [", action: "Next / previous page" },
  { keys: "1 – 4", action: "Toggle a matrix quadrant (W4-5b)" },
  { keys: "P", action: "Open the persona menu" },
  { keys: "T", action: "Toggle light/dark theme" },
  { keys: "?", action: "Open this keyboard shortcuts overlay" },
]);

export const DRAWER_TITLE = "Why this score";
export const DRAWER_SECTION_IDENTITY = "Identity and gate status";
export const DRAWER_SECTION_COMPOSITE = "Composite";
export const DRAWER_SECTION_PILLARS = "Pillars and evidence";
export const DRAWER_SECTION_PERSONAS = "Persona view";
export const DRAWER_SECTION_INTEGRITY = "Integrity";
export const DRAWER_SECTION_PLAYBOOK = "Playbook";
export const PLAYBOOK_DISCLAIMER = "This is a model, not a promise.";
export const PLAYBOOK_STUB_TEXT =
  "Action recommendations are not computed by this snapshot yet.";
export const DRAWER_CLOSE_LABEL = "Close";
export const DRAWER_LOADING_TEXT = "Loading evidence…";
export function drawerErrorText(endpoint, status) {
  return `Could not load evidence from ${endpoint} (status ${status}).`;
}
export const EVIDENCE_RETRY_LABEL = "Retry";
export const RAW_HASH_LABEL = "Raw hash";
export const SNAPSHOT_ID_LABEL = "Snapshot";
export const SUSPICION_NONE_TEXT = "No suspicion flags on this repository.";
export const RATED_LABEL = "Rated";
export const NOT_RATED_LABEL = "Not rated under this lens";
export const COORDINATES_LABEL = "Coordinates";

export const FOOTER_VERIFY_LABEL = "Verify";
export const FOOTER_SNAPSHOT_LABEL = "Snapshot";
export const FOOTER_VERSION_LABEL = "Benchmark version";
export const FOOTER_CONSTANTS_HASH_LABEL = "Constants hash";
export const FOOTER_MANIFEST_ROOT_LABEL = "Manifest root";

export const SKIP_LINK_LABEL = "Skip to main content";
export const MAIN_LANDMARK_LABEL = "CVI leaderboard and repository detail";

export function apiErrorAnnouncement(endpoint, status) {
  return errorBannerText(endpoint, status);
}
