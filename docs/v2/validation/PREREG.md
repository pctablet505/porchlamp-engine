# Pre-registration — forward outcome experiment

version: 1.0
date: 2026-09-12
decision: D-VAL-1 (`docs/v2/02-RANKING-DESIGN.md` section 12, owner decision O-6)
status: frozen. The values below are fixed at this hash; a changed document voids this experiment and the next snapshot restarts it.
self_hash: 29b1174d85c612876f0917836ad47846eec9429908da1dc49a16bda21b78f645

Self-hash rule: replace the value on the `self_hash:` line with 64 zeros, then
take `sha256` of the resulting bytes (UTF-8). One-line check:

```
sed 's/^self_hash: .*/self_hash: 000000000000000000000000000000000000000000000000000000000000000/' PREREG.md | sha256sum
```

## 1. Experiment

T is the `as_of` of the first v2 snapshot S0 (packet W5-2), read from S0's
manifest (`docs/v2/05-WORK-PLAN.md`, W0-3 manifest layout). The ranking is
frozen at T by D-VER-3. At T + 90 d, this one command collects and analyzes:

```
.venv/bin/python -m porchlamp.audit.forward --snapshot <S0-id> --data-dir <S0 data dir> --collect
```

The collection writes one GitHub envelope per rated repo under the output
directory, `raw/fwd_prs/{owner}__{repo}.json`, plus `forward_receipt.json`
(snapshot id, T, collection timestamp, per-repo status, point cost, and the
sha256 of this document). The same invocation then runs section 5 once and
writes `forward_report.json`. No analysis runs before the collection. The T + 90 d
timer is installed by the lead only after packet W5-1 ACCEPT; this document is
the only contract that run follows.

## 2. Population

- **Rated repo:** a repo with `is_stage0_disqualified = FALSE` in the S0
  database (`build/*.db` under the S0 snapshot directory).
- **PR window:** PRs opened on GitHub in (T, T + 60 d], any state, on a rated
  repo. A PR opened exactly at T is out; a PR opened exactly at T + 60 d is in.
- **External:** `author_association` not in {OWNER, MEMBER, COLLABORATOR, APP}.
- **First-timer (as recorded):** the S0 envelopes for that repo (`commits_180d`,
  `pr_events`) record no commit and no PR authored by that login before T.
  S0 envelopes carry at most 300 commits (180 d) and at most 30 PRs. An author
  whose earlier activity falls outside those caps counts as a first-timer, and
  the caps are disclosed in the report.
- **Eligible PR:** external and first-timer. A PR that is neither is excluded
  and counted by reason in the report.

## 3. Labels

All times are UTC. Per eligible PR:

- **decision_30d:** 1 when the PR was closed at or before `created_at + 30 d`,
  else 0. A closed PR is a decision; GitHub closes a PR when it merges. For
  every PR in the window, `created_at + 30 d <= T + 90 d`, so the label is
  fully observed at the collection time.
- **merge_90d:** 1 when the PR was merged at or before `T + 90 d`, else 0.
  The collection runs at `T + 90 d`, so every PR in the window is observed for
  its full merge window.

Per rated repo, over its eligible PRs:

- **decision_rate** = mean of decision_30d.
- **merge_rate** = mean of merge_90d.
- A repo with zero eligible PRs is excluded from every correlation and
  counted in the report.

## 4. Hypotheses

- **H1 (decision):** the responsiveness pillar at T predicts how quickly
  first-timer PRs receive a decision.
- **H2 (merge):** the composite at T predicts how many first-timer PRs merge.
- The other ten score-label pairs are reported descriptively; no threshold
  applies to them.

## 5. Exact analysis

Scores: `score_responsiveness`, `score_criticality`, `score_governance`,
`score_rigor_ai`, `score_accessibility`, `overall_porchlamp`, read from the S0
database at T.

For each score S and each label L in {decision_rate, merge_rate}, over rated
repos with at least one eligible PR:

- **Spearman rho(S, L):** Pearson correlation of the average ranks (tied
  values take the mean rank). Undefined below two repos or when S has zero
  variance: reported as `UNDECIDED:insufficient_repos` or
  `UNDECIDED:constant_score`.
- **AUC(S, L):** Mann-Whitney statistic over PR-level labels, S repeated per
  repo, tied scores take the mean rank. Undefined when one label class is
  empty: reported as `UNDECIDED:one_class`.

Criteria, stated before any collection, from D-VAL-1:

- **C1:** rho(score_responsiveness, decision_rate) >= 0.40. PASS when the
  value is defined and >= 0.40; FAIL when defined and < 0.40; UNDECIDED with
  the section-5 reason code when undefined.
- **C2:** rho(overall_porchlamp, merge_rate) >= 0.30. Same verdict rule.

A FAIL is published as FAIL, printed verbatim and stored in
`forward_report.json`. A failed criterion drives the v2.1 weight revision, per
D-VAL-1.

## 6. Cost disclosure

Collection per repo: 1 point for `GET /repos/{owner}/{repo}`, plus 1 point per
page of `GET /repos/{owner}/{repo}/pulls?state=all&per_page=100&sort=created&direction=desc`,
walked down to `created_at <= T`. The receipt records the actual point total.
The dry-run command (`--collect --dry-run`) prints, per repo, the last page
number read from the pagination `Link` header of page 1 (that is the total
page count of the full PR list) and the point bound 1 + last page, plus the
exact paths that a collection would write. The dry-run itself spends 2 points
per live repo (meta call plus page 1) and writes nothing.
