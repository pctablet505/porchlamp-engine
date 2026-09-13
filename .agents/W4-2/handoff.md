# W4-2 handoff — store schema v2, migrations, writer, reader basics

Branch `W4-2`, worktree `/home/pctablet505/Projects/cvi-wt-W4-2`, based on
main tip `a789466`. Three cherry-picks applied first, clean, no conflicts:

- `9d15d71` (W2-5f `6c409db`): ScoreRecord gains `rated`, `unrated_reason`,
  `tier_ceiling`, `leverage_coverage`, `friction_coverage`,
  `provenance_hash`; `score()` takes `raw_hash`.
- `c66f08b` (W2-6 `9ecfe21`): `cvi/core/tiebreak.py` (`TieBreakInput`,
  `tie_key`, `assign_ranks`).
- `0e666b9` (W2-7 `3617dfd`): `cvi/core/persona.py`.

None of the three cherry-picked files were modified.

## Diffstat (new work on top of the cherry-picks)

```
 cvi/store/__init__.py                 |  11 ++
 cvi/store/migrations/0001_init_v2.sql | 108 +++++++++++
 cvi/store/migrations/__init__.py      |  96 +++++++++
 cvi/store/reader.py                   | 159 +++++++++++++++
 cvi/store/schema.py                   | 212 ++++++++++++++++++++
 cvi/store/writer.py                   | 353 ++++++++++++++++++++++++++++++++++
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

Source (non-test) lines: `cvi/store/**` = 939. **Size deviation**: L-class
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

- `cvi/store/schema.py` — DDL (`SCHEMA_STATEMENTS`), `create_schema`,
  `introspect_schema` (structural fingerprint: column defs + index
  column-sets, order-independent, handles v1's expression index).
- `cvi/store/migrations/0001_init_v2.sql` + `cvi/store/migrations/__init__.py`
  — forward-only runner, `schema_migrations` bookkeeping, and the one-time
  v1-shape check that renames an existing v1 `repositories` table (no
  `snapshot_id` column) to `legacy_repositories` before creating the v2
  table, so nothing is dropped.
- `cvi/store/writer.py` — `RepoWrite` (identity/evidence carrier),
  `write_snapshot`, `write_records` (repos + params long rows +
  populations, idempotent via `ON CONFLICT ... DO UPDATE`, no `RETURNING`),
  `repositories_snapshot_dump_hash`, `finalize_snapshot_hash`.
- `cvi/store/reader.py` — `get_snapshot`, `get_row`, `get_evidence`,
  `list_not_ranked`, `ranked_order_sql` (the `04` section 6 `NULLS LAST` /
  `ROW_NUMBER`/`DENSE_RANK` ordering, for the parity test and for W4-3 to
  build its persona projection on).
- `pyproject.toml` — added `cvi.store`, `cvi.store.migrations` to
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
4. `populations` matches `cvi.snapshot.populations.build_populations_table`'s
   actual row shape (`snapshot_id, ecosystem, param_id, n, fallback,
   values_json, values_hash`) — that already-landed function's own
   docstring says "the schema and the migration are W4-2's"; the draft's
   `sorted_values`-only shape would make the real producer unusable.

## Ownership requests

1. **Repo identity is not on `ScoreRecord`.** `04` section 3 puts
   `owner`/`name`/`ecosystem`/etc. on `FeatureBundle.repo`; the packet's
   literal `write_records(records: list[ScoreRecord], snapshot_id,
   populations)` signature cannot be built as dispatched. `cvi.core.tiebreak`
   already logged this exact gap as an ownership request to W2-8 for its own
   tie-break evidence (`TieBreakInput`). `write_records` here takes
   `Sequence[RepoWrite]` (a `TieBreakInput` + the `FeatureBundle`, for the
   remaining identity fields and the raw per-parameter evidence
   `ScoreRecord.params` does not carry) instead. When W2-8 lands a real
   fix, `RepoWrite`/`TieBreakInput` should be revisited together.
2. **`cvi.lakehouse.repositories_dump_hash` is INT-W0-4-only.** Per the
   packet's own fallback, nothing is vendored: `writer.
   repositories_snapshot_dump_hash` calls `cvi.snapshot.reproduce.
   canonical_dump_hash` (the function IS on this tree; INT-W0-4's helper
   wraps the same one) with its own scratch-copy scoping, extended with a
   row-level `WHERE snapshot_id=?` filter the v1 (single-snapshot) table
   never needed. When INT-W0-4 lands, the lead should consider one shared
   scoping helper instead of two independent ones.
3. **Import-graph note.** `04` section 3 says "store/ imports core ... and
   nothing else"; `writer.py` also imports `cvi.snapshot.populations` and
   `cvi.snapshot.reproduce` because the packet dispatch explicitly directs
   reuse of `build_populations_table` and `canonical_dump_hash`. Flagging
   the prose/instruction mismatch for the lead to reconcile in `04`.
4. **`04` section 6's `DENSE_RANK()`-only `tie_group`** does not null out a
   singleton group's id the way `cvi.core.tiebreak.assign_ranks` does
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

`cvi.store` did not exist before this packet. Reproduced by moving
`cvi/store` aside and re-running:

```
$ mv cvi/store /tmp/store-hideaway && pytest tests/store/test_schema.py -p no:cacheprovider
ImportError while importing test module '.../tests/store/test_schema.py'.
tests/store/test_schema.py:12: in <module>
    from cvi.store import schema
E   ModuleNotFoundError: No module named 'cvi.store'
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

Ruff, `cvi/store` + `tests/store` (this packet's own files):
```
$ ruff check cvi/store tests/store
All checks passed!
EXIT:0
```

Ruff, full tree (pre-existing, out of scope — see below):
```
$ ruff check cvi/ scripts/ tests/
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
  `(owner, name)` order equals `cvi.core.tiebreak.assign_ranks`'s order
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
`cvi/store/**` (new), `tests/store/**` (new), `pyproject.toml` (one line).
`cvi/lakehouse.py`, `cvi/api.py`, `scripts/` untouched.
