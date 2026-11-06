"""Eligibility metadata and software classification for one repo.

Collects the repository-object facts the v2 gates need (D-GATE-1/2/3/6 in
``docs/v2/02-RANKING-DESIGN.md`` section 2) and derives the boolean
eligibility markers from recorded envelopes:

* D-GATE-2: ``archived``, ``mirror`` (``mirror_url`` set), ``fork``;
* D-GATE-3: ``unlicensed`` — SPDX missing / NOASSERTION / OTHER and no
  ``LICENSE*`` file in the tree;
* D-GATE-6: ``off_platform`` (off-platform contribution markers:
  ``Documentation/process/submitting-patches*`` in the tree, a MAINTAINERS
  file with ``L:`` mailing-list lines, or a README that says patches go to a
  list / Gerrit / Phabricator) and ``no_prs_ever`` — the PRs-enabled
  evidence, true when the all-time PR total is zero;
* D-GATE-1: ``non_software`` — no recognised build/package manifest at tree
  depth <= 2.

:func:`collect_repo_object` writes one envelope per repo under
``<raw_dir>/repo_object/``; :func:`derive_eligibility` turns that envelope
(or the recorded ``github_meta`` + ``tree`` envelopes) into the markers.
The D-GATE constants below transcribe the design verbatim and are this
module's single definition of them.
"""

from __future__ import annotations

import json
import re
import subprocess  # noqa: F401  (tests patch this module's subprocess)
from collections.abc import Set as AbstractSet
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from porchlamp.collect import ghcli, net, validate
from porchlamp.collect.envelope import write_envelope

# --------------------------------------------------------------------------- #
# D-GATE constants (docs/v2/02-RANKING-DESIGN.md section 2)                   #
# --------------------------------------------------------------------------- #

#: D-GATE-1: build/package manifest names recognised at tree depth <= 2.
#: Exact basename matches; the design's two wildcard entries
#: (``build.gradle*`` and ``*.cabal``) are the prefix/suffix tuples below.
D_GATE1_MANIFEST_NAMES: frozenset[str] = frozenset({
    "pyproject.toml", "setup.py", "setup.cfg", "package.json", "Cargo.toml",
    "go.mod", "pom.xml", "CMakeLists.txt", "Makefile", "GNUmakefile", "Kbuild",
    "meson.build", "configure.ac", "mix.exs", "Gemfile",
    "BUILD", "BUILD.bazel", "MODULE.bazel", "WORKSPACE", "WORKSPACE.bazel",
    "build.zig", "build.zig.zon", "flake.nix", "default.nix", "SConstruct",
    "vcpkg.json", "conanfile.py", "conanfile.txt", "composer.json",
    "pubspec.yaml", "Package.swift", "build.sbt", "project.clj", "deps.edn",
    "stack.yaml", "dune-project",
    # widened 2026-10-24: build and dependency files of real projects the list missed
    "requirements.txt", "Pipfile", "pixi.toml", "xmake.lua", "premake4.lua", "premake5.lua", "Rakefile",
    "Podfile", "tsconfig.json", "deno.json", "deno.jsonc", "Gopkg.toml",
    "configure.in", "Dockerfile",
})
#: D-GATE-1: build script names recognised only at the repository root (depth 0), avoiding false positives on
#: documentation subdirectories such as ``docs/how-to/configure`` or ``doc/configure``.
D_GATE1_ROOT_MANIFEST_NAMES: frozenset[str] = frozenset({"configure"})
D_GATE1_MANIFEST_PREFIXES: tuple[str, ...] = ("build.gradle",)
D_GATE1_MANIFEST_SUFFIXES: tuple[str, ...] = (
    ".cabal", ".csproj", ".fsproj", ".sln", ".rockspec", ".gemspec", ".podspec",
)
D_GATE1_MAX_DEPTH: int = 2

#: Maximum git-tree REST requests one repo's shallow crawl (:func:`_shallow_tree`)
#: may make, counting the root listing. The D-GATE-1/3/6 evidence this feeds only
#: needs the root listing (depth-0 files and dirs are free) plus the immediate
#: children required to confirm a manifest / ``LICENSE`` / submitting-patches path
#: at depth <= 2; a full crawl of a wide monorepo costs dozens of calls (the audit
#: measured 61+ against the plan's 1-2-per-repo budget) without adding gate
#: evidence. 8 bounds the worst case at the root plus seven subdirectory listings,
#: enough to cover realistic small/medium repos. A crawl that hits the cap returns
#: its partial path list with ``truncated=True`` so the envelope is never mistaken
#: for a complete tree.
MAX_TREE_REQUESTS: int = 8

#: D-GATE-3: licence states that count as "no usable licence recorded"; the
#: gate then looks for a ``LICENSE*`` file before failing.
D_GATE3_UNLICENSED_SPDX: frozenset[Optional[str]] = frozenset({None, "NOASSERTION", "OTHER"})
#: D-GATE-3: case-insensitive basename prefix that counts as a license file.
D_GATE3_LICENSE_FILE_PREFIX: str = "license"

#: D-GATE-6: the tree marker is this path prefix (case-insensitive, the
#: design's ``Documentation/process/submitting-patches*``).
D_GATE6_SUBMITTING_PATCHES_PREFIX: str = "documentation/process/submitting-patches"
#: D-GATE-6: case-insensitive basename of the maintainers file.
D_GATE6_MAINTAINERS_NAME: str = "maintainers"
#: D-GATE-6: case-insensitive basenames that count as the README.
D_GATE6_README_NAMES: frozenset[str] = frozenset(
    {"readme", "readme.md", "readme.rst", "readme.txt", "readme.markdown"})
#: D-GATE-6: named heuristics for "README says patches go to a list /
#: Gerrit / Phabricator"; ``(label, pattern)`` compiled case-insensitively.
D_GATE6_README_PATTERNS: tuple[tuple[str, str], ...] = (
    ("gerrit", r"\bgerrit\b"),
    ("phabricator", r"\bphabricator\b"),
    ("mailing_list", r"mailing[- ]list"),
    ("patch_submission",
     r"(?:submit|send|post|forward)[^.]{0,60}patches?[^.]{0,60}"
     r"(?:mailing[- ]list|list|gerrit|phabricator)"),
)

README_PATTERN_RX: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(rx, re.IGNORECASE)) for label, rx in D_GATE6_README_PATTERNS)
#: D-GATE-6: a MAINTAINERS ``L:`` mailing-list line (line start, list value).
MAINTAINERS_L_LINE_RX: re.Pattern[str] = re.compile(r"^\s*L:\s+\S", re.MULTILINE)

#: The seven derived eligibility markers, in bundle order.
ELIGIBILITY_FIELDS: tuple[str, ...] = (
    "non_software", "mirror", "archived", "fork", "unlicensed",
    "off_platform", "no_prs_ever")

#: One GraphQL lookup for everything the gates read off the repository
#: object. ``pullRequests`` over all three states with ``first: 1`` records
#: the all-time PR total (the PRs-enabled evidence) without fetching nodes.
REPO_OBJECT_QUERY: str = (
    'query($owner: String!, $name: String!) { r: repository(owner: $owner, name: $name) {'
    "isArchived mirrorUrl isFork"
    " parent { name owner { login } }"
    " licenseInfo { spdxId }"
    " pullRequests(states: [OPEN, MERGED, CLOSED], first: 1) { totalCount }"
    " } }"
)

# --------------------------------------------------------------------------- #
# Tree / manifest / marker checks                                             #
# --------------------------------------------------------------------------- #


def tree_depth(path: str) -> int:
    """Directory depth of a repo-relative path.

    ``Makefile`` -> 0, ``src/Makefile`` -> 1, ``a/b/Makefile`` -> 2: the
    number of directory components, the D-GATE-1 depth.
    """
    return path.count("/")


def filter_paths_le2(paths: Optional[list[str]]) -> Optional[list[str]]:
    """Repo paths at D-GATE-1 depth (<= 2), order preserved; None in -> None out."""
    if paths is None:
        return None
    return [p for p in paths if tree_depth(p) <= D_GATE1_MAX_DEPTH]


def has_manifest(paths: list[str]) -> bool:
    """D-GATE-1: does any path carry a recognised build/package manifest?"""
    for p in paths:
        if p in D_GATE1_ROOT_MANIFEST_NAMES:
            return True
        if tree_depth(p) > D_GATE1_MAX_DEPTH:
            continue
        base = p.rsplit("/", 1)[-1]
        if (base in D_GATE1_MANIFEST_NAMES
                or any(base.startswith(x) for x in D_GATE1_MANIFEST_PREFIXES)
                or any(base.endswith(x) for x in D_GATE1_MANIFEST_SUFFIXES)):
            return True
    return False


def has_license_file(paths: list[str]) -> bool:
    """D-GATE-3: does any path carry a ``LICENSE*`` file?"""
    return any(p.rsplit("/", 1)[-1].lower().startswith(D_GATE3_LICENSE_FILE_PREFIX)
               for p in paths)


def off_platform_paths(paths: list[str]) -> list[str]:
    """D-GATE-6: the tree paths matching
    ``Documentation/process/submitting-patches*`` (case-insensitive prefix)."""
    prefix = D_GATE6_SUBMITTING_PATCHES_PREFIX
    return [p for p in paths if p.lower().startswith(prefix)]


def first_named_path(paths: list[str], names: AbstractSet[str]) -> Optional[str]:
    """First path whose lowercased basename is in ``names``; depth 0 preferred."""
    fallback: Optional[str] = None
    for p in paths:
        if p.rsplit("/", 1)[-1].lower() in names:
            if tree_depth(p) == 0:
                return p
            fallback = fallback or p
    return fallback


def readme_off_platform_hits(body: Optional[str]) -> Optional[list[str]]:
    """D-GATE-6: the named :data:`D_GATE6_README_PATTERNS` hits in a README
    body; None when there is no body (no README recorded)."""
    if body is None:
        return None
    return [label for label, rx in README_PATTERN_RX if rx.search(body)]


def maintainers_l_lines(body: Optional[str]) -> Optional[int]:
    """D-GATE-6: the number of ``L:`` mailing-list lines in a MAINTAINERS
    body; None when there is no body."""
    if body is None:
        return None
    return len(MAINTAINERS_L_LINE_RX.findall(body))


# --------------------------------------------------------------------------- #
# Derivation (recorded envelopes -> markers)                                  #
# --------------------------------------------------------------------------- #


def derive_eligibility(ro: Optional[dict],
                       meta: Optional[dict] = None,
                       tree: Optional[dict] = None) -> dict[str, tuple[bool, str, Optional[str]]]:
    """Derive the D-GATE-1/2/3/6 markers from recorded envelopes.

    ``ro``: the repo_object envelope ``data`` (primary source, written by
    :func:`collect_repo_object`). ``meta`` / ``tree``: the recorded
    ``github_meta`` / ``tree`` envelope ``data`` as fallbacks for the fields
    those sweeps already recorded (``archived``, ``license_spdx``, the full
    tree path list).

    Returns ``{field: (value, confidence, source_collector)}`` for every
    field in :data:`ELIGIBILITY_FIELDS`; every value is a bool and the source
    is one of ``"repo_object"``, ``"github_meta"``, ``"tree"`` or None.
    Absence of evidence never reads as a positive flag: a field without
    evidence is ``(False, "missing", None)``. A truncated tree cannot prove a
    manifest absent, so there ``non_software`` stays False with confidence
    ``"flag"``.
    """
    meta = meta or {}
    tree = tree or {}
    if ro is not None:
        paths = ro.get("tree_paths_le2")
        truncated = bool(ro.get("tree_truncated"))
        archived, spdx = ro.get("archived"), ro.get("license_spdx_id")
        mirror_url, fork, total = ro.get("mirror_url"), ro.get("fork"), \
            ro.get("pull_requests_total")
        off_paths = ro.get("offplatform_paths") or []
        maint_l = ro.get("maintainers_has_l_lines")
        readme_hits = ro.get("readme_off_platform_hits")
        primary = "repo_object"
    else:
        paths = filter_paths_le2(tree.get("paths"))
        truncated = bool(tree.get("truncated"))
        archived, spdx = meta.get("archived"), meta.get("license_spdx")
        mirror_url = fork = total = None
        off_paths = off_platform_paths(paths or [])
        maint_l = readme_hits = None
        primary = None

    out: dict[str, tuple[bool, str, Optional[str]]] = {}

    # D-GATE-2: archived
    out["archived"] = ((archived, "verified", primary or "github_meta")
                       if isinstance(archived, bool) else (False, "missing", None))

    # D-GATE-2: mirror (mirror_url set) / fork
    out["mirror"] = ((mirror_url is not None, "verified", "repo_object")
                     if ro is not None else (False, "missing", None))
    out["fork"] = ((fork, "verified", "repo_object")
                   if ro is not None and isinstance(fork, bool) else (False, "missing", None))

    # D-GATE-3: unlicensed (undetermined SPDX and no LICENSE* file in the tree)
    if spdx in D_GATE3_UNLICENSED_SPDX:
        if paths is None:
            out["unlicensed"] = (False, "missing", None)
        else:
            out["unlicensed"] = (not has_license_file(paths), "derived",
                                 primary or "github_meta")
    else:
        out["unlicensed"] = (False, "verified", primary or "github_meta")

    # D-GATE-1: non_software (no manifest at depth <= 2, complete tree only)
    if paths is None:
        out["non_software"] = (False, "missing", None)
    elif has_manifest(paths):
        out["non_software"] = (False, "derived", primary or "tree")
    elif truncated:
        out["non_software"] = (False, "flag", primary or "tree")
    else:
        out["non_software"] = (True, "derived", primary or "tree")

    # D-GATE-6: off_platform (any recorded off-platform marker)
    hit = bool(off_paths) or bool(maint_l) or bool(readme_hits)
    if ro is not None:
        recorded = (ro.get("tree_paths_le2") is not None
                    or maint_l is not None or readme_hits is not None)
        out["off_platform"] = ((hit, "derived", "repo_object") if recorded
                               else (False, "missing", None))
    elif paths is None:
        out["off_platform"] = (False, "missing", None)
    else:
        out["off_platform"] = (hit, "derived", "tree")

    # D-GATE-6: no_prs_ever (PRs-enabled evidence: all-time PR total is zero)
    out["no_prs_ever"] = ((total == 0, "verified", "repo_object")
                          if isinstance(total, int) else (False, "missing", None))
    return out


# --------------------------------------------------------------------------- #
# Collection (network -> one repo_object envelope)                            #
# --------------------------------------------------------------------------- #


def _gh_graphql(query: str, variables: Optional[dict[str, str]] = None) -> tuple[Optional[dict], Optional[str]]:
    """Run one ``gh api graphql`` request; return ``(r, error_summary)``.

    GitHub returns ``data`` and ``errors`` together whenever some fields are
    unavailable (for example a security or admin field the token cannot see)
    while every other field in the same response is present and correct, and
    ``gh api graphql`` exits non-zero for such a partial response. A usable
    ``data.r`` is therefore salvaged and returned with a short summary of the
    errors alongside it, so the caller can tell a partial success from a total
    failure (a whole-envelope discard on any error threw away fields already in
    hand — the same bug fixed at the transport layer in
    ``github_graphql.GhCliTransport``). Raises :class:`RuntimeError` on an
    unparseable body (a true transport/network failure) or when the response has
    errors and no usable data at all; a lookup that found nothing (no data, no
    errors) returns ``(None, None)``.
    """
    fields = [f"{key}={value}" for key, value in (variables or {}).items()]
    argv = ["api", "graphql", "-f", f"query={query}"]
    for field in fields:
        argv += ["-f", field]
    out = ghcli.run(argv, timeout=90)
    try:
        payload = json.loads(out.stdout)
    except (json.JSONDecodeError, ValueError) as e:
        raise RuntimeError(
            f"gh api graphql rc={out.returncode}: {out.stderr[:160] or e}") from e
    r = (payload.get("data") or {}).get("r")
    errors = payload.get("errors")
    if r:
        return r, (str(errors)[:160] if errors else None)
    if errors:
        raise RuntimeError(f"graphql errors: {str(errors)[:160]}")
    return None, None


def _gh_rest(path: str) -> Any:
    """Run one ``gh api`` REST request; return the parsed body (None on empty)."""
    validate.gh_api_path(path)
    out = ghcli.run(["api", path], timeout=90)
    if out.returncode != 0:
        raise RuntimeError(f"gh api {path} rc={out.returncode}: {out.stderr[:160]}")
    return json.loads(out.stdout) if out.stdout.strip() else None


def _shallow_tree(owner: str, name: str, branch: str) -> tuple[Optional[list[str]], bool]:
    """REST fallback for the tree: root listing plus one level per directory,
    i.e. paths at depth <= :data:`D_GATE1_MAX_DEPTH`.

    Returns ``(paths, truncated)``; ``(None, False)`` when the root lookup is
    unusable. At most :data:`MAX_TREE_REQUESTS` git-tree requests are made; when
    the budget runs out mid-crawl the partial path list is returned with
    ``truncated=True``, as it is whenever a subtree listing is unreadable or
    node-truncated — a partial path list never reports ``truncated=False``.
    """
    validate.github_segment(owner, "owner")
    validate.github_segment(name, "name")
    requests = 1  # the root listing below counts against the budget
    root = _gh_rest(f"repos/{owner}/{name}/git/trees/{branch}")
    if not isinstance(root, dict):
        return None, False
    paths: list[str] = []
    truncated = bool(root.get("truncated"))
    frontier: list[tuple[str, str]] = []
    for e in root.get("tree") or []:
        p = e.get("path") or ""
        if not p:
            continue
        paths.append(p)
        if e.get("type") == "tree" and e.get("sha"):
            frontier.append((e["sha"], p))
    for _ in range(D_GATE1_MAX_DEPTH):
        nxt: list[tuple[str, str]] = []
        for sha, prefix in frontier:
            if requests >= MAX_TREE_REQUESTS:
                truncated = True  # budget exhausted: subtrees left unfetched
                return paths, truncated
            node = _gh_rest(f"repos/{owner}/{name}/git/trees/{sha}")
            requests += 1
            if not isinstance(node, dict):
                truncated = True  # unreadable subtree: its paths are not in hand
                continue
            truncated = truncated or bool(node.get("truncated"))
            for e in node.get("tree") or []:
                p = f"{prefix}/{e.get('path') or ''}"
                if e.get("path"):
                    paths.append(p)
                if e.get("type") == "tree" and e.get("sha"):
                    nxt.append((e["sha"], p))
        frontier = nxt
    return paths, truncated


def _fetch_body(url: str) -> Optional[str]:
    """Fetch one raw.githubusercontent.com body; None on any failure (a URL off that host raises)."""
    validate.https_url(url, validate.RAW_GITHUB_HOSTS, "raw content")
    try:
        with net.urlopen(url, 30) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:  # review-ok: gate-network — unreadable body -> no evidence
        return None


def _repo_object_from_meta_envelope(raw_dir: Path, key: str) -> Optional[dict[str, Any]]:
    """Reuse ``raw_dir/github_meta/<key>.json`` when it already carries the
    D-GATE-2/6 repository-object fields (``mirror_url``, ``fork``,
    ``pull_requests_total``), avoiding a duplicate GraphQL round-trip (OPT-4)."""
    meta_env = raw_dir / "github_meta" / f"{key}.json"
    if not meta_env.is_file():
        return None
    try:
        doc = json.loads(meta_env.read_text(encoding="utf-8"))
        mdata = doc.get("data")
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(mdata, dict):
        return None
    if (
        "mirror_url" not in mdata
        or not isinstance(mdata.get("fork"), bool)
        or not isinstance(mdata.get("archived"), bool)
        or not isinstance(mdata.get("pull_requests_total"), int)
    ):
        return None
    spdx = mdata.get("license_spdx")
    parent = mdata.get("parent")
    return {
        "archived": mdata["archived"],
        "mirror_url": mdata.get("mirror_url"),
        "fork": mdata["fork"],
        "parent": parent if isinstance(parent, dict) else None,
        "license_spdx_id": None if spdx == "NOASSERTION" and "license_spdx_id" in mdata else spdx,
        "pull_requests_total": mdata["pull_requests_total"],
        "graphql_partial": False,
        "graphql_error": None,
    }


def collect_repo_object(raw_dir: Path, owner: str, name: str,
                        branch: str) -> str:
    """Collect the repo-object facts and write one envelope.

    One GraphQL repository lookup for ``archived`` / ``mirror_url`` /
    ``fork`` / ``parent`` / ``license.spdx_id`` / the all-time PR total (or
    reuse of a ``github_meta`` envelope that already recorded those fields);
    the recorded ``tree`` envelope when present, else a shallow REST tree at
    depth <= 2; and raw.githubusercontent.com bodies for MAINTAINERS and the
    README when the tree lists them. Writes
    ``raw_dir/repo_object/<owner>__<name>.json`` and returns ``"ok"``;
    returns ``"fail"`` when the repository lookup found nothing. Transport
    failures raise for the caller to mark retryable.
    """
    validate.github_segment(owner, "owner")
    validate.github_segment(name, "name")
    key = f"{owner}__{name}"
    reused = _repo_object_from_meta_envelope(raw_dir, key)
    if reused is not None:
        data: dict[str, Any] = dict(reused)
    else:
        r, graphql_error = _gh_graphql(REPO_OBJECT_QUERY, {"owner": owner, "name": name})
        if not r:
            return "fail"
        data = {
            "archived": r.get("isArchived") if isinstance(r.get("isArchived"), bool) else None,
            "mirror_url": r.get("mirrorUrl"),
            "fork": r.get("isFork") if isinstance(r.get("isFork"), bool) else None,
            "parent": None,
            "license_spdx_id": (r.get("licenseInfo") or {}).get("spdxId"),
            "pull_requests_total": (r.get("pullRequests") or {}).get("totalCount"),
            # A partial GraphQL response (some fields unavailable, the rest present)
            # is salvaged rather than discarded; the partial case is recorded here so
            # the envelope is not silently indistinguishable from a clean lookup.
            "graphql_partial": graphql_error is not None,
            "graphql_error": graphql_error,
        }
        parent = r.get("parent")
        if parent:
            data["parent"] = {"owner": (parent.get("owner") or {}).get("login"),
                              "name": parent.get("name")}

    tree_env = raw_dir / "tree" / f"{key}.json"
    paths: Optional[list[str]] = None
    truncated = False
    tree_source = "none"
    if tree_env.is_file():
        try:
            doc = json.loads(tree_env.read_text(encoding="utf-8"))
            tdata = doc.get("data") or {}
            paths = filter_paths_le2(tdata.get("paths"))
            truncated = bool(tdata.get("truncated"))
            tree_source = "tree-envelope"
        except (json.JSONDecodeError, OSError):  # review-ok: gate-cache — corrupt tree
            paths, truncated, tree_source = None, False, "none"
    if paths is None:
        try:
            paths, truncated = _shallow_tree(owner, name, branch)
            tree_source = "shallow-fetch" if paths is not None else "none"
        except RuntimeError:  # review-ok: gate-network — no tree evidence
            paths, truncated, tree_source = None, False, "none"
    data.update({
        "tree_paths_le2": paths,
        "tree_truncated": truncated,
        "tree_source": tree_source,
        "offplatform_paths": off_platform_paths(paths) if paths is not None else None,
    })

    base_url = f"https://raw.githubusercontent.com/{owner}/{name}/HEAD/"
    maint_path = first_named_path(paths, frozenset({D_GATE6_MAINTAINERS_NAME})) \
        if paths else None
    readme_path = first_named_path(paths, D_GATE6_README_NAMES) if paths else None
    l_count = maintainers_l_lines(_fetch_body(base_url + maint_path)) if maint_path else None
    data.update({
        "maintainers_has_l_lines": bool(l_count) if l_count is not None else None,
        "maintainers_l_line_count": l_count,
        "maintainers_url": base_url + maint_path if maint_path else None,
    })
    hits = readme_off_platform_hits(_fetch_body(base_url + readme_path)) if readme_path else None
    data.update({
        "readme_off_platform": bool(hits) if hits is not None else None,
        "readme_off_platform_hits": hits,
        "readme_url": base_url + readme_path if readme_path else None,
    })

    write_envelope(
        raw_dir / "repo_object" / f"{key}.json",
        source="github-graphql:repository-object",
        data=data,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )
    return "ok"
