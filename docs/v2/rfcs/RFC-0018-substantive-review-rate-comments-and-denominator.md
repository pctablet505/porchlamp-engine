# RFC-0018: substantive-review-rate-comments-and-denominator

## Motivation

Parameter 4.3 (`substantive_review_rate`, `docs/v2/02-RANKING-DESIGN.md` section 4) measures the
share of decided external PRs in the 180-day window that received substantive review scrutiny,
scored on `min(100, 125 * r)` with `min_n = 10`. On the 2026-10-21 snapshot (`5,448` rated
repositories, `2,670` measured on 4.3), the median raw rate is `0.2194` (median score `27.43`,
mean `35.14`), with `413` (`15.5%`) scoring `0.0` and `122` (`4.6%`) scoring `100.0`.

Inspecting `porchlamp/collect/github_graphql.py` (`derive_pr_window`) and live PR windows across a
25-repository stratified sample (`1,198` decided external non-bot PRs across 24 repositories with
at least one decided external PR) identified three structural defects:

1. **Top-level maintainer issue comments were ignored.** `derive_pr_window` inspected only
   `pullRequest.reviews.nodes` (`inline >= 1` or `len(body) >= 80`). Whereas `1.1 TTFHR` and
   `D-GATE-5` (`gates.first_substantive_response`) credit D-IN-2 maintainer issue comments
   (`gates.is_maintainer_response`), `4.3` ignored them completely. Maintainers who post detailed
   architectural or code feedback in the PR conversation thread rather than clicking "Submit review"
   received zero credit (`678` of `955` uncredited decided external PRs in the 25-repo study had
   neither inline review comments nor an 80-char review object; `355` had maintainer issue
   comments).
2. **Raw `len(body)` ignored CJK character weight.** RFC-0011 (`gates.comment_length`) weights each
   Chinese, Japanese, or Korean character at `SUBSTANTIVE_COMMENT_CJK_WEIGHT = 3`, but `4.3` used
   raw `len(str(raw.get("body") or "")) >= 80`.
3. **Denominator included never-ready drafts and unanswered within-14d author self-closes.**
   RFC-0008 and RFC-0010 exclude draft PRs that never became ready for review (Rule 1) and
   unanswered PRs closed by their own author within `GATE5_RESPONSE_DEADLINE_DAYS = 14` of
   `clock_start` (Rule 2) from `D-GATE-5` and `1.4 GR_14`, because maintainers are not expected to
   review work-in-progress drafts or immediately withdrawn PRs. `4.3` still included all `29`
   never-ready drafts and `102` unanswered within-14d author self-closes (`131 / 1,198 = 10.9%` of
   decided external PRs in the 25-repo study) in its denominator.

## Method and Anti-Gaming Analysis

Before crediting D-IN-2 maintainer issue comments, we tested whether unguarded comment inclusion
creates a Goodhart/boilerplate loophole across the 25-repository live sample:

- **Failure mode without boilerplate deduplication:**
  - `apache/doris`: human-typed account `hello-stephen` (`MEMBER`, not in `BOT_LOGINS`) posts an
    identical 600-character welcome template and automated coverage table on `98` of `99` external
    PRs. Without a repetition guard, `apache/doris` jumps from `11.1%` (`11/99`) to `100.0%`
    (`99/99`).
  - `freeCodeCamp/freeCodeCamp`: canned top-level review/comment messages repeated verbatim across
    dozens of PRs (`majestic-owl448`) inflate credited PRs by `55`.
- **Window-level boilerplate repetition guard (`SUBSTANTIVE_REVIEW_BOILERPLATE_MIN_PRS = 3`):**
  - Normalize each candidate review body or D-IN-2 maintainer comment via `_normalize_review_text`:
    lowercase, strip URLs (`https?://\S+`), `@mentions`, `#\d+` issue/PR numbers, and digits, and
    collapse whitespace.
  - Count how many distinct sampled PRs in the repository's PR window carry each normalized string
    among non-bot, non-author reviews or D-IN-2 maintainer comments with
    `gates.comment_length(text) >= SUBSTANTIVE_REVIEW_BODY_CHARS`.
  - Any normalized string appearing on `>= SUBSTANTIVE_REVIEW_BOILERPLATE_MIN_PRS` (`3`) distinct
    PRs in the window is excluded from counting as a body-only substantive review or comment
    (reviews with `comments.totalCount >= 1` inline diff comments continue to count).
  - With `SUBSTANTIVE_REVIEW_BOILERPLATE_MIN_PRS = 3`, `apache/doris` is `16.3%` (`15/92` D1
    decided external PRs) instead of `100.0%`, while genuine per-PR maintainer feedback in
    `llvm/llvm-project` (`33.3% -> 69.8%`), `bevyengine/bevy` (`40.3% -> 71.9%`), `typst/typst`
    (`20.0% -> 70.6%`), and `AUTOMATIC1111/stable-diffusion-webui` (`16.7% -> 50.0%`) is credited.

## Change

1. **`porchlamp/core/constants.py`:**
   - Relocate `SUBSTANTIVE_REVIEW_BODY_CHARS: int = 80` into the hashed constants module.
   - Add `SUBSTANTIVE_REVIEW_BOILERPLATE_MIN_PRS: int = 3` to the hashed constants module.
2. **`porchlamp/collect/github_graphql.py` (`derive_pr_window`):**
   - Exclude decided PRs from the 4.3 denominator (`substantive_review = None`) when the PR is a
     draft that never became ready (`isDraft` and `ready_for_review_at is None`) or was closed by
     its author within `GATE5_RESPONSE_DEADLINE_DAYS` (`14` days) of `clock_start` with no
     maintainer response, merge, or maintainer close within the deadline and no response at or
     before the close (matching RFC-0008 Rules 1 and 2 and RFC-0010).
   - Credit `substantive_review = True` when a sampled PR has either:
     - a non-bot, non-author review with `comments.totalCount >= 1` or
       (`gates.comment_length(body) >= SUBSTANTIVE_REVIEW_BODY_CHARS` and
       `_normalize_review_text(body) not in boilerplate`), or
     - a D-IN-2 maintainer issue comment (`gates.is_maintainer_response(comment, author_login, maintainers)`)
       with `gates.comment_length(bodyText) >= SUBSTANTIVE_REVIEW_BODY_CHARS` and
       `_normalize_review_text(bodyText) not in boilerplate`.

| Constant | Before | After |
|---|---|---|
| `SUBSTANTIVE_REVIEW_BODY_CHARS` | *(absent; 80 unhashed in `porchlamp.collect.github_graphql`)* | `80` |
| `SUBSTANTIVE_REVIEW_BOILERPLATE_MIN_PRS` | *(absent)* | `3` |

`CONSTANTS_HASH` moves:
`a48306f15d2c9e7603ca00ae25f34e176fbb1421b270a072f9db645f1eb5e1a3` ->
`3bc4aac040af2937e438e39e3e18ffbbfad6ddf7aeea7a1911f6e6013105226d`
(and to `93e44701a8a9862e242e0e1f93e7f082848fbc1f222d68bdcd221600192bac8c` together with RFC-0020).

`BENCHMARK_VERSION`: `"2.0.0-rc1"` -> `"2.0.0-rc1"` (pre-release convention).

## Evidence

Across the 24 repositories with `>= 1` decided external PR in the 25-repository stratified live
study (`1,198` total decided external non-bot PRs; `1,067` after excluding `29` never-ready drafts
and `102` unanswered within-14d author self-closes):

| Variant | Median rate | Mean rate | Median score | Mean score |
|---|---|---|---|---|
| Baseline (`rev_only`, D0 denominator) | `16.5%` | `20.3%` | `20.6` | `25.4` |
| Baseline reviews + D1 denominator | `19.6%` | `22.7%` | `24.5` | `28.4` |
| Unguarded comments (`N=80`, D0, no boilerplate guard) | `30.8%` | `39.7%` | `38.5` | `49.6` |
| **RFC-0018 (`G80_D1`: `N=80`, CJK weight 3, `K=3` boilerplate guard, D1 denominator)** | **`31.8%`** | **`33.1%`** | **`39.7`** | **`41.4`** |
