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
