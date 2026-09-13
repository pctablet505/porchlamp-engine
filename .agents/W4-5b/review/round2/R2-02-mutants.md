# R2-02 Mutation battery on `adfec73` — 11/16 killed
Copy of the tree via `git archive`; 124-test packet suite per mutant; tree restored GREEN.
Driver: `scratch/W4-5b-review/mutants5b_r2.py` (M2 re-run separately after its anchor was renamed).

| id | mutation | round 1 | round 2 |
|---|---|---|---|
| M1 | matrix `excluded_count` always 0 | KILLED | KILLED |
| M2 | axis-coverage threshold 0.05 → 0.99 | KILLED | KILLED (2 failures) |
| M3 | plots rows with `quadrant is None` | SURVIVED | **KILLED** |
| M4 | numerator drops the `c_p > 0` guard | SURVIVED | SURVIVED (equivalent on this fixture) |
| M5 | coverage over measured pillars only | SURVIVED | SURVIVED (equivalent on this fixture) |
| M6 | rating floor 0.60 → 0.0 | SURVIVED | **KILLED (2)** |
| M7 | `rank_delta` sign flipped (helper) | SURVIVED | **KILLED** |
| M8 | `MatrixResponse` count guard removed | KILLED | KILLED |
| M9 | planted literal in `methodology.js` | KILLED | KILLED |
| M10 | literal hidden in a braced `${…}` | SURVIVED | SURVIVED (W-2 deferred) |
| M11 | error body flattened | KILLED | KILLED (5) |
| M12 | D-TIE-1 order → alphabetical | KILLED | KILLED (8) |
| **N1** | revert the B-1 four-locks fix | — | **SURVIVED** |
| **N2** | `axis_partial_share` pinned to 0.0 | — | KILLED |
| **N3** | repo-detail 404 → empty 200 | — | **SURVIVED** |
| **N4** | `rank_delta` sign flipped on the LIVE response | — | KILLED |

Three of the four round-1 real survivors are now killed; the two that remain (M4/M5) are equivalent
on the current fixture (no row has `c_p > 0` with `p_p is None`, nor `c_p == 0` with `p_p` set), so a
single fixture row of that shape would convert both into live tests.

**N1 is the significant new survivor.** Reverting B-1 — the headline fix of this round — changes no
Python test outcome, because the property test the implementer added lives in
`web/v2/tests/unit.test.mjs:332` and `node`/`deno`/`bun`/`qjs` are all still absent, and
`grep -rl "normalizeWeights" tests/` returns **nothing**: the 20,000-step Python cross-check they
describe was a one-off, not a shipped test. The fix is real (my own port proves it) but unguarded —
a future edit to that branch would pass CI. Cheapest close: ship the Python port as a real test
(~30 lines, it already exists in my scratch), or land W4-5a's still-open `web/v2/package.json`
`{"type":"module"}` so `node --test` can run the .mjs suite at all.

**N3**: `store.find_repo_detail` returning `None` (→ 404) has no test; replacing the 404 with an
empty 200 passes everything. The drawer's own not-found branch therefore rests on an untested server
path — one `assert httpx.get(.../repos/nobody/nothing).status_code == 404` closes it.
