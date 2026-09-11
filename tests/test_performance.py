"""Performance-SLA suite for the operational store (spec 12.1).

``docs/ARCHITECTURE.md`` section 12.1 (~line 672) sets a performance SLA
of <50ms response time for the matrix endpoint "from in-memory
operational cache". This suite pins that SLA against
``cvi.lakehouse.OperationalStore`` (in-memory SQLite, i.e. the
operational cache) at a fixed, deterministic seed size ``N``.

Bar: a test that is easy to pass is a defect. The SLA assertion is fixed
at <50ms p95 and is never loosened; if this hardware cannot hold it at
N=1000 the seed size is scaled down in documented steps (1000 -> 500 ->
200) instead, with the final N and measured p95 recorded at the ``N``
constant below.

Current hardware (measured 2026-09-11 via the owner's run convention
``env -u PYTEST_ADDOPTS .venv/bin/python -m pytest -n auto -p
no:cacheprovider -q``):
    N = 1000
    query_matrix(min_stars=100)                 p50=...ms p95=...ms
    query_matrix(ecosystem=..., quadrant=...)   p50=...ms p95=...ms
    leaderboard(limit=100)                      p50=...ms p95=...ms
    get_repo(known key)                        p50=...ms p95=...ms
"""

from __future__ import annotations

import math
import time

from cvi.lakehouse import OperationalStore
from cvi.models import CVICompositeResult, Ecosystem, Quadrant, Tier

# Seed size for the SLA runs. Per the task directive the assertion stays
# <50ms p95; if this box cannot hold that, scale N down in steps
# (1000 -> 500 -> 200) until green and document the final N + p95 here.
N = 1000

TIMED_RUNS = 25
SLA_MS = 50.0

_ECOSYSTEMS: tuple[Ecosystem, ...] = tuple(Ecosystem)  # 6 members
_TIERS: tuple[Tier, ...] = tuple(Tier)  # 6 members
_QUADRANTS: tuple[Quadrant, ...] = tuple(Quadrant)  # 4 members


def percentile_ms(samples: list[float], pct: float) -> float:
    """Nearest-rank percentile in ms. Deterministic, no randomness."""
    ordered = sorted(samples)
    rank = math.ceil(pct / 100.0 * len(ordered))
    return ordered[max(1, rank) - 1]


def make_result(i: int) -> CVICompositeResult:
    """Deterministic composite for seed index ``i`` (no randomness).

    All fields vary with ``i`` so ecosystem/quadrant/tier/cvi are spread
    across their full domains; the same index reproduces the same row.
    """
    disqualified = i % 25 == 0
    return CVICompositeResult(
        owner=f"org{i % 50:02d}",
        name=f"proj{i:05d}",
        ecosystem=_ECOSYSTEMS[i % len(_ECOSYSTEMS)],
        overall_cvi=round(((i * 9973) % 10000) / 100.0, 2),
        rank_tier=_TIERS[i % len(_TIERS)],
        matrix_quadrant=_QUADRANTS[i % len(_QUADRANTS)],
        leverage_score=round(((i * 271) % 1000) / 10.0, 1),
        friction_score=round(((i * 613) % 1000) / 10.0, 1),
        is_stage0_disqualified=disqualified,
        disqualification_reason="black-hole heuristic" if disqualified else None,
    )


def seed(store: OperationalStore, count: int) -> None:
    """Seed ``count`` deterministic rows via ``upsert_composite``.

    ``CVICompositeResult`` carries no raw repo metrics (spec 9.3), so
    ``upsert_composite`` stores the documented default ``stars=0``
    (lakehouse module docstring); star counts are varied
    deterministically afterwards via direct SQL, mirroring
    ``tests/test_lakehouse.py::set_stars``.
    """
    for i in range(count):
        store.upsert_composite(make_result(i))
    store.connection.executemany(
        "UPDATE repositories SET stars = ? WHERE name = ?",
        [((i * 37) % 2500, f"proj{i:05d}") for i in range(count)],
    )
    store.connection.commit()


def measure(operation, runs: int = TIMED_RUNS) -> tuple[float, float]:
    """Return (p50_ms, p95_ms) over ``runs`` timed calls.

    One throwaway call first (warmup: connection pools, caches and code
    paths are hot; it is not timed).
    """
    operation()  # warmup, throwaway
    samples: list[float] = []
    for _ in range(runs):
        start = time.perf_counter_ns()
        operation()
        samples.append((time.perf_counter_ns() - start) / 1_000_000.0)
    return percentile_ms(samples, 50.0), percentile_ms(samples, 95.0)


def test_spec_11_2_indexes_exist() -> None:
    """Spec 11.2 mandates idx_cvi_ecosystem_rank and idx_cvi_quadrant."""
    store = OperationalStore()
    try:
        names = [
            row[1]
            for row in store.connection.execute(
                "PRAGMA index_list('repositories')"
            )
        ]
    finally:
        store.close()
    assert "idx_cvi_ecosystem_rank" in names, f"missing spec 11.2 index; found: {names}"
    assert "idx_cvi_quadrant" in names, f"missing spec 11.2 index; found: {names}"


def test_count_by_quadrant_always_returns_all_four_keys() -> None:
    """Documented lakehouse contract: all 4 quadrant keys, even when empty."""
    store = OperationalStore()
    try:
        empty = store.count_by_quadrant()
        assert set(empty) == {q.value for q in Quadrant}, empty
        assert all(v == 0 for v in empty.values()), empty

        # One Sweet Spot row; the other three quadrants must still appear.
        store.upsert_composite(make_result(0))
        partial = store.count_by_quadrant()
        assert set(partial) == {q.value for q in Quadrant}, partial
        assert partial[Quadrant.SWEET_SPOT.value] == 1, partial
        assert sum(partial.values()) == 1, partial
    finally:
        store.close()


def test_matrix_leaderboard_get_repo_sla_p95_under_50ms() -> None:
    """Spec 12.1 SLA: <50ms p95 for the four operational-cache reads.

    Deterministic in-memory store seeded with N repositories; each
    operation gets one throwaway warmup call, then 25 timed runs.
    """
    store = OperationalStore()
    try:
        seed(store, N)

        # Known key: seed index 3 -> ("org03", "proj00003"), stars = 111.
        known_owner, known_name = "org03", "proj00003"

        # Sanity: the workload is non-degenerate before it is timed.
        row = store.get_repo(known_owner, known_name)
        assert row is not None, "known key must exist after seeding"
        assert row["stars"] == 111, row
        assert len(store.query_matrix(min_stars=100)) > 0
        assert len(store.query_matrix(ecosystem="npm", quadrant="Sweet Spot")) > 0
        rows, total = store.leaderboard(limit=100)
        assert len(rows) == 100
        assert total == N

        benchmarks: dict[str, object] = {
            "query_matrix(min_stars=100)": lambda: store.query_matrix(min_stars=100),
            "query_matrix(ecosystem=..., quadrant=...)": lambda: store.query_matrix(
                ecosystem="npm", quadrant="Sweet Spot"
            ),
            "leaderboard(limit=100)": lambda: store.leaderboard(limit=100),
            "get_repo(known key)": lambda: store.get_repo(known_owner, known_name),
        }
        for label, operation in benchmarks.items():
            p50_ms, p95_ms = measure(operation)
            assert p95_ms < SLA_MS, (
                f"{label}: p95={p95_ms:.3f}ms (p50={p50_ms:.3f}ms) over "
                f"{TIMED_RUNS} runs at N={N} violates the spec 12.1 SLA "
                f"of <{SLA_MS:.0f}ms; scale N down (1000->500->200), never "
                "loosen the assertion"
            )
    finally:
        store.close()
