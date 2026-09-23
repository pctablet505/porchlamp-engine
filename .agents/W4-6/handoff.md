# W4-6 handoff — `porchlamp.snapshot store`: score a snapshot directory into a v2 store

Base (final rebase): `4005d7d` (main tip). Two commits on branch `W4-6`
(worktree `~/Projects/cvi-wt-W4-6`): the original delivery, and a
follow-up implementing the lead's 2026-09-14 detector-pin ruling (see
that section below). Diffstat, final base → final HEAD:

```
 .agents/W4-6/handoff.md                  | 334 +++++++++++++++++++++++
 porchlamp/snapshot/__init__.py                 |  12 +
 porchlamp/snapshot/cli.py                      |  88 +++++-
 porchlamp/snapshot/fixture_io.py               |  91 +++++++
 porchlamp/snapshot/store_cmd.py                | 251 +++++++++++++++++
 scripts/build_fixture_snapshot.py        |  77 ++----
 tests/fixtures/snapshot_50/expected.json |   2 +-
 tests/snapshot/test_reproduce_fixture.py |  29 +-
 tests/snapshot/test_store_cli.py         | 447 +++++++++++++++++++++++++++++++
 9 files changed, 1266 insertions(+), 65 deletions(-)
```

(Exact commit shas are in the caller's own report of this handoff, since
amending this file to embed its own commit's sha is circular; `git log
--oneline -3` on branch `W4-6` names them.)

## What was delivered

`python -m porchlamp.snapshot store <snapshot_dir> --db <path> [--previous <dir>] [--no-verify] [--code-commit <sha>]`:

1. Loads `<snapshot_dir>` (`bundles/*.json`, `populations.json`,
   `manifest.json`, optional `expected.json`) via the shared loader moved
   into `porchlamp/snapshot/fixture_io.py` (`FrozenFixture`/`load_frozen_fixture`
   — cut out of `scripts/build_fixture_snapshot.py` verbatim, R-2; that
   script now imports and re-exports them so its own tests, which load it
   by path via `importlib`, keep working unchanged — verified directly).
2. Scores it for real: `porchlamp.core.pipeline.score_snapshot` with detectors
   wired via `porchlamp.verify.pipeline_hook.build_detectors(frozen.bundles)`.
   `--previous <dir>` is loaded and scored the same way (no detectors
   composed for it — only its records' `params` feed `detect_param_jump`)
   and passed as `score_snapshot`'s `previous=`.
3. `expected.json`, if present, is checked against the freshly computed
   `records_hash` (`porchlamp.snapshot.store_cmd.verify_expected`); a mismatch
   exits 1 with a clear message unless `--no-verify`.
4. Opens/creates the DB (`porchlamp.store.schema.connect` + `porchlamp.store.
   migrations.apply_migrations`), writes `write_snapshot` + `write_records`
   in ONE transaction this command opens itself (`porchlamp.snapshot.store_cmd.
   write_scored_snapshot`), then calls `porchlamp.store.writer.
   finalize_snapshot_hash` (necessarily a second, later transaction — that
   function's own docstring: it reads `db_path` fresh off disk, which
   cannot see this connection's own uncommitted writes) and prints the
   result.
5. The writer's immutability `ValueError` (a snapshot id already has rows
   in `--db`) is caught and printed as a one-line message, exit 1, the DB
   file byte-for-byte unchanged (verified: sha256 before/after).

No wall clock anywhere in this command: `as_of`/`created_at`/`promoted_at`
are all `frozen.as_of` (from the snapshot directory's own `manifest.json`);
`code_commit` defaults to `git rev-parse HEAD` (same helper `porchlamp snapshot
freeze` already uses), never a timestamp.

New/moved files:

- `porchlamp/snapshot/fixture_io.py` (new): `FrozenFixture`/`load_frozen_fixture`,
  moved out of `scripts/build_fixture_snapshot.py`. `expected.json` is now
  OPTIONAL (`None` when absent) — a real, non-fixture snapshot directory
  has no pinned expectation of its own; every existing caller
  (`tests/snapshot/test_reproduce_fixture.py`, `tests/audit/
  test_anchors_fixture.py`, `tests/audit/test_sensitivity_fixture.py`) only
  ever points this at `tests/fixtures/snapshot_50`, which always ships
  one, so their behaviour is unchanged (full suite green, checked).
- `porchlamp/snapshot/store_cmd.py` (new): `score_snapshot_dir`, `verify_expected`,
  `manifest_root_of`, `write_scored_snapshot`, `VerificationError`.
- `porchlamp/snapshot/cli.py`: `store` subcommand + `_store` handler.
- `porchlamp/snapshot/__init__.py`: the four new names added to `__all__` (an
  existing test, `test_every_name_cli_imports_from_the_snapshot_package_is_exported`,
  enforces that every name `cli.py` imports from `porchlamp.snapshot.*` is
  re-exported at the package root — caught this the first time, fixed).
- `scripts/build_fixture_snapshot.py`: `FrozenFixture`/`load_frozen_fixture`
  removed, re-imported from `porchlamp.snapshot.fixture_io` (one `# noqa: F401`,
  since they're kept only for backward-compatible re-export).

`manifest_root` (the `snapshots.manifest_root` column): this directory
shape carries no envelope-level freeze manifest of its own (`03` section 5's
Merkle-root-over-envelope-hashes concept belongs to `porchlamp snapshot freeze`,
a different artifact). `write_scored_snapshot` computes it as
`porchlamp.snapshot.manifest.merkle_root(sorted(frozen.raw_hashes.values()))` —
reusing that function rather than a second Merkle implementation (R-2) —
the Merkle root of every bundle's own `raw_hash` in the snapshot, the
closest real analogue this directory shape has to "which inputs this
snapshot covers".

## Follow-up commit (2026-09-14): the detector-pin ruling, resolved

The first commit (`b55a3f4`) found and reported that `tests/fixtures/
snapshot_50/expected.json` was pinned with `detectors=None`, so this
command's records_hash (detectors always wired) genuinely disagreed with
it. Lead ruling: **"the reproduce pin must reflect the real pipeline,
detectors included (with `previous=None` the single-snapshot detectors
fire; `closed_unread` on 12/50 is real evidence, not noise)."** This
follow-up commit implements that ruling in full:

1. **Shared code path**: `porchlamp/snapshot/store_cmd.py` gained
   `score_snapshot_with_detectors(bundles, populations, *, snapshot_id,
   as_of, gate_evidence, raw_hashes, previous=None) -> ScoredSnapshot` --
   the ONE function that calls `build_detectors(bundles)` then
   `score_snapshot(..., detectors=detectors)`. `score_snapshot_dir` now
   calls it (dropped its own inline `build_detectors`/`score_snapshot`
   pair); `scripts/build_fixture_snapshot.py`'s pass-2 "real" scoring now
   imports and calls the SAME function instead of a bare `score_snapshot`
   (pass-1, the population-free bootstrap that only learns gate/rated
   status, is untouched -- detectors there would be wasted work, nothing
   downstream reads that pass's `suspicion`). The two producers of a
   scored snapshot can no longer independently drift on whether/how
   detectors run -- re-verified by mutating the shared function's
   `detectors=` line to `None` and confirming 7 tests across BOTH
   `tests/snapshot/test_store_cli.py` and `tests/snapshot/
   test_reproduce_fixture.py` go red (pasted below), then restored the
   single line by hand (this file was mid-edit relative to the last
   commit, so `git show HEAD:<path>` would have reverted the whole
   refactor -- reverted just the mutated line instead, verified against a
   clean `git diff` afterward).
2. **`tests/snapshot/test_reproduce_fixture.py`**: `test_reproduce_matches_expected_hash`
   (the module's own "permanent, always-on gate") now imports
   `score_snapshot_with_detectors` instead of bare `score_snapshot`, and
   additionally asserts `snapshot.counts.suspicion_counts ==
   {"closed_unread": 12}` -- the gate now proves detectors fired, not just
   that they didn't crash anything.
3. **Fixture regenerated once**: `python scripts/build_fixture_snapshot.py`
   (default `--sample-root`/`--out-dir`, i.e. `tests/fixtures/snapshot_50`
   from `~/Projects/cvi-scratch/sample-200`) printed `records_hash
   fe49557ca04b2de7a4b1518d6a3a45d68e1ffcc1139a80e43aa9f375eb58aed4` --
   exactly the hash predicted in the first commit. `git status` afterward
   showed exactly ONE changed file: `expected.json`, and its own diff is
   exactly one line (the `records_hash` scalar) -- `manifest.json`,
   `populations.json` and every file under `bundles/` came out
   byte-identical, confirming detectors touch nothing `expected.json`
   records besides that top-level scalar (they run at stage 10, strictly
   after ranking, `03` section 6).
4. **This packet's own tests verify for real**: every `tests/snapshot/
   test_store_cli.py` call that stores `tests/fixtures/snapshot_50` now
   omits `--no-verify` entirely -- the directory's own `expected.json` is
   correct again, so `verify_expected` passes on the real, unmodified file.
   `--no-verify` is exercised exactly once now
   (`test_expected_json_mismatch_bypassed_by_no_verify`), against a
   deliberately corrupted SYNTHETIC `expected.json` this suite builds
   itself -- proving the flag still works, never masking a real check
   against real data.

## Coordinator addendum, addressed

- `finalize_snapshot_hash` is called (item 4 above) — already true before
  the addendum arrived; `snapshot_dump_hash` is printed and is what makes
  `queries.snapshot_summary(...)["canonical_dump_hash"]` non-`None`.
- Served-DB guard location: checked **both** main (`08c1502`) and
  `~/Projects/cvi-wt-W4-4` (`f6a398c`) immediately before finishing —
  `porchlamp/serve/guard.py` does not exist on either as of this handoff. W4-4's
  REWORK round 1 ruling (00-README.md, 2026-09-13) names it as the future
  home of a real v2 guard ("recompute snapshot_dump_hash, refuse
  non-finalised with 503 everywhere, refuse fixture-clock rows"), but it
  has not been delivered anywhere this worktree can read. `test_served_db_guard_accepts_this_db`
  probes for `porchlamp.serve.guard` first and **fails loudly** (not silently) if
  it appears without being wired into the test — a deliberate tripwire so
  this doesn't silently test the wrong thing forever. Until then it
  demonstrates the only guard that exists today (`scripts.serve.
  refusal_reasons` / `porchlamp.lakehouse.OperationalStore`, v1-only) does not
  misfire against a v2-only database — `OperationalStore.__init__` raises
  `sqlite3.OperationalError` against a v2 schema (confirmed directly), the
  exact failure `porchlamp/serve/app.py::_run_served_db_guard` (branch `W4-4`,
  read only) already catches to mean "nothing v1-shaped here to refuse".

## Test suite

`tests/snapshot/test_store_cli.py` (10 tests):

- `test_store_snapshot_50_counts_and_hashes` — real fixture, verified
  against its real `expected.json` (no `--no-verify` needed, since the
  fixture regeneration below); printed counts/hashes; `snapshot_summary`
  counts/as_of/benchmark_version/constants_hash match the manifest;
  `canonical_dump_hash` non-null (finalized); `detector_summary ==
  {"closed_unread": 12}`.
- `test_store_snapshot_50_list_ranked_and_not_ranked` — `list_ranked` 0
  rows/0 total; `list_not_ranked` 50 rows, every reason non-empty,
  `sum(counts.values()) == 50`.
- `test_store_twice_is_immutability_error_and_db_unchanged` — second call
  exits 1, DB sha256 unchanged.
- `test_served_db_guard_accepts_this_db` — see addendum section above.
- `test_create_app_serves_snapshot_and_not_ranked_if_w4_4_has_landed` —
  skipped today (`porchlamp.serve.app` not importable); body is real and ready,
  runs for real the moment W4-4 lands and this branch is rebased onto it.
- `test_previous_wires_param_jump_suspicion` — 30 synthetic repos, one
  engineered `dr90_decision_rate` swing (0.05→0.95, n=20 both sides,
  score jump 93.75 > 40 with n ≥ 10 both sides): `suspicion ==
  ["param_jump"]` on that one repo, `[]` on the other 29.
- `test_expected_json_mismatch_exits_1_without_no_verify` — exit 1, no DB
  file created at all (verification runs before the DB is even opened).
- `test_expected_json_mismatch_bypassed_by_no_verify` — exit 0 with the
  same corrupted `expected.json`.
- `test_expected_json_match_exits_0` — positive control, a correct
  `expected.json` stores cleanly with no flag needed.
- `test_write_scored_snapshot_is_one_transaction` — monkeypatches
  `write_records` to raise after `write_snapshot` has already run inside
  one call; asserts zero `snapshots` rows survive.

Suite (full, `-n 4`, from `cvi-wt-W4-6`, final rebase base — see top of
this file for the exact sha):

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -B -m pytest -n 4 -p no:cacheprovider; echo exit=$?
2477 passed, 2 skipped in 11.66s
exit=0
```

Ruff: `python -m ruff check porchlamp/ scripts/ tests/snapshot/` → `All checks passed!`

Every count above is unchanged from the first commit (10 tests, 2477/2 in
the full suite) -- the follow-up commit changed WHAT is being verified
(against the real, regenerated `expected.json` instead of `--no-verify`),
not how many tests exist.

## Printed hashes for snapshot_50 (this base commit)

```
snapshot snapshot_50
rated 0 gated 35 unrated 15
records_hash fe49557ca04b2de7a4b1518d6a3a45d68e1ffcc1139a80e43aa9f375eb58aed4
constants_hash 748be6e459b0d9eac279233dac75acd5ede7da9711590c84fd0317a84762cb6b
snapshot_dump_hash ed1361d8ac8a016b028130bf98684f0042cdc497fa0991409b1bf0aa5ecc7a6f
```

(`snapshot_dump_hash` covers `repositories`/`params`/`populations` rows for
this snapshot id only — it will differ run to run only if the code that
produces those rows changes; it is NOT expected to match anything already
pinned elsewhere, since nothing wrote a v2 store from this fixture before
this packet existed.)

## Mutants (each: red, output pasted below, restored via `git show HEAD:<path> > <path>` — never stash/checkout--)

Below are the original 5 mutants from the first commit (`b55a3f4`,
`detectors=detectors` → `detectors=None` inside what was then
`score_snapshot_dir`'s own inline call). Since the follow-up commit moved
that exact line into the new shared function `score_snapshot_with_detectors`,
I re-ran mutant M1 against its new location (**M1-recheck** below) rather
than trust that a refactor left it equivalent: it now kills 7 tests across
two files instead of 2, because BOTH producers of a scored snapshot go
through the one function. Restored the single mutated line by hand
(`git show HEAD:<path>` would have reverted the whole in-progress refactor,
since this file was mid-edit relative to the last commit) and confirmed a
clean `git diff` afterward. Full output:
`~/Projects/cvi-scratch/W4-6/mutants/M1-recheck-shared-function.txt`.

**M1 — detectors not wired** (`store_cmd.score_snapshot_dir`: `detectors=detectors` → `detectors=None`):
```
FAILED tests/snapshot/test_store_cli.py::test_store_snapshot_50_counts_and_hashes
FAILED tests/snapshot/test_store_cli.py::test_previous_wires_param_jump_suspicion
AssertionError: assert [] == ['param_jump']
```

**M2 — write outside the transaction** (`write_scored_snapshot`: removed the
`conn.execute("BEGIN")`/rollback wrapping around `write_snapshot`+`write_records`):
```
FAILED tests/snapshot/test_store_cli.py::test_write_scored_snapshot_is_one_transaction
AssertionError: write_snapshot's insert must not survive write_records's failure
assert 1 == 0
```

**M3 — expected-hash check skipped** (`cli._store`: `if not args.no_verify:` → `if False:`):
```
FAILED tests/snapshot/test_store_cli.py::test_expected_json_mismatch_exits_1_without_no_verify
assert 0 == 1
```

**M4 — immutability swallowed** (`cli._store`: `except ValueError as error: print(...); return 1` → `except ValueError: dump_hash = "swallowed"`, falls through to success):
```
FAILED tests/snapshot/test_store_cli.py::test_store_twice_is_immutability_error_and_db_unchanged
assert 0 == 1
```

**M5 — as_of from the clock** (`write_scored_snapshot`: `as_of_text = frozen.as_of.isoformat()` → `datetime.now(timezone.utc).isoformat()`):
```
FAILED tests/snapshot/test_store_cli.py::test_store_snapshot_50_counts_and_hashes
AssertionError: assert '2026-09-13T1....993631+00:00' == '2026-09-12T0....089407+00:00'
FAILED tests/snapshot/test_store_cli.py::test_store_twice_is_immutability_error_and_db_unchanged
assert 'already has repositories rows' in "..." or 'immutable' in "..."
```
(the second failure is a bonus: wall-clock `created_at` also breaks
`write_snapshot`'s own identity check between the two calls — two
independent things go wrong from one mutation.)

Full pasted output for each mutant is under
`~/Projects/cvi-scratch/W4-6/mutants/M{1..5}-*.txt`.

## Runbook (local agents — no code reading required)

A real end-to-end run, from a checked-out `contributor-value-index`
(`PYTHONPATH=$PWD` on every command — the package is installed editable
from the main checkout, so a worktree run without this silently tests the
main checkout instead, per the W4-1 inventory's own risk note):

```bash
cd ~/Projects/contributor-value-index   # or your worktree; PYTHONPATH=$PWD either way
export PYTHONPATH=$PWD

# 1. gather: fetch raw envelopes for N candidate repos into data/raw/
.venv/bin/python scripts/gather.py --limit 200 --ecosystem pypi

# 2. derive: build a v2 FeatureBundle for each gathered repo
.venv/bin/python scripts/derive.py <owner> <name> --ecosystem pypi --fetch
# (repeat per repo, or drive it from your own candidate list — derive.py
# takes one owner/name per invocation)

# 3. build a sample manifest over what you gathered+derived (a "mini
#    data/ tree" with its own manifest.json build_fixture_snapshot.py reads)
.venv/bin/python scripts/make_sample.py \
    --data-dir data --out ~/Projects/cvi-scratch/my-sample \
    --per-ecosystem 50 --total 200

# 4. build the snapshot directory (bundles/, populations.json,
#    manifest.json, expected.json) this packet's store command reads --
#    scores twice internally (population-free pass, then the real pass)
.venv/bin/python scripts/build_fixture_snapshot.py \
    --sample-root ~/Projects/cvi-scratch/my-sample \
    --out-dir ~/Projects/cvi-scratch/my-snapshot \
    --size 200 --snapshot-id my_snapshot_v1

# 5. store: score for real (detectors wired) and write into a v2 SQLite store
.venv/bin/python -m porchlamp.snapshot store \
    ~/Projects/cvi-scratch/my-snapshot --db ~/Projects/cvi-scratch/my-cvi.db
#   (add --previous <earlier snapshot dir> once you have one, to light up
#   param_jump; add --no-verify only if you intentionally changed scoring
#   code since the directory's own expected.json was written)

# 6. serve: the v2 API + UI over that database
.venv/bin/python scripts/serve.py --db ~/Projects/cvi-scratch/my-cvi.db
#   http://127.0.0.1:8000/  (API under /v1/porchlamp, legacy v1 under /legacy)
```

**Known gap, honestly stated**: as of this base commit, `scripts/serve.py`'s
`main()` still opens the v1 `OperationalStore` unconditionally
(`cvi-wt-W4-4` REWORK round 1 Blocker B-1, already dispatched, not this
packet's to fix) — pointed at a v2-only database like step 5's output, it
raises `sqlite3.OperationalError` before `uvicorn` ever starts. Until that
lands on main, step 6 needs `cvi-wt-W4-4`'s own `scripts/serve.py`
(`porchlamp.serve.app.create_app`) instead of main's. Once W4-4 lands, step 6 as
written above works unchanged.
