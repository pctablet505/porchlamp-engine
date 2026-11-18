# Porchlamp Engine — Agent & Contributor Card

Self-contained working guide for automated coding and review agents operating on this public repository. Full verification, provenance, UI/UX, and review gates live in [docs/REVIEW_GUIDELINES.md](docs/REVIEW_GUIDELINES.md).

## Key Commands

- **Lint ratchet (must stay clean):**
  ```bash
  ruff check porchlamp/ phone/ scripts/ tests/
  ```
- **Python test suite (always cap xdist workers at `<= 16`; never run uncapped `-n auto`):**
  ```bash
  env -u PYTEST_ADDOPTS python -m pytest tests/ -n auto --maxprocesses=16 -p no:cacheprovider
  ```
- **Frontend Node unit & render performance suite:**
  ```bash
  node --test web/app/tests/unit.test.mjs web/app/tests/render.perf.test.mjs
  ```

## Non-Negotiable Invariants

- **Data provenance envelopes:** Raw collector payloads live under `data/raw/<collector>/<owner>__<name>.json`. A field may claim `verified` confidence only when its envelope belongs to that repository and collector and its `sha256` matches the hash recorded at derive time. Never present `derived` fields as `verified`.
- **DOM-first UI & accessibility checks:** Before altering `web/app/`, `web/prerender.mjs`, or `porchlamp/serve/`, audit semantic HTML, ARIA attributes, keyboard dismissal, `inert` landmarks, and wrapping CSS rules per [docs/UI_UX_SPECIFICATION.md](docs/UI_UX_SPECIFICATION.md#55-enforced-uiux-accessibility--cross-page-parity-invariants) §5.5. When running live UI verification, inspect rendered DOM bounding boxes, contrast, and accessibility snapshots programmatically before taking final screenshots.
- **No weakening of local performance budgets:** `PORCHLAMP_PERF_HOST_FACTOR="2"` is allowed only inside `.github/workflows/ci.yml` for GitHub-hosted runner jitter; local runs enforce the real budgets.
- **Workflow security:** Pin all GitHub Actions in `.github/workflows/*.yml` to exact 40-character commit SHAs with minimal `permissions` (`contents: read` at top level unless deploying) and `persist-credentials: false` on `actions/checkout`.
- **Two-pass author-blind review:** See `## 4. Review pipeline` and `### Negative-Rule Ledger` in [docs/REVIEW_GUIDELINES.md](docs/REVIEW_GUIDELINES.md).
- **Commit identity:** Sole author/committer `pctablet505 <55033230+pctablet505@users.noreply.github.com>`. Stage explicit file paths only and include zero `Co-Authored-By:` or AI attribution trailers.
