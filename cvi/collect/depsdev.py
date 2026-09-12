"""W1-2: package-level dependents via deps.dev (F3), ecosyste.ms fallback.

F3 (``docs/v2/01-FINDINGS-2026-09-12.md``) measured that v1 read
``usedByCount`` from the deps.dev *project* endpoint
(``GET /v3/projects/github.com%2F{o}%2F{n}``), which never carries a
dependents figure, so dependents were missing for 100% of the sample.
The dependents figure lives at the *package* level. W1-2 measured the
two candidate shapes (2026-09-12, keyless, recorded responses under
``tests/collect/data/``):

* ``GET /v3alpha/systems/{system}/packages/{name}`` -- ``packageKey``,
  ``purl`` and the version list only; **no package-level dependents
  count** (recorded: flask, 64 versions).
* ``GET /v3alpha/systems/{system}/packages/{name}/versions/{v}:dependents``
  -- ``{"dependentCount", "directDependentCount",
  "indirectDependentCount"}`` per version, **count-only, no dependent
  package list** (recorded: flask 3.1.3 -> 12698).

So the package-level count is the dependents count of the package's
``isDefault`` (latest) version. Fallback when deps.dev has no figure:
:mod:`cvi.collect.ecosystemes`
(``dependent_packages_count``; the W1-2 404 on the pypi/flask URL was a
registry-name error -- corrected in W1-2f, see that module).

Repo -> package name mapping: the universe's ``selected_by`` fixes the
ecosystem; the repo manifest is the registry namespace itself, so in the
single-level registries (pypi, crates.io, npm unscoped) the repo name is
the package-name candidate. The systems ecosystem has no package registry
(repology feasibility is measured separately, no scoring use yet); go
needs module paths and maven needs ``groupId:artifactId``, neither
derivable from the repo name -> no mapping, no fetch.

D-PARAM 2.1 (``02`` section 4; ``03`` section 3): dependents are deduplicated
by root organisation and dependents owned by the repo's own org are
dropped. :func:`dedup_dependents` implements that rule on recorded
package lists; count-only payloads (the shape deps.dev answers with)
carry no per-package data, so the source count stands as the deduped
value and the exclusion is not measurable for them.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from cvi.collect import ecosystemes as _ecosystemes
from cvi.collect.envelope import write_envelope

#: Base URL of the deps.dev v3alpha API.
DEPSDEV_API: str = "https://api.deps.dev/v3alpha"

#: CVI ecosystem -> deps.dev system id. Absent = no offline repo -> package
#: mapping (see :func:`package_name_for_repo`); systems has no registry at
#: all, go is keyed by module path, maven by ``groupId:artifactId``.
SYSTEM_FOR_ECOSYSTEM: dict[str, str] = {
    "pypi": "pypi",
    "npm": "npm",
    "crates": "crates.io",
}

_USER_AGENT: str = "cvi-research/0.1 (ranking study)"


def system_for_ecosystem(ecosystem: str) -> Optional[str]:
    """Deps.dev system id for a CVI ecosystem, or None when unmapped."""
    return SYSTEM_FOR_ECOSYSTEM.get(ecosystem)


def package_name_for_repo(owner: str, name: str, ecosystem: str) -> Optional[str]:
    """Candidate registry package name for a repo, or None when unmappable.

    The registry is the arbiter: the candidate is only the repo name
    normalised to the registry's case convention (crates: lowercase).
    """
    if ecosystem not in SYSTEM_FOR_ECOSYSTEM:
        return None
    return name.lower() if ecosystem == "crates" else name


def package_url(system: str, package: str) -> str:
    """Deps.dev package endpoint URL."""
    return f"{DEPSDEV_API}/systems/{system}/packages/{package}"


def version_dependents_url(system: str, package: str, version: str) -> str:
    """Deps.dev per-version dependents endpoint URL."""
    return f"{package_url(system, package)}/versions/{version}:dependents"


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


def collect_dependents(raw_dir: Path, owner: str, name: str,
                       ecosystem: str) -> str:
    """Fetch the package-level dependents and write one envelope.

    Writes ``raw_dir/dependents/<owner>__<name>.json`` (the envelope
    contract in :mod:`cvi.collect.envelope`) and returns ``"ok"``.
    Returns ``"skip"`` when the ecosystem has no offline package-name
    mapping (systems / go / maven): nothing is fetched, no envelope is
    written. Returns ``"fail"`` when no source carries a count.
    """
    system = SYSTEM_FOR_ECOSYSTEM.get(ecosystem)
    package = package_name_for_repo(owner, name, ecosystem)
    if system is None or package is None:
        return "skip"

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

    key = f"{owner}__{name}"
    write_envelope(raw_dir / "dependents" / f"{key}.json", source=source,
                   data=data, fetched_at=datetime.now(timezone.utc).isoformat())
    return "ok"
