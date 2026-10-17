"""Unit tests for porchlamp.collect.ledger (W6-3, W6-4, W6-5, RFC-0009)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from porchlamp.collect.ledger import (
    LIGHT_PR_HISTORY_QUERY,
    AuthorEligibilityEntry,
    is_cached_author_reusable,
    is_pr_settled,
    merge_commits_365d_delta,
    merge_pr_window_delta,
    merge_releases_delta,
    merge_runs_delta,
    unsettled_pr_numbers,
)

NOW = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)


# --- Settled-PR Rule (W6-3) ---


def test_is_pr_settled_merged_and_mature() -> None:
    pr = {
        "number": 101,
        "state": "MERGED",
        "createdAt": (NOW - timedelta(days=30)).isoformat(),
        "mergedAt": (NOW - timedelta(days=20)).isoformat(),
        "closedAt": (NOW - timedelta(days=20)).isoformat(),
    }
    assert is_pr_settled(pr, NOW) is True


def test_is_pr_settled_closed_and_mature() -> None:
    pr = {
        "number": 102,
        "state": "CLOSED",
        "createdAt": (NOW - timedelta(days=25)).isoformat(),
        "closedAt": (NOW - timedelta(days=15)).isoformat(),
    }
    assert is_pr_settled(pr, NOW) is True


def test_is_pr_settled_open_is_not_settled() -> None:
    pr = {
        "number": 103,
        "state": "OPEN",
        "createdAt": (NOW - timedelta(days=50)).isoformat(),
    }
    assert is_pr_settled(pr, NOW) is False


def test_is_pr_settled_younger_than_14_days() -> None:
    pr = {
        "number": 104,
        "state": "MERGED",
        "createdAt": (NOW - timedelta(days=10)).isoformat(),
        "mergedAt": (NOW - timedelta(days=5)).isoformat(),
    }
    assert is_pr_settled(pr, NOW) is False


def test_is_pr_settled_decision_after_anchor() -> None:
    pr = {
        "number": 105,
        "state": "MERGED",
        "createdAt": (NOW - timedelta(days=30)).isoformat(),
        "mergedAt": (NOW + timedelta(days=2)).isoformat(),
    }
    assert is_pr_settled(pr, NOW) is False


def test_is_pr_settled_missing_dates() -> None:
    assert is_pr_settled({"state": "MERGED"}, NOW) is False
    assert (
        is_pr_settled(
            {"state": "MERGED", "createdAt": (NOW - timedelta(days=30)).isoformat()},
            NOW,
        )
        is False
    )


def test_unsettled_pr_numbers() -> None:
    prs = [
        # Settled: merged 20d ago, created 30d ago
        {
            "number": 1,
            "state": "MERGED",
            "createdAt": (NOW - timedelta(days=30)).isoformat(),
            "mergedAt": (NOW - timedelta(days=20)).isoformat(),
        },
        # Unsettled: open
        {
            "number": 2,
            "state": "OPEN",
            "createdAt": (NOW - timedelta(days=10)).isoformat(),
        },
        # Unsettled: merged only 3d ago (under 14d maturity)
        {
            "number": 3,
            "state": "MERGED",
            "createdAt": (NOW - timedelta(days=5)).isoformat(),
            "mergedAt": (NOW - timedelta(days=3)).isoformat(),
        },
        # Out of window (created 200d ago)
        {
            "number": 4,
            "state": "OPEN",
            "createdAt": (NOW - timedelta(days=200)).isoformat(),
        },
    ]
    unsettled = unsettled_pr_numbers(prs, NOW, window_days=180)
    assert unsettled == [2, 3]


def test_merge_pr_window_delta_deduplication_and_eviction() -> None:
    existing = [
        {"number": 10, "createdAt": (NOW - timedelta(days=10)).isoformat(), "title": "old_v1"},
        {"number": 20, "createdAt": (NOW - timedelta(days=50)).isoformat(), "title": "old_v2"},
        {"number": 30, "createdAt": (NOW - timedelta(days=190)).isoformat(), "title": "too_old"},
    ]
    delta = [
        # Overwrite PR 10
        {"number": 10, "createdAt": (NOW - timedelta(days=10)).isoformat(), "title": "updated_v1"},
        # New PR 40
        {"number": 40, "createdAt": (NOW - timedelta(days=5)).isoformat(), "title": "new_pr"},
    ]

    merged = merge_pr_window_delta(existing, delta, now=NOW, window_days=180, cap=5)
    numbers = [p["number"] for p in merged]
    assert numbers == [40, 10, 20]
    # Check that PR 10 was updated
    pr_10 = next(p for p in merged if p["number"] == 10)
    assert pr_10["title"] == "updated_v1"
    # Check that PR 30 (> 180d) was evicted
    assert 30 not in numbers


def test_merge_pr_window_delta_cap() -> None:
    prs = [
        {"number": i, "createdAt": (NOW - timedelta(days=i)).isoformat()}
        for i in range(1, 10)
    ]
    merged = merge_pr_window_delta(prs, [], now=NOW, cap=3)
    assert len(merged) == 3
    assert [p["number"] for p in merged] == [1, 2, 3]


# --- Commits, Releases & Runs Delta Ledgers (W6-4) ---


def test_merge_commits_365d_delta() -> None:
    existing = [
        {"sha": "c1", "date": (NOW - timedelta(days=10)).isoformat()},
        {"sha": "c2", "date": (NOW - timedelta(days=400)).isoformat()},  # should evict
    ]
    new_commits = [
        {"sha": "c1", "date": (NOW - timedelta(days=10)).isoformat(), "author_login": "alice"},
        {"sha": "c3", "date": (NOW - timedelta(days=2)).isoformat()},
    ]
    merged = merge_commits_365d_delta(existing, new_commits, now=NOW, window_days=365)
    shas = [c["sha"] for c in merged]
    assert shas == ["c3", "c1"]
    assert merged[1].get("author_login") == "alice"


def test_merge_commits_365d_delta_fallback_key() -> None:
    existing = [
        {"date": (NOW - timedelta(days=5)).isoformat(), "author_login": "alice", "msg": "first"},
    ]
    new_commits = [
        {"date": (NOW - timedelta(days=5)).isoformat(), "author_login": "alice", "msg": "updated"},
        {"date": (NOW - timedelta(days=2)).isoformat(), "author_login": "bob"},
    ]
    merged = merge_commits_365d_delta(existing, new_commits, now=NOW)
    assert len(merged) == 2
    assert merged[0]["author_login"] == "bob"
    assert merged[1]["author_login"] == "alice"
    assert merged[1]["msg"] == "updated"


def test_merge_releases_delta() -> None:
    existing = [
        {"id": 1, "tag_name": "v1.0.0", "published_at": (NOW - timedelta(days=30)).isoformat()},
        {"id": 2, "tag_name": "v1.1.0", "published_at": (NOW - timedelta(days=15)).isoformat()},
    ]
    new_releases = [
        {"id": 2, "tag_name": "v1.1.0", "published_at": (NOW - timedelta(days=15)).isoformat(), "name": "Patch"},
        {"id": 3, "tag_name": "v2.0.0", "published_at": (NOW - timedelta(days=1)).isoformat()},
    ]
    merged = merge_releases_delta(existing, new_releases, cap=2)
    assert len(merged) == 2
    assert [r["tag_name"] for r in merged] == ["v2.0.0", "v1.1.0"]
    assert merged[1].get("name") == "Patch"


def test_merge_runs_delta() -> None:
    existing = [
        {"id": 100, "created_at": (NOW - timedelta(days=10)).isoformat()},
        {"id": 101, "created_at": (NOW - timedelta(days=100)).isoformat()},  # > 90d evicted
    ]
    new_runs = [
        {"id": 100, "created_at": (NOW - timedelta(days=10)).isoformat(), "status": "completed"},
        {"id": 102, "created_at": (NOW - timedelta(days=1)).isoformat()},
    ]
    merged = merge_runs_delta(existing, new_runs, now=NOW, window_days=90)
    ids = [r["id"] for r in merged]
    assert ids == [102, 100]
    assert merged[1]["status"] == "completed"


# --- Light PR History & Author Eligibility (W6-5, RFC-0009) ---


def test_light_pr_history_query_does_not_contain_heavy_connections() -> None:
    query = LIGHT_PR_HISTORY_QUERY
    assert "reviews" not in query
    assert "comments" not in query
    assert "reviewThreads" not in query
    assert "pullRequests" in query
    assert "number" in query
    assert "createdAt" in query
    assert "mergedAt" in query
    assert "closedAt" in query
    assert "authorAssociation" in query


def test_is_cached_author_reusable() -> None:
    entry = AuthorEligibilityEntry(
        login="alice",
        account_created_at="2020-01-01T00:00:00Z",
        prior_commits=10,
        checked_at="2026-01-01T00:00:00Z",
    )
    # PR created in 2026: account is > 90 days old and prior_commits >= 5
    assert is_cached_author_reusable(entry, "2026-06-01T00:00:00Z") is True

    # Account created too recently relative to PR creation (< 90 days)
    recent_entry = AuthorEligibilityEntry(
        login="bob",
        account_created_at="2026-05-15T00:00:00Z",
        prior_commits=10,
        checked_at="2026-05-20T00:00:00Z",
    )
    assert is_cached_author_reusable(recent_entry, "2026-06-01T00:00:00Z") is False

    # Account with insufficient prior commits (< 5)
    few_commits_entry = AuthorEligibilityEntry(
        login="charlie",
        account_created_at="2020-01-01T00:00:00Z",
        prior_commits=3,
        checked_at="2026-01-01T00:00:00Z",
    )
    assert is_cached_author_reusable(few_commits_entry, "2026-06-01T00:00:00Z") is False

    # Missing or invalid account_created_at
    none_entry = AuthorEligibilityEntry(
        login="dave",
        account_created_at=None,
        prior_commits=10,
        checked_at="2026-01-01T00:00:00Z",
    )
    assert is_cached_author_reusable(none_entry, "2026-06-01T00:00:00Z") is False


def test_collect_pr_window_incremental_ledger_reuses_settled_prs(tmp_path) -> None:
    from porchlamp.collect import envelope as env_mod
    from porchlamp.collect import github_graphql as gql

    now = datetime(2026, 4, 15, 0, 0, 0, tzinfo=timezone.utc)
    settled_pr = {
        "number": 100,
        "createdAt": "2026-02-01T00:00:00Z",
        "mergedAt": "2026-02-02T00:00:00Z",
        "closedAt": "2026-02-02T00:00:00Z",
        "state": "MERGED",
        "isDraft": False,
        "authorAssociation": "NONE",
        "author": {"__typename": "User", "login": "alice"},
        "reviews": {"totalCount": 1, "nodes": []},
        "comments": {"totalCount": 0, "nodes": []},
        "reviewThreads": {"totalCount": 0, "nodes": []},
    }
    target = tmp_path / "pr_window" / "org__repo.json"
    env_mod.write_envelope(
        target,
        source="graphql:pr_window(org/repo)",
        fetched_at="2026-04-01T00:00:00Z",
        data={
            "window_anchor": "2026-04-01T00:00:00Z",
            "window_days": 90,
            "cap": 500,
            "count": 1,
            "truncated": False,
            "cost_points": 1,
            "rate_limit_remaining": 4999,
            "prs": [settled_pr],
            "query_sha256": gql.PR_WINDOW_QUERY_SHA256,
        },
    )

    new_pr = {
        "number": 101,
        "createdAt": "2026-04-10T00:00:00Z",
        "mergedAt": None,
        "closedAt": None,
        "state": "OPEN",
        "isDraft": False,
        "authorAssociation": "NONE",
        "author": {"__typename": "User", "login": "bob"},
        "reviews": {"totalCount": 0, "nodes": []},
        "comments": {"totalCount": 0, "nodes": []},
        "reviewThreads": {"totalCount": 0, "nodes": []},
    }
    calls: list[dict] = []

    class FakeTransport:
        def execute(self, query: str, variables: dict):
            calls.append(dict(variables))
            # Page 1 returns new_pr and settled_pr; pagination stops immediately
            # even though hasNextPage is True because settled_pr (100) is already settled!
            return {
                "data": {
                    "rateLimit": {"cost": 1, "remaining": 4998},
                    "r": {
                        "pullRequests": {
                            "pageInfo": {"hasNextPage": True, "endCursor": "cursor-2"},
                            "nodes": [new_pr, settled_pr],
                        }
                    },
                }
            }

    res = gql.collect_pr_window(
        tmp_path, "org", "repo", now=now, transport=FakeTransport(),
        force=True, incremental_ledger=True,
    )
    assert res == "ok"
    assert len(calls) == 1
    out = env_mod.read_envelope(target)
    nums = [p["number"] for p in out.data["prs"]]
    assert nums == [101, 100]


def test_collect_author_history_light_mode_avoids_search_and_reuses_settled_eligibility(tmp_path) -> None:
    from porchlamp.collect import envelope as env_mod
    from porchlamp.collect import github_graphql as gql

    now = datetime(2026, 4, 15, 0, 0, 0, tzinfo=timezone.utc)
    target = tmp_path / "author_history" / "org__repo.json"
    env_mod.write_envelope(
        target,
        source="graphql:author_history(org/repo)",
        fetched_at="2026-04-01T00:00:00Z",
        data={
            "window_anchor": "2026-04-01T00:00:00Z",
            "author_records": [
                {
                    "login": "alice",
                    "account_created_at": "2020-01-01T00:00:00Z",
                    "public_event_count": 12,
                    "eligibility_unmeasured": False,
                    "eligibility_observed_at": "2026-04-01T00:00:00Z",
                    "prs": [],
                }
            ],
        },
    )

    queries: list[str] = []

    class FakeTransport:
        def execute(self, query: str, variables: dict):
            queries.append(query)
            if "AuthorWindow" in query:
                return {
                    "data": {
                        "rateLimit": {"cost": 1, "remaining": 4990},
                        "r": {
                            "pullRequests": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [
                                    {
                                        "number": 10,
                                        "createdAt": "2025-12-01T00:00:00Z",
                                        "mergedAt": "2025-12-02T00:00:00Z",
                                        "closedAt": "2025-12-02T00:00:00Z",
                                        "state": "MERGED",
                                        "isDraft": False,
                                        "authorAssociation": "FIRST_TIME_CONTRIBUTOR",
                                        "author": {"__typename": "User", "login": "alice"},
                                    }
                                ],
                            }
                        },
                    }
                }
            if "LightPrHistory" in query:
                return {
                    "data": {
                        "rateLimit": {"cost": 1, "remaining": 4989},
                        "repository": {
                            "pullRequests": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [
                                    {
                                        "number": 10,
                                        "createdAt": "2025-12-01T00:00:00Z",
                                        "mergedAt": "2025-12-02T00:00:00Z",
                                        "closedAt": "2025-12-02T00:00:00Z",
                                        "state": "MERGED",
                                        "authorAssociation": "FIRST_TIME_CONTRIBUTOR",
                                        "author": {"__typename": "User", "login": "alice", "createdAt": "2020-01-01T00:00:00Z"},
                                    }
                                ],
                            }
                        },
                    }
                }
            raise AssertionError(f"Unexpected query: {query}")

    res = gql.collect_author_history(
        tmp_path, "org", "repo", now=now, transport=FakeTransport(),
        force=True, light_history=True,
    )
    assert res == "ok"
    assert any("LightPrHistory" in q for q in queries)
    assert not any("search(" in q for q in queries)
    out = env_mod.read_envelope(target)
    records = out.data["author_records"]
    assert len(records) == 1
    assert records[0]["login"] == "alice"
    assert records[0]["account_created_at"] == "2020-01-01T00:00:00Z"
    assert len(records[0]["prs"]) == 1

