"""CVI v2 normalization (stage 4, 02 section 6, D-NORM-1).

``mid_rank_ecdf_score`` is the D-NORM-1 mid-rank ECDF
``R(x) = 100 * (#{x_k < x} + 0.5 * #{x_k = x}) / N`` on the 0-100 scale;
``mid_rank_ecdf`` pre-sorts a population so the frozen populations of
D-NORM-2 (built by ``cvi.snapshot.populations``) can be queried in O(log N)
per value. Mid-rank is the tie treatment: the spec's ``<=`` form scores
every member of an all-tied population 100, the mid-rank form scores 50.
Pure math: no I/O, no clock, stdlib only (04 section 3 core rule).

Every numeric input (a query value or a population member) must be finite:
NaN and +-infinity are rejected with ``ValueError`` naming the offending
value, not silently scored. ``docs/v2/02-RANKING-DESIGN.md`` D-MISS-1 makes
an unmeasurable input ``Unmeasured``, never a neutral score; converting an
absence to ``Unmeasured`` is the caller's job, so this module refuses
non-finite input rather than guessing a score for it.
"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from typing import Callable, Sequence


def _numeric(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what} must be a number, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{what} must be finite, got {value!r}")
    return number


def _checked_values(population: Sequence[float]) -> list[float]:
    values = [_numeric(entry, "population value") for entry in population]
    if not values:
        raise ValueError("population must be non-empty")
    return values


def _mid_rank(value: float, ordered: Sequence[float]) -> float:
    below = bisect_left(ordered, value)
    tied = bisect_right(ordered, value) - below
    return 100.0 * (below + 0.5 * tied) / len(ordered)


def mid_rank_ecdf_score(value: float, population: Sequence[float]) -> float:
    """Score ``value`` against ``population`` with the D-NORM-1 mid-rank ECDF.

    ``R(x) = 100 * (#{x_k < x} + 0.5 * #{x_k = x}) / N`` on the 0-100 scale:
    ties get half credit, so an all-tied population scores 50.0 for every
    member, below-minimum values score 0.0 and above-maximum 100.0.
    Raises ``ValueError`` for an empty population, a non-numeric value, or
    a non-finite (NaN / infinity) ``value`` or population member."""
    return _mid_rank(_numeric(value, "value"), sorted(_checked_values(population)))


def mid_rank_ecdf(population: Sequence[float]) -> Callable[[float], float]:
    """The D-NORM-1 mid-rank ECDF of ``population`` as a callable on the 0-100 scale.

    The population is sorted once up front, so each query is O(log N); this
    is the callback shape the ECDF-normalised curves (1.5, 2.1, 2.3,
    D-NORM-3) take. Raises ``ValueError`` for an empty population, a
    non-numeric value, or a non-finite (NaN / infinity) population member
    at build time, or a non-finite query value when the returned callable
    is invoked."""
    ordered = sorted(_checked_values(population))

    def ecdf(value: float) -> float:
        return _mid_rank(_numeric(value, "value"), ordered)

    return ecdf
