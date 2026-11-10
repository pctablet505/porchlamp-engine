"""F-02: the ``porchlamp audit sensitivity`` CLI must exit non-zero when the release
gate fails.

``main`` used to return 0 on every completing path, so a ranking that failed
every perturbation was indistinguishable from a passing one to any caller.
These tests pin the contract -- exit 1 when ``report.all_pass`` is False,
exit 0 when it is True, on both the text and the ``--json`` output path --
against fabricated reports: the module's own ``sensitivity_report`` is
monkeypatched, so no database or network is touched and the printed output
is asserted unchanged.
"""
from __future__ import annotations

import json
from typing import Optional, Sequence

import pytest

from porchlamp.audit import sensitivity
from porchlamp.audit.sensitivity import (
    DEFAULT_PERTURBATION_PP,
    TAU_RELEASE_THRESHOLD,
    PerturbationResult,
    SensitivityReport,
)

_PILLARS = ("responsiveness", "criticality", "governance", "rigor", "accessibility")


def _make_report(taus: Sequence[Optional[float]]) -> SensitivityReport:
    """One row per pillar and sign (+/- pp), taus taken from ``taus`` in row order."""
    rows = tuple(
        PerturbationResult(pillar, delta, tau)
        for pillar, delta, tau in zip(
            [p for p in _PILLARS for _ in (0, 1)],
            [d for _ in _PILLARS for d in (DEFAULT_PERTURBATION_PP, -DEFAULT_PERTURBATION_PP)],
            taus,
        )
    )
    return SensitivityReport(
        rated=10, pp=DEFAULT_PERTURBATION_PP, threshold=TAU_RELEASE_THRESHOLD, rows=rows
    )


# First row 0.5 < 0.90 -> one perturbation fails; UNDECIDED rows are failures
# too, covered by the sensitivity-v1 unit tests already.
_PASSING_REPORT = _make_report([1.0] * 10)
_FAILING_REPORT = _make_report([0.5] + [1.0] * 9)


@pytest.mark.parametrize(
    ("report", "expected_exit"),
    [
        pytest.param(_PASSING_REPORT, 0, id="gate-pass"),
        pytest.param(_FAILING_REPORT, 1, id="gate-fail"),
    ],
)
@pytest.mark.parametrize("as_json", [False, True], ids=["text", "json"])
def test_main_exit_code_follows_the_gate(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    report: SensitivityReport,
    expected_exit: int,
    as_json: bool,
) -> None:
    assert report.all_pass is (expected_exit == 0)  # the fabricated report means what it says

    monkeypatch.setattr(sensitivity, "sensitivity_report", lambda db_path, pp=0: report)
    argv = ["--db", "no-such-database.db"] + (["--json"] if as_json else [])

    assert sensitivity.main(argv) == expected_exit

    out = capsys.readouterr().out
    if as_json:
        assert json.loads(out)["all_pass"] is report.all_pass
    else:
        assert out.strip().endswith(
            sensitivity.format_sensitivity_report(report).strip().splitlines()[-1]
        )
