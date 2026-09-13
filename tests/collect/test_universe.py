"""Tests for W1-10: ``cvi.collect.universe`` (D-IN-5 universe-entry
star-velocity filter).

Every fetch is a fake ``Transport`` callable -- no test in this file makes
a real network call (``block_live_network`` proves it on the tests that
exercise :func:`collect_star_history`/:func:`star_velocity_evidence`, the
two functions that would otherwise reach the network).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from cvi.collect.envelope import read_envelope
from cvi.collect.universe import (
    STAR_HISTORY_COLLECTOR,
    UNIVERSE_ENTRY_COLLECTOR,
    StarVelocityRecord,
    collect_star_history,
    default_star_history_transport,
    parse_stargazer_events,
    star_velocity_evidence,
)
from cvi.models import StargazerEvent

NOW = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)
FETCHED_AT = NOW.isoformat()


def _clean_fetch(owner: str, name: str) -> dict:
    """100 real stars, no burst -- a normal repo."""
    return {
        "events": [
            {"user_login": f"user_{i}", "starred_at": FETCHED_AT, "user_total_platform_events": 100}
            for i in range(100)
        ],
        "repo_forks_delta_90d": 20,
    }


def _burst_fetch(owner: str, name: str) -> dict:
    """534 recent stars (> 500), 334 valid, forks=1 => ratio 334 > 100 =>
    lockstep penalty fires. A textbook lockstep star-burst fixture."""
    events = [
        {"user_login": f"bot_{i}", "starred_at": FETCHED_AT, "user_total_platform_events": 1}
        for i in range(200)
    ] + [
        {"user_login": f"real_{i}", "starred_at": FETCHED_AT, "user_total_platform_events": 100}
        for i in range(334)
    ]
    return {"events": events, "repo_forks_delta_90d": 1}


def _failing_fetch(owner: str, name: str) -> dict:
    raise RuntimeError(f"transport unavailable for {owner}/{name}")


def _malformed_fetch(owner: str, name: str) -> dict:
    return {"events": "not-a-list", "repo_forks_delta_90d": 1}


# --------------------------------------------------------------------------- #
# parse_stargazer_events                                                      #
# --------------------------------------------------------------------------- #


def test_parse_stargazer_events_basic():
    raw = [{"user_login": "alice", "starred_at": "2026-09-01T00:00:00Z", "user_total_platform_events": 12}]
    events = parse_stargazer_events(raw)
    assert events == [
        StargazerEvent(user_login="alice",
                        starred_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                        user_total_platform_events=12)
    ]


def test_parse_stargazer_events_naive_timestamp_assumed_utc():
    raw = [{"user_login": "alice", "starred_at": "2026-09-01T00:00:00", "user_total_platform_events": 12}]
    events = parse_stargazer_events(raw)
    assert events[0].starred_at == datetime(2026, 9, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("row", [
    {"user_login": None, "starred_at": "2026-09-01T00:00:00Z", "user_total_platform_events": 1},
    {"user_login": "", "starred_at": "2026-09-01T00:00:00Z", "user_total_platform_events": 1},
    {"user_login": "alice", "starred_at": "not-a-date", "user_total_platform_events": 1},
    {"user_login": "alice", "starred_at": None, "user_total_platform_events": 1},
    {"user_login": "alice", "starred_at": "2026-09-01T00:00:00Z", "user_total_platform_events": "1"},
    {"user_login": "alice", "starred_at": "2026-09-01T00:00:00Z", "user_total_platform_events": True},
    {"user_login": "alice", "starred_at": "2026-09-01T00:00:00Z"},
    "not-a-mapping",
])
def test_parse_stargazer_events_skips_malformed_rows(row):
    assert parse_stargazer_events([row]) == []


def test_parse_stargazer_events_skips_only_the_bad_row():
    good = {"user_login": "alice", "starred_at": "2026-09-01T00:00:00Z", "user_total_platform_events": 5}
    bad = {"user_login": None, "starred_at": "2026-09-01T00:00:00Z", "user_total_platform_events": 5}
    assert parse_stargazer_events([bad, good]) == [
        StargazerEvent(user_login="alice", starred_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                        user_total_platform_events=5)
    ]


# --------------------------------------------------------------------------- #
# default_star_history_transport: no live implementation yet (W1-10)         #
# --------------------------------------------------------------------------- #


def test_default_star_history_transport_raises_not_implemented(block_live_network):
    """No live implementation yet: raises rather than fabricating a
    per-account throwaway audit -- never touches the network at all."""
    with pytest.raises(NotImplementedError):
        default_star_history_transport("acme", "widget")


# --------------------------------------------------------------------------- #
# collect_star_history                                                        #
# --------------------------------------------------------------------------- #


def test_collect_star_history_ok_writes_envelope(tmp_path: Path, block_live_network):
    status = collect_star_history(tmp_path, "acme", "widget", _clean_fetch, FETCHED_AT)
    assert status == "ok"
    path = tmp_path / STAR_HISTORY_COLLECTOR / "acme__widget.json"
    assert path.is_file()
    envelope = read_envelope(path)
    assert len(envelope.data["events"]) == 100
    assert envelope.data["repo_forks_delta_90d"] == 20
    assert envelope.fetched_at == FETCHED_AT


def test_collect_star_history_checkpoint_skips_refetch(tmp_path: Path, block_live_network):
    calls = []

    def counting_fetch(owner, name):
        calls.append((owner, name))
        return _clean_fetch(owner, name)

    assert collect_star_history(tmp_path, "acme", "widget", counting_fetch, FETCHED_AT) == "ok"
    assert collect_star_history(tmp_path, "acme", "widget", counting_fetch, FETCHED_AT) == "ok"
    assert len(calls) == 1  # second call reused the envelope on disk


def test_collect_star_history_force_refetches(tmp_path: Path, block_live_network):
    calls = []

    def counting_fetch(owner, name):
        calls.append((owner, name))
        return _clean_fetch(owner, name)

    collect_star_history(tmp_path, "acme", "widget", counting_fetch, FETCHED_AT)
    collect_star_history(tmp_path, "acme", "widget", counting_fetch, FETCHED_AT, force=True)
    assert len(calls) == 2


def test_collect_star_history_fail_on_transport_exception(tmp_path: Path, block_live_network):
    status = collect_star_history(tmp_path, "acme", "widget", _failing_fetch, FETCHED_AT)
    assert status == "fail"
    assert not (tmp_path / STAR_HISTORY_COLLECTOR / "acme__widget.json").is_file()


def test_collect_star_history_fail_on_malformed_payload(tmp_path: Path, block_live_network):
    status = collect_star_history(tmp_path, "acme", "widget", _malformed_fetch, FETCHED_AT)
    assert status == "fail"


def test_collect_star_history_fail_on_non_mapping_payload(tmp_path: Path, block_live_network):
    status = collect_star_history(tmp_path, "acme", "widget", lambda o, n: "nope", FETCHED_AT)
    assert status == "fail"


# --------------------------------------------------------------------------- #
# star_velocity_evidence -- the acceptance-level behaviours                   #
# --------------------------------------------------------------------------- #


def test_flagged_fixture_repo_lockstep_burst_is_flagged(tmp_path: Path, block_live_network):
    """A fixture repo with lockstep star bursts is flagged at entry."""
    record = star_velocity_evidence(tmp_path, "acme", "burst-repo", NOW, _burst_fetch, FETCHED_AT)
    assert record.measured is True
    assert record.star_velocity_flag is True
    assert record.stars_audited == 100  # int(334 * 0.30) == 100
    assert record.star_velocity == {"recent_stars": 534, "window_days": 90}
    assert record.provenance["path"] == str(tmp_path / STAR_HISTORY_COLLECTOR / "acme__burst-repo.json")
    envelope = read_envelope(Path(record.provenance["path"]))
    assert record.provenance["sha256"] == envelope.sha256
    assert record.provenance["fetched_at"] == FETCHED_AT


def test_normal_repo_carries_false_and_its_count(tmp_path: Path, block_live_network):
    """A normal repo carries False and its (unaudited-down) count."""
    record = star_velocity_evidence(tmp_path, "acme", "normal-repo", NOW, _clean_fetch, FETCHED_AT)
    assert record.measured is True
    assert record.star_velocity_flag is False
    assert record.stars_audited == 100
    assert record.star_velocity == {"recent_stars": 100, "window_days": 90}


def test_failed_fetch_is_unmeasured_never_false_on_any_of_three_fields(tmp_path: Path, block_live_network):
    """A failed star-history fetch -> Unmeasured(collector_failure) on all
    three fields -- never a silent False/0."""
    record = star_velocity_evidence(tmp_path, "acme", "unreachable", NOW, _failing_fetch, FETCHED_AT)
    assert record.measured is False
    assert record.reason == "collector_failure"
    # The critical over-correction guard: these must be None, not a falsy
    # stand-in that downstream code could mistake for a measured "no".
    assert record.star_velocity_flag is None
    assert record.star_velocity_flag is not False
    assert record.stars_audited is None
    assert record.stars_audited is not False
    assert record.stars_audited != 0
    assert record.star_velocity is None
    assert record.provenance is None


def test_evidence_writes_universe_entry_envelope_matching_the_record(tmp_path: Path, block_live_network):
    record = star_velocity_evidence(tmp_path, "acme", "widget", NOW, _burst_fetch, FETCHED_AT)
    path = tmp_path / UNIVERSE_ENTRY_COLLECTOR / "acme__widget.json"
    assert path.is_file()
    envelope = read_envelope(path)
    assert envelope.data == record.to_json()


def test_evidence_writes_universe_entry_envelope_on_failure_too(tmp_path: Path, block_live_network):
    star_velocity_evidence(tmp_path, "acme", "unreachable", NOW, _failing_fetch, FETCHED_AT)
    path = tmp_path / UNIVERSE_ENTRY_COLLECTOR / "acme__unreachable.json"
    assert path.is_file()
    envelope = read_envelope(path)
    assert envelope.data == {"measured": False, "reason": "collector_failure",
                              "detail": "star-history fetch failed for acme/unreachable"}


# --------------------------------------------------------------------------- #
# StarVelocityRecord: construction, JSON round-trip                          #
# --------------------------------------------------------------------------- #


def test_star_velocity_record_measured_round_trips_through_json():
    record = StarVelocityRecord(
        measured=True, star_velocity_flag=True, stars_audited=150,
        star_velocity={"recent_stars": 600, "window_days": 90},
        provenance={"path": "/x/star_history/a__b.json", "sha256": "a" * 64, "fetched_at": FETCHED_AT},
    )
    assert StarVelocityRecord.from_json(record.to_json()) == record


def test_star_velocity_record_unmeasured_round_trips_through_json():
    record = StarVelocityRecord(measured=False, reason="collector_failure", detail="boom")
    assert StarVelocityRecord.from_json(record.to_json()) == record


def test_star_velocity_record_universe_manifest_round_trips_a_full_candidate_row():
    """The universe manifest round-trips the fields (deliverable #1's own
    acceptance line): the record's to_json() output is exactly what
    scripts/build_universe.py merges into one candidates.jsonl row, and
    JSON-serializing + reparsing that row reconstructs the same record."""
    import json

    record = StarVelocityRecord(
        measured=True, star_velocity_flag=False, stars_audited=42,
        star_velocity={"recent_stars": 42, "window_days": 90},
        provenance={"path": "/x/star_history/a__b.json", "sha256": "b" * 64, "fetched_at": FETCHED_AT},
    )
    candidate_row = {"owner": "a", "name": "b", "ecosystem": "pypi",
                      "selected_by": ["github-stars:x"], "stars_at_selection": 42,
                      "added_at": FETCHED_AT}
    candidate_row.update(record.to_json())
    text = json.dumps(candidate_row)
    reloaded = json.loads(text)
    assert StarVelocityRecord.from_json(reloaded) == record
    assert reloaded["owner"] == "a"  # the rest of the manifest row survives untouched


@pytest.mark.parametrize("reason", ["not_applicable", "insufficient_n", "no_population", "bogus"])
def test_star_velocity_record_unmeasured_reason_must_be_collector_failure_class(reason):
    """This check has no third-party-gap/insufficient-n/not-applicable case
    of its own -- only collector_failure is a real outcome for it, but the
    constructor still enforces membership in the general D-MISS-1
    vocabulary rather than hardcoding just the one string."""
    from cvi.core import constants_v2

    if reason in constants_v2.UNMEASURED_REASONS:
        StarVelocityRecord(measured=False, reason=reason)  # does not raise
    else:
        with pytest.raises(ValueError):
            StarVelocityRecord(measured=False, reason=reason)


def test_star_velocity_record_measured_true_requires_every_field():
    with pytest.raises(ValueError):
        StarVelocityRecord(measured=True)


def test_star_velocity_record_measured_true_rejects_a_reason():
    with pytest.raises(ValueError):
        StarVelocityRecord(
            measured=True, star_velocity_flag=True, stars_audited=1,
            star_velocity={"recent_stars": 1, "window_days": 90},
            provenance={"path": "x", "sha256": "a" * 64, "fetched_at": FETCHED_AT},
            reason="collector_failure",
        )


def test_star_velocity_record_unmeasured_rejects_any_value_field():
    with pytest.raises(ValueError):
        StarVelocityRecord(measured=False, reason="collector_failure", stars_audited=0)
