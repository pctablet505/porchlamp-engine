# RFC-0029: review-command-account

## Motivation

D-IN-1 (`docs/v2/02-RANKING-DESIGN.md` section 3) decides bot identity by account type and the exact
login list `BOT_LOGINS` (RFC-0012, RFC-0014); D-IN-2 counts a review, or a comment of 40 characters
or more, from the repository's own people as its response. RFC-0025 added a rule for automation
posted as a person: an account's reply template is automation when it spans at least
`AUTOMATION_MIN_PRS = 20` PRs and at least `AUTOMATION_FAST_SHARE = 0.80` of its first replies come
within `AUTOMATION_FAST_SECONDS = 60` of the PR opening.

Issue #57 asks that every ranked project with a median first reply under a minute be read by hand.
On the 2026-11-05 snapshot one is left: `homeassistant-ai/ha-mcp`, 45 seconds over 108 outside PRs,
first on the published fastest-replies list. All 56 of its replies under a minute come from the
account `ghhamcp`:

- GitHub types it as a user, created 2026-07-12, with no name, no bio, no public repositories and
  no followers.
- Its 948 comments in the window have two texts only: "@codex review — apply the review criteria
  in .gemini/styleguide.md in addition to AGENTS.md guidance" (847) and "@codex review" (101). Each
  asks an AI reviewer for a review; none answers the author.
- Its 144 reviews have three texts only: "Automated approval: verified Dependabot update; required
  checks remain authoritative." (104), "Automated approval: Renovate enabled auto-merge for this
  update; required checks remain authoritative." (39), and an empty approval (1).
- On the day it was created it opened a PR and merged it itself 3 minutes later (#1824), and it
  merged a member's PR (#1825). People closed its two other PRs unmerged (#1855, #2114).

All its comments and reviews carry the `CONTRIBUTOR` association. It counts among the repository's
own people because it merged PRs (#1824 and #1825; merging takes write access), and that is how its
100-character command became a response.

RFC-0025's rule does not catch it:

- The long comment's template (`review apply`, after the `@codex` mention is removed) spans 419 PRs,
  but its first posting comes within 60 seconds on 289 of them, 69%, under the 80% bar, because the
  account also posts it later in a PR's life.
- "@codex review" normalises to one word, so it has no template; at 13 characters it is not a
  substantive comment either.

Lowering the bar to 69% would loosen the rule for every project's templates, and no template rule
can see the bare command. RFC-0014 drew the line this RFC follows: it listed robot accounts such as
`yoshi-approver`, and kept off people whose accounts also post templates, because listing them would
strip their real commits, PRs and reviews. Every comment and review `ghhamcp` posted is one of the
fixed texts above, so listing it strips nothing a person wrote. It belongs on the identity list with
the service accounts RFC-0012 and RFC-0014 named.

## Change

| Constant | Before | After |
|---|---|---|
| `BOT_LOGINS` | 109 logins | 110 logins: adds `ghhamcp` |

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (D-VER-1 class: none under the pre-release
rule; `BOT_LOGINS` already has scored consumers, so a login added to it bumps nothing, as in
RFC-0012 and RFC-0014).

`CONSTANTS_HASH`: `093cefd6c2f45824a756d1a2fa95b2fe1aa857ad505357ad5cdfcbaff81a4182` ->
`44b11850f0a20c3489cef53d2582215c4a6c17d35867f65a8c62067d16ca91cf`.

## Evidence

- **Where the account appears.** In the 2026-11-05 data root (10,998 projects) only the files of
  `homeassistant-ai/ha-mcp` name `ghhamcp`: its PR window, commits, author history and
  contributors. No fixture under `tests/fixtures/` names it.
- **Sensitivity** (D-AGG-3): the fixture snapshot (`tests/fixtures/snapshot_50/`) holds no comment by
  the account, so its ranking is unchanged: Kendall tau 1.0 for every pillar. Re-scored with the new
  list, every one of its 50 records is identical apart from `constants_hash`; its records hash,
  constants hash and merkle root were regenerated with
  `scripts/build_fixture_snapshot.py --rewrite-expected`.
- **Anchors** (D-VER-4): none of the anchor projects' records names the account; every anchor
  relation holds as before.
- **Rank movement**: on the fixture snapshot, 0 projects change rank. On the 2026-11-05 data,
  `derive_pr_window_fields` and `commit_domains.top_domain_share` on ha-mcp's stored records give,
  before and after the change:

  | Measure | Before | After |
  |---|---|---|
  | Outside PRs (the account's own 3 leave) | 123 | 120 |
  | Outside PRs with a measured first reply | 108 | 105 |
  | Median first reply | 45 seconds | 3.09 hours |
  | Outside PRs closed without a reply | 14 | 15 |
  | Substantive review rate | 0.9314 | 0.94 |
  | Top commit email domain's share | 0.6120 of 1,513 commits | 0.6124 of 1,512 |

  So ha-mcp leaves the top of the fastest-replies list. Its landing and newcomer-cohort fields do
  not change, and no other project's record changes. The published 2026-11-05 snapshot keeps its
  stored values; the change applies from the next derive.
- **Test**: `tests/core/test_bot_identity.py::test_an_account_that_posts_review_commands_to_another_bot_never_answers_a_pr`.

## Reviewer

Not yet reviewed.
