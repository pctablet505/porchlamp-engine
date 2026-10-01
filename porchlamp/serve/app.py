"""Porchlamp v2 API: the endpoints `web/v2/` consumes, served
straight off the v2 store (`porchlamp/store/queries.py` + `porchlamp/store/reader.py`)
through the wire contract (`porchlamp/serve/contract.py`).

:func:`create_app` is the app factory: `create_app(db_path, *, web_dir) ->
FastAPI`. Every route is a thin projection -- store row(s) in, one exact
contract model out (`response_model=`) -- never a raw store dict. The store
functions this module calls (`porchlamp.store.queries`, `porchlamp.store.reader`) provide
the data; nothing here recomputes a score, a tier, a coverage band, or a tie
group (with the exception of `_reprojected_tier_ceiling`, documented at its
call site).

Bridge rules (store shape -> wire contract):

1. A persona-projected row's flat `rank`/`tie_group`/`porchlamp`/`coverage`/`tier`
   are the `_p`-suffixed values (`rank_p`/`tie_group_p`/`porchlamp_p`/`coverage_p`/`tier_p`)
   computed by `porchlamp.store.queries.list_ranked` -- never the stored balanced ones,
   which can be `None` on a row that is balanced-unrated but persona-rated.
   See :func:`_leaderboard_row`.
2. `balanced_rank`/`rank_delta` are the stored balanced `rank` and its
   difference from the resolved `rank` above; both stay `None` under the
   balanced lens itself and when the balanced `rank` is itself `None`.
3. `gate_reason == "unrated: no_external_workflow"` is translated to the
   UNRATED group with `unrated_reason="no_external_workflow"` rather than
   widening `GateReason`. See :func:`_classify_not_ranked`.
4. `unrated_reason="off_platform_routed"` is derived from the stored
   `routed == "off_platform"` column on an eligible repository.
   See :func:`_classify_not_ranked`.
5. `list_not_ranked`'s one flat `reason` field is split back into
   `gate_reason`/`unrated_reason`/`persona_gate_reason` per group.
   See :func:`_split_not_ranked`.
6. Persona gate codes (`ai_hard_ban`, `elephant_factor_below_3`,
   `high_barrier_to_entry`, `criticality_below_threshold`,
   `insufficient_evidence:<input>`) pass through unchanged.
7. `sort` accepts the store's bare `rank`/`porchlamp`/`coverage`/`tier` AND
   `p_<pillar>` (translated to the bare pillar name `porchlamp.store.queries`
   expects), with an optional leading `-` for direction.
   See :func:`_translate_sort`.
8. `page`/`page_size` accept any valid paging values (`page_size` 1..1000,
   `page >= 1`).
9. `snapshot_summary`'s dict matches `SnapshotSummary` field-for-field.
10. `EvidenceRow.pillar` is validated against the closed set defined by
    `porchlamp.core.constants.PARAM_INPUT_IDS`.
11. `MatrixResponse.axis_coverage` reports coverage statistics per axis.
12. `raw_hash` is exposed on both `LeaderboardRow` and `RepoDetailResponse`
    for integrity verification.

Threading: every route is `async def` -- handlers run on the event-loop thread
rather than being offloaded to a worker thread, keeping store access on the
thread that owns the connection. The v2 connection opened uses
`check_same_thread=False` to support test client dispatch.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Mapping, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError as PydanticValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from porchlamp.audit.methodology_history import methodology_with_history
from porchlamp.core import constants
from porchlamp.core.methodology import MethodologyDocument
from porchlamp.core.pipeline import DISPLAY_UNRATED_GATE_REASONS
from porchlamp.serve.contract import (
    DEFAULT_PAGE_SIZE,
    EvidenceRow,
    LeaderboardResponse,
    LeaderboardRow,
    MatrixAxisCoverage,
    MatrixPoint,
    MatrixResponse,
    NotRankedResponse,
    NotRankedRow,
    ReprojectRequest,
    ReprojectResponse,
    ReprojectRow,
    RepoDetailResponse,
    SnapshotCounts,
    SnapshotSummary,
)
from porchlamp.serve.guard import run_guard
from porchlamp.store import queries, reader

__all__ = ["create_app"]

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_CHANGELOG_PATH: Path = _REPO_ROOT / "docs" / "v2" / "rfcs" / "CHANGELOG.md"

_PILLARS = constants.PILLAR_NAMES

# --------------------------------------------------------------------------- #
# Consistent error body (rule 1: "{'error': {'endpoint', 'status', 'detail'}}"
# that the UI's U-15 banner can render). One shape, three sources: a plain
# HTTPException this module raises (404/503), a `ValueError` a store
# validation function raises (persona/sort/paging/weights -- rule 1's "422
# on invalid weights with the validator's message" generalised to every
# other query-side ValueError, not just reproject's), and FastAPI's own
# `RequestValidationError` (a malformed query/body type FastAPI rejects
# before a handler ever runs).
# --------------------------------------------------------------------------- #


def _error_body(request: Request, status_code: int, detail: str) -> dict:
    return {"error": {"endpoint": request.url.path, "status": status_code, "detail": detail}}


def _internal_error_response(request: Request) -> JSONResponse:
    """Every unhandled server fault uses the same `{"error": ...}`
    shape as a 404/422/503, with a non-leaking detail (never the raw
    exception text -- that could expose internal schema/state)."""
    return JSONResponse(status_code=500, content=_error_body(request, 500, "internal server error"))


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(status_code=exc.status_code, content=_error_body(request, exc.status_code, detail))

    @app.exception_handler(RequestValidationError)
    async def _request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        detail = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()
        )
        return JSONResponse(status_code=422, content=_error_body(request, 422, detail))

    @app.exception_handler(ValueError)
    async def _value_error(request: Request, exc: ValueError) -> JSONResponse:
        # PydanticValidationError is a ValueError subclass, so it would
        # otherwise reach this handler too -- relabelling a server fault (a
        # stored value the contract cannot represent, e.g. an out-of-Literal
        # `quadrant`, or an evidence row whose `param_id` is unknown to `PARAMS`)
        # as a client 422. It is mapped to 500 (`_internal_error_response`),
        # never a 422 -- only a genuine caller-input problem (persona/sort/
        # paging/weights, all plain `ValueError`s raised by `porchlamp.store.queries`)
        # gets 422 here.
        if isinstance(exc, PydanticValidationError):
            logging.getLogger(__name__).exception("Unhandled contract validation error on %s", request.url.path)
            return _internal_error_response(request)
        return JSONResponse(status_code=422, content=_error_body(request, 422, str(exc)))

    @app.exception_handler(Exception)
    async def _unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        # Generic catch-all so any fault this module did not anticipate
        # (not an HTTPException, not a ValueError/ValidationError, not a
        # RequestValidationError) still reports the same shape instead of
        # Starlette's plain-text 500.
        logging.getLogger(__name__).exception("Unhandled exception on %s", request.url.path)
        return _internal_error_response(request)


# --------------------------------------------------------------------------- #
# Snapshot resolution: this API always serves ONE snapshot -- the most
# recently PROMOTED one (`finalize_snapshot_hash` stamps `promoted_at`),
# falling back to the most recently created if none has been promoted yet
# (a database written but not yet finalised). No `?snapshot_id=` query
# param exists in this endpoint list -- a caller cannot ask for a different one.
# --------------------------------------------------------------------------- #


def _current_snapshot_id(conn: sqlite3.Connection) -> Optional[str]:
    # A database without a `snapshots` table raises an OperationalError here.
    # Having no `snapshots` table is treated as the same "nothing to serve yet"
    # condition as an empty table, resolving to `None` so the caller's 503
    # handling (`_require_snapshot`) covers it.
    try:
        row = conn.execute(
            "SELECT id FROM snapshots WHERE promoted_at IS NOT NULL ORDER BY promoted_at DESC, id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is not None:
        return row[0]
    row = conn.execute("SELECT id FROM snapshots ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
    return row[0] if row is not None else None


def _require_snapshot(conn: sqlite3.Connection) -> str:
    """Every endpoint that touches a snapshot funnels through here:
    no snapshot at all, or a snapshot that exists but has not been
    finalised yet (`canonical_dump_hash IS NULL` -- `porchlamp.store.writer.
    finalize_snapshot_hash` has not run) both 503 identically on every
    snapshot-touching endpoint (`/methodology` is exempt as it needs no
    snapshot at all)."""
    snapshot_id = _current_snapshot_id(conn)
    if snapshot_id is None:
        raise HTTPException(status_code=503, detail="no snapshot has been written to this database yet")
    canonical_dump_hash = conn.execute(
        "SELECT canonical_dump_hash FROM snapshots WHERE id = ?", (snapshot_id,)
    ).fetchone()[0]
    if canonical_dump_hash is None:
        raise HTTPException(
            status_code=503,
            detail=f"snapshot {snapshot_id!r} has not been finalised (no canonical_dump_hash yet)",
        )
    return snapshot_id


# --------------------------------------------------------------------------- #
# Row projections (store dict -> contract model). Every field is named
# explicitly -- no `**record` passthrough anywhere -- so a store column
# this module does not recognise can never silently reach the wire, and a
# contract field this module forgets raises immediately (pydantic's
# required-field check), not a coincidental content bug months later.
# --------------------------------------------------------------------------- #


def _no_ceiling_or_band(coverage: float) -> Optional[str]:
    """Ceiling band as a label (not the index `queries._ceiling_index_case_sql`
    builds for SQL `CASE`). Used only by :func:`_reproject_row`: `queries.reproject`
    never recomputes `tier_ceiling` for custom coverage, so it is derived
    here instead from the same frozen constants."""
    if coverage >= constants.NO_CEILING_MIN_COVERAGE:
        return None
    if coverage >= constants.CEILING_A_MIN_COVERAGE:
        return "A"
    return "B"


def _project_leaderboard_row(
    record: Mapping[str, Any],
    *,
    rank: Optional[int],
    tie_group: Optional[int],
    porchlamp: Optional[float],
    coverage: float,
    tier: Optional[str],
    tier_ceiling: Optional[str],
    balanced_rank: Optional[int],
    rank_delta: Optional[int],
) -> LeaderboardRow:
    fields: dict[str, Any] = {
        "rank": rank,
        "tie_group": tie_group,
        "owner": record["owner"],
        "name": record["name"],
        "ecosystem": record["ecosystem"],
        "porchlamp": porchlamp,
        "coverage": coverage,
        "tier": tier,
        "tier_ceiling": tier_ceiling,
        "quadrant": record["quadrant"],
        "leverage": record["leverage"],
        "friction": record["friction"],
        "leverage_coverage": record["leverage_coverage"],
        "friction_coverage": record["friction_coverage"],
        "persona_gates": record["persona_gates"],
        "persona_composites": record["persona_composites"],
        "suspicion": record["suspicion"],
        "stars_audited": record["stars_audited"],
        "snapshot_id": record["snapshot_id"],
        "benchmark_version": record["benchmark_version"],
        "raw_hash": record["raw_hash"],
        "balanced_rank": balanced_rank,
        "rank_delta": rank_delta,
    }
    for pillar in _PILLARS:
        fields[f"p_{pillar}"] = record[f"p_{pillar}"]
        fields[f"c_{pillar}"] = record[f"c_{pillar}"]
    return LeaderboardRow(**fields)


def _leaderboard_row(record: Mapping[str, Any], *, persona: str) -> LeaderboardRow:
    """Bridge item 1/2: under `persona="balanced"` the flat fields are the
    stored balanced ones and there is no delta to show; under any other
    persona they are the `_p`-suffixed persona projection
    `porchlamp.store.queries.list_ranked`'s persona branch computes, with
    `balanced_rank`/`rank_delta` filled in from the stored balanced `rank`
    (both stay `None` together when that stored rank is itself `None` --
    a row that is persona-rated but balanced-unrated has nothing to show a
    delta against)."""
    if persona == "balanced":
        return _project_leaderboard_row(
            record,
            rank=record["rank"],
            tie_group=record["tie_group"],
            porchlamp=record["porchlamp"],
            coverage=record["coverage"],
            tier=record["tier"],
            tier_ceiling=record["tier_ceiling"],
            balanced_rank=None,
            rank_delta=None,
        )
    balanced_rank = record["rank"]
    rank_p = record["rank_p"]
    rank_delta = (balanced_rank - rank_p) if balanced_rank is not None else None
    return _project_leaderboard_row(
        record,
        rank=rank_p,
        tie_group=record["tie_group_p"],
        porchlamp=record["porchlamp_p"],
        coverage=record["coverage_p"],
        tier=record["tier_p"],
        tier_ceiling=record["tier_ceiling"],
        balanced_rank=balanced_rank,
        rank_delta=rank_delta,
    )


def _reproject_row(record: Mapping[str, Any]) -> ReprojectRow:
    """`queries.reproject`'s row already carries the custom projection
    under the plain `rank`/`porchlamp`/`coverage`/`tier`/`tie_group` names (its
    own docstring: "reprojection IS the view being asked for") plus
    `balanced_rank`/`balanced_tie_group`/`balanced_porchlamp`/`balanced_coverage`/
    `balanced_tier` for the comparison -- this module reuses `balanced_rank`
    for the SAME `rank_delta` semantics :func:`_leaderboard_row` gives a
    persona view, and recomputes `tier_ceiling` (see
    :func:`_no_ceiling_or_band`) since the row's own `tier_ceiling` column
    is the stale balanced value.

    Round-3 rebase note (2026-09-14): `ReprojectRow` (a `LeaderboardRow`
    SUBCLASS adding the four `balanced_tie_group`/`balanced_porchlamp`/
    `balanced_coverage`/`balanced_tier` fields -- Screen D's "live side
    list of rank deltas vs balanced" needs all five balanced figures from
    one response, not just `balanced_rank`) landed on this branch's own
    `porchlamp/serve/contract.py` (`ReprojectResponse.rows: list[ReprojectRow]`)
    independently of this module, which still built a bare `LeaderboardRow`
    here -- a plain parent-class instance does not satisfy a subclass-typed
    field, so every `/reproject` call 500'd on a pydantic ValidationError
    for the four missing fields. Fixed by building the shared
    `LeaderboardRow` fields once (`_project_leaderboard_row`, still used
    unchanged by :func:`_leaderboard_row`) and widening into a
    `ReprojectRow` with the four extra fields `queries.reproject` already
    provides on `record`."""
    balanced_rank = record["balanced_rank"]
    rank = record["rank"]
    rank_delta = (balanced_rank - rank) if balanced_rank is not None else None
    leaderboard_fields = _project_leaderboard_row(
        record,
        rank=rank,
        tie_group=record["tie_group"],
        porchlamp=record["porchlamp"],
        coverage=record["coverage"],
        tier=record["tier"],
        tier_ceiling=_no_ceiling_or_band(record["coverage"]),
        balanced_rank=balanced_rank,
        rank_delta=rank_delta,
    )
    return ReprojectRow(
        **leaderboard_fields.model_dump(),
        balanced_tie_group=record["balanced_tie_group"],
        balanced_porchlamp=record["balanced_porchlamp"],
        balanced_coverage=record["balanced_coverage"],
        balanced_tier=record["balanced_tier"],
    )


def _repo_detail(record: Mapping[str, Any]) -> RepoDetailResponse:
    fields: dict[str, Any] = {
        "owner": record["owner"],
        "name": record["name"],
        "ecosystem": record["ecosystem"],
        "snapshot_id": record["snapshot_id"],
        "benchmark_version": record["benchmark_version"],
        "raw_hash": record["raw_hash"],
        "eligible": record["eligible"],
        "gate_reason": record["gate_reason"],
        "rated": record["rated"],
        "unrated_reason": record["unrated_reason"],
        "rank": record["rank"],
        "tie_group": record["tie_group"],
        "porchlamp": record["porchlamp"],
        "coverage": record["coverage"],
        "tier": record["tier"],
        "tier_ceiling": record["tier_ceiling"],
        "quadrant": record["quadrant"],
        "leverage": record["leverage"],
        "friction": record["friction"],
        "leverage_coverage": record["leverage_coverage"],
        "friction_coverage": record["friction_coverage"],
        "persona_gates": record["persona_gates"],
        "persona_composites": record["persona_composites"],
        "suspicion": record["suspicion"],
        "stars_audited": record["stars_audited"],
    }
    for pillar in _PILLARS:
        fields[f"p_{pillar}"] = record[f"p_{pillar}"]
        fields[f"c_{pillar}"] = record[f"c_{pillar}"]
    return RepoDetailResponse(**fields)


def _classify_not_ranked(record: Mapping[str, Any], *, persona: str) -> tuple:
    """Bridge items 3-5: return `(group, reason)` for one
    `queries.list_not_ranked` row, where `group` is one of `"gated"`,
    `"unrated"`, `"persona_hidden"`. Precedence (matches
    `porchlamp.store.queries.list_not_ranked`'s own, plus the two translations
    the review flagged):

    1. Stage-0 ineligible (`eligible=0`): a `gate_reason` in
       `porchlamp.core.pipeline.DISPLAY_UNRATED_GATE_REASONS` (today: just
       `"unrated: no_external_workflow"` -- a GATE reason despite the
       spelling) becomes the UNRATED group with the bare
       `unrated_reason` (the `"unrated: "` prefix stripped for the wire);
       every other `gate_reason` stays in the GATED group unchanged. This
       is the SAME classification `porchlamp.core.pipeline.not_ranked_class`
       applies per-record to build `SnapshotCounts` and
       `porchlamp.store.queries.snapshot_summary` applies in SQL -- one
       constant, three call sites, so `/snapshot`'s counts and this
       endpoint's group sizes always agree.
    2. Eligible but excluded by the ACTIVE persona's own gate
       (`persona_gate_reason` is set, only possible when `persona !=
       "balanced"`): `"persona_hidden"`.
    3. Eligible, not persona-gated, `routed == "off_platform"`: UNRATED
       with `unrated_reason="off_platform_routed"` (derived -- nothing in
       the engine writes this string to a column directly).
    4. Otherwise: UNRATED with whatever `queries.list_not_ranked` already
       computed as `record["reason"]` (insufficient evidence, under the
       balanced or the active persona's own coverage)."""
    if not record["eligible"]:
        if record["gate_reason"] in DISPLAY_UNRATED_GATE_REASONS:
            return "unrated", record["gate_reason"].removeprefix("unrated: ")
        return "gated", record["gate_reason"]
    if not record.get("rated", True):
        if record.get("routed") == "off_platform":
            return "unrated", "off_platform_routed"
        return "unrated", record.get("unrated_reason") or record.get("reason", "insufficient_coverage")
    if persona != "balanced" and record.get("persona_gate_reason") is not None:
        return "persona_hidden", record["persona_gate_reason"]
    if record.get("routed") == "off_platform":
        return "unrated", "off_platform_routed"
    return "unrated", record.get("reason", "insufficient_coverage")


def _not_ranked_row(record: Mapping[str, Any], *, group: str, reason: str) -> NotRankedRow:
    return NotRankedRow(
        owner=record["owner"],
        name=record["name"],
        ecosystem=record["ecosystem"],
        coverage=record["coverage"],
        gate_reason=reason if group == "gated" else None,
        unrated_reason=reason if group == "unrated" else None,
        persona_gate_reason=reason if group == "persona_hidden" else None,
    )


def _split_not_ranked(rows: list, *, persona: str) -> tuple:
    gated: list[NotRankedRow] = []
    unrated: list[NotRankedRow] = []
    persona_hidden: list[NotRankedRow] = []
    buckets = {"gated": gated, "unrated": unrated, "persona_hidden": persona_hidden}
    for record in rows:
        group, reason = _classify_not_ranked(record, persona=persona)
        buckets[group].append(_not_ranked_row(record, group=group, reason=reason))
    return gated, unrated, persona_hidden


def _matrix_point(point: Mapping[str, Any]) -> Optional[MatrixPoint]:
    """`None` when the coordinate is not fully computable (`leverage`/
    `friction`/`quadrant` could not all be produced for this rated row --
    `porchlamp.store.queries.matrix_points` filters on `rated`, not on the
    coordinate itself being present): `MatrixPoint`'s `leverage`/
    `friction`/`quadrant` are all required, and a repo with no computable
    coordinate has nothing to plot, matching `06` section 3 B's "gated
    repos are never plotted" extended to "or repos the matrix genuinely
    cannot place." All three are checked independently, never inferring
    one from another's presence (a `quadrant` set while `friction` is
    `None` is a real, if unusual, stored state -- `quadrant` is computed
    at score time and never re-derived here). The caller folds every
    `None` into `excluded_count` alongside the truly-gated repos."""
    if point["quadrant"] is None or point["leverage"] is None or point["friction"] is None:
        return None
    return MatrixPoint(
        owner=point["owner"],
        name=point["name"],
        ecosystem=point["ecosystem"],
        leverage=point["leverage"],
        friction=point["friction"],
        leverage_coverage=point["leverage_coverage"],
        friction_coverage=point["friction_coverage"],
        quadrant=point["quadrant"],
        stars_audited=point["stars_audited"],
        porchlamp=point["porchlamp"],
        coverage=point["coverage"],
        tier=point["tier"],
    )


_SORT_BASE = ("rank", "porchlamp", "coverage", "tier")


def _translate_sort(sort: Optional[str]) -> tuple:
    """Translate sort parameter from API representation to store query format.
    The store's `ALLOWED_SORT_FIELDS` are bare (`rank`/`porchlamp`/`coverage`/
    `tier`/`<pillar>`); the API additionally accepts `p_<pillar>`
    (translated to `<pillar>`) and an optional leading `-` for direction.
    Returns `(field, descending)`."""
    if sort is None:
        return None, True
    descending = not sort.startswith("-")
    raw = sort[1:] if sort.startswith("-") else sort
    if raw in _SORT_BASE:
        return raw, descending
    if raw.startswith("p_") and raw[2:] in _PILLARS:
        return raw[2:], descending
    raise ValueError(
        f"sort must be one of {_SORT_BASE + tuple(f'p_{p}' for p in _PILLARS)} "
        f"(optionally prefixed with '-'); got {sort!r}"
    )


def _paging(page: int, page_size: int) -> tuple:
    return page_size, (page - 1) * page_size


def _require_snapshot_summary(conn: sqlite3.Connection, snapshot_id: str) -> dict:
    """Fetch snapshot summary for a validated snapshot."""
    summary = queries.snapshot_summary(conn, snapshot_id)
    if summary is None:  # pragma: no cover - snapshot_id came from this same conn moments ago
        raise HTTPException(status_code=503, detail=f"snapshot {snapshot_id!r} disappeared mid-request")
    return summary


# --------------------------------------------------------------------------- #
# Served-DB guard: both the v1 and v2 guards live in `porchlamp.serve.guard`.
# `porchlamp.serve.guard.run_guard` runs the v2 guard unconditionally (refuses a
# tampered or fixture-marked v2-shaped database) and the v1 guard only if the
# database has a v1 shape.
# --------------------------------------------------------------------------- #


#: Paths this mount must never SPA-fallback, ensuring that unrecognised API routes
#: or method mismatches return genuine 404/405 responses rather than index.html.
_API_RESERVED_PREFIXES = ("v1/", "legacy/")
_API_RESERVED_EXACT = ("v1", "legacy", "healthz")


class _SPAStaticFiles(StaticFiles):
    """Static file server with index fallback for single-page app routes.

    Registered after API routers so registered endpoints take precedence.
    Reservations under `_API_RESERVED_PREFIXES` and `_API_RESERVED_EXACT`
    are excluded from fallback so that API misses return genuine 404 responses,
    or 405 on method mismatches for known API paths.
    """

    async def get_response(self, path: str, scope):
        norm_path = path.lstrip("/")
        if norm_path in _API_RESERVED_EXACT or norm_path.startswith(_API_RESERVED_PREFIXES):
            method = scope.get("method", "GET")
            if method not in ("GET", "HEAD"):
                raise HTTPException(status_code=405, detail=f"method {method} not allowed on '/{norm_path}'")
            raise HTTPException(status_code=404, detail=f"no route matches '/{norm_path}'")
        if not self.directory or not Path(self.directory).is_dir():
            raise HTTPException(status_code=404, detail=f"no route matches '/{norm_path}'")
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


def _get_conn(request: Request) -> sqlite3.Connection:
    return request.app.state.conn


def _build_router() -> APIRouter:
    router = APIRouter(prefix="/v1/porchlamp")

    @router.get("/leaderboard", response_model=LeaderboardResponse)
    async def get_leaderboard(
        conn: sqlite3.Connection = Depends(_get_conn),
        persona: str = Query("balanced"),
        eco: Optional[str] = Query(None),
        q: Optional[str] = Query(None),
        sort: Optional[str] = Query(None),
        page: int = Query(1),
        page_size: int = Query(DEFAULT_PAGE_SIZE),
    ) -> LeaderboardResponse:
        snapshot_id = _require_snapshot(conn)
        store_sort, descending = _translate_sort(sort)
        limit, offset = _paging(page, page_size)
        rows, total = queries.list_ranked(
            conn, snapshot_id, persona=persona, ecosystem=eco, q=q, sort=store_sort,
            descending=descending, limit=limit, offset=offset,
        )
        summary = _require_snapshot_summary(conn, snapshot_id)
        return LeaderboardResponse(
            rows=[_leaderboard_row(row, persona=persona) for row in rows],
            total=total,
            counts=summary["counts"],
            page=page,
            page_size=page_size,
            persona=persona,
            ecosystem=eco,
        )

    @router.get("/not-ranked", response_model=NotRankedResponse)
    async def get_not_ranked(
        conn: sqlite3.Connection = Depends(_get_conn),
        persona: str = Query("balanced"),
        eco: Optional[str] = Query(None),
        q: Optional[str] = Query(None),
    ) -> NotRankedResponse:
        snapshot_id = _require_snapshot(conn)
        # One page of rows, fetched up to MAX_LIMIT; `counts` below must not be
        # recounted from `rows`, which is truncated whenever the store holds more.
        rows, _counts_by_reason, total_not_ranked = queries.list_not_ranked(
            conn, snapshot_id, persona=persona, ecosystem=eco, q=q, limit=queries.MAX_LIMIT, offset=0
        )
        # `_counts_by_reason` is keyed by gate/unrated reason only, which cannot
        # answer the gated/unrated split: the display rule moves
        # `DISPLAY_UNRATED_GATE_REASONS` rows out of `gated`, and that vocabulary
        # lives in core, not in the store's reason strings. So the authoritative
        # split comes from the snapshot summary instead, same numbers `/snapshot`
        # serves. Stage-0 gating is persona-invariant, so `counts.gated` needs no
        # persona adjustment; the persona view only changes the rated side.
        gated, unrated, persona_hidden = _split_not_ranked(rows, persona=persona)
        summary = _require_snapshot_summary(conn, snapshot_id)
        universe = summary["counts"]["total"]
        gated_count = summary["counts"]["gated"]
        rated = summary["persona_visible_counts"][persona]
        counts = SnapshotCounts(
            rated=rated, gated=gated_count, unrated=universe - rated - gated_count, total=universe,
        )
        return NotRankedResponse(
            gated=gated, unrated=unrated, persona_hidden=persona_hidden, counts=counts,
            truncated=total_not_ranked > len(rows),
        )

    @router.get("/matrix", response_model=MatrixResponse)
    async def get_matrix(
        conn: sqlite3.Connection = Depends(_get_conn),
        persona: str = Query("balanced"),
    ) -> MatrixResponse:
        snapshot_id = _require_snapshot(conn)
        result = queries.matrix_points(conn, snapshot_id, persona=persona)
        points: list[MatrixPoint] = []
        plotted_raw: list[Mapping[str, Any]] = []
        for raw_point in result["points"]:
            point = _matrix_point(raw_point)
            if point is not None:
                points.append(point)
                plotted_raw.append(raw_point)
        raw_axis_cov = result.get("axis_coverage")
        if raw_axis_cov is None:
            raw_axis_cov = queries.axis_coverage(plotted_raw)
        # `excluded_count` stays the store's own figure; rows the store listed
        # but the API could not render are counted separately, never merged.
        return MatrixResponse(
            points=points,
            excluded_count=result["excluded_count"],
            unrenderable_count=len(result["points"]) - len(points),
            axis_coverage={
                axis: MatrixAxisCoverage(**entry) for axis, entry in raw_axis_cov.items()
            },
        )

    @router.get("/repos/{owner}/{name}", response_model=RepoDetailResponse)
    async def get_repo(
        owner: str, name: str, conn: sqlite3.Connection = Depends(_get_conn)
    ) -> RepoDetailResponse:
        snapshot_id = _require_snapshot(conn)
        row = reader.get_row(conn, snapshot_id, owner, name)
        if row is None:
            raise HTTPException(status_code=404, detail=f"no repository {owner}/{name} in snapshot {snapshot_id}")
        return _repo_detail(row)

    @router.get("/repos/{owner}/{name}/evidence", response_model=list[EvidenceRow])
    async def get_evidence(
        owner: str, name: str, conn: sqlite3.Connection = Depends(_get_conn)
    ) -> list[EvidenceRow]:
        snapshot_id = _require_snapshot(conn)
        result = queries.repo_evidence(conn, snapshot_id, owner, name)
        if result is None:
            raise HTTPException(status_code=404, detail=f"no repository {owner}/{name} in snapshot {snapshot_id}")
        return [EvidenceRow(**param) for param in result["params"]]

    @router.get("/snapshot", response_model=SnapshotSummary)
    async def get_snapshot(conn: sqlite3.Connection = Depends(_get_conn)) -> SnapshotSummary:
        snapshot_id = _require_snapshot(conn)
        summary = _require_snapshot_summary(conn, snapshot_id)
        return SnapshotSummary(**summary)

    @router.get("/methodology", response_model=MethodologyDocument)
    async def get_methodology() -> MethodologyDocument:
        return methodology_with_history(_CHANGELOG_PATH)

    @router.post("/reproject", response_model=ReprojectResponse)
    async def post_reproject(
        payload: ReprojectRequest,
        conn: sqlite3.Connection = Depends(_get_conn),
        page: int = Query(1),
        page_size: int = Query(DEFAULT_PAGE_SIZE),
    ) -> ReprojectResponse:
        # `ReprojectRequest` has no paging fields in its body; `page` and
        # `page_size` are accepted as query params, defaulting to DEFAULT_PAGE_SIZE.
        snapshot_id = _require_snapshot(conn)
        weights = {
            "responsiveness": payload.weight_responsiveness,
            "criticality": payload.weight_criticality,
            "governance": payload.weight_governance,
            "rigor": payload.weight_rigor,
            "accessibility": payload.weight_accessibility,
        }
        limit, offset = _paging(page, page_size)
        rows, total = queries.reproject(conn, snapshot_id, weights, limit=limit, offset=offset)
        return ReprojectResponse(
            rows=[_reproject_row(row) for row in rows], total=total, page=page, page_size=page_size
        )

    return router


def _open_v2_connection(db_path: str) -> sqlite3.Connection:
    """A plain `sqlite3.connect` rather than `porchlamp.store.schema.connect`:
    the latter does not expose `check_same_thread`, which this module
    needs for `TestClient`'s portal-thread dispatch (module docstring).
    `PRAGMA foreign_keys = ON` is replicated (not imported -- it is a
    connection SETTING, not a schema/query DEFINITION) to match
    `schema.connect`'s own behaviour for any future write this read-mostly
    API might grow."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.create_function("py_round", 2, lambda val, n=2: round(float(val), n) if val is not None else None)
    return conn


def create_app(db_path, *, web_dir) -> FastAPI:
    """The Porchlamp v2 API app factory.

    Exposes `GET /v1/porchlamp/{leaderboard,not-ranked,matrix,snapshot,methodology}`,
    `GET /v1/porchlamp/repos/{owner}/{name}[/evidence]`, and `POST /v1/porchlamp/reproject`
    with responses typed through `porchlamp.serve.contract` models.

    Legacy v1 API (`porchlamp.api.create_app`) is mounted at `/legacy/` when `db_path`
    contains a v1 schema. Static files from `web_dir` are mounted at `/` with
    SPA routing fallback.

    Raises `SystemExit` if the served-DB guard refuses `db_path`."""
    db_path = str(db_path)
    legacy_store = run_guard(db_path)

    conn = _open_v2_connection(db_path)

    app = FastAPI(title="Porchlamp API v2")
    app.state.conn = conn

    _register_exception_handlers(app)
    app.include_router(_build_router())

    @app.get("/healthz")
    async def healthz(conn: sqlite3.Connection = Depends(_get_conn)) -> dict:
        return {"status": "ok", "snapshot_id": _current_snapshot_id(conn)}

    if legacy_store is not None:
        from porchlamp.api import create_app as _create_legacy_app

        app.mount("/legacy", _create_legacy_app(store=legacy_store))
    else:
        @app.get("/legacy/{path:path}")
        async def legacy_not_mounted(path: str) -> None:
            raise HTTPException(
                status_code=404,
                detail="/legacy is not mounted: this database has no v1 shape for the legacy API to read",
            )

    app.mount("/", _SPAStaticFiles(directory=str(web_dir), html=True, check_dir=False), name="web")

    return app
