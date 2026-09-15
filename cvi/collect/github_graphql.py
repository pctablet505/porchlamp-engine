"""Windowed PR collection with typed actors and review threads.

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
``[bot]``, or an exact-match login in the frozen list below — v1 substring
matching is removed.
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
from cvi.core.constants import AUTHOR_UNMEASURED_MAX_FRACTION
from cvi.core.hygiene import (
    account_eligibility as _hygiene_account_eligibility,
    apply_author_cap,
    is_eligible_account,
)

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
        """Run one query through ``gh api graphql`` and return its parsed
        body. ``gh`` exits non-zero whenever the response carries a
        GraphQL ``errors`` array — including a partially-successful,
        alias-addressed response with real data alongside it (round-3
        fix: a whole-batch discard on ``rc != 0`` threw away every
        resolved alias in a batch that had even one error). The body is
        parsed regardless of ``rc``; only an unparseable body (a true
        transport/network failure, no JSON came back at all) raises.
        Callers that must treat ANY ``errors`` entry as fatal for the
        whole call still do, via :func:`_raise_on_graphql_errors` on the
        returned doc.
        """
        cmd: list[str] = ["gh", "api", "graphql", "-f", f"query={query}"]
        for key, value in variables.items():
            flag = "-F" if isinstance(value, int) and not isinstance(value, bool) else "-f"
            cmd += [flag, f"{key}={value}"]
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=self.timeout_seconds)
        try:
            return json.loads(out.stdout)
        except (json.JSONDecodeError, ValueError) as e:
            raise RuntimeError(
                f"gh api graphql rc={out.returncode}: {out.stderr[:160] or e}") from e


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
# Author history and newcomer cohorts (D-IN-3/D-IN-4, 02 §4 1.2/1.3/5.2/5.3)
# --------------------------------------------------------------------------- #
#
# Data plan. One envelope per repo,
# ``raw_dir/author_history/<owner>__<name>.json``:
#
# * a 365-d window of PRs (cap 500 newest, ``truncated`` flag) — the same
#   fetch serves both cohorts. The decision cohort ([anchor-270 d,
#   anchor-90 d]) is read from this window rather than from the 180-d
#   ``pr_window`` envelope: the 365-d window strictly dominates it,
#   so building the decision cohort from it covers the full 270 d
#   instead of silently narrowing to [180 d, 90 d]. ``decision_cohort_
#   coverage_gap_days`` records the residual, non-zero only when this
#   window itself truncates before reaching 270 d back;
# * an external, non-bot author whose first recorded PR in the window was
#   opened in [anchor-365 d, anchor-180 d] is a newcomer-cohort author
#   (D-IN-4); at most 20 are sampled per repo — sorted by login, then a
#   fixed-seed random draw over that sorted list (see :func:`_select_newcomer_sample`);
# * D-IN-3 eligibility (account age, event count) is fetched for every
#   author in the union (sampled newcomers + external decision-cohort
#   authors), via aliased ``user(login:)`` batches of
#   :data:`ELIGIBILITY_BATCH_SIZE`. An alias with no data in the response —
#   a deleted/renamed account, or a truncated batch — is recorded
#   ``eligibility_unmeasured`` rather than a silent ineligible default;
# * the per-author ``search`` (the author's newest 30 PRs in the repo,
#   ``repo:o/n is:pr author:login`` — prior-merge history for MR_first) is
#   capped at :data:`AUTHOR_SEARCH_CAP` per repo, deterministically sampled
#   (:func:`_seeded_sample`) from the union when it is larger. A search that
#   fails with a GraphQL error (``history_unmeasured``) or that fell outside
#   the sample (``history_unsampled``) degrades that one author to no
#   recorded history rather than failing the whole repo's collection — and
#   excludes that author from MR_first's population entirely
#   (:func:`_no_prior_merged` returns ``None``), never assumed a first-timer;
# * derivation (``derive_cohort_fields``, pure over the recorded envelope):
#   for each of DR_90 (1.2), MR_first (1.3), newcomer conversion (5.2) and
#   RR_90 (5.3) both a raw rate (over the identified cohort, no D-IN-3
#   eligibility or cap) and the D-IN-3-adjusted rate (eligible authors only,
#   each capped at floor(20% * n) items, oldest kept) are stored.
#
# Documented approximations: "no previously merged PR" (MR_first — the
# predicate is merge time, not creation time) is a statement about recorded
# history only — the 365-d window plus the sampled per-author newest-30 search;
# a truncated newcomer band excludes candidates whose oldest recorded PR sits
# within ``TRUNCATION_MARGIN_DAYS`` of the truncation floor, since an even older,
# unrecorded PR would misclassify them as a newcomer.

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
#: Fixed seed for deterministic sampling of the candidate pool.
NEWCOMER_SAMPLE_SEED: int = 20260912
#: Per-author history: the 30 newest of the author's PRs in the repo.
AUTHOR_SEARCH_FIRST: int = 30
#: Truncation guard: a newcomer candidate whose oldest recorded PR is this
#: close to the truncated window's floor may have an unrecorded older PR.
TRUNCATION_MARGIN_DAYS: int = 30
#: D-IN-4: decision-rate cohort, older edge — opened at most 270 d ago.
DECISION_COHORT_START_DAYS: int = 270
#: D-IN-4/1.2: decision-cohort newer edge AND the DR_90 decide-by window —
#: a cohort PR has had exactly this long to be decided.
DECISION_WINDOW_DAYS: int = 90
#: D-IN-3: MIN_ACCOUNT_AGE_DAYS, MIN_PUBLIC_EVENTS and AUTHOR_CAP_FRACTION
#: are imported from cvi.core.constants.
#: 5.2: the first PR converted when merged within 180 d of opening.
NEWCOMER_CONVERT_WINDOW_DAYS: int = 180
#: 5.3: a return needs a second PR within 90 d after the first PR's merge.
RETURN_WINDOW_DAYS: int = 90
#: At most this many per-author ``search`` calls per repo,
#: deterministically sampled (:func:`_seeded_sample`) from the union of the
#: decision cohort and the newcomer sample when that union is larger.
AUTHOR_SEARCH_CAP: int = 50
#: Batch size for aliased user eligibility queries.
ELIGIBILITY_BATCH_SIZE: int = 15
#: Narrower retry widths for aliases a batch left unresolved.
ELIGIBILITY_RETRY_WIDTHS: tuple[int, ...] = (5, 1)
#: Above this fraction of a repo's cohort authors being unmeasured,
#: row 1.2 (DR_90) and row 1.3 (MR_first)'s D-IN-3-adjusted rate
#: are reported unmeasured rather than computed on a collapsed population.
#: Imported from :mod:`cvi.core.constants`.

#: The 365-d window query: PRs in all three states, newest first, light
#: fields (no reviews/comments — the author section does not derive
#: D-IN-2). ``rateLimit`` carries ``cost`` and ``remaining``.
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

def _eligibility_batch_query(n: int) -> str:
    """D-IN-3 eligibility batch query for ``n`` logins: aliased
    ``user(login:)`` nodes ``u0``..``u{n-1}`` (variables ``l0``..``l{n-1}``,
    ``u{i}`` always paired with ``l{i}``), each carrying ``createdAt`` and
    the public-event proxy
    (``contributionsCollection.contributionCalendar.totalContributions`` —
    module note above: GitHub's own default ~1 y contribution window).
    """
    var_decls = " ".join(f"$l{i}: String!" for i in range(n))
    aliases = " ".join(
        f"u{i}: user(login: $l{i}) {{ createdAt "
        "contributionsCollection { contributionCalendar { totalContributions } } }"
        for i in range(n))
    return f"query AuthorEligibilityBatch({var_decls}) {{ rateLimit {{ cost remaining }} {aliases} }}"


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


def _seeded_sample(candidates: Sequence[str], size: int) -> tuple[str, ...]:
    """Deterministic sample shared by the newcomer cohort (D-IN-4:
    sorted login, then a seeded choice) and the
    per-author search cap: sort the candidate logins, then draw at
    most ``size`` of them with a fixed-seed
    ``random.Random(NEWCOMER_SAMPLE_SEED).sample`` — not the earliest
    ``size``, which would sample only one slice of the population every
    time. Pure function of the candidate set: the same candidates always
    yield the same sample, in the same run or a different one.
    """
    sorted_candidates = sorted(set(candidates))
    if len(sorted_candidates) <= size:
        return tuple(sorted_candidates)
    chosen = random.Random(NEWCOMER_SAMPLE_SEED).sample(sorted_candidates, size)
    return tuple(sorted(chosen))


def _select_newcomer_sample(prs: Sequence[dict], anchor: datetime,
                            truncated: bool) -> tuple[str, ...]:
    """Deterministic newcomer sample: external, non-bot authors whose first
    recorded PR in ``prs`` was opened in [anchor-365 d, anchor-180 d],
    reduced to at most 20 by :func:`_seeded_sample` (D-IN-4).

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
    return _seeded_sample(sorted(candidates), NEWCOMER_SAMPLE_SIZE)


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


def _run_eligibility_query(logins: Sequence[str], transport: GraphqlTransport,
                           ) -> tuple[dict[str, dict], list[str], int, Optional[int], bool]:
    """One ``AuthorEligibilityBatch`` call over exactly ``logins``.

    Returns ``(resolved, unresolved, cost, remaining, resource_limited)``.
    ``resolved`` maps a login to its eligibility record for every alias
    that carried data in the response; ``unresolved`` lists the logins
    whose alias was null -- kept for a narrower retry by the
    caller, never discarded along with the rest of a partially-successful
    batch. A whole-call transport failure (``execute`` raises — the body
    was not even parseable, not merely error-carrying) is treated the same
    way: every login in ``logins`` comes back unresolved rather than
    immediately unmeasured, so it too gets a narrower retry first.
    ``resource_limited`` is True when an error names GitHub's per-query
    resource budget — the caller's signal to shrink its width for the rest
    of the run.
    """
    query = _eligibility_batch_query(len(logins))
    variables = {f"l{i}": login for i, login in enumerate(logins)}
    try:
        doc = transport.execute(query, variables)
    except RuntimeError:
        return {}, list(logins), 0, None, False
    data = doc.get("data") or {}
    rl = data.get("rateLimit") or {}
    cost = int(rl.get("cost") or 0)
    remaining = int(rl["remaining"]) if rl.get("remaining") is not None else None
    resource_limited = any("resource limit" in str(e.get("message") or "").lower()
                           for e in (doc.get("errors") or []))
    resolved: dict[str, dict] = {}
    unresolved: list[str] = []
    for i, login in enumerate(logins):
        node = data.get(f"u{i}")
        if node is not None:
            resolved[login] = {
                "account_created_at": node.get("createdAt"),
                "public_event_count": ((node.get("contributionsCollection") or {})
                                       .get("contributionCalendar") or {}).get("totalContributions"),
                "eligibility_unmeasured": False,
            }
        else:
            unresolved.append(login)
    return resolved, unresolved, cost, remaining, resource_limited


def _fetch_eligibility_batch(logins: Sequence[str], transport: GraphqlTransport,
                             batch_size: int = ELIGIBILITY_BATCH_SIZE,
                             ) -> tuple[dict[str, dict], int, Optional[int]]:
    """D-IN-3 account age / event count for every author in ``logins``.

    Round-3 fix (B-1): ``gh api graphql`` exits non-zero on ANY response
    carrying an ``errors`` array, including a partially-successful one
    (review §3.5: 11 of 15 aliases resolved, GitHub still charged the
    point, and the pre-fix code discarded all 15). Every alias that DOES
    resolve is kept immediately; only the specific logins whose alias came
    back null are retried at a narrower width
    (:data:`ELIGIBILITY_RETRY_WIDTHS`), until a width of 1 pins the outcome
    to one login — recorded ``eligibility_unmeasured`` only then, never
    assumed ineligible. A ``Resource limits ... exceeded`` error (GitHub's
    per-query complexity budget, not an alias-count ceiling — no fixed
    width is safe) permanently halves ``batch_size`` for the rest of this
    call's fresh (non-retry) chunks. ``logins`` is de-duplicated
    (order-preserving) before any query is built, so a login appearing
    twice costs one alias and yields one record. Every distinct login in
    ``logins`` gets exactly one entry in the returned dict.
    """
    out: dict[str, dict] = {}
    cost = 0
    remaining: Optional[int] = None
    width = batch_size
    fresh = list(dict.fromkeys(logins))  # de-dup, first-seen order

    def _settle(pool: list[str]) -> None:
        nonlocal cost, remaining, width
        for retry_width in ELIGIBILITY_RETRY_WIDTHS:
            if not pool:
                return
            next_pool: list[str] = []
            for start in range(0, len(pool), retry_width):
                sub = pool[start:start + retry_width]
                sub_resolved, sub_unresolved, c, r, limited = _run_eligibility_query(sub, transport)
                out.update(sub_resolved)
                cost += c
                if r is not None:
                    remaining = r
                if limited:
                    width = max(1, width // 2)
                next_pool.extend(sub_unresolved)
            pool = next_pool
        for login in pool:
            out[login] = {"account_created_at": None, "public_event_count": None,
                          "eligibility_unmeasured": True}

    while fresh:
        chunk, fresh = fresh[:width], fresh[width:]
        resolved, unresolved, c, r, limited = _run_eligibility_query(chunk, transport)
        out.update(resolved)
        cost += c
        if r is not None:
            remaining = r
        if limited:
            width = max(1, width // 2)
        _settle(unresolved)
    return out, cost, remaining


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
    ``sampled_authors`` list, and the per-author ``author_records`` for the
    union of the sampled newcomer authors and the external decision-cohort
    authors identified in the same window. Returns ``"ok"``, or ``"skip"``
    when the envelope already exists and ``force`` is False.

    Round-2 cost cap (review §4): D-IN-3 eligibility (account age, event
    count) is fetched for EVERY author in the union via
    :func:`_fetch_eligibility_batch` — never sampled, so DR_90 keeps its
    full cohort. The per-author ``search`` (prior-merge history for
    MR_first) is capped at :data:`AUTHOR_SEARCH_CAP` and deterministically
    sampled (:func:`_seeded_sample`) when the union exceeds it; an author
    outside the sample is ``history_unsampled`` and, like a failed search
    (``history_unmeasured``), excluded from MR_first's population entirely
    (:func:`_no_prior_merged`) rather than assumed a first-timer.
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
    search_sample = set(_seeded_sample(needed, AUTHOR_SEARCH_CAP))

    author_records: list[dict] = []
    for login in needed:
        record: dict[str, Any] = {
            "login": login, "account_created_at": None, "public_event_count": None,
            "prs": [], "history_unmeasured": False, "history_unsampled": False,
            "eligibility_unmeasured": False,
        }
        if login not in search_sample:
            record["history_unsampled"] = True
        else:
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
                record["history_unmeasured"] = True  # deleted/renamed account or a transient failure
        author_records.append(record)

    elig, elig_cost, elig_remaining = _fetch_eligibility_batch(needed, t, ELIGIBILITY_BATCH_SIZE)
    cost += elig_cost
    if elig_remaining is not None:
        remaining = elig_remaining
    for record in author_records:
        record.update(elig[record["login"]])

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


#: D-IN-3: ``is_eligible_account``/``apply_author_cap`` are imported from
#: :mod:`cvi.core.hygiene`.


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


def _account_eligibility(record: Optional[dict], pr_created_at: Optional[str],
                         fallback_account_at: Optional[str] = None) -> Optional[bool]:
    """D-IN-3 eligibility of one author, tri-state:
    ``True`` (eligible), ``False`` (confirmed ineligible on real data), or
    ``None`` (unknown — this author's eligibility lookup or prior-merge search
    failed: ``eligibility_unmeasured`` or ``history_unmeasured``).
    A ``None`` here must be excluded from a rate's numerator and denominator
    by the caller (checked with ``is True``).
    """
    record = record or {}
    measured = not (record.get("eligibility_unmeasured") or record.get("history_unmeasured"))
    account_at = record.get("account_created_at") or fallback_account_at
    events = record.get("public_event_count")
    return _hygiene_account_eligibility(account_at, pr_created_at, events, measured=measured)


def _eligible(pr: dict, records: dict[str, dict]) -> Optional[bool]:
    """D-IN-3 eligibility of one PR's author — :func:`_account_eligibility`
    keyed by this PR's own author and ``createdAt``, falling back to the
    PR node's own ``author.createdAt`` when no per-author record exists at
    all (never for public events — a PR node carries no event count)."""
    login = _login_of(pr)
    fallback_account_at = (pr.get("author") or {}).get("createdAt")
    return _account_eligibility(records.get(login), pr.get("createdAt"), fallback_account_at)


def _author_status(record: dict, anchor: datetime) -> str:
    """One author's status for accounting fields:
    ``"unmeasured"`` (a search or eligibility fetch failed for this author),
    ``"unsampled"`` (the designed :data:`AUTHOR_SEARCH_CAP` sample),
    ``"ineligible"`` (real data, D-IN-3 thresholds not met), or
    ``"eligible"``. Uses the window anchor as reference time.
    A failed fetch is tested before the search-cap sample."""
    if record.get("history_unmeasured") or record.get("eligibility_unmeasured"):
        return "unmeasured"
    if record.get("history_unsampled"):
        return "unsampled"
    if is_eligible_account(record.get("account_created_at"), anchor.isoformat(),
                           record.get("public_event_count")):
        return "eligible"
    return "ineligible"


def _no_prior_merged(pr: dict, window_prs: Sequence[dict],
                     records: dict[str, dict]) -> Optional[bool]:
    """1.3 MR_first: the author had no other recorded PR merged strictly
    before this one's ``createdAt``, in the union of the window and the
    author's search history.

    Returns ``None`` (unknown) when the author's prior-merge history was not
    measured for this repo: the search failed (``history_unmeasured``) or
    the author fell outside the search cap (``history_unsampled``). Callers
    must exclude a ``None`` result from MR_first's population entirely."""
    login = _login_of(pr)
    record = records.get(login)
    if record is not None and (record.get("history_unmeasured")
                               or record.get("history_unsampled")):
        return None
    created = _parse_iso(pr.get("createdAt"))
    if created is None:
        return False
    history = _author_pr_history(window_prs, record, login)
    return not any(
        (other_merged := _parse_iso(other.get("mergedAt"))) is not None
        and other_merged < created
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
            # The account's creation date, carried through to cvi.core.models.NewcomerAuthor;
            # None when unmeasured.
            "account_created_at": record.get("account_created_at"),
            "first_pr": {"number": first_pr.get("number"),
                         "created_at": first_pr.get("createdAt"),
                         "merged_at": first_pr.get("mergedAt")},
            "followups": [{"number": pr.get("number"),
                           "created_at": pr.get("createdAt"),
                           "merged_at": pr.get("mergedAt")} for pr in followups],
            "_eligible": _account_eligibility(record, first_pr.get("createdAt")),
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
    "authors_total", "authors_excluded_unsampled",
    "authors_excluded_unmeasured", "authors_excluded_ineligible",
    "cohort_unmeasured_detail",
    "dr90_decision_rate_raw", "dr90_decision_pr_count_raw",
    "dr90_decision_rate_author_history_cohort", "dr90_decision_pr_count_author_history_cohort",
    "mr_first_rate_raw", "mr_first_pr_count_raw",
    "mr_first_rate", "mr_first_pr_count",
    "newcomer_cohort_author_count", "cohort_newcomer_authors",
    "newcomer_conversion_raw", "newcomer_conversion_raw_author_count",
    "newcomer_conversion", "newcomer_conversion_author_count",
    "rr90_return_rate_raw", "rr90_return_raw_author_count",
    "rr90_return_rate", "rr90_return_author_count",
    "newcomer_unmeasured_detail",
)


def derive_cohort_fields(ah: Optional[dict]) -> dict[str, tuple[Any, str]]:
    """Bundle fields from the recorded ``author_history`` envelope data
    (D-IN-3/D-IN-4, 02 §4 1.2/1.3/5.2/5.3).

    Returns ``{field: (value, confidence)}`` for :data:`AUTHOR_HISTORY_BUNDLE_FIELDS`:
    the envelope-recorded facts (point cost, truncation, rate-limit remaining,
    sampled-author count) are ``verified``; every cohort roster and rate is
    ``derived``. Both a raw rate (the identified cohort, no D-IN-3 eligibility or cap)
    and the D-IN-3-adjusted rate (eligible authors, per-author capped) are stored
    for DR_90, MR_first, newcomer conversion and RR_90. Empty dict when ``ah`` is None.

    The D-IN-3-adjusted DR_90 fields computed here are named
    ``dr90_decision_rate_author_history_cohort``/``dr90_decision_pr_count_author_history_cohort``
    to distinguish them from the narrower 180-day pr_window cohort.

    When more than :data:`AUTHOR_UNMEASURED_MAX_FRACTION` of cohort authors are
    unmeasured, adjusted rates and counts are reported as ``(None, "missing")``
    with detail recorded in ``cohort_unmeasured_detail`` and ``newcomer_unmeasured_detail``.
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

    # Author accounting: every author record sorted into exactly one bucket.
    statuses = [_author_status(r, anchor) for r in records.values()]
    authors_total = len(statuses)
    authors_excluded_unsampled = statuses.count("unsampled")
    authors_excluded_unmeasured = statuses.count("unmeasured")
    authors_excluded_ineligible = statuses.count("ineligible")
    out["authors_total"] = (authors_total, "verified")
    out["authors_excluded_unsampled"] = (authors_excluded_unsampled, "verified")
    out["authors_excluded_unmeasured"] = (authors_excluded_unmeasured, "verified")
    out["authors_excluded_ineligible"] = (authors_excluded_ineligible, "verified")
    unmeasured_fraction = (authors_excluded_unmeasured / authors_total) if authors_total else 0.0
    collector_failed = unmeasured_fraction > AUTHOR_UNMEASURED_MAX_FRACTION

    # --- 1.2 DR_90 / 1.3 MR_first: the decision cohort ----------------------
    cohort_prs = [pr for pr in window_prs if _in_decision_band(pr, anchor) and _is_external_nonbot(pr)]
    out["cohort_decision_pr_numbers"] = (
        sorted(pr["number"] for pr in cohort_prs if pr.get("number") is not None), "derived")

    raw_decided = sum(1 for pr in cohort_prs if _decided_within_days(pr, DECISION_WINDOW_DAYS))
    out["dr90_decision_rate_raw"] = (_ratio(raw_decided, len(cohort_prs)), "derived")
    out["dr90_decision_pr_count_raw"] = (len(cohort_prs), "derived")

    # Only a confirmed first-timer counts: an author whose prior-merge history
    # is unmeasured or unsampled is excluded from the MR_first population entirely.
    mr_candidates = [pr for pr in cohort_prs
                     if _no_prior_merged(pr, window_prs, records) is True]
    raw_mr_decided = [pr for pr in mr_candidates if pr.get("closedAt") is not None]
    raw_mr_merged = sum(1 for pr in raw_mr_decided if pr.get("mergedAt") is not None)
    out["mr_first_rate_raw"] = (_ratio(raw_mr_merged, len(raw_mr_decided)), "derived")
    out["mr_first_pr_count_raw"] = (len(raw_mr_decided), "derived")

    if collector_failed:
        # Collector failure gate: when too many authors are unmeasured, mark missing.
        detail = f"{authors_excluded_unmeasured} of {authors_total} authors unmeasured"
        out["dr90_decision_rate_author_history_cohort"] = (None, "missing")
        out["dr90_decision_pr_count_author_history_cohort"] = (None, "missing")
        out["mr_first_rate"] = (None, "missing")
        out["mr_first_pr_count"] = (None, "missing")
        out["cohort_unmeasured_detail"] = (detail, "verified")
    else:
        dr90_kept = _cap_prs([pr for pr in cohort_prs if _eligible(pr, records) is True])
        dr90_decided = sum(1 for pr in dr90_kept if _decided_within_days(pr, DECISION_WINDOW_DAYS))
        out["dr90_decision_rate_author_history_cohort"] = (_ratio(dr90_decided, len(dr90_kept)), "derived")
        out["dr90_decision_pr_count_author_history_cohort"] = (len(dr90_kept), "derived")

        mr_kept = _cap_prs([pr for pr in mr_candidates if _eligible(pr, records) is True])
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

    # Collector failure gate over the sampled newcomer roster.
    newcomer_total = len(authors)
    newcomer_unmeasured = sum(1 for a in authors if a["_eligible"] is None)
    newcomer_fraction = (newcomer_unmeasured / newcomer_total) if newcomer_total else 0.0
    newcomer_collector_failed = newcomer_fraction > AUTHOR_UNMEASURED_MAX_FRACTION

    eligible_authors = [a for a in authors if a["_eligible"] is True]
    if newcomer_collector_failed:
        detail = f"{newcomer_unmeasured} of {newcomer_total} newcomer authors unmeasured"
        out["newcomer_conversion"] = (None, "missing")
        out["newcomer_conversion_author_count"] = (None, "missing")
        out["rr90_return_rate"] = (None, "missing")
        out["rr90_return_author_count"] = (None, "missing")
        out["newcomer_unmeasured_detail"] = (detail, "verified")
    else:
        conv_kept = _cap_authors(eligible_authors, "_first_created")
        out["newcomer_conversion"] = (
            _ratio(sum(1 for a in conv_kept if a["_converted"]), len(conv_kept)), "derived")
        out["newcomer_conversion_author_count"] = (len(conv_kept), "derived")

        merged_first_eligible = [a for a in eligible_authors if a["_first_merged"] is not None]
        rr_kept = _cap_authors(merged_first_eligible, "_first_merged")
        out["rr90_return_rate"] = (
            _ratio(sum(1 for a in rr_kept if a["_returned"]), len(rr_kept)), "derived")
        out["rr90_return_author_count"] = (len(rr_kept), "derived")

    merged_first_all = [a for a in authors if a["_first_merged"] is not None]
    out["rr90_return_rate_raw"] = (
        _ratio(sum(1 for a in merged_first_all if a["_returned"]), len(merged_first_all)), "derived")
    out["rr90_return_raw_author_count"] = (len(merged_first_all), "derived")
    return out
