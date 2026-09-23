# W1-2f handoff — dependents for Go and Maven; systems -> not_applicable

Base: `bd0cec4e` (main tip at rebase time; dispatch base was `097142a`, main
advanced twice during this packet -- see "Rebases" below). Branch: `W1-2f`
in `~/Projects/cvi-wt-W1-2f`. Commit: `ea5590b`.

`.agents/W1-2/handoff.md` (asked to be read first) does not exist anywhere
in history (`git log --all -- ".agents/W1-2*"` is empty) -- W1-2's contract
was read instead from `porchlamp/collect/depsdev.py`'s module docstring and
`tests/collect/test_depsdev.py`, which was sufficient.

## What changed

### `porchlamp/collect/depsdev.py`
- `SYSTEM_FOR_ECOSYSTEM` gains `"go": "go"`, `"maven": "maven"` (deps.dev's
  canonical ids are uppercase `GO`/`MAVEN`; kept lowercase to match this
  file's existing, live-validated `pypi`/`npm`/`crates.io` convention --
  confirmed case-insensitive by a live `curl` against both casings, see
  "Live sanity check" below).
- `package_name_for_repo` rewritten as explicit per-ecosystem branches
  (was a `SYSTEM_FOR_ECOSYSTEM`-membership check that could not have
  handled go/maven's different derivation anyway):
  - go: `go_mod_module` if given, else `github.com/<owner>/<name>` (the
    only option this packet can offer -- the `tree` envelope
    `scripts/gather.py` writes carries `{"paths": [...], "truncated": ...}`
    only, confirmed by reading its collector at `scripts/gather.py`'s
    `tree` REST hook and `porchlamp/collect/repo_object.py`'s tree-envelope
    reader: no blob contents, ever, so no `go.mod` fetch is added here).
  - maven: `maven_coordinates` (a `(groupId, artifactId)` tuple) if given,
    else `None` -- no name-derived default exists for Maven.
  - systems: unchanged, `None` (handled upstream, see below).
- New `parse_maven_coordinates(pom_xml) -> Optional[tuple[str, str]]`:
  stdlib `xml.etree.ElementTree`, namespace-agnostic (strips `{ns}` via a
  local `_local_tag` helper), `<parent><groupId>` fallback, never raises.
- `package_url` / `version_dependents_url` (deps.dev) and `ecosystemes.
  package_url` now percent-encode the package/version segment
  (`urllib.parse.quote(..., safe="")`) -- a no-op for the pypi/npm/crates
  names already tested, required for a go module path's `/` and a maven
  `groupId:artifactId`'s `:`. Verified live (below) that ecosyste.ms's
  percent-encoded-slash URL for a go module path resolves correctly.
- `collect_dependents` gains `*, go_mod_module=None, maven_pom_xml=None`
  keyword params (today's only caller, `scripts/gather.py`, passes
  neither -- no behaviour change there) and a new `systems` branch: writes
  `raw_dir/dependents/<owner>__<name>.json` with
  `_not_applicable_envelope_data()` (`source="design-rule:D-MISS-5"`,
  every dependents-shaped field `None`, `not_applicable_rule: "D-MISS-5"`)
  and returns `"not_applicable"` -- no network call. `identifier_method`
  (`"derived:module_path_default"` / `"derived:go_mod_module"` /
  `"parsed:pom_xml"`) is recorded in the envelope for go/maven only (the
  existing pypi/npm/crates envelope shape is untouched, still pinned
  byte-exact by the pre-existing `test_collect_dependents_primary_writes_
  envelope`).
- Maven without `maven_pom_xml` (today's only reality: nothing fetches
  pom.xml yet) stays `"skip"` -- identical to pre-W1-2f behaviour, zero
  calls, no envelope.

### `porchlamp/collect/ecosystemes.py`
- `package_url` percent-encodes `package` (see above); no behaviour change
  for existing plain names, `REGISTRY_FOR_ECOSYSTEM`'s go/maven entries
  (already present, unused before this packet) are now reachable.

### `porchlamp/derive/bundle.py`
- `_resolve_dependents_deduped` only (as scoped): reads `ctx.meta.get(
  "ecosystem")` (already present in the same `meta` mapping `_load_v1`
  itself reads `ecosystem` from -- no `_V1Ctx` field added).
  - `ecosystem == "systems"` -> `Unmeasured(not_applicable, detail=
    "D-MISS-5: ...")` unconditionally, ahead of the confidence check (a
    universe fact, never a collector outcome, per D-MISS-5's guard).
  - `confidence == "missing"` and `ecosystem == "maven"` -> a specific
    `third_party_gap` detail naming the uncollected `pom.xml` (instead of
    the generic "deps.dev/ecosyste.ms returned no dependents data").
  - `confidence == "missing"` and ecosystem outside `{pypi, npm, crates,
    go, maven}` -> `third_party_gap` naming the ecosystem (defensive; the
    real `Ecosystem` enum is closed to 6 values today).
  - Every other branch (verified/derived/unrecognized-confidence, and the
    generic pypi/npm/crates/go missing-case) is byte-for-byte unchanged --
    confirmed by the pre-existing tests passing unmodified plus new
    over-correction-guard tests for go/maven's `"derived"` path.
  - `scripts/derive.py` (forbidden territory) is untouched: its dependents
    block only acts when `dep.get("deduped_count") is not None`, and the
    `not_applicable` envelope's `deduped_count` is `None`, so that block
    stays a no-op for systems repos and the resolver's ecosystem check is
    the only place the decision is made.

### Tests
- `tests/collect/test_depsdev.py`: 24 -> 45 tests. Updated
  `test_ecosystem_system_mapping_exact` / `test_package_name_for_repo` for
  the new go/maven mapping; added identifier-derivation, `parse_maven_
  coordinates` (7 cases: own group, no namespace, parent fallback, own
  wins over parent, missing artifactId, no group anywhere, malformed/non-
  project XML), URL-quoting, `collect_dependents` (systems not_applicable,
  maven no-content skip, unmapped-ecosystem skip, go default/override,
  maven-with-content, go-no-count-is-fail) and the `gather.gather_repo`
  hook tests. Renamed `test_collect_dependents_systems_is_skip_no_envelope`
  -> `..._writes_not_applicable_envelope` and `test_gather_repo_dependents_
  systems_skip` -> `..._not_applicable` (behaviour changed, name follows).
- `tests/derive/test_input_mappings.py`: 32 -> 39 tests. Added: systems ->
  not_applicable across every confidence value including a hypothetical
  `"derived"` collector bug (must not smuggle a Measured value past the
  guard); maven missing -> the specific pom.xml detail; maven/go `"derived"`
  over-correction guards (Measured path unaffected); go missing -> the
  generic detail (requirement 4, unchanged branch); unmapped ecosystem ->
  named `third_party_gap`.
- No `block_live_network` fixture existed at dispatch base `097142a`; it
  landed on main in the interim (W1-7 Blocker B4 round 2) and I adopted it
  for every new/changed test that stubs `_get_json` to raise on an
  unexpected call, per this packet's explicit instruction.

## Rebases

Main advanced twice while this packet ran (background swarm): `097142a`
(dispatch base) -> `f7eeaab` (W2-8c phase 1, `PORCHLAMP_DATA_ROOT`; unrelated to
this packet's files) -> `bd0cec4e` (an unrelated anchors-fixture SKIP fix).
Rebased onto both in turn; only the first produced a conflict, in
`tests/collect/test_depsdev.py` (another packet added the `block_live_
network` fixture parameter to the three pre-existing `gather_repo`
dependents tests, one of which I had renamed/changed) -- resolved by
keeping the fixture param and my new name/docstring/assertions together;
diffed the resolution against `git diff <old-base> <new-base> --
tests/collect/test_depsdev.py` first to confirm that was the *only*
substantive change in the file. The second rebase (onto `bd0cec4e`) was
clean, no conflicts, and it fixed two failures unrelated to this packet
(`test_evaluable_anchors_pass`, `test_no_relation_ever_returns_fail_on_
this_fixture` -- both about `legal_ip`, not `dependents_deduped`) that were
present at `f7eeaab` purely because that commit predated the anchors fix.

## Suite

```
env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest tests/collect/test_depsdev.py tests/derive/test_input_mappings.py -p no:cacheprovider
........................................................................ [ 86%]
...........                                                              [100%]
83 passed in 0.13s

env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest -n 4 -p no:cacheprovider
1 failed, 2024 passed, 1 skipped in 9.23s
exit=1
```

The one failure, `tests/snapshot/test_reproduce_fixture.py::
test_fixture_rebuild_is_byte_identical`, is a real, expected, **out-of-
scope** consequence of this packet's fix, not a regression I introduced by
accident: `scripts/build_fixture_snapshot.py` rebuilds `tests/fixtures/
snapshot_50/` from the read-only sample corpus at `~/Projects/cvi-scratch/
sample-200` and now legitimately disagrees with the *committed* fixture for
every go/maven/systems repo in the 50-repo sample (`bundles/abel533__
Mapper.json`, `alibaba__AndFix.json`, `apache__thrift.json`, `torvalds__
linux.json`, and 16 more, plus the aggregate `expected.json`/
`manifest.json`) -- because `dependents_deduped` now correctly resolves
differently for them. Confirmed this is caused by my diff and not
pre-existing: the same test passes on plain `bd0cec4e` (no W1-2f changes)
and only starts failing once this branch's commits are applied. Regenerating
that fixture is owned by the W2-8b/W2-8c/W2-11 lineage (`docs/v2/
05-WORK-PLAN.md`), not this packet -- `scripts/build_fixture_snapshot.py`
and `tests/fixtures/snapshot_50/` are not in W1-2f's file ownership and
touching them here would risk clobbering that lineage's own in-flight work.
Flagged as a background follow-up task (spawn_task id `task_b25c02c8`,
"Regenerate snapshot_50 fixture after W1-2f dependents fix") rather than
fixed in this diff.

Ruff: `ruff check porchlamp/collect/depsdev.py porchlamp/collect/ecosystemes.py
porchlamp/derive/bundle.py tests/collect/test_depsdev.py tests/derive/
test_input_mappings.py` -> `All checks passed!`

## Fail-before

Reproduced against the actual dispatch/rebase base at each stage via
`git show <base>:<path> > <path>` (never `stash`/`checkout --`), ran the new
test files, confirmed every new/changed assertion failed for the expected
reason, then restored with `git show HEAD:<path> > <path>` (my own committed
fix, since the mutated lines don't exist at `<base>`):

- Against `097142a` (original dispatch base): 4 failures matching exactly
  the four behaviours this packet changes (`system_for_ecosystem`/
  `package_name_for_repo` for go/maven, `collect_dependents` systems
  skip-vs-not_applicable, the `gather_repo` hook) -- plus, confusingly, 25
  *other* failures in unrelated rows (`ttfhr_hours`, `ttm_p90_hours`,
  `substantive_review_rate`, `legal_ip`) that turned out to be caused by
  the base itself being stale (main had moved to `f7eeaab` with W1-6
  governance-evidence changes to `bundle.py` in between); confirmed by
  running the *unmodified* main checkout's own test file against its own
  `bundle.py` (32 passed) -- not a real fail-before signal, just a stale
  base, resolved by rebasing (see "Rebases") before re-measuring.
- Against `f7eeaab` (post first rebase): 24 failures, exactly the new/
  changed assertions in `tests/collect/test_depsdev.py` (21) and
  `tests/derive/test_input_mappings.py` (3: systems not_applicable, maven
  detail, unmapped-ecosystem detail) -- zero unrelated noise this time.

## Mutants (each red, pasted, restored via `git show HEAD:<path> > <path>`)

1. **Swap `not_applicable`/`third_party_gap`** -- `porchlamp/derive/bundle.py`
   line 656, `reason="not_applicable"` -> `reason="third_party_gap"` in the
   systems branch.
   ```
   FAILED tests/derive/test_input_mappings.py::test_dependents_deduped_systems_is_not_applicable
   1 failed, 37 passed in 0.09s
   ```
2. **Module path built from name only** -- `porchlamp/collect/depsdev.py` line
   197, `f"github.com/{owner}/{name}"` -> `f"github.com/{name}"`.
   ```
   FAILED tests/collect/test_depsdev.py::test_package_name_for_repo_go_default_module_path
   FAILED tests/collect/test_depsdev.py::test_collect_dependents_go_default_module_path_writes_envelope
   2 failed, 43 passed in 0.09s
   ```
3. **groupId taken from artifactId** -- `porchlamp/collect/depsdev.py` line 159,
   `resolved_group_id = group_id or parent_group_id` -> `resolved_group_id
   = artifact_id or parent_group_id`.
   ```
   FAILED tests/collect/test_depsdev.py::test_parse_maven_coordinates_own_group_id
   FAILED tests/collect/test_depsdev.py::test_parse_maven_coordinates_no_namespace
   FAILED tests/collect/test_depsdev.py::test_parse_maven_coordinates_parent_group_id_fallback
   FAILED tests/collect/test_depsdev.py::test_parse_maven_coordinates_own_group_id_wins_over_parent
   FAILED tests/collect/test_depsdev.py::test_parse_maven_coordinates_no_group_id_anywhere
   FAILED tests/collect/test_depsdev.py::test_collect_dependents_maven_with_pom_xml_writes_envelope
   6 failed, 39 passed in 0.10s
   ```

All three restored; `tests/collect/test_depsdev.py tests/derive/
test_input_mappings.py` re-verified green after each restore (83 passed).

## Live sanity check

`gh api rate_limit` before: `core {"limit":5000,"used":0,"remaining":5000}`.
After: `core {"limit":5000,"used":0,"remaining":5000}` -- unchanged, because
this collector never calls `gh`/the GitHub REST or GraphQL API; it hits
deps.dev/ecosyste.ms directly over `urllib`. `PORCHLAMP_DATA_ROOT` (the W2-8c
override the packet asked me to check for) landed on main (`f9a0116`) as a
`scripts/gather.py`/`scripts/derive.py` CLI switch, but `collect_dependents`
already takes an explicit `raw_dir` argument, so the live check called it
directly (no CLI, no `scripts/gather.py` involved) against a scratch dir:
`~/Projects/cvi-scratch/W1-2f/live_check_raw/`.

Three repos from `tests/fixtures/snapshot_50/manifest.json` in
`cvi-wt-W2-8b` (read-only): `a-h/templ` (go), `abel533/Mapper` (maven),
`alibaba/AndFix` (systems).

- **`a-h/templ` (go): `status="ok"`.** deps.dev's package lookup found the
  package and a default version (`v0.3.1020`, confirmed independently with
  `curl`), but the per-version `:dependents` endpoint answered `404
  "dependents not found"` for it (confirmed with `curl` -- deps.dev simply
  has no computed dependents graph for this Go module, not a bug in this
  packet's URL construction). The collector correctly fell through to the
  ecosyste.ms fallback, which answered 200 via the percent-encoded module-
  path URL (`.../registries/proxy.golang.org/packages/github.com%2Fa-h
  %2Ftempl`): `dependent_count=201`, `identifier_method="derived:
  module_path_default"`. This is exactly packet requirement 4 exercised
  live end-to-end (mapped package, deps.dev empty, fallback measures it).
  Also confirmed by direct `curl` that deps.dev's `/systems/{go,GO}/...`
  segment is case-insensitive (both return 200), so keeping this file's
  existing lowercase convention (rather than the canonical uppercase `GO`)
  was safe.
- **`abel533/Mapper` (maven): `status="skip"`.** No `maven_pom_xml` was
  passed (this packet adds no fetch); zero network calls, no envelope --
  identical to pre-W1-2f behaviour for maven.
- **`alibaba/AndFix` (systems): `status="not_applicable"`.** Envelope
  written at `.../dependents/alibaba__AndFix.json`,
  `source="design-rule:D-MISS-5"`, every count field `null`,
  `not_applicable_rule="D-MISS-5"`; zero network calls.

## COULD NOT VERIFY

- Maven's "contents available" path (`parse_maven_coordinates` wired
  through `collect_dependents(..., maven_pom_xml=...)`) is unit-tested with
  synthetic pom.xml bodies only -- never exercised against a real fetched
  `pom.xml`, because this packet adds no fetch (by design: "do not add a
  new network call blindly"). A later packet must add exactly one
  raw-content GET per maven repo (the same shape as `porchlamp.collect.
  repo_object._fetch_body`: `raw.githubusercontent.com/<owner>/<name>/
  <default_branch>/pom.xml`), parse it with `parse_maven_coordinates`, and
  pass the result as `maven_coordinates` to `package_name_for_repo` /
  `maven_pom_xml` to `collect_dependents`. The analogous root `go.mod`
  fetch (`.../go.mod`, parse the `module` line) is the same shape for go's
  `go_mod_module` parameter, also not added here.
- `tests/snapshot/test_reproduce_fixture.py::
  test_fixture_rebuild_is_byte_identical` fails as an expected, out-of-
  scope consequence (see "Suite" above); not fixed here, flagged as a
  background task instead (`task_b25c02c8`).
- `.agents/W1-2/handoff.md` does not exist in history; W1-2's contract was
  reconstructed from source + tests instead (noted at the top of this file).
- Whether deps.dev's dependents graph is systematically thinner for `go`
  than for `pypi`/`npm`/`crates` (one 404 on one real package is not a
  sample) -- would need a larger live sweep, out of this packet's 3-repo
  budget.
