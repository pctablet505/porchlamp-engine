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
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional, Sequence

#: 05-W1-5: EF and bus factor are computed over the trailing 365 d.
COMMIT_WINDOW_DAYS: int = 365
#: 05-W1-5: at most 1,000 commits per repo are collected (newest first).
COMMIT_CAP: int = 1000
#: REST listing page size (GitHub max per_page for commits is 100).
COMMIT_PAGE_SIZE: int = 100
#: D-GATE-4: vitality counts the trailing 180 d.
VITALITY_WINDOW_DAYS: int = 180
#: GitHub private-email domain: its local part is an account id (or
#: ``<id>+<login>``), so the domain maps to the author's login, never to an
#: org of its own (spec 3.2).
NOREPLY_DOMAIN: str = "users.noreply.github.com"

#: Personal email providers: each author on one of these domains counts as
#: their own one-person org, not the provider as an org (spec 3.2, the
#: "gmail, outlook, proton, users.noreply..." family; ``github.com`` is
#: included because every ``<login>@github.com`` commit address belongs to
#: one individual). Exact domain match only (no wildcards, no substring
#: matching -- F12).
PERSONAL_DOMAINS: frozenset[str] = frozenset({
    "126.com",
    "163.com",
    "aol.com",
    "att.net",
    "attmail.com",
    "charter.net",
    "fastmail.com",
    "gmx.com",
    "gmx.de",
    "gmx.net",
    "github.com",
    "gmail.com",
    "googlemail.com",
    "hey.com",
    "hotmail.com",
    "icloud.com",
    "live.com",
    "mac.com",
    "mail.com",
    "me.com",
    "msn.com",
    "outlook.com",
    "outlook.es",
    "outlook.fr",
    "pm.me",
    "posteo.de",
    "posteo.net",
    "proton.me",
    "protonmail.com",
    "qq.com",
    "rocketmail.com",
    "sbcglobal.net",
    "sina.cn",
    "sina.com",
    "sohu.com",
    "verizon.net",
    "web.de",
    "yahoo.com",
    "yahoo.co.uk",
    "yahoo.com.au",
    "ymail.com",
    "yandex.com",
    "yandex.ru",
    "zoho.com",
})

#: D-IN-1 exact-match bot logins (compared after lowercasing the recorded
#: login; the ``[bot]`` suffix rule is separate, see :func:`is_bot`).
KNOWN_BOT_LOGINS: frozenset[str] = frozenset({
    "allcontributors",
    "cla-bot",
    "claude",
    "codecov",
    "copilot",
    "dependabot",
    "easycla",
    "github-actions",
    "google-cla",
    "greenkeeper",
    "imgbot",
    "mergify",
    "netlify",
    "pre-commit-ci",
    "renovate",
    "semantic-release",
    "snyk-bot",
    "sonarcloud",
    "stale",
    "vercel",
})


@dataclass(frozen=True)
class CommitActor:
    """One commit author with the three typed fields the collector records.

    ``login`` is the linked GitHub account ("" when the commit has no linked
    account), ``email`` is the git commit-author email, and ``actor_type``
    is the GitHub actor type ("" when unlinked).
    """

    login: str
    email: str
    actor_type: str


@dataclass(frozen=True)
class CommitRecord:
    """One window commit: a typed actor plus its git author date (UTC).

    ``date`` is None when the envelope row recorded no author date.
    """

    actor: CommitActor
    date: Optional[datetime]


def _parse_dt(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 author date; naive values are taken as UTC."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def is_bot(actor: CommitActor) -> bool:
    """D-IN-1: True when the actor is a bot.

    ``actor_type == "Bot"``, a login ending in ``[bot]``, or an exact-match
    login in :data:`KNOWN_BOT_LOGINS`. No substring matching (F12).
    """
    if actor.actor_type == "Bot":
        return True
    login = actor.login.lower()
    return login.endswith("[bot]") or login in KNOWN_BOT_LOGINS


def org_key(actor: CommitActor) -> Optional[str]:
    """The org a commit author's commits count toward (spec 3.2).

    Corporate domains count as one org per domain.
    ``users.noreply.github.com`` maps to the author's login (the domain is
    never an org of its own; None when the commit has no linked login).
    Personal domains count as one org per author (the login, else the
    lowercased email address). None when no email is recorded.
    """
    email = actor.email.strip().lower()
    if "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1]
    if not domain:
        return None
    if domain == NOREPLY_DOMAIN:
        return actor.login.strip() or None
    if domain in PERSONAL_DOMAINS:
        return actor.login.strip() or email
    return domain


def author_key(actor: CommitActor) -> Optional[str]:
    """The individual an author commit belongs to: the login if recorded,
    else the lowercased email address; None when neither is recorded."""
    login = actor.login.strip()
    if login:
        return login
    email = actor.email.strip().lower()
    return email or None


def shape_commit_rows(raw_commits: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Shape raw GitHub REST commit objects into envelope rows.

    Each row is ``{"date", "author_email", "author_login",
    "author_type"}``: ``date`` and ``author_email`` come from the git commit
    author, ``author_login`` and ``author_type`` from the linked GitHub
    account ("" when the commit has none).
    """
    rows: list[dict[str, Any]] = []
    for commit in raw_commits:
        git_author = (commit.get("commit") or {}).get("author") or {}
        account = commit.get("author") or {}
        rows.append({
            "date": git_author.get("date"),
            "author_email": git_author.get("email") or "",
            "author_login": account.get("login") or "",
            "author_type": account.get("type") or "",
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


def records_from_rows(rows: Sequence[Mapping[str, Any]]) -> list[CommitRecord]:
    """Parse ``commits_365d`` envelope rows into typed :class:`CommitRecord`."""
    records: list[CommitRecord] = []
    for row in rows:
        records.append(CommitRecord(
            actor=CommitActor(
                login=str(row.get("author_login") or ""),
                email=str(row.get("author_email") or ""),
                actor_type=str(row.get("author_type") or ""),
            ),
            date=_parse_dt(row.get("date")),
        ))
    return records


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
