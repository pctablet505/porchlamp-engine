"""Pillar-weight sensitivity audit.

``cvi audit sensitivity`` implements the D-AGG-3 sensitivity requirement
from ``docs/v2/02-RANKING-DESIGN.md`` section 7: for a v1 ``cvi.db`` it
compares the canonical ranking (stored ``overall_cvi`` of the rated
repos) against the ranking under a +/-5 pp perturbation of each pillar
weight, redistributed proportionally across the remaining pillars.  The
agreement of each perturbed ranking with the canonical one is reported
as Kendall's tau-b over the composite values; release requires
``TAU_RELEASE_THRESHOLD`` (0.90) for every perturbation.

Definitions (the audit's contract; the report repeats them)
----------------------------------------------------------
rated repo
    A row with ``is_stage0_disqualified = 0``; gated repos do not enter
    the ranking and are excluded from the statistic.
canonical composite
    The stored ``overall_cvi`` (D-AGG-2: 2-decimal weighted sum of the
    pillar scores).
perturbed composite
    ``round(sum(w'_p * pillar_p), 2)`` with the perturbed weight vector
    (D-AGG-3: the perturbed pillar moves by the requested percentage
    points; every other pillar weight is rescaled by
    ``(1 - w')/(1 - w)`` so the vector still sums to 1).
tau-b
    Kendall's tau-b between the canonical and perturbed composite
    vectors: pairs tied in only one vector are discounted in the
    denominator, pairs tied in both are excluded entirely.  Composite
    ties are exactly the ties the ranking exhibits, so tau-b -- not the
    total-order variant -- is the design quantity.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from cvi.core import aggregate, constants
from cvi.core.constants import PILLAR_WEIGHTS
from cvi.core.models import ScoreRecord
from cvi.core.pipeline import ScoredSnapshot
from cvi.core.tiebreak import TieBreakInput, assign_ranks

__version__ = "1.0.0"

#: Release requirement from D-AGG-3: tau-b >= 0.90 for every
#: perturbation among rated repos.
TAU_RELEASE_THRESHOLD = 0.90

#: Default perturbation magnitude in percentage points (D-AGG-3).
DEFAULT_PERTURBATION_PP = 5

#: ``repositories`` columns holding the five pillar scores, in
#: :data:`cvi.core.constants.PILLAR_WEIGHTS` order (v1 schema).
PILLAR_COLUMNS: tuple[str, ...] = (
    "score_responsiveness",
    "score_criticality",
    "score_governance",
    "score_rigor_ai",
    "score_accessibility",
)

#: ``repositories`` columns holding the five pillar scores, in
#: :data:`cvi.core.constants.PILLAR_WEIGHTS` order (v2 schema).
V2_PILLAR_COLUMNS: tuple[str, ...] = (
    "p_responsiveness",
    "p_criticality",
    "p_governance",
    "p_rigor",
    "p_accessibility",
)



def kendall_tau_b(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    """Kendall's tau-b between two equal-length score vectors.

    Counts concordant pairs (``n1``), discordant pairs (``n2``), pairs
    tied in ``x`` only (``n_x``) and pairs tied in ``y`` only
    (``n_y``); pairs tied in both are excluded from the whole statistic.

    ``tau-b = (n1 - n2) / sqrt((n1 + n2 + n_x) * (n1 + n2 + n_y))``

    Returns 1.0 when every pair is tied in both vectors (identical
    rankings).  O(n**2) in the number of items; deterministic.

    Returns ``None`` (UNDECIDED) when the statistic is the 0/0 of a
    degenerate input: every value of one vector is tied while the other
    vector is not (degenerate tie case).  That case is not "perfect
    agreement" -- a perturbation demonstrably moved the ranking -- so it
    must not read as 1.0.

    Raises
    ------
    ValueError
        When the lengths differ or fewer than two items are given.
    """
    if len(x) != len(y):
        raise ValueError("score vectors must have equal length")
    n = len(x)
    if n < 2:
        raise ValueError("need at least 2 items")
    n1 = n2 = 0
    n_x = n_y = 0
    for i in range(n):
        xi, yi = x[i], y[i]
        for j in range(i + 1, n):
            xj, yj = x[j], y[j]
            sx = 1 if xi > xj else -1 if xi < xj else 0
            sy = 1 if yi > yj else -1 if yi < yj else 0
            if sx == 0 and sy == 0:
                continue
            if sx == 0:
                n_x += 1
            elif sy == 0:
                n_y += 1
            elif (sx > 0) == (sy > 0):
                n1 += 1
            else:
                n2 += 1
    denominator = math.sqrt((n1 + n2 + n_x) * (n1 + n2 + n_y))
    if denominator == 0.0:
        if n1 + n2 + n_x + n_y == 0:
            return 1.0
        return None
    return (n1 - n2) / denominator


def perturb_pillar_weight(
    base: Mapping[str, float], pillar: str, delta_pp: int
) -> dict[str, float]:
    """Return the weight vector with one pillar moved by ``delta_pp`` pp.

    The perturbed pillar takes ``base[pillar] + delta_pp / 100``; every
    other pillar is rescaled by ``(1 - w') / (1 - w)`` so the vector
    still sums to 1 (D-AGG-3, redistributed proportionally).  A
    perturbation of 0 pp returns a vector equal to ``base``.

    Raises
    ------
    ValueError
        When ``pillar`` is not a key of ``base`` or the resulting weight
        would leave the open interval (0, 1).
    """
    if pillar not in base:
        raise ValueError(f"unknown pillar {pillar!r}")
    base_weight = base[pillar]
    new_weight = base_weight + delta_pp / 100.0
    if not 0.0 < new_weight < 1.0:
        raise ValueError(f"perturbed weight {new_weight:.4f} outside (0, 1)")
    scale = (1.0 - new_weight) / (1.0 - base_weight)
    return {
        key: (new_weight if key == pillar else weight * scale) for key, weight in base.items()
    }


@dataclass(frozen=True)
class PerturbationResult:
    """The tau-b of one pillar perturbation against the canonical rank.

    Parameters
    ----------
    pillar:
        The perturbed pillar key (``cvi.constants.PILLAR_WEIGHTS`` order).
    delta_pp:
        Signed percentage-point move of the pillar weight.
    tau_b:
        Kendall's tau-b between the canonical and perturbed composites
        of the rated repos. ``None`` is the sole UNDECIDED case (the 0/0
        degenerate input of :func:`kendall_tau_b`); every reader branches
        on ``tau_b is None`` directly rather than on a separate reason
        code, since that is the only condition that ever produces one.
    """

    pillar: str
    delta_pp: int
    tau_b: Optional[float]


@dataclass(frozen=True)
class SensitivityReport:
    """All pillar perturbations for one database.

    Parameters
    ----------
    rated:
        Number of rated (ungated) repos the statistic is computed on.
    pp:
        Perturbation magnitude in percentage points (0 = identity).
    threshold:
        The D-AGG-3 release threshold.
    rows:
        One result per (pillar, sign), pillar in
        :data:`PILLAR_WEIGHTS` order, positive sign first.
    """

    rated: int
    pp: int
    threshold: float
    rows: tuple[PerturbationResult, ...]

    @property
    def all_pass(self) -> bool:
        """True when every perturbation meets the release threshold; an
        UNDECIDED row (``tau_b is None``) is a failed gate, not a pass."""
        return all(
            row.tau_b is not None and row.tau_b >= self.threshold for row in self.rows
        )


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
    if "composite" in cols and "cvi" in cols:
        return "COALESCE(composite, cvi)"
    if "composite" in cols:
        return "composite"
    return "cvi"


def _fetch_rated(db_path: str | Path) -> tuple[list[list[Optional[float]]], list[float]]:
    """Return ``(pillar matrix, canonical composites)`` of the rated repos,
    ordered by ``owner, name``."""
    con = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    try:
        cols = _repo_columns(con)
        if "p_responsiveness" in cols and "rated" in cols:
            comp_expr = _v2_composite_expr(cols)
            snap_id = _served_snapshot_id(con, cols)
            if snap_id is not None and "snapshot_id" in cols:
                rows = con.execute(
                    "SELECT "
                    + ", ".join(V2_PILLAR_COLUMNS)
                    + f", {comp_expr} FROM repositories "
                    "WHERE rated = 1 AND snapshot_id = ? ORDER BY owner, name",
                    (snap_id,),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT "
                    + ", ".join(V2_PILLAR_COLUMNS)
                    + f", {comp_expr} FROM repositories "
                    "WHERE rated = 1 ORDER BY owner, name"
                ).fetchall()
            n_pillars = len(V2_PILLAR_COLUMNS)
        else:
            rows = con.execute(
                "SELECT "
                + ", ".join(PILLAR_COLUMNS)
                + ", overall_cvi FROM repositories "
                "WHERE is_stage0_disqualified = 0 ORDER BY owner, name"
            ).fetchall()
            n_pillars = len(PILLAR_COLUMNS)
    finally:
        con.close()
    pillars = [
        [float(column) if column is not None else None for column in row[:n_pillars]]
        for row in rows
    ]
    canonical = [float(row[n_pillars]) for row in rows]
    return pillars, canonical


def _compute_perturbed_composite(
    value: Sequence[Optional[float]], weights: Mapping[str, float]
) -> float:
    if all(v is not None for v in value):
        return round(
            sum(weights[pillar_key] * float(value[i]) for i, pillar_key in enumerate(PILLAR_WEIGHTS)),
            2,
        )
    measured_weight = sum(
        weights[pillar_key]
        for i, pillar_key in enumerate(PILLAR_WEIGHTS)
        if value[i] is not None
    )
    if measured_weight == 0.0:
        return 0.0
    raw = (
        sum(
            weights[pillar_key] * float(value[i])
            for i, pillar_key in enumerate(PILLAR_WEIGHTS)
            if value[i] is not None
        )
        / measured_weight
    )
    return round(raw, 2)


def sensitivity_report(
    db_path: str | Path, pp: int = DEFAULT_PERTURBATION_PP
) -> SensitivityReport:
    """Compute the D-AGG-3 sensitivity report for ``db_path``.

    Parameters
    ----------
    db_path:
        Path to a v1 or v2 ``cvi.db`` (SQLite with a ``repositories`` table).
    pp:
        Perturbation magnitude in percentage points; 0 recomputes the
        canonical composite unchanged (guard run).

    Returns
    -------
    SensitivityReport
        One :class:`PerturbationResult` per pillar and sign.

    Raises
    ------
    ValueError
        When the rated set has fewer than two repos or ``pp`` is
        negative / would push a weight out of (0, 1).
    """
    if pp < 0:
        raise ValueError("pp must be >= 0")
    pillars, canonical = _fetch_rated(db_path)
    rated = len(pillars)
    if rated < 2:
        raise ValueError("need at least 2 rated repos for a ranking comparison")
    rows: list[PerturbationResult] = []
    for pillar in PILLAR_WEIGHTS:
        for delta in (pp, -pp):
            weights = perturb_pillar_weight(PILLAR_WEIGHTS, pillar, delta)
            perturbed = [
                _compute_perturbed_composite(value, weights)
                for value in pillars
            ]
            tau = kendall_tau_b(canonical, perturbed)
            rows.append(PerturbationResult(pillar, delta, tau))
    return SensitivityReport(rated=rated, pp=pp, threshold=TAU_RELEASE_THRESHOLD, rows=tuple(rows))


def format_sensitivity_report(report: SensitivityReport) -> str:
    """Render the sensitivity report as plain text."""
    def cell(tau: Optional[float]) -> str:
        return "UNDECIDED" if tau is None else f"{tau:.4f}"

    lines = [
        "CVI sensitivity audit (D-AGG-3)",
        f"rated repos: {report.rated} | perturbation: +/- {report.pp} pp | "
        f"threshold tau-b >= {report.threshold:.2f}",
        "pillar           + tau-b      - tau-b",
    ]
    for index in range(0, len(report.rows), 2):
        up, down = report.rows[index], report.rows[index + 1]
        lines.append(f"{up.pillar:<16} {cell(up.tau_b):>9}   {cell(down.tau_b):>9}")
    gate = "PASS" if report.all_pass else "FAIL"
    lines.append(
        f"release gate: {gate} "
        f"({'all' if report.all_pass else 'not all'} "
        f"{len(report.rows)} perturbations meet tau >= {report.threshold:.2f})"
    )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run ``cvi audit sensitivity``; returns the process exit code.

    Exits 0 when the report completes (a failed release gate is data,
    not an error); exits 2 on usage or database errors.
    """
    parser = argparse.ArgumentParser(
        description="Kendall tau-b of the ranking under +/- pp pillar-weight perturbations (D-AGG-3)."
    )
    parser.add_argument("--db", default="data/cvi.db", help="path to the CVI database")
    parser.add_argument("--pp", type=int, default=DEFAULT_PERTURBATION_PP,
                        help="perturbation in percentage points (default 5, 0 = guard run)")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)
    try:
        report = sensitivity_report(args.db, pp=args.pp)
    except (sqlite3.Error, ValueError) as error:
        print(f"cannot compute sensitivity: {error}", file=sys.stderr)
        return 2
    if args.json:
        payload: dict[str, Any] = {
            "db": str(Path(args.db).resolve()),
            "rated": report.rated,
            "pp": report.pp,
            "threshold": report.threshold,
            "all_pass": report.all_pass,
            "rows": [asdict(row) for row in report.rows],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(format_sensitivity_report(report))
    return 0



# =========================================================================== #
# D-AGG-3 sensitivity over a v2 ScoredSnapshot.
#
# ``sensitivity_report`` above compares raw composite *values* via tau-b --
# adequate for v1, where the store recomputes a perturbed composite directly
# from stored pillar columns and nothing else about the ranking (tie-breaks,
# rank) exists. v2 has a full D-TIE-1 tie-break/rank pipeline
# (``cvi.core.tiebreak``), so :func:`sensitivity_v2` recomputes composites
# through ``aggregate`` from the records' pillar scores + coverages, re-ranks
# through the same tiebreak path, and compares Kendall tau-b between the
# *canonical rank order* (``ScoreRecord.rank``, already a strict, unique 1..N
# total order -- see ``cvi.core.tiebreak.assign_ranks``) and the rank order
# recomputed under each perturbed pillar-weight vector, not the raw composite
# values.
#
# A pillar-weight perturbation changes exactly two of the seven D-TIE-1 tie
# levels: the composite (D-AGG-2, level 1) and the coverage (D-MISS-3's
# ``C = Sum(W_p * c_p)``, level 2) -- both are direct functions of the
# active pillar weights. The other five levels (responsiveness pillar
# score, friction, leverage, TTFHR p75, last non-bot commit date) do not:
# ``cvi.core.matrix``'s leverage/friction coefficients (.50/.30/.20 etc.)
# are D-MAT-1's own fixed constants, entirely independent of D-AGG-1's
# pillar-to-composite weights, and a pillar's *own* within-pillar
# renormalised score is stage 5's fact, computed before stage 6 ever sees a
# weight vector at all. So :func:`_perturbed_tie_break_input` only ever
# replaces ``composite``/``coverage`` on a copy of the record -- via
# ``cvi.core.aggregate.composite_score_raw``/``composite_coverage``'s
# ``weights`` keyword, not a private reduction kept here.
# TTFHR p75 and the last-commit date are recovered from the *canonical*
# ``ScoreRecord.tie_key`` (:func:`_decode_measured_asc`/
# :func:`_decode_measured_desc`, the exact inverse of ``cvi.core.tiebreak.
# _measured_asc``/``_measured_desc``) rather than needing the original
# ``FeatureBundle`` again -- ``sensitivity_v2``'s own signature (``snapshot``
# only, no bundles) does not carry one.
# =========================================================================== #


def _decode_measured_asc(pair: tuple) -> Optional[float]:
    """Inverse of ``cvi.core.tiebreak._measured_asc``: ``(1, 0.0)`` was
    "unmeasured" (``None``); ``(0, value)`` keeps its value unnegated."""
    flag, value = pair
    return None if flag == 1 else float(value)


def _decode_measured_desc(pair: tuple) -> Optional[float]:
    """Inverse of ``cvi.core.tiebreak._measured_desc``: ``(1, 0.0)`` was
    "unmeasured" (``None``); ``(0, -value)`` negates back to the original."""
    flag, value = pair
    return None if flag == 1 else -float(value)


def _perturbed_tie_break_input(record: ScoreRecord, weights: Mapping[str, float]) -> TieBreakInput:
    """One rated record's :class:`~cvi.core.tiebreak.TieBreakInput` under a
    perturbed pillar-weight vector: ``composite``/``coverage`` replaced
    (never mutated -- ``dataclasses.replace``), every other D-TIE-1 level
    carried over unchanged from the canonical ``tie_key`` (module docstring
    section above). ``record.tie_key`` must be the canonical, non-empty key
    a rated record always carries (``assign_ranks`` populates it for every
    rated row) -- this function is only ever called on the ``snapshot.
    records`` entries already filtered to ``rank is not None`` by
    :func:`sensitivity_v2`."""
    composite_raw = aggregate.composite_score_raw(record.pillars, weights=weights)
    composite = None if composite_raw is None else round(composite_raw, 2)
    coverage = aggregate.composite_coverage(record.pillars, weights=weights)
    perturbed_record = dataclasses.replace(record, composite=composite, coverage=coverage)

    ttfhr_p75_hours = _decode_measured_asc(record.tie_key[5])
    commit_epoch = _decode_measured_desc(record.tie_key[6])
    last_non_bot_commit_at = (
        None if commit_epoch is None else datetime.fromtimestamp(commit_epoch, tz=timezone.utc).isoformat()
    )
    return TieBreakInput(
        record=perturbed_record, owner=record.repo.owner, name=record.repo.name,
        ttfhr_p75_hours=ttfhr_p75_hours, last_non_bot_commit_at=last_non_bot_commit_at,
    )


def sensitivity_v2(snapshot: ScoredSnapshot, perturbation_pp: float = DEFAULT_PERTURBATION_PP) -> SensitivityReport:
    """D-AGG-3 sensitivity report over a v2 :class:`~cvi.core.pipeline.
    ScoredSnapshot`: Kendall tau-b between the canonical rank order and the
    rank order under each +/- ``perturbation_pp`` single-pillar perturbation
    of :data:`cvi.core.constants.PILLAR_WEIGHTS`, re-ranked through
    :func:`cvi.core.tiebreak.assign_ranks` (module docstring section above)
    -- 5 pillars x 2 directions = 10 rows, in the same
    (:data:`cvi.core.constants.PILLAR_NAMES` order, positive sign first)
    layout :func:`sensitivity_report` uses for v1.

    Only rated records (``record.rank is not None``) enter the comparison
    (D-AGG-3: "among rated repos"); the *set* of rated records is fixed to
    the canonical one at every perturbation -- this mirrors
    :func:`sensitivity_report`'s own v1 behaviour (fetching only the rows
    already rated under the stored, unperturbed weights) rather than
    letting a perturbation additionally re-gate a repo in or out of the
    rated set through a shifted D-MISS-3 coverage threshold, which would
    conflate "how much does the *order* move" with "how many repos would
    even be rated" -- two different questions.

    Raises ``ValueError`` when fewer than 2 records are rated (the same
    guard :func:`sensitivity_report` applies -- reused, not
    reimplemented): a ranking comparison needs at least two items."""
    rated = [record for record in snapshot.records if record.rank is not None]
    if len(rated) < 2:
        raise ValueError(f"need at least 2 rated repos for a ranking comparison, got {len(rated)}")

    canonical_rank = {(record.repo.owner, record.repo.name): float(record.rank) for record in rated}
    keys = sorted(canonical_rank)
    canonical_vector = [canonical_rank[key] for key in keys]

    rows: list[PerturbationResult] = []
    for pillar in constants.PILLAR_NAMES:
        for delta in (perturbation_pp, -perturbation_pp):
            weights = perturb_pillar_weight(constants.PILLAR_WEIGHTS, pillar, delta)
            entries = [_perturbed_tie_break_input(record, weights) for record in rated]
            ranked = assign_ranks(entries)
            perturbed_rank = {
                (rec.repo.owner, rec.repo.name): float(rank)
                for rec, rank, _tie_group in ranked
                if rank is not None
            }
            perturbed_vector = [perturbed_rank[key] for key in keys]
            tau = kendall_tau_b(canonical_vector, perturbed_vector)
            rows.append(PerturbationResult(pillar, delta, tau))
    return SensitivityReport(rated=len(rated), pp=perturbation_pp, threshold=TAU_RELEASE_THRESHOLD, rows=tuple(rows))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
