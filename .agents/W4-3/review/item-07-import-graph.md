# Item 7 — Import-graph widening (L3) — PASS

The one-line widening is `or name.startswith("porchlamp.store")` in
`tests/core/test_import_graph.py::test_store_imports_only_core_snapshot_or_stdlib`;
the allow-list is now {stdlib, porchlamp.core*, porchlamp.snapshot*, porchlamp.store*} -- it does
not relax what `porchlamp/store` may reach outside itself.

Plant on the detached copy (`cvi-scratch/W4-3-review/tree`, 63772a4):
`from porchlamp.collect import x` in `porchlamp/store/queries.py` ->

    FAILED tests/core/test_import_graph.py::test_store_imports_only_core_snapshot_or_stdlib
    AssertionError: porchlamp/store/queries.py imports 'porchlamp.collect' outside
    porchlamp.core/porchlamp.snapshot/porchlamp.store + stdlib
    1 failed, 3 passed

Restored from `git show 63772a4:porchlamp/store/queries.py`; md5 identical to the
branch file; `git status --porcelain` empty.
