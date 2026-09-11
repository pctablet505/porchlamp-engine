"""CVI REST API layer (spec 12).

FastAPI facade over :class:`cvi.lakehouse.OperationalStore` plus the
Phase-4/5 math from :mod:`cvi.persona`.

Routes (spec 12.1-12.3):

- ``GET  /v1/cvi/matrix``         matrix spatial query
- ``GET  /v1/cvi/repos/{o}/{n}``  repository deep-dive scorecard
- ``POST /v1/cvi/reproject``      dynamic persona re-projection
- ``GET  /v1/cvi/leaderboard``    ranked & paginated leaderboard
- ``GET  /healthz``               liveness probe

Interface decisions (documented ambiguities; the spec is source of
truth and these are resolved in favour of the spec text):

- ``repo_id`` is the synthetic identifier ``"{owner}/{name}"`` — the
  autoincrement ``id`` column is not exposed by ``query_matrix``, and
  ``(owner, name)`` is the table's stable natural key.
- Matrix node ``size`` = raw ``stars`` column; ``is_gated`` =
  ``is_stage0_disqualified`` (spec 12.1 node schema).
- The matrix ``domain`` and ``persona`` query parameters are **reserved
  no-ops**: 2D coordinates are persona-invariant (leverage/friction are
  fixed formulas, spec 2 Phase 4), and domain filtering does not exist
  in the store yet. Both are accepted, documented in the OpenAPI
  schema, and ignored.
- ``quadrant_counts`` reflects the *ecosystem-filtered table* (store
  contract: no ``min_stars``/gating applied) so the filter toolbar can
  render totals, not just the visible scatter.
- ``GET /v1/cvi/repos/{owner}/{name}`` returns the full stored row plus
  a nested ``pillar_scores`` object (the 5 pillar keys) for radar
  charts; 404 with a JSON body when the repo is missing.
  When the row's optional ``deep_dive_json`` column is populated, a
  nested ``deep_dive`` object (the spec 12.2 payload, parsed from the
  column) is included; when the column is absent or NULL the key is
  omitted. The raw column still rides along in the full-row spread.
- ``POST /v1/cvi/reproject`` validates the weight vector (exact 5-key
  set, sum == 1.0 within 1e-6) **before** any store access; invalid
  payloads receive a 422 JSON error body. On success it scans the full
  table (old ranking = ``overall_cvi DESC``, ties by ``owner/name``),
  re-scores every repo via ``cvi.persona.reproject``, and returns the
  re-ranked array with ``old_rank`` / ``new_rank`` / ``delta``
  (``old - new``; positive = moved up), ties broken by old-list
  position via ``cvi.persona.rank_deltas``.
- ``create_app(store=None)`` defaults to a fresh in-memory
  ``OperationalStore`` so a bare import suffices for local dev/tests.
- Route handlers are ``async def``: the ``sqlite3`` connection is
  thread-bound, and a sync handler would be offloaded to FastAPI's
  worker thread pool. Running handlers on the event-loop thread keeps
  every store access on a single thread (the one that owns the
  connection), in both uvicorn and ``TestClient``.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, model_validator

from cvi.engine import assign_tier
from cvi.lakehouse import OperationalStore
from cvi.models import AIPosture, Persona, PillarScores, Tier
from cvi.persona import (
    PILLAR_KEYS,
    WEIGHT_SUM_TOLERANCE,
    persona_composite,
    rank_deltas,
    reproject,
)

__all__ = ["ReprojectRequest", "create_app", "get_store"]

#: Full-table scan cap for POST /v1/cvi/reproject. Re-projection is a
#: whole-table operation; the operational store is dimensioned far
#: below this.
REPROJECT_SCAN_LIMIT = 100_000


def _repo_id(owner: str, name: str) -> str:
    return f"{owner}/{name}"


def get_store(request: Request) -> OperationalStore:
    """Return the store attached to the app (DI seam, set by create_app)."""
    return request.app.state.store


class ReprojectRequest(BaseModel):
    """POST /v1/cvi/reproject payload.

    ``weights`` must cover exactly the 5 pillar keys and sum to 1.0
    within 1e-6; anything else is rejected with a 422 JSON error body
    before the store is touched.
    """

    weights: dict[str, float]

    @model_validator(mode="after")
    def _check_weights(self) -> "ReprojectRequest":
        missing = [k for k in PILLAR_KEYS if k not in self.weights]
        extra = [k for k in self.weights if k not in PILLAR_KEYS]
        if missing or extra:
            raise ValueError(
                f"weights must contain exactly the pillar keys {list(PILLAR_KEYS)}; "
                f"missing={missing}, extra={extra}"
            )
        total = sum(self.weights.values())
        if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
            raise ValueError(
                f"weights must sum to 1.0 within {WEIGHT_SUM_TOLERANCE:g}; got {total!r}"
            )
        return self


def create_app(store: Optional[OperationalStore] = None) -> FastAPI:
    """Build the CVI API app around ``store``.

    When ``store`` is None a fresh in-memory ``OperationalStore`` is
    created (and owned by the app, for its lifetime).
    """
    app = FastAPI(title="CVI", version="1.0.0")
    app.state.store = store if store is not None else OperationalStore()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/cvi/matrix")
    async def matrix(
        ecosystem: Optional[str] = Query(default=None),
        domain: Optional[str] = Query(
            default=None,
            description="Reserved: accepted but not yet applied (no-op).",
        ),
        persona: Persona = Query(
            default=Persona.BALANCED,
            description=(
                "Reserved: 2D matrix coordinates are persona-invariant "
                "(spec 2 Phase 4); accepted but no-op."
            ),
        ),
        min_stars: int = Query(default=100, ge=0),
        exclude_black_holes: bool = Query(default=True),
        limit: int = Query(default=1000, ge=1, le=5000),
        store: OperationalStore = Depends(get_store),
    ) -> dict:
        """Scatter nodes + quadrant count badges (spec 12.1)."""
        nodes = [
            {"repo_id": _repo_id(row["owner"], row["name"]), **row}
            for row in store.query_matrix(
                ecosystem=ecosystem,
                min_stars=min_stars,
                exclude_black_holes=exclude_black_holes,
                limit=limit,
            )
        ]
        return {
            "nodes": nodes,
            "quadrant_counts": store.count_by_quadrant(ecosystem=ecosystem),
        }

    @app.get("/v1/cvi/repos/{owner}/{name}")
    async def repo_deep_dive(
        owner: str,
        name: str,
        store: OperationalStore = Depends(get_store),
    ) -> dict:
        """Full stored row + nested pillar_scores (+ deep_dive, spec 12.2)."""
        row = store.get_repo(owner, name)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"repository {owner}/{name} not found"
            )
        pillar_scores = {
            "responsiveness": row["score_responsiveness"],
            "criticality": row["score_criticality"],
            "governance": row["score_governance"],
            "rigor": row["score_rigor_ai"],
            "accessibility": row["score_accessibility"],
        }
        body = {**row, "pillar_scores": pillar_scores}
        deep_dive_json = row.get("deep_dive_json")
        if deep_dive_json is not None:
            body["deep_dive"] = json.loads(deep_dive_json)
        return body

    @app.get("/v1/cvi/leaderboard")
    async def leaderboard_endpoint(
        ecosystem: Optional[str] = Query(default=None),
        persona: Persona = Query(default=Persona.BALANCED),
        min_stars: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        search: Optional[str] = Query(default=None),
        store: OperationalStore = Depends(get_store),
    ) -> dict:
        """Paginated leaderboard with filtering, fuzzy search, and dynamic persona re-projection."""
        where: list[str] = []
        params: list[Any] = []
        if ecosystem is not None:
            where.append("LOWER(ecosystem) = LOWER(?)")
            params.append(ecosystem)
        if min_stars > 0:
            where.append("stars >= ?")
            params.append(min_stars)
        if search is not None and search.strip():
            term = f"%{search.strip().lower()}%"
            where.append(
                "(LOWER(owner) LIKE ? OR LOWER(name) LIKE ? OR LOWER(owner || '/' || name) LIKE ?)"
            )
            params.extend([term, term, term])

        sql = "SELECT * FROM repositories"
        if where:
            sql += f" WHERE {' AND '.join(where)}"
        cur = store.connection.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        total = len(rows)

        processed: list[dict[str, Any]] = []
        for row in rows:
            pillar_scores = {
                "responsiveness": float(row["score_responsiveness"]),
                "criticality": float(row["score_criticality"]),
                "governance": float(row["score_governance"]),
                "rigor": float(row["score_rigor_ai"]),
                "accessibility": float(row["score_accessibility"]),
            }
            if persona == Persona.BALANCED:
                cvi_val = float(row["overall_cvi"])
                tier_val = str(row["rank_tier"])
                is_gated_val = int(row["is_stage0_disqualified"])
            else:
                ps = PillarScores(
                    responsiveness=pillar_scores["responsiveness"],
                    criticality=pillar_scores["criticality"],
                    governance=pillar_scores["governance"],
                    rigor=pillar_scores["rigor"],
                    accessibility=pillar_scores["accessibility"],
                )
                if row["is_stage0_disqualified"] or (
                    persona is Persona.AI_ASSISTED
                    and row["ai_posture"] == AIPosture.PROSCRIBED.value
                ) or (
                    persona is Persona.SENIOR_SYSTEMS
                    and row["elephant_factor"] < 3
                ):
                    cvi_val = 0.0
                    tier_val = Tier.D.value
                    is_gated_val = 1
                else:
                    cvi_val = persona_composite(ps, persona)
                    tier_val = assign_tier(cvi_val).value
                    is_gated_val = 0

            processed.append({
                "repo_id": _repo_id(row["owner"], row["name"]),
                "owner": row["owner"],
                "name": row["name"],
                "ecosystem": row["ecosystem"],
                "cvi": cvi_val,
                "overall_cvi": cvi_val,
                "tier": tier_val,
                "rank_tier": tier_val,
                "quadrant": row["matrix_quadrant"],
                "matrix_quadrant": row["matrix_quadrant"],
                "x": float(row["friction_score"]),
                "y": float(row["leverage_score"]),
                "friction_score": float(row["friction_score"]),
                "leverage_score": float(row["leverage_score"]),
                "pillar_scores": pillar_scores,
                "score_responsiveness": pillar_scores["responsiveness"],
                "score_criticality": pillar_scores["criticality"],
                "score_governance": pillar_scores["governance"],
                "score_rigor_ai": pillar_scores["rigor"],
                "score_accessibility": pillar_scores["accessibility"],
                "stars": int(row["stars"]),
                "downloads": int(row["monthly_downloads"]),
                "monthly_downloads": int(row["monthly_downloads"]),
                "dependents": int(row["dependents_count"]),
                "dependents_count": int(row["dependents_count"]),
                "elephant_factor": int(row["elephant_factor"]),
                "legal_ip_model": row["legal_ip_model"],
                "ai_posture": row["ai_posture"],
                "license": row.get("license"),
                "is_gated": is_gated_val,
                "is_stage0_disqualified": is_gated_val,
            })

        processed.sort(key=lambda r: (-r["cvi"], r["owner"], r["name"]))

        items = processed[offset : offset + limit]
        for i, item in enumerate(items):
            item["rank"] = offset + i + 1

        return {
            "total": total,
            "page": offset // limit + 1,
            "limit": limit,
            "items": items,
        }

    @app.post("/v1/cvi/reproject")
    async def reproject_endpoint(
        body: ReprojectRequest,
        store: OperationalStore = Depends(get_store),
    ) -> list[dict]:
        """Re-rank every repo under custom weights (spec 12.3)."""
        weights = body.weights
        # Old ranking: overall_cvi DESC, ties by owner/name (store contract).
        rows = store.leaderboard(limit=REPROJECT_SCAN_LIMIT)
        by_id: dict[str, dict] = {}
        before: list[tuple[str, float]] = []
        for row in rows:
            repo_id = _repo_id(row["owner"], row["name"])
            by_id[repo_id] = row
            before.append((repo_id, float(row["overall_cvi"])))
        position = {repo_id: i for i, (repo_id, _) in enumerate(before)}
        new_scores: dict[str, float] = {}
        for repo_id, row in by_id.items():
            pillar_scores = PillarScores(
                responsiveness=row["score_responsiveness"],
                criticality=row["score_criticality"],
                governance=row["score_governance"],
                rigor=row["score_rigor_ai"],
                accessibility=row["score_accessibility"],
            )
            new_scores[repo_id] = reproject(pillar_scores, weights)
        deltas = rank_deltas(before, new_scores)
        # New ranking: new_score DESC, ties by position in the old list.
        new_order = sorted(
            by_id, key=lambda rid: (-new_scores[rid], position[rid])
        )
        result: list[dict] = []
        for new_rank, repo_id in enumerate(new_order, start=1):
            row = by_id[repo_id]
            old_rank = position[repo_id] + 1
            result.append(
                {
                    "repo_id": repo_id,
                    "owner": row["owner"],
                    "name": row["name"],
                    "old_rank": old_rank,
                    "new_rank": new_rank,
                    "delta": deltas[repo_id],
                    "new_score": new_scores[repo_id],
                    "x": row["friction_score"],
                    "y": row["leverage_score"],
                    "quadrant": row["matrix_quadrant"],
                }
            )
        return result

    return app
