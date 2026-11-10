"""A downloads count belongs to a package, not to the repo name it was keyed by.

``scripts/gather.py`` keys ``raw/downloads/<ecosystem>__<repo name>.json``, and
until 2026-10 fetched the package named like the repo: npm ``router`` carried
291 M downloads a month on three repos (TanStack/router, reach/router,
vuejs/router), none of which owns it. Now the file also records the package it
counted and the repo that wrote it, and ``scripts/derive.py`` uses the count
only when it is the package the dependents envelope says deps.dev links to the
repo.

Subjects: the downloads block of ``gather_repo`` (which package is fetched,
what is left alone), ``derive.attributed_downloads`` and its effect on the
bundle (``downloads_status``). The registries and deps.dev are scripted; no
live network.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from porchlamp.collect import depsdev as dd

ROOT = Path(__file__).resolve().parent.parent.parent
NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)
KEY = "acme__widget"


def _load(name: str, fname: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / fname)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gather = _load("gather_attribution", "gather.py")
derive = _load("derive_attribution", "derive.py")

ATTRIBUTED = {"guess_linked": True, "deduped_count": 5}


def _write(raw: Path, collector: str, key: str, data) -> Path:
    p = raw / collector / f"{key}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"fetched_at": "2026-10-09T00:00:00+00:00", "source": "test",
                             "data": data}), encoding="utf-8")
    return p


def _seed(tmp_path: Path, dependents: Optional[dict], downloads: Optional[dict] = None,
          eco: str = "npm") -> Path:
    """Every collector but downloads (and, when ``dependents`` is None, dependents) is done."""
    raw = tmp_path / "raw"
    for col in ("github_meta", "pr_events", "pr_reviews", "contributors", "commits_180d",
                "actions_runs", "depsdev", "scorecard", "tree", "repo_object", "governance",
                "commits_365d", "pr_window", "releases", "issues_counts", "runs",
                "author_history"):
        _write(raw, col, KEY, {"default_branch": "main"})
    _write(raw, "gfi", KEY, [{"gfi_newcomer_v2": True, "labels_matched": [], "queries": [],
                               "truncated": False}])
    if dependents is not None:
        _write(raw, "dependents", KEY, dependents)
    if downloads is not None:
        _write(raw, "downloads", f"{eco}__widget", downloads)
    return raw


def _downloads_file(raw: Path, eco: str = "npm") -> dict:
    return json.loads((raw / "downloads" / f"{eco}__widget.json").read_text())["data"]


def _npm_registry(monkeypatch, tmp_path: Path, counts: dict[str, int]) -> list[str]:
    """Point gather at ``tmp_path`` and script the npm download API; returns the URLs asked."""
    monkeypatch.setattr(gather, "DATA", tmp_path / "raw")
    monkeypatch.setattr(gather.time, "sleep", lambda s: None)
    asked: list[str] = []

    def get_json(url: str, headers=None):
        asked.append(url)
        for package, n in counts.items():
            if url == f"https://api.npmjs.org/downloads/point/last-month/{package}":
                return {"downloads": n}
        return None
    monkeypatch.setattr(gather, "_get_json", get_json)
    return asked


ONLY_DOWNLOADS = frozenset({"downloads"})


# --- gather: which package is fetched ------------------------------------------------

def test_gather_downloads_of_the_repo_named_package_reuse_the_existing_file(tmp_path, monkeypatch, block_live_network):
    raw = _seed(tmp_path, {**ATTRIBUTED, "package": "widget"}, {"monthly": 7})
    asked = _npm_registry(monkeypatch, tmp_path, {})
    st = gather.gather_repo("acme", "widget", "npm", collectors=ONLY_DOWNLOADS)
    assert st["downloads"] == "skip" and asked == []
    assert _downloads_file(raw) == {"monthly": 7}


def test_gather_downloads_reuse_a_file_whose_package_is_respelled(tmp_path, monkeypatch, block_live_network):
    """The dependents envelope spells the crate ``wid_get``; the file counted
    ``wid-get`` (the repo name). One crate: nothing to fetch."""
    raw = tmp_path / "raw"
    _write(raw, "dependents", "acme__wid-get", {**ATTRIBUTED, "package": "wid_get"})
    _write(raw, "downloads", "crates__wid-get", {"monthly": 4})
    monkeypatch.setattr(gather, "DATA", raw)
    monkeypatch.setattr(gather.time, "sleep", lambda s: None)

    def must_not_fetch(url, headers=None):
        raise AssertionError("same crate, respelled: the file is reused")
    monkeypatch.setattr(gather, "_get_json", must_not_fetch)
    st = gather.gather_repo("acme", "wid-get", "crates", collectors=ONLY_DOWNLOADS)
    assert st["downloads"] == "skip"
    assert json.loads((raw / "downloads" / "crates__wid-get.json").read_text())["data"] == {"monthly": 4}


def test_gather_downloads_follow_the_package_deps_dev_links_not_the_repo_name(tmp_path, monkeypatch, block_live_network):
    """acme/widget publishes ``@acme/widget-core``. The file under the repo's
    name holds a count of the unrelated npm ``widget``."""
    raw = _seed(tmp_path, {**ATTRIBUTED, "package": "@acme/widget-core"}, {"monthly": 99})
    asked = _npm_registry(monkeypatch, tmp_path, {"@acme/widget-core": 1234})
    st = gather.gather_repo("acme", "widget", "npm", collectors=ONLY_DOWNLOADS)
    assert st["downloads"] == "ok"
    assert asked == ["https://api.npmjs.org/downloads/point/last-month/@acme/widget-core"]
    assert _downloads_file(raw) == {"monthly": 1234, "package": "@acme/widget-core",
                                    "repo": "acme/widget"}


def test_gather_downloads_without_a_linked_package_fetch_nothing(tmp_path, monkeypatch, block_live_network):
    raw = _seed(tmp_path, {"package": None, "guess_linked": False,
                           "source_used": "deps.dev:no_package"}, {"monthly": 99})
    asked = _npm_registry(monkeypatch, tmp_path, {"widget": 99})
    st = gather.gather_repo("acme", "widget", "npm", collectors=ONLY_DOWNLOADS)
    assert st["downloads"] == "skip" and asked == []
    assert _downloads_file(raw) == {"monthly": 99}      # left on disk; derive.py ignores it


def test_gather_downloads_leave_a_shared_file_held_by_another_repo(tmp_path, monkeypatch, block_live_network):
    """Two repos called ``widget``: the first to write the shared file keeps it,
    so reruns do not flip it back and forth."""
    held = {"monthly": 5, "package": "other-pkg", "repo": "other/widget"}
    raw = _seed(tmp_path, {**ATTRIBUTED, "package": "@acme/widget-core"}, held)
    asked = _npm_registry(monkeypatch, tmp_path, {"@acme/widget-core": 1234})
    st = gather.gather_repo("acme", "widget", "npm", collectors=ONLY_DOWNLOADS)
    assert st["downloads"] == "skip" and asked == []
    assert _downloads_file(raw) == held


def test_gather_downloads_refetch_a_null_count_of_the_right_package(tmp_path, monkeypatch, block_live_network):
    raw = _seed(tmp_path, {**ATTRIBUTED, "package": "widget"},
                {"monthly": None, "package": "widget", "repo": "acme/widget"})
    _npm_registry(monkeypatch, tmp_path, {"widget": 42})
    assert gather.gather_repo("acme", "widget", "npm", collectors=ONLY_DOWNLOADS)["downloads"] == "ok"
    assert _downloads_file(raw)["monthly"] == 42


def test_gather_downloads_force_refetches(tmp_path, monkeypatch, block_live_network):
    raw = _seed(tmp_path, {**ATTRIBUTED, "package": "widget"}, {"monthly": 7})
    _npm_registry(monkeypatch, tmp_path, {"widget": 8})
    st = gather.gather_repo("acme", "widget", "npm", force=True, collectors=ONLY_DOWNLOADS)
    assert st["downloads"] == "ok" and _downloads_file(raw)["monthly"] == 8


def test_gather_downloads_unanswered_deps_dev_is_pending_and_fetches_nothing(tmp_path, monkeypatch, block_live_network):
    """No attributed dependents envelope and deps.dev gives no answer: the
    repo's package is unknown, so nothing is fetched or written."""
    raw = _seed(tmp_path, {"default_branch": "main"})          # legacy dependents envelope
    asked = _npm_registry(monkeypatch, tmp_path, {"widget": 7})
    monkeypatch.setattr(dd, "_fetch_status", lambda url: (None, None))
    st = gather.gather_repo("acme", "widget", "npm", collectors=ONLY_DOWNLOADS)
    assert st["downloads"] == "pending" and asked == []
    assert not (raw / "downloads").exists()


def test_gather_resolves_once_for_downloads_and_dependents(tmp_path, monkeypatch, block_live_network):
    """With no dependents envelope yet, downloads resolve the package and hand
    the result to the dependents collector: deps.dev is asked about the repo
    once, not twice."""
    raw = _seed(tmp_path, None)
    asked = _npm_registry(monkeypatch, tmp_path, {"@acme/widget-core": 1234})
    payload = {"versions": [{"versionKey": {"system": "NPM", "name": "@acme/widget-core",
                                            "version": "1.0.0"}, "relationType": "SOURCE_REPO"}]}
    fetches: list[str] = []

    def fetch_status(url: str):
        fetches.append(url)
        return (200, payload) if "/projects/" in url else (404, None)

    def dd_get_json(url: str):
        if url == dd.package_url("npm", "@acme/widget-core"):
            return {"versions": [{"versionKey": {"version": "1.0.0"}, "isDefault": True}]}
        if url == dd.version_dependents_url("npm", "@acme/widget-core", "1.0.0"):
            return {"dependentCount": 12}
        return None
    monkeypatch.setattr(dd, "_fetch_status", fetch_status)
    monkeypatch.setattr(dd, "_get_json", dd_get_json)
    monkeypatch.setattr(gather, "SLEEP_KEYLESS", 0)
    st = gather.gather_repo("acme", "widget", "npm",
                            collectors=frozenset({"downloads", "dependents"}))
    assert st["dependents"] == "ok" and st["downloads"] == "ok"
    assert [u for u in fetches if "/projects/" in u] == [dd.project_packages_url("acme", "widget")]
    assert asked == ["https://api.npmjs.org/downloads/point/last-month/@acme/widget-core"]
    dep = json.loads((raw / "dependents" / f"{KEY}.json").read_text())["data"]
    assert dep["package"] == "@acme/widget-core" and dep["guess_linked"] is False
    assert _downloads_file(raw)["package"] == "@acme/widget-core"


def test_gather_ecosystems_without_a_downloads_registry_are_unchanged(tmp_path, monkeypatch, block_live_network):
    raw = _seed(tmp_path, {**ATTRIBUTED, "package": None}, eco="maven")
    monkeypatch.setattr(gather, "DATA", raw)
    monkeypatch.setattr(gather.time, "sleep", lambda s: None)
    st = gather.gather_repo("acme", "widget", "maven", collectors=ONLY_DOWNLOADS)
    assert st["downloads"] == "unmeasured"
    assert _downloads_file(raw, "maven") == {"monthly": None}


# --- derive: whose count is it -----------------------------------------------------------

def _derive(tmp_path: Path, eco: str, dependents: Optional[dict], downloads: Optional[dict]) -> dict:
    raw = tmp_path / "raw"
    if dependents is not None:
        _write(raw, "dependents", KEY, dependents)
    if downloads is not None:
        _write(raw, "downloads", f"{eco}__widget", downloads)
    return derive.derive_repo("acme", "widget", derive.Ecosystem(eco), tmp_path, fetch=False, now=NOW)


def test_derive_uses_the_count_of_the_repo_named_package(tmp_path):
    out = _derive(tmp_path, "npm", {**ATTRIBUTED, "package": "widget"}, {"monthly": 5000})
    assert out["meta"]["monthly_downloads"] == 5000
    assert out["meta"]["downloads_status"] == "measured"


def test_derive_uses_the_count_of_a_renamed_package(tmp_path):
    out = _derive(tmp_path, "npm", {**ATTRIBUTED, "package": "@acme/widget-core"},
                  {"monthly": 1234, "package": "@acme/widget-core", "repo": "acme/widget"})
    assert out["meta"]["monthly_downloads"] == 1234
    assert out["meta"]["downloads_status"] == "measured"
    assert out["provenance"]["monthly_downloads"]["source"] == "raw/downloads/npm__widget.json"


def test_derive_withholds_the_count_of_another_package(tmp_path):
    """The file under ``npm__widget`` counted the unrelated npm ``widget``."""
    out = _derive(tmp_path, "npm", {**ATTRIBUTED, "package": "@acme/widget-core"}, {"monthly": 5000})
    assert out["meta"]["monthly_downloads"] == 0
    assert out["confidence"]["monthly_downloads"] == "missing"
    assert out["meta"]["downloads_status"] == "unattributed"
    assert out["provenance"]["downloads_status"]["source"] is None


def test_derive_withholds_a_count_when_the_repo_has_no_linked_package(tmp_path):
    out = _derive(tmp_path, "npm", {"package": None, "guess_linked": False}, {"monthly": 5000})
    assert out["meta"]["monthly_downloads"] == 0
    assert out["meta"]["downloads_status"] == "unattributed"


def test_derive_compares_crate_names_by_registry_identity(tmp_path):
    """cfg_if and cfg-if are one crate; the dependents envelope may spell it
    either way."""
    out = _derive(tmp_path, "crates", {**ATTRIBUTED, "package": "cfg_if"},
                  {"monthly": 9, "package": "cfg-if"})
    assert out["meta"]["monthly_downloads"] == 9
    out = _derive(tmp_path / "x", "npm", {**ATTRIBUTED, "package": "a-b"}, {"monthly": 9, "package": "a.b"})
    assert out["meta"]["monthly_downloads"] == 0 and out["meta"]["downloads_status"] == "unattributed"


def test_derive_compares_package_names_case_insensitively(tmp_path):
    out = _derive(tmp_path, "crates", {**ATTRIBUTED, "package": "widget"},
                  {"monthly": 9, "package": "Widget"})
    assert out["meta"]["monthly_downloads"] == 9


def test_derive_without_an_attributed_dependents_envelope_passes_the_count_through(tmp_path):
    """Older data: nothing to compare the count with, so it stays as it was."""
    for dependents in (None, {"deduped_count": 5}):
        out = _derive(tmp_path / ("a" if dependents is None else "b"), "npm", dependents, {"monthly": 5000})
        assert out["meta"]["monthly_downloads"] == 5000
        assert out["meta"]["downloads_status"] == "measured"


def test_derive_a_linked_package_with_no_file_yet_is_pending_not_unattributed(tmp_path):
    out = _derive(tmp_path, "npm", {**ATTRIBUTED, "package": "widget"}, None)
    assert out["meta"]["downloads_status"] == "pending"


def test_derive_leaves_ecosystems_without_a_downloads_registry_alone(tmp_path):
    out = _derive(tmp_path, "maven", {"package": None, "guess_linked": False}, None)
    assert out["meta"]["downloads_status"] == "unmeasured"


def test_attributed_downloads_table():
    f = derive.attributed_downloads
    linked = {"package": "p", "guess_linked": True}
    assert f({"monthly": 1, "package": "p"}, linked, "pypi", "o", "n") == ({"monthly": 1, "package": "p"}, False)
    assert f({"monthly": 1, "package": "q"}, linked, "pypi", "o", "n") == (None, True)
    assert f({"monthly": 1}, linked, "pypi", "o", "n") == (None, True)           # legacy: counted "n"
    assert f({"monthly": 1}, {"package": "n", "guess_linked": True}, "pypi", "o", "N")[1] is False   # PEP 503 name
    assert f(None, linked, "pypi", "o", "n") == (None, False)                   # no file: just missing
    assert f({"monthly": 1}, {"package": None, "guess_linked": False}, "npm", "o", "n") == (None, True)
    assert f({"monthly": 1}, {"package": ""}, "npm", "o", "n") == ({"monthly": 1}, False)   # not attributed
    assert f({"monthly": 1}, linked, "maven", "o", "n") == ({"monthly": 1}, False)
    assert f({"monthly": 1}, None, "npm", "o", "n") == ({"monthly": 1}, False)
