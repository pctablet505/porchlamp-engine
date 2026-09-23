"""Malformed-event tolerance for a ghost/deleted-author ``pr_events``
entry, plus the ``dropped_event_count`` accessor it feeds.

GitHub reports a deleted or suspended account as an empty ``author_login``.
``PRWindowEvent.author_login`` must be a non-empty string (04 section 3: it
is an author-keyed record), so such an event cannot become one. When encountered,
the event is dropped from author-keyed metrics but the bundle still loads,
and its timestamps still count toward ``pr_window.start``/``end`` (window
coverage is author-irrelevant).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from _fixtures import base_document, load

from porchlamp.core.models import BundleValidationError, PRWindowEvent
from porchlamp.derive.bundle import dropped_event_count

GHOST_EVENT = {
    "pr_number": 99,
    "created_at": "2020-01-01T00:00:00+00:00",  # outside the normal event's range on both ends
    "closed_at": "2020-01-02T00:00:00+00:00",
    "merged_at": None,
    "author_login": "",  # GitHub's spelling for a deleted/suspended account
    "author_association": "NONE",
    "is_external": True,
    "first_human_review_at": None,
}
NORMAL_EVENT = {
    "pr_number": 1,
    "created_at": "2026-06-01T00:00:00+00:00",
    "closed_at": "2026-06-02T00:00:00+00:00",
    "merged_at": "2026-06-02T00:00:00+00:00",
    "author_login": "alice",
    "author_association": "NONE",
    "is_external": True,
    "first_human_review_at": None,
}


def test_ghost_author_event_is_dropped_not_rejected(tmp_path: Path) -> None:
    doc = base_document(pr_events=[NORMAL_EVENT, GHOST_EVENT])
    bundle = load(tmp_path, doc)  # must not raise

    # Only the normal event survives as a PRWindowEvent.
    assert len(bundle.pr_window.events) == 1
    assert bundle.pr_window.events[0] == PRWindowEvent(
        pr_number=1, created_at="2026-06-01T00:00:00+00:00", closed_at="2026-06-02T00:00:00+00:00",
        merged_at="2026-06-02T00:00:00+00:00", author_login="alice", author_association="NONE",
        is_external=True, first_human_review_at=None,
    )
    assert dropped_event_count(bundle) == 1

    # The ghost event's timestamps (2020, outside the normal event's 2026
    # range) still widen the window: coverage is author-irrelevant.
    assert bundle.pr_window.start == "2020-01-01T00:00:00+00:00"
    assert bundle.pr_window.end == "2026-06-02T00:00:00+00:00"


def test_dropped_event_count_defaults_to_zero(tmp_path: Path) -> None:
    doc = base_document(pr_events=[NORMAL_EVENT])
    bundle = load(tmp_path, doc)
    assert dropped_event_count(bundle) == 0


def test_dropped_event_count_accessor_default_for_a_bundle_with_no_attribute() -> None:
    """A FeatureBundle that never went through ``_load_v1`` (e.g. one built
    from a native v2 document via ``validate_bundle``) never gets the
    backing attribute set at all; the accessor's ``getattr`` default
    covers that case too, not just the "zero dropped" case above."""
    from porchlamp.core.models import Cohorts, FeatureBundle, PRWindow, RepoIdentity, feature_provenance_hash

    bundle = FeatureBundle(
        schema_version=2,
        repo=RepoIdentity(owner="acme", name="widget", ecosystem="pypi", archived=False,
                          mirror_url=None, fork=False, license_spdx=None, default_branch=None),
        inputs={},
        pr_window=PRWindow(start=None, end=None, truncated=False, events=()),
        cohorts=Cohorts(decision_pr_numbers=(), newcomer_authors=()),
        provenance_hash=feature_provenance_hash({}),
    )
    assert dropped_event_count(bundle) == 0


def test_other_malformed_event_shapes_still_reject_the_whole_bundle(tmp_path: Path) -> None:
    """Only the empty-``author_login`` shape is tolerated. An event missing
    a genuinely required key (``pr_number``) still rejects the whole bundle."""
    broken_event = dict(NORMAL_EVENT)
    del broken_event["pr_number"]
    doc = base_document(pr_events=[broken_event])
    with pytest.raises(BundleValidationError, match="pr_events\\[0\\]"):
        load(tmp_path, doc)
