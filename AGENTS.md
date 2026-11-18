# Porchlamp Engine — Agent & Contributor Card

Self-contained working guide for automated coding and review agents operating on this public repository. Full verification, provenance, UI/UX, and review gates live in [docs/REVIEW_GUIDELINES.md](docs/REVIEW_GUIDELINES.md).

## Key Commands

*(Name your interpreter explicitly — `.venv/bin/python` / `.venv/bin/ruff` inside a local virtualenv, or `python3` in an environment where project dependencies are installed.)*

- **Lint ratchet (must stay clean):**
  ```bash
  .venv/bin/ruff check porchlamp/ phone/ scripts/ tests/
  ```
- **Python test suite (always cap xdist workers at `<= 16`; never run uncapped `-n auto`):**
  ```bash
  env -u PYTEST_ADDOPTS .venv/bin/python -m pytest tests/ -n auto --maxprocesses=16 -p no:cacheprovider
  ```
- **Frontend Node unit & render performance suite (all 6 `.test.mjs` suites):**
  ```bash
  node --test web/app/tests/*.test.mjs
  ```

## Non-Negotiable Invariants

- **Data provenance envelopes:** Raw collector payloads live under `data/raw/<collector>/<owner>__<name>.json` (stored in provenance as data-dir-relative `raw/<collector>/<key>.json` via `porchlamp.collect.envelope`; collector-specific variants are `raw/downloads/<ecosystem>__<name>.json` — see `ECOSYSTEM_KEYED_FIELDS` in `scripts/verify.py` and `attributed_downloads()` in `scripts/derive.py` — and `raw/bodies/<owner>__<name>__<fname>.json` — see `_body_cache_path()` in `scripts/derive.py`). A field may claim `verified` confidence only when its envelope belongs to that repository and collector and its `sha256` matches the hash recorded at derive time. Never present `derived` fields as `verified`.
- **DOM-first UI & accessibility checks:** Before altering `web/app/`, `web/prerender.mjs`, or `porchlamp/serve/`, audit semantic HTML, ARIA attributes, keyboard dismissal, `inert` landmarks, and wrapping CSS rules per [docs/UI_UX_SPECIFICATION.md](docs/UI_UX_SPECIFICATION.md#55-enforced-uiux-accessibility--cross-page-parity-invariants) §5.5. When running live UI verification, inspect rendered DOM bounding boxes, contrast, and accessibility snapshots programmatically before taking final screenshots.
- **No weakening of performance budgets:** In CI, `PORCHLAMP_PERF_HOST_FACTOR="2"` (or finite numeric `1 <= factor <= 2`) is permitted **strictly on the `Run test suite` (`pytest tests/`) step** inside `.github/workflows/ci.yml` (never at workflow/job `env:` level, never on CI's standalone `node --test` step or other steps, never `< 1`, `> 2`, non-finite `NaN`/`inf`, boolean `true`/`false`, or non-numeric, and never committed to local/default test configs). On a slower local machine, contributors may pass `PORCHLAMP_PERF_HOST_FACTOR=2` (finite `1 <= factor <= 2`) transiently on the CLI for `pytest` only when explicitly disclosed under `COULD NOT VERIFY` (matching [CONTRIBUTING.md](CONTRIBUTING.md); never on standalone `node --test`). Without the override, local runs enforce the real budgets: `48 ms` JS render (`web/app/tests/render.perf.test.mjs`), `100 ms` SQLite query p95 (`200 ms` under `PYTEST_XDIST_WORKER`, `tests/store/test_queries.py`), and `150 ms` FastAPI endpoint p95 (`300 ms` under `PYTEST_XDIST_WORKER`, `tests/serve/test_api.py`).
- **Workflow security:** Pin all GitHub Actions in `.github/workflows/*.y*ml` to exact 40-character lowercase commit SHAs with version comments (`# v...`), minimal `permissions` (`permissions: {}` or `contents: read` at top level and never escalated beyond `contents: read` at job level), and `persist-credentials: false` strictly on `actions/checkout` steps (enforced across all `WORKFLOWS` by `tests/test_ci_workflow.py`).
- **Two-pass author-blind review:** See [§4 Review pipeline](docs/REVIEW_GUIDELINES.md#4-review-pipeline-code_review-adapted) and [Negative-Rule Ledger](docs/REVIEW_GUIDELINES.md#negative-rule-ledger) in [docs/REVIEW_GUIDELINES.md](docs/REVIEW_GUIDELINES.md).
- **Commit identity (maintainer / automated-agent commits):** Maintainer and maintainer-operated automated-agent commits use sole author/committer `pctablet505 <55033230+pctablet505@users.noreply.github.com>`, explicit staged file paths only, and zero `Co-Authored-By:` or AI attribution trailers; external contributors (and agents acting on their behalf) retain their own Apache 2.0 authorship attribution.
