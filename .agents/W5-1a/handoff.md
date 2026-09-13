# W5-1a handoff — v2 methodology document + renderer

Dispatch base: `5c7e1b7`'s successor at dispatch time (`5f5350a` — main
tip when the worktree was created). Main advanced once during this packet
(W3-1/RFC-0004 landing); rebased cleanly onto the new tip with **zero
conflicts** (my only shared file, `docs/v2/rfcs/CHANGELOG.md`, was touched
at the opposite end — RFC-0004's row at the top, my genesis row at the
bottom). **Final base: `acdf885`** (`docs(v2): lead log — W3-1 accepted
with follow-ups (W3-1f)`). Commit on branch `W5-1a`: `8a62b6a` ("feat(core,
audit,docs): W5-1a — v2 methodology document + renderer"). One commit
(amended once, after the rebase, to regenerate `docs/METHODOLOGY-v2.md`
against the moved `CONSTANTS_HASH`/new RFC-0004 changelog row). Not
pushed. Main not touched, `data/` not touched, no network, no wall clock.

## Diffstat (vs. base `acdf885`)

```
 cvi/audit/__init__.py                   |   8 +
 cvi/audit/methodology_history.py        | 230 +++++++++++++
 cvi/core/methodology.py                 | 588 ++++++++++++++++++++++++++++++++
 docs/METHODOLOGY-v2.md                  | 204 +++++++++++
 docs/v2/rfcs/CHANGELOG.md               |  11 +-
 docs/v2/rfcs/README.md                  |  46 +++
 scripts/render_methodology.py           | 367 ++++++++++++++++++++
 tests/audit/test_methodology_history.py | 153 +++++++++
 tests/core/test_methodology.py          | 347 +++++++++++++++++++
 tests/test_render_methodology.py        |  99 ++++++
 10 files changed, 2050 insertions(+), 3 deletions(-)
```

## What landed

1. **`cvi/core/methodology.py`** — `methodology_document() -> dict`, pure
   (`cvi.core` + stdlib only; `tests/core/test_import_graph.py` enforces
   this on every file directly under `cvi/core/`). Built only from
   `cvi.core.constants_v2` and the `cvi.core.params.PARAMS` registry, read
   fresh at call time (nothing cached at import time — required for the
   mutation test below). Fields: `benchmark_version`, `schema_version`
   (= `constants_v2.SCHEMA_VERSION` — there is no separate "methodology
   schema version" constant; this is the only traceable choice),
   `constants_hash`, `pillars`, `parameters` (curve description built from
   named constants, value domain/vocabulary from `ParamDef` +
   enum score tables, proxy info where `ParamDef.proxy_min_n` or the
   OpenSSF no-dependents-signal exception applies), `gates` (D-GATE-1..6 +
   D-EVID-1, with whatever numeric cutoffs actually exist in `cvi.core` —
   D-GATE-1/2 genuinely have none), `missingness` (five reasons + one-
   sentence semantics, the 0.60/0.80/0.95 coverage thresholds, D-MISS-5's
   not-applicable rules), `tie_break_levels` (the eight D-TIE-1 levels, in
   order, plus `composite_decimals`), `personas` (the five presets +
   the four `GATED_PERSONAS` gate thresholds). `methodology_hash()` =
   `sha256_hex(canonical_json(document))` (reusing `cvi.core.models`'
   existing functions, not a re-implementation).
   `MethodologyDocument` is a `TypedDict` here (per the packet's fallback
   branch: `cvi/serve/contract.py` does not exist anywhere on `main` as of
   this handoff — confirmed with `find . -iname contract.py` and
   `ls cvi/serve` — so there is nothing for W4-4 to validate against yet;
   **W4-4 should replace this TypedDict import with a pydantic model once
   W4-5a lands that file**).

2. **Deviation from the packet's literal deliverable list — read this.**
   The dispatch says `methodology_document()` should also carry
   `detectors` (from `cvi/verify/detectors.py`) and
   `not_hashed_process_constants` (the audit release bars), built "ONLY
   from the constants module and the param definitions" and restricted to
   "pure, stdlib + cvi.core only." Those two instructions conflict:
   detector thresholds live in `cvi.verify.detectors` and the three
   release bars live in `cvi.audit.anchors`/`cvi.audit.sensitivity` —
   neither is `cvi.core`, and `tests/core/test_import_graph.py`
   (`test_core_imports_nothing_outside_core_and_stdlib`, glob-scoped to
   every `cvi/core/*.py` file) fails hard on such an import — confirmed by
   running it before writing a line of this module. `cvi.core.pipeline`'s
   own docstring documents an identical wall for the unrelated
   `ScoreRecord.suspicion` wiring problem ("the lead's preferred plain
   import option is unavailable, confirmed against that test rather than
   assumed"), so this is a known shape of problem in this codebase, not a
   one-off. Resolution: `methodology_document()` stays pure and does NOT
   carry `detectors` or `not_hashed_process_constants` (or `rfc_log`, which
   the dispatch itself already redirects to an audit-level helper for the
   same reason — I/O). All three live only on the **extended** document
   from `cvi.audit.methodology_history.methodology_with_history(changelog_path)`,
   which is free to cross that boundary the same way
   `cvi/audit/detectors_report.py` already does. If the lead disagrees with
   this split (e.g. prefers gutting the import-graph test's scope instead),
   that is a call above this packet's pay grade — flagging it rather than
   guessing.

3. **`cvi/audit/methodology_history.py`** — the one caller allowed to cross
   that boundary. `methodology_with_history(changelog_path)` returns
   `methodology_document()` plus:
   - `detectors`: one entry per `cvi.verify.detectors.DETECTOR_NAMES` (name,
     trigger text, direction, thresholds — every threshold value read live
     from its named `cvi.verify.detectors` module attribute, never retyped).
     Building keyed by `DETECTOR_NAMES` itself (not a hand-maintained list
     of nine strings) so a tenth detector added later raises `KeyError`
     instead of silently missing from the document.
   - `not_hashed_process_constants`: `MIN_COVERAGE` (`cvi.audit.anchors`),
     `TAU_RELEASE_THRESHOLD`, `DEFAULT_PERTURBATION_PP`
     (`cvi.audit.sensitivity`) — the exact three `docs/v2/rfcs/README.md`
     "Process constants" names, read via `getattr` at call time.
   - `rfc_log`: `parse_changelog(changelog_path)` — a small regex-based
     markdown-table parser (`docs/v2/rfcs/CHANGELOG.md`'s own format:
     `| Date | RFC | Summary | Hash before -> after |`), returned oldest
     first. Handles the genesis row's `*(none)* -> `hash`` one-hash form.
   Registered in `cvi/audit/__init__.py`'s existing lazy-export pattern
   (`parse_changelog`, `methodology_with_history`).

4. **`scripts/render_methodology.py`** + **`docs/METHODOLOGY-v2.md`** — a
   pure `render_markdown(document: dict) -> str` (one table per section,
   the `cvi reproduce <snapshot dir>` template, a link to
   `docs/v2/rfcs/README.md`'s RFC procedure) plus a thin `main()` with
   `--check` (CI drift gate, exit 1 if the committed file would change) and
   plain write mode. Renders from the **extended** document
   (`methodology_with_history`), so the committed page also carries the
   detectors table, the process-constants table and the RFC log — matching
   `06` section 3 F's fuller vision of the Methodology screen more closely
   than the pure core document alone would. `docs/METHODOLOGY-v2.md` is
   committed and **must be regenerated whenever any of its sources change**
   (a constants RFC, a detector threshold change, a new CHANGELOG row) —
   `tests/test_render_methodology.py::test_committed_methodology_doc_equals_a_fresh_render`
   is the CI-visible tripwire; this exact scenario fired once already
   during this packet (RFC-0004 landed mid-rebase, moved `CONSTANTS_HASH`
   and added a changelog row, `--check` correctly went red, re-running the
   script and amending the commit fixed it — see the diffstat's small
   `docs/METHODOLOGY-v2.md` delta between the pre-rebase and post-rebase
   states in the reflog if you want to see it fire live).

5. **The three W2-8-round-2 open items**, all resolved:
   - **Pre-release version bump**: `docs/v2/rfcs/README.md` new section.
     `2.0.0-rc1` -> `2.0.0` happens in the W5-2 release RFC, not an
     ordinary constants RFC. Between now and then, an ordinary RFC still
     bumps the pre-release suffix per D-VER-1's minor/major class, using
     the rule the packet specified verbatim: "a constant that gains a
     scored consumer bumps the minor pre-release tag in its own RFC" — a
     constant added for relocation/documentation only (RFC-0001..0004, all
     four "no scoring-path consumer yet") does not bump it; the RFC that
     later wires one in does.
   - **Genesis row**: `docs/v2/rfcs/CHANGELOG.md`, bottom row, recording
     `d7664f2` (the W2-8-round-2 lead-reproduction commit named in
     `docs/v2/00-README.md`'s own ACCEPT row) and its `CONSTANTS_HASH`
     (`391d10b91e86a230fa8c16e32f57f2bc5d0449c33dc3bcb6c3f96153ef83fed8`,
     verified by `git show d7664f2:cvi/core/constants_v2.py` + exec — the
     three round-2 content/docs commits at that hash all produce the same
     value, so `d7664f2` — the docs commit the lead's own log names — is
     the least ambiguous citation). Chains exactly into RFC-0001's own
     "before" hash — `tests/audit/test_methodology_history.py::test_parse_changelog_hash_chain_is_unbroken`
     proves the whole log (including RFC-0004, which landed after this
     packet started) is one continuous, gapless lineage.
   - **`UNDETERMINED_SPDX`**: ruled **not hashed** in
     `docs/v2/rfcs/README.md` — I agree with the packet's own suggested
     framing after reading `cvi/core/gates.py`'s module docstring, which
     already argues the same thing when `OSI_APPROVED_LICENSES` was
     relocated in the *opposite* direction (W2-8 round 2): three sentinels
     for "no SPDX id recorded" are a closed absence-vocabulary, not a
     drifting licence list. This closes the W2-8-round-2 review's own
     internal inconsistency (its census listed `UNDETERMINED_SPDX` once as
     "excluded-with-reason" and once as "UNDECIDED"). Lead's call to
     reopen via a fresh RFC if this reasoning doesn't hold up.

## Tests

35 new: 17 in `tests/core/test_methodology.py` (shape, determinism,
JSON round-trip, the traceability walk, the `methodology_hash` iff-test),
9 in `tests/audit/test_methodology_history.py` (extended-document shape,
changelog parsing incl. the genesis row and the unbroken hash chain), 9 in
`tests/test_render_methodology.py` (the committed-doc-equals-fresh-render
drift gate, `--check`/write-mode CLI behaviour, cell-escaping).

**Traceability test** (`test_every_numeric_leaf_is_traceable_to_constants_or_a_param_def`):
walks `methodology_document()`, flattens every non-bool int/float leaf
(129 total, `test_traceability_leaf_count_is_reported` pins the count),
and asserts each equals a value present somewhere in a deep flatten of
`constants_v2.constants_dict()` or a registered `ParamDef`'s
`weight`/`min_n`/`proxy_min_n`/`domain`. **0 untraceable** — verified by
hand before writing the formal test, and again by the test itself.

**`methodology_hash` iff-test**: `constants_v2.CONSTANTS_HASH` is computed
once at import time, so "mutate a constant, the hash moves" is simulated
by monkeypatching a real, surfaced constant (`TTFHR_KNEE_HOURS`),
recomputing what `CONSTANTS_HASH` would become from the current
`constants_dict()` (the identical recipe `constants_v2.py`'s own
module-level assignment uses), installing that as the new
`CONSTANTS_HASH`, and confirming both it and `methodology_hash()` moved. A
second test monkeypatches `constants_v2.__doc__` and confirms neither
moves. Both pass.

Ran: `env -u PYTEST_ADDOPTS PYTHONPATH=$PWD /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -B -m pytest tests -n auto -p no:cacheprovider` → **2269 passed, 1 skipped** (the pre-existing skip is unrelated — `tests/core/test_import_graph.py` and the whole suite pass with this packet's files added). `ruff check .` repo-wide: **All checks passed**. `ruff format` applied to every file this packet wrote (not to the one pre-existing, unrelated formatting debt line in `cvi/audit/__init__.py` that predates this packet — confirmed against `main` before touching that file).

## Numbers for the dispatch's final-message template

- Commit: `8a62b6a6991c46b1f52b1ba42b8a785217ece8dc`
- Base: `acdf88505b7213d465a8f7403e913ed251c34e0c`
- Methodology hash (current tree): `fea366b0ec87ada1a3c4a333781d3bd79a1b0a42e9447470c02e59d106e5cad1`
- Constants hash it's built from: `748be6e459b0d9eac279233dac75acd5ede7da9711590c84fd0317a84762cb6b` (moved from `419091af...` to this during the packet — RFC-0004 landed on main mid-rebase; regenerated and reverified, not hidden)
- Traceability test leaf count: **129**, 0 untraceable

## Notes for downstream packets

- **W4-4** (`GET /v1/cvi/methodology`): call
  `cvi.audit.methodology_history.methodology_with_history(changelog_path)`
  for the full document (or `cvi.core.methodology.methodology_document()`
  for just the constants-derived part); merge in the per-snapshot fields
  `06` section 3 F also wants (`id`, `as_of`, `manifest_root`, counts,
  `code_commit`) from whatever snapshot-reading code that endpoint already
  has — none of that is in scope here (it needs a live snapshot, which is
  I/O this packet's brief explicitly keeps out of `cvi.core`). Replace the
  `MethodologyDocument` TypedDict import with a pydantic model once
  `cvi/serve/contract.py` exists (W4-5a).
- **06 §3 F / Methodology screen**: `docs/METHODOLOGY-v2.md` (or the same
  extended document, unrendered) is the ready-made source for every static
  part of that screen; only the six per-snapshot fields above are missing.
- The `window_days` field is `None` for every parameter except the six
  measured directly over the shared 180-day PR window
  (`ttfhr_hours`, `dr90_decision_rate`, `mr_first_rate`, `gr14_ghost_rate`,
  `ttm_p90_hours`, `substantive_review_rate`) — several other parameters
  have a real window (3.2 elephant_factor's 365-day commit window, the
  newcomer-cohort windows) that is not a registered `constants_v2` name
  today. If one of those windows is later frozen as a named constant, add
  it to `_PR_WINDOW_PARAM_IDS`'s sibling logic in
  `cvi/core/methodology.py::_parameter_entry` rather than hand-typing the
  day count.
