# W0-3g — follow-ups from the Opus review of W0-3f

- Worktree: `/home/pctablet505/Projects/cvi-wt-W0-3g`
- Branch: `W0-3g`, base `5b88e08` (main)
- Interpreter: `/home/pctablet505/Projects/contributor-value-index/.venv/bin/python`
  (repo venv, Python 3.12). `PYTHONPATH=$PWD` verified to resolve `porchlamp.__file__`
  inside this worktree before every command below.
- Source: `/tmp/claude-1000/-home-pctablet505-Projects/2a48e84a-2b94-4a2f-8bff-adbe955a4084/scratchpad/reviews/INT-W0-3f-review.md`,
  Warnings 1-2 and Consider 3-6 (section 8), design context `docs/v2/03-ANTI-MANIPULATION.md`
  section 5 ("Promotion reads only envelopes in the manifest").

## Item 1 — degraded snapshots (Warning 1)

Two-sided fix, both required by the packet:

(a) `porchlamp/snapshot/cli.py` `_freeze`: when the scan's `invalid_json` is
non-empty and `--allow-invalid` is absent, prints each `INVALID_JSON <key>`
plus a `refusing to freeze` line and returns 1 **before** calling
`build_manifest`/`rebuild`/`write_manifest` — no `snapshots/` directory is
created. With `--allow-invalid`, behavior is unchanged from W0-3f: writes the
manifest, still returns 1, still prints the keys.

(b) `porchlamp/snapshot/reproduce.py`: `EnvelopeCheck` gets a new field
`invalid_json_frozen`, populated in `check_envelopes` from
`manifest.get("invalid_json", [])` (mirrors the existing `stale_native_hashes`
line one row above it) and included in `.clean`. `cli.py` `_reproduce` prints
each key as `INVALID_JSON_FROZEN <key>` and adds `N invalid-frozen` to the
summary line. This is populated from the **frozen manifest**, independent of
whether a live rescan of the data dir finds anything wrong, so it cannot be
defeated by deleting or repairing the corrupt file after the fact.

New constant `REASON_INVALID_JSON_FROZEN = "INVALID_JSON_FROZEN"` in
`porchlamp/snapshot/manifest.py`, next to `REASON_INVALID_JSON` (see item 3).

### Fail-before / after, on the real sample-200 data (not a synthetic fixture)

Setup: copied `/home/pctablet505/Projects/cvi-scratch/sample-200` (2,181 raw
envelopes) to `/home/pctablet505/Projects/cvi-scratch/W0-3g/sample`,
`chmod -R u+w`. Corrupted the same envelope the review used
(`raw/scorecard/AtsushiSakai__PythonRobotics.json`, first `}` -> `x`).

Method: no commit existed yet to `git show <my-sha>:<path>` from, so the
pre-change state was restored by temporarily overwriting
`porchlamp/snapshot/{cli,manifest,reproduce,__init__}.py` with
`git show 5b88e08:<path> > <path>` (verified empty `git diff --stat 5b88e08`
after), running the demo, then restoring my versions from a byte-for-byte
backup taken before the revert and re-confirming `git diff --stat 5b88e08`
matched my full diffstat exactly (39/12/13/25 lines changed) before
continuing. `git status --short` after every restore showed only the 10
files this packet owns.

**BASE (5b88e08) — the defect, reproduced exactly:**

```
$ python -m porchlamp.snapshot freeze --data-dir sample --as-of 2026-09-12T00:00:00+00:00
snapshot 20260912T0000000000-95c454a78249
merkle_root 95c454a782494eecc978c0d2809be81408f4ac3153d6e70ab7ccd399b8d975fd
envelopes 2180 (native 0, backfilled 2180)
dump_hash 258f9fa373d2ac01ca12f8ca1642fd1741521f7dd75e1e5aa5a2215d0b004135
manifest .../sample/snapshots/20260912T0000000000-95c454a78249/manifest.json
INVALID_JSON scorecard/AtsushiSakai__PythonRobotics
FREEZE_EXIT=1
```

(snapshot id, merkle root and dump hash are byte-identical to the review's
own §4 measurement — same corruption, same sample, same result.)

```
$ rm raw/scorecard/AtsushiSakai__PythonRobotics.json
$ python -m porchlamp.snapshot reproduce 20260912T0000000000-95c454a78249 --data-dir sample
snapshot 20260912T0000000000-95c454a78249
envelopes 2180 match, 0 mismatched, 0 missing, 0 unexpected, 0 invalid, 0 stale-hash
dump_hash 258f9fa373d2ac01ca12f8ca1642fd1741521f7dd75e1e5aa5a2215d0b004135
frozen_dump_hash 258f9fa373d2ac01ca12f8ca1642fd1741521f7dd75e1e5aa5a2215d0b004135
MATCH
REPRODUCE_EXIT=0
```

Confirmed: on base code, a repository silently drops out of a frozen
snapshot and `reproduce` reports a clean MATCH once the corrupt file is gone.

**MY VERSION — same sequence:**

```
$ python -m porchlamp.snapshot freeze --data-dir sample --as-of 2026-09-12T00:00:00+00:00
INVALID_JSON scorecard/AtsushiSakai__PythonRobotics
refusing to freeze: 1 unreadable envelope(s); pass --allow-invalid to write a manifest anyway
FREEZE_REFUSED_EXIT=1
$ ls sample/snapshots
ls: cannot access 'sample/snapshots': No such file or directory
```

```
$ python -m porchlamp.snapshot freeze --data-dir sample --as-of 2026-09-12T00:00:00+00:00 --allow-invalid
snapshot 20260912T0000000000-95c454a78249
merkle_root 95c454a782494eecc978c0d2809be81408f4ac3153d6e70ab7ccd399b8d975fd
envelopes 2180 (native 0, backfilled 2180)
dump_hash 258f9fa373d2ac01ca12f8ca1642fd1741521f7dd75e1e5aa5a2215d0b004135
manifest .../sample/snapshots/20260912T0000000000-95c454a78249/manifest.json
INVALID_JSON scorecard/AtsushiSakai__PythonRobotics
FREEZE_ALLOWED_EXIT=1
```

(same snapshot id / root / dump hash as the base run and as the review's own
measurement -- the accepted hash is unchanged by this packet.)

```
$ rm raw/scorecard/AtsushiSakai__PythonRobotics.json
$ python -m porchlamp.snapshot reproduce 20260912T0000000000-95c454a78249 --data-dir sample
snapshot 20260912T0000000000-95c454a78249
envelopes 2180 match, 0 mismatched, 0 missing, 0 unexpected, 0 invalid, 0 stale-hash, 1 invalid-frozen
INVALID_JSON_FROZEN scorecard/AtsushiSakai__PythonRobotics
dump_hash 258f9fa373d2ac01ca12f8ca1642fd1741521f7dd75e1e5aa5a2215d0b004135
frozen_dump_hash 258f9fa373d2ac01ca12f8ca1642fd1741521f7dd75e1e5aa5a2215d0b004135
MISMATCH
REPRODUCE_FIXED_EXIT=1
```

Non-zero, names the key, `INVALID_JSON_FROZEN` -- and note the dump hash
still matches; the failure is specifically the frozen `invalid_json`, not a
coincidental rebuild difference.

### Over-correction guard

Clean freeze (envelope restored, no corruption) over the same sample:

```
$ python -m porchlamp.snapshot freeze --data-dir sample --as-of 2026-09-12T00:00:00+00:00
snapshot 20260912T0000000000-49814505ca85
merkle_root 49814505ca8516549e842c47a57528fe47cab9c30e85d2739a1c5c182ca910de
envelopes 2181 (native 0, backfilled 2181)
dump_hash 4315c2eda37fce7818ade0c86312259f41354141c5316d40632c20df1004c684
manifest .../sample/snapshots/20260912T0000000000-49814505ca85/manifest.json
GUARD_FREEZE_EXIT=0
```

(root `49814505ca85` matches the review's own §4 citation of the clean root.)

```
$ python -m porchlamp.snapshot reproduce 20260912T0000000000-49814505ca85 --data-dir sample
snapshot 20260912T0000000000-49814505ca85
envelopes 2181 match, 0 mismatched, 0 missing, 0 unexpected, 0 invalid, 0 stale-hash, 0 invalid-frozen
dump_hash 4315c2eda37fce7818ade0c86312259f41354141c5316d40632c20df1004c684
frozen_dump_hash 4315c2eda37fce7818ade0c86312259f41354141c5316d40632c20df1004c684
MATCH
GUARD_REPRODUCE_EXIT=0
```

Both accepted sample hashes (`95c454a78249` degraded, `49814505ca85` clean)
are unchanged from the review's own measurement.

### Permanent regression tests (unit-level, mirroring the P-07b stale-hash pattern)

- `tests/snapshot/test_cli.py::test_freeze_refuses_a_corrupt_envelope_without_the_flag`
- `tests/snapshot/test_cli.py::test_reproduce_of_a_degraded_snapshot_fails_after_the_corrupt_file_is_gone`
  (the exact review sequence, as a synthetic fixture)
- `tests/snapshot/test_cli.py::test_freeze_of_a_dir_with_a_corrupt_envelope_completes_and_reports`
  (existing W0-3f test, updated to pass `--allow-invalid`)
- `tests/snapshot/test_reproduce.py::test_check_envelopes_names_an_invalid_json_frozen_key_from_the_manifest`
- `tests/snapshot/test_reproduce.py::test_reproduce_fails_when_the_frozen_manifest_records_invalid_json`

## Item 2 — `PerturbationResult.reason` (Warning 2 / L1-14)

**Decision: deleted the field and the code that fills it.** Not rendered.

Reasoning: `reason` takes exactly one non-`None` value in the entire
codebase, `"UNDECIDED/TAU_B_UNDEFINED"`, assigned in exactly one place
(`sensitivity_report`) under exactly one condition (`tau is None`). Every
actual behavioral reader -- `SensitivityReport.all_pass`,
`format_sensitivity_report`'s `cell()` -- already branches on `tau_b is None`
directly and would gain nothing from also reading `reason`, because the two
conditions are logically identical by construction. Rendering the string in
the report would add a production reader but not new information: the reader
would learn "this row is UNDECIDED because tau_b is None," which the
UNDECIDED cell already says. `05-WORK-PLAN.md` section 0.4 R-6 (subtractive
rework: answer an objection by removing, not adding) and R-2 (one definition
per fact) both point the same way: a field that is a constant-valued alias
for a condition its own callers already test is the fact's second
definition, not a second fact. Deleting it is the smaller change (net
-4 lines in `porchlamp/audit/sensitivity.py`, -2 assertions in the test) and
removes the zero-reader field the review flagged instead of adding a
reader that would only restate `tau_b is None`.

A second UNDECIDED cause added to `kendall_tau_b` in the future is the
condition under which `reason` carries actual information and earns its
place back.

`tests/audit/test_sensitivity.py::test_all_tied_composite_reports_undecided_not_perfect`
updated: the two `row.reason` assertions are removed, `row.tau_b is None`
alone still fully exercises the UNDECIDED path end to end (`format_
sensitivity_report` render assertions on the same test are unchanged).

## Item 3 — constants

`porchlamp/snapshot/manifest.py`: added `REASON_STALE_HASH = "STALE_HASH"` next to
`REASON_INVALID_JSON`, and `REASON_INVALID_JSON_FROZEN = "INVALID_JSON_FROZEN"`
(new in item 1) next to it. `porchlamp/snapshot/cli.py`'s three pre-existing inline
literals (`cli.py:74`, `:94` `"INVALID_JSON"`; `:96` `"STALE_HASH"`) now
reference the constants; the two new print sites added in item 1
(`_freeze`'s refusal loop, `_reproduce`'s `invalid_json_frozen` loop) use the
constants from the start.

`tests/snapshot/test_manifest.py::test_reason_code_literals_are_spelled_only_at_their_constant_definitions`
greps all of `porchlamp/` for `\bINVALID_JSON_FROZEN\b|\bINVALID_JSON\b|\bSTALE_HASH\b`
(word-boundary: `REASON_INVALID_JSON` etc. do not match, since `_` is a word
character in regex and the three literals are internally disjoint the same
way) and asserts the exact 3-line result -- all three literals are found only
at their `REASON_*` definitions. Mirrors `tests/test_no_clock.py`'s own
grep-based style, per the packet's instruction.

## Item 4 — `EnvelopeScan` in `__all__`

`porchlamp/snapshot/__init__.py` previously re-exported 11 names; `cli.py` imports
15 across its two `porchlamp.snapshot.*` import blocks (11 unchanged plus
`EnvelopeScan`, `REASON_INVALID_JSON`, `REASON_INVALID_JSON_FROZEN`,
`REASON_STALE_HASH` added by items 1/3). `cli.py`'s `_freeze` now annotates
`scan: EnvelopeScan = envelope_entries(data_dir)`, so the CLI itself names
the return type the review said a package-root consumer could not name.
`__init__.py` now imports and exports every name `cli.py` imports from
`porchlamp.snapshot.manifest` / `porchlamp.snapshot.reproduce`
(`EnvelopeScan, PENDING_DUMP_HASH, REASON_INVALID_JSON,
REASON_INVALID_JSON_FROZEN, REASON_STALE_HASH, SNAPSHOTS_DIRNAME,
SnapshotError, code_commit, snapshot_dir`, the 9 that were missing), keeping
the 11 pre-existing exports (`build_manifest`, `canonical_dump`,
`canonical_dump_hash`, `constants_hash`, `envelope_entries`, `load_manifest`,
`merkle_root`, `rebuild`, `reproduce`, `snapshot_id`, `write_manifest`) --
addition only, nothing removed from the existing public surface.

`tests/snapshot/test_cli.py::test_every_name_cli_imports_from_the_snapshot_package_is_exported`
parses `cli.py`'s AST for `ImportFrom` nodes whose module starts with
`porchlamp.snapshot.`, collects every imported name, and asserts that set is a
subset of `porchlamp.snapshot.__all__` (anti-vacuity: asserts the scan is
non-empty first, so the test cannot pass by finding nothing).

## Item 5 — no-clock regex (Consider 4)

`tests/test_no_clock.py`: `CLOCK_RE` extended with `datetime\.today|date\.today`.

### Fail-before / after mutation proof, on a real guarded module

Method: backed up `porchlamp/engine.py` (untouched, in `_sources()`, not in
`EXCLUDED_DIRS`/`EXCLUDED_MODULES`) and `tests/test_no_clock.py` (my fixed
version) before starting; restored both from the backups afterward;
`git diff --stat 5b88e08` empty on `porchlamp/engine.py` and matched my intended
16-line diff on `tests/test_no_clock.py` after restore. `find ... __pycache__
-exec rm -rf` before every run (stale bytecode masked a mutant in an earlier
review, per the environment note).

**BEFORE** (old `CLOCK_RE`, `git show 5b88e08:tests/test_no_clock.py`
restored in place) -- appended
`def _w0_3g_clock_mutant(): return datetime.today().timestamp()` to
`porchlamp/engine.py`:

```
$ pytest tests/test_no_clock.py::test_porchlamp_scoring_modules_own_no_clock
.                                                                        [100%]
1 passed in 0.02s
MUTANT_BEFORE_FIX_EXIT=0
```

Confirmed the gap: the guard test stays green with a live wall-clock call
sitting in a scanned module.

**AFTER** (my `CLOCK_RE` restored, same mutant still in `porchlamp/engine.py`,
unchanged):

```
$ pytest tests/test_no_clock.py::test_porchlamp_scoring_modules_own_no_clock
F                                                                        [100%]
AssertionError: assert ['porchlamp/engine.py:160'] == []
1 failed in 0.03s
MUTANT_AFTER_FIX_EXIT=1
```

Mutant killed, names the injected line. `porchlamp/engine.py` restored from backup
immediately after (`git diff --stat 5b88e08 -- porchlamp/engine.py` empty).

Permanent regression:
`tests/test_no_clock.py::test_clock_pattern_sees_the_dot_today_forms` asserts
`CLOCK_RE.search(...)` on both literal forms, mirroring the existing
`test_sql_clock_pattern_sees_both_clock_forms` anti-vacuity style.

## Verification

Owned suites, `-B -p no:cacheprovider`, `__pycache__` cleared first:

```
$ pytest tests/snapshot tests/audit tests/test_no_clock.py -p no:cacheprovider
161 passed in 0.38s
EXIT=0
```

Full suite, `-n 4`:

```
$ pytest tests/ -n 4 -p no:cacheprovider
1450 passed, 12 warnings in 25.75s
EXIT=0
```

Base count independently measured in a separate detached worktree at
`5b88e08` (`git worktree add --detach`, removed after): **1443 passed**.
1443 + 7 new test functions (`git diff 5b88e08 -- tests/` : `+def test_` = 7,
`-def test_` = 0) = 1450, reconciling exactly.

Ruff, owned files:

```
$ ruff check porchlamp/snapshot/ porchlamp/audit/sensitivity.py tests/snapshot/ tests/audit/ tests/test_no_clock.py
All checks passed!
EXIT=0
```

Ruff, full instructed scope:

```
$ ruff check porchlamp/ scripts/ tests/
Found 4 errors.
EXIT=1
```

All 4 are `F401` unused imports in `scripts/curate_dataset_llm.py`, a file
this packet does not own and did not touch (`git status --short` confirms it
is absent from the diff; `ruff check` against `5b88e08:scripts/
curate_dataset_llm.py` unmodified reproduces the identical 4 errors --
pre-existing, not introduced here). `docs/v2/03-ANTI-MANIPULATION.md` section
5 already calls for this file's deletion as part of a different packet's
scope ("Fabrication path removed"); out of R-10 bounds for W0-3g.

Checked and confirmed out of scope for a doc update: no script or CI config
invokes `porchlamp.snapshot`/`porchlamp snapshot freeze` (`grep -rn "porchlamp\.snapshot\|porchlamp
snapshot" scripts/` and `*.y*ml` both empty) -- item 1's behavior change
(freeze refuses by default) has no automated caller to break.

## Diffstat

```
$ git diff --stat 5b88e08
 porchlamp/audit/sensitivity.py         | 13 +++---
 porchlamp/snapshot/__init__.py         | 25 +++++++++++-
 porchlamp/snapshot/cli.py              | 39 +++++++++++++++---
 porchlamp/snapshot/manifest.py         | 12 ++++++
 porchlamp/snapshot/reproduce.py        | 13 +++++-
 tests/audit/test_sensitivity.py  | 10 ++---
 tests/snapshot/test_cli.py       | 87 ++++++++++++++++++++++++++++++++++++++--
 tests/snapshot/test_manifest.py  | 29 ++++++++++++++
 tests/snapshot/test_reproduce.py | 45 +++++++++++++++++++++
 tests/test_no_clock.py           | 16 +++++++-
 10 files changed, 262 insertions(+), 27 deletions(-)
```

Plus this file, `.agents/W0-3g/handoff.md`. All 10 changed files are inside
the packet's owned paths (`porchlamp/snapshot/`, `porchlamp/audit/sensitivity.py`,
`tests/snapshot/`, `tests/audit/`, `tests/test_no_clock.py`); nothing else
touched.

## COULD NOT VERIFY

None. Every item's fail-before, over-correction guard and full/owned suite
runs completed with the expected exit codes above.
