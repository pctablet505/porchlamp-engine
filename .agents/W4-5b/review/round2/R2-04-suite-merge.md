# R2-04 Suite and merge trial

Branch suite at `adfec73` (clean worktree):
`env -u PYTEST_ADDOPTS PYTHONPATH=$PWD /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -B -m pytest tests -p no:cacheprovider -n 4`
→ **2521 passed, 2 skipped, 12 warnings in 13.70s** — claim exact.
Both skips are pre-existing and explained, neither belongs to this packet:
`tests/snapshot/test_reproduce_fixture.py:110` (run by hand once per packet — import-time constant
mutation) and `tests/snapshot/test_store_cli.py:290` (waiting on W4-4's `porchlamp/serve/app.py`).

Merge trial onto the current main tip **`aa219f5`** (past `916de17`; the lead's own W4-5b
browser-verification log row landed since the dispatch):
```
git worktree add --detach <scratch>/merge2 main
git cherry-pick <the 12 commits, in order>      # clean, no conflicts
env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest tests -p no:cacheprovider -n 4
-> 2526 passed, 2 skipped, 12 warnings in 15.23s
```
