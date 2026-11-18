# Porchlamp Review & Verification Guidelines

Self-contained review and verification guidelines for this repository.
These rules bind every change here, human or agent.

## 1. Definition of done (VERIFICATION)

- [ ] Suite run with the ONE canonical invocation, exit code read directly
      (never through a pipe), capping xdist workers at 16 (`-n 16` or
      `-n auto --maxprocesses=16`):
      `env -u PYTEST_ADDOPTS .venv/bin/python -m pytest tests/ -n auto --maxprocesses=16 -p no:cacheprovider`
- [ ] Full frontend Node UI suite green (all 6 `.test.mjs` suites):
      `node --test web/app/tests/*.test.mjs`
- [ ] Failure-set identity: same failing test IDs before/after a change;
      collected-count changes reconciled with a reason.
- [ ] Every behavioral change seen to fail BEFORE the fix (mutation proof:
      mutate → red → restore → verify restore; assert the mutation pattern
      exists first).
- [ ] Counts measured, never estimated (paste the output, not "about 350").
- [ ] Anything that could not be verified is written as `COULD NOT VERIFY: <what>`
      — "none" is not acceptable on a large diff.
- [ ] Env honesty: name the interpreter (`.venv/bin/python`), never assume a
      system `python`/`pytest`.

## 2. Test discipline (TESTING)

- One test file per source module; orphaned test files deleted.
- No tautological or mock-only tests; mocks only at external boundaries
  (network, clock, filesystem via tmp_path).
- Assert values, not shapes: `assert result.porchlamp == 88.8`, not
  `assert isinstance(result, PorchlampCompositeResult)`.
- A test that kills zero mutants is a Blocker (mutation battery in
  `tests/` must stay green under `scripts/` mutation probes when extended).
- Tests edited to pass must also change `porchlamp/` / `phone/` / `web/` (CA-1): a green suite after
  test-only edits to a failing test needs a root-cause note.

## 3. Data provenance (Porchlamp-specific, owner rule 2026-09-12)

- **Only verified data enters SQLite.** Raw fetches live in `data/raw/`
  (git-ignored); `scripts/derive.py` adds per-field provenance + confidence;
  `scripts/verify.py` cross-checks second sources and flags mismatches;
  only CLEAN bundles are promoted.
- Confidence vocabulary is closed: `verified | derived | missing | flag | collector_failure`.
  `derived` fields (e.g., `legal_ip_model` / v2 `legal_ip`, `ai_posture`, `promotion_ladder`,
  `*_evidence` companions, `elephant_factor` — primary `commits_365d` email-domain EF, or
  `contributors` login-proxy fallback only when `commits_365d` is absent — plus derived
  scored metrics emitted across `scripts/derive.py` and `porchlamp/collect/{governance,commits,pike_signals,github_graphql,repo_object}.py`)
  must never be presented as ground truth.
- No fabricated repos: every row in a universe/dataset must trace to a
  `candidates.jsonl` criterion or a fetched envelope. The retired synthetic
  `porchlamp/benchmark_data.py` module has been removed from the package (and
  `ci.yml` guards against it returning) — never reintroduce synthetic ranking sources.
- Provenance is unforgeable: a field may claim `verified` only with a data-dir-relative
  `raw/<collector>/<owner>__<name>.json` envelope path under `data/raw/` (or
  `raw/downloads/<ecosystem>__<name>.json` for ecosystem-keyed download fields per
  `ECOSYSTEM_KEYED_FIELDS` in `scripts/verify.py`; never a bare `http(s)://` URL)
  belonging to that field's own repository and collector, whose hash matches the sha256
  recorded in provenance at derive time (`verify` flags `unverifiable_provenance` on
  `verified` fields). At promote/serve time, `porchlamp/serve/guard.py` refuses
  synthetic `tests/fixtures/synthetic_catalog.py` clock-sentinel rows (`BENCHMARK_NOW`)
  and tampered content hashes, and promotion refuses snapshots missing a `snapshot_meta` row.
- Hedge words (`should`, `likely`, `appears`) are banned in evidence blocks.

## 4. Review pipeline (CODE_REVIEW, adapted)

- Gate 0: author self-review — read the whole enclosing function, not the
  hunk; absence inputs (`None/0/""/[]`) must not read as success.
- Gate 1: deterministic — `.venv/bin/ruff check porchlamp/ phone/ scripts/ tests/` clean; new public
  API fully typed with docstrings.
- Gate 2: **two-pass author-blind adversarial review** across 6 lenses: YAGNI, perf, API, compat, observability, layering. Severities: `Blocker:` `Warning:` `Consider:` `Nit:` `FYI:` `Praise:`.
  - **Pass 1 (Author-Blind Code & DOM Audit):** Start with a fresh reviewer carrying zero authoring history and **without** reading PR rationalization prose first. The reviewer inspects the diff and its callers (and HTML/JS/CSS DOM semantics for UI changes), emits a **2-sentence diff paraphrase** summarizing what the change does in plain English (a materially wrong paraphrase is a non-contestable `Blocker:`), reports grounded findings with exact `file:line` citations, and lists every unexecuted check under `COULD NOT VERIFY`.
  - **Pass 2 (Proof-Backed Verification):** Never dismiss a Pass-1 `Blocker:` or `Warning:` with prose alone. Every disputed finding resolves strictly to **`VERIFIED`** (must fix or escalate) or **`DISPUTED_WITH_PROOF`** backed by: (a) an executable counter-probe exercising the exact trigger input, (b) a caller-complete invariant citing every `file:line` call site in the repo, (c) a matching row from the **`<base>`** branch's `### Negative-Rule Ledger` below (never from self-edited PR lines), or (d) an exact verbatim documentation clause on non-executable `.md` prose.
- New symbols with zero readers are a Blocker (caller sweep before adding).
- Deletion > modification > addition. One definition per fact (active v2 constants
  live in `porchlamp/core/constants.py` — `porchlamp/constants.py` is legacy v1 only;
  user-facing UI copy lives in `web/app/copy.js` only and is referenced via `copy.<NAME>`).

### Negative-Rule Ledger

| Allowed Exact Shape (symbol/invariant) | Still-A-Bug Counter-Shape (never dismiss) |
| :--- | :--- |
| `PORCHLAMP_PERF_HOST_FACTOR: "2"` (or finite value `1 <= factor <= 2`) set **strictly on the `Run test suite` (`pytest tests/`) step** inside `.github/workflows/ci.yml` to accommodate GitHub-hosted runner jitter (or passed transiently on a slower contributor's CLI for `pytest` only when explicitly noted in `COULD NOT VERIFY`). | Setting `PORCHLAMP_PERF_HOST_FACTOR` at workflow/job `env:` level in `ci.yml`, setting it on CI's standalone `node --test` step or any non-primary step, inline shell overrides inside `run:`, non-positive/non-numeric values (`<= 0`, `NaN`, `inf`) or values `> 2`, committing it into local/default test configs, or loosening local performance budgets (`Blocker:`). |
| `derived` confidence fields clearly tagged `derived` across `scripts/derive.py` and `porchlamp/collect/{governance,commits,pike_signals,github_graphql,repo_object}.py` (e.g., `legal_ip_model` / v2 `legal_ip`, `ai_posture`, `promotion_ladder`, `*_evidence` companions, `elephant_factor` — primary `commits_365d` email-domain EF, or `contributors` login-proxy fallback only when `commits_365d` is absent — plus derived scored metrics). | Presenting `derived` fields as `verified` ground truth, bypassing `data/raw/` `sha256` verification on `verified` fields, or downgrading a `verified` field to `derived` to skip envelope verification (`Blocker:`). |

### 4.1 UI/UX, Accessibility (WCAG 2.1 AA) & Static Export Gates

Every change touching `web/app/`, `web/prerender.mjs`, or `porchlamp/serve/` must pass these review gates (see [UI_UX_SPECIFICATION.md](UI_UX_SPECIFICATION.md#55-enforced-uiux-accessibility--cross-page-parity-invariants) §5.5):

- **Semantic Table & Control Markup:** Never put `role="button"` or `tabindex="0"` on `<tr>` or `.leaderboard-card`; never override `<th role="columnheader">` with `role="button"`; never put `tabindex="0"` on non-interactive `.sparkbar`, `.tier-pill`, or `<td>` elements. Every `<input>` and `<select>` must carry both `id` and `name`. Interactive touch targets must be `>= 24x24px`.
- **Table Wrapping & Prerender CSS Invariants:** Never put `word-break: break-all | break-word` or `overflow-wrap: anywhere` on any CSS selector targeting `td`, `th`, `.cell-id`, `.cell-prose`, `.cell-meta`, or `.spec-table .num` (`tests/test_ui_table_wrapping.py`). Never add `.prerendered-board` rules in `web/app/styles/views.css` outside its two responsive `@media` blocks.
- **Navigation, Drawer & Modal Integrity:** Sub-tabs (`Ranked`, `Not ranked`) and primary nav links must never trap users on preset sub-views (`/fastest-replies/`, `/not-ranked/<reason>/<page>/`). An unranked (`gated`/`unrated`) repository's panel keeps its `/projects/<owner>/<name>/` address (answered by `404.html`) and shows no `Full page` link. The phone card list's CSS height plus margin equals `CARD_HEIGHT_PX`. When `.drawer` or `.keymap-overlay` is open, background landmarks (`header`, `nav`, `main`, `footer`, and `drawerRoot` when keymap is stacked over drawer) must be marked `inert`.
- **Cross-View Formatting & Parity:** `/compare/` must format raw parameter values identically to `/projects/<owner>/<name>/` (`formatParameterValue`), use `var(--text-success)` for `.compare-better-mark`, and implement WAI-ARIA combobox/listbox attributes with outside-click, `Escape`, and `focusout` dismissal. Standalone `/projects/<owner>/<name>/` pages and the drawer must stay in component and hook parity.

## 5. Auto-reject list

- Weakened/skipped assertion without a root-cause note.
- Unreconciled collected-count change in pytest output.
- Golden/expected values re-baselined without root cause.
- Force-push / `reset --hard` / bare `git stash` without owner confirmation.
- `print()` in library code (`porchlamp/`); scripts may print progress.
- Inline `TODO` (use the issue backlog).
- For maintainer / maintainer-operated automated-agent commits: commit email
  other than `55033230+pctablet505@users.noreply.github.com` or presence of
  `Co-Authored-By:` / AI attribution trailers (external contributors retain
  their own Apache 2.0 authorship attribution).

## 6. Commit convention

`<type>: <description>` with type ∈ {fix, feat, refactor, test, docs, chore, ci}.
Data-pipeline changes name the affected collectors and the verification
result (e.g. `feat(gather): crates monthly normalization — hashbrown CLEAN`).
