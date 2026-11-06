"""Operational relational storage for the Porchlamp ranking engine (spec 11.2).

Implements the ``repositories`` table from ``docs/ARCHITECTURE.md``
section 11.2 and the query shapes consumed by the API layer (spec 12):
matrix scatter nodes, leaderboards, and quadrant count badges.

Backend: Python stdlib ``sqlite3`` only. SQL is kept simple/ANSI so a
DuckDB backend can replace this module later.

Interface decisions (spec is source of truth; these are documented
ambiguities resolved in favour of the spec text):

- Enums are stored as their ``.value`` strings (spec 11.2 declares the
  corresponding columns as VARCHAR).
- ``PorchlampCompositeResult`` carries no raw repo metrics (domain_category,
  stars, forks, monthly_downloads, dependents_count,
  openssf_criticality, elephant_factor, legal_ip_model, ai_posture).
  ``upsert_composite`` therefore stores documented defaults for those
  columns: ``domain_category=""``, ``stars=0``, ``forks=0``,
  ``monthly_downloads=0``, ``dependents_count=0``,
  ``openssf_criticality=0.0``, ``elephant_factor=1``,
  ``legal_ip_model="DCO"``, ``ai_posture="AD_HOC"``. The ingestion layer
  is expected to update those columns directly (or a future upsert
  variant will accept them).
- ``last_indexed_at=None``: on INSERT the column stores NULL (no SQL-side
  clock default, D-VER-3); on conflict-update the existing value is
  preserved.
- ``leaderboard(persona=...)``: persona re-projection happens in the API
  layer (spec 12.3); the parameter is accepted and ignored here.
- ``count_by_quadrant`` always returns all four quadrants (0 when absent)
  so the filter toolbar can render every badge.
- ``query_matrix`` / ``leaderboard`` order by ``overall_porchlamp DESC`` with
  ``(owner, name)`` as a deterministic tie-breaker.
- ``snapshot_meta`` (`03` §5 F10) binds a promotion to the frozen snapshot
  it was scored against: ``scripts/promote.py``
  writes one row per promotion carrying the manifest's identity plus
  :func:`repositories_dump_hash`, recomputed over the just-written
  ``repositories`` table. ``scripts/serve.py`` recomputes that hash at
  startup and refuses unless it still matches a stored row, so an ``UPDATE``
  after promotion (renaming an owner, nudging a counter) invalidates every
  row's claim instead of being invisible to a name-based check.
  :func:`repositories_dump_hash` scopes
  :func:`porchlamp.snapshot.reproduce.canonical_dump_hash` to the ``repositories``
  table alone (on a scratch copy, via ``DROP TABLE``) rather than calling it
  on ``db_path`` directly: ``snapshot_meta`` itself lives in the same
  database file, so a hash over the whole file would describe a state
  (with no ``snapshot_meta`` row yet) that no longer exists the instant the
  row recording that hash is written -- every promoted-then-served database
  would refuse itself.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from porchlamp.engine import assign_tier
from porchlamp.models import (
    AIPosture,
    PorchlampCompositeResult,
    Ecosystem,
    LegalIPModel,
    Persona,
    PillarScores,
    Quadrant,
    Tier,
)
from porchlamp.persona import persona_composite
from porchlamp.snapshot.reproduce import canonical_dump_hash

__all__ = ["OperationalStore", "repositories_dump_hash"]

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
    "overall_porchlamp",
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

# Documented defaults for raw-metric columns that PorchlampCompositeResult does
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
    overall_porchlamp REAL NOT NULL,
    rank_tier VARCHAR(30) NOT NULL,
    is_stage0_disqualified BOOLEAN DEFAULT FALSE,
    disqualification_reason TEXT,
    last_indexed_at TIMESTAMP,
    UNIQUE(owner, name)
);
CREATE INDEX IF NOT EXISTS idx_porchlamp_ecosystem_rank
    ON repositories (ecosystem, overall_porchlamp DESC);
CREATE INDEX IF NOT EXISTS idx_porchlamp_overall_rank
    ON repositories (overall_porchlamp DESC, owner, name);
CREATE INDEX IF NOT EXISTS idx_porchlamp_quadrant
    ON repositories (matrix_quadrant, overall_porchlamp DESC);
CREATE INDEX IF NOT EXISTS idx_porchlamp_search
    ON repositories (LOWER(owner), LOWER(name));
CREATE TABLE IF NOT EXISTS snapshot_meta (
    snapshot_id VARCHAR(200) PRIMARY KEY,
    as_of TIMESTAMP NOT NULL,
    benchmark_version VARCHAR(50) NOT NULL,
    constants_hash VARCHAR(64) NOT NULL,
    manifest_root VARCHAR(64) NOT NULL,
    canonical_dump_hash VARCHAR(64) NOT NULL,
    promoted_at TIMESTAMP NOT NULL
);
"""

#: Tables :func:`repositories_dump_hash` keeps out of its identity hash: the
#: bookkeeping table that records the hash cannot also be an input to it.
_DUMP_HASH_EXCLUDED_TABLES = frozenset({"snapshot_meta"})


def repositories_dump_hash(db_path: str) -> str:
    """:func:`porchlamp.snapshot.reproduce.canonical_dump_hash` of the ``repositories``
    table alone.

    ``db_path`` may carry other tables (today, just ``snapshot_meta``) that
    are bookkeeping about the promotion, not promoted content; hashing them
    in would make the value ``write_snapshot_meta`` stores describe a
    database state that stops existing the instant that very row is
    written, so every promoted-then-served database would fail its own
    check. This scopes the SAME hash function (no second implementation)
    to a scratch copy with every excluded table dropped, so
    ``scripts/promote.py`` (writing the value) and ``scripts/serve.py``
    (checking it) always compute over identical content.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        copy_path = Path(tmp_dir) / "repositories_dump.db"
        # Read-only: a wrong path must raise, never create an empty database
        # whose hash is the empty-input digest.
        source = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
        target = sqlite3.connect(copy_path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        conn = sqlite3.connect(copy_path)
        try:
            tables = [
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            ]
            if "legacy_repositories" in tables:
                if "repositories" in tables:
                    conn.execute('DROP TABLE "repositories"')
                conn.execute('ALTER TABLE "legacy_repositories" RENAME TO "repositories"')
                tables = [t for t in tables if t not in ("legacy_repositories", "repositories")] + ["repositories"]
            for table in tables:
                if table != "repositories":
                    conn.execute(f'DROP TABLE "{table}"')
            conn.commit()
        finally:
            conn.close()
        return canonical_dump_hash(copy_path)


class OperationalStore:
    """SQLite-backed operational store for scored Porchlamp composites (spec 11.2).

    One row per ``(owner, name)``. All enums are persisted as their
    ``.value`` strings.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(db_path, timeout=30.0)
        self._conn.execute("PRAGMA busy_timeout=30000;")
        has_legacy = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='legacy_repositories'"
        ).fetchone()
        has_table = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='repositories'"
        ).fetchone()
        has_v1_cols = False
        if has_table:
            cols = {
                r[1] for r in self._conn.execute("PRAGMA table_info(repositories)").fetchall()
            }
            has_v1_cols = "last_indexed_at" in cols
        if not has_legacy and not has_v1_cols:
            if not has_table:
                self._conn.executescript(_SCHEMA)
                self._table = "repositories"
            else:
                raise sqlite3.OperationalError("no v1 repositories table in v2 database")
        else:
            self._table = "legacy_repositories" if has_legacy else "repositories"
        self._leaderboard_cache: dict[tuple, tuple[list[dict], int]] = {}

    @property
    def connection(self) -> sqlite3.Connection:
        """Underlying ``sqlite3.Connection`` (schema inspection / direct SQL)."""
        return self._conn

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #
    def upsert_composite(
        self,
        result: PorchlampCompositeResult,
        pillar_scores: Optional[PillarScores] = None,
        last_indexed_at: Optional[datetime] = None,
    ) -> int:
        """Insert or update the composite for ``(owner, name)``; return rowid.

        ``INSERT ... ON CONFLICT(owner, name) DO UPDATE``: a second upsert
        for the same repo updates the existing row in place (rowid stable,
        last write wins). Pillar columns come from the ``pillar_scores``
        argument (NOT from ``result.pillar_scores``) and default to 0.0
        when it is None. ``last_indexed_at`` is stored as an ISO string;
        when None the column stores NULL on insert and the stored value is
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
            "overall_porchlamp": result.overall_porchlamp,
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
            f"INSERT INTO {self._table} ({', '.join(columns)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT(owner, name) DO UPDATE SET {set_clause} "
            f"RETURNING id"
        )
        row = self._conn.execute(sql, [values[c] for c in columns]).fetchone()
        self._conn.commit()
        self._leaderboard_cache.clear()
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
            for r in self._conn.execute(f"PRAGMA table_info({self._table})").fetchall()
        }
        if "deep_dive_json" not in columns:
            self._conn.execute(
                f"ALTER TABLE {self._table} ADD COLUMN deep_dive_json TEXT"
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
            f"UPDATE {self._table} SET deep_dive_json = ? "
            "WHERE owner = ? AND name = ?",
            (json.dumps(payload, sort_keys=True), owner, name),
        )
        self._conn.commit()
        self._leaderboard_cache.clear()
        return int(cur.rowcount)

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
            f"SELECT * FROM {self._table} WHERE owner = ? AND name = ?",
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
        porchlamp=overall_porchlamp, tier, quadrant, size=stars,
        is_gated=is_stage0_disqualified}``. ``min_stars`` is inclusive
        (``stars >= min_stars``). Rows are ordered by ``overall_porchlamp DESC``
        with ``(owner, name)`` tie-breaker.
        """
        where: list[str] = []
        params: list[Any] = []
        if ecosystem is not None:
            eco_str = (
                ecosystem.value if hasattr(ecosystem, "value") else str(ecosystem)
            ).lower()
            where.append("ecosystem = ?")
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
            "overall_porchlamp AS porchlamp, rank_tier AS tier, matrix_quadrant AS quadrant, "
            "stars AS size, is_stage0_disqualified AS is_gated "
            f"FROM {self._table}"
        )
        if where:
            sql += f" WHERE {' AND '.join(where)}"
        sql += " ORDER BY overall_porchlamp DESC, owner, name LIMIT ?"
        params.append(int(limit))
        rows = self._conn.execute(sql, params).fetchall()
        keys = ("owner", "name", "x", "y", "porchlamp", "tier", "quadrant", "size", "is_gated")
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

        cache_key = (
            str(ecosystem).lower() if ecosystem is not None else None,
            persona_enum.value if persona_enum is not None else None,
            search.strip().lower() if (search and search.strip()) else None,
            limit_val,
            offset_val,
            int(min_stars),
        )
        if cache_key in self._leaderboard_cache:
            cached_items, cached_total = self._leaderboard_cache[cache_key]
            return [dict(d) for d in cached_items], cached_total

        where: list[str] = []
        params: list[Any] = []

        if ecosystem is not None:
            eco_str = (
                ecosystem.value if hasattr(ecosystem, "value") else str(ecosystem)
            ).lower()
            where.append("ecosystem = ?")
            params.append(eco_str)

        if min_stars > 0:
            where.append("stars >= ?")
            params.append(int(min_stars))

        if search is not None and search.strip():
            # Escape LIKE wildcards so a literal '%' or '_' in user input is not
            # treated as a wildcard (e.g. searching "100%" must not match every
            # repo). Backslash is the escape char, declared via ESCAPE below.
            raw = search.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            term = f"%{raw}%"
            where.append(
                "(LOWER(owner) LIKE ? ESCAPE '\\' OR LOWER(name) LIKE ? ESCAPE '\\'"
                " OR LOWER(owner || '/' || name) LIKE ? ESCAPE '\\')"
            )
            params.extend([term, term, term])

        where_clause = f" WHERE {' AND '.join(where)}" if where else ""

        # Fast path: Balanced or default ranking (no re-projection needed)
        if persona_enum is None or persona_enum == Persona.BALANCED:
            count_sql = f"SELECT COUNT(*) FROM {self._table}{where_clause}"
            total_count = int(self._conn.execute(count_sql, params).fetchone()[0])

            # Explicit column list (not SELECT *): the leaderboard never serves
            # the optional deep_dive_json blob, so don't read it for every row.
            query_sql = (
                f"SELECT {', '.join(_COLUMNS)} FROM {self._table}{where_clause} "
                "ORDER BY overall_porchlamp DESC, owner, name LIMIT ? OFFSET ?"
            )
            query_params = list(params) + [limit_val, offset_val]
            cur = self._conn.execute(query_sql, query_params)
            cols = [d[0] for d in cur.description]
            items = [dict(zip(cols, row)) for row in cur.fetchall()]
            if len(self._leaderboard_cache) >= 512:
                self._leaderboard_cache.clear()
            self._leaderboard_cache[cache_key] = (items, total_count)
            return [dict(d) for d in items], total_count

        # Persona re-projection path
        query_sql = f"SELECT {', '.join(_COLUMNS)} FROM {self._table}{where_clause}"
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
                porchlamp_val = 0.0
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
                porchlamp_val = persona_composite(ps, persona_enum)
                tier_val = assign_tier(porchlamp_val).value
                is_gated_val = 0

            row["overall_porchlamp"] = porchlamp_val
            row["rank_tier"] = tier_val
            row["is_stage0_disqualified"] = is_gated_val

        rows.sort(key=lambda r: (-r["overall_porchlamp"], r["owner"], r["name"]))
        items = rows[offset_val : offset_val + limit_val]
        if len(self._leaderboard_cache) >= 512:
            self._leaderboard_cache.clear()
        self._leaderboard_cache[cache_key] = (items, total_count)
        return [dict(d) for d in items], total_count

    def count_by_quadrant(self, ecosystem: Optional[str] = None) -> dict[str, int]:
        """Quadrant -> repo count, for filter-toolbar count badges.

        Always returns all four quadrants (0 when absent) so the toolbar
        can render every badge.
        """
        sql = f"SELECT matrix_quadrant, COUNT(*) FROM {self._table}"
        params: list[Any] = []
        if ecosystem is not None:
            eco_str = (
                ecosystem.value if hasattr(ecosystem, "value") else str(ecosystem)
            ).lower()
            sql += " WHERE ecosystem = ?"
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
        self._leaderboard_cache.clear()
        self._conn.close()


# --------------------------------------------------------------------------- #
# Raw-metric hydration (spec 8, 11.2)
#
# OperationalStore is the only argument type these helpers take, so they live
# beside it and keep the v1 pipeline's import path one hop deep.
# --------------------------------------------------------------------------- #
def _enum_value(value: Any) -> str:
    """Store enums as their ``.value`` strings (lakehouse convention)."""
    return value.value if isinstance(value, Enum) else str(value)


def apply_raw_metrics(
    store: "OperationalStore",
    owner: str,
    name: str,
    *,
    stars: int,
    forks: int,
    monthly_downloads: int = 0,
    dependents_count: int = 0,
    elephant_factor: int = 1,
    legal_ip_model: "LegalIPModel | str" = LegalIPModel.DCO,
    ai_posture: "AIPosture | str" = AIPosture.AD_HOC,
) -> int:
    """Additively update one repository's raw-metric columns (spec 11.2).

    Runs ``UPDATE repositories SET stars=?, forks=?, monthly_downloads=?,
    dependents_count=?, elephant_factor=?, legal_ip_model=?, ai_posture=?
    WHERE owner=? AND name=?`` via ``store.connection`` — the exact column
    names from this module's own ``CREATE TABLE repositories``. This is
    what makes ``query_matrix(min_stars=...)`` return rows after
    ``upsert_composite`` (which stores ``stars=0`` by default). Returns the
    number of rows updated (0 when the repo is not yet indexed).
    """
    sql = (
        "UPDATE repositories SET "
        "stars = ?, forks = ?, monthly_downloads = ?, dependents_count = ?, "
        "elephant_factor = ?, legal_ip_model = ?, ai_posture = ? "
        "WHERE owner = ? AND name = ?"
    )
    cursor = store.connection.execute(
        sql,
        (
            int(stars),
            int(forks),
            int(monthly_downloads),
            int(dependents_count),
            int(elephant_factor),
            _enum_value(legal_ip_model),
            _enum_value(ai_posture),
            owner,
            name,
        ),
    )
    store.connection.commit()
    if hasattr(store, "_leaderboard_cache"):
        store._leaderboard_cache.clear()
    return cursor.rowcount
