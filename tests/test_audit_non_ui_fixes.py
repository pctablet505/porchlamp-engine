"""Regression tests for non-UI codebase audit flaw fixes (#119)."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path

import pytest

from porchlamp.collect import governance
from porchlamp.collect.ratelimit import CircuitBreaker, ETagCache, TokenBucketRateLimiter
from porchlamp.collect.user_cache import UserCache
from porchlamp.core import gates
from porchlamp.core.models import PRWindow, PRWindowEvent
from porchlamp.core.openssf import calculate_pike_criticality
from porchlamp.core.params.curves import clamped_identity, cohort_rate_score, fill_rate_score, reference_ecdf
from porchlamp.core.tiebreak import ttfhr_p75_from_pr_window
from porchlamp.derive.gate_evidence import _ghost_rate_prs_from_nodes
from porchlamp.normalize import compute_ecosystem_percentile
from porchlamp.searchops.credentials import Redactor


def test_nan_and_inf_rejected_in_curves_and_criticality() -> None:
    for fn in (fill_rate_score, cohort_rate_score, clamped_identity, reference_ecdf):
        with pytest.raises(ValueError, match="finite"):
            fn(float("nan"))
        with pytest.raises(ValueError, match="finite"):
            fn(float("inf"))

    with pytest.raises(ValueError, match="finite"):
        compute_ecosystem_percentile(float("nan"), [1.0, 2.0, 3.0])

    with pytest.raises(ValueError, match="finite"):
        compute_ecosystem_percentile(1.0, [1.0, float("nan"), 3.0])

    signals = {
        "created_since_months": 12.0,
        "updated_since_months": 1.0,
        "contributor_count": 10,
        "org_count": 2,
        "commit_frequency_weekly": float("nan"),
        "recent_releases_count": 4,
        "closed_issues_count_90d": 10,
        "updated_issues_count_90d": 20,
        "comment_frequency": 2.0,
        "dependents_count": 100,
    }
    with pytest.raises(ValueError, match="finite"):
        calculate_pike_criticality(signals)


def test_wilson_lower_bound_handles_overshoot_and_nan() -> None:
    assert gates.wilson_lower_bound(15, 10) == gates.wilson_lower_bound(10, 10)
    assert gates.wilson_lower_bound_from_share(float("nan"), 10) == 0.0
    assert gates.wilson_lower_bound_from_share(0.5, 10, z=float("nan")) == 0.0


def test_maintainers_preserves_automated_across_set_ops() -> None:
    m1 = gates.Maintainers(["alice", "bob"])
    m1.automated = frozenset({("alice", "thanks for the pr"), ("bob", "welcome contributor")})

    m2 = gates.Maintainers(["carol"])
    m2.automated = frozenset({("carol", "auto review message")})

    union = m1 | m2
    assert isinstance(union, gates.Maintainers)
    assert union.automated == m1.automated | m2.automated

    diff = m1 - {"bob"}
    assert isinstance(diff, gates.Maintainers)
    assert diff.automated == frozenset({("alice", "thanks for the pr")})

    inter = m1 & {"bob"}
    assert isinstance(inter, gates.Maintainers)
    assert inter.automated == frozenset({("bob", "welcome contributor")})


def test_ttfhr_p75_clamps_clock_skewed_negative_latency() -> None:
    ev = PRWindowEvent(
        pr_number=1,
        created_at="2026-06-01T12:00:00Z",
        closed_at=None,
        merged_at=None,
        author_login="ext",
        author_association="NONE",
        is_external=True,
        first_human_review_at="2026-06-01T11:55:00Z",
    )
    win = PRWindow(
        start="2026-01-01T00:00:00Z",
        end="2026-06-30T00:00:00Z",
        truncated=False,
        events=(ev,),
    )
    res = ttfhr_p75_from_pr_window(win)
    assert res == 0.0
    assert not math.isnan(res)


def test_governance_observed_promotions_reads_timeline_items_closed_events() -> None:
    prs = [
        {
            "number": 1,
            "createdAt": "2026-01-10T00:00:00Z",
            "mergedAt": "2026-01-11T00:00:00Z",
            "author": {"login": "newbie", "createdAt": "2020-01-01T00:00:00Z"},
            "mergedBy": {"login": "maint1"},
        },
        {
            "number": 2,
            "createdAt": "2026-02-10T00:00:00Z",
            "closedAt": "2026-02-12T00:00:00Z",
            "author": {"login": "other_user", "createdAt": "2020-01-01T00:00:00Z"},
            "timelineItems": {
                "nodes": [
                    {
                        "__typename": "ClosedEvent",
                        "createdAt": "2026-02-12T00:00:00Z",
                        "actor": {"login": "newbie"},
                    }
                ]
            },
        },
    ]
    res = governance.observed_promotions(prs)
    assert len(res) == 1
    assert res[0]["login"] == "newbie"
    assert res[0]["first_maintainer_action"] == "close"


def test_user_cache_file_root_fallback_and_monotonic_put(tmp_path: Path) -> None:
    db_file = tmp_path / "users.sqlite"
    cache = UserCache(db_file, backend="sqlite")
    t1 = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

    assert cache.put("alice", {"account_created_at": "2020-01-01T00:00:00Z", "public_event_count": 50}, t2)
    # Older observation must not overwrite newer observation
    assert cache.put("alice", {"account_created_at": "2020-01-01T00:00:00Z", "public_event_count": 10}, t1)
    got = cache.get("alice", t2)
    assert got is not None
    assert got["public_event_count"] == 50

    # Naive datetime is normalized to UTC and readable
    naive_now = datetime(2026, 6, 2, 12, 0)
    assert cache.put("bob", {"account_created_at": "2021-01-01T00:00:00Z", "public_event_count": 25}, naive_now)
    got_bob = cache.get("bob", datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc))
    assert got_bob is not None
    assert got_bob["public_event_count"] == 25

    # JSON fallback path when root is a .sqlite file uses sibling users/ directory
    json_path = cache._path("alice")
    assert json_path == tmp_path / "users" / "alice.json"
    cache.close()


def test_reopened_pr_not_marked_closed_from_historical_timeline_event() -> None:
    prs = _ghost_rate_prs_from_nodes(
        [
            {
                "number": 10,
                "state": "OPEN",
                "isDraft": False,
                "createdAt": "2026-05-01T00:00:00Z",
                "closedAt": None,
                "mergedAt": None,
                "author": {"login": "contributor", "createdAt": "2020-01-01T00:00:00Z"},
                "authorAssociation": "NONE",
                "reviews": {"nodes": [], "pageInfo": {"hasNextPage": False}},
                "comments": {"nodes": [], "pageInfo": {"hasNextPage": False}},
                "timelineItems": {
                    "pageInfo": {"hasNextPage": False},
                    "nodes": [
                        {
                            "__typename": "ClosedEvent",
                            "createdAt": "2026-05-02T00:00:00Z",
                            "actor": {"login": "contributor"},
                        }
                    ],
                },
            }
        ]
    )
    assert len(prs) == 1
    assert prs[0].closed_by_author is False
    assert prs[0].closed_by_maintainer_at is None


def test_redactor_ignores_short_tokens() -> None:
    r = Redactor()
    r.add("a")
    r.add("ab")
    r.add("secret_token_12345")
    assert r("status: active with secret_token_12345") == "status: active with [redacted]"


def test_ratelimit_primitives_have_thread_locks() -> None:
    limiter = TokenBucketRateLimiter(rate=10.0, capacity=2.0)
    limiter.acquire()
    cache = ETagCache()
    cache.put("https://example.com", '"e1"', b"ok")
    assert cache.get("https://example.com") == ('"e1"', b"ok")
    breaker = CircuitBreaker(jitter_fn=lambda: 0.0)
    until = breaker.trip()
    assert until > 0
