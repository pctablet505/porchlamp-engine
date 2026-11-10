"""Round-trip verification for the store.

Three ``ScoreRecord``s built by hand -- rated, gated, unrated -- written and
read back; every field (including JSON columns and the ``params`` rows) is
asserted equal to the value it was built with, not merely "present".
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from porchlamp.core.models import ParamScore, PersonaComposite, PRWindow, PRWindowEvent
from porchlamp.store import reader, schema
from porchlamp.store.migrations import apply_migrations
from porchlamp.store.writer import write_records, write_snapshot

from .helpers import (
    make_gated_record,
    make_measured,
    make_rated_record,
    make_repo_identity,
    make_repo_write,
    make_unrated_record,
)


def _fresh_db(path: Path) -> sqlite3.Connection:
    conn = schema.connect(path)  # PRAGMA foreign_keys=ON
    apply_migrations(conn, applied_at="2026-09-13T00:00:00Z")
    write_snapshot(
        conn, id="snap-1", as_of="2026-09-13", benchmark_version="2.0.0-dev",
        constants_hash="c" * 64, code_commit="deadbeef", manifest_root="root",
        created_at="2026-09-13T00:00:00Z",
    )
    return conn


def test_roundtrip_rated_gated_unrated(tmp_path) -> None:
    conn = _fresh_db(tmp_path / "db.sqlite")

    rated_repo = make_repo_identity(
        "kubernetes", "kubernetes", ecosystem="go", archived=False, fork=False, license_spdx="Apache-2.0",
    )
    rated_record = make_rated_record(
        "snap-1",
        repo=rated_repo,
        raw_hash="r" * 64,
        composite=88.42,
        coverage=0.95,
        pillar_scores={"responsiveness": 90.0, "criticality": 85.0, "governance": 80.0,
                        "rigor": 92.0, "accessibility": 87.0},
        params={
            "ttfhr_hours": ParamScore(value=95.0, score=95.0, measured=True, reason=None, n=50, window_start=None, window_end=None, method="primary", source="test", fetched_at="2026-09-01T00:00:00+00:00"),
            "dependents_deduped": ParamScore(value=None, score=None, measured=False, reason="insufficient_n", n=2, window_start=None, window_end=None, method=None, source=None, fetched_at=None),
        },
        tier="A",
        tier_ceiling=None,
        leverage=91.0,
        leverage_coverage=1.0,
        friction=12.0,
        friction_coverage=1.0,
        quadrant="Sweet Spot",
        persona_gates={"senior_systems": "low_elephant_factor"},
        persona_composites={
            "balanced": PersonaComposite(porchlamp=88.42, coverage=0.95, tier="A", rated=True),
            "first_timer": PersonaComposite(porchlamp=70.1, coverage=0.7, tier="C", rated=True),
        },
        suspicion=["lockstep_review", "source_drift"],
        gate_undecided=("D-GATE-3",),
    )
    rated_pr_window = PRWindow(
        start="2026-01-01T00:00:00Z", end="2026-09-01T00:00:00Z", truncated=False,
        events=(
            PRWindowEvent(
                pr_number=42, created_at="2026-08-01T00:00:00Z", closed_at=None, merged_at=None,
                author_login="alice", author_association="NONE", is_external=True,
                first_human_review_at="2026-08-01T06:15:00Z",  # 6.25 hours later
            ),
        ),
    )
    rated_item = make_repo_write(
        rated_record,
        inputs={
            "ttfhr_hours": make_measured(2.5, n=50, source="data/raw/github_graphql/k8s.json", source_hash="d" * 64),
            "dependents_deduped": make_measured(1200, n=2, method="proxy:package_level"),
        },
        pr_window=rated_pr_window,
    )

    gated_repo = make_repo_identity(
        "mirror-org", "gcc-mirror", ecosystem="c", archived=False,
        mirror_url="https://gcc.gnu.org/git/gcc.git", fork=False, license_spdx="GPL-3.0",
    )
    gated_record = make_gated_record("snap-1", repo=gated_repo, raw_hash="g" * 64, gate_reason="mirror")
    gated_item = make_repo_write(gated_record)

    unrated_repo = make_repo_identity("acme", "tiny-lib", ecosystem="python")
    unrated_record = make_unrated_record(
        "snap-1",
        repo=unrated_repo,
        raw_hash="u" * 64,
        coverage=0.12,
        params={"ttfhr_hours": ParamScore(value=None, score=None, measured=False, reason="third_party_gap", n=0, window_start=None, window_end=None, method=None, source=None, fetched_at=None)},
        tier_ceiling="B",
    )
    unrated_item = make_repo_write(unrated_record, inputs={})

    counts = write_records(conn, "snap-1", [rated_item, gated_item, unrated_item])
    # 3 repos; params rows = rated's 2 (ttfhr_hours, dependents_deduped) +
    # gated's 0 (never scored) + unrated's 1 (ttfhr_hours) = 3.
    assert counts == (3, 3, 0)

    # --- rated ---
    row = reader.get_row(conn, "snap-1", "kubernetes", "kubernetes")
    assert row["ecosystem"] == "go"
    assert row["raw_hash"] == "r" * 64
    assert row["porchlamp"] == 88.42
    assert row["coverage"] == 0.95
    assert row["tier"] == "A"
    assert row["tier_ceiling"] is None
    assert row["leverage"] == 91.0
    assert row["leverage_coverage"] == 1.0
    assert row["friction"] == 12.0
    assert row["friction_coverage"] == 1.0
    assert row["quadrant"] == "Sweet Spot"
    assert row["eligible"] is True
    assert row["rated"] is True
    assert row["unrated_reason"] is None
    assert row["persona_gates"] == {"senior_systems": "low_elephant_factor"}
    assert row["persona_composites"] == {
        "balanced": {"porchlamp": 88.42, "coverage": 0.95, "tier": "A", "rated": True},
        "first_timer": {"porchlamp": 70.1, "coverage": 0.7, "tier": "C", "rated": True},
    }
    assert row["suspicion"] == ["lockstep_review", "source_drift"]
    assert row["gate_undecided"] == ["D-GATE-3"]
    assert row["license_spdx"] == "Apache-2.0"
    assert row["archived"] is False
    assert row["mirror"] is False
    assert row["fork"] is False
    assert row["ttfhr_p75"] == 6.25
    assert row["p_responsiveness"] == 90.0
    assert row["c_responsiveness"] == 0.9
    assert row["benchmark_version"] == "2.0.0-dev"

    rated_evidence = {e["param_id"]: e for e in reader.get_evidence(conn, "snap-1", "kubernetes", "kubernetes")}
    assert rated_evidence["ttfhr_hours"]["raw_value"] == 2.5
    assert rated_evidence["ttfhr_hours"]["score"] == 95.0
    assert rated_evidence["ttfhr_hours"]["measured"] is True
    assert rated_evidence["ttfhr_hours"]["n"] == 50
    assert rated_evidence["ttfhr_hours"]["source"] == "data/raw/github_graphql/k8s.json"
    assert rated_evidence["ttfhr_hours"]["source_hash"] == "d" * 64
    assert rated_evidence["dependents_deduped"]["raw_value"] == 1200
    assert rated_evidence["dependents_deduped"]["measured"] is False
    assert rated_evidence["dependents_deduped"]["reason"] == "insufficient_n"
    assert rated_evidence["dependents_deduped"]["n"] == 2

    # --- gated ---
    gated_row = reader.get_row(conn, "snap-1", "mirror-org", "gcc-mirror")
    assert gated_row["eligible"] is False
    assert gated_row["gate_reason"] == "mirror"
    assert gated_row["rated"] is False
    assert gated_row["porchlamp"] is None
    assert gated_row["coverage"] == 0.0
    assert gated_row["mirror"] is True
    assert gated_row["gate_undecided"] == []
    assert reader.get_evidence(conn, "snap-1", "mirror-org", "gcc-mirror") == []

    # --- unrated ---
    unrated_row = reader.get_row(conn, "snap-1", "acme", "tiny-lib")
    assert unrated_row["eligible"] is True
    assert unrated_row["rated"] is False
    assert unrated_row["unrated_reason"] == "unrated: insufficient_evidence"
    assert unrated_row["tier_ceiling"] == "B"
    assert unrated_row["porchlamp"] is None
    assert unrated_row["coverage"] == 0.12

    unrated_evidence = reader.get_evidence(conn, "snap-1", "acme", "tiny-lib")
    assert len(unrated_evidence) == 1
    assert unrated_evidence[0]["param_id"] == "ttfhr_hours"
    assert unrated_evidence[0]["measured"] is False
    assert unrated_evidence[0]["reason"] == "third_party_gap"
    assert unrated_evidence[0]["raw_value"] is None

    # not-ranked lists both gated and unrated, ordered by name, rated excluded
    not_ranked = reader.list_not_ranked(conn, "snap-1")
    assert [(r["owner"], r["name"]) for r in not_ranked] == [("acme", "tiny-lib"), ("mirror-org", "gcc-mirror")]

    snapshot = reader.get_snapshot(conn, "snap-1")
    assert snapshot["code_commit"] == "deadbeef"
    assert snapshot["manifest_root"] == "root"
