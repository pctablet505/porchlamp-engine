# R2-02 Mutation battery on `c52ab45` — 23/25 killed
Same method: `git archive c52ab45` into a scratch copy, one semantic change at a time, the full
78-test packet suite per mutant, tree restored and re-verified GREEN (78 passed).
Driver: `scratch/W4-5a-review/mutants2.py`.

All 20 round-1 mutants re-run. **Every round-1 survivor is now killed except M16**:
M3 (q row-side case) KILLED by the new mixed-case fixture row + test; M10, M11, M12, M13, M19
KILLED by the five new `pytest.raises` validator tests.

Five NEW mutants aimed at the round-2 code:
| id | mutation | result |
|---|---|---|
| N1 | `balanced_rank` set without `rank_delta` (drop the "both or neither" half) | **SURVIVED** |
| N2 | delta formula sign-flipped to `rank - balanced_rank` — i.e. exactly the projection W4-4 is writing | KILLED (1 failure) |
| N3 | `rank_delta: int` (loses nullability; balanced rows carry null) | KILLED (2) |
| N4 | evidence filename partitioned on a single `_` | KILLED (3) |
| N5 | `q` QUERY-side lowering removed (pairs with M3's row-side mutant) | KILLED (1) |

Surviving, with the reason:
- **M16** AA threshold 4.5 → 2.0 still survives. The new
  `test_4_5_is_a_real_load_bearing_threshold_not_a_number_nothing_ever_fails` asserts that
  `contrast_ratio("#949494", "#FFFFFF")` lies strictly between 2.0 and 4.5 and is `not >= 4.5` —
  that pins `contrast_ratio`'s ARITHMETIC, not the `if ratio < 4.5` comparison inside
  `test_every_text_pair_clears_4_5_to_1`, which is what a weakening mutant edits. To kill it, feed a
  deliberately-failing synthetic pair through the same failure-collection routine the real test uses
  and assert it is reported.
- **N1** the "both or neither" half of the new validator has no negative test (the formula half is
  covered by N2). One more `pytest.raises`.

JS battery (unchanged, still unexecutable — no Node/Deno/Bun/QuickJS on this box): J1 duplicate key,
J2 spacer height, J3 page-size anchor, J4 push/replace swap, J6 Esc focus, J7 tie levels 4/5,
J8 `isTieMate` null guard — none covered by an executable test. Round 2 adds three more pure,
deliberately-extracted-for-testing functions (`sortDirectionLabel`, `shouldRestoreFocusAfterRebuild`,
`urlSyncMode`) that **no runnable test calls**; `unit.test.mjs` gained assertions for them but still
cannot execute (W-5: no `web/v2/package.json` with `{"type":"module"}`).
