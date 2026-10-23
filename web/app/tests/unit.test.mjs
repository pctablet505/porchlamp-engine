// tests/unit.test.mjs — Node unit tests for the pure modules (06 §6 and §8
// acceptance checklist items 3-5, 8; state/query modules, U-14/U-16/U-17/U-18).
//
// Written to run cleanly under `node --test` (Node >= 18, stdlib only).
//
// Run: node --test web/app/tests/unit.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import vm from "node:vm";

import { decidingTieLevel, SORTABLE_COLUMNS, renderColumnHeader, sortColumnLabel } from "../components/table.js";
import { KEY_ACTIONS, actionForKeyEvent } from "../components/keymap.js";
import { renderCoverageBadge } from "../components/badge.js";
import { renderBrand } from "../components/brand.js";
import { escapeHtml } from "../components/table.js";
import {
  createStore,
  defaultState,
  reduce,
  parseLocation,
  serializeLocation,
  stateFromLocation,
  historyEntryFor,
  redirectTarget,
  hrefFor,
  titleForState,
  VALID_PERSONAS,
  VALID_TABS,
  countRankChanges,
  resolveOpenTargetKey,
  footerFieldsFor,
  repoToLoadFromState,
} from "../state.js";
import * as copy from "../copy.js";
import { renderShell as leaderboardRenderShell, syncPager, scrollDeltaToReveal, renderMobileSortBar, personaVisibleForState } from "../views/leaderboard.js";
import { renderShell as notRankedRenderShell, flattenGroups as notRankedFlatten, renderPageRows as notRankedPageRows } from "../views/notranked.js";
import { renderReport, renderShell as reportRenderShell, shellShape as reportShellShape, axisFor } from "../views/report.js";
import { fill as fillTemplate } from "../components/fill.js";
import { centeredScrollLeft, renderNav, navEntryFor, renderFooter, renderHeader, renderAppHeader, headerControlsFor } from "../components/chrome.js";
import { ROUTES, matchRoute, projectPath, routeById, routePath, routeTitle } from "../routes.js";
import { fetchManifest, postReproject } from "../api.js";
import { normalizeWeights, applyPresetRespectingLocks, renderControls, renderShell as renderConfigureShell, sortReprojectRows, filterReprojectRows, renderPager as renderConfigurePager, topMovers, remainingReprojectPages, mergeReprojectPages } from "../views/configure.js";
import { PILLAR_IDS, PERSONA_PRESET_WEIGHTS, BALANCED_WEIGHTS } from "../state.js";
import { nearestMarkerInDirection, hitTestPoint, renderMatrixBody, renderShell as renderMatrixShell, shellShape as matrixShellShape, clientToDomainCoords } from "../views/matrix.js";
import { pointKey, SVG_MARKER_BUDGET, toSvgX, toSvgY, renderScatterSvg } from "../components/scatter.js";
import { synthesizeRows } from "./synthetic-rows.js";
import { shellKey as compareShellKey, shellShape as compareShellShape, renderShell as renderCompareShell, computeVerdict } from "../views/compare.js";
import { renderDrawer, classifyKind, classifyKindForLens, findRow } from "../views/drawer.js";
import { renderProject, renderPillarRadar, formatParameterValue, replyTimeText } from "../views/project.js";
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
test("parseLocation / serializeLocation round-trip (incl. page/page_size), and name nothing at the defaults", () => {
  const s1 = {
    ...defaultState(), persona: "first_timer", ecosystem: "pypi", query: "flask", sort: "-porchlamp",
    page: 3, pageSize: 100,
  };
  const address = serializeLocation(s1);
  // the lens and the ecosystem are the list's path (routes.js python-first-timers); the rest its query, in its order
  assert.equal(address, "/python/first-timers/?sort=-porchlamp&page=3&page_size=100&q=flask");
  const parsed = parseLocation("/python/first-timers/", address.slice(address.indexOf("?")));
  assert.equal(parsed.tab, "ranked");
  assert.equal(parsed.persona, "first_timer");
  assert.equal(parsed.ecosystem, "pypi");
  assert.equal(parsed.query, "flask");
  assert.equal(parsed.sort, "-porchlamp");
  assert.equal(parsed.page, 3);
  assert.equal(parsed.pageSize, 100);

  // Every field at its default writes the bare root, and parsing it back
  // names only the view (the caller resets to defaults itself before
  // applying this — see app.js's applyLocationState).
  assert.equal(serializeLocation(defaultState()), "/");
  assert.deepEqual(parseLocation("/", ""), { tab: "ranked" });
});

test("parseLocation rejects a page_size outside the allowed set", () => {
  const parsed = parseLocation("/", "?page_size=37");
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

// ------------------------------------------------------- theme (theme-init.js)
// Every page runs theme-init.js in <head>, before it draws anything. The choice is kept in localStorage under
// "porchlamp-theme"; earlier builds kept it under "porchlamp-v2-theme", which a returning visitor's first page moves.
function memoryStorage(entries = {}) {
  const items = new Map(Object.entries(entries));
  return {
    items: () => Object.fromEntries(items),
    getItem: (key) => (items.has(key) ? items.get(key) : null),
    setItem: (key, value) => {
      items.set(key, String(value));
    },
    removeItem: (key) => {
      items.delete(key);
    },
  };
}

const THEME_INIT = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "theme-init.js"), "utf8");

/** theme-init.js as a page runs it in <head>: a document with only its root element, this storage and a system that
 *  asks for light or not. Returns the root, the storage, a click on the page and the events the script fired. */
function runThemeInit({ storage = memoryStorage(), systemLight = false } = {}) {
  const root = { dataset: {} };
  const listeners = {};
  const fired = [];
  const document = {
    documentElement: root,
    addEventListener: (type, fn) => {
      listeners[type] = fn;
    },
    dispatchEvent: (evt) => {
      fired.push(evt);
      return true;
    },
  };
  vm.runInNewContext(THEME_INIT, {
    document,
    localStorage: storage,
    matchMedia: (query) => ({ matches: query === "(prefers-color-scheme: light)" && systemLight }),
    CustomEvent: class {
      constructor(type, init) {
        this.type = type;
        this.detail = init.detail;
      }
    },
  });
  const toggle = { closest: (selector) => (selector === "[data-theme-toggle]" ? toggle : null) };
  const elsewhere = { closest: () => null };
  return { root, storage, fired, clickToggle: () => listeners.click({ target: toggle }), clickElsewhere: () => listeners.click({ target: elsewhere }) };
}

test("theme: a stored choice is on <html> before the page draws anything; with none the page follows the system", () => {
  for (const theme of ["light", "dark"]) {
    assert.equal(runThemeInit({ storage: memoryStorage({ "porchlamp-theme": theme }) }).root.dataset.theme, theme);
  }
  assert.equal(runThemeInit().root.dataset.theme, undefined);
});

test("theme: a returning visitor keeps the choice stored under the former key, moved once to porchlamp-theme", () => {
  for (const theme of ["light", "dark"]) {
    const { root, storage } = runThemeInit({ storage: memoryStorage({ "porchlamp-v2-theme": theme }) });
    assert.equal(root.dataset.theme, theme);
    assert.deepEqual(storage.items(), { "porchlamp-theme": theme });
    assert.equal(runThemeInit({ storage }).root.dataset.theme, theme);
  }
});

test("theme: a choice under porchlamp-theme wins over the former key", () => {
  const storage = memoryStorage({ "porchlamp-theme": "light", "porchlamp-v2-theme": "dark" });
  assert.equal(runThemeInit({ storage }).root.dataset.theme, "light");
});

test("theme: when the move cannot be written, this page still gets the former choice and the next one retries", () => {
  const storage = memoryStorage({ "porchlamp-v2-theme": "dark" });
  storage.setItem = () => {
    throw new Error("QuotaExceededError");
  };
  assert.equal(runThemeInit({ storage }).root.dataset.theme, "dark");
  assert.deepEqual(storage.items(), { "porchlamp-v2-theme": "dark" });
});

test("theme: unavailable storage or an unknown stored value follows the system", () => {
  const unavailable = {
    getItem: () => {
      throw new Error("SecurityError");
    },
    setItem: () => {
      throw new Error("SecurityError");
    },
    removeItem: () => {
      throw new Error("SecurityError");
    },
  };
  const page = runThemeInit({ storage: unavailable });
  assert.equal(page.root.dataset.theme, undefined);
  assert.doesNotThrow(() => page.clickToggle(), "the toggle still works for this page");
  assert.equal(page.root.dataset.theme, "light");
  assert.equal(runThemeInit({ storage: memoryStorage({ "porchlamp-theme": "blue" }) }).root.dataset.theme, undefined);
});

test("theme: the toggle switches the theme in force, stores it and tells the app; a click elsewhere does nothing", () => {
  // tokens.css is dark unless the system asks for light: the first click goes to the other one either way.
  for (const [systemLight, first, second] of [[true, "dark", "light"], [false, "light", "dark"]]) {
    const page = runThemeInit({ systemLight });
    page.clickElsewhere();
    assert.equal(page.root.dataset.theme, undefined);
    assert.equal(page.fired.length, 0);
    page.clickToggle();
    assert.equal(page.root.dataset.theme, first);
    assert.deepEqual(page.storage.items(), { "porchlamp-theme": first });
    page.clickToggle();
    assert.equal(page.root.dataset.theme, second);
    assert.deepEqual(page.storage.items(), { "porchlamp-theme": second });
    assert.deepEqual(page.fired.map((evt) => [evt.type, evt.detail.theme]), [["porchlamp-themechange", first], ["porchlamp-themechange", second]]);
  }
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

test("copy.js: coverage band boundaries match D-MISS-3 exactly (>=0.90 full, [0.80,0.90) A, [0.60,0.80) B)", () => {
  assert.equal(copy.coverageBandForValue(0.9), "full");
  assert.equal(copy.coverageBandForValue(0.8999), "A");
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

// Deterministic LCG (same shape as synthetic-rows.js's) so a "randomised"
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

test("repoToLoadFromState: a panel address over the matrix finds the repo to load", () => {
  const parsed = stateFromLocation("/projects/riverline/httpkit/", "", { under: "/matrix/" });
  const state = { ...defaultState(), ...parsed };
  assert.equal(state.tab, "matrix");
  assert.deepEqual(repoToLoadFromState(state), { owner: "riverline", name: "httpkit" });
});

test("repoToLoadFromState: a panel address loaded directly still resolves (06 §3: 'with or without tab=')", () => {
  const parsed = parseLocation("/projects/riverline/httpkit/", "");
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

test("synthetic-rows generates enough points to exceed SVG_MARKER_BUDGET (the canvas path this suite exercises)", () => {
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
// Repo-detail error rendering: a 404 renders the not-in-snapshot panel
// (components/livepanel.js) without a retry button, while
// 500/timeout/network errors render the retry banner.
// --------------------------------------------------------------------- //

function drawerStateFor(overrides) {
  return { ...defaultState(), repo: "nobody/nothing", repoDetailByKey: {}, repoDetailLoading: false, repoDetailError: null, ...overrides };
}

test("renderDrawer: a 404 (genuinely not found) shows the not-in-snapshot panel with the live-check button and no retry button", () => {
  const html = renderDrawer(drawerStateFor({ repoDetailError: { endpoint: "/v1/porchlamp/repos/nobody/nothing", status: 404, detail: "no such repo: nobody/nothing" } }));
  assert.ok(html.includes(escapeHtml(copy.liveNotInSnapshotText("nobody/nothing", null))), "must say the repository is not in the snapshot");
  assert.ok(html.includes('data-live-check="nobody/nothing"'), "a 404 offers the opt-in live check");
  assert.ok(!html.includes("data-repo-detail-retry"), "a genuine 404 must not offer a retry button (it will 404 again)");
});

test("renderDrawer: no record and no error (a malformed key never fetches) keeps the plain not-found copy and offers no live check", () => {
  const html = renderDrawer(drawerStateFor({}));
  assert.ok(html.includes(copy.DRAWER_NOT_FOUND_TEXT));
  assert.ok(!html.includes("data-live-check"));
});

test("renderDrawer: a non-404 error (500/timeout/network) shows the retry banner, not the not-found copy", () => {
  for (const status of [500, "timeout", 0]) {
    const html = renderDrawer(drawerStateFor({ repoDetailError: { endpoint: "/v1/porchlamp/repos/nobody/nothing", status, detail: "boom" } }));
    assert.ok(!html.includes(copy.DRAWER_NOT_FOUND_TEXT), `status=${status}: must not show the not-found copy`);
    assert.ok(!html.includes("data-live-check"), `status=${status}: must not offer the live check (the snapshot may hold it)`);
    assert.ok(html.includes("data-repo-detail-retry"), `status=${status}: must offer a retry button`);
  }
});

test("renderDrawer: still loading shows the loading copy, neither not-found nor the retry banner", () => {
  const html = renderDrawer(drawerStateFor({ repoDetailLoading: true }));
  assert.ok(html.includes(copy.DRAWER_LOADING_TEXT));
  assert.ok(!html.includes(copy.DRAWER_NOT_FOUND_TEXT));
  assert.ok(!html.includes("data-live-check"));
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
// Gated repository rendering: the gate reason first, then the parameters and
// Integrity, all open. Composite and the Persona view are not drawn: no score
// was computed, and every persona row would repeat the sentence.
// --------------------------------------------------------------------- //

test("findRow + classifyKind: a GATED repo renders the gate sentence first, once, with Composite and the Persona view omitted and nothing folded", () => {
  const state = stateWithRepoDetail(FLAT_GATED_DETAIL);
  assert.equal(classifyKind(FLAT_GATED_DETAIL), "gated");
  const html = renderDrawer(state);
  assert.ok(html.includes("repo-mirror-0"));

  const gateSentence = copy.gateReasonText(FLAT_GATED_DETAIL.gate_reason);
  assert.ok(html.includes(gateSentence), "the gate reason sentence must render");
  assert.ok(html.indexOf(gateSentence) < html.indexOf(copy.PROJECT_PARAMETERS_HEADING), "the gate sentence comes before the parameters");

  // Composite is omitted entirely -- no score/tier/quadrant was ever computed.
  assert.ok(!html.includes(copy.DRAWER_SECTION_COMPOSITE), "no Composite section for a gated repo");
  assert.ok(!html.includes("<details"), "nothing is folded");
  assert.ok(html.includes(copy.PROJECT_PARAMETERS_HEADING));
  assert.ok(html.includes(copy.DRAWER_SECTION_INTEGRITY));

  // A gated repo has no persona composites or gates (disqualification happens before any lens), so the Persona
  // view is not drawn and the banner says the sentence once.
  assert.ok(!html.includes(copy.DRAWER_SECTION_PERSONAS), "no Persona view for a gated repo");
  assert.equal(html.split(gateSentence).length - 1, 1, "the gate sentence appears once");

  // Integrity: raw_hash/snapshot_id still render -- both present on the wire for a gated repo (a synthetic
  // raw_hash stands in server-side, scripts/serve_ui_fixtures.py::_synthetic_raw_hash).
  assert.ok(html.includes(FLAT_GATED_DETAIL.raw_hash));
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

test("findRow + classifyKind: an UNRATED repo renders its reason and evidence, not a crash on missing composite fields", () => {
  const state = stateWithRepoDetail(FLAT_UNRATED_DETAIL);
  assert.equal(classifyKind(FLAT_UNRATED_DETAIL), "unrated");
  // Must not throw (the exact regression: `row` was `undefined` under the
  // old `{kind, row}` shape once the real API's flat response arrived).
  const html = renderDrawer(state);
  assert.ok(html.includes("repo-insufficient_evidence-0"));

  // An unrated repo renders the reason, the parameters, the Persona view and Integrity, all open, with no Composite.
  assert.ok(!html.includes("<details"), "nothing is folded");
  assert.ok(html.includes(copy.PROJECT_PARAMETERS_HEADING), "the parameters render for unrated");
  assert.ok(html.includes(copy.DRAWER_SECTION_PERSONAS), "Persona view section renders for unrated");
  assert.ok(html.includes(copy.DRAWER_SECTION_INTEGRITY), "Integrity section renders for unrated");
  assert.ok(!html.includes(copy.DRAWER_SECTION_COMPOSITE), "no Composite section for unrated");
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
// AUD-WEB H1: web/app/views/drawer.js:351 reads copy.RETRY_LABEL for
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
function methodologyShell(snapshot, doc = METHODOLOGY_DOC, report = null) {
  return renderMethodologyShell({ methodology: doc, snapshot, report, methodologyLoading: false, methodologyError: null });
}

test("methodology: the index and the body come from one list (same ids, same order, each once)", () => {
  const html = methodologyShell(SNAPSHOT_DOC);
  const index = sectionIndex(METHODOLOGY_DOC, SNAPSHOT_DOC);
  const sectionIds = [...html.matchAll(/<section class="doc-section" id="([^"]+)"/g)].map((m) => m[1]);
  const jumpIds = [...html.matchAll(/class="doc-index-link" href="#[^"]+" data-doc-jump="([^"]+)"/g)].map((m) => m[1]);
  assert.equal(index.length, 11, "snapshot + the ten methodology sections (Limits among them)");
  assert.deepEqual(sectionIds, index.map((e) => e.id));
  assert.deepEqual(jumpIds, index.map((e) => e.id));
  assert.equal(new Set(sectionIds).size, sectionIds.length, "section ids are unique");
});

test("methodology: the database digest row shows fetchManifest's digest; the API's server has none, so no row", async () => {
  const realFetch = globalThis.fetch;
  let requests = 0;
  globalThis.fetch = () => {
    requests += 1;
    return Promise.reject(new Error("fetchManifest makes no request against the API"));
  };
  try {
    assert.deepEqual(await fetchManifest(), { ok: true, data: null });
  } finally {
    globalThis.fetch = realFetch;
  }
  assert.equal(requests, 0, "answered without a request");
  const digest = "ab".repeat(32);
  const shown = renderMethodologyShell({
    methodology: METHODOLOGY_DOC, snapshot: SNAPSHOT_DOC, methodologyLoading: false, methodologyError: null, sourceDbSha256: digest,
  });
  const hidden = methodologyShell(SNAPSHOT_DOC);
  assert.ok(shown.includes(`<code data-v="snapshot.source_db_sha256">${digest}</code>`), "a manifest's digest is printed");
  assert.ok(shown.includes(escapeHtml(copy.METHODOLOGY_DB_SHA256_LABEL)));
  assert.ok(!hidden.includes("snapshot.source_db_sha256"), "no manifest: no row");
  assert.ok(!hidden.includes(escapeHtml(copy.METHODOLOGY_DB_SHA256_LABEL)));
});

test("brand: the header carries the symbol (decorative), the name and the slogan, all from copy.js", () => {
  const html = renderBrand();
  assert.match(html, /<svg class="brand-mark"[^>]*aria-hidden="true"/, "the symbol is hidden from assistive technology");
  assert.ok(html.includes(`</svg>${escapeHtml(copy.BRAND_NAME)}</span>`), "the name follows the symbol");
  assert.ok(html.includes(`<span class="brand-tagline">${escapeHtml(copy.BRAND_TAGLINE)}</span>`), "the slogan");
});

test("methodology: Why Porchlamp sits above the document, each paragraph once, and only once the document has loaded", () => {
  const html = methodologyShell(SNAPSHOT_DOC, METHODOLOGY_DOC, REPORT_EXAMPLE);
  const why = html.indexOf('<section class="why-card"');
  assert.ok(why > -1, "the Why section renders");
  assert.ok(why < html.indexOf('<div class="doc-layout"'), "and comes before the document");
  assert.ok(html.includes(`>${escapeHtml(copy.WHY_HEADING)}</h2>`));
  for (const paragraph of copy.WHY_PARAGRAPHS) {
    const drawn = paragraph === copy.WHY_FINDING_TEMPLATE ? fillFromReport(paragraph, REPORT_EXAMPLE) : escapeHtml(paragraph);
    assert.equal(html.split(`<p>${drawn}</p>`).length - 1, 1, paragraph.slice(0, 40));
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
  const rowIds = [...html.matchAll(/<th scope="row" class="cell-id">(?:<span class="param-label">[^<]*<\/span>)?<code>([^<]+)<\/code><\/th>/g)].map((m) => m[1]);
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
  assert.match(base, /^https:\/\/[^/]+\/$/, "og:url is the absolute site root, a host root on the site's own domain");
  const canonical = html.match(/<link rel="canonical" href="([^"]*)">/);
  assert.ok(canonical && canonical[1] === base, "rel=canonical matches og:url");
  assert.equal(meta("property", "og:image"), base + "og.png");
  assert.equal(meta("name", "twitter:image"), base + "og.png");
  assert.equal(meta("name", "twitter:card"), "summary_large_image");
  for (const k of ["og:title", "og:description", "og:image:alt"]) assert.ok(meta("property", k), k);
  assert.match(html, /<title>Porchlamp rankings: find the projects that leave the light on<\/title>/);
  // The frame is empty: the export's prerender draws the ranked view into it (web/prerender.mjs).
  assert.match(html, /<main class="app-main" id="app-main" tabindex="-1">\s*<\/main>/);
  const png = readFileSync(path.join(here, "..", "og.png"));
  assert.equal(png.subarray(1, 4).toString(), "PNG");
  assert.equal(png.readUInt32BE(16), 1200, "og:image:width matches the file");
  assert.equal(png.readUInt32BE(20), 630, "og:image:height matches the file");
});

test('SEO: "Porchlamp rankings" leads the title, description and the leaderboard heading', () => {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const html = readFileSync(path.join(here, "..", "index.html"), "utf8");
  const title = html.match(/<title>([^<]*)<\/title>/)[1];
  const description = html.match(/<meta name="description" content="([^"]*)"/)[1];
  assert.match(title, /^Porchlamp rankings\b/);
  assert.ok(title.length <= 65, `title is ${title.length} chars, search results cut it near 60`);
  assert.match(description, /^Porchlamp rankings: /);
  assert.ok(description.length <= 160, `description is ${description.length} chars`);
  assert.equal(copy.LEADERBOARD_HEADING, "Porchlamp rankings");
  const view = readFileSync(path.join(here, "..", "views", "leaderboard.js"), "utf8");
  assert.match(view, /id="leaderboard-heading"[^`]*\$\{escapeHtml\(copy\.LEADERBOARD_HEADING\)\}/);
});

test("SEO: static canonical, root robots.txt on the site's own domain, canonical-only sitemap, lean head and Search Console verification", () => {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const webRoot = path.join(here, "..");
  const html = readFileSync(path.join(webRoot, "index.html"), "utf8");
  const base = html.match(/<link rel="canonical" href="([^"]*)">/)[1];

  // 1. The canonical link is static: app.js never rewrites it, so every query view consolidates to the site root.
  const appSrc = readFileSync(path.join(webRoot, "app.js"), "utf8");
  assert.doesNotMatch(appSrc, /canonical[^\n]*\.setAttribute\(|setAttribute\(\s*["']href["'][^\n]*canonical/i, "app.js must not rewrite rel=canonical");
  assert.doesNotMatch(appSrc, /canonicalHrefForState|syncCanonicalLink/, "no canonical rewrite helper in app.js");

  // 2. The site is the root of its own domain, so robots.txt here is the host's robots.txt: it blocks nothing and names the sitemap.
  const site = new URL(base);
  assert.equal(site.pathname, "/", "the canonical base is a host root");
  assert.equal(readFileSync(path.join(webRoot, "CNAME"), "utf8").trim(), site.host, "CNAME names the canonical host");
  const robots = readFileSync(path.join(webRoot, "robots.txt"), "utf8");
  assert.match(robots, /^User-agent: \*$/m);
  assert.doesNotMatch(robots, /^Disallow: \S/m, "robots.txt blocks nothing");
  assert.ok(robots.includes(`Sitemap: ${base}sitemap.xml`), "robots.txt names the canonical sitemap");
  for (const f of ["index.html", "sitemap.xml", "robots.txt"]) {
    assert.ok(!readFileSync(path.join(webRoot, f), "utf8").includes("pctablet505.github.io/porchlamp"), `${f} keeps no old github.io address`);
  }

  // 2a. Lean head: no meta keywords, no googlebot/bingbot copies of the robots tag, no hreflang on a one-language site.
  assert.doesNotMatch(html, /<meta name="(keywords|googlebot|bingbot)"/, "no ignored or duplicate meta tags");
  assert.doesNotMatch(html, /hreflang=/, "no hreflang links");
  assert.match(html, /<meta name="robots" content="index, follow/, "one robots meta tag stays");

  // 2b. The IndexNow key file. (The JSON-LD, feed.xml and llms.txt are generated at export: tests/test_home_pages.py.)
  const indexNowKey = "7c9d4a2e8b1f6a3c5d8e9f0a1b2c3d4e";
  assert.equal(readFileSync(path.join(webRoot, `${indexNowKey}.txt`), "utf8").trim(), indexNowKey);

  // 3. Google Search Console verification file
  const gscFile = "googled8fac072696a3851.html";
  const gsc = readFileSync(path.join(webRoot, gscFile), "utf8").trim();
  assert.equal(gsc, `google-site-verification: ${gscFile}`);

  // 4. sitemap.xml is a sitemap index naming the two child sitemaps; the pages sitemap lists canonical addresses only,
  //    with no priority or changefreq. (sitemap-projects.xml is written by the exporter, one entry per project page.)
  const sitemap = readFileSync(path.join(webRoot, "sitemap.xml"), "utf8");
  assert.match(sitemap, /<sitemapindex xmlns="http:\/\/www\.sitemaps\.org\/schemas\/sitemap\/0\.9">/, "sitemap.xml is a sitemap index");
  assert.doesNotMatch(sitemap, /<urlset/, "the index lists sitemaps, not pages");
  const children = [...sitemap.matchAll(/<sitemap>\s*<loc>([^<]+)<\/loc>/g)].map((m) => m[1]);
  assert.deepEqual(
    children,
    [`${base}sitemap-pages.xml`, `${base}sitemap-projects.xml`],
    "the sitemap index names both child sitemaps"
  );
  // sitemap-pages.xml is written by the exporter for its own snapshot (scripts/export_static.py _sitemap_pages).
  assert.ok(!existsSync(path.join(webRoot, "sitemap-pages.xml")), "the pages sitemap is not shipped by hand");
  for (const loc of children) assert.ok(!loc.includes("?"), `no query address in the sitemap index: ${loc}`);
  assert.doesNotMatch(sitemap, /<(priority|changefreq)>/, "no priority or changefreq");
});

// ---- PR 1 UX/UI fixes ---------------------------------------------------------------------

import { projectRowForLens, deriveTierCeiling } from "../views/drawer.js";
import { createRowElement, createCardElement } from "../components/table.js";
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
      balanced: { porchlamp: null, tier: null, coverage: 0.48, rated: false },
      ai_assisted: { porchlamp: 78.4, tier: "B", coverage: 0.68, rated: true },
    },
    persona_gates: {},
  };
  assert.equal(classifyKindForLens(personaOnlyRepo, "balanced"), "unrated");
  assert.equal(classifyKindForLens(personaOnlyRepo, "ai_assisted"), "rated");

  const projected = projectRowForLens(personaOnlyRepo, "ai_assisted");
  assert.equal(projected.porchlamp, 78.4);
  assert.equal(projected.tier, "B");
  assert.equal(projected.coverage, 0.68);
  assert.equal(projected.tier_ceiling, "B", "derives tier_ceiling B from coverage 0.68 when omitted on wire");

  const state = {
    ...stateWithRepoDetail(personaOnlyRepo),
    persona: "ai_assisted",
  };
  const html = renderDrawer(state);
  assert.ok(html.includes(copy.DRAWER_SECTION_COMPOSITE), "persona-rated repo renders Composite section");
  assert.ok(html.includes("78.40"), "projected persona score renders in drawer");
  assert.ok(html.includes("B (capped)"), "derived tier_ceiling shows (capped) badge in drawer");
});

test("drawer: deriveTierCeiling matches backend _no_ceiling_or_band coverage thresholds", () => {
  assert.equal(deriveTierCeiling(0.9), null);
  assert.equal(deriveTierCeiling(0.8999), "A");
  assert.equal(deriveTierCeiling(0.85), "A");
  assert.equal(deriveTierCeiling(0.68), "B");
  assert.equal(deriveTierCeiling(0.45), null);
  assert.equal(deriveTierCeiling(0.68, "A"), "A", "explicit ceiling takes precedence when present");
});

test("drawer: renderDrawer renders Persona view table for a rated repository (unrated_reason: null) with populated persona_composites", () => {
  const ratedWithPersonas = {
    ...FLAT_RATED_DETAIL,
    unrated_reason: null,
    gate_reason: null,
    persona_composites: {
      balanced: { porchlamp: 81.2, tier: "A", coverage: 0.96, rated: true },
      first_timer: { porchlamp: null, tier: null, coverage: 0.42, rated: false },
    },
    persona_gates: {
      senior_systems: "min_elephant_factor",
    },
  };
  const html = renderDrawer(stateWithRepoDetail(ratedWithPersonas));
  assert.ok(html.includes(copy.DRAWER_SECTION_PERSONAS));
  assert.ok(html.includes("81.20"));
  assert.ok(html.includes(escapeHtml(copy.NOT_RATED_LABEL)));
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
    persona_gates: {
      balanced: "unrated: no_external_workflow",
      first_timer: "unrated: no_external_workflow",
    },
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
const COVERAGE_FULL = "coverage 90\u2013100%";
const COVERAGE_CEILING_A = "coverage 80\u201390%";

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

test("maintainer badge: the Markdown links the image to the project's own page on the public site, with score, coverage and snapshot in alt and title (U-1, U-6)", () => {
  const row = badgeRow({ porchlamp: 84.5, coverage: 0.85, tier: "A", tier_ceiling: "A", snapshot_id: "2026-10-13c" });
  const spec = copy.maintainerBadgeSpec(row);
  assert.equal(
    spec.alt,
    "Porchlamp: \u2666 Sweet Spot \u00b7 Tier A (capped) \u00b7 Score 84.50 \u00b7 Coverage 85% \u00b7 Ceiling A \u00b7 Snapshot 2026-10-13c",
  );
  const md = copy.maintainerBadgeMarkdown(row, SITE_BASE);
  assert.equal(md, `[![${spec.alt}](${SITE_BASE}badges/sweet-spot-tier-a-capped.svg)](${SITE_BASE}projects/riverline/httpkit/ "${spec.alt}")`);
  assert.equal(copy.maintainerBadgeMarkdown(row, SITE_BASE.replace(/\/$/, "")), md, "a base without its trailing slash gives the same links");
  // Nothing that could end the alt text or the title early reaches the snippet.
  const hostile = copy.maintainerBadgeSpec({ ...row, snapshot_id: 'x"] [y\\' });
  assert.ok(!/["[\]\\]/.test(hostile.alt), hostile.alt);
  assert.equal(copy.maintainerBadgeMarkdown(FLAT_GATED_DETAIL, SITE_BASE), "");
});

test("maintainer badge: the drawer shows the balanced badge at the end of Composite, never one a persona lens would mint (U-4, D-DEF-2)", () => {
  const html = renderDrawer(stateWithRepoDetail(FLAT_RATED_DETAIL), { siteBaseUrl: SITE_BASE });
  const box = html.indexOf('data-maintainer-badge="sweet-spot-tier-s"');
  const composite = html.indexOf('id="rating"');
  const next = html.indexOf('id="parameters"');
  assert.ok(composite > -1 && composite < box && box < next, "the badge box is inside the Composite card, before the sections that follow");
  assert.ok(!html.includes("<details"), "a rated drawer folds nothing");
  assert.ok(html.includes('data-badge-copy="true"') && html.includes('data-badge-markdown="true"'));
  assert.ok(html.includes(escapeHtml(`${SITE_BASE}projects/riverline/httpkit/`)), "the Markdown uses the address app.js passes in");
  assert.ok(html.includes('src="/badges/sweet-spot-tier-s.svg"'), "the preview loads from this site's root");

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
    persona: "senior_systems",
    compareRepos: ["org-a/repo-1", "org-b/repo-2"],
    compareDataByRepo: {
      "org-a/repo-1": {
        kind: "rated",
        row: {
          ...FLAT_RATED_DETAIL,
          owner: "org-a",
          name: "repo-1",
          porchlamp_score: 82.0,
          pillar_responsiveness: 85.0,
          pillar_criticality: 80.0,
          pillar_governance: 78.0,
          pillar_rigor: 84.0,
          pillar_accessibility: 79.0,
          persona_composites: {
            ...FLAT_RATED_DETAIL.persona_composites,
            senior_systems: { porchlamp: 86.25, tier: "B", coverage: 0.68, rated: true },
          },
        },
        evidence: [],
      },
      "org-b/repo-2": {
        kind: "rated",
        row: {
          ...FLAT_RATED_DETAIL,
          owner: "org-b",
          name: "repo-2",
          porchlamp_score: 71.0,
          pillar_responsiveness: 70.0,
          pillar_criticality: 72.0,
          pillar_governance: 74.0,
          pillar_rigor: 68.0,
          pillar_accessibility: 71.0,
          persona_gates: {
            senior_systems: "min_elephant_factor",
          },
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
  assert.ok(html.includes("86.25"), "projects active persona composite score in compare table");
  assert.ok(html.includes("B (capped)"), "derives tier_ceiling from persona coverage in compare table");
  assert.ok(html.includes(escapeHtml(copy.personaGateReason("min_elephant_factor"))), "shows persona gate reason for persona-gated repo in compare table");
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

test("the Not ranked rows and the compare picker print an ecosystem's plain name, never the engine's code", () => {
  for (const [code, name] of copy.ECOSYSTEMS) {
    const chip = `<span class="eco-chip">${escapeHtml(name)}</span>`;
    const row = { owner: "a", name: "b", ecosystem: code, gate_reason: "archived" };
    const groups = { gated: [row], unrated: [], persona_hidden: [], counts: PAGER_COUNTS, truncated: false };
    assert.ok(notRankedPageRows(notRankedFlatten(groups), 1, 20).includes(chip), `not ranked: ${code}`);
    const compare = renderCompareShell({
      ...defaultState(),
      tab: "compare",
      compareRepos: [],
      compareQuery: "a",
      compareSearchResults: [{ owner: "a", name: "b", ecosystem: code }],
    });
    assert.ok(compare.includes(chip), `compare: ${code}`);
  }
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
test("primary nav: centeredScrollLeft puts the current link mid-view and stays inside the strip", () => {
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

// ------------------------------------------------------------- issue #20 regressions
test("copy.formatParameterValue: rounds long floats to 4 decimals without trailing zeros while preserving integers, small floats, and non-finite guards", () => {
  assert.equal(copy.formatParameterValue(0.41509433962264153), "0.4151");
  assert.equal(copy.formatParameterValue(148.94787916666666), "148.9479");
  assert.equal(copy.formatParameterValue(42), "42");
  assert.equal(copy.formatParameterValue(0.5), "0.5");
  assert.equal(copy.formatParameterValue(0.000042), "0.000042");
  assert.equal(copy.formatParameterValue(-0), "0");
  assert.equal(copy.formatParameterValue(NaN), copy.MISSING_VALUE);
  assert.equal(copy.formatParameterValue(Infinity), copy.MISSING_VALUE);
  assert.equal(copy.formatParameterValue("permissive"), "permissive");
});

test("copy.sortedAnnouncement: includes both column label and sort direction", () => {
  assert.equal(copy.sortedAnnouncement("Score", "descending"), "Sorted by Score (descending).");
  assert.equal(copy.sortedAnnouncement("Rank", "ascending"), "Sorted by Rank (ascending).");
  assert.equal(copy.sortedAnnouncement("Score"), "Sorted by Score.");
});

test("table.sortColumnLabel: maps raw sort field IDs to human-readable column names", () => {
  assert.equal(sortColumnLabel("rank"), "Rank");
  assert.equal(sortColumnLabel("-porchlamp"), "Score");
  assert.equal(sortColumnLabel("p_responsiveness"), "Responsiveness");
  assert.equal(sortColumnLabel("-p_rigor"), "Rigor");
});

test("table.renderColumnHeader: includes visual sort arrow indicator and is-active class on the active sort column", () => {
  const html = renderColumnHeader("-porchlamp", false);
  assert.ok(html.includes('data-sort-field="porchlamp"'), "porchlamp header exists");
  assert.ok(html.includes('class="sort-arrow"'), "visual sort arrow rendered");
  assert.ok(html.includes("↑"), "-porchlamp is ascending, so ↑ arrow renders");
  const defaultHtml = renderColumnHeader("porchlamp", false);
  assert.ok(defaultHtml.includes("↓"), "porchlamp is descending (best-first), so ↓ arrow renders");
});

test("table.createRowElement and createCardElement: honor opts.selected on initial creation", () => {
  const fakeDoc = {
    createElement(tag) {
      return {
        tagName: tag.toUpperCase(),
        className: "",
        classList: { add() {} },
        dataset: {},
        attributes: {},
        children: [],
        setAttribute(k, v) { this.attributes[k] = v; },
        appendChild(c) { this.children.push(c); },
      };
    },
  };
  const row = {
    owner: "a",
    name: "b",
    ecosystem: "cargo",
    rank: 1,
    porchlamp: 85.0,
    tier: "A",
    coverage: 0.9,
  };
  const tr = createRowElement(fakeDoc, row, { selected: true });
  assert.ok(tr.className.split(" ").includes("row-selected"), "tr carries row-selected when opts.selected is true");
  const card = createCardElement(fakeDoc, row, { selected: true });
  assert.ok(card.className.split(" ").includes("row-selected"), "card carries row-selected when opts.selected is true");
});

test("state.reduce SET_TAB: isolates rankedPage/rankedSort from Configure and Matrix tab switches", () => {
  let s = defaultState();
  s = reduce(s, { type: "SET_PAGE", page: 3 });
  s = reduce(s, { type: "SET_PARAMS", payload: { sort: "-porchlamp", page: 3 } });
  assert.equal(s.page, 3);
  assert.equal(s.sort, "-porchlamp");

  // Switch to Configure and sort/page there
  s = reduce(s, { type: "SET_TAB", tab: "configure" });
  s = reduce(s, { type: "SET_CONFIGURE_SORT", sort: "p_rigor" });
  s = reduce(s, { type: "SET_CONFIGURE_PAGE", page: 2 });
  assert.equal(s.rankedPage, 3, "rankedPage preserved while on Configure");
  assert.equal(s.rankedSort, "-porchlamp", "rankedSort preserved while on Configure");

  // Switch to Matrix: URL must not inherit Configure's sort or page
  s = reduce(s, { type: "SET_TAB", tab: "matrix" });
  const matrixUrl = serializeLocation(s);
  assert.ok(!matrixUrl.includes("sort="), `Matrix URL should not leak sort: ${matrixUrl}`);
  assert.ok(!matrixUrl.includes("page="), `Matrix URL should not leak page: ${matrixUrl}`);

  // Switch back to Ranked: restores page 3 and -porchlamp
  s = reduce(s, { type: "SET_TAB", tab: "ranked" });
  assert.equal(s.page, 3);
  assert.equal(s.sort, "-porchlamp");
});

test("leaderboard.renderMobileSortBar: renders mobile sort select and direction toggle", () => {
  const html = renderMobileSortBar("porchlamp");
  assert.ok(html.includes("data-mobile-sort-select"));
  assert.ok(html.includes("data-mobile-sort-dir"));
  assert.ok(html.includes("↓"));
});

test("drawer.classifyKindForLens: classifies eligible=false with 'unrated: ...' gate_reason as unrated", () => {
  const unratedFromApi = {
    owner: "org",
    name: "repo",
    eligible: false,
    rated: false,
    gate_reason: "unrated: insufficient_evidence",
    coverage: 0.45,
  };
  assert.equal(classifyKindForLens(unratedFromApi, "balanced"), "unrated");
});

test("drawer.renderPillarRadar: omits unmeasured pillars from polygon area instead of plotting them as 0 at the origin, and uses polyline for 2 measured pillars", () => {
  const row = {
    p_responsiveness: 80.0,
    p_criticality: 75.0,
    p_governance: null,
    p_rigor: 85.0,
    p_accessibility: 70.0,
  };
  const svg = renderPillarRadar(row);
  const polyMatch = svg.match(/<polygon points="([^"]+)" class="radar-area"/);
  assert.ok(polyMatch, "radar polygon renders when >= 3 pillars are measured");
  const vertices = polyMatch[1].trim().split(/\s+/);
  assert.equal(vertices.length, 4, "only the 4 measured pillars form the polygon vertices (unmeasured governance is not plotted at center 190,130)");
  assert.ok(!vertices.includes("190.0,130.0"), "center origin (190.0,130.0) is not used as a false 0 vertex");
  assert.ok(svg.includes("radar-point-unmeasured"), "unmeasured pillar has a distinct hollow marker on the outer ring");

  const twoPillarRow = {
    p_responsiveness: 80.0,
    p_criticality: 75.0,
    p_governance: null,
    p_rigor: null,
    p_accessibility: null,
  };
  const svg2 = renderPillarRadar(twoPillarRow);
  assert.ok(svg2.includes('<polyline points="'), "2 measured pillars render as a <polyline> connector");
  assert.ok(!/<polygon[^>]*class="radar-area"/.test(svg2), "2 measured pillars do not render a degenerate radar-area <polygon>");
});

test("matrix.clientToDomainCoords: accounts for zoomed and panned viewBox when mapping client coordinates", () => {
  const rect = { left: 100, top: 50, width: 500, height: 500 };
  const zoomedView = { scale: 2, x: 25, y: 25 };
  const coords = clientToDomainCoords(350, 300, rect, zoomedView);
  assert.equal(coords.domainX, 50);
  assert.equal(coords.domainY, 50);
  assert.equal(coords.toleranceDomainUnits, (15 / 500) * 50);
});

test("configure.filterReprojectRows and renderConfigurePager: filters by ecosystem/query and renders First/Last buttons", () => {
  const rows = [
    { owner: "PyO3", name: "pyo3", ecosystem: "cargo", rank: 1 },
    { owner: "pallets", name: "flask", ecosystem: "pypi", rank: 2 },
  ];
  assert.equal(filterReprojectRows(rows, { ecosystem: "cargo", query: "" }).length, 1);
  assert.equal(filterReprojectRows(rows, { ecosystem: "PyPI", query: "" }).length, 1, "case-insensitive ecosystem match");
  assert.equal(filterReprojectRows(rows, { ecosystem: "", query: "flask" }).length, 1);
  const topPager = renderConfigurePager(1, 20, 100);
  assert.ok(topPager.includes("data-configure-pager-first"));
  assert.ok(topPager.includes("data-configure-pager-last"));
  assert.ok(topPager.includes("data-configure-pager-size-select"));
  const bottomPager = renderConfigurePager(1, 20, 100, { bottom: true });
  assert.ok(!bottomPager.includes("data-configure-pager-size-select"));
});

// --------------------------------------------------------------------- //
// Black-hole determination evidence (D-GATE-5, Packet 4)
// --------------------------------------------------------------------- //

const FLAT_BLACK_HOLE_DETAIL = {
  ...FLAT_GATED_DETAIL,
  owner: "ghost-org",
  name: "abandoned-lib",
  gate_reason: "black_hole",
  black_hole_evidence: {
    unanswered: 12,
    population_n: 15,
    unanswered_share: 0.8,
    wilson_lower_bound: 0.5982,
    threshold_share: 0.5,
    min_n: 10,
    window_start: "2026-03-01T00:00:00Z",
    window_end: "2026-09-01T00:00:00Z",
    fetched_at: "2026-09-01T01:00:00Z",
    source: "raw/pr_window/ghost-org__abandoned-lib.json",
    source_sha256: "c".repeat(64),
    excluded: {
      draft: 2,
      self_closed: 1,
      new_account: 3,
      bot: 4,
      internal: 5,
      under_14d: 6,
    },
    rule_inputs_measured: true,
    counted_prs: [
      {
        number: 101,
        created_at: "2026-04-01T12:00:00Z",
        answered_within_14d: false,
        first_response_at: null,
        response_kind: "none",
      },
      {
        number: 102,
        created_at: "2026-05-01T12:00:00Z",
        answered_within_14d: true,
        first_response_at: "2026-05-03T12:00:00Z",
        response_kind: "review_or_comment",
      },
    ],
  },
};

/** The black-hole evidence card of a project, as the project view draws it (the `section#black-hole` of `renderProject`). */
function blackHoleCard(detail, evidence = []) {
  const html = renderProject({ detail, evidence }, { mode: "panel" });
  const start = html.indexOf('<section class="project-card black-hole-evidence"');
  return start < 0 ? "" : html.slice(start, html.indexOf("</section>", start) + "</section>".length);
}

/** Markup as text: tags removed, so a number inside its hook reads as part of its sentence. */
const textOf = (html) => html.replace(/<[^>]*>/g, "");

test("black-hole card: renders exact counts, percentage, Wilson lower bound, window dates, exclusions, and PR links without contributor usernames", () => {
  const card = blackHoleCard(FLAT_BLACK_HOLE_DETAIL);
  const html = textOf(card);
  assert.ok(card.includes('data-black-hole-evidence="true"'));
  assert.ok(html.includes(copy.BLACK_HOLE_EVIDENCE_HEADING));
  assert.ok(html.includes(copy.BLACK_HOLE_RULE_TEXT));
  assert.ok(html.includes("12 of 15 (80.0%) external non-bot PRs had no maintainer response, merge, or close within 14 days"));
  assert.ok(html.includes("95% Wilson lower confidence bound: 59.8% (threshold: &gt; 50.0%, min n = 10)"));
  assert.ok(html.includes("Window: 1 March to 1 September 2026"), "the window reads as dates, not ISO timestamps");
  assert.ok(html.includes("Source: raw/pr_window/ghost-org__abandoned-lib.json"));
  assert.ok(html.includes(escapeHtml(copy.BLACK_HOLE_EXCLUSIONS_TEXT)));
  assert.ok(html.includes("2 draft"));
  assert.ok(html.includes("1 author self-closed"));
  assert.ok(html.includes("3 new account"));
  assert.ok(html.includes("4 bot"));
  assert.ok(html.includes("5 internal/maintainer"));
  assert.ok(html.includes("6 open &lt;14d"));
  assert.ok(html.includes(copy.BLACK_HOLE_RECHECK_TEXT));

  // PR links (#101, #102), each number a hook; the opening dates read as dates
  assert.ok(card.includes('<a href="https://github.com/ghost-org/abandoned-lib/pull/101" target="_blank" rel="noopener noreferrer">#<span data-v="bh.counted_prs.0.number">101</span></a>'));
  assert.ok(card.includes('<a href="https://github.com/ghost-org/abandoned-lib/pull/102" target="_blank" rel="noopener noreferrer">#<span data-v="bh.counted_prs.1.number">102</span></a>'));
  assert.ok(card.includes('<span data-v="bh.counted_prs.0.created_at">1 April 2026</span>'));

  // Privacy: never include contributor usernames/logins
  for (const forbiddenLogin of ["octocat", "author", "contributor", "login", "maintainer_login"]) {
    assert.ok(!html.includes(`@${forbiddenLogin}`));
  }
});

test("black-hole card: escapes untrusted strings against XSS", () => {
  const xssDetail = {
    ...FLAT_BLACK_HOLE_DETAIL,
    owner: '<script>alert("owner")</script>',
    name: 'repo"onmouseover="alert(1)',
    black_hole_evidence: {
      ...FLAT_BLACK_HOLE_DETAIL.black_hole_evidence,
      source: '<script>alert("source")</script>',
      window_start: '<img src=x onerror=alert(1)>',
    },
  };
  const html = blackHoleCard(xssDetail);
  assert.ok(!html.includes("<script>alert("));
  assert.ok(!html.includes("<img src=x"));
  assert.ok(html.includes("&lt;script&gt;alert("));
  assert.ok(html.includes("&lt;img src=x"));
});

test("black-hole card: data recorded before the RFC-0008 fields hides the draft and self-closed counts and says so", () => {
  assert.equal(typeof copy.BLACK_HOLE_RULE_INPUTS_NOT_MEASURED_TEXT, "string");
  assert.ok(!copy.BLACK_HOLE_EXCLUSIONS_TEXT.includes("applied"));
  const notMeasured = textOf(blackHoleCard({
    ...FLAT_BLACK_HOLE_DETAIL,
    black_hole_evidence: { ...FLAT_BLACK_HOLE_DETAIL.black_hole_evidence, rule_inputs_measured: false },
  }));
  assert.ok(notMeasured.includes(escapeHtml(copy.BLACK_HOLE_RULE_INPUTS_NOT_MEASURED_TEXT)));
  assert.ok(!notMeasured.includes("2 draft"));
  assert.ok(!notMeasured.includes("author self-closed"));
  assert.ok(notMeasured.includes("3 new account"));
  const measured = textOf(blackHoleCard(FLAT_BLACK_HOLE_DETAIL));
  assert.ok(!measured.includes(escapeHtml(copy.BLACK_HOLE_RULE_INPUTS_NOT_MEASURED_TEXT)));
  assert.ok(measured.includes("2 draft"));
});

test("black-hole card: an XSS payload in counted_prs[].created_at is rendered inert", () => {
  const evidence = FLAT_BLACK_HOLE_DETAIL.black_hole_evidence;
  const html = blackHoleCard({
    ...FLAT_BLACK_HOLE_DETAIL,
    black_hole_evidence: {
      ...evidence,
      counted_prs: [{ ...evidence.counted_prs[0], created_at: '<svg onload=alert("created")>' }],
    },
  });
  assert.ok(!html.includes("<svg onload="));
  assert.ok(html.includes("&lt;svg onload="));
});

test("black-hole card: an owner name that needs URL encoding produces an encoded PR link", () => {
  const html = blackHoleCard({ ...FLAT_BLACK_HOLE_DETAIL, owner: "org with space&co" });
  assert.ok(html.includes('href="https://github.com/org%20with%20space%26co/abandoned-lib/pull/101"'));
});

test("renderDrawer: a black_hole gated repo draws the gate sentence, then the evidence card, with no <details> section", () => {
  const state = stateWithRepoDetail(FLAT_BLACK_HOLE_DETAIL);
  const html = renderDrawer(state);

  const gateSentence = copy.gateReasonText(FLAT_BLACK_HOLE_DETAIL.gate_reason);
  const gateSentenceIndex = html.indexOf(gateSentence);
  const evidencePanelIndex = html.indexOf('data-black-hole-evidence="true"');

  assert.ok(gateSentenceIndex >= 0, "the gate reason sentence must render");
  assert.ok(evidencePanelIndex >= 0, "black-hole evidence panel must render");
  assert.ok(gateSentenceIndex < evidencePanelIndex, "gate sentence must render before the evidence card");
  assert.ok(!html.includes("<details"), "the panel hides nothing behind a disclosure");
  assert.ok(!html.includes('role="alert"'), "the gate sentence is content present at load, not an alert");

  // Evidence card does NOT repeat the exact gateReasonText("black_hole")
  const card = blackHoleCard(FLAT_BLACK_HOLE_DETAIL);
  assert.ok(!card.includes(gateSentence), "evidence card must not repeat exact gate reason sentence");
});

test("black-hole card: renders rule, exclusions, and recovery note when black_hole_evidence is absent, and recomputes nothing from the evidence rows", () => {
  const noEvidenceDetail = {
    ...FLAT_BLACK_HOLE_DETAIL,
    black_hole_evidence: null,
  };
  const ghostRow = { param_id: "gr14_ghost_rate", pillar: "responsiveness", value: 0.75, score: 25.0, n: 20, unmeasured_reason: null };
  const html = blackHoleCard(noEvidenceDetail, [ghostRow]);
  assert.ok(html.includes(copy.BLACK_HOLE_EVIDENCE_HEADING));
  assert.ok(html.includes(copy.BLACK_HOLE_RULE_TEXT));
  assert.ok(html.includes(escapeHtml(copy.BLACK_HOLE_EXCLUSIONS_TEXT)));
  assert.ok(html.includes(copy.BLACK_HOLE_RECHECK_TEXT));
  assert.ok(!html.includes("external non-bot PRs had no maintainer response"));
});

test("state.reduce OPEN_REPO and createStore(panel address): immediately enter loading state to prevent synchronous not-found flash, and guard stale repo responses", () => {
  const store = createStore(stateFromLocation("/projects/org/alpha/", ""));
  const hydrated = store.getState();
  assert.equal(hydrated.repo, "org/alpha");
  assert.equal(hydrated.repoDetailLoading, true);
  assert.equal(hydrated.evidenceLoading, true);
  const hydratedDrawerHtml = renderDrawer(hydrated);
  assert.ok(!hydratedDrawerHtml.includes(copy.DRAWER_NOT_FOUND_TEXT), "URL-hydrated drawer shows skeleton instead of not-found error");

  const malformedStore = createStore({ repo: "malformed-no-slash" });
  const malformedState = malformedStore.getState();
  assert.equal(malformedState.repoDetailLoading, false, "malformed repo key does not stick in loading state");
  const malformedHtml = renderDrawer(malformedState);
  assert.ok(malformedHtml.includes(copy.DRAWER_NOT_FOUND_TEXT), "malformed repo key immediately renders not-found state");
  assert.ok(!malformedHtml.includes("undefined"), "malformed repo key never renders /undefined in heading");

  let s = defaultState();
  s = reduce(s, { type: "OPEN_REPO", repo: "org/alpha" });
  assert.equal(s.repoDetailLoading, true);
  assert.equal(s.evidenceLoading, true);
  assert.ok(!renderDrawer(s).includes(copy.DRAWER_NOT_FOUND_TEXT), "OPEN_REPO immediately shows loading skeleton before fetch dispatch");

  // User switches to org/beta while org/alpha is still in flight
  s = reduce(s, { type: "OPEN_REPO", repo: "org/beta" });
  s = reduce(s, { type: "REPO_DETAIL_OK", repo: "org/alpha", data: { owner: "org", name: "alpha", rated: true, eligible: true } });
  s = reduce(s, { type: "EVIDENCE_OK", repo: "org/alpha", data: [] });
  s = reduce(s, { type: "REPO_DETAIL_ERROR", repo: "org/alpha", error: { status: 500 } });
  s = reduce(s, { type: "EVIDENCE_ERROR", repo: "org/alpha", error: { status: 500 } });
  assert.equal(s.repoDetailLoading, true, "stale REPO_DETAIL_OK/ERROR for org/alpha does not clear loading flag for org/beta");
  assert.equal(s.evidenceLoading, true, "stale EVIDENCE_OK/ERROR for org/alpha does not clear loading flag for org/beta");
  assert.equal(s.repoDetailError, null, "stale REPO_DETAIL_ERROR for org/alpha does not pollute org/beta");
  assert.ok(s.repoDetailByKey["org/alpha"], "stale REPO_DETAIL_OK still caches org/alpha");

  // SET_PARAMS with resetTabSlices resets non-active tab slices without leaking resetTabSlices onto state
  s = { ...s, rankedPage: 4, notRankedPage: 3, configurePage: 2, configureSort: "-porchlamp" };
  s = reduce(s, { type: "SET_PARAMS", resetTabSlices: true, payload: { tab: "ranked", page: 1, sort: "rank" } });
  assert.equal(s.notRankedPage, 1);
  assert.equal(s.configurePage, 1);
  assert.equal(s.configureSort, "rank");
  assert.equal("resetTabSlices" in s, false, "resetTabSlices does not leak onto state");
});

test("copy.maintainerBadgeSpec and maintainerBadgeMarkdown: derives coverage-band ceiling when tier_ceiling is omitted and escapes parentheses in URLs", () => {
  const rowWithoutCeiling = badgeRow({
    owner: "org",
    name: "repo(v2)",
    porchlamp: 84.5,
    tier: "A",
    coverage: 0.85,
    tier_ceiling: undefined,
    snapshot_id: "2026-09-01\r\n",
  });
  const spec = copy.maintainerBadgeSpec(rowWithoutCeiling);
  assert.ok(spec, "Coverage band A repo with tier A qualifies for Tier A (capped) badge even when tier_ceiling is omitted");
  assert.equal(spec.id, "sweet-spot-tier-a-capped", "derives effective ceiling A from coverage band A when tier_ceiling is undefined");
  assert.ok(spec.alt.includes("Snapshot 2026-09-01"), "strips CR/LF from snapshot_id");
  const md = copy.maintainerBadgeMarkdown(rowWithoutCeiling, SITE_BASE);
  assert.ok(!md.includes("(v2))"), "parentheses inside repo URL are percent-encoded so Markdown link syntax never breaks");
  assert.ok(md.includes("repo%28v2%29"), "escapes '(' and ')' in Markdown link target");
});

test("drawer.renderDrawer: scopes persona lens to tabs that expose persona selector and preserves maintainer badge on persona-gated repos", () => {
  const repoRow = badgeRow({
    owner: "org",
    name: "fast-lib",
    persona_gates: { first_timer: "G_FIRST_TIMER" },
  });
  const baseState = {
    ...defaultState(),
    persona: "first_timer",
    repo: "org/fast-lib",
    repoDetailLoading: false,
    evidenceLoading: false,
    repoDetailByKey: { "org/fast-lib": repoRow },
    evidenceByRepo: { "org/fast-lib": [] },
  };
  // On Ranked with first_timer, persona_gates.first_timer gates the repo for first_timer,
  // but its balanced Tier S maintainer badge must still be rendered (U-4).
  const rankedDrawer = renderDrawer({ ...baseState, tab: "ranked" }, { siteBaseUrl: SITE_BASE });
  assert.ok(rankedDrawer.includes("gate-banner"), "Ranked drawer applies first_timer persona gate");
  assert.ok(rankedDrawer.includes('data-badge-markdown="true"'), "persona-gated drawer still renders the balanced README maintainer badge");

  // On Matrix (where the persona selector is hidden), the drawer uses the balanced lens.
  const matrixDrawer = renderDrawer({ ...baseState, tab: "matrix" }, { siteBaseUrl: SITE_BASE });
  assert.ok(!matrixDrawer.includes("gate-banner"), "Matrix drawer uses balanced lens instead of hidden persona filter");
});

test("scatter.renderScatterSvg and matrix.renderMatrixShell: scales SVG marker radii with viewBox zoom and offers Clear filters on filtered-empty Matrix", () => {
  const points = [
    { owner: "a", name: "one", quadrant: "Sweet Spot", friction: 50, leverage: 50, stars_audited: 1000 },
  ];
  const unzoomedSvg = renderScatterSvg(points, { viewBox: "0 0 100 100" });
  const zoomedSvg = renderScatterSvg(points, { viewBox: "40 40 20 20" });
  const unzoomedPath = unzoomedSvg.match(/<path d="M 0 -([0-9.]+)/);
  const zoomedPath = zoomedSvg.match(/<path d="M 0 -([0-9.]+)/);
  assert.ok(unzoomedPath && zoomedPath);
  assert.ok(
    Math.abs(parseFloat(zoomedPath[1]) - parseFloat(unzoomedPath[1]) * 0.2) < 1e-6,
    "5x zoomed SVG scales marker domain radius by 0.2x so screen pixel size stays constant"
  );

  const emptyFilteredMatrixState = {
    ...defaultState(),
    tab: "matrix",
    ecosystem: "maven",
    matrix: { points, excluded_count: 0, axis_coverage: {} },
  };
  assert.equal(matrixShellShape(emptyFilteredMatrixState), "empty:maven:");
  const emptyMatrixHtml = renderMatrixShell(emptyFilteredMatrixState);
  assert.ok(emptyMatrixHtml.includes('data-clear-filters="true"'), "filtered-empty Matrix renders Clear filters button");
});

test("error banners across views include retry buttons", () => {
  const err = { endpoint: "/v1/porchlamp/test", status: 503, detail: "unavailable" };
  const lbHtml = leaderboardRenderShell({ ...defaultState(), leaderboardError: err }, { isMobile: false });
  assert.ok(lbHtml.includes('data-leaderboard-retry="true"'));

  const nrHtml = notRankedRenderShell({ ...defaultState(), notRankedError: err });
  assert.ok(nrHtml.includes('data-notranked-retry="true"'));

  const mxHtml = renderMatrixShell({ ...defaultState(), matrixError: err });
  assert.ok(mxHtml.includes('data-matrix-retry="true"'));

  const mdHtml = renderMethodologyShell({ ...defaultState(), methodologyError: err });
  assert.ok(mdHtml.includes('data-methodology-retry="true"'));
});

test("black-hole card: formats response_kind into human prose, and prints no share or Wilson bound the record lacks", () => {
  const card = blackHoleCard({
    ...FLAT_BLACK_HOLE_DETAIL,
    black_hole_evidence: {
      ...FLAT_BLACK_HOLE_DETAIL.black_hole_evidence,
      unanswered_share: null,
      wilson_lower_bound: null,
      counted_prs: [
        { number: 1, created_at: "2026-04-01T00:00:00Z", answered_within_14d: false, response_kind: "none" },
        { number: 2, created_at: "2026-04-02T00:00:00Z", answered_within_14d: true, response_kind: "review_or_comment" },
        { number: 3, created_at: "2026-04-03T00:00:00Z", answered_within_14d: true, response_kind: "closed_by_maintainer" },
        { number: 4, created_at: "2026-04-04T00:00:00Z", answered_within_14d: true, response_kind: "merged" },
        { number: 5, created_at: "2026-04-05T00:00:00Z", answered_within_14d: true, response_kind: "none" },
      ],
    },
  });
  const html = textOf(card);
  assert.ok(html.includes("12 of 15 external non-bot PRs"), "the counts stand without a percentage the record does not hold");
  assert.ok(!html.includes("95% Wilson lower confidence bound:"), "the view does not work out a Wilson bound itself");
  assert.ok(html.includes("No response within 14d"));
  assert.ok(html.includes("Answered (review or comment)"));
  assert.ok(html.includes("Answered (closed by maintainer)"));
  assert.ok(html.includes("Answered (merged)"));
  assert.ok(!html.includes("Answered (review_or_comment)"));
  assert.ok(!html.includes("Answered (closed_by_maintainer)"));
  assert.ok(!html.includes("Answered (none)"));
  assert.ok(card.includes("black-hole-pr-scroll"));
});

test("methodology: the parameters table keeps seven columns with the readable name in the row header, and Why Porchlamp renders a 3-column kicker grid", () => {
  const html = methodologyShell(SNAPSHOT_DOC, METHODOLOGY_DOC, REPORT_EXAMPLE);
  for (const kicker of copy.WHY_KICKERS) {
    assert.ok(html.includes(escapeHtml(kicker)), `Why Porchlamp includes kicker ${kicker}`);
  }
  for (const p of METHODOLOGY_DOC.parameters) {
    assert.ok(
      html.includes(`<span class="param-label">${escapeHtml(copy.paramName(p.input_id))}</span><code>${escapeHtml(p.input_id)}</code>`),
      `the row header for ${p.input_id} carries its readable name above the identifier`
    );
  }
  // An eighth column made the table scroll beside the rail at 1200-1279 px
  // (MEASURED 2026-10-15): the views.css stacking note sizes it for seven.
  const table = html.slice(html.indexOf("spec-table-parameters"));
  const headRow = table.slice(table.indexOf("<thead>"), table.indexOf("</thead>"));
  assert.equal((headRow.match(/<th scope="col"/g) || []).length, 7, "the parameters table has seven columns");
  assert.ok(headRow.includes(escapeHtml(copy.METHODOLOGY_COL_PARAMETER)));
  assert.ok(html.includes("persona-cell-bar"), "personas table includes visual weight bars");
});

test("matrix: renderMatrixBody includes Quadrant breakdown & top repositories spotlight cards", () => {
  const html = renderMatrixBody(matrixState(40));
  assert.ok(html.includes(escapeHtml(copy.MATRIX_SPOTLIGHT_HEADING)));
  assert.ok(html.includes('data-matrix-spotlight-key="o/r0"'));
  for (const guidance of Object.values(copy.MATRIX_QUADRANT_GUIDANCE)) {
    assert.ok(html.includes(escapeHtml(guidance)));
  }
  // The quadrant buttons toggle (aria-pressed); the subtitle must not promise to "isolate".
  assert.ok(!/isolate/i.test(copy.MATRIX_SPOTLIGHT_SUBTITLE));
  // D-MAT-1: friction rises with WEAK governance; strong governance never adds friction.
  assert.ok(!/strict governance/i.test(copy.MATRIX_QUADRANT_GUIDANCE.Crucible));
});

test("matrix: a spotlight row shows a coverage-capped tier as '(capped)' without nesting a tab stop in its button", () => {
  const state = matrixState(4);
  // Real matrix points carry coverage but no tier_ceiling: the ceiling is derived (0.65 -> B).
  state.matrix.points = state.matrix.points.map((p) => {
    const { tier_ceiling: _dropped, ...rest } = p;
    return { ...rest, coverage: 0.65, tier: "B" };
  });
  const html = renderMatrixBody(state);
  const row = html.slice(html.indexOf('data-matrix-spotlight-key="o/r0"'));
  const button = row.slice(0, row.indexOf("</button>"));
  assert.ok(button.includes(escapeHtml(copy.tierLabel("B", "B"))), "the capped tier reads like the leaderboard's");
  assert.ok(!button.includes("tabindex"), "nothing inside the row's <button> is a second tab stop");
  assert.ok(button.includes("70.00"), "scores use the two decimals every other screen uses");
});

test("compare: preset and suggestion copy never asserts a per-snapshot fact (rank, quadrant, gate, popularity)", () => {
  const claims = /top-ranked|\bleaders?\b|sweet spot|crucible|nursery|graveyard|black.?hole|\bgated\b|\brated\b|popular/i;
  for (const preset of copy.COMPARE_PRESETS) {
    assert.ok(!claims.test(preset.label), `preset label makes no data claim: ${preset.label}`);
    assert.ok(!claims.test(preset.description), `preset description makes no data claim: ${preset.description}`);
    assert.ok(preset.repos.length >= 2 && preset.repos.length <= 4, "a preset loads a comparable 2-4 repositories");
  }
  assert.ok(!claims.test(copy.COMPARE_SUGGESTIONS_HEADING));
});

test("compare: empty and partial states render quick-start presets and suggestions, and a subset verdict names the repo it leaves out", () => {
  const emptyHtml = renderCompareShell({ ...defaultState(), tab: "compare", compareRepos: [] });
  assert.ok(emptyHtml.includes('data-compare-preset='));
  assert.ok(emptyHtml.includes('data-compare-quick-add='));
  assert.ok(!emptyHtml.includes('data-compare-add='), "quick-add buttons do not collide with search dropdown [data-compare-add]");

  const methodologyParams = [
    { input_id: "ttfhr_hours", pillar: "responsiveness", value_domain: { kind: "numeric", higher_is_better: false } },
    { input_id: "dr90_decision_rate", pillar: "responsiveness", value_domain: { kind: "numeric", higher_is_better: true } },
    { input_id: "mr_first_rate", pillar: "responsiveness", value_domain: { kind: "numeric", higher_is_better: true } },
  ];
  const evidenceByRepo = {
    "PyO3/pyo3": [
      { param_id: "ttfhr_hours", measured: true, value: 4.2 },
      { param_id: "dr90_decision_rate", measured: true, value: 0.91 },
      { param_id: "mr_first_rate", measured: true, value: 0.5 },
    ],
    "tokio-rs/tokio": [
      { param_id: "ttfhr_hours", measured: true, value: 8.5 },
      { param_id: "dr90_decision_rate", measured: true, value: 0.84 },
      { param_id: "mr_first_rate", measured: false, value: null, unmeasured_reason: "insufficient_n" },
    ],
    "astral-sh/uv": [
      { param_id: "ttfhr_hours", measured: false, value: null, unmeasured_reason: "gated" },
      { param_id: "dr90_decision_rate", measured: false, value: null, unmeasured_reason: "gated" },
      { param_id: "mr_first_rate", measured: false, value: null, unmeasured_reason: "gated" },
    ],
  };
  const keys = ["PyO3/pyo3", "tokio-rs/tokio", "astral-sh/uv"];
  const v = computeVerdict(methodologyParams, evidenceByRepo, keys, "balanced");
  assert.equal(v.measuredCount, 0);
  assert.ok(v.subsetText.includes("PyO3/pyo3 leads"), `subsetText should name PyO3/pyo3: ${v.subsetText}`);
  assert.ok(!/\brated\b/i.test(v.subsetText), "the subset is 'repositories with measured evidence', not 'rated' ones");
  assert.equal(v.effectiveWins["PyO3/pyo3"], 2);
  assert.equal(v.effectiveWins["tokio-rs/tokio"], 0);
  assert.equal(v.effectiveWins["astral-sh/uv"], null);
  assert.deepEqual(v.excludedRepoKeys, ["astral-sh/uv"]);
  assert.deepEqual(v.subsetNotMeasured, ["mr_first_rate"]);

  // Rendered: the subset result and the excluded repo, never ALSO "cannot be
  // computed", and "Not compared" lists only what the subset skipped.
  const row = (owner, name) => ({ kind: "rated", row: { ...FLAT_RATED_DETAIL, owner, name }, evidence: evidenceByRepo[`${owner}/${name}`] });
  const html = renderCompareTable({
    persona: "balanced",
    compareRepos: keys,
    compareDataByRepo: { "PyO3/pyo3": row("PyO3", "pyo3"), "tokio-rs/tokio": row("tokio-rs", "tokio"), "astral-sh/uv": row("astral-sh", "uv") },
    methodology: { ...METHODOLOGY_DOC, parameters: methodologyParams },
  });
  const verdict = html.slice(html.indexOf('class="compare-verdict"'), html.indexOf("compare-cards-section"));
  assert.ok(verdict.includes(escapeHtml(v.subsetText)));
  assert.ok(verdict.includes(escapeHtml(copy.compareExcludedReposText(["astral-sh/uv"]))));
  assert.ok(!verdict.includes(escapeHtml(copy.compareVerdictNoDataText())), "no contradictory 'cannot be computed' line");
  assert.ok(verdict.includes(escapeHtml(copy.compareUnmeasuredNote([copy.paramName("mr_first_rate")]))));
});



// ---- E2: links to the static report and methodology pages (S2) ---------------------------------

import { renderShell, fillFromReport } from "../views/leaderboard.js";

test("leaderboard: one visible findings line under the header links to the static report with a real href from the site root", () => {
  // The numbers come from report.json (fillFromReport); the verifier reads them back by their hooks.
  const html = renderShell({ ...defaultState(), report: REPORT_EXAMPLE, leaderboardLoading: true, leaderboard: null });
  assert.ok(html.includes(fillFromReport(copy.FINDINGS_LINE, REPORT_EXAMPLE)), "the line is on the page");
  assert.match(html, /<a href="\/black-holes\/"[^>]*>Read the findings<\/a>/);
  assert.ok(
    html.indexOf("data-onboarding-banner") < html.indexOf("data-findings-line"),
    "the line sits under the onboarding banner"
  );
  assert.ok(html.indexOf("data-findings-line") < html.indexOf("data-primary-tab") || !html.includes("data-primary-tab"));
});

test("footer: one methodology link, the report and the data, real links from the site root", () => {
  const footer = renderFooter({ kind: "ok", snapshot: { snapshot_id: "2026-10-17", benchmark_version: "2.0.0", constants_hash: "a".repeat(64), manifest_root: "b".repeat(64) } });
  assert.deepEqual([...footer.matchAll(/<a href="([^"]*)"( data-route-tab="[^"]*")?>([^<]*)<\/a>/g)].map((m) => [m[1], m[3]]), [
    ["/methodology/", "Methodology"],
    ["/black-holes/", "Report"],
    ["https://github.com/pctablet505/porchlamp", "Data on GitHub"],
  ]);
  assert.ok(footer.includes('<a href="/methodology/" data-route-tab="methodology">'), "the app opens the methodology in place");
  for (const [id, value] of [["footer.snapshot_id", "2026-10-17"], ["footer.benchmark_version", "2.0.0"], ["footer.constants_hash", "a".repeat(12)], ["footer.manifest_root", "b".repeat(12)]]) {
    assert.ok(footer.includes(`<span data-v="${id}">${value}</span>`), id);
  }
  assert.match(renderFooter({ kind: "loading" }), /<span data-footer-snapshot="true">Loading…<\/span>/);
  const failed = renderFooter({ kind: "error", endpoint: "/v1/porchlamp/snapshot", status: 500 });
  assert.ok(failed.includes('data-footer-retry="true">') && !failed.includes("data-v="), "an error offers the retry and hooks nothing");
});

// ------------------------------------------------ E3: project pages (rows link to them; the badge targets them)
import { rowCells, renderCard, handleRepoLinkClick, repoLinkHtml } from "../components/table.js";
import { normalizeQuery, searchDestination } from "../state.js";

const E3_ROW = { owner: "FreeCAD", name: "FreeCAD", ecosystem: "systems", rank: 37, porchlamp: 70.1, tier: "B", tier_ceiling: "A", coverage: 0.9, quadrant: "Sweet Spot" };

/** A click as the DOM delivers it: `target` answers `closest` for the link it is on (or none), and the two calls are recorded. */
function clickOn(onLink, init = {}) {
  const calls = [];
  const evt = {
    button: 0, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false,
    target: { closest: (selector) => (onLink && selector === "a.repo-link" ? {} : null) },
    preventDefault: () => calls.push("preventDefault"),
    stopPropagation: () => calls.push("stopPropagation"),
    ...init,
  };
  return { evt, calls };
}

test("E3: a ranked row and a card render the repository name as a real link to the lower-case project page", () => {
  const repo = rowCells(E3_ROW, {}).cells.find((cell) => cell.key === "repository").html;
  assert.match(repo, /^<span class="repo-line"><span class="repo-line-name"><a class="repo-link"[^>]* href="\/projects\/freecad\/freecad\/">/, "the cell's name line starts with a real link");
  assert.ok(repo.includes('<span class="repo-owner">FreeCAD/</span><span class="repo-name">FreeCAD</span></a>'), "the real spelling is shown");
  assert.ok(repo.includes('<span class="eco-chip">C and C++</span>'), "the ecosystem chip is still there, outside the link");
  assert.ok(renderCard(E3_ROW, {}).includes('href="/projects/freecad/freecad/"'), "the card links too");
  assert.ok(!repo.includes("style="), "the link carries no inline style");
  const css = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "styles", "components.css"), "utf8");
  assert.match(css, /\.repo-link \{\s*color: inherit;\s*text-decoration: none;\s*\}/, "components.css keeps the link looking like the row did");
  assert.equal(repoLinkHtml({ ...E3_ROW, owner: "a<b" }).includes("<b"), false, "a name is escaped");
  assert.equal(projectPath("Ggml-Org", "Llama.CPP"), "/projects/ggml-org/llama.cpp/");
});

test("a row's and a card's ecosystem chip print the plain name, never the engine's code", () => {
  for (const [code, name] of copy.ECOSYSTEMS) {
    const row = { ...E3_ROW, ecosystem: code };
    const chip = `<span class="eco-chip">${escapeHtml(name)}</span>`;
    assert.ok(rowCells(row, {}).cells.find((cell) => cell.key === "repository").html.includes(chip), code);
    assert.ok(renderCard(row, {}).includes(chip), code);
  }
  assert.equal(copy.ecosystemName("cran"), "cran", "a code the list does not have is printed as it is");
});

test("E3: a plain left click on the link is kept from navigating (the drawer opens), a modified click follows the link", () => {
  const plain = clickOn(true);
  handleRepoLinkClick(plain.evt);
  assert.deepEqual(plain.calls, ["preventDefault"], "a plain click prevents the navigation and goes on to the drawer");
  for (const modifier of ["ctrlKey", "metaKey", "shiftKey", "altKey"]) {
    const modified = clickOn(true, { [modifier]: true });
    handleRepoLinkClick(modified.evt);
    assert.deepEqual(modified.calls, ["stopPropagation"], `${modifier}: the link is followed and the drawer stays shut`);
  }
  const middle = clickOn(true, { button: 1 });
  handleRepoLinkClick(middle.evt);
  assert.deepEqual(middle.calls, ["stopPropagation"], "a non-primary button follows the link");
  const elsewhere = clickOn(false);
  handleRepoLinkClick(elsewhere.evt);
  assert.deepEqual(elsewhere.calls, [], "a click anywhere else on the row is untouched");
});

test("E3: createRowElement and createCardElement listen for the link click", () => {
  const seen = [];
  const doc = {
    createElement(tag) {
      return {
        tagName: tag.toUpperCase(), className: "", classList: { add() {} }, dataset: {}, children: [],
        setAttribute() {}, appendChild() {},
        addEventListener(type, handler) { seen.push([tag, type, handler]); },
      };
    },
  };
  createRowElement(doc, E3_ROW, {});
  createCardElement(doc, E3_ROW, {});
  const clicks = seen.filter(([, type]) => type === "click");
  assert.deepEqual(clicks.map(([tag]) => tag), ["tr", "div"]);
  for (const [, , handler] of clicks) assert.equal(handler, handleRepoLinkClick);
});

test("E3: the maintainer badge links to the project's page, and an old ?repo= link is not what a new badge carries", () => {
  const row = badgeRow({ owner: "Riverline", name: "HttpKit" });
  const md = copy.maintainerBadgeMarkdown(row, SITE_BASE);
  assert.ok(md.includes(`](${SITE_BASE}projects/riverline/httpkit/ "`), md);
  assert.ok(!md.includes("?repo="), md);
  assert.ok(md.includes(`(${SITE_BASE}badges/`), "the image is unchanged");
});

test("the sitemap index names both child sitemaps, and the exporter writes both", () => {
  const webRoot = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
  const index = readFileSync(path.join(webRoot, "sitemap.xml"), "utf8");
  const children = [...index.matchAll(/<sitemap>\s*<loc>https:\/\/porchlamp\.org\/([^<]+)<\/loc>/g)].map((m) => m[1]);
  assert.deepEqual(children, ["sitemap-pages.xml", "sitemap-projects.xml"]);
  for (const child of children) assert.ok(!existsSync(path.join(webRoot, child)), `${child} is written by the exporter, not shipped by hand`);
});

test("search: what a person types or pastes reads as the text to look for, a GitHub address, case, end spaces and trailing slashes dropped", () => {
  assert.equal(normalizeQuery("  https://github.com/Pallets/Flask/ "), "pallets/flask");
  assert.equal(normalizeQuery("www.github.com/Keras-Team/keras"), "keras-team/keras");
  assert.equal(normalizeQuery("github.com/keras-team/keras//"), "keras-team/keras");
  assert.equal(normalizeQuery("Keras-Team/Keras/"), "keras-team/keras");
  assert.equal(normalizeQuery("kera"), "kera");
  assert.equal(normalizeQuery("   "), "");
  assert.equal(normalizeQuery(null), "");
  assert.equal(normalizeQuery("gitlab.com/a/b"), "gitlab.com/a/b", "only GitHub's address is dropped");
});

test("search: the filter reads the typed or pasted text as normalizeQuery does, and ?q= holds that one spelling", () => {
  // typing, Enter and leaving the box all dispatch SET_PARAMS: a pasted address filters as its owner/name
  const typed = reduce(defaultState(), { type: "SET_PARAMS", payload: { query: " https://github.com/Keras-Team/Keras/ ", page: 1 } });
  assert.equal(typed.query, "keras-team/keras");
  assert.equal(serializeLocation(typed), "/?q=keras-team%2Fkeras");
  // an address someone wrote by hand, or a page without the app sent, reads the same
  assert.equal(parseLocation("/", "?q=Keras-Team/Keras/").query, "keras-team/keras");
  assert.equal(parseLocation("/not-ranked/", "?q=https%3A%2F%2Fgithub.com%2FOrg-Stale-0001%2FRepo1").query, "org-stale-0001/repo1");
  // and finds the row, whatever its case
  const rows = [{ owner: "Keras-Team", name: "Keras", ecosystem: "pypi" }, { owner: "other", name: "keras-x", ecosystem: "pypi" }];
  assert.deepEqual(nrState.filterNotRankedRows(rows, "", typed.query).map((r) => `${r.owner}/${r.name}`), ["Keras-Team/Keras"]);
  assert.equal(reduce(defaultState(), { type: "SET_PARAMS", payload: { query: "kera" } }).query, "kera", "a partial name is kept as typed");
});

test("search: Enter on an owner/name opens its project (a ranked one's panel, a not-ranked one's row, an unknown one's panel); other text stays the filter", async () => {
  const asked = [];
  const held = {
    "keras-team/keras": { owner: "keras-team", name: "keras", rated: true },
    "freecad/freecad": { owner: "FreeCAD", name: "FreeCAD", rated: true },
    "org-stale-0001/repo1": { owner: "Org-stale-0001", name: "Repo1", rated: false },
    "broken/record": { owner: "broken", name: "record", rated: null },
  };
  const lookup = async (owner, name) => {
    asked.push(`${owner}/${name}`);
    if (owner === "unsure") return undefined;
    return held[`${owner}/${name}`] || null;
  };
  const at = (address, extra = {}) => ({ ...defaultState(), ...parseLocation(address, ""), ...extra });
  // an exact ranked project, in any spelling normalizeQuery accepts: its panel, in its real spelling
  for (const text of ["https://github.com/keras-team/keras", "Keras-Team/Keras/", " www.github.com/keras-team/keras "]) {
    assert.deepEqual(await searchDestination(text, at("/"), lookup), { kind: "panel", owner: "keras-team", name: "keras" }, text);
  }
  assert.deepEqual(await searchDestination("freecad/freecad", at("/rust/"), lookup), { kind: "panel", owner: "FreeCAD", name: "FreeCAD" });
  // an exact not-ranked project: the not-ranked list filtered to its row, its ecosystem filter dropped, its lens kept
  assert.deepEqual(await searchDestination("ORG-STALE-0001/repo1", at("/rust/"), lookup), { kind: "address", href: "/not-ranked/?q=org-stale-0001%2Frepo1" });
  assert.deepEqual(await searchDestination("org-stale-0001/repo1", at("/career/"), lookup),
    { kind: "address", href: "/not-ranked/?persona=career_capital&q=org-stale-0001%2Frepo1" });
  // a name the snapshot does not hold, or cannot be told: its panel, which says so
  assert.deepEqual(await searchDestination("nobody/nothing", at("/"), lookup), { kind: "panel", owner: "nobody", name: "nothing" });
  assert.deepEqual(await searchDestination("unsure/name", at("/"), lookup), { kind: "panel", owner: "unsure", name: "name" });
  assert.deepEqual(await searchDestination("broken/record", at("/"), lookup), { kind: "panel", owner: "broken", name: "record" }, "a record that failed to load: the panel says so");
  // text that is not an owner/name: the filter the list already applied, or the rankings filtered by it
  asked.length = 0;
  assert.deepEqual(await searchDestination("kera", at("/"), lookup), { kind: "filter" });
  assert.deepEqual(await searchDestination("kera", at("/not-ranked/"), lookup), { kind: "filter" });
  assert.deepEqual(await searchDestination("Kera", at("/methodology/"), lookup), { kind: "address", href: "/?q=kera" });
  assert.deepEqual(await searchDestination("https://github.com/keras-team", at("/"), lookup), { kind: "filter" }, "an owner's address is not a project");
  assert.deepEqual(asked, [], "a filter looks nothing up");
  // app.js answers the header's Enter this way, and opens or moves to what it says
  const app = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "app.js"), "utf8");
  const submit = app.slice(app.indexOf('header.addEventListener("submit"'), app.indexOf("async function lookUpProject"));
  assert.match(submit, /await searchDestination\(form\.querySelector\("\[data-search-input\]"\)\.value, store\.getState\(\), lookUpProject\)/);
  assert.match(submit, /if \(where\.kind === "panel"\) openRepo\(where\.owner, where\.name\);\s*else if \(where\.kind === "address"\) navigateTo\(where\.href\);/);
  assert.match(submit, /if \(submit !== searchSubmits\) return;/, "an Enter overtaken by a newer one is dropped");
});

test("methodology: the check line names a project's address and the header's search, and links no check page", () => {
  const html = methodologyShell(SNAPSHOT_DOC);
  const line = html.match(/<p class="snapshot-check">(.*?)<\/p>/)[1];
  assert.equal(decodeAttr(line), copy.METHODOLOGY_CHECK_LINE);
  assert.ok(copy.METHODOLOGY_CHECK_LINE.startsWith("To check a project, open "), copy.METHODOLOGY_CHECK_LINE);
  assert.ok(copy.METHODOLOGY_CHECK_LINE.endsWith("/projects/<owner>/<name>/ (lower case), or type its name or GitHub address into the search at the top of the page."));
  assert.ok(!html.includes('href="/check/"'));
});

// ------------------------------------------------ live-site fixes: no test harness, honest links, wording

import { lastPageWhenPastEnd } from "../views/leaderboard.js";
import { renderHoverCard } from "../views/matrix.js";

const WEB_ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");

/** Every relative module a file imports (`import ... from "./x.js"`, `import "./x.js"`), as absolute paths. */
function relativeImports(file) {
  const source = readFileSync(file, "utf8");
  const found = [];
  for (const m of source.matchAll(/(?:^|\n)\s*(?:import|export)\s[^;]*?from\s+["'](\.[^"']+)["']|(?:^|\n)\s*import\s+["'](\.[^"']+)["']/g)) {
    found.push(path.resolve(path.dirname(file), m[1] || m[2]));
  }
  return found;
}

/** Every file reachable from `entry` through relative imports. */
function moduleGraph(entry) {
  const seen = new Set();
  const queue = [entry];
  while (queue.length) {
    const file = queue.pop();
    if (seen.has(file)) continue;
    seen.add(file);
    queue.push(...relativeImports(file));
  }
  return seen;
}

test("the page's module graph holds no synthetic-row generator and no perf harness entry", () => {
  const graph = moduleGraph(path.join(WEB_ROOT, "app.js"));
  assert.ok(graph.size > 10, `the walk must reach the real modules (reached ${graph.size})`);
  assert.ok(graph.has(path.join(WEB_ROOT, "views", "leaderboard.js")), "the walk reaches views/leaderboard.js");
  for (const file of graph) {
    assert.ok(!file.includes(`${path.sep}tests${path.sep}`), `${file} is a test module imported by the page`);
    assert.doesNotMatch(readFileSync(file, "utf8"), /synthesizeRows/, `${file} uses the synthetic-row generator`);
  }
  assert.ok(!existsSync(path.join(WEB_ROOT, "perf-fixtures.js")), "the generator no longer sits beside the shipped modules");
  assert.ok(existsSync(path.join(WEB_ROOT, "tests", "synthetic-rows.js")), "the generator lives with the tests");
});

test("boot() reads no ?perf or ?perfmatrix parameter, so those addresses show the real board", () => {
  const app = readFileSync(path.join(WEB_ROOT, "app.js"), "utf8");
  const bootBody = app.slice(app.indexOf("export function boot("));
  assert.ok(bootBody.length > 1000, "boot() was found");
  assert.doesNotMatch(bootBody, /get\(\s*["']perf/, "boot() must not branch on a perf parameter");
  assert.doesNotMatch(app, /perfmatrix/);
  assert.doesNotMatch(app, /\[perf\]/);
  // The real boot path is the one that loads the leaderboard.
  assert.match(bootBody, /if \(initialTab === "ranked" \|\| initialTab === "notranked"\) loadLeaderboard\(\);/);
});

const LENS_ROW_WITHOUT_BALANCED_RANK = { ...E3_ROW, rank: 4, balanced_rank: null, rank_delta: null };
const LENS_ROW_WITH_BALANCED_RANK = { ...E3_ROW, rank: 4, balanced_rank: 9, rank_delta: 5 };

test("repoLinkHtml: under a lens, a row with no balanced rank is plain text; elsewhere the project page is linked", () => {
  const plain = repoLinkHtml(LENS_ROW_WITHOUT_BALANCED_RANK, true);
  assert.ok(!plain.includes("<a"), plain);
  assert.ok(!plain.includes("href"), plain);
  assert.ok(plain.includes('<span class="repo-owner">FreeCAD/</span><span class="repo-name">FreeCAD</span>'), "the name still shows");
  assert.ok(repoLinkHtml(LENS_ROW_WITH_BALANCED_RANK, true).includes('href="/projects/freecad/freecad/"'), "balanced rank: linked");
  assert.ok(repoLinkHtml(LENS_ROW_WITHOUT_BALANCED_RANK, false).includes('href="/projects/freecad/freecad/"'), "balanced lens: linked");
  assert.ok(repoLinkHtml({ ...E3_ROW, balanced_rank: undefined }, true).indexOf("<a") === -1, "an absent balanced rank is not a rank");
  assert.ok(repoLinkHtml({ ...E3_ROW, balanced_rank: 0 }, true).includes("href"), "a balanced rank of 0 is still a rank");
});

test("rows and cards on a lens link only the projects that have a page", () => {
  const lens = { showPersonaDelta: true };
  const cell = (row, opts) => rowCells(row, opts).cells.find((c) => c.key === "repository").html;
  assert.ok(!cell(LENS_ROW_WITHOUT_BALANCED_RANK, lens).includes("<a"));
  assert.ok(cell(LENS_ROW_WITH_BALANCED_RANK, lens).includes("<a"));
  assert.ok(cell(LENS_ROW_WITHOUT_BALANCED_RANK, { showPersonaDelta: false }).includes("<a"));
  assert.ok(!renderCard(LENS_ROW_WITHOUT_BALANCED_RANK, lens).includes('href="/projects/'));
  assert.ok(renderCard(LENS_ROW_WITH_BALANCED_RANK, lens).includes('href="/projects/freecad/freecad/"'));
});

test("lastPageWhenPastEnd: a page past the end of a non-empty result goes to the last page; nothing else moves", () => {
  const empty = (total) => ({ rows: [], total });
  assert.equal(lastPageWhenPastEnd(empty(95), 99999, 20), 5);
  assert.equal(lastPageWhenPastEnd(empty(100), 6, 20), 5, "an exact multiple of the page size");
  assert.equal(lastPageWhenPastEnd(empty(95), 5, 20), null, "the last page itself, empty for another reason, stays put");
  assert.equal(lastPageWhenPastEnd(empty(0), 3, 20), null, "nothing matched: the empty state is right");
  assert.equal(lastPageWhenPastEnd({ rows: [{ owner: "a", name: "b" }], total: 95 }, 2, 20), null, "a page with rows stays");
  assert.equal(lastPageWhenPastEnd(null, 2, 20), null);
  assert.equal(lastPageWhenPastEnd({ rows: [], total: undefined }, 2, 20), null);
});

/** The text of every string literal in JavaScript source, with comments skipped, and object keys (a literal between
 * `{` or `,` and `:`) skipped too: a key is looked up, never shown. */
function stringLiterals(source) {
  const literals = [];
  let i = 0;
  while (i < source.length) {
    const c = source[i];
    if (c === "/" && source[i + 1] === "/") {
      while (i < source.length && source[i] !== "\n") i++;
    } else if (c === "/" && source[i + 1] === "*") {
      i = source.indexOf("*/", i + 2) + 2;
      if (i < 2) break;
    } else if (c === '"' || c === "'" || c === "`") {
      let j = i + 1;
      let text = "";
      while (j < source.length && source[j] !== c) {
        if (source[j] === "\\") { text += source[j + 1]; j += 2; continue; }
        text += source[j++];
      }
      let before = i - 1;
      while (before >= 0 && /\s/.test(source[before])) before--;
      let after = j + 1;
      while (after < source.length && /\s/.test(source[after])) after++;
      const isObjectKey = (source[before] === "{" || source[before] === ",") && source[after] === ":";
      if (!isObjectKey) literals.push(text);
      i = j + 1;
    } else {
      i++;
    }
  }
  return literals;
}

const REGISTER_ID = /D-[A-Z]+-\d|RFC-\d/;

test("copy.js: no visible string holds a register id such as D-GATE-5 or RFC-0008", () => {
  assert.ok(REGISTER_ID.test(stringLiterals('const x = "Rule (D-GATE-5, RFC-0008)"; // D-GATE-1')[0]), "the scan finds an id in a string");
  assert.deepEqual(stringLiterals('// "D-GATE-5"\nconst k = 1; /* RFC-0008 */'), [], "comments are skipped");
  assert.deepEqual(stringLiterals('const m = { "D-GATE-1": "Software project",\n  "D-GATE-2": "Licensed" };'),
    ["Software project", "Licensed"], "object keys are skipped, their values are not");
  assert.deepEqual(stringLiterals('const s = ok ? "D-GATE-5 passed" : "D-GATE-5 failed";'),
    ["D-GATE-5 passed", "D-GATE-5 failed"], "both branches of a ternary are values");
  const literals = stringLiterals(readFileSync(path.join(WEB_ROOT, "copy.js"), "utf8"));
  assert.ok(literals.length > 200, `the scan must cover the file (${literals.length} literals)`);
  assert.deepEqual(literals.filter((text) => REGISTER_ID.test(text)), []);
});

test("black-hole stat: the 'no response' sentence claims only what was counted", () => {
  const measured = textOf(blackHoleCard(FLAT_BLACK_HOLE_DETAIL));
  assert.ok(measured.includes("12 of 15 (80.0%) external non-bot PRs had no maintainer response, merge, or close within 14 days"));
  const notMeasured = blackHoleCard({
    ...FLAT_BLACK_HOLE_DETAIL,
    black_hole_evidence: { ...FLAT_BLACK_HOLE_DETAIL.black_hole_evidence, rule_inputs_measured: false },
  });
  const stat = textOf(notMeasured.match(/<p class="black-hole-stat">(.*?)<\/p>/)[1]);
  assert.ok(stat.startsWith("12 of 15 (80.0%) external non-bot PRs had no maintainer review, comment or merge within 14 days"), stat);
  assert.doesNotMatch(stat, /or close|response, merge/, "a close by a maintainer was not counted, so the sentence must not say it was");
  assert.ok(stat.includes("a close by a maintainer was not counted"), stat);
});

test("matrix hover card: the tier shows '(capped)' like the table and the drawer, from the coverage ceiling", () => {
  const point = { owner: "a", name: "b", porchlamp: 80.8, coverage: 0.85, tier: "A", friction: 10, leverage: 70, quadrant: "Sweet Spot" };
  assert.ok(renderHoverCard(point).includes("A (capped)"), "coverage 0.85 puts the ceiling at A");
  assert.ok(!renderHoverCard({ ...point, tier: "B" }).includes("capped"), "a tier below the ceiling is not capped");
  assert.ok(!renderHoverCard({ ...point, coverage: 0.9 }).includes("capped"), "full coverage (>= 0.90) has no ceiling");
  assert.ok(renderHoverCard({ ...point, coverage: 0.7, tier: "B" }).includes("B (capped)"), "coverage 0.7 puts the ceiling at B");
  assert.ok(!renderHoverCard({ ...point, tier_ceiling: null }).includes("capped"), "an explicit ceiling of none wins");
  // The card is rebuilt on every hover or focus move and ignores the pointer: it must hold no tab stop.
  assert.doesNotMatch(renderHoverCard(point), /tabindex/, "a capped tier in the card is not a tab stop");
  assert.doesNotMatch(renderHoverCard({ ...point, tier: undefined }), /tabindex/, "a missing tier in the card is not a tab stop");
  assert.ok(renderHoverCard({ ...point, tier: undefined }).includes(copy.MISSING_VALUE), "a missing tier still shows the missing-value mark");
  // the card agrees with the table row for the same project
  const tableTier = rowCells({ ...E3_ROW, coverage: 0.85, tier: "A", tier_ceiling: "A" }, {}).cells.find((c) => c.key === "tier").html;
  assert.ok(tableTier.includes("A (capped)"));
});

// ------------------------------------------------ routes: every view at one real address (web/app/routes.js)
import { renderShell as renderNotFoundShell } from "../views/notfound.js";

//: The plan's route table (overhaul web audit §2.5, with the owner's rulings of 2026-10-17): each view's address
//: and the query fields it keeps, in the order the address writes them.
const EXPECTED_ROUTES = {
  ranked: ["/", ["persona", "eco", "q", "sort", "page", "page_size"]],
  notranked: ["/not-ranked/", ["persona", "eco", "q", "page", "page_size"]],
  matrix: ["/matrix/", ["eco", "q"]],
  compare: ["/compare/", ["persona", "cmp"]],
  configure: ["/weights/", ["eco", "q", "sort", "page", "page_size", "w"]],
  methodology: ["/methodology/", []],
  report: ["/black-holes/", []],
  project: ["/projects/{owner}/{name}/", ["persona"]],
  notfound: [null, []],
};
//: The lists of the leaderboard at their own addresses (list-views DESIGN, build contract B), in table order.
const LIST_ECOSYSTEM_SLUGS = ["rust", "python", "javascript", "go", "java", "c-cpp"];
const EXPECTED_LIST_ROUTES = {
  rankings: ["/rankings/{page}/", []],
  ...Object.fromEntries(LIST_ECOSYSTEM_SLUGS.map((slug) => [slug, [`/${slug}/`, ["persona", "sort", "page", "page_size", "q"]]])),
  ...Object.fromEntries(LIST_ECOSYSTEM_SLUGS.map((slug) => [`${slug}-first-timers`, [`/${slug}/first-timers/`, ["sort", "page", "page_size", "q"]]])),
  ...Object.fromEntries(["first-timers", "career", "senior-systems", "ai-assisted"].map((slug) => [slug, [`/${slug}/`, ["sort", "page", "page_size", "q"]]])),
  "fastest-replies": ["/fastest-replies/", []],
  ...Object.fromEntries(LIST_ECOSYSTEM_SLUGS.map((slug) => [`${slug}-fastest-replies`, [`/${slug}/fastest-replies/`, []]])),
};
//: The not-ranked projects by reason, one list each (list-views build contract, PR 2, B): slug and the bare reason it fixes.
const REASON_LIST_SLUGS = [
  ["stale", "stale"], ["not-software", "non_software"], ["archived", "archived"], ["unlicensed", "unlicensed"],
  ["unanswered", "black_hole"], ["fork", "fork"], ["mirror", "mirror"], ["insufficient-evidence", "insufficient_evidence"],
  ["no-contribution-workflow", "no_external_workflow"], ["off-platform", "off_platform_routed"],
];
//: For each query field: the state field it fills, a value that is not the default as it stands in an address,
//: and that value as state.
const FIELD_SAMPLES = {
  persona: ["persona", "first_timer", "first_timer"],
  eco: ["ecosystem", "pypi", "pypi"],
  q: ["query", "flask", "flask"],
  sort: ["sort", "-porchlamp", "-porchlamp"],
  page: ["page", "3", 3],
  page_size: ["pageSize", "100", 100],
  w: ["weights", "0.3,0.2,0.2,0.2,0.1", { responsiveness: 0.3, criticality: 0.2, governance: 0.2, rigor: 0.2, accessibility: 0.1 }],
  cmp: ["compareRepos", "a/b,c/d", ["a/b", "c/d"]],
};
/** An address of the route itself: a project's for the project route, a missing page's for "notfound", a page of
 *  the ranking's third. */
function addressOf(route) {
  if (route.id === "project") return "/projects/acme/widget/";
  if (route.id === "notfound") return "/no/such/page/";
  return route.path.replace("{page}", "3");
}

test("routes: the table holds every view at its address, with the query fields it keeps", () => {
  const views = ROUTES.filter((route) => !route.view);
  assert.deepEqual(Object.fromEntries(views.map((route) => [route.id, [route.path, [...route.query]]])), EXPECTED_ROUTES);
  assert.deepEqual(views.filter((route) => route.shell).map((route) => route.id), ["ranked", "notranked", "matrix", "compare", "configure", "methodology", "report"]);
  const lists = ROUTES.filter((route) => route.view === "ranked");
  assert.deepEqual(Object.fromEntries(lists.map((route) => [route.id, [route.path, [...route.query]]])), EXPECTED_LIST_ROUTES);
  assert.ok(lists.every((route) => route.shell), "each list is the ranked view, drawn in place");
  // the lists by reason come right after the whole not-ranked list, in the contract's order, each fixing its reason
  const reasons = ROUTES.filter((route) => route.view === "notranked");
  assert.deepEqual(ROUTES.filter((route) => route.view && route.view !== "ranked"), reasons, "no other view has lists");
  assert.equal(ROUTES.indexOf(reasons[0]), ROUTES.findIndex((route) => route.id === "notranked") + 1);
  assert.deepEqual(reasons.map((route) => [route.id, route.path, [...route.query], route.shell, { ...route.fixed }]), REASON_LIST_SLUGS.map(([slug, reason]) => [
    `notranked-${slug}`, `/not-ranked/${slug}/{page}/`, [], true, { reason, persona: "balanced", eco: null, q: null, page_size: 200 },
  ]));
});

test("routes: every route round-trips through parseLocation and serializeLocation", () => {
  // On an ecosystem's list the first-timer lens is a path of its own (/rust/first-timers/): its sample is another lens.
  const sample = (route, key) => (route.view && key === "persona" ? ["persona", "career_capital", "career_capital"] : FIELD_SAMPLES[key]);
  // At `/` a lens or an ecosystem is a list's own path (the list-routes tests): the root round-trips the rest.
  const keysOf = (route) => (route.id === "ranked" ? route.query.filter((key) => key !== "persona" && key !== "eco") : route.query);
  for (const route of ROUTES) {
    const qs = new URLSearchParams(keysOf(route).map((key) => [key, sample(route, key)[1]]));
    const search = keysOf(route).length ? `?${qs}` : "";
    const parsed = parseLocation(addressOf(route), search);
    assert.equal(parsed.tab, route.id === "project" ? "ranked" : route.view || route.id, route.id);
    if (route.id === "project") assert.equal(parsed.repo, "acme/widget");
    if (route.id === "notfound") assert.equal(parsed.missingPath, "/no/such/page/");
    for (const key of keysOf(route)) assert.deepEqual(parsed[sample(route, key)[0]], sample(route, key)[2], `${route.id} reads ${key}`);

    const address = serializeLocation({ ...defaultState(), ...parsed });
    const back = new URL(address, SITE_BASE);
    assert.equal(back.pathname, addressOf(route), `${route.id} writes its own path`);
    assert.deepEqual([...back.searchParams.keys()], keysOf(route), `${route.id} writes exactly its fields, in order`);
    for (const key of keysOf(route)) assert.equal(back.searchParams.get(key), sample(route, key)[1], `${route.id} writes ${key}`);
    assert.equal(serializeLocation({ ...defaultState(), ...parseLocation(back.pathname, back.search) }), address, `${route.id} is stable`);
  }
});

test("routes: a query field the view does not read is neither read from its address nor written to it", () => {
  const every = Object.keys(FIELD_SAMPLES);
  const full = Object.fromEntries(every.map((key) => [FIELD_SAMPLES[key][0], FIELD_SAMPLES[key][2]]));
  for (const route of ROUTES.filter((r) => r.id !== "notfound" && !r.view)) {
    const foreign = every.filter((key) => !route.query.includes(key));
    const parsed = parseLocation(addressOf(route), `?${new URLSearchParams(foreign.map((key) => [key, FIELD_SAMPLES[key][1]]))}`);
    for (const key of foreign) assert.equal(parsed[FIELD_SAMPLES[key][0]], undefined, `${route.id} ignores ${key}`);
    const state = { ...defaultState(), ...full, tab: route.id === "project" ? "ranked" : route.id, repo: route.id === "project" ? "acme/widget" : null };
    const written = new URL(serializeLocation(state), SITE_BASE).searchParams;
    for (const key of foreign) assert.ok(!written.has(key), `${route.id} does not write ${key}`);
  }
});

test("routes: an address no route claims, or a project part that is not a GitHub name, is the not-found view", () => {
  for (const path of ["/projects/acme/", "/projects/acme/widget/extra/", "/projects/%2e%2e/widget/", "/projects/a%2Fb/c/", "/PROJECTS/acme/widget/", "/rankings/0/", "/rankings/03/", "/rankings/x/", "/rust/nope/", "/nope"]) {
    assert.equal(matchRoute(path).route.id, "notfound", path);
  }
  assert.equal(matchRoute("/matrix").route.id, "matrix", "the closing slash is optional, as a static host's redirect makes it");
  assert.equal(matchRoute("/matrix/index.html").route.id, "matrix", "a directory's index.html is the directory");
  assert.equal(matchRoute("/index.html").route.id, "ranked");
  assert.deepEqual(matchRoute("/projects/Acme/Widget.js/"), { route: routeById("project"), owner: "Acme", name: "Widget.js" });
  assert.equal(projectPath("Acme", "Widget.js"), "/projects/acme/widget.js/");
});

//: The old query links (overhaul web audit §2.5, brief 2026-10-17): the old address, the address it lands on, and how:
//: "load" (location.replace) or "panel over <view address>" (opened in place). Each carries a field its view reads
//: and, where there is one, a field it does not.
const OLD_LINKS = [
  ["/?tab=ranked&q=flask&page=2", "/?q=flask&page=2", "load"],
  ["/?tab=notranked&persona=first_timer&eco=npm&page=2&page_size=50&w=0.3,0.2,0.2,0.2,0.1", "/not-ranked/?persona=first_timer&eco=npm&page=2&page_size=50", "load"],
  ["/?tab=matrix&eco=npm&q=widget&persona=first_timer", "/matrix/?eco=npm&q=widget", "load"],
  ["/?tab=compare&cmp=a/b,c/d&persona=career_capital&eco=npm", "/compare/?persona=career_capital&cmp=a%2Fb%2Cc%2Fd", "load"],
  ["/?tab=configure&w=0.3,0.2,0.2,0.2,0.1&eco=npm&persona=first_timer", "/weights/?eco=npm&w=0.3%2C0.2%2C0.2%2C0.2%2C0.1", "load"],
  ["/?tab=methodology&persona=first_timer", "/methodology/", "load"],
  ["/?tab=methodology#gates", "/methodology/#gates", "load"],
  ["/#methodology", "/methodology/", "load"],
  ["/?tab=something-else&q=flask", "/?q=flask", "load"],
  ["/index.html?tab=matrix", "/matrix/", "load"],
  ["/?repo=WordPress/gutenberg", "/projects/wordpress/gutenberg/", "panel over /"],
  ["/?repo=https%3A%2F%2Fgithub.com%2FWordPress%2Fgutenberg.git", "/projects/wordpress/gutenberg/", "panel over /"],
  ["/?tab=matrix&eco=npm&repo=ggml-org%2Fllama.cpp", "/projects/ggml-org/llama.cpp/", "panel over /matrix/?eco=npm"],
  ["/?tab=notranked&persona=first_timer&repo=a/b", "/projects/a/b/?persona=first_timer", "panel over /not-ranked/?persona=first_timer"],
  ["/projects/WordPress/Gutenberg/?persona=first_timer", "/projects/wordpress/gutenberg/?persona=first_timer", "load"],
];

test("routes: every old link lands on its view's address, keeping every other field that view reads", () => {
  for (const [old, expected, how] of OLD_LINKS) {
    const url = new URL(old, SITE_BASE);
    const target = redirectTarget(url.pathname, url.search, url.hash);
    assert.ok(target, `${old} goes somewhere`);
    assert.equal(target.href, expected, old);
    assert.equal(target.load ? "load" : `panel over ${target.under}`, how, old);
    // the fields: kept where the view it lands on reads them, left out where it does not, never an old link again
    const landed = new URL(target.load ? target.href : target.under, SITE_BASE);
    const view = matchRoute(landed.pathname).route;
    for (const [key, value] of url.searchParams) {
      if (key === "tab" || key === "repo") {
        assert.ok(!new URL(target.href, SITE_BASE).searchParams.has(key), `${old}: no ${key}= is left`);
      } else if (view.query.includes(key)) {
        assert.equal(landed.searchParams.get(key), value, `${old} keeps ${key}`);
      } else {
        assert.ok(!landed.searchParams.has(key), `${old} leaves out ${key}, which the ${view.id} view does not read`);
      }
    }
  }
  const panel = redirectTarget("/", "?repo=WordPress%2FGutenberg");
  assert.equal(panel.repo, "WordPress/Gutenberg", "the panel keeps the project as the link spelled it");
  // A view's own address, and a search, stay where they are (?q= is the site's search address; ?eco= and ?persona=
  // alone load their list's path: "routes: an old form of a list's address loads its path").
  for (const stays of ["/", "/?q=flask", "/?q=flask&sort=-porchlamp&page=2", "/matrix/?eco=npm", "/projects/acme/widget/", "/no/such/page/"]) {
    const url = new URL(stays, SITE_BASE);
    assert.equal(redirectTarget(url.pathname, url.search, url.hash), null, stays);
  }
  // every old tab= value named a view the table still holds
  for (const tab of VALID_TABS) assert.ok(routeById(tab)?.shell, tab);
});

test("routes: every primary nav link is a view's address, opened in place, with the shown view current", () => {
  for (const tab of ["ranked", "notranked", "matrix", "compare", "configure", "methodology", "report", "notfound"]) {
    const state = { ...defaultState(), tab, persona: "first_timer", ecosystem: "npm", query: "widget", missingPath: tab === "notfound" ? "/x/" : null };
    const links = [...renderNav(navEntryFor(tab), state).matchAll(/<a class="primary-tab" href="([^"]*)"( data-route-tab="([^"]*)")?( aria-current="page")?>/g)];
    assert.equal(links.length, 6, `${tab}: six links`);
    for (const [, rawHref, , linkTab] of links) {
      const href = rawHref.replace(/&amp;/g, "&");
      const { route } = matchRoute(new URL(href, SITE_BASE).pathname);
      assert.ok(route.shell, `${tab}: ${href} is the address of a view the app draws`);
      assert.equal(linkTab, route.view || route.id, `${tab}: ${href} opens its own view in place`);
      assert.equal(href, hrefFor(state, route.view || route.id), `${tab}: ${href} is where a click goes from here`);
    }
    assert.equal(links[5][1], "/black-holes/", `${tab}: the report is the last entry`);
    const current = links.filter((link) => link[4]).map((link) => link[3]);
    assert.deepEqual(current, tab === "notfound" ? [] : [tab === "notranked" ? "ranked" : tab], `${tab}: the current entry`);
  }
  assert.match(
    renderNav("report"),
    /<a class="primary-tab" href="\/black-holes\/" data-route-tab="report" aria-current="page">Report<\/a><\/div>$/,
    "the report's page marks it, on a page drawn without the reader's state too"
  );
  // The Leaderboard's Ranked and Not ranked links are addresses too, carrying the reader's lens and filters.
  const lb = leaderboardRenderShell({ ...defaultState(), persona: "career_capital", ecosystem: "npm", leaderboardLoading: true });
  assert.ok(lb.includes('<a class="tab" href="/javascript/?persona=career_capital&amp;page_size=20" data-route-tab="ranked" aria-current="page">'), lb);
  assert.ok(lb.includes('<a class="tab" href="/not-ranked/?persona=career_capital&amp;eco=npm" data-route-tab="notranked">'), lb);
  assert.ok(!/role="tab"|aria-selected/.test(lb), "no tab roles left on links");
});

test("header: the brand links home, the search is a form to the rankings, and the app's selects show where the view reads them", () => {
  const snapshot = { snapshot_id: "2026-10-17", benchmark_version: "2.0.0-rc1" };
  const page = renderHeader({ snapshot });
  assert.ok(page.startsWith('<a class="brand-link" href="/" aria-label="Porchlamp">'), page.slice(0, 80));
  assert.ok(page.includes('<form class="header-filter-group" data-header-search-group="true" role="search" action="/" method="get"><input class="control" type="search" name="q"'), "the search works without the app");
  assert.ok(page.includes('title="2026-10-17 · benchmark 2.0.0-rc1"><span data-v="header.snapshot_id">2026-10-17</span>'), "the chip names the snapshot");
  assert.ok(page.includes('data-theme-toggle="true" aria-label="Toggle light/dark theme"'), "the toggle (theme-init.js answers it)");
  assert.ok(!page.includes("<select"), "a page the app does not draw has no selects");
  for (const view of ["ranked", "notranked", "matrix", "compare", "configure", "methodology", "notfound"]) {
    const header = renderAppHeader(view, snapshot);
    const shown = headerControlsFor(view);
    const hidden = (group) => new RegExp(`data-header-${group}-group="true"[^>]* hidden>`).test(header);
    assert.equal(hidden("persona"), !shown.persona, `${view}: persona`);
    assert.equal(hidden("eco"), !shown.ecosystem, `${view}: ecosystem`);
    assert.equal(hidden("search"), !shown.search, `${view}: search`);
  }
  assert.deepEqual(["ranked", "notranked", "matrix", "configure", "methodology", "notfound"].map((v) => headerControlsFor(v).search), Array(6).fill(true));
  assert.equal(headerControlsFor("compare").search, false, "Compare's own search box adds a repository");
});

test("routes: a view's link brings that view back with its own page and sort", () => {
  let s = reduce(defaultState(), { type: "SET_PARAMS", payload: { sort: "-porchlamp", page: 3 } });
  s = reduce(s, { type: "SET_TAB", tab: "notranked" });
  assert.equal(hrefFor(s, "ranked"), "/?sort=-porchlamp&page=3");
  assert.equal(hrefFor(s, "matrix"), "/matrix/");
  assert.equal(hrefFor({ ...s, repo: "a/b" }, "compare"), "/compare/", "a view's link never carries the open panel");
});

test("routes: every route sets its own document title, and the pages the export serves carry the same", () => {
  const titles = new Map();
  const VIEW_ROUTES = ROUTES.filter((route) => !route.view);
  for (const route of VIEW_ROUTES) {
    const state = route.id === "project"
      ? { ...defaultState(), repo: "acme/widget" }
      : { ...defaultState(), tab: route.id, missingPath: route.id === "notfound" ? "/x/" : null };
    const title = titleForState(state);
    assert.ok(title.includes("Porchlamp"), `${route.id}: ${title}`);
    assert.equal(title, routeTitle(route.id, { owner: "acme", name: "widget" }), route.id);
    titles.set(route.id, title);
  }
  assert.equal(new Set(titles.values()).size, VIEW_ROUTES.length, "no two views share a title");
  const loaded = { ...defaultState(), repo: "acme/widget", repoDetailByKey: { "acme/widget": { owner: "Acme", name: "Widget" } } };
  assert.equal(titleForState(loaded), routeTitle("project", { owner: "Acme", name: "Widget" }), "the record's spelling once it has loaded");
  const report = { ...defaultState(), tab: "report", report: { title: "Open-source black holes (October 2026) | Porchlamp" } };
  assert.equal(titleForState(report), report.report.title, "the report's own title, naming its month, once it has loaded");
  const webRoot = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
  const titleOf = (file) => readFileSync(path.join(webRoot, file), "utf8").match(/<title>([^<]*)<\/title>/)[1];
  assert.equal(titleOf("index.html"), titles.get("ranked"), "index.html is the leaderboard's page");
  assert.equal(titleOf("404.html"), titles.get("notfound"), "404.html is the not-found page");
  const app = readFileSync(path.join(webRoot, "app.js"), "utf8");
  assert.match(
    app,
    /const title = titleForState\(state\);\s*if \(!prerendered && doc\.title !== title\) doc\.title = title;/,
    "app.js sets it on every render, once it has taken over a page the export drew (that page carries its own)"
  );
});

test("routes: a panel's history entry brings back the view under it; loaded directly it opens over the leaderboard", () => {
  const over = { ...defaultState(), tab: "matrix", ecosystem: "npm", repo: "Acme/Widget" };
  assert.equal(serializeLocation(over), "/projects/acme/widget/");
  const entry = historyEntryFor(over);
  assert.deepEqual(entry, { under: "/matrix/?eco=npm", repo: "Acme/Widget" });
  const back = stateFromLocation("/projects/acme/widget/", "", entry);
  assert.deepEqual([back.tab, back.ecosystem, back.repo], ["matrix", "npm", "Acme/Widget"], "the view under it, and the project's spelling");
  const direct = stateFromLocation("/projects/acme/widget/", "", null);
  assert.deepEqual([direct.tab, direct.repo], ["ranked", "acme/widget"]);
  const elsewhere = stateFromLocation("/projects/other/thing/", "", entry);
  assert.equal(elsewhere.repo, "other/thing", "a spelling kept for another project is not used");
  assert.equal(historyEntryFor({ ...over, repo: null }), null, "no panel, nothing to keep");
  // the lens of the list under a panel stays in the panel's address; the matrix has no lens
  assert.equal(serializeLocation({ ...defaultState(), persona: "first_timer", repo: "a/b" }), "/projects/a/b/?persona=first_timer");
  assert.equal(serializeLocation({ ...defaultState(), tab: "matrix", persona: "first_timer", repo: "a/b" }), "/projects/a/b/");
});

test("routes: the side panel links a ranked project's own page, and only a ranked project's", () => {
  const ranked = renderDrawer(stateWithRepoDetail(FLAT_RATED_DETAIL));
  assert.ok(ranked.includes(`<a class="drawer-page-link" href="/projects/riverline/httpkit/" data-drawer-page="true">${escapeHtml(copy.DRAWER_PAGE_LABEL)}</a>`), ranked);
  const identity = ranked.match(/<div class="project-identity"[\s\S]*?<\/div>/)[0];
  assert.ok(identity.includes("data-drawer-page"), "under the project's name, not among the buttons");
  for (const detail of [FLAT_GATED_DETAIL, FLAT_UNRATED_DETAIL]) {
    assert.ok(!renderDrawer(stateWithRepoDetail(detail)).includes("data-drawer-page"), `${detail.owner}: no page, no link`);
  }
});

test("routes: the not-found view says what was asked for and searches the ranked projects without the app", () => {
  const html = renderNotFoundShell({ ...defaultState(), tab: "notfound", missingPath: "/projects/<x>/" });
  assert.ok(html.includes(`<h1 id="not-found-heading" class="view-heading">${copy.NOT_FOUND_HEADING}</h1>`));
  assert.ok(html.includes(escapeHtml(copy.notFoundText("/projects/<x>/"))), "the address, escaped");
  assert.match(html, /<form class="not-found-search" action="\/" method="get" role="search">/);
  assert.match(html, /<input class="control" id="not-found-q" type="search" name="q"/);
  assert.match(html, /<a href="\/" data-route-tab="ranked">/);
});

test("routes: on a ranked project's static page, Back and Forward between the app's entries load the address", async () => {
  // The page shares its address with the project's side panel, so the app's history entries can land on it
  // (a reload of an open panel, or its Full page link): the page loads the address so the app draws it.
  const listeners = [];
  let reloads = 0;
  const saved = { addEventListener: globalThis.addEventListener, location: globalThis.location };
  globalThis.addEventListener = (type, handler) => listeners.push([type, handler]);
  globalThis.location = { reload: () => { reloads += 1; } };
  try {
    await import("../back-forward.js");
  } finally {
    globalThis.addEventListener = saved.addEventListener;
    globalThis.location = saved.location;
  }
  assert.deepEqual(listeners.map(([type]) => type), ["popstate"], "it listens for Back and Forward only");
  globalThis.location = { reload: () => { reloads += 1; } };
  try {
    listeners[0][1]({ state: { under: "/" } });
  } finally {
    globalThis.location = saved.location;
  }
  assert.equal(reloads, 1, "a Back or Forward loads the address it moved to");
  const serve = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "porchlamp", "serve");
  const reportPages = readFileSync(path.join(serve, "report_pages.py"), "utf8");
  assert.match(reportPages, /"project": \("\/theme-init\.js", "\/back-forward\.js"\)/, "a project page's scripts are the theme's and this one");
  const projectPages = readFileSync(path.join(serve, "project_pages.py"), "utf8");
  assert.match(projectPages, /scripts=PAGE_SCRIPTS\["project"\]\[1:\]/, "every project page loads its scripts after the theme's");
});

test("routes: the panel's README badge preview loads from the site root, since the panel is open at a project's address", () => {
  const html = renderMaintainerBadge(badgeRow({}), SITE_BASE);
  const src = html.match(/<img class="maintainer-badge-preview" src="([^"]*)"/)[1];
  assert.match(src, /^\/badges\/[a-z0-9-]+\.svg$/, src);
  assert.ok(existsSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", src.slice(1))), `${src} is a file of the site`);
});

// --- report (views/report.js)
// The data is the example report.json of the report-view contract (the file the export writes beside snapshot.json).
//: The export's report.json for the published snapshot 2026-10-17 (tests/test_report_pages.py pins it to report_json).
const REPORT_EXAMPLE_PATH = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "tests", "fixtures", "report", "report.json");
const REPORT_EXAMPLE = JSON.parse(readFileSync(REPORT_EXAMPLE_PATH, "utf8"));
const REPORT_DIGEST = "ff1d7a10" + "0".repeat(48) + "a6e1";
const reportCopy = () => structuredClone(REPORT_EXAMPLE);
const unescapeHtml = (t) => t.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
const REPORT_HOOK = /<span data-v="([^"]+)">([^<]*)<\/span>/g;
const reportHooks = (html) => [...html.matchAll(REPORT_HOOK)].map((m) => ({ id: m[1], text: unescapeHtml(m[2]) }));

/** What a hook id must print: a `values` entry, the digest option, or a field of a chart row. */
function reportExpected(report, id, digest) {
  if (id === "source_db_sha256") return digest;
  if (Object.prototype.hasOwnProperty.call(report.values, id)) return report.values[id];
  const m = /^(bands|ecosystems)\.(\d+)\.(label|name|judged|black_holes|share)$/.exec(id);
  if (!m) return undefined;
  const row = report[m[1]][Number(m[2])];
  const field = { label: "label", name: "name", judged: "judged_text", black_holes: "black_holes_text", share: "share_text" }[m[3]];
  return row ? row[field] : undefined;
}

/** The article's reading text: hooks removed, and nothing inside an aria-hidden element or a chart <caption>. */
function reportProse(html) {
  const out = [];
  const stack = []; // true where that open element hides its content
  let hiddenDepth = 0;
  for (const tok of html.replace(REPORT_HOOK, "").match(/<[^>]+>|[^<]+/g)) {
    if (tok[0] !== "<") {
      if (!hiddenDepth) out.push(unescapeHtml(tok));
    } else if (tok[1] === "/") {
      if (stack.pop()) hiddenDepth--;
    } else {
      // The captions are plain sentences Python filled, numbers inline: scripts/verify_static_export.py checks each whole.
      const hides = /aria-hidden="true"/.test(tok) || /^<caption[\s>]/.test(tok);
      stack.push(hides);
      if (hides) hiddenDepth++;
    }
  }
  return out.join(" ");
}

const REPORT_HOOK_COUNT = reportHooks(renderReport(REPORT_EXAMPLE, { sourceDbSha256: REPORT_DIGEST })).length;

test(`report: every data-v equals its report.values value or row field (${REPORT_HOOK_COUNT} hooks)`, () => {
  const html = renderReport(REPORT_EXAMPLE, { sourceDbSha256: REPORT_DIGEST });
  const hooks = reportHooks(html);
  assert.ok(hooks.length > 60, `${hooks.length} hooks`);
  for (const h of hooks) {
    const want = reportExpected(REPORT_EXAMPLE, h.id, REPORT_DIGEST);
    assert.notEqual(want, undefined, `${h.id} names a value`);
    assert.equal(h.text, String(want), `${h.id} of ${hooks.length} hooks`);
  }
});

test("report: no digit in the article's text outside a hook, but the template literals and aria-hidden or caption text", () => {
  const text = reportProse(renderReport(REPORT_EXAMPLE, { sourceDbSha256: REPORT_DIGEST }));
  assert.ok(text.length > 1500, "the scanned text is not empty");
  const stripped = text.replace(/1 in|CC BY 4\.0|SHA-256|100/g, "");
  assert.doesNotMatch(stripped, /\d/, stripped.match(/.{0,30}\d.{0,30}/)?.[0]);
});

test("report: the two chart captions are the report.json summaries", () => {
  const html = renderReport(REPORT_EXAMPLE);
  const captions = [...html.matchAll(/<caption class="sr-only">([^<]*)<\/caption>/g)].map((m) => unescapeHtml(m[1]));
  assert.deepEqual(captions, [REPORT_EXAMPLE.sentences.stars_chart_summary, REPORT_EXAMPLE.sentences.ecosystems_chart_summary]);
});

test("report: one h1 and no skipped heading level", () => {
  const html = renderReport(REPORT_EXAMPLE);
  const levels = [...html.matchAll(/<h([1-6])[\s>]/g)].map((m) => Number(m[1]));
  assert.equal(levels.filter((l) => l === 1).length, 1);
  assert.equal(levels[0], 1);
  levels.forEach((l, i) => { if (i) assert.ok(l <= levels[i - 1] + 1, `h${l} after h${levels[i - 1]}`); });
  assert.deepEqual(levels, [1, 2, 3, 3, 3, 3, 2, 2, 2, 2, 2, 2]);
});

test("report: the axis maximum follows the data (a planted share of 0.45 gives ticks up to 50%)", () => {
  assert.deepEqual(axisFor(REPORT_EXAMPLE.bands).ticks, [0, 10, 20, 30]);
  const planted = reportCopy();
  planted.bands[0].judged = 100;
  planted.bands[0].black_holes = 45;
  assert.deepEqual(axisFor(planted.bands).ticks, [0, 10, 20, 30, 40, 50]);
  const html = renderReport(planted);
  assert.ok(html.includes('<span class="axis-tick" style="left:100%">50%</span>'));
  assert.ok(!html.includes(">60%<"));
  assert.deepEqual(axisFor([{ judged: 10, black_holes: 3 }]).ticks, [0, 10, 20, 30], "an exact ten does not add a step");
});

test("report: the old ids are present, each once, and no # anchor links", () => {
  const html = renderReport(REPORT_EXAMPLE);
  for (const id of ["finding-share", "finding-stars", "finding-top100", "finding-correlation", "finding-prs", "key-findings", "stars", "ecosystems", "how-we-measured", "what-this-is-not", "download", "cite"]) {
    assert.equal(html.split(`id="${id}"`).length - 1, 1, id);
  }
  assert.ok(!/href="#/.test(html), "no # anchors");
});

test("report: fill throws on a template slot with no value, and so does a report whose values lack one", () => {
  assert.throws(() => fillTemplate("{a} and {b}", { a: "1" }), /no value for the slot \{b\}/);
  const broken = reportCopy();
  delete broken.values.share;
  assert.throws(() => renderReport(broken), /\{share\}/);
});

test("report: without a digest the hashes part is gone; with one it is a hook", () => {
  const without = renderReport(REPORT_EXAMPLE);
  assert.ok(!without.includes("SHA-256") && !without.includes('data-v="source_db_sha256"'));
  assert.ok(without.includes('data-v="constants_hash"') && without.includes("Data licence"), "the other two parts stay");
  const withDigest = renderReport(REPORT_EXAMPLE, { sourceDbSha256: REPORT_DIGEST });
  assert.ok(withDigest.includes(`<span data-v="source_db_sha256">${REPORT_DIGEST}</span>`));
  assert.ok(withDigest.includes("SHA-256"));
});

test("report: the stars note is left out when no judged project sits under the lowest band", () => {
  assert.match(renderReport(REPORT_EXAMPLE), /Not charted/);
  const planted = reportCopy();
  planted.sentences.stars_not_charted = null;
  const html = renderReport(planted);
  assert.ok(!html.includes("Not charted") && !html.includes('data-v="below_bands_judged"'));
  assert.ok(html.includes('class="report-caveat"'));
  const notes = (h) => h.split('class="report-note"').length - 1;
  assert.equal(notes(html), notes(renderReport(REPORT_EXAMPLE)) - 1, "no empty note paragraph is left");
});

test("report: the cite links the frozen copy, the download links the csv, each ecosystem row links its path", () => {
  const html = renderReport(REPORT_EXAMPLE);
  const u = REPORT_EXAMPLE.urls;
  assert.ok(html.includes(`<a href="${u.frozen}"><span data-v="frozen_url">`));
  assert.ok(html.includes(`<a href="${u.csv}"><span data-v="csv_name">`));
  REPORT_EXAMPLE.ecosystems.forEach((r, i) => {
    assert.ok(html.includes(`<a href="${r.path}"><span data-v="ecosystems.${i}.name">${r.name}</span></a>`), r.path);
  });
  assert.ok(!/class="bar-label"><a [^>]*><span data-v="bands\./.test(html), "a band name is not a link");
  assert.ok(html.includes(`<span data-v="bands.0.label">${REPORT_EXAMPLE.bands[0].label}</span> stars`));
});

test("report: the shell is loading, error, empty or the body, the body keyed by the digest", () => {
  const base = { report: null, reportLoading: false, reportError: null, sourceDbSha256: null };
  assert.equal(reportShellShape({ ...base, reportLoading: true }), "loading");
  assert.equal(reportShellShape({ ...base, reportError: { endpoint: "/report.json", status: 500 } }), "error");
  assert.equal(reportShellShape(base), "empty");
  assert.equal(reportShellShape({ ...base, report: REPORT_EXAMPLE }), "body:nodb");
  assert.equal(reportShellShape({ ...base, report: REPORT_EXAMPLE, sourceDbSha256: REPORT_DIGEST }), "body:db");
  assert.equal(reportShellShape({ ...base, report: REPORT_EXAMPLE, reportLoading: true }), "body:nodb", "a loaded report stays while it reloads");

  const loading = reportRenderShell({ ...base, reportLoading: true });
  assert.ok(loading.startsWith(`<h1 class="view-heading">${escapeHtml(copy.REPORT_H1)}</h1>`));
  assert.ok(loading.includes(`<div class="empty-state" aria-busy="true">${copy.REPORT_LOADING_TEXT}</div>`));
  const error = reportRenderShell({ ...base, reportError: { endpoint: "/report.json", status: 500, detail: "" } });
  assert.ok(error.includes(`<div class="error-banner" role="alert">${copy.reportErrorText("/report.json", 500, "")}`));
  assert.ok(error.includes(`<button type="button" class="icon-button" data-report-retry="true">${copy.RETRY_LABEL}</button>`));
  assert.equal((reportRenderShell(base).match(/<h1/g) || []).length, 1);
  assert.equal(reportRenderShell({ ...base, report: REPORT_EXAMPLE, sourceDbSha256: REPORT_DIGEST }), renderReport(REPORT_EXAMPLE, { sourceDbSha256: REPORT_DIGEST }));
});

// --- project (views/project.js)
// The data is three records of the export's shape ({detail, evidence, answer?, standings?, similar?, recordFile}),
// read through one constant so that only the files change when the export's own records replace them.
const PHONE_COLUMN_PX = 294; // the narrowest column the radar gets: the panel on a 375 px phone (measured in headless Chrome)
const PROJECT_FIXTURES = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "tests", "fixtures", "project");
const projectRecord = (stem) => JSON.parse(readFileSync(path.join(PROJECT_FIXTURES, `${stem}.json`), "utf8"));
const PROJECT_STEMS = ["vllm-project__vllm", "keras-team__keras", "3b1b__manim"];
const PROJECT_RECORDS = Object.fromEntries(PROJECT_STEMS.map((stem) => [stem, projectRecord(stem)]));
const projectHtml = (rec, mode, extra = {}) => renderProject(rec, { mode, recordFile: rec.recordFile, ...extra });

const PROJECT_HOOK = /<(span|p)\b[^>]*\bdata-v="([^"]+)"[^>]*>([^<]*)<\/\1>/g;
const projectHooks = (html) => [...html.matchAll(PROJECT_HOOK)].map((m) => ({ id: m[2], text: unescapeHtml(m[3]) }));
const VOID_TAGS = new Set(["img", "br", "hr", "input", "meta", "link", "circle", "line", "polygon", "polyline", "path"]);

/** The reading text of markup: hooks removed, nothing inside an aria-hidden element, tags dropped. */
function projectProse(html) {
  const out = [];
  const stack = [];
  let hidden = 0;
  for (const tok of html.replace(PROJECT_HOOK, " ").match(/<[^>]+>|[^<]+/g)) {
    if (tok[0] !== "<") {
      if (!hidden) out.push(unescapeHtml(tok));
    } else if (tok[1] === "/") {
      if (stack.pop()) hidden--;
    } else if (!tok.endsWith("/>") && !VOID_TAGS.has(/^<([a-z0-9]+)/i.exec(tok)[1])) {
      const hides = /aria-hidden="true"/.test(tok);
      stack.push(hides);
      if (hides) hidden++;
    }
  }
  return out.join(" ");
}

// What the view must print, worked out here from the record by its own rules (not read from the view's output).
const TEST_MONTHS = "January February March April May June July August September October November December".split(" ");
const groupDigits = (n) => new Intl.NumberFormat("en-US").format(n);
const dayText = (iso) => { const [y, m, d] = iso.slice(0, 10).split("-").map(Number); return `${d} ${TEST_MONTHS[m - 1]} ${y}`; };
const spanText = (a, b) => `${a.slice(0, 4) === b.slice(0, 4) ? dayText(a).replace(/ \d{4}$/, "") : dayText(a)} to ${dayText(b)}`;
const wholePercent = (f) => `${Math.round(f * 100)}%`;
const SHARE_PARAM_IDS = ["dr90_decision_rate", "mr_first_rate", "gr14_ghost_rate", "substantive_review_rate", "newcomer_conversion", "rr90_return_rate"];
const ENUM_PARAM_LABELS = {
  legal_ip: { DCO: "Sign-off on commits (DCO)", NONE: "No CLA or sign-off found", FOUNDATION_CLA: "Foundation CLA", CORP_CLA: "Company CLA" },
  ai_posture: { REGULATED: "Written AI policy", PROSCRIBED: "AI ban stated in a written policy", AD_HOC: "No written AI policy" },
  promotion_ladder: {
    VERIFIED: "An outside contributor became a member or collaborator",
    DOCUMENTED: "A written path to becoming a maintainer",
    AD_HOC: "No written path to becoming a maintainer",
    CLOSED: "Merged pull requests all came from one organisation",
  },
};

/** `printed * mul / div` to `places` decimals, half up, worked out on the printed decimal as a fraction (Python's
 *  `Decimal(repr(x))` rule), for a number of 0 or more. */
function printedHalfUp(printed, mul, div, places) {
  const [whole, frac = ""] = String(printed).split(".");
  const num = BigInt(whole + frac);
  const den = 10n ** BigInt(frac.length);
  const scaled = (2n * num * BigInt(mul) * 10n ** BigInt(places) + den * BigInt(div)) / (2n * den * BigInt(div));
  const text = scaled.toString().padStart(places + 1, "0");
  return places ? `${text.slice(0, -places)}.${text.slice(-places)}` : text;
}
const testHours = (h) => printedHalfUp(h, 1, 1, 1);
const testDays = (h) => String(Number(printedHalfUp(h, 1, 24, 1)));
const testShare = (f) => printedHalfUp(f, 100, 1, 1);

/** The value column of a parameter row, by DESIGN's table of units. */
function expectedParamValue(id, v) {
  if (id === "ttfhr_hours" || id === "ttm_p90_hours") return `${testHours(v)} hours (${testDays(v)} days)`;
  if (SHARE_PARAM_IDS.includes(id)) return `${testShare(v)}%`;
  if (id === "downloads_monthly") return v >= 1e6 ? `${(v / 1e6).toFixed(1)} million downloads a month` : `${groupDigits(v)} downloads a month`;
  if (["dependents_deduped", "elephant_factor", "newcomer_issue_supply"].includes(id)) return groupDigits(v);
  if (ENUM_PARAM_LABELS[id]) return ENUM_PARAM_LABELS[id][v];
  return String(Math.round(v * 10000) / 10000);
}

/** The reply time of the key facts: project_pages.reply_time_text for hours, with the hours after the days. */
function expectedReply(hours) {
  if (hours < 48) return `${Number(testHours(hours))} ${Number(testHours(hours)) === 1 ? "hour" : "hours"}`;
  return `${testDays(hours)} days (${testHours(hours)} hours)`;
}

/** Every hook id the view must print for a record, and the text of each. */
function expectedProjectHooks(rec, mode) {
  const d = rec.detail;
  const want = new Map();
  const add = (id, text) => want.set(id, text);
  if (mode === "page") add("detail.snapshot_date", dayText(d.snapshot_id));
  if (rec.answer) add("answer", rec.answer.join(" "));
  const ranked = d.eligible && d.rated;
  const param = (id) => (rec.evidence || []).find((r) => r.param_id === id);
  if (ranked) {
    add("detail.rank", groupDigits(d.rank));
    if (rec.standings) add("standings.balanced.total", groupDigits(rec.standings.balanced.total));
    add("detail.porchlamp", d.porchlamp.toFixed(2));
    add("detail.coverage", copy.coverageBadgeText(d.coverage));
    add("detail.leverage", d.leverage.toFixed(1));
    add("detail.friction", d.friction.toFixed(1));
    add("detail.leverage_coverage", wholePercent(d.leverage_coverage));
    add("detail.friction_coverage", wholePercent(d.friction_coverage));
    const reply = param("ttfhr_hours");
    const ghost = param("gr14_ghost_rate");
    if (reply && !reply.unmeasured_reason) add("facts.reply_time", expectedReply(reply.value));
    if (ghost && !ghost.unmeasured_reason) add("facts.no_reply_share", wholePercent(ghost.value));
    const first = rec.standings && rec.standings.first_timer;
    if (first && first.rank != null) {
      add("standings.first_timer.rank", groupDigits(first.rank));
      add("standings.first_timer.total", groupDigits(first.total));
    }
    const windowRow = [reply, ghost].find((r) => r && !r.unmeasured_reason && r.window_start);
    if (windowRow) add(`evidence.${windowRow.param_id}.window`, spanText(windowRow.window_start, windowRow.window_end));
    add("cite.year", d.snapshot_id.slice(0, 4));
  }
  for (const p of ["responsiveness", "criticality", "governance", "rigor", "accessibility"]) {
    if (!["responsiveness", "criticality", "governance", "rigor", "accessibility"].some((q) => d[`p_${q}`] != null)) break;
    if (d[`p_${p}`] != null) add(`detail.p_${p}`, d[`p_${p}`].toFixed(1));
    add(`detail.c_${p}`, wholePercent(d[`c_${p}`] || 0));
  }
  for (const r of rec.evidence || []) {
    if (r.unmeasured_reason) continue;
    if (r.value != null) add(`evidence.${r.param_id}.value`, expectedParamValue(r.param_id, r.value));
    if (r.score != null) add(`evidence.${r.param_id}.score`, r.score.toFixed(1));
    if (r.n) add(`evidence.${r.param_id}.n`, groupDigits(r.n));
    if (r.window_start && r.window_end) add(`evidence.${r.param_id}.window`, spanText(r.window_start, r.window_end));
  }
  if (d.eligible) {
    for (const lens of Object.keys(copy.PERSONA_NAMES)) {
      const c = (d.persona_composites || {})[lens];
      if ((d.persona_gates || {})[lens] || !c) continue;
      const s = rec.standings && rec.standings[lens];
      if (s && s.rank != null) {
        add(`standings.${lens}.rank`, groupDigits(s.rank));
        add(`standings.${lens}.total`, groupDigits(s.total));
      }
      add(`detail.persona_composites.${lens}.porchlamp`, c.porchlamp.toFixed(2));
    }
  }
  const ev = d.black_hole_evidence;
  if (ev && d.gate_reason === "black_hole") {
    add("bh.unanswered", String(ev.unanswered));
    add("bh.population_n", String(ev.population_n));
    add("bh.unanswered_share", testShare(ev.unanswered_share));
    add("bh.wilson_lower_bound", testShare(ev.wilson_lower_bound));
    add("bh.threshold_share", testShare(ev.threshold_share));
    add("bh.min_n", String(ev.min_n));
    add("bh.window", spanText(ev.window_start, ev.window_end));
    for (const [k, n] of Object.entries(ev.excluded)) {
      const counted = ev.rule_inputs_measured === true || !["draft", "self_closed"].includes(k);
      if (n > 0 && counted) add(`bh.excluded.${k}`, String(n));
    }
    ev.counted_prs.forEach((pr, i) => {
      add(`bh.counted_prs.${i}.number`, String(pr.number));
      if (pr.created_at) add(`bh.counted_prs.${i}.created_at`, dayText(pr.created_at));
    });
  }
  (rec.similar || []).forEach((s, i) => add(`similar.${i}.rank`, groupDigits(s.rank)));
  add("detail.snapshot_id", d.snapshot_id);
  if (d.raw_hash) add("detail.raw_hash", d.raw_hash);
  return want;
}

for (const mode of ["page", "panel"]) {
  for (const stem of PROJECT_STEMS) {
    test(`project: every data-v of ${stem} in ${mode} mode equals the record's value by the view's rules, and every expected hook is there`, () => {
      const rec = PROJECT_RECORDS[stem];
      const hooks = projectHooks(projectHtml(rec, mode));
      const want = expectedProjectHooks(rec, mode);
      assert.ok(hooks.length > (stem === "3b1b__manim" ? 20 : 60), `the scanned set is not empty (${hooks.length} hooks)`);
      for (const { id, text } of hooks) {
        assert.ok(want.has(id), `${id} is printed but the record gives it no value`);
        assert.equal(text, want.get(id), id);
      }
      assert.deepEqual([...new Set(hooks.map((h) => h.id))].sort(), [...want.keys()].sort());
    });
  }
}

test("project: the page and the panel carry the same hook ids, apart from the snapshot date", () => {
  for (const stem of PROJECT_STEMS) {
    const ids = (mode) => new Set(projectHooks(projectHtml(PROJECT_RECORDS[stem], mode)).map((h) => h.id));
    const page = ids("page");
    const panel = ids("panel");
    assert.ok(page.has("detail.snapshot_date") && !panel.has("detail.snapshot_date"), `${stem}: the lede is the page's alone`);
    page.delete("detail.snapshot_date");
    assert.deepEqual([...page].sort(), [...panel].sort(), stem);
  }
});

/** The strings of copy.js that carry a digit, and the identifiers of a record: the only digits the text may hold. */
function projectLiterals(rec) {
  const d = rec.detail;
  const fromCopy = [
    ...Object.keys(copy.PARAM_MEANINGS).map(copy.paramName), ...Object.values(copy.PARAM_MEANINGS), ...Object.values(copy.PROJECT_COLUMNS),
    ...Object.values(copy.KEY_FACT_LABELS), ...Object.values(copy.BLACK_HOLE_EXCLUDED_LABELS),
    copy.PROJECT_PILLARS_CAPTION, copy.BLACK_HOLE_RULE_TEXT, copy.BLACK_HOLE_EXCLUSIONS_TEXT, copy.BLACK_HOLE_RECHECK_TEXT,
    copy.BLACK_HOLE_STAT_PREDICATE, copy.BLACK_HOLE_STAT_PREDICATE_CLOSE_NOT_COUNTED, copy.BLACK_HOLE_RULE_INPUTS_NOT_MEASURED_TEXT,
    "95% Wilson lower confidence bound", "CC BY 4.0", copy.gateReasonText("black_hole"), copy.TIER_CAPPED_EXPLANATION,
    ...["No response within 14d", "Answered"], ...Object.values(copy.AI_POSTURE_LABELS),
  ];
  const identifiers = [
    `${d.owner}/${d.name}`, d.owner, d.name, rec.recordFile, `${copy.SITE_ADDRESS}${projectPath(d.owner, d.name)}`,
    ...(rec.similar || []).flatMap((s) => [`${s.owner}/${s.name}`]),
    ...(rec.evidence || []).flatMap((r) => [r.source, r.method].filter(Boolean)),
    d.black_hole_evidence && d.black_hole_evidence.source,
  ].filter(Boolean);
  return [...fromCopy, ...identifiers].sort((a, b) => b.length - a.length);
}

for (const mode of ["page", "panel"]) {
  test(`project: no digit in the ${mode} text outside a hook, apart from copy literals and identifiers`, () => {
    for (const stem of PROJECT_STEMS) {
      const rec = PROJECT_RECORDS[stem];
      let text = projectProse(projectHtml(rec, mode)).replace(/\s+/g, " ");
      assert.ok(text.length > 800, `${stem}: the scanned text is not empty`);
      for (const literal of projectLiterals(rec)) text = text.split(literal).join(" ");
      assert.doesNotMatch(text, /\d/, `${stem}: ${text.match(/.{0,40}\d.{0,40}/)?.[0]}`);
    }
  });
}

test("project: the page has one h1 and no skipped heading level, and no <details>; the panel has no h1", () => {
  for (const stem of PROJECT_STEMS) {
    const levels = (mode) => [...projectHtml(PROJECT_RECORDS[stem], mode).matchAll(/<h([1-6])[\s>]/g)].map((m) => Number(m[1]));
    const page = levels("page");
    assert.equal(page.filter((l) => l === 1).length, 1, `${stem}: one h1`);
    assert.equal(page[0], 1);
    page.forEach((l, i) => { if (i) assert.ok(l <= page[i - 1] + 1, `${stem}: h${l} after h${page[i - 1]}`); });
    const panel = levels("panel");
    assert.ok(!panel.includes(1), `${stem}: the panel draws no h1`);
    assert.equal(panel[0], 2, "the panel's title is an h2");
    panel.forEach((l, i) => { if (i) assert.ok(l <= panel[i - 1] + 1, `${stem}: panel h${l} after h${panel[i - 1]}`); });
    for (const mode of ["page", "panel"]) assert.ok(!/<details/.test(projectHtml(PROJECT_RECORDS[stem], mode)), `${stem}: no <details> in ${mode} mode`);
  }
  const panelHtml = projectHtml(PROJECT_RECORDS["keras-team__keras"], "panel");
  assert.ok(panelHtml.includes('<h2 id="drawer-title" class="project-title">'), "the dialog names itself by this title");
  assert.ok(projectHtml(PROJECT_RECORDS["keras-team__keras"], "page").includes('<h1 id="project-title" class="project-title">'));
});

test("project: the anchors the old page and panel had are present, and so are the new ones", () => {
  const ids = (html) => new Set([...html.matchAll(/ id="([^"]+)"/g)].map((m) => m[1]));
  for (const mode of ["page", "panel"]) {
    for (const stem of ["vllm-project__vllm", "keras-team__keras"]) {
      const found = ids(projectHtml(PROJECT_RECORDS[stem], mode));
      for (const id of ["answer", "rating", "pillars", "lenses", "similar", "evidence", "correction", "parameters", "parameters-responsiveness", "parameters-governance"]) {
        assert.ok(found.has(id), `${stem} ${mode}: #${id}`);
      }
    }
    const manim = ids(projectHtml(PROJECT_RECORDS["3b1b__manim"], mode));
    for (const id of ["black-hole", "parameters", "evidence", "correction"]) assert.ok(manim.has(id), `manim ${mode}: #${id}`);
    for (const id of ["answer", "rating", "pillars", "lenses", "similar"]) assert.ok(!manim.has(id), `manim ${mode}: no #${id} for a project that was never scored`);
  }
  const html = projectHtml(PROJECT_RECORDS["keras-team__keras"], "page");
  assert.equal([...html.matchAll(/ id="([^"]+)"/g)].length, ids(html).size, "no id is used twice");
  // The panel sits on the list's page: no other view may use one of these ids.
  const others = ["leaderboard", "notranked", "matrix", "compare", "configure", "methodology", "report"].map((v) => readFileSync(path.join(WEB_ROOT, "views", `${v}.js`), "utf8")).join("\n");
  assert.doesNotMatch(others, / id="(answer|rating|pillars|lenses|similar|evidence|correction|parameters|black-hole|key-facts)"/);
});

test("project: the radar is aria-hidden, its labels are 13 px or more where a phone's column is no wider than the viewBox, and a table says the same in text", () => {
  const html = projectHtml(PROJECT_RECORDS["keras-team__keras"], "page");
  const radar = html.match(/<div class="radar-chart-container"[^>]*>/)[0];
  assert.ok(radar.includes('aria-hidden="true"'), "the radar is hidden from assistive technology");
  assert.ok(!/role="img"/.test(html), "no role=img on a hidden box");
  const css = readFileSync(path.join(WEB_ROOT, "styles", "views.css"), "utf8");
  const label = css.match(/\.radar-label\s*\{[^}]*\}/)[0];
  assert.ok(Number(/font-size:\s*(\d+(?:\.\d+)?)px/.exec(label)[1]) >= 13, label);
  const viewBoxWidth = Number(/viewBox="0 0 (\d+) \d+"/.exec(html)[1]);
  assert.ok(viewBoxWidth <= PHONE_COLUMN_PX, `the viewBox (${viewBoxWidth}) is not wider than a phone's column (${PHONE_COLUMN_PX} px), so the labels do not shrink`);
  // The text alternative: a table with a caption and a row header per pillar, score and coverage hooked.
  const table = html.match(/<table class="project-table pillar-table">[\s\S]*?<\/table>/)[0];
  assert.ok(table.includes(`<caption>${escapeHtml(copy.PROJECT_PILLARS_CAPTION)}</caption>`));
  for (const name of Object.values(copy.PILLAR_NAMES)) assert.ok(table.includes(`<th scope="row">${name}</th>`), name);
  assert.equal((table.match(/data-v="detail\.p_/g) || []).length, 5);
  assert.equal((table.match(/data-v="detail\.c_/g) || []).length, 5);
  assert.ok(html.indexOf("radar-chart-container") < html.indexOf("pillar-table"), "the table sits under the radar");
});

test("project: the key facts are a table of row headers with the rows AI-04 lists, for a ranked project only", () => {
  const html = projectHtml(PROJECT_RECORDS["keras-team__keras"], "page");
  const table = html.match(/<table class="project-table key-facts" id="key-facts">[\s\S]*?<\/table>/)[0];
  const heads = [...table.matchAll(/<th scope="row">([^<]*)<\/th>/g)].map((m) => m[1]);
  assert.deepEqual(heads, ["Rank", "Score", "Tier", "Median first reply", "14-day no-reply share", "First-timer rank", "Ecosystem", "Window"]);
  assert.ok(table.includes("<caption>Key facts</caption>"));
  assert.ok(table.includes(">Python<"), "the ecosystem is its plain name");
  assert.ok(html.indexOf('id="answer"') < html.indexOf('id="key-facts"') && html.indexOf('id="key-facts"') < html.indexOf('id="rating"'), "answer, key facts, composite");
  assert.ok(!projectHtml(PROJECT_RECORDS["3b1b__manim"], "page").includes("key-facts"));
  // Not ranked for first-time contributors: the row carries the reason, as the answer does.
  const noFirst = { ...PROJECT_RECORDS["keras-team__keras"], standings: { ...PROJECT_RECORDS["keras-team__keras"].standings, first_timer: { rank: null, total: 0, reason: "too few first-time authors" } } };
  assert.ok(projectHtml(noFirst, "page").includes('<span data-v="standings.first_timer.reason">too few first-time authors</span>'));
});

test("project: each unit of the parameter table, below and above one million downloads", () => {
  const f = formatParameterValue;
  assert.equal(f("ttfhr_hours", 148.3311), "148.3 hours (6.2 days)");
  assert.equal(f("ttm_p90_hours", 41.2108), "41.2 hours (1.7 days)");
  assert.equal(f("ttfhr_hours", 0.0017), "0.0 hours (0 days)");
  for (const id of SHARE_PARAM_IDS) assert.equal(f(id, 0.4523), "45.2%", id);
  assert.equal(f("dr90_decision_rate", 1), "100.0%");
  assert.equal(f("gr14_ghost_rate", 0), "0.0%");
  assert.equal(f("downloads_monthly", 11338452), "11.3 million downloads a month");
  assert.equal(f("downloads_monthly", 11400000), "11.4 million downloads a month");
  assert.equal(f("downloads_monthly", 1000000), "1.0 million downloads a month");
  assert.equal(f("downloads_monthly", 999999), "999,999 downloads a month");
  assert.equal(f("downloads_monthly", 1857), "1,857 downloads a month");
  assert.equal(f("downloads_monthly", 0), "0 downloads a month");
  assert.equal(f("dependents_deduped", 424118), "424,118");
  assert.equal(f("elephant_factor", 25), "25");
  assert.equal(f("newcomer_issue_supply", 1234), "1,234");
  assert.equal(f("openssf_criticality", 0.5843), "0.5843");
  assert.equal(f("pipeline_defenses", 65.0667), "65.0667");
  assert.equal(f("dx", 85), "85");
  assert.equal(f("dx", 0.14040000000000002), "0.1404", "floating-point noise is rounded as before");
  assert.equal(f("ttfhr_hours", null), copy.MISSING_VALUE);
});

test("project: the median first reply follows project_pages.reply_time_text, hours under 48 and days with their hours", () => {
  assert.equal(replyTimeText(3.9156), "3.9 hours");
  assert.equal(replyTimeText(6), "6 hours");
  assert.equal(replyTimeText(1), "1 hour");
  assert.equal(replyTimeText(0.04), "under 0.1 hours");
  assert.equal(replyTimeText(1.45), "1.5 hours", "a printed half rounds up, as Python's Decimal does (toFixed gives 1.4)");
  assert.equal(replyTimeText(47.96), "48 hours");
  assert.equal(replyTimeText(48), "2 days (48.0 hours)");
  assert.equal(replyTimeText(148.3311), "6.2 days (148.3 hours)");
});

test("project: hours, days and shares round the printed decimal half up, in the key facts and the parameter table alike", () => {
  // 3.85 is 3.8499999999999996 as a double: toFixed(1) gives "3.8", the printed decimal rounds to 3.9.
  assert.equal(replyTimeText(3.85), "3.9 hours", "key facts");
  assert.equal(formatParameterValue("ttfhr_hours", 3.85), "3.9 hours (0.2 days)", "parameter table");
  assert.equal(formatParameterValue("ttm_p90_hours", 3.85), "3.9 hours (0.2 days)");
  // 49.2 hours is 2.05 days exactly; as a double the quotient is 2.0499999999999998.
  assert.equal(replyTimeText(49.2), "2.1 days (49.2 hours)");
  assert.equal(formatParameterValue("ttfhr_hours", 49.2), "49.2 hours (2.1 days)");
  assert.equal(replyTimeText(72), "3 days (72.0 hours)", "a trailing .0 of the days drops");
  assert.equal(formatParameterValue("ttfhr_hours", 72), "72.0 hours (3 days)", "and in the table too");
  // 0.3445 * 100 is 34.449999999999996 as a double.
  assert.equal(formatParameterValue("gr14_ghost_rate", 0.3445), "34.5%");
  assert.equal(formatParameterValue("dr90_decision_rate", 0.0005), "0.1%");
  assert.equal(formatParameterValue("mr_first_rate", 1), "100.0%");
  // the key facts' whole percent, from the same printed digits
  const keras = structuredClone(PROJECT_RECORDS["keras-team__keras"]);
  keras.evidence.find((r) => r.param_id === "gr14_ghost_rate").value = 0.285;
  assert.ok(projectHtml(keras, "page").includes('<span data-v="facts.no_reply_share">29%</span>'), "0.285 is 28.5% printed: 29%");
});

test("project: every enum code the engine defines has a plain label, and no label is for a code it does not define", () => {
  const constants = readFileSync(path.join(WEB_ROOT, "..", "..", "porchlamp", "core", "constants.py"), "utf8");
  const codes = (name) => [...constants.match(new RegExp(`^${name}: tuple\\[str, \\.\\.\\.\\] = \\(([^)]*)\\)`, "m"))[1].matchAll(/"([A-Z_]+)"/g)].map((m) => m[1]).sort();
  const maps = { legal_ip: copy.LEGAL_IP_LABELS, ai_posture: copy.AI_POSTURE_LABELS, promotion_ladder: copy.PROMOTION_LADDER_LABELS };
  const defined = { legal_ip: codes("LEGAL_IP_VALUES"), ai_posture: codes("AI_POSTURE_VALUES"), promotion_ladder: codes("PROMOTION_LADDER_VALUES") };
  for (const [param, labels] of Object.entries(maps)) {
    assert.ok(defined[param].length >= 3, `${param}: the engine's codes were read`);
    assert.deepEqual(Object.keys(labels).sort(), defined[param], param);
    for (const code of defined[param]) {
      assert.equal(formatParameterValue(param, code), ENUM_PARAM_LABELS[param][code]);
      assert.doesNotMatch(labels[code], /\b(good|bad|risky|restrictive|strict|safe|best|worst|poor|weak|strong|lenient|friendly)\b/i, `${param} ${code}: a fact, not a judgement`);
    }
  }
  // The codes the three records hold, as they reach the table.
  for (const rec of Object.values(PROJECT_RECORDS)) {
    for (const r of rec.evidence.filter((row) => maps[row.param_id] && row.value != null)) {
      assert.ok(Object.hasOwn(maps[r.param_id], r.value), `${r.param_id} ${r.value}`);
    }
  }
  assert.equal(formatParameterValue("legal_ip", "SOMETHING_NEW"), "SOMETHING_NEW", "a code the engine does not define prints as it is");
});

test("project: the panel through renderDrawer, for a ranked, a gated and a black-hole record", () => {
  const stateFor = (rec, extras) => {
    const key = `${rec.detail.owner}/${rec.detail.name}`;
    return { ...stateWithRepoDetail(rec.detail), evidenceByRepo: { [key]: rec.evidence }, projectExtrasByKey: { [key]: extras } };
  };
  const keras = PROJECT_RECORDS["keras-team__keras"];
  const ranked = renderDrawer(stateFor(keras, { answer: keras.answer, standings: keras.standings, similar: keras.similar, recordFile: keras.recordFile }), { siteBaseUrl: SITE_BASE });
  assert.ok(ranked.startsWith('<div class="drawer-backdrop" data-drawer-backdrop="true"><div class="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">'));
  assert.ok(ranked.includes('<h2 id="drawer-title"'), "the dialog is named by the title");
  assert.ok(!ranked.includes("<h1"));
  assert.ok(ranked.includes('data-drawer-page="true"'), "a ranked project links its page");
  assert.ok(ranked.includes(`<a href="/${keras.recordFile}">`), "the record's file comes from the extras");
  assert.equal(ranked.replace(/^.*?<div class="project-view/, '<div class="project-view').replace(/<\/div><\/div>$/, ""), projectHtml(keras, "panel", { pageHref: "/projects/keras-team/keras/", kind: "rated", persona: "balanced" }).replace(/<div class="project-view is-panel">/, '<div class="project-view is-panel">'), "the drawer's body is the project view, nothing else");

  const gated = renderDrawer(stateFor(PROJECT_RECORDS["3b1b__manim"], null));
  assert.ok(gated.includes(copy.gateReasonText("black_hole")), "the gate sentence");
  assert.ok(gated.includes('id="black-hole"') && !gated.includes('id="rating"') && !gated.includes("data-drawer-page"));
  assert.ok(!gated.includes('role="alert"'));

  const plainGate = { ...PROJECT_RECORDS["3b1b__manim"], detail: { ...PROJECT_RECORDS["3b1b__manim"].detail, gate_reason: "archived", suspicion: [], black_hole_evidence: null } };
  const archived = renderDrawer(stateFor(plainGate, null));
  assert.ok(archived.includes(copy.gateReasonText("archived")) && !archived.includes("data-black-hole-evidence"));
});

test("project: answer, standings and similar null (the dev API) still render, without those parts", () => {
  for (const mode of ["page", "panel"]) {
    for (const stem of ["vllm-project__vllm", "keras-team__keras"]) {
      const { answer, standings, similar, ...bare } = PROJECT_RECORDS[stem];
      const html = projectHtml({ ...bare, answer: null, standings: null, similar: null }, mode);
      const ids = new Set(projectHooks(html).map((h) => h.id));
      assert.ok(!html.includes('id="answer"') && !html.includes('id="similar"'), `${stem} ${mode}: no answer, no similar projects`);
      assert.ok(![...ids].some((id) => id.startsWith("standings.") || id.startsWith("similar.") || id === "answer"), `${stem} ${mode}: no hook of a part that is absent`);
      assert.ok(ids.has("detail.rank") && ids.has("facts.reply_time") && ids.has("detail.porchlamp"), "what the row itself holds still shows");
      assert.ok(html.includes('id="rating"') && html.includes('id="parameters"') && html.includes('id="lenses"'));
      assert.doesNotMatch(html, /undefined|NaN|null/, `${stem} ${mode}`);
    }
  }
});

test("project: loading and failed evidence show in place of the parameters, and the lens's gate replaces the score", () => {
  const keras = PROJECT_RECORDS["keras-team__keras"];
  const loading = projectHtml({ ...keras, evidence: undefined }, "panel");
  assert.ok(loading.includes(copy.DRAWER_LOADING_TEXT));
  const failed = projectHtml({ ...keras, evidence: undefined }, "panel", { evidenceError: { endpoint: "/v1/x", status: 500, detail: "boom" } });
  assert.ok(failed.includes('data-evidence-retry="true"') && failed.includes("boom"));
  const gate = projectHtml(keras, "panel", { kind: "persona-gated", persona: "first_timer", detail: undefined });
  assert.ok(!gate.includes('id="rating"') && gate.includes('class="gate-banner project-gate"'));
});

test("project: untrusted strings are escaped, and a date that is no date prints as text", () => {
  const rec = structuredClone(PROJECT_RECORDS["keras-team__keras"]);
  rec.detail.owner = '<script>alert("o")</script>';
  rec.evidence[0].source = "<img src=x onerror=alert(1)>";
  rec.evidence[0].window_start = "<b>start</b>";
  rec.answer = ["<i>answer</i>"];
  for (const mode of ["page", "panel"]) {
    const html = projectHtml(rec, mode);
    assert.ok(!/<script>alert|<img src=x|<b>start|<i>answer/.test(html), mode);
    assert.ok(html.includes("&lt;script&gt;alert") && html.includes("&lt;i&gt;answer"));
  }
});

test("project: the drawer's loading, error and not-found bodies name the dialog with an h2, never a second h1", () => {
  const states = [
    drawerStateFor({ repoDetailLoading: true }),
    drawerStateFor({ repoDetailError: { endpoint: "/v1/porchlamp/repos/nobody/nothing", status: 500, detail: "boom" } }),
    drawerStateFor({ repoDetailError: { endpoint: "/v1/porchlamp/repos/nobody/nothing", status: 404, detail: "no such repo" } }),
  ];
  for (const state of states) {
    const html = renderDrawer(state);
    assert.ok(html.includes('<h2 id="drawer-title" class="drawer-fallback-title">'), html);
    assert.ok(!html.includes("<h1"), "the list behind the dialog owns the h1");
  }
});

// --- home (the prerendered ranked view)
// The export draws the first page of the ranked view into `/` (web/prerender.mjs, views/leaderboard.js
// `renderPrerenderedBoard`) and the app takes it over without drawing it again (`adoptPrerenderedBoard`). The report's
// numbers fill the findings line and the Finding paragraph; the stars cell prints the same bytes in every locale.

import { FakeDocument } from "./fakedom.mjs";
import { drawPage } from "../../prerender.mjs";
import {
  renderPrerenderedBoard,
  renderRowHtml,
  renderCardHtml,
  adoptPrerenderedBoard,
  prerenderedBoardKey,
  renderFindingsLine,
  syncFindingsLine,
  renderBrowseLinks,
  RANKING_PAGE_SIZE,
  patchTableBody,
  patchCardBody,
  PRERENDERED_BOARD_CLASS,
} from "../views/leaderboard.js";
import { CARD_HEIGHT_PX, computeVisibleRange } from "../components/table.js";
import { requestSignature } from "../state.js";

// ---- a small reader of the board's markup into the fake DOM (the fake has no parser)
const HTML_VOID = new Set(["br", "img", "input", "hr", "meta", "link"]);
const decodeAttr = (v) => v.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");

function matchesSimple(el, selector) {
  const attr = /^\[([\w-]+)\]$/.exec(selector);
  if (attr) return el._attrs.has(attr[1]);
  const cls = /^\.([\w-]+)$/.exec(selector);
  if (cls) return dumpClasses(el).includes(cls[1]);
  throw new Error(`the test reader does not know the selector ${selector}`);
}

function dumpClasses(el) {
  return [...new Set([...(el._attrs.get("class") || "").split(/\s+/), ...el._className.split(/\s+/), ...el.classList._set])].filter(Boolean).sort();
}

function wireParsed(el) {
  el._listeners = [];
  el.addEventListener = (type, fn) => el._listeners.push([type, fn]);
  el.remove = () => el.parentNode.removeChild(el);
  el.querySelector = (selector) => {
    const wanted = selector.split(",").map((s) => s.trim());
    const walk = (node) => {
      for (const child of node.children) {
        if (child.tagName !== "#TEXT" && wanted.some((s) => matchesSimple(child, s))) return child;
        const inner = walk(child);
        if (inner) return inner;
      }
      return null;
    };
    return walk(el);
  };
  // class is held as the fake's own className/classList, so removing the attribute clears both
  const removeAttribute = el.removeAttribute.bind(el);
  el.removeAttribute = (name) => {
    if (name === "class") {
      el._className = "";
      el.classList._set.clear();
      el._attrs.delete("class");
    } else removeAttribute(name);
  };
  return el;
}

function parseAttributes(text) {
  return [...text.matchAll(/([a-zA-Z_:][-\w:.]*)(?:="([^"]*)")?/g)].map((m) => [m[1], m[2] === undefined ? "" : decodeAttr(m[2])]);
}

function applyAttribute(el, name, value) {
  if (name === "class") {
    el._className = value;
    for (const token of value.split(/\s+/).filter(Boolean)) el.classList._set.add(token);
  } else if (name === "tabindex") el._tabIndex = Number(value);
  else if (name === "hidden") el._hidden = true;
  else if (name === "style") {
    const height = /height:\s*([^;]+);?/.exec(value);
    if (height) el._style.height = height[1];
  } else el._attrs.set(name, value);
}

/** `html` (one element, or a list of them) as fake elements under a new `<div>`. `td` and `th` keep their content raw. */
function parseHtml(doc, html) {
  const root = wireParsed(doc.createElement("div"));
  const stack = [root];
  const token = /<(\/?)([a-zA-Z][\w-]*)((?:[^>"]|"[^"]*")*)>|([^<]+)/g;
  let m;
  while ((m = token.exec(html))) {
    const top = stack[stack.length - 1];
    if (m[4] !== undefined) {
      const text = wireParsed(doc.createElement("#text"));
      text._text = m[4];
      top.children.push(text);
      text.parentNode = top;
    } else if (m[1]) {
      stack.pop();
    } else {
      const tag = m[2].toLowerCase();
      const el = wireParsed(doc.createElement(tag));
      for (const [name, value] of parseAttributes(m[3].replace(/\/$/, ""))) applyAttribute(el, name, value);
      top.children.push(el);
      el.parentNode = top;
      const raw = el._attrs.get("data-rendered"); // a row's cell or a card: the builders keep the content raw, and say what it is
      if (raw !== undefined) {
        assert.ok(html.startsWith(raw, token.lastIndex), "the content is what data-rendered says");
        el._innerHTML = raw;
        token.lastIndex += raw.length;
        stack.push(el);
      } else if (tag === "th") {
        const end = html.indexOf(`</${tag}>`, token.lastIndex);
        assert.ok(end >= 0, "a th is closed");
        el._innerHTML = html.slice(token.lastIndex, end);
        token.lastIndex = end + `</${tag}>`.length;
      } else if (!HTML_VOID.has(tag) && !m[3].endsWith("/")) stack.push(el);
    }
  }
  return root.children[0];
}

/** A node as comparable text: tag, attributes (order aside), raw content, children. */
function dumpNode(el) {
  if (el.tagName === "#TEXT") return JSON.stringify(el._text);
  const attrs = new Map([...el._attrs].filter(([name]) => name !== "class"));
  const classes = dumpClasses(el);
  if (classes.length) attrs.set("class", classes.join(" "));
  if (el._tabIndex !== -1) attrs.set("tabindex", String(el._tabIndex));
  if (el._hidden) attrs.set("hidden", "");
  if (el._style.height !== undefined) attrs.set("style", `height:${el._style.height}`);
  const open = [...attrs].sort(([a], [b]) => (a < b ? -1 : 1)).map(([name, value]) => `${name}=${JSON.stringify(value)}`).join(" ");
  return `<${el.tagName.toLowerCase()} ${open}>${el._innerHTML}${el.children.map(dumpNode).join("")}</>`;
}

/** The names a DOM builder set on `el`, in the order it set them (the fake logs each change), `class` left out. */
function attributeOrder(doc, el) {
  const names = [];
  for (const { el: target, prop } of doc.mutationLog) {
    if (target !== el) continue;
    const name = prop.startsWith("attr:") ? prop.slice(5) : prop === "tabIndex" ? "tabindex" : null;
    if (name && !names.includes(name)) names.push(name);
  }
  return names;
}

const HOME_ROWS = (() => {
  const rows = synthesizeRows(20);
  rows[3].tie_group = rows[2].tie_group; // a tied pair
  rows[5].stars_audited = null;
  rows[6].stars_audited = 1234567;
  rows[7].owner = 'o&"<x>';
  rows[7].name = "it's";
  return rows;
})();
const HOME_COUNTS = { total: 11000, rated: 4322, gated: 5381, unrated: 1297 };
const HOME_LEADERBOARD = { rows: HOME_ROWS, total: 4322, counts: HOME_COUNTS, page: 1, page_size: 20, persona: "balanced" };
const homeState = (extra = {}) => ({
  ...defaultState(),
  tab: "ranked",
  leaderboard: HOME_LEADERBOARD,
  snapshot: SNAPSHOT_DOC,
  report: REPORT_EXAMPLE,
  ...extra,
});
const DESKTOP = { isMobile: false, scrollTop: 0, height: 900 };
const PHONE = { isMobile: true, scrollTop: 0, height: 812 };
const rowOptsAt = (rows, i) => ({ previousRow: i > 0 ? rows[i - 1] : null, showPersonaDelta: false, selected: false });

/** What app.js patchLeaderboardBody does to the body, for the layout `viewport` draws. */
function patchHomeBody(main, doc, state, viewport) {
  const rows = state.leaderboard.rows;
  const opts = { showPersonaDelta: state.persona !== "balanced", selectedRepoKey: null };
  if (viewport.isMobile) {
    const range = computeVisibleRange(viewport.scrollTop, viewport.height, rows.length, 8, CARD_HEIGHT_PX);
    patchCardBody(main.querySelector("[data-leaderboard-cards]"), doc, rows, range, opts);
  } else {
    const tbody = main.querySelector("[data-leaderboard-tbody]");
    tbody.parentNode.classList.toggle("stars-unaudited", !copy.hasAuditedStars(rows));
    patchTableBody(tbody, doc, rows, computeVisibleRange(viewport.scrollTop, viewport.height, rows.length), opts);
  }
}

test("home: the prerendered board is keyed by the default state and holds 20 rows, 20 cards and a spacer pair in each layout", () => {
  const html = renderPrerenderedBoard(homeState());
  assert.equal(requestSignature(defaultState()), "balanced|||rank|1|20");
  assert.ok(html.includes(`class="${PRERENDERED_BOARD_CLASS}" data-prerendered-board="${requestSignature(defaultState())}"`), "its key is the default state's");
  assert.equal((html.match(/<tr data-owner=/g) || []).length, 20, "20 rows");
  assert.equal((html.match(/<div data-owner=/g) || []).length, 20, "20 cards");
  assert.equal((html.match(/data-key="__spacer-top__"/g) || []).length, 2, "a top spacer in the table and in the cards");
  assert.equal((html.match(/data-key="__spacer-bottom__"/g) || []).length, 2);
  const at = (needle) => html.indexOf(needle);
  assert.ok(
    at("data-mobile-sort-bar") < at('class="pager"') && at('class="pager"') < at("<table") && at("<table") < at("data-leaderboard-cards") &&
      at("data-leaderboard-cards") < at("pager-bottom"),
    "the sort bar, the pager, the table, the cards, the bottom pager"
  );
  assert.ok(html.includes("data-findings-text") && html.includes(fillFromReport(copy.FINDINGS_LINE, REPORT_EXAMPLE)), "the findings line, filled");
  assert.throws(() => renderPrerenderedBoard(homeState({ leaderboard: { ...HOME_LEADERBOARD, rows: [] } })), /page of rows/);
});

test("home: each prerendered row and card is what the DOM builders make for it, attributes in the order they set them", () => {
  const doc = new FakeDocument();
  HOME_ROWS.forEach((row, i) => {
    const opts = rowOptsAt(HOME_ROWS, i);
    const built = createRowElement(doc, row, opts);
    built.dataset.key = `${row.owner}/${row.name}`;
    const drawn = parseHtml(doc, renderRowHtml(row, opts));
    assert.equal(dumpNode(drawn), dumpNode(built), `row ${row.rank}`);
    const openTag = renderRowHtml(row, opts).match(/^<tr ([^>]*)>/)[1];
    assert.deepEqual(
      parseAttributes(openTag).map(([n]) => n).filter((n) => n !== "class"),
      attributeOrder(doc, built),
      `row ${row.rank}: same attributes, same order`
    );
    const card = createCardElement(doc, row, opts);
    card.dataset.key = `${row.owner}/${row.name}`;
    assert.equal(dumpNode(parseHtml(doc, renderCardHtml(row, opts))), dumpNode(card), `card ${row.rank}`);
  });
  assert.ok(renderRowHtml(HOME_ROWS[3], rowOptsAt(HOME_ROWS, 3)).includes('class="row-tied"'), "a tied row is marked as the builder marks it");
  assert.ok(renderCardHtml(HOME_ROWS[3], rowOptsAt(HOME_ROWS, 3)).includes('class="leaderboard-card row-tied"'));
});

for (const [name, viewport] of [["desktop", DESKTOP], ["phone", PHONE]]) {
  test(`home: on a ${name}, the app adopts the prerendered board and the result is the shell patched in place, no row replaced`, () => {
    const doc = new FakeDocument();
    const state = homeState();
    const drawn = parseHtml(doc, renderPrerenderedBoard(state));
    const container = () => drawn.querySelector(viewport.isMobile ? "[data-leaderboard-cards]" : "[data-leaderboard-tbody]");
    const before = [...container().children];
    assert.equal(prerenderedBoardKey(drawn.parentNode ? drawn.parentNode : { querySelector: () => drawn }), requestSignature(state));
    assert.equal(adoptPrerenderedBoard({ querySelector: (s) => (s === "[data-prerendered-board]" ? drawn : null) }, state, viewport), true);
    for (const el of before.filter((n) => n.getAttribute("data-owner") !== null)) {
      assert.deepEqual(el._listeners.map(([type, fn]) => [type, fn]), [["click", handleRepoLinkClick]], "the click listener is attached");
    }
    patchHomeBody(drawn, doc, state, viewport);
    patchHomeBody(drawn, doc, state, viewport);
    const kept = viewport.isMobile ? computeVisibleRange(0, viewport.height, 20, 8, CARD_HEIGHT_PX).end : 20;
    const after = [...container().children];
    assert.equal(after.length, kept + 2, "the rows in the window and the two spacers");
    after.forEach((node, i) => {
      const came = before.find((b) => b.getAttribute("data-key") === node.getAttribute("data-key"));
      assert.ok(came === node, `node ${i} (${node.getAttribute("data-key")}) is the one the page came with`);
    });

    const fresh = parseHtml(doc, renderShell(state, viewport));
    patchHomeBody(fresh, doc, state, viewport);
    patchHomeBody(fresh, doc, state, viewport);
    assert.equal(dumpNode(drawn), dumpNode(fresh), "main equals renderShell plus the patched rows");
    assert.ok(!dumpNode(drawn).includes("prerendered-board") && !dumpNode(drawn).includes("data-prerendered-board"), "the marks are gone");
    assert.equal(drawn.querySelector(viewport.isMobile ? ".leaderboard-table" : "[data-leaderboard-cards]"), null, "the other layout is gone");
  });
}

test("home: a board that shows another state is left as it is and not adopted, so the shell is drawn", () => {
  const doc = new FakeDocument();
  const drawn = parseHtml(doc, renderPrerenderedBoard(homeState()));
  const root = { querySelector: (s) => (s === "[data-prerendered-board]" ? drawn : null) };
  const before = dumpNode(drawn);
  for (const other of [{ persona: "first_timer" }, { page: 2 }, { ecosystem: "npm" }, { query: "x" }, { sort: "-coverage" }, { pageSize: 50 }]) {
    assert.equal(adoptPrerenderedBoard(root, homeState(other), DESKTOP), false, JSON.stringify(other));
  }
  assert.equal(adoptPrerenderedBoard(root, homeState({ leaderboard: null, leaderboardError: { endpoint: "/x", status: 500 } }), DESKTOP), false, "an error is not a page of rows");
  assert.equal(dumpNode(drawn), before, "nothing was touched");
  assert.equal(adoptPrerenderedBoard({ querySelector: () => null }, homeState(), DESKTOP), false, "a page without a board");
  const app = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "app.js"), "utf8");
  const take = app.slice(app.indexOf("if (prerendered) {\n      // The export drew the first page"));
  assert.match(take, /prerenderedBoardKey\(main\) !== requestSignature\(state\)\) \{\s*prerendered = null;/, "a different key gives the page up at once");
  assert.ok(take.indexOf("adoptPrerenderedBoard(main, state, viewport)") < take.indexOf("main.innerHTML = renderShell(state, viewport)"), "adopt first, else the shell");
  assert.match(take, /\(!state\.leaderboard && !state\.leaderboardError\)[\s\S]*\(!state\.snapshot && !state\.snapshotError\)[\s\S]*\(!state\.report && !state\.reportError\)/, "waits for the page, the snapshot and the report");
  assert.match(app, /if \(prerendered\) return;\n    syncSortHeaders\(state\);/, "no patching of a page not yet taken over");
});

test("home: the ranked page is what the prerender draws, marked for the app", () => {
  const frame =
    '<header class="app-header" id="app-header-controls"></header><nav class="app-primary-nav" id="app-primary-nav"></nav>\n' +
    '<main class="app-main" id="app-main" tabindex="-1">\n    </main><footer class="app-footer" id="app-footer"></footer>';
  const page = drawPage(frame, { path: "index.html", view: "ranked" }, { snapshot: SNAPSHOT_DOC, report: REPORT_EXAMPLE, leaderboard: HOME_LEADERBOARD });
  assert.ok(page.includes('<main class="app-main" id="app-main" tabindex="-1" data-prerendered="ranked">'));
  assert.ok(page.includes(renderPrerenderedBoard({ ...defaultState(), leaderboard: HOME_LEADERBOARD, snapshot: SNAPSHOT_DOC, report: REPORT_EXAMPLE })));
  assert.throws(() => drawPage(frame, { path: "index.html", view: "ranked" }, { snapshot: SNAPSHOT_DOC, report: null, leaderboard: null }), /no leaderboard/);
});

test("home: the findings line fills every slot from report.json as hooks, and draws only its link without a report", () => {
  const slots = [...copy.FINDINGS_LINE.matchAll(/\{([a-z_]+)\}/g)].map((m) => m[1]);
  assert.deepEqual(slots, ["one_in", "deadline_days"]);
  const html = renderFindingsLine(REPORT_EXAMPLE);
  for (const id of slots) assert.ok(html.includes(`<span data-v="${id}">${REPORT_EXAMPLE.values[id]}</span>`), id);
  assert.ok(!html.includes("{"), "no slot left unfilled");
  assert.match(html, /<a href="\/black-holes\/"[^>]*>Read the findings<\/a>/);
  for (const none of [null, undefined, {}, { values: { one_in: "6" } }]) {
    const bare = renderFindingsLine(none);
    assert.ok(!bare.includes("data-findings-text") && !bare.includes("data-v="), JSON.stringify(none));
    assert.match(bare, /^<p class="onboarding-banner" data-findings-line="true"><a href="\/black-holes\/"[^>]*>Read the findings<\/a><\/p>$/);
  }
  const shell = renderShell({ ...defaultState(), leaderboardLoading: true, leaderboard: null, report: REPORT_EXAMPLE });
  assert.ok(shell.includes(html), "the shell draws the line from state.report");
});

test("home: a report arriving after the shell is drawn is put into the line in place, and only a changed value is rewritten", () => {
  const written = [];
  const line = { insertAdjacentHTML: (where, html) => written.push([where, html]), querySelector: () => null };
  const rootWith = (el) => ({ querySelector: (s) => (s === "[data-findings-line]" ? el : null) });
  syncFindingsLine(rootWith(line), null);
  assert.deepEqual(written, [], "no report, nothing written");
  syncFindingsLine(rootWith(line), REPORT_EXAMPLE);
  assert.deepEqual(written, [["afterbegin", `<span data-findings-text="true">${fillFromReport(copy.FINDINGS_LINE, REPORT_EXAMPLE)}</span> `]]);
  const writes = [];
  const slot = (id, text) => {
    let current = text;
    return { get textContent() { return current; }, set textContent(v) { current = v; writes.push([id, v]); } };
  };
  const hooks = { one_in: slot("one_in", REPORT_EXAMPLE.values.one_in), deadline_days: slot("deadline_days", "7") };
  const withText = { querySelector: (s) => (s === "[data-findings-text]" ? { querySelector: (h) => hooks[h.match(/"([a-z_]+)"/)[1]] } : null) };
  syncFindingsLine(rootWith(withText), REPORT_EXAMPLE);
  assert.deepEqual(writes, [["deadline_days", REPORT_EXAMPLE.values.deadline_days]], "only the value that differs");
});

test("home: the Finding paragraph is filled from report.json as hooks and left out, kicker and all, without a report", () => {
  const withReport = methodologyShell(SNAPSHOT_DOC, METHODOLOGY_DOC, REPORT_EXAMPLE);
  for (const id of ["one_in", "deadline_days"]) assert.ok(withReport.includes(`<span data-v="${id}">${REPORT_EXAMPLE.values[id]}</span>`), id);
  assert.ok(withReport.includes(copy.WHY_KICKERS[1]));
  for (const none of [null, { values: {} }]) {
    const without = methodologyShell(SNAPSHOT_DOC, METHODOLOGY_DOC, none);
    assert.ok(!without.includes("Stars do not review") && !without.includes(copy.WHY_KICKERS[1]), JSON.stringify(none));
    assert.ok(without.includes(copy.WHY_KICKERS[0]) && without.includes(copy.WHY_KICKERS[2]), "the other two stay");
  }
});

test("home: no digit is typed into the findings line or the Finding template", () => {
  // "1 in {one_in}" is the claim's own wording; every other digit would be a number typed by hand.
  for (const [name, template] of [["FINDINGS_LINE", copy.FINDINGS_LINE], ["WHY_FINDING_TEMPLATE", copy.WHY_FINDING_TEMPLATE]]) {
    assert.equal((template.match(/\b1 in \{one_in\}/g) || []).length, 1, name);
    assert.doesNotMatch(template.replace(/\b1 in \{one_in\}/, ""), /\d/, name);
  }
  assert.ok(copy.WHY_PARAGRAPHS.includes(copy.WHY_FINDING_TEMPLATE), "the paragraph is the template");
});

test("home: the lists are folded under one closed row, and every link stays inside the folded part", () => {
  const html = renderBrowseLinks(HOME_COUNTS.rated);
  assert.equal((html.match(/<details/g) || []).length, 1, "one fold, not one per line");
  assert.ok(!/<details[^>]*\sopen/.test(html), "closed until the reader opens it: the ranking is what the first screen shows");
  assert.equal(html.match(/<summary class="browse-summary">([^<]*)<\/summary>/)[1], copy.HOME_BROWSE_SUMMARY);
  assert.ok(html.indexOf("<summary") < html.indexOf("<a "), "the row comes before any link");
  assert.ok(html.indexOf("</summary>") < html.indexOf("<a "), "and every link is after it, inside the details");
  const hrefs = [...html.matchAll(/<a href="([^"]+)"/g)].map((m) => m[1]);
  assert.equal(hrefs.length, 23 + Math.ceil(HOME_COUNTS.rated / RANKING_PAGE_SIZE), "no link lost by folding: 23 to the lists, one per page of the ranking");
  assert.equal(new Set(hrefs).size, hrefs.length, "each link once");
  assert.equal(copy.HOME_BROWSE_SUMMARY, "Browse by ecosystem, lens or reply time");
});

test("home: the browse block links the six ecosystem pages and the four lens pages as real paths, in order, with copy.js words", () => {
  const html = renderBrowseLinks();
  assert.match(html, /^<nav class="browse-block" aria-label="Browse" data-home-browse="true"><details class="browse-more"><summary class="browse-summary">/);
  const links = [...html.matchAll(/<a href="([^"]+)" class="onboarding-link">([^<]+)<\/a>/g)].map((m) => [m[1], m[2]]);
  assert.deepEqual(links, [
    ["/rust/", "Rust"], ["/python/", "Python"], ["/javascript/", "JavaScript"], ["/go/", "Go"], ["/java/", "Java"], ["/c-cpp/", "C and C++"],
    ["/first-timers/", "First-timer"], ["/career/", "Career capital"], ["/senior-systems/", "Senior systems"], ["/ai-assisted/", "AI-assisted"],
    ["/rust/first-timers/", "Rust"], ["/python/first-timers/", "Python"], ["/javascript/first-timers/", "JavaScript"], ["/go/first-timers/", "Go"],
    ["/java/first-timers/", "Java"], ["/c-cpp/first-timers/", "C and C++"],
    ["/fastest-replies/", "All projects"], ["/rust/fastest-replies/", "Rust"], ["/python/fastest-replies/", "Python"],
    ["/javascript/fastest-replies/", "JavaScript"], ["/go/fastest-replies/", "Go"], ["/java/fastest-replies/", "Java"], ["/c-cpp/fastest-replies/", "C and C++"],
  ]);
  assert.ok(html.includes(`<span class="browse-group-label">${copy.HOME_BROWSE_ECOSYSTEMS_LABEL}</span>`) && copy.HOME_BROWSE_ECOSYSTEMS_LABEL === "Browse by ecosystem:");
  assert.ok(html.includes(`<span class="browse-group-label">${copy.HOME_BROWSE_LENSES_LABEL}</span>`) && copy.HOME_BROWSE_LENSES_LABEL === "Browse by contributor lens:");
  assert.ok(html.indexOf(copy.HOME_BROWSE_ECOSYSTEMS_LABEL) < html.indexOf("/rust/") && html.indexOf("/c-cpp/") < html.indexOf(copy.HOME_BROWSE_LENSES_LABEL));
  const shell = renderShell(homeState({ leaderboardLoading: false }));
  assert.ok(shell.indexOf("data-findings-line") < shell.indexOf("data-home-browse") && shell.indexOf("data-home-browse") < shell.indexOf('class="tabs"'), "right after the findings line");
  const onHome = renderBrowseLinks(HOME_COUNTS.rated);
  assert.equal(renderPrerenderedBoard(homeState()).split(onHome).length - 1, 1, "and on the prerendered page, with the ranking's pages");
  assert.ok(html.includes('>Java</a> · <a href="/c-cpp/"') && html.includes('>Career capital</a> · <a href="/senior-systems/"'),
    "a middle dot between two names, so a name of several words reads as one");
});

test("home: the stars cell prints the same bytes in every locale", () => {
  const stars = (n) => rowCells({ ...HOME_ROWS[0], stars_audited: n }, {}).cells.find((c) => c.key === "stars").html;
  assert.deepEqual([stars(0), stars(999), stars(1000), stars(1234567), stars(null)], ["0", "999", "1,000", "1,234,567", copy.MISSING_VALUE]);
  assert.notEqual((1234567).toLocaleString("de-DE"), "1,234,567", "a locale prints this number differently");
  const saved = Number.prototype.toLocaleString;
  try {
    Number.prototype.toLocaleString = function () { return saved.call(this, "de-DE"); };
    assert.equal(stars(1234567), "1,234,567", "under German as the default locale");
    Number.prototype.toLocaleString = function () { return saved.call(this, "hi-IN"); };
    assert.equal(stars(1234567), "1,234,567", "under Hindi as the default locale");
  } finally {
    Number.prototype.toLocaleString = saved;
  }
});

test("home: under the board's class the stylesheet shows the table from 768px and the cards below, the app's own breakpoint", () => {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const css = readFileSync(path.join(here, "..", "styles", "views.css"), "utf8");
  const app = readFileSync(path.join(here, "..", "app.js"), "utf8");
  assert.match(app, /isMobile: win\.innerWidth < 768/, "the app draws cards below 768px");
  const compact = css.replace(new RegExp("/\\*[\\s\\S]*?\\*/", "g"), "").replace(/\s+/g, " ");
  assert.ok(
    compact.includes("@media (max-width: 767px) { .prerendered-board .leaderboard-table { display: none; } }"),
    "below 768px the table is hidden"
  );
  assert.ok(
    compact.includes("@media (min-width: 768px) { .prerendered-board .leaderboard-cards, .prerendered-board .mobile-sort-bar { display: none; } }"),
    "from 768px the cards and their sort bar are hidden"
  );
  const outside = compact.replace(/@media \([^)]*\) \{ (?:[^{}]*\{[^{}]*\} ?)+\}/g, "");
  assert.ok(!outside.includes("prerendered-board"), "no rule of the board's class applies at every width");
});

// ------------------------------------------------ the lists at their own addresses (list-views build contract B-D)
import { routeForFields, routeForState, listSpan } from "../state.js";
import { routesOfView } from "../routes.js";
import {
  listRows,
  listHead,
  listRowOpts,
  renderListHead,
  descriptionForState as listDescription,
  replyMinN,
  lensWeightsText,
} from "../views/leaderboard.js";
import { replyTimeShort, firstReplyHtml, noReplyHtml, noReplyColumnLabel } from "../components/table.js";
import { stateForAddress, chromeFor } from "../../prerender.mjs";
import { pageDescription } from "../views/leaderboard.js";

/** The state a list's address names, from the defaults (as app.js and the prerender start). */
const atAddress = (address, extra = {}) => ({ ...stateForAddress(address), ...extra });

//: Every list of the leaderboard at its own address, and the state each one is (list-views build contract B).
const LIST_ADDRESSES = [
  ["/rankings/3/", { persona: "balanced", ecosystem: "", query: "", sort: "rank", page: 3, pageSize: 100 }],
  ["/rust/", { ecosystem: "crates", pageSize: 50 }],
  ["/python/", { ecosystem: "pypi", pageSize: 50 }],
  ["/javascript/", { ecosystem: "npm", pageSize: 50 }],
  ["/go/", { ecosystem: "go", pageSize: 50 }],
  ["/java/", { ecosystem: "maven", pageSize: 50 }],
  ["/c-cpp/", { ecosystem: "systems", pageSize: 50 }],
  ["/rust/first-timers/", { ecosystem: "crates", persona: "first_timer", pageSize: 50 }],
  ["/c-cpp/first-timers/", { ecosystem: "systems", persona: "first_timer", pageSize: 50 }],
  ["/first-timers/", { persona: "first_timer", pageSize: 50 }],
  ["/career/", { persona: "career_capital", pageSize: 50 }],
  ["/senior-systems/", { persona: "senior_systems", pageSize: 50 }],
  ["/ai-assisted/", { persona: "ai_assisted", pageSize: 50 }],
  ["/fastest-replies/", { sort: "first_reply", persona: "balanced", ecosystem: "", pageSize: 50 }],
  ["/go/fastest-replies/", { sort: "first_reply", persona: "balanced", ecosystem: "go", pageSize: 50 }],
];

test("lists: each list's address parses into its state, and that state writes the address back, at every one of them", () => {
  for (const [address, fields] of LIST_ADDRESSES) {
    const parsed = parseLocation(address, "");
    assert.equal(parsed.tab, "ranked", address);
    for (const [key, value] of Object.entries(fields)) assert.equal(parsed[key], value, `${address}: ${key}`);
    assert.equal(serializeLocation({ ...defaultState(), ...parsed }), address, `${address} round-trips`);
  }
  // every route of the table, at every instance the export writes (each page of the ranking is its own)
  for (const route of routesOfView("ranked").filter((r) => r.view)) {
    for (const page of route.path.includes("{page}") ? [1, 2, 45] : [1]) {
      const address = routePath(route.id, { page });
      assert.equal(serializeLocation({ ...defaultState(), ...parseLocation(address, "") }), address, address);
      assert.equal(matchRoute(address).route, route, address);
    }
  }
  // the fields a list's query keeps are written off the list's own defaults, in its order
  assert.equal(serializeLocation(atAddress("/rust/", { page: 2, sort: "-porchlamp" })), "/rust/?sort=-porchlamp&page=2");
  assert.equal(serializeLocation(atAddress("/rust/", { pageSize: 20 })), "/rust/?page_size=20", "20 is off the list's default of 50");
  assert.equal(serializeLocation(atAddress("/rust/", { persona: "career_capital" })), "/rust/?persona=career_capital");
  assert.deepEqual(parseLocation("/rust/", "?eco=pypi&page_size=100"), { tab: "ranked", ecosystem: "crates", pageSize: 100 }, "the path's ecosystem, not the query's");
  assert.deepEqual(parseLocation("/rankings/7/", "?page=2&page_size=20&q=x"), { tab: "ranked", persona: "balanced", ecosystem: "", query: "", sort: "rank", pageSize: 100, page: 7 }, "a page of the ranking reads no query");
});

test("lists: the most fixed fields win, an ecosystem's path beats a lens's, and a field no path holds falls back to /", () => {
  const at = (fields) => serializeLocation({ ...defaultState(), ...fields });
  // most fixed fields: the ecosystem and the lens together are the ecosystem's first-timer list, not the ecosystem's
  assert.equal(at({ ecosystem: "crates", persona: "first_timer", pageSize: 50 }), "/rust/first-timers/");
  assert.equal(at({ sort: "first_reply", ecosystem: "go", pageSize: 50 }), "/go/fastest-replies/", "three fixed beat the ecosystem's one");
  assert.equal(at({ page: 3, pageSize: 100 }), "/rankings/3/", "five fixed beat the root");
  // an ecosystem with another lens goes to the ecosystem's path, the lens in its query (the lens's path cannot hold it)
  assert.equal(at({ ecosystem: "crates", persona: "career_capital", pageSize: 50 }), "/rust/?persona=career_capital");
  // a tie in fixed fields goes to the first in table order (an ecosystem's routes come before a lens's)
  const tie = [
    { id: "home", path: "/", query: ["persona", "eco", "q", "sort", "page", "page_size"] },
    { id: "eco", view: "home", path: "/e/", query: ["persona"], fixed: { eco: "crates" } },
    { id: "lens", view: "home", path: "/l/", query: ["eco"], fixed: { persona: "first_timer" } },
  ];
  assert.equal(routeForFields("home", { eco: "crates", persona: "first_timer" }, tie).id, "eco");
  assert.equal(routeForFields("home", { eco: "crates", persona: "first_timer" }, [tie[0], tie[2], tie[1]]).id, "lens", "table order decides");
  // a field off the route's default that the route's query does not hold: the root
  assert.equal(at({ sort: "first_reply", pageSize: 50, page: 2 }), "/?sort=first_reply&page=2&page_size=50", "a fastest list has no page 2");
  assert.equal(at({ page: 3, pageSize: 100, query: "flask" }), "/?q=flask&page=3&page_size=100", "a search is no page of the ranking");
  assert.equal(at({ persona: "first_timer", query: "flask", ecosystem: "" , pageSize: 20 }), "/first-timers/?page_size=20&q=flask");
  assert.equal(at({ page: 3 }), "/?page=3", "page 3 of 20 rows is not a page of the ranking");
  assert.equal(at({}), "/");
  assert.equal(routeForState({ ...defaultState(), tab: "matrix", ecosystem: "crates" }).id, "matrix", "another view keeps its one route");
});

test("lists: an old form of a list's address loads its path, keeping the other fields and the fragment", () => {
  const go = (address) => {
    const url = new URL(address, SITE_BASE);
    return redirectTarget(url.pathname, url.search, url.hash);
  };
  assert.deepEqual(go("/?eco=crates"), { href: "/rust/", load: true });
  assert.deepEqual(go("/?persona=first_timer&eco=go"), { href: "/go/first-timers/", load: true });
  assert.deepEqual(go("/?page=3&page_size=100"), { href: "/rankings/3/", load: true });
  assert.deepEqual(go("/rust/?persona=first_timer"), { href: "/rust/first-timers/", load: true });
  assert.deepEqual(go("/?eco=crates&sort=-porchlamp&page=2#rows"), { href: "/rust/?sort=-porchlamp&page=2#rows", load: true }, "the other fields and the fragment");
  assert.deepEqual(go("/?eco=crates&page_size=20"), { href: "/rust/?page_size=20", load: true }, "a page size the old address named is kept");
  assert.deepEqual(go("/?persona=career_capital"), { href: "/career/", load: true });
  assert.deepEqual(go("/index.html?eco=npm"), { href: "/javascript/", load: true });
  for (const stays of ["/rust/", "/rust/?page=2", "/rust/?eco=pypi", "/rankings/3/", "/rankings/3/?page=9", "/go/fastest-replies/", "/?page=3", "/?eco=crates&tab=matrix"]) {
    const target = go(stays);
    if (stays.includes("tab=")) assert.equal(target.href, "/matrix/?eco=crates", stays);
    else assert.equal(target, null, stays);
  }
  assert.deepEqual(go("/?tab=ranked&eco=crates"), { href: "/rust/", load: true }, "an old ?tab= link to a list lands on its path");
  const panel = go("/?eco=crates&repo=a/b");
  assert.equal(panel.under, "/rust/", "a panel opened over a list keeps the list's path under it");
});

//: Rows for the lists: the synthetic rows, with the reply fields the API rows carry (list-views build contract A).
const LIST_ROWS = synthesizeRows(50).map((row, i) => ({
  ...row,
  first_reply_hours: i < 30 ? 0.5 + i : null,
  first_reply_n: i < 30 ? 10 + i : 4,
  no_reply_14d_share: i % 7 === 0 ? null : (i % 10) / 10,
  no_reply_14d_n: 12,
}));
const listBoard = (rows, extra = {}) => ({ rows, total: 474, counts: HOME_COUNTS, page: 1, page_size: 50, persona: "balanced", ...extra });

test("lists: a fastest list stops before the first row without a median from at least min_n pull requests", () => {
  assert.equal(replyMinN(METHODOLOGY_DOC), 10, "the methodology's min_n for ttfhr_hours");
  const row = (name, hours, n) => ({ ...LIST_ROWS[0], name, first_reply_hours: hours, first_reply_n: n });
  const at = (rows, methodology = METHODOLOGY_DOC) =>
    listRows(atAddress("/fastest-replies/", { leaderboard: listBoard(rows), methodology })).map((r) => r.name);
  assert.deepEqual(at([row("a", 1, 10), row("b", 2, 10), row("c", 3, 9), row("d", 4, 50)]), ["a", "b"], "n = 10 is listed, n = 9 is not, nor any after it");
  assert.deepEqual(at([row("a", 1, 12), row("b", null, 40), row("c", 3, 40)]), ["a"], "a null median ends the list");
  assert.deepEqual(at([row("a", 1, 12), row("b", 2, null)]), ["a"], "a median without its sample is not listed");
  assert.deepEqual(at([row("a", 1, 3), row("b", null, 3)], null), ["a"], "without the methodology only the null median cuts");
  const rust = listRows(atAddress("/rust/fastest-replies/", { leaderboard: listBoard(LIST_ROWS), methodology: METHODOLOGY_DOC }));
  assert.equal(rust.length, 30, "the synthetic page: 30 measured medians, then nulls");
  assert.equal(listRows(atAddress("/rust/", { leaderboard: listBoard(LIST_ROWS), methodology: METHODOLOGY_DOC })).length, 50, "any other list shows its whole page");
});

test("lists: the reply cells print the median in hours under 48 and in days above, its sample, and the whole percent", () => {
  assert.equal(replyTimeShort(3.85), "3.9 hours", "half up from the printed decimal");
  assert.equal(replyTimeShort(47.9), "47.9 hours");
  assert.equal(replyTimeShort(1), "1 hour");
  assert.equal(replyTimeShort(148.3), "6.2 days");
  assert.equal(replyTimeShort(48), "2 days", "a whole day count has no .0");
  assert.equal(replyTimeShort(0.04), "under 0.1 hours");
  assert.equal(firstReplyHtml({ first_reply_hours: 148.3, first_reply_n: 1234 }), '<span title="1,234 pull requests">6.2 days</span>');
  assert.equal(
    firstReplyHtml({ first_reply_hours: 3.85, first_reply_n: 12 }, true),
    '<span title="12 pull requests">3.9 hours</span><span class="reply-sample">12 pull requests</span>',
    "a fastest list prints the sample as a second line"
  );
  assert.equal(firstReplyHtml({ first_reply_hours: null, first_reply_n: 4 }, true), '<span title="4 pull requests">—</span>', "not measured: a dash, its sample only in the title");
  assert.equal(firstReplyHtml({ first_reply_hours: null, first_reply_n: null }), "—");
  assert.equal(noReplyHtml({ no_reply_14d_share: 0.345 }), "35%");
  assert.equal(noReplyHtml({ no_reply_14d_share: 0 }), "0%");
  assert.equal(noReplyHtml({ no_reply_14d_share: null }), "—");
  const { cells } = rowCells({ ...LIST_ROWS[2], first_reply_hours: 148.3, first_reply_n: 20, no_reply_14d_share: 0.5 }, {});
  const keys = cells.map((c) => c.key);
  assert.deepEqual(keys.slice(keys.indexOf("tier"), keys.indexOf("tier") + 3), ["tier", "first_reply", "no_reply_14d"], "after Tier");
  assert.equal(cells.find((c) => c.key === "no_reply_14d").html, "50%");
  const header = renderColumnHeader("rank", false, { noReply: noReplyColumnLabel(REPORT_EXAMPLE) });
  assert.ok(header.includes('<th data-col="first_reply">First reply (median)</th><th data-col="no_reply_14d">No reply in 14 days</th>'), header);
  assert.equal(noReplyColumnLabel(null), copy.paramName("gr14_ghost_rate"), "without a report, the parameter's name");
  assert.ok(renderColumnHeader("rank", false).includes(`<th data-col="no_reply_14d">${escapeHtml(copy.paramName("gr14_ghost_rate"))}</th>`));
  const card = renderCard({ ...LIST_ROWS[2], first_reply_hours: 148.3, first_reply_n: 20, no_reply_14d_share: 0.5 }, { noReplyLabel: "No reply in 14 days" });
  assert.ok(card.includes('First reply (median) <span data-card-field="first_reply"><span title="20 pull requests">6.2 days</span></span> · No reply in 14 days <span data-card-field="no_reply_14d">50%</span>'), card);
});

test("lists: each page of the ranking links all its sibling pages, the current one marked, and the home links them all", () => {
  const state = atAddress("/rankings/2/", { leaderboard: listBoard(LIST_ROWS, { total: 4402, page: 2, page_size: 100 }), report: REPORT_EXAMPLE });
  const shell = renderShell(state, DESKTOP);
  const lines = shell.match(/<div class="list-pages" data-list-pages="true">.*?<\/div>/g) || [];
  assert.equal(lines.length, 2, "above and below the rows");
  assert.ok(shell.indexOf(lines[0]) < shell.indexOf("<table") && shell.lastIndexOf(lines[1]) > shell.indexOf("</table>"));
  const links = [...lines[0].matchAll(/<a href="([^"]+)" class="onboarding-link"( aria-current="page")?>(\d+)<\/a>/g)];
  assert.equal(links.length, 45, "4,402 projects at 100 a page");
  links.forEach(([, href, current, label], i) => {
    assert.equal(href, `/rankings/${i + 1}/`);
    assert.equal(label, String(i + 1));
    assert.equal(Boolean(current), i === 1, `page ${i + 1} marked only when it is the page shown`);
  });
  assert.ok(lines[0].startsWith('<div class="list-pages" data-list-pages="true"><span>The whole ranking, 100 projects a page:</span> '));
  assert.ok(!shell.includes('class="pager'), "no buttons pager: the links are the pager");
  // the home's line, from the balanced ranking's size
  const home = renderBrowseLinks(4402);
  const homeLinks = [...home.matchAll(/<a href="\/rankings\/(\d+)\/" class="onboarding-link">(\d+)<\/a>/g)].map((m) => Number(m[1]));
  assert.deepEqual(homeLinks, Array.from({ length: 45 }, (_, i) => i + 1));
  assert.ok(home.includes('<div class="browse-group browse-pages" data-ranking-pages="true"><span class="browse-group-label">The whole ranking, 100 projects a page:</span> <a href="/rankings/1/"'));
  assert.ok(!home.includes("aria-current"), "the home marks no page");
  assert.ok(!renderBrowseLinks(null).includes("/rankings/"), "no line before the size is known");
  assert.ok(renderShell(homeState(), DESKTOP).includes(renderBrowseLinks(HOME_COUNTS.rated)), "the home draws it from the snapshot's rated count");
  assert.ok(!shell.includes("data-home-browse"), "a list's page has no home browse block");
});

/** The visible text of markup (tags dropped, entities read). */
const listTextOf = (html) => decodeAttr(html.replace(/<[^>]+>/g, ""));
const listHooksOf = (html) => Object.fromEntries([...html.matchAll(/<span data-v="([^"]+)">([^<]*)<\/span>/g)].map((m) => [m[1], decodeAttr(m[2])]));

test("lists: each list's heading and intro, with every number a hook, and its description the intro's first sentence", () => {
  const data = { report: REPORT_EXAMPLE, methodology: METHODOLOGY_DOC };
  const cases = [
    ["/rankings/2/", { total: 4402, page: 2, page_size: 100 },
      "The Porchlamp ranking, projects 101 to 200",
      "Open-source projects ranked 101 to 200 of 4,402 by what happens to a stranger's pull request, each with its median first reply.",
      { "list.first": "101", "list.last": "200", "list.total": "4,402" }, null],
    ["/rust/", { total: 474 },
      "Rust projects that answer outside pull requests",
      "Porchlamp ranks 474 Rust projects by what happens to a stranger's pull request. Among the 488 judged Rust projects, 16.6% left most outside pull requests unanswered for 14 days, against 19.9% across all six ecosystems.",
      { "list.total": "474", "list.judged": "488", "list.eco_share": "16.6", "list.deadline_days": "14", "list.share": "19.9", "list.ecosystems_word": "six" }, null],
    ["/first-timers/", { total: 1234 },
      "Open-source projects for first-time contributors",
      "This view ranks 1,234 projects for first-time contributors. It weighs responsiveness 35%, accessibility 30%, governance 15%, criticality 10% and rigor 10%.",
      { "list.total": "1,234", "list.weights": "responsiveness 35%, accessibility 30%, governance 15%, criticality 10% and rigor 10%" }, null],
    ["/go/first-timers/", { total: 300 },
      "Go projects for first-time contributors",
      "This view ranks 300 Go projects for first-time contributors. It weighs responsiveness 35%, accessibility 30%, governance 15%, criticality 10% and rigor 10%.",
      { "list.total": "300", "list.weights": "responsiveness 35%, accessibility 30%, governance 15%, criticality 10% and rigor 10%" }, null],
    ["/fastest-replies/", { total: 4402 },
      "Fastest first reply to pull requests",
      "30 projects, ordered by the median time to a first human reply to outside pull requests, fastest first. A median is listed only when it comes from 10 or more pull requests; the sample is shown.",
      { "list.total": "30", "list.min_n": "10" },
      ["This is one measure, not the Porchlamp score, which weighs 18 parameters across five pillars.", { "list.parameters": "18", "list.pillars": "five" }]],
    ["/java/fastest-replies/", { total: 222 },
      "Fastest first reply to pull requests: Java projects",
      "30 Java projects, ordered by the median time to a first human reply to outside pull requests, fastest first. A median is listed only when it comes from 10 or more pull requests; the sample is shown.",
      { "list.total": "30", "list.min_n": "10" },
      ["This is one measure, not the Porchlamp score, which weighs 18 parameters across five pillars.", { "list.parameters": "18", "list.pillars": "five" }]],
  ];
  for (const [address, board, heading, intro, hooks, caveat] of cases) {
    const state = atAddress(address, { leaderboard: listBoard(LIST_ROWS, board), ...data });
    const html = renderListHead(state);
    const h1 = html.match(/<h1 id="leaderboard-heading" class="view-heading">(.*?)<\/h1>/);
    assert.ok(h1, `${address}: a visible h1`);
    assert.equal(listTextOf(h1[1]), heading, address);
    const p = html.match(/<p class="list-intro" data-list-intro="true">(.*?)<\/p>/);
    assert.equal(listTextOf(p[1]), intro, address);
    assert.deepEqual(listHooksOf(p[1]), hooks, `${address}: every number in the intro is a hook`);
    assert.doesNotMatch(listTextOf(p[1]), /\{/, `${address}: no slot left`);
    assert.doesNotMatch(listTextOf(p[1].replace(/<span data-v="[^"]+">[^<]*<\/span>/g, "")), /\d/, `${address}: no number outside a hook`);
    const firstSentence = listHead(state).sentences[0].text;
    assert.equal(listDescription(state), firstSentence, address);
    assert.ok(intro.startsWith(firstSentence), `${address}: the description is the intro's first sentence, verbatim`);
    const c = html.match(/<p class="list-caveat" data-list-caveat="true">(.*?)<\/p>/);
    if (caveat) {
      assert.equal(listTextOf(c[1]), caveat[0], address);
      assert.deepEqual(listHooksOf(c[1]), caveat[1], address);
    } else assert.equal(c, null, `${address}: no caveat`);
    assert.equal(titleForState(state), `${address.startsWith("/rankings/") ? "Open-source projects ranked 101 to 200 of 4,402" : heading} | Porchlamp`, `${address}: the title`);
    assert.ok(titleForState(state).length <= 70, `${address}: the title fits 70 characters`);
    assert.ok(listDescription(state).length <= 155, `${address}: the description fits 155 characters`);
  }
  // an ecosystem's list links its two other lists by their headings
  const rust = renderListHead(atAddress("/rust/", { leaderboard: listBoard(LIST_ROWS), ...data }));
  assert.ok(rust.includes('<p class="list-sublinks" data-list-sublinks="true"><a href="/rust/first-timers/" class="onboarding-link">Rust projects for first-time contributors</a> · <a href="/rust/fastest-replies/" class="onboarding-link">Fastest first reply to pull requests: Rust projects</a></p>'), rust);
  // without the report or the methodology, the sentences that need them are left out; under a search, the counts
  const bare = atAddress("/rust/", { leaderboard: listBoard(LIST_ROWS) });
  assert.equal(listHead(bare).sentences.map((s) => s.text).join(" "), "Porchlamp ranks 474 Rust projects by what happens to a stranger's pull request.");
  assert.deepEqual(listHead(atAddress("/career/", { leaderboard: listBoard(LIST_ROWS) })).sentences.length, 1, "no weights without the methodology");
  assert.equal(listHead(atAddress("/fastest-replies/", { leaderboard: listBoard(LIST_ROWS) })).caveat, null);
  assert.deepEqual(listHead(atAddress("/rust/", { query: "serde", leaderboard: listBoard(LIST_ROWS), ...data })).sentences, [], "a search keeps the heading and drops the counts");
  assert.equal(listHead(atAddress("/", { leaderboard: HOME_LEADERBOARD })), null, "the home has no list head");
  assert.equal(lensWeightsText(METHODOLOGY_DOC, "senior_systems"), "criticality 35%, governance 25%, rigor 20%, responsiveness 15% and accessibility 5%");
  assert.deepEqual(listSpan(atAddress("/rankings/45/", { leaderboard: listBoard([], { total: 4402 }) })), { first: 4401, last: 4402, total: 4402 }, "the last page stops at the total");
});

//: Every copy.js string the lists added (list-views build contract C: "No digit in any new copy.js string").
const LIST_COPY = [
  "HOME_BROWSE_SUMMARY", "HOME_BROWSE_RANKING_LABEL", "HOME_BROWSE_FIRST_TIMERS_LABEL", "HOME_BROWSE_FASTEST_LABEL", "HOME_BROWSE_FASTEST_ALL",
  "LIST_RANKINGS_HEADING", "LIST_INTRO_RANKINGS", "LIST_INTRO_ECOSYSTEM", "LIST_INTRO_ECOSYSTEM_REPORT", "LIST_INTRO_LENS",
  "LIST_INTRO_ECOSYSTEM_LENS", "LIST_INTRO_WEIGHS", "LIST_WEIGHT_ITEM", "LIST_AND", "LIST_INTRO_FASTEST",
  "LIST_INTRO_FASTEST_ECOSYSTEM", "LIST_INTRO_FASTEST_SAMPLE", "LIST_CAVEAT_FASTEST", "COLUMN_FIRST_REPLY", "COLUMN_NO_REPLY",
  "REPLY_SAMPLE_TEXT", "LENS_PHRASES", "NUMBER_WORDS",
  // the not-ranked lists by reason (PR 2)
  "NOT_RANKED_LIST_NAMES", "NOT_RANKED_LIST_PAGE_OF", "NOT_RANKED_LIST_REASONS", "NOT_RANKED_LIST_COUNT", "NOT_RANKED_LIST_TEST",
  "NOT_RANKED_LIST_ROW", "NOT_RANKED_LIST_ROW_BLACK_HOLE", "NOT_RANKED_LIST_PAGES_LABEL", "NOT_RANKED_LISTS_LABEL",
  "NOT_RANKED_LISTS_ITEM",
];
/** copy.js's own strings that no view reads (its functions fill them): read from its source, as the verifier reads them. */
const LIST_COPY_INTERNAL = ["UNRATED_COVERAGE_BELOW_FLOOR", "UNRATED_NO_RESPONSIVENESS"];
const COPY_SOURCE = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "copy.js"), "utf8");
function copySourceString(name) {
  const found = COPY_SOURCE.match(new RegExp(`(?:export )?const ${name} =\\s*"((?:[^"\\\\]|\\\\.)*)";`));
  return found ? JSON.parse(`"${found[1]}"`) : undefined;
}

/** The strings of a copy.js value (a string, a list or a map of strings) that hold a digit. */
function stringsWithDigits(value) {
  const strings = typeof value === "string" ? [value] : Object.values(value);
  return strings.filter((text) => typeof text !== "string" || /\d/.test(text));
}

/** The strings of a copy.js value that type a count as a word, two to nine ("six ecosystems"); "one measure" is wording,
 *  not a count. A count fills a slot, from the data (`NUMBER_WORDS` turns it into a word). */
function stringsWithCountWords(value) {
  const strings = typeof value === "string" ? [value] : Object.values(value);
  return strings.filter((text) => typeof text !== "string" || /\b(two|three|four|five|six|seven|eight|nine)\b/i.test(text));
}

test("lists: no digit is typed into any copy.js string the lists added: every number fills a slot", () => {
  for (const name of LIST_COPY) {
    assert.ok(copy[name] !== undefined, `copy.js has ${name}`);
    assert.deepEqual(stringsWithDigits(copy[name]), [], name);
  }
  for (const name of LIST_COPY_INTERNAL) {
    assert.ok(copySourceString(name) !== undefined, `copy.js has ${name}`);
    assert.deepEqual(stringsWithDigits(copySourceString(name)), [], name);
    assert.deepEqual(stringsWithCountWords(copySourceString(name)), [], name);
  }
  assert.deepEqual(stringsWithDigits("weighs 18 parameters"), ["weighs 18 parameters"], "the check finds a typed number");
  assert.deepEqual(stringsWithDigits({ a: "fine", b: "in 14 days" }), ["in 14 days"]);
  // a list by reason's black-hole sentence with the deadline typed, as the old reason copy has it, is caught
  const typedDeadline = { ...copy.NOT_RANKED_LIST_REASONS, black_hole: copy.gateReasonText("black_hole") };
  assert.deepEqual(stringsWithDigits(typedDeadline), [copy.gateReasonText("black_hole")], "a typed deadline is found");
  // a typed floor in the coverage clause, and a typed page size in the count, are caught
  assert.deepEqual(stringsWithDigits("(coverage {coverage}, below the 60% floor)."), ["(coverage {coverage}, below the 60% floor)."], "a typed 60");
  const typedSize = copy.NOT_RANKED_LIST_COUNT.replace("{page_size}", "200");
  assert.deepEqual(stringsWithDigits(typedSize), [typedSize], "a typed 200");
  // the lists' route titles type no number either: the page part fills its slots
  for (const route of ROUTES.filter((r) => r.view === "notranked")) assert.doesNotMatch(route.title, /\d/, route.id);
  // a count is never typed as a word either ("six ecosystems", "five pillars"): it is the data's count, in words
  for (const name of LIST_COPY.filter((n) => n !== "NUMBER_WORDS")) assert.deepEqual(stringsWithCountWords(copy[name]), [], name);
  assert.deepEqual(stringsWithCountWords("against {share}% across all six ecosystems"), ["against {share}% across all six ecosystems"]);
  assert.deepEqual(stringsWithCountWords("which weighs {parameters} parameters across {pillars} pillars"), []);
  assert.deepEqual(copy.LENS_PHRASES, {
    first_timer: "first-time contributors", career_capital: "building career capital",
    senior_systems: "senior systems engineers", ai_assisted: "AI-assisted contributors",
  }, "the four lens phrases, which home_pages.py reads");
});

/** A list's page as the prerender draws its state: the page of rows, the snapshot, the report and the methodology. */
const listPageState = (address, board) =>
  atAddress(address, { leaderboard: listBoard(LIST_ROWS, board), snapshot: SNAPSHOT_DOC, report: REPORT_EXAMPLE, methodology: METHODOLOGY_DOC });

test("lists: the app adopts a list's board for the list's state, drops it for another, and patches it into renderShell's DOM", () => {
  for (const [address, board] of [["/rust/", { total: 474 }], ["/fastest-replies/", { total: 4402 }], ["/rankings/2/", { total: 4402, page: 2, page_size: 100 }]]) {
    const state = listPageState(address, board);
    for (const viewport of [DESKTOP, PHONE]) {
      const doc = new FakeDocument();
      const drawn = parseHtml(doc, renderPrerenderedBoard(state));
      const root = { querySelector: (sel) => (sel === "[data-prerendered-board]" ? drawn : null) };
      assert.equal(drawn.getAttribute("data-prerendered-board"), requestSignature(state), `${address}: keyed by its state`);
      for (const other of [{ page: state.page + 1 }, { ecosystem: "pypi" }, { pageSize: 20 }, { persona: "ai_assisted" }, { sort: "-coverage" }]) {
        assert.equal(adoptPrerenderedBoard(root, { ...state, ...other }, viewport), false, `${address}: not for ${JSON.stringify(other)}`);
      }
      assert.equal(adoptPrerenderedBoard(root, state, viewport), true, `${address} on ${viewport.isMobile ? "a phone" : "a desktop"}`);
      const rows = listRows(state);
      const opts = { ...listRowOpts(state), selectedRepoKey: null };
      const patch = (el) => {
        if (viewport.isMobile) {
          patchCardBody(el.querySelector("[data-leaderboard-cards]"), doc, rows, computeVisibleRange(0, viewport.height, rows.length, 8, CARD_HEIGHT_PX), opts);
        } else {
          const tbody = el.querySelector("[data-leaderboard-tbody]");
          tbody.parentNode.classList.toggle("stars-unaudited", !copy.hasAuditedStars(rows));
          patchTableBody(tbody, doc, rows, computeVisibleRange(0, viewport.height, rows.length), opts);
        }
      };
      const before = [...(viewport.isMobile ? drawn.querySelector("[data-leaderboard-cards]") : drawn.querySelector("[data-leaderboard-tbody]")).children];
      patch(drawn);
      patch(drawn);
      const after = [...(viewport.isMobile ? drawn.querySelector("[data-leaderboard-cards]") : drawn.querySelector("[data-leaderboard-tbody]")).children];
      assert.ok(after.every((node) => before.includes(node)), `${address}: no row replaced`);
      const fresh = parseHtml(doc, renderShell(state, viewport));
      patch(fresh);
      patch(fresh);
      assert.equal(dumpNode(drawn), dumpNode(fresh), `${address}: the adopted board is renderShell's DOM, patched`);
    }
  }
  const fastest = renderPrerenderedBoard(listPageState("/fastest-replies/", { total: 4402 }));
  assert.equal((fastest.match(/<tr data-owner=/g) || []).length, 30, "the fastest list draws the rows before its cut");
  assert.ok(!fastest.includes("data-mobile-sort-bar") && !fastest.includes('class="pager'), "and no sort bar or pager");
  assert.ok(fastest.includes('<span class="reply-sample">10 pull requests</span>'), "its samples as a second line");
});

test("lists: the prerender draws a page of the ranking and a preset from its address: board, heading, intro, title and description", () => {
  const frame = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "404.html"), "utf8");
  for (const [address, board, title, description, rowCount] of [
    ["/rankings/3/", { total: 4402, page: 3, page_size: 100 }, "Open-source projects ranked 201 to 300 of 4,402 | Porchlamp",
      "Open-source projects ranked 201 to 300 of 4,402 by what happens to a stranger's pull request, each with its median first reply.", 50],
    ["/python/", { total: 696 }, "Python projects that answer outside pull requests | Porchlamp",
      "Porchlamp ranks 696 Python projects by what happens to a stranger's pull request.", 50],
  ]) {
    const data = { snapshot: SNAPSHOT_DOC, report: REPORT_EXAMPLE, methodology: METHODOLOGY_DOC, leaderboard: listBoard(LIST_ROWS, board) };
    const page = drawPage(frame, { path: `${address.slice(1)}index.html`, view: "ranked", address }, data);
    const state = { ...stateForAddress(address), ...data };
    assert.ok(page.includes('<main class="app-main" id="app-main" tabindex="-1" data-prerendered="ranked">'), address);
    assert.ok(page.includes(renderPrerenderedBoard(state)), `${address}: the board of its state`);
    assert.ok(page.includes(`data-prerendered-board="${requestSignature(state)}"`), address);
    assert.equal((page.match(/<tr data-owner=/g) || []).length, rowCount, `${address}: its page of rows`);
    assert.equal((page.match(/<div data-owner=/g) || []).length, rowCount, `${address}: and its cards`);
    assert.ok(page.includes(renderListHead(state)), `${address}: the heading and the intro`);
    assert.equal((page.match(/<title>([^<]*)<\/title>/g) || []).length, 1);
    assert.equal(decodeAttr(page.match(/<title>([^<]*)<\/title>/)[1]), title, address);
    assert.equal(decodeAttr(page.match(/<meta name="description" content="([^"]*)">/)[1]), description, address);
    assert.equal((page.match(/<meta name="description"/g) || []).length, 1, `${address}: one description`);
  }
  const home = drawPage(frame, { path: "index.html", view: "ranked" }, { snapshot: SNAPSHOT_DOC, report: REPORT_EXAMPLE, leaderboard: HOME_LEADERBOARD });
  assert.ok(home.includes("<title>Page not found | Porchlamp</title>"), "a page without an address keeps the head the export wrote");
  assert.throws(() => stateForAddress("/matrix/"), /not the address of a list/);
});

test("lists: a fastest list's count is the rows it lists, not the projects in scope", () => {
  const rows = LIST_ROWS.map((row, i) => ({ ...row, first_reply_hours: i < 7 ? 1 + i : null }));
  const state = atAddress("/rust/fastest-replies/", { leaderboard: listBoard(rows, { total: 474 }), methodology: METHODOLOGY_DOC });
  assert.equal(listRows(state).length, 7);
  const intro = renderListHead(state).match(/<p class="list-intro" data-list-intro="true">(.*?)<\/p>/)[1];
  assert.equal(listHooksOf(intro)["list.total"], "7", "the seven rows before the cut");
  assert.ok(listDescription(state).startsWith("7 Rust projects, ordered by"), listDescription(state));
  const full = atAddress("/fastest-replies/", { leaderboard: listBoard(LIST_ROWS.map((row) => ({ ...row, first_reply_hours: 2, first_reply_n: 12 })), { total: 4402 }), methodology: METHODOLOGY_DOC });
  assert.equal(listHooksOf(renderListHead(full))["list.total"], "50", "at most the page: 50, never the 4,402 in scope");
});

test("lists: a list's page draws its header's lens and ecosystem selects as the app draws them for its state", () => {
  for (const [address, persona, eco] of [["/rust/", null, "crates"], ["/career/", "career_capital", null], ["/go/first-timers/", "first_timer", "go"]]) {
    const drawn = chromeFor({ path: `${address.slice(1)}index.html`, view: "ranked", address }, SNAPSHOT_DOC).header;
    const app = { ...defaultState(), ...stateFromLocation(address, "", null) };
    assert.equal(drawn, renderAppHeader(app.tab, SNAPSHOT_DOC, app), `${address}: the prerendered header is the app's, byte for byte`);
    const selected = [...drawn.matchAll(/<option value="([^"]*)" selected>/g)].map((m) => m[1]);
    assert.deepEqual(selected, [persona, eco].filter(Boolean), `${address}: the state's lens and ecosystem are selected`);
    // app.js syncHeaderControls writes a select only when its value differs: these are the values it compares
    assert.equal(persona || "balanced", app.persona, address);
    assert.equal(eco || "", app.ecosystem, address);
  }
  const home = chromeFor({ path: "index.html", view: "ranked" }, SNAPSHOT_DOC).header;
  assert.equal(home, renderAppHeader("ranked", SNAPSHOT_DOC), "the home's header is as before");
  assert.ok(!home.includes(" selected"), "nothing marked at the defaults");
  assert.equal(renderAppHeader("ranked", SNAPSHOT_DOC, defaultState()), home, "the default state marks nothing either");
});

test("lists: the description goes back to the home's own on /, only when the document first loaded at /", () => {
  const home = { ...defaultState(), leaderboard: HOME_LEADERBOARD };
  const rust = atAddress("/rust/", { leaderboard: listBoard(LIST_ROWS) });
  assert.equal(pageDescription(rust, "the home's"), listDescription(rust), "a list writes its own");
  assert.equal(pageDescription(home, "the home's"), "the home's", "back on / the home's comes back");
  assert.equal(pageDescription({ ...home, query: "flask" }, "the home's"), "the home's", "any address at /");
  assert.equal(pageDescription(home, null), null, "first loaded as a list: / leaves the description as it is");
  assert.equal(pageDescription({ ...home, repo: "a/b" }, "the home's"), null, "an open panel leaves it");
  assert.equal(pageDescription({ ...home, tab: "matrix" }, "the home's"), null, "another view leaves it");
  const app = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "app.js"), "utf8");
  assert.match(app, /const homeDescription =\s*descriptionMeta && matchRoute\(win\.location\.pathname\)\.route\.id === "ranked" \? descriptionMeta\.getAttribute\("content"\) : null;/,
    "app.js keeps the description the document loaded with, when its first address was /");
  assert.match(app, /pageDescription\(state, homeDescription\)/, "and writes what pageDescription says on every render");
});

// --- The not-ranked projects by reason: eight lists at their own addresses, the tab count, the chip (list-views PR 2) ---
// The new names are read through their modules' namespaces, so on code without them each test fails on its own.
import * as nrState from "../state.js";
import * as nrView from "../views/notranked.js";
import * as nrPrerender from "../../prerender.mjs";

//: The balanced not-ranked projects by bare reason (main-f9a96d4 build, read from not-ranked-balanced.json): no fork
//: and no mirror there, so those two lists are empty.
const REASON_COUNTS = Object.freeze({
  stale: 2479, non_software: 1067, black_hole: 638, archived: 624, unlicensed: 491, fork: 0, mirror: 0,
  insufficient_evidence: 1198, no_external_workflow: 95, off_platform_routed: 12,
});
const GATED_REASONS = new Set(["stale", "non_software", "black_hole", "archived", "unlicensed", "fork", "mirror"]);
const REASON_NAMES = {
  stale: "no recent human activity", non_software: "not software", archived: "archived", unlicensed: "no open-source license",
  black_hole: "unanswered pull requests", fork: "fork of another repository", mirror: "mirror of another repository",
  insufficient_evidence: "not enough evidence",
  no_external_workflow: "no outside contribution workflow", off_platform_routed: "contributions happen off GitHub",
};
const NR_ECOSYSTEMS = ["npm", "pypi", "crates", "go", "maven", "systems"];

/**
 * A not-ranked list as the API sends it for the balanced lens: rows in lower-case owner/name order, split gated and
 * unrated. The insufficient-evidence rows carry the engine's prefixed spelling ("unrated: insufficient_evidence"), as the
 * static data does; every third black hole has its numbers, every third has them null and the rest lack the fields.
 */
function notRankedFixture(counts = REASON_COUNTS) {
  const rows = [];
  for (const [reason, n] of Object.entries(counts)) {
    for (let i = 0; i < n; i++) {
      const gated = GATED_REASONS.has(reason);
      const row = {
        owner: `${i % 2 ? "Org" : "org"}-${reason.replace(/_/g, "-")}-${String(i).padStart(4, "0")}`,
        name: `Repo${i}`,
        ecosystem: NR_ECOSYSTEMS[i % NR_ECOSYSTEMS.length],
        gate_reason: gated ? reason : null,
        unrated_reason: gated ? null : reason === "insufficient_evidence" ? "unrated: insufficient_evidence" : reason,
        persona_gate_reason: null,
        coverage: gated ? null : 0.42,
      };
      if (reason === "black_hole" && i % 3 === 0) Object.assign(row, { unanswered: 26 + i, counted: 41 + 2 * i });
      else if (i % 3 === 1) Object.assign(row, { unanswered: null, counted: null });
      rows.push(row);
    }
  }
  const key = (row) => `${row.owner}/${row.name}`.toLowerCase();
  rows.sort((a, b) => (key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0));
  return {
    gated: rows.filter((row) => row.gate_reason),
    unrated: rows.filter((row) => !row.gate_reason),
    persona_hidden: [],
    counts: HOME_COUNTS,
    truncated: false,
  };
}
const NR_LIST = notRankedFixture();

/** The state a list by reason's address names, from the defaults, with `extra` loaded. */
const atReason = (address, extra = {}) => ({ ...defaultState(), ...parseLocation(address, ""), ...extra });
/** A list by reason's page with everything the prerender loads. */
const reasonPageState = (address, extra = {}) =>
  atReason(address, { notRanked: NR_LIST, report: REPORT_EXAMPLE, leaderboard: HOME_LEADERBOARD, snapshot: SNAPSHOT_DOC, methodology: METHODOLOGY_DOC, ...extra });
//: The methodology's coverage floor (missingness.coverage_thresholds.unrated_below), as the views read it.
const NR_FLOOR = METHODOLOGY_DOC.missingness.coverage_thresholds.unrated_below;
const pagesOf = (reason) => Math.max(1, Math.ceil(REASON_COUNTS[reason] / 200));

/** The list items of `html`: `{ owner, name, text }`, `text` the item's whole text. An item holding any element (a
 *  label, a badge, a chip, a hook) is an error: a row is one plain sentence. */
function reasonItemsOf(html) {
  return [...html.matchAll(/<li data-owner="([^"]*)" data-name="([^"]*)">(.*?)<\/li>/g)].map(([, owner, name, inner]) => {
    if (/[<>]/.test(inner)) throw new Error(`${owner}/${name}: the item holds markup: ${inner}`);
    return { owner: decodeAttr(owner), name: decodeAttr(name), text: decodeAttr(inner) };
  });
}

test("not-ranked lists: each list by reason round-trips at its first and last page; page 0 or an unknown reason is not found", () => {
  for (const [slug, reason] of REASON_LIST_SLUGS) {
    for (const page of new Set([1, pagesOf(reason)])) {
      const address = `/not-ranked/${slug}/${page}/`;
      const parsed = parseLocation(address, "");
      assert.deepEqual(parsed, { tab: "notranked", notRankedReason: reason, persona: "balanced", ecosystem: "", query: "", pageSize: 200, page }, address);
      assert.equal(serializeLocation({ ...defaultState(), ...parsed }), address, `${address} round-trips`);
      assert.equal(matchRoute(address).route.id, `notranked-${slug}`, address);
      assert.equal(redirectTarget(address, "", ""), null, `${address} is its own address`);
    }
  }
  assert.equal(pagesOf("stale"), 13, "the stale list's last page is 13");
  for (const missing of ["/not-ranked/archived/0/", "/not-ranked/nope/1/", "/not-ranked/archived/01/", "/not-ranked/archived/"]) {
    assert.deepEqual(parseLocation(missing, ""), { tab: "notfound", missingPath: missing }, missing);
  }
  assert.deepEqual(parseLocation("/not-ranked/archived/2/", "?eco=crates&page_size=20&q=x").ecosystem, "", "a list by reason reads no query");
});

test("not-ranked lists: a reason at 200 a page writes its path; a lens, an ecosystem or a search writes /not-ranked/?…; /not-ranked/ is as it was", () => {
  const at = (fields) => serializeLocation({ ...defaultState(), tab: "notranked", ...fields });
  const stale = { notRankedReason: "stale", pageSize: 200, page: 3 };
  assert.equal(at(stale), "/not-ranked/stale/3/");
  assert.equal(at({ ...stale, notRankedReason: "black_hole", page: 1 }), "/not-ranked/unanswered/1/");
  assert.equal(at({ ...stale, notRankedReason: "fork", page: 1 }), "/not-ranked/fork/1/");
  assert.equal(at({ ...stale, notRankedReason: "mirror", page: 2 }), "/not-ranked/mirror/2/");
  assert.match(at({ notRankedReason: "fork", pageSize: 200, ecosystem: "go" }), /^\/not-ranked\/\?/, "an ecosystem leaves the fork list too");
  for (const [field, value] of [["ecosystem", "crates"], ["persona", "first_timer"], ["query", "flask"]]) {
    assert.match(at({ ...stale, [field]: value }), /^\/not-ranked\/\?/, `a ${field} leaves the list's path`);
  }
  assert.equal(at({ notRankedReason: "stale" }), "/not-ranked/", "at another page size it is the whole list");
  // the whole list's addresses, as before
  assert.equal(at({}), "/not-ranked/");
  assert.equal(at({ ecosystem: "crates", page: 2 }), "/not-ranked/?eco=crates&page=2");
  assert.deepEqual(parseLocation("/not-ranked/", "?eco=pypi&page=2"), { tab: "notranked", ecosystem: "pypi", page: 2 });
  // as the app moves: a choice on a list by reason leaves it, at the whole list's page size
  const onList = { ...defaultState(), ...parseLocation("/not-ranked/stale/3/", "") };
  for (const [payload, address] of [
    [{ ecosystem: "crates", page: 1 }, "/not-ranked/?eco=crates"],
    [{ persona: "first_timer", page: 1 }, "/not-ranked/?persona=first_timer"],
    [{ query: "flask", page: 1 }, "/not-ranked/?q=flask"],
  ]) {
    const next = reduce(onList, { type: "SET_PARAMS", payload });
    assert.equal(serializeLocation(next), address, JSON.stringify(payload));
    assert.equal(next.notRankedReason, null, `${address}: the reason is left`);
  }
  // the tabs: Ranked from a list, and the whole list back from there; the Not ranked tab on a list is the list itself
  assert.equal(hrefFor(onList, "ranked"), "/");
  assert.equal(hrefFor(reduce(onList, { type: "SET_TAB", tab: "ranked" }), "notranked"), "/not-ranked/");
  assert.equal(hrefFor(onList, "notranked"), "/not-ranked/stale/3/");
  // another view never reads a reason
  assert.equal(serializeLocation({ ...defaultState(), notRankedReason: "stale", ecosystem: "crates", pageSize: 50 }), "/rust/");
});

test("not-ranked lists: a list is every row whose gate, else unrated reason, is its reason, in the API's order, 200 a page", () => {
  assert.equal(nrState.notRankedRowReason({ gate_reason: "black_hole", unrated_reason: null }), "black_hole");
  assert.equal(nrState.notRankedRowReason({ gate_reason: null, unrated_reason: "unrated: insufficient_evidence" }), "insufficient_evidence", "the prefixed spelling");
  assert.equal(nrState.notRankedRowReason({ gate_reason: null, unrated_reason: "off_platform_routed" }), "off_platform_routed");
  let all = 0;
  for (const [reason, n] of Object.entries(REASON_COUNTS)) {
    const rows = nrState.notRankedReasonRows(NR_LIST, reason);
    assert.equal(rows.length, n, reason);
    all += rows.length;
  }
  assert.equal(all, 6604, "every not-ranked project in one list");
  const withForks = notRankedFixture({ ...REASON_COUNTS, fork: 3, mirror: 2 });
  const listed = nrView.REASON_LISTS.flatMap(({ reason }) => nrState.notRankedReasonRows(withForks, reason).map((row) => `${row.owner}/${row.name}`));
  assert.equal(listed.length, 6609, "forks and mirrors have their lists");
  assert.equal(new Set(listed).size, withForks.gated.length + withForks.unrated.length, "every project exactly once");
  // the page slice, from the API's order (owner/name, lower case), never sorted again
  const stale = nrState.notRankedReasonRows(NR_LIST, "stale");
  const at = (page) => nrState.reasonListPage(atReason(`/not-ranked/stale/${page}/`, { notRanked: NR_LIST }));
  assert.deepEqual(at(2).rows, stale.slice(200, 400));
  assert.deepEqual({ ...at(13), rows: at(13).rows.length, route: at(13).route.id }, { route: "notranked-stale", reason: "stale", rows: 79, total: 2479, page: 13, pages: 13, pageSize: 200, first: 2401 });
  const order = [{ owner: "b", name: "x", gate_reason: "stale" }, { owner: "A", name: "y", gate_reason: "stale" }, { owner: "a", name: "z", gate_reason: "archived" }, { owner: "a", name: "a", gate_reason: "stale" }];
  const kept = nrState.reasonListPage(atReason("/not-ranked/stale/1/", { notRanked: { gated: order, unrated: [] } })).rows.map((row) => row.name);
  assert.deepEqual(kept, ["x", "y", "a"], "the API's order is kept");
  // a reason with no rows: one empty page
  const none = { ...NR_LIST, unrated: NR_LIST.unrated.filter((row) => row.unrated_reason !== "off_platform_routed") };
  const empty = reasonPageState("/not-ranked/off-platform/1/", { notRanked: none });
  assert.deepEqual(nrState.reasonListPage(empty).rows, []);
  assert.equal(nrState.reasonListPage(empty).pages, 1);
  const html = notRankedRenderShell(empty);
  assert.ok(html.includes('<ol class="reason-list" start="1" data-reason-rows="true"></ol>'), "an empty list of rows");
  assert.equal(reasonItemsOf(html).length, 0);
  // a page's rows are its items, in order, numbered from the page's first position
  const page2 = notRankedRenderShell(reasonPageState("/not-ranked/stale/2/"));
  assert.ok(page2.includes('<ol class="reason-list" start="201" data-reason-rows="true">'));
  assert.deepEqual(reasonItemsOf(page2).map((item) => `${item.owner}/${item.name}`), stale.slice(200, 400).map((row) => `${row.owner}/${row.name}`));
});

test("not-ranked lists: each row is one plain sentence: the reason, the coverage, or a black hole's own numbers", () => {
  const text = (row, report = REPORT_EXAMPLE) => nrView.reasonRowText({ owner: "acme", name: "widget", ecosystem: "go", gate_reason: null, unrated_reason: null, ...row }, report, NR_FLOOR);
  assert.equal(text({ gate_reason: "archived" }), "acme/widget (Go): Archived on GitHub — no active development to evaluate.");
  assert.equal(text({ unrated_reason: "unrated: insufficient_evidence", coverage: 0.42, ecosystem: "crates" }), "acme/widget (Rust): Not enough measured evidence to rank (coverage 42%, below the 60% floor).");
  assert.equal(text({ unrated_reason: "no_external_workflow", ecosystem: "systems" }), "acme/widget (C and C++): No external contribution workflow was found to measure.");
  assert.equal(
    nrView.reasonRowText({ owner: "3b1b", name: "manim", ecosystem: "pypi", gate_reason: "black_hole", unanswered: 26, counted: 41 }, REPORT_EXAMPLE),
    "3b1b/manim (Python): 26 of 41 counted outside pull requests got no human reply, review or merge within 14 days."
  );
  assert.equal(text({ gate_reason: "black_hole", unanswered: 1234, counted: 2479 }), "acme/widget (Go): 1,234 of 2,479 counted outside pull requests got no human reply, review or merge within 14 days.", "numbers grouped");
  const plain = `acme/widget (Go): ${copy.gateReasonText("black_hole")}`;
  assert.equal(text({ gate_reason: "black_hole", unanswered: null, counted: null }), plain, "without numbers: the plain form");
  assert.equal(text({ gate_reason: "black_hole" }), plain, "without the fields at all: the plain form");
  assert.equal(text({ gate_reason: "black_hole", unanswered: 3, counted: null }), plain, "half the numbers: the plain form");
  assert.equal(text({ gate_reason: "black_hole", unanswered: 26, counted: 41 }, null), plain, "without a report: the plain form");
  // drawn: each item's whole text is the row's sentence, and nothing but text
  const state = reasonPageState("/not-ranked/unanswered/1/");
  const rows = nrState.reasonListPage(state).rows;
  const items = reasonItemsOf(notRankedRenderShell(state));
  assert.equal(items.length, 200);
  items.forEach((item, i) => assert.equal(item.text, nrView.reasonRowText(rows[i], REPORT_EXAMPLE, NR_FLOOR), item.name));
  assert.ok(items.some((item) => /^\S+ \(\w+\): \d+ of \d+ counted/.test(item.text)) && items.some((item) => item.text.endsWith(copy.gateReasonText("black_hole"))), "both forms on the page");
  // a planted label or chip beside a name is found
  assert.throws(() => reasonItemsOf('<li data-owner="a" data-name="b">a/b <span class="eco-chip">Go</span>: Archived.</li>'), /holds markup/);
  assert.throws(() => reasonItemsOf('<li data-owner="a" data-name="b"><span class="reason-chip">Archived</span></li>'), /holds markup/);
});

test("not-ranked lists: heading, intro and hooks; the description is the intro's first two sentences; titles fit 70, descriptions 155", () => {
  const expected = {
    archived: ["Archived on GitHub — no active development to evaluate. 624 projects, A to Z, 200 a page.", { "list.total": "624" }],
    insufficient_evidence: ["Not enough measured evidence to rank. 1,198 projects, A to Z, 200 a page.", { "list.total": "1,198" }],
    black_hole: [
      "More than half of outside pull requests got no human reply, review or merge on GitHub within 14 days. 638 projects, A to Z, 200 a page. The test: more than half of at least 10 counted pull requests, at 95% confidence.",
      { "list.deadline_days": "14", "list.total": "638", "list.min_n": "10", "list.ci_percent": "95" },
    ],
    stale: [`${copy.gateReasonText("stale")} 2,479 projects, A to Z, 200 a page.`, { "list.total": "2,479" }],
    off_platform_routed: [`${copy.unratedReasonText("off_platform_routed")} 12 projects, A to Z, 200 a page.`, { "list.total": "12" }],
  };
  const titles = new Set();
  const allTitles = new Set();
  for (const [slug, reason] of REASON_LIST_SLUGS) {
    for (let page = 1; page <= pagesOf(reason); page++) {
      const state = reasonPageState(`/not-ranked/${slug}/${page}/`);
      const html = notRankedRenderShell(state);
      const h1 = html.match(/<h1 id="notranked-heading" class="view-heading">(.*?)<\/h1>/);
      assert.ok(h1, `${slug}: a visible h1`);
      assert.equal(listTextOf(h1[1]), `Not ranked: ${REASON_NAMES[reason]}`);
      assert.equal((html.match(/<h1/g) || []).length, 1, `${slug}: one h1`);
      const intro = html.match(/<p class="list-intro" data-list-intro="true">(.*?)<\/p>/)[1];
      if (expected[reason]) {
        assert.equal(listTextOf(intro), expected[reason][0], slug);
        assert.deepEqual(listHooksOf(intro), expected[reason][1], `${slug}: its hooks`);
      }
      const sentences = listTextOf(intro).split(/(?<=\.) (?=[A-Z0-9])/);
      assert.equal(nrView.reasonListDescription(state), sentences.slice(0, 2).join(" "), `${slug}: the description is the first two sentences`);
      assert.ok(nrView.reasonListDescription(state).length <= 155, `${slug}: the description fits 155`);
      const title = titleForState(state);
      const pageOf = pagesOf(reason) > 1 ? ` (page ${page} of ${pagesOf(reason)})` : "";
      assert.equal(title, `Not ranked: ${REASON_NAMES[reason]}${pageOf} | Porchlamp`, `${slug} ${page}`);
      assert.ok(title.length <= 70, `${title}: fits 70`);
      if (REASON_COUNTS[reason] > 0) titles.add(title);
      allTitles.add(title);
    }
  }
  assert.equal(titles.size, 38, "38 pages of the lists with projects, no two titles alike");
  assert.equal(allTitles.size, 40, "and the two empty lists' own titles, unlike them");
  // without a report the black-hole list says the reason in its own words and leaves the test out
  const bare = reasonPageState("/not-ranked/unanswered/1/", { report: null });
  const bareIntro = notRankedRenderShell(bare).match(/<p class="list-intro" data-list-intro="true">(.*?)<\/p>/)[1];
  assert.equal(listTextOf(bareIntro), `${copy.gateReasonText("black_hole")} 638 projects, A to Z, 200 a page.`);
  assert.deepEqual(listHooksOf(bareIntro), { "list.total": "638" });
  const noTest = { ...REPORT_EXAMPLE, values: { ...REPORT_EXAMPLE.values, min_n: undefined } };
  assert.equal(nrView.reasonListHead(reasonPageState("/not-ranked/unanswered/1/", { report: noTest })).sentences.length, 2, "no test without its min_n");
  // before the list has loaded: the heading, no intro, no description, and the view's own title
  const loading = atReason("/not-ranked/archived/2/", { leaderboard: HOME_LEADERBOARD });
  assert.ok(!notRankedRenderShell(loading).includes("list-intro"));
  assert.equal(nrView.reasonListDescription(loading), null);
  assert.equal(titleForState(loading), routeTitle("notranked"));
  assert.equal(nrView.reasonListHead(atReason("/not-ranked/", { notRanked: NR_LIST })), null, "the whole list has no list head");
});

test("not-ranked lists: the pages line, above and below the rows, links every page of the list and marks the one shown", () => {
  const html = notRankedRenderShell(reasonPageState("/not-ranked/stale/2/"));
  const lines = html.match(/<div class="list-pages" data-list-pages="true">.*?<\/div>/g) || [];
  assert.equal(lines.length, 2, "above and below the rows");
  assert.equal(lines[0], lines[1]);
  assert.ok(html.indexOf(lines[0]) < html.indexOf("<ol") && html.lastIndexOf(lines[1]) > html.indexOf("</ol>"));
  assert.ok(lines[0].startsWith('<div class="list-pages" data-list-pages="true"><span>The whole list, 200 projects a page:</span> '));
  const links = [...lines[0].matchAll(/<a href="([^"]+)" class="onboarding-link"( aria-current="page")?>(\d+)<\/a>/g)];
  assert.equal(links.length, 13);
  links.forEach(([, href, current, label], i) => {
    assert.equal(href, `/not-ranked/stale/${i + 1}/`);
    assert.equal(label, String(i + 1));
    assert.equal(Boolean(current), i === 1, `page ${i + 1} marked only when shown`);
  });
  assert.ok(!html.includes('class="pager'), "the links are the pager");
  const one = notRankedRenderShell(reasonPageState("/not-ranked/off-platform/1/")).match(/<div class="list-pages" data-list-pages="true">.*?<\/div>/)[0];
  assert.deepEqual([...one.matchAll(/<a [^>]*>/g)].map((m) => m[0]), ['<a href="/not-ranked/off-platform/1/" class="onboarding-link" aria-current="page">']);
});

test("not-ranked lists: the \"Lists by reason\" line links each list's first page with its balanced count, leaves out an empty list, on every not-ranked view", () => {
  const line = (html) => {
    const found = html.match(/<p class="reason-lists" data-reason-lists="true">(.*?)<\/p>/);
    assert.ok(found, "the line is drawn");
    return found[1];
  };
  const text =
    "Lists by reason: No recent human activity (2,479) · Not software (1,067) · Archived (624) · No open-source license (491) · " +
    "Unanswered pull requests (638) · Not enough evidence (1,198) · No outside contribution workflow (95) · Contributions happen off GitHub (12)";
  const views = [
    reasonPageState("/not-ranked/archived/2/"),
    atReason("/not-ranked/", { notRanked: NR_LIST, leaderboard: HOME_LEADERBOARD }),
    atReason("/not-ranked/", { ecosystem: "crates", notRanked: NR_LIST, leaderboard: HOME_LEADERBOARD }),
  ];
  for (const state of views) {
    const html = line(notRankedRenderShell(state));
    assert.equal(listTextOf(html), text, serializeLocation(state));
    const links = [...html.matchAll(/<a href="([^"]+)" class="onboarding-link">([^<]+)<\/a>/g)].map((m) => m[1]);
    assert.deepEqual(links, REASON_LIST_SLUGS.filter(([, reason]) => REASON_COUNTS[reason] > 0).map(([slug]) => `/not-ranked/${slug}/1/`), "no link to an empty list");
  }
  // a snapshot with forks: their list is linked, in the table's order; the empty mirror list is left out
  const forks = listTextOf(line(notRankedRenderShell(atReason("/not-ranked/", { notRanked: notRankedFixture({ ...REASON_COUNTS, fork: 3 }), leaderboard: HOME_LEADERBOARD }))));
  assert.ok(forks.includes("· Unanswered pull requests (638) · Fork of another repository (3) · Not enough evidence (1,198) ·"), forks);
  assert.doesNotMatch(forks, /Mirror/);
  assert.doesNotMatch(forks, /\(0\)/, "no list with a count of 0");
  // another lens's list is not the balanced one: every name, without counts
  const lens = atReason("/not-ranked/", { persona: "first_timer", notRanked: NR_LIST, leaderboard: HOME_LEADERBOARD });
  const lensLine = line(notRankedRenderShell(lens));
  assert.doesNotMatch(listTextOf(lensLine), /\d/);
  assert.equal((lensLine.match(/<a /g) || []).length, 10, "all ten names while the counts are not known");
});

test("not-ranked tab: it counts the list it opens, the ecosystem's on /rust/, and never another list's number", () => {
  const tabCount = (html) => html.match(/Not ranked<span class="count">([^<]*)<\/span>/)[1];
  const rustRows = [...NR_LIST.gated, ...NR_LIST.unrated].filter((row) => row.ecosystem === "crates").length;
  const all = HOME_COUNTS.gated + HOME_COUNTS.unrated;
  assert.notEqual(rustRows, all);
  // /rust/ with the not-ranked list loaded: Rust's own count, in the shell and in the prerendered board
  const rust = atAddress("/rust/", { leaderboard: listBoard(LIST_ROWS), notRanked: NR_LIST, snapshot: SNAPSHOT_DOC, report: REPORT_EXAMPLE, methodology: METHODOLOGY_DOC });
  assert.equal(tabCount(leaderboardRenderShell(rust, DESKTOP)), String(rustRows), "/rust/ shows Rust's count");
  assert.equal(tabCount(renderPrerenderedBoard(rust)), String(rustRows));
  // with an ecosystem or a search and no list loaded: no number, never the all-ecosystem one
  assert.equal(tabCount(leaderboardRenderShell({ ...rust, notRanked: null }, DESKTOP)), "", "/rust/ before the list loads");
  assert.equal(tabCount(leaderboardRenderShell(atAddress("/", { query: "flask", leaderboard: HOME_LEADERBOARD }), DESKTOP)), "", "a search before the list loads");
  // a search with the list loaded counts the matches
  const flask = atAddress("/", { query: "org-stale-000", leaderboard: HOME_LEADERBOARD, notRanked: NR_LIST });
  assert.equal(tabCount(leaderboardRenderShell(flask, DESKTOP)), "10");
  // no ecosystem or search: balanced is gated + unrated; a lens is the snapshot's total less what the lens ranks
  assert.equal(tabCount(leaderboardRenderShell(atAddress("/", { leaderboard: HOME_LEADERBOARD }), DESKTOP)), String(all));
  const career = atAddress("/career/", { leaderboard: listBoard(LIST_ROWS), snapshot: SNAPSHOT_DOC });
  const lensCount = SNAPSHOT_DOC.counts.total - SNAPSHOT_DOC.persona_visible_counts.career_capital;
  assert.equal(tabCount(leaderboardRenderShell(career, DESKTOP)), String(lensCount), "a lens list: total less the lens's ranked");
  assert.equal(nrState.notRankedTabCount({ ...career, snapshot: null }), null, "a lens without the snapshot: no number");
  // the not-ranked view counts the same list
  const nrRust = atReason("/not-ranked/", { ecosystem: "crates", notRanked: NR_LIST, leaderboard: HOME_LEADERBOARD });
  assert.equal(tabCount(notRankedRenderShell(nrRust)), String(rustRows));
  assert.equal(nrState.notRankedTabCount(nrRust), notRankedFlatten(NR_LIST, { ecosystem: "crates" }).length);
  assert.equal(renderTabs("ranked", HOME_COUNTS, 1, null).match(/Not ranked<span class="count">([^<]*)</)[1], "", "null draws no number");
});

test("not-ranked lists: the app takes over a list's prerendered page for its own state, and leaves a page of another list alone", () => {
  const state = reasonPageState("/not-ranked/archived/2/");
  const drawn = nrView.renderPrerenderedNotRanked(state);
  assert.ok(drawn.includes('data-prerendered-board="notranked-archived:2"'), "keyed by its list and page");
  assert.equal(drawn.replace(' data-prerendered-board="notranked-archived:2"', ""), notRankedRenderShell(state), "the board is the app's shell");
  /** A page holding `html`, read back as the document reads it (here: as written). */
  const pageWith = (html) => {
    const attrs = new Map([["data-prerendered-board", html.match(/data-prerendered-board="([^"]+)"/)[1]]]);
    const board = {
      getAttribute: (name) => (attrs.has(name) ? attrs.get(name) : null),
      removeAttribute: (name) => attrs.delete(name),
      get outerHTML() {
        return attrs.has("data-prerendered-board") ? html : html.replace(/ data-prerendered-board="[^"]+"/, "");
      },
    };
    const root = { written: null, querySelector: (sel) => (sel === "[data-prerendered-board]" ? board : null), set innerHTML(v) { this.written = v; } };
    return { root, board };
  };
  const doc = { createElement: () => ({ innerHTML: "" }) };
  for (const other of ["/not-ranked/archived/3/", "/not-ranked/stale/2/", "/not-ranked/"]) {
    const { root, board } = pageWith(drawn);
    assert.equal(nrView.adoptNotRankedBoard(root, reasonPageState(other), doc), false, other);
    assert.equal(board.getAttribute("data-prerendered-board"), "notranked-archived:2", `${other}: the page is left as it is`);
    assert.equal(root.written, null);
  }
  const { root, board } = pageWith(drawn);
  assert.equal(nrView.adoptNotRankedBoard(root, state, doc), true);
  assert.equal(board.getAttribute("data-prerendered-board"), null, "the mark is removed");
  assert.equal(root.written, null, "nothing redrawn");
  // the app's data differs (its report failed to load): the page is drawn from what the app has
  const differs = pageWith(drawn);
  const noReport = { ...state, report: null };
  assert.equal(nrView.adoptNotRankedBoard(differs.root, reasonPageState("/not-ranked/unanswered/1/", { report: null }), doc), false, "another list");
  const blackHole = pageWith(nrView.renderPrerenderedNotRanked(reasonPageState("/not-ranked/unanswered/1/")));
  assert.equal(nrView.adoptNotRankedBoard(blackHole.root, reasonPageState("/not-ranked/unanswered/1/", { report: null }), doc), true);
  assert.equal(blackHole.root.written, notRankedRenderShell(reasonPageState("/not-ranked/unanswered/1/", { report: null })), "redrawn from the app's data");
  assert.equal(nrView.rebuildKey(noReport), nrView.rebuildKey({ ...noReport }), "the shell's key is stable");
  assert.notEqual(nrView.rebuildKey(state), nrView.rebuildKey(reasonPageState("/not-ranked/archived/3/")), "and changes with the page");
  // app.js takes it over this way, from the not-ranked view's branch
  const app = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "app.js"), "utf8");
  assert.match(app, /prerenderedBoardKey\(main\) !== notRankedBoardKey\(state\)/);
  assert.match(app, /adoptNotRankedBoard\(main, state, doc\)/);
});

test("not-ranked: the whole list's page the export drew is the app's shell with its first page of rows, taken over for its own lens, filters and page", () => {
  const whole = reasonPageState("/not-ranked/", { report: null });
  const drawn = nrView.renderPrerenderedNotRanked(whole);
  const key = "notranked:balanced:::1:20";
  assert.ok(drawn.includes(`<section aria-labelledby="notranked-heading" data-prerendered-board="${key}">`), "keyed by lens, filters and page");
  // the shell, with the rows its patch puts in
  const container = { innerHTML: "" };
  nrView.patchNotRankedBody({ querySelector: (sel) => (sel === "[data-notranked-rows]" ? container : null), querySelectorAll: () => [] }, whole);
  assert.equal((container.innerHTML.match(/class="notranked-row"/g) || []).length, whole.notRankedPageSize, "a page of rows");
  assert.ok(container.innerHTML.includes(`data-owner="${NR_LIST.gated[0].owner}"`), "the first page of the rows");
  const app = notRankedRenderShell(whole).replace('<div data-notranked-rows="true"></div>', `<div data-notranked-rows="true">${container.innerHTML}</div>`);
  assert.equal(drawn.replace(` data-prerendered-board="${key}"`, ""), app, "the app's drawing, byte for byte");
  // the "Lists by reason" line as plain links: each list with projects, its first page
  const line = drawn.match(/<p class="reason-lists" data-reason-lists="true">(.*?)<\/p>/)[1];
  assert.deepEqual([...line.matchAll(/<a href="([^"]+)" class="onboarding-link">/g)].map((m) => m[1]),
    REASON_LIST_SLUGS.filter(([, reason]) => REASON_COUNTS[reason] > 0).map(([slug]) => `/not-ranked/${slug}/1/`));
  /** A page holding `html`, read back as the document reads it (here: as written). */
  const pageWith = (html) => {
    let mark = html.match(/data-prerendered-board="([^"]+)"/)[1];
    const board = {
      getAttribute: (name) => (name === "data-prerendered-board" ? mark : null),
      removeAttribute: () => { mark = null; },
      get outerHTML() {
        return mark === null ? html.replace(/ data-prerendered-board="[^"]+"/, "") : html;
      },
    };
    const root = { written: null, querySelector: (sel) => (sel === "[data-prerendered-board]" ? board : null), set innerHTML(v) { this.written = v; } };
    return { root, board };
  };
  const doc = { createElement: () => ({ innerHTML: "" }) };
  // another lens, ecosystem, search, page or page size: the page is left as it is, for the app to draw
  for (const other of [{ persona: "first_timer" }, { ecosystem: "crates" }, { query: "org" }, { notRankedPage: 2 }, { notRankedPageSize: 50 }]) {
    const { root, board } = pageWith(drawn);
    assert.equal(nrView.adoptNotRankedBoard(root, { ...whole, ...other }, doc), false, JSON.stringify(other));
    assert.equal(board.getAttribute("data-prerendered-board"), key);
    assert.equal(root.written, null);
  }
  assert.equal(nrView.adoptNotRankedBoard(pageWith(drawn).root, reasonPageState("/not-ranked/stale/1/"), doc), false, "a list by reason");
  const { root, board } = pageWith(drawn);
  assert.equal(nrView.adoptNotRankedBoard(root, whole, doc), true);
  assert.equal(board.getAttribute("data-prerendered-board"), null, "the mark is removed");
  assert.equal(root.written, null, "nothing redrawn");
  // the app's counts differ from the export's: drawn again from the app's data, the rows with it
  const counts = { ...HOME_COUNTS, gated: HOME_COUNTS.gated + 1 };
  const moved = { ...whole, leaderboard: { ...HOME_LEADERBOARD, counts } };
  const again = pageWith(drawn);
  assert.equal(nrView.adoptNotRankedBoard(again.root, moved, doc), true);
  assert.equal(again.root.written, notRankedRenderShell(moved).replace('<div data-notranked-rows="true"></div>', `<div data-notranked-rows="true">${container.innerHTML}</div>`));
  // app.js waits for the report on a list by reason only: the whole list never loads it
  const appJs = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "app.js"), "utf8");
  assert.match(appJs, /\(reasonListKey\(state\) && !state\.report && !state\.reportError\)/);
});

test("not-ranked lists: the prerender draws a list's page from its address: the view, the title and the description", () => {
  const frame = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "404.html"), "utf8");
  const data = { snapshot: SNAPSHOT_DOC, report: REPORT_EXAMPLE, leaderboard: HOME_LEADERBOARD, notRanked: NR_LIST };
  const address = "/not-ranked/archived/2/";
  const page = drawPage(frame, { path: "not-ranked/archived/2/index.html", view: "notranked", address }, data);
  const state = { ...nrPrerender.stateForAddress(address), ...data };
  assert.ok(page.includes('<main class="app-main" id="app-main" tabindex="-1" data-prerendered="notranked">'));
  assert.ok(page.includes(nrView.renderPrerenderedNotRanked(state)), "the view of its state");
  assert.equal(reasonItemsOf(page).length, 200);
  assert.equal(decodeAttr(page.match(/<title>([^<]*)<\/title>/)[1]), "Not ranked: archived (page 2 of 4) | Porchlamp");
  assert.equal(decodeAttr(page.match(/<meta name="description" content="([^"]*)">/)[1]), "Archived on GitHub — no active development to evaluate. 624 projects, A to Z, 200 a page.");
  assert.equal((page.match(/<title>/g) || []).length, 1);
  assert.equal(chromeFor({ path: "not-ranked/archived/2/index.html", view: "notranked", address }, SNAPSHOT_DOC).header, renderAppHeader("notranked", SNAPSHOT_DOC, state));
  // the whole list's page: the view of its state, with the "Lists by reason" line as plain links to each list's first
  // page; the title its route gives, and the description the export wrote (the whole list has none of its own)
  const whole = drawPage(frame, { path: "not-ranked/index.html", view: "notranked", address: "/not-ranked/" }, data);
  const wholeState = { ...nrPrerender.stateForAddress("/not-ranked/"), ...data };
  assert.ok(whole.includes('<main class="app-main" id="app-main" tabindex="-1" data-prerendered="notranked">'));
  assert.ok(whole.includes(nrView.renderPrerenderedNotRanked(wholeState)), "the view of its state");
  const firstPages = REASON_LIST_SLUGS.filter(([, reason]) => REASON_COUNTS[reason] > 0).map(([slug]) => `/not-ranked/${slug}/1/`);
  for (const href of firstPages) assert.ok(whole.includes(`<a href="${href}" class="onboarding-link">`), href);
  assert.equal(decodeAttr(whole.match(/<title>([^<]*)<\/title>/)[1]), "Not ranked: the gated and unrated projects, with their reasons | Porchlamp");
  assert.equal(whole.match(/<meta name="description" content="([^"]*)">/)[1], frame.match(/<meta name="description" content="([^"]*)">/)[1]);
  assert.equal(chromeFor({ path: "not-ranked/index.html", view: "notranked", address: "/not-ranked/" }, SNAPSHOT_DOC).header, renderAppHeader("notranked", SNAPSHOT_DOC, null), "the header as on any not-ranked page");
  assert.throws(() => nrPrerender.stateForAddress("/projects/acme/tool/"), /not the address of a list/);
  // a list with an ecosystem draws the Not ranked tab with the ecosystem's count from the same list
  const rust = drawPage(frame, { path: "rust/index.html", view: "ranked", address: "/rust/" }, { ...data, methodology: METHODOLOGY_DOC, leaderboard: listBoard(LIST_ROWS) });
  const rustRows = [...NR_LIST.gated, ...NR_LIST.unrated].filter((row) => row.ecosystem === "crates").length;
  assert.ok(rust.includes(`Not ranked<span class="count">${rustRows}</span>`), "the ecosystem's count");
  assert.equal(nrPrerender.needsNotRanked(nrPrerender.stateForAddress("/rust/")), true);
  assert.equal(nrPrerender.needsNotRanked(nrPrerender.stateForAddress("/career/")), false);
});

test("not-ranked: the insufficient-evidence reason names the methodology's coverage floor, in every view that prints it", () => {
  assert.equal(copy.unratedCoverageFloor(METHODOLOGY_DOC), 0.6, "methodology.json missingness.coverage_thresholds.unrated_below");
  assert.equal(copy.unratedCoverageFloor(null), null);
  assert.equal(copy.unratedCoverageFloor({ missingness: {} }), null);
  assert.equal(copySourceString("UNRATED_COVERAGE_BELOW_FLOOR"), "(coverage {coverage}, below the {floor} floor).");
  assert.equal(copy.unratedReasonText("unrated: insufficient_evidence", 0.42, 0.6), "Not enough measured evidence to rank (coverage 42%, below the 60% floor).");
  assert.equal(copy.unratedReasonText("insufficient_evidence", 0.42, 0.55), "Not enough measured evidence to rank (coverage 42%, below the 55% floor).", "a whole percent of the floor given");
  assert.equal(copy.unratedReasonText("insufficient_evidence", 0.42), "Not enough measured evidence to rank.", "no floor: the reason alone, never a typed floor");
  // RFC-0024: unrated with coverage at or above the floor means no measured responsiveness parameter
  assert.equal(copySourceString("UNRATED_NO_RESPONSIVENESS"), "(coverage {coverage}, but nothing measured on how it answers outside pull requests).");
  assert.equal(copy.unratedReasonText("insufficient_evidence", 0.72, 0.6), "Not enough measured evidence to rank (coverage 72%, but nothing measured on how it answers outside pull requests).");
  assert.equal(copy.unratedReasonText("unrated: insufficient_evidence", 0.6, 0.6), "Not enough measured evidence to rank (coverage 60%, but nothing measured on how it answers outside pull requests).", "the floor itself is reached");
  assert.equal(copy.unratedReasonText("insufficient_evidence", 0.5999, 0.6), "Not enough measured evidence to rank (coverage 60%, below the 60% floor).", "just under the floor keeps the floor clause");
  assert.equal(copy.unratedReasonText("off_platform_routed", 0.42, 0.6), copy.unratedReasonText("off_platform_routed", 0.42), "other reasons have no floor");
  // a planted floor of 0.5 is what every caller prints
  const planted = { ...METHODOLOGY_DOC, missingness: { ...METHODOLOGY_DOC.missingness, coverage_thresholds: { ...METHODOLOGY_DOC.missingness.coverage_thresholds, unrated_below: 0.5 } } };
  const says = (html) => {
    assert.ok(html.includes("below the 50% floor"), "the planted floor is printed");
    assert.ok(!html.includes("60%"), "and no other");
  };
  // a list by reason
  says(notRankedRenderShell(reasonPageState("/not-ranked/insufficient-evidence/1/", { methodology: planted })));
  // the whole not-ranked list's rows (the chip), as its patch draws them
  const container = { innerHTML: "" };
  const root = { querySelector: (sel) => (sel === "[data-notranked-rows]" ? container : null), querySelectorAll: () => [] };
  const unrated = { ...NR_LIST, gated: [] };
  nrView.patchNotRankedBody(root, atReason("/not-ranked/", { notRanked: unrated, methodology: planted }));
  says(container.innerHTML);
  // compare: the status row and the summary card
  const detail = { ...FLAT_RATED_DETAIL, owner: "acme", name: "tool", rated: false, rank: null, porchlamp: null, unrated_reason: "insufficient_evidence", coverage: 0.42 };
  const compare = renderCompareTable({
    persona: "balanced",
    compareRepos: ["acme/tool", "acme/tool2"],
    compareDataByRepo: { "acme/tool": { row: detail, evidence: [] }, "acme/tool2": { row: { ...detail, name: "tool2" }, evidence: [] } },
    methodology: planted,
  });
  assert.equal((compare.match(/below the 50% floor/g) || []).length, 4, "two status cells and two summary cards");
  says(compare);
  // the project panel's banner
  says(renderDrawer({ ...defaultState(), repo: "acme/tool", repoDetailByKey: { "acme/tool": detail }, evidenceByRepo: { "acme/tool": [] }, methodology: planted }));
  // before the methodology has loaded: the reason alone, in the app and nowhere a guessed floor
  assert.ok(!notRankedRenderShell(reasonPageState("/not-ranked/insufficient-evidence/1/", { methodology: null })).includes("floor"));
  // the prerendered list says what the app says, from the same methodology
  const frame = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "404.html"), "utf8");
  const data = { snapshot: SNAPSHOT_DOC, report: REPORT_EXAMPLE, leaderboard: HOME_LEADERBOARD, notRanked: NR_LIST, methodology: planted };
  const page = drawPage(frame, { path: "not-ranked/insufficient-evidence/1/index.html", view: "notranked", address: "/not-ranked/insufficient-evidence/1/" }, data);
  says(page);
  const appState = reasonPageState("/not-ranked/insufficient-evidence/1/", { methodology: planted });
  const board = nrView.renderPrerenderedNotRanked(appState);
  assert.ok(page.includes(board), "the prerendered board of the app's state");
  assert.equal(board.replace(/ data-prerendered-board="[^"]+"/, ""), notRankedRenderShell(appState), "the app's text, byte for byte");
  // app.js loads it for the not-ranked view and a project's panel, and the takeover waits for it
  const app = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "app.js"), "utf8");
  assert.match(app, /if \(tab === "notranked"\) loadMethodologyOnce\(\);/);
  const panelLoad = app.slice(app.indexOf("function loadRepoDetail(owner, name) {"), app.indexOf("function loadRepoDetail(owner, name) {") + 300);
  assert.match(panelLoad, /^function loadRepoDetail[\s\S]*\n\s*loadMethodologyOnce\(\);/, "a panel's record load asks for the methodology");
  assert.match(app, /\(!state\.methodology && !state\.methodologyError\);/);
});

test("not-ranked lists: the page size printed in the intro and the pages line is the route's own", () => {
  const state = reasonPageState("/not-ranked/stale/1/");
  const at = nrState.reasonListPage(state);
  assert.equal(at.pageSize, routeById("notranked-stale").fixed.page_size, "the route's fixed page_size");
  const fifty = { ...at, pageSize: 50, pages: Math.ceil(at.total / 50), rows: at.rows.slice(0, 50) };
  const html = nrView.renderReasonListShell(state, fifty);
  const intro = listTextOf(html.match(/<p class="list-intro" data-list-intro="true">(.*?)<\/p>/)[1]);
  assert.ok(intro.endsWith("2,479 projects, A to Z, 50 a page."), intro);
  assert.ok(html.includes("<span>The whole list, 50 projects a page:</span>"));
  assert.equal([...html.match(/<div class="list-pages" data-list-pages="true">.*?<\/div>/)[0].matchAll(/<a /g)].length, 50, "2,479 at 50 a page");
  assert.ok(!html.includes("200"), "no 200 anywhere at 50 a page");
});
