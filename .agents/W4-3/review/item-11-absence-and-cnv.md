# Item 11 — Absence table and the three COULD-NOT-VERIFY notes

No verbatim dispatch text is recorded in `docs/v2/00-README.md` (the W4-2
landing row only says "F-013 (ranked/summary/matrix read paths) moves to W4-3").
The eight deliverables are reconstructed from that row, the W4-3 receipt row and
the handoff's own numbering ("deliverable 6/7/8"):

| # | Deliverable | Present | Evidence |
|---|---|---|---|
| 1 | `list_ranked` | yes | items 2, 4 |
| 2 | `list_not_ranked` | yes | item 5 |
| 3 | `snapshot_summary` | yes | item 1 |
| 4 | `matrix_points` | yes | item 1 (N-3/N-4 are shape notes) |
| 5 | `repo_evidence` | yes | item 1 (all W2-11 fields) |
| 6 | `reproject` + parity | yes | item 3 (B-1 on validation) |
| 7 | indexes/migration + p95 | yes | item 6 (W-3 on the stated gain) |
| 8 | tie-break parity + negative control | yes | item 2 |
| + | mutation battery >= 10 | yes | item 9, all 10 re-confirmed |

Nothing absent.

CNV-1 "matrix persona coordinates are not reprojected" -- CORRECT AS STATED.
D-MAT-1 defines leverage/friction from the balanced pillar renormalisation and
no `02`/`04` decision defines a persona coordinate; the docstring says so
(line 599). Verdict: accept; a product question for W4-4/`06`, not a defect.

CNV-2 "tier cutoffs/ceilings built from constants_v2 at import time" --
CORRECT AND HARMLESS today (D-VER-2: one frozen module, covered by
constants_hash). Verdict: accept. The real exposure is not the import-time
binding but the missing test for the unrounded-composite rule (W-4b).

CNV-3 "`reproduce_command` follows `porchlamp/snapshot/cli.py`'s docstring because
`porchlamp/cli.py` does not exist" -- CONFIRMED: `ls porchlamp/cli.py` -> no such file;
that docstring's invocation is `python -m porchlamp.snapshot reproduce <id>`.
Verdict: accept, revisit when the unified CLI lands.
