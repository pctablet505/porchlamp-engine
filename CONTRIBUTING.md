# Contributing to Porchlamp

Porchlamp ranks open-source projects by what happens to a stranger's pull
request. It should pass its own test, so every issue and pull request here gets
a reply from a person within 14 days.

One person maintains Porchlamp. Small, focused changes are the quickest to
review, and an issue before a large change saves you from writing it twice.

## Ways to help

- **A record looks wrong.** A bot counted as a person, a project that is not
  software, a project that reviews patches on a mailing list or Gerrit:
  [report a wrong record](https://github.com/pctablet505/porchlamp-engine/issues/new?template=wrong-record.yml).
  Each project page on [porchlamp.org](https://porchlamp.org/) ends with a link
  that opens this form with the project filled in.
- **Something is broken.** A page that does not load, two numbers that
  disagree, a command that fails:
  [report a bug](https://github.com/pctablet505/porchlamp-engine/issues/new?template=bug.yml).
- **The method could be better.**
  [Open an idea](https://github.com/pctablet505/porchlamp-engine/issues/new?template=idea.yml)
  and say what it would change. A change to a scored constant (a weight, a
  threshold, a curve) moves every score, so it goes through the
  [RFC procedure](docs/v2/rfcs/README.md).
- **You want to write code or docs.** Pick an
  [open issue](https://github.com/pctablet505/porchlamp-engine/issues) or fix
  something you found. Say in the issue that you are on it, so nobody does it
  twice.

## Set up

You need Git, Python 3.12 and Node.js 20 or newer, as CI uses.

```bash
git clone https://github.com/pctablet505/porchlamp-engine.git
cd porchlamp-engine
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]' ruff==0.16.7
```

To see the app with sample data, run
`.venv/bin/python scripts/serve_ui_fixtures.py --port 8899` and open
http://127.0.0.1:8899/ .

## Run the checks CI runs

```bash
.venv/bin/ruff check porchlamp/ phone/ scripts/ tests/
node --test web/app/tests/unit.test.mjs web/app/tests/render.perf.test.mjs
.venv/bin/python -m pytest tests/ -n auto
```

A few tests time queries and page renders against budgets measured on the
maintainer's machine. If they fail on a slower one, run pytest with
`PORCHLAMP_PERF_HOST_FACTOR=2` set, as CI does.

## What a pull request needs

- **One change**, with a title that says what changes for a reader of
  porchlamp.org or a user of the engine.
- **A test that fails without the change and passes with it.** Expected values
  are worked out by hand from the published formulas, never copied from the
  code's own output.
- **The checks above, passing.** GitHub runs CI on a pull request from a fork
  only after the maintainer approves the run, so the checks may wait for that
  reply.
- **Words in `web/app/copy.js`, numbers from the data.** The site's words live
  in that file, and a number on a page comes from the snapshot, never typed
  into the words.

The full checklist the maintainer reviews against is
[docs/REVIEW_GUIDELINES.md](docs/REVIEW_GUIDELINES.md). How the code fits
together: the [architecture table](README.md#architecture) and
[docs/README.md](docs/README.md).

## Licence

The code is under the [Apache License 2.0](LICENSE). Under its section 5, a
contribution you submit is licensed under the same terms, so there is no
separate agreement to sign. The data on porchlamp.org is published under
CC BY 4.0.

## Security

Please do not report a security problem in a public issue. See
[SECURITY.md](SECURITY.md).
