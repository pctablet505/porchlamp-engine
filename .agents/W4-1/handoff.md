# W4-1 handoff — package move and dead-code deletion

Base: main `130ed0f` (W4-5b landed; rebased onto this tip immediately
before handoff, per instruction — the branch's original base was `1e88a85`,
predating W4-5b entirely). Branch `W4-1`, worktree `~/Projects/cvi-wt-W4-1`.
Read first: `.agents/W4-1/inventory.md` (the read-only inventory this
packet executes).

## What moved / deleted / shimmed

**1. `cvi/ingest.py` split (3 commits: `2938c93`, `d99ee5b`, `2bd2dbe`)**

- Rate limiter / ETag cache / circuit breaker (`TokenBucketRateLimiter`,
  `ETagCache`, `FetchResult`, `CircuitBreaker`, `_parse_reset_epoch`,
  `CachedHttpClient`, `IngestError`, `RateLimitedError`) moved **verbatim**
  to `cvi/collect/ratelimit.py` (source byte-identical for 7 of 8 symbols;
  `CachedHttpClient` differs by one docstring word, no code change).
  **Correction (W4-1f, review W-1): the tests were NOT moved verbatim** —
  17 of 23 carried-over test definitions were hand-rewritten (only the
  shared `FakeClock` fixture is byte-identical), and three assertions
  were silently dropped in the process: the `capacity=-0.5` rejection
  case, `assert breaker.trips == 1`, and the `trip()` return value for an
  already-passed reset. The review proved this mattered with a live
  mutant (`if capacity <= 0` → `if capacity == 0`: survived the rewritten
  file, killed by the original assertion). All three were restored
  verbatim from `git show 130ed0f:tests/test_ingest.py` in `tests/collect/
  test_ratelimit.py`, W4-1f; the mutant now dies against the restored
  file too (re-verified).
- `apply_raw_metrics`/`_enum_value` moved to `cvi/lakehouse.py` (its only
  argument type, `OperationalStore`, already lives there). Every caller
  (`scripts/promote.py`, `scripts/demo_app.py`, 5 test files) re-pointed
  in the same commit.
- The GHArchive bulk-stream stack (`GHArchiveStreamer`,
  `map_pull_request_event`, `gharchive_hourly_url`, `_parse_iso8601`) and
  `ingest_and_score` were **Tier A dead code** (zero production callers,
  no relocation target named anywhere in `04-ARCHITECTURE.md`) — deleted
  outright. `tests/test_ingestion_integration.py` (1,222 lines) was
  deleted in full.
  **Correction (W4-1f, review W-2): the original justification here
  understated what this file covered.** Only about 5 of its 20 tests
  actually concerned the deleted GHArchive/`ingest_and_score` stack; the
  other ~15 exercised **live v1 surface** through that pipeline used as a
  fixture — `cvi.api.create_app` (persona reprojection, deep-dive,
  the matrix endpoint, leaderboard order), `cvi.engine.score_repository`,
  `query_matrix`, `count_by_quadrant`, off-platform routing, black-hole
  gating, pillar-score distinctness. Every one of those areas does have
  independent surviving coverage (`tests/test_api.py`,
  `tests/test_deepdive.py`, `tests/test_performance.py`,
  `tests/test_lakehouse.py`, `tests/test_engine.py`,
  `tests/test_stage0.py`) — the deletion is safe — but what is genuinely
  gone, and was not called out plainly enough here originally, is the
  **single end-to-end path**: nothing in the suite now exercises the v1
  raw-events → `score_repository` → `OperationalStore` → `cvi.api`
  pipeline as one continuous flow through a live server the way this
  file did. Piecemeal coverage of every step is not the same guarantee
  as one test proving the steps compose correctly end to end, and no
  replacement for that end-to-end shape was written as part of this
  packet (it was out of scope: rebuilding an equivalent end-to-end test
  around only `apply_raw_metrics`'s surviving half, without
  `ingest_and_score` to drive it, would be new test design, not a
  "package move").
- `cvi/ingest.py` then became a one-release deprecation shim (re-exporting
  the `cvi.collect.ratelimit` names with a `DeprecationWarning`), per the
  task's instruction — then **deleted** in the next commit: grep sweep
  (`grep -rn "cvi\.ingest\|cvi/ingest\|from \.ingest" cvi scripts tests`)
  showed zero in-repo callers outside the shim's own test and prose
  comments, so no in-repo caller justified keeping it.

**2. `cvi/benchmark.py` deleted (`aa7c011`)**

Zero production importers (confirmed by grep, matching the inventory's
own finding); its 8 test importers rewritten to import
`cvi.benchmark_data` directly (one boundary crossing at a time, ahead of
item 3's move).

**3. `cvi/benchmark_data.py` → `tests/fixtures/synthetic_catalog.py`
(2 commits: `4fdc18c`, `bca968e`, plus the rule in `7c5bd87`)**

- The served-DB guard's fixture-clock sentinel (`_FIXTURE_CLOCK_SENTINEL`
  in `cvi/serve/guard.py`) was **already frozen independently** before
  this packet started (landed with the W4-4 REWORK) — confirmed, not
  re-done. Its cross-check test (renamed
  `test_fixture_clock_sentinel_matches_synthetic_catalog`) re-pointed at
  the new import path.
- **The one real production import found and removed**: `cvi/lakehouse.py`
  `OperationalStore.seed_benchmark_catalog` was production code that
  lazily imported `cvi.benchmark_data.generate_synthetic_pr_events` — a
  production module reaching into the fixture module, exactly what the
  non-negotiables forbid. Deleted from `OperationalStore` entirely; the
  identical logic now lives as a free function,
  `tests.fixtures.synthetic_catalog.seed_benchmark_catalog(store, repos,
  now)`, operating on `store` through its public API instead of as one of
  its methods. Regression test:
  `test_operational_store_has_no_synthetic_catalog_seeder`.
- Every remaining importer (14 files) re-pointed to
  `tests.fixtures.synthetic_catalog`; the guard's runtime refusal messages
  and the 4 test assertions checking their text updated
  (`"benchmark_data"` → `"synthetic_catalog"`).
- New enforced rule, `tests/core/test_import_graph.py::
  test_no_production_import_of_the_synthetic_catalog_fixture`: neither
  `cvi/` nor `scripts/` may import `tests.fixtures.synthetic_catalog`,
  directly or transitively named — added only once nothing under either
  tree still needed to (after item 4 landed).

**4. `scripts/demo_app.py` folded into a test helper (`670754e`)**

Full logic (26 deterministic fixtures, seeding, `create_demo_app`,
`main`) moved verbatim to `tests/helpers/demo_app.py`. `scripts/demo_app.py`
is now a one-release deprecation shim re-exporting those names with a
`DeprecationWarning`, plus the same `sys.path` bootstrap
`scripts/promote.py` already uses (so `.venv/bin/python
scripts/demo_app.py` keeps running standalone without `PYTHONPATH=$PWD` —
verified by running it from `/tmp` with an absolute path: server starts,
binds, serves). `tests/test_webapp.py` (untouched — it pins v1 web + v1
demo end to end and is explicitly deferred to a later, lead-verified
packet per the inventory's section 6 checklist) still loads
`scripts/demo_app.py` by file path and uses
`demo.build_fixture_store`/`demo.create_demo_app`/`demo.main` unchanged;
all 53 of its tests pass against the shim.

**5. Package data / CI (`c331655`)**

`cvi.store.migrations` package-data and `cvi.serve`/`cvi.snapshot` in
`packages=[...]` were already correct (landed with W4-4/W4-6, before this
packet). The actual non-editable-install check (built with `python -m
build --sdist` into a scratch venv, then `pip install
dist/*.tar.gz[server]`) surfaced a real, pre-existing blocker: `cvi.serve.
app`/`cvi.api`/`cvi.serve.contract` import `fastapi`+`pydantic` directly,
and `cvi.collect.ratelimit` (this packet's own move, carrying forward a
gap `cvi/ingest.py` already had) imports `httpx` — none of the three were
declared anywhere a plain install could reach (`server` extra had only
`uvicorn`). Fixed: `server` now lists `fastapi`, `httpx`, `pydantic`,
`uvicorn` together. CI's `package` job extended to grep the sdist for the
migration `.sql` files and do a real `pip install [server]` + import
check, turning the one-off manual verification into a standing gate.

**6. Import-graph rules + docs (`2938c93`, `7c5bd87`, `6a0a845`)**

- `tests/core/test_import_graph.py::test_collect_ratelimit_imports_only_stdlib_or_httpx`
  pins `cvi/collect/ratelimit.py` to stdlib + `httpx` only (no `cvi.*`).
- `tests/core/test_import_graph.py::test_no_production_import_of_the_synthetic_catalog_fixture`
  (above).
- `docs/v2/04-ARCHITECTURE.md` §2 layout table rewritten from aspirational
  to actual: real file names for `collect/`, `verify/`, `store/`,
  `serve/`, `audit/`; `cli.py` marked as not-existing-and-not-being-built
  (lead ruling); `scripts/` documented as real entry points, not thin
  shims; `tests/fixtures/synthetic_catalog.py` and `tests/helpers/
  demo_app.py` added to the tree; the "Deleted" section rewritten from
  "planned" to "landed".

## Non-editable-install proof (pasted output)

```
$ python -m build --sdist --outdir <scratch>/verify-build .   # scratch venv, python3.12
$ <scratch>/verify-venv/bin/pip install <scratch>/verify-build/cvi-0.1.0.tar.gz[server]
$ cd /tmp   # neutral cwd — rules out python -c's sys.path[0]='' resolving to a checkout
$ <scratch>/verify-venv/bin/python -c "
import cvi
print('cvi at:', cvi.__file__)
import cvi.serve.app
print('cvi.serve.app imported OK:', cvi.serve.app.__file__)
import cvi.snapshot.store_cmd
print('cvi.snapshot.store_cmd imported OK:', cvi.snapshot.store_cmd.__file__)
import cvi.collect.ratelimit
print('cvi.collect.ratelimit imported OK:', cvi.collect.ratelimit.__file__)
import cvi.store.migrations as m
import os
mdir = os.path.dirname(m.__file__)
print('migrations dir:', mdir)
print('sql files:', sorted(f for f in os.listdir(mdir) if f.endswith('.sql')))
"
cvi at: .../verify-venv/lib/python3.12/site-packages/cvi/__init__.py
cvi.serve.app imported OK: .../site-packages/cvi/serve/app.py
cvi.snapshot.store_cmd imported OK: .../site-packages/cvi/snapshot/store_cmd.py
cvi.collect.ratelimit imported OK: .../site-packages/cvi/collect/ratelimit.py
migrations dir: .../site-packages/cvi/store/migrations
sql files: ['0001_init_v2.sql', '0002_indexes.sql']
```
Plus: confirmed `benchmark_data.py`/`ingest.py`/`benchmark.py` are absent
from the installed `cvi/` package directory. Exact commands replayed as
the new CI `package` job steps.

## Suite / lint, per commit and final (rebased onto main `130ed0f`)

Every commit above was green on its own at the time it was made (`env -u
PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest -p
no:cacheprovider -n 4`, `ruff check cvi/ scripts/ tests/`). Final state
after rebase:

- `ruff check cvi/ scripts/ tests/` → **All checks passed!**
- Full suite → **2573 passed, 1 skipped** (main's own baseline at
  `130ed0f`, after W4-5b landed, was higher than this branch's original
  base of 2518 because W4-5b added its own tests; the two counts are not
  directly comparable across the rebase — no test was silently dropped,
  confirmed by `git diff --stat main..HEAD` showing net **-1555 lines**
  with the deletions all accounted for above).
- The two extra CI steps (`tests/audit/test_anchors_fixture.py`,
  `tests/audit/test_sensitivity_fixture.py`,
  `tests/snapshot/test_reproduce_fixture.py`) → 18 passed, 1 skipped.

## COULD NOT VERIFY

- The actual GitHub Actions run of the new CI steps (no runner available
  in this environment) — every step's exact command was replayed locally
  instead, all green (see above).
- `tests/benchmark_sla_runner.py` and `scripts/build_fixture_snapshot.py`
  are standalone runners with no importer anywhere in the tree (confirmed
  by grep) — their import was fixed (`benchmark_sla_runner.py`'s
  `cvi.benchmark` → `cvi.benchmark_data` in the earlier commit, then to
  `tests.fixtures.synthetic_catalog`) but neither was *executed*
  end-to-end as part of this packet (out of scope: they are SLA/perf
  tools, not part of the standard suite).

## What the inventory predicted that turned out different

- The inventory (written against `c19a93a`) proposed the served-DB guard
  relocation (its own step 1) as a **prerequisite** for this packet. By
  the time W4-1 actually ran, that relocation had **already landed** as
  part of the W4-4 REWORK (Blocker B-4 / Warning W-5) — `cvi/serve/
  guard.py` already existed with its own frozen sentinel and cross-check
  test before this branch's first commit. This packet only needed to
  re-point that already-independent sentinel's cross-check test at the
  new fixture location, not build the freeze itself.
- The inventory's own proposed 10-step commit sequence (§8) included
  fixing `cvi/store/migrations/__init__.py`'s `_split_statements` hygiene
  item (step 10) and a full README.md rewrite (step 9). **The task text
  handed to this packet explicitly narrowed scope to 6 items and did not
  include either** — left untouched, not silently dropped: `README.md`'s
  module map is still entirely v1 (unaffected by this packet, since it
  never named `cvi/ingest.py`/`cvi/benchmark.py`/`cvi/benchmark_data.py`
  in a way this packet's changes would make MORE wrong than before), and
  `_split_statements` is unrelated to any file this packet touched.
- New finding, not anticipated by the inventory: `cvi/lakehouse.py`'s
  `OperationalStore.seed_benchmark_catalog` was a **second** production
  import of the fixture module (via a lazy `from .benchmark_data import
  generate_synthetic_pr_events` inside the method body) that the
  inventory's §5 discussion did not name explicitly (it named `scripts/
  serve.py`'s `BENCHMARK_NOW` import as "the load-bearing dependency" but
  did not flag this second one). Found only by grepping
  `cvi/lakehouse.py` directly during item 3's execution; fixed per the
  same non-negotiable.
- New finding, not anticipated by the inventory: the packaging gap in
  item 5 (`cvi.serve.app` cannot import in a non-editable install because
  `fastapi`/`pydantic`/`httpx` are not declared where a plain install can
  reach them). Pre-existing (not introduced by this packet), but only
  surfaced by actually doing the verification the inventory recommended
  rather than assuming the existing `packages=[...]`/package-data
  correctness was the whole story.
- Environment hazard (not a code defect, worth flagging for future
  packets): the **shared** venv's editable install
  (`.venv/lib/python3.12/site-packages/__editable___cvi_0_1_0_finder.py`)
  has a `MetaPathFinder` that, for a `cvi.<name>` import whose immediate
  parent package IS `cvi` (i.e. `cvi.<leaf>`, not `cvi.<pkg>.<leaf>`),
  hardcodes the **main checkout's** path regardless of what `cvi.__path__`
  actually resolved to. The normal `PathFinder` (checked first) shadows
  this correctly for anything that still exists in the worktree, but a
  module **deleted in the worktree** silently falls through to this
  finder and resurrects from the main checkout instead of raising
  `ModuleNotFoundError` — e.g. `import cvi.benchmark_data` still
  "succeeded" from this worktree after the file was `git mv`'d away,
  resolving to the main checkout's stale copy. This never affected any
  correctness claim in this handoff (every "zero remaining importer"
  claim here is a `grep` over source files, not a runtime import check),
  but a future agent relying on "the import succeeded" as proof of
  "nothing references the old path" from a worktree against the shared
  venv would be fooled by it. Two-level-deep submodules
  (`cvi.collect.ratelimit`) are unaffected (the finder declines and lets
  the normal, correct `PathFinder` handle them).

## W4-1f — review follow-up round (XS)

Base main (post W4-1 landing, carrying `099cc8e` "W4-1 landed with
follow-ups"). Addresses the review's four Warnings (no Blockers were
raised); the six Notes (N-1..N-6) are left for a separate follow-up per
the coordinator's scope for this round.

- **W-1** (see the corrected paragraph above): restored the three dropped
  assertions in `tests/collect/test_ratelimit.py` verbatim from
  `git show 130ed0f:tests/test_ingest.py`. Re-verified the
  `if capacity <= 0` → `if capacity == 0` mutant: survived before the
  fix, dies after (manual mutant run, not committed; restored before
  commit).
- **W-2** (see the corrected paragraph above): corrected the
  `tests/test_ingestion_integration.py` deletion justification and
  recorded explicitly that the v1 raw→`score_repository`→
  `OperationalStore`→`cvi.api` path has no single end-to-end test
  anymore, only piecemeal per-step coverage.
- **W-3**: replaced the (never-committed, only narrated) finder-trap
  observation with an actual regression test,
  `tests/core/test_import_graph.py::
  test_no_cvi_module_resolves_outside_the_repo_root`. It walks every
  `cvi/**/*.py` module that exists on disk today (must import
  successfully and resolve inside the repo root) **plus** the exact
  three names this packet deleted (`cvi.ingest`, `cvi.benchmark`,
  `cvi.benchmark_data` — must raise `ModuleNotFoundError` naming that
  exact module, or resolve inside root if somehow still importable).
  Passes today. Proved it "would have caught the trap" with a full
  sandboxed reproduction (not touching the real main checkout or the
  shared venv, per the standing rule against touching main's working
  tree): a throwaway venv editable-installed from a copy of the tree
  with the three files restored (simulating "main hasn't caught up
  yet"), checked against a second copy with the files genuinely absent
  (simulating this worktree) — the test's own check script correctly
  flagged all three names as resolving outside the checked root. Also
  tightened the exception handling while building this proof: catching
  bare `ModuleNotFoundError` would silently swallow a resurrected
  module's OWN missing sub-dependency (observed with `httpx` while
  reproducing the trap for `cvi.ingest` in a bare venv) as if the target
  module were simply absent; narrowed to `exc.name == name` so only a
  genuinely-missing target module is treated as an acceptable outcome.
- **W-4**: widened the `cvi/` arm of
  `test_no_production_import_of_the_synthetic_catalog_fixture` from
  "reject only `tests.fixtures.synthetic_catalog`" to "reject any
  `tests.` import" (no legitimate reason for `cvi/` to import anything
  under `tests/`); kept the narrow, name-specific rule for `scripts/` so
  `scripts/demo_app.py`'s shim import of `tests.helpers.demo_app` stays
  legal. Reproduced the review's exact planted violation
  (`from tests.helpers.demo_app import DEMO_NOW` in `cvi/lakehouse.py`):
  green before this fix, red after (manual plant, not committed;
  restored before commit).

Suite and lint after this round: see the commit message for the exact
line. N-1..N-6 (the Notes) are explicitly out of scope for this round.
