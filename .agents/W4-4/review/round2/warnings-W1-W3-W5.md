# Warnings verified fixed — W-1, W-3, W-4, W-5, W-6, W-7

## W-1 not-ranked counts derived from the split — FIXED *within the endpoint*
```
  persona=balanced        rows g=7 u=6 ph=0   counts={'rated':64,'gated':7,'unrated':6,'total':77}
    counts.gated == len(gated): True
    counts.unrated == len(unrated)+len(persona_hidden): True
    counts.rated == leaderboard.total (64): True
    counts.total == rated+gated+unrated == all 77 rows: True
  persona=career_capital  rows g=7 u=6 ph=6   counts={'rated':58,'gated':7,'unrated':12,'total':77}
    all four invariants: True   (round 1's 64-vs-58 mismatch is closed)
```
See `NB-1-cross-endpoint-counts.md` — the fix left `/leaderboard` and
`/snapshot` on the raw store triage, so the two now disagree.

## W-3 the `-` sort direction — FIXED
Store level, every allowed `sort` (10 values incl. `None`):
```
  desc_monotonic=True asc_monotonic=True orders_differ=True same_set=True rank_stable=True
```
NULLs last in BOTH directions (proven on `accessibility`, the one column
carrying NULLs in this fixture); `rank`/`tie_group` unchanged by the
direction toggle; offset paging still partitions exactly under
`descending=False` (`sort=coverage`, `sort=rigor`, page size 7).
API level: `sort=X` vs `sort=-X` now differ for `coverage`, `tier`,
`p_rigor`, with the same row set and identical `rank` values.

## W-4 the two detection gaps — FIXED
Mutants RX2 (`_matrix_point` plots a NULL-quadrant point) and RX3
(`_no_ceiling_or_band` bands swapped) now both KILLED, by
`test_matrix_excludes_a_point_whose_quadrant_is_null_even_with_coordinates`
and `test_no_ceiling_or_band_assigns_a_and_b_correctly_via_reproject`.

## W-5 the backwards `scripts.serve` import — FIXED
`porchlamp/serve/guard.py` now owns both guards; `scripts/serve.py` imports FROM
it. Plant on the detached copy:
```
  porchlamp/serve/app.py: from scripts.serve import main as _planted  # PLANTED
  FAILED tests/core/test_import_graph.py::test_serve_imports_only_porchlamp_fastapi_pydantic_or_stdlib
  AssertionError: porchlamp/serve/app.py imports 'scripts.serve' -- porchlamp/serve must never
                  import scripts.* (the reverse of the allowed direction)
```
Restored byte-identical; `git status --porcelain` empty.

## W-6 unified finalisation policy — FIXED
On a written-but-unfinalised snapshot every snapshot-touching endpoint
503s (round 1: three did, three served):
```
  /v1/porchlamp/leaderboard          -> 503      /v1/porchlamp/matrix                -> 503
  /v1/porchlamp/not-ranked           -> 503      /v1/porchlamp/repos/a/r0            -> 503
  /v1/porchlamp/snapshot             -> 503      /v1/porchlamp/repos/a/r0/evidence   -> 503
  POST /v1/porchlamp/reproject       -> 503      /v1/porchlamp/methodology           -> 200 (touches no snapshot)
```

## W-7 a 500 uses the error shape — FIXED
`{"error":{"endpoint":…,"status":500,"detail":"internal server error"}}`,
via `_internal_error_response` plus a broad `Exception` handler.
