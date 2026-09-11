"""Ingestion layer for the CVI pipeline (spec 5, 8, 13; schema 11.2).

Wires the zero-cost GHArchive bulk stream and the delta-hydrated GitHub
path into the operational store:

* :class:`TokenBucketRateLimiter` — strictly 1.2 req/s client-side throttle
  (spec 13), with an injectable clock/sleep so tests are deterministic.
* :class:`CachedHttpClient` — wraps an ``httpx.Client`` (transport
  injectable) and adds conditional ETag fetches: URLs with a cached ETag
  send ``If-None-Match``; a ``304 Not Modified`` response consumes zero
  provider quota (spec 13) and is served from the local cache.
* :class:`CircuitBreaker` — on HTTP 403/429, pauses until the
  ``x-ratelimit-reset`` epoch plus a jittered span (spec 13).
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
- The token bucket charges exactly one token per *outgoing* request, with
  no burst by default (capacity = 1.0). GHArchive is free static hosting,
  so a streamer client may omit the limiter entirely; the limiter remains
  available for politeness.
- "Zero quota consumed" (spec 13) refers to the provider's rate-limit
  budget: a 304 response never increments
  :attr:`CachedHttpClient.quota_consumed`; only a fresh 200 does.
- ``map_pull_request_event`` is intentionally conservative: GHArchive
  ``PullRequestEvent`` payloads carry no author association, prior-commit
  counts, or review telemetry, so those fields default to the spec 9.1
  neutral values (``NONE`` / external, 0 prior commits, no review data).
- GHArchive timestamps are normalised to *naive UTC* datetimes, matching
  the convention used by the rest of the package.
- A merged PR whose payload omits ``closed_at`` gets
  ``closed_at = merged_at`` (merge implies closure; spec 9.1 requires
  ``merged_at <= closed_at``).
- The circuit breaker's jitter is a *span* added on top of the reset
  window; inject a constant ``jitter_fn`` in tests for determinism.
"""

from __future__ import annotations

import gzip
import json
import random
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Iterator, Mapping, Optional

import httpx

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
    "GITHUB_GRAPHQL_ENDPOINT",
    "GHARCHIVE_BASE_URL",
    "DEFAULT_RATE",
    "IngestError",
    "RateLimitedError",
    "TokenBucketRateLimiter",
    "ETagCache",
    "FetchResult",
    "CircuitBreaker",
    "CachedHttpClient",
    "gharchive_hourly_url",
    "map_pull_request_event",
    "GHArchiveStreamer",
    "apply_raw_metrics",
    "ingest_and_score",
]

GITHUB_GRAPHQL_ENDPOINT = "https://api.github.com/graphql"
GHARCHIVE_BASE_URL = "https://data.gharchive.org"

#: Spec 13: outgoing GitHub GraphQL requests are strictly throttled to
#: 1.2 req/s to preserve the 5,000 req/hour token budget.
DEFAULT_RATE: float = 1.2

#: Fallback pause when a 403/429 carries no ``x-ratelimit-reset``.
DEFAULT_RETRY_AFTER_SECONDS: float = 60.0

_RATE_LIMIT_STATUSES: tuple[int, ...] = (403, 429)
_GZIP_MAGIC: bytes = b"\x1f\x8b"

#: PullRequestEvent actions that carry a state transition worth mapping.
_MAPPED_PR_ACTIONS: frozenset[str] = frozenset({"opened", "closed", "reopened", "merged"})


class IngestError(Exception):
    """Ingestion failure (bad partition, malformed body, 304 w/o ETag...)."""


class RateLimitedError(Exception):
    """A conditional fetch hit HTTP 403/429 and tripped the breaker.

    ``retry_at`` is the absolute epoch (seconds) the circuit breaker is
    open until (reset epoch + jitter).
    """

    def __init__(self, status_code: int, retry_at: float) -> None:
        super().__init__(
            f"rate limited: HTTP {status_code}; circuit open until epoch {retry_at:.3f}"
        )
        self.status_code = status_code
        self.retry_at = retry_at


# --------------------------------------------------------------------------- #
# Token bucket (spec 13)
# --------------------------------------------------------------------------- #
class TokenBucketRateLimiter:
    """Classic token-bucket rate limiter, strictly ``rate`` req/s (spec 13).

    ``capacity`` tokens may be held at once (default 1.0 — no burst).
    ``clock`` and ``sleep`` are injectable for deterministic tests: pass a
    fake clock plus a ``sleep`` that advances it, and every :meth:`acquire`
    timing becomes exact without real waiting. (``sleep`` must advance the
    clock; a frozen clock with a no-op sleep would spin.)
    """

    def __init__(
        self,
        rate: float = DEFAULT_RATE,
        capacity: Optional[float] = None,
        clock: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        if rate <= 0.0:
            raise ValueError(f"rate must be > 0, got {rate!r}")
        if capacity is None:
            capacity = 1.0
        if capacity <= 0.0:
            raise ValueError(f"capacity must be > 0, got {capacity!r}")
        self._rate = float(rate)
        self._capacity = float(capacity)
        self._clock = clock if clock is not None else time.monotonic
        self._sleep = sleep if sleep is not None else time.sleep
        self._tokens = self._capacity
        self._refilled_at = self._clock()

    @property
    def rate(self) -> float:
        """Tokens refilled per second."""
        return self._rate

    @property
    def capacity(self) -> float:
        """Maximum burst size."""
        return self._capacity

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._refilled_at
        if elapsed > 0.0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._refilled_at = now

    def acquire(self) -> None:
        """Consume one token, blocking (via ``sleep``) until one is available."""
        while True:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                self._refilled_at = self._clock()
                return
            wait = (1.0 - self._tokens) / self._rate
            self._sleep(wait)


# --------------------------------------------------------------------------- #
# ETag cache + conditional HTTP client (spec 13)
# --------------------------------------------------------------------------- #
class ETagCache:
    """Per-URL conditional-fetch cache: ``url -> (etag, body)`` (spec 13)."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, bytes]] = {}

    def get(self, url: str) -> Optional[tuple[str, bytes]]:
        """Return ``(etag, body)`` for ``url``, or None when uncached."""
        return self._entries.get(url)

    def etag(self, url: str) -> Optional[str]:
        entry = self._entries.get(url)
        return entry[0] if entry else None

    def put(self, url: str, etag: str, body: bytes) -> None:
        self._entries[url] = (etag, body)

    def __len__(self) -> int:
        return len(self._entries)


@dataclass
class FetchResult:
    """Outcome of one conditional fetch.

    ``from_cache`` is True for 304 responses, whose ``content`` is the
    previously cached body (zero provider quota consumed, spec 13).
    """

    status_code: int
    content: bytes
    headers: dict[str, str]
    from_cache: bool = False


class CircuitBreaker:
    """Pauses outgoing requests on HTTP 403/429 until rate-limit reset.

    :meth:`trip` computes ``open_until = now + max(0, reset_epoch - now)
    + jitter`` where ``jitter`` comes from ``jitter_fn`` (default: uniform
    [0, 1) seconds — the spec's "jittered backoff" on top of the reset
    window). :meth:`acquire` blocks (via injectable ``sleep``) while the
    breaker is open, then closes it. ``clock``/``sleep``/``jitter_fn`` are
    injectable for deterministic tests.
    """

    def __init__(
        self,
        clock: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], None]] = None,
        jitter_fn: Optional[Callable[[], float]] = None,
        default_reset_seconds: float = DEFAULT_RETRY_AFTER_SECONDS,
    ) -> None:
        self._clock = clock if clock is not None else time.monotonic
        self._sleep = sleep if sleep is not None else time.sleep
        self._jitter_fn = (
            jitter_fn if jitter_fn is not None else lambda: random.uniform(0.0, 1.0)
        )
        self._default_reset_seconds = float(default_reset_seconds)
        self._open_until: Optional[float] = None
        self.trips = 0

    @property
    def open_until(self) -> Optional[float]:
        """Epoch the breaker is open until, or None when closed."""
        if self._open_until is not None and self._clock() < self._open_until:
            return self._open_until
        return None

    @property
    def is_open(self) -> bool:
        return self.open_until is not None

    def trip(self, reset_epoch: Optional[float] = None) -> float:
        """Open the breaker until ``reset_epoch`` (default: now + fallback)."""
        now = self._clock()
        if reset_epoch is None:
            reset_epoch = now + self._default_reset_seconds
        wait = max(0.0, float(reset_epoch) - now)
        self._open_until = now + wait + float(self._jitter_fn())
        self.trips += 1
        return self._open_until

    def acquire(self) -> None:
        """Block until the breaker is closed; no-op when already closed."""
        while True:
            open_until = self.open_until
            if open_until is None:
                return
            remaining = open_until - self._clock()
            if remaining > 0.0:
                self._sleep(remaining)


def _parse_reset_epoch(headers: Mapping[str, Any]) -> Optional[float]:
    """Read ``x-ratelimit-reset`` (or ``retry-after``) as an epoch, else None."""
    lowered = {str(key).lower(): value for key, value in headers.items()}
    for key in ("x-ratelimit-reset", "retry-after"):
        raw = lowered.get(key)
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    return None


class CachedHttpClient:
    """``httpx.Client`` wrapper: token bucket + ETag delta hydration + breaker.

    * Every GET consults the ETag cache first; a cached URL adds
      ``If-None-Match`` to the outgoing headers (spec 13).
    * The token bucket charges exactly one token per outgoing request.
      A ``304 Not Modified`` consumes **zero** provider quota —
      :attr:`quota_consumed` increments only on fresh 200 responses.
    * HTTP 403/429 trips the circuit breaker (until
      ``x-ratelimit-reset`` + jitter) and raises :class:`RateLimitedError`.

    ``transport`` is injectable (tests pass a fake
    ``httpx.BaseTransport`` so no network I/O happens). Any GET-like
    endpoint works: GitHub GraphQL *and* static GHArchive partitions.
    """

    def __init__(
        self,
        transport: Optional[httpx.BaseTransport] = None,
        rate_limiter: Optional[TokenBucketRateLimiter] = None,
        etag_cache: Optional[ETagCache] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> None:
        self._client = httpx.Client(transport=transport)
        self._limiter = (
            rate_limiter if rate_limiter is not None else TokenBucketRateLimiter()
        )
        self._etags = etag_cache if etag_cache is not None else ETagCache()
        self._breaker = (
            circuit_breaker if circuit_breaker is not None else CircuitBreaker()
        )
        self._base_headers: dict[str, str] = dict(headers or {})
        #: Responses that consumed provider quota (fresh 200s). 304s are
        #: zero-quota by definition (spec 13).
        self.quota_consumed = 0

    @property
    def rate_limiter(self) -> TokenBucketRateLimiter:
        return self._limiter

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        return self._breaker

    @property
    def etag_cache(self) -> ETagCache:
        return self._etags

    def get(self, url: str, headers: Optional[dict[str, str]] = None) -> FetchResult:
        """One conditional GET through limiter -> breaker -> transport."""
        request_headers = {**self._base_headers, **(headers or {})}
        cached = self._etags.get(url)
        if cached is not None:
            request_headers["If-None-Match"] = cached[0]

        self._limiter.acquire()
        self._breaker.acquire()

        response = self._client.request("GET", url, headers=request_headers)
        status = response.status_code

        if status in _RATE_LIMIT_STATUSES:
            retry_at = self._breaker.trip(_parse_reset_epoch(response.headers))
            raise RateLimitedError(status, retry_at)

        if status == 304:
            if cached is None:
                raise IngestError(f"HTTP 304 for {url} without a cached ETag")
            return FetchResult(
                status_code=304,
                content=cached[1],
                headers=dict(response.headers),
                from_cache=True,
            )

        if status == 200:
            self.quota_consumed += 1
            etag = response.headers.get("ETag")
            if etag:
                self._etags.put(url, etag, response.content)

        return FetchResult(
            status_code=status,
            content=response.content,
            headers=dict(response.headers),
            from_cache=False,
        )


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
        if result.status_code != 200:
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
