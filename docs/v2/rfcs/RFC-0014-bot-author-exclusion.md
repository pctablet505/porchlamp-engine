# RFC-0014: bot-author-exclusion

## Motivation

Two defects in how `pr_window` envelopes were read at derive time, together with 14 service
accounts GitHub types as `User` that were absent from `BOT_LOGINS` (issues #57 and #66 section
2.3), let automated activity stand in for outside contributions and human maintainer replies:

1. **Stale stored `derived` list read instead of recomputing from PR nodes.** `collect_pr_window`
   writes both `prs` (the raw GraphQL PR nodes) and `derived = derive_pr_window(prs)` into each
   `pr_window` envelope. D-GATE-5 (`ghost_rate_prs_from_window`) and `closed_unread` recomputed
   per-PR classifications from `prs` at derive time, so RFC-0011 and RFC-0012 reached existing
   envelopes there. By contrast, 1.1 TTFHR and 4.3 (`derive_pr_window_fields`), 1.2 DR_90
   (`_dr90_decision_cohort`), 1.5 TTM_p90 (`_resolve_ttm_p90_hours`), 4.3's denominator `n`
   (`_resolve_substantive_review_rate`), D-GATE-6 review hand-off
   (`gate_evidence._review_handoff_in` and `fingerprint._review_handoff`), and
   `fingerprint._has_decided_external_pr_in_raw` read the envelope's stored `derived` list
   directly. Any envelope collected before a change to `BOT_LOGINS` or D-IN-2 kept its old
   classifications until refetched (`kubernetes-sigs/aws-load-balancer-controller`, fetched
   `2026-10-16T18:26:39Z` before RFC-0012 added `k8s-ci-robot`, published with a 11.2 s TTFHR
   median in the 2026-10-21 snapshot).
2. **Bot-authored pull requests counted as outside PRs in 1.1, 1.2, 1.5 and 4.3.** GitHub gives
   app and bot accounts (`dependabot`, `renovate`, `mergify`, `release-please`, `qlty-releases`,
   `github-actions`) an `authorAssociation` in `{"NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR",
   "FIRST_TIMER"}`. `derive_pr_window` sets both `is_external` and `is_bot_author`, and D-GATE-5,
   `closed_unread`, and D-GATE-6 review hand-off already required `is_external and not
   is_bot_author`, whereas 1.1, 1.2, 1.5, and 4.3 filtered on `is_external` alone. Pull requests
   opened by Dependabot, Renovate, Mergify, or release bots and answered or auto-approved within
   seconds by CI or a maintainer entered the external-PR population of all four parameters.
3. **Service accounts GitHub types as `User` not yet in `BOT_LOGINS`.** RFC-0012 required a login
   to carry a `bot`/`robot` token AND to have given the first response on outside PRs in the
   2026-10-17 data. That missed two sets of automated accounts:
   - accounts with a `bot` token whose sub-minute responses were on bot-authored PRs (`autheliabot`,
     `ciliumbot`) or that push automated sync PRs (`servo-bot`);
   - project-run CI, CLA, build, Gerrit-import, and auto-approver service accounts with no `bot`
     token in the login (`backstage-service`, `crate-jenkins`, `elasticsearchmachine`, `hlsjs-ci`,
     `istio-testing`, `mattermost-build`, `sqla-tester`, `tencent-adm`, `ultralyticsassistant`,
     `webkit-early-warning-system`, `yoshi-approver`).

## Method

1. **Published 2026-10-21 snapshot census:** 13 repositories in the 2026-10-21 export have a
   measured 1.1 TTFHR median under 60 seconds (12 rated): `authelia/authelia` (4.0 s, n=1,210),
   `zilliztech/deep-searcher` (5.4 s, n=12, unrated), `apache/doris` (6.1 s, n=792),
   `sqlalchemy/alembic` (6.1 s, n=23), `handsontable/handsontable` (6.8 s, n=548),
   `kubernetes-sigs/aws-load-balancer-controller` (11.2 s, n=74), `video-dev/hls.js` (11.9 s,
   n=120), `Heroic-Games-Launcher/HeroicGamesLauncher` (13.0 s, n=82), `istio/istio` (15.1 s,
   n=706), `Tencent/Hippy` (16.9 s, n=11), `Tencent/MMKV` (20.2 s, n=11), `qltysh/qlty` (31.0 s,
   n=51), and `googleapis/google-api-python-client` (51.8 s, n=45).
2. **Live PR-window and account verification (40 GraphQL points, 19 REST user calls):** for those
   13 repositories plus the 8 non-`bot`-named accounts deferred in RFC-0012 and the #57 audit
   candidates (`WebKit/WebKit`, `elastic/elasticsearch`, `cilium/cilium`, `servo/servo`,
   `backstage/backstage`, `ultralytics/ultralytics`, `crate/crate`, `mattermost/mattermost`), we
   fetched recent PR windows and inspected every sub-60 s responder's GitHub profile, recent PR
   comments/reviews, and commit/PR history:
   - **Verified service accounts added (14):**
     - `autheliabot` (`name='Authelia[bot]'`, `bio="I'm helping my fellow contributors"`): 46 of 46
       external PRs in the 50-PR `authelia/authelia` sample are `renovate` PRs answered within 4 s
       by `autheliabot` approving reviews; its 47 authored PRs and 81 commits are automated
       dependency updates.
     - `backstage-service` (`name='Backstage Service Account'`, `company='@backstage'`): approves
       `dependabot` and `renovate` PRs in `backstage/backstage` (median 20.0 s over 9 external PRs
       with bot authors included vs 26,156.5 s over 2 human external PRs); its 2,128 commits and
       2,927 PRs are automated `Version Packages` releases.
     - `ciliumbot` (`cilium/cilium`): posts `/test` on `cilium-renovate` and `cilium-release-bot`
       PRs within seconds (median 165.0 s over 11 external PRs with bot authors included vs
       3,077.5 s over 4 human external PRs; 0 PRs authored).
     - `crate-jenkins` (`name='crate-jenkins'`, `company='@crate'`, 0 PRs authored, 2,474 issue
       comments): posts `'Can one of the admins verify this patch?'` and CI statuses on
       `crate/crate` PRs (12 `mergify` PRs and 1 human PR under 60 s; median 5.0 s with bot PR
       authors vs 933.0 s without).
     - `elasticsearchmachine` (`elastic/elasticsearch`): posts CI failure tables on 9 of 13 human
       external PRs under 60 s (median 43.0 s); its 2,818 commits and 116 PRs are automated
       `[renovate] Update branches config` and `Mute ...Test` commits.
     - `hlsjs-ci` (`video-dev/hls.js`, 0 PRs authored): posts automated reviews on 14 `renovate`
       PRs under 60 s (median 10,002.5 s over 30 external PRs with bot authors included vs
       84,276.5 s over 16 human external PRs).
     - `istio-testing` (`name='Istio Automation'`, `company='Istio'`): posts welcome/test/cherrypick
       templates on `istio/istio` PRs under 60 s; its 1,479 commits and 3,438 PRs are
       `Automator: update ...` dependency bumps.
     - `mattermost-build` (`name='Mattermost Build'`, `company='Mattermost, Inc'`): posts merge and
       cherry-pick automation comments on `mattermost/mattermost` PRs (8 under 60 s; 6,845
       `Automated cherry pick of #...` PRs authored).
     - `servo-bot` (`servo/servo`, 0 issue comments, 1 package-sync PR): automated sync account.
     - `sqla-tester` (`sqlalchemy/sqlalchemy`, `sqlalchemy/alembic`, 7,872 issue comments): posts
       Gerrit import notices and issue-link templates within seconds on 15 of 34 human external PRs
       in `sqlalchemy/alembic`.
     - `tencent-adm` (`name='腾讯开源'`, `company='Tencent'`, 5,398 issue comments): posts Tencent CLA
       notices within 17-23 s on 14 of 14 external PRs in `Tencent/Hippy` and 12 of 21 in
       `Tencent/MMKV`.
     - `ultralyticsassistant` (`name='Ultralytics Assistant'`, `bio='Ultralytics AI Assistant...'`,
       22,201 issue comments, 1,200 analytics PRs): posts automated welcome and merge comments on
       12 of 16 human external PRs in `ultralytics/ultralytics` (median 42.5 s).
     - `webkit-early-warning-system` (`name='EWS'`, `company='Apple'`,
       `bio='Automated account administered by WebKit for pre-commit testing of pull-requests.'`,
       0 commits, 0 PRs): answers 27 of 27 human external PRs in `WebKit/WebKit` within 9.0 s.
     - `yoshi-approver` (`name='Yoshi Approver'`,
       `bio='This is a robot account that approves things :stampy:'`, 0 PRs authored): approves 14
       of 25 human external PRs in `googleapis/google-api-python-client` under 60 s (median 54.0 s).
   - **Not added (verified human maintainers whose accounts also post webhook templates):**
     - `hello-stephen` (Dongyang Li, `@SelectDB`, 92 human commits and 196 PRs in `apache/doris`);
     - `flavioislima` (Flávio Fearn, creator of `Heroic-Games-Launcher`, 809 PRs authored, 38
       repositories);
     - `qunabu` (Mateusz Wojczal, `@handsontable`, 1,569 PRs authored, 62 repositories);
     - `Mythie` (Lucas Smith, `documenso`, 502 PRs authored, 50 repositories);
     - `feng626` (`@jumpserver` core maintainer, 148 PRs authored).

     Adding a human maintainer to `BOT_LOGINS` would strip their real commits (D-GATE-4 vitality,
     3.2 elephant factor), PRs, and reviews across every repository. Separating webhook comments
     posted through a personal token on a human maintainer's account requires per-comment
     `viaApp` attribution in the GraphQL query (tracked as a follow-up collection schema change).

## Change

- **One definition of an outside PR (`porchlamp.core.gates.is_outside_pr`).** A recorded PR node is
  an outside PR when its `authorAssociation` (case-insensitive) is in
  `gates.EXTERNAL_ASSOCIATIONS` (`{"NONE", "FIRST_TIMER", "FIRST_TIME_CONTRIBUTOR",
  "CONTRIBUTOR"}`) and its `author` node is not a bot (`gates.is_bot_actor_node`: `__typename ==
  "Bot"`, login ending in `[bot]`, or lower-cased login in `constants.BOT_LOGINS`; a deleted
  account with `author: None` is not a bot).
- **Every `pr_window` consumer reads the recorded PR nodes at derive time (`#57`).**
  - `porchlamp.collect.github_graphql.derive_pr_window_fields` recomputes `derive_pr_window(nodes)`
    from `prw["prs"]` and filters to `d["is_external"] and not d["is_bot_author"]` for 1.1 TTFHR
    (`pr_window_ttfhr_median_hours`, `pr_window_external_measured_latency_count`) and 4.3
    (`pr_window_substantive_review_rate`). The envelope's stored `derived` list is never read.
  - `porchlamp.derive.bundle._resolve_ttm_p90_hours` (1.5), `_dr90_decision_cohort` (1.2), and
    `_resolve_substantive_review_rate` (4.3's `n`) read `data["prs"]` with `gates.is_outside_pr`
    instead of joining against `data["derived"]`.
  - `porchlamp.derive.gate_evidence.outside_pr_authors`,
    `porchlamp.derive.gate_evidence._review_handoff_in`,
    `porchlamp.collect.fingerprint._review_handoff`, and
    `porchlamp.collect.fingerprint._has_decided_external_pr_in_raw` classify outside PRs from
    `data["prs"]` under the current rules, never from `data["derived"]`.
- **14 service accounts added to `porchlamp.core.constants.BOT_LOGINS`** (and mirrored in
  `web/app/livecheck.js` `KNOWN_BOT_LOGINS`, enforced by `tests/test_ui_livecheck_port.py`):
  `autheliabot`, `backstage-service`, `ciliumbot`, `crate-jenkins`, `elasticsearchmachine`,
  `hlsjs-ci`, `istio-testing`, `mattermost-build`, `servo-bot`, `sqla-tester`, `tencent-adm`,
  `ultralyticsassistant`, `webkit-early-warning-system`, `yoshi-approver`.

| Constant | Before | After |
|---|---|---|
| `BOT_LOGINS` | 95 logins (20 generic + 75 RFC-0012 service accounts) | 109 logins (+14 RFC-0014 service accounts) |

`CONSTANTS_HASH` moves:
`be7023c09aa5514472d2434d33527449db5a490152f0a3e1ea2defaf052ab91a` ->
`4116987bb0ea32861181dff41647832e4fbc9fae75dc59fbd7436ac534e96d6b`
(pinned in `tests/core/test_frozen.py`, with `_FROZEN_TABLE`'s `BOT_LOGINS` row updated to `109`).

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump, matching the RFC-0001..0013
pre-release convention).

## Evidence

- **Fixture** (`tests/fixtures/snapshot_50/`): it holds no `pr_window` envelopes and its bundles
  are already derived, so 0 of 50 repositories change rank, composite, pillar score, coverage, or
  tier (`rated=0, gated=18, unrated=32` unchanged). Only `ScoreRecord.constants_hash` moves, which
  moves `records_hash` (`27b0ef6f2ca14e3fefb8a255b83d80b7a9b71bfe25bae6c75aaea2748514349e` ->
  `0337d4f09687d1df7b3d30967cf31b0984828f330e160c9047eb016d01bdacba` in `expected.json` and
  `tests/snapshot/test_store_cli.py`), restamped by
  `scripts/build_fixture_snapshot.py --rewrite-expected`.
- **Sensitivity** (D-AGG-3): `tests/audit/test_sensitivity_fixture.py` passes unchanged; no weight
  or curve constant changes.
- **Anchors** (D-VER-4): `tests/audit/test_anchors_fixture.py` passes unchanged; `torvalds/linux`,
  `gcc-mirror/gcc`, `kubernetes/kubernetes`, `vllm-project/vllm`, and `keras-team/keras-hub` keep
  identical gate, routing, rating, coverage, and tier outcomes on the fixture.
- **Live PR-window samples** (see Method above):
  - Bot-authored PR exclusion removes automated PRs from 1.1/1.2/1.5/4.3: on the 50-PR
    `authelia/authelia` sample, external PRs drop from 46 (all `renovate`, TTFHR median 4.0 s) to
    0; on `video-dev/hls.js`, from 30 (TTFHR median 10,002.5 s) to 16 (84,276.5 s); on
    `qltysh/qlty`, from 4 (TTFHR median 1,341.5 s) to 2 (169,771.0 s); on the 30-PR
    `backstage/backstage` sample, from 9 (TTFHR median 20.0 s) to 2 (26,156.5 s); on
    `crate/crate`, from 16 (TTFHR median 5.0 s) to 4 (933.0 s); on `cilium/cilium`, from 11
    (TTFHR median 165.0 s) to 4 (3,077.5 s); on `mattermost/mattermost`, from 16 (TTFHR median
    12.5 s) to 9 (395.0 s).
  - Recomputing from `prs` at derive time rather than reading the envelope's stored `derived` list
    applies the 109-login `BOT_LOGINS` and bot-author exclusion immediately to already-collected
    `pr_window` envelopes without waiting for a refetch.
- **COULD NOT VERIFY:** full-universe rebuild across all stored `raw/pr_window` envelopes (raw
  envelopes are not present on this machine).

## Reviewer

Pending: independent review in the #66 PR.
