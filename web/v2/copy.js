// copy.js — every user-facing sentence in the Porchlamp v2 UI lives here (06 §6).
// Nothing user-facing is a string literal anywhere else in web/v2/: views
// and components import names from this module and interpolate data into
// the templates defined here, never write new prose inline.
//
// tests/test_ui_copy_usage.py enumerates every exported name in this file (via a
// source-level parse, not execution) and asserts each one is referenced
// somewhere under web/v2/, so no string here is dead/orphaned copy.

export const BRAND_NAME = "Porchlamp";
/** The slogan, beside the brand in the header (hidden on narrow screens). */
export const BRAND_TAGLINE = "Find the projects that leave the light on.";

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
export const COLUMN_PORCHLAMP = "Score";
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

export const PARAM_NAMES = Object.freeze({
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
    "Pull requests go in and never come out — reviews and merges have stalled.",
  non_software: "Not a software repository (docs, data, or list only).",
  fork: "A fork of another repository, not an independent project.",
});
export function gateReasonText(reason) {
  return GATE_REASON_COPY[reason] || reason;
}

// Both spellings of the evidence-floor reason resolve to the same
// sentence: the engine's own constant is "unrated: insufficient_evidence";
// "insufficient_evidence" is accepted for any producer that sends the
// plain form.
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
  return PERSONA_GATE_COPY[code] || code;
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

// The API's error body carries a `detail` string (`{"error": {endpoint,
// status, detail}}`) explaining WHAT went wrong, not just that it did —
// appended after the endpoint and status wherever it is available
// (`ApiError.detail`, `web/v2/api.js`).
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

export const LOADING_LABEL = "Loading…";
export const LOADING_ROW_SR_TEXT = "Loading repositories…";

export const SUSPICION_LABEL = "Suspicion flags";
export function suspicionTooltip(flags) {
  if (!flags || flags.length === 0) return "";
  return `Flagged by: ${flags.join(", ")}`;
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
export function sortedAnnouncement(columnLabel) {
  return `Sorted by ${columnLabel}.`;
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
export const DRAWER_SECTION_IDENTITY = "Identity and gate status";
// A GATED repository was disqualified before scoring and has no score/tier/
// quadrant at all (`RepoDetailResponse.porchlamp` etc. are `None`, never a
// fabricated value), so the composite `<details>` is OMITTED entirely for
// `kind === "gated"` (`views/drawer.js::renderDrawer`) rather than
// rendered with a "no composite" placeholder sentence. The other three
// post-identity sections (pillars/evidence, persona view, integrity)
// still render, collapsed, below the gate sentence (06 §3 C: "the reason
// sentence and nothing else ABOVE THE FOLD" — collapsed sections below
// the fold are fine).
export const DRAWER_SECTION_COMPOSITE = "Composite";
export const DRAWER_SECTION_PILLARS = "Pillars and evidence";
export const DRAWER_SECTION_PERSONAS = "Persona view";
export const DRAWER_SECTION_INTEGRITY = "Integrity";
export const DRAWER_SECTION_PLAYBOOK = "Playbook";
export const PLAYBOOK_DISCLAIMER = "This is a model, not a promise.";
export const PLAYBOOK_STUB_TEXT =
  "Action recommendations are not computed by this snapshot yet.";
export const DRAWER_CLOSE_LABEL = "Close";
export const DRAWER_COMPARE_LABEL = "Compare";
export const DRAWER_NO_EVIDENCE_TEXT = "No detailed evidence telemetry recorded for this repository.";
export const DRAWER_LOADING_TEXT = "Loading evidence…";
export const DRAWER_NOT_FOUND_TEXT = "This repository could not be found in the current snapshot.";
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

export const FOOTER_VERIFY_LABEL = "Verify";
export const FOOTER_SNAPSHOT_LABEL = "Snapshot";
export const FOOTER_VERSION_LABEL = "Benchmark version";
export const FOOTER_CONSTANTS_HASH_LABEL = "Constants hash";
export const FOOTER_MANIFEST_ROOT_LABEL = "Manifest root";

export const SKIP_LINK_LABEL = "Skip to main content";
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
  return `${excludedCount} gated ${excludedCount === 1 ? "repository" : "repositories"} excluded.`;
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
export const MATRIX_RESET_VIEW_LABEL = "Reset view";
export const MATRIX_ZOOM_IN_LABEL = "Zoom in";
export const MATRIX_ZOOM_OUT_LABEL = "Zoom out";
export function matrixQuadrantToggleLabel(quadrantName) {
  return `Toggle ${quadrantName}`;
}
export const MATRIX_CANVAS_FALLBACK_NOTE =
  "Rendering on canvas above 2,000 markers, with an SVG overlay for the focused or hovered marker only.";
export const MATRIX_OPEN_HINT = "Press Enter to view why this score";

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

// --------------------------------------------------------------------- //
// Screen F: Methodology and snapshot
// --------------------------------------------------------------------- //
export const METHODOLOGY_HEADING = "Methodology and snapshot";
/** "Why Porchlamp", above the methodology: the one idea, the finding behind it, and where the numbers come from. */
export const WHY_HEADING = "Why Porchlamp";
export const WHY_PARAGRAPHS = Object.freeze([
  "A porch lamp left on tells a visitor they are expected. Porchlamp ranks open-source projects the same way: by what happens to a stranger's pull request, not by how many stars the project has.",
  "Stars do not review pull requests. In our measurements about half of active projects leave most pull requests from outside contributors without a human reply, review or merge for two weeks, and the more stars a project has, the more likely it is to be one of them. We call these projects black holes; they are listed under Not ranked, never ranked.",
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
export const METHODOLOGY_COL_ID = "ID";
export const METHODOLOGY_COL_NAME = "Name";
export const METHODOLOGY_COL_DOMAIN = "Domain";
export const METHODOLOGY_COL_PROXY = "Proxy";
export const METHODOLOGY_COL_CUTOFFS = "Cutoffs";
export const METHODOLOGY_COL_THRESHOLDS = "Thresholds";
export const METHODOLOGY_COL_PERSONA = "Persona";
export const METHODOLOGY_REPRODUCE_LABEL = "Reproduce command";
export const METHODOLOGY_COPY_LABEL = "Copy";
export const METHODOLOGY_COPIED_LABEL = "Copied";
export function methodologyBenchmarkLine(benchmarkVersion, constantsHash, schemaVersion) {
  return `Benchmark ${benchmarkVersion} · schema ${schemaVersion} · constants hash ${constantsHash.slice(0, 12)}…`;
}
export function methodologySnapshotLine(snapshotId, asOf) {
  return `${snapshotId} · as of ${asOf}`;
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
