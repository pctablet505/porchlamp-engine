"""Unified envelope store interface and implementations (File and SQLite WAL Shards).

Provides a storage seam allowing Porchlamp to store raw envelopes either as
per-repo JSON files (FileEnvelopeStore, default at 10k scale) or in sharded
SQLite WAL databases (SqliteShardEnvelopeStore, for 100k-10M scale without inode
exhaustion), with strict path-traversal containment and parameterized queries.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Protocol, Union, runtime_checkable

from porchlamp.collect.envelope import (
    COLLECTOR_VERSION,
    Envelope,
    _plain,
    canonical_json,
    envelope_hash,
    read_envelope,
    write_envelope,
)


def _validate_segment(segment: str) -> str:
    """Validate a path segment, rejecting path traversal and separators.

    Raises ValueError if segment is empty, contains path separators ('/' or '\\'),
    parent traversal sequences ('..'), or null bytes.
    """
    if not segment or not isinstance(segment, str):
        raise ValueError("Segment must be a non-empty string")
    if "\0" in segment:
        raise ValueError("Null bytes are forbidden in path segments")
    if ".." in segment:
        raise ValueError(f"Parent directory traversal sequences forbidden in segment: {segment!r}")
    if "/" in segment or "\\" in segment:
        raise ValueError(f"Path separators are forbidden in segment: {segment!r}")
    return segment


def _assert_contained(path: Path, root: Path) -> Path:
    """Verify that path is strictly contained within root, raising ValueError otherwise."""
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise ValueError(f"Path traversal detected: {path} is outside {root}")
    return resolved_path


def fnv1a_32(text: str) -> int:
    """Deterministic 32-bit FNV-1a hash used for shard distribution."""
    h = 0x811C9DC5
    for byte in text.encode("utf-8"):
        h = ((h ^ byte) * 0x01000193) & 0xFFFFFFFF
    return h


@runtime_checkable
class EnvelopeStore(Protocol):
    """Storage protocol for raw envelope collection."""

    def get(self, collector: str, key: str) -> Optional[Envelope]:
        """Retrieve an envelope by collector and key, or None if not found."""
        ...

    def put(
        self,
        collector: str,
        key: str,
        *,
        source: str,
        data: Any,
        fetched_at: Optional[str] = None,
    ) -> Envelope:
        """Store an envelope and return the recorded Envelope instance."""
        ...

    def exists(self, collector: str, key: str) -> bool:
        """Return True if an envelope exists for the collector and key."""
        ...

    def list_keys(self, collector: str) -> list[str]:
        """Return sorted list of repo keys stored under the collector."""
        ...


class FileEnvelopeStore:
    """Envelope store backed by per-collector/per-repo JSON files on disk."""

    def __init__(self, raw_dir: Union[Path, str]) -> None:
        self.raw_dir = Path(raw_dir).resolve()
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def _target_path(self, collector: str, key: str) -> Path:
        col = _validate_segment(collector)
        k = _validate_segment(key)
        target = self.raw_dir / col / f"{k}.json"
        _assert_contained(target, self.raw_dir)
        return target

    def get(self, collector: str, key: str) -> Optional[Envelope]:
        """Retrieve envelope from disk, or None if not found."""
        target = self._target_path(collector, key)
        if not target.exists():
            return None
        return read_envelope(target)

    def put(
        self,
        collector: str,
        key: str,
        *,
        source: str,
        data: Any,
        fetched_at: Optional[str] = None,
    ) -> Envelope:
        """Write envelope to atomic JSON file on disk and return parsed Envelope."""
        target = self._target_path(collector, key)
        ts = fetched_at or datetime.now(timezone.utc).isoformat()
        written = write_envelope(target, source=source, data=data, fetched_at=ts)
        return read_envelope(written)

    def exists(self, collector: str, key: str) -> bool:
        """Return True if envelope file exists on disk."""
        target = self._target_path(collector, key)
        return target.exists()

    def list_keys(self, collector: str) -> list[str]:
        """Return sorted list of keys found under raw_dir / collector."""
        col = _validate_segment(collector)
        col_dir = self.raw_dir / col
        _assert_contained(col_dir, self.raw_dir)
        if not col_dir.is_dir():
            return []
        return sorted(p.stem for p in col_dir.glob("*.json"))


class SqliteShardEnvelopeStore:
    """Envelope store sharded across SQLite WAL databases."""

    def __init__(self, db_dir: Union[Path, str], num_shards: int = 16) -> None:
        if num_shards < 1:
            raise ValueError("num_shards must be at least 1")
        self.db_dir = Path(db_dir).resolve()
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.num_shards = num_shards
        self._connections: dict[int, sqlite3.Connection] = {}
        self._lock = threading.Lock()

    def _shard_index(self, collector: str, key: str) -> int:
        return fnv1a_32(f"{collector}/{key}") % self.num_shards

    def _shard_path(self, shard_idx: int) -> Path:
        target = self.db_dir / f"shard_{shard_idx:02x}.sqlite"
        _assert_contained(target, self.db_dir)
        return target

    def _get_connection(self, shard_idx: int) -> sqlite3.Connection:
        if shard_idx not in self._connections:
            shard_path = self._shard_path(shard_idx)
            conn = sqlite3.connect(shard_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS envelopes (
                    collector TEXT NOT NULL,
                    repo_key TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    http_status INTEGER,
                    etag TEXT,
                    collector_version TEXT,
                    PRIMARY KEY (collector, repo_key)
                );
                """
            )
            self._connections[shard_idx] = conn
        return self._connections[shard_idx]

    def get(self, collector: str, key: str) -> Optional[Envelope]:
        """Retrieve envelope from corresponding SQLite shard."""
        col = _validate_segment(collector)
        k = _validate_segment(key)
        shard_idx = self._shard_index(col, k)
        shard_path = self._shard_path(shard_idx)
        if not shard_path.exists():
            return None

        with self._lock:
            conn = self._get_connection(shard_idx)
            cur = conn.cursor()
            cur.execute(
                """
                SELECT fetched_at, source, sha256, data_json, http_status, etag, collector_version
                FROM envelopes
                WHERE collector = ? AND repo_key = ?
                """,
                (col, k),
            )
            row = cur.fetchone()
        if row is None:
            return None

        fetched_at, source, sha, data_json, http_status, etag, col_ver = row
        loaded_data = json.loads(data_json)
        return Envelope(
            path=f"sqlite://shard_{shard_idx:02x}.sqlite/{col}/{k}",
            source=source,
            fetched_at=fetched_at,
            http_status=http_status,
            etag=etag,
            collector_version=col_ver,
            data=loaded_data,
            recorded_sha256=sha,
            computed_sha256=envelope_hash(loaded_data),
        )

    def put(
        self,
        collector: str,
        key: str,
        *,
        source: str,
        data: Any,
        fetched_at: Optional[str] = None,
    ) -> Envelope:
        """Store envelope in corresponding SQLite shard and return Envelope."""
        col = _validate_segment(collector)
        k = _validate_segment(key)
        ts = fetched_at or datetime.now(timezone.utc).isoformat()
        plain_data = _plain(data)
        data_json = canonical_json(plain_data)
        sha = envelope_hash(plain_data)

        shard_idx = self._shard_index(col, k)
        with self._lock:
            conn = self._get_connection(shard_idx)
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO envelopes
                    (collector, repo_key, fetched_at, source, sha256, data_json, http_status, etag, collector_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (col, k, ts, source, sha, data_json, 200, None, COLLECTOR_VERSION),
                )

        loaded_data = json.loads(data_json)
        return Envelope(
            path=f"sqlite://shard_{shard_idx:02x}.sqlite/{col}/{k}",
            source=source,
            fetched_at=ts,
            http_status=200,
            etag=None,
            collector_version=COLLECTOR_VERSION,
            data=loaded_data,
            recorded_sha256=sha,
            computed_sha256=sha,
        )

    def exists(self, collector: str, key: str) -> bool:
        """Return True if envelope exists in shard."""
        col = _validate_segment(collector)
        k = _validate_segment(key)
        shard_idx = self._shard_index(col, k)
        shard_path = self._shard_path(shard_idx)
        if not shard_path.exists():
            return False

        with self._lock:
            conn = self._get_connection(shard_idx)
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM envelopes WHERE collector = ? AND repo_key = ? LIMIT 1",
                (col, k),
            )
            return cur.fetchone() is not None

    def list_keys(self, collector: str) -> list[str]:
        """List all repo keys stored under collector across all shards."""
        col = _validate_segment(collector)
        keys: set[str] = set()
        with self._lock:
            for shard_idx in range(self.num_shards):
                shard_path = self._shard_path(shard_idx)
                if not shard_path.exists():
                    continue
                conn = self._get_connection(shard_idx)
                cur = conn.cursor()
                cur.execute("SELECT repo_key FROM envelopes WHERE collector = ?", (col,))
                for row in cur.fetchall():
                    keys.add(row[0])
        return sorted(keys)

    def close(self) -> None:
        """Close all open SQLite shard connections."""
        with self._lock:
            for conn in self._connections.values():
                conn.close()
            self._connections.clear()
