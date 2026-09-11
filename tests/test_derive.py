"""Tests for scripts/derive.py feature-bundle derivation from the raw cache."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("derive", ROOT / "scripts" / "derive.py")
derive = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(derive)

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)
OWNER, NAME = "acme", "widget"


def write(tmp: Path, collector: str, key: str, data, at="2026-09-11T00:00:00Z") -> None:
    p = tmp / "raw" / collector / f"{key}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"fetched_at": at, "source": "test", "data": data}),
                 encoding="utf-8")


def body(tmp: Path, fname: str, text: str) -> None:
    p = tmp / "raw" / "bodies" / f"{OWNER}__{NAME}__{fname}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"fetched_at": "2026-09-11T00:00:00Z",
                             "source": "test", "data": {"body": text}}),
                 encoding="utf-8")


def seed_full(tmp: Path) -> None:
    k = f"{OWNER}__{NAME}"
    write(tmp, "github_meta", k, {"stars": 1000, "forks": 50,
                                  "pushed_at": "2026-09-01T00:00:00Z", "archived": False,
                                  "license_spdx": "Apache-2.0", "default_branch": "main"})
    write(tmp, "pr_events", k, [
        {"number": 1, "created_at": "2026-06-01T00:00:00Z", "closed_at": "2026-06-02T00:00:00Z",
         "merged_at": "2026-06-02T00:00:00Z", "author_login": "alice",
         "author_association": "NONE", "comments_total": 4, "state": "closed"},
        {"number": 2, "created_at": "2026-06-03T00:00:00Z", "closed_at": "2026-06-04T00:00:00Z",
         "merged_at": None, "author_login": "bob", "author_association": "MEMBER",
         "comments_total": 2, "state": "closed"},
        {"number": 3, "created_at": "2026-06-05T00:00:00Z", "closed_at": None,
         "merged_at": None, "author_login": "alice", "author_association": "NONE",
         "comments_total": 0, "state": "open"},
        {"number": 4, "created_at": "2026-07-01T00:00:00Z", "closed_at": None,
         "merged_at": None, "author_login": "dana",
         "author_association": "FIRST_TIME_CONTRIBUTOR", "comments_total": 9, "state": "open"},
    ])
    write(tmp, "pr_reviews", k, {"1": "2026-06-01T12:00:00Z", "2": None, "3": None, "4": None})
    write(tmp, "contributors", k, [{"login": "alice", "contributions": 40},
                                   {"login": "bob", "contributions": 40},
                                   {"login": "carol", "contributions": 20}])
    write(tmp, "commits_180d", k, [{"author_login": "alice", "date": "2026-08-01T00:00:00Z"},
                                   {"author_login": "alice", "date": "2026-08-02T00:00:00Z"},
                                   {"author_login": "bob", "date": "2026-08-03T00:00:00Z"},
                                   {"author_login": "dependabot[bot]", "date": "2026-08-04T00:00:00Z"},
                                   {"author_login": "github-actions[bot]", "date": "2026-08-05T00:00:00Z"}])
    write(tmp, "gfi", k, [
        {"number": 10, "assignee": None, "updated_at": "2026-09-07T00:00:00Z",
         "labels": ["good-first-issue"]},
        {"number": 11, "assignee": None, "updated_at": "2026-09-08T00:00:00Z",
         "labels": [{"name": "help-wanted"}]},
        {"number": 12, "assignee": "bob", "updated_at": "2026-09-07T00:00:00Z",
         "labels": ["good-first-issue"]},
        {"number": 13, "assignee": None, "updated_at": "2026-07-01T00:00:00Z",
         "labels": ["good-first-issue"]},
    ])
    write(tmp, "actions_runs", k, [{"run_duration_seconds": 600, "created_at": "2026-09-01T00:00:00Z"},
                                    {"run_duration_seconds": 1200, "created_at": "2026-09-02T00:00:00Z"}])
    write(tmp, "depsdev", k, {"stars": 990, "forks": 49, "open_issues": 5,
                              "license": "Apache-2.0", "usedby_count": 777,
                              "scorecard_overall": 8.8})
    write(tmp, "scorecard", k, {"score": 9.0, "checks": {"Branch-Protection": 10}})
    write(tmp, "tree", k, {"paths": ["LICENSE", "CONTRIBUTING.md", "README.md"]})
    write(tmp, "downloads", f"pypi__{NAME}", {"monthly": 5000})
    body(tmp, "CONTRIBUTING.md", "Please sign off your commits (DCO). "
         "Contributors may join the triage team and become maintainers.")
    body(tmp, "README.md", "Use an Assisted-by: trailer for AI-generated code.")


def run(tmp: Path):
    return derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp, fetch=False, now=NOW)


def test_full_bundle(tmp_path):
    seed_full(tmp_path)
    out = run(tmp_path)
    m, c = out["meta"], out["confidence"]
    assert m["stars"] == 1000 and c["stars"] == "verified"
    assert m["forks"] == 50
    assert m["monthly_downloads"] == 5000 and c["monthly_downloads"] == "verified"
    assert m["dependents_count"] == 777
    assert m["elephant_factor"] == 2          # 40 < 50%, 40+40 >= 50%
    assert m["active_nonbot_commits_180d"] == 3
    assert m["scorecard_score"] == 9.0 and c["scorecard_score"] == "verified"  # |9.0-8.8| <= 0.5
    assert m["legal_ip_model"] == "DCO" and c["legal_ip_model"] == "derived"
    assert m["ai_posture"] == "REGULATED"
    assert m["promotion_ladder"] == "DOCUMENTED_PATHWAY"
    assert m["hard_ai_ban"] is False and m["off_platform_reviews"] is False
    assert m["dx_ci_minutes"] == 15.0
    assert m["active_unclaimed_gfis"] == 2
    assert m["rr_90"] == 1.0                  # alice: 2 external PRs, 4-day gap
    assert m["mean_inline_comments"] == 4.0   # only PR1 qualifies
    assert out["provenance"]["stars"]["source"].endswith("github_meta/acme__widget.json")
    assert out["provenance"]["stars"]["fetched_at"] == "2026-09-11T00:00:00Z"
    assert m["openssf_signals"]["contributor_count"] == 3
    assert m["openssf_signals"]["dependents_count"] == 777
    assert m["openssf_signals"]["commit_frequency_weekly"] == round(3 * 7 / 180, 4)
    assert m["openssf_signals"]["created_since_months"] is None  # no source
    assert m["openssf_signals"]["updated_since_months"] > 0


def test_pr_event_mapping(tmp_path):
    seed_full(tmp_path)
    evs = {e["pr_number"]: e for e in run(tmp_path)["pr_events"]}
    assert evs[1]["response_latency_hours"] == 12.0
    assert evs[1]["is_external"] is True and evs[1]["author_association"] == "NONE"
    assert evs[1]["maintainer_review_comments"] == 4
    assert evs[2]["is_external"] is False and evs[2]["author_association"] == "MEMBER"
    assert evs[2]["response_latency_hours"] is None
    assert evs[3]["maintainer_review_comments"] == 0      # open PR -> 0
    assert evs[4]["author_association"] == "FIRST_TIME"   # FIRST_TIME_CONTRIBUTOR mapped
    assert evs[4]["is_external"] is True and evs[4]["maintainer_review_comments"] == 0


def test_missing_files(tmp_path):
    out = run(tmp_path)
    m, c = out["meta"], out["confidence"]
    assert c["stars"] == c["monthly_downloads"] == c["dependents_count"] == "missing"
    assert c["scorecard_score"] == c["rr_90"] == c["dx_ci_minutes"] == "missing"
    assert c["pr_events"] == "missing" and out["pr_events"] == []
    assert m["stars"] == 0 and m["elephant_factor"] == 1 and m["openssf_signals"] is None
    assert m["hard_ai_ban"] is False


def test_bot_filtering(tmp_path):
    k = f"{OWNER}__{NAME}"
    write(tmp_path, "commits_180d", k, [
        {"author_login": "realuser", "date": "2026-08-01T00:00:00Z"},
        {"author_login": "dependabot[bot]", "date": "2026-08-01T00:00:00Z"},
        {"author_login": "actions-user", "date": "2026-08-01T00:00:00Z"},
        {"author_login": "google-cla", "date": "2026-08-01T00:00:00Z"},
        {"author_login": "Stale-Bot", "date": "2026-08-01T00:00:00Z"},
    ])
    assert run(tmp_path)["meta"]["active_nonbot_commits_180d"] == 1


def test_elephant_factor_edges(tmp_path):
    assert derive.elephant_factor(None) == 1
    assert derive.elephant_factor([]) == 1
    assert derive.elephant_factor([{"login": "solo", "contributions": 100}]) == 1
    assert derive.elephant_factor([{"login": "a", "contributions": 60},
                                   {"login": "b", "contributions": 40}]) == 1
    assert derive.elephant_factor([{"login": "a", "contributions": 40},
                                   {"login": "b", "contributions": 40},
                                   {"login": "c", "contributions": 20}]) == 2


def test_scorecard_mismatch_flag(tmp_path):
    k = f"{OWNER}__{NAME}"
    write(tmp_path, "scorecard", k, {"score": 9.0, "checks": {}})
    write(tmp_path, "depsdev", k, {"usedby_count": 1, "scorecard_overall": 6.0})
    out = run(tmp_path)
    assert out["meta"]["scorecard_score"] == 9.0
    assert out["confidence"]["scorecard_score"] == "flag"


def test_downloads_null_is_missing(tmp_path):
    write(tmp_path, "downloads", f"pypi__{NAME}", {"monthly": None})
    out = run(tmp_path)
    assert out["meta"]["monthly_downloads"] == 0
    assert out["confidence"]["monthly_downloads"] == "missing"
