"""Milestone 4 Phase 2: Performance SLA Test Suite.

Empirically validates:
1. OperationalStore seeding SLA: 1,000 repos seeded in < 2.0s (memory & disk).
"""

from __future__ import annotations

import os
import tempfile
import time

from tests.fixtures.synthetic_catalog import (
    generate_benchmark_catalog,
    seed_benchmark_store,
)
from porchlamp.lakehouse import OperationalStore


def _calc_p95(samples: list[float]) -> float:
    """Deterministic nearest-rank p95 in milliseconds."""
    ordered = sorted(samples)
    idx = round(0.95 * len(ordered) + 0.5) - 1
    return ordered[max(0, min(idx, len(ordered) - 1))]


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
