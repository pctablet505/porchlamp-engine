# INT-W0-4 handoff — integrate and finish W0-4

Worktree `/home/pctablet505/Projects/cvi-wt-INT-W0-4`, branch `INT-W0-4`, base `e29fb79`.
Input: `/home/pctablet505/Projects/cvi-wt-W0-4` uncommitted work on base `32bb8b3`
(also `.../scratchpad/inflight/W0-4/tracked.diff`, 1,446 lines).

## 1. Apply and conflicts

`git log --oneline 32bb8b3..e29fb79 -- scripts/verify.py scripts/serve.py
tests/test_verify.py tests/test_serve.py cvi/verify` names one commit,
`d1103bf` (W3-3 canary). Its full diffstat (`cvi/verify/__init__.py`,
`cvi/verify/live_sample.py`, `pyproject.toml`, `scripts/canary.sh`,
`scripts/systemd/*`, `tests/verify/test_live_sample.py`) never touches any of
the four files the pathspec matched on directly — it only matched because it
created the `cvi/verify/` directory. Direct check:
`git diff 32bb8b3..e29fb79 -- scripts/verify.py scripts/serve.py
tests/test_verify.py tests/test_serve.py` is byte-empty. The W3-3 canary is a
fully separate CLI (`python -m cvi.verify.live_sample`, invoked by
`scripts/canary.sh`, never imported by `scripts/verify.py`).

`git apply --3way --index tracked.diff`: **0 conflicts**, all 7 files applied
cleanly (git reported "Falling back to direct application" for two files,
which is its normal message when a 3-way merge isn't needed because the base
blob already matches — not a conflict).

Files: delete `HANDOFF-DATASET-CURATION-AGENT.md`, delete
`scripts/curate_dataset_llm.py`, modify `docs/REVIEW_GUIDELINES.md`,
`scripts/serve.py`, `scripts/verify.py`, `tests/test_serve.py`,
`tests/test_verify.py`.

### Integration defect found and fixed (not a git-apply conflict — a semantic API break)

`tests/test_serve.py::_fixture_rows` called `store.seed_benchmark_catalog(rows)`
(one arg). Between base `32bb8b3` and `e29fb79`, commit `7945e99` (W0-2b)
changed the signature to `seed_benchmark_catalog(self, repos, now: datetime)`
(explicit reference time, D-VER-3, no ambient clock) — `git log
32bb8b3..e29fb79 -- cvi/lakehouse.py` names it. This pathspec was outside the
one the dispatch suggested (it only named `scripts/verify.py scripts/serve.py
tests/test_verify.py tests/test_serve.py cvi/verify`), so it surfaced as a
test failure, not a patch conflict:

```
TypeError: OperationalStore.seed_benchmark_catalog() missing 1 required positional argument: 'now'
```

Fix: import `BENCHMARK_NOW` from `cvi.benchmark_data` (the fixed constant
used the same way by `tests/test_adversarial_tier5_hardening.py`) and call
`store.seed_benchmark_catalog(rows, BENCHMARK_NOW)`. Fail-before/after:

```
$ pytest tests/test_serve.py -k fixture_database   # before fix
FAILED tests/test_serve.py::test_fixture_database_is_refused_naming_the_row
E       TypeError: OperationalStore.seed_benchmark_catalog() missing 1 required positional argument: 'now'
1 failed, 8 deselected in 0.18s

$ pytest tests/test_serve.py                       # after fix
9 passed, 2 warnings in 0.18s
```

## 2. Gap found against `03` §5 and fixed (within packet scope, `verify.py` is owned)

`03-ANTI-MANIPULATION.md` §5 ("Fabrication path removed") states the test
asserts no bundle has "a provenance source outside `data/raw/` or an
`http(s)://` URL with a recorded hash" — two acceptable forms. The applied
`provenance_offender` only implemented the first form; any `http(s)://`
source, hashed or not, fell through to "not an envelope under raw/" and
flagged. Added a branch: an `http(s)://` source whose entry carries a
`sha256` (or a `<url>#<hash>` suffix, via the existing `_claimed_hash`
helper) returns `None` (clean); one without a hash still flags. No real
`derive.py` field with `confidence=="verified"` uses this form today (grep
confirmed every verified field's `source` comes from the `src(collector,
key)` envelope-path helper), so this closes a specification gap without
changing today's measured flag count.

Fail-before/after (new test `test_url_provenance_with_recorded_hash_is_accepted`):
```
$ pytest tests/test_verify.py -k test_url_provenance_with_recorded_hash_is_accepted   # before fix
FAILED: assert (False is True)   [r["clean"] was False, flags=['unverifiable_provenance']]

$ pytest tests/test_verify.py -k "test_url_provenance_with_recorded_hash_is_accepted or test_url_provenance_for_verified_field_flags"   # after fix
2 passed in 0.02s
```
Over-correction guard: `test_url_provenance_for_verified_field_flags` (the
same URL WITHOUT a recorded hash still flags) is unchanged and still passes.

## 3. Diffstat (final, vs `e29fb79`)

```
 HANDOFF-DATASET-CURATION-AGENT.md | 430 --------------------------------------
 docs/REVIEW_GUIDELINES.md         |   4 +
 scripts/curate_dataset_llm.py     | 216 -------------------
 scripts/serve.py                  |  75 ++++++-
 scripts/verify.py                 | 143 ++++++++++++-
 tests/test_serve.py               | 143 +++++++++++++
 tests/test_verify.py              | 242 +++++++++++++++++++--
 7 files changed, 577 insertions(+), 676 deletions(-)
```

## 4. Fail-before proofs (packet's own acceptance items)

Restored `scripts/verify.py`/`scripts/serve.py` from `git show e29fb79:<path>`
into scratch copies, loaded both base and integrated versions by file path
(the same `importlib.util.spec_from_file_location` technique the test files
use), ran identical inputs through each. Scripts at
`.../scratchpad/INT-W0-4/fail_before_provenance.py` and
`fail_before_served_guard.py`.

**Provenance rule**, fabricated bundle with
`provenance.stars.source == "ecosystem-intelligence"` (the exact vocabulary
recorded in the deleted `HANDOFF-DATASET-CURATION-AGENT.md` line 389-390):
```
BASE  (e29fb79, pre-packet)  clean=True flags=[]
INTEGRATED (this worktree)   clean=False flags=['unverifiable_provenance']
```

**Served-DB guard**, scratch copy of `v1-baseline/cvi.db` (1,000 rows, all
`cvi/benchmark_data.py` fixtures, no snapshot manifest):
```
BASE  (e29fb79, pre-packet): no SystemExit; would have served (uvicorn.run called_with={'host': '127.0.0.1', 'port': 8999})
INTEGRATED: SystemExit message:
CVI serve refused for '.../v1-baseline-cvi.db':
  1000 of 1000 rows are cvi/benchmark_data.py fixtures: row 1 vllm-project/vllm (stars=38500, forks=5400) is fabricated telemetry, which is a test fixture and never a ranking source
  row 1 vllm-project/vllm carries no snapshot identity: no manifest.json under .../snapshots — freeze the data dir first with `python -m cvi.snapshot freeze --data-dir ... --as-of <ISO-8601>`
```

## 5. Real acceptance commands (not a harness — the actual CLI, real files)

Data copied to `/home/pctablet505/Projects/cvi-scratch/INT-W0-4/` (sample-200:
199 derived/verified bundles, 2,181 raw envelopes; a scratch copy of
`v1-baseline/cvi.db`); originals untouched.

**Synthetic DB refused** (literal acceptance command):
```
$ python scripts/serve.py --db /home/pctablet505/Projects/cvi-scratch/INT-W0-4/v1-baseline-cvi.db
CVI serve refused for '/home/pctablet505/Projects/cvi-scratch/INT-W0-4/v1-baseline-cvi.db':
  1000 of 1000 rows are cvi/benchmark_data.py fixtures: row 1 vllm-project/vllm (stars=38500, forks=5400) is fabricated telemetry, which is a test fixture and never a ranking source
  row 1 vllm-project/vllm carries no snapshot identity: no manifest.json under /home/pctablet505/Projects/cvi-scratch/INT-W0-4/snapshots — freeze the data dir first with `python -m cvi.snapshot freeze --data-dir /home/pctablet505/Projects/cvi-scratch/INT-W0-4 --as-of <ISO-8601>`
Exit code: 1
```

**Promoted sample DB serves.** Deviation found: `scripts/promote.py` does not
itself write a snapshot manifest (`grep -n "snapshot\|manifest\|freeze"
scripts/promote.py` — no match), so promote alone is correctly refused for
missing snapshot identity (the guard working as designed on a genuinely
unfrozen DB) — freeze must run between promote and serve:
```
$ python scripts/promote.py --db .../sample-200/cvi.db --data-dir .../sample-200 --as-of 2026-09-12T00:00:00Z
promoted 196 verified repos as of 2026-09-12T00:00:00+00:00 -> .../sample-200/cvi.db

$ python scripts/serve.py --db .../sample-200/cvi.db          # before freeze
CVI serve refused for '.../sample-200/cvi.db':
  row 1 0voice/interview_internal_reference carries no snapshot identity: no manifest.json under .../snapshots — freeze the data dir first with ...
Exit code: 1

$ python -m cvi.snapshot freeze --data-dir .../sample-200 --as-of 2026-09-12T00:00:00Z
snapshot 20260912T0000000000-49814505ca85
merkle_root 49814505ca8516549e842c47a57528fe47cab9c30e85d2739a1c5c182ca910de
envelopes 2181 (native 0, backfilled 2181)
dump_hash 4315c2eda37fce7818ade0c86312259f41354141c5316d40632c20df1004c684
# dump_hash matches the W0-3 lead-log entry's recorded value for this same sample (4315c2ed...) — cross-check confirms this is the known-good sample.

$ python scripts/serve.py --db .../sample-200/cvi.db --port 18234   # after freeze
CVI serve: 196 verified repos from '.../sample-200/cvi.db' (snapshot 20260912T0000000000-49814505ca85)
CVI serve: http://127.0.0.1:18234/ (API under /v1, /healthz)

$ curl -m 5 -w '\nHTTP_STATUS:%{http_code}\n' http://127.0.0.1:18234/healthz
{"status":"ok"}
HTTP_STATUS:200
```
Server was started under `timeout 30` and confirmed gone afterward (port
unreachable, no lingering `serve.py --port 18234` process).

## 6. Known hazard (2.5 h hang) — NOT reproduced

```
$ timeout 300 pytest tests/test_verify.py tests/test_serve.py -p no:cacheprovider -v
tests/test_verify.py .....................                               [ 70%]
tests/test_serve.py .........                                            [100%]
30 passed, 2 warnings in 0.21s
Exit code: 0
```
Ran individually and together, multiple times; never hung. No test id to
report. Why it cannot hang in the delivered code: `grep -n "subprocess\|
socket\.\|requests\.\|urllib\|time\.sleep\|while True\|input(" tests/
test_verify.py tests/test_serve.py scripts/verify.py scripts/serve.py` — zero
matches. Every test that would otherwise start a real server uses the
`served` fixture, which monkeypatches `serve.uvicorn.run` to a
non-blocking recorder before `serve.main()` is called, so no test in either
file ever binds a real port. `COULD NOT VERIFY: what state the "copy of this
work" that hung was actually in — it predates the fix in section 1 above (a
bare TypeError fails in milliseconds, it cannot hang for 2.5 h), so the hang
was either a different draft of the test file (one that did not yet mock
uvicorn.run) or an environmental issue (box contention) unrelated to the code
in this diff.`

## 7. Packet test set

```
$ timeout 300 pytest tests/test_verify.py tests/test_serve.py -p no:cacheprovider -v
collected 30 items
tests/test_verify.py .....................                               [ 70%]
tests/test_serve.py .........                                            [100%]
30 passed, 2 warnings in 0.21s
Exit code: 0
```

## 8. Full suite

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD pytest tests/ -n 4 -p no:cacheprovider
1422 passed, 12 warnings in 29.98s
Exit code: 0
```

## 9. Ruff

Owned files (scoped):
```
$ ruff check scripts/verify.py scripts/serve.py tests/test_verify.py tests/test_serve.py
All checks passed!
Exit code: 0
```
Repo-wide (`cvi/ scripts/ tests/`): 19 pre-existing errors in 4 files never
touched by this packet (`tests/benchmark_sla_runner.py`,
`tests/test_adversarial_comparator_m3.py`, `tests/test_perf_sla_stress.py`,
`tests/test_webapp.py`) — confirmed present on base `e29fb79` directly
(`git show e29fb79:tests/test_perf_sla_stress.py` and
`tests/test_webapp.py` re-checked in isolation reproduce the same F401/RUF002/
RUF046 findings). Out of scope (R-10); not modified.

## 10. Measure

- **Sample bundles flagged by the new `unverifiable_provenance` rule: 0 of
  199** (re-verified from `derived/`, not read from the pre-existing
  `verified/` cache, so the new rule actually ran). `verify.py --all
  --data-dir <scratch sample-200>` -> `verified: 196 clean, 3 flagged of
  199`. The 3 flags are `required:stars,required:forks` on
  `anthropics/skills`, `electron/electron`,
  `polaris1119/The-Golang-Standard-Library-by-Example` — confirmed identical
  under base `e29fb79`'s verify.py run against the same sample data (same 3
  repos, same reason string), so the new rule changed nothing in the sample's
  clean/flag split.
- **Deleted files**: `HANDOFF-DATASET-CURATION-AGENT.md` — 430 lines.
  `scripts/curate_dataset_llm.py` — 216 lines.
- **Serve guard error message (verbatim)**, real CLI, real v1-baseline
  scratch copy, exit code 1:
  ```
  CVI serve refused for '/home/pctablet505/Projects/cvi-scratch/INT-W0-4/v1-baseline-cvi.db':
    1000 of 1000 rows are cvi/benchmark_data.py fixtures: row 1 vllm-project/vllm (stars=38500, forks=5400) is fabricated telemetry, which is a test fixture and never a ranking source
    row 1 vllm-project/vllm carries no snapshot identity: no manifest.json under /home/pctablet505/Projects/cvi-scratch/INT-W0-4/snapshots — freeze the data dir first with `python -m cvi.snapshot freeze --data-dir /home/pctablet505/Projects/cvi-scratch/INT-W0-4 --as-of <ISO-8601>`
  ```
- **Hung test**: none. Section 6 above.

## 11. Deviations

1. Fixed the `seed_benchmark_catalog` arity break from W0-2b (section 1) —
   required for the delivered tests to run at all against current main; not
   a design change, a rebase fix.
2. Added the `http(s)://` + recorded-hash acceptance branch to
   `provenance_offender` (section 2) — completes `03` §5 as cited by the
   packet; the applied diff only implemented the envelope-path form.
3. The "promoted sample DB serves" acceptance needs an explicit
   `python -m cvi.snapshot freeze` between `promote.py` and `serve.py`
   (section 5) — `promote.py` does not write a manifest itself, and this is
   correct behavior for the guard (an unfrozen DB has no snapshot identity by
   the guard's own definition), not a defect in the packet's code. Recorded
   as a deviation because the dispatch's suggested command sequence omitted
   it.
4. Left the pre-existing `scripts/verify.py --promote` help string
   `"(TODO wave F)"` untouched — present in the diff's unchanged context
   lines (not introduced by this packet), out of R-10 scope for a
   file-ownership-only fix.

## 12. COULD NOT VERIFY

- `COULD NOT VERIFY: the exact prior state of "a copy of this work" that
  hung for 2.5 h` — cannot be reconstructed; the delivered diff's tests
  already mock `uvicorn.run` and contain no blocking I/O (section 6).
- `COULD NOT VERIFY: whether any packet outside W0-4's ownership (W1-3,
  W1-8, future collectors) will ever mark a list-valued-source field (for
  example pr_events, whose source is `[path, path]`) as confidence=="verified"`
  — today every such field is `"derived"`/`"missing"` (grep-confirmed across
  all of `scripts/derive.py`), so `provenance_offender`'s `isinstance(source,
  str)` check never fires on them; if a future packet ever promotes such a
  field to `"verified"` it would flag as unverifiable (fail closed, arguably
  correct per R-4's singular "envelope path", but not tested here since no
  such field exists).

---

# Round 2 (rework: Blockers B1/B2/B3, Warnings W1/W2/W3)

Continuation after a system restart killed the previous round-2 attempt
mid-task. Six files were already modified and uncommitted when this session
started (`cvi/lakehouse.py`, `scripts/derive.py`, `scripts/promote.py`,
`scripts/run_all.py`, `scripts/serve.py`, `scripts/verify.py`); that work was
kept and built on, not discarded. One load-bearing defect was found and fixed
in it (§R2.2 below) before anything else could be trusted.

## R2.1 Diffstat (working tree vs `HEAD` = `13764e1`)

```
 cvi/lakehouse.py          |  69 ++++++++++++++++++-
 docs/REVIEW_GUIDELINES.md |  11 +--
 scripts/derive.py         |  54 ++++++++++++++-
 scripts/promote.py        |  84 ++++++++++++++++++++++-
 scripts/run_all.py        |  32 +++++++--
 scripts/serve.py          | 112 +++++++++++++++---------------
 scripts/verify.py         | 170 +++++++++++++++++++++++++++++++++++++---------
 tests/test_derive.py      |  35 ++++++++++
 tests/test_promote.py     | 122 +++++++++++++++++++++++++++++++++
 tests/test_serve.py       | 146 ++++++++++++++++++++++++++++++++++-----
 tests/test_verify.py      | 156 ++++++++++++++++++++++++++++++++++++------
 11 files changed, 848 insertions(+), 143 deletions(-)
```

## R2.2 Defect found in the inherited work and fixed: self-referential dump hash

The inherited `promote.py`/`serve.py` diff called
`cvi.snapshot.reproduce.canonical_dump_hash(db_path)` directly on the whole
database file to produce the value `snapshot_meta.canonical_dump_hash`
records. `canonical_dump_hash` hashes every table in the file, and
`snapshot_meta` is a table in that same file. Measured before the fix: a
freshly promoted, never-edited database was refused --

```
$ python scripts/promote.py --db cvi.db --data-dir <sample> --as-of 2026-09-12T00:00:00Z
promoted 197 verified repos ... -> cvi.db (snapshot 20260912T0000000000-49814505ca85)   # exit 0
$ python scripts/serve.py --db cvi.db --port 18790                                       # exit 1
CVI serve refused for 'cvi.db':
  cvi.db's current content matches none of its 1 recorded snapshot_meta row(s) --
  the database was modified after promotion, or copied beside a foreign snapshot
```

because the hash `promote.py` stored was computed while `snapshot_meta` was
still empty, and the hash `serve.py` recomputed at startup included the
`snapshot_meta` row that promotion had since written -- every promoted
database refused itself. Fix: added `cvi.lakehouse.repositories_dump_hash`,
which calls the SAME `canonical_dump_hash` (no second hash implementation,
R-2) on a scratch copy of the db with every table except `repositories`
dropped, so the value is a fixed point across the write and the read.
`promote.py`, `run_all.py` and `serve.py` all now call this function instead
of `canonical_dump_hash` directly. `tests/test_promote.py::
test_write_snapshot_meta_hash_ignores_its_own_row` and
`tests/test_serve.py::test_promoted_database_with_snapshot_identity_serves`
both go red under a mutant that reverts to hashing the whole file (§R2.5,
mutation M4).

## R2.3 Named test outputs, exit codes

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest \
  tests/test_verify.py tests/test_serve.py tests/test_derive.py tests/test_promote.py \
  -p no:cacheprovider -v
tests/test_verify.py ..........................                          [ 44%]
tests/test_serve.py ............                                         [ 65%]
tests/test_derive.py .........                                           [ 81%]
tests/test_promote.py ...........                                        [100%]
58 passed, 2 warnings in 0.31s
exit=0
```

Net new tests this round: `test_verify.py` 21→26 (+5), `test_serve.py` 9→12
(+3, of which 2 are renames of behavior that no longer exists — see §R2.6),
`test_derive.py` 7→9 (+2), `test_promote.py` 3→11 (+8). 18 new tests total.

## R2.4 Ruff

```
$ .venv/bin/python -m ruff check cvi/ scripts/ tests/
All checks passed!
exit=0
```

No new `TODO`, `noqa`, or `type: ignore` in the diff (`git diff -- cvi/ scripts/
tests/ docs/ | grep -E "^\+.*(TODO|FIXME|noqa|type: *ignore)"` → exit 1, zero
matches). `print()` count in `cvi/` unchanged by this packet's files (only
`cvi/lakehouse.py` was touched there, and it added none).

## R2.5 Mutation kills (TQ-PROC-3: mutate → red → restore → verify byte-identical restore)

| # | File | Mutation | Killing test | Result |
|---|---|---|---|---|
| M1 | `verify.py` | Blocker-2 key check `if owner is None or name is None or path.stem != expected_key:` → `if False:` | `test_envelope_for_a_different_repository_flags` | `assert (True is False)` — KILLED |
| M2 | `verify.py` | Blocker-2 collector check `if collectors is not None and path.parent.name not in collectors:` → `if False:` | `test_envelope_from_wrong_collector_flags` | `assert (True is False)` — KILLED |
| M3 | `verify.py` | Blocker-1 missing-hash check `if claimed is None:` → `if False:` | `test_provenance_without_recorded_hash_flags` | fails on the "no sha256" detail assertion — KILLED |
| M4 | `lakehouse.py` | `repositories_dump_hash`'s `if table in _DUMP_HASH_EXCLUDED_TABLES:` → `if False:` (reintroduces §R2.2) | `test_write_snapshot_meta_hash_ignores_its_own_row` + `test_promoted_database_with_snapshot_identity_serves` | hash changes after the write; promoted DB refuses itself again — KILLED (both) |
| M5 | `serve.py` | `matching_snapshot_id`'s `if stored_hash == current:` → `if True:` | `test_renaming_every_owner_after_promotion_is_refused` + `test_nudging_telemetry_after_promotion_is_refused` | `assert 0 == 1` (zero refusal reasons) — KILLED (both) |

Each mutation applied with `sed -i`, run in isolation, then restored with
`diff -q <file> <backup>` confirming byte-identical restore (not `git diff
--quiet`, which is meaningless mid-round against an already-uncommitted
tree) and `__pycache__` cleared on both sides of every run. 5/5 killed, 0
survivors.

## R2.6 Semantics audit against the review's own test table (all re-executed, not read from a prior claim)

| Case (review §3/§4) | Expected | Result |
|---|---|---|
| Fail-before: one-byte tamper in a real envelope (`stars 32721 → 32722` in a copied `numpy__numpy.json`) | FLAG | `numpy/numpy: FLAG unverifiable_provenance` (exit 0, 1 flagged of 1); restored → `CLEAN` (exit 0, 1 clean of 1) |
| Over-correction guard: an untampered re-derived bundle | CLEAN | 197 of 200 clean (the 3 flags are the pre-existing `required:stars/forks` cases, unrelated to provenance — see §R2.7) |
| `numpy/numpy`'s stars pointed at `AtsushiSakai/PythonRobotics`'s `github_meta` envelope | FLAG | `clean=False flags=['unverifiable_provenance']`, detail `... does not match this bundle's own 'numpy__numpy'` |
| `numpy/numpy`'s stars pointed at `numpy/numpy`'s own `gfi` envelope | FLAG | `clean=False flags=['unverifiable_provenance']`, detail `... collector='gfi' is not one of ['github_meta'] ...` |
| List-valued source (`mean_inline_comments`-shaped, 2 envelope paths), both valid | CLEAN | `clean=True flags=[]` |
| Same, second element replaced with a foreign repo's envelope | FLAG | `clean=False flags=['unverifiable_provenance']`, detail names the field |
| `http(s)://` URL, no recorded hash | FLAG | unchanged from round 1 |
| `http(s)://` URL WITH a recorded (unverified) hash | round 1: CLEAN (Warning-1). Round 2 (W1 ruling): FLAG | `clean=False flags=['unverifiable_provenance']` — the branch is deleted, not merely tightened |
| Downloads field keyed on `<ecosystem>__<name>` | CLEAN | `clean=True flags=[]` |
| Same field keyed on `<owner>__<name>` (the bug Blocker-2 would reintroduce for this one field) | FLAG | `clean=False flags=['unverifiable_provenance']` |

## R2.7 Re-derived sample: flag rate under the new hash rule

Every bundle in the read-only sample at `~/Projects/cvi-scratch/sample-200`
was derived before this packet existed, so none of its provenance carries a
`sha256` — under the new rule (B1: "missing recorded hash → FLAG"), it is
NOT clean until re-derived. This is stated plainly, not glossed over: the
sample was copied to `~/Projects/cvi-scratch/INT-W0-4-r2/` and every one of
its 200 bundles was re-derived with this round's `scripts/derive.py`
(offline, from the cached `raw/` envelopes already in the sample, no live
GitHub calls) before verifying.

```
$ python scripts/verify.py --all --data-dir ~/Projects/cvi-scratch/INT-W0-4-r2
...
verified: 197 clean, 3 flagged of 200
exit=0
```

`grep -c unverifiable_provenance` over the run's output → **0**. The 3 flags
are `anthropics/skills`, `electron/electron`,
`polaris1119/The-Golang-Standard-Library-by-Example`, all
`required:stars,required:forks` — the same pre-existing, provenance-unrelated
flags the round-1 review recorded (§2 of this handoff, and the review's §2
"3 pre-existing flags unchanged"). Provenance flag rate on the re-derived
sample: **0 of 200 (0%)**.

## R2.8 The five serve-guard outputs (verbatim, re-derived sample + a fresh v1 copy)

1. **Promoted-then-served DB is ACCEPTED**:
   ```
   CVI serve: 197 verified repos from '.../cvi.db' (snapshot 20260912T0000000000-49814505ca85)
   GET /healthz -> {"status":"ok"}
   ```
2. **`UPDATE repositories SET owner = owner || '-inc'` (rename every owner) is REFUSED**, exit 1:
   ```
   CVI serve refused for '.../renamed.db':
     .../renamed.db's current content matches none of its 1 recorded snapshot_meta row(s) --
     the database was modified after promotion, or copied beside a foreign snapshot
   ```
3. **`UPDATE repositories SET stars = stars + 1` (nudge every counter) is REFUSED**, exit 1: same
   "matches none of its 1 recorded snapshot_meta row(s)" message.
4. **Synthetic `v1-baseline/cvi.db` (1,000 fixture rows, never promoted) is REFUSED**, exit 1:
   ```
   CVI serve refused for '.../v1_fresh.db':
     .../v1_fresh.db has no snapshot_meta row: promote against a frozen snapshot first
     (`python -m cvi.snapshot freeze --data-dir <dir> --as-of <ISO-8601>` then
     `scripts/promote.py --snapshot-id <id> --db <db> --data-dir <dir>`)
   ```
5. **A real, valid `snapshots/` manifest copied beside a foreign (never-promoted) empty DB is REFUSED**,
   exit 1: same "has no snapshot_meta row" message — identity now lives inside the db's own
   `snapshot_meta` table, never a beside-the-file manifest, so copying one buys nothing
   (closes Warning-3).

## R2.9 Full suite

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest tests/ -n 4 -p no:cacheprovider
1479 passed, 12 warnings in 36.58s
exit=0
```

(Not comparable digit-for-digit to round 1's "1422 passed" claim: other
packets landed commits on `main`/other branches between round 1 and this
session, and `-n 4` was used per this packet's own dispatch instructions,
not `-n auto`; the failure-set is what matters and it is empty both before
and after every change in this round — verified per-file above and via the
mutation restores in §R2.5.)

## R2.10 What changed, file by file

- **`cvi/lakehouse.py`** (Blocker-3): `snapshot_meta` table (already added
  before this session) plus the new `repositories_dump_hash` function
  (§R2.2) that both `promote.py` and `serve.py` now import and call instead
  of `cvi.snapshot.reproduce.canonical_dump_hash` directly.
- **`scripts/derive.py`** (Blocker-1): `_envelope_sha256` helper and
  `Bundle.set`'s recording of `entry["sha256"]` (scalar or list-valued,
  memoized per source path) were already correct in the inherited diff —
  verified they use `Envelope.sha256` (native-or-backfilled, the same
  property `cvi.snapshot.manifest`'s freeze reads), not a second hash
  definition. Confirmed the touched region is lines 26 and 83-150 only
  (`git diff scripts/derive.py | grep '^@@'`); the hook chain below line 150
  (owned by other packets) is untouched.
- **`scripts/verify.py`** (Blocker-1, Blocker-2, W1): `FIELD_COLLECTORS`,
  `ECOSYSTEM_KEYED_FIELDS`, `_expected_key`, `_source_items`, and the
  rewritten `_path_offender`/`provenance_offender`/`provenance_checks` were
  already correct in the inherited diff. Verified: the `http(s)+hash`
  acceptance branch is fully gone (no `startswith(("http://", "https://"))`
  anywhere in the file); the key/collector binding reads the bundle's own
  `meta.owner/name/ecosystem`, not a new parameter threaded through every
  caller.
- **`scripts/promote.py`** (Blocker-3, Warning-2): `resolve_manifest`,
  `write_snapshot_meta`, `--snapshot-id` were already correct in the
  inherited diff. Fixed: `write_snapshot_meta`'s hash argument now comes
  from `cvi.lakehouse.repositories_dump_hash`, not `canonical_dump_hash`
  directly (§R2.2); removed the now-unused `cvi.snapshot.reproduce` import.
- **`scripts/run_all.py`** (Blocker-3, Warning-2): the `freeze` step between
  `verify` and `promote` was already correct in the inherited diff (pipeline
  order gather → derive → verify → freeze → promote). Fixed the same
  `canonical_dump_hash` → `repositories_dump_hash` call.
- **`scripts/serve.py`** (Blocker-3, Warning-3): the fixture-owner-name
  heuristic is deleted (not merely demoted); `matching_snapshot_id` /
  `refusal_reasons` were already correct in the inherited diff except for
  the direct `canonical_dump_hash` call, fixed the same way.
- **`docs/REVIEW_GUIDELINES.md`**: the §3 provenance sentence reworded to
  name the collector/key binding, the deleted URL branch, and the
  content-hash-bound serve guard, without touching
  `docs/v2/00-README.md` (already updated by the lead on `main`, per the
  task's own instruction not to re-word it from here).
- **`README.md`**: no `gather → derive → verify → promote → serve` quickstart
  command sequence exists anywhere in this file today (checked this branch
  and current `main` — neither has one); there is nothing to insert the
  freeze step into. Recorded here rather than inventing a new Quickstart
  section, which would be scope beyond B1/B2/B3/W1/W2/W3.

## R2.11 Ownership expansion (for the lead to ratify)

Files this packet (`INT-W0-4`) now writes to that were not all originally
listed as owned in round 1: `cvi/lakehouse.py` (new `snapshot_meta` table +
`repositories_dump_hash`), `scripts/run_all.py` (freeze step). `promote.py`
and `serve.py` were already owned. No file outside
`cvi/lakehouse.py, scripts/{derive,verify,promote,run_all,serve}.py,
docs/REVIEW_GUIDELINES.md, tests/{test_verify,test_serve,test_derive,
test_promote}.py, .agents/INT-W0-4/handoff.md` was touched
(`git diff --name-status` against `HEAD`).

## R2.12 COULD NOT VERIFY

- `COULD NOT VERIFY: an automated test for scripts/run_all.py's freeze-then-
  promote wiring in isolation` — no `tests/test_run_all.py` exists (a
  pre-existing gap, not introduced this round) and `run_all.py`'s sweep
  function performs real network I/O via `gather.gather_repo` for repos not
  already cached, so unit-testing `main()` end-to-end needs a larger
  network-mocking harness than this round's scope. The freeze/promote glue
  itself is four calls into already-independently-tested functions
  (`cvi.snapshot.cli.main`, `promote.resolve_manifest`, `promote.promote`,
  `promote.write_snapshot_meta`, `cvi.lakehouse.repositories_dump_hash`),
  and the exact same sequence was run live end-to-end against the re-derived
  200-repo sample in §R2.7/§R2.8 with the outputs shown there.
- `COULD NOT VERIFY: whether any packet outside W0-4's ownership will ever
  mark a list-valued-source field (for example `pr_events`) as
  `confidence=="verified"`` — carried over from round 1; still true today
  (every such field in `scripts/derive.py` is `"derived"`/`"missing"`), and
  now directly tested at the `verify.py` level regardless
  (`test_list_valued_provenance_checked_elementwise`), so the gap that
  remains is only "no real bundle exercises this path today," not "the code
  path is untested."
- `COULD NOT VERIFY: the exact prior state of "a copy of this work" that
  hung for 2.5 h` (round 1's item, unchanged this round — still not
  reproduced, still no blocking I/O in the delivered tests).
