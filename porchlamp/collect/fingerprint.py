"""Bulk repository fingerprinting, cadence tiers, and dirty-only invalidation (08 D-COL-1, D-COL-3, D-COL-4, W6-2).

Batches up to :data:`FINGERPRINT_BATCH_SIZE` (25) repositories into a single
aliased GitHub GraphQL request (231 queries cover the 5,753-repository
universe). 100 per query is not usable on the live API: it answers with 502/504
or with ``RESOURCE_LIMITS_EXCEEDED`` and null aliases (measured 2026-10-06,
PR #3 review). Each repository alias fetches the lightweight change-detection
fields of ``docs/v2/08-SUSTAINABLE-COLLECTION.md`` section 4 (D-COL-1):

* ``pushedAt``, ``updatedAt``, ``isArchived``, ``isDisabled``,
  ``stargazerCount``, ``forkCount``;
* ``pullRequests(last: 1, orderBy: {field: UPDATED_AT, direction: ASC})``;
* ``issues(last: 1, orderBy: {field: UPDATED_AT, direction: ASC})``;
* ``releases(last: 1, orderBy: {field: CREATED_AT, direction: ASC})``;
* ``defaultBranchRef { target { oid } }``.

A batch that fails is retried (5xx with backoff, null aliases in smaller
batches). A repository still unresolved afterwards is reported in
``unresolved_keys`` and counts as DIRTY (refetch), never clean.

:func:`dirty_collectors` combines every reason a collector must run again:

* the fingerprint moved (:func:`invalidated_collectors`, ``08`` section 6);
* the envelope is missing;
* the weekly clock of ``depsdev`` / ``scorecard`` / ``downloads`` /
  ``dependents`` is due (D-COL-4);
* a slow-fact collector (``governance``, ``contributors``, ``repo_object``,
  ``tree``) is at least 30 days old AND the default branch moved since that
  collector's last fetch (D-COL-4; the baseline is kept in
  :attr:`RepoFingerprint.slow_oids`, so the test does not depend on one pass
  seeing both facts);
* the ``releases`` / ``issues_counts`` window anchor is close to
  ``WINDOW_STALENESS_DAYS`` (derive marks an older anchor Unmeasured).

This module never writes envelopes and a pass never writes state. The caller
persists a repo's new fingerprint with :func:`fingerprint_to_persist` /
:func:`append_fingerprints` only after that repo's invalidated collectors
succeeded, so a failed refetch is retried on the next run.

:func:`classify_cadence_tier` computes the repository's activity cadence tier
(``hot``, ``warm``, ``cold``, ``dormant``) from its observed activity
timestamps (D-COL-3).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from porchlamp.collect.depsdev import SYSTEM_FOR_ECOSYSTEM
from porchlamp.collect.github_graphql import GhCliTransport, GraphqlTransport, is_bot_actor
from porchlamp.collect.repo_object import derive_eligibility
from porchlamp.core.constants import GATE4_VITALITY_WINDOW_DAYS, GATE6_DECISION_WINDOW_DAYS, WINDOW_STALENESS_DAYS
from porchlamp.core.gates import (
    UNDETERMINED_SPDX,
    _is_osi_approved_expression,
    _is_parseable_spdx_expression,
)

__all__ = [
    "FINGERPRINT_BATCH_SIZE",
    "FETCH_ATTEMPTS",
    "BACKOFF_SECONDS",
    "HOT_DAYS",
    "WARM_DAYS",
    "COLD_DAYS",
    "STALE_PUSH_WINDOW_DAYS",
    "SLOW_FACT_TTL_DAYS",
    "WEEKLY_TTL_DAYS",
    "WINDOW_ANCHOR_REFRESH_DAYS",
    "CADENCE_TIERS",
    "PRUNED_REASONS",
    "ALL_GATHER_COLLECTORS",
    "SLOW_BRANCH_COLLECTORS",
    "WEEKLY_COLLECTORS",
    "WINDOW_ANCHOR_COLLECTORS",
    "RepoFingerprint",
    "FingerprintSweepResult",
    "build_fingerprint_batch_query",
    "fetch_fingerprint_batch",
    "fetch_fingerprints_resilient",
    "load_latest_fingerprints",
    "append_fingerprints",
    "classify_cadence_tier",
    "classify_prune_reason",
    "invalidated_collectors",
    "envelope_path",
    "envelope_fetched_at",
    "dirty_collectors",
    "fingerprint_to_persist",
    "run_fingerprint_pass",
]

#: D-COL-1 batch size. 25 aliases per query is the largest size that resolved
#: reliably on the live API (1 point and ~5 s per query; 100 per query returned
#: 502/504 or RESOURCE_LIMITS_EXCEEDED for 99% of the universe).
FINGERPRINT_BATCH_SIZE: int = 25

#: A batch whose transport call raises (a 5xx or a timeout) is tried this many
#: times, sleeping :data:`BACKOFF_SECONDS` between tries.
FETCH_ATTEMPTS: int = 3
BACKOFF_SECONDS: tuple[float, ...] = (2.0, 4.0)

#: D-COL-3 cadence tier thresholds (in days since most recent activity).
HOT_DAYS: int = 7
WARM_DAYS: int = 30
COLD_DAYS: int = 365

#: D-GATE-4 trailing window for repository vitality (180 days).
STALE_PUSH_WINDOW_DAYS: int = GATE4_VITALITY_WINDOW_DAYS

#: Closed vocabulary of pre-collection eligibility prune reasons.
PRUNED_REASONS: tuple[str, ...] = (
    "archived",
    "disabled",
    "mirror",
    "stale",
    "unlicensed",
    "non_software",
    "no_external_workflow",
)

#: D-COL-4: slow-fact collectors (governance, contributors, repo_object, tree)
#: refetch at most every 30 days and only when defaultBranchRef.target.oid moved.
SLOW_FACT_TTL_DAYS: int = 30

#: D-COL-4: ``depsdev``, ``scorecard``, ``downloads`` and ``dependents`` refresh weekly.
WEEKLY_TTL_DAYS: int = 7

#: ``releases`` and ``issues_counts`` carry a ``window_anchor``; derive marks an
#: anchor more than ``WINDOW_STALENESS_DAYS`` old Unmeasured (openssf_criticality).
#: Refetch one day early so a nightly derive never meets a stale anchor.
WINDOW_ANCHOR_REFRESH_DAYS: int = WINDOW_STALENESS_DAYS - 1

#: Closed vocabulary of D-COL-3 cadence tiers.
CADENCE_TIERS: tuple[str, ...] = ("hot", "warm", "cold", "dormant")

#: Every collector name ``scripts/gather.py`` manages.
ALL_GATHER_COLLECTORS: frozenset[str] = frozenset({
    "github_meta", "pr_events", "pr_reviews", "contributors", "gfi", "actions_runs", "tree",
    "commits_180d", "depsdev", "scorecard", "downloads", "repo_object", "commits_365d",
    "dependents", "pr_window", "releases", "issues_counts", "runs", "governance",
    "author_history",
})

#: Collectors on the slow clock: 30 days AND a default-branch move (D-COL-4).
SLOW_BRANCH_COLLECTORS: frozenset[str] = frozenset({
    "governance", "contributors", "repo_object", "tree",
})

#: Collectors on the weekly clock (D-COL-4).
WEEKLY_COLLECTORS: frozenset[str] = frozenset({
    "depsdev", "scorecard", "downloads", "dependents",
})

#: Collectors whose envelope carries a ``window_anchor`` that derive checks.
WINDOW_ANCHOR_COLLECTORS: frozenset[str] = frozenset({"releases", "issues_counts"})

#: Collectors driven by the fingerprint itself when a repo cannot be resolved.
_FAST_COLLECTORS: frozenset[str] = (
    ALL_GATHER_COLLECTORS - SLOW_BRANCH_COLLECTORS - WEEKLY_COLLECTORS
)

#: Strict GitHub owner/name identifier pattern.
_SAFE_SLUG_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")

#: Sentinel: a slow collector's baseline oid was never recorded.
_UNKNOWN: Any = object()


def _validate_repo_pair(owner: str, name: str) -> tuple[str, str]:
    """Validate ``(owner, name)`` against GitHub slug characters."""
    if not _SAFE_SLUG_RE.fullmatch(owner) or not _SAFE_SLUG_RE.fullmatch(name):
        raise ValueError(f"invalid repository identifier: {owner!r}/{name!r}")
    if owner in (".", "..") or name in (".", ".."):
        raise ValueError(f"invalid repository path segment: {owner!r}/{name!r}")
    return owner, name


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp; naive values are interpreted as UTC."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class RepoFingerprint:
    """One D-COL-1 repository fingerprint observation.

    ``slow_oids`` maps each slow-fact collector to the default-branch oid at
    that collector's last successful fetch (the baseline for "the branch moved
    since that collector's last fetch"); a collector absent from it has no
    recorded baseline and counts as moved.
    """

    owner: str
    name: str
    observed_at: str
    pushed_at: Optional[str] = None
    updated_at: Optional[str] = None
    is_archived: bool = False
    is_disabled: bool = False
    stargazer_count: int = 0
    fork_count: int = 0
    pr_updated_at: Optional[str] = None
    issue_updated_at: Optional[str] = None
    release_created_at: Optional[str] = None
    default_branch_oid: Optional[str] = None
    slow_oids: dict[str, Optional[str]] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Canonical ``owner__name`` envelope key."""
        return f"{self.owner}__{self.name}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> RepoFingerprint:
        """Reconstruct a :class:`RepoFingerprint` from a parsed JSON mapping."""
        owner, name = _validate_repo_pair(str(raw["owner"]), str(raw["name"]))
        slow = raw.get("slow_oids")
        return cls(
            owner=owner,
            name=name,
            observed_at=str(raw["observed_at"]),
            pushed_at=raw.get("pushed_at"),
            updated_at=raw.get("updated_at"),
            is_archived=bool(raw.get("is_archived", False)),
            is_disabled=bool(raw.get("is_disabled", False)),
            stargazer_count=int(raw.get("stargazer_count") or 0),
            fork_count=int(raw.get("fork_count") or 0),
            pr_updated_at=raw.get("pr_updated_at"),
            issue_updated_at=raw.get("issue_updated_at"),
            release_created_at=raw.get("release_created_at"),
            default_branch_oid=raw.get("default_branch_oid"),
            slow_oids=dict(slow) if isinstance(slow, Mapping) else {},
        )


@dataclass(frozen=True)
class FingerprintSweepResult:
    """Outcome of a bulk fingerprint pass across the candidate universe.

    ``invalidated_by_repo`` holds only the collectors the fingerprint itself
    invalidated (the set whose success gates persisting the new fingerprint).
    ``dirty_by_repo`` is every collector to refetch (fingerprint, missing
    envelope, clocks, window anchors); an unresolved repo has an entry too.
    ``pruned_by_repo`` maps ``owner__name`` keys pruned by ``prune_ineligible``
    to their :data:`PRUNED_REASONS` classification.
    """

    fingerprints: dict[str, RepoFingerprint]
    previous: dict[str, RepoFingerprint]
    invalidated_by_repo: dict[str, frozenset[str]]
    dirty_by_repo: dict[str, frozenset[str]]
    tiers_by_repo: dict[str, str]
    unresolved_keys: tuple[str, ...]
    queries_executed: int
    cost_points: int
    rate_limit_remaining: Optional[int]
    pruned_by_repo: dict[str, str] = field(default_factory=dict)


def build_fingerprint_batch_query(count: int) -> str:
    """Build the aliased ``RepoFingerprintBatch`` GraphQL query for ``count`` repos.

    Uses parameterized GraphQL variables ``$o{i}: String!, $n{i}: String!``
    for every repository alias so repository names are never interpolated into
    the query syntax.
    """
    if count <= 0:
        raise ValueError(f"count must be >= 1, got {count}")
    var_decls = ", ".join(f"$o{i}: String!, $n{i}: String!" for i in range(count))
    aliases = " ".join(
        f"r{i}: repository(owner: $o{i}, name: $n{i}) {{"
        " pushedAt updatedAt isArchived isDisabled stargazerCount forkCount"
        " pullRequests(last: 1, orderBy: {field: UPDATED_AT, direction: ASC}) {"
        " nodes { updatedAt } }"
        " issues(last: 1, orderBy: {field: UPDATED_AT, direction: ASC}) {"
        " nodes { updatedAt } }"
        " releases(last: 1, orderBy: {field: CREATED_AT, direction: ASC}) {"
        " nodes { createdAt } }"
        " defaultBranchRef { target { oid } }"
        " }"
        for i in range(count)
    )
    return (
        f"query RepoFingerprintBatch({var_decls}) {{"
        f" rateLimit {{ cost remaining resetAt }} {aliases} }}"
    )


def _last_node_field(connection: Any, field_name: str) -> Optional[str]:
    """Extract ``field_name`` from the single node of a ``last: 1`` connection."""
    if not isinstance(connection, Mapping):
        return None
    nodes = connection.get("nodes")
    if not isinstance(nodes, Sequence) or not nodes:
        return None
    node = nodes[-1]
    if not isinstance(node, Mapping):
        return None
    val = node.get(field_name)
    return str(val) if isinstance(val, str) and val else None


def _parse_repo_node(
    owner: str, name: str, node: Mapping[str, Any], observed_at: str
) -> RepoFingerprint:
    """Convert one GraphQL ``repository`` alias object into a :class:`RepoFingerprint`."""
    branch_ref = node.get("defaultBranchRef")
    target = branch_ref.get("target") if isinstance(branch_ref, Mapping) else None
    oid = target.get("oid") if isinstance(target, Mapping) else None
    return RepoFingerprint(
        owner=owner,
        name=name,
        observed_at=observed_at,
        pushed_at=node.get("pushedAt") if isinstance(node.get("pushedAt"), str) else None,
        updated_at=node.get("updatedAt") if isinstance(node.get("updatedAt"), str) else None,
        is_archived=bool(node.get("isArchived", False)),
        is_disabled=bool(node.get("isDisabled", False)),
        stargazer_count=int(node.get("stargazerCount") or 0),
        fork_count=int(node.get("forkCount") or 0),
        pr_updated_at=_last_node_field(node.get("pullRequests"), "updatedAt"),
        issue_updated_at=_last_node_field(node.get("issues"), "updatedAt"),
        release_created_at=_last_node_field(node.get("releases"), "createdAt"),
        default_branch_oid=str(oid) if isinstance(oid, str) and oid else None,
    )


@dataclass(frozen=True)
class _BatchOutcome:
    resolved: dict[str, RepoFingerprint]
    unresolved: list[str]
    gone: frozenset[str]
    cost: int
    remaining: Optional[int]


def _fetch_batch(
    repos: Sequence[tuple[str, str]],
    transport: GraphqlTransport,
    now: datetime,
) -> _BatchOutcome:
    """One aliased query. ``gone`` are aliases GitHub answered ``NOT_FOUND`` for."""
    validated = [_validate_repo_pair(owner, name) for owner, name in repos]
    query = build_fingerprint_batch_query(len(validated))
    variables: dict[str, Any] = {}
    for i, (owner, name) in enumerate(validated):
        variables[f"o{i}"] = owner
        variables[f"n{i}"] = name

    doc = transport.execute(query, variables)
    data = doc.get("data") if isinstance(doc, Mapping) else None
    if not isinstance(data, Mapping):
        errors = doc.get("errors") if isinstance(doc, Mapping) else None
        raise RuntimeError(f"fingerprint batch returned no data: {str(errors)[:160]}")

    rl = data.get("rateLimit") if isinstance(data.get("rateLimit"), Mapping) else {}
    cost = int(rl.get("cost") or 0)
    remaining = int(rl["remaining"]) if rl.get("remaining") is not None else None

    errored_aliases: set[str] = set()
    gone_aliases: set[str] = set()
    for err in doc.get("errors") or []:
        if isinstance(err, Mapping):
            path = err.get("path")
            if isinstance(path, Sequence) and not isinstance(path, str) and path:
                errored_aliases.add(str(path[0]))
                if err.get("type") == "NOT_FOUND":
                    gone_aliases.add(str(path[0]))

    observed_iso = now.isoformat()
    resolved: dict[str, RepoFingerprint] = {}
    unresolved: list[str] = []
    gone: set[str] = set()
    for i, (owner, name) in enumerate(validated):
        alias = f"r{i}"
        key = f"{owner}__{name}"
        node = data.get(alias)
        if not isinstance(node, Mapping) or alias in errored_aliases:
            unresolved.append(key)
            if alias in gone_aliases:
                gone.add(key)
            continue
        resolved[key] = _parse_repo_node(owner, name, node, observed_iso)
    return _BatchOutcome(resolved, unresolved, frozenset(gone), cost, remaining)


def fetch_fingerprint_batch(
    repos: Sequence[tuple[str, str]],
    transport: GraphqlTransport,
    now: datetime,
) -> tuple[dict[str, RepoFingerprint], list[str], int, Optional[int]]:
    """Execute ONE aliased GraphQL fingerprint query over ``repos`` (no retry).

    Returns ``(resolved_by_key, unresolved_keys, cost_points, remaining)``.
    Partially-successful batches salvage every resolved alias (matching the
    transport resilience rule in :mod:`porchlamp.collect.github_graphql`); only
    missing/errored aliases are listed in ``unresolved_keys``. A transport
    failure raises; :func:`fetch_fingerprints_resilient` retries.
    """
    if not repos:
        return {}, [], 0, None
    out = _fetch_batch(repos, transport, now)
    return out.resolved, out.unresolved, out.cost, out.remaining


@dataclass
class _Tally:
    queries: int = 0
    cost: int = 0
    remaining: Optional[int] = None


def _resolve_chunk(
    chunk: Sequence[tuple[str, str]],
    transport: GraphqlTransport,
    now: datetime,
    sleep: Callable[[float], None],
    tally: _Tally,
    resolved: dict[str, RepoFingerprint],
    unresolved: list[str],
) -> None:
    for owner, name in chunk:
        _validate_repo_pair(owner, name)
    outcome: Optional[_BatchOutcome] = None
    for attempt in range(FETCH_ATTEMPTS):
        tally.queries += 1
        try:
            outcome = _fetch_batch(chunk, transport, now)
            break
        except Exception:  # review-ok: gate-network — 5xx/timeout/no body: retry, then report unresolved
            if attempt < FETCH_ATTEMPTS - 1:
                sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
    if outcome is None:
        unresolved.extend(f"{o}__{n}" for o, n in chunk)
        return
    tally.cost += outcome.cost
    if outcome.remaining is not None:
        tally.remaining = outcome.remaining
    resolved.update(outcome.resolved)
    retry = [
        (o, n) for o, n in chunk
        if f"{o}__{n}" in outcome.unresolved and f"{o}__{n}" not in outcome.gone
    ]
    unresolved.extend(k for k in outcome.unresolved if k in outcome.gone)
    if not retry:
        return
    if len(chunk) == 1:
        unresolved.extend(f"{o}__{n}" for o, n in retry)
        return
    # Null aliases (RESOURCE_LIMITS_EXCEEDED and the like): retry the failed
    # repos in two smaller batches.
    mid = (len(retry) + 1) // 2
    for part in (retry[:mid], retry[mid:]):
        if part:
            _resolve_chunk(part, transport, now, sleep, tally, resolved, unresolved)


def fetch_fingerprints_resilient(
    repos: Sequence[tuple[str, str]],
    transport: GraphqlTransport,
    now: datetime,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, RepoFingerprint], list[str], int, int, Optional[int]]:
    """One batch with retries. Returns ``(resolved, unresolved, queries, cost, remaining)``.

    * A transport failure (5xx, timeout, empty answer) is tried
      :data:`FETCH_ATTEMPTS` times with :data:`BACKOFF_SECONDS` between tries.
    * Aliases that came back null (``RESOURCE_LIMITS_EXCEEDED``) are retried in
      two half-size batches, recursively, down to one repository.
    * ``NOT_FOUND`` aliases are final: they are unresolved without a retry.
    * What is still unresolved is returned, never dropped.
    """
    resolved: dict[str, RepoFingerprint] = {}
    unresolved: list[str] = []
    tally = _Tally()
    if repos:
        _resolve_chunk(repos, transport, now, sleep, tally, resolved, unresolved)
    return resolved, unresolved, tally.queries, tally.cost, tally.remaining


def classify_cadence_tier(fp: RepoFingerprint, now: datetime) -> str:
    """Compute the D-COL-3 cadence tier (``hot``, ``warm``, ``cold``, ``dormant``).

    * ``dormant``: ``is_archived`` or ``is_disabled``, or no activity timestamp
      within ``COLD_DAYS`` (365 d).
    * ``hot``: latest activity timestamp within ``HOT_DAYS`` (7 d) of ``now``.
    * ``warm``: latest activity timestamp within ``WARM_DAYS`` (30 d) of ``now``.
    * ``cold``: latest activity timestamp within ``COLD_DAYS`` (365 d) of ``now``.
    """
    if fp.is_archived or fp.is_disabled:
        return "dormant"
    candidates = [
        dt
        for raw in (fp.pushed_at, fp.pr_updated_at, fp.issue_updated_at, fp.release_created_at)
        if (dt := _parse_iso(raw)) is not None
    ]
    if not candidates:
        return "dormant"
    latest = max(candidates)
    age = now - latest
    if age <= timedelta(days=HOT_DAYS):
        return "hot"
    if age <= timedelta(days=WARM_DAYS):
        return "warm"
    if age <= timedelta(days=COLD_DAYS):
        return "cold"
    return "dormant"


def invalidated_collectors(
    previous: Optional[RepoFingerprint],
    current: RepoFingerprint,
) -> frozenset[str]:
    """Return the collector names the fingerprint move invalidates (08 §6).

    When ``previous`` is ``None`` (a newly discovered repository with no prior
    fingerprint), every collector in :data:`ALL_GATHER_COLLECTORS` is
    invalidated. Otherwise each moved field invalidates only its downstream
    collectors per ``08-SUSTAINABLE-COLLECTION.md`` section 6:

    * ``pr_updated_at`` moved -> ``pr_window``, ``author_history``,
      ``pr_events``, ``pr_reviews``, ``github_meta``.
    * ``issue_updated_at`` moved -> ``issues_counts``, ``gfi``.
    * ``pushed_at`` or ``default_branch_oid`` moved -> ``commits_180d``,
      ``commits_365d``, ``runs``, ``actions_runs``, ``github_meta``.
    * ``release_created_at`` moved -> ``releases``.
    * ``stargazer_count`` or ``fork_count`` moved -> ``github_meta``.
    * ``is_archived`` or ``is_disabled`` moved -> ``github_meta``, ``repo_object``.
    * Nothing moved -> empty ``frozenset()``.

    The slow-fact collectors (:data:`SLOW_BRANCH_COLLECTORS`) are not decided
    here: their 30-day clock and branch baseline are per collector, see
    :func:`dirty_collectors`.
    """
    if previous is None:
        return ALL_GATHER_COLLECTORS

    dirty: set[str] = set()
    if current.pr_updated_at != previous.pr_updated_at:
        dirty.update({"github_meta", "pr_events", "pr_reviews", "pr_window", "author_history"})
    if current.issue_updated_at != previous.issue_updated_at:
        dirty.update({"issues_counts", "gfi"})
    if (
        current.pushed_at != previous.pushed_at
        or current.default_branch_oid != previous.default_branch_oid
    ):
        dirty.update({"github_meta", "commits_180d", "commits_365d", "runs", "actions_runs"})
    if current.release_created_at != previous.release_created_at:
        dirty.add("releases")
    if (
        current.stargazer_count != previous.stargazer_count
        or current.fork_count != previous.fork_count
    ):
        dirty.add("github_meta")
    if (
        current.is_archived != previous.is_archived
        or current.is_disabled != previous.is_disabled
    ):
        dirty.update({"github_meta", "repo_object"})
    return frozenset(dirty)


def load_latest_fingerprints(path: Path) -> dict[str, RepoFingerprint]:
    """Read an append-only ``fingerprints.jsonl`` file and return the latest
    :class:`RepoFingerprint` per ``owner__name`` key."""
    if not path.is_file():
        return {}
    latest: dict[str, RepoFingerprint] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            raw = json.loads(text)
            if isinstance(raw, Mapping):
                fp = RepoFingerprint.from_dict(raw)
                latest[fp.key] = fp
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
    return latest


def append_fingerprints(path: Path, fingerprints: Sequence[RepoFingerprint]) -> None:
    """Append ``fingerprints`` to ``path`` (JSONL, one record per line)."""
    if not fingerprints:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = "".join(
        json.dumps(fp.to_dict(), sort_keys=True) + "\n" for fp in fingerprints
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(lines)


def envelope_path(
    raw_dir: Path, collector: str, owner: str, name: str, ecosystem: Optional[str] = None
) -> Optional[Path]:
    """Where ``scripts/gather.py`` writes ``collector``'s envelope for one repo.

    Every collector uses ``<collector>/<owner>__<name>.json`` except
    ``downloads``, which is keyed ``<ecosystem>__<name>``; with no known
    ecosystem that path is unknowable and ``None`` is returned.
    """
    if collector == "downloads":
        if not ecosystem:
            return None
        return raw_dir / "downloads" / f"{ecosystem}__{name}.json"
    return raw_dir / collector / f"{owner}__{name}.json"


def _read_envelope_doc(path: Optional[Path]) -> Optional[Mapping[str, Any]]:
    if path is None or not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, Mapping) else None


def envelope_fetched_at(
    raw_dir: Path, collector: str, owner: str, name: str, ecosystem: Optional[str] = None
) -> Optional[datetime]:
    """``fetched_at`` of an existing, readable envelope; ``None`` when it is
    missing, unreadable or undated."""
    doc = _read_envelope_doc(envelope_path(raw_dir, collector, owner, name, ecosystem))
    return _parse_iso(doc.get("fetched_at")) if doc else None


def _window_anchor(
    raw_dir: Path, collector: str, owner: str, name: str, fallback: datetime
) -> datetime:
    doc = _read_envelope_doc(envelope_path(raw_dir, collector, owner, name))
    data = doc.get("data") if doc else None
    anchor = _parse_iso(data.get("window_anchor")) if isinstance(data, Mapping) else None
    return anchor or fallback


def _branch_moved_since(
    previous: Optional[RepoFingerprint], current: Optional[RepoFingerprint], collector: str
) -> bool:
    """True when the default branch is not known to be where ``collector`` last saw it."""
    if current is None:
        return True
    baseline = _UNKNOWN
    if previous is not None and collector in previous.slow_oids:
        baseline = previous.slow_oids[collector]
    return baseline is _UNKNOWN or baseline != current.default_branch_oid


def _has_dependents_source(ecosystem: Optional[str]) -> bool:
    """False when ``dependents`` can never write an envelope for ``ecosystem``."""
    return ecosystem is None or ecosystem == "systems" or ecosystem in SYSTEM_FOR_ECOSYSTEM


def dirty_collectors(
    previous: Optional[RepoFingerprint],
    current: Optional[RepoFingerprint],
    *,
    raw_dir: Path,
    owner: str,
    name: str,
    now: datetime,
    ecosystem: Optional[str] = None,
) -> frozenset[str]:
    """Every collector that must run again for one repo.

    ``current`` is ``None`` when the fingerprint pass could not resolve the
    repo: nothing is known about it, so every collector that follows
    fingerprint moves is dirty (never "clean"). The rest of the rules apply
    either way:

    * a collector with no readable envelope is dirty;
    * ``depsdev`` / ``scorecard`` / ``downloads`` / ``dependents`` are dirty at
      :data:`WEEKLY_TTL_DAYS` of age;
    * a slow collector is dirty when it is at least :data:`SLOW_FACT_TTL_DAYS`
      old AND the default branch is not where that collector last saw it;
    * ``releases`` / ``issues_counts`` are dirty once their ``window_anchor`` is
      :data:`WINDOW_ANCHOR_REFRESH_DAYS` old.
    """
    dirty: set[str] = set(
        _FAST_COLLECTORS if current is None else invalidated_collectors(previous, current)
    )
    for collector in sorted(ALL_GATHER_COLLECTORS):
        fetched = envelope_fetched_at(raw_dir, collector, owner, name, ecosystem)
        if fetched is None:
            if collector == "dependents" and not _has_dependents_source(ecosystem):
                continue
            if collector == "downloads" and not ecosystem:
                continue
            dirty.add(collector)
            continue
        age = now - fetched
        if collector in SLOW_BRANCH_COLLECTORS:
            if age >= timedelta(days=SLOW_FACT_TTL_DAYS) and _branch_moved_since(
                previous, current, collector
            ):
                dirty.add(collector)
        elif collector in WEEKLY_COLLECTORS:
            if age >= timedelta(days=WEEKLY_TTL_DAYS):
                dirty.add(collector)
        elif collector in WINDOW_ANCHOR_COLLECTORS:
            anchor = _window_anchor(raw_dir, collector, owner, name, fetched)
            if now - anchor >= timedelta(days=WINDOW_ANCHOR_REFRESH_DAYS):
                dirty.add(collector)
    return frozenset(dirty)


def _same_state(a: RepoFingerprint, b: RepoFingerprint) -> bool:
    da, db = a.to_dict(), b.to_dict()
    da.pop("observed_at", None)
    db.pop("observed_at", None)
    return da == db


def fingerprint_to_persist(
    previous: Optional[RepoFingerprint],
    current: RepoFingerprint,
    invalidated: Collection[str],
    fetched_ok: Collection[str],
    failed: Collection[str],
) -> Optional[RepoFingerprint]:
    """The record to append after one repo's gather, or ``None`` to append nothing.

    ``invalidated`` is what the fingerprint move invalidated, ``fetched_ok`` the
    collectors that produced a fresh result in this gather and ``failed`` the
    ones that did not. The new fingerprint is persisted only when no invalidated
    collector failed (otherwise the move would be consumed and the failed
    refetch never retried). A slow collector that was fetched records the
    current oid as its baseline. Returns ``None`` when nothing differs from
    ``previous``, so a clean universe appends nothing.
    """
    if any(c in failed for c in invalidated):
        return None
    slow_oids = dict(previous.slow_oids) if previous is not None else {}
    for collector in SLOW_BRANCH_COLLECTORS & set(fetched_ok):
        slow_oids[collector] = current.default_branch_oid
    candidate = RepoFingerprint(**{**asdict(current), "slow_oids": slow_oids})
    if previous is not None and _same_state(previous, candidate):
        return None
    return candidate


def _scan_prs_for_decided_external(
    prs: Sequence[Any], horizon: datetime, now: datetime
) -> bool:
    for pr in prs:
        if not isinstance(pr, Mapping):
            continue
        is_ext = pr.get("is_external")
        if is_ext is None:
            assoc = str(
                pr.get("author_association") or pr.get("authorAssociation") or "NONE"
            )
            is_ext = assoc not in ("OWNER", "MEMBER", "COLLABORATOR")
        if not is_ext:
            continue
        decided_raw = (
            pr.get("merged_at")
            or pr.get("mergedAt")
            or pr.get("closed_at")
            or pr.get("closedAt")
        )
        decided = _parse_iso(decided_raw) if isinstance(decided_raw, str) else None
        if decided is not None and horizon <= decided <= now:
            return True
    return False


def _has_decided_external_pr_in_raw(
    raw_dir: Path, owner: str, name: str, now: datetime
) -> Optional[bool]:
    """Check recorded ``pr_window`` or ``pr_events`` envelopes for >=1 decided
    external PR within ``GATE6_DECISION_WINDOW_DAYS``. Returns ``None`` when
    no PR envelope is recorded or when a truncated/sample-capped window makes
    absence inconclusive."""
    horizon = now - timedelta(days=GATE6_DECISION_WINDOW_DAYS)
    pr_win_doc = _read_envelope_doc(envelope_path(raw_dir, "pr_window", owner, name))
    if pr_win_doc is not None:
        data = pr_win_doc.get("data")
        truncated = bool(isinstance(data, Mapping) and data.get("truncated") is True)
        prs = (
            data.get("pull_requests")
            if isinstance(data, Mapping) and isinstance(data.get("pull_requests"), Sequence)
            else (
                data.get("events")
                if isinstance(data, Mapping) and isinstance(data.get("events"), Sequence)
                else (data if isinstance(data, Sequence) else None)
            )
        )
        if isinstance(prs, Sequence) and not isinstance(prs, (str, bytes)):
            if _scan_prs_for_decided_external(prs, horizon, now):
                return True
            return None if truncated else False

    pr_events_doc = _read_envelope_doc(envelope_path(raw_dir, "pr_events", owner, name))
    if pr_events_doc is not None:
        ev_data = pr_events_doc.get("data")
        ev_prs = (
            ev_data.get("pull_requests")
            if isinstance(ev_data, Mapping)
            and isinstance(ev_data.get("pull_requests"), Sequence)
            else (ev_data if isinstance(ev_data, Sequence) else None)
        )
        if isinstance(ev_prs, Sequence) and not isinstance(ev_prs, (str, bytes)):
            if _scan_prs_for_decided_external(ev_prs, horizon, now):
                return True
            if len(ev_prs) < 30:
                return False
            return None
    return None


def _has_human_commit_180d(
    raw_dir: Path, owner: str, name: str, now: datetime
) -> Optional[bool]:
    """Return True if ``commits_180d`` records >=1 non-bot commit within
    :data:`STALE_PUSH_WINDOW_DAYS`, False if it is a complete list with 0 qualifying
    human commits, or None when no ``commits_180d`` envelope exists or the 300-commit
    pagination cap was reached inside the 180-day window."""
    doc = _read_envelope_doc(envelope_path(raw_dir, "commits_180d", owner, name))
    if doc is None:
        return None
    data = doc.get("data")
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
        return None
    horizon = now - timedelta(days=STALE_PUSH_WINDOW_DAYS)
    oldest_dt: Optional[datetime] = None
    for item in data:
        if not isinstance(item, Mapping):
            continue
        dt_raw = item.get("date") or item.get("committed_date")
        dt = _parse_iso(dt_raw) if isinstance(dt_raw, str) else None
        if dt is not None and (oldest_dt is None or dt < oldest_dt):
            oldest_dt = dt
        login = str(item.get("author_login") or "")
        typename = str(item.get("author_type") or "")
        if is_bot_actor(typename, login):
            continue
        if dt is None or horizon <= dt <= now:
            return True
    if len(data) >= 300 and (oldest_dt is None or oldest_dt >= horizon):
        return None
    return False


def classify_prune_reason(
    fp: Optional[RepoFingerprint],
    *,
    raw_dir: Optional[Path] = None,
    owner: str,
    name: str,
    now: datetime,
) -> Optional[str]:
    """Classify whether ``owner/name`` should be pruned before running the
    20-collector gather pipeline.

    Returns one of :data:`PRUNED_REASONS` (``"archived"``, ``"disabled"``,
    ``"mirror"``, ``"stale"``, ``"unlicensed"``, ``"non_software"``,
    ``"no_external_workflow"``) when the fingerprint or existing raw envelopes
    establish that the repository is closed for contributions or not software,
    or ``None`` when the repository remains a valid candidate.

    Note: ``black_hole`` repositories are intentionally **never** pruned here —
    they are active software repositories with external PR workflows whose PR
    ghost rate is scored and ranked on the leaderboard with a visible
    ``"black_hole"`` flag.
    """
    if fp is not None:
        if fp.is_archived:
            return "archived"
        if fp.is_disabled:
            return "disabled"
        pushed = _parse_iso(fp.pushed_at)
        if pushed is not None and (now - pushed) > timedelta(days=STALE_PUSH_WINDOW_DAYS):
            return "stale"

    if raw_dir is not None:
        ro_doc = _read_envelope_doc(envelope_path(raw_dir, "repo_object", owner, name))
        meta_doc = _read_envelope_doc(envelope_path(raw_dir, "github_meta", owner, name))
        tree_doc = _read_envelope_doc(envelope_path(raw_dir, "tree", owner, name))
        ro_data = ro_doc.get("data") if isinstance(ro_doc, Mapping) and isinstance(ro_doc.get("data"), Mapping) else None
        meta_data = meta_doc.get("data") if isinstance(meta_doc, Mapping) and isinstance(meta_doc.get("data"), Mapping) else None
        tree_data = tree_doc.get("data") if isinstance(tree_doc, Mapping) and isinstance(tree_doc.get("data"), Mapping) else None

        if ro_data is not None or meta_data is not None or tree_data is not None:
            elig = derive_eligibility(
                dict(ro_data) if ro_data is not None else None,
                dict(meta_data) if meta_data is not None else None,
                dict(tree_data) if tree_data is not None else None,
            )
            if elig["archived"][0]:
                return "archived"
            if elig["mirror"][0]:
                return "mirror"
            if fp is None and meta_data is not None:
                meta_pushed = _parse_iso(meta_data.get("pushed_at"))
                if meta_pushed is not None and (now - meta_pushed) > timedelta(days=STALE_PUSH_WINDOW_DAYS):
                    return "stale"
            spdx = (
                ro_data.get("license_spdx_id")
                if ro_data is not None
                else (meta_data.get("license_spdx") if meta_data is not None else None)
            )
            if (
                isinstance(spdx, str)
                and spdx not in UNDETERMINED_SPDX
                and _is_parseable_spdx_expression(spdx)
                and not _is_osi_approved_expression(spdx)
            ):
                return "unlicensed"
            if elig["unlicensed"][0]:
                return "unlicensed"
            if elig["non_software"][0]:
                return "non_software"
            off_val, off_conf, _ = elig["off_platform"]
            if elig["no_prs_ever"][0] and not off_val:
                return "no_external_workflow"
            if not off_val and off_conf != "missing":
                has_decided = _has_decided_external_pr_in_raw(raw_dir, owner, name, now)
                if has_decided is False:
                    return "no_external_workflow"

        if _has_human_commit_180d(raw_dir, owner, name, now) is False:
            return "stale"

    return None


def run_fingerprint_pass(
    repos: Sequence[tuple[str, str]],
    *,
    state_file: Path,
    raw_dir: Optional[Path] = None,
    transport: Optional[GraphqlTransport] = None,
    now: Optional[datetime] = None,
    batch_size: int = FINGERPRINT_BATCH_SIZE,
    ecosystems: Optional[Mapping[str, str]] = None,
    sleep: Callable[[float], None] = time.sleep,
    prune_ineligible: bool = False,
) -> FingerprintSweepResult:
    """Run the D-COL-1 bulk fingerprint pass over ``repos``. Writes nothing.

    1. Loads the latest persisted fingerprint per repo from ``state_file``.
    2. Queries ``repos`` in chunks of ``batch_size`` (default 25) via
       :func:`fetch_fingerprints_resilient`.
    3. Computes each resolved repo's D-COL-3 cadence tier and its
       invalidated/dirty collector sets. When ``raw_dir`` is given the dirty set
       also covers missing envelopes, clocks and window anchors
       (:func:`dirty_collectors`); an unresolved repo is dirty too.
    4. When ``prune_ineligible=True``, any repository classified by
       :func:`classify_prune_reason` as ``archived``, ``disabled``, ``stale``,
       ``unlicensed``, ``non_software``, or ``no_external_workflow`` is recorded
       in ``pruned_by_repo`` and assigned an empty ``dirty_by_repo`` set so zero
       downstream collectors waste rate limits on it.

    The pass neither appends to ``state_file`` nor touches envelopes: the caller
    persists a repo's fingerprint with :func:`fingerprint_to_persist` after its
    collectors succeeded. ``ecosystems`` maps ``owner__name`` to its ecosystem
    (the ``downloads`` envelope key needs it).
    """
    if batch_size <= 0:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    now = now or datetime.now(timezone.utc)
    transport = transport or GhCliTransport()
    previous = load_latest_fingerprints(state_file)

    deduped: list[tuple[str, str]] = list(dict.fromkeys(repos))
    current: dict[str, RepoFingerprint] = {}
    unresolved: list[str] = []
    # A slug GitHub cannot hold is unresolved (dirty), not a reason to abort the pass.
    queryable: list[tuple[str, str]] = []
    for owner, name in deduped:
        try:
            queryable.append(_validate_repo_pair(owner, name))
        except ValueError:
            unresolved.append(f"{owner}__{name}")
    total_cost = 0
    remaining: Optional[int] = None
    queries = 0

    for start in range(0, len(queryable), batch_size):
        chunk = queryable[start : start + batch_size]
        resolved, chunk_unresolved, n_queries, cost, rem = fetch_fingerprints_resilient(
            chunk, transport, now, sleep=sleep
        )
        queries += n_queries
        total_cost += cost
        if rem is not None:
            remaining = rem
        current.update(resolved)
        unresolved.extend(chunk_unresolved)

    pair_by_key = {f"{o}__{n}": (o, n) for o, n in deduped}
    eco = ecosystems or {}
    invalidated_by_repo: dict[str, frozenset[str]] = {}
    dirty_by_repo: dict[str, frozenset[str]] = {}
    tiers_by_repo: dict[str, str] = {}
    pruned_by_repo: dict[str, str] = {}
    for key, fp in current.items():
        tiers_by_repo[key] = classify_cadence_tier(fp, now)
        if prune_ineligible:
            reason = classify_prune_reason(
                fp, raw_dir=raw_dir, owner=fp.owner, name=fp.name, now=now
            )
            if reason is not None:
                pruned_by_repo[key] = reason
                invalidated_by_repo[key] = frozenset()
                dirty_by_repo[key] = frozenset()
                continue
        inv = invalidated_collectors(previous.get(key), fp)
        invalidated_by_repo[key] = inv
        dirty_by_repo[key] = (
            dirty_collectors(
                previous.get(key), fp, raw_dir=raw_dir, owner=fp.owner, name=fp.name,
                now=now, ecosystem=eco.get(key),
            )
            if raw_dir is not None
            else inv
        )
    for key in unresolved:
        owner, name = pair_by_key[key]
        if prune_ineligible:
            reason = classify_prune_reason(
                previous.get(key), raw_dir=raw_dir, owner=owner, name=name, now=now
            )
            if reason is not None:
                pruned_by_repo[key] = reason
                invalidated_by_repo[key] = frozenset()
                dirty_by_repo[key] = frozenset()
                continue
        invalidated_by_repo[key] = frozenset()
        dirty_by_repo[key] = (
            dirty_collectors(
                previous.get(key), None, raw_dir=raw_dir, owner=owner, name=name,
                now=now, ecosystem=eco.get(key),
            )
            if raw_dir is not None
            else _FAST_COLLECTORS
        )

    return FingerprintSweepResult(
        fingerprints=current,
        previous=previous,
        invalidated_by_repo=invalidated_by_repo,
        dirty_by_repo=dirty_by_repo,
        tiers_by_repo=tiers_by_repo,
        unresolved_keys=tuple(unresolved),
        queries_executed=queries,
        cost_points=total_cost,
        rate_limit_remaining=remaining,
        pruned_by_repo=pruned_by_repo,
    )
