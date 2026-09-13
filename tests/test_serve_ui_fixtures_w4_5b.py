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

    def test_response_validates_against_the_contract(self, server_url: str) -> None:
        from cvi.serve.contract import MatrixResponse

        resp = httpx.get(f"{server_url}/v1/cvi/matrix")
        MatrixResponse.model_validate(resp.json())


class TestAxisCoverageCaptions:
    def test_no_caption_when_every_contributing_pillar_is_well_measured(self) -> None:
        rows = [
            {"p_responsiveness": 1.0, "p_governance": 1.0, "p_accessibility": 1.0, "p_criticality": 1.0, "p_rigor": 1.0}
            for _ in range(20)
        ]
        assert serve_ui_fixtures.axis_coverage_captions(rows) == {}

    def test_caption_names_the_worst_missing_pillar_above_threshold(self) -> None:
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
        captions = serve_ui_fixtures.axis_coverage_captions(rows)
        assert "friction" in captions
        assert "accessibility" in captions["friction"]
        assert "30%" in captions["friction"]
        assert "leverage" not in captions

    def test_below_threshold_produces_no_caption(self) -> None:
        rows = [{"p_responsiveness": 1.0, "p_governance": 1.0, "p_accessibility": None if i == 0 else 1.0, "p_criticality": 1.0, "p_rigor": 1.0} for i in range(100)]
        # 1/100 = 1%, below the 5% threshold.
        assert serve_ui_fixtures.axis_coverage_captions(rows) == {}


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
