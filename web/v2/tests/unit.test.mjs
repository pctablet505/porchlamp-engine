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

import { decidingTieLevel, SORTABLE_COLUMNS } from "../components/table.js";
import { KEY_ACTIONS, actionForKeyEvent } from "../components/keymap.js";
import {
  defaultState,
  reduce,
  parseSearch,
  serializeSearch,
  countRankChanges,
  resolveOpenTargetKey,
} from "../state.js";
import * as copy from "../copy.js";

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
