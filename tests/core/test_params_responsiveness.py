"""W2-3 acceptance: pillar 1 — Responsiveness curves (02 section 4).

Fail-before (R-1): on the base commit ``cvi.core.params`` does not exist, so
every test in this module fails at import. The curves are the absolute
calibrations of 02 section 4 (1.1-1.5); below the minimum-n sample the
parameter is ``insufficient_n`` with the actual ``n`` reported.
"""
from __future__ import annotations

from typing import Callable

from cvi.core import constants_v2
from cvi.core.models import Measured, ParamScore, Unmeasured
from cvi.core.params import dr90, gr14, mr_first, ttfhr, ttm_p90

_HASH = "ab" * 32
_FETCHED_AT = "2026-09-01T00:00:00+00:00"


def measured(value: object, n: int, method: str = "primary") -> Measured:
    return Measured(
        value=value,
        n=n,
        window_start="2026-03-04T00:00:00+00:00",
        window_end="2026-09-01T00:00:00+00:00",
        method=method,
        source="raw/pr_events/org__alpha.json",
        source_hash=_HASH,
        fetched_at=_FETCHED_AT,
    )


def unmeasured(reason: str = "third_party_gap") -> Unmeasured:
    return Unmeasured(reason=reason, detail="demo")


def _scored(value: object, score: float, *, n: int) -> ParamScore:
    """The ``ParamScore`` a param function returns for ``measured(value, n)``."""
    return ParamScore(
        value=value,
        score=score,
        measured=True,
        reason=None,
        n=n,
        window_start="2026-03-04T00:00:00+00:00",
        window_end="2026-09-01T00:00:00+00:00",
        method="primary",
        source="raw/pr_events/org__alpha.json",
        fetched_at=_FETCHED_AT,
    )


def _insufficient_n(value: object, n: int) -> ParamScore:
    """The ``ParamScore`` a param function returns for ``measured(value, n)`` below the minimum sample."""
    return ParamScore(
        value=value,
        score=None,
        measured=False,
        reason="insufficient_n",
        n=n,
        window_start="2026-03-04T00:00:00+00:00",
        window_end="2026-09-01T00:00:00+00:00",
        method=None,
        source="raw/pr_events/org__alpha.json",
        fetched_at=_FETCHED_AT,
    )


def _gap(reason: str) -> ParamScore:
    """The ``ParamScore`` a param function returns for an ``Unmeasured`` input."""
    return ParamScore(
        value=None,
        score=None,
        measured=False,
        reason=reason,
        n=0,
        window_start=None,
        window_end=None,
        method=None,
        source=None,
        fetched_at=None,
    )


def _mid_rank_ecdf(sample: list[float]) -> Callable[[float], float]:
    """D-NORM-1 mid-rank ECDF of ``sample``, on the 0-100 scale."""
    size = len(sample)

    def ecdf(x: float) -> float:
        below = sum(1 for s in sample if s < x)
        tied = sum(1 for s in sample if s == x)
        return 100.0 * (below + 0.5 * tied) / size

    return ecdf


# 1.1 TTFHR: 100 if t <= 12 h, else max(0, 100 - 25*log2(t/12))


def test_ttfhr_oracle() -> None:
    cases = [
        (0.5, 100.0),   # instant response
        (12.0, 100.0),  # knee boundary: exactly 12 h still full marks
        (24.0, 75.0),   # one doubling past the knee
        (48.0, 50.0),
        (96.0, 25.0),
        (192.0, 0.0),   # floor boundary: 100 - 25*4
        (4000.0, 0.0),
    ]
    for hours, expected in cases:
        result = ttfhr(measured(hours, n=12))
        assert result == _scored(hours, expected, n=12)


def test_ttfhr_insufficient_n_one_below_minimum() -> None:
    result = ttfhr(measured(6.0, n=9))
    assert result == _insufficient_n(6.0, 9)


def test_ttfhr_measured_at_exact_minimum() -> None:
    result = ttfhr(measured(12.0, n=10))
    assert result == _scored(12.0, 100.0, n=10)


def test_ttfhr_unmeasured_passes_through() -> None:
    for reason in constants_v2.UNMEASURED_REASONS:
        result = ttfhr(unmeasured(reason))
        assert result == _gap(reason)


# 1.2 DR_90: min(100, 125*r)


def test_dr90_oracle() -> None:
    cases = [
        (0.0, 0.0),      # nothing decided
        (0.2, 25.0),
        (0.6, 75.0),
        (0.8, 100.0),    # cap boundary: 125*0.8
        (1.0, 100.0),
    ]
    for rate, expected in cases:
        result = dr90(measured(rate, n=20))
        assert result == _scored(rate, expected, n=20)


def test_dr90_insufficient_n_one_below_minimum() -> None:
    result = dr90(measured(0.9, n=9))
    assert result == _insufficient_n(0.9, 9)


# 1.3 MR_first: min(100, 125*r), minimum n 5


def test_mr_first_oracle() -> None:
    cases = [
        (0.0, 0.0),
        (0.4, 50.0),
        (0.8, 100.0),    # cap boundary
        (1.0, 100.0),
    ]
    for rate, expected in cases:
        result = mr_first(measured(rate, n=7))
        assert result == _scored(rate, expected, n=7)


def test_mr_first_insufficient_n_one_below_minimum() -> None:
    result = mr_first(measured(1.0, n=4))
    assert result == _insufficient_n(1.0, 4)


# 1.4 GR_14: max(0, 100 - 200*g)


def test_gr14_oracle() -> None:
    cases = [
        (0.0, 100.0),    # no ghosts
        (0.125, 75.0),
        (0.25, 50.0),
        (0.5, 0.0),      # floor boundary: half the cohort ghosted
        (0.9, 0.0),
    ]
    for ghost_rate, expected in cases:
        result = gr14(measured(ghost_rate, n=30))
        assert result == _scored(ghost_rate, expected, n=30)


def test_gr14_insufficient_n_one_below_minimum() -> None:
    result = gr14(measured(0.1, n=9))
    assert result == _insufficient_n(0.1, 9)


# 1.5 TTM_p90: 100 - ECDF_mid(p90)


def test_ttm_p90_oracle() -> None:
    ecdf = _mid_rank_ecdf([100.0, 200.0, 300.0, 400.0, 500.0])
    cases = [
        (50.0, 100.0),   # below the whole population: ECDF 0
        (300.0, 50.0),   # mid-rank: (2 + 0.5) / 5
        (450.0, 20.0),   # 4 below: 4 / 5
        (600.0, 0.0),    # above the whole population: ECDF 1
    ]
    for p90, expected in cases:
        result = ttm_p90(measured(p90, n=6), ecdf)
        assert result == _scored(p90, expected, n=6)


def test_ttm_p90_insufficient_n_one_below_minimum() -> None:
    ecdf = _mid_rank_ecdf([100.0, 200.0])
    result = ttm_p90(measured(300.0, n=4), ecdf)
    assert result == _insufficient_n(300.0, 4)
