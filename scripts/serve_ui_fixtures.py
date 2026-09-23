"""Serve `web/v2/` plus the v2 API surface from static JSON fixtures.

This is a stdlib-only (`http.server`, no FastAPI, no third-party deps) stand-in so
`web/v2/` can be built and exercised against the wire contract in
`porchlamp/serve/contract.py`. Filtering, sorting and paging
happen here in Python over the fixture files under `tests/fixtures/ui/` — the
UI's query parameters are genuinely exercised, not just echoed.

Endpoints (all under `/v1/porchlamp/`, matching `docs/v2/04-ARCHITECTURE.md` §7 and
`docs/v2/06-UX-DESIGN.md` §4):

    GET /v1/porchlamp/leaderboard?persona=&eco=&q=&sort=&page=&page_size=
    GET /v1/porchlamp/not-ranked
    GET /v1/porchlamp/repos/{owner}/{name}/evidence
    GET /v1/porchlamp/snapshot
    GET /healthz

Everything else is served as a static file from `web/v2/` (default `/` ->
`index.html`).

Query parameter semantics (fixture-server contract):

- `persona`: one of `porchlamp.serve.contract.PERSONAS`, default `balanced`.
  `tests/fixtures/ui/leaderboard-first_timer.json` is a genuinely
  re-projected fixture (different ranks) and is served verbatim for
  `persona=first_timer`. The other three non-balanced personas have no
  dedicated re-projected fixture yet, so they fall back to the balanced
  ordering with only that persona's *gated* rows removed — a real
  re-projection is a server engine concern (SQL `reproject`, `04` §6), not this
  fixture stub's.
- `eco`: exact match against `ecosystem` (case-insensitive), unfiltered when
  omitted or empty.
- `q`: case-insensitive substring match against `"owner/name"`.
- `sort`: one of `rank, porchlamp, coverage, tier, p_responsiveness,
  p_criticality, p_governance, p_rigor, p_accessibility` (the columns Screen
  A allows sorting on — U-3 vanity columns are never sortable, and this
  server rejects any other value with 400). A leading `-` reverses the
  field's natural "best first" direction. Default `rank` (ascending — rank 1
  first). `tier` sorts by tier ordinal S > A > B > C > D > F. This encoding
  is mirrored exactly by `web/v2/components/table.js`'s `sortDirectionLabel`
  (the header's `aria-sort` label) and `web/v2/app.js`'s click-to-toggle
  handler — all three must agree on which fields default to descending vs
  ascending.
- `page`, `page_size`: 1-based page, `page_size` one of
  `porchlamp.serve.contract.LEADERBOARD_PAGE_SIZES` (20, 50, 100, 500, 1000;
  default 20). `total` and `counts` always reflect the full filtered set / the
  whole snapshot respectively — `counts` (rated/gated/unrated) never changes
  with filtering (Screen A requirement: the counts beside the Ranked/Not-ranked
  sub-tabs never disappear on filter).

Run:
    PYTHONPATH=$PWD .venv/bin/python scripts/serve_ui_fixtures.py --port 8899
    PYTHONPATH=$PWD .venv/bin/python scripts/serve_ui_fixtures.py --port 0   # OS-assigned free port; printed on start

Additional endpoints (Matrix/Configure/Methodology screens):

    GET  /v1/porchlamp/matrix
    GET  /v1/porchlamp/methodology
    POST /v1/porchlamp/reproject

`GET /v1/porchlamp/matrix` builds `MatrixPoint`s straight from the loaded
`leaderboard.json` rows (every rated row already carries its own
`leverage`/`friction`/`quadrant`/coverage fields — no recomputation from
raw pillar scores is needed); `excluded_count` is the snapshot's `gated`
count (`06` §3 B: "gated repos are not plotted; a caption states how many
are excluded" — unrated rows carry no coordinate fields at all in the
`NotRankedRow` contract, so they were never plottable to begin with, and
the doc's own caption wording only ever names gated repos).
`axis_coverage` (pillar id, missing count, missing share per axis --
never pre-rendered prose) is computed by :func:`axis_coverage` below;
`web/v2/copy.js` composes the caption sentence client-side from those numbers.

`GET /v1/porchlamp/methodology` serves `tests/fixtures/ui/methodology.json`
verbatim. That file is the genuine output of
`porchlamp.audit.methodology_history.methodology_with_history()`.
:func:`FixtureStore._load_methodology` re-reads and re-validates the
file every time a `FixtureStore` is constructed (once per process start,
matching every other fixture here), so a `methodology.json` that drifted
from the contract fails loudly at startup rather than silently serving a
stale shape.

`POST /v1/porchlamp/reproject` mirrors `porchlamp.store.queries.reproject`'s semantics
(D-MISS-2 renormalisation, D-MAT-2 tier/ceiling, D-TIE-1 ordering) in pure
Python over the SAME `leaderboard.json` rows the balanced leaderboard
reads — see :func:`reproject_rows`'s own docstring for the one documented
gap (two D-TIE-1 levels the API row contract does not carry at all).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import sys
import urllib.parse
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pydantic import ValidationError

from porchlamp.serve.contract import (
    DEFAULT_PAGE_SIZE,
    LEADERBOARD_PAGE_SIZES,
    PILLARS,
    ReprojectRequest,
    RepoDetailResponse,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WEB_DIR = REPO_ROOT / "web" / "v2"
DEFAULT_FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "ui"

#: Screen A: sort only on rank, Porchlamp, coverage, tier, pillars (U-3: vanity
#: columns — stars, forks, downloads — carry no sort affordance at all).
SORTABLE_FIELDS = {
    "rank", "porchlamp", "coverage", "tier",
    "p_responsiveness", "p_criticality", "p_governance", "p_rigor", "p_accessibility",
}
#: 20/50/100/500/1000, default 20 — defined
#: once in porchlamp/serve/contract.py, imported here so the fixture server and
#: the real API can never drift apart on the allowed set.
ALLOWED_PAGE_SIZES = LEADERBOARD_PAGE_SIZES
TIER_ORDER = {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4, "F": 5}

# --------------------------------------------------------------------------- #
# Matrix (B) — D-MAT-1 axis weights, read here as plain literals               #
# (this fixture server never imports porchlamp.core, per its own module docstring)  #
# and cited against porchlamp/core/constants.py's LEVERAGE_WEIGHTS/                #
# FRICTION_WEIGHTS so a constants change is a visible two-file diff, not a    #
# silent drift.                                                               #
# --------------------------------------------------------------------------- #
LEVERAGE_AXIS_WEIGHTS = {"criticality": 0.50, "rigor": 0.30, "governance": 0.20}
FRICTION_AXIS_WEIGHTS = {"responsiveness": 0.50, "governance": 0.30, "accessibility": 0.20}

#: "many" (06 §3 B: "friction computed without accessibility for 12%") is
#: not itself pinned to a number by 04/06 — this fixture stub's own
#: documented choice: an axis-contributing pillar unmeasured for >= 5% of
#: the plotted rows earns a caption naming it.
AXIS_CAPTION_MIN_MISSING_FRACTION = 0.05


def matrix_points_for(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`MatrixPoint` rows (`porchlamp.serve.contract.MatrixPoint`) straight from
    already-computed leaderboard row fields — every rated row already
    carries its own D-MAT-1 coordinates, so this never recomputes them from
    raw pillar scores."""
    return [
        {
            "owner": r["owner"],
            "name": r["name"],
            "ecosystem": r["ecosystem"],
            "leverage": r["leverage"],
            "friction": r["friction"],
            "leverage_coverage": r["leverage_coverage"],
            "friction_coverage": r["friction_coverage"],
            "quadrant": r["quadrant"],
            "stars_audited": r.get("stars_audited"),
            "porchlamp": r["porchlamp"],
            "coverage": r["coverage"],
            "tier": r["tier"],
        }
        for r in rows
        if r.get("quadrant") is not None  # a row whose coordinates are wholly unmeasured cannot be plotted
    ]


def axis_coverage(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """One entry per axis (`leverage`/`friction`) whose worst-missing
    contributing pillar is unmeasured for
    :data:`AXIS_CAPTION_MIN_MISSING_FRACTION` or more of `rows` --
    `porchlamp.serve.contract.MatrixAxisCoverage`-shaped dicts (`pillar`,
    `axis_partial_count`, `axis_partial_share`). The API sends raw numbers,
    never prose (06 §6: sentences belong in copy.js) -- `copy.js`'s
    `matrixAxisCoverageCaption` now does the composing, client-side,
    from exactly these three fields.
    Empty when every contributing pillar is well-measured on every axis."""
    coverage: dict[str, dict[str, Any]] = {}
    n = len(rows)
    if n == 0:
        return coverage
    for axis, weights in (("leverage", LEVERAGE_AXIS_WEIGHTS), ("friction", FRICTION_AXIS_WEIGHTS)):
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


# --------------------------------------------------------------------------- #
# Configure weights (D) — a pure-Python mirror of                             #
# porchlamp.store.queries.reproject's semantics (D-MISS-2/D-MAT-2/D-TIE-1) over     #
# the SAME fixture rows the balanced leaderboard reads. Constants below are   #
# transcribed from porchlamp/core/constants.py and cited, not imported (this        #
# fixture server is test-only and never imports porchlamp.core — module docstring)  #
# --------------------------------------------------------------------------- #
#: `constants.EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE`.
RATED_MIN_COVERAGE = 0.60
#: `constants.CEILING_A_MIN_COVERAGE` / `NO_CEILING_MIN_COVERAGE`.
CEILING_A_MIN_COVERAGE = 0.80
NO_CEILING_MIN_COVERAGE = 0.95
#: `constants.TIER_CUTOFFS`, worst to best; below the last is the floor "D".
TIER_CUTOFFS: tuple[tuple[float, str], ...] = ((90.0, "S+"), (80.0, "S"), (70.0, "A"), (55.0, "B"), (40.0, "C"))
#: `constants.TIER_ORDER` (worst to best; index compares two tier labels).
FULL_TIER_ORDER: tuple[str, ...] = ("D", "C", "B", "A", "S", "S+")


def _tier_by_score(score: float) -> str:
    for cutoff, label in TIER_CUTOFFS:
        if score >= cutoff:
            return label
    return "D"


def _ceiling_for_coverage(coverage: float) -> str:
    if coverage >= NO_CEILING_MIN_COVERAGE:
        return "S+"  # no ceiling: this is the BEST index, so min() below never lowers the score-tier
    if coverage >= CEILING_A_MIN_COVERAGE:
        return "A"
    return "B"


def reproject_rows(rows: list[dict[str, Any]], weights: dict[str, float]) -> list[dict[str, Any]]:
    """Pure-Python mirror of `porchlamp.store.queries.reproject`'s semantics over
    already-loaded leaderboard rows (each already carries its `p_*`/`c_*`
    pillar columns).

    - D-MISS-2 renormalisation: `composite = Sum(w*p for measured pillars) /
      Sum(w for measured pillars)`; a pillar counts as measured when its
      coverage is positive AND its score is not null (mirrors
      `porchlamp.store.queries.reproject`'s own defensive double check).
    - Coverage: `Sum(w*c)` over all five pillars.
      A row is dropped entirely when no pillar is measured (composite is
      undefined, matching the SQL's `composite_raw IS NOT NULL` filter) or
      when coverage falls below :data:`RATED_MIN_COVERAGE`.
    - Tier: `min(score_tier, ceiling)` by :data:`FULL_TIER_ORDER` position,
      the score-tier from the UNROUNDED composite.
    - Order (D-TIE-1): composite desc, coverage desc, then the tail levels
      the API row contract actually carries — `p_responsiveness` desc,
      `friction` asc, `leverage` desc, `owner/name` asc. **Levels 6 (TTFHR
      p75) and 7 (last non-bot commit date) are skipped** — `06` §4 never
      sends those two fields to the client at all (the same documented gap
      `web/v2/components/table.js::decidingTieLevel` has for the client-side
      tie-tooltip derivation); two rows that would only be separated by one
      of those two levels in the real engine tie here instead and fall
      through to owner/name.
    - `tie_group`: a DENSE_RANK-equivalent id over the sorted, ROUNDED
      composite (a new id for every distinct value visited, tied or not),
      `None`'d for a singleton group (R-D ruling, `porchlamp.store.queries`).
    - `balanced_rank`/`balanced_tie_group`/`balanced_porchlamp`/`balanced_coverage`/
      `balanced_tier`: the row's ORIGINAL (pre-reprojection) values, carried
      forward unchanged — the side-by-side comparison Screen D's rank-delta
      list needs.
    """
    projected: list[dict[str, Any]] = []
    for row in rows:
        num = den = cov = 0.0
        for pillar in PILLARS:
            w = weights[pillar]
            p = row.get(f"p_{pillar}")
            c = row.get(f"c_{pillar}") or 0.0
            cov += w * c
            if c > 0 and p is not None:
                num += w * p
                den += w
        if den <= 0.0:
            continue  # composite undefined -- excluded, matches the SQL's WHERE composite_raw IS NOT NULL
        composite_raw = num / den
        if cov < RATED_MIN_COVERAGE:
            continue
        porchlamp_display = round(composite_raw, 2)
        ceiling = _ceiling_for_coverage(cov)
        tier = min(_tier_by_score(composite_raw), ceiling, key=FULL_TIER_ORDER.index)
        # `tier_ceiling` (06 §4 / LeaderboardRow docstring): None once coverage
        # clears the no-ceiling band ("S+" here means "no cap"), else the
        # binding ceiling label itself -- recomputed for THIS projection's
        # own coverage, never copied from the row's balanced tier_ceiling.
        tier_ceiling = None if ceiling == "S+" else ceiling
        projected.append(
            {
                **row,
                "balanced_rank": row["rank"],
                "balanced_tie_group": row["tie_group"],
                "balanced_porchlamp": row["porchlamp"],
                "balanced_coverage": row["coverage"],
                "balanced_tier": row["tier"],
                "porchlamp": porchlamp_display,
                "coverage": round(cov, 6),
                "tier": tier,
                "tier_ceiling": tier_ceiling,
            }
        )

    def sort_key(r: dict[str, Any]):
        return (
            -r["porchlamp"],
            -r["coverage"],
            -(r["p_responsiveness"] if r["p_responsiveness"] is not None else float("-inf")),
            r["friction"] if r["friction"] is not None else float("inf"),
            -(r["leverage"] if r["leverage"] is not None else float("-inf")),
            f"{r['owner']}/{r['name']}".lower(),
        )

    projected.sort(key=sort_key)

    counts = Counter(r["porchlamp"] for r in projected)
    out: list[dict[str, Any]] = []
    current_group = 0
    last_porchlamp = object()  # sentinel: never equal to a float, so row 0 always starts a new group
    for rank, r in enumerate(projected, start=1):
        r = dict(r)
        r["rank"] = rank
        if r["porchlamp"] != last_porchlamp:
            current_group += 1
            last_porchlamp = r["porchlamp"]
        r["tie_group"] = current_group if counts[r["porchlamp"]] > 1 else None
        # `LeaderboardRow` validates `rank_delta == balanced_rank - rank` whenever
        # `balanced_rank` is set -- a reprojected row always carries its
        # prior balanced rank, so `rank_delta` is always set here too.
        r["rank_delta"] = r["balanced_rank"] - rank
        out.append(r)
    return out


def _synthetic_raw_hash(owner: str, name: str) -> str:
    """A deterministic, clearly-synthetic stand-in for a gated/
    unrated repo's `raw_hash` -- real per-repo data (`repositories.
    raw_hash TEXT NOT NULL`, computed at ingestion before gating even
    happens) that `tests/fixtures/ui/notranked.json`'s sparser
    `NotRankedRow`-shaped rows never carried, because nothing needed it
    until `RepoDetailResponse` required it on every repo. Deterministic
    (same owner/name always yields the same value) so a fixture-vs-fixture
    diff across runs is never spurious; namespaced with a `"fixture-"`
    prefix in the pre-image so nobody mistakes this for a real hash of
    anything -- it hashes nothing but the repo's own identity."""
    return hashlib.sha256(f"fixture-raw-hash:{owner}/{name}".encode()).hexdigest()


class FixtureStore:
    """Loads `tests/fixtures/ui/*.json` once and answers the v2 endpoints."""

    def __init__(self, fixtures_dir: Path) -> None:
        self.dir = fixtures_dir
        self.leaderboard: dict[str, Any] = self._load("leaderboard.json")
        self.leaderboard_first_timer: dict[str, Any] = self._load("leaderboard-first_timer.json")
        self.notranked: dict[str, Any] = self._load("notranked.json")
        self.notranked_by_persona: dict[str, dict[str, Any]] = {"balanced": self.notranked}
        first_timer_notranked = self.dir / "notranked-first_timer.json"
        if first_timer_notranked.exists():
            self.notranked_by_persona["first_timer"] = json.loads(first_timer_notranked.read_text())
        self.snapshot: dict[str, Any] = self._load("snapshot.json")
        #: Screen F: the methodology document.
        self.methodology: dict[str, Any] = self._load("methodology.json")
        self.evidence: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for path in sorted(self.dir.glob("evidence-*.json")):
            owner, name = self._parse_evidence_filename(path.name)
            self.evidence[(owner, name)] = json.loads(path.read_text())

    def _load(self, name: str) -> dict[str, Any]:
        path = self.dir / name
        if not path.exists():
            raise SystemExit(f"missing fixture: {path}")
        return json.loads(path.read_text())

    @staticmethod
    def _parse_evidence_filename(filename: str) -> tuple[str, str]:
        # "evidence-<owner>__<name>.json" — double underscore, unambiguous
        # because neither a GitHub owner nor a repo name may contain one.
        stem = filename[len("evidence-"):-len(".json")]
        owner, sep, name = stem.partition("__")
        if not sep:
            raise ValueError(
                f"evidence fixture filename {filename!r} has no '__' owner/name separator"
            )
        return owner, name

    def rows_for_persona(self, persona: str) -> list[dict[str, Any]]:
        if persona == "first_timer":
            return list(self.leaderboard_first_timer["rows"])
        rows = [dict(r) for r in self.leaderboard["rows"]]
        if persona != "balanced":
            rows = [r for r in rows if persona not in r.get("persona_gates", {})]
        return rows

    def notranked_for_persona(self, persona: str) -> dict[str, Any]:
        # Only "balanced" and "first_timer" have a dedicated fixture (the
        # other three non-balanced personas fall back to "balanced" with an
        # empty persona_hidden group — same fixture-stub limitation as
        # rows_for_persona above).
        return self.notranked_by_persona.get(persona, self.notranked)

    def find_repo_detail(self, owner: str, name: str) -> dict[str, Any] | None:
        """`GET /v1/porchlamp/repos/{owner}/{name}` -- the same record regardless
        of which screen asks for it: balanced leaderboard rows first (rated),
        then the balanced not-ranked lists (gated/unrated), `None` if the repo
        is in neither (a genuine 404).

        Returns the flat `RepoDetailResponse` shape matching the contract.
        `kind` is derived by the client from `eligible`/`rated`
        (`views/drawer.js::classifyKind`)."""
        for row in self.leaderboard["rows"]:
            if row["owner"] == owner and row["name"] == name:
                return self._repo_detail_from_rated_row(row)
        for row in self.notranked["gated"]:
            if row["owner"] == owner and row["name"] == name:
                return self._repo_detail_from_not_ranked_row(row, eligible=False)
        for row in self.notranked["unrated"]:
            if row["owner"] == owner and row["name"] == name:
                return self._repo_detail_from_not_ranked_row(row, eligible=True)
        return None

    def _repo_detail_from_rated_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """A rated row (`tests/fixtures/ui/leaderboard.json`, `LeaderboardRow`-
        shaped) already carries every fact `RepoDetailResponse` needs
        except the four eligibility/rating fields, which are simple,
        non-derived constants for anything that reached the leaderboard at
        all: eligible (passed every stage-0 gate), rated (cleared the
        coverage floor), and so carries neither a gate nor an unrated
        reason. `balanced_rank`/`rank_delta` are dropped: `LeaderboardRow`-
        only fields (the persona-delta column) `RepoDetailResponse` has no
        room for (`StrictModel`'s `extra="forbid"` -- confirmed by diffing
        `RepoDetailResponse.model_fields` against `LeaderboardRow.model_
        fields` rather than guessed)."""
        fields = {k: v for k, v in row.items() if k not in ("balanced_rank", "rank_delta")}
        fields["eligible"] = True
        fields["rated"] = True
        fields["gate_reason"] = None
        fields["unrated_reason"] = None
        return RepoDetailResponse(**fields).model_dump()

    def _repo_detail_from_not_ranked_row(self, row: dict[str, Any], *, eligible: bool) -> dict[str, Any]:
        """A gated/unrated row (`tests/fixtures/ui/notranked.json`) is a
        `NotRankedRow` -- much sparser than `RepoDetailResponse` requires,
        matching the real store's own asymmetry (`porchlamp/store/schema.py`:
        every `repositories` column always exists per row, but only a
        RATED row ever has `rank`/`porchlamp`/pillar scores/etc. computed at
        all -- a gated/unrated repo genuinely has `NULL` for every one of
        them, per `RepoDetailResponse`'s own docstring). `eligible` comes
        from WHICH not-ranked group this row was found in (the real
        store's own rule, `porchlamp.store.queries.list_not_ranked`'s docstring:
        "Gated (eligible=0) and unrated (eligible=1, rated=0)"), never
        guessed per-row. `coverage` defaults to 0.0 when this fixture
        row's own is `None` (`repositories.coverage` is NOT NULL in the
        real store -- "reportable even at 0.0", `RepoDetailResponse`'s own
        docstring). The one field genuinely absent from this fixture data
        with no real source at all is `raw_hash` (per-repo in the real
        store, computed at ingestion before gating -- `NotRankedRow` never
        carried it because nothing needed it until this endpoint): a
        deterministic synthetic placeholder, clearly namespaced, stands in
        for it here -- this whole store is already synthetic test data
        (see `tests/fixtures/ui/leaderboard.json`'s own `raw_hash` values)."""
        fields: dict[str, Any] = {
            "owner": row["owner"],
            "name": row["name"],
            "ecosystem": row["ecosystem"],
            "snapshot_id": self.snapshot["snapshot_id"],
            "benchmark_version": self.snapshot["benchmark_version"],
            "raw_hash": _synthetic_raw_hash(row["owner"], row["name"]),
            "eligible": eligible,
            "gate_reason": row.get("gate_reason"),
            "rated": False,
            "unrated_reason": row.get("unrated_reason"),
            "rank": None,
            "tie_group": None,
            "porchlamp": None,
            "coverage": row["coverage"] if row.get("coverage") is not None else 0.0,
            "tier": None,
            "tier_ceiling": None,
            "quadrant": None,
            "leverage": None,
            "friction": None,
            "leverage_coverage": None,
            "friction_coverage": None,
            "persona_gates": {},
            "persona_composites": {},
            "suspicion": [],
            "stars_audited": None,
        }
        for pillar in PILLARS:
            fields[f"p_{pillar}"] = None
            fields[f"c_{pillar}"] = None
        return RepoDetailResponse(**fields).model_dump()

    def leaderboard_page(
        self, *, persona: str, eco: str | None, q: str | None, sort: str, page: int, page_size: int
    ) -> dict[str, Any]:
        rows = self.rows_for_persona(persona)
        if eco:
            eco_lower = eco.lower()
            rows = [r for r in rows if r["ecosystem"].lower() == eco_lower]
        if q:
            q_lower = q.lower()
            rows = [r for r in rows if q_lower in f"{r['owner']}/{r['name']}".lower()]

        field = sort[1:] if sort.startswith("-") else sort
        reverse_flag = sort.startswith("-")
        if field not in SORTABLE_FIELDS:
            raise ValueError(f"unsortable field {field!r}; allowed: {sorted(SORTABLE_FIELDS)}")

        def sort_key(row: dict[str, Any]):
            if field == "tier":
                value = TIER_ORDER.get(row.get("tier") or "F", 5)
            else:
                value = row.get(field)
                if value is None:
                    value = float("inf") if field == "rank" else float("-inf")
            return value

        # "Best first" defaults: rank/tier ascending (rank 1, tier S first),
        # every score/coverage field descending (higher is better).
        best_first_ascending = field in ("rank", "tier")
        rows.sort(key=sort_key, reverse=(not best_first_ascending) != reverse_flag)

        total = len(rows)
        start = (page - 1) * page_size
        page_rows = rows[start:start + page_size]
        return {
            "rows": page_rows,
            "total": total,
            "counts": self.leaderboard["counts"],
            "page": page,
            "page_size": page_size,
            "persona": persona,
            "ecosystem": eco or None,
        }


def _query_params(path: str) -> tuple[str, dict[str, str]]:
    parsed = urllib.parse.urlsplit(path)
    params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
    return parsed.path, params


def make_handler(
    store: FixtureStore, web_dir: Path, fail_endpoints: frozenset[str] = frozenset()
) -> type[BaseHTTPRequestHandler]:
    """`fail_endpoints`: endpoint names (currently only "snapshot" is wired)
    that unconditionally return 500 — lets a test or a manual UI check
    exercise U-15's error-state requirement for `GET /v1/porchlamp/snapshot`."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "PorchlampUIFixtureServer/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:  # quieter test runs
            pass

        def _send_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_error_json(self, status: int, message: str) -> None:
            # The API's error body is `{"error": {endpoint, status, detail}}`,
            # not a bare string -- this fixture server mirrors that shape
            # exactly so `api.js`'s error handling is exercised against the
            # real contract. `endpoint` is the request path with any query
            # string stripped (matching how `web/v2/api.js`'s `ApiError`
            # names an endpoint).
            endpoint = self.path.split("?", 1)[0]
            self._send_json(status, {"error": {"endpoint": endpoint, "status": status, "detail": message}})

        def _serve_static(self, path: str) -> None:
            rel = path.lstrip("/") or "index.html"
            candidate = (web_dir / rel).resolve()
            try:
                candidate.relative_to(web_dir.resolve())
            except ValueError:
                self._send_error_json(403, "forbidden")
                return
            if candidate.is_dir():
                candidate = candidate / "index.html"
            if not candidate.is_file():
                self._send_error_json(404, f"not found: {path}")
                return
            content_type, _ = mimetypes.guess_type(str(candidate))
            if candidate.suffix == ".mjs":
                content_type = "text/javascript"
            body = candidate.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path, params = _query_params(self.path)

            if path == "/healthz":
                self._send_json(200, {"ok": True})
                return

            if path == "/v1/porchlamp/leaderboard":
                try:
                    page = int(params.get("page", "1"))
                    page_size = int(params.get("page_size", str(DEFAULT_PAGE_SIZE)))
                except ValueError:
                    self._send_error_json(400, "page and page_size must be integers")
                    return
                if page < 1:
                    self._send_error_json(400, "page must be >= 1")
                    return
                if page_size not in ALLOWED_PAGE_SIZES:
                    self._send_error_json(
                        400, f"page_size must be one of {ALLOWED_PAGE_SIZES}, got {page_size}"
                    )
                    return
                persona = params.get("persona", "balanced")
                try:
                    result = store.leaderboard_page(
                        persona=persona,
                        eco=params.get("eco"),
                        q=params.get("q"),
                        sort=params.get("sort", "rank"),
                        page=page,
                        page_size=page_size,
                    )
                except ValueError as exc:
                    self._send_error_json(400, str(exc))
                    return
                self._send_json(200, result)
                return

            if path == "/v1/porchlamp/not-ranked":
                persona = params.get("persona", "balanced")
                self._send_json(200, store.notranked_for_persona(persona))
                return

            if path == "/v1/porchlamp/snapshot":
                if "snapshot" in fail_endpoints:
                    self._send_error_json(500, "simulated snapshot failure (--fail snapshot)")
                    return
                self._send_json(200, store.snapshot)
                return

            if path == "/v1/porchlamp/methodology":
                self._send_json(200, store.methodology)
                return

            if path == "/v1/porchlamp/matrix":
                rows = store.rows_for_persona("balanced")
                points = matrix_points_for(rows)
                self._send_json(
                    200,
                    {
                        "points": points,
                        "excluded_count": store.leaderboard["counts"]["gated"],
                        "axis_coverage": axis_coverage(rows),
                    },
                )
                return

            if path.startswith("/v1/porchlamp/repos/") and path.endswith("/evidence"):
                remainder = path[len("/v1/porchlamp/repos/"):-len("/evidence")]
                parts = remainder.split("/")
                if len(parts) != 2:
                    self._send_error_json(404, f"malformed repo path: {path}")
                    return
                owner, name = parts
                rows = store.evidence.get((owner, name))
                if rows is None:
                    if store.find_repo_detail(owner, name) is not None:
                        self._send_json(200, [])
                        return
                    self._send_error_json(404, f"no evidence fixture for {owner}/{name}")
                    return
                self._send_json(200, rows)
                return

            if path.startswith("/v1/porchlamp/repos/"):
                # The drawer's record fetch, independent of the `/evidence`
                # sub-resource above and of whichever screen opened it.
                remainder = path[len("/v1/porchlamp/repos/"):]
                parts = remainder.split("/")
                if len(parts) != 2:
                    self._send_error_json(404, f"malformed repo path: {path}")
                    return
                owner, name = parts
                detail = store.find_repo_detail(owner, name)
                if detail is None:
                    self._send_error_json(404, f"no such repo: {owner}/{name}")
                    return
                self._send_json(200, detail)
                return

            if path.startswith("/v1/"):
                self._send_error_json(404, f"no such v2 fixture endpoint: {path}")
                return

            self._serve_static(path)

        def do_POST(self) -> None:
            path, _params = _query_params(self.path)

            if path == "/v1/porchlamp/reproject":
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw = self.rfile.read(length) if length > 0 else b"{}"
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    self._send_error_json(400, "request body is not valid JSON")
                    return
                try:
                    request = ReprojectRequest.model_validate(body)
                except ValidationError as exc:
                    self._send_error_json(400, str(exc))
                    return
                weights = {pillar: getattr(request, f"weight_{pillar}") for pillar in PILLARS}
                rows = store.rows_for_persona("balanced")
                projected = reproject_rows(rows, weights)
                self._send_json(200, {"rows": projected, "total": len(projected), "lens": "custom"})
                return

            if path.startswith("/v1/"):
                self._send_error_json(404, f"no such v2 fixture endpoint: {path}")
                return

            self._send_error_json(404, f"not found: {path}")

    return Handler


def serve(
    *, host: str, port: int, web_dir: Path, fixtures_dir: Path, fail_endpoints: frozenset[str] = frozenset()
) -> ThreadingHTTPServer:
    store = FixtureStore(fixtures_dir)
    handler = make_handler(store, web_dir, fail_endpoints)
    httpd = ThreadingHTTPServer((host, port), handler)
    return httpd


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8899, help="0 for an OS-assigned free port")
    parser.add_argument("--web-dir", type=Path, default=DEFAULT_WEB_DIR)
    parser.add_argument("--fixtures-dir", type=Path, default=DEFAULT_FIXTURES_DIR)
    parser.add_argument(
        "--fail",
        action="append",
        default=[],
        metavar="ENDPOINT",
        help="force ENDPOINT to return 500 (repeatable); currently only 'snapshot' is wired — "
        "for manually checking the U-15 error-state UI without editing a fixture",
    )
    args = parser.parse_args(argv)

    httpd = serve(
        host=args.host, port=args.port, web_dir=args.web_dir, fixtures_dir=args.fixtures_dir,
        fail_endpoints=frozenset(args.fail),
    )
    actual_port = httpd.server_address[1]
    print(f"Porchlamp UI fixture server: http://{args.host}:{actual_port}/ "
          f"(web={args.web_dir}, fixtures={args.fixtures_dir})")
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
