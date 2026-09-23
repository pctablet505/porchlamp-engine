"""Acceptance tests: persona gates and persona composites, ``porchlamp/core/persona.py``.

A gated persona view keeps the balanced score:
fail-before from real, still-live v1 code rather than a deleted one:
``test_v1_lakehouse_mutates_the_gated_row`` reproduces
``porchlamp.lakehouse.OperationalStore.leaderboard``'s persona re-projection path
(the same scenario ``tests/test_lakehouse.py::test_leaderboard_persona_constraints``
already covers) and shows it overwrites ``is_stage0_disqualified`` and
zeroes the score in place;
``test_persona_gates_and_composite_do_not_mutate_record`` builds the v2
equivalent and shows ``ScoreRecord.composite``/``tier``/``gate.eligible``
are unchanged after the same gate fires.

Oracles are hand-computed and asserted as exact values (R-1): every
arithmetic docstring below shows the sum.
"""
from __future__ import annotations

import pytest

from porchlamp.core import constants
from porchlamp.core.models import (
    GateResult,
    Measured,
    ParamValue,
    PersonaComposite,
    PillarScore,
    RepoIdentity,
    ScoreRecord,
    Unmeasured,
)
from porchlamp.core.persona import (
    CAREER_CAPITAL_MIN_COVERAGE,
    CAREER_CAPITAL_MIN_CRITICALITY,
    CRITICALITY_PILLAR,
    FIRST_TIMER_MIN_DECIDED_PRS,
    FIRST_TIMER_MIN_MR_FIRST,
    FIRST_TIMER_MIN_NEWCOMER_ISSUES,
    GATED_PERSONAS,
    INPUT_MR_FIRST_RATE,
    INPUT_NEWCOMER_ISSUE_SUPPLY,
    PERSONA_AI_ASSISTED,
    PERSONA_BALANCED,
    PERSONA_CAREER_CAPITAL,
    PERSONA_FIRST_TIMER,
    PERSONA_PRESETS,
    PERSONA_SENIOR_SYSTEMS,
    REASON_AI_HARD_BAN,
    REASON_HIGH_BARRIER_TO_ENTRY,
    REASON_INSUFFICIENT_EVIDENCE_PREFIX,
    REASON_LOW_CRITICALITY,
    REASON_LOW_ELEPHANT_FACTOR,
    SENIOR_SYSTEMS_MIN_ELEPHANT_FACTOR,
    persona_composite,
    persona_gates,
)

_HASH64 = "a" * 64
_ISO_NOW = "2026-09-12T00:00:00+00:00"
_REPO = RepoIdentity(
    owner="org",
    name="alpha",
    ecosystem="pypi",
    archived=False,
    mirror_url=None,
    fork=False,
    license_spdx="MIT",
    default_branch="main",
)


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def _measured(value: float | int | str, *, n: int = 0) -> Measured:
    return Measured(
        value=value,
        n=n,
        window_start=None,
        window_end=None,
        method="primary",
        source="test://fixture",
        source_hash=_HASH64,
        fetched_at=_ISO_NOW,
    )


def _unmeasured(reason: str = "third_party_gap") -> Unmeasured:
    return Unmeasured(reason=reason)


def _neutral_inputs(**overrides: ParamValue) -> dict[str, ParamValue]:
    """Values that gate no persona (used so each test isolates one gate)."""
    base: dict[str, ParamValue] = {
        "ai_posture": _measured("REGULATED"),
        "elephant_factor": _measured(5.0),
        "mr_first_rate": _measured(0.90, n=20),
        "newcomer_issue_supply": _measured(10),
    }
    base.update(overrides)
    return base


def _pillar(score: float | None, coverage: float) -> PillarScore:
    return PillarScore(score=score, coverage=coverage)


def _gate(
    *, eligible: bool = True, reason: str | None = None, routed: str | None = None, undecided: tuple[str, ...] = ()
) -> GateResult:
    return GateResult(eligible=eligible, reason=reason, routed=routed, undecided=undecided)


def _record(
    pillars: dict[str, PillarScore],
    *,
    composite: float | None = None,
    coverage: float = 0.0,
    tier: str | None = None,
    gate: GateResult | None = None,
) -> ScoreRecord:
    return ScoreRecord(
        repo=_REPO,
        snapshot_id="snap-1",
        benchmark_version=constants.BENCHMARK_VERSION,
        constants_hash=constants.CONSTANTS_HASH,
        raw_hash=_HASH64,
        gate=gate if gate is not None else _gate(),
        params={},
        pillars=pillars,
        composite=composite,
        coverage=coverage,
        tier=tier,
        leverage=None,
        friction=None,
        quadrant=None,
        persona_gates={},
        persona_composites={},
        tie_key=(),
        tie_group=None,
        rank=None,
        suspicion=[],
    )


def _all_measured_pillars(
    *, responsiveness: float, criticality: float, governance: float, rigor: float, accessibility: float
) -> dict[str, PillarScore]:
    values = {
        "responsiveness": responsiveness,
        "criticality": criticality,
        "governance": governance,
        "rigor": rigor,
        "accessibility": accessibility,
    }
    return {name: _pillar(values[name], 1.0) for name in constants.PILLAR_NAMES}


# --------------------------------------------------------------------------- #
# Preset weight vectors (R-2 one-definition, D-PERS-2/spec section 4).
# --------------------------------------------------------------------------- #
def test_all_five_presets_cover_the_five_pillars_and_sum_to_one() -> None:
    assert set(PERSONA_PRESETS) == {
        PERSONA_BALANCED,
        PERSONA_FIRST_TIMER,
        PERSONA_CAREER_CAPITAL,
        PERSONA_SENIOR_SYSTEMS,
        PERSONA_AI_ASSISTED,
    }
    for persona, weights in PERSONA_PRESETS.items():
        assert set(weights) == set(constants.PILLAR_NAMES), persona
        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-6), persona


def test_balanced_preset_is_constants_pillar_weights_not_a_copy() -> None:
    """R-2: the balanced preset is read off the one frozen definition."""
    assert PERSONA_PRESETS[PERSONA_BALANCED] == constants.PILLAR_WEIGHTS


def test_non_balanced_presets_match_v1_constants_exactly() -> None:
    """Over-correction guard for the transcription the module docstring
    explains (porchlamp.core's import-graph test forbids importing
    porchlamp.constants.PERSONA_PRESETS directly): the four non-balanced presets
    transcribed into porchlamp/core/persona.py must not silently drift from the
    v1 source of truth."""
    from porchlamp.constants import PERSONA_PRESETS as V1_PRESETS

    for persona in (PERSONA_FIRST_TIMER, PERSONA_CAREER_CAPITAL, PERSONA_SENIOR_SYSTEMS, PERSONA_AI_ASSISTED):
        assert PERSONA_PRESETS[persona] == V1_PRESETS[persona], persona


# --------------------------------------------------------------------------- #
# ai_assisted: hard AI ban.
# --------------------------------------------------------------------------- #
def test_ai_assisted_gated_when_proscribed_with_evidence() -> None:
    """PROSCRIBED *with* evidence (Measured -- see module docstring).
    Criticality is 50 (< 60) so career_capital is also gated; the neutral
    ``mr_first_rate``/``newcomer_issue_supply`` inputs clear the
    first_timer bar, and elephant_factor 5.0 clears senior_systems, so
    those two are absent -- an exact-oracle dict, not a membership check."""
    record = _record(_all_measured_pillars(responsiveness=50, criticality=50, governance=50, rigor=50, accessibility=50))
    inputs = _neutral_inputs(ai_posture=_measured("PROSCRIBED"))
    assert persona_gates(record, inputs) == {
        PERSONA_AI_ASSISTED: REASON_AI_HARD_BAN,
        PERSONA_CAREER_CAPITAL: REASON_LOW_CRITICALITY,
    }


def test_ai_assisted_not_gated_without_evidence() -> None:
    """PROSCRIBED *without* evidence: Unmeasured, whatever the reason."""
    record = _record({})
    inputs = _neutral_inputs(ai_posture=_unmeasured())
    gates = persona_gates(record, inputs)
    assert PERSONA_AI_ASSISTED not in gates


def test_ai_assisted_not_gated_for_regulated_or_ad_hoc() -> None:
    record = _record({})
    for posture in ("REGULATED", "AD_HOC"):
        gates = persona_gates(record, _neutral_inputs(ai_posture=_measured(posture)))
        assert PERSONA_AI_ASSISTED not in gates, posture


# --------------------------------------------------------------------------- #
# senior_systems: multi-vendor (elephant factor).
# --------------------------------------------------------------------------- #
def test_senior_systems_gated_when_ef_measured_below_3() -> None:
    record = _record({})
    inputs = _neutral_inputs(elephant_factor=_measured(SENIOR_SYSTEMS_MIN_ELEPHANT_FACTOR - 1.0))
    assert persona_gates(record, inputs)[PERSONA_SENIOR_SYSTEMS] == REASON_LOW_ELEPHANT_FACTOR


def test_senior_systems_not_gated_when_ef_measured_at_3() -> None:
    """Boundary: EF == 3 is not "below 3" (>= is the qualifying side)."""
    record = _record({})
    inputs = _neutral_inputs(elephant_factor=_measured(SENIOR_SYSTEMS_MIN_ELEPHANT_FACTOR))
    assert PERSONA_SENIOR_SYSTEMS not in persona_gates(record, inputs)


def test_senior_systems_not_gated_when_ef_unmeasured() -> None:
    """D-MISS-1: no evidence is not evidence of the opposite."""
    record = _record({})
    inputs = _neutral_inputs(elephant_factor=_unmeasured())
    assert PERSONA_SENIOR_SYSTEMS not in persona_gates(record, inputs)


# --------------------------------------------------------------------------- #
# first_timer: low barrier -- gated unless (decided PRs >= 5 and MR >= 0.40)
# or newcomer-issue supply >= 3.
# --------------------------------------------------------------------------- #
def test_first_timer_not_gated_at_5_decided_prs_and_mr_040() -> None:
    """Boundary: n == 5, MR == 0.40, both inclusive; supply unmeasured
    isolates the first branch."""
    record = _record({})
    inputs = _neutral_inputs(
        mr_first_rate=_measured(FIRST_TIMER_MIN_MR_FIRST, n=FIRST_TIMER_MIN_DECIDED_PRS),
        newcomer_issue_supply=_unmeasured(),
    )
    assert PERSONA_FIRST_TIMER not in persona_gates(record, inputs)


def test_first_timer_gated_at_4_decided_prs() -> None:
    """D-PERS-3: ``mr_first_rate`` is ``Measured`` and genuinely fails (n=4),
    but ``newcomer_issue_supply`` is ``Unmeasured`` -- the gate cannot rule
    out that the second branch would have rescued it, so the reason names
    the missing evidence, not the substantive bar."""
    record = _record({})
    inputs = _neutral_inputs(
        mr_first_rate=_measured(FIRST_TIMER_MIN_MR_FIRST, n=FIRST_TIMER_MIN_DECIDED_PRS - 1),
        newcomer_issue_supply=_unmeasured(),
    )
    assert (
        persona_gates(record, inputs)[PERSONA_FIRST_TIMER]
        == f"{REASON_INSUFFICIENT_EVIDENCE_PREFIX}{INPUT_NEWCOMER_ISSUE_SUPPLY}"
    )


def test_first_timer_gated_at_mr_039() -> None:
    record = _record({})
    inputs = _neutral_inputs(
        mr_first_rate=_measured(0.39, n=FIRST_TIMER_MIN_DECIDED_PRS),
        newcomer_issue_supply=_unmeasured(),
    )
    assert PERSONA_FIRST_TIMER in persona_gates(record, inputs)


def test_first_timer_not_gated_at_mr_040() -> None:
    record = _record({})
    inputs = _neutral_inputs(
        mr_first_rate=_measured(FIRST_TIMER_MIN_MR_FIRST, n=FIRST_TIMER_MIN_DECIDED_PRS),
        newcomer_issue_supply=_unmeasured(),
    )
    assert PERSONA_FIRST_TIMER not in persona_gates(record, inputs)


def test_first_timer_gated_at_2_newcomer_issues() -> None:
    """Isolate the second branch: mr_first unmeasured. D-PERS-3: since
    ``mr_first_rate`` itself is ``Unmeasured``, the reason names it (checked
    first, per ``_first_timer_reason``'s order) even though
    ``newcomer_issue_supply`` was genuinely measured and failing."""
    record = _record({})
    inputs = _neutral_inputs(
        mr_first_rate=_unmeasured(),
        newcomer_issue_supply=_measured(FIRST_TIMER_MIN_NEWCOMER_ISSUES - 1),
    )
    assert (
        persona_gates(record, inputs)[PERSONA_FIRST_TIMER]
        == f"{REASON_INSUFFICIENT_EVIDENCE_PREFIX}{INPUT_MR_FIRST_RATE}"
    )


def test_first_timer_gated_reason_substantive_when_both_inputs_measured_and_failing() -> None:
    """D-PERS-3 second branch: when *both* relevant inputs are ``Measured``
    and still fail to qualify, the reason is the substantive
    ``high_barrier_to_entry`` -- there is no missing evidence to blame."""
    record = _record({})
    inputs = _neutral_inputs(
        mr_first_rate=_measured(0.10, n=FIRST_TIMER_MIN_DECIDED_PRS),
        newcomer_issue_supply=_measured(FIRST_TIMER_MIN_NEWCOMER_ISSUES - 1),
    )
    assert persona_gates(record, inputs)[PERSONA_FIRST_TIMER] == REASON_HIGH_BARRIER_TO_ENTRY


def test_first_timer_not_gated_at_3_newcomer_issues() -> None:
    record = _record({})
    inputs = _neutral_inputs(
        mr_first_rate=_unmeasured(),
        newcomer_issue_supply=_measured(FIRST_TIMER_MIN_NEWCOMER_ISSUES),
    )
    assert PERSONA_FIRST_TIMER not in persona_gates(record, inputs)


def test_first_timer_gated_when_both_inputs_unmeasured() -> None:
    """Unmeasured inputs satisfy neither branch. D-PERS-3: with both inputs
    unmeasured, the reason names ``mr_first_rate`` -- the first one checked
    (``_first_timer_reason``'s order) -- rather than a substantive reason;
    this is the sample-200 case (78/78, module docstring)."""
    record = _record({})
    inputs = _neutral_inputs(mr_first_rate=_unmeasured(), newcomer_issue_supply=_unmeasured())
    assert (
        persona_gates(record, inputs)[PERSONA_FIRST_TIMER]
        == f"{REASON_INSUFFICIENT_EVIDENCE_PREFIX}{INPUT_MR_FIRST_RATE}"
    )


def test_first_timer_not_gated_via_supply_even_when_mr_rate_is_low() -> None:
    """The two branches are independent (OR, not AND)."""
    record = _record({})
    inputs = _neutral_inputs(
        mr_first_rate=_measured(0.10, n=FIRST_TIMER_MIN_DECIDED_PRS),
        newcomer_issue_supply=_measured(FIRST_TIMER_MIN_NEWCOMER_ISSUES),
    )
    assert PERSONA_FIRST_TIMER not in persona_gates(record, inputs)


# --------------------------------------------------------------------------- #
# career_capital: high-tier brand -- gated unless criticality >= 60 and its
# coverage >= 0.80.
# --------------------------------------------------------------------------- #
def test_career_capital_not_gated_at_boundary_60_and_080() -> None:
    record = _record({"criticality": _pillar(CAREER_CAPITAL_MIN_CRITICALITY, CAREER_CAPITAL_MIN_COVERAGE)})
    assert PERSONA_CAREER_CAPITAL not in persona_gates(record, _neutral_inputs())


def test_career_capital_gated_just_below_score_and_coverage() -> None:
    """Score fails the threshold on its own, so the substantive reason stands
    even though coverage is also short."""
    record = _record({"criticality": _pillar(59.99, 0.79)})
    assert persona_gates(record, _neutral_inputs())[PERSONA_CAREER_CAPITAL] == REASON_LOW_CRITICALITY


def test_career_capital_gated_when_score_high_but_coverage_low() -> None:
    """Only the coverage condition fails: the reason must name the evidence gap.

    D-PERS-3 forbids a substantive reason when the gate fires on missing
    evidence, and this repo's criticality is 90 -- far above the threshold.
    Reporting ``criticality_below_threshold`` here would state something
    the scores contradict."""
    record = _record({"criticality": _pillar(90.0, 0.79)})
    assert (
        persona_gates(record, _neutral_inputs())[PERSONA_CAREER_CAPITAL]
        == f"{REASON_INSUFFICIENT_EVIDENCE_PREFIX}{CRITICALITY_PILLAR}"
    )


def test_career_capital_thin_coverage_is_never_reported_as_low_criticality() -> None:
    """The boundary case of the rule above: score exactly at the threshold,
    coverage one step under it."""
    record = _record({"criticality": _pillar(CAREER_CAPITAL_MIN_CRITICALITY, CAREER_CAPITAL_MIN_COVERAGE - 0.01)})
    reason = persona_gates(record, _neutral_inputs())[PERSONA_CAREER_CAPITAL]
    assert reason != REASON_LOW_CRITICALITY
    assert reason == f"{REASON_INSUFFICIENT_EVIDENCE_PREFIX}{CRITICALITY_PILLAR}"


def test_career_capital_gated_when_coverage_high_but_score_low() -> None:
    """Score is ``Measured`` (59.99): D-PERS-3's substantive branch."""
    record = _record({"criticality": _pillar(59.99, 1.0)})
    assert persona_gates(record, _neutral_inputs())[PERSONA_CAREER_CAPITAL] == REASON_LOW_CRITICALITY


def test_career_capital_gated_when_criticality_unmeasured() -> None:
    """D-PERS-3: ``score is None`` -- the pillar itself is unmeasured (no
    measured parameter contributed to it), so the reason names the pillar
    rather than claiming a substantively low criticality."""
    record = _record({"criticality": _pillar(None, 0.0)})
    assert (
        persona_gates(record, _neutral_inputs())[PERSONA_CAREER_CAPITAL]
        == f"{REASON_INSUFFICIENT_EVIDENCE_PREFIX}{CRITICALITY_PILLAR}"
    )


def test_career_capital_gated_when_criticality_pillar_missing() -> None:
    """A gate-ineligible record's ``pillars`` is ``{}`` (``porchlamp.core.engine``'s
    ``_bare_record``); the gate must not raise ``KeyError``, and D-PERS-3
    treats a wholly missing pillar the same as an unmeasured one."""
    record = _record({})
    assert (
        persona_gates(record, _neutral_inputs())[PERSONA_CAREER_CAPITAL]
        == f"{REASON_INSUFFICIENT_EVIDENCE_PREFIX}{CRITICALITY_PILLAR}"
    )


def test_persona_gates_no_entry_when_fully_qualifying() -> None:
    """Over-correction guard: a repo that clears every D-PERS-2 bar is gated
    from nothing -- the dict is empty, not full of False-ish entries."""
    record = _record({"criticality": _pillar(90.0, 1.0)})
    inputs = _neutral_inputs(
        mr_first_rate=_measured(0.9, n=20),
        newcomer_issue_supply=_measured(10),
    )
    assert persona_gates(record, inputs) == {}


# --------------------------------------------------------------------------- #
# D-PERS-1 separation: a gated persona view keeps the balanced score.
# Fail-before: v1's porchlamp.lakehouse overwrote the row in place.
# --------------------------------------------------------------------------- #
def test_v1_lakehouse_mutates_the_gated_row() -> None:
    """Fail-before evidence: the still-live v1 defect this packet does not
    repeat. Same scenario as
    ``tests/test_lakehouse.py::test_leaderboard_persona_constraints``: an
    AI_ASSISTED-persona view of a PROSCRIBED repo overwrites
    ``is_stage0_disqualified`` to 1 and the composite to 0.0 *in the
    returned row* -- there is no way for a caller of
    ``OperationalStore.leaderboard`` to recover the balanced score from a
    gated row under that persona."""
    from porchlamp.lakehouse import apply_raw_metrics
    from porchlamp.lakehouse import OperationalStore
    from porchlamp.models import AIPosture, PorchlampCompositeResult, Ecosystem, PillarScores, Quadrant, Tier

    store = OperationalStore(":memory:")
    store.upsert_composite(
        PorchlampCompositeResult(
            owner="o1",
            name="gentoo",
            ecosystem=Ecosystem.PYPI,
            overall_porchlamp=80.0,
            rank_tier=Tier.B,
            matrix_quadrant=Quadrant.SWEET_SPOT,
            leverage_score=60.0,
            friction_score=40.0,
            is_stage0_disqualified=False,
            disqualification_reason=None,
            pillar_scores=PillarScores(80, 80, 80, 80, 80),
        ),
        pillar_scores=PillarScores(80, 80, 80, 80, 80),
    )
    apply_raw_metrics(store, "o1", "gentoo", stars=100, forks=20, ai_posture=AIPosture.PROSCRIBED)

    rows, _ = store.leaderboard(persona="ai_assisted")
    gentoo_row = next(r for r in rows if r["name"] == "gentoo")
    assert gentoo_row["is_stage0_disqualified"] == 1
    assert gentoo_row["overall_porchlamp"] == 0.0
    assert gentoo_row["rank_tier"] == "Tier D"


def test_persona_gates_and_composite_do_not_mutate_record() -> None:
    """The v2 contrast: the same conceptual scenario (a PROSCRIBED repo
    under the ai_assisted persona), but ``ScoreRecord.composite``/``tier``/
    ``gate.eligible`` are read back unchanged after both the gate and the
    projection are computed -- D-PERS-1's rule holds because the record is
    an immutable (frozen) dataclass and both functions are pure, not
    because nothing was gated."""
    # Varied pillar scores (not uniform): a uniform 80/80/80/80/80 record
    # would reproject to 80.0 under *any* weight vector, masking the very
    # thing this test checks (that the projection and the stored score can
    # and do differ). Composite/coverage/tier are the real balanced values
    # for these pillars (see ``_EXPECTED_BY_PERSONA[PERSONA_BALANCED]``).
    record = _record(
        _HAND_PILLARS,
        composite=70.3,
        coverage=1.0,
        tier="A",
        gate=_gate(eligible=True),
    )
    inputs = _neutral_inputs(ai_posture=_measured("PROSCRIBED"))

    gates = persona_gates(record, inputs)
    assert gates[PERSONA_AI_ASSISTED] == REASON_AI_HARD_BAN

    projected = persona_composite(record, PERSONA_AI_ASSISTED)
    assert (projected.porchlamp, projected.coverage, projected.tier) != (record.composite, record.coverage, record.tier)

    # The balanced record itself is untouched by either call.
    assert record.composite == 70.3
    assert record.coverage == 1.0
    assert record.tier == "A"
    assert record.gate.eligible is True


def test_persona_gates_and_persona_composite_do_not_mutate_containers() -> None:
    """Purity guard: a whole-record equality check against a ``copy.deepcopy``
    taken before either call, ensuring mutable container members are not
    mutated in place."""
    import copy

    record = _record(
        _HAND_PILLARS,
        composite=70.3,
        coverage=1.0,
        tier="A",
        gate=_gate(eligible=True),
    )
    before = copy.deepcopy(record)
    inputs = _neutral_inputs(ai_posture=_measured("PROSCRIBED"))

    persona_gates(record, inputs)
    for persona in PERSONA_PRESETS:
        persona_composite(record, persona)

    assert record == before
    assert record.persona_gates == {} and record.persona_gates == before.persona_gates
    assert record.suspicion == [] and record.suspicion == before.suspicion
    assert record.persona_composites == {} and record.persona_composites == before.persona_composites
    assert record.pillars == before.pillars


# --------------------------------------------------------------------------- #
# persona_composite: renormalisation, coverage, ceiling -- every preset.
# --------------------------------------------------------------------------- #
_HAND_PILLARS = _all_measured_pillars(responsiveness=80.0, criticality=60.0, governance=70.0, rigor=52.0, accessibility=90.0)
_HAND_RECORD = _record(_HAND_PILLARS)

#: persona -> (expected composite, expected coverage, expected tier), all
#: pillars measured at coverage 1.0 so persona coverage is always 1.0
#: (>= 0.95 -> no ceiling) and the composite is the plain weighted sum
#: (denominator 1.0). Arithmetic:
#:   balanced:        .25*80+.25*60+.20*70+.15*52+.15*90 = 70.3   -> "A"
#:   first_timer:     .35*80+.10*60+.15*70+.10*52+.30*90 = 76.7   -> "A"
#:   career_capital:  .20*80+.35*60+.15*70+.10*52+.20*90 = 70.7   -> "A"
#:   senior_systems:  .15*80+.35*60+.25*70+.20*52+.05*90 = 65.4   -> "B"
#:   ai_assisted:     .20*80+.20*60+.15*70+.35*52+.10*90 = 65.7   -> "B"
_EXPECTED_BY_PERSONA: dict[str, tuple[float, float, str]] = {
    PERSONA_BALANCED: (70.3, 1.0, "A"),
    PERSONA_FIRST_TIMER: (76.7, 1.0, "A"),
    PERSONA_CAREER_CAPITAL: (70.7, 1.0, "A"),
    PERSONA_SENIOR_SYSTEMS: (65.4, 1.0, "B"),
    PERSONA_AI_ASSISTED: (65.7, 1.0, "B"),
}


@pytest.mark.parametrize("persona", sorted(PERSONA_PRESETS))
def test_persona_composite_arithmetic_every_preset(persona: str) -> None:
    result = persona_composite(_HAND_RECORD, persona)
    assert isinstance(result, PersonaComposite)
    expected_composite, expected_coverage, expected_tier = _EXPECTED_BY_PERSONA[persona]
    assert result.porchlamp == pytest.approx(expected_composite)
    assert result.coverage == pytest.approx(expected_coverage)
    assert result.tier == expected_tier
    assert result.rated is True  # every preset here has coverage 1.0 >= 0.60


def test_persona_composite_equals_aggregate_functions_when_balanced() -> None:
    """Over-correction guard: the generalised reduction is not a second,
    drifting definition of the balanced arithmetic -- it must agree with
    ``porchlamp.core.aggregate`` bit for bit when the weights are balanced."""
    from porchlamp.core import aggregate

    for pillars in (
        _HAND_PILLARS,
        {  # an unrated case: only governance measured (weight .20 < 0.60)
            "responsiveness": _pillar(None, 0.0),
            "criticality": _pillar(None, 0.0),
            "governance": _pillar(70.0, 1.0),
            "rigor": _pillar(None, 0.0),
            "accessibility": _pillar(None, 0.0),
        },
    ):
        record = _record(pillars)
        expected_coverage = aggregate.composite_coverage(pillars)
        rated, ceiling = aggregate.coverage_status(expected_coverage)
        expected_raw = aggregate.composite_score_raw(pillars)
        # Mirrors porchlamp.core.engine.score's own "not rated -> no composite, no
        # tier" branch (_bare_record): aggregate.composite_score itself has
        # no notion of the D-EVID-1 rated gate, so the *caller* -- here and
        # in the engine -- withholds the value below the coverage threshold.
        expected_composite = None if not rated else aggregate.composite_score(pillars)
        expected_tier = (
            None if not rated or expected_raw is None else aggregate.apply_ceiling(aggregate.tier_from_score(expected_raw), ceiling)
        )

        result = persona_composite(record, PERSONA_BALANCED)
        assert result.porchlamp == expected_composite
        assert result.coverage == expected_coverage
        assert result.tier == expected_tier
        assert result.rated is rated


def test_persona_composite_matches_the_inline_formula_bit_for_bit() -> None:
    """Over-correction guard: ``persona_composite``
    weighted pillar-to-composite reduction matches the inline formula
    bit for bit across random pillar vectors and persona presets."""
    import random

    from porchlamp.core import aggregate

    rng = random.Random(20260911)  # arbitrary fixed seed, reproducible

    for _ in range(2000):
        pillars: dict[str, PillarScore] = {}
        for pillar in constants.PILLAR_NAMES:
            if rng.random() < 0.2:  # sometimes unmeasured, to exercise renormalisation/dropout
                pillars[pillar] = _pillar(None, 0.0)
            else:
                pillars[pillar] = _pillar(rng.uniform(0.0, 100.0), rng.uniform(0.0, 1.0))
        record = _record(pillars)

        for persona in sorted(PERSONA_PRESETS):
            weights = PERSONA_PRESETS[persona]

            # The old private reduction persona.py carried before this
            # packet -- transcribed here, never calling the new aggregate
            # functions, so this is a real independent oracle. The one
            # thing it does take from aggregate is RATIO_DECIMALS: every
            # weighted-mean ratio is rounded there to strip the ~1e-16 ULP
            # error the decimal weights carry in binary floating point, and
            # an oracle that skipped that step would be asserting the
            # representation error, not the formula.
            old_coverage = round(
                sum(weights[name] * pillars[name].coverage for name in constants.PILLAR_NAMES),
                aggregate.RATIO_DECIMALS,
            )
            old_measured = [
                (weights[name], pillars[name].score)
                for name in constants.PILLAR_NAMES
                if pillars[name].score is not None
            ]
            old_total_weight = sum(weight for weight, _ in old_measured)
            old_composite_raw = (
                round(
                    sum(weight * value for weight, value in old_measured) / old_total_weight,
                    aggregate.RATIO_DECIMALS,
                )
                if old_total_weight > 0.0
                else None
            )
            old_rated, old_ceiling = aggregate.coverage_status(old_coverage)
            if not old_rated or old_composite_raw is None:
                expected = PersonaComposite(porchlamp=None, coverage=old_coverage, tier=None, rated=False)
            else:
                old_composite = round(old_composite_raw, 2)
                old_tier = aggregate.apply_ceiling(aggregate.tier_from_score(old_composite_raw), old_ceiling)
                expected = PersonaComposite(porchlamp=old_composite, coverage=old_coverage, tier=old_tier, rated=True)

            result = persona_composite(record, persona)
            assert result == expected, (persona, pillars)


def test_coverage_under_a_persona_differs_from_balanced_coverage() -> None:
    """Hand-computed: accessibility unmeasured (coverage 0.0), everything
    else measured (coverage 1.0). Balanced weighs accessibility .15,
    ai_assisted weighs it .10, so the two persona coverages differ:
      balanced:    .25+.25+.20+.15+.15*0 = 0.85
      ai_assisted: .20+.20+.15+.35+.10*0 = 0.90
    Composite (renormalised over the 4 measured pillars):
      balanced:    (.25*80+.25*60+.20*70+.15*52)/0.85 = 56.8/0.85 = 66.8235...  -> 66.82
      ai_assisted: (.20*80+.20*60+.15*70+.35*52)/0.90 = 56.7/0.90 = 63.0
    Both round to tier "B" (55 <= x < 70); coverage 0.85/0.90 both land in
    the [0.80, 0.95) ceiling band ("A"), and ``apply_ceiling`` keeps the
    worse of the two -- "B" either way, so the tier does not distinguish
    them, but the composite and coverage numbers do.
    """
    pillars = {
        "responsiveness": _pillar(80.0, 1.0),
        "criticality": _pillar(60.0, 1.0),
        "governance": _pillar(70.0, 1.0),
        "rigor": _pillar(52.0, 1.0),
        "accessibility": _pillar(None, 0.0),
    }
    record = _record(pillars)

    balanced = persona_composite(record, PERSONA_BALANCED)
    ai = persona_composite(record, PERSONA_AI_ASSISTED)

    assert balanced.coverage == pytest.approx(0.85)
    assert ai.coverage == pytest.approx(0.90)
    assert balanced.coverage != ai.coverage

    assert balanced.porchlamp == pytest.approx(66.82)
    assert ai.porchlamp == pytest.approx(63.0)
    assert balanced.porchlamp != ai.porchlamp

    assert balanced.tier == "B"
    assert ai.tier == "B"
    assert balanced.rated is True
    assert ai.rated is True


def test_persona_composite_unrated_below_evidence_threshold() -> None:
    """Coverage below 0.60 (D-EVID-1): no composite, no tier, under any
    persona -- mirrors ``porchlamp.core.engine.score``'s unrated shape."""
    pillars = {
        "responsiveness": _pillar(None, 0.0),
        "criticality": _pillar(None, 0.0),
        "governance": _pillar(70.0, 1.0),
        "rigor": _pillar(None, 0.0),
        "accessibility": _pillar(None, 0.0),
    }
    record = _record(pillars)
    result = persona_composite(record, PERSONA_BALANCED)
    assert result.coverage == pytest.approx(0.20)
    assert result.porchlamp is None
    assert result.tier is None
    assert result.rated is False


def test_persona_composite_none_when_every_pillar_unmeasured() -> None:
    pillars = {name: _pillar(None, 0.0) for name in constants.PILLAR_NAMES}
    record = _record(pillars)
    result = persona_composite(record, PERSONA_BALANCED)
    assert result.coverage == pytest.approx(0.0)
    assert result.porchlamp is None
    assert result.tier is None
    assert result.rated is False


def test_persona_composite_raises_on_unknown_persona() -> None:
    record = _record(_HAND_PILLARS)
    with pytest.raises(ValueError, match="unknown persona"):
        persona_composite(record, "super_hacker")


def test_persona_composite_raises_when_pillars_incomplete() -> None:
    """A gate-ineligible record's ``pillars`` is ``{}``: no balanced score
    exists to reproject, so this is a programming error to catch loudly,
    not a silent (None, 0.0, None)."""
    record = _record({})
    with pytest.raises(ValueError, match="must carry exactly"):
        persona_composite(record, PERSONA_BALANCED)


def test_persona_composite_tier_uses_unrounded_composite_not_rounded() -> None:
    """Tier-cutoff comparisons use the *unrounded* composite, never the
    rounded display value. All five pillars at 69.996 round to a displayed
    ``porchlamp`` of 70.0, but 69.996 is still below 70.0, so the tier must stay 'B'
    (55 <= x < 70), not jump to 'A'."""
    pillars = _all_measured_pillars(
        responsiveness=69.996, criticality=69.996, governance=69.996, rigor=69.996, accessibility=69.996
    )
    record = _record(pillars)
    result = persona_composite(record, PERSONA_BALANCED)
    assert result.porchlamp == pytest.approx(70.0)
    assert result.coverage == pytest.approx(1.0)
    assert result.tier == "B"
    assert result.rated is True


def test_persona_composite_applies_binding_ceiling_not_just_tier_by_score() -> None:
    """A persona whose own weighted coverage lands in the ceiling-B band
    (0.60 <= C < 0.80) must have its tier capped at 'B' even though the
    unrounded composite alone would earn 'A'."""
    pillars = {
        "responsiveness": _pillar(91.0, 1.0),
        "criticality": _pillar(84.0, 1.0),
        "governance": _pillar(77.0, 1.0),
        "rigor": _pillar(41.0, 1.0),
        "accessibility": _pillar(None, 0.0),
    }
    record = _record(pillars)
    result = persona_composite(record, PERSONA_FIRST_TIMER)
    assert result.coverage == pytest.approx(0.70)
    assert result.porchlamp == pytest.approx(79.86)
    assert result.tier == "B"
    assert result.rated is True


def test_gated_personas_excludes_balanced() -> None:
    """D-PERS-1: the balanced view is the ungated default, never itself a
    gate target."""
    assert PERSONA_BALANCED not in GATED_PERSONAS
    assert set(GATED_PERSONAS) == {
        PERSONA_AI_ASSISTED,
        PERSONA_SENIOR_SYSTEMS,
        PERSONA_FIRST_TIMER,
        PERSONA_CAREER_CAPITAL,
    }
