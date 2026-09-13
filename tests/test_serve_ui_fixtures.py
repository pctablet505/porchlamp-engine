"""Tests for `scripts/serve_ui_fixtures.py`: the stdlib fixture server that
lets `web/v2/` be built and exercised before W4-4's real API exists.

Starts the REAL `http.server.ThreadingHTTPServer` on an OS-assigned free
port (`--port 0`) in a background thread and drives it over real HTTP with
`httpx` — filter/sort/page semantics are exercised end-to-end, not mocked.
"""

from __future__ import annotations

import importlib.util
import json
import threading
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "serve_ui_fixtures.py"
WEB_DIR = REPO_ROOT / "web" / "v2"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "ui"


def _load_module():
    spec = importlib.util.spec_from_file_location("cvi_serve_ui_fixtures", SCRIPT_PATH)
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
        resp = httpx.get(f"{server_url}/v1/cvi/nonexistent")
        assert resp.status_code == 404
        assert "error" in resp.json()

    def test_path_traversal_is_forbidden(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/../../etc/passwd")
        # httpx normalizes ../ before sending; assert the server's own guard
        # holds even if a client somehow sends a raw traversal path.
        assert resp.status_code in (403, 404)


class TestLeaderboardEndpoint:
    def test_default_params_return_page_one_sorted_by_rank(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/cvi/leaderboard")
        assert resp.status_code == 200
        body = resp.json()
        assert body["persona"] == "balanced"
        assert body["page"] == 1
        assert body["page_size"] == 20  # owner instruction 2026-09-13: default page size 20
        ranks = [r["rank"] for r in body["rows"]]
        assert ranks == sorted(ranks)
        assert len(body["rows"]) == 20

    def test_page_size_1000_returns_every_row_in_one_page(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/cvi/leaderboard", params={"page_size": 1000})
        body = resp.json()
        assert body["total"] == len(body["rows"])
        assert body["total"] >= 60

    def test_pagination_covers_every_row_exactly_once(self, server_url: str) -> None:
        page1 = httpx.get(f"{server_url}/v1/cvi/leaderboard", params={"page": 1, "page_size": 50}).json()
        page2 = httpx.get(f"{server_url}/v1/cvi/leaderboard", params={"page": 2, "page_size": 50}).json()
        keys1 = {(r["owner"], r["name"]) for r in page1["rows"]}
        keys2 = {(r["owner"], r["name"]) for r in page2["rows"]}
        assert not (keys1 & keys2), "the same row appeared on two pages"
        assert len(keys1) + len(keys2) == page1["total"]

    def test_invalid_page_size_is_rejected(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/cvi/leaderboard", params={"page_size": 37})
        assert resp.status_code == 400

    def test_eco_filter_matches_only_that_ecosystem(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/cvi/leaderboard", params={"eco": "pypi", "page_size": 1000})
        body = resp.json()
        assert body["rows"], "no pypi rows in the fixture"
        assert all(r["ecosystem"] == "pypi" for r in body["rows"])
        assert body["ecosystem"] == "pypi"

    def test_q_filter_matches_owner_or_name_case_insensitively(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/cvi/leaderboard", params={"q": "RIVERLINE", "page_size": 1000})
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
        resp = httpx.get(f"{server_url}/v1/cvi/leaderboard", params={"q": "blue-forge", "page_size": 1000})
        body = resp.json()
        assert body["rows"], "lowercase query must still match the mixed-case fixture row"
        assert any(r["owner"] == "Blue-Forge" for r in body["rows"])

    def test_sort_cvi_is_descending_by_default(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/cvi/leaderboard", params={"sort": "cvi", "page_size": 1000})
        cvis = [r["cvi"] for r in resp.json()["rows"]]
        assert cvis == sorted(cvis, reverse=True)

    def test_sort_minus_cvi_is_ascending(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/cvi/leaderboard", params={"sort": "-cvi", "page_size": 1000})
        cvis = [r["cvi"] for r in resp.json()["rows"]]
        assert cvis == sorted(cvis)

    def test_sort_tier_is_ascending_best_first(self, server_url: str) -> None:
        order = {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4, "F": 5}
        resp = httpx.get(f"{server_url}/v1/cvi/leaderboard", params={"sort": "tier", "page_size": 1000})
        ordinals = [order[r["tier"]] for r in resp.json()["rows"] if r["tier"]]
        assert ordinals == sorted(ordinals)

    def test_unsortable_field_is_rejected(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/cvi/leaderboard", params={"sort": "stars_audited"})
        assert resp.status_code == 400

    def test_first_timer_persona_drops_gated_rows_and_reorders(self, server_url: str) -> None:
        balanced = httpx.get(f"{server_url}/v1/cvi/leaderboard", params={"page_size": 1000}).json()
        first_timer = httpx.get(
            f"{server_url}/v1/cvi/leaderboard", params={"persona": "first_timer", "page_size": 1000}
        ).json()
        assert first_timer["persona"] == "first_timer"
        balanced_keys = {(r["owner"], r["name"]) for r in balanced["rows"]}
        ft_keys = {(r["owner"], r["name"]) for r in first_timer["rows"]}
        assert ft_keys < balanced_keys, "first_timer must drop at least one gated row"

    def test_counts_are_identical_across_filters(self, server_url: str) -> None:
        unfiltered = httpx.get(f"{server_url}/v1/cvi/leaderboard").json()
        filtered = httpx.get(f"{server_url}/v1/cvi/leaderboard", params={"eco": "pypi"}).json()
        assert unfiltered["counts"] == filtered["counts"], "gated/unrated counts must never change with filtering"


class TestNotRankedEndpoint:
    def test_shape_matches_fixture(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/cvi/not-ranked")
        assert resp.status_code == 200
        body = resp.json()
        expected = json.loads((FIXTURES_DIR / "notranked.json").read_text())
        assert body == expected
        assert body["persona_hidden"] == []

    def test_persona_param_returns_the_persona_hidden_group(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/cvi/not-ranked", params={"persona": "first_timer"})
        assert resp.status_code == 200
        body = resp.json()
        expected = json.loads((FIXTURES_DIR / "notranked-first_timer.json").read_text())
        assert body == expected
        assert body["persona_hidden"], "expected persona-hidden rows under first_timer"


class TestSnapshotEndpoint:
    def test_shape_matches_fixture(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/cvi/snapshot")
        assert resp.status_code == 200
        expected = json.loads((FIXTURES_DIR / "snapshot.json").read_text())
        assert resp.json() == expected


class TestEvidenceEndpoint:
    def test_known_repo_returns_its_evidence_fixture(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/cvi/repos/riverline/httpkit/evidence")
        assert resp.status_code == 200
        expected = json.loads((FIXTURES_DIR / "evidence-riverline__httpkit.json").read_text())
        assert resp.json() == expected

    def test_unknown_repo_is_404(self, server_url: str) -> None:
        resp = httpx.get(f"{server_url}/v1/cvi/repos/nobody/nothing/evidence")
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
            resp = httpx.get(f"{server_url}/v1/cvi/repos/{owner}/{name}/evidence")
            assert resp.status_code == 200, f"{path.name} ({owner}/{name}) did not resolve to 200"
            assert resp.json() == json.loads(path.read_text())

    def test_ivywood_template_cli_71_specifically_is_not_404(self, server_url: str) -> None:
        # The exact repo the review's live probe found broken (1 owner
        # word, 3 name words — the case the old "split hyphens in half"
        # heuristic got wrong).
        resp = httpx.get(f"{server_url}/v1/cvi/repos/ivywood/template-cli-71/evidence")
        assert resp.status_code == 200
        assert resp.json(), "expected non-empty evidence rows"
