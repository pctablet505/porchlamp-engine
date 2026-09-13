# W4-1 inventory — package move and dead-code deletion (read-only)

Prepared on branch `W4-1-inventory`, worktree `~/Projects/cvi-wt-W4-1-inventory`,
against **main `c19a93a`** ("docs(v2): lead log — W4-6 dispatched"). Main moved
three times while this inventory was being written (`88ce3cc` → `d112aa2`
W4-5a landed → `021abc1` W4-4 received/review → `c19a93a` W4-6 dispatched);
this document is pinned to `c19a93a` and states explicitly, per finding, when
a fact comes from a branch that has not landed yet. No source file was
changed to produce it; every claim below is grep/AST/ruff output, re-runnable
with `PYTHONPATH=$PWD` from this worktree or the main checkout (both are
`c19a93a`-identical in `cvi/`, `scripts/`, `tests/`, `docs/`, `pyproject.toml`,
`.github/`).

**Landed-vs-in-flight context that changes the packet's real scope.** Most of
the `04-ARCHITECTURE.md` §2 target tree already exists at its target path —
`cvi/core/`, `cvi/collect/`, `cvi/derive/bundle.py`, `cvi/verify/`,
`cvi/snapshot/`, `cvi/store/`, `cvi/audit/`, `cvi/serve/` were built directly
in place by W0–W4-6, strangler-style, rather than written under the old tree
and moved later. **The move W4-1 actually owns is small**: `cvi/ingest.py`
(delete, rescue two live functions), `cvi/benchmark.py` (delete),
`cvi/benchmark_data.py` (move to `tests/fixtures/synthetic_catalog.py`),
`scripts/demo_app.py` (fold into a test helper) — plus the *pyproject/CI/
README* bookkeeping and the *scripts → shims* step, which turns out to be far
larger than "package move" (§7, §8). The 21 other top-level `cvi/*.py` and
`cvi/pillars/*.py` files are **v1, still the only live scoring/serving path**
(`scripts/promote.py`, `scripts/serve.py`, `scripts/demo_app.py` all import
them) and retire at **W5-2** per `04` §10 step 3 ("old engine keeps serving
the v1 DB until W5-2"), not at W4-1 — the packet text itself only names
`ingest.py`/`benchmark.py`/`benchmark_data.py`/`demo_app.py`, never
`cvi/engine.py` or `cvi/pillars/`.

---

## 1. Module table

Counts are importer **files** (not import statements), from an AST-based
scan (`ast.Import`/`ast.ImportFrom`, module-level only) over `cvi/`,
`scripts/`, `tests/`, resolving `from cvi.x import y` to the submodule edge
`cvi.x.y` when `y` is itself a module (so `from cvi.store import schema`
counts as an import of `cvi.store.schema`, not just `cvi.store`). `ruff
--select F401` over `cvi/ scripts/ tests/` returned **"All checks passed!"**
— zero unused-import findings anywhere; the dead code below is whole
unreferenced modules/functions, not stray imports.

Caveat on 0-importer packages: `cvi/pillars/__init__.py` (0 direct
importers) and `cvi/serve/__init__.py` (0) are **not** dead — Python loads a
package's `__init__.py` on the way to any submodule (`cvi.pillars.rigor`,
`cvi.serve.contract`), and this scan only counts direct
`import`/`from...import` edges to the exact dotted name, not the transitive
parent-package load. Treat "0" on a bare package name as "not measured", not
"unused".

### 1.1 `cvi/core/` — already at §2 target path, v2, pure (import-graph-tested)

| File | Lines | §2 target | Class | Importers (cvi/scripts/tests) |
|---|---:|---|---|---|
| `core/__init__.py` | 85 | stays | v2 | 25/1/24 |
| `core/aggregate.py` | 269 | stays | v2 | 5/0/4 |
| `core/commit_domains.py` | 378 | stays | v2 | 4/0/3 |
| `core/constants_v2.py` | 655 | stays | v2 | 27/1/23 |
| `core/engine.py` | 382 | stays | v2 | 1/0/4 |
| `core/gates.py` | 266 | stays | v2 | 5/1/7 |
| `core/hygiene.py` | 113 | stays | v2 | 2/0/1 |
| `core/matrix.py` | 126 | stays | v2 | 1/0/2 |
| `core/methodology.py` | 588 | stays | v2 | 1/0/2 |
| `core/models.py` | 581 | stays | v2 | 23/1/33 |
| `core/normalize.py` | 74 | stays | v2 | 1/0/1 |
| `core/openssf.py` | 106 | stays | v2 | 2/0/2 |
| `core/params/__init__.py` | 122 | stays | v2 | 4/0/9 |
| `core/params/accessibility.py` | 108 | stays | v2 | 1/0/0 |
| `core/params/criticality.py` | 90 | stays | v2 | 1/0/0 |
| `core/params/curves.py` | 259 | stays | v2 | 8/0/0 |
| `core/params/governance.py` | 99 | stays | v2 | 1/0/0 |
| `core/params/responsiveness.py` | 136 | stays | v2 | 1/0/0 |
| `core/params/rigor.py` | 96 | stays | v2 | 1/0/1 |
| `core/persona.py` | 380 | stays | v2 | 1/0/3 |
| `core/pipeline.py` | 568 | stays | v2 | 3/1/5 |
| `core/tiebreak.py` | 297 | stays | v2 | 3/0/6 |

`tests/core/test_import_graph.py` enforces (subprocess + AST) that `cvi.core`
imports nothing outside `cvi.core` + stdlib, and that `import cvi.core` alone
loads no other `cvi.*` package — both green today. Nothing to move here.

### 1.2 `cvi/collect/` — already at §2 target path, v2, network-only

| File | Lines | §2 target | Class | Importers |
|---|---:|---|---|---|
| `collect/__init__.py` | 28 | stays | v2 | 3/2/9 |
| `collect/commits.py` | 249 | stays | v2 | 2/3/1 |
| `collect/depsdev.py` | 477 | stays (≈`depsdev.py`) | v2 | 0/1/1 |
| `collect/ecosystemes.py` | 74 | stays | v2 | 1/0/1 |
| `collect/envelope.py` | 219 | stays | v2 | 11/4/15 |
| `collect/github_graphql.py` | 1365 | stays | v2 | 2/2/1 |
| `collect/governance.py` | 670 | stays | v2 | 0/2/1 |
| `collect/labels.py` | 331 | stays | v2 | 0/2/1 |
| `collect/pike_signals.py` | 767 | stays | v2 | 0/2/2 |
| `collect/repo_object.py` | 424 | stays | v2 | 0/2/1 |
| `collect/universe.py` | 305 | stays | v2 | 0/2/2 |

**Target-vs-code naming gap (flag, don't fix in W4-1):** `04` §2 names one
`collect/github_rest.py` ("repo object … commits w/ emails, issues, runs,
tree, check-runs"); the code splits that across four independently-tested
modules (`repo_object.py`, `commits.py`, `labels.py`, `pike_signals.py`).
`collect/scorecard.py`, `collect/registries.py`, `collect/checkpoint.py`
named in `04` §2 do not exist — envelope-checkpoint logic lives inline in
`collect/universe.py` (per the W1-10 lead-log row), and there is no
dedicated OpenSSF-scorecard *collector* module (only the v1/v2 *scoring*
modules `cvi/openssf.py` / `cvi/core/openssf.py`, and derive-side resolvers
`_resolve_openssf_criticality[_proxy]` in `cvi/derive/bundle.py` that read
whatever envelope is already on disk). **Recommendation for the real
packet:** amend `04` §2 to name the four modules and drop the three
placeholders that were never built, rather than merging four
independently-tested, currently-passing collectors into one file for a
doc-parity win with no functional gain (also blows the packet's own
"net negative lines" measure).

### 1.3 `cvi/derive/`, `cvi/verify/`, `cvi/snapshot/`, `cvi/store/`, `cvi/audit/`, `cvi/serve/` — already at §2 target path

| File | Lines | Class | Importers | Note |
|---|---:|---|---|---|
| `derive/bundle.py` | 2192 | v2 | 1/1/8 | **No `cvi/derive/__init__.py`** — implicit PEP 420 namespace package, unlike every other `cvi/` subpackage. `pyproject.toml` lists `"cvi.derive"` in `packages=[...]`; works today (sdist job green) but is a style outlier — confirm in the CI/pyproject step (§8) it still installs correctly once other files move. Single biggest file in the repo; `04` §2 wants one deriver module per parameter under `derive/features/` — none exist, every `INPUT_MAPPINGS` resolver lives in this one file. Real, but **out of W4-1's stated scope** (packet text never names a `bundle.py` split); flagging only. |
| `verify/__init__.py` | 7 | v2 | 1/0/2 | |
| `verify/detectors.py` | 534 | v2 | 3/0/4 | |
| `verify/live_sample.py` | 575 | v2 | 0/0/1 | **TESTS-ONLY** — nightly canary, no script/cron wired yet |
| `verify/pipeline_hook.py` | 145 | v2 | 0/0/1 | **TESTS-ONLY** — wires `verify` into `core.pipeline`; unreached because no script calls the v2 pipeline yet (W4-6 is building that) |
| `snapshot/__init__.py` | 57 | v2 | 0/1/2 | |
| `snapshot/__main__.py` | 8 | v2 | 0/0/0 | entry point (`python -m cvi.snapshot`), 0 importers expected |
| `snapshot/cli.py` | 163 | v2 | 1/1/1 | |
| `snapshot/manifest.py` | 277 | v2 | 4/1/6 | |
| `snapshot/populations.py` | 248 | v2 | 1/1/6 | |
| `snapshot/reproduce.py` | 321 | v2 | 4/0/2 | |
| `store/__init__.py` | 11 | v2 | 0/0/6 | **TESTS-ONLY** |
| `store/migrations/__init__.py` | 139 | v2 | 0/0/5 | **TESTS-ONLY**; owns the `_split_statements` hygiene item, §7 |
| `store/queries.py` | 1014 | v2 | 1/0/1 | |
| `store/reader.py` | 133 | v2 | 0/0/3 | **TESTS-ONLY** |
| `store/schema.py` | 225 | v2 | 0/0/6 | **TESTS-ONLY** |
| `store/writer.py` | 503 | v2 | 0/0/5 | **TESTS-ONLY** |
| `audit/__init__.py` | 137 | v2 | 1/0/4 | |
| `audit/anchors.py` | 609 | v2 | 1/0/3 | |
| `audit/coverage.py` | 976 | v2 | 2/1/1 | |
| `audit/detectors_report.py` | 300 | v2 | 0/0/1 | **TESTS-ONLY** |
| `audit/forward.py` | 724 | v2 | 0/0/1 | **TESTS-ONLY** |
| `audit/methodology_history.py` | 230 | v2 | 0/1/1 | |
| `audit/sensitivity.py` | 464 | v2 | 1/0/3 | imports **`cvi.constants.PILLAR_WEIGHTS`** (v1) alongside `cvi.core.*` — a real v1/v2 cross-wire, not covered by any import-graph test (that test only constrains `cvi.core`/`cvi.derive.bundle`/`cvi.store`); flag in §7 |
| `audit/ties.py` | 190 | v2 | 0/0/1 | **TESTS-ONLY** |
| `serve/__init__.py` | 1 | v2 | 0/0/0 | |
| `serve/contract.py` | 501 | v2 | 0/1/1 | landed with W4-5a; consumed today only by `scripts/serve_ui_fixtures.py` and its tests |

"TESTS-ONLY" here means zero callers in `cvi/` or `scripts/` **today** — every
one of these is v2 code that landed ahead of the script/CLI that will call it
(`cvi/store/*` waits on W4-6's `python -m cvi.snapshot store`, dispatched the
same day as this inventory per the lead log; `verify/pipeline_hook.py` waits
on the same wiring; the three `audit/*` reporting modules wait on a `cvi.cli
audit` subcommand that does not exist yet, §7). **None of these are W4-1
deletion candidates** — see Tier B in §2.

### 1.4 `cvi/` top level — v1, stays until W5-2, plus 3 real move/delete targets

| File | Lines | §2 target | Class | Importers | Fate |
|---|---:|---|---|---|---|---|
| `__init__.py` | 12 | stays | shared | 2/0/9 | package root, unaffected |
| `models.py` | 253 | none (no v1 slot in `04` §2) | v1 | 6/3/35 | **stays**, retires W5-2 |
| `constants.py` | 215 | none | v1 | 2/1/13 | **stays**, retires W5-2; read by `audit/sensitivity.py` (v2!) and `scripts/derive.py` (`BOT_LOGIN_MARKERS`) — not purely v1-internal, see §7 |
| `stage0.py` | 126 | none | v1 | 0/0/4 (TESTS-ONLY in isolation, but reached transitively via `cvi.engine`) | **stays** |
| `starscout.py` | 122 | none (superseded by `collect/universe.py` + `core` per W1-10) | v1 (thin wrapper now) | 1/0/2 | **stays**; W1-10 already turned the old filter into a thin wrapper over the new mechanism, so this one is nearly free to retire whenever v1 goes — no action now |
| `normalize.py` | 27 | none | v1 | 0/0/2 | **stays** (superseded by `core/normalize.py`) |
| `openssf.py` | 35 | none | v1 | 0/0/5 | **stays** (superseded by `core/openssf.py`) |
| `pillars/__init__.py` | 5 | none | v1 | 0/0/0 | **stays** |
| `pillars/accessibility.py` | 99 | none (superseded by `core/params/accessibility.py`) | v1 | 0/0/2 | **stays** |
| `pillars/criticality.py` | 94 | none | v1 | 0/0/3 | **stays** |
| `pillars/governance.py` | 98 | none | v1 | 0/0/2 | **stays** |
| `pillars/responsiveness.py` | 244 | none | v1 | 0/0/2 | **stays** |
| `pillars/rigor.py` | 114 | none | v1 | 0/0/2 | **stays** |
| `matrix.py` | 84 | none | v1 | 1/0/3 | **stays** |
| `persona.py` | 103 | none | v1 | 2/0/2 | **stays** |
| `engine.py` | 155 | none | v1 | 2/2/7 | **stays**; live caller of `pillars/*`, `matrix.py`, `persona.py`, `normalize.py`, `openssf.py`, `stage0.py`, `starscout.py`, `constants.py`, `models.py` via `scripts/promote.py`, `scripts/demo_app.py` |
| `lakehouse.py` | 695 | none (superseded by `store/*`) | v1 | 2/3/20 | **stays**; live via `scripts/promote.py`, `scripts/serve.py`, `scripts/demo_app.py`; **recommended new home for `apply_raw_metrics`/`ingest_and_score`**, §4 |
| `api.py` | 353 | none (superseded by `serve/app.py`, in flight on W4-4) | v1 | 0/2/12 | **stays** until `/legacy/` retires, §6 |
| `deepdive.py` | 286 | none | v1 | 1/2/3 | **stays**; live via `scripts/promote.py`, `scripts/demo_app.py` |
| `playbook.py` | 381 | none | v1 | 0/0/4 | **stays**; note: this is the module `test_no_clock.py` checks for the *absence* of a `BENCHMARK_NOW` attribute (W0-2 already removed it from here) |
| `provenance.py` | 159 | none | shared utility | 1/1/1 | **stays**; `utc_now_iso()` used by both v1 (`scripts/derive.py`) and v2 (`audit/forward.py`) |
| `paths.py` | 39 | none | shared utility | 0/2/1 | **stays**; `data_root()` used by `scripts/gather.py` (v2 collector entry) and `scripts/derive.py` — cross-cutting, no move needed |
| **`benchmark.py`** | 21 | **delete** | v1 | 0/0/8 (TESTS-ONLY) | pure re-export shim over `benchmark_data` already (`from .benchmark_data import BENCHMARK_NOW, build_benchmark_store, generate_benchmark_catalog, generate_synthetic_pr_events, get_benchmark_catalog, seed_benchmark_store`); explicitly named in `04` §2's deleted list |
| **`benchmark_data.py`** | 827 | **move** → `tests/fixtures/synthetic_catalog.py` | v1 test fixture | 0/2/4 | see §5 |
| **`ingest.py`** | 669 | **delete**, split first | v1, mostly dead | 0/2/5 | see §4 |

### 1.5 `scripts/` — none are thin; every one holds real logic

| File | Lines | def count | cvi imports | Fate |
|---|---:|---:|---|---|
| `run_all.py` | 148 | 5 | `cvi.snapshot.cli` | closest to thin already; becomes a `cvi.cli` shim once one exists |
| `build_universe.py` | 199 | 7 | `cvi.collect.universe` | logic-heavy (candidate filtering loop); shim target |
| `serve.py` | 215 | 7 | `cvi.api`, `cvi.benchmark_data.BENCHMARK_NOW`, `cvi.lakehouse` | **not thin** — owns the two-guard served-DB check (`_fixture_marker_rows`, content-hash) described in its own 45-line module docstring; W4-4 branch (`d2ea48c` lineage → landed `cvi/serve/app.py`) already reuses this file's `open_or_exit` via a **reverse import from `cvi.serve.app` back into `scripts.serve`** — see §7, top risk |
| `backfill_downloads.py` | 283 | 9 | `cvi.collect.envelope.write_envelope` | one-off backfill tool; logic-heavy; candidate for archiving rather than shimming (ask the lead — not evidenced as still-needed) |
| `verify.py` | 364 | 13 | `cvi.collect.envelope.read_envelope` | logic-heavy (v1 crosscheck; `04` §2's `verify/crosscheck.py` doesn't exist yet, so this script's body IS the crosscheck logic) |
| `serve_ui_fixtures.py` | 366 | 10 | `cvi.serve.contract` (`DEFAULT_PAGE_SIZE`, `LEADERBOARD_PAGE_SIZES`) | **explicit stand-in**, own docstring: *"Packet W4-4 (the real store-backed API) has not landed yet."* W4-4 is now "received, review dispatched" (`021abc1`) — **retire this script once W4-4 lands**, before or as part of W4-1 |
| `render_methodology.py` | 367 | 15 | `cvi.audit.methodology_history` | logic-heavy (renders `docs/METHODOLOGY-v2.md`, fails on drift) |
| `make_sample.py` | 416 | 10 | `cvi.audit.coverage` (`COLLECTORS`, `GITHUB_KEYED_COLLECTORS`, `discover_repos`) | W0-1 one-off sampling tool |
| `gather.py` | 419 | 9 | `cvi.collect.commits`, `.envelope`, `.pike_signals`; `cvi.paths` | logic-heavy; **already the real v2 collector entry point** |
| `demo_app.py` | 483 | 7 | `cvi.api`, `cvi.benchmark_data`, `cvi.deepdive`, `cvi.engine`, `cvi.ingest.apply_raw_metrics`, `cvi.lakehouse`, `cvi.models` | **explicit fold target** ("folded into a test helper", `04` §2) |
| `build_fixture_snapshot.py` | 499 | 18 | `cvi.collect.commits`, `cvi.core.*`, `cvi.derive.bundle`, `cvi.snapshot.populations` | test/dev helper that builds `tests/fixtures/snapshot_50`; not one of the 6 named pipeline scripts; **stays a dev script under `scripts/`, not a shim target** |
| `promote.py` | 504 | 18 | `cvi.engine`, `cvi.deepdive`, `cvi.ingest.apply_raw_metrics`, `cvi.lakehouse`, `cvi.models`, `cvi.snapshot.manifest` | logic-heavy (the whole v1 score→upsert→hydrate→deep-dive loop is inlined here, not delegated) |
| `derive.py` | 652 | 18 | `cvi.collect.*`, `cvi.constants.BOT_LOGIN_MARKERS`, `cvi.models`, `cvi.provenance`, `cvi.paths` | **largest script**; writes the v1-shaped derived document (`meta`/`pr_events`/`provenance`/`confidence`) that `cvi/derive/bundle.py`'s `_load_v1()` then re-derives into a v2 `FeatureBundle` at load time — this file is the real, still-necessary bridge between v2 collectors and the v2 scoring engine, not legacy cruft (§7) |

Every `def`-count is ≥ 5; none is "argparse + one call into a library" —
**"scripts become thin shims calling `cvi.cli`" cannot be done as a rename.**
`cvi/cli.py` does not exist on `c19a93a` (confirmed: absent from the 85-file
`cvi/` listing; the only CLI-ish module is `cvi/snapshot/cli.py`, scoped to
freeze/reproduce). See §7/§8.

---

## 2. Dead-code list

### Tier A — confirmed dead, zero production callers, safe to delete in W4-1

Evidence: `grep -rn` over `cvi/ scripts/ tests/` for each symbol; zero hits
outside `cvi/ingest.py` itself and its two test files.

| Symbol | Location | Evidence |
|---|---|---|
| `TokenBucketRateLimiter` | `cvi/ingest.py:129-190` | `grep -rln "TokenBucketRateLimiter" cvi scripts tests` → `cvi/ingest.py`, `tests/test_ingest.py`, `tests/test_ingestion_integration.py` only |
| `ETagCache` | `cvi/ingest.py:191-212` | same grep set |
| `FetchResult` | `cvi/ingest.py:213-225` | same grep set (dataclass used only by `CachedHttpClient`) |
| `CircuitBreaker` | `cvi/ingest.py:226-284` | same grep set |
| `_parse_reset_epoch` | `cvi/ingest.py:285-298` | same grep set (helper for `CircuitBreaker`) |
| `CachedHttpClient` | `cvi/ingest.py:299-391` | same grep set |
| `gharchive_hourly_url` | `cvi/ingest.py:392-401` | same grep set |
| `_parse_iso8601` | `cvi/ingest.py:402-419` | same grep set |
| `map_pull_request_event` | `cvi/ingest.py:420-506` | same grep set |
| `GHArchiveStreamer` | `cvi/ingest.py:507-562` | same grep set |
| `_enum_value` | `cvi/ingest.py:563-567` | tiny helper used only by `apply_raw_metrics` below — moves with it, not deleted |
| `ingest_and_score` | `cvi/ingest.py:615-669` | `grep -rn "ingest_and_score" cvi scripts tests` → `cvi/ingest.py` (def) + `tests/test_ingest.py` (3 calls) + `tests/test_ingestion_integration.py` (1 call); **`scripts/promote.py` does NOT call it** — it inlines the identical score→upsert→hydrate sequence itself at lines 391-401, so this orchestrator has never had a production caller |
| `cvi/benchmark.py` (whole file, 21 lines) | — | `04` §2's own deleted list; already just a re-export shim (§1.4); 8 importers, all `tests/` |

Everything in this tier matches `04-ARCHITECTURE.md` §1's claim ("a second,
dead ingestion stack lives in `cvi/ingest.py`, never run") for the GHArchive
half of the file. **The claim is not quite right for the whole file**:
`apply_raw_metrics` (line 568) and, transitively, `ingest_and_score`'s
non-caller status needed checking rather than assuming — `apply_raw_metrics`
turned out to be very much alive (Tier C).

### Tier B — landed v2 code, NOT dead, do not delete in W4-1

Reachability BFS from every `scripts/*.py` file's direct `cvi.*` imports plus
`cvi.snapshot.cli`/`cvi.snapshot.__main__` as roots (script `python3
reachability.py`, kept in scratch) marks these unreached:

```
cvi.audit.detectors_report, cvi.audit.forward, cvi.audit.ties
cvi.store, cvi.store.migrations, cvi.store.queries, cvi.store.reader,
cvi.store.schema, cvi.store.writer
cvi.verify.live_sample, cvi.verify.pipeline_hook
```

Every one of these is fully covered by its own test file (§1.3) and is
unreached **only** because the script/CLI wiring that would call it hasn't
landed yet — `cvi/store/*` waits on W4-6 (`python -m cvi.snapshot store`,
dispatched same-day per the lead log at `c19a93a`'s parent), and the three
`cvi/audit/*` reporting modules wait on a `cvi.cli audit` subcommand that
`04` §2 names but that doesn't exist. **Do not delete or move these in
W4-1** — they are exactly on the target path already; the gap is
integration, tracked by other packets.

### Tier C — alive, not obviously so; would have been miscategorized as dead

- `cvi.ingest.apply_raw_metrics` (line 568-614): called by `scripts/promote.py:401`
  and `scripts/demo_app.py:407`. Confirmed live by `grep -n "apply_raw_metrics"
  scripts/promote.py scripts/demo_app.py` → both show the call, not just the
  import. **This is why `cvi/ingest.py` cannot be a straight delete** — see §4.

### ruff F401 result (supporting evidence, not a source of new findings here)

```
$ .venv/bin/ruff check --select F401 cvi/ scripts/ tests/
All checks passed!
```

No unused-import-level cruft anywhere; every dead-code finding above is a
whole unreferenced (or entirely internal) module/function, which F401 cannot
see by design (it only flags an import statement whose bound name is never
used in the importing file).

---

## 3. Shim plan

**External-consumer check first:** `git remote -v` → only
`github.com/pctablet505/contributor-value-index` (personal), local `main` is
currently well ahead of `origin/main` (198+ commits at session start),
`pyproject.toml` has no `[project.scripts]` entry points and the package has
never been published (version `0.1.0`, no PyPI listing evidenced). **Nothing
outside this repository can import any `cvi.*` path.** Every internal
`from cvi.X import Y` caller is inside `cvi/`, `scripts/`, or `tests/` — all
three are rewritten atomically in the same commit that performs a move
(§8), so **no per-symbol re-export shim is required for correctness inside
this repo either.** The only place a shim earns its keep is where *muscle
memory / docs / cron* invoke something **by path** rather than by import —
i.e. `scripts/*.py` as CLI entry points, which is exactly what `04` §2 asks
for ("scripts: thin shims … kept one release for compatibility").

| Moved/deleted target | Shim needed? | Why / what it looks like |
|---|---|---|
| `cvi/benchmark.py` → deleted | **No** | 0 non-test importers of the file itself; its 8 test importers get rewritten to `from tests.fixtures.synthetic_catalog import ...` in the same commit (`benchmark.py` was already a shim over `benchmark_data`, so deleting it removes a shim, it doesn't create the need for one) |
| `cvi/benchmark_data.py` → `tests/fixtures/synthetic_catalog.py` | **No module-level shim inside `cvi/`** (the whole point is it becomes "never importable from cvi/", `04` §2) — **but** `scripts/serve.py` imports `BENCHMARK_NOW` from it today, and `cvi/serve/app.py` (W4-4, in flight) will too. This is not a "keep the old import path alive" shim — it's a live cross-boundary dependency that must be resolved before the move lands. See §5 for the recommended fix (a frozen sentinel constant duplicated into the production guard, not a re-export). |
| `cvi/ingest.py` limiter/ETag/breaker → `cvi/collect/ratelimit.py` | **No** | 0 non-test importers today (Tier A); the 2 test files (`tests/test_ingest.py`'s relevant classes, `tests/test_ingestion_integration.py` if it touches these) move with the code in the same commit |
| `cvi/ingest.py` GHArchive stack (streamer, event mapper, url helper) | **N/A — delete, no destination** | Tier A, no relocation target named anywhere in `04`; carries no shim because nothing outside `cvi/ingest.py`'s own tests ever referenced it |
| `cvi/ingest.py` `apply_raw_metrics`/`ingest_and_score` → recommended: `cvi/lakehouse.py` | **Yes, one-line, until `scripts/promote.py`/`scripts/demo_app.py` are rewritten to call the new location directly** — `cvi/ingest.py` can carry `from cvi.lakehouse import apply_raw_metrics  # noqa: F401` behind a `warnings.warn(..., DeprecationWarning)` for exactly as long as `promote.py`/`demo_app.py` still say `from cvi.ingest import apply_raw_metrics`; since both call sites are rewritten in the SAME commit that moves the function (§8), this shim can be skipped entirely too — the "1 release" grace period in `04` §2 is for `scripts/*.py` paths (external muscle memory), not for `cvi.*` symbols with zero external readers |
| `scripts/demo_app.py` → test helper | **Yes — this is the one shim with real value.** Anyone running `.venv/bin/python scripts/demo_app.py` by habit (it's in the root `README.md`'s own "Demo" section, `.venv/bin/python scripts/demo_app.py`) gets nothing if the file just vanishes. Keep `scripts/demo_app.py` as a **thin wrapper** that imports the relocated test helper and runs it, printing a `DeprecationWarning` to stderr, for one release, then delete |
| `scripts/*.py` pipeline scripts (`gather`, `derive`, `verify`, `promote`, `run_all`, `serve`, `build_universe`, `backfill_downloads`, `render_methodology`, `make_sample`) → thin shims over `cvi.cli` | **Not applicable yet — `cvi/cli.py` does not exist.** There is nothing to shim *to*. This is the packet's largest real risk; see §7/§8. |

**Shim test pattern (no precedent exists in this codebase — `grep -rn
"DeprecationWarning\|warnings.warn" cvi scripts tests` → zero hits
anywhere).** For the one shim that is worth writing
(`scripts/demo_app.py`), the acceptance test is:

```python
def test_demo_app_shim_warns_and_still_runs():
    with pytest.warns(DeprecationWarning, match="moved to tests/"):
        import scripts.demo_app  # or however the shim is invoked
    # then assert the underlying behaviour (app still boots / seeds) is intact
```

---

## 4. `cvi/ingest.py` split

File is 669 lines. Exact class/def line ranges (`awk '/^class |^def /{print
NR": "$0}' cvi/ingest.py`):

| Component | Lines | Current tests (all in `tests/test_ingest.py` unless noted) | Fate |
|---|---:|---|---|
| **Rate limiter**: `TokenBucketRateLimiter` | 129-190 | `TestTokenBucketRateLimiter` (99-163) | → `cvi/collect/ratelimit.py` |
| **ETag cache**: `ETagCache` | 191-212 | `test_conditional_etag_fetch_304_consumes_zero_quota` (210-234), `test_etag_cache_follows_server_side_invalidations` (235-268), `test_etag_cache_etag_lookup_and_len` (305-313), `test_cached_http_client_properties_and_304_without_etag` (314-326) | → `cvi/collect/ratelimit.py` |
| **Circuit breaker**: `CircuitBreaker`, `_parse_reset_epoch` | 226-298 | `TestCircuitBreaker` (164-209), `test_circuit_breaker_blocks_until_ratelimit_reset_then_succeeds` (269-304), `test_parse_reset_epoch_various_headers` (327-339), `test_cached_http_client_429_with_retry_after_and_invalid_reset_headers` (340-398) | → `cvi/collect/ratelimit.py` |
| `FetchResult`, `CachedHttpClient` | 213-225, 299-391 | folded into the circuit-breaker/ETag tests above (`CachedHttpClient` is the orchestrator wiring the three primitives together) | → `cvi/collect/ratelimit.py` alongside the three primitives it wires |
| GHArchive stack (`gharchive_hourly_url`, `_parse_iso8601`, `map_pull_request_event`, `GHArchiveStreamer`) | 392-562 | `test_map_pull_request_event_*` (399-500), `test_parse_iso8601_variants` (453-467), `test_gharchive_hourly_url_formatting` (501-511), `test_stream_pr_events_parses_gzipped_hourly_partition` (512-552), `test_fetch_partition_raises_on_http_error` (553-562), `test_fetch_partition_uncompressed_and_cached_304` (563-592), `test_stream_hourly_ndjson_blank_lines_and_non_dict_values` (593-633) | **delete**, no successor named in `04` |
| `_enum_value`, `apply_raw_metrics` | 563-614 | `test_apply_raw_metrics_hydrates_stars_for_matrix_queries`, `test_apply_raw_metrics_stores_enum_values_as_strings`, `test_apply_raw_metrics_noop_for_unknown_repo` (634-719) | → `cvi/lakehouse.py` (recommended — it already owns `OperationalStore`, which is `apply_raw_metrics`'s only argument type; keeps the live v1 pipeline's import one hop, not two) |
| `ingest_and_score` | 615-669 | `test_ingest_and_score_round_trip_min_stars`, `test_ingest_and_score_defaults_raw_metrics_from_meta`, `test_ingest_and_score_persists_stage0_gate_failure` (720-765) | **delete** — Tier A, zero production callers (§2) |

`IngestError`/`RateLimitedError` (107-128) are exception classes used across
several of the above; move with the rate-limiter/ETag/circuit-breaker group
since that's where every raise site lives.

**Target `cvi/collect/ratelimit.py` API** (per `04` §2: "token bucket, ETag
cache, circuit breaker (port from ingest.py, then delete it)"):

```python
class IngestError(Exception): ...
class RateLimitedError(IngestError): ...
class TokenBucketRateLimiter: ...     # unchanged from cvi/ingest.py:129-190
class ETagCache: ...                  # unchanged from cvi/ingest.py:191-212
@dataclass
class FetchResult: ...                # unchanged from cvi/ingest.py:213-225
class CircuitBreaker: ...             # unchanged from cvi/ingest.py:226-284
def _parse_reset_epoch(headers): ...  # unchanged
class CachedHttpClient: ...           # unchanged from cvi/ingest.py:299-391
```

No signature changes are implied by anything read for this inventory — this
is a pure file move of five classes + two helpers + their exceptions, with
the module docstring's GHArchive-specific paragraphs deleted (only the
limiter/cache/breaker/client design notes survive).

**Net effect on `cvi/ingest.py`:** after the split, the file has nothing left
(rate limiter/ETag/breaker moved out, GHArchive deleted, `apply_raw_metrics`/
`ingest_and_score` moved/deleted) — it is deleted in full, matching `04` §2's
"deleted: `cvi/ingest.py`". The measure ("lines deleted vs added, must be net
negative") is satisfied: 669 lines removed from `cvi/ingest.py`, roughly
~330 relocate into `cvi/collect/ratelimit.py` (limiter+cache+breaker+client+
exceptions, lines 107-391) and ~50 into `cvi/lakehouse.py`
(`apply_raw_metrics`, lines 563-614), leaving ~290 lines (the GHArchive stack
+ `ingest_and_score`) net-deleted from the repo, plus their ~360 lines of
now-obsolete tests (399-633, 720-765 of `tests/test_ingest.py`).

---

## 5. `cvi/benchmark_data.py` → `tests/fixtures/synthetic_catalog.py`

Who uses it today (`grep -rn "BENCHMARK_NOW" cvi scripts tests`):

```
cvi/benchmark.py:6,15            (re-export shim, deleted in this same packet)
cvi/benchmark_data.py:37,45,815  (definition: datetime(2026, 9, 11, 12, 0, 0); seed_benchmark_store default)
scripts/serve.py:25,74,92        (import + _FIXTURE_MARKER = BENCHMARK_NOW.isoformat())
tests/test_adversarial_tier5_hardening.py:703,707,740,745  (via cvi.benchmark re-export)
tests/test_no_clock.py:9,15,46,179   (BENCHMARK_NOW is in the no-wall-clock grep pattern; asserts cvi.playbook does NOT have this attribute — unaffected by the move)
tests/test_serve.py:39,185,487   (via cvi.benchmark_data directly)
```

**The load-bearing dependency, named as asked:** `scripts/serve.py`'s served-
DB fixture-row guard —

```python
from cvi.benchmark_data import BENCHMARK_NOW
_FIXTURE_MARKER = BENCHMARK_NOW.isoformat()

def _fixture_marker_rows(store) -> list[tuple[int, str, str]]:
    """... keyed on last_indexed_at == cvi.benchmark_data.BENCHMARK_NOW,
    a stamp only cvi/benchmark_data.py's seeded catalog ever writes and
    scripts/promote.py never does ..."""
```

This is round 1/2/3 of packet W0-4's anti-fixture guard (its own module
docstring narrates all three rounds and why two independent checks now run).
**On the in-flight W4-4 branch** (not yet on main; commit `d2ea48c`-lineage,
file read via `git show cafaf19:cvi/serve/app.py` from the read-only
`cvi-wt-W4-4` worktree, which is mid-rebase and must not be touched), the new
`cvi/serve/app.py` reuses this exact guard by **importing `open_or_exit` back
from `scripts.serve`** (`from scripts.serve import open_or_exit  # lazy`) —
i.e. today's production code already has `cvi/` reaching backward into
`scripts/`, which is the wrong direction for the target architecture and
will not survive `scripts/serve.py` being reduced to a thin shim (§7).

**Moving `cvi/benchmark_data.py` under `tests/fixtures/synthetic_catalog.py`
breaks this guard as written**, in two ways: (1) a *production* script
(`scripts/serve.py`, soon `cvi/serve/app.py`) would import from a *test*
package, which is architecturally backwards even though nothing technically
prevents it (both are on `PYTHONPATH=$PWD`); (2) `04` §2 says the fixture
module becomes "never importable from `cvi/`" — production code importing it
either way is exactly what that rule exists to prevent, even though the rule
as written only fires from inside `cvi/` (`scripts/` isn't `cvi/`).

**Recommendation:** don't re-export `BENCHMARK_NOW` across the boundary at
all. Freeze the guard's own sentinel value as a literal constant inside the
guard's new home (`cvi/serve/` — the guard belongs there once `scripts/
serve.py` is a shim, §7/§8), e.g. `_FIXTURE_CLOCK_SENTINEL =
datetime(2026, 9, 11, 12, 0, 0)`, and add one test that asserts
`tests.fixtures.synthetic_catalog.BENCHMARK_NOW ==
cvi.serve.<wherever>._FIXTURE_CLOCK_SENTINEL` byte-for-byte, so the two
values can never silently drift apart. This preserves the guard's actual
security property (a stamp only the fixture seeder ever writes) without a
production→test import in either direction.

---

## 6. v1 web/API deletion checklist (execute only after W4-4/W4-5b land and the lead verifies v2 on the real API)

**What mounts where today vs on the in-flight W4-4 branch:**

- **Main (`c19a93a`) today:** `scripts/serve.py` mounts `StaticFiles(directory=web/, html=True)` at `/` — i.e. **v1's `web/index.html`** is still what `/` serves in production; `web/v2/` (landed by W4-5a) is not yet reachable from any served app.
- **W4-4 branch (in flight, not landed):** `cvi/serve/app.py` mounts `web/v2/` at `/` (via `_SPAStaticFiles`) and, **only if a v1 `OperationalStore` guard-passes**, mounts the untouched v1 `cvi.api.create_app()` at `/legacy/`. If the guard finds no legitimate v1 store, nothing is mounted at `/legacy/` at all (no empty mount).

**What to delete once the lead has verified v2 end-to-end on the real API (post W4-4/W4-5b):**

| Item | Why it's v1-pinned |
|---|---|
| `web/index.html` (3,289 lines) | v1 single-file SPA; superseded by `web/v2/**` |
| `cvi/api.py` (353 lines) | v1 FastAPI app; superseded by `cvi/serve/app.py` |
| `tests/test_webapp.py` (661 lines) | own docstring: *"Tests for the CVI web front-end demo (web/index.html + scripts/demo_app.py)"* — pins v1 web + v1 demo app together |
| `tests/test_api.py` | pins v1 `cvi.api.create_app` endpoint contracts |
| `tests/test_serve.py` | pins v1 `scripts/serve.py` guard + mounting behaviour — **rewrite, don't delete outright**, since the served-DB guard concept (not its v1 implementation) must keep an equivalent test once it moves per §5 |
| `tests/test_deepdive.py` | pins v1 `cvi.deepdive` via `cvi.api` |
| `tests/test_ingestion_integration.py`, `tests/test_ingest.py` | pin the v1 ingest/score/lakehouse round trip; partially addressed already by §4 (delete the GHArchive/`ingest_and_score` sections; the `apply_raw_metrics` tests move with the function) |
| `tests/e2e/` (whole directory: `conftest.py`, `test_tier1_features.py`, `test_tier2_boundaries.py`) | `conftest.py`'s own docstring: *"Uses the 1,000-repository benchmark catalog in an in-memory OperationalStore… Mounts web/ at / to verify the offline single-page application"* — this is v1 opaque-box E2E from end to end |
| `tests/test_adversarial_m1.py`, `_m2.py`, `_m3.py`, `_static_m2.py`, `_leaderboard_m2.py`, `_comparator_m3.py`, `_tier5_hardening.py` | v1 mutation-battery suites against `cvi.api`/`cvi.lakehouse`/`cvi.engine`/`cvi.pillars.*` |
| `tests/benchmark_sla_runner.py`, `tests/test_perf_sla_stress.py` | v1 perf harness against `cvi.api`/`cvi.lakehouse` |
| `scripts/demo_app.py` | already scheduled to fold into a test helper in W4-1 itself (§1.5), independent of this later checklist |

**Not on this list — these test v1 *scoring*, not v1 *web/API*, and stay
alive until W5-2 alongside the modules they test** (`test_engine.py`,
`test_matrix.py`, `test_persona.py`, `test_playbook.py`,
`test_playbook_adversarial.py`, `test_stage0.py`, `test_stage0_persona.py`,
`test_starscout.py`, `test_openssf.py`, `test_criticality.py`,
`test_governance.py`, `test_responsiveness.py`, `test_rigor.py`,
`test_accessibility.py`, `test_normalize.py`, `test_ground_truth.py`,
`test_lakehouse.py`, `test_promote.py`, `test_run_all.py`,
`test_provenance.py`, `test_data_root.py`, `test_benchmark_data.py`) — do
not delete these under this checklist; they retire with the v1 engine at
W5-2, a separate decision.

---

## 7. Risks

**1. `cvi/serve/app.py` (W4-4, in flight) already imports backward into
`scripts.serve` for the served-DB guard.** `from scripts.serve import
open_or_exit  # lazy: scripts.serve imports this module at its own top level`
(read from `git show cafaf19:cvi/serve/app.py` — the branch is mid-rebase in
`~/Projects/cvi-wt-W4-4` and was not touched to get this). Once `scripts/
serve.py` is reduced to "a thin shim calling `cvi.cli`", there is nothing
left in `scripts.serve` for `cvi/serve/app.py` to import — the guard logic
(`open_or_exit`, `_fixture_marker_rows`, `_FIXTURE_MARKER`, the
`BENCHMARK_NOW` dependency from §5) must move **into** `cvi/serve/` first,
and `scripts/serve.py`'s shim must import it back from there (the correct
direction). This has to happen *before or as part of* turning `serve.py`
into a shim, and it changes W4-4's already-written code, which the packet
text doesn't currently account for (W4-4's "Owns" list is `cvi/serve/app.py`
and `tests/test_api.py`, not `scripts/serve.py`'s internals).

**2. `cvi/cli.py` does not exist.** `04` §2's target layout ends with `cli.py
cvi universe|gather|derive|verify|snapshot|score|promote|serve|audit|
reproduce`, and `05-WORK-PLAN.md`'s target for `scripts/` is "thin shims
calling `cvi.cli` (kept one release for compatibility, then removed)". Every
`scripts/*.py` file that would need to become such a shim currently holds
5-18 top-level functions and real control flow (§1.5) — there is nothing
"thin" to shim *to* until `cvi/cli.py` is built and each script's logic is
migrated into a `cvi.<domain>` module the CLI calls. That is a substantially
larger unit of work than "package move" and is not bounded by the packet's
own size class (L, ≤ 800 lines) if attempted for all ten pipeline scripts at
once. **Recommend descoping**: W4-1 moves/deletes the four explicitly named
files (`ingest.py`, `benchmark.py`, `benchmark_data.py`, `demo_app.py`) and
the two doomed dev-scaffold scripts (`serve_ui_fixtures.py` once W4-4 lands,
possibly `backfill_downloads.py` if the lead confirms it's a one-off), and
explicitly leaves "scripts → `cvi.cli` shims" as a separate, later packet
(or split it into one sub-packet per script) — attempting it inside W4-1
is the single biggest scope-blowup risk in the whole packet.

**3. `scripts/derive.py` is not legacy — it is the live bridge from v2
collectors to the v2 engine, and nothing named in `04` replaces it.** It
writes the v1-shaped derived document (`meta`/`pr_events`/`provenance`/
`confidence`) using v1 dataclasses (`cvi.models.RawPREvent`, `AIPosture`,
etc.) from raw envelopes fetched by v2 collectors (`cvi.collect.*`);
`cvi/derive/bundle.py`'s `load_bundle()` → `_load_v1()` then reads that same
v1-shaped document plus the raw envelopes directly and re-derives a v2
`FeatureBundle` via `INPUT_MAPPINGS` at load time. There is no "v2 derive
script" anywhere — `cvi/derive/bundle.py` is a library, not an entry point;
its only caller that reads real (non-fixture) data is `scripts/derive.py`'s
own output file. Any change that touches `scripts/derive.py`'s output shape
without also updating `_load_v1()`'s v1-document parsing (or vice versa)
breaks the only working path from raw data to a scoreable bundle. This is
the "hook-chain tail" the task description warns about — every collector
packet (W1-4, W1-5f, W1-6, W1-10, …) has appended a resolver row to
`INPUT_MAPPINGS` and/or a field to `scripts/derive.py`'s output; both files
are conflict magnets and neither is a move candidate in W4-1.

**4. `scripts/gather.py`'s hook-chain tail is the same shape.** Every new
collector (`W1-*`) adds an import + a call in `scripts/gather.py`'s main
loop; a W4-1 move that touches this file's imports (even mechanically) risks
colliding with whatever collector packet lands concurrently — main advanced
three times (`88ce3cc` → `d112aa2` → `021abc1` → `c19a93a`) during the
writing of this inventory alone, and W4-6 (dispatched same-day) is actively
building the missing store-write CLI that both `gather.py` and `derive.py`
feed. **W4-1 should run after the landing order the lead already
committed to** (`00-README.md`: "Landing order: W4-5a → W4-4 → W4-5b →
W4-1") and should rebase immediately before executing each commit in §8,
not just once at the start.

**5. Editable-install shadow.** `cvi` is installed editable from the **main
checkout**: `.venv/lib/python3.12/site-packages/__editable__.cvi-0.1.0.pth`
→ `__editable___cvi_0_1_0_finder.py` → `MAPPING = {'cvi':
'/home/pctablet505/Projects/contributor-value-index/cvi'}`. Any Python run
from a worktree (`cvi-wt-W4-1-...`) **without** `PYTHONPATH=$PWD` silently
imports the main checkout's `cvi/`, not the worktree's — a mid-move worktree
would appear to work while actually testing pre-move code. Every command in
the real W4-1 execution must set `PYTHONPATH=$PWD` explicitly (this
inventory's own read-only Python checks did).

**Smaller, evidence-backed items, roughly in the commit-sequence's own
order:**

- **`cvi.audit.sensitivity` imports `cvi.constants.PILLAR_WEIGHTS`** (v1)
  alongside `cvi.core.*` (v2) — not caught by `test_import_graph.py` (which
  only constrains `cvi.core`, `cvi.derive.bundle`, `cvi.store`). Confirm with
  the lead whether this is intentional (no v2 equivalent for that weight
  table yet) before any move touches `cvi/constants.py`.
- **`cvi/derive/` has no `__init__.py`** (implicit namespace package) while
  every other `cvi/` subpackage does; `pyproject.toml` lists `"cvi.derive"`
  in `packages=[...]` and the sdist job is green today, but this is a style
  outlier worth a one-line fix (add the file) in the same commit that
  touches `pyproject.toml`.
- **`cvi/store/migrations/__init__.py`'s `_split_statements`** (line 76) is
  explicitly flagged in the lead log as a "→ W4-1 hygiene" deferred item: it
  splits SQL on a literal `;` after stripping `--` comments, documented as
  "safe for this schema's DDL (no string literal in it contains a semicolon
  or `--`)" — a real but narrow landmine for any future migration file that
  violates that assumption. In scope for W4-1 per the lead's own deferral.
- **CI package-data for `cvi/store/migrations/*.sql`** (the task's "F-010"
  reference — W4-2 round-1 review finding, folded into round 2, landed):
  already satisfied on `c19a93a` — `pyproject.toml`'s
  `[tool.setuptools.package-data]` has `"cvi.store.migrations" = ["*.sql"]`,
  and `apply_migrations()` raises `RuntimeError` on an empty migrations
  directory rather than silently no-op-ing (`store/migrations/__init__.py`
  docstring, "F-010"). Nothing to fix here; the CI "package" job (`python -m
  build --sdist`) does not currently assert the built artifact *contains*
  the `.sql` files, only that the build succeeds — a cheap addition, not a
  blocker.
- **`pyproject.toml` `packages=[...]` needs a new entry (`cvi.serve`) once
  W4-4 lands** (it's absent from `c19a93a`'s list, correctly, since
  `cvi/serve/` only has `contract.py` + `__init__.py` there today, but
  `cvi/serve/app.py` is 735 lines on the W4-4 branch) — a one-line addition,
  sequenced after W4-4 lands, before or with W4-1's own pyproject edits.
- **Root `README.md`'s module map (lines ~60-82) is entirely v1**: every row
  names a v1 module (`cvi/models.py`, `cvi/constants.py`, `cvi/stage0.py`,
  `cvi/starscout.py`, `cvi/normalize.py`, `cvi/openssf.py`, `cvi/pillars/*`,
  `cvi/matrix.py`, `cvi/persona.py`, `cvi/engine.py`, `cvi/lakehouse.py`,
  `cvi/api.py`, `cvi/ingest.py`, `cvi/deepdive.py`, `cvi/benchmark_data.py`,
  `web/index.html`, `scripts/demo_app.py`), and the "API Reference"/"Demo"
  sections document the v1 API and `scripts/demo_app.py` end-to-end. The
  packet's "Owns: … README module map" undersells this — it is a systematic
  rewrite (new rows for `cvi/core/*`/`cvi/collect/*`/etc., a new API
  reference against `cvi/serve/app.py`'s endpoints, a new demo story), not a
  few line edits, and should be estimated as such.
- **`.agents/` handoff paths are stable and low-risk**: every prior packet
  wrote `.agents/<PACKET-ID>/handoff.md` (+ occasionally `review/`); this
  inventory follows the same convention at `.agents/W4-1/inventory.md`.
  No collision risk found (`.agents/W4-1/` did not exist before this
  worktree created it).

---

## 8. Proposed commit sequence for the real W4-1 (each step green on its own)

Given §7's findings, this sequence deliberately narrows to what's actually
in the packet's evidence-backed scope (excludes the "scripts → `cvi.cli`
shims" work per risk #2, which should be its own packet or explicit
sub-phase):

1. **Guard relocation (prerequisite, blocks step 4).** Move the served-DB
   guard (`open_or_exit`, `_fixture_marker_rows`, `_FIXTURE_MARKER`) from
   `scripts/serve.py` into `cvi/serve/` (coordinate with wherever W4-4 has
   landed `cvi/serve/app.py` by then — landing order already puts W4-4
   before W4-1). Freeze the fixture-clock sentinel as a literal in the new
   location per §5's recommendation, with the cross-check test. `scripts/
   serve.py` becomes a thin wrapper importing the guard back. Green: existing
   `test_serve.py` (rewritten to import from the new location) plus the new
   sentinel cross-check test pass; suite unchanged in count.
2. **`cvi/collect/ratelimit.py`** — move `TokenBucketRateLimiter`,
   `ETagCache`, `FetchResult`, `CircuitBreaker`, `_parse_reset_epoch`,
   `CachedHttpClient`, `IngestError`, `RateLimitedError` verbatim (§4); move
   their tests (`TestTokenBucketRateLimiter`, `TestCircuitBreaker`, the ETag/
   circuit-breaker/cached-client tests) into `tests/collect/
   test_ratelimit.py`. Green: new test file passes standalone; nothing else
   references these symbols yet (Tier A, §2), so no other file changes.
3. **`apply_raw_metrics`/`ingest_and_score` → `cvi/lakehouse.py`** — move
   `_enum_value`, `apply_raw_metrics` (§4); rewrite `scripts/promote.py`'s
   and `scripts/demo_app.py`'s imports to `from cvi.lakehouse import
   apply_raw_metrics` in the same commit (§3 — no shim needed, both callers
   rewritten atomically). Drop `ingest_and_score` and its 3 tests (Tier A).
   Green: `test_promote.py`, `test_ingest.py` (reduced), `tests/
   test_lakehouse.py` (with the 3 moved `apply_raw_metrics` tests) all pass.
4. **Delete `cvi/ingest.py` and the GHArchive test sections** — after steps
   2-3, the file is empty of live content; delete it and the corresponding
   ~360 lines of `tests/test_ingest.py` (GHArchive + `ingest_and_score`
   sections, §4). Green: `grep -rn "cvi.ingest\|cvi/ingest" cvi scripts
   tests` returns zero hits (the reader sweep the packet's acceptance
   criterion asks for); `ruff check cvi/ scripts/ tests/` stays clean.
5. **Delete `cvi/benchmark.py`** — rewrite its 8 test importers to import
   from `cvi.benchmark_data` directly (still legal — the move to
   `tests/fixtures/` happens in the next step, one boundary crossing at a
   time). Green: same 8 test files pass against the new import.
6. **Move `cvi/benchmark_data.py` → `tests/fixtures/synthetic_catalog.py`**
   — rewrite the same 8 test files' imports again (now to
   `tests.fixtures.synthetic_catalog`), plus `scripts/serve.py`
   (already carries only the frozen sentinel per step 1, not a live import —
   confirm it has nothing left to rewrite) and `scripts/demo_app.py`
   (step 7 folds this file anyway). Green: reader sweep for
   `cvi.benchmark_data`/`cvi/benchmark_data.py` returns zero hits outside
   `tests/fixtures/synthetic_catalog.py` and historical docs/comments.
7. **Fold `scripts/demo_app.py` into a test helper** (e.g.
   `tests/helpers/demo_app.py`) with a one-release `DeprecationWarning`
   shim left at `scripts/demo_app.py` per §3; retire `scripts/
   serve_ui_fixtures.py` outright (its own docstring says it's a W4-4
   stand-in, and W4-4 will have landed by the time W4-1 runs per the landing
   order). Green: `test_webapp.py` (still v1-pinned, §6 — not touched by
   this packet) passes against the relocated helper.
8. **CI / pyproject** — add the `cvi.derive` `__init__.py` (risk item, §7);
   add `cvi.serve` to `packages=[...]` if W4-4 landed it since step 1's
   coordination; add a CI assertion that the built sdist contains
   `cvi/store/migrations/*.sql` (cheap hardening, §7); confirm `ruff check
   cvi/ scripts/ tests/` (the CI lint ratchet) is still clean end to end.
   Green: `python -m build --sdist` + a manual `tar tzf dist/*.tar.gz | grep
   migrations` check; full CI job green.
9. **Docs** — rewrite the root `README.md` module map, API reference, and
   Demo section against the actually-current tree (§7 — budget this as a
   real rewrite, not a table edit); update `docs/v2/04-ARCHITECTURE.md` §2
   for the `collect/github_rest.py` naming gap (§1.2) if the lead agrees to
   amend the doc rather than the code; add the W4-1 lead-log row to
   `docs/v2/00-README.md`. Green: `scripts/render_methodology.py`'s
   drift-detection test (unaffected, different doc) plus a human read.
10. **Fix the `_split_statements` hygiene item** (§7) in `cvi/store/
    migrations/__init__.py` — this was explicitly deferred to W4-1 by the
    lead log; do it last since it touches store internals that have their
    own extensive migration tests, and any regression here should be
    isolated from the file-move steps above. Green: existing migration
    tests plus one new adversarial fixture (a DDL statement containing a
    semicolon inside a string literal, to prove the fix rather than just
    re-assert the documented assumption).

Each step above is scoped to be independently revertable and each names its
own green condition, per the task's "each step green on its own"
requirement. Steps 1-7 together are the actual "package move and dead-code
deletion" the packet is named for; step 2 of risk item #2 (scripts → `cvi.cli`
shims for the ten pipeline scripts) is recommended to be a separate,
follow-on packet rather than folded into this sequence.
