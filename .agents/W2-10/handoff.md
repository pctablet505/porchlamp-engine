# W2-10 handoff — ScoreRecord carries RepoIdentity; TieBreakInput validates it

Base sha: `9a31c9f7f50e440e8a23fefda1352b4c7448068d` (main tip at packet start)
Commit sha: `0ee62ce` (branch `W2-10`)

## Diffstat

```
porchlamp/core/engine.py            |   2 +
porchlamp/core/models.py            |   4 ++
porchlamp/core/tiebreak.py          |   8 ++++
tests/core/test_engine_v2.py  | 104 +++++++++++++++++++++++++++++++++++++++++-
tests/core/test_persona_v2.py |  22 ++++++++-
tests/core/test_tiebreak.py   |  47 +++++++++++++++++--
6 files changed, 180 insertions(+), 7 deletions(-)
```

## Change

1. `porchlamp/core/models.py`: `ScoreRecord` gets a required `repo: RepoIdentity`
   field, placed first, no default. One-sentence docstring addition:
   "``repo`` is copied from the scored ``FeatureBundle.repo`` by
   :func:`porchlamp.core.engine.score` (04 section 3)."
2. `porchlamp/core/engine.py`: both `ScoreRecord(` construction sites
   (`_bare_record`'s gated/unrated shape, `score()`'s rated path) now set
   `repo=bundle.repo`. `_REASON_NO_POPULATION` (line ~156, owned by W2-8)
   untouched.
3. `porchlamp/core/tiebreak.py`: `TieBreakInput.__post_init__` additionally
   rejects a mismatch between `(owner, name)` and
   `(record.repo.owner, record.repo.name)`, naming both identities in the
   message. Nothing else in the file touched (owner/name fields themselves
   are W2-8b's, left as-is).
4. Every `ScoreRecord(` constructor site found by
   `grep -rn 'ScoreRecord(' porchlamp tests` fixed:
   - `porchlamp/core/engine.py` x2 (production, item 2 above).
   - `tests/core/test_persona_v2.py::_record` — added module-level `_REPO`
     (`RepoIdentity(owner="org", name="alpha", ...)`, same shape
     `tests/core/test_engine_v2.py::make_repo` uses) and passed it as
     `repo=_REPO`. Persona logic never reads `repo`, so a single fixed
     identity is correct here.
   - `tests/core/test_tiebreak.py::make_record` — added `owner`/`name`
     keyword params (defaults `"acme"` / `label`, matching
     `make_entry`'s own defaults) that build the record's `RepoIdentity`;
     `make_entry` now threads its own `owner`/`resolved_name` into
     `make_record` so every entry it builds has `TieBreakInput.owner/name`
     agreeing with `record.repo.owner/name` by construction (no existing
     call site needed to change).
   - `tests/store/helpers.py` — not on `main` yet (confirmed absent);
     ignored per instructions.
5. Tests added/changed:
   - **(a) fail-before/after** (`tests/core/test_engine_v2.py::test_score_record_requires_repo`):
     verified fail-before manually against the base commit (worktree at
     `9a31c9f`, output below), then wrote the permanent test asserting the
     after-behavior (`TypeError` naming `repo`) plus that supplying it
     succeeds.
   - **(b)** `record.repo == bundle.repo` asserted in three places:
     - `test_engine_end_to_end_every_field` (rated path, existing test
       extended).
     - `test_gated_repo_never_calls_the_parameter_functions` (gated path,
       existing test extended).
     - `test_unrated_repo_has_no_composite_or_tier_but_keeps_params_and_coverage`
       (unrated path, existing test extended).
     - New `test_engine_repo_is_bundle_repo_on_every_path` additionally
       uses a **distinct** owner/name per bundle across all three paths —
       the real mutant guard (a constant `RepoIdentity`, or one bundle's
       repo copied onto another path's record, would still pass the
       equality-only assertions above but fails this one on the specific
       owner/name values).
   - **(c)** `tests/core/test_tiebreak.py`:
     `test_tiebreak_input_rejects_owner_name_mismatch_with_record_repo`
     (both an owner-only mismatch and a name-only mismatch, each asserting
     the message contains both sides' values) and
     `test_tiebreak_input_accepts_a_matching_owner_name_pair`.

## Fail-before proof (verbatim)

Run against a throwaway detached worktree at the base commit
(`9a31c9f7f50e440e8a23fefda1352b4c7448068d`), inside
`/home/pctablet505/Projects/contributor-value-index` (removed after use —
`git worktree remove --force`, confirmed `git status --short` clean and
`base_check` gone from `git worktree list`):

```
BASE: ScoreRecord accepted with no repo field at all: ScoreRecord(snapshot_id='snap-1', ...)
```

(`ScoreRecord(**fields)` with no `repo` key succeeds outright on the base —
the field does not exist yet.)

After this packet, the same call raises:

```
AFTER: TypeError: ScoreRecord.__init__() missing 1 required positional argument: 'repo'
```

## Mutants killed (paste)

**Mutant 1 — engine sets `repo` from a constant instead of `bundle.repo`**
(both `ScoreRecord(` sites in `porchlamp/core/engine.py` changed to a hardcoded
`RepoIdentity(owner="mutant", name="mutant", ...)`; a "different bundle"
variant isn't constructible at either call site — each has exactly one
`bundle` parameter in scope, so the constant substitution is the meaningful
version of this mutant):

```
FAILED tests/core/test_engine_v2.py::test_engine_end_to_end_every_field - AssertionError
FAILED tests/core/test_engine_v2.py::test_gated_repo_never_calls_the_parameter_functions
FAILED tests/core/test_engine_v2.py::test_unrated_repo_has_no_composite_or_tier_but_keeps_params_and_coverage
FAILED tests/core/test_engine_v2.py::test_engine_repo_is_bundle_repo_on_every_path
```

Reverted (`cp` from a pre-mutation snapshot back over `porchlamp/core/engine.py`);
`git diff porchlamp/core/engine.py` against the committed version came back empty
before continuing.

**Mutant 2 — tiebreak mismatch check compares `owner` only**
(`porchlamp/core/tiebreak.py`'s new check narrowed to
`(self.owner,) != (self.record.repo.owner,)`):

```
FAILED tests/core/test_tiebreak.py::test_tiebreak_input_rejects_owner_name_mismatch_with_record_repo - Failed: DID NOT RAISE ValueError
```

(the name-only-mismatch half of that test is what catches it — the
owner-only-mismatch half still raises, since owner alone already differs).
Reverted the same way; diff against committed version came back empty.

## Full suite

```
env -u PYTEST_ADDOPTS PYTHONPATH=$PWD /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -B -m pytest -p no:cacheprovider -n 4
...
1639 passed, 12 warnings in 52.90s
exit=0
```

`ruff check` clean on all six touched files.

## COULD NOT VERIFY

- Nothing outstanding. `tests/store/helpers.py` (mentioned in the packet as
  a possible additional constructor site) does not exist on `main` at the
  base sha — confirmed by the `grep -rn 'ScoreRecord(' porchlamp tests` run
  itself finding no such file, so there was nothing to fix there.

## Hazard note (self-inflicted, resolved)

While setting up the fail-before check I ran
`git -C <main repo> worktree add --detach base_check <base sha>` from
inside `~/Projects/cvi-scratch/W2-10`; git resolved the relative path
against the `-C` target, not my cwd, so it briefly created a worktree
directory at `/home/pctablet505/Projects/contributor-value-index/base_check`
— inside main's own working tree. Caught immediately after use, removed
with `git worktree remove --force` against its real path, and confirmed
`git -C <main repo> status --short` clean and the entry gone from
`git worktree list`. No content in main was ever modified; nothing was
committed there.
