"""Offline tests for the ingestion layer (cvi/ingest.py; spec 5, 8, 13).

No network: all HTTP flows through fake ``httpx.BaseTransport``
implementations, and all time flows through a :class:`FakeClock` whose
``advance`` doubles as the limiter/breaker sleep, keeping token-bucket and
circuit-breaker timing fully deterministic.
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta
from typing import Any, Callable

import httpx
import pytest

from cvi.ingest import (
    GITHUB_GRAPHQL_ENDPOINT,
    CachedHttpClient,
    CircuitBreaker,
    GHArchiveStreamer,
    IngestError,
    RateLimitedError,
    TokenBucketRateLimiter,
    apply_raw_metrics,
    gharchive_hourly_url,
    ingest_and_score,
    map_pull_request_event,
)
from cvi.lakehouse import OperationalStore
from cvi.models import (
    AIPosture,
    AuthorAssociation,
    CVICompositeResult,
    Ecosystem,
    LegalIPModel,
    Quadrant,
    RawPREvent,
    RepoMeta,
    Tier,
)


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
        clock = FakeClock(0.0)
        limiter = TokenBucketRateLimiter(
            rate=1.2, capacity=1.0, clock=clock, sleep=clock.advance
        )
        limiter.acquire()
        assert clock.now == 0.0

    def test_strict_1_2_per_second_without_burst(self):
        clock = FakeClock(0.0)
        limiter = TokenBucketRateLimiter(
            rate=1.2, capacity=1.0, clock=clock, sleep=clock.advance
        )
        limiter.acquire()
        limiter.acquire()
        assert clock.now == pytest.approx(1.0 / 1.2)
        limiter.acquire()
        assert clock.now == pytest.approx(2.0 / 1.2)

    def test_burst_allowed_up_to_capacity(self):
        clock = FakeClock(0.0)
        limiter = TokenBucketRateLimiter(
            rate=1.2, capacity=3.0, clock=clock, sleep=clock.advance
        )
        for _ in range(3):
            limiter.acquire()
        assert clock.now == 0.0
        limiter.acquire()
        assert clock.now == pytest.approx(1.0 / 1.2)

    def test_partial_refill_then_top_up(self):
        clock = FakeClock(0.0)
        limiter = TokenBucketRateLimiter(
            rate=1.2, capacity=1.0, clock=clock, sleep=clock.advance
        )
        limiter.acquire()
        clock.advance(0.5)  # 0.6 tokens refilled
        limiter.acquire()  # needs 0.4 more: 0.4 / 1.2 s
        assert clock.now == pytest.approx(0.5 + 0.4 / 1.2)

    def test_rejects_invalid_rate(self):
        with pytest.raises(ValueError):
            TokenBucketRateLimiter(rate=0.0)


# --------------------------------------------------------------------------- #
# CircuitBreaker
# --------------------------------------------------------------------------- #
class TestCircuitBreaker:
    def test_trip_holds_until_reset_epoch_plus_jitter(self):
        clock = FakeClock(1000.0)
        breaker = CircuitBreaker(clock=clock, sleep=clock.advance, jitter_fn=lambda: 0.5)
        open_until = breaker.trip(reset_epoch=1010.0)
        assert open_until == pytest.approx(1010.5)
        assert breaker.is_open
        breaker.acquire()
        assert clock.now == pytest.approx(1010.5)
        assert not breaker.is_open

    def test_reset_in_the_past_closes_immediately(self):
        clock = FakeClock(2000.0)
        breaker = CircuitBreaker(clock=clock, sleep=clock.advance, jitter_fn=lambda: 0.0)
        assert breaker.trip(reset_epoch=1900.0) == pytest.approx(2000.0)
        assert not breaker.is_open
        breaker.acquire()
        assert clock.now == 2000.0

    def test_missing_reset_uses_default_window(self):
        clock = FakeClock(0.0)
        breaker = CircuitBreaker(
            clock=clock,
            sleep=clock.advance,
            jitter_fn=lambda: 0.0,
            default_reset_seconds=60.0,
        )
        assert breaker.trip() == pytest.approx(60.0)
        assert breaker.trips == 1


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
    url = GITHUB_GRAPHQL_ENDPOINT

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
    url = GITHUB_GRAPHQL_ENDPOINT

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
    url = GITHUB_GRAPHQL_ENDPOINT

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


# --------------------------------------------------------------------------- #
# GHArchive mapping + streaming (spec 5, 8)
# --------------------------------------------------------------------------- #
def _pr_record(
    action: str,
    repo: str = "acme/widget",
    *,
    number: int = 7,
    author: str = "alice",
    state: str = "open",
    created_at: str = "2026-09-10T00:00:00Z",
    closed_at: Any = None,
    merged_at: Any = None,
) -> dict:
    return {
        "id": "rec-1",
        "type": "PullRequestEvent",
        "actor": {"id": 1, "login": author},
        "org": repo.split("/")[0],
        "repo": {"name": repo, "url": f"https://github.com/{repo}"},
        "public": False,
        "created_at": created_at,
        "payload": {
            "action": action,
            "number": number,
            "pull_request": {
                "number": number,
                "state": state,
                "user": {"login": author},
                "created_at": created_at,
                "closed_at": closed_at,
                "merged_at": merged_at,
            },
        },
    }


def test_map_pull_request_event_conservative_mapping():
    event = map_pull_request_event(_pr_record("opened"))
    assert isinstance(event, RawPREvent)
    assert event.repo_owner == "acme"
    assert event.repo_name == "widget"
    assert event.pr_number == 7
    assert event.created_at == datetime(2026, 9, 10, 0, 0, 0)
    assert event.author_login == "alice"
    assert event.author_association is AuthorAssociation.NONE
    assert event.author_prior_merged_commits == 0
    assert event.is_external is True
    assert event.closed_at is None
    assert event.merged_at is None
    assert event.first_human_review_at is None
    assert event.response_latency_hours is None
    assert event.maintainer_review_comments == 0
    assert event.ci_status_green is True


def test_map_merged_event_falls_back_to_merged_at_for_closed_at():
    record = _pr_record("merged", state="closed", merged_at="2026-09-10T05:00:00Z")
    event = map_pull_request_event(record)
    assert event.merged_at == datetime(2026, 9, 10, 5, 0, 0)
    assert event.closed_at == event.merged_at  # merge implies closure
    assert event.is_merged


def test_map_rejected_close_event():
    record = _pr_record(
        "closed",
        number=8,
        author="bob",
        state="closed",
        created_at="2026-09-09T00:00:00Z",
        closed_at="2026-09-10T01:00:00Z",
    )
    event = map_pull_request_event(record)
    assert event.closed_at == datetime(2026, 9, 10, 1, 0, 0)
    assert event.merged_at is None
    assert event.is_closed_unmerged


def test_map_pull_request_event_returns_none_for_noise_and_unmapped_actions():
    assert map_pull_request_event({"type": "CreateRepo", "payload": {}}) is None
    assert map_pull_request_event(_pr_record("labeled")) is None
    assert map_pull_request_event(_pr_record("edited")) is None
    malformed = _pr_record("opened")
    malformed["repo"] = {"name": "no-slash-here"}
    assert map_pull_request_event(malformed) is None
    missing_created = _pr_record("opened")
    missing_created["payload"]["pull_request"]["created_at"] = None
    assert map_pull_request_event(missing_created) is None


def test_gharchive_hourly_url_formatting():
    assert (
        gharchive_hourly_url(datetime(2026, 9, 10, 3))
        == "https://data.gharchive.org/2026/09/10/03.json.gz"
    )
    assert (
        gharchive_hourly_url(datetime(2026, 1, 5, 0))
        == "https://data.gharchive.org/2026/01/05/00.json.gz"
    )


def test_stream_pr_events_parses_gzipped_hourly_partition():
    records = [
        _pr_record("opened"),
        _pr_record(
            "merged",
            state="closed",
            merged_at="2026-09-10T05:00:00Z",
            closed_at="2026-09-10T05:00:00Z",
        ),
        _pr_record(
            "closed",
            number=8,
            author="bob",
            state="closed",
            created_at="2026-09-09T00:00:00Z",
            closed_at="2026-09-10T01:00:00Z",
        ),
        {"type": "CreateRepo", "payload": {}},  # noise
        _pr_record("opened", repo="other/lib", number=1),  # other repo
    ]
    ndjson = "\n".join(json.dumps(record) for record in records).encode("utf-8")
    gzipped = gzip.compress(ndjson)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://data.gharchive.org/2026/09/10/03.json.gz"
        return httpx.Response(
            200, content=gzipped, headers={"Content-Type": "application/x-gzip"}
        )

    client, clock, transport = make_client(handler, capacity=10.0)
    streamer = GHArchiveStreamer(client)
    events = streamer.stream_pr_events(datetime(2026, 9, 10, 3), owner="acme", name="widget")
    assert [event.pr_number for event in events] == [7, 7, 8]
    assert all(
        event.repo_owner == "acme" and event.repo_name == "widget" for event in events
    )
    assert not events[0].is_merged
    assert events[1].is_merged
    assert events[2].is_closed_unmerged


def test_fetch_partition_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"no partition")

    client, clock, transport = make_client(handler, capacity=10.0)
    streamer = GHArchiveStreamer(client)
    with pytest.raises(IngestError, match="HTTP 404"):
        streamer.fetch_partition(datetime(2026, 9, 10, 3))


# --------------------------------------------------------------------------- #
# apply_raw_metrics -> query_matrix round-trip (spec 11.2; handoff gotcha)
# --------------------------------------------------------------------------- #
def _composite(owner: str = "acme", name: str = "widget") -> CVICompositeResult:
    return CVICompositeResult(
        owner=owner,
        name=name,
        ecosystem=Ecosystem.PYPI,
        overall_cvi=72.5,
        rank_tier=Tier.A,
        matrix_quadrant=Quadrant.SWEET_SPOT,
        leverage_score=70.0,
        friction_score=30.0,
        is_stage0_disqualified=False,
    )


def test_apply_raw_metrics_hydrates_stars_for_matrix_queries():
    store = OperationalStore(":memory:")
    store.upsert_composite(_composite())
    assert store.query_matrix(min_stars=100) == []  # stars=0 until ingestion

    updated = apply_raw_metrics(store, "acme", "widget", stars=500, forks=25)
    assert updated == 1
    row = store.get_repo("acme", "widget")
    assert row["stars"] == 500
    assert row["forks"] == 25
    nodes = store.query_matrix(min_stars=100)
    assert [node["size"] for node in nodes] == [500]
    assert nodes[0]["owner"] == "acme"
    assert nodes[0]["name"] == "widget"
    assert store.query_matrix(min_stars=1000) == []
    store.close()


def test_apply_raw_metrics_stores_enum_values_as_strings():
    store = OperationalStore(":memory:")
    store.upsert_composite(_composite())
    apply_raw_metrics(
        store,
        "acme",
        "widget",
        stars=10,
        forks=1,
        monthly_downloads=4200,
        dependents_count=77,
        elephant_factor=3,
        legal_ip_model=LegalIPModel.FOUNDATION_CLA,
        ai_posture=AIPosture.PROSCRIBED,
    )
    row = store.get_repo("acme", "widget")
    assert row["monthly_downloads"] == 4200
    assert row["dependents_count"] == 77
    assert row["elephant_factor"] == 3
    assert row["legal_ip_model"] == "FOUNDATION_CLA"
    assert row["ai_posture"] == "PROSCRIBED"
    store.close()


def test_apply_raw_metrics_noop_for_unknown_repo():
    store = OperationalStore(":memory:")
    assert apply_raw_metrics(store, "ghost", "repo", stars=5, forks=0) == 0
    store.close()


# --------------------------------------------------------------------------- #
# ingest_and_score orchestrator (spec 8)
# --------------------------------------------------------------------------- #
_NOW = datetime(2026, 9, 10, 12, 0, 0)


def _smoke_meta(**overrides: Any) -> RepoMeta:
    base: dict[str, Any] = dict(
        owner="acme",
        name="widget",
        ecosystem=Ecosystem.PYPI,
        stars=500,
        forks=25,
        dependents_count=1200,
        active_nonbot_commits_180d=42,
        scorecard_score=9.0,
    )
    base.update(overrides)
    return RepoMeta(**base)


def _smoke_pr_events() -> list[RawPREvent]:
    return [
        RawPREvent(
            repo_owner="acme",
            repo_name="widget",
            pr_number=1,
            created_at=_NOW - timedelta(hours=3),
            author_login="alice",
            author_association=AuthorAssociation.FIRST_TIME,
            is_external=True,
            closed_at=_NOW - timedelta(hours=1),
            merged_at=_NOW - timedelta(hours=1),
            response_latency_hours=2.0,
        ),
    ]


def test_ingest_and_score_round_trip_min_stars():
    store = OperationalStore(":memory:")
    rowid = ingest_and_score(
        store,
        _smoke_meta(),
        _smoke_pr_events(),
        _NOW,
        {"stars": 500, "forks": 25, "dependents_count": 1200},
    )
    assert isinstance(rowid, int) and rowid >= 1
    row = store.get_repo("acme", "widget")
    assert row["stars"] == 500
    assert row["forks"] == 25
    assert row["dependents_count"] == 1200
    assert row["score_responsiveness"] > 0.0
    nodes = store.query_matrix(min_stars=100)
    assert [node["size"] for node in nodes] == [500]
    assert store.query_matrix(min_stars=1000) == []
    store.close()


def test_ingest_and_score_defaults_raw_metrics_from_meta():
    store = OperationalStore(":memory:")
    ingest_and_score(store, _smoke_meta(), _smoke_pr_events(), _NOW)
    row = store.get_repo("acme", "widget")
    assert row["stars"] == 500
    assert row["forks"] == 25
    assert row["dependents_count"] == 1200
    assert row["elephant_factor"] == 1
    assert row["legal_ip_model"] == "DCO"
    assert row["ai_posture"] == "AD_HOC"
    store.close()


def test_ingest_and_score_persists_stage0_gate_failure():
    store = OperationalStore(":memory:")
    meta = _smoke_meta(active_nonbot_commits_180d=0)  # fails gate 1
    ingest_and_score(store, meta, _smoke_pr_events(), _NOW, {"stars": 300, "forks": 2})
    row = store.get_repo("acme", "widget")
    assert row["is_stage0_disqualified"] == 1
    assert row["overall_cvi"] == 0.0
    assert row["rank_tier"] == "Tier D"
    assert row["stars"] == 300  # raw metrics still hydrated
    assert store.query_matrix(min_stars=100, exclude_black_holes=True) == []
    nodes = store.query_matrix(min_stars=100, exclude_black_holes=False)
    assert [node["is_gated"] for node in nodes] == [1]
    store.close()
