"""The static site (``scripts/export_static.py`` + ``web/static/api.js``)
answers every request the way the API does -- checked request by request by
``scripts/verify_static_export.py`` on ``tests/serve``'s synthetic snapshot
-- and that check catches a broken adapter (its mutants).

An absent Node.js interpreter fails this module, as it fails
``tests/test_ui_node_suite.py`` (H4), unless ``PORCHLAMP_ALLOW_MISSING_NODE=1``.
"""
from __future__ import annotations

import importlib.util
import json
import re
import os
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))  # serve.helpers -> store.helpers

from serve.helpers import ROUTED_PAIR_OWNER, SNAPSHOT_ID, build_snapshot, finalize, fresh_db  # noqa: E402


def _load_script(name: str):
    # By path, never via sys.path: scripts/serve.py would shadow tests/serve.
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


export_static = _load_script("export_static")
verify_static_export = _load_script("verify_static_export")

#: Mutants this ~300-row fixture catches whatever its incidental values. The
#: rest need production-scale data (truncation needs > 1,000 not-ranked
#: rows); the verifier's own run over a real snapshot requires all of them.
#: The ecosystem filter is only visible because :func:`site` moves a third
#: of the rows out of the helpers' single ecosystem.
_FIXTURE_CATCHES = (
    "sort order ignored",
    "ecosystem filter ignored",
    "repo looked up in the wrong bucket",
    "custom tie groups keep singletons",
    # The fixture's 1.0000000000000002 rounds to 1 at RATIO_DECIMALS, so the clamp's mutant has nothing to show here;
    # the rounding's mutant shows the 0.9000000000000001 the API rounds away.
    "custom coverage not rounded as the API rounds it",
)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    if verify_static_export.find_node() is None:
        if os.environ.get("PORCHLAMP_ALLOW_MISSING_NODE") == "1":
            pytest.skip("no Node.js interpreter found (PORCHLAMP_ALLOW_MISSING_NODE=1)")
        pytest.fail("no Node.js interpreter found on PATH, in PORCHLAMP_NODE_BIN, or in ~/.local/opt/node-*")
    tmp = tmp_path_factory.mktemp("static")
    db = tmp / "db.sqlite"
    conn = fresh_db(db)
    # The pair is a repository routed off-platform that clears the coverage floor
    # (so the static reprojection could rank it) and its unrouted twin.
    build_snapshot(conn, db, with_routed_pair=True, with_full_coverage_repo=True)
    # The helpers write every row as "python"; with one ecosystem the filter is a no-op.
    conn.execute("UPDATE repositories SET ecosystem = 'rust' WHERE snapshot_id = ? AND rowid % 3 = 0", (SNAPSHOT_ID,))
    conn.commit()
    finalize(conn, db)
    conn.close()
    out = tmp / "site"
    export_static.export(db, out)
    return db, out


def test_every_static_answer_is_the_apis(site):
    db, out = site
    result = verify_static_export.verify(db, out, log=lambda _line: None)
    assert result["requests"] > 1000
    assert result["mismatches"] == {}
    missing = [name for name, n in result["mutants"].items() if n is None]
    assert missing == [], f"mutant targets absent from api.js: {missing}"
    survived = [name for name in _FIXTURE_CATCHES if not result["mutants"][name]]
    assert survived == [], f"the parity check cannot see: {survived}"


def test_static_reproject_export_holds_no_routed_repo(site):
    """The browser's custom-weights reprojection ranks every row of
    ``reproject.json``, so a repository routed off-platform must not be in
    it. The snapshot holds one that clears the coverage floor (and the
    comparison with the API above runs on it) and its unrouted twin."""
    db, out = site
    reproject = json.loads((out / "data" / SNAPSHOT_ID / "reproject.json").read_text(encoding="utf-8"))
    exported = {(row["owner"], row["name"]) for row in reproject["rows"]}

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    routed = conn.execute(
        "SELECT owner, name, c_responsiveness, c_criticality, c_governance, c_rigor, c_accessibility "
        "FROM repositories WHERE snapshot_id = ? AND eligible = 1 AND routed = 'off_platform'", (SNAPSHOT_ID,),
    ).fetchall()
    twins = conn.execute(
        "SELECT owner, name FROM repositories WHERE snapshot_id = ? AND owner = ? AND routed IS NULL",
        (SNAPSHOT_ID, ROUTED_PAIR_OWNER),
    ).fetchall()
    conn.close()

    balanced = (0.25, 0.25, 0.20, 0.15, 0.15)
    clearing = [(o, n) for o, n, *coverages in routed if sum(w * c for w, c in zip(balanced, coverages)) >= 0.60]
    assert clearing, "the snapshot must hold a routed repository that clears the floor, or this test proves nothing"
    assert {(o, n) for o, n, *_ in routed}.isdisjoint(exported)
    assert len(twins) == 1 and tuple(twins[0]) in exported


def test_a_mutant_is_not_credited_with_what_the_site_already_gets_wrong(site, tmp_path, monkeypatch):
    # A site that answers the grid's unknown repo wrong (its bucket file gone),
    # and a mutant that changes nothing: that mutant must be credited with nothing.
    _db, out = site
    broken = tmp_path / "site"
    shutil.copytree(out, broken)
    bucket = export_static.bucket_of("no-such-owner", "no-such-repo")
    (broken / "data" / SNAPSHOT_ID / "repos" / f"{bucket}.json").unlink()
    first_line = (out / "api.js").read_text(encoding="utf-8").splitlines()[0]
    monkeypatch.setattr(verify_static_export, "MUTANTS", (("inert", first_line, "// inert"),))
    result = verify_static_export.verify(site[0], broken, quick=True, log=lambda _line: None)
    assert result["mismatches"]  # the broken bucket is seen...
    assert result["mutants"] == {"inert": 0}  # ...and not credited to the mutant
    assert not verify_static_export.passed(result)


#: What the export's prerender draws into a page, each as (the frame element it fills, the same element empty).
_DRAWN = (
    (r'<header class="app-header" id="app-header-controls">.*?</header>', '<header class="app-header" id="app-header-controls"></header>'),
    (r'<nav class="app-primary-nav" id="app-primary-nav" aria-label="Primary">.*?</nav>', '<nav class="app-primary-nav" id="app-primary-nav"></nav>'),
    (r'<footer class="app-footer" id="app-footer">.*?</footer>', '<footer class="app-footer" id="app-footer"></footer>'),
)
_MAIN = r'<main class="app-main" id="app-main" tabindex="-1"[^>]*>.*?</main>'


def _undraw(page: str, main: str | None = None) -> str:
    """The page as the app's frame had it before the prerender: the header, the nav (and its aria-label) and the footer
    put back to the empty frame elements and, given the frame's ``<main>``, the view drawn into main put back to it.
    Each drawn element must be on the page exactly once and hold something, so a page the prerender skipped fails here
    instead of passing as an already-empty frame."""
    for drawn, empty in _DRAWN:
        found = re.findall(drawn, page, re.S)
        assert len(found) == 1, f"{drawn[:40]} is on the page {len(found)} times"
        assert found[0] != empty, f"{empty} was not drawn"
        page = re.sub(drawn, lambda _match, empty=empty: empty, page, flags=re.S)
    if main is not None:
        assert len(re.findall(_MAIN, page, re.S)) == 1
        page = re.sub(_MAIN, lambda _match: main, page, flags=re.S)
    return page


def test_the_site_is_web_app_with_only_api_js_replaced(site):
    _db, out = site
    assert (out / "api.js").read_bytes() == export_static.STATIC_API.read_bytes()
    drawn_pages = {Path("index.html"), Path("404.html")}  # the prerender draws the chrome into these two, and the ranked view into /
    for entry in export_static.WEB_APP.rglob("*"):
        relative = entry.relative_to(export_static.WEB_APP)
        if entry.is_dir() or relative.parts[0] in export_static._WEB_SKIP or relative == Path("api.js"):
            continue
        if relative in drawn_pages:
            frame = entry.read_text(encoding="utf-8")
            main = re.search(_MAIN, frame, re.S).group(0) if relative == Path("index.html") else None
            assert _undraw((out / relative).read_text(encoding="utf-8"), main=main) == frame, relative
        else:
            assert (out / relative).read_bytes() == entry.read_bytes(), relative
    assert drawn_pages <= {e.relative_to(export_static.WEB_APP) for e in export_static.WEB_APP.iterdir()}
    assert not (out / "tests").exists() and not (out / "package.json").exists()
    # what draws the pages at export is not part of the site: the prerender, and the page list it was given (ENG-15)
    assert not list(out.rglob("*.mjs")) and not list(out.rglob("pages.json"))
    assert (out / ".nojekyll").exists()  # GitHub Pages must not run Jekyll over the data
    assert f'"{SNAPSHOT_ID}"' in (out / "data-version.js").read_text()
    assert (out / "data" / SNAPSHOT_ID / "snapshot.json").is_file()


def test_the_export_writes_the_app_s_page_at_every_view_s_address_and_404_html(site):
    """A static host serves a view only where a file is: each view the app draws in place (web/app/routes.js
    ``shell``) gets the app's page at its address, with that view's title and canonical address, and every other
    missing address is answered by 404.html. Every address those pages fetch starts at the site root, so the same
    page works at any depth."""
    _db, out = site
    shell = (export_static.WEB_APP / "404.html").read_text(encoding="utf-8")
    # the list views (a route with ``fixed``: the leaderboard at a preset) are tests/test_list_views.py's
    views = [route for route in export_static.app_routes()
             if route["shell"] and not route.get("fixed") and route["id"] not in export_static.VIEWS_WRITTEN_ELSEWHERE]
    assert [route["path"] for route in views] == ["/", "/not-ranked/", "/matrix/", "/compare/", "/weights/", "/methodology/"]
    # the report's page has a head of its own and needs the stats file (tests/test_report_pages.py): without it, the
    # report's address is answered by 404.html, where the app says the report did not load
    assert [r["id"] for r in export_static.app_routes()
            if r["shell"] and not r.get("fixed") and r not in views[1:] and r["path"] != "/"] == ["report"]
    assert not (out / "black-holes").exists()
    uncommented = re.sub(r"\n *<!--.*?-->", "", shell, flags=re.S)
    frame_main = re.search(_MAIN, shell, re.S).group(0)
    home, home_frame = (out / "index.html").read_text(encoding="utf-8"), (export_static.WEB_APP / "index.html").read_text(encoding="utf-8")
    assert 'data-prerendered="ranked"' in home  # the ranked view's first page, drawn into <main>
    assert _undraw(home, main=re.search(_MAIN, home_frame, re.S).group(0)) == home_frame
    assert _undraw((out / "404.html").read_text(encoding="utf-8")) == shell
    for route in views[1:]:
        page = (out / route["path"].strip("/") / "index.html").read_text(encoding="utf-8")
        # the view's own page, with the chrome drawn; the whole not-ranked list and the methodology view also have their
        # view drawn into <main>
        prerendered = f'data-prerendered="{route["id"]}"' in page
        assert prerendered == (route["id"] in ("notranked", "methodology")), route["id"]
        undrawn = _undraw(page, main=frame_main if prerendered else None)
        assert undrawn == export_static.view_page(route), route["id"]
        page = undrawn
        assert re.findall(r"<title>([^<]*)</title>", page) == [route["title"]], route["id"]
        assert re.findall(r'<link rel="canonical" href="([^"]*)">', page) == [f"https://porchlamp.org{route['path']}"], route["id"]
        # apart from the title and the canonical address, the page is 404.html (its comments left out): the app, with
        # no page text of its own
        stripped = re.sub(r'\n  <link rel="canonical" href="[^"]*">', "", re.sub(r"<title>[^<]*</title>", "", page))
        assert stripped == re.sub(r"<title>[^<]*</title>", "", uncommented), route["id"]
    for page in [out / "index.html", out / "404.html", *(out / r["path"].strip("/") / "index.html" for r in views[1:])]:
        urls = re.findall(r'<(?:link rel="(?:icon|stylesheet)"[^>]*href|script[^>]*src|img[^>]*src)="([^"]*)"', page.read_text(encoding="utf-8"))
        assert len(urls) >= 6, (page, urls)
        assert all(url.startswith("/") and not url.startswith("//") for url in urls), (page, urls)


def test_answers_past_the_string_limit_are_streamed_not_stringified(tmp_path):
    """The 11,000-repo snapshot of 2026-10 has about 1 GB of answers, more than V8
    can hold in one string: one JSON.stringify of them all threw "RangeError:
    Invalid string length" and the verifier could not run. Three 181 MB answers
    (543 MB in all, over the 536,870,888-character limit) must come back whole,
    in order, with an error answer between them kept as an error."""
    node = verify_static_export.find_node()
    if node is None:
        if os.environ.get("PORCHLAMP_ALLOW_MISSING_NODE") == "1":
            pytest.skip("no Node.js interpreter found (PORCHLAMP_ALLOW_MISSING_NODE=1)")
        pytest.fail("no Node.js interpreter found on PATH, in PORCHLAMP_NODE_BIN, or in ~/.local/opt/node-*")
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "api.js").write_text(
        "export async function big(n, mark) { return { ok: true, data: mark + 'x'.repeat(n) }; }\n"
        "export async function bad() { return { ok: false, error: { status: 404, detail: 'nope' } }; }\n",
        encoding="utf-8")
    size = 181_000_000
    requests = [{"fn": "big", "args": [size, "a"]}, {"fn": "bad", "args": []},
                {"fn": "big", "args": [size, "b"]}, {"fn": "big", "args": [size, "c"]}]
    results = verify_static_export.run_static(node, site_dir, requests)
    assert len(results) == 4
    assert [r["data"][0] for r in (results[0], results[2], results[3])] == ["a", "b", "c"]
    assert all(len(r["data"]) == size + 1 for r in (results[0], results[2], results[3]))
    assert results[1] == {"ok": False, "status": 404, "detail": "nope"}


def test_bucket_of_spreads_repos_over_every_file():
    buckets = {export_static.bucket_of(f"owner{i}", f"repo{i}") for i in range(5000)}
    assert buckets == {f"{b:02x}" for b in range(export_static.BUCKETS)}


@pytest.mark.parametrize(
    "text, what",
    [
        ('{"x": "someone@example.com"}', "an email address"),
        ('{"x": "ghp_' + "a1" * 18 + '"}', "a GitHub token"),
        ('{"x": "/home/someone/Projects/data"}', "a local home path"),
        ('{"x": "/Users/someone/Projects/data"}', "a local home path"),
    ],
)
def test_export_refuses_to_publish(tmp_path, text, what):
    (tmp_path / "data.json").write_text(text, encoding="utf-8")
    with pytest.raises(SystemExit, match=what):
        export_static._scan_for_forbidden(tmp_path)


def test_export_scan_passes_what_the_site_legitimately_carries(tmp_path):
    (tmp_path / "data.json").write_text(
        '{"source": "raw/commits_365d/acme__widget.json", "rule": "users.noreply.github.com maps to a login"}',
        encoding="utf-8",
    )
    export_static._scan_for_forbidden(tmp_path)  # no SystemExit


def test_the_page_sitemap_lists_only_the_pages_the_export_wrote_dated_with_the_snapshot(tmp_path):
    """The root, the report, its frozen copy, the methodology and the list views it is given, in that order; a page the
    export did not write (here the methodology and one of the two list views) is not listed, nor the /check/ stub."""
    written = ["", "black-holes/", "black-holes/2026-10-17/", "check/", "rankings/2/"]
    listed = [rel for rel in written if rel != "check/"]
    for rel in written:
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
        (tmp_path / rel / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (tmp_path / "methodology").mkdir()  # a folder without a page is not a page
    xml = export_static._sitemap_pages(tmp_path, "2026-10-17", "2026-11-10", ["/rankings/2/", "/rust/"])
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
    assert re.findall(r"<loc>([^<]*)</loc>", xml) == [f"https://porchlamp.org/{rel}" for rel in listed]
    assert re.findall(r"<lastmod>([^<]*)</lastmod>", xml) == ["2026-11-10"] * len(listed)


def _node() -> str:
    node = verify_static_export.find_node()
    if node is None:
        if os.environ.get("PORCHLAMP_ALLOW_MISSING_NODE") == "1":
            pytest.skip("no Node.js interpreter found (PORCHLAMP_ALLOW_MISSING_NODE=1)")
        pytest.fail("no Node.js interpreter found on PATH, in PORCHLAMP_NODE_BIN, or in ~/.local/opt/node-*")
    return node


def test_a_mutant_run_hands_back_only_the_answers_it_changed(tmp_path):
    """The verifier runs every mutant over the whole grid (about 1 GB of answers on the 11,000-repo snapshot). A
    mutant run compares each answer's JSON with the site's own run and returns only those that differ, by index."""
    node = _node()
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "api.js").write_text(
        "export async function same(x) { return { ok: true, data: { x } }; }\n"
        "export async function moved(x) { return { ok: true, data: x * 2 }; }\n"
        "export async function bad() { return { ok: false, error: { status: 404, detail: 'nope' } }; }\n",
        encoding="utf-8")
    (site_dir / "api.mutant.js").write_text(
        "export async function same(x) { return { ok: true, data: { x } }; }\n"
        "export async function moved(x) { return { ok: true, data: x * 3 }; }\n"
        "export async function bad() { return { ok: false, error: { status: 500, detail: 'nope' } }; }\n",
        encoding="utf-8")
    requests = [{"fn": "same", "args": [1]}, {"fn": "moved", "args": [0]}, {"fn": "moved", "args": [2]},
                {"fn": "bad", "args": []}, {"fn": "same", "args": [2]}]
    digests = tmp_path / "digests.json"
    answers = verify_static_export.run_static(node, site_dir, requests, digests_out=digests)
    assert [a["ok"] for a in answers] == [True, True, True, False, True]
    assert len(json.loads(digests.read_text(encoding="utf-8"))) == len(requests)
    changed = verify_static_export.run_static_changed(node, site_dir, requests, "api.mutant.js", digests)
    assert changed == {2: {"ok": True, "data": 6}, 3: {"ok": False, "status": 500, "detail": "nope"}}
    assert verify_static_export.run_static_changed(node, site_dir, requests, "api.js", digests) == {}


def test_the_mutant_runs_start_together(site, monkeypatch):
    """Each mutant's run is independent of the others, so all of them run at once, one Node process each."""
    from concurrent.futures import ThreadPoolExecutor

    db, out = site
    sizes = []

    class RecordingPool(ThreadPoolExecutor):
        def __init__(self, max_workers: int) -> None:
            sizes.append(max_workers)
            super().__init__(max_workers=max_workers)

    monkeypatch.setattr(verify_static_export, "ThreadPoolExecutor", RecordingPool)
    monkeypatch.setattr(verify_static_export.os, "cpu_count", lambda: 64)
    monkeypatch.setattr(verify_static_export, "MUTANTS", verify_static_export.MUTANTS[:3])
    result = verify_static_export.verify(db, out, quick=True, log=lambda _line: None)
    assert sizes == [3]
    assert len(result["mutants"]) == 3 and None not in result["mutants"].values()


def test_mutant_counts_equal_a_comparison_of_every_answer(site, tmp_path, monkeypatch):
    """Comparing only the answers a mutant changed must credit it with exactly what comparing all of them
    does: the answers it alone gets wrong. The site already gets one repository wrong (its bucket file is gone),
    so some answers the mutants change were wrong before they changed them."""
    node = _node()
    db, site_dir = site
    out = tmp_path / "site"
    shutil.copytree(site_dir, out)
    grid = verify_static_export.build_grid(db, quick=True)
    owner, name = next(r["args"] for r, e in zip(grid.requests, grid.expected) if r["kind"] == "repo" and e["ok"])
    (out / "data" / SNAPSHOT_ID / "repos" / f"{export_static.bucket_of(owner, name)}.json").unlink()
    picked = tuple(m for m in verify_static_export.MUTANTS
                   if m[0] in {"search ignores case", "sort order ignored", "repo looked up in the wrong bucket"})
    assert len(picked) == 3
    monkeypatch.setattr(verify_static_export, "MUTANTS", picked)
    result = verify_static_export.verify(db, out, quick=True, log=lambda _line: None)
    assert result["mismatches"]  # the missing bucket is seen

    baseline = verify_static_export.mismatching(grid, verify_static_export.run_static(node, out, grid.requests))
    source_text = (out / "api.js").read_text(encoding="utf-8")
    full = {}
    for name, old, new in picked:
        (out / "api.full-mutant.js").write_text(source_text.replace(old, new), encoding="utf-8")
        try:
            bad = verify_static_export.mismatching(
                grid, verify_static_export.run_static(node, out, grid.requests, "api.full-mutant.js"))
        finally:
            (out / "api.full-mutant.js").unlink()
        full[name] = len(bad.keys() - baseline.keys())
    assert sum(full.values()) > 0  # some mutant is caught, so the equality below is not 0 == 0
    assert result["mutants"] == full


def test_the_grid_holds_the_same_answers_on_one_and_several_processes(site, monkeypatch):
    """The grid's requests whose answers it does not read are answered together on a process pool (``None``: one
    process per CPU); the requests and their answers are the same as on one process."""
    import porchlamp.serve.inprocess as inprocess

    db, _out = site
    serial = verify_static_export.build_grid(db, quick=True, workers=1)
    sizes = []
    real_pool = inprocess.ProcessPoolExecutor

    def recording_pool(max_workers: int, **options):
        sizes.append(max_workers)
        return real_pool(max_workers=max_workers, **options)

    monkeypatch.setattr(inprocess, "ProcessPoolExecutor", recording_pool)
    monkeypatch.setattr(inprocess.os, "cpu_count", lambda: 3)
    parallel = verify_static_export.build_grid(db, quick=True, workers=None)
    assert sizes == [3]
    assert parallel.requests == serial.requests
    assert None not in parallel.expected
    assert parallel.expected == serial.expected
    assert sum(r["kind"] in ("repo", "evidence", "leaderboard") for r in parallel.requests) > 300
    named = [(tuple(a.lower() for a in r["args"]), (e["data"]["owner"].lower(), e["data"]["name"].lower()))
             for r, e in zip(parallel.requests, parallel.expected) if r["kind"] == "repo" and e["ok"]]
    assert len(named) > 10 and all(asked == answered for asked, answered in named)  # each answer is its request's


def test_the_verify_command_answers_the_grid_on_every_cpu(tmp_path, monkeypatch):
    """The library's default is one process (a caller that patches the API keeps its patch); the command, the
    publish step, asks for one per CPU."""
    seen = {}

    def recording_verify(db, site, **options):
        seen.update(options)
        return {"requests": 0, "mismatches": {}, "mutants": {}}

    monkeypatch.setattr(verify_static_export, "verify", recording_verify)
    for page_check in ("verify_chrome", "verify_methodology", "verify_lists", "verify_not_ranked_lists"):  # every page of the site: not what this test is about
        monkeypatch.setattr(verify_static_export, page_check, lambda db, site, **_options: {"errors": []})
    (tmp_path / "site").mkdir()
    verify_static_export.main(["--db", str(tmp_path / "db.sqlite"), "--site", str(tmp_path / "site")])
    assert "workers" in seen and seen["workers"] is None


def test_the_app_page_without_javascript_points_at_the_current_report():
    """The app's page template, used for every view the app draws, tells a reader without JavaScript where the
    rankings, the report and the methodology are: the report is the current one, not the older black-holes report."""
    shell = export_static.APP_SHELL.read_text(encoding="utf-8")
    fallback = re.search(r"<noscript>(.*?)</noscript>", shell, re.S)
    assert fallback, "the template has its no-script line"
    assert 'href="/first-pull-requests/">report</a>' in fallback.group(1)
    assert "/black-holes/" not in fallback.group(1)
