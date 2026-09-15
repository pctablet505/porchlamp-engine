"""Package-level dependents via deps.dev (F3), ecosyste.ms fallback.

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

So the package-level count is the dependents count of the package's
``isDefault`` (latest) version. Fallback when deps.dev has no figure:
:mod:`cvi.collect.ecosystemes` (``dependent_packages_count``).

Repo -> package name mapping: the universe's ``selected_by`` fixes the
ecosystem; the repo manifest is the registry namespace itself, so in the
single-level registries (pypi, crates.io, npm unscoped) the repo name is
the package-name candidate. Past those three (D-MISS-5):

* **go** maps to deps.dev system ``go`` (its canonical id is ``GO``; the
  URL segment stays lowercase, matching this module's pypi/npm/crates.io
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
  repo, the same shape as :func:`cvi.collect.repo_object._fetch_body`).
  Without it, :func:`collect_dependents` returns ``"skip"``: no fetch, no
  envelope.
* **systems** has no package registry at all, ever (D-MISS-5): this is a
  fact about the ecosystem, not a data gap, so :func:`collect_dependents`
  writes a ``not_applicable`` envelope (naming the ``D-MISS-5`` rule)
  rather than skipping -- an auditor can then see the
  repo's 2.1 was assessed, not merely never attempted. No network call is
  made. ``cvi.derive.bundle``'s ``dependents_deduped`` row assigns
  ``Unmeasured(not_applicable)`` for this ecosystem directly (a bundle
  fact recorded at universe entry, per D-MISS-5's guard), independent of
  this envelope.

D-PARAM 2.1 (``02`` section 4; ``03`` section 3): dependents are deduplicated
by root organisation and dependents owned by the repo's own org are
dropped. :func:`dedup_dependents` implements that rule on recorded
package lists; count-only payloads (the shape deps.dev answers with)
carry no per-package data, so the source count stands as the deduped
value and the exclusion is not measurable for them.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as _ET
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from cvi.collect import ecosystemes as _ecosystemes
from cvi.collect.envelope import write_envelope

#: Base URL of the deps.dev v3alpha API.
DEPSDEV_API: str = "https://api.deps.dev/v3alpha"

#: D-MISS-5's not_applicable rule id (``docs/v2/02-RANKING-DESIGN.md``
#: section 5): the only ecosystem this rule names is ``systems``.
NOT_APPLICABLE_RULE: str = "D-MISS-5"

#: CVI ecosystem -> deps.dev system id (the literal URL path segment; the
#: canonical deps.dev ids are uppercase -- ``PYPI``, ``GO``, ``MAVEN`` -- but
#: the v3alpha API accepts these lowercase segments, so this table keeps one
#: casing throughout). Absent = no package registry at
#: all (systems, D-MISS-5); go/maven are present here because deps.dev
#: has a system for both, even though their *package name* still needs
#: :func:`package_name_for_repo`'s extra derivation (module path /
#: ``groupId:artifactId``), not a plain repo-name lookup.
SYSTEM_FOR_ECOSYSTEM: dict[str, str] = {
    "pypi": "pypi",
    "npm": "npm",
    "crates": "crates.io",
    "go": "go",
    "maven": "maven",
}

_USER_AGENT: str = "cvi-research/0.1 (ranking study)"


def system_for_ecosystem(ecosystem: str) -> Optional[str]:
    """Deps.dev system id for a CVI ecosystem, or None when unmapped."""
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
    resolved_group_id = group_id or parent_group_id
    if not resolved_group_id or not artifact_id:
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
    without the ``@``) is the root organisation. Every registry mapped here
    is a single-level namespace, where the package is its own root.
    """
    org = name.split("/", 1)[0][1:] if system == "npm" and name.startswith("@") \
        else name
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
        if own and root == own:
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


def _not_applicable_envelope_data(ecosystem: str) -> dict[str, Any]:
    """D-MISS-5 envelope body for an ecosystem with no package registry.

    Every dependents-shaped field a reader might look for is present and
    ``None`` (never omitted): ``scripts/derive.py``'s dependents block
    only acts on ``deduped_count is not None``, so this body's ``None``
    leaves that block a no-op -- the ``not_applicable`` decision is
    made once, by :mod:`cvi.derive.bundle`'s ``dependents_deduped`` row
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


def collect_dependents(
    raw_dir: Path,
    owner: str,
    name: str,
    ecosystem: str,
    *,
    go_mod_module: Optional[str] = None,
    maven_pom_xml: Optional[str] = None,
) -> str:
    """Fetch the package-level dependents and write one envelope.

    Writes ``raw_dir/dependents/<owner>__<name>.json`` (the envelope
    contract in :mod:`cvi.collect.envelope`) and returns ``"ok"`` on a
    measured count.

    Returns ``"not_applicable"`` (D-MISS-5) for the ``systems``
    ecosystem: no package registry ever exists for it, so an envelope
    recording that fact is written instead -- see
    :func:`_not_applicable_envelope_data` -- with no network call.

    Returns ``"skip"`` when no package identifier can be derived at all
    and no fetch is attempted: maven without ``maven_pom_xml``, or any
    other ecosystem with no known mapping. Nothing is fetched, no
    envelope is written.

    Returns ``"fail"`` when a package identifier exists but no source
    (deps.dev, then the ecosyste.ms fallback) carries a count.

    ``go_mod_module`` / ``maven_pom_xml``: optional content a caller
    already fetched (this function fetches neither) -- respectively the
    root ``go.mod``'s ``module`` line and the root ``pom.xml`` body,
    parsed here via :func:`parse_maven_coordinates`. Passing them is what
    "if contents are available" resolves to; ``scripts/gather.py`` passes
    neither.
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

    maven_coordinates = parse_maven_coordinates(maven_pom_xml) if maven_pom_xml else None
    system = SYSTEM_FOR_ECOSYSTEM.get(ecosystem)
    package = package_name_for_repo(
        owner, name, ecosystem,
        go_mod_module=go_mod_module, maven_coordinates=maven_coordinates,
    )
    if system is None or package is None:
        return "skip"
    identifier_method: Optional[str] = None
    if ecosystem == "go":
        identifier_method = "derived:go_mod_module" if go_mod_module else "derived:module_path_default"
    elif ecosystem == "maven":
        identifier_method = "parsed:pom_xml"

    data: Optional[dict[str, Any]] = None
    source: Optional[str] = None
    version = default_version(_get_json(package_url(system, package)))
    if version is not None:
        record = parse_dependents(
            _get_json(version_dependents_url(system, package, version)))
        if record is not None and record.dependent_count is not None:
            deduped = (dedup_dependents(record.packages, system, owner).count
                       if record.packages is not None else record.dependent_count)
            data = {
                "ecosystem": ecosystem, "package": package, "system": system,
                "version": version,
                "dependent_count": record.dependent_count,
                "direct_dependent_count": record.direct_dependent_count,
                "indirect_dependent_count": record.indirect_dependent_count,
                "deduped_count": deduped,
                "dependents": ([{"system": s, "name": n}
                                for s, n in record.packages]
                               if record.packages is not None else None),
                "source_used": "deps.dev",
            }
            source = version_dependents_url(system, package, version)
    if data is None:
        registry = _ecosystemes.registry_for_ecosystem(ecosystem)
        fallback_url = _ecosystemes.package_url(registry, package) \
            if registry is not None else None
        if fallback_url is not None:
            parsed = _ecosystemes.parse_package(_get_json(fallback_url))
            if parsed is not None:
                data = {
                    "ecosystem": ecosystem, "package": package, "system": system,
                    "version": None,
                    "dependent_count": parsed["dependent_packages_count"],
                    "direct_dependent_count": None,
                    "indirect_dependent_count": None,
                    "deduped_count": parsed["dependent_packages_count"],
                    "dependents": None,
                    "source_used": "ecosyste.ms",
                }
                source = fallback_url
    if data is None:
        return "fail"
    if identifier_method is not None:
        data["identifier_method"] = identifier_method

    key = f"{owner}__{name}"
    write_envelope(raw_dir / "dependents" / f"{key}.json", source=source,
                   data=data, fetched_at=datetime.now(timezone.utc).isoformat())
    return "ok"
