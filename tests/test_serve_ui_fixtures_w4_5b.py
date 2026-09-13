"""Tests for packet W4-5b's additions to `scripts/serve_ui_fixtures.py`:
`GET /v1/cvi/matrix`, `GET /v1/cvi/methodology`, `POST /v1/cvi/reproject`.

Deliberately a NEW file (see `tests/test_ui_fixtures_w4_5b.py`'s docstring
for why) rather than an addition to `tests/test_serve_ui_fixtures.py`.

The reproject parity tests are the packet's required "fixture-server
reproject parity with a hand oracle (>= 5 weight vectors incl. a zero
pillar)": each oracle recomputes D-MISS-2/D-MAT-2 independently, in a
different (more literal, less vectorised) style than
`scripts/serve_ui_fixtures.py::reproject_rows`, over the same
`leaderboard.json` rows, so an error shared by both would have to be a
genuinely identical bug, not a shared shortcut.
"""

from __future__ import annotations

import importlib.util
import json
import math
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "serve_ui_fixtures.py"
WEB_DIR = REPO_ROOT / "web" / "v2"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "ui"

PILLARS = ("responsiveness", "criticality", "governance", "rigor", "accessibility")


def _load_module():
    spec = importlib.util.spec_from_file_location("cvi_serve_ui_fixtures_w4_5b", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


serve_ui_fixtures = _load_module()


@pytest.fixture(scope="module")
def server_url() -> Iterator[str]:
    httpd = serve_ui_fixtures.serve(host="127.0.0.1", port=0, web_dir=WEB_DIR, fixtures_dir=FIXTURES_DIR)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _leaderboard_rows() -> list[dict[str, Any]]:
    return json.loads((FIXTURES_DIR / "leaderboard.json").read_text())["rows"]


# --------------------------------------------------------------------------- #
# Hand oracle: a literal, row-by-row, loop-heavy reimplementation, kept       #
# deliberately distinct in STYLE from `reproject_rows`'s vectorised-ish       #
# per-pillar accumulation (a shared bug would have to be the same bug, not    #
# the same code shape).                                                       #
# --------------------------------------------------------------------------- #
def oracle_reproject(rows: list[dict[str, Any]], weights: dict[str, float]) -> list[dict[str, Any]]:
    scored = []
    for row in rows:
        numerator = 0.0
        weight_of_measured = 0.0
        coverage = 0.0
        for pillar in PILLARS:
            weight = weights[pillar]
            score = row[f"p_{pillar}"]
            cov = row[f"c_{pillar}"]
            coverage = coverage + weight * cov
            if score is not None and cov > 0:
                numerator = numerator + weight * score
                weight_of_measured = weight_of_measured + weight
        if weight_of_measured == 0.0:
            continue
        composite = numerator / weight_of_measured
        if coverage < 0.60:
            continue
        if composite >= 90.0:
            score_tier = "S+"
        elif composite >= 80.0:
            score_tier = "S"
        elif composite >= 70.0:
            score_tier = "A"
        elif composite >= 55.0:
            score_tier = "B"
        elif composite >= 40.0:
            score_tier = "C"
        else:
            score_tier = "D"
        if coverage >= 0.95:
            ceiling = "S+"
        elif coverage >= 0.80:
            ceiling = "A"
        else:
            ceiling = "B"
        order = ["D", "C", "B", "A", "S", "S+"]
        tier = score_tier if order.index(score_tier) <= order.index(ceiling) else ceiling
        scored.append(
            {
                "owner": row["owner"],
                "name": row["name"],
                "cvi": round(composite, 2),
                "coverage": coverage,
                "tier": tier,
                "p_responsiveness": row["p_responsiveness"],
                "friction": row["friction"],
                "leverage": row["leverage"],
            }
        )
    scored.sort(
        key=lambda r: (
            -r["cvi"],
            -r["coverage"],
            -(r["p_responsiveness"] if r["p_responsiveness"] is not None else -1e18),
            r["friction"] if r["friction"] is not None else 1e18,
            -(r["leverage"] if r["leverage"] is not None else -1e18),
            f"{r['owner']}/{r['name']}".lower(),
        )
    )
    for i, r in enumerate(scored, start=1):
        r["rank"] = i
    return scored


#: >= 5 weight vectors, including a zero-pillar one (packet requirement).
WEIGHT_VECTORS: list[dict[str, float]] = [
    {"weight_responsiveness": 0.25, "weight_criticality": 0.25, "weight_governance": 0.20, "weight_rigor": 0.15, "weight_accessibility": 0.15},
    {"weight_responsiveness": 1.0, "weight_criticality": 0.0, "weight_governance": 0.0, "weight_rigor": 0.0, "weight_accessibility": 0.0},
    {"weight_responsiveness": 0.0, "weight_criticality": 1.0, "weight_governance": 0.0, "weight_rigor": 0.0, "weight_accessibility": 0.0},
    {"weight_responsiveness": 0.35, "weight_criticality": 0.10, "weight_governance": 0.15, "weight_rigor": 0.10, "weight_accessibility": 0.30},
    {"weight_responsiveness": 0.15, "weight_criticality": 0.35, "weight_governance": 0.25, "weight_rigor": 0.20, "weight_accessibility": 0.05},
    {"weight_responsiveness": 0.20, "weight_criticality": 0.20, "weight_governance": 0.15, "weight_rigor": 0.35, "weight_accessibility": 0.10},
    # Opus code review M6 (2026-09-13): every OTHER vector here still rates
    # all 73 fixture rows, so a mutant that skips the 0.60 coverage floor
    # entirely (RATED_MIN_COVERAGE -> 0.0) survives every one of them. This
    # one genuinely drops exactly one row on the real fixture (72 of 73;
    # verified live against the running server before being hardcoded here).
    {"weight_responsiveness": 0.05, "weight_criticality": 0.35, "weight_governance": 0.05, "weight_rigor": 0.05, "weight_accessibility": 0.50},
]


class TestReprojectParityAgainstHandOracle:
    @pytest.mark.parametrize("weights", WEIGHT_VECTORS, ids=[f"v{i}" for i in range(len(WEIGHT_VECTORS))])
    def test_server_matches_oracle_on_rank_cvi_coverage_tier(self, server_url: str, weights: dict[str, float]) -> None:
        resp = httpx.post(f"{server_url}/v1/cvi/reproject", json=weights)
        assert resp.status_code == 200
        served = resp.json()["rows"]

        plain_weights = {k[len("weight_"):]: v for k, v in weights.items()}
        expected = oracle_reproject(_leaderboard_rows(), plain_weights)

        assert len(served) == len(expected)
        served_by_key = {(r["owner"], r["name"]): r for r in served}
        for exp in expected:
            key = (exp["owner"], exp["name"])
            assert key in served_by_key, f"{key} present in the oracle but missing from the server"
            got = served_by_key[key]
            assert got["rank"] == exp["rank"], key
            assert math.isclose(got["cvi"], exp["cvi"], abs_tol=1e-9), key
            assert math.isclose(got["coverage"], exp["coverage"], abs_tol=1e-6), key
            assert got["tier"] == exp["tier"], key

    def test_zero_pillar_weight_still_ranks_the_full_visible_set(self, server_url: str) -> None:
        weights = {"weight_responsiveness": 1.0, "weight_criticality": 0.0, "weight_governance": 0.0, "weight_rigor": 0.0, "weight_accessibility": 0.0}
        resp = httpx.post(f"{server_url}/v1/cvi/reproject", json=weights)
        body = resp.json()
        assert body["total"] > 0
        assert body["lens"] == "custom"
        ranks = [r["rank"] for r in body["rows"]]
        assert ranks == sorted(ranks) == list(range(1, len(ranks) + 1))

    def test_a_tilted_vector_genuinely_drops_a_row_below_the_rating_floor(self, server_url: str) -> None:
        """Opus code review M6: proves the 0.60 floor is actually enforced
        under reprojection, not just under the balanced/persona paths —
        the vector itself is WEIGHT_VECTORS[-1], asserted by identity so
        this test breaks loudly if that vector's own coverage-crossing
        property is ever lost to an edit."""
        balanced_total = httpx.get(f"{server_url}/v1/cvi/leaderboard", params={"page_size": 1000}).json()["total"]
        resp = httpx.post(f"{server_url}/v1/cvi/reproject", json=WEIGHT_VECTORS[-1])
        assert resp.json()["total"] < balanced_total

    def test_balanced_weights_reproduce_the_stored_balanced_ranking(self, server_url: str) -> None:
        # D-AGG-1: the balanced weight vector IS `PILLAR_WEIGHTS`.
        balanced = {"weight_responsiveness": 0.25, "weight_criticality": 0.25, "weight_governance": 0.20, "weight_rigor": 0.15, "weight_accessibility": 0.15}
        resp = httpx.post(f"{server_url}/v1/cvi/reproject", json=balanced)
        top = resp.json()["rows"][0]
        stored_top = min(_leaderboard_rows(), key=lambda r: r["rank"])
        assert (top["owner"], top["name"]) == (stored_top["owner"], stored_top["name"])
        assert math.isclose(top["cvi"], stored_top["cvi"], abs_tol=1e-9)

    def test_non_negative_weights_that_do_not_sum_to_one_are_rejected(self, server_url: str) -> None:
        resp = httpx.post(
            f"{server_url}/v1/cvi/reproject",
            json={"weight_responsiveness": 0.5, "weight_criticality": 0.5, "weight_governance": 0.5, "weight_rigor": 0.0, "weight_accessibility": 0.0},
        )
        assert resp.status_code == 400

    def test_negative_weight_is_rejected(self, server_url: str) -> None:
        resp = httpx.post(
            f"{server_url}/v1/cvi/reproject",
            json={"weight_responsiveness": 1.5, "weight_criticality": -0.5, "weight_governance": 0.0, "weight_rigor": 0.0, "weight_accessibility": 0.0},
        )
        assert resp.status_code == 400

    def test_malformed_json_body_is_rejected(self, server_url: str) -> None:
        resp = httpx.post(f"{server_url}/v1/cvi/reproject", content=b"{not json", headers={"Content-Type": "application/json"})
        assert resp.status_code == 400

    def test_response_validates_against_the_contract(self, server_url: str) -> None:
        from cvi.serve.contract import ReprojectResponse

        resp = httpx.post(f"{server_url}/v1/cvi/reproject", json=WEIGHT_VECTORS[0])
        ReprojectResponse.model_validate(resp.json())

    def test_live_response_with_shifted_ranks_validates_with_correct_rank_delta_sign(
        self, server_url: str
    ) -> None:
        """Opus code review (2026-09-13): the balanced weight vector alone
        never exercises `rank_delta` (every row's rank is unchanged, so
        `rank_delta == 0` everywhere — a SIGN-FLIPPED implementation would
        pass just as well). A skewed weight vector genuinely reorders rows,
        so this both validates the LIVE response through
        `cvi.serve.contract.ReprojectResponse` (LeaderboardRow's own
        `rank_delta == balanced_rank - rank` validator, which raises on a
        wrong value) AND asserts the sign against an independent
        recomputation, so a hypothetical `rank - balanced_rank` mutant
        would fail both the contract validator and this assertion.
        """
        from cvi.serve.contract import ReprojectResponse

        # Pure-responsiveness: skewed enough from balanced to move ranks
        # (WEIGHT_VECTORS[1] in this file's own oracle-parity table).
        resp = httpx.post(f"{server_url}/v1/cvi/reproject", json=WEIGHT_VECTORS[1])
        assert resp.status_code == 200
        body = resp.json()
        response = ReprojectResponse.model_validate(body)  # raises if any row's rank_delta is inconsistent

        shifted = [r for r in response.rows if r.rank_delta is not None and r.rank_delta != 0]
        assert shifted, "the skewed weight vector must move at least one row's rank for this test to mean anything"
        for row in shifted:
            assert row.rank_delta == row.balanced_rank - row.rank, (
                f"{row.owner}/{row.name}: rank_delta={row.rank_delta} must equal "
                f"balanced_rank({row.balanced_rank}) - rank({row.rank})"
            )


class TestMatrixEndpoint:
    def test_excludes_gated_repos_and_reports_the_count(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/cvi/matrix")
        assert resp.status_code == 200
        body = resp.json()
        notranked = json.loads((FIXTURES_DIR / "notranked.json").read_text())
        assert body["excluded_count"] == notranked["counts"]["gated"]
        rated_keys = {(r["owner"], r["name"]) for r in _leaderboard_rows()}
        gated_keys = {(r["owner"], r["name"]) for r in notranked["gated"]}
        plotted_keys = {(p["owner"], p["name"]) for p in body["points"]}
        assert plotted_keys <= rated_keys
        assert not (plotted_keys & gated_keys)

    def test_every_point_carries_a_quadrant(self, server_url: str) -> None:
        body = httpx.get(f"{server_url}/v1/cvi/matrix").json()
        assert all(p["quadrant"] is not None for p in body["points"])

    def test_matrix_points_for_excludes_a_row_with_unmeasured_coordinates(self) -> None:
        """Opus code review M3 (2026-09-13): the fixture happens to have no
        null-quadrant row today, so `test_every_point_carries_a_quadrant`
        above passes whether or not the exclusion filter actually exists —
        this constructs one directly, proving matrix_points_for's own
        `if r.get("quadrant") is not None` filter really excludes it."""
        base = dict(
            owner="x", name="y", ecosystem="pypi", leverage=50.0, friction=50.0,
            leverage_coverage=0.0, friction_coverage=0.0, stars_audited=None,
            cvi=50.0, coverage=0.6, tier="B",
        )
        rows = [
            {**base, "quadrant": None},  # every contributing pillar unmeasured -- cannot be plotted
            {**base, "name": "plottable", "quadrant": "Sweet Spot"},
        ]
        points = serve_ui_fixtures.matrix_points_for(rows)
        assert len(points) == 1
        assert points[0]["name"] == "plottable"

    def test_response_validates_against_the_contract(self, server_url: str) -> None:
        from cvi.serve.contract import MatrixResponse

        resp = httpx.get(f"{server_url}/v1/cvi/matrix")
        MatrixResponse.model_validate(resp.json())


class TestAxisCoverage:
    """Opus code review B-2 (2026-09-13): this used to compose the caption
    SENTENCE in Python and send it verbatim (a `06` §6 violation); now it
    sends the raw pillar/count/share numbers and `copy.js` composes the
    sentence — these tests check the numbers, `tests/test_ui_copy_usage.py`
    (via the exported-name check) covers the new copy.js function's own
    reachability, and the number/sentence combination is live-browser
    verified (`.agents/W4-5b/handoff.md`)."""

    def test_no_entry_when_every_contributing_pillar_is_well_measured(self) -> None:
        rows = [
            {"p_responsiveness": 1.0, "p_governance": 1.0, "p_accessibility": 1.0, "p_criticality": 1.0, "p_rigor": 1.0}
            for _ in range(20)
        ]
        assert serve_ui_fixtures.axis_coverage(rows) == {}

    def test_names_the_worst_missing_pillar_above_threshold_with_exact_numbers(self) -> None:
        rows = []
        for i in range(20):
            rows.append(
                {
                    "p_responsiveness": 1.0,
                    "p_governance": 1.0,
                    # accessibility unmeasured on 6/20 = 30% of friction's rows
                    "p_accessibility": None if i < 6 else 1.0,
                    "p_criticality": 1.0,
                    "p_rigor": 1.0,
                }
            )
        coverage = serve_ui_fixtures.axis_coverage(rows)
        assert coverage["friction"] == {"pillar": "accessibility", "axis_partial_count": 6, "axis_partial_share": 0.3}
        assert "leverage" not in coverage

    def test_below_threshold_produces_no_entry(self) -> None:
        rows = [{"p_responsiveness": 1.0, "p_governance": 1.0, "p_accessibility": None if i == 0 else 1.0, "p_criticality": 1.0, "p_rigor": 1.0} for i in range(100)]
        # 1/100 = 1%, below the 5% threshold.
        assert serve_ui_fixtures.axis_coverage(rows) == {}

    def test_response_conforms_to_the_contract(self) -> None:
        from cvi.serve.contract import MatrixAxisCoverage

        rows = [
            {"p_responsiveness": 1.0, "p_governance": 1.0, "p_accessibility": None if i < 6 else 1.0, "p_criticality": 1.0, "p_rigor": 1.0}
            for i in range(20)
        ]
        coverage = serve_ui_fixtures.axis_coverage(rows)
        MatrixAxisCoverage.model_validate(coverage["friction"])


class TestMethodologyEndpoint:
    def test_serves_the_fixture_verbatim(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/cvi/methodology")
        assert resp.status_code == 200
        expected = json.loads((FIXTURES_DIR / "methodology.json").read_text())
        assert resp.json() == expected

    def test_response_validates_against_the_contract(self, server_url: str) -> None:
        from cvi.serve.contract import MethodologyResponse

        resp = httpx.get(f"{server_url}/v1/cvi/methodology")
        MethodologyResponse.model_validate(resp.json())


@pytest.fixture(scope="module")
def server_url_failing_snapshot() -> Iterator[str]:
    """A second server instance with `--fail snapshot` wired (the existing
    NB-2 mechanism, `make_handler`'s `fail_endpoints`), used to exercise the
    W4-4-review error-body shape end to end against a REAL failure, not
    just a 400 from bad input."""
    httpd = serve_ui_fixtures.serve(
        host="127.0.0.1", port=0, web_dir=WEB_DIR, fixtures_dir=FIXTURES_DIR, fail_endpoints=frozenset({"snapshot"})
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


class TestErrorBodyShape:
    """W4-4 review addendum (2026-09-13): the real API's error body is
    `{"error": {endpoint, status, detail}}`, not a bare string —
    `web/v2/api.js` was found to never read `detail` at all. This class
    proves the fixture server actually sends that shape (so `api.js`'s
    `extractErrorDetail` has something real to parse) for both an
    input-validation 400 and a genuine `--fail`-simulated 500.
    """

    def test_400_error_body_is_the_nested_shape(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/cvi/leaderboard", params={"page_size": 37})
        assert resp.status_code == 400
        body = resp.json()
        assert set(body.keys()) == {"error"}
        error = body["error"]
        assert error["status"] == 400
        assert error["endpoint"] == "/v1/cvi/leaderboard"
        assert isinstance(error["detail"], str) and error["detail"]

    def test_fail_snapshot_returns_the_nested_shape_with_a_real_detail(
        self, server_url_failing_snapshot: str
    ) -> None:
        resp = httpx.get(f"{server_url_failing_snapshot}/v1/cvi/snapshot")
        assert resp.status_code == 500
        error = resp.json()["error"]
        assert error["status"] == 500
        assert error["endpoint"] == "/v1/cvi/snapshot"
        assert "simulated snapshot failure" in error["detail"]
        assert "--fail snapshot" in error["detail"]

    def test_query_string_is_stripped_from_the_endpoint_field(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/cvi/leaderboard", params={"page_size": 999999})
        assert resp.status_code == 400
        # The endpoint name must be the bare path, not `path?page_size=999999`
        # (api.js names failures by endpoint for the U-15 banner; a query
        # string baked in would make every distinct query its own "endpoint").
        assert resp.json()["error"]["endpoint"] == "/v1/cvi/leaderboard"
