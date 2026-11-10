"""Direct tests for Phase 2 ECDF normalization (spec 10.2).

``porchlamp/normalize.py`` had no dedicated test file (2026-09-12 review gap): the
ECDF was only exercised indirectly through pillar tests, so the two
load-bearing details — the ``<=`` (not ``<``) comparison and the empty-
population 50.0 fallback — were not locked. These tests pin them.
"""

from __future__ import annotations

import pytest

from porchlamp.normalize import compute_ecosystem_percentile


def test_empty_population_neutral_median():
    # FAIL-BEFORE candidate: an empty population must return the neutral
    # median 50.0, not 0.0 (which would read as "worst").
    assert compute_ecosystem_percentile(42.0, []) == 50.0


def test_inclusive_le_comparison():
    # R_pct counts values <= target (INCLUSIVE). One value equal to the target
    # in a single-element population ranks at 100, not 0.
    assert compute_ecosystem_percentile(5.0, [5.0]) == 100.0


def test_below_all_is_zero():
    assert compute_ecosystem_percentile(1.0, [2.0, 3.0, 4.0]) == 0.0


def test_above_all_is_hundred():
    assert compute_ecosystem_percentile(9.0, [2.0, 3.0, 4.0]) == 100.0


def test_mid_rank_exact():
    # 2 of 4 values (2,3) are <= 3 -> 50.0.
    assert compute_ecosystem_percentile(3.0, [2.0, 3.0, 4.0, 5.0]) == 50.0


def test_third_of_three_rounds_to_two_dp():
    # 1 of 3 <= 2 -> 33.33 (round-half-even to 2 dp).
    assert compute_ecosystem_percentile(2.0, [2.0, 5.0, 9.0]) == pytest.approx(
        33.33
    )


def test_generator_population_consumed_once():
    # The population is an Iterable; it must be materialized once, not
    # iterated twice (a generator would be exhausted on the second pass).
    pop = (x for x in [1.0, 2.0, 3.0])
    assert compute_ecosystem_percentile(2.0, pop) == pytest.approx(66.67)
