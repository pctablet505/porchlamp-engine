"""Empirical benchmark & stress harness runner for Milestone 4 Phase 2.

Measures p50, p95, p99, and max latencies across all project SLAs:
1. OperationalStore seeding SLA (1,000 repos < 2.0s)
2. Leaderboard API response time SLA (p95 < 50ms) under diverse query params
3. Healthz probe latency SLA (p95 < 50ms)
4. Matrix spatial query latency SLA (p95 < 50ms)
5. Static file delivery SLA (p95 < 50ms for / and /index.html)
6. Concurrency stress testing (multi-threaded and async live HTTP server)
"""

from __future__ import annotations

import concurrent.futures
from contextlib import closing
import os
import socket
import statistics
import tempfile
import threading
import time
from typing import Any

from fastapi.testclient import TestClient
import httpx
import uvicorn

from porchlamp.api import create_app
from tests.fixtures.synthetic_catalog import (
    build_benchmark_store,
    generate_benchmark_catalog,
    seed_benchmark_store,
)
from porchlamp.lakehouse import OperationalStore


def percentile(samples: list[float], pct: float) -> float:
    """Calculate nearest-rank percentile."""
    if not samples:
        return 0.0
    sorted_s = sorted(samples)
    idx = round((pct / 100.0) * len(sorted_s) + 0.5) - 1
    return sorted_s[max(0, min(idx, len(sorted_s) - 1))]


def summarize_latencies(samples: list[float]) -> dict[str, float]:
    return {
        "count": len(samples),
        "mean": statistics.mean(samples) if samples else 0.0,
        "stddev": statistics.stdev(samples) if len(samples) > 1 else 0.0,
        "min": min(samples) if samples else 0.0,
        "p50": percentile(samples, 50.0),
        "p95": percentile(samples, 95.0),
        "p99": percentile(samples, 99.0),
        "max": max(samples) if samples else 0.0,
    }


def find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


# ============================================================================ #
# 1. OperationalStore Seeding Benchmark
# ============================================================================ #

def benchmark_seeding(runs: int = 15) -> dict[str, Any]:
    print("\n" + "=" * 75)
    print(" 1. OPERATIONAL STORE SEEDING SLA BENCHMARK (SLA: < 2.0s for 1,000 repos)")
    print("=" * 75)

    catalog = generate_benchmark_catalog()
    assert len(catalog) == 1000, f"Expected 1000 repos, got {len(catalog)}"

    # In-memory seeding
    mem_durations: list[float] = []
    for _ in range(runs):
        store = OperationalStore(":memory:")
        t0 = time.perf_counter()
        count = seed_benchmark_store(store, count=1000)
        dur = time.perf_counter() - t0
        mem_durations.append(dur)
        assert count == 1000
        store.close()

    mem_stats = summarize_latencies([d * 1000.0 for d in mem_durations])
    print(
        f"In-Memory Seeding ({runs} runs):\n"
        f"  Mean: {mem_stats['mean']:.2f}ms | p50: {mem_stats['p50']:.2f}ms | "
        f"p95: {mem_stats['p95']:.2f}ms | p99: {mem_stats['p99']:.2f}ms | Max: {mem_stats['max']:.2f}ms"
    )

    # Disk-backed SQLite seeding
    disk_durations: list[float] = []
    for _ in range(runs):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            store = OperationalStore(tmp_path)
            t0 = time.perf_counter()
            count = seed_benchmark_store(store, count=1000)
            dur = time.perf_counter() - t0
            disk_durations.append(dur)
            assert count == 1000
            store.close()
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    disk_stats = summarize_latencies([d * 1000.0 for d in disk_durations])
    print(
        f"Disk-Backed Seeding ({runs} runs):\n"
        f"  Mean: {disk_stats['mean']:.2f}ms | p50: {disk_stats['p50']:.2f}ms | "
        f"p95: {disk_stats['p95']:.2f}ms | p99: {disk_stats['p99']:.2f}ms | Max: {disk_stats['max']:.2f}ms"
    )

    sla_pass = (mem_stats["p95"] < 2000.0) and (disk_stats["p95"] < 2000.0)
    print(f"Seeding SLA Result (< 2000ms): {'PASS' if sla_pass else 'FAIL'}")
    return {
        "in_memory": mem_stats,
        "disk": disk_stats,
        "sla_pass": sla_pass,
    }


# ============================================================================ #
# 2. In-Process API Endpoint SLA Benchmark (1,000 repos)
# ============================================================================ #

def benchmark_endpoints_in_process(iterations: int = 50) -> dict[str, Any]:
    print("\n" + "=" * 75)
    print(" 2. IN-PROCESS API ENDPOINT SLA BENCHMARK (SLA: p95 < 50ms on 1,000 repos)")
    print("=" * 75)

    app = create_app()
    results: dict[str, dict[str, float]] = {}

    with TestClient(app) as client:
        # Attach 1,000-repo benchmark store on portal thread
        app.state.store = client.portal.call(build_benchmark_store)

        test_cases = [
            # Healthz
            ("GET /healthz", "/healthz", "Healthz Probe"),
            # Matrix
            ("GET /v1/porchlamp/matrix (default)", "/v1/porchlamp/matrix", "Matrix Default"),
            ("GET /v1/porchlamp/matrix (min_stars=100)", "/v1/porchlamp/matrix?min_stars=100", "Matrix Stars Filter"),
            ("GET /v1/porchlamp/matrix (ecosystem=pypi)", "/v1/porchlamp/matrix?ecosystem=pypi", "Matrix PyPI Filter"),
            ("GET /v1/porchlamp/matrix (all black holes)", "/v1/porchlamp/matrix?exclude_black_holes=false", "Matrix Black Holes"),
            # Leaderboard pagination
            ("GET /v1/porchlamp/leaderboard (page=1, size=50)", "/v1/porchlamp/leaderboard?page=1&page_size=50", "Leaderboard Page 1"),
            ("GET /v1/porchlamp/leaderboard (page=10, size=50)", "/v1/porchlamp/leaderboard?page=10&page_size=50", "Leaderboard Page 10"),
            ("GET /v1/porchlamp/leaderboard (page=20, size=50)", "/v1/porchlamp/leaderboard?page=20&page_size=50", "Leaderboard Page 20 (Deep)"),
            ("GET /v1/porchlamp/leaderboard (page_size=1000)", "/v1/porchlamp/leaderboard?page=1&page_size=1000", "Leaderboard 1,000 Repos Bulk"),
            ("GET /v1/porchlamp/leaderboard (offset=950)", "/v1/porchlamp/leaderboard?offset=950&limit=50", "Leaderboard Offset 950"),
            # Leaderboard ecosystem filters
            ("GET /v1/porchlamp/leaderboard (eco=pypi)", "/v1/porchlamp/leaderboard?ecosystem=pypi", "Leaderboard PyPI"),
            ("GET /v1/porchlamp/leaderboard (eco=npm)", "/v1/porchlamp/leaderboard?ecosystem=npm", "Leaderboard npm"),
            ("GET /v1/porchlamp/leaderboard (eco=crates)", "/v1/porchlamp/leaderboard?ecosystem=crates", "Leaderboard Crates"),
            ("GET /v1/porchlamp/leaderboard (eco=go)", "/v1/porchlamp/leaderboard?ecosystem=go", "Leaderboard Go"),
            ("GET /v1/porchlamp/leaderboard (eco=maven)", "/v1/porchlamp/leaderboard?ecosystem=maven", "Leaderboard Maven"),
            ("GET /v1/porchlamp/leaderboard (eco=systems)", "/v1/porchlamp/leaderboard?ecosystem=systems", "Leaderboard Systems"),
            # Leaderboard search
            ("GET /v1/porchlamp/leaderboard (search=vllm)", "/v1/porchlamp/leaderboard?search=vllm", "Leaderboard Search 'vllm'"),
            ("GET /v1/porchlamp/leaderboard (search=kubernetes)", "/v1/porchlamp/leaderboard?search=kubernetes", "Leaderboard Search 'kubernetes'"),
            ("GET /v1/porchlamp/leaderboard (search=a broad)", "/v1/porchlamp/leaderboard?search=a", "Leaderboard Broad Search 'a'"),
            ("GET /v1/porchlamp/leaderboard (search=nonexistent)", "/v1/porchlamp/leaderboard?search=zzzznotfound", "Leaderboard Search 0-hit"),
            ("GET /v1/porchlamp/leaderboard (search=% wildcard)", "/v1/porchlamp/leaderboard?search=%25", "Leaderboard Wildcard Escaped '%'"),
            # Leaderboard dynamic persona projections
            ("GET /v1/porchlamp/leaderboard (persona=balanced)", "/v1/porchlamp/leaderboard?persona=balanced", "Leaderboard Persona BALANCED"),
            ("GET /v1/porchlamp/leaderboard (persona=first_timer)", "/v1/porchlamp/leaderboard?persona=first_timer", "Leaderboard Persona FIRST_TIMER"),
            ("GET /v1/porchlamp/leaderboard (persona=career_capital)", "/v1/porchlamp/leaderboard?persona=career_capital", "Leaderboard Persona CAREER_CAPITAL"),
            ("GET /v1/porchlamp/leaderboard (persona=senior_systems)", "/v1/porchlamp/leaderboard?persona=senior_systems", "Leaderboard Persona SENIOR_SYSTEMS"),
            ("GET /v1/porchlamp/leaderboard (persona=ai_assisted)", "/v1/porchlamp/leaderboard?persona=ai_assisted", "Leaderboard Persona AI_ASSISTED"),
            # Static delivery
            ("GET / (index delivery)", "/", "Static /"),
            ("GET /index.html (direct file)", "/index.html", "Static /index.html"),
            # Repos deep-dive
            ("GET /v1/porchlamp/repos/vllm-project/vllm", "/v1/porchlamp/repos/vllm-project/vllm", "Deep Dive (vllm)"),
            ("GET /v1/porchlamp/repos/torvalds/linux", "/v1/porchlamp/repos/torvalds/linux", "Deep Dive (linux)"),
        ]

        print(f"{'Endpoint':<45s} | {'p50':>7s} | {'p95':>7s} | {'p99':>7s} | {'max':>7s} | {'SLA Status'}")
        print("-" * 88)

        for name, path, _ in test_cases:
            # Warmup
            client.get(path)

            samples: list[float] = []
            for _ in range(iterations):
                t0 = time.perf_counter_ns()
                r = client.get(path)
                dur = (time.perf_counter_ns() - t0) / 1_000_000.0
                assert r.status_code == 200, f"{path} returned {r.status_code}: {r.text}"
                samples.append(dur)

            stats = summarize_latencies(samples)
            results[name] = stats
            status = "PASS (<50ms)" if stats["p95"] < 50.0 else "FAIL (>=50ms)"
            print(f"{name:<45s} | {stats['p50']:6.2f}ms | {stats['p95']:6.2f}ms | {stats['p99']:6.2f}ms | {stats['max']:6.2f}ms | {status}")

    return results


# ============================================================================ #
# 3. Live HTTP Server Over TCP Socket Benchmark
# ============================================================================ #

def benchmark_live_uvicorn(iterations: int = 50) -> dict[str, Any]:
    print("\n" + "=" * 75)
    print(" 3. LIVE UVICORN HTTP SERVER OVER TCP LOOPBACK (SLA: p95 < 50ms)")
    print("=" * 75)

    port = find_free_port()
    server_holder: dict[str, Any] = {}

    def run_server() -> None:
        store = build_benchmark_store()
        app = create_app(store=store)
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        server_holder["server"] = server
        server.run()

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    base_url = f"http://127.0.0.1:{port}"
    sync_client = httpx.Client(base_url=base_url, timeout=10.0)

    # Wait for server readiness
    ready = False
    for _ in range(50):
        try:
            r = sync_client.get("/healthz")
            if r.status_code == 200:
                ready = True
                break
        except Exception:
            time.sleep(0.05)

    assert ready, "Uvicorn server failed to start within 2.5 seconds"
    print(f"Live Uvicorn listening at {base_url} (ready=True)")

    test_endpoints = [
        ("Live GET /healthz", "/healthz"),
        ("Live GET / (static index)", "/"),
        ("Live GET /index.html", "/index.html"),
        ("Live GET /v1/porchlamp/matrix", "/v1/porchlamp/matrix"),
        ("Live GET /v1/porchlamp/leaderboard (default)", "/v1/porchlamp/leaderboard"),
        ("Live GET /v1/porchlamp/leaderboard (deep p20)", "/v1/porchlamp/leaderboard?page=20&page_size=50"),
        ("Live GET /v1/porchlamp/leaderboard (persona=ai_assisted)", "/v1/porchlamp/leaderboard?persona=ai_assisted"),
        ("Live GET /v1/porchlamp/leaderboard (search=vllm)", "/v1/porchlamp/leaderboard?search=vllm"),
        ("Live GET /v1/porchlamp/repos/vllm-project/vllm", "/v1/porchlamp/repos/vllm-project/vllm"),
    ]

    results: dict[str, dict[str, float]] = {}
    print(f"{'Live HTTP Route':<45s} | {'p50':>7s} | {'p95':>7s} | {'p99':>7s} | {'max':>7s} | {'SLA Status'}")
    print("-" * 88)

    for name, path in test_endpoints:
        sync_client.get(path)  # warmup

        samples: list[float] = []
        for _ in range(iterations):
            t0 = time.perf_counter_ns()
            r = sync_client.get(path)
            dur = (time.perf_counter_ns() - t0) / 1_000_000.0
            assert r.status_code == 200
            samples.append(dur)

        stats = summarize_latencies(samples)
        results[name] = stats
        status = "PASS (<50ms)" if stats["p95"] < 50.0 else "FAIL (>=50ms)"
        print(f"{name:<45s} | {stats['p50']:6.2f}ms | {stats['p95']:6.2f}ms | {stats['p99']:6.2f}ms | {stats['max']:6.2f}ms | {status}")

    # Clean up server
    if "server" in server_holder:
        server_holder["server"].should_exit = True
    sync_client.close()

    return results


# ============================================================================ #
# 4. Multi-Worker Concurrency Stress Benchmark
# ============================================================================ #

def benchmark_concurrency_stress() -> dict[str, Any]:
    print("\n" + "=" * 75)
    print(" 4. HIGH CONCURRENCY STRESS BENCHMARK (Mixed Traffic Under Concurrent Workers)")
    print("=" * 75)

    port = find_free_port()
    server_holder: dict[str, Any] = {}

    def run_server() -> None:
        store = build_benchmark_store()
        app = create_app(store=store)
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        server = uvicorn.Server(config)
        server_holder["server"] = server
        server.run()

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    base_url = f"http://127.0.0.1:{port}"
    sync_client = httpx.Client(base_url=base_url, timeout=10.0)

    for _ in range(50):
        try:
            if sync_client.get("/healthz").status_code == 200:
                break
        except Exception:
            time.sleep(0.05)

    traffic_pool = [
        "/healthz",
        "/",
        "/index.html",
        "/v1/porchlamp/matrix",
        "/v1/porchlamp/matrix?ecosystem=pypi&min_stars=100",
        "/v1/porchlamp/leaderboard?page=1&page_size=50",
        "/v1/porchlamp/leaderboard?page=5&page_size=50",
        "/v1/porchlamp/leaderboard?page=20&page_size=50",
        "/v1/porchlamp/leaderboard?ecosystem=crates",
        "/v1/porchlamp/leaderboard?search=vllm",
        "/v1/porchlamp/leaderboard?persona=ai_assisted",
        "/v1/porchlamp/leaderboard?persona=senior_systems",
        "/v1/porchlamp/repos/vllm-project/vllm",
    ]

    concurrency_levels = [1, 5, 10, 20, 40]
    requests_per_worker = 25
    summary: dict[int, dict[str, Any]] = {}

    print(f"{'Concurrency':<12s} | {'Total Req':>9s} | {'RPS':>9s} | {'p50':>7s} | {'p95':>7s} | {'p99':>7s} | {'max':>7s} | {'Errors'}")
    print("-" * 88)

    for c in concurrency_levels:
        total_requests = c * requests_per_worker
        all_latencies: list[float] = []
        errors: list[str] = []

        # B023: bind `errors` as a default arg so the closure captures this
        # iteration's list rather than whatever `errors` is when it later runs.
        def worker_task(worker_id: int, errors: list[str] = errors) -> list[float]:
            worker_latencies: list[float] = []
            with httpx.Client(base_url=base_url, timeout=15.0) as client:
                for i in range(requests_per_worker):
                    route = traffic_pool[(worker_id * requests_per_worker + i) % len(traffic_pool)]
                    t0 = time.perf_counter_ns()
                    try:
                        resp = client.get(route)
                        dur = (time.perf_counter_ns() - t0) / 1_000_000.0
                        if resp.status_code == 200:
                            worker_latencies.append(dur)
                        else:
                            errors.append(f"HTTP {resp.status_code} on {route}")
                    except Exception as exc:
                        errors.append(f"Exc on {route}: {exc}")
            return worker_latencies

        t_wall_start = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=c) as executor:
            futures = [executor.submit(worker_task, w) for w in range(c)]
            for f in concurrent.futures.as_completed(futures):
                all_latencies.extend(f.result())
        wall_time = time.perf_counter() - t_wall_start

        rps = len(all_latencies) / wall_time if wall_time > 0 else 0.0
        stats = summarize_latencies(all_latencies)
        summary[c] = {
            "total_requests": total_requests,
            "wall_time_sec": wall_time,
            "rps": rps,
            "errors": len(errors),
            "stats": stats,
        }

        print(
            f"{c:<12d} | {total_requests:9d} | {rps:8.1f}/s | "
            f"{stats['p50']:6.2f}ms | {stats['p95']:6.2f}ms | {stats['p99']:6.2f}ms | {stats['max']:6.2f}ms | {len(errors)}"
        )

    if "server" in server_holder:
        server_holder["server"].should_exit = True
    sync_client.close()

    return summary


# ============================================================================ #
# Main Entry Point
# ============================================================================ #

if __name__ == "__main__":
    t_suite_start = time.perf_counter()
    seed_res = benchmark_seeding()
    api_res = benchmark_endpoints_in_process()
    live_res = benchmark_live_uvicorn()
    conc_res = benchmark_concurrency_stress()
    total_suite_time = time.perf_counter() - t_suite_start

    print("\n" + "=" * 75)
    print(f" ALL BENCHMARKS COMPLETE (Wall time: {total_suite_time:.2f}s)")
    print("=" * 75)
