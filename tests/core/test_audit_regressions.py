"""Regression tests for the second-wave correctness audit (2026-09-16).

Each test below pins one defect the audit found in the scoring core. They are
kept together, rather than scattered into the per-module suites, because what
makes them worth keeping is not which module they touch but the class of bug
they represent: results that were wrong in ways the existing suites could not
see, because those suites asserted the same arithmetic the production code
used, or exercised only the shapes of data the happy path produces.

Where a fix is already covered by a test in the module's own suite, it is not
duplicated here.
"""
from __future__ import annotations

import datetime

import pytest

from cvi.core import aggregate, constants, gates, matrix, models, tiebreak
from cvi.core.models import (
    BundleValidationError,
    Measured,
    PillarScore,
    PRRef,
    PRWindowEvent,
)

from tests.core.test_gates import (
    NOW,
    healthy_evidence,
    make_bundle,
    make_event,
    recent_decided_external,
)


def _pillar(score: float | None, coverage: float, applicable: bool = True) -> PillarScore:
    return PillarScore(score=score, coverage=coverage, applicable=applicable)


def _measured(value: float, n: int = 10) -> Measured:
    return Measured(
        value=value,
        n=n,
        window_start=None,
        window_end=None,
        method="primary",
        source="raw/oracle.json",
        source_hash="ab" * 32,
        fetched_at="2026-09-12T00:00:00+00:00",
    )


# --------------------------------------------------------------------------- #
# A1. Floating-point deflation in the weighted arithmetic mean.
#
# The ratios are sums of products over floats divided by a sum of weights.
# When D-MISS-5 drops an inapplicable pillar the denominator is no longer 1.0,
# and values that are exact as decimals stop being exact in binary: a coverage
# that is mathematically exactly the evidence floor can land one ULP below it
# and flip the repo to unrated. That is a user-visible verdict decided by
# representation error rather than by evidence.
# --------------------------------------------------------------------------- #


def test_coverage_exactly_at_the_evidence_floor_is_not_pushed_under_it() -> None:
    """The concrete reproducing case, not a hypothetical.

    With ``criticality`` and ``governance`` inapplicable, the denominator is
    0.25 + 0.15 + 0.15 = 0.55. Three pillars each covered exactly 0.60 give
    ``(0.25*0.6 + 0.15*0.6 + 0.15*0.6) / 0.55``, which evaluates to
    0.5999999999999999 in IEEE-754 -- just under the 0.60 floor. The repo is
    fully at the bar on the evidence and must not be denied by the last bit
    of a double.
    """
    floor = constants.EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE
    pillars = {name: _pillar(50.0, floor) for name in constants.PILLAR_NAMES}
    pillars["criticality"] = _pillar(None, 0.0, applicable=False)
    pillars["governance"] = _pillar(None, 0.0, applicable=False)

    coverage = aggregate.composite_coverage(pillars)

    assert coverage >= floor, f"denied at the floor by {floor - coverage!r}"
    assert coverage == floor


def test_coverage_of_a_fully_measured_repo_is_exactly_one() -> None:
    """Every pillar fully measured must give coverage of exactly 1.0, not
    0.9999999999999999 -- the tier-ceiling rules compare against 1.0."""
    pillars = {name: _pillar(50.0, 1.0) for name in constants.PILLAR_NAMES}
    assert aggregate.composite_coverage(pillars) == 1.0


def test_composite_of_equal_pillar_scores_is_that_score() -> None:
    """If every pillar scores 72.0, the weighted mean is 72.0 exactly."""
    pillars = {name: _pillar(72.0, 1.0) for name in constants.PILLAR_NAMES}
    assert aggregate.composite_score_raw(pillars) == 72.0


# --------------------------------------------------------------------------- #
# A2. NaN and infinity must be rejected, not propagated.
#
# A NaN reaching the scorer is silently absorbed: it compares false against
# every threshold, and min(100.0, nan) returns nan while min(100.0, inf)
# returns a perfect 100.0. A broken collector must fail loudly at the contract
# boundary instead of minting a top score.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_measured_value_is_rejected_at_construction(bad: float) -> None:
    with pytest.raises(BundleValidationError):
        _measured(bad)


def test_canonical_json_refuses_to_serialise_nan() -> None:
    """``json.dumps`` emits bare ``NaN`` by default, which is not valid JSON.

    A hash computed over such a document is a hash of something no conforming
    parser will read back, which quietly breaks the reproducibility chain.
    """
    with pytest.raises(ValueError):
        models.canonical_json({"value": float("nan")})


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_a_non_finite_curve_input_raises_rather_than_scoring_100(bad: float) -> None:
    """The second line of defence, at the only place a raw value becomes a
    score: ``min(100.0, inf)`` is 100.0 and ``min(100.0, nan)`` is 100.0, so
    an unguarded clamp turns a broken collector into a perfect score.

    The bad value is installed past ``Measured``'s own validation on purpose
    -- the point of this test is that the curve does not rely on that earlier
    check having run.
    """
    from cvi.core.params import PARAMS, curves

    defn = next(d for d in PARAMS.values() if d.kind == curves.KIND_NUMERIC and d.min_n in (None, 0))
    value = _measured(1.0)
    object.__setattr__(value, "value", bad)
    with pytest.raises(ValueError):
        curves._apply(defn, value)


# --------------------------------------------------------------------------- #
# A3. Matrix axis coverage must weigh each pillar by how measured it is.
#
# The axes previously reported coverage as the share of contributing weight
# that had *any* score at all. A pillar resting on one measured parameter out
# of ten then counted as fully covering its share of the axis.
# --------------------------------------------------------------------------- #


def test_axis_coverage_reflects_partial_pillar_coverage() -> None:
    """Every contributing pillar 10% measured means the axis is 10% covered."""
    pillars = {name: _pillar(50.0, 0.10) for name in constants.PILLAR_NAMES}
    assert matrix.leverage(pillars)[1] == pytest.approx(0.10)
    assert matrix.friction(pillars)[1] == pytest.approx(0.10)


def test_axis_coverage_is_one_when_every_contributing_pillar_is_full() -> None:
    pillars = {name: _pillar(50.0, 1.0) for name in constants.PILLAR_NAMES}
    assert matrix.leverage(pillars)[1] == 1.0
    assert matrix.friction(pillars)[1] == 1.0


def test_a_not_applicable_pillar_leaves_both_sums() -> None:
    """D-MISS-5: an inapplicable pillar is a fact about the world, so it must
    not cap the axis's coverage below 1.0."""
    pillars = {name: _pillar(50.0, 1.0) for name in constants.PILLAR_NAMES}
    pillars[next(iter(constants.LEVERAGE_WEIGHTS))] = _pillar(None, 0.0, applicable=False)
    value, coverage = matrix.leverage(pillars)
    assert coverage == 1.0
    assert value == pytest.approx(50.0)


def test_friction_inverts_the_score_not_the_coverage() -> None:
    """Friction is ``100 - score``; its coverage is still the measured share,
    never ``1 - coverage``."""
    pillars = {name: _pillar(30.0, 0.25) for name in constants.PILLAR_NAMES}
    value, coverage = matrix.friction(pillars)
    assert value == pytest.approx(70.0)
    assert coverage == pytest.approx(0.25)


# --------------------------------------------------------------------------- #
# A4. Tie-break ladder rungs must not be decided by representation error, and
# must not raise on a timestamp that lost its offset.
# --------------------------------------------------------------------------- #


def test_hours_between_normalises_a_naive_timestamp_to_utc() -> None:
    """A collector that dropped the offset must not make the ladder raise."""
    assert tiebreak._hours_between("2026-09-01T00:00:00", "2026-09-01T06:00:00+00:00") == pytest.approx(6.0)


def test_hours_between_agrees_across_equivalent_offsets() -> None:
    """The same instant written in two timezones is the same instant."""
    utc = tiebreak._hours_between("2026-09-01T00:00:00+00:00", "2026-09-01T12:00:00+00:00")
    offset = tiebreak._hours_between("2026-09-01T05:30:00+05:30", "2026-09-01T12:00:00+00:00")
    assert utc == pytest.approx(offset)


# --------------------------------------------------------------------------- #
# A5. Repo keys that differ only in case are the same repo.
#
# GitHub owner/name comparison is case-insensitive. Two bundles for
# ``Owner/Repo`` and ``owner/repo`` are the same repository counted twice: it
# would appear twice in the leaderboard and twice in every population the
# snapshot normalises against, and because the final tie-break level folds
# case, their relative order would be decided by the caller's input sequence.
# --------------------------------------------------------------------------- #


def _keys_for(bundles):
    from cvi.core.pipeline import _validated_keys, repo_key

    evidence = {repo_key(b): healthy_evidence() for b in bundles}
    hashes = {repo_key(b): "cd" * 32 for b in bundles}
    return _validated_keys(bundles, evidence, hashes)


def test_keys_differing_only_in_case_are_rejected_as_duplicates() -> None:
    bundles = [
        make_bundle([], owner="Owner", name="Repo"),
        make_bundle([], owner="owner", name="repo"),
    ]
    with pytest.raises(ValueError) as excinfo:
        _keys_for(bundles)
    message = str(excinfo.value)
    assert "duplicate repo key" in message
    # Both spellings are named, so the operator can find them in the input.
    assert "Owner/Repo" in message and "owner/repo" in message


def test_distinct_repos_are_not_treated_as_duplicates() -> None:
    bundles = [
        make_bundle([], owner="owner", name="alpha"),
        make_bundle([], owner="owner", name="beta"),
    ]
    assert len(_keys_for(bundles)) == 2


# --------------------------------------------------------------------------- #
# A7. Gate correctness.
# --------------------------------------------------------------------------- #


def test_a_repo_that_merges_on_sight_is_not_a_black_hole() -> None:
    """D-GATE-5 asks whether external contributors are ghosted.

    Merging a pull request is the least ambiguous possible answer. A
    maintainer who reads a small diff and merges it the same hour often
    leaves no review artefact behind, and the gate must not read that absence
    as silence -- doing so disqualifies the friendliest repositories in the
    universe as ``black_hole``, which is exactly backwards. This is real
    behaviour, not a hypothetical: in ``tests/fixtures/snapshot_50``,
    boyter/scc merges 25 of its 26 external PRs and was gated by the
    previous rule.
    """
    events = [make_event(100 + i, 30, merged_days_after=1, closed_days_after=1) for i in range(20)]
    result = gates.evaluate_gates(make_bundle(events), healthy_evidence(), NOW)
    assert result.eligible, result.reason


def test_a_repo_that_closes_without_reading_is_still_a_black_hole() -> None:
    """The converse: a bare close with no review is the ghosting this gate
    exists to catch, and is deliberately NOT treated as a response."""
    events = [make_event(100 + i, 30, closed_days_after=1) for i in range(20)]
    result = gates.evaluate_gates(make_bundle(events), healthy_evidence(), NOW)
    assert not result.eligible
    assert result.reason == "black_hole"


def test_a_merge_after_the_deadline_does_not_count_as_a_response() -> None:
    """The response has to arrive inside the 14-day deadline to be one."""
    late = constants.GATE5_RESPONSE_DEADLINE_DAYS + 5
    events = [make_event(100 + i, 90, merged_days_after=late, closed_days_after=late) for i in range(20)]
    result = gates.evaluate_gates(make_bundle(events), healthy_evidence(), NOW)
    assert not result.eligible
    assert result.reason == "black_hole"


def test_gate_6_ignores_a_decision_older_than_its_own_horizon() -> None:
    """D-GATE-6 asks for a decision in the trailing 365 days.

    The PR window is not itself bounded to 365 days in practice -- the real
    fixture carries events opened in 2019 -- so the horizon has to be applied
    here rather than assumed from the caller's window.
    """
    stale = make_event(1, constants.GATE6_DECISION_WINDOW_DAYS + 400, merged_days_after=1, closed_days_after=1)
    result = gates.evaluate_gates(make_bundle([stale]), healthy_evidence(), NOW)
    assert not result.eligible
    assert result.reason == gates.REASON_NO_EXTERNAL_WORKFLOW


def test_gate_6_accepts_a_decision_inside_its_horizon() -> None:
    result = gates.evaluate_gates(make_bundle([recent_decided_external()]), healthy_evidence(), NOW)
    assert result.eligible, result.reason


@pytest.mark.parametrize(
    "expression",
    ["MIT", "MIT OR Apache-2.0", "Apache-2.0 WITH LLVM-exception", "(MIT OR Apache-2.0) AND BSD-3-Clause"],
)
def test_a_compound_spdx_expression_of_approved_licences_passes_gate_3(expression: str) -> None:
    """Dual-licensing is the norm in the Rust ecosystem; reading
    ``"MIT OR Apache-2.0"`` as one unknown identifier gated most of it."""
    bundle = make_bundle([recent_decided_external()], license_spdx=expression)
    result = gates.evaluate_gates(bundle, healthy_evidence(), NOW)
    assert result.eligible, f"{expression}: {result.reason}"


# --------------------------------------------------------------------------- #
# A8. Timestamp fields must hold timestamps.
#
# These fields are parsed with fromisoformat everywhere downstream. Accepting
# any non-empty string at the contract boundary moves the failure from bundle
# validation -- where it names the offending field -- into the middle of gate
# evaluation, as an unhandled ValueError.
# --------------------------------------------------------------------------- #


def test_a_malformed_event_timestamp_is_rejected_at_validation() -> None:
    with pytest.raises(BundleValidationError):
        PRWindowEvent(
            pr_number=1,
            created_at="2026-09-01T00:00:00+00:00",
            closed_at="not a timestamp",
            merged_at=None,
            author_login="contributor",
            author_association="NONE",
            is_external=True,
            first_human_review_at=None,
        )


def test_a_malformed_pr_ref_timestamp_is_rejected_at_validation() -> None:
    with pytest.raises(BundleValidationError):
        PRRef(number=1, created_at="2026-09-01T00:00:00+00:00", merged_at="yesterday")


def test_gates_read_a_naive_timestamp_as_utc() -> None:
    """Comparing a naive datetime against the aware ``as_of`` raises
    TypeError, taking the whole evaluation down."""
    parsed = gates._parse_iso("2026-09-01T00:00:00")
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == datetime.timedelta(0)
    assert (NOW - parsed).days > 0


def test_gates_preserve_an_explicit_offset() -> None:
    assert gates._parse_iso("2026-09-01T05:30:00+05:30") == gates._parse_iso("2026-09-01T00:00:00Z")
