"""Tests for OpenSSF signals computed locally, scorecard subset, proxies.

Subject: ``porchlamp.collect.pike_signals`` -- the REST transport boundary, the
three new collectors (releases, issue-activity search counts, default-branch
CI runs) and the 02 §4 2.2 / 4.1 derivation -- plus the append-only hooks at
the end of ``scripts/gather.py`` (the ``releases`` / ``issues_counts`` /
``runs`` status entries) and ``scripts/derive.py`` (the ``openssf_signals`` /
``pipeline_defenses`` / ``pipeline_defenses_method`` bundle fields).

Oracle summary (02 §4 2.2, 4.1; D-MISS-4):
  2.2: all ten Pike signals map from recorded envelopes; the value fed to
       ``calculate_pike_criticality`` equals the hand-computed C (exact
       math.log expression below), and any missing envelope leaves
       ``openssf_signals`` ``missing`` (never a partial signal set).
  4.1: with a scorecard carrying >= 1 of the five named checks the value is
       their mean x 10, method ``"primary"``; without one it is
       0.6 x code-review rate x 100 + 0.4 x CI pass rate x 100 (0 when no
       completed runs are recorded), method ``"proxy:review_ci"``.
"""
from __future__ import annotations

import importlib.util
import json
import math
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

import pytest

from porchlamp.collect import pike_signals as pike
from porchlamp.collect.pike_signals import (
    ci_pass_rate,
    collect_issue_counts,
    collect_releases,
    collect_runs,
    comments_per_issue,
    issue_q,
    releases_in_window,
    tags_in_window,
)
from porchlamp.models import OpenSSFSignals
from porchlamp.openssf import calculate_pike_criticality
from porchlamp.pillars.criticality import score_openssf_criticality

ROOT = Path(__file__).resolve().parent.parent.parent
NOW = datetime(2026, 9, 12, 0, 0, 0, tzinfo=timezone.utc)
KEY = "acme__widget"
OWNER, NAME = "acme", "widget"


def _load_script(modname: str, fname: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(modname, ROOT / "scripts" / fname)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[modname] = module
    spec.loader.exec_module(module)
    return module


gather = _load_script("porchlamp_gather_w17", "gather.py")
derive = _load_script("porchlamp_derive_w17", "derive.py")


def write_env(raw: Path, collector: str, key: str, data,
              at: str = "2026-09-11T00:00:00Z") -> None:
    from porchlamp.collect.envelope import write_envelope
    write_envelope(raw / collector / f"{key}.json", source="test", data=data,
                   fetched_at=at)


def read_env(raw: Path, collector: str, key: str):
    from porchlamp.collect.envelope import read_envelope
    return read_envelope(raw / collector / f"{key}.json")


# --- fake REST transport ------------------------------------------------------

class FakeRest:
    """Routing transport: ``routes`` maps (path, required-params dict) -> value.

    A route matches when the call's path equals and the call carries every
    required parameter with the same value.
    """

    def __init__(self, routes: dict) -> None:
        self.routes = dict(routes)
        self.calls: list[tuple] = []

    def get(self, path: str, **params: Any) -> Any:
        self.calls.append((path, params))
        for key, value in self.routes.items():
            kpath, kparams = key
            if kpath == path and (kparams is None
                                  or all(params.get(k) == v for k, v in kparams)):
                return value
        raise AssertionError(f"unexpected REST call: {path} {params}")


# --- releases collector --------------------------------------------------------

def _rel(tag: str, published: Optional[str], prerelease: bool = False) -> dict:
    return {"tag_name": tag, "published_at": published, "prerelease": prerelease}


def test_releases_in_window_counts_only_dated_in_window():
    releases = [
        _rel("v9", "2026-09-01T00:00:00Z"),            # inside
        _rel("v8", "2025-09-13T00:00:00Z"),            # inside (day after cutoff)
        _rel("v7", "2025-09-11T23:59:59Z"),            # outside (before cutoff)
        _rel("v6", "2026-01-01T00:00:00Z", True),      # inside prerelease
        _rel("v5", None),                              # undated: excluded
    ]
    assert releases_in_window(releases, NOW) == 3


def test_releases_in_window_custom_window_days():
    releases = [_rel("v1", "2026-06-01T00:00:00Z")]
    assert releases_in_window(releases, NOW, window_days=100) == 0
    assert releases_in_window(releases, NOW, window_days=105) == 1


def test_tags_in_window_counts_recorded_commit_dates():
    tags = [
        {"name": "v1", "sha": "a" * 40, "commit_date": "2026-05-01T00:00:00Z"},
        {"name": "v0", "sha": "b" * 40, "commit_date": "2025-01-01T00:00:00Z"},
        {"name": "v0rc", "sha": "c" * 40, "commit_date": None},
    ]
    assert tags_in_window(tags, NOW) == 1


def test_collect_releases_writes_envelope_with_in_window_count(tmp_path):
    fake = FakeRest({
        ("repos/acme/widget/releases", frozenset({"per_page": 100, "page": 1}.items())):
            [_rel("v3", "2026-02-01T00:00:00Z"), _rel("v2", "2026-04-01T00:00:00Z")],
        ("repos/acme/widget/releases", frozenset({"per_page": 100, "page": 2}.items())): [],
    })
    assert collect_releases(tmp_path, OWNER, NAME, now=NOW, transport=fake) == "ok"
    env = read_env(tmp_path, "releases", KEY)
    data = env.data
    assert data["source"] == "releases"
    assert data["recent_count"] == 2
    assert data["releases_truncated"] is False
    assert [r["tag_name"] for r in data["releases"]] == ["v3", "v2"]
    assert data["tags"] is None
    assert data["window_days"] == 365


def test_collect_releases_tag_fallback_when_no_releases(tmp_path):
    fake = FakeRest({
        ("repos/acme/widget/releases", frozenset({"per_page": 100, "page": 1}.items())): [],
        ("repos/acme/widget/tags", frozenset({"per_page": 100}.items())):
            [{"name": "v1", "commit": {"sha": "a" * 40}},
             {"name": "v0", "commit": {"sha": "b" * 40}}],
        ("repos/acme/widget/commits/" + "a" * 40, None):
            {"commit": {"author": {"date": "2026-07-01T00:00:00Z"}}},
        ("repos/acme/widget/commits/" + "b" * 40, None):
            {"commit": {"author": {"date": "2024-07-01T00:00:00Z"}}},
    })
    assert collect_releases(tmp_path, OWNER, NAME, now=NOW, transport=fake) == "ok"
    data = read_env(tmp_path, "releases", KEY).data
    assert data["source"] == "tags"
    assert data["recent_count"] == 1
    assert data["releases"] == []
    assert [t["name"] for t in data["tags"]] == ["v1", "v0"]
    assert [t["commit_date"] for t in data["tags"]] == [
        "2026-07-01T00:00:00Z", "2024-07-01T00:00:00Z"]


def test_collect_releases_truncates_at_three_pages(tmp_path):
    page = [_rel(f"v{i}", "2026-08-01T00:00:00Z") for i in range(100)]
    fake = FakeRest({
        ("repos/acme/widget/releases", frozenset({"per_page": 100, "page": 1}.items())): list(page),
        ("repos/acme/widget/releases", frozenset({"per_page": 100, "page": 2}.items())): list(page),
        ("repos/acme/widget/releases", frozenset({"per_page": 100, "page": 3}.items())): list(page),
    })
    assert collect_releases(tmp_path, OWNER, NAME, now=NOW, transport=fake) == "ok"
    data = read_env(tmp_path, "releases", KEY).data
    assert data["releases_truncated"] is True
    assert len(data["releases"]) == 300
    assert data["recent_count"] == 300
    assert len(fake.calls) == 3


def test_collect_releases_skip_when_envelope_exists(tmp_path):
    write_env(tmp_path, "releases", KEY, {"recent_count": 3})

    def must_not_fetch(*a, **k):
        raise AssertionError("collector must be skipped when the envelope exists")

    assert collect_releases(tmp_path, OWNER, NAME, now=NOW, transport=must_not_fetch) == "skip"


# --- issues counts collector ---------------------------------------------------

def test_issue_q_builds_repo_scoped_search_queries():
    assert issue_q(OWNER, NAME) == "repo:acme/widget is:issue"
    assert issue_q(OWNER, NAME, qualifier="closed", since="2026-06-14") == \
        "repo:acme/widget is:issue is:closed closed:>=2026-06-14"
    assert issue_q(OWNER, NAME, qualifier="updated", since="2026-06-14") == \
        "repo:acme/widget is:issue updated:>=2026-06-14"


def test_comments_per_issue_mean_and_empty():
    assert comments_per_issue([{"comments": 1}, {"comments": 2}, {"comments": 3}]) == 2.0
    assert comments_per_issue([{"comments": 7}]) == 7.0
    assert comments_per_issue([]) == 0.0


def test_collect_issue_counts_records_counts_and_sample(tmp_path):
    since = (NOW - timedelta(days=90)).date().isoformat()
    fake = FakeRest({
        ("search/issues", frozenset({
            "q": f"repo:{OWNER}/{NAME} is:issue is:closed closed:>={since}", "per_page": 1}.items())):
            {"total_count": 42, "items": []},
        ("search/issues", frozenset({
            "q": f"repo:{OWNER}/{NAME} is:issue updated:>={since}", "per_page": 1}.items())):
            {"total_count": 120, "items": []},
        ("search/issues", frozenset({
            "q": f"repo:{OWNER}/{NAME} is:issue", "sort": "updated",
            "order": "desc", "per_page": 30}.items())):
            {"total_count": 30, "items": [
                {"number": 1, "comments": 4, "updated_at": "2026-09-10T00:00:00Z"},
                {"number": 2, "comments": 5, "updated_at": "2026-09-11T00:00:00Z"},
                {"number": 3, "comments": 4, "updated_at": "2026-09-11T06:00:00Z"}]},
    })
    assert collect_issue_counts(tmp_path, OWNER, NAME, now=NOW, transport=fake) == "ok"
    data = read_env(tmp_path, "issues_counts", KEY).data
    assert data["closed_90d"] == 42
    assert data["updated_90d"] == 120
    assert data["sample_size"] == 30
    assert data["sample_total"] == 30
    assert data["comments_per_issue"] == round(13 / 3, 4)
    assert data["window_days"] == 90


def test_collect_issue_counts_zero_issues_is_measured_zero(tmp_path):
    fake = FakeRest({
        ("search/issues", None): {"total_count": 0, "items": []},
    })
    assert collect_issue_counts(tmp_path, OWNER, NAME, now=NOW, transport=fake) == "ok"
    data = read_env(tmp_path, "issues_counts", KEY).data
    assert data["closed_90d"] == 0
    assert data["updated_90d"] == 0
    assert data["comments_per_issue"] == 0.0


# --- runs collector -------------------------------------------------------------

def test_ci_pass_rate_exact_and_empty():
    runs = [{"conclusion": c} for c in
            ["success", "success", "failure", "success", "neutral",
             "cancelled", "success", "timed_out", "failure", "failure"]]
    assert ci_pass_rate(runs) == 0.4
    assert ci_pass_rate([]) is None
    assert ci_pass_rate([{"conclusion": c} for c in ["success"] * 5]) == 1.0


def test_collect_runs_records_conclusions_on_branch(tmp_path):
    conclusions = (["success"] * 7 + ["failure", "neutral"])
    fake = FakeRest({
        ("repos/acme/widget/actions/runs", frozenset({
            "branch": "main", "status": "completed", "per_page": 30}.items())):
            {"workflow_runs": [
                {"created_at": "2026-09-0%dT00:00:00Z" % (i % 9 + 1),
                 "conclusion": c, "event": "push"} for i, c in enumerate(conclusions)]},
    })
    assert collect_runs(tmp_path, OWNER, NAME, "main", now=NOW, transport=fake) == "ok"
    data = read_env(tmp_path, "runs", KEY).data
    assert data["branch"] == "main"
    assert data["completed"] == 9
    assert data["passed"] == 7
    assert data["ci_pass_rate"] == round(7 / 9, 4)
    assert [r["conclusion"] for r in data["runs"]] == conclusions
    assert fake.calls[0][1] == {"branch": "main", "status": "completed", "per_page": 30}


def test_collect_runs_empty_repo_records_zero_ci(tmp_path):
    fake = FakeRest({
        ("repos/acme/widget/actions/runs", frozenset({
            "branch": "main", "status": "completed", "per_page": 30}.items())):
            {"workflow_runs": []},
    })
    assert collect_runs(tmp_path, OWNER, NAME, "main", now=NOW, transport=fake) == "ok"
    data = read_env(tmp_path, "runs", KEY).data
    assert data["completed"] == 0
    assert data["ci_pass_rate"] is None


# --- 4.1 pipeline defenses -------------------------------------------------------

def _pr(number: int, merged: bool, author: str, reviews: list) -> dict:
    return {
        "number": number, "createdAt": "2026-08-01T00:00:00Z",
        "closedAt": "2026-08-02T00:00:00Z",
        "mergedAt": "2026-08-02T01:00:00Z" if merged else None,
        "state": "MERGED" if merged else "CLOSED",
        "author": {"__typename": "User", "login": author, "createdAt": None},
        "authorAssociation": "NONE",
        "reviews": {"nodes": reviews},
        "comments": {"nodes": []},
        "reviewThreads": {"totalCount": 0},
    }


def _review(login: str, state: str, bot: bool = False) -> dict:
    return {"author": {"__typename": "Bot" if bot else "User", "login": login},
            "state": state, "body": "", "createdAt": "2026-08-01T06:00:00Z",
            "authorAssociation": "MEMBER", "comments": {"totalCount": 0}}


MERCHANT_PRS = [
    _pr(1, True, "alice", [_review("bob", "APPROVED")]),
    _pr(2, True, "carol", [_review("carol", "APPROVED"), _review("dave", "COMMENTED")]),
    _pr(3, True, "dave", [_review("erin", "APPROVED")]),
    _pr(4, True, "frank", []),
    _pr(5, False, "grace", [_review("henry", "APPROVED")]),
]
RUNS_DATA = {"branch": "main", "limit": 30,
             "runs": [{"created_at": f"2026-09-0{i % 9 + 1}T00:00:00Z",
                       "conclusion": c, "event": "push"}
                      for i, c in enumerate(
                          ["success"] * 7 + ["failure", "neutral", "failure"])],
             "completed": 10, "passed": 7, "ci_pass_rate": 0.7}


def test_code_review_rate_counts_the_merged_prs_of_a_reply_sample_only():
    """RFC-0013: a merged PR outside a window's reply sample has no reviews recorded; counting it would read it as
    merged without approval."""
    from porchlamp.collect.github_graphql import REPLIES_RECORDED_KEY

    sampled = [_pr(i, True, f"a{i}", [_review("bob", "APPROVED")]) for i in range(1, 5)]
    unsampled = [{**{k: v for k, v in _pr(i, True, f"a{i}", []).items() if k != "reviews"}, REPLIES_RECORDED_KEY: False}
                 for i in range(5, 11)]
    assert pike.code_review_rate(sampled + unsampled) == 1.0


def test_the_review_ci_proxy_is_refused_on_a_pr_record_cut_short_of_its_window():
    """#51 rule 5: the proxy is not computed on the newest weeks of a cut record; the same record uncut is."""
    prs = [{**pr, "createdAt": (NOW - timedelta(days=10)).isoformat()} for pr in MERCHANT_PRS]
    record = {"window_anchor": NOW.isoformat(), "window_days": 180, "truncated": True, "prs": prs}
    args = list(_stale_check_args(13))
    args[-6:] = [RUNS_DATA, "2026-01-01T00:00:00Z", None, None, record, "2026-01-01T00:00:00Z"]
    out = pike.derive_w17_fields(*args)
    assert out["pipeline_defenses"] == (None, None, None, "collector_failure")
    assert out["pipeline_defenses_detail"][0] == "pr_window envelope is truncated before reaching the 180d window start"
    args[-2] = {**record, "truncated": False}
    assert pike.derive_w17_fields(*args)["pipeline_defenses"][3] == "derived"


def test_code_review_rate_counts_non_author_approvals_on_merged_prs():
    # 4 merged PRs; approvals: #1 by bob (non-author), #2 by carol (the author),
    # #3 by erin (non-author), #4 none; #5 is not merged -> 2/4 = 0.5.
    assert pike.code_review_rate(MERCHANT_PRS) == 0.5
    assert pike.code_review_rate([]) == 0.0
    assert pike.has_non_author_approval(
        _pr(1, True, "alice", [_review("alice", "APPROVED")])) is False
    assert pike.has_non_author_approval(
        _pr(1, True, "alice", [_review("bob", "APPROVED")])) is True
    # a non-author approval still counts when the PR author is a bot account
    assert pike.has_non_author_approval(
        _pr(1, True, "dependabot", [_review("alice", "APPROVED")])) is True


def test_has_non_author_approval_excludes_bot_reviewers():
    # An automated approval (a CI bot, not a human reviewer) is not a code
    # review: 02 §4 4.1 measures human review depth.
    assert pike.has_non_author_approval(
        _pr(1, True, "alice", [_review("github-actions", "APPROVED", bot=True)])) is False
    assert pike.has_non_author_approval(
        _pr(1, True, "alice", [_review("dependabot[bot]", "APPROVED")])) is False


def test_scorecard_subset_mean_uses_only_the_five_named_checks():
    checks = {"Code-Review": 9, "CI-Tests": 10, "Branch-Protection": 7,
              "Maintained": 10, "Dangerous-Workflow": 5, "Token-Permissions": 10}
    assert pike.scorecard_subset_mean(checks) == pytest.approx(41 / 5, abs=1e-12)
    checks["Code-Review"] = None
    assert pike.scorecard_subset_mean(checks) == pytest.approx(32 / 4, abs=1e-12)
    assert pike.scorecard_subset_mean({k: None for k in pike.SCORECARD_CHECKS}) is None
    assert pike.scorecard_subset_mean({}) is None


def test_pipeline_defenses_prefers_scorecard_subset():
    sc = {"score": 8.0, "checks": {"Code-Review": 9, "CI-Tests": 10,
                                   "Branch-Protection": 7, "Maintained": 10,
                                   "Dangerous-Workflow": 5}}
    runs = RUNS_DATA["runs"]
    value, method = pike.pipeline_defenses(sc, MERCHANT_PRS, runs)
    assert method == pike.METHOD_PRIMARY
    assert method == "primary"
    assert value == pytest.approx(41 / 5 * 10, abs=1e-9)


def test_pipeline_defenses_proxy_review_ci():
    runs = RUNS_DATA["runs"]
    value, method = pike.pipeline_defenses(None, MERCHANT_PRS, runs)
    assert method == pike.METHOD_PROXY
    assert method == "proxy:review_ci"
    # 0.6 * 0.5 * 100 + 0.4 * 0.7 * 100 = 30 + 28 = 58
    assert value == pytest.approx(58.0, abs=1e-9)


def test_pipeline_defenses_proxy_zero_ci_when_no_runs():
    value, method = pike.pipeline_defenses(None, MERCHANT_PRS, [])
    assert method == pike.METHOD_PROXY
    # 0.6 * 0.5 * 100 + 0.4 * 0 = 30
    assert value == pytest.approx(30.0, abs=1e-9)


def test_pipeline_defenses_unmeasured_when_inputs_absent():
    assert pike.pipeline_defenses(None, None, RUNS_DATA["runs"]) == (None, None)
    assert pike.pipeline_defenses(None, MERCHANT_PRS, None) == (None, None)
    # a scorecard envelope whose five checks are all null falls back to the proxy
    sc = {"score": None, "checks": {k: None for k in pike.SCORECARD_CHECKS}}
    assert pike.pipeline_defenses(sc, MERCHANT_PRS, RUNS_DATA["runs"])[1] == pike.METHOD_PROXY


# --- 2.2 pike signals: the derived mapping ---------------------------------------

def _commit_rows():
    """520 human commits: 4 orgs (a..d), 100 authors, one at the window edge."""
    rows = []
    plan = [("a.com", 50, 260), ("b.com", 30, 120), ("c.com", 15, 90), ("d.com", 5, 50)]
    for domain, n_authors, n_commits in plan:
        base, extra = divmod(n_commits, n_authors)
        for author in range(n_authors):
            for _ in range(base + (1 if author < extra else 0)):
                rows.append({"date": "2025-09-12T00:00:00Z",  # now - 365 d
                             "author_email": f"dev{author}@{domain}",
                             "author_login": "", "author_type": "User"})
    assert len(rows) == 520
    return rows


def _seed_pike_inputs(raw: Path, include_releases: bool = True,
                      scorecard: Optional[dict] = None) -> None:
    write_env(raw, "github_meta", KEY, {
        "stars": 100, "forks": 10, "pushed_at": NOW.isoformat(), "archived": False,
        "created_at": (NOW - timedelta(days=3650)).isoformat(),
        "license_spdx": "MIT", "default_branch": "main"})
    write_env(raw, "commits_365d", KEY, _commit_rows())
    if include_releases:
        write_env(raw, "releases", KEY, {
            "window_days": 365, "source": "releases", "releases_truncated": False,
            "tags": None,
            "releases": [_rel(f"v{i}", date) for i, date in enumerate(
                ["2025-08-01T00:00:00Z", "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z",
                 "2026-03-01T00:00:00Z", "2026-04-01T00:00:00Z", "2026-05-01T00:00:00Z"])],
            "recent_count": 5})
    write_env(raw, "issues_counts", KEY, {
        "window_days": 90, "closed_90d": 42, "updated_90d": 120,
        "sample_size": 30, "sample_total": 30, "sample": [], "comments_per_issue": 4.0})
    write_env(raw, "dependents", KEY, {
        "ecosystem": "pypi", "package": "widget", "system": "pypi", "version": "1.0",
        "dependent_count": 1000, "direct_dependent_count": 50,
        "indirect_dependent_count": 950, "deduped_count": 1000, "dependents": None,
        "source_used": "deps.dev"})
    write_env(raw, "pr_window", KEY, {
        "window_anchor": NOW.isoformat(), "window_days": 180, "cap": 500,
        "count": 5, "truncated": False, "cost_points": 5,
        "prs": MERCHANT_PRS, "derived": []})
    write_env(raw, "runs", KEY, RUNS_DATA)
    if scorecard is not None:
        write_env(raw, "scorecard", KEY, scorecard)


_PIKE_EXPECTED_C = (
    1.0 * math.log(121.0) / math.log(121.0)
    - 1.0 * math.log(13.0) / math.log(121.0)
    + 2.0 * math.log(101.0) / math.log(5001.0)
    + 1.0 * math.log(5.0) / math.log(11.0)
    + 1.0 * math.log(11.0) / math.log(1001.0)
    + 0.5 * math.log(6.0) / math.log(27.0)
    + 0.5 * math.log(43.0) / math.log(5001.0)
    + 0.5 * math.log(121.0) / math.log(5001.0)
    + 1.0 * math.log(5.0) / math.log(16.0)
    + 2.0 * math.log(1001.0) / math.log(500001.0)
) / 10.5


def test_derive_pike_signals_oracle_hand_built():
    result = pike.derive_pike_signals(
        {"created_at": (NOW - timedelta(days=3650)).isoformat()},
        _commit_rows(),
        {"source": "releases", "releases": [
            _rel(f"v{i}", d) for i, d in enumerate(
                ["2025-08-01T00:00:00Z", "2026-01-01T00:00:00Z",
                 "2026-02-01T00:00:00Z", "2026-03-01T00:00:00Z",
                 "2026-04-01T00:00:00Z", "2026-05-01T00:00:00Z"])]},
        {"closed_90d": 42, "updated_90d": 120, "comments_per_issue": 4.0},
        {"deduped_count": 1000}, NOW)
    assert result is not None
    signals, missing = result
    assert missing == frozenset()          # all ten present
    assert signals["created_since_months"] == 120.0        # 3650 d / (365/12)
    assert signals["updated_since_months"] == 12.0         # 365 d at the window edge
    assert signals["contributor_count"] == 100
    assert signals["org_count"] == 4
    assert signals["commit_frequency_weekly"] == 10.0     # 520 / 52
    assert signals["recent_releases_count"] == 5          # the out-of-window v0 dropped
    assert signals["closed_issues_count_90d"] == 42
    assert signals["updated_issues_count_90d"] == 120
    assert signals["comment_frequency"] == 4.0
    assert signals["dependents_count"] == 1000
    # Independent hand-computation of spec 10.1 for these raw values
    # (weights +1,-1,+2,+1,+1,+0.5,+0.5,+0.5,+1,+2; sum|alpha| = 10.5):
    assert calculate_pike_criticality(OpenSSFSignals(**signals)) == pytest.approx(_PIKE_EXPECTED_C, abs=1e-12)
    from porchlamp.core.openssf import calculate_pike_criticality as core_calculate
    assert core_calculate(signals) == pytest.approx(_PIKE_EXPECTED_C, abs=1e-12)
    assert score_openssf_criticality(OpenSSFSignals(**signals)) == pytest.approx(_PIKE_EXPECTED_C * 100.0, abs=1e-9)


def test_derive_pike_signals_missing_any_source_is_none():
    args = (
        {"created_at": (NOW - timedelta(days=3650)).isoformat()},
        _commit_rows(),
        {"source": "releases", "releases": []},
        {"closed_90d": 42, "updated_90d": 120, "comments_per_issue": 4.0},
        {"deduped_count": 1000},
    )
    assert pike.derive_pike_signals(None, *args[1:], NOW) is None
    assert pike.derive_pike_signals(args[0], None, *args[2:], NOW) is None
    assert pike.derive_pike_signals(args[0], args[1], None, *args[3:], NOW) is None
    assert pike.derive_pike_signals(*args[:3], None, args[4], NOW) is None
    # commits envelope present but empty (a recorded zero-commit repo)
    # and github_meta carries no pushed_at: has nothing to date itself from,
    # so the whole set is None.
    assert pike.derive_pike_signals(args[0], [], *args[2:], NOW) is None


def test_derive_pike_signals_missing_dependents_is_a_nine_signal_partial():
    """Dependents is not hard-required: its absence returns the other nine
    signals plus {"dependents_count"} in the missing set, not None."""
    args = (
        {"created_at": (NOW - timedelta(days=3650)).isoformat()},
        _commit_rows(),
        {"source": "releases", "releases": []},
        {"closed_90d": 42, "updated_90d": 120, "comments_per_issue": 4.0},
    )
    result = pike.derive_pike_signals(*args, None, NOW)
    assert result is not None
    signals, missing = result
    assert missing == frozenset({"dependents_count"})
    assert "dependents_count" not in signals
    assert signals["created_since_months"] == 120.0
    assert signals["contributor_count"] == 100

    # Same result whether dep is entirely absent or present with a null count.
    result_null_count = pike.derive_pike_signals(*args, {"deduped_count": None}, NOW)
    assert result_null_count == result


def test_derive_pike_signals_zero_commits_is_measured_zero_via_pushed_at():
    """An empty commits_365d envelope from a successful fetch (c365 == [], not None)
    is a measured zero for contributor_count/org_count/commit_frequency_weekly;
    updated_since_months falls back to github_meta.pushed_at."""
    gh = {"created_at": (NOW - timedelta(days=3650)).isoformat(),
         "pushed_at": (NOW - timedelta(days=30)).isoformat()}
    result = pike.derive_pike_signals(
        gh, [],
        {"source": "releases", "releases": []},
        {"closed_90d": 0, "updated_90d": 0, "comments_per_issue": 0.0},
        {"deduped_count": 0}, NOW)
    assert result is not None
    signals, missing = result
    assert missing == frozenset()
    assert signals["contributor_count"] == 0
    assert signals["org_count"] == 0
    assert signals["commit_frequency_weekly"] == 0.0
    assert signals["updated_since_months"] == round(
        pike.months_between(pike.parse_iso(gh["pushed_at"]), NOW), 4)
    assert signals["updated_since_months"] == pytest.approx(30.0 * 12.0 / 365.0, abs=1e-4)


def test_derive_pike_signals_missing_commits_envelope_is_still_unmeasured():
    """The other side of the F2 distinction: c365 is None (no envelope at
    all -- a collector failure, not a repo-controlled zero) stays
    unmeasured even with pushed_at present, proving the fallback triggers
    only on a genuinely EMPTY successful fetch, never on an absent one."""
    gh = {"created_at": (NOW - timedelta(days=3650)).isoformat(),
         "pushed_at": (NOW - timedelta(days=30)).isoformat()}
    signals = pike.derive_pike_signals(
        gh, None,
        {"source": "releases", "releases": []},
        {"closed_90d": 0, "updated_90d": 0, "comments_per_issue": 0.0},
        {"deduped_count": 0}, NOW)
    assert signals is None


def test_derive_repo_pike_signals_from_envelopes(tmp_path):
    _seed_pike_inputs(tmp_path / "raw")
    bundle = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path,
                                fetch=False, now=NOW)
    meta = bundle["meta"]
    sig = meta["openssf_signals"]
    assert sig == {
        "created_since_months": 120.0,
        "updated_since_months": 12.0,
        "contributor_count": 100,
        "org_count": 4,
        "commit_frequency_weekly": 10.0,
        "recent_releases_count": 5,
        "closed_issues_count_90d": 42,
        "updated_issues_count_90d": 120,
        "comment_frequency": 4.0,
        "dependents_count": 1000,
    }
    assert bundle["confidence"]["openssf_signals"] == "derived"
    signals = OpenSSFSignals(**sig)
    # the pillar parameter must be exactly the hand-computed C x 100
    assert score_openssf_criticality(signals) == pytest.approx(_PIKE_EXPECTED_C * 100.0, abs=1e-9)
    assert bundle["provenance"]["openssf_signals"]["source"] == [
        f"raw/{c}/{KEY}.json"
        for c in ("github_meta", "commits_365d", "releases", "issues_counts", "dependents")
    ]


def test_derive_repo_without_scorecard_gets_proxy_review_ci(tmp_path):
    _seed_pike_inputs(tmp_path / "raw")
    bundle = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path,
                                fetch=False, now=NOW)
    meta = bundle["meta"]
    assert meta["pipeline_defenses"] == 58.0
    assert meta["pipeline_defenses_method"] == "proxy:review_ci"
    assert bundle["confidence"]["pipeline_defenses"] == "derived"
    assert bundle["provenance"]["pipeline_defenses"]["source"] == [
        f"raw/{c}/{KEY}.json" for c in ("pr_window", "runs")]
    # the v1 scorecard_score stays missing (0.0) — the measured proxy is new
    assert meta["scorecard_score"] == 0.0
    assert bundle["confidence"]["scorecard_score"] == "missing"


def test_derive_repo_scorecard_subset_wins_over_proxy(tmp_path):
    _seed_pike_inputs(tmp_path / "raw", scorecard={
        "score": 8.0,
        "checks": {"Code-Review": 9, "CI-Tests": 10, "Branch-Protection": 7,
                   "Maintained": 10, "Dangerous-Workflow": 5, "Token-Permissions": 10}})
    bundle = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path,
                                fetch=False, now=NOW)
    meta = bundle["meta"]
    assert meta["pipeline_defenses"] == 82.0       # (9+10+7+10+5)/5 x 10, 6th check ignored
    assert meta["pipeline_defenses_method"] == "primary"
    assert bundle["provenance"]["pipeline_defenses"]["source"] == [
        f"raw/scorecard/{KEY}.json"]


def test_derive_repo_partial_signals_leaves_openssf_missing(tmp_path):
    _seed_pike_inputs(tmp_path / "raw", include_releases=False, scorecard={
        "score": 8.0,
        "checks": {"Code-Review": 9, "CI-Tests": 10, "Branch-Protection": 7,
                   "Maintained": 10, "Dangerous-Workflow": 5}})
    bundle = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path,
                                fetch=False, now=NOW)
    meta = bundle["meta"]
    assert meta["openssf_signals"] is None
    assert bundle["confidence"]["openssf_signals"] == "missing"
    # 4.1 is independent of the signal set and stays measured
    assert meta["pipeline_defenses"] == 82.0


def test_derive_repo_missing_dependents_leaves_openssf_missing(tmp_path):
    """When dependents are absent, the full ten-signal ``openssf_signals``
    remains missing, while the nine present signals populate
    ``openssf_signals_partial``."""
    raw = tmp_path / "raw"
    _seed_pike_inputs(raw)
    (raw / "dependents" / f"{KEY}.json").unlink()
    bundle = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path,
                                fetch=False, now=NOW)
    assert bundle["meta"]["openssf_signals"] is None
    assert bundle["confidence"]["openssf_signals"] == "missing"
    partial = bundle["meta"]["openssf_signals_partial"]
    assert bundle["confidence"]["openssf_signals_partial"] == "derived"
    assert "dependents_count" not in partial
    assert partial["created_since_months"] == 120.0
    assert bundle["meta"]["openssf_signals_partial_missing"] == ["dependents_count"]
    assert bundle["provenance"]["openssf_signals_partial"]["source"] == [
        f"raw/{c}/{KEY}.json"
        for c in ("github_meta", "commits_365d", "releases", "issues_counts")
    ]


def test_derive_repo_no_ci_runs_scores_zero_in_proxy(tmp_path):
    _seed_pike_inputs(tmp_path / "raw", include_releases=False)
    write_env(tmp_path / "raw", "runs", KEY,
              {"branch": "main", "limit": 30, "runs": [], "completed": 0,
               "passed": 0, "ci_pass_rate": None})
    bundle = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path,
                                fetch=False, now=NOW)
    assert bundle["meta"]["pipeline_defenses"] == 30.0   # 0.6*0.5*100 + 0.4*0
    assert bundle["meta"]["pipeline_defenses_method"] == "proxy:review_ci"


def test_derive_repo_without_inputs_leaves_pike_fields_absent(tmp_path):
    bundle = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path,
                                fetch=False, now=NOW)
    meta = bundle["meta"]
    assert "pipeline_defenses" not in meta
    assert "pipeline_defenses_method" not in meta
    assert meta["openssf_signals"] is None
    assert bundle["confidence"]["openssf_signals"] == "missing"


# --- Guard against empty timestamps in max(ats) --------------------------------

def test_derive_repo_pike_signals_survive_envelopes_with_no_fetched_at(tmp_path):
    """Verify `max(ats)` handles empty timestamp lists when envelopes omit
    fetched_at/collected_at across prerequisite envelopes."""
    raw = tmp_path / "raw"
    _seed_pike_inputs(raw)
    for collector in ("github_meta", "commits_365d", "releases", "issues_counts", "dependents"):
        p = raw / collector / f"{KEY}.json"
        doc = json.loads(p.read_text(encoding="utf-8"))
        doc.pop("fetched_at", None)
        doc.pop("collected_at", None)
        p.write_text(json.dumps(doc), encoding="utf-8")
    bundle = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path,
                                fetch=False, now=NOW)
    assert bundle["meta"]["openssf_signals"] is not None
    assert bundle["confidence"]["openssf_signals"] == "derived"
    assert bundle["provenance"]["openssf_signals"]["fetched_at"] is None


# --- Hook-level crash guard ----------------------------------------------------

def test_derive_repo_pike_hook_crash_is_collector_failure_not_a_lost_bundle(tmp_path, monkeypatch):
    """The collector must never let one repo's failure abort the sweep: a
    crash inside derive_w17_fields becomes a recorded
    Unmeasured(collector_failure) naming the exception type, and derive_repo
    returns the rest of the bundle completely unaffected."""
    _seed_pike_inputs(tmp_path / "raw")

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("simulated pike_signals crash")

    monkeypatch.setattr(pike, "derive_w17_fields", boom)
    bundle = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path,
                                fetch=False, now=NOW)
    assert bundle["confidence"]["openssf_signals"] == "collector_failure"
    assert "RuntimeError" in bundle["meta"]["openssf_signals_detail"]
    assert bundle["confidence"]["pipeline_defenses"] == "collector_failure"
    assert "RuntimeError" in bundle["meta"]["pipeline_defenses_detail"]
    # the rest of the bundle is untouched by the crash.
    assert bundle["meta"]["stars"] == 100 and bundle["confidence"]["stars"] == "verified"
    assert bundle["meta"]["dependents_count"] == 1000


def test_gather_sweep_continues_after_one_repo_w17_collector_crash(monkeypatch, tmp_path):
    """The gather-side half of the same rule: a fake transport that raises
    on the SECOND of three repos must not stop the third from being
    collected. gather_repo's existing per-collector try/except already
    isolates a crash to one repo's own status dict; this pins that it holds
    across a real sequence of repos, not just within a single call."""
    gather.DATA = tmp_path / "raw"
    for repo_name in ("repo1", "repo2fail", "repo3"):
        seed_gather_done(gather.DATA, key=f"acme__{repo_name}")

    class SelectiveFailTransport:
        def get(self, path: str, **params: Any) -> Any:
            # collect_issue_counts embeds owner/name in the search query
            # (params["q"]), not in `path` itself -- check both.
            haystack = " ".join([path, *(str(v) for v in params.values())])
            if "repo2fail" in haystack:
                raise RuntimeError("simulated transport failure")
            return {}

    shared = SelectiveFailTransport()
    monkeypatch.setattr(pike, "GhRestTransport", lambda *a, **k: shared)

    statuses = {}
    for repo_name in ("repo1", "repo2fail", "repo3"):
        statuses[repo_name] = gather.gather_repo("acme", repo_name, "pypi")

    for collector in ("releases", "issues_counts", "runs"):
        assert statuses["repo1"][collector] == "ok"
        assert statuses["repo2fail"][collector] == "fail"
        assert statuses["repo3"][collector] == "ok"          # <- the third still collected
        assert (gather.DATA / collector / "acme__repo1.json").is_file()
        assert (gather.DATA / collector / "acme__repo3.json").is_file()


# --- Windowed-count staleness ---------------------------------------------------

def _stale_check_args(anchor_days_ago: float) -> tuple:
    anchor = (NOW - timedelta(days=anchor_days_ago)).isoformat()
    return (
        Path("/unused"), KEY, NOW,
        {"created_at": (NOW - timedelta(days=3650)).isoformat()}, "2026-01-01T00:00:00Z",
        _commit_rows(), "2026-01-01T00:00:00Z",
        {"deduped_count": 1000}, "2026-01-01T00:00:00Z",
        {"source": "releases", "releases": [], "window_anchor": anchor}, "2026-01-01T00:00:00Z",
        {"closed_90d": 0, "updated_90d": 0, "comments_per_issue": 0.0}, "2026-01-01T00:00:00Z",
        None, None, None, None, None, None,
    )


def test_derive_w17_fields_releases_window_13_days_is_fresh():
    out = pike.derive_w17_fields(*_stale_check_args(13))
    assert out["openssf_signals"][3] == "derived"
    assert "openssf_signals_detail" not in out


def test_derive_w17_fields_releases_window_exactly_14_days_is_still_fresh():
    """Boundary: '> 14 days' is the rule, not '>= 14 days' -- kills a
    >= mutant on the comparison that 13/15-day tests alone would not."""
    out = pike.derive_w17_fields(*_stale_check_args(14))
    assert out["openssf_signals"][3] == "derived"
    assert "openssf_signals_detail" not in out


def test_derive_w17_fields_releases_window_15_days_is_stale():
    out = pike.derive_w17_fields(*_stale_check_args(15))
    assert out["openssf_signals"] == (None, None, None, "collector_failure")
    detail = out["openssf_signals_detail"][0]
    assert "releases window anchored at" in detail
    assert "as_of" in detail


# --- src()/env_path() shared path formula --------------------------------------

def test_src_and_env_path_share_one_definition():
    from porchlamp.collect import envelope as envelope_mod

    assert derive.raw_envelope_path is envelope_mod.raw_envelope_path
    assert pike.raw_envelope_path is envelope_mod.raw_envelope_path


def test_src_and_env_path_both_route_through_the_shared_function(tmp_path, monkeypatch):
    """A behavioural guard, not just an identity check: patches the name
    BOTH modules import and proves scripts/derive.py's src() (a
    derive.py-only collector, "tree") and porchlamp.collect.pike_signals'
    env_path() (an openssf_signals-only collector, "dependents") each
    actually call through it -- neither closure kept its own inline
    '<data_dir>/raw/<collector>/<key>.json' formula."""
    calls: list[tuple[str, str, str]] = []
    real = derive.raw_envelope_path

    def spy(data_dir: Any, collector: str, key: str) -> str:
        calls.append((str(data_dir), collector, key))
        return real(data_dir, collector, key)

    monkeypatch.setattr(derive, "raw_envelope_path", spy)
    monkeypatch.setattr(pike, "raw_envelope_path", spy)
    _seed_pike_inputs(tmp_path / "raw")
    derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path, fetch=False, now=NOW)
    collectors_seen = {c for (_, c, _) in calls}
    assert "tree" in collectors_seen            # src()-only site
    assert "dependents" in collectors_seen      # env_path()-only site (openssf_signals)


# --- the append-only gather hook ------------------------------------------------

GATHER_DONE = {
    "github_meta": {"stars": 1, "forks": 0, "pushed_at": "2026-01-01T00:00:00Z",
                    "archived": False, "created_at": "2020-01-01T00:00:00Z",
                    "license_spdx": "MIT", "default_branch": "main"},
    "pr_events": [],
    "pr_reviews": {},
    "contributors": [],
    "gfi": [{"labels_matched": [], "queries": []}],
    "actions_runs": [],
    "tree": {"paths": ["pyproject.toml"], "truncated": False},
    "commits_180d": [],
    "depsdev": None,
    "scorecard": None,
    # Pre-seed other collectors to prevent live network calls:
    "commits_365d": [], "dependents": {}, "pr_window": {}, "governance": {},
    "author_history": {},
}


def seed_gather_done(raw: Path, key: str = KEY) -> None:
    name = key.split("__", 1)[1] if "__" in key else NAME
    for collector, data in GATHER_DONE.items():
        write_env(raw, collector, key, data)
    write_env(raw, "downloads", f"pypi__{name}", {"monthly": 10})
    write_env(raw, "repo_object", key, {"archived": False, "mirror_url": None,
                                        "fork": False, "license_spdx_id": "MIT",
                                        "pull_requests_total": 12,
                                        "tree_paths_le2": ["pyproject.toml"],
                                        "tree_truncated": False,
                                        "offplatform_paths": []})
    # Pre-seed envelopes to keep test execution fully offline without network attempts:
    write_env(raw, "commits_365d", key, [])
    write_env(raw, "dependents", key, {"deduped_count": 0, "package": name, "guess_linked": True})
    write_env(raw, "pr_window", key, {"window_anchor": "2026-01-01T00:00:00Z",
                                      "window_days": 180, "prs": [], "derived": []})


def test_gather_repo_w17_collectors_ok(tmp_path, monkeypatch, block_live_network):
    gather.DATA = tmp_path / "raw"
    seed_gather_done(gather.DATA)
    monkeypatch.setattr(pike, "collect_releases", lambda *a, **k: "ok")
    monkeypatch.setattr(pike, "collect_issue_counts", lambda *a, **k: "ok")
    monkeypatch.setattr(pike, "collect_runs", lambda *a, **k: "ok")
    status = gather.gather_repo(OWNER, NAME, "pypi")
    assert status["releases"] == "ok"
    assert status["issues_counts"] == "ok"
    assert status["runs"] == "ok"
    assert status["github_meta"] == "skip"


def test_gather_repo_w17_collectors_skip(tmp_path, monkeypatch, block_live_network):
    gather.DATA = tmp_path / "raw"
    seed_gather_done(gather.DATA)
    for collector in ("releases", "issues_counts", "runs"):
        write_env(gather.DATA, collector, KEY, {"recent_count": 0})

    def must_not_fetch(*a, **k):
        raise AssertionError("collector must be skipped when the envelope exists")

    monkeypatch.setattr(pike, "collect_releases", must_not_fetch)
    monkeypatch.setattr(pike, "collect_issue_counts", must_not_fetch)
    monkeypatch.setattr(pike, "collect_runs", must_not_fetch)
    status = gather.gather_repo(OWNER, NAME, "pypi")
    assert status["releases"] == "skip"
    assert status["issues_counts"] == "skip"
    assert status["runs"] == "skip"


def test_gather_repo_w17_collectors_fail(tmp_path, monkeypatch, block_live_network):
    gather.DATA = tmp_path / "raw"
    seed_gather_done(gather.DATA)

    def raise_transport_error(*a, **k):
        raise RuntimeError("gh api rc=1: rate limited")

    monkeypatch.setattr(pike, "collect_releases", raise_transport_error)
    monkeypatch.setattr(pike, "collect_issue_counts", raise_transport_error)
    monkeypatch.setattr(pike, "collect_runs", raise_transport_error)
    status = gather.gather_repo(OWNER, NAME, "pypi")
    assert status["releases"] == "fail"
    assert status["issues_counts"] == "fail"
    assert status["runs"] == "fail"


# --- Single-call GraphQL issues_counts collector -----------------------------


def _iss(number: int, *, state: str, updated: str, closed: Optional[str] = None,
         comments: int = 0) -> dict:
    return {"number": number, "state": state, "updated_at": updated,
            "closed_at": closed, "comments": comments}


#: The window is [2026-06-14, 2026-09-12] (anchor NOW, 90 days). Issue 3 was closed
#: BEFORE the window and updated (commented on) inside it; issue 4 is closed inside
#: it; issue 5 was closed inside it and reopened (open now); 6 is open and updated.
ISSUES = [
    _iss(1, state="closed", updated="2026-09-11T00:00:00Z", closed="2026-09-11T00:00:00Z", comments=4),
    _iss(2, state="closed", updated="2026-08-01T00:00:00Z", closed="2026-07-01T00:00:00Z", comments=2),
    _iss(3, state="closed", updated="2026-07-20T00:00:00Z", closed="2026-05-01T00:00:00Z", comments=1),
    _iss(4, state="closed", updated="2026-06-14T00:00:00Z", closed="2026-06-14T00:00:00Z", comments=0),
    _iss(5, state="open", updated="2026-09-10T00:00:00Z", closed="2026-08-20T00:00:00Z", comments=7),
    _iss(6, state="open", updated="2026-09-12T00:00:00Z", comments=3),
    _iss(7, state="closed", updated="2025-01-01T00:00:00Z", closed="2025-01-01T00:00:00Z", comments=9),
]


class FakeGitHubIssues:
    """One in-memory repository answering BOTH the Search REST API and the GraphQL
    ``issues_counts`` query with the semantics GitHub documents, so the two collectors
    can be compared on the same issues. Search qualifiers are parsed from the query
    string the collector sends: a mutated qualifier changes the count."""

    def __init__(self, issues: list[dict]) -> None:
        self.issues = issues
        self.gql_variables: list[dict] = []

    def _search(self, q: str) -> list[dict]:
        out = list(self.issues)
        if "is:closed" in q:
            out = [i for i in out if i["state"] == "closed"]
        elif "is:open" in q:
            out = [i for i in out if i["state"] == "open"]
        m = re.search(r"closed:>=(\d{4}-\d{2}-\d{2})", q)
        if m:
            out = [i for i in out if i["closed_at"] and i["closed_at"][:10] >= m.group(1)]
        m = re.search(r"updated:>=(\d{4}-\d{2}-\d{2})", q)
        if m:
            out = [i for i in out if i["updated_at"][:10] >= m.group(1)]
        return out

    # REST search/issues
    def get(self, path: str, **params: Any) -> Any:
        assert path == "search/issues"
        found = self._search(params["q"])
        if params.get("sort") == "updated":
            found = sorted(found, key=lambda i: i["updated_at"], reverse=True)
        return {"total_count": len(found), "items": found[: params["per_page"]]}

    # GraphQL
    def execute(self, query: str, variables=None) -> dict:
        v = dict(variables or {})
        self.gql_variables.append(v)
        # `issues(filterBy: {since})` filters on updatedAt; `states` filters on state.
        updated = [i for i in self.issues if i["updated_at"] >= v["since"]]
        recent = sorted(self.issues, key=lambda i: i["updated_at"], reverse=True)
        closed_conn: dict[str, Any] = {"issueCount": len(self._search(v["closedQuery"]))}
        return {"data": {
            "closed90d": closed_conn,
            "repository": {
                "updated90d": {"totalCount": len(updated)},
                "sample": {"totalCount": len(self.issues), "nodes": [
                    {"number": i["number"], "updatedAt": i["updated_at"],
                     "comments": {"totalCount": i["comments"]}}
                    for i in recent[: v["sampleSize"]]]},
            }}}


def test_collect_issue_counts_graphql_single_call_matches_search_envelope(tmp_path):
    """One GraphQL call replaces 3 Search API calls (and 6.3s sleep) while writing
    the exact same issues_counts envelope data as the REST path -- ``closed_90d``
    counts issues CLOSED in the window (``is:closed closed:>=D``), not issues
    closed and merely updated in it."""
    fake = FakeGitHubIssues(ISSUES)
    assert pike.collect_issue_counts_graphql(tmp_path / "gql", OWNER, NAME, now=NOW, transport=fake) == "ok"
    assert pike.collect_issue_counts(tmp_path / "rest", OWNER, NAME, now=NOW, transport=fake) == "ok"
    assert len(fake.gql_variables) == 1
    gql = read_env(tmp_path / "gql", "issues_counts", KEY)
    rest = read_env(tmp_path / "rest", "issues_counts", KEY)
    assert gql.source == "github-graphql:issues_counts"
    assert gql.data["closed_90d"] == 3  # issues 1, 2 and 4 were closed inside the window
    assert gql.data == rest.data
    assert gql.data["sample_size"] == 30
    assert gql.data["window_days"] == 90


def test_graphql_closed_90d_uses_the_rest_search_query_string(tmp_path):
    fake = FakeGitHubIssues(ISSUES)
    pike.collect_issue_counts_graphql(tmp_path, OWNER, NAME, now=NOW, transport=fake)
    assert fake.gql_variables[0]["closedQuery"] == issue_q(OWNER, NAME, "closed", "2026-06-14")
    assert "is:closed closed:>=2026-06-14" in fake.gql_variables[0]["closedQuery"]


def test_graphql_closed_90d_differs_from_updated_since_filter(tmp_path):
    """Issue 3 was closed before the window and commented on inside it: the old
    ``issues(filterBy: {since}, states: [CLOSED])`` counted it, the search does not."""
    fake = FakeGitHubIssues(ISSUES)
    pike.collect_issue_counts_graphql(tmp_path, OWNER, NAME, now=NOW, transport=fake)
    env = read_env(tmp_path, "issues_counts", KEY)
    closed_and_updated_in_window = sum(
        1 for i in ISSUES if i["state"] == "closed" and i["updated_at"][:10] >= "2026-06-14")
    assert closed_and_updated_in_window == 4  # issues 1, 2, 3, 4
    assert env.data["closed_90d"] == 3  # issues 1, 2, 4 (closed 2026-09-11, 07-01, 06-14)
    assert env.data["updated_90d"] == 6  # issues 1-6; issue 7 was last touched in 2025


def test_graphql_issue_counts_incomplete_answer_is_a_failure_not_a_zero(tmp_path):
    class _NoSearch:
        def execute(self, query, variables=None):
            return {"data": {"repository": {"updated90d": {"totalCount": 5},
                                            "sample": {"totalCount": 0, "nodes": []}}},
                    "errors": [{"message": "search failed"}]}

    with pytest.raises(RuntimeError, match="incomplete"):
        pike.collect_issue_counts_graphql(tmp_path, OWNER, NAME, now=NOW, transport=_NoSearch())
    assert not (tmp_path / "issues_counts").exists()


def test_code_review_rate_and_pipeline_defenses_proxy_count_landed_prs():
    """RFC-0015 (#60): ``code_review_rate`` counts PRs landed outside GitHub's merge (by closing
    commit or ``LANDING_LABELS``) in its denominator alongside GitHub-merged PRs, and in its
    numerator when approved by a non-author human reviewer; plain-closed unmerged PRs do not count."""
    def _landed_pr(number: int, author: str, reviews: list, *, label: str | None = None,
                   closer: str | None = None) -> dict:
        base = _pr(number, False, author, reviews)
        base["labels"] = {"nodes": [{"name": label}] if label else []}
        base["timelineItems"] = {"nodes": [{
            "__typename": "ClosedEvent",
            "createdAt": base["closedAt"],
            "actor": {"__typename": "User", "login": "bob"},
            "closer": {"__typename": "Commit", "oid": "abc123"} if closer == "Commit" else None,
        }]}
        return base

    prs = [
        _pr(1, True, "alice", [_review("bob", "APPROVED")]),                        # GitHub-merged, approved
        _landed_pr(2, "carol", [_review("dave", "APPROVED")], closer="Commit"),     # landed by commit, approved
        _landed_pr(3, "erin", [], label="Merged"),                                  # landed by label, unapproved
        _landed_pr(4, "frank", [_review("grace", "APPROVED")], label="integrated"),  # landed by label, approved
        _landed_pr(5, "henry", [_review("ivan", "APPROVED")]),                      # plain close: not in denominator
    ]
    # 4 merged-or-landed PRs (#1..#4), 3 approved (#1, #2, #4) -> 3/4 = 0.75
    assert pike.code_review_rate(prs) == 0.75
    value, method = pike.pipeline_defenses(None, prs, RUNS_DATA["runs"])
    assert method == pike.METHOD_PROXY
    # 0.6 * 0.75 * 100 + 0.4 * 0.7 * 100 = 45 + 28 = 73.0
    assert value == pytest.approx(73.0, abs=1e-9)

