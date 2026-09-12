"""W2-3 acceptance: pillar 4 — Rigor curves (02 section 4).

Fail-before (R-1): on the base commit ``cvi.core.params`` does not exist, so
every test in this module fails at import. 4.3 replaces the v1 Gaussian with
the monotone rate curve ``min(100, 125*r)``; the Gaussian regression guard
lives in ``test_params_monotonic.py``. 4.1's minimum-n rule (10 merged PRs)
applies to the proxy method only; the Scorecard primary method has none.
"""
from __future__ import annotations

import pytest

from cvi.core import constants_v2
from cvi.core.models import Measured, ParamScore, Unmeasured
from cvi.core.params import ai_posture, pipeline_defenses, substantive_review_rate

_HASH = "ab" * 32
_FETCHED_AT = "2026-09-01T00:00:00+00:00"


def measured(value: object, n: int = 0, method: str = "primary") -> Measured:
    return Measured(
        value=value,
        n=n,
        window_start=None,
        window_end=None,
        method=method,
        source="raw/openssf_scorecard/org__alpha.json",
        source_hash=_HASH,
        fetched_at=_FETCHED_AT,
    )


# 4.1 Pipeline defenses: identity on the 0-100 composite, clamped


def test_pipeline_defenses_oracle() -> None:
    cases = [
        (0.0, 0.0),
        (33.5, 33.5),
        (100.0, 100.0),
        (150.0, 100.0),  # above scale: clamped
        (-20.0, 0.0),    # below scale: clamped
    ]
    for value, expected in cases:
        result = pipeline_defenses(measured(value))
        assert result == ParamScore(score=expected, measured=True, reason=None, n=0, method="primary")


def test_pipeline_defenses_scorecard_method_has_no_minimum_n() -> None:
    result = pipeline_defenses(measured(80.0, n=0))
    assert result == ParamScore(score=80.0, measured=True, reason=None, n=0, method="primary")


def test_pipeline_defenses_proxy_method_insufficient_n_one_below_minimum() -> None:
    result = pipeline_defenses(measured(80.0, n=9, method="proxy:ci_review_blend"))
    assert result == ParamScore(score=None, measured=False, reason="insufficient_n", n=9, method=None)


def test_pipeline_defenses_proxy_method_measured_at_exact_minimum() -> None:
    result = pipeline_defenses(measured(80.0, n=10, method="proxy:ci_review_blend"))
    assert result == ParamScore(score=80.0, measured=True, reason=None, n=10, method="proxy:ci_review_blend")


# 4.2 AI posture: persona-conditional lookup


def test_ai_posture_human_oracle() -> None:
    cases = [
        ("AD_HOC", 40.0),
        ("PROSCRIBED", 85.0),
        ("REGULATED", 95.0),
    ]
    for posture, expected in cases:
        result = ai_posture(measured(posture), "human")
        assert result == ParamScore(score=expected, measured=True, reason=None, n=0, method="primary")


def test_ai_posture_ai_oracle() -> None:
    cases = [
        ("PROSCRIBED", 25.0),
        ("AD_HOC", 40.0),
        ("REGULATED", 95.0),
    ]
    for posture, expected in cases:
        result = ai_posture(measured(posture), "ai")
        assert result == ParamScore(score=expected, measured=True, reason=None, n=0, method="primary")


def test_ai_posture_defaults_to_human() -> None:
    result = ai_posture(measured("PROSCRIBED"))
    assert result == ParamScore(score=85.0, measured=True, reason=None, n=0, method="primary")


def test_ai_posture_rejects_unknown_persona() -> None:
    with pytest.raises(ValueError, match="robot"):
        ai_posture(measured("REGULATED"), "robot")


def test_ai_posture_rejects_a_posture_outside_the_frozen_vocabulary() -> None:
    with pytest.raises(ValueError, match="OPEN"):
        ai_posture(measured("OPEN"))


# 4.3 Substantive review rate: min(100, 125*r) — the v2 replacement of the Gaussian


def test_substantive_review_rate_oracle() -> None:
    cases = [
        (0.0, 0.0),
        (0.2, 25.0),
        (0.8, 100.0),  # cap boundary: 125*0.8
        (1.0, 100.0),
    ]
    for rate, expected in cases:
        result = substantive_review_rate(measured(rate, n=15))
        assert result == ParamScore(score=expected, measured=True, reason=None, n=15, method="primary")


def test_substantive_review_rate_insufficient_n_one_below_minimum() -> None:
    result = substantive_review_rate(measured(0.9, n=9))
    assert result == ParamScore(score=None, measured=False, reason="insufficient_n", n=9, method=None)


def test_substantive_review_rate_unmeasured_passes_through() -> None:
    for reason in constants_v2.UNMEASURED_REASONS:
        result = substantive_review_rate(Unmeasured(reason=reason, detail="x"))
        assert result == ParamScore(score=None, measured=False, reason=reason, n=0, method=None)
