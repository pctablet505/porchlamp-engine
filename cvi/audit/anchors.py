"""D-VER-4 anchor relation audit (packet W0-5).

``cvi audit anchors`` evaluates the relational anchors from
``docs/v2/02-RANKING-DESIGN.md`` section 11 (D-VER-4) against a v1
``repositories`` database and reports, for every relation, the expected
statement, the observed fact and a verdict.

Verdicts
--------
PASS
    The DB records the relation as the design requires.
FAIL
    The DB contradicts the relation or lacks the fact entirely.  On a v1
    database failures are expected: the D-VER-4 gate taxonomy
    (off-platform, mirror, archived, non_software) and the per-parameter
    5.1 score do not exist in v1, so the harness measures that gap.
REPORT
    Report-only relations (``keras-team/keras-hub``) never fail; the
    recorded gate status is emitted for the record.

Mapping of D-VER-4 to the v1 schema (the audit's contract; the report
repeats it)
-------------------------------------------------
routed off-platform / gated mirror / archived / non_software
    Read from ``is_stage0_disqualified`` and ``disqualification_reason``;
    a relation passes when the marker phrase occurs in the recorded
    reason (off-platform, mirror, archived, non_software).
rated with coverage >= 0.90 and tier >= A
    ``coverage`` is the share of the five pillar score columns that is
    non-NULL for the repo; the repo must be ungated and its
    ``rank_tier`` must be Tier A, Tier S or Tier S+.
legal = DCO
    ``legal_ip_model`` equals ``DCO``.
newcomer-label count >= 1 scores 5.1 > 0
    The v1 proxy for the GitHub newcomer-label count is the stored
    ``good_first_issues.count`` inside ``deep_dive_json``.  v1 stores no
    per-parameter 5.1 score, so any rated repo with a count >= 1 makes
    the relation FAIL with the observed count; zero rated repos is
    vacuously PASS.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

__version__ = "1.0.0"

PASS = "PASS"
FAIL = "FAIL"
REPORT = "REPORT"

#: ``repositories`` columns holding the five pillar scores, in
#: :data:`cvi.constants.PILLAR_WEIGHTS` order (v1 schema).
PILLAR_COLUMNS: tuple[str, ...] = (
    "score_responsiveness",
    "score_criticality",
    "score_governance",
    "score_rigor_ai",
    "score_accessibility",
)

#: Minimum rating coverage for the "rated" anchor relations (D-VER-4).
MIN_COVERAGE = 0.90

#: Tier order, weakest to strongest; "tier >= A" keeps the last three.
TIER_ORDER: tuple[str, ...] = ("Tier D", "Tier C", "Tier B", "Tier A", "Tier S", "Tier S+")
_MINIMUM_TIER_RANK = TIER_ORDER.index("Tier A")

#: The D-VER-4 relation list: stable relation id and its expected statement.
ANCHOR_RELATIONS: tuple[tuple[str, str], ...] = (
    ("linux_off_platform", "torvalds/linux is routed off-platform"),
    ("gcc_mirror_gated", "gcc-mirror/gcc is gated as mirror"),
    ("archived_repo_gated", "a chosen archived repo is gated as archived"),
    ("awesome_non_software", "an awesome-* list is non_software"),
    ("kubernetes_rated", "kubernetes/kubernetes is rated with coverage >= 0.90 and tier >= A"),
    ("vllm_rated", "vllm-project/vllm is rated with coverage >= 0.90 and tier >= A"),
    ("linux_dco", "the DCO anchor torvalds/linux has legal = DCO"),
    ("newcomer_label_5_1", "any repo with a newcomer-label count >= 1 on GitHub scores 5.1 > 0"),
    ("keras_hub_gate_reported", "keras-team/keras-hub's gate status is reported, not asserted"),
)

_LINUX = ("torvalds", "linux")
_GCC = ("gcc-mirror", "gcc")
_KUBERNETES = ("kubernetes", "kubernetes")
_VLLM = ("vllm-project", "vllm")
_KERAS = ("keras-team", "keras-hub")

_EXPECTED = dict(ANCHOR_RELATIONS)


@dataclass(frozen=True)
class AnchorResult:
    """One D-VER-4 relation as observed on a database.

    Parameters
    ----------
    relation:
        Stable relation id from :data:`ANCHOR_RELATIONS`.
    expected:
        The expected statement from the design.
    observed:
        The fact read from the DB that decides the verdict.
    verdict:
        One of :data:`PASS`, :data:`FAIL`, :data:`REPORT`.
    """

    relation: str
    expected: str
    observed: str
    verdict: str


def _connect(db_path: str | Path) -> sqlite3.Connection:
    """Open the database read-only; the audit never writes."""
    return sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)


def _repo_row(con: sqlite3.Connection, owner: str, name: str) -> Optional[sqlite3.Row]:
    con.row_factory = sqlite3.Row
    return con.execute(
        "SELECT owner, name, overall_cvi, rank_tier, is_stage0_disqualified, "
        "disqualification_reason, legal_ip_model, "
        + ", ".join(PILLAR_COLUMNS)
        + " FROM repositories WHERE owner = ? AND name = ?",
        (owner, name),
    ).fetchone()


def _gating(row: sqlite3.Row) -> tuple[bool, Optional[str]]:
    """Return ``(gated, reason)`` for a row."""
    gated = bool(row["is_stage0_disqualified"])
    return gated, (row["disqualification_reason"] if gated else None)


def _marker(reason: Optional[str], marker: str) -> bool:
    return reason is not None and marker in reason.lower()


def _coverage(row: sqlite3.Row) -> float:
    """Share of the five pillar score columns that is non-NULL."""
    measured = sum(1 for column in PILLAR_COLUMNS if row[column] is not None)
    return measured / len(PILLAR_COLUMNS)


def _tier_rank(row: sqlite3.Row) -> int:
    tier = row["rank_tier"]
    return TIER_ORDER.index(tier) if tier in TIER_ORDER else -1


def _check_gated(
    con: sqlite3.Connection, relation_id: str, owner: str, name: str, marker: str
) -> AnchorResult:
    row = _repo_row(con, owner, name)
    if row is None:
        return AnchorResult(relation_id, _EXPECTED[relation_id], "repo not present in DB", FAIL)
    gated, reason = _gating(row)
    if gated and _marker(reason, marker):
        return AnchorResult(relation_id, _EXPECTED[relation_id], f"gated: {reason}", PASS)
    observed = f"gated: {reason}" if gated else "present, ungated"
    return AnchorResult(relation_id, _EXPECTED[relation_id], observed, FAIL)


def _check_rated(con: sqlite3.Connection, owner: str, name: str) -> AnchorResult:
    relation_id = "kubernetes_rated" if (owner, name) == _KUBERNETES else "vllm_rated"
    row = _repo_row(con, owner, name)
    if row is None:
        return AnchorResult(relation_id, _EXPECTED[relation_id], "repo not present in DB", FAIL)
    gated, reason = _gating(row)
    if gated:
        return AnchorResult(relation_id, _EXPECTED[relation_id], f"gated: {reason}", FAIL)
    coverage = _coverage(row)
    if coverage < MIN_COVERAGE:
        observed = f"coverage {coverage:.2f} (< {MIN_COVERAGE:.2f}), tier {row['rank_tier']}, rated"
    elif _tier_rank(row) < _MINIMUM_TIER_RANK:
        observed = f"coverage {coverage:.2f}, tier {row['rank_tier']} (< A), rated"
    else:
        return AnchorResult(
            relation_id, _EXPECTED[relation_id], f"coverage {coverage:.2f}, tier {row['rank_tier']}, rated", PASS
        )
    return AnchorResult(relation_id, _EXPECTED[relation_id], observed, FAIL)


def _check_archived(con: sqlite3.Connection) -> AnchorResult:
    relation_id = "archived_repo_gated"
    rows = con.execute(
        "SELECT owner, name FROM repositories "
        "WHERE is_stage0_disqualified = 1 "
        "AND lower(disqualification_reason) LIKE '%archived%' "
        "ORDER BY owner, name"
    ).fetchall()
    if rows:
        keys = ", ".join(f"{r[0]}/{r[1]}" for r in rows)
        noun = "repo" if len(rows) == 1 else "repos"
        return AnchorResult(
            relation_id, _EXPECTED[relation_id], f"{len(rows)} gated {noun} with an archived reason: {keys}", PASS
        )
    return AnchorResult(relation_id, _EXPECTED[relation_id], "no gated repo with an archived reason", FAIL)


def _check_awesome(con: sqlite3.Connection) -> AnchorResult:
    relation_id = "awesome_non_software"
    rows = con.execute(
        "SELECT owner, name, is_stage0_disqualified, disqualification_reason "
        "FROM repositories WHERE lower(name) LIKE 'awesome%' ORDER BY owner, name"
    ).fetchall()
    if not rows:
        return AnchorResult(relation_id, _EXPECTED[relation_id], "0 awesome-* repos in DB", FAIL)
    gated_as_list = [
        row
        for row in rows
        if bool(row[2]) and _marker(row[3], "non_software")
    ]
    plural = "repo" if len(rows) == 1 else "repos"
    if len(gated_as_list) == len(rows):
        return AnchorResult(
            relation_id, _EXPECTED[relation_id], f"{len(rows)} awesome-* {plural} present, all gated non_software", PASS
        )
    gated_text = "none gated non_software" if not gated_as_list else f"{len(gated_as_list)} gated non_software"
    return AnchorResult(
        relation_id, _EXPECTED[relation_id], f"{len(rows)} awesome-* {plural} present, {gated_text}", FAIL
    )


def _check_dco(con: sqlite3.Connection) -> AnchorResult:
    relation_id = "linux_dco"
    row = _repo_row(con, *_LINUX)
    if row is None:
        return AnchorResult(relation_id, _EXPECTED[relation_id], "repo not present in DB", FAIL)
    observed = f"legal_ip_model = {row['legal_ip_model']}"
    verdict = PASS if row["legal_ip_model"] == "DCO" else FAIL
    return AnchorResult(relation_id, _EXPECTED[relation_id], observed, verdict)


def _check_newcomer(con: sqlite3.Connection) -> AnchorResult:
    relation_id = "newcomer_label_5_1"
    count = con.execute(
        "SELECT COUNT(*) FROM repositories "
        "WHERE is_stage0_disqualified = 0 "
        "AND json_extract(deep_dive_json, '$.good_first_issues.count') >= 1"
    ).fetchone()[0]
    if count == 0:
        return AnchorResult(relation_id, _EXPECTED[relation_id], "0 rated repos with count >= 1 (vacuous)", PASS)
    plural = "repo" if count == 1 else "repos"
    observed = f"{count} rated {plural} with newcomer-label count >= 1; no per-parameter 5.1 score stored"
    return AnchorResult(relation_id, _EXPECTED[relation_id], observed, FAIL)


def _check_keras(con: sqlite3.Connection) -> AnchorResult:
    relation_id = "keras_hub_gate_reported"
    row = _repo_row(con, *_KERAS)
    if row is None:
        return AnchorResult(relation_id, _EXPECTED[relation_id], "repo not present in DB", REPORT)
    gated, reason = _gating(row)
    observed = f"gated: {reason}" if gated else "present, ungated"
    return AnchorResult(relation_id, _EXPECTED[relation_id], observed, REPORT)


def evaluate_anchors(db_path: str | Path) -> list[AnchorResult]:
    """Evaluate every D-VER-4 relation against ``db_path``.

    Parameters
    ----------
    db_path:
        Path to a v1 ``cvi.db`` (SQLite with a ``repositories`` table).

    Returns
    -------
    list[AnchorResult]
        One result per relation, in :data:`ANCHOR_RELATIONS` order.
    """
    con = _connect(db_path)
    try:
        return [
            _check_gated(con, "linux_off_platform", *_LINUX, "off-platform"),
            _check_gated(con, "gcc_mirror_gated", *_GCC, "mirror"),
            _check_archived(con),
            _check_awesome(con),
            _check_rated(con, *_KUBERNETES),
            _check_rated(con, *_VLLM),
            _check_dco(con),
            _check_newcomer(con),
            _check_keras(con),
        ]
    finally:
        con.close()


def format_anchor_report(
    results: Sequence[AnchorResult], rows_total: int, rows_rated: int
) -> str:
    """Render the anchor report as plain text, one block per relation."""
    lines = [
        "CVI anchor audit (D-VER-4)",
        f"repos: {rows_total} total, {rows_rated} rated",
    ]
    for result in results:
        lines.append(f"{result.verdict:<7} {result.relation:<24} {result.expected}")
        lines.append(f"{'':<7} observed: {result.observed}")
    counts = {verdict: 0 for verdict in (PASS, FAIL, REPORT)}
    for result in results:
        counts[result.verdict] += 1
    lines.append(
        f"summary: {counts[PASS]} {PASS}, {counts[FAIL]} {FAIL}, {counts[REPORT]} {REPORT}"
    )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run ``cvi audit anchors``; returns the process exit code.

    Exits 0 when the evaluation completes (relation failures are data,
    not errors); exits 2 on usage or database errors.
    """
    parser = argparse.ArgumentParser(
        description="Evaluate the D-VER-4 anchor relations on a v1 CVI database."
    )
    parser.add_argument("--db", default="data/cvi.db", help="path to the CVI database")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args(argv)
    try:
        results = evaluate_anchors(args.db)
    except sqlite3.Error as error:
        print(f"cannot audit {args.db}: {error}", file=sys.stderr)
        return 2
    try:
        con = _connect(args.db)
        total = con.execute("SELECT COUNT(*) FROM repositories").fetchone()[0]
        rated = con.execute(
            "SELECT COUNT(*) FROM repositories WHERE is_stage0_disqualified = 0"
        ).fetchone()[0]
        con.close()
    except sqlite3.Error as error:
        print(f"cannot count rows in {args.db}: {error}", file=sys.stderr)
        return 2
    if args.json:
        payload: dict[str, Any] = {
            "db": str(Path(args.db).resolve()),
            "rows_total": total,
            "rows_rated": rated,
            "results": [asdict(result) for result in results],
        }
        print(json.dumps(payload, indent=2))
    else:
        print(format_anchor_report(results, total, rated))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
