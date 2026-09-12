"""``cvi snapshot freeze`` and ``cvi snapshot reproduce`` (packet W0-3).

The CLI layer owns the wall clock and every ``print`` in the packet; the modules
behind it are pure. ``--data-dir`` is a data directory (or a copied sample) with
``raw/`` under it; ``--as-of`` is the snapshot reference time (D-VER-3) that both
deriving and scoring are pinned to.

Invocation before ``cvi/cli.py`` exists::

    .venv/bin/python -m cvi.snapshot freeze --as-of 2026-09-12T00:00:00+00:00
    .venv/bin/python -m cvi.snapshot reproduce <snapshot-id>
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from cvi.snapshot.manifest import (
    PENDING_DUMP_HASH,
    SNAPSHOTS_DIRNAME,
    build_manifest,
    code_commit,
    envelope_entries,
    snapshot_dir,
    write_manifest,
)
from cvi.snapshot.reproduce import SnapshotError, canonical_dump_hash, rebuild, reproduce

_AS_OF_HELP = "snapshot reference time, ISO-8601 (D-VER-3): what windows are measured against"
_CODE_ROOT = Path(__file__).resolve().parents[2]


def _freeze(args: argparse.Namespace) -> int:
    """Freeze ``--data-dir`` at ``--as-of`` and print the snapshot identity."""
    data_dir = Path(args.data_dir)
    try:
        as_of = datetime.fromisoformat(args.as_of)
    except ValueError:
        print(f"--as-of is not an ISO-8601 timestamp: {args.as_of!r}")
        return 2
    commit = args.code_commit or code_commit(_CODE_ROOT) or "unknown"
    entries = envelope_entries(data_dir)
    frozen = build_manifest(
        entries,
        as_of=as_of.isoformat(),
        code_commit=commit,
        canonical_dump_hash=PENDING_DUMP_HASH,
    )
    work = snapshot_dir(data_dir, frozen["snapshot_id"]) / "build"
    dump_hash = canonical_dump_hash(rebuild(data_dir, frozen, work))
    manifest = build_manifest(
        entries,
        as_of=as_of.isoformat(),
        code_commit=commit,
        canonical_dump_hash=dump_hash,
    )
    path = write_manifest(data_dir, manifest)
    print(f"snapshot {manifest['snapshot_id']}")
    print(f"merkle_root {manifest['merkle_root']}")
    print(
        f"envelopes {manifest['envelope_count']} "
        f"(native {manifest['hash_origins']['native']}, "
        f"backfilled {manifest['hash_origins']['backfilled']})"
    )
    print(f"dump_hash {manifest['canonical_dump_hash']}")
    print(f"manifest {path}")
    return 0


def _reproduce(args: argparse.Namespace) -> int:
    """Rebuild a snapshot and print every difference from what was frozen."""
    report = reproduce(Path(args.data_dir), args.snapshot_id)
    check = report.envelope_check
    print(f"snapshot {report.snapshot_id}")
    print(f"envelopes {check.matched} match, {len(check.mismatched)} mismatched, "
          f"{len(check.missing)} missing, {len(check.unexpected)} unexpected")
    for key in check.mismatched:
        print(f"MISMATCHED {key}")
    for key in check.missing:
        print(f"MISSING {key}")
    for key in check.unexpected:
        print(f"UNEXPECTED {key}")
    print(f"dump_hash {report.dump_hash}")
    print(f"frozen_dump_hash {report.frozen_dump_hash}")
    print("MATCH" if report.ok else "MISMATCH")
    return 0 if report.ok else 1


def _parser() -> argparse.ArgumentParser:
    """Build the ``freeze`` / ``reproduce`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="cvi snapshot",
        description="Freeze a snapshot manifest and reproduce it from raw envelopes.",
    )
    commands = parser.add_subparsers(dest="command")

    freeze = commands.add_parser("freeze", help="hash every envelope and write manifest.json")
    freeze.add_argument("--data-dir", default="data", help="data dir with raw/ under it")
    freeze.add_argument("--as-of", required=True, help=_AS_OF_HELP)
    freeze.add_argument("--code-commit", default=None,
                        help="override the recorded commit (tests, out-of-tree builds)")

    replay = commands.add_parser("reproduce", help="rebuild from raw and compare the dump hash")
    replay.add_argument("snapshot_id", help="snapshot directory name under "
                        f"{SNAPSHOTS_DIRNAME}/")
    replay.add_argument("--data-dir", default="data", help="data dir with raw/ under it")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run ``cvi snapshot``; returns the process exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_usage()
        return 2
    handlers = {"freeze": _freeze, "reproduce": _reproduce}
    try:
        return handlers[str(args.command)](args)
    except (SnapshotError, FileNotFoundError) as error:
        print(f"{type(error).__name__}: {error}")
        return 2
