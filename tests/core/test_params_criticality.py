"""W2-3 acceptance: pillar 2 — Criticality curves (02 section 4).

Fail-before (R-1): on the base commit ``cvi.core.params`` does not exist, so
every test in this module fails at import. 2.1 and 2.3 are the
ECDF-normalised curves of D-NORM-3 (mid-rank ECDF of ``log10(1 + x)``);
2.2 is the absolute linear calibration ``100 * C``. None of the three has a
minimum-n rule; missing signals are an ``Unmeasured`` third-party gap.
"""
from __future__ import annotations

from typing import Callable

from cvi.core.models import Measured, ParamScore, Unmeasured
from cvi.core.params import dependents, downloads, openssf_criticality

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


def _mid_rank_ecdf(sample: list[float]) -> Callable[[float], float]:
    """D-NORM-1 mid-rank ECDF of ``sample``, on the 0-100 scale."""
    size = len(sample)

    def ecdf(x: float) -> float:
        below = sum(1 for s in sample if s < x)
        tied = sum(1 for s in sample if s == x)
        return 100.0 * (below + 0.5 * tied) / size

    return ecdf


# 2.1 Dependents: ECDF_mid(log10(1 + d))


def test_dependents_oracle() -> None:
    ecdf = _mid_rank_ecdf([0.0, 1.0, 2.0, 3.0, 4.0])
    cases = [
        (0, 10.0),        # log10(1) = 0: mid-rank (0 + 0.5) / 5
        (9, 30.0),        # log10(10) = 1: mid-rank (1 + 0.5) / 5
        (99, 50.0),       # log10(100) = 2
        (9999, 90.0),     # log10(10000) = 4
        (10**6, 100.0),   # above the whole population
    ]
    for count, expected in cases:
        result = dependents(measured(count), ecdf)
        assert result == ParamScore(score=expected, measured=True, reason=None, n=0, method="primary")


def test_dependents_unmeasured_passes_through() -> None:
    result = dependents(unmeasured(), _mid_rank_ecdf([0.0, 1.0]))
    assert result == ParamScore(score=None, measured=False, reason="third_party_gap", n=0, method=None)


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
        assert result == ParamScore(score=expected, measured=True, reason=None, n=0, method="primary")


def test_openssf_criticality_unmeasured_when_a_signal_is_missing() -> None:
    result = openssf_criticality(Unmeasured(reason="third_party_gap", detail="commit frequency uncollected"))
    assert result == ParamScore(score=None, measured=False, reason="third_party_gap", n=0, method=None)


# 2.3 Downloads: ECDF_mid(log10(1 + m))


def test_downloads_oracle() -> None:
    ecdf = _mid_rank_ecdf([0.0, 1.0, 2.0, 3.0, 4.0])
    cases = [
        (0, 10.0),        # log10(1) = 0
        (9, 30.0),        # log10(10) = 1
        (999, 70.0),      # log10(1000) = 3: (3 + 0.5) / 5
        (10**9, 100.0),   # above the whole population
    ]
    for monthly, expected in cases:
        result = downloads(measured(monthly), ecdf)
        assert result == ParamScore(score=expected, measured=True, reason=None, n=0, method="primary")


def test_downloads_unmeasured_for_systems_ecosystem() -> None:
    result = downloads(unmeasured(), _mid_rank_ecdf([0.0, 1.0]))
    assert result == ParamScore(score=None, measured=False, reason="third_party_gap", n=0, method=None)
