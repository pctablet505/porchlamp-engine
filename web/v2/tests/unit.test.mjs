// tests/unit.test.mjs — Node unit tests for the pure modules (06 §6 and §8
// acceptance checklist items 3-5, 8; state/query modules added by the
// 2026-09-13 UX rework, U-14/U-16/U-17/U-18).
//
// NOT EXECUTED ON THIS BOX: `node` is not installed here — see
// .agents/W4-5a/handoff.md. Written to run cleanly under `node --test`
// (Node >= 18, stdlib only) the moment Node is available.
//
// Run: node --test web/v2/tests/unit.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { decidingTieLevel, SORTABLE_COLUMNS } from "../components/table.js";
import { KEY_ACTIONS, actionForKeyEvent } from "../components/keymap.js";
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
import { normalizeWeights, applyPresetRespectingLocks, renderControls } from "../views/configure.js";
import { PILLAR_IDS, PERSONA_PRESET_WEIGHTS, BALANCED_WEIGHTS } from "../state.js";
import { nearestMarkerInDirection, hitTestPoint } from "../views/matrix.js";
import { pointKey, SVG_MARKER_BUDGET, toSvgX, toSvgY } from "../components/scatter.js";
import { synthesizeRows } from "../perf-fixtures.js";
import { shellKey as compareShellKey, shellShape as compareShellShape } from "../views/compare.js";
import { renderDrawer, classifyKind, findRow } from "../views/drawer.js";

// --------------------------------------------------------------------- U-5
// D-TIE-1 client-derivable levels: 1 (composite), 2 (coverage), 3
// (responsiveness), 4 (friction), 5 (leverage), 8 (owner/name — levels 6
// TTFHR p75 and 7 last-commit-date are store-only, never in the row
// contract, see docs/v2/06-UX-DESIGN.md discussion in the handoff).
test("decidingTieLevel: level 1 (composite differs — not a genuine tie)", () => {
  const a = { cvi: 90.11, coverage: 0.9, p_responsiveness: 80, friction: 20, leverage: 70, owner: "a", name: "a" };
  const b = { cvi: 88.5, coverage: 0.9, p_responsiveness: 80, friction: 20, leverage: 70, owner: "b", name: "b" };
  assert.equal(decidingTieLevel(a, b), 1);
});

test("decidingTieLevel: level 2 (tied on composite, differ on coverage)", () => {
  const a = { cvi: 90.11, coverage: 0.97, p_responsiveness: 80, friction: 20, leverage: 70, owner: "a", name: "a" };
  const b = { cvi: 90.11, coverage: 0.81, p_responsiveness: 80, friction: 20, leverage: 70, owner: "b", name: "b" };
  assert.equal(decidingTieLevel(a, b), 2);
});

test("decidingTieLevel: level 8 (tied through every client-visible level, owner/name is the fallback)", () => {
  const a = { cvi: 77.0, coverage: 0.9, p_responsiveness: 80, friction: 20, leverage: 70, owner: "aaa", name: "deep" };
  const b = { cvi: 77.0, coverage: 0.9, p_responsiveness: 80, friction: 20, leverage: 70, owner: "zzz", name: "deep" };
  assert.equal(decidingTieLevel(a, b), 8);
});

// -------------------------------------------------------------------- U-3
test("SORTABLE_COLUMNS excludes every vanity/decorative column (stars, quadrant, suspicion, delta, repository)", () => {
  for (const forbidden of ["stars", "quadrant", "suspicion", "delta", "repository"]) {
    assert.ok(!SORTABLE_COLUMNS.has(forbidden), `${forbidden} must not be sortable (U-3)`);
  }
  assert.ok(SORTABLE_COLUMNS.has("rank"));
  assert.ok(SORTABLE_COLUMNS.has("cvi"));
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
    const keyAliases = { esc: "escape" };
    const singleKeys = entry.keys
      .split(/\s*[/–-]\s*/)
      .map((k) => k.trim().toLowerCase())
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

// U-18/§5 (lead-reported defect, 2026-09-13, on c52ab45): a row reached by
// Tab or a direct `.focus()` call — never having gone through the J/K
// cursor's SET_CURSOR — must still open on Enter. This is the full
// key-handler state transition the fix depends on: actionForKeyEvent
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
    ...defaultState(), persona: "first_timer", ecosystem: "pypi", query: "flask", sort: "-cvi",
    page: 3, pageSize: 100, tab: "notranked", repo: "a/b",
  };
  const qs = serializeSearch(s1);
  assert.equal(qs, "?persona=first_timer&eco=pypi&q=flask&sort=-cvi&page=3&page_size=100&repo=a%2Fb&tab=notranked");
  const parsed = parseSearch(qs);
  assert.equal(parsed.persona, "first_timer");
  assert.equal(parsed.ecosystem, "pypi");
  assert.equal(parsed.query, "flask");
  assert.equal(parsed.sort, "-cvi");
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

// query.js (client-side filter/sort) was REMOVED by the owner's
// 2026-09-13 pagination ruling: with only one page loaded at a time, sort/
// search/ecosystem changes must go through the API (only the server can
// see the whole result set) — see app.js's loadLeaderboard and the
// handoff's "sort/search/filter are fetches" note.

// -------------------------------------------------------------- U-10 / copy.js
// The reason maps themselves are private to copy.js (only the wrapper
// functions are exported); a code the map actually covers must translate
// to something OTHER than the raw code itself (the functions' fallback is
// `|| reason`, which would otherwise silently pass this test on a typo).
test("copy.js: gate and unrated reason text covers every code the fixtures use", () => {
  for (const code of ["mirror", "archived", "unlicensed", "stale", "black_hole", "non_software", "fork"]) {
    const text = copy.gateReasonText(code);
    assert.notEqual(text, code, `no gate reason copy for ${code} (fell through to the raw code)`);
  }
  for (const code of ["insufficient_evidence", "unrated: insufficient_evidence", "no_external_workflow", "off_platform_routed"]) {
    const text = copy.unratedReasonText(code, 0.42);
    assert.notEqual(text, code, `no unrated reason copy for ${code} (fell through to the raw code)`);
  }
  assert.equal(
    copy.personaGateReason("insufficient_evidence:decided_first_timer_prs"),
    "Not enough evidence to show under this lens."
  );
  // Engine vocabulary (cvi/core/persona.py:185-188; review Warning W-2).
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

// -------------------------------------------------------------- NB-2/W-4
// U-15/U-7 (review 2026-09-13): a snapshot fetch failure previously left
// the footer/chip resting on a fabricated "Loading..." forever —
// SNAPSHOT_ERROR was reduced into state and read by nothing.
// footerFieldsFor is the single decision app.js's syncFooter and the
// header chip both read.
test("footerFieldsFor: error takes priority over a stale/absent snapshot", () => {
  const errored = { ...defaultState(), snapshotError: { endpoint: "/v1/cvi/snapshot", status: 500 } };
  assert.deepEqual(footerFieldsFor(errored), {
    kind: "error",
    endpoint: "/v1/cvi/snapshot",
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
    snapshotError: { endpoint: "/v1/cvi/snapshot", status: 500 },
  };
  assert.equal(footerFieldsFor(state).kind, "error");
});

// --------------------------------------------------------------------- //
// Configure weights: normalizeWeights / applyPresetRespectingLocks
// (Opus code review B-1, 2026-09-13: locking four pillars and dragging
// the fifth DOWN left the sum at 0.87, not 1 — `free.length === 0` only
// corrected an OVERFLOW, never a shortfall. Property test shape per the
// reviewer's own request: lock subsets x values, plus randomised walks.)
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

test("normalizeWeights: the exact reviewer repro (four locked, fifth dragged down) snaps to the only valid value", () => {
  const locks = { responsiveness: true, criticality: true, governance: true, accessibility: true };
  const next = normalizeWeights(BALANCED_WEIGHTS, locks, "rigor", 0.02);
  assertValidDistribution(next, locks, BALANCED_WEIGHTS, "four-locked repro");
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
// N1 (round-3 review, 2026-09-14): the B-1 normaliser fix was guarded only
// by THIS unexecutable file. `tests/cases/normalize_weights.json` is now
// the shared oracle: `tests/test_ui_weights_port.py` (a faithful Python
// port, since Node cannot run here) asserts the SAME numbers against ITS
// OWN implementation, so a drift between the two would fail one of them.
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
// Configure weights: slider accessibility markup (lead-reported defect
// 2026-09-13: "aria-label is null" — a `<label for>` alone gave this
// box's own accessibility-tree inspection a correct accessible name, but
// an audit checking the `aria-label` ATTRIBUTE directly found nothing).
// Hand-traced against `renderControls`'s own HTML string output (no DOM
// needed) since Node is absent — see the file's own top-of-file note.
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
// repoToLoadFromState: the URL->actions mapping for a drawer opened
// straight from a URL (lead-reported defect 2026-09-13: a `?repo=owner/
// name` URL rendered the drawer's header but never dispatched the
// evidence/record fetch, since only the click path (app.js::openRepo)
// triggered either — "Loading evidence..." stuck forever).
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
// SVG_MARKER_BUDGET (Opus code review 2026-09-13: "above the 2,000-marker
// canvas threshold markers have no DOM, so Tab and click stop working").
// Exercised on the 5,000-point perf fixture, as the reviewer asked.
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
  // one (verified live against the real fixture server, see the handoff);
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
// NB-1 (round-3 review, 2026-09-14): Compare rebuilt `main.innerHTML` on
// EVERY keystroke because `compareQuery` was baked into the shell HTML,
// destroying and recreating the focused search `<input>` (06 §6.1 U-12 —
// "never `innerHTML` on an ancestor of the focused element"). The fix
// moves the rebuild decision into a pure `shellKey(state)` that
// `app.js::syncView` compares instead of the rendered HTML string; these
// tests are the "what to patch" DECISION itself: a keystroke (query/
// results change) must leave the key untouched (results-only patch), a
// chip-set change (add/remove) or a shape transition must change it
// (shell rebuild). A DOM-level check of actual node-identity/caret
// survival needs a real DOM (no Node, no jsdom, and `tests/fakedom.mjs`
// does not parse `innerHTML` strings — it only backs the keyed-patch
// tests above) — verified instead in a real Chromium tab via the Claude
// Browser tool; see `.agents/W4-5b/handoff.md`'s Round 3 section for that
// evidence, which this test cannot itself provide.
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
    compareDataByRepo: { "riverline/httpkit": { loading: false, kind: "rated", row: { cvi: 90 }, evidence: [] } },
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
// N3 (round-3 review, 2026-09-14): the repo-detail 404 path was untested
// end to end. `renderDrawer` used to route ANY `repoDetailError` (a 404
// included, since api.js/app.js treat every non-2xx the same way) through
// the retry-error banner, making `copy.DRAWER_NOT_FOUND_TEXT` dead code
// for the one case it names -- fixed alongside this test (drawer.js: a
// 404 now renders the not-found copy with no retry button; every other
// status keeps the retry banner). Mutant this guards against: the server
// answering an unknown repo with an empty 200 instead of a 404 -- see
// `tests/test_serve_ui_fixtures.py::TestRepoDetailEndpoint` for the wire
// half of this same fix.
// --------------------------------------------------------------------- //

function drawerStateFor(overrides) {
  return { ...defaultState(), repo: "nobody/nothing", repoDetailByKey: {}, repoDetailLoading: false, repoDetailError: null, ...overrides };
}

test("renderDrawer: a 404 (genuinely not found) shows copy.DRAWER_NOT_FOUND_TEXT and no retry button", () => {
  const html = renderDrawer(drawerStateFor({ repoDetailError: { endpoint: "/v1/cvi/repos/nobody/nothing", status: 404, detail: "no such repo: nobody/nothing" } }));
  assert.ok(html.includes(copy.DRAWER_NOT_FOUND_TEXT), "must render the not-found copy for a 404");
  assert.ok(!html.includes("data-repo-detail-retry"), "a genuine 404 must not offer a retry button (it will 404 again)");
});

test("renderDrawer: a non-404 error (500/timeout/network) shows the retry banner, not the not-found copy", () => {
  for (const status of [500, "timeout", 0]) {
    const html = renderDrawer(drawerStateFor({ repoDetailError: { endpoint: "/v1/cvi/repos/nobody/nothing", status, detail: "boom" } }));
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
// W4-5c (2026-09-14): `GET /v1/cvi/repos/{owner}/{name}` has two
// producers -- the real API and the UI fixture stand-in
// (`scripts/serve_ui_fixtures.py`) -- and used to disagree: the fixture
// server invented a `{kind, row}` wrapper the real API never sent,
// crashing the drawer (`TypeError: Cannot read properties of undefined
// (reading 'owner')`) the moment it was pointed at the real API. The
// contract is the truth: `found` is now the flat `RepoDetailResponse`
// itself, and `classifyKind` derives `kind` client-side from
// `eligible`/`rated` (see `tests/test_repo_detail_cross_check.py` for the
// Python-side two-producer parity test this fix is bound to).
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
  rank: 1, tie_group: null, cvi: 98.34, coverage: 1.0, tier: "S", tier_ceiling: null,
  quadrant: "Sweet Spot", leverage: 90, friction: 5, leverage_coverage: 1.0, friction_coverage: 1.0,
  p_responsiveness: 95, c_responsiveness: 1.0, p_criticality: 90, c_criticality: 1.0,
  p_governance: 92, c_governance: 1.0, p_rigor: 88, c_rigor: 1.0, p_accessibility: 91, c_accessibility: 1.0,
  persona_gates: {}, persona_composites: {}, suspicion: [], stars_audited: 16282,
};

const FLAT_GATED_DETAIL = {
  owner: "gated-org", name: "repo-mirror-0", ecosystem: "cargo",
  snapshot_id: "snap-test", benchmark_version: "2.0.0-test", raw_hash: "b".repeat(64),
  eligible: false, gate_reason: "mirror", rated: false, unrated_reason: null,
  rank: null, tie_group: null, cvi: null, coverage: 0.0, tier: null, tier_ceiling: null,
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
  assert.ok(html.includes("98.34"), "the composite CVI must render");
  assert.ok(html.includes(FLAT_RATED_DETAIL.raw_hash.slice(0, 16)), "the integrity section must render raw_hash");
});

// --------------------------------------------------------------------- //
// W4-5d (2026-09-14): the gated branch used to render ONLY the identity/
// gate-status section (test above this comment, now superseded/rewritten)
// -- "the reason sentence and nothing else above the fold" (06 §3 C) was
// implemented as "nothing else, period", losing the pillar/persona/
// integrity sections a gated repo genuinely has data for (measured/
// unmeasured evidence rows, a repo-level raw_hash/snapshot_id). Fixed:
// the gate sentence still renders first and alone above the fold, but
// three COLLAPSED `<details>` sections now sit below it; the composite
// section is OMITTED (no CVI/tier/quadrant ever existed for a gated
// repo -- see copy.js's comment above DRAWER_SECTION_COMPOSITE for the
// documented "omit, don't placeholder" choice).
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

  // Composite is omitted entirely -- no CVI/tier/quadrant was ever computed.
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

  // W4-5d regression guard: the shared `renderEvidenceOnlyPillarSection`
  // extraction (used by both "unrated" and the new "gated" branch) must
  // leave "unrated" byte-for-byte the same shape it always was -- exactly
  // one <details>, still OPEN (unlike gated's now-collapsed three), and no
  // persona/integrity sections (an unrated repo's `persona_composites`/
  // `raw_hash` are real fields but this screen has never shown them, and
  // this packet's scope is gated only -- dispatch: "an unrated repo
  // unchanged").
  const detailsTags = html.match(/<details[^>]*>/g) || [];
  assert.equal(detailsTags.length, 1, "unrated must still render exactly one <details> section");
  assert.ok(/\bopen\b/.test(detailsTags[0]), "unrated's single section must stay open, unchanged");
  assert.ok(!html.includes(copy.DRAWER_SECTION_PERSONAS), "no Persona view section for unrated (unchanged)");
  assert.ok(!html.includes(copy.DRAWER_SECTION_INTEGRITY), "no Integrity section for unrated (unchanged)");
  assert.ok(!html.includes(copy.DRAWER_SECTION_COMPOSITE), "no Composite section for unrated (unchanged)");
});
