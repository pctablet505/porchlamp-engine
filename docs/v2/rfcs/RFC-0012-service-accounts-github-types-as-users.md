# RFC-0012: service-accounts-github-types-as-users

## Motivation

D-IN-1 (`docs/v2/02-RANKING-DESIGN.md` section 3) knows a bot by `__typename == "Bot"`, a
login ending in `[bot]`, or an exact list of 20 generic logins. Service accounts that GitHub
types as users are on none of these: `rustbot`, `k8s-ci-robot`, `flinkbot` and others. With a
maintainer association, or a merge they perform, they count as D-IN-2 responders. They answer
outside pull requests within a minute with a reviewer assignment, a CI report or a welcome
template, so a project that never answers reads as responsive. They also count as people
where D-IN-1 decides the rest: outside PRs they open, and commits they author (D-GATE-4
vitality, the bus and elephant factors).

The independent check of 30 black-hole verdicts raised it while RFC-0011 was being written.
Lead decision, 2026-10-17: fix it in a separate change, as an exact login list excluded by
D-IN-1 everywhere, each login backed by evidence from the recorded data.

## Method

Applied to every recorded `pr_window` envelope of the 2026-10-17 data (10,998 windows), with
RFC-0011's D-IN-2:

- **Candidates:** accounts D-IN-1 does not already know as bots that give the first D-IN-2
  response on at least one outside PR, so that excluding them moves a measurement.
- **The login says it is a bot:** one of its tokens (split on `-`, `_`, `.` and digits) is
  `bot` or `robot`, or ends in `bot`. This keeps people named Talbot, Chabot or Sobot out.
- **And it behaves like one**, by at least one of:
  1. at least half of its substantive texts (at least 5) repeat a normalised template it
     posted at least 5 times. Normalising lower-cases the text and replaces URLs, @mentions,
     #references, commit hashes, numbers and any token holding a digit;
  2. at least 80% of its first answers to outside PRs (at least 5) came within 5 minutes of the
     PR opening.
- **Read by hand:** each selected account's two commonest templates and two sample texts. All
  75 are automated: CI, benchmark and size reports, approval notifiers, reviewer assignment,
  release and backport notices, and welcome, CLA and contribution-policy templates.

Not added:

- **Eight accounts that behave like bots but have no bot name.** Their texts are at least 90%
  templated, and at least 80% of their first answers came within 5 minutes:
  - `webkit-early-warning-system` (386 first answers);
  - `hello-stephen` (321);
  - `aws-cdk-automation` (238);
  - `mervinpraison` (118);
  - `devopshazelcast` (34);
  - `crate-jenkins` (32);
  - `palantirtech` (31);
  - `simlarsen` (10).

  D-IN-1 would also drop their own commits and pull requests (counts from the recorded data):
  - **no commits or PRs, pure service accounts:** `webkit-early-warning-system`,
    `crate-jenkins`, `palantirtech`;
  - **automation that also commits:** `aws-cdk-automation` (74 commits in 365 days, 43 PRs) and
    `devopshazelcast` (16 commits);
  - **people whose accounts also run automation:** `mervinpraison` (289 commits, 94 PRs; the
    project's owner), `simlarsen` (90 commits, 480 PRs) and `hello-stephen` (18 commits, 6 PRs).

  Adding any of them is a separate decision, by name.
- **Accounts with a bot name and no behaviour evidence.** Some are people (`psobot`,
  `brettchabot`, `g-talbot`, `labbott`, `boenrobot`, `bbotella`). Others are AI review agents
  whose text varies from PR to PR:
  - `all-hands-bot` (140 first answers);
  - `qwen-code-review-bot` (97);
  - `worktrunk-bot` (44);
  - `localai-org-maint-bot` (38);
  - `knsv-bot` (19);
  - `qdrant-cloud-bot` (10).

## Change

- `BOT_LOGINS` in `porchlamp.core.constants` is now the one D-IN-1 list, and it is hashed.
  `porchlamp.core.commit_domains.KNOWN_BOT_LOGINS` and `porchlamp.collect.github_graphql.BOT_LOGINS`
  were two unhashed copies; both are now this constant. It keeps the 20 generic logins and
  adds the 75 service accounts:
  - **CI, build, benchmark and size reports:** `flinkbot`, `hudi-bot`, `sre-ci-robot`,
    `babel-bot`, `shaka-bot`, `highsoft-bot`, `voxel51-bot`, `moz-tools-bot`, `agones-bot`,
    `dapr-bot`, `geky-bot`, `aseprite-bot`, `theguild-bot`, `gradio-pr-bot`,
    `posit-snyk-bot`, `cherry-markdown-bot`, `nodejs-github-bot`.
  - **Approval notifiers and review tooling:** `k8s-ci-robot`, `kubeedge-bot`, `volcano-sh-bot`,
    `karmada-bot`, `kruise-bot`, `tekton-robot`, `jenkins-x-bot`, `rustbot`, `rust-rfcbot`,
    `minikube-bot`, `eth-bot`, `qiskit-bot`, `drahtbot`, `ydbot`, `expo-bot`, `zulipbot`,
    `litbot-9000`, `aws-sam-cli-bot`, `tempoxyz-bot`, `lestarch-autobot`, `workweave-bot`,
    `zerxlabbot`, `openlibrary-bot`.
  - **Merge, release and backport bots:** `pytorchmergebot`, `ti-chi-bot`, `lightdash-bot`,
    `frappe-pr-bot`, `cosmistack-bot`, `react-native-community-bot`, `hurl-bot`, `prowler-bot`,
    `geoserver-bot`, `qgis-bot`, `giteabot`, `atuin-bot`, `element-bot`, `coolbear-bot`.
  - **Welcome, CLA, policy and triage templates:** `raycastbot`, `shiftbot`, `stdlib-bot`,
    `ansibot`, `adk-bot`, `kangal-bot`, `is-a-dev-bot`, `astrobot-houston`, `haystackbot`,
    `docs-bot`, `bitwarden-bot`, `apereocas-bot`, `buggiebot`, `ladybird-bot`,
    `mysql-oca-bot`, `juce-push-bot`, `antennapod-bot`, `robot-ci-heartex`,
    `cypress-app-bot`, `nccl-bot`, `gopherbot`.
- The browser live check's copy (`web/app/livecheck.js` `KNOWN_BOT_LOGINS`) follows.
  `tests/test_ui_livecheck_port.py` holds the two equal.

| Constant | Before | After |
|---|---|---|
| `BOT_LOGINS` | *(absent; 20 logins unhashed in `porchlamp.core.commit_domains` and again in `porchlamp.collect.github_graphql`)* | 95 logins |

`CONSTANTS_HASH` moves:
`5dc93c2f7bd97ebe46185da3eab34916d2f203f366c4157b964b96b4a20792c7` ->
`6f95d75a0aa5427288d3e7e8eadc5b9477d3a1816d636ab848b9defc7d6cea5a`
(pinned in `tests/core/test_frozen.py`, with a `_FROZEN_TABLE` row for the list's size).

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump, matching the RFC-0001..0011
pre-release convention).

## Evidence

- **Fixture** (`tests/fixtures/snapshot_50/`): it holds no `pr_window` envelopes and its
  bundles are already derived, so the list has no input there. Scoring it with the old and the
  new code gives byte-identical records apart from `ScoreRecord.constants_hash`. Only
  `records_hash` moves, in `expected.json` and `tests/snapshot/test_store_cli.py`.
- **Sensitivity** (D-AGG-3): not run. No weight or curve constant changes.
- **Anchors** (D-VER-4): `torvalds/linux`, `gcc-mirror/gcc`, `kubernetes/kubernetes` and
  `vllm-project/vllm` have the same gate, routing, rating, coverage and tier before and after.
- **Recorded data**: the 2026-10-17 data, derived, built, stored, summarised and exported on a
  scratch copy with RFC-0011's code (before) and with this change (after):

  | | before | after |
  |---|---|---|
  | judged on D-GATE-5 | 2,745 | 2,745 |
  | black holes | 632 (23.02%) | 638 (23.24%) |
  | 1k-10k stars | 312 of 1,687 | 316 of 1,687 |
  | 10k-50k stars | 280 of 943 | 282 of 943 |
  | 50k+ stars | 40 of 114 | 40 of 114 |
  | rated | 4,402 | 4,396 |

  - Under RFC-0011's rule the 75 accounts give the first response on 8,004 outside PRs.
  - Six verdicts flip, all from rated to black hole. Each project's own bot had been its answer:

    | project | unanswered before | unanswered after | bot that had answered |
    |---|---|---|---|
    | kubeedge/kubeedge | 37/391 | 317/358 | `kubeedge-bot` (405 first answers) |
    | openkruise/kruise | 0/73 | 52/60 | `kruise-bot` (146) |
    | karmada-io/karmada | 3/207 | 106/184 | `karmada-bot` (257) |
    | kubernetes-sigs/kustomize | 36/70 | 44/70 | `k8s-ci-robot` (26) |
    | mysql/mysql-server | 34/74 | 49/74 | `mysql-oca-bot` (21) |
    | zilliztech/GPTCache | 0/11 | 10/10 | `sre-ci-robot` (13) |

    The population shrinks where a bot opened PRs itself, or where an author's early
    self-close is no longer preceded by a bot's "answer".
  - On the 578 PRs that became unanswered, no maintainer wrote anything within 14 days on 546.
    On the other 32 the only maintainer comment is short:
    - `/codex` (19), a request for an AI review;
    - `/ok-to-test` (6);
    - `/close` (2), which the bot then carried out;
    - 3 other short comments.

    A `/close` a bot carries out no longer counts as a maintainer's close; D-IN-2 counts
    neither the short command nor the bot's close.
  - No project becomes stale (D-GATE-4) for losing bot commits.
  - The PRs 12 of the accounts opened themselves leave the outside population: 701 of 615,200
    outside PRs (`apereocas-bot` 251, `haystackbot` 115, `jenkins-x-bot` 66 and others).
    Distinct outside authors go from 148,801 to 148,789.
  - `closed_unread` flags 608 -> 617 projects. The one project `speed_without_decisions` flagged,
    kubeedge/kubeedge, drops out of the ranking: its speed was its bot's, and it is now a black
    hole.
  - The rebuilt snapshot exports (`scripts/export_static.py`, exit 0).
    `scripts/verify_static_export.py --stats` passes every data check: 13 planted defects
    caught, 4,396 project pages, the methodology and report pages, and the stats file
    recomputed from the database and the data. It fails only on the hand-written copy in
    `index.html`, `llms.txt` and `feed.xml`, which states the published numbers (34 statements;
    30 on RFC-0011's rebuild). That copy changes with the publish.

## Reviewer

Pending: the lead session reviews the PR. The code was written by a separate session.
