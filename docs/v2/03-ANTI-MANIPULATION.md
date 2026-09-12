# Anti-manipulation design — nobody moves the number without moving the thing

Goal: a maintainer, an organisation, or an agent operating the pipeline cannot
improve a repository's rank without genuinely improving what contributors
experience, and any attempt is visible.

## 1. Threat model

| Actor | Motive | Means |
|---|---|---|
| Maintainer / vendor | marketing, recruiting | cheap signals: labels, canned replies, doc keywords, star purchases, download bots, sock-puppet contributors |
| Competitor / troll | deflate a rival | spam PRs to raise ghost rate, throwaway accounts, mass issues |
| Pipeline operator or agent | ship a green result, favour a repo | edit derived/verified files, edit constants, weaken tests, fabricate bundles with an LLM (the path in F10) |
| Third-party drift | none (accidental) | deps.dev mapping errors, Scorecard coverage gaps, registry rate limits reading as zeros |

## 2. Principles

1. **Outcome-anchored.** Prefer parameters that record something that happened
   to a contributor (a decision, a merge, a release, a promotion) over
   declarations (a label, a sentence, a badge). Declarations get low weight and
   caps.
2. **Cost asymmetry.** Every parameter must be cheaper to improve honestly than
   to fake at scale. If faking is cheap, the parameter is capped or paired with
   an outcome it cannot fake.
3. **Monotone, capped curves.** No parameter can be pushed past its cap, and
   no curve rewards "less of a good thing" (D-PARAM-MONO).
4. **Evidence, not self-report.** No input is supplied by the repository owner
   through any channel other than the public record; no manual score edits.
5. **Reproducible and public.** Every score can be recomputed from a hashed
   raw snapshot by anyone; the manifest, constants, universe and changelog are
   published. Gaming that is visible is gaming that fails.
6. **Absence is not neutral for repo-controlled signals** (D-MISS-1): hiding
   a signal cannot raise a score.

## 3. Per-parameter cost-to-fake

| Param | Cheapest fake | What v2 does | Residual |
|---|---|---|---|
| 1.1 TTFHR | canned "thanks" comment by a bot or a member within minutes | bots excluded by type (D-IN-1); response must be substantive (≥ 40 chars) by MEMBER/OWNER/COLLABORATOR (D-IN-2); TTFHR p75 in tie-breaks; paired with DR_90 so speed without decisions gains little | a maintainer who really writes 40 chars fast: that *is* responsiveness |
| 1.2 DR_90 | close PRs unread | closing without review counts as a decision but zero for 4.3 and shows in the "closed with no substantive response" detector (§6) | mass-closing is visible and hurts 4.3 |
| 1.3 MR_first | merge sock-puppet first PRs | author eligibility (90-day-old account, ≥ 5 events) and 20% per-author cap (D-IN-3); lockstep detector on account creation dates | paid real newcomers: contributors still benefit |
| 1.4 GR_14 | (attack) spam PRs from throwaways to raise ghost rate | throwaway authors excluded from the denominator (D-IN-3); spam-closed PRs count as decided | a flood from aged accounts: costly |
| 1.5 TTM_p90 | merge trivial PRs fast | ECDF within ecosystem; only 2.5 global points | low |
| 2.1 Dependents | publish many tiny packages depending on yourself | root-org dedup and same-org exclusion (D-PARAM 2.1) | cross-org collusion: expensive |
| 2.2 Criticality | inflate issues/comments | signals saturate with log thresholds; issue spam raises `updated_issues` slightly but comment-per-issue falls | low |
| 2.3 Downloads | CI mirrors, download bots | ECDF rank (bounded), 6.25 points, below dependents by weight; PyPI installer filter when BigQuery is available | moderate; documented as the weakest signal |
| 3.1 Legal IP | claim DCO in docs | evidence = DCO check runs or Signed-off-by share, CLA bot checks; documents are not evidence | a project can genuinely adopt DCO: intended |
| 3.2 Elephant | many emails / accounts | org by email domain; personal-domain accounts count individually so splitting one org across personal emails looks like many individuals only if the commits are real; cross-checked with `org_count` in 2.2 | an org using personal emails inflates EF; detector: commit share of top domain vs top login |
| 3.3 Ladder | write "we promote contributors" | VERIFIED needs an observed association transition; DOCUMENTED gets 75 only | text still worth 35 points of the parameter → 2.1 global points |
| 4.1 Pipeline | none cheap | Scorecard or observed review/CI rates | low |
| 4.2 AI posture | write a policy file | 4.5 global points; REGULATED needs a policy or trailer convention | low value, low weight |
| 4.3 Review rate | paste long review bodies | ≥ 80 chars or inline comments; a maintainer writing long reviews is doing the work | low |
| 5.1 Newcomer issues | label 200 issues | cap at 10 → 100; freshness 90 d; unassigned only; paired with 5.2 conversion (labels without merges do not move 5.2) | 5.25 global points max |
| 5.2 Conversion | merge sock puppets | D-IN-3 | as 1.3 |
| 5.3 Return | sock puppets returning | D-IN-3 | as 1.3 |
| 5.4 DX | add a Dockerfile | 2.25 global points; runtime is measured | low |
| Universe entry (stars) | buy stars | StarScout at entry (D-IN-5); v2.1 multi-criteria entry | entry only; stars never score |

## 4. Input hygiene controls (packets W1-*, W3-1)

- Actor typing from GraphQL; exact-match bot list frozen with the constants.
- Author eligibility and per-author caps as D-IN-3; cohort membership requires
  account creation ≥ 90 d before the first PR.
- Lockstep detector: ≥ 5 eligible-looking first-timer accounts created within
  any 7-day span whose PRs were all merged → `suspicion: newcomer_burst`.
- Dependents: dedup by root org; drop dependents whose owner equals the repo
  owner; store both raw and deduped counts.
- Substantive response and review definitions are code, not judgement; the
  thresholds (40 / 80 chars) are constants under D-VER-2.

## 5. Pipeline integrity (packets W0-3, W0-4, W3-3, W3-4)

**Envelope contract.** Every raw fetch is stored as
`{source_url, fetched_at, http_status, etag, sha256(body), collector_version,
data}`. A derived value's provenance points at an envelope path and repeats its
hash. `verify` recomputes the hash of every referenced envelope; a mismatch or
a provenance that is not an envelope path (for example the LLM handoff's
`"ecosystem-intelligence"`) flags `unverifiable_provenance` and blocks
promotion.

**Snapshot manifest.** `cvi snapshot freeze --as-of <ts>` lists every envelope
hash used, computes a Merkle root, records `benchmark_version`,
`constants_hash`, `code_commit`, and writes `data/snapshots/<id>/manifest.json`.
Promotion reads only envelopes in the manifest. Every stored row carries
`snapshot_id`, `raw_hash` (hash of its bundle) and `constants_hash`.

**Reproducibility.** `cvi reproduce <snapshot_id>` rebuilds the database from
`data/raw` and the manifest and compares a canonical dump hash; CI runs it on a
committed fixture snapshot (about 50 repos) on every push. Scoring code reads
time only from `as_of`.

**Fabrication path removed.** `scripts/curate_dataset_llm.py`,
`HANDOFF-DATASET-CURATION-AGENT.md` and the `ecosystem-intelligence` provenance
vocabulary are deleted. A test asserts that no bundle in `data/verified` has a
provenance source outside `data/raw/` or an `http(s)://` URL with a recorded
hash.

**Live re-verification.** `verify --live-sample` re-fetches, for a random 5% of
rated repos plus the top 50 per ecosystem, the GitHub metadata and the PR
window head, and compares stars/forks (monotone rule), open-PR counts and the
latest PR number; disagreement beyond tolerance flags `source_drift` and the
row is held from promotion until re-gathered. Runs nightly as a canary.

**Served database guard.** `serve.py` refuses a database whose rows lack
`snapshot_id` or whose `constants_hash` does not match the running code; the
synthetic catalog can never be served again by accident (F1).

## 6. Detectors (packet W3-2)

Computed at every snapshot against the previous one; results are flags on the
row (`suspicion: [...]`) shown in the UI and in a report, never silent
penalties:

| Detector | Trigger |
|---|---|
| `param_jump` | any parameter score moves > 40 points between consecutive snapshots with n ≥ 10 |
| `speed_without_decisions` | TTFHR score ≥ 90 and DR_90 score ≤ 40 |
| `merges_without_return` | MR_first ≥ 90 and conversion or return ≤ 20 with n ≥ 10 |
| `closed_unread` | share of external PRs closed unmerged with no substantive response ≥ 0.30 |
| `newcomer_burst` | §4 lockstep rule |
| `star_burst` | StarScout lockstep penalty applied at entry |
| `label_spam` | newcomer-labelled issues ≥ 50 and conversion ≤ 10 |
| `ef_domain_mismatch` | top email domain share ≥ 0.5 while EF ≥ 4 |
| `source_drift` | §5 live re-verification disagreement |

An appeals process exists but is evidence-only: a maintainer can point at a
public record the collector missed; the fix is a collector change (new patch
version), never a manual override. A public `overrides.json` (empty by default)
lists any curated facts (for example reciprocal-CLA classification) with a URL
each.

## 7. Process controls (owner rules and the review protocol)

- Constants change → RFC in `docs/v2/rfcs/`, version bump (D-VER-1), anchor
  test pass, sensitivity report (D-AGG-3) attached.
- Tests cannot be weakened without a root-cause note; every ACCEPT is
  reproduced by a second independent agent; mutation proof for behavioural
  changes (`docs/REVIEW_GUIDELINES.md`, hub review protocol).
- The lead commits from reported diffs; agents never commit or push.
- The methodology page publishes: parameter definitions, windows, minimum n,
  curves, weights, tie-breakers, universe criteria, the manifest root of the
  current snapshot, the constants hash, the sensitivity report, the anchor
  results, and the detector summary.

## 8. What remains gameable, stated honestly

- A maintainer can respond faster, decide more PRs, review more thoroughly
  and merge more newcomers. That raises the score and is the point.
- An organisation can pay real, aged accounts to contribute. Expensive, and
  the contributors still receive decisions and merges.
- Download counts can be inflated by CI traffic; the parameter is rank-bounded
  and carries 6.25 points.
- Governance text still moves 2.1 global points (DOCUMENTED ladder) and AI
  posture 4.5 points; both are declarations by design and are weighted as
  such.
