"""Tests for 180-day vitality commit derivation from commits_365d (W6-2)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from porchlamp.collect.commits import (
    can_derive_180d_from_365d,
    derive_commits_180d_rows,
)

NOW = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)


def test_derive_commits_180d_rows_filters_and_projects() -> None:
    now = NOW
    inside_1 = (now - timedelta(days=10)).isoformat()
    inside_2 = (now - timedelta(days=100)).isoformat()
    outside = (now - timedelta(days=200)).isoformat()
    future = (now + timedelta(days=5)).isoformat()

    c365_data = {
        "commits": [
            {"author_login": "alice", "author_type": "User", "date": inside_1, "extra": 123},
            {"author_login": "bob", "author_type": "Bot", "date": inside_2, "extra": 456},
            {"author_login": "charlie", "author_type": "User", "date": outside, "extra": 789},
            {"author_login": "david", "author_type": "User", "date": future, "extra": 999},
            {"author_login": "eve", "author_type": "User", "date": "invalid-date"},
        ],
        "truncated": False,
    }

    rows = derive_commits_180d_rows(c365_data, now)
    assert len(rows) == 2
    assert rows[0] == {"author_login": "alice", "author_type": "User", "date": inside_1}
    assert rows[1] == {"author_login": "bob", "author_type": "Bot", "date": inside_2}


def test_derive_commits_180d_rows_caps_at_300() -> None:
    now = NOW
    commits = [
        {
            "author_login": f"user{i}",
            "author_type": "User",
            "date": (now - timedelta(minutes=i)).isoformat(),
        }
        for i in range(400)
    ]
    rows = derive_commits_180d_rows({"commits": commits}, now)
    assert len(rows) == 300
    assert rows[0]["author_login"] == "user0"
    assert rows[-1]["author_login"] == "user299"


def test_derive_commits_180d_rows_accepts_rows_key() -> None:
    now = NOW
    date_str = (now - timedelta(days=5)).isoformat()
    c365_data = {
        "rows": [{"author_login": "alice", "author_type": "User", "date": date_str}],
        "truncated": False,
    }
    rows = derive_commits_180d_rows(c365_data, now)
    assert len(rows) == 1
    assert rows[0]["author_login"] == "alice"


def test_derive_commits_180d_rows_empty_or_invalid() -> None:
    now = NOW
    assert derive_commits_180d_rows({}, now) == []
    assert derive_commits_180d_rows({"commits": None}, now) == []  # type: ignore[dict-item]
    assert derive_commits_180d_rows(None, now) == []  # type: ignore[arg-type]


def test_can_derive_180d_from_365d_not_truncated() -> None:
    now = NOW
    c365_data = {
        "commits": [{"date": (now - timedelta(days=10)).isoformat()}],
        "truncated": False,
    }
    assert can_derive_180d_from_365d(c365_data, now) is True


def test_can_derive_180d_from_365d_truncated_with_300_commits() -> None:
    now = NOW
    commits = [{"date": (now - timedelta(minutes=i)).isoformat()} for i in range(300)]
    c365_data = {"commits": commits, "truncated": True}
    assert can_derive_180d_from_365d(c365_data, now) is True


def test_can_derive_180d_from_365d_truncated_oldest_commit_reaches_cutoff() -> None:
    now = NOW
    # Oldest commit is 200 days old (past the 180d cutoff)
    commits = [
        {"date": (now - timedelta(days=1)).isoformat()},
        {"date": (now - timedelta(days=200)).isoformat()},
    ]
    c365_data = {"commits": commits, "truncated": True}
    assert can_derive_180d_from_365d(c365_data, now) is True


def test_can_derive_180d_from_365d_truncated_oldest_commit_inside_window() -> None:
    now = NOW
    # Truncated with fewer than 300 commits and oldest commit only 50 days old
    commits = [
        {"date": (now - timedelta(days=1)).isoformat()},
        {"date": (now - timedelta(days=50)).isoformat()},
    ]
    c365_data = {"commits": commits, "truncated": True}
    assert can_derive_180d_from_365d(c365_data, now) is False


def test_can_derive_180d_from_365d_invalid_payloads() -> None:
    now = NOW
    assert can_derive_180d_from_365d({}, now) is False
    assert can_derive_180d_from_365d(None, now) is False  # type: ignore[arg-type]
    assert can_derive_180d_from_365d({"commits": "not-a-list"}, now) is False
    assert can_derive_180d_from_365d({"rows": []}, now) is True  # empty, not truncated


def test_can_derive_180d_from_365d_sampled_is_false() -> None:
    """RFC-0021: a sampled commits_365d record holds pages spread over the
    year with gaps between them, not the contiguous 180-day window."""
    now = NOW
    commits = [
        {"date": (now - timedelta(days=1)).isoformat()},
        {"date": (now - timedelta(days=200)).isoformat()},
    ] * 200
    assert can_derive_180d_from_365d(
        {"rows": commits, "sampled": True, "truncated": False, "total": 12000}, now,
    ) is False

