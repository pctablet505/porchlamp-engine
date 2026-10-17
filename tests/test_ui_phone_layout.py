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
TABLET = {"width": 768, "height": 1024}
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
const drawer = await until(() => doc.querySelector('.drawer #drawer-title') && doc.querySelector('.drawer'), 20000);
if (!drawer) return { missing: 'drawer' };
await sleep(1500);
// the longest name GitHub allows is 39 + 1 + 100 characters: a header must cope with a long one
doc.querySelector('.drawer #drawer-title').textContent = 'a-long-organisation-name/an-even-longer-repository-name-here';
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
const tab = doc.querySelector('.primary-tab[aria-current="page"]').getBoundingClientRect();
return { tabLeft: tab.left, tabRight: tab.right, stripLeft: strip.left, stripRight: strip.right,
         label: doc.querySelector('.primary-tab[aria-current="page"]').textContent.trim() };
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

#: The header's search is the same on every page; on a view that lists no projects (the methodology) it opens the
#: rankings with the query, in place, as its plain form would on a page without the app.
_SEARCH_FROM_METHODOLOGY = (
    _READY
    + """
await until(() => doc.querySelector('.doc-page'), 15000);
const input = doc.querySelector('[data-search-input]');
const form = input && input.closest('form');
if (!form || form.hidden) return { missing: 'the header search on the methodology page' };
input.value = %r;
input.dispatchEvent(new win.Event('input', { bubbles: true }));
form.requestSubmit();
const rows = await until(() => win.location.pathname === '/' && doc.querySelectorAll('[data-owner]').length > 0 && doc.querySelectorAll('[data-owner]'), 15000);
if (!rows) return { missing: 'the rankings after the search', path: win.location.pathname, search: win.location.search };
return {
  path: win.location.pathname, search: win.location.search, inPlace: win.document === doc, query: input.value,
  names: [...rows].map((r) => `${r.dataset.owner}/${r.dataset.name}`), navCurrent: doc.querySelector('.primary-tab[aria-current="page"]').textContent,
};
"""
)

_HEADER_CHIP = (
    _READY
    + """
const chip = await until(() => { const c = doc.querySelector('[data-snapshot-chip]'); return c && c.textContent !== 'Loading\u2026' && c; }, 15000);
if (!chip) return { missing: 'the snapshot chip' };
return { text: chip.textContent, title: chip.getAttribute('title') };
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

_TABLET_RANKED = (
    _READY
    + """
await until(() => doc.querySelectorAll('.leaderboard-table thead th').length > 0, 15000);
await sleep(500);
const ths = [...doc.querySelectorAll('.leaderboard-table thead th')].filter((th) => win.getComputedStyle(th).display !== 'none');
const overlaps = [];
for (let i = 0; i < ths.length - 1; i++) {
  const a = ths[i].getBoundingClientRect();
  const b = ths[i + 1].getBoundingClientRect();
  if (a.right > b.left + 1) {
    overlaps.push({ a: ths[i].textContent.trim(), b: ths[i + 1].textContent.trim(), aRight: a.right, bLeft: b.left });
  }
}
return {
  innerWidth: win.innerWidth,
  scrollWidth: doc.documentElement.scrollWidth,
  visibleHeaderCount: ths.length,
  overlaps,
};
"""
)

_MOBILE_SORT = (
    _READY
    + """
const select = await until(() => doc.querySelector('[data-mobile-sort-select]'), 15000);
const dir = doc.querySelector('[data-mobile-sort-dir]');
return {
  hasSelect: Boolean(select && select.getBoundingClientRect().width > 0),
  hasDir: Boolean(dir && dir.getBoundingClientRect().width > 0),
};
"""
)

_DESKTOP_STICKY_NAV = (
    _READY
    + """
await sleep(300);
const nav = doc.getElementById('app-primary-nav');
const cs = win.getComputedStyle(nav);
return {
  position: cs.position,
  topPx: parseFloat(cs.top) || 0,
};
"""
)

_MATRIX_CORNERS = (
    _READY
    + """
await until(() => doc.querySelectorAll('.matrix-corner-label').length === 4, 15000);
await sleep(300);
const stage = doc.querySelector('.matrix-stage').getBoundingClientRect();
const midX = stage.left + stage.width / 2;
const midY = stage.top + stage.height / 2;
const corners = {};
for (const cls of ['top-left', 'top-right', 'bottom-left', 'bottom-right']) {
  const el = doc.querySelector('.matrix-corner-' + cls);
  const r = el.getBoundingClientRect();
  corners[cls] = { cx: r.left + r.width / 2, cy: r.top + r.height / 2 };
}
return { midX, midY, corners };
"""
)

_CONFIGURE_THEAD = (
    _READY
    + """
const tr0 = await until(() => doc.querySelector('[data-configure-tbody] tr'), 15000);
await sleep(300);
const th = doc.querySelector('[data-configure-thead] th');
const thRect = th.getBoundingClientRect();
const tr0Rect = tr0.getBoundingClientRect();
return {
  thPosition: win.getComputedStyle(th).position,
  thBottom: thRect.bottom,
  tr0Top: tr0Rect.top,
};
"""
)

_CONFIGURE_1100 = (
    _READY
    + """
await until(() => doc.querySelector('[data-configure-tbody] tr'), 15000);
await sleep(300);
const table = doc.querySelector('.configure-table-col .leaderboard-table').getBoundingClientRect();
const col = doc.querySelector('.configure-table-col').getBoundingClientRect();
return {
  innerWidth: win.innerWidth,
  scrollWidth: doc.documentElement.scrollWidth,
  tableRight: table.right,
  colRight: col.right,
};
"""
)

_MOBILE_CARD_LONG_NAME = (
    _READY
    + """
const card = await until(() => doc.querySelector('.leaderboard-card'), 15000);
if (!card) return { missing: 'leaderboard-card' };
card.querySelector('.repo-name').textContent = 'an_extraordinarily_long_repository_name_without_any_natural_break_opportunities_at_all';
await sleep(200);
return {
  clientHeight: card.clientHeight,
  scrollHeight: card.scrollHeight,
  clientWidth: card.clientWidth,
  scrollWidth: card.scrollWidth,
};
"""
)


#: A name longer than its table cell (240 px) or its card: the name ends in "…" and the ecosystem chip beside it stays
#: whole, inside the cell or the card. A chip pushed past the cell's edge loses its text and shows an empty pill.
_CHIP_BESIDE_LONG_NAME = (
    _READY
    + """
const item = await until(() => doc.querySelector(%r), 15000);
if (!item) return { missing: %r };
item.querySelector('.repo-name').textContent = 'an_extraordinarily_long_repository_name_without_any_natural_break_opportunities_at_all';
await sleep(300);
const box = (%s).getBoundingClientRect();
const chip = item.querySelector('.eco-chip');
const rect = chip.getBoundingClientRect();
return { boxLeft: box.left, boxRight: box.right, boxTop: box.top, boxBottom: box.bottom,
         chipLeft: rect.left, chipRight: rect.right, chipTop: rect.top, chipBottom: rect.bottom, chipText: chip.textContent };
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

    cases = [
        {"name": f"width-{tab}", "path": path, **PHONE, "script": _PAGE_WIDTH % (settle, inject)}
        for tab, path, settle, inject in (
            ("ranked", "./", 1500, ""),
            ("notranked", "./not-ranked/", 2500, _LONG_NOT_RANKED_ROW),
            ("matrix", "./matrix/", 2500, ""),
            ("compare", "./compare/", 1500, ""),
            ("configure", "./weights/", 3500, ""),
            ("methodology", "./methodology/", 2500, ""),
        )
    ]
    cases += [
        {"name": "width-notranked-320", "path": "./not-ranked/", "width": 320, "height": 640,
         "script": _PAGE_WIDTH % (2500, _LONG_NOT_RANKED_ROW)},
        {"name": "drawer", "path": f"./projects/{first['owner'].lower()}/{first['name'].lower()}/", **PHONE, "script": _DRAWER},
        {"name": "header", "path": "./", **PHONE, "script": _HEADER},
        {"name": "active-tab", "path": "./methodology/", **PHONE, "script": _ACTIVE_TAB},
        {"name": "pager", "path": "./?page_size=20", **DESKTOP, "script": _PAGER_REVEAL},
        {"name": "chip", "path": "./", **DESKTOP, "script": _HEADER_CHIP},
        {"name": "compare-enter", "path": "./compare/", **DESKTOP, "script": _COMPARE_ENTER % first["name"]},
        {"name": "search-from-methodology", "path": "./methodology/", **DESKTOP, "script": _SEARCH_FROM_METHODOLOGY % first["name"]},
        {"name": "tablet-ranked", "path": "./", **TABLET, "script": _TABLET_RANKED},
        {"name": "tablet-persona", "path": "./?persona=senior_systems", **TABLET, "script": _TABLET_RANKED},
        {"name": "mobile-sort", "path": "./", **PHONE, "script": _MOBILE_SORT},
        {"name": "desktop-sticky-nav", "path": "./", **DESKTOP, "script": _DESKTOP_STICKY_NAV},
        {"name": "matrix-corners", "path": "./matrix/", **DESKTOP, "script": _MATRIX_CORNERS},
        {"name": "configure-thead", "path": "./weights/", **DESKTOP, "script": _CONFIGURE_THEAD},
        {"name": "configure-1100", "path": "./weights/", "width": 1100, "height": 800, "script": _CONFIGURE_1100},
        {"name": "mobile-card-long-name", "path": "./", **PHONE, "script": _MOBILE_CARD_LONG_NAME},
        {"name": "chip-table", "path": "./", **DESKTOP, "script": _CHIP_BESIDE_LONG_NAME % (
            ".leaderboard-table tbody tr[data-owner]", "a table row", "item.querySelector('td[data-cell=repository]')")},
        {"name": "chip-card", "path": "./", **PHONE, "script": _CHIP_BESIDE_LONG_NAME % (".leaderboard-card", "a card", "item")},
    ]
    cases += [
        {"name": f"names-{tab}", "path": path, **DESKTOP, "script": _UNNAMED_CONTROLS % settle}
        for tab, path, settle in (
            ("ranked", "./", 1500),
            ("notranked", "./not-ranked/", 2500),
            ("matrix", "./matrix/", 2500),
            ("compare", "./compare/", 1500),
            ("configure", "./weights/", 3500),
        )
    ]
    return ui_browser.probe(site, cases, tmp)


@pytest.mark.parametrize("tab", ["ranked", "notranked", "matrix", "compare", "configure", "methodology"])
def test_no_screen_is_wider_than_the_phone(measured, tab):
    got = measured[f"width-{tab}"]
    assert got["innerWidth"] == 375, got  # the browser did not zoom out to fit a wide page
    assert got["scrollWidth"] <= got["innerWidth"], got


def test_a_long_not_ranked_name_fits_a_small_phone(measured):
    got = measured["width-notranked-320"]
    assert got["innerWidth"] == 320, got
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


def test_the_header_search_on_the_methodology_page_opens_the_rankings_with_the_query(measured):
    got = measured["search-from-methodology"]
    assert "missing" not in got, got
    assert got["path"] == "/" and got["search"].startswith("?q="), got
    assert got["inPlace"] is True, got  # the app moved there itself, without a page load
    assert got["names"] and all(got["query"].lower() in name.lower() for name in got["names"]), got  # the rankings, filtered
    assert got["navCurrent"] == "Leaderboard", got


def test_the_header_shows_the_snapshot_date_not_a_release_candidate_tag(measured):
    got = measured["chip"]
    assert "missing" not in got, got
    assert got["text"] == SNAPSHOT_ID, got
    assert "rc" not in got["text"], got
    assert got["title"] and SNAPSHOT_ID in got["title"], got  # the exact version stays one hover away


def test_tablet_768px_ranked_table_fits_without_overflow_or_header_overlap(measured):
    for key in ("tablet-ranked", "tablet-persona"):
        got = measured[key]
        assert got["innerWidth"] == 768, got
        assert got["scrollWidth"] <= got["innerWidth"], got
        assert got["visibleHeaderCount"] >= 6, got
        assert got["overlaps"] == [], got


def test_phone_ranked_shows_mobile_sort_bar(measured):
    got = measured["mobile-sort"]
    assert got["hasSelect"] is True, got
    assert got["hasDir"] is True, got


def test_desktop_primary_nav_stays_sticky_below_header(measured):
    got = measured["desktop-sticky-nav"]
    assert got["position"] == "sticky", got
    assert got["topPx"] >= 40, got


def test_matrix_corner_labels_sit_in_all_four_corners(measured):
    got = measured["matrix-corners"]
    c = got["corners"]
    assert c["top-left"]["cx"] < got["midX"] and c["top-left"]["cy"] < got["midY"], got
    assert c["top-right"]["cx"] > got["midX"] and c["top-right"]["cy"] < got["midY"], got
    assert c["bottom-left"]["cx"] < got["midX"] and c["bottom-left"]["cy"] > got["midY"], got
    assert c["bottom-right"]["cx"] > got["midX"] and c["bottom-right"]["cy"] > got["midY"], got


def test_configure_sticky_thead_does_not_overlap_first_row_at_scroll_zero(measured):
    got = measured["configure-thead"]
    assert got["thPosition"] == "sticky", got
    assert got["thBottom"] <= got["tr0Top"] + 1, got


def test_configure_1100px_table_fits_inside_its_column_without_overflow(measured):
    got = measured["configure-1100"]
    assert got["innerWidth"] == 1100, got
    assert got["scrollWidth"] <= got["innerWidth"], got
    assert got["tableRight"] <= got["colRight"] + 1, got


def test_mobile_card_clamps_long_repository_name_within_fixed_card_height(measured):
    got = measured["mobile-card-long-name"]
    assert "missing" not in got, got
    assert got["scrollHeight"] <= got["clientHeight"], got
    assert got["scrollWidth"] <= got["clientWidth"], got


@pytest.mark.parametrize("case", ["chip-table", "chip-card"])
def test_a_long_name_keeps_its_ecosystem_chip_whole_beside_it(measured, case):
    got = measured[case]
    assert "missing" not in got, got
    assert got["chipText"] and got["chipRight"] > got["chipLeft"], got
    assert got["boxLeft"] - 0.5 <= got["chipLeft"] and got["chipRight"] <= got["boxRight"] + 0.5, got
    assert got["boxTop"] - 0.5 <= got["chipTop"] and got["chipBottom"] <= got["boxBottom"] + 0.5, got
