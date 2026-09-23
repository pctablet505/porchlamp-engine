"""Adversarial stress tests and empirical verification for Milestone 2.

Covers:
1. Static Serving & Content-Type Integrity:
   - GET / and GET /index.html return exact bytes of web/index.html with text/html.
   - HEAD, Range, Caching (ETag/304, If-Modified-Since/304), query parameters.
   - Method rejection (POST, PUT, DELETE return 405).
2. API Route Isolation & Hostile Precedence (Anti-Shadowing):
   - /healthz, /v1/porchlamp/matrix, /v1/porchlamp/repos/{o}/{n}, /v1/porchlamp/leaderboard, /v1/porchlamp/reproject,
     /openapi.json, /docs are never intercepted or shadowed.
   - Active collision attack: mount a directory containing files with exact API route paths.
   - Method enforcement on API routes (POST to GET routes -> 405).
3. 404 Handling & Path Traversal Immunity:
   - Missing files, missing html files, nested 404s.
   - Directory traversal attempts (/../, /%2e%2e/, symlinks outside web_dir).
   - URL-encoded null bytes (%00) and excessively long URLs (>4000 chars).
4. Edge Case Handling for web_dir:
   - web_dir is None: app builds cleanly, API routes work, root returns 404.
   - web_dir is non-existent string/Path: app builds cleanly, root returns 404.
   - web_dir is a file path: app builds cleanly, static mount bypassed, root returns 404.
   - web_dir is empty directory: root returns 404, API routes work.
   - web_dir is empty string: resolves to CWD (Path(".").is_dir() is True).
5. Concurrency & High Load:
   - Concurrent requests across static and API routes without deadlocks or 500s.
"""

from __future__ import annotations

import concurrent.futures
import os
from pathlib import Path
import tempfile
from typing import Callable

from fastapi.testclient import TestClient

from porchlamp.api import create_app
from porchlamp.lakehouse import OperationalStore
from porchlamp.models import Ecosystem, Quadrant, Tier
from tests.test_api import make_pillars, make_result, set_stars


def _seed_test_store() -> OperationalStore:
    """Seed a test store on the portal thread."""
    store = OperationalStore()
    store.upsert_composite(
        make_result(
            "testowner",
            "testrepo",
            85.0,
            Tier.S,
            Quadrant.SWEET_SPOT,
            80.0,
            20.0,
            ecosystem=Ecosystem.PYPI,
        ),
        pillar_scores=make_pillars(80, 80, 80, 80, 80),
    )
    set_stars(store, "testowner", "testrepo", 500)
    return store


def _create_test_client(
    seed: Callable[[], OperationalStore] = _seed_test_store,
    web_dir: str | Path | None = None,
    use_default_web: bool = False,
) -> tuple[TestClient, tempfile.TemporaryDirectory | None]:
    """Create a TestClient with thread-safe store initialization."""
    tmp_dir = None
    if not use_default_web and web_dir is None:
        # None passed explicitly means no static serving
        app = create_app(web_dir=None)
    elif use_default_web:
        app = create_app()
    else:
        app = create_app(web_dir=web_dir)

    client = TestClient(app)
    client.__enter__()
    app.state.store = client.portal.call(seed)
    return client, tmp_dir


# ============================================================================ #
# 1. STATIC SERVING & CONTENT-TYPE INTEGRITY
# ============================================================================ #

class TestStaticServingIntegrity:
    """Adversarially challenge static file serving for web/index.html."""

    def test_root_and_index_exact_bytes_and_headers(self) -> None:
        """GET / and GET /index.html must match web/index.html byte-for-byte."""
        repo_root = Path(__file__).resolve().parent.parent
        index_file = repo_root / "web" / "index.html"
        assert index_file.is_file(), f"Expected {index_file} to exist"
        disk_bytes = index_file.read_bytes()

        client, _ = _create_test_client(use_default_web=True)
        try:
            # Test GET /
            resp_root = client.get("/")
            assert resp_root.status_code == 200
            assert "text/html" in resp_root.headers["content-type"]
            assert resp_root.content == disk_bytes
            assert int(resp_root.headers["content-length"]) == len(disk_bytes)

            # Test GET /index.html
            resp_index = client.get("/index.html")
            assert resp_index.status_code == 200
            assert "text/html" in resp_index.headers["content-type"]
            assert resp_index.content == disk_bytes
            assert int(resp_index.headers["content-length"]) == len(disk_bytes)

            # Test with query parameters
            resp_query = client.get("/?tab=matrix&theme=dark")
            assert resp_query.status_code == 200
            assert resp_query.content == disk_bytes

            resp_index_query = client.get("/index.html?v=1.2.3")
            assert resp_index_query.status_code == 200
            assert resp_index_query.content == disk_bytes
        finally:
            client.__exit__(None, None, None)

    def test_http_methods_on_static_files(self) -> None:
        """Only GET and HEAD are permitted on static files; others must return 405."""
        client, _ = _create_test_client(use_default_web=True)
        try:
            # HEAD /
            r_head = client.head("/")
            assert r_head.status_code == 200
            assert "text/html" in r_head.headers["content-type"]
            assert len(r_head.content) == 0

            # HEAD /index.html
            r_head_idx = client.head("/index.html")
            assert r_head_idx.status_code == 200
            assert "text/html" in r_head_idx.headers["content-type"]
            assert len(r_head_idx.content) == 0

            # Disallowed methods
            assert client.post("/").status_code == 405
            assert client.put("/").status_code == 405
            assert client.delete("/").status_code == 405
            assert client.patch("/").status_code == 405
            assert client.post("/index.html").status_code == 405
            assert client.delete("/index.html").status_code == 405
        finally:
            client.__exit__(None, None, None)

    def test_caching_and_range_headers(self) -> None:
        """StaticFiles must support conditional requests (304) and Range requests (206)."""
        client, _ = _create_test_client(use_default_web=True)
        try:
            init_resp = client.get("/")
            etag = init_resp.headers.get("etag")
            last_mod = init_resp.headers.get("last-modified")
            assert etag is not None
            assert last_mod is not None

            # If-None-Match ETag
            r_etag = client.get("/", headers={"If-None-Match": etag})
            assert r_etag.status_code == 304
            assert len(r_etag.content) == 0

            # If-Modified-Since
            r_mod = client.get("/", headers={"If-Modified-Since": last_mod})
            assert r_mod.status_code == 304
            assert len(r_mod.content) == 0

            # Byte range request
            r_range = client.get("/", headers={"Range": "bytes=0-99"})
            assert r_range.status_code == 206
            assert len(r_range.content) == 100
            assert "bytes 0-99/" in r_range.headers.get("content-range", "")
        finally:
            client.__exit__(None, None, None)


# ============================================================================ #
# 2. API ROUTE PRECEDENCE & ANTI-SHADOWING ATTACK
# ============================================================================ #

class TestRoutePrecedenceAndAntiShadowing:
    """Stress-test that static mounting never shadows or intercepts API endpoints."""

    def test_baseline_api_routes_not_shadowed(self) -> None:
        """Verify baseline API routes respond with application/json."""
        client, _ = _create_test_client(use_default_web=True)
        try:
            r_h = client.get("/healthz")
            assert r_h.status_code == 200
            assert r_h.headers["content-type"] == "application/json"
            assert r_h.json() == {"status": "ok"}

            r_m = client.get("/v1/porchlamp/matrix")
            assert r_m.status_code == 200
            assert r_m.headers["content-type"] == "application/json"
            assert "nodes" in r_m.json()

            r_l = client.get("/v1/porchlamp/leaderboard")
            assert r_l.status_code == 200
            assert r_l.headers["content-type"] == "application/json"
            assert "items" in r_l.json()

            r_r = client.get("/v1/porchlamp/repos/testowner/testrepo")
            assert r_r.status_code == 200
            assert r_r.headers["content-type"] == "application/json"
            assert r_r.json()["owner"] == "testowner"
        finally:
            client.__exit__(None, None, None)

    def test_active_collision_shadowing_attack(self) -> None:
        """Adversarial Attack: Populate web_dir with static files whose paths
        are IDENTICAL to API routes.
        Verify that FastAPI serves API handlers rather than static files.
        """
        tmp_dir = tempfile.TemporaryDirectory()
        try:
            web_p = Path(tmp_dir.name)

            # Plant colliding files
            (web_p / "healthz").write_text("COLLISION_HEALTHZ")
            (web_p / "healthz.html").write_text("COLLISION_HEALTHZ_HTML")
            (web_p / "v1").mkdir()
            (web_p / "v1" / "porchlamp").mkdir()
            (web_p / "v1" / "porchlamp" / "matrix").write_text("COLLISION_MATRIX")
            (web_p / "v1" / "porchlamp" / "leaderboard").write_text("COLLISION_LEADERBOARD")
            (web_p / "v1" / "porchlamp" / "reproject").write_text("COLLISION_REPROJECT")
            (web_p / "v1" / "porchlamp" / "repos").mkdir()
            (web_p / "v1" / "porchlamp" / "repos" / "testowner").mkdir()
            (web_p / "v1" / "porchlamp" / "repos" / "testowner" / "testrepo").write_text("COLLISION_REPO")

            # Plant docs collisions
            (web_p / "openapi.json").write_text("COLLISION_OPENAPI")
            (web_p / "docs").write_text("COLLISION_DOCS")

            # Plant legitimate static files
            (web_p / "index.html").write_text("LEGITIMATE_INDEX_HTML")
            (web_p / "app.js").write_text("console.log('legitimate');")

            client, _ = _create_test_client(web_dir=web_p)
            try:
                # 1. /healthz must hit API handler, NOT static file
                r1 = client.get("/healthz")
                assert r1.status_code == 200
                assert r1.headers["content-type"] == "application/json"
                assert r1.json() == {"status": "ok"}
                assert "COLLISION" not in r1.text

                # 2. /v1/porchlamp/matrix must hit API handler
                r2 = client.get("/v1/porchlamp/matrix")
                assert r2.status_code == 200
                assert r2.headers["content-type"] == "application/json"
                assert "nodes" in r2.json()
                assert "COLLISION" not in r2.text

                # 3. /v1/porchlamp/leaderboard must hit API handler
                r3 = client.get("/v1/porchlamp/leaderboard")
                assert r3.status_code == 200
                assert r3.headers["content-type"] == "application/json"
                assert "items" in r3.json()
                assert "COLLISION" not in r3.text

                # 4. /v1/porchlamp/repos/testowner/testrepo must hit API handler
                r4 = client.get("/v1/porchlamp/repos/testowner/testrepo")
                assert r4.status_code == 200
                assert r4.headers["content-type"] == "application/json"
                assert r4.json()["owner"] == "testowner"
                assert "COLLISION" not in r4.text

                # 5. POST /v1/porchlamp/reproject must hit API handler
                r5 = client.post(
                    "/v1/porchlamp/reproject",
                    json={
                        "weights": {
                            "responsiveness": 0.2,
                            "criticality": 0.2,
                            "governance": 0.2,
                            "rigor": 0.2,
                            "accessibility": 0.2,
                        }
                    },
                )
                assert r5.status_code == 200
                assert isinstance(r5.json(), list)

                # 6. OpenAPI and Docs must hit FastAPI internal routes
                r_oa = client.get("/openapi.json")
                assert r_oa.status_code == 200
                assert "openapi" in r_oa.json()
                assert "COLLISION" not in r_oa.text

                r_docs = client.get("/docs")
                assert r_docs.status_code == 200
                assert "swagger" in r_docs.text.lower()
                assert "COLLISION" not in r_docs.text

                # 7. Legitimate non-colliding static files must STILL be served
                r_idx = client.get("/")
                assert r_idx.status_code == 200
                assert r_idx.text == "LEGITIMATE_INDEX_HTML"

                r_js = client.get("/app.js")
                assert r_js.status_code == 200
                assert "console.log" in r_js.text
            finally:
                client.__exit__(None, None, None)
        finally:
            tmp_dir.cleanup()

    def test_method_not_allowed_on_api_endpoints(self) -> None:
        """POST to GET-only API endpoint must return 405 Method Not Allowed,
        proving it is matched by the router and not swallowed by static mount.
        """
        client, _ = _create_test_client(use_default_web=True)
        try:
            assert client.post("/healthz").status_code == 405
            assert client.post("/v1/porchlamp/matrix").status_code == 405
            assert client.post("/v1/porchlamp/leaderboard").status_code == 405
        finally:
            client.__exit__(None, None, None)


# ============================================================================ #
# 3. 404 HANDLING & PATH TRAVERSAL IMMUNITY
# ============================================================================ #

class TestNotFoundAndPathTraversal:
    """Stress-test non-existent file handling and path traversal attacks."""

    def test_nonexistent_files_return_404(self) -> None:
        """Non-existent files at all levels must return 404."""
        client, _ = _create_test_client(use_default_web=True)
        try:
            assert client.get("/nonexistent_file_xyz.txt").status_code == 404
            assert client.get("/nonexistent.html").status_code == 404
            assert client.get("/deeply/nested/static/file.js").status_code == 404
            assert client.get("/static/").status_code == 404
        finally:
            client.__exit__(None, None, None)

    def test_path_traversal_attempts_return_404(self) -> None:
        """Traversal attempts (.., %2e%2e, symlinks) must return 404 and never leak files."""
        tmp_dir = tempfile.TemporaryDirectory()
        try:
            web_p = Path(tmp_dir.name)
            (web_p / "index.html").write_text("SAFE_INDEX")

            # Secret file outside web_dir
            secret_p = web_p.parent / "secret_adversarial_token.txt"
            secret_p.write_text("SUPER_SECRET_VALUE_DO_NOT_LEAK")

            # Outside symlink inside web_dir
            symlink_p = web_p / "leak_symlink.txt"
            os.symlink(str(secret_p), str(symlink_p))

            client, _ = _create_test_client(web_dir=web_p)
            try:
                traversal_attacks = [
                    "/../secret_adversarial_token.txt",
                    "/../../secret_adversarial_token.txt",
                    "/%2e%2e/secret_adversarial_token.txt",
                    "/%2e%2e/%2e%2e/secret_adversarial_token.txt",
                    "/..%2fsecret_adversarial_token.txt",
                    "/%2e%2e%2fsecret_adversarial_token.txt",
                    "/%2e%2e%5csecret_adversarial_token.txt",
                    "/.%2e/secret_adversarial_token.txt",
                    "/....//secret_adversarial_token.txt",
                    "/leak_symlink.txt",
                ]

                for attack in traversal_attacks:
                    resp = client.get(attack)
                    assert resp.status_code == 404, f"Path traversal {attack} gave {resp.status_code}"
                    assert "SUPER_SECRET_VALUE" not in resp.text
            finally:
                client.__exit__(None, None, None)
                if secret_p.exists():
                    secret_p.unlink()
        finally:
            tmp_dir.cleanup()

    def test_special_characters_and_overflow(self) -> None:
        """Verify URL-encoded null bytes, unicode, and ultra-long URLs return 404."""
        client, _ = _create_test_client(use_default_web=True)
        try:
            # URL-encoded null byte
            r_null = client.get("/null%00byte.html")
            assert r_null.status_code == 404

            # Unicode path
            r_uni = client.get("/%E6%B5%8B%E8%AF%95.html")
            assert r_uni.status_code == 404

            # Path length overflow (> 4,000 characters)
            r_long = client.get("/" + "longpath_" * 500)
            assert r_long.status_code == 404
        finally:
            client.__exit__(None, None, None)


# ============================================================================ #
# 4. EDGE CASE HANDLING FOR web_dir
# ============================================================================ #

class TestWebDirEdgeCases:
    """Stress-test create_app with None, nonexistent, file, and empty web_dir."""

    def test_web_dir_none(self) -> None:
        """When web_dir=None, static files are not mounted; API still works."""
        client, _ = _create_test_client(web_dir=None)
        try:
            assert client.get("/healthz").status_code == 200
            assert client.get("/v1/porchlamp/matrix").status_code == 200
            assert client.get("/").status_code == 404
            assert client.get("/index.html").status_code == 404
        finally:
            client.__exit__(None, None, None)

    def test_web_dir_nonexistent_string_and_path(self) -> None:
        """When web_dir does not exist, mounting is safely bypassed."""
        # Nonexistent str
        app_str = create_app(web_dir="/nonexistent/absent_path_123456789")
        client_str = TestClient(app_str)
        with client_str:
            assert client_str.get("/healthz").status_code == 200
            assert client_str.get("/").status_code == 404

        # Nonexistent Path
        app_path = create_app(web_dir=Path("/nonexistent/absent_path_123456789"))
        client_path = TestClient(app_path)
        with client_path:
            assert client_path.get("/healthz").status_code == 200
            assert client_path.get("/").status_code == 404

    def test_web_dir_pointing_to_file(self) -> None:
        """When web_dir points to a regular file (not dir), mounting is bypassed."""
        repo_root = Path(__file__).resolve().parent.parent
        file_path = repo_root / "pyproject.toml"
        assert file_path.is_file()

        app = create_app(web_dir=file_path)
        client = TestClient(app)
        with client:
            assert client.get("/healthz").status_code == 200
            assert client.get("/").status_code == 404
            assert client.get("/pyproject.toml").status_code == 404

    def test_web_dir_empty_directory(self) -> None:
        """When web_dir exists but is completely empty, 404 on /, API works."""
        tmp_dir = tempfile.TemporaryDirectory()
        try:
            empty_p = Path(tmp_dir.name)
            app = create_app(web_dir=empty_p)
            client = TestClient(app)
            with client:
                assert client.get("/healthz").status_code == 200
                assert client.get("/").status_code == 404
                assert client.get("/index.html").status_code == 404
        finally:
            tmp_dir.cleanup()

    def test_web_dir_empty_string_resolves_to_cwd(self) -> None:
        """Adversarial check: web_dir="" in Python resolves Path("").is_dir() to True (CWD).
        Verify empirical behavior when web_dir="" is supplied.
        """
        app = create_app(web_dir="")
        client = TestClient(app)
        with client:
            assert client.get("/healthz").status_code == 200
            # Since CWD is repo root, pyproject.toml is served if requested
            r_file = client.get("/pyproject.toml")
            assert r_file.status_code == 200
            assert "project" in r_file.text


# ============================================================================ #
# 5. CONCURRENCY & HIGH-LOAD HARNESS
# ============================================================================ #

class TestStaticServingConcurrency:
    """Stress-test concurrent requests across static and API endpoints."""

    def test_high_concurrency_mixed_traffic(self) -> None:
        """Fire 200 concurrent requests across /, /index.html, /healthz, /v1/porchlamp/matrix, 404s."""
        client, _ = _create_test_client(use_default_web=True)
        try:
            endpoints = [
                ("/", 200),
                ("/index.html", 200),
                ("/healthz", 200),
                ("/v1/porchlamp/matrix", 200),
                ("/nonexistent_404", 404),
            ] * 40  # 200 total requests

            def run_request(item: tuple[str, int]) -> tuple[str, int, int]:
                url, expected_code = item
                resp = client.get(url)
                return url, resp.status_code, expected_code

            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                results = list(executor.map(run_request, endpoints))

            for url, actual_code, expected_code in results:
                assert actual_code == expected_code, f"URL {url} returned {actual_code}, expected {expected_code}"
        finally:
            client.__exit__(None, None, None)
