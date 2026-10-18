"""The list views at their own addresses (``web/app/routes.js``'s presets), as the export writes them and
``scripts/verify_static_export.py`` proves them: a page at each address the route table gives and none at any other
(``verify_lists``), and every ranked project's page within two plain-link clicks of ``/`` (``crawl_depths``,
``verify_crawl``).
"""
# ruff: noqa: F811  (the pytest fixtures are imported by name from the report tests and then used as arguments)
from __future__ import annotations

import shutil
from pathlib import Path

from tests.test_report_pages import built, export_static, report_record, verify_static_export  # noqa: F401  (fixtures)


def _page(site: Path, address: str, *links: str) -> None:
    path = site / address.strip("/") / "index.html" if address.strip("/") else site / "index.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("<!doctype html><body>" + "".join(f'<a class="x" href="{href}">x</a>' for href in links) + "</body>",
                    encoding="utf-8")


def test_the_crawl_counts_clicks_from_the_home_over_plain_links_to_pages(tmp_path):
    site = tmp_path / "site"
    _page(site, "/", "/rankings/1/", "/rankings/2/?page=2", "/data/2026-10-17/report.json", "https://github.com/a/b/",
          "/missing/")
    _page(site, "/rankings/1/", "/projects/a/b/", "../../projects/c/d/#evidence", "/rankings/2/")
    _page(site, "/rankings/2/", "/rankings/1/")
    _page(site, "/projects/a/b/", "/projects/e/f/")
    _page(site, "/projects/c/d/")
    _page(site, "/projects/e/f/")
    depths = verify_static_export.crawl_depths(site)
    # a query or a fragment is the same page; a data file, another site and an address without a page are not pages
    assert depths == {"/": 0, "/rankings/1/": 1, "/rankings/2/": 1, "/projects/a/b/": 2, "/projects/c/d/": 2, "/projects/e/f/": 3}


def test_a_project_three_clicks_away_is_named(tmp_path, monkeypatch):
    site = tmp_path / "site"
    _page(site, "/", "/rankings/1/")
    _page(site, "/rankings/1/", "/projects/a/b/")
    _page(site, "/projects/a/b/", "/projects/c/d/")
    _page(site, "/projects/c/d/")
    monkeypatch.setattr(verify_static_export, "_api_answers", lambda _db, *_paths: [{"total": 2}])
    result = verify_static_export.verify_crawl(Path("unused.db"), site, log=lambda _line: None)
    assert result["depths"] == {2: 1, 3: 1}
    assert any("within 2 clicks" in error and "found 1" in error for error in result["errors"]), result["errors"]
    _page(site, "/rankings/1/", "/projects/a/b/", "/projects/c/d/")  # the ranking page links it: two clicks
    assert verify_static_export.verify_crawl(Path("unused.db"), site, log=lambda _line: None)["errors"] == []


def test_each_route_address_has_its_list_page_and_no_list_page_lacks_its_route(built, report_record, tmp_path):
    site, db, *_ = built
    result = verify_static_export.verify_lists(db, site, report=report_record, log=lambda _line: None)
    assert result["errors"] == [], result["errors"][:5]
    assert result["pages"] >= len([r for r in verify_static_export._export_static().app_routes() if r.get("fixed")])
    copy = tmp_path / "site"
    shutil.copytree(site, copy)
    (copy / "go" / "first-timers" / "index.html").unlink()  # a route without its page
    last = max(int(path.parent.name) for path in (copy / "rankings").glob("*/index.html"))
    for stray in (f"rankings/{last + 1}", "rust/stray"):  # a page past the ranking's last, a page no route names
        (copy / stray).mkdir()
        shutil.copyfile(copy / "rust" / "index.html", copy / stray / "index.html")
    errors = verify_static_export.verify_lists(db, copy, report=report_record, log=lambda _line: None)["errors"]
    assert any(error.startswith("/go/first-timers/ is a page of the export") for error in errors), errors[:5]
    assert any("list pages at no address of the route table" in error and f"/rankings/{last + 1}/" in error and "/rust/stray/" in error
               for error in errors), errors[:5]


def test_a_paged_route_has_a_page_for_each_full_hundred_rows_and_one_for_the_rest():
    route = next(r for r in export_static.app_routes() if "{page}" in r["path"])
    assert route["fixed"]["page_size"] == 100

    def boards(rows: int) -> dict:
        return {"balanced": {"rows": [{"ecosystem": "go"}] * rows}}

    assert [len(export_static.route_instances(route, boards(n))) for n in (0, 1, 100, 101, 200, 201)] == [1, 1, 1, 2, 2, 3]
    assert export_static.route_instances(route, boards(101)) == ["/rankings/1/", "/rankings/2/"]
