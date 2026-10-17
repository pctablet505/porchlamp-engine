# RFC-0011: black-hole-gate-whole-window-and-access

## Motivation

An independent check of 30 black-hole verdicts of the 2026-10-17 snapshot against GitHub
(D-GATE-5, `docs/v2/02-RANKING-DESIGN.md` section 2) found that the published rule
reproduces almost exactly where the record is complete, and found two defects:

- **Cut windows.** The collector records at most the newest 500 PRs of the 180-day window
  (`porchlamp.collect.github_graphql.PR_CAP`). For a busy project that covers only the newest
  weeks, yet D-GATE-5 judged it as the window. 86 of the 720 black holes sat on the cap; one
  of them passes over the full window.
- **Maintainers GitHub labels `CONTRIBUTOR`.** D-IN-2 counted a response only from a member,
  owner or collaborator, or an account that merged a PR in the window. Apache committers and
  Signal staff show as `CONTRIBUTOR` and land changes outside GitHub merges, so their timely
  reviews and replies never counted. Closing a PR someone else opened takes triage or write
  access, so it shows who the project's own people are.

And three smaller ones: the 40-character comment minimum undercounts Chinese, Japanese and
Korean replies; Meta's import notice was missing from the D-GATE-6 hand-off notices; and two
PRs were reported as answered by bots (see Evidence: they were not).

Owner, 2026-10-17: "fix it". The rules below are the lead's decisions in the task brief.

## Change

1. **D-GATE-5 judges only a record that covers the window.** A `pr_window` record the
   collector cut at its cap (`truncated` true) holds every PR opened since its oldest recorded
   PR and none before. It covers the gate's window only when that PR opened before the window
   start (`now - PR_WINDOW_DAYS`). An uncut record covers its own window (`window_anchor`
   minus `window_days`), so it covers the gate's window when its own window opens at or before
   the gate's. A record that does not say whether it was cut cannot show that it covers the
   window. A record that does not cover the window leaves D-GATE-5 UNDECIDED, never judged.
   The time from which it holds every PR (the cut-off) is recorded with the gate evidence
   (`GateEvidence.pr_window_complete` and `pr_window_cutoff`, written to the snapshot
   manifest), as D-GATE-6 already treats a cut record as incomplete. The rule is
   `porchlamp.core.gates.pr_window_coverage`. The collector's own black-hole confirmation
   (`porchlamp.collect.fingerprint.is_confirmed_black_hole_in_raw`) applies the same rule.
   The brief's wording also named "a record whose oldest PR is younger than the window start".
   Read literally, that is every record: the collector stops at the first PR older than the
   window and does not keep it. So the rule above tests whether the record was cut, and
   whether its own window opens in time.
2. **D-IN-2 counts the repository's own people.** A review in any state, or a substantive
   comment, answers a PR when its author is not the PR's author, not a bot (D-IN-1), and is a
   member, owner or collaborator, or merged a PR in the recorded window, or closed a PR another
   account opened there. Bots never count, as responders or as evidence. The definition now
   lives in one place, `porchlamp.core.gates` (`maintainer_logins`,
   `is_maintainer_response`, `first_substantive_response`). The collector's per-PR rows,
   GR_14 and D-GATE-5, the served black-hole evidence and the `closed_unread` detector all
   read it; before, the served evidence had its own copy.
3. **A comment's length counts each Chinese, Japanese or Korean letter three times.** It is
   measured on the comment's text as GitHub renders it (`bodyText`, no markup, so an image
   alone has no text), with Han ideographs, kana and Hangul counted
   `SUBSTANTIVE_COMMENT_CJK_WEIGHT` times and everything else once. The weight is 3 because
   those scripts say in one letter about what Latin text says in three. Short maintainer
   comments in CJK text on the recorded data read as substantive at that weight: "请你们停止这种行为，这不是你们的实验场！" (about 66 Latin
   characters in English) counts at 3 and not at 2. "感谢，LGTM！很好的项目" ("Thanks, LGTM!
   Very good project", 31 Latin characters) counts at neither. The cited example, 16 Han
   characters and "PR", scores 50 at 3 and 34 at 2.
4. **Meta's import notice hands review off.** `meta-codesync` tells outside PRs "This pull
   request has been imported. If you are a Meta employee, you can view this in D…", or
   "@user has imported this pull request. …", and the change then lands internally. Both
   wordings join `GATE6_HANDOFF_NOTICES`.

| Constant | Before | After |
|---|---|---|
| `SUBSTANTIVE_COMMENT_MIN_CHARS` | *(absent; `40` unhashed in `porchlamp.collect.github_graphql` and again in `porchlamp.derive.gate_evidence`)* | `40` |
| `SUBSTANTIVE_COMMENT_CJK_WEIGHT` | *(absent)* | `3` |
| `GATE6_HANDOFF_NOTICES` | 3 phrases | + `"this pull request has been imported"`, `"has imported this pull request"` |

`CONSTANTS_HASH` moves:
`6e4f3e8e1d0c0fcd829d9c8c386a65179eadb5f936595f96e9eba98712d55b94` ->
`5dc93c2f7bd97ebe46185da3eab34916d2f203f366c4157b964b96b4a20792c7`
(pinned in `tests/core/test_frozen.py`, with `_FROZEN_TABLE` rows for the three constants).

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (no bump, matching the RFC-0001..0010
pre-release convention).

Considered and decided against:

- **Commit authors as maintainers: not used** (lead decision, 2026-10-17). The task brief also
  named "recorded as committing to the default branch in `raw/commits_365d`". That file
  records each commit's author login (`porchlamp.collect.commits.shape_commit_rows`), not who
  committed it. A PR merged on GitHub keeps its outside author as the commit author, so that
  rule would count every contributor whose PR landed: on the 30 checked projects it adds 19
  responders to Pumpkin-MC/Pumpkin, 6 to JuliusBrussee/caveman, 5 to run-llama/llama_index
  and 3 to crewAIInc/crewAI, most of them outside contributors.

Left to a separate change (lead decision, 2026-10-17):

- **Bots GitHub types as users.** D-IN-1 knows a bot by `__typename == "Bot"`, a `[bot]`
  suffix or an exact login list. Service accounts with a user type and a maintainer
  association or a merge (for example `rustbot`, `k8s-ci-robot`, `flinkbot`) still answer
  PRs. On the recorded data such accounts give the first response on thousands of outside
  PRs. Changing D-IN-1 moves every parameter that reads it, so a follow-up change extends
  the exact login list, each login backed by evidence from the recorded data.

The stats file the report reads counts the projects this leaves undecided:
`gate5_undecided_cut_record` (`porchlamp.snapshot.stats`) counts projects that passed
gates 1 to 4 and are undecided on D-GATE-5, with a measured share of at least the minimum
population, no hand-off and the rule inputs recorded, only because their record does not
cover the window.

## Evidence

- **Fixture** (`tests/fixtures/snapshot_50/`): it holds no `pr_window` envelopes, so the
  rules have no input there. Scoring it with the old and the new code gives byte-identical
  records apart from `ScoreRecord.constants_hash`. Only `records_hash` moves, in
  `expected.json` and `tests/snapshot/test_store_cli.py`.
- **Sensitivity** (D-AGG-3): not run. No weight or curve constant changes.
- **Anchors** (D-VER-4): `torvalds/linux`, `gcc-mirror/gcc`, `kubernetes/kubernetes` and
  `vllm-project/vllm` have the same gate, routing, rating, coverage and tier before and after.
- **Recorded data**: the 2026-10-17 data (`phone-2026-10-17`, as_of
  2026-10-17T05:35:00+00:00), derived, built, stored and summarised on a scratch copy, with the
  old code (which reproduces the published stats file exactly) and with the new code:

  | | old | new |
  |---|---|---|
  | judged on D-GATE-5 | 3,613 | 2,745 |
  | black holes | 720 (19.93%) | 632 (23.02%) |
  | 1k-10k stars | 329 of 2,065 (15.93%) | 312 of 1,687 (18.49%) |
  | 10k-50k stars | 323 of 1,307 (24.71%) | 280 of 943 (29.69%) |
  | 50k+ stars | 68 of 240 (28.33%) | 40 of 114 (35.09%) |
  | rated | 4,322 | 4,402 |
  | routed off-platform | 8 | 12 |
  | `closed_unread` flags | 646 | 608 |

  - 1,238 records do not cover the window (1,245 are cut at the cap; 7 of those reach back
    past the window start). 869 projects that were judged lose their verdict because of it:
    86 black holes and 783 passes. One of them, facebook/pyrefly, also hands review off now.
  - The new responders clear signalapp/libsignal (10 of 11 unanswered before, 8 of 11 now).
    They also give hapijs/hapi and tdlib/telegram-bot-api a tenth countable PR, which passes.
    A PR its author self-closed is no longer excluded when a maintainer answered it first.
  - Meta's notice routes four projects off-platform: facebook/buck2 and facebook/pyrefly
    (black holes before; pyrefly's record is also cut), and facebook/hhvm and
    facebook/watchman (rated before). facebook/sapling stays a black hole: 37 of its 75
    outside PRs carry the notice, under half.
  - The short CJK replies flip no verdict on their own. Over every recorded window they answer
    119 more PRs and the new responders 1,130 more: the gate's population goes from
    130,841 unanswered of 415,487 PRs to 129,592 of 415,736.
  - Of the 88 former black holes, 82 are now rated, 4 are unrated for insufficient evidence
    and 2 are routed off-platform. 4,201 rated projects change rank.
- **The 30 checked projects**, re-scored by the new code on the full windows fetched from
  GitHub: all 30 agree with the check's "project's own people" verdict (27 black holes;
  signalapp/libsignal, apache/cassandra and Pumpkin-MC/Pumpkin pass). On the recorded data,
  the 9 of them whose record was cut are undecided.
- **The two "bot" responses**: in the recorded data the first response on
  microsoft/markitdown #2437 is a member's review at 06:57:19, not the
  `copilot-pull-request-reviewer` bot's. On Pumpkin-MC/Pumpkin #3496 it is a collaborator's
  67-character comment 3 minutes after opening, not coderabbitai's. Neither was a bot.

## Reviewer

Reviewed on 2026-10-17 by the lead session, which did not write the code. The code is
accepted. With main `ce06758` merged in, the full suite passes: 3,722 passed, 0 failed
(testq job `20261017-210141-8725248`). It is not merged yet. Exporting the published
2026-10-17 build with this code refuses 3 of its 720 black-hole verdicts: their pinned
evidence, read by the new rules, no longer shows a black hole. So the change lands together
with a rebuilt snapshot, and publishing new numbers is the owner's decision.
