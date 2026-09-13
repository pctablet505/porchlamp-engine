# RFC-0004: din3-hygiene-constants

## Motivation

D-IN-3 (`docs/v2/02-RANKING-DESIGN.md`) defines the author-eligibility rule
every rate parameter's derivation needs (merge, decision, conversion,
return): an account must be at least 90 days old at PR creation and have
at least 5 public events, and one author's contribution to any rate is
capped at 20% of its numerator and denominator. Packet W1-4 implemented
this mechanism (`is_eligible_account`, `apply_author_cap`) directly inside
`cvi.collect.github_graphql`, with its three governing constants
(`MIN_ACCOUNT_AGE_DAYS`, `MIN_PUBLIC_EVENTS`, `AUTHOR_CAP_FRACTION`) as
local module-level values — outside the constants freeze, the same escape
RFC-0001 closed for `AUTHOR_UNMEASURED_MAX_FRACTION`.

Packet W3-1 (`docs/v2/05-WORK-PLAN.md`) promotes the mechanism itself to
`cvi.core.hygiene` — a `cvi.core`-layer module (import-graph-legal for
`cvi.derive.bundle`'s future resolvers to call, once W2-9 wires DR_90/
MR_first/newcomer_conversion/rr90_return_rate) — so every rate parameter's
derivation shares the same tested D-IN-3 implementation regardless of
which layer computes it (R-2: one definition per fact). The three
constants move with it.

## Change

| Constant | Before | After |
|---|---|---|
| `MIN_ACCOUNT_AGE_DAYS` | `90` (module-level in `cvi/collect/github_graphql.py`) | `90` (`cvi.core.constants_v2`) |
| `MIN_PUBLIC_EVENTS` | `5` (module-level in `cvi/collect/github_graphql.py`) | `5` (`cvi.core.constants_v2`) |
| `AUTHOR_CAP_FRACTION` | `0.20` (module-level in `cvi/collect/github_graphql.py`) | `0.20` (`cvi.core.constants_v2`) |

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump). Same
reasoning as RFC-0001: no value moves, and no `ScoreRecord` field for any
repo scored today reads any of these three constants — `is_eligible_
account`/`apply_author_cap` are called only from `cvi.collect.
github_graphql.derive_cohort_fields`, whose four outputs are still
`_unmapped` in `cvi.derive.bundle` (W2-9 unlanded). `docs/v2/rfcs/
README.md`'s changelog-header exemption for "no scoring-path consumer"
applies identically.

## Evidence

- **Sensitivity** (D-AGG-3): not applicable, same reason as RFC-0001 — no
  consumer downstream of `cvi.core.hygiene` reaches a scored rank yet.
- **Anchors** (D-VER-4): not applicable, same reason.
- **Rank movement**: **none** on the fixture snapshot
  (`tests/fixtures/snapshot_50/`) — confirmed by regenerating the fixture
  (`scripts/build_fixture_snapshot.py`) after this change: every bundle
  file and every `raw_hash` is byte-identical to the pre-change fixture;
  only `constants_hash` (stamped per bundle/manifest) and `records_hash`
  (which embeds `constants_hash`) moved.
- **Direct evidence for the values themselves** (in place of a rank-
  movement report, since one is not obtainable yet): the relocated values
  are UNCHANGED (90 / 5 / 0.20, the same numbers W1-4 shipped and this
  round's `tests/core/test_hygiene.py` pins as packet W3-1's own
  acceptance oracle — 6 first-timer PRs from one author capped to
  `floor(0.20 * 10) = 2`, an 89-day account excluded, a 90-day account
  included). This RFC moves WHERE the constants are defined, not WHAT
  they are.

## Reviewer

Not yet reviewed independently — this RFC accompanies packet W3-1 and is
submitted for review alongside it, following RFC-0001's precedent (its
own Reviewer section was filled by the lead at W1-4's acceptance).
