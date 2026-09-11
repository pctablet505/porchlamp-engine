"""Pillar 4 — Engineering Rigor & AI Alignment (weight 15%).

Three parameter scorers, each calibrated to [0, 100], combined with the
within-pillar weights from ``cvi.constants.PARAM_WEIGHTS["rigor"]``:

4.1 Automated Pipeline Defenses (weight 0.40)
    OpenSSF Scorecard composite (branch protection, mandatory peer review,
    automated testing, SAST). The composite is a 0..10 scale, so:

        score = (scorecard_score / SCORECARD_MAX) * 100.0

    clamped to [0, 100].

4.2 Calibrated AI Policy Posture (weight 0.35)
    Persona-conditional lookup into ``AI_POSTURE_SCORES``:

        score = AI_POSTURE_SCORES[meta.ai_posture.value]["ai" if persona_is_ai
                                                          else "human"]

    REGULATED: 95 for both personas; PROSCRIBED: 25 (AI) / 85 (human);
    AD_HOC: 40 for both personas.

4.3 Review Depth & Substantive Guidance (weight 0.25)
    Gaussian around the optimal mean inline-comment count, penalizing both
    dismissive silence and excessive nitpicking:

        score = 100.0 * exp(-0.5 * ((mean_inline_comments - OPTIMAL) / SIGMA) ** 2)

    with ``REVIEW_DEPTH_OPTIMAL`` = 3.5 and ``REVIEW_DEPTH_SIGMA`` = 1.5.

Edge cases (documented behaviour):
- ``scorecard_score`` above ``SCORECARD_MAX`` (e.g. 12) clamps to 100.0;
  negative values clamp to 0.0. The raw value is never rejected — the
  clamp is the guard.
- ``mean_inline_comments`` is used as-is: negative means (impossible in
  practice) fall on the far left tail of the Gaussian and score near 0.0;
  very large means fall on the far right tail and likewise score near 0.0.
  No clamping is applied to the mean itself.
- The Gaussian is strictly symmetric about the optimum: means at
  ``OPTIMAL - d`` and ``OPTIMAL + d`` always score identically.
- The spec's prose claim that the range [2.5, 4.5] "yields > 80 points"
  holds at the endpoints: the exact value at both 2.5 and 4.5 is
  100 * exp(-0.5 * (1/1.5)**2) ≈ 80.0737, which is (barely) above 80.
  The formula is the source of truth; tests assert the exact computed
  value, not the prose's "> 80" rounding.
- All parameter scores and the combined pillar score are clamped to
  [0, 100].
"""

from __future__ import annotations

import math

from ..constants import (
    AI_POSTURE_SCORES,
    PARAM_WEIGHTS,
    REVIEW_DEPTH_OPTIMAL,
    REVIEW_DEPTH_SIGMA,
    SCORECARD_MAX,
)
from ..models import RepoMeta

_PILLAR = "rigor"


def _clamp(value: float) -> float:
    """Clamp a score into the calibrated [0, 100] range."""
    return max(0.0, min(100.0, value))


def score_pipeline(scorecard_score: float) -> float:
    """4.1 — OpenSSF Scorecard pipeline defenses.

    Linear in the 0..10 composite, clamped to [0, 100]: a scorecard above
    ``SCORECARD_MAX`` scores 100.0, a negative scorecard scores 0.0.
    """
    return _clamp(scorecard_score / SCORECARD_MAX * 100.0)


def score_ai_posture(meta: RepoMeta, persona_is_ai: bool = False) -> float:
    """4.2 — Calibrated AI policy posture.

    Persona-conditional lookup: the AI persona is penalized by strict
    proscription (25) while the human persona is rewarded for it (85);
    regulated and ad-hoc postures score identically for both personas.
    """
    return AI_POSTURE_SCORES[meta.ai_posture.value]["ai" if persona_is_ai else "human"]


def score_review_depth(mean_inline_comments: float) -> float:
    """4.3 — Review depth & substantive guidance.

    Gaussian centered at ``REVIEW_DEPTH_OPTIMAL`` (3.5) with
    ``REVIEW_DEPTH_SIGMA`` (1.5): 100.0 at the optimum, decaying
    symmetrically on both sides.
    """
    z = (mean_inline_comments - REVIEW_DEPTH_OPTIMAL) / REVIEW_DEPTH_SIGMA
    return _clamp(100.0 * math.exp(-0.5 * z * z))


def score_rigor(meta: RepoMeta, persona_is_ai: bool = False) -> float:
    """Calibrated Pillar 4 score in [0, 100].

    Weighted combination of the three parameter scorers using
    ``PARAM_WEIGHTS["rigor"]`` (pipeline 0.40, ai_policy 0.35,
    review_depth 0.25).
    """
    weights = PARAM_WEIGHTS[_PILLAR]
    score = (
        weights["pipeline"] * score_pipeline(meta.scorecard_score)
        + weights["ai_policy"] * score_ai_posture(meta, persona_is_ai)
        + weights["review_depth"] * score_review_depth(meta.mean_inline_comments)
    )
    return _clamp(score)
