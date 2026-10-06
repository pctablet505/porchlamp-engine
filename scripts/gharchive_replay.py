"""Replay GH Archive hourly public-event dumps into Porchlamp ledgers (08 D-COL-11, W6-10).

Filters hourly GH Archive dumps (``https://data.gharchive.org/YYYY-MM-DD-H.json.gz``)
to the candidate universe in ``<data_dir>/raw/candidates.jsonl``, appends
versioned records to ``<data_dir>/ledgers/<ledger>/<owner>__<name>.jsonl`` with
``observed_at`` = the END of the dump hour, and optionally runs the
``ledger_vs_archive`` detector over the replayed hour(s).

Each hour is downloaded into memory (never to disk) and dropped before the next.
The replay REFUSES (exit 2, nothing written for that hour) when an event of a
universe repo lacks the payload fields its ledger record needs -- GH Archive
trims payloads (no ``commits`` on a push, no PR state or dates) -- or when a
dump exceeds the 256 MiB cap. Hours already replayed stay: replay is idempotent.

Usage:
  .venv/bin/python scripts/gharchive_replay.py --hour 2026-09-20-14
  .venv/bin/python scripts/gharchive_replay.py --start 2026-09-20-0 --end 2026-09-20-23
  .venv/bin/python scripts/gharchive_replay.py --hour 2026-09-20-14 --archive-file /path/to/dump.json.gz
  .venv/bin/python scripts/gharchive_replay.py --hour 2026-09-20-14 --verify-only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from porchlamp.collect.gharchive import (
    ArchiveLedgerRecord,
    fetch_gharchive_hour_bytes,
    hour_to_observed_at,
    iter_hours,
    iter_jsonl_bytes,
    ledger_vs_archive,
    replay_hour_stream,
    shape_gharchive_event,
)
from porchlamp.paths import data_root


def _load_candidates(candidates_file: Path) -> list[tuple[str, str]]:
    if not candidates_file.is_file():
        return []
    out: list[tuple[str, str]] = []
    for line in candidates_file.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        row = json.loads(text)
        owner = row.get("owner")
        name = row.get("name")
        if isinstance(owner, str) and owner and isinstance(name, str) and name:
            out.append((owner, name))
    return out


def _process_hour(
    hour: str,
    *,
    candidates: list[tuple[str, str]],
    ledgers_dir: Path,
    archive_file: Optional[Path],
    verify_only: bool,
) -> int:
    """Replay (or verify) one hour; returns 0, or 1 for verify discrepancies."""
    if archive_file is not None:
        raw_bytes = archive_file.read_bytes()
        compressed = archive_file.name.endswith(".gz")
    else:
        raw_bytes = fetch_gharchive_hour_bytes(hour)
        compressed = True

    events = list(iter_jsonl_bytes(raw_bytes, compressed=compressed))
    del raw_bytes
    if verify_only:
        observed_dt = hour_to_observed_at(hour)
        observed_iso = observed_dt.isoformat()
        lookup = {f"{o.lower()}/{n.lower()}": (o, n) for o, n in candidates}
        archive_records: list[ArchiveLedgerRecord] = []
        for ev in events:
            archive_records.extend(shape_gharchive_event(ev, lookup, observed_iso))
        discrepancies = ledger_vs_archive(
            ledgers_dir,
            archive_records,
            window_start=observed_dt,
            window_end=observed_dt,
        )
        print(
            f"gharchive verify {hour}: {len(archive_records)} archive records, "
            f"{len(discrepancies)} discrepancies"
        )
        for d in discrepancies:
            print(
                f"  DISCREPANCY {d.repo_key} [{d.ledger}]: "
                f"ledger={d.ledger_count} archive={d.archive_count} "
                f"missing={list(d.missing_in_ledger)} extra={list(d.extra_in_ledger)}"
            )
        return 1 if discrepancies else 0

    report = replay_hour_stream(
        events,
        candidates=candidates,
        ledgers_dir=ledgers_dir,
        hour_spec=hour,
    )
    print(
        f"gharchive replay {report.hour_spec}: scanned={report.events_scanned} "
        f"matched={report.events_matched} appended={report.records_appended} "
        f"repos={report.repos_touched}"
    )
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--hour",
        default=None,
        help="one GH Archive hour in YYYY-MM-DD-H format (UTC, hour 0..23)",
    )
    ap.add_argument("--start", default=None, help="first hour of a range (YYYY-MM-DD-H), inclusive")
    ap.add_argument("--end", default=None, help="last hour of a range (YYYY-MM-DD-H), inclusive")
    ap.add_argument(
        "--data-dir",
        type=Path,
        default=data_root(),
        help="Porchlamp data directory containing raw/candidates.jsonl and ledgers/",
    )
    ap.add_argument(
        "--archive-file",
        type=Path,
        default=None,
        help="local .json.gz or .jsonl dump file to read instead of fetching over HTTPS "
             "(one --hour only)",
    )
    ap.add_argument(
        "--verify-only",
        action="store_true",
        help="run ledger_vs_archive without appending to ledgers",
    )
    args = ap.parse_args(argv)

    if args.hour is not None and (args.start is not None or args.end is not None):
        ap.error("--hour cannot be combined with --start/--end")
    if args.hour is None and (args.start is None or args.end is None):
        ap.error("give --hour, or both --start and --end")
    if args.archive_file is not None and args.hour is None:
        ap.error("--archive-file holds one hour: use it with --hour")
    try:
        hours = [args.hour] if args.hour is not None else list(iter_hours(args.start, args.end))
    except ValueError as e:
        ap.error(str(e))

    candidates = _load_candidates(args.data_dir / "raw" / "candidates.jsonl")
    ledgers_dir = args.data_dir / "ledgers"
    rc = 0
    for hour in hours:
        try:
            rc = max(rc, _process_hour(
                hour,
                candidates=candidates,
                ledgers_dir=ledgers_dir,
                archive_file=args.archive_file,
                verify_only=args.verify_only,
            ))
        except ValueError as e:  # incomplete payload, or a dump over the size cap
            print(f"gharchive replay REFUSED at hour {hour}: {e}", file=sys.stderr)
            return 2
        except OSError as e:  # network or file error: the hours before this one are kept
            print(f"gharchive replay FAILED at hour {hour}: {e}", file=sys.stderr)
            return 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
