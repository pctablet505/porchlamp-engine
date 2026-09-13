"""W1-2: package-level dependents via deps.dev, ecosyste.ms fallback (F3).

Subjects:
* ``cvi.collect.depsdev`` -- the dependents collector (recorded-response
  oracles; the urllib boundary is mocked, no live network in tests), the
  repo -> package-name mapping, and the D-PARAM 2.1 root-org dedup /
  same-org exclusion;
* ``cvi.collect.ecosystemes`` -- the fallback parser;
* the append-only hooks at the end of ``scripts/gather.py``
  (``dependents`` status entry) and ``scripts/derive.py``
  (``dependents_count`` from the dependents envelope).

Recorded responses (captured 2026-09-12T07:37-07:43Z, keyless, no auth):

* ``GET https://api.deps.dev/v3alpha/systems/pypi/packages/flask``
  (HTTP 200, 12531 bytes, sha256 653d1d347be1c4c5b10ab09b6d67bdef
  13d06397a1ada6e285d257f8535b48d5): ``packageKey``, ``purl`` and 64
  version entries; **no package-level dependents count field** -- the
  measurement the packet asked for.
* ``GET https://api.deps.dev/v3alpha/systems/pypi/packages/flask/
  versions/3.1.3:dependents`` (HTTP 200, 82 bytes, sha256 f4d460aa
  eae1627eb7bedf0906a58191e4e5340aa6a0f36ee5f5478a68a1984d):
  ``{"dependentCount": 12698, "directDependentCount": 8077,
  "indirectDependentCount": 4809}`` -- a count-only payload, no
  dependent package list.
* ``GET https://packages.ecosyste.ms/api/v1/registries/pypi/packages/
  flask`` (HTTP 404, 21 bytes, sha256 51d0a9e3bd9ffc25483790aaffe
  7a3597e642edd040dddb16aafc324e6e922ed):
  ``{"error": "not found"}`` -- ``pypi`` is not a registry name in the
  ecosyste.ms database (the name is ``pypi.org``), so the W1-2 fallback
  URL 404'd for pypi/flask; re-measured 2026-09-12 (packet W1-2f).
* ``GET https://packages.ecosyste.ms/api/v1/registries/pypi.org/
  packages/flask`` (HTTP 200, 69132 bytes, sha256 a61b10ac2f8182415ddd
  3982c9411e844ba9bf2a1f32c70a3b2921b2825ed8a0):
  the corrected fallback URL; the package object carries
  ``dependent_packages_count`` (2451 at capture, packet W1-2f) -- the
  fallback is asserted against this recorded 200; the recorded 404 stays
  as the not-found guard.

The 3.1.3 version is the package's ``isDefault`` version in the recorded
package payload (F3 had measured 3.1.0: dependentCount 139).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType


from cvi.collect import depsdev as dd
from cvi.collect import ecosystemes as ees
from cvi.collect.envelope import envelope_hash, read_envelope

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DATA = HERE / "recorded"
NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)
KEY = "pallets__flask"

FLASK_PACKAGE_SHA = ("653d1d347be1c4c5b10ab09b6d67bdef13d06397a1ada6e285d257f8535b48d5")
FLASK_DEPENDENTS_SHA = ("f4d460aaeae1627eb7bedf0906a58191e4e5340aa6a0f36ee5f5478a68a1984d")
ECOSYSTEMES_404_SHA = ("51d0a9e3bd9ffc25483790aaffe7a3597e642edd040dddb16aafc324e6e922ed")
ECOSYSTEMES_200_SHA = ("a61b10ac2f8182415ddd3982c9411e844ba9bf2a1f32c70a3b2921b2825ed8a0")


def _load_script(modname: str, fname: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(modname, ROOT / "scripts" / fname)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[modname] = module
    spec.loader.exec_module(module)
    return module


gather = _load_script("cvi_gather_w12", "gather.py")
derive = _load_script("cvi_derive_w12", "derive.py")


def write_env(raw: Path, collector: str, key: str, data,
              at: str = "2026-09-11T00:00:00Z") -> None:
    p = raw / collector / f"{key}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"fetched_at": at, "source": "test", "data": data}),
                 encoding="utf-8")


# --- recorded-response oracles -------------------------------------------------

def test_fixture_hashes_match_the_live_capture():
    """The fixture files are the byte-exact recorded responses (R-4)."""
    import hashlib
    for fname, want in (("depsdev_flask_package.json", FLASK_PACKAGE_SHA),
                        ("depsdev_flask_3_1_3_dependents.json", FLASK_DEPENDENTS_SHA),
                        ("ecosystemes_flask_not_found.json", ECOSYSTEMES_404_SHA),
                        ("ecosystemes_flask_package.json", ECOSYSTEMES_200_SHA)):
        got = hashlib.sha256((DATA / fname).read_bytes()).hexdigest()
        assert got == want, fname


def test_recorded_package_endpoint_has_no_package_level_count():
    """Measurement: GET /v3alpha/systems/pypi/packages/flask carries no
    dependents figure at package level (the F3 question, answered)."""
    payload = json.loads((DATA / "depsdev_flask_package.json").read_text())
    assert set(payload) == {"packageKey", "purl", "versions"}
    assert payload["packageKey"] == {"system": "PYPI", "name": "flask"}
    assert payload["purl"] == "pkg:pypi/flask"
    assert len(payload["versions"]) == 64
    default = [v for v in payload["versions"] if v.get("isDefault")]
    assert len(default) == 1
    assert dd.default_version(payload) == "3.1.3"
    assert dd.default_version(None) is None
    assert dd.default_version({"versions": []}) is None


def test_recorded_version_dependents_flask_3_1_3_exact_values():
    """pallets/flask package-level count: 12698 dependents on 3.1.3 (LIVE)."""
    payload = json.loads((DATA / "depsdev_flask_3_1_3_dependents.json").read_text())
    rec = dd.parse_dependents(payload)
    assert rec is not None
    assert rec.dependent_count == 12698
    assert rec.direct_dependent_count == 8077
    assert rec.indirect_dependent_count == 4809
    assert rec.packages is None  # count-only payload, no package list
    assert dd.parse_dependents(None) is None
    assert dd.parse_dependents({}) is None
    assert dd.parse_dependents({"dependentCount": "many"}) is None


def test_recorded_ecosystemes_404_flask_is_absent_source():
    """The W1-2 fallback URL 404'd for pypi/flask: ``pypi`` is not a
    registry name (the name is ``pypi.org``); recorded 2026-09-12,
    re-measured packet W1-2f. The not-found payload stays absent."""
    payload = json.loads((DATA / "ecosystemes_flask_not_found.json").read_text())
    assert payload == {"error": "not found"}
    assert ees.parse_package(payload) is None
    assert ees.parse_package(None) is None


def test_recorded_ecosystemes_package_flask_200_exact_values():
    """Corrected fallback URL (registry name ``pypi.org``) answered 200
    for pypi/flask: 2451 dependent packages (LIVE, packet W1-2f)."""
    payload = json.loads((DATA / "ecosystemes_flask_package.json").read_text())
    assert payload["name"] == "flask"
    assert payload["ecosystem"] == "pypi"
    assert payload["purl"] == "pkg:pypi/flask"
    assert payload["dependent_packages_count"] == 2451
    assert ees.parse_package(payload) == \
        {"name": "flask", "registry": None, "dependent_packages_count": 2451}


def test_ecosystemes_parse_exact_count_values():
    assert ees.parse_package({"name": "pytest", "registry": "pypi",
                              "dependent_packages_count": 7}) == \
        {"name": "pytest", "registry": "pypi", "dependent_packages_count": 7}
    assert ees.parse_package({"dependent_packages_count": 0})["dependent_packages_count"] == 0
    assert ees.parse_package({"dependent_packages_count": None}) is None
    assert ees.parse_package({"dependent_packages_count": 3.5}) is None
    assert ees.parse_package({"dependent_packages_count": -1}) is None


# --- URL / mapping builders -----------------------------------------------------

def test_urls_exact():
    assert dd.package_url("pypi", "flask") == \
        "https://api.deps.dev/v3alpha/systems/pypi/packages/flask"
    assert dd.version_dependents_url("pypi", "flask", "3.1.3") == \
        "https://api.deps.dev/v3alpha/systems/pypi/packages/flask/versions/3.1.3:dependents"
    assert ees.package_url(ees.registry_for_ecosystem("pypi"), "flask") == \
        "https://packages.ecosyste.ms/api/v1/registries/pypi.org/packages/flask"


def test_ecosystem_system_mapping_exact():
    assert dd.system_for_ecosystem("pypi") == "pypi"
    assert dd.system_for_ecosystem("crates") == "crates.io"
    assert dd.system_for_ecosystem("npm") == "npm"
    assert dd.system_for_ecosystem("go") is None        # module paths, not repo names
    assert dd.system_for_ecosystem("maven") is None    # groupId:artifactId unknown
    assert dd.system_for_ecosystem("systems") is None  # no package registry
    assert ees.registry_for_ecosystem("pypi") == "pypi.org"
    assert ees.registry_for_ecosystem("npm") == "npmjs.org"
    assert ees.registry_for_ecosystem("crates") == "crates.io"
    assert ees.registry_for_ecosystem("go") == "proxy.golang.org"
    assert ees.registry_for_ecosystem("maven") == "repo1.maven.org"
    assert ees.registry_for_ecosystem("systems") is None


def test_package_name_for_repo():
    assert dd.package_name_for_repo("pallets", "flask", "pypi") == "flask"
    assert dd.package_name_for_repo("dtolnay", "Anyhow", "crates") == "anyhow"
    assert dd.package_name_for_repo("expressjs", "express", "npm") == "express"
    assert dd.package_name_for_repo("torvalds", "linux", "systems") is None
    assert dd.package_name_for_repo("grpc", "grpc-go", "go") is None
    assert dd.package_name_for_repo("apache", "thrift", "maven") is None


# --- D-PARAM 2.1: root-org dedup + same-org exclusion ----------------------------

def test_root_org_rule():
    assert dd.root_org("npm", "@acme/widget") == "acme"
    assert dd.root_org("npm", "lodash") == "lodash"
    assert dd.root_org("pypi", "flask-sqlalchemy") == "flask-sqlalchemy"


def test_same_org_dependents_excluded_exact_set_difference():
    pkgs = (("npm", "@pallets/evil1"), ("npm", "@pallets/evil2"),
            ("npm", "@acme/x"), ("npm", "@acme/y"),
            ("npm", "lodash"), ("npm", "@express/qs"))
    all_roots = {dd.root_org("npm", n) for _, n in pkgs}
    out = dd.dedup_dependents(pkgs, "npm", "pallets")
    assert out.dropped_same_org == ("@pallets/evil1", "@pallets/evil2")
    assert out.kept_roots == all_roots - {"pallets"}
    assert out.count == 3


def test_same_org_excluded_pypi_identity():
    pkgs = (("pypi", "pallets"), ("pypi", "requests"), ("pypi", "numpy"))
    all_roots = {dd.root_org("pypi", n) for _, n in pkgs}
    out = dd.dedup_dependents(pkgs, "pypi", "pallets")
    assert out.dropped_same_org == ("pallets",)
    assert out.kept_roots == all_roots - {"pallets"}
    assert out.count == 2


def test_dedup_counts_root_orgs_once():
    pkgs = (("npm", "@acme/a"), ("npm", "@acme/b"), ("npm", "@acme/c"), ("npm", "solo"))
    out = dd.dedup_dependents(pkgs, "npm", "owner")
    assert out.count == 2
    assert out.kept_roots == frozenset({"acme", "solo"})
    assert dd.dedup_dependents((), "npm", "owner").count == 0


# --- collect_dependents (urllib boundary mocked) --------------------------------

def _replay(url: str):
    """Recorded payloads keyed by the exact URLs they were captured at."""
    if url == dd.package_url("pypi", "flask"):
        return json.loads((DATA / "depsdev_flask_package.json").read_text())
    if url == dd.version_dependents_url("pypi", "flask", "3.1.3"):
        return json.loads((DATA / "depsdev_flask_3_1_3_dependents.json").read_text())
    return None


def test_collect_dependents_primary_writes_envelope(tmp_path, monkeypatch):
    monkeypatch.setattr(dd, "_get_json", _replay)
    status = dd.collect_dependents(tmp_path, "pallets", "flask", "pypi")
    assert status == "ok"
    env = read_envelope(tmp_path / "dependents" / f"{KEY}.json")
    assert env.source == dd.version_dependents_url("pypi", "flask", "3.1.3")
    assert env.http_status == 200
    assert env.collector_version == "gather/1.0"
    assert env.sha256 == envelope_hash(env.data)
    assert env.data == {
        "ecosystem": "pypi", "package": "flask", "system": "pypi",
        "version": "3.1.3", "dependent_count": 12698,
        "direct_dependent_count": 8077, "indirect_dependent_count": 4809,
        "deduped_count": 12698, "dependents": None,
        "source_used": "deps.dev",
    }


def test_collect_dependents_systems_is_skip_no_envelope(tmp_path, monkeypatch):
    def must_not_fetch(url: str):
        raise AssertionError("no registry exists for systems; nothing may be fetched")
    monkeypatch.setattr(dd, "_get_json", must_not_fetch)
    assert dd.collect_dependents(tmp_path, "torvalds", "linux", "systems") == "skip"
    assert not (tmp_path / "dependents").is_dir()


def test_collect_dependents_fallback_ecosystemes(tmp_path, monkeypatch):
    def replay(url: str):
        # deps.dev package lookup fails; the fallback registry lookup succeeds.
        if url.startswith(dd.DEPSDEV_API):
            return None
        assert url == ees.package_url(ees.registry_for_ecosystem("pypi"), "pytest")
        return {"name": "pytest", "registry": "pypi", "dependent_packages_count": 7}
    monkeypatch.setattr(dd, "_get_json", replay)
    status = dd.collect_dependents(tmp_path, "pytest-dev", "pytest", "pypi")
    assert status == "ok"
    env = read_envelope(tmp_path / "dependents" / "pytest-dev__pytest.json")
    assert env.data["source_used"] == "ecosyste.ms"
    assert env.data["dependent_count"] == 7
    assert env.data["deduped_count"] == 7
    assert env.data["version"] is None
    assert env.data["dependents"] is None

def test_collect_dependents_fallback_recorded_flask_200(tmp_path, monkeypatch):
    """With deps.dev empty, the recorded pypi.org 200 for pypi/flask drives
    the fallback envelope at the exact corrected URL (packet W1-2f)."""
    def replay(url: str):
        if url.startswith(dd.DEPSDEV_API):
            return None
        assert url == "https://packages.ecosyste.ms/api/v1/registries/pypi.org/packages/flask"
        return json.loads((DATA / "ecosystemes_flask_package.json").read_text())
    monkeypatch.setattr(dd, "_get_json", replay)
    status = dd.collect_dependents(tmp_path, "pallets", "flask", "pypi")
    assert status == "ok"
    env = read_envelope(tmp_path / "dependents" / f"{KEY}.json")
    assert env.source == \
        "https://packages.ecosyste.ms/api/v1/registries/pypi.org/packages/flask"
    assert env.data["source_used"] == "ecosyste.ms"
    assert env.data["dependent_count"] == 2451
    assert env.data["deduped_count"] == 2451
    assert env.data["version"] is None
    assert env.data["dependents"] is None


def test_collect_dependents_fail_when_no_source(tmp_path, monkeypatch):
    monkeypatch.setattr(dd, "_get_json", lambda url: None)
    assert dd.collect_dependents(tmp_path, "pallets", "flask", "pypi") == "fail"
    assert not (tmp_path / "dependents").is_dir()


# --- the append-only gather hook --------------------------------------------------

GATHER_DONE = {
    "github_meta": {"stars": 1, "forks": 0, "pushed_at": "2026-01-01T00:00:00Z",
                    "archived": False, "created_at": "2020-01-01T00:00:00Z",
                    "license_spdx": "MIT", "default_branch": "main"},
    "pr_events": [],
    "pr_reviews": {},
    "contributors": [],
    # W1-1's per-label gfi collector treats a bare list as legacy and
    # re-fetches (needs_gather); the new shape's metadata-first record is
    # what actually marks it done.
    "gfi": [{"gfi_newcomer_v2": True, "labels_matched": [], "queries": [],
             "truncated": False}],
    "actions_runs": [],
    "tree": {"paths": ["pyproject.toml"], "truncated": False},
    "commits_180d": [],
    "depsdev": None,
    "scorecard": None,
    # W1-9/W1-5/W1-3/W1-6: these ``gather_repo`` hooks postdate this file and
    # are not under test here; marking them done keeps the real repo
    # "pallets/flask" used below from taking live check-run/file-body calls
    # (repos with a large real PR history make this collector-count matter).
    "repo_object": {}, "commits_365d": [], "pr_window": {}, "governance": {},
    # W1-7 (Blocker B4 round 2): releases/issues_counts/runs also postdate
    # this file; unseeded they made 9 live gh api calls under the
    # block_live_network guard (3 tests x 3 calls each).
    "releases": {}, "issues_counts": {}, "runs": {},
}


def seed_gather_done(raw: Path) -> None:
    for collector, data in GATHER_DONE.items():
        write_env(raw, collector, KEY, data)
    p = raw / "downloads" / "pypi__flask.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"fetched_at": "2026-09-11T00:00:00Z", "source": "t",
                             "data": {"monthly": 10}}), encoding="utf-8")


def test_gather_repo_dependents_status_ok(tmp_path, monkeypatch, block_live_network):
    gather.DATA = tmp_path / "raw"
    seed_gather_done(gather.DATA)
    monkeypatch.setattr(dd, "_get_json", _replay)
    monkeypatch.setattr(gather, "SLEEP_KEYLESS", 0)
    status = gather.gather_repo("pallets", "flask", "pypi")
    assert status["dependents"] == "ok"
    assert (gather.DATA / "dependents" / f"{KEY}.json").is_file()


def test_gather_repo_dependents_status_skip(tmp_path, monkeypatch, block_live_network):
    gather.DATA = tmp_path / "raw"
    seed_gather_done(gather.DATA)
    write_env(gather.DATA, "dependents", KEY,
              {"ecosystem": "pypi", "package": "flask", "system": "pypi",
               "version": "3.1.3", "dependent_count": 12698,
               "direct_dependent_count": 8077, "indirect_dependent_count": 4809,
               "deduped_count": 12698, "dependents": None,
               "source_used": "deps.dev"})

    def must_not_fetch(*a, **k):
        raise AssertionError("collector must be skipped when the envelope exists")
    monkeypatch.setattr(dd, "_get_json", must_not_fetch)
    status = gather.gather_repo("pallets", "flask", "pypi")
    assert status["dependents"] == "skip"


def test_gather_repo_dependents_systems_skip(tmp_path, monkeypatch, block_live_network):
    gather.DATA = tmp_path / "raw"
    seed_gather_done(gather.DATA)

    def must_not_fetch(*a, **k):
        raise AssertionError("no registry for systems; nothing may be fetched")
    monkeypatch.setattr(dd, "_get_json", must_not_fetch)
    status = gather.gather_repo("pallets", "flask", "systems")
    assert status["dependents"] == "skip"
    assert not (gather.DATA / "dependents").is_dir()


# --- the append-only derive hook --------------------------------------------------

def run_derive(tmp: Path, owner: str = "pallets", name: str = "flask",
               ecosystem: str = "pypi") -> dict:
    return derive.derive_repo(owner, name, derive.Ecosystem(ecosystem), tmp,
                              fetch=False, now=NOW)


def test_derive_repo_dependents_from_envelope(tmp_path):
    raw = tmp_path / "raw"
    write_env(raw, "github_meta", KEY, GATHER_DONE["github_meta"])
    write_env(raw, "dependents", KEY,
              {"ecosystem": "pypi", "package": "flask", "system": "pypi",
               "version": "3.1.3", "dependent_count": 12698,
               "direct_dependent_count": 8077, "indirect_dependent_count": 4809,
               "deduped_count": 12698, "dependents": None,
               "source_used": "deps.dev"})
    out = run_derive(tmp_path)
    assert out["meta"]["dependents_count"] == 12698
    assert out["confidence"]["dependents_count"] == "derived"
    assert out["provenance"]["dependents_count"]["source"] == \
        str(raw / "dependents" / f"{KEY}.json")


def test_derive_repo_dependents_stays_missing_without_envelope(tmp_path):
    """Over-correction guard: the v1 path is unchanged when no dependents
    envelope exists (usedby_count still None, exactly as F3 recorded)."""
    raw = tmp_path / "raw"
    write_env(raw, "github_meta", KEY, GATHER_DONE["github_meta"])
    write_env(raw, "depsdev", KEY, {"stars": 1, "usedby_count": None})
    out = run_derive(tmp_path)
    assert out["meta"]["dependents_count"] == 0
    assert out["confidence"]["dependents_count"] == "missing"


def test_derive_repo_dependents_v1_usedby_preserved(tmp_path):
    """Pre-W1-2 data with a real usedby_count keeps the v1 value."""
    raw = tmp_path / "raw"
    write_env(raw, "github_meta", KEY, GATHER_DONE["github_meta"])
    write_env(raw, "depsdev", KEY, {"stars": 1, "usedby_count": 5})
    out = run_derive(tmp_path)
    assert out["meta"]["dependents_count"] == 5
    assert out["confidence"]["dependents_count"] == "verified"
