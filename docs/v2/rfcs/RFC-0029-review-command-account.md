# RFC-0029: review-command-account

## Motivation

D-IN-1 decides bot identity by account type and the exact login list `BOT_LOGINS` (RFC-0012,
RFC-0014); D-IN-2 counts a review, or a comment of 40 characters or more, from the repository's own
people as its response. RFC-0025 added a rule for automation posted as a person: an account's reply
template is automation when it spans at least `AUTOMATION_MIN_PRS = 20` PRs and at least
`AUTOMATION_FAST_SHARE = 0.80` of its first replies come within `AUTOMATION_FAST_SECONDS = 60` of the
PR opening.

Issue #57 asks that every ranked project with a median first reply under a minute be read by hand.
On the 2026-11-05 snapshot one is left: `homeassistant-ai/ha-mcp`, 45 seconds over 108 outside PRs,
first on the published fastest-replies list. All 56 of its replies under a minute come from the
account `ghhamcp`:

- GitHub types it as a user, created 2026-07-12, with no name, no bio, no public repositories and
  no followers.
- Its 948 comments in the window have two openings only: "@codex review — apply the review criteria
  in .gemini/styleguide.md in addition to AGENTS.md guidance" (847) and "@codex review" (101). Each
  asks an AI reviewer for a review; none answers the author.

RFC-0025's rule does not catch it:

- The long comment's template (`review apply`, after the `@codex` mention is removed) spans 419 PRs,
  but its first posting comes within 60 seconds on 289 of them, 69%, under the 80% bar, because the
  account also posts it later in a PR's life.
- "@codex review" normalises to one word, so it has no template; at 13 characters it is not a
  substantive comment either.

The account does nothing but post review commands to another bot, so it belongs on the identity list
with the service accounts RFC-0012 and RFC-0014 named.

## Change

| Constant | Before | After |
|---|---|---|
| `BOT_LOGINS` | 109 logins | 110 logins: adds `ghhamcp` |

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump, matching the RFC-0001..0028
pre-release convention).

`CONSTANTS_HASH`: `093cefd6c2f45824a756d1a2fa95b2fe1aa857ad505357ad5cdfcbaff81a4182` ->
`44b11850f0a20c3489cef53d2582215c4a6c17d35867f65a8c62067d16ca91cf`.

## Evidence

- **Where the account appears.** One `pr_window` record of the 2026-11-05 data root (11,000
  projects) names `ghhamcp`: `homeassistant-ai/ha-mcp`. No fixture under `tests/fixtures/` names it.
- **Sensitivity** (D-AGG-3): the fixture snapshot (`tests/fixtures/snapshot_50/`) holds no comment by
  the account, so its ranking is unchanged: Kendall tau 1.0 for every pillar.
- **Anchors** (D-VER-4): none of the anchor projects' records names the account; every anchor
  relation holds as before.
- **Rank movement**: on the fixture snapshot, 0 projects change rank. On the 2026-11-05 data,
  `homeassistant-ai/ha-mcp`'s median first reply goes from 45 seconds over 108 outside PRs to
  3.09 hours over 105 (`derive_pr_window_fields` on its stored record), so it leaves the top of the
  fastest-replies list. No other project's record changes.
- **Test**: `tests/core/test_bot_identity.py::test_an_account_that_only_posts_review_commands_to_another_bot_never_answers_a_pr`.

## Reviewer

Not yet reviewed.
