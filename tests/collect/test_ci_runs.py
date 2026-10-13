"""``porchlamp.collect.ci_runs``: the CI wait a contributor sees on a pull-request commit."""
from __future__ import annotations

from porchlamp.collect import ci_runs


def _row(sha: str, conclusion: str | None, seconds: int | None, status: str = "completed") -> dict:
    return {"head_sha": sha, "status": status, "conclusion": conclusion, "run_duration_seconds": seconds}


def test_shape_run_times_a_run_from_its_start_not_its_creation() -> None:
    """A run approved two days after it was created is timed on what it ran,
    not on the days it waited (the old created->updated reading)."""
    row = ci_runs.shape_run({
        "id": 4242, "head_sha": "abc", "status": "completed", "conclusion": "success", "workflow_id": 7, "run_attempt": 2,
        "created_at": "2026-10-01T00:00:00Z",
        "run_started_at": "2026-10-03T00:00:00Z",
        "updated_at": "2026-10-03T00:12:30Z",
        "repository": {"full_name": "dropped/dropped"},
    })
    assert row == {
        "id": 4242, "head_sha": "abc", "status": "completed", "conclusion": "success", "workflow_id": 7, "run_attempt": 2,
        "created_at": "2026-10-01T00:00:00Z",
        "run_started_at": "2026-10-03T00:00:00Z",
        "updated_at": "2026-10-03T00:12:30Z",
        "run_duration_seconds": 750,
    }


def test_shape_run_has_no_duration_without_both_stamps() -> None:
    assert ci_runs.shape_run({"head_sha": "abc", "updated_at": "2026-10-03T00:12:30Z"})["run_duration_seconds"] is None
    assert ci_runs.shape_run({"run_started_at": "not a date", "updated_at": "2026-10-03T00:12:30Z"})["run_duration_seconds"] is None


def test_a_commit_waits_for_its_slowest_run() -> None:
    """Three workflows on one commit: the labeler and the lint job finish in
    seconds, the test suite in 40 minutes. The contributor waits 40 minutes."""
    rows = [_row("c1", "success", 8), _row("c1", "failure", 2400), _row("c1", "success", 95)]
    assert ci_runs.commit_waits(rows) == [2400.0]


def test_commit_waits_are_newest_commit_first_whatever_the_row_order_within_a_commit() -> None:
    rows = [_row("new", "success", 60), _row("old", "success", 900), _row("new", "success", 300)]
    assert ci_runs.commit_waits(rows) == [300.0, 900.0]


def test_a_commit_with_a_cancelled_run_is_left_out() -> None:
    """A superseded push: its long workflow was cancelled, so the quick one
    that did finish says nothing about the wait."""
    rows = [_row("superseded", "success", 20), _row("superseded", "cancelled", 400), _row("kept", "success", 600)]
    assert ci_runs.commit_waits(rows) == [600.0]


def test_a_commit_with_a_run_still_going_is_left_out() -> None:
    """The lint job finished in a minute; the test suite is still running.
    Counting the commit now would call a three-hour wait a one-minute wait."""
    rows = [
        _row("running", "success", 60), _row("running", None, 900, status="in_progress"),
        _row("queued", "success", 60), _row("queued", None, None, status="queued"),
        _row("done", "success", 5400),
    ]
    assert ci_runs.commit_waits(rows) == [5400.0]


def test_created_filter_asks_for_runs_at_least_a_day_old() -> None:
    from datetime import datetime, timezone

    now = datetime(2026, 10, 13, 2, 30, 15, tzinfo=timezone.utc)
    assert ci_runs.created_filter(now) == "<=2026-10-12T02:30:15Z"


def test_runs_that_reached_no_verdict_do_not_count() -> None:
    rows = [
        _row("skipped-only", "skipped", 1),
        _row("awaiting-approval", "action_required", 0),
        _row("mixed", "skipped", 1), _row("mixed", "success", 120),
    ]
    assert ci_runs.commit_waits(rows) == [120.0]


def test_unusable_durations_and_rows_are_ignored() -> None:
    rows = [
        _row("c1", "success", None), _row("c1", "success", 0), _row("c1", "success", True),
        _row("c1", "success", float("inf")), _row("c1", "success", 45),
        {"status": "completed", "conclusion": "success", "run_duration_seconds": 999}, "not a row",
    ]
    assert ci_runs.commit_waits(rows) == [45.0]
    assert ci_runs.commit_waits([]) == []


def test_complete_commits_drops_the_oldest_commit_only_when_the_listing_was_cut() -> None:
    rows = [_row("new", "success", 60), _row("cut", "success", 10), _row("new", "success", 70), _row("cut", "success", 20)]
    assert ci_runs.complete_commits(rows, more_exist=False) == rows
    assert ci_runs.complete_commits(rows, more_exist=True) == [rows[0], rows[2]]
    assert ci_runs.complete_commits([], more_exist=True) == []
