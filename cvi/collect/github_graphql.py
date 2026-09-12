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
import random
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



# --------------------------------------------------------------------------- #
# W1-4: author history and newcomer cohorts (D-IN-3/D-IN-4, 02 §4 1.2/1.3/
# 5.2/5.3)
# --------------------------------------------------------------------------- #
#
# Data plan (docs/v2/05-WORK-PLAN.md, packet W1-4). One envelope per repo,
# ``raw_dir/author_history/<owner>__<name>.json``:
#
# * a 365-d window of PRs (cap 500 newest, ``truncated`` flag) — the same
#   fetch serves BOTH cohorts. The decision cohort ([anchor-270 d,
#   anchor-90 d]) is read from this window rather than from the W1-3
#   ``pr_window`` envelope (180 d): the 365-d window strictly dominates it
#   (same page cap, longer target, same newest-first ordering, so its
#   recorded floor is never nearer to ``anchor`` than the 180-d window's),
#   so building the decision cohort from it covers the full 270 d for free
#   instead of silently narrowing to [180 d, 90 d]. ``decision_cohort_
#   coverage_gap_days`` records the residual, non-zero only when this
#   window itself truncates before reaching 270 d back;
# * an external, non-bot author whose first recorded PR in the window was
#   opened in [anchor-365 d, anchor-180 d] is a newcomer-cohort author
#   (D-IN-4); at most 20 are sampled per repo — sorted by login, then a
#   fixed-seed random draw over that sorted list (the packet's own words),
#   not the chronologically earliest 20 (see :func:`_select_newcomer_sample`);
# * per sampled author, and per external decision-cohort author: one search
#   (the author's newest 30 PRs in the repo, ``repo:o/n is:pr
#   author:login``) and one user record (``createdAt``, and
#   ``contributionsCollection.contributionCalendar.totalContributions`` as
#   the public-event proxy — GitHub's profile "public events" count has no
#   current API; this is the closest available aggregate, GitHub's own
#   default window of roughly the trailing year). A search or user lookup
#   that fails with a GraphQL error (a deleted or renamed account is the
#   expected case) degrades that one author to no recorded history/account
#   rather than failing the whole repo's collection — D-IN-3 then reads
#   them as ineligible (an absent input never reads as success), which is
#   the correct, conservative default rather than a defect;
# * derivation (``derive_cohort_fields``, pure over the recorded envelope):
#   for each of DR_90 (1.2), MR_first (1.3), newcomer conversion (5.2) and
#   RR_90 (5.3) both a RAW rate (over the identified cohort, no D-IN-3
#   eligibility or cap) and the D-IN-3-adjusted rate (eligible authors only,
#   each capped at floor(20% * n) items, oldest kept) are stored, per the
#   packet's "both raw and capped counts are stored" instruction; W3-1
#   generalizes D-IN-3 application to every rate parameter and may
#   supersede the capped fields here with a shared implementation.
#
# Documented approximations (R-5): "no previously merged PR" (MR_first) is a
# statement about the recorded history only — the 365-d window plus the
# per-author newest-30 search — an author with more than 30 total PRs may
# have an older merged PR neither source reached; a truncated newcomer band
# excludes candidates whose oldest recorded PR sits within
# ``TRUNCATION_MARGIN_DAYS`` of the truncation floor, since an even older,
# unrecorded PR would misclassify them as a newcomer.
#
# The D-IN constants below are the single definition until W2-1's frozen
# constants module takes over (R-2), mirroring the W1-3 constants above.

#: D-IN-4: the author-history window — 365 d of PRs.
AUTHOR_WINDOW_DAYS: int = 365
#: D-IN-4: at most the 500 newest PRs of the author-history window.
AUTHOR_WINDOW_CAP: int = 500
#: D-IN-4: newcomer band, older edge — the first PR was opened at most 365 d ago.
NEWCOMER_BAND_START_DAYS: int = 365
#: D-IN-4: newcomer band, newer edge — the first PR was opened at least 180 d ago.
NEWCOMER_BAND_END_DAYS: int = 180
#: D-IN-4: at most 20 sampled newcomer-cohort authors per repo.
NEWCOMER_SAMPLE_SIZE: int = 20
#: D-IN-4's own words: "sorted login, then a seeded choice" — the fixed
#: seed for the ``random.Random(...).sample`` draw over the login-sorted
#: candidate pool. Fixed once, never derived from wall-clock time or a
#: repo-specific value (both would break exact reproducibility, D-EVID-1).
NEWCOMER_SAMPLE_SEED: int = 20260912
#: Per-author history: the 30 newest of the author's PRs in the repo.
AUTHOR_SEARCH_FIRST: int = 30
#: Truncation guard: a newcomer candidate whose oldest recorded PR is this
#: close to the truncated window's floor may have an unrecorded older PR.
TRUNCATION_MARGIN_DAYS: int = 30
#: D-IN-4: decision-rate cohort, older edge — opened at most 270 d ago.
DECISION_COHORT_START_DAYS: int = 270
#: D-IN-4/1.2: decision-cohort newer edge AND the DR_90 decide-by window —
#: the same fact from two angles: a cohort PR has had exactly this long to
#: be decided (D-IN-4's stated rationale for the cohort's near boundary).
DECISION_WINDOW_DAYS: int = 90
#: D-IN-3: the account must be at least 90 days old at PR creation.
MIN_ACCOUNT_AGE_DAYS: int = 90
#: D-IN-3: the account must have at least 5 public events.
MIN_PUBLIC_EVENTS: int = 5
#: D-IN-3: one author counts for at most floor(20% * n) of any rate's
#: numerator and denominator.
AUTHOR_CAP_FRACTION: float = 0.20
#: 5.2: the first PR converted when merged within 180 d of opening.
NEWCOMER_CONVERT_WINDOW_DAYS: int = 180
#: 5.3: a return needs a second PR within 90 d after the first PR's merge.
RETURN_WINDOW_DAYS: int = 90

#: The 365-d window query: PRs in all three states, newest first, light
#: fields (no reviews/comments — the author section does not derive
#: D-IN-2). ``rateLimit`` carries both fields the packet's acceptance
#: criterion pastes: ``cost`` and ``remaining``.
AUTHOR_WINDOW_QUERY: str = (
    "query AuthorWindow($owner: String!, $name: String!, $first: Int, $after: String) {"
    " rateLimit { cost remaining }"
    " r: repository(owner: $owner, name: $name) {"
    " pullRequests(states: [OPEN, MERGED, CLOSED], first: $first, after: $after,"
    " orderBy: {field: CREATED_AT, direction: DESC}) {"
    " pageInfo { hasNextPage endCursor }"
    " nodes { number createdAt closedAt mergedAt state"
    " author { __typename login ... on User { createdAt } } authorAssociation }"
    " } } }"
)

#: Per-author PR history: the author's newest PRs in the repo (the search
#: type ISSUE covers pull requests; the query is per D-IN-4 / 02 §4 1.3).
AUTHOR_PRS_SEARCH_QUERY: str = (
    "query AuthorPrsSearch($q: String!, $first: Int) {"
    " rateLimit { cost remaining }"
    " s: search(query: $q, type: ISSUE, first: $first) {"
    " nodes { ... on PullRequest { number createdAt closedAt mergedAt state } }"
    " } }"
)

#: Per-author account record: creation date and the public-event proxy
#: (module note above: GitHub's own default ~1 y contribution window).
AUTHOR_USER_QUERY: str = (
    "query AuthorUser($login: String!) {"
    " rateLimit { cost remaining }"
    " u: user(login: $login) {"
    " createdAt"
    " contributionsCollection { contributionCalendar { totalContributions } } } }"
)


def _raise_on_graphql_errors(doc: dict[str, Any], context: str) -> None:
    """Raise ``RuntimeError`` when a response document carries errors."""
    errors = doc.get("errors")
    if errors:
        raise RuntimeError(f"graphql errors for {context}: "
                           f"{str(errors[0].get('message'))[:160]}")


def _login_of(pr: dict) -> str:
    return str((pr.get("author") or {}).get("login") or "").lower()


def _is_external_nonbot(pr: dict) -> bool:
    """D-IN-1/D-IN-4: an external, non-bot PR author (the population every
    cohort quantity in this section is drawn from)."""
    author = pr.get("author") or {}
    if is_bot_actor(author.get("__typename"), author.get("login")):
        return False
    return _association(pr.get("authorAssociation")) in EXTERNAL_ASSOCIATIONS


@dataclass(frozen=True)
class AuthorHistoryResult:
    """One author-history fetch: the recorded 365-d window and its cost."""

    owner: str
    name: str
    now: datetime
    truncated: bool
    cost_points: int
    remaining: Optional[int]
    prs: tuple[dict, ...]


def fetch_author_window(owner: str, name: str, now: datetime,
                        transport: GraphqlTransport,
                        page_size: int = PAGE_SIZE) -> AuthorHistoryResult:
    """Fetch the PRs ``owner/name`` opened in the trailing 365 days.

    Pages newest-first and stops when a returned PR was opened before the
    window start (a PR opened exactly at ``now - 365 d`` is inside), when no
    page remains, or when :data:`AUTHOR_WINDOW_CAP` records are collected —
    in which case ``truncated`` is True (D-IN-4). ``cost_points`` sums each
    page's ``rateLimit.cost``; ``remaining`` is the last-observed
    ``rateLimit.remaining``. Raises ``RuntimeError`` on GraphQL errors, a
    missing repository, or transport failure — a broken window fetch leaves
    no cohort computable for the whole repo, unlike a single bad per-author
    lookup (see :func:`collect_author_history`).
    """
    window_start = now - timedelta(days=AUTHOR_WINDOW_DAYS)
    prs: list[dict] = []
    cost = 0
    remaining: Optional[int] = None
    after: Optional[str] = None
    while len(prs) < AUTHOR_WINDOW_CAP:
        variables: dict[str, Any] = {"owner": owner, "name": name, "first": page_size}
        if after is not None:
            variables["after"] = after
        doc = transport.execute(AUTHOR_WINDOW_QUERY, variables)
        _raise_on_graphql_errors(doc, f"author window {owner}/{name}")
        data = doc.get("data") or {}
        rl = data.get("rateLimit") or {}
        cost += int(rl.get("cost") or 0)
        if rl.get("remaining") is not None:
            remaining = int(rl["remaining"])
        if (data.get("r") or None) is None:
            raise RuntimeError(f"repository not found: {owner}/{name}")
        connection = data["r"].get("pullRequests") or {}
        for raw in connection.get("nodes") or []:
            created = _parse_iso(raw.get("createdAt"))
            if created is not None and created < window_start:
                return AuthorHistoryResult(owner, name, now, False, cost, remaining, tuple(prs))
            prs.append(raw)
            if len(prs) >= AUTHOR_WINDOW_CAP:
                break
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage") or not page_info.get("endCursor"):
            break
        after = page_info["endCursor"]
    return AuthorHistoryResult(owner, name, now, len(prs) >= AUTHOR_WINDOW_CAP,
                               cost, remaining, tuple(prs))


def _select_newcomer_sample(prs: Sequence[dict], anchor: datetime,
                            truncated: bool) -> tuple[str, ...]:
    """Deterministic newcomer sample (the packet's own words: "sorted login,
    then a seeded choice"): external, non-bot authors whose first recorded
    PR in ``prs`` was opened in [anchor-365 d, anchor-180 d], sorted by
    login, then reduced to at most 20 by a fixed-seed
    ``random.Random(NEWCOMER_SAMPLE_SEED).sample`` draw over that sorted
    population (D-IN-4) — not the oldest 20, which would sample only the
    band's earliest slice every time and never its later months. The draw
    is a pure function of the candidate set: the same candidates always
    yield the same sample, in the same run or a different one.

    When the window is truncated, an author whose oldest recorded PR sits
    within :data:`TRUNCATION_MARGIN_DAYS` of the recorded window floor is
    excluded before sampling: the cap means older PRs may be unrecorded
    (module note).
    """
    band_start = anchor - timedelta(days=NEWCOMER_BAND_START_DAYS)
    band_end = anchor - timedelta(days=NEWCOMER_BAND_END_DAYS)
    oldest: dict[str, datetime] = {}
    for pr in prs:
        if not _is_external_nonbot(pr):
            continue
        login = _login_of(pr)
        created = _parse_iso(pr.get("createdAt"))
        if not login or created is None:
            continue
        current = oldest.get(login)
        if current is None or created < current:
            oldest[login] = created
    candidates = {login for login, created in oldest.items()
                 if band_start <= created <= band_end}
    if truncated and oldest:
        floor = min(oldest.values())
        candidates = {login for login in candidates
                     if oldest[login] >= floor + timedelta(days=TRUNCATION_MARGIN_DAYS)}
    sorted_candidates = sorted(candidates)
    if len(sorted_candidates) <= NEWCOMER_SAMPLE_SIZE:
        return tuple(sorted_candidates)
    chosen = random.Random(NEWCOMER_SAMPLE_SEED).sample(sorted_candidates, NEWCOMER_SAMPLE_SIZE)
    return tuple(sorted(chosen))


def _in_decision_band(pr: dict, anchor: datetime) -> bool:
    """D-IN-4: the PR was opened in [anchor-270 d, anchor-90 d]."""
    created = _parse_iso(pr.get("createdAt"))
    if created is None:
        return False
    age = anchor - created
    return timedelta(days=DECISION_WINDOW_DAYS) <= age <= timedelta(days=DECISION_COHORT_START_DAYS)


def _decision_cohort_logins(prs: Sequence[dict], anchor: datetime) -> tuple[str, ...]:
    """External, non-bot decision-cohort author logins from the recorded
    365-d window (PRs opened in [anchor-270 d, anchor-90 d])."""
    logins = {_login_of(pr) for pr in prs
             if _login_of(pr) and _in_decision_band(pr, anchor) and _is_external_nonbot(pr)}
    return tuple(sorted(logins))


def collect_author_history(raw_dir: Path, owner: str, name: str,
                           now: Optional[datetime] = None,
                           transport: Optional[GraphqlTransport] = None,
                           force: bool = False,
                           page_size: int = PAGE_SIZE) -> str:
    """Collect the author-history envelope for one repository.

    Writes ``raw_dir/author_history/<owner>__<name>.json`` — one envelope
    whose ``data`` carries the 365-d window metadata (``window_anchor``,
    ``window_days``, ``cap``, ``count``, ``truncated``, ``cost_points``,
    ``rate_limit_remaining``), the recorded PR nodes, the deterministic
    ``sampled_authors`` list, and the per-author ``author_records`` (account
    record plus the search's newest-30 PRs) for the union of the sampled
    newcomer authors and the external decision-cohort authors identified in
    the same window. Returns ``"ok"``, or ``"skip"`` when the envelope
    already exists and ``force`` is False.

    A search or user lookup that fails with a GraphQL error (module note:
    the expected case is a deleted or renamed account) degrades that one
    author's record to no history / no account facts rather than raising —
    one bad login does not lose the whole repo's collection. A sustained
    failure (for example rate-limiting reached partway through the author
    loop) surfaces the same way; ``cost_points`` and ``rate_limit_remaining``
    in the same envelope make that operationally distinguishable from
    genuine ineligibility.
    """
    key = f"{owner}__{name}"
    target = raw_dir / "author_history" / f"{key}.json"
    if target.is_file() and not force:
        return "skip"
    now = now or datetime.now(timezone.utc)
    t = transport or GhCliTransport()
    window = fetch_author_window(owner, name, now, t, page_size=page_size)
    cost = window.cost_points
    remaining = window.remaining
    sample = _select_newcomer_sample(window.prs, now, window.truncated)
    needed = sorted(set(_decision_cohort_logins(window.prs, now)) | set(sample))
    author_records: list[dict] = []
    for login in needed:
        record: dict[str, Any] = {"login": login, "account_created_at": None,
                                  "public_event_count": None, "prs": []}
        try:
            search_doc = t.execute(
                AUTHOR_PRS_SEARCH_QUERY,
                {"q": f"repo:{owner}/{name} is:pr author:{login}",
                 "first": AUTHOR_SEARCH_FIRST})
            _raise_on_graphql_errors(search_doc, f"search {owner}/{name} {login}")
            sdata = search_doc.get("data") or {}
            rl = sdata.get("rateLimit") or {}
            cost += int(rl.get("cost") or 0)
            if rl.get("remaining") is not None:
                remaining = int(rl["remaining"])
            record["prs"] = [
                {"number": raw.get("number"), "createdAt": raw.get("createdAt"),
                 "closedAt": raw.get("closedAt"), "mergedAt": raw.get("mergedAt"),
                 "state": raw.get("state")}
                for raw in ((sdata.get("s") or {}).get("nodes") or [])
            ]
        except RuntimeError:
            pass  # deleted/renamed account or a transient search failure: no history recorded
        try:
            user_doc = t.execute(AUTHOR_USER_QUERY, {"login": login})
            _raise_on_graphql_errors(user_doc, f"user {owner}/{name} {login}")
            udata = user_doc.get("data") or {}
            rl = udata.get("rateLimit") or {}
            cost += int(rl.get("cost") or 0)
            if rl.get("remaining") is not None:
                remaining = int(rl["remaining"])
            u = udata.get("u") or {}
            record["account_created_at"] = u.get("createdAt")
            record["public_event_count"] = (
                (u.get("contributionsCollection") or {}).get("contributionCalendar") or {}
            ).get("totalContributions")
        except RuntimeError:
            pass  # deleted/renamed account: eligibility falls back to no record (ineligible)
        author_records.append(record)
    data: dict[str, Any] = {
        "window_anchor": now.isoformat(),
        "window_days": AUTHOR_WINDOW_DAYS,
        "cap": AUTHOR_WINDOW_CAP,
        "count": len(window.prs),
        "truncated": window.truncated,
        "cost_points": cost,
        "rate_limit_remaining": remaining,
        "prs": list(window.prs),
        "sampled_authors": list(sample),
        "author_records": author_records,
    }
    write_envelope(target, source="github-graphql:author-history", data=data,
                   fetched_at=now.isoformat())
    return "ok"


def is_eligible_account(account_created_at: Optional[str],
                        pr_created_at: Optional[str],
                        public_event_count: Optional[int]) -> bool:
    """D-IN-3: the account is at least 90 days old at PR creation and has at
    least 5 public events. ``False`` whenever a recorded input is missing —
    an absent input never reads as success."""
    created = _parse_iso(account_created_at)
    pr = _parse_iso(pr_created_at)
    if created is None or pr is None or pr < created:
        return False
    if pr - created < timedelta(days=MIN_ACCOUNT_AGE_DAYS):
        return False
    return (public_event_count is not None
            and public_event_count >= MIN_PUBLIC_EVENTS)


def apply_author_cap(items: Sequence[tuple[str, datetime]]) -> tuple[int, ...]:
    """D-IN-3 per-author cap: the indices of the items that survive.

    ``items`` are ``(author_login, created_at)`` pairs of one rate's
    population; each author keeps at most ``floor(0.2 * len(items))`` of
    them, the most recent dropped first (the oldest kept). Populations
    under 5 have a cap of 0 and lose every item.
    """
    allowed = int(AUTHOR_CAP_FRACTION * len(items))
    if allowed <= 0:
        return ()
    slots: dict[str, list[int]] = {}
    for idx, (login, _created) in enumerate(items):
        slots.setdefault(login, []).append(idx)
    kept: list[int] = []
    for idxs in slots.values():
        idxs.sort(key=lambda i: items[i][1])  # oldest first
        kept.extend(idxs[:allowed])
    return tuple(sorted(kept))


def _cap_prs(prs: Sequence[dict]) -> list[dict]:
    """Apply :func:`apply_author_cap` to a PR list, dated by ``createdAt``."""
    dated = [(pr, _parse_iso(pr.get("createdAt"))) for pr in prs]
    dated = [(pr, dt) for pr, dt in dated if dt is not None]
    kept = apply_author_cap([(_login_of(pr), dt) for pr, dt in dated])
    return [dated[i][0] for i in kept]


def _cap_authors(authors: Sequence[dict], date_key: str) -> list[dict]:
    """Apply :func:`apply_author_cap` to an author-roster list, dated by
    ``date_key`` (one of the roster's own ``_first_created`` /
    ``_first_merged`` datetime fields)."""
    dated = [(a, a[date_key]) for a in authors if a.get(date_key) is not None]
    kept = apply_author_cap([(a["login"], dt) for a, dt in dated])
    return [dated[i][0] for i in kept]


def _decided_within_days(pr: dict, days: int) -> bool:
    """True when the recorded PR was closed (merged or not) within ``days``
    of its opening."""
    created = _parse_iso(pr.get("createdAt"))
    closed = _parse_iso(pr.get("closedAt"))
    return (created is not None and closed is not None
            and closed - created <= timedelta(days=days))


def _author_pr_history(window_prs: Sequence[dict],
                       record: Optional[dict], login: str) -> list[dict]:
    """The author's recorded PR history: the window PRs union the record's
    search PRs, deduplicated by PR number (search entries win)."""
    seen: dict[Any, dict] = {}
    for pr in window_prs:
        if _login_of(pr) == login:
            seen[pr.get("number")] = pr
    for pr in (record or {}).get("prs") or []:
        seen[pr.get("number")] = pr
    return list(seen.values())


def _eligible(pr: dict, records: dict[str, dict]) -> bool:
    """D-IN-3 eligibility of one PR's author: the fetched account record when
    one exists, else the PR node's own ``author.createdAt`` for account age
    (never for public events — a PR node carries no event count, so the
    absence of a record reads as ineligible, the D-IN-3 default)."""
    login = _login_of(pr)
    record = records.get(login)
    account_at = (record or {}).get("account_created_at")
    if not account_at:
        account_at = (pr.get("author") or {}).get("createdAt")
    events = (record or {}).get("public_event_count")
    return is_eligible_account(account_at, pr.get("createdAt"), events)


def _no_prior_merged(pr: dict, window_prs: Sequence[dict], records: dict[str, dict]) -> bool:
    """1.3 MR_first: the author had no other recorded PR, merged before this
    one's ``createdAt``, in the union of the window and the author's search
    history (module note: a documented approximation of "ever")."""
    login = _login_of(pr)
    created = _parse_iso(pr.get("createdAt"))
    if created is None:
        return False
    history = _author_pr_history(window_prs, records.get(login), login)
    return not any(
        _parse_iso(other.get("mergedAt")) is not None
        and (other_created := _parse_iso(other.get("createdAt"))) is not None
        and other_created < created
        for other in history
        if other.get("number") != pr.get("number")
    )


def _covered_since(ah: dict, anchor: datetime) -> Optional[datetime]:
    """The oldest point in time the recorded 365-d window is known to cover
    completely: exactly ``anchor - 365 d`` when the fetch was not truncated
    (every PR back to the window start is recorded), else the oldest
    recorded PR's ``createdAt`` (only PRs newer than that are guaranteed
    present). ``None`` when truncated with zero recorded PRs (should not
    occur: a truncation requires >= the cap's worth of records)."""
    if not ah.get("truncated"):
        return anchor - timedelta(days=AUTHOR_WINDOW_DAYS)
    created = [c for pr in ah.get("prs") or []
              if (c := _parse_iso(pr.get("createdAt"))) is not None]
    return min(created) if created else None


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    return round(numerator / denominator, 4) if denominator else None


def _newcomer_cohort_authors(window_prs: Sequence[dict], records: dict[str, dict],
                             sampled: Sequence[str], anchor: datetime) -> list[dict]:
    """The sampled newcomer-cohort roster (5.2/5.3), each entry carrying the
    public ``login``/``first_pr``/``followups`` fields plus the private
    (``_``-prefixed) facts :func:`derive_cohort_fields` reduces to rates."""
    band_start = anchor - timedelta(days=NEWCOMER_BAND_START_DAYS)
    band_end = anchor - timedelta(days=NEWCOMER_BAND_END_DAYS)
    oldest: dict[str, datetime] = {}
    for pr in window_prs:
        if not _is_external_nonbot(pr):
            continue
        login = _login_of(pr)
        created = _parse_iso(pr.get("createdAt"))
        if not login or created is None:
            continue
        if login not in oldest or created < oldest[login]:
            oldest[login] = created
    in_band = {login for login, created in oldest.items() if band_start <= created <= band_end}
    authors: list[dict] = []
    for login in sampled:
        if login not in in_band:
            continue
        history = sorted(
            (item for pr in _author_pr_history(window_prs, records.get(login), login)
             if (item := (_parse_iso(pr.get("createdAt")), pr.get("number"), pr))[0] is not None),
            key=lambda item: (item[0], item[1]))
        if not history:
            continue
        first_created, _first_number, first_pr = history[0]
        if first_created < band_start:
            continue  # recorded evidence of an even-older PR: not a genuine newcomer
        followups = [pr for _c, _n, pr in history[1:]]
        record = records.get(login) or {}
        first_merged = _parse_iso(first_pr.get("mergedAt"))
        converted = (first_merged is not None
                     and first_merged - first_created <= timedelta(days=NEWCOMER_CONVERT_WINDOW_DAYS))
        returned = first_merged is not None and any(
            (c := _parse_iso(pr.get("createdAt"))) is not None
            and c > first_merged
            and c - first_merged <= timedelta(days=RETURN_WINDOW_DAYS)
            for pr in followups)
        authors.append({
            "login": login,
            "first_pr": {"number": first_pr.get("number"),
                         "created_at": first_pr.get("createdAt"),
                         "merged_at": first_pr.get("mergedAt")},
            "followups": [{"number": pr.get("number"),
                           "created_at": pr.get("createdAt"),
                           "merged_at": pr.get("mergedAt")} for pr in followups],
            "_eligible": is_eligible_account(record.get("account_created_at"),
                                             first_pr.get("createdAt"),
                                             record.get("public_event_count")),
            "_first_created": first_created,
            "_first_merged": first_merged,
            "_converted": converted,
            "_returned": returned,
        })
    return authors


def _author_public(author: dict) -> dict:
    return {k: v for k, v in author.items() if not k.startswith("_")}


#: The bundle fields :func:`derive_cohort_fields` sets from the recorded
#: author_history envelope, in 02 section 4 order.
AUTHOR_HISTORY_BUNDLE_FIELDS: tuple[str, ...] = (
    "author_history_cost_points", "author_history_truncated",
    "author_history_rate_limit_remaining", "newcomer_sampled_author_count",
    "decision_cohort_coverage_gap_days", "cohort_decision_pr_numbers",
    "dr90_decision_rate_raw", "dr90_decision_pr_count_raw",
    "dr90_decision_rate", "dr90_decision_pr_count",
    "mr_first_rate_raw", "mr_first_pr_count_raw",
    "mr_first_rate", "mr_first_pr_count",
    "newcomer_cohort_author_count", "cohort_newcomer_authors",
    "newcomer_conversion_raw", "newcomer_conversion_raw_author_count",
    "newcomer_conversion", "newcomer_conversion_author_count",
    "rr90_return_rate_raw", "rr90_return_raw_author_count",
    "rr90_return_rate", "rr90_return_author_count",
)


def derive_cohort_fields(ah: Optional[dict]) -> dict[str, tuple[Any, str]]:
    """W1-4 bundle fields from the recorded ``author_history`` envelope
    ``data`` (packet W1-4, D-IN-3/D-IN-4, 02 §4 1.2/1.3/5.2/5.3).

    Returns ``{field: (value, confidence)}`` for
    :data:`AUTHOR_HISTORY_BUNDLE_FIELDS`: the envelope-recorded facts (point
    cost, truncation, rate-limit remaining, sampled-author count) are
    ``verified``; every cohort roster and rate is ``derived``. Both a RAW
    rate (the identified cohort, no D-IN-3 eligibility or cap) and the
    D-IN-3-adjusted rate (eligible authors, per-author capped) are stored
    for DR_90, MR_first, newcomer conversion and RR_90 (module note). Empty
    dict when ``ah`` is None: without the author_history envelope no cohort
    quantity is measurable.
    """
    if not ah:
        return {}
    out: dict[str, tuple[Any, str]] = {}
    out["author_history_cost_points"] = (int(ah.get("cost_points") or 0), "verified")
    out["author_history_truncated"] = (bool(ah.get("truncated")), "verified")
    out["author_history_rate_limit_remaining"] = (ah.get("rate_limit_remaining"), "verified")
    sampled = [str(login).lower() for login in ah.get("sampled_authors") or []]
    out["newcomer_sampled_author_count"] = (len(sampled), "verified")

    anchor = _parse_iso(ah.get("window_anchor"))
    if anchor is None:
        return out
    window_prs: Sequence[dict] = ah.get("prs") or []
    records = {str(r.get("login") or "").lower(): r for r in ah.get("author_records") or []}

    covered_since = _covered_since(ah, anchor)
    requested_start = anchor - timedelta(days=DECISION_COHORT_START_DAYS)
    if covered_since is None:
        gap_days = DECISION_COHORT_START_DAYS
    elif covered_since <= requested_start:
        gap_days = 0
    else:
        gap_days = (covered_since - requested_start).days
    out["decision_cohort_coverage_gap_days"] = (gap_days, "derived")

    # --- 1.2 DR_90 / 1.3 MR_first: the decision cohort ----------------------
    cohort_prs = [pr for pr in window_prs if _in_decision_band(pr, anchor) and _is_external_nonbot(pr)]
    out["cohort_decision_pr_numbers"] = (
        sorted(pr["number"] for pr in cohort_prs if pr.get("number") is not None), "derived")

    raw_decided = sum(1 for pr in cohort_prs if _decided_within_days(pr, DECISION_WINDOW_DAYS))
    out["dr90_decision_rate_raw"] = (_ratio(raw_decided, len(cohort_prs)), "derived")
    out["dr90_decision_pr_count_raw"] = (len(cohort_prs), "derived")

    dr90_kept = _cap_prs([pr for pr in cohort_prs if _eligible(pr, records)])
    dr90_decided = sum(1 for pr in dr90_kept if _decided_within_days(pr, DECISION_WINDOW_DAYS))
    out["dr90_decision_rate"] = (_ratio(dr90_decided, len(dr90_kept)), "derived")
    out["dr90_decision_pr_count"] = (len(dr90_kept), "derived")

    mr_candidates = [pr for pr in cohort_prs if _no_prior_merged(pr, window_prs, records)]
    raw_mr_decided = [pr for pr in mr_candidates if pr.get("closedAt") is not None]
    raw_mr_merged = sum(1 for pr in raw_mr_decided if pr.get("mergedAt") is not None)
    out["mr_first_rate_raw"] = (_ratio(raw_mr_merged, len(raw_mr_decided)), "derived")
    out["mr_first_pr_count_raw"] = (len(raw_mr_decided), "derived")

    mr_kept = _cap_prs([pr for pr in mr_candidates if _eligible(pr, records)])
    mr_decided = [pr for pr in mr_kept if pr.get("closedAt") is not None]
    mr_merged = sum(1 for pr in mr_decided if pr.get("mergedAt") is not None)
    out["mr_first_rate"] = (_ratio(mr_merged, len(mr_decided)), "derived")
    out["mr_first_pr_count"] = (len(mr_decided), "derived")

    # --- 5.2 / 5.3: the sampled newcomer cohort -----------------------------
    authors = _newcomer_cohort_authors(window_prs, records, sampled, anchor)
    out["newcomer_cohort_author_count"] = (len(authors), "derived")
    out["cohort_newcomer_authors"] = ([_author_public(a) for a in authors], "derived")

    out["newcomer_conversion_raw"] = (
        _ratio(sum(1 for a in authors if a["_converted"]), len(authors)), "derived")
    out["newcomer_conversion_raw_author_count"] = (len(authors), "derived")

    eligible_authors = [a for a in authors if a["_eligible"]]
    conv_kept = _cap_authors(eligible_authors, "_first_created")
    out["newcomer_conversion"] = (
        _ratio(sum(1 for a in conv_kept if a["_converted"]), len(conv_kept)), "derived")
    out["newcomer_conversion_author_count"] = (len(conv_kept), "derived")

    merged_first_all = [a for a in authors if a["_first_merged"] is not None]
    out["rr90_return_rate_raw"] = (
        _ratio(sum(1 for a in merged_first_all if a["_returned"]), len(merged_first_all)), "derived")
    out["rr90_return_raw_author_count"] = (len(merged_first_all), "derived")

    merged_first_eligible = [a for a in eligible_authors if a["_first_merged"] is not None]
    rr_kept = _cap_authors(merged_first_eligible, "_first_merged")
    out["rr90_return_rate"] = (
        _ratio(sum(1 for a in rr_kept if a["_returned"]), len(rr_kept)), "derived")
    out["rr90_return_author_count"] = (len(rr_kept), "derived")
    return out
