"""Ingestion layer for the CVI pipeline (spec 5, 8, 13; schema 11.2).

W4-1 NOTE: the rate limiter / ETag cache / circuit breaker moved verbatim
to :mod:`cvi.collect.ratelimit` (`04-ARCHITECTURE.md` section 2). This file
now holds only what has no successor there: the GHArchive bulk-stream
stack (dead code, Tier A -- zero production callers, no relocation target
named anywhere in `04`) and the raw-metric hydration helpers. Once the
GHArchive stack and :func:`ingest_and_score` are also removed, this module
becomes a deprecation shim; see the module's own history for that step.

Wires the zero-cost GHArchive bulk stream into the operational store:

* :class:`GHArchiveStreamer` — fetches the hourly
  ``https://data.gharchive.org/{YYYY}/{MM}/{DD}/{HH}.json.gz`` partition
  (spec 5), gunzips it, and maps PR-related records to
  :class:`~cvi.models.RawPREvent` via :func:`map_pull_request_event`.
* :func:`apply_raw_metrics` — additive ``UPDATE repositories SET ...`` of
  the raw-metric columns that ``upsert_composite`` leaves at defaults
  (the ``stars=0`` gotcha; see ``cvi/lakehouse.py`` docstring), using the
  exact column names of the lakehouse ``CREATE TABLE repositories``
  (spec 11.2).
* :func:`ingest_and_score` — the orchestrator: ``score_repository`` ->
  ``upsert_composite`` -> ``apply_raw_metrics``.

Design notes / documented decisions
-----------------------------------
- ``map_pull_request_event`` is intentionally conservative: GHArchive
  ``PullRequestEvent`` payloads carry no author association, prior-commit
  counts, or review telemetry, so those fields default to the spec 9.1
  neutral values (``NONE`` / external, 0 prior commits, no review data).
- GHArchive timestamps are normalised to *naive UTC* datetimes, matching
  the convention used by the rest of the package.
- A merged PR whose payload omits ``closed_at`` gets
  ``closed_at = merged_at`` (merge implies closure; spec 9.1 requires
  ``merged_at <= closed_at``).
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Iterator, Mapping, Optional

from .collect.ratelimit import CachedHttpClient, IngestError
from .engine import score_repository
from .lakehouse import OperationalStore
from .models import (
    AIPosture,
    AuthorAssociation,
    LegalIPModel,
    Persona,
    RawPREvent,
    RepoMeta,
)

__all__ = [
    "GHARCHIVE_BASE_URL",
    "gharchive_hourly_url",
    "map_pull_request_event",
    "GHArchiveStreamer",
    "apply_raw_metrics",
    "ingest_and_score",
]

GHARCHIVE_BASE_URL = "https://data.gharchive.org"

_GZIP_MAGIC: bytes = b"\x1f\x8b"

#: PullRequestEvent actions that carry a state transition worth mapping.
_MAPPED_PR_ACTIONS: frozenset[str] = frozenset({"opened", "closed", "reopened", "merged"})


# --------------------------------------------------------------------------- #
# GHArchive streaming (spec 5, 8)
# --------------------------------------------------------------------------- #
def gharchive_hourly_url(when: datetime) -> str:
    """Hourly GHArchive partition URL for ``when`` (spec 5)::

        https://data.gharchive.org/{YYYY}/{MM}/{DD}/{HH}.json.gz
    """
    return (
        f"{GHARCHIVE_BASE_URL}/{when:%Y}/{when:%m}/{when:%d}/{when:%H}.json.gz"
    )


def _parse_iso8601(value: Any) -> Optional[datetime]:
    """Parse a GHArchive ISO-8601 timestamp to a naive-UTC datetime.

    GHArchive emits ``...Z`` (UTC); the trailing ``Z`` is stripped so the
    result matches the naive-UTC convention used across the package.
    Returns None for anything unparseable.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1]
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def map_pull_request_event(record: Mapping[str, Any]) -> Optional[RawPREvent]:
    """Conservatively map one GHArchive NDJSON record to a RawPREvent.

    Only ``type == "PullRequestEvent"`` records with an action in
    ``opened/closed/reopened/merged`` are mapped; everything else (pushes,
    issues, gists, ``edited``/``labeled``/... PR actions) returns None.

    Field provenance (documented; conservative where GHArchive is silent):

    * ``repo_owner`` / ``repo_name`` — split of ``record["repo"]["name"]``
      ("owner/name"); a missing/malformed repo field yields None.
    * ``pr_number`` — ``payload["pull_request"]["number"]`` (falls back to
      ``payload["number"]``); must be an int >= 1.
    * ``created_at`` — ``pull_request["created_at"]`` (ISO-8601 UTC). The
      record-level timestamp is the event time, not the PR creation time,
      so it is deliberately NOT used as a fallback; a missing/invalid
      ``pull_request.created_at`` yields None.
    * ``author_login`` — ``pull_request["user"]["login"]`` (falls back to
      ``record["actor"]["login"]``); missing author yields None.
    * ``author_association`` — not carried by GHArchive; defaults to
      ``AuthorAssociation.NONE`` (external per spec 9.1).
    * ``author_prior_merged_commits`` — unavailable; defaults to 0
      (first-timer assumption).
    * ``merged_at`` — ``pull_request["merged_at"]``.
    * ``closed_at`` — ``pull_request["closed_at"]`` when the PR is in the
      closed state; a merged PR missing ``closed_at`` gets
      ``closed_at = merged_at`` (merge implies closure).
    * ``ci_status_green`` — unknown from the event; spec 9.1 default True.
    * ``first_human_review_at`` / ``response_latency_hours`` /
      ``maintainer_review_comments`` — not present in PullRequestEvent;
      default None / None / 0.
    """
    if record.get("type") != "PullRequestEvent":
        return None
    payload = record.get("payload") or {}
    if payload.get("action") not in _MAPPED_PR_ACTIONS:
        return None

    repo_field = record.get("repo") or {}
    repo_slug = repo_field.get("name") or ""
    if "/" not in repo_slug:
        return None
    owner, name = repo_slug.split("/", 1)

    pr = payload.get("pull_request") or {}
    try:
        pr_number = int(pr.get("number", payload.get("number", 0)))
    except (TypeError, ValueError):
        return None
    if pr_number < 1:
        return None

    created_at = _parse_iso8601(pr.get("created_at"))
    if created_at is None:
        return None

    author_field = pr.get("user") or record.get("actor") or {}
    author_login = author_field.get("login")
    if not isinstance(author_login, str) or not author_login:
        return None

    merged_at = _parse_iso8601(pr.get("merged_at"))
    if merged_at is not None:
        closed_at = _parse_iso8601(pr.get("closed_at")) or merged_at
    elif pr.get("state") == "closed":
        closed_at = _parse_iso8601(pr.get("closed_at"))
    else:
        closed_at = None

    return RawPREvent(
        repo_owner=owner,
        repo_name=name,
        pr_number=pr_number,
        created_at=created_at,
        author_login=author_login,
        author_association=AuthorAssociation.NONE,
        author_prior_merged_commits=0,
        closed_at=closed_at,
        merged_at=merged_at,
        is_external=True,
        ci_status_green=True,
        first_human_review_at=None,
        response_latency_hours=None,
        maintainer_review_comments=0,
    )


class GHArchiveStreamer:
    """Fetches and decompresses GHArchive hourly partitions (spec 5, 8).

    ``client`` is any object exposing ``get(url) -> FetchResult`` — in
    production a :class:`CachedHttpClient`, in tests one wrapping a fake
    transport. The partition body is gunzipped when it starts with the
    gzip magic bytes (0x1f 0x8b); otherwise it is treated as already
    decompressed (httpx auto-decompresses responses carrying
    ``Content-Encoding: gzip``).
    """

    def __init__(self, client: CachedHttpClient) -> None:
        self._client = client

    def fetch_partition(self, when: datetime) -> bytes:
        """Download one hourly partition; return decompressed NDJSON bytes."""
        url = gharchive_hourly_url(when)
        result = self._client.get(url)
        if result.status_code not in (200, 304):
            raise IngestError(
                f"GHArchive partition fetch failed: HTTP {result.status_code} for {url}"
            )
        content = result.content
        if content[:2] == _GZIP_MAGIC:
            content = gzip.decompress(content)
        return content

    def stream_hourly_ndjson(self, when: datetime) -> Iterator[dict[str, Any]]:
        """Yield the NDJSON records of one hourly partition, in order."""
        for line in self.fetch_partition(when).splitlines():
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if isinstance(record, dict):
                yield record

    def stream_pr_events(
        self, when: datetime, owner: str, name: str
    ) -> list[RawPREvent]:
        """All :class:`RawPREvent` mapped from PR events of one repo in the hour.

        Non-PR records and PR events of other repositories are dropped;
        unmappable records are skipped (see :func:`map_pull_request_event`).
        """
        events: list[RawPREvent] = []
        for record in self.stream_hourly_ndjson(when):
            event = map_pull_request_event(record)
            if event is not None and event.repo_owner == owner and event.repo_name == name:
                events.append(event)
        return events


# --------------------------------------------------------------------------- #
# Raw-metric hydration + orchestration (spec 8, 11.2)
# --------------------------------------------------------------------------- #
def _enum_value(value: Any) -> str:
    """Store enums as their ``.value`` strings (lakehouse convention)."""
    return value.value if isinstance(value, Enum) else str(value)


def apply_raw_metrics(
    store: OperationalStore,
    owner: str,
    name: str,
    *,
    stars: int,
    forks: int,
    monthly_downloads: int = 0,
    dependents_count: int = 0,
    elephant_factor: int = 1,
    legal_ip_model: "LegalIPModel | str" = LegalIPModel.DCO,
    ai_posture: "AIPosture | str" = AIPosture.AD_HOC,
) -> int:
    """Additively update one repository's raw-metric columns (spec 11.2).

    Runs ``UPDATE repositories SET stars=?, forks=?, monthly_downloads=?,
    dependents_count=?, elephant_factor=?, legal_ip_model=?, ai_posture=?
    WHERE owner=? AND name=?`` via ``store.connection`` — the exact column
    names from the lakehouse ``CREATE TABLE repositories``. This is what
    makes ``query_matrix(min_stars=...)`` return rows after
    ``upsert_composite`` (which stores ``stars=0`` by default). Returns the
    number of rows updated (0 when the repo is not yet indexed).
    """
    sql = (
        "UPDATE repositories SET "
        "stars = ?, forks = ?, monthly_downloads = ?, dependents_count = ?, "
        "elephant_factor = ?, legal_ip_model = ?, ai_posture = ? "
        "WHERE owner = ? AND name = ?"
    )
    cursor = store.connection.execute(
        sql,
        (
            int(stars),
            int(forks),
            int(monthly_downloads),
            int(dependents_count),
            int(elephant_factor),
            _enum_value(legal_ip_model),
            _enum_value(ai_posture),
            owner,
            name,
        ),
    )
    store.connection.commit()
    return cursor.rowcount


def ingest_and_score(
    store: OperationalStore,
    meta: RepoMeta,
    pr_events: list[RawPREvent],
    now: datetime,
    raw_metrics: Optional[dict[str, Any]] = None,
    *,
    persona: Persona = Persona.BALANCED,
    ttm_p90_population: Optional[list[float]] = None,
    dependents_population: Optional[list[float]] = None,
    downloads_population: Optional[list[float]] = None,
    pillar_scorers: Optional[dict[str, Callable[..., float]]] = None,
    last_indexed_at: Optional[datetime] = None,
) -> int:
    """Score -> upsert -> hydrate raw metrics; return the rowid (spec 8).

    1. ``score_repository(meta, pr_events, now, ...)`` (spec 2, 7) — a
       Stage-0 gate failure is persisted like any other result.
    2. ``store.upsert_composite(result, pillar_scores=result.pillar_scores,
       ...)`` — raw-metric columns land at their documented defaults
       (``stars=0`` etc.).
    3. :func:`apply_raw_metrics` — overwrites those defaults with real
       values so matrix queries (``min_stars``) work.

    ``raw_metrics`` is the keyword-arguments dict for
    :func:`apply_raw_metrics` (must contain ``stars`` and ``forks``); when
    None the values are taken from ``meta``.
    """
    result = score_repository(
        meta,
        pr_events,
        now,
        persona=persona,
        ttm_p90_population=ttm_p90_population,
        dependents_population=dependents_population,
        downloads_population=downloads_population,
        pillar_scorers=pillar_scorers,
    )
    rowid = store.upsert_composite(
        result,
        pillar_scores=result.pillar_scores,
        last_indexed_at=last_indexed_at,
    )
    if raw_metrics is None:
        raw_metrics = {
            "stars": meta.stars,
            "forks": meta.forks,
            "monthly_downloads": meta.monthly_downloads,
            "dependents_count": meta.dependents_count,
            "elephant_factor": meta.elephant_factor,
            "legal_ip_model": meta.legal_ip_model,
            "ai_posture": meta.ai_posture,
        }
    apply_raw_metrics(store, meta.owner, meta.name, **raw_metrics)
    return rowid
