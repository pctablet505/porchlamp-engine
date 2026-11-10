"""Acceptance tests: pin the RATED path of ``porchlamp.core.engine.score``.

Tests cover rated status, tier ceilings, and axis coverages on rated fixtures.
This module is self-contained with its own bundle-building helpers.

Two fixtures:

- ``FullyMeasured``: every one of the 18 inputs measured, chosen so
  coverage lands exactly at 1.0 (>= 0.95 -> no ceiling).
- ``GovernanceUnmeasured``: identical except all three governance inputs
  (``legal_ip``, ``elephant_factor``, ``promotion_ladder``) are
  ``Unmeasured`` -- the governance pillar's own score is then ``None``
  (0/3 of its within-pillar weight measured), so ``composite_coverage``
  drops by governance's PILLAR_WEIGHT (0.20) to exactly ``0.80``
  (0.80 <= C < 0.95 -> ceiling "A"), and the two coordinate axes lose
  *different* shares of their own weight (D-MAT-1: governance carries 0.20
  of ``LEVERAGE_WEIGHTS`` but 0.30 of ``FRICTION_WEIGHTS``), so
  ``leverage_coverage`` (0.80) and ``friction_coverage`` (0.70) are
  non-zero and distinct from each other -- a coverage-swap mutant dies on
  this fixture alone.
"""
from __future__ import annotations

import datetime
from typing import Any

import pytest

from porchlamp.core import aggregate, constants
from porchlamp.core.engine import Snapshot, score
from porchlamp.core.gates import GateEvidence
from porchlamp.core.models import (
    Cohorts,
    FeatureBundle,
    Measured,
    PRWindow,
    PRWindowEvent,
    RepoIdentity,
    Unmeasured,
)
from porchlamp.snapshot.populations import Population, PopulationSet

NOW = datetime.datetime(2026, 9, 12, 0, 0, tzinfo=datetime.timezone.utc)
_HASH64 = "e" * 64


def _iso(value: datetime.datetime) -> str:
    return value.isoformat()


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


def _healthy_evidence() -> GateEvidence:
    return GateEvidence(
        manifest_found=True,
        is_curated_anchor=False,
        license_file_present=None,
        fork_parent_in_universe=None,
        vitality_commits_180d=5,
        off_platform_marker=None,
    )


def _qualifying_events() -> tuple[PRWindowEvent, ...]:
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


def _make_bundle(inputs: dict[str, Measured | Unmeasured]) -> FeatureBundle:
    bundle = FeatureBundle(
        schema_version=constants.SCHEMA_VERSION,
        repo=RepoIdentity(
            owner="org",
            name="alpha",
            ecosystem="pypi",
            archived=False,
            mirror_url=None,
            fork=False,
            license_spdx="MIT",
            default_branch="main",
        ),
        inputs=inputs,
        pr_window=PRWindow(
            start=_iso(NOW - datetime.timedelta(days=180)),
            end=_iso(NOW),
            truncated=False,
            events=_qualifying_events(),
        ),
        cohorts=Cohorts(decision_pr_numbers=(), newcomer_authors=()),
        provenance_hash=_HASH64,
    )
    bundle.validate()
    return bundle


def _base_inputs() -> dict[str, Measured]:
    """The same clean-decimal inputs as ``test_engine_v2.EligibleFixture``
    (every pillar 100.0 except rigor at 82.0) -- see that module for the
    arithmetic. Duplicated here deliberately (see module docstring)."""
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


def _populations() -> PopulationSet:
    return PopulationSet(
        snapshot_id="snap-1",
        benchmark_version=constants.BENCHMARK_VERSION,
        constants_hash=constants.CONSTANTS_HASH,
        populations=(
            Population("pypi", "ttm_p90_hours", 4, False, {"a": 5.0, "b": 10.0, "c": 20.0, "d": 30.0}),
            Population("pypi", "dependents_deduped", 4, False, {"a": 0.0, "b": 1.0, "c": 2.0, "d": 3.0}),
            Population("pypi", "downloads_monthly", 3, False, {"a": 0.0, "b": 1.0, "c": 2.0}),
        ),
    )


def _snapshot() -> Snapshot:
    return Snapshot(snapshot_id="snap-1", as_of=NOW, gate_evidence=_healthy_evidence())


class FullyMeasured:
    """coverage C = 1.0 (>= 0.90 -> no ceiling)."""

    @classmethod
    def bundle(cls) -> FeatureBundle:
        return _make_bundle(_base_inputs())


class SingleMissingParam:
    """RFC-0024 (D-MISS-3): only ``dr90_decision_rate`` unmeasured (weight .25
    of responsiveness = .0625 of the composite) -> coverage C = 0.9375
    (>= 0.90 -> no ceiling)."""

    @classmethod
    def bundle(cls) -> FeatureBundle:
        inputs = dict(_base_inputs())
        inputs["dr90_decision_rate"] = Unmeasured(reason="insufficient_n")
        return _make_bundle(inputs)


class GovernanceUnmeasured:
    """All three governance inputs Unmeasured -> governance pillar score is
    None -> coverage C = 1.0 - PILLAR_WEIGHTS["governance"] = 0.80 (ceiling
    "A"); leverage_coverage = 0.80, friction_coverage = 0.70 (see module
    docstring)."""

    @classmethod
    def bundle(cls) -> FeatureBundle:
        inputs = dict(_base_inputs())
        inputs["legal_ip"] = Unmeasured(reason="collector_failure")
        inputs["elephant_factor"] = Unmeasured(reason="collector_failure")
        inputs["promotion_ladder"] = Unmeasured(reason="collector_failure")
        return _make_bundle(inputs)


# --------------------------------------------------------------------------- #
# The pins (fail on any of the R4/R5/R6-shaped mutants below).
# --------------------------------------------------------------------------- #


def test_rated_path_no_ceiling_case() -> None:
    """C >= 0.90: rated, no ceiling, tier_ceiling matches
    ``aggregate.coverage_status`` exactly (not re-derived by the test)."""
    record = score(FullyMeasured.bundle(), _populations(), _snapshot())

    assert record.coverage == pytest.approx(1.0)
    expected_rated, expected_ceiling = aggregate.coverage_status(record.coverage, record.pillars)
    assert expected_rated is True
    assert expected_ceiling is None

    assert record.rated is True
    assert record.unrated_reason is None
    assert record.tier_ceiling == expected_ceiling
    assert record.tier_ceiling is None

    assert record.leverage_coverage == pytest.approx(1.0)
    assert record.friction_coverage == pytest.approx(1.0)


def test_rated_path_single_missing_parameter_has_no_ceiling() -> None:
    """RFC-0024 (D-MISS-3): a single unmeasured parameter (dr90_decision_rate,
    global weight 0.0625 -> C = 0.9375 >= 0.90) leaves ``tier_ceiling`` as
    ``None`` and does not cap an S+ composite at Tier A."""
    record = score(SingleMissingParam.bundle(), _populations(), _snapshot())

    assert record.coverage == pytest.approx(0.9375)
    assert record.rated is True
    assert record.tier_ceiling is None
    assert record.tier == "S+"


def test_rated_path_ceiling_a_case_with_distinct_axis_coverages() -> None:
    """0.80 <= C < 0.90: rated, ceiling "A", and the two axis coverages are
    non-zero and different from each other (a leverage/friction-coverage
    swap mutant dies here)."""
    record = score(GovernanceUnmeasured.bundle(), _populations(), _snapshot())

    assert record.coverage == pytest.approx(0.80)
    expected_rated, expected_ceiling = aggregate.coverage_status(record.coverage, record.pillars)
    assert expected_rated is True
    assert expected_ceiling == "A"

    assert record.rated is True
    assert record.unrated_reason is None
    assert record.tier_ceiling == expected_ceiling
    assert record.tier_ceiling == "A"

    # The axis coverages equal what porchlamp.core.matrix computes directly from
    # the same pillars (never re-derived independently by this test).
    from porchlamp.core import matrix

    _, expected_leverage_coverage = matrix.leverage(record.pillars)
    _, expected_friction_coverage = matrix.friction(record.pillars)
    assert record.leverage_coverage == pytest.approx(expected_leverage_coverage)
    assert record.friction_coverage == pytest.approx(expected_friction_coverage)

    assert record.leverage_coverage == pytest.approx(0.80)
    assert record.friction_coverage == pytest.approx(0.70)
    assert record.leverage_coverage != pytest.approx(record.friction_coverage)
    assert record.leverage_coverage > 0.0
    assert record.friction_coverage > 0.0


# --------------------------------------------------------------------------- #
# D-GATE-6 amendment: a project that hands review off is not a black hole      #
# --------------------------------------------------------------------------- #


def _black_hole_bundle() -> FeatureBundle:
    """A ghost rate of 0.99 over 300 external PRs, everything else as ``FullyMeasured``."""
    inputs = dict(_base_inputs())
    inputs["gr14_ghost_rate"] = _m(0.99, n=300)
    return _make_bundle(inputs)


def _ranking_black_holes(review_handoff: bool | None) -> Snapshot:
    import dataclasses

    return Snapshot(
        snapshot_id="snap-1", as_of=NOW, rank_black_holes=True,
        gate_evidence=dataclasses.replace(_healthy_evidence(), review_handoff=review_handoff))


def test_handoff_repository_is_unrated_off_platform_and_unflagged() -> None:
    record = score(_black_hole_bundle(), _populations(), _ranking_black_holes(True))

    assert record.gate.eligible is True
    assert record.gate.routed == "off_platform"
    assert record.rated is False
    assert record.unrated_reason == "unrated: off_platform_routed"
    assert record.rank is None
    assert "black_hole" not in record.suspicion


def test_a_repository_that_does_not_hand_off_keeps_the_black_hole_flag() -> None:
    """Over-correction guard: the same bundle without the hand-off is ranked and flagged."""
    for review_handoff in (False, None):
        record = score(_black_hole_bundle(), _populations(), _ranking_black_holes(review_handoff))

        assert record.gate.eligible is True, review_handoff
        assert record.gate.routed is None, review_handoff
        assert record.rated is True, review_handoff
        assert "black_hole" in record.suspicion, review_handoff


# --------------------------------------------------------------------------- #
# RFC-0024 (D-EVID-1): no rating without a measured responsiveness parameter   #
# --------------------------------------------------------------------------- #


def _quiet_unanswered_bundle(*, merges_measured: bool = False) -> FeatureBundle:
    """Every other pillar as ``FullyMeasured``; responsiveness only what a
    quiet repository that answers nobody leaves: 5 outside PRs, all 5 without
    a response within 14 days (the ghost rate is measured at 1.0 over n=5,
    under its minimum n of 10, so it scores ``insufficient_n`` and D-GATE-5 is
    UNDECIDED), and the other four parameters ``insufficient_n``. Composite
    coverage is 0.75, over the 0.60 floor. ``merges_measured=True`` adds one
    measured parameter, TTM_p90 over 5 merged PRs (.10 of the pillar: C =
    0.775)."""
    inputs: dict[str, Measured | Unmeasured] = dict(_base_inputs())
    for input_id in ("ttfhr_hours", "dr90_decision_rate", "mr_first_rate", "ttm_p90_hours"):
        inputs[input_id] = Unmeasured(reason="insufficient_n")
    inputs["gr14_ghost_rate"] = _m(1.0, n=5)
    if merges_measured:
        inputs["ttm_p90_hours"] = _m(1.0, n=5)
    return _make_bundle(inputs)


def test_quiet_repository_that_answers_nobody_is_not_ranked_on_its_other_pillars() -> None:
    """Before RFC-0024 this repository was rated (C = 0.75, ceiling B) and
    ranked on criticality, governance, rigor and accessibility alone: D-GATE-5
    cannot judge 5 PRs, and the composite renormalised the empty
    responsiveness pillar away. 64 rated repositories on the 2026-10-21 data
    had every recorded outside PR unanswered this way."""
    record = score(_quiet_unanswered_bundle(), _populations(), _snapshot())

    assert record.gate.eligible is True
    assert "D-GATE-5" in record.gate.undecided
    assert record.params["gr14_ghost_rate"].reason == "insufficient_n"
    assert record.pillars["responsiveness"].score is None
    assert record.coverage == pytest.approx(0.75)
    assert record.rated is False
    assert record.unrated_reason == "unrated: insufficient_evidence"
    assert record.composite is None
    assert record.tier is None
    assert record.tier_ceiling is None


def test_one_measured_responsiveness_parameter_rates_it_with_the_unchanged_ceiling() -> None:
    """Over-correction guard: the pillar condition asks for evidence, not a
    good score -- one measured parameter (TTM_p90) rates the same repository,
    and the D-MISS-3 bands it falls in are unchanged (0.775 -> ceiling B)."""
    record = score(_quiet_unanswered_bundle(merges_measured=True), _populations(), _snapshot())

    assert record.pillars["responsiveness"].score is not None
    assert record.pillars["responsiveness"].coverage == pytest.approx(0.10)
    assert record.coverage == pytest.approx(0.775)
    assert record.rated is True
    assert record.unrated_reason is None
    assert record.tier_ceiling == "B"
    assert record.composite is not None
