"""The page in a real browser: nothing wider than a phone, a header that leaves room, a pager at both ends.

MEASURED defects (2026-10-12, live site, Chrome at 375 px):

* Configure weights laid out 966 px wide and Not ranked 415 px (the browser then zooms out and the page is unreadable);
* the repository panel was 479 px wide: its title was cut off at the left edge and the radar chart half off-screen;
* the header was ``position: sticky`` and wrapped to 330 px, so 40% of every screen was controls;
* the Methodology tab sat off the right edge of the tab bar, with no hint it was there;
* with 1,000 rows a page the only pager was above the table: a reader at the bottom scrolled the whole page back up.

Chrome renders the real stylesheet and scripts (``tests/ui_browser.py``); a case that cannot find its element fails.
An absent Chrome fails this module unless ``PORCHLAMP_ALLOW_MISSING_CHROME=1``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import ui_browser  # noqa: E402
from serve.helpers import SNAPSHOT_ID, build_snapshot, finalize, fresh_db  # noqa: E402
from test_static_export import export_static  # noqa: E402

PHONE = {"width": 375, "height": 812}
DESKTOP = {"width": 1280, "height": 800}

_READY = "await until(() => doc.querySelector('#app-main') && doc.querySelector('#app-main').children.length > 0, 15000);"

_PAGE_WIDTH = (
    _READY
    + """
await sleep(%d);
%s
return { innerWidth: win.innerWidth, scrollWidth: doc.documentElement.scrollWidth };
"""
)

#: The fixture's reasons are short; the real list has long names and this sentence (porchlamp.serve's gate text).
_LONG_NOT_RANKED_ROW = """
const row = await until(() => doc.querySelector('.notranked-row'), 15000);
if (!row) return { innerWidth: 0, scrollWidth: 1, missing: 'a not-ranked row' };
row.querySelector('.repo-name').textContent = 'interview_internal_reference_repository';  // no break points, like many real names
row.querySelector('.reason-chip').textContent = 'Not a software repository (docs, data, or list only).';
await sleep(300);
"""

_DRAWER = (
    """
const drawer = await until(() => doc.querySelector('.drawer h1') && doc.querySelector('.drawer'), 20000);
if (!drawer) return { missing: 'drawer' };
await sleep(1500);
// the longest name GitHub allows is 39 + 1 + 100 characters: a header must cope with a long one
doc.querySelector('.drawer h1').textContent = 'a-long-organisation-name/an-even-longer-repository-name-here';
await sleep(200);
const el = doc.querySelector('.drawer');
return { scrollWidth: el.scrollWidth, clientWidth: el.clientWidth, scrollLeft: el.scrollLeft };
"""
)

_HEADER = (
    _READY
    + """
const header = doc.getElementById('app-header-controls');
const nav = doc.getElementById('app-primary-nav');
return {
  headerPosition: win.getComputedStyle(header).position,
  headerHeight: header.getBoundingClientRect().height,
  navPosition: win.getComputedStyle(nav).position,
  viewportHeight: win.innerHeight,
};
"""
)

_ACTIVE_TAB = (
    _READY
    + """
await sleep(500);
const strip = doc.querySelector('.primary-tabs').getBoundingClientRect();
const tab = doc.querySelector('.primary-tab[aria-selected="true"]').getBoundingClientRect();
return { tabLeft: tab.left, tabRight: tab.right, stripLeft: strip.left, stripRight: strip.right,
         label: doc.querySelector('.primary-tab[aria-selected="true"]').textContent.trim() };
"""
)

#: Form controls with no accessible name (no <label>, aria-label or aria-labelledby), over the controls each tab shows.
_UNNAMED_CONTROLS = (
    _READY
    + """
await sleep(%d);
const unnamed = [...doc.querySelectorAll('select, input, textarea')].filter((el) => {
  if (el.type === 'hidden' || el.hidden || el.closest('[hidden]')) return false;
  return !(el.labels && el.labels.length) && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby');
}).map((el) => el.tagName.toLowerCase() + '[' + (el.type || '') + ']' + (el.className ? '.' + String(el.className).split(' ')[0] : ''));
return { unnamed, total: doc.querySelectorAll('select, input, textarea').length };
"""
)

_COMPARE_ENTER = (
    _READY
    + """
const input = await until(() => doc.querySelector('[data-compare-search]'), 15000);
if (!input) return { missing: 'the compare search box' };
input.focus();
input.value = %r;
input.dispatchEvent(new win.Event('input', { bubbles: true }));
const found = await until(() => doc.querySelector('[data-compare-add]'), 15000);
if (!found) return { missing: 'a search result' };
input.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
await sleep(1500);
return { chips: doc.querySelectorAll('[data-compare-remove]').length, focusKept: doc.activeElement && doc.activeElement.matches('[data-compare-search]') };
"""
)

_PAGER_REVEAL = (
    """
const rows = await until(() => doc.querySelectorAll('.leaderboard-table tbody tr').length > 5, 20000);
if (!rows) return { missing: 'rows' };
const bottom = () => doc.querySelector('.pager-bottom [data-pager-next]');
if (!bottom() || bottom().disabled) return { missing: 'a bottom next button' };
win.scrollTo(0, doc.documentElement.scrollHeight);
await sleep(600);
const before = win.scrollY;
const top = doc.querySelector('.pager:not(.pager-bottom)').getBoundingClientRect().top + win.scrollY;
bottom().click();
await sleep(1500);
return { before, after: win.scrollY, pagerTop: top, status: doc.querySelector('[data-pager-status]').textContent };
"""
)


@pytest.fixture(scope="module")
def measured(tmp_path_factory):
    if ui_browser.find_chrome() is None:
        if os.environ.get("PORCHLAMP_ALLOW_MISSING_CHROME") == "1":
            pytest.skip("no Chrome or Chromium found (PORCHLAMP_ALLOW_MISSING_CHROME=1)")
        pytest.fail("no Chrome or Chromium on PATH; the layout checks are a real gate (or set PORCHLAMP_ALLOW_MISSING_CHROME=1)")
    tmp = tmp_path_factory.mktemp("layout")
    db = tmp / "db.sqlite"
    conn = fresh_db(db)
    build_snapshot(conn, db)
    finalize(conn, db)
    conn.close()
    site = tmp / "site"
    export_static.export(db, site)
    board = json.loads((site / "data" / SNAPSHOT_ID / "leaderboard-balanced.json").read_text())
    first = board["rows"][0]
    repo = f"{first['owner']}%2F{first['name']}"

    cases = [
        {"name": f"width-{tab}", "path": path, **PHONE, "script": _PAGE_WIDTH % (settle, inject)}
        for tab, path, settle, inject in (
            ("ranked", "./", 1500, ""),
            ("notranked", "./?tab=notranked", 2500, _LONG_NOT_RANKED_ROW),
            ("matrix", "./?tab=matrix", 2500, ""),
            ("compare", "./?tab=compare", 1500, ""),
            ("configure", "./?tab=configure", 3500, ""),
            ("methodology", "./?tab=methodology", 2500, ""),
        )
    ]
    cases += [
        {"name": "drawer", "path": f"./?repo={repo}", **PHONE, "script": _DRAWER},
        {"name": "header", "path": "./", **PHONE, "script": _HEADER},
        {"name": "active-tab", "path": "./?tab=methodology", **PHONE, "script": _ACTIVE_TAB},
        {"name": "pager", "path": "./?page_size=20", **DESKTOP, "script": _PAGER_REVEAL},
        {"name": "compare-enter", "path": "./?tab=compare", **DESKTOP, "script": _COMPARE_ENTER % first["name"]},
    ]
    cases += [
        {"name": f"names-{tab}", "path": path, **DESKTOP, "script": _UNNAMED_CONTROLS % settle}
        for tab, path, settle in (
            ("ranked", "./", 1500),
            ("notranked", "./?tab=notranked", 2500),
            ("matrix", "./?tab=matrix", 2500),
            ("compare", "./?tab=compare", 1500),
            ("configure", "./?tab=configure", 3500),
        )
    ]
    return ui_browser.probe(site, cases, tmp)


@pytest.mark.parametrize("tab", ["ranked", "notranked", "matrix", "compare", "configure", "methodology"])
def test_no_screen_is_wider_than_the_phone(measured, tab):
    got = measured[f"width-{tab}"]
    assert got["innerWidth"] == 375, got  # the browser did not zoom out to fit a wide page
    assert got["scrollWidth"] <= got["innerWidth"], got


def test_the_repository_panel_fits_the_phone_with_a_long_name(measured):
    got = measured["drawer"]
    assert "missing" not in got, got
    assert got["scrollWidth"] <= got["clientWidth"], got
    assert got["scrollLeft"] == 0, got


def test_the_phone_header_does_not_stay_on_screen(measured):
    got = measured["header"]
    assert got["headerPosition"] != "sticky" or got["headerHeight"] <= 0.15 * got["viewportHeight"], got
    assert got["navPosition"] == "sticky", got  # the tab bar is what stays


def test_a_tab_opened_by_link_is_visible_in_the_tab_bar(measured):
    got = measured["active-tab"]
    assert got["label"] == "Methodology", got
    assert got["tabLeft"] >= got["stripLeft"] - 1 and got["tabRight"] <= got["stripRight"] + 1, got


def test_going_to_the_next_page_from_the_bottom_returns_to_the_top_of_the_list(measured):
    got = measured["pager"]
    assert "missing" not in got, got
    assert got["before"] > 300, got  # the case really started at the bottom of a long page
    assert got["after"] <= got["pagerTop"], got
    assert got["status"].startswith("Page 2"), got


@pytest.mark.parametrize("tab", ["ranked", "notranked", "matrix", "compare", "configure"])
def test_every_form_control_has_a_name(measured, tab):
    got = measured[f"names-{tab}"]
    assert got["total"] > 0, got  # the case really found the controls
    assert got["unnamed"] == [], got


def test_enter_in_the_compare_search_adds_the_first_match(measured):
    got = measured["compare-enter"]
    assert "missing" not in got, got
    assert got["chips"] == 1, got
    assert got["focusKept"] is True, got  # the next repository can be typed straight away
