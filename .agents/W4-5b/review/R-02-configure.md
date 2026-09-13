# R-02 Screen D Configure — FAIL (Blocker B-1)
I wrote my own property test rather than trusting theirs (`scratch/W4-5b-review/weights_property.py`,
a faithful Python port of `normalizeWeights`/`applyPresetRespectingLocks`): exhaustive over all 32
lock subsets × 6 slider values, plus 4,000 randomised 6-step drag/lock walks, plus presets under every
lock subset.

**40 property failures, every one of them P1 (sum ≠ 1), all in the same branch**: when every OTHER
pillar is locked, `free` is empty and the function returns early WITHOUT re-pinning the dragged
slider unless `fixedSum > 1`. P2 (locked pillars byte-identical) and P3 (non-negative) hold
everywhere — the function is right except for this branch.

Minimal reproduction:
```
start   balanced .25/.25/.20/.15/.15                     (sum 1.0)
locks   responsiveness, criticality, governance, rigor
drag    accessibility 0.15 -> 0.02
result  .25/.25/.20/.15/.02                              (sum 0.87)
```
`app.js::runReproject` then POSTs that vector unconditionally (no sum guard), and the server rejects
it — verbatim, from my own probe on port 8917:
```
sum 0.87 (four locked + drag) -> HTTP 400
{"error": {"endpoint": "/v1/cvi/reproject", "status": 400,
           "detail": "1 validation error for ReprojectRequest\n  Value error, weights must sum to 1.0, got 0.87"}}
```
So the user locks four sliders, moves the fifth, and Screen D shows an API error banner instead of a
re-projection. `renderControls` does flag the off-sum state visually, which is why this survived the
live-browser pass. Fix (1 line): in the `free.length === 0` branch always set
`next[changedPillar] = max(0, 1 - lockedSum)` (or disable the last free slider).

Everything else on this screen checks out:
- Presets respect locks (`applyPresetRespectingLocks`): sum = 1 and locked values untouched across
  all 32 lock subsets in my property run — their fix is correct.
- `w=` round-trips (`parseWeightsParam`/`serializeWeightsParam`, malformed → BALANCED_WEIGHTS).
- U-11: the buttons are labelled by persona name but `copy.CONFIGURE_CUSTOM_LABEL` is rendered
  persistently and `ReprojectResponse.lens` is the literal `"custom"`; no code path calls a custom
  vector a persona.
- Debounce 120 ms ≤ 150 (`app.js`), greyed-table/stale pattern reused from W4-5a.
- Delta list `topMovers` uses `balanced_rank - rank`, matching the contract's own sign convention.
- **Reproject arithmetic: 7/7 weight vectors exact** against my independent oracle
  (`scratch/W4-5b-review/reproject_oracle.py`) — balanced, equal, responsiveness-heavy,
  zero-accessibility, two-zero-pillars, single-pillar (four zeros) and a tilted vector that drops a
  row below the 0.60 floor. Row membership, `cvi` (2 dp), coverage, tier (with ceiling), rank order
  and `rank_delta == balanced_rank - rank` all agree. A rounding difference I first suspected was my
  own oracle's fault: 67.475 is not exactly representable, and Python's `round` and SQLite's `ROUND`
  agree on it (both 67.47) — verified directly, no defect.
