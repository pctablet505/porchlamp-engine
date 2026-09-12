"""Tests for scripts/promote.py: verified-bundle -> OperationalStore promotion.

Focus: the promote path must materialize the spec 12.2 deep-dive payload so the
UI's raw-metrics table, survival curve, and maintainer playbook render on real
data (the repo-detail endpoint degrades to pillars-only without it).
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("promote", ROOT / "scripts" / "promote.py")
promote = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(promote)

NOW = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)


def _bundle(owner="acme", name="widget", eco="pypi"):
    created = (NOW - timedelta(days=10)).isoformat()
    merged = (NOW - timedelta(days=9)).isoformat()
    reviewed = (NOW - timedelta(days=9, hours=20)).isoformat()
    return {
        "meta": {
            "owner": owner, "name": name, "ecosystem": eco,
            "stars": 5000, "forks": 400, "monthly_downloads": 100000,
            "dependents_count": 2000, "elephant_factor": 3,
            "legal_ip_model": "DCO", "ai_posture": "REGULATED",
            "scorecard_score": 8.5, "active_nonbot_commits_180d": 250,
            "off_platform_reviews": False, "hard_ai_ban": False,
            "promotion_ladder": "DOCUMENTED_PATHWAY", "mean_inline_comments": 3.0,
            "active_unclaimed_gfis": 8, "rr_90": 0.6, "dx_ci_minutes": 12.0,
            "openssf_signals": None,
        },
        "pr_events": [
            {
                "repo_owner": owner, "repo_name": name, "pr_number": 1,
                "created_at": created, "author_login": "dev",
                "author_association": "NONE", "author_prior_merged_commits": 0,
                "closed_at": merged, "merged_at": merged, "is_external": True,
                "ci_status_green": True, "first_human_review_at": reviewed,
                "response_latency_hours": 4.0, "maintainer_review_comments": 2,
            }
        ],
    }


def _deep_dive_json(db_path: str, owner: str, name: str):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT deep_dive_json FROM repositories WHERE owner=? AND name=?",
            (owner, name),
        ).fetchone()
    finally:
        conn.close()
    return None if row is None or row[0] is None else json.loads(row[0])


def test_promote_materializes_deep_dive(tmp_path):
    db = str(tmp_path / "cvi.db")
    n = promote.promote([_bundle()], db)
    assert n == 1
    dd = _deep_dive_json(db, "acme", "widget")
    assert dd is not None, "promote must populate deep_dive_json for the UI"
    # The UI reads exactly these sections (web/index.html).
    assert set(dd) >= {"raw_metrics", "survival_curve", "maintainer_playbook",
                       "legal_ip", "elephant_breakdown", "good_first_issues"}
    assert set(dd["raw_metrics"]) >= {"ttfhr_hours", "ghost_rate_14d",
                                      "first_timer_merge_rate",
                                      "tail_merge_latency_days"}
    # Playbook is a dict with an actions list (the double-count fix keeps it sane).
    pb = dd["maintainer_playbook"]
    assert isinstance(pb["actions"], list)
    assert pb["potential_cvi"] <= 100.0


def test_promote_deep_dive_survives_empty_pr_events(tmp_path):
    # A repo with no PR events must still get a deep-dive (empty metrics), not
    # crash promotion or leave the column NULL.
    b = _bundle(owner="quiet", name="library")
    b["pr_events"] = []
    db = str(tmp_path / "cvi.db")
    assert promote.promote([b], db) == 1
    dd = _deep_dive_json(db, "quiet", "library")
    assert dd is not None
    # Kaplan-Meier baseline: with no events the curve is the single t=0, S=1 point.
    assert dd["survival_curve"] == [[0.0, 1.0]]
