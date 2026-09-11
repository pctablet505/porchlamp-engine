"""CVI data contracts.

Pure dataclasses + enums. No I/O, no external dependencies. Every module in
the package imports from here, so treat this file as a stable contract:
change signatures only with care.

Field semantics follow the formal data contracts in
``docs/ARCHITECTURE.md`` sections 9.1-9.3 and the ``repositories`` table in
section 11.2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class Ecosystem(str, Enum):
    """Primary package-registry ecosystem (spec 9.3)."""

    NPM = "npm"
    PYPI = "pypi"
    CRATES = "crates"
    GO = "go"
    MAVEN = "maven"
    SYSTEMS = "systems"


class Quadrant(str, Enum):
    """2D Discovery Matrix classification (spec 2, Phase 4)."""

    SWEET_SPOT = "Sweet Spot"
    CRUCIBLE = "Crucible"
    NURSERY = "Nursery"
    GRAVEYARD = "Graveyard"


class Tier(str, Enum):
    """Cardinal prestige / health tier (spec 9.3)."""

    S_PLUS = "Tier S+"
    S = "Tier S"
    A = "Tier A"
    B = "Tier B"
    C = "Tier C"
    D = "Tier D"


class Persona(str, Enum):
    """Adaptive leaderboard projection persona (spec 4, Phase 5)."""

    BALANCED = "balanced"
    FIRST_TIMER = "first_timer"
    CAREER_CAPITAL = "career_capital"
    SENIOR_SYSTEMS = "senior_systems"
    AI_ASSISTED = "ai_assisted"


class LegalIPModel(str, Enum):
    """Legal IP regime & relicensing safety (spec 3.1)."""

    DCO = "DCO"
    FOUNDATION_CLA = "FOUNDATION_CLA"
    CORP_CLA_RECIPROCAL = "CORP_CLA_RECIPROCAL"
    CORP_CLA_UNILATERAL = "CORP_CLA_UNILATERAL"


class AIPosture(str, Enum):
    """Calibrated AI policy posture (spec 4.2)."""

    REGULATED = "REGULATED"
    PROSCRIBED = "PROSCRIBED"
    AD_HOC = "AD_HOC"


class AuthorAssociation(str, Enum):
    """Contributor affiliation level (spec 9.1)."""

    NONE = "NONE"
    FIRST_TIME = "FIRST_TIME"
    MEMBER = "MEMBER"
    OWNER = "OWNER"


class PromotionLadder(Enum):
    """Contributor promotion pathway (spec 3.3)."""

    VERIFIED_PROMOTION = 100
    DOCUMENTED_PATHWAY = 75
    AD_HOC = 40
    CLOSED_SINGLE_FIRM = 10

    @property
    def score(self) -> float:
        return float(self.value)


# --------------------------------------------------------------------------- #
# Ingestion / telemetry contracts
# --------------------------------------------------------------------------- #
@dataclass
class RawPREvent:
    """Event ingestion contract (spec 9.1).

    One row per pull request. Pillar 1 (Responsiveness) metrics are derived
    from a population of these events.
    """

    repo_owner: str
    repo_name: str
    pr_number: int
    created_at: datetime
    author_login: str
    author_association: AuthorAssociation
    author_prior_merged_commits: int = 0
    closed_at: Optional[datetime] = None
    merged_at: Optional[datetime] = None
    is_external: bool = False
    ci_status_green: bool = True
    first_human_review_at: Optional[datetime] = None
    response_latency_hours: Optional[float] = None
    maintainer_review_comments: int = 0

    @property
    def is_merged(self) -> bool:
        return self.merged_at is not None

    @property
    def is_closed_unmerged(self) -> bool:
        return self.closed_at is not None and self.merged_at is None

    @property
    def is_first_timer(self) -> bool:
        return self.author_prior_merged_commits == 0


@dataclass
class OpenSSFSignals:
    """Repository telemetry & criticality contract (spec 9.2).

    Raw measured values fed to the Rob Pike criticality algorithm.
    """

    created_since_months: float
    updated_since_months: float
    contributor_count: int
    org_count: int
    commit_frequency_weekly: float
    recent_releases_count: int
    closed_issues_count_90d: int
    updated_issues_count_90d: int
    comment_frequency: float
    dependents_count: int


@dataclass
class StargazerEvent:
    """A single stargazer observation for StarScout fraud filtering (spec 10.3)."""

    user_login: str
    starred_at: datetime
    user_total_platform_events: int


# --------------------------------------------------------------------------- #
# Repo-level inputs
# --------------------------------------------------------------------------- #
@dataclass
class RepoMeta:
    """Repo-level raw inputs consumed by the scoring pipeline.

    Holds the *raw* signals; derived pillar scores are computed downstream.
    PR-derived Pillar-1 metrics are computed from a list of :class:`RawPREvent`
    by the responsiveness pillar, so they are not stored here.
    """

    owner: str
    name: str
    ecosystem: Ecosystem
    stars: int = 0
    forks: int = 0
    monthly_downloads: int = 0
    dependents_count: int = 0
    elephant_factor: int = 1
    legal_ip_model: LegalIPModel = LegalIPModel.DCO
    ai_posture: AIPosture = AIPosture.AD_HOC
    scorecard_score: float = 0.0  # OpenSSF Scorecard composite, 0..10
    active_nonbot_commits_180d: int = 0
    off_platform_reviews: bool = False  # e.g. LKML mailing-list workflow
    hard_ai_ban: bool = False  # e.g. gentoo, qemu
    promotion_ladder: PromotionLadder = PromotionLadder.AD_HOC
    mean_inline_comments: float = 0.0  # mean inline review comments / external PR
    active_unclaimed_gfis: int = 0  # unassigned GFI/help-wanted updated < 30d
    rr_90: float = 0.0  # 90-day newcomer return rate, 0..1
    dx_ci_minutes: float = 0.0  # reproducible CI runtime in minutes (0 = unknown)
    openssf_signals: Optional[OpenSSFSignals] = None


# --------------------------------------------------------------------------- #
# Intermediate / result contracts
# --------------------------------------------------------------------------- #
@dataclass
class Stage0Result:
    """Outcome of the Phase 0 Elimination-by-Aspects gate (spec 2, Phase 0)."""

    passed: bool
    reason: Optional[str] = None
    is_black_hole: bool = False
    is_off_platform: bool = False
    is_ai_ban_disqualified: bool = False


@dataclass
class PillarScores:
    """The five calibrated pillar scores, each in [0, 100]."""

    responsiveness: float
    criticality: float
    governance: float
    rigor: float
    accessibility: float

    def as_dict(self) -> dict[str, float]:
        return {
            "responsiveness": self.responsiveness,
            "criticality": self.criticality,
            "governance": self.governance,
            "rigor": self.rigor,
            "accessibility": self.accessibility,
        }


@dataclass
class CVICompositeResult:
    """Scored composite contract (spec 9.3)."""

    owner: str
    name: str
    ecosystem: Ecosystem
    overall_cvi: float
    rank_tier: Tier
    matrix_quadrant: Quadrant
    leverage_score: float
    friction_score: float
    is_stage0_disqualified: bool
    disqualification_reason: Optional[str] = None
    pillar_scores: Optional[PillarScores] = None
    stage0: Optional[Stage0Result] = None
