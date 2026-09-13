# Work plan — waves and packets for local agents

## 0. How this plan runs

### 0.1 Roles

| Role | Who | Does |
|---|---|---|
| Lead | the directing model (or the owner) | sequences packets, resolves `UNDECIDED`, commits serially from reported diffs, runs wave gates, updates the status table in `00-README.md` |
| Implementer | local model, one per packet | works the packet in its own worktree and scratch data dir, produces a handoff with verbatim outputs |
| Reviewer | local model, a *different* session from the implementer | executes hub review-protocol items (L0–L2 always; L3 for architecture packets; lenses named in the packet), one item record per item |
| Reproducer | local model, third session | re-runs the acceptance commands from the handoff on a fresh worktree; `PASS` only if outputs match |
| Adversarial verifier | 27B (swapped in) or the lead | wave gates only: tries to break the landed packets |

### 0.2 Models and routing (as of 2026-09-12)

- Live: `Qwen3.8-Flash-Next-Uncensored-NVFP4` on `:8001` (OpenAI-compatible;
  chat clients that need `reasoning_content` use the proxy on `:8005`), 262k
  context, util 0.96, GPU full. It fills implementer, reviewer and reproducer.
- `Qwen3.8-27B-NVFP4` is not running; the GPU cannot hold both. Swap through
  servedeck (`POST :8010/api/server/stop`, then `start` with the inline
  backend) for wave-gate adversarial passes only, then swap back. Check
  `~/Projects/local_llm/.config` after any non-flashnext start.
- Effort: implementer high; reviewer per protocol item (mechanical); the
  lead's own tokens go to gates and `UNDECIDED` resolution only.

### 0.3 Packet lifecycle

1. Lead dispatches: packet id, this file's path, `00-README.md` path, the
   worktree path, the scratch data dir, the base commit sha.
2. Implementer: `git worktree add ~/Projects/cvi-wt-<id> -b <id> <base>`;
   copy the sample data (`~/Projects/cvi-scratch/sample-200/`, created once by
   W0-1) to `~/Projects/cvi-scratch/<id>/`; export `CVI_DATA_DIR` to it.
3. Fail-before proof: write the test first, run it, paste the failing output;
   never `git stash`, never `git checkout -- <path>`; use
   `git show <base>:<path> > <path>` to reproduce pre-change behaviour.
4. Implement; run the packet's test set (not the full suite unless the packet
   says so); ruff clean.
5. Handoff at `.agents/<id>/handoff.md`: diffstat, files, verbatim test
   outputs with exit codes, the packet's **Measure** lines filled with numbers,
   `COULD NOT VERIFY:` list (may not be empty on a large diff).
6. Reviewer: item records under the protocol; verdicts `PASS/FAIL/UNDECIDED`;
   forbidden phrases scanned.
7. Reproducer: re-runs acceptance on a clean worktree from the diff.
8. Lead: `ACCEPT` / `REWORK` (subtractive; max 4 rounds, the fourth bounded to
   ≤ 3 named additions) / `PARK`; commits with the noreply author.

### 0.4 Rules for every packet (R-*)

- **R-1** Every behavioural change has a test that fails before and passes
  after, plus an over-correction guard that the correct path is unchanged.
  Assert values, not shapes.
- **R-2** One definition per fact: constants only in the frozen constants
  module; no duplicated thresholds.
- **R-3** Never write under `data/` of the main checkout; never change the
  running sweep (pid 173791). Scratch dirs only. Collector changes are tested
  against recorded envelopes and at most 5 live repos.
- **R-4** No fabricated repos or telemetry; no LLM-generated data of any kind;
  every provenance is an envelope path with a hash (`03` §5).
- **R-5** Hedge words are banned in evidence (`should`, `likely`, `appears`).
  `UNDECIDED` with a reason code is legal; guessing is not.
- **R-6** Subtractive rework: answer an objection by removing, not adding;
  diffstat must not grow between rounds.
- **R-7** Do not read beyond the packet's cited sections; budget lines are in
  the dispatch.
- **R-8** No `print()` in library code; no inline `TODO`; typed public API
  with docstrings; ruff clean.
- **R-9** Report numbers measured, never estimated; paste command output.
- **R-10** A packet touches only its owned files; anything else is a
  `COULD NOT VERIFY` or an ownership request to the lead.

### 0.5 Wave gates (what the lead checks before the next wave)

- All packets in the wave `ACCEPT`ed and committed; the packet test sets
  green; the full suite run once per wave with failure-set identity
  (`docs/REVIEW_GUIDELINES.md` §1).
- The wave's **Measure** numbers reproduced by the reproducer.
- Adversarial pass (27B or lead) found no `Blocker`.
- Status table updated.

## 1. Waves

| Wave | Purpose | Packets | Depends on |
|---|---|---|---|
| W0 | Freeze and instrument: measure before changing anything | W0-1..W0-5 | – |
| W1 | Data truth: collectors and derivers deliver what the engine needs | W1-1..W1-9 | W0-1, W0-3 |
| W2 | Scoring v2 (`02-RANKING-DESIGN.md`) | W2-1..W2-8 | W1 for real-data acceptance; unit work can start after W0 |
| W3 | Integrity (`03-ANTI-MANIPULATION.md`) | W3-1..W3-4 | W2-1, W1-3 |
| W4 | Store, API, architecture (`04-ARCHITECTURE.md`) | W4-1..W4-5 | W2 |
| W5 | Validation and release | W5-1, W5-2 | W2, W3, W4 |

Dependency shape rule: no packet is a dependency of more than two others
except W0-1 (sample data) and W2-1 (types), which are small and land first.

## 2. Packets

Format: **Goal** · **Why** (finding ids from `01`) · **Cites** (decision ids in
`02`, sections in `03`/`04`) · **Owns** · **Steps** · **Acceptance** ·
**Measure** · **Forbidden** · **Depends** · **Size** (S ≤ 150 lines of source,
M ≤ 400, L ≤ 800).

---

### W0-1 Sample data and coverage audit tool
**Goal.** A 200-repo sample copied from `data/raw` (stratified: 33 per
ecosystem, including the anchors) under `~/Projects/cvi-scratch/sample-200/`,
and `cvi/audit/coverage.py` that prints the missingness/constant/sample-size
table from `01-FINDINGS` §B for any data dir.
**Why.** F2, F4, F5, F7, F8: nothing can be shown fixed without a before/after
table. **Cites.** `01` §B, §F.
**Owns.** `cvi/audit/__init__.py`, `cvi/audit/coverage.py`,
`tests/audit/test_coverage.py`, `scripts/make_sample.py`.
**Steps.** Copy envelopes for the 200 keys (all collectors) read-only from
`data/raw`; write the tool: per input, missing rate, derived rate, distinct
values, zero fraction, quantiles; per repo, PR count, external count, latency
count, days spanned; black-hole count with denominator histogram.
**Acceptance.** Tool output on the sample matches the numbers computed by the
lead within ±2 pp on missing rates (`01` §B); unit test on a 3-bundle fixture
asserts exact values.
**Measure.** The full table for the sample, pasted.
**Forbidden.** Writing anything under `data/`. **Depends.** – **Size.** S.

### W0-2 Deterministic time
**Goal.** Scoring takes `as_of` explicitly; no wall-clock reads in `cvi/`
scoring modules.
**Why.** D-VER-3; reproducibility is impossible today.
**Cites.** `02` D-VER-3, `04` §8.
**Owns.** `cvi/engine.py`, `cvi/playbook.py` (`BENCHMARK_NOW` removed),
`cvi/deepdive.py`, `scripts/promote.py`, `tests/test_no_clock.py`, affected
tests.
**Steps.** Thread `now`/`as_of` from `promote` into every call; delete
`BENCHMARK_NOW`; add a test that greps `cvi/` (excluding `collect`/`serve`)
for `datetime.now`, `utcnow`, `time.time` and fails on any hit.
**Acceptance.** Grep test fails before (paste), passes after; promote on the
sample twice with the same `as_of` gives identical `cvi.db` dump hashes.
**Measure.** Two dump hashes, equal. **Depends.** W0-1. **Size.** S.

### W0-3 Envelope hashes and snapshot manifest
**Goal.** Every envelope carries `sha256`, `http_status`, `collector_version`;
`cvi snapshot freeze --as-of` writes `manifest.json` (envelope hashes, Merkle
root, code commit); `cvi reproduce` rebuilds from raw and compares a canonical
dump hash.
**Why.** `03` §5; F10. **Cites.** `03` §5, `04` §2 (`snapshot/`), D-VER-3.
**Owns.** `cvi/collect/envelope.py` (new), `cvi/snapshot/*` (new),
`scripts/gather.py` (write path only), `tests/snapshot/*`.
**Steps.** Backfill hashes for existing envelopes at read time (hash the
`data` field canonical JSON when `sha256` is absent; record `backfilled`);
manifest lists `(collector, key, sha256)`; canonical dump = sorted rows, fixed
float format.
**Acceptance.** Freeze + reproduce on the sample: identical hash; flipping one
byte in one envelope changes the root and makes reproduce report the key.
**Measure.** Manifest size, root hash, reproduce runtime on 200 repos.
**Depends.** W0-2. **Size.** M.

### W0-4 Remove the fabrication path; unforgeable provenance; served-DB guard
**Goal.** Delete `scripts/curate_dataset_llm.py` and
`HANDOFF-DATASET-CURATION-AGENT.md`; `verify` flags `unverifiable_provenance`
for any provenance source that is not an envelope path whose hash matches;
`serve.py` refuses a DB that contains fixture names or lacks a snapshot id.
**Why.** F1, F10 (owner decision O-3, default delete). **Cites.** `03` §5.
**Owns.** the two deleted files, `scripts/verify.py`, `scripts/serve.py`,
`tests/test_verify.py`, `tests/test_serve.py`, `docs/REVIEW_GUIDELINES.md` §3
(one sentence).
**Acceptance.** A bundle with `"source": "ecosystem-intelligence"` is FLAG
(fail-before shows CLEAN); a real sample bundle stays CLEAN (guard); `serve.py
--db data/cvi.db` (the synthetic one) exits non-zero with a message naming
the fixture row; a promoted sample DB serves.
**Measure.** Count of sample bundles flagged by the new rule (expected 0).
**Depends.** W0-3 (hash rule). **Size.** S.

### W0-5 Anchors and sensitivity harness
**Goal.** `cvi audit anchors` evaluates D-VER-4 relations on a DB; `cvi audit
sensitivity` computes Kendall τ for ±5 pp per pillar; `cvi audit ties` reports
distinct composite values and tie-group sizes.
**Why.** D-AGG-3, D-VER-4, F14. **Cites.** `02` §7, §11.
**Owns.** `cvi/audit/anchors.py`, `cvi/audit/sensitivity.py`,
`cvi/audit/ties.py`, `tests/audit/*`.
**Acceptance.** On the current v1 sample DB the harness runs and reports (it
will show failures; that is the baseline). Unit tests with a 6-row fixture
assert exact τ and tie counts.
**Measure.** Baseline τ per pillar and tie statistics on the v1 sample.
**Depends.** W0-1. **Size.** M.

---

### W1-1 Newcomer-label issues collector
**Goal.** Replace the AND-query with: fetch the repo's label list once, select
labels matching the alias regex (D-PARAM 5.1), query each selected label
separately (`labels=<one>`), union by issue number, keep open + unassigned +
updated within 90 d; record `labels_matched`.
**Why.** F2 (0 vs 18/36 live). **Cites.** `02` §4 5.1, D-IN-4.
**Owns.** `scripts/gather.py` (gfi block) or `cvi/collect/github_rest.py`,
`scripts/derive.py` (gfi block), `tests/collect/test_labels.py`,
`tests/test_derive.py`.
**Acceptance.** Recorded-response test: label list with `good first issue` and
`E-easy` produces two queries and a union with no duplicates; live check on
`microsoft/vscode` and `pallets/flask` returns non-zero (paste counts);
derive on the sample: zero-fraction of 5.1 drops from 0.999 to the measured
value.
**Measure.** Before/after zero fraction on the sample; live counts for the two
repos.
**Forbidden.** Assuming label names; the list must come from the repo.
**Depends.** W0-1. **Size.** S.

### W1-2 Dependents collector (package level) and dedup
**Goal.** Dependents from deps.dev at package level (measure whether
`/v3alpha/systems/{sys}/packages/{name}` or per-version aggregation gives a
package-level count; fall back to ecosyste.ms
`packages.ecosyste.ms/api/v1/registries/<registry>/packages/<name>`
`dependent_packages_count` if not); map repo → package name via the registry
(the universe's `selected_by` and the repo manifest); dedup by root org, drop
same-org dependents; for `systems`, measure repology package-count feasibility
on 20 repos and report (no scoring use yet).
**Why.** F3 (100% missing; ECDF all-100). **Cites.** `02` §4 2.1, `03` §3.
**Owns.** `cvi/collect/depsdev.py` (new) or the depsdev block in
`gather.py`, `scripts/derive.py` (dependents block), tests.
**Acceptance.** Recorded-response tests for both sources; `pallets/flask`
returns a non-zero package-level count (paste); sample missing-rate for
dependents falls from 1.00 to the measured value; unit test that same-org
dependents are excluded.
**Measure.** Missing rate before/after; distribution quantiles per ecosystem;
repology feasibility (mappable / 20).
**Depends.** W0-1. **Size.** M.

### W1-3 Windowed PR collection with typed actors and review threads
**Goal.** GraphQL: PRs opened in the trailing 180 d (paginate, cap 500, set
`truncated`), fields: number, createdAt, closedAt, mergedAt, state,
`author { __typename login ... on User { createdAt } }`, authorAssociation,
`reviews(first: 10) { author{__typename login} state body createdAt
authorAssociation comments{totalCount} }`, `comments(first: 20) {
author{__typename login} authorAssociation bodyText createdAt }`,
`reviewThreads { totalCount }`. Derive per PR: first substantive human
response time (D-IN-2), substantive review flag (4.3), decided/merged flags.
Measure point cost.
**Why.** F4 (window), F12 (bot typing), 4.3 input. **Cites.** D-IN-1, D-IN-2,
D-IN-4, `02` §4 1.1/1.4/1.5/4.3.
**Owns.** `cvi/collect/github_graphql.py` (new), `scripts/derive.py` (PR
block), `tests/collect/test_pr_window.py`, `tests/test_derive.py`.
**Acceptance.** Recorded-response tests: a bot-typed comment is not a
response; a 30-char member comment is not; a 41-char member comment is;
pagination stops at the window boundary; `truncated` set at 500. Live on 5
repos of different sizes: paste PR counts, point cost, and latency sample
sizes.
**Measure.** Median external PRs with a measured latency on the sample
(before: 4); GraphQL points per repo; wall time per repo.
**Forbidden.** Fetching beyond 500 PRs; changing v1 bundles in place (write
v2 files under the scratch dir).
**Depends.** W0-3. **Size.** L.

### W1-4 Author history and newcomer cohorts
**Goal.** For each repo: decision cohort (PRs opened in [now−270 d, now−90 d])
from W1-3's window; newcomer cohort = external authors whose first PR in the
window falls in [now−365 d, now−180 d] (needs a 365-day window for this query
only, cap 500, or the `search` API), sample ≤ 20 authors; for each sampled
author fetch `search(type: ISSUE, query: "repo:o/n is:pr author:login",
first: 30)` to get prior merged count before the first PR, merge within 180 d,
second PR within 90 d; author account age and public event count (`user.
createdAt`, `contributionsCollection` or REST `users/{login}/events` count).
**Why.** F5. **Cites.** D-IN-3, D-IN-4, `02` §4 1.2/1.3/5.2/5.3.
**Owns.** `cvi/collect/github_graphql.py` (author section), `scripts/derive.py`
(cohort block), tests.
**Acceptance.** Recorded-response tests for each derived quantity with
hand-computed oracles; eligibility exclusion test (89-day-old account excluded,
90-day included); per-author 20% cap test; live on 3 repos with point cost.
**Measure.** Sample: fraction of repos with ≥ 5 cohort authors; MR_first
distribution before (= external merge rate) vs after; RR_90 distribution
(before: 1.0 at q25–q95); points per repo.
**Depends.** W1-3. **Size.** L.

### W1-5 Commits with email domains: elephant factor, bus factor, vitality
**Goal.** Collect 365 d of commits (cap 1,000) with `commit.author.email`,
`author.login`, `author.type`; derive org by domain (personal-domain list
frozen with constants; `users.noreply.github.com` → the login), EF (D-PARAM
3.2), bus factor, and vitality (≥ 1 human commit in 180 d) using typed actors.
**Why.** F9, D-GATE-4. **Cites.** `02` §4 3.2, D-IN-1, D-GATE-4.
**Owns.** commits collector block, `scripts/derive.py` (EF block), tests.
**Acceptance.** Oracle test: 60 commits from `a@corp.com`, 30 from
`b@corp.com`, 10 from `c@gmail.com` → EF 1; 40/30/30 across three domains →
EF 2; noreply handling test; live EF on `kubernetes/kubernetes` (expect ≥ 3)
and a single-vendor repo (paste).
**Measure.** EF distribution on the sample before/after; share of commits
with a resolvable domain.
**Depends.** W0-1. **Size.** M.

### W1-6 Governance evidence: legal IP, promotion ladder, AI posture
**Goal.** Collect check-run names / status contexts on the 20 most recent
merged PRs (DCO, EasyCLA, cla-assistant, google-cla…), `Signed-off-by` share
over window commits (from W1-5), presence and text of GOVERNANCE.md /
MAINTAINERS.md / CONTRIBUTING.md / SECURITY.md / AI policy files; association
transitions from the PR window (an author with earliest association external
and latest MEMBER/COLLABORATOR). Derive 3.1, 3.3, 4.2 per `02` §4 with NONE /
AD_HOC defaults and an `evidence` field per value.
**Why.** F7 (three constants; DCO default 99%). **Cites.** `02` §4 3.1, 3.3,
4.2; `03` §3.
**Owns.** check-runs/bodies collector block, `scripts/derive.py` (governance
block), tests.
**Acceptance.** Recorded-response oracles for each classification path; a
repo with no evidence derives `NONE`/`AD_HOC`/`AD_HOC` (fail-before shows DCO);
live: `torvalds/linux` → DCO (Signed-off-by share ≥ 0.5, paste the share);
`kubernetes/kubernetes` → FOUNDATION_CLA (EasyCLA check name, paste).
**Measure.** Distribution of 3.1 / 3.3 / 4.2 on the sample before/after.
**Depends.** W1-3 (associations), W1-5 (sign-off share). **Size.** L.

### W1-7 OpenSSF signals computed locally; scorecard subset; review/CI proxies
**Goal.** Derive the 10 Pike signals from collected data (age, months since
last human commit, contributor count from commits, org count from W1-5,
commit frequency, releases in 365 d via `releases`/tags, closed and updated
issues in 90 d via `search` counts, comments per issue from a 30-issue sample,
dependents from W1-2); scorecard: keep the live fetch, derive the 5-check
subset mean; proxy: code-review rate (merged PRs with ≥ 1 APPROVED review by a
non-author, from W1-3) and CI pass rate on the default branch (last 30
completed runs `conclusion`).
**Why.** F8 (signals 100% missing; scorecard 38% missing). **Cites.** `02` §4
2.2, 4.1; D-MISS-4.
**Owns.** releases/issues-count/runs collector blocks, `scripts/derive.py`
(signals + pipeline block), tests.
**Acceptance.** Oracle test of `calculate_pike_criticality` on hand-built
signals (exists; extend for the derived mapping); proxy oracle tests; a repo
without scorecard gets `method: proxy:review_ci` and a measured value
(fail-before shows 0.0 worst); live on 3 repos.
**Measure.** Missing rate of 2.2 and 4.1 on the sample before/after; share of
4.1 values by method.
**Depends.** W1-2, W1-3, W1-5. **Size.** L.

### W1-8 Downloads coverage
**Goal.** Measure what is reachable: pypistats throttled backfill runner
(resumable, nulls not done) with an ETA; probe BigQuery public dataset
availability (no credentials → report `UNDECIDED / U-ENV`); Go via deps.dev
dependents only (downloads unmeasured); Maven unmeasured; crates 90-day/3
normalisation kept; npm as is.
**Why.** F8 (77.8% missing). **Cites.** `02` §4 2.3, D-MISS-1.
**Owns.** `scripts/backfill_downloads.py` (new), registries collector block,
tests.
**Acceptance.** Runner resumes after kill (test with a fake server); rate
respected (≤ 4/min measured); on the sample's pypi slice the missing rate
falls (paste before/after and wall time).
**Measure.** Missing rate per ecosystem; projected full-universe backfill
time.
**Depends.** W0-1. **Size.** M.

### W1-9 Eligibility metadata and software classification
**Goal.** Collect `archived`, `mirror_url`, `fork`, `parent`, `license.
spdx_id`, PRs-enabled evidence (≥ 1 PR ever), tree paths at depth ≤ 2;
derive `non_software` (D-GATE-1 manifest list), `mirror`, `archived`, `fork`,
`unlicensed`, `off_platform` markers (D-GATE-6).
**Why.** F11; universe pollution. **Cites.** D-GATE-1, D-GATE-2, D-GATE-3,
D-GATE-6.
**Owns.** repo-object collector block, `scripts/derive.py` (identity block),
tests.
**Acceptance.** Recorded-response oracles per flag; live: `gcc-mirror/gcc` →
mirror; `torvalds/linux` → off_platform markers found (name the file);
an `awesome-*` repo → non_software; sample counts per flag.
**Measure.** Counts of non_software / mirror / archived / fork / unlicensed /
off_platform on the sample and, read-only, on all of `data/raw/github_meta`
if the fields exist there (they do not today; report).
**Depends.** W0-1. **Size.** M.

---

### W1-8 Universe entry: star-velocity filter applied and recorded (added 2026-09-13)
**Goal.** `scripts/build_universe.py` applies `filter_fraudulent_star_velocity`
(D-IN-5) at universe entry and records, per repo, whether the penalty fired
(`star_velocity_flag`, with the window and the velocity) in the universe
manifest; W2-9's mapping carries it into the bundle so the `star_burst`
detector (03 §6) has evidence. Found by the W3-2 review: the filter exists
but nothing calls it and nothing reports whether it fired.
**Why.** D-IN-5, `03` §4 (StarScout lockstep), `03` §6 `star_burst`.
**Owns.** `scripts/build_universe.py`, `cvi/collect/universe.py` (if split
out), the universe manifest schema, one `INPUT_MAPPINGS` row, tests.
**Acceptance.** A fixture repo with lockstep star bursts is flagged at entry
and carries the flag through derive; a normal repo carries `False`; the flag
is Unmeasured(collector_failure) when the star history fetch failed, never
silently `False`.
**Measure.** Flag count on the sample universe.
**Depends.** W1-2, W2-9. **Size.** S.

### W2-1 Core types: Measured/Unmeasured, bundle schema v2, frozen constants
**Goal.** `cvi/core/models.py` with the contracts in `04` §3; `FeatureBundle`
v2 load/validate/dump; `cvi/core/constants_v2.py` with every v2 constant,
`BENCHMARK_VERSION = "2.0.0-dev"`, `CONSTANTS_HASH`.
**Why.** Q1; F8; `04` §1. **Cites.** `04` §3, D-MISS-1, D-VER-2.
**Owns.** `cvi/core/__init__.py`, `cvi/core/models.py`,
`cvi/core/constants_v2.py`, `cvi/derive/bundle.py`, `tests/core/*`.
**Acceptance.** Validation rejects a bundle with a missing input key
(fail-before: silent default); hash test; import-graph test (`core` imports
nothing outside `core`).
**Measure.** Number of inputs in the schema (expect 18 parameters' inputs +
identity). **Depends.** W0-2. **Size.** M (the shared core; lands first).

### W2-2 Gates v2
**Goal.** D-GATE-1..6 and D-EVID-1 in `cvi/core/gates.py`, returning
`GateResult` with `UNDECIDED` gates recorded.
**Why.** F6, F11. **Cites.** `02` §2.
**Owns.** `cvi/core/gates.py`, `tests/core/test_gates.py`.
**Acceptance.** Oracles: 9 external PRs ≥ 14 d old → `UNDECIDED`, not
black hole; 10 with 6 unanswered → black hole; windowed denominator test
(merged PRs in window count in the denominator: 2 stale open + 28 merged →
not a black hole; fail-before with v1 semantics shows black hole); mirror,
archived, unlicensed, non_software, off-platform routing.
**Measure.** On the sample with v2 bundles: gated counts by reason vs v1's
black-hole count.
**Depends.** W2-1. **Size.** M.

### W2-3 Parameter functions v2 with monotonicity property tests
**Goal.** One function per parameter in `cvi/core/params/<pillar>.py` taking
bundle inputs and returning `ParamScore` (score, measured, reason, n, method);
curves per `02` §4; minimum-n rule; a property test that every curve is
monotone non-decreasing over a grid.
**Why.** D-PARAM-MONO; the Gaussian; F5. **Cites.** `02` §4, §5.
**Owns.** `cvi/core/params/*`, `tests/core/test_params_*.py`.
**Acceptance.** Hand-computed oracle per parameter at 3 points and both
boundaries; `insufficient_n` at n = min−1; monotonicity test fails if the old
Gaussian is registered (fail-before).
**Measure.** Parameter count 18; property test grid size.
**Depends.** W2-1. **Size.** L.

### W2-4 Mid-rank ECDF and frozen populations
**Goal.** `cvi/core/normalize.py` mid-rank ECDF; `cvi/snapshot/populations.py`
builds per-ecosystem populations from rated & measured repos, applies the
min-30 fallback, freezes to `populations.json` and the store table.
**Why.** F3. **Cites.** D-NORM-1..3.
**Owns.** `cvi/core/normalize.py`, `cvi/snapshot/populations.py`, tests.
**Acceptance.** All-tied population → 50.0 (fail-before: 100.0); mixed ties
oracle; fallback flag when N < 30; populations exclude gated repos (test).
**Measure.** Population sizes per ecosystem/param on the sample.
**Depends.** W2-1, W2-2. **Size.** S.

### W2-5 Aggregation, coverage, ceilings, composite, coordinates
**Goal.** `cvi/core/aggregate.py` (D-MISS-2/3, D-AGG-2), `cvi/core/matrix.py`
v2 (D-MAT-1/2), `cvi/core/engine.py` producing `ScoreRecord`.
**Why.** Q1; F8. **Cites.** `02` §5, §7, §8.
**Owns.** those modules, `tests/core/test_aggregate.py`,
`tests/core/test_engine_v2.py`.
**Acceptance.** Oracles: a pillar with two of three params measured
renormalises exactly; coverage arithmetic; `C = 0.59` → unrated, `0.60` →
ceiling B; tier ceiling applied (score 92, C 0.85 → A); unmeasured pillar
drops from coordinates; engine end-to-end on a hand-built bundle with every
number asserted.
**Measure.** On the sample: rated / unrated counts, coverage distribution,
tier distribution before ceilings and after.
**Depends.** W2-3, W2-4. **Size.** M.

### W2-6 Tie-breaks and rank
**Goal.** `cvi/core/tiebreak.py` builds the D-TIE-1 key; rank assignment and
`tie_group`.
**Why.** 118 distinct values / 981 rows. **Cites.** D-TIE-1/2.
**Owns.** `cvi/core/tiebreak.py`, tests.
**Acceptance.** Oracle on 8 rows covering every tie level, including two rows
identical except case in the name; stars never affect order (test with a
stars column present).
**Measure.** Tie-group size distribution on the sample.
**Depends.** W2-5. **Size.** S.

### W2-7 Persona gates and projections
**Goal.** `persona_gates` per D-PERS-2; persona composites via the same
renormalisation; `is_stage0_disqualified` untouched by personas.
**Why.** Q3. **Cites.** D-PERS-1/2.
**Owns.** `cvi/core/persona.py`, tests.
**Acceptance.** Oracles for each gate at its boundary; a gated persona view
keeps the balanced score (fail-before: v1 overwrote the flag).
**Measure.** Gate counts per persona on the sample.
**Depends.** W2-5. **Size.** S.

### W2-8 Constants freeze, anchors, sensitivity in CI
**Goal.** `BENCHMARK_VERSION = "2.0.0"`, `CONSTANTS_HASH` pinned by test;
anchors (D-VER-4) and sensitivity (D-AGG-3) run in CI on the fixture snapshot;
a `docs/v2/rfcs/README.md` stating the change procedure.
**Why.** D-VER-1/2, `03` §7. **Cites.** `02` §11, `03` §7.
**Owns.** `cvi/core/constants_v2.py` (hash line), `.github/workflows/ci.yml`,
`tests/fixtures/snapshot_50/` (from the sample), `tests/core/test_frozen.py`.
**Acceptance.** Changing any constant fails the hash test; anchors pass on the
fixture; τ ≥ 0.90 for every ±5 pp perturbation on the fixture (report the
numbers; if below, open an RFC rather than tuning).
**Measure.** τ per pillar; anchor results.
**Depends.** W2-6, W2-7, W0-5. **Size.** M.

---

### W3-1 Author eligibility, per-author caps, lockstep detector
**Goal.** D-IN-3 applied in derivation of every rate parameter;
`newcomer_burst` detector.
**Cites.** D-IN-3, `03` §4, §6. **Owns.** `cvi/core/hygiene.py`,
`cvi/verify/detectors.py` (part), tests.
**Acceptance.** Oracles: 6 first-timer PRs from one author → capped to 20% of
the denominator; 89-day account excluded; 5 accounts created within 7 days,
all merged → flag.
**Measure.** Share of external PRs excluded by eligibility on the sample.
**Depends.** W1-4, W2-1. **Size.** M.

### W3-2 Cross-snapshot detectors and suspicion flags
**Goal.** The `03` §6 table as functions over (current, previous) records;
flags stored in `suspicion`; a report command.
**Owns.** `cvi/verify/detectors.py`, `cvi/audit/detectors_report.py`, tests.
**Acceptance.** One oracle per detector at its threshold, both sides.
**Measure.** Flag counts on the sample (previous = v1 sample scored by v2).
**Depends.** W2-5. **Size.** M.

### W3-3 Live re-verification canary
**Goal.** `verify --live-sample`: random 5% + top 50 per ecosystem; compares
stars/forks (monotone), open-PR count, latest PR number; `source_drift`
holds the row; nightly systemd user timer (installed only after ACCEPT).
**Cites.** `03` §5. **Owns.** `cvi/verify/live_sample.py`, `scripts/canary.sh`,
tests (fake transport).
**Acceptance.** Drift injected in a recorded response → row held; no drift →
promoted; sample run live on 10 repos with counts.
**Measure.** Points consumed per run; drift count on the sample.
**Depends.** W0-3. **Size.** M.

### W3-4 Reproduce in CI
**Goal.** CI job: `cvi reproduce tests/fixtures/snapshot_50` → identical hash.
**Cites.** `03` §5, `04` §9. **Owns.** `.github/workflows/ci.yml`,
`cvi/snapshot/reproduce.py` (if changes needed), fixture.
**Acceptance.** Job green; a deliberately changed constant in a branch makes
it red (paste both).
**Depends.** W0-3, W2-8. **Size.** S.

---

### W4-1 Package move and dead-code deletion (strangler step)
**Goal.** Move to the `04` §2 layout with shims in `scripts/`; delete
`cvi/ingest.py` (after moving the limiter/ETag/circuit breaker to
`cvi/collect/ratelimit.py` with their tests), `cvi/benchmark.py`,
`cvi/benchmark_data.py` → `tests/fixtures/synthetic_catalog.py`,
`scripts/demo_app.py` → test helper.
**Why.** `04` §1. **Cites.** `04` §2, §10. **Owns.** the moved files, all
import sites, `pyproject.toml` packages list, README module map.
**Acceptance.** Reader sweep: every deleted symbol has zero readers (paste
grep); full suite once with failure-set identity; ruff clean; import-graph
test.
**Measure.** Lines deleted vs added (must be net negative).
**Depends.** W2-8. **Size.** L.

### W4-2 Store schema v2 and migrations
**Goal.** `04` §5 schema; numbered migrations; writer for rows/params/
populations/snapshots; v1 → v2 migration test on a copy of the v1 DB.
**Owns.** `cvi/store/*`, tests. **Acceptance.** Migration test both paths;
`RETURNING` absent (grep test); writer round-trip oracle.
**Measure.** Migration time on the v1 DB copy. **Depends.** W2-5. **Size.** L.

### W4-3 SQL persona projection and rank
**Goal.** `04` §6 query replaces the Python full scan; identical results to the
Python engine on the sample (test).
**Owns.** `cvi/store/reader.py`, `cvi/api.py` (call sites), tests.
**Acceptance.** Equality test of ranks and composites between SQL and engine
for every persona on the sample; p95 latency measured (report, no SLA edits).
**Measure.** p95 for leaderboard and reproject on 5k synthetic rows.
**Depends.** W4-2, W2-6. **Size.** M.

### W4-4 API v2 fields, evidence and snapshot endpoints
**Goal.** `04` §7 and the row contract in `06-UX-DESIGN.md` §4: every field the
UI consumes comes from the API (no client-side derivation). Endpoints:
leaderboard (rated, with rank/tie_group/coverage/tier_ceiling/persona
deltas), `not-ranked` (gated + unrated with reasons and copy keys), `matrix`,
`repos/{o}/{n}` (row + pillars + persona gates + suspicion), `repos/{o}/{n}/evidence`
(params long rows), `snapshot`, `reproject` (SQL path from W4-3).
**Owns.** `cvi/serve/api.py` (or `cvi/api.py` until W4-1 moves it), `tests/test_api.py`.
**Acceptance.** Contract tests for every field in `06` §4; the four UI states
have API-level fixtures (empty, error, partial); p95 latencies within `06` §7 on
a 10k-row synthetic store; no Python full scans (grep test).
**Depends.** W4-2, W4-3. **Size.** M.

### W4-5 UI v2 (rebuild to `06-UX-DESIGN.md`)
**Goal.** Rebuild the web UI to the brief: principles U-1..U-8, the seven
screens and their states (`06` §3), keymap and accessibility (`06` §5), the
module architecture (`06` §6: vanilla ES modules, zero CDN, zero build step,
windowed leaderboard, inline-SVG charts, token-based dual theme), performance
budgets (`06` §7). The v1 single-file page is replaced, not patched.
**Owns.** `web/` (new layout), `tests/test_webapp.py` (+ fixtures), the
static-mount lines in the API module.
**Acceptance.** The 10-item checklist in `06` §8, each as a test against a
served fixture store; every user-facing sentence lives in `web/copy.js`; the
gate/unrated reason map is one object; zero external requests (existing
test); the existing v1 UI tests are replaced with v2 equivalents, not deleted
without replacement.
**Depends.** W4-4. **Size.** L (may be split into W4-5a shell+leaderboard+
not-ranked, W4-5b drawer+evidence+matrix, W4-5c configure+compare+methodology
once W4-4 lands; each part keeps the checklist items it owns).

---

### W5-1 Forward validation experiment setup
**Goal.** Freeze snapshot `S0` after the full v2 run; write the pre-registered
analysis (`docs/v2/validation/PREREG.md`: labels, criteria from D-VAL-1);
`cvi audit forward --snapshot S0 --collect` scheduled at `S0 + 90 d` (timer
installed after ACCEPT).
**Cites.** D-VAL-1 (owner O-6). **Owns.** `cvi/audit/forward.py`, the
pre-registration doc, tests with synthetic outcomes.
**Acceptance.** Analysis oracle on synthetic outcomes (known Spearman);
collection dry-run on 5 repos.
**Depends.** W5-2 for the real snapshot; code can land before. **Size.** M.

### W5-2 Full-universe v2 run and release
**Goal.** Re-derive all repos from `data/raw` (plus new collectors' envelopes)
under a fresh data dir, verify, freeze, score, promote `data/cvi-v2.db`,
manifest published, methodology page (`docs/v2/METHODOLOGY.md`: parameters,
windows, curves, weights, tie-breakers, universe, hashes, sensitivity,
anchors, detectors), `serve` switched to v2.
**Owns.** run orchestration, the methodology page, `README.md` sections.
**Acceptance.** Anchors pass on the full snapshot; τ ≥ 0.90; reproduce
identical; coverage table published; corrected black-hole rate published
with method; zero fixture names in the DB (W0-4 guard).
**Measure.** Rated / gated / unrated counts; coverage distribution; tier
distribution; tie statistics; corrected black-hole rate; GitHub points and
wall time consumed.
**Depends.** all W1–W4, W3-3 canary passing once. **Size.** L (mostly
run time).

## 3. Dispatch template (copy for each packet)

```
Packet: <id>  Base: <sha>  Worktree: ~/Projects/cvi-wt-<id>  Scratch: ~/Projects/cvi-scratch/<id>
Read: docs/v2/00-README.md (non-negotiables), docs/v2/05-WORK-PLAN.md §0.4 and packet <id>,
      then only the cited D-ids/sections. Budget: <n> lines.
Deliver: .agents/<id>/handoff.md with diffstat, verbatim test output + exit codes,
         the packet's Measure lines with numbers, COULD NOT VERIFY list.
Rules: R-1..R-10. Never git stash / checkout --. Use `git show <base>:<path> > <path>` for fail-before.
Review: hub protocol items L0-L2 (+L3 for W4-*), lenses <list>; item records to .agents/<id>/review/.
```

## 4. Effort estimate (for sequencing, not a promise)

| Wave | Packets | Local-agent sessions (impl + review + reproduce) | Calendar |
|---|---|---|---|
| W0 | 5 | ~15 | week 1 |
| W1 | 9 | ~27 + live collection time (pypistats backfill ~12 h; author history ~3 h of GraphQL) | weeks 2–4 |
| W2 | 8 | ~24 | weeks 4–6 |
| W3 | 4 | ~12 | week 7 |
| W4 | 5 | ~15 | weeks 8–9 |
| W5 | 2 | ~6 + full run (~5 h GraphQL/REST at the current pace, plus downloads backfill) | weeks 10–11 |

Weeks 12–13 are reserve for rework rounds and the release.
