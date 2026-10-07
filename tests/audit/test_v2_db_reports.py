"""Unit tests verifying v2 SQLite database reports in porchlamp/audit/."""

from __future__ import annotations

import json
from pathlib import Path

from porchlamp.audit import anchors, coverage, sensitivity, ties
from porchlamp.audit.anchors import PASS, REPORT, SKIPPED, anchors_report, evaluate_anchors
from porchlamp.audit.sensitivity import sensitivity_report
from porchlamp.audit.ties import ties_report
from porchlamp.core.models import (
    Cohorts,
    FeatureBundle,
    GateResult,
    Measured,
    ParamScore,
    PillarScore,
    PRWindow,
    RepoIdentity,
    ScoreRecord,
)
from porchlamp.snapshot import store_cmd
from porchlamp.store import schema
from porchlamp.store.migrations import apply_migrations
from porchlamp.store.writer import RepoWrite, finalize_snapshot_hash, write_records, write_snapshot

HEX64 = "a" * 64
SNAPSHOT_ID = "snap-v2-audit-test"
_EMPTY_PR_WINDOW = PRWindow(start=None, end=None, truncated=False, events=())


def _make_v2_record(
    owner: str,
    name: str,
    *,
    eligible: bool = True,
    gate_reason: str | None = None,
    routed: str | None = None,
    rated: bool = True,
    composite: float | None = 85.0,
    coverage_val: float = 0.95,
    tier: str | None = "A",
    pillar_scores: dict[str, float] | None = None,
    params: dict[str, ParamScore] | None = None,
    inputs: dict[str, Measured] | None = None,
    archived: bool = False,
    mirror_url: str | None = None,
) -> RepoWrite:
    p_scores = pillar_scores or {
        "responsiveness": 88.0,
        "criticality": 84.0,
        "governance": 82.0,
        "rigor": 80.0,
        "accessibility": 90.0,
    }
    pillars = {
        k: PillarScore(score=v if rated else None, coverage=coverage_val if rated else 0.0)
        for k, v in p_scores.items()
    }
    repo_id = RepoIdentity(
        owner=owner,
        name=name,
        ecosystem="python",
        archived=archived,
        mirror_url=mirror_url,
        fork=False,
        license_spdx="MIT",
        default_branch="main",
    )
    record = ScoreRecord(
        repo=repo_id,
        snapshot_id=SNAPSHOT_ID,
        benchmark_version="2.0.0",
        constants_hash="c" * 64,
        raw_hash=HEX64,
        provenance_hash=HEX64,
        gate=GateResult(eligible=eligible, reason=gate_reason, routed=routed, undecided=()),
        params=dict(params or {}),
        pillars=pillars,
        composite=composite if rated else None,
        coverage=coverage_val if rated else 0.0,
        tier=tier if rated else None,
        leverage=75.0 if rated else None,
        friction=20.0 if rated else None,
        quadrant="Sweet Spot" if rated else None,
        rated=rated,
        unrated_reason=None if rated else (gate_reason or "insufficient_coverage"),
        tier_ceiling=None,
        leverage_coverage=1.0 if rated else 0.0,
        friction_coverage=1.0 if rated else 0.0,
        persona_gates={},
        persona_composites={},
        tie_key=(),
        tie_group=None,
        rank=None,
        suspicion=[],
    )
    bundle = FeatureBundle(
        schema_version=2,
        repo=repo_id,
        inputs=dict(inputs or {}),
        pr_window=_EMPTY_PR_WINDOW,
        cohorts=Cohorts(decision_pr_numbers=(), newcomer_authors=()),
        provenance_hash=HEX64,
    )
    return RepoWrite(record=record, bundle=bundle)


def _build_full_v2_db(db_path: Path) -> Path:
    conn = schema.connect(db_path)
    try:
        apply_migrations(conn, applied_at="2026-09-16T00:00:00Z")
        write_snapshot(
            conn,
            id=SNAPSHOT_ID,
            as_of="2026-09-16",
            benchmark_version="2.0.0",
            constants_hash="c" * 64,
            code_commit="deadbeef" * 5,
            manifest_root="r" * 8,
            created_at="2026-09-16T00:00:00Z",
        )
        items = [
            # Rated anchor: kubernetes/kubernetes
            _make_v2_record(
                "kubernetes",
                "kubernetes",
                rated=True,
                composite=91.5,
                coverage_val=0.96,
                tier="S",
                pillar_scores={
                    "responsiveness": 95.0,
                    "criticality": 92.0,
                    "governance": 90.0,
                    "rigor": 88.0,
                    "accessibility": 90.0,
                },
                params={
                    "newcomer_issue_supply": ParamScore(
                        value=12,
                        score=85.0,
                        measured=True,
                        reason=None,
                        n=25,
                        window_start=None,
                        window_end=None,
                        method="primary",
                        source="raw/issues_counts/kubernetes__kubernetes.json",
                        fetched_at="2026-09-16T00:00:00Z",
                    )
                },
                inputs={
                    "newcomer_issue_supply": Measured(
                        value=12,
                        n=25,
                        window_start=None,
                        window_end=None,
                        method="primary",
                        source="raw/issues_counts/kubernetes__kubernetes.json",
                        source_hash=HEX64,
                        fetched_at="2026-09-16T00:00:00Z",
                    )
                },
            ),
            # Rated anchor: vllm-project/vllm (tied composite with another repo to test ties)
            _make_v2_record(
                "vllm-project",
                "vllm",
                rated=True,
                composite=84.2,
                coverage_val=0.92,
                tier="A",
                pillar_scores={
                    "responsiveness": 86.0,
                    "criticality": 85.0,
                    "governance": 82.0,
                    "rigor": 80.0,
                    "accessibility": 86.0,
                },
            ),
            # Another rated repo sharing composite 84.2 to form a tie group
            _make_v2_record(
                "pytorch",
                "pytorch",
                rated=True,
                composite=84.2,
                coverage_val=0.94,
                tier="A",
                pillar_scores={
                    "responsiveness": 84.0,
                    "criticality": 88.0,
                    "governance": 80.0,
                    "rigor": 82.0,
                    "accessibility": 85.0,
                },
            ),
            # Report anchor: keras-team/keras-hub
            _make_v2_record(
                "keras-team",
                "keras-hub",
                rated=True,
                composite=78.0,
                coverage_val=0.90,
                tier="B",
                pillar_scores={
                    "responsiveness": 78.0,
                    "criticality": 76.0,
                    "governance": 80.0,
                    "rigor": 75.0,
                    "accessibility": 81.0,
                },
            ),
            # Routed off-platform + DCO anchor: torvalds/linux
            _make_v2_record(
                "torvalds",
                "linux",
                eligible=True,
                routed="off_platform",
                rated=False,
                composite=None,
                coverage_val=0.75,
                params={
                    "legal_ip": ParamScore(
                        value="DCO",
                        score=100.0,
                        measured=True,
                        reason=None,
                        n=1,
                        window_start=None,
                        window_end=None,
                        method="primary",
                        source="raw/governance/torvalds__linux.json",
                        fetched_at="2026-09-16T00:00:00Z",
                    )
                },
                inputs={
                    "legal_ip": Measured(
                        value="DCO",
                        n=1,
                        window_start=None,
                        window_end=None,
                        method="primary",
                        source="raw/governance/torvalds__linux.json",
                        source_hash=HEX64,
                        fetched_at="2026-09-16T00:00:00Z",
                    )
                },
            ),
            # Mirror gated anchor: gcc-mirror/gcc
            _make_v2_record(
                "gcc-mirror",
                "gcc",
                eligible=False,
                gate_reason="mirror",
                rated=False,
                mirror_url="git://gcc.gnu.org/git/gcc.git",
            ),
            # Archived gated anchor
            _make_v2_record(
                "old-org",
                "legacy-repo",
                eligible=False,
                gate_reason="archived",
                rated=False,
                archived=True,
            ),
            # Awesome non-software gated anchor
            _make_v2_record(
                "sindresorhus",
                "awesome-python",
                eligible=False,
                gate_reason="non_software",
                rated=False,
            ),
        ]
        write_records(conn, SNAPSHOT_ID, items)
        finalize_snapshot_hash(conn, db_path, SNAPSHOT_ID, promoted_at="2026-09-16T00:00:00Z")
    finally:
        conn.close()
    return db_path


def test_v2_sensitivity_report(tmp_path: Path) -> None:
    db_path = _build_full_v2_db(tmp_path / "porchlamp_v2.db")
    report = sensitivity_report(db_path, pp=5)
    assert report.rated == 4
    assert len(report.rows) == 10
    for row in report.rows:
        assert row.tau_b is not None
        assert -1.0 <= row.tau_b <= 1.0
    text = sensitivity.format_sensitivity_report(report)
    assert "rated repos: 4" in text
    # The CLI's exit code IS the release verdict (F-02): 0 when the gate
    # passes, 1 when it fails. Asserting it against report.all_pass rather
    # than a literal keeps this meaningful whatever this fixture produces --
    # the previous `== 0` passed vacuously, because main() returned 0
    # unconditionally, and this fixture's gate does not in fact pass.
    assert not report.all_pass, "fixture expected to fail the gate; see the exit-code assertion below"
    assert sensitivity.main(["--db", str(db_path), "--json"]) == (0 if report.all_pass else 1)


def test_v2_ties_report(tmp_path: Path) -> None:
    db_path = _build_full_v2_db(tmp_path / "porchlamp_v2.db")
    report = ties_report(db_path)
    assert report.rated == 4
    assert report.gated == 3
    assert report.unrated == 1
    assert report.distinct == 3
    assert len(report.groups) == 1
    assert report.groups[0].value == 84.2
    assert report.groups[0].size == 2
    text = ties.format_tie_report(report)
    assert "4 rated, 3 gated, 1 unrated" in text
    assert ties.main(["--db", str(db_path), "--json"]) == 0


def test_v2_anchors_report(tmp_path: Path) -> None:
    db_path = _build_full_v2_db(tmp_path / "porchlamp_v2.db")
    report = anchors_report(db_path)
    assert len(report) == 9
    assert report.rows_total == 8
    assert report.rows_rated == 4
    by_rel = {r.relation: r for r in report}
    assert by_rel["linux_off_platform"].verdict == PASS
    assert by_rel["gcc_mirror_gated"].verdict == PASS
    assert by_rel["archived_repo_gated"].verdict == PASS
    assert by_rel["awesome_non_software"].verdict == PASS
    assert by_rel["kubernetes_rated"].verdict == PASS
    assert by_rel["vllm_rated"].verdict == PASS
    assert by_rel["linux_dco"].verdict == PASS
    assert by_rel["newcomer_label_5_1"].verdict == PASS
    assert by_rel["keras_hub_gate_reported"].verdict == REPORT
    # Verify evaluate_anchors alias returns identical results
    assert evaluate_anchors(db_path) == report
    text = anchors.format_anchor_report(report, report.rows_total, report.rows_rated)
    assert "8 PASS, 0 FAIL, 1 REPORT" in text
    assert anchors.main(["--db", str(db_path), "--json"]) == 0


def test_v2_snapshot_50_db_reports(tmp_path: Path) -> None:
    db_path = tmp_path / "snapshot_50.db"
    scored, frozen = store_cmd.score_snapshot_dir(Path("tests/fixtures/snapshot_50"))
    conn = schema.connect(db_path)
    try:
        apply_migrations(conn, applied_at="2026-09-16T00:00:00Z")
        store_cmd.write_scored_snapshot(
            conn, db_path, scored, frozen, code_commit="deadbeef" * 5
        )
    finally:
        conn.close()

    t_report = ties_report(db_path)
    assert t_report.rated == 0
    # 2026-10-06: was 31 / 19. D-GATE-5 reads gr14, which these frozen
    # bundles do not carry, so the fixture's eight black holes are UNDECIDED
    # (tests/snapshot/test_store_cli.py, _SNAPSHOT_50_EXPECTED_COUNTS).
    assert t_report.gated == 23
    assert t_report.unrated == 27
    assert t_report.distinct == 0

    a_report = anchors_report(db_path)
    assert len(a_report) == 9
    by_rel = {r.relation: r for r in a_report}
    assert by_rel["archived_repo_gated"].verdict == PASS
    assert by_rel["awesome_non_software"].verdict == PASS
    assert by_rel["keras_hub_gate_reported"].verdict == REPORT
    assert by_rel["gcc_mirror_gated"].verdict == SKIPPED


def test_coverage_includes_v2_collectors(tmp_path: Path) -> None:
    expected_v2 = {
        "repo_object",
        "commits_365d",
        "dependents",
        "pr_window",
        "releases",
        "issues_counts",
        "runs",
        "governance",
        "author_history",
        "universe_entry",
    }
    assert expected_v2.issubset(set(coverage.COLLECTORS))
    assert expected_v2.issubset(set(coverage.ALL_COLLECTORS))

    # Create a directory with v2 raw collectors and verify discovery
    raw_repo_obj = tmp_path / "raw" / "repo_object"
    raw_repo_obj.mkdir(parents=True)
    (raw_repo_obj / "acme__v2repo.json").write_text(
        json.dumps({"data": {"open_issues": 5}}), encoding="utf-8"
    )
    repos = coverage.discover_repos(tmp_path)
    assert "acme/v2repo" in repos
    report = coverage.audit_universe(tmp_path)
    assert report.n_repos == 1


def test_v2_sensitivity_snapshot_populates_tie_key_and_rank(tmp_path: Path) -> None:
    """Regression test: _v2_snapshot_from_db populates tie_key, rank, and tie_group."""
    db_path = _build_full_v2_db(tmp_path / "porchlamp_v2.db")
    snap = sensitivity._v2_snapshot_from_db(db_path)
    assert snap is not None
    rated = [r for r in snap.records if r.rank is not None]
    assert len(rated) == 4
    for rec in rated:
        assert len(rec.tie_key) == 8
        assert rec.tie_key[7] == (rec.repo.owner.lower(), rec.repo.name.lower())


def test_v2_anchors_mismatched_gate_reason_fails(tmp_path: Path) -> None:
    """Regression test: non-empty mismatched gate_reason fails mirror/archived anchor checks."""
    db_path = tmp_path / "mismatched_anchors.db"
    conn = schema.connect(db_path)
    try:
        apply_migrations(conn, applied_at="2026-09-16T00:00:00Z")
        write_snapshot(
            conn,
            id=SNAPSHOT_ID,
            as_of="2026-09-16",
            benchmark_version="2.0.0",
            constants_hash="c" * 64,
            code_commit="deadbeef" * 5,
            manifest_root="r" * 8,
            created_at="2026-09-16T00:00:00Z",
        )
        items = [
            # Mirror repo gated for "stale" instead of "mirror" -> must FAIL
            _make_v2_record(
                "gcc-mirror",
                "gcc",
                eligible=False,
                gate_reason="stale",
                rated=False,
                mirror_url="git://gcc.gnu.org/git/gcc.git",
            ),
            # Archived repo gated for "stale" instead of "archived" -> must FAIL
            _make_v2_record(
                "old-org",
                "legacy-repo",
                eligible=False,
                gate_reason="stale",
                rated=False,
                archived=True,
            ),
        ]
        write_records(conn, SNAPSHOT_ID, items)
        finalize_snapshot_hash(conn, db_path, SNAPSHOT_ID, promoted_at="2026-09-16T00:00:00Z")
    finally:
        conn.close()

    report = anchors_report(db_path)
    by_rel = {r.relation: r for r in report}
    assert by_rel["gcc_mirror_gated"].verdict == anchors.FAIL
    assert by_rel["archived_repo_gated"].verdict == anchors.FAIL

