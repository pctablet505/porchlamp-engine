"""CVI v2 persona gates and persona composites (02 section 9: D-PERS-1/2/3).

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

D-PERS-3 (ruling 2026-09-13, following the W2-7 review's Warning W-1).
``ai_assisted`` and ``senior_systems`` above are *exclusion* gates and are
unchanged by this ruling: they fire only on measured evidence, so their
reason is always the substantive one. ``first_timer`` and ``career_capital``
are *inclusion* gates -- a repo appears under that lens only when the
evidence *satisfies* the condition, so an ``Unmeasured`` input can never
satisfy it, but firing for that reason is not the same fact as firing
because a measured value failed the bar. When one of these two gates fires
because an input is ``Unmeasured``, the reason is
``f"{REASON_INSUFFICIENT_EVIDENCE_PREFIX}{input_id}"`` (e.g.
``"insufficient_evidence:mr_first_rate"``), never
:data:`REASON_HIGH_BARRIER_TO_ENTRY`/:data:`REASON_LOW_CRITICALITY`; those
substantive reasons are reserved for the case where every relevant input
*was* ``Measured`` and still failed to qualify (:func:`_first_timer_reason`,
:func:`_career_capital_reason`). This matters in practice, not only in
principle: on the sample-200 fixture every one of the 78 gate-eligible
records has ``mr_first_rate``/``newcomer_issue_supply``/the criticality
pillar ``Unmeasured`` (the W1-4 collector that would populate
``mr_first_rate`` is not wired yet, C-2 in the W2-7 review), so before this
ruling both gates reported a substantive-sounding reason
(``high_barrier_to_entry`` / ``criticality_below_threshold``) for 78/78
repos where the true fact was a collector gap, not a measured judgement
about the repo.

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

One-definition-per-constant (R-2). Packet W2-8 (constants freeze) relocated
``PERSONA_PRESETS`` (all five preset vectors, ``"balanced"`` included -- it
is still ``constants_v2.PILLAR_WEIGHTS`` itself, just assembled inside
``constants_v2`` now instead of here), ``GATED_PERSONAS``,
``WEIGHT_SUM_TOLERANCE`` and the six gate thresholds
(``SENIOR_SYSTEMS_MIN_ELEPHANT_FACTOR``, ``FIRST_TIMER_MIN_DECIDED_PRS``,
``FIRST_TIMER_MIN_MR_FIRST``, ``FIRST_TIMER_MIN_NEWCOMER_ISSUES``,
``CAREER_CAPITAL_MIN_CRITICALITY``, ``CAREER_CAPITAL_MIN_COVERAGE``) into
``cvi.core.constants_v2``, imported back here so every existing name still
resolves as ``cvi.core.persona.<NAME>`` (``tests/core/test_persona_v2.py``
is unchanged). Before this packet the five preset vectors and the six gate
thresholds had no v2 home: ``cvi.core``'s import-graph test forbade this
module from importing v1's ``cvi.constants.PERSONA_PRESETS``, and W2-7
did not own ``constants_v2.py`` to add them there, so they were transcribed
here and pinned byte-equal to v1 by
``tests/core/test_persona_v2.py::test_non_balanced_presets_match_v1_constants_exactly``
-- that test is unchanged, since the values themselves did not move, only
their one true definition. This module still transcribes v1's byte-equal
check and the preset-sum validation below.
"""
from __future__ import annotations

from typing import Mapping

from cvi.core import aggregate, constants_v2
from cvi.core.models import Measured, ParamValue, PersonaComposite, ScoreRecord

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
#: gate target (D-PERS-1). Relocated to ``constants_v2.GATED_PERSONAS`` by
#: packet W2-8 (R-2); re-bound here under the original name so every
#: existing caller/test keeps resolving ``persona.GATED_PERSONAS``. The
#: string values there match the ``PERSONA_*`` identifiers below by content
#: (constants_v2 cannot import this module -- persona.py already imports
#: constants_v2 -- so it holds the literal strings, not these names).
GATED_PERSONAS: tuple[str, ...] = constants_v2.GATED_PERSONAS
assert GATED_PERSONAS == (
    PERSONA_AI_ASSISTED,
    PERSONA_SENIOR_SYSTEMS,
    PERSONA_FIRST_TIMER,
    PERSONA_CAREER_CAPITAL,
)

#: Tolerance for the preset weight-sum check. Relocated to
#: ``constants_v2.WEIGHT_SUM_TOLERANCE`` by packet W2-8 (R-2).
WEIGHT_SUM_TOLERANCE: float = constants_v2.WEIGHT_SUM_TOLERANCE

#: D-PERS-2 preset weight vectors (spec section 4). Relocated to
#: ``constants_v2.PERSONA_PRESETS`` by packet W2-8 (R-2) -- the freeze this
#: module's own docstring flagged as an ownership request. ``"balanced"`` is
#: still ``constants_v2.PILLAR_WEIGHTS`` itself, assembled inside
#: ``constants_v2`` now instead of here.
PERSONA_PRESETS: dict[str, dict[str, float]] = constants_v2.PERSONA_PRESETS


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

#: D-PERS-3 (ruling 2026-09-13): the reason prefix an *inclusion* gate
#: (``first_timer``, ``career_capital``) reports when it fires only because
#: one of its inputs is ``Unmeasured`` -- never a substantive reason such as
#: :data:`REASON_HIGH_BARRIER_TO_ENTRY`, which would claim a low measured
#: value the collector never produced. Rendered ``f"{REASON_INSUFFICIENT_EVIDENCE_PREFIX}{input_id}"``,
#: e.g. ``"insufficient_evidence:mr_first_rate"``.
REASON_INSUFFICIENT_EVIDENCE_PREFIX: str = "insufficient_evidence:"

# --------------------------------------------------------------------------- #
# D-PERS-2 gate thresholds. Relocated to ``constants_v2`` by packet W2-8
# (R-2); re-bound here under the original names.
# --------------------------------------------------------------------------- #
SENIOR_SYSTEMS_MIN_ELEPHANT_FACTOR: float = constants_v2.SENIOR_SYSTEMS_MIN_ELEPHANT_FACTOR
FIRST_TIMER_MIN_DECIDED_PRS: int = constants_v2.FIRST_TIMER_MIN_DECIDED_PRS
FIRST_TIMER_MIN_MR_FIRST: float = constants_v2.FIRST_TIMER_MIN_MR_FIRST
FIRST_TIMER_MIN_NEWCOMER_ISSUES: float = constants_v2.FIRST_TIMER_MIN_NEWCOMER_ISSUES
CAREER_CAPITAL_MIN_CRITICALITY: float = constants_v2.CAREER_CAPITAL_MIN_CRITICALITY
CAREER_CAPITAL_MIN_COVERAGE: float = constants_v2.CAREER_CAPITAL_MIN_COVERAGE

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

#: ``career_capital`` reads the already pillar-renormalised
#: ``record.pillars["criticality"]`` rather than a raw bundle input (D-PERS-2
#: table); this is the id the D-PERS-3 ``insufficient_evidence:<id>`` reason
#: names for that gate.
CRITICALITY_PILLAR: str = "criticality"
assert CRITICALITY_PILLAR in constants_v2.PILLAR_NAMES


def _ai_assisted_gated(bundle_inputs: Mapping[str, ParamValue]) -> bool:
    value = bundle_inputs[INPUT_AI_POSTURE]
    return isinstance(value, Measured) and value.value == AI_POSTURE_PROSCRIBED


def _senior_systems_gated(bundle_inputs: Mapping[str, ParamValue]) -> bool:
    value = bundle_inputs[INPUT_ELEPHANT_FACTOR]
    return isinstance(value, Measured) and value.value < SENIOR_SYSTEMS_MIN_ELEPHANT_FACTOR


def _first_timer_reason(bundle_inputs: Mapping[str, ParamValue]) -> str | None:
    """D-PERS-3: ``None`` if either branch of D-PERS-2's OR qualifies;
    otherwise the reason the gate fires.

    Checked in the order the branches are read: if ``mr_first_rate`` is the
    reason its own branch failed to qualify (``Unmeasured``, not merely a
    low value), the reason names it; else if ``newcomer_issue_supply`` is
    the reason its branch failed the same way, the reason names that one
    instead; only when *both* inputs were ``Measured`` and still failed to
    qualify is the reason the substantive
    :data:`REASON_HIGH_BARRIER_TO_ENTRY`."""
    mr_first = bundle_inputs[INPUT_MR_FIRST_RATE]
    mr_qualifies = (
        isinstance(mr_first, Measured)
        and mr_first.n >= FIRST_TIMER_MIN_DECIDED_PRS
        and mr_first.value >= FIRST_TIMER_MIN_MR_FIRST
    )
    supply = bundle_inputs[INPUT_NEWCOMER_ISSUE_SUPPLY]
    supply_qualifies = isinstance(supply, Measured) and supply.value >= FIRST_TIMER_MIN_NEWCOMER_ISSUES
    if mr_qualifies or supply_qualifies:
        return None
    if not isinstance(mr_first, Measured):
        return f"{REASON_INSUFFICIENT_EVIDENCE_PREFIX}{INPUT_MR_FIRST_RATE}"
    if not isinstance(supply, Measured):
        return f"{REASON_INSUFFICIENT_EVIDENCE_PREFIX}{INPUT_NEWCOMER_ISSUE_SUPPLY}"
    return REASON_HIGH_BARRIER_TO_ENTRY


def _career_capital_reason(record: ScoreRecord) -> str | None:
    """D-PERS-3: ``None`` if the criticality pillar qualifies; otherwise the
    reason the gate fires. A missing pillar (gate-ineligible record,
    ``record.pillars == {}``) or a pillar with no measured parameter
    (``score is None``) is the ``Unmeasured`` case -- reason names the
    pillar; a pillar with a real ``score`` that still fails the threshold or
    coverage condition is the substantive :data:`REASON_LOW_CRITICALITY`."""
    criticality = record.pillars.get(CRITICALITY_PILLAR)
    score = criticality.score if criticality is not None else None
    if score is None:
        return f"{REASON_INSUFFICIENT_EVIDENCE_PREFIX}{CRITICALITY_PILLAR}"
    if score >= CAREER_CAPITAL_MIN_CRITICALITY and criticality.coverage >= CAREER_CAPITAL_MIN_COVERAGE:
        return None
    return REASON_LOW_CRITICALITY


def persona_gates(record: ScoreRecord, bundle_inputs: Mapping[str, ParamValue]) -> dict[str, str]:
    """D-PERS-2: which persona views hide this repo, and why.

    ``record`` supplies the already pillar-renormalised scores the
    ``career_capital`` gate reads (``record.pillars``); ``bundle_inputs`` is
    the ``FeatureBundle.inputs`` mapping the other three gates read
    directly, keyed by the 18 frozen input ids (``constants_v2.PARAM_INPUT_IDS``).
    Only the four input ids named in the module docstring are ever looked
    up, so this function works on a gate-ineligible or unrated record too
    (``record.pillars`` may then be ``{}``; ``_career_capital_reason``
    treats a missing/unmeasured criticality pillar as not qualifying, never
    as an error).

    Returns ``{persona: reason}`` for exactly the personas this repo is
    gated from; a persona absent from the dict is not gated (D-PERS-1: the
    balanced score is never touched, and neither is any other persona's
    view of it -- the caller shows the repo under that persona's
    :func:`persona_composite` projection unchanged).

    D-PERS-3 (ruling 2026-09-13): ``ai_assisted`` and ``senior_systems`` are
    *exclusion* gates and are unchanged by this ruling -- they fire only on
    measured evidence, so their reason is always the substantive one above.
    ``first_timer`` and ``career_capital`` are *inclusion* gates: a repo
    appears under that lens only when the evidence satisfies the condition,
    so an ``Unmeasured`` input does not satisfy it, but the reason then
    names the evidence gap (``insufficient_evidence:<input id>``,
    :func:`_first_timer_reason`/:func:`_career_capital_reason`) rather than
    a substantive reason such as :data:`REASON_HIGH_BARRIER_TO_ENTRY`, which
    would misreport a real-but-unmeasured cohort as one that was measured
    and rejected.
    """
    gates: dict[str, str] = {}
    if _ai_assisted_gated(bundle_inputs):
        gates[PERSONA_AI_ASSISTED] = REASON_AI_HARD_BAN
    if _senior_systems_gated(bundle_inputs):
        gates[PERSONA_SENIOR_SYSTEMS] = REASON_LOW_ELEPHANT_FACTOR
    first_timer_reason = _first_timer_reason(bundle_inputs)
    if first_timer_reason is not None:
        gates[PERSONA_FIRST_TIMER] = first_timer_reason
    career_capital_reason = _career_capital_reason(record)
    if career_capital_reason is not None:
        gates[PERSONA_CAREER_CAPITAL] = career_capital_reason
    return gates


def persona_composite(record: ScoreRecord, persona: str) -> PersonaComposite:
    """D-AGG-4: reproject ``record.pillars`` under ``persona``'s preset
    weights -- a :class:`~cvi.core.models.PersonaComposite`.

    Raises ``ValueError`` if ``persona`` is not one of :data:`PERSONA_PRESETS`
    (the five D-PERS-2/spec-section-4 presets, ``"balanced"`` included), or
    if ``record.pillars`` does not carry exactly the 5 frozen pillar names
    (``constants_v2.PILLAR_NAMES`` -- a gate-ineligible record's
    ``pillars`` is ``{}``, out of scope for a persona *projection*: there is
    no balanced score to reproject).

    ``PersonaComposite.coverage`` is ``Sum(w_p * c_p)`` over all 5 pillars
    (D-MISS-3's ``C`` formula, generalised to ``persona``'s weights: unlike
    the pillar-score reduction below, coverage never renormalises over
    "measured" pillars -- an unmeasured pillar's own coverage is already
    0.0, so it contributes nothing and needs no special case).
    ``PersonaComposite.rated`` is that same persona-weighted coverage's own
    D-EVID-1/D-MISS-3 rated outcome (``coverage >= 0.60``, ``04`` section 6's
    per-persona-rated ruling: never copied from the balanced
    ``ScoreRecord``, since a repo rated under the balanced weights can be
    unrated under a persona's, and vice versa). ``cvi`` is ``None`` when
    unrated, or when every pillar is unmeasured (D-AGG-2's renormalisation,
    generalised the same way -- this case is itself always unrated, since an
    all-unmeasured pillar set has coverage 0.0 under any weights); otherwise
    it is rounded to 2 decimals once, at the end, exactly as
    ``cvi.core.aggregate.composite_score`` does. ``tier`` is ``None`` under
    the same unrated/unmeasured conditions; otherwise it reuses
    ``cvi.core.aggregate.tier_from_score``/``apply_ceiling`` verbatim (never
    re-implemented) on the *unrounded* composite, per 04 section 8 --
    tier-cutoff and ceiling-band comparisons never use the rounded ``cvi``.
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
        return PersonaComposite(cvi=None, coverage=coverage, tier=None, rated=False)
    composite = round(composite_raw, 2)
    tier = aggregate.apply_ceiling(aggregate.tier_from_score(composite_raw), ceiling)
    return PersonaComposite(cvi=composite, coverage=coverage, tier=tier, rated=True)
