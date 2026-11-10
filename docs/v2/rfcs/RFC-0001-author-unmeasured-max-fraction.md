# RFC-0001: author-unmeasured-max-fraction

## Motivation

The W1-4 packet's `porchlamp.collect.github_graphql` module computes row 1.2
(DR_90) and row 1.3 (MR_first) — D-IN-3's D-IN-3-adjusted rates — from a
per-repo cohort of authors whose account-age/event-count eligibility and
prior-merge history are fetched per author. The round-2 review (B-1/B-2/B-3
of `W1-4-r2-review.md`) found that a batched-fetch defect could silently
collapse this cohort: on `pytest-dev/pytest`, 45 of 62 cohort authors lost
their eligibility data to a partial-batch discard, and MR_first's
D-IN-3-adjusted rate was computed anyway — `0.3818 (n=55)` moved to
`0.6667 (n=9)` with no status change and no recorded exclusion count. D-IN-3
and D-MISS-1 both require an absent input to read as `Unmeasured`, not as a
silently smaller `Measured` sample; row 1.2/1.3's existing `PARAM_MIN_N`
gate (`porchlamp.core.constants_v2.PARAM_MIN_N`) only bounds the FINAL rate's own
sample size after every filter has run, so it cannot catch this: 9 and 22
are both above their respective minimums (5 and 10).

This RFC adds a distinct gate, applied at the collector's own failure rate
rather than the final sample size: when more than a named fraction of a
repo's cohort authors are unmeasured (a per-author search or eligibility
fetch failed — not the designed search-cap sample, which is a deliberate,
separately-counted exclusion), the D-IN-3-adjusted rate is reported
`Unmeasured(collector_failure)` instead of computed on the collapsed
remainder.

## Change

| Constant | Before | After |
|---|---|---|
| `AUTHOR_UNMEASURED_MAX_FRACTION` | (did not exist; briefly lived as a module-level constant in `porchlamp/collect/github_graphql.py`, outside `constants_v2`, during W1-4 round 3) | `0.20` |

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump). This is a new
constant with no scoring-path consumer yet: `porchlamp.collect.github_graphql`'s
four D-IN-3/D-IN-4 bundle fields (`dr90_decision_rate`, `mr_first_rate`,
`newcomer_conversion`, `rr90_return_rate`) are not wired into
`porchlamp.derive.bundle` — that module still `_unmapped`s all four inputs (W2-9,
unlanded as of this RFC). Adding this constant and its gate therefore moves
zero `ScoreRecord` fields for any repo scored today; the W2-8 round-2
precedent (`391d10b91e...` -> the census-closure hash, same file) applied
the identical reasoning for six constants that were newly hashed with no
re-bound value changing at their consumer — this RFC is the same class of
change, applied to a genuinely new (not merely newly-hashed) constant.

## Evidence

- **Sensitivity** (D-AGG-3): not applicable. `AUTHOR_UNMEASURED_MAX_FRACTION`
  is read only by `porchlamp.collect.github_graphql.derive_cohort_fields`, which
  has no caller in `porchlamp.core`'s scoring path (`engine.score`,
  `aggregate.*`, `matrix.*`, `params.*`) or in `porchlamp.derive.bundle`. A
  Kendall-tau perturbation report requires a scored ranking to perturb;
  none of this constant's four candidate output fields reaches one.
- **Anchors** (D-VER-4): not applicable, same reason — no anchor repo's
  gate outcome, tier, or composite depends on a value this constant can
  change, since nothing downstream of `derive_cohort_fields` consumes its
  output today.
- **Rank movement**: **none** on the fixture snapshot
  (`tests/fixtures/snapshot_50/`) or any other rated repo — confirmed by
  regenerating the fixture (`scripts/build_fixture_snapshot.py`) after this
  change: every bundle file and every `raw_hash` is byte-identical to the
  pre-change fixture; only `constants_hash` (stamped per bundle/manifest)
  and `records_hash` (which embeds `constants_hash` per stored `ScoreRecord`
  row) moved, exactly as the W2-8 round-2 census additions moved them with
  no rank change. The parameter this constant gates was, in the review's
  own words, "previously unmeasurable" in the sense that mattered here: no
  consumer existed to rank against.
- **Direct evidence for the value itself** (in place of a rank-movement
  report, since one is not obtainable yet): the round-2 review's own
  reproduction of the collector defect on `pytest-dev/pytest` — 45 of 62
  (72.6%) cohort authors unmeasured, well above the chosen 0.20 threshold —
  is the case this gate is built to catch. `AUTHOR_UNMEASURED_MAX_FRACTION
  = 0.20` was chosen to match this module's existing `apply_author_cap`
  per-author cap fraction (`AUTHOR_CAP_FRACTION = 0.20`, same file, D-IN-3),
  reusing an already-reviewed round number rather than introducing a second,
  unrelated one; W1-4 round 3's own boundary tests
  (`test_derive_cohort_fields_unmeasured_threshold_exactly_20_percent_not_gated`
  / `..._just_above_20_percent_gated`) pin the exact-0.20-not-gated /
  just-over-0.20-gated boundary this value creates.

## Reviewer

Not yet reviewed independently — this RFC accompanies the W1-4 round-3
constants-freeze-compliance fix (dispatched directly by the lead, closing
the escape the freeze's own census exists to prevent) and is submitted for
review alongside the packet's next round.

Reviewed by the lead (Fable 5.1) on 2026-09-13 at landing (`fbe435d`). Ruling on the declined `BENCHMARK_VERSION` bump: accepted — the changelog header exempts constants that gain a value without a scoring consumer; the bump happens when W3-1 wires these fields into a scored parameter, in that RFC.

