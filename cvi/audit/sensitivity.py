"""Pillar-weight sensitivity audit (packet W0-5).

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
import json
import math
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

from cvi.constants import PILLAR_WEIGHTS

__version__ = "1.0.0"

#: Release requirement from D-AGG-3: tau-b >= 0.90 for every
#: perturbation among rated repos.
TAU_RELEASE_THRESHOLD = 0.90

#: Default perturbation magnitude in percentage points (D-AGG-3).
DEFAULT_PERTURBATION_PP = 5

#: ``repositories`` columns holding the five pillar scores, in
#: :data:`cvi.constants.PILLAR_WEIGHTS` order (v1 schema).
PILLAR_COLUMNS: tuple[str, ...] = (
    "score_responsiveness",
    "score_criticality",
    "score_governance",
    "score_rigor_ai",
    "score_accessibility",
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
    vector is not (W0-gate P-10c).  That case is not "perfect
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


def _fetch_rated(db_path: str | Path) -> tuple[list[list[float]], list[float]]:
    """Return ``(pillar matrix, canonical composites)`` of the rated repos,
    ordered by ``owner, name``."""
    con = sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT "
            + ", ".join(PILLAR_COLUMNS)
            + ", overall_cvi FROM repositories "
            "WHERE is_stage0_disqualified = 0 ORDER BY owner, name"
        ).fetchall()
    finally:
        con.close()
    pillars = [[float(column) for column in row[: len(PILLAR_COLUMNS)]] for row in rows]
    canonical = [float(row[len(PILLAR_COLUMNS)]) for row in rows]
    return pillars, canonical


def sensitivity_report(
    db_path: str | Path, pp: int = DEFAULT_PERTURBATION_PP
) -> SensitivityReport:
    """Compute the D-AGG-3 sensitivity report for ``db_path``.

    Parameters
    ----------
    db_path:
        Path to a v1 ``cvi.db`` (SQLite with a ``repositories`` table).
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
                round(
                    sum(weights[pillar_key] * value[i] for i, pillar_key in enumerate(PILLAR_WEIGHTS)),
                    2,
                )
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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
