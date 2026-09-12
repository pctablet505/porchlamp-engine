"""W2-3 acceptance: pillar 3 — Governance curves (02 section 4).

Fail-before (R-1): on the base commit ``cvi.core.params`` does not exist, so
every test in this module fails at import. 3.1 and 3.3 are evidence-based
enum lookups (the regime/rung is the value, not a rate); 3.2 is the
elephant-factor linear calibration with a 30-commit minimum sample.
"""
from __future__ import annotations

import pytest

from cvi.core import constants_v2
from cvi.core.models import Measured, ParamScore, Unmeasured
from cvi.core.params import PARAMS, elephant_factor, legal_ip, promotion_ladder

_HASH = "ab" * 32
_FETCHED_AT = "2026-09-01T00:00:00+00:00"


def measured(value: object, n: int = 0, method: str = "primary") -> Measured:
    return Measured(
        value=value,
        n=n,
        window_start=None,
        window_end=None,
        method=method,
        source="raw/legal_ip/org__alpha.json",
        source_hash=_HASH,
        fetched_at=_FETCHED_AT,
    )


# 3.1 Legal IP: DCO 100, NONE 85 (owner decision O-1), FOUNDATION_CLA 75, CORP_CLA 25


def test_legal_ip_oracle() -> None:
    cases = [
        ("DCO", 100.0),
        ("NONE", 85.0),
        ("FOUNDATION_CLA", 75.0),
        ("CORP_CLA", 25.0),
    ]
    for regime, expected in cases:
        result = legal_ip(measured(regime))
        assert result == ParamScore(score=expected, measured=True, reason=None, n=0, method="primary")


def test_legal_ip_rejects_a_regime_outside_the_frozen_vocabulary() -> None:
    with pytest.raises(ValueError, match="CORP_CLA_RECIPROCAL"):
        legal_ip(measured("CORP_CLA_RECIPROCAL"))


def test_legal_ip_unmeasured_passes_through() -> None:
    result = legal_ip(Unmeasured(reason="collector_failure", detail="legal_ip probe failed"))
    assert result == ParamScore(score=None, measured=False, reason="collector_failure", n=0, method=None)


# 3.2 Elephant factor: min(100, 25*EF), minimum n 30 commits


def test_elephant_factor_oracle() -> None:
    cases = [
        (1, 25.0),   # one org holds 50% of the commits
        (2, 50.0),
        (3, 75.0),
        (4, 100.0),  # cap boundary: 25*4
        (9, 100.0),
    ]
    for ef, expected in cases:
        result = elephant_factor(measured(ef, n=120))
        assert result == ParamScore(score=expected, measured=True, reason=None, n=120, method="primary")


def test_elephant_factor_insufficient_n_one_below_minimum() -> None:
    result = elephant_factor(measured(3, n=29))
    assert result == ParamScore(score=None, measured=False, reason="insufficient_n", n=29, method=None)


def test_elephant_factor_measured_at_exact_minimum() -> None:
    result = elephant_factor(measured(2, n=30))
    assert result == ParamScore(score=50.0, measured=True, reason=None, n=30, method="primary")


# 3.3 Promotion ladder: VERIFIED 100, DOCUMENTED 75, AD_HOC 40, CLOSED 10


def test_promotion_ladder_oracle() -> None:
    cases = [
        ("CLOSED", 10.0),
        ("AD_HOC", 40.0),
        ("DOCUMENTED", 75.0),
        ("VERIFIED", 100.0),
    ]
    for rung, expected in cases:
        result = promotion_ladder(measured(rung))
        assert result == ParamScore(score=expected, measured=True, reason=None, n=0, method="primary")


def test_promotion_ladder_rejects_a_rung_outside_the_frozen_vocabulary() -> None:
    with pytest.raises(ValueError, match="VERIFIED_PROMOTION"):
        promotion_ladder(measured("VERIFIED_PROMOTION"))


def test_promotion_ladder_unmeasured_passes_through() -> None:
    result = promotion_ladder(Unmeasured(reason="not_applicable", detail="archived repo"))
    assert result == ParamScore(score=None, measured=False, reason="not_applicable", n=0, method=None)


def test_all_frozen_regimes_and_rungs_are_registered() -> None:
    for regime in constants_v2.LEGAL_IP_VALUES:
        assert PARAMS["legal_ip"].score(regime) > 0.0
    for rung in constants_v2.PROMOTION_LADDER_VALUES:
        assert PARAMS["promotion_ladder"].score(rung) > 0.0
