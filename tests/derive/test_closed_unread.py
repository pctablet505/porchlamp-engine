"""The ``closed_unread`` flag reads the recorded PR window.

The detector (03 section 6) flags a project when at least 0.30 of its outside
pull requests were closed unmerged with no substantive response, over at least
``CLOSED_UNREAD_MIN_N`` of them. ``scripts/derive.py`` records both counts from
the ``pr_window`` envelope -- the PRs opened in the 180 days before the fetch
(D-IN-4) -- with the D-IN-2 response rule GR_14 and D-GATE-5 use: a maintainer
review in any state or a comment of at least 40 characters, never the author,
never a bot. ``porchlamp.derive.bundle`` carries the counts on the bundle and
through the snapshot's dump and reload; ``porchlamp.verify.pipeline_hook`` hands
their share to the detector. The bundle's v1 ``pr_events`` (the 30 newest PRs of
any age, where only a review answered a PR) decide nothing.

Every expected count below is by hand from the fixture.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from _fixtures import KEY, load, write_envelope

from porchlamp.collect.github_graphql import derive_pr_window
from porchlamp.core import constants
from porchlamp.core.gates import GateEvidence
from porchlamp.core.pipeline import repo_key, score_snapshot
from porchlamp.snapshot.populations import PopulationSet
from porchlamp.verify.pipeline_hook import build_detectors, detector_inputs_from_bundle

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("derive_closed_unread", ROOT / "scripts" / "derive.py")
derive = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(derive)

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)
OWNER, NAME = "acme", "widget"
FETCHED_AT = "2026-09-11T00:00:00+00:00"
REPLY = "Thanks for this; the next release already carries the same fix."

_EVIDENCE = GateEvidence(
    manifest_found=True, is_curated_anchor=False, license_file_present=None,
    fork_parent_in_universe=None, vitality_commits_180d=5, off_platform_marker=None,
)
_NO_POPULATIONS = PopulationSet(
    snapshot_id="pop", benchmark_version=constants.BENCHMARK_VERSION,
    constants_hash=constants.CONSTANTS_HASH, populations=(),
)


def _at(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def _window_pr(number: int, *, opened: float, closed: float | None = None, replied: float | None = None) -> dict[str, Any]:
    """A recorded outside PR node opened ``opened`` days before NOW, closed
    unmerged ``closed`` days before it (open when ``None``), answered by a
    maintainer's comment ``replied`` days before it (unanswered when ``None``)."""
    comments = [] if replied is None else [{
        "author": {"__typename": "User", "login": "maint"}, "authorAssociation": "MEMBER",
        "bodyText": REPLY, "createdAt": _at(replied),
    }]
    return {
        "number": number, "createdAt": _at(opened), "closedAt": None if closed is None else _at(closed),
        "mergedAt": None, "state": "OPEN" if closed is None else "CLOSED",
        "author": {"__typename": "User", "login": f"contributor{number}"}, "authorAssociation": "NONE",
        "reviews": {"nodes": []}, "comments": {"nodes": comments}, "reviewThreads": {"totalCount": 0},
    }


def _sample_event(pr: dict[str, Any]) -> dict[str, Any]:
    """The v1 ``pr_events`` record of the same PR (``scripts/gather.py``'s sample)."""
    return {"number": pr["number"], "created_at": pr["createdAt"], "closed_at": pr["closedAt"],
            "merged_at": None, "author_login": pr["author"]["login"], "author_association": "NONE",
            "comments_total": len(pr["comments"]["nodes"]), "state": "open" if pr["closedAt"] is None else "closed"}


def _derive(tmp_path: Path, *, sample: list[dict[str, Any]], window: list[dict[str, Any]] | None):
    """Derive the repository from a v1 sample (no PR in it reviewed) and,
    unless ``window`` is ``None``, a recorded PR window; return the derived
    document and the bundle loaded from it."""
    raw = tmp_path / "raw"
    write_envelope(raw, "pr_events", KEY, [_sample_event(pr) for pr in sample], FETCHED_AT)
    write_envelope(raw, "pr_reviews", KEY, {str(pr["number"]): None for pr in sample}, FETCHED_AT)
    if window is not None:
        write_envelope(raw, "pr_window", KEY, {
            "window_anchor": FETCHED_AT, "window_days": 180, "cap": 500, "count": len(window),
            "truncated": False, "cost_points": 1, "prs": window, "derived": derive_pr_window(window),
        }, FETCHED_AT)
    doc = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path, fetch=False, now=NOW)
    return doc, load(tmp_path, json.loads(json.dumps(doc)))


def _flags(bundle) -> list[str]:
    """The suspicion flags the real scoring pipeline gives the bundle."""
    snapshot = score_snapshot(
        [bundle], _NO_POPULATIONS, snapshot_id="snap", as_of=NOW,
        gate_evidence={repo_key(bundle): _EVIDENCE}, raw_hashes={repo_key(bundle): "e" * 64},
        previous=None, detectors=build_detectors([bundle]),
    )
    return list(snapshot.records[0].suspicion)


def _answered_by_comments() -> list[dict[str, Any]]:
    """Ten outside PRs closed unmerged: eight a maintainer answered with a
    comment and no review, two nobody answered."""
    return [_window_pr(n, opened=40, closed=30, replied=35 if n <= 8 else None) for n in range(1, 11)]


def test_a_comment_answer_is_read_where_the_sample_saw_no_review(tmp_path: Path) -> None:
    """The sample's review-only rule reads all ten as unread (share 1.0); the
    window's D-IN-2 rule reads two of ten (0.2 < 0.30): no flag."""
    window = _answered_by_comments()
    doc, bundle = _derive(tmp_path, sample=window, window=window)
    assert "closed_unread" not in _flags(bundle)
    inputs = detector_inputs_from_bundle(bundle)
    assert (inputs.closed_unread_share, inputs.closed_unread_n) == (0.2, 10)
    assert (doc["meta"]["pr_window_closed_unread_count"], doc["meta"]["pr_window_outside_pr_count"]) == (2, 10)


def test_the_window_counts_prs_older_than_the_newest_thirty(tmp_path: Path) -> None:
    """45 outside PRs in the window: the 30 newest are open, the 15 older were
    closed unmerged unanswered. The 30-PR sample holds none of those (share
    0); the window holds all of them (15 of 45, 0.33 >= 0.30): flagged."""
    window = ([_window_pr(n, opened=n) for n in range(1, 31)]
              + [_window_pr(n, opened=n, closed=n - 10) for n in range(31, 46)])
    _, bundle = _derive(tmp_path, sample=window[:30], window=window)
    assert "closed_unread" in _flags(bundle)
    inputs = detector_inputs_from_bundle(bundle)
    assert (inputs.closed_unread_share, inputs.closed_unread_n) == (15 / 45, 45)


def test_without_a_recorded_window_the_flag_is_unmeasured(tmp_path: Path) -> None:
    """Ten closed-unread PRs in the sample alone: no window, no measurement,
    no flag."""
    _, bundle = _derive(tmp_path, sample=[_window_pr(n, opened=40, closed=30) for n in range(1, 11)], window=None)
    assert "closed_unread" not in _flags(bundle)
    inputs = detector_inputs_from_bundle(bundle)
    assert (inputs.closed_unread_share, inputs.closed_unread_n) == (None, None)


def test_a_bundle_derived_before_the_counts_existed_is_unmeasured(tmp_path: Path) -> None:
    """A derived document with a window but without the two counts (derived
    by an earlier tree) measures nothing, whatever its sample says."""
    window = [_window_pr(n, opened=40, closed=30) for n in range(1, 11)]
    raw_doc, _ = _derive(tmp_path / "derive", sample=window, window=window)
    for section in ("meta", "provenance", "confidence"):
        raw_doc[section].pop("pr_window_closed_unread_count", None)
        raw_doc[section].pop("pr_window_outside_pr_count", None)
    bundle = load(tmp_path / "derive", raw_doc)
    assert "closed_unread" not in _flags(bundle)
    assert detector_inputs_from_bundle(bundle).closed_unread_share is None


def test_the_counts_need_their_recorded_window(tmp_path: Path) -> None:
    """Counts whose ``pr_window`` envelope no longer resolves (a data dir
    copied without it) measure nothing, as GR_14 reads the same envelope as
    a collector failure."""
    window = [_window_pr(n, opened=40, closed=30) for n in range(1, 11)]
    doc, _ = _derive(tmp_path, sample=window, window=window)
    (tmp_path / "raw" / "pr_window" / f"{KEY}.json").unlink()
    bundle = load(tmp_path, doc)
    assert (doc["meta"]["pr_window_closed_unread_count"], doc["meta"]["pr_window_outside_pr_count"]) == (10, 10)
    assert "closed_unread" not in _flags(bundle)
    assert detector_inputs_from_bundle(bundle).closed_unread_n is None


def test_the_counts_fold_their_envelope_into_the_provenance(tmp_path: Path) -> None:
    """The flag's source envelope is part of what the bundle's
    ``provenance_hash`` covers, whichever other rows read it."""
    from _fixtures import expected_hash

    from porchlamp.derive.bundle import ClosedUnreadCounts, _resolve_closed_unread, _V1Ctx

    window = [_window_pr(n, opened=40, closed=30) for n in range(1, 11)]
    doc, _ = _derive(tmp_path, sample=window, window=window)
    ctx = _V1Ctx(meta=doc["meta"], provenance=doc["provenance"], confidence=doc["confidence"],
                 raw_root=tmp_path / "raw", problems=[], referenced={})
    assert _resolve_closed_unread(ctx) == ClosedUnreadCounts(closed_unread=10, population_n=10)
    envelope = json.loads((tmp_path / "raw" / "pr_window" / f"{KEY}.json").read_text(encoding="utf-8"))
    assert ctx.referenced == {f"raw/pr_window/{KEY}.json": expected_hash(envelope["data"])}


def test_the_counts_survive_the_snapshot_dump_and_reload(tmp_path: Path) -> None:
    """The snapshot directory stores dumped bundles and the store scores the
    reloaded ones: the counts, and the flag they give, cross that round trip."""
    from porchlamp.derive.bundle import ClosedUnreadCounts, closed_unread_counts, dump_bundle, validate_bundle

    window = [_window_pr(n, opened=40, closed=30, replied=35 if n > 6 else None) for n in range(1, 11)]
    _, bundle = _derive(tmp_path, sample=window, window=window)
    reloaded = validate_bundle(json.loads(json.dumps(dump_bundle(bundle))))
    assert closed_unread_counts(bundle) == closed_unread_counts(reloaded) == ClosedUnreadCounts(
        closed_unread=6, population_n=10)
    assert _flags(reloaded) == _flags(bundle) == ["closed_unread"]


def test_a_bundle_without_the_counts_dumps_as_before(tmp_path: Path) -> None:
    """A bundle with nothing measured writes no new key, so snapshot files of
    bundles derived without a window are byte-identical to their old form."""
    from porchlamp.derive.bundle import closed_unread_counts, dump_bundle, validate_bundle

    _, bundle = _derive(tmp_path, sample=[], window=None)
    document = dump_bundle(bundle)
    assert "closed_unread" not in document
    assert closed_unread_counts(validate_bundle(json.loads(json.dumps(document)))) is None


def test_malformed_stored_counts_measure_nothing(tmp_path: Path) -> None:
    """A snapshot bundle file whose stored counts are not two counts with the
    closed-unread PRs inside the population reads as nothing measured."""
    from porchlamp.derive.bundle import closed_unread_counts, dump_bundle, validate_bundle

    _, bundle = _derive(tmp_path, sample=[], window=None)
    for stored in ({"closed_unread": 11, "population_n": 10}, {"closed_unread": "3", "population_n": 10},
                   {"closed_unread": -1, "population_n": 10}, {"closed_unread": 3}, [3, 10]):
        document = {**dump_bundle(bundle), "closed_unread": stored}
        assert closed_unread_counts(validate_bundle(document)) is None, stored
