"""Phase 5: Persona-Adaptive Leaderboard Projection (spec 2, Phase 5; spec 4).

Reprojects the WAM composite under a persona-specific weight preset, and
computes rank deltas between two leaderboard snapshots.
"""

from __future__ import annotations

from .constants import PERSONA_PRESETS
from .models import Persona, PillarScores

#: The five pillar keys every weight vector must cover, exactly.
PILLAR_KEYS: tuple[str, ...] = (
    "responsiveness",
    "criticality",
    "governance",
    "rigor",
    "accessibility",
)

#: Tolerance for the weight-sum check: ``abs(sum - 1.0) <= 1e-6``.
WEIGHT_SUM_TOLERANCE: float = 1e-6


def _validate_weights(weights: dict[str, float]) -> None:
    """Raise ``ValueError`` unless ``weights`` covers exactly the 5 pillar keys
    and sums to 1.0 within ``WEIGHT_SUM_TOLERANCE`` (1e-6)."""
    missing = [k for k in PILLAR_KEYS if k not in weights]
    extra = [k for k in weights if k not in PILLAR_KEYS]
    if missing or extra:
        raise ValueError(
            f"weights must contain exactly the 5 pillar keys {list(PILLAR_KEYS)}; "
            f"missing={missing}, extra={extra}"
        )
    total = sum(weights.values())
    if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise ValueError(f"weights must sum to 1.0 within 1e-6; got {total!r}")


def reproject(pillar_scores: PillarScores, weights: dict[str, float]) -> float:
    """WAM composite under custom weights: ``sum(w_j * pillar_j)``, 2 decimals.

    Raises ``ValueError`` if ``weights`` does not contain exactly the 5 pillar
    keys, or does not sum to 1.0 within 1e-6.
    """
    _validate_weights(weights)
    pillars = pillar_scores.as_dict()
    raw = sum(weights[k] * pillars[k] for k in PILLAR_KEYS)
    return round(raw, 2)


def persona_composite(pillar_scores: PillarScores, persona: Persona) -> float:
    """WAM composite under the persona's preset weights (spec 4 table)."""
    return reproject(pillar_scores, PERSONA_PRESETS[persona])


def rank_deltas(
    scores_before: list[tuple[str, float]], scores_after: dict[str, float]
) -> dict[str, int]:
    """Rank deltas between two leaderboard snapshots.

    Both rankings are 1-based and ordered by score **descending**. Ties are
    broken by position in ``scores_before``: the repo appearing earlier in
    ``scores_before`` gets the better (lower) rank. This tie-break is applied
    to *both* the old and the new ranking, so ``scores_before`` need not be
    pre-sorted — the old ranking is computed from its scores, not its list
    order (list order only breaks ties).

    Returns ``{repo_id: old_rank - new_rank}``; positive = moved up.

    Raises ``ValueError`` if the two inputs do not cover the same repo ids,
    or if ``scores_before`` contains duplicate repo ids.
    """
    before_ids = [repo for repo, _ in scores_before]
    if len(set(before_ids)) != len(before_ids):
        raise ValueError(f"duplicate repo ids in scores_before: {before_ids}")
    after_ids = set(scores_after)
    before_set = set(before_ids)
    if before_set != after_ids:
        raise ValueError(
            f"inputs must cover the same repo ids; "
            f"only_before={sorted(before_set - after_ids)}, "
            f"only_after={sorted(after_ids - before_set)}"
        )

    before_scores = dict(scores_before)
    position = {repo: i for i, repo in enumerate(before_ids)}
    # Stable tie-break for both rankings: sort by (-score, position in before).
    old_order = sorted(
        before_ids, key=lambda repo: (-before_scores[repo], position[repo])
    )
    old_rank = {repo: i + 1 for i, repo in enumerate(old_order)}
    new_order = sorted(
        before_ids, key=lambda repo: (-scores_after[repo], position[repo])
    )
    new_rank = {repo: i + 1 for i, repo in enumerate(new_order)}
    return {repo: old_rank[repo] - new_rank[repo] for repo in before_ids}


def validate_persona_preset(persona: Persona) -> None:
    """Self-check: the persona's preset must cover the 5 pillar keys and sum
    to 1.0 within 1e-6. Raises ``ValueError`` otherwise."""
    _validate_weights(PERSONA_PRESETS[persona])
