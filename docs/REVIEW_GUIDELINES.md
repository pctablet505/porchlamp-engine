# Porchlamp Review & Verification Guidelines

Adopted from AlgoTrading's bedrock docs (`SELF_REVIEW.md`, `VERIFICATION.md`,
`CODE_REVIEW.md`, `TESTING.md`) on 2026-09-12, adapted for this repo.
These rules bind every change here, human or agent.

## 1. Definition of done (VERIFICATION)

- [ ] Suite run with the ONE canonical invocation, exit code read directly
      (never through a pipe), capping xdist workers at 16 (`-n 16` or
      `-n auto --maxprocesses=16`):
      `env -u PYTEST_ADDOPTS .venv/bin/python -m pytest tests/ -n 16 -p no:cacheprovider`
- [ ] Node UI unit suite green:
      `node --test web/app/tests/unit.test.mjs`
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
- Tests edited to pass must also change `src/` (CA-1): a green suite after
  test-only edits to a failing test needs a root-cause note.

## 3. Data provenance (Porchlamp-specific, owner rule 2026-09-12)

- **Only verified data enters SQLite.** Raw fetches live in `data/raw/`
  (git-ignored); `scripts/derive.py` adds per-field provenance + confidence;
  `scripts/verify.py` cross-checks second sources and flags mismatches;
  only CLEAN bundles are promoted.
- Confidence vocabulary is closed: `verified | derived | missing | flag`.
  `derived` fields (legal_ip_model, ai_posture, promotion_ladder,
  elephant_factor org-proxy) must never be presented as ground truth.
- No fabricated repos: every row in a universe/dataset must trace to a
  `candidates.jsonl` criterion or a fetched envelope. The old synthetic
  `porchlamp/benchmark_data.py` is test fixture only — never a ranking source.
- Provenance is unforgeable: a field may claim `verified` only with a
  `data/raw/<collector>/<owner>__<name>.json` envelope path (never a bare
  `http(s)://` URL) belonging to that field's own repository and collector,
  whose hash matches the sha256 recorded in provenance at derive time
  (`verify` flags `unverifiable_provenance`); and `serve.py` refuses a
  database with `benchmark_data` fixture rows, no `snapshot_meta` row, or a
  recomputed content hash that no longer matches the one recorded there.
- Hedge words (`should`, `likely`, `appears`) are banned in evidence blocks.

## 4. Review pipeline (CODE_REVIEW, adapted)

- Gate 0: author self-review — read the whole enclosing function, not the
  hunk; absence inputs (`None/0/""/[]`) must not read as success.
- Gate 1: deterministic — `ruff check porchlamp scripts tests` clean; new public
  API fully typed with docstrings.
- Gate 2: adversarial pass over 6 lenses: YAGNI, perf, API, compat,
  observability, layering. Severities: `Blocker:` `Warning:` `Consider:`
  `Nit:` `FYI:` `Praise:`.
- New symbols with zero readers are a Blocker (caller sweep before adding).
- Deletion > modification > addition. One definition per fact (constants
  live in `porchlamp/constants.py` only; user-facing UI copy lives in
  `web/app/copy.js` only and is referenced via `copy.<NAME>`).

### 4.1 UI/UX, Accessibility (WCAG 2.1 AA) & Static Export Gates

Every change touching `web/app/`, `web/prerender.mjs`, or `porchlamp/serve/` must pass these review gates (see `docs/UI_UX_SPECIFICATION.md` §5.5):

- **Semantic Table & Control Markup:** Never put `role="button"` or `tabindex="0"` on `<tr>` or `.leaderboard-card`; never override `<th role="columnheader">` with `role="button"`; never put `tabindex="0"` on non-interactive `.sparkbar`, `.tier-pill`, or `<td>` elements. Every `<input>` and `<select>` must carry both `id` and `name`. Interactive touch targets must be `>= 24x24px`.
- **Table Wrapping & Prerender CSS Invariants:** Never put `word-break: break-all | break-word` or `overflow-wrap: anywhere` on any CSS selector targeting `td`, `th`, `.cell-id`, `.cell-prose`, `.cell-meta`, or `.spec-table .num` (`tests/test_ui_table_wrapping.py`). Never add `.prerendered-board` rules in `web/app/styles/views.css` outside its two responsive `@media` blocks.
- **Navigation, Drawer & Modal Integrity:** Sub-tabs (`Ranked`, `Not ranked`) and primary nav links must never trap users on preset sub-views (`/fastest-replies/`, `/not-ranked/<reason>/<page>/`). Opening the drawer for an unranked (`gated`/`unrated`) repository must never push a `/projects/<owner>/<name>/` 404 URL. When `.drawer` or `.keymap-overlay` is open, background landmarks (`header`, `nav`, `main`, `footer`, and `drawerRoot` when keymap is stacked over drawer) must be marked `inert`.
- **Cross-View Formatting & Parity:** `/compare/` must format raw parameter values identically to `/projects/<owner>/<name>/` (`formatParameterValue`), use `var(--text-success)` for `.compare-better-mark`, and implement WAI-ARIA combobox/listbox attributes with outside-click, `Escape`, and `focusout` dismissal. Standalone `/projects/<owner>/<name>/` pages and the drawer must stay in component and hook parity.

## 5. Auto-reject list

- Weakened/skipped assertion without a root-cause note.
- Unreconciled collected-count change in pytest output.
- Golden/expected values re-baselined without root cause.
- Force-push / `reset --hard` / bare `git stash` without owner confirmation.
- `print()` in library code (`porchlamp/`); scripts may print progress.
- Inline `TODO` (use the issue backlog).
- Commit email other than `55033230+pctablet505@users.noreply.github.com`
  (GH007 / employer rules).

## 6. Commit convention

`<type>: <description>` with type ∈ {fix, feat, refactor, test, docs, chore}.
Data-pipeline changes name the affected collectors and the verification
result (e.g. `feat(gather): crates monthly normalization — hashbrown CLEAN`).
