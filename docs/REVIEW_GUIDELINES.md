# Porchlamp Review & Verification Guidelines

Adopted from AlgoTrading's bedrock docs (`SELF_REVIEW.md`, `VERIFICATION.md`,
`CODE_REVIEW.md`, `TESTING.md`) on 2026-09-12, adapted for this repo.
These rules bind every change here, human or agent.

## 1. Definition of done (VERIFICATION)

- [ ] Suite run with the ONE canonical invocation, exit code read directly
      (never through a pipe):
      `env -u PYTEST_ADDOPTS .venv/bin/python -m pytest tests/ -n auto -p no:cacheprovider`
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
  live in `porchlamp/constants.py` only).

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
