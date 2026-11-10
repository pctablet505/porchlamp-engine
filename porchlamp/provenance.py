"""Provenance envelope for every collected field (data-verification contract).

Every metric written by any collector MUST be wrapped in a :class:`FieldRecord`
so the verifier can trace it to a source endpoint, timestamp, and raw-response
checksum. Raw collector output lives under ``data/raw/<collector>/<owner>__<name>.json``
(one file per repo, keyed by the collector name), and the verifier merges those
envelopes into ``data/verified/universe.json`` + per-repo verified files.

Schema (JSON on disk)::

    {
      "repo": "owner/name",
      "collector": "github_meta",
      "collected_at": "2026-09-12T10:00:00Z",
      "fields": {
        "stars": {
          "value": 62000,
          "source": "gh api repos/vllm-project/vllm .stargazers_count",
          "fetched_at": "2026-09-12T10:00:00Z",
          "raw_sha256": "<sha256 of raw response bytes>"
        },
        ...
      }
    }

Rules:
* ``value`` is the final typed value used downstream (int/float/str/bool/None).
* ``source`` is a human-runnable command or URL that reproduces the value.
* ``raw_sha256`` is the SHA-256 hex of the exact response bytes the value was
  parsed from (verifier re-checks by re-fetching when asked).
* A field that could not be collected is recorded with ``value: null`` and a
  ``source`` explaining the failure (HTTP code / missing endpoint). It is NOT
  silently omitted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RAW_DIR = DATA_DIR / "raw"
VERIFIED_DIR = DATA_DIR / "verified"

__all__ = [
    "FieldRecord",
    "RepoEnvelope",
    "raw_path",
    "sha256_of",
    "utc_now_iso",
]


def utc_now_iso(now: datetime) -> str:
    """``now`` as an ISO-8601 UTC string (seconds precision).

    The caller passes the reference time explicitly (D-VER-3): a naive
    ``now`` is treated as UTC, an aware one is converted to UTC.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_of(raw: bytes | str) -> str:
    """SHA-256 hex digest of raw response bytes (or str, UTF-8 encoded)."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass
class FieldRecord:
    """One verified-collectible metric with full provenance."""

    value: Any
    source: str
    fetched_at: str
    raw_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "source": self.source,
            "fetched_at": self.fetched_at,
            "raw_sha256": self.raw_sha256,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FieldRecord":
        return cls(
            value=d["value"],
            source=d["source"],
            fetched_at=d["fetched_at"],
            raw_sha256=d["raw_sha256"],
        )

    @classmethod
    def record(
        cls,
        value: Any,
        source: str,
        raw: bytes | str | None = None,
        *,
        fetched_at: str,
    ) -> "FieldRecord":
        """Convenience constructor: the caller stamps ``fetched_at``; this
        method checksums the raw response."""
        digest = sha256_of(raw) if raw is not None else ""
        return cls(value=value, source=source, fetched_at=fetched_at, raw_sha256=digest)


@dataclass
class RepoEnvelope:
    """Per-repo collector output: repo id + collector name + field records."""

    repo: str  # "owner/name"
    collector: str  # e.g. "github_meta", "pr_events", "registries", "openssf"
    collected_at: str
    fields: dict[str, FieldRecord] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "collector": self.collector,
            "collected_at": self.collected_at,
            "fields": {k: v.to_dict() for k, v in self.fields.items()},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RepoEnvelope":
        return cls(
            repo=d["repo"],
            collector=d["collector"],
            collected_at=d["collected_at"],
            fields={k: FieldRecord.from_dict(v) for k, v in d["fields"].items()},
        )

    def save(self, collector: Optional[str] = None) -> Path:
        """Write the envelope to ``data/raw/<collector>/<owner>__<name>.json``."""
        col = collector or self.collector
        path = raw_path(col, self.repo)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return path

    @classmethod
    def load(cls, collector: str, repo: str) -> "RepoEnvelope":
        return cls.from_dict(json.loads(raw_path(collector, repo).read_text(encoding="utf-8")))


def raw_path(collector: str, repo: str) -> Path:
    """Filesystem path for a collector's envelope for ``owner/name``."""
    owner, name = repo.split("/", 1)
    return RAW_DIR / collector / f"{owner}__{name}.json"
