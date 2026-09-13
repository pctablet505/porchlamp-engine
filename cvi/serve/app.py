"""CVI v2 API (packet W4-4): the endpoints `web/v2/` consumes, served
straight off the v2 store (`cvi/store/queries.py` + `cvi/store/reader.py`,
W4-3) through the wire contract (`cvi/serve/contract.py`, W4-5a).

:func:`create_app` is the app factory: `create_app(db_path, *, web_dir) ->
FastAPI`. Every route is a thin projection -- store row(s) in, one exact
contract model out (`response_model=`) -- never a raw store dict (rule 1
of the dispatch). The store functions this module calls (`cvi.store.
queries`, `cvi.store.reader`) are W4-3's; nothing here recomputes a score,
a tier, a coverage band or a tie group -- the one exception is
`_reprojected_tier_ceiling`, documented at its call site, which derives a
value `queries.reproject` does not compute from constants this module is
already allowed to import.

Bridge decisions (store shape -> wire contract), cited by number from the
W4-5a-review contract bridge table (`/tmp/.../scratchpad/reviews/
W4-5a-review.md`, "Contract bridge table for W4-4") plus the lead's
addendum ruling on it:

1. A persona-projected row's flat `rank`/`tie_group`/`cvi`/`coverage`/
   `tier` are the `_p`-suffixed values (`rank_p`/`tie_group_p`/`cvi_p`/
   `coverage_p`/`tier_p`) `cvi.store.queries.list_ranked`'s persona branch
   computes -- never the stored balanced ones, which can be `None` on a
   row that is balanced-unrated but persona-rated (`LeaderboardRow.cvi`/
   `.rank` are non-null). See :func:`_leaderboard_row`.
2. `balanced_rank`/`rank_delta` (contract addition, Review Blocker B-6,
   landed on `cvi-wt-W4-5a`'s `contract.py` and pulled in here) are the
   STORED balanced `rank` and its difference from the resolved `rank`
   above; both stay `None` under the balanced lens itself and when the
   balanced `rank` is itself `None` (nothing to compare against -- the
   contract's own validator requires both-or-neither).
3. `gate_reason == "unrated: no_external_workflow"` (`cvi.core.gates.
   REASON_NO_EXTERNAL_WORKFLOW`, a stage-6 GATE despite the "unrated:"
   spelling) is translated to the UNRATED group with
   `unrated_reason="no_external_workflow"` (the bare spelling
   `UnratedReason` already accepts) rather than widening `GateReason` --
   the lead's explicit ruling between the review's two offered options.
   See :func:`_classify_not_ranked`.
4. `unrated_reason="off_platform_routed"` is derived from the stored
   `routed == "off_platform"` column on an ELIGIBLE repo (nothing in the
   engine writes this string directly today). See :func:`_classify_not_ranked`.
5. `list_not_ranked`'s one flat `reason` field is split back into
   `gate_reason`/`unrated_reason`/`persona_gate_reason` per group (the
   group itself is the `eligible`/`routed`/`persona_gate_reason` triage in
   item 3/4 above plus D-PERS gating). See :func:`_split_not_ranked`.
6. Persona gate codes (`ai_hard_ban`, `elephant_factor_below_3`,
   `high_barrier_to_entry`, `criticality_below_threshold`,
   `insufficient_evidence:<input>`) pass through unchanged -- vocabulary
   unification across `copy.js`'s four spellings is a UI-layer concern
   (W4-5) outside this packet.
7. `sort` accepts the store's bare `rank`/`cvi`/`coverage`/`tier` AND
   `p_<pillar>` (translated to the bare pillar name `cvi.store.queries`
   expects), with an optional leading `-` for direction. See
   :func:`_translate_sort` for the one-sided limitation this uncovers:
   `cvi.store.queries._build_order_by` has no ASCENDING form at all, so a
   `-`-prefixed sort is accepted (never a spurious 422) but today orders
   identically to its bare form -- a `cvi.store.queries` gap, not
   something this module can fix without touching that file (out of
   scope), flagged in the handoff.
8. `page`/`page_size` accept any value `cvi.store.queries._validate_paging`
   accepts (`page_size` 1..1000, `page >= 1`) -- the dropdown's five
   values (20/50/100/500/1000) are a UI choice, never a server-side
   allow-list (lead ruling).
9. `snapshot_summary`'s dict matches `SnapshotSummary` field-for-field --
   passed straight through (`SnapshotSummary(**summary)`), no bridging.
10. `EvidenceRow.pillar` is guaranteed non-null by `cvi.core.constants_v2.
    PARAM_INPUT_IDS`/`cvi.core.params.PARAMS` being the closed set every
    row `cvi.store.writer` ever writes was drawn from; an evidence row
    whose `param_id` is somehow unknown to that registry (`pillar is
    None` in `queries.repo_evidence`'s output) is deliberately left to
    fail `EvidenceRow`'s non-null `Literal` -- a 500, per the lead's
    ruling, never silently coerced to a null the contract forbids.
11. `MatrixResponse.axis_coverage_caption` is left empty (`{}`): the lead's
    ruling picked "the contract carries the raw numbers, the UI renders
    the sentence" over "render the caption server-side" -- but the
    NUMBERS field (`axis_partial_count`/`axis_partial_share`,
    `cvi.store.queries.matrix_points` already returns them) does not
    exist on `MatrixResponse` yet. Handed to W4-5b in the handoff rather
    than added here (not this packet's call per the addendum).
12. `raw_hash` stays on `LeaderboardRow` (required there) AND is also on
    the new `RepoDetailResponse` (`cvi/serve/contract.py`, this packet's
    own addition) -- the lead's ruling was to MOVE it to the detail
    endpoint and drop it from the leaderboard row, but the already-shipped
    `tests/fixtures/ui/leaderboard*.json` fixtures (cherry-picked from
    `44b1d67`, unregenerated) still carry `raw_hash` on every row and
    `LeaderboardRow.raw_hash` is still a REQUIRED field on the contract
    this module imports -- dropping it here would fail this packet's own
    fixture/model parity test. Flagged in the handoff as the one bridge
    item only partially applied, pending a fixture regeneration this
    packet does not own.

Threading: every route is `async def`, matching `cvi.api`'s own stated
reasoning (its module docstring) -- a synchronous `sqlite3.Connection` is
thread-bound, so handlers run on the event-loop thread rather than being
offloaded to a worker thread, keeping every store access on the one thread
that owns the connection. `starlette.testclient.TestClient` dispatches
through an `anyio` portal thread distinct from the thread that calls
:func:`create_app`, so the v2 connection this module opens itself uses
`check_same_thread=False` (never `cvi.store.schema.connect`, which does
not expose that flag) rather than `tests/test_serve.py`'s
build-inside-the-portal workaround -- simpler, and safe here because
every access is still funnelled through the single-threaded async event
loop, never truly concurrent.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Mapping, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from cvi.audit.methodology_history import methodology_with_history
from cvi.core import constants_v2
from cvi.core.methodology import MethodologyDocument
from cvi.serve.contract import (
    DEFAULT_PAGE_SIZE,
    EvidenceRow,
    LeaderboardResponse,
    LeaderboardRow,
    MatrixPoint,
    MatrixResponse,
    NotRankedResponse,
    NotRankedRow,
    ReprojectRequest,
    ReprojectResponse,
    RepoDetailResponse,
    SnapshotSummary,
)
from cvi.store import queries, reader

__all__ = ["create_app"]

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_CHANGELOG_PATH: Path = _REPO_ROOT / "docs" / "v2" / "rfcs" / "CHANGELOG.md"

_PILLARS = constants_v2.PILLAR_NAMES

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
        return JSONResponse(status_code=422, content=_error_body(request, 422, str(exc)))


# --------------------------------------------------------------------------- #
# Snapshot resolution: this API always serves ONE snapshot -- the most
# recently PROMOTED one (`finalize_snapshot_hash` stamps `promoted_at`),
# falling back to the most recently created if none has been promoted yet
# (a database written but not yet finalised). No `?snapshot_id=` query
# param exists anywhere in this packet's endpoint list or `04` section 7 --
# a caller cannot ask for a different one.
# --------------------------------------------------------------------------- #


def _current_snapshot_id(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute(
        "SELECT id FROM snapshots WHERE promoted_at IS NOT NULL ORDER BY promoted_at DESC, id DESC LIMIT 1"
    ).fetchone()
    if row is not None:
        return row[0]
    row = conn.execute("SELECT id FROM snapshots ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
    return row[0] if row is not None else None


def _require_snapshot(conn: sqlite3.Connection) -> str:
    snapshot_id = _current_snapshot_id(conn)
    if snapshot_id is None:
        raise HTTPException(status_code=503, detail="no snapshot has been written to this database yet")
    return snapshot_id


# --------------------------------------------------------------------------- #
# Row projections (store dict -> contract model). Every field is named
# explicitly -- no `**record` passthrough anywhere -- so a store column
# this module does not recognise can never silently reach the wire, and a
# contract field this module forgets raises immediately (pydantic's
# required-field check), not a coincidental content bug months later.
# --------------------------------------------------------------------------- #


def _no_ceiling_or_band(coverage: float) -> Optional[str]:
    """`02` D-MISS-3's ceiling band as a LABEL (not the index
    `cvi.store.queries._ceiling_index_case_sql` builds for its own SQL
    `CASE`, which this module cannot import -- it is a private helper).
    Used ONLY by :func:`_reproject_row`: `queries.reproject` never
    recomputes `tier_ceiling` for the custom coverage it derives (its own
    docstring's `record["tier_ceiling"]` is left as the stale BALANCED
    value), and this packet's own dispatch does not ask `cvi.store.
    queries` to grow that column, so it is derived here instead from the
    same two frozen constants that module's private helper reads."""
    if coverage >= constants_v2.NO_CEILING_MIN_COVERAGE:
        return None
    if coverage >= constants_v2.CEILING_A_MIN_COVERAGE:
        return "A"
    return "B"


def _project_leaderboard_row(
    record: Mapping[str, Any],
    *,
    rank: Optional[int],
    tie_group: Optional[int],
    cvi: Optional[float],
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
        "cvi": cvi,
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
    `cvi.store.queries.list_ranked`'s persona branch computes, with
    `balanced_rank`/`rank_delta` filled in from the stored balanced `rank`
    (both stay `None` together when that stored rank is itself `None` --
    a row that is persona-rated but balanced-unrated has nothing to show a
    delta against)."""
    if persona == "balanced":
        return _project_leaderboard_row(
            record,
            rank=record["rank"],
            tie_group=record["tie_group"],
            cvi=record["cvi"],
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
        cvi=record["cvi_p"],
        coverage=record["coverage_p"],
        tier=record["tier_p"],
        tier_ceiling=record["tier_ceiling"],
        balanced_rank=balanced_rank,
        rank_delta=rank_delta,
    )


def _reproject_row(record: Mapping[str, Any]) -> LeaderboardRow:
    """`queries.reproject`'s row already carries the custom projection
    under the plain `rank`/`cvi`/`coverage`/`tier`/`tie_group` names (its
    own docstring: "reprojection IS the view being asked for") plus
    `balanced_rank`/`balanced_cvi`/`balanced_coverage`/`balanced_tier` for
    the comparison -- this module reuses `balanced_rank` for the SAME
    `rank_delta` semantics :func:`_leaderboard_row` gives a persona view,
    and recomputes `tier_ceiling` (see :func:`_no_ceiling_or_band`) since
    the row's own `tier_ceiling` column is the stale balanced value."""
    balanced_rank = record["balanced_rank"]
    rank = record["rank"]
    rank_delta = (balanced_rank - rank) if balanced_rank is not None else None
    return _project_leaderboard_row(
        record,
        rank=rank,
        tie_group=record["tie_group"],
        cvi=record["cvi"],
        coverage=record["coverage"],
        tier=record["tier"],
        tier_ceiling=_no_ceiling_or_band(record["coverage"]),
        balanced_rank=balanced_rank,
        rank_delta=rank_delta,
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
        "cvi": record["cvi"],
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
    `cvi.store.queries.list_not_ranked`'s own, plus the two translations
    the review flagged):

    1. Stage-0 ineligible (`eligible=0`): `"unrated: no_external_workflow"`
       (a GATE reason despite the spelling) becomes the UNRATED group with
       the bare `unrated_reason="no_external_workflow"`; every other
       `gate_reason` stays in the GATED group unchanged.
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
        if record["gate_reason"] == "unrated: no_external_workflow":
            return "unrated", "no_external_workflow"
        return "gated", record["gate_reason"]
    if persona != "balanced" and record.get("persona_gate_reason") is not None:
        return "persona_hidden", record["persona_gate_reason"]
    if record.get("routed") == "off_platform":
        return "unrated", "off_platform_routed"
    return "unrated", record["reason"]


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
    `cvi.store.queries.matrix_points` filters on `rated`, not on the
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
        cvi=point["cvi"],
        coverage=point["coverage"],
        tier=point["tier"],
    )


_SORT_BASE = ("rank", "cvi", "coverage", "tier")


def _translate_sort(sort: Optional[str]) -> Optional[str]:
    """Bridge item 7: the store's `ALLOWED_SORT_FIELDS` are bare
    (`rank`/`cvi`/`coverage`/`tier`/`<pillar>`); the API additionally
    accepts `p_<pillar>` (translated to `<pillar>`) and an optional
    leading `-` for direction (lead ruling). The `-` is accepted and
    stripped, never rejected -- but `cvi.store.queries._build_order_by`
    has no ascending form at all (every branch is hard-coded `DESC`), so
    today `-cvi` and `cvi` order identically; this is a `cvi.store.
    queries` gap this module cannot close without editing that file (out
    of this packet's scope), named in the handoff, not silently hidden."""
    if sort is None:
        return None
    raw = sort[1:] if sort.startswith("-") else sort
    if raw in _SORT_BASE:
        return raw
    if raw.startswith("p_") and raw[2:] in _PILLARS:
        return raw[2:]
    raise ValueError(
        f"sort must be one of {_SORT_BASE + tuple(f'p_{p}' for p in _PILLARS)} "
        f"(optionally prefixed with '-'); got {sort!r}"
    )


def _paging(page: int, page_size: int) -> tuple:
    return page_size, (page - 1) * page_size


def _require_snapshot_summary(conn: sqlite3.Connection, snapshot_id: str) -> dict:
    summary = queries.snapshot_summary(conn, snapshot_id)
    if summary is None:  # pragma: no cover - snapshot_id came from this same conn moments ago
        raise HTTPException(status_code=503, detail=f"snapshot {snapshot_id!r} disappeared mid-request")
    if summary["canonical_dump_hash"] is None:
        raise HTTPException(
            status_code=503,
            detail=f"snapshot {snapshot_id!r} has not been finalised (no canonical_dump_hash yet)",
        )
    return summary


# --------------------------------------------------------------------------- #
# Served-DB guard (rule 2): reuse `scripts.serve`'s existing v1 guard
# (`open_or_exit`, itself a thin wrapper this module asked `scripts/
# serve.py` to factor out of `main()` around the identical inline check --
# zero behavioural change for `main()`'s own callers/tests) rather than a
# second implementation. A database that has never held v1 data at all (no
# `cvi.lakehouse.OperationalStore`-shaped `repositories` columns, e.g. every
# database this packet's OWN tests build -- a fresh v2 schema names its
# `repositories` table's columns after `04-ARCHITECTURE.md` section 5, not
# v1's) cannot carry the specific v1 failures that guard checks for (a
# fixture-marked row, a tampered content hash -- both require the v1
# shape to even ask the question), and `OperationalStore.__init__` itself
# -- not just a later query -- raises `sqlite3.OperationalError` against
# one (its own schema script references v1-only columns unconditionally;
# confirmed directly: constructing one against a bare v2 schema fails at
# `_conn.executescript(_SCHEMA)`, before any guard check runs at all).
# There is therefore no `OperationalStore` to hand `/legacy` for such a
# database -- :func:`create_app` mounts nothing at `/legacy` rather than
# retry a construction already proven impossible. A genuine v1-shaped
# refusal still raises `SystemExit` with the exact message
# `scripts.serve.main` always has (that failure surfaces from
# `refusal_reasons`, deep inside a successfully-constructed store, never
# from construction itself, so it is never caught here).
# --------------------------------------------------------------------------- #


def _run_served_db_guard(db_path: str):
    """Returns the guarded v1 `OperationalStore` to mount at `/legacy`, or
    `None` when `db_path` has no v1 shape to guard or mount at all."""
    from scripts.serve import open_or_exit  # lazy: scripts.serve imports this module at its own top level

    try:
        return open_or_exit(db_path)
    except sqlite3.OperationalError:
        return None


class _SPAStaticFiles(StaticFiles):
    """`06` section 6: "index fallback for the app's own paths so URL state
    reloads work" -- U-9's URL state lives entirely in the query string of
    one page (`?persona=&eco=&q=&sort=&page=&repo=&tab=`), but a future
    path-based route or a bookmark to a sub-path must still resurface
    `index.html` rather than a static 404, standard SPA-hosting practice.
    API routes (`/v1/cvi/*`) and `/legacy/*` are registered on the app
    BEFORE this mount, so Starlette's first-match routing never reaches
    this class for them regardless."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


def _get_conn(request: Request) -> sqlite3.Connection:
    return request.app.state.conn


def _build_router() -> APIRouter:
    router = APIRouter(prefix="/v1/cvi")

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
        store_sort = _translate_sort(sort)
        limit, offset = _paging(page, page_size)
        rows, total = queries.list_ranked(
            conn, snapshot_id, persona=persona, ecosystem=eco, q=q, sort=store_sort, limit=limit, offset=offset
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
        # `NotRankedResponse` has no paging fields at all (contract, W4-3
        # review N-2/owner ruling: "this list is bounded and small enough
        # that this packet does not add server-side paging fields to it")
        # -- fetch up to the store's own hard cap in one page. A not-ranked
        # set larger than MAX_LIMIT would silently truncate here; flagged
        # in the handoff as a known limit of the CURRENT contract shape,
        # not something this module can lift on its own.
        rows, _counts_by_reason, _total = queries.list_not_ranked(
            conn, snapshot_id, persona=persona, ecosystem=eco, q=q, limit=queries.MAX_LIMIT, offset=0
        )
        gated, unrated, persona_hidden = _split_not_ranked(rows, persona=persona)
        summary = _require_snapshot_summary(conn, snapshot_id)
        return NotRankedResponse(gated=gated, unrated=unrated, persona_hidden=persona_hidden, counts=summary["counts"])

    @router.get("/matrix", response_model=MatrixResponse)
    async def get_matrix(
        conn: sqlite3.Connection = Depends(_get_conn),
        persona: str = Query("balanced"),
    ) -> MatrixResponse:
        snapshot_id = _require_snapshot(conn)
        result = queries.matrix_points(conn, snapshot_id, persona=persona)
        points: list[MatrixPoint] = []
        excluded = result["excluded_count"]
        for raw_point in result["points"]:
            point = _matrix_point(raw_point)
            if point is None:
                excluded += 1
            else:
                points.append(point)
        return MatrixResponse(points=points, excluded_count=excluded, axis_coverage_caption={})

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
        # `ReprojectRequest` has no paging fields (contract) -- `page`/
        # `page_size` are accepted as query params alongside the POST body,
        # same translation as `/leaderboard`, defaulting to one page of
        # `DEFAULT_PAGE_SIZE` (not this packet's endpoint list spelling out
        # a page/limit contract for this one; the leaderboard's own U-13
        # pattern is the closest precedent, applied here for consistency).
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
        return ReprojectResponse(rows=[_reproject_row(row) for row in rows], total=total)

    return router


def _open_v2_connection(db_path: str) -> sqlite3.Connection:
    """A plain `sqlite3.connect` rather than `cvi.store.schema.connect`:
    the latter does not expose `check_same_thread`, which this module
    needs for `TestClient`'s portal-thread dispatch (module docstring).
    `PRAGMA foreign_keys = ON` is replicated (not imported -- it is a
    connection SETTING, not a schema/query DEFINITION) to match
    `schema.connect`'s own behaviour for any future write this read-mostly
    API might grow."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_app(db_path, *, web_dir) -> FastAPI:
    """The CVI v2 API app factory (packet W4-4).

    `GET /v1/cvi/{leaderboard,not-ranked,matrix,snapshot,methodology}`,
    `GET /v1/cvi/repos/{owner}/{name}[/evidence]`, `POST /v1/cvi/reproject`
    -- every response built through `cvi.serve.contract`'s models. The
    v1 API (`cvi.api.create_app`) is mounted UNCHANGED at `/legacy/` (`04`
    section 10's strangler migration: v1 stays reachable until W4-1
    deletes it) whenever `db_path` has a v1 shape for it to read (a
    v2-only database, e.g. every one this packet's own tests build, mounts
    no `/legacy` at all -- see :func:`_run_served_db_guard`); `web_dir`
    (typically `web/v2/`) is mounted at `/` with an `index.html` SPA
    fallback. Route registration order is API router, then `/legacy` (if
    any), then the static mount -- Starlette matches in registration
    order and a `Mount("/")` registered first would swallow every other
    path.

    Raises `SystemExit` (uncaught, propagates to the caller -- `scripts/
    serve.py`'s `main` lets it exit the process, matching today's
    behaviour) if the served-DB guard refuses `db_path` (rule 2)."""
    db_path = str(db_path)
    legacy_store = _run_served_db_guard(db_path)

    conn = _open_v2_connection(db_path)

    app = FastAPI(title="CVI API v2")
    app.state.conn = conn

    _register_exception_handlers(app)
    app.include_router(_build_router())

    if legacy_store is not None:
        from cvi.api import create_app as _create_legacy_app

        app.mount("/legacy", _create_legacy_app(store=legacy_store))

    app.mount("/", _SPAStaticFiles(directory=str(web_dir), html=True, check_dir=False), name="web")

    return app
