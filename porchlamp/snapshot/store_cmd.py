"""``porchlamp.snapshot store`` driver: score an on-disk snapshot
directory and write it into a v2 store, end to end.

Thin orchestration only -- ``porchlamp/snapshot/cli.py`` owns argv parsing, every
``print``, and process exit codes (that module's own docstring: "the CLI
layer owns the wall clock and every print"; there IS no wall clock read
anywhere in this command -- every timestamp comes from the snapshot
directory's own ``manifest.json``, via :mod:`porchlamp.snapshot.fixture_io`).
This module is the pure-ish driver behind ``porchlamp.snapshot store <dir> --db
<path>``:

1. :func:`score_snapshot_dir` -- load ``snapshot_dir`` (and, optionally,
   ``previous_dir``) via :func:`porchlamp.snapshot.fixture_io.load_frozen_fixture`,
   score ``previous_dir`` for real (only its records' ``params`` feed
   ``detect_param_jump`` downstream -- see :func:`_score_previous`), then
   score ``snapshot_dir`` for real via :func:`score_snapshot_with_detectors`
   -- the shared function that wires the real detector composition
   (:func:`porchlamp.verify.pipeline_hook.build_detectors`) in, also called by
   ``scripts/build_fixture_snapshot.py``'s own pass-2 scoring so the two
   producers of a scored snapshot can never independently drift on
   whether/how detectors run.
2. :func:`verify_expected` -- compares the freshly computed ``records_hash``
   against ``snapshot_dir``'s optional ``expected.json``.
3. :func:`write_scored_snapshot` -- opens one transaction spanning BOTH
   ``porchlamp.store.writer.write_snapshot`` and ``write_records`` (a stricter
   guarantee than either gives alone: if EITHER fails, nothing for this
   snapshot id exists afterwards, not even a lone ``snapshots`` row), then
   ``porchlamp.store.writer.finalize_snapshot_hash`` in a necessarily separate,
   later transaction -- that function's own docstring: its hash reads
   ``db_path`` fresh off disk, which cannot see this connection's own
   writes until they are committed.

Import-graph note: ``porchlamp/snapshot/*.py`` is not one of the packages
``tests/core/test_import_graph.py`` restricts (that test bounds only
``porchlamp/core/*.py``, ``porchlamp/derive/bundle.py`` and ``porchlamp/store/**`` --
confirmed directly against this base commit before writing this module,
rather than assumed); this file freely imports ``porchlamp.core``, ``porchlamp.store``
and ``porchlamp.verify``, exactly as the top-level orchestration layer needs to.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from porchlamp.core.models import FeatureBundle
from porchlamp.core.pipeline import RepoKey, ScoredSnapshot, repo_key, score_snapshot
from porchlamp.snapshot.fixture_io import FrozenFixture, load_frozen_fixture
from porchlamp.snapshot.manifest import merkle_root
from porchlamp.store.writer import RepoWrite, finalize_snapshot_hash, write_records, write_snapshot
from porchlamp.verify.pipeline_hook import build_detectors

__all__ = [
    "VerificationError",
    "score_snapshot_dir",
    "score_snapshot_with_detectors",
    "verify_expected",
    "manifest_root_of",
    "write_scored_snapshot",
]


class VerificationError(ValueError):
    """Raised by :func:`verify_expected` when ``snapshot_dir``'s
    ``expected.json`` names a ``records_hash`` that disagrees with the
    freshly computed one. A ``ValueError`` subclass so ``porchlamp/snapshot/
    cli.py``'s existing ``except (SnapshotError, FileNotFoundError)``
    convention (print the message, return a non-zero exit code) extends to
    this failure with one more ``except`` arm, not a parallel mechanism."""


def _score_previous(previous_dir: Path) -> ScoredSnapshot:
    """Score ``previous_dir`` for real (:func:`porchlamp.core.pipeline.
    score_snapshot`, no detectors composed for IT): only its records'
    ``params`` feed ``porchlamp.verify.detectors.detect_param_jump`` when scoring
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


def score_snapshot_with_detectors(
    bundles,
    populations,
    *,
    snapshot_id: str,
    as_of,
    gate_evidence,
    raw_hashes,
    previous: "ScoredSnapshot | None" = None,
) -> ScoredSnapshot:
    """Scores a bundle set with the real detector composition wired in.

    ``porchlamp.snapshot.store_cmd.score_snapshot_dir`` (this command's own
    driver) and ``scripts/build_fixture_snapshot.py``'s pass-2 "real"
    scoring both call this, ensuring both paths stay aligned.
    """
    detectors = build_detectors(bundles)
    return score_snapshot(
        bundles,
        populations,
        snapshot_id=snapshot_id,
        as_of=as_of,
        gate_evidence=gate_evidence,
        raw_hashes=raw_hashes,
        previous=previous,
        detectors=detectors,
    )


def score_snapshot_dir(
    snapshot_dir: Path,
    *,
    previous_dir: Optional[Path] = None,
) -> tuple[ScoredSnapshot, FrozenFixture]:
    """Load and score ``snapshot_dir`` for real, detectors wired in
    (:func:`score_snapshot_with_detectors` -- the same function ``scripts/
    build_fixture_snapshot.py``'s pass-2 scoring calls).

    ``previous_dir`` (optional): scored the same way (:func:`_score_previous`)
    and passed as ``previous=`` so the cross-snapshot detector
    (``param_jump``) has something to compare against; ``None`` when
    omitted, exactly like a snapshot's first-ever run."""
    frozen = load_frozen_fixture(snapshot_dir)
    previous = _score_previous(previous_dir) if previous_dir is not None else None
    scored = score_snapshot_with_detectors(
        frozen.bundles,
        frozen.populations,
        snapshot_id=frozen.snapshot_id,
        as_of=frozen.as_of,
        gate_evidence=frozen.gate_evidence,
        raw_hashes=frozen.raw_hashes,
        previous=previous,
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
    section 5 Merkle root (:func:`porchlamp.snapshot.manifest.merkle_root` -- the
    same function a real ``porchlamp.snapshot freeze`` folds envelope hashes
    with, R-2, no second Merkle implementation) over every bundle's own
    ``raw_hash`` in this snapshot plus ``populations_hash`` when present
    (matching :func:`porchlamp.snapshot.build_cmd.build_snapshot_dir` and
    :func:`scripts.build_fixture_snapshot.write_fixture`, E-01), sorted for
    a byte-identical root regardless of dict-iteration order."""
    leaves = list(frozen.raw_hashes.values())
    if frozen.populations_hash is not None:
        leaves.append(frozen.populations_hash)
    return merkle_root(sorted(leaves))


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
    ``_end`` -- ``porchlamp.store.writer``'s documented "join an ambient
    transaction" contract, which exercises the identical ``conn.execute("BEGIN")`` idiom): if
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
    except Exception:  # review-ok: rollback transaction on any write failure and re-raise
        conn.rollback()
        raise
    conn.commit()

    return finalize_snapshot_hash(conn, db_path, scored.snapshot_id, promoted_at=as_of_text)
