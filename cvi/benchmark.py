"""Benchmark catalog and store seeder public interface (spec 1, M1 Component 1)."""

from __future__ import annotations

from .benchmark_data import (
    BENCHMARK_NOW,
    build_benchmark_store,
    generate_benchmark_catalog,
    generate_synthetic_pr_events,
    get_benchmark_catalog,
    seed_benchmark_store,
)

__all__ = [
    "BENCHMARK_NOW",
    "build_benchmark_store",
    "generate_benchmark_catalog",
    "generate_synthetic_pr_events",
    "get_benchmark_catalog",
    "seed_benchmark_store",
]
