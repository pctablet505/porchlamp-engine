# Item 11 (coordinator addendum) — the backwards `scripts.serve` import

`cvi/serve/app.py::_run_served_db_guard` does
`from scripts.serve import open_or_exit` — a package importing a script, via
a `scripts` **namespace** package (`scripts/__init__.py` does not exist).

Severity by the import-graph test: **`tests/core/test_import_graph.py` has no
rule for `cvi/serve` at all.** It covers `cvi/core`
(`test_core_imports_nothing_outside_core_and_stdlib`), `cvi/derive/bundle`
and `cvi/store` (`CORE_DIR`, `STORE_DIR`) only. Nothing polices this edge, so
the violation is invisible to CI → Warning, plus "add the edge rule".

Does it break the deployed layout? Tested `python scripts/serve.py` from
`/tmp` (foreign cwd): the import resolves fine — it reaches `main()` and fails
later for an unrelated reason (Blocker B-1). So **no**, not under the current
editable install, which puts the repo root on `sys.path`.

It does break a non-editable layout: with the repo root removed from
`sys.path` (a wheel install, a packaged app),
`create_app(...)` → `ModuleNotFoundError: No module named 'scripts'`.

Recommendation (matches the W4-1 ruling): move the guard's pure logic —
`refusal_reasons`, `matching_snapshot_id`, the `BENCHMARK_NOW` fixture-clock
check — into `cvi/serve/guard.py` (or `cvi/lakehouse`), have `scripts/serve.py`
and `create_app` both call it, and add a `cvi/serve` edge rule to
`tests/core/test_import_graph.py` (allowed: stdlib, `cvi.*`, `fastapi`/
`starlette`/`pydantic`; forbidden: `scripts.*`). Fold the v2 guard (B-4) into
the same module while it is being written.
