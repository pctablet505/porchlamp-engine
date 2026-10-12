"""Security hardening regression and PoC verification suite.

Verifies remediation of the 5 security findings across the Porchlamp repository:
1. CWE-770 / CWE-400: Bounded request body size (64 KiB) and Content-Length
   validation on ``POST /v1/porchlamp/reproject`` in both ``scripts/serve_ui_fixtures.py``
   and ``porchlamp/serve/app.py``.
2. CWE-693: Mandatory HTTP security headers (``Content-Security-Policy``,
   ``X-Content-Type-Options``, ``X-Frame-Options``, ``Referrer-Policy``,
   ``Permissions-Policy``) on both API and static responses.
3. CWE-88 / CWE-20 / CWE-22: Strict ``owner``/``name`` and ``gh api`` REST path
   validation blocking CLI flag injection (``--hostname=...``), path traversal
   (``..``), and GraphQL string interpolation injection.
4. CWE-918 / CWE-319: ``https://`` scheme and host allowlist enforcement before
   ``urllib.request.urlopen`` in collector and derivation modules.
5. CWE-79: Removal of inline ``onclick`` handlers with dynamic interpolation
   and escaping of dynamic fields in ``web/index.html``.
"""

from __future__ import annotations

import http.client
import re
import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from porchlamp.collect import depsdev, governance, pike_signals, repo_object
from porchlamp.serve.app import MAX_REQUEST_BODY_BYTES, create_app
from scripts import derive as derive_script
from scripts import gather as gather_script
from scripts import serve_ui_fixtures
from serve.helpers import build_snapshot, finalize, fresh_db


REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_V2_DIR = serve_ui_fixtures.DEFAULT_WEB_DIR
FIXTURES_DIR = serve_ui_fixtures.DEFAULT_FIXTURES_DIR
LEGACY_INDEX_HTML = REPO_ROOT / "web" / "index.html"


@pytest.fixture()
def fixture_server():
    httpd = serve_ui_fixtures.serve(
        host="127.0.0.1",
        port=0,
        web_dir=WEB_V2_DIR,
        fixtures_dir=FIXTURES_DIR,
    )
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield ("127.0.0.1", port)
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture()
def fastapi_client(tmp_path: Path) -> TestClient:
    db_path = tmp_path / "porchlamp_sec.sqlite"
    conn = fresh_db(db_path)
    build_snapshot(conn, db_path)
    finalize(conn, db_path)
    conn.close()
    app = create_app(db_path, web_dir=WEB_V2_DIR)
    return TestClient(app)


# --------------------------------------------------------------------------- #
# 1. CWE-770 / CWE-400: Request Body Size Cap (64 KiB) & Content-Length       #
# --------------------------------------------------------------------------- #


def test_fixture_server_rejects_oversized_post_body_with_413(fixture_server) -> None:
    host, port = fixture_server
    conn = http.client.HTTPConnection(host, port, timeout=5)
    oversized = b'{"weight_responsiveness":0.2}' + (b" " * (MAX_REQUEST_BODY_BYTES + 100))
    conn.request(
        "POST",
        "/v1/porchlamp/reproject",
        body=oversized,
        headers={"Content-Type": "application/json", "Content-Length": str(len(oversized))},
    )
    resp = conn.getresponse()
    body = resp.read().decode("utf-8")
    conn.close()
    assert resp.status == 413
    assert "exceeds" in body


def test_fixture_server_rejects_negative_content_length_with_400(fixture_server) -> None:
    host, port = fixture_server
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.putrequest("POST", "/v1/porchlamp/reproject")
    conn.putheader("Content-Type", "application/json")
    conn.putheader("Content-Length", "-5")
    conn.endheaders()
    resp = conn.getresponse()
    conn.close()
    assert resp.status == 400


def test_fastapi_rejects_oversized_post_body_with_413(fastapi_client: TestClient) -> None:
    oversized = b'{"weight_responsiveness":0.2}' + (b" " * (MAX_REQUEST_BODY_BYTES + 100))
    resp = fastapi_client.post(
        "/v1/porchlamp/reproject",
        content=oversized,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413
    assert "exceeds" in resp.json()["error"]["detail"]


def test_fastapi_rejects_a_chunked_body_with_411(fastapi_client: TestClient) -> None:
    """A chunked body declares no Content-Length, so the cap above would never see it."""
    resp = fastapi_client.post(
        "/v1/porchlamp/reproject",
        content=iter([b'{"weight_responsiveness":0.2}']),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 411
    assert "Content-Length" in resp.json()["error"]["detail"]


# --------------------------------------------------------------------------- #
# 2. CWE-693: Mandatory HTTP Security Headers                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "header_name,expected_sub",
    [
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "strict-origin-when-cross-origin"),
        ("Permissions-Policy", "geolocation=()"),
        ("Content-Security-Policy", "default-src 'self'"),
    ],
)
def test_security_headers_present_on_fastapi_and_fixture_server(
    fastapi_client: TestClient,
    fixture_server,
    header_name: str,
    expected_sub: str,
) -> None:
    resp = fastapi_client.get("/healthz")
    assert resp.status_code == 200
    assert expected_sub in resp.headers.get(header_name, "")

    host, port = fixture_server
    for path in ("/healthz", "/index.html"):
        conn = http.client.HTTPConnection(host, port, timeout=5)
        conn.request("GET", path)
        f_resp = conn.getresponse()
        f_resp.read()
        conn.close()
        assert expected_sub in (f_resp.getheader(header_name) or "")


# --------------------------------------------------------------------------- #
# 3. CWE-88 / CWE-20 / CWE-22: Subprocess & GraphQL Argument Validation       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "malicious_segment",
    [
        "--hostname=evil.example.com",
        "-XPOST",
        "../secret",
        "..",
        ".",
        'owner") { __schema { types { name } } } #',
        "owner/extra",
        "owner\nname",
        "",
    ],
)
def test_repo_segment_validation_blocks_injection_across_collectors(
    malicious_segment: str, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="invalid GitHub"):
        gather_script.gather_repo(malicious_segment, "repo", "pypi")
    with pytest.raises(ValueError, match="invalid GitHub"):
        gather_script.gather_repo("owner", malicious_segment, "pypi")
    with pytest.raises(ValueError, match="invalid GitHub"):
        repo_object.collect_repo_object(tmp_path, malicious_segment, "repo", "main")
    with pytest.raises(ValueError, match="invalid GitHub"):
        governance.fetch_file_text(malicious_segment, "repo")


@pytest.mark.parametrize(
    "malicious_path",
    [
        "--hostname=evil.example.com",
        "-HAuthorization:Bearer",
        "repos/owner/../../admin",
        "repos/owner/name\r\nX-Injected: 1",
        "",
    ],
)
def test_gh_api_path_validation_blocks_flag_and_traversal_injection(
    malicious_path: str,
) -> None:
    with pytest.raises(ValueError, match="invalid gh api path"):
        gather_script._gh(malicious_path)
    with pytest.raises(ValueError, match="invalid gh api path"):
        repo_object._gh_rest(malicious_path)
    with pytest.raises(ValueError, match="invalid gh api path"):
        governance.GhApiTransport().call(malicious_path, {})
    with pytest.raises(ValueError, match="invalid gh api path"):
        pike_signals.GhRestTransport(sleep_seconds=0).get(malicious_path)


# --------------------------------------------------------------------------- #
# 4. CWE-918 / CWE-319: URL Scheme & Host Allowlist Validation (urlopen)      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad_url",
    [
        "file:///etc/passwd",
        "http://raw.githubusercontent.com/owner/name/HEAD/README.md",
        "https://evil.example.com/owner/name/HEAD/README.md",
        "https://169.254.169.254/latest/meta-data/",
        "https://raw.githubusercontent.com/owner/name/HEAD/../../secret",
    ],
)
def test_urlopen_wrappers_reject_non_https_and_untrusted_hosts(bad_url: str) -> None:
    with pytest.raises(ValueError, match="disallowed"):
        gather_script._fetch_json(bad_url)
    with pytest.raises(ValueError, match="disallowed"):
        repo_object._fetch_body(bad_url)
    with pytest.raises(ValueError, match="disallowed"):
        depsdev._get_json(bad_url)


def test_derive_and_governance_file_fetchers_reject_path_traversal(tmp_path: Path) -> None:
    fetcher = governance.fetch_file_text("pallets", "flask")
    with pytest.raises(ValueError, match="invalid governance file path"):
        fetcher("../../etc/passwd")
    with pytest.raises(ValueError, match="invalid governance file name"):
        derive_script._body(tmp_path, "pallets", "flask", "../secret.txt", fetch=True)


# --------------------------------------------------------------------------- #
# 5. CWE-79: Legacy web/index.html DOM XSS Hardening                          #
# --------------------------------------------------------------------------- #


def test_legacy_web_index_html_has_no_inline_onclick_with_interpolated_data() -> None:
    html = LEGACY_INDEX_HTML.read_text(encoding="utf-8")
    # Ensure no inline onclick handler interpolates ${...} template expressions
    inline_onclick_interpolations = re.findall(r'onclick="[^"]*\$\{[^"]*"', html)
    assert inline_onclick_interpolations == [], (
        f"Found unsafe inline onclick interpolation(s): {inline_onclick_interpolations}"
    )
