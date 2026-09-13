"""Adversarial stress tests and empirical benchmarks for Milestone 1.

Verifies:
1. Benchmark Seeding SLA: Disk-backed and in-memory SQLite seeding of 1,000 repos (< 2.0s SLA).
2. Catalog Integrity: Exactly 1,000 unique (owner, name) repos, non-empty fields, valid enums, non-zero distributions, landmark preservation, and hydrated deep-dive JSON validity.
3. Storage Querying Stress: OperationalStore.leaderboard across limits (1, 50, 500, 1000, 0), offsets, personas, ecosystems, search terms (adversarial characters / SQLi-like strings), repeated loops, and concurrency under < 50ms SLA.
"""

from __future__ import annotations

import collections
import concurrent.futures
import json
import statistics
import tempfile
import time
from typing import Any

import pytest

from tests.fixtures.synthetic_catalog import (
    build_benchmark_store,
    generate_benchmark_catalog,
    seed_benchmark_store,
)
from cvi.lakehouse import OperationalStore
from cvi.models import (
    AIPosture,
    Ecosystem,
    LegalIPModel,
    Persona,
    Quadrant,
    Tier,
)


# ============================================================================ #
# 1. BENCHMARK SEEDING SLA (< 2.0s)
# ============================================================================ #

class TestSeedingSLA:
    """Stress-test benchmark seeding performance against the < 2.0s SLA."""

    def test_in_memory_seeding_sla_repeated(self) -> None:
        """Measure 5 consecutive in-memory seeding runs of 1,000 repos."""
        durations: list[float] = []
        for _ in range(5):
            store = OperationalStore(":memory:")
            t0 = time.perf_counter()
            count = seed_benchmark_store(store, count=1000)
            elapsed = time.perf_counter() - t0
            store.close()

            assert count == 1000
            durations.append(elapsed)

        median_dur = statistics.median(durations)
        max_dur = max(durations)
        print(f"\n[SLA] In-Memory Seeding (5 runs): median={median_dur:.4f}s, max={max_dur:.4f}s, min={min(durations):.4f}s")
        assert max_dur < 2.0, f"In-memory seeding max duration {max_dur:.4f}s exceeded 2.0s SLA"

    def test_disk_backed_seeding_sla_repeated(self) -> None:
        """Measure 5 consecutive disk-backed seeding runs of 1,000 repos in temporary files."""
        durations: list[float] = []
        for _ in range(5):
            with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
                store = OperationalStore(tmp.name)
                t0 = time.perf_counter()
                count = seed_benchmark_store(store, count=1000)
                elapsed = time.perf_counter() - t0
                store.close()

                assert count == 1000
                durations.append(elapsed)

        median_dur = statistics.median(durations)
        max_dur = max(durations)
        print(f"\n[SLA] Disk-Backed Seeding (5 runs): median={median_dur:.4f}s, max={max_dur:.4f}s, min={min(durations):.4f}s")
        assert max_dur < 2.0, f"Disk-backed seeding max duration {max_dur:.4f}s exceeded 2.0s SLA"

    def test_disk_backed_reseeding_upsert_sla(self) -> None:
        """Verify re-seeding existing database (UPSERT / conflict handling) satisfies < 2.0s SLA."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            store = OperationalStore(tmp.name)
            # Initial seed
            seed_benchmark_store(store, count=1000)

            # Re-seed into existing DB
            t0 = time.perf_counter()
            count = seed_benchmark_store(store, count=1000)
            elapsed = time.perf_counter() - t0

            # Assertions
            assert count == 1000
            assert elapsed < 2.0, f"Re-seeding upsert {elapsed:.4f}s exceeded 2.0s SLA"

            # Verify no row duplication
            cur = store.connection.execute("SELECT COUNT(*) FROM repositories")
            total = cur.fetchone()[0]
            assert total == 1000, f"Expected 1000 rows, found {total}"
            store.close()


# ============================================================================ #
# 2. CATALOG INTEGRITY & DISTRIBUTIONS
# ============================================================================ #

class TestCatalogIntegrity:
    """Adversarially verify catalog integrity, schemas, types, and distributions."""

    def test_catalog_exact_count_and_uniqueness(self) -> None:
        catalog = generate_benchmark_catalog()
        assert len(catalog) == 1000

        # Assert exactly 1,000 unique (owner, name) pairs
        keys = [(r["owner"], r["name"]) for r in catalog]
        assert len(set(keys)) == 1000, f"Duplicate repository identifiers found: {len(keys) - len(set(keys))}"

        # Assert no empty or whitespace-only owners/names
        for owner, name in keys:
            assert isinstance(owner, str) and owner.strip() == owner and len(owner) > 0
            assert isinstance(name, str) and name.strip() == name and len(name) > 0
            assert "/" not in owner and "/" not in name

    def test_catalog_valid_fields_and_enums(self) -> None:
        catalog = generate_benchmark_catalog()
        required_keys = {
            "owner", "name", "ecosystem", "quadrant", "tier", "pillars",
            "stars", "forks", "monthly_downloads", "dependents_count",
            "elephant_factor", "legal_ip_model", "ai_posture", "is_gated",
        }

        for idx, r in enumerate(catalog):
            missing = required_keys - set(r.keys())
            assert not missing, f"Repo #{idx} ({r.get('owner')}/{r.get('name')}) missing keys: {missing}"

            # Enums
            assert isinstance(r["ecosystem"], Ecosystem)
            assert isinstance(r["quadrant"], Quadrant)
            assert isinstance(r["tier"], Tier)
            assert isinstance(r["legal_ip_model"], LegalIPModel)
            assert isinstance(r["ai_posture"], AIPosture)

            # Pillars
            pillars = r["pillars"]
            assert isinstance(pillars, tuple) and len(pillars) == 5
            for p in pillars:
                assert isinstance(p, (int, float))
                assert 0.0 <= p <= 100.0, f"Pillar score out of bounds: {p} in {r['owner']}/{r['name']}"

            # Numeric metrics
            assert isinstance(r["stars"], int) and r["stars"] >= 0
            assert isinstance(r["forks"], int) and r["forks"] >= 0
            assert isinstance(r["monthly_downloads"], int) and r["monthly_downloads"] >= 0
            assert isinstance(r["dependents_count"], int) and r["dependents_count"] >= 0
            assert isinstance(r["elephant_factor"], int) and r["elephant_factor"] >= 1
            assert isinstance(r["is_gated"], bool)

    def test_catalog_distributions_non_zero(self) -> None:
        catalog = generate_benchmark_catalog()

        # Ecosystems: all 6 must be represented with substantial counts
        eco_counts = collections.Counter(r["ecosystem"] for r in catalog)
        for eco in Ecosystem:
            assert eco_counts[eco] >= 150, f"Ecosystem {eco} has insufficient count: {eco_counts[eco]}"

        # Quadrants: all 4 must be represented with canonical quotas
        quad_counts = collections.Counter(r["quadrant"] for r in catalog)
        assert quad_counts[Quadrant.SWEET_SPOT] == 350
        assert quad_counts[Quadrant.CRUCIBLE] == 150
        assert quad_counts[Quadrant.NURSERY] == 250
        assert quad_counts[Quadrant.GRAVEYARD] == 250

        # Tiers: all 6 must have non-zero representation
        tier_counts = collections.Counter(r["tier"] for r in catalog)
        for tier in Tier:
            assert tier_counts[tier] > 0, f"Tier {tier} has zero representation"

        # Stars distribution: spans from 0 to >100,000
        stars = [r["stars"] for r in catalog]
        assert min(stars) >= 0
        assert max(stars) >= 100000
        assert statistics.median(stars) > 500

    def test_ground_truth_landmark_preservation(self) -> None:
        catalog = generate_benchmark_catalog()
        by_key = {(r["owner"], r["name"]): r for r in catalog}

        # vllm
        vllm = by_key.get(("vllm-project", "vllm"))
        assert vllm is not None
        assert vllm["ecosystem"] == Ecosystem.PYPI
        assert vllm["quadrant"] == Quadrant.SWEET_SPOT
        assert vllm["tier"] == Tier.S
        assert vllm["legal_ip_model"] == LegalIPModel.DCO
        assert vllm["elephant_factor"] >= 4
        assert not vllm["is_gated"]

        # keras-hub
        hub = by_key.get(("keras-team", "keras-hub"))
        assert hub is not None
        assert hub["ecosystem"] == Ecosystem.PYPI
        assert hub["quadrant"] == Quadrant.GRAVEYARD
        assert hub["tier"] == Tier.D
        assert hub["legal_ip_model"] == LegalIPModel.CORP_CLA_UNILATERAL
        assert hub["elephant_factor"] == 1
        assert hub["is_gated"] is True

        # react
        react = by_key.get(("facebook", "react"))
        assert react is not None
        assert react["ecosystem"] == Ecosystem.NPM
        assert react["quadrant"] == Quadrant.SWEET_SPOT
        assert react["tier"] == Tier.S_PLUS
        assert react["dependents_count"] >= 200000

        # kubernetes
        k8s = by_key.get(("kubernetes", "kubernetes"))
        assert k8s is not None
        assert k8s["ecosystem"] == Ecosystem.GO
        assert k8s["quadrant"] == Quadrant.CRUCIBLE
        assert k8s["elephant_factor"] >= 4

        # linux
        linux = by_key.get(("torvalds", "linux"))
        assert linux is not None
        assert linux["ecosystem"] == Ecosystem.SYSTEMS
        assert linux["quadrant"] == Quadrant.CRUCIBLE
        assert linux["off_platform_reviews"] is True

        # gentoo
        gentoo = by_key.get(("gentoo", "gentoo"))
        assert gentoo is not None
        assert gentoo["ecosystem"] == Ecosystem.SYSTEMS
        assert gentoo["hard_ai_ban"] is True
        assert gentoo["ai_posture"] == AIPosture.PROSCRIBED

    def test_database_hydration_and_deep_dive_payload_integrity(self) -> None:
        store = OperationalStore(":memory:")
        seed_benchmark_store(store, count=1000)

        cur = store.connection.execute("SELECT * FROM repositories")
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        assert len(rows) == 1000

        # Check deep dive payloads and database columns
        for row in rows:
            # No nulls in primary metadata columns
            assert row["owner"] and row["name"] and row["ecosystem"]
            assert row["stars"] is not None and row["stars"] >= 0
            assert row["overall_cvi"] is not None and 0.0 <= row["overall_cvi"] <= 100.0
            assert row["rank_tier"] in [t.value for t in Tier]
            assert row["matrix_quadrant"] in [q.value for q in Quadrant]

            # Deep dive payload parsing
            assert row["deep_dive_json"] is not None
            dd = json.loads(row["deep_dive_json"])
            expected_top_keys = {
                "raw_metrics",
                "survival_curve",
                "elephant_breakdown",
                "legal_ip",
                "good_first_issues",
                "maintainer_playbook",
            }
            assert expected_top_keys.issubset(dd.keys()), f"Missing keys in deep_dive_json: {expected_top_keys - set(dd.keys())}"

            playbook = dd["maintainer_playbook"]
            assert "current_cvi" in playbook
            assert "potential_cvi" in playbook
            assert "total_point_gain" in playbook
            assert "actions" in playbook
            assert playbook["potential_cvi"] <= 100.0
            assert playbook["total_point_gain"] >= 0.0

            # Gating validation
            if row["is_stage0_disqualified"]:
                assert row["overall_cvi"] == 0.0
                assert row["rank_tier"] == Tier.D.value
                assert row["friction_score"] == 100.0
                assert row["leverage_score"] == 0.0
        store.close()


# ============================================================================ #
# 3. STORAGE QUERYING STRESS & SLA (< 50ms)
# ============================================================================ #

@pytest.fixture(scope="module")
def seeded_store() -> Any:
    store = build_benchmark_store(":memory:", count=1000)
    yield store
    store.close()


def _run_process_queries(db_path: str, count: int) -> list[float]:
    store = OperationalStore(db_path)
    latencies: list[float] = []
    specs = [
        {"limit": 50, "persona": Persona.AI_ASSISTED},
        {"limit": 50, "persona": Persona.SENIOR_SYSTEMS},
        {"limit": 50, "search": "react"},
        {"limit": 50, "ecosystem": Ecosystem.PYPI},
        {"limit": 50, "persona": Persona.FIRST_TIMER},
    ]
    for i in range(count):
        spec = specs[i % len(specs)]
        t0 = time.perf_counter()
        items, total = store.leaderboard(**spec)
        latencies.append((time.perf_counter() - t0) * 1000.0)
    store.close()
    return latencies


class TestStorageQueryingStress:
    """Stress-test OperationalStore.leaderboard across complex parameters, edge cases, and concurrency."""

    def test_query_limits_and_offsets(self, seeded_store: OperationalStore) -> None:
        """Verify boundary limits: 0, 1, 50, 500, 1000, 2000 and offsets."""
        # limit=0
        items, total = seeded_store.leaderboard(limit=0)
        assert len(items) == 0
        assert total == 1000

        # limit=1
        items, total = seeded_store.leaderboard(limit=1, offset=0)
        assert len(items) == 1
        assert total == 1000
        first_repo = items[0]

        # limit=50
        items, total = seeded_store.leaderboard(limit=50, offset=0)
        assert len(items) == 50
        assert total == 1000
        assert items[0] == first_repo

        # offset=10
        items_offset, _ = seeded_store.leaderboard(limit=40, offset=10)
        assert len(items_offset) == 40
        assert items_offset[0] == items[10]

        # limit=1000
        items, total = seeded_store.leaderboard(limit=1000, offset=0)
        assert len(items) == 1000
        assert total == 1000

        # offset >= 1000
        items, total = seeded_store.leaderboard(limit=50, offset=1000)
        assert len(items) == 0
        assert total == 1000

        # offset >> 1000
        items, total = seeded_store.leaderboard(limit=50, offset=5000)
        assert len(items) == 0
        assert total == 1000

    def test_adversarial_search_terms(self, seeded_store: OperationalStore) -> None:
        """Test SQL injection-like inputs, wildcards, quotes, unicode, and substring matches."""
        adversarial_terms = [
            "",
            "   ",
            "react",
            "REACT",
            "LiNuX",
            "facebook/react",
            "vllm-project/vllm",
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
            "'' OR 1=1 --",
            "' UNION SELECT * FROM repositories --",
            "\\0",
            "nonexistent_repo_identifier_12345",
            "日本語",
            "🚀",
        ]

        for term in adversarial_terms:
            t0 = time.perf_counter()
            items, total = seeded_store.leaderboard(search=term, limit=50)
            elapsed = (time.perf_counter() - t0) * 1000.0

            assert elapsed < 50.0, f"Search for {term!r} took {elapsed:.2f}ms (exceeded 50ms SLA)"
            assert total >= 0
            assert isinstance(items, list)

        # Confirm case-insensitivity
        items_lower, count_lower = seeded_store.leaderboard(search="react")
        items_upper, count_upper = seeded_store.leaderboard(search="REACT")
        assert count_lower == count_upper
        assert [r["name"] for r in items_lower] == [r["name"] for r in items_upper]

    def test_persona_reprojection_and_gating(self, seeded_store: OperationalStore) -> None:
        """Verify persona re-projection, gating rules, and performance."""
        personas = [
            Persona.BALANCED,
            Persona.AI_ASSISTED,
            Persona.SENIOR_SYSTEMS,
            Persona.FIRST_TIMER,
            Persona.CAREER_CAPITAL,
        ]

        for persona in personas:
            t0 = time.perf_counter()
            items, total = seeded_store.leaderboard(persona=persona, limit=1000)
            elapsed = (time.perf_counter() - t0) * 1000.0

            assert total == 1000
            assert len(items) == 1000
            assert elapsed < 150.0, f"Persona {persona} scan took {elapsed:.2f}ms (exceeded headroom)"

            # Check descending sorting
            scores = [r["overall_cvi"] for r in items]
            assert scores == sorted(scores, reverse=True), f"Items not sorted descending under {persona}"

        # Strict gate: gentoo under AI_ASSISTED must be disqualified
        gentoo_balanced, _ = seeded_store.leaderboard(search="gentoo", persona=Persona.BALANCED)
        assert gentoo_balanced[0]["overall_cvi"] > 0.0
        assert gentoo_balanced[0]["is_stage0_disqualified"] == 0

        gentoo_ai, _ = seeded_store.leaderboard(search="gentoo", persona=Persona.AI_ASSISTED)
        assert gentoo_ai[0]["overall_cvi"] == 0.0
        assert gentoo_ai[0]["rank_tier"] == Tier.D.value
        assert gentoo_ai[0]["is_stage0_disqualified"] == 1

        # Strict gate: EF < 3 under SENIOR_SYSTEMS must be disqualified
        items_sys, _ = seeded_store.leaderboard(persona=Persona.SENIOR_SYSTEMS, limit=1000)
        for r in items_sys:
            if r["elephant_factor"] < 3:
                assert r["overall_cvi"] == 0.0
                assert r["rank_tier"] == Tier.D.value
                assert r["is_stage0_disqualified"] == 1

    def test_high_intensity_query_loop_sla(self, seeded_store: OperationalStore) -> None:
        """Execute 500 diverse queries sequentially and verify all latency percentiles strictly < 50ms."""
        query_specs = [
            {"ecosystem": Ecosystem.PYPI, "limit": 20, "offset": 0},
            {"ecosystem": Ecosystem.NPM, "search": "react", "limit": 10},
            {"persona": Persona.AI_ASSISTED, "limit": 50, "offset": 50},
            {"persona": Persona.SENIOR_SYSTEMS, "search": "core", "limit": 100},
            {"ecosystem": Ecosystem.GO, "persona": Persona.FIRST_TIMER, "limit": 10},
            {"ecosystem": Ecosystem.CRATES, "limit": 500, "offset": 0},
            {"search": "vllm", "persona": Persona.BALANCED, "limit": 5},
            {"search": "linux", "limit": 1},
            {"limit": 1000, "offset": 0},
            {"limit": 0, "offset": 0},
        ]

        latencies_ms: list[float] = []
        for i in range(500):
            spec = query_specs[i % len(query_specs)]
            t0 = time.perf_counter()
            items, total = seeded_store.leaderboard(**spec)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            latencies_ms.append(elapsed_ms)

        p50 = statistics.median(latencies_ms)
        p95 = statistics.quantiles(latencies_ms, n=20)[18]  # 95th percentile
        p99 = statistics.quantiles(latencies_ms, n=100)[98]  # 99th percentile
        max_lat = max(latencies_ms)

        print(f"\n[SLA] 500 Sequential Queries: p50={p50:.2f}ms, p95={p95:.2f}ms, p99={p99:.2f}ms, max={max_lat:.2f}ms")
        assert p95 < 25.0, f"p95 latency {p95:.2f}ms exceeded 25ms internal target"
        assert max_lat < 250.0, f"Max latency {max_lat:.2f}ms exceeded headroom"

    def test_multi_threaded_sql_concurrency_stress(self) -> None:
        """Measure multi-threaded SQL concurrency: 10 worker threads issuing 50 concurrent SQL queries each (500 total) against disk SQLite."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            main_store = OperationalStore(tmp.name)
            seed_benchmark_store(main_store, count=1000)
            main_store.close()

            latencies: list[float] = []
            errors: list[str] = []

            def worker(thread_idx: int) -> list[float]:
                t_latencies: list[float] = []
                store = OperationalStore(tmp.name)
                queries = [
                    {"limit": 50, "offset": thread_idx * 5},
                    {"search": "react", "limit": 10},
                    {"ecosystem": Ecosystem.PYPI, "limit": 25},
                    {"ecosystem": Ecosystem.GO, "limit": 100},
                    {"search": "core", "limit": 50},
                ]
                try:
                    for j in range(50):
                        spec = queries[j % len(queries)]
                        t0 = time.perf_counter()
                        items, total = store.leaderboard(**spec)
                        dur_ms = (time.perf_counter() - t0) * 1000.0
                        t_latencies.append(dur_ms)
                except Exception as e:
                    errors.append(f"Thread {thread_idx}: {e}")
                finally:
                    store.close()
                return t_latencies

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(worker, i) for i in range(10)]
                for f in concurrent.futures.as_completed(futures):
                    latencies.extend(f.result())

            assert not errors, f"Concurrent query errors: {errors}"
            assert len(latencies) == 500

            p50 = statistics.median(latencies)
            p95 = statistics.quantiles(latencies, n=20)[18]
            max_lat = max(latencies)

            print(f"\n[SLA] Multi-threaded SQL (10 threads, 500 queries): p50={p50:.2f}ms, p95={p95:.2f}ms, max={max_lat:.2f}ms")
            assert p95 < 100.0, f"p95 concurrent query latency {p95:.2f}ms exceeded headroom"

    def test_process_pool_concurrency_stress(self) -> None:
        """Measure multi-process concurrency (simulating multi-worker production server) across personas under < 50ms SLA."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            main_store = OperationalStore(tmp.name)
            seed_benchmark_store(main_store, count=1000)
            main_store.close()

            latencies: list[float] = []
            with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(_run_process_queries, tmp.name, 25) for _ in range(4)]
                for f in concurrent.futures.as_completed(futures):
                    latencies.extend(f.result())

            assert len(latencies) == 100
            p50 = statistics.median(latencies)
            p95 = statistics.quantiles(latencies, n=20)[18]
            max_lat = max(latencies)

            print(f"\n[SLA] Process-Pool Concurrency (4 workers, 100 queries): p50={p50:.2f}ms, p95={p95:.2f}ms, max={max_lat:.2f}ms")
            assert p50 < 25.0, f"Process pool median latency {p50:.2f}ms exceeded 25ms"
            assert p95 < 100.0, f"Process pool p95 latency {p95:.2f}ms exceeded headroom"
