"""Unit tests for :mod:`cvi.audit.anchors`.

The fixture database exercises every D-VER-4 relation exactly once in the
verdict it deserves: two rating anchors PASS (kubernetes rated, torvalds/linux
legal = DCO), one archived gate PASSes, and the v1-shaped gaps FAIL
(off-platform, mirror, non-software, low-coverage vllm, unrecorded 5.1).
The keras-hub relation is report-only and must never FAIL.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


from cvi.audit.anchors import (
    ANCHOR_RELATIONS,
    AnchorResult,
    evaluate_anchors,
    format_anchor_report,
)

# owner/name -> pillar scores, overall, tier, gated, reason, legal, gfi count
FIXTURE = (
    ("torvalds/linux", (80.0, 75.0, 70.0, 65.0, 60.0), 70.0, "Tier B", 0, None, "DCO", 3),
    ("gcc-mirror/gcc", (80.0, 75.0, 70.0, 65.0, 60.0), 60.0, "Tier B", 0, None, "FOUNDATION_CLA", 0),
    ("kubernetes/kubernetes", (80.0, 75.0, 70.0, 65.0, 60.0), 72.0, "Tier S", 0, None, "FOUNDATION_CLA", 5),
    ("vllm-project/vllm", (90.0, 88.0, 90.0, 86.0, None), 80.0, "Tier S", 0, None, "DCO", 2),
    ("awesome-org/awesome-python", (50.0, 40.0, 45.0, 40.0, 40.0), 40.0, "Tier C", 0, None, "DCO", 0),
    (
        "oldproj/stale",
        (30.0, 30.0, 30.0, 30.0, 30.0),
        30.0,
        "Tier D",
        1,
        "repo archived upstream (archived)",
        "DCO",
        0,
    ),
    ("keras-team/keras-hub", (0.0, 0.0, 0.0, 0.0, 0.0), 0.0, "Tier D", 0, None, "CORP_CLA_UNILATERAL", 0),
)

EXPECTED_VERDICTS = {
    "linux_off_platform": "FAIL",
    "gcc_mirror_gated": "FAIL",
    "archived_repo_gated": "PASS",
    "awesome_non_software": "FAIL",
    "kubernetes_rated": "PASS",
    "vllm_rated": "FAIL",
    "linux_dco": "PASS",
    "newcomer_label_5_1": "FAIL",
    "keras_hub_gate_reported": "REPORT",
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
    for owner_name, pillars, overall, tier, gated, reason, legal, gfi in rows:
        owner, name = owner_name.split("/")
        con.execute(
            "INSERT INTO repositories (owner, name, score_responsiveness, "
            "score_criticality, score_governance, score_rigor_ai, "
            "score_accessibility, overall_cvi, rank_tier, "
            "is_stage0_disqualified, disqualification_reason, legal_ip_model, "
            "deep_dive_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                owner,
                name,
                *pillars,
                overall,
                tier,
                gated,
                reason,
                legal,
                json.dumps({"good_first_issues": {"count": gfi}}),
            ),
        )
    con.commit()
    con.close()
    return path


def _verdicts(results: list[AnchorResult]) -> dict[str, str]:
    return {result.relation: result.verdict for result in results}


def test_relation_ids_follow_dver4_order() -> None:
    # Exactly the D-VER-4 list, in the order the design statement lists it.
    assert [relation_id for relation_id, _ in ANCHOR_RELATIONS] == [
        "linux_off_platform",
        "gcc_mirror_gated",
        "archived_repo_gated",
        "awesome_non_software",
        "kubernetes_rated",
        "vllm_rated",
        "linux_dco",
        "newcomer_label_5_1",
        "keras_hub_gate_reported",
    ]


def test_exact_verdicts_on_fixture_db(tmp_path: Path) -> None:
    results = evaluate_anchors(_make_db(tmp_path / "cvi.db", FIXTURE))
    assert [result.relation for result in results] == [r for r, _ in ANCHOR_RELATIONS]
    assert _verdicts(results) == EXPECTED_VERDICTS


def test_observed_facts_are_exact(tmp_path: Path) -> None:
    results = {r.relation: r for r in evaluate_anchors(_make_db(tmp_path / "cvi.db", FIXTURE))}
    assert results["linux_dco"].observed == "legal_ip_model = DCO"
    assert results["kubernetes_rated"].observed == "coverage 1.00, tier Tier S, rated"
    assert results["vllm_rated"].observed == "coverage 0.80 (< 0.90), tier Tier S, rated"
    assert results["linux_off_platform"].observed == "present, ungated"
    assert results["gcc_mirror_gated"].observed == "present, ungated"
    assert (
        results["archived_repo_gated"].observed
        == "1 gated repo with an archived reason: oldproj/stale"
    )
    assert results["awesome_non_software"].observed == "1 awesome-* repo present, none gated non_software"
    assert (
        results["newcomer_label_5_1"].observed
        == "3 rated repos with newcomer-label count >= 1; no per-parameter 5.1 score stored"
    )
    assert results["keras_hub_gate_reported"].observed == "present, ungated"
    assert results["keras_hub_gate_reported"].verdict == "REPORT"


def test_missing_anchor_repo_fails_with_named_observed(tmp_path: Path) -> None:
    rows = tuple(row for row in FIXTURE if not row[0].startswith(("torvalds/", "keras-team/")))
    results = {r.relation: r for r in evaluate_anchors(_make_db(tmp_path / "cvi.db", rows))}
    assert results["linux_dco"].verdict == "FAIL"
    assert results["linux_dco"].observed == "repo not present in DB"
    assert results["linux_off_platform"].verdict == "FAIL"
    assert results["linux_off_platform"].observed == "repo not present in DB"
    assert results["keras_hub_gate_reported"].observed == "repo not present in DB"
    assert results["keras_hub_gate_reported"].verdict == "REPORT"


def test_gated_anchor_repo_fails_the_rating_relation(tmp_path: Path) -> None:
    rows = tuple(
        (row[0], row[1], row[2], row[3], 1, "PR ghost rate exceeds 50% ceiling (black hole)")
        + row[6:]
        if row[0] == "kubernetes/kubernetes"
        else row
        for row in FIXTURE
    )
    results = {r.relation: r for r in evaluate_anchors(_make_db(tmp_path / "cvi.db", rows))}
    assert results["kubernetes_rated"].verdict == "FAIL"
    assert results["kubernetes_rated"].observed == "gated: PR ghost rate exceeds 50% ceiling (black hole)"


def test_report_is_stable_across_runs(tmp_path: Path) -> None:
    """Over-correction guard: the untouched fixture yields the same report
    on every evaluation."""
    path = _make_db(tmp_path / "cvi.db", FIXTURE)
    first = evaluate_anchors(path)
    second = evaluate_anchors(path)
    assert first == second
    assert format_anchor_report(first, 7, 6) == format_anchor_report(second, 7, 6)


def test_anchor_report_counts_verdicts(tmp_path: Path) -> None:
    results = evaluate_anchors(_make_db(tmp_path / "cvi.db", FIXTURE))
    text = format_anchor_report(results, 7, 6)
    assert "3 PASS" in text
    assert "5 FAIL" in text
    assert "1 REPORT" in text
