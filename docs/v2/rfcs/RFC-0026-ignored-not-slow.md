# RFC-0026: ignored-not-slow

## Motivation

D-GATE-5 removed a project from the ranking when the Wilson lower bound of the share of its outside PRs
with no substantive human reply, merge or maintainer close inside 14 days exceeded one half. That share
mixes two behaviours the ranking should treat differently:

- a project that **ignores** outside PRs: no maintainer ever replies, reviews, merges or closes them;
- a project that is **slow**: it answers, merges or closes, but after the 14-day deadline.

On the 2026-10-23 data, the 845 black holes of the 2026-10-24 snapshot held 77,083 counted outside PRs:
18,955 answered inside 14 days, 7,663 replied to later, 2,311 merged or closed by a maintainer later,
12,666 closed with no maintainer reply, merge or maintainer close (the author's own close, or one the record does not attribute), and 35,488 still open and untouched. Of the
58,128 not answered in time, 83% were never touched and 17% were answered late. A project whose PRs
get answered after three weeks is a poor place to wait, which 1.4 GR_14 already scores, but it is not a
project that ignores contributors. The gate gave both the same verdict, and removed the slow ones from
the ranking altogether.

## Method

A counted outside PR is **touched** when a maintainer reply (D-IN-2, any time), a merge, or a close by a
maintainer is recorded for it, within the deadline or after it. A PR is **ignored** when it was not
answered inside 14 days and is not touched. D-GATE-5 now judges the ignored share: the Wilson lower bound
(z = `GATE5_WILSON_Z`) of `ignored / n` must not exceed `GATE5_MAX_ZERO_RESPONSE_SHARE`, with `n` the same
population as before and the same minimum `GATE5_MIN_N`. Population, exclusions, the 14-day age rule,
draft handling and undecided cases are unchanged. Ignored PRs are a subset of unanswered ones, so a
project that fails the new test also failed the old one: the change only releases projects.

"Never" is not claimed: a PR is ignored as of when the data was read, and the text says so.

## Change

- `porchlamp.core.gates.evaluate_gate5_window` records `Gate5CountedPR.touched_ever`, counts `ignored`
  and its Wilson bound, and sets `is_black_hole` from the ignored share.
- `GateEvidence` gains `pr_ignored` and `pr_ignored_n` (both or neither); `evaluate_gates` and
  `is_black_hole_bundle` judge them. Without the counts (hand-built evidence) the 14-day unanswered share
  decides, as before. `gate_evidence_for` fills them from the same breakdown D-GATE-5 uses, and
  `porchlamp.snapshot.build_cmd` writes them into the manifest.
- 1.4 GR_14 is unchanged: it is still the 14-day unanswered share, now scored for the slow projects the
  gate no longer removes.
- The collector's early prune (`porchlamp.collect.fingerprint.is_confirmed_black_hole_in_raw`) reads the same
  breakdown's `is_black_hole`, so a slow project is still collected instead of skipped as a black hole.
- The evidence panel (`BlackHoleEvidence`) adds `ignored`, `ignored_share` and, per counted PR,
  `touched_ever`; its `wilson_lower_bound` is the bound of the share the gate judged. The project card
  shows the ignored count with verifier hooks `bh.ignored` and `bh.ignored_share`, and the rule and
  reason sentences say what is judged. List rows and report figures still state the 14-day unanswered
  counts, which remain true of every black hole.
- No constant changes: `CONSTANTS_HASH` is unchanged. `BENCHMARK_VERSION` is unchanged (pre-release
  convention).

## Evidence

Read-only run over the 2026-10-23 data root, evaluated at the snapshot's reference time (2026-10-22):

- Of the 845 projects gated `black_hole`, **553 stay** and **292 are released** to the ranking checks.
  Examples released: `3b1b/manim` (22 of 42 counted PRs ignored; the bound is below one half),
  `activepieces/activepieces` (54 of 122), `1jehuang/jcode` (104 of 271). The 86 projects in which no counted
  PR was ever touched all stay.
- No project that passed before fails now (the new test implies the old one).
- **Fixture**: it holds no `pr_window` envelopes, so no gate outcome changes.
- **COULD NOT VERIFY:** how many of the 292 pass the later checks (coverage, the responsiveness pillar,
  D-GATE-6) and so enter the ranking; that needs a full derive and build. The 60-day variant (judge only
  PRs at least 60 days old) was measured and not adopted: it keeps 347 and leaves 141 projects with too
  few old PRs to judge.

## Reviewer

Not yet reviewed.
