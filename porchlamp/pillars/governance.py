"""Pillar 3 — Governance, Safety & Path-to-Power (weight 20%).

Three parameter scorers, each calibrated to [0, 100], combined with the
within-pillar weights from ``porchlamp.constants.PARAM_WEIGHTS["governance"]``:

3.1 Legal IP Regime & Relicensing Safety (weight 0.35)
    Exact lookup into ``porchlamp.constants.LEGAL_IP_SCORES`` keyed by the
    ``LegalIPModel`` enum value:

        DCO                   -> 100.0
        FOUNDATION_CLA        -> 75.0
        CORP_CLA_RECIPROCAL   -> 40.0
        CORP_CLA_UNILATERAL   -> 10.0

3.2 CHAOSS Elephant Factor / Decentralization (weight 0.35)
    Minimum number of distinct organizations whose affiliated contributors
    author >= 50% of total commits:

        score = min(100.0, elephant_factor * ELEPHANT_MULTIPLIER)

    EF = 1 -> 25, EF = 2 -> 50, EF = 3 -> 75, EF >= 4 -> 100 (capped).

3.3 Contributor Promotion Ladder (weight 0.30)
    The ``PromotionLadder`` enum values ARE the calibrated scores
    (VERIFIED_PROMOTION=100, DOCUMENTED_PATHWAY=75, AD_HOC=40,
    CLOSED_SINGLE_FIRM=10); the scorer is a pass-through of
    ``ladder.score``.

Edge cases (documented decisions):
- Elephant factor: the spec defines EF >= 1 (a single firm dominating
  > 50% of commits is EF = 1). Out-of-domain values (EF <= 0, e.g. a
  missing or corrupt telemetry value) are guarded to 0 and score 0.0
  rather than a negative score. The cap is a hard boundary: EF = 4
  scores exactly 100.0 and EF > 4 stays at 100.0, never above.
- Legal IP: all four ``LegalIPModel`` members are present in
  ``LEGAL_IP_SCORES``; the lookup is by ``model.value`` (the str-enum
  value), so a new enum member without a constant entry raises
  ``KeyError`` at scoring time instead of silently scoring 0.
- Promotion ladder: ``PromotionLadder.score`` returns ``float(value)``,
  already in [0, 100]; no clamping needed.
- The weighted sum is clamped to [0, 100] as a final guard; with the
  current constants and enum domains the sum is already in range.
"""

from __future__ import annotations

from ..constants import ELEPHANT_MULTIPLIER, LEGAL_IP_SCORES, PARAM_WEIGHTS
from ..models import LegalIPModel, PromotionLadder, RepoMeta

_PILLAR = "governance"


def _clamp(value: float) -> float:
    """Clamp a score into the calibrated [0, 100] range."""
    return max(0.0, min(100.0, value))


def score_legal_ip(model: LegalIPModel) -> float:
    """3.1 — Legal IP regime & relicensing safety.

    Exact lookup into ``LEGAL_IP_SCORES`` by the enum's string value.
    """
    return LEGAL_IP_SCORES[model.value]


def score_decentralization(elephant_factor: int) -> float:
    """3.2 — CHAOSS Elephant Factor / decentralization.

    Linear at ``ELEPHANT_MULTIPLIER`` (25) points per organization,
    capped at 100.0 (EF >= 4). Out-of-domain factors (<= 0) are
    guarded to 0.0.
    """
    factor = max(0, elephant_factor)
    return _clamp(factor * ELEPHANT_MULTIPLIER)


def score_promotion_ladder(ladder: PromotionLadder) -> float:
    """3.3 — Contributor promotion ladder.

    Pass-through of the enum's calibrated score (enum value as float).
    """
    return ladder.score


def score_governance(meta: RepoMeta) -> float:
    """Calibrated Pillar 3 score in [0, 100].

    Weighted combination of the three parameter scorers using
    ``PARAM_WEIGHTS["governance"]`` (legal_ip 0.35, decentralization
    0.35, promotion_ladder 0.30).
    """
    weights = PARAM_WEIGHTS[_PILLAR]
    score = (
        weights["legal_ip"] * score_legal_ip(meta.legal_ip_model)
        + weights["decentralization"] * score_decentralization(meta.elephant_factor)
        + weights["promotion_ladder"] * score_promotion_ladder(meta.promotion_ladder)
    )
    return _clamp(score)
