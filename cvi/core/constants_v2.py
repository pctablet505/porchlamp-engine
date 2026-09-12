"""Frozen CVI v2 constants (D-VER-2, R-2).

One definition per fact: every constant the v2 core contract names lives in
this module. ``CONSTANTS_HASH`` is ``sha256(canonical JSON of the full
constants dict)``; it is stamped into every stored ScoreRecord row and the
snapshot manifest so a reader can verify the constants a score was built
from. Changing any constant changes the hash (tests/core/test_constants_hash.py).

The canonical JSON below is the JSON-native subset of
``cvi.core.models.canonical_json`` (byte-identical on JSON-native values);
``cvi.core`` may not import ``cvi.collect`` (04 section 3 import rule), so the
expression is kept local here instead of shared across the boundary.
"""
from __future__ import annotations

import hashlib
import json

#: Benchmark version for the v2 line (D-VER-1: the dev series before release).
BENCHMARK_VERSION: str = "2.0.0-dev"

#: FeatureBundle document version (04 section 3: data/derived/<key>.json).
SCHEMA_VERSION: int = 2

#: Length of the PR window every PR-based parameter measures over (04 section 3).
PR_WINDOW_DAYS: int = 180

#: Unmeasured.reason vocabulary (D-MISS-1).
UNMEASURED_REASONS: tuple[str, ...] = (
    "third_party_gap",
    "collector_failure",
    "insufficient_n",
    "not_applicable",
)

#: Measured.method vocabulary: "primary" or "proxy:<name>" (04 section 3, D-MISS-4).
METHOD_PRIMARY: str = "primary"
PROXY_METHOD_PREFIX: str = "proxy:"

#: The 18 parameter input ids (02 section 4, one per parameter), in pillar order.
PARAM_INPUT_IDS: tuple[str, ...] = (
    "ttfhr_hours",            # 1.1 TTFHR
    "dr90_decision_rate",     # 1.2 DR_90
    "mr_first_rate",          # 1.3 MR_first
    "gr14_ghost_rate",        # 1.4 GR_14
    "ttm_p90_hours",          # 1.5 TTM_p90
    "dependents_deduped",     # 2.1 Dependents
    "openssf_criticality",    # 2.2 OpenSSF criticality
    "downloads_monthly",      # 2.3 Downloads
    "legal_ip",               # 3.1 Legal IP
    "elephant_factor",        # 3.2 Elephant factor
    "promotion_ladder",       # 3.3 Promotion ladder
    "pipeline_defenses",      # 4.1 Pipeline defenses
    "ai_posture",             # 4.2 AI policy posture
    "substantive_review_rate",  # 4.3 Substantive review rate
    "newcomer_issue_supply",  # 5.1 Newcomer-issue supply
    "newcomer_conversion",    # 5.2 Newcomer conversion
    "rr90_return_rate",       # 5.3 RR_90
    "dx",                     # 5.4 DX
)

#: RepoIdentity field order (04 section 3).
REPO_IDENTITY_FIELDS: tuple[str, ...] = (
    "owner",
    "name",
    "ecosystem",
    "archived",
    "mirror_url",
    "fork",
    "license_spdx",
    "default_branch",
)

#: Pillar names and frozen pillar weights (02 section 4; O-2: unchanged at 25/25/20/15/15).
PILLAR_NAMES: tuple[str, ...] = (
    "responsiveness",
    "criticality",
    "governance",
    "rigor",
    "accessibility",
)
PILLAR_WEIGHTS: dict[str, float] = {
    "responsiveness": 0.25,
    "criticality": 0.25,
    "governance": 0.20,
    "rigor": 0.15,
    "accessibility": 0.15,
}

#: Evidence-based legal IP regimes (02 section 4, parameter 3.1).
LEGAL_IP_VALUES: tuple[str, ...] = ("DCO", "FOUNDATION_CLA", "CORP_CLA", "NONE")

#: AI policy postures (02 section 4, parameter 4.2).
AI_POSTURE_VALUES: tuple[str, ...] = ("REGULATED", "PROSCRIBED", "AD_HOC")

#: Promotion ladder rungs (02 section 4, parameter 3.3).
PROMOTION_LADDER_VALUES: tuple[str, ...] = ("VERIFIED", "DOCUMENTED", "AD_HOC", "CLOSED")


def _canonical_json(value: object) -> str:
    """Canonical JSON (sorted keys, no insignificant whitespace), stdlib-only."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def constants_dict() -> dict[str, object]:
    """Every v2 constant in this module, keyed by name.

    The single source for the hash: a value is a constant if and only if it
    appears here. ``CONSTANTS_HASH`` itself is excluded (it is a hash of the
    dict, not a member of it).
    """
    return {
        "AI_POSTURE_VALUES": AI_POSTURE_VALUES,
        "BENCHMARK_VERSION": BENCHMARK_VERSION,
        "LEGAL_IP_VALUES": LEGAL_IP_VALUES,
        "METHOD_PRIMARY": METHOD_PRIMARY,
        "PARAM_INPUT_IDS": PARAM_INPUT_IDS,
        "PILLAR_NAMES": PILLAR_NAMES,
        "PILLAR_WEIGHTS": PILLAR_WEIGHTS,
        "PROXY_METHOD_PREFIX": PROXY_METHOD_PREFIX,
        "PR_WINDOW_DAYS": PR_WINDOW_DAYS,
        "PROMOTION_LADDER_VALUES": PROMOTION_LADDER_VALUES,
        "REPO_IDENTITY_FIELDS": REPO_IDENTITY_FIELDS,
        "SCHEMA_VERSION": SCHEMA_VERSION,
        "UNMEASURED_REASONS": UNMEASURED_REASONS,
    }


CONSTANTS_HASH: str = hashlib.sha256(
    _canonical_json(constants_dict()).encode("utf-8")
).hexdigest()
