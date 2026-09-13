# W4-4 handoff — API v2 (the endpoints the UI consumes)

Worktree `/home/pctablet505/Projects/cvi-wt-W4-4`, branch `W4-4`, base `main`
tip `3c5d99f` (via `90b2553`, the actual dispatch-time tip). Cherry-picked
`44b1d67` (W4-5a's `cvi/serve/contract.py` + `tests/fixtures/ui/*.json`)
first, as instructed.

## What was delivered

1. `cvi/serve/app.py` — the v2 API app factory: `create_app(db_path, *,
   web_dir) -> FastAPI`. Router `/v1/cvi`: `GET /leaderboard`, `GET
   /not-ranked`, `GET /matrix`, `GET /repos/{owner}/{name}`, `GET
   /repos/{owner}/{name}/evidence`, `GET /snapshot`, `GET /methodology`,
   `POST /reproject`. Every response goes through a `cvi.serve.contract`
   model via `response_model=`. Static mount of `web_dir` at `/` with an
   `index.html` SPA fallback (`_SPAStaticFiles`); v1's app
   (`cvi.api.create_app`) mounted unchanged at `/legacy` whenever the
   served DB has a v1 shape to guard (see item 2).
2. Served-DB guard: `scripts/serve.py` gained `open_or_exit(db_path) ->
   OperationalStore` (a pure extraction of `main()`'s existing three
   inline lines — zero behavioural change, `refusal_reasons`/message
   format untouched). `cvi.serve.app._run_served_db_guard` calls it; a
   `sqlite3.OperationalError` (a database that has never held v1 data at
   all — confirmed directly: `OperationalStore.__init__` itself raises
   against a v2-only `repositories` table, not just a later query) is
   treated as "nothing to guard", and `/legacy` is not mounted for such a
   database. A genuine v1-shaped refusal still raises `SystemExit` with
   today's exact message.
3. `scripts/serve.py` `main()` now builds `cvi.serve.app.create_app`
   instead of `cvi.api`'s app directly; same `--db`/`--port`/`$PORT` flags.
   `create_serve_app`/`refusal_reasons`/`matching_snapshot_id`/
   `count_repos` are untouched and still directly tested by the pre-
   existing `tests/test_serve.py` (15/15 still pass, unmodified).
4. `cvi/store/writer.py`: the one-line `stars_audited` producer — reads
   `item.bundle.inputs.get("stars_audited")` (a `Measured` value a future
   promote-pipeline packet places there; `FeatureBundle.inputs` has no
   construction-time validation, only the separate, never-called-here
   `.validate()`), `NULL` when absent or `Unmeasured`. Also updated the
   module docstring's stale "no producer" sentence. Did not touch the
   `last_human_commit_at` line (W1-5f's, left alone per the coordination
   note).
5. `cvi/serve/contract.py`: took `cvi-wt-W4-5a`'s LATEST file (which had
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
is in `cvi/serve/app.py`'s module docstring; summarised per endpoint below.

| Endpoint | Store source | Bridge |
|---|---|---|
| `GET /leaderboard` | `queries.list_ranked` | Item 1: under a persona, flat `rank/tie_group/cvi/coverage/tier` are the `_p`-suffixed values, never the (possibly-null) stored balanced ones. Item 2: `balanced_rank` = stored balanced `rank`; `rank_delta = balanced_rank - rank` (both `None` under balanced, or when `balanced_rank` is itself `None`) — confirmed sign convention against `contract.py`'s own validator. Item 7: `sort` accepts `p_<pillar>` and an optional `-` (stripped; `cvi.store.queries` has no ascending form — see COULD NOT VERIFY). Item 8: `page`/`page_size` → `limit`/`offset`; any value `_validate_paging` accepts. Item 12: `raw_hash` stays (fixture-blocked, see below). Every other field ((`p_*`, `c_*`, `leverage`, `friction`, `*_coverage`, `quadrant`, `tier_ceiling`, `persona_gates`, `persona_composites`, `suspicion`, `stars_audited`, `snapshot_id`, `benchmark_version`) is a direct 1:1 projection, named explicitly (`_project_leaderboard_row`) — no `**record` passthrough. |
| `GET /not-ranked` | `queries.list_not_ranked` + `queries.snapshot_summary` (for `counts`) | Items 3-5: `_classify_not_ranked` splits the store's one flat `reason` into `gated`/`unrated`/`persona_hidden` — `gate_reason == "unrated: no_external_workflow"` (a stage-6 GATE) → UNRATED group, `unrated_reason="no_external_workflow"` (lead's explicit choice over widening `GateReason`); `routed == "off_platform"` on an eligible repo → UNRATED, `unrated_reason="off_platform_routed"`; a persona-gated row (only under a non-balanced `persona`) → `persona_hidden`. No server-side paging (contract has none — `queries.list_not_ranked` is called with `limit=MAX_LIMIT` once; see COULD NOT VERIFY). |
| `GET /matrix` | `queries.matrix_points` | `_matrix_point` returns `None` (folded into `excluded_count`) when `leverage`/`friction`/`quadrant` is null on an otherwise-rated point (found via the fixture's own NULL-friction tie row, not assumed) — `MatrixPoint`'s three fields are all required. Item 11: `axis_coverage_caption` is left `{}` — the lead picked "the contract carries the raw numbers, the UI renders the sentence" over server-rendered captions, but `MatrixResponse` has no numeric field for `axis_partial_count`/`axis_partial_share` yet (`queries.matrix_points` already returns them) — deferred to W4-5b. |
| `GET /repos/{o}/{n}` | `cvi.store.reader.get_row` | `RepoDetailResponse` (this packet's addition) — direct 1:1 projection (`_repo_detail`), works for gated/unrated/rated alike; 404 when `get_row` returns `None`. |
| `GET /repos/{o}/{n}/evidence` | `queries.repo_evidence` | Returns `list[EvidenceRow]` directly (matches `tests/fixtures/ui/evidence-*.json`'s own shape — NOT the `{repo, params}` wrapper `repo_evidence` itself returns); 404 when the repo is unknown. Item 10: `pillar` is never coerced from `None` — an unknown `param_id` fails `EvidenceRow`'s non-null `Literal` as a 500 (lead's explicit ruling), asserted never to happen via the closed `PARAM_INPUT_IDS`/`PARAMS` set every written row is drawn from. |
| `GET /snapshot` | `queries.snapshot_summary` | Item 9: field-for-field, no bridging. Refuses (503) when `canonical_dump_hash` is `None` (not yet finalised via `finalize_snapshot_hash`) rather than crash on `SnapshotSummary`'s required `str`. |
| `GET /methodology` | `cvi.audit.methodology_history.methodology_with_history` | `response_model=MethodologyDocument` (the `TypedDict` from `cvi.core.methodology`, per the packet's own reading list — no pydantic model exists for this and none was invented). |
| `POST /reproject` | `queries.reproject` | Rows reuse the SAME `_project_leaderboard_row` core as the leaderboard, sourcing `rank/tie_group/cvi/coverage/tier` from the plain (already-custom) fields and `balanced_rank`/`rank_delta` from `record["balanced_rank"]`. `tier_ceiling` is RECOMPUTED (`_no_ceiling_or_band`, from `constants_v2.CEILING_A_MIN_COVERAGE`/`NO_CEILING_MIN_COVERAGE`) rather than passed through — `queries.reproject`'s own `tier_ceiling` column is the stale BALANCED value. `page`/`page_size` accepted as query params alongside the POST body (the contract has no paging fields for this endpoint; not specified by the dispatch, added for parity with the leaderboard's own pattern). |

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
on a clean full-suite rerun) — this packet never touches `cvi/store/
queries.py`.

## Mutation table (10/10 killed)

Each: backed up the file (plain `cp`, never `git stash`/`checkout --`),
applied the mutation, ran the named test(s) and confirmed a hard failure,
restored from the backup, re-ran to confirm green, then `diff`-verified
byte-identical restoration.

| # | Mutant | File | Test(s) killing it | Result |
|---|---|---|---|---|
| M1 | Drop `suspicion` from `_project_leaderboard_row`'s fields | `cvi/serve/app.py` | `test_leaderboard_suspicion_and_stars_audited_round_trip` | KILLED |
| M2 | Page off-by-one: `_paging` returns `page * page_size` instead of `(page-1) * page_size` | `cvi/serve/app.py` | `test_paging_pages_partition_the_full_set_in_order`, `test_leaderboard_balanced_default_shape_and_total` | KILLED (both) |
| M3 | Sort allow-list bypass: `_translate_sort` maps the vanity field `"stars"` to `"coverage"` | `cvi/serve/app.py` | `test_leaderboard_sort_pillar_translation_and_allow_list` | KILLED |
| M4 | Error body shape: `_error_body` drops the `"error"` wrapper | `cvi/serve/app.py` | `test_error_body_shape_is_consistent_across_404_422_503` | KILLED |
| M5 | Persona default: leaderboard's `persona` `Query` default changed to `"first_timer"` | `cvi/serve/app.py` | `test_leaderboard_balanced_default_shape_and_total` | KILLED |
| M6 | 404 → 200: unknown-repo `/evidence` returns `[]` instead of raising 404 | `cvi/serve/app.py` | `test_evidence_unknown_repo_is_404_with_consistent_body` | KILLED |
| M7 | Guard skipped: `_run_served_db_guard` always returns `None` | `cvi/serve/app.py` | `test_create_app_refuses_a_fixture_marked_v1_database` | KILLED |
| M8 | `tie_group` coerced: `tie_group or 0` instead of `tie_group` | `cvi/serve/app.py` | `test_leaderboard_tie_group_shared_by_ties_and_null_for_singletons` | KILLED |
| M9 | `stars_audited` fabricated: hardcoded `999` regardless of `Measured`/`Unmeasured`/absent | `cvi/store/writer.py` | `test_leaderboard_stars_audited_null_when_not_measured`, `test_leaderboard_suspicion_and_stars_audited_round_trip` | KILLED (both) |
| M10 | Methodology from a literal: `get_methodology` returns a hardcoded stub dict | `cvi/serve/app.py` | `test_methodology_matches_the_real_pure_document_and_its_hash` | KILLED (via `ResponseValidationError` — the stub is too shallow even for the schema) |

## Suite

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=$PWD .venv/bin/python -B -m pytest tests/ -p no:cacheprovider -n 4
2359 passed, 1 skipped, 12 warnings in 13.43s
exit=0

$ ruff check cvi/ tests/ scripts/
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
  `cvi.store.queries._build_order_by` has no ascending form at all (every
  branch is hard-coded `DESC`) — `_translate_sort` strips a leading `-`
  and never rejects it (per the lead's ruling), but `-cvi` and `cvi` order
  identically today. Needs a `cvi.store.queries` change to actually invert
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
  `index.html` for an unknown app-owned path, and `/v1/cvi/*` still wins
  routing over the static mount. `scripts/serve.py --db <real db>` now
  starts fully.

## Rebase notes

First rebase: based on `main` tip at dispatch time (`90b2553`, itself
descended from the named `3c5d99f`); `main` had advanced to `88ce3cc`
(W1-5f landing) by completion. One conflict, in `cvi/store/writer.py`'s
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
conflict, in `cvi/serve/contract.py` — this branch's own
pull-then-re-add-`RepoDetailResponse` commit against `main`'s now-landed
copy of the same file. Resolved exactly as instructed: took `main`'s
`cvi/serve/contract.py` wholesale (`git show main:... | diff` to confirm
byte-identical before editing further), then re-applied ONLY the
`RepoDetailResponse` addition on top (still not one of the W4-5a-review's
twelve bridge items, still not landed anywhere else) — confirmed
`balanced_rank`/`rank_delta`'s sign convention on `main`'s copy is
unchanged (`rank_delta == balanced_rank - rank`, matching this packet's
`_leaderboard_row`/`_reproject_row` all along; no code change needed).
Full suite green after both rebases: `2502 passed, 1 skipped` (up from
`2359` — `main`'s own landings added tests along the way). Branch is
current with `main` tip `d112aa2` as of this handoff.
