# Item 8 — suite honesty and merge trial — PASS

Branch (`cvi-wt-W4-4`, f6a398c):
```
env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest tests/ -n 4 -p no:cacheprovider
2502 passed, 1 skipped, 12 warnings in 17.85s
```
exactly the claim. (The handoff also contains a stale `2359` line from before
the second rebase; its own rebase note explains it.)

Merge trial onto the current main tip `9d364cc` (advanced past the named base
`d112aa2`; the lead log's W4-1-inventory row landed meanwhile):
```
main alone:  2468 passed, 1 skipped, 12 warnings in 13.40s
cherry-pick 75b9776 d45c278 9650cde b39e016 9237580 f6a398c   # all six, no conflict
merged:      2502 passed, 1 skipped, 12 warnings in 17.96s
ruff check cvi/ tests/ scripts/ -> All checks passed!
```
Worktree removed.
