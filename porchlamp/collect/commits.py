"""Commit-author collection and domain-based derivations.

Collects the trailing 365 d of commits (cap 1,000, newest first) with
``commit.author.email``, ``author.login`` and ``author.type`` (``02`` section
3: "Commits | trailing 365 d (EF, bus factor), trailing 180 d (vitality)");
:func:`collect_commits_365d` records the cap as a fact by returning the
envelope ``data`` as a window-metadata dict with a ``truncated`` boolean,
the convention :mod:`porchlamp.collect.github_graphql` uses for its capped
windows; and derives from the typed commit actors:

* organisation by email domain (spec 3.2): one corporate-domain org per
  domain; ``users.noreply.github.com`` maps to the author's login (never an
  org of its own); personal domains (:data:`PERSONAL_DOMAINS`) count as one
  org per author;
* the elephant factor (D-PARAM 3.2): the minimum number of orgs reaching
  50% of the human commits in the window;
* the bus factor: the minimum number of human authors reaching 50% of the
  human commits in the window;
* vitality (D-GATE-4): at least one non-bot commit in the trailing 180 d.

Bots are excluded from every count: an actor is a bot per D-IN-1
(``actor_type == "Bot"``, a login ending in ``[bot]``, or an exact-match
login in :data:`KNOWN_BOT_LOGINS`); substring matching is absent (F12).

``CommitActor``, ``CommitRecord``, the three domain tables
(``NOREPLY_DOMAIN``, ``PERSONAL_DOMAINS``, ``KNOWN_BOT_LOGINS``),
``is_bot``, ``org_key``, ``author_key``, ``envelope_rows`` and
``records_from_rows`` are
defined in ``porchlamp.core.commit_domains`` (stdlib only, so
``porchlamp.derive.bundle`` can import it while it cannot import this module --
``tests/core/test_import_graph.py``) and are re-exported here unchanged;
``__all__`` below lists them. D-TIE-1 item 7's ``last_non_bot_commit_at``
and 03 section 6's ``top_email_domain_share`` live in that module too.

:func:`shape_commit_rows` also records the boolean :data:`SIGNOFF_TRAILER`
presence per commit as ``signed_off`` -- the fact 02 section 4 3.1's
Signed-off-by share needs -- and never stores the message text itself.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional, Sequence

#: Defined in ``porchlamp.core.commit_domains`` -- the one place this logic
#: lives -- and re-exported here unchanged so callers of this module
#: (``commits.CommitActor``, ``commits.is_bot`` etc.) resolve the same
#: objects. ``porchlamp.derive.bundle`` imports them from that module directly.
from porchlamp.core.commit_domains import (
    KNOWN_BOT_LOGINS,
    NOREPLY_DOMAIN,
    PERSONAL_DOMAINS,
    CommitActor,
    CommitRecord,
    author_key,
    envelope_rows,
    is_bot,
    org_key,
    records_from_rows,
)

#: Declares the re-exported names above (F401: unused-import) as this
#: module's own public names, together with the public names defined
#: locally, so ``__all__`` stays the complete list rather than a partial
#: one that would misleadingly imply the rest are private.
__all__ = [
    "COMMIT_WINDOW_DAYS",
    "COMMIT_CAP",
    "COMMIT_PAGE_SIZE",
    "VITALITY_WINDOW_DAYS",
    "SIGNOFF_TRAILER",
    "KNOWN_BOT_LOGINS",
    "NOREPLY_DOMAIN",
    "PERSONAL_DOMAINS",
    "CommitActor",
    "CommitRecord",
    "author_key",
    "envelope_rows",
    "is_bot",
    "org_key",
    "records_from_rows",
    "shape_commit_rows",
    "collect_commits_365d",
    "derive_commits_180d_rows",
    "can_derive_180d_from_365d",
    "elephant_factor",
    "bus_factor",
    "is_vital",
]

#: EF and bus factor are computed over the trailing 365 d.
COMMIT_WINDOW_DAYS: int = 365
#: At most 1,000 commits per repo are collected (newest first).
COMMIT_CAP: int = 1000
#: REST listing page size (GitHub max per_page for commits is 100).
COMMIT_PAGE_SIZE: int = 100
#: D-GATE-4: vitality counts the trailing 180 d.
VITALITY_WINDOW_DAYS: int = 180
#: 02 section 4 3.1: the DCO trailer :func:`shape_commit_rows` flags
#: per commit (case-sensitive, substring match against the raw message,
#: never stored). The exact convention text per the DCO spec.
SIGNOFF_TRAILER: str = "Signed-off-by:"


def shape_commit_rows(raw_commits: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Shape raw GitHub REST commit objects into envelope rows.

    Each row is ``{"date", "author_email", "author_login", "author_type",
    "signed_off"}``: ``date`` and ``author_email`` come from the git commit
    author, ``author_login`` and ``author_type`` from the linked GitHub
    account ("" when the commit has none), and ``signed_off`` (02
    section 4 3.1) is whether :data:`SIGNOFF_TRAILER` occurs in
    ``commit.message`` -- the message text itself is never kept in the row.
    """
    rows: list[dict[str, Any]] = []
    for commit in raw_commits:
        git_commit = commit.get("commit") or {}
        git_author = git_commit.get("author") or {}
        account = commit.get("author") or {}
        message = git_commit.get("message") or ""
        rows.append({
            "date": git_author.get("date"),
            "author_email": git_author.get("email") or "",
            "author_login": account.get("login") or "",
            "author_type": account.get("type") or "",
            "signed_off": SIGNOFF_TRAILER in message,
        })
    return rows


def collect_commits_365d(
    fetch_page: Callable[[int, str], Sequence[Mapping[str, Any]]],
    now: Optional[datetime] = None,
    *,
    existing_rows: Optional[Sequence[Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """Collect the trailing 365 d of commits, newest first, capped.

    ``fetch_page(page, since)`` is the transport boundary: it must return
    the raw commit objects of one page of the
    ``repos/{owner}/{name}/commits`` listing (page >= 1, ``since`` the
    ISO-8601 window start). Fetching stops at a page with fewer than
    :data:`COMMIT_PAGE_SIZE` items or once :data:`COMMIT_CAP` rows are
    gathered; a repo with more commits than the cap records its newest
    :data:`COMMIT_CAP` commits (a sub-365 d slice).

    When ``existing_rows`` is supplied (W6-4 incremental ledger refresh),
    stops paging once a commit already present in ``existing_rows`` is
    encountered and merges the delta via :func:`porchlamp.collect.ledger.merge_commits_365d_delta`.
    """
    from porchlamp.collect import ledger

    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=COMMIT_WINDOW_DAYS)).isoformat()
    existing_keys: set[Any] = set()
    if existing_rows:
        existing_keys = {ledger._commit_key(r) for r in existing_rows if isinstance(r, Mapping)}

    rows: list[dict[str, Any]] = []
    truncated = False
    for page in range(1, COMMIT_CAP // COMMIT_PAGE_SIZE + 1):
        raw = list(fetch_page(page, since))
        shaped = shape_commit_rows(raw)
        rows.extend(shaped)
        if existing_keys and any(ledger._commit_key(s) in existing_keys for s in shaped):
            break
        if len(raw) < COMMIT_PAGE_SIZE:
            break
        if len(rows) >= COMMIT_CAP:
            truncated = True
            break

    if existing_rows:
        rows = ledger.merge_commits_365d_delta(
            existing_rows,
            rows,
            now=now,
            window_days=COMMIT_WINDOW_DAYS,
            cap=COMMIT_CAP,
        )
        truncated = len(rows) >= COMMIT_CAP
    else:
        rows = rows[:COMMIT_CAP]
    return {
        "count": len(rows),
        "cap": COMMIT_CAP,
        "truncated": truncated,
        "rows": rows,
    }


def _human_window(records: Sequence[CommitRecord], now: datetime,
                  window_days: int, include_undated: bool) -> list[CommitRecord]:
    """Non-bot records on or after ``now - window_days``.

    Records without a recorded author date are kept only when
    ``include_undated`` is set (they were collected inside the window).
    """
    cutoff = now - timedelta(days=window_days)
    out: list[CommitRecord] = []
    for record in records:
        if is_bot(record.actor):
            continue
        if record.date is None:
            if include_undated:
                out.append(record)
        elif record.date >= cutoff:
            out.append(record)
    return out


def elephant_factor(records: Sequence[CommitRecord], now: datetime,
                    window_days: int = COMMIT_WINDOW_DAYS) -> Optional[int]:
    """Spec 3.2: the minimum number of orgs reaching 50% of the attributable
    human commits in the trailing window (365 d by default).

    Orgs come from :func:`org_key` over the non-bot commits; greedy
    accumulation from the largest org yields the minimum count; the result
    is floored at 1 (spec floor). Returns ``None`` when no non-bot commit in
    the window resolves to an org at all -- "no attributable commits" is an
    absence, not the worst measured value of 1 (a single-org project), which
    the caller must be able to tell apart.
    """
    window = _human_window(records, now, window_days, include_undated=True)
    counts: Counter[str] = Counter()
    for record in window:
        org = org_key(record.actor)
        if org is not None:
            counts[org] += 1
    total = sum(counts.values())
    if total == 0:
        return None
    acc, ef = 0, 0
    for _org, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        acc += n
        ef += 1
        if acc / total >= 0.5:
            return ef
    return max(1, ef)


def bus_factor(records: Sequence[CommitRecord], now: datetime,
               window_days: int = COMMIT_WINDOW_DAYS) -> Optional[int]:
    """The minimum number of human authors reaching 50% of the attributable
    human commits in the trailing window (365 d by default).

    Authors come from :func:`author_key` over the non-bot commits (one
    author per login, else per email address); greedy accumulation from the
    largest author yields the minimum count; the result is floored at 1.
    Returns ``None`` when no non-bot commit in the window resolves to an
    author at all -- "no attributable commits" is an absence, not the worst
    measured value of 1, which the caller must be able to tell apart.
    """
    window = _human_window(records, now, window_days, include_undated=True)
    counts: Counter[str] = Counter()
    for record in window:
        author = author_key(record.actor)
        if author is not None:
            counts[author] += 1
    total = sum(counts.values())
    if total == 0:
        return None
    acc, factor = 0, 0
    for _author, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        acc += n
        factor += 1
        if acc / total >= 0.5:
            return factor
    return max(1, factor)


def is_vital(records: Sequence[CommitRecord], now: datetime,
             window_days: int = VITALITY_WINDOW_DAYS) -> bool:
    """D-GATE-4: True when at least one non-bot commit was authored on or
    after ``now - window_days`` (trailing 180 d by default).

    A commit without a recorded author date cannot evidence recency and
    never counts; bot commits never count.
    """
    cutoff = now - timedelta(days=window_days)
    for record in records:
        if record.date is not None and record.date >= cutoff and not is_bot(record.actor):
            return True
    return False


def _parse_commit_date(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 author date string into an aware UTC datetime."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _extract_c365_commits(c365_data: Mapping[str, Any]) -> Optional[list[Any]]:
    """Extract the commit list from a commits_365d payload ('commits' or 'rows')."""
    if not isinstance(c365_data, Mapping):
        return None
    commits = c365_data.get("commits")
    if isinstance(commits, list):
        return commits
    rows = c365_data.get("rows")
    if isinstance(rows, list):
        return rows
    return None


def derive_commits_180d_rows(
    c365_data: Mapping[str, Any],
    now: Optional[datetime] = None,
    window_days: int = VITALITY_WINDOW_DAYS,
) -> list[dict[str, Any]]:
    """Derive 180-day vitality commit rows from a commits_365d data payload.

    Given ``c365_data`` (holding ``commits`` or ``rows``), filters commits whose
    ``date`` parses as ISO-8601 and falls within ``[now - window_days, now]``.
    Each matching commit is projected to ``{"author_login", "author_type", "date"}``,
    preserving newest-first order and capped at 300 rows.
    """
    commits = _extract_c365_commits(c365_data)
    if not commits:
        return []
    if now is None:
        now = datetime.now(timezone.utc)
    ref_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    cutoff = ref_now - timedelta(days=window_days)
    rows: list[dict[str, Any]] = []
    for c in commits:
        if not isinstance(c, Mapping):
            continue
        date_str = c.get("date") or c.get("author_date")
        dt = _parse_commit_date(date_str)
        if dt is not None and cutoff <= dt <= ref_now:
            rows.append({
                "author_login": c.get("author_login"),
                "author_type": c.get("author_type"),
                "date": date_str,
            })
            if len(rows) >= 300:
                break
    return rows[:300]


def can_derive_180d_from_365d(
    c365_data: Mapping[str, Any],
    now: Optional[datetime] = None,
    window_days: int = VITALITY_WINDOW_DAYS,
) -> bool:
    """Return True if commits_180d can be derived from commits_365d with 0 API calls.

    Returns True when ``c365_data`` is a Mapping with a commit list, and either:
    - ``truncated`` is False,
    - the list contains >= 300 commits, or
    - the oldest commit date is <= ``now - window_days``.
    """
    if not isinstance(c365_data, Mapping):
        return False
    commits = _extract_c365_commits(c365_data)
    if not isinstance(commits, list):
        return False
    if not c365_data.get("truncated", False):
        return True
    if len(commits) >= 300:
        return True
    if now is None:
        now = datetime.now(timezone.utc)
    ref_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    cutoff = ref_now - timedelta(days=window_days)
    for c in reversed(commits):
        if isinstance(c, Mapping):
            date_str = c.get("date") or c.get("author_date")
            dt = _parse_commit_date(date_str)
            if dt is not None:
                return dt <= cutoff
    return False

