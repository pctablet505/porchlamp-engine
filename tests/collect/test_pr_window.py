"""Tests for W1-3: windowed PR collection with typed actors and review threads.

Subject: ``cvi.collect.github_graphql`` (D-IN-1/2/4, ``02`` §4 1.1/1.4/4.3):
recorded-response oracles at the GraphQL transport boundary (no live calls)
asserting the exact D-IN-2 / 4.3 values, pagination stop at the window
boundary, ``truncated`` at the 500 cap, point-cost accounting, and the
append-only hooks at the end of ``scripts/gather.py`` (the ``pr_window``
status entry) and ``scripts/derive.py`` (the ``pr_window_*`` bundle fields).
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import pytest

from cvi.collect import github_graphql as gql
from cvi.collect.envelope import envelope_hash, read_envelope, write_envelope

ROOT = Path(__file__).resolve().parent.parent.parent
NOW = datetime(2026, 9, 12, 0, 0, 0, tzinfo=timezone.utc)
WINDOW_START = NOW - timedelta(days=180)  # 2026-03-16T00:00:00+00:00
KEY = "acme__widget"


def _load_script(modname: str, fname: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(modname, ROOT / "scripts" / fname)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[modname] = module
    spec.loader.exec_module(module)
    return module


gather = _load_script("cvi_gather_w13", "gather.py")
derive = _load_script("cvi_derive_w13", "derive.py")


# --- recorded-response fixtures ----------------------------------------------

def author(login: str = "alice", typ: str = "User") -> dict:
    return {"__typename": typ, "login": login,
            "createdAt": None if typ == "Bot" else "2020-01-01T00:00:00Z"}


def review(login="bob", typ="User", assoc="MEMBER", state="COMMENTED", body="",
           created="2026-09-01T00:30:00Z", inline=0) -> dict:
    return {"author": {"__typename": typ, "login": login}, "state": state,
            "body": body, "createdAt": created, "authorAssociation": assoc,
            "comments": {"totalCount": inline}}


def comment(login="bob", typ="User", assoc="MEMBER", body="",
            created="2026-09-01T00:30:00Z") -> dict:
    return {"author": {"__typename": typ, "login": login},
            "authorAssociation": assoc, "bodyText": body, "createdAt": created}


def pr_node(number, created, closed=None, merged=None, assoc="NONE",
            author_login="alice", author_typ="User",
            reviews=(), comments=(), threads=0) -> dict:
    return {
        "number": number, "createdAt": created, "closedAt": closed,
        "mergedAt": merged,
        "state": "MERGED" if merged else ("CLOSED" if closed else "OPEN"),
        "author": author(author_login, author_typ),
        "authorAssociation": assoc,
        "reviews": {"nodes": list(reviews)},
        "comments": {"nodes": list(comments)},
        "reviewThreads": {"totalCount": threads},
    }


def page(nodes: list[dict], has_next: bool, cursor: str = "") -> dict:
    """One recorded page: nodes plus its recorded ``pageInfo``."""
    return {"nodes": nodes, "hasNextPage": has_next, "endCursor": cursor}


class FakeTransport:
    """Recorded-response transport: one scripted page per ``execute`` call.

    ``pages`` are recorded page documents (``{"nodes", "hasNextPage",
    "endCursor"}``); records ``(query, variables)`` per call so tests can
    assert cursor plumbing; calling past the recorded pages is a failure,
    not a default.
    """

    def __init__(self, pages: list[dict], costs: list[int]) -> None:
        self.pages = pages
        self.costs = costs
        self.calls: list[tuple[str, dict]] = []

    def execute(self, query: str, variables: dict) -> dict:
        self.calls.append((query, dict(variables)))
        idx = len(self.calls) - 1
        assert idx < len(self.pages), "transport called past recorded pages"
        assert idx < len(self.costs)
        assert "rateLimit { cost }" in query, "query must read the page cost"
        recorded = self.pages[idx]
        return {"data": {"rateLimit": {"cost": self.costs[idx]},
                         "r": {"pullRequests": {"pageInfo": {
                             "hasNextPage": recorded["hasNextPage"],
                             "endCursor": recorded["endCursor"]},
                             "nodes": recorded["nodes"]}}}}

    @property
    def after_cursors(self) -> list:
        return [v.get("after") for _, v in self.calls]


def _node_at(i: int, ts: str, closed=None) -> dict:
    return pr_node(1000 + i, ts, closed=closed)


# --- D-IN-1 / D-IN-2: actor typing and substantive responses -----------------

def test_is_bot_actor_typename_suffix_and_exact_list():
    assert gql.is_bot_actor("Bot", "anyone")
    assert gql.is_bot_actor("User", "github-actions[bot]")
    assert gql.is_bot_actor("User", "Dependabot")
    assert not gql.is_bot_actor("User", "dependabot-user")  # substrings removed (F12)
    assert not gql.is_bot_actor("User", "alice")


def test_bot_typed_comment_is_not_a_response():
    """A 60-char comment by a listed bot and one by a [bot] account are not
    D-IN-2 responses; the maintainer's 60-char comment one hour on is."""
    prs = [pr_node(
        1, "2026-09-01T00:00:00Z", closed="2026-09-02T00:00:00Z",
        comments=[
            comment("claude", assoc="MEMBER", body="c" * 60,
                    created="2026-09-01T00:05:00Z"),
            comment("github-actions[bot]", typ="Bot", assoc="MEMBER", body="b" * 60,
                    created="2026-09-01T00:10:00Z"),
            comment("bob", assoc="MEMBER", body="m" * 60,
                    created="2026-09-01T01:00:00Z"),
        ])]
    out = gql.derive_pr_window(prs)
    assert out[0]["first_substantive_response_at"] == "2026-09-01T01:00:00Z"
    assert out[0]["first_substantive_response_kind"] == "comment"
    assert out[0]["ttfhr_hours"] == 1.0


def test_member_comment_30_chars_not_substantive_40_and_41_are():
    """D-IN-2 boundary: a member comment counts at exactly 40 characters."""
    created = "2026-08-01T00:00:00Z"
    prs = [
        pr_node(10, created,
                comments=[comment("bob", assoc="MEMBER", body="s" * 30,
                                  created="2026-08-01T00:30:00Z")]),
        pr_node(11, created,
                comments=[comment("bob", assoc="MEMBER", body="s" * 40,
                                  created="2026-08-01T01:00:00Z")]),
        pr_node(12, created,
                comments=[comment("bob", assoc="MEMBER", body="s" * 41,
                                  created="2026-08-01T02:00:00Z")]),
    ]
    out = gql.derive_pr_window(prs)
    assert out[0]["first_substantive_response_at"] is None
    assert out[0]["ttfhr_hours"] is None
    assert out[1]["first_substantive_response_at"] == "2026-08-01T01:00:00Z"
    assert out[1]["ttfhr_hours"] == 1.0
    assert out[2]["first_substantive_response_at"] == "2026-08-01T02:00:00Z"
    assert out[2]["ttfhr_hours"] == 2.0


def test_maintainer_review_any_state_is_a_response():
    prs = [pr_node(13, "2026-08-01T00:00:00Z",
                   reviews=[review("bob", assoc="COLLABORATOR", state="CHANGES_REQUESTED",
                                   body="", created="2026-08-01T00:30:00Z")])]
    out = gql.derive_pr_window(prs)
    assert out[0]["first_substantive_response_at"] == "2026-08-01T00:30:00Z"
    assert out[0]["first_substantive_response_kind"] == "review"
    assert out[0]["ttfhr_hours"] == 0.5


def test_outsider_and_author_comments_are_not_responses():
    created = "2026-08-01T00:00:00Z"
    prs = [
        pr_node(14, created,  # another external account commenting
                comments=[comment("carol", assoc="NONE", body="o" * 60,
                                  created="2026-08-01T00:30:00Z")]),
        pr_node(15, created,  # the PR author's own comment
                comments=[comment("alice", assoc="NONE", body="o" * 60,
                                  created="2026-08-01T00:30:00Z")]),
    ]
    out = gql.derive_pr_window(prs)
    assert out[0]["ttfhr_hours"] is None
    assert out[1]["ttfhr_hours"] is None


# --- 02 §4.3: substantive review flag ------------------------------------------

def test_substantive_review_thresholds_exact():
    """4.3 boundary: 79-char body is not, 80-char body is, one inline
    comment is; a bot's long review is not (D-IN-1 hygiene)."""
    created = "2026-08-01T00:00:00Z"
    prs = [
        pr_node(20, created, reviews=[review(body="r" * 79, inline=0)]),
        pr_node(21, created, reviews=[review(body="r" * 80, inline=0)]),
        pr_node(22, created, reviews=[review(body="", inline=1)]),
        pr_node(23, created, reviews=[review("renovate[bot]", typ="Bot",
                                             assoc="MEMBER", body="r" * 200,
                                             inline=5)]),
        pr_node(24, created, reviews=[review("alice", assoc="NONE", body="r" * 200)]),
    ]
    out = gql.derive_pr_window(prs)
    assert [d["substantive_review"] for d in out] == [False, True, True, False, True]


# --- decided / merged / external flags -----------------------------------------

def test_decided_and_merged_flags():
    out = gql.derive_pr_window([
        pr_node(30, "2026-08-01T00:00:00Z"),
        pr_node(31, "2026-08-01T00:00:00Z", closed="2026-08-10T00:00:00Z"),
        pr_node(32, "2026-08-01T00:00:00Z", closed="2026-08-10T00:00:00Z",
                merged="2026-08-09T00:00:00Z"),
    ])
    assert [(d["decided"], d["merged"]) for d in out] == \
        [(False, False), (True, False), (True, True)]


def test_external_classification():
    out = gql.derive_pr_window([
        pr_node(40, "2026-08-01T00:00:00Z", assoc="NONE"),
        pr_node(41, "2026-08-01T00:00:00Z", assoc="FIRST_TIMER"),
        pr_node(42, "2026-08-01T00:00:00Z", assoc="FIRST_TIME_CONTRIBUTOR"),
        pr_node(43, "2026-08-01T00:00:00Z", assoc="CONTRIBUTOR"),
        pr_node(44, "2026-08-01T00:00:00Z", assoc="MEMBER"),
        pr_node(45, "2026-08-01T00:00:00Z", assoc="OWNER"),
        pr_node(46, "2026-08-01T00:00:00Z", assoc="COLLABORATOR"),
    ])
    assert [d["is_external"] for d in out] == \
        [True, True, True, True, False, False, False]


def test_bot_author_flag():
    out = gql.derive_pr_window([
        pr_node(50, "2026-08-01T00:00:00Z", author_login="dependabot[bot]",
                author_typ="Bot"),
        pr_node(51, "2026-08-01T00:00:00Z", author_login="alice"),
    ])
    assert [d["is_bot_author"] for d in out] == [True, False]


# --- pagination: window boundary, cap, cost ------------------------------------

def test_pagination_stops_at_window_boundary():
    """Page 1: 100 in-window PRs; page 2: 25 in-window then one PR just
    before 2026-03-16T00:00Z. Stop there: 125 kept, boundary PR excluded,
    no third call, truncated False."""
    p1 = page([_node_at(i, (NOW - timedelta(days=3 + i)).isoformat())
               for i in range(100)], True, "cursor-1")
    p2_nodes = [_node_at(100 + i, (NOW - timedelta(days=103 + i)).isoformat())
                for i in range(25)]
    p2_nodes.append(pr_node(9999, "2026-03-15T23:59:00Z"))  # before WINDOW_START
    p2 = page(p2_nodes, False)
    fake = FakeTransport([p1, p2], [11, 22])
    res = gql.fetch_pr_window("acme", "widget", NOW, fake)
    assert len(res.prs) == 125
    assert res.truncated is False
    assert res.cost_points == 33
    assert res.prs[-1]["number"] == 1124
    assert res.prs[-1]["createdAt"] == (NOW - timedelta(days=127)).isoformat()
    assert all(n["number"] != 9999 for n in res.prs)
    assert len(fake.calls) == 2
    assert fake.after_cursors == [None, "cursor-1"]


def test_window_boundary_included_at_exactly_180_days():
    """A PR opened exactly at now-180d is inside the window."""
    prs = [pr_node(70, WINDOW_START.isoformat())]
    fake = FakeTransport([page(prs, False)], [2])
    res = gql.fetch_pr_window("acme", "widget", NOW, fake)
    assert [n["number"] for n in res.prs] == [70]
    assert res.truncated is False


def test_truncated_set_at_exactly_500_prs():
    pages = [page([_node_at(p * 100 + i,
                            (NOW - timedelta(hours=1 + i / 4.0)).isoformat())
                   for i in range(100)], True, f"cursor-{p + 1}")
             for p in range(5)]
    fake = FakeTransport(pages, [3, 4, 5, 6, 7])
    res = gql.fetch_pr_window("acme", "widget", NOW, fake)
    assert len(res.prs) == 500
    assert res.truncated is True
    assert res.cost_points == 25
    assert len(fake.calls) == 5


def test_cost_points_summed_across_pages():
    p1 = page([_node_at(i, (NOW - timedelta(hours=1 + i / 4.0)).isoformat())
               for i in range(100)], True, "cursor-1")
    p2 = page([_node_at(100 + i, (NOW - timedelta(days=1 + i / 4.0)).isoformat())
               for i in range(40)], False)
    fake = FakeTransport([p1, p2], [3, 9])
    res = gql.fetch_pr_window("acme", "widget", NOW, fake)
    assert res.cost_points == 12
    assert res.truncated is False
    assert len(res.prs) == 140


def test_fetch_raises_on_graphql_errors():
    class ErrorsTransport:
        def execute(self, query, variables):
            return {"errors": [{"message": "Field 'bodyText' is not defined"}]}
    with pytest.raises(RuntimeError, match="bodyText"):
        gql.fetch_pr_window("acme", "widget", NOW, ErrorsTransport())


def test_fetch_raises_when_repository_missing():
    class MissingTransport:
        def execute(self, query, variables):
            return {"data": {"rateLimit": {"cost": 1}, "r": None}}
    with pytest.raises(RuntimeError, match="not found"):
        gql.fetch_pr_window("acme", "widget", NOW, MissingTransport())


# --- collect_pr_window: envelope + provenance (R-4) ----------------------------

def test_collect_writes_envelope_with_provenance_hash(tmp_path):
    p1 = [pr_node(5, "2026-09-01T00:00:00Z", closed="2026-09-05T00:00:00Z",
                  reviews=[review(body="r" * 80, created="2026-09-01T01:00:00Z")],
                  comments=[comment(body="m" * 41, created="2026-09-01T02:00:00Z")])]
    fake = FakeTransport([page(p1, False)], [5])
    raw = tmp_path / "raw"
    assert gql.collect_pr_window(raw, "acme", "widget", now=NOW, transport=fake) == "ok"
    env = read_envelope(raw / "pr_window" / "acme__widget.json")
    assert env.hash_matches_data
    assert env.recorded_sha256 == envelope_hash(env.data)
    assert env.source == "github-graphql:pr-window"
    d = env.data
    assert d["count"] == 1
    assert d["truncated"] is False
    assert d["cost_points"] == 5
    assert d["window_days"] == 180
    assert d["cap"] == 500
    assert d["prs"][0]["reviewThreads"]["totalCount"] == 0
    assert d["derived"][0]["ttfhr_hours"] == 1.0  # review at 01:00 beats comment 02:00
    assert d["derived"][0]["substantive_review"] is True
    assert len(fake.calls) == 1
    assert gql.collect_pr_window(raw, "acme", "widget", now=NOW,
                                 transport=fake) == "skip"  # envelope exists
    assert len(fake.calls) == 1
    refake = FakeTransport([page(p1, False)], [5])
    assert gql.collect_pr_window(raw, "acme", "widget", now=NOW, transport=refake,
                                 force=True) == "ok"
    assert len(refake.calls) == 1
    assert len(fake.calls) == 1


# --- the append-only gather hook --------------------------------------------------

GATHER_DONE = {
    "github_meta": {"stars": 1, "forks": 0, "pushed_at": "2026-01-01T00:00:00Z",
                    "archived": False, "created_at": "2020-01-01T00:00:00Z",
                    "license_spdx": "MIT", "default_branch": "main"},
    "pr_events": [],
    "pr_reviews": {},
    "contributors": [],
    # W1-1's per-label gfi collector treats a bare list as legacy and
    # re-fetches (needs_gather); the new shape's metadata-first record is
    # what actually marks it done.
    "gfi": [{"gfi_newcomer_v2": True, "labels_matched": [], "queries": [],
             "truncated": False}],
    "actions_runs": [],
    "tree": {"paths": ["pyproject.toml"], "truncated": False},
    "commits_180d": [],
    "depsdev": None,
    "scorecard": None,
    # W1-5/W1-2/W1-7 postdate this file and are not under test here; marking
    # them done avoids live commits/deps.dev/ecosyste.ms/gh api calls for
    # "acme/widget" below (Blocker B4 round 2).
    "commits_365d": [], "dependents": {},
    "releases": {}, "issues_counts": {}, "runs": {},
}


def write_env(raw: Path, collector: str, key: str, data,
              at: str = "2026-09-11T00:00:00Z") -> None:
    write_envelope(raw / collector / f"{key}.json", source="test", data=data,
                   fetched_at=at)


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
    # W1-6 postdates this file and is not under test here; marking it done
    # avoids live check-run/file-body calls for "acme/widget" below.
    write_env(raw, "governance", KEY, {})


def test_gather_repo_pr_window_status_ok(tmp_path, monkeypatch, block_live_network):
    gather.DATA = tmp_path / "raw"
    seed_gather_done(gather.DATA)
    monkeypatch.setattr(gql, "collect_pr_window", lambda *a, **k: "ok")
    status = gather.gather_repo("acme", "widget", "pypi")
    assert status["pr_window"] == "ok"
    assert status["github_meta"] == "skip"
    assert status["repo_object"] == "skip"


def test_gather_repo_pr_window_status_skip(tmp_path, monkeypatch, block_live_network):
    gather.DATA = tmp_path / "raw"
    seed_gather_done(gather.DATA)
    write_env(gather.DATA, "pr_window", KEY, {"count": 0, "truncated": False,
                                              "cost_points": 0, "prs": [],
                                              "derived": []})

    def must_not_fetch(*a, **k):
        raise AssertionError("collector must be skipped when the envelope exists")

    monkeypatch.setattr(gql, "collect_pr_window", must_not_fetch)
    status = gather.gather_repo("acme", "widget", "pypi")
    assert status["pr_window"] == "skip"


def test_gather_repo_pr_window_status_fail(tmp_path, monkeypatch, block_live_network):
    gather.DATA = tmp_path / "raw"
    seed_gather_done(gather.DATA)

    def raise_transport_error(*a, **k):
        raise RuntimeError("gh api graphql rc=1: rate limited")

    monkeypatch.setattr(gql, "collect_pr_window", raise_transport_error)
    status = gather.gather_repo("acme", "widget", "pypi")
    assert status["pr_window"] == "fail"


# --- the append-only derive hook ---------------------------------------------------

def _write_pr_window_envelope(raw: Path) -> None:
    p1 = [
        pr_node(1, "2026-09-01T00:00:00Z", closed="2026-09-02T00:00:00Z",
                reviews=[review(body="r" * 80, created="2026-09-01T02:00:00Z")],
                comments=[comment(body="m" * 41, created="2026-09-01T03:00:00Z")]),
        pr_node(2, "2026-08-01T00:00:00Z", closed="2026-08-10T00:00:00Z"),
        pr_node(3, "2026-08-15T00:00:00Z", assoc="MEMBER", author_login="bob",
                reviews=[review("carol", assoc="OWNER", body="", inline=1,
                                created="2026-08-15T06:00:00Z")]),
        pr_node(4, "2026-09-05T00:00:00Z", assoc="FIRST_TIMER"),
    ]
    assert gql.collect_pr_window(raw, "acme", "widget", now=NOW,
                                 transport=FakeTransport([page(p1, False)], [7])) == "ok"


def test_derive_repo_pr_window_fields(tmp_path):
    data_dir = tmp_path
    _write_pr_window_envelope(data_dir / "raw")
    bundle = derive.derive_repo("acme", "widget", derive.Ecosystem.PYPI, data_dir,
                                fetch=False, now=NOW)
    meta = bundle["meta"]
    assert meta["pr_window_count"] == 4
    assert meta["pr_window_truncated"] is False
    assert meta["pr_window_cost_points"] == 7
    assert meta["pr_window_external_measured_latency_count"] == 1
    assert meta["pr_window_ttfhr_median_hours"] == 2.0
    assert meta["pr_window_substantive_review_rate"] == 0.5
    assert bundle["confidence"]["pr_window_count"] == "verified"
    assert bundle["confidence"]["pr_window_ttfhr_median_hours"] == "derived"
    assert bundle["provenance"]["pr_window_count"]["source"] == \
        str(data_dir / "raw" / "pr_window" / "acme__widget.json")


def test_derive_repo_without_pr_window_envelope_unaffected(tmp_path):
    bundle = derive.derive_repo("acme", "widget", derive.Ecosystem.PYPI, tmp_path,
                                fetch=False, now=NOW)
    meta = bundle["meta"]
    assert not any(k.startswith("pr_window_") for k in meta)
    assert meta["rr_90"] == 0.0
    assert bundle["confidence"]["rr_90"] == "missing"
    assert meta["stars"] == 0
    assert bundle["confidence"]["stars"] == "missing"


# =============================================================================
# W1-4: author history and newcomer cohorts (D-IN-3/D-IN-4, 02 §4 1.2/1.3/
# 5.2/5.3)
# =============================================================================
#
# Subject: cvi.collect.github_graphql's author section (recorded-response
# oracles, no live calls) plus the append-only hooks at the end of
# scripts/gather.py (the ``author_history`` status entry) and
# scripts/derive.py (the cohort bundle fields). Every derived-quantity
# assertion below was cross-checked against the real implementation before
# being written (not merely a shape check) — see the packet handoff for the
# independent hand computation.

W4_BAND_END = NOW - timedelta(days=180)


def w4_pr(number, created, author_login="alice", assoc="NONE", author_typ="User",
          author_created="2020-01-01T00:00:00Z", closed=None, merged=None) -> dict:
    """One recorded 365-d window PR node (the AUTHOR_WINDOW_QUERY shape)."""
    return {"number": number, "createdAt": created, "closedAt": closed,
            "mergedAt": merged,
            "state": "MERGED" if merged else ("CLOSED" if closed else "OPEN"),
            "author": {"__typename": author_typ, "login": author_login,
                       "createdAt": None if author_typ == "Bot" else author_created},
            "authorAssociation": assoc}


def w4_days(number, days_ago, author_login="alice", assoc="NONE",
           closed_after=None, merged_after=None) -> dict:
    """One recorded PR node dated by whole days before ``NOW`` (oracle tests):
    ``closed_after``/``merged_after`` are days from opening to closing."""
    created = NOW - timedelta(days=days_ago)
    closed = merged = None
    if merged_after is not None:
        merged = closed = (created + timedelta(days=merged_after)).isoformat()
    elif closed_after is not None:
        closed = (created + timedelta(days=closed_after)).isoformat()
    return w4_pr(number, created.isoformat(), author_login, assoc, closed=closed, merged=merged)


def w4_spr(number, created, closed=None, merged=None) -> dict:
    """One recorded per-author search PR node (the PullRequest fragment)."""
    return {"number": number, "createdAt": created, "closedAt": closed,
            "mergedAt": merged,
            "state": "MERGED" if merged else ("CLOSED" if closed else "OPEN")}


def w4_rec(login, account="2015-01-01T00:00:00Z", events=50, prs=()) -> dict:
    """One recorded author record (search + user document)."""
    return {"login": login, "account_created_at": account,
            "public_event_count": events, "prs": list(prs)}


def _w4_ah_data(prs, records, sampled=(), anchor=NOW, cost=99, remaining=4900,
               truncated=False) -> dict:
    """A recorded author_history envelope ``data`` (W1-4 shape)."""
    return {"window_anchor": anchor.isoformat(), "window_days": 365, "cap": 500,
            "count": len(prs), "truncated": truncated, "cost_points": cost,
            "rate_limit_remaining": remaining, "prs": list(prs),
            "sampled_authors": list(sampled), "author_records": list(records)}


def _w4_page(nodes: list[dict], has_next: bool, cursor: str = "") -> dict:
    return {"nodes": nodes,
            "pageInfo": {"hasNextPage": has_next, "endCursor": cursor}}


class W4Transport:
    """Recorded-response transport for the author section: scripted window
    pages, per-author search nodes and per-author user documents, with
    scripted point cost and ``remaining``. An unrecorded author is a
    failure, not a default; ``None`` for a login's search/user response
    simulates a GraphQL "could not resolve" error (a deleted/renamed
    account) instead of a scripted document.
    """

    def __init__(self, window_pages, window_costs, window_remaining, searches, users,
                search_meta=None, user_meta=None):
        self.window_pages = list(window_pages)
        self.window_costs = list(window_costs)
        self.window_remaining = list(window_remaining)
        self.searches = dict(searches)
        self.users = dict(users)
        self.search_meta = dict(search_meta or {})
        self.user_meta = dict(user_meta or {})
        self.calls: list[tuple[str, dict]] = []

    def execute(self, query: str, variables: dict) -> dict:
        self.calls.append((query, dict(variables)))
        if "pullRequests(states" in query:
            n = sum(1 for q, _ in self.calls if "pullRequests(states" in q)
            assert n <= len(self.window_pages), "transport called past recorded pages"
            return {"data": {"rateLimit": {"cost": self.window_costs[n - 1],
                                          "remaining": self.window_remaining[n - 1]},
                            "r": {"pullRequests": self.window_pages[n - 1]}}}
        if "search(query" in query:
            login = variables["q"].rsplit("author:", 1)[-1]
            assert login in self.searches, f"unrecorded search for {login}"
            if self.searches[login] is None:
                return {"errors": [{"message": f"Could not resolve to a User "
                                              f"with the login of '{login}'."}]}
            return {"data": {"rateLimit": self.search_meta.get(login, {"cost": 1, "remaining": 999}),
                            "s": self.searches[login]}}
        if "user(login" in query:
            login = str(variables["login"]).lower()
            assert login in self.users, f"unrecorded user {login}"
            if self.users[login] is None:
                return {"errors": [{"message": f"Could not resolve to a User "
                                              f"with the login of '{login}'."}]}
            return {"data": {"rateLimit": self.user_meta.get(login, {"cost": 1, "remaining": 999}),
                            "u": self.users[login]}}
        raise AssertionError(f"unexpected query: {query[:80]}")

    @property
    def n_window_calls(self) -> int:
        return sum(1 for q, _ in self.calls if "pullRequests(states" in q)


# --- fetch_author_window: 365-d boundary, cap, cost, remaining ---------------

def test_author_window_stops_at_365d_boundary():
    """Page 1: 100 in-window PRs; page 2: one in-window PR then one just
    before 2025-09-12T00:00Z. Stop there: 101 kept, boundary PR excluded."""
    p1 = _w4_page([w4_pr(1 + i, (NOW - timedelta(days=2 + i)).isoformat())
                  for i in range(100)], True, "c1")
    p2_nodes = [w4_pr(111, (NOW - timedelta(days=364)).isoformat())]
    p2_nodes.append(w4_pr(999, "2025-09-11T23:59:00Z"))  # before NOW-365d
    p2 = _w4_page(p2_nodes, False)
    fake = W4Transport([p1, p2], [11, 22], [4989, 4967], {}, {})
    res = gql.fetch_author_window("acme", "widget", NOW, fake)
    assert len(res.prs) == 101
    assert res.truncated is False
    assert res.cost_points == 33
    assert res.remaining == 4967
    assert res.prs[-1]["number"] == 111
    assert all(n["number"] != 999 for n in res.prs)
    assert fake.n_window_calls == 2


def test_author_window_truncated_at_500_and_cost_summed():
    pages = [_w4_page([w4_pr(p * 100 + i,
                            (NOW - timedelta(hours=1 + i / 4.0)).isoformat())
                      for i in range(100)], True, f"c{p + 1}")
            for p in range(5)]
    fake = W4Transport(pages, [3, 4, 5, 6, 7], [100, 96, 91, 85, 78], {}, {})
    res = gql.fetch_author_window("acme", "widget", NOW, fake)
    assert len(res.prs) == 500
    assert res.truncated is True
    assert res.cost_points == 25
    assert res.remaining == 78
    assert fake.n_window_calls == 5


def test_fetch_author_window_raises_on_graphql_errors():
    class ErrorsTransport:
        def execute(self, query, variables):
            return {"errors": [{"message": "Field 'mergedAt' is not defined"}]}
    with pytest.raises(RuntimeError, match="mergedAt"):
        gql.fetch_author_window("acme", "widget", NOW, ErrorsTransport())


def test_fetch_author_window_raises_when_repository_missing():
    class MissingTransport:
        def execute(self, query, variables):
            return {"data": {"rateLimit": {"cost": 1, "remaining": 10}, "r": None}}
    with pytest.raises(RuntimeError, match="not found"):
        gql.fetch_author_window("acme", "widget", NOW, MissingTransport())


# --- _select_newcomer_sample: band, determinism, cap, truncation margin -----

def test_select_newcomer_sample_20_of_25_is_a_seeded_choice_not_earliest():
    """25 band candidates, at most 20 sampled: reproducible (same input,
    same output), sorted in the stored order, and NOT simply the 20
    earliest by creation date — the packet's own words are "sorted login,
    then a seeded choice", not "the oldest 20" (which would sample only the
    band's earliest slice, every repo, every time)."""
    nodes = [w4_pr(900 + i, (NOW - timedelta(days=240 - i)).isoformat(),
                  f"s{i:02d}") for i in range(1, 26)]
    sample = gql._select_newcomer_sample(nodes, NOW, truncated=False)
    again = gql._select_newcomer_sample(nodes, NOW, truncated=False)
    assert sample == again
    assert len(sample) == 20
    assert list(sample) == sorted(sample)
    assert sample != tuple(f"s{i:02d}" for i in range(1, 21))  # not "earliest 20"


def test_select_newcomer_sample_returns_all_when_at_or_under_cap():
    """Fewer than 20 candidates: no sampling needed, every candidate is
    kept, sorted by login (not by creation date)."""
    nodes = [w4_pr(1, (NOW - timedelta(days=200)).isoformat(), "b"),
            w4_pr(2, (NOW - timedelta(days=210)).isoformat(), "a"),
            w4_pr(3, (NOW - timedelta(days=220)).isoformat(), "c")]
    assert gql._select_newcomer_sample(nodes, NOW, truncated=False) == ("a", "b", "c")


def test_select_newcomer_sample_truncation_margin_excludes_near_floor():
    """Truncated window: the floor author and one within 30 d of the floor
    are excluded (an unrecorded, even older PR is plausible for both); a
    candidate safely clear of the floor is kept. Not truncated: the margin
    does not apply and all three are candidates."""
    nodes = [w4_pr(1, (NOW - timedelta(days=300)).isoformat(), "floor_setter"),
            w4_pr(2, (NOW - timedelta(days=295)).isoformat(), "near_floor"),
            w4_pr(3, (NOW - timedelta(days=250)).isoformat(), "safe")]
    assert gql._select_newcomer_sample(nodes, NOW, truncated=True) == ("safe",)
    assert gql._select_newcomer_sample(nodes, NOW, truncated=False) == \
        ("floor_setter", "near_floor", "safe")


# --- collect_author_history: envelope, union sample, cost, error tolerance --

def _w4_main_window_pages():
    """Two recorded pages: decision-cohort author ext1 (two PRs, 103 d and
    120 d old) and newcomer-band authors new1 (2026-02-01) / new2
    (2025-12-01, the earlier of the two); a maintainer PR and a bot PR are
    not external; the boundary PR is just before NOW-365d."""
    p1 = _w4_page([
        w4_pr(1, "2026-02-01T00:00:00Z", "new1"),
        w4_pr(2, "2025-12-01T00:00:00Z", "new2"),
        w4_pr(100, "2026-06-01T00:00:00Z", "ext1"),
        w4_pr(4, "2026-05-01T00:00:00Z", "bob", assoc="MEMBER"),
    ], True, "c1")
    p2 = _w4_page([
        w4_pr(103, "2026-05-15T00:00:00Z", "ext1"),
        w4_pr(5, "2026-07-01T00:00:00Z", "renovate[bot]", author_typ="Bot"),
        w4_pr(999, "2025-09-11T23:59:00Z", "old"),
    ], False)
    return [p1, p2]


def test_collect_author_history_envelope_union_sample_and_cost(tmp_path):
    raw = tmp_path / "raw"
    searches = {
        "ext1": {"nodes": [w4_spr(100, "2026-06-01T00:00:00Z",
                                  closed="2026-06-05T00:00:00Z"),
                           w4_spr(103, "2026-05-15T00:00:00Z")]},
        "new1": None,  # deleted/renamed account: search fails
        "new2": {"nodes": [w4_spr(2, "2025-12-01T00:00:00Z")]},
    }
    users = {
        "ext1": {"createdAt": "2019-05-05T00:00:00Z",
                "contributionsCollection": {"contributionCalendar": {"totalContributions": 40}}},
        "new1": None,  # deleted/renamed account: user fails too
        "new2": {"createdAt": "2015-03-03T00:00:00Z",
                "contributionsCollection": {"contributionCalendar": {"totalContributions": 200}}},
    }
    fake = W4Transport(_w4_main_window_pages(), [10, 12], [4990, 4980], searches, users,
                       search_meta={"ext1": {"cost": 2, "remaining": 4978},
                                   "new1": {"cost": 1, "remaining": 4977},
                                   "new2": {"cost": 1, "remaining": 4976}},
                       user_meta={"ext1": {"cost": 3, "remaining": 4973},
                                 "new1": {"cost": 1, "remaining": 4972},
                                 "new2": {"cost": 1, "remaining": 4971}})
    assert gql.collect_author_history(raw, "acme", "widget", now=NOW,
                                      transport=fake) == "ok"
    env = read_envelope(raw / "author_history" / "acme__widget.json")
    assert env.hash_matches_data
    assert env.source == "github-graphql:author-history"
    d = env.data
    assert d["window_days"] == 365
    assert d["cap"] == 500
    assert d["count"] == 6
    assert d["truncated"] is False
    # 10+12 window points; ext1 search(2)+user(3); new2 search(1)+user(1);
    # new1's two calls fail before their cost/remaining are read -> +0.
    assert d["cost_points"] == 29
    # the last SUCCESSFUL call's remaining (new2's user, after new1 fails).
    assert d["rate_limit_remaining"] == 4971
    # 2 band candidates, under the 20-author cap: both kept, sorted by
    # login (not by creation date — new2's first PR is the older one).
    assert d["sampled_authors"] == ["new1", "new2"]
    recs = {r["login"]: r for r in d["author_records"]}
    assert set(recs) == {"ext1", "new1", "new2"}  # bob/bot never fetched
    assert recs["ext1"]["account_created_at"] == "2019-05-05T00:00:00Z"
    assert recs["ext1"]["public_event_count"] == 40
    assert [p["number"] for p in recs["ext1"]["prs"]] == [100, 103]
    # new1's account/search both failed: degraded to no history, not a crash.
    assert recs["new1"] == {"login": "new1", "account_created_at": None,
                            "public_event_count": None, "prs": []}
    assert len(fake.calls) == 8  # 2 window + 3 search + 3 user
    assert gql.collect_author_history(raw, "acme", "widget", now=NOW,
                                      transport=fake) == "skip"
    assert len(fake.calls) == 8  # skip performs no transport call


def test_collect_author_history_without_pr_window_envelope(tmp_path):
    """The decision cohort and the account/event fetches are entirely
    self-contained on the 365-d window: no pr_window envelope is read (or
    written) even though ext1 is a decision-cohort author too."""
    raw = tmp_path / "raw"
    searches = {"ext1": {"nodes": []}, "new1": {"nodes": []}, "new2": {"nodes": []}}
    users = {"ext1": {"createdAt": "2019-05-05T00:00:00Z",
                      "contributionsCollection": {"contributionCalendar": {"totalContributions": 40}}},
            "new1": {"createdAt": "2021-06-01T00:00:00Z",
                    "contributionsCollection": {"contributionCalendar": {"totalContributions": 8}}},
            "new2": {"createdAt": "2015-03-03T00:00:00Z",
                    "contributionsCollection": {"contributionCalendar": {"totalContributions": 200}}}}
    fake = W4Transport(_w4_main_window_pages(), [10, 12], [4990, 4980], searches, users)
    assert gql.collect_author_history(raw, "acme", "widget", now=NOW,
                                      transport=fake) == "ok"
    assert not (raw / "pr_window").exists()
    d = read_envelope(raw / "author_history" / "acme__widget.json").data
    assert d["sampled_authors"] == ["new1", "new2"]
    assert {r["login"] for r in d["author_records"]} == {"ext1", "new1", "new2"}


# --- derive_cohort_fields: hand-computed oracles ------------------------------
#
# Every ratio below was independently derived by hand (module note at the
# top of this section) and cross-checked against ``derive_cohort_fields``
# before being pasted here; see the packet handoff for the derivation.

DECISION_MAIN_PRS = [
    w4_days(200, 120, "a1"),                                  # open; has a prior merged PR
    w4_days(250, 300, "a1", merged_after=5),                   # a1's prior merged PR
    w4_days(201, 110, "a2", closed_after=15),                  # decided in time, not merged
    w4_days(202, 100, "a3"),                                   # open
    w4_days(203, 140, "a4", closed_after=50),                  # decided in time, not merged
    w4_days(204, 150, "a5", closed_after=101),                 # decided LATE (>90d): not DR_90-decided
    w4_days(205, 91, "a6", merged_after=90),                   # decided exactly at the 90 d boundary
    w4_days(206, 269, "a7", merged_after=10),                  # decided in time, near the 270 d boundary
    w4_days(207, 89, "a8"),                                    # outside the band (age < 90 d): excluded
    w4_days(208, 105, "bob", assoc="MEMBER"),                  # maintainer: not external
    w4_pr(209, (NOW - timedelta(days=150)).isoformat(), "renovate[bot]", author_typ="Bot"),
]
DECISION_MAIN_RECORDS = [
    w4_rec("a1"), w4_rec("a2"), w4_rec("a3"), w4_rec("a4"),
    # a5's account is exactly 89 d old at its PR (D-IN-3 excludes it).
    w4_rec("a5", account=(NOW - timedelta(days=150 + 89)).isoformat()),
    w4_rec("a6"), w4_rec("a7"),
]


def test_decision_cohort_oracle_raw_vs_capped_and_roster():
    """Decision cohort: 7 external PRs in [90 d, 270 d] (a8 and bob/bot
    excluded). DR_90 raw 4/7 = 0.5714 (a1 open, a3 open, a5 late); capped to
    6 eligible PRs (a5 excluded, D-IN-3) -> 4/6 = 0.6667. MR_first: a1's PR
    has a's own prior merged PR and is excluded from the candidate pool, so
    raw is 2/5 = 0.4 over {a2,a4,a5,a6,a7} (a5's PR is closed but late, so
    it still counts as "decided" for MR_first — no 90 d qualifier there);
    capped drops a5 (ineligible) -> 2/4 = 0.5."""
    ah = _w4_ah_data(DECISION_MAIN_PRS, DECISION_MAIN_RECORDS, cost=77, remaining=4900)
    fields = gql.derive_cohort_fields(ah)
    assert fields["decision_cohort_coverage_gap_days"] == (0, "derived")
    assert fields["cohort_decision_pr_numbers"] == \
        ([200, 201, 202, 203, 204, 205, 206], "derived")
    assert fields["dr90_decision_rate_raw"] == (0.5714, "derived")
    assert fields["dr90_decision_pr_count_raw"] == (7, "derived")
    assert fields["dr90_decision_rate"] == (0.6667, "derived")
    assert fields["dr90_decision_pr_count"] == (6, "derived")
    assert fields["mr_first_rate_raw"] == (0.4, "derived")
    assert fields["mr_first_pr_count_raw"] == (5, "derived")
    assert fields["mr_first_rate"] == (0.5, "derived")
    assert fields["mr_first_pr_count"] == (4, "derived")
    assert fields["author_history_cost_points"] == (77, "verified")
    assert fields["author_history_truncated"] == (False, "verified")
    assert fields["author_history_rate_limit_remaining"] == (4900, "verified")


def test_dr90_cap_binds_six_of_ten_capped_to_two():
    """The literal D-IN-3 cap acceptance oracle: one author (sock) opens 6
    of the 10 decision-cohort PRs; floor(20% * 10) = 2, so only sock's two
    OLDEST PRs (its two decided ones) survive, dropping its 4 newest (open)
    PRs. Capped: 6 kept (2 sock + 4 single-PR authors), all decided -> 1.0.
    Raw: 6 of 10 decided (sock's 2 + the 4 others) -> 0.6. MR_first cannot
    exhibit this: once one of sock's PRs merges, every later one fails
    "no prior merged" before the cap is even applied, which is itself a
    property of the anti-manipulation design, not a gap in this test."""
    prs = ([w4_days(300 + d, d, "sock") for d in (100, 101, 102, 103)]
          + [w4_days(300 + d, d, "sock", closed_after=5) for d in (104, 105)]
          + [w4_days(400 + i, d, f"g{i}", closed_after=5)
             for i, d in enumerate((110, 115, 120, 125))])
    records = [w4_rec("sock")] + [w4_rec(f"g{i}") for i in range(4)]
    fields = gql.derive_cohort_fields(_w4_ah_data(prs, records))
    assert fields["dr90_decision_rate_raw"] == (0.6, "derived")
    assert fields["dr90_decision_pr_count_raw"] == (10, "derived")
    assert fields["dr90_decision_rate"] == (1.0, "derived")
    assert fields["dr90_decision_pr_count"] == (6, "derived")


def test_apply_author_cap_six_of_ten_capped_to_two():
    """Unit-level restatement of the same acceptance oracle directly against
    ``apply_author_cap``: 6 items from one author among 10 total -> capped
    to its 2 oldest; the other 4 single-item authors are unaffected."""
    items = [("sock", NOW - timedelta(days=d)) for d in (100, 101, 102, 103, 104, 105)] + \
            [(f"g{i}", NOW - timedelta(days=d)) for i, d in enumerate((110, 115, 120, 125))]
    kept = gql.apply_author_cap(items)
    assert len(kept) == 6
    sock_kept = sorted(items[i][1] for i in kept if items[i][0] == "sock")
    assert sock_kept == [NOW - timedelta(days=105), NOW - timedelta(days=104)]
    assert sum(1 for i in kept if items[i][0].startswith("g")) == 4


def test_apply_author_cap_small_population_loses_everything():
    """Populations under 5 have a cap of 0 (floor(0.2*4) == 0): every item
    is dropped, not just the excess."""
    assert gql.apply_author_cap([("a", NOW)] * 4) == ()
    assert gql.apply_author_cap([]) == ()


def test_is_eligible_account_boundaries():
    """D-IN-3: 89 d old excluded, 90 d old included; 4 events excluded, 5
    included; missing inputs and an account newer than the PR are excluded."""
    assert gql.is_eligible_account("2026-06-15T00:00:00Z",  # 89 d old
                                   "2026-09-12T00:00:00Z", 100) is False
    assert gql.is_eligible_account("2026-06-14T00:00:00Z",  # 90 d old
                                   "2026-09-12T00:00:00Z", 5) is True
    assert gql.is_eligible_account("2020-01-01T00:00:00Z",
                                   "2026-09-12T00:00:00Z", 4) is False
    assert gql.is_eligible_account("2020-01-01T00:00:00Z",
                                   "2026-09-12T00:00:00Z", 5) is True
    assert gql.is_eligible_account("2020-01-01T00:00:00Z",
                                   "2026-09-12T00:00:00Z", None) is False
    assert gql.is_eligible_account(None, "2026-09-12T00:00:00Z", 100) is False
    assert gql.is_eligible_account("2020-01-01T00:00:00Z", None, 100) is False
    assert gql.is_eligible_account("2027-01-01T00:00:00Z",  # account newer than the PR
                                   "2026-09-12T00:00:00Z", 100) is False


def test_decision_cohort_coverage_gap_when_truncated():
    """The 365-d window truncates with its oldest recorded PR 200 d back:
    the decision cohort wants 270 d, so the gap is 270-200 = 70 d. Not
    truncated (even with zero PRs): the full window is covered, gap 0."""
    prs = [w4_days(500, 200, "u0"), w4_days(501, 150, "u1"), w4_days(502, 100, "u2")]
    truncated_fields = gql.derive_cohort_fields(_w4_ah_data(prs, [], truncated=True))
    assert truncated_fields["decision_cohort_coverage_gap_days"] == (70, "derived")
    empty_fields = gql.derive_cohort_fields(_w4_ah_data([], [], truncated=False))
    assert empty_fields["decision_cohort_coverage_gap_days"] == (0, "derived")


NEWCOMER_SPECS = {
    # login: (first PR days ago, merge latency days or None, followup gap
    # after merge in days or None)
    "n1": (200, 20, 8),      # converted, returned
    "n2": (250, 180, 10),    # converted at the exact 180 d boundary, returned
    "n3": (220, 181, None),  # NOT converted (181 > 180); merged, no followup -> not returned
    "n4": (300, None, None),  # never merged: outside the RR_90 population entirely
    "n5": (190, 10, 91),     # converted, NOT returned (91 > 90 d boundary)
    "n6": (185, 5, 90),      # converted, returned at the exact 90 d boundary
    "n7": (200, 5, 10),      # converted+returned if eligible; excluded by events (3 < 5)
    "n8": (200, 5, 10),      # converted+returned if eligible; excluded by account age (89 d)
}


def _build_newcomer_fixture():
    prs, records, num = [], [], 700
    for login, (first_days, merge_lat, followup_gap) in NEWCOMER_SPECS.items():
        prs.append(w4_days(num, first_days, login, merged_after=merge_lat))
        num += 1
        if followup_gap is not None:
            merged_at = NOW - timedelta(days=first_days) + timedelta(days=merge_lat)
            followup_days_ago = (NOW - (merged_at + timedelta(days=followup_gap))).days
            prs.append(w4_days(num, followup_days_ago, login))
            num += 1
        if login == "n7":
            records.append(w4_rec(login, events=3))
        elif login == "n8":
            records.append(w4_rec(login, account=(NOW - timedelta(days=first_days + 89)).isoformat()))
        else:
            records.append(w4_rec(login))
    return prs, records


def test_newcomer_cohort_oracle_raw_vs_capped():
    prs, records = _build_newcomer_fixture()
    fields = gql.derive_cohort_fields(
        _w4_ah_data(prs, records, sampled=list(NEWCOMER_SPECS), cost=50, remaining=4800))
    assert fields["newcomer_sampled_author_count"] == (8, "verified")
    assert fields["newcomer_cohort_author_count"] == (8, "derived")
    assert fields["newcomer_conversion_raw"] == (0.75, "derived")            # 6/8: n3,n4 not converted
    assert fields["newcomer_conversion_raw_author_count"] == (8, "derived")
    assert fields["newcomer_conversion"] == (0.6667, "derived")              # 4/6: n7,n8 excluded (ineligible)
    assert fields["newcomer_conversion_author_count"] == (6, "derived")
    assert fields["rr90_return_rate_raw"] == (0.7143, "derived")             # 5/7: n4 not in population
    assert fields["rr90_return_raw_author_count"] == (7, "derived")
    assert fields["rr90_return_rate"] == (0.6, "derived")                    # 3/5: n7,n8 excluded
    assert fields["rr90_return_author_count"] == (5, "derived")
    authors = {a["login"]: a for a in fields["cohort_newcomer_authors"][0]}
    assert set(authors) == set(NEWCOMER_SPECS)
    n1_first_created = (NOW - timedelta(days=200)).isoformat()
    n1_merged_at = (NOW - timedelta(days=200) + timedelta(days=20)).isoformat()
    assert authors["n1"]["first_pr"] == {"number": 700, "created_at": n1_first_created,
                                         "merged_at": n1_merged_at}
    assert len(authors["n1"]["followups"]) == 1
    assert authors["n4"]["followups"] == []  # never merged, no second PR recorded


def test_newcomer_cohort_excludes_out_of_band_and_stale_evidence():
    """A sampled login outside [180 d, 365 d] is re-checked and dropped even
    though it is in ``sampled_authors`` (defence in depth); a login whose
    oldest RECORDED PR is older than the 365 d band start is dropped rather
    than treated as a genuine newcomer (recorded evidence of a pre-band PR,
    module note)."""
    too_recent = w4_days(1, 170, "n_recent")  # 170 d < 180 d band start
    stale_older = w4_days(2, 370, "n_stale")  # older than the 365 d band
    stale_newer = w4_days(3, 250, "n_stale")  # the same author's in-band PR
    fields = gql.derive_cohort_fields(_w4_ah_data(
        [too_recent, stale_older, stale_newer], [w4_rec("n_recent"), w4_rec("n_stale")],
        sampled=["n_recent", "n_stale"]))
    assert fields["newcomer_cohort_author_count"] == (0, "derived")
    assert fields["cohort_newcomer_authors"] == ([], "derived")


def test_derive_cohort_fields_missing_inputs():
    assert gql.derive_cohort_fields(None) == {}
    assert gql.derive_cohort_fields({}) == {}


# --- the append-only derive cohort block --------------------------------------

def _write_w4_envelopes(raw: Path, prs, records, **kwargs) -> None:
    write_envelope(raw / "author_history" / "acme__widget.json", source="test",
                   data=_w4_ah_data(list(prs), list(records), **kwargs),
                   fetched_at=NOW.isoformat())


def test_derive_repo_cohort_fields(tmp_path):
    data_dir = tmp_path
    _write_w4_envelopes(data_dir / "raw", DECISION_MAIN_PRS, DECISION_MAIN_RECORDS, cost=77)
    bundle = derive.derive_repo("acme", "widget", derive.Ecosystem.PYPI, data_dir,
                                fetch=False, now=NOW)
    meta = bundle["meta"]
    assert meta["dr90_decision_rate"] == 0.6667
    assert meta["dr90_decision_pr_count"] == 6
    assert meta["mr_first_rate"] == 0.5
    assert meta["mr_first_pr_count"] == 4
    assert meta["cohort_decision_pr_numbers"] == [200, 201, 202, 203, 204, 205, 206]
    assert meta["newcomer_cohort_author_count"] == 0
    assert meta["author_history_cost_points"] == 77
    assert bundle["confidence"]["dr90_decision_rate"] == "derived"
    assert bundle["confidence"]["author_history_cost_points"] == "verified"
    ah_path = str(data_dir / "raw" / "author_history" / "acme__widget.json")
    assert bundle["provenance"]["dr90_decision_rate"]["source"] == ah_path
    assert bundle["provenance"]["dr90_decision_rate"]["fetched_at"] == NOW.isoformat()


def test_derive_repo_without_author_history_envelope_no_cohort_fields(tmp_path):
    """Over-correction guard: the W1-3 pr_window path is unchanged when the
    author_history envelope is absent (no cohort fields at all)."""
    data_dir = tmp_path
    _write_pr_window_envelope(data_dir / "raw")
    bundle = derive.derive_repo("acme", "widget", derive.Ecosystem.PYPI, data_dir,
                                fetch=False, now=NOW)
    meta = bundle["meta"]
    for prefix in ("dr90_", "mr_first_", "newcomer_", "rr90_", "cohort_",
                  "author_history_", "decision_cohort_"):
        assert not any(k.startswith(prefix) for k in meta), prefix
    assert meta["pr_window_count"] == 4
    assert meta["pr_window_truncated"] is False


# --- the append-only gather hook ------------------------------------------------

def test_gather_repo_author_history_status_ok(tmp_path, monkeypatch):
    gather.DATA = tmp_path / "raw"
    seed_gather_done(gather.DATA)
    monkeypatch.setattr(gql, "collect_author_history", lambda *a, **k: "ok")
    status = gather.gather_repo("acme", "widget", "pypi")
    assert status["author_history"] == "ok"
    assert status["github_meta"] == "skip"


def test_gather_repo_author_history_status_skip(tmp_path, monkeypatch):
    gather.DATA = tmp_path / "raw"
    seed_gather_done(gather.DATA)
    write_env(gather.DATA, "author_history", KEY,
             {"window_days": 365, "count": 0, "truncated": False, "cost_points": 0,
              "rate_limit_remaining": None, "prs": [], "sampled_authors": [],
              "author_records": []})

    def must_not_fetch(*a, **k):
        raise AssertionError("collector must be skipped when the envelope exists")

    monkeypatch.setattr(gql, "collect_author_history", must_not_fetch)
    status = gather.gather_repo("acme", "widget", "pypi")
    assert status["author_history"] == "skip"


def test_gather_repo_author_history_status_fail(tmp_path, monkeypatch):
    gather.DATA = tmp_path / "raw"
    seed_gather_done(gather.DATA)

    def raise_transport_error(*a, **k):
        raise RuntimeError("gh api graphql rc=1: rate limited")

    monkeypatch.setattr(gql, "collect_author_history", raise_transport_error)
    status = gather.gather_repo("acme", "widget", "pypi")
    assert status["author_history"] == "fail"
