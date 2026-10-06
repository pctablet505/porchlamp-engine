"""Replay GH Archive hourly public-event dumps into Porchlamp ledgers (08 D-COL-11, W6-10).

Filters hourly GH Archive dumps (``https://data.gharchive.org/YYYY-MM-DD-H.json.gz``)
to the candidate universe in ``<data_dir>/raw/candidates.jsonl``, appends
versioned records to ``<data_dir>/ledgers/<ledger>/<owner>__<name>.jsonl`` with
``observed_at`` = the dump hour, and optionally runs the ``ledger_vs_archive``
detector over the replayed hour.

Usage:
  .venv/bin/python scripts/gharchive_replay.py --hour 2026-09-20-14
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


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--hour",
        required=True,
        help="GH Archive hour specifier in YYYY-MM-DD-H format (UTC, hour 0..23)",
    )
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
        help="local .json.gz or .jsonl dump file to read instead of fetching over HTTPS",
    )
    ap.add_argument(
        "--verify-only",
        action="store_true",
        help="run ledger_vs_archive without appending to ledgers",
    )
    args = ap.parse_args(argv)

    candidates = _load_candidates(args.data_dir / "raw" / "candidates.jsonl")
    ledgers_dir = args.data_dir / "ledgers"

    if args.archive_file is not None:
        raw_bytes = args.archive_file.read_bytes()
        compressed = args.archive_file.name.endswith(".gz")
    else:
        raw_bytes = fetch_gharchive_hour_bytes(args.hour)
        compressed = True

    events = list(iter_jsonl_bytes(raw_bytes, compressed=compressed))
    if args.verify_only:
        observed_dt = hour_to_observed_at(args.hour)
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
            f"gharchive verify {args.hour}: {len(archive_records)} archive records, "
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
        hour_spec=args.hour,
    )
    print(
        f"gharchive replay {report.hour_spec}: scanned={report.events_scanned} "
        f"matched={report.events_matched} appended={report.records_appended} "
        f"repos={report.repos_touched}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
