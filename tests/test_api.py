"""Tests for the Porchlamp REST API layer (spec 12).

Seeds an in-memory ``OperationalStore`` via ``upsert_composite`` and
patches the raw ``stars`` column with direct SQL (ingestion-layer
territory — see the ``porchlamp.lakehouse`` module docstring) so the
``min_stars`` filter paths are actually exercised.

Oracle style: exact expected values everywhere; a test that is easy to
pass is a defect.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from porchlamp.api import create_app
from porchlamp.lakehouse import OperationalStore
from porchlamp.models import PorchlampCompositeResult, Ecosystem, PillarScores, Quadrant, Tier


NODE_KEYS = {
    "repo_id",
    "owner",
    "name",
    "x",
    "y",
    "porchlamp",
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
    overall_porchlamp: float,
    rank_tier: Tier,
    matrix_quadrant: Quadrant,
    leverage_score: float,
    friction_score: float,
    ecosystem: Ecosystem = Ecosystem.PYPI,
    is_stage0_disqualified: bool = False,
    disqualification_reason: str | None = None,
) -> PorchlampCompositeResult:
    return PorchlampCompositeResult(
        owner=owner,
        name=name,
        ecosystem=ecosystem,
        overall_porchlamp=overall_porchlamp,
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
        # owner, name, eco, porchlamp, tier, quadrant, leverage, friction, pillars, stars, gated
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
    for owner, name, eco, porchlamp, tier, quad, lev, fric, pillars, stars, gated, reason in seed:
        store.upsert_composite(
            make_result(
                owner,
                name,
                porchlamp,
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
    """Four repos whose old ranking (by overall_porchlamp) intentionally
    disagrees with the criticality-heavy re-projection:

    - one/a:   pillars (100, 0, 0, 0, 0),  porchlamp 50 -> old rank 1
    - two/b:   pillars (0, 100, 0, 0, 0),  porchlamp 40 -> old rank 2
    - three/c: pillars (0, 0, 50, 0, 0),   porchlamp 30 -> old rank 3
    - four/d:  pillars (0, 0, 50, 0, 0),   porchlamp 20 -> old rank 4
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
    for owner, name, porchlamp, tier, quad, lev, fric, pillars in seed:
        store.upsert_composite(
            make_result(owner, name, porchlamp, tier, quad, lev, fric),
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
        resp = client.get("/v1/porchlamp/matrix")
        assert resp.status_code == 200
        body = resp.json()
        assert body["nodes"] == []
        assert body["quadrant_counts"] == dict.fromkeys(ALL_QUADRANTS, 0)
    finally:
        client.__exit__(None, None, None)


# --------------------------------------------------------------------------- #
# GET /v1/porchlamp/matrix
# --------------------------------------------------------------------------- #
class TestMatrix:
    def test_default_params_nodes_and_shape(self, matrix_client) -> None:
        resp = matrix_client.get("/v1/porchlamp/matrix")
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
        assert alpha["porchlamp"] == 92.5
        assert alpha["tier"] == "Tier S+"
        assert alpha["quadrant"] == "Sweet Spot"
        assert alpha["size"] == 150
        assert alpha["is_gated"] == 0
        assert gamma["size"] == 500
        # stars == min_stars boundary is inclusive
        assert delta["size"] == 100

    def test_default_excludes_low_star_and_gated(self, matrix_client) -> None:
        ids = [n["repo_id"] for n in matrix_client.get("/v1/porchlamp/matrix").json()["nodes"]]
        assert "beta/omega" not in ids  # stars=99 < 100
        assert "gated/omega" not in ids  # stage-0 disqualified

    def test_quadrant_counts_always_all_four_keys(self, matrix_client) -> None:
        counts = matrix_client.get("/v1/porchlamp/matrix").json()["quadrant_counts"]
        assert set(counts) == ALL_QUADRANTS
        # counts reflect the table (no stars/gating filter): all 5 repos
        assert counts == {"Sweet Spot": 1, "Crucible": 1, "Nursery": 1, "Graveyard": 2}

    def test_ecosystem_filter(self, matrix_client) -> None:
        body = matrix_client.get("/v1/porchlamp/matrix", params={"ecosystem": "pypi"}).json()
        assert [n["repo_id"] for n in body["nodes"]] == ["alpha/omega", "delta/omega"]
        assert body["quadrant_counts"] == {
            "Sweet Spot": 1,
            "Crucible": 1,
            "Nursery": 0,
            "Graveyard": 2,
        }

    def test_ecosystem_no_match(self, matrix_client) -> None:
        body = matrix_client.get("/v1/porchlamp/matrix", params={"ecosystem": "crates"}).json()
        assert body["nodes"] == []
        assert body["quadrant_counts"] == dict.fromkeys(ALL_QUADRANTS, 0)

    def test_min_stars_filter_values(self, matrix_client) -> None:
        high = matrix_client.get(
            "/v1/porchlamp/matrix", params={"min_stars": 500}
        ).json()["nodes"]
        assert [n["repo_id"] for n in high] == ["gamma/omega"]
        zero = matrix_client.get(
            "/v1/porchlamp/matrix", params={"min_stars": 0}
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
            "/v1/porchlamp/matrix", params={"exclude_black_holes": "false"}
        ).json()
        nodes = body["nodes"]
        assert [n["repo_id"] for n in nodes] == [
            "alpha/omega",
            "gamma/omega",
            "delta/omega",
            "gated/omega",
        ]
        assert nodes[-1]["is_gated"] == 1
        assert nodes[-1]["porchlamp"] == 0.0
        assert nodes[-1]["size"] == 1000

    def test_domain_is_reserved_noop(self, matrix_client) -> None:
        plain = matrix_client.get("/v1/porchlamp/matrix").json()
        with_domain = matrix_client.get(
            "/v1/porchlamp/matrix", params={"domain": "ai_ml"}
        ).json()
        assert with_domain == plain

    def test_persona_is_reserved_noop(self, matrix_client) -> None:
        plain = matrix_client.get("/v1/porchlamp/matrix").json()
        for persona in (
            "balanced",
            "first_timer",
            "career_capital",
            "senior_systems",
            "ai_assisted",
        ):
            assert (
                matrix_client.get("/v1/porchlamp/matrix", params={"persona": persona}).json()
                == plain
            )

    def test_persona_invalid_422(self, matrix_client) -> None:
        resp = matrix_client.get("/v1/porchlamp/matrix", params={"persona": "vibe_coder"})
        assert resp.status_code == 422
        assert "detail" in resp.json()

    def test_min_stars_negative_422(self, matrix_client) -> None:
        resp = matrix_client.get("/v1/porchlamp/matrix", params={"min_stars": -1})
        assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# GET /v1/porchlamp/repos/{owner}/{name}
# --------------------------------------------------------------------------- #
class TestRepoDeepDive:
    def test_happy_path_full_row_plus_pillar_scores(self, matrix_client) -> None:
        resp = matrix_client.get("/v1/porchlamp/repos/alpha/omega")
        assert resp.status_code == 200
        body = resp.json()
        assert body["owner"] == "alpha"
        assert body["name"] == "omega"
        assert body["ecosystem"] == "pypi"
        assert body["stars"] == 150
        assert body["overall_porchlamp"] == 92.5
        assert body["rank_tier"] == "Tier S+"
        assert body["matrix_quadrant"] == "Sweet Spot"
        assert body["leverage_score"] == 80.0
        assert body["friction_score"] == 20.0
        assert body["is_stage0_disqualified"] == 0
        assert body["disqualification_reason"] is None
        assert body["last_indexed_at"] is None  # no SQL-side clock: NULL unless passed explicitly
        assert body["pillar_scores"] == {
            "responsiveness": 90.0,
            "criticality": 80.0,
            "governance": 70.0,
            "rigor": 60.0,
            "accessibility": 50.0,
        }

    def test_gated_repo_flags(self, matrix_client) -> None:
        body = matrix_client.get("/v1/porchlamp/repos/gated/omega").json()
        assert body["is_stage0_disqualified"] == 1
        assert body["disqualification_reason"] == "ghost rate 0.4"

    def test_missing_repo_404_json(self, matrix_client) -> None:
        resp = matrix_client.get("/v1/porchlamp/repos/nobody/nothing")
        assert resp.status_code == 404
        assert "detail" in resp.json()

    def test_existing_repo_includes_parsed_deep_dive_object(self) -> None:
        def seed():
            s = seed_matrix_store()
            s.upsert_deep_dive(
                "alpha", "omega", {"maintainer_playbook": {"total_point_gain": 4.5}}
            )
            return s

        client = open_client(seed)
        try:
            resp = client.get("/v1/porchlamp/repos/alpha/omega")
            assert resp.status_code == 200
            data = resp.json()
            assert "deep_dive" in data
            assert data["deep_dive"]["maintainer_playbook"]["total_point_gain"] == 4.5
        finally:
            client.__exit__(None, None, None)


# --------------------------------------------------------------------------- #
# POST /v1/porchlamp/reproject
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
        resp = reproject_client.post("/v1/porchlamp/reproject", json={"weights": self.WEIGHTS})
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
        resp = reproject_client.post("/v1/porchlamp/reproject", json={"weights": weights})
        assert resp.status_code == 200
        assert len(resp.json()) == 4

    def test_missing_key_422(self, reproject_client) -> None:
        weights = {
            "responsiveness": 0.3,
            "criticality": 0.3,
            "governance": 0.2,
            "rigor": 0.2,
        }
        resp = reproject_client.post("/v1/porchlamp/reproject", json={"weights": weights})
        assert resp.status_code == 422
        assert "detail" in resp.json()

    def test_extra_key_422(self, reproject_client) -> None:
        weights = dict(self.WEIGHTS, vibes=0.0)
        resp = reproject_client.post("/v1/porchlamp/reproject", json={"weights": weights})
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
        resp = reproject_client.post("/v1/porchlamp/reproject", json={"weights": weights})
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
        resp = reproject_client.post("/v1/porchlamp/reproject", json={"weights": weights})
        assert resp.status_code == 422
        assert "detail" in resp.json()

    def test_empty_store_returns_empty_array(self) -> None:
        client = open_client(lambda: OperationalStore())
        try:
            resp = client.post("/v1/porchlamp/reproject", json={"weights": self.WEIGHTS})
            assert resp.status_code == 200
            assert resp.json() == []
        finally:
            client.__exit__(None, None, None)


# --------------------------------------------------------------------------- #
# GET /v1/porchlamp/leaderboard
# --------------------------------------------------------------------------- #
LEADERBOARD_KEYS = {
    "rank",
    "repo_id",
    "owner",
    "name",
    "ecosystem",
    "porchlamp",
    "tier",
    "quadrant",
    "x",
    "y",
    "pillar_scores",
    "stars",
    "downloads",
    "dependents",
    "elephant_factor",
    "legal_ip_model",
    "ai_posture",
    "is_gated",
}


def seed_persona_constraint_store() -> OperationalStore:
    store = OperationalStore()
    # 1. AI proscribed repo with EF=4
    store.upsert_composite(
        make_result("ai", "proscribed", 75.0, Tier.A, Quadrant.SWEET_SPOT, 70.0, 30.0),
        pillar_scores=make_pillars(80, 80, 70, 80, 60),
    )
    store.connection.execute(
        "UPDATE repositories SET ai_posture = 'PROSCRIBED', elephant_factor = 4, stars = 500 WHERE owner = 'ai' AND name = 'proscribed'"
    )
    # 2. Solo dev repo with EF=1 (low EF)
    store.upsert_composite(
        make_result("solo", "dev", 75.0, Tier.A, Quadrant.SWEET_SPOT, 70.0, 30.0),
        pillar_scores=make_pillars(80, 80, 70, 80, 60),
    )
    store.connection.execute(
        "UPDATE repositories SET ai_posture = 'AD_HOC', elephant_factor = 1, stars = 500 WHERE owner = 'solo' AND name = 'dev'"
    )
    # 3. Foundation repo with EF=5
    store.upsert_composite(
        make_result("found", "core", 80.0, Tier.S, Quadrant.SWEET_SPOT, 80.0, 20.0),
        pillar_scores=make_pillars(85, 85, 80, 80, 80),
    )
    store.connection.execute(
        "UPDATE repositories SET ai_posture = 'REGULATED', elephant_factor = 5, stars = 1000 WHERE owner = 'found' AND name = 'core'"
    )
    # 4. Gated repo
    store.upsert_composite(
        make_result("gated", "repo", 0.0, Tier.D, Quadrant.GRAVEYARD, 0.0, 100.0, is_stage0_disqualified=True),
        pillar_scores=make_pillars(0, 0, 0, 0, 0),
    )
    store.connection.commit()
    return store


@pytest.fixture()
def persona_constraint_client() -> TestClient:
    client = open_client(seed_persona_constraint_store)
    try:
        yield client
    finally:
        client.__exit__(None, None, None)


class TestLeaderboard:
    def test_default_params_balanced_pagination(self, matrix_client) -> None:
        resp = matrix_client.get("/v1/porchlamp/leaderboard")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 5
        assert body["page"] == 1
        assert body["page_size"] == 50
        assert body["limit"] == 50
        assert body["total_pages"] == 1
        items = body["items"]
        assert len(items) == 5
        # Ranks must be 1..5 in order
        assert [i["rank"] for i in items] == [1, 2, 3, 4, 5]
        # Highest overall_porchlamp is alpha/omega (92.5)
        assert items[0]["repo_id"] == "alpha/omega"
        assert items[0]["porchlamp"] == 92.5
        assert items[0]["tier"] == "Tier S+"
        # Required keys present on all items
        for item in items:
            assert set(item) >= LEADERBOARD_KEYS

    def test_custom_pagination_page_and_size(self, matrix_client) -> None:
        # Page 1, page_size 2 -> items 1, 2, total_pages 3
        p1 = matrix_client.get("/v1/porchlamp/leaderboard", params={"page": 1, "page_size": 2}).json()
        assert p1["page"] == 1
        assert p1["page_size"] == 2
        assert p1["total_pages"] == 3
        assert len(p1["items"]) == 2
        assert [i["rank"] for i in p1["items"]] == [1, 2]

        # Page 2, page_size 2 -> items 3, 4, total_pages 3
        p2 = matrix_client.get("/v1/porchlamp/leaderboard", params={"page": 2, "page_size": 2}).json()
        assert p2["page"] == 2
        assert p2["page_size"] == 2
        assert p2["total_pages"] == 3
        assert len(p2["items"]) == 2
        assert [i["rank"] for i in p2["items"]] == [3, 4]

        # Page 3, page_size 2 -> item 5, total_pages 3
        p3 = matrix_client.get("/v1/porchlamp/leaderboard", params={"page": 3, "page_size": 2}).json()
        assert p3["page"] == 3
        assert p3["page_size"] == 2
        assert p3["total_pages"] == 3
        assert len(p3["items"]) == 1
        assert [i["rank"] for i in p3["items"]] == [5]

        # Page 2, page_size 10 -> out of bounds items [], total_pages 1
        p_custom = matrix_client.get("/v1/porchlamp/leaderboard", params={"page": 2, "page_size": 10}).json()
        assert p_custom["page"] == 2
        assert p_custom["page_size"] == 10
        assert p_custom["total_pages"] == 1
        assert p_custom["items"] == []

    def test_invalid_pagination_boundaries(self, matrix_client) -> None:
        assert matrix_client.get("/v1/porchlamp/leaderboard", params={"page": 0}).status_code == 422
        assert matrix_client.get("/v1/porchlamp/leaderboard", params={"page": -1}).status_code == 422
        assert matrix_client.get("/v1/porchlamp/leaderboard", params={"page_size": 0}).status_code == 422
        assert matrix_client.get("/v1/porchlamp/leaderboard", params={"page_size": 1001}).status_code == 422
        assert matrix_client.get("/v1/porchlamp/leaderboard", params={"limit": 0}).status_code == 422
        assert matrix_client.get("/v1/porchlamp/leaderboard", params={"limit": 1001}).status_code == 422
        assert matrix_client.get("/v1/porchlamp/leaderboard", params={"offset": -1}).status_code == 422

    def test_ecosystem_filter(self, matrix_client) -> None:
        resp = matrix_client.get("/v1/porchlamp/leaderboard", params={"ecosystem": "pypi"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 4
        assert all(i["ecosystem"] == "pypi" for i in body["items"])

        # Case-insensitive match
        resp_upper = matrix_client.get("/v1/porchlamp/leaderboard", params={"ecosystem": "PYPI"})
        assert resp_upper.status_code == 200
        assert resp_upper.json()["total"] == 4

        # Non-matching ecosystem
        resp_empty = matrix_client.get("/v1/porchlamp/leaderboard", params={"ecosystem": "crates"})
        assert resp_empty.status_code == 200
        assert resp_empty.json()["total"] == 0
        assert resp_empty.json()["items"] == []

    def test_min_stars_filter(self, matrix_client) -> None:
        # beta has stars=99, alpha=150, delta=100, gamma=500, gated=1000
        resp = matrix_client.get("/v1/porchlamp/leaderboard", params={"min_stars": 100})
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 4
        assert "beta/omega" not in [i["repo_id"] for i in body["items"]]

        resp_high = matrix_client.get("/v1/porchlamp/leaderboard", params={"min_stars": 500})
        assert resp_high.status_code == 200
        assert resp_high.json()["total"] == 2

        # Negative min_stars rejected
        resp_bad = matrix_client.get("/v1/porchlamp/leaderboard", params={"min_stars": -1})
        assert resp_bad.status_code == 422

    def test_pagination_offsets(self, matrix_client) -> None:
        # Page 1: limit 2, offset 0
        p1 = matrix_client.get("/v1/porchlamp/leaderboard", params={"limit": 2, "offset": 0}).json()
        assert p1["page"] == 1
        assert p1["limit"] == 2
        assert p1["total"] == 5
        assert len(p1["items"]) == 2
        assert [i["rank"] for i in p1["items"]] == [1, 2]

        # Page 2: limit 2, offset 2
        p2 = matrix_client.get("/v1/porchlamp/leaderboard", params={"limit": 2, "offset": 2}).json()
        assert p2["page"] == 2
        assert p2["limit"] == 2
        assert len(p2["items"]) == 2
        assert [i["rank"] for i in p2["items"]] == [3, 4]

        # Page 3: limit 2, offset 4
        p3 = matrix_client.get("/v1/porchlamp/leaderboard", params={"limit": 2, "offset": 4}).json()
        assert p3["page"] == 3
        assert len(p3["items"]) == 1
        assert [i["rank"] for i in p3["items"]] == [5]

        # Beyond total
        p4 = matrix_client.get("/v1/porchlamp/leaderboard", params={"limit": 2, "offset": 10}).json()
        assert p4["page"] == 6
        assert p4["items"] == []

        # Validation errors
        assert matrix_client.get("/v1/porchlamp/leaderboard", params={"limit": 0}).status_code == 422
        assert matrix_client.get("/v1/porchlamp/leaderboard", params={"limit": 1001}).status_code == 422
        assert matrix_client.get("/v1/porchlamp/leaderboard", params={"offset": -1}).status_code == 422

    def test_fuzzy_search(self, matrix_client) -> None:
        # By owner
        r1 = matrix_client.get("/v1/porchlamp/leaderboard", params={"search": "alpha"}).json()
        assert r1["total"] == 1
        assert r1["items"][0]["repo_id"] == "alpha/omega"

        # By name
        r2 = matrix_client.get("/v1/porchlamp/leaderboard", params={"search": "omega"}).json()
        assert r2["total"] == 5

        # By full id
        r3 = matrix_client.get("/v1/porchlamp/leaderboard", params={"search": "gamma/omega"}).json()
        assert r3["total"] == 1
        assert r3["items"][0]["repo_id"] == "gamma/omega"

        # Whitespace-only search is treated as no search
        r4 = matrix_client.get("/v1/porchlamp/leaderboard", params={"search": "   "}).json()
        assert r4["total"] == 5

        # No match
        r5 = matrix_client.get("/v1/porchlamp/leaderboard", params={"search": "nonexistent"}).json()
        assert r5["total"] == 0
        assert r5["items"] == []

    def test_persona_reproject_ordering(self, reproject_client) -> None:
        # Test dynamic persona projection on the reproject fixture repos
        resp_ft = reproject_client.get(
            "/v1/porchlamp/leaderboard", params={"persona": "first_timer"}
        )
        assert resp_ft.status_code == 200
        items_ft = resp_ft.json()["items"]
        assert len(items_ft) == 4
        # First-timer weights: resp .40, crit .05, gov .15, rigor .10, acc .30
        # Check that scores are reprojected and ranks updated
        assert items_ft[0]["rank"] == 1
        assert items_ft[0]["porchlamp"] > 0.0

    def test_persona_constraints_ai_and_senior_systems(self, persona_constraint_client) -> None:
        # 1. Under BALANCED: all 3 non-gated score normally
        bal = persona_constraint_client.get(
            "/v1/porchlamp/leaderboard", params={"persona": "balanced"}
        ).json()["items"]
        by_id_bal = {i["repo_id"]: i for i in bal}
        assert by_id_bal["ai/proscribed"]["porchlamp"] == 75.0
        assert by_id_bal["ai/proscribed"]["is_gated"] == 0
        assert by_id_bal["solo/dev"]["porchlamp"] == 75.0
        assert by_id_bal["solo/dev"]["is_gated"] == 0
        assert by_id_bal["found/core"]["porchlamp"] == 80.0
        assert by_id_bal["found/core"]["is_gated"] == 0
        assert by_id_bal["gated/repo"]["porchlamp"] == 0.0
        assert by_id_bal["gated/repo"]["is_gated"] == 1

        # 2. Under AI_ASSISTED: ai/proscribed is hard-disqualified (porchlamp=0, Tier D, is_gated=1)
        ai_resp = persona_constraint_client.get(
            "/v1/porchlamp/leaderboard", params={"persona": "ai_assisted"}
        ).json()["items"]
        by_id_ai = {i["repo_id"]: i for i in ai_resp}
        assert by_id_ai["ai/proscribed"]["porchlamp"] == 0.0
        assert by_id_ai["ai/proscribed"]["tier"] == "Tier D"
        assert by_id_ai["ai/proscribed"]["is_gated"] == 1
        # solo/dev and found/core are not proscribed, so they score > 0
        assert by_id_ai["solo/dev"]["porchlamp"] > 0.0
        assert by_id_ai["found/core"]["porchlamp"] > 0.0

        # 3. Under SENIOR_SYSTEMS: solo/dev (EF=1 < 3) is hard-disqualified (porchlamp=0, Tier D, is_gated=1)
        ss_resp = persona_constraint_client.get(
            "/v1/porchlamp/leaderboard", params={"persona": "senior_systems"}
        ).json()["items"]
        by_id_ss = {i["repo_id"]: i for i in ss_resp}
        assert by_id_ss["solo/dev"]["porchlamp"] == 0.0
        assert by_id_ss["solo/dev"]["tier"] == "Tier D"
        assert by_id_ss["solo/dev"]["is_gated"] == 1
        # found/core (EF=5 >= 3) and ai/proscribed (EF=4 >= 3) pass EF constraint
        assert by_id_ss["found/core"]["porchlamp"] > 0.0
        assert by_id_ss["found/core"]["is_gated"] == 0
        assert by_id_ss["ai/proscribed"]["porchlamp"] > 0.0
        assert by_id_ss["ai/proscribed"]["is_gated"] == 0

    def test_invalid_persona_422(self, matrix_client) -> None:
        resp = matrix_client.get("/v1/porchlamp/leaderboard", params={"persona": "invalid_persona"})
        assert resp.status_code == 422

    def test_store_leaderboard_value_error_raises_422(self, monkeypatch, matrix_client) -> None:
        def mock_leaderboard(*args, **kwargs):
            raise ValueError("simulated value error")
        store = matrix_client.app.state.store
        monkeypatch.setattr(store, "leaderboard", mock_leaderboard)
        resp = matrix_client.get("/v1/porchlamp/leaderboard")
        assert resp.status_code == 422
        assert "simulated value error" in resp.json()["detail"]

    def test_empty_store_returns_empty_paginated_dict(self) -> None:
        client = open_client(lambda: OperationalStore())
        try:
            resp = client.get("/v1/porchlamp/leaderboard")
            assert resp.status_code == 200
            assert resp.json() == {
                "total": 0,
                "page": 1,
                "page_size": 50,
                "limit": 50,
                "total_pages": 0,
                "items": [],
            }
        finally:
            client.__exit__(None, None, None)


class TestStaticServing:
    def test_root_serves_index_html(self, matrix_client) -> None:
        resp = matrix_client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "<!doctype html>" in resp.text.lower()

    def test_index_html_direct(self, matrix_client) -> None:
        resp = matrix_client.get("/index.html")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_nonexistent_static_file_returns_404(self, matrix_client) -> None:
        resp = matrix_client.get("/nonexistent_static_file.xyz")
        assert resp.status_code == 404

    def test_api_routes_not_shadowed(self, matrix_client) -> None:
        assert matrix_client.get("/healthz").headers["content-type"] == "application/json"
        assert matrix_client.get("/v1/porchlamp/matrix").headers["content-type"] == "application/json"
        assert matrix_client.get("/v1/porchlamp/leaderboard").headers["content-type"] == "application/json"

    def test_create_app_web_dir_none(self) -> None:
        app = create_app(web_dir=None)
        client = TestClient(app)
        with client:
            assert client.get("/healthz").status_code == 200
            assert client.get("/").status_code == 404

    def test_create_app_web_dir_nonexistent(self) -> None:
        app = create_app(web_dir="/tmp/definitely_nonexistent_dir_12345")
        client = TestClient(app)
        with client:
            assert client.get("/healthz").status_code == 200
            assert client.get("/").status_code == 404

    def test_create_app_custom_web_dir(self, tmp_path) -> None:
        custom_file = tmp_path / "index.html"
        custom_file.write_text("<!doctype html><html><body>Test Custom App</body></html>", encoding="utf-8")
        app = create_app(web_dir=tmp_path)
        client = TestClient(app)
        with client:
            resp = client.get("/")
            assert resp.status_code == 200
            assert "Test Custom App" in resp.text

