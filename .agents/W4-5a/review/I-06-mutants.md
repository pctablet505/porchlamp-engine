# I-06 Mutation battery — Python 13/20 killed, JS 6 hand-traced
Python: each mutant applied to a COPY of 026bb32 (`git archive`), full 65-test packet suite per run,
tree restored and re-verified GREEN after the last one. Driver: `scratch/W4-5a-review/mutants.py`.

KILLED (13): M1 vanity fields sortable; M2 page_size allow-list dropped; M4 unknown-repo evidence
200 instead of 404; M5 counts recomputed from the filtered page; M6 paging ignores `page`;
M7 tie_group non-nullable; M8 stars_audited non-nullable; M9 default page size 20->50;
M14 `'unrated: insufficient_evidence'` spelling rejected; M15 a real AA failure in the dark palette;
M17 copy.js export bypassed by an inline literal; M18 an external font URL in a served file;
M20 'fork' removed from GateReason.

SURVIVED (7):
- M3 `q` row-side `.lower()` removed. Every fixture repo name is already lowercase, so only the
  QUERY side of case-insensitivity is tested. Fix: one fixture repo with a capital letter.
- M10 `SnapshotCounts.total != rated+gated+unrated` accepted. No test constructs an inconsistent one.
- M11 `ReprojectRequest` accepts a NEGATIVE weight — this is the exact defect W4-3's Blocker B-1
  found in the store. The only reproject test checks the sum, never the sign. Fix: one `pytest.raises`.
- M12 `NotRankedRow` with no reason at all accepted.
- M13 `EvidenceRow(measured=False)` with no reason accepted.
- M16 AA threshold weakened 4.5 -> 2.0 — no guard on the guard.
- M19 `LeaderboardResponse` stops bounding page_size.
(M10/M12/M13/M19 are the same class: five hand-written validators, none with a negative test.)

JS (hand-traced; no engine on this box):
J1 duplicate key in `patchRows` -> duplicate nodes accumulate. No test (every fixture key unique). NOT CAUGHT.
J2 spacer height `start*ROW_HEIGHT` -> `(start+1)*…` in `views/leaderboard.js::patchTableBody`.
   render.perf.test.mjs tests its OWN copy of that function. NOT CAUGHT.
J3 `goToPageSize` anchor `Math.floor(abs/newSize)+1` -> `state.page`. No app.js test at all. NOT CAUGHT.
J4 swap pushState/replaceState in `pushUrl`. No test. NOT CAUGHT — and the mutant is already half-present (replaceUrl is dead).
J5 `countRankChanges` -> `nextRows.length`. unit.test.mjs's `countRankChanges(before, before) === 0`
   case WOULD catch it — killed by an unexecuted test.
J6 delete the `.focus()` in `closeDrawer`. No test; lead's browser pass only. NOT CAUGHT.
J7 swap levels 4/5 in `decidingTieLevel`. Node test covers levels 1/2/8 only. NOT CAUGHT.
J8 drop the `tie_group !== null` guard in `isTieMate` (two singletons render as tied). No test. NOT CAUGHT.
