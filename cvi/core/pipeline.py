"""CVI v2 snapshot scorer: the whole-population composition point (packet
W2-8b; ``docs/v2/05-WORK-PLAN.md`` packets W2-8/W3-4 folded in).

``cvi.core.engine.score`` is pure and per-repository: it runs D-ORDER-1
stages 0-7 (``02`` section 1) and leaves ``persona_gates``,
``persona_composites``, ``tie_key``, ``tie_group`` and ``rank`` empty,
because stages 8 (persona lens, ``cvi.core.persona``) and 9 (tie-breaks and
rank, ``cvi.core.tiebreak``) both need the *whole* scored population, not one
record at a time. Nothing before this packet calls ``persona.py`` or
``tiebreak.py`` from a real per-snapshot driver -- :func:`score_snapshot` is
that driver.

Pure, no I/O, no clock (04 section 8): every timestamp (``as_of``) and every
per-repo fact this module cannot derive from a :class:`~cvi.core.models.
FeatureBundle` alone (``gate_evidence``, ``raw_hash``) is supplied by the
caller. Building those inputs from raw collector data (for a real snapshot,
or for the frozen fixture this packet also ships) is the caller's job --
:mod:`scripts.build_fixture_snapshot` is one such caller.

Stage order implemented here, each step a small named function so it is
testable alone (05 section 0.4 R-1's fail-before/over-correction guards need
that):

- :func:`score_bundle_stages_0_7` -- stages 0-7, delegating entirely to
  :func:`cvi.core.engine.score` (this module never re-implements a scoring
  rule; engine.score's signature is the source of truth for what a snapshot
  driver must supply per repo: a :class:`~cvi.core.engine.Snapshot`, i.e.
  ``snapshot_id`` + ``as_of`` shared across the whole call, and
  ``gate_evidence`` varying per repo -- see the module's own docstring on
  why ``gate_evidence`` is inherently per-repo).
- :func:`score_bundle_stage_8_persona` -- stage 8: ``persona.persona_gates``
  and ``persona.persona_composite`` for every preset, folded onto the record
  with ``dataclasses.replace`` (never mutated -- W2-7f's purity test is the
  model this module follows: see the docstring below for the two cases
  ``persona.py``'s own documented contract forbids calling it plainly on).
- :func:`score_bundle_stage_9_tie_input` -- prepares the
  :class:`~cvi.core.tiebreak.TieBreakInput` evidence
  :class:`~cvi.core.models.ScoreRecord` does not itself carry (repo identity,
  TTFHR p75, last non-bot commit date); ranking itself
  (:func:`cvi.core.tiebreak.assign_ranks`) runs once, over the whole
  population, inside :func:`score_snapshot`.
- :func:`build_snapshot_counts` -- rated/gated/unrated counts plus
  per-persona visible counts, over the final ranked records.
- :func:`canonical_records_json` / :func:`records_hash_of` -- the
  reproducibility handle (03 section 5, W3-4): a canonical JSON
  serialisation of the final record tuple (sorted keys, tuples as lists,
  floats via Python's default ``repr``-based float formatting), hashed with
  SHA-256. Field order and dict-insertion order never change it (each
  dataclass becomes a ``{field name: value}`` dict, and
  ``cvi.core.models.canonical_json`` sorts every dict's keys).

Stage 8 on a gated or unrated record (D-PERS-1/2/3, ``02`` section 9). Two
cases ``persona.py``'s own contract does not let a caller treat like a
normal rated record, both handled by :func:`score_bundle_stage_8_persona`:

- ``record.gate.eligible is False`` (a stage-0 gate failure): ``pillars`` is
  ``{}`` (``cvi.core.engine._bare_record``), and
  ``persona.persona_composite`` *raises* ``ValueError`` on an incomplete
  pillar set by design (``tests/core/test_persona_v2.py::
  test_persona_composite_raises_when_pillars_incomplete``: "no balanced
  score exists to reproject, so this is a programming error to catch
  loudly, not a silent (None, 0.0, None)"). ``persona_composites`` is
  therefore ``{}`` here, matching what ``cvi.core.engine`` itself already
  leaves on a gated record.
- ``record.gate.eligible is True`` and ``record.rated is False`` (a stage-3
  D-EVID-1 evidence failure): ``pillars`` *is* the full five-name dict
  (``cvi.core.aggregate.aggregate_pillars`` always returns one), so
  ``persona.persona_composite`` can be called for every preset without
  raising, and is.

In both cases this packet's own instructions (not ``persona.py``'s, which
never mentions the global gate/unrated status at all -- it only ever
computes the four D-PERS-2 evidence-based reasons) require
``persona_gates`` to state the *balanced* gate/unrated reason for every one
of the four :data:`~cvi.core.persona.GATED_PERSONAS`, overriding whatever
``persona.persona_gates`` would have said from raw evidence alone. Why: a
repository with no rank and no tier (02 section 2: "listed, not ranked") has
nothing for *any* persona lens to rank either, and a persona-specific
evidence reason can actively mislead here -- an archived repo with a
healthy, ``Measured`` elephant factor >= 3 would otherwise get *no* entry
for ``senior_systems`` in ``persona.persona_gates``'s literal output (it
only rejects on a low measured EF), which would wrongly suggest that lens
shows it. Stating the one shared reason on all four keeps this consistent
with D-ORDER-1 (gate/evidence status is decided at stages 0/3, strictly
before stage 8) and reuses a fact the record already carries (R-2) instead
of inventing new vocabulary. This is a deliberate design choice this
packet's dispatch mandated in those exact words ("persona_gates must state
the balanced gate/unrated reason for them"); it is flagged again in the
handoff because it interacts with `04` section 6's store-layer ruling that a
persona's own re-weighted coverage may rate a repository the *balanced*
weights left unrated -- that SQL projection (W4-3) is free to recompute its
own per-persona ``rated`` flag independently of this module's
``persona_gates`` override, which only ever governs this Python
composition's own ``ScoreRecord.persona_gates`` field.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import datetime
from typing import Any, Mapping, Sequence

from cvi.core import constants_v2, persona
from cvi.core.engine import PopulationLookup, Snapshot, score
from cvi.core.gates import GateEvidence
from cvi.core.models import (
    FeatureBundle,
    PersonaComposite,
    ScoreRecord,
    canonical_json,
    sha256_hex,
)
from cvi.core.tiebreak import TieBreakInput, assign_ranks, ttfhr_p75_from_pr_window

#: A repository key, ``"owner/name"`` (exact case: :class:`~cvi.core.models.
#: RepoIdentity` never normalises case, and neither does this module -- the
#: same convention :func:`cvi.core.tiebreak._name_key` lowercases only for
#: sort comparison, never for identity). No existing type alias for this
#: shape was found anywhere in the codebase (``grep -rn "RepoKey"`` before
#: this packet returns nothing); this is this module's own definition.
RepoKey = str


def repo_key(bundle: FeatureBundle) -> RepoKey:
    """The repository key a caller's ``gate_evidence``/``raw_hashes``
    mappings (and this module's own :class:`SnapshotCounts`) are keyed by."""
    return f"{bundle.repo.owner}/{bundle.repo.name}"


# --------------------------------------------------------------------------- #
# Stage 0-7: delegate to cvi.core.engine.score verbatim.
# --------------------------------------------------------------------------- #
def score_bundle_stages_0_7(
    bundle: FeatureBundle,
    populations: PopulationLookup,
    *,
    snapshot_id: str,
    as_of: datetime,
    gate_evidence: GateEvidence,
    raw_hash: str,
) -> ScoreRecord:
    """D-ORDER-1 stages 0-7 for one bundle: ``cvi.core.engine.score`` exactly
    (this module never re-implements a scoring rule -- engine.score's own
    signature is the source of truth for what it needs per repo)."""
    snapshot = Snapshot(snapshot_id=snapshot_id, as_of=as_of, gate_evidence=gate_evidence)
    return score(bundle, populations, snapshot, raw_hash)


# --------------------------------------------------------------------------- #
# Stage 8: persona gates and persona composites (D-PERS-1/2/3, 02 section 9).
# --------------------------------------------------------------------------- #
def score_bundle_stage_8_persona(record: ScoreRecord, bundle: FeatureBundle) -> ScoreRecord:
    """Stage 8 for one already stage-0-7-scored record: fold in
    ``persona_gates``/``persona_composites`` via ``dataclasses.replace``
    (``ScoreRecord`` is frozen; never mutated -- see the module docstring
    for the two gated/unrated cases and why their ``persona_gates`` state
    the balanced reason rather than calling ``persona.persona_gates``
    as-is)."""
    if not record.gate.eligible:
        gates = {p: record.gate.reason for p in persona.GATED_PERSONAS}
        composites: dict[str, PersonaComposite] = {}
    elif not record.rated:
        gates = {p: record.unrated_reason for p in persona.GATED_PERSONAS}
        composites = {p: persona.persona_composite(record, p) for p in persona.PERSONA_PRESETS}
    else:
        gates = persona.persona_gates(record, bundle.inputs)
        composites = {p: persona.persona_composite(record, p) for p in persona.PERSONA_PRESETS}
    return replace(record, persona_gates=gates, persona_composites=composites)


# --------------------------------------------------------------------------- #
# Stage 9 prep: the tie-break evidence a ScoreRecord does not itself carry.
# --------------------------------------------------------------------------- #
def score_bundle_stage_9_tie_input(record: ScoreRecord, bundle: FeatureBundle) -> TieBreakInput:
    """Build one :class:`~cvi.core.tiebreak.TieBreakInput`.

    ``owner``/``name`` come from ``record.repo`` (W2-10: ``ScoreRecord`` now
    carries the scored bundle's ``RepoIdentity``, set by
    ``cvi.core.engine.score`` itself, and ``TieBreakInput.__post_init__``
    rejects an ``owner``/``name`` pair that disagrees with
    ``record.repo``). ``record.repo`` and ``bundle.repo`` are always the
    same identity by construction (``engine.score`` copies one from the
    other), so reading from either gives the same value; ``record.repo`` is
    used because it is the field the current ``TieBreakInput`` contract
    actually validates against. ``ttfhr_p75_hours`` is computed for real from
    ``bundle.pr_window`` via :func:`cvi.core.tiebreak.
    ttfhr_p75_from_pr_window`. ``last_non_bot_commit_at`` is always ``None``:
    neither ``FeatureBundle`` nor ``GateEvidence`` carries a dated last-commit
    field anywhere (04 section 3 has no such field; W1-5's
    ``GateEvidence.vitality_commits_180d`` is a trailing-180-day *count*, not
    a date) -- ``tiebreak.py``'s own docstring already names this as an
    ownership request to W2-8, unresolved as of this packet's base commit;
    recorded again as a COULD NOT VERIFY line in the handoff, not invented
    here."""
    return TieBreakInput(
        record=record,
        owner=record.repo.owner,
        name=record.repo.name,
        ttfhr_p75_hours=ttfhr_p75_from_pr_window(bundle.pr_window),
        last_non_bot_commit_at=None,
    )


# --------------------------------------------------------------------------- #
# ScoredSnapshot: the whole-population output.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SnapshotCounts:
    """Rated/gated/unrated counts plus per-persona visible counts, over the
    final ranked :class:`~cvi.core.models.ScoreRecord` tuple.

    ``persona_visible`` covers every key of ``persona.PERSONA_PRESETS``
    (the five presets, ``"balanced"`` included, matching
    ``persona_composites``' own key set -- see :func:`score_bundle_stage_8_
    persona`). ``"balanced"``'s count is simply the ``rated`` count (D-PERS-1:
    the balanced view is never itself a gate target, so nothing hides a
    rated record from it); every other persona's count is the number of
    records for which that persona is absent from ``persona_gates`` *and*
    that persona's own :class:`~cvi.core.models.PersonaComposite` is
    ``rated`` (04 section 6's per-persona-rated ruling: a persona's
    visibility is never read off the balanced ``rated`` flag)."""

    rated: int
    gated: int
    unrated: int
    persona_visible: Mapping[str, int]


@dataclass(frozen=True)
class ScoredSnapshot:
    """The whole-population output of :func:`score_snapshot`.

    ``records`` is in final display order: rated records first by strict
    rank 1..N, then gated/unrated records ordered by lowercase
    ``owner/name`` (D-TIE-2) -- exactly ``cvi.core.tiebreak.assign_ranks``'s
    own documented order, carried through unchanged. ``records_hash`` is
    the reproducibility handle (03 section 5, W3-4): sha256 of
    :func:`canonical_records_json` applied to ``records`` in that same
    order, so a shuffle of the *input* ``bundles`` sequence to
    :func:`score_snapshot` can never change it (the whole population is
    re-sorted deterministically before hashing), while any change to any one
    record's content does."""

    snapshot_id: str
    benchmark_version: str
    constants_hash: str
    records: tuple[ScoreRecord, ...]
    counts: SnapshotCounts
    records_hash: str


def build_snapshot_counts(records: Sequence[ScoreRecord]) -> SnapshotCounts:
    """:class:`SnapshotCounts` over a final, ranked record sequence."""
    rated = sum(1 for record in records if record.rated)
    gated = sum(1 for record in records if not record.gate.eligible)
    unrated = sum(1 for record in records if record.gate.eligible and not record.rated)
    visible: dict[str, int] = {}
    for name in persona.PERSONA_PRESETS:
        if name == persona.PERSONA_BALANCED:
            visible[name] = rated
            continue
        visible[name] = sum(
            1
            for record in records
            if name not in record.persona_gates
            and record.persona_composites.get(name) is not None
            and record.persona_composites[name].rated
        )
    return SnapshotCounts(rated=rated, gated=gated, unrated=unrated, persona_visible=visible)


# --------------------------------------------------------------------------- #
# records_hash: canonical JSON over the final record tuple.
# --------------------------------------------------------------------------- #
def _to_plain(value: Any) -> Any:
    """Recursively turn dataclasses/mappings/tuples into
    dict/dict/list, the JSON-native shapes ``cvi.core.models.canonical_json``
    accepts. A dataclass becomes ``{field name: value}`` (never a
    positional list), so re-ordering a dataclass's declared fields cannot
    change the serialised bytes -- ``canonical_json`` sorts every dict's
    keys besides. Every remaining leaf (``int``/``float``/``str``/``bool``/
    ``None``) is returned unchanged; Python's ``json`` module already
    formats a ``float`` with its ``repr`` (the shortest round-tripping
    form), which is what "floats as repr" in the packet's own words means."""
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _to_plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(item) for item in value]
    return value


def canonical_records_json(records: Sequence[ScoreRecord]) -> str:
    """Canonical JSON of ``records``, in the given order (sorted keys,
    compact separators, tuples as lists, floats as repr -- see
    :func:`_to_plain`). The reproducibility handle's input text; hash it
    with :func:`records_hash_of` or ``cvi.core.models.sha256_hex`` directly."""
    return canonical_json([_to_plain(record) for record in records])


def records_hash_of(records: Sequence[ScoreRecord]) -> str:
    """sha256 of :func:`canonical_records_json` applied to ``records``."""
    return sha256_hex(canonical_records_json(records))


# --------------------------------------------------------------------------- #
# score_snapshot: the whole driver.
# --------------------------------------------------------------------------- #
def _validated_keys(
    bundles: Sequence[FeatureBundle],
    gate_evidence: Mapping[RepoKey, GateEvidence],
    raw_hashes: Mapping[RepoKey, str],
) -> list[RepoKey]:
    keys = [repo_key(bundle) for bundle in bundles]
    seen: set[RepoKey] = set()
    duplicates: set[RepoKey] = set()
    for key in keys:
        (duplicates if key in seen else seen).add(key)
    problems: list[str] = []
    if duplicates:
        problems.append(f"duplicate repo key(s) in bundles: {sorted(duplicates)}")
    missing_evidence = sorted(set(keys) - set(gate_evidence))
    if missing_evidence:
        problems.append(f"gate_evidence missing key(s): {missing_evidence}")
    missing_hashes = sorted(set(keys) - set(raw_hashes))
    if missing_hashes:
        problems.append(f"raw_hashes missing key(s): {missing_hashes}")
    if problems:
        raise ValueError("; ".join(problems))
    return keys


def score_snapshot(
    bundles: Sequence[FeatureBundle],
    populations: PopulationLookup,
    *,
    snapshot_id: str,
    as_of: datetime,
    gate_evidence: Mapping[RepoKey, GateEvidence],
    raw_hashes: Mapping[RepoKey, str],
) -> ScoredSnapshot:
    """Score a whole snapshot: D-ORDER-1 stages 0-9 over every bundle.

    Pure (no I/O, no clock): ``populations`` must already be frozen (stage 4
    -- built by the caller, typically via ``cvi.snapshot.populations.
    build_populations`` over a first, population-free scoring pass; this
    module does not build populations itself, only consumes them, the same
    boundary ``cvi.core.engine.score`` already draws). ``snapshot_id`` and
    ``as_of`` are shared across every repo (``cvi.core.engine.Snapshot``'s
    own documented contract); ``gate_evidence`` and ``raw_hashes`` vary per
    repo and are looked up by :func:`repo_key`.

    Adapts the packet dispatch's sketched signature
    (``score_snapshot(bundles, populations, *, snapshot_id, raw_hashes)``)
    to what ``cvi.core.engine.score`` actually takes, per the dispatch's own
    instruction to keep that function's signature as the source of truth:
    ``engine.score`` needs a full ``Snapshot`` (``snapshot_id`` + ``as_of`` +
    per-repo ``gate_evidence``), so both ``as_of`` and ``gate_evidence`` are
    added as explicit keyword parameters here rather than guessed at or
    smuggled inside ``populations``.

    Steps, each delegated to its own named function (module docstring):
    stages 0-7 per bundle (:func:`score_bundle_stages_0_7`), stage 8 per
    record (:func:`score_bundle_stage_8_persona`), stage 9 tie-break input
    per record (:func:`score_bundle_stage_9_tie_input`) then one call to
    :func:`cvi.core.tiebreak.assign_ranks` over the whole population,
    :func:`build_snapshot_counts`, then :func:`records_hash_of`.

    Raises ``ValueError`` for a duplicate repo key across ``bundles``, or a
    ``gate_evidence``/``raw_hashes`` mapping missing a key some bundle needs
    (naming every missing key, R-9's "measured, never estimated" extended to
    error messages: a caller fixes every problem from one raised message).
    """
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ValueError(f"snapshot_id must be a non-empty string, got {snapshot_id!r}")
    keys = _validated_keys(bundles, gate_evidence, raw_hashes)

    entries: list[TieBreakInput] = []
    for bundle, key in zip(bundles, keys):
        record = score_bundle_stages_0_7(
            bundle,
            populations,
            snapshot_id=snapshot_id,
            as_of=as_of,
            gate_evidence=gate_evidence[key],
            raw_hash=raw_hashes[key],
        )
        record = score_bundle_stage_8_persona(record, bundle)
        entries.append(score_bundle_stage_9_tie_input(record, bundle))

    ranked = assign_ranks(entries)
    records = tuple(record for record, _rank, _tie_group in ranked)
    counts = build_snapshot_counts(records)

    return ScoredSnapshot(
        snapshot_id=snapshot_id,
        benchmark_version=constants_v2.BENCHMARK_VERSION,
        constants_hash=constants_v2.CONSTANTS_HASH,
        records=records,
        counts=counts,
        records_hash=records_hash_of(records),
    )
