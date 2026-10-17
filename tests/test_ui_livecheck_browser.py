"""The live check in a real browser: nothing leaves the page before the click, then only api.github.com.

`web/app/livecheck.js` (06 §3 C, "Repositories outside the snapshot: live check") is the one place the site
may contact another origin, and only after the visitor presses its button. Unit tests pin the logic
(`web/app/tests/livecheck.test.mjs`); this module drives the built static site in headless Chrome
(`tests/ui_browser.py`) against the page's real Content-Security-Policy:

* opening the drawer for a repository the snapshot lacks requests nothing from any other origin;
* the button runs the check through `window.fetch` -- answered here by a stand-in for api.github.com, so the
  suite never touches the network -- and the panel shows six gates, announces the result and keeps focus;
* text from GitHub is rendered as text, the panel fits a phone, and a spent allowance offers a retry;
* the CSP meta is enforced: a request to any other origin is refused by `connect-src`;
* a search that is a repository name offers "Look up", which opens that drawer.

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
from serve.helpers import build_snapshot, finalize, fresh_db  # noqa: E402
from test_static_export import export_static  # noqa: E402

PHONE = {"width": 375, "height": 812}
DESKTOP = {"width": 1280, "height": 800}
MISSING = "nobody-here/nothing-here"
MISSING_PATH = f"./?repo={MISSING.replace('/', '%2F')}"

#: A stand-in for api.github.com installed on the page's own `window.fetch` (what app.js hands the check):
#: it records every API call and answers it; every other URL (the snapshot's data files) goes to the real fetch.
_FAKE_GITHUB = """
const API = 'https://api.github.com';
const realFetch = win.fetch.bind(win);
const seen = [];
const now = Date.now();
const iso = (days) => new Date(now - days * 864e5).toISOString();
const replies = {
  rate_limit: { resources: { core: { limit: 60, remaining: %(remaining)d, reset: Math.floor(now / 1000) + 600 } } },
  repo: { full_name: %(full_name)s, description: '<img src=x onerror="window.__pwned = 1">', stargazers_count: 3,
          language: 'Go', created_at: iso(900), pushed_at: iso(2), archived: false, fork: false, mirror_url: null,
          license: { spdx_id: 'MIT' } },
  tree: { truncated: false, tree: [{ path: 'go.mod', type: 'blob' }, { path: 'LICENSE', type: 'blob' }] },
  commits: [{ author: { login: 'alice', type: 'User' }, commit: { author: { email: 'alice@example.com' } } }],
  pulls: [{ number: 12, created_at: iso(20), closed_at: iso(10), merged_at: iso(10), updated_at: iso(10),
            author_association: 'CONTRIBUTOR', user: { login: 'bob', type: 'User' } }],
};
win.fetch = async (input, init) => {
  const url = String(input && input.url ? input.url : input);
  const resolved = new URL(url, win.location.href);
  if (resolved.origin === win.location.origin) return realFetch(input, init);
  if (resolved.origin !== API) throw new TypeError('Unexpected external origin: ' + resolved.origin);
  seen.push({ url: resolved.href, credentials: init && init.credentials, referrerPolicy: init && init.referrerPolicy });
  const p = resolved.pathname;
  const route = p === '/rate_limit' ? 'rate_limit' : p.includes('/git/trees/') ? 'tree'
    : p.endsWith('/commits') ? 'commits' : p.endsWith('/pulls') ? 'pulls' : 'repo';
  return new win.Response(JSON.stringify(replies[route]), { status: 200, headers: { 'content-type': 'application/json' } });
};
"""

_OPEN_PANEL = """
const btn = await until(() => doc.querySelector('.drawer [data-live-check]'), 20000);
if (!btn) return { missing: 'the live-check button' };
await sleep(500);
const external = win.performance.getEntriesByType('resource').map((e) => e.name).filter((n) => !n.startsWith(win.location.origin));
"""

_CHECK_FLOW = (
    _OPEN_PANEL
    + _FAKE_GITHUB
    + """
btn.focus();
btn.click();
const gates = await until(() => doc.querySelectorAll('.live-gates li').length === 6 && doc.querySelectorAll('.live-gates li'), 15000);
if (!gates) return { missing: 'the gate list', seen, html: doc.querySelector('.drawer').innerHTML.slice(0, 400) };
await sleep(300);
const drawer = doc.querySelector('.drawer');
const description = doc.querySelector('.live-check-description');
const link = doc.querySelector('.live-check-github');
return {
  externalBeforeClick: external,
  seen,
  statuses: [...gates].map((li) => [li.dataset.liveGate, li.querySelector('.live-pill').className.replace('live-pill', '').trim()]),
  announcement: doc.getElementById('app-live-region').textContent,
  focusInRegion: Boolean(doc.activeElement && doc.activeElement.closest('[data-live-check-region]')),
  descriptionHasElement: Boolean(description && description.querySelector('*')),
  descriptionText: description ? description.textContent : '',
  pwned: Boolean(win.__pwned),
  githubHref: link ? link.href : null,
  githubRel: link ? link.rel : null,
  scrollWidth: drawer.scrollWidth,
  clientWidth: drawer.clientWidth,
};
"""
)

_RATE_LIMITED = (
    _OPEN_PANEL
    + _FAKE_GITHUB
    + """
btn.click();
const banner = await until(() => doc.querySelector('.live-check-error'), 15000);
if (!banner) return { missing: 'the error banner', seen };
return {
  text: banner.textContent,
  role: banner.getAttribute('role'),
  retry: Boolean(banner.querySelector('[data-live-check]')),
  seen: seen.map((s) => new URL(s.url).pathname),
};
"""
)

_CSP_ENFORCED = """
await until(() => doc.querySelector('#app-main') && doc.querySelector('#app-main').children.length > 0, 15000);
const violations = [];
doc.addEventListener('securitypolicyviolation', (e) => violations.push({ blocked: e.blockedURI, directive: e.effectiveDirective }));
let outcome = 'allowed';
try { await win.fetch('https://example.invalid/porchlamp-csp-probe'); } catch (err) { outcome = 'refused'; }
await sleep(500);
return {
  outcome,
  violations,
  meta: Boolean(doc.querySelector('meta[http-equiv="Content-Security-Policy"]')),
  booted: doc.querySelector('#app-main').children.length > 0,
};
"""

_SEARCH_LOOKUP = """
const look = await until(() => doc.querySelector('.empty-state [data-owner][data-name]'), 20000);
if (!look) return { missing: 'the look-up button' };
const label = look.textContent;
look.click();
const btn = await until(() => doc.querySelector('.drawer [data-live-check]'), 20000);
return { label, drawerOffersCheck: Boolean(btn), key: btn ? btn.dataset.liveCheck : null, search: win.location.search };
"""

_CASE_INSENSITIVE_IN_SNAPSHOT = """
const title = await until(() => {
  const el = doc.querySelector('#drawer-title');
  return el && el.textContent.trim() === 'bulk/repo-0133' ? el : null;
}, 20000);
const scored = await until(() => doc.querySelector('.drawer [data-drawer-compare="bulk/repo-0133"]'), 20000);
return {
  title: title ? title.textContent.trim() : null,
  hasComposite: Boolean(scored),
  hasLivePanel: Boolean(doc.querySelector('.drawer [data-live-check-region]')),
  search: win.location.search,
};
"""


@pytest.fixture(scope="module")
def measured(tmp_path_factory):
    if ui_browser.find_chrome() is None:
        if os.environ.get("PORCHLAMP_ALLOW_MISSING_CHROME") == "1":
            pytest.skip("no Chrome or Chromium found (PORCHLAMP_ALLOW_MISSING_CHROME=1)")
        pytest.fail("no Chrome or Chromium on PATH; the browser checks are a real gate (or set PORCHLAMP_ALLOW_MISSING_CHROME=1)")
    tmp = tmp_path_factory.mktemp("livecheck")
    db = tmp / "db.sqlite"
    conn = fresh_db(db)
    build_snapshot(conn, db)
    finalize(conn, db)
    conn.close()
    site = tmp / "site"
    export_static.export(db, site)
    same_name = {"remaining": 60, "full_name": json.dumps(MISSING)}
    cases = [
        {"name": "flow-phone", "path": MISSING_PATH, **PHONE, "script": _CHECK_FLOW % same_name},
        {"name": "flow-desktop", "path": MISSING_PATH, **DESKTOP, "script": _CHECK_FLOW % same_name},
        {"name": "rate-limited", "path": MISSING_PATH, **PHONE, "script": _RATE_LIMITED % {**same_name, "remaining": 0}},
        {"name": "csp", "path": "./", **DESKTOP, "script": _CSP_ENFORCED},
        {"name": "lookup", "path": f"./?q={MISSING.replace('/', '%2F')}", **DESKTOP, "script": _SEARCH_LOOKUP},
        {"name": "case-insensitive", "path": "./?repo=BULK%2FREPO-0133.git", **DESKTOP, "script": _CASE_INSENSITIVE_IN_SNAPSHOT},
    ]
    return ui_browser.probe(site, cases, tmp)


@pytest.mark.parametrize("case", ["flow-phone", "flow-desktop"])
def test_nothing_leaves_the_page_before_the_click(measured, case):
    got = measured[case]
    assert "missing" not in got, got
    assert got["externalBeforeClick"] == [], got


@pytest.mark.parametrize("case", ["flow-phone", "flow-desktop"])
def test_the_click_spends_four_requests_on_the_github_api_with_no_credentials(measured, case):
    got = measured[case]
    assert "missing" not in got, got
    paths = sorted(entry["url"].split("api.github.com", 1)[1].split("?", 1)[0] for entry in got["seen"])
    assert paths == sorted([
        "/rate_limit",
        f"/repos/{MISSING}",
        f"/repos/{MISSING}/git/trees/HEAD",
        f"/repos/{MISSING}/commits",
        f"/repos/{MISSING}/pulls",
    ]), got["seen"]
    assert all(e["credentials"] == "omit" and e["referrerPolicy"] == "no-referrer" for e in got["seen"]), got["seen"]


@pytest.mark.parametrize("case", ["flow-phone", "flow-desktop"])
def test_the_panel_shows_six_gates_announces_and_keeps_focus(measured, case):
    got = measured[case]
    assert "missing" not in got, got
    assert got["statuses"] == [
        ["D-GATE-1", "is-pass"], ["D-GATE-2", "is-pass"], ["D-GATE-3", "is-pass"],
        ["D-GATE-4", "is-pass"], ["D-GATE-5", "is-not-checked"], ["D-GATE-6", "is-pass"],
    ], got
    assert got["announcement"] == "Live check done: 5 of 5 checks pass.", got
    assert got["focusInRegion"], got
    assert got["githubHref"] == f"https://github.com/{MISSING}", got
    assert "noopener" in got["githubRel"] and "noreferrer" in got["githubRel"], got


def test_text_from_github_is_rendered_as_text(measured):
    got = measured["flow-desktop"]
    assert not got["descriptionHasElement"], got
    assert got["descriptionText"].startswith("<img"), got
    assert not got["pwned"], got


def test_the_result_fits_the_phone(measured):
    got = measured["flow-phone"]
    assert got["scrollWidth"] <= got["clientWidth"], got


def test_a_spent_allowance_stops_before_spending_and_offers_a_retry(measured):
    got = measured["rate-limited"]
    assert "missing" not in got, got
    assert got["seen"] == ["/rate_limit"], got
    assert "allowance" in got["text"], got
    assert got["role"] == "alert", got
    assert got["retry"], got


def test_the_page_csp_refuses_any_other_origin(measured):
    got = measured["csp"]
    assert got["meta"] and got["booted"], got  # the app runs under its own policy (no inline script, no eval)
    assert got["outcome"] == "refused", got
    assert any(v["directive"] == "connect-src" and v["blocked"].startswith("https://example.invalid") for v in got["violations"]), got


def test_a_search_for_a_missing_repository_offers_to_look_it_up(measured):
    got = measured["lookup"]
    assert "missing" not in got, got
    assert got["label"] == f"Look up {MISSING}", got
    assert got["drawerOffersCheck"], got
    assert got["key"] == MISSING, got


def test_a_differently_cased_or_git_suffixed_in_snapshot_repo_loads_its_snapshot_record(measured):
    got = measured["case-insensitive"]
    assert got["title"] == "bulk/repo-0133", got
    assert got["hasComposite"], got
    assert not got["hasLivePanel"], got
    assert "repo=bulk%2Frepo-0133" in got["search"], got

