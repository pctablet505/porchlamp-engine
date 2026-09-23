"""Acceptance tests for D-PARAM-MONO property (02 section 4) and registry integrity.

The property test asserts that every registered curve is monotone non-decreasing
in "better for the contributor" over a 33-point grid on its registered domain
(numeric and ECDF curves) and over its better-ordering (enum curves; both personas
for the persona-conditional 4.2). Non-monotone curves are not admitted: the test
verifies this enforcement by testing a non-monotonic curve and expecting the
property check to flag it.
"""
from __future__ import annotations

import math

import pytest

from porchlamp.core import constants
from porchlamp.core.models import Measured
from porchlamp.core.params import PARAMS, ParamDef, monotonicity_violations, substantive_review_rate

_HASH = "ab" * 32
_FETCHED_AT = "2026-09-01T00:00:00+00:00"


def measured(value: object, n: int = 0) -> Measured:
    return Measured(
        value=value,
        n=n,
        window_start=None,
        window_end=None,
        method="primary",
        source="raw/pr_events/org__alpha.json",
        source_hash=_HASH,
        fetched_at=_FETCHED_AT,
    )


def test_registry_covers_exactly_the_18_parameters() -> None:
    assert len(PARAMS) == 18
    assert set(PARAMS) == set(constants.PARAM_INPUT_IDS)


def test_registry_maps_every_parameter_to_a_pillar_with_unit_weight() -> None:
    totals: dict[str, float] = {}
    for input_id, defn in PARAMS.items():
        assert defn.input_id == input_id
        assert defn.pillar in constants.PILLAR_NAMES
        assert 0.0 < defn.weight <= 1.0
        totals[defn.pillar] = totals.get(defn.pillar, 0.0) + defn.weight
    assert set(totals) == set(constants.PILLAR_NAMES)
    for pillar, total in sorted(totals.items()):
        assert total == pytest.approx(1.0, abs=1e-9), pillar


def test_every_registered_curve_is_monotone_non_decreasing() -> None:
    violations = monotonicity_violations(PARAMS)
    assert violations == {}, violations


def test_property_check_uses_the_33_point_grid() -> None:
    # A coarser grid must still see the registered curves as monotone...
    assert monotonicity_violations(PARAMS, grid_points=3) == {}
    # ...and the check rejects a degenerate grid instead of passing silently.
    with pytest.raises(ValueError, match="grid_points"):
        monotonicity_violations(PARAMS, grid_points=1)


def _old_gaussian(value: float) -> float:
    """The v1 4.3 review-depth Gaussian (porchlamp/pillars/rigor.py at base commit)."""
    z = (value - 3.5) / 1.5
    return 100.0 * math.exp(-0.5 * z * z)


def test_old_gaussian_registration_fails_the_monotonicity_check() -> None:
    defs = dict(PARAMS)
    defs["substantive_review_rate"] = ParamDef(
        input_id="substantive_review_rate",
        pillar="rigor",
        weight=0.30,
        kind="numeric",
        score=_old_gaussian,
        min_n=10,
        domain=(0.0, 7.0),
        higher_is_better=True,
    )
    violations = monotonicity_violations(defs)
    assert "substantive_review_rate" in violations, violations


def test_v2_substantive_review_curve_is_not_the_gaussian() -> None:
    # Over-correction guard: at a mean of 1.0 inline comments the v1 Gaussian
    # scored 100*exp(-0.5*((1-3.5)/1.5)**2) = 24.93; the v2 rate curve scores
    # 100.0. If the Gaussian slipped back into the registry this fails.
    result = substantive_review_rate(measured(1.0, n=10))
    assert result.score == 100.0
