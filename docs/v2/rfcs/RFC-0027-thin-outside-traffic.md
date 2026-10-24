# RFC-0027: thin-outside-traffic

## Motivation

D-GATE-6 asks for one outside PR decided in 365 days. A repository that gets one or two outside PRs in
six months passes it, and can be ranked on responsiveness, governance and the rest of its pillars computed
from almost no outside activity. Such a project is not a place to put contribution effort: there is
nothing to compare a newcomer's PR against, and a rank over so little says mostly something about those
few accounts. Maintaining the collection and the ranking for them is also work spent where no reader is
helped.

## Method

D-GATE-6 gains a floor: a repository whose **complete** `pr_window` (it reaches back to the start of the
window, `pr_window_coverage`) holds fewer than `GATE6_MIN_OUTSIDE_PRS = 3` outside PRs opened in the
trailing `PR_WINDOW_DAYS = 180` days is thin, whatever it decided. An outside PR is the population every
external measure starts from (`gates.is_outside_pr`: an external `authorAssociation`, a non-bot author).
Drafts, closed and merged PRs all count; the 14-day age and 90-day account rules of D-GATE-5 do not apply,
because the question is whether outsiders come at all.

A thin repository is treated as having no workflow to measure:

- off-platform markers present: routed off-platform, as for a repository with no decided PR;
- off-platform markers unknown: D-GATE-6 is UNDECIDED, never failed;
- markers absent: `unrated: no_external_workflow`, the reason D-GATE-6 already uses.

The count is the record as fetched, like every gate rule: a repository that gains outside PRs after its
window was fetched is re-judged when the window is refreshed. Three PRs by one account count as three; the
floor asks whether outside PRs arrive, not how many people send them.

A window that stops short of the start of the period (cut at the PR cap) gives only a lower bound, so it
never makes a repository thin. Gates 1-5 keep priority: a lower-numbered failure is the reason shown.

## Change

- `constants.GATE6_MIN_OUTSIDE_PRS = 3`, hashed. `PR_WINDOW_DAYS` (180) is the existing window.
- `GateEvidence.outside_prs_180d` (`None` without a readable window or a reference time; validated as a
  non-negative int). `gate_evidence_for` fills it with `outside_pr_count_in`, built on the same
  `outside_pr_authors` the corpus statistics use, and `porchlamp.snapshot.build_cmd` writes it into the
  manifest.
- `evaluate_gates` applies the floor in D-GATE-6 as above.
- The collector's early prune (`classify_prune_reason`) applies the same test to a recorded complete
  `pr_window` (`_outside_prs_below_floor_in_raw`), so a thin repository is not collected further once its
  window is in. Without a window it prunes on nothing new.
- The reason sentence for `no_external_workflow` now names both causes: fewer than 3 outside pull requests
  in 180 days, or none decided.
- No new reason, route or list: the repositories join the existing `no_external_workflow` list.

| Constant | Before | After |
|---|---|---|
| `GATE6_MIN_OUTSIDE_PRS` | - | 3 |

`CONSTANTS_HASH` moves:
`861c2259fbee531957b102e4dbfa11f5c5ab532d9cd75a3df21fe1e2c203fcf7` ->
`de2c71c1d7bad01ff749cd81560974ce5199352075feeeda6d805a8d52dd2332`
(pinned in `tests/core/test_frozen.py`, with one new `_FROZEN_TABLE` row).

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump, matching the RFC-0001..0026 pre-release
convention).

## Evidence

Read-only run over the 2026-10-23 data root (`phone-2026-10-23`), at the live 2026-10-24 snapshot's
reference time (2026-10-22T22:47Z), for the 2,765 projects it ranks on the balanced board:

- **14 of 2,765 (0.5%) have fewer than 3 outside PRs and would leave the ranking**: 4 with none, 5 with
  one, 5 with two. All 14 have a complete window and no off-platform marker, so all become
  `unrated: no_external_workflow`. The other 2,751 have 3 or more (2,605 have 10 or more).
- Examples, with balanced rank: `sindresorhus/ora` (337, 2 outside PRs), `pmndrs/valtio` (419, 2),
  `rust-random/getrandom` (449, 2), `junegunn/fzf` (1600, 0), `mikf/gallery-dl` (1822, 0),
  `xojs/xo` (1862, 1), `FaridSafi/react-native-gifted-chat` (2345, 2).
- **Fixture** (`tests/fixtures/snapshot_50/`), regenerated with
  `scripts/build_fixture_snapshot.py --rewrite-expected`: it holds no `pr_window` envelopes, so
  `rated=0, gated=18, unrated=32` is unchanged and no repository changes outcome; only
  `ScoreRecord.constants_hash` moves (`records_hash`:
  `b8e2f5b32d07c676fe14e7e16700a85c3bc2f776da6dfe834afca39dd55d907d` ->
  `6fa702d8e7a1796fc586050fa603420bcb27f9b45e4a405cff8981f90624ae50`).
- **COULD NOT VERIFY:** how many of the repositories that are not ranked today (gated or unrated for
  another reason) are also thin, which changes only the reason shown for those with no lower-numbered
  failure; and the effect on the collection cost, which depends on how many thin repositories the
  universe holds before their windows are fetched.

## Reviewer

Not yet reviewed.
