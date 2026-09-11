"""Tests for the production serving entrypoint (scripts/serve.py).

The load-bearing property: serve.py reads an ALREADY-PROMOTED store and does
NOT seed synthetic benchmark_data. An empty DB must serve an empty dashboard
(not the ~1,000 fabricated Gemini fixtures). This locks the "only verified
data reaches the API" rule at the serving boundary.

sqlite3 connections are thread-bound and starlette's TestClient dispatches on
its portal thread, so the store is built inside ``client.portal.call`` (the
same pattern as ``tests/test_api.py::open_client``).
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from cvi.api import create_app
from cvi.lakehouse import OperationalStore
from cvi.models import CVICompositeResult, Ecosystem, PillarScores, Quadrant, Tier
from scripts.serve import count_repos, create_serve_app


def _result(name: str) -> CVICompositeResult:
    return CVICompositeResult(
        owner="tokio-rs",
        name=name,
        ecosystem=Ecosystem.CRATES,
        overall_cvi=70.31,
        rank_tier=Tier.A,
        matrix_quadrant=Quadrant.SWEET_SPOT,
        leverage_score=72.0,
        friction_score=30.0,
        is_stage0_disqualified=False,
        disqualification_reason=None,
    )


def open_serve_client(seed: Callable[[], OperationalStore]) -> TestClient:
    """Enter a TestClient whose app store is built on the portal thread."""
    app = create_serve_app(OperationalStore())
    client = TestClient(app)
    client.__enter__()
    app.state.store = client.portal.call(seed)
    return client


def _empty() -> OperationalStore:
    return OperationalStore()


def _promoted() -> OperationalStore:
    store = OperationalStore()
    store.upsert_composite(
        _result("tokio"),
        pillar_scores=PillarScores(70, 80, 60, 65, 55),
    )
    return store


def test_count_repos_empty_is_zero():
    # count_repos runs on the test thread (direct SQL, no portal needed).
    assert count_repos(OperationalStore()) == 0


def test_empty_store_serves_no_repos():
    # FAIL-BEFORE: if serve.py seeded benchmark_data, an empty store would
    # report ~1000 repos here. It must report zero — no fabrication.
    client = open_serve_client(_empty)
    try:
        lb = client.get("/v1/cvi/leaderboard?limit=1000").json()
        assert lb["items"] == []
        assert lb["total"] == 0
    finally:
        client.__exit__(None, None, None)


def test_promoted_row_is_served():
    client = open_serve_client(_promoted)
    try:
        lb = client.get("/v1/cvi/leaderboard?limit=1000").json()
        assert lb["total"] == 1
        assert lb["items"][0]["owner"] == "tokio-rs"
        assert lb["items"][0]["name"] == "tokio"
        assert lb["items"][0]["cvi"] == 70.31
    finally:
        client.__exit__(None, None, None)


def test_healthz_ok():
    client = TestClient(create_app(store=OperationalStore()))
    assert client.get("/healthz").json() == {"status": "ok"}
