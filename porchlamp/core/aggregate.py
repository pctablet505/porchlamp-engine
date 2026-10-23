"""Porchlamp v2 aggregation: pillar renormalisation, coverage, ceilings, composite.

Stages 3, 5 and 6 of D-ORDER-1 (02 section 1): the evidence gate, pillar
aggregation and the composite. ``aggregate_pillars`` is D-MISS-2: pillar
score renormalised over measured parameters, pillar coverage as the
measured share of the pillar's within-pillar weight. ``composite_coverage``
and ``coverage_status`` are D-MISS-3: the composite coverage ``C`` and the
rated/ceiling outcome it drives (unrated below 0.60 or without a measured
responsiveness pillar, RFC-0024, delegated to
:func:`porchlamp.core.gates.evidence_sufficiency` -- D-EVID-1 has one
definition, in that module). ``composite_score`` is D-AGG-2: the
balanced composite, renormalised over measured pillars, rounded once at the
end (04 section 8). ``tier_from_score`` and ``apply_ceiling`` are D-MAT-2:
absolute cutoffs capped by the coverage ceiling.

``composite_score_raw``/``composite_coverage`` take an optional ``weights``
keyword (default the frozen balanced weights, ``constants.PILLAR_WEIGHTS``),
validated by :func:`validate_pillar_weights`; that generalisation is what
lets ``porchlamp.core.persona.persona_composite`` and ``porchlamp.audit.sensitivity``
call these two functions directly with a persona/perturbed weight vector
instead of each keeping its own copy of the same reduction.

The 0.80/0.90 ceiling bands, the 90/80/70/55/40 tier cutoffs and their
ordering live in ``porchlamp.core.constants`` and are re-bound here under their
original names. ``TIER_FLOOR`` is defined in this module: it is a bare
label with no accompanying threshold, unlike ``TIER_CUTOFFS``'s
(cutoff, label) pairs.
"""
from __future__ import annotations

from typing import Mapping

from porchlamp.core import constants
from porchlamp.core.gates import evidence_sufficiency
from porchlamp.core.models import ParamScore, PillarScore
from porchlamp.core.params import PARAMS

#: D-MISS-3 ceiling bands, above the D-EVID-1 rated threshold (0.60, owned by
#: porchlamp.core.gates.EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE), re-bound from
#: ``porchlamp.core.constants``.
CEILING_A_MIN_COVERAGE: float = constants.CEILING_A_MIN_COVERAGE
NO_CEILING_MIN_COVERAGE: float = constants.NO_CEILING_MIN_COVERAGE

#: D-MISS-3 ceiling labels (bare letters: the design text writes "ceiling B" /
#: "ceiling A", never "Tier B"). ``None`` means no cap (coverage >= 0.90).
CEILING_B: str = "B"
CEILING_A: str = "A"

#: D-MAT-2 tier cutoffs (kept, O-5), worst to best; below the last cutoff is
#: ``TIER_FLOOR``. A score exactly at a cutoff belongs to the higher tier
#: (``>=``), matching the v1 convention these cutoffs are kept from.
#: Re-bound from ``porchlamp.core.constants`` under the original name.
TIER_CUTOFFS: tuple[tuple[float, str], ...] = constants.TIER_CUTOFFS
TIER_FLOOR: str = "D"

#: Every tier and ceiling label, worst to best, for ``apply_ceiling``'s
#: ``min(tier_by_score, ceiling)`` (D-MAT-2).
TIER_ORDER: tuple[str, ...] = constants.TIER_ORDER

#: Decimal places every weighted-mean RATIO in this module is rounded to
#: before it is returned (and therefore before any sharp ``>=`` threshold
#: compares it).
#:
#: The weights in ``porchlamp.core.constants`` are decimal literals that IEEE 754
#: binary doubles cannot represent exactly, so a sum of them carries a
#: ``~1e-16`` ULP error while the ``weight * score`` products above it stay
#: exact for integer scores: ``(0.35 * 60 + 0.25 * 60) / (0.35 + 0.25)``
#: evaluates to ``59.99999999999999``, not ``60.0``, and
#: ``0.25*0.6 + 0.25*0.6 + 0.15*1 + 0.15*1`` to ``0.59999999999999998``, not
#: ``0.60``. Every consumer of these ratios compares them against a sharp
#: cutoff -- the D-EVID-1 ``0.60`` coverage floor, the D-MISS-3 ``0.80`` /
#: ``0.90`` ceiling bands, the D-MAT-2 tier cutoffs, the D-PERS-2 persona
#: minimums -- so that ULP is the difference between tier ``S+`` and ``S``,
#: or between rated and ``unrated: insufficient_evidence``, for a repo whose
#: every measured input landed exactly on the cutoff.
#:
#: 12 is chosen to sit far below the ``1e-16`` noise floor (so the exact
#: rational value wins) and far above the precision any threshold or stored
#: value in this codebase distinguishes (composites are stored at 2 decimals,
#: coverages at 4): rounding here can only remove representation error, never
#: change a value the design text could name.
RATIO_DECIMALS: int = 12


def _pillar_problems(params: Mapping[str, ParamScore]) -> list[str]:
    expected = set(constants.PARAM_INPUT_IDS)
    missing = sorted(expected - set(params))
    extra = sorted(set(params) - expected)
    problems = [f"missing input id {key!r}" for key in missing]
    problems += [f"unexpected input id {key!r}" for key in extra]
    return problems


def _is_not_applicable(score: ParamScore) -> bool:
    """D-MISS-5: an ``Unmeasured(not_applicable)`` stage-2 score. The engine
    (``porchlamp.core.engine``) is the only place ``not_applicable`` may be
    assigned from (the closed rule list guard); this module trusts the
    reason it is handed, one source per fact."""
    return not score.measured and score.reason == "not_applicable"


def aggregate_pillars(params: Mapping[str, ParamScore]) -> dict[str, PillarScore]:
    """D-MISS-2/D-MISS-5: renormalised pillar score and pillar coverage.

    ``params`` must carry exactly the 18 frozen input ids
    (``constants.PARAM_INPUT_IDS``), each already a stage-2
    :class:`~porchlamp.core.models.ParamScore` (curve applied, ``measured`` set by
    the min-n rule). For each pillar: ``Unmeasured(not_applicable)``
    parameters (D-MISS-5) are excluded from BOTH sides of the coverage ratio
    first -- score is ``Sum(w_i * s_i) / Sum(w_i)`` over measured parameters
    among the applicable ones; coverage is ``Sum(w_i measured) / Sum(w_i
    applicable)``. A pillar with no measured parameter has ``score=None``
    (drops out of the composite under the same renormalisation, D-MISS-2,
    regardless of why); a pillar whose parameters are ALL not_applicable is
    additionally ``applicable=False`` (D-MISS-5: itself undefined for this
    repo, not merely 0% covered) and :func:`composite_coverage` excludes it
    from its own denominator, not just from the composite. Raises
    ``ValueError`` if ``params`` does not carry exactly the 18 ids."""
    problems = _pillar_problems(params)
    if problems:
        raise ValueError("; ".join(problems))

    by_pillar: dict[str, list[tuple[float, ParamScore]]] = {pillar: [] for pillar in constants.PILLAR_NAMES}
    for input_id, param_score in params.items():
        defn = PARAMS[input_id]
        by_pillar[defn.pillar].append((defn.weight, param_score))

    result: dict[str, PillarScore] = {}
    for pillar in constants.PILLAR_NAMES:
        entries = by_pillar[pillar]
        applicable_entries = [(weight, score) for weight, score in entries if not _is_not_applicable(score)]
        total_weight = sum(weight for weight, _ in applicable_entries)
        measured_weight = sum(weight for weight, score in applicable_entries if score.measured)
        coverage = round(measured_weight / total_weight, RATIO_DECIMALS) if total_weight > 0.0 else 0.0
        if measured_weight > 0.0:
            weighted_sum = sum(weight * score.score for weight, score in applicable_entries if score.measured)
            pillar_score = round(weighted_sum / measured_weight, RATIO_DECIMALS)
        else:
            pillar_score = None
        result[pillar] = PillarScore(score=pillar_score, coverage=coverage, applicable=total_weight > 0.0)
    return result


def validate_pillar_weights(weights: Mapping[str, float]) -> None:
    """Raise ``ValueError`` unless ``weights`` covers exactly the 5 frozen
    pillar names (``constants.PILLAR_NAMES``), every weight is
    non-negative, and they sum to 1.0 within
    :data:`porchlamp.core.constants.WEIGHT_SUM_TOLERANCE`.

    D-AGG-1's weight-vector contract, generalised beyond the balanced
    preset so :func:`composite_score_raw`/:func:`composite_coverage` can
    validate an arbitrary (e.g. persona, or a caller-supplied
    reprojection) weight vector. ``porchlamp.core.persona`` validates each
    persona preset through this one definition too.

    Non-negativity: a vector with a negative weight can still sum to
    exactly 1.0 (a positive weight on one pillar offsetting a negative one
    elsewhere), which is not a weighted AVERAGE any more -- D-AGG-1/D-AGG-2
    describe pillars being combined, never one pillar's score being
    subtracted from another's. Left unchecked,
    ``porchlamp.store.queries.reproject`` (the one caller that accepts an
    arbitrary, non-preset vector -- U-11) could return a composite outside
    ``[0, 100]`` and a tier the cutoffs never intended: e.g.
    ``{responsiveness: -0.5, criticality: 1.5, rest: 0.0}`` sums to 1.0 and
    yields ``porchlamp=147.5, tier='S+'``."""
    missing = sorted(set(constants.PILLAR_NAMES) - set(weights))
    extra = sorted(set(weights) - set(constants.PILLAR_NAMES))
    if missing or extra:
        raise ValueError(
            f"pillar weights must cover exactly {constants.PILLAR_NAMES}; missing={missing}, extra={extra}"
        )
    negative = sorted(pillar for pillar, weight in weights.items() if weight < 0.0)
    if negative:
        raise ValueError(f"pillar weights must be non-negative; negative weight(s) for {negative}: {[weights[p] for p in negative]!r}")
    total = sum(weights.values())
    if abs(total - 1.0) > constants.WEIGHT_SUM_TOLERANCE:
        raise ValueError(
            f"pillar weights must sum to 1.0 within {constants.WEIGHT_SUM_TOLERANCE}; got {total!r}"
        )


def composite_coverage(
    pillars: Mapping[str, PillarScore], *, weights: Mapping[str, float] = constants.PILLAR_WEIGHTS
) -> float:
    """D-MISS-3/D-MISS-5: composite coverage ``C = Sum_p(W_p * c_p) /
    Sum_p(W_p)`` over APPLICABLE pillars, under ``weights`` (D-AGG-1;
    defaults to the frozen balanced weights). ``pillars`` must carry all
    five frozen pillar names (``constants.PILLAR_NAMES``); ``weights``
    must too, and must sum to 1.0 within tolerance
    (:func:`validate_pillar_weights`, raised from here).

    D-MISS-5: a pillar whose parameters are all ``not_applicable``
    (``PillarScore.applicable is False``, set by :func:`aggregate_pillars`)
    is itself not applicable and leaves this sum's denominator entirely,
    the same renormalisation D-MISS-2 applies to a measured/unmeasured
    split -- so a repo with every other pillar fully measured reaches
    ``C = 1.00`` instead of being capped below the ceiling for a fact about
    the world (02 section 5). When every pillar is applicable the
    denominator is 1.0 and this reduces to ``Sum_p(W_p * c_p)``.

    ``weights`` generalises this beyond the balanced composite so a persona
    projection (``porchlamp.core.persona.persona_composite``) can call it with a
    preset vector instead of reimplementing the same reduction."""
    validate_pillar_weights(weights)
    applicable = [pillar for pillar in constants.PILLAR_NAMES if pillars[pillar].applicable]
    denominator = sum(weights[pillar] for pillar in applicable)
    if denominator <= 0.0:
        return 0.0
    return round(
        sum(weights[pillar] * pillars[pillar].coverage for pillar in applicable) / denominator,
        RATIO_DECIMALS,
    )


def composite_score_raw(
    pillars: Mapping[str, PillarScore], *, weights: Mapping[str, float] = constants.PILLAR_WEIGHTS
) -> float | None:
    """D-AGG-2 composite before the single final rounding.

    ``Sum(W_p * pillar_p) / Sum(W_p)`` over measured pillars (D-MISS-2
    renormalisation over ``weights``, defaulting to the frozen balanced
    weights); ``None`` if every pillar is unmeasured. 04 section 8: gate,
    ceiling and tier-cutoff comparisons use this unrounded value, never the
    rounded display value. ``weights`` must sum to 1.0 within tolerance
    (:func:`validate_pillar_weights`, raised from here); generalised beyond
    the balanced composite so a persona projection
    (``porchlamp.core.persona.persona_composite``) can call it with a preset
    vector instead of reimplementing the same reduction."""
    validate_pillar_weights(weights)
    measured = [
        (weights[pillar], pillars[pillar].score)
        for pillar in constants.PILLAR_NAMES
        if pillars[pillar].score is not None
    ]
    total_weight = sum(weight for weight, _ in measured)
    if total_weight <= 0.0:
        return None
    return round(sum(weight * score for weight, score in measured) / total_weight, RATIO_DECIMALS)


def composite_score(pillars: Mapping[str, PillarScore]) -> float | None:
    """D-AGG-2: the stored composite -- :func:`composite_score_raw` rounded
    once, at the end (04 section 8). ``None`` if every pillar is unmeasured."""
    raw = composite_score_raw(pillars)
    return None if raw is None else round(raw, 2)


def coverage_status(coverage: float, pillars: Mapping[str, PillarScore]) -> tuple[bool, str | None]:
    """D-EVID-1 + D-MISS-3 combined: ``(rated, ceiling)`` from the composite
    coverage ``C`` and the repo's pillar scores.

    The rated/unrated decision is delegated to
    :func:`porchlamp.core.gates.evidence_sufficiency` (single definition of the
    0.60 threshold and of RFC-0024's required responsiveness pillar), never
    recomputed here. ``ceiling`` is ``None``
    when unrated (no tier is assigned at all -- the caller must not consult
    it) or when ``C >= 0.90`` (no cap); otherwise ``"B"`` for
    ``0.60 <= C < 0.80`` or ``"A"`` for ``0.80 <= C < 0.90``."""
    rated = evidence_sufficiency(coverage, pillars).eligible
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
