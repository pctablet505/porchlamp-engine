## What changes

<!-- For a reader of porchlamp.org or a user of the engine, in a sentence or two. Link the issue it closes. -->

## How it is tested

<!-- The test that fails without this change and passes with it. -->

- [ ] `.venv/bin/ruff check porchlamp/ phone/ scripts/ tests/` is clean
- [ ] `node --test web/app/tests/*.test.mjs` passes (all 6 suites)
- [ ] `env -u PYTEST_ADDOPTS .venv/bin/python -m pytest tests/ -n auto --maxprocesses=16 -p no:cacheprovider` passes (or `python3` in an activated venv)
- [ ] Fail-before or mutant-kill proof recorded for every behavioral change (or `N/A — pure docs/config` with reason)
- [ ] A change to a scored constant links its RFC ([procedure](https://github.com/pctablet505/porchlamp-engine/blob/main/docs/v2/rfcs/README.md))
