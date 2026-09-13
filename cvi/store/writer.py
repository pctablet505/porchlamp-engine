"""CVI v2 store writer (`04` sections 3, 5; packet W4-2 deliverable 3).

Ownership request 1 (full detail in the handoff): `04` section 3 puts repo
identity on ``FeatureBundle.repo``, never on ``ScoreRecord`` -- the same gap
``cvi.core.tiebreak`` names for its own tie-break evidence. Since a
``repositories`` row also needs the raw per-parameter evidence
(``raw_value``/``window``/``source_hash``/``fetched_at``) that lives on
``FeatureBundle.inputs`` (not on ``ScoreRecord.params``, post-scoring only),
``write_records`` takes ``Sequence[RepoWrite]`` below -- a
:class:`~cvi.core.tiebreak.TieBreakInput` (record + identity + tie-break
evidence) paired with the :class:`~cvi.core.models.FeatureBundle` -- rather
than ``list[ScoreRecord]`` as dispatched (R-2: reuse ``TieBreakInput``, not
a second identity carrier).

Ownership request 2: the packet asks for the canonical dump hash of the
``repositories`` table via the function branch INT-W0-4 reuses
(``cvi.lakehouse.repositories_dump_hash``); that function lives only on the
INT-W0-4 branch, not this tree, and this packet must not touch
``cvi/lakehouse.py``. Per the packet's own fallback, nothing is vendored:
:func:`repositories_snapshot_dump_hash` below calls the one primitive that
IS on this tree, ``cvi.snapshot.reproduce.canonical_dump_hash`` (the same
function INT-W0-4's helper wraps), scoped to one snapshot's rows via a
scratch copy. Once INT-W0-4 lands, the lead should consider one shared
scoping helper instead of two (R-2).

Import-graph note: `04` section 3 says "store/ imports core ... and nothing
else"; this module also imports ``cvi.snapshot.populations`` and
``cvi.snapshot.reproduce`` because the packet dispatch explicitly directs
reuse of their population-table builder and dump-hash function
(deliverable 3) -- a documentation gap between `04` section 3's prose and
this packet's own instructions, not a violation worked around silently.
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from cvi.core import constants_v2
from cvi.core.models import FeatureBundle, Measured, canonical_json
from cvi.core.tiebreak import TieBreakInput
from cvi.snapshot.populations import PopulationSet, build_populations_table
from cvi.snapshot.reproduce import canonical_dump_hash

__all__ = [
    "RepoWrite",
    "write_snapshot",
    "write_records",
    "finalize_snapshot_hash",
    "repositories_snapshot_dump_hash",
]


@dataclass(frozen=True)
class RepoWrite:
    """One repo's write unit: the tie-break evidence (record + identity +
    TTFHR p75 + last non-bot commit + stars/forks) plus the bundle that
    carries the remaining repo identity and the raw per-parameter evidence
    (see module docstring). ``owner``/``name``/the record all live on
    ``tie``; reading them from two places would be a second definition."""

    tie: TieBreakInput
    bundle: FeatureBundle

    def __post_init__(self) -> None:
        if not isinstance(self.bundle, FeatureBundle):
            raise ValueError(f"bundle must be a FeatureBundle, got {type(self.bundle).__name__}")
        if self.tie.owner != self.bundle.repo.owner or self.tie.name != self.bundle.repo.name:
            raise ValueError(
                f"tie identity {self.tie.owner}/{self.tie.name} does not match "
                f"bundle identity {self.bundle.repo.owner}/{self.bundle.repo.name}"
            )


def write_snapshot(
    conn: sqlite3.Connection,
    *,
    id: str,
    as_of: str,
    benchmark_version: str,
    constants_hash: str,
    code_commit: str,
    manifest_root: str,
    created_at: str,
    canonical_dump_hash: Optional[str] = None,
    promoted_at: Optional[str] = None,
) -> None:
    """Insert one ``snapshots`` row (`04` section 5, extended per the
    packet: superset of INT-W0-4's ``snapshot_meta``).

    Idempotent on ``id``: ``INSERT OR IGNORE`` -- a second call with the
    same ``id`` is a no-op (it never rewinds a hash/``promoted_at`` a later
    :func:`finalize_snapshot_hash` call already stamped). ``created_at`` is
    an explicit argument, never the wall clock (04 section 8; `06`/R rules)."""
    for field_name, value in (
        ("id", id),
        ("as_of", as_of),
        ("benchmark_version", benchmark_version),
        ("constants_hash", constants_hash),
        ("code_commit", code_commit),
        ("manifest_root", manifest_root),
        ("created_at", created_at),
    ):
        if not isinstance(value, str) or not value:
            raise ValueError(f"snapshot {field_name} must be a non-empty string, got {value!r}")
    conn.execute(
        """
        INSERT OR IGNORE INTO snapshots
            (id, as_of, benchmark_version, constants_hash, code_commit, manifest_root,
             canonical_dump_hash, created_at, promoted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (id, as_of, benchmark_version, constants_hash, code_commit, manifest_root,
         canonical_dump_hash, created_at, promoted_at),
    )


_PILLAR_NAMES = constants_v2.PILLAR_NAMES
_PARAM_INPUT_IDS = constants_v2.PARAM_INPUT_IDS


def _repo_row(snapshot_id: str, item: RepoWrite) -> tuple:
    record = item.tie.record
    repo = item.bundle.repo
    pillar_values: list[object] = []
    for pillar in _PILLAR_NAMES:
        pillar_score = record.pillars.get(pillar)
        pillar_values.append(pillar_score.score if pillar_score is not None else None)
        pillar_values.append(pillar_score.coverage if pillar_score is not None else None)
    return (
        snapshot_id,
        item.tie.owner,
        item.tie.name,
        repo.ecosystem,
        record.raw_hash,
        record.constants_hash,
        item.tie.stars,
        item.tie.forks,
        repo.license_spdx,
        int(repo.archived),
        int(repo.mirror_url is not None),
        int(repo.fork),
        int(record.gate.eligible),
        record.gate.reason,
        record.gate.routed,
        int(record.rated),
        record.unrated_reason,
        *pillar_values,
        record.composite,
        record.coverage,
        record.tier,
        record.tier_ceiling,
        record.leverage,
        record.leverage_coverage,
        record.friction,
        record.friction_coverage,
        record.quadrant,
        record.rank,
        record.tie_group,
        item.tie.ttfhr_p75_hours,
        item.tie.last_non_bot_commit_at,
        canonical_json(record.persona_gates),
        canonical_json(record.persona_composites),
        canonical_json(record.suspicion),
    )


_REPO_COLUMNS = (
    "snapshot_id", "owner", "name", "ecosystem", "raw_hash", "constants_hash",
    "stars_audited", "forks", "license_spdx", "archived", "mirror", "fork",
    "eligible", "gate_reason", "routed", "rated", "unrated_reason",
) + tuple(
    column for pillar in _PILLAR_NAMES for column in (f"p_{pillar}", f"c_{pillar}")
) + (
    "cvi", "coverage", "tier", "tier_ceiling",
    "leverage", "leverage_coverage", "friction", "friction_coverage", "quadrant",
    "rank", "tie_group", "ttfhr_p75", "last_human_commit_at",
    "persona_gates", "persona_composites", "suspicion",
)

_REPO_UPSERT_SQL = (
    "INSERT INTO repositories (" + ", ".join(_REPO_COLUMNS) + ") "
    "VALUES (" + ", ".join("?" for _ in _REPO_COLUMNS) + ") "
    "ON CONFLICT(snapshot_id, owner, name) DO UPDATE SET "
    + ", ".join(f"{col}=excluded.{col}" for col in _REPO_COLUMNS if col not in ("snapshot_id", "owner", "name"))
)


def _param_row(repo_id: int, param_id: str, item: RepoWrite) -> tuple:
    record = item.tie.record
    param_score = record.params.get(param_id)
    bundle_value = item.bundle.inputs.get(param_id)
    raw_value = canonical_json(bundle_value.value) if isinstance(bundle_value, Measured) else None
    window_start = bundle_value.window_start if isinstance(bundle_value, Measured) else None
    window_end = bundle_value.window_end if isinstance(bundle_value, Measured) else None
    source = bundle_value.source if isinstance(bundle_value, Measured) else None
    source_hash = bundle_value.source_hash if isinstance(bundle_value, Measured) else None
    fetched_at = bundle_value.fetched_at if isinstance(bundle_value, Measured) else None
    return (
        repo_id,
        param_id,
        raw_value,
        param_score.score if param_score is not None else None,
        int(param_score.measured) if param_score is not None else 0,
        param_score.reason if param_score is not None else None,
        param_score.n if param_score is not None else None,
        window_start,
        window_end,
        param_score.method if param_score is not None else None,
        source,
        source_hash,
        fetched_at,
    )


_PARAM_UPSERT_SQL = """
    INSERT INTO params
        (repo_id, param_id, raw_value, score, measured, reason, n,
         window_start, window_end, method, source, source_hash, fetched_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(repo_id, param_id) DO UPDATE SET
        raw_value=excluded.raw_value, score=excluded.score, measured=excluded.measured,
        reason=excluded.reason, n=excluded.n, window_start=excluded.window_start,
        window_end=excluded.window_end, method=excluded.method, source=excluded.source,
        source_hash=excluded.source_hash, fetched_at=excluded.fetched_at
"""

_POPULATION_UPSERT_SQL = """
    INSERT INTO populations (snapshot_id, ecosystem, param_id, n, fallback, values_json, values_hash)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(snapshot_id, ecosystem, param_id) DO UPDATE SET
        n=excluded.n, fallback=excluded.fallback, values_json=excluded.values_json,
        values_hash=excluded.values_hash
"""


def write_records(
    conn: sqlite3.Connection,
    snapshot_id: str,
    items: Sequence[RepoWrite],
    populations: Optional[PopulationSet] = None,
) -> tuple[int, int, int]:
    """Write one snapshot's repositories, their long-format ``params``
    evidence, and the frozen ``populations`` for that snapshot.

    Idempotent per ``(snapshot_id, owner, name)`` (`04` section 5's
    ``UNIQUE`` constraint) via ``INSERT ... ON CONFLICT ... DO UPDATE``: a
    second call with the same identity updates the row in place rather
    than raising or duplicating -- the repo keeps its original ``id`` (an
    ``UPDATE`` never changes it), so ``params`` rows (keyed by that
    ``repo_id``) stay attached correctly across re-writes. ``params`` rows
    for a gated repo (``record.params`` is empty at stage 0 -- `04` section
    3, ``cvi.core.engine`` never scores a gated repo's parameters) are
    correctly zero: there is nothing to show as evidence for a repo that
    was never scored. Returns ``(repos_written, params_written,
    populations_written)`` for the caller's Measure line.
    """
    for item in items:
        if item.tie.record.snapshot_id != snapshot_id:
            raise ValueError(
                f"record for {item.tie.owner}/{item.tie.name} carries "
                f"snapshot_id {item.tie.record.snapshot_id!r}, does not match "
                f"write_records snapshot_id {snapshot_id!r}"
            )

    repos_written = 0
    for item in items:
        conn.execute(_REPO_UPSERT_SQL, _repo_row(snapshot_id, item))
        repos_written += 1

    params_written = 0
    for item in items:
        repo_id_row = conn.execute(
            "SELECT id FROM repositories WHERE snapshot_id=? AND owner=? AND name=?",
            (snapshot_id, item.tie.owner, item.tie.name),
        ).fetchone()
        if repo_id_row is None:  # pragma: no cover - defensive, upsert above guarantees the row
            raise RuntimeError(f"repository row missing after upsert: {item.tie.owner}/{item.tie.name}")
        repo_id = repo_id_row[0]
        for param_id in _PARAM_INPUT_IDS:
            if param_id not in item.tie.record.params:
                continue
            conn.execute(_PARAM_UPSERT_SQL, _param_row(repo_id, param_id, item))
            params_written += 1

    populations_written = 0
    if populations is not None:
        for row in build_populations_table(populations):
            conn.execute(_POPULATION_UPSERT_SQL, row)
            populations_written += 1

    conn.commit()
    return (repos_written, params_written, populations_written)


#: Tables excluded from the repositories-only dump hash: bookkeeping tables
#: that either record the hash itself (self-reference, see
#: :func:`repositories_snapshot_dump_hash`) or are unrelated to "the
#: repositories table" the packet names.
_DUMP_HASH_EXCLUDED_TABLES = ("snapshots", "params", "populations", "schema_migrations")


def repositories_snapshot_dump_hash(db_path: Path, snapshot_id: str) -> str:
    """``cvi.snapshot.reproduce.canonical_dump_hash`` scoped to one
    snapshot's rows of the ``repositories`` table (see module docstring's
    ownership request: no second hash implementation, R-2).

    Copies ``db_path`` to a scratch file, deletes every ``repositories`` row
    not in ``snapshot_id`` and drops every other table, then hashes the
    result -- the same "scratch copy + drop unrelated tables" shape
    ``cvi.lakehouse.repositories_dump_hash`` uses on the v1 (single-
    snapshot) table, extended with the row-level filter the v2 table needs
    because it holds every snapshot at once."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        copy_path = Path(tmp_dir) / "repositories_dump.db"
        shutil.copyfile(db_path, copy_path)
        scratch = sqlite3.connect(copy_path)
        try:
            scratch.execute("DELETE FROM repositories WHERE snapshot_id != ?", (snapshot_id,))
            for table in _DUMP_HASH_EXCLUDED_TABLES:
                scratch.execute(f'DROP TABLE IF EXISTS "{table}"')
            scratch.commit()
        finally:
            scratch.close()
        return canonical_dump_hash(copy_path)


def finalize_snapshot_hash(
    conn: sqlite3.Connection,
    db_path: Path,
    snapshot_id: str,
    promoted_at: str,
) -> str:
    """Compute :func:`repositories_snapshot_dump_hash` over ``db_path`` (a
    file ``conn``'s writes have already been committed to) and stamp it
    plus ``promoted_at`` onto the snapshot row.

    Split from :func:`write_records` because the hash covers rows that
    must already exist on disk -- a connection's own uncommitted writes are
    invisible to a second connection opened on the same path. Returns the
    computed hash."""
    if not isinstance(promoted_at, str) or not promoted_at:
        raise ValueError(f"promoted_at must be a non-empty string, got {promoted_at!r}")
    dump_hash = repositories_snapshot_dump_hash(db_path, snapshot_id)
    conn.execute(
        "UPDATE snapshots SET canonical_dump_hash=?, promoted_at=? WHERE id=?",
        (dump_hash, promoted_at, snapshot_id),
    )
    conn.commit()
    return dump_hash
