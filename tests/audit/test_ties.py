"""Unit tests for :mod:`porchlamp.audit.ties`.

A 6-row fixture with one exact composite tie (ccc/r3 and ddd/r4 both at
69.25) pins the distinct-value count, the tie-group sizes, the size
histogram and the share of rated repos that share a composite.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from porchlamp.audit.ties import TieReport, TieGroup, format_tie_report, ties_report

# owner/name -> (pillars, canonical overall_porchlamp, rank_tier, gated)
FIXTURE = (
    ("aaa/r1", 68.0, "Tier A", 0),
    ("bbb/r2", 74.25, "Tier S", 0),
    ("ccc/r3", 69.25, "Tier A", 0),
    ("ddd/r4", 69.25, "Tier B", 0),
    ("eee/r5", 62.5, "Tier B", 0),
    ("fff/r6", 57.75, "Tier C", 0),
)


def _make_db(path: Path, rows: tuple[tuple, ...]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.execute(
        """
        CREATE TABLE repositories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner VARCHAR(100),
            name VARCHAR(100),
            ecosystem VARCHAR(50) DEFAULT 'python',
            domain_category VARCHAR(50) DEFAULT 'software',
            stars INTEGER DEFAULT 0,
            forks INTEGER DEFAULT 0,
            monthly_downloads BIGINT,
            dependents_count INTEGER,
            openssf_criticality REAL,
            elephant_factor INTEGER,
            legal_ip_model VARCHAR(30) DEFAULT 'DCO',
            ai_posture VARCHAR(30) DEFAULT 'AD_HOC',
            score_responsiveness REAL,
            score_criticality REAL,
            score_governance REAL,
            score_rigor_ai REAL,
            score_accessibility REAL,
            leverage_score REAL,
            friction_score REAL,
            matrix_quadrant VARCHAR(30),
            overall_porchlamp REAL,
            rank_tier VARCHAR(30),
            is_stage0_disqualified BOOLEAN DEFAULT 0,
            disqualification_reason TEXT,
            last_indexed_at TIMESTAMP,
            deep_dive_json TEXT
        )
        """
    )
    for owner_name, overall, tier, gated in rows:
        owner, name = owner_name.split("/")
        con.execute(
            "INSERT INTO repositories (owner, name, overall_porchlamp, rank_tier, "
            "is_stage0_disqualified) VALUES (?,?,?,?,?)",
            (owner, name, overall, tier, gated),
        )
    con.commit()
    con.close()
    return path


def test_exact_tie_statistics_on_six_row_fixture(tmp_path: Path) -> None:
    """Distinct composites and tie-group sizes are exact values, not shapes."""
    path = _make_db(tmp_path / "porchlamp.db", FIXTURE)
    report = ties_report(path)
    assert isinstance(report, TieReport)
    assert report.rated == 6
    assert report.gated == 0
    assert report.distinct == 5
    assert report.groups == (TieGroup(value=69.25, size=2),)
    assert report.size_histogram() == {2: 1}
    assert report.largest_size() == 2
    assert report.share_in_tie_groups() == pytest.approx(1.0 / 3.0, abs=1e-12)


def test_all_distinct_yields_zero_groups(tmp_path: Path) -> None:
    rows = (
        ("aaa/r1", 90.0, "Tier S", 0),
        ("bbb/r2", 80.0, "Tier A", 0),
        ("ccc/r3", 70.0, "Tier B", 0),
    )
    report = ties_report(_make_db(tmp_path / "porchlamp.db", rows))
    assert report.distinct == 3
    assert report.groups == ()
    assert report.size_histogram() == {}
    assert report.largest_size() == 0
    assert report.share_in_tie_groups() == 0.0


def test_gated_repos_do_not_enter_the_tie_counts(tmp_path: Path) -> None:
    """Over-correction guard: a gated row duplicating the tied composite
    leaves every reported number unchanged."""
    rows = FIXTURE + (("ggg/r7", 69.25, "Tier D", 1),)
    path = _make_db(tmp_path / "porchlamp.db", rows)
    report = ties_report(path)
    assert report.rated == 6
    assert report.gated == 1
    assert report.distinct == 5
    assert report.groups == (TieGroup(value=69.25, size=2),)
    assert report.size_histogram() == {2: 1}


def test_repeated_evaluation_is_deterministic(tmp_path: Path) -> None:
    """Over-correction guard: re-reading the untouched fixture yields the
    identical report."""
    path = _make_db(tmp_path / "porchlamp.db", FIXTURE)
    first = ties_report(path)
    second = ties_report(path)
    assert first == second
    assert format_tie_report(first) == format_tie_report(second)


def test_report_names_the_largest_group(tmp_path: Path) -> None:
    rows = (
        ("aaa/r1", 70.0, "Tier A", 0),
        ("bbb/r2", 70.0, "Tier A", 0),
        ("ccc/r3", 70.0, "Tier B", 0),
        ("ddd/r4", 60.0, "Tier C", 0),
        ("eee/r5", 60.0, "Tier C", 0),
    )
    report = ties_report(_make_db(tmp_path / "porchlamp.db", rows))
    assert report.size_histogram() == {3: 1, 2: 1}
    assert report.largest_size() == 3
    text = format_tie_report(report)
    assert "70.0" in text
    assert "3" in text
