"""Commit-author collection and domain-based derivations.

Collects the trailing 365 d of commits (newest first) with the commit
``sha``, ``commit.author.email``, ``author.login`` and ``author.type``
(``02`` section 3: "Commits | trailing 365 d (EF, bus factor), trailing
180 d (vitality)"): every commit when GitHub counts at most
:data:`COMMIT_CAP` in the window, else :data:`COMMIT_CAP` of them in whole
pages spread evenly over the year (RFC-0021). :func:`collect_commits_365d`
records which as a fact by returning the envelope ``data`` as a
window-metadata dict (``total``, ``sampled``, ``truncated``), the
convention :mod:`porchlamp.collect.github_graphql` uses for its windows;
``porchlamp.core.commit_domains.commit_window`` is how readers read it.
This module derives from the typed commit actors:

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
``is_bot``, ``org_key``, ``author_key``, ``envelope_rows``,
``commit_window`` (with ``CommitWindow`` and ``COMMIT_WINDOW_CUT_DETAIL``)
and ``records_from_rows`` are
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

from porchlamp.core import constants

#: Defined in ``porchlamp.core.commit_domains`` -- the one place this logic
#: lives -- and re-exported here unchanged so callers of this module
#: (``commits.CommitActor``, ``commits.is_bot`` etc.) resolve the same
#: objects. ``porchlamp.derive.bundle`` imports them from that module directly.
from porchlamp.core.commit_domains import (
    COMMIT_WINDOW_CUT_DETAIL,
    KNOWN_BOT_LOGINS,
    NOREPLY_DOMAIN,
    PERSONAL_DOMAINS,
    CommitActor,
    CommitRecord,
    CommitWindow,
    author_key,
    commit_window,
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
    "COMMIT_TOTAL_QUERY",
    "COMMIT_WINDOW_CUT_DETAIL",
    "VITALITY_WINDOW_DAYS",
    "SIGNOFF_TRAILER",
    "KNOWN_BOT_LOGINS",
    "NOREPLY_DOMAIN",
    "PERSONAL_DOMAINS",
    "CommitActor",
    "CommitRecord",
    "CommitWindow",
    "author_key",
    "commit_window",
    "envelope_rows",
    "is_bot",
    "org_key",
    "records_from_rows",
    "shape_commit_rows",
    "commit_total_fetcher",
    "sample_pages",
    "collect_commits_365d",
    "derive_commits_180d_rows",
    "can_derive_180d_from_365d",
    "elephant_factor",
    "bus_factor",
    "is_vital",
]

#: EF and bus factor are computed over the trailing 365 d.
COMMIT_WINDOW_DAYS: int = 365
#: D-IN-4 Commits (RFC-0021): every commit of the window up to this many,
#: else this many in whole pages spread evenly over it. Defined in the
#: constants freeze (``porchlamp.core.constants``) and re-bound here under its
#: original name.
COMMIT_CAP: int = constants.COMMIT_CAP
#: REST listing page size (GitHub max per_page for commits is 100).
COMMIT_PAGE_SIZE: int = 100
#: D-GATE-4: vitality counts the trailing 180 d.
VITALITY_WINDOW_DAYS: int = 180
#: 02 section 4 3.1: the DCO trailer :func:`shape_commit_rows` flags
#: per commit (case-sensitive, substring match against the raw message,
#: never stored). The exact convention text per the DCO spec.
SIGNOFF_TRAILER: str = "Signed-off-by:"

#: RFC-0021: GitHub's count of the default-branch commits since ``$since``
#: (one GraphQL call, 1 point). It counts the commits the REST listing pages
#: through with the same ``since``: 2,568, 3,839, 5,586 and 11,767 both ways
#: on ``numpy/numpy``, ``moby/moby``, ``cilium/cilium`` and
#: ``grafana/grafana`` (2026-10-22).
COMMIT_TOTAL_QUERY: str = (
    "query CommitTotal($owner: String!, $name: String!, $since: GitTimestamp!) {"
    " repository(owner: $owner, name: $name) { defaultBranchRef { target {"
    " ... on Commit { history(since: $since) { totalCount } } } } } }"
)


def shape_commit_rows(raw_commits: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Shape raw GitHub REST commit objects into envelope rows.

    Each row is ``{"sha", "date", "author_email", "author_login",
    "author_type", "signed_off"}``: ``sha`` is the commit id ("" when the
    object carries none; the incremental ledger's dedup key,
    :func:`porchlamp.collect.ledger._commit_key`), ``date`` and
    ``author_email`` come from the git commit author, ``author_login`` and
    ``author_type`` from the linked GitHub account ("" when the commit has
    none), and ``signed_off`` (02 section 4 3.1) is whether
    :data:`SIGNOFF_TRAILER` occurs in ``commit.message`` -- the message text
    itself is never kept in the row.
    """
    rows: list[dict[str, Any]] = []
    for commit in raw_commits:
        git_commit = commit.get("commit") or {}
        git_author = git_commit.get("author") or {}
        account = commit.get("author") or {}
        message = git_commit.get("message") or ""
        rows.append({
            "sha": str(commit.get("sha") or ""),
            "date": git_author.get("date"),
            "author_email": git_author.get("email") or "",
            "author_login": account.get("login") or "",
            "author_type": account.get("type") or "",
            "signed_off": SIGNOFF_TRAILER in message,
        })
    return rows


def commit_total_fetcher(transport: Any, owner: str, name: str) -> Callable[[str], int]:
    """A ``fetch_total`` for :func:`collect_commits_365d` over a GraphQL
    transport (``porchlamp.collect.github_graphql.GhCliTransport``'s
    ``execute(query, variables)``): :data:`COMMIT_TOTAL_QUERY` for
    ``owner/name``. Raises ``RuntimeError`` when the answer carries no count
    (an error, a repository without a default branch), so the caller marks
    the collector failed and a rerun asks again -- never a guessed count.
    """

    def fetch_total(since: str) -> int:
        doc = transport.execute(COMMIT_TOTAL_QUERY, {"owner": owner, "name": name, "since": since})
        node: Any = doc
        for key in ("data", "repository", "defaultBranchRef", "target", "history", "totalCount"):
            node = node.get(key) if isinstance(node, Mapping) else None
        if isinstance(node, bool) or not isinstance(node, int) or node < 0:
            errors = doc.get("errors") if isinstance(doc, Mapping) else None
            raise RuntimeError(f"commit total for {owner}/{name} not answered: {str(errors)[:160]}")
        return node

    return fetch_total


def sample_pages(total: int) -> list[int]:
    """The REST pages (1-based, newest first) the commits of a window of
    ``total`` commits are read from (RFC-0021).

    The window fills ``P = ceil(total / COMMIT_PAGE_SIZE)`` pages. Up to
    ``k = COMMIT_CAP // COMMIT_PAGE_SIZE`` pages that is every page; above
    it, the ``k`` pages ``1 + floor(i * P / k)`` for ``i`` in ``0..k-1``: page
    1 (the newest commits, which vitality and the last-commit date read) and
    then one page every ``P / k`` pages, so each read page stands for the
    same share of the window (``P / k`` pages, rounded) and the year's oldest
    months are read as densely as its newest.
    """
    pages_total = -(-total // COMMIT_PAGE_SIZE)
    k = COMMIT_CAP // COMMIT_PAGE_SIZE
    if pages_total <= k:
        return list(range(1, pages_total + 1))
    return [1 + (i * pages_total) // k for i in range(k)]


def _window_data(rows: list[dict[str, Any]], since: str, *, total: Optional[int],
                 truncated: bool = False, pages: Optional[list[int]] = None) -> dict[str, Any]:
    """The ``commits_365d`` envelope ``data`` :func:`collect_commits_365d`
    records (``porchlamp.core.commit_domains.commit_window`` reads it)."""
    data: dict[str, Any] = {
        "count": len(rows),
        "cap": COMMIT_CAP,
        "truncated": truncated,
        "since": since,
        "total": total,
        "sampled": pages is not None,
    }
    if pages is not None and total is not None:
        data["pages_total"] = -(-total // COMMIT_PAGE_SIZE)
        data["sample_pages"] = pages
    data["rows"] = rows
    return data


def _merged_with_delta(
    fetch_page: Callable[[int, str], Sequence[Mapping[str, Any]]],
    since: str,
    now: datetime,
    existing_rows: Sequence[Mapping[str, Any]],
) -> Optional[list[dict[str, Any]]]:
    """W6-4: the existing whole window plus the commits pushed since, or
    ``None`` when the delta does not meet a known commit within
    :data:`COMMIT_CAP` rows or the merge passes :data:`COMMIT_CAP` (the window
    then needs the sample a full collection takes)."""
    from porchlamp.collect import ledger

    known = {ledger._commit_key(row) for row in existing_rows}
    delta: list[dict[str, Any]] = []
    for page in range(1, COMMIT_CAP // COMMIT_PAGE_SIZE + 1):
        raw = list(fetch_page(page, since))
        shaped = shape_commit_rows(raw)
        delta.extend(shaped)
        if any(ledger._commit_key(row) in known for row in shaped) or len(raw) < COMMIT_PAGE_SIZE:
            merged = ledger.merge_commits_365d_delta(
                existing_rows, delta, now=now, window_days=COMMIT_WINDOW_DAYS, cap=COMMIT_CAP + 1)
            return merged if len(merged) <= COMMIT_CAP else None
    return None


def collect_commits_365d(
    fetch_page: Callable[[int, str], Sequence[Mapping[str, Any]]],
    now: Optional[datetime] = None,
    *,
    fetch_total: Optional[Callable[[str], int]] = None,
    existing_rows: Optional[Sequence[Mapping[str, Any]]] = None,
    existing_truncated: bool = False,
) -> dict[str, Any]:
    """Collect the trailing 365 d of commits, newest first (D-IN-4
    Commits, RFC-0021).

    ``fetch_page(page, since)`` is the transport boundary: it must return
    the raw commit objects of one page of the
    ``repos/{owner}/{name}/commits`` listing (page >= 1, ``since`` the
    ISO-8601 window start). ``fetch_total(since)`` returns GitHub's count of
    the default-branch commits since then (:func:`commit_total_fetcher`); it
    is asked only when the first page is full.

    * A first page short of :data:`COMMIT_PAGE_SIZE` is the whole window.
    * When the count is at most :data:`COMMIT_CAP`, pages are read in order
      until a short page -- one page past the count's last, for commits pushed
      meanwhile: the whole window, ``total`` its row count.
    * Above it, the :func:`sample_pages` pages are read: ``sampled`` true,
      ``total`` the count, ``pages_total`` and ``sample_pages`` recorded (a
      commit a push moved onto a second read page is kept once).
    * A window that does not end where it should -- past the count's pages,
      or past :data:`COMMIT_CAP` rows when no ``fetch_total`` is given (a run
      told to keep no sample) -- is recorded ``truncated``: cut, so no
      windowed measure reads it.

    When ``existing_rows`` is supplied (W6-4 incremental ledger refresh), is
    not ``existing_truncated`` and every row carries its ``sha``, pages are
    read until a known commit and the delta is merged via
    :func:`porchlamp.collect.ledger.merge_commits_365d_delta`; a delta that
    meets no known commit or a merge past :data:`COMMIT_CAP` falls back to a
    full collection, and so do rows without a ``sha`` (their fallback key
    never matches a new row's sha, so every commit would be kept twice).
    """
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=COMMIT_WINDOW_DAYS)).isoformat()

    if existing_rows and not existing_truncated and all(
            isinstance(row, Mapping) and row.get("sha") for row in existing_rows):
        merged = _merged_with_delta(fetch_page, since, now, existing_rows)
        if merged is not None:
            return _window_data(merged, since, total=len(merged))

    first = list(fetch_page(1, since))
    rows = shape_commit_rows(first)
    if len(first) < COMMIT_PAGE_SIZE:
        return _window_data(rows, since, total=len(rows))
    total = fetch_total(since) if fetch_total is not None else None
    if total is not None and total > COMMIT_CAP:
        pages = sample_pages(total)
        seen = {row["sha"] for row in rows if row["sha"]}
        for page in pages[1:]:
            for row in shape_commit_rows(list(fetch_page(page, since))):
                if row["sha"] and row["sha"] in seen:
                    continue
                seen.add(row["sha"])
                rows.append(row)
        return _window_data(rows, since, total=total, pages=pages)
    last_page = (-(-total // COMMIT_PAGE_SIZE) + 1 if total is not None
                 else COMMIT_CAP // COMMIT_PAGE_SIZE)
    for page in range(2, last_page + 1):
        raw = list(fetch_page(page, since))
        rows.extend(shape_commit_rows(raw))
        if len(raw) < COMMIT_PAGE_SIZE:
            return _window_data(rows, since, total=len(rows))
    return _window_data(rows, since, total=total, truncated=True)


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

    Returns False for a ``sampled`` record (RFC-0021): its rows are pages
    spread over the year with gaps between them, not the newest commits in
    order. Otherwise True when ``c365_data`` is a Mapping with a commit list,
    and either:
    - ``truncated`` is False,
    - the list contains >= 300 commits, or
    - the oldest commit date is <= ``now - window_days``.
    """
    if not isinstance(c365_data, Mapping):
        return False
    commits = _extract_c365_commits(c365_data)
    if not isinstance(commits, list):
        return False
    if c365_data.get("sampled") is True:
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

