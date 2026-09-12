"""CVI v2 persona gates and persona composites (02 section 9: D-PERS-1/2).

D-PERS-1 (separation). The stage-0 :class:`~cvi.core.models.GateResult` is
persona-independent (``cvi.core.gates``); this module never touches it and
never changes the stored balanced score. Persona gates are pure filters:
:func:`persona_gates` returns ``{persona: reason}`` only for the personas a
repo is *hidden from* -- a persona absent from the dict means that persona's
view shows the repo with its one, unchanged balanced
``ScoreRecord.composite``/``tier``. :func:`persona_composite` is a *separate*
re-weighted projection (``ScoreRecord.persona_composites`` is where a caller
stores it); it is never written back onto ``composite``/``tier`` themselves.
Contrast with the v1 defect this replaces: ``cvi.lakehouse``'s persona
re-projection path zeroes the gated row in place (``cvi_val = 0.0``,
``tier_val = Tier.D.value``) and overwrites
``row["is_stage0_disqualified"] = 1`` -- a gated persona view there *edits
the record*, not just a view of it (``tests/core/test_persona_v2.py``
reproduces this live against ``cvi.lakehouse.OperationalStore.leaderboard``
and shows the v2 functions below leave the equivalent
:class:`~cvi.core.models.ScoreRecord` untouched).

D-PERS-2 (gate definitions). Each gate reads either the bundle's raw stage-2
inputs (not the composite -- gates work even for a gate-ineligible or
unrated record, since raw inputs exist before any pillar is aggregated) or
the record's already pillar-renormalised scores:

- ``ai_assisted`` -- hard AI ban: gated when the ``ai_posture`` input is
  ``Measured`` with value ``"PROSCRIBED"``. The FeatureBundle v2 contract
  (04 section 3) has no field named ``ai_policy_evidence`` distinct from the
  Measured/Unmeasured status of ``ai_posture`` itself; every ``Measured``
  value already carries non-empty envelope provenance
  (``cvi.core.models.measured_problems`` requires a non-empty ``source``),
  so a ``Measured("PROSCRIBED", ...)`` *is* the non-empty evidence the
  design text names, and ``Unmeasured`` (whatever the reason) is the
  "without evidence" case. Recorded as an ownership request below.
- ``senior_systems`` -- multi-vendor: gated when the ``elephant_factor``
  input is ``Measured`` and its raw value is below 3. An ``Unmeasured``
  elephant factor does not gate (D-MISS-1: no evidence is not evidence of
  the opposite; nobody is punished for a collector gap).
- ``first_timer`` -- low barrier: gated *unless* the decided first-timer
  cohort is large enough and welcoming (``mr_first_rate`` -- "merged/decided
  among external first-time authors", ``cvi/core/params/responsiveness.py``
  -- is ``Measured`` with ``n >= 5`` decided first-timer PRs and
  ``value >= 0.40``), or the newcomer-issue supply (``newcomer_issue_supply``)
  is ``Measured`` with ``value >= 3``. An ``Unmeasured`` input satisfies
  neither branch.
- ``career_capital`` -- high-tier brand: gated *unless* the record's
  criticality pillar is measured (``score is not None``) with
  ``score >= 60`` and ``coverage >= 0.80``.

Persona composites (D-AGG-4: "linear ... no Python full scan" -- this
function runs once per already-scored record, the shape a per-row SQL
expression takes). :func:`persona_composite` reprojects the record's
already pillar-renormalised scores (``ScoreRecord.pillars``, stage 5,
persona-invariant: a persona only changes the pillar-to-composite weights,
never the within-pillar renormalisation) under one of the five preset
weight vectors, then applies the *same* D-MISS-3/D-MAT-2 rules the balanced
composite uses by calling
``cvi.core.aggregate.coverage_status``/``tier_from_score``/``apply_ceiling``
directly -- never re-implemented. Only the weighted pillar-to-composite
reduction itself (``Sum(w_p * pillar_p) / Sum(w_p)`` and
``Sum(w_p * c_p)``) is necessarily written again here, one line each:
``cvi.core.aggregate.composite_score_raw``/``composite_coverage`` hard-code
the balanced weights (``constants_v2.PILLAR_WEIGHTS``) with no weight
parameter, so they cannot be called with a persona's weights.
``tests/core/test_persona_v2.py`` proves the two reductions agree with
``cvi.core.aggregate`` exactly when the persona is ``"balanced"`` (an
over-correction guard that the generalisation is not a second, drifting
definition of the same arithmetic). Ownership request: relocate the
persona-weighted reduction into ``cvi.core.aggregate`` (an optional
``weights`` parameter, defaulting to ``constants_v2.PILLAR_WEIGHTS``) so
this module can call it instead of duplicating it -- flagged for W2-8.

One-definition-per-constant (R-2). The ``"balanced"`` preset is not
duplicated: it is read directly off ``constants_v2.PILLAR_WEIGHTS`` (the one
frozen definition). The other four preset vectors have no v2 home yet:
``cvi.core``'s import-graph test
(``tests/core/test_import_graph.py::test_core_imports_nothing_outside_core_and_stdlib``)
forbids this module from importing ``cvi.constants.PERSONA_PRESETS`` (v1,
outside ``cvi.core``), and this packet does not own
``cvi/core/constants_v2.py`` to add them there (W2-7 owns only
``cvi/core/persona.py``, R-10). They are therefore transcribed once, below,
and pinned byte-equal to v1's values by
``tests/core/test_persona_v2.py::test_non_balanced_presets_match_v1`` -- an
over-correction guard standing in for the cross-package import the
architecture does not allow. Ownership request: W2-8 (constants freeze)
should relocate all five preset vectors into ``constants_v2.py`` and delete
both this module's copy and ``cvi/constants.py``'s, leaving one definition.

Gate thresholds (3, 5, 0.40, 3, 60, 0.80) and the gate reason strings have no
v2 home either (the same R-10 reasoning); following the precedent
``cvi.core.gates`` already sets for its own not-yet-relocated thresholds
(its module docstring: "The gate thresholds ... live in this module ...
until the constants freeze relocates them"), they are defined once, below,
and flagged for W2-8.
"""
from __future__ import annotations

from typing import Mapping

from cvi.core import aggregate, constants_v2
from cvi.core.models import Measured, ParamValue, ScoreRecord

# --------------------------------------------------------------------------- #
# Persona identifiers (D-PERS-2 table; also the ``ScoreRecord.persona_gates``/
# ``persona_composites`` dict keys). Match the v1 ``cvi.models.Persona``
# enum's string values so a future caller can round-trip through either.
# --------------------------------------------------------------------------- #
PERSONA_BALANCED: str = "balanced"
PERSONA_FIRST_TIMER: str = "first_timer"
PERSONA_CAREER_CAPITAL: str = "career_capital"
PERSONA_SENIOR_SYSTEMS: str = "senior_systems"
PERSONA_AI_ASSISTED: str = "ai_assisted"

#: Every persona this module knows how to gate. ``PERSONA_BALANCED`` is
#: deliberately excluded: the balanced view is the ungated default, never a
#: gate target (D-PERS-1).
GATED_PERSONAS: tuple[str, ...] = (
    PERSONA_AI_ASSISTED,
    PERSONA_SENIOR_SYSTEMS,
    PERSONA_FIRST_TIMER,
    PERSONA_CAREER_CAPITAL,
)

#: Tolerance for the preset weight-sum check (matches v1
#: ``cvi.persona.WEIGHT_SUM_TOLERANCE``).
WEIGHT_SUM_TOLERANCE: float = 1e-6

#: D-PERS-2 preset weight vectors, transcribed from ``cvi/constants.py``'s
#: ``PERSONA_PRESETS`` (spec section 4; see the module docstring's
#: one-definition-per-constant note for why this module cannot import that
#: dict directly). ``"balanced"`` is not transcribed -- it is
#: ``constants_v2.PILLAR_WEIGHTS`` itself, the single frozen definition.
PERSONA_PRESETS: dict[str, dict[str, float]] = {
    PERSONA_BALANCED: dict(constants_v2.PILLAR_WEIGHTS),
    PERSONA_FIRST_TIMER: {
        "responsiveness": 0.35,
        "criticality": 0.10,
        "governance": 0.15,
        "rigor": 0.10,
        "accessibility": 0.30,
    },
    PERSONA_CAREER_CAPITAL: {
        "responsiveness": 0.20,
        "criticality": 0.35,
        "governance": 0.15,
        "rigor": 0.10,
        "accessibility": 0.20,
    },
    PERSONA_SENIOR_SYSTEMS: {
        "responsiveness": 0.15,
        "criticality": 0.35,
        "governance": 0.25,
        "rigor": 0.20,
        "accessibility": 0.05,
    },
    PERSONA_AI_ASSISTED: {
        "responsiveness": 0.20,
        "criticality": 0.20,
        "governance": 0.15,
        "rigor": 0.35,
        "accessibility": 0.10,
    },
}


def _validate_preset(persona: str, weights: Mapping[str, float]) -> None:
    """Raise ``ValueError`` unless ``weights`` covers exactly the 5 pillar
    names and sums to 1.0 within :data:`WEIGHT_SUM_TOLERANCE`."""
    missing = sorted(set(constants_v2.PILLAR_NAMES) - set(weights))
    extra = sorted(set(weights) - set(constants_v2.PILLAR_NAMES))
    if missing or extra:
        raise ValueError(
            f"persona {persona!r} preset must cover exactly {constants_v2.PILLAR_NAMES}; "
            f"missing={missing}, extra={extra}"
        )
    total = sum(weights.values())
    if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
        raise ValueError(f"persona {persona!r} preset must sum to 1.0 within 1e-6; got {total!r}")


for _persona, _weights in PERSONA_PRESETS.items():
    _validate_preset(_persona, _weights)
del _persona, _weights


# --------------------------------------------------------------------------- #
# D-PERS-2 gate reasons (this module's own vocabulary -- a different
# D-ORDER-1 stage from ``cvi.core.gates``'s stage-0 reasons, so not shared).
# --------------------------------------------------------------------------- #
REASON_AI_HARD_BAN: str = "ai_hard_ban"
REASON_LOW_ELEPHANT_FACTOR: str = "elephant_factor_below_3"
REASON_HIGH_BARRIER_TO_ENTRY: str = "high_barrier_to_entry"
REASON_LOW_CRITICALITY: str = "criticality_below_threshold"

# --------------------------------------------------------------------------- #
# D-PERS-2 gate thresholds (no v2 home yet; see module docstring).
# --------------------------------------------------------------------------- #
SENIOR_SYSTEMS_MIN_ELEPHANT_FACTOR: float = 3.0
FIRST_TIMER_MIN_DECIDED_PRS: int = 5
FIRST_TIMER_MIN_MR_FIRST: float = 0.40
FIRST_TIMER_MIN_NEWCOMER_ISSUES: float = 3.0
CAREER_CAPITAL_MIN_CRITICALITY: float = 60.0
CAREER_CAPITAL_MIN_COVERAGE: float = 0.80

#: The bundle input ids this module reads directly (``FeatureBundle.inputs``
#: keys, ``constants_v2.PARAM_INPUT_IDS``).
INPUT_AI_POSTURE: str = "ai_posture"
INPUT_ELEPHANT_FACTOR: str = "elephant_factor"
INPUT_MR_FIRST_RATE: str = "mr_first_rate"
INPUT_NEWCOMER_ISSUE_SUPPLY: str = "newcomer_issue_supply"

#: The one ``ai_posture`` value this module gates on (``constants_v2.AI_POSTURE_VALUES``).
AI_POSTURE_PROSCRIBED: str = "PROSCRIBED"
assert AI_POSTURE_PROSCRIBED in constants_v2.AI_POSTURE_VALUES
for _input_id in (INPUT_AI_POSTURE, INPUT_ELEPHANT_FACTOR, INPUT_MR_FIRST_RATE, INPUT_NEWCOMER_ISSUE_SUPPLY):
    assert _input_id in constants_v2.PARAM_INPUT_IDS
del _input_id


def _ai_assisted_gated(bundle_inputs: Mapping[str, ParamValue]) -> bool:
    value = bundle_inputs[INPUT_AI_POSTURE]
    return isinstance(value, Measured) and value.value == AI_POSTURE_PROSCRIBED


def _senior_systems_gated(bundle_inputs: Mapping[str, ParamValue]) -> bool:
    value = bundle_inputs[INPUT_ELEPHANT_FACTOR]
    return isinstance(value, Measured) and value.value < SENIOR_SYSTEMS_MIN_ELEPHANT_FACTOR


def _first_timer_qualifies(bundle_inputs: Mapping[str, ParamValue]) -> bool:
    mr_first = bundle_inputs[INPUT_MR_FIRST_RATE]
    if (
        isinstance(mr_first, Measured)
        and mr_first.n >= FIRST_TIMER_MIN_DECIDED_PRS
        and mr_first.value >= FIRST_TIMER_MIN_MR_FIRST
    ):
        return True
    supply = bundle_inputs[INPUT_NEWCOMER_ISSUE_SUPPLY]
    return isinstance(supply, Measured) and supply.value >= FIRST_TIMER_MIN_NEWCOMER_ISSUES


def _career_capital_qualifies(record: ScoreRecord) -> bool:
    criticality = record.pillars.get("criticality")
    if criticality is None or criticality.score is None:
        return False
    return (
        criticality.score >= CAREER_CAPITAL_MIN_CRITICALITY
        and criticality.coverage >= CAREER_CAPITAL_MIN_COVERAGE
    )


def persona_gates(record: ScoreRecord, bundle_inputs: Mapping[str, ParamValue]) -> dict[str, str]:
    """D-PERS-2: which persona views hide this repo, and why.

    ``record`` supplies the already pillar-renormalised scores the
    ``career_capital`` gate reads (``record.pillars``); ``bundle_inputs`` is
    the ``FeatureBundle.inputs`` mapping the other three gates read
    directly, keyed by the 18 frozen input ids (``constants_v2.PARAM_INPUT_IDS``).
    Only the four input ids named in the module docstring are ever looked
    up, so this function works on a gate-ineligible or unrated record too
    (``record.pillars`` may then be ``{}``; ``_career_capital_qualifies``
    treats a missing/unmeasured criticality pillar as not qualifying, never
    as an error).

    Returns ``{persona: reason}`` for exactly the personas this repo is
    gated from; a persona absent from the dict is not gated (D-PERS-1: the
    balanced score is never touched, and neither is any other persona's
    view of it -- the caller shows the repo under that persona's
    :func:`persona_composite` projection unchanged).
    """
    gates: dict[str, str] = {}
    if _ai_assisted_gated(bundle_inputs):
        gates[PERSONA_AI_ASSISTED] = REASON_AI_HARD_BAN
    if _senior_systems_gated(bundle_inputs):
        gates[PERSONA_SENIOR_SYSTEMS] = REASON_LOW_ELEPHANT_FACTOR
    if not _first_timer_qualifies(bundle_inputs):
        gates[PERSONA_FIRST_TIMER] = REASON_HIGH_BARRIER_TO_ENTRY
    if not _career_capital_qualifies(record):
        gates[PERSONA_CAREER_CAPITAL] = REASON_LOW_CRITICALITY
    return gates


def persona_composite(record: ScoreRecord, persona: str) -> tuple[float | None, float, str | None]:
    """D-AGG-4: reproject ``record.pillars`` under ``persona``'s preset
    weights -- ``(composite, coverage, tier)``.

    Raises ``ValueError`` if ``persona`` is not one of :data:`PERSONA_PRESETS`
    (the five D-PERS-2/spec-section-4 presets, ``"balanced"`` included), or
    if ``record.pillars`` does not carry exactly the 5 frozen pillar names
    (``constants_v2.PILLAR_NAMES`` -- a gate-ineligible record's
    ``pillars`` is ``{}``, out of scope for a persona *projection*: there is
    no balanced score to reproject).

    ``coverage`` is ``Sum(w_p * c_p)`` over all 5 pillars (D-MISS-3's ``C``
    formula, generalised to ``persona``'s weights: unlike the pillar-score
    reduction below, coverage never renormalises over "measured" pillars --
    an unmeasured pillar's own coverage is already 0.0, so it contributes
    nothing and needs no special case). ``composite`` is ``None`` when every
    pillar is unmeasured (D-AGG-2's renormalisation, generalised the same
    way) or when ``cvi.core.aggregate.coverage_status`` finds the coverage
    below the D-EVID-1 rated threshold (mirroring ``cvi.core.engine.score``:
    an unrated record gets no composite and no tier under any persona,
    either); otherwise it is rounded to 2 decimals once, at the end, exactly
    as ``cvi.core.aggregate.composite_score`` does. ``tier`` reuses
    ``cvi.core.aggregate.tier_from_score``/``apply_ceiling`` verbatim (never
    re-implemented) on the *unrounded* composite, per 04 section 8.
    """
    if persona not in PERSONA_PRESETS:
        raise ValueError(f"unknown persona {persona!r}; expected one of {sorted(PERSONA_PRESETS)}")
    weights = PERSONA_PRESETS[persona]
    pillars = record.pillars
    missing = sorted(set(constants_v2.PILLAR_NAMES) - set(pillars))
    extra = sorted(set(pillars) - set(constants_v2.PILLAR_NAMES))
    if missing or extra:
        raise ValueError(
            f"record.pillars must carry exactly {constants_v2.PILLAR_NAMES}; missing={missing}, extra={extra}"
        )

    coverage = sum(weights[name] * pillars[name].coverage for name in constants_v2.PILLAR_NAMES)
    measured = [
        (weights[name], pillars[name].score)
        for name in constants_v2.PILLAR_NAMES
        if pillars[name].score is not None
    ]
    total_weight = sum(weight for weight, _ in measured)
    composite_raw = sum(weight * value for weight, value in measured) / total_weight if total_weight > 0.0 else None

    rated, ceiling = aggregate.coverage_status(coverage)
    if not rated or composite_raw is None:
        return None, coverage, None
    composite = round(composite_raw, 2)
    tier = aggregate.apply_ceiling(aggregate.tier_from_score(composite_raw), ceiling)
    return composite, coverage, tier
