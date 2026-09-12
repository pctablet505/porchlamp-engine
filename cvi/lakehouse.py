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

import json
import sqlite3
from datetime import datetime
from typing import Any, Optional

from cvi.deepdive import build_deep_dive
from cvi.engine import assign_tier
from cvi.matrix import decompose
from cvi.models import (
    AIPosture,
    CVICompositeResult,
    Ecosystem,
    Persona,
    PillarScores,
    Quadrant,
    RepoMeta,
    Tier,
)
from cvi.persona import persona_composite

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
CREATE INDEX IF NOT EXISTS idx_cvi_overall_rank
    ON repositories (overall_cvi DESC, owner, name);
CREATE INDEX IF NOT EXISTS idx_cvi_quadrant
    ON repositories (matrix_quadrant, overall_cvi DESC);
CREATE INDEX IF NOT EXISTS idx_cvi_search
    ON repositories (LOWER(owner), LOWER(name));
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
    # Deep-dive payload (spec 12.2)
    # ------------------------------------------------------------------ #
    def ensure_deep_dive_column(self) -> None:
        """Idempotently add the optional ``deep_dive_json`` TEXT column.

        Spec 12.2's deep-dive payload is stored per-repo in an *additive*
        column on ``repositories``. SQLite has no ``ADD COLUMN IF NOT
        EXISTS``, so presence is checked via ``PRAGMA table_info`` first.
        The column is deliberately NOT part of the spec 11.2 base schema
        (``_SCHEMA``): a fresh store keeps the exact spec column set, and
        the column materializes only when a deep-dive payload is stored.
        """
        columns = {
            r[1]
            for r in self._conn.execute("PRAGMA table_info(repositories)").fetchall()
        }
        if "deep_dive_json" not in columns:
            self._conn.execute(
                "ALTER TABLE repositories ADD COLUMN deep_dive_json TEXT"
            )
            self._conn.commit()

    def upsert_deep_dive(self, owner: str, name: str, payload: dict) -> int:
        """Store the serialized deep-dive payload (spec 12.2) on a row.

        JSON-serializes ``payload`` (``sort_keys=True`` for stable
        output) and UPDATEs ``deep_dive_json`` on the ``(owner, name)``
        row. Returns the number of rows updated: 1 on success, 0 when the
        repository is absent from the table (no row is created).
        """
        self.ensure_deep_dive_column()
        cur = self._conn.execute(
            "UPDATE repositories SET deep_dive_json = ? "
            "WHERE owner = ? AND name = ?",
            (json.dumps(payload, sort_keys=True), owner, name),
        )
        self._conn.commit()
        return int(cur.rowcount)

    def seed_benchmark_catalog(self, repos: list[dict[str, Any]]) -> None:
        """Bulk insert repository definitions in a single transaction in <2.0s."""
        self.ensure_deep_dive_column()
        from .benchmark_data import BENCHMARK_NOW, generate_synthetic_pr_events

        when = BENCHMARK_NOW
        when_iso = when.isoformat()
        rows = []

        for item in repos:
            pillars = item.get("pillars", (50.0, 50.0, 50.0, 50.0, 50.0))
            is_gated = item.get("is_gated", False)

            if is_gated:
                cvi = 0.0
                tier = Tier.D.value
                quadrant = Quadrant.GRAVEYARD.value
                lev = 0.0
                fric = 100.0
                is_dq = 1
                dq_reason = "PR ghost rate exceeds 50% ceiling (black hole)"
                p_scores = (0.0, 0.0, 0.0, 0.0, 0.0)
                ps = None
            else:
                ps = PillarScores(*pillars)
                lev, fric, quad = decompose(ps)
                cvi = persona_composite(ps, Persona.BALANCED)
                tier = assign_tier(cvi).value
                quadrant = quad.value
                is_dq = 0
                dq_reason = None
                p_scores = pillars

            meta = RepoMeta(
                owner=item["owner"],
                name=item["name"],
                ecosystem=item["ecosystem"],
                stars=item["stars"],
                forks=item["forks"],
                monthly_downloads=item["monthly_downloads"],
                dependents_count=item["dependents_count"],
                elephant_factor=item["elephant_factor"],
                legal_ip_model=item["legal_ip_model"],
                ai_posture=item["ai_posture"],
                active_nonbot_commits_180d=item.get("active_nonbot_commits_180d", 100),
                off_platform_reviews=item.get("off_platform_reviews", False),
                hard_ai_ban=item.get("hard_ai_ban", False),
                active_unclaimed_gfis=item.get("active_unclaimed_gfis", 5),
            )
            pr_events = generate_synthetic_pr_events(
                owner=item["owner"],
                name=item["name"],
                p1=pillars[0],
                now=when,
                is_gated=is_gated,
            )
            dd = build_deep_dive(
                meta,
                pr_events,
                when,
                pillar_scores=ps,
                overall_cvi=cvi,
            )
            dd_str = json.dumps(dd, sort_keys=True)

            eco_str = (
                item["ecosystem"].value
                if hasattr(item["ecosystem"], "value")
                else str(item["ecosystem"])
            )
            legal_str = (
                item["legal_ip_model"].value
                if hasattr(item["legal_ip_model"], "value")
                else str(item["legal_ip_model"])
            )
            ai_str = (
                item["ai_posture"].value
                if hasattr(item["ai_posture"], "value")
                else str(item["ai_posture"])
            )

            rows.append((
                item["owner"],
                item["name"],
                eco_str,
                item.get("domain_category", ""),
                item["stars"],
                item["forks"],
                item["monthly_downloads"],
                item["dependents_count"],
                round(pillars[1] / 100.0, 4),
                item["elephant_factor"],
                legal_str,
                ai_str,
                p_scores[0],
                p_scores[1],
                p_scores[2],
                p_scores[3],
                p_scores[4],
                lev,
                fric,
                quadrant,
                cvi,
                tier,
                is_dq,
                dq_reason,
                when_iso,
                dd_str,
            ))

        sql = """
        INSERT INTO repositories (
            owner, name, ecosystem, domain_category, stars, forks,
            monthly_downloads, dependents_count, openssf_criticality,
            elephant_factor, legal_ip_model, ai_posture,
            score_responsiveness, score_criticality, score_governance,
            score_rigor_ai, score_accessibility, leverage_score,
            friction_score, matrix_quadrant, overall_cvi, rank_tier,
            is_stage0_disqualified, disqualification_reason, last_indexed_at,
            deep_dive_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(owner, name) DO UPDATE SET
            ecosystem = excluded.ecosystem,
            domain_category = excluded.domain_category,
            stars = excluded.stars,
            forks = excluded.forks,
            monthly_downloads = excluded.monthly_downloads,
            dependents_count = excluded.dependents_count,
            openssf_criticality = excluded.openssf_criticality,
            elephant_factor = excluded.elephant_factor,
            legal_ip_model = excluded.legal_ip_model,
            ai_posture = excluded.ai_posture,
            score_responsiveness = excluded.score_responsiveness,
            score_criticality = excluded.score_criticality,
            score_governance = excluded.score_governance,
            score_rigor_ai = excluded.score_rigor_ai,
            score_accessibility = excluded.score_accessibility,
            leverage_score = excluded.leverage_score,
            friction_score = excluded.friction_score,
            matrix_quadrant = excluded.matrix_quadrant,
            overall_cvi = excluded.overall_cvi,
            rank_tier = excluded.rank_tier,
            is_stage0_disqualified = excluded.is_stage0_disqualified,
            disqualification_reason = excluded.disqualification_reason,
            last_indexed_at = excluded.last_indexed_at,
            deep_dive_json = excluded.deep_dive_json
        """
        with self._conn:
            self._conn.executemany(sql, rows)

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    def get_repo(self, owner: str, name: str) -> Optional[dict]:
        """Full row for ``(owner, name)`` as a dict, or None if absent.

        The dict is built from the live table description, so when the
        optional ``deep_dive_json`` column (spec 12.2, materialized by
        :meth:`upsert_deep_dive` / :meth:`ensure_deep_dive_column`) exists
        it appears as a key (``None`` when no payload has been stored).
        """
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
            eco_str = ecosystem.value if hasattr(ecosystem, "value") else str(ecosystem)
            where.append("LOWER(ecosystem) = LOWER(?)")
            params.append(eco_str)
        if quadrant is not None:
            where.append("matrix_quadrant = ?")
            params.append(quadrant)
        if min_stars > 0:
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
        ecosystem: Optional[str | Ecosystem] = None,
        persona: Optional[str | Persona] = None,
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        min_stars: int = 0,
    ) -> tuple[list[dict], int]:
        """Paginated leaderboard with ecosystem filter, substring search, and persona re-projection.

        Returns ``(items, total_count)`` where ``items`` is a list of row dicts for the
        requested slice, and ``total_count`` is the total number of matching repositories
        before pagination.
        """
        limit_val = max(0, int(limit))
        offset_val = max(0, int(offset))

        # Parse persona
        persona_enum: Optional[Persona] = None
        if persona is not None:
            if isinstance(persona, Persona):
                persona_enum = persona
            else:
                try:
                    persona_enum = Persona(str(persona).lower().strip())
                except ValueError:
                    raise ValueError(f"Unknown persona: {persona!r}") from None

        where: list[str] = []
        params: list[Any] = []

        if ecosystem is not None:
            eco_str = ecosystem.value if hasattr(ecosystem, "value") else str(ecosystem)
            where.append("LOWER(ecosystem) = LOWER(?)")
            params.append(eco_str)

        if min_stars > 0:
            where.append("stars >= ?")
            params.append(int(min_stars))

        if search is not None and search.strip():
            term = f"%{search.strip().lower()}%"
            where.append(
                "(LOWER(owner) LIKE ? OR LOWER(name) LIKE ? OR LOWER(owner || '/' || name) LIKE ?)"
            )
            params.extend([term, term, term])

        where_clause = f" WHERE {' AND '.join(where)}" if where else ""

        # Fast path: Balanced or default ranking (no re-projection needed)
        if persona_enum is None or persona_enum == Persona.BALANCED:
            count_sql = f"SELECT COUNT(*) FROM repositories{where_clause}"
            total_count = int(self._conn.execute(count_sql, params).fetchone()[0])

            query_sql = (
                f"SELECT * FROM repositories{where_clause} "
                "ORDER BY overall_cvi DESC, owner, name LIMIT ? OFFSET ?"
            )
            query_params = list(params) + [limit_val, offset_val]
            cur = self._conn.execute(query_sql, query_params)
            cols = [d[0] for d in cur.description]
            items = [dict(zip(cols, row)) for row in cur.fetchall()]
            return items, total_count

        # Persona re-projection path
        query_sql = f"SELECT * FROM repositories{where_clause}"
        cur = self._conn.execute(query_sql, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        total_count = len(rows)

        for row in rows:
            if row["is_stage0_disqualified"] or (
                persona_enum is Persona.AI_ASSISTED
                and row["ai_posture"] == AIPosture.PROSCRIBED.value
            ) or (
                persona_enum is Persona.SENIOR_SYSTEMS
                and row["elephant_factor"] < 3
            ):
                cvi_val = 0.0
                tier_val = Tier.D.value
                is_gated_val = 1
            else:
                ps = PillarScores(
                    responsiveness=float(row["score_responsiveness"]),
                    criticality=float(row["score_criticality"]),
                    governance=float(row["score_governance"]),
                    rigor=float(row["score_rigor_ai"]),
                    accessibility=float(row["score_accessibility"]),
                )
                cvi_val = persona_composite(ps, persona_enum)
                tier_val = assign_tier(cvi_val).value
                is_gated_val = 0

            row["overall_cvi"] = cvi_val
            row["rank_tier"] = tier_val
            row["is_stage0_disqualified"] = is_gated_val

        rows.sort(key=lambda r: (-r["overall_cvi"], r["owner"], r["name"]))
        items = rows[offset_val : offset_val + limit_val]
        return items, total_count

    def count_by_quadrant(self, ecosystem: Optional[str] = None) -> dict[str, int]:
        """Quadrant -> repo count, for filter-toolbar count badges.

        Always returns all four quadrants (0 when absent) so the toolbar
        can render every badge.
        """
        sql = "SELECT matrix_quadrant, COUNT(*) FROM repositories"
        params: list[Any] = []
        if ecosystem is not None:
            eco_str = ecosystem.value if hasattr(ecosystem, "value") else str(ecosystem)
            sql += " WHERE LOWER(ecosystem) = LOWER(?)"
            params.append(eco_str)
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
