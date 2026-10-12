# RFC-0007: review-handoff-routing

## Motivation

D-GATE-5 (`docs/v2/02-RANKING-DESIGN.md` section 2) fails a repository as a
black hole when more than half of its outside PRs get no substantive response
on GitHub within 14 days. It can only see GitHub. Four widely used projects
answer every outside PR, just not there, and were published as black holes:

- `golang/go` and `golang/tools`: a bot imports each PR into Gerrit, where the
  review happens ("This PR ... has been imported to Gerrit for code review").
- `git/git`: GitGitGadget relays each PR to the mailing list ("Welcome to
  GitGitGadget", comments by `gitgitgadget-git`).
- `mapbox/mapbox-gl-js`: a bot says it will "import it to our internal
  repository for further review".

D-GATE-6 already has the right outcome for a project whose review is not on
GitHub: it is routed off-platform (`unrated: off_platform_routed`), scored but
never ranked. What it lacked is a way to see this from the project's own
notices (the existing off-platform marker reads the file tree only).

The rule added here, in D-GATE-6: the **outside PRs** of a repository are the
PRs in its recorded `pr_window` envelope that the collector's own
classification (`derive_pr_window`: `is_external` and not `is_bot_author`)
marks as external and not bot-authored, with no age or date filter. A PR carries
a **review-elsewhere notice** when at least one of its comments, written by an
account other than the PR's own author (logins compared case-insensitively),
contains one of `GATE6_HANDOFF_NOTICES` (case-insensitively) or is written by an
account whose login starts with one of `GATE6_HANDOFF_RELAY_LOGIN_PREFIXES`; a
bot's comment counts, reviews and review threads are not read. A repository
**hands review off** when `notices >= GATE6_HANDOFF_MIN_PRS` and
`notices >= GATE6_HANDOFF_MIN_SHARE * outside_prs`. For such a repository
(`GateEvidence.review_handoff is True`):

- D-GATE-5 is recorded UNDECIDED, never failed, whatever the ghost rate;
- D-GATE-6 routes it off-platform whether or not it has a decided external PR;
- D-GATE-1..4 are unchanged (an archived or stale repository is still gated);
- the engine does not give it the `"black_hole"` suspicion flag.

A repository that does not hand review off, or whose `pr_window` envelope is
absent or unreadable (`review_handoff` is `False` or `None`), behaves exactly as
before.

Not in this RFC: any change to how the ghost rate (GR_14) is computed, a
"merged elsewhere" rule (the internal-merge notices of `google/adk-python` and
`vercel/vercel` arrive after the 14-day deadline, so those projects stay
flagged), and README-level statements such as "we do not accept pull requests".

## Change

| Constant | Before | After |
|---|---|---|
| `GATE6_HANDOFF_MIN_PRS` | *(absent)* | `5` |
| `GATE6_HANDOFF_MIN_SHARE` | *(absent)* | `0.5` |
| `GATE6_HANDOFF_NOTICES` | *(absent)* | `("has been imported to gerrit for code review", "welcome to gitgitgadget", "import it to our internal repository for further review")` |
| `GATE6_HANDOFF_RELAY_LOGIN_PREFIXES` | *(absent)* | `("gitgitgadget",)` |

All four are members of `constants_dict()`, so `CONSTANTS_HASH` moves:
`aa6c4cd6f702fdc738cafb68022eb2613491657a9cb74c180fc2a1bd1236e662` ->
`aca719a922264ba5edb6adcdab6c0c08eaff7a315d0e5818a567509e5b0d3ba9`
(pinned in `tests/core/test_frozen.py`, with one `_FROZEN_TABLE` row per constant).
Consumers: `porchlamp.derive.gate_evidence._review_handoff` and
`_pr_has_review_handoff_notice` read all four; `porchlamp.core.methodology`
publishes them in the D-GATE-6 entry (criterion text and cutoffs).

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump, matching RFC-0003
and RFC-0006 on the `rc1` pre-release line, by the lead's ruling of 2026-10-13;
README step 3 would class a gate change as major, so the reviewer should
confirm the ruling covers this RFC).

## Evidence

- **Sensitivity** (D-AGG-3): unchanged on `tests/fixtures/snapshot_50/` (0 of 50
  repos rated before and after). The fixture's source corpus
  (`cvi-scratch/sample-200`) holds no `pr_window` envelopes, so
  `review_handoff` is `None` for all 50 and the rule cannot move any of them.
- **Anchors** (D-VER-4): `torvalds/linux` remains `routed: off_platform`,
  `rated: false`; the anchor checks pass.
- **Rank movement**: none on `tests/fixtures/snapshot_50/` (`counts`,
  `rank_order` and every record are unchanged; `records_hash` updates only for
  the new `CONSTANTS_HASH`, rewritten by `scripts/build_fixture_snapshot.py
  --rewrite-expected`).
- **Real data** (`refresh-2026-10`, 10,997 recorded `pr_window` envelopes):
  exactly six repositories hand review off: `git/git` (158 notices of 166
  outside PRs), `golang/go` (374 of 437), `golang/oauth2` (8 of 9),
  `golang/tools` (50 of 59), `golang/vscode-go` (11 of 13) and
  `mapbox/mapbox-gl-js` (22 of 22). `golang/mobile` (2 of 4) carries notices and
  does not hand off. The engine's `gate_evidence_for` agrees with an independent
  computation of the rule on all 10,997.

## Reviewer

Reviewed on 2026-10-13 by the lead session (Claude Opus 5.5), which wrote the
spec and did not write the code; the code was written by a separate agent
(Claude Sonnet 5.5). The reviewer read the diff against the spec and
reproduced the proof on the reviewed tree:

- the known-answers check on the real data, from a script that does not use
  the engine's code: the engine agrees on 10,997 of 10,997 repositories;
- three defects planted by hand, each turning its test red and green again
  after a byte-identical restore: D-GATE-5 still failing a hand-off repository,
  the share test weakened from `>=` to `>`, and `review_handoff` dropped from
  the manifest that `snapshot store` reads;
- the full suite: 3555 passed, 0 failed (base: 3529 passed, 0 failed).

The no-bump ruling above is confirmed for this RFC: `BENCHMARK_VERSION` stays
`2.0.0-rc1`, as for RFC-0003 and RFC-0006.
