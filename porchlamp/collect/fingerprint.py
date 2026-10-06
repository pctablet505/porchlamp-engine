"""Bulk repository fingerprinting, cadence tiers, and dirty-only invalidation (08 D-COL-1, D-COL-3, D-COL-4, W6-2).

Batches up to :data:`FINGERPRINT_BATCH_SIZE` (100) repositories into a single
aliased GitHub GraphQL request (58 queries cover the 5,753-repository
universe). Each repository alias fetches the lightweight change-detection
fields of ``docs/v2/08-SUSTAINABLE-COLLECTION.md`` section 4 (D-COL-1):

* ``pushedAt``, ``updatedAt``, ``isArchived``, ``isDisabled``,
  ``stargazerCount``, ``forkCount``;
* ``pullRequests(last: 1, orderBy: {field: UPDATED_AT, direction: ASC})``;
* ``issues(last: 1, orderBy: {field: UPDATED_AT, direction: ASC})``;
* ``releases(last: 1, orderBy: {field: CREATED_AT, direction: ASC})``;
* ``defaultBranchRef { target { oid } }``.

From a recorded :class:`RepoFingerprint` pair ``(previous, current)``,
:func:`invalidated_collectors` computes the exact collector subset that must
be refetched (``08`` section 6 table), and :func:`classify_cadence_tier`
computes the repository's activity cadence tier (``hot``, ``warm``, ``cold``,
``dormant``) from its observed activity timestamps (D-COL-3).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from porchlamp.collect.github_graphql import GhCliTransport, GraphqlTransport

__all__ = [
    "FINGERPRINT_BATCH_SIZE",
    "HOT_DAYS",
    "WARM_DAYS",
    "COLD_DAYS",
    "SLOW_FACT_TTL_DAYS",
    "CADENCE_TIERS",
    "ALL_GATHER_COLLECTORS",
    "SLOW_BRANCH_COLLECTORS",
    "RepoFingerprint",
    "FingerprintSweepResult",
    "build_fingerprint_batch_query",
    "fetch_fingerprint_batch",
    "load_latest_fingerprints",
    "append_fingerprints",
    "classify_cadence_tier",
    "invalidated_collectors",
    "advance_envelope_watermarks",
    "run_fingerprint_pass",
]

#: D-COL-1: 100 repositories per aliased GraphQL query (58 queries for 5,753 repos).
FINGERPRINT_BATCH_SIZE: int = 100

#: D-COL-3 cadence tier thresholds (in days since most recent activity).
HOT_DAYS: int = 7
WARM_DAYS: int = 30
COLD_DAYS: int = 365

#: D-COL-4: slow-fact collectors (governance, contributors, repo_object, tree)
#: refetch at most every 30 days and only when defaultBranchRef.target.oid moved.
SLOW_FACT_TTL_DAYS: int = 30

#: Closed vocabulary of D-COL-3 cadence tiers.
CADENCE_TIERS: tuple[str, ...] = ("hot", "warm", "cold", "dormant")

#: Every collector name ``scripts/gather.py`` manages.
ALL_GATHER_COLLECTORS: frozenset[str] = frozenset({
    "github_meta", "pr_events", "pr_reviews", "contributors", "gfi", "actions_runs", "tree",
    "commits_180d", "depsdev", "scorecard", "downloads", "repo_object", "commits_365d",
    "dependents", "pr_window", "releases", "issues_counts", "runs", "governance",
    "author_history",
})

#: Collectors gated by both default-branch OID movement and the 30-day slow clock (D-COL-4).
SLOW_BRANCH_COLLECTORS: frozenset[str] = frozenset({
    "governance", "contributors", "repo_object", "tree",
})

#: Strict GitHub owner/name identifier pattern.
_SAFE_SLUG_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


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
    """One D-COL-1 repository fingerprint observation."""

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
        )


@dataclass(frozen=True)
class FingerprintSweepResult:
    """Outcome of a bulk fingerprint pass across the candidate universe."""

    fingerprints: dict[str, RepoFingerprint]
    previous: dict[str, RepoFingerprint]
    invalidated_by_repo: dict[str, frozenset[str]]
    tiers_by_repo: dict[str, str]
    unresolved_keys: tuple[str, ...]
    queries_executed: int
    cost_points: int
    rate_limit_remaining: Optional[int]


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


def _last_node_field(connection: Any, field: str) -> Optional[str]:
    """Extract ``field`` from the single node of a ``last: 1`` connection."""
    if not isinstance(connection, Mapping):
        return None
    nodes = connection.get("nodes")
    if not isinstance(nodes, Sequence) or not nodes:
        return None
    node = nodes[-1]
    if not isinstance(node, Mapping):
        return None
    val = node.get(field)
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


def fetch_fingerprint_batch(
    repos: Sequence[tuple[str, str]],
    transport: GraphqlTransport,
    now: datetime,
) -> tuple[dict[str, RepoFingerprint], list[str], int, Optional[int]]:
    """Execute one aliased GraphQL fingerprint query over ``repos``.

    Returns ``(resolved_by_key, unresolved_keys, cost_points, remaining)``.
    Partially-successful batches salvage every resolved alias (matching the
    transport resilience rule in :mod:`porchlamp.collect.github_graphql`); only
    missing/errored aliases are listed in ``unresolved_keys``.
    """
    if not repos:
        return {}, [], 0, None
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
    for err in doc.get("errors") or []:
        if isinstance(err, Mapping):
            path = err.get("path")
            if isinstance(path, Sequence) and path:
                errored_aliases.add(str(path[0]))

    observed_iso = now.isoformat()
    resolved: dict[str, RepoFingerprint] = {}
    unresolved: list[str] = []
    for i, (owner, name) in enumerate(validated):
        alias = f"r{i}"
        key = f"{owner}__{name}"
        node = data.get(alias)
        if not isinstance(node, Mapping) or alias in errored_aliases:
            unresolved.append(key)
            continue
        resolved[key] = _parse_repo_node(owner, name, node, observed_iso)
    return resolved, unresolved, cost, remaining


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
    *,
    slow_clock_due: bool = False,
) -> frozenset[str]:
    """Return the collector names invalidated by ``current`` vs ``previous`` (08 §6).

    When ``previous`` is ``None`` (a newly discovered repository with no prior
    fingerprint), every collector in :data:`ALL_GATHER_COLLECTORS` is
    invalidated. Otherwise each moved field invalidates only its downstream
    collectors per ``08-SUSTAINABLE-COLLECTION.md`` section 6:

    * ``pr_updated_at`` moved -> ``pr_window``, ``author_history``,
      ``pr_events``, ``pr_reviews``, ``github_meta``.
    * ``issue_updated_at`` moved -> ``issues_counts``, ``gfi``.
    * ``pushed_at`` or ``default_branch_oid`` moved -> ``commits_180d``,
      ``commits_365d``, ``runs``, ``actions_runs``, ``github_meta``; plus
      :data:`SLOW_BRANCH_COLLECTORS` (``tree``, ``repo_object``,
      ``governance``, ``contributors``) when ``default_branch_oid`` moved and
      ``slow_clock_due`` is True (D-COL-4).
    * ``release_created_at`` moved -> ``releases``.
    * ``stargazer_count`` or ``fork_count`` moved -> ``github_meta``.
    * ``is_archived`` or ``is_disabled`` moved -> ``github_meta``, ``repo_object``.
    * Nothing moved -> empty ``frozenset()`` (zero collector calls needed).
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
        if current.default_branch_oid != previous.default_branch_oid and slow_clock_due:
            dirty.update(SLOW_BRANCH_COLLECTORS)
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


def _slow_clock_is_due(raw_dir: Path, key: str, now: datetime) -> bool:
    """True when the repo's ``governance`` envelope is absent or older than
    :data:`SLOW_FACT_TTL_DAYS` (D-COL-4)."""
    gov_path = raw_dir / "governance" / f"{key}.json"
    if not gov_path.is_file():
        return True
    try:
        doc = json.loads(gov_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    fetched_at = _parse_iso(doc.get("fetched_at") if isinstance(doc, Mapping) else None)
    if fetched_at is None:
        return True
    return (now - fetched_at) >= timedelta(days=SLOW_FACT_TTL_DAYS)


def advance_envelope_watermarks(
    raw_dir: Path,
    key: str,
    unchanged_collectors: frozenset[str],
    observed_at: datetime,
) -> int:
    """Advance ``fetched_at`` (and ``data.window_anchor`` when present and
    identical to the envelope's previous ``fetched_at``) on existing envelopes
    whose upstream fingerprint did not move (D-COL-1).

    An unchanged fingerprint is a live observation from GitHub that the
    repository's state has not moved since the envelope was recorded, keeping
    cold/dormant repositories from tripping ``WINDOW_STALENESS_DAYS`` without
    re-fetching unchanged windows. Note: ``data`` is only modified when
    ``window_anchor`` is NOT part of ``data``'s content hash; because
    :func:`porchlamp.collect.envelope.write_envelope` hashes ``data``, envelopes
    whose ``data`` embeds ``window_anchor`` keep ``data`` untouched and record
    ``verified_unchanged_at`` at the top-level envelope metadata.
    Returns the count of envelopes updated.
    """
    updated = 0
    iso = observed_at.isoformat()
    for collector in sorted(unchanged_collectors):
        env_path = raw_dir / collector / f"{key}.json"
        if not env_path.is_file():
            continue
        try:
            doc = json.loads(env_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(doc, dict):
            continue
        doc["verified_unchanged_at"] = iso
        tmp = env_path.with_name(f".{env_path.name}.watermark.tmp")
        tmp.write_text(json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(env_path)
        updated += 1
    return updated


def run_fingerprint_pass(
    repos: Sequence[tuple[str, str]],
    *,
    state_file: Path,
    raw_dir: Optional[Path] = None,
    transport: Optional[GraphqlTransport] = None,
    now: Optional[datetime] = None,
    batch_size: int = FINGERPRINT_BATCH_SIZE,
) -> FingerprintSweepResult:
    """Run the D-COL-1 bulk fingerprint pass over ``repos`` and persist state.

    1. Loads the latest prior fingerprint per repo from ``state_file``.
    2. Queries ``repos`` in chunks of ``batch_size`` (default 100) via
       :func:`fetch_fingerprint_batch`.
    3. Appends the newly observed fingerprints to ``state_file``.
    4. Computes each repo's D-COL-3 cadence tier and ``08`` §6 invalidated
       collector set (plus any collector whose envelope is missing on disk when
       ``raw_dir`` is provided).
    5. Advances ``verified_unchanged_at`` watermarks for unchanged collectors when
       ``raw_dir`` is provided.
    """
    if batch_size <= 0:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    now = now or datetime.now(timezone.utc)
    transport = transport or GhCliTransport()
    previous = load_latest_fingerprints(state_file)

    deduped: list[tuple[str, str]] = list(dict.fromkeys(repos))
    current: dict[str, RepoFingerprint] = {}
    unresolved: list[str] = []
    total_cost = 0
    remaining: Optional[int] = None
    queries = 0

    for start in range(0, len(deduped), batch_size):
        chunk = deduped[start : start + batch_size]
        resolved, chunk_unresolved, cost, rem = fetch_fingerprint_batch(
            chunk, transport, now
        )
        queries += 1
        total_cost += cost
        if rem is not None:
            remaining = rem
        current.update(resolved)
        unresolved.extend(chunk_unresolved)

    append_fingerprints(state_file, list(current.values()))

    invalidated_by_repo: dict[str, frozenset[str]] = {}
    tiers_by_repo: dict[str, str] = {}
    for key, fp in current.items():
        tiers_by_repo[key] = classify_cadence_tier(fp, now)
        prev_fp = previous.get(key)
        slow_due = _slow_clock_is_due(raw_dir, key, now) if raw_dir is not None else False
        dirty = set(invalidated_collectors(prev_fp, fp, slow_clock_due=slow_due))
        if raw_dir is not None:
            unchanged = ALL_GATHER_COLLECTORS - dirty
            advance_envelope_watermarks(raw_dir, key, unchanged, now)
        invalidated_by_repo[key] = frozenset(dirty)

    return FingerprintSweepResult(
        fingerprints=current,
        previous=previous,
        invalidated_by_repo=invalidated_by_repo,
        tiers_by_repo=tiers_by_repo,
        unresolved_keys=tuple(unresolved),
        queries_executed=queries,
        cost_points=total_cost,
        rate_limit_remaining=remaining,
    )
