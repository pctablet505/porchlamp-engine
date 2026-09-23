# Handoff — W2-4f (follow-ups from the Opus review of W2-4)

Worktree: `/home/pctablet505/Projects/cvi-wt-W2-4f`, branch `W2-4f`, base `5b88e08`.
Source review: `/tmp/claude-1000/-home-pctablet505-Projects/2a48e84a-2b94-4a2f-8bff-adbe955a4084/scratchpad/reviews/INT-W2-4-review.md`, Warnings 1/2/3/6 and follow-ups 1/2/3/5 (§6-7).

## Diffstat

```
$ git diff --stat 5b88e08 -- porchlamp/core/normalize.py porchlamp/snapshot/populations.py tests/core/test_normalize_v2.py tests/snapshot/test_populations.py
 porchlamp/core/normalize.py              |  24 +++++-
 porchlamp/snapshot/populations.py        |  17 ++++-
 tests/core/test_normalize_v2.py    |  31 ++++++++
 tests/snapshot/test_populations.py | 150 ++++++++++++++++++++++++++++++++++++-
 4 files changed, 216 insertions(+), 6 deletions(-)
```

`git status --short` reports only these four files modified (plus this handoff, untracked). No public function, class, field or parameter was renamed or removed in either module; every change is additive (new checks that raise, new tests, one test rename).

## 1. Frozen document header (Warning 1 / follow-up 1)

Added `TestFreeze::test_frozen_document_header_fields_are_pinned` (`tests/snapshot/test_populations.py`): asserts the exact header key set, the literal document tag `"porchlamp-populations"`, the literal `POPULATIONS_DOCUMENT_VERSION == 1` (both the module constant and the written `document["version"]`), the literal `min_population == 30`, and that `benchmark_version`/`constants_hash` round-trip `constants_v2`'s values. No production code changed for this item; only the test was missing.

Mutant `POPULATIONS_DOCUMENT_VERSION: int = 1 -> 2` (`porchlamp/snapshot/populations.py:42`), re-run clean (`__pycache__` cleared, `python -B`):

```
$ sed -i 's/POPULATIONS_DOCUMENT_VERSION: int = 1/POPULATIONS_DOCUMENT_VERSION: int = 2/' porchlamp/snapshot/populations.py
$ find . -name "__pycache__" -exec rm -rf {} +
$ python -B -m pytest tests/snapshot/test_populations.py::TestFreeze::test_frozen_document_header_fields_are_pinned -p no:cacheprovider -q
F                                                                        [100%]
AssertionError: assert 2 == 1
 +  where 2 = populations.POPULATIONS_DOCUMENT_VERSION
M9_RED_EXIT=1
$ <restore porchlamp/snapshot/populations.py from the pre-mutant copy>
$ git status --short -- porchlamp/snapshot/populations.py   ->  M porchlamp/snapshot/populations.py   (only the packet's own diff vs 5b88e08, no mutant residue)
$ find . -name "__pycache__" -exec rm -rf {} +
$ python -B -m pytest tests/snapshot/test_populations.py::TestFreeze::test_frozen_document_header_fields_are_pinned -p no:cacheprovider -q
.                                                                        [100%]
M9_RESTORE_GREEN_EXIT=0
```

Mutant `"document": "porchlamp-populations" -> "porchlamp-populations-v2"` (`porchlamp/snapshot/populations.py:205`), same procedure:

```
$ sed -i 's/"document": "porchlamp-populations",/"document": "porchlamp-populations-v2",/' porchlamp/snapshot/populations.py
$ find . -name "__pycache__" -exec rm -rf {} +
$ python -B -m pytest tests/snapshot/test_populations.py::TestFreeze::test_frozen_document_header_fields_are_pinned -p no:cacheprovider -q
F                                                                        [100%]
AssertionError: assert 'porchlamp-populations-v2' == 'porchlamp-populations'
M12_RED_EXIT=1
$ <restore>
$ python -B -m pytest tests/snapshot/test_populations.py::TestFreeze::test_frozen_document_header_fields_are_pinned -p no:cacheprovider -q
.                                                                        [100%]
M12_RESTORE_GREEN_EXIT=0
```

Both M9 and M12 killed.

## 2. Emission order (Warning 2 / follow-up 2)

Added class `TestEmissionOrder` (`tests/snapshot/test_populations.py`):
- `test_population_order_is_the_sorted_ecosystem_param_order` -- members built in reverse ecosystem order, each member's `measured` mapping keyed in reverse param order, asserted against a literal sorted `(ecosystem, param)` list (in-process; proves the order claim independent of input order).
- `test_written_document_order_is_stable_across_pythonhashseed` -- runs `_ORDER_PROBE_SCRIPT` (a fixed 6-ecosystem, 2-param `PopulationSet`, all measured values computed from plain indices, never `hash()`) in three subprocesses with `PYTHONHASHSEED` `"1"`, `"2"`, `"3"`, each writing `populations.json` and returning its raw bytes; asserts each seed's `(ecosystem, param)` order equals the literal sorted list and that all three raw byte strings are identical.

Mutant `for ecosystem, param in sorted(pairs):` -> `for ecosystem, param in pairs:` (`porchlamp/snapshot/populations.py:166`):

```
$ sed -i 's/for ecosystem, param in sorted(pairs):/for ecosystem, param in pairs:/' porchlamp/snapshot/populations.py
$ find . -name "__pycache__" -exec rm -rf {} +
$ python -B -m pytest tests/snapshot/test_populations.py::TestEmissionOrder -p no:cacheprovider -q
FF                                                                       [100%]
test_population_order_is_the_sorted_ecosystem_param_order:
  AssertionError: At index 1 diff: ('npm', 'param_b') != ('crates', 'param_b')
test_written_document_order_is_stable_across_pythonhashseed:
  AssertionError: seed 1: order [('systems','param_a'), ('systems','param_b'), ('go','param_a'),
  ('crates','param_b'), ('crates','param_a'), ('go','param_b'), ('npm','param_a'), ('npm','param_b'),
  ('maven','param_a'), ('maven','param_b'), ('pypi','param_a'), ('pypi','param_b')]
M11_RED_EXIT=1
$ <restore>
$ git status --short -- porchlamp/snapshot/populations.py   ->  M porchlamp/snapshot/populations.py   (packet diff only)
$ find . -name "__pycache__" -exec rm -rf {} +
$ python -B -m pytest tests/snapshot/test_populations.py::TestEmissionOrder -p no:cacheprovider -q
..                                                                       [100%]
M11_RESTORE_GREEN_EXIT=0
```

Both tests fail under the mutant (2 failed); both pass clean after restore. M11 killed.

## 3. Non-finite inputs (Warning 3 / follow-up 3)

Fail-before, captured on the unmodified base file before any edit:

```
$ python -B -c "from porchlamp.core import normalize; print(normalize.mid_rank_ecdf_score(float('nan'), [1.0, 2.0]))"
50.0
```

and, for the population builder:

```
BASE (populations.py, no member-level finiteness check): build_populations did NOT raise
  n = 30  org/nan-repo value = nan  isnan = True
```

Changes:
- `porchlamp/core/normalize.py`: `_numeric` now converts to `float` and rejects a non-finite result with `ValueError(f"{what} must be finite, got {value!r}")`. This is reached by every numeric input in the module (`mid_rank_ecdf_score`'s `value` and every population entry, `mid_rank_ecdf`'s population entries at build time and its closure's query value at call time). Docstring rationale added to the module docstring and both public functions' `Raises` clauses, citing `docs/v2/02-RANKING-DESIGN.md` D-MISS-1 per the packet's instruction: an unmeasurable input is `Unmeasured`, never a neutral score; a NaN scored exactly 50.0 before this check; converting an absence to `Unmeasured` is the caller's job, so the library refuses rather than guessing.
- `porchlamp/snapshot/populations.py`: `_member_problems` now also flags a numeric-but-non-finite measured value: `f"member {member.key!r}: {param!r} measured value must be finite, got {value!r}"`, which `build_populations` raises as part of its existing `ValueError("; ".join(problems))`. Same D-MISS-1 rationale added to the module docstring and `build_populations`'s `Raises` clause.

Verification after the fix (both files, one process):

```
OK query nan -> value must be finite, got nan
OK query inf -> value must be finite, got inf
OK query -inf -> value must be finite, got -inf
OK population population value must be finite, got nan
OK builder population value must be finite, got inf
OK closure value must be finite, got nan
finite unchanged: 12.5 50.0 87.5
OK NaN member -> member 'org/nan-repo': 'downloads_monthly' measured value must be finite, got nan
OK inf member -> member 'org/inf-repo': 'downloads_monthly' measured value must be finite, got inf
```

Tests added:
- `test_normalize_v2.py`: `test_rejects_non_finite_query_value`, `test_rejects_non_finite_population_values`, `test_finite_values_are_unchanged_by_the_finiteness_check` (over-correction guard: the mixed-ties oracle values are asserted unchanged).
- `test_populations.py` (`TestNonFiniteRejection`): `test_nan_measured_value_raises_naming_key_and_param`, `test_infinite_measured_value_raises_naming_key_and_param` (both assert the repo key, the param, and the word "finite" appear in the message), `test_finite_members_are_unchanged_by_the_finiteness_check` (over-correction guard: an all-finite 30-member build still yields `fallback=False`, `n=30`, `values["org/r1"] == 1.0`).

## 4. Test rename (Warning 6 / follow-up 5)

`test_nine_below_min_is_flagged` -> `test_twenty_nine_below_min_is_flagged` (`tests/snapshot/test_populations.py`, class `TestMinPopulationFallback`); body unchanged (still builds `_pypi_members(29)`, still asserts `fallback is True`, `n == 29`, `len(pop.values) == 29`). The 30 boundary already had its own test, `test_at_min_is_not_flagged`, unchanged. Both boundary values (29 and 30) now each have a test whose name matches what it builds.

## Full verification

Packet tests (4 files):

```
$ python -B -m pytest tests/core/test_normalize_v2.py tests/snapshot/test_populations.py -p no:cacheprovider
collected 31 items
tests/core/test_normalize_v2.py ...........                              [ 35%]
tests/snapshot/test_populations.py ....................                  [100%]
31 passed in 0.19s
PACKET_EXIT=0
```

`tests/core` + `tests/snapshot`:

```
$ python -B -m pytest tests/core tests/snapshot -p no:cacheprovider
........................................................................ [ 30%]
........................................................................ [ 61%]
........................................................................ [ 91%]
...................                                                      [100%]
235 passed in 0.48s
EXITCODE_IS=0
```

Full suite:

```
$ python -B -m pytest tests/ -n 4 -p no:cacheprovider
[...]
1452 passed, 12 warnings in 26.34s
EXITCODE_IS=0
```

The 12 warnings are pre-existing (`starlette`/`fastapi`/`multiprocessing` deprecation notices), unrelated to this packet's files.

Ruff, the four owned files:

```
$ ruff check porchlamp/core/normalize.py porchlamp/snapshot/populations.py tests/core/test_normalize_v2.py tests/snapshot/test_populations.py
All checks passed!
RUFF_OWNED_EXIT=0
```

Ruff, repo-wide (`porchlamp/ scripts/ tests/`):

```
$ ruff check porchlamp/ scripts/ tests/
Found 4 errors (F401, unused imports) in scripts/curate_dataset_llm.py
RUFF_FULL_EXIT=1
```

`scripts/curate_dataset_llm.py` is byte-identical to `git show 5b88e08:scripts/curate_dataset_llm.py` (`diff` exit 0) -- pre-existing on the base commit, untouched by this packet, and outside this packet's `Owns` list. Not fixed here per R-10 (a packet touches only its owned files); flagged separately as a standalone cleanup task rather than folded into this diff.

`grep -rn "print(" porchlamp/core/normalize.py porchlamp/snapshot/populations.py` and `grep -rn "TODO" porchlamp/core/normalize.py porchlamp/snapshot/populations.py`: both exit 1 (no match).

## Ownership check

`git diff --stat 5b88e08` (whole worktree) touches exactly: `porchlamp/core/normalize.py`, `porchlamp/snapshot/populations.py`, `tests/core/test_normalize_v2.py`, `tests/snapshot/test_populations.py`, plus `.agents/W2-4f/handoff.md` (untracked, this file). No other file changed.

## COULD NOT VERIFY

None. All four packet items implemented, tested, and mutation-proven; full suite and packet ruff clean.
