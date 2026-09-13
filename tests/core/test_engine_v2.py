"""W2-5 acceptance: the v2 scoring engine, ``score(bundle, populations,
snapshot) -> ScoreRecord`` (02 sections 1, 5, 7, 8; 04 sections 3, 4).

Fail-before: before this packet ``cvi.core.engine`` does not exist, so every
test below fails on ``ModuleNotFoundError`` (recorded verbatim in the
handoff).

``EligibleFixture`` builds one hand-computed, fully-measured bundle (every
pillar 100.0 except rigor at 82.0, chosen so every intermediate value is a
clean decimal -- see its docstring for the arithmetic) used by the
end-to-end oracle and by the parameter-function call-count sanity check.
The D-GATE acceptance oracle (gated repo never calls the parameter
functions) uses a separate, minimal archived-repo bundle plus a
populations double that raises if ``ecdf`` is ever called, so the guard
does not depend on the fixture bundle at all.
"""
from __future__ import annotations

import datetime
from typing import Any, Callable

import pytest

from cvi.core import constants_v2, matrix
from cvi.core.engine import Snapshot, score
from cvi.core.gates import GateEvidence
from cvi.core.models import (
    Cohorts,
    FeatureBundle,
    GateResult,
    Measured,
    ParamScore,
    PillarScore,
    PRWindow,
    PRWindowEvent,
    RepoIdentity,
    ScoreRecord,
    Unmeasured,
)
from cvi.snapshot.populations import Population, PopulationSet

NOW = datetime.datetime(2026, 9, 12, 0, 0, tzinfo=datetime.timezone.utc)
_HASH64 = "b" * 64


def _iso(value: datetime.datetime) -> str:
    return value.isoformat()


def make_repo(**overrides: Any) -> RepoIdentity:
    repo = RepoIdentity(
        owner="org",
        name="alpha",
        ecosystem="pypi",
        archived=False,
        mirror_url=None,
        fork=False,
        license_spdx="MIT",
        default_branch="main",
    )
    if not overrides:
        return repo
    return RepoIdentity(**{**repo.__dict__, **overrides})


def qualifying_events() -> tuple[PRWindowEvent, ...]:
    """One external PR, decided (merged) inside the 365 d D-GATE-6 horizon,
    30 d old so it enters D-GATE-5's population (>= 14 d) -- with only one
    member that population is below D-GATE-5's minimum n (10), so D-GATE-5
    is UNDECIDED rather than a clean pass. Chosen deliberately (a 10-member
    healthy population would also work but adds nine more events for no
    extra coverage of this packet's own code)."""
    created = NOW - datetime.timedelta(days=30)
    return (
        PRWindowEvent(
            pr_number=1,
            created_at=_iso(created),
            closed_at=None,
            merged_at=_iso(created + datetime.timedelta(days=2)),
            author_login="contributor",
            author_association="NONE",
            is_external=True,
            first_human_review_at=_iso(created + datetime.timedelta(days=1)),
        ),
    )


def healthy_evidence() -> GateEvidence:
    """Evidence that clears every stage-0 gate outright (D-GATE-5 still
    lands UNDECIDED: see :func:`qualifying_events`)."""
    return GateEvidence(
        manifest_found=True,
        is_curated_anchor=False,
        license_file_present=None,
        fork_parent_in_universe=None,
        vitality_commits_180d=5,
        off_platform_marker=None,
    )


def _m(value: Any, *, n: int = 0, method: str = "primary") -> Measured:
    return Measured(
        value=value,
        n=n,
        window_start=None,
        window_end=None,
        method=method,
        source="test://fixture",
        source_hash=_HASH64,
        fetched_at=_iso(NOW),
    )


def make_bundle(inputs: dict[str, Measured | Unmeasured], **repo_overrides: Any) -> FeatureBundle:
    events = qualifying_events()
    bundle = FeatureBundle(
        schema_version=constants_v2.SCHEMA_VERSION,
        repo=make_repo(**repo_overrides),
        inputs=inputs,
        pr_window=PRWindow(
            start=_iso(NOW - datetime.timedelta(days=180)),
            end=_iso(NOW),
            truncated=False,
            events=events,
        ),
        cohorts=Cohorts(decision_pr_numbers=(), newcomer_authors=()),
        provenance_hash=_HASH64,
    )
    bundle.validate()
    return bundle


class EligibleFixture:
    """A fully-measured, gate-eligible bundle plus its frozen populations.

    Every parameter is chosen to land on a clean part of its curve:

    Responsiveness (all 100.0): TTFHR 12.0h (== the 12h knee); DR_90 rate
    0.8 (0.8*125=100, capped); MR_first rate 0.8 (125*0.8=100); GR_14 rate
    0.0 (100-200*0=100); TTM_p90 1.0h against population [5,10,20,30] (below
    every member -> ecdf=0 -> 100-0=100).
      pillar = 100.0, coverage = 1.0

    Criticality (all 100.0): dependents 999999 queried raw against the
    population [0,1,2,3] (above every member -> ecdf=100 -- D-NORM-3: the
    dependents/downloads curves query the raw measured value, and
    ``build_populations`` freezes raw values too, so the two sides rank the
    same quantity); OpenSSF criticality 1.0 -> 100*1=100; downloads
    999999999 queried raw against population [0,1,2] (above every member ->
    100).
      pillar = 100.0, coverage = 1.0

    Governance (all 100.0): legal_ip DCO=100; elephant_factor 4.0 ->
    min(100,25*4)=100; promotion_ladder VERIFIED=100.
      pillar = 100.0, coverage = 1.0

    Rigor: pipeline_defenses 100.0 (identity); ai_posture AD_HOC -> 40.0
    (human persona, the default); substantive_review_rate 0.8 ->
    min(100,125*0.8)=100.
      pillar = (.40*100 + .30*40 + .30*100) / 1.0 = 82.0, coverage = 1.0

    Accessibility (all 100.0): newcomer_issue_supply 10 -> min(100,10*10)
    =100; newcomer_conversion rate 0.5 -> min(100,200*0.5)=100; RR_90 rate
    0.5 -> 100; DX 100.0 (identity).
      pillar = 100.0, coverage = 1.0

    Composite coverage C = .25+.25+.20+.15+.15 = 1.0 (>= 0.95 -> no ceiling)
    composite_raw = .25*100 + .25*100 + .20*100 + .15*82 + .15*100
                  = 25 + 25 + 20 + 12.3 + 15 = 97.3
    tier_from_score(97.3) = "S+" (>=90); ceiling None -> tier "S+"

    leverage = .50*100 + .30*82 + .20*100 = 50 + 24.6 + 20 = 94.6
    friction = .50*(100-100) + .30*(100-100) + .20*(100-100) = 0.0
    quadrant: leverage high (>=50), friction low (<50) -> "Sweet Spot"
    """

    composite_raw = 97.3
    composite = 97.3
    tier = "S+"
    leverage = 94.6
    friction = 0.0
    quadrant = "Sweet Spot"

    @staticmethod
    def inputs() -> dict[str, Measured]:
        return {
            "ttfhr_hours": _m(12.0, n=10),
            "dr90_decision_rate": _m(0.8, n=10),
            "mr_first_rate": _m(0.8, n=5),
            "gr14_ghost_rate": _m(0.0, n=10),
            "ttm_p90_hours": _m(1.0, n=5),
            "dependents_deduped": _m(999999.0, n=0),
            "openssf_criticality": _m(1.0, n=0),
            "downloads_monthly": _m(999999999.0, n=0),
            "legal_ip": _m("DCO", n=0),
            "elephant_factor": _m(4.0, n=30),
            "promotion_ladder": _m("VERIFIED", n=0),
            "pipeline_defenses": _m(100.0, n=0),
            "ai_posture": _m("AD_HOC", n=0),
            "substantive_review_rate": _m(0.8, n=10),
            "newcomer_issue_supply": _m(10.0, n=0),
            "newcomer_conversion": _m(0.5, n=5),
            "rr90_return_rate": _m(0.5, n=5),
            "dx": _m(100.0, n=5),
        }

    @classmethod
    def bundle(cls) -> FeatureBundle:
        return make_bundle(cls.inputs())

    @staticmethod
    def populations() -> PopulationSet:
        return PopulationSet(
            snapshot_id="snap-1",
            benchmark_version=constants_v2.BENCHMARK_VERSION,
            constants_hash=constants_v2.CONSTANTS_HASH,
            populations=(
                Population("pypi", "ttm_p90_hours", 4, False, {"a": 5.0, "b": 10.0, "c": 20.0, "d": 30.0}),
                Population("pypi", "dependents_deduped", 4, False, {"a": 0.0, "b": 1.0, "c": 2.0, "d": 3.0}),
                Population("pypi", "downloads_monthly", 3, False, {"a": 0.0, "b": 1.0, "c": 2.0}),
            ),
        )

    @staticmethod
    def snapshot() -> Snapshot:
        return Snapshot(snapshot_id="snap-1", as_of=NOW, gate_evidence=healthy_evidence())


# --------------------------------------------------------------------------- #
# End-to-end oracle: every ScoreRecord field.
# --------------------------------------------------------------------------- #


def test_engine_end_to_end_every_field() -> None:
    bundle = EligibleFixture.bundle()
    record = score(bundle, EligibleFixture.populations(), EligibleFixture.snapshot())

    assert record.repo == bundle.repo
    assert record.snapshot_id == "snap-1"
    assert record.benchmark_version == constants_v2.BENCHMARK_VERSION
    assert record.constants_hash == constants_v2.CONSTANTS_HASH
    assert record.raw_hash == _HASH64

    assert record.gate.eligible is True
    assert record.gate.reason is None
    assert record.gate.routed is None
    assert record.gate.undecided == ("D-GATE-5",)

    assert set(record.params) == set(constants_v2.PARAM_INPUT_IDS)
    for input_id, param_score in record.params.items():
        assert param_score.measured is True, input_id
        assert param_score.reason is None, input_id
        # W2-11: every Measured ParamScore's ``value`` is the bundle input's
        # raw Measured.value, verbatim, before any curve.
        assert param_score.value == bundle.inputs[input_id].value, input_id
        assert param_score.n == bundle.inputs[input_id].n, input_id
        assert param_score.window_start == bundle.inputs[input_id].window_start, input_id
        assert param_score.window_end == bundle.inputs[input_id].window_end, input_id
        assert param_score.source == bundle.inputs[input_id].source, input_id
        assert param_score.fetched_at == bundle.inputs[input_id].fetched_at, input_id
    assert record.params["ttfhr_hours"].score == pytest.approx(100.0)
    assert record.params["ai_posture"].score == pytest.approx(40.0)
    assert record.params["ai_posture"].method == "primary"
    assert record.params["elephant_factor"].n == 30

    assert set(record.pillars) == set(constants_v2.PILLAR_NAMES)
    assert record.pillars["responsiveness"].score == pytest.approx(100.0)
    assert record.pillars["responsiveness"].coverage == pytest.approx(1.0)
    assert record.pillars["criticality"].score == pytest.approx(100.0)
    assert record.pillars["governance"].score == pytest.approx(100.0)
    assert record.pillars["rigor"].score == pytest.approx(82.0)
    assert record.pillars["accessibility"].score == pytest.approx(100.0)
    for pillar in constants_v2.PILLAR_NAMES:
        assert record.pillars[pillar].coverage == pytest.approx(1.0)

    assert record.composite == pytest.approx(EligibleFixture.composite)
    assert record.coverage == pytest.approx(1.0)
    assert record.tier == EligibleFixture.tier
    assert record.leverage == pytest.approx(EligibleFixture.leverage)
    assert record.friction == pytest.approx(EligibleFixture.friction)
    assert record.quadrant == EligibleFixture.quadrant

    assert record.persona_gates == {}
    assert record.persona_composites == {}
    assert record.tie_key == ()
    assert record.tie_group is None
    assert record.rank is None
    assert record.suspicion == []


# --------------------------------------------------------------------------- #
# D-GATE acceptance oracle: a gated repo never calls the parameter functions.
# --------------------------------------------------------------------------- #


class _PoisonPopulations:
    """A populations double that fails the test if ``ecdf`` is ever called."""

    def ecdf(self, ecosystem: str, param: str) -> Callable[[float], float]:
        raise AssertionError(f"populations.ecdf({ecosystem!r}, {param!r}) must not be called")


def _counting_stub(counter: list[int]) -> Callable[..., Any]:
    def stub(*_args: Any, **_kwargs: Any) -> Any:
        counter.append(1)
        raise AssertionError("parameter function must not be called")

    return stub


def test_gated_repo_never_calls_the_parameter_functions(monkeypatch: pytest.MonkeyPatch) -> None:
    from cvi.core import engine

    calls: list[int] = []
    monkeypatch.setattr(
        engine,
        "_PARAM_FUNCTIONS",
        {input_id: _counting_stub(calls) for input_id in constants_v2.PARAM_INPUT_IDS},
    )
    bundle = make_bundle(
        {input_id: Unmeasured(reason="collector_failure") for input_id in constants_v2.PARAM_INPUT_IDS},
        archived=True,
    )
    snapshot = Snapshot(snapshot_id="snap-1", as_of=NOW, gate_evidence=healthy_evidence())

    record = engine.score(bundle, _PoisonPopulations(), snapshot)

    assert record.repo == bundle.repo
    assert calls == []
    assert record.gate.eligible is False
    assert record.gate.reason == "archived"
    assert record.params == {}
    assert record.pillars == {}
    assert record.composite is None
    assert record.coverage == 0.0
    assert record.tier is None
    assert record.leverage is None
    assert record.friction is None
    assert record.quadrant is None


def test_eligible_repo_calls_every_parameter_function_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anti-vacuity for the guard above: the same counting mechanism, on an
    eligible bundle, does observe all 18 calls."""
    from cvi.core import engine

    calls: dict[str, int] = {input_id: 0 for input_id in constants_v2.PARAM_INPUT_IDS}
    real_functions = dict(engine._PARAM_FUNCTIONS)

    def make_counted(input_id: str) -> Callable[..., Any]:
        def counted(*args: Any, **kwargs: Any) -> Any:
            calls[input_id] += 1
            return real_functions[input_id](*args, **kwargs)

        return counted

    monkeypatch.setattr(
        engine,
        "_PARAM_FUNCTIONS",
        {input_id: make_counted(input_id) for input_id in constants_v2.PARAM_INPUT_IDS},
    )

    record = engine.score(EligibleFixture.bundle(), EligibleFixture.populations(), EligibleFixture.snapshot())

    assert calls == {input_id: 1 for input_id in constants_v2.PARAM_INPUT_IDS}
    assert record.composite == pytest.approx(EligibleFixture.composite)


# --------------------------------------------------------------------------- #
# D-EVID-1 acceptance oracle: insufficient composite coverage is unrated.
# --------------------------------------------------------------------------- #


def test_unrated_repo_has_no_composite_or_tier_but_keeps_params_and_coverage() -> None:
    """Only accessibility (weight .15) measured; the rest Unmeasured.
    C = .15*1.0 = 0.15 < 0.60 -> unrated. The three ECDF-kind inputs
    (responsiveness's ttm_p90, criticality's dependents/downloads) are all
    Unmeasured here, so an empty ``PopulationSet`` must not be consulted
    (regression guard for the eager-ecdf-fetch bug: calling ``.ecdf`` on an
    empty set would raise ``LookupError``).

    W2-5f (W-1): the stage-0 ``gate`` stays exactly as ``evaluate_gates``
    returned it (``eligible=True`` -- this repo passed every stage-0 gate);
    the stage-3 D-EVID-1 outcome lives in ``rated``/``unrated_reason``
    instead of overwriting it."""
    inputs: dict[str, Measured | Unmeasured] = {
        input_id: Unmeasured(reason="collector_failure") for input_id in constants_v2.PARAM_INPUT_IDS
    }
    inputs.update(
        {
            "newcomer_issue_supply": _m(10.0, n=0),
            "newcomer_conversion": _m(0.5, n=5),
            "rr90_return_rate": _m(0.5, n=5),
            "dx": _m(100.0, n=5),
        }
    )
    bundle = make_bundle(inputs)
    empty_populations = PopulationSet(
        snapshot_id="snap-1", benchmark_version=constants_v2.BENCHMARK_VERSION,
        constants_hash=constants_v2.CONSTANTS_HASH, populations=(),
    )
    snapshot = Snapshot(snapshot_id="snap-1", as_of=NOW, gate_evidence=healthy_evidence())

    record = score(bundle, empty_populations, snapshot)

    assert record.repo == bundle.repo
    assert record.gate.eligible is True
    assert record.gate.reason is None
    assert record.gate.undecided == ("D-GATE-5",)
    assert record.rated is False
    assert record.unrated_reason == "unrated: insufficient_evidence"
    assert record.tier_ceiling is None
    assert record.coverage == pytest.approx(0.15)
    assert record.pillars["accessibility"].score == pytest.approx(100.0)
    assert record.pillars["responsiveness"].score is None
    assert record.composite is None
    assert record.tier is None
    assert record.leverage is None
    assert record.friction is None
    assert record.quadrant is None
    assert record.leverage_coverage == pytest.approx(0.0)
    assert record.friction_coverage == pytest.approx(0.0)
    assert len(record.params) == 18

    # W2-11: value is None for every Unmeasured input, and the raw measured
    # value (verbatim) for every Measured one, regardless of the score/
    # measured outcome the curve produced.
    measured_ids = {"newcomer_issue_supply", "newcomer_conversion", "rr90_return_rate", "dx"}
    for input_id, param_score in record.params.items():
        if input_id in measured_ids:
            assert param_score.value == bundle.inputs[input_id].value, input_id
        else:
            assert param_score.value is None, input_id


# --------------------------------------------------------------------------- #
# D-MAT-1 acceptance oracles: cvi.core.matrix directly (unmeasured pillars
# drop out of the coordinate with renormalised weights; the 50.0 quadrant
# boundary counts as high on both axes).
# --------------------------------------------------------------------------- #


def test_leverage_drops_an_unmeasured_pillar_with_renormalised_weights() -> None:
    """Governance unmeasured; leverage's remaining weights are criticality
    .50 + rigor .30 = 0.80.
    leverage = (.50*90 + .30*50) / 0.80 = (45 + 15) / 0.80 = 75.0
    """
    pillars = {
        "responsiveness": PillarScore(score=0.0, coverage=1.0),
        "criticality": PillarScore(score=90.0, coverage=1.0),
        "governance": PillarScore(score=None, coverage=0.0),
        "rigor": PillarScore(score=50.0, coverage=1.0),
        "accessibility": PillarScore(score=0.0, coverage=1.0),
    }
    value, coverage = matrix.leverage(pillars)
    assert value == pytest.approx(75.0)
    assert coverage == pytest.approx(0.80)


def test_friction_drops_an_unmeasured_pillar_with_renormalised_weights() -> None:
    """Accessibility unmeasured; friction's remaining weights are
    responsiveness .50 + governance .30 = 0.80.
    friction = (.50*(100-80) + .30*(100-50)) / 0.80 = (10 + 15) / 0.80 = 31.25
    """
    pillars = {
        "responsiveness": PillarScore(score=80.0, coverage=1.0),
        "criticality": PillarScore(score=0.0, coverage=1.0),
        "governance": PillarScore(score=50.0, coverage=1.0),
        "rigor": PillarScore(score=0.0, coverage=1.0),
        "accessibility": PillarScore(score=None, coverage=0.0),
    }
    value, coverage = matrix.friction(pillars)
    assert value == pytest.approx(31.25)
    assert coverage == pytest.approx(0.80)


def test_coordinates_all_pillars_unmeasured_gives_none_axes_and_no_quadrant() -> None:
    """Anti-vacuity for the drop-out tests above: when every contributing
    pillar is unmeasured, the axis (and so the quadrant) is None, not 0.0 --
    0.0 would be a silently wrong "low" classification instead of "unknown"."""
    pillars = {pillar: PillarScore(score=None, coverage=0.0) for pillar in constants_v2.PILLAR_NAMES}
    coords = matrix.coordinates(pillars)
    assert coords.leverage is None
    assert coords.leverage_coverage == pytest.approx(0.0)
    assert coords.friction is None
    assert coords.friction_coverage == pytest.approx(0.0)
    assert coords.quadrant is None


def test_quadrant_boundary_50_counts_as_high_on_both_axes() -> None:
    """D-MAT-1: threshold 50.0, >= counts as high (the v1 convention kept):
    (50.0, 50.0) -> Crucible."""
    assert matrix.quadrant(50.0, 50.0) == matrix.QUADRANT_CRUCIBLE
    assert matrix.quadrant(49.99, 50.0) == matrix.QUADRANT_GRAVEYARD
    assert matrix.quadrant(50.0, 49.99) == matrix.QUADRANT_SWEET_SPOT
    assert matrix.quadrant(49.99, 49.99) == matrix.QUADRANT_NURSERY


def test_coordinates_rounds_leverage_and_friction_before_the_quadrant_comparison() -> None:
    """W2-5f mutant guard (review section 7, M13): matrix.py's own module
    docstring names this the deliberate v1-kept exception -- the quadrant
    compares *rounded* leverage/friction, not the raw pre-round values.
    criticality .50, rigor/governance 0.0 -> leverage_raw = .50*99.998 =
    49.999, which rounds to 50.0 (high); responsiveness/accessibility 100.0,
    governance 0.0 -> friction_raw = .30*(100-0) = 30.0 (unambiguously low,
    no rounding boundary involved). A mutant that quadrants on the raw
    (49.999, 30.0) pair would call leverage low and land on Nursery instead
    of Sweet Spot."""
    pillars = {
        "responsiveness": PillarScore(score=100.0, coverage=1.0),
        "criticality": PillarScore(score=99.998, coverage=1.0),
        "governance": PillarScore(score=0.0, coverage=1.0),
        "rigor": PillarScore(score=0.0, coverage=1.0),
        "accessibility": PillarScore(score=100.0, coverage=1.0),
    }
    lev_raw, _ = matrix.leverage(pillars)
    fric_raw, _ = matrix.friction(pillars)
    assert lev_raw == pytest.approx(49.999)
    assert fric_raw == pytest.approx(30.0)

    coords = matrix.coordinates(pillars)

    assert coords.leverage == pytest.approx(50.0)
    assert coords.friction == pytest.approx(30.0)
    assert coords.quadrant == matrix.QUADRANT_SWEET_SPOT


# --------------------------------------------------------------------------- #
# W2-5f mutant guards (review section 7, M10/M12): the engine's stored
# ``composite`` is the rounded display value, but ``tier`` is derived from
# the unrounded ``composite_raw`` (04 section 8) -- and this is the one case
# where the two diverge across a tier cutoff.
# --------------------------------------------------------------------------- #


def test_engine_derives_tier_from_the_unrounded_composite_not_the_rounded_display_value() -> None:
    """Every EligibleFixture pillar stays as documented except criticality's
    openssf_criticality, tuned so the criticality pillar lands at 70.784 and
    the composite at 89.996 (coverage 1.0, no ceiling):
    composite_raw = .25*100 + .25*70.784 + .20*100 + .15*82 + .15*100
                  = 25 + 17.696 + 20 + 12.3 + 15 = 89.996
    criticality = .40*100(dependents) + .35*100*C(openssf) + .25*100(downloads)
                = 65 + 35*C = 70.784 -> C = 5.784/35 = 0.16525714285714263

    89.996 rounds to 90.0, the S/S+ cutoff: ``tier_from_score(89.996) ==
    "S"`` but ``tier_from_score(90.0) == "S+"``. A correct engine stores
    ``composite=90.0`` (the rounded value, M12's target) with ``tier="S"``
    (derived from the unrounded 89.996, M10's target) -- both wrong in the
    mutant, in opposite directions, so one bundle kills both mutants."""
    inputs = dict(EligibleFixture.inputs())
    inputs["openssf_criticality"] = _m(0.16525714285714263, n=0)
    bundle = make_bundle(inputs)

    record = score(bundle, EligibleFixture.populations(), EligibleFixture.snapshot())

    assert record.coverage == pytest.approx(1.0)
    assert record.tier_ceiling is None
    assert record.composite == pytest.approx(90.0)
    assert record.tier == "S"


# --------------------------------------------------------------------------- #
# W2-5f (W-1): a stage-0 gate failure and a stage-3 evidence failure must be
# distinguishable from the record alone.
# --------------------------------------------------------------------------- #


def test_gated_and_unrated_records_are_distinguishable() -> None:
    gated_bundle = make_bundle(
        {input_id: Unmeasured(reason="collector_failure") for input_id in constants_v2.PARAM_INPUT_IDS},
        archived=True,
    )
    snapshot = Snapshot(snapshot_id="snap-1", as_of=NOW, gate_evidence=healthy_evidence())
    gated = score(gated_bundle, EligibleFixture.populations(), snapshot)
    assert gated.gate.eligible is False
    assert gated.gate.reason == "archived"
    assert gated.rated is False
    assert gated.unrated_reason is None
    assert gated.tier_ceiling is None

    unrated_inputs: dict[str, Measured | Unmeasured] = {
        input_id: Unmeasured(reason="collector_failure") for input_id in constants_v2.PARAM_INPUT_IDS
    }
    unrated_inputs.update(
        {
            "newcomer_issue_supply": _m(10.0, n=0),
            "newcomer_conversion": _m(0.5, n=5),
            "rr90_return_rate": _m(0.5, n=5),
            "dx": _m(100.0, n=5),
        }
    )
    unrated_bundle = make_bundle(unrated_inputs)
    empty_populations = PopulationSet(
        snapshot_id="snap-1", benchmark_version=constants_v2.BENCHMARK_VERSION,
        constants_hash=constants_v2.CONSTANTS_HASH, populations=(),
    )
    unrated = score(unrated_bundle, empty_populations, snapshot)
    assert unrated.gate.eligible is True
    assert unrated.gate.reason is None
    assert unrated.rated is False
    assert unrated.unrated_reason == "unrated: insufficient_evidence"


# --------------------------------------------------------------------------- #
# W2-5f (W-1): raw_hash is the caller's own value; provenance_hash is kept.
# --------------------------------------------------------------------------- #


def test_score_defaults_raw_hash_to_bundle_provenance_hash_when_omitted() -> None:
    record = score(EligibleFixture.bundle(), EligibleFixture.populations(), EligibleFixture.snapshot())
    assert record.raw_hash == _HASH64
    assert record.provenance_hash == _HASH64


def test_score_accepts_a_caller_supplied_raw_hash_distinct_from_provenance_hash() -> None:
    custom_hash = "c" * 64
    record = score(
        EligibleFixture.bundle(), EligibleFixture.populations(), EligibleFixture.snapshot(), raw_hash=custom_hash
    )
    assert record.raw_hash == custom_hash
    assert record.provenance_hash == _HASH64


# --------------------------------------------------------------------------- #
# W2-5f (W-3): a Measured ECDF input with no frozen population is
# Unmeasured(reason="no_population"), never an escaping LookupError.
# --------------------------------------------------------------------------- #


def test_ecdf_params_missing_population_are_no_population_not_a_crash() -> None:
    """Fail-before: on the base engine, ``populations.ecdf(...)`` is called
    eagerly for every Measured ECDF input regardless of population
    existence, so this raises ``LookupError`` instead of returning (recorded
    verbatim in the handoff). ``ttm_p90_hours`` is Unmeasured here (its own
    D-MISS-1 reason must pass through, and the missing population must never
    even be consulted for it -- the eager-ecdf-fetch guard already proves
    that path); ``dependents_deduped``/``downloads_monthly`` are Measured
    with no population in an empty ``PopulationSet``, so both come back
    ``no_population`` with the real sample size preserved."""
    inputs = dict(EligibleFixture.inputs())
    inputs["ttm_p90_hours"] = Unmeasured(reason="third_party_gap")
    inputs["dependents_deduped"] = _m(999999.0, n=42)
    bundle = make_bundle(inputs)
    empty_populations = PopulationSet(
        snapshot_id="snap-1", benchmark_version=constants_v2.BENCHMARK_VERSION,
        constants_hash=constants_v2.CONSTANTS_HASH, populations=(),
    )

    record = score(bundle, empty_populations, EligibleFixture.snapshot())

    assert record.params["ttm_p90_hours"] == ParamScore(
        value=None,
        score=None,
        measured=False,
        reason="third_party_gap",
        n=0,
        window_start=None,
        window_end=None,
        method=None,
        source=None,
        fetched_at=None,
    )
    assert record.params["dependents_deduped"] == ParamScore(
        value=999999.0,
        score=None,
        measured=False,
        reason="no_population",
        n=42,
        window_start=None,
        window_end=None,
        method=None,
        source="test://fixture",
        fetched_at=_iso(NOW),
    )
    assert record.params["downloads_monthly"] == ParamScore(
        value=999999999.0,
        score=None,
        measured=False,
        reason="no_population",
        n=0,
        window_start=None,
        window_end=None,
        method=None,
        source="test://fixture",
        fetched_at=_iso(NOW),
    )


# --------------------------------------------------------------------------- #
# W2-10 acceptance: ScoreRecord carries its own repo identity (04 section 3).
# --------------------------------------------------------------------------- #


def _bare_score_record_fields() -> dict[str, Any]:
    """Every ``ScoreRecord`` field except ``repo`` -- a minimal gated shape,
    reused by the fail-before/after proof below."""
    return dict(
        snapshot_id="snap-1",
        benchmark_version=constants_v2.BENCHMARK_VERSION,
        constants_hash=constants_v2.CONSTANTS_HASH,
        raw_hash=_HASH64,
        gate=GateResult(eligible=True, reason=None, routed=None, undecided=()),
        params={},
        pillars={},
        composite=None,
        coverage=0.0,
        tier=None,
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


def test_score_record_requires_repo() -> None:
    """Fail-before: on the base ``cvi.core.models``, ``ScoreRecord`` has no
    ``repo`` field at all, so ``ScoreRecord(**_bare_score_record_fields())``
    is accepted outright (recorded verbatim in the handoff). After this
    packet ``repo`` is a required field with no default, so the same call
    (still omitting it) raises ``TypeError`` naming it as missing."""
    with pytest.raises(TypeError, match="repo"):
        ScoreRecord(**_bare_score_record_fields())  # type: ignore[call-arg]

    # Supplying it is the only difference -- proves the rejection is
    # specifically about the missing field, not some other regression.
    record = ScoreRecord(repo=make_repo(), **_bare_score_record_fields())
    assert record.repo == make_repo()


def test_engine_repo_is_bundle_repo_on_every_path() -> None:
    """``record.repo`` equals the scored bundle's ``repo`` on all three
    shapes :func:`score` can return: gated (stage-0 ``eligible=False``),
    unrated (D-EVID-1 insufficient coverage) and rated. A distinct
    ``owner``/``name`` per bundle (rather than reusing ``EligibleFixture``'s
    default ``org/alpha`` for all three) is the mutant guard named by the
    packet: an engine that hard-codes a constant ``RepoIdentity``, or copies
    a *different* bundle's ``repo``, would pass a same-identity check but
    fail this one."""
    snapshot = Snapshot(snapshot_id="snap-1", as_of=NOW, gate_evidence=healthy_evidence())

    gated_bundle = make_bundle(
        {input_id: Unmeasured(reason="collector_failure") for input_id in constants_v2.PARAM_INPUT_IDS},
        owner="gated-owner", name="gated-repo", archived=True,
    )
    gated = score(gated_bundle, _PoisonPopulations(), snapshot)
    assert gated.repo == gated_bundle.repo
    assert gated.repo.owner == "gated-owner"
    assert gated.repo.name == "gated-repo"

    unrated_inputs: dict[str, Measured | Unmeasured] = {
        input_id: Unmeasured(reason="collector_failure") for input_id in constants_v2.PARAM_INPUT_IDS
    }
    unrated_inputs.update(
        {
            "newcomer_issue_supply": _m(10.0, n=0),
            "newcomer_conversion": _m(0.5, n=5),
            "rr90_return_rate": _m(0.5, n=5),
            "dx": _m(100.0, n=5),
        }
    )
    unrated_bundle = make_bundle(unrated_inputs, owner="unrated-owner", name="unrated-repo")
    empty_populations = PopulationSet(
        snapshot_id="snap-1", benchmark_version=constants_v2.BENCHMARK_VERSION,
        constants_hash=constants_v2.CONSTANTS_HASH, populations=(),
    )
    unrated = score(unrated_bundle, empty_populations, snapshot)
    assert unrated.repo == unrated_bundle.repo
    assert unrated.rated is False
    assert unrated.repo.owner == "unrated-owner"
    assert unrated.repo.name == "unrated-repo"

    rated_bundle = make_bundle(EligibleFixture.inputs(), owner="rated-owner", name="rated-repo")
    rated = score(rated_bundle, EligibleFixture.populations(), snapshot)
    assert rated.repo == rated_bundle.repo
    assert rated.rated is True
    assert rated.repo.owner == "rated-owner"
    assert rated.repo.name == "rated-repo"
