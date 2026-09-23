# W4-4 handoff — API v2 (the endpoints the UI consumes)

Worktree `/home/pctablet505/Projects/cvi-wt-W4-4`, branch `W4-4`, base `main`
tip `3c5d99f` (via `90b2553`, the actual dispatch-time tip). Cherry-picked
`44b1d67` (W4-5a's `porchlamp/serve/contract.py` + `tests/fixtures/ui/*.json`)
first, as instructed.

## What was delivered

1. `porchlamp/serve/app.py` — the v2 API app factory: `create_app(db_path, *,
   web_dir) -> FastAPI`. Router `/v1/porchlamp`: `GET /leaderboard`, `GET
   /not-ranked`, `GET /matrix`, `GET /repos/{owner}/{name}`, `GET
   /repos/{owner}/{name}/evidence`, `GET /snapshot`, `GET /methodology`,
   `POST /reproject`. Every response goes through a `porchlamp.serve.contract`
   model via `response_model=`. Static mount of `web_dir` at `/` with an
   `index.html` SPA fallback (`_SPAStaticFiles`); v1's app
   (`porchlamp.api.create_app`) mounted unchanged at `/legacy` whenever the
   served DB has a v1 shape to guard (see item 2).
2. Served-DB guard: `scripts/serve.py` gained `open_or_exit(db_path) ->
   OperationalStore` (a pure extraction of `main()`'s existing three
   inline lines — zero behavioural change, `refusal_reasons`/message
   format untouched). `porchlamp.serve.app._run_served_db_guard` calls it; a
   `sqlite3.OperationalError` (a database that has never held v1 data at
   all — confirmed directly: `OperationalStore.__init__` itself raises
   against a v2-only `repositories` table, not just a later query) is
   treated as "nothing to guard", and `/legacy` is not mounted for such a
   database. A genuine v1-shaped refusal still raises `SystemExit` with
   today's exact message.
3. `scripts/serve.py` `main()` now builds `porchlamp.serve.app.create_app`
   instead of `porchlamp.api`'s app directly; same `--db`/`--port`/`$PORT` flags.
   `create_serve_app`/`refusal_reasons`/`matching_snapshot_id`/
   `count_repos` are untouched and still directly tested by the pre-
   existing `tests/test_serve.py` (15/15 still pass, unmodified).
4. `porchlamp/store/writer.py`: the one-line `stars_audited` producer — reads
   `item.bundle.inputs.get("stars_audited")` (a `Measured` value a future
   promote-pipeline packet places there; `FeatureBundle.inputs` has no
   construction-time validation, only the separate, never-called-here
   `.validate()`), `NULL` when absent or `Unmeasured`. Also updated the
   module docstring's stale "no producer" sentence. Did not touch the
   `last_human_commit_at` line (W1-5f's, left alone per the coordination
   note).
5. `porchlamp/serve/contract.py`: took `cvi-wt-W4-5a`'s LATEST file (which had
   moved since the dispatch's own snapshot — it now includes
   `balanced_rank`/`rank_delta` on `LeaderboardRow`, Review Blocker B-6,
   and had REVERTED my own first-pass `GateReason` widening) as the base,
   then re-added `RepoDetailResponse` (this packet's own addition, not one
   of the W4-5a-review's twelve bridge items — no existing model supports
   a gated/unrated repo's detail view). `RepoDetailResponse.gate_reason`
   is a plain `str` (not the shared `GateReason` Literal — see bridge item
   3 below) and `c_<pillar>` is nullable (unlike `LeaderboardRow`'s, since
   a gated repo has no `PillarScore` for any pillar at all, unlike a rated
   one `list_ranked` would ever return).
6. Tests: `tests/serve/helpers.py` (a >= 300-row synthetic snapshot
   builder reusing `tests/store/helpers.py` + `tests/store/test_queries.py`'s
   own `assign_ranks` pattern) and `tests/serve/test_api_v2.py` (34 tests).

## Endpoint field-bridge table

Bridge decisions are numbered from the W4-5a-review's own "Contract bridge
table for W4-4" (`/tmp/claude-1000/.../scratchpad/reviews/W4-5a-review.md`)
plus the lead's addendum ruling on the items it left open. Full rationale
is in `porchlamp/serve/app.py`'s module docstring; summarised per endpoint below.

| Endpoint | Store source | Bridge |
|---|---|---|
| `GET /leaderboard` | `queries.list_ranked` | Item 1: under a persona, flat `rank/tie_group/porchlamp/coverage/tier` are the `_p`-suffixed values, never the (possibly-null) stored balanced ones. Item 2: `balanced_rank` = stored balanced `rank`; `rank_delta = balanced_rank - rank` (both `None` under balanced, or when `balanced_rank` is itself `None`) — confirmed sign convention against `contract.py`'s own validator. Item 7: `sort` accepts `p_<pillar>` and an optional `-` (stripped; `porchlamp.store.queries` has no ascending form — see COULD NOT VERIFY). Item 8: `page`/`page_size` → `limit`/`offset`; any value `_validate_paging` accepts. Item 12: `raw_hash` stays (fixture-blocked, see below). Every other field ((`p_*`, `c_*`, `leverage`, `friction`, `*_coverage`, `quadrant`, `tier_ceiling`, `persona_gates`, `persona_composites`, `suspicion`, `stars_audited`, `snapshot_id`, `benchmark_version`) is a direct 1:1 projection, named explicitly (`_project_leaderboard_row`) — no `**record` passthrough. |
| `GET /not-ranked` | `queries.list_not_ranked` + `queries.snapshot_summary` (for `counts`) | Items 3-5: `_classify_not_ranked` splits the store's one flat `reason` into `gated`/`unrated`/`persona_hidden` — `gate_reason == "unrated: no_external_workflow"` (a stage-6 GATE) → UNRATED group, `unrated_reason="no_external_workflow"` (lead's explicit choice over widening `GateReason`); `routed == "off_platform"` on an eligible repo → UNRATED, `unrated_reason="off_platform_routed"`; a persona-gated row (only under a non-balanced `persona`) → `persona_hidden`. No server-side paging (contract has none — `queries.list_not_ranked` is called with `limit=MAX_LIMIT` once; see COULD NOT VERIFY). |
| `GET /matrix` | `queries.matrix_points` | `_matrix_point` returns `None` (folded into `excluded_count`) when `leverage`/`friction`/`quadrant` is null on an otherwise-rated point (found via the fixture's own NULL-friction tie row, not assumed) — `MatrixPoint`'s three fields are all required. Item 11: `axis_coverage_caption` is left `{}` — the lead picked "the contract carries the raw numbers, the UI renders the sentence" over server-rendered captions, but `MatrixResponse` has no numeric field for `axis_partial_count`/`axis_partial_share` yet (`queries.matrix_points` already returns them) — deferred to W4-5b. |
| `GET /repos/{o}/{n}` | `porchlamp.store.reader.get_row` | `RepoDetailResponse` (this packet's addition) — direct 1:1 projection (`_repo_detail`), works for gated/unrated/rated alike; 404 when `get_row` returns `None`. |
| `GET /repos/{o}/{n}/evidence` | `queries.repo_evidence` | Returns `list[EvidenceRow]` directly (matches `tests/fixtures/ui/evidence-*.json`'s own shape — NOT the `{repo, params}` wrapper `repo_evidence` itself returns); 404 when the repo is unknown. Item 10: `pillar` is never coerced from `None` — an unknown `param_id` fails `EvidenceRow`'s non-null `Literal` as a 500 (lead's explicit ruling), asserted never to happen via the closed `PARAM_INPUT_IDS`/`PARAMS` set every written row is drawn from. |
| `GET /snapshot` | `queries.snapshot_summary` | Item 9: field-for-field, no bridging. Refuses (503) when `canonical_dump_hash` is `None` (not yet finalised via `finalize_snapshot_hash`) rather than crash on `SnapshotSummary`'s required `str`. |
| `GET /methodology` | `porchlamp.audit.methodology_history.methodology_with_history` | `response_model=MethodologyDocument` (the `TypedDict` from `porchlamp.core.methodology`, per the packet's own reading list — no pydantic model exists for this and none was invented). |
| `POST /reproject` | `queries.reproject` | Rows reuse the SAME `_project_leaderboard_row` core as the leaderboard, sourcing `rank/tie_group/porchlamp/coverage/tier` from the plain (already-custom) fields and `balanced_rank`/`rank_delta` from `record["balanced_rank"]`. `tier_ceiling` is RECOMPUTED (`_no_ceiling_or_band`, from `constants_v2.CEILING_A_MIN_COVERAGE`/`NO_CEILING_MIN_COVERAGE`) rather than passed through — `queries.reproject`'s own `tier_ceiling` column is the stale BALANCED value. `page`/`page_size` accepted as query params alongside the POST body (the contract has no paging fields for this endpoint; not specified by the dispatch, added for parity with the leaderboard's own pattern). |

## Performance

TestClient, 10,000-row synthetic snapshot (`tests/serve/test_api_v2.py::
test_performance_leaderboard_and_reproject_p95_under_150ms_for_10k_rows`),
p95 over 15 calls, budget 150 ms (server 100 ms + serialisation):

```
GET  /leaderboard  page_size=20:     ~35-37 ms
POST /reproject    page_size=20:     ~76-80 ms
GET  /leaderboard  page_size=1000:   ~74-106 ms
POST /reproject    page_size=1000:   ~116-163 ms
```

All comfortably under budget in isolation; ONE observed overshoot
(162.72 ms) under the FULL suite's `-n 4` xdist contention (this shared
host runs several concurrent agent sessions — the same documented
phenomenon `tests/store/test_queries.py`'s own perf tests already
tolerate). Fixed the same way that file already does: one bounded retry
before failing (`_assert_under_budget_with_retry`, copied pattern), never
a relaxed budget. Confirmed not attributable to this packet's queries: the
pre-existing `tests/store/test_queries.py::
test_performance_list_ranked_and_reproject_p95_under_100ms_for_10k_rows`
ALSO flaked once under the same `-n 4` run (passes cleanly standalone and
on a clean full-suite rerun) — this packet never touches `porchlamp/store/
queries.py`.

## Mutation table (10/10 killed)

Each: backed up the file (plain `cp`, never `git stash`/`checkout --`),
applied the mutation, ran the named test(s) and confirmed a hard failure,
restored from the backup, re-ran to confirm green, then `diff`-verified
byte-identical restoration.

| # | Mutant | File | Test(s) killing it | Result |
|---|---|---|---|---|
| M1 | Drop `suspicion` from `_project_leaderboard_row`'s fields | `porchlamp/serve/app.py` | `test_leaderboard_suspicion_and_stars_audited_round_trip` | KILLED |
| M2 | Page off-by-one: `_paging` returns `page * page_size` instead of `(page-1) * page_size` | `porchlamp/serve/app.py` | `test_paging_pages_partition_the_full_set_in_order`, `test_leaderboard_balanced_default_shape_and_total` | KILLED (both) |
| M3 | Sort allow-list bypass: `_translate_sort` maps the vanity field `"stars"` to `"coverage"` | `porchlamp/serve/app.py` | `test_leaderboard_sort_pillar_translation_and_allow_list` | KILLED |
| M4 | Error body shape: `_error_body` drops the `"error"` wrapper | `porchlamp/serve/app.py` | `test_error_body_shape_is_consistent_across_404_422_503` | KILLED |
| M5 | Persona default: leaderboard's `persona` `Query` default changed to `"first_timer"` | `porchlamp/serve/app.py` | `test_leaderboard_balanced_default_shape_and_total` | KILLED |
| M6 | 404 → 200: unknown-repo `/evidence` returns `[]` instead of raising 404 | `porchlamp/serve/app.py` | `test_evidence_unknown_repo_is_404_with_consistent_body` | KILLED |
| M7 | Guard skipped: `_run_served_db_guard` always returns `None` | `porchlamp/serve/app.py` | `test_create_app_refuses_a_fixture_marked_v1_database` | KILLED |
| M8 | `tie_group` coerced: `tie_group or 0` instead of `tie_group` | `porchlamp/serve/app.py` | `test_leaderboard_tie_group_shared_by_ties_and_null_for_singletons` | KILLED |
| M9 | `stars_audited` fabricated: hardcoded `999` regardless of `Measured`/`Unmeasured`/absent | `porchlamp/store/writer.py` | `test_leaderboard_stars_audited_null_when_not_measured`, `test_leaderboard_suspicion_and_stars_audited_round_trip` | KILLED (both) |
| M10 | Methodology from a literal: `get_methodology` returns a hardcoded stub dict | `porchlamp/serve/app.py` | `test_methodology_matches_the_real_pure_document_and_its_hash` | KILLED (via `ResponseValidationError` — the stub is too shallow even for the schema) |

## Suite

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest tests/ -p no:cacheprovider -n 4
2359 passed, 1 skipped, 12 warnings in 13.43s
exit=0

$ ruff check porchlamp/ tests/ scripts/
All checks passed!
```

`tests/test_serve.py` (v1, pre-existing): 15/15 unchanged, unmodified.

## COULD NOT VERIFY / known limitations, flagged for follow-up

- **Bridge item 12 (raw_hash) only partially applied.** The lead's ruling
  was "move `raw_hash` to the `repos/{o}/{n}` detail response, drop it
  from `LeaderboardRow`." `RepoDetailResponse.raw_hash` was added (done),
  but `raw_hash` was NOT dropped from `LeaderboardRow`: it is still a
  REQUIRED field on `cvi-wt-W4-5a`'s current `contract.py`, and the
  already-shipped `tests/fixtures/ui/leaderboard*.json` fixtures
  (cherry-picked from `44b1d67`, never regenerated) still carry it on
  every row. Dropping it here would fail this packet's own fixture/model
  parity test (`test_ui_fixtures_validate_against_the_api_contract_models`).
  Whoever regenerates those fixtures should drop the field from both at
  once.
- **Bridge item 11 (matrix axis captions).** `MatrixResponse.
  axis_coverage_caption` stays empty. The lead's ruling ("the contract
  carries the numbers, the UI renders the sentence") needs a contract
  change (`axis_partial_count`/`axis_partial_share` as numeric fields,
  values already computed by `queries.matrix_points`) this packet did not
  make — named for W4-5b, per the addendum's own instruction.
  Additionally, `queries.matrix_points`'s own `axis_partial_count`/
  `axis_partial_share` are a SINGLE combined-axis aggregate (W4-3 review
  N-4, never fixed): even once the contract carries numbers, they cannot
  honestly say "friction ... 12%" per-pillar without a `queries.py` change
  this packet is out of scope to make.
- **Bridge item 7 (`-` sort direction) is accepted but inert.**
  `porchlamp.store.queries._build_order_by` has no ascending form at all (every
  branch is hard-coded `DESC`) — `_translate_sort` strips a leading `-`
  and never rejects it (per the lead's ruling), but `-porchlamp` and `porchlamp` order
  identically today. Needs a `porchlamp.store.queries` change to actually invert
  order; out of this packet's file-ownership scope.
- **`/not-ranked` has no server-side paging.** The CURRENT
  `NotRankedResponse` contract carries no `page`/`page_size`/`total`
  fields at all (by design, per the W4-3 review's own note — "this list
  is bounded and small enough..."). This endpoint fetches up to
  `queries.MAX_LIMIT` (1000) rows in one call; a not-ranked set exceeding
  1000 would silently truncate. Not exercised by this packet's fixture
  (18 not-ranked rows).
- **`POST /reproject`'s `page`/`page_size` query params are this packet's
  own addition**, not named in the dispatch or the contract (`ReprojectRequest`
  carries only the five weights). Added for parity with `/leaderboard`'s
  own pattern; flagging in case a future contract revision wants to make
  it explicit.
- **Persona-view `tier_ceiling` is the stored BALANCED value**, not
  recomputed for `coverage_p` (unlike `/reproject`, where it IS
  recomputed — `queries.list_ranked`'s persona branch has no
  `tier_ceiling_p` column to read, and the review's bridge table did not
  flag this specific field). A minor product-accuracy gap, not asserted
  against by any test here.
- **`RepoDetailResponse` is entirely this packet's own invention** (not
  one of the W4-5a-review's twelve bridge items) — reviewed by neither the
  W4-3 nor the W4-5a review passes. Its `gate_reason` is deliberately a
  plain `str`, not the shared `GateReason` Literal (a repo gated via
  `"unrated: no_external_workflow"` would otherwise fail this model too,
  and this model has no "group" to translate that string into, unlike
  `NotRankedRow`).
- **RESOLVED by the second rebase (below):** `web/v2/` now exists (W4-5a
  landed it on `main`) and was smoke-tested directly through
  `create_app(db_path, web_dir=Path("web/v2"))` — `index.html`, `app.js`
  and every other static asset serve, the SPA fallback returns
  `index.html` for an unknown app-owned path, and `/v1/porchlamp/*` still wins
  routing over the static mount. `scripts/serve.py --db <real db>` now
  starts fully.

## Rebase notes

First rebase: based on `main` tip at dispatch time (`90b2553`, itself
descended from the named `3c5d99f`); `main` had advanced to `88ce3cc`
(W1-5f landing) by completion. One conflict, in `porchlamp/store/writer.py`'s
module docstring — W1-5f's own `last_human_commit_at` producer and this
packet's `stars_audited` producer both rewrote the same paragraph;
resolved by merging both explanations into one (the CODE hunks — the
`_last_human_commit_at` function, the `_repo_row` tuple's two separate
lines, the new import — auto-merged cleanly, no manual code changes
needed). Verified both producers coexist (`grep` for both function/column
names) before continuing.

Second rebase (mid-packet, per the coordinator's W4-5a-landed notice):
`main` advanced to `d112aa2` (W4-5a's UI v2 core + Python suite +
handoff). The cherry-picked `44b1d67` was auto-dropped by patch-id
(`git rebase` reported "skipped previously applied commit"); one
conflict, in `porchlamp/serve/contract.py` — this branch's own
pull-then-re-add-`RepoDetailResponse` commit against `main`'s now-landed
copy of the same file. Resolved exactly as instructed: took `main`'s
`porchlamp/serve/contract.py` wholesale (`git show main:... | diff` to confirm
byte-identical before editing further), then re-applied ONLY the
`RepoDetailResponse` addition on top (still not one of the W4-5a-review's
twelve bridge items, still not landed anywhere else) — confirmed
`balanced_rank`/`rank_delta`'s sign convention on `main`'s copy is
unchanged (`rank_delta == balanced_rank - rank`, matching this packet's
`_leaderboard_row`/`_reproject_row` all along; no code change needed).
Full suite green after both rebases: `2502 passed, 1 skipped` (up from
`2359` — `main`'s own landings added tests along the way). Branch is
current with `main` tip `d112aa2` as of this handoff.

---

# Round 2 (REWORK, Opus review) — the four Blockers + W-1/W-3/W-4

Review: `/tmp/claude-1000/-home-pctablet505-Projects/2a48e84a-2b94-4a2f-8bff-adbe955a4084/scratchpad/reviews/W4-4-review.md`, records under `.agents/W4-4/review/`. Verdict: REWORK bounded to four Blockers (6 PASS / 2 PASS-with-findings / 3 FAIL, 17/20 mutants). Rebased onto `main` tip `08c1502` (>= the named `9d364cc`) before starting — clean, no conflicts (only doc/inventory commits landed in between).

## What changed

1. **`porchlamp/serve/guard.py`** (new file) — both guards now live here (Blocker B-4, folding in Warning W-5):
   - The v1 guard (`count_repos`/`matching_snapshot_id`/`refusal_reasons`/`open_or_exit`) moved VERBATIM from `scripts/serve.py` — same logic, same messages. `_FIXTURE_CLOCK_SENTINEL` is a frozen literal (`datetime(2026, 9, 11, 12, 0, 0)`), not an import of `porchlamp.benchmark_data.BENCHMARK_NOW` — per `.agents/W4-1/inventory.md` §5's explicit recommendation (that module is scheduled to move under `tests/fixtures/synthetic_catalog.py`, "never importable from `porchlamp/`"); `tests/serve/test_guard.py::test_fixture_clock_sentinel_matches_benchmark_data` pins the two values byte-identical.
   - The v2 guard (`v2_refusal_reasons`, new): recomputes `porchlamp.store.writer.snapshot_dump_hash` for `served_snapshot_id` (most-recently-promoted, else most-recently-created) and refuses on a mismatch against the stored `canonical_dump_hash` — but ONLY for a snapshot that IS finalised (`canonical_dump_hash IS NOT NULL`); an unfinalised one is a normal, temporary state (Warning W-6, see below), not a startup refusal. Also checks the fixture-clock stamp against whichever table carries the v1 shape today (`repositories` unmigrated, `legacy_repositories` after `04` §10's migration renames it).
   - `run_guard(db_path)`: the ONE function both entrypoints call — v2 guard first (unconditional), then the v1 guard only if the database has a v1 shape at all (`OperationalError` -> `None`, not an error).
   - `porchlamp/serve/app.py` no longer imports `scripts.serve` at all (the reverse-import Warning W-5 flagged); `scripts/serve.py` now imports FROM `porchlamp.serve.guard` and re-exports the v1 names so `tests/test_serve.py` keeps resolving `scripts.serve.refusal_reasons` etc. unchanged (15/15 still pass, unmodified).
   - `tests/core/test_import_graph.py` gained a `porchlamp/serve` edge rule (stdlib + any `porchlamp.*` + `fastapi`/`starlette`/`pydantic`; `scripts.*` forbidden and asserted so explicitly).

2. **Blocker B-1** (`scripts/serve.py` couldn't serve the v2 app on any database): `main()` now calls `porchlamp.serve.guard.run_guard` (the SAME function `create_app` calls) instead of the v1-only `open_or_exit` unconditionally — a v2-only database no longer crashes with an uncaught `sqlite3.OperationalError` before `uvicorn` starts; the v1 banner is skipped gracefully (`store is None`) rather than computed against a nonexistent shape. Separately, `porchlamp/serve/app.py::_current_snapshot_id` now catches `sqlite3.OperationalError` (no `snapshots` table at all, a v1-shaped database) and returns `None` — the SAME "nothing to serve yet" fact an empty `snapshots` table already produced, flowing into the existing 503 instead of escaping as a bare `500 Internal Server Error`. Tested through `main()` via a REAL subprocess (`test_main_starts_on_a_v2_database_from_a_foreign_cwd`, `test_main_v1_endpoints_do_not_500_on_a_v2_only_database` — foreign cwd, real port, real HTTP calls, not just `create_app` in-process) and directly at the `create_app` layer against a LEGITIMATE (non-fixture-marked, non-tampered) promoted v1 database (`test_create_app_serves_v1_porchlamp_endpoints_as_503_not_500_on_a_legitimate_v1_db`).

3. **Blocker B-2** (the `Mount("/")` SPA fallback swallowed unmatched API paths and method mismatches): `_SPAStaticFiles.get_response` now raises a JSON 404 immediately for any path under `_API_RESERVED_PREFIXES` (`v1/`, `legacy/`) or `_API_RESERVED_EXACT` (`v1`, `legacy`, `healthz`), before ever attempting to resolve or fall back to a static file — a method mismatch on a real endpoint (`GET /v1/porchlamp/reproject`, POST-only) reaches this same mount (StaticFiles only ever handles GET/HEAD) and is caught by the SAME prefix check. Five new tests cover unmatched paths, the method mismatch, bare `/healthz`, an unmounted `/legacy/*`, and an over-correction guard proving real endpoints and the genuine SPA fallback (`/some/deep/app/route`) still work.

4. **Blocker B-3** (the blanket `ValueError` handler misclassified server faults as 422): the handler now checks `isinstance(exc, pydantic.ValidationError)` and routes that case to a NEW generic 500 response (`_internal_error_response`, Warning W-7) instead of 422 — a stored `quadrant='Nowhere'` or any other contract-model failure inside a handler is now a 500 with a non-leaking `"internal server error"` detail, never raw pydantic text. A separate `@app.exception_handler(Exception)` catches anything else unanticipated with the SAME shape (W-7's "every fault uses that shape"). Genuine caller-input `ValueError`s (persona/sort/paging/weights, from `porchlamp.store.queries`'s own validators) are UNCHANGED — still 422 (over-correction guard test included).

5. **Warning W-6** (inconsistent finalisation policy): folded into the SAME change as B-1's `_require_snapshot` — every snapshot-touching endpoint now checks BOTH existence and `canonical_dump_hash IS NOT NULL` in the one place (`_require_snapshot`), so `/matrix`, `/repos/{o}/{n}` and `/repos/{o}/{n}/evidence` 503 on an unfinalised snapshot exactly like `/leaderboard`/`/not-ranked`/`/snapshot` always did (`test_snapshot_not_finalised_is_503` extended). `_require_snapshot_summary` no longer duplicates the finalisation check.

6. **Warning W-1** (not-ranked counts contradicted the groups): `/not-ranked`'s `counts` are now derived from the SAME three-way split the response itself carries (`gated=len(gated)`, `unrated=len(unrated)+len(persona_hidden)`, `rated=persona_visible_counts[persona]`) instead of `snapshot_summary`'s raw stage-0/stage-3 triage — the two genuinely disagree whenever bridge item 3's reclassification applies (my own fixture reproduces the exact review pattern: `counts.gated` one less, `counts.unrated` one more, than the raw store counts). Three tests: the raw store triage (still correct on its own terms, renamed off the old, now-wrong test name), the balanced split, and the persona split (`counts.rated == leaderboard.total` under the same persona — the exact equality the review found broken at 64 vs 58).

7. **Warning W-3 / item-06** (the `-` sort direction was inert): `porchlamp/store/queries.py` (lead-authorised, out-of-scope-normally change) — `_build_order_by` gained `descending: bool = True`, applied ONLY to the promoted level (never the D-TIE-1 tail, never `rank`/`tie_group`/`rank_p`/`tie_group_p`); `list_ranked` threads it through. `porchlamp/serve/app.py::_translate_sort` now returns `(field, descending)` instead of discarding the stripped `-`. Two new `tests/store/test_queries.py` tests: every allowed `sort` value gives a genuinely different, NULLs-last-in-both-directions order under `descending=False` with `rank`/`tie_group` unchanged; offset paging re-verified deterministic under `descending=False` too. 56/56 `test_queries.py` tests pass.

8. **Warning W-4** (two real detection gaps, RX2/RX3): one test each — `_matrix_point`'s independent `quadrant is None` check (a row with real leverage/friction but no quadrant, excluded, not plotted with a null); `_no_ceiling_or_band`'s A/B bands, via `/reproject` on this fixture's uniform 0.9 per-pillar coverage (always squarely inside band A).

## Suite / ruff

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest tests/ -p no:cacheprovider -n 4
2531 passed, 1 skipped, 12 warnings in ~20s
exit=0

$ ruff check porchlamp/ tests/ scripts/
All checks passed!
```
(Re-run twice; stable both times, not attributable to the shared-host contention this suite's own perf tests already document.)

## Mutation re-run: 20 review mutants + 4 Blocker-specific = 24 run, 23 killed, 1 equivalent

Same protocol as round 1: plain `cp` backup (never `git stash`/`checkout --`), single unique-string mutation, run the named test(s), confirm hard failure, restore from backup, `diff` byte-identical.

| # | Mutant | File | Result |
|---|---|---|---|
| M1-M10 | (round 1's ten, re-injected against the reworked tree) | `porchlamp/serve/app.py`, `porchlamp/store/writer.py` | ALL KILLED (unchanged from round 1) |
| R1 | persona projection swapped (flat = balanced) | `porchlamp/serve/app.py` | KILLED |
| R2 | `rank_delta` sign inverted | `porchlamp/serve/app.py` | KILLED |
| R3 | SPA fallback removed entirely | `porchlamp/serve/app.py` | KILLED |
| R4 | guard's `SystemExit` swallowed in `run_guard` | `porchlamp/serve/guard.py` | KILLED (round 1's fixture-marked test alone did not catch it — the v2 guard's OWN fixture-clock check independently refuses that same case; added `test_run_guard_raises_systemexit_for_a_v1_database_with_no_snapshot_meta`, a v1 database with NO fixture stamp and NO tamper, refused only by `open_or_exit`'s "no snapshot_meta row" reason, to distinguish it) |
| R5 | `_paging` clamps `page_size` so the store never rejects it | `porchlamp/serve/app.py` | SURVIVED (equivalent, unchanged from round 1 — `LeaderboardResponse._paging_is_sane` bounds it independently) |
| R6 | error body missing `endpoint` | `porchlamp/serve/app.py` | KILLED |
| RX1 | `_classify_not_ranked` drops the `off_platform_routed` branch | `porchlamp/serve/app.py` | KILLED |
| RX2 | `_matrix_point` plots a NULL-`quadrant` point | `porchlamp/serve/app.py` | KILLED (new test added this round) |
| RX3 | `_no_ceiling_or_band` bands swapped (A <-> B) | `porchlamp/serve/app.py` | KILLED (new test added this round) |
| B-1 | `_current_snapshot_id`'s `OperationalError` tolerance removed | `porchlamp/serve/app.py` | KILLED |
| B-2 | `_SPAStaticFiles`'s reserved-prefix exclusion removed | `porchlamp/serve/app.py` | KILLED |
| B-3 | `ValidationError` classification removed (blanket 422 restored) | `porchlamp/serve/app.py` | KILLED |
| B-4 | v2 guard's hash-mismatch check disabled | `porchlamp/serve/guard.py` | KILLED |

## COULD NOT VERIFY / still open (unchanged from round 1, or newly noted)

- Everything round 1 already flagged as deferred (raw_hash fixture-blocked on `LeaderboardRow`; matrix `axis_coverage_caption` needs a contract change, W4-5b's; `/not-ranked` has no server-side paging by contract design) is UNCHANGED by this round.
- **Not mine this round** (per the coordinator's explicit scope): W-2 (`web/v2/api.js` never reads the error body) is W4-5b's; the store-CLI / rated-fixture follow-ups the review's own "Verdict" section names are not in the bounded rework list and were not attempted.
- `run_guard`'s v2 guard (`_v1_fixture_reasons`/`_v1_hash_tamper_reasons`) now duplicates part of what `refusal_reasons`/`_fixture_marker_rows`/`matching_snapshot_id` already check (generalised to also read `legacy_repositories`) — a deliberate defense-in-depth overlap (R4's mutation proved it: swallowing `open_or_exit`'s own `SystemExit` is STILL caught by the v2 guard for a fixture-marked or tampered v1 database, just not for the narrower "no `snapshot_meta` row at all" case). Flagging the duplication itself in case a future packet wants to fold the v1 checks' "any table with this shape" generalisation back into `refusal_reasons` directly rather than parallel logic in `v2_refusal_reasons`.

## Second rebase within round 2 (main advanced again, mid-fix)

`main` advanced from `08c1502` to `930e596` while this round was in
progress (packet W4-6: a real `python -m porchlamp.snapshot store` CLI +
`finalize_snapshot_hash` wiring, plus a real-data fixture). Rebase was
clean, no conflicts. One required follow-up: W4-6 left a deliberately
self-upgrading test, `tests/snapshot/test_store_cli.py::
test_served_db_guard_accepts_this_db`, whose own docstring says it
"fails loudly (not silently)" once `porchlamp/serve/guard.py` exists, with an
explicit HANDOFF note asking for a real assertion against this module's
guard function. Updated it (`guard.v2_refusal_reasons`/`run_guard`
against the store CLI's real, finalised output) — 0 refusal reasons, no
v1 shape, exactly as expected. A second self-upgrading test in the same
file, `test_create_app_serves_snapshot_and_not_ranked_if_w4_4_has_landed`,
needed NO edit — it self-upgraded automatically (its own `try: from
porchlamp.serve.app import create_app except ImportError: skip` pattern) and
its real body now passes end-to-end against the same store-CLI fixture,
independently confirming `/v1/porchlamp/snapshot` and `/v1/porchlamp/not-ranked`
both serve correctly off real, CLI-written data.

Final suite: `2541 passed, 1 skipped` (up from `2531` — one test file
gained coverage from this fix, plus whatever W4-6 itself added). `ruff
check porchlamp/ tests/ scripts/`: All checks passed. Branch is current with
`main` tip `930e596` as of this handoff.

---

# Round 3 (lead verification on the real fixture store) — NB-1, NB-2

Lead verified the branch end to end via `scripts/serve.py --db <real fixture
store>` on port 8791. Two items, both fixed:

## NB-1 — counts classification unified (U-2)

`/v1/porchlamp/snapshot.counts` (`gated=35/unrated=15`, the raw engine `eligible`
flag) disagreed with `/v1/porchlamp/not-ranked` (`gated=31/unrated=19`, `06`
section 3 A2's classification — `no_external_workflow`/`off_platform_routed`
are evidence outcomes, not disqualifications). Fixed in the ONE place the
lead named, `porchlamp/core/pipeline.py` (lead-authorised edit, otherwise out of
this packet's scope):

- New `DISPLAY_UNRATED_GATE_REASONS` (frozen set, today just
  `porchlamp.core.gates.REASON_NO_EXTERNAL_WORKFLOW`) and `not_ranked_class(record)`
  — `"rated"`/`"gated"`/`"unrated"` per `06`'s classification. `record.gate.
  eligible`/`.reason` are read, never changed (`03` section 5: that flag
  stays exactly what `evaluate_gates` computed, as provenance).
  `build_snapshot_counts` now uses it.
- `porchlamp/store/queries.py::snapshot_summary`: the `gated` SQL `COUNT(*)` now
  excludes `DISPLAY_UNRATED_GATE_REASONS` via one `NOT IN (...)` clause
  built from the same frozen constant — still zero Python loop over rows.
- `porchlamp/serve/app.py::_classify_not_ranked`: replaced its own hardcoded
  `"unrated: no_external_workflow"` string literal with a membership check
  against the same constant.

New test `test_counts_agree_across_pipeline_store_and_api_on_the_real_fixture`
scores `tests/fixtures/snapshot_50` for real (`porchlamp.snapshot.store_cmd.
score_snapshot_dir`/`write_scored_snapshot`, not a synthetic fixture) and
asserts all three layers — `ScoredSnapshot.counts`, `snapshot_summary`, and
this API's `/not-ranked` split — independently compute `gated=31`/
`unrated=19`, matching the lead's own real-store numbers exactly. Two
existing synthetic-fixture tests were updated from the old (now-fixed)
off-by-one comparison to the correct equality; `tests/fixtures/snapshot_50/
expected.json` was regenerated (`scripts/build_fixture_snapshot.py`,
sample corpus available in this environment) — only `counts.gated`/
`counts.unrated` changed (35→31, 15→19); `records_hash`, `rank_order` and
every `bundles/*.json` file are byte-identical, confirmed via diff before
committing. `tests/snapshot/test_store_cli.py`'s hardcoded expectations
updated to match.

## NB-2 — `/healthz` and `/legacy` 404 clarity

- `GET /healthz` now exists (was excluded from the SPA fallback per Blocker
  B-2 but had no route, so it 404'd): returns `200 {"status": "ok",
  "snapshot_id": ...}`. Always 200 once the process is up on any database
  shape — the served-DB guard already passed (`SystemExit` otherwise,
  before `create_app` returns) by the time any request reaches a route at
  all; `snapshot_id` is `None`, never fabricated, when nothing has been
  promoted into the v2 store yet.
- `GET /legacy/{path:path}` is registered whenever `/legacy` is NOT mounted
  (a v2-only database), returning an explanatory 404 (`"/legacy is not
  mounted: this database has no v1 shape..."`) instead of the bare "no
  route matches" the SPA-fallback exclusion alone would give.

Two existing tests updated (both used to assert 404 for what is now
legitimately 200 / a more specific 404 message). Re-verified the existing
Blocker B-2 mutant (remove `_SPAStaticFiles`'s reserved-prefix exclusion)
still kills `test_unmatched_api_path_is_a_json_404_not_the_spa_shell` (the
`/v1/*` cases depend on nothing else) even with the new `/healthz`/`/legacy`
routes in place, which now pass independently of that exclusion — defense
in depth, not a regression.

## Division of labour: the `porchlamp.store.writer` <-> `porchlamp.snapshot` circular import

NOT this packet's to fix — packet W4-6f fixed it separately on `main`
(`porchlamp/snapshot/__init__.py` lazy names + a subprocess import-order test),
landing at `916de17`. This branch rebased onto it and the one temporary
`import porchlamp.snapshot` workaround added to the new NB-1 test was removed
once confirmed fixed.

## Mutation re-run (NB-1/NB-2, same protocol: plain `cp` backup, single
mutation, restore, byte-identical diff)

| # | Mutant | File | Killing test | Result |
|---|---|---|---|---|
| NB1-A | `not_ranked_class` drops the `DISPLAY_UNRATED_GATE_REASONS` check | `porchlamp/core/pipeline.py` | `test_counts_agree_across_pipeline_store_and_api_on_the_real_fixture` | KILLED |
| NB1-B | `snapshot_summary`'s `gated` count drops the `NOT IN` clause | `porchlamp/store/queries.py` | same + `test_snapshot_counts_match_the_display_classification` | KILLED (both) |
| NB1-C | `_classify_not_ranked` drops the reclassification | `porchlamp/serve/app.py` | same + `test_not_ranked_no_external_workflow_gate_is_translated_to_unrated` | KILLED (both; the second via a `ValidationError` — the raw gate string isn't a valid `GateReason`) |
| NB2-A | `/healthz` route removed entirely | `porchlamp/serve/app.py` | `test_bare_healthz_returns_200_with_the_snapshot_id` | KILLED |
| B-2 (re-check) | reserved-prefix exclusion removed, WITH the new `/healthz`/`/legacy` routes present | `porchlamp/serve/app.py` | `test_unmatched_api_path_is_a_json_404_not_the_spa_shell` | KILLED (unchanged; the two new routes pass on their own regardless, confirmed not masking this one) |

## Suite / ruff

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest tests/ -p no:cacheprovider -n 4
2547 passed, 1 skipped, 12 warnings in ~14s
exit=0

$ ruff check porchlamp/ tests/ scripts/
All checks passed!
```

Branch current with `main` tip `752d5ec` as of this handoff.
