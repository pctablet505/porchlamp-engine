"""Tests for tests.fixtures.synthetic_catalog (W4-1: moved from
cvi/benchmark_data.py -- never importable from cvi/, per
04-ARCHITECTURE.md section 2): Top 1,000 benchmark dataset and seeder.

Verifies:
- Exactly 1,000 repositories across 6 ecosystems (pypi, npm, crates, go, maven, systems).
- Exact quadrant distribution (35% Sweet Spot, 15% Crucible, 25% Nursery, 25% Graveyard).
- Ground truth reference repositories (vllm, keras-hub, linux, gentoo).
- Fast initialization SLA (<2.0s).
- Deep-dive scorecard payload generation for benchmark repos.
"""

from __future__ import annotations

import collections
import time
from datetime import datetime

from cvi.lakehouse import OperationalStore
from cvi.models import Ecosystem, Quadrant, Tier
from tests.fixtures.synthetic_catalog import (
    build_benchmark_store,
    generate_synthetic_pr_events,
    get_benchmark_catalog,
    seed_benchmark_store,
)


def test_catalog_total_and_slice() -> None:
    catalog = get_benchmark_catalog(1000)
    assert len(catalog) == 1000
    slice_10 = get_benchmark_catalog(10)
    assert len(slice_10) == 10
    slice_50 = get_benchmark_catalog(50)
    assert len(slice_50) == 50


def test_catalog_unique_repo_ids() -> None:
    catalog = get_benchmark_catalog(1000)
    repo_ids = [f"{r['owner']}/{r['name']}" for r in catalog]
    assert len(repo_ids) == len(set(repo_ids)) == 1000


def test_ecosystem_distribution() -> None:
    catalog = get_benchmark_catalog(1000)
    counts = collections.Counter(r["ecosystem"] for r in catalog)
    assert counts[Ecosystem.PYPI] == 175
    assert counts[Ecosystem.NPM] == 175
    assert counts[Ecosystem.CRATES] == 165
    assert counts[Ecosystem.GO] == 165
    assert counts[Ecosystem.MAVEN] == 160
    assert counts[Ecosystem.SYSTEMS] == 160
    assert sum(counts.values()) == 1000


def test_quadrant_distribution() -> None:
    catalog = get_benchmark_catalog(1000)
    counts = collections.Counter(r["quadrant"] for r in catalog)
    assert counts[Quadrant.SWEET_SPOT] == 350
    assert counts[Quadrant.CRUCIBLE] == 150
    assert counts[Quadrant.NURSERY] == 250
    assert counts[Quadrant.GRAVEYARD] == 250
    assert sum(counts.values()) == 1000


def test_tier_distribution_within_quadrants() -> None:
    catalog = get_benchmark_catalog(1000)
    for r in catalog:
        quad = r["quadrant"]
        tier = r["tier"]
        if quad == Quadrant.SWEET_SPOT:
            assert tier in (Tier.S_PLUS, Tier.S, Tier.A)
        elif quad == Quadrant.CRUCIBLE:
            assert tier in (Tier.A, Tier.B)
        elif quad == Quadrant.NURSERY:
            assert tier in (Tier.B, Tier.C)
        elif quad == Quadrant.GRAVEYARD:
            assert tier in (Tier.C, Tier.D)


def test_bell_shaped_tier_distribution() -> None:
    catalog = get_benchmark_catalog(1000)
    tier_counts = collections.Counter(r["tier"] for r in catalog)
    assert tier_counts[Tier.S_PLUS] > 0
    assert tier_counts[Tier.S] > tier_counts[Tier.S_PLUS]
    assert tier_counts[Tier.A] > tier_counts[Tier.S]
    assert tier_counts[Tier.B] > 150
    assert tier_counts[Tier.C] > 150
    assert tier_counts[Tier.D] > 50


def test_ground_truth_vllm() -> None:
    catalog = get_benchmark_catalog(1000)
    vllm = next(r for r in catalog if r["owner"] == "vllm-project" and r["name"] == "vllm")
    assert vllm["ecosystem"] == Ecosystem.PYPI
    assert vllm["quadrant"] == Quadrant.SWEET_SPOT
    assert vllm["tier"] == Tier.S
    assert vllm["pillars"] == (92.0, 88.0, 90.0, 86.0, 86.0)
    assert vllm["legal_ip_model"].value == "DCO"
    assert vllm["elephant_factor"] >= 4
    assert vllm["is_gated"] is False


def test_ground_truth_hub() -> None:
    catalog = get_benchmark_catalog(1000)
    hub = next(r for r in catalog if r["owner"] == "keras-team" and r["name"] == "keras-hub")
    assert hub["ecosystem"] == Ecosystem.PYPI
    assert hub["quadrant"] == Quadrant.GRAVEYARD
    assert hub["tier"] == Tier.D
    assert hub["elephant_factor"] == 1
    assert hub["is_gated"] is True


def test_ground_truth_react() -> None:
    catalog = get_benchmark_catalog(1000)
    react = next(r for r in catalog if r["owner"] == "facebook" and r["name"] == "react")
    assert react["ecosystem"] == Ecosystem.NPM
    assert react["quadrant"] == Quadrant.SWEET_SPOT
    assert react["tier"] == Tier.S_PLUS
    assert react["dependents_count"] >= 200000
    assert react["legal_ip_model"].value == "FOUNDATION_CLA"


def test_ground_truth_kubernetes() -> None:
    catalog = get_benchmark_catalog(1000)
    k8s = next(r for r in catalog if r["owner"] == "kubernetes" and r["name"] == "kubernetes")
    assert k8s["ecosystem"] == Ecosystem.GO
    assert k8s["quadrant"] == Quadrant.CRUCIBLE
    assert k8s["elephant_factor"] >= 4
    assert k8s["legal_ip_model"].value == "FOUNDATION_CLA"


def test_ground_truth_linux() -> None:
    catalog = get_benchmark_catalog(1000)
    linux = next(r for r in catalog if r["owner"] == "torvalds" and r["name"] == "linux")
    assert linux["ecosystem"] == Ecosystem.SYSTEMS
    assert linux["quadrant"] == Quadrant.CRUCIBLE
    assert linux["off_platform_reviews"] is True
    assert linux["legal_ip_model"].value == "DCO"
    assert linux["is_gated"] is False


def test_ground_truth_gentoo() -> None:
    catalog = get_benchmark_catalog(1000)
    gentoo = next(r for r in catalog if r["owner"] == "gentoo" and r["name"] == "gentoo")
    assert gentoo["ecosystem"] == Ecosystem.SYSTEMS
    assert gentoo["hard_ai_ban"] is True
    assert gentoo["ai_posture"].value == "PROSCRIBED"
    assert gentoo["tier"] == Tier.A


def test_benchmark_module_exports_and_generate() -> None:
    from tests.fixtures.synthetic_catalog import (
        generate_benchmark_catalog,
    )
    cat = generate_benchmark_catalog()
    assert len(cat) == 1000
    assert cat[0]["owner"] is not None


def test_synthetic_pr_events_gated() -> None:
    now = datetime(2026, 9, 11, 12, 0, 0)
    events = generate_synthetic_pr_events("test-owner", "test-gated", p1=20.0, now=now, is_gated=True)
    assert len(events) == 4
    # 3 ghost PRs (> 14 days old, unmerged, 0 comments)
    ghosts = [e for e in events if e.maintainer_review_comments == 0 and e.merged_at is None]
    assert len(ghosts) == 3


def test_synthetic_pr_events_responsiveness_tiers() -> None:
    now = datetime(2026, 9, 11, 12, 0, 0)
    high = generate_synthetic_pr_events("o", "high", p1=80.0, now=now, is_gated=False)
    assert len(high) == 7
    assert high[0].response_latency_hours == 2.0

    med = generate_synthetic_pr_events("o", "med", p1=50.0, now=now, is_gated=False)
    assert len(med) == 7
    assert med[0].response_latency_hours == 12.0

    low = generate_synthetic_pr_events("o", "low", p1=20.0, now=now, is_gated=False)
    assert len(low) == 7
    assert low[0].response_latency_hours == 36.0


def test_seed_benchmark_store_and_deep_dive() -> None:
    store = OperationalStore()
    t0 = time.perf_counter()
    seeded = seed_benchmark_store(store, count=1000)
    elapsed = time.perf_counter() - t0

    assert seeded == 1000
    assert elapsed < 2.0, f"Seeding took {elapsed:.2f}s, expected < 2.0s"

    cur = store.connection.execute("SELECT COUNT(*) FROM repositories")
    assert cur.fetchone()[0] == 1000

    cur_dd = store.connection.execute(
        "SELECT COUNT(*) FROM repositories WHERE deep_dive_json IS NOT NULL"
    )
    assert cur_dd.fetchone()[0] == 1000

    # Ground truth in DB
    vllm = store.get_repo("vllm-project", "vllm")
    assert vllm is not None
    assert vllm["overall_cvi"] == 88.8
    assert vllm["rank_tier"] == "Tier S"
    assert vllm["matrix_quadrant"] == "Sweet Spot"

    hub = store.get_repo("keras-team", "keras-hub")
    assert hub is not None
    assert hub["overall_cvi"] == 0.0
    assert hub["rank_tier"] == "Tier D"
    assert hub["is_stage0_disqualified"] == 1


def test_build_benchmark_store_helper() -> None:
    store = build_benchmark_store(":memory:", count=15)
    cur = store.connection.execute("SELECT COUNT(*) FROM repositories")
    assert cur.fetchone()[0] == 15


def test_pinned_scorers() -> None:
    from tests.fixtures.synthetic_catalog import _pinned_scorers
    scorers = _pinned_scorers((10.0, 20.0, 30.0, 40.0, 50.0))
    assert scorers["responsiveness"]() == 10.0
    assert scorers["criticality"]() == 20.0
    assert scorers["governance"]() == 30.0
    assert scorers["rigor"]() == 40.0
    assert scorers["accessibility"]() == 50.0


def test_synthetic_collision_resolution(monkeypatch) -> None:
    import tests.fixtures.synthetic_catalog as bd
    collision_repo = (
        "py-data", "tensor-opt-core", bd.Ecosystem.PYPI, bd.Quadrant.SWEET_SPOT, bd.Tier.S,
        (90.0, 90.0, 90.0, 90.0, 90.0), 1000, 100, 1000, 10, 3,
        bd.LegalIPModel.DCO, bd.AIPosture.AD_HOC, False, False, False
    )
    new_prominents = tuple([collision_repo] + list(bd._PROMINENT_REPOSITORIES))
    monkeypatch.setattr(bd, "_PROMINENT_REPOSITORIES", new_prominents)
    cat = bd._generate_synthetic_catalog()
    assert len(cat) == 1000
