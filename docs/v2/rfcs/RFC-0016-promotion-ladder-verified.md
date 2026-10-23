# RFC-0016: promotion-ladder-verified

## Motivation

Parameter 3.3 (`promotion_ladder`, `docs/v2/02-RANKING-DESIGN.md` section 4) awards `VERIFIED = 100`
when at least one account transitions from an external association (`NONE`, `FIRST_TIMER`,
`FIRST_TIME_CONTRIBUTOR`, `CONTRIBUTOR`) on an earlier pull request to `MEMBER` or `COLLABORATOR`
on a later pull request inside the 180-day `pr_window`.

On the 2026-10-21 published dataset (5,448 repositories):

- `promotion_ladder` measures `VERIFIED = 0 / 4,918` (`0.00%`), `AD_HOC = 4,600`, `CLOSED = 215`,
  `DOCUMENTED = 103`, and `530` `Unmeasured(collector_failure)`.
- Across all recorded `pr_window` envelopes in `sample-200`, zero `(repo, author_login)` pairs
  exhibit more than one distinct `authorAssociation` value.
- **Root cause (verified against live GitHub GraphQL):** GitHub evaluates
  `PullRequest.authorAssociation` at **query time** against the author's current relationship to the
  repository or organisation, not historically at `createdAt` or `mergedAt`. Every historical pull
  request by a promoted maintainer therefore returns their current status (`MEMBER` or
  `COLLABORATOR`), and a maintainer whose org membership is private returns `CONTRIBUTOR` on both
  pre-promotion and post-promotion pull requests:
  - `astral-sh/ruff`: `dhruvmanila`'s first PRs (#3627, #3743 in 2023-03) return
    `authorAssociation: "MEMBER"` today.
  - `vllm-project/vllm`: `DarkLight1337`'s first PRs (#4294, #4318 in 2024-04) return
    `authorAssociation: "MEMBER"` today.
  - `pydantic/pydantic`: `sydney-runkle` PR #7346 (2023-09-05, merged by `adriangb`) and PR #7358
    (2023-09-07, self-merged by `sydney-runkle` after receiving merge rights) both return
    `authorAssociation: "CONTRIBUTOR"`.

Because a single snapshot of `PullRequest.authorAssociation` is invariant across an author's pull
requests, `VERIFIED` was structurally unreachable (`0 / 4,918`).

## Method

`PR_WINDOW_QUERY` (`porchlamp.collect.github_graphql`) already records timestamped actor nodes on
every pull request in the 180-day window with **zero additional GraphQL queries or fields**:

- `author { __typename login ... on User { createdAt } }`
- `mergedBy { __typename login }` and `mergedAt`
- `timelineItems(itemTypes: [CLOSED_EVENT], first: 5) { nodes { ... on ClosedEvent { createdAt actor { __typename login } } } }`

Consistent with D-IN-2 (`porchlamp.core.gates.maintainers_from_prs`, RFC-0011), merging a pull
request (`mergedBy`) or closing another author's pull request (`ClosedEvent.actor != author`)
requires repository write/triage access regardless of whether the actor's GitHub org membership is
public.

We evaluated action-based promotion rules on a 16-repository live GraphQL sample (180-day window,
plus 360-day history validation on every candidate):

- **Naive action ordering (`first_outside_merged_at < first_maintainer_at`, no phase-duration
  floor)** produced 12 candidates across 5 of 16 repositories. Extending the window to 360 days
  showed that 6 of the 12 were false positives: existing maintainers who happened to have 1–4 of
  their own PRs merged by a fellow maintainer 2–26 days before their first merge of someone else's
  PR inside the 180-day window (`django/django`: `sarahboyce` 13 d, `smithdc1` 15 d;
  `matplotlib/matplotlib`: `story645` 2 d; `prometheus/prometheus`: `ywwg` 4 d; `pypa/pip`:
  `ichard26` 26 d; `rust-lang/rust-clippy`: `manishearth` 4 d).
- **Adding a 30-day minimum outside-contributor phase (`PROMOTION_MIN_OUTSIDE_DAYS = 30`)**
  eliminates all 6 short-gap false positives while retaining all 5 genuine promotions (69–136 d
  outside phase, 6–13 PRs merged by others before first maintainer action) plus 1 dormant maintainer
  returning after >270 days without maintainer actions (`tim-schilling` on `django/django`, 93 d
  outside phase).

## Change

- **`observed_promotions(prs)` in `porchlamp.collect.governance`:** an account `who` is a verified
  promotion (`VERIFIED`) in `pr_window` when all four conditions hold:
  1. **Bot exclusion (D-IN-1):** `who` is a non-bot human (`not gates.is_bot_actor_node`), and every
     access action by `who` in `prs` has a parseable timestamp.
  2. **Maintainer action on another author's PR (D-IN-2):** `who` merges (`mergedBy` at `mergedAt`)
     or closes (`closedEvents[].actor` at `closedEvents[].createdAt`) at least one pull request
     authored by another non-empty login (`pr_author != who`). `first_access` is the earliest
     timestamp across *all* of `who`'s access actions in `prs` (including any self-merge, since a
     self-merge already exercises write access).
  3. **Prior D-IN-3 age-eligible outside merge:** strictly before `first_access`
     (`mergedAt < first_access`), `who` authored at least one merged pull request in `prs` whose
     account age at `pr.createdAt` is at least `MIN_ACCOUNT_AGE_DAYS` (90 days) and that was merged
     by another non-bot human (`mergedBy.login != who`).
  4. **Minimum outside phase (`PROMOTION_MIN_OUTSIDE_DAYS = 30`):**
     `first_access - first_outside_merged_at >= timedelta(days=PROMOTION_MIN_OUTSIDE_DAYS)`.
- **Cut-window refusal (`#51` rule 5):** because action-based `VERIFIED` rests on the absence of
  earlier maintainer actions in the 180-day window (just as `CLOSED` rests on the absence of
  external merges), a `pr_window` cut short of 180 days (`pr_window_cut_short(prw)`) now refuses
  **every** `promotion_ladder` rung (`VERIFIED`, `CLOSED`, `DOCUMENTED`, `AD_HOC`) as
  `Unmeasured(collector_failure)` in `classify_promotion_ladder`, `derive_governance_fields`, and
  `porchlamp.derive.bundle._vocab_scalar_resolver`.

| Constant | Before | After |
|---|---|---|
| `PROMOTION_MIN_OUTSIDE_DAYS` | *(absent)* | `30` |

`CONSTANTS_HASH` moves:
`5a406aecf99b4939c18d7453b7b73bd3f9af0cd460140489568f0b23f91d4365` ->
`85df224143be2f7ee4a94f610b104b22f2c6a82f0ad35ddbb47ef9fb78ece50f`
(chained into [RFC-0017](RFC-0017-legal-ip-precedence.md)).

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump, matching the pre-release convention).

## Evidence

- **Fixture (`tests/fixtures/snapshot_50/`):** contains no `pr_window` envelopes; no score or rank
  changes.
- **Sensitivity (D-AGG-3):** not run; no pillar weight or continuous curve constant changed.
- **Live 16-repository validation (180-day window + 360-day history check):**
  - 4 of 16 repositories classify `VERIFIED` under `PROMOTION_MIN_OUTSIDE_DAYS = 30` (vs `0 / 16`
    under the old `authorAssociation` transition check):
    - `matplotlib/matplotlib`: `iccir` (7 outside merges over 82 d before first maintainer action;
      0 prior maintainer actions in 180–360 d) and `ayshih` (12 outside merges over 136 d; 0 prior
      actions in 180–360 d).
    - `pypa/pip`: `2ykwang` (13 outside merges over 69 d before merging PR #13965; 0 prior actions
      in 180–360 d).
    - `rust-lang/rust-clippy`: `commanderstorm` (6 outside merges over 50 d; 0 prior actions in
      180–360 d) and `gri-ffin` (12 outside merges over 136 d; 0 prior actions in 180–360 d).
    - `django/django`: `tim-schilling` (3 outside merges over 93 d; 2 prior actions in 180–360 d,
      residual dormant-maintainer edge case).
