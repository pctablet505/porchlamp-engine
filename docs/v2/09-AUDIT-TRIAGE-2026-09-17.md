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

## Lead finding the audit missed: row 3.1 is unmeasured for the whole corpus

While verifying `A-01` I found a defect none of the eight auditors reported,
and it is the most expensive one on this page.

`legal_ip` (row 3.1, `PARAM_WEIGHTS = 0.35` — the heaviest parameter in the
governance pillar) is `Unmeasured(collector_failure)` for **every repository in
the real corpus**, because `cvi/derive/bundle.py:1983` maps it from a bare
`legal_ip` field while `scripts/derive.py:486` writes the classification to
`legal_ip_model`. Nothing writes a bare `legal_ip`; the `"NONE"` that appears
in every derived file is the v1 bundle's default-fill, stamped confidence
`"missing"`, which the resolver correctly refuses.

Measured at `96922c1` over the live corpus:

| Measurement | Result |
| --- | --- |
| real bundles loaded through `load_bundle` | 60 |
| row 3.1 `Measured` | **0** |
| row 3.1 `Unmeasured(collector_failure)` | **60** |
| `legal_ip_model` values actually present (200-file sample) | `DCO` 188, `CORP_CLA_UNILATERAL` 7, `FOUNDATION_CLA` 5 |
| `confidence["legal_ip"]` | `missing` in 199, absent in 1 |
| `confidence["legal_ip_model"]` | `derived` in 200 |

Cost: 0.35 of a governance pillar summing to 1.0, times
`PILLAR_WEIGHTS["governance"] = 0.20`, is **0.07 of composite coverage lost
unconditionally by every repo** against a 0.60 evidence floor — 11.7 % of the
evidence budget a repo needs to be rated at all, lost to a key name. This is
the most plausible single cause of the standing `rated 0` result on both the
fixture and every real build so far.

The fix cannot be made without also settling `A-01`, because wiring
`legal_ip_model` in means deciding what its two corporate-CLA values mean in
v2. **`docs/v2/rfcs/RFC-0005-legal-ip-wiring-and-cla-reciprocity.md`** is that
decision: keep the reciprocal/unilateral split (40.0/10.0, the values v1 has
always carried and three published docs describe) and delete the v2-only
`CORP_CLA = 25.0`, which prices a foundation-style reciprocal CLA and a
company-owned unilateral one identically. This is the first constants change in
this lineage that will move published scores, and RFC-0005 names the
before/after evidence the packet must paste.

## Batch 2 — scoring correctness (needs lead review, not a local model alone)

- **RFC-0005** (above): wire row 3.1 and settle the CLA vocabulary. Highest
  value item outstanding. Serialise it after `AUD-SNAPHASH`, which also
  regenerates the fixture.

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
