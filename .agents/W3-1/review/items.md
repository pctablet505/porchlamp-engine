# W3-1 review — item records (2026-09-13-w3-1-1)

Head `98dd4c5` (content `a9413e8` + fixup `98dd4c5`), base `5f5350a`, main `bcdf07b`. **PASS 16 · FAIL 4 · UNDECIDED 0** — **ACCEPT_WITH_FOLLOWUP**. No Blockers.

Full report: `scratchpad/reviews/W3-1-review.md`. Machine-readable: `items.json`.

| Item | Checklist | Verdict | Finding | Evidence |
|---|---|---|---|---|
| C1a | L1-13 | **PASS** | — | My round-3 fixture (9 of 20 newcomer authors unmeasured, true rate 0.45): was (0.0,'derived') n=11, now (None,'missing') with newcomer_unmeasured_detail '9 of 20 newcomer authors unmeasured'; rr90 gated with it; raw still 0.45. |
| C1b | L0-05 | **PASS** | — | My round-3 fixture (10 of 20 both unsampled and failed): was unmeasured=0/unsampled=10 with dr90 (1.0,'derived') n=10 against a raw 0.5; now unmeasured=10/unsampled=0 and the gate fires. Bucket identity holds. |
| C1c | LENS-DET-01 | **PASS** | — | Sub-threshold fixture (3 of 20 = 0.15) gives (1.0,'derived') n=17, the round-3 value. Mutant T3 (is True -> is not False) KILLED by test_eligible_call_site_excludes_unmeasured_below_gate_threshold; R8 (T4) also killed. |
| C1d | L0-05 | **PASS** | — | The invented fifth value 'unmeasured' is gone; over a full gated output every field carries verified/derived/missing/flag only. |
| C1e | L1-14 | **FAIL** | F-004 | grep -c collector_failure in the collector = 1, and that hit is a docstring explaining why it is NOT emitted. No loader mapping added: mr_first_rate/newcomer_conversion/rr90_return_rate remain _unmapped and dr90's resolver never reads the collector field of the same name (grep = 0). The detail strings reach no consumer. |
| C2a | L1-13 | **PASS** | — | derive_cohort_fields: _eligible(...) is True -> _cap_prs for 1.3; a['_eligible'] is True -> _cap_authors for 5.2/5.3. |
| C2b | L1-13 | **FAIL** | F-001 | _dr90_decision_cohort counts every external PR: no gate, no cap, method=METHOD_PRIMARY with the caveat only in a docstring. Packet oracle 1 measured there: 6 sock PRs among 10 give DR_90 0.6; the cap gives 0.3333 (+0.267 on a 0.25-weight parameter). |
| C2c | L0-03 | **FAIL** | F-001 | Correct for the gate: PR_WINDOW_QUERY requests no contributionsCollection, so is_eligible_account would mark every author ineligible (n=0 everywhere). Not correct for the cap: apply_author_cap needs only (login, created_at), both present on every node. |
| C2d | L0-03 | **PASS** | — | D-IN-3's text names merge/decision/conversion/return; 4.3 measures a maintainer action. _resolve_substantive_review_rate applies neither gate nor cap. |
| C3a | L1-05 | **PASS** | — | floor(0.20*10)=2 kept, population 10->6, and the two kept are the OLDEST (most recent dropped first). |
| C3b | L1-05 | **PASS** | — | 89/90/91 d -> False/True/True; 4/5 events -> False/True. |
| C3c | L1-05 | **PASS** | — | Fires at 5 accounts / 6.9 d; 4 never fire; an unmerged account is excluded from the count. |
| C3d | LENS-DET-01 | **PASS** | — | X1 (cap +1) KILLED by 8; X2 (keeps newest) by 4; X3 (age 90->89) by 9; X4 (events 5->4) by 6; X5 (cap 0.20->0.22) by 6; X6 (burst 5->4) by 4; X7 (window 7->8 d) by 2. |
| C4 | L1-13 | **PASS** | — | run_detectors -> ['newcomer_burst'] for 5 accounts / 6.9 d / all merged, and [] for 4 accounts, a 30-day spread, unmerged PRs and empty inputs. The date survives the NewcomerAuthor round-trip; an unmeasured author is skipped (mutant X11 proves the skip is load-bearing). |
| C5 | LENS-PROV-02 | **PASS** | — | Recomputed with cvi.core.models.canonical_json = module = pinned = changelog after 748be6e4...; removing the three keys reproduces the before 419091af... exactly. Three _FROZEN_TABLE rows (test_frozen.py:226-228); M4 completeness test present; fixture diff is 3 files, 1 line each. |
| C6 | L2-02 | **PASS** | — | AST-derived imports: __future__, datetime, typing, cvi.core.constants_v2 only; zero clock/I-O/randomness tokens; tests/core/test_import_graph.py 4 passed. |
| C7 | LENS-DET-01 | **PASS** | — | 17 run (their 5 + my 12): 16 KILLED, 1 equivalent (X12: 'is True' -> truthiness agrees on every value in {True,False,None}; only 'is not False' differs, and that is T3, killed), 0 genuine survivors. Tree restored clean. |
| C8a | L1-08 | **PASS** | — | '2234 passed, 1 skipped, 12 warnings in 9.13s' EXIT=0 - the claim is exact. Ruff on the nine touched files: All checks passed! |
| C8b | TC-4 | **PASS** | — | Both commits clean onto bcdf07b; merged suite 2234 passed, 1 skipped; regenerating the fixture on the merged tree changes nothing, so it is byte-correct (unlike the W1-4 round-3 race). |
| C9 | L0-12 | **FAIL** | F-003 | Three staleness defects: github_graphql.py:500 and :932 cite 'RFC-0002' for this relocation (RFC-0002 is starscout-constants-relocation; this is RFC-0004); the handoff still names RFC-0002 in 4 places and quotes the pre-rebase hash pair d2702ea9->6610ed06; and NewcomerAccountSample's docstring (detectors.py:196-201) still says the schema does not carry account_created_at, which this same commit added. |

## Findings

| id | severity | file:line | statement |
|---|---|---|---|
| F-001 | Warning: | `cvi/derive/bundle.py` | DR_90 - the only rate parameter with a live resolver - scores with neither the D-IN-3 eligibility gate nor the per-author cap. |
| F-004 | Warning: | `cvi/collect/github_graphql.py:1216` | collector_failure is still not emitted and no bundle loader maps the gated fields, so the truthful detail strings reach no consumer. |
| F-003 | Warning: | `cvi/collect/github_graphql.py:500` | Two shipped-source citations point at RFC-0002 (the StarScout relocation) where they mean RFC-0004. |

## Follow-ups (landing order)

1. F-001 apply `apply_author_cap` in `_dr90_decision_cohort`; eligibility gate behind an `author_history` branch — **before any snapshot that ranks on responsiveness**
2. A method tag that says D-IN-3 is unapplied to DR_90 — with #1
3. F-003 fix the two `RFC-0002` citations to `RFC-0004` — at merge
4. F-004 emit/map `collector_failure` when the resolvers are written — with W2-9
5. Refresh the handoff (RFC number, hash pair) — at merge
6. Fix the `NewcomerAccountSample` docstring — at merge
7. One line in `_resolve_dr90_decision_rate`'s docstring: it does not read the collector's field of the same name — with #1
