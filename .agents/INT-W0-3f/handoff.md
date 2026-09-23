# INT-W0-3f — integrate the completed W0-3f fix onto main

- Worktree: `/home/pctablet505/Projects/cvi-wt-INT-W0-3f`
- Branch: `INT-W0-3f`, base `e29fb79` (main)
- Patch source: commit `18102f7` on branch `W0-3f` in the read-only tree
  `/tmp/cvi-wt-W0-3f` (base `6651386`), applied via `git am -3` as `4f9757c`.
- Interpreter: `/home/pctablet505/Projects/contributor-value-index/.venv/bin/python`
  (repo venv, Python 3.12). `PYTHONPATH=$PWD` verified to resolve `porchlamp.__file__`
  inside this worktree before every command below.

## Step 1 — git am -3

Patch file integrity check before applying: regenerated the patch from the
read-only fix tree (`git format-patch -1 18102f7 --stdout`) and diffed it
against the saved copy at
`/tmp/claude-1000/-home-pctablet505-Projects/2a48e84a-2b94-4a2f-8bff-adbe955a4084/scratchpad/inflight/W0-3f/0001-*.patch`
— byte-identical (`diff` exit 0). Used the regenerated copy for `git am -3`.

```
$ git am -3 <patch>
Applying: fix(snapshot,audit): W0-3f wave-gate findings P-09/P-07b/P-10c/P-01
```

**No conflicts.** Applied cleanly as `4f9757c`. Confirmed by diffstat identity
between the source commit and the applied commit:

```
 porchlamp/audit/sensitivity.py         |  37 ++++++++---
 porchlamp/snapshot/cli.py              |  21 ++++--
 porchlamp/snapshot/manifest.py         |  60 ++++++++++++++++-
 porchlamp/snapshot/reproduce.py        |  49 +++++++++++---
 tests/audit/test_sensitivity.py  |  56 ++++++++++++++++
 tests/snapshot/test_cli.py       |  87 ++++++++++++++++++++++++
 tests/snapshot/test_manifest.py  |  95 ++++++++++++++++++++++----
 tests/snapshot/test_reproduce.py | 139 +++++++++++++++++++++++++++++++++++++--
 tests/test_no_clock.py           |  34 ++++++++++
 9 files changed, 532 insertions(+), 46 deletions(-)
```

Reason no conflicts occurred: neither `d1103bf` (W3-3 canary) nor `e29fb79`
(docs) touched any of the 9 files the patch owns (`git diff 6651386 e29fb79
--stat -- <9 paths>` is empty), so the patch's base tree for these files was
identical to `e29fb79`'s.

## Step 2 — mutation proofs (fail-before / pass-after)

Method: restore the pre-fix blob(s) from `git show 6651386:<path>` in place,
run the guarding test (must fail), restore the post-patch blob(s) from
`git show 4f9757c:<path>` (or, for P-01, restore the untouched file from a
byte-for-byte backup). `git status`/`git diff --stat` confirmed empty (worktree
byte-identical to `4f9757c`) after every restore.

### P-09 (Blocker) — corrupt-JSON envelope crash

Files reverted together (they are interdependent — `cli.py` and `reproduce.py`
both call `manifest.envelope_entries`): `porchlamp/snapshot/manifest.py`,
`porchlamp/snapshot/reproduce.py`, `porchlamp/snapshot/cli.py`.

Fail-before (`pytest ... -p no:cacheprovider`), **exit 1**:

```
FFF                                                                      [100%]
=================================== FAILURES ===================================
___________ test_reproduce_survives_a_corrupt_envelope_and_names_it ____________
>       manifest = _freeze(data_dir, envelope_entries(data_dir).entries)
E       AttributeError: 'list' object has no attribute 'entries'
_________ test_reproduce_of_a_corrupt_envelope_exits_one_and_names_it __________
    porchlamp/snapshot/manifest.py:63: in envelope_entries
        envelope = read_envelope(path)
    porchlamp/collect/envelope.py:160: in read_envelope
        document = json.loads(Path(path).read_text(encoding="utf-8"))
E   json.decoder.JSONDecodeError: Expecting ',' delimiter: line 1 column 267 (char 266)
______ test_freeze_of_a_dir_with_a_corrupt_envelope_completes_and_reports ______
    porchlamp/snapshot/cli.py:45: in _freeze
        entries = envelope_entries(data_dir)
    porchlamp/snapshot/manifest.py:63: in envelope_entries
        envelope = read_envelope(path)
E   json.decoder.JSONDecodeError: Expecting ',' delimiter: line 1 column 111 (char 110)
3 failed in 0.09s
```

The two `JSONDecodeError` failures reproduce the gate's exact traceback shape
(uncaught exception from the unguarded `read_envelope` call inside
`envelope_entries`, at `manifest.py:63` via `reproduce.py:192` / `cli.py:45`).

Pass-after (files restored to `4f9757c`), **exit 0**:
```
......                                                                   [100%]
6 passed in 0.08s
```
(this run also includes the 3 P-07b tests below, run together — see next
section)

### P-07b (Minor) — stale native hash reported as MATCH

Same file group as P-09 (`reproduce.py`'s `check_envelopes`/`EnvelopeCheck`
and `cli.py`'s printing are the same functions the P-09 fix touched).

Fail-before, **exit 1**:
```
FFF                                                                      [100%]
___ test_check_envelopes_names_a_stale_native_hash_from_the_frozen_manifest ___
>       manifest = _freeze(data_dir, envelope_entries(data_dir).entries)
E       AttributeError: 'list' object has no attribute 'entries'
______ test_reproduce_fails_when_the_frozen_manifest_records_a_stale_hash ______
>       manifest = _freeze(data_dir, envelope_entries(data_dir).entries)
E       AttributeError: 'list' object has no attribute 'entries'
______ test_reproduce_of_a_stale_native_hash_exits_one_and_names_the_key _______
    code = main(["reproduce", snapshot_id, "--data-dir", str(root)])
    printed = capsys.readouterr().out
>   assert code == 1
E   assert 0 == 1
3 failed in 0.07s
```

Two of the three failures are `AttributeError` from the test-helper `_freeze`
using the new `.entries` accessor (collateral of reverting `manifest.py`
alongside `reproduce.py`/`cli.py` in the same interdependent group — noted as
a deviation below). The third,
`test_reproduce_of_a_stale_native_hash_exits_one_and_names_the_key`, is the
uncontaminated proof: it drives the CLI end-to-end (`main([...])`, no direct
call to `envelope_entries`) and fails on the exact real-behavior assertion —
`assert 0 == 1` — meaning `reproduce` returned exit 0 (MATCH) while the frozen
manifest recorded a stale native hash. This is byte-for-byte the bug the gate
described ("reproduce prints MATCH / exit 0").

Pass-after (files restored to `4f9757c`), run together with the 3 P-09 tests,
**exit 0**:
```
......                                                                   [100%]
6 passed in 0.08s
```

### P-10c (Minor) — Kendall tau-b returns 1.0 on a degenerate all-tied case

File reverted: `porchlamp/audit/sensitivity.py` only (independent of the snapshot
group).

Fail-before, **exit 1**:
```
FF                                                                       [100%]
_____________ test_kendall_tau_b_all_tied_on_one_side_is_undefined _____________
>       assert kendall_tau_b([75.0, 75.0, 75.0, 75.0],
                             [70.0, 76.67, 76.67, 70.0]) is None
E       assert 1.0 is None
____________ test_all_tied_composite_reports_undecided_not_perfect _____________
>               assert row.tau_b is None, (row.pillar, row.delta_pp)
E               AssertionError: ('responsiveness', 5)
E               assert 1.0 is None
E                +  where 1.0 = PerturbationResult(pillar='responsiveness', delta_pp=5, tau_b=1.0).tau_b
2 failed in 0.03s
```

This is exactly the gate's finding: the pre-fix code reports 1.0 ("perfect
agreement") for a ranking that provably changed.

Pass-after (file restored to `4f9757c`), **exit 0**:
```
...                                                                      [100%]
3 passed in 0.01s
```

### P-01 (Minor) — unguarded `porchlamp/ingest.py` no-clock exemption

**Deviation from the literal step:** no `porchlamp/` source file changed for this
finding — `git diff 6651386 4f9757c -- porchlamp/ingest.py` is empty; the fix is
`tests/test_no_clock.py::test_ingest_exemption_covers_only_the_monotonic_defaults`
only. There is no pre-fix blob of a production file to restore. Substituted
an equivalent mutation: appended the exact regression class the finding
names (an unguarded `datetime.now()` call) to the end of `porchlamp/ingest.py`,
kept a byte-for-byte backup first, ran the guarding test, then restored the
file from the backup and confirmed `diff` reported no difference.

Fail-before (mutated `ingest.py`), **exit 1**:
```
F                                                                        [100%]
___________ test_ingest_exemption_covers_only_the_monotonic_defaults ___________
    source = (PORCHLAMP_PKG / "ingest.py").read_text(encoding="utf-8")
    wall_clock_lines = [line for line in source.splitlines() if CLOCK_RE.search(line)]
>   assert wall_clock_lines == []
E   AssertionError: assert ['    return ....timestamp()'] == []
E     Left contains one more item: '    return datetime.now().timestamp()'
1 failed in 0.02s
```

Pass-after (file restored, `diff` against backup empty), full
`tests/test_no_clock.py`, **exit 0**:
```
..........                                                               [100%]
10 passed in 0.02s
```

**Observation (measured, not a hedge):** the same mutation was also run
against the rest of `tests/test_no_clock.py` with the new test deselected
(`-k "not test_ingest_exemption..."`), to check the gate's literal claim that
"a `datetime.now()` added to `porchlamp/ingest.py` today would pass the whole
no-clock suite":
```
E       AssertionError: assert {'porchlamp/benchma...vi/ingest.py'} == {'porchlamp/benchma...mark_data.py'}
1 failed, 8 passed, 1 deselected in 0.03s
```
`test_exempt_modules_actually_own_a_clock` also fails on this mutation,
because it scans the same `EXCLUDED_MODULES` (including `ingest.py`) with the
same `CLOCK_RE` and asserts the resulting file-set by exact equality — so a
literal `datetime.now()` in `ingest.py` was already visible to that
pre-existing test, contrary to the gate's specific wording. This does not
change the verdict (P-01 stands as Minor guard-scope hardening; the real
blind spot is a wall-clock idiom that does not match `CLOCK_RE` at all, e.g.
something other than `datetime.now|utcnow|time.time|BENCHMARK_NOW`, which
neither test would catch). The new test's independent value is pinning
`ingest.py`'s clock surface in isolation (exact line count and content of the
two `time.monotonic` defaults) rather than relying on a set-equality
assertion shared with two unrelated fixture-clock modules.

## Step 3 — P-09 reproduction on a copy of the sample

Scratch copy: `/home/pctablet505/Projects/cvi-scratch/INT-W0-3f/sample`
(`cp -r` of `~/Projects/cvi-scratch/sample-200`, 2,181 raw envelopes, 60 MB;
`sample-200` itself never written to). The copy inherited `sample-200`'s
`r--r--r--` file mode; `chmod -R u+w` applied only to this packet's own
scratch copy so the corruption step could write.

Freeze (clean copy), **exit 0**, hashes match the accepted W0-3 values:
```
snapshot 20260912T0000000000-49814505ca85
merkle_root 49814505ca8516549e842c47a57528fe47cab9c30e85d2739a1c5c182ca910de
envelopes 2181 (native 0, backfilled 2181)
dump_hash 4315c2eda37fce7818ade0c86312259f41354141c5316d40632c20df1004c684
manifest .../sample/snapshots/20260912T0000000000-49814505ca85/manifest.json
```

Corrupted one byte of `raw/scorecard/AtsushiSakai__PythonRobotics.json`
(index 466 of 469, `}` -> `x`, same method as the gate's P-09 repro);
confirmed `json.load` now raises `JSONDecodeError`.

`reproduce`, **exit 1**:
```
$ python -m porchlamp.snapshot reproduce 20260912T0000000000-49814505ca85 --data-dir <copy>
snapshot 20260912T0000000000-49814505ca85
envelopes 2180 match, 0 mismatched, 0 missing, 0 unexpected, 1 invalid, 0 stale-hash
INVALID_JSON scorecard/AtsushiSakai__PythonRobotics
dump_hash 258f9fa373d2ac01ca12f8ca1642fd1741521f7dd75e1e5aa5a2215d0b004135
frozen_dump_hash 4315c2eda37fce7818ade0c86312259f41354141c5316d40632c20df1004c684
MISMATCH
```

Meets every requirement: exit code 1 (non-zero); names the collector/key
(`scorecard/AtsushiSakai__PythonRobotics`) with the `INVALID_JSON` reason; no
traceback; still reports on every other envelope (2180 match + 1 invalid =
2181, the full frozen count — nothing silently dropped).

Restored the byte (`x` -> `}`) and re-ran `reproduce`: **exit 0**, `MATCH`,
`dump_hash` == `frozen_dump_hash` == `4315c2eda37fce7818ade0c86312259f41354141c5316d40632c20df1004c684`
— over-correction confirmed on the live sample, not just the unit fixtures.

## Step 4 — test runs

### Packet test set: tests/snapshot tests/audit tests/core

```
env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../python -m pytest tests/snapshot tests/audit tests/core -p no:cacheprovider
........................................................................ [ 26%]
........................................................................ [ 53%]
........................................................................ [ 80%]
......................................................                   [100%]
270 passed in 0.29s
```
**Exit 0.**

### Full suite

```
env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .../python -m pytest tests/ -n 4 -p no:cacheprovider
[... 1421 passed ...]
1421 passed, 12 warnings in 29.57s
```
**Exit 0.** 12 warnings are pre-existing library deprecation notices
(`starlette.testclient`/`httpx`, `anyio.abc.BlockingPortal`, a `fork()`
multi-threading notice in an adversarial stress test) — none touch this
packet's files.

### Ruff

Packet-owned files only (the 9 files the patch touched), **exit 0**:
```
$ ruff check porchlamp/audit/sensitivity.py porchlamp/snapshot/cli.py porchlamp/snapshot/manifest.py porchlamp/snapshot/reproduce.py \
    tests/audit/test_sensitivity.py tests/snapshot/test_cli.py tests/snapshot/test_manifest.py \
    tests/snapshot/test_reproduce.py tests/test_no_clock.py
All checks passed!
```

Full command per the packet header (`ruff check porchlamp/ scripts/ tests/`),
**exit 1** — 23 pre-existing errors in 5 files this packet does not own and
never touched (confirmed by the Step-1 diffstat): `scripts/curate_dataset_llm.py`
(4x F401), `tests/benchmark_sla_runner.py` (6x F401/RUF046/B023),
`tests/test_adversarial_comparator_m3.py` (RUF001/SIM114/RUF021/2x F541),
`tests/test_perf_sla_stress.py` (2x F401/RUF046), `tests/test_webapp.py`
(RUF002). None of these files are listed in the `git am` diffstat or in `git diff
6651386 e29fb79` for the 9 owned paths — pre-existing on main, out of this
packet's ownership (R-10), recorded and not touched.

## Measure

- **Tests added by the patch: 17** (`git show 4f9757c | grep -c '^\+def test_'`
  = 17; distribution: 4 in `test_manifest.py`, 6 in `test_reproduce.py`, 3 in
  `test_cli.py`, 3 in `test_sensitivity.py`, 1 in `test_no_clock.py`).
- **P-09 reproduction: exit code 1**, named key
  `scorecard/AtsushiSakai__PythonRobotics` with reason `INVALID_JSON`.
- **Full-suite: 1421 passed, exit 0.**

## Deviations

1. `git am -3` applied with **zero conflicts** (step 1 anticipated conflicts
   and asked to record them if any occurred — none did; verified the
   diffstat of the applied commit is identical to the source commit's).
2. P-09 and P-07b mutation proofs necessarily reverted the same three-file
   group (`manifest.py`, `reproduce.py`, `cli.py`) together rather than one
   file per finding, because the two fixes share the same functions
   (`check_envelopes`, `EnvelopeCheck`) in the same commit; reverting only
   one file at a time produces cross-version `AttributeError`s that are not
   the finding's actual bug. Recorded per-test which failures are the direct
   proof (`JSONDecodeError` traceback for P-09,
   `assert 0 == 1` for P-07b) versus which are collateral `AttributeError`s
   from the test-helper API mismatch.
3. P-01 has no production-code "pre-fix version" to restore (the fix is
   test-only); substituted an equivalent injected mutation into
   `porchlamp/ingest.py`, restored byte-for-byte afterward (`diff` confirmed
   empty). See the Observation under P-01 above: the specific literal
   mutation used is also caught by a pre-existing, unrelated test
   (`test_exempt_modules_actually_own_a_clock`), which narrows exactly what
   residual gap the new test closes (a `CLOCK_RE`-invisible wall-clock idiom
   in `ingest.py`, not a literal `datetime.now()`).
4. The scratch sample copy at
   `/home/pctablet505/Projects/cvi-scratch/INT-W0-3f/sample` needed
   `chmod -R u+w` after copying (source `sample-200` files are `r--r--r--`).
   Applied only to this packet's own scratch copy; `sample-200` itself was
   never opened for writing.
5. Ruff on the full `porchlamp/ scripts/ tests/` tree exits 1 on 23 pre-existing,
   unowned errors (listed above under Step 4); ruff on the 9 files this
   packet owns exits 0.

## COULD NOT VERIFY

- None. Every step in the packet (git am, four mutation proofs, the P-09
  sample reproduction, the four test/lint runs) completed and produced a
  measured result.
