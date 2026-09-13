# I-08 Suite honesty + merge trial — PASS
Branch suite (tip 026bb32, worktree clean at the time of the run):
`env -u PYTEST_ADDOPTS PYTHONPATH=$PWD /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -B -m pytest tests -p no:cacheprovider -n 4`
-> **2299 passed, 1 skipped, 12 warnings in 15.33s** — the handoff's claim is exact.

Merge trial: `git worktree add --detach <scratch>/merge main` (main tip **819d361**, which is past the
packet's base acdf885 — W4-3, W1-5f and the lead logs landed since), then
`git cherry-pick 44b1d67 313c828 c99e324 026bb32` — **applied clean, no conflicts** (pyproject's
packages line merged without a textual conflict; `cvi.serve` is present in the merged list).
Full suite in the merge tree: **2390 passed, 1 skipped in 18.71s**.
A first run of the same tree reported `1 failed, 2389 passed` —
`tests/store/test_queries.py::test_performance_list_ranked_and_reproject_p95_under_100ms_for_10k_rows`
(reproject p95 101.73 ms then 114.50 ms vs a 100 ms budget) while my own probe/server load was on the
box. It passes alone on the untouched base 819d361 (4.04 s) and passes in the merge tree on a quiet
rerun: U-LOAD, a W4-3 store SLA test, not attributable to W4-5a (which adds no store code).
Ruff: `ruff check` over all seven touched Python files -> **All checks passed!**
