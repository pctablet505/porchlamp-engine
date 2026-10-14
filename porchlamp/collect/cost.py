"""Per-collector API cost accounting for GitHub calls.

Tracks calls, HTTP status, GraphQL point costs, bytes received, and elapsed
duration per collector and per repository scope.
"""

from __future__ import annotations

import dataclasses
import json
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CostRecord:
    """A single recorded HTTP or GraphQL API call."""

    timestamp: str
    repo_key: str
    collector: str
    endpoint: str
    method: str
    status: int
    status_304: bool = False
    graphql_points: int = 0
    bytes_in: int = 0
    duration_ms: float = 0.0


_CURRENT_SCOPE: ContextVar[tuple[str, str]] = ContextVar(
    "cost_collector_scope", default=("", "unscoped")
)

_RECORDS: list[CostRecord] = []
_RETAIN = False
_LOCK = threading.Lock()


def retain_records(on: bool = True) -> None:
    """Keep every recorded call in memory (``--cost-log`` needs them); off by default, so a long-lived process does not grow."""
    global _RETAIN
    _RETAIN = on


def current_scope() -> tuple[str, str]:
    """Return the active ``(repo_key, collector)`` scope tuple."""
    return _CURRENT_SCOPE.get()


@contextmanager
def collector_scope(repo_key: str, collector: str) -> Iterator[None]:
    """Set the active ``(repo_key, collector)`` scope for the enclosed block."""
    token = _CURRENT_SCOPE.set((repo_key, collector))
    try:
        yield
    finally:
        _CURRENT_SCOPE.reset(token)


def record_call(
    *,
    endpoint: str,
    method: str,
    status: int,
    status_304: bool = False,
    graphql_points: int = 0,
    bytes_in: int = 0,
    duration_ms: float = 0.0,
    repo_key: str | None = None,
    collector: str | None = None,
    timestamp: str | None = None,
) -> CostRecord:
    """Record an API call; it joins the in-memory ledger only while ``retain_records`` is on."""
    scope_repo, scope_collector = current_scope()
    if repo_key is None:
        repo_key = scope_repo
    if collector is None:
        collector = scope_collector
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()
    if status == 304:
        status_304 = True

    record = CostRecord(
        timestamp=timestamp,
        repo_key=repo_key,
        collector=collector,
        endpoint=endpoint,
        method=method,
        status=status,
        status_304=status_304,
        graphql_points=graphql_points,
        bytes_in=bytes_in,
        duration_ms=duration_ms,
    )
    if _RETAIN:
        with _LOCK:
            _RECORDS.append(record)
    return record


def get_records() -> list[CostRecord]:
    """Return a snapshot copy of the in-memory cost records."""
    with _LOCK:
        return list(_RECORDS)


def clear_records() -> None:
    """Clear all records from the in-memory cost ledger."""
    with _LOCK:
        _RECORDS.clear()


def write_cost_log(path: Path, records: Sequence[CostRecord] | None = None) -> Path:
    """Write cost records as JSONL to ``path`` (creating parent directories)."""
    if records is None:
        records = get_records()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(dataclasses.asdict(r)) + "\n")
    return path


def read_cost_log(path: Path) -> list[CostRecord]:
    """Read cost records from a JSONL file at ``path``."""
    records: list[CostRecord] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            records.append(CostRecord(**data))
    return records


def summarize_costs(records: Sequence[CostRecord] | None = None) -> dict[str, dict[str, Any]]:
    """Aggregate cost records by collector."""
    if records is None:
        records = get_records()
    summary: dict[str, dict[str, Any]] = {}
    for r in records:
        if r.collector not in summary:
            summary[r.collector] = {
                "calls": 0,
                "status_304": 0,
                "graphql_points": 0,
                "bytes_in": 0,
                "duration_ms": 0.0,
            }
        s = summary[r.collector]
        s["calls"] += 1
        if r.status_304 or r.status == 304:
            s["status_304"] += 1
        s["graphql_points"] += r.graphql_points
        s["bytes_in"] += r.bytes_in
        s["duration_ms"] += r.duration_ms
    for s in summary.values():
        s["duration_ms"] = round(s["duration_ms"], 2)
    return summary
