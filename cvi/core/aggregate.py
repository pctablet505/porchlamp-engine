"""CVI v2 aggregation: pillar renormalisation, coverage, ceilings, composite.

Stages 3, 5 and 6 of D-ORDER-1 (02 section 1): the evidence gate, pillar
aggregation and the composite. ``aggregate_pillars`` is D-MISS-2: pillar
score renormalised over measured parameters, pillar coverage as the
measured share of the pillar's within-pillar weight. ``composite_coverage``
and ``coverage_status`` are D-MISS-3: the composite coverage ``C`` and the
rated/ceiling outcome it drives (unrated below 0.60, delegated to
:func:`cvi.core.gates.evidence_sufficiency` -- D-EVID-1's threshold has one
definition, in that module, R-2). ``composite_score`` is D-AGG-2: the
balanced composite, renormalised over measured pillars, rounded once at the
end (04 section 8). ``tier_from_score`` and ``apply_ceiling`` are D-MAT-2:
absolute cutoffs capped by the coverage ceiling.

The 0.80/0.95 ceiling bands, the 90/80/70/55/40 tier cutoffs and their
ordering were relocated to ``cvi.core.constants_v2`` by packet W2-8 (round 1
for the ceiling bands, round 2 for :data:`TIER_CUTOFFS`/:data:`TIER_ORDER`
per the round-1 review's Blocker 1); re-bound here under the original names.
``TIER_FLOOR`` stays defined in this module: it is a bare label with no
accompanying threshold (the round-1 review's own census: "correctly
excluded"), unlike ``TIER_CUTOFFS``'s (cutoff, label) pairs.
"""
from __future__ import annotations

from typing import Mapping

from cvi.core import constants_v2
from cvi.core.gates import evidence_sufficiency
from cvi.core.models import ParamScore, PillarScore
from cvi.core.params import PARAMS

#: D-MISS-3 ceiling bands, above the D-EVID-1 rated threshold (0.60, owned by
#: cvi.core.gates.EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE). Relocated to
#: ``constants_v2`` by packet W2-8 (R-2).
CEILING_A_MIN_COVERAGE: float = constants_v2.CEILING_A_MIN_COVERAGE
NO_CEILING_MIN_COVERAGE: float = constants_v2.NO_CEILING_MIN_COVERAGE

#: D-MISS-3 ceiling labels (bare letters: the design text writes "ceiling B" /
#: "ceiling A", never "Tier B"). ``None`` means no cap (coverage >= 0.95).
CEILING_B: str = "B"
CEILING_A: str = "A"

#: D-MAT-2 tier cutoffs (kept, O-5), worst to best; below the last cutoff is
#: ``TIER_FLOOR``. A score exactly at a cutoff belongs to the higher tier
#: (``>=``), matching the v1 convention these cutoffs are kept from.
#: Relocated to ``constants_v2`` by packet W2-8 round 2 (R-2); re-bound here
#: under the original name.
TIER_CUTOFFS: tuple[tuple[float, str], ...] = constants_v2.TIER_CUTOFFS
TIER_FLOOR: str = "D"

#: Every tier and ceiling label, worst to best, for ``apply_ceiling``'s
#: ``min(tier_by_score, ceiling)`` (D-MAT-2). Relocated to ``constants_v2``
#: by packet W2-8 round 2 (R-2).
TIER_ORDER: tuple[str, ...] = constants_v2.TIER_ORDER


def _pillar_problems(params: Mapping[str, ParamScore]) -> list[str]:
    expected = set(constants_v2.PARAM_INPUT_IDS)
    missing = sorted(expected - set(params))
    extra = sorted(set(params) - expected)
    problems = [f"missing input id {key!r}" for key in missing]
    problems += [f"unexpected input id {key!r}" for key in extra]
    return problems


def aggregate_pillars(params: Mapping[str, ParamScore]) -> dict[str, PillarScore]:
    """D-MISS-2: renormalised pillar score and pillar coverage.

    ``params`` must carry exactly the 18 frozen input ids
    (``constants_v2.PARAM_INPUT_IDS``), each already a stage-2
    :class:`~cvi.core.models.ParamScore` (curve applied, ``measured`` set by
    the min-n rule). For each pillar: score is ``Sum(w_i * s_i) / Sum(w_i)``
    over measured parameters in the pillar; coverage is
    ``Sum(w_i measured) / Sum(w_i all)``. A pillar with no measured
    parameter has ``score=None`` and ``coverage=0.0``: it drops out of the
    composite under the same renormalisation (D-MISS-2). Raises
    ``ValueError`` if ``params`` does not carry exactly the 18 ids."""
    problems = _pillar_problems(params)
    if problems:
        raise ValueError("; ".join(problems))

    by_pillar: dict[str, list[tuple[float, ParamScore]]] = {pillar: [] for pillar in constants_v2.PILLAR_NAMES}
    for input_id, param_score in params.items():
        defn = PARAMS[input_id]
        by_pillar[defn.pillar].append((defn.weight, param_score))

    result: dict[str, PillarScore] = {}
    for pillar in constants_v2.PILLAR_NAMES:
        entries = by_pillar[pillar]
        total_weight = sum(weight for weight, _ in entries)
        measured_weight = sum(weight for weight, score in entries if score.measured)
        coverage = measured_weight / total_weight if total_weight > 0.0 else 0.0
        if measured_weight > 0.0:
            weighted_sum = sum(weight * score.score for weight, score in entries if score.measured)
            pillar_score = weighted_sum / measured_weight
        else:
            pillar_score = None
        result[pillar] = PillarScore(score=pillar_score, coverage=coverage)
    return result


def composite_coverage(pillars: Mapping[str, PillarScore]) -> float:
    """D-MISS-3: composite coverage ``C = Sum_p(W_p * c_p)`` under the
    balanced pillar weights (D-AGG-1). ``pillars`` must carry all five
    frozen pillar names (``constants_v2.PILLAR_NAMES``); the balanced
    weights already sum to 1.0, so ``C`` lands directly in ``[0, 1]``."""
    return sum(constants_v2.PILLAR_WEIGHTS[pillar] * pillars[pillar].coverage for pillar in constants_v2.PILLAR_NAMES)


def composite_score_raw(pillars: Mapping[str, PillarScore]) -> float | None:
    """D-AGG-2 composite before the single final rounding.

    ``Sum(W_p * pillar_p) / Sum(W_p)`` over measured pillars (D-MISS-2
    renormalisation over the balanced weights); ``None`` if every pillar is
    unmeasured. 04 section 8: gate, ceiling and tier-cutoff comparisons use
    this unrounded value, never the rounded display value."""
    measured = [
        (constants_v2.PILLAR_WEIGHTS[pillar], pillars[pillar].score)
        for pillar in constants_v2.PILLAR_NAMES
        if pillars[pillar].score is not None
    ]
    total_weight = sum(weight for weight, _ in measured)
    if total_weight <= 0.0:
        return None
    return sum(weight * score for weight, score in measured) / total_weight


def composite_score(pillars: Mapping[str, PillarScore]) -> float | None:
    """D-AGG-2: the stored composite -- :func:`composite_score_raw` rounded
    once, at the end (04 section 8). ``None`` if every pillar is unmeasured."""
    raw = composite_score_raw(pillars)
    return None if raw is None else round(raw, 2)


def coverage_status(coverage: float) -> tuple[bool, str | None]:
    """D-EVID-1 + D-MISS-3 combined: ``(rated, ceiling)`` from the composite
    coverage ``C``.

    The rated/unrated boundary is delegated to
    :func:`cvi.core.gates.evidence_sufficiency` (single definition of the
    0.60 threshold, R-2), never recomputed here. ``ceiling`` is ``None``
    when unrated (no tier is assigned at all -- the caller must not consult
    it) or when ``C >= 0.95`` (no cap); otherwise ``"B"`` for
    ``0.60 <= C < 0.80`` or ``"A"`` for ``0.80 <= C < 0.95``."""
    rated = evidence_sufficiency(coverage).eligible
    if not rated:
        return False, None
    if coverage < CEILING_A_MIN_COVERAGE:
        return True, CEILING_B
    if coverage < NO_CEILING_MIN_COVERAGE:
        return True, CEILING_A
    return True, None


def tier_from_score(composite: float) -> str:
    """D-MAT-2: tier cutoffs 90/80/70/55/40 (kept, O-5) on ``composite``.

    ``composite`` must be the unrounded value (04 section 8); a score
    exactly at a cutoff belongs to the higher tier (``>=``)."""
    for cutoff, tier in TIER_CUTOFFS:
        if composite >= cutoff:
            return tier
    return TIER_FLOOR


def apply_ceiling(tier: str, ceiling: str | None) -> str:
    """D-MAT-2: ``tier = min(tier_by_score, ceiling)`` -- the worse of the
    two, per :data:`TIER_ORDER`. ``ceiling=None`` means no cap: ``tier`` is
    returned unchanged."""
    if ceiling is None:
        return tier
    return min(tier, ceiling, key=TIER_ORDER.index)
