"""Tests for ``porchlamp.core.landing``: pull requests landed outside GitHub's merge (RFC-0015, #60).

Recorded-node oracles: each test builds the PR node shape the collector's
queries record (``labels`` and ``timelineItems`` as ``PR_WINDOW_QUERY`` and the
author-history queries ask for them) and asserts the exact landing verdict and
merge time.
"""
from __future__ import annotations

from typing import Any

from porchlamp.core import constants, landing

CREATED = "2026-08-01T00:00:00Z"
CLOSED = "2026-08-03T12:00:00Z"


def node(*, merged: str | None = None, closed: str | None = CLOSED, labels: tuple[str, ...] = (),
         closes: tuple[tuple[str, str | None], ...] = ()) -> dict[str, Any]:
    """One recorded PR node with the landing fields: ``closes`` lists close
    events as ``(createdAt, closer typename or None)``."""
    return {
        "number": 7, "createdAt": CREATED, "closedAt": closed, "mergedAt": merged,
        "state": "MERGED" if merged else ("CLOSED" if closed else "OPEN"),
        "labels": {"nodes": [{"name": name} for name in labels]},
        "timelineItems": {"nodes": [
            {"__typename": "ClosedEvent", "createdAt": at, "actor": {"__typename": "User", "login": "bob"},
             "closer": None if typename is None else (
                 {"__typename": "Commit", "oid": "abc123"} if typename == "Commit"
                 else {"__typename": typename, "number": 9})}
            for at, typename in closes]},
    }


def test_a_close_that_names_a_commit_lands_the_pr_at_its_close() -> None:
    """GitHub closes a PR for a commit only when the commit reaches the default branch and says it
    closes the PR: such a PR was landed, at its ``closedAt``."""
    pr = node(closes=((CLOSED, "Commit"),))
    assert landing.is_landed(pr) is True
    assert landing.merged_or_landed_at(pr) == CLOSED


def test_a_landing_label_lands_the_pr_in_any_letter_case_and_only_as_a_whole_name() -> None:
    """``Merged`` (pytorch, Meta), ``merged`` (freebsd) and ``integrated`` (openjdk) land a closed PR
    with no closing commit; a label that only contains the word does not."""
    assert constants.LANDING_LABELS == frozenset({"integrated", "merged"})
    for name in ("Merged", "merged", "INTEGRATED", "integrated"):
        pr = node(labels=("bug", name), closes=((CLOSED, None),))
        assert landing.is_landed(pr) is True, name
        assert landing.merged_or_landed_at(pr) == CLOSED
    for name in ("not merged", "merged-upstream", "integration", "to be merged"):
        assert landing.is_landed(node(labels=(name,), closes=((CLOSED, None),))) is False, name


def test_a_close_with_neither_signal_is_a_rejection_not_a_landing() -> None:
    """A maintainer's plain close, a close by another PR and a close with no recorded event are not
    landings: the PR counts as decided and unmerged, as before."""
    for pr in (node(closes=((CLOSED, None),)), node(closes=((CLOSED, "PullRequest"),)), node()):
        assert landing.is_landed(pr) is False
        assert landing.merged_or_landed_at(pr) is None


def test_an_open_or_merged_pr_is_never_landed() -> None:
    """A landing label on an open PR lands nothing (it has no close); a PR GitHub merged is merged
    at its ``mergedAt``, whatever closed it."""
    open_pr = node(closed=None, labels=("Merged",))
    assert landing.is_landed(open_pr) is False
    assert landing.merged_or_landed_at(open_pr) is None
    merged = node(merged="2026-08-02T00:00:00Z", closed="2026-08-02T00:00:00Z", labels=("Merged",),
                  closes=(("2026-08-02T00:00:00Z", "Commit"),))
    assert landing.is_landed(merged) is False
    assert landing.merged_or_landed_at(merged) == "2026-08-02T00:00:00Z"


def test_the_latest_close_decides_whatever_order_the_events_are_recorded_in() -> None:
    """Closed by a commit, reopened, then closed by hand: the latest close names no commit, so the
    PR is not landed; the same events recorded in the other order give the same answer, and a
    commit on the latest close lands it."""
    early, late = "2026-08-02T00:00:00Z", CLOSED
    assert landing.is_landed(node(closes=((early, "Commit"), (late, None)))) is False
    assert landing.is_landed(node(closes=((late, None), (early, "Commit")))) is False
    assert landing.is_landed(node(closes=((early, None), (late, "Commit")))) is True
    assert landing.is_landed(node(closes=((late, "Commit"), (early, None)))) is True


def test_a_node_recorded_without_the_landing_fields_shows_no_landing() -> None:
    """An envelope from a query that did not ask for ``labels`` and the close event's closer: the
    node is merged only when GitHub merged it, as before RFC-0015, and says so."""
    legacy_closed = {"number": 7, "createdAt": CREATED, "closedAt": CLOSED, "mergedAt": None, "state": "CLOSED"}
    assert landing.landing_recorded(legacy_closed) is False
    assert landing.is_landed(legacy_closed) is False
    assert landing.merged_or_landed_at(legacy_closed) is None
    legacy_merged = dict(legacy_closed, mergedAt=CLOSED, state="MERGED")
    assert landing.merged_or_landed_at(legacy_merged) == CLOSED
    assert landing.landing_recorded(node()) is True
    assert landing.landing_recorded(dict(node(), labels=None)) is False


def test_an_unparseable_time_is_no_merge_time() -> None:
    """A ``mergedAt`` that does not parse is no merge time (and no landing: GitHub said merged); a
    landed node whose ``closedAt`` does not parse is not landed."""
    assert landing.merged_or_landed_at(node(merged="yesterday", closes=((CLOSED, "Commit"),))) is None
    assert landing.is_landed(node(closed="soon", closes=((CLOSED, "Commit"),))) is False


def test_the_query_selection_records_what_landing_reads() -> None:
    """``PR_LANDING_FIELDS`` asks for the first 10 labels and the latest close event with its closer,
    the selection ``PR_WINDOW_QUERY`` records since #63; its braces balance."""
    fields = landing.PR_LANDING_FIELDS
    assert " labels(first: 10) { nodes { name } }" in fields
    assert " timelineItems(itemTypes: [CLOSED_EVENT], last: 1) { nodes { __typename" in fields
    assert " closer { __typename ... on Commit { oid } ... on PullRequest { number } }" in fields
    assert fields.count("{") == fields.count("}")
