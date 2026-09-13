"""Commit-author collection and domain-based derivations (packet W1-5).

Collects the trailing 365 d of commits (cap 1,000, newest first) with
``commit.author.email``, ``author.login`` and ``author.type`` (``02`` section
3: "Commits | trailing 365 d (EF, bus factor), trailing 180 d (vitality)"),
and derives from the typed commit actors:

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

W1-5 owns only this module (R-10), so the packet's frozen constants are
defined here, not in ``cvi/constants.py``.

W1-5f relocation note: ``CommitActor``, ``CommitRecord``, the three domain
tables (``NOREPLY_DOMAIN``, ``PERSONAL_DOMAINS``, ``KNOWN_BOT_LOGINS``),
``is_bot``, ``org_key``, ``author_key``, ``_parse_dt`` (kept private, not
re-exported -- no caller outside ``cvi.core.commit_domains`` uses it) and
``records_from_rows`` now live in ``cvi.core.commit_domains`` (stdlib
only, importable from ``cvi.derive.bundle`` -- this module cannot be,
``tests/core/test_import_graph.py``); every symbol with an existing
caller is re-exported here unchanged so that caller keeps working
verbatim (``__all__`` below lists them). See that module's docstring for
why (D-TIE-1 item 7's ``last_non_bot_commit_at`` and 03 section 6's
``top_email_domain_share`` also live there now).

W1-6 ownership extension (02 section 4 3.1, dispatch-authorized): this
collector already receives ``commit.message`` in the raw GitHub REST
response for every window commit (the ``repos/{owner}/{repo}/commits``
listing) but discarded it when shaping envelope rows. :func:`shape_commit_rows`
now also records the boolean :data:`SIGNOFF_TRAILER` presence per commit as
``signed_off`` -- the fact 3.1's Signed-off-by share needs -- and still never
stores the message text itself, so no new live call is added for 3.1.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional, Sequence

#: Relocated to ``cvi.core.commit_domains`` (W1-5f, see the module
#: docstring); re-exported here unchanged so every existing caller of this
#: module (``commits.CommitActor``, ``commits.is_bot`` etc.) keeps working
#: verbatim. ``cvi.derive.bundle`` (W1-5f) reads the same symbols directly
#: from ``cvi.core.commit_domains`` -- the one place this logic is defined.
from cvi.core.commit_domains import (
    KNOWN_BOT_LOGINS,
    NOREPLY_DOMAIN,
    PERSONAL_DOMAINS,
    CommitActor,
    CommitRecord,
    author_key,
    is_bot,
    org_key,
    records_from_rows,
)

#: Declares the relocated names above (F401: unused-import) as this
#: module's own public re-exports, matching the convention ``cvi/
#: openssf.py``'s own W2-9g relocation set (``__all__ = [...]``) -- plus
#: this module's locally-defined public names, so ``__all__`` stays the
#: complete list rather than a partial one that would misleadingly imply
#: the rest are private.
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
    "is_bot",
    "org_key",
    "records_from_rows",
    "shape_commit_rows",
    "collect_commits_365d",
    "elephant_factor",
    "bus_factor",
    "is_vital",
]

#: 05-W1-5: EF and bus factor are computed over the trailing 365 d.
COMMIT_WINDOW_DAYS: int = 365
#: 05-W1-5: at most 1,000 commits per repo are collected (newest first).
COMMIT_CAP: int = 1000
#: REST listing page size (GitHub max per_page for commits is 100).
COMMIT_PAGE_SIZE: int = 100
#: D-GATE-4: vitality counts the trailing 180 d.
VITALITY_WINDOW_DAYS: int = 180
#: W1-6 (02 section 4 3.1): the DCO trailer :func:`shape_commit_rows` flags
#: per commit (case-sensitive, substring match against the raw message,
#: never stored). The exact convention text per the DCO spec.
SIGNOFF_TRAILER: str = "Signed-off-by:"


def shape_commit_rows(raw_commits: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Shape raw GitHub REST commit objects into envelope rows.

    Each row is ``{"date", "author_email", "author_login", "author_type",
    "signed_off"}``: ``date`` and ``author_email`` come from the git commit
    author, ``author_login`` and ``author_type`` from the linked GitHub
    account ("" when the commit has none), and ``signed_off`` (W1-6, 02
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
) -> list[dict[str, Any]]:
    """Collect the trailing 365 d of commits, newest first, capped.

    ``fetch_page(page, since)`` is the transport boundary: it must return
    the raw commit objects of one page of the
    ``repos/{owner}/{name}/commits`` listing (page >= 1, ``since`` the
    ISO-8601 window start). Fetching stops at a page with fewer than
    :data:`COMMIT_PAGE_SIZE` items or once :data:`COMMIT_CAP` rows are
    gathered; a repo with more commits than the cap records its newest
    :data:`COMMIT_CAP` commits (a sub-365 d slice).
    """
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=COMMIT_WINDOW_DAYS)).isoformat()
    rows: list[dict[str, Any]] = []
    for page in range(1, COMMIT_CAP // COMMIT_PAGE_SIZE + 1):
        raw = list(fetch_page(page, since))
        rows.extend(shape_commit_rows(raw))
        if len(raw) < COMMIT_PAGE_SIZE or len(rows) >= COMMIT_CAP:
            break
    return rows[:COMMIT_CAP]


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
                    window_days: int = COMMIT_WINDOW_DAYS) -> int:
    """Spec 3.2: the minimum number of orgs reaching 50% of the human
    commits in the trailing window (365 d by default).

    Orgs come from :func:`org_key` over the non-bot commits; commits whose
    author resolves to no org still count in the 50% denominator. Greedy
    accumulation from the largest org yields the minimum count; the result
    is floored at 1 (spec floor).
    """
    window = _human_window(records, now, window_days, include_undated=True)
    total = len(window)
    if total == 0:
        return 1
    counts: Counter[str] = Counter()
    for record in window:
        org = org_key(record.actor)
        if org is not None:
            counts[org] += 1
    acc, ef = 0, 0
    for _org, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        acc += n
        ef += 1
        if acc / total >= 0.5:
            return ef
    return max(1, ef)


def bus_factor(records: Sequence[CommitRecord], now: datetime,
               window_days: int = COMMIT_WINDOW_DAYS) -> int:
    """The minimum number of human authors reaching 50% of the human commits
    in the trailing window (365 d by default).

    Authors come from :func:`author_key` over the non-bot commits (one
    author per login, else per email address); unattributable commits stay
    in the 50% denominator. The result is floored at 1.
    """
    window = _human_window(records, now, window_days, include_undated=True)
    total = len(window)
    if total == 0:
        return 1
    counts: Counter[str] = Counter()
    for record in window:
        author = author_key(record.actor)
        if author is not None:
            counts[author] += 1
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
