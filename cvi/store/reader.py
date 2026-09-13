"""CVI v2 store reader basics (`04` sections 5, 6; packet W4-2 deliverable 4).

Only the single-row/evidence/snapshot getters and the ordering helper the
SQL persona-projection query (W4-3) builds on; the persona re-projection
query itself (weighted pillar re-combination, per-persona gate filter,
``tie_group`` via ``DENSE_RANK`` -- F-014, REWORK round 1: handed to W4-3
in full, including the null-out-a-singleton-group refinement `04` section 6
leaves open) is W4-3's.

``_ORDER_BY_SQL`` is `04` section 6's amended ``ORDER BY`` (ruling
2026-09-13: ``NULLS LAST`` on every tie-break column, because SQLite sorts
NULL first under plain ``ASC`` -- the exact inversion the W2-6 review found)
applied directly to the *stored, un-reprojected* ``cvi``/pillar columns
(the balanced view); W4-3's ``cvi.store.queries`` reuses this same constant
(``queries.ORDER_BY_SQL``, this module's own name re-bound to it, R-2 --
one definition) for its ``cvi_p``/custom-weight projections so the ordering
clause is defined once. The full read-path surface F-013 asked for (ranked/
not-ranked listings, snapshot summary, matrix, evidence, reprojection) lives
in ``cvi.store.queries``; this module keeps only what W4-2 built.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from cvi.store.queries import ORDER_BY_SQL as _ORDER_BY_SQL, row_to_dict as _row_to_dict

__all__ = [
    "get_snapshot",
    "get_row",
    "get_evidence",
    "list_not_ranked",
    "ranked_order_sql",
]


def get_snapshot(conn: sqlite3.Connection, snapshot_id: str) -> Optional[Dict[str, Any]]:
    """The ``snapshots`` row for ``snapshot_id``, or ``None``."""
    cursor = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,))
    row = cursor.fetchone()
    if row is None:
        return None
    columns = [description[0] for description in cursor.description]
    return dict(zip(columns, row))


def get_row(conn: sqlite3.Connection, snapshot_id: str, owner: str, name: str) -> Optional[Dict[str, Any]]:
    """One repo's ``repositories`` row at ``snapshot_id``, joined with its
    snapshot's ``benchmark_version`` (`04` section 5: not duplicated onto
    ``repositories`` -- one definition per fact, see ``schema.py``), or
    ``None`` if no such row exists. Boolean and JSON columns are decoded
    back to their Python shapes (`06` section 4 row contract)."""
    cursor = conn.execute(
        """
        SELECT r.*, s.benchmark_version AS benchmark_version
        FROM repositories r JOIN snapshots s ON r.snapshot_id = s.id
        WHERE r.snapshot_id = ? AND r.owner = ? AND r.name = ?
        """,
        (snapshot_id, owner, name),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    columns = [description[0] for description in cursor.description]
    return _row_to_dict(columns, row)


def get_evidence(conn: sqlite3.Connection, snapshot_id: str, owner: str, name: str) -> List[Dict[str, Any]]:
    """The ``params`` long rows for one repo at ``snapshot_id`` (value,
    score, measured, reason, n, window, method, source, source_hash,
    fetched_at), ordered by ``param_id``. Empty for a gated repo (never
    scored) or an unknown repo -- both indistinguishable from this call
    alone; use :func:`get_row` first to tell them apart."""
    cursor = conn.execute(
        """
        SELECT p.* FROM params p
        JOIN repositories r ON p.repo_id = r.id
        WHERE r.snapshot_id = ? AND r.owner = ? AND r.name = ?
        ORDER BY p.param_id
        """,
        (snapshot_id, owner, name),
    )
    rows = cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    evidence = []
    for row in rows:
        record = dict(zip(columns, row))
        if record.get("measured") is not None:
            record["measured"] = bool(record["measured"])
        if record.get("raw_value") is not None:
            record["raw_value"] = json.loads(record["raw_value"])
        evidence.append(record)
    return evidence


def list_not_ranked(conn: sqlite3.Connection, snapshot_id: str) -> List[Dict[str, Any]]:
    """Gated (``eligible=0``) and unrated (``eligible=1``, ``rated=0``)
    repos at ``snapshot_id`` with their reasons (D-TIE-2: listed after
    every rated row, unranked, ordered by lowercase ``owner/name``)."""
    cursor = conn.execute(
        """
        SELECT r.*, s.benchmark_version AS benchmark_version
        FROM repositories r JOIN snapshots s ON r.snapshot_id = s.id
        WHERE r.snapshot_id = ? AND (r.eligible = 0 OR r.rated = 0)
        ORDER BY lower(r.owner || '/' || r.name) ASC
        """,
        (snapshot_id,),
    )
    rows = cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    return [_row_to_dict(columns, row) for row in rows]


def ranked_order_sql(conn: sqlite3.Connection, snapshot_id: str) -> List[tuple]:
    """The SQL-computed D-TIE-1 order for rated rows at ``snapshot_id``:
    ``(owner, name, sql_rank)`` in final display order.

    ``sql_rank`` is ``ROW_NUMBER() OVER (ORDER BY <_ORDER_BY_SQL>)`` (strict
    1..N, `04` section 6). This function computes the order fresh from
    stored columns for parity testing against
    :func:`cvi.core.tiebreak.tie_key`; it does not read or write the stored
    ``rank``/``tie_group`` columns (those are the writer's job, from the
    already-ranked ``ScoreRecord``). ``tie_group`` (``DENSE_RANK`` over the
    composite level) is W4-3's -- see the module docstring."""
    sql = f"""
        SELECT owner, name,
               ROW_NUMBER() OVER (ORDER BY {_ORDER_BY_SQL}) AS sql_rank
        FROM repositories
        WHERE snapshot_id = ? AND rated = 1
        ORDER BY sql_rank
    """
    return conn.execute(sql, (snapshot_id,)).fetchall()
