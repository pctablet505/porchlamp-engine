"""Incremental delta ledgers, settled-PR rules, and light PR history (W6-3, W6-4, W6-5).

This module implements append-only and windowed delta ledgers for PRs, commits,
releases, and workflow runs, alongside the Settled-PR Rule (W6-3) and RFC-0009
light PR history collector utilities.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from porchlamp.core.landing import PR_LANDING_FIELDS

#: D-GATE-5 / W6-3: A merged or closed PR is considered settled once it has
#: matured past this number of days after its creation and decision.
SETTLED_MATURITY_DAYS: int = 14


def _ensure_utc(dt: datetime) -> datetime:
    """Ensure datetime is timezone-aware and set to UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp into a UTC-aware datetime, or None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _ensure_utc(dt)


def is_pr_settled(pr: Mapping[str, Any], anchor: datetime) -> bool:
    """Return True if a PR is settled at anchor date (W6-3).

    A PR is settled if:
    1. Its state is in ("MERGED", "CLOSED").
    2. Its createdAt timestamp is at least SETTLED_MATURITY_DAYS (14 days)
       before anchor.
    3. At least one of closedAt or mergedAt is present and at least
       SETTLED_MATURITY_DAYS (14 days) before anchor.

    Returns False if pr state is "OPEN", if created/closed younger than 14 days
    at anchor, or if timestamps are missing or invalid.
    """
    state = str(pr.get("state") or "").upper()
    if state not in ("MERGED", "CLOSED"):
        return False

    anchor_utc = _ensure_utc(anchor)
    created_at = _parse_iso(pr.get("createdAt"))
    if created_at is None:
        return False

    if created_at > anchor_utc - timedelta(days=SETTLED_MATURITY_DAYS):
        return False

    decided_candidates = [
        _parse_iso(pr.get("closedAt")),
        _parse_iso(pr.get("mergedAt")),
    ]
    valid_decided = [d for d in decided_candidates if d is not None]
    if not valid_decided:
        return False

    return any(d <= anchor_utc - timedelta(days=SETTLED_MATURITY_DAYS) for d in valid_decided)


def merge_pr_window_delta(
    existing_prs: Sequence[Mapping[str, Any]],
    delta_prs: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
    window_days: int = 180,
    cap: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Merge delta PRs into existing PR window, evicting stale and optionally capping (W6-3).

    - Deduplicates PR nodes by integer `number` (`delta_prs` overwrite `existing_prs`).
    - Evicts PRs whose `createdAt` is older than `now - timedelta(days=window_days)`.
    - Sorts newest-first by `(createdAt, number)` and caps at `cap` when set.
    """
    now_utc = _ensure_utc(now)
    cutoff = now_utc - timedelta(days=window_days)

    by_number: dict[int, dict[str, Any]] = {}
    for pr in existing_prs:
        try:
            num = int(pr["number"])
            by_number[num] = dict(pr)
        except (KeyError, TypeError, ValueError):
            continue

    for pr in delta_prs:
        try:
            num = int(pr["number"])
            by_number[num] = dict(pr)
        except (KeyError, TypeError, ValueError):
            continue

    survivors: list[dict[str, Any]] = []
    for pr in by_number.values():
        created_at = _parse_iso(pr.get("createdAt"))
        if created_at is not None and created_at >= cutoff:
            survivors.append(pr)

    def sort_key(p: dict[str, Any]) -> tuple[datetime, int]:
        c = _parse_iso(p.get("createdAt")) or datetime.min.replace(tzinfo=timezone.utc)
        num = int(p.get("number", 0))
        return (c, num)

    survivors.sort(key=sort_key, reverse=True)
    return survivors[:cap] if cap is not None else survivors


def unsettled_pr_numbers(
    prs: Sequence[Mapping[str, Any]],
    anchor: datetime,
    *,
    window_days: int = 180,
) -> list[int]:
    """Return sorted PR numbers in trailing window that are not settled (W6-3)."""
    anchor_utc = _ensure_utc(anchor)
    cutoff = anchor_utc - timedelta(days=window_days)

    unsettled: set[int] = set()
    for pr in prs:
        try:
            num = int(pr["number"])
        except (KeyError, TypeError, ValueError):
            continue

        created_at = _parse_iso(pr.get("createdAt"))
        if created_at is not None and created_at < cutoff:
            continue

        if not is_pr_settled(pr, anchor_utc):
            unsettled.add(num)

    return sorted(unsettled)


def _commit_key(c: Mapping[str, Any]) -> Any:
    """Deduplication key for commit records."""
    sha = c.get("sha")
    if sha:
        return str(sha)
    date_str = str(c.get("date") or (c.get("commit") or {}).get("author", {}).get("date") or "")
    author_login = str(c.get("author_login") or (c.get("author") or {}).get("login") or "")
    author_email = str(c.get("author_email") or (c.get("commit") or {}).get("author", {}).get("email") or "")
    signed_off = c.get("signed_off")
    if author_email or signed_off is not None:
        return (date_str, author_login, author_email, bool(signed_off))
    return (date_str, author_login)


def _commit_date(c: Mapping[str, Any]) -> Optional[datetime]:
    """Parse commit date."""
    date_str = c.get("date") or (c.get("commit") or {}).get("author", {}).get("date")
    return _parse_iso(date_str)


def merge_commits_365d_delta(
    existing_commits: Sequence[Mapping[str, Any]],
    new_commits: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
    window_days: int = 365,
    cap: int = 2500,
) -> list[dict[str, Any]]:
    """Merge delta commits into existing 365d commit ledger (W6-4).

    Deduplicates by `sha` (or `(date, author_login)` if sha is absent), evicts
    commits older than `now - timedelta(days=window_days)`, preserves
    newest-first order by date, and caps at `cap`.
    """
    now_utc = _ensure_utc(now)
    cutoff = now_utc - timedelta(days=window_days)

    by_key: dict[Any, dict[str, Any]] = {}
    for c in existing_commits:
        by_key[_commit_key(c)] = dict(c)
    for c in new_commits:
        by_key[_commit_key(c)] = dict(c)

    survivors: list[dict[str, Any]] = []
    for c in by_key.values():
        dt = _commit_date(c)
        if dt is not None and dt >= cutoff:
            survivors.append(c)

    def sort_key(c: dict[str, Any]) -> tuple[datetime, str]:
        dt = _commit_date(c) or datetime.min.replace(tzinfo=timezone.utc)
        sha = str(c.get("sha") or "")
        return (dt, sha)

    survivors.sort(key=sort_key, reverse=True)
    return survivors[:cap]


def _release_key(r: Mapping[str, Any]) -> Any:
    """Deduplication key for releases."""
    return r.get("tag_name") or r.get("id") or r.get("name")


def _release_date(r: Mapping[str, Any]) -> Optional[datetime]:
    """Parse release published_at or created_at date."""
    return _parse_iso(r.get("published_at")) or _parse_iso(r.get("created_at"))


def merge_releases_delta(
    existing_releases: Sequence[Mapping[str, Any]],
    new_releases: Sequence[Mapping[str, Any]],
    *,
    cap: int = 100,
) -> list[dict[str, Any]]:
    """Merge delta releases, deduplicating by tag_name/id and capping (W6-4)."""
    by_key: dict[Any, dict[str, Any]] = {}
    for r in existing_releases:
        k = _release_key(r)
        if k is not None:
            by_key[k] = dict(r)
    for r in new_releases:
        k = _release_key(r)
        if k is not None:
            by_key[k] = dict(r)

    releases = list(by_key.values())

    def sort_key(r: dict[str, Any]) -> tuple[datetime, str]:
        dt = _release_date(r) or datetime.min.replace(tzinfo=timezone.utc)
        rid = str(r.get("id") or r.get("tag_name") or "")
        return (dt, rid)

    releases.sort(key=sort_key, reverse=True)
    return releases[:cap]


def _run_date(r: Mapping[str, Any]) -> Optional[datetime]:
    """Parse workflow run creation date."""
    return _parse_iso(r.get("created_at"))


def merge_runs_delta(
    existing_runs: Sequence[Mapping[str, Any]],
    new_runs: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
    window_days: int = 90,
    cap: int = 200,
) -> list[dict[str, Any]]:
    """Merge delta workflow runs, deduplicating by id and evicting older (W6-4)."""
    now_utc = _ensure_utc(now)
    cutoff = now_utc - timedelta(days=window_days)

    by_id: dict[Any, dict[str, Any]] = {}
    for r in existing_runs:
        rid = r.get("id")
        if rid is not None:
            by_id[rid] = dict(r)
    for r in new_runs:
        rid = r.get("id")
        if rid is not None:
            by_id[rid] = dict(r)

    survivors: list[dict[str, Any]] = []
    for r in by_id.values():
        dt = _run_date(r)
        if dt is not None and dt >= cutoff:
            survivors.append(r)

    def sort_key(r: dict[str, Any]) -> tuple[datetime, int]:
        dt = _run_date(r) or datetime.min.replace(tzinfo=timezone.utc)
        try:
            rid = int(r.get("id", 0))
        except (ValueError, TypeError):
            rid = 0
        return (dt, rid)

    survivors.sort(key=sort_key, reverse=True)
    return survivors[:cap]


#: RFC-0009 / W6-5: Light PR history GraphQL query fetching only scalar fields.
#: Excludes nested reviews, comments, and reviewThreads.
#: RFC-0015 (#60): plus each PR's labels and latest close event with its closer
#: (:data:`porchlamp.core.landing.PR_LANDING_FIELDS`), so an author's earlier PR
#: that the project landed outside GitHub's merge counts as a merge.
LIGHT_PR_HISTORY_QUERY: str = (
    "query LightPrHistory($owner: String!, $name: String!, $first: Int = 100, $after: String) {"
    " rateLimit { cost }"
    " repository(owner: $owner, name: $name) {"
    " pullRequests(states: [OPEN, MERGED, CLOSED], first: $first, after: $after,"
    " orderBy: {field: CREATED_AT, direction: DESC}) {"
    " pageInfo { hasNextPage endCursor }"
    " nodes {"
    " number createdAt mergedAt closedAt state authorAssociation"
    " author { __typename login ... on User { createdAt } }"
    + PR_LANDING_FIELDS +
    " }"
    " } } }"
)


@dataclass(frozen=True)
class AuthorEligibilityEntry:
    """Cached author eligibility determination record (W6-5, RFC-0009)."""

    login: str
    account_created_at: str | None
    prior_commits: int
    checked_at: str


def is_cached_author_reusable(
    entry: AuthorEligibilityEntry,
    pr_created_at: str,
    min_account_age_days: int = 90,
    min_prior_commits: int = 5,
) -> bool:
    """Return True if cached author eligibility can be reused monotonically.

    Since account age and cumulative prior commits are monotonically non-decreasing
    for later PRs, an author who met both criteria at an earlier timestamp remains
    eligible for any subsequent PR.
    """
    acct_dt = _parse_iso(entry.account_created_at)
    pr_dt = _parse_iso(pr_created_at)
    if acct_dt is None or pr_dt is None:
        return False

    if pr_dt - acct_dt < timedelta(days=min_account_age_days):
        return False

    return entry.prior_commits >= min_prior_commits
