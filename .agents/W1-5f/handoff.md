# Handoff — W1-5f

Branch `W1-5f`. Packet dispatch base was `91362a1`; landed through four
separate rebases as main advanced while this packet was in flight (W1-4,
W2-11, W1-7f, W1-2f, W2-10, W3-2/W3-2f, W2-12, W1-10, W2-13, W3-1, W5-1a,
W4-3 — see "Rebase history" below). Lead review (Opus) verdict: REWORK
bounded to two items (F-001 Blocker, F-002 Warning) — see "REWORK round
(F-001, F-002)" below for what changed and why; everything else in this
document describes the reviewed, unchanged-since-review delivery.

Final rebase base: main tip `819d361` (the lead's rework instruction named
`3c5d99f` or later; main kept advancing after that, including an
API-rate-limit pause logged on main — not re-chased past `819d361`, since
the instruction's own floor was already cleared).

Final commit: `3706d43`.

## Diffstat

Production code, vs the final rebase base `819d361`:
```
 porchlamp/collect/commits.py      | 235 +++++++--------------------
 porchlamp/core/commit_domains.py  | 378 +++++++++++++++++++++++++++++++++++++++++++  (new file)
 porchlamp/core/pipeline.py        |  29 ++--
 porchlamp/derive/bundle.py        | 385 +++++++++++++++++++++++++++++++++++++++++++-
 porchlamp/store/writer.py         |  37 +++-                                        (F-001, REWORK)
 porchlamp/verify/pipeline_hook.py |  42 +++--
 6 files changed, 896 insertions(+), 210 deletions(-)
```
Full diff vs `819d361`: `74 files changed, 4170 insertions(+), 366 deletions(-)`
— includes tests, the regenerated fixture, `.agents/W1-5f/handoff.md`
itself, and the ten `.agents/W1-5f/review/*/record.json` files the lead
review left in the worktree (untracked at review time; committed here
since they are this packet's own review trail, not authored by this
packet's agent).

## Suite

```
env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest tests/ -p no:cacheprovider -n 4
2375 passed, 1 skipped, 12 warnings in ~13s
exit=0
```
`ruff check .` — all checks passed, repo-wide. One transient failure
observed mid-REWORK (`tests/store/test_queries.py::
test_performance_list_ranked_and_reproject_p95_under_100ms_for_10k_rows`,
a pre-existing timing-sensitive perf test unrelated to this packet) —
reproduced on the unmodified main checkout under the same `-n 4` full-suite
contention and confirmed green in isolation and on a clean re-run; not
a regression this packet introduced.

## REWORK round (F-001, F-002)

Lead review verdict (Opus): 8 PASS / 2 FAIL / 11 of 13 mutants killed —
REWORK bounded to two items. Full report:
`/tmp/claude-1000/-home-pctablet505-Projects/2a48e84a-2b94-4a2f-8bff-adbe955a4084/scratchpad/reviews/W1-5f-review.md`;
item records under `.agents/W1-5f/review/` (now committed, see Diffstat).

**F-001 (Blocker, item 4 `LENS-CORR-15`).** `porchlamp/store/writer.py::_repo_row`
still wrote `last_human_commit_at = NULL` unconditionally
(`_normalize_utc_z(None),  # last_human_commit_at: no producer yet`) — the
comment became false the moment this packet landed
`last_non_bot_commit_at` as a bundle sidecar fact, but nothing updated the
writer to read it. `porchlamp/store/reader.py`'s SQL `ORDER BY` carries
`last_human_commit_at DESC NULLS LAST` at D-TIE-1 level 7; with the column
always NULL, SQL fell straight through to the level-8 name fallback while
`porchlamp.core.tiebreak.assign_ranks` (the Python authority) used the real
date — a real, demonstrated order inversion (review §4.5: Python
`zzz-new, aaa-old`, SQL `aaa-old, zzz-new`), invisible to the existing
`tests/store/test_reader.py::_write_and_rank` fixture because it always
stamps the column with a direct `UPDATE`, "as the writer would if it had a
real producer" — which, before this packet, it never did.

Fixed with the lead's one authorised edit to `porchlamp/store/writer.py`: new
`_last_human_commit_at(bundle)` reads
`porchlamp.core.commit_domains.last_non_bot_commit_at(bundle)` (the same
accessor `porchlamp.core.pipeline.score_bundle_stage_9_tie_input` already uses
for `TieBreakInput`), extracts `.value` when it is a
`LastNonBotCommitMeasured`, else `None`, and normalises through the
already-landed `_normalize_utc_z`. Module docstring corrected (the
"`last_human_commit_at` ... no producer" sentence is now false; rewritten
to name the real producer and the F-001 history).

Five tests in `tests/store/test_writer.py`, every one writing through the
real `write_records` path with **no** post-write `UPDATE` stamp (the gap
`_write_and_rank` could not see): direct sidecar → column passthrough,
non-UTC-offset normalisation through the real writer, a measured absence
→ `NULL`, and — closing the review's own repro exactly — two records
tied through D-TIE-1 level 6 (`aaa-old`, `zzz-new`) where
`assign_ranks` and `reader.ranked_order_sql` now agree once the writer
produces the column for real. The pre-existing
`test_stars_forks_last_commit_are_null_no_producer` was renamed to
`test_stars_forks_are_null_no_producer` and its docstring corrected
(`last_human_commit_at` is NULL in that fixture only because the bundle
never had the sidecar fact set, not because the column is unproducible;
`stars_audited`/`forks` still have no producer at all).

Fail-before: reverted `porchlamp/store/writer.py` to the pre-fix version and ran
the four new positive-value tests — 3 of 4 failed (the measured-absence
case is vacuously `NULL` either way), including the exact SQL/Python order
inversion:
```
FAILED test_last_human_commit_at_is_produced_from_the_bundle_sidecar_fact
    AssertionError: assert None == '2026-06-01T00:00:00Z'
FAILED test_last_human_commit_at_normalizes_a_non_utc_offset_through_the_real_writer
    AssertionError: assert None == '2026-06-02T01:00:00Z'
FAILED test_sql_rank_order_agrees_with_python_when_writer_produces_the_commit_date
    AssertionError: assert [('acme', 'aaa-old'), ('acme', 'zzz-new')] == [('acme', 'zzz-new'), ('acme', 'aaa-old')]
```
Restored; `tests/store/` 68/68 green after.

**F-002 (Warning, mutant R3 survived).** `org_key`'s `.lower()` case fold
(the recorded email is lower-cased before matching `NOREPLY_DOMAIN`/
`PERSONAL_DOMAINS`, both stored lowercase) had no test pinning it.
Removing it left the whole suite green: an upper-case
`...@USERS.NOREPLY.GITHUB.COM` domain no longer matches `NOREPLY_DOMAIN`
and falls through to the generic "return the literal domain" branch,
collapsing 40 distinct individuals into one org (review §4.6: share
0.025 → 1.000, firing `ef_domain_mismatch` on a repo with no dominant
contributor at all).

Fixed: one test,
`test_org_key_folds_email_case_for_corporate_personal_and_noreply_domains`
(`tests/core/test_commit_domains.py`), pinning all three branches
(corporate, personal, noreply) against upper-case input. Mutant R3
re-applied in isolation (only `org_key`'s `.lower()` removed, not
`author_key`'s separate one) — killed cleanly:
```
FAILED tests/core/test_commit_domains.py::test_org_key_folds_email_case_for_corporate_personal_and_noreply_domains
    AssertionError: assert 'CORP.COM' == 'corp.com'
1 failed, 2374 passed, 1 skipped
```
Restored; full suite green after (`2375 passed, 1 skipped`).

F-003, F-004 and F-006 (the review's other, non-blocking findings) are
**not** addressed in this REWORK round — the lead's instruction bounded
the rework to F-001 and F-002 specifically; they remain open for a future
packet or lead ruling.

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
   normalised author-email domain (`porchlamp.core.commit_domains.org_key` —
   noreply/personal addresses count as distinct individuals, corporate
   domains merge, exactly `elephant_factor`'s own rule) equals the most
   frequent one. `n` = non-bot commit count;
   `n < constants_v2.PARAM_MIN_N["elephant_factor"]` (30, reused, no new
   tunable) → `Unmeasured(insufficient_n)`; `n` sufficient but no commit
   resolves to an org at all (no `@` in any recorded email — the real
   corpus's own case) → `Unmeasured(collector_failure)`, distinct from
   insufficient_n. Tie for the top domain → alphabetically-first wins
   (pinned; same rule `elephant_factor` already uses).
3. Wired: `porchlamp.core.pipeline.score_bundle_stage_9_tie_input` now reads
   `last_non_bot_commit_at` from the bundle instead of hardcoding `None`
   (W3-2f had already landed by the time I reached this step, so I did
   the one-line wiring + tests directly rather than deferring).
   `porchlamp.verify.pipeline_hook.detector_inputs_from_bundle` now reads
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

**Relocation to `porchlamp/core/commit_domains.py` (new file, stdlib-only).**
`porchlamp/derive/bundle.py` may import only `porchlamp.core` + stdlib
(`tests/core/test_import_graph.py::test_derive_bundle_imports_only_core_and_stdlib`)
and `porchlamp/core/pipeline.py` is itself inside `porchlamp/core`, so neither can
import `porchlamp.collect.commits` to reuse its `is_bot`/`org_key`/`CommitActor`/
`CommitRecord`/`records_from_rows`. Moved those five (verbatim, not
rewritten — the packet's own "reuse, do not re-implement" instruction) plus
the three domain tables into the new module; `porchlamp.collect.commits`
re-exports them unchanged (`__all__` declares this so ruff doesn't flag the
re-export as unused) so every existing caller/test needed zero changes —
confirmed: `tests/test_commits_ef.py` (25 tests, W1-5's own suite) and every
consumer of `commits.CommitActor`/`is_bot`/`org_key`/`records_from_rows`
(`porchlamp/collect/governance.py`, `porchlamp/collect/pike_signals.py`,
`scripts/build_fixture_snapshot.py`, `scripts/gather.py`, `scripts/derive.py`)
still pass/import unchanged. Same move packet W2-9g made for Pike
criticality (`porchlamp/core/openssf.py`), same reasoning.

The two new pure facts (`last_non_bot_commit_date(records)`,
`top_domain_share(records) -> TopDomainShare`) live in the new module too,
plus the `last_non_bot_commit_at` **bundle**-level sidecar
(dataclass/attr-name/getter/setter) — because `porchlamp.core.pipeline` needs to
read that one back and cannot reach `porchlamp.derive.bundle`.
`top_email_domain_share`'s bundle-level sidecar has no `porchlamp.core` reader
(only `porchlamp/verify/pipeline_hook.py`, unrestricted) and stays in
`porchlamp.derive.bundle`.

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
dataclass field — `porchlamp/core/models.py` stays untouched), always written by
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
shape than `porchlamp.collect.commits.shape_commit_rows`'s landed
`{"date", "author_email", "author_login", "author_type", "signed_off"}`.
`org_key` needs an `@`-bearing email and has nothing to work with, so no
commit ever resolves to an org. `last_non_bot_commit_at` is unaffected
(login + date is enough for bot exclusion and max-date), which is why 36
of 50 repos now carry a real date while 0 of 50 carry a real domain share.
Refreshing the sample corpus with W1-5's own collector (already landed)
would fix this; out of this packet's scope (no network, and the corpus is
a shared, not-mine-to-regenerate resource).

## Fixture outcome

`scripts/build_fixture_snapshot.py` run four times in total (before/after
each of the two rebases) against `~/Projects/cvi-scratch/sample-200`.
Final numbers over the 50 fixture repos (unchanged since the first run --
only `raw_hash`/`records_hash` move on a re-pin, never these facts):

| fact | outcome | count |
|---|---|---|
| `last_non_bot_commit_at` | measured, real date | 36 |
| `last_non_bot_commit_at` | measured absence (`value=None`) | 14 |
| `last_non_bot_commit_at` | `Unmeasured(collector_failure)` | 0 |
| `top_email_domain_share` | measured | 0 |
| `top_email_domain_share` | `Unmeasured(insufficient_n)` | 27 |
| `top_email_domain_share` | `Unmeasured(collector_failure)` | 23 |

Only `expected.json`'s `records_hash` changed each time this packet's code
changed (last value, after the REWORK round's rebase onto `819d361`:
`b4177bf29fa39678c16d3a05895e80c6d3bcbd6c5d4127bbc3d3b2f5e158ee51`); every
other field (rank_order, counts, per-record gate/rated/coverage/composite/
tier/rank/tie_group) stayed byte-identical to the pre-packet fixture across
every regeneration — confirms this packet changes no scoring outcome, only
adds evidence. The lead review's own §7b merge trial independently
predicted this exact hash for the `819d361` composition before this
REWORK round ran it for real.

## Pipeline wiring (not deferred)

`git log main --oneline | grep W3-2f` showed W3-2f had landed
(`4fbc1c3`/`583a63d`/`7159461`) partway through this packet's work, so I
did **not** leave the one-liner for the lead:

- `porchlamp/core/pipeline.py::score_bundle_stage_9_tie_input`: replaced the
  hardcoded `last_non_bot_commit_at=None` with
  `commit_domains.last_non_bot_commit_at(bundle).value if isinstance(...,
  LastNonBotCommitMeasured) else None`.
- `porchlamp/verify/pipeline_hook.py::detector_inputs_from_bundle`: added
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

## Tests (50 new, all in this branch)

(F-005 nit from the lead review: the original handoff claimed 67 and did
not re-derive; the real total at that point was 45 — 19 + 20 + 3 + 3
below. This REWORK round adds 5 more: 1 for F-002, 4 for F-001 — see
"REWORK round (F-001, F-002)" below. 45 + 5 = 50, confirmed by the suite
delta: main `2325 passed` vs this branch `2375 passed`.)

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

### REWORK round (F-001, F-002)

- `tests/core/test_commit_domains.py` — +1:
  `test_org_key_folds_email_case_for_corporate_personal_and_noreply_domains`
  (F-002, kills mutant R3).
- `tests/store/test_writer.py` — +4: `test_last_human_commit_at_is_produced_
  from_the_bundle_sidecar_fact`, `..._normalizes_a_non_utc_offset_through_
  the_real_writer`, `..._is_null_for_a_measured_absence`, and
  `test_sql_rank_order_agrees_with_python_when_writer_produces_the_commit_
  date` (F-001 — the review's own two-repo inversion repro, closed).
  `test_stars_forks_last_commit_are_null_no_producer` renamed to
  `test_stars_forks_are_null_no_producer` (not a new test) with its
  docstring corrected: `last_human_commit_at` now HAS a producer, it is
  just unset in that particular fixture.

## Fail-before

Restored `porchlamp/derive/bundle.py`, `porchlamp/collect/commits.py`,
`porchlamp/core/pipeline.py`, `porchlamp/verify/pipeline_hook.py` to the pre-packet
main tip (`8a4846c`, main before this packet's first commit) and deleted
`porchlamp/core/commit_domains.py` (did not exist there); ran the new test
files:
```
tests/core/test_commit_domains.py: ModuleNotFoundError: No module named 'porchlamp.core.commit_domains'
tests/derive/test_w15f_commit_facts.py: ImportError: cannot import name 'LastNonBotCommitMeasured' from 'porchlamp.derive.bundle'
tests/core/test_pipeline.py: 3 new tests FAILED (ImportError / hardcoded None), 34 pre-existing tests still passed
tests/verify/test_pipeline_hook.py: 3 new tests FAILED (ImportError), 6 pre-existing tests still passed
```
Restored via `git show HEAD:<path> > <path>` for each file (`HEAD` = the
already-landed commit at the time, never `git stash`/`checkout --`); full
suite green again after restore.

## Mutants (6 original + R3 for F-002, each red / pasted / restored via `git show HEAD:<path> > <path>`)

All 7 re-run a final time on the fully rebased tree (base `819d361`) as
part of this REWORK round, in addition to the review's own independent
14-mutant battery (§6 of the review report: 13 run, 11 killed, R3 and R6
survived — R3 closed here, R6 (`_commit_envelope_path`'s `is_file()`
guard, F-003) intentionally left open, out of this round's bounded
scope).

`__pycache__` cleared before the run and again after each restore.

1. **Bots counted** (`porchlamp/core/commit_domains.py::top_domain_share`,
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
   (`porchlamp/derive/bundle.py::_resolve_commit_facts`): missing-envelope case
   wrongly returns a measured absence → 1 test fails
   (`test_last_non_bot_commit_at_missing_envelope_is_unmeasured_collector_failure`);
   genuine-absence case wrongly returns `Unmeasured` → 2 tests fail
   (`..._365d_present_but_empty_is_measured_absence_not_fallback`,
   `..._no_non_bot_commit_is_measured_absence`). Re-verified after the
   raw_root resolution refactor (structure unchanged, still killed).
6. **`org_key` case fold removed** (F-002, mutant R3 -- `.lower()` dropped
   from `org_key` only, `author_key`'s separate `.lower()` left intact):
   1 test fails, `test_org_key_folds_email_case_for_corporate_personal_and_
   noreply_domains` (`assert 'CORP.COM' == 'corp.com'`).

Every mutant restored, full suite reconfirmed green after each
(`2375 passed, 1 skipped, exit=0` after the final restore).

## Rebase history

Rebased five times in total as main advanced during and after review:
`91362a1` (dispatch base) -> `8a4846c` (clean) -> `5f5350a` (one conflict:
`tests/fixtures/snapshot_50/expected.json`'s `records_hash` line -- both
sides had re-pinned it; resolved by taking either value and immediately
regenerating the fixture for real afterward, since the merged
`porchlamp/derive/bundle.py` -- auto-merged cleanly, W1-10/W2-13 never touched
this packet's lines -- needed a fresh, authoritative hash anyway).
`porchlamp/collect/commits.py`, `porchlamp/core/commit_domains.py`,
`porchlamp/core/pipeline.py`, `porchlamp/verify/pipeline_hook.py` were never touched by
the commits landing in between, so those four applied with zero conflicts.

REWORK round, after the lead's review: `5f5350a` -> `819d361` (main tip at
the time; the lead's floor was `3c5d99f`, cleared). Same shape of
conflict, same resolution: only `tests/fixtures/snapshot_50/expected.json`
conflicted (both sides had re-pinned `records_hash`); `porchlamp/store/writer.py`
-- touched by both this REWORK round and W4-3's own landing -- auto-merged
with zero conflict (confirmed by grepping the merged file for
`_last_human_commit_at`/`commit_domains` after the rebase). Resolved by
taking either `records_hash` value and regenerating for real:
`b4177bf29fa39678c16d3a05895e80c6d3bcbd6c5d4127bbc3d3b2f5e158ee51` --
the exact value the lead review's own §7b merge trial had already
predicted for this composition. Main moved twice more after `819d361`
while this rework was in progress (including an API-rate-limit pause
logged on main); not re-chased, since the lead's instructed floor
(`3c5d99f` or later) was already cleared by `819d361`.

## COULD NOT VERIFY / open items for the lead

- `top_email_domain_share` is genuinely `Unmeasured` for the whole 50-repo
  fixture today (see "Real finding" above) — the `ef_domain_mismatch`
  detector cannot fire against this fixture until the sample corpus is
  refreshed with the landed W1-5 commits collector (owned by whichever
  packet gets the sample-refresh work, referenced in the W2-8b/W3-2 lead
  log rows as "W2-8c").
- I did not touch `scripts/derive.py` at all — the two new facts are
  computed entirely inside `porchlamp.derive.bundle._load_v1` from the raw
  commits envelope, never via a `scripts/derive.py`-computed meta scalar
  (unlike `elephant_factor`). This means a *fresh* v1 derive run (not
  through this fixture's re-load path) would not carry these two facts in
  its `meta`/`provenance` at all — only `porchlamp.derive.bundle.load_bundle`
  reading a document's raw envelopes (via `raw_root`) produces them. If a
  future packet wants these facts computed and stored by `scripts/
  derive.py` itself (e.g. for `porchlamp.lakehouse`/`porchlamp.ingest`'s v1 path), that
  is new scope this packet did not attempt.
- The review's F-003 (mutant R6, `_commit_envelope_path`'s `is_file()`
  guard un-pinned in the `raw_root`-set-but-file-missing-with-a-provenance-
  entry case), F-004 (a malformed optional top-level key degrades silently
  to `Unmeasured(collector_failure)` instead of raising), and F-006 (two
  stale docstrings in `porchlamp/core/tiebreak.py` and `tests/verify/
  test_pipeline_hook.py` that still describe the pre-W1-5f state) are
  **not** addressed in this REWORK round — out of the lead's bounded scope
  (F-001, F-002 only). Left open for the lead or a future packet.
