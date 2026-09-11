"""Operational relational storage for the CVI ranking engine (spec 11.2).

Implements the ``repositories`` table from ``docs/ARCHITECTURE.md``
section 11.2 and the query shapes consumed by the API layer (spec 12):
matrix scatter nodes, leaderboards, and quadrant count badges.

Backend: Python stdlib ``sqlite3`` only. SQL is kept simple/ANSI so a
DuckDB backend can replace this module later.

Interface decisions (spec is source of truth; these are documented
ambiguities resolved in favour of the spec text):

- Enums are stored as their ``.value`` strings (spec 11.2 declares the
  corresponding columns as VARCHAR).
- ``CVICompositeResult`` carries no raw repo metrics (domain_category,
  stars, forks, monthly_downloads, dependents_count,
  openssf_criticality, elephant_factor, legal_ip_model, ai_posture).
  ``upsert_composite`` therefore stores documented defaults for those
  columns: ``domain_category=""``, ``stars=0``, ``forks=0``,
  ``monthly_downloads=0``, ``dependents_count=0``,
  ``openssf_criticality=0.0``, ``elephant_factor=1``,
  ``legal_ip_model="DCO"``, ``ai_posture="AD_HOC"``. The ingestion layer
  is expected to update those columns directly (or a future upsert
  variant will accept them).
- ``last_indexed_at=None``: on INSERT the DB default
  (``CURRENT_TIMESTAMP``) applies; on conflict-update the existing value
  is preserved.
- ``leaderboard(persona=...)``: persona re-projection happens in the API
  layer (spec 12.3); the parameter is accepted and ignored here.
- ``count_by_quadrant`` always returns all four quadrants (0 when absent)
  so the filter toolbar can render every badge.
- ``query_matrix`` / ``leaderboard`` order by ``overall_cvi DESC`` with
  ``(owner, name)`` as a deterministic tie-breaker.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Optional

from cvi.models import CVICompositeResult, PillarScores, Quadrant

__all__ = ["OperationalStore"]

# Ordered column list of the repositories table (spec 11.2).
_COLUMNS: tuple[str, ...] = (
    "id",
    "owner",
    "name",
    "ecosystem",
    "domain_category",
    "stars",
    "forks",
    "monthly_downloads",
    "dependents_count",
    "openssf_criticality",
    "elephant_factor",
    "legal_ip_model",
    "ai_posture",
    "score_responsiveness",
    "score_criticality",
    "score_governance",
    "score_rigor_ai",
    "score_accessibility",
    "leverage_score",
    "friction_score",
    "matrix_quadrant",
    "overall_cvi",
    "rank_tier",
    "is_stage0_disqualified",
    "disqualification_reason",
    "last_indexed_at",
)

# (table column, PillarScores attribute) pairs for the five pillar columns.
_PILLAR_COLUMNS: tuple[tuple[str, str], ...] = (
    ("score_responsiveness", "responsiveness"),
    ("score_criticality", "criticality"),
    ("score_governance", "governance"),
    ("score_rigor_ai", "rigor"),
    ("score_accessibility", "accessibility"),
)

# Documented defaults for raw-metric columns that CVICompositeResult does
# not carry (see module docstring).
_RAW_METRIC_DEFAULTS: dict[str, Any] = {
    "domain_category": "",
    "stars": 0,
    "forks": 0,
    "monthly_downloads": 0,
    "dependents_count": 0,
    "openssf_criticality": 0.0,
    "elephant_factor": 1,
    "legal_ip_model": "DCO",
    "ai_posture": "AD_HOC",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS repositories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner VARCHAR(100) NOT NULL,
    name VARCHAR(100) NOT NULL,
    ecosystem VARCHAR(50) NOT NULL,
    domain_category VARCHAR(50) NOT NULL,
    stars INTEGER NOT NULL,
    forks INTEGER NOT NULL,
    monthly_downloads BIGINT DEFAULT 0,
    dependents_count INTEGER DEFAULT 0,
    openssf_criticality REAL NOT NULL,
    elephant_factor INTEGER NOT NULL,
    legal_ip_model VARCHAR(30) NOT NULL,
    ai_posture VARCHAR(30) NOT NULL,
    score_responsiveness REAL NOT NULL,
    score_criticality REAL NOT NULL,
    score_governance REAL NOT NULL,
    score_rigor_ai REAL NOT NULL,
    score_accessibility REAL NOT NULL,
    leverage_score REAL NOT NULL,
    friction_score REAL NOT NULL,
    matrix_quadrant VARCHAR(30) NOT NULL,
    overall_cvi REAL NOT NULL,
    rank_tier VARCHAR(30) NOT NULL,
    is_stage0_disqualified BOOLEAN DEFAULT FALSE,
    disqualification_reason TEXT,
    last_indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner, name)
);
CREATE INDEX IF NOT EXISTS idx_cvi_ecosystem_rank
    ON repositories (ecosystem, overall_cvi DESC);
CREATE INDEX IF NOT EXISTS idx_cvi_quadrant
    ON repositories (matrix_quadrant, overall_cvi DESC);
"""


class OperationalStore:
    """SQLite-backed operational store for scored CVI composites (spec 11.2).

    One row per ``(owner, name)``. All enums are persisted as their
    ``.value`` strings.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(_SCHEMA)

    @property
    def connection(self) -> sqlite3.Connection:
        """Underlying ``sqlite3.Connection`` (schema inspection / direct SQL)."""
        return self._conn

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #
    def upsert_composite(
        self,
        result: CVICompositeResult,
        pillar_scores: Optional[PillarScores] = None,
        last_indexed_at: Optional[datetime] = None,
    ) -> int:
        """Insert or update the composite for ``(owner, name)``; return rowid.

        ``INSERT ... ON CONFLICT(owner, name) DO UPDATE``: a second upsert
        for the same repo updates the existing row in place (rowid stable,
        last write wins). Pillar columns come from the ``pillar_scores``
        argument (NOT from ``result.pillar_scores``) and default to 0.0
        when it is None. ``last_indexed_at`` is stored as an ISO string;
        when None the DB default applies on insert and the stored value is
        preserved on update.
        """
        values: dict[str, Any] = {
            "owner": result.owner,
            "name": result.name,
            "domain_category": _RAW_METRIC_DEFAULTS["domain_category"],
            "stars": _RAW_METRIC_DEFAULTS["stars"],
            "forks": _RAW_METRIC_DEFAULTS["forks"],
            "monthly_downloads": _RAW_METRIC_DEFAULTS["monthly_downloads"],
            "dependents_count": _RAW_METRIC_DEFAULTS["dependents_count"],
            "openssf_criticality": _RAW_METRIC_DEFAULTS["openssf_criticality"],
            "elephant_factor": _RAW_METRIC_DEFAULTS["elephant_factor"],
            "legal_ip_model": _RAW_METRIC_DEFAULTS["legal_ip_model"],
            "ai_posture": _RAW_METRIC_DEFAULTS["ai_posture"],
            "ecosystem": result.ecosystem.value,
            "leverage_score": result.leverage_score,
            "friction_score": result.friction_score,
            "matrix_quadrant": result.matrix_quadrant.value,
            "overall_cvi": result.overall_cvi,
            "rank_tier": result.rank_tier.value,
            "is_stage0_disqualified": int(result.is_stage0_disqualified),
            "disqualification_reason": result.disqualification_reason,
        }
        for column, attr in _PILLAR_COLUMNS:
            values[column] = (
                float(getattr(pillar_scores, attr))
                if pillar_scores is not None
                else 0.0
            )
        if last_indexed_at is not None:
            values["last_indexed_at"] = last_indexed_at.isoformat()

        columns = [c for c in _COLUMNS if c in values]
        placeholders = ", ".join("?" for _ in columns)
        # owner/name are the conflict key; last_indexed_at is only in the
        # SET list when explicitly provided (see module docstring).
        update_columns = [c for c in columns if c not in ("owner", "name")]
        set_clause = ", ".join(f"{c} = excluded.{c}" for c in update_columns)
        sql = (
            f"INSERT INTO repositories ({', '.join(columns)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT(owner, name) DO UPDATE SET {set_clause} "
            f"RETURNING id"
        )
        row = self._conn.execute(sql, [values[c] for c in columns]).fetchone()
        self._conn.commit()
        return int(row[0])

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    def get_repo(self, owner: str, name: str) -> Optional[dict]:
        """Full row for ``(owner, name)`` as a dict, or None if absent."""
        cur = self._conn.execute(
            "SELECT * FROM repositories WHERE owner = ? AND name = ?",
            (owner, name),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return dict(zip((d[0] for d in cur.description), row))

    def query_matrix(
        self,
        ecosystem: Optional[str] = None,
        quadrant: Optional[str] = None,
        min_stars: int = 0,
        exclude_black_holes: bool = False,
        limit: int = 500,
    ) -> list[dict]:
        """Scatter nodes for the 2D matrix (spec 12.1).

        Each node: ``{owner, name, x=friction_score, y=leverage_score,
        cvi=overall_cvi, tier, quadrant, size=stars,
        is_gated=is_stage0_disqualified}``. ``min_stars`` is inclusive
        (``stars >= min_stars``). Rows are ordered by ``overall_cvi DESC``
        with ``(owner, name)`` tie-breaker.
        """
        where: list[str] = []
        params: list[Any] = []
        if ecosystem is not None:
            where.append("ecosystem = ?")
            params.append(ecosystem)
        if quadrant is not None:
            where.append("matrix_quadrant = ?")
            params.append(quadrant)
        where.append("stars >= ?")
        params.append(int(min_stars))
        if exclude_black_holes:
            where.append("is_stage0_disqualified = 0")
        sql = (
            "SELECT owner, name, friction_score AS x, leverage_score AS y, "
            "overall_cvi AS cvi, rank_tier AS tier, matrix_quadrant AS quadrant, "
            "stars AS size, is_stage0_disqualified AS is_gated "
            "FROM repositories"
        )
        if where:
            sql += f" WHERE {' AND '.join(where)}"
        sql += " ORDER BY overall_cvi DESC, owner, name LIMIT ?"
        params.append(int(limit))
        rows = self._conn.execute(sql, params).fetchall()
        keys = ("owner", "name", "x", "y", "cvi", "tier", "quadrant", "size", "is_gated")
        return [dict(zip(keys, row)) for row in rows]

    def leaderboard(
        self,
        ecosystem: Optional[str] = None,
        persona: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Full rows ranked by ``overall_cvi DESC`` (spec 12.3 input).

        ``persona`` is reserved: persona re-projection happens in the API
        layer (spec 12.3); it is accepted and ignored here.
        """
        where: list[str] = []
        params: list[Any] = []
        if ecosystem is not None:
            where.append("ecosystem = ?")
            params.append(ecosystem)
        sql = "SELECT * FROM repositories"
        if where:
            sql += f" WHERE {' AND '.join(where)}"
        sql += " ORDER BY overall_cvi DESC, owner, name LIMIT ?"
        params.append(int(limit))
        cur = self._conn.execute(sql, params)
        return [dict(zip((d[0] for d in cur.description), row)) for row in cur.fetchall()]

    def count_by_quadrant(self, ecosystem: Optional[str] = None) -> dict[str, int]:
        """Quadrant -> repo count, for filter-toolbar count badges.

        Always returns all four quadrants (0 when absent) so the toolbar
        can render every badge.
        """
        sql = "SELECT matrix_quadrant, COUNT(*) FROM repositories"
        params: list[Any] = []
        if ecosystem is not None:
            sql += " WHERE ecosystem = ?"
            params.append(ecosystem)
        sql += " GROUP BY matrix_quadrant"
        counts = {q.value: 0 for q in Quadrant}
        for quadrant, n in self._conn.execute(sql, params):
            counts[quadrant] = n
        return counts

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        """Close the underlying connection (idempotent)."""
        self._conn.close()
