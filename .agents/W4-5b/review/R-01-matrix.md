# R-01 Screen B Matrix — PASS with 2 gaps
Encoding (`components/scatter.js`), colour-blind-safe dual encoding, all four pairs read from source:
| quadrant | shape | colour token |
|---|---|---|
| Sweet Spot | diamond (`<path>` 4-point) | `--quad-sweet-spot` via `.quad-sweet-spot` |
| Crucible | hexagon (`<polygon>`, 6 pts at 60°) | `--quad-crucible` |
| Nursery | circle (`<circle>`) | `--quad-nursery` |
| Graveyard | cross (two `<line>`s) | `--quad-graveyard` |
Shape is carried in SVG **and** canvas (`drawMatrixCanvas` repeats all four paths), so the encoding is
genuinely identical across the budget switch.

- Size: `radiusForStars` sqrt-scales AREA between MIN/MAX radius; `null` → `DEFAULT_RADIUS` and the
  view renders `copy.MATRIX_UNAUDITED_STARS_NOTE`. `starsDomain` is computed over ALL points, not the
  visible subset, so hiding a quadrant does not silently rescale every other marker. Correct.
- Y-flip: `toSvgY(leverage) = DOMAIN_MAX - leverage`; washes place Sweet Spot at y=0..50 (high
  leverage, low friction) — matches `porchlamp/core/matrix.py`'s quadrant rule. The arrow-key bug the
  implementer found is genuinely fixed: `nearestMarkerInDirection` computes `cx/cy` through
  `toSvgX/toSvgY` (screen space) and its comment states why — no second sign derivation exists.
- Gated excluded + caption: `excluded_count` = the snapshot's gated count (mutant M1 killed).
- Quadrant toggle 1–4 really re-renders: the reducer `delete`s the key on un-hide
  (`state.js:436`) and `shellShape` folds `Object.keys(hidden).sort().join(",")` into the rebuild
  key, so the string changes in BOTH directions. The bug they found is properly fixed.
- Hover card: name, Porchlamp, coverage badge, tier + open hint; rendered on hover AND focus.
- Click/Enter → drawer via the existing `[data-owner][data-name]` delegation; `repo=` is pushed.
- Reset view / wheel zoom / arrow pan all wired.

GAPS
- **W-3** Above `SVG_MARKER_BUDGET` (2,000) the markers have no DOM nodes at all, so Tab focus, the
  delegated click-to-open-drawer and `syncMatrixHover`'s `.matrix-marker` class toggle all stop
  working — only the focus overlay for an already-known key survives. The *encoding* is identical
  above the threshold; the *interaction* is not. Harmless on the 73-point fixture and at the owner's
  N≤300 mandate; a real gap at the 10k budget the perf table advertises.
- **N-1** `views/matrix.js`'s docstring claims "`+`/`-` zoom"; no such key binding exists (grep:
  only `[data-matrix-zoom-in|out]` buttons). Wheel zoom and arrow pan ARE bound, so 06 §3 B's
  "zoom/pan with keys and wheel" is met — the docstring is wrong, not the code.
- **N-2** `renderFocusOverlaySvg` draws at `DEFAULT_RADIUS + 0.8` regardless of the marker's own
  stars-scaled radius, so above the budget the highlighted marker changes size on focus.
