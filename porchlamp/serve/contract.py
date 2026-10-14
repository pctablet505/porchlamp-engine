"""The Porchlamp v2 wire contract: pydantic models for every payload the UI (`web/v2/`)
consumes over HTTP.

This module is the contract both sides test against:
`tests/fixtures/ui/*.json` validate against these models
(`tests/test_ui_fixtures.py`), and `scripts/serve_ui_fixtures.py` answers
the v2 endpoints from those fixtures so the front end under `web/v2/` can
be exercised without a store. The real store-backed API serves these same
shapes — any field the UI needs beyond what is defined here is an API change,
never a client-side derivation (`docs/v2/06-UX-DESIGN.md` §4).

Free of I/O by design (no imports of `porchlamp.store`, `porchlamp.collect`, `porchlamp.core`
engine internals, no filesystem or network calls) so it can be imported by
both a FastAPI app and a stdlib fixture server without pulling in the
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

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

#: Windows drive letter or UNC prefix -- rejected alongside a leading POSIX
#: ``/`` so `EvidenceRow.source` can never name an absolute path on
#: any platform. Duplicated from ``porchlamp.core.models.is_relative_envelope_
#: source``'s identical check: this module is deliberately free of
#: ``porchlamp.core`` imports (module docstring above) so it can be imported by a
#: stdlib fixture server without pulling in the scoring engine.
_DRIVE_OR_UNC = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


def _is_relative_envelope_source(value: str) -> bool:
    """Whether ``value`` is a safe, portable envelope-provenance path: a
    non-empty relative POSIX path with no leading ``/``/``\\``, no Windows
    drive letter or UNC prefix, and no ``..`` path segment (provenance
    must never leak this server's absolute filesystem layout, and must
    resolve identically against any data root)."""
    if value == "":
        return False
    if value.startswith(("/", "\\")):
        return False
    if _DRIVE_OR_UNC.match(value):
        return False
    return ".." not in re.split(r"[\\/]+", value)

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
#: unlicensed / stale / black hole / non-software"), plus `fork` (the
#: engine gates forks separately from mirrors — same UI treatment, distinct
#: reason code).
GateReason = Literal["mirror", "archived", "unlicensed", "stale", "black_hole", "non_software", "fork"]

#: `unrated_reason` values (`06-UX-DESIGN.md` §3 A2: "insufficient evidence
#: ... ; no external workflow; off-platform routed"). Both spellings of each
#: reason are accepted: the engine's own constants are prefixed
#: (`porchlamp.core.gates.REASON_INSUFFICIENT_EVIDENCE` is
#: `"unrated: insufficient_evidence"`, and likewise for the other two); this
#: contract takes those strings as-is rather than asking the API to
#: normalise them, and `copy.js.unratedReasonText` maps either spelling to
#: the same sentence. The bare spellings are kept because stored v1 rows and
#: the hand-written UI fixtures use them.
UnratedReason = Literal[
    "insufficient_evidence",
    "unrated: insufficient_evidence",
    "no_external_workflow",
    "unrated: no_external_workflow",
    "off_platform_routed",
    "unrated: off_platform_routed",
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

    porchlamp: float | None
    coverage: float | None
    tier: str | None
    rated: bool


class LeaderboardRow(StrictModel):
    """One ranked repository row, as `GET /v1/porchlamp/leaderboard` returns it.

    Every field in `06-UX-DESIGN.md` §4. Coverage bands (`02` D-MISS-3):
    `>= 0.95` no ceiling, `[0.80, 0.95)` ceiling A, `[0.60, 0.80)` ceiling B;
    a row here always has `coverage >= 0.60` (below that a repository is
    `unrated`, see `NotRankedRow`).
    """

    rank: int
    #: `None` means this row is a singleton on its composite (no tie).
    #: A non-`None` value is an OPAQUE label compared only for equality within
    #: one response — never assume it is small, contiguous, or stable across responses.
    tie_group: int | None
    owner: str
    name: str
    ecosystem: str
    porchlamp: float
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

    #: Nullable if unmeasured; the UI renders "—" for a null value.
    stars_audited: int | None
    snapshot_id: str
    benchmark_version: str
    #: Raw hash for integrity verification, shown in the drawer's
    #: integrity section alongside snapshot ID and suspicion flags.
    raw_hash: str

    #: Persona delta fields for comparative ranking:
    #: `balanced_rank` is this repo's `rank` under the balanced lens;
    #: `rank_delta = balanced_rank - rank` (positive means the row ranks
    #: better — a lower rank number — under the active persona than under
    #: balanced; rendered in UI as "▲"). Both are `None` under the
    #: balanced lens itself and set together under every other persona.
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


class BlackHoleCountedPR(StrictModel):
    """One counted external PR in the D-GATE-5 (black_hole) 180-day window.
    Carries PR number, timestamps, and response classification only (never contributor logins)."""
    number: int
    created_at: str
    answered_within_14d: bool
    first_response_at: str | None = None
    response_kind: Literal["review_or_comment", "merged", "closed_by_maintainer", "none"] = "none"


class BlackHoleExcludedCounts(StrictModel):
    """Counts of PRs excluded from the D-GATE-5 denominator by RFC-0008 false-positive rules."""
    draft: int = 0
    self_closed: int = 0
    new_account: int = 0
    bot: int = 0
    internal: int = 0
    under_14d: int = 0


class BlackHoleEvidence(StrictModel):
    """Transparent mathematical determination evidence for D-GATE-5 (`black_hole`)."""
    unanswered: int
    population_n: int
    unanswered_share: float
    wilson_lower_bound: float
    threshold_share: float = 0.50
    min_n: int = 10
    window_start: str | None = None
    window_end: str | None = None
    fetched_at: str | None = None
    source: str | None = None
    source_sha256: str | None = None
    excluded: BlackHoleExcludedCounts = BlackHoleExcludedCounts()
    counted_prs: list[BlackHoleCountedPR] = []


class RepoDetailResponse(StrictModel):
    """`GET /v1/porchlamp/repos/{owner}/{name}`: one repository's stored record plus
    its persona view, for the score detail drawer (which supports rated, gated,
    and unrated repos).

    Field sources are consistent with `LeaderboardRow`; overall `coverage`
    is always a float, while `porchlamp` is `None` when the repo was gated at stage 0.
    Per-pillar `c_<pillar>` is `float | None` (a gated repo does not compute
    pillar scores, storing NULL).

    `gate_reason` is a plain `str` passing `porchlamp.core.gates.GateResult.reason`
    through verbatim."""

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
    porchlamp: float | None
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
    black_hole_evidence: BlackHoleEvidence | None = None

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
    """A repository absent from the RANKED list, as `GET /v1/porchlamp/not-ranked`
    returns it, characterized by at least one of:

    - `gate_reason`: a universal, persona-independent gate.
    - `unrated_reason`: composite coverage below the 0.60 floor.
    - `persona_gate_reason`: the repository is balanced-rated but excluded
      by the active persona's gate, populated when `/not-ranked` is called
      with a non-balanced `persona`.

    There is no `rank` field — none of these rows are ordered by score.
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
    """One parameter's evidence, as `GET /v1/porchlamp/repos/{o}/{n}/evidence` returns
    it in its `params` list.

    A measured parameter carries `value`/`score`/`n`/window/method/source/
    `fetched_at`; an unmeasured one carries only `unmeasured_reason` and
    leaves the rest `None` (`core/models.py`'s `Measured | Unmeasured` union,
    flattened for the wire). `measured` is the authoritative flag stored in the
    database; other measured-field presence checks serve as consistency guards.
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
        if self.source is not None and not _is_relative_envelope_source(self.source):
            raise ValueError(
                f"{self.param_id}: source must be a path relative to the data root "
                f"(no leading '/', no drive letter, no '..' segment), got {self.source!r}"
            )
        return self


class SnapshotCounts(StrictModel):
    """Rated / gated / unrated repository counts for one snapshot, plus
    explicit `total` (rated + gated + unrated)."""

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
    """`GET /v1/porchlamp/snapshot`: the manifest summary.

    Includes `persona_visible_counts` (rated and not persona-gated count per
    persona), `code_commit` (git commit used for snapshot production), and
    `canonical_dump_hash` (reproducible dump hash, distinct from `manifest_root`
    Merkle root over input envelopes).
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
    def _persona_visible_never_exceeds_non_gated(self) -> "SnapshotSummary":
        """A persona count is bounded by the NON-GATED rows, not by
        ``counts.rated``.

        MEASURED 2026-09-21. This bound used to be ``counts.rated`` and it is
        wrong: a persona view is a re-weighted projection
        (``porchlamp.core.persona``: ``persona_composite`` is "a separate
        re-weighted projection"), and
        ``porchlamp.store.queries._persona_visible_where_sql`` admits a row on
        ``persona_composites[<persona>].coverage >= 0.60`` -- that persona's
        own weights -- not on the balanced ``rated`` flag. A persona that
        down-weights the pillars a sweep has not collected yet therefore
        clears the floor for MORE repos than balanced does. Snapshot
        2026-09-21-L3gp, the first in this project's history with
        ``rated > 0``, measured rated=94 with ai_assisted=303 and
        senior_systems=98, and the old bound 500-ed
        ``GET /v1/porchlamp/snapshot`` on it.

        The old assertion was unfalsifiable rather than merely untested:
        every snapshot before that one had rated=0, which forces every
        persona count to 0 and makes ``visible > counts.rated`` impossible
        to trip. Tightening a bound until only the degenerate case satisfies
        it is the broken-measurement-fails-downward pattern -- the guard
        reported agreement because there was nothing to disagree with.

        ``rated + unrated`` is the sound bound and is still a real one:
        persona visibility requires ``eligible = 1``, and every eligible row
        is counted in ``rated`` or in ``unrated``, never in ``gated``.
        """
        bound = self.counts.rated + self.counts.unrated
        for persona, visible in self.persona_visible_counts.items():
            if visible < 0 or visible > bound:
                raise ValueError(
                    f"persona_visible_counts[{persona!r}]={visible} must be in "
                    f"[0, counts.rated+counts.unrated={bound}]"
                )
        return self


#: Allowed page sizes for leaderboard endpoint pagination.
LEADERBOARD_PAGE_SIZES: tuple[int, ...] = (20, 50, 100, 500, 1000)
DEFAULT_PAGE_SIZE: int = 20


class LeaderboardResponse(StrictModel):
    """`GET /v1/porchlamp/leaderboard` envelope: rows plus paging and scope."""

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
    """`GET /v1/porchlamp/not-ranked` envelope: `gated`, `unrated`, and `persona_hidden`
    (rows balanced-rated but excluded by the active persona's gate — populated
    only when called with a non-balanced `persona`)."""

    gated: list[NotRankedRow]
    unrated: list[NotRankedRow]
    persona_hidden: list[NotRankedRow] = []
    counts: SnapshotCounts
    #: True when the store held more not-ranked rows than this fetch's cap, so
    #: the three row lists below are a truncated page while `counts` is still
    #: the full, authoritative figure.
    truncated: bool = False

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
    """One scatter marker for the 2D matrix (Screen B)."""

    owner: str
    name: str
    ecosystem: str
    leverage: float
    friction: float
    leverage_coverage: float
    friction_coverage: float
    quadrant: Literal["Sweet Spot", "Crucible", "Nursery", "Graveyard"]
    stars_audited: int | None
    porchlamp: float
    coverage: float
    tier: str | None


class MatrixAxisCoverage(StrictModel):
    """One axis's (`leverage`/`friction`) worst-missing contributing pillar
    and metrics. `axis_partial_count` and `axis_partial_share` quantify the
    fraction of plotted rows where `pillar` was unmeasured (so the axis
    coordinate for that row is partially measured)."""

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
    """`GET /v1/porchlamp/matrix`. `excluded_count`: the store's own count of
    repositories excluded from plotting, reported unchanged.
    `unrenderable_count`: rows the store listed as plottable that the API
    could not convert to `MatrixPoint` — kept separate so each number has
    exactly one meaning. `axis_coverage`: coverage statistics per axis
    (`leverage` / `friction`) for missing contributing pillars."""

    points: list[MatrixPoint]
    excluded_count: int
    unrenderable_count: int = 0
    axis_coverage: dict[str, MatrixAxisCoverage] = {}

    @model_validator(mode="after")
    def _excluded_count_is_non_negative(self) -> "MatrixResponse":
        if self.excluded_count < 0:
            raise ValueError(f"excluded_count must be >= 0, got {self.excluded_count}")
        if self.unrenderable_count < 0:
            raise ValueError(f"unrenderable_count must be >= 0, got {self.unrenderable_count}")
        for axis in self.axis_coverage:
            if axis not in ("leverage", "friction"):
                raise ValueError(f"axis_coverage key must be 'leverage' or 'friction', got {axis!r}")
        return self


class ReprojectRequest(StrictModel):
    """`POST /v1/porchlamp/reproject`: a custom weight vector over the five
    pillars (labelled "custom" everywhere, never called a persona).
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
    """One `ReprojectResponse` row: `LeaderboardRow` fields representing
    the custom projection (`rank`, `porchlamp`, `coverage`, `tier`, `tie_group`),
    alongside the five stored balanced benchmark values for comparative delta display."""

    balanced_rank: int | None
    balanced_tie_group: int | None
    balanced_porchlamp: float | None
    balanced_coverage: float | None
    balanced_tier: str | None


class ReprojectResponse(StrictModel):
    """Rows re-projected under a custom weight vector — `ReprojectRow`
    (`LeaderboardRow` plus the balanced-comparison fields), `lens` is
    always the literal `"custom"` (U-11). `page`/`page_size` state which
    slice of the `total` eligible rows `rows` is, so a client can tell a
    page from the whole set."""

    rows: list[ReprojectRow]
    total: int
    lens: Literal["custom"] = "custom"
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE


# --------------------------------------------------------------------------- #
# GET /v1/porchlamp/methodology models mirroring `porchlamp.core.methodology.MethodologyDocument`
# and `porchlamp.audit.methodology_history.methodology_with_history` while keeping  #
# this contract free of core/audit runtime imports.                           #
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
    #: TypedDict's own `dict[str, object]` (`porchlamp.core.methodology._value_domain`).
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
    """One row per `porchlamp.verify.detectors.DETECTOR_NAMES` entry -- present
    only on the extended document (`methodology_with_history`); the pure
    `methodology_document()` never populates this list (`porchlamp.core`'s
    import boundary keeps `porchlamp.verify` out of `porchlamp.core`)."""

    name: str
    trigger: str
    direction: Literal["magnitude", "above", "below", "compound", "boolean"]
    thresholds: dict[str, Any] = {}


class MethodologyProcessConstant(StrictModel):
    """One of the three audit-only release-gate constants
    (`porchlamp.audit.anchors`/`porchlamp.audit.sensitivity`) that `constants` itself
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
    """`GET /v1/porchlamp/methodology`: the full extended document
    (`methodology_with_history`'s shape) -- pillars, parameters, gates,
    missingness, tie-break levels and personas are always present;
    `detectors`, `not_hashed_process_constants` and `rfc_log` are the three
    additions the extended document carries beyond the pure
    `methodology_document()` (`06` §3 F: "the published methodology
    ... rendered from the frozen constants module so it cannot drift from
    the code", plus the detector/process-constant/RFC-log facts that live
    just outside `porchlamp.core`'s import boundary)."""

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
    "BlackHoleCountedPR",
    "BlackHoleExcludedCounts",
    "BlackHoleEvidence",
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
