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

Source-repo map, captured 2026-10-10T05:18Z, keyless, no auth:

* ``GET https://api.deps.dev/v3alpha/projects/github.com%2Fniieani%2F
  hashids.js:packageversions`` (HTTP 200, 17929 bytes, sha256 68a2d8a8
  13532d89 ... in ``HASHIDS_MAP_SHA``): 92 versions of 4 packages. The
  npm package ``hashids`` links the repo as ``SOURCE_REPO`` (20 versions,
  plus 20 as ``ISSUE_TRACKER``); ``@dalongrong/hashids`` once as
  ``SOURCE_REPO``; ``@titanium/hashids`` 47 times, every one only
  ``ISSUE_TRACKER``; 3 Go versions of the repo's own module.
* ``GET https://api.deps.dev/v3alpha/systems/npm/packages/path-to-regexp/
  versions/8.4.2`` (HTTP 200, 1192 bytes, captured 2026-10-10T05:56Z):
  ``relatedProjects`` lists github.com/pillarjs/path-to-regexp twice, as
  ``ISSUE_TRACKER`` and as ``SOURCE_REPO``. The source-repo map of that repo
  was cut at the 1,500-version cap without the package, which is why the
  version payload is the second check.
* ``GET https://api.deps.dev/v3alpha/systems/pypi/packages/inventree/
  versions/0.23.2`` (HTTP 200, 2102 bytes, same capture): ``relatedProjects``
  lists only github.com/inventree/inventree-python -- the PyPI name
  ``inventree`` is that client library, not the InvenTree server repo.

Repository lookup, captured 2026-10-22T15:13Z, keyless, no auth (issue #66):

* ``GET https://packages.ecosyste.ms/api/v1/packages/lookup?repository_url=
  https://github.com/spring-projects/spring-boot&sort=dependent_packages_count
  &order=desc&per_page=5`` (HTTP 200, 1476597 bytes, sha256 2044797c01b514ad
  76505b55eb332fbd33959ea2c20c8b8655488b78c5b51d92): five Maven Central
  packages, most dependents first. ``ecosystemes_lookup_spring_boot_trimmed.json``
  keeps each object's ``name``, ``registry.name``, ``dependent_packages_count``
  and ``repository_url`` (the rest is repository metadata the parser never
  reads). The same day deps.dev's source-repo map of spring-boot was cut at
  1,500 versions (414 Go, 1,086 Maven) with no ``org.springframework`` package
  in it: its five most-linked Maven packages were ``be.appify.prefab:*``, each
  with 0 dependents, and the published 2026-10-21 record measured 0.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Optional


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
HASHIDS_MAP_SHA = ("68a2d8a813532d89ba5e6dbc690879519593df45e8466377c845b5b2e9e19632")
PATH_TO_REGEXP_VERSION_SHA = ("471c213dbcc01dba61e52f2f530f8e38c47f84cee045c540cbd2b4612de22962")
INVENTREE_VERSION_SHA = ("257a78a698343d2ef05ff0b17e28cf1b6e087db27f528a022981d021893baeb9")


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
                        ("ecosystemes_flask_package.json", ECOSYSTEMES_200_SHA),
                        ("depsdev_project_hashidsjs_packageversions.json", HASHIDS_MAP_SHA),
                        ("depsdev_npm_path_to_regexp_8_4_2_version.json", PATH_TO_REGEXP_VERSION_SHA),
                        ("depsdev_pypi_inventree_0_23_2_version.json", INVENTREE_VERSION_SHA)):
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
    assert dd.system_for_ecosystem("crates") == "cargo"
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


def test_url_segment_is_the_lowercased_canonical_system_id():
    """deps.dev answers 404 on every route for a segment that is not its own
    system id in lower case (``crates.io`` for crates, observed 2026-10-10;
    ``cargo/packages/clap`` is 200). The map payloads spell the canonical id,
    so the two tables must agree for every ecosystem that has one."""
    assert set(dd.SYSTEM_FOR_ECOSYSTEM) == set(dd.MAPPED_SYSTEM_FOR_ECOSYSTEM)
    for eco, segment in dd.SYSTEM_FOR_ECOSYSTEM.items():
        assert segment == dd.MAPPED_SYSTEM_FOR_ECOSYSTEM[eco].lower(), eco
    assert dd.package_url("cargo", "clap") == "https://api.deps.dev/v3alpha/systems/cargo/packages/clap"


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

def _entry(system: str, package: str, relation: str = "SOURCE_REPO", n: int = 1) -> list[dict]:
    return [{"versionKey": {"system": system, "name": package, "version": f"1.{i}.0"},
             "relationType": relation} for i in range(n)]


def _map_lists(monkeypatch, system: str, *packages: str) -> None:
    """The status-aware boundary: the source-repo map lists ``packages`` (deps.dev
    system id, upper case) as the repo's SOURCE_REPO packages; every other URL is a 404."""
    payload = {"versions": [e for p in packages for e in _entry(system, p)]}

    def fetch_status(url: str):
        if url.startswith(f"{dd.DEPSDEV_API}/projects/"):
            return 200, payload
        return 404, None
    monkeypatch.setattr(dd, "_fetch_status", fetch_status)


def _replay(url: str):
    """Recorded payloads keyed by the exact URLs they were captured at."""
    if url == dd.package_url("pypi", "flask"):
        return json.loads((DATA / "depsdev_flask_package.json").read_text())
    if url == dd.version_dependents_url("pypi", "flask", "3.1.3"):
        return json.loads((DATA / "depsdev_flask_3_1_3_dependents.json").read_text())
    return None


def test_collect_dependents_primary_writes_envelope(tmp_path, monkeypatch):
    monkeypatch.setattr(dd, "_get_json", _replay)
    _map_lists(monkeypatch, "PYPI", "flask")
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
        "guessed_package": "flask", "guess_linked": True,
        "source_repo_map": dd.project_packages_url("pallets", "flask"),
        "map_truncated": False,
        "mapped_candidates": [
            {"package": "flask", "linked_versions": 1, "dependent_count": 12698}],
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


def test_collect_dependents_maven_without_pom_makes_no_name_guess(tmp_path, monkeypatch, block_live_network):
    """maven has no name-derived default identifier: without a fetched pom.xml
    there is no package to look up by name, so the only call is the
    source-repo map (never a package URL)."""
    asked = _script_net(monkeypatch, "maven", {}, (404, None))
    assert dd.collect_dependents(tmp_path, "apache", "thrift", "maven") == "no_package"
    assert asked == [dd.project_packages_url("apache", "thrift")]


def test_collect_dependents_unmapped_ecosystem_is_skip_no_envelope(tmp_path, monkeypatch, block_live_network):
    """Any ecosystem this module has never heard of behaves as "skip",
    no fetch, no envelope."""
    def must_not_fetch(url: str):
        raise AssertionError("no mapping for this ecosystem; nothing may be fetched")
    monkeypatch.setattr(dd, "_get_json", must_not_fetch)
    monkeypatch.setattr(dd, "_fetch_status", must_not_fetch)
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
    _map_lists(monkeypatch, "GO", "github.com/a-h/templ")
    status = dd.collect_dependents(tmp_path, "a-h", "templ", "go")
    assert status == "ok"
    env = read_envelope(tmp_path / "dependents" / "a-h__templ.json")
    assert env.data == {
        "ecosystem": "go", "package": "github.com/a-h/templ", "system": "go",
        "version": "v0.3.960", "dependent_count": 5,
        "direct_dependent_count": 4, "indirect_dependent_count": 1,
        "deduped_count": 5, "dependents": None, "source_used": "deps.dev",
        "identifier_method": "derived:module_path_default",
        "guessed_package": "github.com/a-h/templ", "guess_linked": True,
        "source_repo_map": dd.project_packages_url("a-h", "templ"),
        "map_truncated": False,
        "mapped_candidates": [{"package": "github.com/a-h/templ",
                               "linked_versions": 1, "dependent_count": 5}],
    }


def test_collect_dependents_go_mod_module_override_recorded(tmp_path, monkeypatch):
    def replay(url: str):
        if url == dd.package_url("go", "google.golang.org/grpc"):
            return {"versions": [{"versionKey": {"version": "v1.60.0"}, "isDefault": True}]}
        if url == dd.version_dependents_url("go", "google.golang.org/grpc", "v1.60.0"):
            return {"dependentCount": 999}
        raise AssertionError(f"unexpected URL: {url}")
    monkeypatch.setattr(dd, "_get_json", replay)
    _map_lists(monkeypatch, "GO", "google.golang.org/grpc")
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
    _map_lists(monkeypatch, "MAVEN", "org.apache.thrift:libthrift")
    status = dd.collect_dependents(tmp_path, "apache", "thrift", "maven", maven_pom_xml=pom)
    assert status == "ok"
    env = read_envelope(tmp_path / "dependents" / "apache__thrift.json")
    assert env.data["package"] == "org.apache.thrift:libthrift"
    assert env.data["deduped_count"] == 42
    assert env.data["identifier_method"] == "parsed:pom_xml"


def test_collect_dependents_go_no_count_anywhere_is_fail(tmp_path, monkeypatch, block_live_network):
    """A mapped package deps.dev (and the ecosyste.ms fallback) has no count
    stays "fail", exactly like the existing pypi/npm/crates contract. The
    source-repo map gets no answer either, so the registry is not shown to
    lack the package."""
    monkeypatch.setattr(dd, "_get_json", lambda url: None)
    monkeypatch.setattr(dd, "_fetch_status", lambda url: (None, None))
    assert dd.collect_dependents(tmp_path, "a-h", "templ", "go") == "fail"
    assert not (tmp_path / "dependents").is_dir()


def test_collect_dependents_ecosystemes_is_the_count(tmp_path, monkeypatch, block_live_network):
    """ecosyste.ms answers, so its count is the count even when deps.dev has
    one; deps.dev's latest-version figure rides along for comparison."""
    payload = {"versions": _entry("PYPI", "pytest")}
    _script_net(monkeypatch, "pypi", {"pytest": 31}, (200, payload), ecosystems={"pytest": 7})
    assert dd.collect_dependents(tmp_path, "pytest-dev", "pytest", "pypi") == "ok"
    env = read_envelope(tmp_path / "dependents" / "pytest-dev__pytest.json")
    assert env.source == ees.package_url("pypi.org", "pytest")
    assert env.data["source_used"] == "ecosyste.ms"
    assert env.data["dependent_count"] == 7 and env.data["deduped_count"] == 7
    assert env.data["version"] is None and env.data["dependents"] is None
    assert env.data["depsdev_latest"] == {"version": "1.0.0", "dependent_count": 31,
                                          "direct_dependent_count": 31, "indirect_dependent_count": 0}
    assert env.data["mapped_candidates"] == [
        {"package": "pytest", "linked_versions": 1, "dependent_count": 7}]
    assert env.sha256 == envelope_hash(env.data)


def test_collect_dependents_recorded_flask_ecosystemes_200(tmp_path, monkeypatch):
    """The recorded pypi.org 200 for pypi/flask is the count, at its exact URL;
    the recorded deps.dev flask figure (12,698, indirect dependents included)
    is kept beside it."""
    flask_url = "https://packages.ecosyste.ms/api/v1/registries/pypi.org/packages/flask"
    recorded = json.loads((DATA / "ecosystemes_flask_package.json").read_text())

    def fetch_status(url: str):
        if url == flask_url:
            return 200, recorded
        if url.startswith(f"{dd.DEPSDEV_API}/projects/"):
            return 200, {"versions": _entry("PYPI", "flask")}
        return 404, None
    monkeypatch.setattr(dd, "_fetch_status", fetch_status)
    monkeypatch.setattr(dd, "_get_json", _replay)
    assert dd.collect_dependents(tmp_path, "pallets", "flask", "pypi") == "ok"
    env = read_envelope(tmp_path / "dependents" / f"{KEY}.json")
    assert env.source == flask_url
    assert env.data["source_used"] == "ecosyste.ms"
    assert env.data["dependent_count"] == 2451 and env.data["deduped_count"] == 2451
    assert env.data["version"] is None and env.data["dependents"] is None
    assert env.data["depsdev_latest"]["dependent_count"] == 12698
    assert env.data["depsdev_latest"]["version"] == "3.1.3"


def test_collect_dependents_depsdev_is_the_count_only_when_ecosystemes_has_no_record(tmp_path, monkeypatch, block_live_network):
    payload = {"versions": _entry("PYPI", "pytest")}
    _script_net(monkeypatch, "pypi", {"pytest": 31}, (200, payload), ecosystems={})
    assert dd.collect_dependents(tmp_path, "pytest-dev", "pytest", "pypi") == "ok"
    env = read_envelope(tmp_path / "dependents" / "pytest-dev__pytest.json")
    assert env.data["source_used"] == "deps.dev" and env.data["dependent_count"] == 31
    assert "depsdev_latest" not in env.data


def test_collect_dependents_ecosystemes_200_without_a_count_is_no_record(tmp_path, monkeypatch, block_live_network):
    payload = {"versions": _entry("PYPI", "pytest")}
    _script_net(monkeypatch, "pypi", {"pytest": 31}, (200, payload), ecosystems={"pytest": "nocount"})
    assert dd.collect_dependents(tmp_path, "pytest-dev", "pytest", "pypi") == "ok"
    env = read_envelope(tmp_path / "dependents" / "pytest-dev__pytest.json")
    assert env.data["source_used"] == "deps.dev" and env.data["dependent_count"] == 31


def test_collect_dependents_ecosystemes_down_is_a_retry_not_a_deps_dev_count(tmp_path, monkeypatch, block_live_network):
    """A throttled or failing ecosyste.ms must not push the repo onto
    deps.dev's scale: nothing is written, the next run asks again."""
    payload = {"versions": _entry("PYPI", "pytest")}
    asked = _script_net(monkeypatch, "pypi", {"pytest": 31}, (200, payload), ecosystems={"pytest": "down"})
    assert dd.collect_dependents(tmp_path, "pytest-dev", "pytest", "pypi") == "fail"
    assert not (tmp_path / "dependents" / "pytest-dev__pytest.json").exists()
    assert dd.version_dependents_url("pypi", "pytest", "1.0.0") not in asked


def test_collect_dependents_one_candidate_unanswered_is_a_retry_not_a_win_for_the_other(tmp_path, monkeypatch, block_live_network):
    """Two packages are linked to the repo; ecosyste.ms answers for one and is
    down for the other. Judging on the one that answered could crown the wrong
    package, so the repo is retried."""
    payload = {"versions": _entry("NPM", "scope-a", n=2) + _entry("NPM", "scope-b", n=1)}
    _script_net(monkeypatch, "npm", {}, (200, payload), ecosystems={"scope-a": 5, "scope-b": "down"})
    assert dd.collect_dependents(tmp_path, "acme", "mono", "npm") == "fail"
    assert not (tmp_path / "dependents" / "acme__mono.json").exists()


def test_collect_dependents_candidates_are_ranked_on_the_ecosystemes_count(tmp_path, monkeypatch, block_live_network):
    """deps.dev would crown ``loud`` (900 against 40); ecosyste.ms, the count we
    rank on, says ``quiet`` (300 against 5)."""
    payload = {"versions": _entry("NPM", "loud", n=2) + _entry("NPM", "quiet", n=1)}
    _script_net(monkeypatch, "npm", {"loud": 900, "quiet": 40}, (200, payload),
                ecosystems={"loud": 5, "quiet": 300})
    assert dd.collect_dependents(tmp_path, "acme", "mono", "npm") == "ok"
    env = read_envelope(tmp_path / "dependents" / "acme__mono.json")
    assert env.data["package"] == "quiet" and env.data["dependent_count"] == 300
    assert env.data["depsdev_latest"]["dependent_count"] == 40


def test_collect_dependents_crate_is_counted_under_the_spelling_ecosystemes_knows(tmp_path, monkeypatch, block_live_network):
    """The map spells the crate ``cfg_if``; ecosyste.ms answers to ``cfg-if`` only."""
    payload = {"versions": _entry("CARGO", "cfg_if", n=3)}
    _script_net(monkeypatch, "cargo", {}, (200, payload), ecosystems={"cfg-if": 1962})
    assert dd.collect_dependents(tmp_path, "rust-lang", "cfg-if", "crates") == "ok"
    env = read_envelope(tmp_path / "dependents" / "rust-lang__cfg-if.json")
    assert env.data["package"] == "cfg-if" and env.data["dependent_count"] == 1962
    assert env.data["source_used"] == "ecosyste.ms" and env.data["depsdev_latest"] is None


def test_collect_dependents_go_module_path_case_is_part_of_the_name(tmp_path, monkeypatch, block_live_network):
    """aws/aws-sdk-go: the package metadata spells the module ``github.com/AWS/AWS-SDK-GO``, an empty
    record on ecosyste.ms; the module with the dependents is ``github.com/aws/aws-sdk-go``. The first
    spelling that answers is not the package: the one with the most dependents is."""
    payload = {"versions": _entry("GO", "github.com/AWS/AWS-SDK-GO", n=4)}
    _script_net(monkeypatch, "go", {}, (200, payload),
                ecosystems={"github.com/AWS/AWS-SDK-GO": 0, "github.com/aws/aws-sdk-go": 12000})
    assert dd.collect_dependents(tmp_path, "aws", "aws-sdk-go", "go") == "ok"
    env = read_envelope(tmp_path / "dependents" / "aws__aws-sdk-go.json")
    assert env.data["package"] == "github.com/aws/aws-sdk-go" and env.data["dependent_count"] == 12000
    assert env.source == ees.package_url("proxy.golang.org", "github.com/aws/aws-sdk-go")


def test_collect_dependents_go_lower_case_module_is_tried_too(tmp_path, monkeypatch, block_live_network):
    """The repo list and the package metadata both write ``Rancher``; the module path is lower case."""
    payload = {"versions": _entry("GO", "github.com/Rancher/rancher")}
    _script_net(monkeypatch, "go", {}, (200, payload),
                ecosystems={"github.com/Rancher/rancher": 0, "github.com/rancher/rancher": 800})
    assert dd.collect_dependents(tmp_path, "Rancher", "rancher", "go") == "ok"
    env = read_envelope(tmp_path / "dependents" / "Rancher__rancher.json")
    assert env.data["package"] == "github.com/rancher/rancher" and env.data["dependent_count"] == 800


def test_collect_dependents_equal_counts_keep_the_mapped_spelling(tmp_path, monkeypatch, block_live_network):
    payload = {"versions": _entry("GO", "github.com/Acme/Tool")}
    _script_net(monkeypatch, "go", {}, (200, payload),
                ecosystems={"github.com/Acme/Tool": 7, "github.com/acme/tool": 7})
    assert dd.collect_dependents(tmp_path, "acme", "tool", "go") == "ok"
    env = read_envelope(tmp_path / "dependents" / "acme__tool.json")
    assert env.data["package"] == "github.com/Acme/Tool" and env.data["dependent_count"] == 7


def test_collect_dependents_one_spelling_unanswered_is_a_retry(tmp_path, monkeypatch, block_live_network):
    """The second spelling may be the one with the dependents, so one that did not answer cannot be skipped."""
    payload = {"versions": _entry("GO", "github.com/AWS/AWS-SDK-GO")}
    _script_net(monkeypatch, "go", {}, (200, payload),
                ecosystems={"github.com/AWS/AWS-SDK-GO": 0, "github.com/aws/aws-sdk-go": "down"})
    assert dd.collect_dependents(tmp_path, "aws", "aws-sdk-go", "go") == "fail"
    assert not (tmp_path / "dependents" / "aws__aws-sdk-go.json").exists()


def test_collect_dependents_depsdev_latest_is_none_when_depsdev_has_nothing(tmp_path, monkeypatch, block_live_network):
    payload = {"versions": _entry("GO", "github.com/acme/tool")}
    _script_net(monkeypatch, "go", {}, (200, payload), ecosystems={"github.com/acme/tool": 12})
    assert dd.collect_dependents(tmp_path, "acme", "tool", "go") == "ok"
    env = read_envelope(tmp_path / "dependents" / "acme__tool.json")
    assert env.data["dependent_count"] == 12 and env.data["depsdev_latest"] is None


def test_ecosystems_count_states(monkeypatch):
    def answer(status, body):
        monkeypatch.setattr(dd, "_fetch_status", lambda url: (status, body))
        return dd._ecosystems_count("pypi", "flask")
    url = ees.package_url("pypi.org", "flask")
    assert answer(200, {"dependent_packages_count": 0}) == ("count", 0, url)
    assert answer(200, {"name": "flask"}) == ("absent", None, url)
    assert answer(404, None) == ("absent", None, url)
    assert answer(200, None) == ("no_answer", None, url)       # not a JSON object
    assert answer(503, None) == ("no_answer", None, url)
    assert answer(429, None) == ("no_answer", None, url)
    assert answer(None, None) == ("no_answer", None, url)      # nothing answered
    assert dd._ecosystems_count("systems", "x") == ("absent", None, None)


def test_collect_dependents_fail_when_no_source(tmp_path, monkeypatch, block_live_network):
    monkeypatch.setattr(dd, "_get_json", lambda url: None)
    monkeypatch.setattr(dd, "_fetch_status", lambda url: (None, None))
    assert dd.collect_dependents(tmp_path, "pallets", "flask", "pypi") == "fail"
    assert not (tmp_path / "dependents").is_dir()


# --- the source-repo map ---------------------------------------------------------

def _recorded(fname: str) -> dict:
    return json.loads((DATA / fname).read_text())


def _hashids_map() -> dict:
    return _recorded("depsdev_project_hashidsjs_packageversions.json")


def _related(repo: str, relation: str = "SOURCE_REPO") -> dict:
    return {"relatedProjects": [{"projectKey": {"id": f"github.com/{repo}"},
                                 "relationType": relation}]}


def _script_net(monkeypatch, system: str, counts: dict[str, int],
                map_answer: tuple[Optional[int], Optional[dict]],
                linked: Optional[dict[str, str]] = None,
                ecosystems: Optional[dict[str, object]] = None,
                lookup: tuple[Optional[int], object] = (404, None)) -> list[str]:
    """A scripted deps.dev: every package in ``counts`` exists with that many
    dependents, the source-repo map answers ``map_answer``, every other
    package is a 404. ``linked`` maps a package to the ``owner/name`` its
    default version names as SOURCE_REPO (the check used when the map is cut).
    ``ecosystems`` scripts ecosyste.ms, the primary count: package -> count,
    ``"down"`` (HTTP 503) or ``"nocount"`` (200 without a count); every other
    package is a 404 there (absent, so deps.dev's figure is the count).
    ``lookup`` is the ecosyste.ms repository lookup's ``(status, body)``
    (asked only on a cut map; default: 404, no packages).
    Returns the list every asked URL is appended to."""
    asked: list[str] = []
    linked = linked or {}
    ecosystems = ecosystems or {}
    registry = ees.registry_for_ecosystem({"cargo": "crates"}.get(system, system))

    def ecosystems_status(url: str):
        for package, answer in ecosystems.items():
            if url == ees.package_url(registry, package):
                if answer == "down":
                    return 503, None
                if answer == "nocount":
                    return 200, {"name": package}
                return 200, {"name": package, "dependent_packages_count": answer}
        return 404, None

    def get_json(url: str):
        asked.append(url)
        for package, n in counts.items():
            if url == dd.package_url(system, package):
                return {"versions": [{"versionKey": {"version": "1.0.0"}, "isDefault": True}]}
            if url == dd.version_dependents_url(system, package, "1.0.0"):
                return {"dependentCount": n, "directDependentCount": n,
                        "indirectDependentCount": 0}
        return None

    def fetch_status(url: str):
        asked.append(url)
        if url.startswith(f"{ees.ECOSYSTEMES_API}/packages/lookup?"):
            return lookup
        if url.startswith(ees.ECOSYSTEMES_API):
            return ecosystems_status(url)
        if url.startswith(f"{dd.DEPSDEV_API}/projects/"):
            return map_answer
        for package in counts:
            if url == dd.package_url(system, package):
                return 200, {"versions": [{"versionKey": {"version": "1.0.0"},
                                           "isDefault": True}]}
            if url == dd.version_url(system, package, "1.0.0"):
                return 200, _related(linked[package]) if package in linked else {}
        return 404, None

    monkeypatch.setattr(dd, "_get_json", get_json)
    monkeypatch.setattr(dd, "_fetch_status", fetch_status)
    return asked


def _capped_map(*entries: dict) -> dict:
    """A source-repo map cut at the 1,500-version cap: filler versions of another
    system, then the given entries."""
    filler = [{"versionKey": {"system": "GO", "name": f"filler{i}", "version": "v1"},
               "relationType": "SOURCE_REPO"} for i in range(dd.MAP_VERSION_CAP - len(entries))]
    return {"versions": filler + list(entries)}


def test_project_packages_url_exact():
    assert dd.project_packages_url("niieani", "hashids.js") == (
        "https://api.deps.dev/v3alpha/projects/github.com%2Fniieani%2Fhashids.js:packageversions")


def test_version_url_exact():
    assert dd.version_url("npm", "@a/b", "1.0.0") == (
        "https://api.deps.dev/v3alpha/systems/npm/packages/%40a%2Fb/versions/1.0.0")


def test_mapped_packages_recorded_hashids_keeps_source_repo_relation_only():
    """Recorded: ``@titanium/hashids`` links the repo in 47 versions, every one
    only as ISSUE_TRACKER, so it is not the repo's package."""
    payload = _hashids_map()
    assert dd.mapped_packages(payload, "npm") == [("hashids", 20), ("@dalongrong/hashids", 1)]
    assert dd.mapped_packages(payload, "go") == [("github.com/niieani/hashids.js", 3)]
    assert dd.mapped_packages(payload, "pypi") == []


def test_mapped_packages_orders_by_linked_versions_then_name():
    payload = {"versions": (_entry("NPM", "b", n=2) + _entry("NPM", "a", n=2)
                            + _entry("NPM", "c", n=3))}
    assert dd.mapped_packages(payload, "npm") == [("c", 3), ("a", 2), ("b", 2)]


def test_mapped_packages_absent_malformed_or_unmapped_is_empty():
    for bad in (None, {}, {"versions": None}, {"versions": ["x", {"versionKey": "y"}, {}]}):
        assert dd.mapped_packages(bad, "npm") == []
    assert dd.mapped_packages({"versions": _entry("NPM", "a")}, "conda") == []
    assert dd.mapped_packages({"versions": _entry("NPM", "")}, "npm") == []


def test_links_repo_recorded_path_to_regexp_names_its_source_repo():
    """Recorded: npm path-to-regexp 8.4.2 lists pillarjs/path-to-regexp as
    SOURCE_REPO (and as ISSUE_TRACKER)."""
    payload = _recorded("depsdev_npm_path_to_regexp_8_4_2_version.json")
    assert dd.links_repo(payload, "pillarjs", "path-to-regexp") is True
    assert dd.links_repo(payload, "PillarJS", "Path-To-Regexp") is True   # case-insensitive
    assert dd.links_repo(payload, "pillarjs", "router") is False


def test_links_repo_recorded_pypi_inventree_is_the_client_not_the_server():
    """Recorded: the PyPI package ``inventree`` names inventree/inventree-python,
    not the InvenTree server repo whose name it shares."""
    payload = _recorded("depsdev_pypi_inventree_0_23_2_version.json")
    assert dd.links_repo(payload, "inventree", "inventree-python") is True
    assert dd.links_repo(payload, "inventree", "InvenTree") is False


def test_links_repo_needs_the_source_repo_relation_and_a_well_formed_payload():
    assert dd.links_repo(_related("a/b", "ISSUE_TRACKER"), "a", "b") is False
    assert dd.links_repo(_related("a/b"), "a", "b") is True
    for bad in (None, {}, {"relatedProjects": None}, {"relatedProjects": ["x", {"projectKey": 3}]},
                {"relatedProjects": [{"projectKey": {"id": 5}, "relationType": "SOURCE_REPO"}]}):
        assert dd.links_repo(bad, "a", "b") is False


def test_collect_dependents_guess_listed_by_the_map_needs_no_version_check(tmp_path, monkeypatch, block_live_network):
    asked = _script_net(monkeypatch, "pypi", {"flask": 12698}, (200, {"versions": _entry("PYPI", "flask")}))
    assert dd.collect_dependents(tmp_path, "pallets", "flask", "pypi") == "ok"
    assert dd.version_url("pypi", "flask", "1.0.0") not in asked
    env = read_envelope(tmp_path / "dependents" / f"{KEY}.json")
    assert env.data["package"] == "flask"
    assert env.data["guess_linked"] is True
    assert "identifier_method" not in env.data


def test_collect_dependents_guess_not_listed_by_a_complete_map_is_not_attributed(tmp_path, monkeypatch, block_live_network):
    """inventree/InvenTree: PyPI ``inventree`` exists and has dependents, but it
    is the Python client of another repo. Its count must not become InvenTree's."""
    asked = _script_net(monkeypatch, "pypi", {"inventree": 77}, (200, {"versions": []}))
    assert dd.collect_dependents(tmp_path, "inventree", "InvenTree", "pypi") == "no_package"
    assert dd.version_dependents_url("pypi", "inventree", "1.0.0") not in asked
    env = read_envelope(tmp_path / "dependents" / "inventree__InvenTree.json")
    assert env.data["deduped_count"] is None and env.data["package"] is None
    assert env.data["guessed_package"] == "inventree" and env.data["guess_linked"] is False


def test_collect_dependents_unlinked_guess_loses_to_the_mapped_package(tmp_path, monkeypatch, block_live_network):
    payload = {"versions": _entry("PYPI", "inventree-server", n=2)}
    _script_net(monkeypatch, "pypi", {"inventree": 77, "inventree-server": 5}, (200, payload))
    assert dd.collect_dependents(tmp_path, "inventree", "InvenTree", "pypi") == "ok"
    env = read_envelope(tmp_path / "dependents" / "inventree__InvenTree.json")
    assert env.data["package"] == "inventree-server"
    assert env.data["dependent_count"] == 5
    assert env.data["guess_linked"] is False
    assert env.data["identifier_method"] == "deps.dev:source_repo_map"
    assert [c["package"] for c in env.data["mapped_candidates"]] == ["inventree-server"]


def test_collect_dependents_cut_map_accepts_a_guess_whose_version_names_the_repo(tmp_path, monkeypatch, block_live_network):
    """pillarjs/path-to-regexp: the map is cut at 1,500 versions and lacks the
    package, but the package's own default version names the repo."""
    asked = _script_net(monkeypatch, "npm", {"path-to-regexp": 500},
                        (200, _capped_map()), linked={"path-to-regexp": "pillarjs/path-to-regexp"})
    assert dd.collect_dependents(tmp_path, "pillarjs", "path-to-regexp", "npm") == "ok"
    assert dd.version_url("npm", "path-to-regexp", "1.0.0") in asked
    env = read_envelope(tmp_path / "dependents" / "pillarjs__path-to-regexp.json")
    assert env.data["package"] == "path-to-regexp"
    assert env.data["guess_linked"] is True and env.data["map_truncated"] is True
    assert env.data["mapped_candidates"] == [
        {"package": "path-to-regexp", "linked_versions": 0, "dependent_count": 500}]


def test_collect_dependents_cut_map_verifies_a_crate_on_the_cargo_segment(tmp_path, monkeypatch, block_live_network):
    """clap-rs/clap: the map is cut and lacks the crate, so the guess is checked
    through the crate's own default version. That route is ``systems/cargo/...``;
    on ``crates.io`` it 404s, the guess reads as not linked, and the flagship
    crate came out unresolved."""
    asked = _script_net(monkeypatch, "cargo", {"clap": 7289}, (200, _capped_map()),
                        linked={"clap": "clap-rs/clap"})
    assert dd.collect_dependents(tmp_path, "clap-rs", "clap", "crates") == "ok"
    assert dd.version_url("cargo", "clap", "1.0.0") in asked
    assert not any("/systems/crates.io/" in url for url in asked)
    env = read_envelope(tmp_path / "dependents" / "clap-rs__clap.json")
    assert env.data["package"] == "clap" and env.data["deduped_count"] == 7289
    assert env.data["guess_linked"] is True and env.data["map_truncated"] is True
    assert env.data["source_used"] == "deps.dev"


def test_collect_dependents_cut_map_rejects_a_guess_that_names_another_repo(tmp_path, monkeypatch, block_live_network):
    """The guess names pillarjs/router, not TanStack/router, so its count is not
    used; and with the map cut, "no package" cannot be claimed either."""
    _script_net(monkeypatch, "npm", {"router": 900}, (200, _capped_map()),
                linked={"router": "pillarjs/router"})
    assert dd.collect_dependents(tmp_path, "TanStack", "router", "npm") == "unresolved"
    env = read_envelope(tmp_path / "dependents" / "TanStack__router.json")
    assert env.data["guess_linked"] is False and env.data["map_truncated"] is True
    assert env.data["source_used"] == "deps.dev:cut_map_unresolved"
    assert env.data["deduped_count"] is None and env.data["package"] is None


def test_collect_dependents_cut_map_with_nothing_visible_is_unresolved_not_no_package(tmp_path, monkeypatch, block_live_network):
    """apache/thrift (maven, no pom coordinates): deps.dev returned 1,500
    versions of other registries' packages and none of Maven Central's. The
    cut map asks the ecosyste.ms repository lookup once (here: 404, none)."""
    asked = _script_net(monkeypatch, "maven", {}, (200, _capped_map()))
    assert dd.collect_dependents(tmp_path, "apache", "thrift", "maven") == "unresolved"
    assert asked == [dd.project_packages_url("apache", "thrift"),
                     ees.repository_lookup_url("apache", "thrift", dd.LOOKUP_CANDIDATE_LIMIT)]
    env = read_envelope(tmp_path / "dependents" / "apache__thrift.json")
    assert env.data["source_used"] == "deps.dev:cut_map_unresolved"
    assert env.data["map_truncated"] is True and env.data["guessed_package"] is None


def test_collect_dependents_cut_map_with_an_unanswered_version_check_is_fail(tmp_path, monkeypatch, block_live_network):
    _script_net(monkeypatch, "npm", {"router": 900}, (200, _capped_map()))
    real = dd._fetch_status

    def flaky(url: str):
        return (503, None) if "/versions/" in url else real(url)
    monkeypatch.setattr(dd, "_fetch_status", flaky)
    assert dd.collect_dependents(tmp_path, "pillarjs", "router", "npm") == "fail"
    assert not (tmp_path / "dependents").is_dir()


def test_collect_dependents_cut_map_with_a_guess_deps_dev_does_not_have_is_unresolved(tmp_path, monkeypatch, block_live_network):
    """A guessed package deps.dev answers 404 for is settled as unlinked, not retried."""
    _script_net(monkeypatch, "npm", {}, (200, _capped_map()))
    assert dd.collect_dependents(tmp_path, "acme", "widget", "npm") == "unresolved"
    assert read_envelope(tmp_path / "dependents" / "acme__widget.json").data["guess_linked"] is False


# --- a cut map: the repository lookup, the npm spelling, no zero from a rival (#66) -----

SPRING = "spring-projects/spring-boot"
PREFAB = "be.appify.prefab:prefab-core"


def _lookup_row(registry: str, package: str, count: object) -> dict:
    return {"name": package, "registry": {"name": registry}, "dependent_packages_count": count}


def test_repository_lookup_url_exact():
    assert ees.repository_lookup_url("apache", "kafka", 3) == (
        "https://packages.ecosyste.ms/api/v1/packages/lookup?repository_url="
        "https%3A%2F%2Fgithub.com%2Fapache%2Fkafka&sort=dependent_packages_count&order=desc&per_page=3")


def test_parse_lookup_recorded_spring_boot_keeps_the_answer_order():
    payload = _recorded("ecosystemes_lookup_spring_boot_trimmed.json")
    assert ees.parse_lookup(payload, "repo1.maven.org") == [
        ("org.springframework.boot:spring-boot-starter-test", 9077),
        ("org.springframework.boot:spring-boot-starter-web", 8658),
        ("org.springframework.boot:spring-boot-configuration-processor", 7252),
        ("org.springframework.boot:spring-boot-autoconfigure", 6483),
        ("org.springframework.boot:spring-boot-starter", 6138),
    ]
    assert ees.parse_lookup(payload, "npmjs.org") == []


def test_parse_lookup_drops_other_registries_and_malformed_rows():
    payload = [_lookup_row("repo1.maven.org", "a:b", 9), _lookup_row("proxy.golang.org", "github.com/x/y", 50),
               {"name": "c:d", "registry": {"name": "repo1.maven.org"}},
               _lookup_row("repo1.maven.org", "e:f", 0), "junk", _lookup_row("repo1.maven.org", "", 3),
               _lookup_row("repo1.maven.org", "g:h", True), {"name": "i:j", "registry": "repo1.maven.org",
                                                            "dependent_packages_count": 4}]
    assert ees.parse_lookup(payload, "repo1.maven.org") == [("a:b", 9), ("e:f", 0)]
    for bad in (None, {"error": "not found"}, "x", 3):
        assert ees.parse_lookup(bad, "repo1.maven.org") == []


def test_collect_dependents_cut_map_rival_zero_loses_to_the_lookups_linked_package(tmp_path, monkeypatch, block_live_network):
    """spring-projects/spring-boot (module docstring of this file): every Maven
    package visible in the cut map is a rival's with 0 dependents and the repo
    has no pom.xml to guess from. The lookup's top package is linked by deps.dev
    and wins on its count."""
    real = "org.springframework.boot:spring-boot-starter-test"
    asked = _script_net(monkeypatch, "maven", {PREFAB: 0, real: 9077},
                        (200, _capped_map(*_entry("MAVEN", PREFAB, n=58))),
                        linked={real: SPRING},
                        lookup=(200, _recorded("ecosystemes_lookup_spring_boot_trimmed.json")[:1]))
    assert dd.collect_dependents(tmp_path, "spring-projects", "spring-boot", "maven") == "ok"
    assert dd.version_url("maven", real, "1.0.0") in asked       # linked through its own version
    env = read_envelope(tmp_path / "dependents" / "spring-projects__spring-boot.json")
    assert env.data["package"] == real and env.data["deduped_count"] == 9077
    assert env.data["mapped_candidates"] == [
        {"package": PREFAB, "linked_versions": 58, "dependent_count": 0},
        {"package": real, "linked_versions": 0, "dependent_count": 9077}]
    assert env.data["identifier_method"] == "deps.dev:source_repo_map"
    assert env.data["map_truncated"] is True and env.data["guessed_package"] is None


def test_collect_dependents_cut_map_zero_without_the_namesake_is_unresolved_not_a_measured_zero(tmp_path, monkeypatch, block_live_network):
    """The same cut map with nothing from the lookup: the rival's 0 is not a
    measurement of spring-boot's package, so 2.1 stays a third-party gap
    instead of a measured zero, and the zero candidates are recorded."""
    _script_net(monkeypatch, "maven", {PREFAB: 0}, (200, _capped_map(*_entry("MAVEN", PREFAB, n=58))),
                lookup=(200, []))
    assert dd.collect_dependents(tmp_path, "spring-projects", "spring-boot", "maven") == "unresolved"
    env = read_envelope(tmp_path / "dependents" / "spring-projects__spring-boot.json")
    assert env.data["deduped_count"] is None and env.data["package"] is None
    assert env.data["source_used"] == "deps.dev:cut_map_unresolved"
    assert env.data["mapped_candidates"] == [{"package": PREFAB, "linked_versions": 58, "dependent_count": 0}]


def test_collect_dependents_cut_map_lookup_with_only_zero_packages_is_unresolved(tmp_path, monkeypatch, block_live_network):
    """apache/kafka (live 2026-10-22): all 1,500 map versions are Go
    pseudo-versions of the repo; the lookup's linked Maven packages are build
    plugins with 0 dependents (``kafka-clients``, 2,034 on ecosyste.ms, names
    no repository there and deps.dev does not link it to apache/kafka)."""
    plugin = "org.apache.kafka:kafka-api-checker-core"
    _script_net(monkeypatch, "maven", {plugin: 0}, (200, _capped_map()), linked={plugin: "apache/kafka"},
                lookup=(200, [_lookup_row("repo1.maven.org", plugin, 0)]))
    assert dd.collect_dependents(tmp_path, "apache", "kafka", "maven") == "unresolved"
    env = read_envelope(tmp_path / "dependents" / "apache__kafka.json")
    assert env.data["mapped_candidates"] == [{"package": plugin, "linked_versions": 0, "dependent_count": 0}]


def test_collect_dependents_cut_map_linked_namesake_with_zero_dependents_stays_a_measured_zero(tmp_path, monkeypatch, block_live_network):
    """comet-ml/opik (live 2026-10-22): the map is cut, and the PyPI namesake
    names the repo as its source with 0 dependents. The package is the repo's
    own, so its 0 is a measurement."""
    _script_net(monkeypatch, "pypi", {"opik": 0}, (200, _capped_map()), linked={"opik": "comet-ml/opik"},
                lookup=(200, []))
    assert dd.collect_dependents(tmp_path, "comet-ml", "opik", "pypi") == "ok"
    env = read_envelope(tmp_path / "dependents" / "comet-ml__opik.json")
    assert env.data["package"] == "opik" and env.data["deduped_count"] == 0
    assert env.data["guess_linked"] is True


def test_collect_dependents_cut_map_npm_guess_is_checked_in_lower_case(tmp_path, monkeypatch, block_live_network):
    """KaTeX/KaTeX (live 2026-10-22): the map is 1,500 Go pseudo-versions, deps.dev
    answers 404 for npm ``KaTeX`` and links ``katex`` (1,186 dependents on
    ecosyste.ms) to the repo."""
    asked = _script_net(monkeypatch, "npm", {"katex": 1186}, (200, _capped_map()),
                        linked={"katex": "KaTeX/KaTeX"})
    assert dd.collect_dependents(tmp_path, "KaTeX", "KaTeX", "npm") == "ok"
    assert dd.package_url("npm", "KaTeX") in asked and dd.version_url("npm", "katex", "1.0.0") in asked
    env = read_envelope(tmp_path / "dependents" / "KaTeX__KaTeX.json")
    assert env.data["package"] == "katex" and env.data["deduped_count"] == 1186
    assert env.data["guessed_package"] == "KaTeX" and env.data["guess_linked"] is True
    assert "identifier_method" not in env.data                  # the namesake, not a map find


def test_collect_dependents_cut_map_lookup_package_deps_dev_does_not_link_is_not_attributed(tmp_path, monkeypatch, block_live_network):
    """flowable/flowable-engine (live 2026-10-22): the lookup names
    ``org.flowable:flowable-engine`` (77 dependents), but its deps.dev default
    version does not name the repo, so the precision rule keeps it out."""
    package = "org.flowable:flowable-engine"
    asked = _script_net(monkeypatch, "maven", {package: 77}, (200, _capped_map()),
                        lookup=(200, [_lookup_row("repo1.maven.org", package, 77)]))
    assert dd.collect_dependents(tmp_path, "flowable", "flowable-engine", "maven") == "unresolved"
    assert dd.version_url("maven", package, "1.0.0") in asked
    assert dd.version_dependents_url("maven", package, "1.0.0") not in asked     # never counted


def test_collect_dependents_cut_map_lookup_package_the_map_lists_needs_no_version_check(tmp_path, monkeypatch, block_live_network):
    """A package the visible map lists as SOURCE_REPO but below the candidate
    limit comes back through the lookup without a second version check, and
    wins on its count."""
    top = [f"org.x:mod{i}" for i in range(dd.MAPPED_CANDIDATE_LIMIT)]
    deep = "org.x:core"
    entries = [e for p in top for e in _entry("MAVEN", p, n=10)] + _entry("MAVEN", deep, n=1)
    asked = _script_net(monkeypatch, "maven", {**dict.fromkeys(top, 1), deep: 500}, (200, _capped_map(*entries)),
                        lookup=(200, [_lookup_row("repo1.maven.org", deep, 500)]))
    assert dd.collect_dependents(tmp_path, "x", "core", "maven") == "ok"
    assert dd.version_url("maven", deep, "1.0.0") not in asked
    env = read_envelope(tmp_path / "dependents" / "x__core.json")
    assert env.data["package"] == deep and env.data["deduped_count"] == 500
    assert env.data["mapped_candidates"][-1] == {"package": deep, "linked_versions": 1, "dependent_count": 500}


def test_collect_dependents_cut_map_unanswered_lookup_is_fail(tmp_path, monkeypatch, block_live_network):
    _script_net(monkeypatch, "maven", {}, (200, _capped_map()), lookup=(503, None))
    assert dd.collect_dependents(tmp_path, "apache", "thrift", "maven") == "fail"
    assert not (tmp_path / "dependents").is_dir()


def test_collect_dependents_complete_map_never_asks_the_lookup(tmp_path, monkeypatch, block_live_network):
    asked = _script_net(monkeypatch, "pypi", {"flask": 12698}, (200, {"versions": _entry("PYPI", "flask")}),
                        lookup=(200, [_lookup_row("pypi.org", "flask-rival", 99999)]))
    assert dd.collect_dependents(tmp_path, "pallets", "flask", "pypi") == "ok"
    assert not any("/packages/lookup?" in url for url in asked)
    assert read_envelope(tmp_path / "dependents" / f"{KEY}.json").data["package"] == "flask"


def test_guess_is_linked_outcomes(monkeypatch):
    """True/False when deps.dev answered, None only when it did not."""
    package = {"versions": [{"versionKey": {"version": "1.0.0"}, "isDefault": True}]}

    def script(package_answer, version_answer):
        def fetch_status(url: str):
            return package_answer if url == dd.package_url("npm", "w") else version_answer
        monkeypatch.setattr(dd, "_fetch_status", fetch_status)

    script((200, package), (200, _related("a/w")))
    assert dd._guess_is_linked("npm", "w", "a", "w") is True
    assert dd._guess_is_linked("npm", "w", "a", "other") is False
    script((200, package), (404, None))
    assert dd._guess_is_linked("npm", "w", "a", "w") is False
    script((200, package), (503, None))
    assert dd._guess_is_linked("npm", "w", "a", "w") is None
    script((200, {"versions": []}), (200, _related("a/w")))
    assert dd._guess_is_linked("npm", "w", "a", "w") is False      # no default version: nothing to verify
    script((404, None), (200, _related("a/w")))
    assert dd._guess_is_linked("npm", "w", "a", "w") is False
    script((502, None), (200, _related("a/w")))
    assert dd._guess_is_linked("npm", "w", "a", "w") is None


def test_collect_dependents_source_repo_map_finds_the_renamed_package(tmp_path, monkeypatch, block_live_network):
    """microsoft/windows-rs publishes the crate ``windows``; the repo name
    ``windows-rs`` is a 404 on deps.dev."""
    payload = {"versions": _entry("CARGO", "windows", n=3)}
    _script_net(monkeypatch, "cargo", {"windows": 4321}, (200, payload))
    assert dd.collect_dependents(tmp_path, "microsoft", "windows-rs", "crates") == "ok"
    env = read_envelope(tmp_path / "dependents" / "microsoft__windows-rs.json")
    assert env.source == dd.version_dependents_url("cargo", "windows", "1.0.0")
    assert env.data["package"] == "windows"
    assert env.data["dependent_count"] == 4321
    assert env.data["deduped_count"] == 4321
    assert env.data["identifier_method"] == "deps.dev:source_repo_map"
    assert env.data["guessed_package"] == "windows-rs" and env.data["guess_linked"] is False
    assert env.data["source_repo_map"] == dd.project_packages_url("microsoft", "windows-rs")
    assert env.data["mapped_candidates"] == [
        {"package": "windows", "linked_versions": 3, "dependent_count": 4321}]
    assert env.sha256 == envelope_hash(env.data)


def test_package_key_is_spelling_independent_only_where_the_registry_says_so():
    key = dd.package_key
    assert key("crates", "cfg_if") == key("crates", "CFG-IF") == "cfg-if"
    assert key("npm", "MathJax") == key("npm", "mathjax")
    assert key("npm", "mermaid.cli") != key("npm", "mermaid-cli")        # npm: "." and "-" differ
    assert key("npm", "a_b") != key("npm", "a-b")
    assert key("pypi", "Foo_Bar.baz") == key("pypi", "foo-bar-baz")      # PEP 503
    assert key("go", "github.com/RANCHER/rancher") == key("go", "github.com/rancher/rancher")
    assert key("maven", "Com.Acme:Core") == key("maven", "com.acme:core")


def test_collect_dependents_crates_underscore_in_the_map_is_the_hyphenated_crate(tmp_path, monkeypatch, block_live_network):
    """rust-lang/cfg-if: the map spells the crate ``cfg_if``, the package
    endpoint answers to ``cfg-if``. Same crate: the guess is linked, and the
    spelling that answers is the one recorded."""
    payload = {"versions": _entry("CARGO", "cfg_if", n=17)}
    _script_net(monkeypatch, "cargo", {"cfg-if": 4000}, (200, payload))
    assert dd.collect_dependents(tmp_path, "rust-lang", "cfg-if", "crates") == "ok"
    env = read_envelope(tmp_path / "dependents" / "rust-lang__cfg-if.json")
    assert env.data["package"] == "cfg-if" and env.data["dependent_count"] == 4000
    assert env.data["guess_linked"] is True
    assert "identifier_method" not in env.data                    # it is the guess, respelled
    assert env.data["mapped_candidates"] == [
        {"package": "cfg-if", "linked_versions": 17, "dependent_count": 4000}]


def test_collect_dependents_npm_case_only_difference_is_the_same_package(tmp_path, monkeypatch, block_live_network):
    """mathjax/MathJax: the repo is called MathJax, the npm package is lowercase."""
    payload = {"versions": _entry("NPM", "mathjax", n=3)}
    _script_net(monkeypatch, "npm", {"mathjax": 70}, (200, payload))
    assert dd.collect_dependents(tmp_path, "mathjax", "MathJax", "npm") == "ok"
    env = read_envelope(tmp_path / "dependents" / "mathjax__MathJax.json")
    assert env.data["package"] == "mathjax" and env.data["guess_linked"] is True
    assert "identifier_method" not in env.data       # a respelled guess is still the guess


def test_collect_dependents_one_package_listed_in_two_spellings_is_one_candidate(tmp_path, monkeypatch, block_live_network):
    payload = {"versions": _entry("CARGO", "foo_bar", n=3) + _entry("CARGO", "foo-bar", n=2)}
    _script_net(monkeypatch, "cargo", {"foo-bar": 9}, (200, payload))
    assert dd.collect_dependents(tmp_path, "acme", "other-name", "crates") == "ok"
    env = read_envelope(tmp_path / "dependents" / "acme__other-name.json")
    assert [c["linked_versions"] for c in env.data["mapped_candidates"]] == [5]
    assert env.data["package"] == "foo-bar"


def test_collect_dependents_npm_dot_is_not_hyphen(tmp_path, monkeypatch, block_live_network):
    """mermaid-js/mermaid-cli: the guess ``mermaid-cli`` is a different npm
    package from the linked ``mermaid.cli`` and must not be treated as linked."""
    payload = {"versions": _entry("NPM", "mermaid.cli", n=2)}
    _script_net(monkeypatch, "npm", {"mermaid-cli": 500, "mermaid.cli": 3}, (200, payload))
    assert dd.collect_dependents(tmp_path, "mermaid-js", "mermaid-cli", "npm") == "ok"
    env = read_envelope(tmp_path / "dependents" / "mermaid-js__mermaid-cli.json")
    assert env.data["package"] == "mermaid.cli" and env.data["guess_linked"] is False


def test_collect_dependents_source_repo_map_prefers_most_dependents_over_most_versions(tmp_path, monkeypatch, block_live_network):
    payload = {"versions": _entry("NPM", "wide", n=9) + _entry("NPM", "popular", n=2)}
    _script_net(monkeypatch, "npm", {"wide": 10, "popular": 900}, (200, payload))
    assert dd.collect_dependents(tmp_path, "acme", "widget", "npm") == "ok"
    env = read_envelope(tmp_path / "dependents" / "acme__widget.json")
    assert env.data["package"] == "popular"
    assert env.data["dependent_count"] == 900
    assert [c["package"] for c in env.data["mapped_candidates"]] == ["wide", "popular"]


def test_collect_dependents_source_repo_map_ties_go_to_more_versions_then_lower_name(tmp_path, monkeypatch, block_live_network):
    payload = {"versions": (_entry("NPM", "few", n=1) + _entry("NPM", "many", n=4)
                            + _entry("NPM", "b-tie", n=4) + _entry("NPM", "a-tie", n=4))}
    _script_net(monkeypatch, "npm", {"few": 7, "many": 7, "b-tie": 7, "a-tie": 7}, (200, payload))
    assert dd.collect_dependents(tmp_path, "acme", "widget", "npm") == "ok"
    assert read_envelope(tmp_path / "dependents" / "acme__widget.json").data["package"] == "a-tie"


def test_collect_dependents_source_repo_map_examines_at_most_the_candidate_limit(tmp_path, monkeypatch, block_live_network):
    names = [f"pkg{i}" for i in range(dd.MAPPED_CANDIDATE_LIMIT + 2)]
    payload = {"versions": [e for i, n in enumerate(names) for e in _entry("NPM", n, n=20 - i)]}
    asked = _script_net(monkeypatch, "npm", {n: 5 for n in names}, (200, payload))
    assert dd.collect_dependents(tmp_path, "acme", "widget", "npm") == "ok"
    env = read_envelope(tmp_path / "dependents" / "acme__widget.json")
    assert [c["package"] for c in env.data["mapped_candidates"]] == names[:dd.MAPPED_CANDIDATE_LIMIT]
    assert dd.package_url("npm", names[-1]) not in asked


def test_collect_dependents_maven_without_pom_uses_the_map(tmp_path, monkeypatch, block_live_network):
    payload = {"versions": _entry("MAVEN", "com.acme:acme-core", n=2)}
    _script_net(monkeypatch, "maven", {"com.acme:acme-core": 33}, (200, payload))
    assert dd.collect_dependents(tmp_path, "acme", "acme-java", "maven") == "ok"
    env = read_envelope(tmp_path / "dependents" / "acme__acme-java.json")
    assert env.data["package"] == "com.acme:acme-core"
    assert env.data["guessed_package"] is None
    assert env.data["identifier_method"] == "deps.dev:source_repo_map"


def test_collect_dependents_no_package_writes_an_envelope_with_no_count(tmp_path, monkeypatch, block_live_network):
    """deps.dev links the repo to no package of its registry: the repo is
    recorded as such (and not refetched every pass), still unmeasured --
    never zero, never not_applicable. A complete map that omits the guess
    settles it with one call."""
    asked = _script_net(monkeypatch, "pypi", {}, (200, {"versions": []}))
    assert dd.collect_dependents(tmp_path, "521xueweihan", "GitHub520", "pypi") == "no_package"
    assert asked == [dd.project_packages_url("521xueweihan", "GitHub520")]
    env = read_envelope(tmp_path / "dependents" / "521xueweihan__GitHub520.json")
    assert env.source == dd.project_packages_url("521xueweihan", "GitHub520")
    assert env.http_status == 200
    assert env.data == {
        "ecosystem": "pypi", "package": None, "system": "pypi", "version": None,
        "dependent_count": None, "direct_dependent_count": None,
        "indirect_dependent_count": None, "deduped_count": None, "dependents": None,
        "source_used": "deps.dev:no_package",
        "identifier_method": "deps.dev:source_repo_map",
        "guessed_package": "github520", "guess_linked": False,
        "source_repo_map_http": 200, "map_truncated": False,
    }
    assert env.sha256 == envelope_hash(env.data)


def test_collect_dependents_no_package_when_deps_dev_does_not_know_the_repo(tmp_path, monkeypatch, block_live_network):
    _script_net(monkeypatch, "npm", {}, (404, None))
    assert dd.collect_dependents(tmp_path, "acme", "widget", "npm") == "no_package"
    env = read_envelope(tmp_path / "dependents" / "acme__widget.json")
    assert env.http_status == 404
    assert env.data["source_repo_map_http"] == 404


def test_collect_dependents_unanswered_map_is_fail_and_writes_nothing(tmp_path, monkeypatch, block_live_network):
    for answer in ((None, None), (500, None), (429, None)):
        _script_net(monkeypatch, "npm", {}, answer)
        assert dd.collect_dependents(tmp_path, "acme", "widget", "npm") == "fail"
        assert not (tmp_path / "dependents").is_dir()


def test_resolve_repo_package_unanswered_map_is_fail_with_no_status(monkeypatch, block_live_network):
    """The resolver's own contract, independent of its caller: a map that did
    not answer is "fail", never "no_package"."""
    _script_net(monkeypatch, "npm", {}, (None, None))
    res = dd.resolve_repo_package("acme", "widget", "npm")
    assert (res.verdict, res.package, res.found, res.map_status) == ("fail", None, None, None)


def test_resolve_repo_package_unmapped_ecosystem_is_none(monkeypatch, block_live_network):
    def must_not_fetch(url: str):
        raise AssertionError("no deps.dev system for this ecosystem; nothing may be fetched")
    monkeypatch.setattr(dd, "_fetch_status", must_not_fetch)
    monkeypatch.setattr(dd, "_get_json", must_not_fetch)
    assert dd.resolve_repo_package("acme", "widget", "conda") is None


def test_collect_dependents_listed_guess_without_a_count_is_fail_not_no_package(tmp_path, monkeypatch, block_live_network):
    """The package is linked to the repo but no source carries its count: the
    registry was NOT shown to lack the package, so the next run retries."""
    _script_net(monkeypatch, "npm", {}, (200, {"versions": _entry("NPM", "widget")}))
    assert dd.collect_dependents(tmp_path, "acme", "widget", "npm") == "fail"
    assert not (tmp_path / "dependents").is_dir()


def test_collect_dependents_mapped_candidates_without_a_count_is_fail(tmp_path, monkeypatch, block_live_network):
    payload = {"versions": _entry("NPM", "ghost", n=3)}
    _script_net(monkeypatch, "npm", {}, (200, payload))
    assert dd.collect_dependents(tmp_path, "acme", "widget", "npm") == "fail"
    assert not (tmp_path / "dependents").is_dir()


def test_collect_dependents_zero_dependents_is_a_measured_count_not_no_package(tmp_path, monkeypatch, block_live_network):
    """A linked package with 0 dependents is a real zero (the registry has the
    package); only a registry with no linked package is "no_package"."""
    _script_net(monkeypatch, "npm", {"lonely": 0}, (200, {"versions": _entry("NPM", "lonely")}))
    assert dd.collect_dependents(tmp_path, "acme", "widget", "npm") == "ok"
    env = read_envelope(tmp_path / "dependents" / "acme__widget.json")
    assert env.data["dependent_count"] == 0 and env.data["deduped_count"] == 0


def test_collect_dependents_reuses_a_resolution_without_any_lookup(tmp_path, monkeypatch, block_live_network):
    """The downloads collector resolves the same package first; the dependents
    collector must write from that result, not look the repo up again."""
    _script_net(monkeypatch, "pypi", {"flask": 12698}, (200, {"versions": _entry("PYPI", "flask")}))
    res = dd.resolve_repo_package("pallets", "flask", "pypi")

    def must_not_fetch(url: str):
        raise AssertionError("resolution already computed; nothing may be fetched")
    monkeypatch.setattr(dd, "_fetch_status", must_not_fetch)
    monkeypatch.setattr(dd, "_get_json", must_not_fetch)
    assert dd.collect_dependents(tmp_path, "pallets", "flask", "pypi", resolution=res) == "ok"
    assert read_envelope(tmp_path / "dependents" / f"{KEY}.json").data["package"] == "flask"


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
    _map_lists(monkeypatch, "PYPI", "flask")
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
               "source_used": "deps.dev", "guess_linked": True})

    def must_not_fetch(*a, **k):
        raise AssertionError("collector must be skipped when the envelope exists")
    monkeypatch.setattr(dd, "_get_json", must_not_fetch)
    status = gather.gather_repo("pallets", "flask", "pypi")
    assert status["dependents"] == "skip"


def test_gather_repo_dependents_status_no_package(tmp_path, monkeypatch, block_live_network):
    """The gather hook propagates "no_package" (the layer counts it as done,
    not as a failure) and the written envelope makes the next run skip it."""
    gather.DATA = tmp_path / "raw"
    seed_gather_done(gather.DATA)
    _script_net(monkeypatch, "pypi", {}, (200, {"versions": []}))
    monkeypatch.setattr(gather, "SLEEP_KEYLESS", 0)
    status = gather.gather_repo("pallets", "flask", "pypi")
    assert status["dependents"] == "no_package"
    assert (gather.DATA / "dependents" / f"{KEY}.json").is_file()
    assert gather.gather_repo("pallets", "flask", "pypi")["dependents"] == "skip"


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


def test_derive_repo_dependents_no_package_envelope_stays_missing(tmp_path):
    """A "no package" envelope carries no count: derive treats it exactly as
    an absent envelope (unmeasured), never as a measured zero."""
    raw = tmp_path / "raw"
    write_env(raw, "github_meta", KEY, GATHER_DONE["github_meta"])
    write_env(raw, "depsdev", KEY, {"stars": 1, "usedby_count": None})
    write_env(raw, "dependents", KEY,
              {"ecosystem": "pypi", "package": None, "system": "pypi", "version": None,
               "dependent_count": None, "direct_dependent_count": None,
               "indirect_dependent_count": None, "deduped_count": None,
               "dependents": None, "source_used": "deps.dev:no_package"})
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
