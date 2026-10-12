"""Derive :class:`~porchlamp.core.gates.GateEvidence` from raw envelopes.

Extracted from ``scripts/build_fixture_snapshot.py`` so both the fixture
builder and production v2 snapshot builder (``porchlamp snapshot build``) share
one implementation of gate-evidence derivation from ``raw/tree`` and
``raw/commits_180d`` envelopes, and the D-GATE-6 review hand-off from the
``raw/pr_window`` envelope.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from porchlamp.core import constants
from porchlamp.core.commit_domains import CommitActor, envelope_rows, is_bot
from porchlamp.core.gates import GateEvidence

__all__ = [
    "RECOGNIZED_MANIFEST_BASENAMES",
    "MANIFEST_MAX_DEPTH",
    "_has_recognized_manifest",
    "_has_license_file",
    "_has_off_platform_marker",
    "_pr_has_review_handoff_notice",
    "_review_handoff",
    "_tree_paths",
    "_vitality_commit_count",
    "gate_evidence_for",
]

#: D-GATE-1 recognised build/package manifests (02 section 2), exact
#: basename match plus the prefix/suffix rules below.
RECOGNIZED_MANIFEST_BASENAMES: frozenset[str] = frozenset({
    "pyproject.toml", "setup.py", "setup.cfg", "package.json", "Cargo.toml",
    "go.mod", "pom.xml", "CMakeLists.txt", "Makefile", "GNUmakefile", "Kbuild",
    "meson.build", "configure.ac", "mix.exs", "Gemfile",
    "BUILD", "BUILD.bazel", "MODULE.bazel", "WORKSPACE", "WORKSPACE.bazel",
    "build.zig", "build.zig.zon", "flake.nix", "default.nix", "SConstruct",
    "vcpkg.json", "conanfile.py", "conanfile.txt", "composer.json",
    "pubspec.yaml", "Package.swift", "build.sbt", "project.clj", "deps.edn",
    "stack.yaml", "dune-project",
})
RECOGNIZED_MANIFEST_PREFIXES: tuple[str, ...] = ("build.gradle",)
RECOGNIZED_MANIFEST_SUFFIXES: tuple[str, ...] = (
    ".cabal", ".csproj", ".fsproj", ".sln", ".rockspec", ".gemspec", ".podspec",
)
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
            or any(base.startswith(p) for p in RECOGNIZED_MANIFEST_PREFIXES)
            or any(base.endswith(s) for s in RECOGNIZED_MANIFEST_SUFFIXES)
        ):
            return True
    return False


def _has_license_file(paths: Sequence[str]) -> bool:
    """D-GATE-3's "look for a LICENSE* file" fallback, root-level only.

    Recognises both the literal ``LICENSE*`` pattern the design text names
    and the extremely common ``COPYING`` synonym (the file GNU-licensed
    projects -- ``torvalds/linux`` included -- use instead): this is this
    module's own documented interpretation, since ``porchlamp.core.gates`` takes
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


def _tree_envelope(raw_root: Path, basename: str) -> tuple[list[str] | None, bool]:
    """Read ``(paths, truncated)`` from ``raw/tree/{basename}.json``, or ``(None, False)`` if absent/invalid."""
    path = raw_root / "tree" / f"{basename}.json"
    if not path.is_file():
        return None, False
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, False
    data = document.get("data") if isinstance(document, Mapping) else None
    if not isinstance(data, Mapping):
        return None, False
    paths = data.get("paths")
    if not isinstance(paths, list):
        return None, False
    truncated = bool(data.get("truncated", False))
    return [p for p in paths if isinstance(p, str)], truncated


def _tree_paths(raw_root: Path, basename: str) -> list[str]:
    paths, _ = _tree_envelope(raw_root, basename)
    return paths or []


def _vitality_commit_count(raw_root: Path, basename: str) -> int | None:
    """D-GATE-4: non-bot commit count over the sample's own ``commits_180d``
    envelope (already the trailing-180-day window -- D-IN-4's own window for
    this gate). ``None`` (UNDECIDED, never a guessed 0) when the envelope is
    absent."""
    path = raw_root / "commits_180d" / f"{basename}.json"
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    rows = envelope_rows(document.get("data") if isinstance(document, Mapping) else None)
    if rows is None:
        return None
    count = 0
    for entry in rows:
        if not isinstance(entry, Mapping):
            continue
        actor = CommitActor(
            login=str(entry.get("author_login") or ""),
            email=str(entry.get("author_email") or ""),
            actor_type=str(entry.get("author_type") or ""),
        )
        # A row with no identity is a commit whose author has no linked GitHub
        # account: a person, unless something marks it a bot. The collector
        # records only author_login and date, so most such rows have no email
        # or type to check either.
        if not is_bot(actor):
            count += 1
    return count


def _login(node: object) -> str:
    """The lower-cased ``author.login`` of a PR or comment node (``""`` if none)."""
    author = node.get("author") if isinstance(node, Mapping) else None
    login = author.get("login") if isinstance(author, Mapping) else None
    return login.lower() if isinstance(login, str) else ""


def _pr_has_review_handoff_notice(pr: Mapping[str, Any]) -> bool:
    """D-GATE-6 review hand-off: whether one PR node carries a review-elsewhere notice.

    A notice is a comment, written by an account other than the PR's own author
    (logins compared case-insensitively), whose text contains one of
    ``constants.GATE6_HANDOFF_NOTICES`` (case-insensitively) or whose author's
    login starts with one of ``constants.GATE6_HANDOFF_RELAY_LOGIN_PREFIXES``.
    A bot's comment counts: the notices are written by bots. Reviews and review
    threads are not read."""
    author = _login(pr)
    comments = pr.get("comments")
    nodes = comments.get("nodes") if isinstance(comments, Mapping) else None
    for comment in nodes if isinstance(nodes, list) else ():
        if not isinstance(comment, Mapping):
            continue
        who = _login(comment)
        if who == author:
            continue
        text = comment.get("bodyText")
        text = text.lower() if isinstance(text, str) else ""
        if who.startswith(tuple(constants.GATE6_HANDOFF_RELAY_LOGIN_PREFIXES)):
            return True
        if any(phrase in text for phrase in constants.GATE6_HANDOFF_NOTICES):
            return True
    return False


def _review_handoff(raw_root: Path, basename: str) -> bool | None:
    """D-GATE-6 review hand-off from ``raw/pr_window/{basename}.json``.

    The outside PRs are those the collector's own ``derived`` rows (joined to
    ``prs`` by PR number) classify as external and not bot-authored, so there
    is one definition of "external" and "bot" (``derive_pr_window``); no age or
    date filter. The repository hands review off when
    ``constants.GATE6_HANDOFF_MIN_PRS`` or more of them, and at least
    ``constants.GATE6_HANDOFF_MIN_SHARE`` of them, carry a notice
    (:func:`_pr_has_review_handoff_notice`). ``None`` (no opinion) when the
    envelope is absent, unreadable, or holds no ``prs`` / ``derived`` lists."""
    path = raw_root / "pr_window" / f"{basename}.json"
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    data = document.get("data") if isinstance(document, Mapping) else None
    prs = data.get("prs") if isinstance(data, Mapping) else None
    derived = data.get("derived") if isinstance(data, Mapping) else None
    if not isinstance(prs, list) or not isinstance(derived, list):
        return None
    classified = {
        row.get("number"): row for row in derived if isinstance(row, Mapping) and row.get("number") is not None
    }
    outside = [
        pr for pr in prs
        if isinstance(pr, Mapping)
        and (row := classified.get(pr.get("number"))) is not None
        and row.get("is_external") and not row.get("is_bot_author")
    ]
    notices = sum(_pr_has_review_handoff_notice(pr) for pr in outside)
    return bool(
        notices >= constants.GATE6_HANDOFF_MIN_PRS
        and notices >= constants.GATE6_HANDOFF_MIN_SHARE * len(outside)
    )


def _tree_flag(found: bool, truncated: bool) -> bool | None:
    """Return ``True`` when found, ``None`` (UNDECIDED) when absent in a truncated tree, else ``False``."""
    if found:
        return True
    if truncated:
        return None
    return False


def gate_evidence_for(raw_root: Path, basename: str) -> GateEvidence:
    """Derive one repo's :class:`~porchlamp.core.gates.GateEvidence` from the
    raw ``tree``/``commits_180d``/``pr_window`` envelopes. ``fork_parent_in_universe`` stays
    ``None`` (undecided): every v1-loaded bundle's ``RepoIdentity.fork`` is
    hardcoded ``False`` by ``porchlamp.derive.bundle._load_v1`` (that loader never
    observes a real fork flag), so D-GATE-2's fork branch never reads this
    field for any repo this script scores -- ``None`` is correct, not a
    stand-in for missing data."""
    paths, truncated = _tree_envelope(raw_root, basename)
    return GateEvidence(
        manifest_found=None if paths is None else _tree_flag(_has_recognized_manifest(paths), truncated),
        is_curated_anchor=False,
        license_file_present=None if paths is None else _tree_flag(_has_license_file(paths), truncated),
        fork_parent_in_universe=None,
        vitality_commits_180d=_vitality_commit_count(raw_root, basename),
        off_platform_marker=None if paths is None else _tree_flag(_has_off_platform_marker(paths), truncated),
        review_handoff=_review_handoff(raw_root, basename),
    )
