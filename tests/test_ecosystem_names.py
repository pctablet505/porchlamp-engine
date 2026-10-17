"""Every page that names an ecosystem uses the list's name, never the registry's.

Owner decision (2026-10-17): "One list of ecosystem names in copy.js, in plain language, used everywhere (report, app
filters, Python pages): crates = Rust, pypi = Python, npm = JavaScript, go = Go, maven = Java, systems = C and C++."
The list is ``ECOSYSTEM_NAMES`` in ``web/app/copy.js`` (``report_pages.ECOSYSTEM_NAMES`` for Python). Ids, addresses and
data keys keep the engine's codes, so only the text a reader sees is scanned: the visible text of the landing pages, the
project pages and the report pages of the report tests' fixture export, and the app's ecosystem filter options.
"""
# ruff: noqa: F811  (the pytest fixture is imported by name from the report tests and then used as an argument)
from __future__ import annotations

import re
import shutil
from html.parser import HTMLParser
from pathlib import Path

import pytest

from porchlamp.serve import report_pages
from tests.test_report_pages import SNAPSHOT, built, export_static  # noqa: F401  (fixture)
from tests.test_ui_ecosystems import _listed_options

#: The registries' names, as a reader would see them. ``npm`` is matched as a word: a path such as ``/npm/`` in an
#: address is not text, and the scan reads text only.
REGISTRY_NAMES = {
    "PyPI": re.compile(r"\bPyPI\b"),
    "crates.io": re.compile(r"\bcrates\.io\b"),
    "Maven": re.compile(r"\bMaven\b"),
    "npm": re.compile(r"\bnpm\b"),
}

class _VisibleText(HTMLParser):
    """The text a reader sees: the title, the body's text and the meta description; script and style are skipped."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "meta" and dict(attrs).get("name") == "description":
            self.parts.append(dict(attrs).get("content") or "")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def visible_text(html: str) -> str:
    parser = _VisibleText()
    parser.feed(html)
    parser.close()
    return " ".join(" ".join(parser.parts).split())


def registry_hits(text: str) -> list[str]:
    return sorted(name for name, pattern in REGISTRY_NAMES.items() if pattern.search(text))


def list_paths(site: Path) -> list[Path]:
    """Every list view's page the export wrote: each address of each preset in the route table (``web/app/routes.js``),
    the ranking's pages included."""
    paths = []
    for route in export_static.app_routes():
        if route.get("fixed"):
            paths += sorted(site.glob(route["path"].strip("/").replace("{page}", "*") + "/index.html"))
    return paths


def project_paths(site: Path) -> list[Path]:
    return sorted((site / "projects").rglob("index.html"))


def report_paths(site: Path) -> list[Path]:
    return [site / "black-holes" / "index.html", site / "black-holes" / SNAPSHOT / "index.html"]


def pages_naming_a_registry(paths: list[Path], site: Path) -> dict[str, list[str]]:
    """``{page: [registry names]}`` for the pages whose visible text names one."""
    assert paths, "no page to scan"
    found = {}
    for path in paths:
        assert path.is_file(), f"{path.relative_to(site)} is missing from the export"
        hits = registry_hits(visible_text(path.read_text(encoding="utf-8")))
        if hits:
            found[path.relative_to(site).as_posix()] = hits
    return found


def test_no_list_page_names_a_registry(built):
    site = built[0]
    presets = [route for route in export_static.app_routes() if route.get("fixed")]
    assert len(list_paths(site)) >= len(presets)  # one page a preset at least, every ranking page beside them
    assert pages_naming_a_registry(list_paths(site), site) == {}


def test_no_project_page_names_a_registry(built):
    site = built[0]
    assert len(project_paths(site)) > 10
    assert pages_naming_a_registry(project_paths(site), site) == {}


def test_no_report_page_names_a_registry(built):
    site = built[0]
    assert pages_naming_a_registry(report_paths(site), site) == {}


def test_the_generated_home_page_llms_txt_and_feed_name_no_registry(built):
    """The export writes these three from the data (porchlamp/serve/home_pages.py): read as text, none names a registry,
    and each names the ecosystems the list's way where it names them."""
    site = built[0]
    home = visible_text((site / "index.html").read_text(encoding="utf-8"))
    files = {"index.html": home, **{name: (site / name).read_text(encoding="utf-8") for name in ("llms.txt", "feed.xml")}}
    assert {name: registry_hits(text) for name, text in files.items()} == {name: [] for name in files}
    assert all(f"[{name} projects that answer outside pull requests]" in files["llms.txt"] for name in report_pages.ECOSYSTEM_NAMES.values())


def test_the_filter_offers_the_lists_names_and_no_registry():
    options = _listed_options()
    assert [label for _value, label in options] == [report_pages.ECOSYSTEM_NAMES[value] for value, _label in options]
    assert len(options) == len(report_pages.ECOSYSTEM_NAMES)
    assert registry_hits(" ".join(label for _value, label in options)) == []


def test_each_ecosystem_page_says_its_ecosystem_with_the_lists_name(built):
    site = built[0]
    pages = [route for route in export_static.app_routes() if list((route.get("fixed") or {}).keys()) == ["eco"]]
    assert sorted(route["fixed"]["eco"] for route in pages) == sorted(report_pages.ECOSYSTEM_NAMES)
    for route in pages:
        text = visible_text((site / route["path"].strip("/") / "index.html").read_text(encoding="utf-8"))
        assert f"judged {report_pages.ECOSYSTEM_NAMES[route['fixed']['eco']]} projects" in text, route["path"]


@pytest.mark.parametrize("registry", sorted(REGISTRY_NAMES))
def test_a_registry_name_planted_in_a_copied_page_is_found(built, tmp_path, registry):
    site = tmp_path / "site"
    shutil.copytree(built[0], site)
    page = site / "rust" / "index.html"
    text = page.read_text(encoding="utf-8")
    assert pages_naming_a_registry([page], site) == {}
    page.write_text(text.replace("judged Rust projects", f"judged {registry} projects", 1), encoding="utf-8")
    assert pages_naming_a_registry([page], site) == {"rust/index.html": [registry]}


def test_a_registry_name_in_a_script_or_an_address_is_not_text():
    html = '<title>t</title><script>var s = "PyPI npm";</script><style>.npm{}</style><a href="/npm/crates.io/">Rust</a>'
    assert registry_hits(visible_text(html)) == []
    assert registry_hits(visible_text('<meta name="description" content="Among the 5 judged crates.io projects">')) == ["crates.io"]
