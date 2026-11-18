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

- **Data provenance envelopes:** Raw collector payloads live under `data/raw/<collector>/<owner>__<name>.json` (with collector-specific variants `data/raw/downloads/<ecosystem>__<name>.json` and `data/raw/bodies/<owner>__<name>__<fname>.json`; see `porchlamp.collect.envelope`). A field may claim `verified` confidence only when its envelope belongs to that repository and collector and its `sha256` matches the hash recorded at derive time. Never present `derived` fields as `verified`.
- **DOM-first UI & accessibility checks:** Before altering `web/app/`, `web/prerender.mjs`, or `porchlamp/serve/`, audit semantic HTML, ARIA attributes, keyboard dismissal, `inert` landmarks, and wrapping CSS rules per [docs/UI_UX_SPECIFICATION.md](docs/UI_UX_SPECIFICATION.md#55-enforced-uiux-accessibility--cross-page-parity-invariants) §5.5. When running live UI verification, inspect rendered DOM bounding boxes, contrast, and accessibility snapshots programmatically before taking final screenshots.
- **No weakening of performance budgets:** `PORCHLAMP_PERF_HOST_FACTOR="2"` is permitted **strictly on the `pytest` step** inside `.github/workflows/ci.yml` (never at workflow/job `env:` level, never on CI's standalone `node --test` step, never `> 2`, and never committed to local/default test configs); local runs and CI's standalone `node --test` step enforce the real `48 ms` budget.
- **Workflow security:** Pin all GitHub Actions in `.github/workflows/*.yml` to exact 40-character lowercase commit SHAs with minimal `permissions` (`permissions: {}` or `contents: read` at top level, scoped per job) and `persist-credentials: false` strictly on `actions/checkout` steps (enforced by `tests/test_ci_workflow.py`).
- **Two-pass author-blind review:** See [§4 Review pipeline](docs/REVIEW_GUIDELINES.md#4-review-pipeline-code_review-adapted) and [Negative-Rule Ledger](docs/REVIEW_GUIDELINES.md#negative-rule-ledger) in [docs/REVIEW_GUIDELINES.md](docs/REVIEW_GUIDELINES.md).
- **Commit identity (maintainer / automated-agent commits):** Automated agent and maintainer commits use sole author/committer `pctablet505 <55033230+pctablet505@users.noreply.github.com>`, explicit staged file paths only, and zero `Co-Authored-By:` or AI attribution trailers (external contributors retain their own Apache 2.0 authorship attribution).
