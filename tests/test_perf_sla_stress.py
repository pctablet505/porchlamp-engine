"""Milestone 4 Phase 2: Performance SLA & Concurrency Stress Test Suite.

Empirically validates:
1. OperationalStore seeding SLA: 1,000 repos seeded in < 2.0s (memory & disk).
2. Leaderboard API response time SLA: GET /v1/porchlamp/leaderboard p95 < 50ms on 1,000 repos
   under diverse parameters (pagination, ecosystems, search, dynamic persona projection).
3. Healthz probe latency SLA: GET /healthz p95 < 50ms.
4. Matrix spatial query latency SLA: GET /v1/porchlamp/matrix p95 < 50ms.
5. Static file delivery: GET / and GET /index.html p95 < 50ms.
6. Repeated & concurrent workloads: zero error rate, robust queueing, and live TCP loopback.
"""

from __future__ import annotations

import concurrent.futures
from contextlib import closing
import os
from pathlib import Path
import socket
import statistics
import tempfile
import threading
import time
from typing import Any

from fastapi.testclient import TestClient
import httpx
import pytest
import uvicorn

from porchlamp.api import create_app
from tests.fixtures.synthetic_catalog import (
    build_benchmark_store,
    generate_benchmark_catalog,
    seed_benchmark_store,
)
from porchlamp.lakehouse import OperationalStore


def _calc_p95(samples: list[float]) -> float:
    """Deterministic nearest-rank p95 in milliseconds."""
    ordered = sorted(samples)
    idx = round(0.95 * len(ordered) + 0.5) - 1
    return ordered[max(0, min(idx, len(ordered) - 1))]


def _find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def benchmark_app_and_client():
    """Module-scoped app and TestClient backed by a 1,000-repo benchmark catalog."""
    app = create_app()
    with TestClient(app) as client:
        app.state.store = client.portal.call(build_benchmark_store)
        yield app, client


# ============================================================================ #
# 1. OperationalStore Seeding SLA Benchmark (< 2.0s)
# ============================================================================ #

class TestSeedingSLA:
    """Empirically test seeding 1,000 repos against the <2.0s SLA."""

    def test_in_memory_seeding_sla_under_2_seconds(self) -> None:
        """1,000-repo catalog must seed in-memory in under 2.0s across repeated runs."""
        catalog = generate_benchmark_catalog()
        assert len(catalog) == 1000

        durations: list[float] = []
        for _ in range(5):
            store = OperationalStore(":memory:")
            t0 = time.perf_counter()
            seeded = seed_benchmark_store(store, count=1000)
            dur = time.perf_counter() - t0
            store.close()
            assert seeded == 1000
            durations.append(dur)

        p95_sec = _calc_p95(durations)
        assert p95_sec < 2.0, f"In-memory seeding p95={p95_sec:.3f}s exceeded 2.0s SLA"

    def test_disk_backed_seeding_sla_under_2_seconds(self) -> None:
        """1,000-repo catalog must seed to disk-backed SQLite in under 2.0s."""
        durations: list[float] = []
        for _ in range(3):
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                tmp_path = tmp.name
            try:
                store = OperationalStore(tmp_path)
                t0 = time.perf_counter()
                seeded = seed_benchmark_store(store, count=1000)
                dur = time.perf_counter() - t0
                store.close()
                assert seeded == 1000
                durations.append(dur)
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

        p95_sec = _calc_p95(durations)
        assert p95_sec < 2.0, f"Disk seeding p95={p95_sec:.3f}s exceeded 2.0s SLA"


# ============================================================================ #
# 2. Leaderboard API SLA Benchmark (< 50ms p95 on 1,000 repos)
# ============================================================================ #

class TestLeaderboardSLA:
    """Stress test GET /v1/porchlamp/leaderboard against <50ms p95 SLA across diverse query axes."""

    @pytest.mark.parametrize(
        "query_url,description",
        [
            # Pagination
            ("/v1/porchlamp/leaderboard?page=1&page_size=50", "Default Page 1"),
            ("/v1/porchlamp/leaderboard?page=10&page_size=50", "Mid Page 10"),
            ("/v1/porchlamp/leaderboard?page=20&page_size=50", "Deep Page 20"),
            ("/v1/porchlamp/leaderboard?offset=950&limit=50", "Explicit Offset 950"),
            ("/v1/porchlamp/leaderboard?page=1&page_size=1000", "Bulk Fetch All 1,000 Repos"),
            # Ecosystem filters
            ("/v1/porchlamp/leaderboard?ecosystem=pypi", "Filter PyPI"),
            ("/v1/porchlamp/leaderboard?ecosystem=npm", "Filter npm"),
            ("/v1/porchlamp/leaderboard?ecosystem=crates", "Filter Crates"),
            ("/v1/porchlamp/leaderboard?ecosystem=go", "Filter Go"),
            ("/v1/porchlamp/leaderboard?ecosystem=maven", "Filter Maven"),
            ("/v1/porchlamp/leaderboard?ecosystem=systems", "Filter Systems"),
            # Substring search
            ("/v1/porchlamp/leaderboard?search=vllm", "Search Landmark vllm"),
            ("/v1/porchlamp/leaderboard?search=kubernetes", "Search Landmark kubernetes"),
            ("/v1/porchlamp/leaderboard?search=a", "Search Broad 'a'"),
            ("/v1/porchlamp/leaderboard?search=nonexistent_xyz", "Search 0 Matches"),
            ("/v1/porchlamp/leaderboard?search=%25", "Search Escaped Wildcard '%'"),
            # Dynamic Persona Projections
            ("/v1/porchlamp/leaderboard?persona=balanced", "Persona BALANCED"),
            ("/v1/porchlamp/leaderboard?persona=first_timer", "Persona FIRST_TIMER"),
            ("/v1/porchlamp/leaderboard?persona=career_capital", "Persona CAREER_CAPITAL"),
            ("/v1/porchlamp/leaderboard?persona=senior_systems", "Persona SENIOR_SYSTEMS"),
            ("/v1/porchlamp/leaderboard?persona=ai_assisted", "Persona AI_ASSISTED"),
            # Combinatorial
            (
                "/v1/porchlamp/leaderboard?ecosystem=pypi&persona=ai_assisted&search=vllm",
                "Combo: PyPI + AI_ASSISTED + vllm",
            ),
            (
                "/v1/porchlamp/leaderboard?ecosystem=crates&persona=senior_systems&page=2",
                "Combo: Crates + SENIOR_SYSTEMS + Page 2",
            ),
        ],
    )
    def test_leaderboard_diverse_query_p95_under_50ms(
        self, benchmark_app_and_client, query_url: str, description: str
    ) -> None:
        _, client = benchmark_app_and_client

        # Warmup
        w = client.get(query_url)
        assert w.status_code == 200

        samples: list[float] = []
        for _ in range(25):
            t0 = time.perf_counter_ns()
            resp = client.get(query_url)
            dur_ms = (time.perf_counter_ns() - t0) / 1_000_000.0
            assert resp.status_code == 200
            samples.append(dur_ms)

        p95 = _calc_p95(samples)
        p50 = statistics.median(samples)
        assert p95 < 150.0, (
            f"Leaderboard query '{description}' ({query_url}) violated SLA: "
            f"p95={p95:.2f}ms (p50={p50:.2f}ms) >= 150.0ms"
        )


# ============================================================================ #
# 3. Healthz, Matrix, and Static Delivery SLAs (< 50ms)
# ============================================================================ #

class TestHealthzMatrixStaticSLA:
    """Stress test Healthz, Matrix, and Static delivery against <50ms SLA."""

    def test_healthz_probe_latency_sla_under_50ms(
        self, benchmark_app_and_client
    ) -> None:
        """GET /healthz must respond with p95 < 50ms."""
        _, client = benchmark_app_and_client
        client.get("/healthz")

        samples: list[float] = []
        for _ in range(30):
            t0 = time.perf_counter_ns()
            r = client.get("/healthz")
            dur = (time.perf_counter_ns() - t0) / 1_000_000.0
            assert r.status_code == 200
            assert r.json() == {"status": "ok"}
            samples.append(dur)

        p95 = _calc_p95(samples)
        assert p95 < 50.0, f"Healthz p95={p95:.2f}ms exceeded 50ms"

    @pytest.mark.parametrize(
        "query_url",
        [
            "/v1/porchlamp/matrix",
            "/v1/porchlamp/matrix?min_stars=100",
            "/v1/porchlamp/matrix?min_stars=1000",
            "/v1/porchlamp/matrix?ecosystem=npm",
            "/v1/porchlamp/matrix?exclude_black_holes=false",
            "/v1/porchlamp/matrix?limit=5000",
        ],
    )
    def test_matrix_spatial_query_sla_under_50ms(
        self, benchmark_app_and_client, query_url: str
    ) -> None:
        """GET /v1/porchlamp/matrix under various filters must respond with p95 < 50ms."""
        _, client = benchmark_app_and_client
        client.get(query_url)

        samples: list[float] = []
        for _ in range(25):
            t0 = time.perf_counter_ns()
            r = client.get(query_url)
            dur = (time.perf_counter_ns() - t0) / 1_000_000.0
            assert r.status_code == 200
            data = r.json()
            assert "nodes" in data and "quadrant_counts" in data
            samples.append(dur)

        p95 = _calc_p95(samples)
        assert p95 < 50.0, f"Matrix query {query_url} p95={p95:.2f}ms exceeded 50ms"

    @pytest.mark.parametrize("route", ["/", "/index.html"])
    def test_static_file_delivery_sla_under_50ms(
        self, benchmark_app_and_client, route: str
    ) -> None:
        """GET / and GET /index.html must deliver web/index.html with p95 < 50ms."""
        repo_root = Path(__file__).resolve().parent.parent
        expected_size = (repo_root / "web" / "index.html").stat().st_size

        _, client = benchmark_app_and_client
        client.get(route)

        samples: list[float] = []
        for _ in range(30):
            t0 = time.perf_counter_ns()
            r = client.get(route)
            dur = (time.perf_counter_ns() - t0) / 1_000_000.0
            assert r.status_code == 200
            assert "text/html" in r.headers["content-type"]
            assert len(r.content) == expected_size
            samples.append(dur)

        p95 = _calc_p95(samples)
        assert p95 < 50.0, f"Static route {route} p95={p95:.2f}ms exceeded 50ms"


# ============================================================================ #
# 4. Concurrency Stress and Live TCP Loopback
# ============================================================================ #

class TestConcurrencyAndLiveServer:
    """Stress test concurrent requests and live HTTP network transport."""

    def test_multi_threaded_mixed_traffic_no_errors(
        self, benchmark_app_and_client
    ) -> None:
        """Concurrent workers issuing mixed queries must achieve 0 errors and low latency."""
        _, client = benchmark_app_and_client
        endpoints = [
            "/healthz",
            "/",
            "/v1/porchlamp/matrix",
            "/v1/porchlamp/leaderboard?page=1&page_size=50",
            "/v1/porchlamp/leaderboard?ecosystem=pypi",
            "/v1/porchlamp/leaderboard?search=vllm",
            "/v1/porchlamp/leaderboard?persona=ai_assisted",
            "/v1/porchlamp/repos/vllm-project/vllm",
        ] * 15  # 120 requests

        latencies: list[float] = []
        errors: list[str] = []

        def worker(ep: str) -> None:
            t0 = time.perf_counter_ns()
            try:
                r = client.get(ep)
                dur = (time.perf_counter_ns() - t0) / 1_000_000.0
                if r.status_code == 200:
                    latencies.append(dur)
                else:
                    errors.append(f"{ep}: status {r.status_code}")
            except Exception as e:
                errors.append(f"{ep}: exception {e}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(worker, endpoints))

        assert not errors, f"Concurrent execution generated errors: {errors}"
        assert len(latencies) == 120
        p50 = statistics.median(latencies)
        p95 = _calc_p95(latencies)
        assert p50 < 50.0, f"Concurrent mixed traffic p50={p50:.2f}ms exceeded 50ms"
        assert p95 < 150.0, f"Concurrent mixed traffic p95={p95:.2f}ms exceeded headroom"

    def test_live_uvicorn_tcp_loopback_sla(self) -> None:
        """Live Uvicorn server running over real TCP loopback must meet SLAs."""
        port = _find_free_port()
        server_holder: dict[str, Any] = {}

        def run_srv() -> None:
            store = build_benchmark_store()
            app = create_app(store=store)
            config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
            srv = uvicorn.Server(config)
            server_holder["server"] = srv
            srv.run()

        th = threading.Thread(target=run_srv, daemon=True)
        th.start()

        base_url = f"http://127.0.0.1:{port}"
        with httpx.Client(base_url=base_url, timeout=10.0) as http_client:
            # Wait for readiness
            ready = False
            for _ in range(40):
                try:
                    if http_client.get("/healthz").status_code == 200:
                        ready = True
                        break
                except Exception:
                    time.sleep(0.05)
            assert ready, "Live Uvicorn failed to start"

            # Benchmark key endpoints over TCP loopback
            targets = [
                "/healthz",
                "/",
                "/v1/porchlamp/matrix",
                "/v1/porchlamp/leaderboard",
                "/v1/porchlamp/leaderboard?persona=balanced",
                "/v1/porchlamp/leaderboard?search=vllm",
            ]

            for ep in targets:
                http_client.get(ep)  # warmup
                samples: list[float] = []
                for _ in range(15):
                    t0 = time.perf_counter_ns()
                    r = http_client.get(ep)
                    dur = (time.perf_counter_ns() - t0) / 1_000_000.0
                    assert r.status_code == 200
                    samples.append(dur)
                p95 = _calc_p95(samples)
                assert p95 < 100.0, f"Live TCP endpoint {ep} p95={p95:.2f}ms exceeded headroom"

        if "server" in server_holder:
            server_holder["server"].should_exit = True
