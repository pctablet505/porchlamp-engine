# 08 — Sustainable collection: from sweeps to deltas

Lead design note, 2026-09-17. Owner question: *"the way we are gathering the
data is not sustainable to maintain regularly (daily or weekly). Analyse and
plan how to do it sustainably."* Decisions here carry ids `D-COL-*`; the
packets that implement them are wave **W6** in `05-WORK-PLAN.md`.

Every number in §1 is measured from the cache in `data/raw` (fetched
2026-09-11/12) or from the W2-8c live run. Numbers in §5 are estimates built
from those measurements and are labelled so; packet **W6-0** replaces them with
measurements from the sweep now in flight.

---

## 1. What a sweep costs today (measured)

The universe: 5,753 candidates. Budget on one user token: 5,000 REST calls/h,
5,000 GraphQL points/h, 30 search calls/min; conditional requests answered
`304 Not Modified` are free (GitHub's documented rule) — and we never send
one.

### 1.1 Per-collector cost, one full sweep

| Collector | Calls per repo (from the code) | Sweep total | Budget it draws on | Hours per sweep |
|---|---|---|---|---|
| `github_meta`+`pr_events`+`pr_reviews` | 1 GraphQL | 5,753 | GraphQL | ~1.2 |
| `contributors`, `actions_runs`, `tree`, `gfi` (legacy) | 4 REST | 23k | REST | ~4.6 |
| `commits_180d` | ≤3 REST | ≤17k | REST | ~3.5 |
| `commits_365d` | ≤10 REST | ≤58k (busy repos) | REST | up to 11 |
| `repo_object` | 1–2 REST | ~9k | REST | ~2 |
| `gfi` (per-label rewrite) | 1 + labels matched | ~12k | REST | ~2.4 |
| `pr_window` | 1–5 GraphQL pages, ≈31 points/page (100 PRs × nested reviews/comments) | ≈35k points (W2-8c: 147 points / 24 repos ≈ 6 per repo, most repos 1 page) | GraphQL | ~7 |
| `issues_counts` | **3 search calls** at 2.1 s each | 17k search calls | search (30/min) | **~10** |
| `releases`, `runs` | 1–2 REST each | ~17k | REST | ~3.5 |
| `governance` | merged-PR page + **check names for each of 20 merged PRs** + sign-off commits + files ≈ 25–30 REST | **~155k** | REST | **~31** |
| `author_history` | 365-d PR window (GraphQL pages) + eligibility batches of 15 logins | ≈ (external authors ÷ 15) batches per repo; a 200-author repo = 14 batches | GraphQL | ~5–10 |
| `dependents`, `depsdev`, `scorecard` | keyless HTTP | ~17k | none (0.35 s sleep) | ~1.7 wall |
| `downloads` (pypi) | pypistats, 4/min | 900 | pypistats | ~4 wall |
| **Sum** | ≈ 30 REST + 4 GraphQL + 3 search per repo (W2-8c measured 712 REST / 98 GraphQL for 24 repos) | ≈ 172k REST, ≈ 45k points, 17k search | | **≈ 45–50 h serialised** |

Add the per-call overhead: every call is a `gh api` **subprocess** (~0.2 s
process start), so a 30-call repo spends ~6 s in process spawning alone —
about 10 h per sweep of pure overhead, overlapping the above.

**A full sweep is ~2 days of wall-clock on one token.** Weekly refresh would
be a ~30 % duty cycle with no headroom for anything else; daily is
impossible; and `WINDOW_STALENESS_DAYS = 14` forces at least one sweep every
two weeks, so the design's floor is *two-day sweeps every fortnight with data
up to 14 days old at snapshot time*.

### 1.2 The universe is very unevenly active

From `github_meta.pushed_at` (5,731 repos) and the 30-newest-PR samples
(5,189 repos with a full sample), relative to the fetch date:

| Signal | ≤ 7 d | ≤ 30 d | ≤ 90 d | ≤ 365 d | > 365 d |
|---|---|---|---|---|---|
| last push | 2,633 (46 %) | 572 | 445 | 668 | **1,413 (25 %)** |
| newest PR | 2,885 (50 %) | 751 | 492 | 1,546 (27 %, incl. 57 with no PR) | |

PR arrival rate: **median 0.20 PRs/day**; p90 7.5/day; 1,490 repos see ≥ 1
PR/day, 651 see ≥ 5/day; 361 repos are archived. So half the universe changes
weekly, a quarter has not changed in a year, and PR volume is concentrated in
~650 repos. A sweep spends the same on `torvalds/linux` and on an archived
repo nobody has pushed since 2021.

## 2. Root causes

1. **No change detection.** Every collector is fetched for every repo
   regardless of whether anything changed. The only "state" is *file exists
   ⇒ done*, and the only refresh is `--force` ⇒ refetch everything.
2. **Windows are re-materialised, not updated.** `pr_window` refetches up to
   500 PRs by `CREATED_AT` each time; `commits_365d` refetches ten pages;
   `issues_counts` re-runs three searches. None of them can say "give me what
   changed since yesterday".
3. **Slow facts on fast clocks.** Governance files, check names, CLA/DCO,
   contributor shares, dependents, download counts move monthly at most, yet
   `governance` alone is ~60 % of the REST budget of a sweep.
4. **Per-author facts recomputed per repo.** A login's `createdAt` never
   changes and its public-event count moves slowly, but eligibility is looked
   up again for every repo the author touches.
5. **The transport wastes what GitHub gives away.** No keep-alive, no
   `If-None-Match` (the envelope's `etag` field is always `null`), no reading
   of `X-RateLimit-Remaining`; `cvi/collect/ratelimit.py`'s token bucket, ETag
   cache and circuit breaker have **zero callers**. `gh api rate_limit` is
   known-stale in this environment (W2-8c) and nothing else measures budget.
6. **Search API on the hot path** for counts the issues list endpoint gives
   with `since`.

## 3. Target

- A **daily** snapshot, chained to the previous one (`--previous`, so
  `param_jump` and the other cross-snapshot detectors finally have two points).
- **≤ 2 h of API time per day** on the single user token, leaving ≥ 80 % of
  every hourly budget free; hot repos refreshed hourly if we choose.
- Active repos never more than 24 h stale at snapshot time; inactive repos
  *proven* unchanged rather than assumed.
- Unattended: timers, a queue, resumable after a crash, a daily cost report.
- Reproducible: any past daily snapshot rebuildable from the same ledgers.

## 4. Decisions

**D-COL-1 Change detection before collection.** A *fingerprint* pass runs
first, every hour or day: one aliased GraphQL query per 100 repos returning
`pushedAt`, `updatedAt`, `isArchived`, `isDisabled`, `stargazerCount`,
`forkCount`, `pullRequests(last:1, orderBy:UPDATED_AT){updatedAt}`,
`issues(last:1, orderBy:UPDATED_AT){updatedAt}`, `releases(last:1){createdAt}`,
`defaultBranchRef{target{oid}}`. 58 queries cover the universe. Each field
maps to the collectors it can invalidate (table in §6). A repo whose
fingerprint is unchanged **costs nothing further** — and the pass *advances the
watermarks* of its unchanged collectors, because an unchanged fingerprint is an
observation that the state is current. That is what keeps cold repos from
ever going "stale".

**D-COL-2 Deltas, not windows.** Activity collectors (`pr_window`, issues,
commits, releases, workflow runs) become **append-only ledgers** keyed by the
GitHub object id (PR number, issue number, commit sha, release id, run id),
one JSONL file per (repo, ledger), with a per-(repo, ledger) watermark. A
fetch orders by `UPDATED_AT DESC` (`since=<watermark>` on REST) and stops at
`watermark − 1 h`; each fetched object is appended as a new *version* with
`observed_at`. Windows are cut at derive time from the ledger with `as_of`:
D-IN-4's 180-day window and 500-PR cap become derive-time truncation of the
ledger, with the same `truncated` flag semantics.

**D-COL-3 Cadence tiers are computed from activity, never configured.**
`hot`: fingerprint changed within 7 d → activity ledgers refreshed daily
(hourly if budget allows). `warm`: within 30 d → every 3 days. `cold`: within
365 d → fingerprint weekly, ledgers only when the fingerprint moves.
`dormant`: > 365 d or archived → fingerprint weekly, nothing else. Measured
today that is ≈ 2,900 / 570 / 1,100 / 1,800 repos.

**D-COL-4 Slow facts on slow clocks.** `governance`, `contributors`,
`repo_object` (license/manifest), `tree`: at most every 30 d **and** only
when `defaultBranchRef.target.oid` moved since the last fetch. `dependents`,
`depsdev`, `scorecard`, `downloads`: weekly, spread across the week
(≈ 820 repos/day; pypistats' 4/min handles ~130 pypi repos/day). Star history:
monthly, incremental (`Accept: application/vnd.github.star+json`, newest
first, stop at the watermark).

**D-COL-5 Author facts are global and cached.** `data/state/users/<login>.json`:
`createdAt` (immutable), `__typename`, public-event count (TTL 90 d),
eligibility. A login is looked up **once across the whole universe**; a repo's
`author_history` becomes a join of its PR ledger with the user cache. The
eligibility batches (15 logins per GraphQL call) only ever run for logins the
cache has never seen or whose TTL expired.

**D-COL-6 One transport.** A single persistent HTTPS session (stdlib
`http.client`, keep-alive) with the token from `gh auth token`; every REST GET
sends `If-None-Match` from the stored ETag and treats `304` as "unchanged,
free, advance the watermark"; `X-RateLimit-Remaining/Reset` and GraphQL
`rateLimit{cost remaining resetAt}` feed `cvi/collect/ratelimit.py`'s token
bucket and circuit breaker, which finally get callers. `gh api` is used for
nothing but the token. Secondary-limit responses (`403` with `Retry-After`)
open the breaker for the stated time.

**D-COL-7 The search API leaves the hot path.** Issue counts come from the
issue ledger (`GET /repos/{o}/{n}/issues?state=all&since=<watermark>&per_page=100`,
which also returns PRs — filtered by `pull_request` presence). Closed / updated
counts within a window are computed locally. Search is kept only for the
one-time 365-day backfill of repos with > 1,000 issues, where paging the list
is dearer.

**D-COL-8 Reproducibility with ledgers.** Ledger records are never modified,
only appended (a deleted/renamed object gets a tombstone record). A snapshot
records `as_of` and, per repo, the hash of the *as-of slice* of each ledger
(all records with `observed_at ≤ as_of`, latest version per object). That
slice is a pure function of (ledger file, `as_of`), so `cvi.snapshot
reproduce <id>` re-derives yesterday's bundles from today's ledgers and must
reproduce `records_hash`. D-VER-3's `manifest_root` is the Merkle root over
those slice hashes. Compaction rewrites only records older than the retention
horizon (400 d: 365-d author window + margin) and appends a compaction record
so a reproduce that needs the compacted range refuses with a named reason
instead of silently differing.

**D-COL-9 Daily snapshot, weekly publication.** Every night: derive → build →
store with `--previous` = yesterday → served-DB guard → flip the served
database. Snapshot id = the UTC date. The Sunday snapshot is the *published*
one (D-VER-1 versioning unchanged; the benchmark version only moves with
constants). Retention: daily databases 30 d, weekly 1 y, monthly forever;
ledgers 400 d.

**D-COL-10 Budget accounting and fail-soft.** The scheduler drains a priority
queue — fingerprints → hot activity ledgers → warm → cold → slow facts — and
stops at a **reserve** (500 REST calls, 500 points) kept for interactive use.
Whatever is not drained carries over. At derive time a (repo, collector) whose
watermark is older than `WINDOW_STALENESS_DAYS` relative to `as_of` is
`Unmeasured(collector_failure, detail="stale since <watermark>")` — never
silently old — and the snapshot still builds. Because fingerprints advance
watermarks (D-COL-1), only a repo the fingerprint pass itself could not reach
can go stale.

**D-COL-11 A bulk feed as the backstop, not the path.** GH Archive's hourly
public-event dumps replay any window with zero API quota. They are used (a)
to backfill after an outage and (b) as an *independent* cross-check: a
detector that compares ledger event counts with the archive's for the same
window (a disagreement is evidence of a collector fault or a deleted history).
Not on the daily path: several GB/day of downloads for what fingerprints give
in 58 queries.

## 5. Daily cost under the new design (estimate from §1 measurements; W6-0 measures)

| Step | Volume | Cost | Notes |
|---|---|---|---|
| Fingerprints | 58 queries | ≈ 300–600 points | ~4 points per 100 repos assumed; measured in W6-2 |
| Changed repos per day | ≈ 2,000 (1,490 with ≥ 1 PR/day + pushes) | | from §1.2 |
| PR deltas | 2,000 × 1 page of ≤ 25 PRs | ≈ 16k points | busy repos need 2–3 pages |
| Issue / commit / run deltas | 2,000 × 3 conditional REST | ≈ 6k REST, many `304` (free) | |
| New authors | ~500 logins/day → 34 batches | < 100 points | after the first pass |
| Slow facts (30-d spread) | ≈ 190 repos/day × ~27 REST | ≈ 5k REST | governance stays the dearest fact; it now runs 1/30 as often |
| Keyless weekly spread | ≈ 820/day | 0 API | ~35 min wall incl. pypistats |
| **Total** | | **≈ 12k REST + 17k points/day ≈ 10–15 % of the daily budget** | ≈ 1–2 h wall with keep-alive |

The same arithmetic for a **weekly** cadence with only D-COL-1/3/4/6 landed
(no ledgers yet, today's full-window collectors kept): ≈ 3,500 changed repos
× (1 GraphQL + 5 REST) ≈ 20k REST + 20k points ≈ **4–5 h per week** — down
from ~48 h. That is the interim state after the first three packets.

## 6. Fingerprint → collector invalidation

| Fingerprint field moved | Refetch (delta) |
|---|---|
| `pullRequests.last.updatedAt` | PR ledger; user cache for new logins |
| `issues.last.updatedAt` | issue ledger |
| `pushedAt` / `defaultBranchRef.target.oid` | commit ledger; `runs`; and (D-COL-4 clock permitting) `tree`, `repo_object`, `governance` |
| `releases.last.createdAt` | release ledger |
| `stargazerCount` / `forkCount` | nothing (recorded in the fingerprint itself; star history on its monthly clock) |
| `isArchived` / `isDisabled` | tier → dormant; gate evidence |
| nothing | advance every watermark to the fingerprint time |

Layout: `data/raw/<collector>/` keeps today's envelope contract for the
slow-fact collectors; activity moves to `data/ledgers/<ledger>/<owner>__<name>.jsonl`;
scheduler state (`fingerprints.jsonl`, `schedule.sqlite`, `users/`) under
`data/state/`. `derive.py` reads ledgers through one `as_of` slicer and keeps
producing the same v1 document + v2 bundle, so the scoring core, the store and
the UI do not change.

## 7. What the BFS sweep in flight contributes

Nothing is wasted. The L1–L4 sweep (`07-BFS-COLLECTION-TASK.md`) is the
**initial backfill**: `pr_window` envelopes seed the PR ledgers (each node
becomes version 1 with `observed_at` = the envelope's `fetched_at`),
`commits_365d` seeds the commit ledgers, `issues_counts` samples seed the
issue ledgers, `author_history` seeds the user cache. W6-0 adds cost
accounting to that sweep so §5 is replaced by numbers.

## 8. Migration in three steps

1. **Weekly refresh becomes cheap (W6-0, W6-1, W6-2, W6-6).** Transport with
   ETags and budget accounting; fingerprints; dirty-only gather; slow facts on
   their own clocks. Weekly sweep ≈ 4–5 h. No change to envelopes, derive,
   scoring or the store.
2. **Daily becomes feasible (W6-3, W6-4, W6-5).** Ledgers for PRs, issues,
   commits, releases, runs; search API retired; global user cache. Daily API
   time ≈ 1–2 h.
3. **Unattended and reproducible (W6-7, W6-8, W6-9; W6-10 later).** Scheduler
   daemon and timers; ledger-slice reproducibility; nightly snapshot chain with
   retention; GH Archive backstop.

Routing (per `05` §0.2): W6-1, W6-2, W6-6, W6-9 are mechanical enough for the
local model; W6-3, W6-7, W6-8 change contracts and need Sonnet/Opus-class
agents with the lead reviewing.

## 9. Operations

- `cvi-fingerprint.timer` hourly (58 queries), `cvi-collect.timer` hourly
  drain under budget, `cvi-snapshot.timer` daily at 02:00 UTC. One instance
  each, pidfile-locked (never `pkill -f`).
- Daily cost report written next to the snapshot: calls by collector, `304`
  ratio, points spent, queue backlog, stale set, wall time. A backlog that
  grows for three days is the alarm.
- Capacity that stays within GitHub's terms: a GitHub App installed on the
  owner's own organisation gets its **own** 5,000/h installation budget and
  can read public repositories, doubling capacity. Multiple free user accounts
  would not (terms of service). Owner decision; not needed for the §5 numbers.

## 10. Limits this does not remove

Deleted, renamed or made-private repositories (tombstones, then dormant);
GitHub outages (queue carries over; the snapshot still builds with stale
marks); pypistats' 4/min (weekly spread absorbs it); secondary rate limits on
bursty GraphQL (breaker + spread); the first backfill of a newly added repo
still costs a full window.
