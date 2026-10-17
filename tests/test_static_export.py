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
    "custom coverage not clamped to 1",
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


def test_the_site_is_web_v2_with_only_api_js_replaced(site):
    _db, out = site
    assert (out / "api.js").read_bytes() == export_static.STATIC_API.read_bytes()
    for entry in export_static.WEB_V2.rglob("*"):
        relative = entry.relative_to(export_static.WEB_V2)
        if entry.is_dir() or relative.parts[0] in export_static._WEB_SKIP or relative == Path("api.js"):
            continue
        assert (out / relative).read_bytes() == entry.read_bytes(), relative
    assert not (out / "tests").exists() and not (out / "package.json").exists()
    assert (out / ".nojekyll").exists()  # GitHub Pages must not run Jekyll over the data
    assert f'"{SNAPSHOT_ID}"' in (out / "data-version.js").read_text()
    assert (out / "data" / SNAPSHOT_ID / "snapshot.json").is_file()


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
    """The root, the report, its frozen copy, the methodology, /check/ and the landing pages, in that order; a page
    the export did not write (here the methodology and every landing page but one) is not listed."""
    from porchlamp.serve import landing_pages

    written = ["", "black-holes/", "black-holes/2026-10-17/", "check/", f"{landing_pages.SLUGS[0]}/"]
    for rel in written:
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
        (tmp_path / rel / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (tmp_path / "methodology").mkdir()  # a folder without a page is not a page
    xml = export_static._sitemap_pages(tmp_path, "2026-10-17", "2026-10-17T05:35:00+00:00")
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
    assert re.findall(r"<loc>([^<]*)</loc>", xml) == [f"https://porchlamp.org/{rel}" for rel in written]
    assert re.findall(r"<lastmod>([^<]*)</lastmod>", xml) == ["2026-10-17"] * len(written)
