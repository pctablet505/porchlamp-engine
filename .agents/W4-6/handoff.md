# W4-6 handoff — `cvi.snapshot store`: score a snapshot directory into a v2 store

Base: `08c1502` (main tip at rebase time). Commit: `e781e55` on branch
`W4-6` (worktree `~/Projects/cvi-wt-W4-6`). Diffstat base→HEAD:

```
 cvi/snapshot/__init__.py          |  10 +
 cvi/snapshot/cli.py               |  88 +++++++-
 cvi/snapshot/fixture_io.py        |  91 ++++++++
 cvi/snapshot/store_cmd.py         | 206 ++++++++++++++++++
 scripts/build_fixture_snapshot.py |  64 +-----
 tests/snapshot/test_store_cli.py  | 445 ++++++++++++++++++++++++++++++++++++++
 6 files changed, 850 insertions(+), 54 deletions(-)
```

## What was delivered

`python -m cvi.snapshot store <snapshot_dir> --db <path> [--previous <dir>] [--no-verify] [--code-commit <sha>]`:

1. Loads `<snapshot_dir>` (`bundles/*.json`, `populations.json`,
   `manifest.json`, optional `expected.json`) via the shared loader moved
   into `cvi/snapshot/fixture_io.py` (`FrozenFixture`/`load_frozen_fixture`
   — cut out of `scripts/build_fixture_snapshot.py` verbatim, R-2; that
   script now imports and re-exports them so its own tests, which load it
   by path via `importlib`, keep working unchanged — verified directly).
2. Scores it for real: `cvi.core.pipeline.score_snapshot` with detectors
   wired via `cvi.verify.pipeline_hook.build_detectors(frozen.bundles)`.
   `--previous <dir>` is loaded and scored the same way (no detectors
   composed for it — only its records' `params` feed `detect_param_jump`)
   and passed as `score_snapshot`'s `previous=`.
3. `expected.json`, if present, is checked against the freshly computed
   `records_hash` (`cvi.snapshot.store_cmd.verify_expected`); a mismatch
   exits 1 with a clear message unless `--no-verify`.
4. Opens/creates the DB (`cvi.store.schema.connect` + `cvi.store.
   migrations.apply_migrations`), writes `write_snapshot` + `write_records`
   in ONE transaction this command opens itself (`cvi.snapshot.store_cmd.
   write_scored_snapshot`), then calls `cvi.store.writer.
   finalize_snapshot_hash` (necessarily a second, later transaction — that
   function's own docstring: it reads `db_path` fresh off disk, which
   cannot see this connection's own uncommitted writes) and prints the
   result.
5. The writer's immutability `ValueError` (a snapshot id already has rows
   in `--db`) is caught and printed as a one-line message, exit 1, the DB
   file byte-for-byte unchanged (verified: sha256 before/after).

No wall clock anywhere in this command: `as_of`/`created_at`/`promoted_at`
are all `frozen.as_of` (from the snapshot directory's own `manifest.json`);
`code_commit` defaults to `git rev-parse HEAD` (same helper `cvi snapshot
freeze` already uses), never a timestamp.

New/moved files:

- `cvi/snapshot/fixture_io.py` (new): `FrozenFixture`/`load_frozen_fixture`,
  moved out of `scripts/build_fixture_snapshot.py`. `expected.json` is now
  OPTIONAL (`None` when absent) — a real, non-fixture snapshot directory
  has no pinned expectation of its own; every existing caller
  (`tests/snapshot/test_reproduce_fixture.py`, `tests/audit/
  test_anchors_fixture.py`, `tests/audit/test_sensitivity_fixture.py`) only
  ever points this at `tests/fixtures/snapshot_50`, which always ships
  one, so their behaviour is unchanged (full suite green, checked).
- `cvi/snapshot/store_cmd.py` (new): `score_snapshot_dir`, `verify_expected`,
  `manifest_root_of`, `write_scored_snapshot`, `VerificationError`.
- `cvi/snapshot/cli.py`: `store` subcommand + `_store` handler.
- `cvi/snapshot/__init__.py`: the four new names added to `__all__` (an
  existing test, `test_every_name_cli_imports_from_the_snapshot_package_is_exported`,
  enforces that every name `cli.py` imports from `cvi.snapshot.*` is
  re-exported at the package root — caught this the first time, fixed).
- `scripts/build_fixture_snapshot.py`: `FrozenFixture`/`load_frozen_fixture`
  removed, re-imported from `cvi.snapshot.fixture_io` (one `# noqa: F401`,
  since they're kept only for backward-compatible re-export).

`manifest_root` (the `snapshots.manifest_root` column): this directory
shape carries no envelope-level freeze manifest of its own (`03` section 5's
Merkle-root-over-envelope-hashes concept belongs to `cvi snapshot freeze`,
a different artifact). `write_scored_snapshot` computes it as
`cvi.snapshot.manifest.merkle_root(sorted(frozen.raw_hashes.values()))` —
reusing that function rather than a second Merkle implementation (R-2) —
the Merkle root of every bundle's own `raw_hash` in the snapshot, the
closest real analogue this directory shape has to "which inputs this
snapshot covers".

## A real, load-bearing finding: `tests/fixtures/snapshot_50/expected.json` predates detector wiring

`scripts/build_fixture_snapshot.py` (unmodified by this packet, besides the
loader move) scores its fixture with `detectors=None` — so does `tests/
snapshot/test_reproduce_fixture.py`'s own "permanent, always-on gate"
(`test_reproduce_matches_expected_hash`, that test's own docstring). This
packet's whole job (item 1) is to wire `build_detectors` in, and doing so
genuinely changes the fixture's `records_hash`: **verified directly**, 12 of
the 50 real fixture repos trip `closed_unread` (0 with `detectors=None`, 12
with it — nothing else fires on a first-ever snapshot with no `previous`).
`records_hash` hashes every field of every `ScoreRecord`, `suspicion`
included, so this is a real, correct divergence, not a bug.

Consequence: `tests/fixtures/snapshot_50/expected.json`'s pinned
`records_hash` is now stale relative to what `cvi.snapshot store` correctly
computes on that exact directory. I did **not** touch that shared fixture
file — it is W2-8b/W3-2f's, referenced by three other test files and by the
CI "reproduce (fixture snapshot)" step, and regenerating it would silently
flip that permanent no-detector gate's own comparison out from under it.
Instead: `tests/snapshot/test_store_cli.py`'s own snapshot_50 tests run with
`--no-verify` and pin the actual, independently-verified WITH-detectors hash
(`fe49557c…`) as their own constant, with the full reasoning in both the
module docstring and each test's docstring.

**Recommendation for the lead**: once every packet that touches detector
wiring on this fixture has landed, regenerate `tests/fixtures/snapshot_50/
expected.json` via `scripts/build_fixture_snapshot.py` with `detectors=
build_detectors(bundles)` wired into its own scoring calls (currently it
still calls `score_snapshot` with no `detectors=` in both its pass-1 and
pass-2 calls) — and then update `test_reproduce_matches_expected_hash`'s own
call to match, so the "permanent gate" and the real, fully-wired pipeline
stop disagreeing by construction. I left this alone deliberately: it is a
cross-cutting decision for the fixture's owners (W2-8b/W3-2f), not a W4-6
call, and touching it here would have broken tests outside this packet's
ownership.

## Coordinator addendum, addressed

- `finalize_snapshot_hash` is called (item 4 above) — already true before
  the addendum arrived; `snapshot_dump_hash` is printed and is what makes
  `queries.snapshot_summary(...)["canonical_dump_hash"]` non-`None`.
- Served-DB guard location: checked **both** main (`08c1502`) and
  `~/Projects/cvi-wt-W4-4` (`f6a398c`) immediately before finishing —
  `cvi/serve/guard.py` does not exist on either as of this handoff. W4-4's
  REWORK round 1 ruling (00-README.md, 2026-09-13) names it as the future
  home of a real v2 guard ("recompute snapshot_dump_hash, refuse
  non-finalised with 503 everywhere, refuse fixture-clock rows"), but it
  has not been delivered anywhere this worktree can read. `test_served_db_guard_accepts_this_db`
  probes for `cvi.serve.guard` first and **fails loudly** (not silently) if
  it appears without being wired into the test — a deliberate tripwire so
  this doesn't silently test the wrong thing forever. Until then it
  demonstrates the only guard that exists today (`scripts.serve.
  refusal_reasons` / `cvi.lakehouse.OperationalStore`, v1-only) does not
  misfire against a v2-only database — `OperationalStore.__init__` raises
  `sqlite3.OperationalError` against a v2 schema (confirmed directly), the
  exact failure `cvi/serve/app.py::_run_served_db_guard` (branch `W4-4`,
  read only) already catches to mean "nothing v1-shaped here to refuse".

## Test suite

`tests/snapshot/test_store_cli.py` (10 tests):

- `test_store_snapshot_50_counts_and_hashes` — real fixture, `--no-verify`;
  printed counts/hashes; `snapshot_summary` counts/as_of/benchmark_version/
  constants_hash match the manifest; `canonical_dump_hash` non-null
  (finalized); `detector_summary == {"closed_unread": 12}`.
- `test_store_snapshot_50_list_ranked_and_not_ranked` — `list_ranked` 0
  rows/0 total; `list_not_ranked` 50 rows, every reason non-empty,
  `sum(counts.values()) == 50`.
- `test_store_twice_is_immutability_error_and_db_unchanged` — second call
  exits 1, DB sha256 unchanged.
- `test_served_db_guard_accepts_this_db` — see addendum section above.
- `test_create_app_serves_snapshot_and_not_ranked_if_w4_4_has_landed` —
  skipped today (`cvi.serve.app` not importable); body is real and ready,
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

Suite (full, `-n 4`, from `cvi-wt-W4-6`, base `08c1502`):

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -B -m pytest -n 4 -p no:cacheprovider; echo exit=$?
2477 passed, 2 skipped in 11.91s
exit=0
```

Ruff: `python -m ruff check cvi/ scripts/ tests/snapshot/` → `All checks passed!`

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
.venv/bin/python -m cvi.snapshot store \
    ~/Projects/cvi-scratch/my-snapshot --db ~/Projects/cvi-scratch/my-cvi.db
#   (add --previous <earlier snapshot dir> once you have one, to light up
#   param_jump; add --no-verify only if you intentionally changed scoring
#   code since the directory's own expected.json was written)

# 6. serve: the v2 API + UI over that database
.venv/bin/python scripts/serve.py --db ~/Projects/cvi-scratch/my-cvi.db
#   http://127.0.0.1:8000/  (API under /v1/cvi, legacy v1 under /legacy)
```

**Known gap, honestly stated**: as of this base commit, `scripts/serve.py`'s
`main()` still opens the v1 `OperationalStore` unconditionally
(`cvi-wt-W4-4` REWORK round 1 Blocker B-1, already dispatched, not this
packet's to fix) — pointed at a v2-only database like step 5's output, it
raises `sqlite3.OperationalError` before `uvicorn` ever starts. Until that
lands on main, step 6 needs `cvi-wt-W4-4`'s own `scripts/serve.py`
(`cvi.serve.app.create_app`) instead of main's. Once W4-4 lands, step 6 as
written above works unchanged.
