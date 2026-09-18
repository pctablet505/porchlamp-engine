# 09 — Deep-audit triage, 2026-09-17

Lead triage of `AUDIT-2026-09-17-DEEP-REVIEW.md` (8 parallel auditors, 18
Blockers, 34 Warnings, 12 Considers, 63 distinct finding ids).

## Verdict on the audit itself

**Sound. Act on it.** I independently re-verified twelve findings against the
current tree before scheduling any work, reading the cited code myself:

| Finding | Claim | My check |
| --- | --- | --- |
| `H1` | `copy.EVIDENCE_RETRY_LABEL` is read but never exported | CONFIRMED — `grep -rn` over `web/v2/` returns exactly one hit, the read at `views/drawer.js:351`; no definition |
| `H3` | the UI renames tier `S+` to `S` | CONFIRMED — `copy.js::tierLabel` has `tier === "S+" ? "S" : tier`, while `constants.py` `TIER_CUTOFFS` emits `S+` and `TIER_ORDER` lists it as the top tier |
| `H2` | the JS integrity test cannot see a phantom `copy.*` name | CONFIRMED — the namespace branch asserts only `assert target_exports` |
| `H4` | the JS suite skips itself silently | CONFIRMED — `@pytest.mark.skipif(shutil.which("node") is None, ...)` |
| `G12` | CI runs only when a person clicks | CONFIRMED — `on: workflow_dispatch:` and nothing else |
| `F-02` | the release gate always exits 0 | CONFIRMED — `main` ends `return 0` on both output paths regardless of `report.all_pass` |
| `B1` | `write_envelope` is not atomic | CONFIRMED — `target.write_text(...)`, while `scripts/backfill_downloads.py::_write_envelope` shows the `.tmp` + `os.replace` pattern the repo already knows |
| `B2` | a failed keyless fetch is envelopes as a 200 | CONFIRMED — `_get_json` swallows every exception into `None`, the caller writes the envelope anyway, and `write_envelope`'s docstring asserts the opposite ("the write path runs only after a transport that raises") |
| `G1` | `/reproject` pages 20 rows | CONFIRMED — `page_size: int = Query(DEFAULT_PAGE_SIZE)` = 20, and `ReprojectResponse` carries no paging field |
| `G4` | `/not-ranked` discards the store's counts | CONFIRMED — `rows, _counts_by_reason, _total = queries.list_not_ranked(...)` then recounts with `len()` over a page capped at `MAX_LIMIT` |
| `E-01` | the Merkle root omits `populations.json` | CONFIRMED — `merkle_root(sorted(raw_hashes.values()))`, and `raw_hashes` is per-bundle only |
| `E-02` | two `constants_hash` definitions under one field name | CONFIRMED **numerically**: `manifest.constants_hash()` = `2282711184d2…`, `core.CONSTANTS_HASH` = `748be6e459b0…` |

Also confirmed: `D-01` (the coverage denominator uses the full weight sum while
`aggregate.py` drops a `not_applicable` pillar from both sides), `G2`
(`axis_coverage={}` hardcoded), `G3` (`excluded_count` incremented on top of the
store's own), `A-01`/`A-02` (`cvi/pillars/governance.py` documents
`CORP_CLA_RECIPROCAL`/`CORP_CLA_UNILATERAL` while `constants.py` knows only
`CORP_CLA`), and `B9` — which I had already found independently while writing
`08`: `cvi/collect/ratelimit.py` has no importer outside its own tests.

**The audit also corrected me.** Finding `B6` showed `08-SUSTAINABLE-COLLECTION.md`
§1 understated governance: it spends **two** REST calls (`statuses` +
`check-runs`) per distinct head SHA across `MERGED_PR_CAP = 20` merged PRs, so
≈45 REST/repo ≈ 259k calls ≈ 52 h — by itself more than the figure my table
published for the entire sweep. Corrected in `bff292c`, together with every
total that inherited it (sweep ≈65–70 h; interim weekly ≈6–7 h) and a new
**D-COL-12** that caps the SHA probe at the 5 newest and makes a thin CLA
sample `Unmeasured(insufficient_n)` rather than a guess.

### Caveats to carry

- Slice B read at `80fb117`, **169 commits behind** current `main`; its line
  numbers predate the `--collectors` change in `407a273`. The defects I
  re-verified above hold at HEAD, but re-read before quoting a line number.
- The auditors ran against a tree a data-collection agent was concurrently
  using; the report says HEAD moved under them and one marker was clobbered.
- No network and no `sqlite3` CLI were available, so every live rate-limit
  claim and every v2-store finding was checked against the in-memory schema,
  not against a served database. `data/cvi.db` is still **v1 only** — no v2
  migration has ever run against a real file.

## Sequencing gate (from the audit, adopted)

`B1` (atomic write) and `B2` (never envelope a failed fetch) land **before**
`B3`, `B4`, `B6`, and before the W6 ledger redesign. Otherwise the ledgers
faithfully record corrupt envelopes as collected and the redesign inherits the
bug it exists to detect.

## Batch 1 — in flight (6 local agents, one worktree each, disjoint files)

Dispatched to Kimi CLI on the local Qwen Flash-Next model, six in parallel.
Briefs at `~/Projects/cvi-scratch/audit-fix-2026-09-17/briefs/`, reports at
`.../logs/`.

| Branch | Findings | Files owned |
| --- | --- | --- |
| `AUD-B1B2` | `B1`, `B2` (+ closes the cause of `C-02`) | `cvi/collect/envelope.py`, `scripts/gather.py` |
| `AUD-WEB` | `H1`, `H3`, `H6` | `web/v2/{copy.js,views/drawer.js,components/badge.js,styles/components.css}` |
| `AUD-GATE` | `H2`, `H4`, `G12`, `G13`, `F-02` | `.github/workflows/ci.yml`, `tests/test_ui_{node_suite,js_integrity}.py`, `cvi/audit/sensitivity.py` |
| `AUD-SERVE` | `G1`, `G4`, `G3`, `G2` | `cvi/serve/{app.py,contract.py}` |
| `AUD-REPROJECT` | `D-01`, `D-03` | `cvi/store/queries.py` |
| `AUD-SNAPHASH` | `E-01`, `E-02` | `cvi/snapshot/{build_cmd.py,manifest.py}` |

Batch-1 rules given to every agent: own only the listed files; write the
failing test first and paste its real failure; never weaken an assertion or add
a skip; report honestly what you could not do. The lead verifies each report
against the tree before anything is committed to `main`.

## Lead finding the audit missed: one collector gates 29 % of the evidence budget

While verifying `A-01` I ran a census of all 18 v2 inputs against the live
corpus. **10 of 18 are never measured on real data**, and the largest
recoverable cause is a single collector that has never run.

Census over 120 real bundles loaded through `cvi.derive.bundle.load_bundle`
at `cd8df60`:

| input | weight | measured | dominant unmeasured reason |
| --- | --- | --- | --- |
| `elephant_factor` | 0.35 | **100 %** | — |
| `newcomer_issue_supply` | 0.35 | **100 %** | — |
| `substantive_review_rate` | 0.30 | 52 % | `insufficient_n` |
| `ttm_p90_hours` | 0.10 | 47 % | `insufficient_n` |
| `dr90_decision_rate` | 0.25 | 42 % | `insufficient_n` |
| `pipeline_defenses` | 0.40 | 40 % | `third_party_gap` |
| `ttfhr_hours` | 0.30 | 35 % | `insufficient_n` |
| `downloads_monthly` | 0.25 | 12 % | `third_party_gap` (pypi backfill pending) |
| `legal_ip` | 0.35 | **0 %** | `collector_failure` |
| `promotion_ladder` | 0.30 | **0 %** | `collector_failure` |
| `ai_posture` | 0.30 | **0 %** | `collector_failure` |
| `dependents_deduped` | 0.40 | **0 %** | `third_party_gap` (registry map) |
| `openssf_criticality` | 0.35 | **0 %** | `third_party_gap` |
| `mr_first_rate`, `gr14_ghost_rate`, `newcomer_conversion`, `rr90_return_rate`, `dx` | | **0 %** | `collector_failure` (author history / unmapped by design) |

The three `0 %` rows that are cheapest to fix — `legal_ip`, `promotion_ladder`,
`ai_posture` — all come from one function,
`cvi.collect.governance.derive_governance_fields`, which returns `"missing"`
for every one of them when the `governance` envelope is absent. It is absent
for the whole universe: `ls data/raw/governance | wc -l` is **0**.

| Row | Share of its pillar | Pillar weight | Composite coverage |
| --- | --- | --- | --- |
| `legal_ip` + `promotion_ladder` | 0.65 of governance | 0.20 | 0.1300 |
| `ai_posture` | 0.30 of rigor | 0.15 | 0.0450 |
| **total gated on the `governance` collector** | | | **0.1750** |

Against a 0.60 evidence floor that is **29.2 % of the evidence a repository
needs to be rated at all**, and it leaves the governance pillar measured at
35 % (`elephant_factor` alone). It is the most plausible single cause of the
standing `rated 0` result on both the fixture and every real build.

**This was a planning error of mine.** `07-BFS-COLLECTION-TASK.md` scheduled
`governance` in L3, third of four layers, because I ranked layers by cost
rather than by coverage unlocked per call. Governance is expensive per repo but
it is the second-largest unlock in the whole plan. It moves to **L1 beside
`pr_window`**, with the D-COL-12 sampling cap applied so the cost is ~15 REST
per repo rather than ~45.

**And a correction.** My first write-up of this — published in
`RFC-0005` at `cd8df60` — said the cause was a key name: that
`cvi/derive/bundle.py` read a bare `legal_ip` while `scripts/derive.py` wrote
only `legal_ip_model`. That mechanism was wrong, and I published it before
finishing the trace. `scripts/derive.py:617-634` does write the bare `legal_ip`
under the correct v2 name. The measurements were right; the explanation was
not. RFC-0005 has been rewritten to carry the correction in its own text and
renamed to `RFC-0005-cla-reciprocity-vocabulary.md`, since the vocabulary
decision (`A-01`) is what actually needs deciding.

Recorded but not decided: `scripts/derive.py:486-497` computes `ai_posture` and
`promotion_ladder` from cached policy-file bodies by regex, and the governance
block then overwrites both — including when it has no evidence and writes
`"missing"`. Preferring evidence over a heuristic is right; discarding the
heuristic when there is no evidence is worse than keeping it as a declared
`proxy:` method. Its own RFC.

## Batch 2 — scoring correctness (needs lead review, not a local model alone)

- **RFC-0005** (above): settle the CLA vocabulary before the `governance`
  gather starts emitting real regimes. Serialise after `AUD-SNAPHASH`, which
  also regenerates the fixture.

- `A-01`/`A-02` legal-IP regime defined three ways; a spec-published level
  raises `ValueError` and legacy rows are silently re-scored. Touches the
  frozen constants, so it moves `CONSTANTS_HASH` and the fixture — an RFC
  under `docs/v2/rfcs/` first.
- `C-05`/`E-03` the loader and the certification chain read the wall clock, so
  the same `derived/*.json` scores differently across a window boundary and
  `gr14_ghost_rate`/`dx` are permanently unmeasurable.
- `F-03`/`F-05` forward evaluation coerces a missing outcome to `0.0` and
  computes "rated universe" two ways — both change published statistics.
- `A-03` the methodology advertises a per-persona score the engine cannot
  produce.
- `B:agents-file` any repo carrying `AGENTS.md`/`CLAUDE.md` classifies as
  `REGULATED`, and the module concedes `PROSCRIBED` can never fire — so the
  `ai_assisted` hard-ban gate is dead by construction. This is the already
  deferred W1-6b, now with evidence.

## Batch 3 — collection hardening (after batch 1's gate, before W6 ledgers)

`B3` partial-GraphQL salvage, `B4` uncapped tree frontier with no `truncated`
flag, `B5` one unguarded exception ends a multi-hour sweep, `B6` governance
cost and evidence (implements **D-COL-12**), `B7` star history has no live
transport, `B8` the universe's selection column mixes stars with downloads,
`B9` wire `ratelimit.py` into a real transport (this is W6-1).

## Batch 4 — store and ops

`D-02` no WAL or `busy_timeout` and the v2 write path mutates the served
database in place, `G11` the shipped tree cannot be served at all, `G5` the
guard verifies a snapshot the server may not serve, `G7` a production 500
leaves no trace, `G6` no `Cache-Control`/`ETag` on immutable content-hashed
data, `E-01`'s sibling checks.

## Not scheduled

The `Nit` tail, and `F-04`/`F-06`/`F-07` (forward/anchor reporting shape) until
there is real rated data to run them against — they are unfalsifiable today.

## Batch 1 outcome (2026-09-17/18)

Seven of eight packets landed. Every report was verified against the tree
before anything reached `main`: I read each diff, re-ran the tests myself, and
in three cases the verification changed the result.

| Packet | Landed | Verification note |
| --- | --- | --- |
| `AUD-WEB` | `07c284f` | Correct. Its brief (mine) made it add a duplicate `Retry` constant; I collapsed that to the existing `RETRY_LABEL`, which `drawer.js` already used for its other retry button. |
| `AUD-GATE` | `b7085ad` | Correct. Its H2 test caught the phantom name with a precise message. |
| `AUD-B1B2` | `478a712` | Correct, and it corrected a false docstring. |
| `AUD-REPOOBJ` | `69462ff` | Correct. Declared the `rc != 0` trade-off it had to make. |
| `AUD-COMMITS` | `12ffe8e` | Correct. Checked its caller rather than assuming. |
| `AUD-SNAPHASH` | `67ec1a0` + `d5b4206` | Half the fix. `scripts/build_fixture_snapshot.py` is a second implementation of the manifest write and still omitted populations; the packet did not own that file and correctly left it. I closed it, regenerated the fixture (`merkle_root` `cab43b15…` → `944101ba…`, `records_hash` unchanged). |
| `AUD-SERVE` | `545bc8d` | Correct. |
| `AUD-REPROJECT` | `d9509c2` | D-03 fixed; D-01 **pinned, not fixed** — see below. |
| `AUD-STOREWAL` | **not landed** | Correctly refused. See below. |

`main` at `d9509c2`: 2,702 passed, 1 skipped, 1 xfailed, ruff clean. The one
failure, `tests/test_backfill_downloads.py::test_nulls_stay_undone_and_are_refetched`,
is pre-existing and intermittent on `main` (measured 1–2 failures in 4 runs
before any of this work). I attempted a fix, found it traded that failure for a
new one in `test_runner_resumes_after_kill`, and reverted rather than land a
net-negative change. The race is in the test's own `ThreadingHTTPServer`, which
appends to `seen` after flushing the response.

### Two findings that came out of verification, not from the audit

**The JavaScript suite had never run on this host.** `node` is installed in
`~/.local/opt` and has never been on `PATH`, so `tests/test_ui_node_suite.py`
skipped silently — which is finding `H4` exactly. With the skip made loud and
`find_node()` looking past `PATH`, the suite ran and had **two real failures**,
both of which pre-date the 2026-09-15/16 agent commits: they were shipped at
`1dff982`, in my own lead session, and hidden by that skip.

- U-19 ("a one-row data change patches exactly one `<tr>`") reported three.
  `patch.js` calls `updateItem` only on nodes that survive a patch, never on
  ones it just created, and `createSpacerElement` did not set its own height,
  so both spacers took their first height on the *next* patch. Measured after a
  warm-up patch the same scenario touches exactly one row — the windowing was
  always right. Fixed at the create path.
- The keymap assertion reported that `/` had no handler when `KEY_ACTIONS` has
  always had one: the JS expander split `entry.keys` on `/`, turning the bare
  `/` shortcut into `["", ""]`. Its Python twin already required the
  surrounding spaces. **Had I briefed this to an agent as "fix the missing
  handler", it would have changed working code.**

Both were found only because the gate was switched on. That is the argument for
`G12`/`G13`/`H4` in one line.

**`snapshot_dump_hash` cannot survive WAL, and that blocks `D-02`.**
`AUD-STOREWAL` enabled WAL and `busy_timeout` as instructed, then reported that
the instructed suite was **not** green: 27 failures and 38 errors, every one the
same exception, `no such table: params`, from
`cvi/store/writer.py::snapshot_dump_hash`. That function copies the database
with `shutil.copyfile` — the main `.db` file only. In rollback-journal mode all
committed data is in that file; in WAL mode committed-but-un-checkpointed data
lives in the `-wal` sidecar, so the copy has no tables at all.

This is a latent Blocker in its own right, independent of WAL: copying a live
SQLite file with `shutil.copyfile` is not a safe way to obtain a consistent
snapshot for hashing. The fix is to use SQLite's own backup API or
`VACUUM INTO`, which produce a consistent single-file copy in any journal mode.
**`D-02` is therefore blocked on that, and the agent was right to stop rather
than force the suite green.** Queued as the next packet.

### `D-01` is pinned rather than fixed, deliberately

`reproject`'s coverage denominator uses the full weight sum while
`cvi.core.aggregate` drops a `not_applicable` pillar from both sides (D-MISS-5),
so one inapplicable pillar moves a repo from coverage 1.0/tier S+ to 0.8/tier A
on the custom-weights endpoint — and since coverage gates rating, it can vanish
from that leaderboard. It cannot be fixed in SQL today: `repositories` has no
per-pillar applicability column, so an entirely-`not_applicable` pillar is
indistinguishable from a fully-unmeasured one. The divergence is pinned by a
**strict** xfail naming the column that would be needed, so it converts to a
passing test the moment that lands. Inventing a proxy that is right on the
fixture and wrong in general was the available alternative and was refused.

### `D-02` landed after its blocker was cleared

`AUD-DUMPHASH` replaced `snapshot_dump_hash`'s `shutil.copyfile` with SQLite's
online backup API (`sqlite3.Connection.backup`), which produces a consistent
single-file copy in any journal mode. `VACUUM INTO` was the alternative and was
rejected because it compacts, and the integrity hash must not depend on storage
layout.

The hash does not move, verified three ways rather than claimed:

1. The packet pinned `a0dba53d…`, measured on the pre-fix code. I re-ran that
   single test against pre-fix `main` and it passes there too, so the pin is a
   real regression guard and not a value recorded after the change.
2. `canonical_dump_hash` hashes `canonical_dump`'s table CONTENT, not file
   bytes, so WAL's byte-level churn cannot reach it. Read, not assumed.
3. End to end: `python -m cvi.snapshot store tests/fixtures/snapshot_50`
   against a fresh database now reports
   `snapshot_dump_hash 43484221eac344668f3ed4e2f27fc3c23621d21c5065c423c4c09cd459f433f0`
   — the same value this fixture produced before either change, with
   `PRAGMA journal_mode` confirming `wal` and the v2 served-DB guard returning
   no refusal reasons.

`AUD-STOREWAL` then landed unchanged from what the agent wrote. **All nine
batch-1 packets are in.** `main` at `08032e3`: **2,712 passed, 1 skipped,
1 xfailed, 0 failed**, ruff clean — the first fully green full-suite run of this
session, including the JavaScript layer that had never executed on this host.

## D-COL-12 landed ahead of the layer that needed it (2026-09-18)

The live collection run reaches L3 — the layer carrying `governance` — in about
three hours, and `governance` was the single most expensive thing in the plan.
The local model server went down mid-dispatch (`503 … main slot: empty`, a
clean stop with the GPU fully released, no Xid), so I implemented D-COL-12
myself rather than load a 94 GB model unasked. The data collection itself never
needed the model: it is pure GitHub API work.

Measured with a counting fake transport on 20 merged PRs with 20 distinct head
SHAs, not estimated:

| | total calls | probe calls |
| --- | --- | --- |
| before | 41 | 40 |
| after | 11 | 10 |

At ~45 core calls/repo that was ~259,000 calls and ~52 h of a 5,000/h budget;
at ~15 it is ~86,000 and ~17 h, taking L3 from roughly 63 h to 28 h.

Two consequences of capping, both handled rather than ignored:

- **"Newest" had to become a fact.** The pulls listing is sorted by *creation*
  date and a PR opened earlier can merge later, so the probe now sorts
  explicitly by `merged_at` descending.
- **A thin sample cannot support a negative finding.** Capping makes it likelier
  that every probed merge is a maintainer's own PR, which carries no CLA check.
  A *positive* signal still stands on one probe — an EasyCLA context proves a
  CLA exists, and a DCO sign-off share comes from `commits_365d`, not from the
  probe at all — but the `no_evidence → NONE` default now needs 3 distinct
  probed SHAs, else row 3.1 is an evidence gap. Evidence of absence and absence
  of evidence are different claims and are now scored differently.

The equivalence argument is asserted, not assumed: the existing envelope test
now checks that the capped sample reaches the *same* legal-IP verdict
(`FOUNDATION_CLA` via `foundation_cla_check`) as the full uncapped set, and its
fixture was extended so both evidence kinds still fall inside the probed
window rather than losing that coverage.

The three p95 timing tests fail while this is written. That is host load from
**another project** — AlgoTrading's pytest at 213 % and 186 % CPU on a 32-core
box, load average 90.5 — not a regression: this change touches only
`cvi/collect/governance.py`, while those tests import
`cvi.core.params.governance` and `cvi.pillars.governance`, different modules
entirely.
