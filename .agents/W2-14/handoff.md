# W2-14 handoff — provenance sources are relative to the data root

Commit: `867d9873019d7b6bf7045e7fc9857e18ff5b6c82` (branch `W2-14`)
Base (after rebase): `main` @ `269aacf08ef5654d5ad765098341db8dedeaf66f`
Diffstat: `66 files changed, 833 insertions(+), 333 deletions(-)` (50 of those files are
`tests/fixtures/snapshot_50/bundles/*.json`, mechanically regenerated — see below).

## The bug and the fix

The real API served `source: "/home/pctablet505/Projects/cvi-scratch/sample-200/raw/gfi/torvalds__linux.json"`
— an absolute path of this machine — as evidence provenance. Root cause:
`cvi/derive/bundle.py::_resolve_envelope` re-roots a v1 document's recorded
source under `raw_root` for portability, but `raw_root` (always
`<data_root>/raw`, and `cvi.paths.data_root()` is always absolute) is itself
absolute, so the resolved `Path` stayed absolute all the way into
`Measured.source` / `ctx.referenced` / the two W1-5f sidecar facts.

One rule, several enforcement points (duplicated only where the import graph
forces it — `cvi.derive.bundle` may import only `cvi.core` + stdlib, `04`
section 3):

- **`cvi/derive/bundle.py::_relative_source(path, raw_root)`** (new): the
  single site every `Measured`/sidecar-fact/`ctx.referenced`/`referenced`
  construction now goes through instead of `str(path)`. Raises
  `BundleValidationError` when it cannot express the path relative to a
  known root — a stale absolute source can never enter a bundle silently.
  Applied at all ~20 call sites in the file (`_resolve_commit_facts`, every
  `_plain_scalar_resolver`/`_vocab_scalar_resolver` row, `dependents_deduped`,
  `dr90_decision_rate`, `ttfhr_hours`, `substantive_review_rate`,
  `ttm_p90_hours`, `openssf_criticality`(+proxy), `pipeline_defenses`, the
  author-history rate-field factory, `star_velocity_flag`, and `_load_v1`'s
  `github_meta`/`pr_events` sites).
- **`cvi/collect/envelope.py::relative_envelope_path(root, path)`** (new):
  the twin helper for producer-side code outside `cvi.derive.bundle` (the
  packet's literally-named location). Same arithmetic, documented as the
  intentional duplicate (same precedent as `canonical_json` existing once
  in each layer).
- **`cvi/core/models.py::is_relative_envelope_source`** (new) +
  `measured_problems` (extended): `Measured.__post_init__` now refuses to
  construct a `Measured` whose `source` is absolute, drive-lettered, or
  `..`-escaping — the single enforcement point every construction site
  (v1 resolution AND v2 document loading via `_param_from`) passes through
  unconditionally. This is what makes "derive refuses an absolute source in
  an input document" true regardless of how the value got there.
- **`cvi/serve/contract.py::EvidenceRow`**: a matching pydantic
  `model_validator`, with a self-contained predicate (the module is
  deliberately free of `cvi.core` imports, per its own docstring, so the
  check is duplicated rather than imported).
- **`cvi/store/writer.py::_param_row`**: an explicit `ValueError` raise as
  defense-in-depth (the store is the last stop before an absolute path
  would be served).

### Orthogonal bug found and fixed in the same file

Writing the "walk every real fixture repo's `/evidence`" test (below) hit a
real, pre-existing, unrelated crash: `reason="no_population"` is a **stage-2**
outcome (`cvi.core.models.Unmeasured`'s own docstring: the scoring engine
assigns it to a `ParamScore`, never to an input-stage value), but
`cvi/store/writer.py::_param_row` copied the **stage-1** `source`/
`source_hash`/`fetched_at` through regardless of the stage-2 `measured` flag.
Every real repo with a `no_population` parameter therefore had a `params` row
with `measured=0` and a non-null `source`/`fetched_at` — which fails
`EvidenceRow`'s own pre-existing validator ("measured=False but carries a
measured field") and made `GET /v1/cvi/repos/{o}/{n}/evidence` 500 for that
repo. Fixed by gating those three columns on `param_score.measured` (the
stage-2 flag), not on whether the bundle input was `Measured` (stage-1).
`ai-dynamo/dynamo` in the real fixture is the repro case.

## The fixture

Regenerated `tests/fixtures/snapshot_50` via
`PYTHONPATH=$PWD .venv/bin/python -B scripts/build_fixture_snapshot.py`
against the unchanged `~/Projects/cvi-scratch/sample-200` corpus (no network,
no edit to that read-only corpus).

- **50 of 50** bundle files changed (every one carried at least one absolute
  `source`; **158** absolute `source` strings total → all relative, 0
  remaining — verified by grep before/after).
- Every per-repo `manifest.json` `raw_hash` moved with them (the path string
  is part of what `cvi.core.models.feature_provenance_hash` hashes — this is
  the reproducibility property, not a regression: two different data roots
  holding the same relative paths now hash identically, see the pinned test
  below).
- `expected.json`: **only** the top-level `records_hash` scalar changed —
  every record's `composite`/`coverage`/`tier`/`rank`/`gate_reason`/
  `unrated_reason` is byte-identical (diffed directly).
  - old: `fe49557ca04b2de7a4b1518d6a3a45d68e1ffcc1139a80e43aa9f375eb58aed4`
  - new: `55a01ac2c6c139ba283072767a4e8b9d706375529841e6d672d37044b8eced84`
- `tests/snapshot/test_store_cli.py::_SNAPSHOT_50_RECORDS_HASH_WITH_DETECTORS`
  re-pinned to the new hash (with a dated comment explaining why).
- Every existing test that asserted the OLD absolute `source` shape
  (`tests/derive/test_input_mappings.py`, `tests/derive/
  test_universe_meta_mappings.py`, `tests/core/test_sample200_load.py`) was
  mechanically updated to the relative form (`str(tmp_path / "raw" / ...)`
  → `"raw/.../....json"`), verified by regex substitution + full diff review.

## Tests

New/updated coverage, all passing:

- `tests/snapshot/test_envelope.py`: `relative_envelope_path` strips the
  root, is POSIX even with a trailing slash on the root, rejects a path
  outside the root, and — the reproducibility property — the same
  collector/key relativises to the identical string under two different
  absolute data roots.
- `tests/core/test_validation.py`: `validate_bundle` rejects an absolute
  path, a leading-`/`, a Windows drive-letter path, a `..` escape, and an
  empty string as `Measured.source` (parametrized); accepts a relative one.
- `tests/derive/test_relative_provenance.py` (new file): derive produces a
  relative source for a resolved v1 envelope; derive refuses
  (`BundleValidationError`) an absolute source it resolved with no
  `raw_root` to relativise against (the exact shape the real bug served);
  two simulated "machines" (different absolute `tmp_path_factory` roots)
  loading the same document produce byte-identical `source`/`source_hash`/
  `provenance_hash`.
- `tests/serve/test_api_v2.py`: `EvidenceRow` rejects an absolute path, a
  leading `\`, a Windows drive letter, and a `..` escape (parametrized);
  accepts a relative one. `test_evidence_sources_are_relative_on_the_real_fixture`
  walks all 50 real fixture repos through `create_app` on a store built by
  `score_snapshot_dir`/`write_scored_snapshot` (the same path
  `python -m cvi.snapshot store` uses) and greps every served `source` for a
  leading `/`, `\`, or drive letter.
- `tests/store/test_writer.py`: `_param_row` nulls `source`/`source_hash`/
  `fetched_at`/`method` for a `no_population` row (the orthogonal bug,
  fail-before reproduced then fixed); `_param_row` raises on an absolute
  source injected via `object.__setattr__` (since `Measured.__post_init__`
  makes it otherwise unconstructable — this proves the writer's own
  defense-in-depth independently of the upstream gate).

## Mutants (each red, then restored via `git show HEAD:<path> > <path>`)

1. **Helper returns the absolute path** (`_relative_source` mutated to
   `return str(path)` unconditionally): 48 failed, 8 errors across
   `tests/derive/`, `tests/core/test_sample200_load.py`,
   `tests/snapshot/test_reproduce_fixture.py`. Restored, diff empty, 113
   passed.
2. **Validator accepts `..`** (both `cvi.core.models.
   is_relative_envelope_source` and `cvi.serve.contract.
   _is_relative_envelope_source` mutated to always return `True` after the
   drive-letter check): each time, exactly the `..`-segment parametrized
   case went red (`raw/gfi/../../etc/passwd` / `raw/pr_window/../../etc/passwd`)
   while the absolute/empty/drive-letter cases still caught it on their own
   checks. Restored, diff empty, both suites green.
3. **Writer accepts an absolute path** (`_param_row`'s guard short-circuited
   with `if False and ...`): `test_param_row_rejects_an_absolute_source`
   went red (`DID NOT RAISE ValueError`). Restored, diff empty, 43 passed.

## Suite

```
env -u PYTEST_ADDOPTS PYTHONPATH=$PWD /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -B -m pytest -p no:cacheprovider -n 4
2605 passed, 1 skipped, 15 warnings in 13.83s
exit=0
```

Ruff: `All checks passed!` (`cvi/ tests/ scripts/`).

## Explicitly out of scope (flagged as a follow-on task, not silently dropped)

`scripts/derive.py`'s `Bundle.set()`/`src()` closure and
`cvi/collect/pike_signals.py`'s `env_path()` closure still record absolute
paths in the v1 "provenance" documents they write (`src()`'s return value is
also used for immediate sha256 hashing via `_envelope_sha256`, so a naive
relativize-in-place would break that file read; the real fix needs `Bundle`
to know the data root separately from the hashing path). This never reaches
a served bundle — `cvi.derive.bundle`'s resolution chain (fixed above)
converts every one of these absolute v1-recorded paths to a relative
`Measured.source` regardless — but a fresh v1 document written today still
carries an absolute path internally. Flagged to the lead as a background
task (`task_975af7ce`) rather than expanded into this packet, since it
touches files outside this packet's read list and needs its own
fail-before/over-correction proof.

## Files touched

- `cvi/derive/bundle.py`, `cvi/core/models.py`, `cvi/collect/envelope.py`,
  `cvi/serve/contract.py`, `cvi/store/writer.py` — the fix.
- `tests/core/test_validation.py`, `tests/core/test_sample200_load.py`,
  `tests/derive/test_input_mappings.py`,
  `tests/derive/test_universe_meta_mappings.py`,
  `tests/derive/test_relative_provenance.py` (new),
  `tests/snapshot/test_envelope.py`, `tests/snapshot/test_store_cli.py`,
  `tests/serve/test_api_v2.py`, `tests/store/test_writer.py` — tests.
- `tests/fixtures/snapshot_50/{bundles/*.json,expected.json,manifest.json}` —
  regenerated fixture.
