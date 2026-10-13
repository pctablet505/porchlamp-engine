"""Porchlamp v2 SQL reprojection and read paths (`04` sections 5-7; `06` sections
3-4, 7; `02` sections 5, 7-10).

Every function here does its work in one SQL statement (plus, where a total
count is needed, one cheap `COUNT(*)`) -- no Python loop over rows, per `04`
section 6: "No Python loop over all rows for any endpoint." Everything a
caller needs for a repo's persona view is read straight from the columns
`porchlamp.store.writer` already wrote (``p_*``/``c_*`` pillar columns, the
``persona_composites`` JSON blob) -- nothing here recomputes a
:class:`~porchlamp.core.models.ScoreRecord`.

Two SQL rules this module implements:

- A tie group of exactly one row is reported as ``tie_group = NULL``
  ("nothing to share", matching ``porchlamp.core.tiebreak.assign_ranks``), not a
  lonely ``DENSE_RANK`` value no other row shares. :func:`_tie_group_sql`
  builds this once; every caller that needs a ``DENSE_RANK``-based tie group
  (the persona projection in :func:`list_ranked`, the custom-weight
  projection in :func:`reproject`) uses it, so the singleton-nulling rule
  has one definition.
- 04 section 6: a repo's rating under a persona is never the stored balanced
  ``rated`` flag -- it is recomputed from that persona's own weighted
  coverage. This module reads the *already computed* ``persona_composites``
  JSON (written by ``porchlamp.core.persona.persona_composite`` at score time)
  rather than recomputing the weighted pillar reduction in SQL for a
  *preset* persona -- :func:`reproject`'s custom weights are the one case
  that genuinely has no precomputed JSON to read, so it is the one function
  here that reimplements the D-MISS-2 renormalisation and D-MAT-2
  tier/ceiling logic directly in SQL.

Field-naming contract (importer's guide, since `06` section 4's row shape
does not by itself say what changes under a persona): every row this module
returns carries the *stored, balanced* fields under their own names
(``rank``, ``tie_group``, ``porchlamp``, ``coverage``, ``tier``, ``rated``) --
these are `06` section 4's row contract, verbatim, and are ``NULL``/``False``
exactly when the balanced record itself has no rank (gated or unrated).
:func:`list_ranked` and :func:`matrix_points` additionally attach
``porchlamp_p``/``coverage_p``/``tier_p``/``rated_p``/``rank_p``/``tie_group_p``
(the persona-projected figures, `04` section 6) whenever ``persona`` is not
``"balanced"``, so a caller can show the balanced score, the persona's own
score, and their delta, all at once (`06` U-4) without a second query.
:func:`reproject` -- U-11: "a custom weight vector ... is never called a
persona" -- returns the CUSTOM projection under the plain names
(``rank``/``porchlamp``/``coverage``/``tier``/``rated``: reprojection IS the view
being asked for) plus ``balanced_rank``/``balanced_porchlamp``/
``balanced_coverage``/``balanced_tier`` for the same side-by-side comparison
(`06` section 3 D: "live rank deltas in a side list").

Import-graph note (`04` section 3; enforced by
``tests/core/test_import_graph.py``): this module imports only ``porchlamp.core``
(enums, frozen constants, the pillar/param registries) plus the standard
library -- no ``porchlamp.collect``, ``porchlamp.derive`` or ``porchlamp.serve``. It does NOT
import ``porchlamp.core.persona``, and imports exactly TWO names from
``porchlamp.core.aggregate``: :func:`~porchlamp.core.aggregate.validate_pillar_weights`
(a pure, no-SQL, called-once-per-call validation function that
:func:`reproject`'s ``_validate_weights`` delegates to rather than carrying
a second, drifting copy of the non-negativity/sum check that
``porchlamp.core.persona``'s presets are validated by), and the constant
``porchlamp.core.aggregate.RATIO_DECIMALS`` (the canonical composite-rounding
precision, so ``reproject``'s score-tier CASE rounds a composite to the same
digits the core does before comparing it to a tier cutoff). Neither
``porchlamp.core.persona`` nor ``porchlamp.core.aggregate``'s WEIGHTED-REDUCTION
functions (``composite_score_raw``/``composite_coverage``) are imported:
those compute a :class:`~porchlamp.core.models.ScoreRecord`'s pillars from *raw
parameters*, which this module never sees (only the store's own
already-aggregated columns) -- pulling them in for the reduction itself
would be a second, unused definition of logic this module's SQL
reimplements at the column level for :func:`reproject` only, one time,
deliberately (the docstring above explains why that one function cannot
instead read a precomputed JSON value).

``tie_group``/``tie_group_p`` are an OPAQUE, EQUALITY-ONLY label within
one response: two rows in the SAME response sharing a non-``None``
``tie_group`` tie on the composite; the numeric VALUE itself carries no
meaning and is never comparable across two different calls or two
different lenses on the same call. SQL's ``DENSE_RANK()``
(:func:`_tie_group_sql`, used by the persona branch of :func:`list_ranked`
and by :func:`reproject`) numbers a new group for every distinct composite
value it visits, tied or not, while ``porchlamp.core.tiebreak.assign_ranks``' own
counter only advances for composite values that actually recur -- balanced
``list_ranked`` reads the STORED ``tie_group`` column (``assign_ranks``'
numbering) verbatim, so a persona row can carry a ``tie_group`` (balanced,
low ids) and a ``tie_group_p`` (DENSE_RANK, ids that can run into the
hundreds) side by side that never agree numerically yet both correctly
group the same set of tied rows. Compare group MEMBERSHIP, never the raw
id.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from porchlamp.core import constants
from porchlamp.core.aggregate import RATIO_DECIMALS, validate_pillar_weights
from porchlamp.core.gates import REASON_INSUFFICIENT_EVIDENCE, ROUTED_OFF_PLATFORM
from porchlamp.core.pipeline import DISPLAY_UNRATED_GATE_REASONS
from porchlamp.core.params import PARAMS

__all__ = [
    "ORDER_BY_SQL",
    "ALLOWED_SORT_FIELDS",
    "AXIS_CAPTION_MIN_MISSING_FRACTION",
    "CUSTOM_WEIGHT_LABEL",
    "REPROJECT_CANDIDATE_SQL",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "row_to_dict",
    "BOOL_COLUMNS",
    "JSON_OBJECT_COLUMNS",
    "JSON_LIST_COLUMNS",
    "list_ranked",
    "list_not_ranked",
    "snapshot_summary",
    "axis_coverage",
    "matrix_points",
    "repo_evidence",
    "reproject",
]

# --------------------------------------------------------------------------- #
# Frozen constants this module reads (never re-declared).
# --------------------------------------------------------------------------- #
_PILLAR_NAMES: Tuple[str, ...] = constants.PILLAR_NAMES
_PILLAR_SORT_FIELDS = set(_PILLAR_NAMES)
_TIER_ORDER: Tuple[str, ...] = constants.TIER_ORDER
_TIER_CUTOFFS: Tuple[Tuple[float, str], ...] = constants.TIER_CUTOFFS
_CEILING_A_MIN_COVERAGE: float = constants.CEILING_A_MIN_COVERAGE
_NO_CEILING_MIN_COVERAGE: float = constants.NO_CEILING_MIN_COVERAGE
_RATED_MIN_COVERAGE: float = constants.EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE
_WEIGHT_SUM_TOLERANCE: float = constants.WEIGHT_SUM_TOLERANCE
_PERSONA_PRESETS: Dict[str, Dict[str, float]] = constants.PERSONA_PRESETS

#: U-11: a custom weight vector is labelled "custom" everywhere it is
#: applied and is never called a persona.
CUSTOM_WEIGHT_LABEL: str = "custom"

#: The ONE definition of which stored rows a custom-weight reprojection may
#: rank: stage-0 eligible and not routed off-platform (D-GATE-6). GitHub cannot
#: see a routed repository's review, so its responsiveness cannot be compared
#: with anyone's, under any weights. :func:`reproject` filters with it and
#: ``scripts/export_static.py`` exports the rows the static site reprojects
#: with it, so the API and the static site cannot disagree on the set. Column
#: names are unqualified so it reads the same with or without a table alias;
#: ``routed`` is NULL for an unrouted row, hence ``IS NOT``, never ``!=``.
REPROJECT_CANDIDATE_SQL: str = f"eligible = 1 AND routed IS NOT '{ROUTED_OFF_PLATFORM}'"

#: `06` section 3 A / D-TIE-1: the only columns a leaderboard may sort on --
#: vanity fields (stars, forks, downloads) are never sortable (U-3).
ALLOWED_SORT_FIELDS: Tuple[str, ...] = ("rank", "porchlamp", "coverage", "tier") + _PILLAR_NAMES

#: `06` U-13 (paginated leaderboard): the page-size menu is 20
#: (default)/50/100/500/1000 -- 1000 is the largest a caller may request,
#: and the module owns this bound itself (`04` section 6: it already owns
#: paging determinism) rather than trusting SQLite's own "a negative LIMIT
#: means unlimited" behaviour.
DEFAULT_LIMIT: int = 20
MAX_LIMIT: int = 1000

# --------------------------------------------------------------------------- #
# Row decoding -- one definition; ``porchlamp.store.reader`` imports these back
# under its own private names.
# --------------------------------------------------------------------------- #
#: Columns stored as 0/1 that round-trip to Python ``bool``.
BOOL_COLUMNS: Tuple[str, ...] = ("eligible", "archived", "mirror", "fork", "rated")

#: Columns stored as canonical JSON text.
JSON_OBJECT_COLUMNS: Tuple[str, ...] = ("persona_gates", "persona_composites")
JSON_LIST_COLUMNS: Tuple[str, ...] = ("suspicion", "gate_undecided")


def row_to_dict(columns: List[str], row: tuple) -> Dict[str, Any]:
    record = dict(zip(columns, row))
    for column in BOOL_COLUMNS:
        if column in record and record[column] is not None:
            record[column] = bool(record[column])
    for column in JSON_OBJECT_COLUMNS + JSON_LIST_COLUMNS:
        if column in record and record[column] is not None:
            record[column] = json.loads(record[column])
    return record


def _fetch(conn: sqlite3.Connection, sql: str, params: Sequence[Any]) -> Tuple[List[str], List[tuple]]:
    cursor = conn.execute(sql, params)
    rows = cursor.fetchall()
    columns = [description[0] for description in cursor.description]
    return columns, rows


# --------------------------------------------------------------------------- #
# `04` section 6's amended D-TIE-1 ORDER BY: ``NULLS LAST`` on every column
# (SQLite sorts NULL first under plain ``ASC``, which is backwards for an
# unmeasured *ascending* tie level such as ``friction``).
# ``ORDER_BY_SQL`` is the balanced form (``porchlamp``/``coverage`` columns
# directly); :func:`_order_by_sql` generalises it to any composite/coverage
# EXPRESSION so the persona projection (:func:`list_ranked`) and the custom
# projection (:func:`reproject`) reuse the same levels 2-8 -- one definition
# of D-TIE-1's tail, never copy-pasted per caller.
# --------------------------------------------------------------------------- #
_TIE_TAIL_SQL = (
    "p_responsiveness DESC NULLS LAST, "
    "friction ASC NULLS LAST, "
    "leverage DESC NULLS LAST, "
    "ttfhr_p75 ASC NULLS LAST, "
    "last_human_commit_at DESC NULLS LAST, "
    "lower(owner || '/' || name) ASC"
)


def _order_by_sql(composite_expr: str, coverage_expr: str) -> str:
    """D-TIE-1's full 8-level order, levels 1-2 parameterised by whichever
    composite/coverage expression the caller's projection uses (stored
    ``porchlamp``/``coverage`` for balanced, ``porchlamp_p``/``coverage_p`` for a
    persona, the recomputed custom composite/coverage for
    :func:`reproject`); levels 3-8 (persona/weight-invariant: the
    responsiveness pillar score, friction, leverage, TTFHR p75, last commit,
    owner/name) are always :data:`_TIE_TAIL_SQL`."""
    return f"{composite_expr} DESC NULLS LAST, {coverage_expr} DESC NULLS LAST, {_TIE_TAIL_SQL}"


#: The balanced ordering (`04` section 6): public so a caller outside this
#: module (``porchlamp.store.reader``) can reuse the exact same clause instead of
#: a second, drifting copy.
ORDER_BY_SQL: str = _order_by_sql("ROUND(porchlamp, 2)", "coverage")


def _tier_index_sql(tier_expr: str) -> str:
    """``tier_expr`` (a column or expression holding a tier LABEL, e.g.
    ``'B'``) mapped to its :data:`_TIER_ORDER` position (worst=0) as a
    ``CASE`` expression, for sorting tier as a total order rather than as
    text (``'S+' < 'S'`` lexicographically, which is backwards). ``NULL``
    (unrated/gated -- no tier) maps to ``NULL`` under simple-``CASE``
    semantics with no ``ELSE``, so callers must still add ``NULLS LAST``
    where this is used in an ``ORDER BY``."""
    whens = " ".join(f"WHEN '{tier}' THEN {index}" for index, tier in enumerate(_TIER_ORDER))
    return f"(CASE {tier_expr} {whens} END)"


def _build_order_by(
    sort: Optional[str], composite_expr: str, coverage_expr: str, tier_expr: str, *, descending: bool = True
) -> str:
    """`06` section 3 A: sort only on rank, Porchlamp, coverage, tier or a pillar
    (:data:`ALLOWED_SORT_FIELDS`); anything else raises ``ValueError``
    naming the allowed set (U-3: vanity fields are never sortable, and are
    not even spellable here). ``sort=None``/``"rank"``/``"porchlamp"`` all give
    the plain D-TIE-1 order (rank IS that order's ``ROW_NUMBER``, and
    composite is already its first level); ``"coverage"``/``"tier"``/a
    pillar name promote that field ahead of the full D-TIE-1 chain, which
    still runs after it -- so the result is always a *total* order (offset
    paging is deterministic under every ``sort`` value, `04` section 6:
    "offset paging is deterministic because the ORDER BY is total").

    ``descending`` applies ONLY to the PROMOTED level (the column ``sort``
    names, or the composite itself when ``sort`` is
    ``None``/``"rank"``/``"porchlamp"``); the D-TIE-1 TAIL after it
    (:data:`_TIE_TAIL_SQL`, via ``default``) is never reversed -- reversing
    the tie-break levels too would make ``rank``/``tie_group`` (a FIXED
    fact about the visible set, D-TIE-2) depend on display direction.
    ``NULLS LAST`` stays on the ascending branch as well: an unmeasured
    value sorts last regardless of direction (D-TIE-1's rule is about
    missingness, not direction)."""
    default = _order_by_sql(composite_expr, coverage_expr)
    if sort is None or sort in ("rank", "porchlamp"):
        return default if descending else f"{composite_expr} ASC NULLS LAST, {default}"
    direction = "DESC" if descending else "ASC"
    if sort == "coverage":
        return f"{coverage_expr} {direction} NULLS LAST, {default}"
    if sort == "tier":
        return f"{_tier_index_sql(tier_expr)} {direction} NULLS LAST, {default}"
    if sort in _PILLAR_SORT_FIELDS:
        return f"p_{sort} {direction} NULLS LAST, {default}"
    raise ValueError(f"sort must be one of {ALLOWED_SORT_FIELDS} or None; got {sort!r}")


def _tie_group_sql(composite_expr: str) -> str:
    """``DENSE_RANK() OVER (ORDER BY ROUND(composite_expr, 2) DESC)``,
    ``NULL``'d out when that composite
    value's group has exactly one member
    (``COUNT(*) OVER (PARTITION BY ROUND(composite_expr, 2)) = 1``) -- a
    singleton has nothing to share a tie mark with, matching
    ``porchlamp.core.tiebreak.assign_ranks`` (``tie_group=None`` when
    ``composite_counts[composite] == 1``). Both window functions partition
    on the identical rounded expression, so a row's own group membership
    and its own count agree by construction."""
    return (
        f"(CASE WHEN COUNT(*) OVER (PARTITION BY ROUND({composite_expr}, 2)) = 1 THEN NULL "
        f"ELSE DENSE_RANK() OVER (ORDER BY ROUND({composite_expr}, 2) DESC) END)"
    )


def _validate_persona(persona: str) -> None:
    if persona not in _PERSONA_PRESETS:
        raise ValueError(f"unknown persona {persona!r}; expected one of {sorted(_PERSONA_PRESETS)}")


def _validate_paging(limit: int, offset: int) -> None:
    """`06` U-13: ``limit`` must be ``1..MAX_LIMIT`` (the page-size menu's
    largest option, 1000) and ``offset`` must be ``>= 0``. SQLite itself
    treats a negative ``LIMIT`` as "no limit" and silently floors a
    negative ``OFFSET`` to 0 -- both are surprises this module's own
    callers should get as a loud ``ValueError`` instead, naming the bound
    that was violated."""
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit must be an integer in 1..{MAX_LIMIT}; got {limit!r}")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError(f"offset must be a non-negative integer; got {offset!r}")


def _validate_weights(weights: Mapping[str, float]) -> None:
    """U-11 / `02` D-AGG-1: a reprojection weight vector must cover exactly
    the 5 frozen pillars, every weight must be non-negative, and they must
    sum to 1.0 within :data:`~porchlamp.core.constants.WEIGHT_SUM_TOLERANCE`.

    Delegates entirely to :func:`porchlamp.core.aggregate.validate_pillar_weights`
    -- one definition, the same one ``porchlamp.core.persona``'s preset
    validation uses. The sum check alone is not enough: a negative weight
    can still sum to exactly 1.0, offset by a larger positive one
    elsewhere (e.g. ``{responsiveness: -0.5, criticality: 1.5, rest:
    0.0}``), which the non-negativity check is what rejects."""
    validate_pillar_weights(weights)


def _search_filter(q: Optional[str]) -> Tuple[str, List[str]]:
    """`06` section 2: ``q`` matches owner/name case-insensitively. A plain
    substring test (``instr(lower(...), lower(?)) > 0``) rather than
    ``LIKE`` -- ``LIKE``'s ``%``/``_`` wildcards in a user-typed search
    string would otherwise need escaping to be treated as literal
    characters."""
    if not q:
        return "", []
    return (
        " AND (instr(lower(r.owner), lower(?)) > 0"
        " OR instr(lower(r.name), lower(?)) > 0"
        " OR instr(lower(r.owner || '/' || r.name), lower(?)) > 0)",
        [q, q, q],
    )


def _ecosystem_filter(ecosystem: Optional[str]) -> Tuple[str, List[str]]:
    if ecosystem is None:
        return "", []
    return " AND r.ecosystem = ?", [ecosystem]


# --------------------------------------------------------------------------- #
# Persona projection: `04` section 6's per-persona-rated ruling reads
# porchlamp_p/coverage_p/tier_p/rated_p straight from the
# stored ``persona_composites`` JSON (``porchlamp.core.persona.persona_composite``
# already computed and wrote them at score time) rather than recomputing the
# weighted pillar reduction here -- D-AGG-4's "linear ... no Python full
# scan" already happened once, at write time; this module must not
# duplicate it. The one exception is ``rank_p``/``tie_group_p``, which have
# no stored column at all (rank is an ORDER-dependent, cross-row fact) and
# so are exactly what this module computes.
# --------------------------------------------------------------------------- #
def _persona_paths(persona: str) -> Dict[str, str]:
    return {
        "gate": f"$.{persona}",
        "porchlamp": f"$.{persona}.porchlamp",
        "coverage": f"$.{persona}.coverage",
        "tier": f"$.{persona}.tier",
        "rated": f"$.{persona}.rated",
    }


def _persona_visible_where_sql(persona: str) -> Tuple[str, List[str]]:
    """`04` section 6 ruling: a row is visible under ``persona`` iff it is
    stage-0 eligible, not gated by that persona's own D-PERS-1/2/3 filter,
    and its stored persona composite exists and clears the D-MISS-3 rated
    threshold (0.60) under THAT persona's weighted coverage -- never the
    balanced ``rated`` flag (a repo rated under the balanced weights can be
    unrated under a persona's, and vice versa; the ``PersonaComposite``
    docstring). Shared by :func:`list_ranked`'s persona branch,
    :func:`list_not_ranked`'s complement, ``_count_persona_visible`` and
    :func:`matrix_points`."""
    paths = _persona_paths(persona)
    clause = (
        "r.eligible = 1 "
        "AND json_extract(r.persona_gates, ?) IS NULL "
        "AND json_extract(r.persona_composites, ?) IS NOT NULL "
        f"AND json_extract(r.persona_composites, ?) >= {_RATED_MIN_COVERAGE}"
    )
    return clause, [paths["gate"], paths["porchlamp"], paths["coverage"]]


def _count_persona_visible(conn: sqlite3.Connection, snapshot_id: str, persona: str) -> int:
    clause, clause_params = _persona_visible_where_sql(persona)
    sql = f"SELECT COUNT(*) FROM repositories r WHERE r.snapshot_id = ? AND {clause}"
    return conn.execute(sql, [snapshot_id, *clause_params]).fetchone()[0]


def list_ranked(
    conn: sqlite3.Connection,
    snapshot_id: str,
    *,
    persona: str = "balanced",
    ecosystem: Optional[str] = None,
    q: Optional[str] = None,
    sort: Optional[str] = None,
    descending: bool = True,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    """`04` section 6 / `06` section 3 A: the ranked leaderboard page.

    Returns ``(rows, total)`` -- ``total`` is the count of the FULL matching
    set (before ``limit``/``offset``), so a caller can page correctly.

    ``persona="balanced"`` (default): ``rank``/``tie_group``/``porchlamp``/
    ``coverage``/``tier``/``rated`` are the STORED balanced values, never
    recomputed; the ranked set is ``rated = 1``.

    Any other ``persona`` (one of :data:`~porchlamp.core.constants.PERSONA_PRESETS`,
    else ``ValueError``): the ranked set is
    :func:`_persona_visible_where_sql`; each row ADDITIONALLY carries
    ``porchlamp_p``/``coverage_p``/``tier_p``/``rated_p`` (read from the stored
    ``persona_composites`` JSON, never recomputed) and ``rank_p``/
    ``tie_group_p`` (computed here, per `04` section 6: ``ROW_NUMBER()``
    over the persona's own D-TIE-1 order for ``rank_p``,
    :func:`_tie_group_sql` -- singleton-nulling included -- for
    ``tie_group_p``). The plain ``rank``/``porchlamp``/``coverage``/``tier``/
    ``rated`` fields on such a row are still the balanced ones (possibly
    ``None``/``False`` if the repo is balanced-gated or -unrated: a repo can
    be persona-rated while balanced-unrated, `04` section 6's ruling, so
    this module does not require ``rated = 1`` in the persona branch).

    ``rated_p``: on any row this function actually RETURNS, ``rated_p`` is
    always ``True`` -- the visibility filter
    (:func:`_persona_visible_where_sql`) already requires
    ``coverage_p >= 0.60``, which is the exact same threshold
    ``porchlamp.core.persona.persona_composite`` applies when setting the STORED
    ``rated`` flag, so the two can never disagree on a row that clears the
    filter. The field is kept (not dropped) for shape symmetry with
    :class:`~porchlamp.core.models.PersonaComposite` (``porchlamp``, ``coverage``,
    ``tier``, ``rated``) and because it is read from real storage rather
    than hardcoded -- a forged ``persona_composites`` JSON with
    ``rated=False`` under a coverage that clears 0.60 (never produced by
    the real engine, but not physically impossible in the column) is still
    surfaced faithfully as ``False``, never silently overwritten to
    ``True``.

    ``ecosystem``: exact match. ``q``: case-insensitive owner/name substring
    (`06` section 2). ``sort``: :func:`_build_order_by` (``ValueError`` on
    an unknown value, naming :data:`ALLOWED_SORT_FIELDS` -- U-3, vanity
    fields are never sortable and are not even a valid input here). ``sort``
    changes PAGE ORDER only -- ``rank``/``tie_group`` (balanced, the stored
    columns) and ``rank_p``/``tie_group_p`` (persona, computed here) are
    D-TIE-2's fixed "strict 1..N after tie-breaking" fact about the visible
    set and never renumber under a different ``sort``.

    ``descending``: applies only to the PROMOTED sort level, never to
    ``rank``/``tie_group``/``rank_p``/``tie_group_p`` (still the fixed
    D-TIE-1/D-TIE-2 order regardless -- see :func:`_build_order_by`) nor to
    the D-TIE-1 tail after it, so offset paging stays deterministic under
    either direction.

    ``limit``: 1..1000 inclusive (`06` U-13's page-size menu tops out at
    1000); ``ValueError`` naming the max above
    that, or below 1. ``offset``: >= 0, ``ValueError`` otherwise. The
    module owns paging determinism (the ORDER BY is total, `04` section 6),
    so it owns this bound too rather than trusting SQLite's own
    negative-LIMIT-means-unlimited behaviour.
    """
    _validate_persona(persona)
    _validate_paging(limit, offset)
    eco_sql, eco_params = _ecosystem_filter(ecosystem)
    q_sql, q_params = _search_filter(q)
    extra_where = eco_sql + q_sql
    extra_params = [*eco_params, *q_params]

    if persona == "balanced":
        order_expr = _build_order_by(sort, "ROUND(porchlamp, 2)", "coverage", "tier", descending=descending)
        where = f"r.snapshot_id = ? AND r.rated = 1{extra_where}"
        params = [snapshot_id, *extra_params]

        total = conn.execute(f"SELECT COUNT(*) FROM repositories r WHERE {where}", params).fetchone()[0]
        sql = f"""
            SELECT r.*, s.benchmark_version AS benchmark_version
            FROM repositories r JOIN snapshots s ON r.snapshot_id = s.id
            WHERE {where}
            ORDER BY {order_expr}
            LIMIT ? OFFSET ?
        """
        columns, raw_rows = _fetch(conn, sql, [*params, limit, offset])
        return [row_to_dict(columns, row) for row in raw_rows], total

    paths = _persona_paths(persona)
    visible_clause, visible_params = _persona_visible_where_sql(persona)
    base_where = f"r.snapshot_id = ? AND {visible_clause}"
    base_params = [snapshot_id, *visible_params]
    proj_select_params = [paths["porchlamp"], paths["coverage"], paths["tier"], paths["rated"], *base_params]

    count_where = f"r.snapshot_id = ? AND {visible_clause}{extra_where}"
    count_params = [snapshot_id, *visible_params, *extra_params]
    total = conn.execute(f"SELECT COUNT(*) FROM repositories r WHERE {count_where}", count_params).fetchone()[0]

    rank_order_expr = _order_by_sql("ROUND(porchlamp_p, 2)", "coverage_p")
    display_order_expr = _build_order_by(sort, "ROUND(porchlamp_p, 2)", "coverage_p", "tier_p", descending=descending)

    slim_select = f"""
        SELECT r.id, r.owner, r.name,
            r.p_responsiveness, r.p_criticality, r.p_governance, r.p_rigor, r.p_accessibility,
            r.friction, r.leverage, r.ttfhr_p75, r.last_human_commit_at,
            json_extract(r.persona_composites, ?) AS porchlamp_p,
            json_extract(r.persona_composites, ?) AS coverage_p,
            json_extract(r.persona_composites, ?) AS tier_p,
            json_extract(r.persona_composites, ?) AS rated_p
        FROM repositories r
        WHERE {base_where}
    """
    sql = f"""
        WITH ranked AS (
            SELECT id, porchlamp_p, coverage_p, tier_p, rated_p,
                ROW_NUMBER() OVER (ORDER BY {rank_order_expr}) AS rank_p,
                {_tie_group_sql("porchlamp_p")} AS tie_group_p
            FROM ({slim_select})
        )
        SELECT r.*, s.benchmark_version AS benchmark_version,
            rk.porchlamp_p, rk.coverage_p, rk.tier_p, rk.rated_p,
            rk.rank_p, rk.tie_group_p
        FROM ranked rk
        JOIN repositories r ON rk.id = r.id
        JOIN snapshots s ON r.snapshot_id = s.id
        WHERE 1=1{extra_where}
        ORDER BY {display_order_expr}
        LIMIT ? OFFSET ?
    """
    columns, raw_rows = _fetch(conn, sql, [*proj_select_params, *extra_params, limit, offset])
    rows = []
    for raw in raw_rows:
        record = row_to_dict(columns, raw)
        record["rated_p"] = bool(record["rated_p"]) if record.get("rated_p") is not None else False
        rows.append(record)
    return rows, total


def list_not_ranked(
    conn: sqlite3.Connection,
    snapshot_id: str,
    *,
    persona: str = "balanced",
    ecosystem: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], Dict[str, int], int]:
    """`06` section 3 A2 / D-TIE-2: the "Not ranked" tab -- every row
    :func:`list_ranked` excludes at ``snapshot_id``, each with the reason it
    is missing, ordered by lowercase ``owner/name``. Returns ``(rows,
    counts, total)``: ``counts`` tallies EVERY matching row by reason
    (never just the current page -- `06` section 8 acceptance item 2:
    "Gated and unrated counts are visible ... and match the Not-ranked
    tab", which the badge on the Ranked tab needs regardless of which page
    of the Not-ranked tab is open); ``total`` is the same full-set count
    :func:`list_ranked` returns, so ``sum(counts.values()) == total``
    always.

    ``limit``/``offset`` (`06` U-13): 1..``MAX_LIMIT``
    and >= 0 respectively (``_validate_paging``, ``ValueError`` otherwise),
    applied to ``rows`` only -- ``counts``/``total`` are always over the
    FULL not-ranked set. Implemented by paging the already-fetched,
    already-ordered Python list rather than a second SQL query: this
    table's own rows are the MINORITY of a snapshot's universe (gated +
    unrated repos), and fetching every one of them measured 1.92ms p95 on
    a 10,000-row synthetic snapshot even unpaginated -- restructuring the
    two branches below into a SQL-level LIMIT/OFFSET plus a separate
    GROUP-BY-reason COUNT would not measurably help and would risk the
    rows/counts disagreeing (the two would then be two queries, not one
    filtered list read twice).

    ``persona="balanced"``: two reasons -- stage-0 gated (``eligible=0``,
    reason = ``gate_reason``) and balanced-unrated (``eligible=1,
    rated=0``, reason = ``unrated_reason``). Exactly the complement of
    :func:`list_ranked`'s ``rated = 1`` set.

    Any other ``persona``: the exact complement of :func:`list_ranked`'s
    persona branch (``_persona_visible_where_sql`` negated) -- stage-0
    gated (as above), persona-gated (`02` D-PERS-1/2/3: reason = that
    persona's own ``persona_gates`` entry), routed off-platform (D-GATE-6:
    eligible with ``routed == ROUTED_OFF_PLATFORM``, which stage 8 gives no
    persona composites or gates -- reason = its stored ``unrated_reason``,
    ``"unrated: off_platform_routed"``, the same reason as under the balanced
    lens), or persona-unrated (eligible, not persona-gated, not routed, but
    ``porchlamp_p`` is null or ``coverage_p`` is below 0.60 under this
    persona's weights -- reason =
    ``porchlamp.core.gates.REASON_INSUFFICIENT_EVIDENCE``, the same string the
    balanced path uses for the same underlying fact, generalised to a
    persona's own coverage).
    """
    _validate_paging(limit, offset)
    eco_sql, eco_params = _ecosystem_filter(ecosystem)
    q_sql, q_params = _search_filter(q)
    extra_where = eco_sql + q_sql
    extra_params = [*eco_params, *q_params]

    if persona == "balanced":
        sql = f"""
            SELECT r.*, s.benchmark_version AS benchmark_version
            FROM repositories r JOIN snapshots s ON r.snapshot_id = s.id
            WHERE r.snapshot_id = ? AND (r.eligible = 0 OR r.rated = 0){extra_where}
            ORDER BY lower(r.owner || '/' || r.name) ASC
        """
        columns, raw_rows = _fetch(conn, sql, [snapshot_id, *extra_params])
        all_rows = []
        counts: Dict[str, int] = {}
        for raw in raw_rows:
            record = row_to_dict(columns, raw)
            reason = record["gate_reason"] if not record["eligible"] else record["unrated_reason"]
            record["reason"] = reason
            record["rank"] = None
            record["tie_group"] = None
            all_rows.append(record)
            counts[reason] = counts.get(reason, 0) + 1
        return all_rows[offset : offset + limit], counts, len(all_rows)

    _validate_persona(persona)
    paths = _persona_paths(persona)
    sql = f"""
        SELECT r.*, s.benchmark_version AS benchmark_version,
            json_extract(r.persona_gates, ?) AS persona_gate_reason,
            json_extract(r.persona_composites, ?) AS porchlamp_p,
            json_extract(r.persona_composites, ?) AS coverage_p
        FROM repositories r JOIN snapshots s ON r.snapshot_id = s.id
        WHERE r.snapshot_id = ?
          AND (r.eligible = 0
               OR json_extract(r.persona_gates, ?) IS NOT NULL
               OR json_extract(r.persona_composites, ?) IS NULL
               OR json_extract(r.persona_composites, ?) < {_RATED_MIN_COVERAGE}){extra_where}
        ORDER BY lower(r.owner || '/' || r.name) ASC
    """
    params = [
        paths["gate"], paths["porchlamp"], paths["coverage"],
        snapshot_id,
        paths["gate"], paths["porchlamp"], paths["coverage"],
        *extra_params,
    ]
    columns, raw_rows = _fetch(conn, sql, params)
    all_rows = []
    counts: Dict[str, int] = {}
    for raw in raw_rows:
        record = row_to_dict(columns, raw)
        if not record["eligible"]:
            reason = record["gate_reason"]
        elif record["routed"] == ROUTED_OFF_PLATFORM:
            reason = record["unrated_reason"]
        elif record["persona_gate_reason"] is not None:
            reason = record["persona_gate_reason"]
        else:
            reason = REASON_INSUFFICIENT_EVIDENCE
        record["reason"] = reason
        record["rank"] = None
        record["tie_group"] = None
        all_rows.append(record)
        counts[reason] = counts.get(reason, 0) + 1
    return all_rows[offset : offset + limit], counts, len(all_rows)


def snapshot_summary(conn: sqlite3.Connection, snapshot_id: str) -> Optional[Dict[str, Any]]:
    """`04` section 7 / `06` section 3 F: the ``GET /v1/porchlamp/snapshot``
    manifest summary. ``None`` if ``snapshot_id`` does not exist.

    ``counts``: balanced rated/gated/unrated/total (exhaustive and mutually
    exclusive: ``rated`` is ``eligible=1 AND rated=1``; ``gated`` is
    stage-0 ``eligible=0`` MINUS any row whose ``gate_reason`` is in
    ``porchlamp.core.pipeline.DISPLAY_UNRATED_GATE_REASONS`` (U-2: ``06``
    section 3 A2 classifies "no external
    workflow" as an Unrated evidence outcome, not a Gated
    disqualification, even though the ENGINE gates it at stage 0 --
    :func:`porchlamp.core.pipeline.not_ranked_class` is the same rule applied
    per-record by the scoring pipeline and by :mod:`porchlamp.serve.app`'s
    not-ranked split, so this count agrees with both); ``unrated`` is the
    remainder, which therefore already absorbs those reclassified rows.
    Note this module never divides by a stale cached count: nothing here
    reads the balanced counts and the not-ranked-listing counts from
    different queries then hopes they agree -- ``snapshot_summary``
    recomputes both from ``repositories`` directly, every call.

    ``persona_visible_counts``: for every preset in
    ``porchlamp.core.constants.PERSONA_PRESETS`` (``"balanced"`` included, so
    the balanced view's own visible count -- ``counts["rated"]`` -- is
    present under its own key too), the count :func:`list_ranked` would
    return for that persona with no other filter.

    ``detector_summary``: count per distinct suspicion flag string actually
    present in this snapshot's ``suspicion`` JSON arrays
    (``json_each`` -- no fixed name list is imported here: `03`'s detector
    names live in ``porchlamp.verify``, outside this module's import allow-list,
    04 section 3; a flag this snapshot never raised simply does not
    appear, which is a correct summary of the DATA, not a stale catalogue).

    ``reproduce_command``: the exact invocation named in
    ``porchlamp/snapshot/cli.py``'s own module docstring, filled in with this
    snapshot's id.
    """
    snap_row = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    if snap_row is None:
        return None
    snap_columns = [d[0] for d in conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).description]
    snapshot = dict(zip(snap_columns, snap_row))

    total = conn.execute(
        "SELECT COUNT(*) FROM repositories WHERE snapshot_id = ?", (snapshot_id,)
    ).fetchone()[0]
    unrated_gate_placeholders = ", ".join("?" for _ in DISPLAY_UNRATED_GATE_REASONS)
    gated = conn.execute(
        f"SELECT COUNT(*) FROM repositories WHERE snapshot_id = ? AND eligible = 0 "
        f"AND gate_reason NOT IN ({unrated_gate_placeholders})",
        (snapshot_id, *DISPLAY_UNRATED_GATE_REASONS),
    ).fetchone()[0]
    rated = conn.execute(
        "SELECT COUNT(*) FROM repositories WHERE snapshot_id = ? AND eligible = 1 AND rated = 1", (snapshot_id,)
    ).fetchone()[0]
    unrated = total - gated - rated

    persona_visible_counts: Dict[str, int] = {}
    for persona in sorted(_PERSONA_PRESETS):
        persona_visible_counts[persona] = rated if persona == "balanced" else _count_persona_visible(
            conn, snapshot_id, persona
        )

    detector_rows = conn.execute(
        """
        SELECT je.value AS flag, COUNT(*) AS n
        FROM repositories r, json_each(r.suspicion) je
        WHERE r.snapshot_id = ?
        GROUP BY je.value
        ORDER BY je.value
        """,
        (snapshot_id,),
    ).fetchall()
    detector_summary = dict(detector_rows)

    return {
        "snapshot_id": snapshot["id"],
        "as_of": snapshot["as_of"],
        "benchmark_version": snapshot["benchmark_version"],
        "constants_hash": snapshot["constants_hash"],
        "manifest_root": snapshot["manifest_root"],
        "code_commit": snapshot["code_commit"],
        "canonical_dump_hash": snapshot["canonical_dump_hash"],
        "counts": {"rated": rated, "gated": gated, "unrated": unrated, "total": total},
        "persona_visible_counts": persona_visible_counts,
        "detector_summary": detector_summary,
        "reproduce_command": f"python -m porchlamp.snapshot reproduce {snapshot_id}",
    }


#: Minimum fraction of plotted rows missing an axis-contributing pillar before
#: `axis_coverage` reports that axis's worst-missing pillar (`06` §3 B).
AXIS_CAPTION_MIN_MISSING_FRACTION: float = 0.05


def axis_coverage(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """One entry per axis (``leverage``/``friction``) whose worst-missing
    contributing pillar is unmeasured for :data:`AXIS_CAPTION_MIN_MISSING_FRACTION`
    or more of ``rows``."""
    coverage: Dict[str, Dict[str, Any]] = {}
    n = len(rows)
    if n == 0:
        return coverage
    for axis, weights in (
        ("leverage", constants.LEVERAGE_WEIGHTS),
        ("friction", constants.FRICTION_WEIGHTS),
    ):
        worst_pillar = None
        worst_count = 0
        worst_fraction = 0.0
        for pillar in weights:
            missing = sum(1 for r in rows if r.get(f"p_{pillar}") is None)
            fraction = missing / n
            if fraction > worst_fraction:
                worst_fraction, worst_pillar, worst_count = fraction, pillar, missing
        if worst_pillar is not None and worst_fraction >= AXIS_CAPTION_MIN_MISSING_FRACTION:
            coverage[axis] = {
                "pillar": worst_pillar,
                "axis_partial_count": worst_count,
                "axis_partial_share": worst_fraction,
            }
    return coverage


def matrix_points(
    conn: sqlite3.Connection, snapshot_id: str, *, persona: str = "balanced"
) -> Dict[str, Any]:
    """`04` section 7 / `06` section 3 B: the friction/leverage scatter.

    Rated rows only (balanced ``rated = 1``, or `04` section 6's
    persona-visible predicate under any other ``persona``): ``owner``,
    ``name``, ``ecosystem``, ``leverage``, ``friction``,
    ``leverage_coverage``, ``friction_coverage``, ``quadrant``,
    ``stars_audited`` (``NULL``: no v2 producer, `04` section 5) are always
    the STORED, persona-invariant values (D-MAT-1's
    coordinates are computed from the balanced pillar renormalisation only;
    no design decision reprojects them per persona). ``porchlamp``/``coverage``/
    ``tier`` follow ``persona`` exactly as :func:`list_ranked` does.

    Returns ``{"points": [...], "excluded_count": N, "axis_partial_count":
    N, "axis_partial_share": frac, "axis_coverage": {...}}``."""
    _validate_persona(persona)
    total = conn.execute(
        "SELECT COUNT(*) FROM repositories WHERE snapshot_id = ?", (snapshot_id,)
    ).fetchone()[0]

    if persona == "balanced":
        where = "r.snapshot_id = ? AND r.rated = 1"
        params: List[Any] = [snapshot_id]
        select_extra = "r.porchlamp AS porchlamp, r.coverage AS coverage, r.tier AS tier"
        select_extra_params: List[Any] = []
    else:
        paths = _persona_paths(persona)
        visible_clause, visible_params = _persona_visible_where_sql(persona)
        where = f"r.snapshot_id = ? AND {visible_clause}"
        params = [snapshot_id, *visible_params]
        select_extra = (
            "json_extract(r.persona_composites, ?) AS porchlamp, "
            "json_extract(r.persona_composites, ?) AS coverage, "
            "json_extract(r.persona_composites, ?) AS tier"
        )
        select_extra_params = [paths["porchlamp"], paths["coverage"], paths["tier"]]

    sql = f"""
        SELECT r.owner, r.name, r.ecosystem, r.leverage, r.friction,
               r.leverage_coverage, r.friction_coverage, r.quadrant, r.stars_audited,
               r.p_responsiveness, r.p_criticality, r.p_governance, r.p_rigor, r.p_accessibility,
               {select_extra}
        FROM repositories r
        WHERE {where}
    """
    columns, raw_rows = _fetch(conn, sql, [*select_extra_params, *params])
    points = [dict(zip(columns, row)) for row in raw_rows]

    partial = sum(
        1
        for point in points
        if (point["friction_coverage"] or 0.0) < 1.0 or (point["leverage_coverage"] or 0.0) < 1.0
    )
    share = partial / len(points) if points else 0.0
    plottable = [
        point
        for point in points
        if point["quadrant"] is not None and point["leverage"] is not None and point["friction"] is not None
    ]

    return {
        "points": points,
        "excluded_count": total - len(points),
        "axis_partial_count": partial,
        "axis_partial_share": share,
        "axis_coverage": axis_coverage(plottable),
    }


def repo_evidence(
    conn: sqlite3.Connection, snapshot_id: str, owner: str, name: str
) -> Optional[Dict[str, Any]]:
    """`04` section 7 / `06` section 3 C: ``GET
    /v1/porchlamp/repos/{owner}/{name}/evidence``. ``None`` if the repo does not
    exist at ``snapshot_id``.

    ``{"repo": <record row>, "params": [<evidence row>, ...]}``. Each
    evidence row (06 section 4's shape):
    ``param_id``, ``pillar`` (looked up from
    ``porchlamp.core.params.PARAMS[param_id].pillar`` -- the ``params`` table
    itself carries no pillar column), ``value`` (the decoded raw value),
    ``score``, ``measured``, ``n``, ``window_start``, ``window_end``,
    ``method``, ``source`` (the envelope path), ``source_sha256``, ``
    fetched_at``, ``unmeasured_reason`` (``NULL`` when measured)."""
    repo_cursor = conn.execute(
        """
        SELECT r.*, s.benchmark_version AS benchmark_version
        FROM repositories r JOIN snapshots s ON r.snapshot_id = s.id
        WHERE r.snapshot_id = ? AND r.owner = ? AND r.name = ?
        """,
        (snapshot_id, owner, name),
    )
    repo_row = repo_cursor.fetchone()
    if repo_row is None:
        return None
    repo_columns = [d[0] for d in repo_cursor.description]
    repo = row_to_dict(repo_columns, repo_row)

    param_cursor = conn.execute(
        """
        SELECT p.* FROM params p
        JOIN repositories r ON p.repo_id = r.id
        WHERE r.snapshot_id = ? AND r.owner = ? AND r.name = ?
        ORDER BY p.param_id
        """,
        (snapshot_id, owner, name),
    )
    param_columns = [d[0] for d in param_cursor.description]
    params = []
    for raw in param_cursor.fetchall():
        p = dict(zip(param_columns, raw))
        defn = PARAMS.get(p["param_id"])
        params.append(
            {
                "param_id": p["param_id"],
                "pillar": defn.pillar if defn is not None else None,
                "value": json.loads(p["raw_value"]) if p["raw_value"] is not None else None,
                "score": p["score"],
                "measured": bool(p["measured"]),
                "n": p["n"],
                "window_start": p["window_start"],
                "window_end": p["window_end"],
                "method": p["method"],
                "source": p["source"],
                "source_sha256": p["source_hash"],
                "fetched_at": p["fetched_at"],
                "unmeasured_reason": p["reason"],
            }
        )
    return {"repo": repo, "params": params}


def _tier_by_score_case_sql(score_expr: str) -> str:
    """`02` D-MAT-2 tier cutoffs (``constants.TIER_CUTOFFS``, best to
    worst already), mapped to each label's :data:`_TIER_ORDER` position --
    the score-only tier, before the D-MISS-3 coverage ceiling is applied.
    ``score_expr`` must be the composite rounded to ``RATIO_DECIMALS`` -- the
    same rounding the canonical core applies inside ``composite_score_raw``
    before ``tier_from_score`` -- so a composite that is mathematically exactly
    a cutoff but accumulates to a float one ULP below it tiers the same way
    here as in the core. It must never be the 2-decimal display value (`04`
    section 8: tier-cutoff comparisons never use the rounded display value)."""
    whens = " ".join(f"WHEN {score_expr} >= {cutoff!r} THEN {_TIER_ORDER.index(label)}" for cutoff, label in _TIER_CUTOFFS)
    floor_index = _TIER_ORDER.index("D")
    return f"(CASE {whens} ELSE {floor_index} END)"


def _ceiling_index_case_sql(coverage_expr: str) -> str:
    """`02` D-MISS-3 ceiling bands as a :data:`_TIER_ORDER` position: ``>=
    0.95`` -> no cap (the best position, so ``MIN`` with the score-tier
    index never lowers it); ``[0.80, 0.95)`` -> ceiling A; below that (this
    expression is only ever evaluated on a row already known to have
    ``coverage_expr >= 0.60``, the rated threshold) -> ceiling B."""
    no_ceiling_index = _TIER_ORDER.index("S+")
    ceiling_a_index = _TIER_ORDER.index("A")
    ceiling_b_index = _TIER_ORDER.index("B")
    return (
        f"(CASE WHEN {coverage_expr} >= {_NO_CEILING_MIN_COVERAGE!r} THEN {no_ceiling_index} "
        f"WHEN {coverage_expr} >= {_CEILING_A_MIN_COVERAGE!r} THEN {ceiling_a_index} "
        f"ELSE {ceiling_b_index} END)"
    )


def _tier_label_case_sql(index_expr: str) -> str:
    whens = " ".join(f"WHEN {index} THEN '{tier}'" for index, tier in enumerate(_TIER_ORDER))
    return f"(CASE {index_expr} {whens} END)"


def reproject(
    conn: sqlite3.Connection,
    snapshot_id: str,
    weights: Mapping[str, float],
    *,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> Tuple[List[Dict[str, Any]], int]:
    """`04` section 7 / `06` section 3 D: ``POST /v1/porchlamp/reproject``, the
    ONE view this module cannot read from a precomputed JSON column --
    ``weights`` is arbitrary (U-11: "custom", never a persona), so there is
    no stored composite for it anywhere. Recomputes, entirely in SQL, from
    the stored ``p_*``/``c_*`` pillar columns:

    - composite (`02` D-MISS-2 renormalisation): ``Sum(w_p * p_p) / Sum(w_p)``
      over pillars with ``c_p > 0`` (measured) only -- a pillar with
      ``c_p = 0`` (``p_p`` is then always ``NULL``) contributes to neither
      the numerator nor the denominator, so its weight does not silently
      dilute the average of the pillars that ARE measured.
    - coverage: ``Sum(w_p * c_p)`` over ALL 5 pillars (D-MISS-3's ``C``,
      generalised to ``weights`` -- an unmeasured pillar's own coverage is
      already 0.0, so it needs no special case here, unlike the composite).
    - rated iff coverage >= 0.60 (``EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE``).
    - tier: ``MIN(score-tier, ceiling)`` by :data:`_TIER_ORDER` position
      (`02` D-MAT-2), the score-tier from the composite rounded to
      ``RATIO_DECIMALS`` (`_tier_by_score_case_sql`, matching the core's
      ``composite_score_raw`` rounding) and the ceiling from coverage
      (`_ceiling_index_case_sql`) -- `04` section 8's rule that ceiling/tier
      comparisons never use the rounded display value.
    - rank/tie_group: D-TIE-1 with this custom composite/coverage at levels
      1-2 (:func:`_order_by_sql`), :func:`_tie_group_sql`'s singleton rule
      for ``tie_group``.

    ``weights`` is validated (:func:`_validate_weights`: exactly the 5
    pillar names, sums to 1.0 within
    ``constants.WEIGHT_SUM_TOLERANCE`` -- ``ValueError`` otherwise).

    Rows carry the custom projection under the plain field names
    (``rank``/``porchlamp``/``coverage``/``tier``/``rated``/``tie_group`` -- this
    IS the requested view) plus ``balanced_rank``/``balanced_porchlamp``/
    ``balanced_coverage``/``balanced_tier`` (the stored, unchanged balanced
    values) for the side-by-side comparison `06` section 3 D asks for
    ("live rank deltas in a side list"). ``lens`` is always
    :data:`CUSTOM_WEIGHT_LABEL` (U-11: never a persona name).

    Only eligible (stage-0, persona-independent) repos not routed off-platform
    are candidates (:data:`REPROJECT_CANDIDATE_SQL`); gate status itself never
    changes under a reprojection (D-PERS-1's separation, generalised: a
    custom weight vector reweights pillars, it does not un-gate a mirror or
    an archived repo), and neither does routing (D-GATE-6): a repository
    whose review GitHub cannot see is ranked under no weights.

    ``limit``/``offset``: same 1..``MAX_LIMIT``/``>= 0`` bound as
    :func:`list_ranked` (:func:`_validate_paging`, `06` U-13).
    """
    _validate_weights(weights)
    _validate_paging(limit, offset)

    num_terms = []
    den_terms = []
    cov_terms = []
    num_params: List[float] = []
    den_params: List[float] = []
    cov_params: List[float] = []
    for pillar in _PILLAR_NAMES:
        weight = weights[pillar]
        # A pillar counts as "measured" (D-MISS-2) only when BOTH its
        # coverage is positive AND its score is non-NULL -- in real writer
        # output the two always agree (``porchlamp.core.aggregate.aggregate_pillars``
        # sets ``coverage=0.0`` exactly when ``score is None``), but this SQL
        # does not assume that invariant: without the second condition a
        # NULL score under a positive coverage would poison the whole
        # numerator to NULL (SQL NULL propagates through ``+``), silently
        # dropping every repo whose data violates it instead of just
        # excluding that one pillar.
        num_terms.append(f"(CASE WHEN c_{pillar} > 0 AND p_{pillar} IS NOT NULL THEN ? * p_{pillar} ELSE 0 END)")
        num_params.append(weight)
        den_terms.append(f"(CASE WHEN c_{pillar} > 0 AND p_{pillar} IS NOT NULL THEN ? ELSE 0 END)")
        den_params.append(weight)
        cov_terms.append(f"(? * COALESCE(c_{pillar}, 0))")
        cov_params.append(weight)
    total_weight = float(sum(weights.values()))
    composite_raw_sql = f"(({' + '.join(num_terms)}) / NULLIF({' + '.join(den_terms)}, 0))"
    coverage_sql = f"(({' + '.join(cov_terms)}) / ?)"
    cov_params.append(total_weight)
    proj_params = [*num_params, *den_params, *cov_params]

    proj_sql = f"""
        SELECT r.id, r.owner, r.name,
            r.p_responsiveness, r.friction, r.leverage, r.ttfhr_p75, r.last_human_commit_at,
            {composite_raw_sql} AS composite_raw,
            {coverage_sql} AS coverage_custom
        FROM repositories r
        WHERE r.snapshot_id = ? AND {REPROJECT_CANDIDATE_SQL}
    """
    proj_full_params = [*proj_params, snapshot_id]

    visible_sql = f"""
        SELECT *,
            {_tier_by_score_case_sql(f"ROUND(composite_raw, {RATIO_DECIMALS})")} AS score_tier_index,
            {_ceiling_index_case_sql("coverage_custom")} AS ceiling_index
        FROM ({proj_sql})
        WHERE composite_raw IS NOT NULL AND coverage_custom >= {_RATED_MIN_COVERAGE}
    """

    total = conn.execute(f"SELECT COUNT(*) FROM ({visible_sql})", proj_full_params).fetchone()[0]

    porchlamp_display_sql = "ROUND(composite_raw, 2)"
    order_expr = _order_by_sql(porchlamp_display_sql, "coverage_custom")
    tier_label = _tier_label_case_sql("MIN(score_tier_index, ceiling_index)")
    sql = f"""
        WITH ranked AS (
            SELECT id, composite_raw,
                {porchlamp_display_sql} AS porchlamp_display,
                {tier_label} AS tier_custom,
                coverage_custom,
                ROW_NUMBER() OVER (ORDER BY {order_expr}) AS rank_custom,
                {_tie_group_sql("composite_raw")} AS tie_group_custom
            FROM ({visible_sql})
            ORDER BY {order_expr}
            LIMIT ? OFFSET ?
        )
        SELECT r.*, s.benchmark_version AS benchmark_version,
            rk.porchlamp_display, rk.tier_custom, rk.coverage_custom,
            rk.rank_custom, rk.tie_group_custom
        FROM ranked rk
        JOIN repositories r ON rk.id = r.id
        JOIN snapshots s ON r.snapshot_id = s.id
        ORDER BY {order_expr}
    """
    columns, raw_rows = _fetch(conn, sql, [*proj_full_params, limit, offset])

    rows = []
    for raw in raw_rows:
        record = row_to_dict(columns, raw)
        record["balanced_rank"] = record.pop("rank")
        record["balanced_tie_group"] = record.pop("tie_group")
        record["balanced_porchlamp"] = record.pop("porchlamp")
        record["balanced_coverage"] = record.pop("coverage")
        record["balanced_tier"] = record.pop("tier")
        record.pop("composite_raw", None)
        record["porchlamp"] = record.pop("porchlamp_display")
        record["coverage"] = record.pop("coverage_custom")
        record["tier"] = record.pop("tier_custom")
        record["rated"] = True
        record["rank"] = record.pop("rank_custom")
        record["tie_group"] = record.pop("tie_group_custom")
        record.pop("score_tier_index", None)
        record.pop("ceiling_index", None)
        record["lens"] = CUSTOM_WEIGHT_LABEL
        rows.append(record)
    return rows, total
