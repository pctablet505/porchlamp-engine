"""Porchlamp tuning constants: weights, thresholds, and persona presets.

Every magic number in the ranking pipeline lives here so it is auditable and
mutable in one place. Values transcribed from ``docs/ARCHITECTURE.md``.
"""

from __future__ import annotations

from .models import Persona

# --------------------------------------------------------------------------- #
# Phase 3: Multi-Pillar Weights (WAM) — Balanced Default (spec 2, Phase 3)
# --------------------------------------------------------------------------- #
PILLAR_WEIGHTS: dict[str, float] = {
    "responsiveness": 0.25,
    "criticality": 0.25,
    "governance": 0.20,
    "rigor": 0.15,
    "accessibility": 0.15,
}

# Within-pillar parameter weights (spec 3). Keys are stable parameter ids.
PARAM_WEIGHTS: dict[str, dict[str, float]] = {
    "responsiveness": {
        "ttfhr": 0.35,
        "mr_first": 0.25,
        "ghost_rate": 0.20,
        "ttm_p90": 0.20,
    },
    "criticality": {
        "dependents": 0.40,
        "openssf_criticality": 0.35,
        "downloads": 0.25,
    },
    "governance": {
        "legal_ip": 0.35,
        "decentralization": 0.35,
        "promotion_ladder": 0.30,
    },
    "rigor": {
        "pipeline": 0.40,
        "ai_policy": 0.35,
        "review_depth": 0.25,
    },
    "accessibility": {
        "gfi": 0.40,
        "return_rate": 0.35,
        "dx": 0.25,
    },
}

# --------------------------------------------------------------------------- #
# Phase 4: 2D Matrix coordinate decomposition (spec 2, Phase 4)
# --------------------------------------------------------------------------- #
LEVERAGE_WEIGHTS: dict[str, float] = {
    "criticality": 0.50,
    "rigor": 0.30,
    "governance": 0.20,
}
FRICTION_WEIGHTS: dict[str, float] = {
    "responsiveness": 0.50,
    "governance": 0.30,
    "accessibility": 0.20,
}
QUADRANT_THRESHOLD: float = 50.0  # median coordinate cutoff

# --------------------------------------------------------------------------- #
# Phase 0: EBA hard gates (spec 2, Phase 0)
# --------------------------------------------------------------------------- #
MAINTENANCE_WINDOW_DAYS: int = 180
GHOST_RATE_GATE: float = 0.50  # GR_14 ceiling; above => Black Hole

# --------------------------------------------------------------------------- #
# Pillar 1: Responsiveness (spec 1.1-1.4)
# --------------------------------------------------------------------------- #
TTFHR_BASE_HOURS: float = 12.0  # <= base => 100 pts
TTFHR_DECAY: float = 25.0  # pts lost per doubling above base
MR_FIRST_MULTIPLIER: float = 125.0  # 80% => 100 pts
GHOST_RATE_MULTIPLIER: float = 200.0  # 50% => 0 pts

# --------------------------------------------------------------------------- #
# Pillar 2: Criticality (spec 2.1-2.3)
# --------------------------------------------------------------------------- #
# (dependents / downloads use ECDF; OpenSSF criticality uses the engine below)

# --------------------------------------------------------------------------- #
# Pillar 3: Governance (spec 3.1-3.3)
# --------------------------------------------------------------------------- #
LEGAL_IP_SCORES: dict[str, float] = {
    "DCO": 100.0,
    "FOUNDATION_CLA": 75.0,
    "CORP_CLA_RECIPROCAL": 40.0,
    "CORP_CLA_UNILATERAL": 10.0,
}
ELEPHANT_MULTIPLIER: float = 25.0  # EF >= 4 => 100 pts

# --------------------------------------------------------------------------- #
# Pillar 4: Rigor & AI Alignment (spec 4.1-4.3)
# --------------------------------------------------------------------------- #
SCORECARD_MAX: float = 10.0
AI_POSTURE_SCORES: dict[str, dict[str, float]] = {
    # posture -> {persona_is_ai: score, persona_is_human: score}
    "REGULATED": {"ai": 95.0, "human": 95.0},
    "PROSCRIBED": {"ai": 25.0, "human": 85.0},
    "AD_HOC": {"ai": 40.0, "human": 40.0},
}
REVIEW_DEPTH_OPTIMAL: float = 3.5  # optimal mean inline comments
REVIEW_DEPTH_SIGMA: float = 1.5

# --------------------------------------------------------------------------- #
# Pillar 5: Accessibility & DX (spec 5.1-5.3)
# --------------------------------------------------------------------------- #
GFI_TARGET: int = 10  # 10 active unclaimed GFIs => 100 pts
RR90_MULTIPLIER: float = 200.0  # 50% return rate => 100 pts
DX_CI_MINUTES_TARGET: float = 20.0  # reproducible CI under 20 min => full marks

# Phase 1 StarScout anti-gaming thresholds (spec 10.3) live in
# porchlamp.core.constants (RFC-0002): porchlamp/starscout.py reads them from there, so
# they are covered by the v2 constants freeze (CONSTANTS_HASH).

# Bot / service-account markers stripped from review-latency metrics (spec 2, P1).
BOT_LOGIN_MARKERS: tuple[str, ...] = (
    "[bot]",
    "-actions",
    "actions",
    "google-cla",
    "claude",
    "stale",
    "codecov",
    "dependabot",
)

# --------------------------------------------------------------------------- #
# Tier cutoffs on the final Porchlamp composite (spec 9.3).
# --------------------------------------------------------------------------- #
TIER_CUTOFFS: tuple[tuple[float, str], ...] = (
    (90.0, "Tier S+"),
    (80.0, "Tier S"),
    (70.0, "Tier A"),
    (55.0, "Tier B"),
    (40.0, "Tier C"),
    (0.0, "Tier D"),
)

# --------------------------------------------------------------------------- #
# Phase 5: Persona-adaptive weight presets (spec 4).
# Each preset maps pillar -> weight and must sum to 1.0.
# --------------------------------------------------------------------------- #
PERSONA_PRESETS: dict[Persona, dict[str, float]] = {
    Persona.BALANCED: {
        "responsiveness": 0.25,
        "criticality": 0.25,
        "governance": 0.20,
        "rigor": 0.15,
        "accessibility": 0.15,
    },
    Persona.FIRST_TIMER: {
        "responsiveness": 0.35,
        "criticality": 0.10,
        "governance": 0.15,
        "rigor": 0.10,
        "accessibility": 0.30,
    },
    Persona.CAREER_CAPITAL: {
        "responsiveness": 0.20,
        "criticality": 0.35,
        "governance": 0.15,
        "rigor": 0.10,
        "accessibility": 0.20,
    },
    Persona.SENIOR_SYSTEMS: {
        "responsiveness": 0.15,
        "criticality": 0.35,
        "governance": 0.25,
        "rigor": 0.20,
        "accessibility": 0.05,
    },
    Persona.AI_ASSISTED: {
        "responsiveness": 0.20,
        "criticality": 0.20,
        "governance": 0.15,
        "rigor": 0.35,
        "accessibility": 0.10,
    },
}

# --------------------------------------------------------------------------- #
# OpenSSF Rob Pike criticality: signal weights (alpha_i) and thresholds (T_i)
# (spec 2.2, 10.1).
# --------------------------------------------------------------------------- #
OPENSSF_WEIGHTS: dict[str, float] = {
    "created_since": 1.0,
    "updated_since": -1.0,
    "contributor_count": 2.0,
    "org_count": 1.0,
    "commit_frequency": 1.0,
    "recent_releases_count": 0.5,
    "closed_issues_count": 0.5,
    "updated_issues_count": 0.5,
    "comment_frequency": 1.0,
    "dependents_count": 2.0,
}
OPENSSF_THRESHOLDS: dict[str, float] = {
    "created_since": 120.0,
    "updated_since": 120.0,
    "contributor_count": 5000.0,
    "org_count": 10.0,
    "commit_frequency": 1000.0,
    "recent_releases_count": 26.0,
    "closed_issues_count": 5000.0,
    "updated_issues_count": 5000.0,
    "comment_frequency": 15.0,
    "dependents_count": 500000.0,
}
