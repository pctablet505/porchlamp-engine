# W4-2 handoff — store schema v2, migrations, writer, reader basics

Branch `W4-2`, worktree `/home/pctablet505/Projects/cvi-wt-W4-2`, based on
main tip `a789466`. Three cherry-picks applied first, clean, no conflicts:

- `9d15d71` (W2-5f `6c409db`): ScoreRecord gains `rated`, `unrated_reason`,
  `tier_ceiling`, `leverage_coverage`, `friction_coverage`,
  `provenance_hash`; `score()` takes `raw_hash`.
- `c66f08b` (W2-6 `9ecfe21`): `porchlamp/core/tiebreak.py` (`TieBreakInput`,
  `tie_key`, `assign_ranks`).
- `0e666b9` (W2-7 `3617dfd`): `porchlamp/core/persona.py`.

None of the three cherry-picked files were modified.

## Diffstat (new work on top of the cherry-picks)

```
 porchlamp/store/__init__.py                 |  11 ++
 porchlamp/store/migrations/0001_init_v2.sql | 108 +++++++++++
 porchlamp/store/migrations/__init__.py      |  96 +++++++++
 porchlamp/store/reader.py                   | 159 +++++++++++++++
 porchlamp/store/schema.py                   | 212 ++++++++++++++++++++
 porchlamp/store/writer.py                   | 353 ++++++++++++++++++++++++++++++++++
 pyproject.toml                        |   2 +-
 tests/store/__init__.py               |   0
 tests/store/helpers.py                | 242 +++++++++++++++++++++++
 tests/store/test_migrations.py        | 132 +++++++++++++
 tests/store/test_reader.py            | 182 ++++++++++++++++++
 tests/store/test_roundtrip.py         | 172 +++++++++++++++++
 tests/store/test_schema.py            | 169 ++++++++++++++++
 tests/store/test_writer.py            | 209 ++++++++++++++++++++
 14 files changed, 2046 insertions(+), 1 deletion(-)   (.agents/W4-2/handoff.md itself not counted here)
```

Source (non-test) lines: `porchlamp/store/**` = 939. **Size deviation**: L-class
is <=800; 939 is over by 139 (~17%). Subtraction deferred, not a blocker —
consistent with nearly every prior packet's own logged deviation (W0-3 754
vs M, W2-1 1258 vs M, W2-5 867 vs L, W1-3/W1-8/W2-9 similarly). Most of the
excess is either enumerative (43 `repositories` columns appear twice, once
in `schema.py`'s Python DDL and once in the migration SQL file, by design —
two independent artifacts a test keeps in sync, not a copy-paste accident)
or the three ownership-request/deviation records the packet explicitly
required documenting in the code, not just the handoff. Trimmed once
already (writer.py/schema.py module docstrings cut ~35 lines) before
accepting the remaining deviation rather than cutting substance.

## What was built

- `porchlamp/store/schema.py` — DDL (`SCHEMA_STATEMENTS`), `create_schema`,
  `introspect_schema` (structural fingerprint: column defs + index
  column-sets, order-independent, handles v1's expression index).
- `porchlamp/store/migrations/0001_init_v2.sql` + `porchlamp/store/migrations/__init__.py`
  — forward-only runner, `schema_migrations` bookkeeping, and the one-time
  v1-shape check that renames an existing v1 `repositories` table (no
  `snapshot_id` column) to `legacy_repositories` before creating the v2
  table, so nothing is dropped.
- `porchlamp/store/writer.py` — `RepoWrite` (identity/evidence carrier),
  `write_snapshot`, `write_records` (repos + params long rows +
  populations, idempotent via `ON CONFLICT ... DO UPDATE`, no `RETURNING`),
  `repositories_snapshot_dump_hash`, `finalize_snapshot_hash`.
- `porchlamp/store/reader.py` — `get_snapshot`, `get_row`, `get_evidence`,
  `list_not_ranked`, `ranked_order_sql` (the `04` section 6 `NULLS LAST` /
  `ROW_NUMBER`/`DENSE_RANK` ordering, for the parity test and for W4-3 to
  build its persona projection on).
- `pyproject.toml` — added `porchlamp.store`, `porchlamp.store.migrations` to
  `[tool.setuptools] packages`.

## Deviations from `04` section 5's draft (documented in `schema.py`)

1. `repositories` gains `leverage_coverage`, `friction_coverage` (D-MAT-1,
   on `ScoreRecord` per W2-5f/W2-5 review, in `06` section 4's row contract
   but missing from the draft table) and `persona_composites` (JSON, same
   reason).
2. `benchmark_version` is NOT duplicated onto `repositories`: it lives once
   on `snapshots`; `reader.get_row`/`list_not_ranked` join it in.
3. `snapshots` gains `canonical_dump_hash`, `promoted_at` — superset of
   INT-W0-4's `snapshot_meta` (same column names; `id` plays
   `snapshot_meta.snapshot_id`'s role), per the packet's own instruction.
4. `populations` matches `porchlamp.snapshot.populations.build_populations_table`'s
   actual row shape (`snapshot_id, ecosystem, param_id, n, fallback,
   values_json, values_hash`) — that already-landed function's own
   docstring says "the schema and the migration are W4-2's"; the draft's
   `sorted_values`-only shape would make the real producer unusable.

## Ownership requests

1. **Repo identity is not on `ScoreRecord`.** `04` section 3 puts
   `owner`/`name`/`ecosystem`/etc. on `FeatureBundle.repo`; the packet's
   literal `write_records(records: list[ScoreRecord], snapshot_id,
   populations)` signature cannot be built as dispatched. `porchlamp.core.tiebreak`
   already logged this exact gap as an ownership request to W2-8 for its own
   tie-break evidence (`TieBreakInput`). `write_records` here takes
   `Sequence[RepoWrite]` (a `TieBreakInput` + the `FeatureBundle`, for the
   remaining identity fields and the raw per-parameter evidence
   `ScoreRecord.params` does not carry) instead. When W2-8 lands a real
   fix, `RepoWrite`/`TieBreakInput` should be revisited together.
2. **`porchlamp.lakehouse.repositories_dump_hash` is INT-W0-4-only.** Per the
   packet's own fallback, nothing is vendored: `writer.
   repositories_snapshot_dump_hash` calls `porchlamp.snapshot.reproduce.
   canonical_dump_hash` (the function IS on this tree; INT-W0-4's helper
   wraps the same one) with its own scratch-copy scoping, extended with a
   row-level `WHERE snapshot_id=?` filter the v1 (single-snapshot) table
   never needed. When INT-W0-4 lands, the lead should consider one shared
   scoping helper instead of two independent ones.
3. **Import-graph note.** `04` section 3 says "store/ imports core ... and
   nothing else"; `writer.py` also imports `porchlamp.snapshot.populations` and
   `porchlamp.snapshot.reproduce` because the packet dispatch explicitly directs
   reuse of `build_populations_table` and `canonical_dump_hash`. Flagging
   the prose/instruction mismatch for the lead to reconcile in `04`.
4. **`04` section 6's `DENSE_RANK()`-only `tie_group`** does not null out a
   singleton group's id the way `porchlamp.core.tiebreak.assign_ranks` does
   (tested in `test_ranked_order_sql_tie_group_dense_rank` — a non-tying
   row still gets a distinct dense-rank number, not `None`). Implemented
   exactly as `04` section 6 specifies since that is what a parity/reader
   helper should expose; W4-3, which owns the actual persona query and
   `tie_group` display, should decide whether to add the null-out
   refinement there.

## COULD NOT VERIFY

- Whether a future consumer (W4-3/W4-4) wants `deep_dive_json` populated by
  this writer — no source for it exists yet in any packet's contract, so
  it is left `NULL` on every write (column present, per `04` section 5).
- `GateResult.undecided` (a tuple of decision ids) has no column in `04`
  section 5's schema and is not in `06` section 4's row contract; left
  unwritten. Flagging in case a future packet needs it surfaced.

## Fail-before proof (verbatim)

`porchlamp.store` did not exist before this packet. Reproduced by moving
`porchlamp/store` aside and re-running:

```
$ mv porchlamp/store /tmp/store-hideaway && pytest tests/store/test_schema.py -p no:cacheprovider
ImportError while importing test module '.../tests/store/test_schema.py'.
tests/store/test_schema.py:12: in <module>
    from porchlamp.store import schema
E   ModuleNotFoundError: No module named 'porchlamp.store'
1 error in 0.05s
```
(restored immediately after)

## Verbatim outputs, exit codes

Packet tests:
```
$ pytest tests/store/ -p no:cacheprovider
.............................                                            [100%]
29 passed in 0.08s
EXIT:0
```

Ruff, `porchlamp/store` + `tests/store` (this packet's own files):
```
$ ruff check porchlamp/store tests/store
All checks passed!
EXIT:0
```

Ruff, full tree (pre-existing, out of scope — see below):
```
$ ruff check porchlamp/ scripts/ tests/
... 4 errors, all in scripts/curate_dataset_llm.py (F401 unused imports:
    time, datetime, timezone, typing.Any)
Found 4 errors.
EXIT:1
```
`scripts/curate_dataset_llm.py` is untouched by this packet (`git diff
--stat scripts/` is empty) and explicitly forbidden ("Do not touch ...
scripts/"); it is also the LLM-curation script the non-negotiables mark
for deletion in W0-4. Pre-existing, not mine to fix.

Full suite:
```
$ pytest tests/ -n 4 -p no:cacheprovider
1629 passed, 12 warnings in 49.88s
EXIT:0
```

## Measure

- **Migration time on the v1 DB copy** (`/home/pctablet505/Projects/cvi-scratch/v1-baseline/cvi.db`,
  3,194,880 bytes, 1,000 `repositories` rows, copied to
  `/home/pctablet505/Projects/cvi-scratch/W4-2/measure-v1-copy.db`):
  **0.0803 s**, `applied=[1]`, `legacy_repositories` rows after = 1,000
  (unchanged from before).
- **Round-trip oracle fixture** (`tests/store/test_roundtrip.py`, one
  rated + one gated + one unrated record written together):
  `write_records` returns `(3, 3, 0)` — 3 `repositories` rows, 3 `params`
  rows (rated: 2, gated: 0 — never scored, unrated: 1), 0 `populations`
  rows (none supplied in that fixture; a separate test,
  `test_write_records_writes_populations`, confirms population rows are
  written when a `PopulationSet` is passed).
- **SQL/tie_key parity test**
  (`test_ranked_order_sql_parity_with_tie_key_nulls_at_multiple_levels`):
  5-row fixture with NULLs at two different D-TIE-1 levels (friction on one
  row, leverage on two rows) — `reader.ranked_order_sql`'s
  `(owner, name)` order equals `porchlamp.core.tiebreak.assign_ranks`'s order
  exactly: `[r1, r2, r4, r5, r3]`. A companion assertion in the same test
  runs the identical query with `NULLS LAST` stripped and confirms it
  produces a *different* (wrong) order on this fixture — proving the test
  is sensitive to the fix, not vacuously passing. PASS.

## Rules followed

No `RETURNING` (grep test in `test_schema.py`, docstring-aware so its own
prose isn't a false positive, with a planted-violation positive control).
No wall-clock reads: `write_snapshot`/`apply_migrations` require an
explicit `created_at`/`applied_at` string argument (`ValueError` if
empty). No `print()`, no `TODO`. Ruff clean on owned files. Files touched:
`porchlamp/store/**` (new), `tests/store/**` (new), `pyproject.toml` (one line).
`porchlamp/lakehouse.py`, `porchlamp/api.py`, `scripts/` untouched.

---

# REWORK round 1

Review: `/tmp/claude-1000/-home-pctablet505-Projects/2a48e84a-2b94-4a2f-8bff-adbe955a4084/scratchpad/reviews/W4-2-review.md` (Opus 5, 26 items, 13 FAIL, 5 Blocker/9 Warning/1 Consider, 9/14 mutants killed). Item records under `.agents/W4-2/review/` (26 files, committed alongside this round). Dispatch covered F-001..F-015 except F-013 (explicitly W4-3's).

## Step 0 — rebase

`git rebase --onto localmain/main 0e666b9` on branch `W4-2`: dropped the three old cherry-picks (`9d15d71`/`c66f08b`/`0e666b9`, superseded on main), kept only the packet commit, replayed cleanly onto main tip `913ae2c` (no conflicts) — new head `ff7b049`. Picked up for free: W2-6 round 2 (UTC-safe `_commit_epoch`), W2-7f (`PersonaComposite`), W2-9f/g, W2-10 (`ScoreRecord.repo: RepoIdentity`, `TieBreakInput` identity validation), W1-6 round 2, W3-2, and `.agents/` no longer gitignored (`db8bb78`).

`RepoWrite` becomes `(record: ScoreRecord, bundle: FeatureBundle)` per the lead ruling: identity is `record.repo`; `__post_init__` raises on a mismatch with `bundle.repo`. `TieBreakInput` is dropped from the writer's own input — it is still how a caller runs `assign_ranks` **upstream** of the writer (tests still use it for that); the writer reads `record.tie_key`/`record.rank`/`record.tie_group` directly. `ttfhr_p75` is genuinely computed from `bundle.pr_window` now (`porchlamp.core.tiebreak.ttfhr_p75_from_pr_window`, real code path, tested with a real `PRWindowEvent`). `stars_audited`, `forks` (count) and `last_human_commit_at` have **no source anywhere** in the current `FeatureBundle`/`GateEvidence` contract (confirmed by reading both dataclasses on the rebased tree) — written `NULL`, documented in `porchlamp/store/writer.py`'s module docstring, not silently guessed. This is a real, load-bearing finding, not an assumption: I verified `RepoIdentity` and `GateEvidence` field lists directly before concluding there is no producer.

## Blockers and warnings addressed

**F-001 (migrations atomicity).** `apply_migrations` no longer uses `conn.executescript()` (which commits the pending transaction before running — the root cause). Each migration file's statements now run inside one explicit `BEGIN` … `COMMIT`, `rollback()` on any exception, the legacy rename included. Statement splitting (`_split_statements`) strips `--` line comments first — the header comment in `0001_init_v2.sql` itself contains a semicolon in prose, which a naive split broke on before this fix (caught immediately by the packet's own existing tests, not the mutation battery). Test: `test_apply_migrations_is_atomic_on_a_v1_database` (parametrized, breaking after statement 0/2/7 of 8) — sha256 of the db file before/after a failed migration:
```
raised: OperationalError incomplete input
sha256 before: 3a127ad869e98545704e5e07b90f00215ce7af2c8a562decd012f7240bf4dbe4
sha256 after : 3a127ad869e98545704e5e07b90f00215ce7af2c8a562decd012f7240bf4dbe4
MATCH: True
tables: ['repositories', 'sqlite_sequence']
rows still under original name: [('acme', 'one'), ('acme', 'three'), ('acme', 'two')]
```
Plus `test_apply_migrations_atomicity_does_not_lose_a_later_migration_file`: a two-file directory where file 2 fails leaves file 1's changes committed and file 2's changes (including its own first, otherwise-successful statement) fully rolled back.

**F-002 + F-003 (transaction + immutability).** `write_records` is one transaction (`BEGIN`/`rollback()`/`commit()`); a raise mid-batch leaves zero rows even after a later, unrelated `commit()` on the same connection (in my repro the second write actually raises `OperationalError: cannot start a transaction within a transaction` because the first failed write's rollback never ran to completion the way a bare `except: raise` would — replaced with a real `try/except Exception: conn.rollback(); raise`, verified). Lead ruling (superseding the review's own suggested delete-then-insert fix): **a snapshot id is immutable** — `write_records` raises `ValueError` before writing anything if `repositories` already has any row for that `snapshot_id`; no upsert, no replace flag, no delete-then-insert. A legitimate retry is only possible after a rollback left zero rows (tested: `test_write_records_retry_after_a_rolled_back_failure_succeeds`). Tests: `test_write_records_rejects_a_second_write_to_the_same_snapshot` (5 repos, then 2 more for the same id → raises, still exactly 5, dump hash unchanged), `test_write_records_rejects_rewrite_even_when_shrinking`, `test_write_records_rolls_back_on_failure_leaving_zero_rows_after_a_later_commit`.

**F-004 (snapshot conflict).** `write_snapshot` no longer uses `INSERT OR IGNORE` blindly: on an existing id it compares the six identity fields (`as_of`, `benchmark_version`, `constants_hash`, `code_commit`, `manifest_root`, `created_at`) — byte-equal is a no-op, any difference raises `ValueError` naming both. `canonical_dump_hash`/`promoted_at` are excluded from the comparison on purpose (they are `finalize_snapshot_hash`'s to fill in later; a normal pre-finalize call must not be treated as a conflict with its own later-stamped hash — `test_write_snapshot_never_clobbers_a_finalized_hash` still passes).

**F-005 (UTC normalisation).** New `_normalize_utc_z` (offset-less → UTC by convention, non-`Z` offset converted, `Z` idempotent, `None` passthrough). Applied at the one call site the writer has for `last_human_commit_at` (a no-op today — see the "no producer" note above; four direct unit tests on the function). The reader parity fixture (`test_reader.py`) gained a mixed-UTC-offset pair (`-05:00` vs `Z`) tied down to level 7, and a dedicated `test_last_human_commit_at_mixed_offset_sorts_by_instant_not_text`; both pass, proving normalisation on write is what makes the TEXT-vs-epoch comparison agree.

**F-006 (mutant survivors) + full 14-mutant re-run.** All 14 mutants from the review's §6 table were re-applied by hand to the reworked code (backup/restore via file copy, `git stash`/`checkout --` never used), `__pycache__` cleared before each run, baseline = `tests/store/` + `tests/test_no_clock.py` + `tests/core/test_import_graph.py` (70 passed before each mutant, restored to 70 passed after every restore):

| # | Mutant | Round-1 result | Round-2 result |
|---|---|---|---|
| M1 | writer: drop `friction_coverage` from the INSERT column list | SURVIVED→ | **KILLED** (1 failed: `test_roundtrip.py::test_roundtrip_rated_gated_unrated`) |
| M2 | writer: swap `leverage`/`friction` values | KILLED | **KILLED** (1 failed) |
| M3 | writer: not-ranked row gets `rank = 1` | SURVIVED | **KILLED** (1 failed: new `test_rank_is_null_whenever_not_rated`) |
| M4 | writer: non-canonical JSON (`sort_keys=False`) | SURVIVED | **KILLED** (1 failed: new `test_two_dict_orderings_of_the_same_record_write_byte_identical_json`) |
| M5 | writer: skip the final `conn.commit()` | SURVIVED | **KILLED** (1 failed — the immutability guard's own second-write path now surfaces the missing commit as `OperationalError: cannot start a transaction within a transaction`) |
| M6 | writer: `params.measured` always 1 | KILLED (2) | **KILLED** (2 failed) |
| M7 | writer: swap `p_`/`c_` pillar order | KILLED | **KILLED** (1 failed) |
| M8 | writer: drop the `snapshot_id` mismatch guard | KILLED | **KILLED** (1 failed) |
| M9 | migrations: skip the `schema_migrations` version bump | KILLED (3) | **KILLED** (4 failed — one more test now depends on idempotency) |
| M10 | migrations: rename `repositories` unconditionally | SURVIVED | **KILLED** (2 failed: new `test_migrating_a_create_schema_built_v2_db_is_a_noop_for_rename` + `test_writer.py`'s twin) |
| M11 | reader: drop `NULLS LAST` from the friction level | KILLED | **KILLED** (1 failed) |
| M12 | reader: remove the leverage `ORDER BY` level | KILLED | **KILLED** (1 failed) |
| M13 | reader: `list_not_ranked` drops the unrated branch | KILLED (2) | **KILLED** (2 failed) |
| M14 | reader: `ROW_NUMBER` → `RANK` for rank | SURVIVED (equivalent) | **SURVIVED — equivalent** (0 failed; level 8 lowercase `owner/name` is unique under `UNIQUE(snapshot_id, owner, name)`, so `RANK`/`ROW_NUMBER` can never diverge on this schema) |

**13/14 killed, 1 equivalent, 0 real survivors.** Every file was restored from a pre-mutation backup and the 70-test baseline re-confirmed green after each restore and once more at the end.

**F-007 (dump hash scoping).** `repositories_snapshot_dump_hash` now keeps `repositories` (an **allow-list**) instead of dropping a fixed exclude-list — every other table, named or not, is dropped from the scratch copy. Tests: `test_repositories_snapshot_dump_hash_ignores_legacy_repositories` (creates a real `legacy_repositories` table with a row, edits it, hash unchanged both times; then edits the actual v2 row and confirms the hash *does* move — the sensitivity control the review's own repro used) and `test_repositories_snapshot_dump_hash_ignores_other_snapshots` (unaffected by another snapshot's write; sensitive to its own snapshot's content via a third, genuinely different snapshot).

**F-008 (v1-migration tests skip silently).** New `_build_minimal_v1_db` + `test_v1_shaped_database_migration_preserves_legacy_rows_synthetic` is now the primary, always-running proof; the real 1,000-row baseline test is renamed `..._real_db_extra` and kept `skipif`-gated as a supplementary check, not the only one.

**F-009 (import-graph coverage).** `tests/core/test_import_graph.py` gained `test_store_imports_only_core_snapshot_or_stdlib` (allows `porchlamp.core`, `porchlamp.snapshot`, stdlib). This is the one file outside my originally-owned `porchlamp/store`/`tests/store` I touched, per the coordinator's explicit F-009 instruction.

**F-010 (packaging).** `[tool.setuptools.package-data]` added for `"porchlamp.store.migrations" = ["*.sql"]`; `apply_migrations` raises `RuntimeError` when `discover_migrations()` is empty (test: `test_apply_migrations_raises_on_an_empty_migrations_directory`).

**F-011 + F-012 (rebase + PersonaComposite).** `tests/store/helpers.py` rebuilt: `make_repo_identity`/`make_bundle`/`make_rated_record`/`make_gated_record`/`make_unrated_record` all take `owner`/`name`/`ecosystem` (or an explicit `repo=`) and build `RepoIdentity` as `ScoreRecord`'s required first field; `persona_composites` parameters are `dict[str, PersonaComposite]`. Writer fix: `_repo_row` serialises `persona_composites` via `{persona: asdict(composite) ...}` before `canonical_json` (a bare `canonical_json(record.persona_composites)` raises `TypeError` on the real dataclass — reproduced directly in `test_writer_raises_a_clear_error_on_the_stale_dict_float_shape`, not just cited from the review's merge trial). Round-trip test: `test_persona_composites_round_trip_via_asdict` reconstructs `PersonaComposite(**stored[persona])` and asserts equality to the original dataclasses.

**F-015 (gate_undecided).** `repositories.gate_undecided TEXT NOT NULL DEFAULT '[]'` added to both `schema.py` and `0001_init_v2.sql` (kept in sync, parity test passes); writer stores `canonical_json(list(record.gate.undecided))`; reader decodes it as a JSON list. Test: `test_gate_undecided_round_trips` + assertions in the round-trip oracle.

**F-014 (size).** Moved the ownership-request/deviation prose out of `writer.py`'s and `schema.py`'s module docstrings into this handoff (kept only a one-line pointer in each); removed `_TIE_GROUP_ORDER_BY_SQL` and the `DENSE_RANK` column from `reader.ranked_order_sql` entirely (handed to W4-3, per the coordinator's ruling), and deleted the test that exercised it (`test_ranked_order_sql_tie_group_dense_rank`) rather than keeping a test for a feature this package no longer has. Net effect: `porchlamp/store/*` source went from 939 to 1016 lines. **This is a net increase, not the ~855 the review projected** — the F-014 trim alone would have gotten there, but F-001/F-002/F-003/F-004/F-005/F-007/F-012/F-015 are all genuinely new, required logic (atomicity, immutability, conflict detection, UTC normalisation, allow-list scoping, dataclass serialisation, a new column) that the round-1 code did not have. Per-file: `writer.py` 353→413, `migrations/__init__.py` 96→139, `schema.py` 212→193, `reader.py` 159→151, `0001_init_v2.sql` 108→109. Named, not hidden: L-class (≤800) remains exceeded; subtraction deferred once more, this time against a larger, correctness-driven base.

## Not touched (F-013)

`list_ranked`, `snapshot_summary`, matrix projection: not added. `reader.py`'s public surface is unchanged apart from removing the `DENSE_RANK` column (net subtraction).

## Ownership requests (carried forward + new)

1. (carried forward) `porchlamp.lakehouse.repositories_dump_hash` exists only on INT-W0-4; `repositories_snapshot_dump_hash` still duplicates its scratch-copy shape, now via an allow-list. Unchanged recommendation: one shared helper once INT-W0-4 lands.
2. (new) `stars_audited`/`forks`(count)/`last_human_commit_at` have no producer anywhere in `FeatureBundle`/`GateEvidence` today (verified directly against the rebased tree, not assumed). Whichever packet wires real values should also revisit `RepoWrite`'s shape — it may need a third, small evidence carrier again, or these facts may land on `RepoIdentity`/a future bundle field.
3. (new, from the review's own note, not independently re-verified this round) foreign keys are off by default in SQLite and the writer never enables them, so a `write_records` call can target a `snapshot_id` with no `snapshots` row at all. Not one of the coordinator's F-numbered items; recorded as `COULD NOT VERIFY: whether W4-3/W4-4 wants PRAGMA foreign_keys=ON or an explicit existence check` rather than fixed unprompted, per the subtractive-rework instruction to fix exactly the listed items.

## COULD NOT VERIFY

- Whether `PRAGMA foreign_keys=ON` should be enabled on the writer's connection (see ownership request 3) — out of the coordinator's F-list for this round.
- `deep_dive_json` remains `NULL`/undocumented-further, as round 1 left it (not F-numbered this round).

## Verification (this round)

```
$ pytest tests/store/ -p no:cacheprovider
.......................................................                  [100%]
55 passed in 0.11s
EXIT:0

$ pytest tests/store/ tests/test_no_clock.py tests/core/test_import_graph.py -p no:cacheprovider
......................................................................   [100%]
70 passed in 0.20s (mutation-testing baseline, confirmed after every restore)

$ ruff check porchlamp/store tests/store tests/core/test_import_graph.py
All checks passed!
EXIT:0

$ pytest tests/ -n 4 -p no:cacheprovider
1708 passed, 12 warnings in 58.06s (0:01:00)
EXIT:0

$ ruff check porchlamp/ scripts/ tests/
... 4 errors, all in scripts/curate_dataset_llm.py (F401, untouched, forbidden, pre-existing since round 1)
EXIT:1
```

## Diffstat (round 2, on top of the rebased `ff7b049`)

```
 porchlamp/store/migrations/0001_init_v2.sql |   1 +
 porchlamp/store/migrations/__init__.py      |  65 ++++-
 porchlamp/store/reader.py                   |  30 +--
 porchlamp/store/schema.py                   |  49 ++--
 porchlamp/store/writer.py                   | 328 ++++++++++++++-----------
 pyproject.toml                        |   3 +
 tests/core/test_import_graph.py       |  22 ++
 tests/store/helpers.py                | 119 ++++++----
 tests/store/test_migrations.py        | 205 +++++++++++++++-
 tests/store/test_reader.py            | 128 +++++++---
 tests/store/test_roundtrip.py         |  79 ++++--
 tests/store/test_schema.py            |   5 +-
 tests/store/test_writer.py            | 436 ++++++++++++++++++++++++++++++----
 13 files changed, 1104 insertions(+), 366 deletions(-)
```
Plus `.agents/W4-2/review/` (26 review item-record files, committed alongside per the project's `.agents/` convention).

---

# REWORK round 2 (context for round 3)

Round-2 review: 21 items, 14 PASS / 7 FAIL, 1 Blocker (G-001) + 3 one-line
hardening items (G-002 warning, G-004 warning, G-005 warning), 18/20 mutants
killed (M1-M14 + N1-N6), all 5 round-1 Blockers independently re-verified
closed, merge trial `1777 passed`. Full report:
`/tmp/claude-1000/-home-pctablet505-Projects/2a48e84a-2b94-4a2f-8bff-adbe955a4084/scratchpad/reviews/W4-2-r2-review.md`.
Round-2 review records: `.agents/W4-2/review/round2/` (committed this round).

---

# REWORK round 3

Review: `.../W4-2-r2-review.md` (same file as above); dispatch covered
G-001, G-002, G-004, G-005, G-006. Not this round (explicitly deferred):
G-003 (size, state honestly), G-007 (`_split_statements` string-literal
risk, note only).

## Step 0 — rebase

`git rebase localmain/main` on branch `W4-2`: two commits replayed onto
main tip `4e917e1` → `bea2e06` with no conflicts (new heads `358ab11`,
`3ccf7c9`). Picked up for free: INT-W0-4 rounds 3-4 (landed on main,
`porchlamp.lakehouse.repositories_dump_hash`/`snapshot_meta` now real), the
`.agents/<packet>/review/` commit convention ratified.

## G-001 (Blocker) — widen the identity hash

`repositories_snapshot_dump_hash` renamed `snapshot_dump_hash`; the
allow-list is now `("repositories", "params", "populations")`, all three
scoped to the snapshot: `params` via `repo_id IN (SELECT id FROM
repositories WHERE snapshot_id != ?)` (deleted first -- `params` has no
`snapshot_id` of its own, and this must run before the `repositories`
delete or `PRAGMA foreign_keys` on a normal connection would block it),
`repositories` via `snapshot_id != ?`, `populations` via `snapshot_id !=
?`. Every other table is dropped. Tests: `test_snapshot_dump_hash_covers_params_and_populations_rows`
(rewriting a params row -- score, raw_value, source_hash -- moves exactly
that snapshot's hash and not another's; same for a populations row --
n, values_json, values_hash); the two existing F-007 tests
(`..._ignores_other_snapshots`, `..._ignores_legacy_repositories`) still
pass unchanged, proving repositories-level scoping and legacy isolation
both survived the widening.

**Lakehouse-delegation answer (asked, not to be implemented):**
`porchlamp.lakehouse.repositories_dump_hash` (now real, landed with INT-W0-4)
and `porchlamp.store.writer.snapshot_dump_hash` should NOT delegate to each
other. They already share the one thing that should be shared -- both
call `porchlamp.snapshot.reproduce.canonical_dump_hash` on a scratch copy
(R-2, one canonicalisation) -- but their table-scoping orchestration is
genuinely different: v1's `repositories` is a single flat table with no
`snapshot_id` (the whole table IS one implicit snapshot, hence a plain
exclude-list of `{"snapshot_meta"}` is correct there), while v2 spans
three related tables that all need a `WHERE snapshot_id = ?` filter
because the same database holds every snapshot at once. Unifying them
into one function would need a parameter switching between "hash the
whole table" and "hash three tables scoped by a key", which is not one
function doing one thing -- keeping two thin, table-shape-specific
callers of the one shared primitive is the R-2-compliant design, not a
violation of it.

## G-002 — the transaction boundary, closing mutant N1

Round 2's mutation report found mutant N1 (moving `write_records`'
`commit()` to before the params loop) SURVIVED with the production code
already correct -- the gap was that no test observed it, and the new
immutability rule (F-003) makes that gap worse: a caller who hit it would
be permanently locked out of retrying under the same id. Fixed on two
levels:
1. `test_write_records_params_loop_failure_leaves_zero_repositories_rows`:
   monkeypatches `_PARAM_INSERT_SQL` to a malformed statement so the
   failure lands specifically in the params loop, AFTER the repositories
   loop already ran in the same call; asserts zero `repositories` rows
   even after a later, unrelated `commit()`, then that a retry with the
   same id succeeds.
2. The coordinator's fuller guarantee -- "nothing for that id exists" (not
   just no `params` rows, no `snapshots` row either): enabled by G-005's
   join-if-open guard (`_begin`/`_end`), so a caller can wrap
   `write_snapshot` and `write_records` in ONE outer transaction.
   `test_write_snapshot_and_write_records_compose_into_one_transaction`
   does exactly that, injects the same params-loop failure, and asserts
   the `snapshots` row is also gone before a fresh attempt with the same
   id succeeds.

## G-004 — `PRAGMA foreign_keys = ON`

New `porchlamp.store.schema.connect(path)`: the one place a caller should open a
v2 store connection; sets the pragma before returning it. Every `tests/
store` fixture that exercises the writer (`test_writer.py`,
`test_reader.py`, `test_roundtrip.py`) now opens its connection through
it. `tests/store/test_migrations.py`'s connections are untouched --
migration-runner tests are about schema shape and atomicity, not the
writer's referential integrity, and several of them (deliberately) build
minimal test tables with no FK relationships at all.

Deliberately NOT applied to `snapshot_dump_hash`'s internal scratch
connection: verified directly that `DROP TABLE snapshots` fails with
`FOREIGN KEY constraint failed` under enforcement while `repositories`
rows still reference it, which is exactly what that function's cleanup
step needs to do. Documented in both `schema.connect`'s and
`snapshot_dump_hash`'s docstrings.

Tests: `test_orphan_params_row_is_rejected`, `test_orphan_repositories_row_is_rejected`
(both raise `sqlite3.IntegrityError` now, `pytest.raises` proves it),
`test_foreign_key_check_is_empty_after_a_full_write` (a normal write
leaves `PRAGMA foreign_key_check` empty -- **honestly noted**: this third
test does NOT actually depend on FK enforcement being on, since the
writer's own repo_id lookup never produces an orphan regardless; it
survived mutant O3 (FK pragma dropped) while the other two killed it. Kept
anyway because it is still a real, useful invariant to assert, just not
one that isolates this specific fix).

## G-005 — join an already-open transaction

`_begin(conn) -> bool` / `_end(conn, owns, *, ok)` replace the unconditional
`conn.execute("BEGIN")` + `conn.commit()`/`conn.rollback()` pairs in both
`write_snapshot` and `write_records`: `_begin` returns whether THIS call
started the transaction (`False` if one was already open), and only the
call that started it may end it. This directly implements the
coordinator's named option ("a guard... or `with conn:`") and is what
makes G-002's fuller composition possible without inventing new public
surface -- no new orchestration function was added; composing the two
existing functions under one transaction is the caller's to do, and three
tests prove it works (`test_write_snapshot_joins_an_already_open_transaction`,
`test_write_snapshot_joined_transaction_still_rolls_back`,
`test_write_records_joins_an_already_open_transaction`).

## G-006 ruling — `stars_audited`

Documented in `schema.py`'s module docstring (not code behaviour change --
the column was already `NULL`, already nullable, already tested via
`test_stars_forks_last_commit_are_null_no_producer`): W1-10 owns the
StarScout-audited value; this packet writes `NULL`, never a raw,
unaudited count under the audited-sounding column name.

## Not this round (per dispatch)

- **G-003 (size).** Stated honestly below, not cut further.
- **G-007.** `_split_statements` splits on every `;` and strips from every
  `--` regardless of string literals; safe for this schema's actual DDL
  (verified: the only string literals are `DEFAULT '{}'`/`'[]'`, neither
  containing `;` or `--`) but not defensively safe against a future
  migration that embeds either inside a string. Noted, not fixed --
  `sqlite3.complete_statement`-based accumulation is the named
  alternative if a future migration needs a literal containing `;`.

## Size (G-003, stated honestly)

```
porchlamp/store/__init__.py                      11
porchlamp/store/migrations/0001_init_v2.sql     109
porchlamp/store/migrations/__init__.py          139
porchlamp/store/reader.py                       151
porchlamp/store/schema.py                       219  (was 193; +26: connect() + its docstring, G-006 note)
porchlamp/store/writer.py                       482  (was 413; +69: _begin/_end, widened snapshot_dump_hash + its docstring)
porchlamp/store TOTAL: 1111 | L class cap 800 | round 2 was 1016 | delta +95
```
Still over cap; the deviation is now three rounds deep and each round's
growth has been real, required correctness work (atomicity, immutability,
allow-list widening, FK enforcement, transaction composability) rather
than padding -- recommend ratifying the deviation at this point rather
than continuing to ask for cuts against a size class this packet's own
subject matter (three tables' worth of atomic, integrity-checked writes
plus a migration runner) does not comfortably fit.

## Mutation re-run: all 20 round-2 mutants + 4 new (round 3)

Baseline `tests/store/` + `tests/test_no_clock.py` + `tests/core/test_import_graph.py`:
**79 passed** (was 70 in round 2; +9 for the new G-002/G-004/G-005 tests).
`__pycache__` cleared before every run; every file restored from a
pre-mutation backup copy (never `git stash`/`checkout --`) and the
79-test baseline re-confirmed green after each restore and once more at
the end.

| # | Mutant | Round 2 | Round 3 |
|---|---|---|---|
| M1 | writer: drop `friction_coverage` from the INSERT list | KILLED | KILLED (1 failed) |
| M2 | writer: swap leverage/friction values | KILLED | KILLED (1 failed) |
| M3 | writer: not-ranked row gets `rank = 1` | KILLED | KILLED (1 failed) |
| M4 | writer: non-canonical JSON | KILLED | KILLED (1 failed) |
| M5 | writer: skip the final commit/`_end` | KILLED | KILLED (1 failed -- now via `test_snapshot_dump_hash_ignores_other_snapshots`, since writes never land on disk) |
| M6 | writer: `params.measured` always 1 | KILLED | KILLED (2 failed) |
| M7 | writer: swap `p_`/`c_` pillar order | KILLED | KILLED (1 failed) |
| M8 | writer: drop the `snapshot_id` guard | KILLED | KILLED (1 failed) |
| M9 | migrations: skip the version bump | KILLED | KILLED (4 failed) |
| M10 | migrations: unconditional legacy rename | KILLED | KILLED (2 failed) |
| M11 | reader: drop `NULLS LAST` at the friction level | KILLED | KILLED (1 failed) |
| M12 | reader: remove the leverage `ORDER BY` level | KILLED | KILLED (1 failed) |
| M13 | reader: `list_not_ranked` drops the unrated branch | KILLED | KILLED (2 failed) |
| M14 | reader: `ROW_NUMBER` -> `RANK` | equivalent | **SURVIVED — equivalent** (0 failed; level 8 owner/name unique) |
| N1 | writer: transaction boundary moved (commit before params loop) | SURVIVED | **KILLED** (3 failed: the two new G-002 tests plus `test_write_records_joins_an_already_open_transaction`) |
| N2 | hash: include-list gains `legacy_repositories` back | KILLED | KILLED (1 failed) |
| N3 | writer: immutability keyed on `snapshots`, not `repositories` | KILLED | KILLED (27 failed) |
| N4 | writer: `asdict` dropped for one persona | KILLED | KILLED (4 failed) |
| N5 | migrations: `rollback()` dropped (bare re-raise) | KILLED | KILLED (1 failed) |
| N6 | writer: `rollback()`/`_end(...ok=False)` dropped in `write_records` | KILLED | KILLED (2 failed) |
| O1 | writer: `_SNAPSHOT_DUMP_TABLES` reverted to `("repositories",)` (G-001 undone) | -- | **KILLED** (1 failed: `test_snapshot_dump_hash_covers_params_and_populations_rows`) |
| O2 | (same code change as N1 -- G-002's fix and N1's kill are the same mutant) | -- | see N1 |
| O3 | `schema.connect` no longer sets `PRAGMA foreign_keys=ON` (G-004 undone) | -- | **KILLED** (2 failed: the two orphan-rejection tests; `test_foreign_key_check_is_empty_after_a_full_write` survives this one, noted above) |
| O4 | `_begin` guard removed, always unconditional `BEGIN` (G-005 undone) | -- | **KILLED** (4 failed: all three join-transaction tests plus the composed G-002 test) |

**23/24 killed, 1 equivalent, 0 real survivors.**

## Verification (round 3)

```
$ pytest tests/store/ -p no:cacheprovider
................................................................         [100%]
64 passed in 0.12s
EXIT:0

$ pytest tests/store/ tests/test_no_clock.py tests/core/test_import_graph.py -p no:cacheprovider
(mutation baseline)
79 passed in 0.22s

$ ruff check porchlamp/store tests/store tests/core/test_import_graph.py
All checks passed!

$ ruff check porchlamp/ scripts/ tests/
All checks passed!
EXIT:0
```
(The `scripts/curate_dataset_llm.py` F401 findings noted in rounds 1-2 are
gone -- that file has since been fixed/removed upstream on main; ruff is
now clean over the WHOLE tree, not just this packet's files.)

```
$ pytest tests/ -n 4 -p no:cacheprovider
1841 passed, 12 warnings in 76.01s (0:01:16)
EXIT:0
```

## Diffstat (round 3, on top of rebased `3ccf7c9`)

```
 porchlamp/store/schema.py           |  26 +++++
 porchlamp/store/writer.py           | 137 +++++++++++++++++------
 tests/store/test_reader.py    |   4 +-
 tests/store/test_roundtrip.py |   4 +-
 tests/store/test_writer.py    | 246 +++++++++++++++++++++++++++++++++++++++---
 5 files changed, 363 insertions(+), 54 deletions(-)
```
Plus `.agents/W4-2/review/round2/` (21 review item-record files, committed
this round per the ratified convention).

## COULD NOT VERIFY / ownership requests, round 3

- (carried forward) `stars_audited`/`forks`(count)/`last_human_commit_at`
  still have no v2 producer; G-006 formalises this for `stars_audited`
  specifically. No new information this round beyond the schema docstring
  update.
- G-007 is a genuine, if currently inert, robustness gap in
  `_split_statements` -- flagged, not fixed, per the dispatch.
