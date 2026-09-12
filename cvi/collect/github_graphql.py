"""W1-3: windowed PR collection with typed actors and review threads.

Collects the PRs a repository opened in the trailing 180 days (D-IN-4,
``docs/v2/02-RANKING-DESIGN.md`` section 3) from the GitHub GraphQL API and
derives the per-PR responsiveness inputs of ``02`` section 4:

* D-IN-2 — ``first_substantive_response_at`` / ``ttfhr_hours`` (parameter
  1.1): the earliest review (any state) or comment of at least 40 characters
  posted by a non-author, non-bot account whose ``authorAssociation`` is
  ``MEMBER``, ``OWNER`` or ``COLLABORATOR``;
* 02 §4.3 — ``substantive_review``: at least one non-bot review with inline
  comments (``comments.totalCount >= 1``) or a review body of at least 80
  characters;
* 02 §4 1.1/1.4/1.5 inputs — ``decided`` (``closedAt`` set) and ``merged``
  (``mergedAt`` set) flags plus the ``is_external`` / ``is_bot_author``
  actor classifications and ``review_threads_total``.

Bot typing follows D-IN-1: ``__typename == "Bot"``, a login ending in
``[bot]``, or an exact-match login in the frozen list below — the v1
substring matching is removed (F12). The D-IN-1/2/4 and §4 constants below
transcribe the design verbatim; W2-1 moves them to the v2 frozen constants
module (``cvi.core.constants_v2``) — until then this module is their single
definition (R-2).
"""

from __future__ import annotations

import json
import statistics
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Protocol

from cvi.collect.envelope import write_envelope

# --------------------------------------------------------------------------- #
# D-IN-4 / D-IN-2 / 02 §4 constants (docs/v2/02-RANKING-DESIGN.md)            #
# --------------------------------------------------------------------------- #

#: D-IN-4: PRs opened in the trailing 180 days.
PR_WINDOW_DAYS: int = 180
#: D-IN-4: at most the 500 newest PRs; ``truncated`` when the cap is hit.
PR_CAP: int = 500
#: One GraphQL page (``first``); the cap is reached in five full pages.
PAGE_SIZE: int = 100
#: D-IN-2: a maintainer comment counts as a response at exactly 40 chars.
SUBSTANTIVE_COMMENT_CHARS: int = 40
#: 02 §4.3: a review counts as substantive at an 80-char body or 1 inline.
SUBSTANTIVE_REVIEW_BODY_CHARS: int = 80

#: D-IN-1: exact-match bot logins (case-insensitive). Substring matching is
#: removed (F12); ``__typename == "Bot"`` and the ``[bot]`` suffix cover the
#: rest.
BOT_LOGINS: frozenset[str] = frozenset({
    "dependabot", "renovate", "github-actions", "codecov", "stale",
    "google-cla", "cla-bot", "easycla", "mergify", "pre-commit-ci",
    "allcontributors", "semantic-release", "imgbot", "netlify",
    "vercel", "sonarcloud", "snyk-bot", "greenkeeper", "copilot", "claude",
})

#: D-IN-2: associations that mark a substantive human response.
MAINTAINER_ASSOCIATIONS: frozenset[str] = frozenset({"MEMBER", "OWNER", "COLLABORATOR"})
#: External (non-maintainer) associations, matching the v1 ``EXTERNAL_ASSOC``
#: set used by ``scripts/derive.py`` for the same fact.
EXTERNAL_ASSOCIATIONS: frozenset[str] = frozenset(
    {"NONE", "FIRST_TIMER", "FIRST_TIME_CONTRIBUTOR", "CONTRIBUTOR"})

#: The windowed-PR query: PRs in all three states, newest first, with the
#: typed actors (``__typename``), reviews, issue-style comments and the
#: review-thread total. ``rateLimit.cost`` measures the page's point cost.
PR_WINDOW_QUERY: str = (
    "query PrWindow($owner: String!, $name: String!, $first: Int, $after: String) {"
    " rateLimit { cost }"
    " r: repository(owner: $owner, name: $name) {"
    " pullRequests(states: [OPEN, MERGED, CLOSED], first: $first, after: $after,"
    " orderBy: {field: CREATED_AT, direction: DESC}) {"
    " pageInfo { hasNextPage endCursor }"
    " nodes { number createdAt closedAt mergedAt state"
    " author { __typename login ... on User { createdAt } } authorAssociation"
    " reviews(first: 10) { nodes { author { __typename login } state body createdAt"
    " authorAssociation comments { totalCount } } }"
    " comments(first: 20) { nodes { author { __typename login } authorAssociation"
    " bodyText createdAt } }"
    " reviewThreads { totalCount } }"
    " } } }"
)


# --------------------------------------------------------------------------- #
# Transports                                                                    #
# --------------------------------------------------------------------------- #

class GraphqlTransport(Protocol):
    """One GraphQL execution; the live implementation shells to ``gh``."""

    def execute(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Run one query and return the full response document."""


class GhCliTransport:
    """Live transport via ``gh api graphql`` (the gh auth token is used).

    Per ``gh api`` semantics, every field other than ``query`` is a GraphQL
    variable; integer values need ``-F`` (JSON type conversion) while
    strings go through ``-f``.
    """

    def __init__(self, timeout_seconds: int = 90) -> None:
        self.timeout_seconds = timeout_seconds

    def execute(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        """Run one query through ``gh api graphql`` and raise on rc != 0."""
        cmd: list[str] = ["gh", "api", "graphql", "-f", f"query={query}"]
        for key, value in variables.items():
            flag = "-F" if isinstance(value, int) and not isinstance(value, bool) else "-f"
            cmd += [flag, f"{key}={value}"]
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=self.timeout_seconds)
        if out.returncode != 0:
            raise RuntimeError(
                f"gh api graphql rc={out.returncode}: {out.stderr[:160]}")
        return json.loads(out.stdout)


# --------------------------------------------------------------------------- #
# Actor typing (D-IN-1)                                                       #
# --------------------------------------------------------------------------- #

def is_bot_actor(typename: Optional[str], login: Optional[str]) -> bool:
    """D-IN-1: an actor is a bot when ``__typename`` is ``"Bot"``, the login
    ends in ``[bot]``, or the login (case-insensitive) is in the exact-match
    frozen :data:`BOT_LOGINS` list. Substring matching is removed (F12)."""
    if (typename or "").lower() == "bot":
        return True
    low = (login or "").lower()
    return low.endswith("[bot]") or low in BOT_LOGINS


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp; None when absent or unparseable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _association(value: Any) -> str:
    return str(value or "").upper()


def _is_maintainer(login: Any, association: Any, author_login: str) -> bool:
    """D-IN-2 responder: non-bot, not the PR author, maintainer association."""
    low = str(login or "").lower()
    if is_bot_actor(None, login):
        return False
    if low and low == author_login:
        return False
    return _association(association) in MAINTAINER_ASSOCIATIONS


# --------------------------------------------------------------------------- #
# Per-PR derivation (D-IN-2, 02 §4.3) over recorded GraphQL nodes             #
# --------------------------------------------------------------------------- #

def derive_pr_window(prs: Sequence[dict]) -> list[dict]:
    """Derive the per-PR responsiveness inputs from recorded PR nodes.

    ``prs`` are the raw ``pullRequests.nodes`` records of one windowed
    fetch (the shape in :data:`PR_WINDOW_QUERY`). Each result dict carries
    ``number``, ``is_external``, ``is_bot_author``, ``decided``, ``merged``,
    ``first_substantive_response_at`` / ``_kind``, ``ttfhr_hours`` and
    ``substantive_review``. A response candidate is a maintainer
    (D-IN-2) review in any state or a comment of at least 40 characters,
    never the PR author and never a bot; ``ttfhr_hours`` is the opening-to-
    response span in hours (None when there is no candidate or the recorded
    response predates the opening). ``substantive_review`` (02 §4.3) is any
    non-bot review with 1+ inline comments or an 80+ character body.
    """
    out: list[dict] = []
    for pr in prs:
        author = pr.get("author") or {}
        author_login = str(author.get("login") or "").lower()
        best_at: Optional[str] = None
        best_kind: Optional[str] = None
        best_dt: Optional[datetime] = None
        for kind in ("review", "comment"):
            source = "reviews" if kind == "review" else "comments"
            for raw in ((pr.get(source) or {}).get("nodes") or []):
                if kind == "review":
                    ok = _is_maintainer(
                        (raw.get("author") or {}).get("login"),
                        raw.get("authorAssociation"), author_login)
                else:
                    ok = _is_maintainer(
                        (raw.get("author") or {}).get("login"),
                        raw.get("authorAssociation"), author_login) \
                        and len(str(raw.get("bodyText") or "")) >= SUBSTANTIVE_COMMENT_CHARS
                if not ok:
                    continue
                dt = _parse_iso(raw.get("createdAt"))
                if dt is not None and (best_dt is None or dt < best_dt):
                    best_dt, best_at, best_kind = dt, raw["createdAt"], kind
        ttfhr: Optional[float] = None
        if best_dt is not None:
            created_dt = _parse_iso(pr.get("createdAt"))
            if created_dt is not None and best_dt >= created_dt:
                ttfhr = (best_dt - created_dt).total_seconds() / 3600.0
        substantive_review = any(
            not is_bot_actor((raw.get("author") or {}).get("__typename"),
                             (raw.get("author") or {}).get("login"))
            and (int((raw.get("comments") or {}).get("totalCount") or 0) >= 1
                 or len(str(raw.get("body") or "")) >= SUBSTANTIVE_REVIEW_BODY_CHARS)
            for raw in ((pr.get("reviews") or {}).get("nodes") or []))
        out.append({
            "number": pr.get("number"),
            "is_external": _association(pr.get("authorAssociation")) in EXTERNAL_ASSOCIATIONS,
            "is_bot_author": is_bot_actor(author.get("__typename"),
                                          author.get("login")),
            "decided": pr.get("closedAt") is not None,
            "merged": pr.get("mergedAt") is not None,
            "first_substantive_response_at": best_at,
            "first_substantive_response_kind": best_kind,
            "ttfhr_hours": ttfhr,
            "substantive_review": substantive_review,
        })
    return out


# --------------------------------------------------------------------------- #
# Windowed fetch (D-IN-4)                                                     #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PrWindowResult:
    """One windowed PR fetch: recorded nodes, truncation and point cost."""

    owner: str
    name: str
    now: datetime
    truncated: bool
    cost_points: int
    prs: tuple[dict, ...]


def fetch_pr_window(owner: str, name: str, now: datetime,
                    transport: GraphqlTransport,
                    page_size: int = PAGE_SIZE) -> PrWindowResult:
    """Fetch the PRs ``owner/name`` opened in the trailing 180 days.

    Pages newest-first and stops when a returned PR was opened before the
    window start (the boundary is exclusive, a PR opened exactly at
    ``now - 180 d`` is inside), when no page remains, or when :data:`PR_CAP`
    records are collected — in which case ``truncated`` is True (D-IN-4).
    ``cost_points`` sums each page's ``rateLimit.cost``. Raises
    ``RuntimeError`` on GraphQL errors, a missing repository, or transport
    failure.
    """
    window_start = now - timedelta(days=PR_WINDOW_DAYS)
    prs: list[dict] = []
    cost = 0
    after: Optional[str] = None
    while len(prs) < PR_CAP:
        variables: dict[str, Any] = {"owner": owner, "name": name, "first": page_size}
        if after is not None:
            variables["after"] = after
        doc = transport.execute(PR_WINDOW_QUERY, variables)
        errors = doc.get("errors")
        if errors:
            raise RuntimeError(f"graphql errors for {owner}/{name}: "
                               f"{str(errors[0].get('message'))[:160]}")
        data = doc.get("data") or {}
        cost += int((data.get("rateLimit") or {}).get("cost") or 0)
        if (data.get("r") or None) is None:
            raise RuntimeError(f"repository not found: {owner}/{name}")
        connection = (data["r"].get("pullRequests") or {})
        for raw in connection.get("nodes") or []:
            created = _parse_iso(raw.get("createdAt"))
            if created is not None and created < window_start:
                return PrWindowResult(owner, name, now, False, cost, tuple(prs))
            prs.append(raw)
            if len(prs) >= PR_CAP:
                break
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage") or not page_info.get("endCursor"):
            break
        after = page_info["endCursor"]
    return PrWindowResult(owner, name, now, len(prs) >= PR_CAP, cost, tuple(prs))


# --------------------------------------------------------------------------- #
# Envelope entry point (called from the scripts/gather.py hook)               #
# --------------------------------------------------------------------------- #

def collect_pr_window(raw_dir: Path, owner: str, name: str,
                      now: Optional[datetime] = None,
                      transport: Optional[GraphqlTransport] = None,
                      force: bool = False,
                      page_size: int = PAGE_SIZE) -> str:
    """Collect the windowed PR envelope for one repository.

    Writes ``raw_dir/pr_window/<owner>__<name>.json`` — one envelope whose
    ``data`` carries the window metadata (``count``, ``truncated``,
    ``cost_points``, ``window_days``, ``cap``), the recorded PR nodes and
    the per-PR derivation — and returns ``"ok"``. Returns ``"skip"`` when
    the envelope already exists and ``force`` is False. ``page_size`` is
    the GraphQL ``first`` per page; drop it for very active repositories
    whose full page exceeds the server-side query time budget.
    """
    key = f"{owner}__{name}"
    target = raw_dir / "pr_window" / f"{key}.json"
    if target.is_file() and not force:
        return "skip"
    now = now or datetime.now(timezone.utc)
    result = fetch_pr_window(owner, name, now, transport or GhCliTransport(),
                          page_size=page_size)
    data: dict[str, Any] = {
        "window_anchor": now.isoformat(),
        "window_days": PR_WINDOW_DAYS,
        "cap": PR_CAP,
        "count": len(result.prs),
        "truncated": result.truncated,
        "cost_points": result.cost_points,
        "prs": list(result.prs),
        "derived": derive_pr_window(result.prs),
    }
    write_envelope(target, source="github-graphql:pr-window", data=data,
                   fetched_at=now.isoformat())
    return "ok"


# --------------------------------------------------------------------------- #
# Bundle fields (called from the scripts/derive.py hook)                      #
# --------------------------------------------------------------------------- #

#: The six bundle fields the derive hook sets from the pr_window envelope.
PR_WINDOW_BUNDLE_FIELDS: tuple[str, ...] = (
    "pr_window_count",
    "pr_window_truncated",
    "pr_window_cost_points",
    "pr_window_external_measured_latency_count",
    "pr_window_ttfhr_median_hours",
    "pr_window_substantive_review_rate",
)


def derive_pr_window_fields(prw: Optional[dict]) -> dict[str, tuple[Any, str]]:
    """Bundle fields from a recorded ``pr_window`` envelope ``data``.

    Returns ``{field: (value, confidence)}`` for
    :data:`PR_WINDOW_BUNDLE_FIELDS`. The envelope-recorded facts (count,
    truncation, point cost) are ``verified``; the statistics computed here
    are ``derived``: the count of external PRs with a measured TTFHR, the
    TTFHR median over them (02 §4 1.1) and the share of decided external PRs
    with a substantive review (02 §4 4.3). Empty dict when ``prw`` is None.
    """
    if not prw:
        return {}
    derived = prw.get("derived") or []
    external = [d for d in derived if d.get("is_external")]
    with_latency = [d["ttfhr_hours"] for d in external
                    if d.get("ttfhr_hours") is not None]
    decided_external = [d for d in external if d.get("decided")]
    substantive = [d for d in decided_external if d.get("substantive_review")]
    return {
        "pr_window_count": (int(prw.get("count") or 0), "verified"),
        "pr_window_truncated": (bool(prw.get("truncated")), "verified"),
        "pr_window_cost_points": (int(prw.get("cost_points") or 0), "verified"),
        "pr_window_external_measured_latency_count": (len(with_latency), "derived"),
        "pr_window_ttfhr_median_hours": (
            round(statistics.median(with_latency), 4), "derived") if with_latency
        else (None, "derived"),
        "pr_window_substantive_review_rate": (
            round(len(substantive) / len(decided_external), 4), "derived")
        if decided_external else (None, "derived"),
    }
