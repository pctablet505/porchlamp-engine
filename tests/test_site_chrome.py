"""One chrome, one policy, a named script allowlist and a methodology that needs no JavaScript, on a real ``--stats`` export.

``scripts/export_static.py --stats`` writes every page of the site from ``web/app/404.html`` and then runs
``web/prerender.mjs`` over all of them. This module exports the report-pages fixture (``tests/test_report_pages.py``) and
proves, page by page:

* every page's header, nav and footer are what ``web/app/components/chrome.js`` draws for that kind of page
  (``node web/prerender.mjs --chrome``), the nav labelled "Primary";
* every page carries one Content-Security-Policy meta, the same string everywhere, the app shell's own;
* every page loads the scripts of its kind and no others (``report_pages.PAGE_SCRIPTS``), and no inline script but
  structured data;
* ``/theme-init.js`` runs in ``<head>`` before the stylesheets, as a plain script, on every page;
* the exported methodology page holds its headings, ``data-v`` hooks, links and anchors in the HTML itself, with the
  values of the export's own record, and is what the Node prerender draws from that record in any locale and time zone;
* each project page is what the Node prerender draws from the project's record in any locale and time zone, and a real
  browser, in two locales and time zones, draws the same project view byte for byte;
* in a real browser a stored theme shows on a static and an app page, and the toggle switches it;
* the lens addresses and the data link in ``copy.js`` are the Python pages' own;
* ``scripts/verify_static_export.py`` passes the export, and names a methodology page whose database digest is not the
  verifier's own sha256 of the database file (even where the export's manifest agrees with the page) and a page
  without its nav.

Each checker returns a list of problems; a test also runs it on a broken copy of a page (a plant) and expects the page
to be named, so a checker that cannot fail would show.
"""
# ruff: noqa: F811  (the ``built`` fixture is imported by name from the report tests and then used as an argument)
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
from decimal import ROUND_HALF_UP, Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))  # serve.helpers -> store.helpers, as test_report_pages does

import ui_browser  # noqa: E402
from test_report_pages import PAGES as REPORT_PAGES  # noqa: E402
from test_report_pages import SNAPSHOT, built, export_static, report_record, verify_static_export  # noqa: E402,F401

from porchlamp.serve import report_pages  # noqa: E402

APP_SHELL = ROOT / "web" / "app" / "404.html"
APP_INDEX = ROOT / "web" / "app" / "index.html"
PRERENDER = ROOT / "web" / "prerender.mjs"
CSP_META = re.compile(r'<meta http-equiv="Content-Security-Policy" content="([^"]*)">')


# --------------------------------------------------------------------------- #
# The pages of the site, by kind
# --------------------------------------------------------------------------- #
class Page(NamedTuple):
    chrome: str  # the --chrome page spec, as JSON: {"view": id} for a page of the app, else {"nav": entry or null}
    scripts: str  # the key of report_pages.PAGE_SCRIPTS


def _pages(site: Path) -> dict[str, Page]:
    """Every ``index.html`` and the 404 page of the exported site, by path, each with its kind. The set is checked
    against the files on disk, so a page this table does not name stops the module instead of going unchecked."""
    app = lambda view: Page(json.dumps({"view": view}), "app")  # noqa: E731
    nowhere = json.dumps({"nav": None})
    pages: dict[str, Page] = {}
    for route in export_static.app_routes():
        if not route["shell"]:
            continue
        if route.get("fixed"):  # a list view (the leaderboard's or the not-ranked view's) at each of its addresses
            for path in sorted(site.glob(route["path"].strip("/").replace("{page}", "*") + "/index.html")):
                address = "/" + path.parent.relative_to(site).as_posix() + "/"
                pages[path.relative_to(site).as_posix()] = Page(json.dumps({"view": route["view"], "address": address}), "app")
            continue
        pages["index.html" if route["path"] == "/" else route["path"].strip("/") + "/index.html"] = app(route["id"])
    pages["404.html"] = app("notfound")
    # /black-holes/ is the app's report view (a route above); its frozen copy is the same view on a page with no app
    pages[REPORT_PAGES[1]] = Page(json.dumps({"view": "report"}), "page")
    for path in sorted(site.glob("projects/*/*/index.html")):
        pages[path.relative_to(site).as_posix()] = Page(nowhere, "project")
    # /check/ is a stub with no chrome (verify_projects checks it)
    on_disk = ({path.relative_to(site).as_posix() for path in site.rglob("index.html")} - {"check/index.html"}) | {"404.html"}
    assert on_disk == set(pages), f"unclassified: {sorted(on_disk - set(pages))}; missing: {sorted(set(pages) - on_disk)}"
    return pages


def _snapshot_id(site: Path) -> str:
    ids = [path.name for path in (site / "data").iterdir() if path.is_dir()]
    assert ids == [SNAPSHOT], ids
    return ids[0]


def _node() -> str:
    node = export_static.find_node()
    assert node, "no Node.js for web/prerender.mjs (PATH, PORCHLAMP_NODE_BIN or ~/.local/opt/node-*)"
    return node


def _copy_pages(site: Path, dest: Path, rels) -> None:
    """The pages ``rels`` of ``site`` under ``dest``, byte for byte: what a plant breaks one of."""
    for rel in rels:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        (dest / rel).write_bytes((site / rel).read_bytes())


def _read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


def _rewrite(root: Path, rel: str, old: str, new: str) -> None:
    """``rel`` under ``root`` with the one ``old`` replaced by ``new`` (a plant that misses its target stops here)."""
    text = _read(root, rel)
    assert text.count(old) == 1, f"{rel}: the plant's target {old[:60]!r} is not there exactly once"
    (root / rel).write_text(text.replace(old, new), encoding="utf-8")


class _Scan(HTMLParser):
    """Every start tag of a page in order, with its attributes and whether it sits in ``<head>``; inline script text."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str], bool]] = []
        self.inline_scripts: list[tuple[dict[str, str], str]] = []
        self._in_head = False
        self._script: tuple[dict[str, str], list[str]] | None = None

    def handle_starttag(self, tag, attrs):
        attributes = {name: value or "" for name, value in attrs}
        if tag == "head":
            self._in_head = True
        self.tags.append((tag, attributes, self._in_head))
        if tag == "script" and "src" not in attributes:
            self._script = (attributes, [])

    def handle_endtag(self, tag):
        if tag == "head":
            self._in_head = False
        if tag == "script" and self._script is not None:
            self.inline_scripts.append((self._script[0], "".join(self._script[1])))
            self._script = None

    def handle_data(self, data):
        if self._script is not None:
            self._script[1].append(data)


def _scan(text: str) -> _Scan:
    scan = _Scan()
    scan.feed(text)
    scan.close()
    return scan


# --------------------------------------------------------------------------- #
# 1. The chrome
# --------------------------------------------------------------------------- #
#: The empty frame of web/app/404.html, as the export leaves it filled: the element and its inner HTML.
_FRAME = {
    "header": re.compile(r'<header class="app-header" id="app-header-controls">(.*?)</header>', re.S),
    "nav": re.compile(r'<nav class="app-primary-nav" id="app-primary-nav" aria-label="Primary">(.*?)</nav>', re.S),
    "footer": re.compile(r'<footer class="app-footer" id="app-footer">(.*?)</footer>', re.S),
}


def _expected_chrome(site: Path, pages: dict[str, Page], tmp: Path) -> dict[str, dict[str, str]]:
    """What chrome.js draws for each kind of page: ``node web/prerender.mjs --chrome``, once per kind."""
    snapshot = site / "data" / _snapshot_id(site) / "snapshot.json"
    drawn = {}
    for index, spec in enumerate(sorted({page.chrome for page in pages.values()})):
        job = tmp / f"chrome-{index}.json"
        job.write_text(spec, encoding="utf-8")
        ran = subprocess.run([_node(), str(PRERENDER), "--chrome", str(snapshot), str(job)], capture_output=True, text=True, check=False)
        assert ran.returncode == 0, ran.stderr[-1500:]
        drawn[spec] = json.loads(ran.stdout)
        assert set(drawn[spec]) == set(_FRAME) and all(drawn[spec].values()), (spec, sorted(drawn[spec]))
    return drawn


def chrome_problems(root: Path, pages: dict[str, Page], expected: dict[str, dict[str, str]]) -> list[str]:
    problems = []
    for rel, page in pages.items():
        text = _read(root, rel)
        for name, pattern in _FRAME.items():
            opened, found = len(re.findall(rf'<{name}\b[^>]*\bid="app-', text)), pattern.findall(text)
            if opened != 1 or len(found) != 1:
                problems.append(f"{rel}: wants one <{name}> of the app frame (a nav labelled Primary); has {opened} and {len(found)} complete")
            elif found[0] != expected[page.chrome][name]:
                problems.append(f"{rel}: the {name} is not what chrome.js draws for this page")
    return problems


def test_every_page_carries_the_header_nav_and_footer_chrome_js_draws(built, tmp_path):
    site = built[0]
    pages = _pages(site)
    assert len(pages) > 300 and {page.scripts for page in pages.values()} == {"app", "page", "project"}
    expected = _expected_chrome(site, pages, tmp_path)
    lists = {page.chrome for page in pages.values() if '"address"' in page.chrome}  # a list view at one of its addresses
    assert {json.loads(spec)["view"] for spec in lists} == {"ranked", "notranked"}  # the leaderboard's and the not-ranked view's
    assert len({page.chrome for page in pages.values()} - lists) == 9  # eight app views (the report among them), every page with no nav entry
    report = expected[json.dumps({"view": "report"})]["nav"]
    assert report.count('aria-current="page"') == 1 and 'href="/black-holes/" data-route-tab="report" aria-current="page"' in report
    assert 'aria-current' not in expected[json.dumps({"nav": None})]["nav"]
    assert chrome_problems(site, pages, expected) == []

    project = next(rel for rel, page in pages.items() if page.scripts == "project")
    landing = "python/index.html"
    planted = tmp_path / "planted"
    _copy_pages(site, planted, [project, landing])
    nav = _FRAME["nav"].search(_read(planted, project))
    (planted / project).write_text(_read(planted, project).replace(nav.group(0), nav.group(0).replace(nav.group(1), "")), encoding="utf-8")
    footer = _FRAME["footer"].search(_read(planted, landing))
    (planted / landing).write_text(_read(planted, landing).replace(footer.group(0), ""), encoding="utf-8")
    problems = chrome_problems(planted, {project: pages[project], landing: pages[landing]}, expected)
    assert sorted(problem.split(":")[0] for problem in problems) == sorted([project, landing]), problems
    assert any("nav" in problem for problem in problems if problem.startswith(project))
    assert any("footer" in problem for problem in problems if problem.startswith(landing))


# --------------------------------------------------------------------------- #
# 2. The content security policy
# --------------------------------------------------------------------------- #
def csp_problems(root: Path, rels, want: str) -> list[str]:
    problems = []
    for rel in rels:
        policies = [
            attrs.get("content") for tag, attrs, _ in _scan(_read(root, rel)).tags
            if tag == "meta" and attrs.get("http-equiv", "").lower() == "content-security-policy"
        ]
        if len(policies) != 1:
            problems.append(f"{rel}: wants one Content-Security-Policy meta; has {len(policies)}")
        elif policies[0] != want:
            problems.append(f"{rel}: its Content-Security-Policy is not the app shell's")
    return problems


def test_every_page_carries_one_content_security_policy_the_same_everywhere(built, tmp_path):
    site = built[0]
    pages = _pages(site)
    shell, index = CSP_META.findall(APP_SHELL.read_text(encoding="utf-8")), CSP_META.findall(APP_INDEX.read_text(encoding="utf-8"))
    assert len(shell) == 1 and shell == index, "web/app/404.html and web/app/index.html state one policy, the same"
    want = shell[0]
    assert "script-src 'self'" in want
    assert csp_problems(site, pages, want) == []

    first, second = "projects/acme/flagged-repo/index.html", "python/index.html"
    planted = tmp_path / "planted"
    _copy_pages(site, planted, [first, second])
    meta = CSP_META.search(_read(planted, first)).group(0)
    _rewrite(planted, first, meta, meta + meta)
    _rewrite(planted, second, meta, meta.replace("script-src 'self'", "script-src 'self' 'unsafe-inline'"))
    problems = csp_problems(planted, [first, second], want)
    assert [problem.split(":")[0] for problem in problems] == [first, second], problems


# --------------------------------------------------------------------------- #
# 3. The scripts a page may load
# --------------------------------------------------------------------------- #
def script_problems(root: Path, pages: dict[str, Page]) -> list[str]:
    problems = []
    for rel, page in pages.items():
        scan = _scan(_read(root, rel))
        sources = [attrs["src"] for tag, attrs, _ in scan.tags if tag == "script" and "src" in attrs]
        if sources != list(report_pages.PAGE_SCRIPTS[page.scripts]):
            problems.append(f"{rel}: a {page.scripts} page loads {list(report_pages.PAGE_SCRIPTS[page.scripts])}; this one loads {sources}")
        for attrs, body in scan.inline_scripts:
            if attrs.get("type") != "application/ld+json":
                problems.append(f"{rel}: an inline script that is not structured data: {body.strip()[:40]!r}")
    return problems


def test_each_page_loads_only_the_scripts_its_kind_allows(built, tmp_path):
    site = built[0]
    pages = _pages(site)
    assert script_problems(site, pages) == []
    assert report_pages.PAGE_SCRIPTS["app"] == ("/theme-init.js", "/app.js")  # the allowlist this checks against, as named

    landing, project = "go/index.html", "projects/acme/tie2-measured/index.html"
    planted = tmp_path / "planted"
    _copy_pages(site, planted, [landing, project])
    _rewrite(planted, landing, "</body>", '<script type="module" src="/extra.js"></script>\n</body>')
    _rewrite(planted, project, '<script type="module" src="/back-forward.js"></script>',
             '<script type="module" src="/back-forward.js"></script><script src="https://cdn.example.net/tracker.js"></script>')
    problems = script_problems(planted, {landing: pages[landing], project: pages[project]})
    assert [problem.split(":")[0] for problem in problems] == [landing, project], problems

    inline = tmp_path / "inline"
    _copy_pages(site, inline, [landing])
    _rewrite(inline, landing, "</body>", "<script>alert(1)</script>\n</body>")
    assert [problem.split(":")[0] for problem in script_problems(inline, {landing: pages[landing]})] == [landing]


# --------------------------------------------------------------------------- #
# 4. The theme before the stylesheets
# --------------------------------------------------------------------------- #
def theme_problems(root: Path, rels) -> list[str]:
    problems = []
    for rel in rels:
        tags = _scan(_read(root, rel)).tags
        themes = [i for i, (tag, attrs, _) in enumerate(tags) if tag == "script" and attrs.get("src") == "/theme-init.js"]
        sheets = [i for i, (tag, attrs, _) in enumerate(tags) if tag == "link" and attrs.get("rel") == "stylesheet"]
        if len(themes) != 1 or not sheets:
            problems.append(f"{rel}: wants one /theme-init.js script and a stylesheet; has {len(themes)} and {len(sheets)}")
            continue
        _, attrs, in_head = tags[themes[0]]
        if not in_head:
            problems.append(f"{rel}: /theme-init.js is not in <head>")
        if themes[0] > sheets[0]:
            problems.append(f"{rel}: /theme-init.js comes after the first stylesheet")
        if attrs != {"src": "/theme-init.js"}:
            problems.append(f"{rel}: /theme-init.js must be a plain script; it has {sorted(attrs)}")
    return problems


def test_the_theme_runs_in_head_before_the_stylesheets_on_every_page(built, tmp_path):
    site = built[0]
    pages = _pages(site)
    assert theme_problems(site, pages) == []

    plain = '<script src="/theme-init.js"></script>'
    deferred, late = "projects/acme/flagged-repo/index.html", "methodology/index.html"
    planted = tmp_path / "planted"
    _copy_pages(site, planted, [deferred, late])
    _rewrite(planted, deferred, plain, '<script src="/theme-init.js" defer></script>')
    _rewrite(planted, late, plain, "")
    _rewrite(planted, late, "</head>", plain + "\n</head>")
    problems = theme_problems(planted, [deferred, late])
    assert [problem.split(":")[0] for problem in problems] == [deferred, late], problems
    assert "plain script" in problems[0] and "after the first stylesheet" in problems[1]


# --------------------------------------------------------------------------- #
# 5. The methodology page without JavaScript
# --------------------------------------------------------------------------- #
class _Node:
    def __init__(self, tag: str, attrs: dict[str, str], parent: "_Node | None") -> None:
        self.tag, self.attrs, self.parent, self.kids = tag, attrs, parent, []

    def text(self) -> str:
        return "".join(kid if isinstance(kid, str) else kid.text() for kid in self.kids)

    def walk(self):
        for kid in self.kids:
            if isinstance(kid, _Node):
                yield kid
                yield from kid.walk()


class _Tree(HTMLParser):
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__()
        self.root = self._at = _Node("#document", {}, None)

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, {name: value or "" for name, value in attrs}, self._at)
        self._at.kids.append(node)
        if tag not in self._VOID:
            self._at = node

    def handle_endtag(self, tag):
        if tag in self._VOID:
            return
        up = self._at
        while up.parent is not None and up.tag != tag:
            up = up.parent
        if up.tag == tag and up.parent is not None:
            self._at = up.parent

    def handle_data(self, data):
        self._at.kids.append(data)


def _tree(text: str) -> _Node:
    parser = _Tree()
    parser.feed(text)
    parser.close()
    return parser.root


def _percent(weight: float) -> str:
    return f"{(Decimal(str(weight)) * 100).quantize(Decimal(1), ROUND_HALF_UP)}%"


SECTION_LABELS = ("Snapshot", "Gates", "Pillars", "Parameters", "Missingness", "Personas", "Tie-breakers", "Detectors",
                  "Limits", "Process constants", "Change log")
OLD_ANCHORS = ("gates", "pillars", "parameters", "missing-data", "lenses", "tie-break", "limits", "reproduce", "hashes",
               *(f"d-gate-{n}" for n in range(1, 7)), "d-evid-1")


def _data_files(site: Path) -> tuple[dict, dict, dict]:
    folder = site / "data" / _snapshot_id(site)
    return tuple(json.loads((folder / f"{name}.json").read_text(encoding="utf-8")) for name in ("methodology", "snapshot", "manifest"))


def methodology_problems(text: str, record: tuple[dict, dict, dict]) -> list[str]:
    methodology, snapshot, manifest = record
    root = _tree(text)
    nodes = list(root.walk())
    problems = []
    h1 = [node.text() for node in nodes if node.tag == "h1"]
    if h1 != ["Methodology and snapshot"]:
        problems.append(f"the page's h1 is {h1}, not ['Methodology and snapshot']")
    h2 = {node.text() for node in nodes if node.tag == "h2"}
    problems += [f"no h2 {label!r}" for label in SECTION_LABELS if label not in h2]

    hooks: dict[str, list[str]] = {}
    for node in nodes:
        if "data-v" in node.attrs:
            hooks.setdefault(node.attrs["data-v"], []).append(node.text())
    if sum(map(len, hooks.values())) < 100:
        problems.append(f"{sum(map(len, hooks.values()))} data-v hooks; wants at least 100")
    expected = {f"pillar.{p['name']}.weight": _percent(p["weight"]) for p in methodology["pillars"]}
    expected |= {
        f"persona.{lens}.{pillar}": _percent(weight)
        for lens, weights in methodology["personas"]["presets"].items() for pillar, weight in weights.items()
    }
    expected |= {f"parameter.{p['input_id']}.min_n": "—" if p["min_n"] is None else str(p["min_n"]) for p in methodology["parameters"]}
    expected |= {f"snapshot.{key}": snapshot[key] for key in ("manifest_root", "constants_hash", "reproduce_command")}
    expected["snapshot.source_db_sha256"] = manifest["source_db_sha256"]
    for name, want in expected.items():
        if not hooks.get(name):
            problems.append(f"no data-v hook {name}")
        elif set(hooks[name]) != {want}:
            problems.append(f"data-v {name} reads {sorted(set(hooks[name]))}, the record says {want!r}")

    anchors = [(node.attrs["href"], node.text()) for node in nodes if node.tag == "a" and "href" in node.attrs]
    names = report_pages.copy_js_map("PERSONA_NAMES")
    for lens, slug in report_pages.LENS_SLUGS.items():
        if (f"/{slug}/", names[lens]) not in anchors:
            problems.append(f"no link {names[lens]!r} to /{slug}/")
    if any(href == "/check/" for href, _ in anchors):
        problems.append("a link to /check/, a stub")
    check_line = report_pages.copy_js_string("METHODOLOGY_CHECK_LINE")
    if not any(node.text() == check_line for node in nodes if node.tag == "p"):
        problems.append(f"no paragraph {check_line!r}")
    ids = {node.attrs["id"] for node in nodes if "id" in node.attrs}
    problems += [f"no element with id {anchor!r}" for anchor in OLD_ANCHORS if anchor not in ids]

    limits = [li.text() for section in nodes if section.attrs.get("id") == "methodology-limits" for li in section.walk() if li.tag == "li"]
    if limits != report_pages.copy_js_list("LIMITS"):
        problems.append(f"the Limits items are {limits}, not copy.js LIMITS")
    return problems


def report_limits(text: str) -> list[str]:
    """The items of the report's "What this is not" list."""
    for node in _tree(text).walk():
        if node.tag == "h2" and node.attrs.get("id") == "what-this-is-not":
            siblings = node.parent.kids
            following = next(kid for kid in siblings[siblings.index(node) + 1 :] if isinstance(kid, _Node) and kid.tag == "ul")
            return [li.text() for li in following.kids if isinstance(li, _Node) and li.tag == "li"]
    return []


def test_the_methodology_page_holds_its_headings_hooks_and_links_without_javascript(built, tmp_path):
    site = built[0]
    record = _data_files(site)
    text = _read(site, "methodology/index.html")
    assert 'data-prerendered="methodology"' in text
    assert methodology_problems(text, record) == []
    limits = report_pages.copy_js_list("LIMITS")
    assert len(limits) == 3 and report_limits(_read(site, "black-holes/index.html"))[:3] == limits  # the report says what the methodology says

    planted = tmp_path / "planted"
    _copy_pages(site, planted, ["methodology/index.html"])
    _rewrite(planted, "methodology/index.html", 'data-v="pillar.responsiveness.weight">25%', 'data-v="pillar.responsiveness.weight">26%')
    _rewrite(planted, "methodology/index.html", ">Detectors</h2>", ">Sensors</h2>")
    _rewrite(planted, "methodology/index.html", limits[1], limits[1] + " (changed)")
    _rewrite(planted, "methodology/index.html", '<a href="/career/">', '<a href="/careers/">')
    problems = methodology_problems(_read(planted, "methodology/index.html"), record)
    for wanted in ("pillar.responsiveness.weight", "no h2 'Detectors'", "the Limits items", "/career/"):
        assert any(wanted in problem for problem in problems), (wanted, problems)
    assert len(problems) == 4, problems


# --------------------------------------------------------------------------- #
# 6. The methodology page is the Node prerender's output
# --------------------------------------------------------------------------- #
def _prerender_methodology(site: Path, tmp: Path, env: dict[str, str], *, mutate=None) -> bytes:
    """The methodology page as ``web/prerender.mjs`` draws it from the export's record, through the export's api.js,
    in a clean directory."""
    sid = _snapshot_id(site)
    route = next(r for r in export_static.app_routes() if r["id"] == "methodology")
    (tmp / "methodology").mkdir(parents=True)
    (tmp / "methodology" / "index.html").write_text(export_static.view_page(route), encoding="utf-8")
    for name in ("api.js", "data-version.js"):
        (tmp / name).write_bytes((site / name).read_bytes())
    (tmp / "data" / sid).mkdir(parents=True)
    for name in ("snapshot", "methodology", "manifest", "report"):
        data = (site / "data" / sid / f"{name}.json").read_text(encoding="utf-8")
        if mutate and name == mutate[0]:
            data = mutate[1](data)
        (tmp / "data" / sid / f"{name}.json").write_text(data, encoding="utf-8")
    job = tmp / "job.json"
    job.write_text(json.dumps({"pages": [{"path": "methodology/index.html", "view": "methodology"}]}), encoding="utf-8")
    ran = subprocess.run([_node(), str(PRERENDER), str(tmp), str(job)], capture_output=True, text=True, env={**os.environ, **env}, check=False)
    assert ran.returncode == 0, ran.stderr[-1500:]
    return (tmp / "methodology" / "index.html").read_bytes()


def test_the_methodology_page_is_what_the_prerender_draws_in_any_locale_and_time_zone(built, tmp_path):
    site = built[0]
    exported = (site / "methodology" / "index.html").read_bytes()
    utc = _prerender_methodology(site, tmp_path / "utc", {"TZ": "UTC", "LANG": "C", "LC_ALL": "C"})
    far = _prerender_methodology(
        site, tmp_path / "kiritimati", {"TZ": "Pacific/Kiritimati", "LANG": "de_DE.UTF-8", "LC_ALL": "de_DE.UTF-8"}
    )
    assert len(exported) > 20000
    assert utc == exported
    assert far == exported

    # the comparison can fail: a record that differs by one weight draws a different page
    changed = _prerender_methodology(
        site, tmp_path / "changed", {"TZ": "UTC"}, mutate=("methodology", lambda data: data.replace('"weight":0.25', '"weight":0.26', 1))
    )
    assert changed != exported


# --------------------------------------------------------------------------- #
# 6b. A project page is the prerender's output, and a browser draws the same view
# --------------------------------------------------------------------------- #
#: The project view as its page holds it: what web/prerender.mjs drew into <main>.
_PROJECT_VIEW = re.compile(r'<main class="app-main" id="app-main" tabindex="-1">\n(.*)\n *</main>', re.S)


def _sample_projects(site: Path) -> dict[str, str]:
    """Three project pages, ``{path: "owner/name"}``: the first, one whose reply time was too small a sample, and one
    where both values were."""
    found: dict[str, str] = {}
    wanted = (r"Outside pull requests to ", r"For [^ ]+, the reply time has too small", r"For [^ ]+, the reply time and the share")
    for pattern in wanted:
        for path in sorted(site.glob("projects/*/*/index.html")):
            text = path.read_text(encoding="utf-8")
            if re.search(r'id="answer" data-v="answer">' + pattern, text):
                name = re.search(r'<h1 id="project-title" class="project-title"><span class="repo-owner">([^<]+)/</span>([^<]+)</h1>', text)
                found[path.relative_to(site).as_posix()] = f"{name.group(1)}/{name.group(2)}"
                break
    assert len(found) == 3, found
    return found


def _prerender_projects(site: Path, tmp: Path, env: dict[str, str], pages: dict[str, str], *, mutate=None) -> dict[str, bytes]:
    """The project pages ``pages`` as ``web/prerender.mjs`` draws them from the export's records, through the export's
    api.js, in a clean directory: each page's shell is the export's (``project_pages.project_page``, with the page's own
    description), its header, nav, footer and view drawn afresh."""
    from porchlamp.serve import project_pages

    sid = _snapshot_id(site)
    for name in ("api.js", "data-version.js"):
        (tmp / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp / name).write_bytes((site / name).read_bytes())
    data = tmp / "data" / sid
    (data / "repos").mkdir(parents=True)
    for name in ("snapshot", "methodology", "manifest"):
        (data / f"{name}.json").write_bytes((site / "data" / sid / f"{name}.json").read_bytes())
    for bucket in (site / "data" / sid / "repos").glob("*.json"):
        text = bucket.read_text(encoding="utf-8")
        (data / "repos" / bucket.name).write_text(mutate(text) if mutate else text, encoding="utf-8")
    for rel, key in pages.items():
        description = re.search(r'<meta name="description" content="([^"]*)"', _read(site, rel)).group(1)
        owner, name = key.split("/")
        (tmp / rel).parent.mkdir(parents=True)
        (tmp / rel).write_text(project_pages.project_page(owner, name, html.unescape(description)), encoding="utf-8")
    job = tmp / "job.json"
    job.write_text(json.dumps({"pages": [{"path": rel, "nav": None, "project": key} for rel, key in pages.items()]}), encoding="utf-8")
    ran = subprocess.run([_node(), str(PRERENDER), str(tmp), str(job)], capture_output=True, text=True, env={**os.environ, **env}, check=False)
    assert ran.returncode == 0, ran.stderr[-1500:]
    return {rel: (tmp / rel).read_bytes() for rel in pages}


def test_a_project_page_is_what_the_prerender_draws_from_its_record_in_any_locale_and_time_zone(built, tmp_path):
    site = built[0]
    pages = _sample_projects(site)
    exported = {rel: (site / rel).read_bytes() for rel in pages}
    utc = _prerender_projects(site, tmp_path / "utc", {"TZ": "UTC", "LANG": "C", "LC_ALL": "C"}, pages)
    far = _prerender_projects(
        site, tmp_path / "kiritimati", {"TZ": "Pacific/Kiritimati", "LANG": "de_DE.UTF-8", "LC_ALL": "de_DE.UTF-8"}, pages
    )
    assert all(len(page) > 20000 for page in exported.values())
    assert utc == exported
    assert far == exported

    # the comparison can fail: a record whose first answer sentence differs draws a different page
    rel, key = next(iter(pages.items()))
    changed = _prerender_projects(
        site, tmp_path / "changed", {"TZ": "UTC"}, {rel: key}, mutate=lambda text: text.replace("got a first human reply", "got a first reply", 1)
    )
    assert changed[rel] != exported[rel]


_BROWSER_VIEW = """
const [view, api] = await Promise.all([import('/views/project.js'), import('/api.js')]);
const [owner, name] = %(key)s.split('/');
const [detail, evidence, extras] = await Promise.all([
  api.fetchRepoDetail(owner, name), api.fetchEvidence(owner, name), api.fetchProjectExtras(owner, name),
]);
const { recordFile, ...rest } = extras.data;
return {
  html: view.renderProject({ detail: detail.data, evidence: evidence.data, ...rest }, { mode: 'page', recordFile }),
  zone: Intl.DateTimeFormat().resolvedOptions().timeZone, language: navigator.language,
};
"""


def test_a_browser_draws_each_project_view_byte_for_byte_as_its_page_holds_it_in_two_locales_and_time_zones(built, tmp_path):
    if ui_browser.find_chrome() is None:
        if os.environ.get("PORCHLAMP_ALLOW_MISSING_CHROME") == "1":
            pytest.skip("no Chrome or Chromium found (PORCHLAMP_ALLOW_MISSING_CHROME=1)")
        pytest.fail("no Chrome or Chromium on PATH; the re-draw check is a real gate (or set PORCHLAMP_ALLOW_MISSING_CHROME=1)")
    site = built[0]
    pages = _sample_projects(site)
    held = {rel: _PROJECT_VIEW.search(_read(site, rel)).group(1) for rel in pages}
    cases = [
        {"name": rel, "path": "/" + rel.removesuffix("index.html"), "width": 1280, "height": 800,
         "script": _BROWSER_VIEW % {"key": json.dumps(key)}}
        for rel, key in pages.items()
    ]
    for zone, language in (("UTC", "en-US"), ("Pacific/Kiritimati", "de-DE")):
        drawn = ui_browser.probe(
            site, cases, tmp_path / zone.replace("/", "-"), flags=(f"--lang={language}", f"--accept-lang={language}"), env={"TZ": zone}
        )
        for rel in pages:
            assert "zone" in drawn[rel], drawn[rel]
            assert drawn[rel]["zone"] == zone and drawn[rel]["language"] == language, drawn[rel]
            assert drawn[rel]["html"] == held[rel], rel

    # the comparison can fail: one changed number in the page's view is a different draw
    rel = next(iter(pages))
    planted = re.sub(r'(data-v="detail\.porchlamp">)([\d.]+)', lambda m: m.group(1) + m.group(2) + "1", held[rel], count=1)
    assert planted != held[rel] and planted != drawn[rel]["html"]


# --------------------------------------------------------------------------- #
# 7. The theme in a real browser
# --------------------------------------------------------------------------- #
_SET_DARK = "win.localStorage.setItem('porchlamp-theme', 'dark'); return win.localStorage.getItem('porchlamp-theme');"
_STATIC_PAGE = """
const root = doc.documentElement;
const first = { theme: root.dataset.theme, background: win.getComputedStyle(doc.body).backgroundColor };
const toggle = await until(() => doc.querySelector('[data-theme-toggle]'), 8000);
if (!toggle) return { ...first, missing: 'the theme toggle' };
toggle.click();
await sleep(300);
return { ...first, after: root.dataset.theme, stored: win.localStorage.getItem('porchlamp-theme') };
"""
_APP_PAGE = """
const root = doc.documentElement;
const first = { theme: root.dataset.theme };
await until(() => doc.querySelector('#app-main') && doc.querySelector('#app-main').children.length > 0, 8000);
const toggle = await until(() => doc.querySelector('[data-theme-toggle]'), 8000);
if (!toggle) return { ...first, missing: 'the theme toggle' };
toggle.click();
await sleep(500);
return { ...first, after: root.dataset.theme, stored: win.localStorage.getItem('porchlamp-theme') };
"""


def test_a_stored_theme_shows_on_a_static_and_an_app_page_and_the_toggle_switches_it(built, tmp_path):
    if ui_browser.find_chrome() is None:
        if os.environ.get("PORCHLAMP_ALLOW_MISSING_CHROME") == "1":
            pytest.skip("no Chrome or Chromium found (PORCHLAMP_ALLOW_MISSING_CHROME=1)")
        pytest.fail("no Chrome or Chromium on PATH; the theme check is a real gate (or set PORCHLAMP_ALLOW_MISSING_CHROME=1)")
    view = {"width": 1280, "height": 800}
    cases = [
        {"name": "store", "path": "/black-holes/", "script": _SET_DARK, **view},
        {"name": "static", "path": "/black-holes/", "script": _STATIC_PAGE, **view},
        {"name": "app", "path": "/methodology/", "script": _APP_PAGE, **view},
    ]
    result = ui_browser.probe(built[0], cases, tmp_path)
    assert result["store"] == "dark"
    # a static page: the stored choice is on <html> and painted, then the toggle flips it and stores the flip
    assert result["static"] == {"theme": "dark", "background": "rgb(9, 13, 22)", "after": "light", "stored": "light"}, result["static"]
    # an app page: the flip made on the other page is kept, and the toggle flips it back
    assert result["app"] == {"theme": "light", "after": "dark", "stored": "dark"}, result["app"]


_STORE_THEME = "win.localStorage.setItem('porchlamp-theme', '%s'); return win.localStorage.getItem('porchlamp-theme');"
_FORGET_THEME = "win.localStorage.removeItem('porchlamp-theme'); return win.localStorage.getItem('porchlamp-theme');"
_TOGGLE_ICONS = """
const toggle = await until(() => doc.querySelector('[data-theme-toggle]'), 8000);
if (!toggle) return { missing: 'the theme toggle' };
const read = () => [...toggle.querySelectorAll('svg')].filter((icon) => win.getComputedStyle(icon).display !== 'none').map((icon) => ({
  icon: icon.getAttribute('class').split(' ').pop(),
  drawing: icon.firstElementChild.tagName,
  width: Math.round(icon.getBoundingClientRect().width),
  followsText: win.getComputedStyle(icon).stroke === win.getComputedStyle(toggle).color,
  hidden: icon.getAttribute('aria-hidden'),
}));
const first = read();
const box = toggle.getBoundingClientRect();
const hit = doc.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2) === toggle;
toggle.click();
await sleep(200);
return { first, after: read(), hit, name: toggle.getAttribute('aria-label') };
"""
_MOON = {"icon": "theme-icon-dark", "drawing": "path", "width": 15, "followsText": True, "hidden": "true"}
_SUN = {"icon": "theme-icon-light", "drawing": "circle", "width": 15, "followsText": True, "hidden": "true"}


@pytest.mark.parametrize("flags, unset", [((), _SUN), (("--force-dark-mode",), _MOON)], ids=["system-light", "system-dark"])
def test_the_toggle_shows_one_icon_the_theme_in_force_on_a_static_and_an_app_page(built, tmp_path, flags, unset):
    if ui_browser.find_chrome() is None:
        if os.environ.get("PORCHLAMP_ALLOW_MISSING_CHROME") == "1":
            pytest.skip("no Chrome or Chromium found (PORCHLAMP_ALLOW_MISSING_CHROME=1)")
        pytest.fail("no Chrome or Chromium on PATH; the theme check is a real gate (or set PORCHLAMP_ALLOW_MISSING_CHROME=1)")
    view = {"width": 1280, "height": 800}
    cases = []
    for path in ("/black-holes/", "/methodology/"):
        for choice, store in (("none", _FORGET_THEME), ("dark", _STORE_THEME % "dark"), ("light", _STORE_THEME % "light")):
            cases.append({"name": f"store {path} {choice}", "path": path, "script": store, **view})
            cases.append({"name": f"{path} {choice}", "path": path, "script": _TOGGLE_ICONS, **view})
    result = ui_browser.probe(built[0], cases, tmp_path, flags=flags)
    for path in ("/black-holes/", "/methodology/"):
        for choice, first in (("none", unset), ("dark", _MOON), ("light", _SUN)):
            got = result[f"{path} {choice}"]
            after = _SUN if first is _MOON else _MOON
            assert got == {"first": [first], "after": [after], "hit": True, "name": "Toggle light/dark theme"}, (flags, path, choice, got)


# --------------------------------------------------------------------------- #
# 8. Addresses shared by copy.js and the Python pages
# --------------------------------------------------------------------------- #
def test_the_lens_addresses_and_the_data_link_agree_with_the_python_pages():
    assert report_pages.copy_js_map("LENS_PAGE_HREFS") == {lens: f"/{slug}/" for lens, slug in report_pages.LENS_SLUGS.items()}
    assert report_pages.copy_js_string("FOOTER_DATA_HREF") == report_pages.GITHUB_REPO


# --------------------------------------------------------------------------- #
# 9. The verifier's chrome and methodology checks
# --------------------------------------------------------------------------- #
def test_the_verifier_passes_the_export_and_names_a_wrong_database_digest_and_a_dropped_nav(built, report_record, tmp_path):
    site, db = built[0], built[1]
    quiet = lambda _line: None  # noqa: E731
    methodology = verify_static_export.verify_methodology(db, site, report=report_record, log=quiet)
    chrome = verify_static_export.verify_chrome(db, site, log=quiet)
    assert methodology["errors"] == [], methodology["errors"][:5]
    assert chrome["errors"] == [], chrome["errors"][:5]
    assert methodology["hooks"] > 100
    assert chrome["pages"] == len(_pages(site))

    planted = tmp_path / "site"
    shutil.copytree(site, planted)
    # an export that printed a wrong digest, with a manifest that agrees with its page: the database file decides
    digest, wrong = hashlib.sha256(db.read_bytes()).hexdigest(), "0" * 64
    _rewrite(planted, f"data/{_snapshot_id(site)}/manifest.json", digest, wrong)
    _rewrite(planted, "methodology/index.html", digest, wrong)
    problems = verify_static_export.verify_methodology(db, planted, report=report_record, log=quiet)["errors"]
    assert any("'snapshot.source_db_sha256' shows ['" + wrong in problem for problem in problems), problems
    # a page without its nav
    nav = _FRAME["nav"].search(_read(planted, "black-holes/index.html"))
    _rewrite(planted, "black-holes/index.html", nav.group(0), nav.group(0).replace(nav.group(1), ""))
    problems = verify_static_export.verify_chrome(db, planted, log=quiet)["errors"]
    assert [problem.split(":")[0] for problem in problems] == ["black-holes/index.html nav is chrome.js's"], problems
