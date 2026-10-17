"""Tier 1: Comprehensive Feature Coverage E2E Tests.

Verifies >=5 test cases per core feature:
1. Benchmark Catalog (1,000 repos across 6 ecosystems, 4 quadrants, 6 tiers, <2s seeder SLA)
2. Canonical Parameter Hierarchy & WAM Weights (25/25/20/15/15, 15 metrics, leverage/friction)
"""

from __future__ import annotations

import time

import pytest

from tests.fixtures.synthetic_catalog import build_benchmark_store
from porchlamp.constants import (
    FRICTION_WEIGHTS,
    LEVERAGE_WEIGHTS,
    PARAM_WEIGHTS,
    PILLAR_WEIGHTS,
)
from porchlamp.lakehouse import OperationalStore


# =========================================================================== #
# Feature 1: Benchmark Catalog (>=5 tests)
# =========================================================================== #
class TestBenchmarkCatalogFeature:
    """Empirical catalog of 1,000 top open-source repositories."""

    def test_catalog_exact_total_count(self, store: OperationalStore) -> None:
        """Requirement R1: Exactly 1,000 repositories indexed in the store."""
        cur = store.connection.execute("SELECT COUNT(*) FROM repositories")
        total = cur.fetchone()[0]
        assert total == 1000, f"Expected 1,000 repositories in store, found {total}"

    def test_catalog_ecosystem_distribution(self, store: OperationalStore) -> None:
        """Requirement R1: Catalog spans 6 major ecosystems (PyPI, npm, Crates, Go, Maven, Systems)."""
        cur = store.connection.execute(
            "SELECT ecosystem, COUNT(*) FROM repositories GROUP BY ecosystem"
        )
        counts = dict(cur.fetchall())
        expected_ecosystems = {"pypi", "npm", "crates", "go", "maven", "systems"}
        assert set(counts.keys()) == expected_ecosystems
        for eco, cnt in counts.items():
            assert cnt >= 100, f"Ecosystem {eco} has only {cnt} repos, expected >= 100"
        assert sum(counts.values()) == 1000

    def test_catalog_quadrant_dispersion(self, store: OperationalStore) -> None:
        """Requirement R1: Catalog spans all 4 quadrants (Sweet Spot, Crucible, Nursery, Graveyard)."""
        counts = store.count_by_quadrant()
        assert set(counts.keys()) == {"Sweet Spot", "Crucible", "Nursery", "Graveyard"}
        for quad, count in counts.items():
            assert count >= 100, f"Quadrant {quad} under-represented with {count} repos"
        assert sum(counts.values()) == 1000

    def test_catalog_tier_dispersion(self, store: OperationalStore) -> None:
        """Requirement R1: Catalog spans all 6 cardinal tiers (Tier S+ down to Tier D)."""
        cur = store.connection.execute(
            "SELECT rank_tier, COUNT(*) FROM repositories GROUP BY rank_tier"
        )
        tiers = dict(cur.fetchall())
        for expected_tier in ("Tier S+", "Tier S", "Tier A", "Tier B", "Tier C", "Tier D"):
            assert expected_tier in tiers, f"Missing tier {expected_tier} in benchmark catalog"
            assert tiers[expected_tier] > 0, f"Tier {expected_tier} has 0 repositories"
        assert sum(tiers.values()) == 1000

    def test_catalog_seeder_speed_sla(self) -> None:
        """Acceptance Criteria: Seeding 1,000 repositories completes in under 2 seconds (<2s SLA)."""
        t0 = time.perf_counter()
        store = build_benchmark_store(":memory:", count=1000)
        elapsed = time.perf_counter() - t0
        cur = store.connection.execute("SELECT COUNT(*) FROM repositories")
        assert cur.fetchone()[0] == 1000
        assert elapsed < 2.0, f"Seeding 1,000 repositories took {elapsed:.3f}s (exceeds 2.0s SLA)"

    def test_catalog_realistic_metric_distributions(self, store: OperationalStore) -> None:
        """Catalog repositories have realistic distributions of stars, downloads, and dependents."""
        cur = store.connection.execute(
            "SELECT MIN(stars), MAX(stars), AVG(stars), "
            "MIN(monthly_downloads), MAX(monthly_downloads), "
            "MIN(dependents_count), MAX(dependents_count) FROM repositories"
        )
        row = cur.fetchone()
        min_stars, max_stars, avg_stars = row[0], row[1], row[2]
        max_dl = row[4]
        max_dep = row[6]

        assert min_stars >= 0
        assert max_stars >= 50000, f"Max stars {max_stars} should reflect top OSS landmark repos"
        assert avg_stars > 1000, f"Average stars {avg_stars} is unrealistically low"
        assert max_dl > 1_000_000, "Max monthly downloads should exceed 1M"
        assert max_dep > 1000, "Max dependents should exceed 1,000"


# =========================================================================== #
# Feature 2: Canonical Parameter Hierarchy & WAM (>=5 tests)
# =========================================================================== #
class TestCanonicalWeightsFeature:
    """Canonical parameter priority ordering and WAM mathematics."""

    def test_canonical_pillar_priority_weights(self) -> None:
        """Requirement R2: P1 25%, P2 25%, P3 20%, P4 15%, P5 15% canonical weights."""
        assert PILLAR_WEIGHTS["responsiveness"] == 0.25
        assert PILLAR_WEIGHTS["criticality"] == 0.25
        assert PILLAR_WEIGHTS["governance"] == 0.20
        assert PILLAR_WEIGHTS["rigor"] == 0.15
        assert PILLAR_WEIGHTS["accessibility"] == 0.15
        assert sum(PILLAR_WEIGHTS.values()) == pytest.approx(1.0, abs=1e-6)

    def test_canonical_underlying_15_parameters_weights(self) -> None:
        """Requirement R2: 15 empirical parameters with exact internal pillar allocations."""
        assert sum(PARAM_WEIGHTS["responsiveness"].values()) == pytest.approx(1.0, abs=1e-6)
        assert sum(PARAM_WEIGHTS["criticality"].values()) == pytest.approx(1.0, abs=1e-6)
        assert sum(PARAM_WEIGHTS["governance"].values()) == pytest.approx(1.0, abs=1e-6)
        assert sum(PARAM_WEIGHTS["rigor"].values()) == pytest.approx(1.0, abs=1e-6)
        assert sum(PARAM_WEIGHTS["accessibility"].values()) == pytest.approx(1.0, abs=1e-6)

        # Verify key individual weights per specification
        assert PARAM_WEIGHTS["responsiveness"]["ttfhr"] == 0.35
        assert PARAM_WEIGHTS["responsiveness"]["mr_first"] == 0.25
        assert PARAM_WEIGHTS["responsiveness"]["ghost_rate"] == 0.20
        assert PARAM_WEIGHTS["responsiveness"]["ttm_p90"] == 0.20

    def test_wam_scoring_mathematical_consistency(self, store: OperationalStore) -> None:
        """Overall Porchlamp is the exact canonical weighted sum of the 5 pillar scores for non-gated repos."""
        cur = store.connection.execute(
            "SELECT score_responsiveness, score_criticality, score_governance, "
            "score_rigor_ai, score_accessibility, overall_porchlamp "
            "FROM repositories WHERE is_stage0_disqualified = 0 LIMIT 50"
        )
        for p1, p2, p3, p4, p5, stored_porchlamp in cur.fetchall():
            expected_porchlamp = 0.25 * p1 + 0.25 * p2 + 0.20 * p3 + 0.15 * p4 + 0.15 * p5
            assert stored_porchlamp == pytest.approx(expected_porchlamp, abs=0.15)

    def test_leverage_formula_decomposition(self, store: OperationalStore) -> None:
        """Leverage (Y-axis) decomposes into: 0.50*P2 + 0.30*P4 + 0.20*P3."""
        assert LEVERAGE_WEIGHTS["criticality"] == 0.50
        assert LEVERAGE_WEIGHTS["rigor"] == 0.30
        assert LEVERAGE_WEIGHTS["governance"] == 0.20

        cur = store.connection.execute(
            "SELECT score_criticality, score_rigor_ai, score_governance, leverage_score "
            "FROM repositories WHERE is_stage0_disqualified = 0 LIMIT 50"
        )
        for p2, p4, p3, stored_y in cur.fetchall():
            expected_y = 0.50 * p2 + 0.30 * p4 + 0.20 * p3
            assert stored_y == pytest.approx(expected_y, abs=0.15)

    def test_friction_formula_decomposition(self, store: OperationalStore) -> None:
        """Friction (X-axis) decomposes into: 0.50*(100-P1) + 0.30*(100-P3) + 0.20*(100-P5)."""
        assert FRICTION_WEIGHTS["responsiveness"] == 0.50
        assert FRICTION_WEIGHTS["governance"] == 0.30
        assert FRICTION_WEIGHTS["accessibility"] == 0.20

        cur = store.connection.execute(
            "SELECT score_responsiveness, score_governance, score_accessibility, friction_score "
            "FROM repositories WHERE is_stage0_disqualified = 0 LIMIT 50"
        )
        for p1, p3, p5, stored_x in cur.fetchall():
            expected_x = 0.50 * (100.0 - p1) + 0.30 * (100.0 - p3) + 0.20 * (100.0 - p5)
            assert stored_x == pytest.approx(expected_x, abs=0.15)
