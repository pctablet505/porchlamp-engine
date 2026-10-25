"""Reading again the PRs a stored ``pr_window`` record cut short, instead of the whole window (RFC-0022).

The query before RFC-0022 read the first 10 reviews and 20 comments of a PR and the newest 5 timeline events, and
recorded neither a ``totalCount`` nor a ``pageInfo``: a longer thread was silently cut, and every reader took the
prefix for the whole thread. A phone cycle that was open when the engine changed kept 10,940 such records. The current
query differs from that one in exactly those sizes, so the PRs whose lists sit at the old sizes, and only those, are read
again by number and the record is stamped with the current query.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from porchlamp.collect import github_graphql as gql
from porchlamp.collect.envelope import read_envelope, write_envelope
from tests.collect.test_pr_window import NOW, ThreadedWindow, _at, _threads, comment, pr_node, review

LEGACY = gql.LEGACY_PR_WINDOW_QUERY_SHA256
CURRENT = gql.PR_WINDOW_QUERY_SHA256
FETCHED = "2026-08-01T12:00:00+00:00"


# --------------------------------------------------------------------------- #
# what "the query before RFC-0022" is                                         #
# --------------------------------------------------------------------------- #

def _legacy_query_text() -> str:
    """The current query with the three edits RFC-0022 made: the timeline read 5 events, the reply lists asked for
    neither ``totalCount`` nor ``pageInfo``."""
    page_fields = " totalCount pageInfo { hasNextPage endCursor } nodes {"
    text = gql.PR_WINDOW_QUERY.replace(f"last: {gql.PR_REPLY_PAGE_SIZE})", f"last: {gql.LEGACY_TIMELINE_LAST})")
    assert text.count(page_fields) == 2
    return text.replace(page_fields, " nodes {")


def test_the_legacy_query_hash_is_the_current_query_with_the_three_sizes_RFC_0022_changed():
    assert gql.sha256_hex(_legacy_query_text()) == LEGACY
    assert LEGACY != CURRENT


def test_the_legacy_sizes_are_the_ones_the_current_query_started_from():
    assert (gql.LEGACY_REVIEWS_FIRST, gql.LEGACY_COMMENTS_FIRST, gql.LEGACY_TIMELINE_LAST) == (10, 20, 5)
    assert (gql.PR_REVIEWS_FIRST, gql.PR_COMMENTS_FIRST) == (gql.LEGACY_REVIEWS_FIRST, gql.LEGACY_COMMENTS_FIRST)


def test_the_timeline_a_repair_reads_is_the_one_both_window_queries_read():
    for query in (gql.PR_WINDOW_QUERY, gql.PR_WINDOW_LIGHT_QUERY):
        assert gql._TIMELINE_SELECTION in query
    query = gql._pr_timeline_query(2)
    assert query.startswith("query PrTimeline(") and query.count(gql._TIMELINE_SELECTION) == 2
    assert "$n0: Int!, $n1: Int!" in query and "p1: pullRequest(number: $n1)" in query


# --------------------------------------------------------------------------- #
# which PRs may have been cut                                                 #
# --------------------------------------------------------------------------- #

def _pr(reviews=0, comments=0, timeline=None, **extra) -> dict:
    node = {"number": 7, "reviews": {"nodes": [review() for _ in range(reviews)]},
            "comments": {"nodes": [comment() for _ in range(comments)]}}
    if timeline is not None:
        node["timelineItems"] = {"nodes": [{"__typename": "ClosedEvent"} for _ in range(timeline)]}
    node.update(extra)
    return node


def _cuts(*prs, query=LEGACY):
    return gql.legacy_reply_cuts({"query_sha256": query, "prs": list(prs)})


@pytest.mark.parametrize("pr, expected", [
    (_pr(reviews=10), ([7], [])),
    (_pr(reviews=9), ([], [])),
    (_pr(comments=20), ([7], [])),
    (_pr(comments=19), ([], [])),
    (_pr(reviews=13, comments=2), ([7], [])),
    (_pr(timeline=5), ([], [7])),
    (_pr(timeline=4), ([], [])),
    (_pr(reviews=10, timeline=5), ([7], [7])),
])
def test_a_list_at_the_old_size_may_have_run_on_and_a_shorter_one_was_whole(pr, expected):
    assert _cuts(pr) == expected


def test_a_list_that_records_its_page_info_is_the_current_shape_and_is_never_cut_here():
    pr = _pr(reviews=10, comments=20)
    for field in ("reviews", "comments"):
        pr[field]["pageInfo"] = {"hasNextPage": False, "endCursor": "x"}
    assert _cuts(pr) == ([], [])


def test_a_pr_outside_the_reply_sample_holds_no_replies_to_repair_but_its_timeline_is_still_read():
    pr = _pr(reviews=10, comments=20, timeline=5, **{gql.REPLIES_RECORDED_KEY: False})
    assert _cuts(pr) == ([], [7])


@pytest.mark.parametrize("query", [CURRENT, None, "0" * 64])
def test_a_record_of_any_other_query_is_not_this_repairs_to_make(query):
    assert _cuts(_pr(reviews=10, comments=20, timeline=5), query=query) == ([], [])


@pytest.mark.parametrize("data", [None, [], "x", {"query_sha256": LEGACY}, {"query_sha256": LEGACY, "prs": "x"}])
def test_an_envelope_without_a_pr_list_has_nothing_to_repair(data):
    assert gql.legacy_reply_cuts(data) == ([], [])


def test_entries_that_are_not_pr_nodes_are_passed_over():
    assert _cuts("x", {"reviews": {"nodes": [1] * 10}}, {"number": True, "reviews": {"nodes": [1] * 10}},
                 _pr(reviews=10)) == ([7], [])


# --------------------------------------------------------------------------- #
# the repair of one stored record                                             #
# --------------------------------------------------------------------------- #

class RepairWindow(ThreadedWindow):
    """A ``ThreadedWindow`` that also answers ``PrTimeline`` from ``timelines`` (``{number: [event, ...]}``), and can
    fail the reply batch for the PRs in ``batch_fail``."""

    def __init__(self, nodes, timelines=None, batch_fail=(), **kw) -> None:
        super().__init__(nodes, **kw)
        self.timelines = timelines or {}
        self.batch_fail = set(batch_fail)

    def execute(self, query: str, variables: dict) -> dict:
        name = query.split("(", 1)[0].split()[-1]
        if name == "PrTimeline":
            self.calls.append((name, dict(variables)))
            repo = {f"p{k[1:]}": {"number": n, "timelineItems": {"nodes": self.timelines[n]}}
                    for k, n in variables.items() if k.startswith("n") and k[1:].isdigit()}
            return {"data": {"rateLimit": {"cost": 1}, "r": repo}}
        doc = super().execute(query, variables)
        if name == "PrRepliesBatch":
            for k, n in variables.items():
                if k.startswith("n") and k[1:].isdigit() and n in self.batch_fail:
                    doc["data"]["r"][f"p{k[1:]}"] = None
                    doc.setdefault("errors", []).append({"message": "boom", "path": ["r", f"p{k[1:]}"]})
        return doc

    def asked(self, name: str) -> list[int]:
        return sorted(n for call, v in self.calls if call == name
                      for k, n in v.items() if k.startswith("n") and k[1:].isdigit())


def _event(kind: str, hours: float) -> dict:
    return {"__typename": kind, "createdAt": _at(hours)}


def _legacy_node(node: dict, timeline=None) -> dict:
    """``node`` as the query before RFC-0022 recorded it."""
    out = dict(node)
    out["reviews"] = {"nodes": list(node["reviews"]["nodes"][:gql.LEGACY_REVIEWS_FIRST])}
    out["comments"] = {"nodes": list(node["comments"]["nodes"][:gql.LEGACY_COMMENTS_FIRST])}
    if timeline is not None:
        out["timelineItems"] = {"nodes": list(timeline[-gql.LEGACY_TIMELINE_LAST:])}
    return out


def _store(raw, nodes, *, timelines=None, query=LEGACY, **extra) -> dict:
    prs = [_legacy_node(n, (timelines or {}).get(n["number"])) for n in nodes]
    data = {"window_anchor": FETCHED, "query_sha256": query, "window_days": 180, "cap": gql.PR_CAP,
            "count": len(prs), "truncated": False, "cost_points": 7, "prs": prs, "derived": gql.derive_pr_window(prs)}
    data.update(extra)
    write_envelope(raw / "pr_window" / "acme__widget.json", source="github-graphql:pr-window", data=data,
                   fetched_at=FETCHED)
    return data


def _path(raw):
    return raw / "pr_window" / "acme__widget.json"


def _repair(raw, transport, now=NOW):
    return gql.repair_pr_window_replies(raw, "acme", "widget", now=now, transport=transport)


def _read(raw):
    return read_envelope(_path(raw))


def _by_number(data) -> dict:
    return {pr["number"]: pr for pr in data["prs"]}


def _derived(data) -> dict:
    return {d["number"]: d for d in data["derived"]}


def _digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_cut_threads_are_read_whole_and_a_whole_thread_is_not_asked_for_again(tmp_path):
    nodes = _threads()                        # PR 1: 25 comments, PR 2: 12 reviews, PR 3: whole
    full = {n["number"]: n for n in nodes}
    before = _store(tmp_path, nodes)
    assert [len(p["comments"]["nodes"]) for p in before["prs"]] == [1, 0, 20]
    assert [len(p["reviews"]["nodes"]) for p in before["prs"]] == [0, 10, 1]
    t = RepairWindow(nodes)

    assert _repair(tmp_path, t) == "ok"

    env = _read(tmp_path)
    got = _by_number(env.data)
    assert got[1]["comments"]["nodes"] == full[1]["comments"]["nodes"]
    assert got[2]["reviews"]["nodes"] == full[2]["reviews"]["nodes"]
    assert got[1]["comments"]["pageInfo"]["hasNextPage"] is False and got[2]["reviews"]["pageInfo"]["hasNextPage"] is False
    assert got[3] == _by_number(before)[3]                                   # a whole thread is left as it was
    assert t.asked("PrRepliesBatch") == [1, 2]                               # and never read again
    assert env.data["query_sha256"] == CURRENT


def test_the_repaired_record_judges_a_pr_by_its_whole_thread(tmp_path):
    nodes = _threads()
    before = _store(tmp_path, nodes)
    # PR 1: twenty bot comments fill the prefix, so the maintainer's reply at hour 5 was missed and the review at hour
    # 50 was the first response; PR 2: eleven bot reviews hid the maintainer's approval altogether
    assert _derived(before)[1]["ttfhr_hours"] == 50.0 and _derived(before)[2]["first_substantive_response_at"] is None

    _repair(tmp_path, RepairWindow(nodes))

    after = _derived(_read(tmp_path).data)
    assert after[1]["ttfhr_hours"] == 5.0
    assert after[2]["ttfhr_hours"] == 30.0


def test_what_the_repair_did_not_read_is_kept_and_what_it_read_is_told(tmp_path):
    nodes = _threads()
    before = _store(tmp_path, nodes, sampled=True, sample_size=3)
    t = RepairWindow(nodes)
    _repair(tmp_path, t)
    env = _read(tmp_path)
    for key in ("window_anchor", "window_days", "cap", "count", "truncated", "sampled", "sample_size"):
        assert env.data[key] == before[key]
    assert (env.fetched_at, env.source) == (FETCHED, "github-graphql:pr-window")
    spent = len(t.calls)                                                     # the fake charges a point a call
    assert env.data["replies_repaired"] == {"from_query_sha256": LEGACY, "reply_prs": 2, "timeline_prs": 0,
                                            "cost_points": spent, "repaired_at": NOW.isoformat()}
    assert env.data["cost_points"] == 7 + spent
    assert env.hash_matches_data is True


def test_a_timeline_cut_at_five_events_is_read_again_to_the_whole_hundred(tmp_path):
    ready = [_event("ReadyForReviewEvent", 1)] + [_event("ClosedEvent", 2 + k) for k in range(6)]
    nodes = [pr_node(1, _at(0), closed=_at(9), comments=[comment("maint", body="m" * 45, created=_at(3))]),
             pr_node(2, _at(1), comments=[comment("maint", body="m" * 45, created=_at(4))])]
    before = _store(tmp_path, nodes, timelines={1: ready, 2: ready[-3:]})
    assert [len(p["timelineItems"]["nodes"]) for p in before["prs"]] == [5, 3]
    t = RepairWindow(nodes, timelines={1: ready, 2: ready[-3:]})

    assert _repair(tmp_path, t) == "ok"

    got = _by_number(_read(tmp_path).data)
    assert got[1]["timelineItems"]["nodes"] == ready                         # the oldest events are back
    assert got[2]["timelineItems"]["nodes"] == ready[-3:]                    # under five events was whole
    assert t.asked("PrTimeline") == [1] and t.asked("PrRepliesBatch") == []
    assert _read(tmp_path).data["replies_repaired"]["timeline_prs"] == 1


def test_the_cut_timelines_are_read_in_batches_of_twenty_five(tmp_path):
    events = [_event("ClosedEvent", k) for k in range(6)]
    nodes = [pr_node(n, _at(0), comments=[comment("maint", body="m" * 45, created=_at(3))]) for n in range(1, 61)]
    timelines = {n: events for n in range(1, 61)}
    _store(tmp_path, nodes, timelines=timelines)
    t = RepairWindow(nodes, timelines=timelines)
    _repair(tmp_path, t)
    sizes = [sum(1 for k in v if k.startswith("n") and k[1:].isdigit()) for call, v in t.calls if call == "PrTimeline"]
    assert sizes == [25, 25, 10]


def test_a_long_thread_is_read_on_to_the_node_cap_like_a_fresh_fetch(tmp_path):
    cap = gql.PR_REPLY_NODE_CAP
    nodes = [pr_node(1, _at(0), comments=[comment("passer", assoc="NONE", body="+1", created=_at(1 + k / 100))
                                          for k in range(150)]),
             pr_node(2, _at(2), comments=[comment("passer", assoc="NONE", body="+1", created=_at(3 + k / 100))
                                          for k in range(cap + 40)])]
    _store(tmp_path, nodes)
    t = RepairWindow(nodes)
    _repair(tmp_path, t)
    got = _by_number(_read(tmp_path).data)
    assert got[1]["comments"]["nodes"] == nodes[0]["comments"]["nodes"]
    assert got[1]["comments"]["pageInfo"]["hasNextPage"] is False
    assert len(got[2]["comments"]["nodes"]) == cap                          # genuinely too busy: cut at the cap
    assert got[2]["comments"]["pageInfo"]["hasNextPage"] is True
    assert t.follow_ups()


def test_a_pr_outside_the_reply_sample_is_not_asked_for_replies(tmp_path):
    nodes = _threads()
    stored = _store(tmp_path, nodes)
    unsampled = _by_number(stored)[2]
    unsampled[gql.REPLIES_RECORDED_KEY] = False
    write_envelope(_path(tmp_path), source="x", data=stored, fetched_at=FETCHED)
    t = RepairWindow(nodes)
    _repair(tmp_path, t)
    assert t.asked("PrRepliesBatch") == [1]


@pytest.mark.parametrize("make", ["missing", "current", "other", "none", "corrupt", "whole"])
def test_a_record_with_nothing_to_repair_is_left_untouched_and_costs_nothing(tmp_path, make):
    nodes = _threads()
    t = RepairWindow(nodes)
    if make == "current":
        _store(tmp_path, nodes, query=CURRENT)
    elif make == "other":
        _store(tmp_path, nodes, query="0" * 64)
    elif make == "none":
        _store(tmp_path, nodes, query=None)
    elif make == "corrupt":
        _path(tmp_path).parent.mkdir(parents=True)
        _path(tmp_path).write_text("{not json")
    elif make == "whole":
        _store(tmp_path, [nodes[0]])                                        # a legacy record, no list at an old size
    before = _digest(_path(tmp_path)) if _path(tmp_path).exists() else None
    assert _repair(tmp_path, t) == "skip"
    assert t.calls == []
    assert (_digest(_path(tmp_path)) if _path(tmp_path).exists() else None) == before


def test_a_repaired_record_is_not_repaired_again(tmp_path):
    nodes = _threads()
    _store(tmp_path, nodes)
    assert _repair(tmp_path, RepairWindow(nodes)) == "ok"
    again = RepairWindow(nodes)
    assert _repair(tmp_path, again) == "skip" and again.calls == []


def test_a_pr_github_will_not_answer_for_fails_the_repair_and_leaves_the_old_record_whole(tmp_path):
    nodes = _threads()
    _store(tmp_path, nodes)
    before = _digest(_path(tmp_path))
    with pytest.raises(RuntimeError, match="pr replies batch failed for acme/widget"):
        _repair(tmp_path, RepairWindow(nodes, batch_fail={2}))
    assert _digest(_path(tmp_path)) == before
    assert _read(tmp_path).data["query_sha256"] == LEGACY                    # still to be tried again
    assert [p.name for p in _path(tmp_path).parent.iterdir()] == ["acme__widget.json"]


def test_a_timeline_that_comes_back_without_events_fails_the_repair(tmp_path):
    nodes = [pr_node(1, _at(0), comments=[comment("maint", body="m" * 45, created=_at(3))])]
    events = [_event("ClosedEvent", k) for k in range(6)]
    _store(tmp_path, nodes, timelines={1: events})
    before = _digest(_path(tmp_path))

    class Empty(RepairWindow):
        def execute(self, query, variables):
            doc = super().execute(query, variables)
            if query.startswith("query PrTimeline("):
                for alias in doc["data"]["r"]:
                    doc["data"]["r"][alias]["timelineItems"] = None
            return doc

    with pytest.raises(RuntimeError, match="returned no events list"):
        _repair(tmp_path, Empty(nodes, timelines={1: events}))
    assert _digest(_path(tmp_path)) == before


def test_the_cut_prs_are_read_in_batches_of_twenty_five(tmp_path):
    base = _at(0)
    nodes = [pr_node(n, base, reviews=[review(f"bot{k}", typ="Bot", assoc="NONE", created=_at(1 + k / 10))
                                       for k in range(10)]) for n in range(1, 61)]
    _store(tmp_path, nodes)
    t = RepairWindow(nodes)
    _repair(tmp_path, t)
    sizes = [sum(1 for k in v if k.startswith("n") and k[1:].isdigit()) for call, v in t.calls
             if call == "PrRepliesBatch"]
    assert sizes == [25, 25, 10]


def test_the_record_is_replaced_whole_and_leaves_no_temporary_file(tmp_path):
    nodes = _threads()
    _store(tmp_path, nodes)
    _repair(tmp_path, RepairWindow(nodes))
    assert [p.name for p in _path(tmp_path).parent.iterdir()] == ["acme__widget.json"]


def test_the_repaired_record_is_what_the_current_collector_would_have_recorded(tmp_path):
    # the repair's whole claim: a record read again equals one fetched fresh, for every PR it re-read
    nodes = _threads()
    _store(tmp_path, nodes)
    _repair(tmp_path, RepairWindow(nodes))
    repaired = _by_number(_read(tmp_path).data)
    fresh = {pr["number"]: pr for pr in gql.fetch_pr_window("acme", "widget", NOW, RepairWindow(nodes), page_size=25).prs}
    for number in (1, 2):
        for field in ("reviews", "comments", "reviewThreads"):
            assert repaired[number][field] == fresh[number][field], (number, field)


def test_without_a_clock_the_repair_stamps_the_time_it_ran(tmp_path):
    nodes = _threads()
    _store(tmp_path, nodes)
    started = datetime.now(timezone.utc).isoformat()
    gql.repair_pr_window_replies(tmp_path, "acme", "widget", transport=RepairWindow(nodes))
    assert started <= _read(tmp_path).data["replies_repaired"]["repaired_at"] <= datetime.now(timezone.utc).isoformat()
