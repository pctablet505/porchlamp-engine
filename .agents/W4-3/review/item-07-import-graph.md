# Item 7 — Import-graph widening (L3) — PASS

The one-line widening is `or name.startswith("cvi.store")` in
`tests/core/test_import_graph.py::test_store_imports_only_core_snapshot_or_stdlib`;
the allow-list is now {stdlib, cvi.core*, cvi.snapshot*, cvi.store*} -- it does
not relax what `cvi/store` may reach outside itself.

Plant on the detached copy (`cvi-scratch/W4-3-review/tree`, 63772a4):
`from cvi.collect import x` in `cvi/store/queries.py` ->

    FAILED tests/core/test_import_graph.py::test_store_imports_only_core_snapshot_or_stdlib
    AssertionError: cvi/store/queries.py imports 'cvi.collect' outside
    cvi.core/cvi.snapshot/cvi.store + stdlib
    1 failed, 3 passed

Restored from `git show 63772a4:cvi/store/queries.py`; md5 identical to the
branch file; `git status --porcelain` empty.
