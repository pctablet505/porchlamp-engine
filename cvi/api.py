"""CVI REST API layer (spec 12).

FastAPI facade over :class:`cvi.lakehouse.OperationalStore` plus the
Phase-4/5 math from :mod:`cvi.persona`.

Routes (spec 12.1-12.3):

- ``GET  /v1/cvi/matrix``         matrix spatial query
- ``GET  /v1/cvi/repos/{o}/{n}``  repository deep-dive scorecard
- ``POST /v1/cvi/reproject``      dynamic persona re-projection
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

from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, model_validator

from cvi.lakehouse import OperationalStore
from cvi.models import PillarScores, Persona
from cvi.persona import PILLAR_KEYS, WEIGHT_SUM_TOLERANCE, rank_deltas, reproject

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
        store: OperationalStore = Depends(get_store),
    ) -> dict:
        """Scatter nodes + quadrant count badges (spec 12.1)."""
        nodes = [
            {"repo_id": _repo_id(row["owner"], row["name"]), **row}
            for row in store.query_matrix(
                ecosystem=ecosystem,
                min_stars=min_stars,
                exclude_black_holes=exclude_black_holes,
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
        """Full stored row + nested pillar_scores (spec 12.2)."""
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
        return {**row, "pillar_scores": pillar_scores}

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
