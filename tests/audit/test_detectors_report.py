"""Acceptance tests for :mod:`cvi.audit.detectors_report` (packet W3-2).

Fail-before: before this packet ``cvi.audit.detectors_report`` does not
exist, so every test below fails on ``ModuleNotFoundError`` (recorded
verbatim in the handoff).

Covers: the lazy-registry wiring (the ``forward``/W5-1f pattern), row
loading from the interim JSON format, key-matched current/previous
detection, and a 6-record fixture asserting the rendered counts.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import json
from pathlib import Path

import pytest

from cvi.audit.detectors_report import (
    DetectorRow,
    build_report,
    format_report,
    load_rows,
    main,
)


# --------------------------------------------------------------------------- #
# Wiring (mirrors tests/audit/test_wiring.py's forward pattern)               #
# --------------------------------------------------------------------------- #


def _public_top_level_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name):
                names.add(target.id)
    return {name for name in names if not name.startswith("_")}


def test_detectors_report_entry_lists_module_public_names() -> None:
    from cvi import audit

    origin = importlib.util.find_spec("cvi.audit.detectors_report").origin
    expected = _public_top_level_names(Path(origin).read_text(encoding="utf-8"))
    assert "cvi.audit.detectors_report" in audit._LAZY_EXPORTS
    assert set(audit._LAZY_EXPORTS["cvi.audit.detectors_report"]) == expected
    assert not any(name.startswith("_") for name in audit._LAZY_EXPORTS["cvi.audit.detectors_report"])


def test_detectors_report_names_resolve_via_getattr() -> None:
    from cvi import audit

    registry = audit._LAZY_EXPORTS
    assert set(registry["cvi.audit.detectors_report"]) <= set(audit.__all__)
    for name in registry["cvi.audit.detectors_report"]:
        provider = next(module for module, names in registry.items() if name in names)
        expected = importlib.import_module(provider)
        assert getattr(audit, name) is getattr(expected, name)


# --------------------------------------------------------------------------- #
# Row loading                                                                  #
# --------------------------------------------------------------------------- #


def _write_rows(tmp_path: Path, name: str, rows: list[dict]) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def _speed_row(key: str, ttfhr: float, dr90: float) -> dict:
    return {
        "key": key,
        "params": {
            "ttfhr_hours": {"score": ttfhr, "measured": True, "n": 40},
            "dr90_decision_rate": {"score": dr90, "measured": True, "n": 40},
        },
    }


def test_load_rows_builds_key_to_row_mapping(tmp_path: Path) -> None:
    path = _write_rows(tmp_path, "current.json", [_speed_row("org/a", 95.0, 10.0)])
    rows = load_rows(path)
    assert set(rows) == {"org/a"}
    row = rows["org/a"]
    assert isinstance(row, DetectorRow)
    assert row.record.params["ttfhr_hours"].score == 95.0
    assert row.record.params["ttfhr_hours"].measured is True


def test_load_rows_missing_param_is_unmeasured(tmp_path: Path) -> None:
    path = _write_rows(tmp_path, "current.json", [{"key": "org/a", "params": {}}])
    rows = load_rows(path)
    assert "ttfhr_hours" not in rows["org/a"].record.params


def test_load_rows_optional_fields_become_detector_inputs(tmp_path: Path) -> None:
    path = _write_rows(tmp_path, "current.json", [{
        "key": "org/a",
        "params": {},
        "newcomer_issue_count": 60,
        "elephant_factor": 5,
        "top_email_domain_share": 0.7,
        "closed_unread_share": 0.4,
        "closed_unread_n": 20,
        "star_lockstep_penalty_applied": True,
        "source_drift_held": True,
        "newcomer_accounts": [
            {"login": "n1", "account_created_at": "2026-01-01T00:00:00+00:00", "all_prs_merged": True}
        ],
    }])
    row = load_rows(path)["org/a"]
    assert row.inputs.newcomer_issue_count == 60
    assert row.inputs.elephant_factor == 5
    assert row.inputs.top_email_domain_share == 0.7
    assert row.inputs.closed_unread_share == 0.4
    assert row.inputs.closed_unread_n == 20
    assert row.inputs.star_lockstep_penalty_applied is True
    assert row.inputs.source_drift_held is True
    assert len(row.inputs.newcomer_accounts) == 1
    assert row.inputs.newcomer_accounts[0].login == "n1"


def test_load_rows_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_rows("/nonexistent/path/rows.json")


# --------------------------------------------------------------------------- #
# build_report / format_report: a 6-record fixture                            #
# --------------------------------------------------------------------------- #


def _six_record_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """6 repos, one clean hit per detector family this fixture can drive
    without cohort/identity ownership-request data, plus one healthy repo
    that flags nothing (the over-correction control)."""
    current_rows = [
        # 1. speed_without_decisions: fast but never decides.
        _speed_row("org/speedy", 95.0, 10.0),
        # 2. merges_without_return: merges first-timers, nobody returns.
        {
            "key": "org/onehit",
            "params": {
                "mr_first_rate": {"score": 95.0, "measured": True, "n": 30},
                "newcomer_conversion": {"score": 90.0, "measured": True, "n": 30},
                "rr90_return_rate": {"score": 5.0, "measured": True, "n": 30},
            },
        },
        # 3. label_spam: 80 newcomer issues, 3% conversion.
        {
            "key": "org/spammer",
            "params": {"newcomer_conversion": {"score": 3.0, "measured": True, "n": 30}},
            "newcomer_issue_count": 80,
        },
        # 4. ef_domain_mismatch: one org's domain is 90% of commits, EF 5.
        {
            "key": "org/onedomain",
            "params": {},
            "elephant_factor": 5,
            "top_email_domain_share": 0.9,
        },
        # 5. closed_unread + source_drift together (two detectors, one repo).
        {
            "key": "org/ghosted",
            "params": {},
            "closed_unread_share": 0.5,
            "closed_unread_n": 20,
            "source_drift_held": True,
        },
        # 6. healthy: nothing fires.
        {
            "key": "org/healthy",
            "params": {
                "ttfhr_hours": {"score": 70.0, "measured": True, "n": 40},
                "dr90_decision_rate": {"score": 75.0, "measured": True, "n": 40},
            },
            "elephant_factor": 6,
            "top_email_domain_share": 0.1,
        },
    ]
    current = _write_rows(tmp_path, "current.json", current_rows)
    # previous = identical rows (Measure convention from the packet): no
    # param_jump possible, same-snapshot detectors reproduce identically.
    previous = _write_rows(tmp_path, "previous.json", current_rows)
    return current, previous


def test_build_report_six_record_fixture_counts(tmp_path: Path) -> None:
    current_path, previous_path = _six_record_fixture(tmp_path)
    current = load_rows(current_path)
    previous = load_rows(previous_path)
    report = build_report(current, previous)

    assert report.total_repos == 6
    assert report.counts == {
        "param_jump": 0,
        "speed_without_decisions": 1,
        "merges_without_return": 1,
        "closed_unread": 1,
        "newcomer_burst": 0,
        "star_burst": 0,
        "label_spam": 1,
        "ef_domain_mismatch": 1,
        "source_drift": 1,
    }
    assert report.flagged["speed_without_decisions"] == ("org/speedy",)
    assert report.flagged["merges_without_return"] == ("org/onehit",)
    assert report.flagged["label_spam"] == ("org/spammer",)
    assert report.flagged["ef_domain_mismatch"] == ("org/onedomain",)
    assert report.flagged["closed_unread"] == ("org/ghosted",)
    assert report.flagged["source_drift"] == ("org/ghosted",)
    assert report.flagged["param_jump"] == ()
    assert report.flagged["newcomer_burst"] == ()
    assert report.flagged["star_burst"] == ()


def test_format_report_renders_counts_and_flagged_repos(tmp_path: Path) -> None:
    current_path, previous_path = _six_record_fixture(tmp_path)
    report = build_report(load_rows(current_path), load_rows(previous_path))
    text = format_report(report)
    assert "repositories scored: 6" in text
    assert "speed_without_decisions: 1 (org/speedy)" in text
    assert "merges_without_return: 1 (org/onehit)" in text
    assert "label_spam: 1 (org/spammer)" in text
    assert "ef_domain_mismatch: 1 (org/onedomain)" in text
    assert "source_drift: 1 (org/ghosted)" in text
    assert "closed_unread: 1 (org/ghosted)" in text
    assert "param_jump: 0" in text
    assert "newcomer_burst: 0" in text
    assert "star_burst: 0" in text


def test_build_report_repo_absent_from_previous_still_scores(tmp_path: Path) -> None:
    """A repo present only in ``current`` is scored with ``previous=None``
    for it: the same-snapshot detectors still run."""
    current = _write_rows(tmp_path, "current.json", [_speed_row("org/new", 95.0, 10.0)])
    empty_previous: dict[str, DetectorRow] = {}
    report = build_report(load_rows(current), empty_previous)
    assert report.counts["speed_without_decisions"] == 1
    assert report.counts["param_jump"] == 0


def test_main_current_only_exits_zero_and_prints_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    current_path, _ = _six_record_fixture(tmp_path)
    exit_code = main(["--current", str(current_path)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "repositories scored: 6" in out


def test_main_missing_current_file_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--current", "/nonexistent/rows.json"])
    assert exit_code == 2
    assert "cannot load detector rows" in capsys.readouterr().err


def test_main_json_output_is_valid_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    current_path, previous_path = _six_record_fixture(tmp_path)
    exit_code = main(["--current", str(current_path), "--previous", str(previous_path), "--json"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total_repos"] == 6
    assert payload["counts"]["label_spam"] == 1
