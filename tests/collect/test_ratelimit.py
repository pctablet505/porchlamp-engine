"""Offline tests for the rate limiter / ETag cache / circuit breaker
(``porchlamp/collect/ratelimit.py``; spec 13).

No network: all HTTP flows through fake ``httpx.BaseTransport``
implementations, and all time flows through a :class:`FakeClock` whose
``advance`` doubles as the limiter/breaker sleep, keeping token-bucket and
circuit-breaker timing fully deterministic.
"""

from __future__ import annotations

from typing import Callable

import httpx
import pytest

from porchlamp.collect.ratelimit import (
    CachedHttpClient,
    CircuitBreaker,
    ETagCache,
    IngestError,
    RateLimitedError,
    TokenBucketRateLimiter,
    _parse_reset_epoch,
)

#: Any GET-like URL works for these fakes -- reused from the old
#: ``porchlamp.ingest.GITHUB_GRAPHQL_ENDPOINT`` constant, inlined since
#: ``ratelimit.py`` is a generic HTTP-primitives module with no
#: GitHub-specific constants of its own.
_SOME_URL = "https://api.github.com/graphql"


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeClock:
    """Callable clock; ``advance`` is used as the sleep, so acquires are exact."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class FakeTransport(httpx.BaseTransport):
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    start: float = 0.0,
    rate: float = 1.2,
    capacity: float = 1.0,
    jitter: float = 0.0,
):
    """Build a CachedHttpClient wired to fakes; return (client, clock, transport)."""
    clock = FakeClock(start)
    transport = FakeTransport(handler)
    limiter = TokenBucketRateLimiter(
        rate=rate, capacity=capacity, clock=clock, sleep=clock.advance
    )
    breaker = CircuitBreaker(clock=clock, sleep=clock.advance, jitter_fn=lambda: jitter)
    client = CachedHttpClient(
        transport=transport, rate_limiter=limiter, circuit_breaker=breaker
    )
    return client, clock, transport


# --------------------------------------------------------------------------- #
# TokenBucketRateLimiter (spec 13: 1.2 req/s)
# --------------------------------------------------------------------------- #
class TestTokenBucketRateLimiter:
    def test_first_acquire_is_immediate(self):
        clock = FakeClock()
        limiter = TokenBucketRateLimiter(rate=1.2, clock=clock, sleep=clock.advance)
        limiter.acquire()
        assert clock.now == 0.0

    def test_strict_1_2_per_second_without_burst(self):
        clock = FakeClock()
        limiter = TokenBucketRateLimiter(rate=1.2, clock=clock, sleep=clock.advance)
        limiter.acquire()
        limiter.acquire()
        assert clock.now == pytest.approx(1.0 / 1.2)
        limiter.acquire()
        assert clock.now == pytest.approx(2.0 / 1.2)

    def test_burst_allowed_up_to_capacity(self):
        clock = FakeClock()
        limiter = TokenBucketRateLimiter(
            rate=1.2, capacity=3.0, clock=clock, sleep=clock.advance
        )
        limiter.acquire()
        limiter.acquire()
        limiter.acquire()
        assert clock.now == 0.0  # 3 tokens available immediately (burst)
        limiter.acquire()
        assert clock.now == pytest.approx(1.0 / 1.2)

    def test_partial_refill_then_top_up(self):
        clock = FakeClock()
        limiter = TokenBucketRateLimiter(
            rate=1.2, capacity=2.0, clock=clock, sleep=clock.advance
        )
        limiter.acquire()
        limiter.acquire()
        assert clock.now == 0.0
        clock.advance(0.5 / 1.2)  # half a token refilled
        limiter.acquire()
        assert clock.now == pytest.approx(1.0 / 1.2)

    def test_rejects_invalid_rate(self):
        with pytest.raises(ValueError, match="rate must be > 0"):
            TokenBucketRateLimiter(rate=0.0)
        with pytest.raises(ValueError, match="rate must be > 0"):
            TokenBucketRateLimiter(rate=-1.0)

    def test_default_capacity_and_properties(self):
        limiter = TokenBucketRateLimiter()
        assert limiter.rate == 1.2
        assert limiter.capacity == 1.0

    def test_rejects_non_positive_capacity(self):
        with pytest.raises(ValueError, match="capacity must be > 0"):
            TokenBucketRateLimiter(capacity=0.0)
        with pytest.raises(ValueError, match="capacity must be > 0"):
            TokenBucketRateLimiter(rate=1.0, capacity=-0.5)

    def test_custom_rate_and_capacity_properties(self):
        limiter = TokenBucketRateLimiter(rate=5.0, capacity=10.0)
        assert limiter.rate == 5.0
        assert limiter.capacity == 10.0


# --------------------------------------------------------------------------- #
# CircuitBreaker
# --------------------------------------------------------------------------- #
class TestCircuitBreaker:
    def test_trip_holds_until_reset_epoch_plus_jitter(self):
        clock = FakeClock(start=1000.0)
        breaker = CircuitBreaker(
            clock=clock, sleep=clock.advance, jitter_fn=lambda: 2.0
        )
        open_until = breaker.trip(reset_epoch=1050.0)
        assert open_until == pytest.approx(1052.0)
        assert breaker.is_open
        breaker.acquire()
        assert clock.now == pytest.approx(1052.0)
        assert not breaker.is_open
        assert breaker.trips == 1

    def test_reset_in_the_past_closes_immediately(self):
        clock = FakeClock(start=1000.0)
        breaker = CircuitBreaker(clock=clock, sleep=clock.advance, jitter_fn=lambda: 0.0)
        assert breaker.trip(reset_epoch=500.0) == pytest.approx(1000.0)  # already passed
        assert not breaker.is_open
        breaker.acquire()
        assert clock.now == 1000.0

    def test_missing_reset_uses_default_window(self):
        clock = FakeClock(start=1000.0)
        breaker = CircuitBreaker(
            clock=clock, sleep=clock.advance, jitter_fn=lambda: 0.0,
            default_reset_seconds=30.0,
        )
        open_until = breaker.trip(reset_epoch=None)
        assert open_until == pytest.approx(1030.0)
        assert breaker.trips == 1

    def test_acquire_no_sleep_when_clock_already_passed_open_until(self):
        clock = FakeClock(start=1000.0)
        calls = []
        breaker = CircuitBreaker(
            clock=clock, sleep=lambda s: calls.append(s), jitter_fn=lambda: 0.0
        )
        breaker.trip(reset_epoch=1005.0)
        clock.advance(10.0)  # jump past open_until without going through sleep
        breaker.acquire()
        assert calls == []
        assert not breaker.is_open


# --------------------------------------------------------------------------- #
# ETag / 304 semantics (spec 13)
# --------------------------------------------------------------------------- #
def test_conditional_etag_fetch_304_consumes_zero_quota():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("if-none-match") == "v1":
            return httpx.Response(304, headers={"ETag": "v1"})
        return httpx.Response(
            200, content=b'{"data":{"v":1}}', headers={"ETag": "v1"}
        )

    client, clock, transport = make_client(handler, capacity=10.0)
    url = _SOME_URL

    first = client.get(url)
    assert first.status_code == 200
    assert first.from_cache is False
    assert client.quota_consumed == 1
    assert "if-none-match" not in transport.requests[0].headers

    second = client.get(url)
    assert second.status_code == 304
    assert second.from_cache is True
    assert second.content == first.content == b'{"data":{"v":1}}'
    assert client.quota_consumed == 1  # 304 -> zero quota
    assert transport.requests[1].headers["if-none-match"] == "v1"


def test_etag_cache_follows_server_side_invalidations():
    server = {"etag": "a"}
    bodies = {"a": b'{"v": 1}', "b": b'{"v": 2}'}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("if-none-match") == server["etag"]:
            return httpx.Response(304, headers={"ETag": server["etag"]})
        return httpx.Response(
            200, content=bodies[server["etag"]], headers={"ETag": server["etag"]}
        )

    client, clock, transport = make_client(handler, capacity=10.0)
    url = _SOME_URL

    first = client.get(url)
    assert first.status_code == 200 and first.content == b'{"v": 1}'
    assert client.quota_consumed == 1

    server["etag"] = "b"  # server-side change invalidates the cached ETag
    second = client.get(url)
    assert second.status_code == 200 and second.content == b'{"v": 2}'
    assert client.quota_consumed == 2
    assert transport.requests[1].headers["if-none-match"] == "a"

    third = client.get(url)
    assert third.status_code == 304 and third.from_cache
    assert third.content == b'{"v": 2}'
    assert client.quota_consumed == 2
    assert transport.requests[2].headers["if-none-match"] == "b"


# --------------------------------------------------------------------------- #
# Circuit breaker through the client: 403/429 -> pause until reset
# --------------------------------------------------------------------------- #
def test_circuit_breaker_blocks_until_ratelimit_reset_then_succeeds():
    start = 1000.0
    reset_epoch = start + 50.0
    responses = [
        httpx.Response(429, headers={"X-Ratelimit-Reset": str(reset_epoch)}),
        httpx.Response(429, headers={"X-Ratelimit-Reset": str(reset_epoch)}),
        httpx.Response(200, content=b"ok", headers={"ETag": "e1"}),
    ]
    iterator = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        return next(iterator)

    client, clock, transport = make_client(handler, start=start, capacity=10.0, jitter=0.5)
    url = _SOME_URL

    with pytest.raises(RateLimitedError) as exc_info:
        client.get(url)
    assert exc_info.value.status_code == 429
    # open until start + 50 (reset) + 0.5 (jitter)
    assert exc_info.value.retry_at == pytest.approx(start + 50.5)
    assert client.circuit_breaker.is_open

    with pytest.raises(RateLimitedError):
        client.get(url)  # request #2: delayed to 1050.5, still 429 -> re-trips to 1051.0

    # request #3: blocked until 1051.0, then succeeds after reset
    result = client.get(url)
    assert result.status_code == 200
    assert result.content == b"ok"
    assert clock.now == pytest.approx(start + 51.0)
    assert client.circuit_breaker.trips == 2
    assert not client.circuit_breaker.is_open
    assert len(transport.requests) == 3


def test_etag_cache_etag_lookup_and_len():
    cache = ETagCache()
    assert cache.etag("https://example.com/unseen") is None
    assert len(cache) == 0
    cache.put("https://example.com/seen", '"abc"', b"content")
    assert cache.etag("https://example.com/seen") == '"abc"'
    assert len(cache) == 1


def test_cached_http_client_properties_and_304_without_etag():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(304)

    client, clock, transport = make_client(handler, capacity=5.0)
    assert isinstance(client.rate_limiter, TokenBucketRateLimiter)
    assert isinstance(client.circuit_breaker, CircuitBreaker)
    assert isinstance(client.etag_cache, ETagCache)

    with pytest.raises(IngestError, match="without a cached ETag"):
        client.get("https://example.com/item")


def test_parse_reset_epoch_various_headers():
    # retry-after used when x-ratelimit-reset is missing (hits continue on missing x-ratelimit-reset)
    assert _parse_reset_epoch({"Retry-After": "123.45"}) == 123.45
    # missing headers return None
    assert _parse_reset_epoch({}) is None
    assert _parse_reset_epoch({"Other-Header": "value"}) is None
    # invalid headers return None (TypeError / ValueError)
    assert _parse_reset_epoch({"X-RateLimit-Reset": "invalid"}) is None
    assert _parse_reset_epoch({"Retry-After": "not-a-number"}) is None
    assert _parse_reset_epoch({"X-RateLimit-Reset": None}) is None
    assert _parse_reset_epoch({"X-RateLimit-Reset": object()}) is None


def test_cached_http_client_429_with_retry_after_and_invalid_reset_headers():
    # 429 with Retry-After header
    client1, _, _ = make_client(
        lambda req: httpx.Response(429, headers={"Retry-After": "1234.5"}),
        start=1000.0,
        jitter=0.0,
    )
    with pytest.raises(RateLimitedError) as exc1:
        client1.get("https://example.com/api")
    assert exc1.value.retry_at == 1234.5

    # 429 with invalid reset header falls back to default reset seconds
    client2, _, _ = make_client(
        lambda req: httpx.Response(429, headers={"X-RateLimit-Reset": "corrupt"}),
        start=1000.0,
        jitter=0.0,
    )
    with pytest.raises(RateLimitedError) as exc2:
        client2.get("https://example.com/api")
    assert exc2.value.retry_at == 1060.0  # 1000 + default 60s
