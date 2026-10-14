"""Unit tests for porchlamp.collect.envelope_store."""

from __future__ import annotations

from pathlib import Path

import pytest

from porchlamp.collect.envelope_store import (
    FileEnvelopeStore,
    SqliteShardEnvelopeStore,
    _validate_segment,
)


def test_validate_segment_path_traversal_rejection() -> None:
    # Empty string
    with pytest.raises(ValueError, match="non-empty string"):
        _validate_segment("")

    # Traversal sequences
    with pytest.raises(ValueError, match="Parent directory traversal"):
        _validate_segment("..")
    with pytest.raises(ValueError, match="Parent directory traversal"):
        _validate_segment("../etc/passwd")

    # Path separators
    with pytest.raises(ValueError, match="Path separators"):
        _validate_segment("foo/bar")
    with pytest.raises(ValueError, match="Path separators"):
        _validate_segment("foo\\bar")

    # Null bytes
    with pytest.raises(ValueError, match="Null bytes"):
        _validate_segment("foo\0bar")

    # Valid segments
    assert _validate_segment("valid_segment") == "valid_segment"
    assert _validate_segment("owner__repo") == "owner__repo"


def test_file_envelope_store_crud(tmp_path: Path) -> None:
    store = FileEnvelopeStore(tmp_path / "raw")
    collector = "github_meta"
    key = "octocat__hello-world"
    payload = {"stars": 42, "topics": ["git", "demo"]}

    assert store.exists(collector, key) is False
    assert store.get(collector, key) is None
    assert store.list_keys(collector) == []

    env = store.put(collector, key, source="test", data=payload, fetched_at="2026-10-14T00:00:00Z")
    assert env.data == payload
    assert env.source == "test"
    assert store.exists(collector, key) is True

    loaded = store.get(collector, key)
    assert loaded is not None
    assert loaded.data == payload
    assert loaded.sha256 == env.sha256
    assert loaded.hash_matches_data is True

    # List keys
    assert store.list_keys(collector) == [key]


def test_sqlite_shard_envelope_store_crud_and_sharding(tmp_path: Path) -> None:
    num_shards = 4
    store = SqliteShardEnvelopeStore(tmp_path / "sqlite_shards", num_shards=num_shards)

    keys = [f"owner__repo_{i}" for i in range(12)]
    collector = "github_meta"

    for i, k in enumerate(keys):
        store.put(collector, k, source="test", data={"index": i}, fetched_at="2026-10-14T00:00:00Z")

    # Verify keys are listed and exists works
    for k in keys:
        assert store.exists(collector, k) is True

    stored_keys = store.list_keys(collector)
    assert stored_keys == sorted(keys)

    # Verify distribution across multiple SQLite shard files
    shard_files = list((tmp_path / "sqlite_shards").glob("shard_*.sqlite"))
    assert len(shard_files) > 1

    store.close()


def test_store_parity_identical_sha256_and_data(tmp_path: Path) -> None:
    file_store = FileEnvelopeStore(tmp_path / "raw")
    sqlite_store = SqliteShardEnvelopeStore(tmp_path / "sqlite_shards", num_shards=4)

    collector = "prs"
    key = "torvalds__linux"
    data = {
        "count": 100,
        "labels": ["kernel", "driver"],
        "nested": {"author": "torvalds", "active": True},
    }
    ts = "2026-10-14T10:00:00+00:00"

    file_env = file_store.put(collector, key, source="github_api", data=data, fetched_at=ts)
    sqlite_env = sqlite_store.put(collector, key, source="github_api", data=data, fetched_at=ts)

    # Identical SHA-256 digests and data representation
    assert file_env.sha256 == sqlite_env.sha256
    assert file_env.data == sqlite_env.data
    assert file_env.source == sqlite_env.source
    assert file_env.fetched_at == sqlite_env.fetched_at

    # Read back parity
    file_read = file_store.get(collector, key)
    sqlite_read = sqlite_store.get(collector, key)

    assert file_read is not None and sqlite_read is not None
    assert file_read.sha256 == sqlite_read.sha256
    assert file_read.data == sqlite_read.data

    sqlite_store.close()


def test_file_envelope_store_path_traversal_rejection(tmp_path: Path) -> None:
    store = FileEnvelopeStore(tmp_path / "raw")
    with pytest.raises(ValueError):
        store.get("..", "evil")
    with pytest.raises(ValueError):
        store.put("collector", "../evil", source="test", data={})
    with pytest.raises(ValueError):
        store.exists("collector/sub", "key")


def test_sqlite_shard_envelope_store_data_survives_close_and_reopen(tmp_path: Path) -> None:
    """A put is committed: a new store instance over the same directory reads it back."""
    shards = tmp_path / "sqlite_shards"
    first = SqliteShardEnvelopeStore(shards, num_shards=4)
    written = first.put(
        "github_meta", "owner__repo", source="test", data={"stars": 5}, fetched_at="2026-10-14T00:00:00Z"
    )
    first.close()

    second = SqliteShardEnvelopeStore(shards, num_shards=4)
    loaded = second.get("github_meta", "owner__repo")
    assert loaded is not None
    assert loaded.data == {"stars": 5}
    assert loaded.source == "test"
    assert loaded.fetched_at == "2026-10-14T00:00:00Z"
    assert loaded.sha256 == written.sha256
    assert second.exists("github_meta", "owner__repo") is True
    assert second.list_keys("github_meta") == ["owner__repo"]
    second.close()
