"""Tests for bulk GH Archive event stream replay and cross-check (D-COL-11, W6-10).

Subject: ``porchlamp.collect.gharchive`` and ``scripts/gharchive_replay.py``.

Verifies:
  * Hour token parsing, deterministic ``observed_at`` derivation (the END of the
    hour, no wall clock),
    and canonical ``https://data.gharchive.org/YYYY-MM-DD-H.json.gz`` URL validation.
  * Streaming gzip and plain JSONL parsing with decompression size cap enforcement.
  * Candidate-set filtering and event shaping across all supported event types
    (PullRequestEvent, PullRequestReviewEvent, PullRequestReviewCommentEvent,
    IssuesEvent, PushEvent, ReleaseEvent).
  * Idempotent ledger append by ``(event_id, object_id, observed_at)``.
  * ``ledger_vs_archive`` cross-check over time windows (exact match -> 0
    discrepancies; injected mismatch -> recorded ``ArchiveDiscrepancy``).
  * CLI execution via ``scripts/gharchive_replay.py`` (both replay and ``--verify-only``).
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from porchlamp.collect.gharchive import (
    ArchiveLedgerRecord,
    IncompletePayloadError,
    archive_hour_url,
    fetch_gharchive_hour_bytes,
    hour_start,
    hour_to_observed_at,
    iter_hours,
    iter_jsonl_bytes,
    ledger_vs_archive,
    load_ledger_records,
    replay_hour_stream,
    shape_gharchive_event,
)

ROOT = Path(__file__).resolve().parent.parent.parent
HOUR = "2026-09-11-14"


def _load_replay_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "porchlamp_gharchive_replay_cli", ROOT / "scripts" / "gharchive_replay.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["porchlamp_gharchive_replay_cli"] = mod
    spec.loader.exec_module(mod)
    return mod


def _sample_events() -> list[dict[str, Any]]:
    return [
        # 1. Merged PR on candidate acme/widget
        {
            "id": "ev-pr-1",
            "type": "PullRequestEvent",
            "created_at": "2026-09-11T14:05:00Z",
            "repo": {"name": "acme/widget"},
            "actor": {"login": "alice"},
            "payload": {
                "action": "closed",
                "number": 42,
                "pull_request": {
                    "number": 42,
                    "state": "closed",
                    "created_at": "2026-09-10T10:00:00Z",
                    "updated_at": "2026-09-11T14:05:00Z",
                    "closed_at": "2026-09-11T14:05:00Z",
                    "merged_at": "2026-09-11T14:05:00Z",
                    "merged": True,
                },
            },
        },
        # 2. PR review on candidate acme/widget
        {
            "id": "ev-rev-1",
            "type": "PullRequestReviewEvent",
            "created_at": "2026-09-11T14:10:00Z",
            "repo": {"name": "acme/widget"},
            "actor": {"login": "bob"},
            "payload": {
                "action": "submitted",
                "pull_request": {"number": 42, "state": "open"},
                "review": {
                    "state": "APPROVED",
                    "body": "LGTM",
                },
            },
        },
        # 3. PR review comment on candidate acme/widget
        {
            "id": "ev-rc-1",
            "type": "PullRequestReviewCommentEvent",
            "created_at": "2026-09-11T14:11:00Z",
            "repo": {"name": "acme/widget"},
            "actor": {"login": "bob"},
            "payload": {
                "action": "created",
                "pull_request": {"number": 42, "state": "open"},
                "comment": {"body": "nit: rename variable"},
            },
        },
        # 4. Issue closed on candidate acme/widget
        {
            "id": "ev-iss-1",
            "type": "IssuesEvent",
            "created_at": "2026-09-11T14:20:00Z",
            "repo": {"name": "acme/widget"},
            "actor": {"login": "carol"},
            "payload": {
                "action": "closed",
                "issue": {
                    "number": 7,
                    "state": "closed",
                    "updated_at": "2026-09-11T14:20:00Z",
                    "closed_at": "2026-09-11T14:20:00Z",
                    "comments": 3,
                },
            },
        },
        # 5. Push event with 2 commits on candidate acme/widget
        {
            "id": "ev-push-1",
            "type": "PushEvent",
            "created_at": "2026-09-11T14:25:00Z",
            "repo": {"name": "acme/widget"},
            "actor": {"login": "alice"},
            "payload": {
                "ref": "refs/heads/main",
                "head": "deadbeef",
                "commits": [
                    {
                        "sha": "sha-1",
                        "author": {"email": "alice@corp.com"},
                        "message": "feat: bulk\n\nSigned-off-by: Alice <alice@corp.com>",
                    },
                    {
                        "sha": "sha-2",
                        "author": {"email": "bob@corp.com"},
                        "message": "fix: typo",
                    },
                ],
            },
        },
        # 6. Release published on candidate acme/widget
        {
            "id": "ev-rel-1",
            "type": "ReleaseEvent",
            "created_at": "2026-09-11T14:30:00Z",
            "repo": {"name": "acme/widget"},
            "actor": {"login": "alice"},
            "payload": {
                "action": "published",
                "release": {
                    "tag_name": "v1.2.0",
                    "published_at": "2026-09-11T14:30:00Z",
                    "prerelease": False,
                },
            },
        },
        # 7. Non-candidate repo event (must be filtered out)
        {
            "id": "ev-other-1",
            "type": "PullRequestEvent",
            "created_at": "2026-09-11T14:35:00Z",
            "repo": {"name": "other/unrelated"},
            "actor": {"login": "zed"},
            "payload": {"action": "opened", "number": 1, "pull_request": {"number": 1}},
        },
    ]


# --- Hour parsing & URL allowlist -------------------------------------------- #


def test_hour_to_observed_at_and_url_validation():
    # observed_at is the END of the hour: the dump is complete only then
    assert hour_to_observed_at("2026-09-11-0") == datetime(2026, 9, 11, 1, 0, tzinfo=timezone.utc)
    assert hour_to_observed_at("2026-09-11-14") == datetime(2026, 9, 11, 15, 0, tzinfo=timezone.utc)
    assert hour_to_observed_at("2026-09-11-23") == datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc)
    assert hour_start("2026-09-11-14") == datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
    assert archive_hour_url("2026-09-11-14") == "https://data.gharchive.org/2026-09-11-14.json.gz"
    assert archive_hour_url("2026-09-11-04") == "https://data.gharchive.org/2026-09-11-4.json.gz"

    with pytest.raises(ValueError, match="hour must be in 0..23"):
        hour_to_observed_at("2026-09-11-24")

    with pytest.raises(ValueError, match="invalid GH Archive hour"):
        archive_hour_url("2026/09/11/14")


# --- Streaming gzip & decompression cap -------------------------------------- #


def test_iter_jsonl_bytes_gzip_and_size_cap():
    lines = [json.dumps(e) for e in _sample_events()]
    raw_bytes = ("\n".join(lines) + "\n").encode("utf-8")
    gz_bytes = gzip.compress(raw_bytes)

    parsed_plain = list(iter_jsonl_bytes(raw_bytes, compressed=False))
    parsed_gz = list(iter_jsonl_bytes(gz_bytes, compressed=True))
    assert len(parsed_plain) == 7
    assert parsed_plain == parsed_gz

    with pytest.raises(ValueError, match="exceeded"):
        list(iter_jsonl_bytes(gz_bytes, compressed=True, max_bytes=64))


# --- Event shaping & candidate filtering ------------------------------------- #


def test_shape_gharchive_event_all_supported_types():
    lookup = {"acme/widget": ("acme", "widget")}
    obs = hour_to_observed_at(HOUR).isoformat()
    shaped: list[ArchiveLedgerRecord] = []
    for ev in _sample_events():
        shaped.extend(shape_gharchive_event(ev, lookup, obs))

    # 6 candidate events; PushEvent has 2 commits -> 7 ledger records total
    assert len(shaped) == 7
    by_id = {r.event_id: r for r in shaped}

    pr = by_id["ev-pr-1"]
    assert pr.repo_key == "acme__widget"
    assert pr.ledger == "prs"
    assert pr.payload_summary["merged"] is True
    assert pr.object_id == "42"

    rev = by_id["ev-rev-1"]
    assert rev.payload_summary["review_state"] == "APPROVED"
    assert rev.payload_summary["review_body_len"] == 4

    c0 = by_id["ev-push-1:0"]
    assert c0.ledger == "commits"
    assert c0.payload_summary["signed_off"] is True
    c1 = by_id["ev-push-1:1"]
    assert c1.payload_summary["signed_off"] is False

    rel = by_id["ev-rel-1"]
    assert rel.ledger == "releases"
    assert rel.object_id == "v1.2.0"


# --- Replay & idempotency + cross-check -------------------------------------- #


def test_replay_hour_stream_idempotent_and_cross_check(tmp_path: Path):
    ledgers_dir = tmp_path / "ledgers"
    candidates = [("acme", "widget")]
    events = _sample_events()

    rep1 = replay_hour_stream(
        events,
        candidates=candidates,
        ledgers_dir=ledgers_dir,
        hour_spec=HOUR,
    )
    assert rep1.events_scanned == 7
    assert rep1.events_matched == 7
    assert rep1.records_appended == 7
    assert rep1.repos_touched == 1

    # Replaying the exact same hour stream is a no-op (0 duplicate records appended)
    rep2 = replay_hour_stream(
        events,
        candidates=candidates,
        ledgers_dir=ledgers_dir,
        hour_spec=HOUR,
    )
    assert rep2.events_matched == 7
    assert rep2.records_appended == 0
    assert len(load_ledger_records(ledgers_dir / "prs" / "acme__widget.jsonl")) == 3

    # Cross-check against the exact same archive records -> 0 discrepancies
    obs = hour_to_observed_at(HOUR).isoformat()
    lookup = {"acme/widget": ("acme", "widget")}
    archive_records: list[ArchiveLedgerRecord] = []
    for ev in events:
        archive_records.extend(shape_gharchive_event(ev, lookup, obs))
    assert ledger_vs_archive(ledgers_dir, archive_records) == []

    # Inject an extra release in archive_records that is missing from on-disk ledger:
    extra_rel = ArchiveLedgerRecord(
        ledger="releases",
        owner="acme",
        name="widget",
        object_id="v1.3.0",
        event_id="ev-rel-missing",
        event_type="ReleaseEvent",
        action="published",
        actor_login="alice",
        created_at="2026-09-11T14:40:00Z",
        observed_at=obs,
        payload_summary={"tag_name": "v1.3.0"},
    )
    diffs = ledger_vs_archive(ledgers_dir, [*archive_records, extra_rel])
    assert len(diffs) == 1
    assert diffs[0].repo_key == "acme__widget"
    assert diffs[0].ledger == "releases"
    assert diffs[0].missing_in_ledger == ("v1.3.0",)


def test_gharchive_replay_cli_runner(tmp_path: Path, capsys):
    cli = _load_replay_script()
    cand_file = tmp_path / "raw" / "candidates.jsonl"
    cand_file.parent.mkdir(parents=True, exist_ok=True)
    cand_file.write_text(
        json.dumps({"owner": "acme", "name": "widget", "ecosystem": "pypi"}) + "\n",
        encoding="utf-8",
    )
    in_file = tmp_path / "hour.json.gz"
    in_file.write_bytes(
        gzip.compress(("\n".join(json.dumps(e) for e in _sample_events()) + "\n").encode("utf-8"))
    )

    rc = cli.main(
        [
            "--data-dir",
            str(tmp_path),
            "--hour",
            HOUR,
            "--archive-file",
            str(in_file),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert f"gharchive replay {HOUR}: scanned=7 matched=7 appended=7 repos=1" in out

    # Now run --verify-only against the populated ledgers:
    rc_verify = cli.main(
        [
            "--data-dir",
            str(tmp_path),
            "--hour",
            HOUR,
            "--archive-file",
            str(in_file),
            "--verify-only",
        ]
    )
    assert rc_verify == 0
    out_verify = capsys.readouterr().out
    assert f"gharchive verify {HOUR}: 7 archive records, 0 discrepancies" in out_verify


# --- Trimmed payloads are refused, not written as nulls ------------------------- #

LOOKUP = {"acme/widget": ("acme", "widget")}


def _trimmed(event: dict[str, Any]) -> dict[str, Any]:
    """The event as GH Archive serves it today: payload reduced to ids and numbers."""
    ev = json.loads(json.dumps(event))
    p = ev["payload"]
    if ev["type"] == "PullRequestEvent":
        p["pull_request"] = {"number": p["pull_request"]["number"]}
    elif ev["type"] in ("PullRequestReviewEvent", "PullRequestReviewCommentEvent"):
        p["pull_request"] = {"number": p["pull_request"]["number"]}
        p.pop("review", None)
        p.pop("comment", None)
    elif ev["type"] == "IssuesEvent":
        p["issue"] = {"number": p["issue"]["number"]}
    elif ev["type"] == "PushEvent":
        p.pop("commits")
    elif ev["type"] == "ReleaseEvent":
        p["release"] = {"tag_name": p["release"]["tag_name"]}
    return ev


@pytest.mark.parametrize("index", range(6))
def test_shape_refuses_a_trimmed_payload_for_every_supported_event_type(index: int):
    ev = _sample_events()[index]
    assert shape_gharchive_event(ev, LOOKUP, "2026-09-11T15:00:00+00:00")  # full payload: fine
    with pytest.raises(IncompletePayloadError, match="refusing to write"):
        shape_gharchive_event(_trimmed(ev), LOOKUP, "2026-09-11T15:00:00+00:00")


def test_shape_ignores_trimmed_events_of_repos_outside_the_universe():
    other = _trimmed(_sample_events()[0])
    other["repo"] = {"name": "other/unrelated"}
    assert shape_gharchive_event(other, LOOKUP, "2026-09-11T15:00:00+00:00") == []


def test_replay_writes_nothing_when_a_payload_is_trimmed(tmp_path: Path):
    events = _sample_events()
    events[4] = _trimmed(events[4])  # the push lost its commits
    with pytest.raises(IncompletePayloadError):
        replay_hour_stream(events, candidates=[("acme", "widget")],
                           ledgers_dir=tmp_path / "ledgers", hour_spec=HOUR)
    assert not (tmp_path / "ledgers").exists()


def test_replay_records_are_observed_at_the_end_of_the_hour(tmp_path: Path):
    ledgers = tmp_path / "ledgers"
    rep = replay_hour_stream(_sample_events(), candidates=[("acme", "widget")],
                             ledgers_dir=ledgers, hour_spec=HOUR)
    assert rep.observed_at == "2026-09-11T15:00:00+00:00"
    assert {r.observed_at for r in load_ledger_records(ledgers / "prs" / "acme__widget.jsonl")} == {
        "2026-09-11T15:00:00+00:00"}


def test_iter_hours_is_inclusive_and_crosses_midnight():
    assert list(iter_hours("2026-09-11-22", "2026-09-12-1")) == [
        "2026-09-11-22", "2026-09-11-23", "2026-09-12-0", "2026-09-12-1"]
    assert list(iter_hours("2026-09-11-5", "2026-09-11-5")) == ["2026-09-11-5"]
    with pytest.raises(ValueError, match="before start"):
        list(iter_hours("2026-09-11-5", "2026-09-11-4"))


def test_fetch_over_the_cap_raises_instead_of_truncating(monkeypatch: pytest.MonkeyPatch):
    import porchlamp.collect.gharchive as ga

    monkeypatch.setattr(ga, "MAX_ARCHIVE_DECOMPRESSED_BYTES", 16)

    class _Resp:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

        def read(self, n: int = -1) -> bytes:
            return self.payload if n < 0 else self.payload[:n]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ga.urllib.request, "urlopen", lambda req, timeout=0: _Resp(b"x" * 40))
    with pytest.raises(ValueError, match="exceeds"):
        fetch_gharchive_hour_bytes(HOUR)
    monkeypatch.setattr(ga.urllib.request, "urlopen", lambda req, timeout=0: _Resp(b"x" * 16))
    assert fetch_gharchive_hour_bytes(HOUR) == b"x" * 16


def _write_dump(path: Path, events: list[dict[str, Any]]) -> None:
    path.write_bytes(gzip.compress(("\n".join(json.dumps(e) for e in events) + "\n").encode("utf-8")))


def _seed_candidates(tmp_path: Path) -> None:
    cand = tmp_path / "raw" / "candidates.jsonl"
    cand.parent.mkdir(parents=True, exist_ok=True)
    cand.write_text(json.dumps({"owner": "acme", "name": "widget", "ecosystem": "pypi"}) + "\n")


def test_cli_exits_nonzero_and_writes_nothing_on_a_trimmed_dump(tmp_path: Path, capsys):
    cli = _load_replay_script()
    _seed_candidates(tmp_path)
    dump = tmp_path / "hour.json.gz"
    _write_dump(dump, [_trimmed(e) for e in _sample_events()])
    rc = cli.main(["--data-dir", str(tmp_path), "--hour", HOUR, "--archive-file", str(dump)])
    assert rc == 2
    assert "REFUSED" in capsys.readouterr().err
    assert not (tmp_path / "ledgers").exists()


def test_cli_range_replays_every_hour_and_stops_at_a_refused_one(tmp_path: Path, monkeypatch, capsys):
    cli = _load_replay_script()
    _seed_candidates(tmp_path)
    good = _sample_events()
    dumps = {
        "2026-09-11-14": good,
        "2026-09-11-15": [dict(e, id=e["id"] + "-b") for e in good],
        "2026-09-11-16": [_trimmed(e) for e in good],
        "2026-09-11-17": good,
    }
    fetched: list[str] = []

    def _fake_fetch(hour: str) -> bytes:
        fetched.append(hour)
        dump = tmp_path / "fetch.json.gz"
        _write_dump(dump, dumps[hour])
        return dump.read_bytes()

    monkeypatch.setattr(cli, "fetch_gharchive_hour_bytes", _fake_fetch)
    rc = cli.main(["--data-dir", str(tmp_path), "--start", "2026-09-11-14", "--end", "2026-09-11-17"])
    assert rc == 2
    assert fetched == ["2026-09-11-14", "2026-09-11-15", "2026-09-11-16"]  # 17 never reached
    out = capsys.readouterr()
    assert "gharchive replay 2026-09-11-14" in out.out and "gharchive replay 2026-09-11-15" in out.out
    assert "REFUSED at hour 2026-09-11-16" in out.err
    recs = load_ledger_records(tmp_path / "ledgers" / "releases" / "acme__widget.jsonl")
    assert {r.observed_at for r in recs} == {"2026-09-11T15:00:00+00:00", "2026-09-11T16:00:00+00:00"}


def test_cli_range_options_are_validated(tmp_path: Path):
    cli = _load_replay_script()
    _seed_candidates(tmp_path)
    for argv in (["--hour", HOUR, "--start", HOUR, "--end", HOUR],
                 ["--start", HOUR],
                 [],
                 ["--start", "2026-09-11-5", "--end", "2026-09-11-4"],
                 ["--start", HOUR, "--end", HOUR, "--archive-file", str(tmp_path / "x.gz")]):
        with pytest.raises(SystemExit) as exc:
            cli.main(["--data-dir", str(tmp_path), *argv])
        assert exc.value.code == 2
