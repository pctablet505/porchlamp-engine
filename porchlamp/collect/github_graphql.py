"""Windowed PR collection with typed actors and review threads.

Collects the PRs a repository opened in the trailing 180 days (D-IN-4,
``docs/v2/02-RANKING-DESIGN.md`` section 3) from the GitHub GraphQL API and
derives the per-PR responsiveness inputs of ``02`` section 4:

* D-IN-2 — ``first_substantive_response_at`` / ``ttfhr_hours`` (parameter
  1.1): the earliest substantive response by the repository's own people,
  as :func:`porchlamp.core.gates.first_substantive_response` defines it (the
  one definition, which GR_14, D-GATE-5 and its served evidence also read);
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

import hashlib
import heapq
import json
import random
import re
import statistics
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Protocol

from porchlamp.collect import ghcli, ledger, net
from porchlamp.collect.envelope import COLLECTOR_VERSION, read_envelope, sha256_hex, write_envelope
from porchlamp.collect.user_cache import UserCache
from porchlamp.core import constants, gates, landing
from porchlamp.core.constants import AUTHOR_UNMEASURED_MAX_FRACTION
from porchlamp.core.hygiene import (
    account_eligibility as _hygiene_account_eligibility,
    apply_author_cap,
    is_eligible_account,
)

# --------------------------------------------------------------------------- #
# D-IN-4 / D-IN-2 / 02 §4 constants (docs/v2/02-RANKING-DESIGN.md)            #
# --------------------------------------------------------------------------- #

#: D-IN-4: PRs opened in the trailing 180 days.
PR_WINDOW_DAYS: int = 180
#: D-IN-4 (RFC-0013): every PR of the window is recorded with all fields up to
#: this many; ``truncated`` when a fetch without a reply sample stops here.
PR_CAP: int = constants.PR_CAP
#: RFC-0013: above :data:`PR_CAP`, every PR of the window is recorded with the
#: light fields and replies on an equal-probability sample of this many.
PR_SAMPLE_CAP: int = constants.PR_SAMPLE_CAP
#: RFC-0013: the fixed seed of the reply sample (:func:`reply_sample_numbers`).
PR_SAMPLE_SEED: int = constants.PR_SAMPLE_SEED
#: #51: aliased ``pullRequest(number:)`` lookups per reply batch query.
PR_REPLIES_BATCH_SIZE: int = 25
#: #51: narrower retry widths for reply aliases a batch left unresolved.
PR_REPLIES_RETRY_WIDTHS: tuple[int, ...] = (5, 1)
#: #51: a page that fails with a heavy-query error is fetched again from the
#: same cursor at the next size down this ladder, until one succeeds and the
#: failed page's span is covered; the walk then goes on at its own size. Only a
#: failure at the last rung propagates. An aliased batch retries at the same
#: rungs (:func:`batch_retry_widths`).
PR_PAGE_SIZE_LADDER: tuple[int, ...] = (50, 25, 10)
#: #51: substrings marking a heavy-query failure (an HTTP 502 or 504, a body
#: cut off mid-JSON, a timeout): a page that fails with one is read again in
#: smaller pages (:class:`_PageSizer`), where the phone used to fetch the whole
#: project again at page size 10.
_HEAVY_QUERY_ERRORS: tuple[str, ...] = (
    "504",
    "502",
    "end of JSON",
    "timed out",
    "timeout",
    "Timeout",
)
#: One GraphQL page (``first``); 2,000 PRs are reached in twenty full pages.
PAGE_SIZE: int = 100
#: #51: the light continuation's page size, whatever the heavy page size: a
#: 100-PR light page costs 1 point and stays well inside the time budget.
PR_LIGHT_PAGE_SIZE: int = 100
#: 02 §4.3 (RFC-0018): a review or D-IN-2 maintainer comment counts as
#: substantive at an 80-char effective body or 1 inline comment.
SUBSTANTIVE_REVIEW_BODY_CHARS: int = constants.SUBSTANTIVE_REVIEW_BODY_CHARS
#: 02 §4.3 (RFC-0018): normalized review/comment text repeated across at least
#: this many distinct PRs in the window is excluded as canned boilerplate.
SUBSTANTIVE_REVIEW_BOILERPLATE_MIN_PRS: int = constants.SUBSTANTIVE_REVIEW_BOILERPLATE_MIN_PRS
#: RFC-0022: the reviews and the comments a PR's first read carries (the
#: :data:`PR_WINDOW_QUERY` page and the ``PrRepliesBatch`` lookups), oldest
#: first. They were all the record kept; a longer list is now read on by PR
#: number (:func:`_fetch_reply_followups`), so these sizes set what one page
#: carries, not what the record holds.
PR_REVIEWS_FIRST: int = 10
PR_COMMENTS_FIRST: int = 20
#: RFC-0022: GitHub's largest page. A follow-up read asks a cut reply list for
#: at most this many more nodes, and the timeline holds this many of a PR's
#: ready-for-review and close events, the newest (it held 5; the point cost of
#: a page does not change with it).
PR_REPLY_PAGE_SIZE: int = 100
#: RFC-0022 (D-IN-4): a PR's reviews and comments are read on until whole or
#: this many of each (:data:`porchlamp.core.constants.PR_REPLY_NODE_CAP`).
PR_REPLY_NODE_CAP: int = constants.PR_REPLY_NODE_CAP

#: D-IN-1: exact-match bot logins (case-insensitive), the one list
#: :data:`porchlamp.core.constants.BOT_LOGINS`. Substring matching is removed
#: (F12); ``__typename == "Bot"`` and the ``[bot]`` suffix cover the rest.
BOT_LOGINS: frozenset[str] = constants.BOT_LOGINS

#: External (non-maintainer) associations, matching the v1 ``EXTERNAL_ASSOC``
#: set used by ``scripts/derive.py`` for the same fact: the core's one
#: definition, :data:`porchlamp.core.gates.EXTERNAL_ASSOCIATIONS`.
EXTERNAL_ASSOCIATIONS: frozenset[str] = gates.EXTERNAL_ASSOCIATIONS

#: RFC-0022: the fields of one review node and of one comment node, and of a
#: page of either, the same in every query that reads replies (the window page,
#: the reply batch and the follow-up reads), so a follow-up node is recorded in
#: the shape of the first page's. ``pageInfo`` says whether GitHub holds more.
_REVIEW_NODE_FIELDS: str = "author { __typename login } state body createdAt authorAssociation comments { totalCount }"
_COMMENT_NODE_FIELDS: str = "author { __typename login } authorAssociation bodyText createdAt"
_REPLY_PAGE_FIELDS: str = "totalCount pageInfo { hasNextPage endCursor }"

#: The windowed-PR query: PRs in all three states, newest first, with the
#: typed actors (``__typename``), who merged each PR (``mergedBy``, the
#: window's mergers set for D-IN-2), reviews, issue-style comments and the
#: review-thread total. ``rateLimit.cost`` measures the page's point cost.
#: Envelopes recorded before ``mergedBy`` was requested lack the key; their
#: mergers set is empty and D-IN-2 is association-only, as it was.
#: #60: each PR's ``labels`` and each close's ``closer`` (the commit or PR that
#: closed it) are recorded for landing detection, a PR a project lands outside
#: GitHub's merge (a "Merged" label, a closing commit); no measure reads them
#: yet. Envelopes recorded before they were requested lack both keys.
#: RFC-0022: each reply list records its ``totalCount`` and ``pageInfo`` (a
#: longer list is read on by number), and the timeline its newest
#: :data:`PR_REPLY_PAGE_SIZE` ready-for-review and close events. Envelopes
#: recorded before carry neither; their lists read as whole, as they did.
PR_WINDOW_QUERY: str = (
    "query PrWindow($owner: String!, $name: String!, $first: Int, $after: String) {"
    " rateLimit { cost }"
    " r: repository(owner: $owner, name: $name) {"
    " pullRequests(states: [OPEN, MERGED, CLOSED], first: $first, after: $after,"
    " orderBy: {field: CREATED_AT, direction: DESC}) {"
    " pageInfo { hasNextPage endCursor }"
    " nodes { number createdAt closedAt mergedAt state isDraft mergedBy { login }"
    " author { __typename login ... on User { createdAt } } authorAssociation"
    " labels(first: 10) { nodes { name } }"
    f" timelineItems(itemTypes: [READY_FOR_REVIEW_EVENT, CLOSED_EVENT], last: {PR_REPLY_PAGE_SIZE}) {{"
    " nodes { __typename ... on ReadyForReviewEvent { createdAt }"
    " ... on ClosedEvent { createdAt actor { __typename login }"
    " closer { __typename ... on Commit { oid } ... on PullRequest { number } } } } }"
    f" reviews(first: {PR_REVIEWS_FIRST}) {{ {_REPLY_PAGE_FIELDS} nodes {{ {_REVIEW_NODE_FIELDS} }} }}"
    f" comments(first: {PR_COMMENTS_FIRST}) {{ {_REPLY_PAGE_FIELDS} nodes {{ {_COMMENT_NODE_FIELDS} }} }}"
    " reviewThreads { totalCount } }"
    " } } }"
)

#: Light PR-window continuation query for repositories with > 2,000 PRs in 180 d.
#: Carries all PR-level and timeline fields (2 pts/page at 100 PRs/page, measured
#: 2026-10-18 with #60's labels and closer; 1 pt without them), omitting
#: ``reviews``, ``comments``, and ``reviewThreads`` which are fetched for the
#: seeded 1,000-PR sample via :func:`_pr_replies_batch_query`. RFC-0022: its
#: timeline is :data:`PR_WINDOW_QUERY`'s, the newest :data:`PR_REPLY_PAGE_SIZE`
#: events (still 2 points a page, measured 2026-10-22).
PR_WINDOW_LIGHT_QUERY: str = (
    "query PrWindowLight($owner: String!, $name: String!, $first: Int, $after: String) {"
    " rateLimit { cost }"
    " r: repository(owner: $owner, name: $name) {"
    " pullRequests(states: [OPEN, MERGED, CLOSED], first: $first, after: $after,"
    " orderBy: {field: CREATED_AT, direction: DESC}) {"
    " pageInfo { hasNextPage endCursor }"
    " nodes { number createdAt closedAt mergedAt state isDraft mergedBy { login }"
    " author { __typename login ... on User { createdAt } } authorAssociation"
    " labels(first: 10) { nodes { name } }"
    f" timelineItems(itemTypes: [READY_FOR_REVIEW_EVENT, CLOSED_EVENT], last: {PR_REPLY_PAGE_SIZE}) {{"
    " nodes { __typename ... on ReadyForReviewEvent { createdAt }"
    " ... on ClosedEvent { createdAt actor { __typename login }"
    " closer { __typename ... on Commit { oid } ... on PullRequest { number } } } } } }"
    " } } }"
)

#: The identity of :data:`PR_WINDOW_QUERY`'s text, recorded in every
#: ``pr_window`` envelope's ``data`` as ``query_sha256``. An envelope that
#: records another value, or none, was fetched by a different query and may
#: lack fields this one asks for; a refresh refetches it in full.
PR_WINDOW_QUERY_SHA256: str = sha256_hex(PR_WINDOW_QUERY)

#: RFC-0022: :data:`PR_WINDOW_QUERY_SHA256` as it stood before RFC-0022, the
#: query the phone's cycles read with until engine 5dc23da. It is the current
#: text with three edits: the timeline read ``last: 5`` events and neither
#: reply list asked for ``totalCount`` or ``pageInfo``. Every other field is
#: the same, so such a record needs only the PRs whose lists sat at those old
#: sizes read again (:func:`repair_pr_window_replies`), not the whole window.
LEGACY_PR_WINDOW_QUERY_SHA256: str = "4c7951ae11433034dbf7f8eec042d3ccabf87879fb1ca791d21242be8456b2a1"
#: The sizes that query read: a list of this many nodes may have held more.
LEGACY_REVIEWS_FIRST: int = 10
LEGACY_COMMENTS_FIRST: int = 20
LEGACY_TIMELINE_LAST: int = 5
#: The timeline selection of :data:`PR_WINDOW_QUERY` and
#: :data:`PR_WINDOW_LIGHT_QUERY`, read by PR number for a record that cut it.
_TIMELINE_SELECTION: str = (
    f"timelineItems(itemTypes: [READY_FOR_REVIEW_EVENT, CLOSED_EVENT], last: {PR_REPLY_PAGE_SIZE}) {{"
    " nodes { __typename ... on ReadyForReviewEvent { createdAt }"
    " ... on ClosedEvent { createdAt actor { __typename login }"
    " closer { __typename ... on Commit { oid } ... on PullRequest { number } } } } }"
)


def _pr_replies_batch_query(n: int) -> str:
    """Aliased ``pullRequest(number:)`` batch query for ``n`` sampled PRs.

    RFC-0022: the reply lists are :data:`PR_WINDOW_QUERY`'s, with their
    ``totalCount`` and ``pageInfo``, so a sampled PR's longer list is read on
    by :func:`_fetch_reply_followups` like one from a window page."""
    var_decls = ", ".join(f"$n{i}: Int!" for i in range(n))
    aliases = " ".join(
        f"p{i}: pullRequest(number: $n{i}) {{"
        " number"
        f" reviews(first: {PR_REVIEWS_FIRST}) {{ {_REPLY_PAGE_FIELDS} nodes {{ {_REVIEW_NODE_FIELDS} }} }}"
        f" comments(first: {PR_COMMENTS_FIRST}) {{ {_REPLY_PAGE_FIELDS} nodes {{ {_COMMENT_NODE_FIELDS} }} }}"
        " reviewThreads { totalCount } }"
        for i in range(n)
    )
    return (
        f"query PrRepliesBatch($owner: String!, $name: String!, {var_decls}) {{"
        f" rateLimit {{ cost }} r: repository(owner: $owner, name: $name) {{ {aliases} }} }}"
    )


def _pr_timeline_query(n: int) -> str:
    """Aliased ``pullRequest(number:)`` query reading the timeline of ``n`` PRs
    as :data:`PR_WINDOW_QUERY` does (:data:`_TIMELINE_SELECTION`): the repair of
    a record that read ``last: 5`` events (:func:`repair_pr_window_replies`)."""
    var_decls = ", ".join(f"$n{i}: Int!" for i in range(n))
    aliases = " ".join(f"p{i}: pullRequest(number: $n{i}) {{ number {_TIMELINE_SELECTION} }}" for i in range(n))
    return (
        f"query PrTimeline($owner: String!, $name: String!, {var_decls}) {{"
        f" rateLimit {{ cost }} r: repository(owner: $owner, name: $name) {{ {aliases} }} }}"
    )


def _is_heavy_query_error(msg: str) -> bool:
    """True when ``msg`` carries one of :data:`_HEAVY_QUERY_ERRORS`."""
    return any(needle in msg for needle in _HEAVY_QUERY_ERRORS)


#: Seconds to wait before each new read of a page or batch that failed because the network did. #89: on the phone, a
#: dropped connection 2 h 36 min into ``NousResearch/hermes-agent``'s history failed the whole project, and the next
#: pass began again at page 1; six of the 78 busiest projects were lost that way (24% of the step's time). Five waits
#: of 13 minutes in all, under the phone's 25-minute stuck watchdog.
NETWORK_RETRY_WAITS_S: tuple[int, ...] = (15, 45, 120, 300, 300)
#: What a failed transport call says when the network, not GitHub or the query, was the cause.
_NETWORK_ERRORS: tuple[str, ...] = (
    "No address associated with hostname",
    "Name or service not known",
    "Temporary failure in name resolution",
    "Network is unreachable",
    "Software caused connection abort",
    "Connection reset",
    "Remote end closed connection",
    "IncompleteRead",
    "EOF occurred in violation of protocol",
    "urlopen error",
)


def _is_network_error(exc: BaseException) -> bool:
    """True when ``exc`` is a call that never got an answer: ``gh`` itself timed out, or its message names a network
    failure (:data:`_NETWORK_ERRORS`). A GraphQL answer carrying an error is not one, and neither is GitHub's own
    502, 504 or "Gateway Timeout" (:func:`_is_heavy_query_error`): that is the query being too heavy, which a smaller
    page answers and a wait does not."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return True
    message = str(exc)
    return any(needle in message for needle in _NETWORK_ERRORS) and not _is_heavy_query_error(message)


def _wait(seconds: float) -> None:
    """The wait before a call is read again after a network failure; a stop (or the thread's cancel binding) ends it
    within a second (D4: a worker thread must not hold a stop back for up to 300 s)."""
    net.sleep(seconds)


def _execute_waiting_for_the_network(transport: "GraphqlTransport", query: str, variables: dict[str, Any], *,
                                     declined: Optional[Callable[[BaseException], bool]] = None) -> dict[str, Any]:
    """``transport.execute``, read again after each of :data:`NETWORK_RETRY_WAITS_S` when the call failed because
    the network did (:func:`_is_network_error`). Any other failure, and a network failure after the last wait,
    propagates. ``declined(exc)`` true leaves a failure to the caller at once: a page too heavy for GitHub is read
    again in smaller pages, not after a wait."""
    waits = iter(NETWORK_RETRY_WAITS_S)
    while True:
        try:
            return transport.execute(query, variables)
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            wait = next(waits, None)
            if wait is None or not _is_network_error(exc) or (declined is not None and declined(exc)):
                raise
        _wait(wait)


def _next_smaller_size(size: int) -> Optional[int]:
    """The largest rung of :data:`PR_PAGE_SIZE_LADDER` below ``size``, or ``None`` when there is none."""
    return max((rung for rung in PR_PAGE_SIZE_LADDER if rung < size), default=None)


def batch_retry_widths(width: int) -> tuple[int, ...]:
    """The widths an aliased batch of ``width`` aliases is read again at, in order, after a heavy-query failure
    (an HTTP 502 or 504, a timeout, a cut body) or an alias it left unresolved: the rungs of
    :data:`PR_PAGE_SIZE_LADDER` below ``width``, then one alias alone. ``()`` for a batch of one."""
    rungs = sorted((rung for rung in PR_PAGE_SIZE_LADDER if rung < width), reverse=True)
    return (*rungs, 1) if width > 1 else ()


class _PageSizer:
    """#51 per-page retry for one connection walk, down :data:`PR_PAGE_SIZE_LADDER`.

    :attr:`first` is the size of the next page. A page that fails with a
    heavy-query error (:func:`_is_heavy_query_error`) is fetched again from
    the same cursor at the next size down the ladder, and at the next again
    when that fails too, until pages of one size cover the failed page's span;
    the walk then returns to its own page size. A page of the last rung that
    fails, any other error, or a walk whose own size is no larger than the last
    rung propagates, failing the fetch as before. A failure inside the span
    goes on down the ladder from the size in use. The last page of a span is no
    longer than what is left of it, so page boundaries never move (a walk that
    stops at a boundary, like the sampled one at its cap, stops where a
    fault-free walk does). Pages read before the failure are kept: the project
    is not repeated from its first page.
    """

    def __init__(self, page_size: int) -> None:
        self.page_size = page_size
        self._size: Optional[int] = None  # the rung in use while a failed page's span is covered
        self._span_left = 0               # PRs of that span not yet read

    @property
    def first(self) -> int:
        """The ``first`` of the next page: the rung in use, or what is left of the failed page's span when that
        is less, so the span is covered exactly and the walk's later page boundaries never move."""
        return self.page_size if self._size is None else min(self._size, self._span_left)

    def can_retry_smaller(self, message: str) -> bool:
        """Whether a page that failed with ``message`` would be fetched again at the next size down."""
        return _is_heavy_query_error(message) and _next_smaller_size(self.first) is not None

    def retry_smaller(self, message: str) -> bool:
        """Whether the page that failed with ``message`` is fetched again at the next size down
        (and switch to it when it is)."""
        if not self.can_retry_smaller(message):
            return False
        if self._size is None:
            self._span_left = self.page_size
        self._size = _next_smaller_size(self.first)
        return True

    def page_done(self) -> None:
        """Count one page read successfully."""
        if self._size is None:
            return
        self._span_left -= self._size
        if self._span_left <= 0:
            self._size = None
            self._span_left = 0


def _execute_page(transport: "GraphqlTransport", query: str, variables: dict[str, Any],
                  sizer: _PageSizer,
                  raise_errors: Callable[[dict[str, Any]], None]) -> Optional[dict[str, Any]]:
    """One page of a connection walk, ``first`` set by ``sizer``.

    ``None`` when the page failed with a heavy-query error and is to be read
    again, smaller, from the same cursor (:class:`_PageSizer`). Any other
    GraphQL error goes to ``raise_errors(doc)``; a transport failure that is
    not a heavy-query one is read again after a wait when the network caused it
    (:func:`_execute_waiting_for_the_network`), else it propagates.
    """
    try:
        doc = _execute_waiting_for_the_network(
            transport, query, {**variables, "first": sizer.first},
            declined=lambda exc: sizer.can_retry_smaller(str(exc)))
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        if sizer.retry_smaller(str(exc)):
            return None
        raise
    errors = doc.get("errors")
    if errors:
        first_error = errors[0] if isinstance(errors, list) else errors
        message = str(first_error.get("message") if isinstance(first_error, Mapping) else first_error)
        if sizer.retry_smaller(message):
            return None
        raise_errors(doc)
    sizer.page_done()
    return doc


def reply_sample_numbers(owner: str, name: str, numbers: Sequence[int], size: int,
                         seed: int = PR_SAMPLE_SEED) -> frozenset[int]:
    """#51: the PR numbers whose replies a window of more than :data:`PR_CAP`
    PRs records: the ``size`` numbers with the smallest
    ``sha256("<seed>:<owner>/<name>:<number>")`` (repository lowercased).

    Bottom-k sampling: the hash gives each PR an independent uniform key, so
    the result is an equal-probability sample of ``size`` of the window's PRs,
    without replacement, that needs no weights. It is drawn by PR number with
    a fixed seed, so a rerun over the same PRs draws the same sample. A PR
    keeps its key as the window slides, so consecutive collections share most
    of their sample and their reply measures differ by what changed, not by a
    fresh draw; and two repositories' samples are independent.
    """
    repo = f"{owner}/{name}".lower()

    def key(number: int) -> bytes:
        return hashlib.sha256(f"{seed}:{repo}:{number}".encode("utf-8")).digest()

    return frozenset(heapq.nsmallest(max(size, 0), set(numbers), key=key))


#: #51: the core definitions (:mod:`porchlamp.core.gates`), re-exported for
#: the collector's callers: the reply-sample marker, the PR nodes whose
#: replies a record holds, and whether a record stopped short of its window.
REPLIES_RECORDED_KEY: str = gates.REPLIES_RECORDED_KEY
reply_sampled_prs = gates.reply_sampled_prs
pr_window_cut_short = gates.pr_window_cut_short


def pr_window_query_is_current(data: Any) -> bool:
    """Whether a ``pr_window`` envelope ``data`` was fetched by the current
    :data:`PR_WINDOW_QUERY` (its recorded ``query_sha256`` matches)."""
    return isinstance(data, Mapping) and data.get("query_sha256") == PR_WINDOW_QUERY_SHA256


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
        cmd: list[str] = ["api", "graphql", "-f", f"query={query}"]
        for key, value in variables.items():
            flag = "-F" if isinstance(value, int) and not isinstance(value, bool) else "-f"
            cmd += [flag, f"{key}={value}"]
        out = ghcli.run(cmd, timeout=self.timeout_seconds)
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


# --------------------------------------------------------------------------- #
# Per-PR derivation (D-IN-2, 02 §4.3) over recorded GraphQL nodes             #
# --------------------------------------------------------------------------- #

_REVIEW_BOILERPLATE_STRIP_RE = re.compile(r"https?://\S+|@[\w-]+|#\d+|\d+")
_REVIEW_WS_RE = re.compile(r"\s+")


def _normalize_review_text(text: str) -> str:
    """Normalize review or comment prose for boilerplate deduplication (RFC-0018).

    Lowercases, strips URLs, ``@mentions``, ``#123`` issue/PR references, and
    digits, and collapses whitespace so templated greetings or coverage pastes
    that differ only by PR number, contributor login, or percentage collapse to
    one key.
    """
    cleaned = _REVIEW_BOILERPLATE_STRIP_RE.sub("", (text or "").lower())
    return _REVIEW_WS_RE.sub(" ", cleaned).strip()


def _substantive_review_boilerplate(
    prs: Sequence[Mapping[str, Any]], maintainers: frozenset[str]
) -> frozenset[str]:
    """Normalized texts appearing on >= ``SUBSTANTIVE_REVIEW_BOILERPLATE_MIN_PRS``
    distinct sampled PRs across non-bot, non-author reviews or D-IN-2 maintainer
    comments meeting ``SUBSTANTIVE_REVIEW_BODY_CHARS`` (RFC-0018)."""
    pr_counts: dict[str, int] = {}
    for pr in prs:
        author = pr.get("author") or {}
        author_login = str(author.get("login") or "").lower()
        seen_on_pr: set[str] = set()
        for raw in (pr.get("reviews") or {}).get("nodes") or []:
            rev_author = raw.get("author") or {}
            rev_login = str(rev_author.get("login") or "").lower()
            if is_bot_actor(rev_author.get("__typename"), rev_author.get("login")):
                continue
            if rev_login == author_login:
                continue
            body = str(raw.get("body") or "")
            if gates.comment_length(body) >= SUBSTANTIVE_REVIEW_BODY_CHARS:
                norm = _normalize_review_text(body)
                if norm:
                    seen_on_pr.add(norm)
        for comment in (pr.get("comments") or {}).get("nodes") or []:
            if not gates.is_maintainer_response(comment, author_login, maintainers):
                continue
            body_text = str(comment.get("bodyText") or "")
            if gates.comment_length(body_text) >= SUBSTANTIVE_REVIEW_BODY_CHARS:
                norm = _normalize_review_text(body_text)
                if norm:
                    seen_on_pr.add(norm)
        for norm in seen_on_pr:
            pr_counts[norm] = pr_counts.get(norm, 0) + 1
    return frozenset(
        norm
        for norm, count in pr_counts.items()
        if count >= SUBSTANTIVE_REVIEW_BOILERPLATE_MIN_PRS
    )


def derive_pr_window(prs: Sequence[dict]) -> list[dict]:
    """Derive the per-PR responsiveness inputs from recorded PR nodes.

    ``prs`` are the raw ``pullRequests.nodes`` records of one windowed
    fetch (the shape in :data:`PR_WINDOW_QUERY`). Each result dict carries
    ``number``, ``is_external``, ``is_bot_author``, ``decided``, ``merged``,
    ``first_substantive_response_at`` / ``_kind``, ``ttfhr_hours`` and
    ``substantive_review``. The response is D-IN-2's
    (:func:`porchlamp.core.gates.first_substantive_response` over the
    window's :func:`porchlamp.core.gates.maintainer_logins`); ``ttfhr_hours`` is the opening-to-
    response span in hours (None when there is no candidate or the recorded
    response predates the opening). ``substantive_review`` (02 §4.3, RFC-0018) is
    True when the PR received either (a) a non-bot, non-author review with 1+
    inline comments or a non-boilerplate body of >= 80 effective characters
    (:func:`porchlamp.core.gates.comment_length`), or (b) a non-boilerplate
    D-IN-2 maintainer issue comment of >= 80 effective characters; ``None`` when
    the PR is outside the reply sample or excluded from the decided review
    denominator as a never-ready draft or unanswered author self-close within
    ``GATE5_RESPONSE_DEADLINE_DAYS``.

    When ``prs`` comes from a >2,000-PR light+sample window, ``maintainers`` is
    computed across all ``prs`` while reply-based fields (``first_substantive_response_at``,
    ``ttfhr_hours``, ``substantive_review``) are evaluated only on the 1,000-PR
    reply sample (:func:`reply_sampled_prs`), and set to ``None`` on unsampled PRs.
    """
    maintainers = gates.maintainer_logins(prs)
    sampled_prs = reply_sampled_prs(prs)
    sampled_ids = {id(p) for p in sampled_prs}
    boilerplate = _substantive_review_boilerplate(sampled_prs, maintainers)
    deadline = timedelta(days=constants.GATE5_RESPONSE_DEADLINE_DAYS)
    out: list[dict] = []
    for pr in prs:
        author = pr.get("author") or {}
        author_login = str(author.get("login") or "").lower()
        is_draft = bool(pr.get("isDraft", False))
        ready_for_review_at: Optional[str] = (
            pr.get("readyForReviewAt") if isinstance(pr.get("readyForReviewAt"), str) else None
        )
        closed_by_author: bool = bool(pr.get("closedByAuthor", False))
        closed_by_maint_at: Optional[str] = (
            pr.get("closedByMaintainerAt") if isinstance(pr.get("closedByMaintainerAt"), str) else None
        )
        if (
            closed_by_maint_at is None
            and pr.get("state") == "CLOSED"
            and not pr.get("mergedAt")
            and pr.get("closedByMaintainer") is True
        ):
            closed_by_maint_at = pr.get("closedAt") if isinstance(pr.get("closedAt"), str) else None

        timeline_conn = pr.get("timelineItems")
        timeline_nodes = timeline_conn.get("nodes") if isinstance(timeline_conn, Mapping) else None
        if isinstance(timeline_nodes, list):
            latest_ready_dt: Optional[datetime] = None
            latest_ready_at: Optional[str] = None
            latest_close_dt: Optional[datetime] = None
            latest_close_event: Optional[Mapping[str, Any]] = None
            for ev in timeline_nodes:
                if not isinstance(ev, Mapping):
                    continue
                ev_type = ev.get("__typename")
                ev_ts = ev.get("createdAt")
                ev_dt = _parse_iso(ev_ts) if isinstance(ev_ts, str) else None
                if ev_type == "ReadyForReviewEvent" and isinstance(ev_ts, str) and ev_dt is not None:
                    if latest_ready_dt is None or ev_dt >= latest_ready_dt:
                        latest_ready_dt, latest_ready_at = ev_dt, ev_ts
                elif ev_type == "ClosedEvent" and (
                    latest_close_event is None
                    or (ev_dt is not None and (latest_close_dt is None or ev_dt >= latest_close_dt))
                ):
                    latest_close_dt, latest_close_event = ev_dt, ev
            if ready_for_review_at is None and latest_ready_at is not None:
                ready_for_review_at = latest_ready_at
            if (
                latest_close_event is not None
                and not pr.get("mergedAt")
                and not closed_by_author
                and closed_by_maint_at is None
            ):
                actor = latest_close_event.get("actor")
                actor_map = actor if isinstance(actor, Mapping) else {}
                actor_login = str(actor_map.get("login") or "").lower()
                actor_typename = actor_map.get("__typename")
                if actor_login and actor_login == author_login:
                    closed_by_author = True
                elif actor_login and not is_bot_actor(actor_typename, actor_login):
                    ev_created = latest_close_event.get("createdAt")
                    closed_by_maint_at = (
                        ev_created
                        if isinstance(ev_created, str) and _parse_iso(ev_created) is not None
                        else (pr.get("closedAt") if isinstance(pr.get("closedAt"), str) else None)
                    )

        if id(pr) in sampled_ids:
            response = gates.first_substantive_response(pr, maintainers)
            best_at, best_kind = response if response is not None else (None, None)
            best_dt = _parse_iso(best_at)
            created_dt = _parse_iso(pr.get("createdAt"))
            other_reply_cut = (
                (best_kind == "comment" and gates.reply_connection_cut(pr.get("reviews")))
                or (best_kind == "review" and gates.reply_connection_cut(pr.get("comments")))
            )
            ttfhr: Optional[float] = None
            if (
                best_dt is not None
                and created_dt is not None
                and best_dt >= created_dt
                and not other_reply_cut
            ):
                ttfhr = (best_dt - created_dt).total_seconds() / 3600.0

            ready_dt = _parse_iso(ready_for_review_at)
            clock_start = (
                max(created_dt, ready_dt)
                if (created_dt is not None and ready_dt is not None)
                else (ready_dt or created_dt)
            )
            closed_dt = _parse_iso(pr.get("closedAt"))
            merged_dt = _parse_iso(pr.get("mergedAt"))
            closed_m_dt = _parse_iso(closed_by_maint_at)
            never_ready_draft = is_draft and ready_dt is None and closed_dt is not None
            answered_within_deadline = bool(
                clock_start is not None
                and (
                    (best_dt is not None and best_dt <= clock_start + deadline)
                    or (merged_dt is not None and clock_start <= merged_dt <= clock_start + deadline)
                    or (closed_m_dt is not None and clock_start <= closed_m_dt <= clock_start + deadline)
                )
            )
            had_prior_response = bool(
                best_dt is not None and closed_dt is not None and best_dt <= closed_dt
            )
            unanswered_early_self_close = bool(
                closed_by_author
                and merged_dt is None
                and closed_dt is not None
                and clock_start is not None
                and not answered_within_deadline
                and not had_prior_response
                and closed_dt <= clock_start + deadline
            )
            if never_ready_draft or unanswered_early_self_close:
                substantive_review: Optional[bool] = None
            else:
                has_substantive_review = any(
                    not is_bot_actor(
                        (raw.get("author") or {}).get("__typename"),
                        (raw.get("author") or {}).get("login"),
                    )
                    and str((raw.get("author") or {}).get("login") or "").lower() != author_login
                    and (
                        int((raw.get("comments") or {}).get("totalCount") or 0) >= 1
                        or (
                            gates.comment_length(str(raw.get("body") or ""))
                            >= SUBSTANTIVE_REVIEW_BODY_CHARS
                            and _normalize_review_text(str(raw.get("body") or ""))
                            not in boilerplate
                        )
                    )
                    for raw in ((pr.get("reviews") or {}).get("nodes") or [])
                )
                has_substantive_maint_comment = any(
                    gates.is_maintainer_response(comment, author_login, maintainers)
                    and gates.comment_length(str(comment.get("bodyText") or ""))
                    >= SUBSTANTIVE_REVIEW_BODY_CHARS
                    and _normalize_review_text(str(comment.get("bodyText") or ""))
                    not in boilerplate
                    for comment in ((pr.get("comments") or {}).get("nodes") or [])
                )
                has_substantive = has_substantive_review or has_substantive_maint_comment
                substantive_review = (
                    None
                    if not has_substantive and gates.replies_cut(pr)
                    else has_substantive
                )
        else:
            best_at, best_kind = None, None
            ttfhr = None
            substantive_review = None

        author_node = pr.get("author")
        author_created_at = (
            author_node.get("createdAt")
            if isinstance(author_node, Mapping)
            else None
        )
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
            "is_draft": is_draft,
            "ready_for_review_at": ready_for_review_at,
            "closed_by_author": closed_by_author,
            "closed_by_maintainer_at": closed_by_maint_at,
            "author_created_at": author_created_at,
        })
    return out


def ghost_rate_prs_from_window(
    prs: Sequence[Mapping[str, Any]],
) -> list[gates.GhostRatePR]:
    """Pair recorded PR nodes with their derived attributes for D-GATE-5 / GR_14.

    Reads the reply-sampled PRs (:func:`reply_sampled_prs`) while keeping
    :func:`derive_pr_window`'s full-window :func:`gates.maintainer_logins` set.
    Skips entries with unparseable ``createdAt`` and populates all
    :class:`~porchlamp.core.gates.GhostRatePR` fields.
    """
    sampled_ids = {id(p) for p in reply_sampled_prs(prs)}
    out: list[gates.GhostRatePR] = []
    for pr, d in zip(prs, derive_pr_window(prs)):
        if id(pr) not in sampled_ids:
            continue
        if _parse_iso(pr.get("createdAt")) is None:
            continue
        out.append(
            gates.GhostRatePR(
                created_at=str(pr["createdAt"]),
                is_external=bool(d["is_external"]),
                is_bot_author=bool(d["is_bot_author"]),
                first_substantive_response_at=d["first_substantive_response_at"],
                merged_at=pr.get("mergedAt") if _parse_iso(pr.get("mergedAt")) is not None else None,
                is_draft=bool(d.get("is_draft", False)),
                ready_for_review_at=d.get("ready_for_review_at"),
                closed_at=pr.get("closedAt") if _parse_iso(pr.get("closedAt")) is not None else None,
                closed_by_author=bool(d.get("closed_by_author", False)),
                closed_by_maintainer_at=d.get("closed_by_maintainer_at"),
                author_created_at=d.get("author_created_at"),
                number=d.get("number"),
                replies_cut=gates.replies_cut(pr),
            )
        )
    return out


def _run_aliased_pr_query(
    transport: GraphqlTransport,
    query: str,
    variables: dict[str, Any],
    numbers: Sequence[int],
) -> tuple[dict[int, Mapping[str, Any]], list[int], int]:
    """One aliased ``pullRequest(number:)`` call, alias ``p<i>`` naming
    ``numbers[i]``: ``(alias node by number, unresolved numbers, point cost)``.
    A heavy-query failure leaves every number unresolved, for the caller to
    retry narrower (:func:`_in_aliased_batches`); any other transport failure
    propagates. An alias with no node, or named by a GraphQL error, is
    unresolved; an error naming no alias leaves every number unresolved."""
    try:
        doc = transport.execute(query, variables)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        if _is_heavy_query_error(str(exc)):
            return {}, list(numbers), 0
        raise
    data = doc.get("data") or {}
    cost = int(((data.get("rateLimit") or {}).get("cost")) or 0)
    errors = [error for error in (doc.get("errors") or []) if isinstance(error, Mapping)]
    if len(errors) != len(doc.get("errors") or []) or any(not error.get("path") for error in errors):
        return {}, list(numbers), cost
    errored: set[str] = set()
    for error in errors:
        for part in (error.get("path") or []):
            if isinstance(part, str) and part.startswith("p"):
                errored.add(part)
    repo = data.get("r")
    if not isinstance(repo, Mapping):
        return {}, list(numbers), cost
    resolved: dict[int, Mapping[str, Any]] = {}
    unresolved: list[int] = []
    for i, num in enumerate(numbers):
        alias = f"p{i}"
        node = repo.get(alias)
        if not isinstance(node, Mapping) or alias in errored:
            unresolved.append(int(num))
            continue
        resolved[int(num)] = node
    return resolved, unresolved, cost


def _in_aliased_batches(
    numbers: Sequence[int],
    run: Callable[[Sequence[int]], tuple[dict[int, Mapping[str, Any]], list[int], int]],
    batch_size: int,
    failure: str,
) -> tuple[dict[int, Mapping[str, Any]], int]:
    """Run ``run`` (one aliased call, :func:`_run_aliased_pr_query`) over
    ``numbers`` in batches of ``batch_size``, retrying the numbers a batch left
    unresolved at :data:`PR_REPLIES_RETRY_WIDTHS`: ``(node by number, point
    cost)``. A number no width resolves raises ``RuntimeError(f"{failure}:
    <numbers>")``, failing the fetch."""
    out: dict[int, Mapping[str, Any]] = {}
    cost = 0

    def _run(pool: Sequence[int]) -> list[int]:
        nonlocal cost
        resolved, unresolved, c = run(pool)
        out.update(resolved)
        cost += c
        return unresolved

    fresh = list(dict.fromkeys(int(n) for n in numbers))
    while fresh:
        chunk, fresh = fresh[:batch_size], fresh[batch_size:]
        pool = _run(chunk)
        for width in PR_REPLIES_RETRY_WIDTHS:
            if not pool:
                break
            next_pool: list[int] = []
            for start in range(0, len(pool), width):
                next_pool.extend(_run(pool[start : start + width]))
            pool = next_pool
        if pool:
            raise RuntimeError(f"{failure}: {pool[:10]}")
    return out, cost


def _run_pr_replies_batch_query(
    owner: str,
    name: str,
    numbers: Sequence[int],
    transport: GraphqlTransport,
) -> tuple[dict[int, dict[str, Any]], list[int], int]:
    """One ``PrRepliesBatch`` call over ``numbers``: ``(replies by number,
    unresolved numbers, point cost)``. A heavy-query failure leaves every number
    unresolved, for :func:`_fetch_pr_replies_batch` to retry narrower; any other
    transport failure propagates."""
    variables: dict[str, Any] = {"owner": owner, "name": name}
    for i, num in enumerate(numbers):
        variables[f"n{i}"] = int(num)
    nodes, unresolved, cost = _run_aliased_pr_query(
        transport, _pr_replies_batch_query(len(numbers)), variables, numbers)
    resolved: dict[int, dict[str, Any]] = {
        num: {
            "reviews": node.get("reviews") or {"nodes": []},
            "comments": node.get("comments") or {"nodes": []},
            "reviewThreads": node.get("reviewThreads") or {"totalCount": 0},
        }
        for num, node in nodes.items()
    }
    return resolved, unresolved, cost


def _fetch_pr_replies_batch(
    owner: str,
    name: str,
    numbers: Sequence[int],
    transport: GraphqlTransport,
    batch_size: int = PR_REPLIES_BATCH_SIZE,
) -> tuple[dict[int, dict[str, Any]], int]:
    """Fetch ``reviews``, ``comments``, and ``reviewThreads`` for sampled PRs
    in aliased batches of :data:`PR_REPLIES_BATCH_SIZE`, retrying unresolved
    aliases at :data:`PR_REPLIES_RETRY_WIDTHS`."""
    replies, cost = _in_aliased_batches(
        numbers, lambda pool: _run_pr_replies_batch_query(owner, name, pool, transport),
        batch_size, f"pr replies batch failed for {owner}/{name} PRs")
    return {num: dict(reply) for num, reply in replies.items()}, cost


def _reply_followup_parts(pr: Mapping[str, Any]) -> dict[str, tuple[str, int]]:
    """RFC-0022: the reply lists of one PR node a follow-up read goes on with,
    as ``{field: (endCursor, first)}``: each of ``reviews`` and ``comments``
    that is cut (:func:`porchlamp.core.gates.reply_connection_cut`), names the
    cursor to go on from and holds fewer than :data:`PR_REPLY_NODE_CAP` nodes,
    read for the rest of its cap at most :data:`PR_REPLY_PAGE_SIZE` at a time."""
    parts: dict[str, tuple[str, int]] = {}
    for field in ("reviews", "comments"):
        connection = pr.get(field)
        if not gates.reply_connection_cut(connection):
            continue
        cursor = connection["pageInfo"].get("endCursor")
        nodes = connection.get("nodes")
        have = len(nodes) if isinstance(nodes, list) else 0
        if isinstance(cursor, str) and cursor and have < PR_REPLY_NODE_CAP:
            parts[field] = (cursor, min(PR_REPLY_PAGE_SIZE, PR_REPLY_NODE_CAP - have))
    return parts


def _reply_followup_query(
    numbers: Sequence[int], parts: Sequence[Mapping[str, tuple[str, int]]],
) -> tuple[str, dict[str, Any]]:
    """RFC-0022: one aliased ``PrRepliesFollowUp`` query and its variables
    (``owner`` and ``name`` left to the caller): alias ``p<i>`` reads, on PR
    ``numbers[i]``, the next page of each reply list ``parts[i]`` names
    (:func:`_reply_followup_parts`), in the first page's fields."""
    declarations: list[str] = []
    aliases: list[str] = []
    variables: dict[str, Any] = {}
    for i, (number, lists) in enumerate(zip(numbers, parts)):
        declarations.append(f"$n{i}: Int!")
        variables[f"n{i}"] = int(number)
        reads: list[str] = []
        for field, key, node_fields in (("reviews", "r", _REVIEW_NODE_FIELDS),
                                        ("comments", "c", _COMMENT_NODE_FIELDS)):
            if field not in lists:
                continue
            cursor, first = lists[field]
            declarations += [f"${key}f{i}: Int!", f"${key}a{i}: String!"]
            variables[f"{key}f{i}"] = int(first)
            variables[f"{key}a{i}"] = cursor
            reads.append(f"{field}(first: ${key}f{i}, after: ${key}a{i}) {{"
                         f" {_REPLY_PAGE_FIELDS} nodes {{ {node_fields} }} }}")
        aliases.append(f"p{i}: pullRequest(number: $n{i}) {{ number {' '.join(reads)} }}")
    query = (
        f"query PrRepliesFollowUp($owner: String!, $name: String!, {', '.join(declarations)}) {{"
        f" rateLimit {{ cost }} r: repository(owner: $owner, name: $name) {{ {' '.join(aliases)} }} }}"
    )
    return query, variables


def _merge_reply_followup(pr: dict[str, Any], node: Mapping[str, Any], fields: Iterable[str]) -> set[str]:
    """RFC-0022: append one follow-up read's page of each of ``fields`` to the
    PR node's recorded list (in place), oldest first, with that page's
    ``totalCount`` and ``pageInfo``. Returns the fields whose page cannot move
    the list on: missing, without ``pageInfo``, or adding no node while saying
    more remain. Such a list is left as it was, still cut, so its readers know
    it is not whole, and is not read on again (that read would repeat)."""
    stalled: set[str] = set()
    for field in fields:
        page = node.get(field)
        recorded = pr.get(field)
        new_nodes = page.get("nodes") if isinstance(page, Mapping) else None
        page_info = page.get("pageInfo") if isinstance(page, Mapping) else None
        if (not isinstance(recorded, Mapping) or not isinstance(new_nodes, list)
                or not isinstance(page_info, Mapping)
                or (not new_nodes and page_info.get("hasNextPage") is True)):
            stalled.add(field)
            continue
        merged = dict(recorded)
        merged["nodes"] = [*(recorded.get("nodes") or []), *new_nodes]
        merged["pageInfo"] = dict(page_info)
        if "totalCount" in page:
            merged["totalCount"] = page["totalCount"]
        pr[field] = merged
    return stalled


def _fetch_reply_followups(owner: str, name: str, prs: Sequence[dict[str, Any]],
                           transport: GraphqlTransport,
                           batch_size: int = PR_REPLIES_BATCH_SIZE) -> int:
    """RFC-0022: read on, by PR number, every reply list that a PR whose
    replies the record holds left cut, until it is whole or holds
    :data:`PR_REPLY_NODE_CAP` nodes; returns the point cost.

    A window page and a ``PrRepliesBatch`` lookup carry a PR's first
    :data:`PR_REVIEWS_FIRST` reviews and :data:`PR_COMMENTS_FIRST` comments,
    oldest first, with each list's ``pageInfo``. Each round reads the next page
    of every list left cut, in aliased batches of ``batch_size`` PRs retried
    narrower like the reply batches (:func:`_in_aliased_batches`): a PR no width
    resolves fails the fetch. Rounds stop when no list is left to read on, at
    most ``ceil(PR_REPLY_NODE_CAP / PR_REPLY_PAGE_SIZE)`` of them. A longer list
    keeps ``pageInfo.hasNextPage`` true, so its readers know it is cut
    (:func:`porchlamp.core.gates.replies_cut`). PRs of every author are read on:
    the 4.1 review proxy counts every merged PR's approvals. Nodes marked
    ``REPLIES_RECORDED_KEY: False`` (outside a reply sample) are not read, and a
    node recorded without ``pageInfo`` is never cut.
    """
    by_number: dict[int, dict[str, Any]] = {}
    for pr in prs:
        number = pr.get("number")
        if isinstance(number, int) and not isinstance(number, bool) and pr.get(REPLIES_RECORDED_KEY) is not False:
            by_number[number] = pr
    stalled: set[tuple[int, str]] = set()
    cost = 0
    for _round in range(-(-PR_REPLY_NODE_CAP // PR_REPLY_PAGE_SIZE)):
        todo: dict[int, dict[str, tuple[str, int]]] = {}
        for number, pr in by_number.items():
            lists = {field: part for field, part in _reply_followup_parts(pr).items()
                     if (number, field) not in stalled}
            if lists:
                todo[number] = lists
        if not todo:
            break

        def run(pool: Sequence[int], todo: Mapping[int, Mapping[str, tuple[str, int]]] = todo,
                ) -> tuple[dict[int, Mapping[str, Any]], list[int], int]:
            query, variables = _reply_followup_query(pool, [todo[number] for number in pool])
            return _run_aliased_pr_query(transport, query, {"owner": owner, "name": name, **variables}, pool)

        pages, round_cost = _in_aliased_batches(
            list(todo), run, batch_size, f"pr reply follow-up failed for {owner}/{name} PRs")
        cost += round_cost
        for number, node in pages.items():
            for field in _merge_reply_followup(by_number[number], node, todo[number]):
                stalled.add((number, field))
    return cost


# --------------------------------------------------------------------------- #
# Windowed fetch (D-IN-4)                                                     #
# --------------------------------------------------------------------------- #

def _shape_author_window_pr(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Shape a ``pr_window`` or ``author_history`` PR node to :data:`AUTHOR_WINDOW_QUERY`'s fields.

    Strips nested ``reviews``, ``comments``, ``reviewThreads``, ``isDraft``, and ``mergedBy`` while
    preserving ``labels`` and the latest ``ClosedEvent`` in ``timelineItems`` (RFC-0015 landing fields).
    """
    out: dict[str, Any] = {
        "number": raw.get("number"),
        "createdAt": raw.get("createdAt"),
        "closedAt": raw.get("closedAt"),
        "mergedAt": raw.get("mergedAt"),
        "state": raw.get("state"),
        "author": dict(raw["author"]) if isinstance(raw.get("author"), Mapping) else raw.get("author"),
        "authorAssociation": raw.get("authorAssociation"),
    }
    if "labels" in raw:
        out["labels"] = raw["labels"]
    if "timelineItems" in raw:
        tl = raw["timelineItems"]
        nodes = tl.get("nodes") if isinstance(tl, Mapping) else None
        if isinstance(nodes, list):
            closed_events = [n for n in nodes if isinstance(n, Mapping) and n.get("__typename") == "ClosedEvent"]
            out["timelineItems"] = {"nodes": closed_events[-1:] if closed_events else []}
        else:
            out["timelineItems"] = tl
    return out


@dataclass(frozen=True)
class PrWindowResult:
    """One windowed PR fetch: recorded nodes, truncation and point cost."""

    owner: str
    name: str
    now: datetime
    truncated: bool
    cost_points: int
    prs: tuple[dict, ...]
    sampled: bool = False
    window_end_cursor: Optional[str] = None
    boundary_overflow_prs: tuple[dict, ...] = ()
    window_exhausted: bool = False


def fetch_pr_window(owner: str, name: str, now: datetime,
                    transport: GraphqlTransport,
                    page_size: int = PAGE_SIZE,
                    *,
                    cap: Optional[int] = PR_CAP,
                    sample_cap: Optional[int] = PR_SAMPLE_CAP,
                    existing_prs: Optional[Sequence[dict]] = None,
                    existing_anchor: Optional[datetime] = None,
                    existing_truncated: bool = False) -> PrWindowResult:
    """Fetch the PRs ``owner/name`` opened in the trailing 180 days.

    Pages newest-first and stops when a returned PR was opened before the
    window start (the boundary is exclusive, a PR opened exactly at
    ``now - 180 d`` is inside), when no page remains, or when ``cap``
    records are collected without a reply sample (``sample_cap=None``) —
    in which case ``truncated`` is True (D-IN-4).
    When ``existing_prs`` is supplied (W6-3 incremental ledger refresh),
    stops paging once all unsettled PRs in the window have been refreshed and
    a settled PR is reached, then merges the delta with ``existing_prs``.
    ``cost_points`` sums each page's ``rateLimit.cost``. Raises
    ``RuntimeError`` on GraphQL errors, a missing repository, or transport
    failure.

    #51: ``cap`` (default :data:`PR_CAP`) bounds the PRs recorded with all
    fields. With ``sample_cap`` set (default :data:`PR_SAMPLE_CAP`) a window
    holding more PRs is not truncated: the walk finishes the page it is on,
    continues to the window start with :data:`PR_WINDOW_LIGHT_QUERY` in pages
    of :data:`PR_LIGHT_PAGE_SIZE`, and keeps replies on an equal-probability
    sample of every PR in the window (:func:`_apply_reply_sample`); the
    result is ``sampled``. A page that fails with a heavy-query error is read
    again in smaller pages from the same cursor (:class:`_PageSizer`). A
    ledger delta that reaches ``cap`` without meeting a settled PR has read
    exactly the first pages of a fresh fetch, so it continues as one and the
    existing records are not reused.

    RFC-0022: a page and a sample lookup carry each PR's first
    :data:`PR_REVIEWS_FIRST` reviews and :data:`PR_COMMENTS_FIRST` comments with
    each list's ``pageInfo``; every list they leave cut, on a PR whose replies
    the record holds, is then read on by PR number until whole or
    :data:`PR_REPLY_NODE_CAP` long (:func:`_fetch_reply_followups`), its cost in
    ``cost_points``. A ledger delta's PRs are read on before the merge.

    #89 (Q1): on the boundary page where the walk crosses ``now - 180 d``,
    any remaining PRs on that page older than ``now - 180 d`` are shaped to
    :data:`AUTHOR_WINDOW_QUERY`'s fields in ``boundary_overflow_prs`` alongside
    that page's ``endCursor`` (``window_end_cursor``) or ``window_exhausted``,
    so ``collect_author_history`` can seed ``[180 d, 0 d]`` directly from
    ``pr_window`` and resume pagination at ``window_end_cursor`` without
    re-fetching the newer 180 days.
    """
    window_start = now - timedelta(days=PR_WINDOW_DAYS)
    prs: list[dict] = []
    boundary_overflow_prs: list[dict] = []
    window_end_cursor: Optional[str] = None
    window_exhausted: bool = False
    crossed_window_start: bool = False
    cost = 0
    after: Optional[str] = None
    settled_nums: set[int] = set()
    remaining_unsettled: set[int] = set()
    ref_anchor = existing_anchor or now
    if existing_prs is not None:
        for ep in existing_prs:
            if isinstance(ep, Mapping) and ep.get("number") is not None:
                try:
                    num = int(ep["number"])
                except (TypeError, ValueError):
                    continue
                created = _parse_iso(ep.get("createdAt"))
                if ledger.is_pr_settled(ep, ref_anchor):
                    if created is not None and created >= window_start:
                        settled_nums.add(num)
                elif created is None or created >= window_start:
                    remaining_unsettled.add(num)

    def raise_errors(doc: dict[str, Any]) -> None:
        errors = doc.get("errors")
        first_error = errors[0] if isinstance(errors, list) and errors else errors
        message = first_error.get("message") if isinstance(first_error, Mapping) else first_error
        raise RuntimeError(f"graphql errors for {owner}/{name}: {str(message)[:160]}")

    sampling = cap is not None and sample_cap is not None
    stopped_early = False
    stopped_on_settled = False
    sizer = _PageSizer(page_size)
    page_info: Mapping[str, Any] = {}
    while cap is None or len(prs) < cap:
        variables: dict[str, Any] = {"owner": owner, "name": name}
        if after is not None:
            variables["after"] = after
        doc = _execute_page(transport, PR_WINDOW_QUERY, variables, sizer, raise_errors)
        if doc is None:
            continue
        data = doc.get("data") or {}
        cost += int((data.get("rateLimit") or {}).get("cost") or 0)
        if (data.get("r") or None) is None:
            raise RuntimeError(f"repository not found: {owner}/{name}")
        connection = (data["r"].get("pullRequests") or {})
        nodes = connection.get("nodes") or []
        for idx, raw in enumerate(nodes):
            created = _parse_iso(raw.get("createdAt"))
            if created is not None and created < window_start:
                stopped_early = True
                crossed_window_start = True
                for rem in nodes[idx:]:
                    rem_created = _parse_iso(rem.get("createdAt"))
                    if rem_created is not None and rem_created < window_start:
                        boundary_overflow_prs.append(_shape_author_window_pr(rem))
                break
            raw_num = raw.get("number")
            if isinstance(raw_num, int) and not isinstance(raw_num, bool):
                remaining_unsettled.discard(raw_num)
                if existing_prs is not None and raw_num in settled_nums and not remaining_unsettled:
                    prs.append(dict(raw))
                    stopped_early = True
                    stopped_on_settled = True
                    break
            prs.append(dict(raw))
            # Without a reply sample the record stops at exactly ``cap`` PRs. With
            # one, the page is finished: the light walk resumes after this page,
            # so leaving it part-way would drop its remaining PRs from the record.
            if not sampling and cap is not None and len(prs) >= cap:
                break
        page_info = connection.get("pageInfo") or {}
        if stopped_early:
            if crossed_window_start:
                if page_info.get("hasNextPage") and page_info.get("endCursor"):
                    window_end_cursor = str(page_info["endCursor"])
                else:
                    window_exhausted = True
            break
        if not page_info.get("hasNextPage") or not page_info.get("endCursor"):
            stopped_early = True
            window_exhausted = True
            break
        after = page_info["endCursor"]

    if sampling and not stopped_early and existing_prs is not None:
        # The ledger delta reached ``cap`` without meeting a settled PR: its pages
        # are exactly the first pages a fresh fetch reads, so it continues as one.
        existing_prs = None

    sampled = False
    if sampling and cap is not None and sample_cap is not None and not stopped_early:
        light = _PageSizer(PR_LIGHT_PAGE_SIZE)
        while page_info.get("hasNextPage") and page_info.get("endCursor"):
            doc = _execute_page(
                transport, PR_WINDOW_LIGHT_QUERY,
                {"owner": owner, "name": name, "after": page_info["endCursor"]},
                light, raise_errors,
            )
            if doc is None:
                continue
            data = doc.get("data") or {}
            cost += int((data.get("rateLimit") or {}).get("cost") or 0)
            if (data.get("r") or None) is None:
                raise RuntimeError(f"repository not found: {owner}/{name}")
            connection = data["r"].get("pullRequests") or {}
            reached_start = False
            nodes = connection.get("nodes") or []
            for idx, raw in enumerate(nodes):
                created = _parse_iso(raw.get("createdAt"))
                if created is not None and created < window_start:
                    reached_start = True
                    crossed_window_start = True
                    for rem in nodes[idx:]:
                        rem_created = _parse_iso(rem.get("createdAt"))
                        if rem_created is not None and rem_created < window_start:
                            boundary_overflow_prs.append(_shape_author_window_pr(rem))
                    break
                prs.append(dict(raw))
            page_info = connection.get("pageInfo") or {}
            if reached_start:
                if page_info.get("hasNextPage") and page_info.get("endCursor"):
                    window_end_cursor = str(page_info["endCursor"])
                else:
                    window_exhausted = True
                break
        if not crossed_window_start and (not page_info.get("hasNextPage") or not page_info.get("endCursor")):
            window_exhausted = True
        # The light walk has no cap: it ends at the window start or at the end
        # of the list, so the record holds the whole window.
        stopped_early = True
        if len(prs) > cap:
            cost += _apply_reply_sample(owner, name, prs, sample_cap, transport)
            sampled = True

    # RFC-0022: each reply list a page or a sample lookup left cut is read on by
    # PR number, so the record holds a PR's replies whole up to the cap.
    cost += _fetch_reply_followups(owner, name, prs, transport)

    if existing_prs is not None:
        merged = ledger.merge_pr_window_delta(
            existing_prs, prs, now=now, window_days=PR_WINDOW_DAYS, cap=None
        )
        trunc = (
            (not stopped_early and cap is not None and len(prs) >= cap)
            or (stopped_on_settled and existing_truncated)
        )
        return PrWindowResult(owner, name, now, trunc, cost, tuple(merged))
    is_truncated = not stopped_early and cap is not None and len(prs) >= cap
    return PrWindowResult(
        owner,
        name,
        now,
        is_truncated,
        cost,
        tuple(prs),
        sampled=sampled,
        window_end_cursor=None if is_truncated else window_end_cursor,
        boundary_overflow_prs=() if is_truncated else tuple(boundary_overflow_prs),
        window_exhausted=False if is_truncated else window_exhausted,
    )


def _apply_reply_sample(owner: str, name: str, prs: list[dict], size: int,
                        transport: GraphqlTransport) -> int:
    """#51: keep replies on the :func:`reply_sample_numbers` sample of ``prs``
    (in place); return the point cost of reading the replies it lacks.

    A sampled PR the light query read gets its replies from
    :func:`_fetch_pr_replies_batch` (the reply fields of
    :data:`PR_WINDOW_QUERY`). Every other PR loses its replies and is marked
    ``REPLIES_RECORDED_KEY: False``, so no reader mistakes the newest pages,
    whose replies the full query carried, for part of the sample.
    """
    numbers = [pr["number"] for pr in prs
               if isinstance(pr.get("number"), int) and not isinstance(pr.get("number"), bool)]
    sample = reply_sample_numbers(owner, name, numbers, size)
    missing: list[int] = []
    for pr in prs:
        num = pr.get("number")
        if isinstance(num, int) and not isinstance(num, bool) and num in sample:
            pr.pop(REPLIES_RECORDED_KEY, None)
            if "reviews" not in pr:
                missing.append(num)
            continue
        for field in ("reviews", "comments", "reviewThreads"):
            pr.pop(field, None)
        pr[REPLIES_RECORDED_KEY] = False
    if not missing:
        return 0
    replies, cost = _fetch_pr_replies_batch(owner, name, missing, transport)
    for pr in prs:
        num = pr.get("number")
        if isinstance(num, int) and not isinstance(num, bool) and num in replies:
            pr.update(replies[num])
    return cost


# --------------------------------------------------------------------------- #
# Envelope entry point (called from the scripts/gather.py hook)               #
# --------------------------------------------------------------------------- #

def collect_pr_window(raw_dir: Path, owner: str, name: str,
                      now: Optional[datetime] = None,
                      transport: Optional[GraphqlTransport] = None,
                      force: bool = False,
                      page_size: int = PAGE_SIZE,
                      *,
                      incremental_ledger: bool = False) -> str:
    """Collect the windowed PR envelope for one repository.

    Writes ``raw_dir/pr_window/<owner>__<name>.json`` — one envelope whose
    ``data`` carries the window metadata (``count``, ``truncated``,
    ``cost_points``, ``window_days``, ``cap``, ``query_sha256``), the recorded PR nodes and
    the per-PR derivation — and returns ``"ok"``. Returns ``"skip"`` when
    the envelope already exists and ``force`` is False. ``page_size`` is
    the GraphQL ``first`` per page; drop it for very active repositories
    whose full page exceeds the server-side query time budget.

    #57: the stored per-PR derivation (``derived``) records the rules of this
    collection run only; no measure reads it. Every reader recomputes the
    per-PR facts from the recorded nodes under the rules of the derive run
    (:func:`derive_pr_window_fields`, :func:`ghost_rate_prs_from_window`,
    :func:`closed_unread_counts`), so a rule change reaches every record.

    With ``incremental_ledger`` the existing envelope's settled PRs are
    reused only when it was fetched by the current query
    (:func:`pr_window_query_is_current`); an envelope from an older query is
    refetched in full, so its records cannot outlive the query change.

    #51: the ledger also refetches in full an envelope that is ``truncated``
    (a refresh that stops at the first settled PR would never reach back past
    its cut) or ``sampled`` (merging fresh PRs, every one carrying replies,
    into a sample would over-represent them). A window of more than
    :data:`PR_CAP` PRs is recorded ``sampled``, with the sample's
    ``sample_size``, ``sample_seed`` and ``sample_cap``.
    """
    key = f"{owner}__{name}"
    target = raw_dir / "pr_window" / f"{key}.json"
    if target.is_file() and not force and not incremental_ledger:
        return "skip"
    now = now or datetime.now(timezone.utc)
    existing_prs: Optional[list[dict]] = None
    existing_anchor: Optional[datetime] = None
    if incremental_ledger and target.is_file():
        try:
            prev_env = read_envelope(target)
            if (
                isinstance(prev_env.data, Mapping)
                and isinstance(prev_env.data.get("prs"), list)
                and pr_window_query_is_current(prev_env.data)
                and prev_env.data.get("truncated") is False
                and not prev_env.data.get("sampled")
            ):
                existing_prs = prev_env.data["prs"]
                existing_anchor = (
                    _parse_iso(prev_env.data.get("window_anchor"))
                    or _parse_iso(getattr(prev_env, "fetched_at", None))
                )
        except Exception:
            existing_prs = None
            existing_anchor = None
    result = fetch_pr_window(owner, name, now, transport or GhCliTransport(),
                             page_size=page_size, existing_prs=existing_prs,
                             existing_anchor=existing_anchor)
    data: dict[str, Any] = {
        "window_anchor": now.isoformat(),
        "query_sha256": PR_WINDOW_QUERY_SHA256,
        "window_days": PR_WINDOW_DAYS,
        "cap": PR_CAP,
        "count": len(result.prs),
        "truncated": result.truncated,
        "cost_points": result.cost_points,
        "prs": list(result.prs),
        "derived": derive_pr_window(result.prs),
    }
    if result.window_end_cursor is not None:
        data["window_end_cursor"] = result.window_end_cursor
    if result.boundary_overflow_prs:
        data["boundary_overflow_prs"] = list(result.boundary_overflow_prs)
    if result.window_exhausted:
        data["window_exhausted"] = True
    if result.sampled:
        data["sampled"] = True
        data["sample_cap"] = PR_SAMPLE_CAP
        data["sample_seed"] = PR_SAMPLE_SEED
        data["sample_size"] = sum(1 for pr in result.prs if pr.get(REPLIES_RECORDED_KEY) is not False)
    write_envelope(target, source="github-graphql:pr-window", data=data,
                   fetched_at=now.isoformat())
    return "ok"


def _at_legacy_size(connection: Any, size: int) -> bool:
    """Whether a recorded reply list holds ``size`` nodes and no ``pageInfo``:
    the shape of a list the query before RFC-0022 stopped at."""
    if not isinstance(connection, Mapping) or "pageInfo" in connection:
        return False
    nodes = connection.get("nodes")
    return isinstance(nodes, list) and len(nodes) >= size


def legacy_reply_cuts(data: Any) -> tuple[list[int], list[int]]:
    """RFC-0022: the PRs of a ``pr_window`` envelope ``data`` that the query
    before RFC-0022 may have cut short, as ``(replies, timeline)`` PR numbers;
    both empty for a record fetched by any other query (the current one is
    whole, and an older one differs in more than these sizes).

    ``replies`` are the PRs whose replies the record holds
    (:data:`REPLIES_RECORDED_KEY` is not false) and whose reviews hold
    :data:`LEGACY_REVIEWS_FIRST` nodes or whose comments hold
    :data:`LEGACY_COMMENTS_FIRST`, with no ``pageInfo``: the list may have run
    on. A list below its size was whole. ``timeline`` are the PRs whose
    timeline holds :data:`LEGACY_TIMELINE_LAST` events, of every PR, since the
    light query read it too."""
    if not isinstance(data, Mapping) or data.get("query_sha256") != LEGACY_PR_WINDOW_QUERY_SHA256:
        return [], []
    prs = data.get("prs")
    replies: list[int] = []
    timeline: list[int] = []
    for pr in prs if isinstance(prs, list) else []:
        number = pr.get("number") if isinstance(pr, Mapping) else None
        if not isinstance(number, int) or isinstance(number, bool):
            continue
        if pr.get(REPLIES_RECORDED_KEY) is not False and (
                _at_legacy_size(pr.get("reviews"), LEGACY_REVIEWS_FIRST)
                or _at_legacy_size(pr.get("comments"), LEGACY_COMMENTS_FIRST)):
            replies.append(number)
        if _at_legacy_size(pr.get("timelineItems"), LEGACY_TIMELINE_LAST):
            timeline.append(number)
    return replies, timeline


def repair_pr_window_replies(raw_dir: Path, owner: str, name: str,
                             now: Optional[datetime] = None,
                             transport: Optional[GraphqlTransport] = None) -> str:
    """RFC-0022: read again, by PR number, the PRs a stored ``pr_window``
    record fetched by the query before RFC-0022 may have cut short
    (:func:`legacy_reply_cuts`), and stamp the record with the current query.

    Returns ``"skip"`` when there is no readable record, it was fetched by any
    other query, or none of its lists sat at the old sizes; ``"ok"`` after the
    record was rewritten. The re-read PRs take the current query's first pages
    (``PrRepliesBatch``, then the follow-up reads of :func:`fetch_pr_window`,
    so a long thread is whole up to :data:`PR_REPLY_NODE_CAP`) and their
    timelines the current ``last: 100``; every other PR is already what the
    current query would record. The record then differs from a fresh fetch only
    in what happened since: a re-read thread may hold replies newer than the
    record's ``window_anchor``, as a PR's replies did for the window's own
    newest pages. ``replies_repaired`` records what was read again and its cost
    (added to ``cost_points``). A PR GitHub no longer answers for fails the
    repair, leaving the old record whole and unstamped, to be tried again.

    Costs about a point per batch of :data:`PR_REPLIES_BATCH_SIZE` cut PRs
    (against a point per page of 25 for refetching the window), which is why
    the phone runs this and not a refetch for the record the old query cut.
    """
    key = f"{owner}__{name}"
    target = raw_dir / "pr_window" / f"{key}.json"
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return "skip"
    # A legacy record carries this exact pair (json.dumps escapes any quote a PR
    # body holds), so a record of another query is passed over without parsing.
    if f'"query_sha256": "{LEGACY_PR_WINDOW_QUERY_SHA256}"' not in text:
        return "skip"
    try:
        document = json.loads(text)
    except ValueError:
        return "skip"
    data = document.get("data") if isinstance(document, dict) else None
    reply_numbers, timeline_numbers = legacy_reply_cuts(data)
    if not reply_numbers and not timeline_numbers:
        return "skip"
    now = now or datetime.now(timezone.utc)
    transport = transport or GhCliTransport()
    prs = data["prs"]
    by_number = {pr["number"]: pr for pr in prs if isinstance(pr, dict) and isinstance(pr.get("number"), int)}
    cost = 0
    if reply_numbers:
        replies, spent = _fetch_pr_replies_batch(owner, name, reply_numbers, transport)
        cost += spent
        for number in reply_numbers:
            by_number[number].update(replies[number])
        cost += _fetch_reply_followups(owner, name, [by_number[number] for number in reply_numbers], transport)
    if timeline_numbers:
        def run(pool: Sequence[int]) -> tuple[dict[int, Mapping[str, Any]], list[int], int]:
            return _run_aliased_pr_query(
                transport, _pr_timeline_query(len(pool)),
                {"owner": owner, "name": name, **{f"n{i}": int(number) for i, number in enumerate(pool)}}, pool)

        timelines, spent = _in_aliased_batches(
            timeline_numbers, run, PR_REPLIES_BATCH_SIZE, f"pr timeline re-read failed for {owner}/{name} PRs")
        cost += spent
        for number in timeline_numbers:
            timeline = timelines[number].get("timelineItems")
            if not isinstance(timeline, Mapping) or not isinstance(timeline.get("nodes"), list):
                raise RuntimeError(f"pr timeline re-read for {owner}/{name} #{number} returned no events list")
            by_number[number]["timelineItems"] = {"nodes": timeline["nodes"]}
    repaired = {
        **data,
        "query_sha256": PR_WINDOW_QUERY_SHA256,
        "cost_points": int(data.get("cost_points") or 0) + cost,
        "derived": derive_pr_window(prs),
        "replies_repaired": {
            "from_query_sha256": LEGACY_PR_WINDOW_QUERY_SHA256,
            "reply_prs": len(reply_numbers),
            "timeline_prs": len(timeline_numbers),
            "cost_points": cost,
            "repaired_at": now.isoformat(),
        },
    }
    write_envelope(target, source=document.get("source") or "github-graphql:pr-window", data=repaired,
                   fetched_at=document.get("fetched_at") or now.isoformat(),
                   http_status=document.get("http_status") or 200, etag=document.get("etag"),
                   collector_version=document.get("collector_version") or COLLECTOR_VERSION)
    return "ok"


# --------------------------------------------------------------------------- #
# Bundle fields (called from the scripts/derive.py hook)                      #
# --------------------------------------------------------------------------- #

#: The bundle fields the derive hook sets from the pr_window envelope.
PR_WINDOW_BUNDLE_FIELDS: tuple[str, ...] = (
    "pr_window_count",
    "pr_window_truncated",
    "pr_window_cost_points",
    "pr_window_external_measured_latency_count",
    "pr_window_ttfhr_median_hours",
    "pr_window_substantive_review_rate",
    "pr_window_outside_pr_count",
    "pr_window_closed_unread_count",
)


def closed_unread_counts(prs: Sequence[dict]) -> tuple[int, int]:
    """``(closed_unread, outside)`` over a recorded window's PR nodes: the
    ``closed_unread`` detector's input (03 section 6).

    ``outside`` counts the outside PRs: an external author association and a
    non-bot author (D-IN-1). ``closed_unread`` counts those closed and never
    merged with no D-IN-2 response ever recorded (:func:`derive_pr_window`'s
    ``first_substantive_response_at``, recomputed from the recorded nodes as
    GR_14 and D-GATE-5 recompute it).

    #51: both count the PRs whose replies the record holds
    (:func:`reply_sampled_prs`): the whole window up to :data:`PR_CAP` PRs,
    else its sample. The detector reads their share, which needs no weights.
    """
    sampled_ids = {id(p) for p in reply_sampled_prs(prs)}
    outside = closed_unread = 0
    for pr, d in zip(prs, derive_pr_window(prs)):
        if id(pr) not in sampled_ids:
            continue
        if not d["is_external"] or d["is_bot_author"]:
            continue
        outside += 1
        if d["decided"] and not d["merged"] and d["first_substantive_response_at"] is None:
            closed_unread += 1
    return closed_unread, outside


def derive_pr_window_fields(prw: Optional[dict]) -> dict[str, tuple[Any, str]]:
    """Bundle fields from a recorded ``pr_window`` envelope ``data``.

    Returns ``{field: (value, confidence)}`` for
    :data:`PR_WINDOW_BUNDLE_FIELDS`. The envelope-recorded facts (count,
    truncation, point cost) are ``verified``; the statistics computed here
    are ``derived``: the count of outside PRs with a measured TTFHR, the
    TTFHR median over them (02 §4 1.1), the share of decided outside PRs
    with a substantive review (02 §4 4.3), and the outside PRs and the
    closed-unread ones among them (:func:`closed_unread_counts`; absent when
    the envelope records no PR list). Empty dict when ``prw`` is None.

    #57: every per-PR fact is recomputed here from the recorded PR nodes
    (``prs``) by :func:`derive_pr_window` under the current rules, as
    D-GATE-5 and ``closed_unread`` recompute them. The envelope's stored
    ``derived`` list records the rules of the run that collected it (the bot
    list, D-IN-2's maintainers) and is never read: a rule change reaches
    every stored record at the next derive. An outside PR has an external
    author association and a non-bot author (D-IN-1,
    :func:`porchlamp.core.gates.is_outside_pr`): a bot's own pull requests
    (Dependabot, Renovate, a release bot) are not outside contributions,
    whoever answers them.

    #51: the statistics read the PRs whose replies the record holds (rows
    whose ``substantive_review`` is ``None`` were not sampled). A record that
    stopped short of its window (:func:`pr_window_cut_short`) keeps its
    recorded facts and refuses every statistic: ``(None, "missing")``.
    """
    if not prw:
        return {}
    if pr_window_cut_short(prw):
        fields: dict[str, tuple[Any, str]] = {
            "pr_window_count": (int(prw.get("count") or 0), "verified"),
            "pr_window_truncated": (bool(prw.get("truncated")), "verified"),
            "pr_window_cost_points": (int(prw.get("cost_points") or 0), "verified"),
            "pr_window_external_measured_latency_count": (None, "missing"),
            "pr_window_ttfhr_median_hours": (None, "missing"),
            "pr_window_substantive_review_rate": (None, "missing"),
        }
        if isinstance(prw.get("prs"), list):
            fields["pr_window_outside_pr_count"] = (None, "missing")
            fields["pr_window_closed_unread_count"] = (None, "missing")
        return fields
    prs = prw.get("prs")
    nodes = [pr for pr in prs if isinstance(pr, Mapping)] if isinstance(prs, list) else []
    outside = [d for d in derive_pr_window(nodes) if d["is_external"] and not d["is_bot_author"]]
    with_latency = [d["ttfhr_hours"] for d in outside if d["ttfhr_hours"] is not None]
    decided_outside = [d for d in outside if d["decided"] and d["substantive_review"] is not None]
    substantive = [d for d in decided_outside if d["substantive_review"]]
    fields = {
        "pr_window_count": (int(prw.get("count") or 0), "verified"),
        "pr_window_truncated": (bool(prw.get("truncated")), "verified"),
        "pr_window_cost_points": (int(prw.get("cost_points") or 0), "verified"),
        "pr_window_external_measured_latency_count": (len(with_latency), "derived"),
        "pr_window_ttfhr_median_hours": (
            round(statistics.median(with_latency), 4), "derived") if with_latency
        else (None, "derived"),
        "pr_window_substantive_review_rate": (
            round(len(substantive) / len(decided_outside), 4), "derived")
        if decided_outside else (None, "derived"),
    }
    if isinstance(prs, list):
        closed_unread, outside_count = closed_unread_counts(prs)
        fields["pr_window_outside_pr_count"] = (outside_count, "derived")
        fields["pr_window_closed_unread_count"] = (closed_unread, "derived")
    return fields


def derive_pr_window_landing_fields(prw: Optional[dict]) -> dict[str, tuple[Any, str]]:
    """RFC-0015 (#60): what a recorded ``pr_window`` envelope ``data`` shows of
    pull requests landed outside GitHub's merge (:mod:`porchlamp.core.landing`).

    Returns ``{field: (value, confidence)}`` for two bundle fields:

    * ``pr_window_landing_recorded`` (``verified``): whether every recorded PR
      node carries the fields landing reads
      (:func:`porchlamp.core.landing.landing_recorded`). A record fetched before
      #63 does not; its 1.5 TTM_p90 and 4.1 review proxy then read GitHub's
      merges alone, as they did before.
    * ``pr_window_landed_external_count`` (``derived``): the outside PRs (an
      external author association and a non-bot author, D-IN-1) the record shows
      landed, which 1.5 TTM_p90 counts as merged at their close. ``(None,
      "missing")`` on a record cut short of its window, whose statistics are all
      refused (:func:`derive_pr_window_fields`).

    Empty dict when ``prw`` is None or records no PR list.
    """
    if not prw or not isinstance(prw.get("prs"), list):
        return {}
    prs = [pr for pr in prw["prs"] if isinstance(pr, Mapping)]
    fields: dict[str, tuple[Any, str]] = {
        "pr_window_landing_recorded": (bool(prs) and all(landing.landing_recorded(pr) for pr in prs), "verified"),
    }
    if pr_window_cut_short(prw):
        fields["pr_window_landed_external_count"] = (None, "missing")
    else:
        fields["pr_window_landed_external_count"] = (
            sum(1 for pr in prs if _is_external_nonbot(pr) and landing.is_landed(pr)), "derived")
    return fields



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
# * the per-author ``search`` (the author's OLDEST 30 PRs in the repo,
#   ``repo:<nameWithOwner> is:pr author:login sort:created-asc`` — the prior-merge
#   history MR_first needs and the proof that a newcomer is one, D3-G) is
#   capped at :data:`AUTHOR_SEARCH_CAP` per repo, deterministically sampled
#   (:func:`_seeded_sample`) from the union when it is larger. A search that
#   fails with a GraphQL error (``history_unmeasured``) or that fell outside
#   the sample (``history_unsampled``) degrades that one author to no
#   recorded history rather than failing the whole repo's collection — and
#   excludes that author from MR_first's population entirely
#   (:func:`_no_prior_merged` returns ``None``), never assumed a first-timer;
# * RFC-0015 (#60): each window PR records its labels and its latest close
#   event with that event's closer (:data:`porchlamp.core.landing.PR_LANDING_FIELDS`),
#   so a PR the project landed outside GitHub's merge counts as merged, at its
#   close, wherever the cohorts read a merge (:mod:`porchlamp.core.landing`).
#   The per-author search records neither: a 25-alias search batch asking for
#   them failed with HTTP 502 when measured on ``pytorch/pytorch`` (2026-10-22).
#   The light history (RFC-0009, :func:`fetch_light_pr_history`) records both;
# * derivation (``derive_cohort_fields``, pure over the recorded envelope):
#   for each of DR_90 (1.2), MR_first (1.3), newcomer conversion (5.2) and
#   RR_90 (5.3) both a raw rate (over the identified cohort, no D-IN-3
#   eligibility or cap) and the D-IN-3-adjusted rate (eligible authors only,
#   each capped at floor(20% * n) items, oldest kept) are stored.
#
# Documented approximations: "no previously merged PR" (MR_first — the
# predicate is merge time, not creation time) is a statement about recorded
# history only — the 365-d window plus the sampled per-author oldest-30 search;
# a PR older than the window that the project landed outside GitHub's merge is
# known only from that search, which records no landing, so it does not count
# as a prior merge; a truncated newcomer band excludes candidates whose oldest
# recorded PR sits within ``TRUNCATION_MARGIN_DAYS`` of the truncation floor,
# since an even older, unrecorded PR would misclassify them as a newcomer.

#: D-IN-4: the author-history window — 365 d of PRs.
AUTHOR_WINDOW_DAYS: int = 365
#: D-IN-4 (#51): the whole 365-d window, bounded only for safety: far above the
#: busiest measured history (about 153,000 PRs), so a record that reaches it is
#: ``truncated`` and its cohort rates are refused, never computed on part of it.
AUTHOR_WINDOW_CAP: Optional[int] = 250_000
#: D-IN-4: newcomer band, older edge — the first PR was opened at most 365 d ago.
NEWCOMER_BAND_START_DAYS: int = 365
#: D-IN-4: newcomer band, newer edge — the first PR was opened at least 180 d ago.
NEWCOMER_BAND_END_DAYS: int = 180
#: D-IN-4: at most 20 sampled newcomer-cohort authors per repo.
NEWCOMER_SAMPLE_SIZE: int = 20
#: Fixed seed for deterministic sampling of the candidate pool.
NEWCOMER_SAMPLE_SEED: int = 20260912
#: Per-author history: the 30 oldest of the author's PRs in the repo (D3-G). Read newest
#: first, a veteran's early PRs fell outside the 30 and they read as a first-timer or a newcomer.
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
#: are imported from porchlamp.core.constants.
#: 5.2: the first PR converted when merged within 180 d of opening (merged on
#: GitHub or landed outside its merge, RFC-0015).
NEWCOMER_CONVERT_WINDOW_DAYS: int = 180
#: 5.3: a return needs a second PR within 90 d after the first PR's merge (or
#: landing, RFC-0015).
RETURN_WINDOW_DAYS: int = 90
#: At most this many per-author ``search`` calls per repo,
#: deterministically sampled (:func:`_seeded_sample`) from the union of the
#: decision cohort and the newcomer sample when that union is larger.
AUTHOR_SEARCH_CAP: int = 50
#: Batch size for aliased user eligibility queries. MEASURED 2026-11-02 (D4, workstation, live API): 15-login
#: batches now sit at GitHub's 10-second limit, 4 of 12 timed out (502 after about 10.7 s) and 1 came back
#: RESOURCE_LIMITS_EXCEEDED, the rest took 5.0-9.6 s; 8-login batches had no problem in 31 calls at 2.2-4.2 s,
#: no dearer per login (0.28-0.53 s against 0.33-0.64 s). A timeout costs extra primary points and the narrower
#: retries that follow. A login's record depends only on its own alias, so the width changes no value.
ELIGIBILITY_BATCH_SIZE: int = 8
#: Narrower retry widths for aliases a batch left unresolved.
ELIGIBILITY_RETRY_WIDTHS: tuple[int, ...] = (5, 1)
#: Aliased per-author searches per ``AuthorPrsSearchBatch`` call. MEASURED
#: 2026-09-22 on the live API: 10 one-search calls took 7.49 s and 10 points;
#: the same 10 searches as one aliased query took 2.08 s and 1 point and
#: returned node-for-node identical results; 25 aliases took 4.5-5.1 s and 1
#: point on the three busiest repos in the universe (392-440 external
#: authors), inside GitHub's 10 s query timeout. One search per call made
#: these searches 79 % of L4's projected ~112k GraphQL calls.
AUTHOR_SEARCH_BATCH_SIZE: int = 25
#: Narrower retry widths for search aliases a batch left unresolved.
AUTHOR_SEARCH_RETRY_WIDTHS: tuple[int, ...] = (5, 1)
#: D3-G, the project guard: the search ``repo:<nameWithOwner> is:pr`` must find at least this share of the
#: repository's own PR count (``pullRequests.totalCount``, read in the author window's first call). The search
#: index runs a median 0.27% below the total (72 projects, 2026-11-01); a repository the search cannot find under
#: the name asked (a moved one found 0 of 1,152) is far below it. When the guard fails no searched author is
#: measured: an empty search would read every one of them as a first-timer.
AUTHOR_SEARCH_GUARD_FACTOR: float = 0.95
#: Above this fraction of a repo's cohort authors being unmeasured,
#: row 1.2 (DR_90) and row 1.3 (MR_first)'s D-IN-3-adjusted rate
#: are reported unmeasured rather than computed on a collapsed population.
#: Imported from :mod:`porchlamp.core.constants`.

#: The 365-d window query: PRs in all three states, newest first, light
#: fields (no reviews/comments — the author section does not derive
#: D-IN-2). ``rateLimit`` carries ``cost`` and ``remaining``.
#: RFC-0015 (#60): each PR's labels and latest close event with its closer
#: (:data:`porchlamp.core.landing.PR_LANDING_FIELDS`), so the cohorts count a PR
#: landed outside GitHub's merge as merged. A 100-PR page costs 2 points where it
#: cost 1 (``pytorch/pytorch``'s newest page, measured 2026-10-22). Envelopes
#: recorded before lack both keys and their cohorts read GitHub's merges alone.
#: D3-G: the repository's own ``nameWithOwner`` (a moved repository answers to its new name
#: only) and its PR total, which the per-author searches are asked by and checked against
#: (:func:`_search_guard_passes`); both ride in the first page's call and cost no point.
AUTHOR_WINDOW_QUERY: str = (
    "query AuthorWindow($owner: String!, $name: String!, $first: Int, $after: String) {"
    " rateLimit { cost remaining }"
    " r: repository(owner: $owner, name: $name) {"
    " nameWithOwner total: pullRequests { totalCount }"
    " pullRequests(states: [OPEN, MERGED, CLOSED], first: $first, after: $after,"
    " orderBy: {field: CREATED_AT, direction: DESC}) {"
    " pageInfo { hasNextPage endCursor }"
    " nodes { number createdAt closedAt mergedAt state"
    " author { __typename login ... on User { createdAt } } authorAssociation"
    + landing.PR_LANDING_FIELDS + " }"
    " } } }"
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


def _author_search_batch_query(n: int, guard_at: Optional[int] = None) -> str:
    """Per-author PR history for ``n`` authors in one call: aliased
    ``search`` fields ``s0``..``s{n-1}`` (variables ``q0``..``q{n-1}``,
    ``s{i}`` always paired with ``q{i}``), each one author's oldest PRs in
    the repo (the search type ISSUE covers pull requests; the query is per
    D-IN-4 / 02 §4 1.3) with ``issueCount``, the count of all of them. Alias
    ``guard_at`` (if any) is the project guard: the repository's own PRs found
    by the same search, counted and not listed (:func:`_search_guard_passes`).
    Search nodes never carry the landing fields: 25 aliases with them returned
    HTTP 502 (2026-11-02)."""
    var_decls = " ".join(f"$q{i}: String!" for i in range(n))
    aliases = " ".join(
        f"s{i}: search(query: $q{i}, type: ISSUE, first: 1) {{ issueCount }}" if i == guard_at else
        f"s{i}: search(query: $q{i}, type: ISSUE, first: $first) {{"
        " issueCount nodes { ... on PullRequest { number createdAt closedAt mergedAt state } } }"
        for i in range(n))
    return (f"query AuthorPrsSearchBatch({var_decls} $first: Int) {{"
            f" rateLimit {{ cost remaining }} {aliases} }}")


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


#: Pages between append-only checkpoints during an ``AuthorWindow`` walk (#89 Q5).
AUTHOR_WINDOW_CHECKPOINT_PAGES: int = 5


@dataclass(frozen=True)
class AuthorHistoryResult:
    """One author-history fetch: the recorded 365-d window and its cost.

    D3-G: ``name_with_owner`` and ``pull_requests_total`` are the repository's own name and PR count as the
    first page that answered read them (``None`` when the reply carried none)."""

    owner: str
    name: str
    now: datetime
    truncated: bool
    cost_points: int
    remaining: Optional[int]
    prs: tuple[dict, ...]
    name_with_owner: Optional[str] = None
    pull_requests_total: Optional[int] = None


def _load_author_window_checkpoint(
    checkpoint_path: Path,
    now: datetime,
    window_start: datetime,
    seen_numbers: set[Any],
    prs: list[dict],
) -> Optional[tuple[Optional[str], int, Optional[int], Optional[str], Optional[int]]]:
    """Resume an interrupted ``AuthorWindow`` walk from ``checkpoint_path`` when its anchor is within
    :data:`constants.WINDOW_STALENESS_DAYS` of ``now`` (#89 Q5).

    Each JSONL line records only the delta of PRs and points since the previous flush, so a truncated trailing
    line from power loss is discarded while all earlier lines stay intact.
    """
    try:
        raw_lines = checkpoint_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    after: Optional[str] = None
    cost = 0
    remaining: Optional[int] = None
    name_with_owner: Optional[str] = None
    pull_requests_total: Optional[int] = None
    valid_lines = 0
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            break
        if not isinstance(entry, Mapping):
            break
        if valid_lines == 0:
            anchor = _parse_iso(entry.get("window_anchor") if isinstance(entry.get("window_anchor"), str) else None)
            if anchor is None or abs((now - anchor).total_seconds()) > constants.WINDOW_STALENESS_DAYS * 86400:
                try:
                    checkpoint_path.unlink(missing_ok=True)
                except OSError:
                    pass
                return None
        valid_lines += 1
        cost += int(entry.get("cost_delta") or 0)
        if entry.get("remaining") is not None:
            remaining = int(entry["remaining"])
        if isinstance(entry.get("name_with_owner"), str) and entry["name_with_owner"]:
            name_with_owner = entry["name_with_owner"]
        if isinstance(entry.get("pull_requests_total"), int) and not isinstance(entry.get("pull_requests_total"), bool):
            pull_requests_total = entry["pull_requests_total"]
        after = entry.get("after") if isinstance(entry.get("after"), str) and entry.get("after") else None
        for node in entry.get("prs") or []:
            if not isinstance(node, Mapping):
                continue
            created = _parse_iso(node.get("createdAt"))
            if created is not None and created < window_start:
                continue
            num = node.get("number")
            if num is not None and num in seen_numbers:
                continue
            if num is not None:
                seen_numbers.add(num)
            prs.append(dict(node))
    if valid_lines == 0 or after is None:
        return None
    return after, cost, remaining, name_with_owner, pull_requests_total


def _append_author_window_checkpoint(
    checkpoint_path: Path,
    now: datetime,
    after: Optional[str],
    cost_delta: int,
    remaining: Optional[int],
    name_with_owner: Optional[str],
    pull_requests_total: Optional[int],
    batch_prs: Sequence[dict],
) -> None:
    """Append one delta line to ``checkpoint_path`` (#89 Q5)."""
    if not after:
        return
    try:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "window_anchor": now.isoformat(),
            "after": after,
            "cost_delta": cost_delta,
            "remaining": remaining,
            "name_with_owner": name_with_owner,
            "pull_requests_total": pull_requests_total,
            "prs": list(batch_prs),
        }
        with checkpoint_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
    except OSError:
        pass


def fetch_author_window(owner: str, name: str, now: datetime,
                        transport: GraphqlTransport,
                        page_size: int = PAGE_SIZE,
                        *,
                        cap: Optional[int] = AUTHOR_WINDOW_CAP,
                        seed_prs: Sequence[dict] = (),
                        seed_after: Optional[str] = None,
                        seed_exhausted: bool = False,
                        seed_name_with_owner: Optional[str] = None,
                        seed_pull_requests_total: Optional[int] = None,
                        checkpoint_path: Optional[Path] = None) -> AuthorHistoryResult:
    """Fetch the PRs ``owner/name`` opened in the trailing 365 days.

    Pages newest-first and stops when a returned PR was opened before the
    window start (a PR opened exactly at ``now - 365 d`` is inside), when no
    page remains, or when ``cap`` records are collected —
    in which case ``truncated`` is True (D-IN-4). ``cost_points`` sums each
    page's ``rateLimit.cost``; ``remaining`` is the last-observed
    ``rateLimit.remaining``. Raises ``RuntimeError`` on GraphQL errors, a
    missing repository, or transport failure — a broken window fetch leaves
    no cohort computable for the whole repo, unlike a single bad per-author
    lookup (see :func:`collect_author_history`).

    #51: ``cap`` defaults to :data:`AUTHOR_WINDOW_CAP`, a safety bound far above
    any measured history, so the walk reads the whole 365 days (``None`` lifts
    the bound). A page that fails with a heavy-query
    error is read again in smaller pages from the same cursor
    (:class:`_PageSizer`).

    #89 (Q1, Q5): when ``seed_prs`` / ``seed_after`` are provided from an uncut
    ``pr_window`` envelope, the `[180 d, 0 d]` slice is reused and pagination
    resumes at ``seed_after`` (or returns immediately when ``seed_exhausted`` is
    True). When ``checkpoint_path`` is provided, appends a JSONL delta every
    :data:`AUTHOR_WINDOW_CHECKPOINT_PAGES` pages and resumes from it on retry.
    """
    window_start = now - timedelta(days=AUTHOR_WINDOW_DAYS)
    prs: list[dict] = []
    seen_numbers: set[Any] = set()
    for raw in seed_prs:
        created = _parse_iso(raw.get("createdAt"))
        if created is not None and created < window_start:
            continue
        num = raw.get("number")
        if num is not None and num in seen_numbers:
            continue
        if num is not None:
            seen_numbers.add(num)
        prs.append(dict(raw))
        if cap is not None and len(prs) >= cap:
            break

    cost = 0
    remaining: Optional[int] = None
    after: Optional[str] = seed_after
    name_with_owner: Optional[str] = seed_name_with_owner
    pull_requests_total: Optional[int] = seed_pull_requests_total

    def result(truncated: bool) -> AuthorHistoryResult:
        return AuthorHistoryResult(owner, name, now, truncated, cost, remaining, tuple(prs),
                                   name_with_owner, pull_requests_total)

    if seed_exhausted:
        return result(cap is not None and len(prs) >= cap)

    if checkpoint_path is not None and checkpoint_path.is_file():
        resumed = _load_author_window_checkpoint(checkpoint_path, now, window_start, seen_numbers, prs)
        if resumed is not None:
            after, ckpt_cost, ckpt_rem, ckpt_nwo, ckpt_total = resumed
            cost += ckpt_cost
            if ckpt_rem is not None:
                remaining = ckpt_rem
            if ckpt_nwo is not None:
                name_with_owner = ckpt_nwo
            if ckpt_total is not None:
                pull_requests_total = ckpt_total

    first_page = name_with_owner is None or pull_requests_total is None
    sizer = _PageSizer(page_size)
    pages_since_ckpt = 0
    ckpt_cost_delta = 0
    ckpt_prs: list[dict] = []

    while cap is None or len(prs) < cap:
        variables: dict[str, Any] = {"owner": owner, "name": name}
        if after is not None:
            variables["after"] = after
        doc = _execute_page(
            transport, AUTHOR_WINDOW_QUERY, variables, sizer,
            lambda failed: _raise_on_graphql_errors(failed, f"author window {owner}/{name}"),
        )
        if doc is None:
            continue
        data = doc.get("data") or {}
        rl = data.get("rateLimit") or {}
        page_cost = int(rl.get("cost") or 0)
        cost += page_cost
        ckpt_cost_delta += page_cost
        if rl.get("remaining") is not None:
            remaining = int(rl["remaining"])
        if (data.get("r") or None) is None:
            raise RuntimeError(f"repository not found: {owner}/{name}")
        if first_page:
            first_page = False
            nwo = data["r"].get("nameWithOwner")
            name_with_owner = nwo if isinstance(nwo, str) and nwo else None
            total = (data["r"].get("total") or {}).get("totalCount")
            pull_requests_total = total if isinstance(total, int) and not isinstance(total, bool) else None
        connection = data["r"].get("pullRequests") or {}
        for raw in connection.get("nodes") or []:
            created = _parse_iso(raw.get("createdAt"))
            if created is not None and created < window_start:
                return result(False)
            num = raw.get("number")
            if num is not None and num in seen_numbers:
                continue
            if num is not None:
                seen_numbers.add(num)
            prs.append(raw)
            ckpt_prs.append(raw)
            if cap is not None and len(prs) >= cap:
                break
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage") or not page_info.get("endCursor"):
            return result(False)
        after = page_info["endCursor"]
        pages_since_ckpt += 1
        if checkpoint_path is not None and pages_since_ckpt >= AUTHOR_WINDOW_CHECKPOINT_PAGES:
            _append_author_window_checkpoint(
                checkpoint_path, now, after, ckpt_cost_delta, remaining,
                name_with_owner, pull_requests_total, ckpt_prs,
            )
            pages_since_ckpt = 0
            ckpt_cost_delta = 0
            ckpt_prs = []
    return result(cap is not None and len(prs) >= cap)


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
        doc = _execute_waiting_for_the_network(transport, query, variables)
    except (RuntimeError, subprocess.TimeoutExpired):
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
        created_at = node.get("createdAt") if isinstance(node, dict) else None
        events = (((node.get("contributionsCollection") or {})
                   .get("contributionCalendar") or {}).get("totalContributions")
                  if isinstance(node, dict) else None)
        if isinstance(created_at, str) and isinstance(events, int) and not isinstance(events, bool):
            resolved[login] = {
                "account_created_at": created_at,
                "public_event_count": events,
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


#: The pool entry that stands for the project guard alias in a search batch (no login is empty).
_SEARCH_GUARD: str = ""


@dataclass(frozen=True)
class AuthorSearch:
    """One search alias' answer: the oldest :data:`AUTHOR_SEARCH_FIRST` PRs of the author as shaped records
    (``prs``) and ``issue_count``, how many PRs the search found in all. The project guard's answer has no PRs."""

    prs: list[dict]
    issue_count: int


def _run_author_search_query(owner: str, name: str, logins: Sequence[str],
                             transport: GraphqlTransport, *, repo: Optional[str] = None,
                             ) -> tuple[dict[str, AuthorSearch], list[str], int, Optional[int]]:
    """One ``AuthorPrsSearchBatch`` call over exactly ``logins``, searched under ``repo`` (the repository's
    ``nameWithOwner``; default ``owner/name``). :data:`_SEARCH_GUARD` among ``logins`` is the project guard alias.

    Returns ``(resolved, unresolved, cost, remaining)``; ``resolved`` maps a
    login to its :class:`AuthorSearch`. An alias is resolved only when its data is
    present with its ``issueCount`` AND no ``errors`` entry is addressed to it: one search that
    fails with any error is ``history_unmeasured`` (the rule of the
    one-search-per-call loop this replaces), so an alias carrying data next
    to an error of its own is not kept either. An ``errors`` entry without a
    path is addressed to no single alias and leaves every alias unresolved,
    as does a transport failure (``execute`` raises). So does a body with no
    ``data`` at all -- a rate-limit answer is JSON with neither ``data`` nor
    ``errors`` -- which the one-search loop read as an author with no PRs.
    """
    repo = repo or f"{owner}/{name}"
    guard_at = list(logins).index(_SEARCH_GUARD) if _SEARCH_GUARD in logins else None
    variables: dict[str, Any] = {
        f"q{i}": (f"repo:{repo} is:pr" if login == _SEARCH_GUARD
                  else f"repo:{repo} is:pr author:{login} sort:created-asc")
        for i, login in enumerate(logins)}
    variables["first"] = AUTHOR_SEARCH_FIRST
    try:
        doc = _execute_waiting_for_the_network(
            transport, _author_search_batch_query(len(logins), guard_at), variables)
    except (RuntimeError, subprocess.TimeoutExpired):
        return {}, list(logins), 0, None
    data = doc.get("data") or {}
    rl = data.get("rateLimit") or {}
    cost = int(rl.get("cost") or 0)
    remaining = int(rl["remaining"]) if rl.get("remaining") is not None else None
    errors = doc.get("errors") or []
    if any(not error.get("path") for error in errors):
        return {}, list(logins), cost, remaining
    errored = {str(error["path"][0]) for error in errors}
    resolved: dict[str, AuthorSearch] = {}
    unresolved: list[str] = []
    for i, login in enumerate(logins):
        node = data.get(f"s{i}")
        count = node.get("issueCount") if isinstance(node, Mapping) else None
        if node is None or f"s{i}" in errored or not isinstance(count, int) or isinstance(count, bool):
            unresolved.append(login)
            continue
        resolved[login] = AuthorSearch(
            [{"number": raw.get("number"), "createdAt": raw.get("createdAt"),
              "closedAt": raw.get("closedAt"), "mergedAt": raw.get("mergedAt"),
              "state": raw.get("state")}
             for raw in (node.get("nodes") or [])],
            count)
    return resolved, unresolved, cost, remaining


def _fetch_author_searches(owner: str, name: str, logins: Sequence[str],
                           transport: GraphqlTransport,
                           batch_size: int = AUTHOR_SEARCH_BATCH_SIZE,
                           *, repo: Optional[str] = None, guard: bool = False,
                           ) -> tuple[dict[str, Optional[AuthorSearch]], Optional[int], int, Optional[int]]:
    """Per-author PR history (the oldest :data:`AUTHOR_SEARCH_FIRST` PRs
    each author opened in the repo, and how many they opened in all) for every login in ``logins``.

    Batched like :func:`_fetch_eligibility_batch`: every alias that
    resolves is kept at once; only the logins whose alias did not are
    retried, at the narrower :data:`AUTHOR_SEARCH_RETRY_WIDTHS`, and a login
    still unresolved at width 1 maps to ``None`` -- the caller records it
    ``history_unmeasured``, never as an author with no PRs. ``logins`` is
    de-duplicated in first-seen order. With ``guard`` the project guard alias
    leads the first batch (24 authors and the guard) and is retried like an
    author; its count is the second item returned, ``None`` when it never
    resolved. Returns ``(histories, guard_count, cost, remaining)``.
    """
    out: dict[str, Optional[AuthorSearch]] = {}
    cost = 0
    remaining: Optional[int] = None

    def _run(pool: Sequence[str]) -> list[str]:
        nonlocal cost, remaining
        resolved, unresolved, c, r = _run_author_search_query(owner, name, pool, transport, repo=repo)
        out.update(resolved)
        cost += c
        if r is not None:
            remaining = r
        return unresolved

    fresh = ([_SEARCH_GUARD] if guard else []) + list(dict.fromkeys(logins))
    while fresh:
        chunk, fresh = fresh[:batch_size], fresh[batch_size:]
        pool = _run(chunk)
        for width in AUTHOR_SEARCH_RETRY_WIDTHS:
            next_pool: list[str] = []
            for start in range(0, len(pool), width):
                next_pool.extend(_run(pool[start:start + width]))
            pool = next_pool
        for login in pool:
            out[login] = None
    guard_search = out.pop(_SEARCH_GUARD, None)
    return out, (guard_search.issue_count if guard_search is not None else None), cost, remaining


def _search_guard_passes(search_count: Optional[int], pull_requests_total: Optional[int]) -> bool:
    """D3-G, the project guard: the repository-wide search found at least :data:`AUTHOR_SEARCH_GUARD_FACTOR` of
    the repository's own PR count. A count or a total that was not read fails it: the searches cannot be trusted."""
    if search_count is None or pull_requests_total is None:
        return False
    return search_count >= AUTHOR_SEARCH_GUARD_FACTOR * pull_requests_total


def _eligibility_with_cache(logins: Sequence[str], transport: GraphqlTransport,
                            now: datetime, user_cache: Optional[UserCache],
                            ) -> tuple[dict[str, dict], int, Optional[int]]:
    """D-IN-3 eligibility for ``logins``, reading and filling the D-COL-5
    user cache (:mod:`porchlamp.collect.user_cache`) when one is given.

    Only logins the cache cannot answer go to :func:`_fetch_eligibility_batch`.
    Every returned record carries ``eligibility_observed_at``: ``now`` for a
    value fetched by this call, the original lookup time for a value the
    cache supplied, ``None`` when the lookup failed.
    """
    cached: dict[str, dict] = {}
    if user_cache is not None:
        for login in dict.fromkeys(logins):
            hit = user_cache.get(login, now)
            if hit is not None:
                cached[login] = hit
    fetched, cost, remaining = _fetch_eligibility_batch(
        [login for login in logins if login not in cached], transport, ELIGIBILITY_BATCH_SIZE)
    out: dict[str, dict] = {}
    for login, record in fetched.items():
        measured = not record["eligibility_unmeasured"]
        out[login] = {**record, "eligibility_observed_at": now.isoformat() if measured else None}
        if measured and user_cache is not None:
            user_cache.put(login, record, now)
    for login, hit in cached.items():
        out[login] = {"account_created_at": hit["account_created_at"],
                      "public_event_count": hit["public_event_count"],
                      "eligibility_unmeasured": False,
                      "eligibility_observed_at": hit["observed_at"]}
    return out, cost, remaining


def fetch_light_pr_history(
    owner: str,
    name: str,
    transport: GraphqlTransport,
    *,
    page_size: int = PAGE_SIZE,
    cap: int = 2000,
) -> tuple[dict[str, list[dict]], int, Optional[int], bool]:
    """Fetch repo-wide light PR history (RFC-0009 / W6-5) via ``LIGHT_PR_HISTORY_QUERY``.

    Pages newest-first at 1 GraphQL point per page (no ``search(type: ISSUE)`` calls)
    and groups up to :data:`AUTHOR_SEARCH_FIRST` PRs per author login.
    Returns ``(by_author, cost_points, remaining, ok)``.

    RFC-0015: each grouped PR keeps the ``labels`` and ``timelineItems`` its page
    recorded (:data:`porchlamp.core.landing.PR_LANDING_FIELDS`), so the cohorts
    count an earlier PR landed outside GitHub's merge as a merge; a page without
    them adds neither key.
    """
    by_author: dict[str, list[dict]] = {}
    total_seen = 0
    cost = 0
    remaining: Optional[int] = None
    after: Optional[str] = None
    while total_seen < cap:
        variables: dict[str, Any] = {"owner": owner, "name": name, "first": page_size}
        if after is not None:
            variables["after"] = after
        try:
            doc = transport.execute(ledger.LIGHT_PR_HISTORY_QUERY, variables)
        except RuntimeError:
            return {}, cost, remaining, False
        if doc.get("errors"):
            return {}, cost, remaining, False
        data = doc.get("data") or {}
        rl = data.get("rateLimit") or {}
        cost += int(rl.get("cost") or 0)
        if rl.get("remaining") is not None:
            remaining = int(rl["remaining"])
        repo_node = data.get("repository") or data.get("r")
        if not isinstance(repo_node, Mapping):
            return {}, cost, remaining, False
        connection = repo_node.get("pullRequests") or {}
        nodes = connection.get("nodes") or []
        for raw in nodes:
            if not isinstance(raw, Mapping):
                continue
            total_seen += 1
            login = _login_of(dict(raw))
            if login:
                bucket = by_author.setdefault(login, [])
                if len(bucket) < AUTHOR_SEARCH_FIRST:
                    entry = {
                        "number": raw.get("number"),
                        "createdAt": raw.get("createdAt"),
                        "closedAt": raw.get("closedAt"),
                        "mergedAt": raw.get("mergedAt"),
                        "state": raw.get("state"),
                    }
                    entry.update({key: raw[key] for key in ("labels", "timelineItems") if key in raw})
                    bucket.append(entry)
            if total_seen >= cap:
                break
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage") or not page_info.get("endCursor"):
            break
        after = page_info["endCursor"]
    return by_author, cost, remaining, True


def _stored_dr90_band(raw_dir: Path, key: str) -> Optional[tuple[str, frozenset[str]]]:
    """``(window_anchor, logins)`` of 1.2 DR_90's cohort in the stored ``pr_window`` record of ``key``
    (:func:`porchlamp.core.gates.dr90_band_logins`), or ``None`` when the record is absent, unreadable, has no
    readable anchor, or stopped short of its window: then the band is not known and every cohort author is
    looked up, as before."""
    try:
        data = read_envelope(raw_dir / "pr_window" / f"{key}.json").data
    except (OSError, ValueError):
        return None
    if not isinstance(data, Mapping) or gates.pr_window_cut_short(data):
        return None
    anchor_raw = data.get("window_anchor")
    anchor = _parse_iso(anchor_raw) if isinstance(anchor_raw, str) else None
    prs = data.get("prs")
    if anchor is None or not isinstance(prs, list):
        return None
    return anchor_raw, gates.dr90_band_logins(prs, anchor)


def _load_pr_window_context(
    raw_dir: Path,
    key: str,
    owner: str,
    name: str,
    now: datetime,
) -> tuple[
    Optional[tuple[str, frozenset[str]]],
    list[dict],
    Optional[str],
    bool,
    Optional[str],
    Optional[int],
]:
    """Read ``raw_dir/pr_window/<key>.json`` once (#89 Q1, Q7): extract ``dr90_band`` and, when the record
    carries ``window_end_cursor`` / ``window_exhausted`` and RFC-0015 landing fields within
    :data:`constants.WINDOW_STALENESS_DAYS` of ``now``, shape its PRs for :func:`fetch_author_window` and free
    the full ``pr_window`` dict immediately."""
    try:
        data = read_envelope(raw_dir / "pr_window" / f"{key}.json").data
    except (OSError, ValueError):
        return None, [], None, False, None, None
    if not isinstance(data, Mapping) or gates.pr_window_cut_short(data):
        return None, [], None, False, None, None
    anchor_raw = data.get("window_anchor")
    anchor = _parse_iso(anchor_raw) if isinstance(anchor_raw, str) else None
    prs = data.get("prs")
    if anchor is None or not isinstance(prs, list):
        return None, [], None, False, None, None
    band = (anchor_raw, gates.dr90_band_logins(prs, anchor))

    has_cursor_meta = "window_end_cursor" in data or data.get("window_exhausted") is True
    fresh_anchor = abs((now - anchor).total_seconds()) <= constants.WINDOW_STALENESS_DAYS * 86400
    has_landing = all(landing.landing_recorded(p) for p in prs if isinstance(p, Mapping))
    if not (has_cursor_meta and fresh_anchor and has_landing):
        del data, prs
        return band, [], None, False, None, None

    window_start = now - timedelta(days=AUTHOR_WINDOW_DAYS)
    seed_prs: list[dict] = []
    seen_numbers: set[Any] = set()
    overflow = data.get("boundary_overflow_prs")
    overflow_list = overflow if isinstance(overflow, list) else []
    for raw in (*prs, *overflow_list):
        if not isinstance(raw, Mapping):
            continue
        created = _parse_iso(raw.get("createdAt"))
        if created is not None and created < window_start:
            continue
        num = raw.get("number")
        if num is not None and num in seen_numbers:
            continue
        if num is not None:
            seen_numbers.add(num)
        seed_prs.append(_shape_author_window_pr(raw))
    seed_after = data.get("window_end_cursor") if isinstance(data.get("window_end_cursor"), str) else None
    seed_exhausted = bool(data.get("window_exhausted"))
    seed_nwo: Optional[str] = None
    seed_total: Optional[int] = None
    if seed_exhausted:
        raw_nwo = data.get("name_with_owner")
        seed_nwo = raw_nwo if isinstance(raw_nwo, str) and raw_nwo else f"{owner}/{name}"
        raw_total = data.get("pull_requests_total")
        seed_total = (
            raw_total
            if isinstance(raw_total, int) and not isinstance(raw_total, bool)
            else len(prs) + len(overflow_list)
        )
    del data, prs, overflow, overflow_list
    return band, seed_prs, seed_after, seed_exhausted, seed_nwo, seed_total


def _author_window_prs_by_number_query(n: int) -> str:
    """Aliased ``pullRequest(number:)`` query fetching :data:`AUTHOR_WINDOW_QUERY` fields for ``n`` PRs (#89 Q1)."""
    var_decls = ", ".join(f"$n{i}: Int!" for i in range(n))
    aliases = " ".join(
        f"p{i}: pullRequest(number: $n{i}) {{"
        " number createdAt closedAt mergedAt state"
        " author { __typename login ... on User { createdAt } } authorAssociation"
        + landing.PR_LANDING_FIELDS + " }"
        for i in range(n)
    )
    return (
        f"query AuthorWindowPrsByNumber($owner: String!, $name: String!, {var_decls}) {{"
        f" rateLimit {{ cost remaining }} r: repository(owner: $owner, name: $name) {{ {aliases} }} }}"
    )


def _fetch_author_window_prs_by_number(
    owner: str,
    name: str,
    numbers: Sequence[int],
    transport: GraphqlTransport,
    batch_size: int = PR_REPLIES_BATCH_SIZE,
) -> tuple[Optional[dict[int, dict]], int, Optional[int]]:
    """Refresh specific PR numbers in :data:`AUTHOR_WINDOW_QUERY` shape (#89 Q1)."""
    resolved: dict[int, dict] = {}
    cost = 0
    remaining: Optional[int] = None
    todo = list(dict.fromkeys(numbers))
    for start in range(0, len(todo), batch_size):
        chunk = todo[start:start + batch_size]
        query = _author_window_prs_by_number_query(len(chunk))
        variables: dict[str, Any] = {"owner": owner, "name": name, **{f"n{i}": num for i, num in enumerate(chunk)}}
        try:
            doc = _execute_waiting_for_the_network(transport, query, variables)
        except (RuntimeError, subprocess.TimeoutExpired):
            return None, cost, remaining
        if doc.get("errors"):
            return None, cost, remaining
        data = doc.get("data") or {}
        rl = data.get("rateLimit") or {}
        cost += int(rl.get("cost") or 0)
        if rl.get("remaining") is not None:
            remaining = int(rl["remaining"])
        repo_node = data.get("r")
        if not isinstance(repo_node, Mapping):
            return None, cost, remaining
        for i, num in enumerate(chunk):
            node = repo_node.get(f"p{i}")
            if not isinstance(node, Mapping):
                return None, cost, remaining
            resolved[num] = _shape_author_window_pr(node)
    return resolved, cost, remaining


def _reuse_stale_author_window_tail(
    raw_dir: Path,
    target: Path,
    key: str,
    owner: str,
    name: str,
    now: datetime,
    transport: GraphqlTransport,
    seed_prs: Sequence[dict],
) -> Optional[tuple[list[dict], int, Optional[int], Optional[str], Optional[int]]]:
    """Cycle 2+ reuse (#89 Q1): combine fresh ``[180d, 0d]`` ``seed_prs`` from ``pr_window`` with settled
    ``[365d, 180d)`` PRs from a previous uncut ``author_history`` envelope in ``raw-stale-*/author_history/<key>.json``
    (or ``target`` when ``force=True``), refreshing only previously-``OPEN`` PRs by number."""
    candidates: list[Path] = []
    if target.is_file():
        candidates.append(target)
    if raw_dir.parent.is_dir():
        candidates.extend(
            sorted(raw_dir.parent.glob(f"raw-stale-*/author_history/{key}.json"), reverse=True)
        )
    if not candidates:
        return None

    window_start = now - timedelta(days=AUTHOR_WINDOW_DAYS)
    for cand in candidates:
        try:
            prev_data = read_envelope(cand).data
        except (OSError, ValueError):
            continue
        if not isinstance(prev_data, Mapping) or prev_data.get("truncated"):
            continue
        prev_anchor_raw = prev_data.get("window_anchor")
        prev_anchor = _parse_iso(prev_anchor_raw) if isinstance(prev_anchor_raw, str) else None
        prev_prs = prev_data.get("prs")
        if prev_anchor is None or not isinstance(prev_prs, list):
            continue
        age_seconds = (now - prev_anchor).total_seconds()
        if age_seconds < 0 or age_seconds > PR_WINDOW_DAYS * 86400:
            continue
        if not all(landing.landing_recorded(p) for p in prev_prs if isinstance(p, Mapping)):
            continue
        prev_nwo = prev_data.get("name_with_owner")
        prev_total = prev_data.get("pull_requests_total")
        if not (isinstance(prev_nwo, str) and prev_nwo and isinstance(prev_total, int) and not isinstance(prev_total, bool)):
            continue

        seen_numbers = {p.get("number") for p in seed_prs if p.get("number") is not None}
        tail_prs: list[dict] = []
        open_numbers: list[int] = []
        for raw in prev_prs:
            if not isinstance(raw, Mapping):
                continue
            created = _parse_iso(raw.get("createdAt"))
            if created is None or created < window_start or created > now:
                continue
            num = raw.get("number")
            if num is None or num in seen_numbers:
                continue
            seen_numbers.add(num)
            shaped = _shape_author_window_pr(raw)
            tail_prs.append(shaped)
            if shaped.get("state") == "OPEN" and isinstance(num, int) and not isinstance(num, bool):
                open_numbers.append(num)
        del prev_data, prev_prs

        cost = 0
        remaining: Optional[int] = None
        if open_numbers:
            refreshed, cost, remaining = _fetch_author_window_prs_by_number(owner, name, open_numbers, transport)
            if refreshed is None:
                return None
            tail_prs = [refreshed.get(p.get("number"), p) for p in tail_prs]

        combined = sorted(
            [*seed_prs, *tail_prs],
            key=lambda p: str(p.get("createdAt") or ""),
            reverse=True,
        )
        return combined, cost, remaining, prev_nwo, prev_total
    return None


def _prefilter_local_eligibility(
    logins: Sequence[str],
    window_prs: Sequence[dict],
    now: datetime,
    user_cache: Optional[UserCache] = None,
    author_records: Optional[Sequence[Mapping[str, Any]]] = None,
) -> tuple[dict[str, dict], list[str]]:
    """Resolve D-IN-3 eligibility locally at 0 GraphQL cost (#89 Q2a) before calling
    :func:`_eligibility_with_cache`:

    1. Non-``User`` actors (``__typename`` is a non-empty string other than ``"User"``, e.g. ``"Mannequin"``,
       ``"Organization"``) are confirmed ineligible.
    2. Accounts younger than :data:`constants.MIN_ACCOUNT_AGE_DAYS` at their latest in-window PR creation date
       (``latest_pr_created_at - author.createdAt < 90 d``) are confirmed ineligible for every PR in this window.
    3. ``User`` accounts with at least :data:`constants.MIN_PUBLIC_EVENTS` (5) distinct PRs in the repository
       (across ``window_prs`` and ``author_records`` from search/light-history) and at least
       :data:`constants.MIN_ACCOUNT_AGE_DAYS` of account age at their earliest in-window PR are confirmed
       eligible (each PR creation is itself a public contribution) and written to ``user_cache``.
    """
    if not logins:
        return {}, []
    target_set = set(logins)
    typenames: dict[str, set[str]] = {}
    created_at_by_login: dict[str, str] = {}
    pr_numbers_by_login: dict[str, set[Any]] = {}
    history_total_by_login: dict[str, int] = {}
    earliest_pr_by_login: dict[str, datetime] = {}
    latest_pr_by_login: dict[str, datetime] = {}

    for pr in window_prs:
        login = _login_of(pr)
        if not login or login not in target_set:
            continue
        author = pr.get("author") or {}
        if isinstance(author, Mapping):
            tname = author.get("__typename")
            if isinstance(tname, str) and tname:
                typenames.setdefault(login, set()).add(tname)
            acct_created = author.get("createdAt")
            if isinstance(acct_created, str) and acct_created and login not in created_at_by_login:
                created_at_by_login[login] = acct_created
        pr_created = _parse_iso(pr.get("createdAt"))
        if pr_created is not None:
            prev_min = earliest_pr_by_login.get(login)
            if prev_min is None or pr_created < prev_min:
                earliest_pr_by_login[login] = pr_created
            prev_max = latest_pr_by_login.get(login)
            if prev_max is None or pr_created > prev_max:
                latest_pr_by_login[login] = pr_created
        num = pr.get("number")
        pr_numbers_by_login.setdefault(login, set()).add(
            num if num is not None else pr.get("createdAt")
        )

    for rec in author_records or ():
        if not isinstance(rec, Mapping):
            continue
        login = str(rec.get("login") or "").lower()
        if not login or login not in target_set:
            continue
        htotal = rec.get("history_total")
        if isinstance(htotal, int) and not isinstance(htotal, bool) and htotal > 0:
            history_total_by_login[login] = max(history_total_by_login.get(login, 0), htotal)
        for hist_pr in rec.get("prs") or ():
            if not isinstance(hist_pr, Mapping):
                continue
            num = hist_pr.get("number")
            key = num if num is not None else hist_pr.get("createdAt")
            if key is not None:
                pr_numbers_by_login.setdefault(login, set()).add(key)

    min_age = timedelta(days=constants.MIN_ACCOUNT_AGE_DAYS)
    now_iso = now.isoformat()
    resolved: dict[str, dict] = {}
    remaining: list[str] = []

    for login in logins:
        tnames = typenames.get(login) or set()
        if tnames and "User" not in tnames:
            resolved[login] = {
                "account_created_at": None,
                "public_event_count": 0,
                "eligibility_unmeasured": False,
                "eligibility_observed_at": now_iso,
            }
            continue

        acct_iso = created_at_by_login.get(login)
        acct_dt = _parse_iso(acct_iso)
        latest_pr = latest_pr_by_login.get(login)
        earliest_pr = earliest_pr_by_login.get(login)
        pr_count = max(
            len(pr_numbers_by_login.get(login) or ()),
            history_total_by_login.get(login, 0),
        )

        if acct_dt is not None and latest_pr is not None and latest_pr - acct_dt < min_age:
            resolved[login] = {
                "account_created_at": acct_iso,
                "public_event_count": 0,
                "eligibility_unmeasured": False,
                "eligibility_observed_at": now_iso,
            }
            continue

        if (
            "User" in tnames
            and acct_dt is not None
            and earliest_pr is not None
            and pr_count >= constants.MIN_PUBLIC_EVENTS
            and earliest_pr - acct_dt >= min_age
        ):
            rec = {
                "account_created_at": acct_iso,
                "public_event_count": pr_count,
                "eligibility_unmeasured": False,
                "eligibility_observed_at": now_iso,
            }
            resolved[login] = rec
            if user_cache is not None:
                user_cache.put(login, rec, now)
            continue

        remaining.append(login)

    return resolved, remaining


def collect_author_history(raw_dir: Path, owner: str, name: str,
                           now: Optional[datetime] = None,
                           transport: Optional[GraphqlTransport] = None,
                           force: bool = False,
                           page_size: int = PAGE_SIZE,
                           user_cache: Optional[UserCache] = None,
                           *,
                           cap: Optional[int] = AUTHOR_WINDOW_CAP,
                           light_history: bool = False) -> str:
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
    count) is fetched via :func:`_fetch_eligibility_batch` for every author
    some score reads, never a sample of them, so DR_90 keeps its full
    cohort. Those are the authors of 1.2 DR_90's cohort in the stored
    ``pr_window`` record (:func:`_stored_dr90_band`), the per-author search
    sample and the newcomer sample; with no usable ``pr_window`` record that
    is every author in the union. #89: every other author (in the history's
    wider decision cohort, outside the search sample) keeps a record with
    ``eligibility_looked_up`` false and no account facts: 1.3 already leaves
    such an author out (``history_unsampled``) and 1.2 never joins it, so no
    score changes, and its failed lookups can no longer count toward the
    unmeasured-fraction gate. The envelope then names the ``pr_window`` it was
    narrowed to (``dr90_band_anchor``), and 1.2 refuses a different one. The
    per-author ``search`` (prior-merge history for
    MR_first) is capped at :data:`AUTHOR_SEARCH_CAP` and deterministically
    sampled (:func:`_seeded_sample`) when the union exceeds it; an author
    outside the sample is ``history_unsampled`` and, like a failed search
    (``history_unmeasured``), excluded from MR_first's population entirely
    (:func:`_no_prior_merged`) rather than assumed a first-timer. D3-G: the search
    reads each author's oldest :data:`AUTHOR_SEARCH_FIRST` PRs under the repository's
    own ``nameWithOwner`` and counts all of them (``history_total``, recorded above
    :data:`AUTHOR_SEARCH_FIRST`); a search that finds none of an author the window holds, or whose
    repository-wide guard (:func:`_search_guard_passes`) fails, leaves the searched authors
    ``history_unmeasured``, so the 20% gate (:data:`AUTHOR_UNMEASURED_MAX_FRACTION`) refuses
    1.3, 5.2 and 5.3 rather than score a veteran as a first-timer. The guard's inputs are
    recorded whenever a search ran (``name_with_owner``, ``pull_requests_total``, ``search_pr_count``,
    null when the guard alias never answered), and :func:`derive_cohort_fields` refuses on
    them: a project with many authors keeps the 20% gate below its line when the search covers
    only 50 of them.

    When ``light_history`` is True (RFC-0009 / W6-5), replaces the per-author
    ``search(type: ISSUE)`` queries and :data:`AUTHOR_SEARCH_CAP` sampling with
    repo-wide ``pullRequests`` pagination (:func:`fetch_light_pr_history`) and
    reuses settled author eligibility (:func:`ledger.is_cached_author_reusable`).
    """
    key = f"{owner}__{name}"
    target = raw_dir / "author_history" / f"{key}.json"
    if target.is_file() and not force:
        return "skip"
    now = now or datetime.now(timezone.utc)
    t = transport or GhCliTransport()

    prev_records_by_login: dict[str, dict] = {}
    prev_anchor_iso: str = ""
    if light_history and target.is_file():
        try:
            prev_env = read_envelope(target)
            if isinstance(prev_env.data, Mapping):
                prev_anchor_iso = str(prev_env.data.get("window_anchor") or "")
                for rec in prev_env.data.get("author_records") or []:
                    if isinstance(rec, Mapping) and isinstance(rec.get("login"), str):
                        prev_records_by_login[rec["login"].lower()] = dict(rec)
        except Exception:
            prev_records_by_login = {}
            prev_anchor_iso = ""

    band, seed_prs, seed_after, seed_exhausted, seed_nwo, seed_total = (
        (None, [], None, False, None, None)
        if light_history
        else _load_pr_window_context(raw_dir, key, owner, name, now)
    )
    if seed_prs and not seed_exhausted:
        reused_tail = _reuse_stale_author_window_tail(raw_dir, target, key, owner, name, now, t, seed_prs)
        if reused_tail is not None:
            seed_prs, tail_cost, tail_rem, seed_nwo, seed_total = reused_tail
            seed_exhausted = True
            seed_after = None
        else:
            tail_cost, tail_rem = 0, None
    else:
        tail_cost, tail_rem = 0, None

    checkpoint_path = raw_dir.parent / "state" / "partial" / "author_history" / f"{key}.pages.jsonl"
    window = fetch_author_window(
        owner, name, now, t, page_size=page_size, cap=cap,
        seed_prs=seed_prs, seed_after=seed_after, seed_exhausted=seed_exhausted,
        seed_name_with_owner=seed_nwo, seed_pull_requests_total=seed_total,
        checkpoint_path=checkpoint_path,
    )
    del seed_prs
    cost = window.cost_points + tail_cost
    remaining = window.remaining if window.remaining is not None else tail_rem
    sample = _select_newcomer_sample(window.prs, now, window.truncated)
    newcomers = sorted(set(sample))
    decision_logins = sorted(set(_decision_cohort_logins(window.prs, now)))
    needed = sorted(set(decision_logins) | set(newcomers))

    author_records: list[dict] = []
    reused_elig: dict[str, dict] = {}
    search_sample: set[str] = set()
    search_meta: dict[str, Any] = {}
    if light_history:
        light_by_author, light_cost, light_rem, light_ok = (
            fetch_light_pr_history(owner, name, t, page_size=page_size)
            if needed
            else ({}, 0, None, True)
        )
        cost += light_cost
        if light_rem is not None:
            remaining = light_rem

        for login in needed:
            prev_rec = prev_records_by_login.get(login)
            if prev_rec is not None and not prev_rec.get("eligibility_unmeasured", True):
                entry = ledger.AuthorEligibilityEntry(
                    login=login,
                    account_created_at=prev_rec.get("account_created_at"),
                    prior_commits=int(prev_rec.get("public_event_count") or 0),
                    checked_at=str(prev_rec.get("eligibility_observed_at") or prev_anchor_iso),
                )
                if ledger.is_cached_author_reusable(entry, now.isoformat()):
                    reused_elig[login] = {
                        "account_created_at": prev_rec.get("account_created_at"),
                        "public_event_count": prev_rec.get("public_event_count"),
                        "eligibility_unmeasured": False,
                        "eligibility_observed_at": prev_rec.get("eligibility_observed_at") or prev_anchor_iso,
                    }

            record: dict[str, Any] = {
                "login": login, "account_created_at": None, "public_event_count": None,
                "prs": [], "history_unmeasured": False, "history_unsampled": False,
                "eligibility_unmeasured": False,
            }
            if not light_ok:
                record["history_unmeasured"] = True
            else:
                record["prs"] = light_by_author.get(login, [])
            author_records.append(record)
    else:
        remaining_cap = max(0, AUTHOR_SEARCH_CAP - len(newcomers))
        other_logins = sorted(set(decision_logins) - set(newcomers))
        search_sample = set(newcomers) | set(_seeded_sample(other_logins, remaining_cap))

        searched = [login for login in needed if login in search_sample]
        repo_searched = window.name_with_owner or f"{owner}/{name}"
        histories, search_count, search_cost, search_remaining = _fetch_author_searches(
            owner, name, searched, t, repo=repo_searched, guard=bool(searched))
        project_guard_failed = bool(searched) and not _search_guard_passes(
            search_count, window.pull_requests_total)
        if searched:
            # The guard's inputs, so derive can refuse on a failed guard and a record can say why (D3 3.4).
            search_meta = {"name_with_owner": repo_searched, "pull_requests_total": window.pull_requests_total,
                           "search_pr_count": search_count}
        cost += search_cost
        if search_remaining is not None:
            remaining = search_remaining
        for login in needed:
            record = {
                "login": login, "account_created_at": None, "public_event_count": None,
                "prs": [], "history_unmeasured": False, "history_unsampled": False,
                "eligibility_unmeasured": False,
            }
            found = histories.get(login)
            if login not in search_sample:
                record["history_unsampled"] = True
            elif project_guard_failed or found is None or found.issue_count == 0:
                # D3-G: the repository-wide search does not find the repository under the name asked, an alias
                # unresolved at every width, or a search that finds none of an author whose PR the window
                # holds (every searched author has one): not an author with no history.
                record["history_unmeasured"] = True
            else:
                record["prs"] = found.prs
                if found.issue_count > AUTHOR_SEARCH_FIRST:
                    record["history_total"] = found.issue_count
            author_records.append(record)

    read = set() if band is None else band[1] | search_sample | set(newcomers)
    skipped = set() if band is None else set(needed) - read
    elig_candidates = [login for login in needed if login not in reused_elig and login not in skipped]
    local_elig, elig_needed = _prefilter_local_eligibility(
        elig_candidates, window.prs, now, user_cache, author_records=author_records
    )
    elig, elig_cost, elig_remaining = _eligibility_with_cache(elig_needed, t, now, user_cache)
    cost += elig_cost
    if elig_remaining is not None:
        remaining = elig_remaining
    elig.update(local_elig)
    elig.update(reused_elig)
    for record in author_records:
        login = record["login"]
        if login in skipped:
            record.update({"eligibility_looked_up": False, "eligibility_observed_at": None})
        else:
            record.update(elig[login])

    data: dict[str, Any] = {
        "window_anchor": now.isoformat(),
        "window_days": AUTHOR_WINDOW_DAYS,
        "cap": cap,
        "count": len(window.prs),
        "truncated": window.truncated,
        "cost_points": cost,
        "rate_limit_remaining": remaining,
        "prs": list(window.prs),
        "sampled_authors": list(sample),
        "author_records": author_records,
    }
    data.update(search_meta)
    if skipped and band is not None:
        data["dr90_band_anchor"] = band[0]
    write_envelope(target, source="github-graphql:author-history", data=data,
                   fetched_at=now.isoformat())
    try:
        checkpoint_path.unlink(missing_ok=True)
    except OSError:
        pass
    return "ok"


#: D-IN-3: ``is_eligible_account``/``apply_author_cap`` are imported from
#: :mod:`porchlamp.core.hygiene`.


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
    search PRs, deduplicated by PR number (search entries win).

    RFC-0015: a search entry wins only the fields it records; a window node's
    other fields -- its labels and latest close event, which the per-author
    search does not record -- are kept, so a PR landed outside GitHub's merge
    stays landed when the search found it too."""
    seen: dict[Any, dict] = {}
    for pr in window_prs:
        if _login_of(pr) == login:
            seen[pr.get("number")] = pr
    for pr in (record or {}).get("prs") or []:
        number = pr.get("number")
        seen[number] = {**seen[number], **pr} if number in seen else pr
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
    author's search history. A PR landed outside GitHub's merge counts as
    merged at its close (:func:`porchlamp.core.landing.merged_or_landed_at`,
    RFC-0015).

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
        (other_merged := _parse_iso(landing.merged_or_landed_at(other))) is not None
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
    (``_``-prefixed) facts :func:`derive_cohort_fields` reduces to rates.

    RFC-0015: a PR landed outside GitHub's merge counts as merged at its close
    (:func:`porchlamp.core.landing.merged_or_landed_at`), for the conversion, the
    return clock and each roster entry's ``merged_at``; ``_first_landed`` marks a
    first PR merged that way rather than by GitHub."""
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
        record = records.get(login) or {}
        if record.get("history_unsampled") or record.get("history_unmeasured"):
            continue
        history = sorted(
            (item for pr in _author_pr_history(window_prs, record, login)
             if (item := (_parse_iso(pr.get("createdAt")), pr.get("number"), pr))[0] is not None),
            key=lambda item: (item[0], item[1]))
        if not history:
            continue
        first_created, _first_number, first_pr = history[0]
        if first_created < band_start:
            continue  # recorded evidence of an even-older PR: not a genuine newcomer
        followups = [pr for _c, _n, pr in history[1:]]
        record = records.get(login) or {}
        first_merged_at = landing.merged_or_landed_at(first_pr)
        first_merged = _parse_iso(first_merged_at)
        converted = (first_merged is not None
                     and first_merged - first_created <= timedelta(days=NEWCOMER_CONVERT_WINDOW_DAYS))
        returned = first_merged is not None and any(
            (c := _parse_iso(pr.get("createdAt"))) is not None
            and c > first_merged
            and c - first_merged <= timedelta(days=RETURN_WINDOW_DAYS)
            for pr in followups)
        authors.append({
            "login": login,
            # The account's creation date, carried through to porchlamp.core.models.NewcomerAuthor;
            # None when unmeasured.
            "account_created_at": record.get("account_created_at"),
            "first_pr": {"number": first_pr.get("number"),
                         "created_at": first_pr.get("createdAt"),
                         "merged_at": first_merged_at},
            "followups": [{"number": pr.get("number"),
                           "created_at": pr.get("createdAt"),
                           "merged_at": landing.merged_or_landed_at(pr)} for pr in followups],
            "_eligible": _account_eligibility(record, first_pr.get("createdAt")),
            "_first_created": first_created,
            "_first_merged": first_merged,
            "_first_landed": landing.is_landed(first_pr),
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
    "author_history_landing_recorded",
    "decision_cohort_coverage_gap_days", "cohort_decision_pr_numbers",
    "authors_total", "authors_excluded_unsampled",
    "authors_excluded_unmeasured", "authors_excluded_ineligible",
    "cohort_unmeasured_detail",
    "dr90_decision_rate_raw", "dr90_decision_pr_count_raw",
    "dr90_decision_rate_author_history_cohort", "dr90_decision_pr_count_author_history_cohort",
    "mr_first_rate_raw", "mr_first_pr_count_raw",
    "mr_first_rate", "mr_first_pr_count", "mr_first_landed_pr_count",
    "newcomer_cohort_author_count", "cohort_newcomer_authors",
    "newcomer_conversion_raw", "newcomer_conversion_raw_author_count",
    "newcomer_conversion", "newcomer_conversion_author_count",
    "newcomer_conversion_landed_author_count",
    "rr90_return_rate_raw", "rr90_return_raw_author_count",
    "rr90_return_rate", "rr90_return_author_count", "rr90_return_landed_author_count",
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

    RFC-0015 (#60): MR_first, its no-prior-merge predicate, newcomer conversion and
    RR_90 count a PR landed outside GitHub's merge as merged at its close
    (:func:`porchlamp.core.landing.merged_or_landed_at`); DR_90 is unchanged (a close
    was always a decision). How many of each adjusted rate's PRs or authors that
    makes is recorded beside the rate's own count -- ``mr_first_landed_pr_count``,
    ``newcomer_conversion_landed_author_count`` (converted by a landing) and
    ``rr90_return_landed_author_count`` (first PR landed) -- and
    ``author_history_landing_recorded`` says whether the record's window PRs carry
    the landing fields at all: an envelope recorded before them does not, and its
    rates count GitHub's merges alone.
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
    out["author_history_landing_recorded"] = (
        bool(window_prs) and all(landing.landing_recorded(pr) for pr in window_prs), "verified")

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
    # D3-G (RB2-D): a record that carries the project guard's inputs and fails them refuses 1.3, 5.2 and 5.3
    # whatever the 20% gate says (it divides by every author record, and the search covers only 50). A record
    # without ``search_pr_count`` (every record collected before) takes the path it always did.
    guard_failed = "search_pr_count" in ah and not _search_guard_passes(
        ah.get("search_pr_count"), ah.get("pull_requests_total"))
    guard_detail = (f"the per-author search failed its project guard: repo:{ah.get('name_with_owner')} is:pr "
                    f"found {ah.get('search_pr_count')} of the repository's {ah.get('pull_requests_total')} PRs "
                    f"(needs {AUTHOR_SEARCH_GUARD_FACTOR:.0%}), so no searched author is measured")
    collector_failed = unmeasured_fraction > AUTHOR_UNMEASURED_MAX_FRACTION or guard_failed
    # #51 rule 5: a record cut short of the decision cohort's start yields no
    # cohort rate (the core's one definition, which the bundle reader also uses).
    decision_window_cut = gates.record_cut_short(ah, DECISION_COHORT_START_DAYS)

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
    raw_mr_merged = sum(1 for pr in raw_mr_decided if landing.merged_or_landed_at(pr) is not None)
    out["mr_first_rate_raw"] = (_ratio(raw_mr_merged, len(raw_mr_decided)), "derived")
    out["mr_first_pr_count_raw"] = (len(raw_mr_decided), "derived")

    if collector_failed or decision_window_cut:
        # Collector failure or cut window gate: mark missing.
        if decision_window_cut:
            detail = (f"author_history window truncated before reaching the {DECISION_COHORT_START_DAYS}d "
                      f"decision-cohort start ({gap_days}d short)")
        elif guard_failed:
            detail = guard_detail
        else:
            detail = f"{authors_excluded_unmeasured} of {authors_total} authors unmeasured"
        out["dr90_decision_rate_author_history_cohort"] = (None, "missing")
        out["dr90_decision_pr_count_author_history_cohort"] = (None, "missing")
        out["mr_first_rate"] = (None, "missing")
        out["mr_first_pr_count"] = (None, "missing")
        out["mr_first_landed_pr_count"] = (None, "missing")
        out["cohort_unmeasured_detail"] = (detail, "verified")
    else:
        dr90_kept = _cap_prs([pr for pr in cohort_prs if _eligible(pr, records) is True])
        dr90_decided = sum(1 for pr in dr90_kept if _decided_within_days(pr, DECISION_WINDOW_DAYS))
        out["dr90_decision_rate_author_history_cohort"] = (_ratio(dr90_decided, len(dr90_kept)), "derived")
        out["dr90_decision_pr_count_author_history_cohort"] = (len(dr90_kept), "derived")

        mr_kept = _cap_prs([pr for pr in mr_candidates if _eligible(pr, records) is True])
        mr_decided = [pr for pr in mr_kept if pr.get("closedAt") is not None]
        mr_merged = sum(1 for pr in mr_decided if landing.merged_or_landed_at(pr) is not None)
        out["mr_first_rate"] = (_ratio(mr_merged, len(mr_decided)), "derived")
        out["mr_first_pr_count"] = (len(mr_decided), "derived")
        out["mr_first_landed_pr_count"] = (sum(1 for pr in mr_decided if landing.is_landed(pr)), "derived")

    # --- 5.2 / 5.3: the sampled newcomer cohort -----------------------------
    authors = _newcomer_cohort_authors(window_prs, records, sampled, anchor)
    out["newcomer_cohort_author_count"] = (len(authors), "derived")
    out["cohort_newcomer_authors"] = ([_author_public(a) for a in authors], "derived")

    out["newcomer_conversion_raw"] = (
        _ratio(sum(1 for a in authors if a["_converted"]), len(authors)), "derived")
    out["newcomer_conversion_raw_author_count"] = (len(authors), "derived")

    # Collector failure / cut window gate over the sampled newcomer roster.
    newcomer_total = len(authors)
    newcomer_unmeasured = sum(1 for a in authors if a["_eligible"] is None)
    newcomer_fraction = (newcomer_unmeasured / newcomer_total) if newcomer_total else 0.0
    newcomer_collector_failed = newcomer_fraction > AUTHOR_UNMEASURED_MAX_FRACTION or guard_failed
    newcomer_window_cut = gates.record_cut_short(ah, NEWCOMER_BAND_START_DAYS)

    eligible_authors = [a for a in authors if a["_eligible"] is True]
    if newcomer_collector_failed or newcomer_window_cut:
        if newcomer_window_cut:
            detail = "author_history window truncated before reaching 365d newcomer band start"
        elif guard_failed:
            detail = guard_detail
        else:
            detail = f"{newcomer_unmeasured} of {newcomer_total} newcomer authors unmeasured"
        out["newcomer_conversion"] = (None, "missing")
        out["newcomer_conversion_author_count"] = (None, "missing")
        out["newcomer_conversion_landed_author_count"] = (None, "missing")
        out["rr90_return_rate"] = (None, "missing")
        out["rr90_return_author_count"] = (None, "missing")
        out["rr90_return_landed_author_count"] = (None, "missing")
        out["newcomer_unmeasured_detail"] = (detail, "verified")
    else:
        conv_kept = _cap_authors(eligible_authors, "_first_created")
        out["newcomer_conversion"] = (
            _ratio(sum(1 for a in conv_kept if a["_converted"]), len(conv_kept)), "derived")
        out["newcomer_conversion_author_count"] = (len(conv_kept), "derived")
        out["newcomer_conversion_landed_author_count"] = (
            sum(1 for a in conv_kept if a["_converted"] and a["_first_landed"]), "derived")

        merged_first_eligible = [a for a in eligible_authors if a["_first_merged"] is not None]
        rr_kept = _cap_authors(merged_first_eligible, "_first_merged")
        out["rr90_return_rate"] = (
            _ratio(sum(1 for a in rr_kept if a["_returned"]), len(rr_kept)), "derived")
        out["rr90_return_author_count"] = (len(rr_kept), "derived")
        out["rr90_return_landed_author_count"] = (sum(1 for a in rr_kept if a["_first_landed"]), "derived")

    merged_first_all = [a for a in authors if a["_first_merged"] is not None]
    out["rr90_return_rate_raw"] = (
        _ratio(sum(1 for a in merged_first_all if a["_returned"]), len(merged_first_all)), "derived")
    out["rr90_return_raw_author_count"] = (len(merged_first_all), "derived")
    return out
