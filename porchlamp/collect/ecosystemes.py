"""Ecosyste.ms fallback for the package-level dependents count (F3).

Fallback source when deps.dev has no usable figure for a package:
``GET https://packages.ecosyste.ms/api/v1/registries/{registry}/packages/
{package}`` -> ``dependent_packages_count``. ``{registry}`` is the
registry name in the ecosyste.ms database (``pypi.org``, ``npmjs.org``,
``crates.io``, ``proxy.golang.org``, ``repo1.maven.org``), not the
ecosystem slug: an ecosystem slug such as ``pypi`` answers HTTP 404 with
``{"error": "not found"}``. A recorded not-found
payload must be treated as "source absent" (None) and never as a zero.

Keyless and unauthenticated. This module owns only the fallback URL shape
and the response parse, and the same for the repository lookup
(:func:`repository_lookup_url`, :func:`parse_lookup`) that
:mod:`porchlamp.collect.depsdev` asks when deps.dev's source-repo map is cut;
:mod:`porchlamp.collect.depsdev` owns the primary/fallback flow and the envelope.
"""

from __future__ import annotations

import urllib.parse
from typing import Optional

#: Base URL of the public ecosyste.ms REST API.
ECOSYSTEMES_API: str = "https://packages.ecosyste.ms/api/v1"

#: Porchlamp ecosystem -> ecosyste.ms registry name (the value the Packages API
#: routes on, e.g. ``pypi.org``; the ecosystem slug ``pypi`` is not a
#: registry name and answers 404). Absent = no fallback registry (the
#: systems ecosystem has no package registry at all).
REGISTRY_FOR_ECOSYSTEM: dict[str, str] = {
    "pypi": "pypi.org",
    "npm": "npmjs.org",
    "crates": "crates.io",
    "go": "proxy.golang.org",
    "maven": "repo1.maven.org",
}


def registry_for_ecosystem(ecosystem: str) -> Optional[str]:
    """Ecosyste.ms registry name for a Porchlamp ecosystem, or None."""
    return REGISTRY_FOR_ECOSYSTEM.get(ecosystem)


def package_url(registry: str, package: str) -> str:
    """The fallback package-lookup URL for one registry package.

    ``package`` is percent-encoded: a go module path
    (``github.com/owner/name``) or a maven ``groupId:artifactId`` is not a
    single path segment otherwise. A no-op on the plain pypi/npm/crates
    names this function already covers (``quote(..., safe="")`` never
    touches letters, digits or ``_.-~``).
    """
    return f"{ECOSYSTEMES_API}/registries/{registry}/packages/{urllib.parse.quote(package, safe='')}"


def parse_package(payload: Optional[dict]) -> Optional[dict]:
    """Extract the fallback fields from a recorded package response.

    Returns ``{"name", "registry", "dependent_packages_count"}`` when the
    payload carries an integral ``dependent_packages_count`` (0 is a real
    count); None for a missing payload, a recorded 404 body such as
    ``{"error": "not found"}``, or a non-integral count.
    """
    if not isinstance(payload, dict):
        return None
    count = payload.get("dependent_packages_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return None
    return {
        "name": payload.get("name"),
        "registry": payload.get("registry"),
        "dependent_packages_count": count,
    }


def repository_lookup_url(owner: str, name: str, limit: int) -> str:
    """The repository lookup URL: the packages whose metadata names the GitHub
    repo, most dependents first, at most ``limit`` of them.

    ``GET /packages/lookup?repository_url=https://github.com/{o}/{n}`` answers
    with a JSON array of package objects from every registry ecosyste.ms
    indexes; ``sort=dependent_packages_count&order=desc`` puts the most
    depended-on first and ``per_page`` bounds the answer (one package object
    is tens to hundreds of kilobytes: measured 2026-10-22, five
    ``spring-projects/spring-boot`` packages were 1,476,597 bytes).
    """
    query = urllib.parse.urlencode({
        "repository_url": f"https://github.com/{owner}/{name}",
        "sort": "dependent_packages_count",
        "order": "desc",
        "per_page": limit,
    })
    return f"{ECOSYSTEMES_API}/packages/lookup?{query}"


def parse_lookup(payload: object, registry: str) -> list[tuple[str, int]]:
    """``(name, dependent_packages_count)`` of the lookup's packages in one registry.

    ``registry`` is the ecosyste.ms registry name (:data:`REGISTRY_FOR_ECOSYSTEM`);
    a package object names its registry as ``{"name": ...}``. The answer's
    order is kept (most dependents first, as :func:`repository_lookup_url`
    asks). A package without a name or without an integral, non-negative
    count is dropped, and anything that is not a list of objects reads as no
    packages -- never as a count.
    """
    if not isinstance(payload, list):
        return []
    rows: list[tuple[str, int]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        reg = entry.get("registry")
        if not isinstance(reg, dict) or reg.get("name") != registry:
            continue
        parsed = parse_package(entry)
        package = entry.get("name")
        if parsed is None or not isinstance(package, str) or not package:
            continue
        rows.append((package, parsed["dependent_packages_count"]))
    return rows
