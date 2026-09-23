"""Derive :class:`~cvi.core.gates.GateEvidence` from raw envelopes.

Extracted from ``scripts/build_fixture_snapshot.py`` so both the fixture
builder and production v2 snapshot builder (``cvi snapshot build``) share
one implementation of gate-evidence derivation from ``raw/tree`` and
``raw/commits_180d`` envelopes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence

from cvi.collect.commits import CommitActor, envelope_rows, is_bot
from cvi.core.gates import GateEvidence

__all__ = [
    "RECOGNIZED_MANIFEST_BASENAMES",
    "MANIFEST_MAX_DEPTH",
    "_has_recognized_manifest",
    "_has_license_file",
    "_has_off_platform_marker",
    "_tree_paths",
    "_vitality_commit_count",
    "gate_evidence_for",
]

#: D-GATE-1 recognised build/package manifests (02 section 2), exact
#: basename match plus the two suffix rules the design text also names.
RECOGNIZED_MANIFEST_BASENAMES: frozenset[str] = frozenset({
    "pyproject.toml", "setup.py", "setup.cfg", "package.json", "Cargo.toml",
    "go.mod", "pom.xml", "CMakeLists.txt", "Makefile", "meson.build",
    "configure.ac", "mix.exs", "Gemfile",
})
#: D-GATE-1 depth bound: at most this many "/" separators from the repo root.
MANIFEST_MAX_DEPTH = 2


def _has_recognized_manifest(paths: Sequence[str]) -> bool:
    """D-GATE-1: a recognised manifest at tree depth <= 2 anywhere in
    ``paths`` (a flat list of repo-relative file paths, as the sample's
    ``raw/tree`` envelopes record them)."""
    for path in paths:
        if path.count("/") > MANIFEST_MAX_DEPTH:
            continue
        base = path.rsplit("/", 1)[-1]
        if (
            base in RECOGNIZED_MANIFEST_BASENAMES
            or base.endswith(".cabal")
            or base.startswith("build.gradle")
        ):
            return True
    return False


def _has_license_file(paths: Sequence[str]) -> bool:
    """D-GATE-3's "look for a LICENSE* file" fallback, root-level only.

    Recognises both the literal ``LICENSE*`` pattern the design text names
    and the extremely common ``COPYING`` synonym (the file GNU-licensed
    projects -- ``torvalds/linux`` included -- use instead): this is this
    module's own documented interpretation, since ``cvi.core.gates`` takes
    the bool as a caller-supplied fact and defines no file-detection rule of
    its own to import."""
    for path in paths:
        if "/" in path:
            continue
        upper = path.upper()
        if upper.startswith("LICENSE") or upper.startswith("COPYING"):
            return True
    return False


def _has_off_platform_marker(paths: Sequence[str]) -> bool:
    """D-GATE-6 off-platform marker (02 section 2): tree-path signal
    (``Documentation/process/submitting-patches*``)."""
    return any("documentation/process/submitting-patches" in path.lower() for path in paths)


def _tree_paths(raw_root: Path, basename: str) -> list[str]:
    path = raw_root / "tree" / f"{basename}.json"
    if not path.is_file():
        return []
    document = json.loads(path.read_text(encoding="utf-8"))
    data = document.get("data") if isinstance(document, Mapping) else None
    paths = data.get("paths") if isinstance(data, Mapping) else None
    return [p for p in paths if isinstance(p, str)] if isinstance(paths, list) else []


def _vitality_commit_count(raw_root: Path, basename: str) -> int | None:
    """D-GATE-4: non-bot commit count over the sample's own ``commits_180d``
    envelope (already the trailing-180-day window -- D-IN-4's own window for
    this gate). ``None`` (UNDECIDED, never a guessed 0) when the envelope is
    absent."""
    path = raw_root / "commits_180d" / f"{basename}.json"
    if not path.is_file():
        return None
    document = json.loads(path.read_text(encoding="utf-8"))
    rows = envelope_rows(document.get("data") if isinstance(document, Mapping) else None)
    if rows is None:
        return None
    count = 0
    for entry in rows:
        if not isinstance(entry, Mapping):
            continue
        login = entry.get("author_login") or ""
        actor = CommitActor(login=str(login), email="", actor_type="")
        if not is_bot(actor):
            count += 1
    return count


def gate_evidence_for(raw_root: Path, basename: str) -> GateEvidence:
    """Derive one repo's :class:`~cvi.core.gates.GateEvidence` from the
    raw ``tree``/``commits_180d`` envelopes. ``fork_parent_in_universe`` stays
    ``None`` (undecided): every v1-loaded bundle's ``RepoIdentity.fork`` is
    hardcoded ``False`` by ``cvi.derive.bundle._load_v1`` (that loader never
    observes a real fork flag), so D-GATE-2's fork branch never reads this
    field for any repo this script scores -- ``None`` is correct, not a
    stand-in for missing data."""
    paths = _tree_paths(raw_root, basename)
    return GateEvidence(
        manifest_found=_has_recognized_manifest(paths) if paths else None,
        is_curated_anchor=False,
        license_file_present=_has_license_file(paths) if paths else None,
        fork_parent_in_universe=None,
        vitality_commits_180d=_vitality_commit_count(raw_root, basename),
        off_platform_marker=_has_off_platform_marker(paths) if paths else None,
    )
