"""CI wait per pull-request commit (``02`` section 4 row 5.4, D-IN-4 "CI runs").

The runtime half of DX asks how long a contributor waits for CI on a pull
request. GitHub Actions reports one *workflow run* per workflow file, so the
most recent runs of a repository are mostly small ones (a labeler, a lint
job): their median reads under a minute even where the test suite takes
hours. The wait a contributor sees on a commit is its SLOWEST workflow, so
this module groups the ``pull_request`` runs by commit and takes the longest
finished run of each.

Pure functions over the rows the ``actions_runs`` collector stores; shared by
``scripts/gather.py`` (how many pages to fetch) and ``scripts/derive.py``
(the score), so the two cannot disagree on what a usable commit is.

What the runs list cannot show, and this measure therefore does not see:

- CI that is not a ``pull_request`` Actions run (an external CI service, or
  runs triggered by ``pull_request_target``, ``merge_group`` or
  ``workflow_run``). A repository whose only ``pull_request`` workflow is a
  quick check reads as seconds; one with none is unmeasured.
- Whether a run did any work. A run that waited for a maintainer's approval
  is timed from its creation (``run_started_at`` does not move when it is
  approved), and one never approved is closed as a ``failure`` after 30 days
  with no job in it. :data:`MAX_RUN_HOURS` removes the long ones; an approval
  granted within that bound still counts as wait.
- Earlier attempts of a re-run: the list returns the latest attempt only.
- A commit that fails fast reads short: this is the wait for a verdict, not
  the time to green.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Optional

#: A run that ran to a verdict. ``skipped`` ran nothing, ``cancelled`` was cut
#: short, ``action_required`` never started.
VERDICT_CONCLUSIONS: frozenset[str] = frozenset({"success", "failure"})

#: A commit with a cancelled run was superseded by a newer push (or stopped by
#: hand): its longest workflow may be the one that never finished.
SUPERSEDED_CONCLUSION = "cancelled"

#: Only runs created at least this long before the fetch are listed. A commit
#: pushed minutes ago shows its quick workflows as completed while the slow
#: ones still run, which reads as a short wait (pytorch/pytorch, 2026-10-13:
#: 6 min on the newest runs, 75 min on runs a day old).
SETTLE_HOURS = 24

#: A verdict run longer than this is a wait for approval, not CI: GitHub stops
#: a hosted job at 6 hours. On the first 2,742 envelopes collected, 8.2 % of
#: success/failure runs were longer, 98 % of those were failures, and more than
#: half ended exactly 30 days after they were created; ``vuejs/vue`` run
#: 34053110597 is one (failure, 0 jobs, 30 days). Its commit is left out.
MAX_RUN_HOURS = 6

#: REST page size (the API maximum) and how many pages one repository may cost.
PAGE_SIZE = 100
MAX_PAGES = 3


def _seconds_between(started: Any, ended: Any) -> Optional[int]:
    if not isinstance(started, str) or not isinstance(ended, str):
        return None
    try:
        delta = datetime.fromisoformat(ended.replace("Z", "+00:00")) - datetime.fromisoformat(
            started.replace("Z", "+00:00")
        )
    except ValueError:
        return None
    return int(delta.total_seconds())


def created_filter(now: datetime) -> str:
    """The REST ``created`` filter for runs old enough to have finished."""
    cutoff = now - timedelta(hours=SETTLE_HOURS)
    return "<=" + cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")


def shape_run(run: dict) -> dict:
    """One stored row from one REST ``workflow_run``.

    ``run_duration_seconds`` is ``updated_at - run_started_at``: the list
    endpoint carries no duration field, and this difference is what the
    per-run timing endpoint reports (within seconds on 24 sampled runs,
    2026-10-13). ``run_started_at`` restarts on a re-run, so a re-run is timed
    on its own attempt. It does not restart when a waiting run is approved:
    see :data:`MAX_RUN_HOURS`.
    """
    return {
        "id": run.get("id"),
        "head_sha": run.get("head_sha"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "workflow_id": run.get("workflow_id"),
        "run_attempt": run.get("run_attempt"),
        "created_at": run.get("created_at"),
        "run_started_at": run.get("run_started_at"),
        "updated_at": run.get("updated_at"),
        "run_duration_seconds": _seconds_between(run.get("run_started_at"), run.get("updated_at")),
    }


def complete_commits(rows: list[dict], *, more_exist: bool) -> list[dict]:
    """``rows`` without the oldest commit when the listing was cut off there.

    Rows arrive newest first. When more runs exist beyond the last fetched
    page, the commit of the last row may have runs on the next page, so its
    longest run is unknown: all of its rows are dropped.
    """
    if not more_exist or not rows:
        return rows
    cut = rows[-1].get("head_sha")
    return [row for row in rows if row.get("head_sha") != cut]


def commit_waits(rows: list[dict]) -> list[float]:
    """Seconds each commit waited for CI, newest commit first.

    A commit's wait is the longest run on it that reached a verdict
    (:data:`VERDICT_CONCLUSIONS`) with a positive duration. A commit is left
    out when one of its runs has not completed, when one was cancelled, when a
    verdict run on it lasted longer than :data:`MAX_RUN_HOURS`, or when none
    reached a verdict.
    """
    max_seconds = MAX_RUN_HOURS * 3600
    order: list[str] = []
    longest: dict[str, float] = {}
    left_out: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        sha = row.get("head_sha")
        if not isinstance(sha, str) or not sha:
            continue
        if sha not in longest:
            longest[sha] = 0.0
            order.append(sha)
        conclusion = row.get("conclusion")
        if row.get("status") != "completed" or conclusion == SUPERSEDED_CONCLUSION:
            left_out.add(sha)
            continue
        seconds = row.get("run_duration_seconds")
        if (
            conclusion not in VERDICT_CONCLUSIONS
            or not isinstance(seconds, (int, float))
            or isinstance(seconds, bool)
            or not math.isfinite(seconds)
        ):
            continue
        if seconds > max_seconds:
            left_out.add(sha)
        elif seconds > longest[sha]:
            longest[sha] = float(seconds)
    return [longest[sha] for sha in order if sha not in left_out and longest[sha] > 0]
