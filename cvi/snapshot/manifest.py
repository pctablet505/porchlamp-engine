"""Snapshot manifest: the frozen envelope hashes of a data dir (`03` §5, `04` §2).

A freeze records one entry per envelope -- ``(collector, key, sha256)`` plus the
file it came from -- and folds them into a Merkle root. The snapshot is then
``(as_of, manifest_root, benchmark_version, constants_hash, code_commit)``
(D-VER-3): two freezes over the same envelopes at the same ``as_of`` name the
same snapshot and write the same bytes. Nothing here reads a clock, so the
manifest is a function of the data directory and its arguments only.

Merkle definition: the leaves are the :func:`entry_leaf` digests, each leaf is
hashed once more to separate the leaf domain, a parent is
``sha256(left_hex + right_hex)``, a level with an odd count reuses its last node
as its own right child, and an empty tree is ``sha256(canonical_json([]))``.
"""

from __future__ import annotations

import json
import re
import subprocess
from cvi import __version__ as BENCHMARK_VERSION
from cvi import constants as _constants
from cvi.collect.envelope import canonical_json, envelope_hash, read_envelope, sha256_hex
from pathlib import Path
from typing import Any, Optional, Sequence

#: Directory under a data dir that holds one directory per snapshot.
SNAPSHOTS_DIRNAME = "snapshots"
#: Name of the manifest inside a snapshot directory.
MANIFEST_FILENAME = "manifest.json"
#: Manifest layout version, so a reader knows what it is holding.
MANIFEST_VERSION = 1
#: Dump hash recorded while a freeze is mid-flight: the real one needs a rebuilt
#: database, which needs the snapshot id, which needs the Merkle root.
PENDING_DUMP_HASH = "pending"

_ROOT_PREFIX = 12
_UNSAFE = re.compile(r"[^0-9A-Za-z]+")


def envelope_entries(data_dir: Path) -> list[dict[str, Any]]:
    """Hash every envelope under ``<data_dir>/raw/<collector>/<key>.json``.

    Entries come back sorted by ``(collector, key)``, each carrying its hash
    (``sha256``), size and path. Paths are relative to the data dir, so a frozen
    manifest still names its envelopes after the dir has been copied elsewhere.

    The hash is the one computed from the bytes on disk, never the one an
    envelope asserts about itself: that is what makes the Merkle root evidence
    about the bytes, so editing ``data`` inside an envelope that still carries
    its old ``sha256`` moves the root instead of hiding behind it. An envelope
    whose recorded hash no longer describes its data is named in
    ``hash_mismatches`` (packet W0-3 read-time backfill rule). Envelopes written before
    packet W0-3 carry no ``sha256`` field: their ``data`` field is hashed here,
    at read time, and the entry is reported as ``backfilled``.
    """
    root = Path(data_dir)
    raw = root / "raw"
    collectors = sorted((path for path in raw.iterdir() if path.is_dir())) if raw.is_dir() else []
    entries: list[dict[str, Any]] = []
    for collector_dir in collectors:
        for path in sorted(collector_dir.glob("*.json")):
            envelope = read_envelope(path)
            entries.append(
                {
                    "collector": collector_dir.name,
                    "key": path.stem,
                    "path": str(path.relative_to(root)),
                    "sha256": envelope.computed_sha256,
                    "bytes": path.stat().st_size,
                    "hash_origin": envelope.hash_origin,
                    "hash_matches_data": envelope.hash_matches_data,
                }
            )
    return sorted(entries, key=lambda entry: (entry["collector"], entry["key"]))


def entry_leaf(entry: dict[str, Any]) -> str:
    """Merkle leaf for one entry: hash of ``[collector, key, sha256]``."""
    return envelope_hash([entry["collector"], entry["key"], entry["sha256"]])


def merkle_root(leaves: Sequence[str]) -> str:
    """Root over ``leaves``: each leaf is hashed, then pairs fold with ``sha256``.

    A level with an odd count reuses its last node as its own right child, so the
    tree shape is a function of the entry count alone.
    """
    level = [sha256_hex(leaf) for leaf in leaves]
    if not level:
        return sha256_hex(canonical_json([]))
    while len(level) > 1:
        level = [
            sha256_hex(
                level[index] + (level[index + 1] if index + 1 < len(level) else level[index])
            )
            for index in range(0, len(level), 2)
        ]
    return level[0]


def constants_hash() -> str:
    """Hash of every tuning constant in :mod:`cvi.constants` (D-VER-2).

    Covers each public upper-case binding, so changing one weight moves the hash
    and a snapshot can never be scored with constants it was not frozen against.
    """
    values = {
        name: getattr(_constants, name)
        for name in dir(_constants)
        if name.isupper() and not name.startswith("_")
    }
    return envelope_hash(values)


def code_commit(work_tree: Path) -> Optional[str]:
    """Commit sha of the checked-out code, or ``None`` outside a repository."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=work_tree,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def snapshot_id(as_of: str, root: str) -> str:
    """Filesystem-safe snapshot id: the ``as_of`` stamp plus the root prefix."""
    return f"{_UNSAFE.sub('', as_of)}-{root[:_ROOT_PREFIX]}"


def build_manifest(
    entries: Sequence[dict[str, Any]],
    as_of: str,
    code_commit: str,
    canonical_dump_hash: str,
) -> dict[str, Any]:
    """Assemble the manifest for ``entries`` at ``as_of``.

    ``hash_origins`` and ``backfilled_keys`` make the read-time backfill visible
    inside the manifest: a reader can see which hashes the collector recorded and
    which packet W0-3 reconstructed. ``hash_mismatches`` names the envelopes
    whose recorded ``sha256`` no longer describes their ``data`` field.
    """
    keys = [f"{entry['collector']}/{entry['key']}" for entry in entries]
    root = merkle_root([entry_leaf(entry) for entry in entries])
    return {
        "manifest_version": MANIFEST_VERSION,
        "snapshot_id": snapshot_id(as_of, root),
        "as_of": as_of,
        "benchmark_version": BENCHMARK_VERSION,
        "constants_hash": constants_hash(),
        "code_commit": code_commit,
        "canonical_dump_hash": canonical_dump_hash,
        "merkle_root": root,
        "envelope_count": len(entries),
        "hash_origins": {
            "native": sum(entry["hash_origin"] == "native" for entry in entries),
            "backfilled": sum(entry["hash_origin"] == "backfilled" for entry in entries),
        },
        "backfilled_keys": [
            key
            for key, entry in zip(keys, entries)
            if entry["hash_origin"] == "backfilled"
        ],
        "hash_mismatches": [
            key for key, entry in zip(keys, entries) if not entry["hash_matches_data"]
        ],
        "envelopes": [
            {
                "collector": entry["collector"],
                "key": entry["key"],
                "path": entry["path"],
                "sha256": entry["sha256"],
                "bytes": entry["bytes"],
                "hash_origin": entry["hash_origin"],
            }
            for entry in entries
        ],
    }


def snapshot_dir(data_dir: Path, snapshot: str) -> Path:
    """Directory holding one snapshot's manifest and its rebuild output."""
    return Path(data_dir) / SNAPSHOTS_DIRNAME / snapshot


def write_manifest(data_dir: Path, manifest: dict[str, Any]) -> Path:
    """Write ``<data_dir>/snapshots/<id>/manifest.json`` and return its path.

    Sorted keys and fixed indent: a freeze writes the same bytes twice, so two
    manifests can be diffed as text.
    """
    target = snapshot_dir(data_dir, manifest["snapshot_id"]) / MANIFEST_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def load_manifest(data_dir: Path, snapshot: str) -> dict[str, Any]:
    """Read the manifest of ``snapshot``, naming the path when it is absent."""
    target = snapshot_dir(data_dir, snapshot) / MANIFEST_FILENAME
    if not target.is_file():
        raise FileNotFoundError(f"no snapshot {snapshot!r}: {target} does not exist")
    return json.loads(target.read_text(encoding="utf-8"))
