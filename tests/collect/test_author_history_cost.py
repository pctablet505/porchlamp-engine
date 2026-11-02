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

