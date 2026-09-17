# CVI Deep Audit — 2026-09-17

Read-only adversarial review of the whole project. Branch `main`, no code changed by this
audit. Rules in force: `docs/REVIEW_GUIDELINES.md`, plus the AlgoTrading bedrock standards
it was derived from (`docs/bedrock/CODE_REVIEW.md`, `docs/standards/code_quality.md`,
`docs/standards/SECURITY_REVIEW.md`).

## How to read this

- 8 auditors worked **in parallel**, one per slice, each writing only inside its own
  anchored section below. Slice IDs are `A` … `H`; finding IDs are `<slice>-<nn>`.
- Severity vocabulary is the repo's own: **Blocker** (wrong result, data loss/corruption,
  crash, security hole, silent wrong answer) > **Warning** (real defect, limited blast
  radius) > **Consider** (suboptimal, contained) > **Nit** (style) > **FYI**.
- Every finding cites `path:line` the auditor actually opened, with the current code
  quoted. Anything the auditor could not check is marked `COULD NOT VERIFY:`.
- The consolidated register at the top is the merge pass: deduplicated, re-ranked, false
  positives removed. It is written last, by the merging agent. **Sections below are raw
  evidence; the register is the thing to act on.**

## Scope map

| Slice | Ownership |
| --- | --- |
| A-core | Scoring algorithm: `cvi/core/**` incl. `params/`, `cvi/pillars/**`, legacy top-level shims |
| B-collect | Data acquisition: `cvi/collect/**`, `scripts/gather.py`, `build_universe.py`, `backfill_downloads.py` |
| C-derive | Provenance + verification: `cvi/derive/**`, `cvi/verify/**`, `cvi/provenance.py`, `scripts/{derive,verify,promote}.py` |
| D-store | Persistence: `cvi/store/**`, `cvi/lakehouse.py`, `data/cvi.db` |
| E-snapshot | Reproducibility: `cvi/snapshot/**`, `scripts/{build_fixture_snapshot,make_sample,run_all}.py`, `canary.sh` |
| F-audit | Self-critique: `cvi/audit/**`, `cvi/{stage0,starscout,deepdive,playbook}.py` |
| G-serve | API + ops: `cvi/serve/**`, `cvi/api.py`, `scripts/{serve,serve_ui_fixtures}.py`, CI, packaging |
| H-web | Frontend: `web/v2/**`, `web/index.html` |

## Consolidated register

Merged from the eight slices below. Deduplicated by **root cause**, then re-ranked by blast radius
across slices — the grade an auditor gave inside its own slice is not the last word when the same
cause damages three layers. Every entry names its upstream IDs, so the owner can jump to the
evidence; nothing here is a new finding, and no finding was dropped. Severity changes are stated and
justified one by one in "Duplicates and disagreements reconciled" — nothing was softened or inflated
silently.

### If you fix only ten things

1. **Make a green pipeline mean something.** Four separate mechanisms, four separate fixes, one
   systemic failure. Closes `G12`, `G13`, `H4`, `F-02`, `F-01`, and the gate half of `H2`.
   - CI fires only when a person remembers: `workflow_dispatch` is the only trigger
     (`.github/workflows/ci.yml:4`) → add `push` and `pull_request`.
   - the JS suite skips itself when `node` is absent (`tests/test_ui_node_suite.py:20`) → fail unless
     an explicit flag allows the skip.
   - the release gate returns 0 whatever it finds (`cvi/audit/sensitivity.py:540`) → return 1 on a
     failed gate, and call the CLI from CI so a verdict reaches something that acts.
   - the newcomer anchor has no raw input to compare against
     (`cvi/audit/anchors.py:676`) → persist the raw count, return NOT-APPLICABLE when it is absent.
   This cluster is why 7 of the 18 Blockers (`H1`, `H3`, `G1`, `G2`, `G8`, and both F Blockers) could
   reach `main` with no signal.

2. **Write envelopes atomically and never write one for a failed fetch.** Closes `B1`, `B2`, `C-02`,
   and B's `B:no-resume` and `B:backfill-tmp` tail items. The fix is already in the repo at
   `scripts/backfill_downloads.py:122-133` (`.tmp` + `os.replace`). Blast radius: one interrupted
   write turns 0 releases / 0 PRs / 0 dependents into a permanent *measured* zero in the published
   score with `--force` as the only recovery, and one torn file aborts every derive over that tree.

3. **Stop re-deriving totals from a truncated page.** Closes `G1`, `G4`, and the count semantics in
   `G3`. `/reproject` serves 20 of 285 eligible rows in production today (measured live), `/not-ranked`
   under-reports and then contradicts `/snapshot` the moment a bucket crosses 1000.

4. **Give the tier vocabulary one owner, and stop renaming `S+`.** Closes `H3`, `G8`, and the boundary
   half of `D-03`. The engine emits `S+` (`cvi/core/constants.py:128-137`, re-verified at HEAD);
   `web/v2/copy.js:107` collapses it to `S`, and the fixture's invented ordering
   (`scripts/serve_ui_fixtures.py:130`) sorts `S+` behind `F` — which is how the client bug survived
   review. The top 1% of the benchmark is displayed as plain `S`, contradicting three published docs.

5. **Make `reproject` use the canonical aggregation instead of re-deriving it in SQL.** Closes `D-01`,
   `D-03`, `A-05`. A repo with one `not_applicable` pillar is published a tier lower on the
   custom-weight endpoint, and one returned row asserts its coverage is both 1.0 and 0.8.

6. **star history: implement or unbind the transport, then put it in the coverage inventory.** Closes
   `B7`, `F-08`. Stars never enter the composite (`cvi/core/constants.py:277-279`), so no score is
   wrong — but StarScout's universe-entry filter and the `star_burst` detector can never fire in a
   real run (`data/raw/` has no `star_history/` directory), the reader raises through a docstring that
   promises it never raises (`cvi/collect/universe.py:272`), and the coverage auditor's collector lists
   were drawn without it (`cvi/audit/coverage.py:71-100`), so a 100%-unmeasured input is invisible to
   the tool that reports unmeasured coverage.

7. **Make the shipped tree servable, and stop writing to the inode the server holds open.** Closes
   `G11`, `D-02`, `G5`, `G10`. `scripts/serve.py` refuses the only database it defaults to, no CI step
   builds a servable one, and the first v2 promote against a served DB meets rollback-journal locking
   with no `busy_timeout` and a guard that verified a different snapshot than the one being served.

8. **Make the sweep survive itself and cost what the plan says.** Closes `B5`, `B6`, `B4`, `B8`, `B9`,
   and B's `B:retry-storm` and `B:cost-accounting` warnings. One `gh` call over 90 s ends a six-hour
   sweep with no per-repo `try`; governance alone is ≈52 h against a plan that budgets 45–50 h for the
   whole sweep; the universe's selection column ranks GitHub stars against crates.io downloads; and the
   module meant to enforce budgets has no readers at all.

9. **Route read-path attestation through a real verifier, and stop defaulting absence away.** Closes
   `C-01`, `C-04`, `C-06`, `C-07`, `F-03`, `F-07`, and B's `B:commits-cap`, `B:branch-main`,
   `B:dedup-dependents`, `B:downloads-key` warnings. This is the class that produces provenance which
   is well-formed, hash-consistent, and describes the wrong thing — the one class `verify` cannot see.

10. **One definition per fact, enforced by parity tests rather than care.** Closes `E-02`, `G3`, `G9`,
    `H5`, `H7`, `H6`, `A-01`/`A-02`, `F-05`, `F-06`, and B's `B:duplicate-constants`, `B:spelled-paths`,
    `B:retryable-ecosystems` items. The repo already has the right mechanism — one shared JSON fixture
    read by both `tests/test_ui_weights_port.py` and the JS suite — and uses it in exactly one place.

Plus one line that is not in the ten because it costs nothing: `web/v2/views/drawer.js:351` reads
`copy.EVIDENCE_RETRY_LABEL`, which is never exported, so the drawer's error button renders the word
"undefined" (`H1`).

### Blocker

- **`write_envelope` is non-atomic while every resume probe is a bare existence check** — `B1` (cause of
  `C-02`, and of `B7`'s raise path) — `cvi/collect/envelope.py:232` — silent permanent measured-zeros in
  the score; `--force`-only recovery; upstream cause of the derive-side outage. Same root cause one level up:
  a multi-call collector emits one envelope only at the end, so a failure on page 4 of 5 pays all five pages
  of rate limit again (`B:no-resume`, folded here) — `cvi/collect/github_graphql.py:278-320`, `:575-621`.
- **A failed keyless fetch is written as a permanent provenanced null stamped `http_status=200`** — `B2` —
  `scripts/gather.py:97-103` — a transient 503 becomes "this project has no dependents", and the run
  reports `fail` while laying down the envelope that marks it done forever.
- **`repo_object` discards a whole envelope on one partial GraphQL error** — `B3` —
  `cvi/collect/repo_object.py:285` — `owner`, `hasIssuesEnabled`, license and stars already in hand are
  thrown away; the salvage path exists in `GhCliTransport` and was never propagated.
- **`_shallow_tree`'s frontier is uncapped and records no `truncated` flag** — `B4` —
  `cvi/collect/repo_object.py:330` — a monorepo costs 61+ REST calls against a 1–2 call budget; a
  budget-aborted tree is byte-identical to a complete one on disk.
- **One unguarded exception ends a multi-hour sweep** — `B5` — `scripts/gather.py:496` — six hours in, one
  `gh` call over 90 s kills the process and everything after repo ~3,000 is silently absent.
- **Governance is under-budgeted on cost and under-evidenced on correctness** — `B6` —
  `cvi/collect/governance.py:308` — ≈45 REST/repo (≈52 h for the universe), `404 → continue` drops
  evidence unflagged, `legal_ip` and `promotion_ladder` classify from a 3-PR sample.
- **star history has no live transport and is absent from the coverage inventory** — `B7` + `F-08` —
  `cvi/collect/universe.py:212` — universe-entry filter and `star_burst` detector permanently inert;
  coverage report green with 100% of one input absent. Composite impact is zero (D-IN-5).
- **The universe's selection column mixes GitHub stars with crates.io downloads; crates.io is one page** —
  `B8` — `scripts/build_universe.py:87` — the cut point is an arbitrary unit mismatch, and one failed page
  permanently deletes a criterion.
- **`cvi/collect/ratelimit.py`: 332 lines, zero readers** — `B9` — `cvi/collect/ratelimit.py` — the
  enforcement mechanism `08` §4 leans on does not exist to the running code, cannot express a points
  budget, and its cache would not survive a rerun.
- **The `newcomer_label_5_1` anchor cannot fail on any input** — `F-01` — `cvi/audit/anchors.py:676` —
  every world yields vacuous PASS, score-restating PASS, or a boilerplate FAIL the test pins as expected;
  the one anchor whose job is to compare a score to its input never does.
- **The release gate exits 0 unconditionally and real data has no consumer** — `F-02` —
  `cvi/audit/sensitivity.py:540` — a ranking that fails every criterion ships green. See the
  reconciliation note: its one mechanical consumer is a fixture assertion that passes vacuously.
- **`/reproject` silently pages 20 rows while the UI assumes the whole set** — `G1` —
  `cvi/serve/app.py:664` — Screen D shows 20 of 285 eligible repos today; the fixture server and the two
  tests that touch the endpoint both hide it.
- **`/matrix` ships `axis_coverage={}` with a renderer and no producer** — `G2` — `cvi/serve/app.py:628` —
  the missing-pillar caption is dead in production *and* in the fixture; the per-axis counts exist only as
  a private copy inside `scripts/serve_ui_fixtures.py`.
- **`/not-ranked` truncates at 1000 and re-derives its counts from the truncated page** — `G4` —
  `cvi/serve/app.py:601` — the store's authoritative `_counts_by_reason` is discarded; the mechanism is
  certain, the symptom waits for a bucket to cross 1000.
- **The drawer retry button renders the literal word `undefined`** — `H1` — `web/v2/views/drawer.js:351` —
  user-facing on the most-trafficked error path.
- **The JS integrity tests structurally cannot see a phantom `copy.*` name** — `H2` —
  `tests/test_ui_js_integrity.py:154` — `import * as copy` plus a namespace check that only asserts
  "target has some exports" leaves ~600 property reads unchecked; this is the delivery path for `H1`/`H3`.
- **The UI silently renames tier `S+` to `S`** — `H3` (fixture cause `G8`, boundary sibling `D-03`) —
  `web/v2/copy.js:107` — the highest tier in the methodology is invisible to a reader of the UI and
  contradicts `docs/UI_UX_SPECIFICATION.md:334`, `docs/ARCHITECTURE.md:525`,
  `docs/BENCHMARK_SPECIFICATION.md:38`.
- **Neither CI nor the canonical invocation gates the frontend** — `G12` + `G13` + `H4` —
  `tests/test_ui_node_suite.py:20` — CI is manual-trigger-only (`ci.yml:4`) and the Python suite skips the
  whole JS layer silently on a host without `node`. Re-ranked; see reconciliation.

### Warning

- **The legal-IP regime enum is defined three times with three different answers** — `A-01`, `A-02` —
  `cvi/core/constants.py:162` — a spec-published level raises `ValueError` in scoring, and legacy rows are
  silently re-scored (RECIPROCAL 40→25, UNILATERAL 10→25).
- **The published methodology advertises a persona-conditional score the engine cannot produce** — `A-03`
  (+ `A-04`) — `cvi/core/engine.py:213` — `METHODOLOGY-v2.md:71` renders a per-persona table no code path
  reaches, because the engine calls every non-ECDF input with no persona.
- **One torn raw envelope aborts every derive over the tree** — `C-02` — `cvi/derive/bundle.py:945` —
  the derive-side half of `B1`; kept at Warning so the outage is not counted twice against the same cause.
- **The loader and the certification chain run on the wall clock** — `C-05`, `E-03` —
  `scripts/derive.py:268-272` — the same `derived/*.json` loaded twice across a window boundary scores
  differently, `gr14_ghost_rate` and `dx` are permanently unmeasurable, and a fresh sample→fixture rebuild
  is not byte-reproducible but skips in CI.
- **The bundle loader is self-attesting: derive-time hashes are recorded and never compared** — `C-01` —
  `cvi/derive/bundle.py:950` — a consumer of `derived/*.json` that does not go through `promote` cannot
  detect an in-place envelope edit.
- **`reproject` cannot see D-MISS-5, so coverage and tier diverge from the canonical persona path** —
  `D-01` — `cvi/store/queries.py:960` — reproduced numerically: coverage 1.0 vs 0.8, tier S vs A.
- **No WAL or `busy_timeout`, and the v2 write path mutates the served database in place** — `D-02` —
  `cvi/store/schema.py:170` — the first v2 promote against a served DB risks `database is locked`
  mid-promotion; v1's temp-file + `os.replace` discipline was not carried across.
- **The snapshot directory's Merkle root omits `populations.json`** — `E-01` —
  `cvi/snapshot/build_cmd.py:174` — two dirs with one `merkle_root` can score differently; the DB dump
  hash covers populations, the directory hash does not.
- **Two different `constants_hash` definitions share one field name** — `E-02` —
  `cvi/snapshot/manifest.py:163` vs `cvi/core/constants.py:600` — measured to differ at runtime; a reader
  mis-identifies which tuning constants a legacy snapshot was frozen against.
- **Forward evaluation coerces missing outcomes to 0.0, which PREREG does not authorize** — `F-03` —
  `cvi/audit/forward.py:215` — absent data becomes an observation of zero and feeds the significance
  statistics with no trace in the report.
- **The forward report has nowhere to put the S0 cap disclosure PREREG requires** — `F-04` —
  `cvi/audit/forward.py:139` — a frozen pre-registered obligation is unmet, on the most influential
  observations in the validation design.
- **"Rated universe" has two definitions with no declared equivalence** — `F-05` —
  `cvi/audit/forward.py:209` — the same pre-registered statistic is computed over two different
  populations depending on which branch runs.
- **Archived/awesome anchors answer the same empty population three different ways** — `F-06` —
  `cvi/audit/anchors.py:909` — anchor reports are not comparable across snapshot/DB or v1/v2 runs, and a
  correct universe can fail its own anchor.
- **The detectors report reads a missing `measured` key as "not measured"** — `F-07` —
  `cvi/audit/detectors_report.py:96` — a producer bug collapses "could not express itself" into "tested and
  fine" in the one artifact whose job is coverage.
- **Three incompatible definitions of `excluded_count`** — `G3` — `cvi/store/queries.py:774` — measured
  27 vs the store's own 26 in one call; the published number is not reproducible from any documented rule.
- **The guard verifies a snapshot the server may not serve** — `G5` — `cvi/serve/app.py:711` — boot-time
  bytes are hashed, the served row is re-picked per request by a second implementation of the same rule;
  `OperationalError` degrades to a 503 rather than a refusal.
- **No `Cache-Control` or `ETag` on snapshot-immutable, content-hash-pinned data** — `G6` —
  `cvi/serve/app.py` (all v2 handlers) — a discarded `snapshot_summary` is ≈73% of a leaderboard request
  (27.40 ms median of 37.58 ms at 10k rows), repaid on every page turn.
- **A production 500 leaves no trace** — `G7` — `cvi/serve/app.py:147` — the traceback is discarded by a
  handler whose own comment says it exists for faults nobody anticipated; no logger in the module.
- **The fixture enforces `LEADERBOARD_PAGE_SIZES` and production does not** — `G9` —
  `scripts/serve_ui_fixtures.py:621` — the stand-in is stricter than the thing it stands in for, so a
  client built against the demo encodes an assumption production does not share.
- **The working tree as shipped cannot be served at all** — `G11` — `scripts/serve.py:71` — the guard
  correctly refuses the default database (reproduced), and no CI step builds a servable one; the only
  working demo is the fixture server, which is a different implementation.
- **`escapeHtml` is defined 13 times while an exported one already exists** — `H5` —
  `web/v2/components/table.js:63` — escaping is the code where a non-propagating fix matters most.
- **Below-floor coverage rows wear the B-band badge** — `H6` — `web/v2/components/badge.js:18` —
  `.badge-coverage-below` does not exist in the stylesheet, so a data-quality warning reads as an ordinary
  B.
- **Cross-language constants are hand-mirrored with no parity test** — `H7` — `web/v2/state.js:21` —
  page sizes, persona weights, sort fields and pillar order are transcriptions; `PARAM_NAMES` carries 9
  dead keys, each with a live twin holding the same label.
- **Contract drift that misreports state to the user** — `H7b` — `web/v2/copy.js:394` — "showing your last
  good data" is announced on a first-load failure with no prior data; `configureDeltaText` renders `NaN`
  for a null `balanced_rank` (`copy.js:471`).
- **The eligibility retry storm puts all its cost on the failure path** — `B:retry-storm` —
  `cvi/collect/github_graphql.py:736` — one deleted login in a 15-login chunk costs ~19 extra GraphQL
  calls; a whole-batch error leaves every login in it unresolved.
- **Cost accounting that gates nothing** — `B:cost-accounting` — `cvi/collect/github_graphql.py:885` —
  `rate_limit_remaining` is recorded and read by nobody; a failed call is booked as `cost = 0`.
- **Pagination ends on a shape check, so a short list and a truncated list are byte-identical** —
  `B:pagination-shape` — `cvi/collect/github_graphql.py:316` — a partial-error response records
  `truncated: False` over a short list.
- **`commits_365d` is capped with no flag, and zero commits scores maximum concentration** —
  `B:commits-cap` — `cvi/collect/commits.py:185` — a repo at the 1000 cap looks like one with exactly
  1000 commits, and `total == 0` returns `1`, identical to a genuine single-author project.
- **`dedup_dependents` unit conflation means dedup silently does nothing** — `B:dedup-dependents` —
  `cvi/collect/depsdev.py:315` — D-PARAM 2.1's anti-double-count rule is trusted blindly by derive and
  never sees deduplicated data.
- **The `branch = "main"` fallback becomes a scoring penalty** — `B:branch-main` — `scripts/gather.py:165`
  — a `master`/`trunk` repo queries a branch with no runs, gets 0, and `pipeline_defenses` applies "0 if
  no CI"; the envelope never says the branch was guessed.
- **Dead shims probe another module's private signature** — `B:dead-shims` — `scripts/gather.py:329` —
  `inspect.signature(_ro_mod._fetch_body)` against a one-parameter function, so a branch is unreachable.
- **Collection constants and envelope paths are re-spelled instead of imported** — `B:duplicate-constants`,
  `B:spelled-paths`, `B:retryable-ecosystems` — `scripts/gather.py:457` and `:464` (one `User-Agent`
  spelled twice inside one function) — 11 hand-spelled `raw/<collector>/<key>.json` paths while
  `relative_envelope_path` exists; `RETRYABLE_ECOSYSTEMS` is a second source of truth.
- **The downloads key drops the owner** — `B:downloads-key` — `scripts/gather.py:257` — registry names are
  global and GitHub repos are not, so two tracked repos on one crate invisibly share one number.
- **Unvalidated `owner`/`name` reach a GraphQL string literal and a URL path** — `B:unvalidated-owner` —
  `scripts/gather.py:136` — a stray quote is either a syntax error (`B5`) or a 404 read as absence (`B2`);
  `github_graphql.py:78-93` shows the variable-binding alternative.

### Consider

- **The persona-composite rule exists twice, in Python and in SQL, with no test that they agree** — `A-05` —
  `cvi/core/pipeline.py:192` vs `docs/v2/02-RANKING-DESIGN.md:276-277` — the same two-implementation shape
  that already produced `D-01`.
- **The benchmark spec still prints v1 gradients** — `A-06` — `docs/BENCHMARK_SPECIFICATION.md` §5 — a
  reader calibrating against §5 gets −10.0/+0.60 where the shipped engine is −7.5/+0.525.
- **A dead store makes the absolute-path fallback unconditional** — `C-03` — `cvi/derive/bundle.py:936` — a
  source merely outside `raw_root` kills every field instead of one.
- **`paths[0]` names one envelope for a value the collector computed from several** — `C-04` —
  `cvi/derive/bundle.py:1624` — provenance that verifies cleanly and names the wrong file; for the
  `pipeline_defenses` proxy an absent `pr_window` becomes a *zero* `n`.
- **Source hashes can come from a different data root than the run** — `C-06` — `scripts/derive.py:116` —
  breaks the self-contained-copied-dir property for hashes while the paths stay relative.
- **The `bodies/` cache is backfilled provenance, null-as-measured, and HEAD-dependent** — `C-07` —
  `scripts/derive.py:~178-196` — `{"body": null}` is indistinguishable from a fetch failure, and the fetch
  targets `HEAD`, not the repo's `default_branch`.
- **`cvi/provenance.py` is a test-only schema that reads as production** — `C-08` — `cvi/provenance.py` —
  two provenance schemas coexist with nothing marking which is authoritative, plus a live-looking `RAW_DIR`.
- **`reproject` tiers on the full-float composite; canonical rounds to 12 places first** — `D-03` —
  `cvi/store/queries.py:980` — off-by-one tier at an exact cutoff, and `RATIO_DECIMALS` lives outside
  `constants.py`.
- **The read path breaks its own "no Python loop" invariant and defeats its ranking index** — `D-04` —
  `cvi/store/queries.py:772` — `EXPLAIN QUERY PLAN` shows `USE TEMP B-TREE FOR ORDER BY` because
  `ROUND(cvi, 2)` makes `idx_rank`'s ordering columns unusable; `reproject` scans the set twice.
- **The evidence drawer can 500 on a stored value the contract cannot represent** — `G10` —
  `cvi/store/queries.py:789-849` — no evidence block exists for this ID; it is recorded only in G's
  Coverage note, where it was left at Consider for want of a real snapshot to test against.
- **`AGENTS.md`/`CLAUDE.md` counts as an AI policy file** — `B:agents-file` — `cvi/collect/governance.py:95`
  — any repo carrying an agent config classifies as `REGULATED`, and the module concedes `PROSCRIBED` can
  never fire, so the `ai_assisted` hard-ban gate is dead by construction — the same shape `F-01` graded
  Blocker in the audit layer.
- **Governance stores 12 full document bodies per repo in the dearest envelope** — `B:bodies-in-envelope` —
  `cvi/collect/governance.py:406` — against `08` §3 and `03` §3, which say documents are not 3.1 evidence;
  the `bodies/` cache already shows the on-demand alternative.

### Nit

- **D-GATE-5's population rests on an invariant no one asserts** — `A-07` — `cvi/core/gates.py` — a caller
  passing a differently-scoped `pr_window` gets wrong gates with no error.
- **Cosmetic and defensive-code tail** — `A-08` — `cvi/core/constants.py:586` (one sentence emitted twice),
  `cvi/core/gates.py:325` (comment words the code backwards), `cvi/core/tiebreak.py` (`coverage` typed
  `float | None`, treated as non-null), `cvi/core/params/curves.py:24-40` (four re-bound constants).
- **Store nit tail** — `D-04` tail — `cvi/store/queries.py:697` (`json_each` full scan), `:628`
  (`list_not_ranked` Python-slices the whole set, in-code justified), `cvi/store/ops.py` (v1-only
  leaderboard cache).
- **The audit tooling reads tie-break layers by tuple position** — `F:tie-key-positional` —
  `cvi/audit/sensitivity.py:610` — `record.tie_key[5]`/`[6]` against `cvi/core/tiebreak.py:259`; a reorder
  silently re-points the comparison with no test to catch it.
- **More "answer for missing data" defaults, and a wrong annotation** — `F:coverage-default` —
  `cvi/audit/sensitivity.py:376` — a missing `c_*` column becomes 1.0; `SensitivityReport.pp` is annotated
  `int` and carries fractions.
- **Audit tooling nit tail: dropped negatives, duplicated constants, mislabeled anchor** —
  `cvi/audit/coverage.py:273` (negative latencies silently dropped), `:109` (black-hole score hardcoded
  beside the METHODOLOGY text instead of derived from it), `cvi/audit/anchors.py:74` (a completeness check
  wearing a coverage label).
- **Serve nit tail: nine queries for an immutable answer, and two contracts under one version string** —
  `cvi/store/queries.py:668` (`SELECT * FROM snapshots` twice per call), `:674-703` (population counts and
  a per-persona `COUNT` each call), `cvi/serve/app.py:562` (the v2 router is still prefixed `/v1/cvi` while
  v1 is mounted at `/legacy`), `cvi/api.py` (the legacy surface registers no exception handlers and never
  passes the guard).
- **Packaging tail** — `pyproject.toml` — no `dependencies` key, so `pip install cvi` cannot serve;
  `requires-python = ">=3.10"` against ruff `target-version = "py312"`; no `[project.scripts]` entry, which
  is why serving means knowing the path to `scripts/serve.py`.
- **Frontend nit tail** — `web/v2/app.js:976` (`void state;`), `:1654` (debug handle ships in production),
  `:157` (registry list hardcoded, so a new upstream registry is silently absent from the UI),
  `web/v2/components/table.js:98` (`!== null` does not exclude `undefined`).
- **Interrupted backfills strand `*.json.backfill-tmp` beside real envelopes forever** — `B:backfill-tmp` —
  `scripts/backfill_downloads.py`.

### FYI

- **Flaky v1-era test race outside the v2 core** — `A-09` — `tests/test_backfill_downloads.py:103` —
  intermittent red from an assertion that can beat the handler thread's `seen.append`.
- **DR_90 is recomputed from raw `pr_window`; one open question about cap-vs-`min_n`** — `C-09` —
  `cvi/derive/bundle.py:1359` — recomputation is the right call; whether `min_n` gates the capped or the raw
  cohort is not settled by any text either auditor could find.
- **Determinism confirmed on the v1 reproduce path and the promote path** — `E-04` and E's low-severity tail
  — `cvi/snapshot/reproduce.py`, `scripts/promote.py:206-231`, `cvi/snapshot/store_cmd.py:180-234` — the
  dump hash is float-normalised and row-sorted, `as_of`/`created_at`/`promoted_at` all come from
  `frozen.as_of`, and the pass-1 constant ECDF is intentional.
- **Plan-only mode picks the lexicographic first database and rewrites the report anyway** — `F:plan-mode-db`
  — `cvi/audit/forward.py:669`.
- **The release gate partly measures the tie-breaker against itself** — `F:tau-vacuous` —
  `cvi/audit/sensitivity.py:554` — the canonical is scored as the stored rank, so on a fixture where
  composites tie, tau = 1.0 and the gate passes vacuously.
- **The report header cites `docs/bedrock/*` and `docs/standards/*`, and neither directory exists here** —
  `G:FYI-docs` — no rule was cited from either.

### Duplicates and disagreements reconciled

- **star history (`B7` + `F-08`) is one root cause with two blast radii, and it does not move the
  composite.** `cvi/collect/universe.py:206-215`'s `NotImplementedError` is the cause; B read it as "every
  candidate is `Unmeasured(collector_failure)` and the reader can raise through a never-raises contract", F
  read it as "the coverage auditor's denominator was built without the collector". Kept as one Blocker at
  `B7`'s grade. Severity is *not* raised on scoring grounds: `cvi/core/constants.py:277-279` states plainly
  that stars never enter the composite (D-IN-5), so the real loss is universe-entry filtering and the
  `star_burst` suspicion detector, both permanently inert, plus a raise path that B1 makes reachable.
- **`G12` re-ranked Warning → Blocker** as part of the CI cluster. Reason: its blast radius is not the
  missing trigger but the delivery of every other defect — `G1`, `G2`, `G4`, `G8`, `H1` and `H3` all merged
  with no CI signal. `G13` and `H4` are one finding seen from two sides (`H4` was already Blocker; the
  merged entry keeps that grade). Note the precise scope: `ci.yml:26` does run `node --test`, so a
  dispatched CI run *does* exercise the frontend — the defect is that dispatch is manual and the canonical
  local invocation skips silently, not that the JS suite never runs.
- **`F-02`'s "nothing in CI runs the gate" is too broad, and the finding survives the correction.**
  `.github/workflows/ci.yml:31-35` runs `tests/audit/test_sensitivity_fixture.py`, which asserts
  `report.all_pass is True` (`:76`), so the verdict does have one mechanical consumer — on the fixture
  snapshot only, where F's own FYI records the gate passing vacuously at tau = 1.0. What stands: the CLI
  exit code is unconditional (`sensitivity.py:540`), no test pins a non-zero exit, and nothing runs the
  gate against real data. Blocker either way.
- **Torn envelopes: `B1` is the cause, `C-02` is the consequence, and the dependency is kept visible
  rather than flattened.** `C-02` stays Warning even though it is an outage, because grading it Blocker too
  would count one failure twice; `B1` sequences first, exactly as C wrote.
- **`A-02`'s impact line corrected against source.** `cvi/constants.py:89-94` scores RECIPROCAL 40.0 and
  UNILATERAL 10.0; `cvi/derive/bundle.py:52-58` maps both to `CORP_CLA`, which scores 25.0. The collapse is
  15 points *harsher* for reciprocal and 15 points *more lenient* for unilateral — A's "15 points harsher"
  describes only the reciprocal branch. Both directions belong in the fix note.
- **`D-01` kept at Warning although it is a silent wrong tier**, which the vocabulary would put at Blocker.
  Reason: the Warning clause that holds it down is real — only the custom-weight endpoint reads that SQL,
  and only for repos carrying a `not_applicable` pillar. It is in the top ten regardless of grade, because
  "wrong tier" outranks most of what outranks it.
- **`G4` kept at Blocker with its magnitude flagged as future-dated.** G measured the mechanism and graded
  present-day magnitude medium; `G1` is the same class already visible in production. The register ranks
  `G1` above `G4` for that reason and changes neither grade.
- **`G2` kept at Blocker, ranked below every wrong-number Blocker.** The Blocker verdict comes from the
  repo's own verbatim rule (a published symbol with no counterpart is a Blocker), not from blast radius,
  which is a caption that never renders. Rule-based severity is not automatically top-of-queue.
- **`B:agents-file` is graded Consider by B and is the same shape as `F-01`'s Blocker** — a gate that
  cannot fire. B's grade is kept because the blast radius is one posture bucket in v1-derived data, not a
  published certification report; it is filed under the gate-never-fails theme all the same.
- **`A-01`/`A-02` merged as one enum-definition finding**, and `A-04` merged into `A-03` as the
  doc-side half of the same "published methodology over-promises" defect. `E-03` merged into `C-05` (wall
  clock where an `as_of` is required, at two layers). B's `B:duplicate-constants`, `B:spelled-paths` and
  `B:retryable-ecosystems` merged into one transcription finding. `G8` folded into the `H3` tier entry as
  its upstream fixture cause. `F-08` folded into `B7`. `H4` folded into the CI entry. B's `B:no-resume`
  tail item folded into the `B1` bullet: it is the same root cause (one envelope per collector, existence
  checks as resume probes) observed one level up, at the page loop rather than the file write, and a
  separate entry would let a fixer repair the write and leave the page loop alone. No ID was dropped;
  each fold is named in both places.
- **`S+` across the slices: no disagreement, and slice A corroborates H.** Slice A read
  `cvi/core/constants.py` in full and filed nothing against the tier tables; `cvi/core/constants.py:128-137`
  carries `(90.0, "S+")` and a six-name `TIER_ORDER`, re-verified at HEAD. So `H3` is a client-side defect
  alone, `G8` is the fixture that hid it, and `D-03` is a third place the same 90.0 boundary is evaluated,
  under a different rounding rule.
- **Not settled by this merge, and the reason.** `F-05`: whether v2 `rated` ever diverges from `NOT
  is_stage0_disqualified` in a real snapshot — nobody sampled one. `C-02`/`C-04` trigger rates and whether
  any live `data/raw/` envelope is torn — the tree was deliberately not scanned or opened for writing while
  a collector is running. `D-01`'s precondition (does a production persona repo carry a `not_applicable`
  pillar) — not sampled. `F-03`'s prevalence of NULL outcomes in the shipped snapshot — not sampled. `G4`'s
  present-day bucket sizes — not measured. `C-09`'s `min_n` intent — the spec text defining the DR_90
  denominator was not found by either auditor. `E-01`'s severity — it depends on whether the certified
  artifact is the directory or the DB, and the repo has not decided. `G12`/`H4`'s "is dispatch triggered
  diligently" — three auditors marked it COULD NOT VERIFY. B's deps.dev system ids — no network.
- **Totals in the brief corrected:** the per-severity figures are right for *headed* findings and the total
  is not. See the next subsection.

### Honest totals

**Before merging.** `grep -c '^### .* — <severity>'` over this file: 18 Blocker, 29 Warning, 11 Consider,
2 Nit, 3 FYI = **63 findings with a severity in the heading**. The brief's 18/29/11/2/3 reproduce exactly;
its total of 71 does not — 63 is the headed count. The file also carries **40 unnumbered tail bullets** (B:
11 Warnings + 5 lower-severity items, `A-08`'s sub-bullets excluded, D: 3, E: 3, F: 8, G: 6, H: 4) and one
finding that exists only as a parenthetical in a Coverage note (`G10`, a Consider with no evidence block).
All-in, this audit filed **104 discrete entries**: 18 Blocker, 40 Warning, 16 Consider, 21 Nit-or-below,
9 FYI. B's five unlabeled tail bullets are counted by content — three rule violations as Consider, two
housekeeping items as Nit — and B's own numbering skips from `B9` to unnumbered prose.

**After merging: 80 entries** — 18 Blocker, 34 Warning, 12 Consider, 10 Nit, 6 FYI — covering all 104 raw
entries. The Blocker count is unchanged at 18 because the folds cancel: `H4` is absorbed into the CI entry
(which also absorbs the Warnings `G12`/`G13`), while `F-08` and `G8` are absorbed into existing Blockers
rather than adding entries. Warning drops 40 → 34 by `A-01`/`A-02` and B's three transcription items
collapsing, and by `G8`, `G12`, `G13` moving out of the group. FYI drops 9 → 6 by folding `E-04` together
with E's three low-severity tail notes into one determinism entry.

---

## Slice A — scoring algorithm

<!-- BEGIN A-CORE -->
### A-01 — Warning — `CORP_CLA_RECIPROCAL` is published in the spec but unscoreable in v2

**Where** — `cvi/core/constants.py:162-168`
```python
#: 3.1 legal-IP regime scores (owner decision O-1: NONE 85).
LEGAL_IP_SCORES: Mapping[str, float] = MappingProxyType({
    "DCO": 100.0,
    "NONE": 85.0,
    "FOUNDATION_CLA": 75.0,
    "CORP_CLA": 25.0,
})
```
**Why it's wrong** — `docs/v2/02-RANKING-DESIGN.md:151` (row 3.1) publishes `DCO 100 · NONE 85 · FOUNDATION_CLA 75 · CORP_CLA_RECIPROCAL 40 (curated override only) · CORP_CLA 25`. The constant — and `LEGAL_IP_VALUES` (`cvi/core/constants.py:~485`) — omit that level entirely, so `cvi/core/params/governance.py` raises `ValueError` on it. That rejection is deliberate and asserted by `tests/core/test_params_governance.py:96-97`.
**Impact** — Any input carrying the spec-published level crashes scoring rather than being overridden, and even the doc's own "curated override" path cannot express 40. v1 still scores it 40 (`cvi/constants.py:92`, `cvi/pillars/governance.py:12`, `cvi/playbook.py:251`, `cvi/deepdive.py:39,223`, `web/index.html:2637`, `cvi/models.py:69`). The generated `docs/METHODOLOGY-v2.md` hides the gap because the table is rendered from the constant (`cvi/core/methodology.py:246-256`).
**Fix** — Add `"CORP_CLA_RECIPROCAL": 40.0` to `LEGAL_IP_SCORES` and `LEGAL_IP_VALUES` (then update that test), or amend 02:151 to drop the level.
**Confidence** — High.

### A-02 — Warning — derive silently collapses `CORP_CLA_RECIPROCAL` to 25

**Where** — `cvi/derive/bundle.py:52-58`
```python
#: v1 enum spellings -> v2 3.1 regimes.
V1_LEGAL_IP_VALUES: dict[str, str] = {
    "DCO": "DCO",
    "FOUNDATION_CLA": "FOUNDATION_CLA",
    "CORP_CLA_RECIPROCAL": "CORP_CLA",
    "CORP_CLA_UNILATERAL": "CORP_CLA",
}
```
**Why it's wrong** — The mapping folds the reciprocal regime into `CORP_CLA`, which scores 25, while 02:151 publishes 40 for it. Nothing warns or logs that a v1 row is being re-scored downward.
**Impact** — Legacy features inherit a score 15 points harsher than the spec's own value, invisibly and permanently in the derived bundle.
**Fix** — Either map to a distinct `CORP_CLA_RECIPROCAL` regime (see A-01), or emit a warning on this collapse and record the loss in the bundle's provenance.
**Confidence** — High.

### A-03 — Warning — the persona-conditional AI-posture score never reaches v2, yet the methodology advertises it

**Where** — `cvi/core/params/rigor.py:28-35`
```python
AI_POSTURE_SCORES: Dict[str, Dict[str, float]] = constants.AI_POSTURE_SCORES

def _ai_posture_score(posture: str, persona: str) -> float:
    table = AI_POSTURE_SCORES.get(posture)
    if table is None:
        raise ValueError(f"ai_posture: unknown posture {posture!r}; expected one of {sorted(AI_POSTURE_SCORES)}")
    return table[persona]
```
**Why it's wrong** — `ai_posture(value, persona="human")` (`rigor.py:82`, tested `tests/core/test_params_rigor.py:115-149`) is never handed a persona: `cvi/core/engine.py:213-215` calls `function(value)` for every non-ECDF input, so the default `"human"` column is always the one used.
**Impact** — 02:165 (row 4.2) publishes `25 (AI)` for `PROSCRIBED`, and `docs/METHODOLOGY-v2.md:71` renders that per-persona table straight from `constants.AI_POSTURE_SCORES` (`methodology.py:254-255`), so the published methodology promises a number the engine cannot produce. The code's design — one human score plus persona *gates* — is the coherent one: D-AGG-4 (02:276-277) recomputes per-persona composites in SQL from stored pillar scores, and `cvi/core/persona.py:319-369` only re-weights stored pillars, so `PROSCRIBED` scores 85 even in `ai_assisted`.
**Fix** — Amend 02:165 to the gate design and drop the dead `persona` argument, or thread persona through `engine.py:213-215`.
**Confidence** — High.

### A-04 — Consider — 02's `ai_assisted` gate cites a field that does not exist in v2

**Where** — `cvi/core/persona.py:197-199`
```python
def _ai_assisted_gated(bundle_inputs: Mapping[str, ParamValue]) -> bool:
    value = bundle_inputs[INPUT_AI_POSTURE]
    return isinstance(value, Measured) and value.value == AI_POSTURE_PROSCRIBED
```
**Why it's wrong** — The gate text in 02 also requires a non-empty `ai_policy_evidence`, which is not a field of the v2 `FeatureBundle`. `persona.py:26-34` argues `Measured` already implies provenance, which is why the check is posture-only. `cvi/core/methodology.py:499-501` repeats the doc's phrasing.
**Impact** — None functionally; the doc and the generated methodology overstate the gate's condition.
**Fix** — Delete the evidence clause from the 02 gate text (and from `methodology.py:499-501`), or add the field.
**Confidence** — High.

### A-05 — Consider — the persona-composite rule is expressed twice, in two languages

**Where** — `cvi/core/pipeline.py:192` (stage 8 computes `persona_composites` in Python) against D-AGG-4, `docs/v2/02-RANKING-DESIGN.md:276-277`, which mandates the composites "in SQL from the stored pillar scores".
**Why it's wrong** — Two implementations of one rule, in different layers, with no shared test asserting they agree.
**Impact** — The numbers match today; drift is the risk, and drift would be silent because both are plausible.
**Fix** — Keep one authority and make the other a checked projection of it (assert equality in a test, or generate the SQL from the Python).
**Confidence** — Medium.

### A-06 — Consider — the benchmark spec still prints v1 gradients

**Where** — `docs/BENCHMARK_SPECIFICATION.md` §5: `∂CVI/∂GR₁₄ = −10.0` and GFI `+0.60/issue`.
**Why it's wrong** — v2 values are −7.5 and +0.525; `EF +1.75/org` and Scorecard `+0.60/unit` still agree.
**Impact** — A reader calibrating against §5 gets the wrong sensitivities for the shipped engine. The doc sits under §14's supersession clause, so it is legacy rather than authoritative — but it is the natural place to look.
**Fix** — Update the two gradients, or delete §5 and point at 02 §4.
**Confidence** — High.

### A-07 — Nit — D-GATE-5's population rests on an unasserted invariant

**Where** — `cvi/core/gates.py`, D-GATE-5 branch: only the `now − created_at >= GATE5_MIN_AGE_DAYS` floor is evaluated, over `pr_window`.
**Why it's wrong** — The rule's population ("no PR activity in the trailing 180 days", 02:100) is correct only if `pr_window` *is* that trailing window. `gates.py` takes that on faith from the caller; nothing here asserts it.
**Impact** — A caller passing a differently-scoped window would produce wrong gates with no error.
**Fix** — Assert the window length (or pass the constant into the gate) so the invariant is local.
**Confidence** — Medium.

### A-08 — Nit — cosmetic and defensive-code tail

- `cvi/core/gates.py:325-326` — comment reads "not a fork (fork only when the parent is absent from the universe)". The code is right (`REASON_FORK` iff `fork_parent_in_universe is True`, `undecided` on `None`, pass on `False`; `tests/core/test_gates.py:363,382,402`), but the comment words it backwards and invites a wrong "fix".
- `cvi/core/params/curves.py:24-40` — `MONO_TOLERANCE`, `_REFERENCE_SAMPLE`, `RATE_SLOPE`, `COHORT_RATE_SLOPE` each re-bind a `constants` value under a near-identical comment block; collapse to one note.
- `cvi/core/constants.py:586-587` — the same sentence is emitted twice: `#: Module-level UPPER_CASE names that are deliberately NOT part of` / `#: Module-level UPPER_CASE names that are deliberately NOT part of`.
- `cvi/core/tiebreak.py` — `record.coverage` is typed `float | None` yet treated as non-`None` for rated rows; either narrow the type or handle `None`.
- 02 never prescribes how `tie_group` ids are numbered, so the chosen convention is undocumented.

**Fix** — Comment/typing cleanups only; no behavior change. **Confidence** — High.

### A-09 — FYI — flaky v1-era test race (outside the v2 core)

**Where** — `tests/test_backfill_downloads.py:103-104`
```python
                ok = self._send(200, body)
            if ok:
                self.server.seen.append((name, time.time()))
```
**Why it's wrong** — `seen.append` runs in the `ThreadingHTTPServer` handler thread only *after* `_send` returns, so the main thread can assert against `seen` before the retried `throttledpkg` request is recorded. Observed failure: `assert ['goodpkg', 'throttledpkg'] == ['goodpkg', 'throttledpkg', 'throttledpkg']`.
**Impact** — Intermittent red in a v1 test file; unrelated to slice A's code under review.
**Fix** — Record the hit before writing the response.
**Confidence** — High.

**Slice summary**

- The constants → `methodology.py` renderer chain means the *published* methodology can over-promise what the engine does: A-01 and A-03 are both cases where 02/`METHODOLOGY-v2.md` advertises a number no v2 code path produces.
- Enum vocabularies are the real coupling point. A level published in 02 but missing from a constant becomes either a hard `ValueError` (A-01) or a silent downward v1 collapse (A-02) — never a warning.
- Persona conditioning is implemented as gates over a single human score, not per-persona scoring (A-03, A-04); the design is coherent, the documentation has not caught up.
- Gate, tiebreak, and missingness logic otherwise follow 02 faithfully, including the D-MISS-5 `not_applicable` → `collector_failure` downgrade (`cvi/core/engine.py:208-212`), and D-VER-3 determinism holds mechanically: `grep -rn "datetime\.now\|time\.time\|utcnow\|date\.today\|datetime\.today" cvi/core/` returns zero hits.
- `OPENSSF_PROXY_MIN_SIGNALS = 8` **is** enforced, not decorative: `cvi/derive/bundle.py:1681-1683` computes `present_count` and returns `Unmeasured(reason="insufficient_n", detail=f"{present_count} of 10 signals")`, guarded again at `:1661-1674` and `:1771-1774`, tested by `tests/derive/test_input_mappings.py:1037-1058` (`"7 of 10 signals"`) — matches 02:238-242.
- The v1 scoring stack is absent from the v2 serve path: `cvi/serve/app.py:67-71,87-88` and `cvi/serve/guard.py:59-60` import `cvi.core`, `cvi.store`, `cvi.audit` and the v1 lakehouse/writer only, with the legacy app mounted lazily at `app.py:726` (`from cvi.api import create_app as _create_legacy_app`); `cvi/api.py` exists, `cvi/cli.py` does not.
- Everything else found is cosmetic or defensive (A-07, A-08) — the core's defect density is low.

**Coverage**

Read fully: `cvi/core/constants.py`, `models.py`, `pipeline.py`, `persona.py`, `matrix.py`, `tiebreak.py`, `gates.py`, `aggregate.py`, `normalize.py`, `engine.py`, `methodology.py`, `openssf.py`, `hygiene.py`, `commit_domains.py`, `params/curves.py`, `params/governance.py`, `params/rigor.py`; the cited tests under `tests/core/` and `tests/derive/`; `cvi/derive/bundle.py` in the proxy-guard and v1-mapping regions.
Skimmed: `docs/v2/02-RANKING-DESIGN.md`, `docs/METHODOLOGY-v2.md`, `docs/BENCHMARK_SPECIFICATION.md`, `cvi/serve/*`.
Verification runs: full `.venv/bin/pytest -q` → exit 0, `[100%]` with a single `s` and no `F`/`E`; focused subset (`test_methodology.py`, `test_render_methodology.py`, `test_ui_methodology_no_literals.py`, `test_constants_hash.py`, `test_frozen.py`) → exit 0, 80 passes; `.venv/bin/ruff check .` → `All checks passed!`.
COULD NOT VERIFY: a total pass count. The `N passed` summary line never appeared in either captured full-suite log, so magnitude is taken from `docs/v2/00-README.md:173` ("Suite on main 2,211 passed, 1 skipped") rather than from this run.
<!-- END A-CORE -->

## Slice B — data collection

<!-- BEGIN B-COLLECT -->
Auditor B ran read-only against HEAD `80fb117` (the collection remediation line). Finding IDs
are `B1…B9` (Blocker) plus an unnumbered Warning/Consider tail. No tests were executed.

### Verdict on sustainability

**Collection is not sustainable at either the daily or weekly cadence in
`docs/v2/08-SUSTAINABLE-COLLECTION.md` §5**, and that doc understates its own most expensive
line item. `08:39` budgets a sweep at ≈172k REST + 45k GraphQL points + 17k search calls
≈ 45–50 h serialised, which `08:41-48` turns into "~2 days of wall-clock" at a "~30 % duty
cycle". But governance alone is ≈45 REST calls/repo (`cvi/collect/governance.py:255-334`), so
45 × 5,753 repos ≈ **259k REST ≈ 52 h** — governance by itself exceeds the entire plan.
`08 §5`'s "interim ~4–5 h/week" (`08:215-216`) inherits the same error.

### B1 — Blocker — `write_envelope` is not atomic, while every resume check is a bare existence probe

- **Where**: `cvi/collect/envelope.py:204-232`; resume probes at `scripts/gather.py:86-87`,
  `cvi/collect/github_graphql.py:344`, `:825`, `cvi/collect/pike_signals.py:240`, `:323`,
  `:387`, `cvi/collect/governance.py:394-396`
- **Evidence**: `target.parent.mkdir(...)` (`:230`) then `target.write_text(json.dumps(...))`
  (`:232`). `write_text` truncates then writes, so an interrupt 200 bytes into a 300 KB
  envelope leaves a half-written JSON *file*, and `_done` = `.is_file() and not force`.
- **Why it's wrong**: downstream reads a torn envelope as absence, not corruption —
  `scripts/derive.py:160-169` catches `json.JSONDecodeError` and returns `(None, None)`, so
  every field from it is recorded `missing` at its default (`derive.py:156-157`). The sha256
  provenance hash is silently dropped (`derive.py:141-152`).
- **Impact**: 0 releases, 0 PRs, 0 dependents become a *measured zero* in the score
  (REVIEW_GUIDELINES §4 Gate 0), permanently, and `--force` (refetch everything) is the only
  recovery.
- **Fix**: write `<name>.json.tmp` then `os.replace` — the pattern already in-repo at
  `scripts/backfill_downloads.py:122-133` — plus a `labels.needs_gather`-style validity
  re-probe. `08` §2.1 names "file exists ⇒ done" as root cause 1 but §4 never fixes the write.
- **Confidence**: high — `write_text` semantics plus the quoted probes.

### B2 — Blocker — a keyless 404/timeout is written as a permanent, *provenanced* null

- **Where**: `scripts/gather.py:97-103`, `:250-253`, `:70-83`
- **Evidence**: `_get_json` swallows every exception into `None`; then `_env(collector, key,
  url, shaped)` is called **unconditionally** with `shaped=None`, reporting
  `status = "ok" if shaped else "fail"`. `_env` stamps `http_status=200`, and its docstring
  (`:73-75`) defends that as "a fact about the transports used here, not a guess".
- **Why it's wrong**: the run reports `fail` while laying down an envelope, so the next run's
  `done(collector)` (`:246`) is True and deps.dev / OSSF Scorecard are never refetched. The
  request never succeeded, yet the envelope's own integrity field says 200.
  `derive.py:355-360`, `:455-463` then record `dependents_count = 0` / `scorecard_score = 0.0`.
- **Impact**: a transient 503 becomes "this project has no dependents". The same file knows
  better 3 lines later — downloads refuse null-as-done (`:255-264`) — two opposite rules for
  one fact (guidelines §4).
- **Fix**: never write an envelope for a failed keyless fetch; follow the downloads rule.
- **Confidence**: high.

### B3 — Blocker — `repo_object` still discards an entire envelope on one partial error

- **Where**: `cvi/collect/repo_object.py:273-286`, `:343`, `:357`, `:388`, `:403`, `:409`
- **Evidence**: `_gh_graphql` raises on `returncode != 0` (`:281-282`) and again whenever the
  response carries a GraphQL `errors` array (`:285`); `collect_repo_object` builds everything
  off that one call plus `_shallow_tree` plus `_fetch_body` for MAINTAINERS and README.
- **Why it's wrong**: this is exactly what `80fb117 fix(collect): W1-4 round 3` removed from
  `github_graphql.GhCliTransport:118-141`, which now returns partial `data` and records
  `errors`. `repo_object` was left behind.
- **Impact**: a repo without a README, or one partially-resolvable selection, throws away
  `owner`, `hasIssuesEnabled`, license and stars that were already in hand.
- **Fix**: reuse the salvage path from `GhCliTransport`, or degrade per-section and record
  which sections failed.
- **Confidence**: high.

### B4 — Blocker — `_shallow_tree`'s frontier is uncapped and contradicts its own budget

- **Where**: `cvi/collect/repo_object.py:297-331` (`:309`, `:316`, `:319`, frontier push `:330`)
- **Evidence**: one tree call plus one per top-level directory.
- **Why it's wrong**: a monorepo with 60 top-level dirs costs 61+ REST calls for one repo,
  against `08:30`'s budget of "1–2 REST … ~9k … ~2 h". No cap and no `truncated` flag.
- **Impact**: cost explodes on exactly the repos most likely to be in the universe, and the
  envelope cannot distinguish a complete tree from a budget-aborted one.
- **Fix**: cap the frontier and record `truncated` as `labels.py` does.
- **Confidence**: high.

### B5 — Blocker — one unguarded exception ends a multi-hour sweep

- **Where**: `scripts/gather.py:496`; raisers at `cvi/collect/github_graphql.py:135`,
  `cvi/collect/governance.py:196`, `scripts/gather.py:138-160`, `:490`
- **Evidence**: `st = gather_repo(...)` in the candidate loop has no `try`, and
  `subprocess.run(timeout=90)` propagates `subprocess.TimeoutExpired`.
- **Impact**: six hours in, one `gh` call exceeding 90 s kills the process; every repo after
  ~#3,000 is silently absent from the sweep and the rerun looks like a routine resume.
- **Fix**: wrap per-repo, record the failure in the run log, continue.
- **Confidence**: high.

### B6 — Blocker — governance is under-budgeted on cost and silently under-evidenced on correctness

- **Where**: `cvi/collect/governance.py:80`, `:82`, `:84`, `:255-289`, `:292-334`, `:308`,
  `:310`, `:312-314`, `:404-406`, `:408-409`, `:211-229`
- **Evidence**: `_merged_prs` pulls 5 × 100 PRs; `_check_evidence` makes 2 REST calls per
  distinct head SHA for up to `MERGED_PR_CAP=20` = 40 calls. Pulls are fetched
  `sort=created&direction=desc` under a hard 500-PR ceiling; `404 → continue` drops that PR's
  evidence with no flag; `fetch_file_text` re-raises on any non-404.
- **Why it's wrong**: ≈45 REST/repo vs `08:35`'s budget (see verdict). Recent closed-unmerged
  spam can fill the 500 ceiling with fewer than 20 merged PRs; `data` records
  `pulls_available`/`merged_pr_cap` but never "I stopped early"; a repo whose `check-runs`
  404s is indistinguishable from one with no checks. `legal_ip` (3.1) and `promotion_ladder`
  (3.3) then classify from a 3-PR sample.
- **Impact**: ≈52 h of the sweep in one collector, plus measured zeros where the truth is
  Unmeasured. The 45 calls are also spent *before* the envelope exists, so one 403 from
  raw.githubusercontent discards all of them with no resume point.
- **Fix**: record early-stop and per-PR fetch failure; cap evidence calls; fetch documents
  after the envelope is written.
- **Confidence**: high on the counts; the cadence impact is arithmetic on the repo count.

### B7 — Blocker — star history is inert, and its reader violates its own contract

- **Where**: `cvi/collect/universe.py:206-215`, `:253`, `:272`, `:289`, `:297`;
  `scripts/build_universe.py:33`, `:41-45`
- **Evidence**: `default_star_history_transport` is `raise NotImplementedError` (`:212`),
  bound live at `build_universe.py:45` (comment: "No live implementation yet").
  `star_velocity_evidence:253` calls `read_envelope(path)` **unguarded** inside a function
  whose docstring claims it never raises; `:297` hand-spells
  `"path": f"raw/{STAR_HISTORY_COLLECTOR}/{owner}__{name}.json"`.
- **Impact**: every fetch fails and the caller swallows it, so every candidate ends up
  `Unmeasured(collector_failure)` — StarScout and `star_burst` have never had measured input,
  and "no burst measured" is indistinguishable from "no burst". Because the reader can raise,
  a missing envelope aborts the universe build through a "never raises" API.
- **Fix**: implement or unbind the transport, and honour the never-raises contract.
- **Confidence**: high.

### B8 — Blocker — the universe's selection column mixes units; crates.io is one page

- **Where**: `scripts/build_universe.py:70-88` (`:87`), `:140`, `:154`, `:160`, `:164`, `:97`
- **Evidence**: `crates_top` requests a **single** page
  (`?sort=downloads&per_page=100`) then `[:limit]`; `:87` stores
  `"stars": int(c.get("downloads") or 0)`, fed to `add(..., stars=int(c["downloads"]))` at
  `:160`, and `:164` cuts with `sorted(...)[:max_total]` on that column. `ck["done"][cid] =
  len(items)` (`:154`) marks a criterion done even when `items == []`.
- **Why it's wrong**: `stars_at_selection` mixes GitHub stars with crates.io downloads, so a
  crate with 900k downloads outranks a 90k-star repo and the cut point is arbitrary rather
  than a quality ranking; one failed page permanently deletes a criterion (`:140` skips it
  forever). `_save_ckpt:97` and `CANDIDATES.write_text` are non-atomic, so the universe's own
  resume inherits B1.
- **Fix**: keep units in separate columns, paginate crates.io, treat 0 items as not-done,
  write atomically.
- **Confidence**: high.

### B9 — Blocker — `cvi/collect/ratelimit.py`: 332 lines, zero readers

- **Where**: `cvi/collect/ratelimit.py` (whole module), `:43-45`, `:141`
- **Evidence**: grep for `ratelimit|CachedHttpClient|TokenBucketRateLimiter|CircuitBreaker`
  hits only its own file, `tests/collect/test_ratelimit.py`, `tests/test_no_clock.py`,
  `tests/core/test_import_graph.py`, docs, `.github/workflows/ci.yml`, `.agents/W4-1/*`,
  `pyproject.toml` — **no consumer under `cvi/` or `scripts/`**.
- **Why it's wrong**: `docs/REVIEW_GUIDELINES.md:60-67` makes "new symbols with zero readers"
  a Blocker verbatim, and `08:82-86` already admits it. It also cannot do the job:
  `DEFAULT_RATE = 1.2` is a requests/second bucket that cannot express a *points* budget
  (`pr_window` ≈31 points/page), `ETagCache` is an in-memory dict so it would not survive a
  rerun, and `CachedHttpClient` needs `httpx` while every live collector uses `urllib` plus
  `gh` subprocesses.
- **Fix**: wire it in or delete it — today it is a mock of a plan.
- **Confidence**: high for this working tree; `COULD NOT VERIFY`: a reader on an unpushed
  branch.

## Warnings

- **Eligibility retry storm** — `github_graphql.py:736-794`: `ELIGIBILITY_RETRY_WIDTHS = (5, 1)`
  means one deleted login in a 15-login chunk triggers ~19 extra GraphQL calls, and a
  whole-batch `RuntimeError` (`:710-713`) leaves every login in the chunk unresolved. All cost
  is on the failure path.
- **Cost accounting that gates nothing** — `cost = 0` on a failed call under-reports
  `cost_points`; `rate_limit_remaining` is recorded at `github_graphql.py:885` and read by
  nobody, so no collector consults remaining budget before the next call.
- **No resume inside multi-call collectors** — `github_graphql.py:278-320`, `:575-621` and
  governance page for many calls and write one envelope at the end: crash on page 4 of 5, pay
  all 5 again. Crash cost scales with the collector, not the repo.
- **Pagination on a shape check** — `github_graphql.py:316-319`, `:616-619`: if `pageInfo` is
  absent (partial-error response) the loop stops and the envelope records `truncated: False`
  over a short list. Short list and truncated list are byte-identical on disk.
- **`commits_365d` cap with no flag** — `cvi/collect/commits.py:87`, `:124-146`: rows sliced to
  `COMMIT_CAP=1000` with no `truncated`, feeding `elephant_factor:169`, `bus_factor:196` and
  vitality, so a repo at the cap looks like a repo with exactly 1000 commits (biases EF
  upward). Compounding: `:185-186` and `:212-213` both return `1` when `total == 0`, so a repo
  with **no commits** gets the maximum concentration score, identical to a genuine
  single-author project.
- **`dedup_dependents` unit conflation** — `cvi/collect/depsdev.py:315-334`: the parent's
  `system` is applied to each dependent and `packages` is always `None`, so `deduped_count`
  silently equals the raw count — D-PARAM 2.1's anti-double-count rule, trusted blindly by
  `derive.py:549-556`, never sees deduplicated data.
- **`branch = "main"` fallback becomes a scoring penalty** — `gather.py:165-168`, `:170-174`
  (`# review-ok: gate-cache — corrupt envelope treated as main`): a `master`/`trunk` repo
  queries `actions_runs` for a branch with no runs → 0 runs → `pipeline_defenses` applies the
  "0 if no CI" rule, and the envelope never says the branch was guessed.
- **Dead shims into another module's privates** — `gather.py:329-335`, `:350-357`:
  `inspect.signature(_ro_mod._fetch_body)` against a function that takes exactly one parameter
  (`repo_object.py:334`), so the `len(ro_params) >= 3` branch is unreachable.
- **Duplicate constants**, against guidelines §4 ("one definition per fact, constants live in
  `cvi/constants.py` only"): `SLEEP_PYPISTATS` (`gather.py:38`) vs
  `ratelimit.DEFAULT_MIN_INTERVAL`, `SLEEP_KEYLESS` (`:37`), `SLEEP_REST` merely re-exported
  (`:31`), and the download `User-Agent` spelled twice inside one function (`:457`
  `"cvi-research/0.1 (ranking study)"` vs `:464` `"cvi-research/0.1"`) and again in the
  backfill.
- **Downloads key drops the owner** — `dl_key = f"{ecosystem}__{name}"` (`gather.py:257`), read
  back at `derive.py:290`: registry names are global, GitHub repos are not, so two tracked repos
  on one crate invisibly share one download number.
- **Unvalidated `owner`/`name` reach a GraphQL string literal and a URL path** —
  `gather.py:127-143` (interpolated at `:136`), `repo_object.py:357`, `gather.py:238`
  (`%2F`-quoted deps.dev project path) and `:241` (scorecard URL, *unquoted*), from
  `candidates.jsonl` at `:488-490`. Contrast `github_graphql.py:78-93`, `:503-513`, which use
  real `$owner`/`$name` variables. A stray quote is either a syntax error (→ B5) or a 404 read
  as absence (→ B2).

## Consider / Nit / FYI

- 11 hand-spelled `raw/<collector>/<key>.json` paths while `envelope.py:108`
  (`raw_envelope_path`) and `:117` (`relative_envelope_path`) exist: `derive.py:162`, `:175`,
  `universe.py:230`, `:265`, `:297`, `gather.py:78`, `:87`, `:258`, others. `universe.py:265`
  and `:297` spell the same path two ways within one function.
- `AGENTS.md`/`CLAUDE.md` counted as an "AI policy file" (`governance.py:95-98`) makes
  `classify_ai_posture:559-577` return `REGULATED` for any repo carrying an agent config — the
  4.2 proxy measures "someone dropped an instructions file". The module docstring (`:24-38`)
  already concedes `PROSCRIBED` can never fire, so the `ai_assisted` hard-ban gate is dead by
  construction.
- `governance.py:406` stores full `{"present", "sha256", "body"}` for 12 files/repo
  (`:88-98`) in the dearest envelope, while `08` §3 and `03` §3 say documents are not 3.1
  evidence; `derive.py:172-175`'s `bodies/` cache already shows the on-demand alternative.
- `*.json.backfill-tmp` files stranded by an interrupted backfill sit beside real envelopes
  forever.
- `RETRYABLE_ECOSYSTEMS` (`scripts/backfill_downloads.py`, imported at `gather.py:374-383`)
  restates gather's per-ecosystem rules — a second source of truth for "which ecosystem may be
  retried".

## Praise (patterns to copy, not defects)

- `scripts/backfill_downloads.py` is the model fix: `.tmp` + `os.replace` (`:122-133`),
  null-is-not-done, a bool-safe int check, a monotonic-clock throttle, ETA from measured
  per-request time. B1 and B2 are already solved here.
- `cvi/collect/labels.py` is the model collector: one query per label, union by issue *number*,
  explicit `MAX_ISSUE_PAGES = 2`, `truncated` recorded, and a `needs_gather` that re-collects
  corrupt and legacy envelopes — exactly the re-detection `_done()` lacks.
- `governance.py:351-371` `signoff_share` re-derives from the collected `commits_365d` envelope
  instead of a second fetch; `_row_sequence:337-348` treats a malformed shape as "no rows";
  `derive_governance_fields:580-655` marks `missing` only when **both** sources are absent.
- `derive.py:126-153` hashes the source envelope in `Bundle.set` so `verify.py` checks content
  not path existence, and `derive.py:310-313`'s `src()` is the correct path rule
  (`c26072b` closed the escape route). Gap check performed: `derive.py` has no bare-URL
  provenance; gather's keyless `source=url` (`gather.py:252`) is the outlier and derive
  re-anchors it to a path.

## Answers to the operational questions

- **What a crash costs**: everything after the last *completed* envelope, per collector — not
  one number. Each of the ~12 collectors writes exactly one envelope at the end, so a crash
  mid-`pr_window` costs its GraphQL pages and mid-governance ≈45 REST calls. Across repos
  nothing already written is lost (`gather.py:86-87`) *provided the write finished*; a crash
  **during** the write is permanent silent corruption (B1). For `build_universe`, a crash costs
  the whole run (`build_universe.py:97`, `:164` are non-atomic).
- **What a network blip costs**: for deps.dev and Scorecard, a **permanent** null stamped
  `http_status=200` and reported `fail` (B2) — the only retry is `--force`. For downloads,
  correctly a rerun (`gather.py:255-264`). For every `gh`-backed collector,
  `gather.py:90-93`/`:209-211` turn a blip into `status="fail"` with no envelope, so the rerun
  re-fetches from page 1: `commits_365d` up to 10 pages, governance ~45 calls, `pr_window` its
  whole frontier.
- **Can it resume?** Only at envelope-file granularity, only if the file is complete (not
  guaranteed — B1), and existence ≠ validity (only `labels.needs_gather` knows otherwise). No
  checkpoint inside any multi-call collector, no watermark, `etag` always null (`08:82-86`),
  `rate_limit_remaining` recorded but never read. Therefore **`08` §4's
  fingerprint/`If-None-Match`/ledger/watermark design cannot be switched on as written**: it
  rests on envelopes that are non-atomic and ETag-less, so a new ledger would faithfully record
  a corruption. Sequence B1 → B2 → B3/B4/B6a, then `08` §4/§6 and §5's cadence.

## Slice summary

- **Absence and corruption are conflated everywhere.** Existence-of-file stands in for
  validity, `None` stands in for a measured zero, and a short list stands in for a complete
  one. Three separate Blockers (B1, B2, B6b) are the same mistake at different layers.
- **A fix landed where it was noticed, not where it was needed.** `backfill_downloads.py` and
  `labels.py` already implement atomicity, null-is-not-done, truncation flags and re-probing;
  `envelope.py`, `gather.py`, `commits.py` and `repo_object.py` do not. The repo has no rule
  that a fix to one collector propagates to its siblings.
- **Budgets are documented but not enforced.** `08`'s per-collector costs are wrong by an order
  of magnitude for governance and `repo_object`, nothing reads `rate_limit_remaining`, and
  `ratelimit.py` — the enforcement mechanism — has no readers.
- **Observability is write-only.** `cost_points`, `rate_limit_remaining`, `truncated`,
  `pulls_available` are recorded and mostly never read by code or surfaced to the operator.
- **The planned redesign inherits these bugs.** `08` §2 diagnoses correctly; §4 builds a ledger
  on top of an un-fixed write primitive.
- **Contract violations are local, not incidental**: a "never raises" reader that raises (B7),
  a docstring asserting `http_status=200` is a fact when it is a guess (B2).

## Coverage

- **Read fully**: `cvi/collect/{envelope,github_graphql,commits,depsdev,ecosystemes,governance,labels,pike_signals,ratelimit,repo_object,universe}.py`,
  `scripts/{gather,build_universe,backfill_downloads}.py`.
- **Skimmed**: `tests/collect/**`, `docs/v2/08-SUSTAINABLE-COLLECTION.md`, `docs/v2/03-ANTI-MANIPULATION.md`.
- **COULD NOT VERIFY**: deps.dev system ids (`SYSTEM_FOR_ECOSYSTEM` → `crates.io`/`go`/`maven`)
  — no network in this review; if an id is wrong, its 404 becomes a permanent null via B2.
  Whether a torn envelope exists in the live `data/raw/` tree — live data not scanned. No tests
  were run; canonical invocation is
  `env -u PYTEST_ADDOPTS .venv/bin/python -m pytest tests/ -n auto -p no:cacheprovider`, and
  B1/B5/B7 all look directly testable against the fixture snapshot.
<!-- END B-COLLECT -->

## Slice C — derive, verify, provenance

<!-- BEGIN C-DERIVE -->
Auditor C ran read-only against HEAD `671fe29`. No repo file was edited; the scratch repro that
confirmed C-02 was deleted afterwards. Unlike auditor B, tests *were* run — 685 collected across
`tests/derive`, `tests/verify`, `tests/core`, exit 0 — so every finding below is written against a
**green** suite. **The hash chain is genuinely verified at the boundaries that gate publication, so
this slice has no Blocker**: `scripts/verify.py:191-220` (`_path_offender`) re-checks every
`verified` field (the path stem must equal `f"{owner}__{name}"`; `monthly_downloads` must sit in an
`ECOSYSTEM_KEYED_FIELDS` layout; the collector dir must be in `FIELD_COLLECTORS` at `:76`; the file
must open via `read_envelope`; `:220 if not envelope.hash_matches_data` is an offender;
`_claimed_hash` must equal `envelope.sha256`; a missing recorded hash becomes
`unverifiable_provenance`, which blocks promotion), `scripts/promote.py:72` imports
`_claimed_hash`, `_envelope_path` and `_source_items` out of `scripts.verify` and re-runs them
against the manifest map (`:235-268`), with `:276` "A bundle with zero resolvable references
fails", and `cvi/snapshot/manifest.py:84` records `entry["sha256"] = envelope.computed_sha256`
*with* `hash_origin` plus `hash_matches_data`. Caveats on that gate: `ADVISORY_CHECKS =
{"scorecard"}` never gates, and `REQUIRED_VERIFIED = ("stars", "forks")` is a narrow floor. The
realistic risks here are C-02 (availability) and C-05 (reproducibility), not a corrupted score
shipping silently.

### C-01 — Warning — The bundle loader is self-attesting: derive-time envelope hashes are recorded but never compared

**Where**: `cvi/derive/bundle.py:949-951` — `_envelope_hash` is
`sha256_hex(canonical_json(_envelope_data(path)))`, called at `:1042`, `:1075`, `:1108`, `:1186`,
`:1521`, `:1565`, `:1705`, `:1758`, `:1819`. `_relative_source:922-925` states it "duplicates
`cvi.collect.envelope.relative_envelope_path`'s arithmetic rather than importing it … to avoid
circular or collect-layer dependencies". `grep -n sha256 cvi/derive/bundle.py` returns only four
hits (`:14`, `:950`, `:951`, `:2131` — docstrings plus `_envelope_hash` itself), and
`grep -nE "sha256|hash_matches_data|read_envelope|computed_sha256" cvi/verify/**` returns **no
matches**; `cvi/verify/pipeline_hook.py` (171 L) was read fully and is pure detector composition.

**Why it's wrong**: the integrity value is produced by the same reader that consumes it, so it
attests to itself; the loader never reads back the `sha256` that the v1 document recorded in
`prov[field]`.

**Impact**: editing an envelope in place moves `source_hash` and `provenance_hash` with no mismatch
signal — `provenance_hash` fingerprints the bytes as they are now, not the bytes the score came
from, so a consumer of `derived/*.json` alone (`web/v2`, `cvi/serve`, an external reader) cannot
detect a swap. This is a design-level gap, not a test contradiction:
`tests/derive/test_relative_provenance.py:42,47` deliberately pins `fact.source ==
"raw/contributors/acme__widget.json"` and `bundle.provenance_hash ==
feature_provenance_hash({fact.source: fact.source_hash})` with the hash recomputed at load.

**Fix**: route `_resolve_envelope`/`_envelope_data` through `cvi.collect.envelope.read_envelope`
and treat `hash_matches_data is False` as `Unmeasured(reason="collector_failure")`; if the
`derive/`→`core/`-only rule in `docs/v2/04-ARCHITECTURE.md` is what blocks the import, move the
envelope reader into `cvi/core/` rather than duplicating its path arithmetic.

**Confidence**: high — exhaustive grep, nine call sites enumerated.

### C-02 — Warning — One torn raw envelope aborts the entire bundle load; upstream cause is B1

**Where**: `cvi/derive/bundle.py:944-946` — `_envelope_data` is a bare
`json.loads(path.read_text(encoding="utf-8"))` — reached via `_resolve_envelope:887-909`, which
guards only with `alt.is_file()` / `candidate.is_file()`, from `_load_v1:2177`
(`input_id: INPUT_MAPPINGS[input_id](ctx) for input_id in constants.PARAM_INPUT_IDS`). The same
shape appears at `cvi/derive/gate_evidence.py:84` (`_tree_paths`) and `:98`
(`_vitality_commit_count`). Reproduced on a scratch copy of the fixture root by truncating
`raw/github_meta/acme__widget.json`: `json.decoder.JSONDecodeError: Expecting value: line 1 column
53 (char 52)`; the chain is `load_bundle` → `_load_v1:2177` → `resolve:1042 source_hash =
_envelope_hash(path)` → `_envelope_hash:951` → `_envelope_data:945`.

**Why it's wrong**: the other two layers that walk the same tree refuse to die on one unreadable
envelope — `scripts/derive.py:160-169` catches `JSONDecodeError` and returns `(None, None)`, and
`cvi/snapshot/manifest.py::envelope_entries` catches `UNREADABLE_ENVELOPE_ERRORS` and continues.
This is not a "readers must never raise" claim: `tests/derive/test_malformed_events.py` shows
whole-bundle rejection *is* intended for malformed document **content**, whereas a malformed raw
**envelope file** is an I/O artifact generated one layer out. **This is the derive-side half of
collection auditor B's B1** (`cvi/collect/envelope.py:204-232`'s non-atomic `write_text` is the
cause), so the two slices overlap by design and B1 sequences first.

**Impact**: after an interrupted gather, every `derive` over that data dir raises — for any repo
whose provenance names that collector key — until someone hand-finds the one bad file. Correct
degradation is `Unmeasured(reason="collector_failure")`, whose vocabulary already exists at
`_unmapped:1930-1934`.

**Fix**: fix the cause at `envelope.py:204-232` (write to `.tmp` + `os.replace`, the in-repo
pattern at `scripts/backfill_downloads.py:122-133`) *and* catch unreadable envelopes at
`_resolve_envelope`/`_envelope_data` plus `gate_evidence.py:84,:98`, recording a collector failure
instead of raising.

**Confidence**: high (reproduced with a real traceback); the `gate_evidence.py` pair is
read-verified only.

### C-03 — Consider — `_relative_source`'s absolute-path fallback is dead code

**Where**: `cvi/derive/bundle.py:931-941` — under `if raw_root is not None:`, the
`try: return path.relative_to(Path(raw_root).parent).as_posix()` is followed by
`except ValueError: candidate = None` (`:935`), then `:936` assigns
`candidate = path.as_posix()` **unconditionally**, so the `None` written one line earlier is never
read; `:939-941` raises `BundleValidationError` whose message contains "absolute".

**Why it's wrong**: the `except` branch was written to give up on that source, but the next line
overwrites it, leaving only the absolute path, which then fails the whole bundle.

**Impact**: the dead store itself is inert; the live cost is blast radius — a source that is merely
outside `raw_root` kills every field, not one. `tests/derive/test_relative_provenance.py:50-69`
pins the absolute-path rejection as *intended*, so the contract is not questioned here.

**Fix**: delete the dead assignment, or restore the `None` path and let that field fall to
`unverifiable_provenance` (already promotion-blocking in `scripts/verify.py`).

**Confidence**: high on the dead store; medium on which fallback was meant.

### C-04 — Consider — `paths[0]` names one envelope for a value the collector computed from several

**Where**: `cvi/derive/bundle.py:1624-1634` `_resolved_source_paths` — no sort,
`[path for path in (_resolve_envelope(s, raw_root) for s in source_field) if path is not None]` —
consumed at `:1708-1709` and `:1826-1827`, with `n` taken from
`_envelope_data(paths[0])["prs"]` counting `mergedAt is not None` at `:1820-1824`. Document order
is hardcoded in `cvi/collect/pike_signals.py::derive_w17_fields`: `:739-743`
`[env_path(col) for col in ("github_meta", "commits_365d", "releases", "issues_counts", "dependents")]`,
`:752-756` the four-source `openssf_signals_partial` variant, `:765-771` `pipeline_defenses` →
`[env_path("scorecard")]` for `METHOD_PRIMARY` / `[env_path("pr_window"), env_path("runs")]` for
the proxy; `env_path:697-706` does build through `relative_envelope_path`.

**Why it's wrong**: **demoted from Warning** — I suspected order nondeterminism and disproved it
(the tuples are fixed and fully reproducible). What survives is the filter: if `github_meta` is
absent from a copied data root, `paths[0]` silently becomes `commits_365d` while the value still
came from `meta.*`, so `Measured.source`/`source_hash` name the wrong envelope.

**Impact**: this passes `verify` cleanly (the path is well-formed and the hash matches the file it
names — which is the wrong file), and for the `pipeline_defenses` proxy an absent `pr_window` yields
a silently **zero** `n` instead of an absent measurement — exactly the v1-default-trusted-downstream
class `04` warns about.

**Fix**: record the primary source explicitly (a `(primary, secondary…)` shape, or a primary index
in the document) and treat "primary absent" as `Unmeasured(reason="collector_failure")` rather than
sliding to the next entry.

**Confidence**: high that there is no nondeterminism, and on the mechanism; medium on frequency
(the live `data/raw/` tree was not scanned).

### C-05 — Warning — The loader runs on the wall clock and two of eighteen parameters are structurally unmeasurable through it

**Where**: `scripts/derive.py:268-272` — `def derive_repo(owner, name, ecosystem, data_dir=DATA_DIR,
fetch=False, now: Optional[datetime] = None)` then `now = now or datetime.now(timezone.utc)` — plus
`:193` `at = utc_now_iso(datetime.now(timezone.utc))` in the `_body` cache writer;
`cvi/derive/bundle.py:125-147` `load_bundle(path, *, raw_root=None)` has **no** `as_of`/`now`, and
`_load_v1:2127 def _load_v1(document, raw_root)` has none either. See also `_unmapped:1930-1934`,
the table at `:1949` ("`gr14_ghost_rate` | (none landed) | collector_failure: needs a snapshot
as_of the v1-to-v2 loader is never given"), `:1972-1976` ("must not borrow the pr_window
collector's own fetch-time clock (04 section 8)"), `:2004` ("v2 5.4 blends CI runtime with setup
signals; v1 stores a CI runtime mean only"), and `:2007-2008`, which raises `ValueError` unless
`INPUT_MAPPINGS` covers exactly `PARAM_INPUT_IDS`.

**Why it's wrong**: one clock value drives the GFI recency window, EF, vitality and bus factor, and
the v1→v2 path cannot be given an as-of even in principle.

**Impact**: the same `derived/*.json` loaded twice across a window boundary yields different
scores, so `cvi/snapshot/reproduce.py` cannot tell benign drift from a real mismatch; and
2-of-18-unmeasurable (`gr14_ghost_rate`, `dx`) is a permanent ceiling *inside* the coverage check
rather than a crash.

**Fix**: thread an explicit `as_of` from the snapshot manifest into `load_bundle`/`_load_v1`, make
`derive_repo` reject a missing `now`, and use it at `:193`; `gr14_ghost_rate` then becomes
computable from data already on disk.

**Confidence**: high on the code facts; medium on operational weight.

### C-06 — Consider — Source hashes can be read from a different data root than the one being derived

**Where**: `scripts/derive.py:109-120` — `p = cand if cand.is_file() else DATA_DIR / p` (`:116`)
falls back to the **module-global `DATA_DIR`** although the function receives `data_dir`
(`Bundle.__init__:134` stores `self._data_dir`); `:117` `cache[source] = read_envelope(p).sha256`;
`:118` catches `(OSError, json.JSONDecodeError)` and caches `None`. `Bundle.set:141-153` records
`entry["sha256"]` (list-shaped for list sources).

**Why it's wrong**: under `--data-dir /other`, a relative source present in the default root but
absent under `/other` is hashed from the default root, so the hash describes a file outside the
run's own tree — breaking `04`'s self-contained-copied-dir property for hashes even though the
paths stay relative.

**Impact**: silent both ways — a `hash_mismatch` under `--data-dir /other`, or a pass carrying the
wrong provenance under the default root.

**Fix**: resolve the fallback against the caller's `data_dir` (or drop it and record `None`), and
record which root each hash came from.

**Confidence**: high on the mechanism (it is one line), low on trigger rate (no two-root run was
performed).

### C-07 — Consider — The `bodies/` cache is backfilled provenance, null-as-measured, and HEAD-dependent

**Where**: `scripts/derive.py:~178-196` (`_body`) — caches envelopes without a `sha256`, stores
`{"body": null}` for a failed fetch (`:191`, marked with the review note `ok: gate-network`), and
fetches `HEAD` rather than the repo's `default_branch`.

**Why it's wrong**: `{"body": null}` is indistinguishable from a genuinely null body — auditor B's
B2 class reproduced inside derive's own file; `HEAD` is not the file the governance fields claim to
read; and a hash taken at cache-read time is a *backfilled* origin, which `manifest.py:84` already
models honestly via `hash_origin`.

**Impact**: governance-flavoured fields measured from the wrong branch and cached as `null`
permanently (`--force`-only recovery); body-sourced hashes carry no origin marker.

**Fix**: record fetch failure distinctly from `null`, target `default_branch`, and attach
`hash_origin` the way `manifest.py:84` does.

**Confidence**: medium-high — the shape is clear from reading, but the non-default-branch case was
not executed.

### C-08 — Consider — `cvi/provenance.py` is a test-only schema that reads as production

**Where**: `cvi/provenance.py` (`RepoEnvelope`, `FieldRecord`, `raw_path`, `RAW_DIR`); the only
production import anywhere is `utc_now_iso` (`scripts/derive.py:40`, `cvi/audit/forward.py:44`),
and everything else is referenced only by `tests/test_provenance.py`.

**Why it's wrong**: two provenance schemas coexist (the live one in `cvi/core/models.py` plus
`bundle.py`) with nothing marking which one is authoritative.

**Impact**: no runtime defect — a maintenance and misreading hazard, plus a live-looking `RAW_DIR`
pointing at a layout derive no longer uses.

**Fix**: move `utc_now_iso` to its real home, then delete or explicitly label the module, folding
its tests into the live schema's tests.

**Confidence**: high (the import grep is exhaustive).

### C-09 — FYI — DR_90 is recomputed from raw `pr_window` at load; one question about cap-vs-`min_n`

**Where**: `cvi/derive/bundle.py:1359` `_dr90_decision_cohort` — a half-open
`[anchor - 180d, anchor - 90d)` window, `hygiene.apply_author_cap` over `(login, createdAt)`,
no-author PRs keyed `__no_author_pr_<number>`, "decided" meaning `mergedAt`/`closedAt` within 90 d
inclusive, `n` set to the **capped** cohort, `min_n` re-checked in
`cvi.core.params.curves._apply`, and the method flipping to `proxy:din3_unapplied` when there is no
`author_history` envelope.

**Why it's wrong**: re-deriving rather than trusting a v1 scalar is the right call; reading cannot
settle whether `min_n` should gate the capped cohort (as it does now) or the raw one.

**Impact**: a repo made of many single-PR contributors can pass a floor that a spec reader might
expect it to fail.

**Fix**: none required — a comment or a spec citation defining `min_n` against the capped cohort
closes it.

**Confidence**: high on mechanics, unknown on intent.

**Slice summary**

- Verification here is a **gate, not a layer**: every hash check lives on the publish path
  (`scripts/verify.py`, `scripts/promote.py`, `cvi/snapshot/manifest.py`) and none in the read path
  (`cvi/derive/bundle.py`, `cvi/verify/detectors.py`, `cvi/verify/pipeline_hook.py`), so anything
  that reads `derived/*.json` without going through promotion trusts an attestation the loader
  wrote about itself → C-01.
- The same corruption is handled three different ways by three layers over the same tree —
  `scripts/derive.py` swallows it, `manifest.py` skips it, `bundle.py` raises — which turns a single
  torn file into an outage → C-02, whose upstream cause is collection auditor B's **B1** (the
  non-atomic envelope write), so B1 sequences first and this is its derive-side half.
- The spec forbade a clock in the loader and the code reached for one anyway, which converts
  reproducibility checking into drift detection and caps coverage at 16/18 → C-05.
- Silent substitutions at the edges of provenance — the wrong `paths[0]`, the wrong data root —
  produce provenance that is well-formed, hash-consistent, and describes the wrong thing, which is
  precisely the class of defect `verify` cannot see → C-04, C-06.
- Dead code here is ambiguous rather than harmless: an overwritten `None` and a test-only schema
  both hide what the author actually meant → C-03, C-08.
- **Disproved claim, so nobody re-raises it**: `_FORKS_DOC_KEY` cannot collide with a
  `RepoIdentity` field, because `cvi/core/models.py:274` `RepoIdentity` has **no `forks` field**.
- Worth crediting: `scripts/verify.py::_path_offender:191-220` is provenance-by-default-deny and
  treats the *absence* of a recorded hash as an offender; `manifest.py:84`'s `hash_origin` is the
  honest way to model backfilled hashes; `_dr90_decision_cohort:1359` recomputes rather than
  trusting; and `bundle.py:2007-2008` is a real completeness gate, not a decorative one.
- This slice has **no Blocker, and that is worth saying out loud** — the realistic failure modes are
  availability (C-02) and reproducibility (C-05), not a corrupted score shipping silently.

**Coverage**

- **Read fully**: `cvi/derive/bundle.py`, `cvi/derive/gate_evidence.py`,
  `cvi/verify/pipeline_hook.py`, `cvi/provenance.py`, `scripts/derive.py`, `scripts/verify.py`,
  `scripts/promote.py`, plus targeted full reads of `cvi/collect/pike_signals.py::derive_w17_fields`
  and `env_path`, `cvi/snapshot/manifest.py`, `cvi/core/models.py::RepoIdentity`,
  `cvi/core/params/curves._apply`, and `tests/derive/{test_relative_provenance,test_malformed_events}.py`.
- **Skimmed**: `cvi/verify/detectors.py` (513 L), `cvi/verify/live_sample.py` (575 L),
  `cvi/snapshot/reproduce.py` (321 L), `scripts/gather.py`.
- **COULD NOT VERIFY**: `detectors.py` and `live_sample.py` in full — C-01's claim that no verifier
  checks hashes rests on an exhaustive grep, strong but not a read; `reproduce.py` beyond earlier
  greps (`computed_sha256 == entry["sha256"]`, `hash_mismatches`/`invalid_json`, cf.
  `tests/snapshot/test_reproduce.py:364,:378` P-07b), so C-02 may or may not be caught there;
  `cvi/core/commit_domains.py`; whether any live `data/raw/` envelope is actually torn, or any tree
  is missing a named collector key (the trigger rates for C-02 and C-04); C-09's `min_n` intent —
  the spec definition of the DR_90 denominator was not located; and, with no network available,
  C-07's fetch behaviour was unexercised.
- Tooling note: `Read` sometimes renders adjacent `bundle.py` lines merged (seen at the `:1703` and
  `:1808` windows). Both corruption suspects (`pipeline_hook.py`'s docstring and
  `tests/derive/test_relative_provenance.py:43`) were proven clean via
  `sed -n 'A,Bp' file | cat -A`, so no source-corruption finding is filed here.
<!-- END C-DERIVE -->

## Slice D — storage

<!-- BEGIN D-STORE -->

Slice verdict: the persistence layer is honest and well-provenanced, but the
custom-weight reprojection in `cvi/store/queries.py` silently re-implements the
canonical aggregation in SQL and gets one of its two rules wrong (the coverage
denominator), which then propagates into the tier. Separately, every store
connection opens without WAL or `busy_timeout` while the v2 write path mutates
the served database file in place, and the read path's own "no Python loop"
invariant is broken by `matrix_points` while the ranking index's ordering
columns are defeated by a `ROUND()` wrapper. All findings below are on the v2
code paths and an in-memory v2 schema; the live `data/cvi.db` is v1-only and
could not host them (see **Coverage**).

### D-01 — Warning — `reproject` cannot see D-MISS-5, so coverage and tier diverge from the canonical persona path

- **Where**: `cvi/store/queries.py:960-965` (coverage formula) and `:956-958`
  (composite formula) inside `reproject`; canonical rule at
  `cvi/core/aggregate.py:181-211` (`composite_coverage`, the same function
  `cvi.core.persona.persona_composite` calls); the `not_applicable` persistence
  at `cvi/core/aggregate.py:101-140`; the v2 column set at
  `cvi/store/schema.py:64-112`.
- **Evidence**: coverage is computed as `sum(w_p * COALESCE(c_p, 0))` over
  *all five* pillars divided by the *full* weight vector —
  `queries.py:960` `cov_terms.append(f"(? * COALESCE(c_{pillar}, 0))")`,
  `:962` `total_weight = float(sum(weights.values()))` (= 1.0), `:964-965`
  `coverage_sql = f"(({' + '.join(cov_terms)}) / ?)"` with `cov_params.append(total_weight)`.
  The canonical `composite_coverage` renormalises over *applicable* pillars only:
  `aggregate.py:204` `applicable = [pillar for pillar in constants.PILLAR_NAMES if pillars[pillar].applicable]`,
  `:205` `denominator = sum(weights[pillar] for pillar in applicable)`,
  `:209` `sum(weights[pillar] * pillars[pillar].coverage for pillar in applicable) / denominator`.
  When a pillar is `not_applicable`, `aggregate.py:133/138/139` persist it as
  `c_<pillar>=0.0`, `p_<pillar>=NULL`, `applicable=False`, but the v2
  `repositories` table has only `p_*`/`c_*` columns — no `applicable` flag — so
  the SQL cannot tell "not applicable" from "unmeasured". It is not the
  composite that breaks: `:956-958` renormalise the numerator/denominator per
  pillar with a `CASE`, so `composite_raw` matches canonical (confirmed — cvi
  agrees). It is the **coverage** denominator that is wrong.
- **Why it's wrong**: a pillar that is legitimately `not_applicable` (D-MISS-5)
  contributes `0` to the numerator yet its full weight stays in the
  denominator, so composite coverage is diluted by a fact about the world
  rather than a measurement gap. Reproduced on the in-memory v2 schema
  (governance `not_applicable` w=0.20, other four pillars `score=80`
  `coverage=1.0`, balanced weights): canonical coverage `1.0` → tier S;
  `reproject` coverage `0.8` → tier A. Tier is `MIN(score_tier, ceiling(coverage))`
  (`queries.py:980-981` `_tier_by_score_case_sql("composite_raw")`,
  `_ceiling_index_case_sql("coverage_custom")`), so the same composite lands a
  tier lower purely because coverage was diluted. The divergence is also
  self-contradicting *within one returned row*: `reproject` returns both
  `balanced_coverage = 1.0` (the stored canonical value, renamed at
  `queries.py:1019` `record["balanced_coverage"] = record.pop("coverage")`) and
  `coverage = 0.8` (the recomputed one, `:1023`
  `record["coverage"] = record.pop("coverage_custom")`) under the same balanced
  weights — one row asserting coverage is both 1.0 and 0.8.
- **Impact**: any repo with a `not_applicable` pillar is under-covered and under-tiered
  on the custom-weight endpoint only; balanced and stored values stay correct.
  A reviewer comparing `coverage` against `balanced_coverage` on that endpoint
  sees an unexplained gap that looks like data corruption but is a
  reprojection bug. Does not affect repos with no `not_applicable` pillars, so
  it can sit unnoticed for a long time.
- **Fix**: persist applicability (a bit column, or the sentinel that a
  `not_applicable` pillar differs from an unmeasured one) so `reproject` can
  exclude it from the coverage denominator exactly as `composite_coverage`
  does — or compute custom coverage in Python by reusing `composite_coverage`
  rather than re-deriving it in SQL.
- **Confidence**: high — the two formulas are quoted verbatim and the divergence
  is reproduced numerically (coverage 1.0 vs 0.8, tier S vs A) on the real v2 schema.

### D-02 — Warning — no WAL / `busy_timeout`, and the v2 write path mutates the served database in place

- **Where**: `cvi/store/schema.py:156-173` (`connect`),
  `cvi/serve/app.py:684-695` (`_open_v2_connection`),
  `cvi/snapshot/store_cmd.py:217-233` (`write_scored_snapshot`),
  `cvi/snapshot/cli.py:170` and `scripts/run_all.py:97` (both call
  `connect_store(Path(args.db))` / `connect_store(Path(db))` on the served
  path). Contrast the safe v1 path: `scripts/promote.py` `promote_atomically`
  (≈418-460).
- **Evidence**: `schema.py:170-173` `conn = sqlite3.connect(path)` then only
  `PRAGMA foreign_keys = ON` and `create_function("py_round", ...)` — no
  `PRAGMA journal_mode=WAL`, no `PRAGMA busy_timeout`. The serve connection is
  the same: `app.py:684-695` `sqlite3.connect(db_path, check_same_thread=False)`
  plus `foreign_keys=ON`, nothing else. So the served file stays in
  rollback-journal (`delete`) mode, where a second writer does not queue — it
  returns `database is locked` immediately. `write_scored_snapshot`
  (`store_cmd.py:217-233`) runs `conn.execute("BEGIN")` … `commit()` directly
  against `db_path`, and both v2 entry points open that same served path via
  `schema.connect`/`connect_store`. `promote_v2` in `run_all.py:97` runs
  `apply_migrations` + `write_scored_snapshot` against `Path(db)`.
- **Why it's wrong**: the v1 promote path is safe by design precisely because it
  never touches the served inode — `promote.py` builds into
  `<db>.tmp-<snapshot_id>` and finishes with `os.replace`, an atomic rename.
  The v2 snapshot path has no such guard: it takes a RESERVED→EXCLUSIVE
  transaction on the very file the server holds open, with no `busy_timeout` to
  absorb the overlap. In rollback-journal mode that collides as
  `sqlite3.OperationalError: database is locked` on either side.
- **Impact**: the first time a v2 snapshot is promoted while the API is serving
  the same file, a reader (or the writer) gets a spurious lock error and a
  promotion can fail halfway. This is forward-looking and medium-high: it is
  not observed live today only because the current `data/cvi.db` is v1-only, so
  no v2 promotion has ever run against a served database yet.
- **Fix**: set `PRAGMA journal_mode=WAL` and a `busy_timeout` in *both*
  `schema.connect` and `_open_v2_connection`, or make the v2 write path mirror
  v1: build into a temp file and `os.replace` the served path, never mutating
  the inode a server has open.
- **Confidence**: high on the structural gap (the two connects are quoted;
  `journal_mode=delete` confirmed via `PRAGMA journal_mode` on the live db);
  `COULD NOT VERIFY`: an actual `database is locked` under a live concurrent
  collector — no v2 promotion against a served db has occurred.

### D-03 — Consider — `reproject` tiers on the full-float composite; canonical rounds to 12 places first

- **Where**: `cvi/core/aggregate.py:81` (`RATIO_DECIMALS: int = 12`) and
  `:214-237` (`composite_score_raw`, rounds at `:237`
  `return round(sum(...) / total_weight, RATIO_DECIMALS)`), consumed by tier
  comparisons in `cvi/core/persona.py:362,368`; versus `reproject` at
  `cvi/store/queries.py:980`, which feeds the unrounded `composite_raw` straight
  into `_tier_by_score_case_sql("composite_raw")`.
- **Evidence**: `RATIO_DECIMALS` is 12 and is *not* in `constants.py` (defined
  locally at `aggregate.py:81`). Canonical `composite_score_raw` rounds to 12
  places **before** any tier/cutoff comparison (`persona.py:362,368`), whereas
  `reproject`'s `composite_raw` (`queries.py:963`, `NULLIF`-divided float) is
  passed to the tier `CASE` unrounded (`:980`).
- **Why it's wrong**: a composite of `90.0 - 1e-13` rounds to `90.0` at 12
  decimals and clears the S+ cutoff on the canonical path, but stays
  `89.9999999999999` as a raw float on the reproject path and falls to S. The
  mechanism is shown by the rounding rule, not reproduced against a database.
- **Impact**: off-by-one tier at an exact cutoff boundary, reachable only by a
  composite landing in the last float ulp below a threshold — rare, but the same
  divergence class as D-01 and it compounds with it.
- **Fix**: apply `ROUND(composite_raw, 12)` before the tier `CASE` in
  `_tier_by_score_case_sql` so reproject matches canonical's rounding point.
- **Confidence**: medium — the mechanism (round-then-compare vs compare-raw) is
  certain from the two quoted call sites; a concrete DB row straddling the
  boundary was not found.

### D-04 — Consider — read path breaks its own "no Python loop" invariant and half-uses the ranking index

Measured with `EXPLAIN QUERY PLAN` on the in-memory v2 schema (3,000 rows),
reproduced verbatim:

```
list_ranked balanced PAGE:
   SEARCH s USING INDEX sqlite_autoindex_snapshots_1 (id=?)
   SEARCH r USING INDEX idx_rank (snapshot_id=? AND rated=?)
   USE TEMP B-TREE FOR ORDER BY
list_ranked persona PAGE:
   SEARCH r USING INDEX idx_eligible (snapshot_id=? AND eligible=?)
   USE TEMP B-TREE FOR ORDER BY
snapshot_summary:
   SEARCH r USING INDEX idx_eligible (snapshot_id=?)
   SCAN je VIRTUAL TABLE INDEX 1:
```

- **`matrix_points` contradicts the module's own docstring** — `queries.py:4-6`
  asserts "no Python loop over rows for any endpoint", yet `matrix_points` loads
  every rated row into `points` (`queries.py:772`
  `points = [dict(zip(columns, row)) for row in raw_rows]`) and Python-loops it
  for `axis_partial_count` (`:774-779`). **Fix**: fold the count into SQL,
  `SUM(CASE WHEN friction_coverage < 1.0 OR leverage_coverage < 1.0 THEN 1 ELSE 0 END)`.
- **The ranking index's ordering columns are dead** — `idx_rank`
  (`schema.py:144`) tails `cvi DESC, coverage DESC`, but the balanced page
  `ORDER BY` leads with `ROUND(cvi, 2)` (`queries.py:451`, applied at
  `:460`) plus a tie-break tail (tier, owner, name, ecosystem) that is not in
  the index, so SQLite cannot use the index order and re-sorts every page —
  exactly the `USE TEMP B-TREE FOR ORDER BY` above. The `COUNT(*)` path is fine
  (covering index). **Fix**: index the expression `ROUND(cvi,2)` (and the
  tie-break columns), or order on the stored 2-decimal column directly.
- **`reproject` scans twice** — `total = COUNT(*) FROM (visible_sql)`
  (`queries.py:986`) and the ranked CTE re-runs `visible_sql` (`:992-999`): two
  full projections of the same set plus a `ROW_NUMBER()` window sort per call.
- **Nit / FYI tail**:
  - `snapshot_summary` full-scans the `json_each(r.suspicion) je` virtual table
    (`queries.py:697`) — the `SCAN je VIRTUAL TABLE INDEX 1:` above; joins are
    non-fan-out (`repositories`→`snapshots` is N:1 on the PK, and `COUNT` is
    scoped to one `snapshot_id`), so no double-counting — the scan is a cost,
    not a correctness bug.
  - `list_not_ranked` (`queries.py:516`) Python-slices the whole not-ranked set
    (`all_rows[offset:offset+limit]` at `:628`); explicitly justified in-code and
    low-concern.
  - `OperationalStore._leaderboard_cache` (cap 512) staleness is v1-path only —
    informational, out of the v2 slice.

**Confidence**: high for the EQP shapes (measured) and the docstring
contradiction (quoted); the perf impact is a scaling argument, not a measured
latency.

## Slice summary

- **The schema omits the applicability bit, so any custom-weight recompute
  silently diverges from the canonical path.** `aggregate.py` carries D-MISS-5
  (`applicable`), the `repositories` table does not, so `reproject`'s SQL treats
  a `not_applicable` pillar as unmeasured and dilutes coverage (D-01). The
  custom-weight endpoint is the only reader of that SQL, so it fails quietly.
- **The read path violates its stated invariant and defeats its own indices.**
  `queries.py:4-6` promises "no Python loop" while `matrix_points` loops in
  Python; `idx_rank` carries ordering columns that a `ROUND(cvi,2)` wrapper makes
  unusable (D-04). Both are self-consistency breaks the code can be made to pass
  without a redesign.
- **Every connection lacks WAL/`busy_timeout` while v2 writes the served file in
  place** — the opposite of v1's temp-plus-`os.replace` discipline (D-02). The
  safety was proven for v1 and not carried into v2.
- **Rounding is done in two places on two rules.** Canonical rounds to
  `RATIO_DECIMALS=12` before comparing (and the constant lives outside
  `constants.py`, against guidelines §4); reproject compares the raw float
  (D-03), so "one source per fact" does not hold for the tier boundary either.
- **Follow-up (§4), all in `queries.py`:** fold `matrix_points`'
  `axis_partial_count` into SQL; index the `ROUND(cvi,2)` expression plus the
  tie-break columns so `idx_rank`'s ordering is actually used; and stop
  re-running `visible_sql` twice per `reproject` (count and page from one pass).

## Coverage

- **Read fully**: `cvi/store/queries.py`, `cvi/store/schema.py`,
  `cvi/core/aggregate.py`.
- **Skimmed**: `cvi/store/writer.py`, `cvi/store/ops.py`,
  `cvi/snapshot/{store_cmd,cli}.py`, `cvi/serve/app.py`,
  `scripts/{promote,run_all}.py`.
- **Verified-by**: `.venv/bin/python -m pytest tests/store/` → **128 passed in
  6.70s** (pytest 9.1.1). The `sqlite3` CLI is not installed here; all SQL was
  run through `.venv/bin/python` and Python's `sqlite3` module (SQLite lib
  3.53.1).
- **COULD NOT VERIFY**: the live `data/cvi.db` is a **V1 lakehouse only**
  (`repositories` ≈1,000 rows with v1 columns `overall_cvi`/`rank_tier`/
  `deep_dive_json`, `PRAGMA user_version=0`, `journal_mode=delete`, integrity
  check OK) — it has **no v2 tables**, so the v2 `queries.*` functions cannot run
  against it as-is; every §1–§4 finding above is on the v2 code and an in-memory
  v2 schema, not on the live file. `database is locked` (D-02) was **not**
  reproduced under a live concurrent collector because no v2 promotion has ever
  run against a served database in this working tree. Whether a persona repo in
  production actually carries a `not_applicable` pillar (the precondition for
  D-01 to bite) was not sampled against live data.

<!-- END D-STORE -->

## Slice E — snapshot and reproducibility

<!-- BEGIN E-SNAPSHOT -->
Auditor E reviewed the snapshot/reproducibility layer — `cvi/snapshot/{manifest,build_cmd,store_cmd,fixture_io,reproduce}.py`, `cvi/core/constants.py`, `cvi/constants.py`, `cvi/store/writer.py`, `cvi/serve/guard.py`, `scripts/{promote,make_sample,build_fixture_snapshot}.py` — against `tests/core/test_constants_hash.py` and `tests/snapshot/{test_reproduce_fixture,test_populations}.py`. What was actually executed: `pytest tests/core/test_constants_hash.py tests/snapshot/test_reproduce_fixture.py -p no:cacheprovider -q` → **11 passed, 1 skipped**; `pytest tests/snapshot/test_populations.py -p no:cacheprovider -q` → **22 passed**; a runtime probe computing both `constants_hash` implementations (see E-02). No source file was modified and nothing under `cvi/`, `scripts/`, `web/`, `tests/`, `docs/`, `data/` or git state was written to. Finding IDs are `E-01`… by severity; the labels `Finding B` / `Finding A` from the working analysis are kept in the titles so the two carry-over follow-ups stay traceable.

**Bottom line on the slice question:** a stranger *can* reproduce a published number from the snapshot alone along each pipeline's intended path — the v2 store/serve guard and the v1 reproduce path are internally consistent, and the `snapshot_50` fixture is pinned as a permanent CI gate (`test_reproduce_matches_expected_hash`). What breaks first is not arithmetic but **certification coverage**: the snapshot *directory's* Merkle root silently omits `populations.json` (E-01), so two dirs with an identical `merkle_root` can score differently, and the field literally named `constants_hash` in the legacy manifest means a *different* number than the canonical core hash (E-02). Both are invisible to the current suite.

### E-01 — Warning — Snapshot-dir Merkle root omits `populations.json` (Finding B)

- **Where**: `cvi/snapshot/build_cmd.py:174` `    root = merkle_root(sorted(raw_hashes.values()))`, stored as `build_cmd.py:199` `        "merkle_root": root,`; populations are written one line earlier at `build_cmd.py:171` `    write_populations(populations, out_dir / "populations.json")` but their hash is never added to `raw_hashes`. The store recomputes the same root: `cvi/snapshot/store_cmd.py:177` `    return merkle_root(sorted(frozen.raw_hashes.values()))`. `cvi/snapshot/fixture_io.py:60` `    expected = json.loads(expected_path.read_text(encoding="utf-8")) if expected_path.is_file() else None` — production snapshots ship no `expected.json`, so `expected` is `None` and `verify_expected` (`store_cmd.py:147-163`) no-ops, never checking populations either.
- **Why it's wrong**: inside the published directory the Merkle root certifies only the per-repo bundle `raw_hash`es. The population ECDFs directly drive percentile scores and composite ranking, yet a hand-edited `populations.json` sitting in the same dir is uncertified at the directory level.
- **Impact**: two snapshot dirs sharing one `merkle_root` can carry different `populations.json` and produce different published numbers — "reproduce from the snapshot alone" fails while every hash still matches. Severity leans LOW because the *post-store DB* guard does cover it: `cvi/store/writer.py:454` `_SNAPSHOT_DUMP_TABLES = ("repositories", "params", "populations")` is folded into `snapshot_dump_hash` (`writer.py:457-505`), and `cvi/serve/guard.py:282` `                recomputed = snapshot_dump_hash(Path(db_path), snapshot_id)` (compared at `:283`) refuses on mismatch. The exposure is the snapshot-dir → store boundary, *before* the DB guard exists.
- **Fix** *(carry-over follow-up #1)*: fold the `populations.json` content hash into `merkle_root` / `manifest_root_of`, or add a test asserting the directory-level root covers `populations.json`. No test currently asserts this.
- **Confidence**: high — omission read directly off both call sites; DB-side mitigation read off `writer.py`/`guard.py` and exercised by the passing store/guard suite.

### E-02 — Warning — Two different `constants_hash` definitions under one name (Finding A, CA-23 "one fact, one definition")

- **Where**: legacy `cvi/snapshot/manifest.py:23` `from cvi import constants as _constants` feeds `cvi/snapshot/manifest.py:163-174` `def constants_hash() -> str:` which hashes UPPER_CASE bindings of `cvi/constants.py` via `envelope_hash`. Core `cvi/core/constants.py:600-601` `CONSTANTS_HASH: str = hashlib.sha256(` / `    _canonical_json(constants_dict()).encode("utf-8")`, consumed at `cvi/snapshot/build_cmd.py:197` `        "constants_hash": constants.CONSTANTS_HASH,`.
- **Why it's wrong**: the two disagree. Runtime probe this session: legacy `manifest.constants_hash()` = `2282711184d229fc38e04c23eb45ea58c4c3e5728e4bad8ac0be54e0a183fd15`, core `CONSTANTS_HASH` = `748be6e459b0d9eac279233dac75acd5ede7da9711590c84fd0317a84762cb6b`. The tables genuinely differ — legacy `cvi/constants.py:89-94` `LEGAL_IP_SCORES` `{DCO:100.0, FOUNDATION_CLA:75.0, CORP_CLA_RECIPROCAL:40.0, CORP_CLA_UNILATERAL:10.0}` vs core `cvi/core/constants.py:163-168` `{DCO:100.0, NONE:85.0, FOUNDATION_CLA:75.0, CORP_CLA:25.0}`.
- **Impact**: each pipeline is internally self-consistent (v1 reproduce reruns v1 scripts on `cvi.constants`; v2 on core), so within-pipeline reproduce is fine. The hazard is a reader who sees a field named `constants_hash` in a legacy manifest, assumes it is the canonical core `CONSTANTS_HASH`, and mis-identifies which tuning constants a snapshot was frozen against. `cvi/audit/sensitivity.py` imports **both** (already flagged at `.agents/W4-1/inventory.md:144,552`).
- **Fix** *(carry-over follow-up #2)*: unify under one definition per CA-23, or add a cross-check/documenting test asserting legacy `manifest.constants_hash()` is a distinct value and rename to kill the collision. `tests/core/test_constants_hash.py` imports only `cvi.core.constants`, so no test cross-checks the two — the collision is uncaught.
- **Confidence**: high — both hashes computed at runtime this session and confirmed to diverge; table deltas read verbatim from both files.

### E-03 — Consider — Fresh sample→fixture chain is not byte-reproducible (masked in CI)

- **Where**: `scripts/make_sample.py` stamps the sample manifest `generated_at = datetime.now(timezone.utc)` (wall clock); `scripts/build_fixture_snapshot.py` reads it back as `as_of`, which feeds scoring windows. The committed `tests/fixtures/snapshot_50/manifest.json` pins a fixed `as_of`, so *fixture* reproduce is stable.
- **Why it's wrong**: only a from-scratch sample regeneration is non-deterministic, and `test_reproduce_fixture.py::test_fixture_rebuild_is_byte_identical` **SKIPS** when the sample corpus is absent (the 1 skip in the run above).
- **Impact**: a real `as_of`-regression in the fresh chain would slip through CI; the byte-identical guarantee holds only for the pre-committed fixture.
- **Fix**: make `make_sample` accept an explicit `as_of` (or accept the non-determinism as out-of-scope and delete the skip so the gap is visible rather than silent).
- **Confidence**: medium — `make_sample`/`build_fixture` read from the analysis, not re-opened this session; the skip was confirmed live in the run.

### E-04 — FYI — v1 reproduce verifies via dump hash, not `constants_hash`

- **Where**: `cvi/snapshot/reproduce.py` `rebuild()` reruns `scripts/{derive,verify,promote}.py` over the frozen `raw/` symlink at the manifest `as_of`, then requires `canonical_dump_hash` to match `manifest["canonical_dump_hash"]`.
- **Why it's a note, not a defect**: the dump hash is a sufficient content check and is float-normalized (`FIXED_FLOAT_DECIMALS = 9`) and row-sorted → platform-stable; `scripts/verify.py` does not independently re-assert `constants_hash` at reproduce time. Recorded so it is not mistaken for a gap in E-02's mitigation.
- **Fix**: none required.
- **Confidence**: high.

### Low-severity tail

- **FYI** — `cvi/snapshot/build_cmd.py:67-68` (`_BootstrapPopulationLookup.ecdf`) and build_fixture pass-1 return a constant `lambda _val: 50.0`; intentional (pass 1 only needs gate/rated status to size populations; pass 2 uses real ECDFs). Not a defect.
- **FYI** — `scripts/promote.py` is deterministic given `as_of`: `resolved_as_of` (`promote.py:206-231`) forces CLI `--as-of` == manifest `as_of` (compared as instants); wall-clock `datetime.now` appears only at the script boundary (`promote.py:362`, when `as_of` is None) and for the `promoted.json` receipt timestamp (`promote.py:413`) — a receipt, not scored data.
- **FYI** — `cvi/snapshot/store_cmd.py::write_scored_snapshot` (`store_cmd.py:180-234`) takes `as_of`/`created_at`/`promoted_at` all from `frozen.as_of`; no wall clock reaches scored output.

**Slice summary**

- **Certification lags computation.** The Merkle root (E-01) and the DB dump hash (`writer.py:454`) disagree on what belongs in the certified set — populations are in the DB hash but not the dir hash. The same fact is protected in one layer and not the other.
- **One name, two definitions.** `constants_hash` (E-02) is the textbook CA-23 violation: an audit-trail field whose meaning depends on which module you happened to import.
- **Green ≠ covered.** Both the E-01 dir gap and the E-02 collision are invisible to the suite — no test asserts the dir root covers populations, and none cross-checks the two hashes. The suite is genuinely strong where it looks (`test_populations.py` pins determinism, `MIN_POPULATION=30` fallback, emission order across `PYTHONHASHSEED`).
- **The certified artifact is ill-defined.** Is it the directory or the DB? The answer changes E-01 between Medium and Low — the repo has not picked.
- **Reproducibility is real along the pinned path.** The `snapshot_50` fixture through real `score_snapshot_with_detectors` is a durable gate; the fragility is at the edges (fresh sample chain E-03, hand-edited dir E-01), not the core arithmetic.
- **Guard/verify are sound where they act** (`guard.py` refuses the fixture-clock sentinel `datetime(2026,9,11,12,0,0)` and any hash mismatch) — they simply do not act at the snapshot-dir boundary.

**Coverage**

- **Read fully**: `cvi/serve/guard.py`, `scripts/promote.py`, and the load-bearing regions of `cvi/snapshot/{manifest,build_cmd,store_cmd,fixture_io}.py`, `cvi/store/writer.py`, `cvi/core/constants.py`, `cvi/constants.py`, `cvi/snapshot/reproduce.py`, `tests/core/test_constants_hash.py`, `tests/snapshot/{test_reproduce_fixture,test_populations}.py`.
- **Skimmed / relied on from working analysis**: `scripts/{make_sample,build_fixture_snapshot}.py`, `.agents/W4-1/inventory.md`.
- **COULD NOT VERIFY**: `scripts/derive.py`, `scripts/verify.py`, `cvi/lakehouse.py` (`repositories_dump_hash`/`OperationalStore`), `cvi/serve/{app,contract}.py`, `cvi/store/schema.py` + `migrations/0001_init_v2.sql`, `tests/snapshot/test_import_order.py`, `tests/serve/test_guard.py`, `tests/core/test_frozen.py` — treated as confirmatory only because the guard/reproduce core was verified sound and the passing serve/store/populations suites cover their ground; none would change E-01/E-02. E-03's `make_sample`/`build_fixture_snapshot` clock behavior was carried from the earlier analysis and not re-executed as a fresh from-scratch sample build this session.
<!-- END E-SNAPSHOT -->

## Slice F — audit tooling

<!-- BEGIN F-AUDIT -->
Auditor F reviewed the audit tooling against what it claims to verify — all of `cvi/audit/**`
(`anchors.py`, `sensitivity.py`, `forward.py`, `coverage.py`, `detectors_report.py`,
`methodology_history.py`, `provenance.py`, `diff.py`), the frozen validation contract
`docs/v2/validation/PREREG.md`, the CI/cron wiring (`scripts/canary.sh`, `scripts/run_all.py`,
`.github/workflows/ci.yml`), and the collectors the audit layer is supposed to watch
(`cvi/collect/universe.py`, `scripts/build_universe.py`) — plus the audit test suites under
`tests/audit/`. Findings are `F-01`…`F-08` by severity, with a short tail. Nothing under
`cvi/`, `scripts/`, or `docs/` was modified.

**Headline: which "verified" relationships cannot fail.** The numeric gates can genuinely
fail and do exercise their arithmetic — the tie-break checks, forward-significance criteria,
Kendall tau-b and AUC computations are real cross-checks recomputed from stored evidence, and
this review found no defect in the statistics themselves. What cannot fail is different, and
it is where the false-clean signal lives. The `newcomer_label_5_1` anchor (F-01) is
structurally unverifiable in **all three** implementations: the v1 anchor reports a vacuous
PASS on zero repos and a boilerplate FAIL on any count (the FAIL is pinned as the *expected*
value in `tests/audit/test_anchors.py:52`), the v2 snapshot anchor is an unconditional SKIP,
and the v2 DB anchor PASSes on "N repos score 5.1 > 0" — a re-reading of the score being
checked, never an independent raw count. The strongest false-clean claim in the whole audit
suite is therefore the one that has never compared a score to its input. Close behind, the
release gate (F-02) cannot fail mechanically: its FAIL verdict exists only in printed text and
a `--json` field, the CLI exits 0 unconditionally, and nothing in CI or `canary.sh` consumes
the report at all — a fully broken ranking would still ship with a green pipeline. The
remaining findings are quieter but follow the same pattern: defaults that answer for missing
data (`measured=False`, NULL→0.0, coverage→1.0), frozen-contract promises with no guard
tests, and one collector (`star_history`) that is 100% unmeasured in every real run yet
invisible to the coverage auditor by construction.

### F-01 — Blocker — The `newcomer_label_5_1` anchor cannot verify the relationship it claims to anchor

**Where** — `cvi/audit/anchors.py:245-256` (v1), `:1013-1022` (v2 snapshot), `:632-713` (v2 DB)

**Why it's wrong** — Three implementations of the same anchor, none capable of cross-checking
parameter 5.1 against its input. The v1 `_check_newcomer` returns `PASS` with
`"...(vacuous)"` when `count == 0`, and for any count > 0 returns `FAIL` with the fixed string
`f"{count} rated repos with newcomer-label count >= 1; no per-parameter 5.1 score stored"` —
the same verdict regardless of the data. The test suite cements this:
`tests/audit/test_anchors.py:52` asserts `"newcomer_label_5_1": "FAIL",` as the *expected*
verdict, and `:155-158` asserts the boilerplate observed string verbatim
(`"3 rated repos with newcomer-label count >= 1; no per-parameter 5.1 score stored"`). The v2
snapshot variant `_check_newcomer_v2` returns `SKIPPED` unconditionally:
`"ScoreRecord.params carries only the curved score, no independent raw newcomer-label count to cross-check it against (04 section 3 gap)"`.
The v2 DB variant `_check_newcomer_db_v2` queries `params` for `param_id IN
('newcomer_issue_supply', '5.1')` (`:638-641`); when zero raw rows exist but positive scores
do, it returns `f"{positive_scores} rated repo(s) score 5.1 > 0", PASS` (`:676-682`) — that
"PASS" restates the score being audited, with zero independent observation, and the
zero-GFI-repos path also ends in `PASS` (`"...(vacuous)"`, `:711-712`).

**Impact** — Every possible input produces PASS (vacuous or score-restating), SKIP, or a
constant FAIL: there is no world in which this anchor detects a wrong 5.1 score. Yet it is
listed alongside the anchors that do real work, so the anchor report reads as if the newcomer
pipeline were spot-checked when it never is. The v2-DB vacuous/positive-score PASSes are the
dangerous pair — they are *clean* verdicts with no evidence behind them.

**Fix** — Persist the raw newcomer-label count per repo (the 04 section 3 gap the SKIP
message names), then make all three variants compare stored 5.1 scores against it and return
NOT-APPLICABLE (not PASS) when the raw column is absent. Update
`tests/audit/test_anchors.py:52,155-158` to the new contract instead of pinning the
boilerplate FAIL.

**Confidence** — High. All three code paths and the test expectations read in full this session.

### F-02 — Blocker — The release gate cannot fail: exit 0 is unconditional and nothing consumes the verdict

**Where** — `cvi/audit/sensitivity.py:509-540`; `scripts/canary.sh`; `scripts/run_all.py`; `.github/`; `tests/audit/test_sensitivity.py`

**Why it's wrong** — `main()`'s own docstring states the contract: `"Exits 0 when the report
completes (a failed release gate is data, not an error); exits 2 on usage or database
errors."` The function then executes `return 0` (`:540`) unconditionally; the computed
`all_pass` — which does correctly fold every criterion including a `tau_b is None` failure
(`:212-217`) — is only serialized into the `--json` payload (`:534`). `scripts/canary.sh`
never invokes the audit CLI (it runs `cvi.verify.live_sample` only), `scripts/run_all.py` and
`.github/workflows/ci.yml` contain no call to any `cvi.audit.*` entry point, and
`tests/audit/test_sensitivity.py` contains no test of `main()` or its exit code. Compare the
forward checker, which *does* map verdicts to status — `forward.py:752-753`
`if any(c.verdict == "FAIL" for c in report.criteria): return 1` — yet even there no test
asserts `code == 1` on a FAIL report.

**Impact** — A ranking that fails every release criterion exits 0, prints its report, and the
pipeline stays green. Because no CI job, cron, or test even *runs* the gate, its FAIL text is
never read by anything mechanical. As shipped, "the release gate" is a report nobody consumes
— the audit suite's own certification path is decorative.

**Fix** — Return 1 when `--json` is requested and `all_pass` is false (or add a
`--fail-on-gate` flag and wire it into CI), add a `main()`-level test for both exit paths, and
call the gate from `canary.sh` or a CI job so the verdict reaches something that acts on it.

**Confidence** — High.

### F-03 — Warning — Forward evaluation silently converts missing outcomes to 0.0, which PREREG does not authorize

**Where** — `cvi/audit/forward.py:215` (v2 branch) and `:229` (v1 branch)

**Why it's wrong** — The outcome loaders coerce absent values to zero: the v2 branch maps
`canon: (float(val) if val is not None else 0.0)` and the v1 branch `column: (float(value) if
value is not None else 0.0)`. `docs/v2/validation/PREREG.md:85-91` enumerates exactly three
sanctioned reasons a repo may be excluded as `UNDECIDED` — `UNDECIDED:insufficient_repos`,
`constant_score`, `one_class` — and says nothing about treating an unmeasured outcome as a
zero outcome. A repo with no recorded PR merges between snapshots is thereby scored as *zero
merges* and stays in the rho/AUC computation, not dropped.

**Impact** — Missing data is indistinguishable from a real zero and actively contributes to
the significance statistics in the direction that flatters "forward prediction looks
predictive" (many tied zeros weaken rank correlation but inflate nothing that would flag it).
The report cannot disclose this because the coercion leaves no trace: the pair just looks like
an observation of zero.

**Fix** — Exclude NULL outcomes as a fourth, declared `UNDECIDED` reason (or amend PREREG to
authorize imputation and state the direction of bias), and count them into the report so the
substitution is visible.

**Confidence** — High on the code paths and the PREREG enumeration; Medium on how often real
snapshots actually carry NULL outcomes (see Coverage).

### F-04 — Warning — The forward report never discloses the S0 truncation caps PREREG requires it to disclose

**Where** — `docs/v2/validation/PREREG.md:43-45`; `cvi/audit/forward.py:139-155`

**Why it's wrong** — PREREG fixes the evidence envelopes: `"S0 envelopes carry at most 300
commits (180 d) and at most 30 PRs. ... and the caps are disclosed in the report."` The
`ForwardReport` dataclass carries only `as_of, n_repos_rated, n_repos_in_analysis,
n_prs_eligible, excluded, pairs, criteria`, and `to_dict` is `asdict` plus an isoformat of the
date — no cap field, no per-repo "was this truncated" count, nothing downstream could use to
disclose it.

**Impact** — A frozen, pre-registered disclosure obligation is silently unmet: the audit
artifact PREREG itself designates as the disclosure venue has no place to put the disclosure.
Since the caps clamp exactly the heavy repos the 180-day window favors, the unmet obligation
lands on the most influential observation in the validation design.

**Fix** — Add `commit_cap`, `pr_cap`, and the count of repos whose evidence hit each cap to
`ForwardReport`, populate them where the envelopes are read, and assert them in a test so the
contract can't regress again.

**Confidence** — High.

### F-05 — Warning — "Rated universe" has two definitions across the forward checker's branches, with no declared equivalence

**Where** — `docs/v2/validation/PREREG.md:36-37`; `cvi/audit/forward.py:209-213` vs `:222-227`; `cvi/store/queries.py:345-352`

**Why it's wrong** — PREREG defines the analysis universe by `is_stage0_disqualified =
FALSE`. The forward checker's v1 branch uses `not bool(disqualified)` from the snapshot
column, while the v2 branch reads the DB `rated` column instead. `cvi/store/queries.py:345-352`
documents that `rated` column as the *coverage* flag — repos meeting the balanced
D-MISS-3(0.60) coverage bar — not as the disqualification complement. Nothing in code,
docs, or tests declares or checks that `rated` ⟺ `NOT is_stage0_disqualified`, and the two
flags are computed by different logic in different places.

**Impact** — The same pre-registered statistic is computed over different populations on the
two paths; if the flags diverge for any repo (a disqualified-but-covered repo, or a
clean-but-thin one), the two branches answer different questions under one label. Because no
guard test compares them on real data, divergence is invisible until someone diffs two audit
runs that used different inputs.

**Fix** — Pick one definition, derive the other from it at write time, and add a store-level
test asserting the equivalence invariant (or drop one column).

**Confidence** — High that the divergence is undeclared and untested; Medium that it bites in
practice (I could not check whether the current snapshot's flag values actually differ).

### F-06 — Warning — Archived/awesome anchors disagree across the three implementations on what "no such repos" means

**Where** — `cvi/audit/anchors.py:909-919` (`_check_archived_v2`), `:452-455`
(`_check_archived_db_v2`), `:217-218` (`_check_awesome` vs `_check_awesome_v2`)

**Why it's wrong** — An empty population is the same fact in all these cases, but the audit
suite answers it three ways. `_check_archived_v2` returns `FAIL` when the snapshot contains no
archived repos, conflating "collection missed them" with "the universe has none" — a correct
snapshot can fail its own anchor. `_check_archived_db_v2` returns `"0 archived repos in DB
slice", SKIPPED` for the same condition. And awesome has the identical split: v1
`_check_awesome` returns SKIPPED on zero awesome repos while `_check_awesome_v2` returns FAIL.
Which verdict a given dataset gets depends on which auditor function you happen to invoke,
not on the data.

**Impact** — Anchor verdicts are not comparable across snapshot/DB or v1/v2 runs, so
regression diffs of anchor reports mix real regressions with routing artifacts. The FAIL side
of the split is the expensive one: a universe that legitimately contains no archived repo
fails the suite, and the natural "fix" is to collect junk to satisfy the anchor.

**Fix** — Pick one policy for empty-but-legal populations (SKIP with a stated reason, or FAIL
only when the reference lists demand members), implement it once, and route all three
variants through it.

**Confidence** — High.

### F-07 — Warning — The detectors report treats a missing `measured` key as "not measured", so a producer bug reads as a clean report

**Where** — `cvi/audit/detectors_report.py:96`

**Why it's wrong** — `measured = bool(payload.get("measured", False))`: a producer that
forgets the key entirely yields the same result as a detector that honestly reported
"unmeasured". The module docstring itself concedes (`:92-95`) that a payload missing the field
parses anyway, "as every example in this module's docstring does". The failure is silent in
both directions that matter — nothing errors, nothing logs, and the rendered report shows the
detector as simply not-run.

**Impact** — The whole point of a detectors report is to distinguish "tested and fine" from
"never tested"; this default collapses the third state ("the test couldn't express itself")
into the second. A rename or refactor on the producer side turns the report into a wall of
"not measured" rows that still look orderly, and the one signal the artifact exists to carry —
coverage — is the one it cannot report honestly.

**Fix** — Parse `measured` as a required key (KeyError → a `malformed` verdict for that
detector), or at minimum render "key absent" distinctly from `measured: false`.

**Confidence** — High.

### F-08 — Warning — `star_history` is unmeasured in every real run and the coverage auditor can never see it

**Where** — `cvi/collect/universe.py:206-215`, `:233-236`, `:70`; `scripts/build_universe.py:45,176-179`;
`cvi/audit/coverage.py:71-100`

**Why it's wrong** — The transport is a placeholder: `default_star_history_transport` does
`raise NotImplementedError(...)`, and its docstring states plainly that "a real run of
`scripts/build_universe.py` reports every candidate `Unmeasured(collector_failure)`";
`scripts/build_universe.py:45` binds `STAR_HISTORY_TRANSPORT: Transport =
default_star_history_transport` and passes it per-candidate at `:176-179`. In
`collect_star_history` (`universe.py:233-236`) `except Exception:  # review-ok:
gate-network -- transport failure -> "fail"` swallows the `NotImplementedError` into a soft
fail, which routes to `unmeasured_star_velocity` — D-MISS-1 compliant, and the module docstring
(`build_universe.py:17-18`) is candid about it, so the *collector* layer degrades correctly.
The gap is in the audit layer: the coverage auditor enumerates collectors through the
`V1_COLLECTORS` / `V2_COLLECTORS` / `COLLECTORS` lists in `cvi/audit/coverage.py:71-100`, and
none contains `"star_history"` — the envelope directory name is `STAR_HISTORY_COLLECTOR =
"star_history"` (`universe.py:70`), and the only mention anywhere in `cvi/audit/` is a
lockstep note in `methodology_history.py:95`. `data/raw/` confirms the state on disk: there is
no `star_history/` directory at all.

**Impact** — A collector that has never measured anything in a real run is outside the very
tool whose job is to report unmeasured coverage. The coverage report can be green while 100%
of one input's observations are absent, because the auditor's denominator was built without
it. This is Warning, not Blocker, because the shortfall is explicit in code and docs and the
pipeline treats it as unmeasured rather than fabricating a value — the defect is audit blind
status, not a false number.

**Fix** — Add `star_history` to the collector inventory in `coverage.py` so its 100%-unmeasured
share is reported every run, and keep it there until a real transport lands (the D-MISS-1
path then makes the number self-explaining).

**Confidence** — High for the code inventory and the on-disk data check; the "every real run"
claim is the module docstring's own, and no other transport is wired anywhere in the repo.

**Tail (Consider / FYI / Nit)**

- **FYI** — `forward.py` in plan-only mode with several DBs picks `sorted((ref.snap_dir /
  "build").glob("*.db"))[0]` (`:669`) and then rewrites `forward_report.json` anyway
  (`:730-741`): a plan invocation with multiple candidate DBs mutates the artifact and the
  chosen DB is the lexicographic first, not a stated one.
- **FYI** — `sensitivity_v2` scores the canonical as the stored rank (`sensitivity.py:554-556`
  comment concedes it), so tau-b is lifted by the tiebreak chain itself; on a fixture where
  composites tie, tau = 1.0 and the release gate PASSes vacuously — the gate is partly
  measuring the tie-breaker against itself.
- **Consider** — `all_pass` correctly treats `tau_b is None` as a failure
  (`sensitivity.py:212-217`), but nothing consumes it (F-02): the right verdict is computed in
  the right place and then discarded.
- **Nit** — `sensitivity.py:610-611` reads `record.tie_key[5]` / `record.tie_key[6]` by
  position against the tuple built in `cvi/core/tiebreak.py:259-267` (currently
  `ttfhr_p75` / `commit_epoch`). Correct today; a tuple reorder silently re-points the
  comparison at the wrong tie-break layer with no test to catch it.
- **Nit** — `_v2_snapshot_from_db` (`sensitivity.py:376`) defaults `coverage` to `1.0` when a
  `c_*` column is absent (`c_val = ... else (1.0 if p_val is not None else 0.0)`), the same
  "answer for missing data" pattern as F-03; also `SensitivityReport.pp` is annotated `int`
  (`:207`) while carrying fractional values.
- **Nit** — `coverage.py:273-275` `if hours >= 0: samples.append(hours)` silently drops
  negative latencies from the latency distribution instead of flagging them as bad data.
- **Nit** — the black-hole definition is duplicated: `coverage.py:109-110` hardcodes
  `BLACK_HOLE_SCORE = 40.0` / `BLACK_HOLE_DENOMINATOR = 10` alongside the METHODOLOGY D-GATE-5
  text rather than deriving from one source.
- **Nit** — `anchors.py:74` `MIN_COVERAGE = 0.90` is checked against a non-NULL proxy in the
  v1 `_coverage` path (the module admits it), so the v1 coverage anchor is a completeness
  check wearing a coverage label.

**Slice summary**

- **The exit-code contract is missing, not broken.** Both CLI auditors were read as if a FAIL
  would fail a pipeline; only `forward.py` even returns 1, and no test pins it. Nothing in CI,
  `canary.sh`, or `run_all.py` runs any audit CLI at all (F-02), so the audit suite is an
  evidence generator with no consumer — this is the finding that downgrades every other green
  check in this slice from "verified" to "printed".
- **Same relationship, three implementations, divergent semantics.** The newcomer anchor
  (F-01) and the archived/awesome anchors (F-06) each exist in v1, v2-snapshot, and v2-DB
  variants that map the *same* input state to PASS, SKIP, or FAIL depending on entry point.
  Anchor reports are therefore not comparable across runs unless you also pin which variant
  produced them.
- **Defaults answer for missing data.** `measured` absent → `False` (F-07), outcome NULL →
  0.0 (F-03), coverage column absent → 1.0 (F-08 tail nit). Each is individually defensible;
  together they mean "no data" and "benign data" produce identical reports, which is the
  false-clean mechanism this slice is about.
- **The tests pin the broken design.** `test_anchors.py:52` requires the newcomer anchor to
  FAIL and `:155-158` requires the boilerplate string — so fixing F-01 turns the suite red
  until expectations are rewritten, and the suite currently cannot tell a vacuous anchor from
  a working one.
- **Frozen contract, unguarded implementation.** PREREG is treated as immutable and is
  genuinely honored where it names constants (`test_forward.py:272` pins the window), but the
  two PREREG obligations expressed as *shapes* — cap disclosure (F-04) and the rated-universe
  definition (F-05) — have no corresponding test, and that is exactly where they were broken.
- **The numeric core is sound.** Ties handling, forward rho/AUC, tau-b with tie correction,
  and the DCO-inversion anchor were checked against their inputs and hold; every blocker here
  is about what the tooling can and cannot mechanically notice, not about the arithmetic.

**Coverage**

- **Read fully**: every file under `cvi/audit/` (`anchors.py`, `sensitivity.py`, `forward.py`,
  `coverage.py`, `detectors_report.py`, `methodology_history.py`, `provenance.py`, `diff.py`),
  `docs/v2/validation/PREREG.md`, `scripts/canary.sh`, and the load-bearing regions of
  `cvi/collect/universe.py`, `scripts/build_universe.py`, `tests/audit/test_anchors.py`,
  `tests/audit/test_sensitivity.py`, `tests/audit/test_forward.py`.
- **Spot-checked against source**: `cvi/core/constants.py:163-168` (the four legal-IP scores
  are distinct, so the DCO anchor's inversion is well-defined), `cvi/core/tiebreak.py:259-268,
  277-328`, `cvi/core/aggregate.py:240-244` + `cvi/core/engine.py:368-369` (round-to-2dp is
  idempotent, so the recomputed-composite comparisons are not float-noise),
  `cvi/store/queries.py:345-352` (the `rated` column's documented meaning), and the real
  envelope field names under `data/raw/` against the fields `forward.py:271-311` reads.
- **Executed**: `ls data/raw/` (confirms no `star_history/` directory, F-08); grep sweeps of
  `scripts/` and `.github/` for `cvi.audit` invocations (F-02). No test runs were executed —
  this slice is static review and no source was modified.
- **COULD NOT VERIFY**: the v1 engine's composite storage formula (relied on only as
  consistent with the E-slice round-2dp finding); `_check_dco_db_v2`'s params-table parsing was
  exercised on fixtures only; whether v2 `rated` ever differs from `NOT
  is_stage0_disqualified` on the real DB (F-05 quantification); and whether NULL scores
  actually occur in the shipped snapshot DB's rated rows (F-03's practical blast radius).
  None of these would change a verdict; F-03/F-05's severity would rise if they do occur.
<!-- END F-AUDIT -->

## Slice G — serve, API, ops

<!-- BEGIN G-SERVE -->
Auditor G reviewed the serve layer — `cvi/serve/{app,contract,guard}.py`, the legacy
`cvi/api.py`, the read path they drive (`cvi/store/queries.py:255-1032`), `scripts/serve.py`,
`scripts/serve_ui_fixtures.py`, `tests/serve/**`, `tests/test_serve.py`,
`tests/test_ui_node_suite.py`, `.github/workflows/ci.yml` and `pyproject.toml` — cross-checked
against the client call sites in `web/v2/**` (owned by auditor H) and `docs/v2/06-UX-DESIGN.md`.
What was actually executed: `pytest tests/serve tests/test_serve.py -p no:cacheprovider -q` →
**83 passed** (2 deprecation warnings); `.venv/bin/ruff check cvi scripts tests` → `All checks
passed!`; a `run_guard` probe against a **copy** of `data/cvi.db`, and a `TestClient` script
driving real `create_app` calls over a synthetic v2 snapshot, plus timings at 10,000 rows. No
source file was modified and nothing under `data/` was written to. Note that the serve baseline
was already green, so nothing here is a fresh regression: three of these blockers are shipped
contract/behaviour defects the green suite does not look at. Finding IDs are `G1`–`G13` by
severity, with a nit tail.

### G1 — Blocker — `/reproject` silently pages 20 rows while the UI assumes the whole set

- **Where**: `cvi/serve/app.py:660-679`; `cvi/serve/contract.py:431`, `:580-588`;
  `web/v2/api.js:153-166`; `web/v2/views/configure.js:225`, `:232-239`;
  `scripts/serve_ui_fixtures.py:710-730`
- **Evidence**: the handler takes paging as **query** params — `page: int = Query(1)`,
  `page_size: int = Query(DEFAULT_PAGE_SIZE)` (`app.py:664-665`) with `DEFAULT_PAGE_SIZE = 20`
  (`contract.py:431`, imported at `app.py:72`) — and `queries.reproject` returns
  `(rows, total)` from a full `COUNT(*)` (`cvi/store/queries.py:986`), so the server *knows* it
  truncated. `ReprojectResponse` (`contract.py:580-588`) carries only `rows`, `total` and
  `lens: "custom"` — no `page`, no `page_size`. Measured live: a valid 5-weight `POST
  /v1/cvi/reproject` with no query string returns `200` with `len(rows) == 20`, `total == 285`,
  and body keys exactly `['lens', 'rows', 'total']`; the same call with `?page_size=1000`
  returns all 285.
- **Why it's wrong**: the client never sends a paging param (`api.js:153-166` builds the URL
  with no query string), and `configure.js:225` computes
  `totalPages = Math.ceil(rows.length / state.configurePageSize)` — rows it already holds. The
  comment at `configure.js:232-239` states the contract outright: "`/reproject` returns the WHOLE
  eligible set in one call (no server-side paging fields at all)". Neither `configure.js` nor
  `state.js` reads `.total` from the reproject payload. The fixture server
  (`serve_ui_fixtures.py:710-730`) ignores paging and returns everything, so the fixture agrees
  with the client's belief and the production server does not. The two tests that exercise
  reproject (`tests/serve/test_api.py:542`, `:1137`) both pass `?page_size=1000`, which hides the
  default.
- **Impact**: in production, Screen D's reproject table and its top-movers show 20 of 285
  eligible repositories while the paginator offers 15 pages whose clicks only slice 20 rows. The
  defect is invisible under the fixture server and invisible to the test suite.
- **Fix**: either drop paging from this endpoint (return the whole set, as the fixture and the
  client already assume), or echo `page`/`page_size` in `ReprojectResponse` and have the client
  page against the server; make the fixture mirror whichever is chosen and stop passing
  `page_size=1000` in the tests.
- **Confidence**: high — measured on a live `TestClient`, and the client comment states the
  intended contract explicitly.

### G2 — Blocker — `/matrix` hardcodes `axis_coverage={}` and nothing in the repo produces it

- **Where**: `cvi/serve/app.py:628`; `cvi/serve/contract.py:501-518`;
  `cvi/store/queries.py:774-786`; `web/v2/views/matrix.js:168-182`; `web/v2/copy.js:434-440`;
  `scripts/serve_ui_fixtures.py:146`, `:174-203`
- **Evidence**: the handler returns
  `MatrixResponse(points=points, excluded_count=excluded, axis_coverage={})` — a literal empty
  dict (`app.py:628`). The client does its half: `matrix.js:168-182` loops
  `Object.entries(matrix.axis_coverage || {})` and `copy.js:434-440` renders "`<axis>` computed
  without `<pillar>` for N% of plotted repositories." The store cannot fill the shape even if the
  literal were removed: `MatrixAxisCoverage` (`contract.py:501-518`) is keyed **per axis** and
  needs `{pillar, axis_partial_count, axis_partial_share}`, whereas `matrix_points` returns a
  single **flat** `axis_partial_count` / `axis_partial_share` pair over
  `friction_coverage < 1 OR leverage_coverage < 1` with no pillar attribution
  (`queries.py:774-786`). The per-pillar computation exists only in the fixture
  (`serve_ui_fixtures.py:174-203`, threshold `AXIS_CAPTION_MIN_MISSING_FRACTION = 0.05` at `:146`),
  derived privately from `LEVERAGE_AXIS_WEIGHTS`/`FRICTION_AXIS_WEIGHTS`.
- **Why it's wrong**: a declared wire field with a renderer and no producer anywhere — the
  "one definition per fact" rule in `docs/REVIEW_GUIDELINES.md` §4 is broken by having the fixture
  own a private implementation of a number the API is supposed to publish, and the zero-reader
  rule makes the shipped field a Blocker. Checked against the committed fixture data
  (`tests/fixtures/ui/leaderboard.json`, 73 rows): the fixture's own `axis_coverage()` returns
  `{}` too, so the caption does not render even in the demo. `tests/fixtures/ui/matrix.json` does
  not exist, and `grep -rn "axis_coverage" web/v2/tests/` returns no matches — no test can fail
  on this.
- **Impact**: the axis-coverage caption is dead code in production *and* in the fixture; users
  are shown matrix positions computed with missing pillars and never told which pillar was
  missing or how often.
- **Fix**: compute per-axis, per-pillar partial counts in `queries.matrix_points`, forward them
  through `MatrixResponse`, delete the fixture's private copy, and assert the rendered caption in
  a `web/v2` test.
- **Confidence**: high — the `{}` literal is exhaustive and the shape mismatch is structural.

### G4 — Blocker — `/not-ranked` truncates at 1000 and recomputes its counts from the truncated page

- **Where**: `cvi/serve/app.py:592-611`; `cvi/store/queries.py:142`, `:589`, `:628`;
  `docs/v2/06-UX-DESIGN.md` U-13 `:184-196`
- **Evidence**: `limit=queries.MAX_LIMIT` (`MAX_LIMIT = 1000`, `queries.py:142`) with `offset=0`
  and no paging parameters (`app.py:601-602`); the comment at `:600` reads "fetch up to MAX_LIMIT
  in one page." The store returns its own authoritative `_counts_by_reason` and `_total`, which
  the handler discards and re-derives as `len(gated)` / `len(unrated)` / `len(persona_hidden)`
  plus `summary["persona_visible_counts"][persona]` (`app.py:604-610`). `list_not_ranked` filters
  the whole population and only then slices (`queries.py:589`, `:628`).
- **Why it's wrong**: `docs/v2/06-UX-DESIGN.md` U-13 `:184-196` specifies `limit`/`offset`
  paging for this endpoint and none is exposed, and `docs/v2/06-UX-DESIGN.md` §8 item 2 requires
  the not-ranked counts to agree with `/snapshot`. Constructed repro (a snapshot with 285 rated
  rows): `/snapshot` reports `{'rated': 285, 'gated': 7, 'unrated': 19, 'total': 311}` and
  `/not-ranked` agrees at the real `MAX_LIMIT`; patching `queries.MAX_LIMIT = 5` makes
  `/not-ranked` report `{'rated': 285, 'gated': 5, 'unrated': 0, 'total': 290}` while `/snapshot`
  still says 311 — the two endpoints disagree about the same snapshot, and the list simply stops.
- **Impact**: the mechanism is certain, the magnitude at today's universe size is not (see
  Coverage): both buckets are currently small, so the visible symptom appears only once either
  bucket crosses 1000 rows, at which point the page silently under-reports and the total stops
  matching the header. Counts are derived from a page rather than from the counts the store
  already computed.
- **Fix**: take `limit`/`offset` as the spec says, return `total` from the store's own `_total` /
  `_counts_by_reason` instead of re-deriving from the sliced page, and assert `/snapshot` ↔
  `/not-ranked` agreement with a case where a bucket exceeds the page limit.
- **Confidence**: high on the mechanism (constructed repro, code quoted); medium on present-day
  magnitude.

### G3 — Warning — three incompatible definitions of `excluded_count`

- **Where**: `cvi/store/queries.py:774`; `cvi/serve/contract.py:520-527`;
  `scripts/serve_ui_fixtures.py:665`; `cvi/serve/app.py:613-628`
- **Evidence**: the store computes it as *every* un-plotted row (`total - len(points)`,
  `queries.py:774`); the `MatrixResponse` docstring (`contract.py:520-527`) defines it as "count
  of **gated** repositories excluded from plotting"; the fixture sets it to
  `store.leaderboard["counts"]["gated"]` (`serve_ui_fixtures.py:665`).
- **Why it's wrong**: three sources of truth for one published number, none of which agrees.
  Measured in a single live call: the API returned `excluded_count = 27` while the store's own
  tuple reported `26` for the same snapshot — the extra row is the one in the app loop that fails
  `_matrix_point` (`app.py:626-627`) and is neither gated nor unrated. So the field means
  "everything not plotted" in the store, "gated" in the docs, "gated" in the fixture, and
  "not-plotted-minus-`_matrix_point`-failures" at the HTTP boundary.
- **Impact**: the matrix's excluded count is not reproducible from any documented rule, and a
  client that trusts the docstring gets a different number than the wire carries.
- **Fix**: pick one definition (the store's, since it is the only one that is closed under the
  plotted set), state it in the docstring, and have both the app and the fixture read it from the
  store.
- **Confidence**: high — the 27-vs-26 delta is observed.

### G5 — Warning — the guard verifies a snapshot the server may not end up serving

- **Where**: `cvi/serve/app.py:711`, `:156-175`; `cvi/serve/guard.py:195-204`;
  `scripts/serve.py:111-127`
- **Evidence**: `run_guard(db_path)` runs exactly once per process, at startup (`app.py:711`),
  and again in the entrypoint (`scripts/serve.py:116`) — which computes the whole-DB dump hash a
  second time. The snapshot the server actually serves is re-picked per request by
  `_current_snapshot_id` (`app.py:165-175`), whose query is
  `... WHERE promoted_at IS NOT NULL ORDER BY promoted_at DESC, id DESC LIMIT 1`
  (`guard.py:195-204` equivalent, duplicated logic in `app.py`). The handler's own comment block
  (`app.py:156-163`) states there is no `?snapshot_id=` and that a fallback to
  "most recently created" applies when nothing is promoted.
- **Why it's wrong**: the thing that was verified (a hash of the file at boot) and the thing that
  is served (whichever row is most recently promoted *right now*) are chosen by two independent
  implementations of the same rule. A snapshot promoted or re-finalised after boot is served
  unverified, and two requests inside one page load can resolve to different snapshot ids.
  `_current_snapshot_id` also swallows `OperationalError` into `None` (`app.py:160-175`), turning
  a corrupt database into a 503 rather than a refusal.
- **Impact**: the tamper guarantee is real but narrower than it reads — it covers the bytes at
  boot, not the served rows. Live risk, not theoretical: collection is running against this tree
  right now (`data/derived/*.json` mtimes are 21:03 today), so a promote during a served window
  is expected, not exotic.
- **Fix**: resolve the served id in one place (`guard.served_snapshot_id`), have the request path
  call it rather than re-deriving it, and re-verify (or refuse) when the id changes.
- **Confidence**: high on the duplication and the once-per-process guard; medium on how often the
  id actually moves in a served window.

### G6 — Warning — no `Cache-Control` or `ETag` on snapshot-immutable, content-hash-pinned data

- **Where**: `cvi/serve/app.py` (all v2 handlers); measured timings below
- **Evidence**: grep for `ETag|Cache-Control|Last-Modified` across `cvi/`, `scripts/` and
  `web/v2/` returns `ETag` only in `cvi/collect/ratelimit.py` — the *outbound* GitHub client, not
  this server. Every v2 response is a plain `JSONResponse` with no validator or freshness header.
  Cost per request, measured at `n_rated = 10_000`: `queries.snapshot_summary` median **27.40 ms**
  (min 26.90 / max 29.00), `queries.list_ranked(..., limit=20)` median **6.57 ms** (6.42 / 7.27),
  and the full `GET /v1/cvi/leaderboard?page_size=20` median **37.58 ms** (36.02 / 43.02).
  `snapshot_summary` is computed even where it is thrown away — `/not-ranked` calls it at
  `app.py:605` only to use one field.
- **Why it's wrong**: the payload for a given `(snapshot_id, persona, sort, page, q)` is
  byte-identical until the next promote, and the snapshot id is itself a content hash, so these
  responses are the ideal caching case and are served with the worst possible header set —
  clients revalidate nothing and re-download everything, and the server re-runs a 9-query scan
  (see the nit about `SELECT * FROM snapshots` twice) to answer it.
- **Impact**: the discarded `snapshot_summary` is roughly **73 %** of a leaderboard request and
  about **4×** the cost of the page query it accompanies, paid again on every page turn, on data
  that cannot change under a fixed id.
- **Fix**: `Cache-Control: public, max-age=…` plus an `ETag`/`Last-Modified` derived from the
  snapshot hash, and stop computing a full summary where only one field is needed.
- **Confidence**: high — header absence is exhaustive by grep; timings measured.

### G7 — Warning — a production 500 leaves no trace at all

- **Where**: `cvi/serve/app.py:147-153`, `:113-117`; the file has no `logging` import
- **Evidence**: the catch-all `@app.exception_handler(Exception)` is
  `return _internal_error_response(request)` — and `_internal_error_response` (`:113-117`)
  returns a fixed `"internal server error"` body. There is no logger anywhere in
  `cvi/serve/app.py` (grep for `logging` returns nothing).
- **Why it's wrong**: the non-leaking 500 is correct and deliberate, but discarding the exception
  object without logging it means the traceback is dropped on the floor. The observability lens in
  `docs/REVIEW_GUIDELINES.md` §4: a 500 in production is undiagnosable after the fact, and the
  code's own comment (`:149-152`) says this handler exists for faults "this module did not
  anticipate" — i.e. precisely the ones nobody has a theory about.
- **Impact**: every unexpected failure (including G10's `ValidationError` path and G2/G3's shape
  mismatches when they do fire) is invisible to an operator.
- **Fix**: `logging.exception(...)` inside the handler before returning the canned body; the body
  stays non-leaking, the log carries the traceback.
- **Confidence**: high.

### G8 — Warning — the fixture's tier vocabulary is not the engine's

- **Where**: `scripts/serve_ui_fixtures.py:130`, `:219-221`; `cvi/core/constants.py:137`;
  `tests/fixtures/ui/leaderboard.json`, `tests/fixtures/ui/leaderboard-first_timer.json`
- **Evidence**: the fixture defines `TIER_ORDER = {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4,
  "F": 5}` (`:130`) while the engine's real ordering is
  `TIER_ORDER: tuple[str, ...] = ("D", "C", "B", "A", "S", "S+")` (`cvi/core/constants.py:137`).
  The same file re-transcribes the real tiers again at `:219-221`, including
  `FULL_TIER_ORDER`, so two vocabularies coexist in one script. The sort key is
  `.get(tier or "F", 5)`.
- **Why it's wrong**: a real `S+` row hits the `.get` default and sorts **last**, behind `F`; and
  `F` is not a tier the engine emits at all — both committed fixture files contain exactly
  `['A', 'B', 'C', 'D', 'F', 'S']`, so the fixtures carry a fabricated band.
  `docs/REVIEW_GUIDELINES.md` §3 forbids fabricated values in fixtures, and the transcription at
  `:219-221` is a second definition of a fact `constants.py` owns.
- **Impact**: the demo server can rank the top band to the bottom of the leaderboard, and the
  fixture data teaches the frontend a tier set that production never sends — which is one of the
  reasons auditor H's `S+` findings (H3) survived.
- **Fix**: import `TIER_ORDER`/`FULL_TIER_ORDER` from `cvi.core.constants`, drop `F`, and
  regenerate both committed fixture files.
- **Confidence**: high — both orderings quoted, fixture contents enumerated.

### G9 — Warning — the fixture enforces `LEADERBOARD_PAGE_SIZES` and production does not

- **Where**: `cvi/serve/contract.py:429-431`, `:445-455`; `scripts/serve_ui_fixtures.py:129`, `:621`
- **Evidence**: `LEADERBOARD_PAGE_SIZES = (20, 50, 100, 500, 1000)` and `DEFAULT_PAGE_SIZE = 20`
  (`contract.py:429-431`); the fixture imports the tuple (`:129`) and rejects anything outside it
  with a 400 (`:621`). Production's only gate is `LeaderboardResponse._paging_is_sane`
  (`contract.py:445-455`), which accepts any `1..max(LEADERBOARD_PAGE_SIZES)`, i.e. any page size
  up to 1000.
- **Why it's wrong**: the declared API constraint is enforced only by the test/demo server, so
  production accepts `?page_size=33` where the fixture 400s it. The fixture is stricter than the
  thing it is standing in for, which inverts the purpose of a stand-in: the demo becomes the
  faithful one and production the divergent one.
- **Impact**: moderate — a client built against the fixture can encode an assumption production
  does not share, and a non-menu page size silently bypasses whatever the tuple was meant to
  guarantee (a bounded set of cacheable page shapes).
- **Fix**: validate `page_size ∈ LEADERBOARD_PAGE_SIZES` in the real request path (422 on
  violation) and keep the fixture's check as a mirror, not a substitute.
- **Confidence**: high.

### G11 — Warning — the working tree as shipped cannot be served at all

- **Where**: `scripts/serve.py:71`, `:94`, `:111-116`; `data/cvi.db`
- **Evidence**: `python scripts/serve.py` with no arguments uses
  `_DEFAULT_DB = <repo>/data/cvi.db` (`:71`, wired to the CLI default at `:94`) and calls
  `run_guard(args.db)` at `:116`. Executed against a copy of that file, it exits:
  `SystemExit: CVI serve refused for '/tmp/probe.db': 1000 of 1000 rows in 'repositories' carry
  the tests/fixtures/synthetic_catalog.py fixture clock (last_indexed_at=2026-09-11T12:00:00):
  row 1 vllm-project/vllm is fabricated telemetry, which is a test fixture and never a ranking
  source`. Separately, `data/cvi.db` contains only the tables `['repositories',
  'sqlite_sequence']` over 1000 rows with **v1** columns (27 of them:
  `score_responsiveness`, `leverage_score`, `matrix_quadrant`, `overall_cvi`, `rank_tier`,
  `is_stage0_disqualified`, `deep_dive_json`, …) — no `rated`, no `p_*`, no
  `friction_coverage`/`leverage_coverage`, and no `snapshots` or `snapshot_meta` table.
- **Why it's wrong**: the guard refusing is the guard *working correctly*, and that is the
  finding — nothing has ever promoted a v2 snapshot into this database, so the only DB the
  entrypoint points at by default is refused by design. Had the guard let it through,
  `_current_snapshot_id` would resolve to `None` (no `snapshots` table) and every v2 endpoint
  would answer 503. `data/` is gitignored, so a fresh clone or CI checkout has no database at
  all, and no step in `.github/workflows/ci.yml` builds a servable one.
- **Impact**: "run the app" does not work out of the box for anyone, including a reviewer on this
  very checkout; the only working demo path is the fixture server, which is a different
  implementation (see G1, G2, G3, G8, G9).
- **Fix**: a documented bootstrap that promotes a v2 snapshot (or a committed demo DB built by
  `scripts/build_fixture_snapshot.py`), and a CI step that asserts `create_app` on the default
  path yields 200 rather than `SystemExit`.
- **Confidence**: high — refusal reproduced verbatim on a copy.

### G12 — Warning — CI runs only when someone remembers to run it

- **Where**: `.github/workflows/ci.yml` (`on:` block; job steps)
- **Evidence**: the workflow trigger is `on: workflow_dispatch:` — no `push:`, no
  `pull_request:`, so nothing gates a commit or a PR. `actions/checkout@v4` and
  `actions/setup-python@v5` are floated by major tag rather than pinned to a SHA, and
  `pip install build` is unpinned. The test tools are pinned only inline in a `pip install`
  argument list (pytest 9.1.1, xdist 3.8.0, fastapi 0.141.1, httpx 0.28.1, ruff 0.16.7), so the
  `dev` extra declared in `pyproject.toml` (`pytest>=9`) is never what actually runs. No coverage
  floor, no dependency audit.
- **Why it's wrong**: with a dispatch-only trigger, "CI is green" is a statement about a manual
  action, not about the commit. Floating action tags are an unpinned supply-chain input to every
  job, and pinning in the workflow while the manifest declares a looser range means the declared
  dependency set is the one that is never exercised.
- **Impact**: any of G1/G2/G4/G8 could have arrived with no CI signal whatsoever. This is the same
  root cause auditor H reaches from the frontend side (H4).
- **Fix**: add `pull_request` (and `push` to `main`), pin actions to commit SHAs, install the
  `dev` extra from the manifest so the declared range is the tested range.
- **Confidence**: high on the facts; I could not verify whether dispatch is diligently triggered
  by convention here.

### G13 — Warning — the canonical suite can pass while the entire frontend test suite never runs

- **Where**: `tests/test_ui_node_suite.py:20`
- **Evidence**: `@pytest.mark.skipif(shutil.which("node") is None, ...)`, with no root-cause note
  or backlog reference on the line.
- **Why it's wrong**: `docs/REVIEW_GUIDELINES.md` §5 treats a skipped or weakened assertion
  without a root-cause note as an automatic reject. On a host without `node` — which is this
  host, and any minimal CI image — the canonical Python suite is green while every JS test under
  `web/v2/tests/` is silently not executed. `.github/workflows/ci.yml` does run `node --test`
  directly, so this is specifically the local and canonical-invocation path that goes quiet, and
  combined with G12's dispatch-only trigger neither path is guaranteed.
- **Impact**: `pytest tests/ -n auto` reporting all-green is compatible with zero frontend
  coverage. (Auditor H found the same shape from the JS side as H4; recorded here because the
  skip is a Python-side file in my slice.)
- **Fix**: fail when `node` is absent unless an explicit `--allow-missing-node` flag is passed, or
  record the skip reason plus a backlog id on the decorator line.
- **Confidence**: high.

## Nits

- `cvi/store/queries.py:668-671` — `snapshot_summary` runs `SELECT * FROM snapshots WHERE id = ?`
  twice per call, back to back. One fetch, and `SELECT` only the columns used.
- `cvi/store/queries.py:674-703` — the same call issues three population COUNTs (total / gated /
  rated), then one more `SELECT COUNT(*) FROM repositories r WHERE r.snapshot_id = ? AND <clause>`
  per persona via `_count_persona_visible` (`:689-692`; `balanced` short-circuits, so four extra
  COUNTs on the default persona), then a `json_each(r.suspicion)` `GROUP BY` scan (`:694-703`).
  Nine-plus queries to answer a question that is fixed for a content-hashed snapshot; see G6.
- `cvi/api.py` — the legacy v1 app (353 lines) registers **no** exception handlers, coerces
  possibly-NULL columns with `int(...)`/`float(row[...])`, and `create_app(store=None)` defaults
  to a fresh in-memory `OperationalStore` that has never passed through `run_guard`, so the
  `/legacy` surface is served without the tamper guarantee the v2 surface documents.
- `cvi/serve/app.py:562` — the v2 router's prefix is still `APIRouter(prefix="/v1/cvi")` while the
  legacy v1 app is mounted at `/legacy`. Two generations of the same version string mean the URL
  cannot tell a caller which contract they are talking to, and every client has to know that
  `/v1/cvi` is v2.
- `pyproject.toml` — no `dependencies` key at all (only the `server` and `dev` extras), so
  `pip install cvi` yields a package that cannot serve; `requires-python = ">=3.10"` while ruff is
  configured `target-version = "py312"`, so ruff will accept syntax the declared floor rejects; no
  `[project.scripts]` entry, which is why serving requires knowing the path to
  `scripts/serve.py`.
- **FYI** — the report header states that `docs/bedrock/*` and `docs/standards/*` are in force.
  Neither directory exists in this checkout, so no rule was cited from either.

## What the serve layer genuinely does well

- **Paging input is validated, not crashed on** — `_validate_paging` (`cvi/store/queries.py:280-290`)
  raises `ValueError`, which `app.py:145` maps to a 422 with the standard error body. Bad `page`
  and `page_size` are a clean client error, not a 500.
- **Search is not a `LIKE` injection** — `queries.py:307` matches with
  `instr(lower(...), lower(?))`, so `%` and `_` in a user query are literals.
- **500 responses do not leak** — `_internal_error_response` (`app.py:113-117`) returns a fixed
  detail, deliberately never the exception text, in the same `{"error": ...}` shape as 404/422/503.
- **Server faults are not relabelled as client faults** — `app.py:143-144` routes
  `PydanticValidationError` to **500**, with a comment (`:136-142`) explaining why a stored value
  the contract cannot represent must not become a 422. That is the opposite of the common default
  and it is correct.
- **SPA fallback is contained** — `_API_RESERVED_PREFIXES` / `_API_RESERVED_EXACT`
  (`app.py:525-528`) keep `/v1/*` and `/legacy/*` out of the index fallback so API misses are real
  404/405s (`app.py:542-546`), and file resolution is delegated to Starlette's maintained
  `StaticFiles` (`app.py:737`) rather than a hand-rolled path join.
- **The tamper guard works and is tested** — `tests/serve/test_guard.py` and
  `tests/test_serve.py` (including `test_fixture_database_is_refused_naming_the_row:218`,
  `test_database_without_snapshot_meta_is_refused:264`,
  `test_promoted_database_with_snapshot_identity_serves:279`,
  `test_seed_then_promote_serves_only_the_real_rows:434`) pass, and I reproduced a real refusal on
  a real file. `test_database_without_snapshot_meta_is_refused:264` is exactly the G11 condition,
  tested — the gap is that nobody has run the promote step, not that the behaviour is unknown.
- **CI's `package` job is the strongest step in the workflow** — sdist build, migrations applied
  against it, and a non-editable install smoke. That is the right thing to gate; it is only
  unfortunately gated on a manual trigger (G12).

## Slice summary

- **The fixture server is a second implementation of the API, and the suite tests both while
  checking that they agree on nothing.** G1, G2, G3, G8 and G9 are all "the fixture says X, the
  server says Y" — and in every case the fixture, not the server, matches what `web/v2` expects.
  Because the fixture is what the demo and the frontend tests run against, the divergence is
  systematically invisible from the user's side.
- **Published fields without producers, and definitions without owners.** `axis_coverage` (G2)
  ships as a literal `{}`; `excluded_count` (G3) has three meanings; tier order (G8) is
  re-transcribed. `docs/REVIEW_GUIDELINES.md` §4 "one definition per fact" is the rule these
  break, and the fix is always to import from or delegate to `cvi/core/constants.py` and
  `cvi/store/queries.py`.
- **Counts are re-derived from pages instead of read from the store** (G4, and again in G1's
  `total`), so a truncation turns into a wrong total rather than a visible page boundary.
- **Per-request cost is paid on immutable data**: no caching headers at all (G6) over a nine-query
  summary that is frequently discarded. The cheapest fix is headers, not query tuning.
- **Correctness is concentrated in the guard and the error mapping** — those two areas are
  genuinely careful and well tested — while **operational concerns are thin**: no logging in the
  request path (G7), a dispatch-only CI (G12), and a JS suite that skips itself when `node` is
  missing (G13).
- **Nothing in this slice is currently servable from the default path** (G11): the guard correctly
  refuses the only database the entrypoint defaults to, so the shipped tree cannot be exercised
  end-to-end without the fixture server.

## Coverage

- **Read fully**: `cvi/serve/app.py`, `cvi/serve/contract.py`, `cvi/serve/guard.py`,
  `cvi/store/queries.py:255-1032`, `cvi/api.py`, `scripts/serve.py`,
  `scripts/serve_ui_fixtures.py`, `tests/serve/helpers.py`, `tests/serve/test_guard.py`,
  `tests/test_ui_node_suite.py`, `docs/v2/06-UX-DESIGN.md`, `docs/REVIEW_GUIDELINES.md`,
  `.github/workflows/ci.yml`, `cvi/core/constants.py:130-143`.
- **Cross-checked** (call sites and declarations, not read line by line): `web/v2/api.js`,
  `web/v2/app.js`, `web/v2/copy.js`, `web/v2/views/matrix.js`, `web/v2/views/configure.js`
  (auditor H owns these files), `pyproject.toml`, `tests/fixtures/ui/*.json`,
  `tests/serve/test_api.py` (`:700-810` read in full; the rest grepped at the cited lines).
- **Executed**:
  1. `timeout 280 env -u PYTEST_ADDOPTS .venv/bin/python -m pytest tests/serve tests/test_serve.py
     -p no:cacheprovider -q` → **83 passed**, 2 deprecation warnings. This is *not* §1's canonical
     invocation (`pytest tests/ -n auto -p no:cacheprovider`); the slice suite was run, not the full
     suite.
  2. `.venv/bin/ruff check cvi scripts tests` → `All checks passed!`, exit 0.
  3. `run_guard` on a copy at `/tmp/probe.db` → `SystemExit`, message quoted in G11. The real
     `data/cvi.db` was never passed to `create_app`/`run_guard`, since both can write.
  4. Schema probe: `data/cvi.db` tables `['repositories', 'sqlite_sequence']`, 1000 rows, 27
     v1-shaped columns, no `rated` / `p_*` / `*_coverage` / `snapshots` / `snapshot_meta`.
  5. `TestClient` over `create_app` with `build_snapshot(conn, db, n_rated=285, seed=20260913)`
     (and the required `finalize()`) → the G1 paging measurement, the G2 empty
     `axis_coverage`, the G3 27-vs-26 delta, the G4 `MAX_LIMIT = 5` monkeypatch disagreement.
  6. Timings at `n_rated = 10_000`, `seed = 7` (median / min / max, ms):
     `snapshot_summary` 27.40 / 26.90 / 29.00; `list_ranked(limit=20)` 6.57 / 6.42 / 7.27;
     `GET /v1/cvi/leaderboard?page_size=20` 37.58 / 36.02 / 43.02.
  7. `serve_ui_fixtures.py`'s `axis_coverage()` over the committed
     `tests/fixtures/ui/leaderboard.json` (73 rows) → `{}`;
     `tests/fixtures/ui/matrix.json` does not exist;
     `grep -rn "axis_coverage" web/v2/tests/` → no matches.
  8. `PERSONA_PRESETS` → 5 personas (`ai_assisted`, `balanced`, `career_capital`, `first_timer`,
     `senior_systems`), and the per-call query inventory in the first two nits.
  `tests/test_serve.py` was **run and its test names enumerated**, not read line by line; it is
  the guard / serve-entrypoint suite and all four tests named above are cited from its collected
  list.
- **COULD NOT VERIFY**: whether any real promoted evidence row actually violates `EvidenceRow`
  (G10, left at Consider for exactly this reason — `queries.repo_evidence:789-849` can set
  `pillar = defn.pillar if defn else None` against a required `Literal` at `contract.py:340`, and
  `value = json.loads(raw_value)` can yield a list or dict where the model allows
  `float | str | int | None`, either of which would 500 the whole drawer; I never had a real
  snapshot to try it against); whether either not-ranked bucket currently exceeds 1000 rows at
  the real universe size (G4's magnitude, not its mechanism); whether `workflow_dispatch` CI is
  diligently triggered by convention in this repo (G12); the end-to-end behaviour of the shipped
  tree, since G11 blocks it; and any JS runtime behaviour, as there is no `node` on this host.
- **Never read**: `data/promoted.json`, `cvi/store/reader.py`, `cvi/lakehouse.py`,
  `cvi/store/queries.py:1-255`, `docs/ARCHITECTURE.md`.
<!-- END G-SERVE -->

## Slice H — frontend

<!-- BEGIN H-WEB -->
Auditor H reviewed `web/v2/**` and `web/index.html` by read-only source inspection. **Nothing
was executed**: there is no `node` binary on this host (`node: command not found`), so
`web/v2/tests/*.mjs` could not be run and no `pytest` run was performed. Statements of the form
"would fail if run" are inferences from code, not observed failures. Finding IDs are
`H1`–`H7` by severity, with a nit tail.

### H1 — Blocker — the drawer's retry button renders the literal word `undefined`

- **Where**: `web/v2/views/drawer.js:351`; `web/v2/copy.js:340`, `:482`
- **Evidence**: the button renders `${escapeHtml(copy.EVIDENCE_RETRY_LABEL)}`, but
  `EVIDENCE_RETRY_LABEL` is never exported — a repo-wide grep for
  `EVIDENCE_RETRY_LABEL|RETRY_LABEL` returns six hits and the real exports are
  `RETRY_LABEL = "Retry"` (`copy.js:340`) and `CONFIGURE_RETRY_LABEL = "Retry"` (`copy.js:482`),
  correctly consumed at `app.js:216`, `drawer.js:177`, `configure.js:285`.
- **Why it's wrong**: the module is imported as `import * as copy`, so a typo is not a load-time
  error. `copy.EVIDENCE_RETRY_LABEL` is `undefined`, `escapeHtml(undefined)` yields
  `"undefined"`, and the button's visible accessible name becomes the word "undefined".
- **Impact**: user-facing on the most-trafficked error path — a 500/timeout/network failure while
  opening a repo drawer.
- **Fix**: use `copy.RETRY_LABEL`, and assert the label in the test (see H2).
- **Confidence**: high — grep is exhaustive over a closed module set.

### H2 — Blocker — the integrity tests structurally cannot see a bad `copy.*` name

- **Where**: `tests/test_ui_js_integrity.py:153-156`; `tests/test_ui_copy_usage.py`;
  `web/v2/tests/unit.test.mjs:613-619`
- **Evidence**: the import/export integrity test resolves each specifier and checks *named*
  imports against the target's exports, but for a namespace import it stops at
  `if is_namespace: assert target_exports, (...)`. `test_ui_copy_usage.py` checks only the
  reverse direction — every *exported* copy key is referenced somewhere.
- **Why it's wrong**: every view does `import * as copy`, so **all ~600 `copy.*` references
  across 14 files are unchecked**; the usage test catches dead keys, not phantom ones. The two
  tests that look like a copy-name safety net are orthogonal to the actual failure mode. The
  drawer test at `unit.test.mjs:613-619` does exercise the branch (drawer is genuinely imported
  at `unit.test.mjs:33`) but asserts only `html.includes("data-repo-detail-retry")` and
  `!html.includes(copy.DRAWER_NOT_FOUND_TEXT)` — never the label, so the gap is **assertion
  depth, not coverage**.
- **Impact**: this is why H1 and H3 reached `main`. Any future `copy.<typo>` is invisible at
  build time.
- **Fix**: validate `copy.<name>` property reads and destructured names in view modules against
  the parsed export set of `copy.js`; add a label assertion to the retry test.
- **Confidence**: high.

### H3 — Blocker — tier `S+` exists in the engine and the UI silently renames it

- **Where**: `cvi/core/constants.py:127-134`, `:137`; `web/v2/copy.js:108`, `:109`;
  `web/v2/components/badge.js:28`; `web/v2/tests/unit.test.mjs:255-260`
- **Evidence**: `TIER_CUTOFFS` begins at `(90.0, "S+")` and
  `TIER_ORDER = ("D", "C", "B", "A", "S", "S+")`, while
  `const normalizedTier = tier === "S+" ? "S" : tier;` collapses it.
- **Why it's wrong**: three documents specify the opposite — `docs/UI_UX_SPECIFICATION.md:334`,
  `:349-350` (an `[S+]` badge, "Tier S+: Glowing emerald border, top 1% composite CVI"),
  `docs/ARCHITECTURE.md:525`, `:650`, and `docs/BENCHMARK_SPECIFICATION.md:38`. The label never
  reaches the DOM. `unit.test.mjs:255-260` tests `tierLabel` for capped/uncapped/null but never
  passes `"S+"` — the same assertion-depth gap as H1 in a different module.
- **Impact**: the top 1% of the benchmark is displayed as plain `S`, so the highest tier in the
  methodology is invisible to a reader of the UI, and the UI contradicts its own published spec.
- **Fix**: resolve in one direction (drop the collapse or amend all three specs) and add an
  `"S+"` case to the test. `badge.js:28` also recomputes the "is this the ceiling tier" decision
  that `copy.tierLabel` computes internally, so the two can drift independently.
- **Confidence**: high on the code; the docs quotes are verbatim.

### H4 — Blocker — the JS test suite can pass by never having run

- **Where**: `tests/test_ui_node_suite.py`; `.github/workflows/ci.yml`
- **Evidence**: the Node suite is **skip-if-missing** rather than fail-if-missing, and
  `ci.yml` is `workflow_dispatch`-only — not triggered by pushes or PRs. No `node` binary exists
  on this host.
- **Why it's wrong**: on any machine without Node the Python suite reports green while the
  entire JS behavioural layer is untested, and CI does not run on change at all.
- **Impact**: the mechanism that lets H1 and H3 ship. There is no enforced gate between a
  frontend defect and `main`.
- **Fix**: make the Node suite non-skippable in CI and add `push`/`pull_request` triggers.
- **Confidence**: high on the workflow trigger; `COULD NOT VERIFY`: whether dispatch is run
  diligently by convention.

### H5 — Warning — `escapeHtml` is defined 13 times while an exported one already exists

- **Where**: `app.js:99`, `components/badge.js:6`, `components/keymap.js:58`,
  `components/primarynav.js:15`, `components/scatter.js:56`, `components/sparkbar.js:9`,
  `views/compare.js:12`, `views/configure.js:15`, `views/drawer.js:31`,
  `views/leaderboard.js:36`, `views/matrix.js:38`, `views/methodology.js:17`,
  `views/notranked.js:15`; exported at `components/table.js:63`
- **Why it's wrong**: one definition per fact (guidelines §4). One sanitizer being fixed — or one
  being subtly weaker — does not propagate, and escaping is precisely the code where that
  matters.
- **Fix**: import the `components/table.js:63` export everywhere.
- **Confidence**: high.

### H6 — Warning — below-floor coverage rows wear the B-band badge

- **Where**: `web/v2/components/badge.js:18`; `web/v2/styles/components.css:170`, `:174`, `:178`;
  `web/v2/copy.js:127`
- **Evidence**: band `"below"` maps to class `badge-coverage-b`, but the stylesheet defines only
  `.badge-coverage-full` / `-a` / `-b` — there is **no `.badge-coverage-below`** — while
  `copy.js:127` prints the text "Below floor".
- **Impact**: below-floor rows render in B-band colour with B-band meaning, so "below floor"
  reads as an ordinary B. A data-quality warning that is visually indistinguishable from a score
  band.
- **Fix**: add the missing rule (or map `below` to a distinct existing class).
- **Confidence**: high.

### H7 — Warning — cross-language constants are hand-mirrored with no parity test

- **Where**: `web/v2/state.js:21` vs `cvi/serve/contract.py:430`, `:449-450`;
  `web/v2/state.js:46-52` vs `cvi/core/constants.py:392-422`;
  `web/v2/state.js:199-209` vs `cvi/serve/app.py:482-502`; `web/v2/copy.js:56-84`
- **Evidence**: `PAGE_SIZES = [20,50,100,500,1000]` vs `LEADERBOARD_PAGE_SIZES` (equal today,
  but the server actually accepts any `1..max`, so the "Mirrors" comment overstates it);
  `PERSONA_PRESET_WEIGHTS` vs `PERSONA_PRESETS` (byte-equal today — all four persona vectors and
  `balanced` `0.25/0.25/0.20/0.15/0.15` checked line by line); `VALID_SORT_FIELDS` vs `_SORT_BASE`
  + `p_<pillar>` (in sync); `PILLAR_ORDER`/`PILLAR_IDS`/`SORTABLE_COLUMNS` likewise duplicated.
  `PARAM_NAMES` carries **9 dead keys**: the engine's scored inputs are exactly 18
  (`grep input_id=` over `cvi/core/params/*.py`, also `cvi/core/constants.py:450-468`) and all 18
  are present, but the leftovers — `first_timer_merge_rate`, `pr_ghost_rate_14d`,
  `legal_ip_regime`, `scorecard_pipeline`, `ai_policy_posture`, `newcomer_return_rate_90d`, plus
  `active_gfis`, `archived_flag` (no Python occurrence at all) and `bus_factor` (a
  `cvi/collect/commits.py:80` signal, not a scored input) — each have a live twin with an
  **identical label string**, so the map holds two spellings for one metric.
- **Why it's wrong**: no parity test exists for any of these, so a server-side change silently
  strands the client. Dead keys are runtime-harmless (`views/drawer.js:192` and `copy.js:87`
  both fall back to the raw id) but mislead maintainers into thinking both spellings are current.
- **Fix**: follow the pattern the project already uses correctly for the weight engine — a shared
  JSON fixture consumed by both `unit.test.mjs:399-426` and
  `tests/test_ui_weights_port.py` — for persona weights, page sizes and sort fields; prune the
  dead `PARAM_NAMES` keys.
- **Confidence**: high; verified equal today by line-by-line comparison.

### H7b — Warning — contract drift that misreports state to the user

- `web/v2/copy.js:394` — `apiErrorAnnouncement` appends "showing your last good data"
  unconditionally. On a first-load failure there is no previous data, so the announcement tells a
  screen-reader user that data is present while the view is empty.
- `web/v2/copy.js:471-475` — `configureDeltaText` subtracts `balanced_rank` with no null guard,
  so a row whose `balanced_rank` is `null` renders `NaN`.
- Verified **not** drift: `cvi/serve/contract.py:206` validates `0.0 <= self.coverage <= 1.0`
  (plus per-pillar `c_<pillar>`), so coverage is a 0..1 fraction and the client's
  `Math.round(coverage * 100)` with 0.95/0.8/0.6 bands is correct. `web/v2/index.html` (25 lines)
  contains every mount point `app.js` queries (`.skip-link`→`#app-main`,
  `#app-header-controls`, `#app-primary-nav`, `#app-main`, `#app-footer`, `#app-drawer-root`,
  `#app-keymap-root`, `#app-live-region[role=status][aria-live=polite]`).

## Nits

- `web/v2/app.js:976` — `void state;`, a no-op statement.
- `web/v2/app.js:1654` — `window.__cviDebug = boot();` ships a debug handle in production.
- `web/v2/app.js:157` — hardcoded registry list `["pypi","npm","cargo","go","maven"]` for the
  filter `<select>`, not derived from any shared constant, so a new upstream registry is
  silently absent from the UI.
- `web/v2/components/table.js:98-102` — `isTieMate` guards `tie_group !== null`, which does not
  exclude `undefined`. `tie_group` is a required wire field (`cvi/serve/contract.py:156`, `:257`)
  and is `null` for singleton groups (`cvi/store/queries.py:14`), so this is reachable only via
  hand-built fixture rows. Worth a `!= null` cleanup, not a bug report.

## What the suite genuinely covers well

- **Keymap ↔ overlay parity** — `unit.test.mjs:73-106`, with a Python twin in
  `tests/test_ui_js_integrity.py` (`_expand_keymap_entry_keys` normalizes `copy.js`
  `KEYMAP_ENTRIES.keys` into real `KeyboardEvent.key` values).
- **Weight engine oracle shared across languages** — `unit.test.mjs:399-426` and
  `tests/test_ui_weights_port.py` read the *same* JSON fixture, so the JS port is checked against
  Python's real output rather than a hand-copied expectation. The right pattern, and exactly what
  H7's hand-mirrored constants lack.
- **Drawer gated / unrated / 404 contract behaviour** — `unit.test.mjs:607-785` is substantial and
  covers the interesting states.
- **Coverage-band boundaries** — `unit.test.mjs:246-253` tests band edges, not just middles.
- **Compare-view shell-key handling** — `unit.test.mjs:551-595`.
- **Honest documented deferrals** — e.g. `web/v2/views/notranked.js:138-145` documents a deliberate
  scope cut instead of pretending.

## Slice summary

- **Namespace imports defeat the integrity suite.** `import * as copy` plus a namespace check that
  only asserts "target has some exports" leaves ~600 property reads unchecked, which is the
  delivery path for H1 and H3. This is one missing check guarding the largest surface in the
  client.
- **Assertion depth, not coverage, is the gap.** The failing paths *are* exercised; the tests
  assert that a hook exists rather than that the rendered value is right. Adding value assertions
  to existing tests is cheaper than adding coverage.
- **The UI contradicts its own published spec on the headline tier**, and the test that looks
  closest to it never passes the value in question.
- **No enforced gate exists between a frontend defect and `main`**: dispatch-only CI plus
  skip-if-missing Node means green can mean untested.
- **Sanitizers and constants are duplicated rather than imported**, including an already-exported
  `escapeHtml`, so fixes do not propagate.
- **Where the project does cross-language parity properly (the weight fixture) it is sound** —
  the fix for H7 is to extend a pattern already in the repo, not to invent one.

## Coverage

- **Read fully**: `web/v2/{app,state,api,copy}.js`, all of `web/v2/components/` and
  `web/v2/views/`, `web/v2/index.html`, `web/v2/tests/{unit.test.mjs,fakedom.mjs,render.perf.test.mjs}`,
  `tests/test_ui_js_integrity.py`, `tests/test_ui_copy_usage.py`, `tests/test_ui_node_suite.py`,
  `tests/test_ui_weights_port.py`, `.github/workflows/ci.yml`, `web/v2/package.json`.
- **Cross-checked**: `cvi/core/constants.py`, `cvi/serve/contract.py`, `cvi/serve/app.py`,
  `cvi/core/params/*.py`, `cvi/store/queries.py`, `docs/UI_UX_SPECIFICATION.md`.
- **COULD NOT VERIFY**: any runtime behaviour (no `node` on this host, and no `pytest` run);
  whether `render.perf.test.mjs` can actually fail on a slow render, since it was never executed
  here; whether `workflow_dispatch` CI is diligently triggered by convention; whether the CSS
  under `web/v2/styles/` contains an `S+` token that simply never reaches the DOM (the label is
  dropped upstream of styling either way).
<!-- END H-WEB -->

---

## Cross-cutting themes

A theme is a pattern that two or more slices reached independently. Every finding ID cited
below already exists in the register above; an ID with a colon (`B:cost-accounting`,
`F:tau-vacuous`) is a sub-label kept visible inside a folded entry. Neither list is a
partition — one finding sits in several themes where the causes overlap — and nothing in this
section is a new finding.

### T1 — The gate that cannot fail

`F-01` `F-02` `F-06` `F-07` `H2` `G13`/`H4` `G12` `E-03` `A-07` `B:agents-file`

Blast radius: this is how 7 of the 18 Blockers (`H1`, `H3`, `G1`, `G2`, `G8`, `F-01`, `F-02`)
reached `main` with no signal. Each layer reads its own absent input as success. The
sensitivity gate returns 0 from `cvi/audit/sensitivity.py:540` whatever the verdict says; the
`newcomer_label_5_1` anchor at `cvi/audit/anchors.py:676` (`if positive_scores > 0:`) has no
raw count to compare against, so it cannot express "we had nothing";
`cvi/audit/detectors_report.py:96` (`measured = bool(payload.get("measured", False))`)
collapses "could not express itself" into an orderly-looking row;
`tests/test_ui_js_integrity.py:154` (`if is_namespace:`) only asserts that a namespace has
some exports, leaving roughly 600 `copy.*` reads unchecked; `tests/test_ui_node_suite.py:20`
turns an absent `node` binary into a silent pass; `.github/workflows/ci.yml:4` fires only on
`workflow_dispatch`; and E's reproducibility check skips in CI on the same host for the same
reason (`E-03`).

Scope, as settled in the register and not to be overstated: `ci.yml:26` does run
`node --test`, and `ci.yml:31-35` does consume the sensitivity verdict — on the fixture,
through `tests/audit/test_sensitivity_fixture.py:76` (`assert report.all_pass is True`).
Dispatch is manual, and the local canonical run skips silently.

Fix: one CI job that runs every audit CLI against real data with a non-zero exit wired to the
verdict; `fail_if_missing` semantics on the node wrapper; `push` and `pull_request` triggers;
and every new check ships with a mutation that turns it red.

### T2 — Absence read as measurement

`B1` `B2` `B6` `B:pagination-shape` `B:commits-cap` `B:branch-main` `B:downloads-key` `C-02`
`C-04` `C-07` `F-03` `F-07` `F:coverage-default` `G4` `H6`

The number a reader sees is sometimes "we did not look". The envelope is written in place
(`cvi/collect/envelope.py:232`, `target.write_text(`) while every resume probe is a bare
existence check (`scripts/gather.py:86-87`), so a torn file reads as a completed one. A
keyless 503 is swallowed by `except Exception: return None` at `scripts/gather.py:97-103` and
written as a null stamped `http_status=200`. Pagination ends on a shape check
(`cvi/collect/github_graphql.py:316`, `page_info = connection.get("pageInfo") or {}`), so a
short list is recorded with `truncated: False`. `commits_365d` is sliced to
`COMMIT_CAP: int = 1000` (`cvi/collect/commits.py:87`, applied at `:185`) with no flag on the
repo. The branch is guessed at `scripts/gather.py:165`
(`branch = (r.get("defaultBranchRef") or {}).get("name") or "main"`), which yields 0 workflow
runs for a repo whose default branch is not `main`, which collects the "0 if no CI" penalty.
The forward tracker coerces a missing outcome into a measurement at
`cvi/audit/forward.py:215` (`canon: (float(val) if val is not None else 0.0)`).

Each of these is byte-identical on disk to a real measurement. That is how three layers carry
it forward unflagged: the collector writes it, the store indexes it, the audit layer counts it
in its denominator.

Fix: one `Measured(value) | Unmeasured(reason)` at every boundary, serialized into the
envelope, plus a test that no `except` clause or `.get(key, default)` in the read path can
yield a scoring input.

### T3 — One fact, several definitions

`E-02` `G3` `G8` `H3` `H5` `H7` `H7b` `D-01` `D-03` `A-01` `A-02` `A-05` `F-05` `F-06`
`B:duplicate-constants` `B:spelled-paths` `B:retryable-ecosystems`

Two functions compute `constants_hash` under one field name: `cvi/snapshot/manifest.py:163`
(`def constants_hash()`) and `cvi/core/constants.py:600`
(`CONSTANTS_HASH: str = hashlib.sha256(`), and a runtime probe shows the two tables genuinely
differ (`E-02`). One API response carries three `excluded_count` meanings —
`cvi/store/queries.py:774` (`partial = sum(`) counts partial populations, `:783`
(`"excluded_count": total - len(points)`) counts dropped points, and the measured response
reports 27 where the store's own count is 26 (`G3`). The legal-IP enum lives in three places
with three answers: the comment at `cvi/core/constants.py:162`, RECIPROCAL 40.0 and
UNILATERAL 10.0 in `cvi/constants.py:89-94`, and `cvi/derive/bundle.py:52-58`, which collapses
both to `CORP_CLA` 25.0. The persona-composite rule is written in Python
(`cvi/core/pipeline.py:192`, `composites = {p: persona.persona_composite(record, p) for p in
persona.PERSONA_PRESETS}`) and again in SQL with no parity test between them — that gap is the
exact shape behind `D-01`'s coverage 1.0 versus 0.8 and tier S versus A. The 90.0 boundary is
evaluated in `cvi/core/constants.py:128`, again in `cvi/store/queries.py` with different
rounding (`D-03`), and a third time in `web/v2/copy.js:107`.

Fix: import or generate every downstream copy from the Python table.
`tests/test_ui_weights_port.py` already proves the pattern; extend it to tiers, sort fields,
page sizes, and pillar order.

### T4 — The fixture server is a second implementation, and is the one everyone tests against

`G1` `G2` `G3` `G8` `G9` `G11` `G13` `H4` `C-08` `E-03`

`scripts/serve_ui_fixtures.py:621` (`if page_size not in ALLOWED_PAGE_SIZES:`) enforces a rule
production does not enforce (`G9`); the fixture carries private per-axis counts, which is why
`G2`'s empty `axis_coverage` looked fine in the demo; and its tier vocabulary is not the
engine's (`G8`), which is what let the `H3` rename survive review. The real server cannot start
from a clean tree (`scripts/serve.py:71`,
`_DEFAULT_DB: str = str(_REPO_ROOT / "data" / "cvi.db")`, `G11`), so the fixture is the only
thing anyone runs — and the shipped `data/cvi.db` has no v2 tables at all, which slices D and G
each established by probing it directly.

`G1` is the clean case: `/reproject` serves 20 of 285 eligible rows while the UI assumes the
whole set, and every test that touches it passes `page_size=1000`
(`tests/serve/test_api.py:114`, `:130`, `:167`, `:176`, `:202`), so the truncation is invisible
from the suite.

Fix: one server. Boot `create_app` against a snapshot that CI builds, and point the JS tests at
the real handler.

### T5 — The audit layer structurally cannot notice what was never in its denominator

`F-08`/`B7` `F-01` `F-02` `F-04` `F-05` `F-07` `E-01` `E-02`

Coverage reports green with 100% of one input absent, because `cvi/audit/coverage.py:71-100`
builds its collector inventory by hand and `star_history` is not in the list. It has no
transport at all: `cvi/collect/universe.py:212` raises `NotImplementedError` under a "never
raises" docstring at `:206-215`, and `data/raw/` has no `star_history/` directory. The same
shape recurs three more times. `cvi/audit/forward.py:209` (`SELECT owner, name, rated, ...`)
defines "rated" twice with no declared equivalence; `:139` (`@dataclass(frozen=True)`
`ForwardReport`, fields `as_of`, `n_repos_rated`, `n_repos_in_analysis`, `n_prs_eligible`,
`excluded`, `pairs`, `criteria`) has no field that can carry PREREG's S0 cap disclosure; and
`cvi/audit/detectors_report.py:96` reports a check as measured from a payload flag. In slice E
the hole is in the artifact itself: `cvi/snapshot/build_cmd.py:174`
(`root = merkle_root(sorted(raw_hashes.values()))`) hashes the file set the builder chose, so
omitting `populations.json` leaves the artifact hash unable to express the thing that changed.

Fix: draw every denominator from the artifact being audited — enumerate collectors from the
`data/raw/` directory names plus the registered collector list, and derive the hashed set from
the snapshot writer's own output list.

### T6 — A durability fix lands where it was noticed and never propagates

`B1` `B4`/`B5`/`B8` `B:backfill-tmp` `B:no-resume` `C-02` `D-02`

The correct pattern exists three times in this repo and is not shared.
`scripts/backfill_downloads.py:121-133` writes `.backfill-tmp` and calls `os.replace(tmp,
path)`; v1's promote discipline swaps a temp file; `cvi/collect/envelope.py:232` writes in
place. `cvi/store/schema.py:170` (`conn = sqlite3.connect(path)`) opens with no WAL and no
`busy_timeout`, while v2 mutates the served database in place where v1 replaced it.

Because every resume probe is a bare existence check, the non-atomic write is not a cleanliness
problem — it is the mechanism that turns one interrupted run into permanently wrong scores.

Fix: one `atomic_write_json` in `cvi/` used by every writer, plus a grep test that no other
`write_text` touches a data path and that no second `sqlite3.connect` lacks `busy_timeout`.

### T7 — The project describes itself more confidently than it tests itself

`A-01` `A-02` `A-03` `A-04` `A-05` `A-06` `A-07` `A-08` `B2` `B7` `C-08` `D-04` `F-01` `F-04`
`F-05` `G2` `G:FYI-docs` `H3` `H7`

`docs/METHODOLOGY-v2.md:71` renders a per-persona AI-posture table that no code path reaches:
`cvi/core/engine.py:213` (`function = _PARAM_FUNCTIONS[input_id]`) calls every non-ECDF input
with no persona argument. The benchmark specification still prints v1 gradients. Three docs
state that `S+` exists — `docs/UI_UX_SPECIFICATION.md:334` (`[ Tier S+ ]`),
`docs/ARCHITECTURE.md:525` (`| rank_tier | Enum | Tier S+, ... Tier D |`),
`docs/BENCHMARK_SPECIFICATION.md:38` (`Tier (S+ to D)`) — while `web/v2/copy.js:107` renames it
(`H3`). Docstrings promise what the code contradicts: "never raises" at
`cvi/collect/universe.py:206-215`, above a `raise NotImplementedError`, and the "no Python
loop" claim behind `D-04`. `F-01`'s SKIP text describes a check that cannot run. The serve
report header cites `docs/bedrock/*` and `docs/standards/*`, neither of which exists in this
checkout (`G:FYI-docs`).

Fix: a doc-consistency test that pulls the tier table, parameter names, and gradient numbers
out of `cvi/core/constants.py` and asserts that the literals in the three specs match — the
shape of `tests/test_ui_weights_port.py`, aimed at prose.

### T8 — Cost recorded and never read

`B9` `B:cost-accounting` `B:retry-storm` `B4` `B6` `B8` `G6`

The budget that `docs/v2/08-SUSTAINABLE-COLLECTION.md` §4 makes load-bearing does not exist in
running code. `cvi/collect/ratelimit.py` is 332 lines that no collector imports — its only
readers are `tests/collect/test_ratelimit.py:17` and `tests/core/test_import_graph.py:21`,
`:111-121` — it cannot express a points budget, and its cache would not survive a rerun.
`"rate_limit_remaining": remaining,` at `cvi/collect/github_graphql.py:885` is consulted by
nothing. A failed call books `cost = 0`, which under-reports exactly when the retry storm
spends most: roughly 19 extra calls per deleted login (`cvi/collect/github_graphql.py:736` and
the retry path around it).

The server carries the same miniature. A discarded `snapshot_summary` is 27.40 ms of a
37.58 ms leaderboard request at 10k rows, about 73%, and it is repaid on every page turn
because there is no `Cache-Control` or `ETag` (`G6`).

Fix: one budget object read before each call, plus a test that the collector refuses to start
when it is exhausted.

## Method, gaps, and what was not verified

### How this was run

Eight auditors worked in parallel, one per slice in the scope map, each editing **only**
inside its own `<!-- BEGIN X --> … <!-- END X -->` block in this file. No file outside this
report was written; no code was changed. Everything was done on `main`, in a working tree
that a **concurrent data-collection agent was also using** — HEAD moved under the auditors
several times, and that agent once clobbered F's `END F-AUDIT` marker (F repaired it). Line
numbers below are as of each auditor's read; re-verify before quoting one in a PR.

Severity is each auditor's claim against the repo vocabulary in *How to read this*, not an
independent measurement. The merge pass deduplicated by root cause and re-ranked by blast
radius, but it did not re-execute the code.

Infrastructure worth knowing about, because it shaped what got said: the subagent pool model
hit a provider quota partway through and several auditors were re-run on a different model;
background subagent dispatch silently did nothing on this account, so all eight ran in the
foreground. That is why some slices have two passes of the same question and slightly
different prose.

### Citation defects found in the audit itself

Three files appear in auditors' coverage lists that **do not exist** in the repo:
`cvi/audit/provenance.py` and `cvi/audit/diff.py` (both in F's "read fully"), and
`cvi/store/ops.py` (in D's "skimmed", and in the register's store-nit bullet). The real
`cvi/audit/` is `__init__, anchors, coverage, detectors_report, forward, methodology_history,
sensitivity, ties`; the real `cvi/store/` is `__init__, migrations, queries, reader, schema,
writer`. The v1 leaderboard-cache code those citations describe is real, but lives in
`cvi/api.py` and `cvi/lakehouse.py`. A fourth: E cites `migrations/0001_init_v2.sql`; the
v2 schema is actually in `cvi/store/migrations/0001_init.sql`. The findings built on that code
stand — only the paths are wrong. Treat a path in this report as a pointer to the claim, not
as a line you can paste.

### Never read by anyone

`scripts/demo_app.py` (no auditor mentioned it), `data/promoted.json`,
`cvi/store/reader.py`, `cvi/store/queries.py:1-255`, `cvi/lakehouse.py`,
`docs/ARCHITECTURE.md`, `scripts/derive.py`, `scripts/verify.py`. G's slice covered
`cvi/serve/**` but not the two older read paths it also owns (`cvi/api.py` is covered,
`cvi/lakehouse.py` is not). E's reproducibility claims about `make_sample` and
`build_fixture_snapshot` were reasoned from code, not re-executed as a fresh from-scratch
sample build.

### What could not be executed at all

- **No `node` binary on the host.** Every H finding and `G13` is static reading only — the
  frontend was never run, so no browser-observable behaviour is confirmed. `H1`
  (`drawer.js:351` reads `copy.EVIDENCE_RETRY_LABEL`, which `copy.js` never exports) is a
  certainty from the module source; how it fails at runtime is inference.
- **`G10` has no heading by design.** Auditor G filed it under Coverage as an unvalidated
  inference and declined to assert it. It is deliberately absent from the register.
- **No test-suite run for A, B, C, E, F, H.** D ran `pytest tests/store/` → 128 passed.
  Nobody captured the whole-suite number the README claims (2,211 passed / 1 skipped), so
  "tests pass" is not evidence this audit either confirms or refutes.
- **`sqlite3` CLI absent**; D used Python's `sqlite3` (library version 3.53.1).
- **No network.** deps.dev system ids, upstream API responses, and every rate-limit claim
  about a live provider are unverified.

The live `data/cvi.db` is **v1 lakehouse only** — roughly 1,000 `repositories` rows with the
v1 columns (`overall_cvi`, `rank_tier`, `deep_dive_json`), `PRAGMA user_version = 0`,
`journal_mode = delete`, integrity check clean. **There are no v2 tables in it.** Every v2
store finding was therefore checked against the in-memory v2 schema built from
`cvi/store/migrations/0001_init.sql`, not against the file anyone is actually serving. The
blast radius of `D-01`/`D-02` is a code path, not an observed corruption; equally, nobody has
yet seen a v2 migration run against a real database.

### Sequencing gate carried from B

`B1` (atomic envelope write plus validity re-probe) and `B2` (never envelope a failed keyless
fetch) must land **before** `B3`, `B4`, `B6a`, and before the ledger/watermark redesign in
`docs/v2/08-SUSTAINABLE-COLLECTION.md` §4 and §6. Otherwise the new ledger faithfully records
corrupt envelopes as collected, and the redesign inherits the bug it was meant to detect.
