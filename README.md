<img src="web/app/favicon.svg" width="72" height="72" alt="Porchlamp's symbol: a porch lamp with its light on">

# Porchlamp

**Find the projects that leave the light on.**

A porch lamp left on tells a visitor they are expected. Porchlamp ranks
open-source projects the same way: by what happens to a stranger's pull
request, not by how many stars the project has.

## Stars don't review pull requests

Measured on the 2026-10-12 snapshot of 11,000 projects:

- **More than a third of active projects are black holes.** Of the 4,375
  licensed, maintained software projects that received at least ten outside pull
  requests in six months, 1,622 (37.1%) left most of them with no human reply,
  review or merge for two weeks. Counting only pull requests opened by people,
  not bots, gives 56.7%.
- **More stars, more silence.** The black-hole share rises with popularity: 33%
  of projects with 1k–10k stars, 41% at 10k–50k, and 48% at 50k or more.
- **Stars barely predict the ranking.** Across the 3,321 ranked projects the
  rank correlation between stars and the Porchlamp score is 0.12, and 7 of the
  100 most-starred projects make the top 100.

## Where the numbers come from

We followed 212,390 pull requests from 110,605 outside contributors to 11,000
projects over six months: who got an answer, how fast, how many reached a
decision, whether first-time contributors came back, and how much other software
depends on each project. Black holes, and projects that are archived,
unmaintained, unlicensed or not software, are listed but never ranked. The rest
are scored on five pillars and re-weighted for five kinds of contributor. Every
number traces back to a recorded GitHub response and its content hash.

## How the ranking works

Every number comes from public GitHub activity plus deps.dev, ecosyste.ms and
OpenSSF data, recorded as content-hashed envelopes. A snapshot is built from
those envelopes at one fixed reference time (`--as-of`), so the same data and
code always produce the same ranking.

1. **Gates.** A project is listed but never ranked if it fails one:
   - not software (no build or package manifest);
   - archived, a mirror or a fork;
   - unlicensed (no OSI-approved SPDX licence, including compound expressions);
   - stale (no commit by a person in 180 days);
   - **black hole**: of at least 10 outside pull requests that are 14+ days old, more
     than half got no substantive human response within 14 days;
   - no outside pull request decided in a year.
2. **Parameters.** 18 parameters across five pillars, each scored 0-100 by a
   published curve or a within-ecosystem percentile:
   - Responsiveness 25%;
   - Criticality 25%;
   - Governance 20%;
   - Rigor 15%;
   - Accessibility 15%.

   A parameter with too little data is *unmeasured*, never a silent default.
3. **Coverage and tiers.**
   - Coverage is the weighted share of parameters actually measured.
   - Below 60% the project is listed as unrated.
   - The score sets the tier: S+ ≥ 90, S ≥ 80, A ≥ 70, B ≥ 55, C ≥ 40, D below.
   - Coverage caps it: under 80% coverage the tier can be at most B, and under 95%
     at most A. Such a tier shows as "(capped)".
4. **Matrix.**
   - Leverage (criticality, rigor, governance) and friction (responsiveness,
     governance, accessibility) place each project in a quadrant: Sweet Spot,
     Crucible, Nursery or Graveyard.
5. **Personas.** The composite is re-weighted for five lenses:
   - balanced;
   - first-timer;
   - career capital;
   - senior systems;
   - AI-assisted.

   Each has its own inclusion or exclusion gate.

The full method, with every curve and threshold, is published with each
snapshot (the site's Methodology tab, from `porchlamp.core.methodology`).
Design documents: [docs/README.md](docs/README.md), [docs/METHODOLOGY-v2.md](docs/METHODOLOGY-v2.md),
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), and [docs/v2/](docs/v2/00-README.md).

## Quickstart

```bash
cd porchlamp-engine

# venv: .venv — Python 3.12 with pytest + pytest-xdist, fastapi + httpx and
# uvicorn installed (pyproject extras: `dev` = test deps, `server` = uvicorn).
# Gotcha: some shells export PYTEST_ADDOPTS (here observed `-n 6`, previously
# `-n 6 -n 6`), which pollutes the explicit `-n auto` — always run pytest with
# it unset:
env -u PYTEST_ADDOPTS .venv/bin/python -m pytest tests/ -n auto -p no:cacheprovider
```

On 2026-09-23 the suite ran 2,774 passed, 1 skipped (a manual procedure) and
1 xfailed (a documented reproject limitation). CI runs on Python 3.12.

## Architecture

The pipeline, in order:

| Step | Command | Output |
|---|---|---|
| Collect | `scripts/gather.py` (collectors in `porchlamp/collect/`), or the Android collector in `phone/` pulled with `scripts/pull_from_phone.py` | one envelope per signal under `data/raw/` |
| Pick as_of | `python -m porchlamp.snapshot as-of --data-dir data` | the earliest `--as-of` every guard accepts (the newest pull-request record's time, rounded up to the minute; later only when a releases or issues file needs it), and PASS/FAIL per guard; exit 0, 1 on a fail, 2 on an error; `--print` writes only the value, `--as-of TIME` checks a given one |
| Derive | `scripts/derive.py OWNER NAME --ecosystem ECO` | one bundle per project under `data/derived/` |
| Build | `python -m porchlamp.snapshot build --data-dir data --out-dir SNAP --snapshot-id ID --as-of AS_OF` (AS_OF from the step above, never the wall clock) | score records plus a manifest with a Merkle root |
| Store | `python -m porchlamp.snapshot store SNAP --db porchlamp.db` | the SQLite store the API reads |
| Serve | `scripts/serve.py --db porchlamp.db` | the API under `/v1/porchlamp/` and the UI in `web/app/` |
| Publish | `scripts/export_static.py --db porchlamp.db --stats STATS --out SITE`, then `scripts/verify_static_export.py --db porchlamp.db --stats STATS --site SITE`, with `PORCHLAMP_DATA_ROOT` set to the snapshot's raw data (the report needs the black-hole evidence) | the static site, checked request by request against the API; with `--stats`, also the report (`black-holes/`), its frozen copy, the ranked CSV and the methodology page, one page per ranked project (`projects/<owner>/<name>/`, lower case), `check/` (search any project) and the project sitemap, and ten landing pages (`rust/`, `python/`, `javascript/`, `go/`, `java/`, `c-cpp/`, `first-timers/`, `career/`, `senior-systems/`, `ai-assisted/`), and the home page's findings line, top 20 and JSON-LD, `llms.txt` and `feed.xml`, generated from the snapshot's data, every number checked |

| Package | Role |
|---|---|
| `porchlamp/core/` | Pure scoring: gates, parameters (`params/`), normalisation, aggregation, tiers, matrix, personas, tie-breaks, the published methodology. Standard library only, no I/O, no clock; `tests/core/test_import_graph.py` enforces it. |
| `porchlamp/collect/` | Collectors: one network fetch per signal, each returning a content-hashed envelope; rate limiting and retries in `ratelimit.py`. |
| `phone/` | Android collector (`entry.py` is the app's entry point; `runner.py`, `control.py`, `httpgate.py`, `archive.py`, `serve.py`, `auth.py`): runs layers `L1..L4, LK` under battery/thermal/Wi-Fi guards, allow-lists outbound hosts, packs deterministic `.tar.gz` archives and serves them over the LAN with HMAC-signed requests (`X-Porch-Mac`, `auth.py`) to `scripts/pull_from_phone.py`. |
| `porchlamp/derive/` | Turns envelopes into typed bundles and gate evidence. |
| `porchlamp/snapshot/` | `as-of`, `build`, `store`, `freeze` and `reproduce`: picks and checks the snapshot's `as_of`, rebuilds a snapshot from its envelopes and checks it against the recorded hash. |
| `porchlamp/store/` | SQLite schema, forward-only migrations, writer and queries. |
| `porchlamp/serve/` | FastAPI app, wire contract, and the guard that refuses to serve an unverified database. |
| `porchlamp/audit/`, `porchlamp/verify/` | Read-only audits (coverage, anchors, sensitivity, ties, detectors) and a live re-verification canary. |
| `web/app/` | The UI: leaderboard, matrix, compare, configure weights, methodology. |
| `web/static/api.js` | The static site's stand-in for the API: answers every request from precomputed JSON. |

The v1 modules at the top of `porchlamp/` (`engine.py`, `lakehouse.py` and friends) are
no longer served: the first-generation UI and its API were removed on 2026-10-17.
`scripts/promote.py` still writes the v1 store, and the serve guard still uses
`porchlamp/lakehouse.py` for its dump hash.

## API

Base path `/v1/porchlamp`; every response names its snapshot.

| Method and path | Returns |
|---|---|
| `GET /snapshot` | snapshot id, as-of time, hashes, counts |
| `GET /leaderboard?persona=&eco=&q=&sort=&page=&page_size=` | ranked rows |
| `GET /not-ranked?persona=&eco=&q=` | listed projects with the gate or reason that keeps each unranked |
| `GET /matrix?persona=` | leverage/friction points, quadrant counts, axis coverage |
| `GET /repos/{owner}/{name}` | one project's full record |
| `GET /repos/{owner}/{name}/evidence` | the parameter values and the envelopes behind them |
| `GET /methodology` | the published method for this snapshot |
| `POST /reproject` | the ranking under custom pillar weights |

`GET /healthz` is the liveness probe.

## Testing

- **Oracle-first:** expected values are computed by hand from the published
  formulas, so a wrong formula fails instead of matching itself.
- **Every fix ships a test** that fails before the fix, and mutation checks
  confirm that the tests catch planted defects.
- **The static site is checked against the API**, request by request, by
  `scripts/verify_static_export.py`, including planted mutants of `web/static/api.js`.
- **CI** (`.github/workflows/ci.yml`) runs lint, the Node UI suite, the full
  pytest suite and an sdist install check on Python 3.12.

## License

This project is licensed under the Apache 2.0 License. See [LICENSE](LICENSE) for details.
