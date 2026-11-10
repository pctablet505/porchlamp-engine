"""Tests for porchlamp.provenance (data-verification contract)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone, timedelta

from porchlamp.provenance import (
    DATA_DIR,
    RAW_DIR,
    VERIFIED_DIR,
    FieldRecord,
    RepoEnvelope,
    raw_path,
    sha256_of,
    utc_now_iso,
)


def test_constants():
    assert DATA_DIR.name == "data"
    assert RAW_DIR == DATA_DIR / "raw"
    assert VERIFIED_DIR == DATA_DIR / "verified"


def test_utc_now_iso_requires_an_explicit_time():
    """The formatter takes the reference time in; no wall-clock read."""
    aware = datetime(2026, 9, 12, 10, 30, 45, tzinfo=timezone.utc)
    assert utc_now_iso(aware) == "2026-09-12T10:30:45Z"
    naive = datetime(2026, 9, 12, 10, 30, 45)
    assert utc_now_iso(naive) == "2026-09-12T10:30:45Z"
    offset = aware.replace(tzinfo=timezone(timedelta(hours=5)))
    assert utc_now_iso(offset) == "2026-09-12T05:30:45Z"


def test_sha256_of_str_and_bytes():
    text = "hello world"
    raw_bytes = text.encode("utf-8")
    expected = hashlib.sha256(raw_bytes).hexdigest()

    assert sha256_of(text) == expected
    assert sha256_of(raw_bytes) == expected


def test_field_record_to_and_from_dict():
    record = FieldRecord(
        value=123,
        source="gh api repos/foo/bar",
        fetched_at="2026-09-12T10:00:00Z",
        raw_sha256="abc123def456",
    )
    d = record.to_dict()
    assert d == {
        "value": 123,
        "source": "gh api repos/foo/bar",
        "fetched_at": "2026-09-12T10:00:00Z",
        "raw_sha256": "abc123def456",
    }
    restored = FieldRecord.from_dict(d)
    assert restored == record


def test_field_record_convenience_constructor():
    # 1. With raw payload; the caller stamps fetched_at
    raw_content = '{"stars": 42}'
    rec1 = FieldRecord.record(
        value=42, source="gh api", raw=raw_content,
        fetched_at="2026-09-12T10:00:00Z",
    )
    assert rec1.value == 42
    assert rec1.source == "gh api"
    assert rec1.raw_sha256 == sha256_of(raw_content)
    assert rec1.fetched_at == "2026-09-12T10:00:00Z"

    # 2. Without raw payload (raw=None)
    rec2 = FieldRecord.record(
        value=None, source="missing endpoint", raw=None,
        fetched_at="2026-09-12T10:00:01Z",
    )
    assert rec2.value is None
    assert rec2.source == "missing endpoint"
    assert rec2.raw_sha256 == ""
    assert rec2.fetched_at == "2026-09-12T10:00:01Z"


def test_raw_path():
    path = raw_path("github_meta", "fastapi/fastapi")
    assert path == RAW_DIR / "github_meta" / "fastapi__fastapi.json"


def test_repo_envelope_to_and_from_dict():
    f1 = FieldRecord.record(
        value=100, source="test source", fetched_at="2026-09-12T11:59:59Z"
    )
    envelope = RepoEnvelope(
        repo="owner/repo",
        collector="github_meta",
        collected_at="2026-09-12T12:00:00Z",
        fields={"metric1": f1},
    )
    d = envelope.to_dict()
    assert d["repo"] == "owner/repo"
    assert d["collector"] == "github_meta"
    assert d["collected_at"] == "2026-09-12T12:00:00Z"
    assert "metric1" in d["fields"]
    assert d["fields"]["metric1"]["value"] == 100

    restored = RepoEnvelope.from_dict(d)
    assert restored.repo == "owner/repo"
    assert restored.collector == "github_meta"
    assert restored.collected_at == "2026-09-12T12:00:00Z"
    assert restored.fields["metric1"] == f1


def test_repo_envelope_save_and_load(tmp_path, monkeypatch):
    monkeypatch.setattr("porchlamp.provenance.RAW_DIR", tmp_path)

    f1 = FieldRecord.record(
        value=999, source="test", raw="payload", fetched_at="2026-09-12T11:00:00Z"
    )
    envelope = RepoEnvelope(
        repo="org/project",
        collector="test_collector",
        collected_at="2026-09-12T11:00:00Z",
        fields={"stars": f1},
    )

    # Save with default collector
    saved_path = envelope.save()
    assert saved_path == tmp_path / "test_collector" / "org__project.json"
    assert saved_path.exists()

    # Load back
    loaded = RepoEnvelope.load("test_collector", "org/project")
    assert loaded.repo == "org/project"
    assert loaded.collector == "test_collector"
    assert loaded.fields["stars"].value == 999
    assert loaded.fields["stars"].raw_sha256 == sha256_of("payload")

    # Save with explicit collector override
    saved_path_custom = envelope.save(collector="custom_collector")
    assert saved_path_custom == tmp_path / "custom_collector" / "org__project.json"
    assert saved_path_custom.exists()

    loaded_custom = RepoEnvelope.load("custom_collector", "org/project")
    assert loaded_custom.repo == "org/project"
