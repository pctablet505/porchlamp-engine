"""Subject: what the contributor-history step (L4, ``collect_author_history``) costs, and that none of the
savings moves a score (engine issue #89, phase 1).

* A page, search batch or eligibility batch that fails because the network did (DNS, reset, abort, timeout) is read
  again after a wait, not the whole repository from its first page: on the phone six of the busiest projects lost up
  to 2 h 36 min each to one dropped connection.
* D-IN-3 eligibility is looked up only for authors a score reads: the 1.2 DR_90 band of the stored ``pr_window``
  record, the per-author search sample and the newcomer sample. Every other cohort author keeps a record that says it
  was not looked up. The scored values (1.2, 1.3, 5.2, 5.3) are the same as with every lookup.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pytest

from porchlamp.collect import github_graphql as gql
from porchlamp.collect.envelope import read_envelope, write_envelope
from porchlamp.core import gates, landing
from porchlamp.derive import bundle
from tests.collect.test_pr_window import NOW, W4Transport, _w4_page, w4_days, w4_spr

NETWORK_DROP = RuntimeError("gh api graphql rc=1: gh: [Errno 103] Software caused connection abort")
NO_DNS = RuntimeError("gh api graphql rc=1: gh: <urlopen error [Errno 7] No address associated with hostname>")
BAD_CREDENTIALS = RuntimeError("gh api graphql rc=1: HTTP 401: Bad credentials")


def _timeout() -> subprocess.TimeoutExpired:
    return subprocess.TimeoutExpired(["gh", "api", "graphql"], 90)


class Flaky:
    """Wraps a transport: the first ``skip`` calls whose query contains ``marker`` pass, the next ones raise the
    queued ``errors`` one by one, then calls pass again. ``attempts`` holds the variables of every marked call."""

    def __init__(self, inner, marker: str, errors, skip: int = 0) -> None:
        self.inner, self.marker, self.errors, self.skip = inner, marker, list(errors), skip
        self.attempts: list[dict] = []

    def execute(self, query: str, variables: dict) -> dict:
        if self.marker in query:
            self.attempts.append(dict(variables))
            if self.skip:
                self.skip -= 1
            elif self.errors:
                raise self.errors.pop(0)
        return self.inner.execute(query, variables)


@pytest.fixture
def slept(monkeypatch) -> list[float]:
    """No real waiting: the waits are zero and the sleeps are recorded."""
    waits: list[float] = []
    monkeypatch.setattr(gql, "NETWORK_RETRY_WAITS_S", (0, 0, 0))
    monkeypatch.setattr(gql, "_wait", waits.append)
    return waits


def _two_page_window() -> W4Transport:
    p1 = _w4_page([w4_days(1, 10, "a"), w4_days(2, 11, "b")], True, "c1")
    p2 = _w4_page([w4_days(3, 12, "c")], False)
    return W4Transport([p1, p2], [2, 2], [4990, 4980], {}, {})


# --- a network failure is waited out on the same page ----------------------------------------------------------------

def test_a_window_page_that_fails_on_the_network_is_read_again_from_the_same_cursor(slept):
    flaky = Flaky(_two_page_window(), "pullRequests(states", [NETWORK_DROP, NO_DNS], skip=1)
    window = gql.fetch_author_window("acme", "widget", NOW, flaky)
    assert [pr["number"] for pr in window.prs] == [1, 2, 3]
    assert [a.get("after") for a in flaky.attempts] == [None, "c1", "c1", "c1"]
    assert slept == [0, 0]


def test_a_failure_that_is_not_the_network_is_not_waited_for(slept):
    flaky = Flaky(_two_page_window(), "pullRequests(states", [BAD_CREDENTIALS], skip=1)
    with pytest.raises(RuntimeError, match="Bad credentials"):
        gql.fetch_author_window("acme", "widget", NOW, flaky)
    assert len(flaky.attempts) == 2 and slept == []


def test_a_network_failure_that_does_not_clear_fails_after_its_waits(slept):
    flaky = Flaky(_two_page_window(), "pullRequests(states", [NETWORK_DROP] * 10, skip=1)
    with pytest.raises(RuntimeError, match="connection abort"):
        gql.fetch_author_window("acme", "widget", NOW, flaky)
    assert len(flaky.attempts) == 1 + 1 + len(gql.NETWORK_RETRY_WAITS_S)
    assert slept == [0, 0, 0]


def test_the_waits_are_minutes_and_stay_under_the_phones_stuck_watchdog():
    """A wait that outlasts the 25-minute watchdog would get the collector restarted mid-wait."""
    assert gql.NETWORK_RETRY_WAITS_S == tuple(sorted(gql.NETWORK_RETRY_WAITS_S))
    assert 10 * 60 <= sum(gql.NETWORK_RETRY_WAITS_S) <= 20 * 60


def _repo_transport(**kw) -> W4Transport:
    p1 = _w4_page([w4_days(1, 120, "ext1"), w4_days(2, 300, "new1")], False)
    searches = {"ext1": {"nodes": [w4_spr(1, (NOW - timedelta(days=120)).isoformat())]}, "new1": {"nodes": []}}
    users = {name: {"createdAt": "2015-01-01T00:00:00Z",
                    "contributionsCollection": {"contributionCalendar": {"totalContributions": 30}}}
             for name in ("ext1", "new1")}
    return W4Transport([p1], [2], [4990], searches, users, **kw)


def test_a_search_batch_that_times_out_is_read_again(tmp_path, slept):
    flaky = Flaky(_repo_transport(), "AuthorPrsSearchBatch", [_timeout()])
    assert gql.collect_author_history(tmp_path / "raw", "acme", "widget", now=NOW, transport=flaky) == "ok"
    records = read_envelope(tmp_path / "raw" / "author_history" / "acme__widget.json").data["author_records"]
    assert [r["history_unmeasured"] for r in records] == [False, False]
    assert len(flaky.attempts) == 2 and slept == [0]


def test_a_search_batch_that_never_answers_marks_its_authors_unmeasured_not_the_repo_failed(tmp_path, slept):
    flaky = Flaky(_repo_transport(), "AuthorPrsSearchBatch", [_timeout()] * 99)
    assert gql.collect_author_history(tmp_path / "raw", "acme", "widget", now=NOW, transport=flaky) == "ok"
    records = read_envelope(tmp_path / "raw" / "author_history" / "acme__widget.json").data["author_records"]
    assert [r["history_unmeasured"] for r in records] == [True, True]


def test_an_eligibility_batch_that_times_out_is_read_again_at_the_same_width(tmp_path, slept):
    flaky = Flaky(_repo_transport(), "AuthorEligibilityBatch", [_timeout()])
    assert gql.collect_author_history(tmp_path / "raw", "acme", "widget", now=NOW, transport=flaky) == "ok"
    records = read_envelope(tmp_path / "raw" / "author_history" / "acme__widget.json").data["author_records"]
    assert [r["eligibility_unmeasured"] for r in records] == [False, False]
    assert len(flaky.attempts) == 2 and slept == [0]


def test_an_eligibility_batch_that_never_answers_marks_its_authors_unmeasured(tmp_path, slept):
    flaky = Flaky(_repo_transport(), "AuthorEligibilityBatch", [NO_DNS] * 99)
    assert gql.collect_author_history(tmp_path / "raw", "acme", "widget", now=NOW, transport=flaky) == "ok"
    records = read_envelope(tmp_path / "raw" / "author_history" / "acme__widget.json").data["author_records"]
    assert [r["eligibility_unmeasured"] for r in records] == [True, True]


# --- eligibility is looked up where a score reads it -----------------------------------------------------------------

OLD_ONLY = [f"o{i:02d}" for i in range(60)]      # PRs 200-259 d old: in the history's decision cohort, not in 1.2's band
BAND = ["b0", "b1", "b2"]                          # PRs 100-120 d old: in 1.2's band
GHOST = "ghost"                                    # in the stored pr_window band only, never in the history window


def _window_prs() -> list[dict]:
    prs = [w4_days(500 + i, 100 + 10 * i, login) for i, login in enumerate(BAND)]
    prs += [w4_days(600 + i, 200 + i, login) for i, login in enumerate(OLD_ONLY)]
    prs.append(w4_days(700, 300, "n0"))            # a newcomer: first PR in [365 d, 180 d]
    return sorted(prs, key=lambda pr: pr["createdAt"], reverse=True)


def _pr_window_prs() -> list[dict]:
    """The stored 180-day record: the band authors' PRs and the ghost's (no ghost PR is in the history window)."""
    return [w4_days(500 + i, 100 + 10 * i, login) for i, login in enumerate(BAND)] + [w4_days(800, 130, GHOST)]


def _write_pr_window(raw, prs, *, truncated: bool = False, anchor=NOW) -> None:
    write_envelope(raw / "pr_window" / "acme__widget.json", source="github-graphql:pr-window",
                   data={"window_anchor": anchor.isoformat(), "window_days": 180, "cap": 2000, "count": len(prs),
                         "truncated": truncated, "prs": prs}, fetched_at=anchor.isoformat())


def _expected_sets(prs: list[dict], band_prs: list[dict]):
    sample = gql._select_newcomer_sample(prs, NOW, False)
    decision = gql._decision_cohort_logins(prs, NOW)
    others = sorted(set(decision) - set(sample))
    search = set(sample) | set(gql._seeded_sample(others, gql.AUTHOR_SEARCH_CAP - len(sample)))
    band = set(gates.dr90_band_logins(band_prs, NOW))
    needed = set(decision) | set(sample)
    return needed, search, band, set(sample)


def _transport(prs, logins_looked_up, logins_searched) -> W4Transport:
    user = {"createdAt": "2015-01-01T00:00:00Z",
            "contributionsCollection": {"contributionCalendar": {"totalContributions": 30}}}
    return W4Transport([_w4_page(prs, False)], [2], [4990],
                       {login: {"nodes": []} for login in logins_searched},
                       {login: user for login in logins_looked_up})


def test_the_fixture_has_authors_a_score_never_reads():
    prs = _window_prs()
    needed, search, band, newcomers = _expected_sets(prs, _pr_window_prs())
    assert needed - (band | search | newcomers), "the rule under test needs at least one author nobody reads"
    assert GHOST in band and GHOST not in needed
    assert len(newcomers) == gql.NEWCOMER_SAMPLE_SIZE


def test_eligibility_is_looked_up_only_for_the_band_the_search_sample_and_the_newcomers(tmp_path):
    prs, raw = _window_prs(), tmp_path / "raw"
    _write_pr_window(raw, _pr_window_prs())
    needed, search, band, newcomers = _expected_sets(prs, _pr_window_prs())
    lookup = needed & (band | search | newcomers)
    skipped = needed - lookup
    fake = _transport(prs, lookup, search)          # an unrecorded user is a failure: a lookup of a skipped author fails
    assert gql.collect_author_history(raw, "acme", "widget", now=NOW, transport=fake) == "ok"
    data = read_envelope(raw / "author_history" / "acme__widget.json").data
    records = {r["login"]: r for r in data["author_records"]}
    assert set(records) == needed and GHOST not in records       # the ghost is a band author, never a record
    assert data["dr90_band_anchor"] == NOW.isoformat()
    assert sum(fake.eligibility_batch_sizes) == len(lookup)
    for login in skipped:
        assert records[login] == {
            "login": login, "account_created_at": None, "public_event_count": None, "prs": [],
            "history_unmeasured": False, "history_unsampled": True, "eligibility_unmeasured": False,
            "eligibility_looked_up": False, "eligibility_observed_at": None}
    for login in lookup:
        assert records[login]["account_created_at"] == "2015-01-01T00:00:00Z"
        assert "eligibility_looked_up" not in records[login]


def test_the_scored_values_are_the_same_as_with_every_lookup(tmp_path):
    prs = _window_prs()
    needed, search, band, newcomers = _expected_sets(prs, _pr_window_prs())
    lookup = needed & (band | search | newcomers)
    narrow, full = tmp_path / "narrow", tmp_path / "full"
    _write_pr_window(narrow, _pr_window_prs())
    for root, looked_up in ((narrow, lookup), (full, needed)):
        fake = _transport(prs, looked_up, search)
        assert gql.collect_author_history(root, "acme", "widget", now=NOW, transport=fake) == "ok"

    def envelope(root):
        return read_envelope(root / "author_history" / "acme__widget.json").data

    a, b = gql.derive_cohort_fields(envelope(narrow)), gql.derive_cohort_fields(envelope(full))
    for field in ("mr_first_rate", "newcomer_conversion", "rr90_return_rate"):
        assert a[field] == b[field], field
    pr_window = read_envelope(narrow / "pr_window" / "acme__widget.json").data
    anchor = bundle._parse_iso_or_none(pr_window["window_anchor"])
    by_login = lambda data: {r["login"]: r for r in data["author_records"]}   # noqa: E731
    assert (bundle._dr90_decision_cohort(pr_window, anchor, by_login(envelope(narrow)))
            == bundle._dr90_decision_cohort(pr_window, anchor, by_login(envelope(full))))


def test_without_a_stored_pr_window_every_cohort_author_is_looked_up(tmp_path):
    prs, raw = _window_prs(), tmp_path / "raw"
    needed, search, _band, _new = _expected_sets(prs, _pr_window_prs())
    fake = _transport(prs, needed, search)
    assert gql.collect_author_history(raw, "acme", "widget", now=NOW, transport=fake) == "ok"
    data = read_envelope(raw / "author_history" / "acme__widget.json").data
    assert "dr90_band_anchor" not in data
    assert all("eligibility_looked_up" not in r and r["account_created_at"] for r in data["author_records"])


def test_a_pr_window_that_stopped_short_does_not_narrow_the_lookups(tmp_path):
    prs, raw = _window_prs(), tmp_path / "raw"
    _write_pr_window(raw, [w4_days(900, 5, "recent")], truncated=True)
    needed, search, _band, _new = _expected_sets(prs, _pr_window_prs())
    fake = _transport(prs, needed, search)
    assert gql.collect_author_history(raw, "acme", "widget", now=NOW, transport=fake) == "ok"
    data = read_envelope(raw / "author_history" / "acme__widget.json").data
    assert "dr90_band_anchor" not in data and len(fake.eligibility_batch_sizes) == -(-len(needed) // gql.ELIGIBILITY_BATCH_SIZE)


# --- the band: one definition, shared with 1.2 -----------------------------------------------------------------------

def test_the_band_logins_are_the_authors_of_the_prs_1_2_counts():
    """[anchor-180 d, anchor-90 d), outside PRs only (not members, not bots), the login lower-cased; the same PRs
    ``bundle._dr90_decision_cohort`` puts in the cohort: given a record for exactly these logins it keeps every
    one of their PRs, so an author the helper missed would be dropped there."""
    def outside(number, days_ago, login, assoc="NONE", typ="User"):
        pr = w4_days(number, days_ago, login, assoc)
        pr["author"]["__typename"] = typ
        return pr

    prs = [outside(1, 180, "Edge-Old"), outside(2, 90, "edge-new"), outside(3, 91, "In-Band"),
           outside(4, 150, "member", assoc="MEMBER"), outside(5, 150, "robot[bot]", typ="Bot"),
           outside(6, 181, "too-old"), outside(7, 120, "Other-One"),
           {"number": 8, "createdAt": (NOW - timedelta(days=120)).isoformat(), "authorAssociation": "NONE",
            "author": None}]
    logins = gates.dr90_band_logins(prs, NOW)
    assert logins == frozenset({"edge-old", "in-band", "other-one"})
    records = {login: {"login": login, "account_created_at": "2015-01-01T00:00:00Z", "public_event_count": 50}
               for login in logins}
    _decided, kept, entries, _malformed, _gate = bundle._dr90_decision_cohort({"prs": prs}, NOW, records)
    assert (kept, entries) == (3, 3)    # the PR with no author has no record: the gate drops it, the three authors stay


# --- RFC-0028: Q11 (DR90 > 500 sampling to 300), Q9 (newcomer reserve 35 -> 20), Q2b (two-tier eligibility) ----------


def test_dr90_band_logins_caps_above_500_to_deterministic_300_and_cohort_agrees():
    """RFC-0028 (Q11): <= 500 distinct DR_90 band authors are kept in full; > 500 are deterministically sampled
    to 300 using PR_SAMPLE_SEED, and bundle._dr90_decision_cohort evaluates the exact same 300-author sample."""
    prs_500 = [w4_days(1000 + i, 120, f"u{i:04d}", closed_after=5) for i in range(500)]
    assert len(gates.dr90_band_logins(prs_500, NOW)) == 500

    prs_600 = [w4_days(2000 + i, 120, f"u{i:04d}", closed_after=5) for i in range(600)]
    sampled = gates.dr90_band_logins(prs_600, NOW)
    assert len(sampled) == gates.DR90_AUTHOR_SAMPLE_CAP == 300
    assert sampled == gates.dr90_band_logins(list(reversed(prs_600)), NOW)

    records = {login: {"login": login, "account_created_at": "2015-01-01T00:00:00Z", "public_event_count": 50}
               for login in sampled}
    decided, kept, entries, malformed, gate = bundle._dr90_decision_cohort({"prs": prs_600}, NOW, records)
    assert (decided, kept, entries, malformed, gate) == (300, 300, 300, 0, True)


def test_newcomer_pre_filters_young_and_non_user_and_promotes_reserve_up_to_20(tmp_path):
    """RFC-0028 (Q9): _select_newcomer_sample excludes <90d accounts and non-User actors at 0 GraphQL cost,
    and collect_author_history searches up to 15 reserve candidates (NEWCOMER_CANDIDATE_CAP = 35) when primary
    candidates have prior PRs before anchor - 365 d, retaining 20 confirmed newcomers."""
    young_pr = w4_days(1, 200, "young_acc")
    young_pr["author"]["createdAt"] = (NOW - timedelta(days=250)).isoformat()  # 50d old at PR (< 90d)
    org_pr = w4_days(2, 200, "some_org")
    org_pr["author"]["__typename"] = "Organization"
    valid_pr = w4_days(3, 200, "valid_newcomer")
    assert gql._select_newcomer_sample([young_pr, org_pr, valid_pr], NOW, truncated=False) == ("valid_newcomer",)

    # 35 eligible newcomer-band candidates: 8 of the primary 20 have a prior PR at NOW - 400 d (repeat contributors).
    prs = [w4_days(100 + i, 220, f"nc{i:02d}", merged_after=5) for i in range(35)]
    primary, reserve = gql._select_newcomer_candidates(prs, NOW, truncated=False)
    assert len(primary) == 20 and len(reserve) == 15
    disqualified = set(primary[:8])

    searches = {}
    for login in primary + reserve:
        pr_num = int(login[2:]) + 100
        in_win = w4_spr(pr_num, (NOW - timedelta(days=220)).isoformat(), merged=(NOW - timedelta(days=215)).isoformat())
        if login in disqualified:
            older = w4_spr(pr_num + 900, (NOW - timedelta(days=400)).isoformat(), merged=(NOW - timedelta(days=395)).isoformat())
            searches[login] = {"nodes": [older, in_win]}
        else:
            searches[login] = {"nodes": [in_win]}
    users = {
        login: {"createdAt": "2015-01-01T00:00:00Z",
                "contributionsCollection": {"contributionCalendar": {"totalContributions": 25}}}
        for login in primary + reserve
    }
    fake = W4Transport([_w4_page(prs, False)], [2], [4990], searches, users)
    raw = tmp_path / "raw"
    assert gql.collect_author_history(raw, "acme", "widget", now=NOW, transport=fake) == "ok"
    data = read_envelope(raw / "author_history" / "acme__widget.json").data
    assert len(data["sampled_authors"]) == 20
    assert not (set(data["sampled_authors"]) & disqualified)
    fields = gql.derive_cohort_fields(data)
    assert fields["newcomer_cohort_author_count"] == (20, "derived")


def test_two_tier_eligibility_settles_active_at_tier1_and_escalates_ambiguous_to_bounded_tier2():
    """RFC-0028 (Q2b): Tier 1 batches 50 logins with earliest 5 PRs/issues and settles accounts with >= 5 prior
    events (or < 90d age at cutoff); only ambiguous accounts (< 5 prior PR/issue events and >= 90d age) escalate
    to Tier 2 scalar contributionsCollection(from:, to:) bounded to the PR creation timestamp."""
    cutoff = NOW - timedelta(days=100)
    early_ts = (NOW - timedelta(days=300)).isoformat()

    class TwoTierTransport:
        def __init__(self) -> None:
            self.queries: list[tuple[str, dict]] = []

        def execute(self, query: str, variables: dict) -> dict:
            self.queries.append((query, dict(variables)))
            if "AuthorEligibilityTier1Batch" in query:
                return {
                    "data": {
                        "rateLimit": {"cost": 1, "remaining": 4999},
                        "u0": {
                            "createdAt": "2018-01-01T00:00:00Z",
                            "pullRequests": {"totalCount": 3, "nodes": [{"createdAt": early_ts}] * 3},
                            "issues": {"totalCount": 2, "nodes": [{"createdAt": early_ts}] * 2},
                        },
                        "u1": {
                            "createdAt": "2018-01-01T00:00:00Z",
                            "pullRequests": {"totalCount": 1, "nodes": [{"createdAt": early_ts}]},
                            "issues": {"totalCount": 0, "nodes": []},
                        },
                        "u2": {
                            "createdAt": (cutoff - timedelta(days=30)).isoformat(),  # < 90d at cutoff
                            "pullRequests": {"totalCount": 0, "nodes": []},
                            "issues": {"totalCount": 0, "nodes": []},
                        },
                    }
                }
            assert "AuthorEligibilityTier2Batch" in query
            assert "contributionCalendar" not in query
            assert variables["l0"] == "commit_only"
            assert variables["to0"] == cutoff.isoformat()
            return {
                "data": {
                    "rateLimit": {"cost": 1, "remaining": 4998},
                    "u0": {
                        "createdAt": "2018-01-01T00:00:00Z",
                        "contributionsCollection": {
                            "totalCommitContributions": 12,
                            "totalIssueContributions": 0,
                            "totalPullRequestContributions": 1,
                            "totalPullRequestReviewContributions": 2,
                            "restrictedContributionsCount": 0,
                        },
                    },
                }
            }

    t = TwoTierTransport()
    out, cost, rem = gql.fetch_two_tier_eligibility_batch(
        ["active_pr_author", "commit_only", "young_author"],
        t,
        cutoff_by_login={
            "active_pr_author": cutoff,
            "commit_only": cutoff,
            "young_author": cutoff,
        },
    )
    assert len(t.queries) == 2
    assert out["active_pr_author"]["public_event_count"] == 5
    assert out["commit_only"]["public_event_count"] == 15
    assert out["young_author"]["public_event_count"] == 0
    assert cost == 2 and rem == 4998


# --- Q1: L1 (pr_window) -> L4 (author_history) window reuse & Cycle 2+ raw-stale reuse ------------------------------

def _landing_pr(number: int, days_ago: int, login: str, *, state: str = "MERGED") -> dict:
    created = (NOW - timedelta(days=days_ago)).isoformat()
    closed = (NOW - timedelta(days=max(0, days_ago - 1))).isoformat() if state != "OPEN" else None
    merged = closed if state == "MERGED" else None
    return {
        "number": number,
        "createdAt": created,
        "closedAt": closed,
        "mergedAt": merged,
        "state": state,
        "author": {"__typename": "User", "login": login, "createdAt": "2018-01-01T00:00:00Z"},
        "authorAssociation": "NONE",
        "labels": {"nodes": []},
        "timelineItems": {"nodes": []},
        "reviews": {"totalCount": 0, "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []},
        "comments": {"totalCount": 0, "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []},
        "reviewThreads": {"totalCount": 0},
    }


def test_pr_window_seeds_180d_prs_and_resumes_author_window_at_cursor(tmp_path):
    """Q1: when collect_pr_window records window_end_cursor and boundary_overflow_prs, collect_author_history
    reuses the [180d, 0d] PRs + overflow PRs from pr_window and starts AuthorWindow pagination at window_end_cursor."""
    raw = tmp_path / "raw"
    pr_recent = _landing_pr(10, 120, "ext1")
    pr_overflow = gql._shape_author_window_pr(_landing_pr(9, 190, "OverflowUser"))
    write_envelope(
        raw / "pr_window" / "acme__widget.json",
        source="github-graphql:pr-window",
        data={
            "window_anchor": NOW.isoformat(),
            "window_days": 180,
            "cap": 2000,
            "count": 1,
            "truncated": False,
            "prs": [pr_recent],
            "window_end_cursor": "cursor-page-k",
            "boundary_overflow_prs": [pr_overflow],
            "window_exhausted": False,
        },
        fetched_at=NOW.isoformat(),
    )
    pr_older = _landing_pr(8, 250, "new1")
    # Only the continuation page after cursor-page-k is served by the transport!
    fake = W4Transport(
        [_w4_page([gql._shape_author_window_pr(pr_older)], False)],
        [2],
        [4990],
        {"ext1": {"nodes": []}, "overflowuser": {"nodes": []}, "new1": {"nodes": []}},
        {
            u: {"createdAt": "2018-01-01T00:00:00Z", "contributionsCollection": {"contributionCalendar": {"totalContributions": 20}}}
            for u in ("ext1", "overflowuser", "new1")
        },
    )
    assert gql.collect_author_history(raw, "acme", "widget", now=NOW, transport=fake) == "ok"
    window_calls = [v for q, v in fake.calls if "pullRequests(states" in q]
    assert len(window_calls) == 1
    assert window_calls[0].get("after") == "cursor-page-k"
    ah_data = read_envelope(raw / "author_history" / "acme__widget.json").data
    assert [p["number"] for p in ah_data["prs"]] == [10, 9, 8]
    assert all("reviews" not in p and "comments" not in p for p in ah_data["prs"])


def test_pr_window_exhausted_walks_no_page(tmp_path):
    """Q1: when pr_window already exhausted the repository's PRs (window_exhausted=True), collect_author_history
    walks no page: one PR's worth of the query for the repository's name and total."""
    raw = tmp_path / "raw"
    pr_recent = _landing_pr(10, 120, "ext1")
    pr_overflow = gql._shape_author_window_pr(_landing_pr(9, 240, "new1"))
    write_envelope(
        raw / "pr_window" / "acme__widget.json",
        source="github-graphql:pr-window",
        data={
            "window_anchor": NOW.isoformat(),
            "window_days": 180,
            "cap": 2000,
            "count": 1,
            "truncated": False,
            "prs": [pr_recent],
            "window_end_cursor": None,
            "boundary_overflow_prs": [pr_overflow],
            "window_exhausted": True,
        },
        fetched_at=NOW.isoformat(),
    )
    fake = W4Transport(
        [_w4_page([], False)],
        [1],
        [4990],
        {"ext1": {"nodes": []}, "new1": {"nodes": []}},
        {
            u: {"createdAt": "2018-01-01T00:00:00Z", "contributionsCollection": {"contributionCalendar": {"totalContributions": 20}}}
            for u in ("ext1", "new1")
        },
        guard_count=2,
    )
    assert gql.collect_author_history(raw, "acme", "widget", now=NOW, transport=fake) == "ok"
    assert [v.get("first") for q, v in fake.calls if "pullRequests(states" in q] == [1]
    ah_data = read_envelope(raw / "author_history" / "acme__widget.json").data
    assert [p["number"] for p in ah_data["prs"]] == [10, 9]


# --- Q5: Append-only 5-page checkpointing and resume -----------------------------------------------------------------

def test_author_window_checkpoint_flushes_every_5_pages_and_resumes_on_retry(tmp_path):
    """Q5: fetch_author_window appends a JSONL delta every 5 pages, resumes from page 6 after an interruption,
    and collect_author_history unlinks the checkpoint file once the envelope is written."""
    pages = [
        _w4_page([w4_days(i + 1, 100 + i, f"u{i}")], i < 6, f"c{i + 1}" if i < 6 else "")
        for i in range(7)
    ]
    ckpt_path = tmp_path / "state" / "partial" / "author_history" / "acme__widget.pages.jsonl"

    class FailOnPage6:
        def __init__(self) -> None:
            self.calls: list[Optional[str]] = []

        def execute(self, query: str, variables: dict) -> dict:
            after = variables.get("after")
            self.calls.append(after)
            idx = 0 if after is None else int(str(after)[1:])
            if idx == 5:
                raise RuntimeError("gh api graphql rc=1: HTTP 401: interrupted on page 6")
            return {
                "data": {
                    "rateLimit": {"cost": 2, "remaining": 4900 - idx},
                    "r": {
                        "nameWithOwner": "acme/widget",
                        "total": {"totalCount": 7},
                        "pullRequests": pages[idx],
                    },
                }
            }

    failing = FailOnPage6()
    with pytest.raises(RuntimeError, match="interrupted on page 6"):
        gql.fetch_author_window("acme", "widget", NOW, failing, checkpoint_path=ckpt_path)
    assert ckpt_path.is_file()
    assert failing.calls == [None, "c1", "c2", "c3", "c4", "c5"]

    # Now resume from ckpt_path: pages 1..5 are loaded from the checkpoint and pagination starts at "c5" (page 6)!
    resumed_transport = W4Transport(pages[5:], [2, 2], [4890, 4888], {}, {})
    res = gql.fetch_author_window("acme", "widget", NOW, resumed_transport, checkpoint_path=ckpt_path)
    resumed_afters = [v.get("after") for q, v in resumed_transport.calls if "pullRequests(states" in q]
    assert resumed_afters == ["c5", "c6"]
    assert [p["number"] for p in res.prs] == [1, 2, 3, 4, 5, 6, 7]
    assert res.cost_points == 14
    assert res.name_with_owner == "acme/widget" and res.pull_requests_total == 7



# --- #89 Q1 and Q5: the same PR list by two roads, on a scripted GitHub --------------------------------------------

class ListGitHub:
    """One repository's PR list, newest first, cursors ``c<offset>``, as L1 and L4 both read it. ``refused``: GitHub
    will not read the first stored cursor it is given (its words, measured 2026-11-02). ``raise_at``: one failure at that offset.
    ``heavy_at``: one 502 for a full page at that offset."""

    def __init__(self, nodes, *, name_with_owner=None, refused=False, raise_at=None, heavy_at=None):
        self.nodes = nodes
        self.name_with_owner = name_with_owner
        self.refused = refused
        self.raise_at = raise_at
        self.heavy_at = heavy_at
        self.pages: list[tuple[str, Optional[str], int]] = []
        self.search_queries: list[str] = []

    def execute(self, query: str, variables: dict) -> dict:
        kind = query.split("(")[0].replace("query ", "").strip()
        if kind in ("PrWindow", "PrWindowLight", "AuthorWindow"):
            after = variables.get("after")
            if kind == "AuthorWindow" and after and self.refused:
                self.refused = False    # the stored cursor; a walk's own fresh cursors are read
                return {"errors": [{"message": "The cursor does not appear to be a valid cursor."}], "data": None}
            start = int(after[1:]) if after else 0
            first = variables["first"]
            if kind == "AuthorWindow" and self.raise_at is not None and start >= self.raise_at:
                self.raise_at = None
                raise RuntimeError("gh api graphql rc=1: HTTP 401: interrupted")
            if self.heavy_at == start and first == gql.PAGE_SIZE:
                self.heavy_at = None
                raise RuntimeError("gh api graphql rc=1: HTTP 502: Bad Gateway")
            self.pages.append((kind, after, first))
            chunk = self.nodes[start:start + first]
            end = start + len(chunk)
            empty = {"totalCount": 0, "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}
            extra = {"isDraft": False, "mergedBy": None}
            if kind == "PrWindow":
                extra.update(reviews=empty, comments=empty, reviewThreads={"totalCount": 0})
            repo: dict = {"pullRequests": {
                "pageInfo": {"hasNextPage": end < len(self.nodes), "endCursor": f"c{end}"},
                "nodes": [dict(n) if kind == "AuthorWindow" else {**n, **extra} for n in chunk]}}
            if kind == "AuthorWindow":
                repo["nameWithOwner"] = self.name_with_owner or f"{variables['owner']}/{variables['name']}"
                repo["total"] = {"totalCount": len(self.nodes)}
            return {"data": {"rateLimit": {"cost": 2, "remaining": 4000}, "r": repo}}
        if kind == "PrRepliesBatch":
            empty = {"totalCount": 0, "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}
            aliases = {f"p{i}": {"number": variables[f"n{i}"], "reviews": empty, "comments": empty,
                                 "reviewThreads": {"totalCount": 0}}
                       for i in range(sum(1 for k in variables if re.fullmatch(r"n\d+", k)))}
            return {"data": {"rateLimit": {"cost": 1}, "r": aliases}}
        if kind == "AuthorPrsSearchBatch":
            data: dict = {"rateLimit": {"cost": 1, "remaining": 4000}}
            real = self.name_with_owner
            for i in range(sum(1 for k in variables if k.startswith("q"))):
                q = variables[f"q{i}"]
                self.search_queries.append(q)
                found = real is None or q.startswith(f"repo:{real} ")
                data[f"s{i}"] = ({"issueCount": len(self.nodes) if found else 0} if " author:" not in q
                                 else {"issueCount": 1 if found else 0, "nodes": []})
            return {"data": data}
        if kind == "AuthorEligibilityBatch":
            data = {"rateLimit": {"cost": 1, "remaining": 4000}}
            for i in range(len(variables)):
                data[f"u{i}"] = {"createdAt": "2018-01-01T00:00:00Z",
                                 "contributionsCollection": {"contributionCalendar": {"totalContributions": 9}}}
            return {"data": data}
        raise AssertionError(f"unscripted query {kind}")


def _listed(count: int, hours: float = 12.0, *, closer: bool = False) -> list[dict]:
    """``count`` PRs, one every ``hours``, newest first, by forty outside authors, each with the landing fields."""
    nodes = []
    for j in range(count):
        created = NOW - timedelta(days=1) - timedelta(hours=hours * j)
        closed = (created + timedelta(days=1)).isoformat()
        event = {"__typename": "ClosedEvent", "createdAt": closed, "actor": {"__typename": "User", "login": "m"},
                 "closer": {"__typename": "Commit", "oid": "abc"} if closer else None}
        nodes.append({
            "number": 100000 - j, "createdAt": created.isoformat(), "closedAt": closed, "mergedAt": None, "state": "CLOSED",
            "author": {"__typename": "User", "login": f"u{j % 40}", "createdAt": "2018-01-01T00:00:00Z"},
            "authorAssociation": "NONE", "labels": {"nodes": []},
            "timelineItems": {"nodes": [{"__typename": "ReadyForReviewEvent", "createdAt": created.isoformat()}, event]}})
    return nodes


def _walk_nodes(nodes: list[dict]) -> list[dict]:
    """What ``AUTHOR_WINDOW_QUERY`` holds of each node: the timeline's latest close event, not the ready-for-review."""
    out = []
    for node in nodes:
        events = [e for e in node["timelineItems"]["nodes"] if e["__typename"] == "ClosedEvent"]
        out.append({**node, "timelineItems": {"nodes": events[-1:]}})
    return out


def _collect_both(tmp_path, nodes, **github):
    """The history a seeded walk records, then the one a walk from the first page records, at one instant."""
    raw = tmp_path / "seeded" / "raw"
    assert gql.collect_pr_window(raw, "acme", "widget", now=NOW, transport=ListGitHub(nodes, **github)) == "ok"
    seeded = ListGitHub(_walk_nodes(nodes), **github)
    assert gql.collect_author_history(raw, "acme", "widget", now=NOW, transport=seeded) == "ok"
    whole_raw = tmp_path / "whole" / "raw"
    shutil.copytree(raw / "pr_window", whole_raw / "pr_window")
    env = whole_raw / "pr_window" / "acme__widget.json"
    envelope = read_envelope(env)
    data = {k: v for k, v in envelope.data.items()
            if k not in ("window_end_cursor", "boundary_overflow_prs", "window_exhausted")}
    write_envelope(env, source=envelope.source, data=data, fetched_at=envelope.fetched_at)
    whole = ListGitHub(_walk_nodes(nodes), **github)
    assert gql.collect_author_history(whole_raw, "acme", "widget", now=NOW, transport=whole) == "ok"
    return (read_envelope(raw / "author_history" / "acme__widget.json").data, seeded,
            read_envelope(whole_raw / "author_history" / "acme__widget.json").data, whole)


def _masked_history(data: dict) -> dict:
    return {k: v for k, v in data.items() if k not in ("cost_points", "rate_limit_remaining", "reused_pr_window_anchor")}


@pytest.mark.parametrize("count, hours, older", [
    (150, 12.0, "older than a year"),   # the 180-d line falls mid page
    (800, 12.0, "older than a year"),   # 360 PRs in the window: several pages
    (720, 6.0, "older than a year"),    # the line falls on the first PR of a page
    (725, 6.0, "older than a year"),
    (60, 12.0, "the list ends"),        # the list ends inside the window's last page
    (359, 12.0, "the list ends"),       # the list ends exactly with a page
    (400, 12.0, "the list ends"),
])
def test_a_seeded_history_equals_a_walk_from_the_first_page(tmp_path, count, hours, older):
    """#89 Q1: L1's PRs, the rest of L1's last page and the walk from L1's cursor are the same record, in the same order,
    as a walk from page 1: no PR lost, none twice, every field equal."""
    nodes = _listed(count, hours)
    if older == "older than a year":
        nodes = nodes + [
            {**_listed(1)[0], "number": 1000 - j, "createdAt": (NOW - timedelta(days=370 + j)).isoformat()} for j in range(150)]
    seeded, seeded_github, whole, whole_github = _collect_both(tmp_path, nodes)
    assert _masked_history(seeded) == _masked_history(whole)
    assert [p["number"] for p in seeded["prs"]] == [p["number"] for p in whole["prs"]]
    assert len({p["number"] for p in seeded["prs"]}) == len(seeded["prs"])
    assert sum(1 for k, _a, _f in seeded_github.pages if k == "AuthorWindow") <= sum(
        1 for k, _a, _f in whole_github.pages if k == "AuthorWindow")


def test_a_seeded_history_equals_the_walk_when_the_crossing_page_needs_the_ladder(tmp_path):
    """#89 Q1 with #95: a 502 on the page that crosses 180 d is read again smaller; the resume point is that smaller
    page's, and the seeded record is still the whole one."""
    nodes = _listed(900)
    raw = tmp_path / "seeded" / "raw"
    assert gql.collect_pr_window(raw, "acme", "widget", now=NOW, transport=ListGitHub(nodes, heavy_at=300)) == "ok"
    prw = read_envelope(raw / "pr_window" / "acme__widget.json").data
    assert prw["window_end_cursor"] == "c400"                   # page boundaries do not move under the ladder (#96)
    assert [p["number"] for p in prw["boundary_overflow_prs"]] == [n["number"] for n in nodes[prw["count"]:400]]
    assert gql.collect_author_history(raw, "acme", "widget", now=NOW, transport=ListGitHub(_walk_nodes(nodes))) == "ok"
    seeded = read_envelope(raw / "author_history" / "acme__widget.json").data
    want = [n["number"] for n in nodes if gql._parse_iso(n["createdAt"]) >= NOW - timedelta(days=365)]
    assert [p["number"] for p in seeded["prs"]] == want


def test_l1_records_where_its_walk_stopped_on_the_crossing_page():
    """#89 Q1: the page that crosses 180 d gives its end cursor, and its PRs older than 180 d in L4's shape."""
    nodes = _listed(500)
    result = gql.fetch_pr_window("acme", "widget", NOW, ListGitHub(nodes))
    in_window = [n for n in nodes if gql._parse_iso(n["createdAt"]) >= NOW - timedelta(days=180)]
    assert [p["number"] for p in result.prs] == [n["number"] for n in in_window]
    assert result.window_end_cursor == "c400" and result.window_exhausted is False
    assert [p["number"] for p in result.boundary_overflow_prs] == [n["number"] for n in nodes[len(in_window):400]]
    assert all("reviews" not in p and "isDraft" not in p for p in result.boundary_overflow_prs)


def test_l1_says_the_list_ended_when_no_page_is_left():
    """#89 Q1: no cursor, and ``window_exhausted``, both when the window holds the whole list and when the list ends on
    the crossing page."""
    whole = gql.fetch_pr_window("acme", "widget", NOW, ListGitHub(_listed(50)))
    assert whole.window_exhausted is True and whole.window_end_cursor is None and whole.boundary_overflow_prs == ()
    crossing = gql.fetch_pr_window("acme", "widget", NOW, ListGitHub(_listed(380)))
    assert crossing.window_exhausted is True and crossing.window_end_cursor is None
    assert len(crossing.boundary_overflow_prs) == 380 - len(crossing.prs) > 0


def test_l1_records_where_its_light_walk_stopped():
    """#89 Q1: a window over the full-field cap goes on with the light query; the resume point is that walk's."""
    nodes = _listed(1200, 2.0)        # 12 PRs a day: 2,160 in 180 d would not fit 1,200: the list ends
    capped = gql.fetch_pr_window("acme", "widget", NOW, ListGitHub(nodes), cap=200, sample_cap=100)
    assert capped.sampled is True and capped.window_exhausted is True
    longer = _listed(3000, 2.0)
    capped = gql.fetch_pr_window("acme", "widget", NOW, ListGitHub(longer), cap=200, sample_cap=100)
    in_window = sum(1 for n in longer if gql._parse_iso(n["createdAt"]) >= NOW - timedelta(days=180))
    assert capped.sampled is True and len(capped.prs) == in_window
    assert capped.window_end_cursor == f"c{(in_window // 100 + 1) * 100}" and capped.window_exhausted is False
    assert len(capped.boundary_overflow_prs) == (in_window // 100 + 1) * 100 - in_window


def _cursor_envelope(raw: Path, nodes: list[dict], at: datetime) -> dict:
    """The ``pr_window`` record of ``nodes`` collected at ``at``."""
    assert gql.collect_pr_window(raw, "acme", "widget", now=at, transport=ListGitHub(nodes)) == "ok"
    return read_envelope(raw / "pr_window" / "acme__widget.json").data


@pytest.mark.parametrize("age, seeded", [(timedelta(days=14), True), (timedelta(days=14, seconds=1), False)])
def test_an_l1_record_is_reused_up_to_14_days(tmp_path, age, seeded):
    """#89 Q1: at most :data:`constants.WINDOW_STALENESS_DAYS` of read shift; older walks from page 1."""
    nodes = _listed(900)
    raw = tmp_path / "raw"
    _cursor_envelope(raw, nodes, NOW)
    github = ListGitHub(_walk_nodes(nodes))
    assert gql.collect_author_history(raw, "acme", "widget", now=NOW + age, transport=github) == "ok"
    first_after = next(a for k, a, _f in github.pages if k == "AuthorWindow")
    assert (first_after is not None) is seeded


def test_an_l1_record_without_landing_fields_is_not_reused(tmp_path):
    """#89 Q1: a PR without the landing fields would read GitHub's merges alone: the whole window is walked."""
    nodes = _listed(900)
    raw = tmp_path / "raw"
    data = _cursor_envelope(raw, nodes, NOW)
    for pr in data["prs"]:
        pr.pop("labels"), pr.pop("timelineItems")
    write_envelope(raw / "pr_window" / "acme__widget.json", source="github-graphql:pr-window", data=data,
                   fetched_at=NOW.isoformat())
    github = ListGitHub(_walk_nodes(nodes))
    assert gql.collect_author_history(raw, "acme", "widget", now=NOW, transport=github) == "ok"
    assert next(a for k, a, _f in github.pages if k == "AuthorWindow") is None


def test_a_seeded_pr_keeps_its_latest_close_event(tmp_path):
    """#89 Q1 with RFC-0015: a PR landed by a commit reads landed from L1's node as from the walk's."""
    nodes = _listed(900, closer=True)
    seeded, _s, whole, _w = _collect_both(tmp_path, nodes)
    recent = [p for p in seeded["prs"] if gql._parse_iso(p["createdAt"]) >= NOW - timedelta(days=180)]
    assert recent and all(landing.is_landed(p) for p in recent)
    assert seeded["prs"] == whole["prs"]


def test_an_exhausted_window_still_reads_the_repository_own_name_and_total(tmp_path):
    """#89 Q1 with D3-G (live 2026-11-02, BraveOPotato/FckSignups, moved to NoSignups: 12 of 12 authors unmeasured): the
    searches are asked under the repository's own name, from one PR's worth of the window query."""
    nodes = _listed(380)    # 190 days: the list ends on the page that crosses 180 d, and the cohorts hold authors
    seeded, github, whole, _w = _collect_both(tmp_path, nodes, name_with_owner="newowner/newname")
    assert [(k, a, f) for k, a, f in github.pages if k == "AuthorWindow"] == [("AuthorWindow", None, 1)]
    assert all(q.startswith("repo:newowner/newname ") for q in github.search_queries) and github.search_queries
    assert seeded["name_with_owner"] == "newowner/newname" and seeded["pull_requests_total"] == 380
    assert not any(r["history_unmeasured"] for r in seeded["author_records"])
    assert _masked_history(seeded) == _masked_history(whole)


def test_a_refused_cursor_costs_a_full_walk_not_the_project(tmp_path):
    """#89 Q1: a cursor's format is GitHub's to change. One it will not read means the whole window, as with no cursor."""
    nodes = _listed(900)
    raw = tmp_path / "raw"
    _cursor_envelope(raw, nodes, NOW)
    github = ListGitHub(_walk_nodes(nodes), refused=True)
    assert gql.collect_author_history(raw, "acme", "widget", now=NOW, transport=github) == "ok"
    want = [n["number"] for n in nodes if gql._parse_iso(n["createdAt"]) >= NOW - timedelta(days=365)]
    assert [p["number"] for p in read_envelope(raw / "author_history" / "acme__widget.json").data["prs"]] == want


def test_the_off_switch_walks_from_the_first_page(tmp_path, monkeypatch):
    """#89 Q1: with the phone's ``l4_reuse_l1`` off the record is the whole walk's, cursor or no cursor; on, it reuses."""
    nodes = _listed(900)
    raw = tmp_path / "raw"
    _cursor_envelope(raw, nodes, NOW)
    monkeypatch.setenv(gql.L4_REUSE_L1_ENV, "0")
    off = ListGitHub(_walk_nodes(nodes))
    assert gql.collect_author_history(raw, "acme", "widget", now=NOW, transport=off) == "ok"
    assert next(a for k, a, _f in off.pages if k == "AuthorWindow") is None
    (raw / "author_history" / "acme__widget.json").unlink()
    monkeypatch.setenv(gql.L4_REUSE_L1_ENV, "1")
    on = ListGitHub(_walk_nodes(nodes))
    assert gql.collect_author_history(raw, "acme", "widget", now=NOW, transport=on) == "ok"
    assert next(a for k, a, _f in on.pages if k == "AuthorWindow") is not None


# --- #89 Q5: failure paths of the page checkpoint ----------------------------------------------------------------------

def _ckpt(root: Path) -> Path:
    return root / "state" / "partial" / "author_history" / "acme__widget.pages.jsonl"


@pytest.mark.parametrize("how", ["switch off", "record older than 14 d"])
def test_the_checkpoint_of_a_seeded_walk_is_dropped_when_its_seed_is_gone(tmp_path, monkeypatch, how):
    """#89 Q1 x Q5: a seeded walk's lines hold only PRs below its cursor. Resumed without the seed they would be a record
    with no recent PR at all, and ``truncated`` false."""
    nodes = _listed(2000, 4.0)
    root = tmp_path / "r"
    raw = root / "raw"
    _cursor_envelope(raw, nodes, NOW)
    first_at = NOW + timedelta(days=13, hours=23)
    with pytest.raises(RuntimeError, match="interrupted"):
        gql.collect_author_history(raw, "acme", "widget", now=first_at, transport=ListGitHub(_walk_nodes(nodes), raise_at=1700))
    assert _ckpt(root).is_file()
    second_at = first_at
    if how == "switch off":
        monkeypatch.setenv(gql.L4_REUSE_L1_ENV, "0")
    else:
        second_at = NOW + timedelta(days=14, hours=1)
    assert gql.collect_author_history(raw, "acme", "widget", now=second_at, transport=ListGitHub(_walk_nodes(nodes))) == "ok"
    data = read_envelope(raw / "author_history" / "acme__widget.json").data
    want = [n["number"] for n in nodes if second_at - timedelta(days=365) <= gql._parse_iso(n["createdAt"]) <= second_at]
    assert [p["number"] for p in data["prs"]] == want


def test_a_checkpoint_that_could_not_be_written_loses_no_pr(tmp_path, monkeypatch):
    """#89 Q5: a full disk at one flush keeps that flush's PRs for the next line: no line skips any."""
    nodes = _listed(2600, 4.0)
    path = tmp_path / "state" / "partial" / "author_history" / "acme__widget.pages.jsonl"
    real_open = Path.open
    failures = [OSError(28, "No space left on device")]

    def full_once(self, *args, **kwargs):
        if self == path and failures:
            raise failures.pop()
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", full_once)
    with pytest.raises(RuntimeError, match="interrupted"):
        gql.fetch_author_window("acme", "widget", NOW, ListGitHub(_walk_nodes(nodes), raise_at=1200), checkpoint_path=path)
    assert not failures and path.is_file()
    res = gql.fetch_author_window("acme", "widget", NOW, ListGitHub(_walk_nodes(nodes)), checkpoint_path=path)
    want = [n["number"] for n in nodes if gql._parse_iso(n["createdAt"]) >= NOW - timedelta(days=365)]
    assert [p["number"] for p in res.prs] == want


@pytest.mark.parametrize("content", [
    b'{"window_anchor":"2026-09-12T00:00:00+00:00"\xff\xfe\n',
    b'{"window_anchor":"2026-09-12T00:00:00+00:00","start_after":null,"after":"c500","cost_delta":"x","prs":[]}\n',
    b'[1, 2, 3]\n',
    b'\x00\x00\x00\x00',
])
def test_an_unreadable_checkpoint_is_dropped_and_the_walk_starts_over(tmp_path, content):
    """#89 Q5: no checkpoint may fail its project: that would fail every run, and for good, as only success removes it."""
    nodes = _listed(300)
    root = tmp_path / "r"
    _ckpt(root).parent.mkdir(parents=True)
    _ckpt(root).write_bytes(content)
    assert gql.collect_author_history(root / "raw", "acme", "widget", now=NOW, transport=ListGitHub(_walk_nodes(nodes))) == "ok"
    assert not _ckpt(root).exists()
    assert read_envelope(root / "raw" / "author_history" / "acme__widget.json").data["count"] == 300


@pytest.mark.parametrize("torn_bytes", [1000, 1])    # mid line; or a whole record that lost only its newline
def test_a_torn_last_line_is_cut_so_the_next_flush_counts(tmp_path, torn_bytes):
    """#89 Q5: a line torn by a crash is dropped and cut away; a later flush, appended after it, is read again."""
    nodes = _listed(2600, 4.0)
    path = tmp_path / "ckpt.jsonl"
    with pytest.raises(RuntimeError, match="interrupted"):
        gql.fetch_author_window("acme", "widget", NOW, ListGitHub(_walk_nodes(nodes), raise_at=1200), checkpoint_path=path)
    blob = path.read_bytes()
    assert blob.count(b"\n") == 2
    path.write_bytes(blob[:-torn_bytes])
    second = ListGitHub(_walk_nodes(nodes), raise_at=1700)
    with pytest.raises(RuntimeError, match="interrupted"):
        gql.fetch_author_window("acme", "widget", NOW, second, checkpoint_path=path)
    assert second.pages[0][1] == "c500"
    third = ListGitHub(_walk_nodes(nodes))
    res = gql.fetch_author_window("acme", "widget", NOW, third, checkpoint_path=path)
    assert third.pages[0][1] == "c1500"
    want = [n["number"] for n in nodes if gql._parse_iso(n["createdAt"]) >= NOW - timedelta(days=365)]
    assert [p["number"] for p in res.prs] == want


def test_collect_removes_the_checkpoint_once_the_envelope_is_written(tmp_path):
    """#89 Q5: a crash leaves the file, the next success removes it."""
    nodes = _listed(2600, 4.0)
    root = tmp_path / "r"
    with pytest.raises(RuntimeError, match="interrupted"):
        gql.collect_author_history(root / "raw", "acme", "widget", now=NOW, transport=ListGitHub(_walk_nodes(nodes), raise_at=1200))
    assert _ckpt(root).is_file()
    assert gql.collect_author_history(root / "raw", "acme", "widget", now=NOW, transport=ListGitHub(_walk_nodes(nodes))) == "ok"
    assert not _ckpt(root).exists()


def test_a_checkpoint_older_than_14_days_is_dropped(tmp_path):
    """#89 Q5: pages read more than 14 days ago are not joined to today's."""
    nodes = _listed(2600, 4.0)
    path = tmp_path / "ckpt.jsonl"
    with pytest.raises(RuntimeError, match="interrupted"):
        gql.fetch_author_window("acme", "widget", NOW, ListGitHub(_walk_nodes(nodes), raise_at=1200), checkpoint_path=path)
    later = ListGitHub(_walk_nodes(nodes))
    gql.fetch_author_window("acme", "widget", NOW + timedelta(days=14, seconds=1), later, checkpoint_path=path)
    assert later.pages[0][1] is None
    assert json.loads(path.read_text().splitlines()[0])["window_anchor"] == (NOW + timedelta(days=14, seconds=1)).isoformat()


def test_each_checkpoint_line_holds_only_its_own_prs(tmp_path):
    """#89 Q5: a line is a delta: five pages' PRs, none repeated."""
    nodes = _listed(2600, 4.0)
    path = tmp_path / "ckpt.jsonl"
    with pytest.raises(RuntimeError, match="interrupted"):
        gql.fetch_author_window("acme", "widget", NOW, ListGitHub(_walk_nodes(nodes), raise_at=1200), checkpoint_path=path)
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [len(line["prs"]) for line in lines] == [500, 500]
    assert not {p["number"] for p in lines[0]["prs"]} & {p["number"] for p in lines[1]["prs"]}


def test_a_seeded_record_names_the_pr_window_it_reused(tmp_path, monkeypatch):
    """#89 Q1: a record says when its newest PRs were read; a record walked from page 1, with the switch off or after a
    refused cursor, does not."""
    nodes = _listed(900)
    raw = tmp_path / "raw"
    anchor = _cursor_envelope(raw, nodes, NOW)["window_anchor"]
    target = raw / "author_history" / "acme__widget.json"
    later = NOW + timedelta(days=9)
    assert gql.collect_author_history(raw, "acme", "widget", now=later, transport=ListGitHub(_walk_nodes(nodes))) == "ok"
    assert read_envelope(target).data["reused_pr_window_anchor"] == anchor
    assert read_envelope(target).data["window_anchor"] == later.isoformat()
    target.unlink()
    github = ListGitHub(_walk_nodes(nodes), refused=True)
    assert gql.collect_author_history(raw, "acme", "widget", now=later, transport=github) == "ok"
    assert "reused_pr_window_anchor" not in read_envelope(target).data
    target.unlink()
    monkeypatch.setenv(gql.L4_REUSE_L1_ENV, "0")
    assert gql.collect_author_history(raw, "acme", "widget", now=later, transport=ListGitHub(_walk_nodes(nodes))) == "ok"
    assert "reused_pr_window_anchor" not in read_envelope(target).data
