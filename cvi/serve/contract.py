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

from typing import Literal

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


class MatrixResponse(StrictModel):
    """`GET /v1/cvi/matrix`. `excluded_count`: gated repositories are never
    plotted (`06` §3 B); `axis_coverage_caption`, one entry per axis whose
    coverage-affecting pillar was unmeasured for enough rows to need the
    "friction computed without accessibility for 12%" style caption."""

    points: list[MatrixPoint]
    excluded_count: int
    axis_coverage_caption: dict[str, str] = {}


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


class ReprojectResponse(StrictModel):
    """Rows re-projected under a custom weight vector — same shape as
    `LeaderboardRow`, `lens` is always the literal `"custom"` (U-11)."""

    rows: list[LeaderboardRow]
    total: int
    lens: Literal["custom"] = "custom"


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
    "NotRankedRow",
    "EvidenceRow",
    "SnapshotCounts",
    "SnapshotSummary",
    "LeaderboardResponse",
    "NotRankedResponse",
    "MatrixPoint",
    "MatrixResponse",
    "ReprojectRequest",
    "ReprojectResponse",
]
