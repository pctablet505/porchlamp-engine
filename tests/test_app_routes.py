"""Every view of the app at one real address (``web/app/routes.js``): the route table the export reads is the one the
app runs on, ``404.html`` is the app with ``index.html``'s head and frame, and the site preview answers addresses the
way GitHub Pages does.

The export's own pages are checked in ``tests/test_static_export.py`` (a page per view) and the app's routing in
``web/app/tests/unit.test.mjs`` ("routes: ..."). An absent Node.js interpreter fails this module, as it fails
``tests/test_ui_node_suite.py`` (H4), unless ``PORCHLAMP_ALLOW_MISSING_NODE=1``.
"""
from __future__ import annotations

import functools
import http.client
import http.server
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from test_static_export import export_static, verify_static_export  # noqa: E402

from porchlamp.serve import project_pages  # noqa: E402

WEB_APP = ROOT / "web" / "app"


def _serve_script():
    # By path, never via sys.path: scripts/serve.py would shadow tests/serve.
    spec = importlib.util.spec_from_file_location("serve_script", ROOT / "scripts" / "serve.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _node() -> str:
    node = verify_static_export.find_node()
    if node is None:
        if os.environ.get("PORCHLAMP_ALLOW_MISSING_NODE") == "1":
            pytest.skip("no Node.js interpreter found (PORCHLAMP_ALLOW_MISSING_NODE=1)")
        pytest.fail("no Node.js interpreter found on PATH, in PORCHLAMP_NODE_BIN, or in ~/.local/opt/node-*")
    return node


def test_the_export_reads_the_route_table_the_app_runs_on():
    script = "const m = await import(process.argv[1]); process.stdout.write(JSON.stringify(m.ROUTES));"
    ran = subprocess.run(
        [_node(), "--input-type=module", "-e", script, (WEB_APP / "routes.js").as_uri()],
        capture_output=True, text=True, timeout=60, check=True,
    )
    in_node = json.loads(ran.stdout)
    assert len(in_node) >= 8, in_node
    assert export_static.app_routes() == in_node


def test_the_route_table_reader_stops_on_a_table_that_is_not_json(tmp_path, monkeypatch):
    broken = tmp_path / "routes.js"
    broken.write_text(
        export_static.ROUTES_JS.read_text(encoding="utf-8").replace('"shell": true}', "shell: true}", 1), encoding="utf-8"
    )
    monkeypatch.setattr(export_static, "ROUTES_JS", broken)
    with pytest.raises(SystemExit, match="no JSON route table"):
        export_static.app_routes()


#: What a page of the app shares with ``index.html``: the policy, the viewport, the icon, the stylesheets, the script,
#: and the frame the app draws into.
_SHARED_HEAD = (
    r'<meta http-equiv="Content-Security-Policy" content="[^"]*">',
    r'<meta name="viewport" content="[^"]*">',
    r'<meta name="theme-color" content="[^"]*">',
    r'<link rel="icon" href="[^"]*" type="image/svg\+xml">',
    r'<link rel="stylesheet" href="[^"]*">',
    r'<script type="module" src="[^"]*"></script>',
)
_FRAME_IDS = (
    "app-header-controls", "app-primary-nav", "app-main", "app-footer", "app-drawer-root", "app-keymap-root",
    "app-live-region",
)
#: Every address the browser fetches on its own: icons, stylesheets, scripts and images.
_ASSET_URL = re.compile(r'<(?:link rel="(?:icon|stylesheet)"[^>]*href|script[^>]*src|img[^>]*src)="([^"]*)"')


def _page(name: str) -> str:
    return (WEB_APP / name).read_text(encoding="utf-8")


def test_404_html_is_the_app_with_index_html_s_head_and_frame_and_no_home_text():
    index, missing = _page("index.html"), _page("404.html")
    for pattern in _SHARED_HEAD:
        assert re.findall(pattern, missing) == re.findall(pattern, index), pattern
        assert re.findall(pattern, index), f"index.html has no {pattern}"
    frame = lambda text: re.findall(r'<[a-z]+ (?:class="[^"]*" )?id="(app-[a-z-]+)"', text)  # noqa: E731
    assert frame(missing) == frame(index) == list(_FRAME_IDS)
    # The home page's own text and data stay on the home page.
    assert '<link rel="canonical"' not in missing and 'application/ld+json' not in missing
    assert 'class="seo-fallback"' not in missing and "og:url" not in missing


@pytest.mark.parametrize("name", ["index.html", "404.html"])
def test_every_address_a_page_of_the_app_fetches_starts_at_the_site_root(name):
    urls = _ASSET_URL.findall(_page(name))
    assert len(urls) >= 6, urls  # the icon, four stylesheets and the script, at least
    for url in urls:
        assert url.startswith("/") and not url.startswith("//"), f"{name}: {url} depends on the page's own address"


def test_a_project_panel_and_the_project_s_own_page_have_one_title():
    project = next(route for route in export_static.app_routes() if route["id"] == "project")
    for owner, name in (("ggml-org", "llama.cpp"), ("WordPress", "gutenberg")):
        assert project["title"].replace("{owner}", owner).replace("{name}", name) == project_pages.project_title(owner, name)


@pytest.fixture()
def preview(tmp_path):
    site = tmp_path / "site"
    (site / "matrix").mkdir(parents=True)
    (site / "data").mkdir()
    (site / "index.html").write_text("home", encoding="utf-8")
    (site / "matrix" / "index.html").write_text("matrix view", encoding="utf-8")
    (site / "404.html").write_text("the app", encoding="utf-8")
    handler = functools.partial(_serve_script().StaticSiteHandler, directory=str(site))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def get(path: str) -> tuple[int, str, str]:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        conn.request("GET", path)
        response = conn.getresponse()
        answer = (response.status, response.getheader("Location") or "", response.read().decode("utf-8"))
        conn.close()
        return answer

    yield get
    server.shutdown()
    server.server_close()


def test_the_site_preview_answers_addresses_the_way_github_pages_does(preview):
    assert preview("/") == (200, "", "home")
    assert preview("/matrix/") == (200, "", "matrix view")
    assert preview("/matrix")[:2] == (301, "/matrix/")  # a directory's address without its closing slash
    assert preview("/projects/acme/widget/") == (404, "", "the app")  # no file: 404.html, where the app opens the panel
    assert preview("/data/") == (404, "", "the app")  # a directory without index.html is not listed
