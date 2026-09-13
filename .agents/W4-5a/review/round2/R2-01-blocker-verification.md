# R2-01 Blocker-by-Blocker verification at `c52ab45`

| B | Fix in code | Verified how | Test, and what it fails on (fail-before) |
|---|---|---|---|
| B-1 Not-ranked crash | `notranked.js` `shellShape` returns bare `loading/error/empty/table`; the counts suffix moved to a new `rebuildKey()`; `app.js:265-272` rebuilds on `rebuildKey` and patches on `shellShape(state) === "table"` | Re-traced: leaderboard loaded + `notRanked === null` now yields `shellShape -> "empty"`, which `renderShell:120` matches, so `flattenGroups(null)` is unreachable | No Python/Node test (the lead's browser pass is the evidence: "Not-ranked switches with `tab=notranked`, Gated (7)/Unrated (4)"). A regression here is caught only by a human — see R2-04 |
| B-2 J/K focus | `focusRowByKey()` (`app.js:749-755`) called after the patch; `scrollRowIntoView` moved BEFORE the patch so the target row is inside the window when focus is applied | Order verified in source; the re-order is necessary and sufficient | None automated; lead browser-verified |
| B-3 drawer focus loss | `shouldRestoreFocusAfterRebuild(isOpen, focusWasInside)` in `state.js`, called for both drawer and keymap rebuilds | Predicate is pure and correct: restores only when focus was already inside AND the dialog stays open — never steals focus from the header | Extracted expressly to be unit-testable, but **no test calls it** (Node suite still unexecutable). Fail-before: none |
| B-4 `aria-sort` | `sortDirectionLabel()` in `table.js`, used by both `renderColumnHeader` and `app.js:184 syncSortHeaders` | I re-derived the truth table against the server's own `best_first_ascending` + `reverse_flag` logic: cvi→descending, -cvi→ascending, rank→ascending, -rank→descending — **all four agree** | Node test only (unexecutable). Lead browser-verified truthfulness and toggling |
| B-5 `q` replaceState | `urlSyncMode(trigger)` in `state.js`; the debounced input path now calls `replaceUrl`, and Enter/blur commit with `pushUrl(state, {force:true})` | `replaceUrl` is now genuinely called (was dead) | `urlSyncMode` has no test. New minor defect: `{force:true}` pushes even when the URL is unchanged, so repeatedly focusing/blurring the search box stacks identical history entries (R2-05 N-7) |
| B-6 delta column | `balanced_rank`/`rank_delta` on `LeaderboardRow` + a validator pinning `rank_delta == balanced_rank - rank` and "both or neither"; both fixtures carry the fields | Fixture check: ft row 3 is `rank=3, balanced_rank=7, rank_delta=+4`; sign convention matches `table.js:114-117` (positive ⇒ ▲ ⇒ better under the lens) | **3 real tests** (`TestBalancedRankAndDelta`): balanced fixture must be all-null; ft fixture must satisfy the formula on every row; most rows must be non-zero. Fail-before proven by mutants N2 (sign flip) and N3 (nullability) — both KILLED |
| B-7 evidence 404 | `evidence-<owner>__<name>.json`; `_parse_evidence_filename` partitions on `__` and RAISES when absent; three fixtures renamed | Live probe on my own port 8913: `ivywood/template-cli-71` → **200, 7 rows**; `ivywood-template/cli-71` → **404** (the inverse of round 1) | **3 tests**: every fixture file reachable at 200 with byte-identical body; the specific repo not 404; every filename round-trips and names a real leaderboard row. Fail-before proven by mutant N4 (single-underscore split) — KILLED, 3 failures |

Also fixed as claimed: persona-gate vocabulary re-keyed to `ai_hard_ban` / `elephant_factor_below_3` /
`high_barrier_to_entry` / `criticality_below_threshold` in both `copy.js` and the fixtures (W-2);
persona composites now equal the re-projected top-level score, with a test (W-1); a mixed-case fixture
repo `Blue-Forge/Task-Runner` added (W-8); the `copy.js` docstring path corrected (N-6).

**NEW Blocker found by the lead's parallel browser pass, present at this tip** (`git show c52ab45:web/v2/app.js`):
```
      case "open-selected": {
        if (isInputFocused || s.repo || s.keymapOpen) return;
        const rows = s.leaderboard ? s.leaderboard.rows : [];
        const row = rows.find((r) => rowKey(r) === s.selectedRepoKey);
        if (row) openRepo(row.owner, row.name);
```
Nothing sets `selectedRepoKey` on focus (only J/K at `:698` and `openRepo` at `:515`), so Enter on a
row reached by Tab or by `.focus()` opens nothing — 06 §5 "`Enter` open" and §8 item 8. A fix
(`resolveOpenTargetKey`, focus first then the cursor as fallback) is uncommitted in the worktree.
