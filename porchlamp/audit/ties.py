"""Composite tie audit.

``porchlamp audit ties`` reports how much the v1 ranking actually distinguishes
repos: the number of distinct composite (``overall_porchlamp``) values among the
rated repos and the size of each tie group (every value shared by two or
more rated repos).  A ranking whose composite values collapse into a few
tie groups cannot separate its top repos; D-AGG-3's tau report and this
audit are read together when judging whether the composite is a usable
standard.

Definitions (the audit's contract; the report repeats them)
-----------------------------------------------------------
rated repo
    A row with ``is_stage0_disqualified = 0``; gated repos are counted
    but excluded from the composite statistics.
composite
    The stored ``overall_porchlamp`` (D-AGG-2: 2-decimal weighted sum of the
    pillar scores).
tie group
    A distinct composite value shared by two or more rated repos;
    :attr:`TieReport.groups` is ordered by group size, then value.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

__version__ = "1.0.0"


@dataclass(frozen=True)
class TieGroup:
    """A composite value shared by several rated repos.

    Parameters
    ----------
    value:
        The ``overall_porchlamp`` value.
    size:
        Number of rated repos carrying that value.
    """

    value: float
    size: int


@dataclass(frozen=True)
class TieReport:
    """Tie statistics over the rated composites of one database.

    Parameters
    ----------
    rated:
        Number of rated (ungated) repos.
    gated:
        Number of gated repos (reported for context only).
    distinct:
        Number of distinct composite values among rated repos.
    groups:
        The tie groups, ordered by group size descending, then value.
    """

    rated: int
    gated: int
    distinct: int
    groups: tuple[TieGroup, ...]
    unrated: int = 0

    def size_histogram(self) -> dict[int, int]:
        """Map group size to the number of groups of that size."""
        histogram: dict[int, int] = {}
        for group in self.groups:
            histogram[group.size] = histogram.get(group.size, 0) + 1
        return dict(sorted(histogram.items()))

    def largest_size(self) -> int:
        """Size of the largest tie group, or 0 when none exists."""
        return max((group.size for group in self.groups), default=0)

    def share_in_tie_groups(self) -> float:
        """Share of rated repos that share a composite with someone."""
        if self.rated == 0:
            return 0.0
        in_ties = sum(group.size for group in self.groups)
        return in_ties / self.rated


def _repo_columns(con: sqlite3.Connection) -> set[str]:
    """Return column names present on ``repositories``."""
    return {row[1] for row in con.execute('PRAGMA table_info("repositories")').fetchall()}


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return (
        con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def _served_snapshot_id(con: sqlite3.Connection, cols: set[str]) -> Optional[str]:
    """Return the latest/served snapshot ID for a v2 database, if any."""
    if _table_exists(con, "snapshots"):
        row = con.execute(
            "SELECT id FROM snapshots WHERE promoted_at IS NOT NULL ORDER BY promoted_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if row is not None:
            return str(row[0])
        row = con.execute(
            "SELECT id FROM snapshots ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if row is not None:
            return str(row[0])
    if "snapshot_id" in cols:
        row = con.execute(
            "SELECT snapshot_id FROM repositories ORDER BY snapshot_id DESC LIMIT 1"
        ).fetchone()
        if row is not None:
            return str(row[0])
    return None


def _v2_composite_expr(cols: set[str]) -> str:
    if "composite" in cols and "porchlamp" in cols:
        return "COALESCE(composite, porchlamp)"
    if "composite" in cols:
        return "composite"
    return "porchlamp"


def ties_report(db_path: str | Path) -> TieReport:
    """Compute the tie statistics for ``db_path``.

    Parameters
    ----------
    db_path:
        Path to a v1 or v2 ``porchlamp.db`` (SQLite with a ``repositories`` table).

    Returns
    -------
    TieReport
        Distinct-value and tie-group statistics over rated repos.
    """
    con = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    try:
        cols = _repo_columns(con)
        if "rated" in cols and ("composite" in cols or "porchlamp" in cols):
            comp_expr = _v2_composite_expr(cols)
            snap_id = _served_snapshot_id(con, cols)
            where_snap = "WHERE snapshot_id = ?" if (snap_id is not None and "snapshot_id" in cols) else ""
            and_snap = "AND snapshot_id = ?" if (snap_id is not None and "snapshot_id" in cols) else ""
            params = (snap_id,) if (snap_id is not None and "snapshot_id" in cols) else ()

            counts = con.execute(
                f"SELECT {comp_expr}, COUNT(*) FROM repositories "
                f"WHERE rated = 1 {and_snap} GROUP BY {comp_expr}",
                params,
            ).fetchall()
            total = con.execute(
                f"SELECT COUNT(*) FROM repositories {where_snap}",
                params,
            ).fetchone()[0]
            rated = con.execute(
                f"SELECT COUNT(*) FROM repositories WHERE rated = 1 {and_snap}",
                params,
            ).fetchone()[0]
            if "eligible" in cols:
                gated = con.execute(
                    f"SELECT COUNT(*) FROM repositories WHERE eligible = 0 {and_snap}",
                    params,
                ).fetchone()[0]
                unrated = con.execute(
                    f"SELECT COUNT(*) FROM repositories WHERE eligible = 1 AND rated = 0 {and_snap}",
                    params,
                ).fetchone()[0]
            else:
                gated = total - rated
                unrated = 0
        else:
            counts = con.execute(
                "SELECT overall_porchlamp, COUNT(*) FROM repositories "
                "WHERE is_stage0_disqualified = 0 GROUP BY overall_porchlamp"
            ).fetchall()
            total = con.execute("SELECT COUNT(*) FROM repositories").fetchone()[0]
            rated = con.execute(
                "SELECT COUNT(*) FROM repositories WHERE is_stage0_disqualified = 0"
            ).fetchone()[0]
            gated = total - rated
            unrated = 0
    finally:
        con.close()
    valid_counts = [(value, count) for value, count in counts if value is not None]
    groups = tuple(
        TieGroup(value=float(value), size=int(count))
        for value, count in valid_counts
        if int(count) >= 2
    )
    groups = tuple(sorted(groups, key=lambda group: (-group.size, group.value)))
    return TieReport(
        rated=int(rated),
        gated=int(gated),
        distinct=len(valid_counts),
        groups=groups,
        unrated=int(unrated),
    )


def format_tie_report(report: TieReport) -> str:
    """Render the tie report as plain text."""
    histogram = report.size_histogram()
    histogram_text = ", ".join(f"{size}: {count}" for size, count in histogram.items())
    if report.unrated > 0:
        repos_line = f"repos: {report.rated + report.gated + report.unrated} total, {report.rated} rated, {report.gated} gated, {report.unrated} unrated"
    else:
        repos_line = f"repos: {report.rated + report.gated} total, {report.rated} rated, {report.gated} gated"
    lines = [
        "Porchlamp tie audit",
        repos_line,
        f"distinct composite values: {report.distinct}",
        f"tie groups (>= 2 rated repos): {len(report.groups)}",
    ]
    if report.groups:
        largest = report.groups[0]
        lines.append(
            f"largest tie group: {largest.size} rated repos at composite {largest.value:.2f}"
        )
        lines.append(f"group sizes (size -> number of groups): {{{histogram_text}}}")
        lines.append(f"share of rated repos in a tie group: {report.share_in_tie_groups():.4f}")
    else:
        lines.append("no tie groups: every rated composite is distinct")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run ``porchlamp audit ties``; returns the process exit code.

    Exits 0 when the report completes; exits 2 on usage or database
    errors.
    """
    parser = argparse.ArgumentParser(
        description="Distinct composite values and tie-group sizes of a v1 Porchlamp database."
    )
    parser.add_argument("--db", default="data/cvi.db", help="path to the Porchlamp database")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)
    try:
        report = ties_report(args.db)
    except sqlite3.Error as error:
        print(f"cannot compute ties: {error}", file=sys.stderr)
        return 2
    if args.json:
        payload: dict[str, Any] = {
            "db": str(Path(args.db).resolve()),
            "rated": report.rated,
            "gated": report.gated,
            "distinct": report.distinct,
            "largest_size": report.largest_size(),
            "share_in_tie_groups": report.share_in_tie_groups(),
            "size_histogram": report.size_histogram(),
            "groups": [asdict(group) for group in report.groups],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(format_tie_report(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
