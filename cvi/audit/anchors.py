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
import math
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from cvi.core.aggregate import TIER_ORDER as _V2_TIER_ORDER
from cvi.core.pipeline import ScoredSnapshot

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


# =========================================================================== #
# W2-8b: D-VER-4 anchors over v2 ScoredSnapshot records.
#
# Same nine relations (:data:`ANCHOR_RELATIONS`), same D-VER-4 semantics, now
# read from ``cvi.core.pipeline.ScoredSnapshot.records`` instead of the v1
# ``repositories`` table -- the v1 functions above are untouched (this
# section is purely additive). Two differences from the v1 reading, both
# consequences of the v2 contract shape rather than a change in what D-VER-4
# means:
#
# - v1's "repo not present in DB" is a FAIL (a full v1 database should
#   contain every real GitHub repo it was ever asked about); a v2
#   ``ScoredSnapshot`` is commonly a small fixture slice of the universe
#   (this packet's own ``tests/fixtures/snapshot_50``) that may legitimately
#   never have collected ``gcc-mirror/gcc``, ``kubernetes/kubernetes``,
#   ``vllm-project/vllm`` or ``keras-team/keras-hub`` at all. Absence of one
#   of these four *identity* anchors is therefore :data:`SKIPPED` with a
#   named reason -- not a silent pass (there is nothing to vacuously pass:
#   the relation was never evaluated) and not a FAIL (a FAIL should mean the
#   ranking got the relation wrong, not that the fixture is small).
#   ``archived_repo_gated`` and ``awesome_non_software`` are *not* tied to
#   one repo identity (the v1 relation is "at least one repo of this kind
#   exists and is gated correctly"), so their absence is still a real FAIL,
#   matching v1's own ``_check_archived``/``_check_awesome`` behaviour.
# - ``newcomer_label_5_1`` compares the v1 5.1 score against an
#   *independent* raw newcomer-label count carried in a separate
#   ``deep_dive_json`` field the v1 schema stores alongside (not derived
#   from) the score. ``cvi.core.models.ParamScore`` (04 section 3) carries
#   only ``score``/``measured``/``reason``/``n``/``method`` -- no raw
#   measured *value* survives past stage 2 into the record at all, for any
#   parameter, so no independent cross-check is possible from a
#   ``ScoredSnapshot`` alone (an already-known contract gap: 04 section 7's
#   own `/evidence` endpoint promises a "value" the stored shape has nowhere
#   to keep -- flagged again in the handoff). :func:`_check_newcomer_v2`
#   therefore also reports :data:`SKIPPED`, naming this exact reason, rather
#   than fabricating a same-source "check" that could never fail
#   independently of the curve it is supposedly verifying.
# =========================================================================== #

#: A relation this snapshot has no way to evaluate: the identity anchor it
#: names is absent from the snapshot, or (``newcomer_label_5_1``) the v2
#: ``ScoreRecord`` contract does not carry the fact the relation needs.
#: Distinct from :data:`FAIL` (the relation was evaluated and did not hold)
#: and from :data:`REPORT` (evaluated, never fails by design) -- a
#: :data:`SKIPPED` relation was not evaluated at all.
SKIPPED = "SKIPPED"


def _records_by_identity(snapshot: ScoredSnapshot) -> dict[tuple[str, str], Any]:
    """``{(owner, name): ScoreRecord}`` over ``snapshot.records`` -- every
    record carries its own ``repo`` (W2-10), so no separate bundle lookup is
    needed the way the v1 SQL reader needed a join."""
    return {(record.repo.owner, record.repo.name): record for record in snapshot.records}


def _check_routed_v2(records: Mapping[tuple[str, str], Any], relation_id: str, owner: str, name: str, routed: str) -> AnchorResult:
    """D-GATE-6 routing (``linux_off_platform``): ``gate.routed`` is a
    separate field from ``gate.reason`` -- a routed repo stays
    ``gate.eligible = True`` (02 section 2: "Off-platform -> routed, scored
    with responsiveness unmeasured"), so this is never a gated-reason check."""
    record = records.get((owner, name))
    if record is None:
        return AnchorResult(relation_id, _EXPECTED[relation_id], f"{owner}/{name} not present in the snapshot", SKIPPED)
    if record.gate.eligible and record.gate.routed == routed:
        return AnchorResult(relation_id, _EXPECTED[relation_id], f"routed: {record.gate.routed}", PASS)
    observed = f"gated: {record.gate.reason}" if not record.gate.eligible else f"routed: {record.gate.routed}"
    return AnchorResult(relation_id, _EXPECTED[relation_id], observed, FAIL)


def _check_gated_reason_v2(records: Mapping[tuple[str, str], Any], relation_id: str, owner: str, name: str, reason: str) -> AnchorResult:
    """A stage-0 D-GATE-2/3 failure with the exact frozen ``gate.reason``
    string (``gcc_mirror_gated``): an exact-match check, not the v1
    substring-in-free-text search -- ``cvi.core.gates``' reason vocabulary
    is a small frozen enum of exact strings (02 section 2), not free text."""
    record = records.get((owner, name))
    if record is None:
        return AnchorResult(relation_id, _EXPECTED[relation_id], f"{owner}/{name} not present in the snapshot", SKIPPED)
    if not record.gate.eligible and record.gate.reason == reason:
        return AnchorResult(relation_id, _EXPECTED[relation_id], f"gated: {record.gate.reason}", PASS)
    observed = f"gated: {record.gate.reason}" if not record.gate.eligible else "present, eligible"
    return AnchorResult(relation_id, _EXPECTED[relation_id], observed, FAIL)


def _check_archived_v2(snapshot: ScoredSnapshot) -> AnchorResult:
    relation_id = "archived_repo_gated"
    matches = sorted(
        f"{r.repo.owner}/{r.repo.name}" for r in snapshot.records if not r.gate.eligible and r.gate.reason == "archived"
    )
    if matches:
        noun = "repo" if len(matches) == 1 else "repos"
        return AnchorResult(
            relation_id, _EXPECTED[relation_id], f"{len(matches)} gated {noun} with reason 'archived': {', '.join(matches)}", PASS,
        )
    return AnchorResult(relation_id, _EXPECTED[relation_id], "no gated repo with reason 'archived'", FAIL)


def _check_awesome_v2(snapshot: ScoredSnapshot) -> AnchorResult:
    relation_id = "awesome_non_software"
    rows = sorted(
        (f"{r.repo.owner}/{r.repo.name}", not r.gate.eligible and r.gate.reason == "non_software")
        for r in snapshot.records
        if r.repo.name.lower().startswith("awesome")
    )
    if not rows:
        return AnchorResult(relation_id, _EXPECTED[relation_id], "0 awesome-* repos in the snapshot", FAIL)
    gated_as_list = [key for key, ok in rows if ok]
    plural = "repo" if len(rows) == 1 else "repos"
    if len(gated_as_list) == len(rows):
        return AnchorResult(
            relation_id, _EXPECTED[relation_id],
            f"{len(rows)} awesome-* {plural} present, all gated non_software: {', '.join(key for key, _ in rows)}", PASS,
        )
    gated_text = "none gated non_software" if not gated_as_list else f"{len(gated_as_list)} gated non_software"
    return AnchorResult(relation_id, _EXPECTED[relation_id], f"{len(rows)} awesome-* {plural} present, {gated_text}", FAIL)


def _tier_rank_v2(tier: Optional[str]) -> int:
    """v2 tier order (bare letters -- ``cvi.core.aggregate.TIER_ORDER``, not
    v1's ``"Tier A"``-prefixed :data:`TIER_ORDER` above; reused, not
    redefined, R-2)."""
    return _V2_TIER_ORDER.index(tier) if tier in _V2_TIER_ORDER else -1


def _check_rated_v2(records: Mapping[tuple[str, str], Any], owner: str, name: str) -> AnchorResult:
    relation_id = "kubernetes_rated" if (owner, name) == _KUBERNETES else "vllm_rated"
    record = records.get((owner, name))
    if record is None:
        return AnchorResult(relation_id, _EXPECTED[relation_id], f"{owner}/{name} not present in the snapshot", SKIPPED)
    if not record.gate.eligible:
        return AnchorResult(relation_id, _EXPECTED[relation_id], f"gated: {record.gate.reason}", FAIL)
    if not record.rated:
        return AnchorResult(relation_id, _EXPECTED[relation_id], f"coverage {record.coverage:.2f} (< 0.60), unrated", FAIL)
    minimum_tier_rank = _V2_TIER_ORDER.index("A")
    if record.coverage < MIN_COVERAGE:
        observed = f"coverage {record.coverage:.2f} (< {MIN_COVERAGE:.2f}), tier {record.tier}, rated"
    elif _tier_rank_v2(record.tier) < minimum_tier_rank:
        observed = f"coverage {record.coverage:.2f}, tier {record.tier} (< A), rated"
    else:
        return AnchorResult(
            relation_id, _EXPECTED[relation_id], f"coverage {record.coverage:.2f}, tier {record.tier}, rated", PASS,
        )
    return AnchorResult(relation_id, _EXPECTED[relation_id], observed, FAIL)


def _skipped_unmeasured_input(relation_id: str, reason: str) -> AnchorResult:
    """Shared rule for every anchor that reads a stage-2 ``ParamScore``
    (dispatch fix, following the W1-6 fixture-regeneration finding): a
    required input that is ``Unmeasured`` on the anchor repo means the
    relation cannot be evaluated at all -- ``SKIPPED``, naming the
    ``Unmeasured`` reason, never ``FAIL``. ``FAIL`` is reserved for a
    *measured* value that contradicts the relation; an absent measurement
    contradicts nothing, so reporting it as a failed relation previously
    misrepresented a collector gap as a wrong ranking (measured: this
    happened for real on ``linux_dco`` once ``legal_ip`` started reading
    the governance envelope, which is absent on the fixture -- W1-6
    landing changed the input from a v1 meta field, always present on this
    fixture, to a real envelope read, not always present)."""
    return AnchorResult(relation_id, _EXPECTED[relation_id], f"input unmeasured: {reason}", SKIPPED)


def _check_dco_v2(records: Mapping[tuple[str, str], Any]) -> AnchorResult:
    """``legal_ip == "DCO"``: ``ScoreRecord.params`` carries only the
    *scored* value (04 section 3's ``ParamScore`` has no raw-value field --
    see the module docstring), so this reads the score instead. Safe by
    construction: ``cvi.core.params.governance.LEGAL_IP_SCORES`` maps
    ``DCO -> 100.0``, ``NONE -> 85.0``, ``FOUNDATION_CLA -> 75.0``,
    ``CORP_CLA -> 25.0`` -- four *distinct* scores, so ``score == 100.0``
    on a ``measured`` parameter is recoverable evidence of ``DCO`` and
    nothing else, not a guess.

    Gated (no params scored at all) and ``Unmeasured`` both mean "nothing
    measured to contradict the relation with" -- both are ``SKIPPED``, not
    ``FAIL`` (see :func:`_skipped_unmeasured_input`); only a *measured*
    ``legal_ip`` whose score is not 100.0 is an actual FAIL."""
    relation_id = "linux_dco"
    record = records.get(_LINUX)
    if record is None:
        return AnchorResult(relation_id, _EXPECTED[relation_id], "torvalds/linux not present in the snapshot", SKIPPED)
    param = record.params.get("legal_ip") if record.gate.eligible else None
    if param is None:
        if not record.gate.eligible:
            return _skipped_unmeasured_input(relation_id, f"gated: {record.gate.reason}")
        return AnchorResult(relation_id, _EXPECTED[relation_id], "legal_ip not in params", FAIL)
    if not param.measured:
        return _skipped_unmeasured_input(relation_id, param.reason or "unknown")
    verdict = PASS if param.score is not None and math.isclose(param.score, 100.0, abs_tol=1e-9) else FAIL
    observed = f"legal_ip score = {param.score} (DCO <=> 100.0)"
    return AnchorResult(relation_id, _EXPECTED[relation_id], observed, verdict)


def _check_newcomer_v2(relation_id: str = "newcomer_label_5_1") -> AnchorResult:
    """See the module docstring: ``ScoreRecord`` carries no independent raw
    newcomer-label count distinct from the already-curved 5.1 score, so this
    relation cannot be cross-checked from a ``ScoredSnapshot`` alone."""
    return AnchorResult(
        relation_id, _EXPECTED[relation_id],
        "ScoreRecord.params carries only the curved score, no independent raw "
        "newcomer-label count to cross-check it against (04 section 3 gap)",
        SKIPPED,
    )


def _check_keras_v2(records: Mapping[tuple[str, str], Any]) -> AnchorResult:
    relation_id = "keras_hub_gate_reported"
    record = records.get(_KERAS)
    if record is None:
        return AnchorResult(relation_id, _EXPECTED[relation_id], "repo not present in the snapshot", REPORT)
    observed = f"gated: {record.gate.reason}" if not record.gate.eligible else "present, eligible"
    return AnchorResult(relation_id, _EXPECTED[relation_id], observed, REPORT)


def evaluate_anchors_v2(snapshot: ScoredSnapshot) -> list[AnchorResult]:
    """Evaluate every D-VER-4 relation (:data:`ANCHOR_RELATIONS`) against a
    v2 :class:`cvi.core.pipeline.ScoredSnapshot`, in the same order
    :func:`evaluate_anchors` uses for the v1 database. Never raises for a
    relation this particular snapshot cannot evaluate -- see :data:`SKIPPED`
    in the module-level docstring section above."""
    records = _records_by_identity(snapshot)
    return [
        _check_routed_v2(records, "linux_off_platform", *_LINUX, "off_platform"),
        _check_gated_reason_v2(records, "gcc_mirror_gated", *_GCC, "mirror"),
        _check_archived_v2(snapshot),
        _check_awesome_v2(snapshot),
        _check_rated_v2(records, *_KUBERNETES),
        _check_rated_v2(records, *_VLLM),
        _check_dco_v2(records),
        _check_newcomer_v2(),
        _check_keras_v2(records),
    ]


def format_anchor_report_v2(results: Sequence[AnchorResult], repo_count: int) -> str:
    """Render the v2 anchor report as plain text (mirrors
    :func:`format_anchor_report`'s shape for the v1 report)."""
    lines = [
        "CVI anchor audit (D-VER-4, v2 ScoredSnapshot)",
        f"repos in snapshot: {repo_count}",
    ]
    for result in results:
        lines.append(f"{result.verdict:<7} {result.relation:<24} {result.expected}")
        lines.append(f"{'':<7} observed: {result.observed}")
    counts = {verdict: 0 for verdict in (PASS, FAIL, REPORT, SKIPPED)}
    for result in results:
        counts[result.verdict] += 1
    lines.append(
        f"summary: {counts[PASS]} {PASS}, {counts[FAIL]} {FAIL}, {counts[REPORT]} {REPORT}, {counts[SKIPPED]} {SKIPPED}"
    )
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
