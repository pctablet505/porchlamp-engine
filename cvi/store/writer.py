"""CVI v2 store writer (`04` sections 3, 5).

``RepoWrite`` is ``(record: ScoreRecord, bundle: FeatureBundle)``: identity
comes from ``record.repo``, and ``__post_init__`` raises if it differs from
``bundle.repo``. Rank/tie_group ordering is already baked into
``record.tie_key``/``record.rank``/``record.tie_group`` by the time a record
reaches this module, and TTFHR p75 is recomputed from ``bundle.pr_window``
(:func:`cvi.core.tiebreak.ttfhr_p75_from_pr_window`).

``forks`` (count) comes from :func:`cvi.derive.bundle.forks_count` (or ``NULL``
when absent). ``last_human_commit_at`` comes from :func:`_last_human_commit_at`, which
reads the ``last_non_bot_commit_at`` bundle sidecar fact
(``cvi.core.commit_domains``, D-TIE-1 item 7) the same way
``cvi.core.pipeline.score_bundle_stage_9_tie_input`` does for
``TieBreakInput``, normalised through :func:`_normalize_utc_z` so the stored
column agrees with the ``rank`` column beside it under
``cvi/store/reader.py``'s ``last_human_commit_at DESC NULLS LAST``
(D-TIE-1 level 7). ``stars_audited`` comes from
:func:`cvi.derive.bundle.stars_audited` (falling back to a ``"stars_audited"``
key on ``bundle.inputs`` when one is present) and is written ``NULL``
when absent or ``Unmeasured`` -- never a raw, unaudited star count.

A snapshot id is immutable (03 section 5): once ``repositories``
carries any row for a ``snapshot_id``, a later :func:`write_records` call
for that same id raises rather than upserting, replacing, or deleting --
the only way to retry is a transaction that never committed (see
:func:`write_records`) or a fresh snapshot id. :func:`write_snapshot`
mirrors this at the snapshot-identity level: a repeated call with
the same id is a no-op only when every identity field matches byte-for-
byte; any difference raises.

Import-graph note: this module imports ``cvi.snapshot.populations`` and
``cvi.snapshot.reproduce`` in addition to ``cvi.core`` -- `04` section 3
allows it; enforced by ``tests/core/test_import_graph.py``.
"""
from __future__ import annotations

import importlib
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from cvi.core import commit_domains, constants
from cvi.core.commit_domains import LastNonBotCommitMeasured
from cvi.core.models import FeatureBundle, Measured, ScoreRecord, canonical_json, is_relative_envelope_source
from cvi.core.tiebreak import ttfhr_p75_from_pr_window
from cvi.snapshot.populations import PopulationSet, build_populations_table
from cvi.snapshot.reproduce import canonical_dump_hash


def bundle_stars_audited(bundle: FeatureBundle) -> Optional[int]:
    return importlib.import_module("cvi.derive.bundle").stars_audited(bundle)


def bundle_forks_count(bundle: FeatureBundle) -> Optional[int]:
    return importlib.import_module("cvi.derive.bundle").forks_count(bundle)


__all__ = [
    "RepoWrite",
    "write_snapshot",
    "write_records",
    "finalize_snapshot_hash",
    "snapshot_dump_hash",
]


@dataclass(frozen=True)
class RepoWrite:
    """One repo's write unit: the scored ``record`` and the ``bundle`` that
    carries the raw per-parameter evidence ``ScoreRecord.params`` does not
    itself carry (``FeatureBundle.inputs``: ``raw_value``/``window``/
    ``source``/``source_hash``/``fetched_at``) plus TTFHR p75 (via
    ``bundle.pr_window``). Identity is ``record.repo``; ``bundle.repo``
    must be identical (dataclass equality) or construction raises."""

    record: ScoreRecord
    bundle: FeatureBundle

    def __post_init__(self) -> None:
        if not isinstance(self.record, ScoreRecord):
            raise ValueError(f"record must be a ScoreRecord, got {type(self.record).__name__}")
        if not isinstance(self.bundle, FeatureBundle):
            raise ValueError(f"bundle must be a FeatureBundle, got {type(self.bundle).__name__}")
        if self.record.repo != self.bundle.repo:
            raise ValueError(
                f"record.repo {self.record.repo!r} does not match bundle.repo {self.bundle.repo!r}"
            )


_SNAPSHOT_IDENTITY_FIELDS = ("as_of", "benchmark_version", "constants_hash", "code_commit", "manifest_root", "created_at")


def _begin(conn: sqlite3.Connection) -> bool:
    """Start a transaction unless one is already open: returns whether THIS
    call started it. Only the call that started a transaction may end it --
    so a caller who already has one open (to wrap :func:`write_snapshot`
    and :func:`write_records` together in one transaction) never hits
    ``OperationalError: cannot start a transaction within a transaction``,
    and never has that outer transaction committed or rolled back early by
    an inner call that merely joined it."""
    if conn.in_transaction:
        return False
    conn.execute("BEGIN")
    return True


def _end(conn: sqlite3.Connection, owns: bool, *, ok: bool) -> None:
    """Commit or roll back only if THIS call owns the transaction (paired
    with :func:`_begin`); a no-op when it merely joined an ambient one."""
    if not owns:
        return
    if ok:
        conn.commit()
    else:
        conn.rollback()


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
    """Insert one ``snapshots`` row (`04` section 5, a superset of the
    ``snapshot_meta`` contract).

    A second call with the same ``id`` is a no-op ONLY when every identity
    field (``as_of``, ``benchmark_version``, ``constants_hash``,
    ``code_commit``, ``manifest_root``, ``created_at``) matches the stored
    row byte-for-byte; any difference raises ``ValueError`` instead of
    silently keeping the first definition. ``canonical_dump_hash``/
    ``promoted_at`` are deliberately excluded from that comparison -- they
    are :func:`finalize_snapshot_hash`'s to fill in later, so a call that
    omits them (the normal, pre-finalize shape) must not be treated as a
    conflict with a row a prior :func:`finalize_snapshot_hash` already
    stamped. ``created_at`` is an explicit argument, never the wall clock.

    Joins ``conn``'s transaction if one is already open
    (:func:`_begin`/:func:`_end`) rather than always issuing its own
    ``BEGIN`` -- a caller may wrap this together with :func:`write_records`
    in one outer transaction without an ``OperationalError``."""
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
    incoming = (as_of, benchmark_version, constants_hash, code_commit, manifest_root, created_at)
    owns = _begin(conn)
    try:
        existing = conn.execute(
            "SELECT as_of, benchmark_version, constants_hash, code_commit, manifest_root, created_at "
            "FROM snapshots WHERE id=?",
            (id,),
        ).fetchone()
        if existing is not None:
            if tuple(existing) != incoming:
                mismatch = dict(zip(_SNAPSHOT_IDENTITY_FIELDS, zip(existing, incoming)))
                raise ValueError(f"snapshot {id!r} already exists with a different definition: {mismatch}")
            _end(conn, owns, ok=True)  # byte-equal: nothing written, still ends OUR OWN transaction if any
            return
        conn.execute(
            """
            INSERT INTO snapshots
                (id, as_of, benchmark_version, constants_hash, code_commit, manifest_root,
                 canonical_dump_hash, created_at, promoted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (id, as_of, benchmark_version, constants_hash, code_commit, manifest_root,
             canonical_dump_hash, created_at, promoted_at),
        )
    except Exception:  # review-ok: transaction cleanup helper terminates transaction before re-raise
        _end(conn, owns, ok=False)
        raise
    _end(conn, owns, ok=True)


def _normalize_utc_z(value: Optional[str]) -> Optional[str]:
    """Normalise an ISO datetime to UTC with a literal ``Z`` suffix so
    ``last_human_commit_at DESC`` compares correctly as TEXT in SQL (the
    same ordering a caller gets by comparing epochs in Python). ``None``
    passes through unchanged. Never builds a naive ``datetime`` (an
    offset-less value is already UTC, this codebase's own convention --
    see ``cvi.core.tiebreak._commit_epoch``)."""
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_PILLAR_NAMES = constants.PILLAR_NAMES
_PARAM_INPUT_IDS = constants.PARAM_INPUT_IDS


def _last_human_commit_at(bundle: FeatureBundle) -> Optional[str]:
    """The ``last_non_bot_commit_at`` bundle-level sidecar fact (D-TIE-1
    item 7, ``cvi.core.commit_domains`` -- the same accessor
    ``cvi.core.pipeline.score_bundle_stage_9_tie_input`` reads for
    ``TieBreakInput``), normalised through :func:`_normalize_utc_z` so the
    stored column compares correctly as TEXT in ``cvi/store/reader.py``'s
    ``ORDER BY ... last_human_commit_at DESC NULLS LAST``. ``None`` for a
    measured absence (no non-bot commit in the window) or an
    unset/``Unmeasured`` fact -- never a guess."""
    fact = commit_domains.last_non_bot_commit_at(bundle)
    value = fact.value if isinstance(fact, LastNonBotCommitMeasured) else None
    return _normalize_utc_z(value)


def _repo_row(snapshot_id: str, item: RepoWrite) -> tuple:
    record = item.record
    repo = record.repo
    pillar_values: list[object] = []
    for pillar in _PILLAR_NAMES:
        pillar_score = record.pillars.get(pillar)
        pillar_values.append(pillar_score.score if pillar_score is not None else None)
        pillar_values.append(pillar_score.coverage if pillar_score is not None else None)
    persona_composites_json = canonical_json(
        {persona: asdict(composite) for persona, composite in record.persona_composites.items()}
    )
    stars_val = bundle_stars_audited(item.bundle)
    if stars_val is None:
        audited = item.bundle.inputs.get("stars_audited")
        stars_val = audited.value if isinstance(audited, Measured) else None
    forks_val = bundle_forks_count(item.bundle)
    return (
        snapshot_id,
        repo.owner,
        repo.name,
        repo.ecosystem,
        record.raw_hash,
        record.constants_hash,
        # stars_audited: read from bundle sidecar via bundle_stars_audited,
        # falling back to a "stars_audited" key on bundle.inputs for legacy
        # test compatibility. NULL when absent or Unmeasured (StarScout's
        # audit failed for this repo) -- never the raw, unaudited stars count.
        stars_val,
        forks_val,
        repo.license_spdx,
        int(repo.archived),
        int(repo.mirror_url is not None),
        int(repo.fork),
        int(record.gate.eligible),
        record.gate.reason,
        record.gate.routed,
        canonical_json(list(record.gate.undecided)),
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
        ttfhr_p75_from_pr_window(item.bundle.pr_window),
        _last_human_commit_at(item.bundle),  # last_human_commit_at
        canonical_json(record.persona_gates),
        persona_composites_json,
        canonical_json(record.suspicion),
    )


_REPO_COLUMNS = (
    "snapshot_id", "owner", "name", "ecosystem", "raw_hash", "constants_hash",
    "stars_audited", "forks", "license_spdx", "archived", "mirror", "fork",
    "eligible", "gate_reason", "routed", "gate_undecided", "rated", "unrated_reason",
) + tuple(
    column for pillar in _PILLAR_NAMES for column in (f"p_{pillar}", f"c_{pillar}")
) + (
    "cvi", "coverage", "tier", "tier_ceiling",
    "leverage", "leverage_coverage", "friction", "friction_coverage", "quadrant",
    "rank", "tie_group", "ttfhr_p75", "last_human_commit_at",
    "persona_gates", "persona_composites", "suspicion",
)

_REPO_INSERT_SQL = (
    "INSERT INTO repositories (" + ", ".join(_REPO_COLUMNS) + ") "
    "VALUES (" + ", ".join("?" for _ in _REPO_COLUMNS) + ")"
)


def _param_row(repo_id: int, param_id: str, item: RepoWrite) -> tuple:
    record = item.record
    param_score = record.params.get(param_id)
    bundle_value = item.bundle.inputs.get(param_id)
    raw_value = canonical_json(bundle_value.value) if isinstance(bundle_value, Measured) else None
    window_start = bundle_value.window_start if isinstance(bundle_value, Measured) else None
    window_end = bundle_value.window_end if isinstance(bundle_value, Measured) else None
    # `measured` below is the STAGE-2 (scoring) outcome (cvi.core.engine's
    # ParamScore), which can be False even when the INPUT was genuinely
    # Measured -- `reason="no_population"` is exactly that case (cvi.core.
    # models.Unmeasured's own docstring: assigned by the scoring engine to
    # a ParamScore, never to an input-stage value). `EvidenceRow`'s
    # contract (`cvi/serve/contract.py`) is explicit that an unmeasured row
    # carries no score/method/source/fetched_at -- so those four columns
    # follow the score-stage `measured` flag and never leak the input-stage
    # envelope reference for a row the API reports as unmeasured.
    scored_measured = param_score is not None and param_score.measured
    source = bundle_value.source if scored_measured and isinstance(bundle_value, Measured) else None
    if source is not None and not is_relative_envelope_source(source):
        # cvi.core.models.Measured.__post_init__ already refuses to
        # construct a Measured with an absolute source, so this should be
        # unreachable in practice -- but the store is the last stop before
        # an absolute path of this machine would be served as evidence,
        # and it must never write one, however it got here.
        raise ValueError(
            f"{param_id}: params.source must be a path relative to the data root, got {source!r}"
        )
    source_hash = bundle_value.source_hash if scored_measured and isinstance(bundle_value, Measured) else None
    fetched_at = bundle_value.fetched_at if scored_measured and isinstance(bundle_value, Measured) else None
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


_PARAM_INSERT_SQL = """
    INSERT INTO params
        (repo_id, param_id, raw_value, score, measured, reason, n,
         window_start, window_end, method, source, source_hash, fetched_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    A snapshot id is immutable. If ``repositories`` already carries any row
    for ``snapshot_id``, this raises ``ValueError`` before writing anything
    -- no upsert, no replace flag, no delete-then-insert. The only
    legitimate retry of the same id is one whose PRIOR attempt never
    committed (see the transaction guarantee below), or a fresh id.

    The whole call is one transaction (``rollback()`` on any exception,
    ``commit()`` only at the very end) -- a failure partway through leaves
    zero rows for this snapshot, even after a later, unrelated ``commit()``
    on the same connection; a failure in the params loop leaves zero
    ``repositories`` rows too. It joins ``conn``'s transaction if the caller
    already has one open (:func:`_begin`), so wrapping this together with
    :func:`write_snapshot` in one outer transaction (the fuller guarantee:
    nothing for the id exists at all, not just no ``params`` rows) is the
    caller's to compose.

    ``params`` rows for a gated repo (``record.params`` is empty at stage 0
    -- `04` section 3, ``cvi.core.engine`` never scores a gated repo's
    parameters) are correctly zero. Returns ``(repos_written,
    params_written, populations_written)`` for the caller's Measure line.
    """
    for item in items:
        if item.record.snapshot_id != snapshot_id:
            raise ValueError(
                f"record for {item.record.repo.owner}/{item.record.repo.name} carries "
                f"snapshot_id {item.record.snapshot_id!r}, does not match "
                f"write_records snapshot_id {snapshot_id!r}"
            )

    existing = conn.execute(
        "SELECT 1 FROM repositories WHERE snapshot_id=? LIMIT 1", (snapshot_id,)
    ).fetchone()
    if existing is not None:
        raise ValueError(
            f"snapshot {snapshot_id!r} already has repositories rows; snapshots are immutable "
            f"once written (03 section 5) -- mint a new snapshot id to retry"
        )

    owns = _begin(conn)
    try:
        repos_written = 0
        for item in items:
            conn.execute(_REPO_INSERT_SQL, _repo_row(snapshot_id, item))
            repos_written += 1

        params_written = 0
        for item in items:
            repo_id_row = conn.execute(
                "SELECT id FROM repositories WHERE snapshot_id=? AND owner=? AND name=?",
                (snapshot_id, item.record.repo.owner, item.record.repo.name),
            ).fetchone()
            repo_id = repo_id_row[0]
            for param_id in _PARAM_INPUT_IDS:
                if param_id not in item.record.params:
                    continue
                conn.execute(_PARAM_INSERT_SQL, _param_row(repo_id, param_id, item))
                params_written += 1

        populations_written = 0
        if populations is not None:
            for row in build_populations_table(populations):
                conn.execute(_POPULATION_UPSERT_SQL, row)
                populations_written += 1
    except Exception:  # review-ok: transaction cleanup helper terminates transaction before re-raise
        _end(conn, owns, ok=False)
        raise
    _end(conn, owns, ok=True)
    return (repos_written, params_written, populations_written)


#: The snapshot's identity covers every v2 table that carries evidence for
#: it -- not just membership (``repositories``) but "why this score"
#: (``params``) and the frozen normalisation inputs (``populations``). A
#: narrower allow-list would let a `params` row (score, raw value,
#: source_hash) or a `populations` row be rewritten with the identity hash
#: unchanged, leaving 03 section 5's guard certifying evidence it does not
#: cover.
_SNAPSHOT_DUMP_TABLES = ("repositories", "params", "populations")


def snapshot_dump_hash(db_path: Path, snapshot_id: str) -> str:
    """``cvi.snapshot.reproduce.canonical_dump_hash`` scoped to one
    snapshot's own rows of ``repositories``, ``params`` and ``populations``
    -- there is no second hash implementation here.

    An ALLOW-list of tables (keep only the three above), not a fixed
    exclude-list -- an exclude-list would silently hash
    ``legacy_repositories`` into every v2 snapshot's identity on a migrated
    v1 database. Makes a consistent single-file copy of ``db_path`` in ANY
    journal mode via SQLite's online backup API
    (:meth:`sqlite3.Connection.backup`) -- unlike a byte copy, which misses
    committed-but-uncheckpointed data that still lives in a ``-wal`` sidecar
    (the copy can then hold no tables at all) and is not guaranteed valid while
    another connection writes. The copy is then scoped, in FK-safe
    child-before-parent order: deletes every ``params`` row whose
    ``repo_id`` belongs to a repository OUTSIDE ``snapshot_id`` (``params``
    has no ``snapshot_id`` column of its own), deletes every
    ``repositories`` row not in ``snapshot_id``, deletes every
    ``populations`` row not in ``snapshot_id``, drops every table that is
    not one of the three (whatever it is called, including a legacy or a
    future table), then hashes the result.

    This scratch connection deliberately does NOT use
    :func:`cvi.store.schema.connect` (no ``PRAGMA foreign_keys = ON``):
    the final ``DROP TABLE`` of ``snapshots`` (and anything else) would
    fail under FK enforcement while ``repositories`` rows still reference
    it. Enforcing integrity on a copy being intentionally, selectively torn
    apart for hashing would break the operation, not protect real data --
    this connection only reads and discards a throwaway copy. The separate
    ``source`` connection is opened solely so :meth:`sqlite3.Connection.backup`
    can read the live database; it issues no writes of its own."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        copy_path = Path(tmp_dir) / "snapshot_dump.db"
        scratch = sqlite3.connect(copy_path)
        source = sqlite3.connect(db_path)
        try:
            source.backup(scratch)
        finally:
            source.close()
        try:
            scratch.execute(
                "DELETE FROM params WHERE repo_id IN "
                "(SELECT id FROM repositories WHERE snapshot_id != ?)",
                (snapshot_id,),
            )
            scratch.execute("DELETE FROM repositories WHERE snapshot_id != ?", (snapshot_id,))
            scratch.execute("DELETE FROM populations WHERE snapshot_id != ?", (snapshot_id,))
            tables = [
                row[0]
                for row in scratch.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            ]
            for table in tables:
                if table not in _SNAPSHOT_DUMP_TABLES:
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
    """Compute :func:`snapshot_dump_hash` over ``db_path`` (a file
    ``conn``'s writes have already been committed to) and stamp it plus
    ``promoted_at`` onto the snapshot row.

    Split from :func:`write_records` because the hash covers rows that
    must already exist on disk -- a connection's own uncommitted writes are
    invisible to a second connection opened on the same path. Returns the
    computed hash."""
    if not isinstance(promoted_at, str) or not promoted_at:
        raise ValueError(f"promoted_at must be a non-empty string, got {promoted_at!r}")
    dump_hash = snapshot_dump_hash(db_path, snapshot_id)
    conn.execute(
        "UPDATE snapshots SET canonical_dump_hash=?, promoted_at=? WHERE id=?",
        (dump_hash, promoted_at, snapshot_id),
    )
    conn.commit()
    return dump_hash
