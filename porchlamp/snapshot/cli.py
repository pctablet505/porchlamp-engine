"""``porchlamp snapshot freeze``, ``porchlamp snapshot reproduce``,
``porchlamp snapshot store``, ``porchlamp snapshot build``, ``porchlamp snapshot stats`` and
``porchlamp snapshot as-of``.

The CLI layer owns the wall clock and every ``print``; the modules
behind it are pure. ``--data-dir`` is a data directory (or a copied sample) with
``raw/`` under it; ``--as-of`` is the snapshot reference time (D-VER-3) that both
deriving and scoring are pinned to.

``store`` has no wall clock of its own either: every timestamp it writes
(``as_of``/``created_at``/``promoted_at``) comes from the snapshot
directory's own ``manifest.json``, via ``porchlamp.snapshot.store_cmd`` (that
module's own docstring).

Invocation before ``porchlamp/cli.py`` exists::

    .venv/bin/python -m porchlamp.snapshot freeze --as-of 2026-09-12T00:00:00+00:00
    .venv/bin/python -m porchlamp.snapshot reproduce <snapshot-id>
    .venv/bin/python -m porchlamp.snapshot store <snapshot-dir> --db data/cvi.db
    .venv/bin/python -m porchlamp.snapshot stats <snapshot.db> --data-dir data > stats-<snapshot-id>.json
    .venv/bin/python -m porchlamp.snapshot as-of --data-dir data

``stats`` prints the report's stats file (``porchlamp.snapshot.stats``): the star
figures and corpus totals ``scripts/export_static.py --stats`` reads.

``as-of`` is the pre-flight for ``--as-of``: it prints the earliest instant every guard accepts
(``porchlamp.snapshot.asof``) and PASS/FAIL/WARN/INFO per guard before anything is derived or built.
Exit 0 when no guard fails, 1 on a failed guard or when no valid value exists, 2 on a usage or
read error.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

import porchlamp.snapshot.asof as _asof
import porchlamp.snapshot.build_cmd as _build_cmd
from porchlamp.snapshot.manifest import (
    PENDING_DUMP_HASH,
    REASON_INVALID_JSON,
    REASON_INVALID_JSON_FROZEN,
    REASON_STALE_HASH,
    SNAPSHOTS_DIRNAME,
    EnvelopeScan,
    build_manifest,
    code_commit,
    envelope_entries,
    snapshot_dir,
    write_manifest,
)
from porchlamp.snapshot.reproduce import SnapshotError, canonical_dump_hash, rebuild, reproduce
from porchlamp.snapshot.stats import StatsError, compute_stats
from porchlamp.snapshot.store_cmd import VerificationError, score_snapshot_dir, verify_expected, write_scored_snapshot
from porchlamp.store.migrations import apply_migrations
from porchlamp.store.schema import connect as connect_store

_AS_OF_HELP = "snapshot reference time, ISO-8601 (D-VER-3): what windows are measured against"
_CODE_ROOT = Path(__file__).resolve().parents[2]


def _freeze(args: argparse.Namespace) -> int:
    """Freeze ``--data-dir`` at ``--as-of`` and print the snapshot identity.

    Refuses to write a manifest when the scan finds an unreadable envelope:
    the manifest is the promotion boundary (`03` section 5), so a snapshot
    born short a repository must be the operator's explicit choice
    (``--allow-invalid``), never a silent default that a later ``reproduce``
    cannot detect once the corrupt file is gone.
    """
    data_dir = Path(args.data_dir)
    try:
        as_of = datetime.fromisoformat(args.as_of)
    except ValueError:
        print(f"--as-of is not an ISO-8601 timestamp: {args.as_of!r}")
        return 2
    commit = args.code_commit or code_commit(_CODE_ROOT) or "unknown"
    scan: EnvelopeScan = envelope_entries(data_dir)
    invalid_keys = [f"{item['collector']}/{item['key']}" for item in scan.invalid_json]
    if invalid_keys and not args.allow_invalid:
        for key in invalid_keys:
            print(f"{REASON_INVALID_JSON} {key}")
        print(
            f"refusing to freeze: {len(invalid_keys)} unreadable envelope(s); "
            "pass --allow-invalid to write a manifest anyway"
        )
        return 1
    frozen = build_manifest(
        scan.entries,
        as_of=as_of.isoformat(),
        code_commit=commit,
        canonical_dump_hash=PENDING_DUMP_HASH,
        invalid_json=invalid_keys,
    )
    work = snapshot_dir(data_dir, frozen["snapshot_id"]) / "build"
    dump_hash = canonical_dump_hash(rebuild(data_dir, frozen, work))
    manifest = build_manifest(
        scan.entries,
        as_of=as_of.isoformat(),
        code_commit=commit,
        canonical_dump_hash=dump_hash,
        invalid_json=invalid_keys,
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
    for key in invalid_keys:
        print(f"{REASON_INVALID_JSON} {key}")
    return 1 if invalid_keys else 0


def _reproduce(args: argparse.Namespace) -> int:
    """Rebuild a snapshot and print every difference from what was frozen."""
    report = reproduce(Path(args.data_dir), args.snapshot_id)
    check = report.envelope_check
    print(f"snapshot {report.snapshot_id}")
    print(f"envelopes {check.matched} match, {len(check.mismatched)} mismatched, "
          f"{len(check.missing)} missing, {len(check.unexpected)} unexpected, "
          f"{len(check.invalid_json)} invalid, "
          f"{len(check.stale_native_hashes)} stale-hash, "
          f"{len(check.invalid_json_frozen)} invalid-frozen")
    for key in check.mismatched:
        print(f"MISMATCHED {key}")
    for key in check.missing:
        print(f"MISSING {key}")
    for key in check.unexpected:
        print(f"UNEXPECTED {key}")
    for key in check.invalid_json:
        print(f"{REASON_INVALID_JSON} {key}")
    for key in check.stale_native_hashes:
        print(f"{REASON_STALE_HASH} {key}")
    for key in check.invalid_json_frozen:
        print(f"{REASON_INVALID_JSON_FROZEN} {key}")
    print(f"dump_hash {report.dump_hash}")
    print(f"frozen_dump_hash {report.frozen_dump_hash}")
    print("MATCH" if report.ok else "MISMATCH")
    return 0 if report.ok else 1


def _store(args: argparse.Namespace) -> int:
    """Score ``args.snapshot_dir`` for real and write it into the v2 store
    at ``args.db``, printing counts and the reproducibility
    hashes. Every timestamp comes from the directory's own manifest -- see
    the module docstring.

    Exit 1 with a one-line message (no traceback) for every failure this
    command can name and explain to an operator: the directory is missing
    a required file (``FileNotFoundError``), its ``expected.json``
    disagrees with the freshly computed ``records_hash``
    (:class:`~porchlamp.snapshot.store_cmd.VerificationError`, skipped entirely
    when ``--no-verify`` is passed), or ``--db`` already holds this
    snapshot id (``porchlamp.store.writer.write_records``'s immutability
    ``ValueError`` -- 03 section 5: a snapshot id is immutable, never
    upserted, never replaced). Anything else is a programming error and is
    left to propagate with its traceback, same as ``_freeze``/``_reproduce``."""
    snapshot_path = Path(args.snapshot_dir)
    db_path = Path(args.db)
    previous_path = Path(args.previous) if args.previous else None

    try:
        scored, frozen = score_snapshot_dir(snapshot_path, previous_dir=previous_path)
    except FileNotFoundError as error:
        print(f"{type(error).__name__}: {error}")
        return 1

    if not args.no_verify:
        try:
            verify_expected(frozen, scored)
        except VerificationError as error:
            print(str(error))
            return 1

    commit = args.code_commit or code_commit(_CODE_ROOT) or "unknown"
    conn = connect_store(db_path)
    try:
        apply_migrations(conn, applied_at=frozen.as_of.isoformat())
        dump_hash = write_scored_snapshot(conn, db_path, scored, frozen, code_commit=commit)
    except ValueError as error:
        print(f"{type(error).__name__}: {error}")
        return 1
    finally:
        conn.close()

    print(f"snapshot {scored.snapshot_id}")
    print(
        f"rated {scored.counts.rated} gated {scored.counts.gated} unrated {scored.counts.unrated}"
    )
    print(f"records_hash {scored.records_hash}")
    print(f"constants_hash {scored.constants_hash}")
    print(f"snapshot_dump_hash {dump_hash}")
    return 0


def _build(args: argparse.Namespace) -> int:
    """Build a v2 snapshot directory from ``args.data_dir`` at ``args.out_dir``."""
    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    try:
        as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
    except ValueError:
        print(f"--as-of is not an ISO-8601 timestamp: {args.as_of!r}")
        return 2
    frozen = _build_cmd.build_snapshot_dir(
        data_dir,
        out_dir,
        snapshot_id=args.snapshot_id,
        as_of=as_of,
        code_commit_sha=args.code_commit,
        workers=args.workers,
    )
    print(f"snapshot {frozen.snapshot_id}")
    print(f"bundles {len(frozen.bundles)}")
    print(f"out_dir {out_dir}")
    return 0


def _stats(args: argparse.Namespace) -> int:
    """Print the stats file for the snapshot database ``args.db``."""
    try:
        stats = compute_stats(Path(args.db), Path(args.data_dir), as_of=args.as_of)
    except StatsError as error:
        print(f"StatsError: {error}")
        return 2
    print(json.dumps(stats, indent=2))
    return 0


def _as_of(args: argparse.Namespace) -> int:
    """Print the earliest valid ``as_of`` of ``args.data_dir`` and every guard's verdict at it (or at
    ``--as-of``, which is then checked instead of computed). ``--print`` writes only the value, and only
    when no guard fails, for ``AS_OF=$(...)``; the report goes to stderr then. Exit 0 when nothing FAILs,
    1 on a FAIL or when no valid value exists, 2 on a usage or read error (printed to stderr)."""
    given: Optional[datetime] = None
    if args.as_of is not None:
        given = _asof.parse_as_of(args.as_of)
        if given is None:
            print(f"--as-of is not an ISO-8601 timestamp: {args.as_of!r}", file=sys.stderr)
            return 2
    try:
        scan = _asof.scan_root(Path(args.data_dir))
    except _asof.AsOfError as error:
        print(f"as-of: {error}", file=sys.stderr)
        return 2
    plan = _asof.plan_as_of(scan)
    as_of = given if given is not None else plan.earliest
    results = _asof.check_as_of(scan, as_of) if as_of is not None else ()
    ok = as_of is not None and _asof.passed(results)
    report = "\n".join(_asof.format_report(scan, plan, results, as_of, given is not None))
    if not args.print:
        print(report)
    elif ok and as_of is not None:
        print(_asof.iso(as_of))
    else:
        print(report, file=sys.stderr)
    return 0 if ok else 1


def _positive_int(text: str) -> int:
    """An argparse type: an integer of at least 1."""
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {value}")
    return value


def _parser() -> argparse.ArgumentParser:
    """Build the ``freeze`` / ``reproduce`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="porchlamp snapshot",
        description="Freeze a snapshot manifest and reproduce it from raw envelopes.",
    )
    commands = parser.add_subparsers(dest="command")

    freeze = commands.add_parser("freeze", help="hash every envelope and write manifest.json")
    freeze.add_argument("--data-dir", default="data", help="data dir with raw/ under it")
    freeze.add_argument("--as-of", required=True, help=_AS_OF_HELP)
    freeze.add_argument("--code-commit", default=None,
                        help="override the recorded commit (tests, out-of-tree builds)")
    freeze.add_argument(
        "--allow-invalid", action="store_true",
        help="write the manifest even when an envelope is unreadable "
             "(03 section 5: an explicit operator choice, never a silent default)",
    )

    replay = commands.add_parser("reproduce", help="rebuild from raw and compare the dump hash")
    replay.add_argument("snapshot_id", help="snapshot directory name under "
                        f"{SNAPSHOTS_DIRNAME}/")
    replay.add_argument("--data-dir", default="data", help="data dir with raw/ under it")

    store = commands.add_parser(
        "store", help="score a snapshot directory and write it into a v2 store"
    )
    store.add_argument(
        "snapshot_dir",
        help="snapshot directory: bundles/*.json, populations.json, manifest.json[, expected.json]",
    )
    store.add_argument("--db", required=True, help="v2 store SQLite path (created if it does not exist)")
    store.add_argument(
        "--previous", default=None,
        help="a previous snapshot directory, scored to feed the cross-snapshot param_jump detector",
    )
    store.add_argument(
        "--no-verify", action="store_true",
        help="skip the expected.json records_hash check (03 section 5's fixture pin), if the "
             "directory has one",
    )
    store.add_argument("--code-commit", default=None,
                        help="override the recorded commit (tests, out-of-tree builds)")

    build = commands.add_parser(
        "build", help="build a v2 snapshot directory from derived bundles and raw envelopes"
    )
    build.add_argument("--data-dir", default="data", help="data dir with raw/ and derived/ under it")
    build.add_argument("--out-dir", required=True, help="output directory for the built v2 snapshot")
    build.add_argument("--snapshot-id", required=True, help="snapshot identifier")
    build.add_argument("--as-of", required=True, help=_AS_OF_HELP)
    build.add_argument("--code-commit", default=None,
                       help="override the recorded commit (tests, out-of-tree builds)")
    build.add_argument("--workers", type=_positive_int, default=None,
                       help="processes that load the bundles and gate evidence (default: one per CPU); "
                            "the snapshot written is the same for any count")

    stats = commands.add_parser(
        "stats", help="print the report's stats file (star figures, corpus totals) for a snapshot database"
    )
    stats.add_argument("db", help="the snapshot database (opened read-only)")
    stats.add_argument(
        "--data-dir", required=True,
        help="data dir the snapshot was derived from: raw/github_meta/ (the star counts) and derived/ (pr_events)",
    )
    stats.add_argument("--as-of", default=None,
                       help="PR-age reference time, ISO-8601; default: the database's snapshots.as_of")

    as_of = commands.add_parser(
        "as-of",
        help="print the earliest valid --as-of for a data dir and check the cut and 14-day guards before a build",
    )
    as_of.add_argument(
        "--data-dir",
        required=True,
        help="data dir with raw/ under it (raw/candidates.jsonl lists the repos the guards read)",
    )
    as_of.add_argument(
        "--as-of",
        default=None,
        help="check this ISO-8601 time instead of computing one (the earliest valid time is still printed)",
    )
    as_of.add_argument(
        "--print",
        action="store_true",
        help="write only the value, and only when no guard fails (for AS_OF=$(...)); the report goes to stderr",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run ``porchlamp snapshot``; returns the process exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_usage()
        return 2
    handlers = {
        "freeze": _freeze,
        "reproduce": _reproduce,
        "store": _store,
        "build": _build,
        "stats": _stats,
        "as-of": _as_of,
    }
    try:
        return handlers[str(args.command)](args)
    except (SnapshotError, FileNotFoundError) as error:
        print(f"{type(error).__name__}: {error}")
        return 2
