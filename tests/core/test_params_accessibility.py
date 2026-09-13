"""W2-3 acceptance: pillar 5 — Accessibility curves (02 section 4).

Fail-before (R-1): on the base commit ``cvi.core.params`` does not exist, so
every test in this module fails at import. 5.1 is the capped supply curve;
5.2/5.3 are the cohort rate curves with a 5-author minimum; 5.4 is the
identity on the 0-100 DX composite (runtime + setup blended by the deriver)
with a 5-run minimum.
"""
from __future__ import annotations

from cvi.core import constants_v2
from cvi.core.models import Measured, ParamScore, Unmeasured
from cvi.core.params import dx, newcomer_conversion, newcomer_issue_supply, rr90_return_rate

_HASH = "ab" * 32
_FETCHED_AT = "2026-09-01T00:00:00+00:00"


def measured(value: object, n: int = 0, method: str = "primary") -> Measured:
    return Measured(
        value=value,
        n=n,
        window_start=None,
        window_end=None,
        method=method,
        source="raw/gfi_issues/org__alpha.json",
        source_hash=_HASH,
        fetched_at=_FETCHED_AT,
    )


def _scored(value: object, score: float, *, n: int = 0) -> ParamScore:
    """The ``ParamScore`` a param function returns for ``measured(value, n)``."""
    return ParamScore(
        value=value,
        score=score,
        measured=True,
        reason=None,
        n=n,
        window_start=None,
        window_end=None,
        method="primary",
        source="raw/gfi_issues/org__alpha.json",
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
        window_start=None,
        window_end=None,
        method=None,
        source="raw/gfi_issues/org__alpha.json",
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


# 5.1 Newcomer-issue supply: min(100, 10*count)


def test_newcomer_issue_supply_oracle() -> None:
    cases = [
        (0, 0.0),      # repo-controlled absence: measured zero, not Unmeasured
        (3, 30.0),
        (7, 70.0),
        (10, 100.0),   # cap boundary: 10*10
        (25, 100.0),
    ]
    for count, expected in cases:
        result = newcomer_issue_supply(measured(count))
        assert result == _scored(count, expected)


# 5.2 Newcomer conversion: min(100, 200*r), minimum n 5 authors


def test_newcomer_conversion_oracle() -> None:
    cases = [
        (0.0, 0.0),
        (0.25, 50.0),
        (0.5, 100.0),  # cap boundary: 200*0.5
        (1.0, 100.0),
    ]
    for rate, expected in cases:
        result = newcomer_conversion(measured(rate, n=8))
        assert result == _scored(rate, expected, n=8)


def test_newcomer_conversion_insufficient_n_one_below_minimum() -> None:
    result = newcomer_conversion(measured(1.0, n=4))
    assert result == _insufficient_n(1.0, 4)


def test_newcomer_conversion_measured_at_exact_minimum() -> None:
    result = newcomer_conversion(measured(0.2, n=5))
    assert result == _scored(0.2, 40.0, n=5)


# 5.3 RR_90 return rate: min(100, 200*r), minimum n 5 authors


def test_rr90_return_rate_oracle() -> None:
    cases = [
        (0.0, 0.0),
        (0.15, 30.0),
        (0.5, 100.0),  # cap boundary
        (1.0, 100.0),
    ]
    for rate, expected in cases:
        result = rr90_return_rate(measured(rate, n=6))
        assert result == _scored(rate, expected, n=6)


def test_rr90_return_rate_insufficient_n_one_below_minimum() -> None:
    result = rr90_return_rate(measured(0.9, n=4))
    assert result == _insufficient_n(0.9, 4)


# 5.4 DX: identity on the 0-100 composite, 5-run minimum


def test_dx_oracle() -> None:
    cases = [
        (0.0, 0.0),
        (42.5, 42.5),
        (100.0, 100.0),
        (130.0, 100.0),  # clamped
    ]
    for value, expected in cases:
        result = dx(measured(value, n=30))
        assert result == _scored(value, expected, n=30)


def test_dx_insufficient_n_one_below_minimum() -> None:
    result = dx(measured(50.0, n=4))
    assert result == _insufficient_n(50.0, 4)


def test_dx_unmeasured_when_run_timing_is_absent() -> None:
    for reason in constants_v2.UNMEASURED_REASONS:
        result = dx(Unmeasured(reason=reason, detail="no run_timing"))
        assert result == _gap(reason)
