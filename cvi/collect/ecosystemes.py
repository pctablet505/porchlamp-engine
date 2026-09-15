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
and the response parse; :mod:`cvi.collect.depsdev` owns the
primary/fallback flow and the envelope.
"""

from __future__ import annotations

import urllib.parse
from typing import Optional

#: Base URL of the public ecosyste.ms REST API.
ECOSYSTEMES_API: str = "https://packages.ecosyste.ms/api/v1"

#: CVI ecosystem -> ecosyste.ms registry name (the value the Packages API
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
    """Ecosyste.ms registry name for a CVI ecosystem, or None."""
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
