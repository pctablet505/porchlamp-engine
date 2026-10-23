"""Pull requests a project lands outside GitHub's merge (02 section 4, RFC-0015, #60).

Some projects land a pull request by committing its change themselves and then
closing it, so GitHub records it closed and never merged (``mergedAt`` null):
``pytorch/pytorch``'s merge bot and Meta's export bot push a commit that closes
the pull request and label it ``Merged``, ``openjdk/jdk``'s bot labels it
``integrated``, a maintainer commits the patch with "Closes #N"
(``curl/curl``, ``notepad-plus-plus/notepad-plus-plus``). A measure that read
``mergedAt`` alone counted every one of them as a rejection. The merge-based
measures -- 1.3 MR_first and its no-prior-merge predicate, 1.5 TTM_p90, 5.2
newcomer conversion, 5.3 RR_90 and the 4.1 review proxy -- read
:func:`merged_or_landed_at`, this one definition. The decision measures (1.2
DR_90, 1.4 GR_14 and D-GATE-5) are unchanged: a close was always a decision.

A recorded pull-request node is landed (:func:`is_landed`) when it is closed and
not merged, and either

* its latest close event (a ``ClosedEvent`` in ``timelineItems``) names a commit
  as its ``closer``. GitHub closes a pull request for a commit only when the
  commit reaches the default branch and its message says it closes that pull
  request, which takes write access to the repository; or
* it carries a label in :data:`porchlamp.core.constants.LANDING_LABELS`, its whole
  name compared lower-cased: the project's own mark of a landing, which only an
  account with triage access can set.

It was landed at its ``closedAt``, the time GitHub recorded the close: a closing
commit's own date is the committer's to set.

A node recorded without ``labels`` and ``timelineItems`` (an envelope from a query
that did not ask for them) shows no landing whatever happened: it counts as
merged only when GitHub merged it, as before, and :func:`landing_recorded` tells
such a record apart. A comment announcing a landing ("Landed in <sha>", "Applied,
thanks") is not read: the author-history queries record no comments, and the
words are as common in other replies (RFC-0015 gives the measured cases).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from porchlamp.core import constants

#: The GraphQL selection, on a ``PullRequest``, that records what :func:`is_landed`
#: reads: its first 10 labels and its latest close event with the event's closer,
#: the fields ``porchlamp.collect.github_graphql.PR_WINDOW_QUERY`` records since #63.
#: The author-history queries (``AUTHOR_WINDOW_QUERY`` in
#: ``porchlamp.collect.github_graphql`` and ``LIGHT_PR_HISTORY_QUERY`` in
#: ``porchlamp.collect.ledger``) carry it.
PR_LANDING_FIELDS: str = (
    " labels(first: 10) { nodes { name } }"
    " timelineItems(itemTypes: [CLOSED_EVENT], last: 1) { nodes { __typename"
    " ... on ClosedEvent { createdAt actor { __typename login }"
    " closer { __typename ... on Commit { oid } ... on PullRequest { number } } } } }"
)


def _parse_iso(value: object) -> datetime | None:
    """An ISO-8601 timestamp as an aware datetime; ``None`` when absent or unparseable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _latest_close_event(pr: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The latest ``ClosedEvent`` among a node's recorded ``timelineItems``, by its
    ``createdAt`` (an event whose time does not parse is kept only while no other
    is found, the order ``porchlamp.collect.github_graphql.derive_pr_window`` reads
    close events in); ``None`` when none is recorded."""
    timeline = pr.get("timelineItems")
    events = timeline.get("nodes") if isinstance(timeline, Mapping) else None
    latest: Mapping[str, Any] | None = None
    latest_at: datetime | None = None
    for event in events if isinstance(events, list) else ():
        if not isinstance(event, Mapping) or event.get("__typename") != "ClosedEvent":
            continue
        at = _parse_iso(event.get("createdAt"))
        if latest is None or (at is not None and (latest_at is None or at >= latest_at)):
            latest, latest_at = event, at
    return latest


def is_landed(pr: Mapping[str, Any]) -> bool:
    """RFC-0015: whether a recorded pull-request node was landed outside GitHub's merge.

    True when the node is closed (its ``closedAt`` parses) and not merged (no
    ``mergedAt``), and its latest close event names a commit as its ``closer`` or
    it carries a label in :data:`~porchlamp.core.constants.LANDING_LABELS`
    (compared lower-cased). False for every other node: open, merged, closed with
    neither signal, or recorded without the landing fields (:func:`landing_recorded`).
    """
    if pr.get("mergedAt") or _parse_iso(pr.get("closedAt")) is None:
        return False
    event = _latest_close_event(pr)
    closer = event.get("closer") if event is not None else None
    if isinstance(closer, Mapping) and closer.get("__typename") == "Commit":
        return True
    labels = pr.get("labels")
    names = labels.get("nodes") if isinstance(labels, Mapping) else None
    return any(
        isinstance(label, Mapping)
        and isinstance(label.get("name"), str)
        and label["name"].lower() in constants.LANDING_LABELS
        for label in (names if isinstance(names, list) else ())
    )


def merged_or_landed_at(pr: Mapping[str, Any]) -> str | None:
    """RFC-0015: when a recorded pull-request node was merged, by GitHub or outside
    its merge -- its ``mergedAt`` when that parses, else its ``closedAt`` when the
    node :func:`is_landed`; ``None`` when it was neither (still open, or closed
    without being merged or landed)."""
    merged = pr.get("mergedAt")
    if _parse_iso(merged) is not None:
        return merged
    if is_landed(pr):
        return pr.get("closedAt")
    return None


def landing_recorded(pr: Mapping[str, Any]) -> bool:
    """Whether a recorded pull-request node carries the fields :func:`is_landed`
    reads (``labels`` and ``timelineItems``). A node without them (an envelope from
    a query that did not ask for them) shows no landing whatever happened, so a
    measure over such nodes reads GitHub's merges alone, as it did before RFC-0015."""
    return isinstance(pr.get("labels"), Mapping) and isinstance(pr.get("timelineItems"), Mapping)
