"""Snapshot manifests and reproduction (packet W0-3, `04` §2 ``snapshot/``).

``freeze`` freezes the envelope hashes of a data dir into
``snapshots/<id>/manifest.json`` with a Merkle root; ``reproduce`` rebuilds the
database from those envelopes and compares a canonical dump hash. Envelope
hashing itself lives in :mod:`cvi.collect.envelope` (one definition per fact).
"""

from __future__ import annotations

from cvi.snapshot.manifest import (
    build_manifest,
    constants_hash,
    envelope_entries,
    load_manifest,
    merkle_root,
    snapshot_id,
    write_manifest,
)
from cvi.snapshot.reproduce import canonical_dump, canonical_dump_hash, rebuild, reproduce

__all__ = [
    "build_manifest",
    "canonical_dump",
    "canonical_dump_hash",
    "constants_hash",
    "envelope_entries",
    "load_manifest",
    "merkle_root",
    "rebuild",
    "reproduce",
    "snapshot_id",
    "write_manifest",
]
