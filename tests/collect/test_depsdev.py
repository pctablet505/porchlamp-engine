"""Package-level dependents via deps.dev, ecosyste.ms fallback.

Subjects:
* ``porchlamp.collect.depsdev`` -- the dependents collector (recorded-response
  oracles; the urllib boundary is mocked, no live network in tests), the
  repo -> package-name mapping, and the root-org dedup / same-org exclusion;
* ``porchlamp.collect.ecosystemes`` -- the fallback parser;
* the append-only hooks at the end of ``scripts/gather.py``
  (``dependents`` status entry) and ``scripts/derive.py``
  (``dependents_count`` from the dependents envelope).

Recorded responses (captured 2026-09-12T07:37-07:43Z, keyless, no auth):

* ``GET https://api.deps.dev/v3alpha/systems/pypi/packages/flask``
  (HTTP 200, 12531 bytes, sha256 653d1d347be1c4c5b10ab09b6d67bdef
  13d06397a1ada6e285d257f8535b48d5): ``packageKey``, ``purl`` and 64
  version entries; **no package-level dependents count field**.
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
  ecosyste.ms database (the name is ``pypi.org``), so the initial fallback
  URL 404'd for pypi/flask; re-measured with corrected registry name.
* ``GET https://packages.ecosyste.ms/api/v1/registries/pypi.org/
  packages/flask`` (HTTP 200, 69132 bytes, sha256 a61b10ac2f8182415ddd
  3982c9411e844ba9bf2a1f32c70a3b2921b2825ed8a0):
  the corrected fallback URL; the package object carries
  ``dependent_packages_count`` (2451 at capture) -- the
  fallback is asserted against this recorded 200; the recorded 404 stays
  as the not-found guard.

The 3.1.3 version is the package's ``isDefault`` version in the recorded
package payload (version 3.1.0 had dependentCount 139).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType


from porchlamp.collect import depsdev as dd
from porchlamp.collect import ecosystemes as ees
from porchlamp.collect.envelope import envelope_hash, read_envelope

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


gather = _load_script("porchlamp_gather_depsdev", "gather.py")
derive = _load_script("porchlamp_derive_depsdev", "derive.py")


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
    dependents figure at package level."""
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
    """The fallback URL 404'd for pypi/flask: ``pypi`` is not a
    registry name (the name is ``pypi.org``); recorded 2026-09-12.
    The not-found payload stays absent."""
    payload = json.loads((DATA / "ecosystemes_flask_not_found.json").read_text())
    assert payload == {"error": "not found"}
    assert ees.parse_package(payload) is None
    assert ees.parse_package(None) is None


def test_recorded_ecosystemes_package_flask_200_exact_values():
    """Corrected fallback URL (registry name ``pypi.org``) answered 200
    for pypi/flask: 2451 dependent packages (LIVE)."""
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
    # go/maven have a deps.dev system (module path / groupId:artifactId
    # is still a separate question -- package_name_for_repo, below).
    assert dd.system_for_ecosystem("go") == "go"
    assert dd.system_for_ecosystem("maven") == "maven"
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


def test_package_name_for_repo_go_default_module_path():
    """go defaults to github.com/<owner>/<name> -- the tree envelope
    carries file paths only, not go.mod contents, so this is the default
    identifier available without a new fetch."""
    assert dd.package_name_for_repo("grpc", "grpc-go", "go") == "github.com/grpc/grpc-go"
    assert dd.package_name_for_repo("a-h", "templ", "go") == "github.com/a-h/templ"


def test_package_name_for_repo_go_mod_module_override():
    """When a caller already has the root go.mod's module line, it wins over the default."""
    assert dd.package_name_for_repo(
        "grpc", "grpc-go", "go", go_mod_module="google.golang.org/grpc",
    ) == "google.golang.org/grpc"


def test_package_name_for_repo_maven_needs_parsed_coordinates():
    """maven has no name-derived default (unlike go) -- a GitHub repo
    name carries no Maven coordinate -- so it is None until the caller
    supplies coordinates parsed from a fetched pom.xml."""
    assert dd.package_name_for_repo("apache", "thrift", "maven") is None
    assert dd.package_name_for_repo(
        "apache", "thrift", "maven", maven_coordinates=("org.apache.thrift", "libthrift"),
    ) == "org.apache.thrift:libthrift"


# --- maven pom.xml groupId:artifactId parsing (stdlib XML) -----------------------

def test_parse_maven_coordinates_own_group_id():
    pom = """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>org.apache.thrift</groupId>
  <artifactId>libthrift</artifactId>
  <version>0.20.0</version>
</project>
"""
    assert dd.parse_maven_coordinates(pom) == ("org.apache.thrift", "libthrift")


def test_parse_maven_coordinates_no_namespace():
    pom = """<project>
  <groupId>com.example</groupId>
  <artifactId>widget</artifactId>
</project>
"""
    assert dd.parse_maven_coordinates(pom) == ("com.example", "widget")


def test_parse_maven_coordinates_parent_group_id_fallback():
    """The common multi-module convention: a child POM declares no groupId
    of its own and inherits its parent's."""
    pom = """<project xmlns="http://maven.apache.org/POM/4.0.0">
  <parent>
    <groupId>com.example.parent</groupId>
    <artifactId>parent-pom</artifactId>
    <version>1.0.0</version>
  </parent>
  <artifactId>widget-core</artifactId>
</project>
"""
    assert dd.parse_maven_coordinates(pom) == ("com.example.parent", "widget-core")


def test_parse_maven_coordinates_own_group_id_wins_over_parent():
    pom = """<project xmlns="http://maven.apache.org/POM/4.0.0">
  <parent><groupId>com.example.parent</groupId><artifactId>parent-pom</artifactId></parent>
  <groupId>com.example.child</groupId>
  <artifactId>widget-core</artifactId>
</project>
"""
    assert dd.parse_maven_coordinates(pom) == ("com.example.child", "widget-core")


def test_parse_maven_coordinates_missing_artifact_id():
    pom = """<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>com.example</groupId>
</project>
"""
    assert dd.parse_maven_coordinates(pom) is None


def test_parse_maven_coordinates_no_group_id_anywhere():
    pom = """<project xmlns="http://maven.apache.org/POM/4.0.0">
  <artifactId>widget</artifactId>
</project>
"""
    assert dd.parse_maven_coordinates(pom) is None


def test_parse_maven_coordinates_malformed_xml_returns_none():
    assert dd.parse_maven_coordinates("<project><groupId>com.example</project>") is None
    assert dd.parse_maven_coordinates("") is None
    assert dd.parse_maven_coordinates("not xml at all") is None


def test_parse_maven_coordinates_non_project_root():
    assert dd.parse_maven_coordinates(
        "<settings><groupId>x</groupId><artifactId>y</artifactId></settings>"
    ) is None


# --- URL building percent-encodes slash/colon-bearing identifiers ------------

def test_package_url_quotes_go_module_path():
    assert dd.package_url("go", "github.com/a-h/templ") == \
        "https://api.deps.dev/v3alpha/systems/go/packages/github.com%2Fa-h%2Ftempl"


def test_package_url_quotes_maven_coordinate():
    assert dd.package_url("maven", "org.apache.thrift:libthrift") == \
        "https://api.deps.dev/v3alpha/systems/maven/packages/org.apache.thrift%3Alibthrift"


def test_package_url_unchanged_for_plain_names():
    """Quoting is a no-op for the already-tested pypi/npm/crates names."""
    assert dd.package_url("pypi", "flask") == \
        "https://api.deps.dev/v3alpha/systems/pypi/packages/flask"


def test_ecosystemes_package_url_quotes_go_module_path():
    assert ees.package_url("proxy.golang.org", "github.com/a-h/templ") == \
        "https://packages.ecosyste.ms/api/v1/registries/proxy.golang.org/packages/github.com%2Fa-h%2Ftempl"


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


def test_collect_dependents_systems_writes_not_applicable_envelope(tmp_path, monkeypatch, block_live_network):
    """systems writes an envelope recording the design rule instead of
    silent "skip" -- but still makes zero network calls (the fact is about
    the ecosystem, never fetched)."""
    def must_not_fetch(url: str):
        raise AssertionError("no registry exists for systems; nothing may be fetched")
    monkeypatch.setattr(dd, "_get_json", must_not_fetch)
    status = dd.collect_dependents(tmp_path, "torvalds", "linux", "systems")
    assert status == "not_applicable"
    env = read_envelope(tmp_path / "dependents" / "torvalds__linux.json")
    assert env.source == "design-rule:D-MISS-5"
    assert env.data == {
        "ecosystem": "systems", "package": None, "system": None,
        "version": None, "dependent_count": None,
        "direct_dependent_count": None, "indirect_dependent_count": None,
        "deduped_count": None, "dependents": None,
        "source_used": "not_applicable", "not_applicable_rule": "D-MISS-5",
    }
    assert env.sha256 == envelope_hash(env.data)


def test_collect_dependents_maven_without_pom_content_is_skip_no_envelope(tmp_path, monkeypatch, block_live_network):
    """maven has no name-derived default identifier; without a
    fetched pom.xml nothing can be mapped, so this stays "skip"."""
    def must_not_fetch(url: str):
        raise AssertionError("no maven coordinates derivable; nothing may be fetched")
    monkeypatch.setattr(dd, "_get_json", must_not_fetch)
    assert dd.collect_dependents(tmp_path, "apache", "thrift", "maven") == "skip"
    assert not (tmp_path / "dependents").is_dir()


def test_collect_dependents_unmapped_ecosystem_is_skip_no_envelope(tmp_path, monkeypatch, block_live_network):
    """Any ecosystem this module has never heard of behaves as "skip",
    no fetch, no envelope."""
    def must_not_fetch(url: str):
        raise AssertionError("no mapping for this ecosystem; nothing may be fetched")
    monkeypatch.setattr(dd, "_get_json", must_not_fetch)
    assert dd.collect_dependents(tmp_path, "acme", "widget", "conda") == "skip"
    assert not (tmp_path / "dependents").is_dir()


def test_collect_dependents_go_default_module_path_writes_envelope(tmp_path, monkeypatch):
    """go's default identifier drives the same primary deps.dev
    fetch pypi/npm/crates use; the envelope records the derivation method."""
    def replay(url: str):
        if url == dd.package_url("go", "github.com/a-h/templ"):
            return {"packageKey": {"system": "GO", "name": "github.com/a-h/templ"},
                    "versions": [{"versionKey": {"version": "v0.3.960"}, "isDefault": True}]}
        if url == dd.version_dependents_url("go", "github.com/a-h/templ", "v0.3.960"):
            return {"dependentCount": 5, "directDependentCount": 4, "indirectDependentCount": 1}
        raise AssertionError(f"unexpected URL: {url}")
    monkeypatch.setattr(dd, "_get_json", replay)
    status = dd.collect_dependents(tmp_path, "a-h", "templ", "go")
    assert status == "ok"
    env = read_envelope(tmp_path / "dependents" / "a-h__templ.json")
    assert env.data == {
        "ecosystem": "go", "package": "github.com/a-h/templ", "system": "go",
        "version": "v0.3.960", "dependent_count": 5,
        "direct_dependent_count": 4, "indirect_dependent_count": 1,
        "deduped_count": 5, "dependents": None, "source_used": "deps.dev",
        "identifier_method": "derived:module_path_default",
    }


def test_collect_dependents_go_mod_module_override_recorded(tmp_path, monkeypatch):
    def replay(url: str):
        if url == dd.package_url("go", "google.golang.org/grpc"):
            return {"versions": [{"versionKey": {"version": "v1.60.0"}, "isDefault": True}]}
        if url == dd.version_dependents_url("go", "google.golang.org/grpc", "v1.60.0"):
            return {"dependentCount": 999}
        raise AssertionError(f"unexpected URL: {url}")
    monkeypatch.setattr(dd, "_get_json", replay)
    status = dd.collect_dependents(tmp_path, "grpc", "grpc-go", "go",
                                   go_mod_module="google.golang.org/grpc")
    assert status == "ok"
    env = read_envelope(tmp_path / "dependents" / "grpc__grpc-go.json")
    assert env.data["package"] == "google.golang.org/grpc"
    assert env.data["identifier_method"] == "derived:go_mod_module"


def test_collect_dependents_maven_with_pom_xml_writes_envelope(tmp_path, monkeypatch):
    """If contents are available, proceed as for Go -- a
    caller-supplied pom.xml body drives the same primary/fallback flow."""
    pom = """<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>org.apache.thrift</groupId>
  <artifactId>libthrift</artifactId>
</project>
"""
    def replay(url: str):
        if url == dd.package_url("maven", "org.apache.thrift:libthrift"):
            return {"versions": [{"versionKey": {"version": "0.20.0"}, "isDefault": True}]}
        if url == dd.version_dependents_url("maven", "org.apache.thrift:libthrift", "0.20.0"):
            return {"dependentCount": 42}
        raise AssertionError(f"unexpected URL: {url}")
    monkeypatch.setattr(dd, "_get_json", replay)
    status = dd.collect_dependents(tmp_path, "apache", "thrift", "maven", maven_pom_xml=pom)
    assert status == "ok"
    env = read_envelope(tmp_path / "dependents" / "apache__thrift.json")
    assert env.data["package"] == "org.apache.thrift:libthrift"
    assert env.data["deduped_count"] == 42
    assert env.data["identifier_method"] == "parsed:pom_xml"


def test_collect_dependents_go_no_count_anywhere_is_fail(tmp_path, monkeypatch):
    """A mapped package deps.dev (and the ecosyste.ms fallback) has no count
    stays "fail", exactly like the existing pypi/npm/crates contract."""
    monkeypatch.setattr(dd, "_get_json", lambda url: None)
    assert dd.collect_dependents(tmp_path, "a-h", "templ", "go") == "fail"
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
    the fallback envelope at the exact corrected URL."""
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
    # Per-label gfi collector metadata-first record marks it done:
    "gfi": [{"gfi_newcomer_v2": True, "labels_matched": [], "queries": [],
             "truncated": False}],
    "actions_runs": [],
    "tree": {"paths": ["pyproject.toml"], "truncated": False},
    "commits_180d": [],
    "depsdev": None,
    "scorecard": None,
    # Marking other collectors done avoids taking live calls when gather_repo runs
    # against pallets/flask:
    "repo_object": {}, "commits_365d": [], "pr_window": {}, "governance": {},
    "releases": {}, "issues_counts": {}, "runs": {},
    "author_history": {},
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


def test_gather_repo_dependents_systems_not_applicable(tmp_path, monkeypatch, block_live_network):
    """The gather hook sees "not_applicable" (and a written
    envelope) for systems, propagated unchanged from collect_dependents --
    zero network calls."""
    gather.DATA = tmp_path / "raw"
    seed_gather_done(gather.DATA)

    def must_not_fetch(*a, **k):
        raise AssertionError("no registry for systems; nothing may be fetched")
    monkeypatch.setattr(dd, "_get_json", must_not_fetch)
    status = gather.gather_repo("pallets", "flask", "systems")
    assert status["dependents"] == "not_applicable"
    assert (gather.DATA / "dependents" / "pallets__flask.json").is_file()


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
        f"raw/dependents/{KEY}.json"


def test_derive_repo_dependents_stays_missing_without_envelope(tmp_path):
    """Over-correction guard: the fallback path is unchanged when no dependents
    envelope exists (usedby_count still None)."""
    raw = tmp_path / "raw"
    write_env(raw, "github_meta", KEY, GATHER_DONE["github_meta"])
    write_env(raw, "depsdev", KEY, {"stars": 1, "usedby_count": None})
    out = run_derive(tmp_path)
    assert out["meta"]["dependents_count"] == 0
    assert out["confidence"]["dependents_count"] == "missing"


def test_derive_repo_dependents_v1_usedby_preserved(tmp_path):
    """Legacy data with a real usedby_count keeps the existing value."""
    raw = tmp_path / "raw"
    write_env(raw, "github_meta", KEY, GATHER_DONE["github_meta"])
    write_env(raw, "depsdev", KEY, {"stars": 1, "usedby_count": 5})
    out = run_derive(tmp_path)
    assert out["meta"]["dependents_count"] == 5
    assert out["confidence"]["dependents_count"] == "verified"
