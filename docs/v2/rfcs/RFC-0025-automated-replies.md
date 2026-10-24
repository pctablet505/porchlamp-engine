# RFC-0025: automated-replies

## Motivation

D-IN-2 counts a review, or a comment of 40 characters or more, from a non-bot account that GitHub
shows as `MEMBER`, `OWNER` or `COLLABORATOR` (or that merged or closed another author's PR in the
window) as the project's response. D-IN-1 decides bot identity by account type and an exact login
list (`BOT_LOGINS`, RFC-0012, RFC-0014). Both miss a person's account that also runs automation:
the automation's comments are posted as the person, so the account is neither a bot nor in any
list, and its comments count as human replies.

Seen on the 2026-10-23 data (10,998 `pr_window` envelopes):

- `handsontable/handsontable`: the maintainer account `qunabu` posts "Task linked: DEV-1780 ..." under
  each PR seconds after it opens (1,060 times), from a ticket-link integration. The project's
  median first reply is 6.8 s and 1.1 TTFHR scores 100; read without those comments the median is
  11.9 h and 516 of 522 answered outside PRs got a reply within 14 days.
- `Heroic-Games-Launcher/HeroicGamesLauncher`: the account `flavioislima` posts a CLA notice ("All
  contributors have signed the CLA") a median of 12 s after each PR opens.
- `istio/istio` (`istio-testing`), `falcosecurity/falco` (`poiana`), `NomaDamas/k-skill`
  (`vkehfdl1`, a long templated advisory reply), `palantir/blueprint` (`palantirtech`), and others.

An identity list cannot keep up with these (each is a different account on a different project)
and would also discard the same accounts' real reviews, as `qunabu` also reviews by hand.

## Method

What marks the automation is the comment itself. For each PR window, group every review or comment
text by `(account, template)`, where the template is the first `AUTOMATION_TEMPLATE_WORDS = 2` words
of the text after `normalize_review_text` removes links, `@mentions`, `#123` references and digits
(RFC-0018's normaliser), so "Hello @alice, thank you" and "Hello @bob, thank you" are one template.
Take each group's first such reply on each PR. The group is automation when:

- it spans at least `AUTOMATION_MIN_PRS = 20` distinct PRs, and
- at least `AUTOMATION_FAST_SHARE = 0.80` of those first replies came within
  `AUTOMATION_FAST_SECONDS = 60` of the PR opening.

A person does not answer 80% of 20 PRs inside a minute by hand. Only the template is automation:
the same account's other replies, and its reviews with other wording, still count. Reviews with an
empty body have no template and are never grouped (an approval stays a response). Bot accounts
(D-IN-1) are already excluded and are skipped.

Measured over all 10,998 windows with 2 words: 339 groups across 231 accounts qualify, and
reading the 22 whose names do not look like bots found only templated notices, status reports,
CLA greetings, ticket links and commands to other bots. Three words gave 347 groups, 233 accounts
(two more account-in-project pairs), so the choice is not sensitive.

## Change

- `porchlamp.core.gates.reply_template` and `automated_replies(prs)` implement the rule.
- `maintainer_logins(prs)` now returns `Maintainers`, a `frozenset` of logins that also carries the
  window's `automated` pairs. `is_maintainer_response` does not count a node whose
  `(login, template)` is automated. Every reply measure takes the window's maintainers
  (1.1 TTFHR, 1.2 DR_90, 1.4 GR_14 and D-GATE-5, 1.5 TTM_p90, 4.3, `closed_unread`, D-GATE-6
  hand-off, the reply sample), so the rule reaches all of them through one object. A plain
  `frozenset` of logins still works and means no automation.
- 4.3's review branch (`has_substantive_review_or_comment`) reads reviews without the maintainer
  test; its comment branch uses `is_maintainer_response` and so is covered. Reviews there are
  not filtered by the rule (a bot-like review with a body is not seen in the data); this is the
  one reader left out.

| Constant | Before | After |
|---|---|---|
| `AUTOMATION_MIN_PRS` | - | 20 |
| `AUTOMATION_FAST_SECONDS` | - | 60 |
| `AUTOMATION_FAST_SHARE` | - | 0.80 |
| `AUTOMATION_TEMPLATE_WORDS` | - | 2 |

`CONSTANTS_HASH` moves:
`d8c067b9e928b138beb68c9909d605b5737a1885275c786e0fbe8d9050b8a1c2` ->
`861c2259fbee531957b102e4dbfa11f5c5ab532d9cd75a3df21fe1e2c203fcf7`
(pinned in `tests/core/test_frozen.py`, with four new `_FROZEN_TABLE` rows).

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump, matching the RFC-0001..0024
pre-release convention).

## Evidence

Read-only run over the 2026-10-23 data root with the rule on and off (`derive_pr_window_fields`
and the D-GATE-5 breakdown per project):

- 15 of 10,998 projects change; 9 of them are ranked in the 2026-10-24 snapshot. The other 10,983
  are unchanged, because most automation accounts GitHub shows as no `MEMBER`, `OWNER` or
  `COLLABORATOR` were never counted as maintainers.
- Median first reply, off -> on: `handsontable` 0.002 h -> 10.95 h, `HeroicGamesLauncher`
  0.004 h -> 26.0 h, `istio/istio` 0.57 h -> 26.7 h, `falco` 0.024 h -> 1.46 h, `NomaDamas/k-skill`
  0.24 h -> 8.6 h, `palantir/blueprint` 1.84 h -> 32.2 h, `apache/doris` 0.002 h -> 3.4 h.
- Black-hole verdict changes for one project (`mailcow/mailcow-dockerized`, already gated as
  `non_software`).
- **Fixture** (`tests/fixtures/snapshot_50/`), regenerated with
  `scripts/build_fixture_snapshot.py --rewrite-expected`: it holds no `pr_window` envelopes, so
  `rated=0, gated=18, unrated=32` is unchanged and 0 of 50 repositories change rank, composite,
  tier or gate outcome; only `ScoreRecord.constants_hash` moves (`records_hash`:
  `aebc001b33f82296df3c8b1d166faf9496b84d7babf207856f9f05a4f8a1c910` ->
  `b8e2f5b32d07c676fe14e7e16700a85c3bc2f776da6dfe834afca39dd55d907d`; `merkle_root`:
  `3ba128e51f3480f9458efe59c2ac39d328d74fa97416337ae169e5bd4cfb8fc7` ->
  `3ae40b8b6c10fd885fc0c4bb30fa0ddff7b802fcc2a224526a8cbc6ee9ecabb5`).
- No score or rank is recomputed here (a full derive and build is needed for that).
- **COULD NOT VERIFY:** rank movement of the 9 ranked projects; whether other windows (not yet
  collected) hold templates the 2-word key splits.

## Reviewer

Not yet reviewed.
