"""Derive :class:`~porchlamp.core.gates.GateEvidence` from raw envelopes.

Extracted from ``scripts/build_fixture_snapshot.py`` so both the fixture
builder and production v2 snapshot builder (``porchlamp snapshot build``) share
one implementation of gate-evidence derivation from ``raw/tree`` and
``raw/commits_180d`` envelopes, and the D-GATE-6 review hand-off and the
D-GATE-5 rule inputs from the ``raw/pr_window`` envelope.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from porchlamp.core import constants, gates
from porchlamp.core.commit_domains import CommitActor, envelope_rows, is_bot
from porchlamp.core.gates import Gate5WindowBreakdown, GateEvidence, _parse_iso

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
    "black_hole_window_breakdown_from_raw",
    "gate_evidence_for",
    "outside_pr_authors",
    "pr_rule_inputs_recorded",
    "pr_window_data",
    "PR_SAMPLE_SIZE",
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


def _in_vitality_window(entry: Mapping[str, Any], end: datetime) -> bool:
    """The commit's ``date`` falls in ``[end - 180 d, end]`` (D-GATE-4's window).

    A row with no readable date cannot be placed in the window and is not counted."""
    raw = entry.get("date")
    if not isinstance(raw, str) or not raw:
        return False
    try:
        when = _parse_iso(raw)
    except ValueError:
        return False
    return end - timedelta(days=constants.GATE4_VITALITY_WINDOW_DAYS) <= when <= end


def _vitality_window_end(document: Mapping[str, Any], as_of: datetime) -> datetime:
    """Where D-GATE-4's window ends: ``as_of``, or the envelope's fetch when that came earlier. A fetch
    before ``as_of`` cannot show the commits made after it, so the window ends where the data does, as
    D-GATE-5 judges a PR's age at ``min(now, observed_at)``."""
    raw = document.get("fetched_at")
    try:
        fetched = _parse_iso(raw) if isinstance(raw, str) and raw else None
    except ValueError:
        fetched = None
    return min(as_of, fetched) if fetched is not None else as_of


def _vitality_commit_count(raw_root: Path, basename: str, as_of: datetime | None = None) -> int | None:
    """D-GATE-4: non-bot commit count in the ``commits_180d`` envelope.

    With ``as_of`` (the snapshot's reference time) only commits dated in the
    180 days ending at ``as_of``, or at the envelope's fetch when that came
    earlier (:func:`_vitality_window_end`), count: the envelope's own window
    ends at its fetch time, which can be days or weeks before or after the
    snapshot's. The date is the row's recorded ``date`` (the collector records
    the author date). Without ``as_of`` every row counts. ``None`` (UNDECIDED,
    never a guessed 0) when the envelope is absent."""
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
    end = _vitality_window_end(document, as_of) if as_of is not None else None
    count = 0
    for entry in rows:
        if not isinstance(entry, Mapping):
            continue
        if end is not None and not _in_vitality_window(entry, end):
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


def _envelope_document(raw_root: Path, layer: str, basename: str) -> Mapping[str, Any] | None:
    """The whole envelope ``raw/{layer}/{basename}.json`` (``data`` and its
    ``fetched_at``); ``None`` when absent, unreadable or not a document."""
    path = raw_root / layer / f"{basename}.json"
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return document if isinstance(document, Mapping) else None


def _document_data(document: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """An envelope document's ``data``; ``None`` when it has none."""
    data = document.get("data") if isinstance(document, Mapping) else None
    return data if isinstance(data, Mapping) else None


def _envelope_data(raw_root: Path, layer: str, basename: str) -> Mapping[str, Any] | None:
    """The ``data`` of ``raw/{layer}/{basename}.json``; ``None`` when the
    envelope is absent or unreadable."""
    return _document_data(_envelope_document(raw_root, layer, basename))


def pr_window_data(raw_root: Path, basename: str) -> Mapping[str, Any] | None:
    """The ``data`` of the repository's ``pr_window`` envelope: the PRs opened
    in the 180 days before the fetch; ``None`` when absent or unreadable."""
    return _envelope_data(raw_root, "pr_window", basename)


#: The v1 ``pr_events`` sample size: the collector records the newest this-many PRs of any age
#: (``scripts/gather.py`` ``PR_SAMPLE``; a test pins the two equal). A sample holding fewer is the
#: repository's whole PR history at the time it was fetched.
PR_SAMPLE_SIZE: int = 30


def _pr_sample(raw_root: Path, basename: str) -> list[Any] | None:
    """The PRs of ``raw/pr_events/{basename}.json`` (the v1 sample); ``None`` when absent or unreadable."""
    path = raw_root / "pr_events" / f"{basename}.json"
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    data = document.get("data") if isinstance(document, Mapping) else None
    return data if isinstance(data, list) else None


def _is_decided_external(pr: Mapping[str, Any], horizon: datetime | None, as_of: datetime | None) -> bool:
    """An external PR (author association in :data:`_EXTERNAL_ASSOCIATIONS`) merged or closed in
    ``[horizon, as_of]``; any recorded decision without ``as_of``. Reads both recorded spellings: the
    GraphQL nodes' ``authorAssociation``/``mergedAt``/``closedAt`` and the v1 sample's snake case."""
    association = pr.get("authorAssociation", pr.get("author_association"))
    if str(association or "").upper() not in _EXTERNAL_ASSOCIATIONS:
        return False
    decided_raw = pr.get("mergedAt") or pr.get("merged_at") or pr.get("closedAt") or pr.get("closed_at")
    if not isinstance(decided_raw, str):
        return False
    try:
        decided = _parse_iso(decided_raw)
    except (ValueError, TypeError):
        return False
    return as_of is None or horizon <= decided <= as_of


def _decided_external_pr_in(
    pr_window: Mapping[str, Any] | None,
    author_history: Mapping[str, Any] | None,
    sample: Sequence[Any] | None,
    as_of: datetime | None,
) -> bool | None:
    """D-GATE-6 from the recorded PR envelopes.

    ``True`` when the ``pr_window`` (PRs opened in 180 days), the ``author_history`` (opened in 365 days)
    or the v1 ``pr_events`` sample (the newest :data:`PR_SAMPLE_SIZE` PRs of any age) holds an external PR
    merged or closed in the :data:`~porchlamp.core.constants.GATE6_DECISION_WINDOW_DAYS` ending at
    ``as_of``. Bots count: D-GATE-6 asks whether a repository works through GitHub pull requests at all,
    and D-GATE-5 then judges the outside human ones.

    ``False`` only when the records hold the repository's whole PR history and none of them has one: the
    sample held fewer than :data:`PR_SAMPLE_SIZE` PRs (so it was every PR the repository had when it was
    fetched), and every window or history read beside it says it is complete (``truncated`` false; they
    add the PRs opened since). Each record shows decisions up to its own fetch.
    ``None`` otherwise: a record of the newest PRs, or of the PRs opened in a window, can show that a
    decided external PR exists, but not that none does, because a PR opened before it may have been
    decided inside the horizon."""
    records = [d["prs"] for d in (author_history, pr_window) if d is not None and isinstance(d.get("prs"), list)]
    if sample is not None:
        records.append(sample)
    if not records:
        return None
    horizon = as_of - timedelta(days=constants.GATE6_DECISION_WINDOW_DAYS) if as_of is not None else None
    if any(isinstance(pr, Mapping) and _is_decided_external(pr, horizon, as_of) for prs in records for pr in prs):
        return True
    whole_history = sample is not None and len(sample) < PR_SAMPLE_SIZE
    complete = all(d.get("truncated") is False for d in (author_history, pr_window) if d is not None)
    return False if whole_history and complete else None


def outside_pr_authors(prs: Sequence[Any], *, start: datetime, end: datetime) -> list[str]:
    """One entry per outside PR opened in ``[start, end]``: an external
    author association and a non-bot author (D-IN-1), the PRs D-GATE-5
    starts from. The entry is the author's lowercased login, ``""`` for a
    deleted account."""
    out: list[str] = []
    for pr in prs:
        if not isinstance(pr, Mapping) or not isinstance(pr.get("createdAt"), str):
            continue
        try:
            created = _parse_iso(pr["createdAt"])
        except (ValueError, TypeError):
            continue
        if not start <= created <= end:
            continue
        author = pr.get("author") if isinstance(pr.get("author"), Mapping) else {}
        if str(pr.get("authorAssociation") or "").upper() not in _EXTERNAL_ASSOCIATIONS or _is_bot_node(author):
            continue
        out.append(str(author.get("login") or "").lower())
    return out


def pr_rule_inputs_recorded(prs: Sequence[Any]) -> bool:
    """Whether every recorded PR node carries the fields D-GATE-5's exclusion
    rules read: ``isDraft`` (the draft rule) and the ``timelineItems``
    connection (the ready-for-review clock start and the close actor, which
    decide the author self-close and maintainer-close rules).

    A node recorded by an older query has neither key; reading the absence as
    "not a draft, nobody closed it" is what turned unknowns into unanswered PRs.
    """
    for pr in prs:
        if not isinstance(pr, Mapping) or not isinstance(pr.get("isDraft"), bool):
            return False
        timeline = pr.get("timelineItems")
        if not isinstance(timeline, Mapping) or not isinstance(timeline.get("nodes"), list):
            return False
    return True


def _rule_inputs_in(data: Mapping[str, Any] | None) -> bool | None:
    """D-GATE-5 rule inputs of a ``pr_window`` envelope ``data``
    (:func:`pr_rule_inputs_recorded`); ``None`` (no opinion) when there is no
    envelope or it holds no ``prs`` list."""
    prs = data.get("prs") if data is not None else None
    return pr_rule_inputs_recorded(prs) if isinstance(prs, list) else None


def _review_handoff(raw_root: Path, basename: str) -> bool | None:
    """D-GATE-6 review hand-off from ``raw/pr_window/{basename}.json``
    (:func:`_review_handoff_in`)."""
    return _review_handoff_in(pr_window_data(raw_root, basename))


def _review_handoff_in(data: Mapping[str, Any] | None) -> bool | None:
    """D-GATE-6 review hand-off from a ``pr_window`` envelope's ``data``.

    The outside PRs are those the collector's own ``derived`` rows (joined to
    ``prs`` by PR number) classify as external and not bot-authored, so there
    is one definition of "external" and "bot" (``derive_pr_window``); no age or
    date filter. The repository hands review off when
    ``constants.GATE6_HANDOFF_MIN_PRS`` or more of them, and at least
    ``constants.GATE6_HANDOFF_MIN_SHARE`` of them, carry a notice
    (:func:`_pr_has_review_handoff_notice`). ``None`` (no opinion) when the
    envelope is absent, unreadable, or holds no ``prs`` / ``derived`` lists."""
    if data is None:
        return None
    prs = data.get("prs")
    derived = data.get("derived")
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


def _window_coverage_in(document: Mapping[str, Any] | None, as_of: datetime | None) -> tuple[bool | None, str | None]:
    """D-GATE-5 window coverage of a ``pr_window`` envelope document
    (:func:`porchlamp.core.gates.pr_window_coverage`) against the window
    ending at ``as_of`` (its own window without ``as_of``); ``(None, None)``
    (no opinion) when there is no envelope or its ``data`` holds no ``prs``
    list."""
    data = document.get("data") if isinstance(document, Mapping) else None
    if not isinstance(data, Mapping) or not isinstance(data.get("prs"), list):
        return None, None
    fetched = document.get("fetched_at")
    start = as_of - timedelta(days=constants.PR_WINDOW_DAYS) if as_of is not None else None
    return gates.pr_window_coverage(data, start, fetched_at=fetched if isinstance(fetched, str) else None)


def gate_evidence_for(raw_root: Path, basename: str, *, as_of: datetime | None = None) -> GateEvidence:
    """Derive one repo's :class:`~porchlamp.core.gates.GateEvidence` from the
    raw ``tree``/``commits_180d``/``pr_window``/``author_history``/``pr_events`` envelopes.
    ``as_of`` is the snapshot's reference time: D-GATE-4 counts only commits in
    the 180 days ending there (:func:`_vitality_commit_count`), D-GATE-5 asks
    whether the PR window record covers the window ending there
    (:func:`_window_coverage_in`), and D-GATE-6 only counts decisions in the
    365 days ending there (:func:`_decided_external_pr_in`).
    The ``pr_window`` envelope is read once, for the review hand-off, the
    D-GATE-5 rule inputs and window coverage, and D-GATE-6. ``fork_parent_in_universe`` stays
    ``None`` (undecided): every v1-loaded bundle's ``RepoIdentity.fork`` is
    hardcoded ``False`` by ``porchlamp.derive.bundle._load_v1`` (that loader never
    observes a real fork flag), so D-GATE-2's fork branch never reads this
    field for any repo this script scores -- ``None`` is correct, not a
    stand-in for missing data."""
    paths, truncated = _tree_envelope(raw_root, basename)
    pr_window_document = _envelope_document(raw_root, "pr_window", basename)
    pr_window = _document_data(pr_window_document)
    window_complete, window_cutoff = _window_coverage_in(pr_window_document, as_of)
    author_history = _envelope_data(raw_root, "author_history", basename)
    return GateEvidence(
        manifest_found=None if paths is None else _tree_flag(_has_recognized_manifest(paths), truncated),
        is_curated_anchor=False,
        license_file_present=None if paths is None else _tree_flag(_has_license_file(paths), truncated),
        fork_parent_in_universe=None,
        vitality_commits_180d=_vitality_commit_count(raw_root, basename, as_of),
        off_platform_marker=None if paths is None else _tree_flag(_has_off_platform_marker(paths), truncated),
        review_handoff=_review_handoff_in(pr_window),
        pr_rule_inputs_recorded=_rule_inputs_in(pr_window),
        pr_window_complete=window_complete,
        pr_window_cutoff=window_cutoff,
        decided_external_pr=_decided_external_pr_in(pr_window, author_history, _pr_sample(raw_root, basename), as_of),
    )


_EXTERNAL_ASSOCIATIONS: frozenset[str] = frozenset(
    {"NONE", "FIRST_TIMER", "FIRST_TIME_CONTRIBUTOR", "CONTRIBUTOR"}
)

#: D-IN-1 over a GraphQL actor node (the core's one definition).
_is_bot_node = gates.is_bot_actor_node


def _ghost_rate_prs_from_nodes(prs: Sequence[Mapping[str, Any]]) -> list[gates.GhostRatePR]:
    """D-GATE-5's records from recorded PR nodes, the responses by D-IN-2
    (:func:`porchlamp.core.gates.first_substantive_response` over the window's
    :func:`porchlamp.core.gates.maintainer_logins`)."""
    maintainers = gates.maintainer_logins(prs)
    out: list[gates.GhostRatePR] = []
    for pr in prs:
        if not isinstance(pr, Mapping):
            continue
        created_raw = pr.get("createdAt")
        if not isinstance(created_raw, str):
            continue
        try:
            _parse_iso(created_raw)
        except (ValueError, TypeError):
            continue

        author = pr.get("author") if isinstance(pr.get("author"), Mapping) else {}
        author_login = str(author.get("login") or "").lower()
        assoc = str(pr.get("authorAssociation") or "").upper()
        is_external = assoc in _EXTERNAL_ASSOCIATIONS
        bot_author = _is_bot_node(author)

        response = gates.first_substantive_response(pr, maintainers)
        best_at = response[0] if response is not None else None

        merged_raw = pr.get("mergedAt")
        merged_at: str | None = None
        if isinstance(merged_raw, str):
            try:
                _parse_iso(merged_raw)
                merged_at = merged_raw
            except (ValueError, TypeError):
                merged_at = None

        closed_raw = pr.get("closedAt")
        closed_at: str | None = None
        if isinstance(closed_raw, str):
            try:
                _parse_iso(closed_raw)
                closed_at = closed_raw
            except (ValueError, TypeError):
                closed_at = None

        ready_for_review_at: str | None = (
            pr.get("readyForReviewAt") if isinstance(pr.get("readyForReviewAt"), str) else None
        )
        closed_by_author: bool = bool(pr.get("closedByAuthor", False))
        closed_by_maint_at = pr.get("closedByMaintainerAt")
        if (
            closed_by_maint_at is None
            and pr.get("state") == "CLOSED"
            and not merged_at
            and pr.get("closedByMaintainer") is True
        ):
            closed_by_maint_at = closed_at

        timeline_conn = pr.get("timelineItems")
        timeline_nodes = timeline_conn.get("nodes") if isinstance(timeline_conn, Mapping) else None
        if isinstance(timeline_nodes, list):
            latest_ready_dt: datetime | None = None
            latest_ready_at: str | None = None
            latest_close_dt: datetime | None = None
            latest_close_event: Mapping[str, Any] | None = None
            for ev in timeline_nodes:
                if not isinstance(ev, Mapping):
                    continue
                ev_type = ev.get("__typename")
                ev_ts = ev.get("createdAt")
                ev_dt: datetime | None = None
                if isinstance(ev_ts, str):
                    try:
                        ev_dt = _parse_iso(ev_ts)
                    except (ValueError, TypeError):
                        ev_dt = None
                if ev_type == "ReadyForReviewEvent" and isinstance(ev_ts, str) and ev_dt is not None:
                    if latest_ready_dt is None or ev_dt >= latest_ready_dt:
                        latest_ready_dt, latest_ready_at = ev_dt, ev_ts
                elif ev_type == "ClosedEvent" and (
                    latest_close_event is None
                    or (ev_dt is not None and (latest_close_dt is None or ev_dt >= latest_close_dt))
                ):
                    latest_close_dt, latest_close_event = ev_dt, ev
            if ready_for_review_at is None and latest_ready_at is not None:
                ready_for_review_at = latest_ready_at
            if (
                latest_close_event is not None
                and not merged_at
                and not closed_by_author
                and closed_by_maint_at is None
            ):
                actor = latest_close_event.get("actor")
                actor_map = actor if isinstance(actor, Mapping) else {}
                actor_login = str(actor_map.get("login") or "").lower()
                if actor_login and actor_login == author_login:
                    closed_by_author = True
                elif actor_login and not _is_bot_node(actor_map):
                    ev_created = latest_close_event.get("createdAt")
                    if isinstance(ev_created, str):
                        try:
                            _parse_iso(ev_created)
                            closed_by_maint_at = ev_created
                        except (ValueError, TypeError):
                            closed_by_maint_at = closed_at
                    else:
                        closed_by_maint_at = closed_at

        author_created_at = author.get("createdAt") if isinstance(author.get("createdAt"), str) else None
        num = pr.get("number")
        out.append(
            gates.GhostRatePR(
                created_at=created_raw,
                is_external=is_external,
                is_bot_author=bot_author,
                first_substantive_response_at=best_at,
                merged_at=merged_at,
                is_draft=bool(pr.get("isDraft", False)),
                ready_for_review_at=ready_for_review_at,
                closed_at=closed_at,
                closed_by_author=closed_by_author,
                closed_by_maintainer_at=closed_by_maint_at if isinstance(closed_by_maint_at, str) else None,
                author_created_at=author_created_at,
                number=num if isinstance(num, int) and not isinstance(num, bool) else None,
            )
        )
    return out


def black_hole_window_breakdown_from_raw(
    raw_root: Path,
    basename: str,
    now: datetime | None = None,
) -> Gate5WindowBreakdown | None:
    """Evaluate D-GATE-5 window breakdown from raw pr_window envelope."""
    path = raw_root / "pr_window" / f"{basename}.json"
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if not isinstance(document, Mapping):
        return None
    data = document.get("data")
    if not isinstance(data, Mapping):
        return None
    prs = data.get("prs")
    if not isinstance(prs, list):
        return None

    anchor_str = data.get("window_anchor")
    fetched_str = (
        document.get("fetched_at")
        if isinstance(document.get("fetched_at"), str)
        else (data.get("fetched_at") if isinstance(data.get("fetched_at"), str) else None)
    )

    anchor_dt: datetime | None = None
    if isinstance(anchor_str, str):
        try:
            anchor_dt = _parse_iso(anchor_str)
        except (ValueError, TypeError):
            anchor_dt = None

    fetched_dt: datetime | None = None
    if isinstance(fetched_str, str):
        try:
            fetched_dt = _parse_iso(fetched_str)
        except (ValueError, TypeError):
            fetched_dt = None

    if now is not None:
        ref_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    elif anchor_dt is not None:
        ref_now = anchor_dt
    elif fetched_dt is not None:
        ref_now = fetched_dt
    else:
        return None

    # The verdict's rule (scripts/derive.py): age is judged at the earlier of
    # the envelope's fetched_at and its window_anchor, whichever exist.
    read_times = [t for t in (fetched_dt, anchor_dt) if t is not None]
    observed_at = min(read_times) if read_times else None

    records = _ghost_rate_prs_from_nodes(prs)
    try:
        return gates.evaluate_gate5_window(records, ref_now, observed_at=observed_at)
    except ValueError:
        return None

