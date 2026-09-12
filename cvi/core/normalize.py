"""CVI v2 normalization (stage 4, 02 section 6, D-NORM-1).

``mid_rank_ecdf_score`` is the D-NORM-1 mid-rank ECDF
``R(x) = 100 * (#{x_k < x} + 0.5 * #{x_k = x}) / N`` on the 0-100 scale;
``mid_rank_ecdf`` pre-sorts a population so the frozen populations of
D-NORM-2 (built by ``cvi.snapshot.populations``) can be queried in O(log N)
per value. Mid-rank is the tie treatment: the spec's ``<=`` form scores
every member of an all-tied population 100 (finding F3), the mid-rank form
scores 50. Pure math: no I/O, no clock, stdlib only (04 section 3 core rule).
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import Callable, Sequence


def _numeric(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what} must be a number, got {type(value).__name__}")
    return float(value)


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
    Raises ``ValueError`` for an empty population or non-numeric values."""
    return _mid_rank(_numeric(value, "value"), sorted(_checked_values(population)))


def mid_rank_ecdf(population: Sequence[float]) -> Callable[[float], float]:
    """The D-NORM-1 mid-rank ECDF of ``population`` as a callable on the 0-100 scale.

    The population is sorted once up front, so each query is O(log N); this
    is the callback shape the W2-3 ECDF-normalised curves (1.5, 2.1, 2.3,
    D-NORM-3) take. Raises ``ValueError`` for an empty population or
    non-numeric values."""
    ordered = sorted(_checked_values(population))

    def ecdf(value: float) -> float:
        return _mid_rank(_numeric(value, "value"), ordered)

    return ecdf
