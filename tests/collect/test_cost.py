"""Tests for per-collector API cost accounting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from porchlamp.collect import cost


@pytest.fixture(autouse=True)
def clean_records() -> None:
    cost.clear_records()


def test_default_scope_is_unscoped() -> None:
    assert cost.current_scope() == ("", "unscoped")


def test_collector_scope_and_nesting() -> None:
    assert cost.current_scope() == ("", "unscoped")
    with cost.collector_scope("owner__repo1", "pr_window"):
        assert cost.current_scope() == ("owner__repo1", "pr_window")
        with cost.collector_scope("owner__repo2", "governance"):
            assert cost.current_scope() == ("owner__repo2", "governance")
        assert cost.current_scope() == ("owner__repo1", "pr_window")
    assert cost.current_scope() == ("", "unscoped")


def test_record_call_uses_active_scope_and_defaults() -> None:
    with cost.collector_scope("octo__cat", "pike"):
        rec = cost.record_call(
            endpoint="/repos/octo/cat",
            method="GET",
            status=200,
            bytes_in=1024,
            duration_ms=45.123,
        )
    assert rec.repo_key == "octo__cat"
    assert rec.collector == "pike"
    assert rec.endpoint == "/repos/octo/cat"
    assert rec.method == "GET"
    assert rec.status == 200
    assert rec.status_304 is False
    assert rec.graphql_points == 0
    assert rec.bytes_in == 1024
    assert rec.duration_ms == 45.123
    assert "T" in rec.timestamp

    # Recorded in memory ledger
    all_recs = cost.get_records()
    assert len(all_recs) == 1
    assert all_recs[0] == rec


def test_record_call_status_304_handling() -> None:
    rec = cost.record_call(
        endpoint="/repos/o/r",
        method="GET",
        status=304,
        bytes_in=0,
        duration_ms=10.0,
    )
    assert rec.status == 304
    assert rec.status_304 is True
    assert rec.repo_key == ""
    assert rec.collector == "unscoped"


def test_clear_records_empties_ledger() -> None:
    cost.record_call(endpoint="/a", method="GET", status=200)
    assert len(cost.get_records()) == 1
    cost.clear_records()
    assert cost.get_records() == []


def test_write_and_read_cost_log(tmp_path: Path) -> None:
    rec1 = cost.record_call(
        endpoint="/graphql",
        method="POST",
        status=200,
        graphql_points=5,
        bytes_in=500,
        duration_ms=120.456,
        repo_key="o__r",
        collector="pr_window",
    )
    rec2 = cost.record_call(
        endpoint="/repos/o/r",
        method="GET",
        status=304,
        bytes_in=0,
        duration_ms=15.1,
        repo_key="o__r",
        collector="pr_window",
    )
    log_path = tmp_path / "sub" / "dir" / "gather-cost.jsonl"
    written_path = cost.write_cost_log(log_path)
    assert written_path == log_path
    assert log_path.exists()

    loaded = cost.read_cost_log(log_path)
    assert len(loaded) == 2
    assert loaded[0] == rec1
    assert loaded[1] == rec2


def test_summarize_costs() -> None:
    recs = [
        cost.CostRecord(
            timestamp="2026-10-14T00:00:00Z",
            repo_key="a__b",
            collector="pr_window",
            endpoint="/graphql",
            method="POST",
            status=200,
            status_304=False,
            graphql_points=2,
            bytes_in=1000,
            duration_ms=50.25,
        ),
        cost.CostRecord(
            timestamp="2026-10-14T00:00:01Z",
            repo_key="a__b",
            collector="pr_window",
            endpoint="/repos/a/b",
            method="GET",
            status=304,
            status_304=True,
            graphql_points=0,
            bytes_in=0,
            duration_ms=12.11,
        ),
        cost.CostRecord(
            timestamp="2026-10-14T00:00:02Z",
            repo_key="c__d",
            collector="governance",
            endpoint="/repos/c/d/community/profile",
            method="GET",
            status=200,
            status_304=False,
            graphql_points=0,
            bytes_in=450,
            duration_ms=30.0,
        ),
    ]
    summary = cost.summarize_costs(recs)
    assert summary == {
        "pr_window": {
            "calls": 2,
            "status_304": 1,
            "graphql_points": 2,
            "bytes_in": 1000,
            "duration_ms": 62.36,
        },
        "governance": {
            "calls": 1,
            "status_304": 0,
            "graphql_points": 0,
            "bytes_in": 450,
            "duration_ms": 30.0,
        },
    }


def test_security_no_authorization_tokens_persisted(tmp_path: Path) -> None:
    rec = cost.record_call(
        endpoint="/repos/octo/cat",
        method="GET",
        status=200,
        bytes_in=100,
        duration_ms=20.0,
    )
    log_path = tmp_path / "cost.jsonl"
    cost.write_cost_log(log_path, [rec])
    content = log_path.read_text(encoding="utf-8")
    for sensitive in ("token", "Token", "Bearer", "Authorization", "authorization", "secret"):
        assert sensitive not in content
    parsed = json.loads(content.strip())
    allowed_keys = {
        "timestamp",
        "repo_key",
        "collector",
        "endpoint",
        "method",
        "status",
        "status_304",
        "graphql_points",
        "bytes_in",
        "duration_ms",
    }
    assert set(parsed.keys()) == allowed_keys
