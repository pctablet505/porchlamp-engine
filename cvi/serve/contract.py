"""The CVI v2 wire contract: pydantic models for every payload the UI (`web/v2/`)
consumes over HTTP.

Packet W4-4 (API v2) has not been built yet. This module is the contract both
sides test against in the meantime: `tests/fixtures/ui/*.json` validate
against these models (`tests/test_ui_fixtures.py`), and
`scripts/serve_ui_fixtures.py` answers the v2 endpoints from those fixtures
so the front end under `web/v2/` can be built and exercised before W4-4
lands. When W4-4 wires the real store-backed API, it MUST serve these same
shapes — any field the UI needs beyond what is defined here is an API change,
never a client-side derivation (`docs/v2/06-UX-DESIGN.md` §4).

Free of I/O by design (no imports of `cvi.store`, `cvi.collect`, `cvi.core`
engine internals, no filesystem or network calls) so it can be imported by
both a future FastAPI app and a stdlib fixture server without pulling in the
scoring engine.

Field sources, in order of authority:
- `docs/v2/06-UX-DESIGN.md` §4 (the row contract the UI needs).
- `docs/v2/04-ARCHITECTURE.md` §3 (`ScoreRecord`) and §5 (store schema v2)
  for field names, nullability and the five pillar ids.
- `docs/v2/02-RANKING-DESIGN.md` §7-10 for the semantics of coverage,
  ceilings, tiers, persona gates/composites and tie-breaking (informs the
  fixtures in `tests/fixtures/ui/`, not this module directly).

Nullability choices (not stated verbatim in `06` §4, decided here because a
contract needs concrete types):
- A pillar SCORE (`p_*`) is `float | None`: `None` means the pillar could not
  be scored at all for this repository (every parameter in it unmeasured).
- A pillar COVERAGE (`c_*`) is always a `float` in `[0, 1]`, never `None`:
  coverage is a fraction of weight backed by evidence and 0.0 is itself a
  meaningful, always-reportable value (a pillar with `c_p == 0.0` implies
  `p_p is None`; the reverse is not required — a pillar can be scored from a
  minority of its parameters with material coverage below 1.0).
- `tier` and `tier_ceiling` are `str | None`: gated/unrated repositories (and,
  for `tier_ceiling`, any row at or above the no-ceiling coverage band) carry
  no value.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

#: The five pillars, in the fixed order `02-RANKING-DESIGN.md` D-AGG-1 weighs
#: them (responsiveness .25, criticality .25, governance .20, rigor .15,
#: accessibility .15). Used by fixtures and by the UI's client-side tie-level
#: derivation; the API itself sends flat `p_<pillar>` / `c_<pillar>` fields.
PILLARS: tuple[str, ...] = (
    "responsiveness",
    "criticality",
    "governance",
    "rigor",
    "accessibility",
)

#: `persona_gates` / `persona_composites` keys: the balanced (ungated) lens
#: plus the four persona lenses defined in `02-RANKING-DESIGN.md` D-PERS-2.
PERSONAS: tuple[str, ...] = (
    "balanced",
    "first_timer",
    "career_capital",
    "senior_systems",
    "ai_assisted",
)

#: `gate_reason` values (`06-UX-DESIGN.md` §3 A2: "mirror / archived /
#: unlicensed / stale / black hole / non-software"), plus `fork` (W4-3
#: store-queries review, 2026-09-13: the engine gates forks separately from
#: mirrors — same UI treatment, distinct reason code).
GateReason = Literal["mirror", "archived", "unlicensed", "stale", "black_hole", "non_software", "fork"]

#: `unrated_reason` values (`06-UX-DESIGN.md` §3 A2: "insufficient evidence
#: ... ; no external workflow; off-platform routed"). Both spellings of the
#: evidence-floor reason are accepted (W4-3 review, 2026-09-13): the
#: engine's own constant is `"unrated: insufficient_evidence"`; this
#: contract takes that string as-is rather than asking the API to
#: normalise it, and `copy.js.unratedReasonText` maps either spelling to
#: the same sentence.
UnratedReason = Literal[
    "insufficient_evidence", "unrated: insufficient_evidence", "no_external_workflow", "off_platform_routed"
]


class StrictModel(BaseModel):
    """Base for every contract model: reject unknown fields both ways.

    An API response with a typo'd field name, or a UI fixture carrying a
    field the model does not know about, is a contract break — it must fail
    validation loudly rather than pass silently through `.model_dump()`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class PersonaComposite(StrictModel):
    """One persona's re-projection of a single repository's score.

    `04-ARCHITECTURE.md` §3: `persona_composites: dict[str, PersonaComposite]`
    on `ScoreRecord`, one entry per entry in `PERSONAS` for a rated repo that
    the persona does not gate out. `rated` is recomputed per-persona from the
    persona-weighted coverage (`04` §6 "Per-persona rating"), never copied
    from the balanced `rated` flag.
    """

    cvi: float | None
    coverage: float | None
    tier: str | None
    rated: bool


class LeaderboardRow(StrictModel):
    """One ranked repository row, as `GET /v1/cvi/leaderboard` returns it.

    Every field in `06-UX-DESIGN.md` §4. Coverage bands (`02` D-MISS-3):
    `>= 0.95` no ceiling, `[0.80, 0.95)` ceiling A, `[0.60, 0.80)` ceiling B;
    a row here always has `coverage >= 0.60` (below that a repository is
    `unrated`, see `NotRankedRow`).
    """

    rank: int
    #: `None` means this row is a singleton on its composite (no tie): W4-3
    #: store-queries review, 2026-09-13 (ruling R-D). A non-`None` value is
    #: an OPAQUE label compared only for equality within one response —
    #: never assume it is small, contiguous, or stable across responses.
    tie_group: int | None
    owner: str
    name: str
    ecosystem: str
    cvi: float
    coverage: float
    tier: str | None
    tier_ceiling: str | None
    quadrant: Literal["Sweet Spot", "Crucible", "Nursery", "Graveyard"] | None
    leverage: float | None
    friction: float | None
    leverage_coverage: float | None
    friction_coverage: float | None

    p_responsiveness: float | None
    c_responsiveness: float
    p_criticality: float | None
    c_criticality: float
    p_governance: float | None
    c_governance: float
    p_rigor: float | None
    c_rigor: float
    p_accessibility: float | None
    c_accessibility: float

    persona_gates: dict[str, str] = {}
    persona_composites: dict[str, PersonaComposite] = {}
    suspicion: list[str] = []

    #: `None` until W1-10 lands a v2 producer (W4-3 review, 2026-09-13); the
    #: UI renders "—" for a null value.
    stars_audited: int | None
    snapshot_id: str
    benchmark_version: str
    #: `04-ARCHITECTURE.md` §3 `ScoreRecord.raw_hash`, shown in the drawer's
    #: integrity section (`06-UX-DESIGN.md` §3 Screen C item 5: "integrity:
    #: raw_hash, snapshot id, suspicion flags"). Not itself in `06` §4's row
    #: field list — added here because W4-4's work-plan entry names a
    #: `repos/{o}/{n}` detail endpoint this packet does not build, and the
    #: leaderboard row is the only place left for the drawer to get it.
    raw_hash: str

    #: Review Blocker B-6 (2026-09-13): the persona delta column (`06` §3
    #: Screen A, §8 checklist item 4) has no data to render without these.
    #: `balanced_rank` is this repo's `rank` under the balanced lens;
    #: `rank_delta = balanced_rank - rank` (positive means the row ranks
    #: BETTER — a lower rank NUMBER — under the active persona than under
    #: balanced; the UI shows that as "▲"). Both are `None` under the
    #: balanced lens itself (there is nothing to compare against) and both
    #: set together under every other persona. W4-4 already computes the
    #: store-side equivalent (`reproject()`'s `balanced_rank`); it need
    #: only subtract to get `rank_delta`, per `04-ARCHITECTURE.md` §6's
    #: persona projection query (`rank_p` there is this row's `rank` here).
    balanced_rank: int | None = None
    rank_delta: int | None = None

    @model_validator(mode="after")
    def _rank_and_coverage_are_positive(self) -> "LeaderboardRow":
        if self.rank < 1:
            raise ValueError(f"rank must be >= 1, got {self.rank}")
        if not (0.0 <= self.coverage <= 1.0):
            raise ValueError(f"coverage must be in [0, 1], got {self.coverage}")
        for pillar in PILLARS:
            c = getattr(self, f"c_{pillar}")
            if not (0.0 <= c <= 1.0):
                raise ValueError(f"c_{pillar} must be in [0, 1], got {c}")
        return self

    @model_validator(mode="after")
    def _balanced_rank_and_delta_are_consistent(self) -> "LeaderboardRow":
        has_balanced_rank = self.balanced_rank is not None
        has_delta = self.rank_delta is not None
        if has_balanced_rank != has_delta:
            raise ValueError(
                f"{self.owner}/{self.name}: balanced_rank and rank_delta must both be set or both be null "
                f"(got balanced_rank={self.balanced_rank!r}, rank_delta={self.rank_delta!r})"
            )
        if has_balanced_rank and self.rank_delta != self.balanced_rank - self.rank:
            raise ValueError(
                f"{self.owner}/{self.name}: rank_delta={self.rank_delta} must equal "
                f"balanced_rank({self.balanced_rank}) - rank({self.rank}) = {self.balanced_rank - self.rank}"
            )
        return self


class RepoDetailResponse(StrictModel):
    """`GET /v1/cvi/repos/{owner}/{name}` (W4-4 addition, 2026-09-13): one
    repository's stored record plus its persona view, for the "Why this
    score" drawer (`06-UX-DESIGN.md` Screen C) -- which must open for a
    GATED or UNRATED repo too ("(1) identity strip and gate status (if
    gated: the reason sentence and nothing else above the fold)"). No
    existing contract model fits: `LeaderboardRow` requires a real
    `rank >= 1` and is built only for rows `list_ranked` actually returns
    (rated ones). This model carries the same facts with `rank`/`tie_group`/
    `cvi` relaxed to `None` and adds `eligible`/`gate_reason`/`rated`/
    `unrated_reason` so the drawer's gate-status section has what it needs
    without a second request. Independent of the W4-5a-review contract
    bridge table (not one of its twelve items): that table never asked for
    a repo-detail model at all, so this is W4-4's own addition, reviewed by
    neither agent yet.

    Field sources are identical to `LeaderboardRow` (`04-ARCHITECTURE.md`
    §3/§5); the OVERALL `coverage` is always a float (D-MISS-3's `C`,
    reportable even at 0.0 -- `repositories.coverage` is `NOT NULL`) while
    `cvi` is `None` exactly when the repo was never scored (gated at stage
    0). PER-PILLAR `c_<pillar>` is `float | None`, unlike `LeaderboardRow`'s
    non-null version of the same field: a GATED repo's `cvi.core.engine`
    never builds a `PillarScore` for any pillar at all (`record.pillars`
    is `{}`), so `cvi.store.writer` stores SQL `NULL`, not a fabricated
    `0.0` (`repositories.c_<pillar>` has no `NOT NULL` constraint,
    matching this). `LeaderboardRow` never hits this because
    `cvi.store.queries.list_ranked` only ever returns `rated=1` rows,
    which always have every pillar computed; this model is built for
    gated/unrated repos too, so it cannot share that assumption.

    `gate_reason` is a plain `str`, not the shared `GateReason` Literal:
    the W4-5a-review contract bridge table item 3 ruling (map
    `"unrated: no_external_workflow"` -- a stage-6 GATE despite the
    spelling -- onto `NotRankedRow.unrated_reason`, not `GateReason`) is
    a `NotRankedRow`-shaped decision about which UI GROUP a row belongs
    to; this model has no group, only "the repo's own record", so it
    passes `cvi.core.gates.GateResult.reason` through verbatim rather
    than repeat that same translation with no group to translate it
    into."""

    owner: str
    name: str
    ecosystem: str
    snapshot_id: str
    benchmark_version: str
    raw_hash: str

    eligible: bool
    gate_reason: str | None = None
    rated: bool
    unrated_reason: UnratedReason | None = None

    rank: int | None
    tie_group: int | None
    cvi: float | None
    coverage: float
    tier: str | None
    tier_ceiling: str | None
    quadrant: Literal["Sweet Spot", "Crucible", "Nursery", "Graveyard"] | None
    leverage: float | None
    friction: float | None
    leverage_coverage: float | None
    friction_coverage: float | None

    p_responsiveness: float | None
    c_responsiveness: float | None
    p_criticality: float | None
    c_criticality: float | None
    p_governance: float | None
    c_governance: float | None
    p_rigor: float | None
    c_rigor: float | None
    p_accessibility: float | None
    c_accessibility: float | None

    persona_gates: dict[str, str] = {}
    persona_composites: dict[str, PersonaComposite] = {}
    suspicion: list[str] = []
    stars_audited: int | None

    @model_validator(mode="after")
    def _rank_requires_rated_and_coverage_is_bounded(self) -> "RepoDetailResponse":
        if self.rated and self.rank is None:
            raise ValueError(f"{self.owner}/{self.name}: rated=True but rank is None")
        if not (0.0 <= self.coverage <= 1.0):
            raise ValueError(f"coverage must be in [0, 1], got {self.coverage}")
        for pillar in PILLARS:
            c = getattr(self, f"c_{pillar}")
            if c is not None and not (0.0 <= c <= 1.0):
                raise ValueError(f"c_{pillar} must be in [0, 1], got {c}")
        return self


class NotRankedRow(StrictModel):
    """A repository absent from the RANKED list, as `GET /v1/cvi/not-ranked`
    returns it — for three distinct reasons (W4-3 store-queries review,
    2026-09-13 relaxes this from "exactly one" to "at least one" of):

    - `gate_reason`: a universal, persona-independent gate (`06` §3 A2
      Gated group).
    - `unrated_reason`: composite coverage below the 0.60 floor (`06` §3 A2
      Unrated group).
    - `persona_gate_reason`: the repository IS balanced-rated (would appear
      on the ranked leaderboard under the balanced lens) but the currently
      active PERSONA gates it out (D-PERS-2/3) — this is U-4's "hidden by
      this lens (N)" disclosure's row-level detail, only populated when
      `/not-ranked` is called with a non-balanced `persona`.

    There is no `rank` field at all — none of these rows are ordered by
    score.
    """

    owner: str
    name: str
    ecosystem: str
    gate_reason: GateReason | None = None
    unrated_reason: UnratedReason | None = None
    persona_gate_reason: str | None = None
    coverage: float | None = None

    @model_validator(mode="after")
    def _at_least_one_reason(self) -> "NotRankedRow":
        if self.gate_reason is None and self.unrated_reason is None and self.persona_gate_reason is None:
            raise ValueError(
                f"{self.owner}/{self.name}: at least one of gate_reason/unrated_reason/"
                "persona_gate_reason must be set"
            )
        return self


class EvidenceRow(StrictModel):
    """One parameter's evidence, as `GET /v1/cvi/repos/{o}/{n}/evidence` returns
    it in its `params` list (`04-ARCHITECTURE.md` §7).

    A measured parameter carries `value`/`score`/`n`/window/method/source/
    `fetched_at`; an unmeasured one carries only `unmeasured_reason` and
    leaves the rest `None` (`core/models.py`'s `Measured | Unmeasured` union,
    flattened for the wire). `measured` (W4-3 store-queries review,
    2026-09-13) is the authoritative flag the store actually carries; the
    other measured-field presence check is kept as a consistency guard, not
    the source of truth.
    """

    param_id: str
    pillar: Literal["responsiveness", "criticality", "governance", "rigor", "accessibility"]
    measured: bool
    value: float | str | int | None = None
    score: float | None = None
    n: int | None = None
    window_start: str | None = None
    window_end: str | None = None
    method: str | None = None
    source: str | None = None
    source_sha256: str | None = None
    fetched_at: str | None = None
    unmeasured_reason: (
        Literal["third_party_gap", "collector_failure", "insufficient_n", "not_applicable", "no_population"]
        | None
    ) = None

    @model_validator(mode="after")
    def _measured_matches_reason(self) -> "EvidenceRow":
        if self.measured and self.unmeasured_reason is not None:
            raise ValueError(f"{self.param_id}: measured=True but unmeasured_reason is also set")
        if not self.measured and self.unmeasured_reason is None:
            raise ValueError(f"{self.param_id}: measured=False needs an unmeasured_reason")
        measured_fields = (self.score, self.method, self.source, self.fetched_at)
        has_measured_fields = any(f is not None for f in measured_fields)
        if self.measured and not has_measured_fields:
            raise ValueError(f"{self.param_id}: measured=True but score/method/source/fetched_at are all null")
        if not self.measured and has_measured_fields:
            raise ValueError(f"{self.param_id}: measured=False but carries a measured field")
        return self


class SnapshotCounts(StrictModel):
    """Rated / gated / unrated repository counts for one snapshot, plus
    `total` (W4-3 store-queries review, 2026-09-13: rated+gated+unrated,
    carried explicitly rather than asking every reader to sum the three)."""

    rated: int
    gated: int
    unrated: int
    total: int

    @model_validator(mode="after")
    def _non_negative_and_total_matches(self) -> "SnapshotCounts":
        for field_name in ("rated", "gated", "unrated", "total"):
            if getattr(self, field_name) < 0:
                raise ValueError(f"{field_name} must be >= 0")
        if self.total != self.rated + self.gated + self.unrated:
            raise ValueError(
                f"total={self.total} must equal rated+gated+unrated="
                f"{self.rated + self.gated + self.unrated}"
            )
        return self


class SnapshotSummary(StrictModel):
    """`GET /v1/cvi/snapshot`: the manifest summary (`06` §3 F, `04` §7).

    W4-3 store-queries review (2026-09-13) additions: `persona_visible_counts`
    (rated-and-not-persona-gated row count per persona — the number behind
    U-4's "hidden by this lens (N)" disclosure, where N = counts.rated -
    persona_visible_counts[persona]), `code_commit` (the git commit the
    snapshot was produced from) and `canonical_dump_hash` (the reproduce-in-CI
    byte-identical dump hash, distinct from `manifest_root`'s Merkle root
    over input envelopes).
    """

    snapshot_id: str
    as_of: str
    benchmark_version: str
    constants_hash: str
    manifest_root: str
    counts: SnapshotCounts
    persona_visible_counts: dict[str, int] = {}
    code_commit: str
    canonical_dump_hash: str
    detector_summary: dict[str, int] = {}
    reproduce_command: str

    @model_validator(mode="after")
    def _persona_visible_never_exceeds_rated(self) -> "SnapshotSummary":
        for persona, visible in self.persona_visible_counts.items():
            if visible < 0 or visible > self.counts.rated:
                raise ValueError(
                    f"persona_visible_counts[{persona!r}]={visible} must be in [0, counts.rated={self.counts.rated}]"
                )
        return self


#: Owner instruction 2026-09-13 (verbatim, replacing the earlier
#: no-pagination ruling): "use pagination ... default will be 20 ... keep
#: value of pagesize like 20, 50, 100, 500, 1000". `scripts/serve_ui_fixtures.py`
#: and `web/v2/` both import this so the allowed set is defined exactly once.
LEADERBOARD_PAGE_SIZES: tuple[int, ...] = (20, 50, 100, 500, 1000)
DEFAULT_PAGE_SIZE: int = 20


class LeaderboardResponse(StrictModel):
    """`GET /v1/cvi/leaderboard` envelope: rows plus paging and scope."""

    rows: list[LeaderboardRow]
    total: int
    counts: SnapshotCounts
    page: int
    page_size: int
    persona: str
    ecosystem: str | None = None

    @model_validator(mode="after")
    def _paging_is_sane(self) -> "LeaderboardResponse":
        if self.page < 1:
            raise ValueError(f"page must be >= 1, got {self.page}")
        if self.page_size < 1 or self.page_size > max(LEADERBOARD_PAGE_SIZES):
            raise ValueError(f"page_size must be in (0, {max(LEADERBOARD_PAGE_SIZES)}], got {self.page_size}")
        if self.total < 0:
            raise ValueError(f"total must be >= 0, got {self.total}")
        if len(self.rows) > self.page_size:
            raise ValueError(
                f"page returned {len(self.rows)} rows, more than page_size={self.page_size}"
            )
        return self


class NotRankedResponse(StrictModel):
    """`GET /v1/cvi/not-ranked` envelope: the two groups from `06` §3 A2,
    plus a third `persona_hidden` group (W4-3 review: rows balanced-rated
    but excluded by the active persona's gate — populated only when this
    endpoint is called with a non-balanced `persona`). Client-side paged
    (see the handoff: this list is bounded and small enough that this
    packet does not add server-side paging fields to it)."""

    gated: list[NotRankedRow]
    unrated: list[NotRankedRow]
    persona_hidden: list[NotRankedRow] = []
    counts: SnapshotCounts

    @model_validator(mode="after")
    def _rows_match_their_group(self) -> "NotRankedResponse":
        for row in self.gated:
            if row.gate_reason is None:
                raise ValueError(f"{row.owner}/{row.name} in 'gated' has no gate_reason")
        for row in self.unrated:
            if row.unrated_reason is None:
                raise ValueError(f"{row.owner}/{row.name} in 'unrated' has no unrated_reason")
        for row in self.persona_hidden:
            if row.persona_gate_reason is None:
                raise ValueError(f"{row.owner}/{row.name} in 'persona_hidden' has no persona_gate_reason")
        return self


class MatrixPoint(StrictModel):
    """One scatter marker for Screen B (the 2D matrix — `06` §3 B). Not
    rendered by this packet (W4-5a owns Screens A/A2/C only; the matrix is
    W4-5b), added to the contract now per the W4-3 store-queries review so
    W4-4 can build the endpoint against a settled shape."""

    owner: str
    name: str
    ecosystem: str
    leverage: float
    friction: float
    leverage_coverage: float
    friction_coverage: float
    quadrant: Literal["Sweet Spot", "Crucible", "Nursery", "Graveyard"]
    stars_audited: int | None
    cvi: float
    coverage: float
    tier: str | None


class MatrixAxisCoverage(StrictModel):
    """One axis's (`leverage`/`friction`) worst-missing contributing pillar
    and the raw numbers behind it — Opus code review B-2 (2026-09-13): the
    original `axis_coverage_caption: dict[str, str]` composed the SENTENCE
    server-side (a `06` §6 violation: "every sentence in copy.js") and
    could never match the real API, which carries these numbers, not
    prose. `axis_partial_count`/`axis_partial_share` name the fraction of
    PLOTTED rows where `pillar` was unmeasured (so the axis coordinate for
    that row is itself only partially measured)."""

    pillar: Literal["responsiveness", "criticality", "governance", "rigor", "accessibility"]
    axis_partial_count: int
    axis_partial_share: float

    @model_validator(mode="after")
    def _share_and_count_are_sane(self) -> "MatrixAxisCoverage":
        if self.axis_partial_count < 0:
            raise ValueError(f"axis_partial_count must be >= 0, got {self.axis_partial_count}")
        if not (0.0 <= self.axis_partial_share <= 1.0):
            raise ValueError(f"axis_partial_share must be in [0, 1], got {self.axis_partial_share}")
        return self


class MatrixResponse(StrictModel):
    """`GET /v1/cvi/matrix`. `excluded_count`: gated repositories are never
    plotted (`06` §3 B). `axis_coverage`: one entry per axis (`leverage`/
    `friction`) whose worst-missing contributing pillar clears the
    fixture server's own documented threshold — the UI (`copy.js`)
    composes the "friction computed without accessibility for 12%" style
    caption from these numbers plus the pillar's display name; this
    contract never carries pre-rendered prose (B-2)."""

    points: list[MatrixPoint]
    excluded_count: int
    axis_coverage: dict[str, MatrixAxisCoverage] = {}

    @model_validator(mode="after")
    def _excluded_count_is_non_negative(self) -> "MatrixResponse":
        # W4-5b addition: every other count model in this file
        # (`SnapshotCounts`, `LeaderboardResponse.page`/`total`) validates
        # non-negativity; `MatrixResponse` had no such guard.
        if self.excluded_count < 0:
            raise ValueError(f"excluded_count must be >= 0, got {self.excluded_count}")
        for axis in self.axis_coverage:
            if axis not in ("leverage", "friction"):
                raise ValueError(f"axis_coverage key must be 'leverage' or 'friction', got {axis!r}")
        return self


class ReprojectRequest(StrictModel):
    """`POST /v1/cvi/reproject`: a custom weight vector over the five
    pillars (U-11: labelled "custom" everywhere, never called a persona).
    Weights are non-negative and sum to 1 (within floating-point epsilon)."""

    weight_responsiveness: float
    weight_criticality: float
    weight_governance: float
    weight_rigor: float
    weight_accessibility: float

    @model_validator(mode="after")
    def _weights_are_a_valid_distribution(self) -> "ReprojectRequest":
        weights = (
            self.weight_responsiveness,
            self.weight_criticality,
            self.weight_governance,
            self.weight_rigor,
            self.weight_accessibility,
        )
        for w in weights:
            if w < 0:
                raise ValueError(f"weights must be non-negative, got {w}")
        total = sum(weights)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"weights must sum to 1.0, got {total}")
        return self


class ReprojectRow(LeaderboardRow):
    """One `ReprojectResponse` row (packet W4-5b addition — no model existed
    for this before): `LeaderboardRow`'s fields ARE the custom projection
    (`rank`/`cvi`/`coverage`/`tier`/`tie_group` under the plain names, per
    U-11 and `cvi.store.queries.reproject`'s own field-naming contract —
    "this IS the requested view") plus the five stored BALANCED values
    side by side, exactly as `cvi.store.queries.reproject` already returns
    them (`record["balanced_rank"] = record.pop("rank")` etc.) — Screen D's
    "live side list of rank deltas vs balanced" needs both figures from one
    response, and `ReprojectResponse` had no field for the balanced side
    until this packet."""

    balanced_rank: int | None
    balanced_tie_group: int | None
    balanced_cvi: float | None
    balanced_coverage: float | None
    balanced_tier: str | None


class ReprojectResponse(StrictModel):
    """Rows re-projected under a custom weight vector — `ReprojectRow`
    (`LeaderboardRow` plus the balanced-comparison fields), `lens` is
    always the literal `"custom"` (U-11)."""

    rows: list[ReprojectRow]
    total: int
    lens: Literal["custom"] = "custom"


class RepoDetailFixtureResponse(StrictModel):
    """`GET /v1/cvi/repos/{owner}/{name}` AS `scripts/serve_ui_fixtures.py`
    (the stand-in fixture server, not the real API) SHAPES IT -- packet
    W4-5b addition, lead ruling 2026-09-13 on the Matrix-vs-Leaderboard
    drawer-content-parity finding: the SAME record `findRow` used to
    search for only in whatever state slice happened to already be loaded
    (`06` §3 Screen C) -- now the single, tab-independent source every
    entry point into the drawer (Leaderboard, Matrix, Compare, Configure)
    fetches, so the drawer renders identically regardless of which screen
    opened it.

    `kind="rated"` carries a `LeaderboardRow`; `kind in ("gated",
    "unrated")` carries a `NotRankedRow` (`06` §4's own two row shapes) --
    a discriminated union rather than a third, redundant shape.

    Round-3 rebase note (2026-09-14): renamed from `RepoDetailResponse`
    on this same endpoint -- W4-4 landed its OWN, differently-shaped
    `RepoDetailResponse` (a flat `LeaderboardRow`-like model with every
    field nullable, matching `cvi/serve/app.py::_repo_detail`'s REAL,
    served response) on `main` while this branch still carried this one
    under the identical name; the rebase auto-merged both class bodies
    into the file with no textual conflict (they sit in different
    locations), silently shadowing W4-4's actual, imported-and-used model
    with this dead one (nothing had ever imported this class by name --
    `scripts/serve_ui_fixtures.py` builds `{"kind": ..., "row": ...}` as a
    plain dict and JSON-serialises it without validating against any
    pydantic model at all) and breaking every real `/repos/{owner}/{name}`
    call with a 500 (a `LeaderboardRow`/`NotRankedRow`-shaped kwarg dict
    failing THIS class's very different required fields). This class is
    kept, renamed, purely as documentation of the shape the fixture
    server/frontend (`web/v2/views/drawer.js`) currently use while the UI
    has not yet switched from the fixture server to the real API -- it
    validates nothing at runtime today, same as before the rename."""

    kind: Literal["rated", "gated", "unrated"]
    row: LeaderboardRow | NotRankedRow

    @model_validator(mode="after")
    def _kind_matches_row_shape(self) -> "RepoDetailFixtureResponse":
        if self.kind == "rated" and not isinstance(self.row, LeaderboardRow):
            raise ValueError("kind='rated' must carry a LeaderboardRow")
        if self.kind in ("gated", "unrated") and not isinstance(self.row, NotRankedRow):
            raise ValueError(f"kind={self.kind!r} must carry a NotRankedRow")
        if self.kind == "gated" and self.row.gate_reason is None:
            raise ValueError("kind='gated' row must carry a gate_reason")
        if self.kind == "unrated" and self.row.unrated_reason is None:
            raise ValueError("kind='unrated' row must carry an unrated_reason")
        return self


# --------------------------------------------------------------------------- #
# GET /v1/cvi/methodology (packet W4-5b, `06` §3 F). No model existed for      #
# this payload before this packet (W4-5a's own docstring lists only the       #
# leaderboard/not-ranked/evidence/matrix/reproject shapes) -- these mirror,   #
# field for field, `cvi.core.methodology.MethodologyDocument` (a TypedDict,   #
# free of pydantic per that module's own `cvi.core` import-boundary rule) and #
# its extension `cvi.audit.methodology_history.methodology_with_history`,    #
# so the two can be cross-checked by a parity test without this contract     #
# ever importing either module (this file stays I/O- and engine-free, per    #
# its own module docstring). `GET /v1/cvi/snapshot` reuses the existing      #
# `SnapshotSummary` model above -- the Methodology screen renders both       #
# payloads, not a third combined one (`06` §3 F).                            #
# --------------------------------------------------------------------------- #


class MethodologyPillar(StrictModel):
    name: str
    weight: float


class MethodologyProxyInfo(StrictModel):
    """Present only on a parameter with a documented proxy path (e.g.
    `openssf_criticality`'s `proxy_min_signals`, `pipeline_defenses`'s
    `proxy_min_n`) -- both fields are individually optional, never both
    absent (a `ProxyInfo` block that names neither would carry no
    information)."""

    proxy_min_n: int | None = None
    proxy_min_signals: int | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "MethodologyProxyInfo":
        if self.proxy_min_n is None and self.proxy_min_signals is None:
            raise ValueError("MethodologyProxyInfo must set proxy_min_n and/or proxy_min_signals")
        return self


class MethodologyParameter(StrictModel):
    input_id: str
    pillar: Literal["responsiveness", "criticality", "governance", "rigor", "accessibility"]
    weight_within_pillar: float
    min_n: int | None = None
    window_days: int | None = None
    curve: str
    #: Free-form per-kind shape (numeric domain bounds, an enum score table,
    #: or an ECDF description) -- deliberately untyped, mirroring the
    #: TypedDict's own `dict[str, object]` (`cvi.core.methodology._value_domain`).
    value_domain: dict[str, Any] = {}
    proxy: MethodologyProxyInfo | None = None


class MethodologyGate(StrictModel):
    id: str
    name: str
    criterion: str
    reasons: tuple[str, ...] = ()
    cutoffs: dict[str, Any] = {}
    license_vocabulary: dict[str, Any] = {}


class MethodologyMissingnessReason(StrictModel):
    reason: str
    semantics: str


class MethodologyNotApplicableRule(StrictModel):
    parameter: str
    bundle_fact: str
    value: str


class MethodologyMissingness(StrictModel):
    reasons: list[MethodologyMissingnessReason]
    coverage_thresholds: dict[str, float]
    not_applicable_rules: list[MethodologyNotApplicableRule]
    openssf_proxy_min_signals: int


class MethodologyTieBreakLevel(StrictModel):
    field: str
    direction: Literal["ascending", "descending"]
    note: str | None = None


class MethodologyTieBreak(StrictModel):
    composite_decimals: int
    levels: list[MethodologyTieBreakLevel]


class MethodologyPersonaGate(StrictModel):
    persona: str
    description: str
    thresholds: dict[str, Any] = {}


class MethodologyPersonas(StrictModel):
    presets: dict[str, dict[str, float]]
    gates: list[MethodologyPersonaGate]


class MethodologyDetector(StrictModel):
    """One row per `cvi.verify.detectors.DETECTOR_NAMES` entry -- present
    only on the extended document (`methodology_with_history`); the pure
    `methodology_document()` never populates this list (`cvi.core`'s
    import boundary keeps `cvi.verify` out of `cvi.core`)."""

    name: str
    trigger: str
    direction: Literal["magnitude", "above", "below", "compound", "boolean"]
    thresholds: dict[str, Any] = {}


class MethodologyProcessConstant(StrictModel):
    """One of the three audit-only release-gate constants
    (`cvi.audit.anchors`/`cvi.audit.sensitivity`) that `constants_v2` itself
    excludes from `CONSTANTS_HASH` on purpose (`docs/v2/rfcs/README.md`
    "Process constants")."""

    name: str
    module: str
    value: Any
    gates: str


class MethodologyRfcLogEntry(StrictModel):
    date: str
    rfc: str
    summary: str
    hash_before: str | None = None
    hash_after: str | None = None


class MethodologyResponse(StrictModel):
    """`GET /v1/cvi/methodology`: the full extended document
    (`methodology_with_history`'s shape) -- pillars, parameters, gates,
    missingness, tie-break levels and personas are always present;
    `detectors`, `not_hashed_process_constants` and `rfc_log` are the three
    additions the extended document carries beyond the pure
    `methodology_document()` (`06` §3 F: "the published methodology
    ... rendered from the frozen constants module so it cannot drift from
    the code", plus the detector/process-constant/RFC-log facts that live
    just outside `cvi.core`'s import boundary)."""

    benchmark_version: str
    schema_version: int
    constants_hash: str
    pillars: list[MethodologyPillar]
    parameters: list[MethodologyParameter]
    gates: list[MethodologyGate]
    missingness: MethodologyMissingness
    tie_break_levels: MethodologyTieBreak
    personas: MethodologyPersonas
    detectors: list[MethodologyDetector] = []
    not_hashed_process_constants: list[MethodologyProcessConstant] = []
    rfc_log: list[MethodologyRfcLogEntry] = []


__all__ = [
    "PILLARS",
    "PERSONAS",
    "LEADERBOARD_PAGE_SIZES",
    "DEFAULT_PAGE_SIZE",
    "GateReason",
    "UnratedReason",
    "StrictModel",
    "PersonaComposite",
    "LeaderboardRow",
    "RepoDetailResponse",
    "NotRankedRow",
    "EvidenceRow",
    "SnapshotCounts",
    "SnapshotSummary",
    "LeaderboardResponse",
    "NotRankedResponse",
    "MatrixPoint",
    "MatrixAxisCoverage",
    "MatrixResponse",
    "ReprojectRequest",
    "ReprojectRow",
    "ReprojectResponse",
    "RepoDetailFixtureResponse",
    "MethodologyPillar",
    "MethodologyProxyInfo",
    "MethodologyParameter",
    "MethodologyGate",
    "MethodologyMissingnessReason",
    "MethodologyNotApplicableRule",
    "MethodologyMissingness",
    "MethodologyTieBreakLevel",
    "MethodologyTieBreak",
    "MethodologyPersonaGate",
    "MethodologyPersonas",
    "MethodologyDetector",
    "MethodologyProcessConstant",
    "MethodologyRfcLogEntry",
    "MethodologyResponse",
]
