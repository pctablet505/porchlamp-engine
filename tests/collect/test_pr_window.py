"""Tests for windowed PR collection with typed actors and review threads.

Subject: ``porchlamp.collect.github_graphql``:
recorded-response oracles at the GraphQL transport boundary (no live calls)
asserting the exact values, pagination stop at the window
boundary, ``truncated`` at the PR cap, point-cost accounting, and the
append-only hooks at the end of ``scripts/gather.py`` (the ``pr_window``
status entry) and ``scripts/derive.py`` (the ``pr_window_*`` bundle fields).
"""
from __future__ import annotations

import hashlib
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
from porchlamp.core import gates
from porchlamp.derive.gate_evidence import pr_rule_inputs_recorded

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


_NO_KEY = object()


def pr_node(number, created, closed=None, merged=None, assoc="NONE",
            author_login="alice", author_typ="User",
            reviews=(), comments=(), threads=0, merged_by=_NO_KEY) -> dict:
    """``merged_by`` left at its default omits the ``mergedBy`` key, the
    shape of an envelope recorded before the query asked for it; a login
    string records ``{"login": ...}``; ``None`` records a null."""
    node = {
        "number": number, "createdAt": created, "closedAt": closed,
        "mergedAt": merged,
        "state": "MERGED" if merged else ("CLOSED" if closed else "OPEN"),
        "author": author(author_login, author_typ),
        "authorAssociation": assoc,
        "reviews": {"nodes": list(reviews)},
        "comments": {"nodes": list(comments)},
        "reviewThreads": {"totalCount": threads},
    }
    if merged_by is not _NO_KEY:
        node["mergedBy"] = None if merged_by is None else {"login": merged_by}
    return node


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


# --- D-IN-2 maintainers by merge (owner ruling 2026-10-06) -------------------

def test_query_requests_merged_by_login():
    """The pr_window query records who merged each PR: the mergers set."""
    assert "mergedBy { login }" in gql.PR_WINDOW_QUERY


def test_both_window_queries_record_labels_and_what_closed_each_pr():
    """#60: landing detection reads a PR's labels (a project's own "Merged" label) and what closed it (a commit, or
    another PR), so the heavy query and its light continuation both ask for them; the closer sits inside the close
    event, not on the PR. Each query's braces balance."""
    for query in (gql.PR_WINDOW_QUERY, gql.PR_WINDOW_LIGHT_QUERY):
        assert " labels(first: 10) { nodes { name } }" in query
        closer = " closer { __typename ... on Commit { oid } ... on PullRequest { number } }"
        assert query.count(closer) == 1
        assert "... on ClosedEvent { createdAt actor { __typename login } closer {" in query
        assert query.count("{") == query.count("}")


def _mergers_window(merged_by=_NO_KEY) -> list[dict]:
    """PR 1: erin's (CONTRIBUTOR) PR, merged by carol when ``merged_by`` is
    recorded. PRs 2-4: alice's external PRs, each with one 40-character
    comment, by carol (CONTRIBUTOR association, a merger), by dave
    (CONTRIBUTOR, merged nothing) and by erin (CONTRIBUTOR, authored a merged
    PR but merged nothing). PR 5: carol's review on alice's PR."""
    created = "2026-08-01T00:00:00Z"
    return [
        pr_node(1, "2026-07-01T00:00:00Z", closed="2026-07-02T00:00:00Z",
                merged="2026-07-02T00:00:00Z", assoc="CONTRIBUTOR",
                author_login="erin", merged_by=merged_by),
        pr_node(2, created, comments=[comment("carol", assoc="CONTRIBUTOR", body="c" * 40,
                                              created="2026-08-01T01:00:00Z")]),
        pr_node(3, created, comments=[comment("dave", assoc="CONTRIBUTOR", body="d" * 40,
                                              created="2026-08-01T01:00:00Z")]),
        pr_node(4, created, comments=[comment("erin", assoc="CONTRIBUTOR", body="e" * 40,
                                              created="2026-08-01T01:00:00Z")]),
        pr_node(5, created, reviews=[review("carol", assoc="CONTRIBUTOR",
                                            created="2026-08-01T02:00:00Z")]),
    ]


def test_merger_comment_counts_and_non_merger_does_not():
    """A comment of 40+ characters by a CONTRIBUTOR-association account that
    merged a PR in the window is a D-IN-2 response (maintainers whose org
    membership is private); one by a CONTRIBUTOR who merged nothing is not,
    and neither is one by the author of a merged PR who did not merge it."""
    out = gql.derive_pr_window(_mergers_window(merged_by="carol"))
    assert out[1]["first_substantive_response_at"] == "2026-08-01T01:00:00Z"
    assert out[1]["first_substantive_response_kind"] == "comment"
    assert out[1]["ttfhr_hours"] == 1.0
    assert out[2]["first_substantive_response_at"] is None
    assert out[3]["first_substantive_response_at"] is None
    assert out[4]["first_substantive_response_at"] == "2026-08-01T02:00:00Z"
    assert out[4]["first_substantive_response_kind"] == "review"


def test_merger_rule_keeps_the_other_d_in_2_exclusions():
    """Over-correction guard: a merger's comment on their own PR, a merger's
    39-character comment, and a bot merger's comment are still not
    responses."""
    prs = [
        pr_node(1, "2026-07-01T00:00:00Z", merged="2026-07-02T00:00:00Z",
                closed="2026-07-02T00:00:00Z", merged_by="carol"),
        pr_node(2, "2026-07-03T00:00:00Z", merged="2026-07-04T00:00:00Z",
                closed="2026-07-04T00:00:00Z", merged_by="mergify[bot]"),
        pr_node(3, "2026-08-01T00:00:00Z", assoc="CONTRIBUTOR", author_login="carol",
                comments=[comment("carol", assoc="CONTRIBUTOR", body="c" * 60,
                                  created="2026-08-01T01:00:00Z")]),
        pr_node(4, "2026-08-01T00:00:00Z",
                comments=[comment("carol", assoc="CONTRIBUTOR", body="c" * 39,
                                  created="2026-08-01T01:00:00Z")]),
        pr_node(5, "2026-08-01T00:00:00Z",
                comments=[comment("mergify[bot]", typ="Bot", assoc="CONTRIBUTOR", body="m" * 60,
                                  created="2026-08-01T01:00:00Z")]),
    ]
    out = gql.derive_pr_window(prs)
    assert [d["first_substantive_response_at"] for d in out[2:]] == [None, None, None]


def test_envelope_without_merged_by_behaves_as_before():
    """Envelopes recorded before the query asked for ``mergedBy``: the
    mergers set is empty and only MEMBER/OWNER/COLLABORATOR respond. The
    output is the same with the key absent and with it null, and equals the
    association-only result written out by hand."""
    absent = gql.derive_pr_window(_mergers_window())
    null = gql.derive_pr_window(_mergers_window(merged_by=None))
    assert absent == null
    assert [d["first_substantive_response_at"] for d in absent] == [None] * 5
    assert [d["ttfhr_hours"] for d in absent] == [None] * 5
    assert [d["is_external"] for d in absent] == [True] * 5


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


def test_substantive_review_counts_maintainer_issue_comments_and_cjk_weight():
    """RFC-0018: D-IN-2 maintainer issue comments of >= 80 effective chars
    (including CJK weight 3) count as substantive review, while non-maintainer,
    bot, self, or < 80-effective-char comments do not."""
    created = "2026-08-01T00:00:00Z"
    prs = [
        # 1. MEMBER issue comment with 80 chars -> True
        pr_node(101, created, comments=[comment("maint1", assoc="MEMBER", body="m" * 80)]),
        # 2.MEMBER issue comment with 79 chars -> False
        pr_node(102, created, comments=[comment("maint2", assoc="MEMBER", body="n" * 79)]),
        # 3. Merger (in maintainers via mergedBy on PR 101) with CONTRIBUTOR association and 85 chars -> True
        {**pr_node(103, created, comments=[comment("merger_bob", assoc="CONTRIBUTOR", body="b" * 85)]),
         "mergedBy": {"login": "merger_bob"}},
        # 4. External bystander (NONE, not in maintainers) with 120 chars -> False
        pr_node(104, created, comments=[comment("stranger", assoc="NONE", body="s" * 120)]),
        # 5. Author self-comment even with MEMBER association -> False
        pr_node(105, created, author_login="alice",
                comments=[comment("alice", assoc="MEMBER", body="a" * 120)]),
        # 6. Bot comment with MEMBER association -> False
        pr_node(106, created, comments=[comment("codecov", typ="Bot", assoc="MEMBER", body="c" * 120)]),
        # 7. CJK maintainer comment: 27 CJK chars = 81 effective chars >= 80 -> True
        pr_node(107, created, comments=[comment("maint3", assoc="OWNER", body="請" * 27)]),
        # 8. CJK maintainer comment: 26 CJK chars = 78 effective chars < 80 -> False
        pr_node(108, created, comments=[comment("maint3", assoc="OWNER", body="請" * 26)]),
        # 9. CJK review body: 27 CJK chars = 81 effective chars >= 80 -> True
        pr_node(109, created, reviews=[review("rev1", assoc="MEMBER", body="審" * 27, inline=0)]),
        # 10. CJK review body: 26 CJK chars = 78 effective chars < 80 -> False
        pr_node(110, created, reviews=[review("rev1", assoc="MEMBER", body="審" * 26, inline=0)]),
    ]
    out = gql.derive_pr_window(prs)
    assert [d["substantive_review"] for d in out] == [
        True, False, True, False, False, False, True, False, True, False
    ]


def test_substantive_review_boilerplate_guard_excludes_repeated_templates():
    """RFC-0018: normalized review bodies or maintainer comments appearing on
    >= 3 distinct PRs (ignoring URLs, @mentions, #numbers, and digits) are
    excluded as canned boilerplate, while unique feedback or inline comments
    still count."""
    created = "2026-08-01T00:00:00Z"
    canned = (
        "Thank you for your contribution to our project @user{i}! "
        "Before we can review PR #{num}, please run the test suite and check "
        "https://example.org/docs/pr-{num} — coverage is currently {pct}%."
    )
    prs = [
        # PRs 201..203 receive only the canned maintainer comment (differing in @user, #num, URL, %) -> all False
        pr_node(201, created, comments=[comment("maint", assoc="MEMBER", body=canned.format(i=1, num=201, pct=81))]),
        pr_node(202, created, comments=[comment("maint", assoc="MEMBER", body=canned.format(i=2, num=202, pct=82))]),
        pr_node(203, created, reviews=[review("maint", assoc="MEMBER", body=canned.format(i=3, num=203, pct=83), inline=0)]),
        # PR 204 receives the canned comment PLUS a unique 85-char maintainer review comment -> True
        pr_node(204, created, comments=[
            comment("maint", assoc="MEMBER", body=canned.format(i=4, num=204, pct=84)),
            comment("maint", assoc="MEMBER", body="The retry loop in transport.py drops the cursor when page_size=10; please preserve after_cursor."),
        ]),
        # PR 205 receives the canned review body WITH an inline diff comment (inline=1) -> True
        pr_node(205, created, reviews=[
            review("maint", assoc="MEMBER", body=canned.format(i=5, num=205, pct=85), inline=1),
        ]),
        # PRs 206..207 share a 2-PR comment (< 3 threshold) -> both True
        pr_node(206, created, comments=[comment("maint", assoc="MEMBER", body="Two-PR shared architectural note on the memory allocator alignment requirements in core.")]),
        pr_node(207, created, comments=[comment("maint", assoc="MEMBER", body="Two-PR shared architectural note on the memory allocator alignment requirements in core.")]),
    ]
    out = gql.derive_pr_window(prs)
    assert [d["substantive_review"] for d in out] == [
        False, False, False, True, True, True, True
    ]


def test_substantive_review_denominator_excludes_never_ready_drafts_and_early_self_closes():
    """RFC-0018: decided external PRs that are never-ready drafts or unanswered
    author self-closes within 14d have substantive_review=None and are excluded
    from the decided_external denominator."""
    created = "2026-08-01T00:00:00Z"
    closed_early = "2026-08-03T00:00:00Z"   # 2d after creation (<= 14d)
    closed_late = "2026-08-25T00:00:00Z"    # 24d after creation (> 14d)
    resp_early = "2026-08-02T00:00:00Z"     # 1d after creation
    prs = [
        # 1. Never-ready draft, closed -> None (excluded from denominator)
        {**pr_node(301, created, closed=closed_late), "isDraft": True, "timelineItems": {"nodes": []}},
        # 2. Draft that became ready before closing, unreviewed -> False (in denominator)
        {**pr_node(302, created, closed=closed_late), "isDraft": True,
         "timelineItems": {"nodes": [{"__typename": "ReadyForReviewEvent", "createdAt": "2026-08-05T00:00:00Z"}]}},
        # 3. Unanswered author self-close within 14d -> None (excluded from denominator)
        {**pr_node(303, created, author_login="alice", closed=closed_early),
         "timelineItems": {"nodes": [{"__typename": "ClosedEvent", "createdAt": closed_early,
                                      "actor": {"__typename": "User", "login": "alice"}}]}},
        # 4. Author self-close after 14d (24d) with no response -> False (stays in denominator)
        {**pr_node(304, created, author_login="alice", closed=closed_late),
         "timelineItems": {"nodes": [{"__typename": "ClosedEvent", "createdAt": closed_late,
                                      "actor": {"__typename": "User", "login": "alice"}}]}},
        # 5. Author self-close within 14d AFTER a substantive maintainer comment -> True (stays in denominator)
        {**pr_node(305, created, author_login="alice", closed=closed_early,
                   comments=[comment("maint", assoc="MEMBER", created=resp_early, body="x" * 90)]),
         "timelineItems": {"nodes": [{"__typename": "ClosedEvent", "createdAt": closed_early,
                                      "actor": {"__typename": "User", "login": "alice"}}]}},
    ]
    out = gql.derive_pr_window(prs)
    assert [d["substantive_review"] for d in out] == [None, False, None, False, True]
    fields = gql.derive_pr_window_fields({
        "window_anchor": "2026-09-01T00:00:00Z",
        "window_days": 180,
        "count": len(prs),
        "truncated": False,
        "cost_points": 5,
        "prs": prs,
        "derived": out,
    })
    # Denominator has 3 PRs (302=False, 304=False, 305=True) -> rate = 1/3 = 0.3333
    assert fields["pr_window_substantive_review_rate"] == (round(1 / 3, 4), "derived")



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
    res = gql.fetch_pr_window("acme", "widget", NOW, fake, cap=500, sample_cap=None)
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
    assert d["cap"] == 2000
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
    "commits_365d": [], "dependents": {"package": "widget", "guess_linked": True},
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


# --- closed_unread: the two counts the derive hook records from the window ---
#
# The closed_unread detector (03 section 6) reads the outside PRs of the
# window (an external author association and a non-bot author, D-IN-1) and,
# of those, the ones closed and never merged with no D-IN-2 response ever
# recorded: a maintainer review in any state or a comment of at least 40
# characters, never the PR's author, never a bot. Every expected count below
# is counted by hand from the fixture.

_OPENED, _ANSWERED, _CLOSED = "2026-08-01T00:00:00Z", "2026-08-02T00:00:00Z", "2026-08-03T00:00:00Z"


def _closed_unmerged(number, **kw) -> dict:
    return pr_node(number, _OPENED, closed=_CLOSED, **kw)


def _merged_by(number, merger) -> dict:
    """An internal PR ``merger`` merged: it puts ``merger`` in the window's mergers set."""
    return pr_node(number, _OPENED, closed=_CLOSED, merged=_CLOSED, assoc="MEMBER",
                   author_login="insider", merged_by=merger)


def _closed_unread_counts(prs) -> tuple:
    """``(closed unread, outside)`` as the derive hook records them for a window of ``prs``."""
    fields = gql.derive_pr_window_fields({"count": len(prs), "truncated": False, "cost_points": 1,
                                         "prs": prs, "derived": gql.derive_pr_window(prs)})
    assert fields["pr_window_closed_unread_count"][1] == fields["pr_window_outside_pr_count"][1] == "derived"
    return fields["pr_window_closed_unread_count"][0], fields["pr_window_outside_pr_count"][0]


def test_closed_unread_counts_a_maintainer_comment_as_read():
    """Two closed, unmerged outside PRs, neither reviewed: the one a maintainer
    answered with a 40-character comment is read; the one nobody answered is
    unread."""
    prs = [_closed_unmerged(1, comments=[comment(body="m" * 40, created=_ANSWERED)]),
           _closed_unmerged(2)]
    assert _closed_unread_counts(prs) == (1, 2)


def test_closed_unread_author_comment_is_not_a_reply():
    """``alice`` merged a PR in the window, so her comments answer like a
    maintainer's (PR 2), but her association shows CONTRIBUTOR, so her own PR
    1 is an outside PR. Her 40-character comment on it does not answer it:
    never the author."""
    alice_says = comment("alice", assoc="CONTRIBUTOR", body="a" * 40, created=_ANSWERED)
    prs = [_closed_unmerged(1, assoc="CONTRIBUTOR", author_login="alice", comments=[alice_says]),
           _closed_unmerged(2, author_login="bob", comments=[alice_says]),
           _merged_by(9, "alice")]
    assert _closed_unread_counts(prs) == (1, 2)


def test_closed_unread_bot_comment_is_not_a_reply():
    """``mergify[bot]`` and ``maint`` each merged a PR in the window, so both
    pass the maintainer test; only the bot rule keeps the bot's 40-character
    comment on PR 1 from answering it, while maint's answers PR 2."""
    prs = [_closed_unmerged(1, comments=[comment("mergify[bot]", typ="Bot", assoc="NONE", body="b" * 40,
                                                 created=_ANSWERED)]),
           _closed_unmerged(2, author_login="bob", comments=[comment("maint", assoc="CONTRIBUTOR", body="c" * 40,
                                                                     created=_ANSWERED)]),
           _merged_by(9, "mergify[bot]"), _merged_by(10, "maint")]
    assert _closed_unread_counts(prs) == (1, 2)


def test_closed_unread_population_is_outside_human_prs_and_only_closed_unmerged_unanswered_count():
    prs = [
        _closed_unmerged(1),                                                      # outside, closed unread
        _closed_unmerged(2, assoc="FIRST_TIME_CONTRIBUTOR", author_login="newbie"),  # outside, closed unread
        _closed_unmerged(3, assoc="FIRST_TIMER", author_login="newer",            # outside, a review answers
                         reviews=[review(state="CHANGES_REQUESTED", created=_ANSWERED)]),
        pr_node(4, _OPENED, closed=_CLOSED, merged=_CLOSED),                      # outside, merged unanswered
        pr_node(5, _OPENED),                                                      # outside, still open
        _closed_unmerged(6, assoc="MEMBER", author_login="insider"),              # internal: not outside
        _closed_unmerged(7, author_login="dependabot[bot]", author_typ="Bot"),    # bot author: not outside
    ]
    assert _closed_unread_counts(prs) == (2, 5)


def test_closed_unread_counts_of_an_empty_window_are_zero():
    assert _closed_unread_counts([]) == (0, 0)


def test_no_closed_unread_counts_without_recorded_prs():
    """No envelope, or one without a PR list: nothing is recorded, so the flag
    has no input."""
    assert gql.derive_pr_window_fields(None) == {}
    fields = gql.derive_pr_window_fields({"count": 0, "derived": []})
    assert "pr_window_closed_unread_count" not in fields
    assert "pr_window_outside_pr_count" not in fields


def test_derive_repo_records_the_closed_unread_counts(tmp_path):
    """``_write_pr_window_envelope``: PR 1 (outside, closed, a review and a
    comment) is read; PR 2 (outside, closed unmerged, no response) is unread;
    PR 3 is a MEMBER's; PR 4 (outside) is open."""
    _write_pr_window_envelope(tmp_path / "raw")
    bundle = derive.derive_repo("acme", "widget", derive.Ecosystem.PYPI, tmp_path,
                                fetch=False, now=NOW)
    assert (bundle["meta"]["pr_window_closed_unread_count"], bundle["meta"]["pr_window_outside_pr_count"]) == (1, 3)
    assert bundle["confidence"]["pr_window_closed_unread_count"] == "derived"
    assert bundle["confidence"]["pr_window_outside_pr_count"] == "derived"
    assert bundle["provenance"]["pr_window_closed_unread_count"]["source"] == "raw/pr_window/acme__widget.json"


# --- #57: the window statistics come from the recorded PR nodes -------------
#
# derive_pr_window_fields recomputes every per-PR fact from the stored nodes
# under the current rules; the stored ``derived`` list (the rules of the run
# that collected the envelope) is never read, and a bot's own PRs are not
# outside PRs, whoever answers them.

_PLUS_2H, _PLUS_10S = "2026-08-01T02:00:00Z", "2026-08-01T00:00:10Z"


def _window_fields(prs, derived=None) -> dict:
    record = {"count": len(prs), "truncated": False, "cost_points": 1, "prs": prs}
    if derived is not None:
        record["derived"] = derived
    return gql.derive_pr_window_fields(record)


def test_window_statistics_come_from_the_pr_nodes_not_the_stored_derived_list():
    """PR 1 got a maintainer's substantive review 2 h after opening; PR 2's only
    reply came from ``k8s-ci-robot`` (in BOT_LOGINS since RFC-0012) 10 s after
    opening. An older run stored a list that counted the bot's reply on both and
    no substantive review: read, it gives a median of 10 s over 2 and a rate of 0."""
    prs = [_closed_unmerged(1, reviews=[review(body="r" * 80, created=_PLUS_2H)]),
           _closed_unmerged(2, author_login="bob",
                            comments=[comment("k8s-ci-robot", assoc="MEMBER", body="k" * 40, created=_PLUS_10S)])]
    stale = [{**row, "ttfhr_hours": 10 / 3600, "substantive_review": False} for row in gql.derive_pr_window(prs)]
    for derived in (None, stale):
        fields = _window_fields(prs, derived)
        assert fields["pr_window_external_measured_latency_count"] == (1, "derived")
        assert fields["pr_window_ttfhr_median_hours"] == (2.0, "derived")
        assert fields["pr_window_substantive_review_rate"] == (0.5, "derived")


def test_window_statistics_leave_out_pull_requests_a_bot_opened():
    """D-IN-1 keeps a bot's own PRs out of 1.1 and 4.3, as D-GATE-5 and
    closed_unread already do: three Dependabot PRs (external association, typed
    Bot), each approved by a maintainer 10 s after opening, beside one outside PR
    reviewed after 2 h. Counted, they read a median of 10 s over 4 and 4.3 at 0.25."""
    bot_prs = [_closed_unmerged(10 + i, assoc="CONTRIBUTOR", author_login="dependabot", author_typ="Bot",
                                reviews=[review(state="APPROVED", created=_PLUS_10S)]) for i in range(3)]
    prs = [_closed_unmerged(1, reviews=[review(body="r" * 80, created=_PLUS_2H)]), *bot_prs]
    fields = _window_fields(prs)
    assert fields["pr_window_external_measured_latency_count"] == (1, "derived")
    assert fields["pr_window_ttfhr_median_hours"] == (2.0, "derived")
    assert fields["pr_window_substantive_review_rate"] == (1.0, "derived")
    assert fields["pr_window_outside_pr_count"] == (1, "derived")


def test_the_per_pr_rows_and_the_core_agree_on_who_is_outside():
    """One definition of an outside PR: derive_pr_window's ``is_external`` and
    ``is_bot_author`` agree with porchlamp.core.gates.is_outside_pr on every node,
    so the measures read from the rows and the readers of the nodes cannot part."""
    from porchlamp.core import gates

    prs = [pr_node(1, _OPENED), pr_node(2, _OPENED, assoc="MEMBER"), pr_node(3, _OPENED, assoc="FIRST_TIMER"),
           pr_node(4, _OPENED, assoc="CONTRIBUTOR", author_login="dependabot", author_typ="Bot"),
           pr_node(5, _OPENED, author_login="acme-ci[bot]"), pr_node(6, _OPENED, author_login="tekton-robot"),
           pr_node(7, _OPENED, assoc="MANNEQUIN"), {**pr_node(8, _OPENED), "author": None}]
    rows = gql.derive_pr_window(prs)
    assert [gates.is_outside_pr(pr) for pr in prs] == [r["is_external"] and not r["is_bot_author"] for r in rows]
    assert [gates.is_outside_pr(pr) for pr in prs] == [True, False, True, False, False, False, False, True]


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
    res = gql.fetch_author_window("acme", "widget", NOW, fake, cap=500)
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
    120 d old) and newcomer-band author new1 (2026-02-01) / new2
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
    assert d["cap"] == gql.AUTHOR_WINDOW_CAP
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




# --- the query that fetched an envelope -----------------------------------------

def _rule_input_fields(node: dict) -> dict:
    """``node`` as the current query records it: ``isDraft`` and the timeline."""
    return {**node, "isDraft": False, "timelineItems": {"nodes": []}}


def test_a_pr_window_envelope_records_the_query_that_fetched_it(tmp_path):
    fake = FakeTransport([page([pr_node(5, "2026-09-01T00:00:00Z")], False)], [1])
    raw = tmp_path / "raw"
    assert gql.collect_pr_window(raw, "acme", "widget", now=NOW, transport=fake) == "ok"
    data = read_envelope(raw / "pr_window" / "acme__widget.json").data
    assert data["query_sha256"] == gql.PR_WINDOW_QUERY_SHA256
    assert gql.PR_WINDOW_QUERY_SHA256 == hashlib.sha256(gql.PR_WINDOW_QUERY.encode("utf-8")).hexdigest()
    assert gql.pr_window_query_is_current(data)
    assert not gql.pr_window_query_is_current({**data, "query_sha256": "0" * 64})
    assert not gql.pr_window_query_is_current({k: v for k, v in data.items() if k != "query_sha256"})


@pytest.mark.parametrize("recorded_query", [None, "0" * 64])
def test_an_incremental_refresh_refetches_an_envelope_from_an_older_query(tmp_path, recorded_query):
    """The existing envelope was fetched by another query (no hash, or another
    one): its settled record (PR 1, without ``isDraft``) is not reused. The
    refresh pages the window in full, and the envelope it writes holds only the
    refetched records, stamped with the current query."""
    raw = tmp_path / "raw"
    old = {"window_anchor": (NOW - timedelta(days=5)).isoformat(), "window_days": 180, "cap": 500,
           "count": 2, "truncated": False, "cost_points": 1, "derived": [],
           "prs": [pr_node(2, "2026-08-20T00:00:00Z", merged="2026-08-21T00:00:00Z"),
                   pr_node(1, "2026-08-01T00:00:00Z", merged="2026-08-02T00:00:00Z")]}
    if recorded_query is not None:
        old["query_sha256"] = recorded_query
    write_envelope(raw / "pr_window" / "acme__widget.json", "github-graphql:pr-window", old,
                   (NOW - timedelta(days=5)).isoformat())
    fresh = [_rule_input_fields(pr_node(3, "2026-09-05T00:00:00Z")),
             _rule_input_fields(pr_node(2, "2026-08-20T00:00:00Z", merged="2026-08-21T00:00:00Z"))]
    tail = [_rule_input_fields(pr_node(1, "2026-08-01T00:00:00Z", merged="2026-08-02T00:00:00Z"))]
    fake = FakeTransport([page(fresh, True, "c1"), page(tail, False)], [1, 1])
    assert gql.collect_pr_window(raw, "acme", "widget", now=NOW, transport=fake,
                                 force=True, incremental_ledger=True) == "ok"
    assert len(fake.calls) == 2  # paged past the settled PR 2: nothing was reused
    data = read_envelope(raw / "pr_window" / "acme__widget.json").data
    assert data["query_sha256"] == gql.PR_WINDOW_QUERY_SHA256
    assert [p["number"] for p in data["prs"]] == [3, 2, 1]
    assert pr_rule_inputs_recorded(data["prs"])


def test_an_incremental_refresh_reuses_an_envelope_from_the_current_query(tmp_path):
    """Over-correction guard: an envelope stamped with the current query keeps
    its settled records; paging stops at the first settled PR."""
    raw = tmp_path / "raw"
    settled = _rule_input_fields(pr_node(2, "2026-08-20T00:00:00Z", merged="2026-08-21T00:00:00Z"))
    old = {"window_anchor": (NOW - timedelta(days=5)).isoformat(), "window_days": 180, "cap": 500,
           "count": 1, "truncated": False, "cost_points": 1, "derived": [], "prs": [settled],
           "query_sha256": gql.PR_WINDOW_QUERY_SHA256}
    write_envelope(raw / "pr_window" / "acme__widget.json", "github-graphql:pr-window", old,
                   (NOW - timedelta(days=5)).isoformat())
    fake = FakeTransport([page([_rule_input_fields(pr_node(3, "2026-09-05T00:00:00Z")), settled], True, "c1")], [1])
    assert gql.collect_pr_window(raw, "acme", "widget", now=NOW, transport=fake,
                                 force=True, incremental_ledger=True) == "ok"
    assert len(fake.calls) == 1


def test_pr_rule_inputs_recorded_needs_the_draft_flag_and_the_timeline_on_every_pr():
    current = _rule_input_fields(pr_node(1, "2026-09-01T00:00:00Z"))
    assert pr_rule_inputs_recorded([current, current])
    old = pr_node(2, "2026-09-01T00:00:00Z")
    assert not pr_rule_inputs_recorded([current, old])
    assert not pr_rule_inputs_recorded([{**old, "isDraft": False}])
    assert not pr_rule_inputs_recorded([{**old, "timelineItems": {"nodes": []}}])


# --- #51: 2,000 PRs whole, a light walk and a reply sample above, per-page retry -----------

class ScriptedWindow:
    """A repository's whole PR list (newest first), served from any cursor (the
    offset as a string) at any page size: ``PrWindow`` pages carry every field,
    ``PrWindowLight`` pages lose the replies, ``PrRepliesBatch`` returns the
    replies of the PRs it names. ``fail`` holds ``(query name, offset, first)``
    calls that fail once with a gateway timeout."""

    def __init__(self, nodes: list[dict], fail=()) -> None:
        self.nodes = nodes
        self.by_number = {node["number"]: node for node in nodes}
        self.fail = set(fail)
        self.calls: list[tuple[str, dict]] = []

    def execute(self, query: str, variables: dict) -> dict:
        name = query.split("(", 1)[0].split()[-1]
        self.calls.append((name, dict(variables)))
        if name == "PrRepliesBatch":
            repo = {}
            for key, number in variables.items():
                if key.startswith("n") and key[1:].isdigit():
                    node = self.by_number[number]
                    repo[f"p{key[1:]}"] = {"number": number, "reviews": node["reviews"],
                                           "comments": node["comments"],
                                           "reviewThreads": node["reviewThreads"]}
            return {"data": {"rateLimit": {"cost": 1}, "r": repo}}
        offset = int(variables.get("after") or 0)
        first = variables["first"]
        if (name, offset, first) in self.fail:
            self.fail.discard((name, offset, first))
            raise RuntimeError("gh api graphql rc=1: gh: HTTP 504: Gateway Timeout")
        chunk = [dict(node) for node in self.nodes[offset:offset + first]]
        if name == "PrWindowLight":
            chunk = [{k: v for k, v in node.items() if k not in ("reviews", "comments", "reviewThreads")}
                     for node in chunk]
        end = offset + len(chunk)
        return {"data": {"rateLimit": {"cost": 1}, "r": {"pullRequests": {
            "pageInfo": {"hasNextPage": end < len(self.nodes), "endCursor": str(end)},
            "nodes": chunk}}}}

    def pages(self, name: str) -> list[tuple[int, int]]:
        """``(offset, first)`` of each ``name`` call, failed ones included."""
        return [(int(v.get("after") or 0), v["first"]) for n, v in self.calls if n == name]


def _window_nodes(count: int, *, step_minutes: int = 60) -> list[dict]:
    """``count`` outside PRs, newest first, one an hour back from ``NOW``; every
    other one has a member's 90-character review two hours after it opened."""
    out = []
    for i in range(count):
        created = NOW - timedelta(hours=1) - timedelta(minutes=step_minutes * i)
        reviews = ([review(body="r" * 90, created=(created + timedelta(hours=2)).isoformat())]
                   if i % 2 == 0 else [])
        out.append(pr_node(count - i, created.isoformat(), reviews=reviews))
    return out


class _RefusesPagesAbove(ScriptedWindow):
    """A ScriptedWindow whose pages and light pages fail with an HTTP 502 whenever ``first`` is at least
    ``limit`` (GitHub refusing a page that is too heavy, every time it is asked), and are served otherwise."""

    def __init__(self, nodes: list[dict], limit: int) -> None:
        super().__init__(nodes)
        self.limit = limit

    def execute(self, query: str, variables: dict) -> dict:
        name = query.split("(", 1)[0].split()[-1]
        if name != "PrRepliesBatch" and variables["first"] >= self.limit:
            self.calls.append((name, dict(variables)))
            raise RuntimeError("gh api graphql rc=1: gh: HTTP 502: Bad Gateway")
        return super().execute(query, variables)


def test_the_pr_cap_is_2000_and_the_author_window_is_bounded_only_for_safety():
    assert (gql.PR_CAP, gql.PR_SAMPLE_CAP, gql.PR_PAGE_SIZE_LADDER, gql.PR_LIGHT_PAGE_SIZE) == (
        2000, 1000, (50, 25, 10), 100)
    assert not hasattr(gql, "PR_PAGE_SIZE_RETRY")
    assert gql.AUTHOR_WINDOW_CAP == 250_000


def test_an_author_window_that_reaches_its_bound_is_truncated_at_it():
    nodes = [w4_pr(700 - i, (NOW - timedelta(hours=12 * i)).isoformat(), f"u{i}") for i in range(600)]
    res = gql.fetch_author_window("acme", "widget", NOW, ScriptedWindow(nodes), cap=250)
    assert (len(res.prs), res.truncated) == (250, True)


def test_a_failing_page_is_read_again_in_pages_of_ten_then_the_walk_returns_to_its_size():
    nodes = _window_nodes(120)
    t = ScriptedWindow(nodes, fail=[("PrWindow", 0, 25)])
    res = gql.fetch_pr_window("acme", "widget", NOW, t, page_size=25)
    assert [p["number"] for p in res.prs] == [n["number"] for n in nodes]
    assert res.truncated is False
    # the failed 25-PR page is covered by pages of ten and a last one of five, then pages of 25 again
    assert t.pages("PrWindow") == [(0, 25), (0, 10), (10, 10), (20, 5),
                                   (25, 25), (50, 25), (75, 25), (100, 25)]


def test_a_local_gh_timeout_is_retried_in_pages_of_ten():
    import subprocess

    class TimesOutOnce(ScriptedWindow):
        def execute(self, query, variables):
            if not self.calls:
                self.calls.append(("PrWindow", dict(variables)))
                raise subprocess.TimeoutExpired(["gh", "api", "graphql"], 90)
            return super().execute(query, variables)

    t = TimesOutOnce(_window_nodes(12))
    res = gql.fetch_pr_window("acme", "widget", NOW, t, page_size=25)
    assert len(res.prs) == 12
    assert t.pages("PrWindow") == [(0, 25), (0, 10), (10, 10)]


def test_a_page_that_fails_again_in_pages_of_ten_fails_the_fetch():
    t = ScriptedWindow(_window_nodes(30), fail=[("PrWindow", 0, 25), ("PrWindow", 0, 10)])
    with pytest.raises(RuntimeError, match="504"):
        gql.fetch_pr_window("acme", "widget", NOW, t, page_size=25)


# --- the page ladder (50, 25, 10): a failed page goes down it from the same cursor ----------

def test_a_page_refused_at_100_is_read_at_50_then_the_walk_returns_to_100_and_the_prs_are_the_fault_free_ones():
    """T1.1: every page of 100 draws a 502, so each costs one failed call and two pages of 50; the PR tuple is
    the one a fault-free walk returns."""
    nodes = _window_nodes(230)
    clean = gql.fetch_pr_window("acme", "widget", NOW, ScriptedWindow(nodes), page_size=100)
    t = _RefusesPagesAbove(nodes, limit=100)
    res = gql.fetch_pr_window("acme", "widget", NOW, t, page_size=100)
    assert tuple(res.prs) == tuple(clean.prs)
    assert (res.truncated, res.sampled) == (clean.truncated, clean.sampled) == (False, False)
    assert t.pages("PrWindow") == [(0, 100), (0, 50), (50, 50), (100, 100), (100, 50), (150, 50),
                                   (200, 100), (200, 50)]


def test_a_page_refused_at_50_too_is_read_at_25_and_the_span_is_covered_before_the_walk_returns_to_its_size():
    """T1.2: a 502 at 50 then success at 25; four pages of 25 cover the failed page of 100."""
    nodes = _window_nodes(130)
    clean = gql.fetch_pr_window("acme", "widget", NOW, ScriptedWindow(nodes), page_size=100)
    t = _RefusesPagesAbove(nodes, limit=50)
    res = gql.fetch_pr_window("acme", "widget", NOW, t, page_size=100)
    assert tuple(res.prs) == tuple(clean.prs)
    assert t.pages("PrWindow") == [(0, 100), (0, 50), (0, 25), (25, 25), (50, 25), (75, 25),
                                   (100, 100), (100, 50), (100, 25), (125, 25)]


def test_a_page_refused_at_every_size_down_to_10_still_fails_the_fetch_without_waiting(monkeypatch):
    """T1.3: only a failure at the last rung propagates, at once: GitHub's own 502 is not a dropped network."""
    waits = []
    monkeypatch.setattr(gql, "_wait", waits.append)
    t = _RefusesPagesAbove(_window_nodes(30), limit=10)
    with pytest.raises(RuntimeError, match="502"):
        gql.fetch_pr_window("acme", "widget", NOW, t, page_size=100)
    assert t.pages("PrWindow") == [(0, 100), (0, 50), (0, 25), (0, 10)]
    assert waits == []


def test_a_page_that_fails_inside_the_span_goes_on_down_the_ladder_from_where_it_is():
    """The first half of the span was read at 50; the second half is refused at 50 and read at 25, and the walk
    then returns to 100. (Before the ladder, a failure inside the span failed the fetch.)"""
    nodes = _window_nodes(230)
    clean = gql.fetch_pr_window("acme", "widget", NOW, ScriptedWindow(nodes), page_size=100)
    t = ScriptedWindow(nodes, fail=[("PrWindow", 0, 100), ("PrWindow", 50, 50)])
    res = gql.fetch_pr_window("acme", "widget", NOW, t, page_size=100)
    assert tuple(res.prs) == tuple(clean.prs)
    assert t.pages("PrWindow") == [(0, 100), (0, 50), (50, 50), (50, 25), (75, 25), (100, 100), (200, 100)]


def test_a_walk_of_50_goes_down_to_25_and_a_walk_of_25_goes_straight_to_10():
    nodes = _window_nodes(120)
    t = ScriptedWindow(nodes, fail=[("PrWindow", 0, 50)])
    gql.fetch_pr_window("acme", "widget", NOW, t, page_size=50)
    assert t.pages("PrWindow") == [(0, 50), (0, 25), (25, 25), (50, 50), (100, 50)]
    t = ScriptedWindow(nodes, fail=[("PrWindow", 0, 25)])
    gql.fetch_pr_window("acme", "widget", NOW, t, page_size=25)
    assert t.pages("PrWindow")[:5] == [(0, 25), (0, 10), (10, 10), (20, 5), (25, 25)]


def test_a_walk_of_10_has_no_smaller_page_to_go_to():
    t = ScriptedWindow(_window_nodes(30), fail=[("PrWindow", 0, 10)])
    with pytest.raises(RuntimeError, match="504"):
        gql.fetch_pr_window("acme", "widget", NOW, t, page_size=10)
    assert t.pages("PrWindow") == [(0, 10)]


@pytest.mark.parametrize("page_size, fail", [
    (100, [("PrWindow", 100, 100), ("PrWindow", 100, 50), ("PrWindow", 125, 25)]),
    (25, [("PrWindow", 175, 25)]),
], ids=["page100", "page25"])
def test_a_refused_page_does_not_move_the_boundary_the_sampled_walk_stops_at(page_size, fail):
    """F-1: a refused page read again in smaller pages must cover exactly its span, so every later page boundary
    stays where a fault-free walk puts it. A last small page that overshot moved the cap boundary, the full-field
    walk read the 203-PR window to its end, and the record held every reply with ``sampled`` false where the
    fault-free walk samples."""
    nodes = _window_nodes(203)
    clean = gql.fetch_pr_window("acme", "widget", NOW, ScriptedWindow(nodes), page_size=page_size, cap=200,
                                sample_cap=50)
    assert clean.sampled is True
    res = gql.fetch_pr_window("acme", "widget", NOW, ScriptedWindow(nodes, fail=fail), page_size=page_size,
                              cap=200, sample_cap=50)
    assert res.sampled is True
    assert tuple(res.prs) == tuple(clean.prs)


def test_the_last_page_of_a_span_is_as_long_as_what_is_left_of_it():
    sizer = gql._PageSizer(25)
    assert sizer.first == 25
    assert sizer.retry_smaller("HTTP 502") is True
    assert [sizer.first] == [10]
    sizer.page_done()
    assert sizer.first == 10
    sizer.page_done()
    assert sizer.first == 5          # 5 PRs of the failed page's 25 are left
    sizer.page_done()
    assert sizer.first == 25         # the span is covered exactly: the walk is back on its own boundaries
    sizer = gql._PageSizer(60)
    assert sizer.retry_smaller("HTTP 504") is True and sizer.first == 50
    sizer.page_done()
    assert sizer.first == 10
    assert sizer.retry_smaller("HTTP 504") is False   # nothing between 10 and the last rung's size to go to
    sizer.page_done()
    assert sizer.first == 60


def test_the_light_walk_and_the_author_window_walk_use_the_same_ladder():
    nodes = _window_nodes(300)
    clean = gql.fetch_pr_window("acme", "widget", NOW, ScriptedWindow(nodes), page_size=30, cap=50, sample_cap=20)
    t = _RefusesPagesAbove(nodes, limit=100)
    res = gql.fetch_pr_window("acme", "widget", NOW, t, page_size=30, cap=50, sample_cap=20)
    assert [p["number"] for p in res.prs] == [p["number"] for p in clean.prs]
    assert t.pages("PrWindowLight") == [(60, 100), (60, 50), (110, 50), (160, 100), (160, 50), (210, 50),
                                        (260, 100), (260, 50)]
    author_nodes = [w4_pr(700 - i, (NOW - timedelta(hours=12 * i)).isoformat(), f"u{i}") for i in range(230)]
    clean_window = gql.fetch_author_window("acme", "widget", NOW, ScriptedWindow(author_nodes))
    t = _RefusesPagesAbove(author_nodes, limit=100)
    window = gql.fetch_author_window("acme", "widget", NOW, t)
    assert tuple(window.prs) == tuple(clean_window.prs)
    assert t.pages("AuthorWindow") == [(0, 100), (0, 50), (50, 50), (100, 100), (100, 50), (150, 50),
                                       (200, 100), (200, 50)]


@pytest.mark.parametrize("width, expected", [
    (100, (50, 25, 10, 1)), (50, (25, 10, 1)), (30, (25, 10, 1)), (25, (10, 1)), (10, (1,)), (5, (1,)), (1, ()),
])
def test_the_batch_width_ladder_is_the_page_ladder_then_one(width, expected):
    """An aliased batch (reply lookups, number lookups) retries at the page ladder's rungs below its width, then
    alone: the ladder PR-3's number reads go down."""
    assert gql.batch_retry_widths(width) == expected


def test_the_batch_width_ladder_follows_the_page_ladder(monkeypatch):
    monkeypatch.setattr(gql, "PR_PAGE_SIZE_LADDER", (40, 20))
    assert gql.batch_retry_widths(100) == (40, 20, 1)
    t = ScriptedWindow(_window_nodes(100), fail=[("PrWindow", 0, 100)])
    gql.fetch_pr_window("acme", "widget", NOW, t, page_size=100)
    assert t.pages("PrWindow") == [(0, 100), (0, 40), (40, 40), (80, 20)]


def test_a_page_failing_for_another_reason_is_not_retried():
    calls = []

    class Refused:
        def execute(self, query, variables):
            calls.append(variables)
            raise RuntimeError("gh api graphql rc=1: HTTP 401: Bad credentials")

    with pytest.raises(RuntimeError, match="401"):
        gql.fetch_pr_window("acme", "widget", NOW, Refused())
    assert len(calls) == 1


def test_a_window_above_the_cap_is_recorded_whole_with_replies_on_a_seeded_sample():
    nodes = _window_nodes(130)
    t = ScriptedWindow(nodes)
    res = gql.fetch_pr_window("acme", "widget", NOW, t, page_size=30, cap=50, sample_cap=20)
    # Every PR of the window, in order: the cap fell inside the second page, which is finished
    # (leaving it part-way would drop its last ten PRs), then the light walk runs at 100.
    assert [p["number"] for p in res.prs] == [n["number"] for n in nodes]
    assert (res.truncated, res.sampled) == (False, True)
    assert t.pages("PrWindow") == [(0, 30), (30, 30)]
    assert t.pages("PrWindowLight") == [(60, 100)]
    sample = gql.reply_sample_numbers("acme", "widget", [n["number"] for n in nodes], 20)
    assert len(sample) == 20
    assert {p["number"] for p in gql.reply_sampled_prs(res.prs)} == sample
    for pr in res.prs:
        if pr["number"] in sample:
            assert pr["reviews"] == t.by_number[pr["number"]]["reviews"]
            assert gql.REPLIES_RECORDED_KEY not in pr
        else:
            assert pr[gql.REPLIES_RECORDED_KEY] is False
            assert not {"reviews", "comments", "reviewThreads"} & set(pr)
    # replies were read by number only for the sampled PRs the light walk listed
    heavy = {n["number"] for n in nodes[:60]}
    asked = sorted(v for name, call in t.calls if name == "PrRepliesBatch"
                   for k, v in call.items() if k.startswith("n") and k[1:].isdigit())
    assert asked == sorted(n for n in sample if n not in heavy)
    # reply measures read the sample only: one derived row per PR, None where unsampled
    derived = {d["number"]: d for d in gql.derive_pr_window(res.prs)}
    assert {n for n, d in derived.items() if d["substantive_review"] is not None} == sample


def test_the_reply_readers_count_only_the_sampled_prs_of_a_sampled_window():
    """RFC-0013: a PR outside the sample carries no replies, so a reply reader that counted it would read it as
    unanswered. GR_14's and D-GATE-5's records and the closed-unread counts hold the sampled PRs only; the hand-off
    share is taken over the sampled outside PRs."""
    from porchlamp.core import constants
    from porchlamp.derive import gate_evidence

    nodes = _window_nodes(130)
    res = gql.fetch_pr_window("acme", "widget", NOW, ScriptedWindow(nodes), page_size=30, cap=50, sample_cap=20)
    prs = [dict(pr) for pr in res.prs]
    sample = gql.reply_sample_numbers("acme", "widget", [n["number"] for n in nodes], 20)
    created = {pr["createdAt"] for pr in prs if pr["number"] in sample}
    assert {r.created_at for r in gql.ghost_rate_prs_from_window(prs)} == created
    assert {r.created_at for r in gate_evidence._ghost_rate_prs_from_nodes(prs)} == created
    assert gql.closed_unread_counts(prs) == (0, 20)
    # a hand-off notice on 6 of the 20 sampled outside PRs: 30% of the sample, so no hand-off ...
    notice = comment(login="gerrit-relay", assoc="NONE", body="This change has been imported to Gerrit for code review.")
    noticed = sorted(sample)[:6]
    for pr in prs:
        if pr["number"] in noticed:
            pr["comments"] = {"nodes": [notice]}
    data = {"prs": prs, "derived": gql.derive_pr_window(prs)}
    assert 6 >= constants.GATE6_HANDOFF_MIN_PRS
    assert gate_evidence._review_handoff_in(data) is False
    # ... and on 12 of the 20 it is one, however many unsampled outside PRs (with no comments recorded) the window holds
    for pr in prs:
        if pr["number"] in sorted(sample)[:12]:
            pr["comments"] = {"nodes": [notice]}
    assert gate_evidence._review_handoff_in({"prs": prs, "derived": gql.derive_pr_window(prs)}) is True


def test_without_a_reply_sample_the_record_stops_at_exactly_the_cap_and_is_cut():
    t = ScriptedWindow(_window_nodes(130))
    res = gql.fetch_pr_window("acme", "widget", NOW, t, page_size=30, cap=50, sample_cap=None)
    assert (len(res.prs), res.truncated, res.sampled) == (50, True, False)
    assert gql.pr_window_cut_short({"window_anchor": NOW.isoformat(), "window_days": 180,
                                    "truncated": True, "prs": list(res.prs)})


def test_the_reply_sample_is_drawn_by_pr_number_and_survives_a_sliding_window():
    numbers = list(range(1, 3001))
    sample = gql.reply_sample_numbers("acme", "widget", numbers, 1000)
    assert len(sample) == 1000 and sample <= set(numbers)
    assert gql.reply_sample_numbers("acme", "widget", list(reversed(numbers)), 1000) == sample
    assert gql.reply_sample_numbers("ACME", "Widget", numbers, 1000) == sample
    assert gql.reply_sample_numbers("acme", "other", numbers, 1000) != sample
    # the window slides by 100 PRs: a rerun keeps almost all of the sample
    slid = gql.reply_sample_numbers("acme", "widget", list(range(101, 3101)), 1000)
    assert len(sample & slid) >= 900
    assert gql.reply_sample_numbers("acme", "widget", numbers[:10], 1000) == set(numbers[:10])


def test_a_ledger_delta_that_reaches_the_cap_continues_as_a_fresh_fetch():
    nodes = _window_nodes(80)
    t = ScriptedWindow(nodes)
    res = gql.fetch_pr_window("acme", "widget", NOW, t, page_size=20, cap=30, sample_cap=10,
                              existing_prs=[dict(nodes[70])],
                              existing_anchor=NOW - timedelta(days=1))
    assert [p["number"] for p in res.prs] == [n["number"] for n in nodes]
    assert (res.truncated, res.sampled) == (False, True)
    assert t.pages("PrWindowLight") == [(40, 100)]


@pytest.mark.parametrize("old_flags", [{"truncated": True}, {"truncated": False, "sampled": True}])
def test_the_ledger_refetches_a_cut_or_sampled_envelope_in_full(tmp_path, old_flags):
    """A cut record would stay cut under a refresh that stops at its first settled
    PR, and merging fresh PRs into a sample would over-represent them."""
    raw = tmp_path / "raw"
    settled = _rule_input_fields(pr_node(2, "2026-08-20T00:00:00Z", merged="2026-08-21T00:00:00Z"))
    old = {"window_anchor": (NOW - timedelta(days=5)).isoformat(), "window_days": 180, "cap": 500,
           "count": 1, "cost_points": 1, "derived": [], "prs": [settled],
           "query_sha256": gql.PR_WINDOW_QUERY_SHA256, **old_flags}
    write_envelope(raw / "pr_window" / "acme__widget.json", "github-graphql:pr-window", old,
                   (NOW - timedelta(days=5)).isoformat())
    fresh = _rule_input_fields(pr_node(3, "2026-09-05T00:00:00Z"))
    tail = _rule_input_fields(pr_node(1, "2026-08-01T00:00:00Z", merged="2026-08-02T00:00:00Z"))
    fake = FakeTransport([page([fresh, settled], True, "c1"), page([tail], False)], [1, 1])
    assert gql.collect_pr_window(raw, "acme", "widget", now=NOW, transport=fake,
                                 force=True, incremental_ledger=True) == "ok"
    assert len(fake.calls) == 2  # paged past the settled PR 2: nothing was reused
    data = read_envelope(raw / "pr_window" / "acme__widget.json").data
    assert [p["number"] for p in data["prs"]] == [3, 2, 1]
    assert data["truncated"] is False


def test_the_author_window_is_the_whole_365_days_and_a_failing_page_is_read_in_fifties():
    nodes = [w4_pr(700 - i, (NOW - timedelta(hours=12 * i)).isoformat(), f"u{i}") for i in range(600)]
    t = ScriptedWindow(nodes, fail=[("AuthorWindow", 500, 100)])
    res = gql.fetch_author_window("acme", "widget", NOW, t)
    assert (len(res.prs), res.truncated) == (600, False)
    assert t.pages("AuthorWindow")[5:] == [(500, 100), (500, 50), (550, 50)]


def test_a_record_cut_short_of_its_window_yields_no_window_statistics():
    nodes = _window_nodes(40)
    cut = {"window_anchor": NOW.isoformat(), "window_days": 180, "cap": 40, "count": 40,
           "truncated": True, "cost_points": 4, "prs": nodes, "derived": gql.derive_pr_window(nodes)}
    assert gql.pr_window_cut_short(cut)
    fields = gql.derive_pr_window_fields(cut)
    assert fields["pr_window_count"] == (40, "verified")
    assert fields["pr_window_truncated"] == (True, "verified")
    for name in ("pr_window_external_measured_latency_count", "pr_window_ttfhr_median_hours",
                 "pr_window_substantive_review_rate", "pr_window_outside_pr_count",
                 "pr_window_closed_unread_count"):
        assert fields[name] == (None, "missing"), name
    whole = {**cut, "truncated": False}
    assert not gql.pr_window_cut_short(whole)
    assert gql.derive_pr_window_fields(whole)["pr_window_ttfhr_median_hours"] == (2.0, "derived")


def test_gr14_judges_a_truncated_record_against_the_window_it_is_computed_over():
    """Fetched ten days before derive, cut at its cap 185 days before derive: short of its
    own window, but it holds every PR of the window GR_14 is computed over (#52)."""
    record = {"window_anchor": (NOW - timedelta(days=10)).isoformat(), "window_days": 180,
              "truncated": True,
              "prs": [pr_node(2, (NOW - timedelta(days=3)).isoformat()),
                      pr_node(1, (NOW - timedelta(days=185)).isoformat())]}
    assert gql.pr_window_cut_short(record)
    assert not gql.pr_window_cut_short(record, NOW - timedelta(days=180))
    assert gql.pr_window_cut_short(record, NOW - timedelta(days=186))
    assert not gql.pr_window_cut_short({**record, "truncated": False})


def test_an_author_history_cut_short_of_a_cohort_start_yields_no_rate_for_that_cohort():
    logins = [f"u{i}" for i in range(10)]
    prs = [w4_days(500 + i, 300 - i, login, merged_after=5) for i, login in enumerate(logins)]
    fields = gql.derive_cohort_fields(
        _w4_ah_data(prs, [w4_rec(login) for login in logins], sampled=logins, truncated=True))
    # reaching back 300 days covers the 270-day decision cohort, not the 365-day newcomer band
    assert fields["mr_first_rate"][1] == "derived"
    assert fields["newcomer_conversion"] == (None, "missing")
    assert fields["rr90_return_rate"] == (None, "missing")
    assert fields["newcomer_unmeasured_detail"][0] == (
        "author_history window truncated before reaching 365d newcomer band start")
    short = gql.derive_cohort_fields(_w4_ah_data([w4_days(600, 200, "v")], [w4_rec("v")], truncated=True))
    assert short["mr_first_rate"] == (None, "missing")
    assert short["dr90_decision_rate_author_history_cohort"] == (None, "missing")
    assert short["cohort_unmeasured_detail"][0].startswith(
        "author_history window truncated before reaching the 270d decision-cohort start")


# =============================================================================
# RFC-0015 (#60): pull requests landed outside GitHub's merge
# =============================================================================


def _with_landing(node: dict, *, labels: tuple[str, ...] = (), closer: str | None = None) -> dict:
    """Attach the RFC-0015 ``labels`` and ``timelineItems`` fields to a PR node."""
    closed_at = node.get("closedAt")
    events = []
    if closed_at is not None:
        events.append({
            "__typename": "ClosedEvent",
            "createdAt": closed_at,
            "actor": {"__typename": "User", "login": "bob"},
            "closer": None if closer is None else (
                {"__typename": "Commit", "oid": "deadbeef"} if closer == "Commit"
                else {"__typename": closer, "number": 99}),
        })
    return {
        **node,
        "labels": {"nodes": [{"name": name} for name in labels]},
        "timelineItems": {"nodes": events},
    }


def test_author_window_and_light_history_queries_record_landing_fields():
    """RFC-0015: both author-history window queries include ``PR_LANDING_FIELDS`` and balance braces."""
    from porchlamp.collect.ledger import LIGHT_PR_HISTORY_QUERY
    from porchlamp.core import landing

    for query in (gql.AUTHOR_WINDOW_QUERY, LIGHT_PR_HISTORY_QUERY):
        assert landing.PR_LANDING_FIELDS in query
        assert query.count("{") == query.count("}")


def test_fetch_light_pr_history_preserves_landing_fields_when_present():
    """RFC-0015: ``fetch_light_pr_history`` keeps ``labels`` and ``timelineItems`` from a page that
    records them and omits them on a page that does not."""
    landed_raw = _with_landing(
        w4_pr(10, "2026-05-01T00:00:00Z", "alice", closed="2026-05-02T00:00:00Z"),
        labels=("Merged",),
        closer="Commit",
    )
    plain_raw = w4_pr(11, "2026-04-01T00:00:00Z", "bob", closed="2026-04-02T00:00:00Z")

    class _LightTransport:
        def execute(self, query: str, variables: dict) -> dict:
            return {
                "data": {
                    "rateLimit": {"cost": 2, "remaining": 4998},
                    "repository": {
                        "pullRequests": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [landed_raw, plain_raw],
                        }
                    },
                }
            }

    by_author, cost, remaining, ok = gql.fetch_light_pr_history("acme", "widget", _LightTransport())
    assert (cost, remaining, ok) == (2, 4998, True)
    assert by_author["alice"][0]["labels"] == landed_raw["labels"]
    assert by_author["alice"][0]["timelineItems"] == landed_raw["timelineItems"]
    assert "labels" not in by_author["bob"][0]
    assert "timelineItems" not in by_author["bob"][0]


def test_derive_pr_window_landing_fields_and_derive_repo_hook(tmp_path):
    """RFC-0015: ``derive_pr_window_landing_fields`` records whether every PR node has the landing
    fields and counts only external non-bot landed PRs; refuses the count on a cut window; and
    ``scripts/derive.py`` writes both fields with ``raw/pr_window/<key>.json`` provenance."""
    assert gql.derive_pr_window_landing_fields(None) == {}
    assert gql.derive_pr_window_landing_fields({"count": 0}) == {}
    assert gql.derive_pr_window_landing_fields({"prs": []}) == {
        "pr_window_landing_recorded": (False, "verified"),
        "pr_window_landed_external_count": (0, "derived"),
    }

    prs = [
        # 1: external, landed by commit closer -> counted
        _with_landing(pr_node(1, "2026-08-01T00:00:00Z", closed="2026-08-02T00:00:00Z"), closer="Commit"),
        # 2: external, landed by "integrated" label -> counted
        _with_landing(pr_node(2, "2026-08-03T00:00:00Z", closed="2026-08-04T00:00:00Z"), labels=("integrated",)),
        # 3: external, GitHub-merged -> not landed outside GitHub's merge
        _with_landing(
            pr_node(3, "2026-08-05T00:00:00Z", closed="2026-08-06T00:00:00Z", merged="2026-08-06T00:00:00Z"),
            labels=("Merged",),
            closer="Commit",
        ),
        # 4: external, plain close -> not landed
        _with_landing(pr_node(4, "2026-08-07T00:00:00Z", closed="2026-08-08T00:00:00Z")),
        # 5: internal (MEMBER), landed by commit closer -> not external
        _with_landing(
            pr_node(5, "2026-08-09T00:00:00Z", closed="2026-08-10T00:00:00Z", assoc="MEMBER", author_login="bob"),
            closer="Commit",
        ),
        # 6: bot author, landed by commit closer -> not human external
        _with_landing(
            pr_node(6, "2026-08-11T00:00:00Z", closed="2026-08-12T00:00:00Z",
                    author_login="dependabot[bot]", author_typ="Bot"),
            closer="Commit",
        ),
    ]
    whole = {
        "window_anchor": NOW.isoformat(), "window_days": 180, "cap": 2000,
        "count": len(prs), "truncated": False, "cost_points": 1,
        "prs": prs, "derived": gql.derive_pr_window(prs),
    }
    assert gql.derive_pr_window_landing_fields(whole) == {
        "pr_window_landing_recorded": (True, "verified"),
        "pr_window_landed_external_count": (2, "derived"),
    }

    # One node missing the landing fields -> pr_window_landing_recorded is False
    partial = {**whole, "prs": [*prs[:-1], pr_node(7, "2026-08-13T00:00:00Z", closed="2026-08-14T00:00:00Z")]}
    assert gql.derive_pr_window_landing_fields(partial)["pr_window_landing_recorded"] == (False, "verified")

    # Cut-short window -> count is (None, "missing"), recorded flag stays (True, "verified")
    cut = {**whole, "truncated": True}
    assert gql.derive_pr_window_landing_fields(cut) == {
        "pr_window_landing_recorded": (True, "verified"),
        "pr_window_landed_external_count": (None, "missing"),
    }

    # End-to-end through scripts/derive.py
    write_env(tmp_path / "raw", "pr_window", KEY, whole)
    bundle = derive.derive_repo("acme", "widget", derive.Ecosystem.PYPI, tmp_path, fetch=False, now=NOW)
    assert bundle["meta"]["pr_window_landing_recorded"] is True
    assert bundle["meta"]["pr_window_landed_external_count"] == 2
    assert bundle["confidence"]["pr_window_landing_recorded"] == "verified"
    assert bundle["confidence"]["pr_window_landed_external_count"] == "derived"
    assert bundle["provenance"]["pr_window_landed_external_count"]["source"] == "raw/pr_window/acme__widget.json"


def test_author_pr_history_and_no_prior_merged_preserve_window_landing_across_search():
    """RFC-0015: a per-author search entry carries neither ``labels`` nor ``timelineItems``; when the
    same PR is also in the 365-day window with a landing signal, ``_author_pr_history`` keeps those
    fields so ``_no_prior_merged`` disqualifies an author whose earlier PR was landed before the
    cohort PR opened (and keeps one whose earlier PR was landed after)."""
    earlier_landed = _with_landing(
        w4_pr(10, "2026-01-10T00:00:00Z", "alice", closed="2026-01-20T00:00:00Z"),
        labels=("Merged",),
    )
    later_pr = _with_landing(w4_pr(20, "2026-04-01T00:00:00Z", "alice", closed="2026-04-05T00:00:00Z"))
    # Search entry for PR 10 has no labels/timelineItems
    records = {"alice": w4_rec("alice", prs=[w4_spr(10, "2026-01-10T00:00:00Z", closed="2026-01-20T00:00:00Z")])}
    merged_hist = {p["number"]: p for p in gql._author_pr_history([earlier_landed, later_pr], records["alice"], "alice")}
    assert merged_hist[10]["labels"] == earlier_landed["labels"]
    assert gql._no_prior_merged(later_pr, [earlier_landed, later_pr], records) is False

    # Closed after later_pr opened -> still a first-timer at later_pr's creation
    landed_after = _with_landing(
        w4_pr(10, "2026-01-10T00:00:00Z", "alice", closed="2026-04-10T00:00:00Z"),
        closer="Commit",
    )
    records_after = {"alice": w4_rec("alice", prs=[w4_spr(10, "2026-01-10T00:00:00Z", closed="2026-04-10T00:00:00Z")])}
    assert gql._no_prior_merged(later_pr, [landed_after, later_pr], records_after) is True


def test_derive_cohort_fields_counts_landed_prs_in_mr_first_conversion_and_rr90():
    """RFC-0015: ``derive_cohort_fields`` counts landed PRs as merged at their ``closedAt`` across
    1.3 MR_first, 5.2 newcomer conversion and 5.3 RR_90, records the landed counts and
    ``author_history_landing_recorded``, and leaves 1.2 DR_90 unchanged."""
    # Decision cohort [90d, 270d]:
    # - d1: first-timer, landed by commit closer -> merged for MR_first (landed)
    # - d2: first-timer, landed by "Merged" label -> merged for MR_first (landed)
    # - d3: first-timer, GitHub-merged -> merged for MR_first (not landed)
    # - d4: first-timer, plain close -> decided, not merged
    # - d5: had an earlier landed PR (300d ago, closed 295d ago) -> excluded from MR_first
    d1 = _with_landing(w4_days(101, 150, "d1", closed_after=5), closer="Commit")
    d2 = _with_landing(w4_days(102, 150, "d2", closed_after=5), labels=("Merged",))
    d3 = _with_landing(w4_days(103, 150, "d3", merged_after=5))
    d4 = _with_landing(w4_days(104, 150, "d4", closed_after=5))
    d5_prior = _with_landing(w4_days(99, 300, "d5", closed_after=5), closer="Commit")
    d5_cohort = _with_landing(w4_days(105, 150, "d5", closed_after=5), closer="Commit")

    # Newcomer cohort [180d, 365d] (placed at 300d/280d ago, outside the 270d decision band):
    # - n1: first PR 300d ago, landed after 10d (converts), followup 20d after landing (returns)
    # - n2: first PR 300d ago, landed after 181d (> 180d: does NOT convert, but IS in RR_90 denominator, no followup)
    # - n3: first PR 300d ago, GitHub-merged after 10d (converts, not landed), followup 10d after merge (returns)
    # - n4: first PR 300d ago, plain close after 10d (neither converts nor enters RR_90)
    n1_first = _with_landing(w4_days(201, 300, "n1", closed_after=10), closer="Commit")
    n1_follow = _with_landing(w4_days(202, 280, "n1", closed_after=5), labels=("integrated",))
    n2_first = _with_landing(w4_days(203, 300, "n2", closed_after=181), labels=("Merged",))
    n3_first = _with_landing(w4_days(204, 300, "n3", merged_after=10))
    n3_follow = _with_landing(w4_days(205, 280, "n3"))
    n4_first = _with_landing(w4_days(206, 300, "n4", closed_after=10))

    prs = [d1, d2, d3, d4, d5_prior, d5_cohort, n1_first, n1_follow, n2_first, n3_first, n3_follow, n4_first]
    logins = ["d1", "d2", "d3", "d4", "d5", "n1", "n2", "n3", "n4"]
    records = [w4_rec(login) for login in logins]
    sampled = ["n1", "n2", "n3", "n4"]
    fields = gql.derive_cohort_fields(_w4_ah_data(prs, records, sampled=sampled))

    assert fields["author_history_landing_recorded"] == (True, "verified")
    # 1.2 DR_90: all 5 decision-band PRs (d1..d5_cohort) closed within 5d
    assert fields["dr90_decision_rate_author_history_cohort"] == (1.0, "derived")
    assert fields["dr90_decision_pr_count_author_history_cohort"] == (5, "derived")
    # 1.3 MR_first: d5 excluded by prior landed PR; of {d1, d2, d3, d4}, 3 merged (2 landed + 1 GitHub)
    assert fields["mr_first_rate_raw"] == (0.75, "derived")
    assert fields["mr_first_pr_count_raw"] == (4, "derived")
    assert fields["mr_first_rate"] == (0.75, "derived")
    assert fields["mr_first_pr_count"] == (4, "derived")
    assert fields["mr_first_landed_pr_count"] == (2, "derived")
    # 5.2 newcomer_conversion: n1 (landed at 10d) and n3 (GitHub-merged at 10d) convert; n2 (landed at 181d) and n4 do not
    assert fields["newcomer_conversion"] == (0.5, "derived")
    assert fields["newcomer_conversion_author_count"] == (4, "derived")
    assert fields["newcomer_conversion_landed_author_count"] == (1, "derived")
    # 5.3 RR_90: n1, n2, n3 have a merged/landed first PR (2 of them landed: n1 and n2); n1 and n3 return within 90d
    assert fields["rr90_return_rate"] == (round(2 / 3, 4), "derived")
    assert fields["rr90_return_author_count"] == (3, "derived")
    assert fields["rr90_return_landed_author_count"] == (2, "derived")

    roster = {a["login"]: a for a in fields["cohort_newcomer_authors"][0]}
    assert roster["n1"]["first_pr"]["merged_at"] == n1_first["closedAt"]
    assert roster["n1"]["followups"][0]["merged_at"] == n1_follow["closedAt"]
    assert roster["n4"]["first_pr"]["merged_at"] is None

    # Cut-short window sets the three landed counts to (None, "missing")
    cut_fields = gql.derive_cohort_fields(
        _w4_ah_data([_with_landing(w4_days(1, 100, "u1", closed_after=5))], [w4_rec("u1")], sampled=["u1"], truncated=True)
    )
    assert cut_fields["mr_first_landed_pr_count"] == (None, "missing")
    assert cut_fields["newcomer_conversion_landed_author_count"] == (None, "missing")
    assert cut_fields["rr90_return_landed_author_count"] == (None, "missing")


# --- RFC-0022: each PR's reviews and comments whole up to PR_REPLY_NODE_CAP -----------------

def test_the_window_page_and_the_reply_batch_record_each_reply_lists_total_and_page_info():
    """The first read of a PR's replies records whether GitHub holds more (``pageInfo``) and how
    many (``totalCount``); the timeline reads the newest 100 ready-for-review and close events."""
    reviews = ("reviews(first: 10) { totalCount pageInfo { hasNextPage endCursor } nodes { author {"
               " __typename login } state body createdAt authorAssociation comments { totalCount } } }")
    comments = ("comments(first: 20) { totalCount pageInfo { hasNextPage endCursor } nodes { author {"
                " __typename login } authorAssociation bodyText createdAt } }")
    batch = gql._pr_replies_batch_query(2)
    for query in (gql.PR_WINDOW_QUERY, batch):
        assert reviews in query and comments in query
    assert batch.count(reviews) == batch.count(comments) == 2
    timeline = "timelineItems(itemTypes: [READY_FOR_REVIEW_EVENT, CLOSED_EVENT], last: 100) {"
    for query in (gql.PR_WINDOW_QUERY, gql.PR_WINDOW_LIGHT_QUERY):
        assert timeline in query
    assert (gql.PR_REVIEWS_FIRST, gql.PR_COMMENTS_FIRST, gql.PR_REPLY_PAGE_SIZE, gql.PR_REPLY_NODE_CAP) == (
        10, 20, 100, 300)


class ThreadedWindow(ScriptedWindow):
    """A ``ScriptedWindow`` whose PR nodes hold their whole threads. ``PrWindow`` pages and
    ``PrRepliesBatch`` lookups carry each PR's first ``PR_REVIEWS_FIRST`` reviews and
    ``PR_COMMENTS_FIRST`` comments with GitHub's ``totalCount`` and ``pageInfo`` (the cursor is
    the offset); ``PrRepliesFollowUp`` serves the page each alias asks for. A PR in ``stall``
    gets an empty page that says more remain; one in ``refuse`` gets a GraphQL error."""

    def __init__(self, nodes: list[dict], *, stall=(), refuse=()) -> None:
        super().__init__(nodes)
        self.stall, self.refuse = set(stall), set(refuse)

    @staticmethod
    def _page(items: list, offset: int, first: int) -> dict:
        chunk = items[offset:offset + first]
        end = offset + len(chunk)
        return {"totalCount": len(items), "pageInfo": {"hasNextPage": end < len(items), "endCursor": str(end)},
                "nodes": chunk}

    def _first_reads(self, node: dict) -> dict:
        out = dict(node)
        for field, first in (("reviews", gql.PR_REVIEWS_FIRST), ("comments", gql.PR_COMMENTS_FIRST)):
            if field in node:
                out[field] = self._page(node[field]["nodes"], 0, first)
        return out

    def execute(self, query: str, variables: dict) -> dict:
        name = query.split("(", 1)[0].split()[-1]
        if name != "PrRepliesFollowUp":
            doc = super().execute(query, variables)
            repo = doc["data"]["r"]
            if name == "PrWindow":
                repo["pullRequests"]["nodes"] = [self._first_reads(n) for n in repo["pullRequests"]["nodes"]]
            elif name == "PrRepliesBatch":
                for alias, node in repo.items():
                    repo[alias] = self._first_reads(node)
            return doc
        self.calls.append((name, dict(variables)))
        repo, errors = {}, []
        for key, number in variables.items():
            if not (key.startswith("n") and key[1:].isdigit()):
                continue
            i = key[1:]
            if number in self.refuse:
                errors.append({"message": "Something went wrong", "path": ["r", f"p{i}"]})
                continue
            out = {"number": number}
            for field, short in (("reviews", "r"), ("comments", "c")):
                if f"{short}a{i}" not in variables:
                    continue
                if number in self.stall:
                    out[field] = {"totalCount": 99, "nodes": [],
                                  "pageInfo": {"hasNextPage": True, "endCursor": variables[f"{short}a{i}"]}}
                else:
                    out[field] = self._page(self.by_number[number][field]["nodes"],
                                            int(variables[f"{short}a{i}"]), variables[f"{short}f{i}"])
            repo[f"p{i}"] = out
        doc = {"data": {"rateLimit": {"cost": 1}, "r": repo}}
        if errors:
            doc["errors"] = errors
        return doc

    def follow_ups(self) -> list[dict]:
        """``{number: {field: (after, first)}}`` per ``PrRepliesFollowUp`` call."""
        out = []
        for name, call in self.calls:
            if name != "PrRepliesFollowUp":
                continue
            asked: dict = {}
            for key, number in call.items():
                if key.startswith("n") and key[1:].isdigit():
                    i = key[1:]
                    asked[number] = {field: (call[f"{short}a{i}"], call[f"{short}f{i}"])
                                     for field, short in (("reviews", "r"), ("comments", "c"))
                                     if f"{short}a{i}" in call}
            out.append(asked)
        return out


_T0 = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _at(hours: float) -> str:
    return (_T0 + timedelta(hours=hours)).isoformat()


def _bot_reply(minutes: int) -> dict:
    return comment("ci-bot", typ="Bot", assoc="NONE", body="CI report: every check passed on this commit.",
                   created=_at(minutes / 60))


def _threads() -> list[dict]:
    """Three outside PRs, newest first. PR 3 is whole. PR 2's twelfth review is the first
    maintainer's (an approval with a 90-character body), after eleven bot reviews. PR 1's
    twenty-first comment is a maintainer's reply at hour 5, after twenty bot comments; its
    one review, a maintainer's, comes at hour 50."""
    pr1 = pr_node(1, _at(0), closed=_at(96),
                  comments=[_bot_reply(m) for m in range(1, 21)]
                  + [comment("maint", body="Thanks! Could you add a test for the empty case?", created=_at(5))]
                  + [comment("passer", assoc="NONE", body="+1, I need this fix too, any update?", created=_at(6 + k))
                     for k in range(4)],
                  reviews=[review("maint", created=_at(50))])
    pr2 = pr_node(2, _at(1), closed=_at(72), merged=_at(72), merged_by="maint",
                  reviews=[review("copilot-pull-request-reviewer", typ="Bot", assoc="NONE", body="b" * 300,
                                  created=_at(1 + k)) for k in range(1, 12)]
                  + [review("maint", state="APPROVED", body="r" * 90, created=_at(31))])
    pr3 = pr_node(3, _at(2), comments=[comment("maint", body="m" * 45, created=_at(4))])
    return [pr3, pr2, pr1]


def test_a_cut_reply_list_is_read_on_by_number_until_whole():
    nodes = _threads()
    t = ThreadedWindow(nodes)
    res = gql.fetch_pr_window("acme", "widget", NOW, t, page_size=25)
    got = {pr["number"]: pr for pr in res.prs}
    full = {node["number"]: node for node in nodes}
    assert got[1]["comments"]["nodes"] == full[1]["comments"]["nodes"]
    assert got[1]["comments"]["pageInfo"] == {"hasNextPage": False, "endCursor": "25"}
    assert got[1]["comments"]["totalCount"] == 25
    assert got[2]["reviews"]["nodes"] == full[2]["reviews"]["nodes"]
    assert got[2]["reviews"]["pageInfo"]["hasNextPage"] is False
    assert not any(gates.replies_cut(pr) for pr in res.prs)
    # one follow-up read, naming only the cut lists, from where the first read stopped
    assert t.follow_ups() == [{1: {"comments": ("20", 100)}, 2: {"reviews": ("10", 100)}}]
    assert res.cost_points == 2  # the page and the follow-up read


def test_the_followed_up_replies_reach_ttfhr_the_review_rate_and_the_review_proxy():
    """1.1, 4.3 and the 4.1 proxy read the replies the first read cut off: PR 1 was answered at
    hour 5 (not at the review at hour 50), PR 2's approval is its twelfth review."""
    from porchlamp.collect import pike_signals

    res = gql.fetch_pr_window("acme", "widget", NOW, ThreadedWindow(_threads()), page_size=25)
    prs = list(res.prs)
    rows = {row["number"]: row for row in gql.derive_pr_window(prs)}
    assert (rows[1]["first_substantive_response_at"], rows[1]["ttfhr_hours"]) == (_at(5), 5.0)
    assert (rows[2]["ttfhr_hours"], rows[2]["substantive_review"]) == (30.0, True)
    fields = gql.derive_pr_window_fields({"count": 3, "truncated": False, "cost_points": 2,
                                          "prs": prs, "derived": gql.derive_pr_window(prs)})
    assert fields["pr_window_ttfhr_median_hours"] == (5.0, "derived")  # of 5, 30 and 2 hours
    assert fields["pr_window_substantive_review_rate"] == (0.5, "derived")  # PR 2 of PRs 1 and 2
    assert pike_signals.code_review_rate(prs) == 1.0


def _silent_cut_window() -> list[dict]:
    """PR 7, opened 40 days before ``NOW`` and closed unmerged, holds sixty bot comments and no
    reply; PR 8, opened 30 days before, a maintainer answered the next day."""
    opened = NOW - timedelta(days=40)
    pr7 = pr_node(7, opened.isoformat(), closed=(opened + timedelta(days=20)).isoformat(),
                  comments=[comment("ci-bot", typ="Bot", assoc="NONE", body="CI report: every check passed.",
                                    created=(opened + timedelta(minutes=m)).isoformat()) for m in range(60)])
    opened8 = NOW - timedelta(days=30)
    pr8 = pr_node(8, opened8.isoformat(),
                  comments=[comment("maint", body="m" * 45, created=(opened8 + timedelta(days=1)).isoformat())])
    return [pr8, pr7]


def test_a_reply_list_longer_than_the_cap_stays_cut_and_its_silent_pr_reads_as_no_ones(monkeypatch):
    """The list is read on in rounds until it holds the cap, then keeps ``hasNextPage``; a PR
    whose recorded part holds no reply is then left out of D-GATE-5 and GR_14 (both record
    builders), closed-unread and the per-PR reply rows, never counted as unanswered."""
    from porchlamp.derive import gate_evidence

    monkeypatch.setattr(gql, "PR_REPLY_NODE_CAP", 30)
    monkeypatch.setattr(gql, "PR_REPLY_PAGE_SIZE", 5)
    t = ThreadedWindow(_silent_cut_window())
    res = gql.fetch_pr_window("acme", "widget", NOW, t, page_size=25)
    prs = list(res.prs)
    pr7 = next(pr for pr in prs if pr["number"] == 7)
    assert len(pr7["comments"]["nodes"]) == 30
    assert (pr7["comments"]["pageInfo"]["hasNextPage"], pr7["comments"]["totalCount"]) == (True, 60)
    assert t.follow_ups() == [{7: {"comments": ("20", 5)}}, {7: {"comments": ("25", 5)}}]
    assert [pr["number"] for pr in gates.reply_sampled_prs(prs)] == [8]
    assert gates.zero_response_counts(gql.ghost_rate_prs_from_window(prs), NOW) == (0, 1)
    assert [r.number for r in gate_evidence._ghost_rate_prs_from_nodes(prs)] == [8]
    assert gql.closed_unread_counts(prs) == (0, 1)
    row7 = next(row for row in gql.derive_pr_window(prs) if row["number"] == 7)
    assert (row7["first_substantive_response_at"], row7["substantive_review"]) == (None, None)


def test_derive_leaves_a_silent_cut_pr_out_of_the_ghost_rate(tmp_path, monkeypatch):
    """The derive hook's 1.4 GR_14 (the input D-GATE-5 reads) over an envelope the collector
    wrote: the silent cut PR 7 is not in the population; PR 8 was answered."""
    monkeypatch.setattr(gql, "PR_REPLY_NODE_CAP", 30)
    raw = tmp_path / "raw"
    assert gql.collect_pr_window(raw, "acme", "widget", now=NOW,
                                 transport=ThreadedWindow(_silent_cut_window())) == "ok"
    meta = derive.derive_repo("acme", "widget", derive.Ecosystem.PYPI, tmp_path, fetch=False, now=NOW)["meta"]
    assert (meta["gr14_population_n"], meta["gr14_ghost_rate"]) == (1, 0.0)
    assert (meta["pr_window_outside_pr_count"], meta["pr_window_closed_unread_count"]) == (1, 0)


def test_a_follow_up_page_that_cannot_move_a_list_on_is_not_read_again():
    t = ThreadedWindow(_threads(), stall={1})
    res = gql.fetch_pr_window("acme", "widget", NOW, t, page_size=25)
    pr1 = next(pr for pr in res.prs if pr["number"] == 1)
    assert len(pr1["comments"]["nodes"]) == 20
    assert pr1["comments"]["pageInfo"] == {"hasNextPage": True, "endCursor": "20"}
    assert t.follow_ups() == [{1: {"comments": ("20", 100)}, 2: {"reviews": ("10", 100)}}]


def test_a_follow_up_no_width_resolves_fails_the_fetch():
    t = ThreadedWindow(_threads(), refuse={1})
    with pytest.raises(RuntimeError, match=r"pr reply follow-up failed for acme/widget PRs: \[1\]"):
        gql.fetch_pr_window("acme", "widget", NOW, t, page_size=25)
    # the batch, then PR 1 alone at each narrower width
    assert [sorted(call) for call in t.follow_ups()] == [[1, 2], [1], [1]]


def test_a_record_from_the_query_before_page_info_is_never_read_on():
    """Nodes without ``pageInfo`` (the shape every envelope before RFC-0022 records) are read
    as whole: no follow-up read, and every one stays in the reply set."""
    t = ScriptedWindow([pr_node(2, _at(1), comments=[_bot_reply(m) for m in range(20)]),
                        pr_node(1, _at(0), reviews=[review(created=_at(1))])])
    res = gql.fetch_pr_window("acme", "widget", NOW, t, page_size=25)
    assert [name for name, _ in t.calls] == ["PrWindow"]
    assert [pr["number"] for pr in gql.reply_sampled_prs(res.prs)] == [2, 1]


def test_follow_ups_read_only_the_sampled_prs_of_a_sampled_window():
    """Above the cap the replies of the sample alone are recorded, so the sample alone is read on."""
    nodes = _window_nodes(130)
    for node in nodes:
        node["comments"] = {"nodes": [_bot_reply(m) for m in range(25)]}
    t = ThreadedWindow(nodes)
    res = gql.fetch_pr_window("acme", "widget", NOW, t, page_size=30, cap=50, sample_cap=20)
    sample = gql.reply_sample_numbers("acme", "widget", [n["number"] for n in nodes], 20)
    asked = {number for call in t.follow_ups() for number in call}
    assert asked == sample
    for pr in res.prs:
        if pr["number"] in sample:
            assert len(pr["comments"]["nodes"]) == 25 and not gates.replies_cut(pr)
        else:
            assert "comments" not in pr
    assert gql._fetch_reply_followups(
        "acme", "widget",
        [{**nodes[0], gql.REPLIES_RECORDED_KEY: False,
          "comments": ThreadedWindow._page([_bot_reply(m) for m in range(25)], 0, 20)}],
        t,
    ) == 0


def test_the_sample_size_counts_the_drawn_sample_whatever_its_cut_prs(tmp_path, monkeypatch):
    """``sample_size`` is the size of the drawn sample: a sampled PR whose replies stay cut is
    still in it, though no reply measure reads it."""
    monkeypatch.setattr(gql, "PR_REPLY_NODE_CAP", 20)
    nodes = _window_nodes(2010, step_minutes=5)
    cut = [_bot_reply(m) for m in range(21)]
    for node in nodes:
        node["comments"] = {"nodes": cut}
    raw = tmp_path / "raw"
    assert gql.collect_pr_window(raw, "acme", "widget", now=NOW, transport=ThreadedWindow(nodes)) == "ok"
    data = read_envelope(raw / "pr_window" / "acme__widget.json").data
    assert (data["sampled"], data["sample_size"]) == (True, gql.PR_SAMPLE_CAP)
    silent = [pr for pr in data["prs"] if pr.get(gql.REPLIES_RECORDED_KEY) is not False and not pr["reviews"]["nodes"]]
    assert all(gates.replies_cut(pr) for pr in silent) and silent
    assert len(gql.reply_sampled_prs(data["prs"])) == gql.PR_SAMPLE_CAP - len(silent)


def test_cut_pr_per_measure_guards_withhold_unsettled_4_3_4_1_gr14_and_ttfhr():
    """RFC-0022: when a cut PR stays in reply_sampled_prs because its recorded
    prefix holds a non-substantive (<80 char) review or a post-14d comment,
    downstream measures withhold it when the unrecorded suffix could reverse the
    outcome (4.3 substantive_review, 4.1 code_review_rate, 1.4 GR_14, 1.1 TTFHR)."""
    from porchlamp.collect import pike_signals
    from porchlamp.derive import gate_evidence

    short_rev = {
        "author": {"__typename": "User", "login": "maint"},
        "authorAssociation": "MEMBER",
        "state": "COMMENTED",
        "body": "Ack.",
        "comments": {"totalCount": 0},
        "createdAt": (NOW - timedelta(days=20)).isoformat(),
    }
    approved_long_rev = {
        "author": {"__typename": "User", "login": "maint"},
        "authorAssociation": "MEMBER",
        "state": "APPROVED",
        "body": (
            "This implementation handles the cursor pagination boundary cleanly "
            "and preserves backwards compatibility with legacy envelopes."
        ),
        "comments": {"totalCount": 1},
        "createdAt": (NOW - timedelta(days=39)).isoformat(),
    }
    pr_cut_unsettled = {
        **pr_node(
            1,
            (NOW - timedelta(days=40)).isoformat(),
            closed=(NOW - timedelta(days=10)).isoformat(),
            merged=(NOW - timedelta(days=10)).isoformat(),
        ),
        "reviews": {
            "nodes": [short_rev],
            "totalCount": 305,
            "pageInfo": {"hasNextPage": True, "endCursor": "c300"},
        },
        "comments": {
            "nodes": [],
            "totalCount": 305,
            "pageInfo": {"hasNextPage": True, "endCursor": "m300"},
        },
    }
    pr_cut_settled = {
        **pr_node(
            2,
            (NOW - timedelta(days=40)).isoformat(),
            closed=(NOW - timedelta(days=35)).isoformat(),
            merged=(NOW - timedelta(days=35)).isoformat(),
        ),
        "reviews": {
            "nodes": [approved_long_rev],
            "totalCount": 305,
            "pageInfo": {"hasNextPage": True, "endCursor": "c300"},
        },
        "comments": {
            "nodes": [],
            "totalCount": 0,
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        },
    }
    prs = [pr_cut_unsettled, pr_cut_settled]
    assert [p["number"] for p in gql.reply_sampled_prs(prs)] == [1, 2]

    derived = {d["number"]: d for d in gql.derive_pr_window(prs)}
    # PR 1: best_kind == "review" while comments is cut -> ttfhr_hours is None;
    # no recorded substantive review while replies are cut -> substantive_review is None.
    assert derived[1]["ttfhr_hours"] is None
    assert derived[1]["substantive_review"] is None
    # PR 2: comments is whole -> ttfhr_hours is measured; substantive review is True.
    assert derived[2]["ttfhr_hours"] == 24.0
    assert derived[2]["substantive_review"] is True

    # 4.1 code_review_rate: PR 1 (merged, cut reviews, no approval) is withheld;
    # PR 2 (merged, cut reviews, recorded approval) counts -> rate is 1.0 (1/1), not 0.5 (1/2).
    assert pike_signals.code_review_rate(prs) == 1.0

    # 1.4 GR_14 / D-GATE-5: PR 1's only response is at day 20 (>14d) and its replies are cut
    # -> withheld from population_n; PR 2 was answered at day 1 -> (0, 1).
    gr_records = gql.ghost_rate_prs_from_window(prs)
    assert [r.replies_cut for r in gr_records] == [True, True]
    assert gates.zero_response_counts(gr_records, NOW) == (0, 1)
    assert gates.zero_response_counts(gate_evidence._ghost_rate_prs_from_nodes(prs), NOW) == (0, 1)

