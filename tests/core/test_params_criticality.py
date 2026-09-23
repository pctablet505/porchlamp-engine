"""Acceptance tests for pillar 2 — Criticality curves (02 section 4).

2.1 and 2.3 are the ECDF-normalised curves of D-NORM-3 (mid-rank ECDF of
the raw measured value, no transform on either side); 2.2 is the absolute
linear calibration ``100 * C``. None of the three has a minimum-n rule;
missing signals are an ``Unmeasured`` third-party gap.
"""
from __future__ import annotations

from typing import Callable

from porchlamp.core.models import Measured, ParamScore, Unmeasured
from porchlamp.core.params import dependents, downloads, openssf_criticality

_HASH = "ab" * 32
_FETCHED_AT = "2026-09-01T00:00:00+00:00"


def measured(value: object, n: int = 0, method: str = "primary") -> Measured:
    return Measured(
        value=value,
        n=n,
        window_start=None,
        window_end=None,
        method=method,
        source="raw/depsdev/org__alpha.json",
        source_hash=_HASH,
        fetched_at=_FETCHED_AT,
    )


def unmeasured(reason: str = "third_party_gap") -> Unmeasured:
    return Unmeasured(reason=reason, detail="registry has no data")


def _scored(value: object, score: float) -> ParamScore:
    """The ``ParamScore`` a param function returns for ``measured(value)``."""
    return ParamScore(
        value=value,
        score=score,
        measured=True,
        reason=None,
        n=0,
        window_start=None,
        window_end=None,
        method="primary",
        source="raw/depsdev/org__alpha.json",
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


# 2.1 Dependents: ECDF_mid(d), raw


def test_dependents_oracle() -> None:
    ecdf = _mid_rank_ecdf([0.0, 1.0, 2.0, 3.0, 4.0])
    cases = [
        (0, 10.0),        # d=0: mid-rank (0 + 0.5) / 5, tied with the 0.0 member
        (1, 30.0),        # d=1: mid-rank (1 + 0.5) / 5, tied with the 1.0 member
        (2, 50.0),        # d=2: mid-rank (2 + 0.5) / 5
        (4, 90.0),        # d=4: mid-rank (4 + 0.5) / 5, tied with the top member
        (10**6, 100.0),   # above the whole population
    ]
    for count, expected in cases:
        result = dependents(measured(count), ecdf)
        assert result == _scored(count, expected)


def test_dependents_unmeasured_passes_through() -> None:
    result = dependents(unmeasured(), _mid_rank_ecdf([0.0, 1.0]))
    assert result == _gap("third_party_gap")


# 2.2 OpenSSF criticality: 100 * C


def test_openssf_criticality_oracle() -> None:
    cases = [
        (0.0, 0.0),
        (0.37, 37.0),
        (0.75, 75.0),
        (1.0, 100.0),
    ]
    for pike, expected in cases:
        result = openssf_criticality(measured(pike))
        assert result == _scored(pike, expected)


def test_openssf_criticality_unmeasured_when_a_signal_is_missing() -> None:
    result = openssf_criticality(Unmeasured(reason="third_party_gap", detail="commit frequency uncollected"))
    assert result == _gap("third_party_gap")


# 2.3 Downloads: ECDF_mid(m), raw


def test_downloads_oracle() -> None:
    ecdf = _mid_rank_ecdf([0.0, 1.0, 2.0, 3.0, 4.0])
    cases = [
        (0, 10.0),        # m=0: tied with the 0.0 member
        (1, 30.0),        # m=1: tied with the 1.0 member
        (3, 70.0),        # m=3: mid-rank (3 + 0.5) / 5, tied with the 3.0 member
        (10**9, 100.0),   # above the whole population
    ]
    for monthly, expected in cases:
        result = downloads(measured(monthly), ecdf)
        assert result == _scored(monthly, expected)


def test_downloads_unmeasured_for_systems_ecosystem() -> None:
    result = downloads(unmeasured(), _mid_rank_ecdf([0.0, 1.0]))
    assert result == _gap("third_party_gap")
