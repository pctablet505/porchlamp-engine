# CVI v2 — design and delivery plan (2026-09-12)

This directory is the binding design for the next iteration of the Contributor
Value Index. Where it conflicts with `docs/ARCHITECTURE.md` §2–§4 or
`docs/BENCHMARK_SPECIFICATION.md`, this directory wins; the old documents remain
the origin of the calibration curves, the UI design, and the persona presets.

Written by the directing model on 2026-09-12 after auditing the code, the spec,
the live derived data (3,762 bundles), the served database, and GitHub/deps.dev
endpoints. Implementation is delegated to local agents through `05-WORK-PLAN.md`.

## Files

| File | What it is | Who reads it |
|---|---|---|
| `01-FINDINGS-2026-09-12.md` | Audit results, evidence-tagged; what the current ranking actually computes; answers to the handoff's ten questions | Lead, owner |
| `02-RANKING-DESIGN.md` | **The ranking process v2**: stage order, gates, 18 parameters with windows and minimum sample sizes, missingness policy, normalization, weights, tie-breakers, persona gates, versioning, validation. Every decision has an id `D-*` that packets cite | Everyone; packets cite sections |
| `03-ANTI-MANIPULATION.md` | Threat model, per-parameter cost-to-fake table, input hygiene, pipeline integrity (hash manifests, reproducibility), detectors, process controls | Lead, reviewers, integrity packets |
| `04-ARCHITECTURE.md` | Target package layout, core contracts (`Measured`/`Unmeasured`, envelopes, snapshots), store schema v2, API v2, migration path | Architecture packets, reviewers |
| `05-WORK-PLAN.md` | Waves and packets: goal, evidence, cited decisions, file ownership, steps, acceptance (fail-before tests), measurements to report, forbidden actions, model routing, dependencies | Local agents (one packet each), lead |

## How a local agent uses this directory

1. You receive **one packet id** (for example `W1-1`). Open `05-WORK-PLAN.md`,
   read only that packet and the "Rules for every packet" section.
2. Read only the `D-*` decisions the packet cites in `02-RANKING-DESIGN.md`
   (and the `03`/`04` sections it names). Do not read the whole directory.
3. Work in your own worktree with your own scratch data directory (rule R-3).
4. Review happens under the hub review protocol:
   `/home/pctablet505/Projects/kimi-colab-context/data/context/shared/process/review-protocol/00-README.md`
   (levels `20-LEVELS.md`, lenses `30-LENSES.md`, templates `60-TEMPLATES/`).
   Your verdicts are `PASS`, `FAIL`, `UNDECIDED` per item; the lead decides
   `ACCEPT`/`REWORK`.

## Non-negotiables (owner rules, restated)

- Only verified data enters SQLite. Provenance is an envelope path plus that
  envelope's content hash (URLs live inside envelopes as source_url); a bundle
  without that is unverifiable and never promoted (lead ruling 2026-09-12).
- No fabricated repositories and no fabricated telemetry. The LLM-curation
  path (`scripts/curate_dataset_llm.py`, `HANDOFF-DATASET-CURATION-AGENT.md`)
  is deleted in packet `W0-4`; do not run it.
- Every behavioural change ships a test that fails before the change and an
  over-correction guard that the correct path is unchanged.
- Never edit `data/` in place, and never change the behaviour of a running
  sweep. `scripts/run_all.py --all --skip-downloads` (pid 173791, started
  09:08 on 2026-09-12) is still writing `data/raw`, `data/derived`,
  `data/verified`. Use a copied sample under a scratch directory.
- Agents report diffs; the lead commits serially. Commit author email is the
  noreply address (see `docs/REVIEW_GUIDELINES.md` §5).

## Owner decisions requested (do not block on these; defaults are stated)

| # | Decision | Default in this plan | Where |
|---|---|---|---|
| O-1 | Legal-IP score for "no CLA and no DCO" | 85 (between DCO 100 and foundation CLA 75) | `02` D-PARAM 3.1 |
| O-2 | Pillar weights 25/25/20/15/15 | Unchanged (R2 mandates them); within-pillar composition changes; a v3 question | `02` D-AGG-1 |
| O-3 | Delete the LLM-curation script and its handoff | Delete (integrity violation of the only-verified rule) | `05` W0-4 |
| O-4 | Universe size for v2.0 | Freeze at the 5,753 candidates minus non-software; expand criteria at v2.1 | `02` D-UNI |
| O-5 | Tier cutoffs | Absolute 90/80/70/55/40 kept; coverage ceilings added | `02` D-MAT-2 |
| O-6 | Forward validation experiment | Start at the first v2 snapshot; re-collect 90 days later | `02` D-VAL |
| O-7 | Model swap for review windows | Flash-Next implements and reviews; swap to the 27B for adversarial passes at wave gates | `05` §0.2 |

## Status

| Wave | State | Landed packets |
|---|---|---|
| W0 Freeze & instrument | in progress | W0-1, W0-2, W0-2b, W0-3, W0-5 (W0-4 in flight) |
| W1 Data truth | in progress | W1-1, W1-2, W1-5, W1-9 (W1-3 in integration; W1-4/6/7, W1-8 queued behind it) |
| W2 Scoring v2 | in progress | W2-1 (W2-2, W2-3 in flight) |
| W3 Integrity | in progress | – (W3-3 in flight) |
| W4 Store / API / architecture | not started | – |
| W5 Validation & release | in progress | W5-1 (code ahead of snapshot; W5-2 pending) |

The lead updates this table; agents never edit it.

### Lead log

| Date | Packet | Decision | Note |
|---|---|---|---|
| 2026-09-12 | W0-2 | ACCEPT, `f5f1e4b` | Reproducer PASS on every claim (equal `iterdump` sha256 at fixed `as_of`, differing at base). Guard-scope follow-on: `tests/test_no_clock.py` exempts `lakehouse.py` and `provenance.py` by module basename, and SQL-side clocks (`DEFAULT CURRENT_TIMESTAMP`) are invisible to the regex — tracked as follow-on packet W0-2b. |
| 2026-09-12 | W0-1 | ACCEPT, `ba3f4c1` | Sample `~/Projects/cvi-scratch/sample-200` (200 repos / 2,579 envelopes) + `cvi audit coverage`. Sample misses the ±2 pp clause on `mean_inline_comments` (+4.6) and `rr_90` (+3.3); the same tool on all 4,145 collected repos matches `01` F8 within 1.0 pp, so the tool's definitions are right and the misses are 200-repo sampling noise. Deviations recorded: size S packet landed 1,392 source lines; `cvi.audit` added to `pyproject.toml` packages by grant to W0-3. Open taxonomy question handed to W1-9/W2-4: candidate-ecosystem strata disagree with the deriver's `meta.ecosystem` for 68 of 200 repos. |
| 2026-09-12 | W0-3 | ACCEPT, `32bb8b3` | Freeze + reproduce on `sample-200` give the identical canonical dump `4315c2ed…`; a one-byte flip changes the Merkle root (`49814505ca…` -> `080d07fc12…`) and `reproduce` names the key, exit 1. Manifest 685,938 B / 2,181 entries, reproduce 5.26 s on 200 repos, 2,181 hashes `backfilled` / 0 native. Suite 1,046 passed. Deviation: 754 source lines against size class M (<=400) — subtraction deferred, not a blocker; the packet's own CI-fixture and store-column items belong to W3-4 and W4-2. |
| 2026-09-12 | W0-4 | dispatched | Uses the preserved pre-sweep DB `~/Projects/cvi-scratch/v1-baseline/cvi.db` for the fixture-row test because the running sweep will replace `data/cvi.db`. |
| 2026-09-12 | W0-2b | ACCEPT, `7945e99` | Guard-scope follow-on: `tests/test_no_clock.py` now scans `lakehouse.py`/`provenance.py` and catches SQL-side clocks (`DEFAULT CURRENT_TIMESTAMP`); `utc_now_iso(now)` requires the explicit time. Suite green. |
| 2026-09-12 | W0-5 | ACCEPT, `3f6c5e0` | Anchor relations, sensitivity τ, tie report (`cvi/audit/` owns its wiring). Baseline: τ ≥ 0.957 on every pillar; ties: 118 distinct values / 981 rows. |
| 2026-09-12 | W1-9 | ACCEPT, `cf41a58` | Eligibility metadata and software classification (archived/mirror/fork, license spdx, PRs-enabled, tree paths; `non_software`/`mirror`/`archived`/`fork`/`unlicensed`/`off_platform` markers). |
| 2026-09-12 | W2-1 | ACCEPT, `5c13054` | Core types: Measured/Unmeasured contracts, FeatureBundle v2, frozen constants (19 schema inputs). Size deviation: 1,258 lines vs M — subtraction deferred. Packaging fix `39952ca` (pyproject packages) landed with it. |
| 2026-09-12 | W1-1 | ACCEPT, `8252a8c` | Per-label GFI collector (label list once → per-label queries → union by issue number, `labels_matched`); rebased onto current main. Live: vscode=7, flask=0 (all flask issues closed — recorded, not a defect). |
| 2026-09-12 | W5-1 | ACCEPT, `56d3cbb` | Forward-validation pre-registration + analysis (`cvi/audit/forward.py`, `docs/v2/validation/PREREG.md`); real snapshot S0 waits on W5-2. Size deviation: 707 lines vs M — subtraction deferred. `forward` registry wiring was an OWNERSHIP REQUEST → dispatched as W5-1f. |
| 2026-09-12 | W5-1c | ACCEPT, `b19a015` | Explicit `--fetched-at` for forward collect after W0-2b (no wall-clock reads on the forward path). |
| 2026-09-12 | W1-5 | ACCEPT, `ea59caf` | Commit email domains, real EF, bus factor, vitality; rebased. EF mean on sample 2.47 → 1.75. One-time exception: re-gathered commits live for all 200 sample repos to execute its Measure line (exceeds the 5-live-repo bound) — adjudicated allowed once; the bound is re-asserted in every subsequent dispatch. `github.com` added to PERSONAL_DOMAINS (accepted). |
| 2026-09-12 | W1-2 | ACCEPT, `bc01e21` | Package-level dependents (deps.dev primary, ecosyste.ms fallback, root-org dedup) + derive hook; stacked by integrator (W1-2b) after the W1-5 hooks in both monoliths; full suite 1,227 passed. Adjudications: package-level `dependentCount` = default-version endpoint (flask 12,698); repology keyless 403 → `UNDECIDED/U-ENV`; ecosyste.ms fallback URL 404 live → follow-up W1-2f; sample missing-rate stays 1.00 until re-sweep. |
| 2026-09-12 | W1-3 | ACCEPT, `d8d3189` | Windowed PR collection (GraphQL, 180 d, cap 500, typed actors, review threads) + derive hook; stacked by integrator after W1-2 hooks; suite 1,248 passed. Latency sample median 4 to 56 (from implementer Measure). |
| 2026-09-12 | W5-1f | ACCEPT, `d275499` | `forward` wired into the `cvi.audit` lazy re-export registry (33 public names; first-provider rule for `main` preserved + test). |
| 2026-09-12 | W1-8 | ACCEPT, `ab8893f` | Downloads status + resumable pypistats backfill runner (throttled, fake-server resume test) + derive hook; stacked by integrator after W1-3; suite 1,267 passed on main. BigQuery probe stays `UNDECIDED/U-ENV`. |
| 2026-09-12 | W1-2f | ACCEPT, `6129f7c` | ecosyste.ms fallback fixed: registry segment must be the registry NAME (`pypi.org`), not the ecosystem slug (`pypi`) - root cause confirmed in the service source; live 200 measured (flask dependent_packages_count 2,451), byte-exact 200 fixture recorded; old URL re-measured 404 (byte-identical to W1-2 fixture). |
| 2026-09-12 | W2-3 | ACCEPT, `cf71367` | 18 parameter functions (`cvi/core/params/`), 02 section 4 curves, min-n, D-PARAM-MONO property test that rejects the old Gaussian; 55 tests. Size deviation: library 867 lines vs L-class <= 800 - subtraction deferred. `cvi.core.params` added to pyproject packages (39952ca precedent). |
| 2026-09-12 | W2-2 | ACCEPT, `6651386` | Gates v2: D-GATE-1..6 + D-EVID-1 in `cvi/core/gates.py`, UNDECIDED gates recorded by decision id; 36 tests incl. all four oracles (windowed denominator flips 4 v1-only black holes; v2 sample: 55 black hole / 34 stale / 14 non_software / 7 archived vs v1's 27 black holes). |
| 2026-09-12 | W3-3 | ACCEPT, `d1103bf` | Live re-verification canary (`cvi.verify.live_sample`, fake-transport tests, systemd units shipped NOT installed); 5-vs-10 live-repo deviation recorded; live run detected 1 real drift (pytorch/pytorch open_pr_count 16 to 25 vs tolerance 8). `cvi.verify` added to pyproject (manual merge of a pyproject reject). |
| 2026-09-12 | W0-gate | gate pass (adversarial, 23 probes) | All five landed W0 packets probed: **1 Blocker (P-09)** corrupt-JSON envelope crashes `freeze`/`reproduce` (unguarded `read_envelope`) -> fix dispatched as W0-3f; 4 minors: P-07b stale-hash MATCH (in W0-3f), P-10c degenerate tau-b -> 1.0 (in W0-3f), P-01 unguarded no-clock exemption (in W0-3f), P-04 legacy-DLL `DEFAULT CURRENT_TIMESTAMP` wall-clock stamp - accepted, migration belongs to W4-2. |
| 2026-09-12 | dispatch wave | 7 agents dispatched | After W1-2: W1-3 integrator (critical path for W1-4/W1-6/W1-7), W2-2 gates, W2-3 parameter functions, W3-3 canary (5-vs-10 live-repo deviation recorded), W0 wave-gate adversarial pass on landed W0-1/2/2b/3/5, W1-2f ecosyste.ms endpoint check, W5-1f audit `forward` registry wiring. W0-4 (Hubble) still in flight. |
| 2026-09-12 | HYG-1 | ACCEPT, `970500a` | Lead takeover after the GPU loss (NV_ERR_GPU_IS_LOST 14:46; vLLM dead; servedeck persisted STOPPED; local swarm stopped). 19 legacy ruff findings cleared in four test files, no behaviour change; CI lint gate now blocked only by the file W0-4 deletes. Reproduced by the lead. |
| 2026-09-12 | INT-W2-4 | ACCEPT_WITH_FOLLOWUP (Opus), `7c68486` | Mid-rank ECDF + frozen populations integrated unchanged; 213 packet tests, suite 1,426, populations.json byte-identical to the local agent's run. Opus review: 44 PASS / 4 FAIL / 9 UNDECIDED, 5/5 mutants killed, no Blocker. Follow-ups → W2-4f: assert the frozen document header; pin (ecosystem, param) emission order with a hash-stable test across PYTHONHASHSEED; reject non-finite inputs (NaN scored 50.0 silently); rename test_nine_below_min_is_flagged. Rulings: size class breach (292 lines vs S) ratified; D-NORM-2 sub-30 global case: use the global population whatever its size and keep population_fallback=True, no extra rule for W2-5. |
| 2026-09-12 | INT-W0-3f | ACCEPT_WITH_FOLLOWUP (Opus), `9f62e22` | W0-gate fixes P-09/P-07b/P-10c/P-01 applied with zero conflicts; 270 packet tests, suite 1,421; P-09 reproduction: corrupt envelope → exit 1 naming INVALID_JSON scorecard/AtsushiSakai__PythonRobotics, 2,180 others still reported. Opus review: 51 PASS / 2 FAIL / 4 UNDECIDED, 12/12 mutants killed. Follow-ups → W0-3g: freeze refuses to write a manifest when any envelope is invalid unless --allow-invalid is passed, and reproduce fails on a frozen non-empty invalid_json (symmetric with P-07b); give PerturbationResult.reason a production reader or delete it; use the INVALID_JSON constant everywhere and add a STALE_HASH constant; add EnvelopeScan to __all__; extend the no-clock regex to datetime.today(). |
| 2026-09-12 | INT-W0-4 | REWORK round 1 (Opus) | Claims reproduced and 10/10 mutants killed, but three Blockers: the provenance rule never compares a hash on real data (no envelope or bundle records one); a foreign repository's envelope satisfies it; the serve guard is defeated by renaming owners. Rework spec sent: derive records the envelope hash (W0-3's function), verify recomputes it and binds path to repo key and collector, promote writes a snapshot_meta identity bound to the canonical dump hash and requires a frozen manifest (pipeline order gather → derive → verify → freeze → promote → serve, run_all gains the freeze step), serve verifies the identity; the http(s)+hash branch is deleted. Ruling: provenance is an envelope path only (non-negotiables wording updated above). |
| 2026-09-12 | W2-9 | dispatched (new packet) | Wire every landed collector output into FeatureBundle v2: the v1 loader maps 5 of 18 inputs, so W2-4 measured 0 of 67 for dependents and ttm and W2-5 rated 0 of 164 (coverage capped at 0.3075). Declarative mapping table, malformed-event tolerance (15 of 199 sample bundles rejected for empty author_login), provenance hashes carried. Owner: cvi/derive/bundle.py. |
| 2026-09-12 | W1-7, W2-5 | implemented, under Opus review | W1-7: 10 Pike signals + scorecard subset + review/CI proxy, three fixes over the local draft, suite 1,437. W2-5: aggregate/matrix/engine with 43 exact oracles, suite 1,469; flagged a raw-vs-log-space mismatch between W2-3 curves and W2-4 populations for the reviewer to adjudicate. |
| 2026-09-12 | W2-4f | ACCEPT, `0d47899` | W2-4 follow-ups: frozen document header pinned, (ecosystem, param) emission order pinned across PYTHONHASHSEED subprocesses, NaN/inf rejected with ValueError (was a silent 50.0), boundary tests at 29 and 30. Three named mutants killed; suite 1,452; no public signature changed (W2-5 unaffected). Reproduced by the lead. |
| 2026-09-12 | W1-7 | ACCEPT_WITH_FOLLOWUP (Opus), `ed7abb2` | Ten Pike signals computed locally, scorecard 5-check subset, review/CI proxy with method tag; five draft modules consolidated into one; suite 1,437 (1,476 on the merged tree). Opus: 49 PASS / 8 FAIL / 0 UNDECIDED, 12/12 mutants killed, independent 10/10 signal recomputation on pallets/flask, merge trial clean. Follow-ups → W1-7f (queued behind INT-W0-4 round 2 because it must use the provenance helper that gains hashes): guard the unguarded max(ats) crash path with a test; make the over-correction guard actually assert; ruling F2: an empty commits envelope from a successful fetch is a measured zero (commit_frequency 0, updated_since from pushed_at), Unmeasured only when the envelope is absent or failed; ruling F5: fetch-time windowed counts store window_anchor and derive treats an anchor more than 14 days from as_of as Unmeasured(stale_window); use gather's src() and SLEEP_REST instead of duplicates; record n for 4.1 (checks present, merged PRs for the proxy) so the min-n rule can fire. |
| 2026-09-12 | W2-5 | ACCEPT_WITH_FOLLOWUP (Opus), `6e2dfb5` | Aggregation, coverage ceilings, composite, coordinates and engine; 43 exact oracles, suite 1,469 (1,495 on the merged tree). Opus: 39 PASS / 8 FAIL / 2 UNDECIDED, every oracle matched independent arithmetic, merge trial clean; 13/16 mutants killed, the 3 survivors all in the rounding-location family (missing guards, code correct). Rulings: log-space question resolved by deleting the transform from the ECDF curves and keeping populations raw (monotone transform is inert under mid-rank ECDF; 02 rows 2.1/2.3 and D-NORM-3 amended); a missing population yields Unmeasured(no_population) (D-MISS-1 amended), never an escaping LookupError; the unrated path must keep the stage-0 GateResult so gated and unrated stay distinguishable; raw_hash is supplied by the caller. Follow-ups → W2-5f. |
