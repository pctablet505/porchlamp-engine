"""Snapshot manifests and reproduction (packet W0-3, `04` §2 ``snapshot/``).

``freeze`` freezes the envelope hashes of a data dir into
``snapshots/<id>/manifest.json`` with a Merkle root; ``reproduce`` rebuilds the
database from those envelopes and compares a canonical dump hash. Envelope
hashing itself lives in :mod:`cvi.collect.envelope` (one definition per fact).
"""

from __future__ import annotations

from cvi.snapshot.manifest import (
    PENDING_DUMP_HASH,
    REASON_INVALID_JSON,
    REASON_INVALID_JSON_FROZEN,
    REASON_STALE_HASH,
    SNAPSHOTS_DIRNAME,
    EnvelopeScan,
    build_manifest,
    code_commit,
    constants_hash,
    envelope_entries,
    load_manifest,
    merkle_root,
    snapshot_dir,
    snapshot_id,
    write_manifest,
)
from cvi.snapshot.reproduce import (
    SnapshotError,
    canonical_dump,
    canonical_dump_hash,
    rebuild,
    reproduce,
)
from cvi.snapshot.store_cmd import (
    VerificationError,
    score_snapshot_dir,
    verify_expected,
    write_scored_snapshot,
)

__all__ = [
    "EnvelopeScan",
    "PENDING_DUMP_HASH",
    "REASON_INVALID_JSON",
    "REASON_INVALID_JSON_FROZEN",
    "REASON_STALE_HASH",
    "SNAPSHOTS_DIRNAME",
    "SnapshotError",
    "VerificationError",
    "build_manifest",
    "canonical_dump",
    "canonical_dump_hash",
    "code_commit",
    "constants_hash",
    "envelope_entries",
    "load_manifest",
    "merkle_root",
    "rebuild",
    "reproduce",
    "score_snapshot_dir",
    "snapshot_dir",
    "snapshot_id",
    "verify_expected",
    "write_manifest",
    "write_scored_snapshot",
]
