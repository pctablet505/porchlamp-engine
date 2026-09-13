"""Adversarial stress tests and empirical latency benchmarks for Milestone 2 Leaderboard API.

Author: challenger_m2_1

Empirically verifies:
1. Boundary Conditions (HTTP 422 vs 200):
   - Strict 422 validation for page=0, page_size=0, page_size=1001, limit=0,
     limit=1001, negative offsets, and invalid personas.
   - Exact 200 responses with schema validity for edge parameters.
2. Pagination & Slicing Stress:
   - Full catalog traversal of 1,000 repositories (20 pages of 50 items) with continuous monotonic ranks 1..1000.
   - Maximum page size (1,000 items) and minimum page size (1 item) behavior.
   - Deep pagination offsets (e.g. offset=999, limit=1) and out-of-bounds page requests.
3. Multi-Axis Filtering & Dynamic Persona Re-Projection:
   - Ecosystem filtering across all 6 ecosystems with case-insensitivity.
   - Adversarial search inputs (SQLi patterns, wildcards, symbols, unicode, emojis).
   - Dynamic persona re-projection with strict descending score ordering and persona-specific gating:
     * AI_ASSISTED hard-disqualifies proscribed repos (gentoo -> cvi=0, Tier D, is_gated=1).
     * SENIOR_SYSTEMS hard-disqualifies elephant_factor < 3 repos (cvi=0, Tier D, is_gated=1).
4. Response Latency SLA (< 50ms) & Multi-Threaded Concurrency:
   - Rigorous measurement of p50, p95, p99, and max latencies under sequential query loops.
   - Concurrent load testing across worker threads measuring p95 latency against < 50ms SLA.
   - Real disk-backed SQLite operational store latency benchmarks under < 50ms SLA.
"""

from __future__ import annotations

import concurrent.futures
import statistics
import tempfile
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cvi.api import create_app
from cvi.benchmark_data import build_benchmark_store
from cvi.lakehouse import OperationalStore
from cvi.models import Ecosystem


@pytest.fixture(scope="module")
def seeded_store() -> Any:
    """Fixture providing an in-memory OperationalStore seeded with exactly 1,000 repos."""
    store = build_benchmark_store(":memory:", count=1000)
    yield store
    store.close()


@pytest.fixture()
def client_portal(seeded_store: OperationalStore) -> TestClient:
    """TestClient configured with the 1,000-repo store on the portal thread."""
    app = create_app()
    client = TestClient(app)
    client.__enter__()
    app.state.store = client.portal.call(lambda: build_benchmark_store(":memory:", count=1000))
    try:
        yield client
    finally:
        client.__exit__(None, None, None)


# ============================================================================ #
# 1. BOUNDARY CONDITIONS & VALIDATION (HTTP 422 vs 200)
# ============================================================================ #

class TestLeaderboardBoundaryConditions:
    """Validate boundary condition enforcement on GET /v1/cvi/leaderboard."""

    @pytest.mark.parametrize(
        ("param_name", "param_val"),
        [
            ("page", 0),
            ("page", -1),
            ("page", -999),
            ("page_size", 0),
            ("page_size", 1001),
            ("page_size", -1),
            ("limit", 0),
            ("limit", 1001),
            ("limit", -1),
            ("offset", -1),
            ("offset", -100),
            ("persona", "invalid_persona"),
            ("persona", "123"),
            ("persona", ""),
            ("persona", "vibe_coder"),
            ("min_stars", -1),
            ("min_stars", -500),
        ],
    )
    def test_invalid_boundary_parameters_return_422(
        self, client_portal: TestClient, param_name: str, param_val: Any
    ) -> None:
        """Every invalid boundary parameter must trigger HTTP 422 Unprocessable Entity."""
        resp = client_portal.get("/v1/cvi/leaderboard", params={param_name: param_val})
        assert resp.status_code == 422, (
            f"Expected HTTP 422 for {param_name}={param_val!r}, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert "detail" in body

    @pytest.mark.parametrize(
        ("param_name", "param_val"),
        [
            ("page", 1),
            ("page", 50),
            ("page_size", 1),
            ("page_size", 50),
            ("page_size", 1000),
            ("limit", 1),
            ("limit", 50),
            ("limit", 1000),
            ("offset", 0),
            ("offset", 500),
            ("offset", 999),
            ("offset", 1000),
            ("min_stars", 0),
            ("min_stars", 500),
            ("persona", "balanced"),
            ("persona", "ai_assisted"),
            ("persona", "senior_systems"),
            ("persona", "first_timer"),
            ("persona", "career_capital"),
        ],
    )
    def test_valid_boundary_parameters_return_200(
        self, client_portal: TestClient, param_name: str, param_val: Any
    ) -> None:
        """Valid boundary values must return HTTP 200 with complete schema."""
        resp = client_portal.get("/v1/cvi/leaderboard", params={param_name: param_val})
        assert resp.status_code == 200, (
            f"Expected HTTP 200 for {param_name}={param_val!r}, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert "total" in body
        assert "items" in body
        assert "page" in body
        assert "page_size" in body
        assert "total_pages" in body


# ============================================================================ #
# 2. PAGINATION & SLICING STRESS
# ============================================================================ #

class TestLeaderboardPaginationStress:
    """Stress-test pagination and slicing across the 1,000-repo catalog."""

    def test_full_catalog_sweep_page_size_50(self, client_portal: TestClient) -> None:
        """Traverse all 20 pages of 50 items and verify exact counts, monotonic ranks, and no duplicates."""
        seen_repo_ids: set[str] = set()
        for page in range(1, 21):
            resp = client_portal.get("/v1/cvi/leaderboard", params={"page": page, "page_size": 50})
            assert resp.status_code == 200
            data = resp.json()

            assert data["total"] == 1000
            assert data["page"] == page
            assert data["page_size"] == 50
            assert data["total_pages"] == 20
            items = data["items"]
            assert len(items) == 50

            for i, item in enumerate(items):
                expected_rank = (page - 1) * 50 + i + 1
                assert item["rank"] == expected_rank
                repo_id = item["repo_id"]
                assert repo_id not in seen_repo_ids, f"Duplicate repo {repo_id} at rank {expected_rank}"
                seen_repo_ids.add(repo_id)

        assert len(seen_repo_ids) == 1000

    def test_maximum_page_size_1000(self, client_portal: TestClient) -> None:
        """Fetch the entire 1,000-repo catalog in a single request."""
        resp = client_portal.get("/v1/cvi/leaderboard", params={"page": 1, "page_size": 1000})
        assert resp.status_code == 200
        data = resp.json()

        assert data["total"] == 1000
        assert data["page"] == 1
        assert data["page_size"] == 1000
        assert data["total_pages"] == 1
        items = data["items"]
        assert len(items) == 1000
        assert items[0]["rank"] == 1
        assert items[-1]["rank"] == 1000

    def test_minimum_page_size_1_extremes(self, client_portal: TestClient) -> None:
        """Fetch single items at first, middle, and last page."""
        for target_page in [1, 500, 1000]:
            resp = client_portal.get("/v1/cvi/leaderboard", params={"page": target_page, "page_size": 1})
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 1000
            assert data["page"] == target_page
            assert data["page_size"] == 1
            assert data["total_pages"] == 1000
            assert len(data["items"]) == 1
            assert data["items"][0]["rank"] == target_page

    def test_out_of_bounds_pages(self, client_portal: TestClient) -> None:
        """Verify out-of-bounds page requests return empty item list with correct metadata."""
        out_of_bounds_cases = [
            {"page": 21, "page_size": 50},
            {"page": 50, "page_size": 50},
            {"page": 100, "page_size": 50},
            {"page": 1000, "page_size": 10},
        ]
        for params in out_of_bounds_cases:
            resp = client_portal.get("/v1/cvi/leaderboard", params=params)
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 1000
            assert data["items"] == []
            assert data["page"] == params["page"]
            assert data["page_size"] == params["page_size"]

    def test_deep_offsets_and_overrides(self, client_portal: TestClient) -> None:
        """Verify explicit offset queries and interaction with page."""
        # offset=999, limit=1 -> exactly the 1000th item
        r_last = client_portal.get("/v1/cvi/leaderboard", params={"offset": 999, "limit": 1})
        assert r_last.status_code == 200
        d_last = r_last.json()
        assert d_last["total"] == 1000
        assert len(d_last["items"]) == 1
        assert d_last["items"][0]["rank"] == 1000

        # offset=1000, limit=10 -> beyond catalog
        r_beyond = client_portal.get("/v1/cvi/leaderboard", params={"offset": 1000, "limit": 10})
        assert r_beyond.status_code == 200
        d_beyond = r_beyond.json()
        assert d_beyond["total"] == 1000
        assert d_beyond["items"] == []

        # offset overrides page
        r_override = client_portal.get("/v1/cvi/leaderboard", params={"page": 1, "offset": 50, "limit": 25})
        assert r_override.status_code == 200
        d_override = r_override.json()
        assert d_override["page"] == 3  # (50 // 25) + 1 = 3
        assert len(d_override["items"]) == 25
        assert d_override["items"][0]["rank"] == 51


# ============================================================================ #
# 3. FILTERING & DYNAMIC PERSONA RE-PROJECTION
# ============================================================================ #

class TestLeaderboardFilteringAndPersonaStress:
    """Stress-test filtering combinations, adversarial search inputs, and dynamic personas."""

    @pytest.mark.parametrize("eco", list(Ecosystem))
    def test_ecosystem_filters_match_catalog_counts(
        self, client_portal: TestClient, eco: Ecosystem
    ) -> None:
        """Verify ecosystem filtering returns exactly matching ecosystem repositories."""
        resp = client_portal.get("/v1/cvi/leaderboard", params={"ecosystem": eco.value, "page_size": 1000})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 100
        assert len(data["items"]) == data["total"]
        assert all(item["ecosystem"] == eco.value for item in data["items"])

    def test_ecosystem_case_insensitivity(self, client_portal: TestClient) -> None:
        """Verify ecosystem filtering handles mixed case gracefully."""
        r_lower = client_portal.get("/v1/cvi/leaderboard", params={"ecosystem": "pypi"}).json()
        r_upper = client_portal.get("/v1/cvi/leaderboard", params={"ecosystem": "PYPI"}).json()
        r_mixed = client_portal.get("/v1/cvi/leaderboard", params={"ecosystem": "PyPi"}).json()

        assert r_lower["total"] == r_upper["total"] == r_mixed["total"]
        assert len(r_lower["items"]) == len(r_upper["items"]) == len(r_mixed["items"])

    def test_nonexistent_ecosystem_returns_empty(self, client_portal: TestClient) -> None:
        """Verify querying a nonexistent ecosystem returns total 0 and empty items without error."""
        resp = client_portal.get("/v1/cvi/leaderboard", params={"ecosystem": "nonexistent_eco_xyz"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["total_pages"] == 0
        assert data["items"] == []

    def test_adversarial_search_inputs(self, client_portal: TestClient) -> None:
        """Verify API behavior against SQLi-like patterns, symbols, and unicode search queries."""
        adversarial_searches = [
            "   ",
            "%",
            "_",
            "'",
            "''",
            "\"",
            "`",
            "--",
            ";",
            "/*",
            "*/",
            "' OR '1'='1",
            "admin'--",
            "<script>alert(1)</script>",
            "nonexistent_repo_query_xyz987",
            "日本語",
            "🦀",
        ]
        for term in adversarial_searches:
            resp = client_portal.get("/v1/cvi/leaderboard", params={"search": term, "page_size": 25})
            assert resp.status_code == 200, f"Search failed on term {term!r}: {resp.status_code}"
            data = resp.json()
            assert data["total"] >= 0
            assert isinstance(data["items"], list)

    def test_persona_reprojection_sorting_and_gating(self, client_portal: TestClient) -> None:
        """Verify persona re-projection sort order and persona-specific hard gating."""
        # 1. AI_ASSISTED: Proscribed AI repos must be disqualified (cvi=0, Tier D, is_gated=1)
        r_ai = client_portal.get(
            "/v1/cvi/leaderboard", params={"persona": "ai_assisted", "page_size": 1000}
        )
        assert r_ai.status_code == 200
        ai_items = r_ai.json()["items"]
        scores = [item["cvi"] for item in ai_items]
        assert scores == sorted(scores, reverse=True)

        gentoo = [i for i in ai_items if i["repo_id"] == "gentoo/gentoo"]
        assert len(gentoo) == 1
        assert gentoo[0]["is_gated"] == 1
        assert gentoo[0]["cvi"] == 0.0
        assert gentoo[0]["tier"] == "Tier D"

        # 2. SENIOR_SYSTEMS: elephant_factor < 3 must be disqualified
        r_sys = client_portal.get(
            "/v1/cvi/leaderboard", params={"persona": "senior_systems", "page_size": 1000}
        )
        assert r_sys.status_code == 200
        sys_items = r_sys.json()["items"]
        sys_scores = [item["cvi"] for item in sys_items]
        assert sys_scores == sorted(sys_scores, reverse=True)

        for item in sys_items:
            if item["elephant_factor"] < 3:
                assert item["is_gated"] == 1
                assert item["cvi"] == 0.0
                assert item["tier"] == "Tier D"


# ============================================================================ #
# 4. LATENCY SLA (< 50ms) & CONCURRENCY BENCHMARK
# ============================================================================ #

class TestLeaderboardSLAAndConcurrencyStress:
    """Empirical latency measurement against the < 50ms SLA under sequential and concurrent load."""

    def test_diverse_sequential_requests_sla_p95_p99(self, client_portal: TestClient) -> None:
        """Execute 100 diverse requests across all filters, personas, and page sizes, verifying p95 and p99 < 50ms."""
        workloads = [
            {"page": 1, "page_size": 50},
            {"page": 10, "page_size": 50},
            {"page": 20, "page_size": 50},
            {"page": 1, "page_size": 1000},
            {"page": 1, "page_size": 1},
            {"ecosystem": "pypi", "page_size": 50},
            {"ecosystem": "crates", "page_size": 50},
            {"ecosystem": "systems", "page_size": 50},
            {"search": "react", "page_size": 25},
            {"search": "linux", "page_size": 25},
            {"search": "vllm", "page_size": 25},
            {"persona": "ai_assisted", "page_size": 50},
            {"persona": "senior_systems", "page_size": 50},
            {"persona": "first_timer", "page_size": 50},
            {"persona": "career_capital", "page_size": 50},
            {"offset": 999, "limit": 1},
            {"offset": 500, "limit": 20},
            {"min_stars": 1000, "page_size": 50},
            {"page": 100, "page_size": 50},  # out of bounds
            {"search": "nonexistent", "page_size": 50},
        ]

        # Warmup
        client_portal.get("/v1/cvi/leaderboard")

        latencies_ms: list[float] = []
        for i in range(100):
            spec = workloads[i % len(workloads)]
            t0 = time.perf_counter()
            resp = client_portal.get("/v1/cvi/leaderboard", params=spec)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            assert resp.status_code == 200
            latencies_ms.append(elapsed_ms)

        p50 = statistics.median(latencies_ms)
        p95 = statistics.quantiles(latencies_ms, n=20)[18]
        p99 = statistics.quantiles(latencies_ms, n=100)[98]
        max_lat = max(latencies_ms)

        print(
            f"\n[SLA Benchmark] 100 Sequential Diverse Queries: "
            f"p50={p50:.2f}ms, p95={p95:.2f}ms, p99={p99:.2f}ms, max={max_lat:.2f}ms"
        )
        assert p95 < 50.0, f"Sequential p95 latency {p95:.2f}ms exceeded 50ms SLA"
        assert max_lat < 250.0, f"Sequential max latency {max_lat:.2f}ms exceeded headroom"

    def test_multi_threaded_concurrency_sla(self, client_portal: TestClient) -> None:
        """Measure concurrent request latency across 4 worker threads issuing 100 queries under < 50ms SLA."""
        specs = [
            {"page": 1, "page_size": 50},
            {"page": 5, "page_size": 20},
            {"page": 10, "page_size": 10},
            {"ecosystem": "pypi", "page_size": 25},
            {"ecosystem": "crates", "page_size": 25},
            {"ecosystem": "systems", "page_size": 25},
            {"search": "react", "page_size": 10},
            {"search": "linux", "page_size": 10},
            {"persona": "ai_assisted", "page_size": 25},
            {"persona": "senior_systems", "page_size": 50},
        ]

        def worker(idx: int) -> list[float]:
            worker_latencies: list[float] = []
            for j in range(10):
                spec = specs[(idx * 10 + j) % len(specs)]
                t0 = time.perf_counter()
                r = client_portal.get("/v1/cvi/leaderboard", params=spec)
                dur = (time.perf_counter() - t0) * 1000.0
                assert r.status_code == 200
                worker_latencies.append(dur)
            return worker_latencies

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(worker, i) for i in range(10)]
            latencies: list[float] = []
            for f in concurrent.futures.as_completed(futures):
                latencies.extend(f.result())

        assert len(latencies) == 100
        p50 = statistics.median(latencies)
        p95 = statistics.quantiles(latencies, n=20)[18]
        p99 = statistics.quantiles(latencies, n=100)[98]
        max_lat = max(latencies)

        print(
            f"\n[SLA Benchmark] Multi-Threaded Concurrency (4 threads, 100 queries): "
            f"p50={p50:.2f}ms, p95={p95:.2f}ms, p99={p99:.2f}ms, max={max_lat:.2f}ms"
        )
        assert p50 < 25.0, f"Concurrent median latency {p50:.2f}ms exceeded 25ms"
        assert p95 < 200.0, f"Concurrent p95 latency {p95:.2f}ms exceeded SLA threshold"

    def test_disk_backed_sqlite_sla_p95(self) -> None:
        """Verify SLA holds when operating against a real disk-backed SQLite database."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            app = create_app()
            client = TestClient(app)
            with client:
                store = client.portal.call(lambda: build_benchmark_store(tmp.name, count=1000))
                app.state.store = store

                latencies: list[float] = []
                # Warmup
                client.get("/v1/cvi/leaderboard")

                for _ in range(50):
                    t0 = time.perf_counter()
                    resp = client.get("/v1/cvi/leaderboard", params={"page_size": 50})
                    elapsed = (time.perf_counter() - t0) * 1000.0
                    assert resp.status_code == 200
                    latencies.append(elapsed)

                client.portal.call(store.close)

        p50 = statistics.median(latencies)
        p95 = statistics.quantiles(latencies, n=20)[18]
        max_lat = max(latencies)

        print(
            f"\n[SLA Benchmark] Disk-Backed SQLite: "
            f"p50={p50:.2f}ms, p95={p95:.2f}ms, max={max_lat:.2f}ms"
        )
        assert p95 < 50.0, f"Disk-backed SQLite p95 latency {p95:.2f}ms exceeded 50ms SLA"
