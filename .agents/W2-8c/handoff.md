# Handoff — W2-8c phase 1 (fixture refresh: live gather + derive on the landed collectors)

**STATUS: stopped by owner instruction before completion.** "Dataset gathering will be done later
by local agents; this session only builds." The lead killed `gather_50.py` (pid 183749) at 24/50
repos fully done + repo 25 partially done. This file is a runbook: exact commands to resume, state
at the stop, and the scoped derive/score/report steps (phase 1 items 3-5) so resuming is mechanical.

Worktree `/home/pctablet505/Projects/cvi-wt-W2-8c`, branch `W2-8c`, base `fb1f30b` (main tip at
dispatch; main has since advanced independently — that's fine, this branch was never rebased).
Scratch/data root: `~/Projects/cvi-scratch/W2-8c/data/` (**left untouched, as instructed** — do not
edit anything under it by hand; only `scripts/gather.py`/`scripts/derive.py` should ever write
there). Orchestration scripts (not part of the `porchlamp` package, not committed, already built and
smoke-tested on 4 real repos) live in `~/Projects/cvi-scratch/W2-8c/`: `gather_50.py`,
`measure_50.py`, `dr90_amended_cohort.py`, `dmiss5_criticality_proxy.py`.

## 1. Deliverable 1 — data-root switch (COMPLETE, committed)

Commit `d0231a9` on branch `W2-8c` (base `fb1f30b`). Diffstat:

```
 porchlamp/paths.py            | 39 ++++++++++++++++++++++++++++
 scripts/derive.py       |  3 ++-
 scripts/gather.py       |  3 ++-
 tests/test_data_root.py | 68 +++++++++++++++++++++++++++++++++++++++++++++++++
 4 files changed, 111 insertions(+), 2 deletions(-)
```

`porchlamp/paths.py::data_root()` resolves `PORCHLAMP_DATA_ROOT` if set, else the unchanged default
`<repo>/data`. `scripts/gather.py`'s `DATA` and `scripts/derive.py`'s `DATA_DIR` now read through
it. No collector touched. Verified at wrap-up:

```
$ env -u PYTEST_ADDOPTS PYTHONPATH=/home/pctablet505/Projects/cvi-wt-W2-8c /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -B -m pytest tests/ -p no:cacheprovider -n auto
1728 passed, 68 warnings in 96.69s (exit 0)

$ /home/pctablet505/Projects/contributor-value-index/.venv/bin/python -m ruff check porchlamp/paths.py scripts/gather.py scripts/derive.py tests/test_data_root.py
All checks passed! (exit 0)
```

## 2. Gather state at the stop (24/50 complete, repo 25 partial)

**Resume command (identical to the original — gather_repo() checkpoints per collector via file
existence, so this is safe to just re-run):**

```bash
PORCHLAMP_DATA_ROOT=/home/pctablet505/Projects/cvi-scratch/W2-8c/data \
PYTHONPATH=/home/pctablet505/Projects/cvi-wt-W2-8c \
/home/pctablet505/Projects/contributor-value-index/.venv/bin/python \
/home/pctablet505/Projects/cvi-scratch/W2-8c/gather_50.py
```

It reads the 50 repo keys straight from
`/home/pctablet505/Projects/cvi-wt-W2-8b/tests/fixtures/snapshot_50/manifest.json` (owner/name/
ecosystem), in that manifest's order, so it will re-attempt repos 1-24 (every collector for them
is already `_done()` and gets skipped instantly, no points spent), finish repo 25's remaining
collectors, then continue through repo 50. Hard caps in the script: stop at 3,000 GraphQL points or
2,000 REST-core calls attributed to *this process* (counted by monkeypatching `subprocess.run`,
immune to other agents sharing the same `gh` token — see the script's own docstring for why
`gh api rate_limit` is NOT used for this: it returns a stale `{used: 0}` in this sandbox regardless
of true consumption).

**Points spent so far (this process's own tally, exact call counts):** `rest_core=712`,
`graphql_calls=98`, `graphql_points~=147` — both far under the 2,000/3,000 caps; the run was
stopped by owner instruction, not by budget exhaustion.

**True shared-token readings taken during the run** (not `gh api rate_limit`, which is broken here
— see script docstring; these are real REST-header + GraphQL-`rateLimit`-field reads):

| checkpoint | core_used | core_remaining | graphql_used | graphql_remaining |
|---|---|---|---|---|
| before repo 1 | 3466 | 1534 | 2293 | 2707 |
| after repo 10 | 3852 | 1148 | 2548 | 2452 |
| after repo 20 | 776 | 4224 | 701 | 4299 |

(The jump between repo 10 and repo 20 is the shared token's hourly window resetting — other
concurrent agents' usage, not this run's; confirms the token really is shared and volatile.) No
"after" reading was taken — the process was killed externally before its final checkpoint.

**Per-collector completion counts, repos 1-24 (fully done):**

| collector | ok | skip | fail | pending | unmeasured |
|---|---|---|---|---|---|
| github_meta / pr_events / pr_reviews / contributors / gfi / actions_runs / tree / commits_180d / commits_365d / repo_object / releases / runs | 24 | - | - | - | - |
| depsdev | 23 | - | 1 | - | - |
| scorecard | 18 | - | 6 | - | - |
| pr_window | 22 | - | 2 | - | - |
| dependents | 8 | 13 | 3 | - | - |
| downloads | 4 | - | - | 7 | 13 |

`dependents: skip` (13) = go/maven/systems ecosystems, no registry mapping in
`porchlamp/collect/depsdev.py::SYSTEM_FOR_ECOSYSTEM` (only pypi/npm/crates map) — no envelope written, by
design, not a failure. `dependents: fail` (3) = pypi/npm/crates repos where deps.dev/ecosyste.ms
returned no count for that package (also no envelope; `collect_dependents`'s own documented "no
source carries a count" return, not an exception).

**Repo 25 (`be5invis/Sarasa-Gothic`, npm) partially gathered** before the kill: `actions_runs`,
`commits_180d`, `commits_365d`, `contributors`, `depsdev`, `gfi`, `github_meta`, `pr_events`,
`pr_reviews`, `repo_object`, `scorecard`, `tree` are present; `downloads`, `dependents`,
`pr_window`, `releases`, `issues_counts`, `runs` are NOT yet fetched for it. The resume command
above will finish it automatically (each collector's own `_done()` check skips what's present).

**Repos 26-50: not started.**

## 3. Derive state — 4/50 done (smoke-test validation only, real data, keep them)

`scripts/derive.py::derive_repo` (v1 format) has been run for exactly 4 repos as part of validating
the analysis scripts below (`~/Projects/cvi-scratch/W2-8c/data/derived/`):
`0voice__interview_internal_reference.json`, `a-h__templ.json`, `abel533__Mapper.json`,
`apache__thrift.json`. These are real derives against real gathered data, not placeholders — no
need to redo them. The other 20 gathered repos have raw data but no `derived/` doc yet.

Two real findings already confirmed from these 4 (see `.agents/W2-8c/` git history / this file's
section 5 for the full mechanism): `dependents_deduped` resolves to `Unmeasured(third_party_gap)`
for ALL FOUR (including the two ecosystems, go and maven, that `collect_dependents` "skips" —
the fallback confidence path lands on `"missing"`, not a hole in the schema), and
`openssf_criticality` is `Unmeasured(third_party_gap)` for all four for the same underlying reason
(`derive_pike_signals` hard-requires the dependents envelope as one of its five sources).

## 4. Phase 1 items 3-5 (derive + the 18-input table + coverage + handoff) — SCOPED, not yet run to completion

Once gather finishes all 50 (section 2's resume command) and derive is run for the remaining 46
repos (loop `derive.derive_repo(owner, name, Ecosystem(eco), data_dir=NEW_DATA, fetch=True, now=...)`
per repo, exactly as `measure_50.py::derive_new` already does — just run `measure_50.py`, it drives
derive itself), the full report is one command:

```bash
PYTHONPATH=/home/pctablet505/Projects/cvi-wt-W2-8c \
/home/pctablet505/Projects/contributor-value-index/.venv/bin/python \
/home/pctablet505/Projects/cvi-scratch/W2-8c/measure_50.py
```

This produces, printed and written to `~/Projects/cvi-scratch/W2-8c/measure_results.json`:

- The 18-input (`constants_v2.PARAM_INPUT_IDS`) Measured/Unmeasured(reason) table, NEW (this
  gather) vs OLD (`~/Projects/cvi-scratch/sample-200`, the same 50 repo keys, v1-format, predates
  the W1 collectors).
- **Column 1** — coverage histogram (0.1 bins), rated/gated/unrated counts + reasons, **as data
  derives today** (no amendments), scored via `porchlamp.core.pipeline.score_snapshot` loaded read-only
  from `/home/pctablet505/Projects/cvi-wt-W2-8b` (verified byte-identical to main on every file it
  shares except `porchlamp/audit/anchors.py`, `porchlamp/audit/sensitivity.py` — unused by this path — and
  `pipeline.py` itself, which is new: `git diff --stat 1f8e428 fb1f30b -- . ':!docs'` is empty).
- **Column 2** — same, **with DR_90 under the amended cohort** (see section 5.1): evidence only,
  computed by `dr90_amended_cohort.py` directly from raw `pr_window` envelopes, `porchlamp/derive/
  bundle.py::INPUT_MAPPINGS` is NOT touched.
- **Column 3** — same, **with D-MISS-5 + the criticality proxy, on top of column 2** (section 5.2):
  evidence only, `dmiss5_criticality_proxy.py`, via a monkeypatch of
  `porchlamp.core.aggregate.aggregate_pillars` scoped to one `score_snapshot` call (restored immediately
  after) — nothing in `porchlamp/` is left modified.
- Per-ecosystem breakdown (go/systems/maven/pypi/npm/crates) of rated/gated/unrated, in all three
  columns (`by_ecosystem` in each column's result dict).

All three scripts are already written and were smoke-tested end to end (compiled, and actually run)
against the 4 real derived repos in section 3 — e.g. `apache/thrift` (systems) moved from coverage
bucket `[0.2,0.3)` to `[0.3,0.4)` between column 1 and column 3, confirming the mechanism has a
real, measurable, positive, correctly-signed effect. Re-running `measure_50.py` once all 50 are
gathered+derived needs no code changes, only more data.

## 5. Two structural findings raised mid-task, with the coordinator's ruling and packet ids

### 5.1 DR_90 (`dr90_decision_rate`) is structurally unmapped today — ruling: amend the cohort, packet W2-12

`porchlamp/derive/bundle.py::INPUT_MAPPINGS["dr90_decision_rate"]` is a constant `_unmapped(...)`
resolver (always `Unmeasured(collector_failure)`) because D-IN-4's original cohort definition
(`docs/v2/02-RANKING-DESIGN.md` row 1.2: PRs opened in `[now-270d, now-90d]`) falls outside the
landed W1-3 `pr_window` collector's trailing-180-day window — no amount of gathering fixes it.
**Ruling received:** amend the cohort to `[as_of-180d, as_of-90d]` (the older half of the W1-3
window, entirely inside data already collected). **Packet W2-12** wires this into `INPUT_MAPPINGS`
for real; not touched in this packet. `dr90_amended_cohort.py` computes it as evidence directly from
raw `pr_window` envelopes (PRs opened in that window, external, decided — closed or merged — within
90 d of opening; min n = 10 per the design doc's row 1.2), and `measure_50.py` uses it to build
column 2/3's patched bundles.

### 5.2 `dependents_deduped`/`openssf_criticality` are coupled and capped by ecosystem — ruling: D-MISS-5 + criticality proxy, packets W1-2f / W2-13

`porchlamp/collect/depsdev.py::SYSTEM_FOR_ECOSYSTEM` maps only pypi/npm/crates to a deps.dev system id;
go/maven/systems get `"skip"` from `collect_dependents` (no envelope, by design — no derivable
package name). Separately, `porchlamp/collect/pike_signals.py::derive_pike_signals` hard-requires the
dependents envelope (`dep.get("deduped_count")`) as one of its five sources for ALL TEN Pike
signals — so a repo with no dependents envelope can never get `openssf_criticality` either, not
just `dependents_deduped`. In the 50-repo fixture (go=12, systems=10, maven=8, pypi=8, npm=6,
crates=6), that's 30/50 (60%) structurally excluded from both inputs regardless of gather effort.
**Ruling received:**
- **D-MISS-5** (a new missingness class, hashed to a design-level rule keyed on a universe-entry
  fact): `Unmeasured(reason="not_applicable")` drops a parameter's weight from BOTH sides of its
  pillar's coverage ratio (today's `porchlamp.core.aggregate.aggregate_pillars` only drops it from the
  numerator). Today's one hashed rule: `ecosystem == "systems"` → `dependents_deduped` is
  `not_applicable`. go/maven get a **real** fix instead, in a new packet **W1-2f** (deps.dev module
  path for go, `groupId:artifactId` from the root `pom.xml` for maven) — not implemented here (real
  collector work, out of this packet's scope); their `dependents_deduped` stays whatever it derives
  today (observed: `third_party_gap`, same fallback-confidence path as pypi/npm) until W1-2f lands.
- **Criticality proxy** (packet **W2-13**, method string `"proxy:no_dependents_signal"`): when
  dependents is `not_applicable` or `third_party_gap` (never `collector_failure` — a genuine fetch
  fault still voids the whole ten-signal set), recompute criticality from the other nine signals,
  weights renormalised over them. `dmiss5_criticality_proxy.py` reuses
  `porchlamp.core.openssf.calculate_pike_criticality` UNCHANGED (it already accepts optional
  `weights`/`thresholds` dicts and renormalises over whichever keys `weights` carries) — no
  reimplementation of the formula. **Caveat, recorded honestly:** `derive_pike_signals` gates
  per-*source* (github_meta/commits_365d/releases/issues_counts), not per-signal, so this script
  can only observe the "9 of 9 present" case, a subset of the ruling's ">= 8 of 9" — the
  finer-grained partial case needs W2-13's real implementation to see. Neither `aggregate_pillars`
  nor `pike_signals.py`/`openssf.py` is modified; the D-MISS-5 behaviour is reproduced by
  monkeypatching `porchlamp.core.aggregate.aggregate_pillars` for the duration of one `score_snapshot`
  call only (see `dmiss5_criticality_proxy.py::make_not_applicable_aware_aggregate_pillars`),
  restored immediately after.

## 6. COULD NOT VERIFY / observations for the lead

- `porchlamp/collect/labels.py::collect_newcomer_issues` (via `gather.py`'s `_gh` fetch),
  `porchlamp/collect/repo_object.py::collect_repo_object`, and
  `porchlamp/collect/github_graphql.py::fetch_pr_window` have no internal inter-call pacing (no
  `time.sleep` between successive `gh api` calls within one repo's own collection) — unlike the
  `rest` loop's `SLEEP_REST`, `pike_signals.py`'s `SLEEP_CORE_SECONDS`/`SLEEP_SEARCH_SECONDS`, and
  the commit-paging closures' sleeps. Not fixed here (collector logic, out of this packet's
  ownership); this run paced only between repos (1 s) plus each collector's own existing internal
  sleeps. No rate-limit errors were observed attributable to this gap in the 24-repo run, but it is
  a real gap for a future packet.
- `gh api rate_limit` returns a stale `{used: 0, remaining: limit}` in this sandbox regardless of
  true consumption (confirmed against real REST headers and the GraphQL `rateLimit` field) — a
  project-wide hazard for any packet budgeting against it, not just this one.
- Repo 25 (`be5invis/Sarasa-Gothic`) is mid-gather (section 2) — resume, don't re-derive it from a
  partial raw cache.
