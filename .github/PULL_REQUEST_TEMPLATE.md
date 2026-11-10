## What changes

<!-- For a reader of porchlamp.org or a user of the engine, in a sentence or two. Link the issue it closes. -->

## How it is tested

<!-- The test that fails without this change and passes with it. -->

- [ ] `ruff check porchlamp/ phone/ scripts/ tests/` is clean
- [ ] `node --test web/app/tests/unit.test.mjs web/app/tests/render.perf.test.mjs` passes
- [ ] `python -m pytest tests/ -n auto` passes
- [ ] A change to a scored constant links its RFC ([procedure](https://github.com/pctablet505/porchlamp-engine/blob/main/docs/v2/rfcs/README.md))
