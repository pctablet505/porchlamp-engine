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

import subprocess
from datetime import timedelta

import pytest

from porchlamp.collect import github_graphql as gql
from porchlamp.collect.envelope import read_envelope, write_envelope
from porchlamp.core import gates
from porchlamp.derive import bundle
from tests.collect.test_pr_window import NOW, W4Transport, _w4_page, w4_days, w4_pr, w4_spr

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


def test_pr_window_exhausted_skips_author_window_calls_completely(tmp_path):
    """Q1: when pr_window already exhausted the repository's PRs (window_exhausted=True), collect_author_history
    makes 0 AuthorWindow GraphQL calls."""
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
        [],
        [],
        [],
        {"ext1": {"nodes": []}, "new1": {"nodes": []}},
        {
            u: {"createdAt": "2018-01-01T00:00:00Z", "contributionsCollection": {"contributionCalendar": {"totalContributions": 20}}}
            for u in ("ext1", "new1")
        },
        guard_count=2,
    )
    assert gql.collect_author_history(raw, "acme", "widget", now=NOW, transport=fake) == "ok"
    assert sum(1 for q, _ in fake.calls if "pullRequests(states" in q) == 0
    ah_data = read_envelope(raw / "author_history" / "acme__widget.json").data
    assert [p["number"] for p in ah_data["prs"]] == [10, 9]


def test_cycle_2_reuses_raw_stale_tail_and_refreshes_only_open_prs(tmp_path):
    """Q1 Cycle 2+: when raw-stale-*/author_history/<key>.json has an uncut 365-d window within 180d of now,
    settled [365d, 180d) PRs are reused directly and only previously-OPEN PRs in [365d, 180d) are refreshed by number."""
    raw = tmp_path / "raw"
    stale_ah = tmp_path / "raw-stale-2026-10-01" / "author_history" / "acme__widget.json"
    prev_anchor = NOW - timedelta(days=30)
    pr_settled_old = gql._shape_author_window_pr(_landing_pr(5, 260, "settled_user", state="MERGED"))
    pr_was_open_old = gql._shape_author_window_pr(_landing_pr(6, 220, "open_user", state="OPEN"))
    write_envelope(
        stale_ah,
        source="github-graphql:author-history",
        data={
            "window_anchor": prev_anchor.isoformat(),
            "window_days": 365,
            "cap": 50000,
            "count": 2,
            "truncated": False,
            "prs": [pr_was_open_old, pr_settled_old],
            "name_with_owner": "acme/widget",
            "pull_requests_total": 1000,
            "sampled_authors": [],
            "author_records": [],
        },
        fetched_at=prev_anchor.isoformat(),
    )
    pr_fresh = _landing_pr(10, 100, "fresh_user", state="MERGED")
    write_envelope(
        raw / "pr_window" / "acme__widget.json",
        source="github-graphql:pr-window",
        data={
            "window_anchor": NOW.isoformat(),
            "window_days": 180,
            "cap": 2000,
            "count": 1,
            "truncated": False,
            "prs": [pr_fresh],
            "window_end_cursor": "cursor-page-k",
            "boundary_overflow_prs": [],
            "window_exhausted": False,
        },
        fetched_at=NOW.isoformat(),
    )

    refreshed_pr6 = gql._shape_author_window_pr(_landing_pr(6, 220, "open_user", state="MERGED"))

    class StaleReuseTransport(W4Transport):
        def execute(self, query: str, variables: dict) -> dict:
            if "AuthorWindowPrsByNumber" in query:
                self.calls.append((query, dict(variables)))
                assert variables["n0"] == 6
                return {
                    "data": {
                        "rateLimit": {"cost": 1, "remaining": 4995},
                        "r": {"p0": refreshed_pr6},
                    }
                }
            return super().execute(query, variables)

    logins = ("fresh_user", "open_user", "settled_user")
    fake = StaleReuseTransport(
        [],
        [],
        [],
        {u: {"nodes": []} for u in logins},
        {
            u: {"createdAt": "2018-01-01T00:00:00Z", "contributionsCollection": {"contributionCalendar": {"totalContributions": 20}}}
            for u in logins
        },
    )
    assert gql.collect_author_history(raw, "acme", "widget", now=NOW, transport=fake) == "ok"
    assert sum(1 for q, _ in fake.calls if "pullRequests(states" in q) == 0
    assert sum(1 for q, _ in fake.calls if "AuthorWindowPrsByNumber" in q) == 1
    ah_data = read_envelope(raw / "author_history" / "acme__widget.json").data
    by_num = {p["number"]: p for p in ah_data["prs"]}
    assert sorted(by_num) == [5, 6, 10]
    assert by_num[6]["state"] == "MERGED"


# --- Q2a: Local 0-call D-IN-3 eligibility pre-filters ----------------------------------------------------------------

def test_local_eligibility_prefilter_resolves_non_users_young_accounts_and_5_pr_veterans_at_zero_cost(tmp_path):
    """Q2a: Mannequin/Organization actors, accounts < 90d old at their latest in-window PR, and User accounts
    with >= 5 PRs in the repo (window + search) and >= 90d age at their earliest in-window PR are resolved locally
    with 0 AuthorEligibilityBatch calls."""
    young_created = (NOW - timedelta(days=140)).isoformat()
    vet_created = "2018-01-01T00:00:00Z"
    prs = [
        # Non-User actor (Mannequin) -> locally ineligible
        w4_pr(1, (NOW - timedelta(days=120)).isoformat(), "mannequin1", author_typ="Mannequin", author_created=None),
        # Young account: created 140d ago, PR opened 100d ago (age at PR = 40d < 90d) -> locally ineligible
        w4_pr(2, (NOW - timedelta(days=100)).isoformat(), "young1", author_typ="User", author_created=young_created),
        # Active veteran: 5 distinct PRs in window, account created in 2018 -> locally eligible
        *[w4_pr(10 + i, (NOW - timedelta(days=110 + i)).isoformat(), "vet5", author_typ="User", author_created=vet_created)
          for i in range(5)],
        # Search veteran: 2 PRs in window + 3 older PRs in per-author search = 5 distinct PRs -> locally eligible
        w4_pr(30, (NOW - timedelta(days=115)).isoformat(), "vet_search", author_typ="User", author_created=vet_created),
        w4_pr(31, (NOW - timedelta(days=125)).isoformat(), "vet_search", author_typ="User", author_created=vet_created),
        # Ambiguous 1-PR old User -> still queried via AuthorEligibilityBatch
        w4_pr(99, (NOW - timedelta(days=130)).isoformat(), "ambig1", author_typ="User", author_created=vet_created),
    ]
    raw = tmp_path / "raw"
    fake = W4Transport(
        [_w4_page(prs, False)],
        [2],
        [4990],
        {
            **{u: {"nodes": []} for u in ("mannequin1", "young1", "vet5", "ambig1")},
            "vet_search": {
                "issueCount": 5,
                "nodes": [
                    {"number": 20 + i, "createdAt": "2024-01-01T00:00:00Z", "closedAt": "2024-01-02T00:00:00Z",
                     "mergedAt": "2024-01-02T00:00:00Z", "state": "MERGED"}
                    for i in range(3)
                ],
            },
        },
        # Only ambig1 is in users; if mannequin1, young1, vet5, or vet_search were queried, W4Transport would fail!
        {"ambig1": {"createdAt": vet_created, "contributionsCollection": {"contributionCalendar": {"totalContributions": 12}}}},
    )
    assert gql.collect_author_history(raw, "acme", "widget", now=NOW, transport=fake) == "ok"
    assert fake.eligibility_batch_sizes == [1]
    recs = {r["login"]: r for r in read_envelope(raw / "author_history" / "acme__widget.json").data["author_records"]}
    assert recs["mannequin1"]["account_created_at"] is None and recs["mannequin1"]["public_event_count"] == 0
    assert recs["young1"]["account_created_at"] == young_created and recs["young1"]["public_event_count"] == 0
    assert recs["vet5"]["account_created_at"] == vet_created and recs["vet5"]["public_event_count"] == 5
    assert recs["vet_search"]["account_created_at"] == vet_created and recs["vet_search"]["public_event_count"] == 5
    assert recs["ambig1"]["public_event_count"] == 12


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

