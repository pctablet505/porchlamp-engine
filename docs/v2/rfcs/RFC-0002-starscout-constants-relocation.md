# RFC-0002: starscout-constants-relocation

## Motivation

Packet W1-10 wires `porchlamp.starscout.filter_fraudulent_star_velocity` (D-IN-5,
spec 10.3 Algorithm 3) into `scripts/build_universe.py` at universe entry —
before this packet the filter existed but nothing called it and nothing
recorded whether the lockstep penalty fired (found by the W3-2 review,
`docs/v2/00-README.md` 2026-09-13 lead log row). The filter's five
thresholds (`STARS_SCOUT_WINDOW_DAYS`, `STARS_SCOUT_MIN_EVENTS`,
`STARS_SCOUT_FORK_RATIO`, `STARS_SCOUT_BURST_THRESHOLD`,
`STARS_SCOUT_LOCKSTEP_PENALTY`) lived in `porchlamp.constants` — the v1 module,
outside `CONSTANTS_HASH`'s coverage — so a change to any of them was an
invisible edit: it would move which repositories get flagged `star_burst`
(`03` section 6) and what `stars_audited` records, with no failing test and
no entry in `docs/v2/rfcs/CHANGELOG.md`. `docs/v2/rfcs/README.md`'s
contract is "every value the v2 core computes with ... lives in that one
module"; a universe-entry gate that decides whether a repository's stars
are trusted is exactly this kind of value, D-IN-5 scope note
notwithstanding (see Evidence below for why it still needs no version
bump).

This RFC relocates the five thresholds into `porchlamp.core.constants_v2`,
updates `porchlamp.starscout`'s one import site to read them from there, and
registers them in `constants_dict()` / `tests/core/test_frozen.py`'s
`_FROZEN_TABLE`. No threshold's value changes.

## Change

| Constant | Before | After |
|---|---|---|
| `STARS_SCOUT_WINDOW_DAYS` | `90` (in `porchlamp.constants`, unhashed) | `90` (in `porchlamp.core.constants_v2`, hashed) |
| `STARS_SCOUT_MIN_EVENTS` | `5` (in `porchlamp.constants`, unhashed) | `5` (in `porchlamp.core.constants_v2`, hashed) |
| `STARS_SCOUT_FORK_RATIO` | `100.0` (in `porchlamp.constants`, unhashed) | `100.0` (in `porchlamp.core.constants_v2`, hashed) |
| `STARS_SCOUT_BURST_THRESHOLD` | `500` (in `porchlamp.constants`, unhashed) | `500` (in `porchlamp.core.constants_v2`, hashed) |
| `STARS_SCOUT_LOCKSTEP_PENALTY` | `0.30` (in `porchlamp.constants`, unhashed) | `0.30` (in `porchlamp.core.constants_v2`, hashed) |

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump). Same class of
change as RFC-0001 (`AUTHOR_UNMEASURED_MAX_FRACTION`): a value with no
scoring-path consumer moves module, not meaning. D-IN-5 states plainly
"Stars never enter the composite" — `star_velocity_flag` and
`stars_audited` reach `porchlamp.verify.detectors.DetectorInputs` (the
`star_burst` detector) and `porchlamp.store.writer`'s `stars_audited` column,
never `porchlamp.core.engine.score`/`aggregate.*`/`matrix.*`/`params.*`. Moving
these five thresholds' storage location therefore cannot move any
`ScoreRecord.composite`/`.pillars`/`.params` field for any repository; it
only changes whether an edit to one of them in the future is caught.

## Evidence

- **Sensitivity** (D-AGG-3): not applicable, same reasoning as RFC-0001 —
  no consumer of these five constants sits anywhere in
  `porchlamp.core.engine.score`, `aggregate.*`, `matrix.*` or `params.*`
  (`porchlamp.starscout` is not imported by any of them; `grep -rn
  "import.*starscout" porchlamp/core` returns nothing). A Kendall-tau
  perturbation report requires a scored ranking to perturb; the composite
  is structurally unaffected by anything these thresholds decide.
- **Anchors** (D-VER-4): not applicable, same reason — no anchor repo's
  gate outcome, tier or composite reads `porchlamp.starscout`.
- **Rank movement**: **none**, by construction — `CONSTANTS_HASH` moved
  (`d2702ea93a7ad9eb22696942dd3994d63f066ff2bf2e68e52abfc8235433570e`,
  post-RFC-0001's landed value, ->
  `446c6a25db4e8c1f9d528f98a332d52bdf5ba570f07774bd674d9c816fdd09d0`,
  recomputed with `python -c "from porchlamp.core import constants_v2 as c;
  print(c.CONSTANTS_HASH)"`) but every one of `porchlamp.starscout`'s own 22
  existing oracle tests (`tests/test_starscout.py`) and the adversarial
  burst-ring test (`tests/test_adversarial_tier5_hardening.py
  ::TestStarScoutAntiGamingAdversarial`) is pinned to a literal expected
  integer and passes unchanged after the relocation — the values did not
  move, only where they are registered and enforced.
- **Direct evidence for the relocation itself**: before this RFC, editing
  `porchlamp.constants.STARS_SCOUT_BURST_THRESHOLD` from `500` to any other
  value changed `filter_fraudulent_star_velocity`'s behaviour with the v2
  `CONSTANTS_HASH` unchanged and no `_FROZEN_TABLE` row failing — reproduced
  as this packet's own mutant proof (`.agents/W1-10/handoff.md`: "threshold
  changed, hash unchanged" mutant, red before this RFC's `_FROZEN_TABLE`
  row existed, still red without the row even after the constant moved,
  green — i.e. caught — only with both the relocation and the row in
  place). After this RFC, the same edit moves `CONSTANTS_HASH` and fails
  `tests/core/test_frozen.py::test_frozen_table[STARS_SCOUT_BURST_THRESHOLD]`
  by name.

## Reviewer

Not yet reviewed independently — this RFC accompanies packet W1-10 (universe
entry: star-velocity filter applied and recorded) and is submitted for
review alongside that packet.
