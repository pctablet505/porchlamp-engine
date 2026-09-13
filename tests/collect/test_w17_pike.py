"""Tests for W1-7: OpenSSF signals computed locally, scorecard subset, proxies.

Subject: ``cvi.collect.pike_signals`` -- the REST transport boundary, the
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
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

import pytest

from cvi.collect import pike_signals as pike
from cvi.collect.pike_signals import (
    ci_pass_rate,
    collect_issue_counts,
    collect_releases,
    collect_runs,
    comments_per_issue,
    issue_q,
    releases_in_window,
    tags_in_window,
)
from cvi.models import OpenSSFSignals
from cvi.openssf import calculate_pike_criticality
from cvi.pillars.criticality import score_openssf_criticality

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


gather = _load_script("cvi_gather_w17", "gather.py")
derive = _load_script("cvi_derive_w17", "derive.py")


def write_env(raw: Path, collector: str, key: str, data,
              at: str = "2026-09-11T00:00:00Z") -> None:
    from cvi.collect.envelope import write_envelope
    write_envelope(raw / collector / f"{key}.json", source="test", data=data,
                   fetched_at=at)


def read_env(raw: Path, collector: str, key: str):
    from cvi.collect.envelope import read_envelope
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
    signals = pike.derive_pike_signals(
        {"created_at": (NOW - timedelta(days=3650)).isoformat()},
        _commit_rows(),
        {"source": "releases", "releases": [
            _rel(f"v{i}", d) for i, d in enumerate(
                ["2025-08-01T00:00:00Z", "2026-01-01T00:00:00Z",
                 "2026-02-01T00:00:00Z", "2026-03-01T00:00:00Z",
                 "2026-04-01T00:00:00Z", "2026-05-01T00:00:00Z"])]},
        {"closed_90d": 42, "updated_90d": 120, "comments_per_issue": 4.0},
        {"deduped_count": 1000}, NOW)
    assert signals is not None
    assert signals.created_since_months == 120.0        # 3650 d / (365/12)
    assert signals.updated_since_months == 12.0         # 365 d at the window edge
    assert signals.contributor_count == 100
    assert signals.org_count == 4
    assert signals.commit_frequency_weekly == 10.0     # 520 / 52
    assert signals.recent_releases_count == 5          # the out-of-window v0 dropped
    assert signals.closed_issues_count_90d == 42
    assert signals.updated_issues_count_90d == 120
    assert signals.comment_frequency == 4.0
    assert signals.dependents_count == 1000
    # Independent hand-computation of spec 10.1 for these raw values
    # (weights +1,-1,+2,+1,+1,+0.5,+0.5,+0.5,+1,+2; sum|alpha| = 10.5):
    assert calculate_pike_criticality(signals) == pytest.approx(_PIKE_EXPECTED_C, abs=1e-12)
    assert score_openssf_criticality(signals) == pytest.approx(_PIKE_EXPECTED_C * 100.0, abs=1e-9)


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
    assert pike.derive_pike_signals(*args[:4], None, NOW) is None
    # commits envelope present but empty (a real, recorded zero-commit repo):
    # the last-commit date is undefined, so the whole set is still None.
    assert pike.derive_pike_signals(args[0], [], *args[2:], NOW) is None


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
        str(tmp_path / "raw" / c / f"{KEY}.json")
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
        str(tmp_path / "raw" / c / f"{KEY}.json") for c in ("pr_window", "runs")]
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
        str(tmp_path / "raw" / "scorecard" / f"{KEY}.json")]


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
    """One prerequisite (W1-2 dependents) absent -> the whole set is missing,
    never a nine-of-ten partial score (D-MISS-1)."""
    raw = tmp_path / "raw"
    _seed_pike_inputs(raw)
    (raw / "dependents" / f"{KEY}.json").unlink()
    bundle = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path,
                                fetch=False, now=NOW)
    assert bundle["meta"]["openssf_signals"] is None
    assert bundle["confidence"]["openssf_signals"] == "missing"


def test_derive_repo_no_ci_runs_scores_zero_in_proxy(tmp_path):
    _seed_pike_inputs(tmp_path / "raw", include_releases=False)
    write_env(tmp_path / "raw", "runs", KEY,
              {"branch": "main", "limit": 30, "runs": [], "completed": 0,
               "passed": 0, "ci_pass_rate": None})
    bundle = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path,
                                fetch=False, now=NOW)
    assert bundle["meta"]["pipeline_defenses"] == 30.0   # 0.6*0.5*100 + 0.4*0
    assert bundle["meta"]["pipeline_defenses_method"] == "proxy:review_ci"


def test_derive_repo_without_inputs_leaves_w17_fields_absent(tmp_path):
    bundle = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path,
                                fetch=False, now=NOW)
    meta = bundle["meta"]
    assert "pipeline_defenses" not in meta
    assert "pipeline_defenses_method" not in meta
    assert meta["openssf_signals"] is None
    assert bundle["confidence"]["openssf_signals"] == "missing"


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
    # W1-5/W1-2/W1-3/W1-6 postdate this file and are not under test here;
    # marking them done avoids live commits/deps.dev/ecosyste.ms/graphql/gh
    # api calls for "acme/widget" below (Warning WW3 round 3: W1-6's own
    # `_merged_prs` "…/pulls" call was the 5th, newly-added leak on top of
    # 4 pre-existing ones -- all 4 collectors are seeded here together so
    # the shared block_live_network guard records 0 attempts).
    "commits_365d": [], "dependents": {}, "pr_window": {}, "governance": {},
}


def seed_gather_done(raw: Path) -> None:
    for collector, data in GATHER_DONE.items():
        write_env(raw, collector, KEY, data)
    write_env(raw, "downloads", "pypi__widget", {"monthly": 10})
    write_env(raw, "repo_object", KEY, {"archived": False, "mirror_url": None,
                                        "fork": False, "license_spdx_id": "MIT",
                                        "pull_requests_total": 12,
                                        "tree_paths_le2": ["pyproject.toml"],
                                        "tree_truncated": False,
                                        "offplatform_paths": []})


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
