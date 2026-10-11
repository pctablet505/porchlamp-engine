"""Package-level dependents (F3): ecosyste.ms count, deps.dev to find the package.

The dependents figure lives at the *package* level. The deps.dev
*project* endpoint (``GET /v3/projects/github.com%2F{o}%2F{n}``) carries
no dependents figure at all (F3,
``docs/v2/01-FINDINGS-2026-09-12.md``). Two package-level shapes exist:

* ``GET /v3alpha/systems/{system}/packages/{name}`` -- ``packageKey``,
  ``purl`` and the version list only; **no package-level dependents
  count**.
* ``GET /v3alpha/systems/{system}/packages/{name}/versions/{v}:dependents``
  -- ``{"dependentCount", "directDependentCount",
  "indirectDependentCount"}`` per version, **count-only, no dependent
  package list**.

So deps.dev's package-level count is the dependents count of the package's
``isDefault`` (latest) version, and it is the wrong thing to rank on (owner
ruling 2026-10-10, measured on 161 packages): it counts indirect dependents
and moves with release timing (``reqwest``'s newest release read 0 against
9,368 on ecosyste.ms; ``scipy`` read 71,554 on one version and 37 on the next;
about 4 % of its zeros were false), and deps.dev has no dependents for Go at
all, so Go was on a different scale from the rest. The count is therefore
:mod:`porchlamp.collect.ecosystemes`' ``dependent_packages_count`` (direct
dependents, all versions, one source for every ecosystem), and deps.dev is used
to find which package is the repo's. deps.dev's latest-version count is kept in
the envelope as ``depsdev_latest`` for comparison, and is the count itself only
when ecosyste.ms has no record of the package (404; ``source_used: "deps.dev"``).
A source that did not answer (timeout, 429, 5xx) is never read as "absent": the
repo is retried, so one repo is not counted on two scales by accident.

Repo -> package name mapping: the universe's ``selected_by`` fixes the
ecosystem; the repo manifest is the registry namespace itself, so in the
single-level registries (pypi, crates.io, npm unscoped) the repo name is
the package-name candidate. Past those three (D-MISS-5):

* **go** maps to deps.dev system ``go`` (its canonical id is ``GO``; the
  URL segment stays lowercase, matching this module's pypi/npm/cargo
  convention, which the v3alpha API accepts). Package name = module path,
  default ``github.com/<owner>/<name>``, method
  ``"derived:module_path_default"``. The root ``go.mod``'s ``module``
  line would be more precise for a multi-module or vanity-imported repo,
  but the ``tree`` envelope this codebase writes
  (``scripts/gather.py``'s ``tree`` collector) carries file *paths* only,
  never blob contents, so this module never reads it; a caller that has
  the module line passes it as ``go_mod_module`` and gets method
  ``"derived:go_mod_module"`` instead.
* **maven** has no registry-derivable identifier at all: ``groupId:
  artifactId`` lives inside the root ``pom.xml``, which (same tree-envelope
  limitation) this module cannot read. :func:`package_name_for_repo`
  returns ``None`` for maven unless the caller supplies
  ``maven_coordinates`` (parsed by :func:`parse_maven_coordinates` from a
  ``pom.xml`` body the caller fetched -- one raw-content GET per maven
  repo, the same shape as :func:`porchlamp.collect.repo_object._fetch_body`).
  Without it there is no name guess, and :func:`collect_dependents` goes
  straight to the source-repo map below.
* **systems** has no package registry at all, ever (D-MISS-5): this is a
  fact about the ecosystem, not a data gap, so :func:`collect_dependents`
  writes a ``not_applicable`` envelope (naming the ``D-MISS-5`` rule)
  rather than skipping -- an auditor can then see the
  repo's 2.1 was assessed, not merely never attempted. No network call is
  made. ``porchlamp.derive.bundle``'s ``dependents_deduped`` row assigns
  ``Unmeasured(not_applicable)`` for this ecosystem directly (a bundle
  fact recorded at universe entry, per D-MISS-5's guard), independent of
  this envelope.

Source-repo map (2026-10-10): the repo name is only a guess at the package
name, and a wrong guess is worse than none. ``microsoft/windows-rs`` publishes
the crate ``windows``; ``niieani/hashids.js`` the npm package ``hashids``;
and a repo called ``InvenTree`` has a PyPI namesake, ``inventree``, that is a
different project's Python client. On a 400-repo sample of the 2026-10 run,
only 58 % of the guesses that returned a count were linked to the repo by
deps.dev (31 % definitely were not; the rest could not be told because the
map below is cut at 1,500 versions). So the repo is asked instead:
``GET /projects/github.com%2F{o}%2F{n}:packageversions`` lists the package
versions deps.dev links to it, and the ``SOURCE_REPO`` ones in the repo's own
registry are the candidates. The name guess is one more candidate, accepted
only if the map lists it or, when the map is cut, its own default version
names the repo as its source (``relatedProjects``). The candidate with the
most dependents wins (a scoped fork can republish a repo's package under
another name, and the most-depended-on one is the repo's own); ties go to the
package with more linked versions, then to the lower name. Every candidate
examined is recorded in the envelope. Anyone can point a package's metadata at
a repo, but a rival must out-depend the repo's real package to win, which is
the cost 03 section 3 already treats as expensive. A repo deps.dev links to no
package of its registry (the map answered, HTTP 200 with none, or 404; the
guess, if any, is not linked) gets an envelope with
``source_used: "deps.dev:no_package"`` and no count: an unmeasured
``third_party_gap`` (never a measured zero, and never ``not_applicable``,
which D-MISS-5 reserves for design-level rules), no longer refetched on every
pass. When the map was cut at 1,500 versions (popular repos: a fifth of the
sample) and no visible candidate matched, the envelope says so
(``deps.dev:cut_map_unresolved``) rather than claim the registry has no
package. A lookup that did not answer writes nothing and reports ``"fail"``,
so the next run retries it. The cost of this precision is recall: a real package
whose metadata does not name the repo is not attributed to it.

D-PARAM 2.1 (``02`` section 4; ``03`` section 3): dependents are deduplicated
by root organisation and dependents owned by the repo's own org are
dropped. :func:`dedup_dependents` implements that rule on recorded
package lists; count-only payloads (the shape deps.dev answers with)
carry no per-package data, so the source count stands as the deduped
value and the exclusion is not measurable for them.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as _ET
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from porchlamp.collect import ecosystemes as _ecosystemes
from porchlamp.collect.envelope import write_envelope

#: Base URL of the deps.dev v3alpha API.
DEPSDEV_API: str = "https://api.deps.dev/v3alpha"

#: D-MISS-5's not_applicable rule id (``docs/v2/02-RANKING-DESIGN.md``
#: section 5): the only ecosystem this rule names is ``systems``.
NOT_APPLICABLE_RULE: str = "D-MISS-5"

#: Porchlamp ecosystem -> deps.dev system id (the literal URL path segment; the
#: systems endpoint requires lowercase for pypi/npm/cargo/maven, and accepts
#: "go" as an alias for canonical "GO"). crates.io is ``cargo`` here: the
#: registry's own name (``crates.io``) is a 404 on every deps.dev route, which
#: sent every crate to the ecosyste.ms fallback until 2026-10-10.
SYSTEM_FOR_ECOSYSTEM: dict[str, str] = {
    "pypi": "pypi",
    "npm": "npm",
    "crates": "cargo",
    "go": "go",
    "maven": "maven",
}

#: Porchlamp ecosystem -> deps.dev system id as the ``:packageversions``
#: response spells it (canonical upper case; crates.io is ``CARGO``). Not the
#: lowercase URL segment of :data:`SYSTEM_FOR_ECOSYSTEM`.
MAPPED_SYSTEM_FOR_ECOSYSTEM: dict[str, str] = {
    "pypi": "PYPI",
    "npm": "NPM",
    "crates": "CARGO",
    "go": "GO",
    "maven": "MAVEN",
}

#: The relation a mapped package version must report to the repo. Versions
#: that only list the repo as their issue tracker or homepage are not its
#: package.
MAPPED_RELATION: str = "SOURCE_REPO"

#: Most mapped packages whose dependents are fetched for one repo (two calls
#: each), taken in order of linked versions. Bounds the cost of a monorepo
#: that publishes dozens of packages.
MAPPED_CANDIDATE_LIMIT: int = 5

_USER_AGENT: str = "porchlamp-research/0.1 (ranking study)"


def system_for_ecosystem(ecosystem: str) -> Optional[str]:
    """Deps.dev system id for a Porchlamp ecosystem, or None when unmapped."""
    return SYSTEM_FOR_ECOSYSTEM.get(ecosystem)


def _local_tag(tag: str) -> str:
    """Strip an XML namespace from an ``ElementTree`` tag (``{ns}tag`` -> ``tag``)."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def parse_maven_coordinates(pom_xml: str) -> Optional[tuple[str, str]]:
    """Parse ``(groupId, artifactId)`` from a root ``pom.xml`` body (D-PARAM 2.1).

    ``groupId`` falls back to ``<parent><groupId>`` when the project element
    carries none of its own -- the standard multi-module Maven convention of
    a child POM inheriting its parent's group. Namespace-agnostic (a pom.xml
    is usually declared under the ``http://maven.apache.org/POM/4.0.0``
    default namespace; :func:`_local_tag` strips whichever namespace, if
    any, ``ElementTree`` reports). Returns ``None`` -- never raises -- for
    input that is not well-formed XML, whose root is not a ``<project>``
    element, or that carries no ``artifactId`` or no group id at all: a
    hostile or truncated ``pom.xml`` must not crash the collector.
    """
    try:
        root = _ET.fromstring(pom_xml)
    except _ET.ParseError:
        return None
    if _local_tag(root.tag) != "project":
        return None
    group_id: Optional[str] = None
    artifact_id: Optional[str] = None
    parent_group_id: Optional[str] = None
    for child in root:
        tag = _local_tag(child.tag)
        if tag == "groupId" and child.text and child.text.strip():
            group_id = child.text.strip()
        elif tag == "artifactId" and child.text and child.text.strip():
            artifact_id = child.text.strip()
        elif tag == "parent":
            for grandchild in child:
                if _local_tag(grandchild.tag) == "groupId" and grandchild.text \
                        and grandchild.text.strip():
                    parent_group_id = grandchild.text.strip()
    if group_id in ("${project.parent.groupId}", "${parent.groupId}", "${project.groupId}"):
        resolved_group_id = parent_group_id
    else:
        resolved_group_id = group_id or parent_group_id
    if not resolved_group_id or not artifact_id or resolved_group_id.startswith("${"):
        return None
    return resolved_group_id, artifact_id


def package_name_for_repo(
    owner: str,
    name: str,
    ecosystem: str,
    *,
    go_mod_module: Optional[str] = None,
    maven_coordinates: Optional[tuple[str, str]] = None,
) -> Optional[str]:
    """Candidate registry package name for a repo, or None when unmappable.

    pypi/npm/crates: the registry is the arbiter -- the candidate is only
    the repo name normalised to the registry's case convention (crates:
    lowercase).

    go: the module path. Default ``github.com/<owner>/<name>``
    unless ``go_mod_module`` (the root ``go.mod``'s ``module`` line, which
    this module does not fetch) is given, in which case it is used
    verbatim.

    maven: ``groupId:artifactId``, built only from
    ``maven_coordinates`` (:func:`parse_maven_coordinates`'s result on a
    ``pom.xml`` the caller fetched); ``None``
    otherwise. Unlike go, there is no name-derived default: a GitHub repo
    name carries no Maven coordinate.

    systems: always ``None`` -- D-MISS-5: no package registry exists for
    this class at all (handled upstream of this function, not as a mapping
    gap).
    """
    if ecosystem in ("pypi", "npm", "crates"):
        if ecosystem == "pypi":
            return re.sub(r"[-_.]+", "-", name).lower()
        return name.lower() if ecosystem == "crates" else name
    if ecosystem == "go":
        return go_mod_module if go_mod_module else f"github.com/{owner}/{name}"
    if ecosystem == "maven":
        if maven_coordinates is None:
            return None
        group_id, artifact_id = maven_coordinates
        return f"{group_id}:{artifact_id}"
    return None


def package_url(system: str, package: str) -> str:
    """Deps.dev package endpoint URL.

    ``package`` is percent-encoded: a go module path such as
    ``github.com/owner/name`` contains ``/``, which must not be read as
    extra path segments; a maven ``groupId:artifactId`` contains ``:``,
    which URL parsers can otherwise mistake for a scheme separator when the
    URL is re-parsed downstream. ``quote(..., safe="")`` is a no-op on the
    plain pypi/npm/crates names.
    """
    return f"{DEPSDEV_API}/systems/{system}/packages/{urllib.parse.quote(package, safe='')}"


def version_dependents_url(system: str, package: str, version: str) -> str:
    """Deps.dev per-version dependents endpoint URL (``version`` percent-encoded)."""
    return f"{package_url(system, package)}/versions/{urllib.parse.quote(version, safe='')}:dependents"


def default_version(package_payload: Optional[dict]) -> Optional[str]:
    """Version the package endpoint marks ``isDefault`` (the latest).

    None when the payload is absent, carries no versions, or marks none
    default -- the caller then falls back to the ecosyste.ms source.
    """
    if not isinstance(package_payload, dict):
        return None
    for entry in package_payload.get("versions") or []:
        if isinstance(entry, dict) and entry.get("isDefault"):
            version = (entry.get("versionKey") or {}).get("version")
            if isinstance(version, str) and version:
                return version
    return None


def project_packages_url(owner: str, name: str) -> str:
    """Deps.dev URL listing the package versions linked to a GitHub repo."""
    project = urllib.parse.quote(f"github.com/{owner}/{name}", safe="")
    return f"{DEPSDEV_API}/projects/{project}:packageversions"


def mapped_packages(payload: Optional[dict], ecosystem: str) -> list[tuple[str, int]]:
    """``(package, linked versions)`` the repo's source-repo map lists, best first.

    Only versions of the repo's own registry whose relation to the repo is
    :data:`MAPPED_RELATION` count. Sorted by linked versions, most first, then
    by name, so the order never depends on the response's. Empty when the
    payload is absent or malformed, the ecosystem is unmapped, or the repo
    has no package in its registry.
    """
    system = MAPPED_SYSTEM_FOR_ECOSYSTEM.get(ecosystem)
    if system is None or not isinstance(payload, dict):
        return []
    linked: dict[str, int] = {}
    for entry in payload.get("versions") or []:
        if not isinstance(entry, dict) or entry.get("relationType") != MAPPED_RELATION:
            continue
        key = entry.get("versionKey")
        if not isinstance(key, dict) or key.get("system") != system:
            continue
        package = key.get("name")
        if isinstance(package, str) and package:
            linked[package] = linked.get(package, 0) + 1
    return sorted(linked.items(), key=lambda item: (-item[1], item[0]))


def package_key(ecosystem: str, name: str) -> str:
    """The spelling-independent identity of a registry package name.

    deps.dev's source-repo map spells a package as the repo's metadata wrote
    it, not as the registry's package endpoint does: ``cfg_if`` and ``cfg-if``
    are one crate, ``MathJax`` and ``mathjax`` one npm package. Case never
    distinguishes packages here; crates.io treats ``-`` and ``_`` alike and
    PyPI treats ``-``, ``_`` and ``.`` alike (PEP 503). npm does not: ``a.b``
    and ``a-b`` are different packages.
    """
    key = name.lower()
    if ecosystem == "crates":
        return key.replace("_", "-")
    if ecosystem == "pypi":
        return re.sub(r"[-_.]+", "-", key)
    return key


def _spellings(ecosystem: str, package: str, guessed: Optional[str]) -> list[str]:
    """Spellings of one package to try on the package endpoint, mapped one first.

    The guess is tried too when it names the same package; crates.io's
    separator variants are tried last (the endpoint answers to the registry's
    own spelling, the map to the repo metadata's).
    """
    options = [package]
    if guessed is not None and package_key(ecosystem, guessed) == package_key(ecosystem, package):
        options.append(guessed)
    if ecosystem == "crates":
        options += [package.replace("_", "-"), package.replace("-", "_")]
    if ecosystem == "go":
        options.append(package.lower())
    return list(dict.fromkeys(options))


@dataclass(frozen=True)
class DependentsRecord:
    """One recorded dependents payload.

    ``packages`` is the dependent ``(system, name)`` tuples; None when the
    payload is count-only (the recorded deps.dev shape).
    """

    dependent_count: Optional[int]
    direct_dependent_count: Optional[int]
    indirect_dependent_count: Optional[int]
    packages: Optional[tuple[tuple[str, str], ...]]


def _int_field(payload: dict, field: str) -> Optional[int]:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def parse_dependents(payload: Optional[dict]) -> Optional[DependentsRecord]:
    """Parse a recorded per-version dependents payload.

    None unless the payload carries an integral ``dependentCount``; the
    ``packages`` list (absent in the recorded deps.dev shape) becomes a
    tuple of ``(system, name)`` tuples, dropping entries without a name.
    """
    if not isinstance(payload, dict):
        return None
    count = _int_field(payload, "dependentCount")
    if count is None:
        return None
    packages: Optional[tuple[tuple[str, str], ...]] = None
    raw = payload.get("packages")
    if isinstance(raw, list):
        entries: list[tuple[str, str]] = []
        for entry in raw:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str) \
                    and entry["name"]:
                entries.append((str(entry.get("system") or ""), entry["name"]))
        packages = tuple(entries) if entries else None
    return DependentsRecord(
        dependent_count=count,
        direct_dependent_count=_int_field(payload, "directDependentCount"),
        indirect_dependent_count=_int_field(payload, "indirectDependentCount"),
        packages=packages,
    )


def root_org(system: str, name: str) -> str:
    """Root organisation of a dependent package (D-PARAM 2.1).

    npm namespaces packages by the ``@scope`` prefix; the scope (lowercased,
    without the ``@``) is the root organisation.
    go modules namespace by host/owner/repo; the owner is the root organisation.
    maven coordinates are groupId:artifactId; the groupId is the root organisation.
    Every other registry mapped here is a single-level namespace, where the package
    is its own root.
    """
    sys_lower = system.lower()
    if sys_lower == "npm" and name.startswith("@"):
        org = name.split("/", 1)[0][1:]
    elif sys_lower in ("go", "golang"):
        parts = name.split("/")
        org = parts[1] if len(parts) >= 2 and "." in parts[0] else parts[0]
    elif sys_lower == "maven":
        org = name.split(":", 1)[0]
    else:
        org = name
    return org.lower()


@dataclass(frozen=True)
class DedupedDependents:
    """D-PARAM 2.1 result: distinct root organisations kept.

    ``dropped_same_org``: the dependent package names excluded because
    their root organisation is the repo's own (recorded order kept).
    """

    count: int
    kept_roots: frozenset[str]
    dropped_same_org: tuple[str, ...]


def dedup_dependents(packages: Iterable[tuple[str, str]], system: str,
                     owner: str) -> DedupedDependents:
    """Drop same-org dependents, then count distinct root organisations.

    ``packages``: recorded dependent ``(system, name)`` tuples; ``owner``:
    the repo's GitHub owner organisation. A list of one package per root
    organisation is the anti-fake guarantee of ``03`` section 3
    ("publish many tiny packages depending on yourself").
    """
    own = (owner or "").lower()
    kept: set[str] = set()
    dropped: list[str] = []
    for _, name in packages:
        root = root_org(system, name)
        if own and (root == own or (system.lower() == "maven" and own in root.split("."))):
            dropped.append(name)
        else:
            kept.add(root)
    return DedupedDependents(count=len(kept), kept_roots=frozenset(kept),
                             dropped_same_org=tuple(dropped))


def _get_json(url: str) -> Optional[dict]:
    """One keyless GET; None on any transport or parse failure."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except Exception:  # review-ok: gate-network — keyless source absent -> None
        return None


def _fetch_status(url: str) -> tuple[Optional[int], Optional[dict]]:
    """One keyless GET as ``(http_status, body)``, for callers that must tell
    "the source answered: nothing here" (404) from "no answer".

    ``http_status`` is None when nothing answered (transport error, timeout);
    an HTTP error answer returns its status with no body; a 200 whose body is
    not a JSON object returns ``(200, None)``.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            status = response.status
            try:
                body = json.load(response)
            except ValueError:
                return status, None
            return status, body if isinstance(body, dict) else None
    except urllib.error.HTTPError as err:
        return err.code, None
    except Exception:  # review-ok: gate-network — no answer is reported as None status
        return None, None


def _not_applicable_envelope_data(ecosystem: str) -> dict[str, Any]:
    """D-MISS-5 envelope body for an ecosystem with no package registry.

    Every dependents-shaped field a reader might look for is present and
    ``None`` (never omitted): ``scripts/derive.py``'s dependents block
    only acts on ``deduped_count is not None``, so this body's ``None``
    leaves that block a no-op -- the ``not_applicable`` decision is
    made once, by :mod:`porchlamp.derive.bundle`'s ``dependents_deduped`` row
    reading the bundle's own ``ecosystem`` fact (D-MISS-5's guard: a
    universe-entry fact, not a collector outcome), not by this envelope.
    """
    return {
        "ecosystem": ecosystem, "package": None, "system": None,
        "version": None, "dependent_count": None,
        "direct_dependent_count": None, "indirect_dependent_count": None,
        "deduped_count": None, "dependents": None,
        "source_used": "not_applicable",
        "not_applicable_rule": NOT_APPLICABLE_RULE,
    }


#: ``identifier_method`` recorded when the chosen package is not the name guess
#: (the repo name, a go.mod module or a pom the caller read) but one the
#: source-repo map found.
SOURCE_REPO_MAP_METHOD: str = "deps.dev:source_repo_map"

#: deps.dev answers the source-repo map with at most this many package versions.
#: A response this long may have been cut, so a package missing from it is not
#: proven unlinked and the guess is checked on the package's own version instead.
MAP_VERSION_CAP: int = 1500


def version_url(system: str, package: str, version: str) -> str:
    """Deps.dev URL of one package version (its ``relatedProjects`` name the repos it links)."""
    return f"{package_url(system, package)}/versions/{urllib.parse.quote(version, safe='')}"


def links_repo(version_payload: Optional[dict], owner: str, name: str) -> bool:
    """True when a package version lists the GitHub repo as its ``SOURCE_REPO``.

    Compared case-insensitively: GitHub owner and repo names are, deps.dev's
    project ids keep whatever case the package metadata wrote.
    """
    if not isinstance(version_payload, dict):
        return False
    want = f"github.com/{owner}/{name}".lower()
    for entry in version_payload.get("relatedProjects") or []:
        if not isinstance(entry, dict) or entry.get("relationType") != MAPPED_RELATION:
            continue
        key = entry.get("projectKey")
        if isinstance(key, dict) and isinstance(key.get("id"), str) \
                and key["id"].lower() == want:
            return True
    return False


def _guess_is_linked(system: str, package: str, owner: str, name: str) -> Optional[bool]:
    """Whether the guessed package's default version names the repo as its source.

    True or False when deps.dev answered; None when it did not, so the caller
    retries rather than records "unlinked". A package deps.dev does not have
    (404), or one with no default version, links nothing.
    """
    status, payload = _fetch_status(package_url(system, package))
    if status == 404:
        return False
    if status != 200:
        return None
    version = default_version(payload)
    if version is None:
        return False
    status, version_payload = _fetch_status(version_url(system, package, version))
    if status == 404:
        return False
    if status != 200:
        return None
    return links_repo(version_payload, owner, name)


def _depsdev_latest(system: str, package: str) -> Optional[tuple[str, DependentsRecord]]:
    """deps.dev's dependents of the package's default (latest) version, or None."""
    version = default_version(_get_json(package_url(system, package)))
    if version is None:
        return None
    record = parse_dependents(_get_json(version_dependents_url(system, package, version)))
    if record is None or record.dependent_count is None:
        return None
    return version, record


def _ecosystems_count(ecosystem: str, package: str) -> tuple[str, Optional[int], Optional[str]]:
    """ecosyste.ms ``dependent_packages_count`` of one package as ``(state, count, url)``.

    ``state``: ``"count"`` (it answered with a count, 0 included), ``"absent"``
    (it answered 404 or without a count: no record of the package), or
    ``"no_answer"`` (timeout, 429, 5xx: nothing is known).
    """
    registry = _ecosystemes.registry_for_ecosystem(ecosystem)
    if registry is None:
        return "absent", None, None
    url = _ecosystemes.package_url(registry, package)
    status, payload = _fetch_status(url)
    if status == 404:
        return "absent", None, url
    if status != 200 or payload is None:
        return "no_answer", None, url
    parsed = _ecosystemes.parse_package(payload)
    if parsed is None:
        return "absent", None, url
    return "count", parsed["dependent_packages_count"], url


def _fetch_dependents(
    ecosystem: str, system: str, spellings: Iterable[str], owner: str,
) -> tuple[Optional[tuple[dict[str, Any], str]], bool]:
    """Dependents of one registry package: ecosyste.ms first, deps.dev second.

    ``spellings`` are the names to try for the one package (crates.io answers to
    the hyphenated spelling only). Returns ``(found, settled)``: ``found`` is
    ``(envelope data, source url)`` when a source carries a count (0 is a
    count), None when none does; ``settled`` is False when ecosyste.ms did not
    answer, in which case deps.dev is not asked (its count is on another scale)
    and the caller must retry rather than judge the package.
    """
    names = list(spellings)
    # Every spelling is asked and the one with the most dependents wins: a Go module path is
    # case-sensitive, so ``github.com/AWS/AWS-SDK-GO`` (the spelling the repo's package metadata wrote)
    # is an empty record while ``github.com/aws/aws-sdk-go`` has the dependents. The first spelling
    # that answers is not the package. npm, PyPI and crates.io answer alike to any case, so this
    # costs one more call there only when the spellings differ.
    best: Optional[tuple[str, int, str]] = None
    for package in names:
        state, count, url = _ecosystems_count(ecosystem, package)
        if state == "no_answer":
            return None, False
        if state == "count" and (best is None or count > best[1]):
            best = (package, count, url)
    if best is not None:
        package, count, url = best
        latest = _depsdev_latest(system, package)
        data = {
            "ecosystem": ecosystem, "package": package, "system": system,
            "version": None,
            "dependent_count": count,
            "direct_dependent_count": None,
            "indirect_dependent_count": None,
            "deduped_count": count,
            "dependents": None,
            "source_used": "ecosyste.ms",
            "depsdev_latest": (None if latest is None else {
                "version": latest[0],
                "dependent_count": latest[1].dependent_count,
                "direct_dependent_count": latest[1].direct_dependent_count,
                "indirect_dependent_count": latest[1].indirect_dependent_count,
            }),
        }
        return (data, url), True
    for package in names:
        latest = _depsdev_latest(system, package)
        if latest is None:
            continue
        version, record = latest
        deduped = (dedup_dependents(record.packages, system, owner).count
                   if record.packages is not None else record.dependent_count)
        data = {
            "ecosystem": ecosystem, "package": package, "system": system,
            "version": version,
            "dependent_count": record.dependent_count,
            "direct_dependent_count": record.direct_dependent_count,
            "indirect_dependent_count": record.indirect_dependent_count,
            "deduped_count": deduped,
            "dependents": ([{"system": s, "name": n} for s, n in record.packages]
                           if record.packages is not None else None),
            "source_used": "deps.dev",
        }
        return (data, version_dependents_url(system, package, version)), True
    return None, True


@dataclass(frozen=True)
class PackageResolution:
    """Which registry package belongs to a repo, as :func:`resolve_repo_package` found it.

    ``verdict``: ``"found"`` (``package`` and ``found`` set); ``"no_package"``
    (the map is complete and the registry has no package linked to the repo);
    ``"unresolved"`` (nothing visible matched, but the map was cut at
    :data:`MAP_VERSION_CAP`, so a package may lie beyond what deps.dev
    returned -- not a claim that none exists); or ``"fail"`` (not settled;
    retry). ``found`` is ``(envelope data, source url)`` of the
    chosen package's dependents. ``guessed``: the name guess, if one could be
    made; ``guess_linked``: whether deps.dev links it to the repo (None when
    there was no guess or the verdict is ``"fail"``). ``map_truncated``: the
    source-repo map hit :data:`MAP_VERSION_CAP`.
    """

    verdict: str
    package: Optional[str]
    found: Optional[tuple[dict[str, Any], str]]
    map_status: Optional[int]
    guessed: Optional[str]
    guess_linked: Optional[bool]
    map_truncated: bool


def resolve_repo_package(
    owner: str,
    name: str,
    ecosystem: str,
    *,
    go_mod_module: Optional[str] = None,
    maven_coordinates: Optional[tuple[str, str]] = None,
) -> Optional[PackageResolution]:
    """Find the registry package deps.dev links to a repo, with its dependents.

    None for an ecosystem with no deps.dev system (the caller skips it). The
    name guess (:func:`package_name_for_repo`) is a candidate only: it counts
    when the source-repo map lists it, or, when the map is cut at
    :data:`MAP_VERSION_CAP`, when its own default version names the repo
    (:func:`links_repo`). At most :data:`MAPPED_CANDIDATE_LIMIT` mapped
    packages are examined, plus a linked guess; the one with the most
    dependents wins (module docstring). Nothing here writes.
    """
    system = SYSTEM_FOR_ECOSYSTEM.get(ecosystem)
    if system is None:
        return None
    guessed = package_name_for_repo(
        owner, name, ecosystem,
        go_mod_module=go_mod_module, maven_coordinates=maven_coordinates)
    guess_method: Optional[str] = None
    if ecosystem == "go":
        guess_method = "derived:go_mod_module" if go_mod_module else "derived:module_path_default"
    elif ecosystem == "maven" and guessed is not None:
        guess_method = "parsed:pom_xml"

    map_url = project_packages_url(owner, name)
    map_status, payload = _fetch_status(map_url)
    if map_status not in (200, 404):
        return PackageResolution("fail", None, None, map_status, guessed, None, False)
    truncated = isinstance(payload, dict) \
        and len(payload.get("versions") or []) >= MAP_VERSION_CAP
    merged: dict[str, tuple[str, int]] = {}       # package_key -> (best spelling, linked versions)
    for package, linked in mapped_packages(payload, ecosystem):
        key = package_key(ecosystem, package)
        spelling, seen = merged.get(key, (package, 0))
        merged[key] = (spelling, seen + linked)
    mapped = sorted(merged.values(), key=lambda item: (-item[1], item[0]))

    guess_key = package_key(ecosystem, guessed) if guessed is not None else None
    guess_linked: Optional[bool] = None
    if guessed is not None:
        if guess_key in merged:
            guess_linked = True
        elif truncated:
            guess_linked = _guess_is_linked(system, guessed, owner, name)
            if guess_linked is None:
                return PackageResolution("fail", None, None, map_status, guessed, None, truncated)
        else:
            guess_linked = False

    candidates = mapped[:MAPPED_CANDIDATE_LIMIT]
    if guess_linked and guessed is not None \
            and guess_key not in {package_key(ecosystem, p) for p, _ in candidates}:
        candidates.append((guessed, merged.get(guess_key, (guessed, 0))[1]))
    if not candidates:
        return PackageResolution("unresolved" if truncated else "no_package", None, None,
                                 map_status, guessed, guess_linked, truncated)

    examined: list[dict[str, Any]] = []
    results: list[tuple[tuple[dict[str, Any], str], int, str]] = []
    unsettled = False
    for package, linked in candidates:
        found, settled = _fetch_dependents(
            ecosystem, system, _spellings(ecosystem, package, guessed), owner)
        unsettled = unsettled or not settled
        if found is not None:
            package = found[0]["package"]
        examined.append({"package": package, "linked_versions": linked,
                         "dependent_count": found[0]["dependent_count"] if found else None})
        if found is not None:
            results.append((found, linked, package))
    if unsettled or not results:
        return PackageResolution("fail", None, None, map_status, guessed,
                                 guess_linked, truncated)
    (data, source), _linked, package = min(
        results, key=lambda r: (-r[0][0]["dependent_count"], -r[1], r[2]))
    data["guessed_package"] = guessed
    data["guess_linked"] = guess_linked
    data["source_repo_map"] = map_url
    data["map_truncated"] = truncated
    data["mapped_candidates"] = examined
    same_as_guess = guessed is not None and package_key(ecosystem, package) == guess_key
    method = guess_method if same_as_guess else SOURCE_REPO_MAP_METHOD
    if method is not None:
        data["identifier_method"] = method
    return PackageResolution("found", package, (data, source), map_status, guessed,
                             guess_linked, truncated)


def _no_package_envelope_data(
    ecosystem: str, system: str, resolution: PackageResolution,
) -> dict[str, Any]:
    """Envelope body for a repo with no count: deps.dev links it to no package of
    its registry (``no_package``), or the map was cut and showed none
    (``unresolved``).

    Every dependents-shaped field is present and ``None`` (never omitted), as
    in :func:`_not_applicable_envelope_data`, so ``scripts/derive.py``'s
    dependents block (which acts only on ``deduped_count is not None``) leaves
    the input unmeasured. This is an observation about the registry, not a
    design-level rule: it is never ``not_applicable`` (D-MISS-5).
    """
    return {
        "ecosystem": ecosystem, "package": None, "system": system,
        "version": None, "dependent_count": None,
        "direct_dependent_count": None, "indirect_dependent_count": None,
        "deduped_count": None, "dependents": None,
        "source_used": ("deps.dev:no_package" if resolution.verdict == "no_package"
                        else "deps.dev:cut_map_unresolved"),
        "identifier_method": SOURCE_REPO_MAP_METHOD,
        "guessed_package": resolution.guessed,
        "guess_linked": resolution.guess_linked,
        "source_repo_map_http": resolution.map_status,
        "map_truncated": resolution.map_truncated,
    }


def collect_dependents(
    raw_dir: Path,
    owner: str,
    name: str,
    ecosystem: str,
    *,
    go_mod_module: Optional[str] = None,
    maven_pom_xml: Optional[str] = None,
    resolution: Optional[PackageResolution] = None,
) -> str:
    """Fetch the package-level dependents and write one envelope.

    Writes ``raw_dir/dependents/<owner>__<name>.json`` (the envelope
    contract in :mod:`porchlamp.collect.envelope`) and returns ``"ok"`` on a
    measured count.

    Returns ``"not_applicable"`` (D-MISS-5) for the ``systems``
    ecosystem: no package registry ever exists for it, so an envelope
    recording that fact is written instead -- see
    :func:`_not_applicable_envelope_data` -- with no network call.

    Returns ``"skip"`` for an ecosystem with no known mapping. Nothing is
    fetched, no envelope is written.

    Returns ``"no_package"`` when deps.dev links no package of the repo's
    registry to the repo (:func:`resolve_repo_package`), including when the
    only package of the repo's name belongs to another project. An envelope
    with no count is written, so the repo is not refetched on every pass; the
    input stays unmeasured.

    Returns ``"unresolved"`` when the map was cut at :data:`MAP_VERSION_CAP`
    and nothing visible matched: the same no-count envelope is written
    (``source_used: "deps.dev:cut_map_unresolved"``), but it does not claim the
    registry lacks a package.

    Returns ``"fail"`` when the question was not settled: the source-repo map
    or the guess check got no answer, or no candidate package carried a count.
    Nothing is written; the next run retries.

    ``go_mod_module`` / ``maven_pom_xml``: optional content a caller
    already fetched (this function fetches neither) -- respectively the
    root ``go.mod``'s ``module`` line and the root ``pom.xml`` body,
    parsed here via :func:`parse_maven_coordinates`. They only sharpen the
    name guess. ``resolution``: a result the caller already computed with
    :func:`resolve_repo_package` for this repo (the downloads collector needs
    the same package), so the lookups are not made twice.
    """
    if ecosystem == "systems":
        key = f"{owner}__{name}"
        write_envelope(
            raw_dir / "dependents" / f"{key}.json",
            source=f"design-rule:{NOT_APPLICABLE_RULE}",
            data=_not_applicable_envelope_data(ecosystem),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
        return "not_applicable"

    if resolution is None:
        maven_coordinates = parse_maven_coordinates(maven_pom_xml) if maven_pom_xml else None
        resolution = resolve_repo_package(
            owner, name, ecosystem,
            go_mod_module=go_mod_module, maven_coordinates=maven_coordinates)
    if resolution is None:
        return "skip"

    path = raw_dir / "dependents" / f"{owner}__{name}.json"
    system = SYSTEM_FOR_ECOSYSTEM[ecosystem]
    if resolution.verdict == "found" and resolution.found is not None:
        data, source = resolution.found
        write_envelope(path, source=source, data=data,
                       fetched_at=datetime.now(timezone.utc).isoformat())
        return "ok"
    if resolution.verdict in ("no_package", "unresolved") and resolution.map_status is not None:
        write_envelope(
            path, source=project_packages_url(owner, name),
            data=_no_package_envelope_data(ecosystem, system, resolution),
            fetched_at=datetime.now(timezone.utc).isoformat(),
            http_status=resolution.map_status)
        return resolution.verdict
    return "fail"
