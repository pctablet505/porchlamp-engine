"""Unit tests for :mod:`cvi.audit.sensitivity` (packet W0-5).

A 6-row fixture pins the exact Kendall tau-b of every +/-5 pp pillar
perturbation against the canonical ranking (D-AGG-3).  The oracles are
hand-computed from the fixture's pillar values:

canonical composites (weights .25/.25/.20/.15/.15, 2-decimal rounding)

    aaa/r1   68.00    bbb/r2   74.25    ccc/r3   69.25
    ddd/r4   69.25    eee/r5   62.50    fff/r6   57.75

so the only tied pair in the canonical ranking is (ccc/r3, ddd/r4).
With the six items, 15 pairs exist.  When the r3/r4 pair stays tied in
the perturbed composite it is excluded from the whole statistic, giving
tau-b = 13/13 = 1.0.  When the pair splits with all strict pairs still
concordant, tau-b = 14/sqrt(14*15) = sqrt(14/15).  When it splits and
one strict pair (r1 against r4) inverts, tau-b = 12/sqrt(15*14).
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import pytest

from cvi.audit.sensitivity import (
    TAU_RELEASE_THRESHOLD,
    PerturbationResult,
    SensitivityReport,
    kendall_tau_b,
    perturb_pillar_weight,
    sensitivity_report,
)

# owner/name -> (responsiveness, criticality, governance, rigor, accessibility,
#               canonical overall_cvi, rank_tier)
FIXTURE = (
    ("aaa/r1", 90.0, 60.0, 70.0, 60.0, 50.0, 68.0, "Tier A"),
    ("bbb/r2", 80.0, 95.0, 70.0, 60.0, 50.0, 74.25, "Tier S"),
    ("ccc/r3", 85.0, 70.0, 70.0, 60.0, 50.0, 69.25, "Tier A"),
    ("ddd/r4", 70.0, 85.0, 70.0, 60.0, 50.0, 69.25, "Tier B"),
    ("eee/r5", 60.0, 70.0, 90.0, 40.0, 40.0, 62.5, "Tier B"),
    ("fff/r6", 50.0, 65.0, 85.0, 40.0, 40.0, 57.75, "Tier C"),
)

# Hand-computed tau-b oracles for each pillar and direction (D-AGG-3 rule:
# +5 pp on the pillar, the other four weights rescaled by (1-w')/(1-w)).
EXPECTED_TAU: dict[tuple[str, int], float] = {
    ("responsiveness", 5): 12.0 / math.sqrt(210.0),
    ("responsiveness", -5): 14.0 / math.sqrt(210.0),
    ("criticality", 5): 14.0 / math.sqrt(210.0),
    ("criticality", -5): 12.0 / math.sqrt(210.0),
    ("governance", 5): 1.0,
    ("governance", -5): 1.0,
    ("rigor", 5): 1.0,
    ("rigor", -5): 1.0,
    ("accessibility", 5): 1.0,
    ("accessibility", -5): 1.0,
}


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
            overall_cvi REAL,
            rank_tier VARCHAR(30),
            is_stage0_disqualified BOOLEAN DEFAULT 0,
            disqualification_reason TEXT,
            last_indexed_at TIMESTAMP,
            deep_dive_json TEXT
        )
        """
    )
    for row in rows:
        owner_name, resp, crit, gov, rig, acc, overall, tier = row[:8]
        gated = int(row[8]) if len(row) > 8 else 0
        owner, name = owner_name.split("/")
        con.execute(
            "INSERT INTO repositories (owner, name, score_responsiveness, "
            "score_criticality, score_governance, score_rigor_ai, "
            "score_accessibility, overall_cvi, rank_tier, "
            "is_stage0_disqualified) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (owner, name, resp, crit, gov, rig, acc, overall, tier, gated),
        )
    con.commit()
    con.close()
    return path


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return _make_db(tmp_path / "cvi.db", FIXTURE)


def test_kendall_tau_b_hand_oracles() -> None:
    """Exact tau-b values on hand-built vectors, including tie handling."""
    assert kendall_tau_b([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 1.0
    assert kendall_tau_b([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]) == -1.0
    # x ties pair (2a, 2b) only; y ranks them: 5 concordant, 1 tie-in-x-only.
    assert kendall_tau_b([1.0, 2.0, 2.0, 4.0], [1.0, 2.0, 3.0, 4.0]) == pytest.approx(
        5.0 / math.sqrt(30.0), abs=1e-12
    )
    # Pairs tied in both vectors are excluded from the whole statistic.
    assert kendall_tau_b([1.0, 1.0, 3.0], [1.0, 1.0, 3.0]) == 1.0
    # Symmetry.
    assert kendall_tau_b([1.0, 2.0, 2.0, 4.0], [1.0, 2.0, 3.0, 4.0]) == pytest.approx(
        kendall_tau_b([1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 2.0, 4.0]), abs=1e-15
    )


def test_kendall_tau_b_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="length"):
        kendall_tau_b([1.0, 2.0], [1.0])


def test_perturb_pillar_weight_redistributes_proportionally() -> None:
    base = {"responsiveness": 0.25, "criticality": 0.25, "governance": 0.20,
            "rigor": 0.15, "accessibility": 0.15}
    up = perturb_pillar_weight(base, "responsiveness", 5)
    assert up["responsiveness"] == pytest.approx(0.30, abs=1e-12)
    assert up["criticality"] == pytest.approx(0.25 * 0.70 / 0.75, abs=1e-12)
    assert up["governance"] == pytest.approx(0.20 * 0.70 / 0.75, abs=1e-12)
    assert up["rigor"] == pytest.approx(0.14, abs=1e-12)
    assert up["accessibility"] == pytest.approx(0.14, abs=1e-12)
    assert sum(up.values()) == pytest.approx(1.0, abs=1e-12)
    down = perturb_pillar_weight(base, "criticality", -5)
    assert down["criticality"] == pytest.approx(0.20, abs=1e-12)
    assert down["responsiveness"] == pytest.approx(0.25 * 0.80 / 0.75, abs=1e-12)
    with pytest.raises(ValueError, match="pillar"):
        perturb_pillar_weight(base, "vibes", 5)


def test_sensitivity_report_exact_tau_values(db_path: Path) -> None:
    """The 6-row fixture: exact tau-b per pillar and direction (D-AGG-3)."""
    report = sensitivity_report(db_path, pp=5)
    assert isinstance(report, SensitivityReport)
    assert report.rated == 6
    assert report.pp == 5
    assert report.threshold == TAU_RELEASE_THRESHOLD
    assert len(report.rows) == 10
    for row in report.rows:
        assert isinstance(row, PerturbationResult)
        expected = EXPECTED_TAU[(row.pillar, row.delta_pp)]
        assert row.tau_b == pytest.approx(expected, abs=1e-12), (row.pillar, row.delta_pp)


def test_sensitivity_report_ordering_is_canonical(db_path: Path) -> None:
    """Rows are listed pillar-by-pillar in the frozen weight order."""
    report = sensitivity_report(db_path, pp=5)
    assert [(row.pillar, row.delta_pp) for row in report.rows] == [
        ("responsiveness", 5),
        ("responsiveness", -5),
        ("criticality", 5),
        ("criticality", -5),
        ("governance", 5),
        ("governance", -5),
        ("rigor", 5),
        ("rigor", -5),
        ("accessibility", 5),
        ("accessibility", -5),
    ]


def test_zero_pp_perturbation_is_the_identity(db_path: Path) -> None:
    """Over-correction guard: an unperturbed fixture yields the same numbers
    -- every tau is exactly 1.0 and the recomputed canonical composite
    reproduces the stored overall_cvi for all six rows."""
    report = sensitivity_report(db_path, pp=0)
    assert all(row.tau_b == 1.0 for row in report.rows)
    con = sqlite3.connect(str(db_path))
    try:
        stored = dict(con.execute("SELECT owner || '/' || name, overall_cvi FROM repositories"))
    finally:
        con.close()
    base = {
        "responsiveness": 0.25,
        "criticality": 0.25,
        "governance": 0.20,
        "rigor": 0.15,
        "accessibility": 0.15,
    }
    for owner_name, resp, crit, gov, rig, acc, overall, _tier in FIXTURE:
        recomputed = round(
            base["responsiveness"] * resp
            + base["criticality"] * crit
            + base["governance"] * gov
            + base["rigor"] * rig
            + base["accessibility"] * acc,
            2,
        )
        assert stored[owner_name] == pytest.approx(recomputed, abs=1e-12)
        assert overall == pytest.approx(recomputed, abs=1e-12)


def test_gated_repos_are_excluded_from_the_rated_set(tmp_path: Path) -> None:
    """Guard: a gated row at the same composite must not move any number."""
    gated_row = ("ggg/r7", 90.0, 60.0, 70.0, 60.0, 50.0, 68.0, "Tier D", 1)
    path = _make_db(tmp_path / "cvi.db", FIXTURE + (gated_row,))
    base_report = sensitivity_report(_make_db(tmp_path / "base.db", FIXTURE), pp=5)
    with_gated = sensitivity_report(path, pp=5)
    assert with_gated.rated == 6
    for plain, extra in zip(base_report.rows, with_gated.rows):
        assert extra.tau_b == pytest.approx(plain.tau_b, abs=1e-15)



# ---------------------------------------------------------------------------
# W0-3f: gate finding P-10c — tau-b on an all-tied composite is 0/0 when the
# perturbation breaks the tie, and must be UNDECIDED (None), not 1.0.
# ---------------------------------------------------------------------------

TIED_COMPOSITE_DB = (
    # Every row carries overall_cvi = 75.0: the canonical composite is tied.
    ("aaa/r1", 0.0, 100.0, 100.0, 100.0, 100.0, 75.0, "Tier A"),
    ("bbb/r2", 100.0, 0.0, 100.0, 100.0, 100.0, 75.0, "Tier A"),
    ("ccc/r3", 100.0, 100.0, 50.0, 0.0, 100.0, 75.0, "Tier B"),
    ("ddd/r4", 0.0, 100.0, 100.0, 100.0, 100.0, 75.0, "Tier B"),
)


def test_kendall_tau_b_all_tied_on_one_side_is_undefined() -> None:
    """P-10c: every x tied, the perturbation breaks the tie in y -> 0/0 -> None."""
    assert kendall_tau_b([75.0, 75.0, 75.0, 75.0],
                         [70.0, 76.67, 76.67, 70.0]) is None
    # Symmetric: every y tied, x broken -> 0/0 -> None as well.
    assert kendall_tau_b([70.0, 76.67, 76.67, 70.0],
                         [75.0, 75.0, 75.0, 75.0]) is None


def test_kendall_tau_b_tied_in_both_vectors_stays_one() -> None:
    """Over-correction guard: every pair tied in BOTH vectors is the identity
    ranking and keeps the documented 1.0 (the pp=0 guard relies on it)."""
    assert kendall_tau_b([5.0, 5.0, 5.0], [5.0, 5.0, 5.0]) == 1.0
    assert kendall_tau_b([1.0, 2.0, 2.0, 4.0], [1.0, 2.0, 2.0, 4.0]) == 1.0


def test_all_tied_composite_reports_undecided_not_perfect(tmp_path: Path) -> None:
    """P-10c end-to-end: the W0-gate 4-repo DB. The canonical composite is
    tied (75.0 for all rows). Eight of the ten +/-5 pp perturbations break
    the tie -> 0/0-undefined: None with a reason. Accessibility +/-5 keeps
    the composite all-tied -> the documented identical-ranking 1.0. Either
    way all_pass is False and the rendered report says UNDECIDED for the
    broken rows instead of perfect agreement."""
    from cvi.audit.sensitivity import format_sensitivity_report

    db = _make_db(tmp_path / "tied.db", TIED_COMPOSITE_DB)
    report = sensitivity_report(db, pp=5)
    assert report.rated == 4
    assert len(report.rows) == 10
    for row in report.rows:
        if row.pillar == "accessibility":
            assert row.tau_b == 1.0, (row.pillar, row.delta_pp)
            assert row.reason is None
        else:
            assert row.tau_b is None, (row.pillar, row.delta_pp)
            assert row.reason == "UNDECIDED/TAU_B_UNDEFINED"
    assert report.all_pass is False
    text = format_sensitivity_report(report)
    assert "UNDECIDED" in text
    assert "release gate: FAIL" in text
