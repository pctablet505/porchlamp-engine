# Handoff — W1-5f

Branch `W1-5f`, rebased onto main tip `5f5350a` (packet dispatch base was
`91362a1`; main advanced through W1-4, W2-11, W1-7f, W1-2f, W2-10, W3-2/W3-2f,
W2-12, W1-10, W2-13 while this packet was in flight — three separate rebases,
each clean or trivially resolved, see "Rebase history" below).

Final commit: `00d8257`.

## Diffstat

Production code, vs rebase base `5f5350a`:
```
 cvi/collect/commits.py      | 235 +++++++--------------------
 cvi/core/commit_domains.py  | 378 +++++++++++++++++++++++++++++++++++++++++++  (new file)
 cvi/core/pipeline.py        |  29 ++--
 cvi/derive/bundle.py        | 385 +++++++++++++++++++++++++++++++++++++++++++-
 cvi/verify/pipeline_hook.py |  42 +++--
 5 files changed, 867 insertions(+), 202 deletions(-)
```
Full diff (incl. tests and the regenerated fixture) vs `5f5350a`:
`61 files changed, 2438 insertions(+), 356 deletions(-)`.

## Suite

```
env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest tests/ -p no:cacheprovider -n 4
2256 passed, 1 skipped in ~11s
```
`ruff check .` — all checks passed, repo-wide.

## What was delivered

1. **`last_non_bot_commit_at`** (D-TIE-1 item 7): max commit date over
   non-bot authors in the `commits_365d` envelope, falling back to
   `commits_180d` when 365d was never collected (true for every repo in
   the current sample-200 corpus — see "Real finding" below). No
   non-bot commit in the window → measured absence
   (`value=None, method="measured:no_non_bot_commit_365d"` or the
   `..._180d_fallback` sibling for the narrower window), never
   `Unmeasured` (D-MISS-1 F2 ruling). Missing/malformed envelope →
   `Unmeasured(reason="collector_failure")`.
2. **`top_email_domain_share`**: share of non-bot commits whose
   normalised author-email domain (`cvi.core.commit_domains.org_key` —
   noreply/personal addresses count as distinct individuals, corporate
   domains merge, exactly `elephant_factor`'s own rule) equals the most
   frequent one. `n` = non-bot commit count;
   `n < constants_v2.PARAM_MIN_N["elephant_factor"]` (30, reused, no new
   tunable) → `Unmeasured(insufficient_n)`; `n` sufficient but no commit
   resolves to an org at all (no `@` in any recorded email — the real
   corpus's own case) → `Unmeasured(collector_failure)`, distinct from
   insufficient_n. Tie for the top domain → alphabetically-first wins
   (pinned; same rule `elephant_factor` already uses).
3. Wired: `cvi.core.pipeline.score_bundle_stage_9_tie_input` now reads
   `last_non_bot_commit_at` from the bundle instead of hardcoding `None`
   (W3-2f had already landed by the time I reached this step, so I did
   the one-line wiring + tests directly rather than deferring).
   `cvi.verify.pipeline_hook.detector_inputs_from_bundle` now reads
   `top_email_domain_share` the same way, feeding the `ef_domain_mismatch`
   detector for real (new end-to-end test: fires through the real
   `build_detectors`/`run_detectors` composition, not just the unit level).
4. Fixture `tests/fixtures/snapshot_50` regenerated. New
   `records_hash`: `4fbd3b5b02cca2dcb640081aa1e6e1875a4fbbe3d43db46d747cc636b766462b`.
   Everything else in `expected.json` (rank_order, counts, per-record
   gate/rated/coverage/composite/tier) is byte-identical to the pre-W1-5f
   fixture — only `records_hash` moved, because `raw_hash` (part of every
   canonical record) now includes the two new `dump_bundle` keys even
   when they carry the unset default (see "Round-trip persistence" below).

## Architecture decisions (not literal "two bundle.py rows")

The packet's own text anticipated these might be either `INPUT_MAPPINGS`
rows or bundle fields; they are bundle fields, following the
`dropped_event_count`/`_w2_9_dropped_event_count` precedent (W2-9) —
`FeatureBundle.inputs` is closed to exactly the 18 scored parameter ids
(`FeatureBundle.validate()` rejects any other key), so neither fact could
live there. Three things came out of chasing this correctly through the
import graph:

**Relocation to `cvi/core/commit_domains.py` (new file, stdlib-only).**
`cvi/derive/bundle.py` may import only `cvi.core` + stdlib
(`tests/core/test_import_graph.py::test_derive_bundle_imports_only_core_and_stdlib`)
and `cvi/core/pipeline.py` is itself inside `cvi/core`, so neither can
import `cvi.collect.commits` to reuse its `is_bot`/`org_key`/`CommitActor`/
`CommitRecord`/`records_from_rows`. Moved those five (verbatim, not
rewritten — the packet's own "reuse, do not re-implement" instruction) plus
the three domain tables into the new module; `cvi.collect.commits`
re-exports them unchanged (`__all__` declares this so ruff doesn't flag the
re-export as unused) so every existing caller/test needed zero changes —
confirmed: `tests/test_commits_ef.py` (25 tests, W1-5's own suite) and every
consumer of `commits.CommitActor`/`is_bot`/`org_key`/`records_from_rows`
(`cvi/collect/governance.py`, `cvi/collect/pike_signals.py`,
`scripts/build_fixture_snapshot.py`, `scripts/gather.py`, `scripts/derive.py`)
still pass/import unchanged. Same move packet W2-9g made for Pike
criticality (`cvi/core/openssf.py`), same reasoning.

The two new pure facts (`last_non_bot_commit_date(records)`,
`top_domain_share(records) -> TopDomainShare`) live in the new module too,
plus the `last_non_bot_commit_at` **bundle**-level sidecar
(dataclass/attr-name/getter/setter) — because `cvi.core.pipeline` needs to
read that one back and cannot reach `cvi.derive.bundle`.
`top_email_domain_share`'s bundle-level sidecar has no `cvi.core` reader
(only `cvi/verify/pipeline_hook.py`, unrestricted) and stays in
`cvi.derive.bundle`.

**Real envelope resolution (not provenance-dict lookup).** My first
implementation read `ctx.provenance.get("commits_365d")` /
`.get("commits_180d")` — this works for my own hand-built test fixtures
(which use exactly those strings as provenance keys) but **never matches
the real `~/Projects/cvi-scratch/sample-200` corpus**: its v1 documents key
`provenance` by *meta field name* (`elephant_factor`,
`active_nonbot_commits_180d`, ...), never by the literal collector name —
confirmed with `grep -l '"commits_365d"' derived/*.json` returning nothing
across all 200 repos. Fixed by resolving directly against
`raw_root/<collector>/<key>.json` (the same convention
`scripts/build_fixture_snapshot.py`'s own `_vitality_commit_count` already
uses to read this exact envelope), reading `fetched_at` from the envelope
file itself rather than a provenance entry, with the provenance-keyed
lookup kept only as a fallback for a `raw_root=None` caller. New test
(`test_last_non_bot_commit_at_resolves_via_raw_root_with_no_provenance_entry`)
pins this against a doc with zero provenance entry naming the collector.

**Round-trip persistence.** `dump_bundle`/`validate_bundle` (the v2-native
document path) didn't know about either sidecar fact, so a
dump→write→reload cycle silently reset `last_non_bot_commit_at` to unset —
harmless for `dropped_event_count` (never read by scoring) but **not**
harmless here: it feeds `tie_key`, hence `records_hash`. This would have
made `tests/snapshot/test_reproduce_fixture.py::test_reproduce_matches_expected_hash`
fail the moment the fixture was regenerated (caught by running it, not
guessed) — a real bug this packet introduced and had to close, not a
pre-existing one. Fixed: two OPTIONAL top-level v2-document keys
(`last_non_bot_commit_at`, `top_email_domain_share`, tagged
`{"kind": "measured"|"unmeasured", ...}`), whitelisted in `validate_bundle`'s
strict-keys check (`_W1_5F_OPTIONAL_TOP_LEVEL_KEYS`, not a `FeatureBundle`
dataclass field — `cvi/core/models.py` stays untouched), always written by
`dump_bundle`, read back (never raising on malformed data — falls back to
the same "unset" default) by `validate_bundle`. Covered by
`test_both_sidecar_facts_survive_a_dump_reload_round_trip` and
`test_sidecar_facts_default_when_v2_document_has_neither_optional_key`.

## Real finding (not a bug in this packet)

`top_email_domain_share` measures `Unmeasured` for **all 50** fixture
repos: 27 `insufficient_n`, 23 `collector_failure` — because every row in
the sample corpus's `commits_180d` envelopes is shaped
`{"author_login", "date"}` only (verified: `commits_365d` never exists at
all in the corpus; `commits_180d` rows carry no `author_email`/
`author_type` anywhere across all 200 repos), an older/different collector
shape than `cvi.collect.commits.shape_commit_rows`'s landed
`{"date", "author_email", "author_login", "author_type", "signed_off"}`.
`org_key` needs an `@`-bearing email and has nothing to work with, so no
commit ever resolves to an org. `last_non_bot_commit_at` is unaffected
(login + date is enough for bot exclusion and max-date), which is why 36
of 50 repos now carry a real date while 0 of 50 carry a real domain share.
Refreshing the sample corpus with W1-5's own collector (already landed)
would fix this; out of this packet's scope (no network, and the corpus is
a shared, not-mine-to-regenerate resource).

## Fixture outcome

`scripts/build_fixture_snapshot.py` run twice (once before the final
rebase, once after, to pick up W1-10/W2-13's own re-pin) against
`~/Projects/cvi-scratch/sample-200`. Final numbers over the 50 fixture
repos:

| fact | outcome | count |
|---|---|---|
| `last_non_bot_commit_at` | measured, real date | 36 |
| `last_non_bot_commit_at` | measured absence (`value=None`) | 14 |
| `last_non_bot_commit_at` | `Unmeasured(collector_failure)` | 0 |
| `top_email_domain_share` | measured | 0 |
| `top_email_domain_share` | `Unmeasured(insufficient_n)` | 27 |
| `top_email_domain_share` | `Unmeasured(collector_failure)` | 23 |

Only `expected.json`'s `records_hash` changed
(`4fbd3b5b02cca2dcb640081aa1e6e1875a4fbbe3d43db46d747cc636b766462b`); every
other field (rank_order, counts, per-record gate/rated/coverage/composite/
tier/rank/tie_group) is byte-identical to the pre-packet fixture —
confirms this packet changes no scoring outcome, only adds evidence.

## Pipeline wiring (not deferred)

`git log main --oneline | grep W3-2f` showed W3-2f had landed
(`4fbc1c3`/`583a63d`/`7159461`) partway through this packet's work, so I
did **not** leave the one-liner for the lead:

- `cvi/core/pipeline.py::score_bundle_stage_9_tie_input`: replaced the
  hardcoded `last_non_bot_commit_at=None` with
  `commit_domains.last_non_bot_commit_at(bundle).value if isinstance(...,
  LastNonBotCommitMeasured) else None`.
- `cvi/verify/pipeline_hook.py::detector_inputs_from_bundle`: added
  `top_email_domain_share=_top_email_domain_share(bundle)` reading the new
  sidecar the same way.

Tests: `tests/core/test_pipeline.py` gained
`test_score_bundle_stage_9_tie_input_reads_last_non_bot_commit_at_from_bundle_sidecar`,
`..._measured_absence_is_none`, and the packet's own required
`test_score_bundle_stage_9_tie_input_last_non_bot_commit_at_orders_per_level_7`
(two records, identical everything except the commit date, asserts
`assign_ranks` orders them by D-TIE-1 level 7). `tests/verify/
test_pipeline_hook.py` gained the adapter-level tests plus
`test_ef_domain_mismatch_fires_through_the_real_composition` (end-to-end
through `build_detectors`/`run_detectors`).

## Tests (67 new, all in this branch)

- `tests/core/test_commit_domains.py` — 19 tests: `last_non_bot_commit_date`
  (max over non-bot, bot exclusion, known-login exact match, no-login
  non-noreply-email-counts-as-human, mixed-timezone same-instant),
  `top_domain_share` (corporate merge, noreply/personal distinct
  individuals, bot exclusion in both numerator and denominator, tie
  alphabetical-first, zero-n, zero-attributable-org), plus the bundle-level
  sidecar accessor/setter/default and the measured-absence shape.
- `tests/derive/test_w15f_commit_facts.py` — 20 tests: both facts measured
  from `commits_365d`, fallback to `commits_180d` + method labelling,
  365d-present-but-empty (measured absence, not a fallback trigger),
  missing envelope (`Unmeasured(collector_failure)`), mixed timezones,
  bot exclusion, no-login-non-noreply-counts-as-human, insufficient_n,
  no-attributable-org distinct from insufficient_n, noreply/tie-break for
  the domain share, raw_root-direct resolution with zero provenance entry,
  and the two round-trip tests.
- `tests/core/test_pipeline.py` — 3 new tests (above) + one existing test's
  stale "COULD NOT VERIFY: no such field exists yet" comment corrected.
- `tests/verify/test_pipeline_hook.py` — 3 new tests (above) + one
  existing test's comment corrected (`top_email_domain_share` is `None`
  there because the hand-built bundle never had the sidecar set, not
  because the field is unsourceable).

## Fail-before

Restored `cvi/derive/bundle.py`, `cvi/collect/commits.py`,
`cvi/core/pipeline.py`, `cvi/verify/pipeline_hook.py` to the pre-packet
main tip (`8a4846c`, main before this packet's first commit) and deleted
`cvi/core/commit_domains.py` (did not exist there); ran the new test
files:
```
tests/core/test_commit_domains.py: ModuleNotFoundError: No module named 'cvi.core.commit_domains'
tests/derive/test_w15f_commit_facts.py: ImportError: cannot import name 'LastNonBotCommitMeasured' from 'cvi.derive.bundle'
tests/core/test_pipeline.py: 3 new tests FAILED (ImportError / hardcoded None), 34 pre-existing tests still passed
tests/verify/test_pipeline_hook.py: 3 new tests FAILED (ImportError), 6 pre-existing tests still passed
```
Restored via `git show HEAD:<path> > <path>` for each file (`HEAD` = the
already-landed commit at the time, never `git stash`/`checkout --`); full
suite green again after restore.

## Mutants (6, each red / pasted / restored via `git show HEAD:<path> > <path>`)

`__pycache__` cleared before the run and again after each restore.

1. **Bots counted** (`cvi/core/commit_domains.py::top_domain_share`,
   `non_bot = list(records)`): 3 tests fail —
   `test_top_domain_share_excludes_bot_commits_from_numerator_and_denominator`,
   `test_top_domain_share_computed_over_non_bot_only_not_all_commits`,
   `test_top_email_domain_share_excludes_bot_commits`.
2. **max → min date** (`last_non_bot_commit_date`, `min(dated)`): 7 tests
   fail across `tests/core/test_commit_domains.py` and
   `tests/derive/test_w15f_commit_facts.py`.
3. **Noreply domains merged** (`org_key`, noreply branch returns `domain`
   instead of `actor.login.strip() or None`): 3 tests fail, including the
   landed W1-5 oracle `tests/test_commits_ef.py::
   test_noreply_maps_to_login_and_never_to_the_domain` — this mutant would
   have broken a pre-existing test too, not just mine.
4. **Share computed over all commits instead of non-bot** (denominator
   `len(records)` instead of `n`): 3 tests fail (same set as #1, different
   assertion — `.share` wrong while `.n` stays right, proving the two
   mutants are independently detected).
5. **Absence and failure swapped**, both directions
   (`cvi/derive/bundle.py::_resolve_commit_facts`): missing-envelope case
   wrongly returns a measured absence → 1 test fails
   (`test_last_non_bot_commit_at_missing_envelope_is_unmeasured_collector_failure`);
   genuine-absence case wrongly returns `Unmeasured` → 2 tests fail
   (`..._365d_present_but_empty_is_measured_absence_not_fallback`,
   `..._no_non_bot_commit_is_measured_absence`). Re-verified after the
   raw_root resolution refactor (structure unchanged, still killed).

Every mutant restored, full suite reconfirmed green after each.

## Rebase history

Rebased three times as main advanced during this packet:
`91362a1` (dispatch base) → `8a4846c` (clean) → `5f5350a` (one conflict:
`tests/fixtures/snapshot_50/expected.json`'s `records_hash` line — both
sides had re-pinned it; resolved by taking either value and immediately
regenerating the fixture for real afterward, since the merged
`cvi/derive/bundle.py` — auto-merged cleanly, W1-10/W2-13 never touched
this packet's lines — needed a fresh, authoritative hash anyway).
`cvi/collect/commits.py`, `cvi/core/commit_domains.py`,
`cvi/core/pipeline.py`, `cvi/verify/pipeline_hook.py` were never touched by
the commits landing in between, so those four applied with zero conflicts.

## COULD NOT VERIFY / open items for the lead

- `top_email_domain_share` is genuinely `Unmeasured` for the whole 50-repo
  fixture today (see "Real finding" above) — the `ef_domain_mismatch`
  detector cannot fire against this fixture until the sample corpus is
  refreshed with the landed W1-5 commits collector (owned by whichever
  packet gets the sample-refresh work, referenced in the W2-8b/W3-2 lead
  log rows as "W2-8c").
- I did not touch `scripts/derive.py` at all — the two new facts are
  computed entirely inside `cvi.derive.bundle._load_v1` from the raw
  commits envelope, never via a `scripts/derive.py`-computed meta scalar
  (unlike `elephant_factor`). This means a *fresh* v1 derive run (not
  through this fixture's re-load path) would not carry these two facts in
  its `meta`/`provenance` at all — only `cvi.derive.bundle.load_bundle`
  reading a document's raw envelopes (via `raw_root`) produces them. If a
  future packet wants these facts computed and stored by `scripts/
  derive.py` itself (e.g. for `cvi.lakehouse`/`cvi.ingest`'s v1 path), that
  is new scope this packet did not attempt.
