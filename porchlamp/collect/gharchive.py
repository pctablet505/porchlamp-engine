"""GH Archive bulk public-event stream parser, ledger replay, and cross-check detector (08 D-COL-11, W6-10).

Filters hourly GH Archive dumps (``https://data.gharchive.org/YYYY-MM-DD-H.json.gz``)
against the Porchlamp candidate universe with zero GitHub API quota, appends
versioned records to ``<data_root>/ledgers/<ledger>/<owner>__<name>.jsonl``
stamped with ``observed_at`` = the END of the dump hour (the moment the hour's
events were complete), and runs the
:func:`ledger_vs_archive` cross-check detector over any time window to flag
missing events or count divergences between live collection and the bulk
archive.

GH Archive trims event payloads (a ``PushEvent`` carries no ``commits``; a
``PullRequestEvent`` carries no PR state or dates). A record built from such a
payload would hold nulls and False defaults that look like facts, so
:func:`shape_gharchive_event` raises :class:`IncompletePayloadError` for an
in-universe event that lacks the fields its ledger record needs, and the replay
refuses instead of writing it.
"""

from __future__ import annotations

import gzip
import io
import json
import re
import urllib.request
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from porchlamp.collect import net

__all__ = [
    "GHARCHIVE_BASE_URL",
    "MAX_ARCHIVE_DECOMPRESSED_BYTES",
    "SUPPORTED_EVENT_TYPES",
    "LEDGER_NAMES",
    "IncompletePayloadError",
    "ArchiveLedgerRecord",
    "ArchiveDiscrepancy",
    "ReplayReport",
    "hour_start",
    "hour_to_observed_at",
    "iter_hours",
    "archive_hour_url",
    "shape_gharchive_event",
    "iter_jsonl_bytes",
    "fetch_gharchive_hour_bytes",
    "append_ledger_records",
    "load_ledger_records",
    "replay_hour_stream",
    "ledger_vs_archive",
]

#: Official GH Archive HTTPS base URL.
GHARCHIVE_BASE_URL: str = "https://data.gharchive.org"

#: Hard decompression ceiling (256 MiB) per hourly archive stream to guard against gzip bombs.
MAX_ARCHIVE_DECOMPRESSED_BYTES: int = 256 * 1024 * 1024

#: Public GitHub event types mapped into Porchlamp activity ledgers.
SUPPORTED_EVENT_TYPES: frozenset[str] = frozenset({
    "PullRequestEvent",
    "PullRequestReviewEvent",
    "PullRequestReviewCommentEvent",
    "IssuesEvent",
    "PushEvent",
    "ReleaseEvent",
})

#: Closed set of ledger subdirectory names under ``<data_root>/ledgers/``.
LEDGER_NAMES: tuple[str, ...] = ("prs", "issues", "commits", "releases")

_HOUR_SPEC_RE: re.Pattern[str] = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})-(?P<hour>\d{1,2})$"
)
_SAFE_SLUG_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_ALLOWED_ARCHIVE_URL_RE: re.Pattern[str] = re.compile(
    r"^https://data\.gharchive\.org/\d{4}-\d{2}-\d{2}-\d{1,2}\.json\.gz$"
)
_USER_AGENT: str = "porchlamp-research/0.1 (ranking study)"


def _validate_repo_pair(owner: str, name: str) -> tuple[str, str]:
    if not _SAFE_SLUG_RE.fullmatch(owner) or not _SAFE_SLUG_RE.fullmatch(name):
        raise ValueError(f"invalid repository slug: {owner!r}/{name!r}")
    if owner in (".", "..") or name in (".", ".."):
        raise ValueError(f"invalid repository path segment: {owner!r}/{name!r}")
    return owner, name


class IncompletePayloadError(ValueError):
    """An archive event for a universe repo lacks the fields its ledger record needs."""


def _require(event: Mapping[str, Any], container: Any, what: str, keys: Sequence[str],
             nullable: Sequence[str] = ()) -> None:
    """Raise :class:`IncompletePayloadError` unless ``container`` is a mapping holding every
    key with a value (a key in ``nullable`` may hold ``None``: a PR not yet merged)."""
    missing = [
        k for k in keys
        if not isinstance(container, Mapping) or k not in container
        or (container[k] is None and k not in nullable)
    ]
    if missing:
        raise IncompletePayloadError(
            f"GH Archive event {event.get('id')!r} ({event.get('type')}) for "
            f"{(event.get('repo') or {}).get('name')!r} lacks {what} field(s) {missing}: "
            "GH Archive trims event payloads, and a ledger record built from them would "
            "hold nulls as facts; refusing to write it"
        )


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def hour_start(hour_spec: str) -> datetime:
    """Parse a GH Archive ``YYYY-MM-DD-H`` hour string into the UTC START of that hour.

    Each GH Archive dump ``YYYY-MM-DD-H.json.gz`` covers ``[H:00:00, H+1:00:00)`` UTC.
    """
    match = _HOUR_SPEC_RE.fullmatch(hour_spec.strip())
    if not match:
        raise ValueError(f"invalid GH Archive hour spec {hour_spec!r}; expected YYYY-MM-DD-H")
    year = int(match.group("year"))
    month = int(match.group("month"))
    day = int(match.group("day"))
    hour = int(match.group("hour"))
    if not (0 <= hour <= 23):
        raise ValueError(f"hour must be in 0..23, got {hour}")
    return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc)


def hour_to_observed_at(hour_spec: str) -> datetime:
    """The UTC END of a GH Archive hour: its canonical ``observed_at``.

    The dump ``YYYY-MM-DD-H.json.gz`` covers ``[H:00:00, H+1:00:00)`` and is only
    complete at ``H+1:00:00`` (``2026-09-11-14`` -> ``2026-09-11T15:00:00+00:00``;
    hour 23 ends at 00:00 of the next day). A record observed at the hour's start
    would predate the events it holds.
    """
    return hour_start(hour_spec) + timedelta(hours=1)


def iter_hours(start_spec: str, end_spec: str) -> Iterator[str]:
    """Every hour spec from ``start_spec`` to ``end_spec``, both inclusive, oldest first."""
    cursor = hour_start(start_spec)
    last = hour_start(end_spec)
    if last < cursor:
        raise ValueError(f"end hour {end_spec!r} is before start hour {start_spec!r}")
    while cursor <= last:
        yield f"{cursor.year:04d}-{cursor.month:02d}-{cursor.day:02d}-{cursor.hour}"
        cursor += timedelta(hours=1)


def archive_hour_url(hour_spec: str) -> str:
    """Build and validate the canonical ``https://data.gharchive.org/YYYY-MM-DD-H.json.gz`` URL."""
    dt = hour_start(hour_spec)
    # GH Archive uses non-zero-padded hour (0..23) in filenames.
    url = f"{GHARCHIVE_BASE_URL}/{dt.year:04d}-{dt.month:02d}-{dt.day:02d}-{dt.hour}.json.gz"
    if not _ALLOWED_ARCHIVE_URL_RE.fullmatch(url):
        raise ValueError(f"refusing non-canonical GH Archive URL: {url!r}")
    return url


@dataclass(frozen=True)
class ArchiveLedgerRecord:
    """One versioned ledger entry extracted from a GH Archive public event."""

    ledger: str
    owner: str
    name: str
    object_id: str
    event_id: str
    event_type: str
    action: Optional[str]
    actor_login: str
    created_at: Optional[str]
    observed_at: str
    payload_summary: dict[str, Any]

    @property
    def repo_key(self) -> str:
        """Canonical ``owner__name`` repository key."""
        return f"{self.owner}__{self.name}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ArchiveLedgerRecord:
        """Reconstruct an :class:`ArchiveLedgerRecord` from a parsed JSON mapping."""
        owner, name = _validate_repo_pair(str(raw["owner"]), str(raw["name"]))
        ledger = str(raw["ledger"])
        if ledger not in LEDGER_NAMES:
            raise ValueError(f"unknown ledger {ledger!r}")
        return cls(
            ledger=ledger,
            owner=owner,
            name=name,
            object_id=str(raw["object_id"]),
            event_id=str(raw["event_id"]),
            event_type=str(raw["event_type"]),
            action=str(raw["action"]) if raw.get("action") is not None else None,
            actor_login=str(raw.get("actor_login") or ""),
            created_at=str(raw["created_at"]) if raw.get("created_at") is not None else None,
            observed_at=str(raw["observed_at"]),
            payload_summary=dict(raw.get("payload_summary") or {}),
        )


@dataclass(frozen=True)
class ArchiveDiscrepancy:
    """One ``ledger_vs_archive`` discrepancy for a ``(repo_key, ledger)`` pair."""

    repo_key: str
    ledger: str
    ledger_count: int
    archive_count: int
    missing_in_ledger: tuple[str, ...]
    extra_in_ledger: tuple[str, ...]


@dataclass(frozen=True)
class ReplayReport:
    """Summary of one GH Archive stream replay."""

    hour_spec: str
    observed_at: str
    events_scanned: int
    events_matched: int
    records_appended: int
    repos_touched: int


def shape_gharchive_event(
    event: Mapping[str, Any],
    universe_lookup: Mapping[str, tuple[str, str]],
    observed_at: str,
) -> list[ArchiveLedgerRecord]:
    """Convert one raw GH Archive event dict into 0+ :class:`ArchiveLedgerRecord` items.

    ``universe_lookup`` maps lowercased ``"owner/name"`` to canonical
    ``(owner, name)``. Events for repositories outside ``universe_lookup`` or
    event types outside :data:`SUPPORTED_EVENT_TYPES` return ``[]``. An in-universe
    event whose payload lacks the fields its record needs raises
    :class:`IncompletePayloadError` (a record of nulls would read as fact).
    """
    event_type = str(event.get("type") or "")
    if event_type not in SUPPORTED_EVENT_TYPES:
        return []
    repo_block = event.get("repo")
    if not isinstance(repo_block, Mapping):
        return []
    repo_full = str(repo_block.get("name") or "").strip().lower()
    canonical = universe_lookup.get(repo_full)
    if canonical is None:
        return []
    owner, name = canonical

    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        _require(event, None, "event", ["payload"])  # raises: nothing to build a record from
    actor = event.get("actor")
    actor_login = str(actor.get("login") or "") if isinstance(actor, Mapping) else ""
    event_id = str(event.get("id") or "")
    created_at = str(event["created_at"]) if isinstance(event.get("created_at"), str) else None
    action = str(payload["action"]) if isinstance(payload.get("action"), str) else None

    if event_type in (
        "PullRequestEvent",
        "PullRequestReviewEvent",
        "PullRequestReviewCommentEvent",
    ):
        pr = payload.get("pull_request")
        _require(event, pr, "pull_request", ["number"])
        pr_number = int(pr["number"])
        if event_type == "PullRequestEvent":
            _require(event, pr, "pull_request", ["state", "created_at"])
            if "merged" not in pr and "merged_at" not in pr:
                _require(event, pr, "pull_request", ["merged"])
        review = payload.get("review") if isinstance(payload.get("review"), Mapping) else {}
        comment = payload.get("comment") if isinstance(payload.get("comment"), Mapping) else {}
        if event_type == "PullRequestReviewEvent":
            _require(event, review, "review", ["state"])
        elif event_type == "PullRequestReviewCommentEvent":
            _require(event, comment, "comment", ["body"], nullable=["body"])
        summary: dict[str, Any] = {
            "number": pr_number,
            "state": pr.get("state"),
            "merged": bool(pr.get("merged")) if pr.get("merged") is not None else (pr.get("merged_at") is not None),
            "created_at": pr.get("created_at"),
            "updated_at": pr.get("updated_at"),
            "closed_at": pr.get("closed_at"),
            "merged_at": pr.get("merged_at"),
        }
        if event_type == "PullRequestReviewEvent":
            summary["review_state"] = review.get("state")
            summary["review_body_len"] = len(str(review.get("body") or ""))
        elif event_type == "PullRequestReviewCommentEvent":
            summary["comment_body_len"] = len(str(comment.get("body") or ""))
        return [
            ArchiveLedgerRecord(
                ledger="prs",
                owner=owner,
                name=name,
                object_id=str(pr_number),
                event_id=event_id,
                event_type=event_type,
                action=action,
                actor_login=actor_login,
                created_at=created_at,
                observed_at=observed_at,
                payload_summary=summary,
            )
        ]

    if event_type == "IssuesEvent":
        issue = payload.get("issue")
        _require(event, issue, "issue", ["number"])
        if issue.get("pull_request") is not None:
            return []
        _require(event, issue, "issue", ["state", "updated_at"])
        issue_number = int(issue["number"])
        return [
            ArchiveLedgerRecord(
                ledger="issues",
                owner=owner,
                name=name,
                object_id=str(issue_number),
                event_id=event_id,
                event_type=event_type,
                action=action,
                actor_login=actor_login,
                created_at=created_at,
                observed_at=observed_at,
                payload_summary={
                    "number": issue_number,
                    "state": issue.get("state"),
                    "comments": int(issue.get("comments") or 0),
                    "updated_at": issue.get("updated_at"),
                    "closed_at": issue.get("closed_at"),
                },
            )
        ]

    if event_type == "PushEvent":
        _require(event, payload, "payload", ["commits"])
        commits = payload.get("commits")
        if not isinstance(commits, Sequence) or not commits:
            head = str(payload.get("head") or "")
            if not head:
                return []
            return [
                ArchiveLedgerRecord(
                    ledger="commits",
                    owner=owner,
                    name=name,
                    object_id=head,
                    event_id=event_id,
                    event_type=event_type,
                    action="push",
                    actor_login=actor_login,
                    created_at=created_at,
                    observed_at=observed_at,
                    payload_summary={"sha": head, "ref": payload.get("ref")},
                )
            ]
        records: list[ArchiveLedgerRecord] = []
        for idx, commit in enumerate(commits):
            if not isinstance(commit, Mapping):
                continue
            sha = str(commit.get("sha") or "")
            if not sha:
                continue
            c_author = commit.get("author") if isinstance(commit.get("author"), Mapping) else {}
            msg = str(commit.get("message") or "")
            records.append(
                ArchiveLedgerRecord(
                    ledger="commits",
                    owner=owner,
                    name=name,
                    object_id=sha,
                    event_id=f"{event_id}:{idx}" if event_id else sha,
                    event_type=event_type,
                    action="push",
                    actor_login=actor_login,
                    created_at=created_at,
                    observed_at=observed_at,
                    payload_summary={
                        "sha": sha,
                        "author_email": str(c_author.get("email") or ""),
                        "signed_off": "Signed-off-by:" in msg,
                    },
                )
            )
        return records

    if event_type == "ReleaseEvent":
        release = payload.get("release")
        _require(event, release, "release", ["published_at"], nullable=["published_at"])
        tag = str(release.get("tag_name") or release.get("id") or "")
        if not tag:
            return []
        return [
            ArchiveLedgerRecord(
                ledger="releases",
                owner=owner,
                name=name,
                object_id=tag,
                event_id=event_id,
                event_type=event_type,
                action=action,
                actor_login=actor_login,
                created_at=created_at,
                observed_at=observed_at,
                payload_summary={
                    "tag_name": tag,
                    "published_at": release.get("published_at"),
                    "prerelease": bool(release.get("prerelease")),
                },
            )
        ]

    return []


def iter_jsonl_bytes(
    raw_bytes: bytes,
    *,
    compressed: bool = False,
    max_bytes: int = MAX_ARCHIVE_DECOMPRESSED_BYTES,
) -> Iterator[dict[str, Any]]:
    """Yield parsed JSON objects line-by-line from raw or gzip-compressed bytes.

    Enforces ``max_bytes`` on the decompressed stream so a crafted gzip archive
    cannot exhaust memory.
    """
    if compressed:
        seen = 0
        with gzip.GzipFile(fileobj=io.BytesIO(raw_bytes), mode="rb") as gz:
            while True:
                line = gz.readline(max_bytes + 1)
                if not line:
                    break
                seen += len(line)
                if seen > max_bytes:
                    raise ValueError(
                        f"decompressed GH Archive stream exceeded {max_bytes} bytes"
                    )
                text = line.decode("utf-8", "replace").strip()
                if not text:
                    continue
                try:
                    obj = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    else:
        if len(raw_bytes) > max_bytes:
            raise ValueError(f"GH Archive stream exceeded {max_bytes} bytes")
        for line in raw_bytes.decode("utf-8", "replace").splitlines():
            text = line.strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


def fetch_gharchive_hour_bytes(
    hour_spec: str,
    *,
    opener: Optional[Callable[[str], bytes]] = None,
    timeout: float = 60.0,
) -> bytes:
    """Download one hourly ``.json.gz`` dump from ``data.gharchive.org`` into memory.

    Raises :class:`ValueError` when the dump is larger than
    :data:`MAX_ARCHIVE_DECOMPRESSED_BYTES` instead of returning a truncated one.
    """
    url = archive_hour_url(hour_spec)
    if opener is not None:
        return opener(url)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with net.urlopen(req, timeout) as resp:
        body = resp.read(MAX_ARCHIVE_DECOMPRESSED_BYTES + 1)
    if len(body) > MAX_ARCHIVE_DECOMPRESSED_BYTES:
        # Never hand back a silently truncated dump: a cut gzip stream replays as
        # a clean-looking hour with the rest of its events missing.
        raise ValueError(
            f"GH Archive hour {hour_spec} exceeds the {MAX_ARCHIVE_DECOMPRESSED_BYTES}-byte cap"
        )
    return body


def load_ledger_records(ledger_file: Path) -> list[ArchiveLedgerRecord]:
    """Read all valid :class:`ArchiveLedgerRecord` lines from ``ledger_file``."""
    if not ledger_file.is_file():
        return []
    out: list[ArchiveLedgerRecord] = []
    for line in ledger_file.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            raw = json.loads(text)
            if isinstance(raw, Mapping):
                out.append(ArchiveLedgerRecord.from_dict(raw))
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
    return out


def append_ledger_records(
    ledgers_dir: Path, records: Iterable[ArchiveLedgerRecord]
) -> tuple[int, int]:
    """Append ``records`` to ``<ledgers_dir>/<ledger>/<owner>__<name>.jsonl``.

    Deduplicates against existing ``(event_id, object_id, observed_at)`` keys in
    each target ledger file so replaying the same hour twice is idempotent.
    Returns ``(appended_count, repos_touched_count)``.
    """
    by_target: dict[tuple[str, str], list[ArchiveLedgerRecord]] = {}
    for rec in records:
        _validate_repo_pair(rec.owner, rec.name)
        if rec.ledger not in LEDGER_NAMES:
            raise ValueError(f"invalid ledger {rec.ledger!r}")
        by_target.setdefault((rec.ledger, rec.repo_key), []).append(rec)

    appended = 0
    repos_touched: set[str] = set()
    for (ledger, repo_key), group in sorted(by_target.items()):
        target = ledgers_dir / ledger / f"{repo_key}.jsonl"
        existing = load_ledger_records(target)
        seen_keys = {
            (r.event_id, r.object_id, r.observed_at) for r in existing
        }
        new_lines: list[str] = []
        for rec in group:
            dedup_key = (rec.event_id, rec.object_id, rec.observed_at)
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            new_lines.append(json.dumps(rec.to_dict(), sort_keys=True) + "\n")
        if new_lines:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as handle:
                handle.writelines(new_lines)
            appended += len(new_lines)
            repos_touched.add(repo_key)
    return appended, len(repos_touched)


def replay_hour_stream(
    events: Iterable[Mapping[str, Any]],
    *,
    candidates: Sequence[tuple[str, str]],
    ledgers_dir: Path,
    hour_spec: str,
) -> ReplayReport:
    """Filter ``events`` for ``candidates`` and append to ``ledgers_dir``."""
    observed_dt = hour_to_observed_at(hour_spec)
    observed_iso = observed_dt.isoformat()
    universe_lookup: dict[str, tuple[str, str]] = {}
    for owner, name in candidates:
        valid_owner, valid_name = _validate_repo_pair(owner, name)
        universe_lookup[f"{valid_owner.lower()}/{valid_name.lower()}"] = (
            valid_owner,
            valid_name,
        )

    scanned = 0
    matched_records: list[ArchiveLedgerRecord] = []
    for event in events:
        scanned += 1
        matched_records.extend(
            shape_gharchive_event(event, universe_lookup, observed_iso)
        )

    appended, repos_touched = append_ledger_records(ledgers_dir, matched_records)
    return ReplayReport(
        hour_spec=hour_spec,
        observed_at=observed_iso,
        events_scanned=scanned,
        events_matched=len(matched_records),
        records_appended=appended,
        repos_touched=repos_touched,
    )


def _in_window(
    rec: ArchiveLedgerRecord,
    window_start: Optional[datetime],
    window_end: Optional[datetime],
) -> bool:
    obs = _parse_iso(rec.observed_at) or _parse_iso(rec.created_at)
    if obs is None:
        return True
    if window_start is not None and obs < window_start:
        return False
    return not (window_end is not None and obs > window_end)


def ledger_vs_archive(
    ledgers_dir: Path,
    archive_records: Sequence[ArchiveLedgerRecord],
    *,
    window_start: Optional[datetime] = None,
    window_end: Optional[datetime] = None,
) -> list[ArchiveDiscrepancy]:
    """Compare on-disk ledgers under ``ledgers_dir`` against ``archive_records`` (W6-10).

    Groups unique ``object_id`` values per ``(repo_key, ledger)`` inside
    ``[window_start, window_end]`` and returns an :class:`ArchiveDiscrepancy`
    for every pair where the on-disk ledger and the archive disagree (for
    example, when an event present in the GH Archive dump is missing from the
    ledger). Returns ``[]`` when every pair matches.
    """
    archive_by_pair: dict[tuple[str, str], set[str]] = {}
    for rec in archive_records:
        if not _in_window(rec, window_start, window_end):
            continue
        archive_by_pair.setdefault((rec.repo_key, rec.ledger), set()).add(rec.object_id)

    ledger_by_pair: dict[tuple[str, str], set[str]] = {}
    for ledger in LEDGER_NAMES:
        ldir = ledgers_dir / ledger
        if not ldir.is_dir():
            continue
        for path in sorted(ldir.glob("*.jsonl")):
            repo_key = path.stem
            for rec in load_ledger_records(path):
                if not _in_window(rec, window_start, window_end):
                    continue
                ledger_by_pair.setdefault((repo_key, ledger), set()).add(rec.object_id)

    all_pairs = sorted(set(archive_by_pair) | set(ledger_by_pair))
    discrepancies: list[ArchiveDiscrepancy] = []
    for repo_key, ledger in all_pairs:
        arch_ids = archive_by_pair.get((repo_key, ledger), set())
        ledg_ids = ledger_by_pair.get((repo_key, ledger), set())
        missing = tuple(sorted(arch_ids - ledg_ids))
        extra = tuple(sorted(ledg_ids - arch_ids))
        if missing or extra:
            discrepancies.append(
                ArchiveDiscrepancy(
                    repo_key=repo_key,
                    ledger=ledger,
                    ledger_count=len(ledg_ids),
                    archive_count=len(arch_ids),
                    missing_in_ledger=missing,
                    extra_in_ledger=extra,
                )
            )
    return discrepancies
