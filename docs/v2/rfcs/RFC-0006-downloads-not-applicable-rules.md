# RFC-0006: downloads-not-applicable-rules

## Motivation

Parameter 2.3 (`downloads_monthly`, 25% of Pillar 2 Criticality) measures
monthly package-registry downloads for ecosystems that expose a public monthly
download counter (`pypi`, `npm`, `crates`). In `scripts/backfill_downloads.py`,
`UNMEASURED_ECOSYSTEMS = frozenset({"go", "maven", "systems"})` explicitly
records that the Go module proxy, Maven Central, and `systems` have no public
monthly-downloads API (`scripts/gather.py` returns `(None, "no-source")`).

Because `NOT_APPLICABLE_RULES` previously included only
`("dependents_deduped", "ecosystem", "systems")`, every `go`, `maven`, and
`systems` repository received `Unmeasured(reason="third_party_gap")` on
`downloads_monthly`. Under D-MISS-2, `third_party_gap` counts against pillar
coverage (`c_criticality`), capping `c_criticality` at `0.75` for `go` and
`maven` and `0.5833` (`0.35 / 0.60`) for `systems`. Because the `career_capital`
persona gate (`CAREER_CAPITAL_MIN_COVERAGE = 0.80`) requires `c_criticality >=
0.80`, 100% of Go, Maven, and Systems repositories were structurally excluded
from `career_capital` for an ecosystem-level fact rather than a repository gap.

Adding `("downloads_monthly", "ecosystem", "go")`,
`("downloads_monthly", "ecosystem", "maven")`, and
`("downloads_monthly", "ecosystem", "systems")` to `NOT_APPLICABLE_RULES` (and
checking `NOT_APPLICABLE_RULES` in
`porchlamp.derive.bundle._resolve_downloads_monthly`) aligns 2.3 with D-MISS-5:
when an ecosystem has no public downloads registry by design, `downloads_monthly`
is `Unmeasured(reason="not_applicable")` and excluded from both numerator and
denominator of `c_criticality`.

## Change

| Constant | Before | After |
|---|---|---|
| `NOT_APPLICABLE_RULES` | `(("dependents_deduped", "ecosystem", "systems"),)` | `(("dependents_deduped", "ecosystem", "systems"), ("downloads_monthly", "ecosystem", "go"), ("downloads_monthly", "ecosystem", "maven"), ("downloads_monthly", "ecosystem", "systems"))` |

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump, matching RFC-0003
on the `rc1` pre-release line).

## Evidence

- **Sensitivity** (D-AGG-3): unchanged on `tests/fixtures/snapshot_50/` (0 of 50
  repos rated in the fixture).
- **Anchors** (D-VER-4): `torvalds/linux` remains `routed: off_platform`,
  `rated: false`; all anchor checks pass.
- **Rank movement**: none on `tests/fixtures/snapshot_50/` (`rank_order` and
  `counts` are unchanged; `records_hash` updates for the new `CONSTANTS_HASH`
  and `not_applicable` reason on `go`/`maven`/`systems` fixture bundles).

## Reviewer

Reviewed and verified against the full pytest and fixture reproduction suite.
