"""Tests for eligibility metadata and software classification.

Subjects:
* ``cvi.collect.repo_object`` — the repo-object collector (recorded-response
  oracles; the gh/urllib boundary is mocked, no live network) and the
  D-GATE-1/2/3/6 marker derivations;
* the append-only hooks at the end of ``scripts/gather.py`` (the
  ``repo_object`` status entry) and ``scripts/derive.py`` (the seven
  eligibility markers in the derived bundle).

Flag oracles are recorded envelopes: ``mirror_url`` set -> ``mirror``;
``archived: true`` -> ``archived``; ``fork: true`` with a parent -> ``fork``;
missing ``license.spdx_id`` -> ``unlicensed``; an ``awesome-*`` shape (no
manifest at depth <= 2) -> ``non_software``; zero PRs ever -> ``no_prs_ever``.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

from cvi.collect import repo_object as ro_mod
from cvi.collect.envelope import envelope_hash

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)
KEY = "acme__widget"


def _load_script(modname: str, fname: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(modname, ROOT / "scripts" / fname)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[modname] = module
    spec.loader.exec_module(module)
    return module


gather = _load_script("cvi_gather_w19", "gather.py")
derive = _load_script("cvi_derive_w19", "derive.py")


def write_env(raw: Path, collector: str, key: str, data,
              at: str = "2026-09-11T00:00:00Z") -> None:
    p = raw / collector / f"{key}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"fetched_at": at, "source": "test", "data": data}),
                 encoding="utf-8")


def ro_data(**over) -> dict:
    """A healthy repo_object envelope: MIT, 12 PRs, a manifest in the tree,
    no archived/mirror/fork, no off-platform markers."""
    base = {
        "archived": False, "mirror_url": None, "fork": False, "parent": None,
        "license_spdx_id": "MIT", "pull_requests_total": 12,
        "tree_paths_le2": ["LICENSE", "README.md", "pyproject.toml", "src/app.py"],
        "tree_truncated": False, "tree_source": "tree-envelope",
        "offplatform_paths": [],
        "maintainers_has_l_lines": None, "maintainers_l_line_count": None,
        "readme_off_platform": None, "readme_off_platform_hits": None,
        "maintainers_url": None, "readme_url": None,
    }
    base.update(over)
    return base


def run(tmp: Path) -> dict:
    return derive.derive_repo("acme", "widget", derive.Ecosystem.PYPI, tmp,
                              fetch=False, now=NOW)


# --- pure derivation helpers -------------------------------------------------

def test_tree_depth_and_filter_le2():
    assert ro_mod.tree_depth("Makefile") == 0
    assert ro_mod.tree_depth("src/Makefile") == 1
    assert ro_mod.tree_depth("a/b/Makefile") == 2
    assert ro_mod.filter_paths_le2(None) is None
    assert ro_mod.filter_paths_le2(
        ["Makefile", "src/a.py", "a/b/c.py", "x/y/z/w.py"]) == \
        ["Makefile", "src/a.py", "a/b/c.py"]


def test_has_manifest_recognised_set():
    assert ro_mod.has_manifest(["pyproject.toml"]) is True
    assert ro_mod.has_manifest(["src/package.json"]) is True
    assert ro_mod.has_manifest(["a/b/Cargo.toml"]) is True
    assert ro_mod.has_manifest(["sub/build.gradle.kts"]) is True
    assert ro_mod.has_manifest(["d/mylib.cabal"]) is True
    assert ro_mod.has_manifest(["Makefile", "README.md"]) is True
    assert ro_mod.has_manifest(["makefile", "README.md"]) is False  # spec lists Makefile
    assert ro_mod.has_manifest(["README.md", "docs/guide.md"]) is False


def test_has_license_file():
    assert ro_mod.has_license_file(["LICENSE"]) is True
    assert ro_mod.has_license_file(["LICENSE.md", "src/app.py"]) is True
    assert ro_mod.has_license_file(["license"]) is True
    assert ro_mod.has_license_file(["licenses/apache-2.0.md"]) is False
    assert ro_mod.has_license_file(["COPYING", "src/app.py"]) is False


def test_off_platform_paths_exact_prefix():
    assert ro_mod.off_platform_paths(
        ["Documentation/process/submitting-patches.rst"]) == \
        ["Documentation/process/submitting-patches.rst"]
    assert ro_mod.off_platform_paths(
        ["documentation/process/submitting-patches.md"]) == \
        ["documentation/process/submitting-patches.md"]
    assert ro_mod.off_platform_paths(["docs/process/submitting-patches.md"]) == []
    assert ro_mod.off_platform_paths(["Documentation/process/coding-style.rst"]) == []


def test_readme_off_platform_hits_named_patterns():
    assert ro_mod.readme_off_platform_hits(None) is None
    assert ro_mod.readme_off_platform_hits("ordinary project readme") == []
    assert "gerrit" in ro_mod.readme_off_platform_hits("We use Gerrit for code review.")
    assert "phabricator" in ro_mod.readme_off_platform_hits("Send patches to Phabricator.")
    assert "mailing_list" in ro_mod.readme_off_platform_hits(
        "Patches go to the gcc-patches mailing list.")


def test_maintainers_l_lines_count():
    assert ro_mod.maintainers_l_lines(None) is None
    body = "M: Acme <a@x.com>\nL: widget@x.com\nS: maintained\nL: more@x.com\n"
    assert ro_mod.maintainers_l_lines(body) == 2


# --- recorded oracles per flag (through the derive hook) ----------------------

def test_oracle_mirror_url_set(tmp_path):
    write_env(tmp_path / "raw", "repo_object", KEY,
              ro_data(mirror_url="git://example.com/x.git"))
    out = run(tmp_path)
    assert out["meta"]["mirror"] is True
    assert out["confidence"]["mirror"] == "verified"


def test_oracle_archived_true(tmp_path):
    write_env(tmp_path / "raw", "repo_object", KEY, ro_data(archived=True))
    out = run(tmp_path)
    assert out["meta"]["archived"] is True
    assert out["confidence"]["archived"] == "verified"


def test_oracle_fork_with_parent(tmp_path):
    write_env(tmp_path / "raw", "repo_object", KEY,
              ro_data(fork=True, parent={"owner": "upstream-org", "name": "widget"}))
    out = run(tmp_path)
    assert out["meta"]["fork"] is True
    assert out["confidence"]["fork"] == "verified"


def test_oracle_unlicensed_spdx_missing(tmp_path):
    write_env(tmp_path / "raw", "repo_object", KEY,
              ro_data(license_spdx_id=None,
                      tree_paths_le2=["README.md", "src/app.py"]))
    out = run(tmp_path)
    assert out["meta"]["unlicensed"] is True
    assert out["confidence"]["unlicensed"] == "derived"


def test_oracle_unlicensed_noassertion_license_present_is_false(tmp_path):
    # base tree lists LICENSE -> D-GATE-3 finds a license file, no fail
    write_env(tmp_path / "raw", "repo_object", KEY, ro_data(license_spdx_id="NOASSERTION"))
    out = run(tmp_path)
    assert out["meta"]["unlicensed"] is False
    assert out["confidence"]["unlicensed"] == "derived"


def test_oracle_non_software_awesome_repo(tmp_path):
    # an awesome-* list shape: no build/package manifest at depth <= 2
    write_env(tmp_path / "raw", "repo_object", KEY, ro_data(
        license_spdx_id="CC0-1.0",
        tree_paths_le2=["LICENSE", "README.md", "docs/README.md",
                        "categories/python.md"]))
    out = run(tmp_path)
    assert out["meta"]["non_software"] is True
    assert out["confidence"]["non_software"] == "derived"


def test_oracle_no_prs_ever(tmp_path):
    write_env(tmp_path / "raw", "repo_object", KEY, ro_data(pull_requests_total=0))
    out = run(tmp_path)
    assert out["meta"]["no_prs_ever"] is True
    assert out["confidence"]["no_prs_ever"] == "verified"


def test_oracle_off_platform_tree_marker(tmp_path):
    write_env(tmp_path / "raw", "repo_object", KEY, ro_data(
        tree_paths_le2=["Makefile", "Documentation/process/submitting-patches.rst"],
        offplatform_paths=["Documentation/process/submitting-patches.rst"]))
    out = run(tmp_path)
    assert out["meta"]["off_platform"] is True


def test_oracle_off_platform_maintainers_marker(tmp_path):
    write_env(tmp_path / "raw", "repo_object", KEY, ro_data(
        maintainers_has_l_lines=True, maintainers_l_line_count=412,
        maintainers_url="https://raw.githubusercontent.com/acme/widget/HEAD/MAINTAINERS"))
    out = run(tmp_path)
    assert out["meta"]["off_platform"] is True


def test_oracle_off_platform_readme_marker(tmp_path):
    write_env(tmp_path / "raw", "repo_object", KEY, ro_data(
        readme_off_platform=True, readme_off_platform_hits=["mailing_list"],
        readme_url="https://raw.githubusercontent.com/acme/widget/HEAD/README.md"))
    out = run(tmp_path)
    assert out["meta"]["off_platform"] is True


def test_truncated_tree_non_software_false_flagged(tmp_path):
    # an incomplete tree cannot prove a manifest absent
    write_env(tmp_path / "raw", "repo_object", KEY,
              ro_data(tree_paths_le2=["README.md"], tree_truncated=True))
    out = run(tmp_path)
    assert out["meta"]["non_software"] is False
    assert out["confidence"]["non_software"] == "flag"


def test_healthy_repo_all_flags_false(tmp_path):
    # over-correction guard: the correct path is unchanged
    write_env(tmp_path / "raw", "repo_object", KEY, ro_data())
    out = run(tmp_path)
    m, c = out["meta"], out["confidence"]
    for f in ro_mod.ELIGIBILITY_FIELDS:
        assert m[f] is False, f
    assert c["archived"] == c["mirror"] == c["fork"] == "verified"
    assert c["unlicensed"] == "verified"
    assert c["non_software"] == c["off_platform"] == "derived"
    assert c["no_prs_ever"] == "verified"


def test_no_envelopes_all_flags_false_missing(tmp_path):
    out = run(tmp_path)
    m, c = out["meta"], out["confidence"]
    for f in ro_mod.ELIGIBILITY_FIELDS:
        assert m[f] is False, f
        assert c[f] == "missing", f
    assert out["provenance"]["non_software"]["source"] is None


def test_fallback_to_recorded_meta_and_tree(tmp_path):
    # Legacy sweep: only github_meta + tree recorded, no repo_object envelope
    write_env(tmp_path / "raw", "github_meta", KEY,
              {"stars": 10, "forks": 0, "pushed_at": "2026-09-01T00:00:00Z",
               "archived": True, "created_at": "2020-01-01T00:00:00Z",
               "license_spdx": "NOASSERTION", "default_branch": "main"})
    write_env(tmp_path / "raw", "tree", KEY,
              {"paths": ["README.md", "src/app.py"], "truncated": False})
    out = run(tmp_path)
    m, c = out["meta"], out["confidence"]
    assert m["archived"] is True and c["archived"] == "verified"
    assert out["provenance"]["archived"]["source"].endswith("github_meta/acme__widget.json")
    assert m["unlicensed"] is True and c["unlicensed"] == "derived"
    assert m["non_software"] is True and c["non_software"] == "derived"
    assert m["off_platform"] is False and c["off_platform"] == "derived"
    assert m["mirror"] is False and m["fork"] is False and m["no_prs_ever"] is False
    assert c["mirror"] == c["fork"] == c["no_prs_ever"] == "missing"


def test_derive_repo_provenance_points_at_repo_object_envelope(tmp_path):
    write_env(tmp_path / "raw", "repo_object", KEY, ro_data(),
              at="2026-09-11T06:00:00Z")
    out = run(tmp_path)
    for f in ro_mod.ELIGIBILITY_FIELDS:
        assert out["provenance"][f]["source"].endswith(f"repo_object/{KEY}.json")
        assert out["provenance"][f]["fetched_at"] == "2026-09-11T06:00:00Z"


# --- collector recorded-response oracles --------------------------------------

def test_collect_writes_recorded_envelope(tmp_path):
    raw = tmp_path / "raw"
    write_env(raw, "tree", KEY, {"paths": [
        "MAINTAINERS", "README.md", "Makefile", "src/a.py", "x/y/z/w.py",
        "Documentation/process/submitting-patches.rst"], "truncated": False})
    recorded_r = {
        "isArchived": True, "mirrorUrl": "git://example/x.git", "isFork": True,
        "parent": {"name": "widget", "owner": {"login": "upstream"}},
        "licenseInfo": {"spdxId": "GPL-2.0"},
        "pullRequests": {"totalCount": 0}}
    bodies = {
        "https://raw.githubusercontent.com/acme/widget/HEAD/MAINTAINERS":
            "M: acme <acme@example.com>\nL: widget@example.com\nS: maintained\n",
        "https://raw.githubusercontent.com/acme/widget/HEAD/README.md":
            "Patches are reviewed in Gerrit before merge.\n"}
    with patch.object(ro_mod, "_gh_graphql", return_value=(recorded_r, None)), \
         patch.object(ro_mod, "_fetch_body", side_effect=lambda u: bodies.get(u)):
        status = ro_mod.collect_repo_object(raw, "acme", "widget", "main")
    assert status == "ok"
    doc = json.loads((raw / "repo_object" / f"{KEY}.json").read_text(encoding="utf-8"))
    d = doc["data"]
    assert d["archived"] is True
    assert d["mirror_url"] == "git://example/x.git"
    assert d["fork"] is True
    assert d["parent"] == {"owner": "upstream", "name": "widget"}
    assert d["license_spdx_id"] == "GPL-2.0"
    assert d["pull_requests_total"] == 0
    assert d["tree_source"] == "tree-envelope"
    assert d["tree_truncated"] is False
    assert d["tree_paths_le2"] == ["MAINTAINERS", "README.md", "Makefile", "src/a.py",
                                   "Documentation/process/submitting-patches.rst"]
    assert d["offplatform_paths"] == ["Documentation/process/submitting-patches.rst"]
    assert d["maintainers_has_l_lines"] is True
    assert d["maintainers_l_line_count"] == 1
    assert d["readme_off_platform"] is True
    assert d["readme_off_platform_hits"] == ["gerrit"]
    assert d["maintainers_url"].endswith("/HEAD/MAINTAINERS")
    assert d["readme_url"].endswith("/HEAD/README.md")
    assert doc["sha256"] == envelope_hash(d)


def test_collect_shallow_tree_fallback(tmp_path):
    raw = tmp_path / "raw"  # no tree envelope recorded
    recorded_r = {"isArchived": False, "mirrorUrl": None, "isFork": False,
                  "parent": None, "licenseInfo": None,
                  "pullRequests": {"totalCount": 3}}
    trees = {
        "repos/acme/widget/git/trees/main": {"truncated": False, "tree": [
            {"path": "src", "type": "tree", "sha": "s1"},
            {"path": "README.md", "type": "blob"}]},
        "repos/acme/widget/git/trees/s1": {"truncated": False, "tree": [
            {"path": "lib", "type": "tree", "sha": "s2"},
            {"path": "a.py", "type": "blob"}]},
        "repos/acme/widget/git/trees/s2": {"truncated": True, "tree": [
            {"path": "b.py", "type": "blob"}]},
    }
    with patch.object(ro_mod, "_gh_graphql", return_value=(recorded_r, None)), \
         patch.object(ro_mod, "_gh_rest", side_effect=lambda p: trees[p]), \
         patch.object(ro_mod, "_fetch_body", return_value=None):
        status = ro_mod.collect_repo_object(raw, "acme", "widget", "main")
    assert status == "ok"
    d = json.loads((raw / "repo_object" / f"{KEY}.json").read_text(encoding="utf-8"))["data"]
    assert d["tree_source"] == "shallow-fetch"
    assert d["tree_paths_le2"] == ["src", "README.md", "src/lib", "src/a.py", "src/lib/b.py"]
    assert d["tree_truncated"] is True
    assert d["license_spdx_id"] is None
    assert d["parent"] is None
    assert d["fork"] is False
    assert d["pull_requests_total"] == 3
    assert d["maintainers_has_l_lines"] is None
    assert d["readme_off_platform"] is None


def test_collect_repo_not_found_returns_fail(tmp_path):
    with patch.object(ro_mod, "_gh_graphql", return_value=(None, None)):
        status = ro_mod.collect_repo_object(tmp_path / "raw", "acme", "missing", "main")
    assert status == "fail"
    assert not (tmp_path / "raw" / "repo_object").is_dir()


def test_collect_transport_failure_raises(tmp_path):
    with patch.object(ro_mod, "_gh_graphql",
                      side_effect=RuntimeError("gh api graphql rc=1")), \
         pytest.raises(RuntimeError):
        ro_mod.collect_repo_object(tmp_path / "raw", "acme", "widget", "main")


# --- defect B3: a partial GraphQL response is salvaged, not discarded ---------

def test_collect_partial_graphql_salvages_usable_fields(tmp_path, block_live_network):
    # GitHub returns ``data`` AND ``errors`` together whenever some fields are
    # unavailable (a security/admin field the token cannot see) while every
    # other field in the same response is present and correct. The collector
    # must keep the usable fields and record that the response was partial, not
    # throw all of it away as a total failure.
    raw = tmp_path / "raw"
    payload = {
        "data": {"r": {
            "isArchived": False, "mirrorUrl": None, "isFork": False,
            "parent": None, "licenseInfo": {"spdxId": "MIT"},
            "pullRequests": {"totalCount": 7}}},
        "errors": [{"message": "Field 'securityAndAnalysis' requires repo admin"}],
    }
    proc = SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
    with patch.object(ro_mod.subprocess, "run", return_value=proc), \
         patch.object(ro_mod, "_gh_rest", return_value=None), \
         patch.object(ro_mod, "_fetch_body", return_value=None):
        status = ro_mod.collect_repo_object(raw, "acme", "widget", "main")
    assert status == "ok"
    d = json.loads((raw / "repo_object" / f"{KEY}.json").read_text(encoding="utf-8"))["data"]
    assert d["archived"] is False
    assert d["license_spdx_id"] == "MIT"        # the license already in hand is kept
    assert d["pull_requests_total"] == 7
    assert d["graphql_partial"] is True          # the partial case is visible, not silent


def test_gh_graphql_total_failure_still_raises(block_live_network):
    # errors with no usable data at all still raise, exactly as before.
    payload = {"data": {"r": None},
               "errors": [{"message": "Could not resolve to a Repository with the name 'o/n'."}]}
    proc = SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
    with patch.object(ro_mod.subprocess, "run", return_value=proc), \
         pytest.raises(RuntimeError):
        ro_mod._gh_graphql('query { r: repository(owner: "o", name: "n") { name } }')


# --- defect B4: the shallow-tree crawl caps its requests and flags truncation --

def _forever_subtree_rest(calls, fanout):
    """A fake ``_gh_rest`` returning ``fanout`` fresh subtrees for every tree it
    is asked about — a frontier that would grow without bound if uncapped."""
    def fake(path):
        calls.append(path)
        n = len(calls)
        return {"truncated": False, "tree": [
            {"path": f"d{n}_{i}", "type": "tree", "sha": f"sha{n}_{i}"}
            for i in range(fanout)]}
    return fake


def test_shallow_tree_request_count_is_capped(block_live_network):
    calls: list[str] = []
    with patch.object(ro_mod, "_gh_rest", side_effect=_forever_subtree_rest(calls, 5)):
        ro_mod._shallow_tree("acme", "widget", "main")
    assert len(calls) <= ro_mod.MAX_TREE_REQUESTS


def test_shallow_tree_budget_abort_reports_truncated(block_live_network):
    calls: list[str] = []
    with patch.object(ro_mod, "_gh_rest", side_effect=_forever_subtree_rest(calls, 5)):
        _paths, truncated = ro_mod._shallow_tree("acme", "widget", "main")
    assert truncated is True   # a budget-aborted crawl must not look complete


def test_shallow_tree_complete_small_crawl_not_truncated(block_live_network):
    # a small tree that finishes inside the depth cap and the request budget,
    # with no node-level truncation, must still report ``truncated is False``.
    trees = {
        "repos/acme/widget/git/trees/main": {"truncated": False, "tree": [
            {"path": "src", "type": "tree", "sha": "s1"},
            {"path": "README.md", "type": "blob"}]},
        "repos/acme/widget/git/trees/s1": {"truncated": False, "tree": [
            {"path": "a.py", "type": "blob"}]},
    }
    with patch.object(ro_mod, "_gh_rest", side_effect=lambda p: trees[p]):
        paths, truncated = ro_mod._shallow_tree("acme", "widget", "main")
    assert truncated is False
    assert paths == ["src", "README.md", "src/a.py"]


# --- the append-only gather hook ----------------------------------------------

GATHER_DONE = {
    "github_meta": {"stars": 1, "forks": 0, "pushed_at": "2026-01-01T00:00:00Z",
                    "archived": False, "created_at": "2020-01-01T00:00:00Z",
                    "license_spdx": "MIT", "default_branch": "main"},
    "pr_events": [],
    "pr_reviews": {},
    "contributors": [],
    # The per-label gfi collector treats a bare list as legacy and
    # re-fetches (needs_gather); the new shape's metadata-first record is
    # what actually marks it done.
    "gfi": [{"gfi_newcomer_v2": True, "labels_matched": [], "queries": [],
             "truncated": False}],
    "actions_runs": [],
    "tree": {"paths": ["pyproject.toml"], "truncated": False},
    "commits_180d": [],
    "depsdev": None,
    "scorecard": None,
    # Other collectors are tested in their dedicated test modules
    # here; marking them done avoids live check-run/file-body/commits/
    # deps.dev/ecosyste.ms/graphql/gh api calls for "acme/widget" below
    # (Blocker B4 round 2).
    "governance": {}, "commits_365d": [], "dependents": {}, "pr_window": {},
    "releases": {}, "issues_counts": {}, "runs": {},
    # author_history hook is tested in its dedicated test module
    # test here; marking it done avoids a live AuthorWindow gh api call —
    # surfaced by network guard.
    "author_history": {},
}


def seed_gather_done(raw: Path) -> None:
    for collector, data in GATHER_DONE.items():
        write_env(raw, collector, KEY, data)
    p = raw / "downloads" / "pypi__widget.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"fetched_at": "2026-09-11T00:00:00Z", "source": "t",
                             "data": {"monthly": 10}}), encoding="utf-8")


def test_gather_repo_repo_object_status_ok(tmp_path, monkeypatch, block_live_network):
    gather.DATA = tmp_path / "raw"
    seed_gather_done(gather.DATA)
    monkeypatch.setattr(ro_mod, "collect_repo_object", lambda *a, **k: "ok")
    status = gather.gather_repo("acme", "widget", "pypi")
    assert status["repo_object"] == "ok"
    assert status["github_meta"] == "skip"


def test_gather_repo_repo_object_status_skip(tmp_path, monkeypatch, block_live_network):
    gather.DATA = tmp_path / "raw"
    seed_gather_done(gather.DATA)
    write_env(gather.DATA, "repo_object", KEY, ro_data())

    def must_not_fetch(*a, **k):
        raise AssertionError("collector must be skipped when the envelope exists")

    monkeypatch.setattr(ro_mod, "collect_repo_object", must_not_fetch)
    status = gather.gather_repo("acme", "widget", "pypi")
    assert status["repo_object"] == "skip"
