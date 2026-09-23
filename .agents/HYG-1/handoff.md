# HYG-1 handoff — clear 19 ruff findings in legacy test files (no behaviour change)

- **Status: DONE.** `ruff check porchlamp/ scripts/ tests/` now reports exactly the
  4 findings in `scripts/curate_dataset_llm.py` (owned by another packet,
  untouched here) and nothing else. All 19 findings assigned to this packet
  are cleared in exactly the 4 files named in the brief.
- Branch `HYG-1`, base `e29fb79` (main).
- Worktree `/home/pctablet505/Projects/cvi-wt-HYG-1`, interpreter
  `/home/pctablet505/Projects/contributor-value-index/.venv/bin/python`
  (editable install of the main checkout; `PYTHONPATH=$PWD` set in the
  worktree for every command below).

## Diffstat (`git diff --stat e29fb79`)

```
 tests/benchmark_sla_runner.py           | 11 +++++------
 tests/test_adversarial_comparator_m3.py | 13 +++++--------
 tests/test_perf_sla_stress.py           |  3 +--
 tests/test_webapp.py                    |  2 +-
 4 files changed, 12 insertions(+), 17 deletions(-)
```

Exactly the four files named in the brief. `scripts/curate_dataset_llm.py`
was not touched (`.agents/HYG-1/handoff.md`, this file, is new and outside
the stat).

## What changed, per finding

### tests/benchmark_sla_runner.py (10 findings)
- F401 x7 (auto-fixed via `ruff check --fix`, scoped to the 4 target files
  only): unused imports `asyncio`, `pathlib.Path`, `typing.Callable`,
  `porchlamp.models.{Ecosystem,Persona,Quadrant,Tier}`. Verified by grep that none
  of these names are referenced anywhere else in the file (the only
  remaining hits are string literals like `"Persona BALANCED"`, not the
  imported symbols).
- RUF046 (manual, not in the safe-fix set): `idx = int(round(...)) - 1` ->
  `idx = round(...) - 1`. `round(x)` called with no `ndigits` on a `float`
  always returns `int` in Python 3, so the outer `int()` was a no-op.
  Verified with a 2,000-case randomized equivalence check
  (`int(round(x)) - 1 == round(x) - 1` and `type(round(x)) is int` for every
  case) before editing.
- B023 x2 (manual — "function definition does not bind loop variable
  `errors`"): `worker_task(worker_id: int)` closed over the loop-scoped
  `errors` list inside `for c in concurrency_levels:`. Fixed by binding it
  as a default argument (`def worker_task(worker_id: int, errors: list[str]
  = errors)`), which captures the current iteration's list object at
  `def`-time — the standard fix for this rule — plus a comment naming the
  finding. The only call site, `executor.submit(worker_task, w)`, passes a
  single positional arg, so the default fires every time; behaviour is
  unchanged since all workers for iteration `c` are submitted and awaited
  (`as_completed`) before the loop advances to `c`'s successor in the
  original code too. Verified with a standalone repro of the same
  loop/closure/`ThreadPoolExecutor` shape, old vs. new, across 5 concurrency
  levels x 20 repeats: identical `(results, errors, len(errors))` per level
  every time.

### tests/test_adversarial_comparator_m3.py (5 findings)
- SIM114 (auto-fixed): collapsed the `if / elif` with identical bodies into
  one `if (A) or (B):`. Branches were mutually exclusive on
  `item["higherBetter"]`, so combining with `or` is equivalent to the
  original `if/elif` (no double-assignment risk).
- RUF021 (auto-fixed): added explicit parens around the `and` sub-expression
  in `A and B or C` -> `(A and B) or C`. Python's `and`/`or` precedence
  already evaluates it that way; the parens are a no-op on semantics, purely
  a readability/lint fix.
- F541 x2 (auto-fixed): dropped the extraneous `f` prefix on two string
  literals with no placeholders (`f'<td colspan="4">...'` ->
  `'<td colspan="4">...'`), used in `assert ... in diff_3["tbody"]` /
  `diff_4["tbody"]`. Content of the string is byte-for-byte identical, only
  the redundant prefix is gone.
- RUF001 (manual): `"SPATIAL POSITIONING (LEVERAGE × FRICTION)"` -> `(...
  LEVERAGE x FRICTION)"`. Checked (grep) that this string is not part of any
  assertion on rendered output anywhere in the file — it is only ever
  produced once, as a `"cat"` row-header value fed into the diff-table HTML
  builder, and no test asserts on this literal string. Per the packet rule,
  replaced with its ASCII equivalent rather than adding a `noqa`.

### tests/test_perf_sla_stress.py (3 findings)
- F401 x2 (auto-fixed): unused `porchlamp.models.Ecosystem`, `porchlamp.models.Persona`
  imports. Verified by grep that remaining occurrences of "Ecosystem" /
  "Persona" in the file are only comments and string literals
  (`"Persona BALANCED"`, etc.), not the imported symbols.
- RUF046 (manual, same reasoning as above): `idx = int(round(0.95 * ...)) -
  1` -> `idx = round(0.95 * ...) - 1`.

### tests/test_webapp.py (1 finding)
- RUF002 (manual): class docstring `"""Requirement 4: Screen E Repository
  Comparator (2–4 repositories)."""` (EN DASH) -> `(2-4 repositories)`
  (HYPHEN-MINUS). Grepped the file for the EN DASH character: this is the
  only occurrence, it's a docstring (not asserted on), so it was normalized
  to ASCII rather than suppressed with `noqa`.

## Ruff — before (baseline on `e29fb79`, `porchlamp/ scripts/ tests/`)

23 findings total: 4 in `scripts/curate_dataset_llm.py` (out of scope — owned
by another packet) + 19 across the four target files (10 + 5 + 3 + 1, matching
the brief exactly).

## Ruff — after (verbatim, exit code included)

```
$ /home/pctablet505/Projects/contributor-value-index/.venv/bin/ruff check porchlamp/ scripts/ tests/
F401 [*] `time` imported but unused
  --> scripts/curate_dataset_llm.py:16:8
   |
14 | import re
15 | import sys
16 | import time
   |        ^^^^
17 | import urllib.request
18 | from datetime import datetime, timezone
   |
help: Remove unused import: `time`
   |
15 | import sys
   - import time
16 | import urllib.request
   |

F401 [*] `datetime.datetime` imported but unused
  --> scripts/curate_dataset_llm.py:18:22
   |
16 | import time
17 | import urllib.request
18 | from datetime import datetime, timezone
   |                      ^^^^^^^^
19 | from pathlib import Path
20 | from typing import Any, Optional
   |
help: Remove unused import
   |
17 | import urllib.request
   - from datetime import datetime, timezone
18 | from pathlib import Path
   |

F401 [*] `datetime.timezone` imported but unused
  --> scripts/curate_dataset_llm.py:18:32
   |
16 | import time
17 | import urllib.request
18 | from datetime import datetime, timezone
   |                                ^^^^^^^^
19 | from pathlib import Path
20 | from typing import Any, Optional
   |
help: Remove unused import
   |
17 | import urllib.request
   - from datetime import datetime, timezone
18 | from pathlib import Path
   |

F401 [*] `typing.Any` imported but unused
  --> scripts/curate_dataset_llm.py:20:20
   |
18 | from datetime import datetime, timezone
19 | from pathlib import Path
20 | from typing import Any, Optional
   |                    ^^^
21 |
22 | ROOT = Path(__file__).resolve().parent.parent
   |
help: Remove unused import: `typing.Any`
   |
19 | from pathlib import Path
   - from typing import Any, Optional
20 + from typing import Optional
21 |
   |

Found 4 errors.
[*] 4 fixable with the `--fix` option.
$ echo "EXIT:$?"
EXIT:1
```

Exit code 1 is expected: ruff exits non-zero whenever any findings remain,
and the 4 `scripts/curate_dataset_llm.py` findings are explicitly out of
scope for this packet (that file is being deleted by another packet). All 19
findings assigned to HYG-1 are gone; no new findings were introduced in the
four edited files or anywhere else in `porchlamp/ scripts/ tests/`.

## Pytest — the four edited files (verbatim tail, exit code included)

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -m pytest tests/benchmark_sla_runner.py tests/test_adversarial_comparator_m3.py tests/test_perf_sla_stress.py tests/test_webapp.py -p no:cacheprovider
........................................................................ [ 67%]
..................................                                       [100%]
106 passed, 2 warnings in 3.40s
$ echo "EXIT:$?"
EXIT:0
```

Note: `tests/benchmark_sla_runner.py` is a standalone benchmark script (no
`test_*` functions; not matched by `[tool.pytest.ini_options]`'s default
discovery pattern), so it contributes 0 collected tests but must still
*import* cleanly under pytest — confirmed above (no collection error) and
separately by `importlib`-loading the edited file directly and calling
`percentile()` on several inputs (matches pre-edit values; `int(round(x))`
and `round(x)` are identical for every case exercised).

## Pytest — full suite (verbatim tail, exit code included)

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -m pytest tests/ -n 4 -p no:cacheprovider
bringing up nodes...
........................................................................ [  5%]
........................................................................ [ 10%]
........................................................................ [ 15%]
........................................................................ [ 20%]
........................................................................ [ 25%]
........................................................................ [ 30%]
........................................................................ [ 35%]
........................................................................ [ 41%]
........................................................................ [ 46%]
........................................................................ [ 51%]
........................................................................ [ 56%]
........................................................................ [ 61%]
........................................................................ [ 66%]
........................................................................ [ 71%]
........................................................................ [ 76%]
........................................................................ [ 82%]
........................................................................ [ 87%]
........................................................................ [ 92%]
........................................................................ [ 97%]
....................................                                     [100%]
1404 passed, 12 warnings in 25.16s
$ echo "EXIT:$?"
EXIT:0
```

No SLA/timing flakes observed; single run was clean, so no rerun-alone step
was needed.

## Behavioural-equivalence checks (ad hoc, not committed as test code)

Two of the fixes touch runtime logic rather than pure style, so each was
independently verified for equivalence before editing (see file-by-file
notes above for the exact checks): RUF046's `int(round(x))` vs `round(x)`
(2,000+2,000 randomized cases, exact match + type check), and B023's
default-argument binding vs. the original loop-scoped closure (5 concurrency
levels x 20 repeats against a minimal repro of the same
loop/closure/`ThreadPoolExecutor` shape, identical results and error lists
every time). These were exploratory scripts run against `/dev/stdin`, not
added to the repo — the pre-existing tests in the four files (106 passing)
are the committed regression coverage for this packet, per "no behavioural
change" rather than "add new tests" being the acceptance bar.

## Rules followed

- No blanket ignores added to `pyproject.toml`.
- `ruff check --fix` was run scoped to exactly the four target files (not
  the whole tree), so it could not touch `scripts/curate_dataset_llm.py`.
- Every hunk from the auto-fix pass was inspected via `git diff` before the
  manual fixes were layered on top.
- Both B023 sites bound the loop variable via a default argument (not a
  factory — no separate factory function was needed since the closure only
  needed one captured name) and carry a `# B023: ...` comment.
- RUF001/RUF002 were resolved by checking for assertions on the literal
  string first (grep, both files) — neither was asserted on, so ASCII
  replacement was used instead of `# noqa`.
