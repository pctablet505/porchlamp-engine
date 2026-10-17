# RFC-0010: self-close-exclusion-within-deadline

## Motivation

D-GATE-5 (`docs/v2/02-RANKING-DESIGN.md` section 2; rule 2 of RFC-0008) excludes an
outside PR from the black-hole population when its author closed it and no maintainer
had responded before the close. RFC-0008 put no time limit on that close. An author who
waited 30 days without a reply and then gave up dropped out of the count, so the
repository's silence toward that PR was never counted.

Owner ruling, 2026-10-17: "Fix: only closes within 14 days excluded".

## Change

The rule, as `porchlamp.core.gates.evaluate_gate5_window` applies it:

- An unanswered PR whose author closed it, with no substantive maintainer response at
  or before the close, is excluded (`excluded_self_closed`) only when the close came
  within the response deadline: `closedAt <= clock_start + GATE5_RESPONSE_DEADLINE_DAYS`.
- `clock_start` is the clock the gate already uses: `createdAt`, or
  `max(createdAt, readyForReviewAt)` for a PR that was a draft (RFC-0008 rule 1).
- A self-close after the deadline with no response is counted, and counts as unanswered.
- A self-closed PR with no recorded `closedAt` is counted (the close cannot be placed
  inside the deadline).
- The boundary is inclusive, the same as for a response: a close exactly 14 days after
  `clock_start` is within the deadline.

The limit reads the existing constant `GATE5_RESPONSE_DEADLINE_DAYS = 14` (the same
deadline a response must meet); no constant is added or changed.

| Constant | Before | After |
|---|---|---|
| *(none)* | | |

`CONSTANTS_HASH` is unchanged:
`6e4f3e8e1d0c0fcd829d9c8c386a65179eadb5f936595f96e9eba98712d55b94` ->
`6e4f3e8e1d0c0fcd829d9c8c386a65179eadb5f936595f96e9eba98712d55b94`.
`tests/core/test_frozen.py` needs no change.

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"`. Step 3 of the procedure applies to
an RFC that changes a constant; this one changes none, and
`GATE5_RESPONSE_DEADLINE_DAYS` gains no new consumer (the same function already read it
for the response deadline). RFC-0007 and RFC-0008 changed D-GATE-5 without a bump.

The published methodology (`porchlamp/core/methodology.py`, D-GATE-5 criterion) states
the rule.

## Evidence

- **Sensitivity** (D-AGG-3) and **rank movement**: not run. The fixture snapshot
  (`tests/fixtures/snapshot_50/`) holds no `pr_window` envelopes (RFC-0008, Evidence), so
  the rule has no input there and cannot move any of its 50 repositories.
- **Recorded data**: not measurable. The `pr_window` envelopes behind the 2026-10-14
  snapshot were fetched before the query asked for the close actor, so no PR in them is
  known to be author-closed. The effect is measured on the next collection.
- **Tests** (`tests/core/test_gates.py`): a self-close on day 10 with no response is
  excluded; on day 15 it is counted as unanswered; for a draft, the 14 days run from
  ready-for-review; a close exactly at the deadline is excluded; raising
  `GATE5_RESPONSE_DEADLINE_DAYS` moves the limit with it.

## Reviewer

Reviewed on 2026-10-17 by the lead session (Claude Opus 5.5), which did not write the
code; the code was written by a separate agent. Checked: the rule matches the owner's
ruling word for word, the limit reads `GATE5_RESPONSE_DEADLINE_DAYS` and runs from the
same clock start as a response, and the two tests above fail when the limit is removed
from `evaluate_gate5_window`.
