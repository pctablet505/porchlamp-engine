"""Tests for porchlamp.collect.github_graphql additions under RFC-0008.

Verifies PR_WINDOW_QUERY includes isDraft, derive_pr_window derives the new
RFC-0008 attributes (is_draft, ready_for_review_at, closed_by_author,
closed_by_maintainer_at, author_created_at) while maintaining backwards
compatibility, and ghost_rate_prs_from_window constructs complete GhostRatePR records.
"""
from __future__ import annotations

from typing import Any

from porchlamp.collect import github_graphql
from porchlamp.core import gates


def test_pr_window_query_includes_is_draft() -> None:
    assert "isDraft" in github_graphql.PR_WINDOW_QUERY


def test_derive_pr_window_backward_compatibility() -> None:
    """Envelopes without RFC-0008 fields derive safe default values."""
    pr: dict[str, Any] = {
        "number": 101,
        "createdAt": "2026-08-01T00:00:00Z",
        "author": {"__typename": "User", "login": "alice"},
        "authorAssociation": "NONE",
        "state": "OPEN",
    }
    derived = github_graphql.derive_pr_window([pr])
    assert len(derived) == 1
    d = derived[0]
    assert d["number"] == 101
    assert d["is_draft"] is False
    assert d["ready_for_review_at"] is None
    assert d["closed_by_author"] is False
    assert d["closed_by_maintainer_at"] is None
    assert d["author_created_at"] is None


def test_derive_pr_window_extracts_rfc0008_fields() -> None:
    prs: list[dict[str, Any]] = [
        # Draft PR with ready_for_review_at
        {
            "number": 1,
            "createdAt": "2026-08-01T00:00:00Z",
            "isDraft": True,
            "readyForReviewAt": "2026-08-05T00:00:00Z",
            "author": {"__typename": "User", "login": "alice", "createdAt": "2025-01-01T00:00:00Z"},
            "authorAssociation": "NONE",
            "state": "OPEN",
        },
        # Author self-closed PR
        {
            "number": 2,
            "createdAt": "2026-08-02T00:00:00Z",
            "closedAt": "2026-08-03T00:00:00Z",
            "closedByAuthor": True,
            "author": {"__typename": "User", "login": "bob"},
            "authorAssociation": "NONE",
            "state": "CLOSED",
        },
        # Maintainer-closed PR with explicit closedByMaintainerAt
        {
            "number": 3,
            "createdAt": "2026-08-03T00:00:00Z",
            "closedAt": "2026-08-04T00:00:00Z",
            "closedByMaintainerAt": "2026-08-04T00:00:00Z",
            "author": {"__typename": "User", "login": "charlie"},
            "authorAssociation": "NONE",
            "state": "CLOSED",
        },
        # Maintainer-closed PR with state=CLOSED and closedByMaintainer=True
        {
            "number": 4,
            "createdAt": "2026-08-04T00:00:00Z",
            "closedAt": "2026-08-06T00:00:00Z",
            "closedByMaintainer": True,
            "author": {"__typename": "User", "login": "dave"},
            "authorAssociation": "NONE",
            "state": "CLOSED",
        },
    ]
    derived = github_graphql.derive_pr_window(prs)
    assert len(derived) == 4

    assert derived[0]["is_draft"] is True
    assert derived[0]["ready_for_review_at"] == "2026-08-05T00:00:00Z"
    assert derived[0]["author_created_at"] == "2025-01-01T00:00:00Z"

    assert derived[1]["closed_by_author"] is True

    assert derived[2]["closed_by_maintainer_at"] == "2026-08-04T00:00:00Z"

    assert derived[3]["closed_by_maintainer_at"] == "2026-08-06T00:00:00Z"


def test_ghost_rate_prs_from_window() -> None:
    prs: list[dict[str, Any]] = [
        {
            "number": 10,
            "createdAt": "2026-08-01T00:00:00Z",
            "isDraft": False,
            "author": {"__typename": "User", "login": "alice", "createdAt": "2025-01-01T00:00:00Z"},
            "authorAssociation": "NONE",
            "state": "OPEN",
        },
        # Unparseable createdAt -> skipped
        {
            "number": 11,
            "createdAt": "not-a-date",
            "author": {"__typename": "User", "login": "bad"},
            "authorAssociation": "NONE",
            "state": "OPEN",
        },
    ]
    records = github_graphql.ghost_rate_prs_from_window(prs)
    assert len(records) == 1
    r = records[0]
    assert isinstance(r, gates.GhostRatePR)
    assert r.number == 10
    assert r.created_at == "2026-08-01T00:00:00Z"
    assert r.is_external is True
    assert r.is_bot_author is False
    assert r.author_created_at == "2025-01-01T00:00:00Z"
