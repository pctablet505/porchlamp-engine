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
    "gfi": [],
    "actions_runs": [],
    "tree": {"paths": ["pyproject.toml"], "truncated": False},
    "commits_180d": [],
    "depsdev": None,
    "scorecard": None,
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


def test_gather_repo_pr_window_status_ok(tmp_path, monkeypatch):
    gather.DATA = tmp_path / "raw"
    seed_gather_done(gather.DATA)
    monkeypatch.setattr(gql, "collect_pr_window", lambda *a, **k: "ok")
    status = gather.gather_repo("acme", "widget", "pypi")
    assert status["pr_window"] == "ok"
    assert status["github_meta"] == "skip"
    assert status["repo_object"] == "skip"


def test_gather_repo_pr_window_status_skip(tmp_path, monkeypatch):
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


def test_gather_repo_pr_window_status_fail(tmp_path, monkeypatch):
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
