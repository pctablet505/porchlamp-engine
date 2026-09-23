"""The static site (``scripts/export_static.py`` + ``web/static/api.js``)
answers every request the way the API does -- checked request by request by
``scripts/verify_static_export.py`` on ``tests/serve``'s synthetic snapshot
-- and that check catches a broken adapter (its mutants).

An absent Node.js interpreter fails this module, as it fails
``tests/test_ui_node_suite.py`` (H4), unless ``CVI_ALLOW_MISSING_NODE=1``.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))  # serve.helpers -> store.helpers

from serve.helpers import SNAPSHOT_ID, build_snapshot, finalize, fresh_db  # noqa: E402


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
)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    if verify_static_export.find_node() is None:
        if os.environ.get("CVI_ALLOW_MISSING_NODE") == "1":
            pytest.skip("no Node.js interpreter found (CVI_ALLOW_MISSING_NODE=1)")
        pytest.fail("no Node.js interpreter found on PATH, in CVI_NODE_BIN, or in ~/.local/opt/node-*")
    tmp = tmp_path_factory.mktemp("static")
    db = tmp / "db.sqlite"
    conn = fresh_db(db)
    build_snapshot(conn, db)
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


def test_bucket_of_spreads_repos_over_every_file():
    buckets = {export_static.bucket_of(f"owner{i}", f"repo{i}") for i in range(5000)}
    assert buckets == {f"{b:02x}" for b in range(export_static.BUCKETS)}


@pytest.mark.parametrize(
    "text, what",
    [
        ('{"x": "someone@example.com"}', "an email address"),
        ('{"x": "ghp_' + "a1" * 18 + '"}', "a GitHub token"),
        ('{"x": "/home/someone/Projects/data"}', "a local home path"),
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
