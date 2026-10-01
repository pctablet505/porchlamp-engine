"""Tests for windowed PR collection with typed actors and review threads.

Subject: ``porchlamp.collect.github_graphql``:
recorded-response oracles at the GraphQL transport boundary (no live calls)
asserting the exact values, pagination stop at the window
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

from porchlamp.collect import github_graphql as gql
from porchlamp.collect.envelope import envelope_hash, read_envelope, write_envelope
from porchlamp.collect import user_cache as gql_user_cache
from porchlamp.collect.user_cache import UserCache

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


gather = _load_script("porchlamp_gather_pr_window", "gather.py")
derive = _load_script("porchlamp_derive_pr_window", "derive.py")


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
        pr_node(24, created, reviews=[review("carol", assoc="NONE", body="r" * 200)]),
        pr_node(25, created, author_login="alice", reviews=[review("alice", assoc="NONE", body="r" * 200)]),
    ]
    out = gql.derive_pr_window(prs)
    assert [d["substantive_review"] for d in out] == [False, True, True, False, True, False]


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
    # Per-label gfi collector metadata-first record marks it done:
    "gfi": [{"gfi_newcomer_v2": True, "labels_matched": [], "queries": [],
             "truncated": False}],
    "actions_runs": [],
    "tree": {"paths": ["pyproject.toml"], "truncated": False},
    "commits_180d": [],
    "depsdev": None,
    "scorecard": None,
    # Marking other collectors done avoids taking live calls:
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
    # Marking governance done avoids live check-run/file-body calls:
    write_env(raw, "governance", KEY, {})


def test_gather_repo_pr_window_status_ok(tmp_path, monkeypatch, block_live_network):
    gather.DATA = tmp_path / "raw"
    seed_gather_done(gather.DATA)
    # Marking author_history done avoids live calls:
    write_env(gather.DATA, "author_history", KEY, {})
    monkeypatch.setattr(gql, "collect_pr_window", lambda *a, **k: "ok")
    status = gather.gather_repo("acme", "widget", "pypi")
    assert status["pr_window"] == "ok"
    assert status["github_meta"] == "skip"
    assert status["repo_object"] == "skip"


def test_gather_repo_pr_window_status_skip(tmp_path, monkeypatch, block_live_network):
    gather.DATA = tmp_path / "raw"
    seed_gather_done(gather.DATA)
    write_env(gather.DATA, "author_history", KEY, {})
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
    write_env(gather.DATA, "author_history", KEY, {})

    def raise_transport_error(*a, **k):
        raise RuntimeError("gh api graphql rc=1: rate limited")

    monkeypatch.setattr(gql, "collect_pr_window", raise_transport_error)
    status = gather.gather_repo("acme", "widget", "pypi")
    assert status["pr_window"] == "fail"


def test_gather_passes_one_user_cache_beside_data(tmp_path, monkeypatch, block_live_network):
    """author_history gets the D-COL-5 cache at ``<data_root>/state/users``,
    derived from ``DATA`` (so a test's tree, never the real one), and the
    SAME cache object for every repo of a run -- a fresh cache per repo
    would silently turn the universe-wide lookup back into a per-repo one."""
    gather.DATA = tmp_path / "raw"
    seen = []

    def fake_collect(raw_dir, owner, name, **kwargs):
        seen.append(kwargs.get("user_cache"))
        return "ok"

    monkeypatch.setattr(gql, "collect_author_history", fake_collect)
    only = frozenset({"author_history"})
    assert gather.gather_repo("acme", "widget", "pypi", collectors=only)["author_history"] == "ok"
    assert gather.gather_repo("acme", "gadget", "pypi", collectors=only)["author_history"] == "ok"
    assert len(seen) == 2
    assert seen[0] is seen[1]
    assert isinstance(seen[0], UserCache)
    assert seen[0].root == tmp_path / "state" / "users"


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
        "raw/pr_window/acme__widget.json"


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
# Author history and newcomer cohorts
# =============================================================================
#
# Subject: porchlamp.collect.github_graphql's author section (recorded-response
# oracles, no live calls) plus the append-only hooks at the end of
# scripts/gather.py (the ``author_history`` status entry) and
# scripts/derive.py (the cohort bundle fields).

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
    """A recorded author_history envelope ``data``."""
    return {"window_anchor": anchor.isoformat(), "window_days": 365, "cap": 500,
            "count": len(prs), "truncated": truncated, "cost_points": cost,
            "rate_limit_remaining": remaining, "prs": list(prs),
            "sampled_authors": list(sampled), "author_records": list(records)}


def _w4_page(nodes: list[dict], has_next: bool, cursor: str = "") -> dict:
    return {"nodes": nodes,
            "pageInfo": {"hasNextPage": has_next, "endCursor": cursor}}


class ResourceLimited:
    """Sentinel: this login's alias comes back null because of
    GitHub's per-query resource budget, not a deleted/renamed account —
    the live signature ``_fetch_eligibility_batch`` reads to shrink its
    batch width for the rest of the run."""


RESOURCE_LIMITED = ResourceLimited()


class NoDataBody:
    """Sentinel: a batched search that includes this login answers with a
    body carrying neither ``data`` nor ``errors`` (GitHub's rate-limit
    answer) -- nothing in the call resolves."""


NO_DATA_BODY = NoDataBody()


class DataWithError:
    """Sentinel: this login's alias carries ``value`` AND an ``errors``
    entry addressed inside it -- partial data from a failed search."""

    def __init__(self, value: dict):
        self.value = value


class ResolvesBelowWidth:
    """Sentinel: this login's alias errors with GitHub's
    resource-budget message whenever the CURRENT query has more than
    ``threshold`` aliases, and resolves with ``value`` otherwise —
    simulates a login whose contribution calendar is only over-budget
    inside a large batch, recovering once a narrower retry queries it
    alongside fewer others."""

    def __init__(self, threshold: int, value: dict):
        self.threshold = threshold
        self.value = value


class W4Transport:
    """Recorded-response transport for the author section: scripted window
    pages, per-author search nodes served through the batched
    ``AuthorPrsSearchBatch`` alias query, and per-login eligibility
    documents served through the batched ``AuthorEligibilityBatch`` alias
    query, with scripted point cost and ``remaining`` per call. An
    unrecorded author is a failure, not a default. ``None`` for a login's
    search makes its alias null with an ``errors`` entry addressed to it
    (one failed search inside a batch that still resolves the others);
    ``NO_DATA_BODY`` makes the whole call answer with a rate-limit body;
    ``search_raise_on_call`` (0-indexed search-call numbers) makes the
    transport itself raise. MEASURED 2026-09-22: a search for a login that
    does not exist answers ``{"nodes": []}`` with no error, so that case is
    scripted as an empty node list, not as ``None``.

    Eligibility responses are LIVE-shaped. ``None`` for a login's user
    document produces a null alias PLUS a path-addressed ``errors`` entry
    in the SAME response (a deleted/renamed account still resolving its
    other aliases, exactly like ``gh api graphql`` on a real partially-
    successful batch — it never raises for this, matching the
    ``GhCliTransport`` fix); ``RESOURCE_LIMITED`` does the same with
    GitHub's own resource-budget error text. ``eligibility_raise_on_call``
    (a set of 0-indexed eligibility-call numbers) makes the transport
    itself raise ``RuntimeError`` — a true unparseable-body failure,
    distinct from an errors-carrying response. The query text is parsed
    (not just ``variables``) to assert the alias/variable correspondence
    the live query depends on.
    """

    def __init__(self, window_pages, window_costs, window_remaining, searches, users,
                search_batch_meta=None, eligibility_batch_meta=None,
                eligibility_raise_on_call=None, search_raise_on_call=None):
        self.window_pages = list(window_pages)
        self.window_costs = list(window_costs)
        self.window_remaining = list(window_remaining)
        self.searches = dict(searches)
        self.users = dict(users)
        self.search_batch_meta = list(search_batch_meta or [])
        self.eligibility_batch_meta = list(eligibility_batch_meta or [])
        self.eligibility_raise_on_call = set(eligibility_raise_on_call or ())
        self.search_raise_on_call = set(search_raise_on_call or ())
        self.calls: list[tuple[str, dict]] = []
        self.eligibility_batch_sizes: list[int] = []
        self.search_batch_sizes: list[int] = []
        self.searched_logins: list[str] = []
        self._eligibility_batches = 0
        self._search_batches = 0

    def execute(self, query: str, variables: dict) -> dict:
        self.calls.append((query, dict(variables)))
        if "pullRequests(states" in query:
            n = sum(1 for q, _ in self.calls if "pullRequests(states" in q)
            assert n <= len(self.window_pages), "transport called past recorded pages"
            return {"data": {"rateLimit": {"cost": self.window_costs[n - 1],
                                          "remaining": self.window_remaining[n - 1]},
                            "r": {"pullRequests": self.window_pages[n - 1]}}}
        if "AuthorPrsSearchBatch" in query:
            batch_idx = self._search_batches
            self._search_batches += 1
            n = sum(1 for key in variables if key.startswith("q"))
            self.search_batch_sizes.append(n)
            # Parse the query text itself: every alias s{i} paired with $q{i},
            # exactly n of them, all sharing the one $first variable.
            for i in range(n):
                assert f"s{i}: search(query: $q{i}, type: ISSUE, first: $first)" in query, \
                    f"alias s{i} not paired with $q{i} in query text"
            assert query.count("search(query:") == n
            assert variables["first"] == gql.AUTHOR_SEARCH_FIRST
            if batch_idx in self.search_raise_on_call:
                raise RuntimeError("gh api graphql rc=1: simulated transport failure")
            data: dict = {}
            errors: list[dict] = []
            for i in range(n):
                q = variables[f"q{i}"]
                assert q.startswith("repo:acme/") and " is:pr author:" in q, q
                login = q.rsplit("author:", 1)[-1]
                assert login in self.searches, f"unrecorded search for {login}"
                self.searched_logins.append(login)
                val = self.searches[login]
                if val is NO_DATA_BODY:
                    # A secondary-rate-limit answer: JSON, but neither data
                    # nor errors -- the whole call carries nothing.
                    return {"message": "You have exceeded a secondary rate limit."}
                if val is None:
                    data[f"s{i}"] = None
                    errors.append({"path": [f"s{i}"],
                                  "message": "Something went wrong while executing your query."})
                elif isinstance(val, DataWithError):
                    data[f"s{i}"] = val.value
                    errors.append({"path": [f"s{i}", "nodes", 0],
                                  "message": "Something went wrong while executing your query."})
                else:
                    data[f"s{i}"] = val
            data["rateLimit"] = (self.search_batch_meta[batch_idx]
                                 if batch_idx < len(self.search_batch_meta)
                                 else {"cost": 1, "remaining": 999})
            doc = {"data": data}
            if errors:
                doc["errors"] = errors
            return doc
        if "AuthorEligibilityBatch" in query:
            batch_idx = self._eligibility_batches
            self._eligibility_batches += 1
            n = len(variables)
            self.eligibility_batch_sizes.append(n)
            # Parse the query text itself (not just `variables`): every
            # alias must be u{i} paired with $l{i}, exactly n of them, each
            # login appearing exactly once.
            for i in range(n):
                assert f"u{i}: user(login: $l{i})" in query, \
                    f"alias u{i} not paired with $l{i} in query text"
            assert query.count("user(login:") == n, \
                f"query has {query.count('user(login:')} user() fields, expected {n}"
            if batch_idx in self.eligibility_raise_on_call:
                raise RuntimeError("gh api graphql rc=1: simulated transport failure")
            data: dict = {}
            errors: list[dict] = []
            for i in range(n):
                login = str(variables[f"l{i}"]).lower()
                assert login in self.users, f"unrecorded user {login}"
                val = self.users[login]
                if isinstance(val, ResolvesBelowWidth):
                    val = val.value if n <= val.threshold else RESOURCE_LIMITED
                if val is RESOURCE_LIMITED:
                    data[f"u{i}"] = None
                    errors.append({"path": [f"u{i}", "contributionsCollection",
                                            "contributionCalendar", "totalContributions"],
                                  "message": "Resource limits for this query exceeded."})
                elif val is None:
                    data[f"u{i}"] = None
                    errors.append({"path": [f"u{i}"],
                                  "message": f"Could not resolve to a User "
                                            f"with the login of '{login}'."})
                else:
                    data[f"u{i}"] = val
            rl = (self.eligibility_batch_meta[batch_idx]
                 if batch_idx < len(self.eligibility_batch_meta)
                 else {"cost": 1, "remaining": 999})
            data["rateLimit"] = rl
            doc: dict = {"data": data}
            if errors:
                doc["errors"] = errors
            return doc
        raise AssertionError(f"unexpected query: {query[:80]}")

    @property
    def n_window_calls(self) -> int:
        return sum(1 for q, _ in self.calls if "pullRequests(states" in q)

    @property
    def n_search_calls(self) -> int:
        return sum(1 for q, _ in self.calls if "AuthorPrsSearchBatch" in q)

    @property
    def n_eligibility_batch_calls(self) -> int:
        return sum(1 for q, _ in self.calls if "AuthorEligibilityBatch" in q)


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
    earliest by creation date — sorted login, then a seeded choice,
    not "the oldest 20" (which would sample only the band's earliest slice)."""
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
        "new1": None,  # unresolved alias inside the batch: eligibility_unmeasured
        "new2": {"createdAt": "2015-03-03T00:00:00Z",
                "contributionsCollection": {"contributionCalendar": {"totalContributions": 200}}},
    }
    fake = W4Transport(_w4_main_window_pages(), [10, 12], [4990, 4980], searches, users,
                       search_batch_meta=[{"cost": 2, "remaining": 4978},
                                         {"cost": 1, "remaining": 4977},
                                         {"cost": 1, "remaining": 4976}],
                       eligibility_batch_meta=[{"cost": 1, "remaining": 4971},
                                              {"cost": 1, "remaining": 4970},
                                              {"cost": 1, "remaining": 4969}])
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
    # 10+12 window points; searches: ONE batched call for ext1/new1/new2
    # (2 pts) resolves ext1 and new2, and new1's failed alias is retried
    # alone at width 5 and width 1 (1 pt each) -> 4; eligibility: the fresh
    # batch resolves ext1/new2 and new1 is retried the same way -> 3 calls,
    # 1 pt each. A failed alias still costs its call's points.
    assert d["cost_points"] == 29
    # the last (width-1) retry call executes last.
    assert d["rate_limit_remaining"] == 4969
    # 2 band candidates, under the 20-author cap: both kept, sorted by
    # login (not by creation date — new2's first PR is the older one).
    assert d["sampled_authors"] == ["new1", "new2"]
    recs = {r["login"]: r for r in d["author_records"]}
    assert set(recs) == {"ext1", "new1", "new2"}  # bob/bot never fetched
    assert recs["ext1"] == {"login": "ext1", "account_created_at": "2019-05-05T00:00:00Z",
                            "public_event_count": 40,
                            "prs": [{"number": 100, "createdAt": "2026-06-01T00:00:00Z",
                                     "closedAt": "2026-06-05T00:00:00Z", "mergedAt": None,
                                     "state": "CLOSED"},
                                    {"number": 103, "createdAt": "2026-05-15T00:00:00Z",
                                     "closedAt": None, "mergedAt": None, "state": "OPEN"}],
                            "history_unmeasured": False, "history_unsampled": False,
                            "eligibility_unmeasured": False,
                            "eligibility_observed_at": NOW.isoformat()}
    # new1's search failed (history_unmeasured) and its eligibility alias
    # came back unresolved (eligibility_unmeasured) — degraded to no
    # history/account, not a crash.
    assert recs["new1"] == {"login": "new1", "account_created_at": None,
                            "public_event_count": None, "prs": [],
                            "history_unmeasured": True, "history_unsampled": False,
                            "eligibility_unmeasured": True,
                            "eligibility_observed_at": None}
    assert recs["new2"]["history_unmeasured"] is False
    assert recs["new2"]["eligibility_unmeasured"] is False
    assert len(fake.calls) == 8  # 2 window + 3 search + 3 eligibility calls
    assert fake.search_batch_sizes == [3, 1, 1]  # one batch, then new1 alone twice
    assert fake.n_eligibility_batch_calls == 3
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


# --- First-timer merge-time predicate, unmeasured-author exclusion,
# AUTHOR_SEARCH_CAP sample, and batched eligibility ---------------------------

def test_no_prior_merged_prior_pr_merged_after_this_pr_created_still_first_timer():
    """An earlier-CREATED PR that merges AFTER this PR's ``createdAt``
    does not disqualify — the predicate is merge time, not creation time."""
    earlier_created_later_merged = w4_pr(1, "2026-01-01T00:00:00Z", "x",
                                        merged="2026-09-01T00:00:00Z")
    this_pr = w4_pr(2, "2026-03-01T00:00:00Z", "x")
    assert gql._no_prior_merged(this_pr, [earlier_created_later_merged, this_pr], {}) is True


def test_no_prior_merged_prior_pr_merged_before_this_pr_created_excluded():
    """A PR merged strictly before this PR's ``createdAt`` correctly disqualifies."""
    earlier_merged = w4_pr(1, "2026-01-01T00:00:00Z", "x", merged="2026-02-01T00:00:00Z")
    this_pr = w4_pr(2, "2026-03-01T00:00:00Z", "x")
    assert gql._no_prior_merged(this_pr, [earlier_merged, this_pr], {}) is False


def test_no_prior_merged_ignores_an_earlier_unmerged_pr():
    """An earlier PR that was never merged must not disqualify a first-timer,
    even though it was created earlier."""
    earlier_unmerged = w4_days(1, 200, "x", closed_after=5)  # closed, not merged
    this_pr = w4_days(2, 100, "x")
    assert gql._no_prior_merged(this_pr, [earlier_unmerged, this_pr], {}) is True


def test_no_prior_merged_excludes_history_unmeasured_author():
    """A failed per-author search (``history_unmeasured``) must return
    None (unknown), never True (first-timer)."""
    this_pr = w4_days(1, 150, "vet")
    records = {"vet": {"prs": [], "history_unmeasured": True, "history_unsampled": False}}
    assert gql._no_prior_merged(this_pr, [this_pr], records) is None


def test_no_prior_merged_excludes_history_unsampled_author():
    """An author outside the AUTHOR_SEARCH_CAP sample is unknown, not a first-timer."""
    this_pr = w4_days(1, 150, "vet")
    records = {"vet": {"prs": [], "history_unmeasured": False, "history_unsampled": True}}
    assert gql._no_prior_merged(this_pr, [this_pr], records) is None


def _b2_repro_prs():
    """A veteran ('vet') with a merged PR outside the 365-d window
    plus one merged in-cohort PR, and 9 single-PR authors decided-not-
    merged in the decision band."""
    vet_cohort = w4_days(901, 150, "vet", merged_after=3)
    others = [w4_days(902 + i, 150 + i, f"g{i}", closed_after=5) for i in range(9)]
    return [vet_cohort] + others


def test_b2_veteran_prior_merge_measured_excludes_from_mr_first():
    """Measured baseline: vet's search finds their real, older merged PR
    (outside the 365-d window, recorded only via the search), so vet is
    correctly excluded from the first-timer candidate pool: 0/9, not the
    10-PR population a first-timer default would produce."""
    prs = _b2_repro_prs()
    vet_record = w4_rec("vet", prs=[w4_spr(900, (NOW - timedelta(days=500)).isoformat(),
                                          merged=(NOW - timedelta(days=495)).isoformat())])
    records = [vet_record] + [w4_rec(f"g{i}") for i in range(9)]
    fields = gql.derive_cohort_fields(_w4_ah_data(prs, records))
    assert fields["mr_first_rate_raw"] == (0.0, "derived")
    assert fields["mr_first_pr_count_raw"] == (9, "derived")


def test_b2_veteran_history_fetch_failure_does_not_flip_mr_first():
    """Fail-before (this exact review repro): pre-fix, a failed search left
    ``prs: []`` and vet's own merged in-cohort PR was wrongly counted as a
    first-timer merge, flipping MR_first from 0.0/9 to 0.1/10 (review B2).
    Fixed: ``history_unmeasured`` excludes vet's PR from the population
    entirely — the same 0.0/9 as the measured case above, never defaulted
    to "first-timer"."""
    prs = _b2_repro_prs()
    vet_record = {"login": "vet", "account_created_at": "2015-01-01T00:00:00Z",
                 "public_event_count": 50, "prs": [], "history_unmeasured": True}
    records = [vet_record] + [w4_rec(f"g{i}") for i in range(9)]
    fields = gql.derive_cohort_fields(_w4_ah_data(prs, records))
    assert fields["mr_first_rate_raw"] == (0.0, "derived")
    assert fields["mr_first_pr_count_raw"] == (9, "derived")


def test_b2_dr90_unaffected_by_history_unmeasured():
    """DR_90 does not read per-author search history at all (review §4.4):
    the same envelope's DR_90 is identical whether vet's search succeeded
    or failed."""
    prs = _b2_repro_prs()
    measured = [w4_rec("vet", prs=[w4_spr(900, (NOW - timedelta(days=500)).isoformat(),
                                         merged=(NOW - timedelta(days=495)).isoformat())])] + \
              [w4_rec(f"g{i}") for i in range(9)]
    unmeasured = [{"login": "vet", "account_created_at": "2015-01-01T00:00:00Z",
                  "public_event_count": 50, "prs": [], "history_unmeasured": True}] + \
                [w4_rec(f"g{i}") for i in range(9)]
    dr90_measured = gql.derive_cohort_fields(_w4_ah_data(prs, measured))["dr90_decision_rate_raw"]
    dr90_unmeasured = gql.derive_cohort_fields(_w4_ah_data(prs, unmeasured))["dr90_decision_rate_raw"]
    assert dr90_measured == dr90_unmeasured
    assert dr90_measured == (1.0, "derived")


# --- Boundary conditions and edge cases -------------------------------------

def test_decision_band_near_edge_90_vs_89_days():
    """A PR opened exactly 90 d ago is IN the decision band; 89 d
    ago is not yet (age < DECISION_WINDOW_DAYS)."""
    pr90 = w4_days(1, 90, "x")
    pr89 = w4_days(2, 89, "x")
    assert gql._in_decision_band(pr90, NOW) is True
    assert gql._in_decision_band(pr89, NOW) is False


def test_decision_band_far_edge_270_vs_271_days():
    """A PR opened exactly 270 d ago is still IN the decision
    band; 271 d ago is outside it."""
    pr270 = w4_days(1, 270, "x")
    pr271 = w4_days(2, 271, "x")
    assert gql._in_decision_band(pr270, NOW) is True
    assert gql._in_decision_band(pr271, NOW) is False


def test_decided_within_90_vs_91_days():
    """A PR closed exactly 90 d after opening counts as decided
    within the DR_90 window; 91 d does not."""
    pr90 = w4_days(1, 200, "x", closed_after=90)
    pr91 = w4_days(2, 200, "x", closed_after=91)
    assert gql._decided_within_days(pr90, gql.DECISION_WINDOW_DAYS) is True
    assert gql._decided_within_days(pr91, gql.DECISION_WINDOW_DAYS) is False


def test_newcomer_band_newer_edge_180_vs_179_days():
    """A first PR opened exactly 180 d ago is IN the newcomer
    band; 179 d ago (more recent than the band's newer edge) is not."""
    pr180 = [w4_days(1, 180, "n180")]
    pr179 = [w4_days(2, 179, "n179")]
    assert gql._select_newcomer_sample(pr180, NOW, truncated=False) == ("n180",)
    assert gql._select_newcomer_sample(pr179, NOW, truncated=False) == ()


def test_apply_author_cap_fraction_20_vs_22_percent_boundary():
    """floor(0.20*54) = 10 but floor(0.22*54) = 11 — a population
    size where the two cap fractions diverge."""
    items = [("sock", NOW - timedelta(days=d)) for d in range(1, 31)] + \
            [(f"g{i}", NOW - timedelta(days=100 + i)) for i in range(24)]
    assert len(items) == 54
    kept = gql.apply_author_cap(items)
    assert len(kept) == 34  # sock capped to its 10 oldest + 24 singles
    assert sum(1 for i in kept if items[i][0] == "sock") == 10


# --- AUTHOR_SEARCH_CAP sampling and batched eligibility fetch ---------------

def test_seeded_sample_returns_all_when_at_or_under_size():
    assert gql._seeded_sample(["b", "a", "c"], 5) == ("a", "b", "c")


def test_seeded_sample_50_of_80_is_deterministic_not_sorted_first_50():
    """Reproducible (same input, same output), and not simply the sorted-first 50 —
    the same seeded-choice property :func:`_select_newcomer_sample` relies on."""
    candidates = [f"u{i:03d}" for i in range(80)]
    sample = gql._seeded_sample(candidates, 50)
    again = gql._seeded_sample(candidates, 50)
    assert sample == again
    assert len(sample) == 50
    assert list(sample) == sorted(sample)
    assert sample != tuple(f"u{i:03d}" for i in range(50))


def test_collect_author_history_search_cap_marks_unsampled_authors(tmp_path):
    """When the union of decision-cohort + newcomer-sample authors exceeds
    AUTHOR_SEARCH_CAP, only the deterministic sample is searched — every other
    needed author is ``history_unsampled`` (never searched, cost 0), not
    defaulted to "no history"."""
    raw = tmp_path / "raw"
    # 60 decision-cohort authors (one PR each, 150 d ago) push ``needed``
    # to 60, above the 50-author search cap.
    prs = [w4_days(1000 + i, 150, f"d{i:03d}") for i in range(60)]
    fake_window = _w4_page(prs, False)
    users = {f"d{i:03d}": {"createdAt": "2015-01-01T00:00:00Z",
                          "contributionsCollection": {"contributionCalendar": {"totalContributions": 50}}}
            for i in range(60)}
    searches = {f"d{i:03d}": {"nodes": []} for i in range(60)}
    fake = W4Transport([fake_window], [3], [4990], searches, users)
    assert gql.collect_author_history(raw, "acme", "widget", now=NOW, transport=fake) == "ok"
    d = read_envelope(raw / "author_history" / "acme__widget.json").data
    recs = {r["login"]: r for r in d["author_records"]}
    assert len(recs) == 60
    sampled = [login for login, r in recs.items() if not r["history_unsampled"]]
    unsampled = [login for login, r in recs.items() if r["history_unsampled"]]
    assert len(sampled) == 50
    assert len(unsampled) == 10
    # only the sampled authors are searched, each once, 25 to a call
    assert sorted(fake.searched_logins) == sorted(sampled)
    assert fake.search_batch_sizes == [25, 25]
    assert set(sampled) == set(gql._seeded_sample(sorted(recs), gql.AUTHOR_SEARCH_CAP))
    assert fake.n_eligibility_batch_calls == 4  # ceil(60 / 15): every author, uncapped


def test_dr90_unaffected_by_search_cap(tmp_path):
    """The 10 authors excluded from the search sample above still get a
    full, decided DR_90 cohort: eligibility (not search) drives DR_90, and
    eligibility is never sampled."""
    raw = tmp_path / "raw"
    prs = [w4_days(1000 + i, 150, f"d{i:03d}", closed_after=5) for i in range(60)]
    fake_window = _w4_page(prs, False)
    users = {f"d{i:03d}": {"createdAt": "2015-01-01T00:00:00Z",
                          "contributionsCollection": {"contributionCalendar": {"totalContributions": 50}}}
            for i in range(60)}
    searches = {f"d{i:03d}": {"nodes": []} for i in range(60)}
    fake = W4Transport([fake_window], [3], [4990], searches, users)
    gql.collect_author_history(raw, "acme", "widget", now=NOW, transport=fake)
    d = read_envelope(raw / "author_history" / "acme__widget.json").data
    fields = gql.derive_cohort_fields(d)
    assert fields["dr90_decision_pr_count_raw"] == (60, "derived")
    assert fields["dr90_decision_rate_raw"] == (1.0, "derived")
    assert fields["dr90_decision_pr_count_author_history_cohort"] == (60, "derived")
    assert fields["dr90_decision_rate_author_history_cohort"] == (1.0, "derived")


# --- Batched per-author searches (AUTHOR_SEARCH_BATCH_SIZE) -----------------

def _decision_authors(logins, events=50, closed_after=None):
    """One decision-cohort PR (150 d old) per login, plus an eligibility
    document per login (account from 2015, ``events`` contributions --
    a dict maps login -> count)."""
    prs = [w4_days(1000 + i, 150, login, closed_after=closed_after)
           for i, login in enumerate(logins)]
    users = {login: {"createdAt": "2015-01-01T00:00:00Z",
                     "contributionsCollection": {"contributionCalendar": {
                         "totalContributions": events[login] if isinstance(events, dict) else events}}}
             for login in logins}
    return prs, users


def _records(raw, key=KEY):
    data = read_envelope(raw / "author_history" / f"{key}.json").data
    return {r["login"]: r for r in data["author_records"]}


def _eligibility_requests(fake):
    """The logins each eligibility call asked for, in call order."""
    return [[variables[f"l{i}"] for i in range(len(variables))]
            for query, variables in fake.calls if "AuthorEligibilityBatch" in query]


def test_author_search_batch_query_alias_variable_correspondence():
    for n in (1, 3, gql.AUTHOR_SEARCH_BATCH_SIZE):
        query = gql._author_search_batch_query(n)
        assert query.count("search(query:") == n
        for i in range(n):
            assert f"s{i}: search(query: $q{i}, type: ISSUE, first: $first)" in query
            assert f"$q{i}: String!" in query
        assert query.count("$first: Int") == 1


def test_batched_searches_keep_each_authors_own_nodes(tmp_path):
    """30 searched authors take 2 calls (25 + 5), not 30, and batching never
    mixes authors up: each record holds exactly its own scripted nodes.
    Over-correction guard: an author whose search finds nothing is MEASURED
    with no PRs -- MEASURED 2026-09-22, a search for a login that does not
    exist answers ``{"nodes": []}`` with no error, and that is an answer."""
    raw = tmp_path / "raw"
    logins = [f"d{i:03d}" for i in range(30)]
    prs, users = _decision_authors(logins)
    searches = {login: {"nodes": [w4_spr(5000 + i, "2026-01-01T00:00:00Z")]}
                for i, login in enumerate(logins)}
    searches["d007"] = {"nodes": []}
    fake = W4Transport([_w4_page(prs, False)], [3], [4990], searches, users)
    assert gql.collect_author_history(raw, "acme", "widget", now=NOW, transport=fake) == "ok"
    recs = _records(raw)
    assert fake.search_batch_sizes == [25, 5]
    for i, login in enumerate(logins):
        assert recs[login]["history_unmeasured"] is False
        expected = [] if login == "d007" else [5000 + i]
        assert [pr["number"] for pr in recs[login]["prs"]] == expected


def test_one_failed_alias_retries_only_that_author(tmp_path):
    """The other 24 aliases of a batch are kept at once; only the failed
    author is retried (width 5, then 1) and only it is unmeasured."""
    raw = tmp_path / "raw"
    logins = [f"d{i:03d}" for i in range(30)]
    prs, users = _decision_authors(logins)
    searches = {login: {"nodes": []} for login in logins}
    searches["d003"] = None
    fake = W4Transport([_w4_page(prs, False)], [3], [4990], searches, users)
    gql.collect_author_history(raw, "acme", "widget", now=NOW, transport=fake)
    recs = _records(raw)
    assert [login for login, r in recs.items() if r["history_unmeasured"]] == ["d003"]
    assert fake.search_batch_sizes == [25, 1, 1, 5]


def test_a_rate_limit_body_is_unmeasured_never_an_empty_history(tmp_path):
    """FAIL-BEFORE (the one-search loop, reproduced 2026-09-22): a search
    answered with GitHub's rate-limit body -- JSON with neither ``data``
    nor ``errors`` -- was recorded as an author with NO PRs and
    ``history_unmeasured`` False, which MR_first then counts as a
    first-timer. It is now unmeasured, and the authors that shared its call
    are retried narrower and resolve."""
    raw = tmp_path / "raw"
    prs, users = _decision_authors(["d000", "d001", "d002"])
    merged = w4_spr(1, "2026-01-01T00:00:00Z", merged="2026-01-02T00:00:00Z")
    searches = {"d000": {"nodes": [merged]}, "d001": NO_DATA_BODY, "d002": {"nodes": []}}
    fake = W4Transport([_w4_page(prs, False)], [3], [4990], searches, users)
    gql.collect_author_history(raw, "acme", "widget", now=NOW, transport=fake)
    recs = _records(raw)
    assert recs["d001"]["history_unmeasured"] is True
    assert recs["d001"]["prs"] == []
    assert recs["d000"]["history_unmeasured"] is False
    assert recs["d000"]["prs"] == [merged]
    assert recs["d002"]["history_unmeasured"] is False
    assert fake.search_batch_sizes == [3, 3, 1, 1, 1]


def test_an_alias_with_data_and_its_own_error_is_not_kept(tmp_path):
    """One search that fails with ANY error is unmeasured, even with data
    alongside it -- the one-search loop's rule, kept inside a batch."""
    raw = tmp_path / "raw"
    prs, users = _decision_authors(["d000", "d001"])
    partial = DataWithError({"nodes": [w4_spr(9, "2026-01-01T00:00:00Z")]})
    fake = W4Transport([_w4_page(prs, False)], [3], [4990],
                       {"d000": {"nodes": []}, "d001": partial}, users)
    gql.collect_author_history(raw, "acme", "widget", now=NOW, transport=fake)
    recs = _records(raw)
    assert recs["d001"]["history_unmeasured"] is True
    assert recs["d001"]["prs"] == []
    assert recs["d000"]["history_unmeasured"] is False


def test_an_error_without_a_path_leaves_every_alias_unresolved():
    """An ``errors`` entry with no path (a timeout, a server fault) is
    addressed to no single alias, so the data that came back beside it is
    not trusted for any of them."""
    class GlobalErrorTransport:
        def execute(self, query, variables):
            return {"data": {"rateLimit": {"cost": 1, "remaining": 900},
                             "s0": {"nodes": []}, "s1": {"nodes": []}},
                    "errors": [{"message": "Something went wrong while executing your query."}]}

    resolved, unresolved, cost, remaining = gql._run_author_search_query(
        "acme", "widget", ["a", "b"], GlobalErrorTransport())
    assert resolved == {}
    assert unresolved == ["a", "b"]
    assert (cost, remaining) == (1, 900)


def test_search_transport_failure_retries_narrower(tmp_path):
    raw = tmp_path / "raw"
    logins = [f"d{i:03d}" for i in range(7)]
    prs, users = _decision_authors(logins)
    fake = W4Transport([_w4_page(prs, False)], [3], [4990],
                       {login: {"nodes": []} for login in logins}, users,
                       search_raise_on_call={0})
    gql.collect_author_history(raw, "acme", "widget", now=NOW, transport=fake)
    assert not any(r["history_unmeasured"] for r in _records(raw).values())
    assert fake.search_batch_sizes == [7, 5, 2]


# --- D-COL-5 user cache inside collect_author_history -----------------------

def test_user_cache_second_repo_looks_up_only_logins_it_has_never_seen(tmp_path):
    """FAIL-BEFORE: with no cache, repo B looked up d001 and d002 again
    although repo A had just done so. With the cache, B's only eligibility
    call asks for d003, and the reused values keep A's observation time."""
    cache = UserCache(tmp_path / "state" / "users")
    prs_a, users_a = _decision_authors(["d000", "d001", "d002"])
    fake_a = W4Transport([_w4_page(prs_a, False)], [3], [4990],
                         {login: {"nodes": []} for login in users_a}, users_a)
    gql.collect_author_history(tmp_path / "raw", "acme", "widget", now=NOW,
                               transport=fake_a, user_cache=cache)
    assert _eligibility_requests(fake_a) == [["d000", "d001", "d002"]]

    later = NOW + timedelta(days=1)
    prs_b, users_b = _decision_authors(["d001", "d002", "d003"])
    fake_b = W4Transport([_w4_page(prs_b, False)], [3], [4990],
                         {login: {"nodes": []} for login in users_b}, users_b)
    gql.collect_author_history(tmp_path / "raw", "acme", "gadget", now=later,
                               transport=fake_b, user_cache=cache)
    assert _eligibility_requests(fake_b) == [["d003"]]
    recs = _records(tmp_path / "raw", "acme__gadget")
    assert recs["d001"]["eligibility_observed_at"] == NOW.isoformat()
    assert recs["d002"]["eligibility_observed_at"] == NOW.isoformat()
    assert recs["d003"]["eligibility_observed_at"] == later.isoformat()
    assert recs["d001"]["public_event_count"] == 50
    assert recs["d001"]["account_created_at"] == "2015-01-01T00:00:00Z"
    assert cache.stats["hits"] == 2
    assert cache.stats["writes"] == 4


def test_user_cache_expired_entry_is_looked_up_again_once(tmp_path):
    cache = UserCache(tmp_path / "users")
    stale = {"account_created_at": "2015-01-01T00:00:00Z", "public_event_count": 3,
             "eligibility_unmeasured": False}
    cache.put("d000", stale, NOW - timedelta(days=gql_user_cache.USER_CACHE_TTL_DAYS + 1))
    cache.put("d001", {**stale, "public_event_count": 50}, NOW - timedelta(days=10))
    prs, users = _decision_authors(["d000", "d001"])
    fake = W4Transport([_w4_page(prs, False)], [3], [4990],
                       {login: {"nodes": []} for login in users}, users)
    gql.collect_author_history(tmp_path / "raw", "acme", "widget", now=NOW,
                               transport=fake, user_cache=cache)
    assert _eligibility_requests(fake) == [["d000"]]
    recs = _records(tmp_path / "raw")
    assert recs["d000"]["public_event_count"] == 50  # the fresh value, not the stale 3
    assert recs["d000"]["eligibility_observed_at"] == NOW.isoformat()
    assert recs["d001"]["eligibility_observed_at"] == (NOW - timedelta(days=10)).isoformat()
    assert cache.get("d000", NOW)["public_event_count"] == 50  # rewritten


def test_unmeasured_eligibility_is_not_cached_and_is_asked_again(tmp_path):
    cache = UserCache(tmp_path / "users")
    prs, users = _decision_authors(["d000"])
    users["d000"] = None  # the alias never resolves
    for repo in ("widget", "gadget"):
        fake = W4Transport([_w4_page(prs, False)], [3], [4990], {"d000": {"nodes": []}}, users)
        gql.collect_author_history(tmp_path / "raw", "acme", repo, now=NOW,
                                   transport=fake, user_cache=cache)
        assert _eligibility_requests(fake) == [["d000"], ["d000"], ["d000"]]
        rec = _records(tmp_path / "raw", f"acme__{repo}")["d000"]
        assert rec["eligibility_unmeasured"] is True
        assert rec["eligibility_observed_at"] is None
    assert not (tmp_path / "users" / "d000.json").exists()


def test_cached_eligibility_derives_the_same_fields_as_a_fresh_lookup(tmp_path):
    """Over-correction guard: the cache changes where an eligibility value
    came from, never what it derives. Five authors are ineligible (2
    contributions) so the D-IN-3 gate really moves the cohort fields; only
    the cost bookkeeping may differ between a cold and a warm run."""
    logins = [f"d{i:03d}" for i in range(20)]
    events = {login: (2 if i < 5 else 50) for i, login in enumerate(logins)}
    prs, users = _decision_authors(logins, events=events, closed_after=5)
    searches = {login: {"nodes": []} for login in logins}

    def run(raw, cache):
        fake = W4Transport([_w4_page(prs, False)], [3], [4990], searches, users)
        gql.collect_author_history(raw, "acme", "widget", now=NOW, transport=fake,
                                   user_cache=cache)
        return fake, gql.derive_cohort_fields(
            read_envelope(raw / "author_history" / f"{KEY}.json").data)

    cache = UserCache(tmp_path / "users")
    _, cold = run(tmp_path / "cold", None)
    run(tmp_path / "seed", cache)
    warm_fake, warm = run(tmp_path / "warm", cache)
    assert warm_fake.n_eligibility_batch_calls == 0
    bookkeeping = {"author_history_cost_points", "author_history_rate_limit_remaining"}
    assert {k: v for k, v in warm.items() if k not in bookkeeping} == \
        {k: v for k, v in cold.items() if k not in bookkeeping}
    assert cold["dr90_decision_pr_count_raw"] != cold["dr90_decision_pr_count_author_history_cohort"]


# --- GhCliTransport parses the body on rc != 0 --------------------------------

def test_ghclitransport_returns_parsed_body_on_nonzero_rc(monkeypatch):
    """gh api graphql exits non-zero on ANY response with an errors
    array, including a partially-successful one — the body must still be
    parsed and returned, not discarded."""
    import subprocess as _subprocess

    class FakeCompleted:
        returncode = 1
        stdout = '{"data": {"rateLimit": {"cost": 1, "remaining": 100}, "u0": {"createdAt": "2020-01-01T00:00:00Z"}}, "errors": [{"path": ["u1"], "message": "Resource limits for this query exceeded."}]}'
        stderr = "gh: Resource limits for this query exceeded."

    monkeypatch.setattr(_subprocess, "run", lambda *a, **k: FakeCompleted())
    t = gql.GhCliTransport()
    doc = t.execute("query {...}", {"l0": "a", "l1": "b"})
    assert doc["data"]["u0"]["createdAt"] == "2020-01-01T00:00:00Z"
    assert doc["errors"][0]["path"] == ["u1"]


def test_ghclitransport_raises_on_unparseable_body(monkeypatch):
    """A body that is not JSON at all (a true transport/network failure,
    not merely an errors-carrying response) still raises."""
    import subprocess as _subprocess

    class FakeCompleted:
        returncode = 1
        stdout = ""
        stderr = "curl: (7) Failed to connect"

    monkeypatch.setattr(_subprocess, "run", lambda *a, **k: FakeCompleted())
    t = gql.GhCliTransport()
    with pytest.raises(RuntimeError, match="Failed to connect"):
        t.execute("query {...}", {})


# --- Partial-batch salvage, narrower retry, adaptive width -------------------

def test_eligibility_batch_query_alias_variable_correspondence():
    """Direct check on the query text itself: each u{i} is paired with $l{i},
    exactly n of them."""
    q = gql._eligibility_batch_query(3)
    for i in range(3):
        assert f"u{i}: user(login: $l{i})" in q
    assert q.count("user(login:") == 3


def test_fetch_eligibility_batch_partial_batch_keeps_resolved_retries_only_errored():
    """A 15-alias batch where 11 resolve and 4 error must keep the 11 from the
    FIRST call — not discarded with the rest — and retry only the 4, narrower
    each time; 2 of the 4 turn out to be over-budget only in the big batch
    (they resolve once retried alongside fewer others), the other 2 are
    genuinely bad logins that never resolve."""
    logins = [f"q{i:02d}" for i in range(15)]
    good = {"createdAt": "2015-01-01T00:00:00Z",
           "contributionsCollection": {"contributionCalendar": {"totalContributions": 10}}}
    users = {login: good for login in logins}
    users["q11"] = ResolvesBelowWidth(10, {"createdAt": "2018-01-01T00:00:00Z",
                                           "contributionsCollection": {"contributionCalendar": {"totalContributions": 5}}})
    users["q12"] = ResolvesBelowWidth(10, {"createdAt": "2019-01-01T00:00:00Z",
                                           "contributionsCollection": {"contributionCalendar": {"totalContributions": 6}}})
    users["q13"] = None
    users["q14"] = None
    fake = W4Transport([], [], [], {}, users)
    out, cost, remaining = gql._fetch_eligibility_batch(logins, fake)
    # fresh(15, 11 resolve) -> retry@5(4 unresolved: q11/q12 now resolve at
    # n=4<=10, q13/q14 stay null) -> retry@1(q13 alone, q14 alone).
    assert fake.eligibility_batch_sizes == [15, 4, 1, 1]
    for login in logins:
        if login in ("q13", "q14"):
            continue
        assert out[login]["eligibility_unmeasured"] is False, login
    assert out["q11"]["account_created_at"] == "2018-01-01T00:00:00Z"
    assert out["q12"]["account_created_at"] == "2019-01-01T00:00:00Z"
    assert out["q13"] == {"account_created_at": None, "public_event_count": None,
                          "eligibility_unmeasured": True}
    assert out["q14"] == {"account_created_at": None, "public_event_count": None,
                          "eligibility_unmeasured": True}
    assert len(out) == 15


def test_fetch_eligibility_batch_deleted_account_confirmed_at_width_1():
    """A genuinely deleted/renamed account survives every retry width and
    is recorded eligibility_unmeasured only after the width-1 query pins
    the outcome to that one login; the other 14 in the same original batch
    resolve, never discarded."""
    logins = [f"p{i:02d}" for i in range(15)]
    good = {"createdAt": "2015-01-01T00:00:00Z",
           "contributionsCollection": {"contributionCalendar": {"totalContributions": 10}}}
    users = {login: good for login in logins}
    users["p07"] = None
    fake = W4Transport([], [], [], {}, users)
    out, cost, remaining = gql._fetch_eligibility_batch(logins, fake)
    assert fake.eligibility_batch_sizes == [15, 1, 1]
    assert out["p07"] == {"account_created_at": None, "public_event_count": None,
                          "eligibility_unmeasured": True}
    assert all(not out[login]["eligibility_unmeasured"] for login in logins if login != "p07")
    assert cost == 3  # 1 pt scripted (default) per call, 3 calls


def test_fetch_eligibility_batch_whole_call_transport_failure_retries_narrower():
    """A whole-call transport failure (execute() raises — the body was not
    even parseable) gets the SAME narrower-retry treatment as a partial
    batch, not an immediate unmeasured verdict: it can still recover."""
    logins = [f"t{i:02d}" for i in range(5)]
    good = {"createdAt": "2015-01-01T00:00:00Z",
           "contributionsCollection": {"contributionCalendar": {"totalContributions": 10}}}
    users = {login: good for login in logins}
    fake = W4Transport([], [], [], {}, users, eligibility_raise_on_call={0})
    out, cost, remaining = gql._fetch_eligibility_batch(logins, fake, batch_size=5)
    # call 0 (the fresh 5-wide batch) raises -> all 5 unresolved -> retried
    # at width 5 (call 1, succeeds) -> nothing left for width 1.
    assert fake.eligibility_batch_sizes == [5, 5]
    assert all(not out[login]["eligibility_unmeasured"] for login in logins)
    assert cost == 1  # call 0 raised (0 pts); call 1 succeeded (1 pt, default)


def test_fetch_eligibility_batch_resource_limited_halves_width_for_rest_of_run():
    """A 'Resource limits ... exceeded' error permanently halves
    the batch width for the rest of the call's fresh chunks.
    g00's contribution calendar is only over-budget inside the wide
    first batch (n=4); queried alongside fewer others (n<=3) it resolves
    fine."""
    logins = [f"g{i:02d}" for i in range(8)]
    good = {"createdAt": "2015-01-01T00:00:00Z",
           "contributionsCollection": {"contributionCalendar": {"totalContributions": 10}}}
    users = {login: good for login in logins}
    users["g00"] = ResolvesBelowWidth(3, {"createdAt": "2021-01-01T00:00:00Z",
                                          "contributionsCollection": {"contributionCalendar": {"totalContributions": 1}}})
    fake = W4Transport([], [], [], {}, users)
    out, cost, remaining = gql._fetch_eligibility_batch(logins, fake, batch_size=4)
    # chunk1 (g00..g03, width 4, n=4>3): g00 resource-limited -> width
    # halves 4 -> 2 for the rest of the run; g00's own retry (n=1<=3)
    # resolves immediately; chunk2/chunk3 use the halved width (2).
    assert fake.eligibility_batch_sizes == [4, 1, 2, 2]
    assert out["g00"]["eligibility_unmeasured"] is False
    assert out["g00"]["account_created_at"] == "2021-01-01T00:00:00Z"
    assert all(not out[login]["eligibility_unmeasured"] for login in logins)


def test_fetch_eligibility_batch_width_16_splits_15_plus_1():
    """Fresh mutant (F2): ELIGIBILITY_BATCH_SIZE 15 vs 16 must be tellable
    apart — 16 logins at width 15 split into batches of 15 then 1, not one
    16-wide call (which is exactly what a 15->16 mutation would produce)."""
    logins = [f"w{i:02d}" for i in range(16)]
    good = {"createdAt": "2015-01-01T00:00:00Z",
           "contributionsCollection": {"contributionCalendar": {"totalContributions": 10}}}
    users = {login: good for login in logins}
    fake = W4Transport([], [], [], {}, users)
    out, cost, remaining = gql._fetch_eligibility_batch(logins, fake, batch_size=gql.ELIGIBILITY_BATCH_SIZE)
    assert fake.eligibility_batch_sizes == [15, 1]
    assert len(out) == 16


def test_fetch_eligibility_batch_cost_accumulates_across_batches():
    """Fresh mutant (F3): cost must SUM across batches, not be overwritten
    by the last one — two batches with distinct, nonzero costs."""
    logins = [f"c{i:02d}" for i in range(20)]
    good = {"createdAt": "2015-01-01T00:00:00Z",
           "contributionsCollection": {"contributionCalendar": {"totalContributions": 10}}}
    users = {login: good for login in logins}
    fake = W4Transport([], [], [], {}, users,
                       eligibility_batch_meta=[{"cost": 3, "remaining": 500},
                                              {"cost": 5, "remaining": 480}])
    out, cost, remaining = gql._fetch_eligibility_batch(logins, fake, batch_size=15)
    assert fake.eligibility_batch_sizes == [15, 5]
    assert cost == 8  # 3 + 5, not 5 (an overwrite would read 5)
    assert remaining == 480


def test_fetch_eligibility_batch_width_15_resolves_every_alias():
    """At ELIGIBILITY_BATCH_SIZE width every alias resolves when the
    accounts in the batch stay within GitHub's resource budget."""
    logins = [f"e{i:02d}" for i in range(gql.ELIGIBILITY_BATCH_SIZE)]
    users = {login: {"createdAt": "2015-01-01T00:00:00Z",
                     "contributionsCollection": {"contributionCalendar": {"totalContributions": 10}}}
            for login in logins}
    fake = W4Transport([], [], [], {}, users)
    out, cost, remaining = gql._fetch_eligibility_batch(logins, fake, gql.ELIGIBILITY_BATCH_SIZE)
    assert fake.eligibility_batch_sizes == [15]
    assert all(not out[login]["eligibility_unmeasured"] for login in logins)
    assert all(out[login]["account_created_at"] == "2015-01-01T00:00:00Z" for login in logins)
    assert cost == 1
    assert remaining == 999


def test_fetch_eligibility_batch_unresolved_alias_marked_unmeasured_not_ineligible():
    """A batch that returns fewer than requested (a deleted account, or a
    resource-budget error) marks only the unresolved author
    eligibility_unmeasured — excluded from D-IN-3 eligibility, never
    silently defaulted to a confirmed-ineligible record indistinguishable
    from real data. (is_eligible_account is False in BOTH cases — the
    property this test actually pins is that ONLY "gone" is flagged, not
    that the two states differ in the rate math the flag feeds; that is
    _eligible's job, tested separately below.)"""
    logins = ["ok1", "gone", "ok2"]
    users = {"ok1": {"createdAt": "2015-01-01T00:00:00Z",
                    "contributionsCollection": {"contributionCalendar": {"totalContributions": 10}}},
            "gone": None,
            "ok2": {"createdAt": "2016-01-01T00:00:00Z",
                   "contributionsCollection": {"contributionCalendar": {"totalContributions": 20}}}}
    fake = W4Transport([], [], [], {}, users)
    out, cost, remaining = gql._fetch_eligibility_batch(logins, fake)
    assert out["gone"] == {"account_created_at": None, "public_event_count": None,
                          "eligibility_unmeasured": True}
    assert out["ok1"]["eligibility_unmeasured"] is False
    assert out["ok2"]["eligibility_unmeasured"] is False


def test_fetch_eligibility_batch_duplicate_login_costs_one_query_one_count():
    """Fresh mutant (F4): a login appearing twice in the input must still
    cost one alias and yield one record — the defensive de-dup this
    function owns, independent of ``needed`` already being a set at the
    call site."""
    users = {"a": {"createdAt": "2015-01-01T00:00:00Z",
                  "contributionsCollection": {"contributionCalendar": {"totalContributions": 10}}},
            "b": {"createdAt": "2016-01-01T00:00:00Z",
                 "contributionsCollection": {"contributionCalendar": {"totalContributions": 20}}}}
    fake = W4Transport([], [], [], {}, users)
    out, cost, remaining = gql._fetch_eligibility_batch(["a", "a", "b"], fake)
    assert fake.eligibility_batch_sizes == [2]  # one query, 2 distinct aliases
    assert set(out) == {"a", "b"}
    assert cost == 1


def test_seeded_sample_duplicate_candidate_does_not_shrink_the_sample():
    """Fresh mutant (F4 at the newcomer-cohort/search-cap boundary):
    ``_seeded_sample`` de-dups its input, so a duplicated candidate does
    not silently return fewer than ``size`` distinct logins when enough
    exist."""
    candidates = [f"s{i:02d}" for i in range(10)] + ["s00", "s00", "s01"]  # 3 dupes
    sample = gql._seeded_sample(candidates, 5)
    assert len(sample) == 5
    assert len(set(sample)) == 5



# --- derive_cohort_fields: hand-computed oracles ------------------------------

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
    assert fields["dr90_decision_rate_author_history_cohort"] == (0.6667, "derived")
    assert fields["dr90_decision_pr_count_author_history_cohort"] == (6, "derived")
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
    assert fields["dr90_decision_rate_author_history_cohort"] == (1.0, "derived")
    assert fields["dr90_decision_pr_count_author_history_cohort"] == (6, "derived")


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


def test_apply_author_cap_small_population_binds_when_one_author_dominates():
    """A population entirely owned by one login is still capped down to its
    allowance (``max(1, floor(0.20*4)) == 1``, the oldest survives) even though
    the population is small -- ``porchlamp.core.hygiene.apply_author_cap`` no
    longer zeroes a small population outright (see
    ``tests/core/test_hygiene.py`` for the full oracle, including the
    "nobody dominates" case that must NOT be zeroed)."""
    items = [("a", NOW - timedelta(days=d)) for d in (1, 2, 3, 4)]
    kept = gql.apply_author_cap(items)
    assert len(kept) == 1
    assert items[kept[0]][1] == NOW - timedelta(days=4)  # the oldest
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


def test_eligible_tri_state_measured_eligible_and_ineligible():
    """With real data, _eligible still returns a plain True/False —
    the tri-state only adds the third (unknown) outcome, it does not
    change the first two."""
    pr = w4_days(1, 100, "x")
    eligible_rec = {"x": w4_rec("x")}
    ineligible_rec = {"x": w4_rec("x", account="2026-08-01T00:00:00Z")}  # too young
    assert gql._eligible(pr, eligible_rec) is True
    assert gql._eligible(pr, ineligible_rec) is False


def test_eligible_tri_state_unknown_on_eligibility_unmeasured():
    """A refused eligibility query — real, complete account data never
    arrived — is unknown, not the same as a confirmed event-free account."""
    pr = w4_days(1, 100, "x")
    refused = {"x": {"account_created_at": None, "public_event_count": None,
                     "prs": [], "eligibility_unmeasured": True}}
    event_free = {"x": {"account_created_at": "2015-01-01T00:00:00Z",
                        "public_event_count": 0, "prs": [], "eligibility_unmeasured": False}}
    assert gql._eligible(pr, refused) is None
    assert gql._eligible(pr, event_free) is False  # a real, measured ineligible — not unknown


def test_eligible_tri_state_unknown_on_history_unmeasured():
    """A failed prior-merge search also makes eligibility unknown for
    that author."""
    pr = w4_days(1, 100, "x")
    rec = {"x": {"account_created_at": "2015-01-01T00:00:00Z", "public_event_count": 50,
                "prs": [], "history_unmeasured": True, "eligibility_unmeasured": False}}
    assert gql._eligible(pr, rec) is None


def test_author_status_distinguishes_refused_query_from_event_free_account():
    """A refused eligibility query and a genuinely event-free account
    both fail ``is_eligible_account``, but they are different facts and
    must sort into different buckets."""
    refused = {"account_created_at": None, "public_event_count": None,
              "eligibility_unmeasured": True}
    event_free = {"account_created_at": "2015-01-01T00:00:00Z", "public_event_count": 0,
                 "eligibility_unmeasured": False}
    assert gql._author_status(refused, NOW) == "unmeasured"
    assert gql._author_status(event_free, NOW) == "ineligible"


def _bundle_with_unmeasured_fraction(n_total, n_unmeasured, cohort_prs, cohort_records):
    """An accounting fixture: the real decision-cohort PRs/records
    under test, padded with filler author_records (not tied to any PR)
    purely to size ``authors_total``/``authors_excluded_unmeasured`` —
    inert for the rate math itself, since no padded login appears in
    ``cohort_prs``."""
    records = list(cohort_records)
    padded_unmeasured = 0
    i = 0
    while len(records) < n_total:
        if padded_unmeasured < n_unmeasured:
            records.append({"login": f"filler_unmeasured_{i}", "account_created_at": None,
                            "public_event_count": None, "prs": [], "eligibility_unmeasured": True})
            padded_unmeasured += 1
        else:
            records.append(w4_rec(f"filler_eligible_{i}"))
        i += 1
    return _w4_ah_data(cohort_prs, records)


def test_derive_cohort_fields_unmeasured_threshold_exactly_20_percent_not_gated():
    """The gate is `> 0.20`, not `>= 0.20`: exactly 20% unmeasured (2 of
    10) must NOT collapse DR_90/MR_first to unmeasured. Six single-PR
    authors (n=6 >= 5) so the SEPARATE per-author cap (0 below n=5,
    unrelated to this gate) does not itself zero the population."""
    prs = [w4_days(i, 150, f"a{i}", closed_after=5) for i in range(6)]
    records = [w4_rec(f"a{i}") for i in range(6)]
    data = _bundle_with_unmeasured_fraction(10, 2, prs, records)
    fields = gql.derive_cohort_fields(data)
    assert fields["authors_total"] == (10, "verified")
    assert fields["authors_excluded_unmeasured"] == (2, "verified")
    assert fields["dr90_decision_rate_author_history_cohort"] == (1.0, "derived")
    assert fields["dr90_decision_pr_count_author_history_cohort"] == (6, "derived")
    assert fields["mr_first_rate"][1] == "derived"


def test_derive_cohort_fields_unmeasured_threshold_just_above_20_percent_gated():
    """21 of 100 (21%), just over the 20% line, DOES collapse DR_90 and
    MR_first (both raw... no, both CAPPED) to unmeasured, with a
    reconstructible detail string."""
    prs = [w4_days(1, 150, "a1", closed_after=5), w4_days(2, 150, "a2", closed_after=5)]
    records = [w4_rec("a1"), w4_rec("a2")]
    data = _bundle_with_unmeasured_fraction(100, 21, prs, records)
    fields = gql.derive_cohort_fields(data)
    assert fields["authors_total"] == (100, "verified")
    assert fields["authors_excluded_unmeasured"] == (21, "verified")
    assert fields["dr90_decision_rate_author_history_cohort"] == (None, "missing")
    assert fields["dr90_decision_pr_count_author_history_cohort"] == (None, "missing")
    assert fields["mr_first_rate"] == (None, "missing")
    assert fields["mr_first_pr_count"] == (None, "missing")
    assert fields["cohort_unmeasured_detail"] == ("21 of 100 authors unmeasured", "verified")
    # the RAW rates are unaffected by the gate — they never depended on
    # per-author eligibility in the first place.
    assert fields["dr90_decision_rate_raw"][0] is not None
    assert fields["dr90_decision_rate_raw"][1] == "derived"


def test_derive_cohort_fields_large_unmeasured_loss_yields_unmeasured_not_a_rate():
    """A repo whose eligibility batch lost 45 of 62 cohort authors must report
    DR_90/MR_first unmeasured, not silently compute a rate on whichever
    authors are left."""
    prs = [w4_days(100 + i, 150, f"a{i}", merged_after=5) for i in range(6)] + \
         [w4_days(200 + i, 150, f"a{i + 6}", closed_after=5) for i in range(3)]
    records = [w4_rec(f"a{i}") for i in range(9)]
    data = _bundle_with_unmeasured_fraction(62, 45, prs, records)
    fields = gql.derive_cohort_fields(data)
    assert fields["authors_total"] == (62, "verified")
    assert fields["authors_excluded_unmeasured"] == (45, "verified")
    assert fields["mr_first_rate"] == (None, "missing")
    assert fields["mr_first_pr_count"] == (None, "missing")
    assert fields["dr90_decision_rate_author_history_cohort"] == (None, "missing")


def test_author_status_unmeasured_takes_priority_over_unsampled():
    """An author outside the search-cap sample AND with a failed fetch is
    'unmeasured', not 'unsampled'."""
    both_history = {"history_unsampled": True, "history_unmeasured": True,
                    "account_created_at": None, "public_event_count": None}
    assert gql._author_status(both_history, NOW) == "unmeasured"
    both_eligibility = {"history_unsampled": True, "eligibility_unmeasured": True,
                        "account_created_at": None, "public_event_count": None}
    assert gql._author_status(both_eligibility, NOW) == "unmeasured"


def test_derive_cohort_fields_unmeasured_and_unsampled_overlap_still_gates():
    """10 of 20 authors have BOTH a failed eligibility fetch AND fall outside
    the search-cap sample; authors_excluded_unmeasured must count all 10,
    and the gate must fire."""
    prs = [w4_days(i, 150, f"e{i}", closed_after=5) for i in range(10)] + \
         [w4_days(100 + i, 150, f"u{i}") for i in range(10)]  # open (not decided)
    records = [w4_rec(f"e{i}") for i in range(10)] + \
             [{"login": f"u{i}", "account_created_at": None, "public_event_count": None,
               "prs": [], "eligibility_unmeasured": True, "history_unsampled": True}
              for i in range(10)]
    fields = gql.derive_cohort_fields(_w4_ah_data(prs, records))
    assert fields["authors_total"] == (20, "verified")
    assert fields["authors_excluded_unmeasured"] == (10, "verified")
    assert fields["authors_excluded_unsampled"] == (0, "verified")
    assert fields["dr90_decision_rate_raw"] == (0.5, "derived")
    assert fields["dr90_decision_rate_author_history_cohort"] == (None, "missing")
    assert fields["dr90_decision_pr_count_author_history_cohort"] == (None, "missing")


def test_derive_cohort_fields_unsampled_only_author_counted_separately():
    """An author who is ONLY outside the search-cap sample (no failed fetch)
    is counted in authors_excluded_unsampled, not folded into
    authors_excluded_unmeasured or silently dropped."""
    prs = [w4_days(i, 150, f"e{i}", closed_after=5) for i in range(8)] + \
         [w4_days(100, 150, "s0")]
    records = [w4_rec(f"e{i}") for i in range(8)] + \
             [{"login": "s0", "account_created_at": None, "public_event_count": None,
               "prs": [], "history_unsampled": True}]
    fields = gql.derive_cohort_fields(_w4_ah_data(prs, records))
    assert fields["authors_total"] == (9, "verified")
    assert fields["authors_excluded_unsampled"] == (1, "verified")
    assert fields["authors_excluded_unmeasured"] == (0, "verified")


def test_eligible_call_site_excludes_unmeasured_below_gate_threshold():
    """``_eligible(...) is True`` must exclude a None (unmeasured) author even
    when the population is well under the 20% gate threshold."""
    prs = [w4_days(i, 150, f"e{i}", closed_after=5) for i in range(17)] + \
         [w4_days(100 + i, 150, f"u{i}") for i in range(3)]  # open (not decided)
    records = [w4_rec(f"e{i}") for i in range(17)] + \
             [{"login": f"u{i}", "account_created_at": None, "public_event_count": None,
               "prs": [], "eligibility_unmeasured": True} for i in range(3)]
    fields = gql.derive_cohort_fields(_w4_ah_data(prs, records))
    assert fields["authors_excluded_unmeasured"] == (3, "verified")  # 3/20 = 0.15, under the gate
    assert fields["dr90_decision_rate_author_history_cohort"] == (1.0, "derived")
    assert fields["dr90_decision_pr_count_author_history_cohort"] == (17, "derived")


def test_derive_cohort_fields_newcomer_conversion_gated_not_zero():
    """Newcomer conversion rows get the same tri-state gate over the newcomer
    roster's own fraction. 9 of 20 newcomer authors have a failed eligibility
    fetch — and those 9 are exactly the ones whose first PR really did
    convert (merged within 180 d)."""
    prs: list = []
    records: list = []
    for i in range(9):
        login = f"conv{i}"
        prs.append(w4_days(700 + i, 200, login, merged_after=20))  # converts within 180d
        records.append({"login": login, "account_created_at": None, "public_event_count": None,
                        "prs": [], "eligibility_unmeasured": True})
    for i in range(11):
        login = f"stay{i}"
        prs.append(w4_days(800 + i, 200, login))  # never merged: does not convert
        records.append(w4_rec(login))
    sampled = [f"conv{i}" for i in range(9)] + [f"stay{i}" for i in range(11)]
    fields = gql.derive_cohort_fields(_w4_ah_data(prs, records, sampled=sampled))
    assert fields["newcomer_cohort_author_count"] == (20, "derived")
    assert fields["newcomer_conversion_raw"] == (0.45, "derived")
    assert fields["newcomer_conversion_raw_author_count"] == (20, "derived")
    assert fields["newcomer_conversion"] == (None, "missing")
    assert fields["newcomer_conversion_author_count"] == (None, "missing")
    assert fields["newcomer_unmeasured_detail"] == ("9 of 20 newcomer authors unmeasured", "verified")


def test_derive_cohort_fields_newcomer_conversion_not_gated_below_threshold():
    """Over-correction guard: when the newcomer roster's unmeasured
    fraction is at/under 0.20, newcomer metrics compute normally (not gated),
    and an unmeasured author is simply excluded from the eligible pool."""
    prs: list = []
    records: list = []
    for i in range(2):
        login = f"conv{i}"
        prs.append(w4_days(700 + i, 200, login, merged_after=20))
        records.append({"login": login, "account_created_at": None, "public_event_count": None,
                        "prs": [], "eligibility_unmeasured": True})
    for i in range(8):
        login = f"stay{i}"
        prs.append(w4_days(800 + i, 200, login, merged_after=20))  # also converts
        records.append(w4_rec(login))
    sampled = [f"conv{i}" for i in range(2)] + [f"stay{i}" for i in range(8)]
    fields = gql.derive_cohort_fields(_w4_ah_data(prs, records, sampled=sampled))
    assert fields["newcomer_cohort_author_count"] == (10, "derived")
    assert fields["newcomer_conversion_raw"] == (1.0, "derived")  # all 10 convert
    assert fields["newcomer_conversion"][1] == "derived"          # not gated: 2/10 = 0.20
    assert fields["newcomer_conversion"] == (1.0, "derived")      # the 8 measured all convert too
    assert fields["newcomer_conversion_author_count"] == (8, "derived")  # 2 unmeasured excluded


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
    assert meta["dr90_decision_rate_author_history_cohort"] == 0.6667
    assert meta["dr90_decision_pr_count_author_history_cohort"] == 6
    assert meta["mr_first_rate"] == 0.5
    assert meta["mr_first_pr_count"] == 4
    assert meta["cohort_decision_pr_numbers"] == [200, 201, 202, 203, 204, 205, 206]
    assert meta["newcomer_cohort_author_count"] == 0
    assert meta["author_history_cost_points"] == 77
    assert bundle["confidence"]["dr90_decision_rate_author_history_cohort"] == "derived"
    assert bundle["confidence"]["author_history_cost_points"] == "verified"
    ah_path = "raw/author_history/acme__widget.json"
    assert bundle["provenance"]["dr90_decision_rate_author_history_cohort"]["source"] == ah_path
    assert bundle["provenance"]["dr90_decision_rate_author_history_cohort"]["fetched_at"] == NOW.isoformat()


def test_derive_repo_without_author_history_envelope_no_cohort_fields(tmp_path):
    """Over-correction guard: the pr_window path is unchanged when the
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


def test_partial_eligibility_alias_missing_contributions_collection_is_unmeasured(tmp_path):
    """If an eligibility alias returns createdAt but contributionsCollection is
    null/missing due to a partial GraphQL error, it must remain unmeasured and
    never be cached or treated as a 0-event account."""
    cache = UserCache(tmp_path / "users")
    prs, users = _decision_authors(["d000"])
    users["d000"] = {"createdAt": "2020-01-01T00:00:00Z", "contributionsCollection": None}
    fake = W4Transport([_w4_page(prs, False)], [3], [4990], {"d000": {"nodes": []}}, users)
    gql.collect_author_history(tmp_path / "raw", "acme", "widget", now=NOW,
                               transport=fake, user_cache=cache)
    rec = _records(tmp_path / "raw", "acme__widget")["d000"]
    assert rec["eligibility_unmeasured"] is True
    assert rec["public_event_count"] is None
    assert cache.get("d000", NOW) is None


