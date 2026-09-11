"""Cross-module integration test: GHArchive stream -> scoring -> store -> API.

One fully offline, deterministic end-to-end scenario exercises the whole
CVI platform pipeline in a single fixture:

1. A fabricated GHArchive hourly partition
   (``https://data.gharchive.org/2026/09/10/03.json.gz``) — gunzipped
   NDJSON of ``PullRequestEvent`` records for five repositories across
   three ecosystems (pypi / npm / crates), plus six noise records that
   must be dropped. No PR variety is random: every created/merged/closed
   timestamp, review latency, and maintainer comment count is a fixed
   offset from the scenario clock ``NOW = 2026-09-10T12:00:00Z``.
2. ``GHArchiveStreamer`` (backed by a fake ``httpx`` transport inside
   ``CachedHttpClient``) streams the partition per repo; mapping counts
   are asserted (7 / 6 / 5 / 3 / 2 mapped events).
3. A documented enrichment pass simulates the delta-hydrated review
   telemetry the GHArchive bulk stream cannot carry (per spec 9.1 the
   conservative mapping defaults to no review data): first-human-review
   latency, maintainer comment counts, author association, and
   external/internal flags. The REAL pillar scorers then run on the
   enriched events.
4. ``ingest_and_score`` persists each composite via
   ``score_repository -> upsert_composite -> apply_raw_metrics`` into a
   single ``OperationalStore``.
5. The store is served by ``create_app`` + ``TestClient``; the matrix,
   deep-dive, and reproject endpoints are asserted against the stored
   rows.

Fixture outcomes (pinned from the real scorers; change a fixture value
and these assertions tell you exactly which pillar moved):

    repo                stars   P1       quadrant    tier    cvi
    acme/widgets        45000   88.4375  Sweet Spot  S+      91.34
    acme/core           12000   63.0581  Sweet Spot  B       65.43
    acme/cli             20     92.0000  Nursery     B       62.74
    kern/kernel-tools    3500   68.7500  Crucible    B       58.91  (off-platform)
    ghost/legacy-lib     800    gated    Graveyard   D       0.0    (>50% ghost rate)

Acceptance: every test in this file is deterministic and offline; the
fake transport is the only HTTP layer and the fake clock drives all
token-bucket / breaker sleeps.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

import httpx
import pytest
from fastapi.testclient import TestClient

from cvi.api import create_app
from cvi.engine import score_repository
from cvi.ingest import (
    CachedHttpClient,
    CircuitBreaker,
    GHArchiveStreamer,
    TokenBucketRateLimiter,
    apply_raw_metrics,
    gharchive_hourly_url,
    ingest_and_score,
    map_pull_request_event,
)
from cvi.lakehouse import OperationalStore
from cvi.models import (
    AIPosture,
    AuthorAssociation,
    CVICompositeResult,
    Ecosystem,
    LegalIPModel,
    OpenSSFSignals,
    Persona,
    PromotionLadder,
    RawPREvent,
    RepoMeta,
)

# --------------------------------------------------------------------------- #
# Scenario constants (all deterministic)
# --------------------------------------------------------------------------- #

#: The "current time" of the scenario; every fixture timestamp derives
#: from this, so nothing ever reads the real clock.
NOW = datetime(2026, 9, 10, 12, 0, 0)

#: The GHArchive hourly partition the fake transport serves.
PARTITION_WHEN = datetime(2026, 9, 10, 3, 0, 0)
PARTITION_URL = "https://data.gharchive.org/2026/09/10/03.json.gz"
assert gharchive_hourly_url(PARTITION_WHEN) == PARTITION_URL
ETAG = '"partition-2026-09-10-03"'

#: Per-ecosystem population vectors for the ECDF normalisations
#: (Pillar 1 TTM_p90 and Pillar 2 dependents/downloads). The
#: dependents/downloads populations are pre-mapped ``log10(count + 1)``
#: values, matching what :func:`cvi.pillars.criticality` scores.
PYPY_TTM = [12.0, 24.0, 36.0, 48.0, 60.0, 72.0, 84.0, 96.0, 120.0, 1500.0]
NPM_TTM = [24.0, 48.0, 72.0, 96.0, 120.0]
CRATES_TTM = [48.0, 72.0, 96.0]
PYPY_DEPENDENTS = [1.0, 2.0, 3.0, 4.0, 5.0]
NPM_DEPENDENTS = [1.0, 2.0, 2.5, 3.0]
CRATES_DEPENDENTS = [2.0, 3.0, 4.0]
PYPY_DOWNLOADS = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
NPM_DOWNLOADS = [1.0, 2.0, 3.0, 4.0, 5.0]
CRATES_DOWNLOADS = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]

#: Weights for ``POST /v1/cvi/reproject``: they deliberately reorder
#: >= 2 repositories (acme/cli rises, acme/core falls).
REPROJECT_WEIGHTS = {
    "responsiveness": 0.45,
    "criticality": 0.25,
    "governance": 0.15,
    "rigor": 0.05,
    "accessibility": 0.10,
}
assert abs(sum(REPROJECT_WEIGHTS.values()) - 1.0) < 1e-12


def _ts(hours_before: float) -> str:
    """Scenario timestamp ``hours_before`` before NOW, GHArchive-style."""
    return (NOW - timedelta(hours=hours_before)).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Fixture data: PR population + raw metrics
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PrSpec:
    """One PullRequestEvent record in the fake partition.

    The GHArchive streamer can only see record-level fields; the last
    five fields are the delta-hydrated review telemetry applied after
    streaming (see :func:`hydrate_review_telemetry`).
    """

    owner: str
    name: str
    number: int
    author: str
    action: str  # "opened" | "merged" | "closed"
    created_hours_before: float
    merged_hours_before: Optional[float] = None
    closed_hours_before: Optional[float] = None
    latency_hours: Optional[float] = None
    maintainer_comments: int = 0
    association: str = "NONE"
    prior_merged_commits: int = 0
    external: bool = True


#: 23 PullRequestEvents across the five fixture repos.
PR_SPECS: list[PrSpec] = [
    # acme/widgets (pypi) — fast reviewer, healthy first-timer pipeline.
    PrSpec("acme", "widgets", 101, "alice-ext", "merged", 1200.0,
           merged_hours_before=1176.0, latency_hours=6.0, maintainer_comments=4),
    PrSpec("acme", "widgets", 102, "bob-ext", "merged", 960.0,
           merged_hours_before=900.0, latency_hours=8.0, maintainer_comments=5),
    PrSpec("acme", "widgets", 103, "carol-ext", "closed", 480.0,
           closed_hours_before=432.0, latency_hours=4.0, maintainer_comments=2),
    PrSpec("acme", "widgets", 104, "dave-ext", "opened", 720.0,
           latency_hours=10.0, maintainer_comments=3),
    PrSpec("acme", "widgets", 105, "erin-ext", "opened", 72.0,
           latency_hours=12.0),
    PrSpec("acme", "widgets", 106, "root-owner", "opened", 96.0,
           association="OWNER", prior_merged_commits=50, external=False),
    PrSpec("acme", "widgets", 107, "dependabot[bot]", "merged", 48.0,
           merged_hours_before=24.0),
    # acme/core (pypi) — slower: mid-latency reviews, one 20-day-old
    # unreviewed external PR (a ghost under the Pillar-1 definition, but
    # only 1/3 of its *open* external population, so the Stage-0 gate
    # at <= 50% still passes).
    PrSpec("acme", "core", 201, "frank-ext", "merged", 96.0,
           merged_hours_before=48.0, latency_hours=24.0, maintainer_comments=2),
    PrSpec("acme", "core", 202, "grace-ext", "opened", 72.0, latency_hours=48.0),
    PrSpec("acme", "core", 203, "heidi-ext", "opened", 300.0,
           latency_hours=36.0, maintainer_comments=1),
    PrSpec("acme", "core", 204, "ivan-vet", "opened", 500.0,
           prior_merged_commits=10),
    PrSpec("acme", "core", 205, "judy-ext", "closed", 600.0,
           closed_hours_before=400.0, latency_hours=12.0),
    PrSpec("acme", "core", 206, "kate-owner", "merged", 200.0,
           merged_hours_before=120.0, association="OWNER",
           prior_merged_commits=80, external=False),
    # ghost/legacy-lib (npm) — Black Hole: 3 of 3 open external PRs are
    # > 14 days old with zero maintainer comments (ghost rate 100% > 50%
    # gate) -> Stage-0 gate 2 disqualifies the repo.
    PrSpec("ghost", "legacy-lib", 301, "mallory-ext", "opened", 8760.0),
    PrSpec("ghost", "legacy-lib", 302, "niaj-ext", "opened", 720.0),
    PrSpec("ghost", "legacy-lib", 303, "oscar-ext", "opened", 1000.0,
           latency_hours=96.0),
    PrSpec("ghost", "legacy-lib", 304, "patty-ext", "merged", 100.0,
           merged_hours_before=50.0),
    PrSpec("ghost", "legacy-lib", 305, "quinn-maint", "opened", 3000.0,
           association="OWNER", prior_merged_commits=120, external=False),
    # kern/kernel-tools (crates) — mailing-list workflow: reviews happen
    # off-platform; Stage-0 gate 3 routes (not gates) the repo.
    PrSpec("kern", "kernel-tools", 401, "rupert-ext", "opened", 200.0,
           latency_hours=36.0, maintainer_comments=2),
    PrSpec("kern", "kernel-tools", 402, "sara-ext", "opened", 60.0,
           latency_hours=12.0, maintainer_comments=1),
    PrSpec("kern", "kernel-tools", 403, "tina-maint", "opened", 30.0,
           association="OWNER", prior_merged_commits=300, external=False),
    # acme/cli (npm) — tiny (20 stars) but the most responsive repo.
    PrSpec("acme", "cli", 501, "uma-ext", "merged", 300.0,
           merged_hours_before=240.0, latency_hours=10.0, maintainer_comments=3),
    PrSpec("acme", "cli", 502, "victor-ext", "opened", 90.0, latency_hours=6.0),
]

PR_INDEX: dict[tuple[str, str, int], PrSpec] = {
    (s.owner, s.name, s.number): s for s in PR_SPECS
}


def _pr_payload(spec: PrSpec) -> dict[str, Any]:
    pr: dict[str, Any] = {
        "number": spec.number,
        "state": (
            "closed"
            if spec.merged_hours_before is not None or spec.action == "closed"
            else "open"
        ),
        "user": {"login": spec.author},
        "created_at": _ts(spec.created_hours_before),
    }
    if spec.merged_hours_before is not None:
        pr["merged_at"] = _ts(spec.merged_hours_before)
        # Deliberately omit ``closed_at`` on merged records: the mapper's
        # documented fallback (closed_at = merged_at) is exercised here.
    if spec.action == "closed":
        pr["closed_at"] = _ts(spec.closed_hours_before)
    return pr


def _pr_record(seq: int, spec: PrSpec) -> dict[str, Any]:
    return {
        "id": f"gharch-{seq}",
        "type": "PullRequestEvent",
        "actor": {"id": 1000 + seq, "login": spec.author},
        "repo": {"id": 2000 + seq, "name": f"{spec.owner}/{spec.name}"},
        "created_at": "2026-09-10T03:00:00Z",
        "payload": {
            "action": spec.action,
            "number": spec.number,
            "pull_request": _pr_payload(spec),
        },
    }


#: Records that MUST be dropped by the streamer/mapper.
def _noise_records() -> list[dict[str, Any]]:
    foreign = PrSpec("other", "foreign", 1, "zed-ext", "opened", 100.0)
    labeled_pr = _pr_payload(PR_INDEX[("acme", "widgets", 101)])
    return [
        {  # push: not a PR event at all
            "id": "noise-1",
            "type": "PushEvent",
            "actor": {"id": 1, "login": "root-owner"},
            "repo": {"id": 2, "name": "acme/widgets"},
            "created_at": "2026-09-10T03:00:01Z",
            "payload": {"size": 3, "commits": []},
        },
        {  # issues: not a PR event
            "id": "noise-2",
            "type": "IssuesEvent",
            "actor": {"id": 1, "login": "frank-ext"},
            "repo": {"id": 2, "name": "acme/core"},
            "created_at": "2026-09-10T03:00:02Z",
            "payload": {"action": "opened", "issue": {"number": 55}},
        },
        {  # PR action outside the mapped set (edited/labeled/...)
            "id": "noise-3",
            "type": "PullRequestEvent",
            "actor": {"id": 1, "login": "alice-ext"},
            "repo": {"id": 2, "name": "acme/widgets"},
            "created_at": "2026-09-10T03:00:03Z",
            "payload": {"action": "labeled", "number": 101,
                        "pull_request": labeled_pr},
        },
        {  # valid PR event, but for a different repo -> filtered out
            "id": "noise-4",
            "type": "PullRequestEvent",
            "actor": {"id": 1, "login": "zed-ext"},
            "repo": {"id": 3, "name": "other/foreign"},
            "created_at": "2026-09-10T03:00:04Z",
            "payload": {"action": foreign.action, "number": foreign.number,
                        "pull_request": _pr_payload(foreign)},
        },
        {  # malformed repo field (no owner/name split)
            "id": "noise-5",
            "type": "PullRequestEvent",
            "actor": {"id": 1, "login": "wanda-ext"},
            "repo": {"id": 4, "name": "no-slash"},
            "created_at": "2026-09-10T03:00:05Z",
            "payload": {"action": "opened", "number": 77,
                        "pull_request": {"number": 77,
                                         "user": {"login": "wanda-ext"},
                                         "created_at": "2026-09-01T00:00:00Z"}},
        },
        {  # missing pull_request.created_at -> record time must NOT be used
            "id": "noise-6",
            "type": "PullRequestEvent",
            "actor": {"id": 1, "login": "xena-ext"},
            "repo": {"id": 5, "name": "acme/cli"},
            "created_at": "2026-09-10T03:00:06Z",
            "payload": {"action": "opened", "number": 599,
                        "pull_request": {"number": 599,
                                         "user": {"login": "xena-ext"}}},
        },
    ]


def build_partition_ndjson() -> bytes:
    """The full hourly partition body: 23 PR records + 6 noise + blank line."""
    records = [_pr_record(i + 1, spec) for i, spec in enumerate(PR_SPECS)]
    records.extend(_noise_records())
    lines = [json.dumps(record) for record in records]
    lines.insert(15, "")  # the streamer must skip blank lines
    return ("\n".join(lines) + "\n").encode("utf-8")


PARTITION_NDJSON: bytes = build_partition_ndjson()
PARTITION_GZIP: bytes = gzip.compress(PARTITION_NDJSON)


def hydrate_review_telemetry(events: list[RawPREvent]) -> None:
    """Simulate delta hydration of review telemetry onto streamed events.

    GHArchive ``PullRequestEvent`` payloads carry no author association,
    review latency, or comment counts (see ``cvi.ingest`` docstring), so
    the conservative mapping defaults them all. This pass applies the
    per-PR telemetry the delta-hydrated GitHub path would fetch, in
    place, so the real pillar scorers see meaningful variation.
    """
    for event in events:
        spec = PR_INDEX.get((event.repo_owner, event.repo_name, event.pr_number))
        if spec is None:
            raise AssertionError(
                f"unexpected PR in stream: {event.repo_owner}/{event.repo_name}#{event.pr_number}"
            )
        event.author_association = AuthorAssociation(spec.association)
        event.author_prior_merged_commits = spec.prior_merged_commits
        event.is_external = spec.external
        event.response_latency_hours = spec.latency_hours
        event.maintainer_review_comments = spec.maintainer_comments
        if spec.latency_hours is not None:
            event.first_human_review_at = event.created_at + timedelta(
                hours=spec.latency_hours
            )


# --------------------------------------------------------------------------- #
# Fixture data: repo-level meta + raw metrics
# --------------------------------------------------------------------------- #


def _signals(created: float, updated: float, contributors: int, orgs: int,
             commits_weekly: float, releases: int, closed90: int,
             updated_issues90: int, comments: float, dependents: int) -> OpenSSFSignals:
    return OpenSSFSignals(
        created_since_months=created,
        updated_since_months=updated,
        contributor_count=contributors,
        org_count=orgs,
        commit_frequency_weekly=commits_weekly,
        recent_releases_count=releases,
        closed_issues_count_90d=closed90,
        updated_issues_count_90d=updated_issues90,
        comment_frequency=comments,
        dependents_count=dependents,
    )


@dataclass(frozen=True)
class RepoSpec:
    owner: str
    name: str
    ecosystem: Ecosystem
    stars: int
    forks: int
    monthly_downloads: int
    dependents_count: int
    elephant_factor: int
    legal_ip_model: LegalIPModel
    ai_posture: AIPosture
    scorecard_score: float
    active_nonbot_commits_180d: int
    off_platform_reviews: bool
    mean_inline_comments: float
    active_unclaimed_gfis: int
    rr_90: float
    dx_ci_minutes: float
    promotion_ladder: PromotionLadder
    openssf_signals: Optional[OpenSSFSignals]
    ttm_p90_population: tuple[float, ...]
    dependents_population: tuple[float, ...]
    downloads_population: tuple[float, ...]

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"

    def meta(self) -> RepoMeta:
        return RepoMeta(
            owner=self.owner,
            name=self.name,
            ecosystem=self.ecosystem,
            stars=self.stars,
            forks=self.forks,
            monthly_downloads=self.monthly_downloads,
            dependents_count=self.dependents_count,
            elephant_factor=self.elephant_factor,
            legal_ip_model=self.legal_ip_model,
            ai_posture=self.ai_posture,
            scorecard_score=self.scorecard_score,
            active_nonbot_commits_180d=self.active_nonbot_commits_180d,
            off_platform_reviews=self.off_platform_reviews,
            mean_inline_comments=self.mean_inline_comments,
            active_unclaimed_gfis=self.active_unclaimed_gfis,
            rr_90=self.rr_90,
            dx_ci_minutes=self.dx_ci_minutes,
            promotion_ladder=self.promotion_ladder,
            openssf_signals=self.openssf_signals,
        )

    @property
    def raw_metrics(self) -> dict[str, Any]:
        return {
            "stars": self.stars,
            "forks": self.forks,
            "monthly_downloads": self.monthly_downloads,
            "dependents_count": self.dependents_count,
            "elephant_factor": self.elephant_factor,
            "legal_ip_model": self.legal_ip_model,
            "ai_posture": self.ai_posture,
        }


REPOS: list[RepoSpec] = [
    RepoSpec(
        "acme", "widgets", Ecosystem.PYPI,
        stars=45000, forks=5200, monthly_downloads=50_000_000,
        dependents_count=99_999, elephant_factor=4,
        legal_ip_model=LegalIPModel.FOUNDATION_CLA, ai_posture=AIPosture.REGULATED,
        scorecard_score=8.5, active_nonbot_commits_180d=350, off_platform_reviews=False,
        mean_inline_comments=3.5, active_unclaimed_gfis=10, rr_90=0.5, dx_ci_minutes=15.0,
        promotion_ladder=PromotionLadder.VERIFIED_PROMOTION,
        openssf_signals=_signals(36.0, 1.0, 4200, 8, 60.0, 20, 900, 700, 12.0, 99_999),
        ttm_p90_population=tuple(PYPY_TTM),
        dependents_population=tuple(PYPY_DEPENDENTS),
        downloads_population=tuple(PYPY_DOWNLOADS),
    ),
    RepoSpec(
        "acme", "core", Ecosystem.PYPI,
        stars=12000, forks=2100, monthly_downloads=5_000_000,
        dependents_count=999, elephant_factor=3,
        legal_ip_model=LegalIPModel.DCO, ai_posture=AIPosture.AD_HOC,
        scorecard_score=7.0, active_nonbot_commits_180d=120, off_platform_reviews=False,
        mean_inline_comments=2.5, active_unclaimed_gfis=5, rr_90=0.25, dx_ci_minutes=30.0,
        promotion_ladder=PromotionLadder.DOCUMENTED_PATHWAY,
        openssf_signals=_signals(60.0, 3.0, 1800, 5, 30.0, 12, 500, 300, 8.0, 999),
        ttm_p90_population=tuple(PYPY_TTM),
        dependents_population=tuple(PYPY_DEPENDENTS),
        downloads_population=tuple(PYPY_DOWNLOADS),
    ),
    RepoSpec(
        "ghost", "legacy-lib", Ecosystem.NPM,
        stars=800, forks=95, monthly_downloads=500_000,
        dependents_count=99, elephant_factor=1,
        legal_ip_model=LegalIPModel.DCO, ai_posture=AIPosture.AD_HOC,
        scorecard_score=3.0, active_nonbot_commits_180d=5, off_platform_reviews=False,
        mean_inline_comments=0.5, active_unclaimed_gfis=0, rr_90=0.0, dx_ci_minutes=0.0,
        promotion_ladder=PromotionLadder.CLOSED_SINGLE_FIRM,
        openssf_signals=None,
        ttm_p90_population=tuple(NPM_TTM),
        dependents_population=tuple(NPM_DEPENDENTS),
        downloads_population=tuple(NPM_DOWNLOADS),
    ),
    RepoSpec(
        "kern", "kernel-tools", Ecosystem.CRATES,
        stars=3500, forks=410, monthly_downloads=500_000,
        dependents_count=9999, elephant_factor=2,
        legal_ip_model=LegalIPModel.CORP_CLA_UNILATERAL,
        ai_posture=AIPosture.PROSCRIBED,
        scorecard_score=5.0, active_nonbot_commits_180d=40, off_platform_reviews=True,
        mean_inline_comments=4.5, active_unclaimed_gfis=2, rr_90=0.1, dx_ci_minutes=0.0,
        promotion_ladder=PromotionLadder.AD_HOC,
        openssf_signals=_signals(24.0, 0.5, 900, 4, 25.0, 8, 250, 150, 6.0, 9999),
        ttm_p90_population=tuple(CRATES_TTM),
        dependents_population=tuple(CRATES_DEPENDENTS),
        downloads_population=tuple(CRATES_DOWNLOADS),
    ),
    RepoSpec(
        "acme", "cli", Ecosystem.NPM,
        stars=20, forks=3, monthly_downloads=50_000,
        dependents_count=99, elephant_factor=1,
        legal_ip_model=LegalIPModel.DCO, ai_posture=AIPosture.AD_HOC,
        scorecard_score=4.0, active_nonbot_commits_180d=12, off_platform_reviews=False,
        mean_inline_comments=1.5, active_unclaimed_gfis=4, rr_90=0.3, dx_ci_minutes=18.0,
        promotion_ladder=PromotionLadder.AD_HOC,
        openssf_signals=_signals(8.0, 1.0, 120, 2, 6.0, 3, 40, 25, 2.0, 99),
        ttm_p90_population=tuple(NPM_TTM),
        dependents_population=tuple(NPM_DEPENDENTS),
        downloads_population=tuple(NPM_DOWNLOADS),
    ),
]
REPO_BY_SLUG: dict[str, RepoSpec] = {repo.slug: repo for repo in REPOS}

def store_access(pipeline: Pipeline, fn: Callable[[], Any]) -> Any:
    """Run one store access on the pipeline's portal thread (sqlite affinity)."""
    return pipeline.portal.call(fn)


# --------------------------------------------------------------------------- #
# Pinned expectations (computed by the real scorers from the fixtures above)
# --------------------------------------------------------------------------- #

#: (owner, name, overall_cvi, tier) in leaderboard order (cvi DESC).
EXPECTED_LEADERBOARD = [
    ("acme", "widgets", 91.34, "Tier S+"),
    ("acme", "core", 65.43, "Tier B"),
    ("acme", "cli", 62.74, "Tier B"),
    ("kern", "kernel-tools", 58.91, "Tier B"),
    ("ghost", "legacy-lib", 0.0, "Tier D"),
]

EXPECTED_QUADRANT_COUNTS = {
    "Sweet Spot": 2,  # acme/widgets, acme/core
    "Crucible": 1,    # kern/kernel-tools
    "Nursery": 1,     # acme/cli
    "Graveyard": 1,   # ghost/legacy-lib (gated zero-pillar decomposition)
}

#: Pinned pillar scores (legacy-lib is gated: all pillar columns are 0.0).
EXPECTED_PILLARS: dict[str, dict[str, float]] = {
    "acme/widgets": {"responsiveness": 88.4375, "criticality": 88.5567955162,
                     "governance": 91.25, "rigor": 92.25, "accessibility": 100.0},
    "acme/core": {"responsiveness": 63.0581291697, "criticality": 64.4398681148,
                  "governance": 83.75, "rigor": 62.0184350729, "accessibility": 50.0},
    "acme/cli": {"responsiveness": 92.0, "criticality": 52.9908311179,
                 "governance": 55.75, "rigor": 40.2778072627, "accessibility": 62.0},
    "kern/kernel-tools": {"responsiveness": 68.75, "criticality": 82.1151778877,
                          "governance": 33.0, "rigor": 69.7684350729,
                          "accessibility": 27.5},
}

EXPECTED_COORDINATES: dict[str, tuple[float, float]] = {
    "acme/widgets": (90.20, 8.41),
    "acme/core": (67.58, 33.35),
    "acme/cli": (49.73, 24.88),
    "kern/kernel-tools": (68.59, 50.22),
    "ghost/legacy-lib": (0.0, 100.0),
}

EXPECTED_QUADRANT_BY_REPO = {
    "acme/widgets": "Sweet Spot",
    "acme/core": "Sweet Spot",
    "acme/cli": "Nursery",
    "kern/kernel-tools": "Crucible",
    "ghost/legacy-lib": "Graveyard",
}

#: Pinned ``reproject`` outputs under REPROJECT_WEIGHTS (2-decimal rounding
#: is part of the ``cvi.persona.reproject`` contract).
EXPECTED_REPROJECT = {
    "acme/widgets": {"old_rank": 1, "new_rank": 1, "delta": 0, "new_score": 90.24},
    "acme/core": {"old_rank": 2, "new_rank": 3, "delta": -1, "new_score": 65.15},
    "acme/cli": {"old_rank": 3, "new_rank": 2, "delta": 1, "new_score": 71.22},
    "kern/kernel-tools": {"old_rank": 4, "new_rank": 4, "delta": 0, "new_score": 62.65},
    "ghost/legacy-lib": {"old_rank": 5, "new_rank": 5, "delta": 0, "new_score": 0.0},
}

#: PillarScores attribute -> repositories table column (spec 11.2).
PILLAR_COLUMNS = {
    "responsiveness": "score_responsiveness",
    "criticality": "score_criticality",
    "governance": "score_governance",
    "rigor": "score_rigor_ai",
    "accessibility": "score_accessibility",
}

#: Pinned mapping counts per repo from the fake partition.
EXPECTED_MAPPING_COUNTS = {
    "acme/widgets": 7,
    "acme/core": 6,
    "ghost/legacy-lib": 5,
    "kern/kernel-tools": 3,
    "acme/cli": 2,
}


def _partition_records() -> list[dict[str, Any]]:
    """All 29 NDJSON records of the fabricated partition, in order."""
    return [
        json.loads(line)
        for line in PARTITION_NDJSON.splitlines()
        if line.strip()
    ]


def pipeline_events_from_raw_partition() -> dict[str, list[RawPREvent]]:
    """Stream the raw (unhydrated) partition through a fresh in-memory client.

    Used to inspect the conservative mapping defaults before the pipeline's
    hydration pass rewrites the events.
    """
    clock = FakeClock(0.0)
    transport = FakeTransport(make_partition_handler())
    # rate=1.0: exact-1s waits, avoids a float livelock (see pipeline fixture)
    limiter = TokenBucketRateLimiter(
        rate=1.0, capacity=1.0, clock=clock, sleep=clock.advance
    )
    breaker = CircuitBreaker(clock=clock, sleep=clock.advance, jitter_fn=lambda: 0.0)
    client = CachedHttpClient(transport=transport, rate_limiter=limiter, circuit_breaker=breaker)
    streamer = GHArchiveStreamer(client)
    return {
        repo.slug: streamer.stream_pr_events(PARTITION_WHEN, repo.owner, repo.name)
        for repo in REPOS
    }


# --------------------------------------------------------------------------- #
# Fakes (offline HTTP + deterministic clocks)
# --------------------------------------------------------------------------- #


class FakeClock:
    """Callable clock; ``advance`` doubles as the limiter/breaker sleep."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class FakeTransport(httpx.BaseTransport):
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


def make_partition_handler(
    include_etag: bool = False,
) -> Callable[[httpx.Request], httpx.Response]:
    """Serve the fabricated partition.

    ``include_etag=True`` adds an ``ETag`` header and answers conditional
    requests with 304 Not Modified (zero-quota delta hydration, spec 13).
    The pipeline fixture uses the plain 200-only variant: ``GHArchiveStreamer.fetch_partition``
    intentionally raises on non-200, so repeated streamer fetches through
    one client must stay unconditional.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == PARTITION_URL
        if include_etag and request.headers.get("if-none-match") == ETAG:
            return httpx.Response(304, headers={"ETag": ETAG})
        headers = {"Content-Type": "application/x-gzip"}
        if include_etag:
            headers["ETag"] = ETAG
        return httpx.Response(200, content=PARTITION_GZIP, headers=headers)

    return handler


@dataclass
class Pipeline:
    """Everything one end-to-end pipeline run produced.

    The store (and its sqlite connection) was created on the TestClient's
    portal thread, mirroring ``tests/test_api.py``: a sqlite connection is
    thread-affine, so every direct store access must happen through
    ``portal.call`` (the API requests already run on that thread).
    """

    store: OperationalStore
    client: TestClient
    portal: Any
    http_client: CachedHttpClient
    transport: FakeTransport
    clock: FakeClock
    streamer: GHArchiveStreamer
    events: dict[str, list[RawPREvent]]
    results: dict[str, CVICompositeResult]
    rowids: list[int]


def _build_pipeline_state() -> tuple[dict[str, Any], list[int]]:
    """Stream -> hydrate -> score -> store; executed on the portal thread."""
    clock = FakeClock(0.0)
    transport = FakeTransport(make_partition_handler())
    # NOTE: rate=1.0, not the spec's 1.2. With rate=1.2 the limiter's
    # 4th acquire livelocks under a fake clock: after two waits of
    # 1/1.2 s the clock lands on 2.5, and (2.5 - 1.6666666666666667)
    # * 1.2 rounds to 0.9999999999999999 < 1.0, so the follow-up sleeps
    # are smaller than the clock's float ULP and never refill a token.
    # rate=1.0 makes every wait an exact 1.0 s, keeping the throttle
    # deterministic without the float edge case.
    limiter = TokenBucketRateLimiter(
        rate=1.0, capacity=1.0, clock=clock, sleep=clock.advance
    )
    breaker = CircuitBreaker(clock=clock, sleep=clock.advance, jitter_fn=lambda: 0.0)
    http_client = CachedHttpClient(
        transport=transport, rate_limiter=limiter, circuit_breaker=breaker
    )
    streamer = GHArchiveStreamer(http_client)

    store = OperationalStore()
    events: dict[str, list[RawPREvent]] = {}
    results: dict[str, CVICompositeResult] = {}
    rowids: list[int] = []
    for repo in REPOS:
        repo_events = streamer.stream_pr_events(PARTITION_WHEN, repo.owner, repo.name)
        hydrate_review_telemetry(repo_events)
        events[repo.slug] = repo_events
        results[repo.slug] = score_repository(
            repo.meta(),
            repo_events,
            NOW,
            persona=Persona.BALANCED,
            ttm_p90_population=list(repo.ttm_p90_population),
            dependents_population=list(repo.dependents_population),
            downloads_population=list(repo.downloads_population),
        )
        rowids.append(
            ingest_and_score(
                store,
                repo.meta(),
                repo_events,
                NOW,
                raw_metrics=repo.raw_metrics,
                persona=Persona.BALANCED,
                ttm_p90_population=list(repo.ttm_p90_population),
                dependents_population=list(repo.dependents_population),
                downloads_population=list(repo.downloads_population),
                last_indexed_at=NOW,
            )
        )

    state = {
        "store": store,
        "http_client": http_client,
        "transport": transport,
        "clock": clock,
        "streamer": streamer,
        "events": events,
        "results": results,
        "rowids": rowids,
    }
    return state, rowids


@pytest.fixture()
def pipeline() -> Pipeline:
    """Run the whole scenario once: stream -> hydrate -> score -> store -> app.

    The pipeline (including ``OperationalStore``) is built inside
    ``client.portal.call`` so the sqlite connection is created on the
    TestClient's portal thread — the same thread that serves the API
    requests — which is what makes ``create_app(store=that_store)`` usable
    without a cross-thread ``sqlite3.ProgrammingError``.
    """
    app = create_app()  # default store (unused; replaced below)
    client = TestClient(app)
    client.__enter__()
    try:
        state, rowids = client.portal.call(_build_pipeline_state)
        app.state.store = state["store"]
    except BaseException:
        client.__exit__(None, None, None)
        raise
    yield Pipeline(
        store=state["store"],
        client=client,
        portal=client.portal,
        http_client=state["http_client"],
        transport=state["transport"],
        clock=state["clock"],
        streamer=state["streamer"],
        events=state["events"],
        results=state["results"],
        rowids=rowids,
    )
    client.__exit__(None, None, None)


# --------------------------------------------------------------------------- #
# Stage 1: GHArchive streaming + conservative mapping
# --------------------------------------------------------------------------- #


class TestPartitionStreaming:
    def test_partition_shape_and_mapping_counts(self, pipeline: Pipeline) -> None:
        records = [json.loads(line) for line in PARTITION_NDJSON.splitlines() if line.strip()]
        assert len(records) == 29  # 23 mapped PRs + 6 noise
        # Exactly one noise record (other/foreign) maps but is filtered out
        # by the owner/name check; the other five are unmappable.
        mappable = [r for r in records if map_pull_request_event(r) is not None]
        assert len(mappable) == 24
        for slug, expected in EXPECTED_MAPPING_COUNTS.items():
            assert len(pipeline.events[slug]) == expected
        assert sum(len(e) for e in pipeline.events.values()) == 23
        # Five repo streams -> five GETs, all for the same partition, and
        # five quota charges through the strict 1.0 req/s token bucket
        # (four exact 1.0 s token waits after the initial burst token).
        assert len(pipeline.transport.requests) == 5
        assert {str(r.url) for r in pipeline.transport.requests} == {PARTITION_URL}
        assert pipeline.http_client.quota_consumed == 5
        assert pipeline.clock.now == pytest.approx(4.0)

    def test_streamed_events_carry_conservative_mapping_defaults(self) -> None:
        raw_events = pipeline_events_from_raw_partition()["acme/widgets"]
        assert [event.pr_number for event in raw_events] == [101, 102, 103, 104, 105, 106, 107]
        # The raw stream: GHArchive carries no review telemetry and no
        # author association, so every *mapped* event shares the spec-9.1
        # conservative defaults before the hydration pass.
        for record in _partition_records():
            event = map_pull_request_event(record)
            if event is None:
                continue
            assert event.author_association is AuthorAssociation.NONE
            assert event.is_external is True
            assert event.author_prior_merged_commits == 0
            assert event.first_human_review_at is None
            assert event.response_latency_hours is None
            assert event.maintainer_review_comments == 0
            assert event.ci_status_green is True

    def test_hydrated_events_vary_review_telemetry(self, pipeline: Pipeline) -> None:
        widgets = pipeline.events["acme/widgets"]
        by_number = {event.pr_number: event for event in widgets}
        # External vs internal, maintainer comments, and first-human-review
        # latencies now vary across the population the real scorers see.
        assert by_number[106].is_external is False
        assert by_number[106].author_association is AuthorAssociation.OWNER
        assert by_number[101].is_external is True
        assert by_number[101].maintainer_review_comments == 4
        assert by_number[105].maintainer_review_comments == 0
        assert by_number[101].first_human_review_at == (
            by_number[101].created_at + timedelta(hours=6.0)
        )
        external_latencies = sorted(
            event.response_latency_hours
            for event in widgets
            if event.is_external and event.response_latency_hours is not None
        )
        assert external_latencies == [4.0, 6.0, 8.0, 10.0, 12.0]

    def test_merged_without_closed_at_falls_back_to_merged_at(
        self, pipeline: Pipeline
    ) -> None:
        by_number = {event.pr_number: event for event in pipeline.events["acme/widgets"]}
        merged = by_number[101]
        assert merged.is_merged is True
        assert merged.merged_at == NOW - timedelta(hours=1176.0)
        assert merged.closed_at == merged.merged_at  # mapper fallback
        closed_unmerged = by_number[103]
        assert closed_unmerged.is_closed_unmerged is True
        assert closed_unmerged.merged_at is None
        assert closed_unmerged.closed_at == NOW - timedelta(hours=432.0)

    def test_repeated_partition_fetch_is_served_from_etag_cache(self) -> None:
        clock = FakeClock(0.0)
        transport = FakeTransport(make_partition_handler(include_etag=True))
        limiter = TokenBucketRateLimiter(rate=1.0, capacity=1.0,
                                         clock=clock, sleep=clock.advance)
        breaker = CircuitBreaker(clock=clock, sleep=clock.advance,
                                 jitter_fn=lambda: 0.0)
        client = CachedHttpClient(transport=transport, rate_limiter=limiter,
                                  circuit_breaker=breaker)
        first = client.get(PARTITION_URL)
        assert first.status_code == 200
        assert first.from_cache is False
        assert first.content == PARTITION_GZIP
        assert client.quota_consumed == 1
        assert len(client.etag_cache) == 1
        second = client.get(PARTITION_URL)
        assert second.status_code == 304
        assert second.from_cache is True
        # The 304 body is the cached partition; gunzipping it yields NDJSON.
        assert second.content == PARTITION_GZIP
        assert gzip.decompress(second.content) == PARTITION_NDJSON
        # 304 consumes zero provider quota (spec 13).
        assert client.quota_consumed == 1
        assert transport.requests[1].headers.get("if-none-match") == ETAG
        assert len(transport.requests) == 2
        # Two GETs through the strict 1.0 req/s bucket: one token wait.
        assert clock.now == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Stage 2: store assertions (upsert + raw-metric hydration)
# --------------------------------------------------------------------------- #


class TestStoreAfterIngestion:
    def test_five_upserts_five_rowids_hydrated_raw_metrics(
        self, pipeline: Pipeline
    ) -> None:
        assert len(pipeline.rowids) == 5
        assert len(set(pipeline.rowids)) == 5
        sql = (
            "SELECT owner, name, stars, forks, monthly_downloads, "
            "dependents_count, elephant_factor, legal_ip_model, ai_posture, "
            "last_indexed_at FROM repositories ORDER BY owner, name"
        )
        rows = store_access(
            pipeline, lambda: pipeline.store.connection.execute(sql).fetchall()
        )
        indexed_at = "2026-09-10T12:00:00"
        assert rows == [
            ("acme", "cli", 20, 3, 50_000, 99, 1, "DCO", "AD_HOC", indexed_at),
            ("acme", "core", 12000, 2100, 5_000_000, 999, 3, "DCO", "AD_HOC", indexed_at),
            ("acme", "widgets", 45000, 5200, 50_000_000, 99_999, 4,
             "FOUNDATION_CLA", "REGULATED", indexed_at),
            ("ghost", "legacy-lib", 800, 95, 500_000, 99, 1, "DCO", "AD_HOC",
             indexed_at),
            ("kern", "kernel-tools", 3500, 410, 500_000, 9999, 2,
             "CORP_CLA_UNILATERAL", "PROSCRIBED", indexed_at),
        ]
        # A hydrate call against an unknown repo is a no-op, not an error.
        assert store_access(
            pipeline,
            lambda: apply_raw_metrics(
                pipeline.store, "ghost", "no-such-repo", stars=1, forks=0
            ),
        ) == 0

    def test_query_matrix_min_stars_returns_exact_rows_with_stars(
        self, pipeline: Pipeline
    ) -> None:
        # If upsert_composite's stars=0 default had survived, every
        # min_stars > 0 query would come back empty; the exact sizes below
        # prove apply_raw_metrics fixed the column.
        nodes = store_access(
            pipeline, lambda: pipeline.store.query_matrix(min_stars=0)
        )
        assert [(n["owner"], n["name"], n["size"]) for n in nodes] == [
            ("acme", "widgets", 45000),
            ("acme", "core", 12000),
            ("acme", "cli", 20),
            ("kern", "kernel-tools", 3500),
            ("ghost", "legacy-lib", 800),
        ]
        # 21 excludes acme/cli (20 stars) but still includes the gated
        # legacy-lib — the store filter is on stars only.
        nodes = store_access(
            pipeline, lambda: pipeline.store.query_matrix(min_stars=21)
        )
        assert [(n["owner"], n["name"], n["size"]) for n in nodes] == [
            ("acme", "widgets", 45000),
            ("acme", "core", 12000),
            ("kern", "kernel-tools", 3500),
            ("ghost", "legacy-lib", 800),
        ]
        assert [(n["owner"], n["name"]) for n in store_access(
            pipeline, lambda: pipeline.store.query_matrix(min_stars=20000)
        )] == [
            ("acme", "widgets"),
        ]
        # Inclusive boundary: exactly 45000 stars still qualifies.
        assert [(n["owner"], n["name"]) for n in store_access(
            pipeline, lambda: pipeline.store.query_matrix(min_stars=45000)
        )] == [
            ("acme", "widgets"),
        ]
        assert store_access(
            pipeline, lambda: pipeline.store.query_matrix(min_stars=45001)
        ) == []

    def test_query_matrix_ecosystem_and_quadrant_filters(self, pipeline: Pipeline) -> None:
        pypi = store_access(
            pipeline, lambda: pipeline.store.query_matrix(ecosystem="pypi", min_stars=0)
        )
        assert [(n["owner"], n["name"]) for n in pypi] == [
            ("acme", "widgets"),
            ("acme", "core"),
        ]
        crucible = store_access(
            pipeline, lambda: pipeline.store.query_matrix(quadrant="Crucible")
        )
        assert [(n["owner"], n["name"], n["x"], n["y"]) for n in crucible] == [
            ("kern", "kernel-tools", 50.22, 68.59),
        ]
        gated = store_access(
            pipeline,
            lambda: pipeline.store.query_matrix(min_stars=0, exclude_black_holes=True),
        )
        assert all(n["is_gated"] == 0 for n in gated)
        assert [n["name"] for n in gated] == ["widgets", "core", "cli", "kernel-tools"]

    def test_count_by_quadrant_matches_upsert_total(self, pipeline: Pipeline) -> None:
        counts = store_access(pipeline, pipeline.store.count_by_quadrant)
        assert counts == EXPECTED_QUADRANT_COUNTS
        # All four keys always present, total == number of upserts.
        assert set(counts) == {"Sweet Spot", "Crucible", "Nursery", "Graveyard"}
        assert sum(counts.values()) == len(pipeline.rowids) == 5
        # Ecosystem-scoped counts still cover all four keys.
        npm_counts = store_access(
            pipeline, lambda: pipeline.store.count_by_quadrant(ecosystem="npm")
        )
        assert npm_counts == {
            "Sweet Spot": 0, "Crucible": 0, "Nursery": 1, "Graveyard": 1,
        }

    def test_leaderboard_order_matches_expected_cvi_order(self, pipeline: Pipeline) -> None:
        rows = store_access(pipeline, pipeline.store.leaderboard)
        assert [(r["owner"], r["name"]) for r in rows] == [
            (owner, name) for owner, name, _, _ in EXPECTED_LEADERBOARD
        ]
        for row, (owner, name, cvi, tier) in zip(rows, EXPECTED_LEADERBOARD):
            assert row["overall_cvi"] == pytest.approx(cvi)
            assert row["rank_tier"] == tier
            assert row["owner"] == owner and row["name"] == name
        cvi_values = [r["overall_cvi"] for r in rows]
        assert cvi_values == sorted(cvi_values, reverse=True)

    def test_get_repo_full_row_includes_pillar_scores(self, pipeline: Pipeline) -> None:
        row = store_access(
            pipeline, lambda: pipeline.store.get_repo("acme", "widgets")
        )
        assert row is not None
        assert row["id"] == pipeline.rowids[0]
        assert row["owner"] == "acme" and row["name"] == "widgets"
        assert row["ecosystem"] == "pypi"
        assert row["stars"] == 45000
        assert row["overall_cvi"] == pytest.approx(91.34)
        assert row["rank_tier"] == "Tier S+"
        assert row["matrix_quadrant"] == "Sweet Spot"
        assert row["is_stage0_disqualified"] == 0
        assert row["disqualification_reason"] is None
        leverage, friction = EXPECTED_COORDINATES["acme/widgets"]
        assert row["leverage_score"] == pytest.approx(leverage)
        assert row["friction_score"] == pytest.approx(friction)
        for pillar, expected in EXPECTED_PILLARS["acme/widgets"].items():
            column = PILLAR_COLUMNS[pillar]
            assert row[column] == pytest.approx(expected, rel=1e-7, abs=1e-9)

    def test_get_repo_ghost_repo_row_is_black_hole_gated(self, pipeline: Pipeline) -> None:
        row = store_access(
            pipeline, lambda: pipeline.store.get_repo("ghost", "legacy-lib")
        )
        assert row is not None
        assert row["overall_cvi"] == 0.0
        assert row["rank_tier"] == "Tier D"
        assert row["matrix_quadrant"] == "Graveyard"
        assert row["leverage_score"] == 0.0
        assert row["friction_score"] == 100.0
        assert row["is_stage0_disqualified"] == 1
        assert row["disqualification_reason"] == "PR ghost rate > 50% (black hole)"
        # Gated rows still persist zero pillars and hydrated raw metrics.
        assert row["score_responsiveness"] == 0.0
        assert row["stars"] == 800
        # The result object records the black-hole classification.
        result = pipeline.results["ghost/legacy-lib"]
        assert result.is_stage0_disqualified is True
        assert result.stage0 is not None
        assert result.stage0.is_black_hole is True
        assert result.stage0.passed is False

    def test_off_platform_repo_is_routed_not_gated(self, pipeline: Pipeline) -> None:
        result = pipeline.results["kern/kernel-tools"]
        assert result.is_stage0_disqualified is False
        assert result.stage0 is not None
        assert result.stage0.passed is True
        assert result.stage0.is_off_platform is True
        assert result.stage0.reason == "off-platform review workflow (specialized routing)"
        row = store_access(
            pipeline, lambda: pipeline.store.get_repo("kern", "kernel-tools")
        )
        assert row is not None
        assert row["matrix_quadrant"] == "Crucible"
        assert row["rank_tier"] == "Tier B"
        assert row["is_stage0_disqualified"] == 0

    def test_responsiveness_pillar_scores_are_meaningfully_distinct(
        self, pipeline: Pipeline
    ) -> None:
        values = {slug: pipeline.results[slug].pillar_scores.responsiveness
                  for slug in EXPECTED_PILLARS}
        assert len(set(values.values())) == 4
        pair_differences = [
            abs(a - b)
            for i, a in enumerate(values.values())
            for b in list(values.values())[i + 1:]
        ]
        assert min(pair_differences) >= 3.0
        for slug, expected in EXPECTED_PILLARS.items():
            assert values[slug] == pytest.approx(expected["responsiveness"], rel=1e-7, abs=1e-9)
            assert 0.0 <= values[slug] <= 100.0


# --------------------------------------------------------------------------- #
# Stage 3: API surface
# --------------------------------------------------------------------------- #


class TestApi:
    def test_matrix_endpoint_all_nodes_and_quadrant_counts(self, pipeline: Pipeline) -> None:
        resp = pipeline.client.get(
            "/v1/cvi/matrix", params={"min_stars": 0, "exclude_black_holes": False}
        )
        assert resp.status_code == 200
        body = resp.json()
        nodes = body["nodes"]
        # One node per ingested repo, in overall_cvi DESC order.
        assert [n["repo_id"] for n in nodes] == [
            "acme/widgets", "acme/core", "acme/cli", "kern/kernel-tools",
            "ghost/legacy-lib",
        ]
        by_id = {n["repo_id"]: n for n in nodes}
        top = by_id["acme/widgets"]
        assert top["size"] == 45000
        assert top["cvi"] == pytest.approx(91.34)
        assert top["tier"] == "Tier S+"
        assert top["quadrant"] == "Sweet Spot"
        assert top["x"] == pytest.approx(8.41)
        assert top["y"] == pytest.approx(90.20)
        assert top["is_gated"] == 0
        ghost = by_id["ghost/legacy-lib"]
        assert ghost["size"] == 800
        assert ghost["quadrant"] == "Graveyard"
        assert ghost["is_gated"] == 1
        cli = by_id["acme/cli"]
        assert cli["size"] == 20
        assert cli["quadrant"] == "Nursery"
        assert body["quadrant_counts"] == EXPECTED_QUADRANT_COUNTS

    def test_matrix_endpoint_defaults_exclude_black_holes(self, pipeline: Pipeline) -> None:
        resp = pipeline.client.get("/v1/cvi/matrix", params={"min_stars": 0})
        assert resp.status_code == 200
        nodes = resp.json()["nodes"]
        assert [n["repo_id"] for n in nodes] == [
            "acme/widgets", "acme/core", "acme/cli", "kern/kernel-tools",
        ]
        # Quadrant counts still reflect the full table, not the filter.
        assert resp.json()["quadrant_counts"] == EXPECTED_QUADRANT_COUNTS
        # min_stars=100 drops acme/cli (20 stars); the default
        # exclude_black_holes=true also hides the gated ghost/legacy-lib.
        resp = pipeline.client.get("/v1/cvi/matrix", params={"min_stars": 100})
        assert [n["repo_id"] for n in resp.json()["nodes"]] == [
            "acme/widgets", "acme/core", "kern/kernel-tools",
        ]

    def test_repo_deep_dive_matches_stored_row(self, pipeline: Pipeline) -> None:
        stored = store_access(
            pipeline, lambda: pipeline.store.get_repo("acme", "widgets")
        )
        expected = {
            **stored,
            "pillar_scores": {
                "responsiveness": stored["score_responsiveness"],
                "criticality": stored["score_criticality"],
                "governance": stored["score_governance"],
                "rigor": stored["score_rigor_ai"],
                "accessibility": stored["score_accessibility"],
            },
        }
        resp = pipeline.client.get("/v1/cvi/repos/acme/widgets")
        assert resp.status_code == 200
        assert resp.json() == expected
        for pillar, value in EXPECTED_PILLARS["acme/widgets"].items():
            assert expected["pillar_scores"][pillar] == pytest.approx(
                value, rel=1e-7, abs=1e-9
            )

    def test_repo_deep_dive_404_for_unknown_repo(self, pipeline: Pipeline) -> None:
        resp = pipeline.client.get("/v1/cvi/repos/nobody/nowhere")
        assert resp.status_code == 404
        assert "nobody/nowhere" in resp.json()["detail"]

    def test_reproject_reorders_two_repos_with_signed_deltas(self, pipeline: Pipeline) -> None:
        resp = pipeline.client.post(
            "/v1/cvi/reproject", json={"weights": REPROJECT_WEIGHTS}
        )
        assert resp.status_code == 200
        items = resp.json()
        assert [i["repo_id"] for i in items] == [
            "acme/widgets", "acme/cli", "acme/core", "kern/kernel-tools",
            "ghost/legacy-lib",
        ]
        by_id = {i["repo_id"]: i for i in items}
        for repo_id, expected in EXPECTED_REPROJECT.items():
            item = by_id[repo_id]
            assert item["old_rank"] == expected["old_rank"]
            assert item["new_rank"] == expected["new_rank"]
            assert item["delta"] == expected["delta"]
            assert item["new_score"] == pytest.approx(expected["new_score"], abs=1e-9)
        moved = [i for i in items if i["delta"] != 0]
        assert len(moved) >= 2
        # Delta sign encodes direction: positive = moved up the ranking.
        for item in moved:
            assert (item["delta"] > 0) == (item["new_rank"] < item["old_rank"])
        # 2D coordinates travel with each item.
        assert by_id["acme/cli"]["x"] == pytest.approx(24.88)
        assert by_id["acme/cli"]["y"] == pytest.approx(49.73)
        assert by_id["acme/cli"]["quadrant"] == "Nursery"
        assert by_id["kern/kernel-tools"]["quadrant"] == "Crucible"

    def test_reproject_rejects_invalid_weights_with_422(self, pipeline: Pipeline) -> None:
        missing_key = {k: v for k, v in REPROJECT_WEIGHTS.items() if k != "accessibility"}
        resp = pipeline.client.post(
            "/v1/cvi/reproject", json={"weights": missing_key}
        )
        assert resp.status_code == 422
        bad_sum = dict(REPROJECT_WEIGHTS, responsiveness=0.55)  # sums to 1.10
        resp = pipeline.client.post(
            "/v1/cvi/reproject", json={"weights": bad_sum}
        )
        assert resp.status_code == 422
