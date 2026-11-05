"""The first-pull-requests report and the lookup in a real browser, on the report's real record.

MEASURED defects (review of 2026-11-05, Chrome, 320 to 1920 px), none of which a test could see before this file:

* axis labels overlapped: ``2 mo`` over ``3 mo`` from 560 to 859 px, ``1 mo`` over ``3 mo`` at 320 to 360;
* the tables' last columns were cut off on a phone, with no sign that they scrolled;
* the histogram's last label and value ran past their card at 320;
* a hover tip stayed over the text through a scroll, and an off bar had under 3:1 against its card;
* the slider's reading was silent for a screen reader, the method's panel stretched to 790 px of blank space;
* on a phone with the keyboard up, the lookup gave no visible reaction to the button; a long username scrolled the page.

Chrome renders the real stylesheet and scripts (``tests/ui_browser.py``); a case that cannot find its element fails. GitHub
is stood in for on the page's own ``fetch``. An absent Chrome fails this module unless ``PORCHLAMP_ALLOW_MISSING_CHROME=1``.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import ui_browser  # noqa: E402
from serve.helpers import SNAPSHOT_ID, build_snapshot, finalize, fresh_db  # noqa: E402
from test_static_export import export_static  # noqa: E402

from porchlamp.serve import first_pull_requests_page as page_module  # noqa: E402

RECORD = ROOT / "web" / "app" / "tests" / "cases" / "first_pull_requests.json"
REPORT = "./first-pull-requests/"
LOOKUP = "./lookup/"
DESKTOP = {"width": 1280, "height": 800}
PHONE_WIDTHS = (320, 375, 559)
TICK_WIDTHS = (320, 375, 560, 768, 859, 1280)
LABEL_WIDTHS = (320, 375, 768, 1280)
OVERFLOW_WIDTHS = (320, 375, 560, 768, 1280, 1920)

_READY = """
await until(() => doc.querySelector('.fpr-plot') && !doc.querySelector('[data-fpr-slide]').hidden, 15000);
await sleep(150);
"""

_TICKS = _READY + """
const visible = (t) => { const s = win.getComputedStyle(t); return s.display !== 'none' && s.visibility !== 'hidden'; };
const collisions = [];
let seen = 0;
for (const plot of doc.querySelectorAll('.fpr-plot')) {
  const rows = [...plot.querySelectorAll('.fpr-tick-x')].filter(visible)
    .map((t) => ({ text: t.textContent, box: t.getBoundingClientRect() })).sort((a, b) => a.box.left - b.box.left);
  seen += rows.length;
  for (let i = 1; i < rows.length; i++) {
    const gap = rows[i].box.left - rows[i - 1].box.right;
    if (gap < 2) collisions.push([rows[i - 1].text, rows[i].text, Math.round(gap * 10) / 10]);
  }
}
return { seen, collisions };
"""

_TABLES = _READY + """
return [...doc.querySelectorAll('.fpr-table-wrap')].map((w) => [w.scrollWidth, w.clientWidth]);
"""

_HISTOGRAM_EDGE = _READY + """
const section = doc.getElementById('who-answers');
const style = win.getComputedStyle(section);
const box = section.getBoundingClientRect();
const inner = [box.left + parseFloat(style.paddingLeft), box.right - parseFloat(style.paddingRight)];
const text = (el) => { const range = doc.createRange(); range.selectNodeContents(el); return range.getBoundingClientRect(); };
const cols = [...section.querySelectorAll('.fpr-cols-hist .fpr-col')];
const edges = [];
for (const col of cols) for (const part of col.querySelectorAll('.fpr-col-value, .fpr-col-label')) {
  if (win.getComputedStyle(part).visibility === 'hidden') continue;
  const t = text(part);
  edges.push([part.textContent, Math.round((inner[0] - t.left) * 10) / 10, Math.round((t.right - inner[1]) * 10) / 10]);
}
return { columns: cols.length, past: edges.filter((e) => e[1] > 0.5 || e[2] > 0.5), read: edges.length };
"""

_OFF_BAR = _READY + """
const bar = doc.querySelector('.fpr-col-bar-off');
return { found: Boolean(bar), shadow: bar ? win.getComputedStyle(bar).boxShadow : null };
"""

_TIP_SCROLL = _READY + """
const bar = doc.querySelector('.fpr-cols-hist .fpr-col');
const tip = doc.getElementById('fpr-tip');
const box = bar.getBoundingClientRect();
bar.dispatchEvent(new win.PointerEvent('pointermove', { bubbles: true, clientX: box.left + 2, clientY: box.bottom - 2 }));
const shown = !tip.hidden;
win.scrollBy(0, 400);
await sleep(200);
// A frame nothing paints may never get its scroll event in this harness; the listener is what's on trial.
win.dispatchEvent(new win.Event('scroll'));
return { shown, text: tip.textContent, scrolled: win.scrollY, hiddenAfterScroll: tip.hidden };
"""

_SLIDER = _READY + """
const input = doc.getElementById('fpr-days');
const opening = input.getAttribute('aria-valuetext');
input.value = '3';
input.dispatchEvent(new win.Event('input', { bubbles: true }));
return { opening, moved: input.getAttribute('aria-valuetext'), shown: doc.querySelector('[data-fpr-label]').textContent };
"""

_LINE_LABELS = _READY + """
const hits = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
const visible = (t) => { const s = win.getComputedStyle(t); return s.display !== 'none' && s.visibility !== 'hidden'; };
const out = [];
const labels = [...doc.querySelectorAll('.fpr-line-label')];
for (const label of labels) {
  const box = label.getBoundingClientRect();
  const plot = label.closest('.fpr-plot');
  const area = plot.getBoundingClientRect();
  const name = label.textContent;
  if (box.left < area.left - 0.5 || box.right > area.right + 0.5 || box.top < area.top - 0.5 || box.bottom > area.bottom + 0.5) out.push([name, 'outside the plot']);
  for (const dot of plot.querySelectorAll('.fpr-dot')) if (hits(box, dot.getBoundingClientRect())) out.push([name, 'dot', dot.getAttribute('data-tip')]);
  for (const tick of [...plot.querySelectorAll('.fpr-tick-x, .fpr-tick-y')].filter(visible)) if (hits(box, tick.getBoundingClientRect())) out.push([name, 'tick', tick.textContent]);
  for (const other of labels) if (other !== label && other.closest('.fpr-plot') === plot && hits(box, other.getBoundingClientRect())) out.push([name, 'label', other.textContent]);
  for (const line of plot.querySelectorAll('.fpr-line')) {
    const points = line.getAttribute('points').trim().split(' ').map((p) => p.split(',').map(Number));
    for (let i = 1; i < points.length; i++) for (let step = 0; step <= 200; step++) {
      const x = area.left + area.width * (points[i - 1][0] + (points[i][0] - points[i - 1][0]) * step / 200) / 100;
      const y = area.top + area.height * (points[i - 1][1] + (points[i][1] - points[i - 1][1]) * step / 200) / 100;
      if (x > box.left && x < box.right && y > box.top && y < box.bottom) { out.push([name, 'line', line.getAttribute('class')]); step = 201; i = points.length; }
    }
  }
}
return { labels: labels.length, overlaps: out };
"""

_PAIR = _READY + """
const pair = doc.querySelector('.fpr-page .report-pair');
const [left, right] = [...pair.children].map((c) => c.getBoundingClientRect());
return { left: left.height, right: right.height, sideBySide: Math.abs(left.top - right.top) < 1 && right.left > left.right - 1 };
"""

_FIRST_SCREEN = _READY + """
const chart = doc.querySelector('.fpr-cols-hist').getBoundingClientRect();
const tiles = doc.querySelector('.fpr-tiles').getBoundingClientRect();
return { chartTop: chart.top, chartBottom: chart.bottom, tilesTop: tiles.top, screen: win.innerHeight };
"""

_OVERFLOW = """
await until(() => doc.querySelector('.report-page'), 15000);
await sleep(150);
return { scrollWidth: doc.documentElement.scrollWidth, clientWidth: doc.documentElement.clientWidth };
"""

#: A stand-in for api.github.com on the page's own fetch: three pull requests from `login`, nobody answering.
_FAKE_GITHUB = """
const realFetch = win.fetch.bind(win);
const iso = (days) => new Date(Date.now() - days * 864e5).toISOString();
const item = (n, days) => ({ number: n, title: 'A change ' + n, html_url: 'https://github.com/acme/widget/pull/' + n, state: 'open', draft: false,
  created_at: iso(days), closed_at: null, author_association: 'NONE', user: { login, type: 'User' },
  repository_url: 'https://api.github.com/repos/acme/widget', pull_request: { merged_at: null } });
win.fetch = async (input, init) => {
  const url = new URL(String(input && input.url ? input.url : input), win.location.href);
  if (url.origin === win.location.origin) return realFetch(input, init);
  const reset = Math.floor(Date.now() / 1000) + 600;
  const body = url.pathname === '/rate_limit' ? { resources: { core: { limit: 60, remaining: 60, reset }, search: { limit: 10, remaining: 10, reset } } }
    : url.pathname === '/search/issues' ? { total_count: 3, items: [item(1, 40), item(2, 20), item(3, 3)] }
    : url.pathname.endsWith('/timeline') ? [] : { login };
  return new win.Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
};
"""

_LOOKUP_RUN = """
const login = %s;
await until(() => doc.querySelector('[data-lookup-form]'), 15000);
await sleep(150);
""" + _FAKE_GITHUB + """
const field = doc.getElementById('lookup-login');
field.focus();
field.value = login;
doc.querySelector('[data-lookup-form]').requestSubmit();
const list = await until(() => doc.querySelector('#lookup-result .lookup-list'), 15000);
await sleep(200);
if (!list) return { missing: 'the result list', html: doc.getElementById('lookup-result').innerHTML.slice(0, 400) };
return {
  scrollWidth: doc.documentElement.scrollWidth,
  clientWidth: doc.documentElement.clientWidth,
  resultTop: doc.getElementById('lookup-result').getBoundingClientRect().top,
  screen: win.innerHeight,
  fieldKeepsFocus: doc.activeElement === field,
  asking: (doc.getElementById('lookup-result').textContent.match(/more answers/g) || []).length,
};
"""


@pytest.fixture(scope="module")
def measured(tmp_path_factory):
    if ui_browser.find_chrome() is None:
        if os.environ.get("PORCHLAMP_ALLOW_MISSING_CHROME") == "1":
            pytest.skip("no Chrome or Chromium found (PORCHLAMP_ALLOW_MISSING_CHROME=1)")
        pytest.fail("no Chrome or Chromium on PATH; these checks are a real gate (or set PORCHLAMP_ALLOW_MISSING_CHROME=1)")
    tmp = tmp_path_factory.mktemp("first-prs")
    db = tmp / "db.sqlite"
    conn = fresh_db(db)
    build_snapshot(conn, db)
    finalize(conn, db)
    conn.close()
    site = tmp / "site"
    export_static.export(db, site)
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    (site / "data" / SNAPSHOT_ID / page_module.DATA_FILE).write_text(json.dumps(record), encoding="utf-8")
    export_static._prerender(site, [export_static._write_first_pull_requests_page(site, record, exported_on=date(2026, 11, 5))])
    cases = [
        *({"name": f"ticks-{w}", "path": REPORT, "width": w, "height": 800, "script": _TICKS} for w in TICK_WIDTHS),
        *({"name": f"tables-{w}", "path": REPORT, "width": w, "height": 800, "script": _TABLES} for w in PHONE_WIDTHS),
        *({"name": f"edge-{w}", "path": REPORT, "width": w, "height": 800, "script": _HISTOGRAM_EDGE} for w in PHONE_WIDTHS),
        *({"name": f"labels-{w}", "path": REPORT, "width": w, "height": 800, "script": _LINE_LABELS} for w in LABEL_WIDTHS),
        *({"name": f"overflow-report-{w}", "path": REPORT, "width": w, "height": 800, "script": _OVERFLOW} for w in OVERFLOW_WIDTHS),
        *({"name": f"overflow-lookup-{w}", "path": LOOKUP, "width": w, "height": 800, "script": _OVERFLOW} for w in OVERFLOW_WIDTHS),
        {"name": "off-bar", "path": REPORT, **DESKTOP, "script": _OFF_BAR},
        {"name": "tip", "path": REPORT, "width": 375, "height": 700, "script": _TIP_SCROLL},
        {"name": "slider", "path": REPORT, **DESKTOP, "script": _SLIDER},
        {"name": "pair", "path": REPORT, **DESKTOP, "script": _PAIR},
        {"name": "first-screen", "path": REPORT, **DESKTOP, "script": _FIRST_SCREEN},
        {"name": "long-login", "path": LOOKUP, "width": 320, "height": 640, "script": _LOOKUP_RUN % json.dumps("a" * 39)},
        {"name": "keyboard-up", "path": LOOKUP, "width": 375, "height": 430, "script": _LOOKUP_RUN % json.dumps("ann")},
    ]
    return ui_browser.probe(site, cases, tmp, budget_ms=120000)


@pytest.mark.parametrize("width", TICK_WIDTHS)
def test_axis_labels_never_touch(measured, width):
    row = measured[f"ticks-{width}"]
    assert row["seen"] >= 6, row
    assert row["collisions"] == [], row


@pytest.mark.parametrize("width", PHONE_WIDTHS)
def test_no_table_is_cut_off_on_a_phone(measured, width):
    rows = measured[f"tables-{width}"]
    assert len(rows) == 3, rows
    assert [r for r in rows if r[0] > r[1]] == [], rows


@pytest.mark.parametrize("width", PHONE_WIDTHS)
def test_the_histogram_stays_inside_its_card(measured, width):
    row = measured[f"edge-{width}"]
    assert row["columns"] == 10 and row["read"] >= 3, row
    assert row["past"] == [], row


@pytest.mark.parametrize("width", LABEL_WIDTHS)
def test_a_lines_name_sits_clear_of_dots_lines_ticks_and_the_other_name(measured, width):
    row = measured[f"labels-{width}"]
    assert row["labels"] == 4, row
    assert row["overlaps"] == [], row


@pytest.mark.parametrize("name", [f"overflow-report-{w}" for w in OVERFLOW_WIDTHS] + [f"overflow-lookup-{w}" for w in OVERFLOW_WIDTHS])
def test_neither_page_scrolls_sideways(measured, name):
    row = measured[name]
    assert row["scrollWidth"] <= row["clientWidth"], (name, row)


def test_an_off_bar_has_an_edge_a_reader_can_see(measured):
    row = measured["off-bar"]
    assert row["found"], row
    assert row["shadow"] and "inset" in row["shadow"], row


def test_a_tip_goes_away_on_a_scroll(measured):
    row = measured["tip"]
    assert row["shown"] and row["text"], row
    assert row["scrolled"] > 0, row
    assert row["hiddenAfterScroll"] is True, row


def test_the_slider_speaks_the_numbers_not_only_the_days(measured):
    row = measured["slider"]
    assert "%" in row["opening"], row
    assert "%" in row["moved"] and row["moved"] != row["opening"], row
    assert row["shown"] in row["moved"], row


def test_the_short_panel_beside_the_method_keeps_its_own_height(measured):
    row = measured["pair"]
    assert row["sideBySide"], row
    assert row["right"] < row["left"] * 0.6, row


def test_the_first_chart_is_on_the_first_screen_and_the_tiles_come_after_it(measured):
    row = measured["first-screen"]
    assert row["chartBottom"] <= row["screen"], row
    assert row["tilesTop"] > row["chartBottom"], row


def test_the_longest_github_username_keeps_the_page_a_phone_wide(measured):
    row = measured["long-login"]
    assert "missing" not in row, row
    assert row["scrollWidth"] <= row["clientWidth"], row


def test_the_button_shows_its_result_on_a_phone_with_the_keyboard_up(measured):
    row = measured["keyboard-up"]
    assert "missing" not in row, row
    assert 0 <= row["resultTop"] < row["screen"], row
    assert row["fieldKeepsFocus"] is False, row
    assert row["asking"] == 1, row


def test_forced_colours_keep_every_bar_line_and_key():
    css = (ROOT / "web" / "app" / "styles" / "views.css").read_text(encoding="utf-8")
    blocks = [part for part in css.split("@media (forced-colors: active)")[1:] if ".fpr-col-bar" in part.split("\n}\n")[0]]
    assert len(blocks) == 1, "the report's forced-colors block"
    block = blocks[0].split("\n}\n")[0]
    for selector in (".fpr-col-bar", ".fpr-col-bar-off", ".fpr-dot", ".fpr-range-dot", ".fpr-range-line", ".fpr-key::before", ".fpr-line-first", ".fpr-line-returning"):
        assert selector in block, selector
    assert "forced-color-adjust: none" in block
