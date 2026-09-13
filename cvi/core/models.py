"""CVI v2 core contracts (04 section 3).

Measured/Unmeasured parameter values, the raw envelope, the v2 FeatureBundle
and the ScoreRecord the engine emits. Pure dataclasses plus structural
validation: no I/O, no clocks, and no import outside ``cvi.core`` plus the
standard library (enforced by tests/core/test_import_graph.py).

On-disk load / validate / dump lives in ``cvi.derive.bundle``; this module
owns the shapes, the invariants and the two hash primitives the provenance
chain is built from.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
from dataclasses import dataclass, fields
from typing import Any, Literal, Mapping

from cvi.core import constants_v2

#: Envelope hash / provenance hash shape: 64 lowercase hex digits.
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class BundleValidationError(Exception):
    """A document or FeatureBundle violates the v2 schema.

    ``problems`` carries every violation found, in deterministic order, so a
    caller fixes them all at once instead of iterating."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("; ".join(problems))


def canonical_json(value: object) -> str:
    """Canonical JSON for JSON-native values: sorted keys, no insignificant space.

    Byte-identical to ``cvi.collect.envelope.canonical_json`` on values
    decoded from JSON (that encoder additionally lowers enums, datetimes and
    sets, which v2 documents do not carry)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    """SHA-256 hex digest of ``text`` encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def feature_provenance_hash(referenced: Mapping[str, str]) -> str:
    """Provenance hash of a bundle: the hash of all referenced envelope hashes.

    04 section 3: ``sha256`` of the canonical JSON of the list of
    ``{"source": path, "source_hash": hex}`` pairs, sorted by source path."""
    pairs = [{"source": source, "source_hash": referenced[source]} for source in sorted(referenced)]
    return sha256_hex(canonical_json(pairs))


def _is_iso(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def _optional_nonempty_str(value: Any) -> bool:
    return value is None or _nonempty_str(value)


def measured_problems(value: Measured) -> list[str]:
    """Field-level invariants of one Measured value."""
    problems: list[str] = []
    if isinstance(value.value, bool) or not isinstance(value.value, (int, float, str)):
        problems.append(f"measured.value must be int/float/str, got {type(value.value).__name__}")
    if not _is_int(value.n) or value.n < 0:
        problems.append(f"measured.n must be a non-negative int, got {value.n!r}")
    for name in ("window_start", "window_end"):
        field = getattr(value, name)
        if not _optional_nonempty_str(field):
            problems.append(f"measured.{name} must be an ISO date string or null")
    if value.method != constants_v2.METHOD_PRIMARY and not (
        value.method.startswith(constants_v2.PROXY_METHOD_PREFIX)
        and len(value.method) > len(constants_v2.PROXY_METHOD_PREFIX)
    ):
        problems.append(f"measured.method must be 'primary' or 'proxy:<name>', got {value.method!r}")
    if not _nonempty_str(value.source):
        problems.append("measured.source must be a non-empty envelope path")
    if not _HEX64.match(value.source_hash):
        problems.append(f"measured.source_hash must be 64 lowercase hex digits, got {value.source_hash!r}")
    if not _is_iso(value.fetched_at):
        problems.append(f"measured.fetched_at must be an ISO datetime, got {value.fetched_at!r}")
    return problems


def unmeasured_problems(value: Unmeasured) -> list[str]:
    """Field-level invariants of one Unmeasured value."""
    problems: list[str] = []
    if value.reason not in constants_v2.UNMEASURED_REASONS:
        problems.append(f"unmeasured.reason must be one of {constants_v2.UNMEASURED_REASONS}, got {value.reason!r}")
    if not isinstance(value.detail, str):
        problems.append(f"unmeasured.detail must be a string, got {type(value.detail).__name__}")
    return problems


@dataclass(frozen=True)
class Measured:
    """A parameter input that was measured (04 section 3, D-MISS-1).

    Provenance is an envelope path plus the hash of the envelope body (R-4):
    nothing about where the value came from is asserted without a path and a
    hash. ``n`` is the sample size behind the value (0 for scalars)."""

    value: float | int | str
    n: int
    window_start: str | None
    window_end: str | None
    method: str
    source: str
    source_hash: str
    fetched_at: str

    def __post_init__(self) -> None:
        problems = measured_problems(self)
        if problems:
            raise BundleValidationError(problems)


@dataclass(frozen=True)
class Unmeasured:
    """A parameter input that is not measured, with the D-MISS-1 reason.

    Repo-controlled absences are measured zeros, not Unmeasured; Unmeasured
    means a third-party gap, a collector failure, a sample below the minimum
    n, a parameter that does not apply to this repo, or (added 2026-09-12)
    an ECDF-normalised parameter with no rated, measured repository to build
    a population from in its ecosystem or globally at this snapshot --
    ``reason="no_population"``. ``no_population`` is not produced by this
    module's own validation (a value that reaches this dataclass was never
    measured in the first place, regardless of populations); the scoring
    engine (``cvi.core.engine``, stage 2) is the one caller that assigns it,
    to a stage-2 ``ParamScore`` rather than to an input-stage ``Unmeasured``
    -- listed in ``constants_v2.UNMEASURED_REASONS`` when that module's own
    frozen vocabulary is next revised (R-2: kept here only in this type's
    documented taxonomy, per 02 section 5's D-MISS-1 amendment)."""

    reason: Literal[
        "third_party_gap", "collector_failure", "insufficient_n", "not_applicable", "no_population"
    ]
    detail: str = ""

    def __post_init__(self) -> None:
        problems = unmeasured_problems(self)
        if problems:
            raise BundleValidationError(problems)


#: A parameter input is either a measurement or a documented absence.
ParamValue = Measured | Unmeasured


def param_value_problems(value: Any, input_id: str) -> list[str]:
    problems: list[str] = []
    if not isinstance(value, (Measured, Unmeasured)):
        problems.append(f"{input_id}: not a Measured/Unmeasured value")
    elif isinstance(value, Measured):
        problems.extend(f"{input_id}: {p}" for p in measured_problems(value))
    else:
        problems.extend(f"{input_id}: {p}" for p in unmeasured_problems(value))
    return problems


@dataclass(frozen=True)
class Envelope:
    """One raw fetch, the unit of provenance (04 section 3, 03 section 5).

    Contract shape only: collectors write envelopes, ``cvi.derive`` reads
    them; no I/O lives in this module."""

    source_url: str
    fetched_at: str
    http_status: int
    etag: str | None
    sha256: str
    collector_version: str
    data: Any

    def __post_init__(self) -> None:
        problems: list[str] = []
        if not _nonempty_str(self.source_url):
            problems.append("envelope.source_url must be a non-empty URL")
        if not _is_iso(self.fetched_at):
            problems.append(f"envelope.fetched_at must be an ISO datetime, got {self.fetched_at!r}")
        if not _is_int(self.http_status):
            problems.append(f"envelope.http_status must be an int, got {self.http_status!r}")
        if not _HEX64.match(self.sha256):
            problems.append("envelope.sha256 must be 64 lowercase hex digits")
        if not _nonempty_str(self.collector_version):
            problems.append("envelope.collector_version must be a non-empty string")
        if problems:
            raise BundleValidationError(problems)


@dataclass(frozen=True)
class RepoIdentity:
    """Repo identity block of a FeatureBundle (04 section 3)."""

    owner: str
    name: str
    ecosystem: str
    archived: bool
    mirror_url: str | None
    fork: bool
    license_spdx: str | None
    default_branch: str | None

    def __post_init__(self) -> None:
        problems: list[str] = []
        for name in ("owner", "name", "ecosystem"):
            if not _nonempty_str(getattr(self, name)):
                problems.append(f"repo.{name} must be a non-empty string")
        if not isinstance(self.archived, bool):
            problems.append(f"repo.archived must be a bool, got {self.archived!r}")
        if not isinstance(self.fork, bool):
            problems.append(f"repo.fork must be a bool, got {self.fork!r}")
        for name in ("mirror_url", "license_spdx", "default_branch"):
            if not _optional_nonempty_str(getattr(self, name)):
                problems.append(f"repo.{name} must be a string or null")
        if problems:
            raise BundleValidationError(problems)


@dataclass(frozen=True)
class PRWindowEvent:
    """One PR inside the 180-day window (04 section 3).

    Review-thread detail (typed actors, review bodies) is the W1-3 contract;
    this event carries the window fields every PR-based parameter needs."""

    pr_number: int
    created_at: str
    closed_at: str | None
    merged_at: str | None
    author_login: str
    author_association: str
    is_external: bool
    first_human_review_at: str | None

    def __post_init__(self) -> None:
        problems: list[str] = []
        if not _is_int(self.pr_number) or self.pr_number < 1:
            problems.append(f"event.pr_number must be an int >= 1, got {self.pr_number!r}")
        if not _is_iso(self.created_at):
            problems.append(f"event.created_at must be an ISO datetime, got {self.created_at!r}")
        for name in ("closed_at", "merged_at", "first_human_review_at"):
            if not _optional_nonempty_str(getattr(self, name)):
                problems.append(f"event.{name} must be an ISO datetime or null")
        if not _nonempty_str(self.author_login):
            problems.append("event.author_login must be a non-empty string")
        if not _nonempty_str(self.author_association):
            problems.append("event.author_association must be a non-empty string")
        if not isinstance(self.is_external, bool):
            problems.append(f"event.is_external must be a bool, got {self.is_external!r}")
        if problems:
            raise BundleValidationError(problems)


@dataclass(frozen=True)
class PRWindow:
    """The 180-day PR window: bounds, truncation flag, events (04 section 3)."""

    start: str | None
    end: str | None
    truncated: bool
    events: tuple[PRWindowEvent, ...]

    def __post_init__(self) -> None:
        problems: list[str] = []
        if not isinstance(self.truncated, bool):
            problems.append(f"pr_window.truncated must be a bool, got {self.truncated!r}")
        has_events = len(self.events) > 0
        if has_events and (self.start is None or self.end is None):
            problems.append("pr_window.start/end must be set when events are present")
        if not has_events and (self.start is not None or self.end is not None):
            problems.append("pr_window.start/end must be null when no events are present")
        if not isinstance(self.events, tuple) or not all(
            isinstance(event, PRWindowEvent) for event in self.events
        ):
            problems.append("pr_window.events must be a tuple of PRWindowEvent")
        if (
            has_events
            and isinstance(self.start, str)
            and isinstance(self.end, str)
            and _is_iso(self.start)
            and _is_iso(self.end)
        ):
            start = datetime.datetime.fromisoformat(self.start.replace("Z", "+00:00"))
            end = datetime.datetime.fromisoformat(self.end.replace("Z", "+00:00"))
            if start > end:
                problems.append(f"pr_window.start {self.start!r} is after end {self.end!r}")
        if problems:
            raise BundleValidationError(problems)


@dataclass(frozen=True)
class PRRef:
    """A PR reference inside a cohort history: number plus the times it matters."""

    number: int
    created_at: str
    merged_at: str | None

    def __post_init__(self) -> None:
        problems: list[str] = []
        if not _is_int(self.number) or self.number < 1:
            problems.append(f"pr_ref.number must be an int >= 1, got {self.number!r}")
        if not _is_iso(self.created_at):
            problems.append(f"pr_ref.created_at must be an ISO datetime, got {self.created_at!r}")
        if not _optional_nonempty_str(self.merged_at):
            problems.append("pr_ref.merged_at must be an ISO datetime or null")
        if problems:
            raise BundleValidationError(problems)


@dataclass(frozen=True)
class NewcomerAuthor:
    """One sampled newcomer-cohort author with their PR history (04 section 3)."""

    login: str
    first_pr: PRRef
    followups: tuple[PRRef, ...]

    def __post_init__(self) -> None:
        problems: list[str] = []
        if not _nonempty_str(self.login):
            problems.append("newcomer_author.login must be a non-empty string")
        if not isinstance(self.first_pr, PRRef):
            problems.append("newcomer_author.first_pr must be a PRRef")
        if not isinstance(self.followups, tuple) or not all(isinstance(p, PRRef) for p in self.followups):
            problems.append("newcomer_author.followups must be a tuple of PRRef")
        if problems:
            raise BundleValidationError(problems)


@dataclass(frozen=True)
class Cohorts:
    """Decision cohort and sampled newcomer cohort (04 section 3)."""

    decision_pr_numbers: tuple[int, ...]
    newcomer_authors: tuple[NewcomerAuthor, ...]

    def __post_init__(self) -> None:
        problems: list[str] = []
        if not isinstance(self.decision_pr_numbers, tuple) or not all(
            _is_int(n) and n >= 1 for n in self.decision_pr_numbers
        ):
            problems.append("cohorts.decision_pr_numbers must be a tuple of ints >= 1")
        if not isinstance(self.newcomer_authors, tuple) or not all(
            isinstance(a, NewcomerAuthor) for a in self.newcomer_authors
        ):
            problems.append("cohorts.newcomer_authors must be a tuple of NewcomerAuthor")
        if problems:
            raise BundleValidationError(problems)


def _repo_problems(repo: RepoIdentity) -> list[str]:
    problems: list[str] = []
    if not isinstance(repo, RepoIdentity):
        problems.append("repo must be a RepoIdentity")
    else:
        for name in constants_v2.REPO_IDENTITY_FIELDS:
            if name not in {f.name for f in fields(repo)}:
                problems.append(f"repo missing field {name}")
    return problems


def _window_problems(window: PRWindow) -> list[str]:
    return [] if isinstance(window, PRWindow) else ["pr_window must be a PRWindow"]


def _cohorts_problems(cohorts: Cohorts) -> list[str]:
    return [] if isinstance(cohorts, Cohorts) else ["cohorts must be a Cohorts"]


@dataclass
class FeatureBundle:
    """One repo's v2 feature bundle: data/derived/<key>.json (04 section 3).

    ``inputs`` is keyed by the 18 parameter input ids; every key must be
    present as a Measured or an Unmeasured value. ``provenance_hash`` covers
    every envelope the bundle references (see :func:`feature_provenance_hash`)."""

    schema_version: int
    repo: RepoIdentity
    inputs: dict[str, ParamValue]
    pr_window: PRWindow
    cohorts: Cohorts
    provenance_hash: str

    def validate(self) -> None:
        """Check the full v2 schema against this bundle; raise on violation."""
        problems: list[str] = []
        if not _is_int(self.schema_version) or self.schema_version != constants_v2.SCHEMA_VERSION:
            problems.append(f"schema_version must be {constants_v2.SCHEMA_VERSION}, got {self.schema_version!r}")
        problems.extend(_repo_problems(self.repo))
        expected = set(constants_v2.PARAM_INPUT_IDS)
        missing = sorted(expected - set(self.inputs))
        extra = sorted(set(self.inputs) - expected)
        problems.extend(f"inputs missing key {key!r}" for key in missing)
        problems.extend(f"inputs has unexpected key {key!r}" for key in extra)
        for input_id in sorted(set(self.inputs) & expected):
            problems.extend(param_value_problems(self.inputs[input_id], input_id))
        problems.extend(_window_problems(self.pr_window))
        problems.extend(_cohorts_problems(self.cohorts))
        if not _HEX64.match(self.provenance_hash):
            problems.append(f"provenance_hash must be 64 lowercase hex digits, got {self.provenance_hash!r}")
        if problems:
            raise BundleValidationError(problems)


@dataclass(frozen=True)
class GateResult:
    """Stage-0 gate outcome (04 section 3): eligible, reason, routed, undecided gates."""

    eligible: bool
    reason: str | None
    routed: str | None
    undecided: tuple[str, ...]


@dataclass(frozen=True)
class ParamScore:
    """One scored parameter (04 section 3): score, measured flag, reason, n, method."""

    score: float | None
    measured: bool
    reason: str | None
    n: int
    method: str | None


@dataclass(frozen=True)
class PillarScore:
    """One scored pillar (04 section 3): renormalised score and coverage."""

    score: float | None
    coverage: float


@dataclass(frozen=True)
class PersonaComposite:
    """One persona's re-projected composite (04 section 3; ruling
    2026-09-13, `PersonaComposite(cvi, coverage, tier, rated)`).

    Returned by ``cvi.core.persona.persona_composite`` and stored under
    ``ScoreRecord.persona_composites[persona]``. ``coverage`` and ``tier``
    are that persona's *own* weighted projection (D-MISS-3/D-MAT-2 applied
    under the persona's preset weights, not the balanced ones); ``rated`` is
    that persona-weighted coverage's own D-MISS-3 rated/unrated outcome
    (>= 0.60), computed independently of ``ScoreRecord.tier``/``coverage`` --
    04 section 6's per-persona-rated ruling: a repo rated under the balanced
    weights can be unrated under a persona's weights, and vice versa, so
    ``rated`` here must never be copied from the balanced record."""

    cvi: float | None
    coverage: float
    tier: str | None
    rated: bool


@dataclass(frozen=True)
class ScoreRecord:
    """The pure scoring output for one repo at one snapshot (04 section 3).

    ``repo`` is copied from the scored ``FeatureBundle.repo`` by
    :func:`cvi.core.engine.score` (04 section 3).

    ``benchmark_version`` plus ``constants_hash`` let any reader re-derive
    which benchmark a row came from (D-VER-2/D-VER-3); field validation lands
    with the packets that populate each stage (W2-2, W2-5, W2-6, W2-7).

    ``gate`` is always the unmodified stage-0 :class:`GateResult` (04
    section 3): whether the repo went on to be rated is carried by
    ``rated``/``unrated_reason`` instead, so a stage-0 gate failure
    (``gate.eligible=False``) and a stage-3 D-EVID-1 evidence failure
    (``gate.eligible=True``, ``rated=False``) are always distinguishable
    from the record alone, matching the store schema's separate
    ``eligible``/``gate_reason``/``rated``/``unrated_reason`` columns (04
    section 5; W2-5 review W-1). ``tier_ceiling`` is the D-MISS-3 coverage
    ceiling (``None``, ``"B"`` or ``"A"``) that capped -- or would have
    capped -- ``tier``; ``leverage_coverage``/``friction_coverage`` are each
    D-MAT-1 coordinate's own measured-weight share, additive alongside
    ``leverage``/``friction`` (04 section 3 names no such fields, but
    ``cvi.core.matrix.Coordinates`` already carries them -- W2-5 review
    W-1(c)). ``raw_hash`` is whatever the caller of
    :func:`cvi.core.engine.score` supplied (recommended: a hash of the
    whole bundle document); ``provenance_hash`` is always
    ``FeatureBundle.provenance_hash`` (hash of the referenced envelope
    hashes only) kept as its own fact so the two are never conflated
    (W2-5 review W-1)."""

    repo: RepoIdentity
    snapshot_id: str
    benchmark_version: str
    constants_hash: str
    raw_hash: str
    gate: GateResult
    params: dict[str, ParamScore]
    pillars: dict[str, PillarScore]
    composite: float | None
    coverage: float
    tier: str | None
    leverage: float | None
    friction: float | None
    quadrant: str | None
    persona_gates: dict[str, str]
    persona_composites: dict[str, PersonaComposite]
    tie_key: tuple
    tie_group: int | None
    rank: int | None
    suspicion: list[str]
    rated: bool = False
    unrated_reason: str | None = None
    tier_ceiling: str | None = None
    leverage_coverage: float = 0.0
    friction_coverage: float = 0.0
    provenance_hash: str = ""
