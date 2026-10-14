// tests/unit.test.mjs — Node unit tests for the pure modules (06 §6 and §8
// acceptance checklist items 3-5, 8; state/query modules, U-14/U-16/U-17/U-18).
//
// Written to run cleanly under `node --test` (Node >= 18, stdlib only).
//
// Run: node --test web/v2/tests/unit.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { decidingTieLevel, SORTABLE_COLUMNS, renderColumnHeader } from "../components/table.js";
import { KEY_ACTIONS, actionForKeyEvent } from "../components/keymap.js";
import { renderCoverageBadge } from "../components/badge.js";
import { renderBrand } from "../components/brand.js";
import { escapeHtml } from "../components/table.js";
import {
  defaultState,
  reduce,
  parseSearch,
  serializeSearch,
  countRankChanges,
  resolveOpenTargetKey,
  footerFieldsFor,
  repoToLoadFromState,
} from "../state.js";
import * as copy from "../copy.js";
import { renderShell as leaderboardRenderShell, syncPager, scrollDeltaToReveal } from "../views/leaderboard.js";
import { renderShell as notRankedRenderShell } from "../views/notranked.js";
import { centeredScrollLeft } from "../components/primarynav.js";
import { postReproject } from "../api.js";
import { normalizeWeights, applyPresetRespectingLocks, renderControls, sortReprojectRows, topMovers, remainingReprojectPages, mergeReprojectPages } from "../views/configure.js";
import { PILLAR_IDS, PERSONA_PRESET_WEIGHTS, BALANCED_WEIGHTS } from "../state.js";
import { nearestMarkerInDirection, hitTestPoint, renderMatrixBody } from "../views/matrix.js";
import { pointKey, SVG_MARKER_BUDGET, toSvgX, toSvgY } from "../components/scatter.js";
import { synthesizeRows } from "../perf-fixtures.js";
import { shellKey as compareShellKey, shellShape as compareShellShape } from "../views/compare.js";
import { renderDrawer, classifyKind, findRow } from "../views/drawer.js";
import {
  personaVisibleCount,
  personaDelta,
  renderTabs,
  renderCountsStrip,
} from "../views/leaderboard.js";
import {
  renderShell as renderMethodologyShell,
  sectionIndex,
  parameterGroups,
  changeLogEntries,
  inlineCodeHtml,
} from "../views/methodology.js";

// ---------------------------------------------------------------------
// D-TIE-1 client-derivable levels: 1 (composite), 2 (coverage), 3
// (responsiveness), 4 (friction), 5 (leverage), 8 (owner/name — levels 6
// TTFHR p75 and 7 last-commit-date are store-only, never in the row
// contract; see docs/v2/06-UX-DESIGN.md).
test("decidingTieLevel: level 1 (composite differs — not a genuine tie)", () => {
  const a = { porchlamp: 90.11, coverage: 0.9, p_responsiveness: 80, friction: 20, leverage: 70, owner: "a", name: "a" };
  const b = { porchlamp: 88.5, coverage: 0.9, p_responsiveness: 80, friction: 20, leverage: 70, owner: "b", name: "b" };
  assert.equal(decidingTieLevel(a, b), 1);
});

test("decidingTieLevel: level 2 (tied on composite, differ on coverage)", () => {
  const a = { porchlamp: 90.11, coverage: 0.97, p_responsiveness: 80, friction: 20, leverage: 70, owner: "a", name: "a" };
  const b = { porchlamp: 90.11, coverage: 0.81, p_responsiveness: 80, friction: 20, leverage: 70, owner: "b", name: "b" };
  assert.equal(decidingTieLevel(a, b), 2);
});

test("decidingTieLevel: level 8 (tied through every client-visible level, owner/name is the fallback)", () => {
  const a = { porchlamp: 77.0, coverage: 0.9, p_responsiveness: 80, friction: 20, leverage: 70, owner: "aaa", name: "deep" };
  const b = { porchlamp: 77.0, coverage: 0.9, p_responsiveness: 80, friction: 20, leverage: 70, owner: "zzz", name: "deep" };
  assert.equal(decidingTieLevel(a, b), 8);
});

// -------------------------------------------------------------------- U-3
test("SORTABLE_COLUMNS excludes every vanity/decorative column (stars, quadrant, suspicion, delta, repository)", () => {
  for (const forbidden of ["stars", "quadrant", "suspicion", "delta", "repository"]) {
    assert.ok(!SORTABLE_COLUMNS.has(forbidden), `${forbidden} must not be sortable (U-3)`);
  }
  assert.ok(SORTABLE_COLUMNS.has("rank"));
  assert.ok(SORTABLE_COLUMNS.has("porchlamp"));
  assert.ok(SORTABLE_COLUMNS.has("coverage"));
  assert.ok(SORTABLE_COLUMNS.has("tier"));
  for (const pillar of ["responsiveness", "criticality", "governance", "rigor", "accessibility"]) {
    assert.ok(SORTABLE_COLUMNS.has(`p_${pillar}`));
  }
});

// -------------------------------------------------------------------- 06 §5
test("keymap: every documented key in copy.KEYMAP_ENTRIES has a matching KEY_ACTIONS entry", () => {
  // copy.KEYMAP_ENTRIES is the user-facing overlay list; KEY_ACTIONS is the
  // handler table actionForKeyEvent dispatches on. Multi-key entries like
  // "J / K" and "1 - 4" expand to several KEY_ACTIONS rows.
  const handledKeys = new Set(KEY_ACTIONS.map((e) => e.key.toLowerCase()));
  assert.ok(handledKeys.has("/"));
  assert.ok(handledKeys.has("j") && handledKeys.has("k"));
  assert.ok(handledKeys.has("enter"));
  assert.ok(handledKeys.has("escape"));
  for (const n of ["1", "2", "3", "4"]) assert.ok(handledKeys.has(n));
  assert.ok(handledKeys.has("p"));
  assert.ok(handledKeys.has("t"));
  assert.ok(handledKeys.has("?"));

  // Every KEY_ACTIONS entry names a real, non-empty action (no dead rows).
  for (const entry of KEY_ACTIONS) {
    assert.ok(typeof entry.action === "string" && entry.action.length > 0, `KEY_ACTIONS entry for ${entry.key} has no action`);
  }

  // Every overlay-documented key group ("J / K", "1 - 4", ...) has at
  // least one single key inside it that KEY_ACTIONS actually handles —
  // the overlay never documents a shortcut the handler table doesn't
  // implement.
  for (const entry of copy.KEYMAP_ENTRIES) {
    // The overlay uses the short, human label "Esc" for KeyboardEvent.key's
    // "Escape" — the only place the two vocabularies diverge.
    const keyAliases = {
      esc: "escape",
      "\u2191": "arrowup",
      "\u2193": "arrowdown",
      "\u2190": "arrowleft",
      "\u2192": "arrowright",
    };
    // Split only on SEPARATORS, never on a key that IS the separator: the
    // overlay documents "/" (Focus search) as a shortcut in its own right, and
    // splitting it on "/" yielded ["", ""] -- so this assertion reported a
    // missing handler for a key KEY_ACTIONS has always handled. The Python twin
    // of this helper (tests/test_ui_js_integrity.py::_expand_keymap_entry_keys)
    // already required the surrounding spaces; this now matches it.
    const singleKeys = (entry.keys.trim().length === 1
      ? [entry.keys.trim()]
      : entry.keys.split(/\s+[/\u2013-]\s+|\s*\u2013\s*|\s+/))
      .map((k) => k.trim().toLowerCase())
      .filter((k) => k.length > 0)
      .map((k) => keyAliases[k] || k);
    const anyHandled = singleKeys.some((k) => handledKeys.has(k));
    assert.ok(anyHandled, `overlay entry "${entry.keys}" (${entry.action}) has no matching KEY_ACTIONS handler`);
  }
});

test("actionForKeyEvent: letter shortcuts are suppressed while a text input is focused, Escape/Enter are not", () => {
  assert.equal(actionForKeyEvent({ key: "j" }, { isInputFocused: true }), null);
  assert.equal(actionForKeyEvent({ key: "j" }, { isInputFocused: false }), "cursor-down");
  assert.equal(actionForKeyEvent({ key: "Escape" }, { isInputFocused: true }), "close-overlay");
  assert.equal(actionForKeyEvent({ key: "Enter" }, { isInputFocused: true }), "open-selected");
  assert.equal(actionForKeyEvent({ key: "z" }, { isInputFocused: false }), null);
});

// U-18/§5: a row reached by Tab or a direct `.focus()` call — never having
// gone through the J/K cursor's SET_CURSOR — must still open on Enter.
// This is the full key-handler state transition: actionForKeyEvent
// resolves "Enter" to "open-selected" regardless of how focus got there,
// and resolveOpenTargetKey must prefer DOM focus over a stale/absent
// cursor when deciding WHICH row that opens.
test("Enter on a focused row with no J/K cursor set resolves to that row, not nothing", () => {
  // No cursor has ever been set (fresh page load, or focus reached the row
  // via Tab rather than J/K).
  const noCursor = null;
  const focusedRow = "owner-x/repo-y";
  assert.equal(actionForKeyEvent({ key: "Enter" }, { isInputFocused: false }), "open-selected");
  assert.equal(resolveOpenTargetKey(noCursor, focusedRow), focusedRow);
});

test("resolveOpenTargetKey prefers DOM focus over a stale J/K cursor", () => {
  // The cursor still points at a PREVIOUSLY selected row, but focus has
  // since moved (Tab, or a click elsewhere) to a DIFFERENT row — DOM focus
  // must win, or Enter would silently reopen the wrong repo.
  assert.equal(resolveOpenTargetKey("owner-a/repo-a", "owner-b/repo-b"), "owner-b/repo-b");
});

test("resolveOpenTargetKey falls back to the J/K cursor when focus is not on any row", () => {
  // Focus is elsewhere (e.g. a header control); the cursor from the last
  // J/K move is the closest thing to a "current" row.
  assert.equal(resolveOpenTargetKey("owner-a/repo-a", null), "owner-a/repo-a");
});

test("resolveOpenTargetKey returns null when neither a cursor nor a focused row exists", () => {
  assert.equal(resolveOpenTargetKey(null, null), null);
});

// ------------------------------------------------------------- U-16 (state.js)
test("parseSearch / serializeSearch round-trip (incl. page/page_size), and clear a param absent from a new URL", () => {
  const s1 = {
    ...defaultState(), persona: "first_timer", ecosystem: "pypi", query: "flask", sort: "-porchlamp",
    page: 3, pageSize: 100, tab: "notranked", repo: "a/b",
  };
  const qs = serializeSearch(s1);
  assert.equal(qs, "?persona=first_timer&eco=pypi&q=flask&sort=-porchlamp&page=3&page_size=100&repo=a%2Fb&tab=notranked");
  const parsed = parseSearch(qs);
  assert.equal(parsed.persona, "first_timer");
  assert.equal(parsed.ecosystem, "pypi");
  assert.equal(parsed.query, "flask");
  assert.equal(parsed.sort, "-porchlamp");
  assert.equal(parsed.page, 3);
  assert.equal(parsed.pageSize, 100);
  assert.equal(parsed.tab, "notranked");
  assert.equal(parsed.repo, "a/b");

  // A bare "" (every param at its default) must serialize to the empty
  // string, and parsing it back must find nothing (the caller resets to
  // defaults itself before applying this — see app.js's popstate handler).
  assert.equal(serializeSearch(defaultState()), "");
  assert.deepEqual(parseSearch(""), {});
});

test("parseSearch rejects a page_size outside the allowed set", () => {
  const parsed = parseSearch("?page_size=37");
  assert.equal(parsed.pageSize, undefined);
});

test("reduce: SET_PARAMS never touches leaderboard data; LEADERBOARD_OK replaces the current page", () => {
  let state = defaultState();
  state = reduce(state, { type: "SET_PARAMS", payload: { ecosystem: "npm" } });
  assert.equal(state.ecosystem, "npm");
  assert.equal(state.leaderboard, null);

  const payload = { rows: [{ owner: "a", name: "b" }], total: 1, counts: { rated: 1, gated: 0, unrated: 0, total: 1 }, page: 1, page_size: 1, persona: "balanced" };
  state = reduce(state, { type: "LEADERBOARD_OK", data: payload });
  assert.equal(state.leaderboard, payload);
  assert.equal(state.leaderboardLoading, false);
  assert.equal(state.leaderboardRefetching, false);
});

test("reduce: SET_PAGE_SIZE can jump to a recomputed page in the same action", () => {
  let state = { ...defaultState(), page: 5, pageSize: 20 };
  state = reduce(state, { type: "SET_PAGE_SIZE", pageSize: 100, page: 1 });
  assert.equal(state.pageSize, 100);
  assert.equal(state.page, 1);
});

// -------------------------------------------------------------- U-17
test("countRankChanges: only counts rows whose rank actually differs, matched by owner/name", () => {
  const before = [
    { owner: "a", name: "x", rank: 1 },
    { owner: "b", name: "y", rank: 2 },
    { owner: "c", name: "z", rank: 3 },
  ];
  const after = [
    { owner: "b", name: "y", rank: 1 }, // moved: 2 -> 1
    { owner: "a", name: "x", rank: 2 }, // moved: 1 -> 2
    // "c/z" hidden by the persona: absent, must not count as "moved"
  ];
  assert.equal(countRankChanges(before, after), 2);
  assert.equal(countRankChanges(before, before), 0);
  assert.equal(countRankChanges(null, after), 0);
});

// With server-side pagination (one page loaded at a time), sort/search/ecosystem
// changes must go through the API so the server queries the complete dataset
// (see app.js loadLeaderboard).

// --------------------------------------------------------------------- copy.js
// The reason maps themselves are private to copy.js (only the wrapper
// functions are exported); a code the map actually covers must translate
// to something OTHER than the raw code itself (the functions' fallback is
// `|| reason`, which would otherwise silently pass this test on a typo).
// The black-hole sentence is shown on named projects, so it may only say what
// D-GATE-5 measures: human responses, on GitHub, inside 14 days. It measures
// nothing about whether reviews happen elsewhere (golang/go reviews in Gerrit)
// or whether merges have stopped.
test("copy.js: the black-hole reason states the measurement, not a verdict", () => {
  const text = copy.gateReasonText("black_hole");
  for (const needed of ["on GitHub", "14 days", "human"]) {
    assert.ok(text.includes(needed), `black-hole reason must say "${needed}": ${text}`);
  }
  for (const verdict of ["never", "stalled"]) {
    assert.ok(!text.includes(verdict), `black-hole reason claims more than is measured ("${verdict}"): ${text}`);
  }
});

test("copy.js: gate and unrated reason text covers every code the fixtures use", () => {
  for (const code of ["mirror", "archived", "unlicensed", "stale", "black_hole", "non_software", "fork"]) {
    const text = copy.gateReasonText(code);
    assert.notEqual(text, code, `no gate reason copy for ${code} (fell through to the raw code)`);
  }
  for (const code of ["insufficient_evidence", "unrated: insufficient_evidence", "no_external_workflow", "off_platform_routed"]) {
    const text = copy.unratedReasonText(code, 0.42);
    assert.notEqual(text, code, `no unrated reason copy for ${code} (fell through to the raw code)`);
  }
  // A project record carries the engine's own spelling, the Not-ranked list the bare one: same sentence.
  for (const bare of ["insufficient_evidence", "no_external_workflow", "off_platform_routed"]) {
    assert.equal(copy.unratedReasonText(`unrated: ${bare}`, 0.42), copy.unratedReasonText(bare, 0.42));
  }
  // "unrated: no_external_workflow" reaches the drawer as a GATE reason (the record is not eligible).
  assert.equal(copy.gateReasonText("unrated: no_external_workflow"), copy.unratedReasonText("no_external_workflow"));
  assert.equal(copy.gateReasonText("some_new_gate"), "some_new_gate");
  assert.equal(
    copy.personaGateReason("insufficient_evidence:decided_first_timer_prs"),
    "Not enough evidence to show under this lens."
  );
  // Engine vocabulary (porchlamp/core/persona.py).
  const hardBan = copy.personaGateReason("ai_hard_ban");
  assert.notEqual(hardBan, "ai_hard_ban", "no persona gate copy for ai_hard_ban");
  for (const code of ["elephant_factor_below_3", "high_barrier_to_entry", "criticality_below_threshold"]) {
    assert.notEqual(copy.personaGateReason(code), code, `no persona gate copy for ${code}`);
  }
});

test("copy.js: coverage band boundaries match D-MISS-3 exactly (>=0.95 full, [0.80,0.95) A, [0.60,0.80) B)", () => {
  assert.equal(copy.coverageBandForValue(0.95), "full");
  assert.equal(copy.coverageBandForValue(0.9499), "A");
  assert.equal(copy.coverageBandForValue(0.8), "A");
  assert.equal(copy.coverageBandForValue(0.7999), "B");
  assert.equal(copy.coverageBandForValue(0.6), "B");
  assert.equal(copy.coverageBandForValue(0.5999), "below");
});

test("copy.js: tierLabel appends '(capped)' only when the ceiling is the binding tier", () => {
  assert.equal(copy.tierLabel("A", "A"), "A (capped)");
  assert.equal(copy.tierLabel("S", "A"), "S"); // score already exceeds the ceiling tier -> ceiling isn't binding here
  assert.equal(copy.tierLabel("B", null), "B");
  assert.equal(copy.tierLabel(null, null), copy.MISSING_VALUE);
});

// ---------------------------------------------------------------------
// Snapshot fetch error handling: when a snapshot fetch fails, the footer/chip
// reflects the error state rather than remaining on a loading indicator.
// footerFieldsFor is the single decision app.js's syncFooter and the
// header chip both read.
test("footerFieldsFor: error takes priority over a stale/absent snapshot", () => {
  const errored = { ...defaultState(), snapshotError: { endpoint: "/v1/porchlamp/snapshot", status: 500 } };
  assert.deepEqual(footerFieldsFor(errored), {
    kind: "error",
    endpoint: "/v1/porchlamp/snapshot",
    status: 500,
    detail: undefined,
  });
});

test("footerFieldsFor: ok once a snapshot has loaded and there is no error", () => {
  const snapshot = { snapshot_id: "s1", benchmark_version: "v1", constants_hash: "c", manifest_root: "m" };
  const loaded = { ...defaultState(), snapshot };
  assert.deepEqual(footerFieldsFor(loaded), { kind: "ok", snapshot });
});

test("footerFieldsFor: loading is the ONLY resting state before either resolves", () => {
  assert.deepEqual(footerFieldsFor(defaultState()), { kind: "loading" });
});

test("footerFieldsFor: an error set alongside a stale snapshot still reports error (never silently reverts to ok)", () => {
  const snapshot = { snapshot_id: "s1", benchmark_version: "v1", constants_hash: "c", manifest_root: "m" };
  const state = {
    ...defaultState(),
    snapshot,
    snapshotError: { endpoint: "/v1/porchlamp/snapshot", status: 500 },
  };
  assert.equal(footerFieldsFor(state).kind, "error");
});

// --------------------------------------------------------------------- //
// Configure weights: normalizeWeights / applyPresetRespectingLocks
// Lock subsets x values, plus randomized walks. Ensures sum remains 1
// when dragging down with locked pillars.
// --------------------------------------------------------------------- //

function sumOf(weights) {
  return PILLAR_IDS.reduce((s, p) => s + weights[p], 0);
}

function assertValidDistribution(weights, locks, before, label) {
  const sum = sumOf(weights);
  assert.ok(Math.abs(sum - 1) < 1e-9, `${label}: sum ${sum} must be 1 (±1e-9)`);
  for (const p of PILLAR_IDS) {
    assert.ok(weights[p] >= 0, `${label}: ${p}=${weights[p]} must not be negative`);
    if (locks[p]) {
      assert.equal(weights[p], before[p], `${label}: locked pillar ${p} must not change`);
    }
  }
}

// Deterministic LCG (same shape as perf-fixtures.js's) so a "randomised"
// walk is reproducible.
function makeRng(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

test("normalizeWeights: every subset of 0-4 locked pillars keeps the sum at 1", () => {
  const base = { ...BALANCED_WEIGHTS };
  // All 32 subsets of the 5 pillars, excluding the full set (a slider that
  // is itself locked is never draggable in the UI — this function is only
  // ever called with `changedPillar` NOT among the locked ones).
  for (let mask = 0; mask < 32; mask++) {
    const locks = {};
    PILLAR_IDS.forEach((p, i) => {
      locks[p] = (mask & (1 << i)) !== 0;
    });
    const freePillars = PILLAR_IDS.filter((p) => !locks[p]);
    if (freePillars.length === 0) continue; // nothing to drag
    const changed = freePillars[0];
    for (const target of [0, 0.02, 0.15, 0.5, 1]) {
      const next = normalizeWeights(base, locks, changed, target);
      assertValidDistribution(next, locks, base, `mask=${mask} changed=${changed} target=${target}`);
    }
  }
});

test("normalizeWeights: four locked and fifth dragged down snaps to the only valid value", () => {
  const locks = { responsiveness: true, criticality: true, governance: true, accessibility: true };
  const next = normalizeWeights(BALANCED_WEIGHTS, locks, "rigor", 0.02);
  assertValidDistribution(next, locks, BALANCED_WEIGHTS, "four-locked drag down");
  // 0.25 + 0.25 + 0.20 + 0.15 (the four locked pillars) = 0.85, so rigor
  // MUST be 0.15 regardless of the 0.02 drag target.
  assert.ok(Math.abs(next.rigor - 0.15) < 1e-9);
});

test("normalizeWeights: randomised walk of locks + drags never breaks the invariant", () => {
  const rng = makeRng(20260913);
  let weights = { ...BALANCED_WEIGHTS };
  let locks = { responsiveness: false, criticality: false, governance: false, rigor: false, accessibility: false };
  for (let step = 0; step < 500; step++) {
    // Occasionally flip a lock (never locking every pillar at once).
    if (rng() < 0.3) {
      const p = PILLAR_IDS[Math.floor(rng() * PILLAR_IDS.length)];
      const wouldLockAll = !locks[p] && PILLAR_IDS.every((q) => q === p || locks[q]);
      if (!wouldLockAll) locks = { ...locks, [p]: !locks[p] };
    }
    const freePillars = PILLAR_IDS.filter((p) => !locks[p]);
    if (freePillars.length === 0) continue;
    const changed = freePillars[Math.floor(rng() * freePillars.length)];
    const target = rng();
    const before = weights;
    weights = normalizeWeights(weights, locks, changed, target);
    assertValidDistribution(weights, locks, before, `step=${step}`);
  }
});

test("applyPresetRespectingLocks: with no locks, reduces to the preset exactly", () => {
  for (const preset of Object.values(PERSONA_PRESET_WEIGHTS)) {
    const locks = { responsiveness: false, criticality: false, governance: false, rigor: false, accessibility: false };
    const next = applyPresetRespectingLocks(BALANCED_WEIGHTS, locks, preset);
    for (const p of PILLAR_IDS) assert.ok(Math.abs(next[p] - preset[p]) < 1e-9);
  }
});

test("applyPresetRespectingLocks: a locked pillar keeps its value and the rest still sum to 1", () => {
  const weights = { ...BALANCED_WEIGHTS };
  const locks = { responsiveness: false, criticality: false, governance: false, rigor: true, accessibility: false };
  const next = applyPresetRespectingLocks(weights, locks, PERSONA_PRESET_WEIGHTS.career_capital);
  assertValidDistribution(next, locks, weights, "preset with one lock");
});

// --------------------------------------------------------------------- //
// Shared weight normalization oracle: tests/cases/normalize_weights.json
// provides cross-language fixtures verified against both this JS module
// and tests/test_ui_weights_port.py.
// --------------------------------------------------------------------- //

const CASES_PATH = path.join(path.dirname(fileURLToPath(import.meta.url)), "cases", "normalize_weights.json");
const WEIGHT_CASES = JSON.parse(readFileSync(CASES_PATH, "utf8"));

test("normalizeWeights: shared JSON cases match the real JS implementation exactly", () => {
  assert.ok(WEIGHT_CASES.normalize_weights_cases.length >= 4, "the shared fixture must carry a non-trivial case set");
  for (const c of WEIGHT_CASES.normalize_weights_cases) {
    const result = normalizeWeights(c.weights, c.locks, c.changed_pillar, c.new_value);
    for (const p of PILLAR_IDS) {
      assert.ok(
        Math.abs(result[p] - c.expected[p]) < 1e-9,
        `${c.label}: ${p} = ${result[p]}, expected ${c.expected[p]}`
      );
    }
  }
});

test("applyPresetRespectingLocks: shared JSON cases match the real JS implementation exactly", () => {
  assert.ok(WEIGHT_CASES.apply_preset_respecting_locks_cases.length >= 3, "the shared fixture must carry a non-trivial case set");
  for (const c of WEIGHT_CASES.apply_preset_respecting_locks_cases) {
    const result = applyPresetRespectingLocks(c.weights, c.locks, c.preset);
    for (const p of PILLAR_IDS) {
      assert.ok(
        Math.abs(result[p] - c.expected[p]) < 1e-9,
        `${c.label}: ${p} = ${result[p]}, expected ${c.expected[p]}`
      );
    }
  }
});

// --------------------------------------------------------------------- //
// Configure weights: slider accessibility markup.
// Checks aria-label, aria-labelledby, and aria-valuetext attributes.
// Evaluated against renderControls' HTML string output.
// --------------------------------------------------------------------- //

test("renderControls: every slider carries aria-label, a matching aria-labelledby target, and a pillar-named aria-valuetext", () => {
  const state = { weights: { ...BALANCED_WEIGHTS }, weightLocks: { responsiveness: false, criticality: false, governance: false, rigor: false, accessibility: false } };
  const html = renderControls(state);
  for (const pillar of PILLAR_IDS) {
    const label = copy.PILLAR_NAMES[pillar];
    assert.ok(html.includes(`id="weight-${pillar}-label"`), `${pillar}: missing the label element's own id`);
    assert.ok(html.includes(`aria-label="${label}"`), `${pillar}: missing aria-label="${label}"`);
    assert.ok(html.includes(`aria-labelledby="weight-${pillar}-label"`), `${pillar}: aria-labelledby must point at the label id`);
    assert.ok(html.includes(`for="weight-${pillar}"`), `${pillar}: <label for> must still target the input's id too (belt-and-suspenders)`);
    // aria-valuetext must carry BOTH the pillar name and a percentage —
    // never just the bare number (a screen reader arrow-keying through
    // values must hear WHICH slider changed, not just the new value).
    const valuetextMatch = html.match(new RegExp(`data-weight-slider="${pillar}"[^>]*aria-valuetext="([^"]*)"`));
    assert.ok(valuetextMatch, `${pillar}: no aria-valuetext found on the slider`);
    assert.ok(valuetextMatch[1].startsWith(`${label}:`), `${pillar}: aria-valuetext "${valuetextMatch[1]}" must start with the pillar name`);
    assert.ok(/%$/.test(valuetextMatch[1]), `${pillar}: aria-valuetext "${valuetextMatch[1]}" must end with a percentage`);
  }
});

// --------------------------------------------------------------------- //
// repoToLoadFromState: URL-to-actions mapping for a drawer opened directly
// from URL query params (e.g. ?repo=owner/name).
// --------------------------------------------------------------------- //

test("repoToLoadFromState: a full URL round-trip finds the repo to load", () => {
  const parsed = parseSearch("?repo=riverline%2Fhttpkit&tab=matrix");
  const state = { ...defaultState(), ...parsed };
  assert.deepEqual(repoToLoadFromState(state), { owner: "riverline", name: "httpkit" });
});

test("repoToLoadFromState: repo= with no tab= at all still resolves (06 §3: 'with or without tab=')", () => {
  const parsed = parseSearch("?repo=riverline%2Fhttpkit");
  const state = { ...defaultState(), ...parsed };
  assert.deepEqual(repoToLoadFromState(state), { owner: "riverline", name: "httpkit" });
});

test("repoToLoadFromState: no repo in the URL loads nothing", () => {
  assert.equal(repoToLoadFromState(defaultState()), null);
});

test("repoToLoadFromState: a malformed repo (no slash) loads nothing rather than throwing", () => {
  assert.equal(repoToLoadFromState({ ...defaultState(), repo: "not-a-valid-key" }), null);
});

// --------------------------------------------------------------------- //
// Matrix: virtual-index keyboard navigation and canvas hit-testing above
// SVG_MARKER_BUDGET threshold, evaluated on the 5,000-point perf fixture.
// --------------------------------------------------------------------- //

const PERF_POINTS = synthesizeRows(5000);

test("perf-fixtures generates enough points to exceed SVG_MARKER_BUDGET (the canvas path this suite exercises)", () => {
  assert.ok(PERF_POINTS.length > SVG_MARKER_BUDGET);
});

test("nearestMarkerInDirection: a full virtual-index walk on 5,000 points always lands on a real, distinct point", () => {
  let key = null;
  const visited = new Set();
  for (let i = 0; i < 50; i++) {
    const next = nearestMarkerInDirection(PERF_POINTS, key, "down");
    if (!next) break; // ran off the edge of the domain — a legitimate stop, not a failure
    const nextKey = pointKey(next);
    assert.ok(!visited.has(nextKey) || nextKey === key, "must not orbit the same point forever");
    visited.add(nextKey);
    key = nextKey;
  }
  assert.ok(visited.size > 1, "the walk must actually move across multiple points");
});

test("nearestMarkerInDirection: with no current key, falls back to the first point (a valid starting focus)", () => {
  const next = nearestMarkerInDirection(PERF_POINTS, null, "down");
  assert.equal(pointKey(next), pointKey(PERF_POINTS[0]));
});

test("hitTestPoint: an exact click on a point's own domain position finds it (or a genuinely closer neighbour, never nothing)", () => {
  const target = PERF_POINTS[42];
  const domainX = toSvgX(target.friction);
  const domainY = toSvgY(target.leverage);
  const hit = hitTestPoint(PERF_POINTS, domainX, domainY, 5);
  assert.ok(hit, "a click exactly at a point's own position must hit something within a 5-unit tolerance");
  // The hit must be AT LEAST as close as the target itself — with 5,000
  // densely-packed synthetic points a different point can legitimately be
  // marginally closer to the exact click coordinate than the "intended"
  // one (verified against the fixture server);
  // the correctness property is "closest within tolerance", not "always
  // the specific point a human eye would guess".
  const distTo = (p) => Math.hypot(toSvgX(p.friction) - domainX, toSvgY(p.leverage) - domainY);
  assert.ok(distTo(hit) <= distTo(target) + 1e-9);
});

test("hitTestPoint: a click far from every point finds nothing (never opens the wrong repo on empty space)", () => {
  // (250, 250) is far outside the 0..100 domain entirely.
  assert.equal(hitTestPoint(PERF_POINTS, 250, 250, 5), null);
});

test("hitTestPoint: a smaller tolerance can legitimately find nothing even near real points", () => {
  // An essentially-zero tolerance only hits a point sitting EXACTLY on the
  // queried coordinate — vanishingly unlikely across floating-point
  // leverage/friction values, so this must normally return null.
  const anyPoint = PERF_POINTS[0];
  const hit = hitTestPoint(PERF_POINTS, toSvgX(anyPoint.friction) + 50, toSvgY(anyPoint.leverage) + 50, 1e-6);
  assert.equal(hit, null);
});

// --------------------------------------------------------------------- //
// Compare view shell key stability: ensures shellKey(state) does not change
// on keystroke/query updates so the focused input element is preserved,
// while changing on repo chip addition/removal or shape transitions.
// --------------------------------------------------------------------- //

const COMPARE_BASE_STATE = {
  compareRepos: ["riverline/httpkit", "ivywood/template-cli-71"],
  compareQuery: "",
  compareSearchResults: [],
  compareDataByRepo: {},
};

test("compareShellKey: a keystroke (compareQuery + compareSearchResults changing) never changes the key", () => {
  const before = compareShellKey(COMPARE_BASE_STATE);
  const afterKeystrokes = [
    { ...COMPARE_BASE_STATE, compareQuery: "r" },
    { ...COMPARE_BASE_STATE, compareQuery: "ri" },
    { ...COMPARE_BASE_STATE, compareQuery: "riv" },
    {
      ...COMPARE_BASE_STATE,
      compareQuery: "riv",
      compareSearchResults: [{ owner: "riverline", name: "other-repo", ecosystem: "npm" }],
    },
  ];
  for (const after of afterKeystrokes) {
    assert.equal(compareShellKey(after), before, `query=${JSON.stringify(after.compareQuery)} must not change the shell key`);
  }
});

test("compareShellKey: a per-repo data fetch landing (compareDataByRepo) never changes the key", () => {
  const before = compareShellKey(COMPARE_BASE_STATE);
  const afterFetch = {
    ...COMPARE_BASE_STATE,
    compareDataByRepo: { "riverline/httpkit": { loading: false, kind: "rated", row: { porchlamp: 90 }, evidence: [] } },
  };
  assert.equal(compareShellKey(afterFetch), before, "a repo's data landing must be a results/table patch, never a shell rebuild");
});

test("compareShellKey: adding or removing a chip changes the key (a real shell rebuild)", () => {
  const before = compareShellKey(COMPARE_BASE_STATE);
  const added = { ...COMPARE_BASE_STATE, compareRepos: [...COMPARE_BASE_STATE.compareRepos, "acme/widgets"] };
  const removed = { ...COMPARE_BASE_STATE, compareRepos: [COMPARE_BASE_STATE.compareRepos[0]] };
  assert.notEqual(compareShellKey(added), before, "adding a chip must change the shell key");
  assert.notEqual(compareShellKey(removed), before, "removing a chip must change the shell key");
  assert.notEqual(compareShellKey(added), compareShellKey(removed));
});

test("compareShellKey: a chip-count shape transition (empty / partial-min / table) always changes the key", () => {
  const empty = { ...COMPARE_BASE_STATE, compareRepos: [] };
  const partialMin = { ...COMPARE_BASE_STATE, compareRepos: [COMPARE_BASE_STATE.compareRepos[0]] };
  const table = COMPARE_BASE_STATE;
  assert.equal(compareShellShape(empty), "empty");
  assert.equal(compareShellShape(partialMin), "partial-min");
  assert.equal(compareShellShape(table), "table");
  const keys = new Set([compareShellKey(empty), compareShellKey(partialMin), compareShellKey(table)]);
  assert.equal(keys.size, 3, "each distinct shape must produce a distinct shell key");
});

// --------------------------------------------------------------------- //
// Repo-detail error rendering: a 404 renders copy.DRAWER_NOT_FOUND_TEXT
// without a retry button, while 500/timeout/network errors render the
// retry banner.
// --------------------------------------------------------------------- //

function drawerStateFor(overrides) {
  return { ...defaultState(), repo: "nobody/nothing", repoDetailByKey: {}, repoDetailLoading: false, repoDetailError: null, ...overrides };
}

test("renderDrawer: a 404 (genuinely not found) shows copy.DRAWER_NOT_FOUND_TEXT and no retry button", () => {
  const html = renderDrawer(drawerStateFor({ repoDetailError: { endpoint: "/v1/porchlamp/repos/nobody/nothing", status: 404, detail: "no such repo: nobody/nothing" } }));
  assert.ok(html.includes(copy.DRAWER_NOT_FOUND_TEXT), "must render the not-found copy for a 404");
  assert.ok(!html.includes("data-repo-detail-retry"), "a genuine 404 must not offer a retry button (it will 404 again)");
});

test("renderDrawer: a non-404 error (500/timeout/network) shows the retry banner, not the not-found copy", () => {
  for (const status of [500, "timeout", 0]) {
    const html = renderDrawer(drawerStateFor({ repoDetailError: { endpoint: "/v1/porchlamp/repos/nobody/nothing", status, detail: "boom" } }));
    assert.ok(!html.includes(copy.DRAWER_NOT_FOUND_TEXT), `status=${status}: must not show the not-found copy`);
    assert.ok(html.includes("data-repo-detail-retry"), `status=${status}: must offer a retry button`);
  }
});

test("renderDrawer: still loading shows the loading copy, neither not-found nor the retry banner", () => {
  const html = renderDrawer(drawerStateFor({ repoDetailLoading: true }));
  assert.ok(html.includes(copy.DRAWER_LOADING_TEXT));
  assert.ok(!html.includes(copy.DRAWER_NOT_FOUND_TEXT));
  assert.ok(!html.includes("data-repo-detail-retry"));
});

// --------------------------------------------------------------------- //
// Repo-detail flat response contract: the API returns flat RepoDetailResponse
// objects, and classifyKind derives kind client-side from eligible/rated flags.
// --------------------------------------------------------------------- //

test("classifyKind: ineligible (gated at stage 0) is 'gated' regardless of rated", () => {
  assert.equal(classifyKind({ eligible: false, rated: false }), "gated");
});

test("classifyKind: eligible but not rated (below the coverage floor) is 'unrated'", () => {
  assert.equal(classifyKind({ eligible: true, rated: false }), "unrated");
});

test("classifyKind: eligible and rated is 'rated'", () => {
  assert.equal(classifyKind({ eligible: true, rated: true }), "rated");
});

const FLAT_RATED_DETAIL = {
  owner: "riverline", name: "httpkit", ecosystem: "pypi",
  snapshot_id: "snap-test", benchmark_version: "2.0.0-test", raw_hash: "a".repeat(64),
  eligible: true, gate_reason: null, rated: true, unrated_reason: null,
  rank: 1, tie_group: null, porchlamp: 98.34, coverage: 1.0, tier: "S", tier_ceiling: null,
  quadrant: "Sweet Spot", leverage: 90, friction: 5, leverage_coverage: 1.0, friction_coverage: 1.0,
  p_responsiveness: 95, c_responsiveness: 1.0, p_criticality: 90, c_criticality: 1.0,
  p_governance: 92, c_governance: 1.0, p_rigor: 88, c_rigor: 1.0, p_accessibility: 91, c_accessibility: 1.0,
  persona_gates: {}, persona_composites: {}, suspicion: [], stars_audited: 16282,
};

const FLAT_GATED_DETAIL = {
  owner: "gated-org", name: "repo-mirror-0", ecosystem: "cargo",
  snapshot_id: "snap-test", benchmark_version: "2.0.0-test", raw_hash: "b".repeat(64),
  eligible: false, gate_reason: "mirror", rated: false, unrated_reason: null,
  rank: null, tie_group: null, porchlamp: null, coverage: 0.0, tier: null, tier_ceiling: null,
  quadrant: null, leverage: null, friction: null, leverage_coverage: null, friction_coverage: null,
  p_responsiveness: null, c_responsiveness: null, p_criticality: null, c_criticality: null,
  p_governance: null, c_governance: null, p_rigor: null, c_rigor: null, p_accessibility: null, c_accessibility: null,
  persona_gates: {}, persona_composites: {}, suspicion: [], stars_audited: null,
};

const FLAT_UNRATED_DETAIL = {
  ...FLAT_GATED_DETAIL,
  owner: "unrated-org", name: "repo-insufficient_evidence-0", ecosystem: "npm",
  eligible: true, gate_reason: null, unrated_reason: "unrated: insufficient_evidence", coverage: 0.42,
};

function stateWithRepoDetail(detail) {
  const key = `${detail.owner}/${detail.name}`;
  return {
    ...defaultState(),
    repo: key,
    repoDetailByKey: { [key]: detail },
    repoDetailLoading: false,
    repoDetailError: null,
    evidenceByRepo: {},
    evidenceError: null,
  };
}

test("findRow + classifyKind: the real (flat) shape round-trips through renderDrawer for a RATED repo (composite/pillar/persona/integrity sections)", () => {
  const state = stateWithRepoDetail(FLAT_RATED_DETAIL);
  assert.deepEqual(findRow(state, "riverline", "httpkit"), FLAT_RATED_DETAIL);
  assert.equal(classifyKind(FLAT_RATED_DETAIL), "rated");
  const html = renderDrawer(state);
  assert.ok(html.includes("riverline"));
  assert.ok(html.includes("98.34"), "the composite score must render");
  assert.ok(html.includes(FLAT_RATED_DETAIL.raw_hash.slice(0, 16)), "the integrity section must render raw_hash");
});

// --------------------------------------------------------------------- //
// Gated repository rendering: renders gate reason above the fold, followed
// by three collapsed <details> sections (Pillars, Personas, Integrity).
// Composite and Playbook sections are omitted.
// --------------------------------------------------------------------- //

test("findRow + classifyKind: a GATED repo renders the gate sentence first, then Pillars/Persona/Integrity collapsed, with Composite omitted", () => {
  const state = stateWithRepoDetail(FLAT_GATED_DETAIL);
  assert.equal(classifyKind(FLAT_GATED_DETAIL), "gated");
  const html = renderDrawer(state);
  assert.ok(html.includes("repo-mirror-0"));

  const gateSentence = copy.gateReasonText(FLAT_GATED_DETAIL.gate_reason);
  const gateSentenceIndex = html.indexOf(gateSentence);
  const firstDetailsIndex = html.indexOf("<details");
  assert.ok(gateSentenceIndex >= 0, "the gate reason sentence must render");
  assert.ok(firstDetailsIndex >= 0, "at least one <details> section must render");
  assert.ok(gateSentenceIndex < firstDetailsIndex, "the gate sentence must render before any <details> section (nothing else above the fold)");

  // Composite is omitted entirely -- no score/tier/quadrant was ever computed.
  assert.ok(!html.includes(copy.DRAWER_SECTION_COMPOSITE), "no Composite section for a gated repo");
  // Playbook is omitted too -- nothing to recommend for a disqualified repo.
  assert.ok(!html.includes(copy.DRAWER_SECTION_PLAYBOOK), "no Playbook section for a gated repo");

  // Exactly three <details> sections, every one collapsed (no `open`
  // attribute) so the fold shows only the gate sentence.
  const detailsTags = html.match(/<details[^>]*>/g) || [];
  assert.equal(detailsTags.length, 3, `expected exactly 3 <details> sections for gated, got ${detailsTags.length}`);
  for (const tag of detailsTags) {
    assert.ok(!/\bopen\b/.test(tag), `every gated section must be collapsed, found: ${tag}`);
  }
  assert.ok(html.includes(copy.DRAWER_SECTION_PILLARS));
  assert.ok(html.includes(copy.DRAWER_SECTION_PERSONAS));
  assert.ok(html.includes(copy.DRAWER_SECTION_INTEGRITY));

  // Persona view: every known persona listed against the SAME repo-level
  // gate reason (persona_gates/persona_composites are both {} on the wire
  // for a gated repo -- disqualification happens before any persona lens
  // is ever applied).
  for (const personaName of Object.values(copy.PERSONA_NAMES)) {
    assert.ok(html.includes(personaName), `persona "${personaName}" must be listed in the Persona view`);
  }
  const gateSentenceOccurrences = html.split(gateSentence).length - 1;
  assert.equal(
    gateSentenceOccurrences,
    1 + Object.keys(copy.PERSONA_NAMES).length,
    "the gate sentence must appear once in the identity banner plus once per persona row"
  );

  // Integrity: raw_hash/snapshot_id still render -- both present on the
  // wire for a gated repo (a synthetic raw_hash stands in server-side,
  // scripts/serve_ui_fixtures.py::_synthetic_raw_hash).
  assert.ok(html.includes(FLAT_GATED_DETAIL.raw_hash.slice(0, 16)));
  assert.ok(html.includes(FLAT_GATED_DETAIL.snapshot_id));
});

test("findRow + classifyKind: a GATED repo's Pillars section renders its real evidence rows once loaded (openRepo does not skip evidence for gated)", () => {
  const detail = FLAT_GATED_DETAIL;
  const key = `${detail.owner}/${detail.name}`;
  // Mirrors tests/fixtures/ui/evidence-gated-org__repo-archived-1.json --
  // a real gated repo's real evidence fixture (one unmeasured governance
  // parameter).
  const evidenceRow = {
    param_id: "archived_flag", pillar: "governance", value: null, score: null, n: null,
    window_start: null, window_end: null, method: null, source: null, source_sha256: null,
    fetched_at: null, measured: false, unmeasured_reason: "not_applicable",
  };
  const state = { ...stateWithRepoDetail(detail), evidenceByRepo: { [key]: [evidenceRow] } };
  const html = renderDrawer(state);
  assert.ok(html.includes(copy.PILLAR_NAMES.governance), "the governance pillar heading must render inside Pillars and evidence");
  assert.ok(html.includes(copy.missingValueReason("not_applicable")), "the unmeasured-reason text must render for the evidence row");
});

test("findRow + classifyKind: an UNRATED repo renders its evidence table, not a crash on missing composite fields", () => {
  const state = stateWithRepoDetail(FLAT_UNRATED_DETAIL);
  assert.equal(classifyKind(FLAT_UNRATED_DETAIL), "unrated");
  // Must not throw (the exact regression: `row` was `undefined` under the
  // old `{kind, row}` shape once the real API's flat response arrived).
  const html = renderDrawer(state);
  assert.ok(html.includes("repo-insufficient_evidence-0"));

  // An unrated repo renders exactly one open <details> section, with
  // persona, integrity, and composite sections omitted.
  const detailsTags = html.match(/<details[^>]*>/g) || [];
  assert.equal(detailsTags.length, 1, "unrated must still render exactly one <details> section");
  assert.ok(/\bopen\b/.test(detailsTags[0]), "unrated's single section must stay open, unchanged");
  assert.ok(!html.includes(copy.DRAWER_SECTION_PERSONAS), "no Persona view section for unrated (unchanged)");
  assert.ok(!html.includes(copy.DRAWER_SECTION_INTEGRITY), "no Integrity section for unrated (unchanged)");
  assert.ok(!html.includes(copy.DRAWER_SECTION_COMPOSITE), "no Composite section for unrated (unchanged)");
});

// --------------------------------------------------------------------- //
// AUD-WEB H3: the scoring engine emits "S+" as a real, highest tier
// (porchlamp/core/constants.py: TIER_CUTOFFS = ((90.0, "S+"), (80.0, "S"), ...),
// TIER_ORDER = ("D","C","B","A","S","S+")). tierLabel must NOT rewrite it down
// to "S" — a 95-score repo and an 82-score repo must read differently — while
// still appending "(capped)" when the coverage ceiling is the binding tier.
// --------------------------------------------------------------------- //
test("copy.js: tierLabel keeps S+ distinct from S and caps it only when the ceiling is S+", () => {
  assert.equal(copy.tierLabel("S+", null), "S+");
  assert.equal(copy.tierLabel("S+", "S+"), "S+ (capped)");
  assert.equal(copy.tierLabel("A", null), "A");
});

// --------------------------------------------------------------------- //
// AUD-WEB H1: web/v2/views/drawer.js:351 reads copy.RETRY_LABEL for
// the repo-detail retry button. That export was missing, so the button read
// the literal text "undefined". It must be a real, non-empty label.
// --------------------------------------------------------------------- //
test("copy.js: RETRY_LABEL is a non-empty string (the retry button is never 'undefined')", () => {
  assert.equal(typeof copy.RETRY_LABEL, "string");
  assert.ok(copy.RETRY_LABEL.length > 0, "RETRY_LABEL must be a non-empty string");
});

// --------------------------------------------------------------------- //
// AUD-WEB H1 (rendered path): the drawer's retryable repo-detail error banner
// must not render the literal text "undefined" anywhere in its HTML. Built
// the same way the existing renderDrawer error tests above build markup.
// --------------------------------------------------------------------- //
test("renderDrawer: a retryable repo-detail error renders no literal 'undefined' in its HTML", () => {
  const html = renderDrawer(
    drawerStateFor({ repoDetailError: { endpoint: "/v1/porchlamp/repos/nobody/nothing", status: 500, detail: "boom" } }),
  );
  assert.ok(html.includes("data-repo-detail-retry"), "the retry banner must render for a non-404 error");
  assert.ok(!html.includes("undefined"), `drawer error HTML must not contain the literal text "undefined":\n${html}`);
});

// --------------------------------------------------------------------- //
// AUD-WEB H6: a below-floor coverage (< 0.6) is a data-quality warning and
// must render with its own badge-coverage-below class, not the ordinary
// "Ceiling B" band badge class — otherwise the warning looks like a normal B.
// --------------------------------------------------------------------- //
test("renderCoverageBadge: a below-floor coverage uses badge-coverage-below, not the B badge", () => {
  const html = renderCoverageBadge(0.42);
  assert.ok(html.includes("badge-coverage-below"), `below-floor badge must carry badge-coverage-below, got: ${html}`);
  assert.ok(
    !/class="badge badge-coverage-b"/.test(html),
    `below-floor badge must not reuse the plain B badge class, got: ${html}`,
  );
});

// --------------------------------------------------------------------- //
// AUD-WEB H6 (styled): the badge-coverage-below class must actually exist in
// the stylesheet, styled off the theme's --coverage-below token (defined for
// light and dark in tokens.css), so the badge is distinct and legible in both
// themes rather than an unstyled plain-B badge.
// --------------------------------------------------------------------- //
test("components.css: .badge-coverage-below is styled off var(--coverage-below)", () => {
  const cssPath = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "styles", "components.css");
  const css = readFileSync(cssPath, "utf8");
  assert.ok(css.includes(".badge-coverage-below"), "components.css must define a .badge-coverage-below rule");
  assert.match(css, /\.badge-coverage-below[^{]*\{[^}]*--coverage-below[^}]*\}/);
});

// ---------------------------------------------------------------------
// Persona-visible counts (regression, 2026-09-21).
//
// A persona view is a RE-WEIGHTED projection: porchlamp.core.persona computes
// persona_composites[p].coverage from that persona's own pillar weights, and
// the store admits a row on that coverage clearing 0.60 -- not on the
// balanced `rated` flag. So a persona that down-weights the pillars a sweep
// has not collected yet is visible for MORE repos than balanced, not fewer.
//
// Measured on snapshot 2026-09-21-L3gp: counts.rated = 94 while
// persona_visible_counts.ai_assisted = 303 and senior_systems = 98. Two
// things broke on those numbers:
//   1. the Ranked sub-tab kept showing 94 while the table under it paged 303
//      rows ("Page 1 of 16 (303 rows)");
//   2. `hidden = counts.rated - visible` went to -209, and the disclosure was
//      rendered only when `> 0`, so it silently vanished exactly when the
//      lens was changing the set the most.
// Both assumed persona-visible is a SUBSET of balanced-rated. It is not.
// ---------------------------------------------------------------------

const L3GP_COUNTS = { rated: 94, gated: 3498, unrated: 2163, total: 5755 };
const L3GP_SNAPSHOT = {
  persona_visible_counts: {
    ai_assisted: 303, balanced: 94, career_capital: 0, first_timer: 0, senior_systems: 98,
  },
};

test("leaderboard: personaVisibleCount follows the lens, not the balanced rated flag", () => {
  assert.equal(personaVisibleCount(L3GP_COUNTS, L3GP_SNAPSHOT, "balanced"), 94);
  assert.equal(personaVisibleCount(L3GP_COUNTS, L3GP_SNAPSHOT, "ai_assisted"), 303);
  assert.equal(personaVisibleCount(L3GP_COUNTS, L3GP_SNAPSHOT, "senior_systems"), 98);
  assert.equal(personaVisibleCount(L3GP_COUNTS, L3GP_SNAPSHOT, "first_timer"), 0);
});

test("leaderboard: the Ranked tab count matches what the table actually pages", () => {
  const html = renderTabs("ranked", L3GP_COUNTS, personaVisibleCount(L3GP_COUNTS, L3GP_SNAPSHOT, "ai_assisted"));
  assert.match(html, /Ranked<span class="count">303<\/span>/);
  // Not-ranked is persona-independent (gate reasons are persona-free).
  assert.match(html, /Not ranked<span class="count">5661<\/span>/);
});

test("leaderboard: personaDelta reports BOTH directions, not just hidden", () => {
  // more visible than balanced -> "extra", the case that used to disappear
  assert.deepEqual(personaDelta(L3GP_COUNTS, L3GP_SNAPSHOT, "ai_assisted"), { extra: 209 });
  // fewer visible than balanced -> "hidden", the original U-4 case
  assert.deepEqual(personaDelta(L3GP_COUNTS, L3GP_SNAPSHOT, "first_timer"), { hidden: 94 });
  // equal, and balanced itself -> no disclosure at all
  assert.equal(personaDelta(L3GP_COUNTS, L3GP_SNAPSHOT, "balanced"), null);
  assert.equal(personaDelta({ ...L3GP_COUNTS, rated: 303 }, L3GP_SNAPSHOT, "ai_assisted"), null);
});

test("leaderboard: the counts strip renders the extra-visible disclosure", () => {
  const html = renderCountsStrip(L3GP_COUNTS, personaDelta(L3GP_COUNTS, L3GP_SNAPSHOT, "ai_assisted"));
  assert.match(html, /Also visible in this lens \(209\)/);
  const hiddenHtml = renderCountsStrip(L3GP_COUNTS, personaDelta(L3GP_COUNTS, L3GP_SNAPSHOT, "first_timer"));
  assert.match(hiddenHtml, /Hidden by this lens \(94\)/);
});

// --- over-correction guards ------------------------------------------- #

test("leaderboard: a missing snapshot or unknown persona falls back to balanced rated", () => {
  assert.equal(personaVisibleCount(L3GP_COUNTS, null, "ai_assisted"), 94);
  assert.equal(personaVisibleCount(L3GP_COUNTS, L3GP_SNAPSHOT, "not_a_persona"), 94);
  assert.equal(personaVisibleCount(null, L3GP_SNAPSHOT, "ai_assisted"), null);
  assert.equal(personaDelta(L3GP_COUNTS, null, "ai_assisted"), null);
});

// ---------------------------------------------------------------------
// Corpus strip: the snapshot's rated/gated/unrated split as a meter.
// ---------------------------------------------------------------------

test("leaderboard: the corpus meter segments are proportional and sum to the total", () => {
  const html = renderCountsStrip(L3GP_COUNTS, null);
  // every segment carries a visible label + count, never colour alone
  assert.match(html, /Rated/);
  assert.match(html, /Gated/);
  assert.match(html, /Unrated/);
  assert.match(html, /94/);
  assert.match(html, /3498/);
  assert.match(html, /2163/);
  // proportions, to one decimal: 94/5755 = 1.6%, 3498/5755 = 60.8%, 2163/5755 = 37.6%
  assert.match(html, /1\.6%/);
  assert.match(html, /60\.8%/);
  assert.match(html, /37\.6%/);
  // the meter is an accessible progressbar-free group with a text summary
  assert.match(html, /role="img"/);
  assert.match(html, /aria-label="[^"]*5755[^"]*"/);
});

test("leaderboard: the corpus meter survives a zero total without dividing by zero", () => {
  const html = renderCountsStrip({ rated: 0, gated: 0, unrated: 0, total: 0 }, null);
  assert.doesNotMatch(html, /NaN/);
  assert.doesNotMatch(html, /Infinity/);
});

test("table: every column header carries data-col so th and td can be targeted together", () => {
  const html = renderColumnHeader("-porchlamp", false);
  for (const key of ["rank", "repository", "porchlamp", "coverage", "tier", "quadrant", "pillars", "suspicion", "stars"]) {
    assert.match(html, new RegExp(`data-col="${key}"`), `missing data-col="${key}"`);
  }
  // the persona-only delta column appears only with the flag on
  assert.doesNotMatch(html, /data-col="delta"/);
  assert.match(renderColumnHeader("-porchlamp", true), /data-col="delta"/);
});

// --------------------------------------------------------------------- //
// Screen F (methodology), redesign 2026-09-22. Rendered against the real
// fixtures in tests/fixtures/ui/ -- methodology.json is generated from
// methodology_document(), not hand-typed (tests/test_ui_fixtures.py).
// --------------------------------------------------------------------- //

const UI_FIXTURES = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "tests", "fixtures", "ui");
const METHODOLOGY_DOC = JSON.parse(readFileSync(path.join(UI_FIXTURES, "methodology.json"), "utf8"));
const SNAPSHOT_DOC = JSON.parse(readFileSync(path.join(UI_FIXTURES, "snapshot.json"), "utf8"));
function methodologyShell(snapshot, doc = METHODOLOGY_DOC) {
  return renderMethodologyShell({ methodology: doc, snapshot, methodologyLoading: false, methodologyError: null });
}

test("methodology: the index and the body come from one list (same ids, same order, each once)", () => {
  const html = methodologyShell(SNAPSHOT_DOC);
  const index = sectionIndex(METHODOLOGY_DOC, SNAPSHOT_DOC);
  const sectionIds = [...html.matchAll(/<section class="doc-section" id="([^"]+)"/g)].map((m) => m[1]);
  const jumpIds = [...html.matchAll(/class="doc-index-link" href="#[^"]+" data-doc-jump="([^"]+)"/g)].map((m) => m[1]);
  assert.equal(index.length, 10, "snapshot + the nine methodology sections");
  assert.deepEqual(sectionIds, index.map((e) => e.id));
  assert.deepEqual(jumpIds, index.map((e) => e.id));
  assert.equal(new Set(sectionIds).size, sectionIds.length, "section ids are unique");
});

test("brand: the header carries the symbol (decorative), the name and the slogan, all from copy.js", () => {
  const html = renderBrand();
  assert.match(html, /<svg class="brand-mark"[^>]*aria-hidden="true"/, "the symbol is hidden from assistive technology");
  assert.ok(html.includes(`</svg>${escapeHtml(copy.BRAND_NAME)}</span>`), "the name follows the symbol");
  assert.ok(html.includes(`<span class="brand-tagline">${escapeHtml(copy.BRAND_TAGLINE)}</span>`), "the slogan");
});

test("methodology: Why Porchlamp sits above the document, each paragraph once, and only once the document has loaded", () => {
  const html = methodologyShell(SNAPSHOT_DOC);
  const why = html.indexOf('<section class="why-card"');
  assert.ok(why > -1, "the Why section renders");
  assert.ok(why < html.indexOf('<div class="doc-layout"'), "and comes before the document");
  assert.ok(html.includes(`>${escapeHtml(copy.WHY_HEADING)}</h2>`));
  for (const paragraph of copy.WHY_PARAGRAPHS) {
    assert.equal(html.split(`<p>${escapeHtml(paragraph)}</p>`).length - 1, 1, paragraph.slice(0, 40));
  }
  const loading = renderMethodologyShell({ methodology: null, snapshot: null, methodologyLoading: true, methodologyError: null });
  assert.ok(!loading.includes("why-card"), "no Why section while the document is loading");
});

test("methodology: the Snapshot section appears only once the snapshot payload has loaded", () => {
  assert.ok(sectionIndex(METHODOLOGY_DOC, SNAPSHOT_DOC).some((e) => e.id === "methodology-snapshot"));
  assert.ok(!sectionIndex(METHODOLOGY_DOC, null).some((e) => e.id === "methodology-snapshot"));
  assert.ok(!methodologyShell(null).includes('id="methodology-snapshot"'));
});

test("methodology: section counts are the payload's own lengths, never literals", () => {
  const byId = Object.fromEntries(sectionIndex(METHODOLOGY_DOC, SNAPSHOT_DOC).map((e) => [e.id, e.count]));
  assert.equal(byId["methodology-parameters"], METHODOLOGY_DOC.parameters.length);
  assert.equal(byId["methodology-gates"], METHODOLOGY_DOC.gates.length);
  assert.equal(byId["methodology-detectors"], METHODOLOGY_DOC.detectors.length);
  assert.equal(byId["methodology-rfc_log"], METHODOLOGY_DOC.rfc_log.length);
  assert.equal(byId["methodology-snapshot"], null, "the snapshot is not a list, so it carries no count");
});

test("methodology: parameterGroups keeps every parameter exactly once, grouped under its own pillar, in pillar order", () => {
  const groups = parameterGroups(METHODOLOGY_DOC.parameters, METHODOLOGY_DOC.pillars);
  const expectedOrder = METHODOLOGY_DOC.pillars.map((p) => p.name).filter((n) => METHODOLOGY_DOC.parameters.some((p) => p.pillar === n));
  assert.deepEqual(groups.map((g) => g.pillar), expectedOrder);
  const flat = groups.flatMap((g) => g.parameters.map((p) => p.input_id));
  assert.deepEqual([...flat].sort(), METHODOLOGY_DOC.parameters.map((p) => p.input_id).sort());
  for (const g of groups) {
    for (const p of g.parameters) assert.equal(p.pillar, g.pillar, `${p.input_id} is filed under ${g.pillar}`);
  }
});

test("methodology: a parameter naming a pillar the pillar list lacks still gets a group (guard: never dropped)", () => {
  const orphan = { ...METHODOLOGY_DOC.parameters[0], input_id: "orphan_input", pillar: "unlisted_pillar" };
  const groups = parameterGroups([...METHODOLOGY_DOC.parameters, orphan], METHODOLOGY_DOC.pillars);
  const last = groups[groups.length - 1];
  assert.equal(last.pillar, "unlisted_pillar", "unknown pillars follow the known ones");
  assert.deepEqual(last.parameters.map((p) => p.input_id), ["orphan_input"]);
  assert.equal(last.weight, null, "no weight is invented for a pillar the payload does not weigh");
});

test("methodology: every parameter is a row of the rendered table (grouping drops nothing)", () => {
  const html = methodologyShell(SNAPSHOT_DOC);
  const rowIds = [...html.matchAll(/<th scope="row" class="cell-id"><code>([^<]+)<\/code><\/th>/g)].map((m) => m[1]);
  for (const p of METHODOLOGY_DOC.parameters) assert.ok(rowIds.includes(p.input_id), `${p.input_id} is rendered`);
});

test("methodology: an enum parameter shows every level WITH its score_table score (the curve text points there)", () => {
  // Before the redesign the domain cell printed only the level order, while
  // the curve said "see this parameter's value_domain.score_table".
  const html = methodologyShell(SNAPSHOT_DOC);
  const enums = METHODOLOGY_DOC.parameters.filter((p) => p.value_domain && p.value_domain.kind === "enum");
  assert.ok(enums.length > 0, "the fixture must carry enum parameters for this test to mean anything");
  for (const p of enums) {
    const d = p.value_domain;
    const table = d.score_table || {};
    const pairs = d.values_worst_to_best
      ? d.values_worst_to_best.map((lv) => [lv, table[lv]])
      : Object.entries(d.persona_orders).flatMap(([persona, order]) => order.map((lv) => [lv, (table[lv] || {})[persona]]));
    for (const [level, score] of pairs) {
      assert.ok(
        html.includes(`<code class="spec-chip">${level}<span class="spec-chip-value">${score}</span></code>`),
        `${p.input_id}: ${level} -> ${score}`
      );
    }
  }
});

test("methodology: the change log is newest first, and only the entry that produced the page's constants hash is Current", () => {
  const entries = changeLogEntries(METHODOLOGY_DOC.rfc_log, METHODOLOGY_DOC.constants_hash);
  assert.deepEqual(entries.map((e) => e.rfc), [...METHODOLOGY_DOC.rfc_log].reverse().map((e) => e.rfc));
  const current = entries.filter((e) => e.current);
  assert.equal(current.length, 1);
  assert.equal(current[0].hash_after, METHODOLOGY_DOC.constants_hash);
  assert.equal(METHODOLOGY_DOC.rfc_log.length, entries.length, "reversing must not drop or add entries");
  assert.equal(METHODOLOGY_DOC.rfc_log[0].rfc, "genesis", "the payload itself is left in chronological order");
});

test("methodology: when no logged change produced the constants hash, no entry claims to be Current (guard)", () => {
  assert.ok(changeLogEntries(METHODOLOGY_DOC.rfc_log, "0".repeat(64)).every((e) => !e.current));
  assert.ok(changeLogEntries(METHODOLOGY_DOC.rfc_log, "").every((e) => !e.current));
  assert.ok(changeLogEntries(METHODOLOGY_DOC.rfc_log, null).every((e) => !e.current));
  const html = methodologyShell(SNAPSHOT_DOC, { ...METHODOLOGY_DOC, constants_hash: "0".repeat(64) });
  assert.ok(!html.includes("spec-tag-current"));
});

test("methodology: inlineCodeHtml sets backtick spans as code and escapes everything else first (guard: markup stays inert)", () => {
  assert.equal(inlineCodeHtml("see `value_domain.score_table` here"), "see <code>value_domain.score_table</code> here");
  assert.equal(inlineCodeHtml("a <script>x()</script> `b`"), "a &lt;script&gt;x()&lt;/script&gt; <code>b</code>");
  assert.equal(inlineCodeHtml("`<img src=x onerror=y>`"), "<code>&lt;img src=x onerror=y&gt;</code>");
  assert.equal(inlineCodeHtml("an unmatched ` stays literal"), "an unmatched ` stays literal");
});

test("methodology: detector flags link to a definition only when that detector is defined on the page", () => {
  const [defined] = METHODOLOGY_DOC.detectors;
  // Smaller count listed FIRST, so only a real sort puts the larger first
  // (with the larger listed first, an unsorted render also passed -- measured).
  const html = methodologyShell({ ...SNAPSHOT_DOC, detector_summary: { not_a_detector: 2, [defined.name]: 3 } });
  const anchor = `methodology-detector-${defined.name}`;
  assert.ok(html.includes(`data-doc-jump="${anchor}"`), "a defined detector's flag links to its definition");
  assert.ok(html.includes(`<li class="spec-entry" id="${anchor}"`), "and that definition carries the anchor");
  assert.ok(!html.includes('data-doc-jump="methodology-detector-not_a_detector"'), "an undefined one is not a dead link");
  assert.ok(html.indexOf(defined.name) < html.indexOf("not_a_detector"), "largest count first");
  // The real fixture's flags name no detector the fixture defines: nothing links.
  assert.ok(!methodologyShell(SNAPSHOT_DOC).includes('data-doc-jump="methodology-detector-'));
});

test("methodology: an empty detector summary says so instead of rendering an empty list", () => {
  const html = methodologyShell({ ...SNAPSHOT_DOC, detector_summary: {} });
  assert.ok(html.includes(copy.METHODOLOGY_DETECTOR_SUMMARY_NONE));
  assert.ok(!html.includes('class="detector-flags"'));
});

test("configure: sortReprojectRows places tier S+ ahead of S and A", () => {
  const rows = [
    { owner: "a", name: "r-a", tier: "A", rank: 3 },
    { owner: "a", name: "r-splus", tier: "S+", rank: 1 },
    { owner: "a", name: "r-s", tier: "S", rank: 2 },
  ];
  assert.deepEqual(sortReprojectRows(rows, "tier").map((r) => r.tier), ["S+", "S", "A"]);
  assert.deepEqual(sortReprojectRows(rows, "-tier").map((r) => r.tier), ["A", "S", "S+"]);
});

test("configure: topMovers excludes rows with null/undefined balanced_rank or rank, and configureDeltaText formats newly rated rows", () => {
  const rows = [
    { owner: "a", name: "new-repo", balanced_rank: null, rank: 2 },
    { owner: "a", name: "undef-repo", balanced_rank: undefined, rank: 3 },
    { owner: "a", name: "up-repo", balanced_rank: 10, rank: 4 },
    { owner: "a", name: "down-repo", balanced_rank: 1, rank: 6 },
  ];
  const { up, down } = topMovers(rows);
  assert.deepEqual(up.map((d) => d.row.name), ["up-repo"]);
  assert.deepEqual(down.map((d) => d.row.name), ["down-repo"]);
  assert.equal(copy.configureDeltaText(rows[0]), "• a/new-repo: new → 2");
  assert.equal(copy.configureDeltaText(rows[2]), "▲ a/up-repo: 10 → 4");
});

test("api: postReproject appends page and page_size when provided and omits query string by default", async () => {
  const originalFetch = globalThis.fetch;
  const seenUrls = [];
  globalThis.fetch = async (url) => {
    seenUrls.push(url);
    return {
      ok: true,
      status: 200,
      headers: { get: () => "application/json" },
      json: async () => ({ rows: [], total: 0, page: 1, page_size: 1000 }),
    };
  };
  try {
    await postReproject({ weight_responsiveness: 0.2 });
    await postReproject({ weight_responsiveness: 0.2 }, { page_size: 1000 });
    assert.equal(seenUrls[0], "/v1/porchlamp/reproject");
    assert.equal(seenUrls[1], "/v1/porchlamp/reproject?page_size=1000");
  } finally {
    globalThis.fetch = originalFetch;
  }
});


// ---- Site-polish fixes (2026-10-03) ------------------------------------------------------

import { renderTierCell } from "../components/badge.js";

test("a capped tier pill explains itself; an uncapped one carries no explanation", () => {
  const capped = renderTierCell("B", "B");
  assert.match(capped, /B \(capped\)/);
  assert.ok(capped.includes(`title="${escapeHtml(copy.TIER_CAPPED_EXPLANATION)}"`), capped);
  assert.match(capped, /tabindex="0"/, "keyboard users can reach the tooltip");
  const uncapped = renderTierCell("A", "S");
  assert.equal(uncapped, `<span class="tier-pill">A</span>`);
  assert.match(copy.TIER_CAPPED_EXPLANATION, /not how good the score is/);
});

test("hasAuditedStars: the Audited stars column shows only when a row on screen has a value", () => {
  assert.equal(copy.hasAuditedStars([]), false);
  assert.equal(copy.hasAuditedStars(undefined), false);
  assert.equal(copy.hasAuditedStars([{ stars_audited: null }, { stars_audited: undefined }]), false);
  assert.equal(copy.hasAuditedStars([{ stars_audited: null }, { stars_audited: 0 }]), true, "0 is an audit result, not a missing one");
  assert.equal(copy.hasAuditedStars([{ stars_audited: 16282 }]), true);
});

test("the stars-unaudited class hides the column's header and cells together", () => {
  const css = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "styles", "components.css"), "utf8");
  const rule = css.match(/\.stars-unaudited \[data-col="stars"\],\s*\.stars-unaudited \[data-cell="stars"\]\s*\{\s*display:\s*none;/);
  assert.ok(rule, "rule must cover both th[data-col] and td[data-cell]");
  const header = renderColumnHeader("rank", false);
  assert.match(header, /<th data-col="stars"/, "the header cell carries the hook the rule targets");
});

test("index.html carries absolute link-preview tags and the preview image exists", () => {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const html = readFileSync(path.join(here, "..", "index.html"), "utf8");
  const meta = (attr, name) => {
    const m = html.match(new RegExp(`<meta ${attr}="${name}" content="([^"]*)"`));
    return m ? m[1] : null;
  };
  // No URL literal here: tests/test_ui_no_external_resources.py scans this file too.
  const base = meta("property", "og:url");
  assert.match(base, /^https:\/\/[^/]+\/porchlamp\/$/, "og:url is the absolute site root");
  assert.equal(meta("property", "og:image"), base + "og.png");
  assert.equal(meta("name", "twitter:image"), base + "og.png");
  assert.equal(meta("name", "twitter:card"), "summary_large_image");
  for (const k of ["og:title", "og:description", "og:image:alt"]) assert.ok(meta("property", k), k);
  const png = readFileSync(path.join(here, "..", "og.png"));
  assert.equal(png.subarray(1, 4).toString(), "PNG");
  assert.equal(png.readUInt32BE(16), 1200, "og:image:width matches the file");
  assert.equal(png.readUInt32BE(20), 630, "og:image:height matches the file");
});

// ---- PR 1 UX/UI fixes ---------------------------------------------------------------------

import { classifyKindForLens, projectRowForLens } from "../views/drawer.js";
import { flattenGroups } from "../views/notranked.js";
import { filterMatrixPoints } from "../views/matrix.js";
import { renderTable as renderCompareTable } from "../views/compare.js";
import { renderOnboardingBanner } from "../views/leaderboard.js";

test("drawer: classifyKindForLens and projectRowForLens project a persona-rated repo even when balanced is unrated", () => {
  const personaOnlyRepo = {
    ...FLAT_UNRATED_DETAIL,
    owner: "ai-org",
    name: "ai-repo",
    persona_composites: {
      balanced: { porchlamp: null, tier: null, coverage: 0.48, tier_ceiling: null, rated: false },
      ai_assisted: { porchlamp: 78.4, tier: "A", coverage: 0.68, tier_ceiling: "A", rated: true },
    },
    persona_gates: {},
  };
  assert.equal(classifyKindForLens(personaOnlyRepo, "balanced"), "unrated");
  assert.equal(classifyKindForLens(personaOnlyRepo, "ai_assisted"), "rated");

  const projected = projectRowForLens(personaOnlyRepo, "ai_assisted");
  assert.equal(projected.porchlamp, 78.4);
  assert.equal(projected.tier, "A");
  assert.equal(projected.coverage, 0.68);
  assert.equal(projected.tier_ceiling, "A");

  const state = {
    ...stateWithRepoDetail(personaOnlyRepo),
    persona: "ai_assisted",
  };
  const html = renderDrawer(state);
  assert.ok(html.includes(copy.DRAWER_SECTION_COMPOSITE), "persona-rated repo renders Composite section");
  assert.ok(html.includes("78.40"), "projected persona score renders in drawer");
});

test("drawer: classifyKindForLens identifies persona-gated repositories under active persona lens", () => {
  const personaGatedRepo = {
    ...FLAT_RATED_DETAIL,
    persona_gates: { senior_systems: "min_elephant_factor" },
  };
  assert.equal(classifyKindForLens(personaGatedRepo, "balanced"), "rated");
  assert.equal(classifyKindForLens(personaGatedRepo, "senior_systems"), "persona-gated");
});

test("drawer: a routed repo shows the off-platform reason under a persona lens", () => {
  // The API sends a repository D-GATE-6 routed off-platform with no persona
  // composites and no persona gates, so the drawer falls back to the balanced
  // classification: unrated, with the off-platform sentence, on every lens.
  const routedRepo = {
    ...FLAT_UNRATED_DETAIL,
    owner: "kernel-org",
    name: "mailing-list-repo",
    // The project record's own spelling (the published golang/go record), not the list's bare one.
    unrated_reason: "unrated: off_platform_routed",
    persona_composites: {},
    persona_gates: {},
  };
  assert.equal(classifyKindForLens(routedRepo, "first_timer"), "unrated");

  for (const persona of ["balanced", "first_timer"]) {
    const html = renderDrawer({ ...stateWithRepoDetail(routedRepo), persona });
    assert.ok(
      html.includes(escapeHtml("Contribution activity happens off-platform and cannot be measured here.")),
      `the drawer must show the off-platform sentence under the ${persona} lens`,
    );
    assert.ok(!html.includes("unrated: off_platform_routed"), `the drawer shows the raw reason code under the ${persona} lens`);
  }
});

test("drawer: a project with no external workflow shows the sentence, not the reason code", () => {
  // The published seemoo-lab/opendrop record: not eligible, and both reason fields carry the engine's spelling.
  const noWorkflow = {
    ...FLAT_GATED_DETAIL,
    owner: "seemoo-lab",
    name: "opendrop",
    gate_reason: "unrated: no_external_workflow",
    unrated_reason: "unrated: no_external_workflow",
  };
  const html = renderDrawer(stateWithRepoDetail(noWorkflow));
  assert.ok(html.includes(escapeHtml("No external contribution workflow was found to measure.")));
  assert.ok(!html.includes("unrated: no_external_workflow"), "the drawer shows the raw reason code");
});

test("drawer: renders outbound GitHub link with noopener noreferrer", () => {
  const html = renderDrawer(stateWithRepoDetail(FLAT_RATED_DETAIL));
  assert.ok(html.includes(copy.DRAWER_GITHUB_LABEL), "drawer includes GitHub outbound link label");
  assert.ok(html.includes("drawer-github-link"), "drawer includes .drawer-github-link");
  assert.ok(html.includes('rel="noopener noreferrer"'), "drawer link has rel=noopener noreferrer");
});

// ------------------------------------------------ Maintainer README badge
// MEASURED on snapshot 2026-10-13c (3,407 rated): "Sweet Spot or tier A+" offered
// 1,213 repositories a badge, 182 of them tier C, and the tier images named no
// coverage (U-1) although 44 of the 71 tier-A repositories are capped. A badge is
// for a balanced tier of A or above (65 repositories); the Sweet Spot marks it.
import { readdirSync } from "node:fs";
import { renderMaintainerBadge } from "../views/drawer.js";

const BADGES_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "badges");
// The public site's address is read from index.html's og:url, never written here (this file is scanned for URLs).
const SITE_BASE = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "index.html"), "utf8")
  .match(/<meta property="og:url" content="([^"]*)"/)[1];
const COVERAGE_FULL = "coverage 95\u2013100%";
const COVERAGE_CEILING_A = "coverage 80\u201395%";

/** A balanced record; FLAT_RATED_DETAIL is tier S, full coverage, in the Sweet Spot. */
function badgeRow(overrides) {
  return { ...FLAT_RATED_DETAIL, ...overrides };
}

// Every tier state a badge image exists for, plain and in the Sweet Spot.
const BADGE_CASES = [
  [{ tier: "S+", tier_ceiling: null, coverage: 1.0, quadrant: "Nursery" }, "tier-s-plus"],
  [{ tier: "S", tier_ceiling: null, coverage: 0.97, quadrant: "Crucible" }, "tier-s"],
  [{ tier: "A", tier_ceiling: null, coverage: 0.96, quadrant: "Nursery" }, "tier-a"],
  [{ tier: "A", tier_ceiling: "A", coverage: 0.85, quadrant: "Nursery" }, "tier-a-capped"],
  [{ tier: "S+", tier_ceiling: null, coverage: 1.0, quadrant: "Sweet Spot" }, "sweet-spot-tier-s-plus"],
  [{ tier: "S", tier_ceiling: null, coverage: 0.97, quadrant: "Sweet Spot" }, "sweet-spot-tier-s"],
  [{ tier: "A", tier_ceiling: null, coverage: 0.96, quadrant: "Sweet Spot" }, "sweet-spot-tier-a"],
  [{ tier: "A", tier_ceiling: "A", coverage: 0.85, quadrant: "Sweet Spot" }, "sweet-spot-tier-a-capped"],
];

test("maintainer badge: offered for a balanced tier of A or above with no suspicion flag; the Sweet Spot marks a badge but never earns one", () => {
  for (const [overrides, id] of BADGE_CASES) {
    const spec = copy.maintainerBadgeSpec(badgeRow(overrides));
    assert.ok(spec, `${id}: expected a badge`);
    assert.equal(spec.id, id);
    assert.equal(spec.file, `badges/${id}.svg`);
  }
  // The measured hole: the Sweet Spot below tier A earns nothing.
  for (const tier of ["B", "C", "D"]) {
    const row = badgeRow({ tier, tier_ceiling: null, coverage: 0.97, quadrant: "Sweet Spot" });
    assert.equal(copy.maintainerBadgeSpec(row), null, `Sweet Spot at tier ${tier}`);
  }
  assert.equal(copy.maintainerBadgeSpec(badgeRow({ suspicion: ["closed_unread"] })), null, "a flagged row is under review, not promoted");
  assert.equal(copy.maintainerBadgeSpec(FLAT_GATED_DETAIL), null);
  assert.equal(copy.maintainerBadgeSpec(FLAT_UNRATED_DETAIL), null);
  // Guard: a tier whose coverage band has no truthful image gets no badge rather than a wrong one.
  assert.equal(copy.maintainerBadgeSpec(badgeRow({ tier: "A", tier_ceiling: null, coverage: 0.7 })), null);
});

test("maintainer badge: each image states its tier, '(capped)' when the ceiling binds, and its coverage band (U-1); no image is orphaned", () => {
  const mark = `${copy.QUADRANT_GLYPHS["Sweet Spot"]} `;
  const ids = [];
  for (const [overrides, id] of BADGE_CASES) {
    const row = badgeRow(overrides);
    const spec = copy.maintainerBadgeSpec(row);
    ids.push(spec.id);
    const capped = row.tier_ceiling === row.tier;
    assert.ok(spec.imageText.includes(`Tier ${row.tier}${capped ? " (capped)" : ""} \u00b7 `), `${id}: tier and ceiling in "${spec.imageText}"`);
    assert.ok(spec.imageText.endsWith(capped ? COVERAGE_CEILING_A : COVERAGE_FULL), `${id}: coverage band in "${spec.imageText}"`);
    assert.equal(spec.imageText.startsWith(mark), row.quadrant === "Sweet Spot", `${id}: the Sweet Spot mark`);

    const svg = readFileSync(path.join(BADGES_DIR, `${id}.svg`), "utf8");
    assert.match(svg, /^<svg[^>]*role="img"/, `${id}: an image to assistive technology`);
    assert.ok(svg.includes(`aria-label="Porchlamp: ${spec.imageText}"`), `${id}: aria-label matches copy.js`);
    assert.ok(svg.includes(`<title>Porchlamp: ${spec.imageText}</title>`), `${id}: title matches copy.js`);
    // The diamond is drawn as a shape, so the visible words are imageText without it.
    const words = spec.imageText.startsWith(mark) ? spec.imageText.slice(mark.length) : spec.imageText;
    const visible = [...svg.matchAll(/<text[^>]*>([^<]*)<\/text>/g)].map((m) => m[1]);
    assert.deepEqual(visible, ["Porchlamp", words], `${id}: the visible words`);
    assert.ok(svg.includes("#fbbf24"), `${id}: the lamp is lit`);
  }
  const files = readdirSync(BADGES_DIR).filter((f) => f.endsWith(".svg")).sort();
  assert.deepEqual(files, ids.map((id) => `${id}.svg`).sort(), "every badge file is reachable, and only those exist");
});

test("maintainer badge: the Markdown links the image to the repository's drawer on the public site, with score, coverage and snapshot in alt and title (U-1, U-6)", () => {
  const row = badgeRow({ porchlamp: 84.5, coverage: 0.85, tier: "A", tier_ceiling: "A", snapshot_id: "2026-10-13c" });
  const spec = copy.maintainerBadgeSpec(row);
  assert.equal(
    spec.alt,
    "Porchlamp: \u2666 Sweet Spot \u00b7 Tier A (capped) \u00b7 Score 84.50 \u00b7 Coverage 85% \u00b7 Ceiling A \u00b7 Snapshot 2026-10-13c",
  );
  const md = copy.maintainerBadgeMarkdown(row, SITE_BASE);
  assert.equal(md, `[![${spec.alt}](${SITE_BASE}badges/sweet-spot-tier-a-capped.svg)](${SITE_BASE}?repo=riverline%2Fhttpkit "${spec.alt}")`);
  assert.equal(copy.maintainerBadgeMarkdown(row, SITE_BASE.replace(/\/$/, "")), md, "a base without its trailing slash gives the same links");
  // Nothing that could end the alt text or the title early reaches the snippet.
  const hostile = copy.maintainerBadgeSpec({ ...row, snapshot_id: 'x"] [y\\' });
  assert.ok(!/["[\]\\]/.test(hostile.alt), hostile.alt);
  assert.equal(copy.maintainerBadgeMarkdown(FLAT_GATED_DETAIL, SITE_BASE), "");
});

test("maintainer badge: the drawer shows the balanced badge at the end of Composite, never one a persona lens would mint (U-4, D-DEF-2)", () => {
  const html = renderDrawer(stateWithRepoDetail(FLAT_RATED_DETAIL), { siteBaseUrl: SITE_BASE });
  const box = html.indexOf('data-maintainer-badge="sweet-spot-tier-s"');
  const composite = html.indexOf(`<summary>${escapeHtml(copy.DRAWER_SECTION_COMPOSITE)}</summary>`);
  const pillars = html.indexOf(`<summary>${escapeHtml(copy.DRAWER_SECTION_PILLARS)}</summary>`);
  assert.ok(composite > -1 && composite < box && box < pillars, "the badge box is inside the Composite section");
  assert.equal((html.match(/<details[^>]*>/g) || []).length, 5, "a rated drawer still has five sections");
  assert.ok(html.includes('data-badge-copy="true"') && html.includes('data-badge-markdown="true"'));
  assert.ok(html.includes(escapeHtml(`${SITE_BASE}?repo=riverline%2Fhttpkit`)), "the Markdown uses the address app.js passes in");
  assert.ok(html.includes('src="badges/sweet-spot-tier-s.svg"'), "the preview loads from this site");

  // Balanced tier B, tier A under a lens: Composite shows the lens's A, but no badge is minted from it.
  const lensOnly = {
    ...FLAT_RATED_DETAIL, owner: "lens-org", name: "lens-repo", porchlamp: 66.1, tier: "B", tier_ceiling: "A", coverage: 0.9,
    persona_composites: { ai_assisted: { porchlamp: 81.2, tier: "A", coverage: 0.9, tier_ceiling: "A", rated: true } },
  };
  const lensHtml = renderDrawer({ ...stateWithRepoDetail(lensOnly), persona: "ai_assisted" }, { siteBaseUrl: SITE_BASE });
  assert.ok(lensHtml.includes("81.20"), "the lens's own score is shown");
  assert.ok(!lensHtml.includes("data-maintainer-badge"), "but no badge: the balanced tier is B");

  // Balanced tier S seen through a lens that scores it lower: the badge is still the balanced one.
  const demoted = {
    ...FLAT_RATED_DETAIL,
    persona_composites: { first_timer: { porchlamp: 61.0, tier: "B", coverage: 1.0, tier_ceiling: null, rated: true } },
  };
  const demotedHtml = renderDrawer({ ...stateWithRepoDetail(demoted), persona: "first_timer" }, { siteBaseUrl: SITE_BASE });
  assert.ok(demotedHtml.includes('data-maintainer-badge="sweet-spot-tier-s"'));

  // Unrated under balanced, rated under a lens: no badge either.
  const personaOnly = {
    ...FLAT_UNRATED_DETAIL, owner: "ai-org", name: "ai-repo",
    persona_composites: { ai_assisted: { porchlamp: 78.4, tier: "A", coverage: 0.68, tier_ceiling: "A", rated: true } },
  };
  assert.ok(!renderDrawer({ ...stateWithRepoDetail(personaOnly), persona: "ai_assisted" }).includes("data-maintainer-badge"));
  assert.equal(renderMaintainerBadge(FLAT_GATED_DETAIL, SITE_BASE), "");
});

test("table: renderColumnHeader renders interactive sort buttons for all 5 pillars", () => {
  const html = renderColumnHeader("-p_rigor", false);
  for (const p of ["responsiveness", "criticality", "governance", "rigor", "accessibility"]) {
    assert.ok(html.includes(`data-sort-field="p_${p}"`), `missing pillar sort button for p_${p}`);
    assert.ok(html.includes(escapeHtml(copy.pillarSortTooltip(p))), `missing tooltip for ${p}`);
  }
  assert.match(html, /class="pillar-sort-btn is-active"[^>]*data-sort-field="p_rigor"[^>]*aria-sort="ascending"/);
});

test("notranked: flattenGroups filters by ecosystem and search query", () => {
  const fixture = {
    gated: [
      { owner: "py-org", name: "alpha-lib", ecosystem: "pypi", gate_reason: "stale" },
      { owner: "rs-org", name: "beta-crate", ecosystem: "cargo", gate_reason: "stale" },
    ],
    unrated: [
      { owner: "py-org", name: "gamma-tool", ecosystem: "pypi", unrated_reason: "insufficient_evidence", coverage: 0.42 },
    ],
    persona_hidden: [],
  };
  assert.equal(flattenGroups(fixture).length, 3);
  assert.equal(flattenGroups(fixture, { ecosystem: "pypi" }).length, 2);
  assert.equal(flattenGroups(fixture, { ecosystem: "pypi", query: "gamma" }).length, 1);
  assert.equal(flattenGroups(fixture, { ecosystem: "cargo", query: "BETA" })[0].row.name, "beta-crate");
});

test("matrix: filterMatrixPoints filters by ecosystem and search query", () => {
  const points = [
    { owner: "tokio-rs", name: "tokio", ecosystem: "cargo", leverage: 80, friction: 20, quadrant: "sweet_spot" },
    { owner: "pallets", name: "flask", ecosystem: "pypi", leverage: 75, friction: 25, quadrant: "sweet_spot" },
  ];
  assert.equal(filterMatrixPoints(points, { ecosystem: "", query: "" }).length, 2);
  assert.equal(filterMatrixPoints(points, { ecosystem: "cargo", query: "" }).length, 1);
  assert.equal(filterMatrixPoints(points, { ecosystem: "", query: "FLASK" })[0].name, "flask");
});

test("compare: renderCompareTable places verdict above table, groups parameters by pillar, and renders clickable repo buttons", () => {
  const state = {
    compareRepos: ["org-a/repo-1", "org-b/repo-2"],
    compareDataByRepo: {
      "org-a/repo-1": {
        detail: {
          ...FLAT_RATED_DETAIL,
          owner: "org-a",
          name: "repo-1",
          porchlamp_score: 82.0,
          pillar_responsiveness: 85.0,
          pillar_criticality: 80.0,
          pillar_governance: 78.0,
          pillar_rigor: 84.0,
          pillar_accessibility: 79.0,
        },
        evidence: [],
      },
      "org-b/repo-2": {
        detail: {
          ...FLAT_RATED_DETAIL,
          owner: "org-b",
          name: "repo-2",
          porchlamp_score: 71.0,
          pillar_responsiveness: 70.0,
          pillar_criticality: 72.0,
          pillar_governance: 74.0,
          pillar_rigor: 68.0,
          pillar_accessibility: 71.0,
        },
        evidence: [],
      },
    },
    methodology: METHODOLOGY_DOC,
  };
  const html = renderCompareTable(state);
  const verdictIdx = html.indexOf('class="compare-verdict"');
  const tableIdx = html.indexOf('class="compare-table"');
  assert.ok(verdictIdx > -1 && tableIdx > -1 && verdictIdx < tableIdx, "verdict appears above compare table");
  assert.ok(html.includes('class="compare-section-row"'), "parameter rows are grouped by pillar section headers");
  assert.ok(html.includes('class="link-button repo-name" data-owner="org-a" data-name="repo-1"'), "repo headers are clickable drawer triggers");
});

test("leaderboard: renderOnboardingBanner includes subtitle and methodology link", () => {
  const html = renderOnboardingBanner();
  assert.ok(html.includes(escapeHtml(copy.ONBOARDING_SUBTITLE)));
  assert.ok(html.includes(escapeHtml(copy.ONBOARDING_METHODOLOGY_LINK)));
  assert.ok(html.includes("data-onboarding-methodology"));
});


// ---------------------------------------------------------------- pagers
// MEASURED 2026-10-12: with 1000 rows a page the only pager sat above the table, so a reader at the bottom had to
// scroll the whole page back up to go on.
const PAGER_COUNTS = { total: 100, rated: 40, gated: 40, unrated: 20 };

function occurrences(html, needle) {
  return html.split(needle).length - 1;
}

test("pager: Ranked puts a pager above and one below the list, and only the top one has the page-size select", () => {
  const state = {
    ...defaultState(),
    tab: "ranked",
    page: 2,
    pageSize: 20,
    leaderboard: { rows: [{ owner: "a", name: "b" }], total: 100, counts: PAGER_COUNTS },
  };
  const html = leaderboardRenderShell(state, { isMobile: false });
  assert.equal(occurrences(html, 'class="pager'), 2, "two pagers");
  assert.equal(occurrences(html, "data-pager-next"), 2, "both have a next button");
  assert.equal(occurrences(html, "data-pager-size-select"), 1, "one page-size select");
  assert.ok(html.indexOf("data-pager-next") < html.indexOf("<table"), "the first pager is above the table");
  assert.ok(html.lastIndexOf("data-pager-next") > html.indexOf("</table>"), "the second pager is below the table");
  const cards = leaderboardRenderShell(state, { isMobile: true });
  assert.equal(occurrences(cards, "data-pager-next"), 2, "the card list has both pagers too");
  assert.ok(cards.lastIndexOf("data-pager-next") > cards.indexOf("data-leaderboard-cards"), "the second is below the cards");
});

test("pager: Not ranked puts a pager above and one below the list", () => {
  const row = { owner: "a", name: "b", ecosystem: "go", gate_reason: "archived" };
  const state = {
    ...defaultState(),
    tab: "notranked",
    notRankedPage: 2,
    notRankedPageSize: 20,
    notRanked: { gated: [row], unrated: [], persona_hidden: [], counts: PAGER_COUNTS, truncated: false },
  };
  const html = notRankedRenderShell(state);
  assert.equal(occurrences(html, "data-notranked-pager-next"), 2);
  assert.equal(occurrences(html, "data-notranked-pager-size-select"), 1);
  assert.ok(html.lastIndexOf("data-notranked-pager-next") > html.indexOf("data-notranked-rows"), "the second pager follows the rows");
});

test("pager: syncPager updates every pager on the page", () => {
  const make = (extra = {}) => ({ textContent: "", disabled: false, value: "20", ...extra });
  const map = {
    "[data-pager-status]": [make(), make()],
    "[data-pager-size-select]": [make()],
    "[data-pager-first]": [make(), make()],
    "[data-pager-prev]": [make(), make()],
    "[data-pager-next]": [make(), make()],
    "[data-pager-last]": [make(), make()],
  };
  const root = {
    querySelector: (sel) => (map[sel] || [])[0] || null,
    querySelectorAll: (sel) => map[sel] || [],
  };
  syncPager(root, 5, 20, 100, "pager");
  for (const el of map["[data-pager-status]"]) assert.equal(el.textContent, copy.pagerStatusText(5, 5, 100));
  for (const el of map["[data-pager-next]"].concat(map["[data-pager-last]"])) assert.equal(el.disabled, true, "last page: next and last are off");
  for (const el of map["[data-pager-first]"].concat(map["[data-pager-prev]"])) assert.equal(el.disabled, false);
});

test("pager: scrollDeltaToReveal brings the top pager back only when the reader has scrolled past it", () => {
  assert.equal(scrollDeltaToReveal(300, 60), null, "the pager is already in view");
  assert.equal(scrollDeltaToReveal(68, 60), null, "just below the sticky header and its gap");
  assert.equal(scrollDeltaToReveal(-1900, 60), -1968, "scrolled far past it: go up to just under the header");
  assert.equal(scrollDeltaToReveal(10, 0), null, "a header that is not sticky takes no room");
  assert.equal(scrollDeltaToReveal(-5, 0), -13);
});

// ------------------------------------------------------------ tab strip
test("primary nav: centeredScrollLeft puts the active tab mid-view and stays inside the strip", () => {
  // 375 px of strip showing 476 px of tabs; Methodology is the last tab, at 360..476.
  assert.equal(centeredScrollLeft(360, 116, 375, 476), 101, "the last tab: clamped to the strip's end");
  assert.equal(centeredScrollLeft(0, 90, 375, 476), 0, "the first tab: clamped to the start");
  assert.equal(centeredScrollLeft(200, 100, 375, 600), 62.5, "a middle tab: centred");
  assert.equal(centeredScrollLeft(0, 90, 375, 300), 0, "a strip that fits never scrolls");
});

// ---------------------------------------------------------------- matrix
// MEASURED 2026-10-12: the Matrix showed "Rendering on canvas above 2,000 markers, with an SVG overlay ..." to
// visitors, said marker size was audited stars when no project has any, counted "gated" for every unranked
// repository, left the canvas without a name for assistive technology, and never said what the quadrants mean.
function matrixState(count, starsAudited = null) {
  const points = Array.from({ length: count }, (_, i) => ({
    owner: "o",
    name: `r${i}`,
    ecosystem: "go",
    friction: 20 + (i % 60),
    leverage: 30 + (i % 50),
    quadrant: ["Sweet Spot", "Crucible", "Nursery", "Graveyard"][i % 4],
    porchlamp: 70,
    coverage: 0.8,
    tier: "B",
    tier_ceiling: "B",
    stars_audited: starsAudited,
  }));
  return {
    ...defaultState(),
    tab: "matrix",
    matrix: { points, excluded_count: 7679, axis_coverage: {} },
    matrixQuadrantsHidden: {},
  };
}

test("matrix: no wording about how it is drawn, and a plain how-to-read note", () => {
  const html = renderMatrixBody(matrixState(2500));
  assert.ok(!html.includes("Rendering on canvas"), "the drawing technique is not the reader's business");
  assert.ok(html.includes(escapeHtml(copy.MATRIX_HOW_TO_READ)));
});

test("matrix: the star note is true for the data: no audited stars means every dot is the same size", () => {
  const without = renderMatrixBody(matrixState(50, null));
  assert.ok(without.includes(escapeHtml(copy.MATRIX_NO_STARS_NOTE)));
  assert.ok(!without.includes("Marker size is audited stars"));
  const withStars = renderMatrixBody(matrixState(50, 1200));
  assert.ok(withStars.includes(escapeHtml(copy.MATRIX_UNAUDITED_STARS_NOTE)));
  assert.ok(!withStars.includes(escapeHtml(copy.MATRIX_NO_STARS_NOTE)));
});

test("matrix: the excluded count says not ranked, which is what it counts", () => {
  const html = renderMatrixBody(matrixState(50));
  assert.ok(html.includes("7679 repositories are not ranked and not plotted."));
  assert.ok(!html.includes("gated repositories excluded"));
});

test("matrix: the canvas has a name for assistive technology, and the stage says how to use it", () => {
  const html = renderMatrixBody(matrixState(SVG_MARKER_BUDGET + 1));
  assert.match(html, /<canvas[^>]*role="img"[^>]*aria-label="Friction\/Leverage matrix/);
  assert.match(html, /data-matrix-stage="true"[^>]*aria-label="[^"]*arrow keys/);
});

test("matrix: each quadrant chip says what the quadrant is", () => {
  const html = renderMatrixBody(matrixState(50));
  for (const [name, text] of Object.entries(copy.QUADRANT_DESCRIPTIONS)) {
    assert.ok(html.includes(`${text}`), `${name} is described`);
  }
  assert.equal(Object.keys(copy.QUADRANT_DESCRIPTIONS).length, 4);
});

// ---------------------------------------------------------- empty states
// MEASURED 2026-10-12: a search or filter with no ranked match said "No rated repositories match. N gated and M
// unrated are listed under Not ranked." with no way out, and Not ranked said "No gated or unrated repositories to
// show." for a search that simply found nothing.
const EMPTY_COUNTS = { total: 11000, rated: 3321, gated: 6279, unrated: 1400 };

test("empty states: a filtered empty Ranked list names the search and offers both ways out", () => {
  const state = {
    ...defaultState(),
    tab: "ranked",
    query: "golang/go",
    ecosystem: "go",
    leaderboard: { rows: [], total: 0, counts: EMPTY_COUNTS },
  };
  const html = leaderboardRenderShell(state, { isMobile: false });
  assert.ok(html.includes("No ranked repositories match"), "says what was not found");
  assert.ok(html.includes("golang/go"), "names the search");
  assert.ok(html.includes("data-clear-filters"), "a way to clear the filters");
  assert.ok(html.includes("data-search-notranked"), "a way to look in Not ranked");
});

test("empty states: with no filter the Ranked empty text stays as it was and offers no buttons", () => {
  const state = { ...defaultState(), tab: "ranked", leaderboard: { rows: [], total: 0, counts: EMPTY_COUNTS } };
  const html = leaderboardRenderShell(state, { isMobile: false });
  assert.ok(html.includes(escapeHtml(copy.emptyLeaderboardText(6279, 1400))));
  assert.ok(!html.includes("data-clear-filters") && !html.includes("data-search-notranked"));
});

test("empty states: an ecosystem filter alone offers to clear it but not to search Not ranked", () => {
  const state = { ...defaultState(), tab: "ranked", ecosystem: "systems", leaderboard: { rows: [], total: 0, counts: EMPTY_COUNTS } };
  const html = leaderboardRenderShell(state, { isMobile: false });
  assert.ok(html.includes("data-clear-filters"));
  assert.ok(!html.includes("data-search-notranked"));
});

test("not ranked: a cut list says so, an uncut one does not", () => {
  const row = { owner: "a", name: "b", ecosystem: "go", gate_reason: "archived" };
  const base = { ...defaultState(), tab: "notranked", notRanked: { gated: [row], unrated: [], persona_hidden: [], counts: EMPTY_COUNTS, truncated: true } };
  assert.ok(notRankedRenderShell(base).includes(escapeHtml(copy.NOT_RANKED_TRUNCATED)));
  const whole = { ...base, notRanked: { ...base.notRanked, truncated: false } };
  assert.ok(!notRankedRenderShell(whole).includes(escapeHtml(copy.NOT_RANKED_TRUNCATED)));
});

test("not ranked: a search with no match says so and offers to clear it or look in Ranked", () => {
  const state = {
    ...defaultState(),
    tab: "notranked",
    query: "zzz-nothing",
    notRanked: { gated: [{ owner: "a", name: "b", ecosystem: "go", gate_reason: "archived" }], unrated: [], persona_hidden: [], counts: EMPTY_COUNTS, truncated: false },
  };
  const html = notRankedRenderShell(state);
  assert.ok(html.includes("zzz-nothing"));
  assert.ok(html.includes("data-clear-filters"));
  assert.ok(html.includes("data-search-ranked"));
});

// ------------------------------------------------------------- configure
// MEASURED 2026-10-12: Configure weights asked for page 1 of 1,000 rows only, so a custom ranking of 3,321 repositories
// showed "Page 1 of 50 (1000 rows)" and its rank deltas covered the top 1,000 only.
test("configure: remainingReprojectPages lists the pages after the first, and none for a short ranking", () => {
  assert.deepEqual(remainingReprojectPages(3321, 1000), [2, 3, 4]);
  assert.deepEqual(remainingReprojectPages(1000, 1000), []);
  assert.deepEqual(remainingReprojectPages(1001, 1000), [2]);
  assert.deepEqual(remainingReprojectPages(0, 1000), []);
});

test("configure: mergeReprojectPages puts every page's rows in order and keeps the first page's other fields", () => {
  const first = { rows: [{ k: 1 }, { k: 2 }], total: 5, lens: "custom", page: 1 };
  const merged = mergeReprojectPages(first, [{ rows: [{ k: 3 }, { k: 4 }] }, { rows: [{ k: 5 }] }]);
  assert.deepEqual(merged.rows.map((r) => r.k), [1, 2, 3, 4, 5]);
  assert.equal(merged.total, 5);
  assert.equal(merged.lens, "custom");
  assert.deepEqual(first.rows.map((r) => r.k), [1, 2], "the first answer is not changed");
});
