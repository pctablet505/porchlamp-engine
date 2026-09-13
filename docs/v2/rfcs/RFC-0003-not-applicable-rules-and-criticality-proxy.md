# RFC-0003: not-applicable-rules-and-criticality-proxy

## Motivation

W2-8c found that `dependents_deduped` (row 2.1) exists only for
pypi/npm/crates/go/maven (the deps.dev system map); `go`/`maven`/`systems`
get no envelope by design, and `openssf_criticality` (row 2.2) hard-required
that same dependents signal — so every `systems` repo (10 of the 50 fixture
repos) could never measure 2.1 or 2.2 and, under D-MISS-2/3 as written
before this RFC, was capped at ceiling B for a fact about registries, not
about the repository (`docs/v2/00-README.md`, 2026-09-13 "D-MISS-5, W1-2f,
W2-13" ruling row).

This RFC's constants close the loop the ruling opened:

1. `NOT_APPLICABLE_RULES` makes the D-MISS-5 guard itself a hashed fact
   (`02` section 5's "the rule list lives in `constants_v2` and is part of
   the constants hash"): the only design-level rule permitted to assign
   `Unmeasured(not_applicable)` today is `dependents_deduped` when the
   bundle's own `ecosystem` fact is `"systems"`. `cvi.core.engine` reads it
   to downgrade any other not_applicable claim to `collector_failure` (03
   section 4: "a bundle or collector can never assert not_applicable on its
   own"), and `cvi.derive.bundle._resolve_dependents_deduped` reads the same
   tuple instead of its own inline check.
2. `OPENSSF_PROXY_MIN_SIGNALS` is the minimum count of the ten Pike signals
   (`cvi.core.openssf.SIGNAL_FIELDS`) that must be present for row 2.2's
   `proxy:no_dependents_signal` renormalised score to fire when the
   dependents signal is `not_applicable`/`third_party_gap` (never a
   retry-able `collector_failure`) — the D-MISS-5 consequence text in `02`
   section 5.
3. `WINDOW_STALENESS_DAYS` is a straight relocation: W1-7f (2026-09-13,
   `91362a1`) introduced this value inside `cvi/collect/pike_signals.py`,
   outside `constants_dict()`'s coverage, with its own follow-up noting the
   gap ("relocate, register, RFC + hash re-pin" -- folded into this
   packet). It already gated row 2.2's F5 staleness refusal; this RFC only
   moves where the value of `14` lives, per the same "one definition per
   fact" principle W2-8 round 2's census applied to `TIER_CUTOFFS` and the
   OpenSSF weights.

## Change

| Constant | Before | After |
|---|---|---|
| `NOT_APPLICABLE_RULES` | did not exist (the rule was an inline `if ecosystem == "systems"` check in `cvi/derive/bundle.py`, unhashed and unenforced against any other row) | `(("dependents_deduped", "ecosystem", "systems"),)` |
| `OPENSSF_PROXY_MIN_SIGNALS` | did not exist (row 2.2 had no proxy path at all — a missing dependents signal made the whole ten-signal set `Unmeasured`) | `8` |
| `WINDOW_STALENESS_DAYS` | `14` (module-level constant in `cvi/collect/pike_signals.py`, outside `constants_dict()`) | `14` (relocated into `constants_v2`, re-bound in `pike_signals.py` under the same name) |

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump). `01/02`
section 5's D-MISS-5 is a missingness-policy change (D-VER-1's "major"
class) in the ranking-design sense, but per the W2-8 round-2 precedent
(relocating a value that was already governing behaviour, with no re-bound
value at its consumer module changing) this RFC's `WINDOW_STALENESS_DAYS`
relocation moves nothing; `NOT_APPLICABLE_RULES`/`OPENSSF_PROXY_MIN_SIGNALS`
are new constants but the *behaviour* they enable was mandated by the
2026-09-13 lead ruling that created D-MISS-5 in the same session this
packet was dispatched from -- the ranking-design change itself, not this
RFC, is the D-VER-1 event; W5-2 (the pre-release version-bump procedure,
already flagged open in the W2-8 round-2 lead-log row) is where the
consolidated 2.0.0-rc1 -> 2.0.0-rc2-or-later bump for the whole
02/03-amendment set belongs, not a per-packet bump against a still-`rc1`
line.

## Evidence

- **Sensitivity** (D-AGG-3): not computable on `tests/fixtures/snapshot_50/`
  -- 0 of 50 repos are rated in this fixture (W2-8b's documented, pre-existing
  finding: the sample's derived documents are v1-format and 11 inputs are
  `collector_failure`/`third_party_gap` on all 50), so there is no ranking to
  perturb. Same limitation RFC-0001 recorded for the same fixture.
- **Anchors** (D-VER-4): `torvalds/linux` is the only D-VER-4 anchor present
  in the fixture and is unaffected -- still `routed: off_platform`,
  `rated: false`, `coverage: 0.0525` before and after (its criticality
  pillar's other two parameters, 2.2 and 2.3, are independently unmeasured
  in this fixture for reasons unrelated to D-MISS-5, so removing 2.1's
  not_applicable weight from the pillar's denominator does not change a
  0-numerator ratio: `0/0.60 = 0/1.00 = 0.0`). No anchor relation moves.
- **Rank movement**: **none** on the fixture snapshot -- confirmed by
  regenerating it (`scripts/build_fixture_snapshot.py`) after this change:
  `rank_order` (alphabetical; 0 ranks exist with 0 rated repos) is
  byte-identical, `counts` (`rated=0, gated=35, unrated=15`) is unchanged,
  and none of the 10 `systems`-ecosystem repos' per-repo `coverage` value
  moved (checked all 10: `alibaba/AndFix`, `apache/thrift`,
  `apple/darwin-xnu`, `blender/blender`, `brunodev85/winlator`, `ceph/ceph`,
  `drogonframework/drogon`, `duckdb/duckdb`, `electron/electron`,
  `torvalds/linux`) -- every one of them has ITS OWN 2.2/2.3 unmeasured for
  reasons the sample's staleness predates this packet (2.2's
  `proxy:no_dependents_signal` never fires against this specific sample
  because its underlying v1 derived documents were produced before this
  packet's `cvi/collect/pike_signals.py` change and carry no
  `openssf_signals_partial` field at all -- a fresh `derive_repo` run would
  activate it, per this RFC's own new code path, but that re-derive is out
  of this packet's scope). `records_hash` still moves
  (`099264a0d5…` in this packet's own handoff) because
  `cvi.core.models.PillarScore` gains a new field (`applicable: bool`,
  default `True`) that appears in every stored record's `pillars` dump
  structurally, and because `CONSTANTS_HASH` itself moved (stamped per
  record) -- not because any repo's coverage or tier value changed.
  `docs/v2/02-RANKING-DESIGN.md`'s own D-MISS-5 acceptance oracle (a
  hypothetical isolated `systems` repo with everything else measured
  reaching `C = 1.00`, `< 0.95` before) is proven at the unit level
  (`tests/core/test_aggregate.py::test_composite_coverage_systems_repo_end_to_end_reaches_one`),
  not observable on this specific, heavily-gapped 50-repo fixture.

## Reviewer

Not yet reviewed independently -- submitted with the W2-13 packet for the
lead's reproduction pass per `03` section 7 ("every ACCEPT is reproduced by
a second independent agent; mutation proof for behavioural changes").
