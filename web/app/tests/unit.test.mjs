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
import { renderShell as notRankedRenderShell } from "../views/notranked.js";
import { centeredScrollLeft, renderNav, navEntryFor, renderFooter, renderHeader, renderAppHeader, headerControlsFor } from "../components/chrome.js";
import { ROUTES, matchRoute, projectPath, routeById, routePath, routeTitle } from "../routes.js";
import { postReproject } from "../api.js";
import { normalizeWeights, applyPresetRespectingLocks, renderControls, renderShell as renderConfigureShell, sortReprojectRows, filterReprojectRows, renderPager as renderConfigurePager, topMovers, remainingReprojectPages, mergeReprojectPages } from "../views/configure.js";
import { PILLAR_IDS, PERSONA_PRESET_WEIGHTS, BALANCED_WEIGHTS } from "../state.js";
import { nearestMarkerInDirection, hitTestPoint, renderMatrixBody, renderShell as renderMatrixShell, shellShape as matrixShellShape, clientToDomainCoords } from "../views/matrix.js";
import { pointKey, SVG_MARKER_BUDGET, toSvgX, toSvgY, renderScatterSvg } from "../components/scatter.js";
import { synthesizeRows } from "./synthetic-rows.js";
import { shellKey as compareShellKey, shellShape as compareShellShape, renderShell as renderCompareShell, computeVerdict } from "../views/compare.js";
import { renderDrawer, classifyKind, classifyKindForLens, renderPillarRadar, findRow, renderBlackHoleEvidence } from "../views/drawer.js";
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
  assert.equal(address, "/?persona=first_timer&eco=pypi&q=flask&sort=-porchlamp&page=3&page_size=100");
  const parsed = parseLocation("/", address.slice(1));
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

  // An unrated repo renders open Pillars and evidence plus collapsed Persona view and Integrity,
  // with Composite and Playbook omitted.
  const detailsTags = html.match(/<details[^>]*>/g) || [];
  assert.equal(detailsTags.length, 3, "unrated renders Pillars and evidence (open) + Persona view + Integrity");
  assert.ok(/\bopen\b/.test(detailsTags[0]), "unrated's Pillars and evidence section is open");
  assert.ok(!/\bopen\b/.test(detailsTags[1]), "unrated's Persona view section is collapsed by default");
  assert.ok(!/\bopen\b/.test(detailsTags[2]), "unrated's Integrity section is collapsed by default");
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
function methodologyShell(snapshot, doc = METHODOLOGY_DOC) {
  return renderMethodologyShell({ methodology: doc, snapshot, methodologyLoading: false, methodologyError: null });
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
  assert.match(html, /<main class="app-main" id="app-main" tabindex="-1">\s*<article class="seo-fallback">/);
  assert.match(html, /Stars don't review pull requests/);
  const png = readFileSync(path.join(here, "..", "og.png"));
  assert.equal(png.subarray(1, 4).toString(), "PNG");
  assert.equal(png.readUInt32BE(16), 1200, "og:image:width matches the file");
  assert.equal(png.readUInt32BE(20), 630, "og:image:height matches the file");
});

test('SEO: "Porchlamp rankings" leads the title, description, static h1, structured data and the leaderboard heading', () => {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const html = readFileSync(path.join(here, "..", "index.html"), "utf8");
  const title = html.match(/<title>([^<]*)<\/title>/)[1];
  const description = html.match(/<meta name="description" content="([^"]*)"/)[1];
  assert.match(title, /^Porchlamp rankings\b/);
  assert.ok(title.length <= 65, `title is ${title.length} chars, search results cut it near 60`);
  assert.match(description, /^Porchlamp rankings: /);
  assert.ok(description.length <= 160, `description is ${description.length} chars`);
  assert.match(html, /<h1>Porchlamp rankings<\/h1>/);
  const ld = JSON.parse(html.match(/<script type="application\/ld\+json">\s*([\s\S]*?)\s*<\/script>/)[1]);
  const site = ld["@graph"].find((n) => n["@type"] === "WebSite");
  assert.ok(site.alternateName.includes("Porchlamp rankings"));
  assert.equal(ld["@graph"].find((n) => n["@type"] === "WebPage").name, title);
  assert.equal(copy.LEADERBOARD_HEADING, "Porchlamp rankings");
  const view = readFileSync(path.join(here, "..", "views", "leaderboard.js"), "utf8");
  assert.match(view, /id="leaderboard-heading"[^`]*\$\{escapeHtml\(copy\.LEADERBOARD_HEADING\)\}/);
});

// E7: the Dataset lives on the report page (its licence is pinned in tests/test_report_pages.py); the home page
// keeps the site, the person, the page and the top-10 list, and points the page's `about` at the report's Dataset.
test("E7: the home JSON-LD has no SoftwareApplication or Dataset, names the report's Dataset, and lists project pages", () => {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const html = readFileSync(path.join(here, "..", "index.html"), "utf8");
  const base = html.match(/<link rel="canonical" href="([^"]*)">/)[1];
  const ld = JSON.parse(html.match(/<script type="application\/ld\+json">\s*([\s\S]*?)\s*<\/script>/)[1]);
  const types = ld["@graph"].map((n) => n["@type"]);
  assert.ok(!types.includes("SoftwareApplication"), "no software-application markup");
  assert.ok(!types.includes("Dataset"), "the report page carries the Dataset");
  assert.deepEqual(types, ["Person", "WebSite", "WebPage", "ItemList"]);
  const webpage = ld["@graph"].find((n) => n["@type"] === "WebPage");
  assert.deepEqual(webpage.about, { "@id": `${base}black-holes/#dataset` }, "about names the report's Dataset @id");
  const items = ld["@graph"].find((n) => n["@type"] === "ItemList").itemListElement;
  assert.equal(items.length, 10);
  for (const item of items) {
    assert.ok(item.url.startsWith(`${base}projects/`), item.url);
    assert.match(item.url.slice(base.length), /^projects\/[a-z0-9._-]+\/[a-z0-9._-]+\/$/, "a lower-case project page address");
  }
  assert.ok(!JSON.stringify(ld).includes("?repo="), "no address in the JSON-LD is a ?repo= link");
});

// The ten landing pages (porchlamp/serve/landing_pages.py): six ecosystems, then four lenses.
const ECOSYSTEM_SLUGS = ["rust", "python", "javascript", "go", "java", "c-cpp"];
const LANDING_SLUGS = [...ECOSYSTEM_SLUGS, "first-timers", "career", "senior-systems", "ai-assisted"];

test("SEO: static canonical, root robots.txt on the site's own domain, canonical-only sitemap, lean head, Search Console verification, and U-1/U-2/U-6 static fallback", () => {
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
  for (const f of ["index.html", "sitemap.xml", "feed.xml", "llms.txt", "robots.txt"]) {
    assert.ok(!readFileSync(path.join(webRoot, f), "utf8").includes("pctablet505.github.io/porchlamp"), `${f} keeps no old github.io address`);
  }

  // 2a. Lean head: no meta keywords, no googlebot/bingbot copies of the robots tag, no hreflang on a one-language site.
  assert.doesNotMatch(html, /<meta name="(keywords|googlebot|bingbot)"/, "no ignored or duplicate meta tags");
  assert.doesNotMatch(html, /hreflang=/, "no hreflang links");
  assert.match(html, /<meta name="robots" content="index, follow/, "one robots meta tag stays");

  // 2b. JSON-LD @graph, feed.xml, llms.txt, and IndexNow key file
  const ldMatch = html.match(/<script type="application\/ld\+json">\s*([\s\S]*?)\s*<\/script>/);
  assert.ok(ldMatch, "JSON-LD structured data block present in index.html");
  const ldJson = JSON.parse(ldMatch[1]);
  assert.ok(Array.isArray(ldJson["@graph"]) && ldJson["@graph"].length >= 4, "JSON-LD @graph contains rich entities");
  assert.ok(!ldJson["@graph"].some((n) => n["@type"] === "FAQPage"), "no FAQPage markup for text the page does not show");
  const feedXml = readFileSync(path.join(webRoot, "feed.xml"), "utf8");
  assert.ok(feedXml.includes(`<id>${base}</id>`), "feed.xml uses canonical base id");
  const llmsTxt = readFileSync(path.join(webRoot, "llms.txt"), "utf8");
  // The values are the snapshot's (checked by scripts/verify_static_export.py check_hand_written); here only the shape.
  assert.match(llmsTxt, /\d{1,3}(,\d{3})* pull requests from \d{1,3}(,\d{3})* outside contributors/, "llms.txt documents empirical dataset");
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
  const snapshotId = html.match(/Snapshot <code>([\d-]+)<\/code>/)[1];
  const pageAddresses = [
    `${base}black-holes/`, `${base}black-holes/${snapshotId}/`, `${base}methodology/`, `${base}check/`,
    ...LANDING_SLUGS.map((slug) => `${base}${slug}/`),
  ];
  const llms = readFileSync(path.join(webRoot, "llms.txt"), "utf8");
  for (const loc of pageAddresses) assert.ok(llms.includes(loc), `llms.txt names ${loc}`);
  for (const loc of children) assert.ok(!loc.includes("?"), `no query address in the sitemap index: ${loc}`);
  assert.doesNotMatch(sitemap, /<(priority|changefreq)>/, "no priority or changefreq");
  assert.ok(
    llms.includes(`To check a project, open ${base}projects/<owner>/<name>/ (lower case).`),
    "llms.txt states the address of a project's page"
  );

  // 5. index.html .seo-fallback: every internal link is canonical, U-2 excluded counts and U-6 hashes are present,
  //    and all 20 rows carry U-1 coverage, tier, and ?repo= drawer links.
  const articleMatch = html.match(/<article class="seo-fallback">([\s\S]*?)<\/article>/);
  assert.ok(articleMatch, ".seo-fallback article exists");
  const article = articleMatch[1];
  // The values are checked against the snapshot's data by scripts/verify_static_export.py (check_hand_written).
  assert.match(article, /[\d,]+ ranked, [\d,]+ gated, and [\d,]+ unrated of [\d,]+ evaluated/, "U-2 excluded counts");
  assert.match(article, /python -m porchlamp\.snapshot reproduce \d{4}-\d\d-\d\d\w*/, "U-6 reproduce command");

  const hrefs = [...article.matchAll(/<a href="([^"]+)">/g)].map((m) => m[1]);
  assert.ok(hrefs.length >= 30, "persona, tab, ecosystem, and top-20 repo links");
  // S2: the static article links the report after the findings and the methodology page where the numbers come from.
  assert.match(
    article,
    /<\/ul>\s*<p><a href="black-holes\/">Read the report: Stars don't review pull requests<\/a><\/p>/,
    "report link follows the findings list"
  );
  // E4: one line links the six ecosystem pages.
  assert.match(
    article,
    /Browse by ecosystem:\s*<a href="rust\/">Rust<\/a> ·\s*<a href="python\/">Python<\/a> ·\s*<a href="javascript\/">JavaScript and TypeScript<\/a> ·\s*<a href="go\/">Go<\/a> ·\s*<a href="java\/">Java<\/a> ·\s*<a href="c-cpp\/">C, C\+\+ and Zig<\/a>/,
    "the Browse by ecosystem line"
  );
  const whereFrom = article.match(/<h2>Where the numbers come from<\/h2>([\s\S]*?)<h2>/)[1];
  assert.match(whereFrom, /<a href="methodology\/">[^<]+<\/a>/, "methodology link in 'Where the numbers come from'");
  const pageLinks = ["black-holes/", ...ECOSYSTEM_SLUGS.map((slug) => `${slug}/`)];
  for (const href of hrefs) {
    if (href === "./" || pageLinks.includes(href) || /^projects\/[^/]+\/[^/]+\/$/.test(href)) continue;
    // Every other link is a view of the app (routes.js), already at its one address.
    const url = new URL(href, base);
    const hydrated = { ...defaultState(), ...parseLocation(url.pathname, url.search) };
    assert.notEqual(hydrated.tab, "notfound", `fallback link ${href} names a view`);
    assert.equal(
      serializeLocation(hydrated),
      `${url.pathname}${url.search}`,
      `fallback link ${href} must already be in canonical serializeLocation form`
    );
  }

  const rows = [...article.match(/<tbody>([\s\S]*?)<\/tbody>/)[1].matchAll(/<tr>([\s\S]*?)<\/tr>/g)];
  assert.equal(rows.length, 20, "top-20 balanced repositories pre-rendered");
  rows.forEach((rowMatch, idx) => {
    const cells = [...rowMatch[1].matchAll(/<td>([\s\S]*?)<\/td>/g)].map((m) => m[1]);
    assert.equal(cells.length, 7, `row ${idx + 1} has 7 columns (Rank, Repository, Ecosystem, Porchlamp, Coverage, Tier, Quadrant)`);
    assert.equal(cells[0], String(idx + 1), "rank cell");
    const link = cells[1].match(/^<a href="([^"]+)">([^<]+\/[^<]+)<\/a>$/);
    assert.ok(link, "U-1 repo cell is one link");
    assert.equal(link[1], `projects/${link[2].toLowerCase()}/`, "U-1 repo cell links to the project's own page, as the app's rows do");
    assert.match(cells[3], /^\d+\.\d{2}$/, "Porchlamp score formatted to 2 decimals");
    assert.match(cells[4], /^\d+%$/, "U-1 coverage percentage present");
    assert.match(cells[5], /^(?:S\+|S|A|B|C)(?: \(capped\))?$/, "U-1 tier with ceiling note present");
    assert.equal(cells[6], "Sweet Spot");
  });
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
  assert.equal(deriveTierCeiling(0.96), null);
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
  const composite = html.indexOf(`<summary>${escapeHtml(copy.DRAWER_SECTION_COMPOSITE)}</summary>`);
  const pillars = html.indexOf(`<summary>${escapeHtml(copy.DRAWER_SECTION_PILLARS)}</summary>`);
  assert.ok(composite > -1 && composite < box && box < pillars, "the badge box is inside the Composite section");
  assert.equal((html.match(/<details[^>]*>/g) || []).length, 5, "a rated drawer still has five sections");
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

test("renderBlackHoleEvidence: renders exact counts, percentage, Wilson lower bound, window dates, exclusions, and PR links without contributor usernames", () => {
  const html = renderBlackHoleEvidence(FLAT_BLACK_HOLE_DETAIL);
  assert.ok(html.includes('data-black-hole-evidence="true"'));
  assert.ok(html.includes(copy.BLACK_HOLE_EVIDENCE_HEADING));
  assert.ok(html.includes(copy.BLACK_HOLE_RULE_TEXT));
  assert.ok(html.includes("12 of 15 (80.0%) external non-bot PRs had no maintainer response, merge, or close within 14 days"));
  assert.ok(html.includes("95% Wilson lower confidence bound: 59.8% (threshold: &gt; 50.0%, min n = 10)"));
  assert.ok(html.includes("2026-03-01T00:00:00Z – 2026-09-01T00:00:00Z"));
  assert.ok(html.includes("Source: raw/pr_window/ghost-org__abandoned-lib.json"));
  assert.ok(html.includes(escapeHtml(copy.BLACK_HOLE_EXCLUSIONS_TEXT)));
  assert.ok(html.includes("2 draft"));
  assert.ok(html.includes("1 author self-closed"));
  assert.ok(html.includes("3 new account"));
  assert.ok(html.includes("4 bot"));
  assert.ok(html.includes("5 internal/maintainer"));
  assert.ok(html.includes("6 open &lt;14d"));
  assert.ok(html.includes(copy.BLACK_HOLE_RECHECK_TEXT));

  // PR links (#101, #102)
  assert.ok(html.includes('<a href="https://github.com/ghost-org/abandoned-lib/pull/101" target="_blank" rel="noopener noreferrer">#101</a>'));
  assert.ok(html.includes('<a href="https://github.com/ghost-org/abandoned-lib/pull/102" target="_blank" rel="noopener noreferrer">#102</a>'));

  // Privacy: never include contributor usernames/logins
  for (const forbiddenLogin of ["octocat", "author", "contributor", "login", "maintainer_login"]) {
    assert.ok(!html.includes(`@${forbiddenLogin}`));
  }
});

test("renderBlackHoleEvidence: escapes untrusted strings against XSS", () => {
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
  const html = renderBlackHoleEvidence(xssDetail);
  assert.ok(!html.includes("<script>alert("));
  assert.ok(!html.includes("<img src=x"));
  assert.ok(html.includes("&lt;script&gt;alert("));
  assert.ok(html.includes("&lt;img src=x"));
});

test("renderBlackHoleEvidence: data recorded before the RFC-0008 fields hides the draft and self-closed counts and says so", () => {
  assert.equal(typeof copy.BLACK_HOLE_RULE_INPUTS_NOT_MEASURED_TEXT, "string");
  assert.ok(!copy.BLACK_HOLE_EXCLUSIONS_TEXT.includes("applied"));
  const notMeasured = renderBlackHoleEvidence({
    ...FLAT_BLACK_HOLE_DETAIL,
    black_hole_evidence: { ...FLAT_BLACK_HOLE_DETAIL.black_hole_evidence, rule_inputs_measured: false },
  });
  assert.ok(notMeasured.includes(escapeHtml(copy.BLACK_HOLE_RULE_INPUTS_NOT_MEASURED_TEXT)));
  assert.ok(!notMeasured.includes("2 draft"));
  assert.ok(!notMeasured.includes("author self-closed"));
  assert.ok(notMeasured.includes("3 new account"));
  const measured = renderBlackHoleEvidence(FLAT_BLACK_HOLE_DETAIL);
  assert.ok(!measured.includes(escapeHtml(copy.BLACK_HOLE_RULE_INPUTS_NOT_MEASURED_TEXT)));
  assert.ok(measured.includes("2 draft"));
});

test("renderBlackHoleEvidence: an XSS payload in counted_prs[].created_at is rendered inert", () => {
  const evidence = FLAT_BLACK_HOLE_DETAIL.black_hole_evidence;
  const html = renderBlackHoleEvidence({
    ...FLAT_BLACK_HOLE_DETAIL,
    black_hole_evidence: {
      ...evidence,
      counted_prs: [{ ...evidence.counted_prs[0], created_at: '<svg onload=alert("created")>' }],
    },
  });
  assert.ok(!html.includes("<svg onload="));
  assert.ok(html.includes("&lt;svg onload="));
});

test("renderBlackHoleEvidence: an owner name that needs URL encoding produces an encoded PR link", () => {
  const html = renderBlackHoleEvidence({ ...FLAT_BLACK_HOLE_DETAIL, owner: "org with space&co" });
  assert.ok(html.includes('href="https://github.com/org%20with%20space%26co/abandoned-lib/pull/101"'));
});

test("renderDrawer: a black_hole gated repo renders the evidence panel while preserving 3 collapsed <details> sections and gate sentence precedence", () => {
  const state = stateWithRepoDetail(FLAT_BLACK_HOLE_DETAIL);
  const html = renderDrawer(state);

  const gateSentence = copy.gateReasonText(FLAT_BLACK_HOLE_DETAIL.gate_reason);
  const gateSentenceIndex = html.indexOf(gateSentence);
  const firstDetailsIndex = html.indexOf("<details");
  const evidencePanelIndex = html.indexOf('data-black-hole-evidence="true"');

  assert.ok(gateSentenceIndex >= 0, "the gate reason sentence must render");
  assert.ok(evidencePanelIndex >= 0, "black-hole evidence panel must render");
  assert.ok(firstDetailsIndex >= 0, "at least one <details> section must render");
  assert.ok(gateSentenceIndex < firstDetailsIndex, "gate sentence must render before any <details> section");
  assert.ok(evidencePanelIndex < firstDetailsIndex, "evidence panel must render before any <details> section");

  // Preserves exactly 3 <details> sections
  const detailsTags = html.match(/<details(?:\s+open)?>/g) || [];
  assert.equal(detailsTags.length, 3, "must preserve exactly 3 <details> sections (pillars, personas, integrity)");

  // Evidence panel does NOT repeat the exact gateReasonText("black_hole")
  const evidenceBox = html.slice(evidencePanelIndex, html.indexOf("</div>", evidencePanelIndex + 100));
  assert.ok(!evidenceBox.includes(gateSentence), "evidence panel must not repeat exact gate reason sentence");
});

test("renderBlackHoleEvidence: falls back to gr14_ghost_rate from evidenceRows when black_hole_evidence is absent", () => {
  const noEvidenceDetail = {
    ...FLAT_BLACK_HOLE_DETAIL,
    black_hole_evidence: null,
  };
  const evidenceRows = [
    {
      param_id: "gr14_ghost_rate",
      value: 0.75,
      score: 25.0,
      n: 20,
      window_start: "2026-03-01T00:00:00Z",
      window_end: "2026-09-01T00:00:00Z",
      method: "windowed_share",
      source: "raw/pr_window/ghost-org__abandoned-lib.json",
      source_sha256: "d".repeat(64),
      fetched_at: "2026-09-01T01:00:00Z",
    },
  ];
  const html = renderBlackHoleEvidence(noEvidenceDetail, evidenceRows);
  assert.ok(html.includes("15 of 20 (75.0%) external non-bot PRs had no maintainer response"));
  assert.ok(html.includes("95% Wilson lower confidence bound:"));
  assert.ok(html.includes("Source: raw/pr_window/ghost-org__abandoned-lib.json"));
});

test("renderBlackHoleEvidence: renders rule, exclusions, and recovery note when no evidence rows or black_hole_evidence exist", () => {
  const noEvidenceDetail = {
    ...FLAT_BLACK_HOLE_DETAIL,
    black_hole_evidence: null,
  };
  const html = renderBlackHoleEvidence(noEvidenceDetail, []);
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

test("renderBlackHoleEvidence: formats response_kind into human prose and computes missing share/Wilson bound", () => {
  const html = renderBlackHoleEvidence({
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
  assert.ok(html.includes("12 of 15 (80.0%) external non-bot PRs"));
  assert.ok(html.includes("95% Wilson lower confidence bound: 59.1%"));
  assert.ok(html.includes("No response within 14d"));
  assert.ok(html.includes("Answered (review or comment)"));
  assert.ok(html.includes("Answered (closed by maintainer)"));
  assert.ok(html.includes("Answered (merged)"));
  assert.ok(!html.includes("Answered (review_or_comment)"));
  assert.ok(!html.includes("Answered (closed_by_maintainer)"));
  assert.ok(!html.includes("Answered (none)"));
  assert.ok(html.includes("black-hole-pr-scroll"));
});

test("methodology: the parameters table keeps seven columns with the readable name in the row header, and Why Porchlamp renders a 3-column kicker grid", () => {
  const html = methodologyShell(SNAPSHOT_DOC);
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

import { renderShell } from "../views/leaderboard.js";

test("leaderboard: one visible findings line under the header links to the static report with a real href from the site root", () => {
  // The number is checked against the snapshot's data by scripts/verify_static_export.py (check_hand_written).
  assert.match(copy.FINDINGS_LINE, /^More than 1 in \d+ active projects leave most strangers' pull requests unanswered for two weeks\.$/);
  const html = renderShell({ ...defaultState(), leaderboardLoading: true, leaderboard: null });
  assert.ok(html.includes(escapeHtml(copy.FINDINGS_LINE)), "the line is on the page");
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

// ------------------------------------------------ E3: project pages (rows link to them; the badge targets them; /check/)
import { rowCells, renderCard, handleRepoLinkClick, repoLinkHtml } from "../components/table.js";
import { projectAddress, normalizeQuery, matchProjects, renderResults } from "../check.js";

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
  assert.match(repo, /^<a class="repo-link"[^>]* href="\/projects\/freecad\/freecad\/">/, "the cell starts with a real link");
  assert.ok(repo.includes('<span class="repo-owner">FreeCAD/</span><span class="repo-name">FreeCAD</span></a>'), "the real spelling is shown");
  assert.ok(repo.includes('<span class="eco-chip">systems</span>'), "the ecosystem chip is still there, outside the link");
  assert.ok(renderCard(E3_ROW, {}).includes('href="/projects/freecad/freecad/"'), "the card links too");
  assert.ok(!repo.includes("style="), "the link carries no inline style");
  const css = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "styles", "components.css"), "utf8");
  assert.match(css, /\.repo-link \{\s*color: inherit;\s*text-decoration: none;\s*\}/, "components.css keeps the link looking like the row did");
  assert.equal(repoLinkHtml({ ...E3_ROW, owner: "a<b" }).includes("<b"), false, "a name is escaped");
  assert.equal(projectPath("Ggml-Org", "Llama.CPP"), "/projects/ggml-org/llama.cpp/");
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

test("E3: /check/ search: the address rule, pasted GitHub addresses, ranking of matches and real links", () => {
  assert.equal(projectAddress("ggml-org/llama.cpp"), "projects/ggml-org/llama.cpp/");
  assert.equal(projectAddress("FreeCAD/FreeCAD"), "projects/freecad/freecad/");
  assert.equal(normalizeQuery("  https://github.com/Pallets/Flask/ "), "pallets/flask");
  const names = ["psf/requests", "pallets/flask", "someone/flask-extras", "pallets/click", "FreeCAD/FreeCAD"];
  assert.deepEqual(matchProjects(names, "flask"), ["pallets/flask", "someone/flask-extras"], "both start the name: rank order is kept");
  assert.deepEqual(matchProjects(["ab/xflask", "cd/flask"], "flask"), ["cd/flask", "ab/xflask"], "a name that starts with it comes before one that only contains it");
  assert.deepEqual(matchProjects(names, "pallets/flask"), ["pallets/flask"]);
  assert.deepEqual(matchProjects(names, "FREECAD"), ["FreeCAD/FreeCAD"]);
  assert.deepEqual(matchProjects(names, "   "), []);
  assert.equal(matchProjects(names, "a", 2).length, 2, "the limit holds");
  const html = renderResults(["FreeCAD/FreeCAD"], "freecad");
  assert.equal(html, '<li><a href="../projects/freecad/freecad/">FreeCAD/FreeCAD</a></li>');
  assert.ok(renderResults([], 'x<y"').includes("No ranked project matches"), "an empty result says so");
  assert.ok(!renderResults([], "<img>").includes("<img>"), "the query is escaped");
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
  const measured = renderBlackHoleEvidence(FLAT_BLACK_HOLE_DETAIL);
  assert.ok(measured.includes("12 of 15 (80.0%) external non-bot PRs had no maintainer response, merge, or close within 14 days"));
  const notMeasured = renderBlackHoleEvidence({
    ...FLAT_BLACK_HOLE_DETAIL,
    black_hole_evidence: { ...FLAT_BLACK_HOLE_DETAIL.black_hole_evidence, rule_inputs_measured: false },
  });
  const stat = notMeasured.match(/<p class="black-hole-stat">(.*?)<\/p>/)[1];
  assert.ok(stat.startsWith("12 of 15 (80.0%) external non-bot PRs had no maintainer review, comment or merge within 14 days"), stat);
  assert.doesNotMatch(stat, /or close|response, merge/, "a close by a maintainer was not counted, so the sentence must not say it was");
  assert.ok(stat.includes("a close by a maintainer was not counted"), stat);
});

test("matrix hover card: the tier shows '(capped)' like the table and the drawer, from the coverage ceiling", () => {
  const point = { owner: "a", name: "b", porchlamp: 80.8, coverage: 0.9, tier: "A", friction: 10, leverage: 70, quadrant: "Sweet Spot" };
  assert.ok(renderHoverCard(point).includes("A (capped)"), "coverage 0.9 puts the ceiling at A");
  assert.ok(!renderHoverCard({ ...point, tier: "B" }).includes("capped"), "a tier below the ceiling is not capped");
  assert.ok(!renderHoverCard({ ...point, coverage: 0.99 }).includes("capped"), "full coverage has no ceiling");
  assert.ok(renderHoverCard({ ...point, coverage: 0.7, tier: "B" }).includes("B (capped)"), "coverage 0.7 puts the ceiling at B");
  assert.ok(!renderHoverCard({ ...point, tier_ceiling: null }).includes("capped"), "an explicit ceiling of none wins");
  // The card is rebuilt on every hover or focus move and ignores the pointer: it must hold no tab stop.
  assert.doesNotMatch(renderHoverCard(point), /tabindex/, "a capped tier in the card is not a tab stop");
  assert.doesNotMatch(renderHoverCard({ ...point, tier: undefined }), /tabindex/, "a missing tier in the card is not a tab stop");
  assert.ok(renderHoverCard({ ...point, tier: undefined }).includes(copy.MISSING_VALUE), "a missing tier still shows the missing-value mark");
  // the card agrees with the table row for the same project
  const tableTier = rowCells({ ...E3_ROW, coverage: 0.9, tier: "A", tier_ceiling: "A" }, {}).cells.find((c) => c.key === "tier").html;
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
  project: ["/projects/{owner}/{name}/", ["persona"]],
  notfound: [null, []],
};
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
/** An address of the route itself: a project's for the project route, a missing page's for "notfound". */
function addressOf(route) {
  if (route.id === "project") return "/projects/acme/widget/";
  if (route.id === "notfound") return "/no/such/page/";
  return route.path;
}

test("routes: the table holds every view at its address, with the query fields it keeps", () => {
  assert.deepEqual(Object.fromEntries(ROUTES.map((route) => [route.id, [route.path, [...route.query]]])), EXPECTED_ROUTES);
  assert.deepEqual(ROUTES.filter((route) => route.shell).map((route) => route.id), ["ranked", "notranked", "matrix", "compare", "configure", "methodology"]);
});

test("routes: every route round-trips through parseLocation and serializeLocation", () => {
  for (const route of ROUTES) {
    const qs = new URLSearchParams(route.query.map((key) => [key, FIELD_SAMPLES[key][1]]));
    const search = route.query.length ? `?${qs}` : "";
    const parsed = parseLocation(addressOf(route), search);
    assert.equal(parsed.tab, route.id === "project" ? "ranked" : route.id, route.id);
    if (route.id === "project") assert.equal(parsed.repo, "acme/widget");
    if (route.id === "notfound") assert.equal(parsed.missingPath, "/no/such/page/");
    for (const key of route.query) assert.deepEqual(parsed[FIELD_SAMPLES[key][0]], FIELD_SAMPLES[key][2], `${route.id} reads ${key}`);

    const address = serializeLocation({ ...defaultState(), ...parsed });
    const back = new URL(address, SITE_BASE);
    assert.equal(back.pathname, addressOf(route), `${route.id} writes its own path`);
    assert.deepEqual([...back.searchParams.keys()], route.query, `${route.id} writes exactly its fields, in order`);
    for (const key of route.query) assert.equal(back.searchParams.get(key), FIELD_SAMPLES[key][1], `${route.id} writes ${key}`);
    assert.equal(serializeLocation({ ...defaultState(), ...parseLocation(back.pathname, back.search) }), address, `${route.id} is stable`);
  }
});

test("routes: a query field the view does not read is neither read from its address nor written to it", () => {
  const every = Object.keys(FIELD_SAMPLES);
  const full = Object.fromEntries(every.map((key) => [FIELD_SAMPLES[key][0], FIELD_SAMPLES[key][2]]));
  for (const route of ROUTES.filter((r) => r.id !== "notfound")) {
    const foreign = every.filter((key) => !route.query.includes(key));
    const parsed = parseLocation(addressOf(route), `?${new URLSearchParams(foreign.map((key) => [key, FIELD_SAMPLES[key][1]]))}`);
    for (const key of foreign) assert.equal(parsed[FIELD_SAMPLES[key][0]], undefined, `${route.id} ignores ${key}`);
    const state = { ...defaultState(), ...full, tab: route.id === "project" ? "ranked" : route.id, repo: route.id === "project" ? "acme/widget" : null };
    const written = new URL(serializeLocation(state), SITE_BASE).searchParams;
    for (const key of foreign) assert.ok(!written.has(key), `${route.id} does not write ${key}`);
  }
});

test("routes: an address no route claims, or a project part that is not a GitHub name, is the not-found view", () => {
  for (const path of ["/projects/acme/", "/projects/acme/widget/extra/", "/projects/%2e%2e/widget/", "/projects/a%2Fb/c/", "/PROJECTS/acme/widget/", "/rust/", "/nope"]) {
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
  // A view's own address, the leaderboard's filters and its search stay where they are (?q= is the site's
  // search address; ?eco= and ?persona= move with the ecosystem and lens pages, a later step).
  for (const stays of ["/", "/?q=flask", "/?persona=first_timer&eco=pypi", "/matrix/?eco=npm", "/projects/acme/widget/", "/no/such/page/"]) {
    const url = new URL(stays, SITE_BASE);
    assert.equal(redirectTarget(url.pathname, url.search, url.hash), null, stays);
  }
  // every old tab= value named a view the table still holds
  for (const tab of VALID_TABS) assert.ok(routeById(tab)?.shell, tab);
});

test("routes: every primary nav link is a view's address, opened in place, or the report's page, with the shown view current", () => {
  for (const tab of ["ranked", "notranked", "matrix", "compare", "configure", "methodology", "notfound"]) {
    const state = { ...defaultState(), tab, persona: "first_timer", ecosystem: "npm", query: "widget", missingPath: tab === "notfound" ? "/x/" : null };
    const links = [...renderNav(navEntryFor(tab), state).matchAll(/<a class="primary-tab" href="([^"]*)"( data-route-tab="([^"]*)")?( aria-current="page")?>/g)];
    assert.equal(links.length, 6, `${tab}: six links`);
    const [report, ...views] = [links[5], ...links.slice(0, 5)];
    for (const [, rawHref, , linkTab] of views) {
      const href = rawHref.replace(/&amp;/g, "&");
      const { route } = matchRoute(new URL(href, SITE_BASE).pathname);
      assert.ok(route.shell, `${tab}: ${href} is the address of a view the app draws`);
      assert.equal(linkTab, route.id, `${tab}: ${href} opens its own view in place`);
      assert.equal(href, hrefFor(state, route.id), `${tab}: ${href} is where a click goes from here`);
    }
    assert.deepEqual([report[1], report[3]], ["/black-holes/", undefined], `${tab}: the report is a page of its own, loaded`);
    const current = links.filter((link) => link[4]).map((link) => link[3]);
    assert.deepEqual(current, tab === "notfound" ? [] : [tab === "notranked" ? "ranked" : tab], `${tab}: the current entry`);
  }
  assert.match(renderNav("report"), /<a class="primary-tab" href="\/black-holes\/" aria-current="page">Report<\/a><\/div>$/, "the report's own page marks it");
  // The Leaderboard's Ranked and Not ranked links are addresses too, carrying the reader's lens and filters.
  const lb = leaderboardRenderShell({ ...defaultState(), persona: "first_timer", ecosystem: "npm", leaderboardLoading: true });
  assert.ok(lb.includes('<a class="tab" href="/?persona=first_timer&amp;eco=npm" data-route-tab="ranked" aria-current="page">'), lb);
  assert.ok(lb.includes('<a class="tab" href="/not-ranked/?persona=first_timer&amp;eco=npm" data-route-tab="notranked">'), lb);
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
  for (const route of ROUTES) {
    const state = route.id === "project"
      ? { ...defaultState(), repo: "acme/widget" }
      : { ...defaultState(), tab: route.id, missingPath: route.id === "notfound" ? "/x/" : null };
    const title = titleForState(state);
    assert.ok(title.includes("Porchlamp"), `${route.id}: ${title}`);
    assert.equal(title, routeTitle(route.id, { owner: "acme", name: "widget" }), route.id);
    titles.set(route.id, title);
  }
  assert.equal(new Set(titles.values()).size, ROUTES.length, "no two views share a title");
  const loaded = { ...defaultState(), repo: "acme/widget", repoDetailByKey: { "acme/widget": { owner: "Acme", name: "Widget" } } };
  assert.equal(titleForState(loaded), routeTitle("project", { owner: "Acme", name: "Widget" }), "the record's spelling once it has loaded");
  const webRoot = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
  const titleOf = (file) => readFileSync(path.join(webRoot, file), "utf8").match(/<title>([^<]*)<\/title>/)[1];
  assert.equal(titleOf("index.html"), titles.get("ranked"), "index.html is the leaderboard's page");
  assert.equal(titleOf("404.html"), titles.get("notfound"), "404.html is the not-found page");
  const app = readFileSync(path.join(webRoot, "app.js"), "utf8");
  assert.match(app, /const title = titleForState\(state\);\s*if \(doc\.title !== title\) doc\.title = title;/, "app.js sets it on every render");
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
  const identity = ranked.match(/<div class="drawer-identity"[\s\S]*?<\/div>/)[0];
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
  const projectPages = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "porchlamp", "serve", "project_pages.py"), "utf8");
  assert.match(projectPages, /scripts=\("\/back-forward\.js",\)/, "every project page loads it");
});

test("routes: the panel's README badge preview loads from the site root, since the panel is open at a project's address", () => {
  const html = renderMaintainerBadge(badgeRow({}), SITE_BASE);
  const src = html.match(/<img class="maintainer-badge-preview" src="([^"]*)"/)[1];
  assert.match(src, /^\/badges\/[a-z0-9-]+\.svg$/, src);
  assert.ok(existsSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "..", src.slice(1))), `${src} is a file of the site`);
});
