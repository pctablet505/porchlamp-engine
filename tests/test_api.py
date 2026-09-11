"""Tests for the CVI REST API layer (spec 12).

Seeds an in-memory ``OperationalStore`` via ``upsert_composite`` and
patches the raw ``stars`` column with direct SQL (ingestion-layer
territory — see the ``cvi.lakehouse`` module docstring) so the
``min_stars`` filter paths are actually exercised.

Oracle style: exact expected values everywhere; a test that is easy to
pass is a defect.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from cvi.api import create_app
from cvi.lakehouse import OperationalStore
from cvi.models import CVICompositeResult, Ecosystem, PillarScores, Quadrant, Tier


NODE_KEYS = {
    "repo_id",
    "owner",
    "name",
    "x",
    "y",
    "cvi",
    "tier",
    "quadrant",
    "size",
    "is_gated",
}
ALL_QUADRANTS = {"Sweet Spot", "Crucible", "Nursery", "Graveyard"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def make_result(
    owner: str,
    name: str,
    overall_cvi: float,
    rank_tier: Tier,
    matrix_quadrant: Quadrant,
    leverage_score: float,
    friction_score: float,
    ecosystem: Ecosystem = Ecosystem.PYPI,
    is_stage0_disqualified: bool = False,
    disqualification_reason: str | None = None,
) -> CVICompositeResult:
    return CVICompositeResult(
        owner=owner,
        name=name,
        ecosystem=ecosystem,
        overall_cvi=overall_cvi,
        rank_tier=rank_tier,
        matrix_quadrant=matrix_quadrant,
        leverage_score=leverage_score,
        friction_score=friction_score,
        is_stage0_disqualified=is_stage0_disqualified,
        disqualification_reason=disqualification_reason,
    )


def make_pillars(resp, crit, gov, rig, acc) -> PillarScores:
    return PillarScores(
        responsiveness=resp,
        criticality=crit,
        governance=gov,
        rigor=rig,
        accessibility=acc,
    )


def set_stars(store: OperationalStore, owner: str, name: str, stars: int) -> None:
    store.connection.execute(
        "UPDATE repositories SET stars = ? WHERE owner = ? AND name = ?",
        (stars, owner, name),
    )
    store.connection.commit()


def seed_matrix_store() -> OperationalStore:
    """Five repos: covers every filter axis and quadrant (incl. gated)."""
    store = OperationalStore()
    seed = [
        # owner, name, eco, cvi, tier, quadrant, leverage, friction, pillars, stars, gated
        ("alpha", "omega", Ecosystem.PYPI, 92.5, Tier.S_PLUS, Quadrant.SWEET_SPOT,
         80.0, 20.0, make_pillars(90, 80, 70, 60, 50), 150, False, None),
        ("beta", "omega", Ecosystem.PYPI, 88.0, Tier.S, Quadrant.CRUCIBLE,
         90.0, 70.0, make_pillars(10, 90, 20, 90, 30), 99, False, None),
        ("gamma", "omega", Ecosystem.NPM, 75.0, Tier.A, Quadrant.NURSERY,
         30.0, 10.0, make_pillars(80, 20, 10, 30, 70), 500, False, None),
        ("delta", "omega", Ecosystem.PYPI, 60.0, Tier.B, Quadrant.GRAVEYARD,
         20.0, 60.0, make_pillars(40, 30, 50, 20, 40), 100, False, None),
        ("gated", "omega", Ecosystem.PYPI, 0.0, Tier.D, Quadrant.GRAVEYARD,
         0.0, 100.0, make_pillars(0, 0, 0, 0, 0), 1000, True, "ghost rate 0.4"),
    ]
    for owner, name, eco, cvi, tier, quad, lev, fric, pillars, stars, gated, reason in seed:
        store.upsert_composite(
            make_result(
                owner,
                name,
                cvi,
                tier,
                quad,
                lev,
                fric,
                ecosystem=eco,
                is_stage0_disqualified=gated,
                disqualification_reason=reason,
            ),
            pillar_scores=pillars,
        )
        set_stars(store, owner, name, stars)
    return store


def seed_reproject_store() -> OperationalStore:
    """Four repos whose old ranking (by overall_cvi) intentionally
    disagrees with the criticality-heavy re-projection:

    - one/a:   pillars (100, 0, 0, 0, 0),  cvi 50 -> old rank 1
    - two/b:   pillars (0, 100, 0, 0, 0),  cvi 40 -> old rank 2
    - three/c: pillars (0, 0, 50, 0, 0),   cvi 30 -> old rank 3
    - four/d:  pillars (0, 0, 50, 0, 0),   cvi 20 -> old rank 4
    """
    store = OperationalStore()
    seed = [
        ("one", "a", 50.0, Tier.B, Quadrant.SWEET_SPOT, 80.0, 10.0,
         make_pillars(100, 0, 0, 0, 0)),
        ("two", "b", 40.0, Tier.C, Quadrant.CRUCIBLE, 90.0, 70.0,
         make_pillars(0, 100, 0, 0, 0)),
        ("three", "c", 30.0, Tier.D, Quadrant.NURSERY, 20.0, 10.0,
         make_pillars(0, 0, 50, 0, 0)),
        ("four", "d", 20.0, Tier.D, Quadrant.NURSERY, 10.0, 30.0,
         make_pillars(0, 0, 50, 0, 0)),
    ]
    for owner, name, cvi, tier, quad, lev, fric, pillars in seed:
        store.upsert_composite(
            make_result(owner, name, cvi, tier, quad, lev, fric),
            pillar_scores=pillars,
        )
    return store


def open_client(seed: Callable[[], OperationalStore]) -> TestClient:
    """Enter a TestClient and rebuild the store on the ASGI portal thread.

    sqlite3 connections are thread-bound, and starlette's TestClient
    dispatches requests onto its blocking-portal thread, so a store
    created on the test thread cannot be touched by the app. The store
    (and its seeding) therefore happens inside the portal via
    ``client.portal.call``; ``create_app()``'s default store is simply
    replaced and left to GC (it is never used).
    """
    app = create_app()
    client = TestClient(app)
    client.__enter__()
    app.state.store = client.portal.call(seed)
    return client


@pytest.fixture()
def matrix_client() -> TestClient:
    client = open_client(seed_matrix_store)
    try:
        yield client
    finally:
        client.__exit__(None, None, None)


@pytest.fixture()
def reproject_client() -> TestClient:
    client = open_client(seed_reproject_store)
    try:
        yield client
    finally:
        client.__exit__(None, None, None)


# --------------------------------------------------------------------------- #
# healthz
# --------------------------------------------------------------------------- #
def test_healthz(matrix_client) -> None:
    resp = matrix_client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# --------------------------------------------------------------------------- #
# create_app default store
# --------------------------------------------------------------------------- #
def test_create_app_default_store_serves_empty_matrix() -> None:
    # store built on the portal thread stands in for create_app()'s
    # default (see open_client docstring re: sqlite thread affinity)
    client = open_client(lambda: OperationalStore())
    try:
        resp = client.get("/v1/cvi/matrix")
        assert resp.status_code == 200
        body = resp.json()
        assert body["nodes"] == []
        assert body["quadrant_counts"] == {key: 0 for key in ALL_QUADRANTS}
    finally:
        client.__exit__(None, None, None)


# --------------------------------------------------------------------------- #
# GET /v1/cvi/matrix
# --------------------------------------------------------------------------- #
class TestMatrix:
    def test_default_params_nodes_and_shape(self, matrix_client) -> None:
        resp = matrix_client.get("/v1/cvi/matrix")
        assert resp.status_code == 200
        body = resp.json()
        nodes = body["nodes"]
        # defaults: min_stars=100 (stars=99 beta excluded), gated excluded
        assert [n["repo_id"] for n in nodes] == [
            "alpha/omega",
            "gamma/omega",
            "delta/omega",
        ]
        for node in nodes:
            assert set(node) == NODE_KEYS
        alpha, gamma, delta = nodes
        # x = friction, y = leverage (spec 12.1 coordinate mapping)
        assert alpha["owner"] == "alpha" and alpha["name"] == "omega"
        assert alpha["repo_id"] == "alpha/omega"
        assert alpha["x"] == 20.0 and alpha["y"] == 80.0
        assert alpha["cvi"] == 92.5
        assert alpha["tier"] == "Tier S+"
        assert alpha["quadrant"] == "Sweet Spot"
        assert alpha["size"] == 150
        assert alpha["is_gated"] == 0
        assert gamma["size"] == 500
        # stars == min_stars boundary is inclusive
        assert delta["size"] == 100

    def test_default_excludes_low_star_and_gated(self, matrix_client) -> None:
        ids = [n["repo_id"] for n in matrix_client.get("/v1/cvi/matrix").json()["nodes"]]
        assert "beta/omega" not in ids  # stars=99 < 100
        assert "gated/omega" not in ids  # stage-0 disqualified

    def test_quadrant_counts_always_all_four_keys(self, matrix_client) -> None:
        counts = matrix_client.get("/v1/cvi/matrix").json()["quadrant_counts"]
        assert set(counts) == ALL_QUADRANTS
        # counts reflect the table (no stars/gating filter): all 5 repos
        assert counts == {"Sweet Spot": 1, "Crucible": 1, "Nursery": 1, "Graveyard": 2}

    def test_ecosystem_filter(self, matrix_client) -> None:
        body = matrix_client.get("/v1/cvi/matrix", params={"ecosystem": "pypi"}).json()
        assert [n["repo_id"] for n in body["nodes"]] == ["alpha/omega", "delta/omega"]
        assert body["quadrant_counts"] == {
            "Sweet Spot": 1,
            "Crucible": 1,
            "Nursery": 0,
            "Graveyard": 2,
        }

    def test_ecosystem_no_match(self, matrix_client) -> None:
        body = matrix_client.get("/v1/cvi/matrix", params={"ecosystem": "crates"}).json()
        assert body["nodes"] == []
        assert body["quadrant_counts"] == {key: 0 for key in ALL_QUADRANTS}

    def test_min_stars_filter_values(self, matrix_client) -> None:
        high = matrix_client.get(
            "/v1/cvi/matrix", params={"min_stars": 500}
        ).json()["nodes"]
        assert [n["repo_id"] for n in high] == ["gamma/omega"]
        zero = matrix_client.get(
            "/v1/cvi/matrix", params={"min_stars": 0}
        ).json()["nodes"]
        # gated still excluded (default), everything else passes
        assert [n["repo_id"] for n in zero] == [
            "alpha/omega",
            "beta/omega",
            "gamma/omega",
            "delta/omega",
        ]

    def test_exclude_black_holes_off_includes_gated(self, matrix_client) -> None:
        body = matrix_client.get(
            "/v1/cvi/matrix", params={"exclude_black_holes": "false"}
        ).json()
        nodes = body["nodes"]
        assert [n["repo_id"] for n in nodes] == [
            "alpha/omega",
            "gamma/omega",
            "delta/omega",
            "gated/omega",
        ]
        assert nodes[-1]["is_gated"] == 1
        assert nodes[-1]["cvi"] == 0.0
        assert nodes[-1]["size"] == 1000

    def test_domain_is_reserved_noop(self, matrix_client) -> None:
        plain = matrix_client.get("/v1/cvi/matrix").json()
        with_domain = matrix_client.get(
            "/v1/cvi/matrix", params={"domain": "ai_ml"}
        ).json()
        assert with_domain == plain

    def test_persona_is_reserved_noop(self, matrix_client) -> None:
        plain = matrix_client.get("/v1/cvi/matrix").json()
        for persona in (
            "balanced",
            "first_timer",
            "career_capital",
            "senior_systems",
            "ai_assisted",
        ):
            assert (
                matrix_client.get("/v1/cvi/matrix", params={"persona": persona}).json()
                == plain
            )

    def test_persona_invalid_422(self, matrix_client) -> None:
        resp = matrix_client.get("/v1/cvi/matrix", params={"persona": "vibe_coder"})
        assert resp.status_code == 422
        assert "detail" in resp.json()

    def test_min_stars_negative_422(self, matrix_client) -> None:
        resp = matrix_client.get("/v1/cvi/matrix", params={"min_stars": -1})
        assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# GET /v1/cvi/repos/{owner}/{name}
# --------------------------------------------------------------------------- #
class TestRepoDeepDive:
    def test_happy_path_full_row_plus_pillar_scores(self, matrix_client) -> None:
        resp = matrix_client.get("/v1/cvi/repos/alpha/omega")
        assert resp.status_code == 200
        body = resp.json()
        assert body["owner"] == "alpha"
        assert body["name"] == "omega"
        assert body["ecosystem"] == "pypi"
        assert body["stars"] == 150
        assert body["overall_cvi"] == 92.5
        assert body["rank_tier"] == "Tier S+"
        assert body["matrix_quadrant"] == "Sweet Spot"
        assert body["leverage_score"] == 80.0
        assert body["friction_score"] == 20.0
        assert body["is_stage0_disqualified"] == 0
        assert body["disqualification_reason"] is None
        assert body["last_indexed_at"]  # DB default timestamp applied
        assert body["pillar_scores"] == {
            "responsiveness": 90.0,
            "criticality": 80.0,
            "governance": 70.0,
            "rigor": 60.0,
            "accessibility": 50.0,
        }

    def test_gated_repo_flags(self, matrix_client) -> None:
        body = matrix_client.get("/v1/cvi/repos/gated/omega").json()
        assert body["is_stage0_disqualified"] == 1
        assert body["disqualification_reason"] == "ghost rate 0.4"

    def test_missing_repo_404_json(self, matrix_client) -> None:
        resp = matrix_client.get("/v1/cvi/repos/nobody/nothing")
        assert resp.status_code == 404
        assert "detail" in resp.json()


# --------------------------------------------------------------------------- #
# POST /v1/cvi/reproject
# --------------------------------------------------------------------------- #
class TestReproject:
    #: 0.4*resp + 0.6*crit: inverts old ranks 1 and 2, leaves 3/4 tied at 0.0
    WEIGHTS = {
        "responsiveness": 0.4,
        "criticality": 0.6,
        "governance": 0.0,
        "rigor": 0.0,
        "accessibility": 0.0,
    }

    def test_happy_path_rerank_and_deltas(self, reproject_client) -> None:
        resp = reproject_client.post("/v1/cvi/reproject", json={"weights": self.WEIGHTS})
        assert resp.status_code == 200
        items = resp.json()
        # new scores: two/b=60.0, one/a=40.0, three/c=0.0, four/d=0.0
        assert [i["repo_id"] for i in items] == [
            "two/b",
            "one/a",
            "three/c",
            "four/d",
        ]
        two_b, one_a, three_c, four_d = items
        assert two_b["new_score"] == 60.0
        assert two_b["old_rank"] == 2 and two_b["new_rank"] == 1
        assert two_b["delta"] == 1  # moved up
        assert one_a["new_score"] == 40.0
        assert one_a["old_rank"] == 1 and one_a["new_rank"] == 2
        assert one_a["delta"] == -1  # moved down
        # tie at 0.0: position in the old list breaks it (three/c before four/d)
        assert three_c["new_score"] == 0.0
        assert three_c["old_rank"] == 3 and three_c["new_rank"] == 3
        assert three_c["delta"] == 0
        assert four_d["new_score"] == 0.0
        assert four_d["old_rank"] == 4 and four_d["new_rank"] == 4
        assert four_d["delta"] == 0
        # owner/name and quadrant coordinates ride along
        for item in items:
            assert {"owner", "name", "x", "y", "quadrant"} <= set(item)
        assert two_b["owner"] == "two" and two_b["name"] == "b"
        assert two_b["x"] == 70.0 and two_b["y"] == 90.0
        assert two_b["quadrant"] == "Crucible"

    def test_sum_off_by_tiny_epsilon_is_accepted(self, reproject_client) -> None:
        # sums to 1.0 + 1e-9 -> within the 1e-6 tolerance
        weights = {
            "responsiveness": 0.5,
            "criticality": 0.25,
            "governance": 0.125,
            "rigor": 0.125,
            "accessibility": 1e-9,
        }
        resp = reproject_client.post("/v1/cvi/reproject", json={"weights": weights})
        assert resp.status_code == 200
        assert len(resp.json()) == 4

    def test_missing_key_422(self, reproject_client) -> None:
        weights = {
            "responsiveness": 0.3,
            "criticality": 0.3,
            "governance": 0.2,
            "rigor": 0.2,
        }
        resp = reproject_client.post("/v1/cvi/reproject", json={"weights": weights})
        assert resp.status_code == 422
        assert "detail" in resp.json()

    def test_extra_key_422(self, reproject_client) -> None:
        weights = dict(self.WEIGHTS, vibes=0.0)
        resp = reproject_client.post("/v1/cvi/reproject", json={"weights": weights})
        assert resp.status_code == 422
        assert "detail" in resp.json()

    def test_sum_off_by_more_than_tolerance_422(self, reproject_client) -> None:
        weights = {
            "responsiveness": 0.35,
            "criticality": 0.35,
            "governance": 0.15,
            "rigor": 0.15,
            "accessibility": 0.05,  # sums to 1.05
        }
        resp = reproject_client.post("/v1/cvi/reproject", json={"weights": weights})
        assert resp.status_code == 422
        assert "detail" in resp.json()

    def test_sum_outside_tolerance_sub_percent_422(self, reproject_client) -> None:
        # sums to 1.00001 -> deviation 1e-5, strictly outside 1e-6 tolerance
        weights = {
            "responsiveness": 0.25,
            "criticality": 0.25,
            "governance": 0.20,
            "rigor": 0.15,
            "accessibility": 0.15001,
        }
        resp = reproject_client.post("/v1/cvi/reproject", json={"weights": weights})
        assert resp.status_code == 422
        assert "detail" in resp.json()

    def test_empty_store_returns_empty_array(self) -> None:
        client = open_client(lambda: OperationalStore())
        try:
            resp = client.post("/v1/cvi/reproject", json={"weights": self.WEIGHTS})
            assert resp.status_code == 200
            assert resp.json() == []
        finally:
            client.__exit__(None, None, None)
