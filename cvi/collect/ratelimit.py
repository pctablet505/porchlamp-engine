"""Rate limiting, ETag delta-hydration, and circuit breaking for outgoing
HTTP collectors (spec 13).

Moved verbatim from ``cvi/ingest.py`` (W4-1, `04-ARCHITECTURE.md` section 2:
"``collect/ratelimit.py``: token bucket, ETag cache, circuit breaker (port
from ``ingest.py``, then delete it)"). No behaviour changed by the move —
same classes, same docstrings, same messages.

* :class:`TokenBucketRateLimiter` -- strictly 1.2 req/s client-side throttle
  (spec 13), with an injectable clock/sleep so tests are deterministic.
* :class:`CachedHttpClient` -- wraps an ``httpx.Client`` (transport
  injectable) and adds conditional ETag fetches: URLs with a cached ETag
  send ``If-None-Match``; a ``304 Not Modified`` response consumes zero
  provider quota (spec 13) and is served from the local cache.
* :class:`CircuitBreaker` -- on HTTP 403/429, pauses until the
  ``x-ratelimit-reset`` epoch plus a jittered span (spec 13).

Design notes / documented decisions
-----------------------------------
- The token bucket charges exactly one token per *outgoing* request, with
  no burst by default (capacity = 1.0).
- "Zero quota consumed" (spec 13) refers to the provider's rate-limit
  budget: a 304 response never increments
  :attr:`CachedHttpClient.quota_consumed`; only a fresh 200 does.
- The circuit breaker's jitter is a *span* added on top of the reset
  window; inject a constant ``jitter_fn`` in tests for determinism.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Any, Optional

import httpx

__all__ = [
    "IngestError",
    "RateLimitedError",
    "TokenBucketRateLimiter",
    "ETagCache",
    "FetchResult",
    "CircuitBreaker",
    "CachedHttpClient",
]

#: Spec 13: outgoing GitHub GraphQL requests are strictly throttled to
#: 1.2 req/s to preserve the 5,000 req/hour token budget.
DEFAULT_RATE: float = 1.2

#: Fallback pause when a 403/429 carries no ``x-ratelimit-reset``.
DEFAULT_RETRY_AFTER_SECONDS: float = 60.0

_RATE_LIMIT_STATUSES: tuple[int, ...] = (403, 429)


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
    endpoint works: GitHub GraphQL *and* static partitions.
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
