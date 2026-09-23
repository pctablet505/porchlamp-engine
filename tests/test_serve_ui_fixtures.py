"""Tests for `scripts/serve_ui_fixtures.py`: the stdlib fixture server that
backs `web/v2/`.

Starts the REAL `http.server.ThreadingHTTPServer` on an OS-assigned free
port (`--port 0`) in a background thread and drives it over real HTTP with
`httpx` — filter/sort/page semantics are exercised end-to-end, not mocked.

The reproject tests check the server's `reproject_rows` against a hand
oracle that recomputes D-MISS-2/D-MAT-2 independently, in a deliberately
more literal style, over the same `leaderboard.json` rows: an error shared
by both would have to be a genuinely identical bug, not a shared shortcut.
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

from porchlamp.serve.contract import RepoDetailResponse

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "serve_ui_fixtures.py"
WEB_DIR = REPO_ROOT / "web" / "v2"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "ui"

PILLARS = ("responsiveness", "criticality", "governance", "rigor", "accessibility")


def _load_module():
    spec = importlib.util.spec_from_file_location("porchlamp_serve_ui_fixtures", SCRIPT_PATH)
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


@pytest.fixture(scope="module")
def failing_snapshot_server_url() -> Iterator[str]:
    """A second server instance with `--fail snapshot` behaviour baked in —
    review 2026-09-13 NB-2/W-4: SNAPSHOT_ERROR was reduced into the
    front-end's state but rendered nowhere, so this is what a real 500
    from this exact endpoint looks like on the wire."""
    httpd = serve_ui_fixtures.serve(
        host="127.0.0.1", port=0, web_dir=WEB_DIR, fixtures_dir=FIXTURES_DIR,
        fail_endpoints=frozenset({"snapshot"}),
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


class TestHealthAndStatic:
    def test_healthz(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_root_serves_index_html(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/")
        assert resp.status_code == 200
        assert "<!doctype html>" in resp.text.lower()
        assert 'src="app.js"' in resp.text

    def test_app_js_served_as_javascript(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/app.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]

    def test_unknown_v1_path_is_404_json(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/porchlamp/nonexistent")
        assert resp.status_code == 404
        assert "error" in resp.json()

    def test_path_traversal_is_forbidden(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/../../etc/passwd")
        # httpx normalizes ../ before sending; assert the server's own guard
        # holds even if a client somehow sends a raw traversal path.
        assert resp.status_code in (403, 404)


class TestLeaderboardEndpoint:
    def test_default_params_return_page_one_sorted_by_rank(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/porchlamp/leaderboard")
        assert resp.status_code == 200
        body = resp.json()
        assert body["persona"] == "balanced"
        assert body["page"] == 1
        assert body["page_size"] == 20  # owner instruction 2026-09-13: default page size 20
        ranks = [r["rank"] for r in body["rows"]]
        assert ranks == sorted(ranks)
        assert len(body["rows"]) == 20

    def test_page_size_1000_returns_every_row_in_one_page(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/porchlamp/leaderboard", params={"page_size": 1000})
        body = resp.json()
        assert body["total"] == len(body["rows"])
        assert body["total"] >= 60

    def test_pagination_covers_every_row_exactly_once(self, server_url: str) -> None:
        page1 = httpx.get(f"{server_url}/v1/porchlamp/leaderboard", params={"page": 1, "page_size": 50}).json()
        page2 = httpx.get(f"{server_url}/v1/porchlamp/leaderboard", params={"page": 2, "page_size": 50}).json()
        keys1 = {(r["owner"], r["name"]) for r in page1["rows"]}
        keys2 = {(r["owner"], r["name"]) for r in page2["rows"]}
        assert not (keys1 & keys2), "the same row appeared on two pages"
        assert len(keys1) + len(keys2) == page1["total"]

    def test_invalid_page_size_is_rejected(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/porchlamp/leaderboard", params={"page_size": 37})
        assert resp.status_code == 400

    def test_eco_filter_matches_only_that_ecosystem(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/porchlamp/leaderboard", params={"eco": "pypi", "page_size": 1000})
        body = resp.json()
        assert body["rows"], "no pypi rows in the fixture"
        assert all(r["ecosystem"] == "pypi" for r in body["rows"])
        assert body["ecosystem"] == "pypi"

    def test_q_filter_matches_owner_or_name_case_insensitively(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/porchlamp/leaderboard", params={"q": "RIVERLINE", "page_size": 1000})
        body = resp.json()
        assert body["rows"]
        assert all("riverline" in f"{r['owner']}/{r['name']}".lower() for r in body["rows"])

    def test_q_filter_is_case_insensitive_on_the_ROW_side_too(self, server_url: str) -> None:
        # Mutation-battery follow-up (M3, survived): every fixture repo
        # used to be all-lowercase, so a mutant that removes the ROW
        # side's `.lower()` (leaving only the query lowercased) could not
        # be caught — a query already matched every row's raw casing.
        # tests/fixtures/ui/leaderboard.json now carries one mixed-case
        # repo ("Blue-Forge/Task-Runner") specifically so a lowercase
        # query against it only matches if the row side is ALSO lowered.
        resp = httpx.get(f"{server_url}/v1/porchlamp/leaderboard", params={"q": "blue-forge", "page_size": 1000})
        body = resp.json()
        assert body["rows"], "lowercase query must still match the mixed-case fixture row"
        assert any(r["owner"] == "Blue-Forge" for r in body["rows"])

    def test_sort_porchlamp_is_descending_by_default(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/porchlamp/leaderboard", params={"sort": "porchlamp", "page_size": 1000})
        porchlamps = [r["porchlamp"] for r in resp.json()["rows"]]
        assert porchlamps == sorted(porchlamps, reverse=True)

    def test_sort_minus_porchlamp_is_ascending(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/porchlamp/leaderboard", params={"sort": "-porchlamp", "page_size": 1000})
        porchlamps = [r["porchlamp"] for r in resp.json()["rows"]]
        assert porchlamps == sorted(porchlamps)

    def test_sort_tier_is_ascending_best_first(self, server_url: str) -> None:
        order = {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4, "F": 5}
        resp = httpx.get(f"{server_url}/v1/porchlamp/leaderboard", params={"sort": "tier", "page_size": 1000})
        ordinals = [order[r["tier"]] for r in resp.json()["rows"] if r["tier"]]
        assert ordinals == sorted(ordinals)

    def test_unsortable_field_is_rejected(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/porchlamp/leaderboard", params={"sort": "stars_audited"})
        assert resp.status_code == 400

    def test_first_timer_persona_drops_gated_rows_and_reorders(self, server_url: str) -> None:
        balanced = httpx.get(f"{server_url}/v1/porchlamp/leaderboard", params={"page_size": 1000}).json()
        first_timer = httpx.get(
            f"{server_url}/v1/porchlamp/leaderboard", params={"persona": "first_timer", "page_size": 1000}
        ).json()
        assert first_timer["persona"] == "first_timer"
        balanced_keys = {(r["owner"], r["name"]) for r in balanced["rows"]}
        ft_keys = {(r["owner"], r["name"]) for r in first_timer["rows"]}
        assert ft_keys < balanced_keys, "first_timer must drop at least one gated row"

    def test_counts_are_identical_across_filters(self, server_url: str) -> None:
        unfiltered = httpx.get(f"{server_url}/v1/porchlamp/leaderboard").json()
        filtered = httpx.get(f"{server_url}/v1/porchlamp/leaderboard", params={"eco": "pypi"}).json()
        assert unfiltered["counts"] == filtered["counts"], "gated/unrated counts must never change with filtering"


class TestNotRankedEndpoint:
    def test_shape_matches_fixture(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/porchlamp/not-ranked")
        assert resp.status_code == 200
        body = resp.json()
        expected = json.loads((FIXTURES_DIR / "notranked.json").read_text())
        assert body == expected
        assert body["persona_hidden"] == []

    def test_persona_param_returns_the_persona_hidden_group(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/porchlamp/not-ranked", params={"persona": "first_timer"})
        assert resp.status_code == 200
        body = resp.json()
        expected = json.loads((FIXTURES_DIR / "notranked-first_timer.json").read_text())
        assert body == expected
        assert body["persona_hidden"], "expected persona-hidden rows under first_timer"


class TestSnapshotEndpoint:
    def test_shape_matches_fixture(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/porchlamp/snapshot")
        assert resp.status_code == 200
        expected = json.loads((FIXTURES_DIR / "snapshot.json").read_text())
        assert resp.json() == expected

    def test_fail_snapshot_switch_returns_500(self, failing_snapshot_server_url: str) -> None:
        # Review 2026-09-13 NB-2/W-4: this is the exact response shape the
        # front end's footer/snapshot chip must stop treating as
        # "Loading..." forever (state.footerFieldsFor / app.js syncFooter).
        resp = httpx.get(f"{failing_snapshot_server_url}/v1/porchlamp/snapshot")
        assert resp.status_code == 500
        assert "error" in resp.json()

    def test_fail_snapshot_switch_does_not_affect_other_endpoints(self, failing_snapshot_server_url: str) -> None:
        resp = httpx.get(f"{failing_snapshot_server_url}/v1/porchlamp/leaderboard")
        assert resp.status_code == 200
        resp2 = httpx.get(f"{failing_snapshot_server_url}/healthz")
        assert resp2.status_code == 200


class TestEvidenceEndpoint:
    def test_known_repo_returns_its_evidence_fixture(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/porchlamp/repos/riverline/httpkit/evidence")
        assert resp.status_code == 200
        expected = json.loads((FIXTURES_DIR / "evidence-riverline__httpkit.json").read_text())
        assert resp.json() == expected

    def test_unknown_repo_is_404(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/porchlamp/repos/nobody/nothing/evidence")
        assert resp.status_code == 404

    def test_every_evidence_fixture_file_is_reachable_at_200(self, server_url: str) -> None:
        # Review Blocker B-7 regression test: the module docstring claimed
        # this test existed before it actually did, while
        # `evidence-ivywood-template-cli-71.json`'s owner/name were
        # silently mis-split (`ivywood-template` / `cli-71`) by the old
        # hyphen-guessing decoder, 404ing a REAL leaderboard row's
        # evidence forever. Every fixture file now round-trips at 200,
        # for owner/name pairs of every word-length combination present.
        evidence_files = sorted(FIXTURES_DIR.glob("evidence-*.json"))
        assert len(evidence_files) >= 3
        for path in evidence_files:
            stem = path.name[len("evidence-") : -len(".json")]
            owner, _, name = stem.partition("__")
            resp = httpx.get(f"{server_url}/v1/porchlamp/repos/{owner}/{name}/evidence")
            assert resp.status_code == 200, f"{path.name} ({owner}/{name}) did not resolve to 200"
            assert resp.json() == json.loads(path.read_text())

    def test_ivywood_template_cli_71_specifically_is_not_404(self, server_url: str) -> None:
        # The exact repo the review's live probe found broken (1 owner
        # word, 3 name words — the case the old "split hyphens in half"
        # heuristic got wrong).
        resp = httpx.get(f"{server_url}/v1/porchlamp/repos/ivywood/template-cli-71/evidence")
        assert resp.status_code == 200
        assert resp.json(), "expected non-empty evidence rows"


class TestRepoDetailEndpoint:
    """N3 (round-3 review, 2026-09-14): `GET /v1/porchlamp/repos/{owner}/{name}`
    (the drawer's own record fetch, `FixtureStore.find_repo_detail` —
    distinct from the `/evidence` sub-resource above) had NO test at all,
    positive or negative. The 404 case specifically matters because
    `views/drawer.js::renderDrawer` has a dedicated "genuinely not found"
    copy (`copy.DRAWER_NOT_FOUND_TEXT`, see the sibling render test in
    `web/v2/tests/unit.test.mjs`) that is only reachable for a REAL 404 —
    an empty `200 {}` would leave the drawer silently rendering nothing
    useful instead. `test_unknown_repo_is_404` is written to fail red
    against exactly that mutant.

     (2026-09-14): the success-path assertions below were rewritten
    for the flat `RepoDetailResponse` shape (this endpoint's OTHER
    producer, the real API, never sent `{kind, row}` — this server's own
    invented wrapper broke the drawer once pointed at the real API; see
    `tests/test_repo_detail_cross_check.py` for the two-producer parity
    test this defect's fix is bound to). Every success case here validates
    the actual response against `RepoDetailResponse` itself, not just a
    couple of hand-picked keys, since a shape-only check is exactly the
    class of defect that shipped unnoticed before."""

    def test_known_rated_repo_validates_and_carries_the_expected_facts(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/porchlamp/repos/riverline/httpkit")
        assert resp.status_code == 200
        body = resp.json()
        detail = RepoDetailResponse.model_validate(body)
        assert detail.owner == "riverline"
        assert detail.name == "httpkit"
        assert detail.eligible is True
        assert detail.rated is True
        assert detail.gate_reason is None
        assert detail.unrated_reason is None
        assert detail.rank is not None
        assert detail.porchlamp is not None

    def test_known_gated_repo_validates_and_carries_the_expected_facts(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/porchlamp/repos/gated-org/repo-mirror-0")
        assert resp.status_code == 200
        detail = RepoDetailResponse.model_validate(resp.json())
        assert detail.eligible is False
        assert detail.rated is False
        assert detail.gate_reason == "mirror"
        assert detail.rank is None
        assert detail.porchlamp is None
        assert detail.c_responsiveness is None
        # `coverage` is NOT NULL on the real store even for a never-scored
        # repo (`RepoDetailResponse`'s own docstring) — this fixture row's
        # own `coverage` (0.0) carries straight through, never fabricated.
        assert detail.coverage == 0.0

    def test_known_unrated_repo_validates_and_carries_the_expected_facts(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/porchlamp/repos/unrated-org/repo-insufficient_evidence-0")
        assert resp.status_code == 200
        detail = RepoDetailResponse.model_validate(resp.json())
        assert detail.eligible is True
        assert detail.rated is False
        assert detail.unrated_reason == "unrated: insufficient_evidence"
        assert detail.gate_reason is None
        assert detail.rank is None

    def test_a_gated_repo_with_no_fixture_coverage_defaults_to_zero_not_null(self, server_url: str) -> None:
        # `tests/fixtures/ui/notranked.json`'s "repo-non_software-5" has
        # `"coverage": null` — `RepoDetailResponse.coverage` is a required
        # `float`, never `None`, so this must default to 0.0 rather than
        # 500 on a validation error or silently omit the field.
        resp = httpx.get(f"{server_url}/v1/porchlamp/repos/gated-org/repo-non_software-5")
        assert resp.status_code == 200
        detail = RepoDetailResponse.model_validate(resp.json())
        assert detail.coverage == 0.0

    def test_unknown_repo_is_404_with_the_nested_error_body(self, server_url: str) -> None:
        # Mutant this kills: the handler answering an unknown repo with an
        # empty `200 {}` instead of calling `_send_error_json(404, ...)` —
        # every assertion below (status code AND body shape) must hold.
        resp = httpx.get(f"{server_url}/v1/porchlamp/repos/nobody/nothing")
        assert resp.status_code == 404
        body = resp.json()
        assert set(body.keys()) == {"error"}
        error = body["error"]
        assert error["status"] == 404
        assert error["endpoint"] == "/v1/porchlamp/repos/nobody/nothing"
        assert isinstance(error["detail"], str) and error["detail"]

    def test_malformed_path_is_404_not_a_500(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/porchlamp/repos/just-one-segment")
        assert resp.status_code == 404


class TestGatedRepoDrawerSections:
    """ (2026-09-14): the drawer's gated branch used to render ONLY
    the identity/gate-status section — "the reason sentence and nothing
    else above the fold" (06 §3 C) was implemented client-side
    (`views/drawer.js::renderDrawer`) as "nothing else, period", so a
    gated repo's real pillar-evidence, persona, and integrity facts were
    never reachable at all, even by clicking. Fixed: those three sections
    now render collapsed below the gate sentence; the composite section is
    omitted (no Porchlamp/tier/quadrant was ever computed for a gated repo).

    This is the fixture-server half of that fix: it proves the SAME two
    calls `app.js::openRepo` makes for ANY kind — `GET /v1/porchlamp/repos/{o}/
    {n}` and `GET /v1/porchlamp/repos/{o}/{n}/evidence` — both succeed for a
    GATED repo (i.e. evidence is fetched for gated repos exactly as for
    unrated/rated ones, never skipped on `kind`), and that the evidence
    payload actually carries rows for the drawer's now-reachable "Pillars
    and evidence" section to render once expanded — the render side of
    this same fixture repo is exercised live in a real browser (dynamic
    `import('/views/drawer.js')` against this same server, documented in
    this packet's handoff) since Node is not available on this box to run
    `web/v2/tests/unit.test.mjs` directly.
    """

    def test_gated_repo_with_a_real_evidence_fixture_is_fully_fetchable(self, server_url: str) -> None:
        # gated-org/repo-archived-1 is one of only 3 repos with a real
        # `evidence-*.json` fixture file (the other 2 are rated repos) —
        # deliberately chosen so this test exercises real evidence rows,
        # not a 404-tolerated empty set.
        detail_resp = httpx.get(f"{server_url}/v1/porchlamp/repos/gated-org/repo-archived-1")
        assert detail_resp.status_code == 200
        detail = RepoDetailResponse.model_validate(detail_resp.json())
        assert detail.eligible is False, "repo-archived-1 must classify as gated (classifyKind: !eligible)"
        assert detail.gate_reason == "archived"

        evidence_resp = httpx.get(f"{server_url}/v1/porchlamp/repos/gated-org/repo-archived-1/evidence")
        assert evidence_resp.status_code == 200, (
            "evidence must be fetchable for a gated repo exactly as for any other kind "
            "(app.js::openRepo calls loadEvidence unconditionally, never gated on `kind`)"
        )
        rows = evidence_resp.json()
        assert rows, "the Pillars and evidence section has nothing to render without this"
        assert rows[0]["pillar"] == "governance"
        assert rows[0]["unmeasured_reason"] == "not_applicable"

    def test_every_gated_fixture_repo_validates_with_no_composite_facts(self, server_url: str) -> None:
        # Every gated repo in the fixture (not just repo-archived-1) must
        # carry no Porchlamp/tier/quadrant — the contract-level reason the
        # drawer's Composite section is omitted for `kind === "gated"`.
        notranked = json.loads((FIXTURES_DIR / "notranked.json").read_text())
        gated_repos = notranked["gated"]
        assert len(gated_repos) >= 5, "expected the full set of gated fixture repos"
        for row in gated_repos:
            resp = httpx.get(f"{server_url}/v1/porchlamp/repos/{row['owner']}/{row['name']}")
            assert resp.status_code == 200
            detail = RepoDetailResponse.model_validate(resp.json())
            assert detail.eligible is False
            assert detail.porchlamp is None
            assert detail.tier is None
            assert detail.quadrant is None
            assert detail.persona_composites == {}
            assert detail.persona_gates == {}



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
                "porchlamp": round(composite, 2),
                "coverage": coverage,
                "tier": tier,
                "p_responsiveness": row["p_responsiveness"],
                "friction": row["friction"],
                "leverage": row["leverage"],
            }
        )
    scored.sort(
        key=lambda r: (
            -r["porchlamp"],
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
    #  every OTHER vector here still rates
    # all 73 fixture rows, so a mutant that skips the 0.60 coverage floor
    # entirely (RATED_MIN_COVERAGE -> 0.0) survives every one of them. This
    # one genuinely drops exactly one row on the real fixture (72 of 73;
    # verified live against the running server before being hardcoded here).
    {"weight_responsiveness": 0.05, "weight_criticality": 0.35, "weight_governance": 0.05, "weight_rigor": 0.05, "weight_accessibility": 0.50},
]


class TestReprojectParityAgainstHandOracle:
    @pytest.mark.parametrize("weights", WEIGHT_VECTORS, ids=[f"v{i}" for i in range(len(WEIGHT_VECTORS))])
    def test_server_matches_oracle_on_rank_porchlamp_coverage_tier(self, server_url: str, weights: dict[str, float]) -> None:
        resp = httpx.post(f"{server_url}/v1/porchlamp/reproject", json=weights)
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
            assert math.isclose(got["porchlamp"], exp["porchlamp"], abs_tol=1e-9), key
            assert math.isclose(got["coverage"], exp["coverage"], abs_tol=1e-6), key
            assert got["tier"] == exp["tier"], key

    def test_zero_pillar_weight_still_ranks_the_full_visible_set(self, server_url: str) -> None:
        weights = {"weight_responsiveness": 1.0, "weight_criticality": 0.0, "weight_governance": 0.0, "weight_rigor": 0.0, "weight_accessibility": 0.0}
        resp = httpx.post(f"{server_url}/v1/porchlamp/reproject", json=weights)
        body = resp.json()
        assert body["total"] > 0
        assert body["lens"] == "custom"
        ranks = [r["rank"] for r in body["rows"]]
        assert ranks == sorted(ranks) == list(range(1, len(ranks) + 1))

    def test_a_tilted_vector_genuinely_drops_a_row_below_the_rating_floor(self, server_url: str) -> None:
        """ proves the 0.60 floor is actually enforced
        under reprojection, not just under the balanced/persona paths —
        the vector itself is WEIGHT_VECTORS[-1], asserted by identity so
        this test breaks loudly if that vector's own coverage-crossing
        property is ever lost to an edit."""
        balanced_total = httpx.get(f"{server_url}/v1/porchlamp/leaderboard", params={"page_size": 1000}).json()["total"]
        resp = httpx.post(f"{server_url}/v1/porchlamp/reproject", json=WEIGHT_VECTORS[-1])
        assert resp.json()["total"] < balanced_total

    def test_balanced_weights_reproduce_the_stored_balanced_ranking(self, server_url: str) -> None:
        # D-AGG-1: the balanced weight vector IS `PILLAR_WEIGHTS`.
        balanced = {"weight_responsiveness": 0.25, "weight_criticality": 0.25, "weight_governance": 0.20, "weight_rigor": 0.15, "weight_accessibility": 0.15}
        resp = httpx.post(f"{server_url}/v1/porchlamp/reproject", json=balanced)
        top = resp.json()["rows"][0]
        stored_top = min(_leaderboard_rows(), key=lambda r: r["rank"])
        assert (top["owner"], top["name"]) == (stored_top["owner"], stored_top["name"])
        assert math.isclose(top["porchlamp"], stored_top["porchlamp"], abs_tol=1e-9)

    def test_non_negative_weights_that_do_not_sum_to_one_are_rejected(self, server_url: str) -> None:
        resp = httpx.post(
            f"{server_url}/v1/porchlamp/reproject",
            json={"weight_responsiveness": 0.5, "weight_criticality": 0.5, "weight_governance": 0.5, "weight_rigor": 0.0, "weight_accessibility": 0.0},
        )
        assert resp.status_code == 400

    def test_negative_weight_is_rejected(self, server_url: str) -> None:
        resp = httpx.post(
            f"{server_url}/v1/porchlamp/reproject",
            json={"weight_responsiveness": 1.5, "weight_criticality": -0.5, "weight_governance": 0.0, "weight_rigor": 0.0, "weight_accessibility": 0.0},
        )
        assert resp.status_code == 400

    def test_malformed_json_body_is_rejected(self, server_url: str) -> None:
        resp = httpx.post(f"{server_url}/v1/porchlamp/reproject", content=b"{not json", headers={"Content-Type": "application/json"})
        assert resp.status_code == 400

    def test_response_validates_against_the_contract(self, server_url: str) -> None:
        from porchlamp.serve.contract import ReprojectResponse

        resp = httpx.post(f"{server_url}/v1/porchlamp/reproject", json=WEIGHT_VECTORS[0])
        ReprojectResponse.model_validate(resp.json())

    def test_live_response_with_shifted_ranks_validates_with_correct_rank_delta_sign(
        self, server_url: str
    ) -> None:
        """ the balanced weight vector alone
        never exercises `rank_delta` (every row's rank is unchanged, so
        `rank_delta == 0` everywhere — a SIGN-FLIPPED implementation would
        pass just as well). A skewed weight vector genuinely reorders rows,
        so this both validates the LIVE response through
        `porchlamp.serve.contract.ReprojectResponse` (LeaderboardRow's own
        `rank_delta == balanced_rank - rank` validator, which raises on a
        wrong value) AND asserts the sign against an independent
        recomputation, so a hypothetical `rank - balanced_rank` mutant
        would fail both the contract validator and this assertion.
        """
        from porchlamp.serve.contract import ReprojectResponse

        # Pure-responsiveness: skewed enough from balanced to move ranks
        # (WEIGHT_VECTORS[1] in this file's own oracle-parity table).
        resp = httpx.post(f"{server_url}/v1/porchlamp/reproject", json=WEIGHT_VECTORS[1])
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
        resp = httpx.get(f"{server_url}/v1/porchlamp/matrix")
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
        body = httpx.get(f"{server_url}/v1/porchlamp/matrix").json()
        assert all(p["quadrant"] is not None for p in body["points"])

    def test_matrix_points_for_excludes_a_row_with_unmeasured_coordinates(self) -> None:
        """ the fixture happens to have no
        null-quadrant row today, so `test_every_point_carries_a_quadrant`
        above passes whether or not the exclusion filter actually exists —
        this constructs one directly, proving matrix_points_for's own
        `if r.get("quadrant") is not None` filter really excludes it."""
        base = dict(
            owner="x", name="y", ecosystem="pypi", leverage=50.0, friction=50.0,
            leverage_coverage=0.0, friction_coverage=0.0, stars_audited=None,
            porchlamp=50.0, coverage=0.6, tier="B",
        )
        rows = [
            {**base, "quadrant": None},  # every contributing pillar unmeasured -- cannot be plotted
            {**base, "name": "plottable", "quadrant": "Sweet Spot"},
        ]
        points = serve_ui_fixtures.matrix_points_for(rows)
        assert len(points) == 1
        assert points[0]["name"] == "plottable"

    def test_response_validates_against_the_contract(self, server_url: str) -> None:
        from porchlamp.serve.contract import MatrixResponse

        resp = httpx.get(f"{server_url}/v1/porchlamp/matrix")
        MatrixResponse.model_validate(resp.json())


class TestAxisCoverage:
    """ this used to compose the caption
    SENTENCE in Python and send it verbatim (a `06` §6 violation); now it
    sends the raw pillar/count/share numbers and `copy.js` composes the
    sentence — these tests check the numbers, `tests/test_ui_copy_usage.py`
    (via the exported-name check) covers the new copy.js function's own
    reachability, and the number/sentence combination is live-browser
    verified (`.agents//handoff.md`)."""

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
        from porchlamp.serve.contract import MatrixAxisCoverage

        rows = [
            {"p_responsiveness": 1.0, "p_governance": 1.0, "p_accessibility": None if i < 6 else 1.0, "p_criticality": 1.0, "p_rigor": 1.0}
            for i in range(20)
        ]
        coverage = serve_ui_fixtures.axis_coverage(rows)
        MatrixAxisCoverage.model_validate(coverage["friction"])


class TestMethodologyEndpoint:
    def test_serves_the_fixture_verbatim(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/porchlamp/methodology")
        assert resp.status_code == 200
        expected = json.loads((FIXTURES_DIR / "methodology.json").read_text())
        assert resp.json() == expected

    def test_response_validates_against_the_contract(self, server_url: str) -> None:
        from porchlamp.serve.contract import MethodologyResponse

        resp = httpx.get(f"{server_url}/v1/porchlamp/methodology")
        MethodologyResponse.model_validate(resp.json())


class TestErrorBodyShape:
    """ review addendum (2026-09-13): the real API's error body is
    `{"error": {endpoint, status, detail}}`, not a bare string —
    `web/v2/api.js` was found to never read `detail` at all. This class
    proves the fixture server actually sends that shape (so `api.js`'s
    `extractErrorDetail` has something real to parse) for both an
    input-validation 400 and a genuine `--fail`-simulated 500.
    """

    def test_400_error_body_is_the_nested_shape(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/porchlamp/leaderboard", params={"page_size": 37})
        assert resp.status_code == 400
        body = resp.json()
        assert set(body.keys()) == {"error"}
        error = body["error"]
        assert error["status"] == 400
        assert error["endpoint"] == "/v1/porchlamp/leaderboard"
        assert isinstance(error["detail"], str) and error["detail"]

    def test_fail_snapshot_returns_the_nested_shape_with_a_real_detail(
        self, failing_snapshot_server_url: str
    ) -> None:
        resp = httpx.get(f"{failing_snapshot_server_url}/v1/porchlamp/snapshot")
        assert resp.status_code == 500
        error = resp.json()["error"]
        assert error["status"] == 500
        assert error["endpoint"] == "/v1/porchlamp/snapshot"
        assert "simulated snapshot failure" in error["detail"]
        assert "--fail snapshot" in error["detail"]

    def test_query_string_is_stripped_from_the_endpoint_field(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/porchlamp/leaderboard", params={"page_size": 999999})
        assert resp.status_code == 400
        # The endpoint name must be the bare path, not `path?page_size=999999`
        # (api.js names failures by endpoint for the U-15 banner; a query
        # string baked in would make every distinct query its own "endpoint").
        assert resp.json()["error"]["endpoint"] == "/v1/porchlamp/leaderboard"
