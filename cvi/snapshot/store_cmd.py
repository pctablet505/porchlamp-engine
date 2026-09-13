"""``cvi.snapshot store`` driver (packet W4-6): score an on-disk snapshot
directory and write it into a v2 store, end to end.

Thin orchestration only -- ``cvi/snapshot/cli.py`` owns argv parsing, every
``print``, and process exit codes (that module's own docstring: "the CLI
layer owns the wall clock and every print"; there IS no wall clock read
anywhere in this command -- every timestamp comes from the snapshot
directory's own ``manifest.json``, via :mod:`cvi.snapshot.fixture_io`).
This module is the pure-ish driver behind ``cvi.snapshot store <dir> --db
<path>``:

1. :func:`score_snapshot_dir` -- load ``snapshot_dir`` (and, optionally,
   ``previous_dir``) via :func:`cvi.snapshot.fixture_io.load_frozen_fixture`,
   score ``previous_dir`` for real (only its records' ``params`` feed
   ``detect_param_jump`` downstream -- see :func:`_score_previous`), then
   score ``snapshot_dir`` for real with the real detector composition
   (:func:`cvi.verify.pipeline_hook.build_detectors`) wired in.
2. :func:`verify_expected` -- compares the freshly computed ``records_hash``
   against ``snapshot_dir``'s optional ``expected.json``.
3. :func:`write_scored_snapshot` -- opens one transaction spanning BOTH
   ``cvi.store.writer.write_snapshot`` and ``write_records`` (a stricter
   guarantee than either gives alone: if EITHER fails, nothing for this
   snapshot id exists afterwards, not even a lone ``snapshots`` row), then
   ``cvi.store.writer.finalize_snapshot_hash`` in a necessarily separate,
   later transaction -- that function's own docstring: its hash reads
   ``db_path`` fresh off disk, which cannot see this connection's own
   writes until they are committed.

Import-graph note: ``cvi/snapshot/*.py`` is not one of the packages
``tests/core/test_import_graph.py`` restricts (that test bounds only
``cvi/core/*.py``, ``cvi/derive/bundle.py`` and ``cvi/store/**`` --
confirmed directly against this base commit before writing this module,
rather than assumed); this file freely imports ``cvi.core``, ``cvi.store``
and ``cvi.verify``, exactly as the top-level orchestration layer needs to.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from cvi.core.models import FeatureBundle
from cvi.core.pipeline import RepoKey, ScoredSnapshot, repo_key, score_snapshot
from cvi.snapshot.fixture_io import FrozenFixture, load_frozen_fixture
from cvi.snapshot.manifest import merkle_root
from cvi.store.writer import RepoWrite, finalize_snapshot_hash, write_records, write_snapshot
from cvi.verify.pipeline_hook import build_detectors

__all__ = [
    "VerificationError",
    "score_snapshot_dir",
    "verify_expected",
    "manifest_root_of",
    "write_scored_snapshot",
]


class VerificationError(ValueError):
    """Raised by :func:`verify_expected` when ``snapshot_dir``'s
    ``expected.json`` names a ``records_hash`` that disagrees with the
    freshly computed one. A ``ValueError`` subclass so ``cvi/snapshot/
    cli.py``'s existing ``except (SnapshotError, FileNotFoundError)``
    convention (print the message, return a non-zero exit code) extends to
    this failure with one more ``except`` arm, not a parallel mechanism."""


def _score_previous(previous_dir: Path) -> ScoredSnapshot:
    """Score ``previous_dir`` for real (:func:`cvi.core.pipeline.
    score_snapshot`, no detectors composed for IT): only its records'
    ``params`` feed ``cvi.verify.detectors.detect_param_jump`` when scoring
    the CURRENT directory -- the previous snapshot's own ``suspicion`` is
    never read by anything downstream of this call, so it is left at the
    engine's default (``[]``) rather than wiring a second detector
    composition purely to discard its output."""
    frozen = load_frozen_fixture(previous_dir)
    return score_snapshot(
        frozen.bundles,
        frozen.populations,
        snapshot_id=frozen.snapshot_id,
        as_of=frozen.as_of,
        gate_evidence=frozen.gate_evidence,
        raw_hashes=frozen.raw_hashes,
    )


def score_snapshot_dir(
    snapshot_dir: Path,
    *,
    previous_dir: Optional[Path] = None,
) -> tuple[ScoredSnapshot, FrozenFixture]:
    """Load and score ``snapshot_dir`` for real, detectors wired in.

    ``previous_dir`` (optional): scored the same way (:func:`_score_previous`)
    and passed as ``score_snapshot``'s ``previous=`` so the cross-snapshot
    detector (``param_jump``) has something to compare against; ``None``
    when omitted, exactly like a snapshot's first-ever run -- forwarded to
    ``score_snapshot`` unchanged, which is itself the only place that
    decides what ``previous is None`` does or does not suppress (that
    function's own docstring: only ``param_jump`` needs a genuine previous
    record)."""
    frozen = load_frozen_fixture(snapshot_dir)
    previous = _score_previous(previous_dir) if previous_dir is not None else None
    detectors = build_detectors(frozen.bundles)
    scored = score_snapshot(
        frozen.bundles,
        frozen.populations,
        snapshot_id=frozen.snapshot_id,
        as_of=frozen.as_of,
        gate_evidence=frozen.gate_evidence,
        raw_hashes=frozen.raw_hashes,
        previous=previous,
        detectors=detectors,
    )
    return scored, frozen


def verify_expected(frozen: FrozenFixture, scored: ScoredSnapshot) -> None:
    """Raise :class:`VerificationError` when ``frozen.expected`` (from
    ``expected.json``) names a ``records_hash`` that disagrees with
    ``scored.records_hash``. A no-op when ``frozen.expected`` is ``None``
    (no ``expected.json`` in ``snapshot_dir`` -- a real, non-fixture
    directory has no pinned expectation to check itself against) or when
    it carries no ``records_hash`` key at all."""
    if frozen.expected is None:
        return
    expected_hash = frozen.expected.get("records_hash")
    if expected_hash is None:
        return
    if expected_hash != scored.records_hash:
        raise VerificationError(
            f"records_hash mismatch: expected.json says {expected_hash!r}, "
            f"computed {scored.records_hash!r} -- pass --no-verify to store anyway"
        )


def manifest_root_of(frozen: FrozenFixture) -> str:
    """The ``snapshots.manifest_root`` value for ``frozen``: the `03`
    section 5 Merkle root (:func:`cvi.snapshot.manifest.merkle_root` -- the
    same function a real ``cvi.snapshot freeze`` folds envelope hashes
    with, R-2, no second Merkle implementation) over every bundle's own
    ``raw_hash`` in this snapshot, sorted for a byte-identical root
    regardless of dict-iteration order. A scored-snapshot directory carries
    no envelope-level freeze manifest of its own (:mod:`cvi.snapshot.
    fixture_io`'s module docstring); the bundle raw hashes are the closest
    real analogue this directory shape has to "which inputs this snapshot
    covers"."""
    return merkle_root(sorted(frozen.raw_hashes.values()))


def write_scored_snapshot(
    conn: sqlite3.Connection,
    db_path: Path,
    scored: ScoredSnapshot,
    frozen: FrozenFixture,
    *,
    code_commit: str,
) -> str:
    """Write ``scored``/``frozen`` into the v2 store at ``db_path`` and
    return the finalised ``snapshot_dump_hash``.

    ``write_snapshot`` and ``write_records`` run inside ONE transaction
    this function opens itself (both join it via their own ``_begin``/
    ``_end`` -- ``cvi.store.writer``'s documented "join an ambient
    transaction" contract, ``tests/store/test_writer.py``'s own G-005
    tests exercise the identical ``conn.execute("BEGIN")`` idiom): if
    EITHER fails, nothing for ``scored.snapshot_id`` exists afterwards, not
    even the ``snapshots`` row alone -- a stricter guarantee than either
    function gives by itself (each is already atomic on its own; nothing
    before this function made the PAIR atomic together).
    ``finalize_snapshot_hash`` runs in a necessarily separate, LATER
    transaction: its hash reads ``db_path`` fresh off disk (a second
    connection), which cannot see this function's own connection's writes
    until they are committed (that function's own docstring).

    ``as_of``/``created_at``/``promoted_at`` are all ``frozen.as_of`` (the
    snapshot directory's own frozen reference time -- repo rule: no wall
    clock in scored values, extended here to this store bookkeeping too):
    running this command twice against the same ``snapshot_dir`` into two
    fresh databases writes byte-identical ``snapshots`` rows (the
    ``INTEGER PRIMARY KEY`` on ``repositories``/``params`` aside)."""
    as_of_text = frozen.as_of.isoformat()
    manifest_root = manifest_root_of(frozen)
    bundles_by_key: dict[RepoKey, FeatureBundle] = {
        repo_key(bundle): bundle for bundle in frozen.bundles
    }
    items = [RepoWrite(record=record, bundle=bundles_by_key[repo_key(record)]) for record in scored.records]

    conn.execute("BEGIN")
    try:
        write_snapshot(
            conn,
            id=scored.snapshot_id,
            as_of=as_of_text,
            benchmark_version=scored.benchmark_version,
            constants_hash=scored.constants_hash,
            code_commit=code_commit,
            manifest_root=manifest_root,
            created_at=as_of_text,
        )
        write_records(conn, scored.snapshot_id, items, populations=frozen.populations)
    except Exception:
        conn.rollback()
        raise
    conn.commit()

    return finalize_snapshot_hash(conn, db_path, scored.snapshot_id, promoted_at=as_of_text)
