"""Acceptance tests for the D-NORM-1 mid-rank ECDF (02 section 6).

``porchlamp.core.normalize`` implements ``R(x) = 100 * (#{x_k < x} + 0.5 * #{x_k = x})
/ N``. The spec's ``<=`` form (v1 ``porchlamp.normalize.compute_ecosystem_percentile``)
scores every member of an all-tied population at 100.0, whereas the
mid-rank form scores 50.0 and is the standard tie treatment. The oracles below
hand-compute every expected value from the D-NORM-1 formula.
"""
from __future__ import annotations

import pytest

from porchlamp.core import normalize


def test_all_tied_population_scores_50():
    population = [42.0, 42.0, 42.0, 42.0, 42.0, 42.0, 42.0]
    for value in population:
        assert normalize.mid_rank_ecdf_score(value, population) == 50.0


def test_mixed_ties_oracle():
    # D-NORM-1 by hand, N = 4:
    #   x=1: 100 * (0 + 0.5*1) / 4 = 12.5
    #   x=2: 100 * (1 + 0.5*2) / 4 = 50.0
    #   x=3: 100 * (3 + 0.5*1) / 4 = 87.5
    population = [1.0, 2.0, 2.0, 3.0]
    assert normalize.mid_rank_ecdf_score(1.0, population) == 12.5
    assert normalize.mid_rank_ecdf_score(2.0, population) == 50.0
    assert normalize.mid_rank_ecdf_score(3.0, population) == 87.5


def test_outside_population_range_clamps_to_0_and_100():
    population = [1.0, 2.0, 2.0, 3.0]
    assert normalize.mid_rank_ecdf_score(0.5, population) == 0.0
    assert normalize.mid_rank_ecdf_score(10.0, population) == 100.0


def test_all_distinct_members_score_by_mid_rank():
    population = [10.0, 20.0, 30.0, 40.0, 50.0]
    for index, value in enumerate(population):
        expected = 100.0 * (index + 0.5) / 5.0
        assert normalize.mid_rank_ecdf_score(value, population) == expected


def test_ecdf_closure_matches_pointwise_score():
    population = [3.0, 1.0, 2.0, 2.0]
    ecdf = normalize.mid_rank_ecdf(population)
    for probe in (-1.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
        assert ecdf(probe) == normalize.mid_rank_ecdf_score(probe, population)


def test_empty_population_raises():
    with pytest.raises(ValueError):
        normalize.mid_rank_ecdf_score(1.0, [])
    with pytest.raises(ValueError):
        normalize.mid_rank_ecdf([])


def test_rejects_non_numeric_values():
    with pytest.raises(ValueError):
        normalize.mid_rank_ecdf_score(True, [1.0, 2.0])
    with pytest.raises(ValueError):
        normalize.mid_rank_ecdf_score(1.0, [1.0, "x"])
    with pytest.raises(ValueError):
        normalize.mid_rank_ecdf([None, 1.0])


def test_integer_values_coerce_to_float():
    assert normalize.mid_rank_ecdf_score(2, [1, 2, 2, 3]) == 50.0


def test_rejects_non_finite_query_value():
    # Fail-before: mid_rank_ecdf_score(nan, [1.0, 2.0]) returned exactly 50.0
    # (the population median) before this check existed -- see the module
    # docstring and D-MISS-1 (docs/v2/02-RANKING-DESIGN.md).
    population = [1.0, 2.0, 3.0]
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="value must be finite"):
            normalize.mid_rank_ecdf_score(bad, population)
    ecdf = normalize.mid_rank_ecdf(population)
    with pytest.raises(ValueError, match="value must be finite"):
        ecdf(float("nan"))


def test_rejects_non_finite_population_values():
    with pytest.raises(ValueError, match="population value must be finite"):
        normalize.mid_rank_ecdf_score(1.0, [1.0, float("nan"), 3.0])
    with pytest.raises(ValueError, match="population value must be finite"):
        normalize.mid_rank_ecdf([1.0, float("inf")])


def test_finite_values_are_unchanged_by_the_finiteness_check():
    # Over-correction guard for the non-finite rejection: every finite input
    # from the mixed-ties oracle must keep scoring exactly as before.
    population = [1.0, 2.0, 2.0, 3.0]
    assert normalize.mid_rank_ecdf_score(1.0, population) == 12.5
    assert normalize.mid_rank_ecdf_score(2.0, population) == 50.0
    assert normalize.mid_rank_ecdf_score(3.0, population) == 87.5
    ecdf = normalize.mid_rank_ecdf(population)
    assert ecdf(1.0) == 12.5
