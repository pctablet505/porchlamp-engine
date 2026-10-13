"""Porchlamp v2 snapshot scorer: the whole-population composition point.

``porchlamp.core.engine.score`` is pure and per-repository: it runs D-ORDER-1
stages 0-7 (``02`` section 1) and leaves ``persona_gates``,
``persona_composites``, ``tie_key``, ``tie_group`` and ``rank`` empty,
because stages 8 (persona lens, ``porchlamp.core.persona``) and 9 (tie-breaks and
rank, ``porchlamp.core.tiebreak``) both need the *whole* scored population, not one
record at a time. :func:`score_snapshot` is the whole-population driver.

Pure, no I/O, no clock (04 section 8): every timestamp (``as_of``) and every
per-repo fact this module cannot derive from a :class:`~porchlamp.core.models.
FeatureBundle` alone (``gate_evidence``, ``raw_hash``) is supplied by the
caller. Building those inputs from raw collector data is the caller's job --
:mod:`scripts.build_fixture_snapshot` is one such caller.

Stage order implemented here, each step a small named function so it is
testable alone:

- :func:`score_bundle_stages_0_7` -- stages 0-7, delegating entirely to
  :func:`porchlamp.core.engine.score` (this module never re-implements a scoring
  rule; engine.score's signature is the source of truth for what a snapshot
  driver must supply per repo: a :class:`~porchlamp.core.engine.Snapshot`, i.e.
  ``snapshot_id`` + ``as_of`` shared across the whole call, and
  ``gate_evidence`` varying per repo -- see the module's own docstring on
  why ``gate_evidence`` is inherently per-repo).
- :func:`score_bundle_stage_8_persona` -- stage 8: ``persona.persona_gates``
  and ``persona.persona_composite`` for every preset, folded onto the record
  with ``dataclasses.replace`` (never mutated; see the docstring below for the
  two cases ``persona.py``'s own documented contract forbids calling it plainly on,
  and the one it deliberately does not apply a persona lens to at all).
- :func:`score_bundle_stage_9_tie_input` -- prepares the
  :class:`~porchlamp.core.tiebreak.TieBreakInput` evidence
  :class:`~porchlamp.core.models.ScoreRecord` does not itself carry (repo identity,
  TTFHR p75, last non-bot commit date); ranking itself
  (:func:`porchlamp.core.tiebreak.assign_ranks`) runs once, over the whole
  population, inside :func:`score_snapshot`.
- :func:`apply_suspicion` -- stage 10 (``03`` section 6): folds a
  caller-supplied ``detectors`` callable's per-repo flag names onto
  ``ScoreRecord.suspicion``, run after stage 9's ``assign_ranks`` so
  suspicion can never influence rank/tie_key. ``porchlamp.core`` cannot import
  ``porchlamp.verify.detectors`` (``tests/core/test_import_graph.py``), so this
  module never runs a detector itself -- see :data:`DetectorsCallable` and
  ``porchlamp/verify/pipeline_hook.py`` for where the real composition lives.
- :func:`build_snapshot_counts` -- rated/gated/unrated counts plus
  per-persona visible counts and a per-detector ``suspicion_counts``
  histogram, over the final ranked records.
- :func:`canonical_records_json` / :func:`records_hash_of` -- the
  reproducibility handle (03 section 5): a canonical JSON
  serialisation of the final record tuple (sorted keys, tuples as lists,
  floats via Python's default ``repr``-based float formatting), hashed with
  SHA-256. Field order and dict-insertion order never change it (each
  dataclass becomes a ``{field name: value}`` dict, and
  ``porchlamp.core.models.canonical_json`` sorts every dict's keys).

Stage 8 on a gated, unrated or routed record (D-PERS-1/2/3, ``02`` section 9).
Two cases ``persona.py``'s own contract does not let a caller treat like a normal
rated record, both handled by :func:`score_bundle_stage_8_persona`, and a third
that is not scored at all:

- ``record.gate.eligible is False`` (a stage-0 gate failure): ``pillars``
  is ``{}`` (``porchlamp.core.engine._bare_record``), and
  ``persona.persona_composite`` *raises* ``ValueError`` on an incomplete
  pillar set by design ("no balanced score exists to reproject, so this is
  a programming error to catch loudly, not a silent (None, 0.0, None)").
  ``persona_composites`` is therefore ``{}`` here, matching what
  ``porchlamp.core.engine`` itself already leaves on a gated record. A gated repo
  is gated under every persona, with the balanced ``gate.reason`` -- D-ORDER-1
  decides eligibility at stage 0, strictly before any persona lens exists, so
  nothing about a persona's own weights can undo it.
- ``record.gate.eligible is True`` and ``record.rated is False`` (a
  stage-3 D-EVID-1 evidence failure): ``pillars`` *is* the full five-name
  dict (``porchlamp.core.aggregate.aggregate_pillars`` always returns one), so
  ``persona.persona_composite`` can be called for every preset without
  raising, and is. Unlike the gated case, this is not universal:
  a persona's ``rated`` flag is recomputed from *that persona's own*
  re-weighted coverage, never copied from the balanced record -- a repo the
  balanced weights left unrated can still be rated under a persona whose weights
  happen to favour the pillars this repo *did* measure. So for each of the four
  :data:`~porchlamp.core.persona.GATED_PERSONAS`: if that persona's own
  ``PersonaComposite.rated`` is ``False`` (its own weighted coverage is
  below 0.60), the reason is
  ``f"{persona.REASON_INSUFFICIENT_EVIDENCE_PREFIX}coverage"``
  (``"insufficient_evidence:coverage"`` -- a coverage-level sibling of
  D-PERS-3's per-input ``insufficient_evidence:<input id>`` reasons,
  naming the persona-weighted coverage gate itself rather than one
  parameter); otherwise the persona is gated (or not) exactly as
  ``persona.persona_gates`` already computes it from raw evidence -- same
  as a rated record. A persona clearing both bars is simply absent from
  ``persona_gates``, which is what makes the repo visible under that lens,
  the same rule a rated record already follows.
- ``record.gate.eligible is True`` and ``record.gate.routed ==``
  :data:`~porchlamp.core.gates.ROUTED_OFF_PLATFORM` (D-GATE-6: the repository's
  review happens on a mailing list, in Gerrit or in an internal repository):
  GitHub cannot see that review, so its responsiveness cannot be compared with
  anyone's, and the balanced board already withholds its rating
  (``porchlamp.core.engine.score``). No persona lens is applied either:
  ``persona_gates`` and ``persona_composites`` are both ``{}``, so there is no
  per-lens score and no per-lens gate, and every lens reads the reason from the
  record's own ``gate.routed`` / ``unrated_reason``. This is not the unrated
  case above: a record unrated for thin evidence keeps its persona composites,
  because a persona's own weights may cover what the balanced weights do not;
  routing is a property of the repository, not of any weight vector.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass, replace
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from porchlamp.core import commit_domains, constants, persona
from porchlamp.core.commit_domains import LastNonBotCommitMeasured
from porchlamp.core.engine import PopulationLookup, Snapshot, score
from porchlamp.core.gates import (
    DISPLAY_UNRATED_GATE_REASONS as _DISPLAY_UNRATED_GATE_REASONS,
    ROUTED_OFF_PLATFORM,
    GateEvidence,
)
from porchlamp.core.models import (
    FeatureBundle,
    PersonaComposite,
    ScoreRecord,
    canonical_json,
    sha256_hex,
)
from porchlamp.core.tiebreak import TieBreakInput, assign_ranks, ttfhr_p75_from_pr_window

#: A repository key, ``"owner/name"`` (exact case: :class:`~porchlamp.core.models.
#: RepoIdentity` never normalises case, and neither does this module -- the
#: same convention :func:`porchlamp.core.tiebreak._name_key` lowercases only for
#: sort comparison, never for identity).
RepoKey = str


def repo_key(carrier: "FeatureBundle | ScoreRecord") -> RepoKey:
    """The repository key a caller's ``gate_evidence``/``raw_hashes``
    mappings (and this module's own :class:`SnapshotCounts`) are keyed by.

    Accepts either a :class:`~porchlamp.core.models.FeatureBundle` or a
    :class:`~porchlamp.core.models.ScoreRecord` (the ``detectors``
    composition seam needs to key *records*, not just bundles, by the same
    identity).
    Both types carry a ``repo: RepoIdentity`` field, and ``ScoreRecord.repo``
    is always copied from the bundle it was scored from
    (``porchlamp.core.engine.score``), so the two never disagree for the same
    repository."""
    return f"{carrier.repo.owner}/{carrier.repo.name}"


# --------------------------------------------------------------------------- #
# Stage 0-7: delegate to porchlamp.core.engine.score verbatim.
# --------------------------------------------------------------------------- #
def score_bundle_stages_0_7(
    bundle: FeatureBundle,
    populations: PopulationLookup,
    *,
    snapshot_id: str,
    as_of: datetime,
    gate_evidence: GateEvidence,
    raw_hash: str,
    rank_black_holes: bool = False,
) -> ScoreRecord:
    """D-ORDER-1 stages 0-7 for one bundle: ``porchlamp.core.engine.score`` exactly
    (this module never re-implements a scoring rule -- engine.score's own
    signature is the source of truth for what it needs per repo)."""
    snapshot = Snapshot(
        snapshot_id=snapshot_id,
        as_of=as_of,
        gate_evidence=gate_evidence,
        rank_black_holes=rank_black_holes,
    )
    return score(bundle, populations, snapshot, raw_hash)


# --------------------------------------------------------------------------- #
# Stage 8: persona gates and persona composites (D-PERS-1/2/3, 02 section 9).
# --------------------------------------------------------------------------- #
def score_bundle_stage_8_persona(record: ScoreRecord, bundle: FeatureBundle) -> ScoreRecord:
    """Stage 8 for one already stage-0-7-scored record: fold in
    ``persona_gates``/``persona_composites`` via ``dataclasses.replace``
    (``ScoreRecord`` is frozen; never mutated -- see the module docstring
    for the gated-vs-unrated distinction and why the unrated
    branch recomputes each persona's own coverage rather than stating one
    shared reason).

    A gate-ineligible record fails every persona for the same D-GATE reason
    and has no persona composites at all. An eligible record routed
    off-platform (``gate.routed == ROUTED_OFF_PLATFORM``, D-GATE-6) is ranked
    under no lens: no persona was applied, so ``persona_gates`` and
    ``persona_composites`` are both ``{}`` (the record's own ``gate.routed`` and
    ``unrated_reason`` are the reason on every lens). Otherwise each gated
    persona gets exactly one reason, chosen by what the reason *claims* rather
    than by which check produced it:

    1. A substantive D-PERS-2 gate (``ai_hard_ban``,
       ``high_barrier_to_entry``, ``criticality_below_threshold``). These
       fire on evidence that was measured and genuinely failed, so they are
       durable: collecting more data for the pillars this persona is thin on
       would not open the lens. Reporting the coverage gap instead would
       tell a reader to wait for evidence that cannot change the outcome,
       and would hide a hard ban behind a soft-sounding reason.
    2. ``insufficient_evidence:coverage``, when the persona's own composite
       did not clear the coverage floor. This is recomputed per persona
       rather than inherited from ``record.rated`` because the preset weight
       vectors differ: a repo can be unrated overall yet have enough
       evidence under one persona's weights, and a repo rated overall can
       still be under-covered for a persona that leans on a thin pillar.
       It outranks the input-level evidence reason below because the whole
       lens is unreadable, not just one of its inputs.
    3. An ``insufficient_evidence:<input>`` D-PERS-2 gate, when the persona
       is otherwise well enough covered to be shown -- then the one missing
       input is exactly the thing to name.
    """
    composites: dict[str, PersonaComposite]
    if not record.gate.eligible:
        gates = {p: record.gate.reason for p in persona.GATED_PERSONAS}
        composites = {}
    elif record.gate.routed == ROUTED_OFF_PLATFORM:
        gates = {}
        composites = {}
    else:
        composites = {p: persona.persona_composite(record, p) for p in persona.PERSONA_PRESETS}
        own_gates = persona.persona_gates(record, bundle.inputs)
        gates = {}
        for name in persona.GATED_PERSONAS:
            own = own_gates.get(name)
            substantive = own is not None and not own.startswith(persona.REASON_INSUFFICIENT_EVIDENCE_PREFIX)
            if substantive:
                gates[name] = own
            elif not composites[name].rated:
                gates[name] = f"{persona.REASON_INSUFFICIENT_EVIDENCE_PREFIX}coverage"
            elif own is not None:
                gates[name] = own
    return replace(record, persona_gates=gates, persona_composites=composites)


# --------------------------------------------------------------------------- #
# Stage 9 prep: the tie-break evidence a ScoreRecord does not itself carry.
# --------------------------------------------------------------------------- #
def score_bundle_stage_9_tie_input(record: ScoreRecord, bundle: FeatureBundle) -> TieBreakInput:
    """Build one :class:`~porchlamp.core.tiebreak.TieBreakInput`.

    ``owner``/``name`` come from ``record.repo`` (``ScoreRecord``
    carries the scored bundle's ``RepoIdentity``, set by
    ``porchlamp.core.engine.score`` itself, and ``TieBreakInput.__post_init__``
    rejects an ``owner``/``name`` pair that disagrees with
    ``record.repo``). ``record.repo`` and ``bundle.repo`` are always the
    same identity by construction (``engine.score`` copies one from the
    other), so reading from either gives the same value; ``record.repo`` is
    used because it is the field the current ``TieBreakInput`` contract
    actually validates against. ``ttfhr_p75_hours`` is computed for real from
    ``bundle.pr_window`` via :func:`porchlamp.core.tiebreak.
    ttfhr_p75_from_pr_window`. ``last_non_bot_commit_at`` (D-TIE-1
    item 7) is read from ``bundle`` via
    :func:`porchlamp.core.commit_domains.last_non_bot_commit_at` -- ``None`` when
    that fact is ``Unmeasured`` (a missing/failed commits envelope) or a
    measured absence (no non-bot commit in the window), the same
    "unmeasured -> None" collapse this module already applies to
    ``ttfhr_p75_hours``; ``tiebreak.py``'s own D-TIE-1 ordering already
    treats a ``None`` value as sorting after every measured one at this
    level regardless. Read through an accessor rather than a declared field."""
    last_commit = commit_domains.last_non_bot_commit_at(bundle)
    last_commit_at = last_commit.value if isinstance(last_commit, LastNonBotCommitMeasured) else None
    return TieBreakInput(
        record=record,
        owner=record.repo.owner,
        name=record.repo.name,
        ttfhr_p75_hours=ttfhr_p75_from_pr_window(bundle.pr_window),
        last_non_bot_commit_at=last_commit_at,
    )


# --------------------------------------------------------------------------- #
# ScoredSnapshot: the whole-population output.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SnapshotCounts:
    """Rated/gated/unrated counts plus per-persona visible counts, over the
    final ranked :class:`~porchlamp.core.models.ScoreRecord` tuple.

    ``persona_visible`` covers every key of ``persona.PERSONA_PRESETS``
    (the five presets, ``"balanced"`` included, matching
    ``persona_composites``' own key set -- see :func:`score_bundle_stage_8_
    persona`). ``"balanced"``'s count is simply the ``rated`` count (D-PERS-1:
    the balanced view is never itself a gate target, so nothing hides a
    rated record from it); every other persona's count is the number of
    records for which that persona is absent from ``persona_gates`` *and*
    that persona's own :class:`~porchlamp.core.models.PersonaComposite` is
    ``rated`` (04 section 6's per-persona-rated ruling: a persona's
    visibility is never read off the balanced ``rated`` flag). A record
    routed off-platform (D-GATE-6) has no persona composites at stage 8, so no
    persona's count includes it.

    ``suspicion_counts`` is a per-detector-name histogram over every
    record's final ``suspicion`` list: ``{name: number of records carrying
    that name}``. Built generically from whatever names actually appear
    (``porchlamp.core`` cannot import ``porchlamp.verify.detectors.DETECTOR_NAMES`` --
    see ``score_snapshot``'s ``detectors`` seam docstring -- so this is not
    a fixed nine-key dict; a name no record carries is simply absent, not
    zero-filled). Defaults to ``{}``."""

    rated: int
    gated: int
    unrated: int
    persona_visible: Mapping[str, int]
    suspicion_counts: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoredSnapshot:
    """The whole-population output of :func:`score_snapshot`.

    ``records`` is in final display order: rated records first by strict
    rank 1..N, then gated/unrated records ordered by lowercase
    ``owner/name`` (D-TIE-2) -- exactly ``porchlamp.core.tiebreak.assign_ranks``'s
    own documented order, carried through unchanged. ``records_hash`` is
    the reproducibility handle (03 section 5): sha256 of
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


#: `06-UX-DESIGN.md` section 3 A2's user-facing classification lists
#: "no external workflow" under UNRATED (an evidence outcome, alongside
#: "insufficient evidence" and "off-platform routed"), never under GATED
#: (the six universal disqualifications: mirror/archived/unlicensed/
#: stale/black_hole/non_software, plus fork). The ENGINE nonetheless
#: gates it at stage 0 (`porchlamp.core.gates.REASON_NO_EXTERNAL_WORKFLOW`,
#: D-GATE-6: `eligible=False`) -- that raw flag is `03` section 5's
#: provenance and stays exactly as `evaluate_gates` computed it, never
#: rewritten.
#:
#: Re-exported from ``porchlamp.core.gates``, which is where it is defined (beside
#: the reason vocabulary it is a subset of, and reachable from
#: ``porchlamp.core.engine``, which this module imports). Named here as well so its
#: existing consumers -- :func:`not_ranked_class` below,
#: `porchlamp.store.queries.snapshot_summary`, `porchlamp.serve.app`'s not-ranked split --
#: keep importing it from the module whose classification it describes.
DISPLAY_UNRATED_GATE_REASONS: frozenset[str] = _DISPLAY_UNRATED_GATE_REASONS


def not_ranked_class(record: ScoreRecord) -> str:
    """`06-UX-DESIGN.md` section 3 A2's user-facing classification of one
    record: ``"rated"``, ``"gated"`` or ``"unrated"`` -- distinct from the
    raw engine ``record.gate.eligible`` flag, which this function reads
    but never changes (`03` section 5: that flag is provenance).

    - ``"rated"``: ``record.rated`` is ``True``.
    - ``"gated"``: not rated, ``eligible`` is ``False``, and
      ``record.gate.reason`` is NOT one of
      :data:`DISPLAY_UNRATED_GATE_REASONS` -- one of the six universal
      06 section 3 A2 Gated reasons, or fork.
    - ``"unrated"``: everything else -- an evidence-floor unrated repo
      (D-EVID-1, ``eligible=True`` but composite coverage below 0.60), OR
      an engine-gated repo whose reason IS in
      :data:`DISPLAY_UNRATED_GATE_REASONS` (06's own classification
      overrides the engine's stage-0 bucket for display/counting only).
    """
    if record.rated:
        return "rated"
    if not record.gate.eligible and record.gate.reason not in DISPLAY_UNRATED_GATE_REASONS:
        return "gated"
    return "unrated"


def build_snapshot_counts(records: Sequence[ScoreRecord]) -> SnapshotCounts:
    """:class:`SnapshotCounts` over a final, ranked record sequence."""
    rated = sum(1 for record in records if record.rated)
    gated = sum(1 for record in records if not_ranked_class(record) == "gated")
    unrated = sum(1 for record in records if not_ranked_class(record) == "unrated")
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
    suspicion_counts: dict[str, int] = {}
    for record in records:
        for name in record.suspicion:
            suspicion_counts[name] = suspicion_counts.get(name, 0) + 1
    return SnapshotCounts(
        rated=rated, gated=gated, unrated=unrated, persona_visible=visible,
        suspicion_counts=suspicion_counts,
    )


# --------------------------------------------------------------------------- #
# records_hash: canonical JSON over the final record tuple.
# --------------------------------------------------------------------------- #
def _to_plain(value: Any) -> Any:
    """Recursively turn dataclasses/mappings/tuples into
    dict/dict/list, the JSON-native shapes ``porchlamp.core.models.canonical_json``
    accepts. A dataclass becomes ``{field name: value}`` (never a
    positional list), so re-ordering a dataclass's declared fields cannot
    change the serialised bytes -- ``canonical_json`` sorts every dict's
    keys besides. Every remaining leaf (``int``/``float``/``str``/``bool``/``None``)
    is returned unchanged; Python's ``json`` module already formats a
    ``float`` with its ``repr`` (the shortest round-tripping form)."""
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
    with :func:`records_hash_of` or ``porchlamp.core.models.sha256_hex`` directly."""
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
    # Case-INSENSITIVE: the last level of both D-TIE-1 (``tie_key``) and
    # D-TIE-2 (``_name_key``) is ``(owner.lower(), name.lower())``, so two
    # bundles differing only in case produce an identical final key. Python's
    # sort is stable, which means their relative order -- and therefore
    # ``ScoredSnapshot.records_hash`` -- would be decided by the caller's
    # input sequence, breaking this module's contract that a shuffle of
    # ``bundles`` can never change the hash. They are also, in every
    # real sense, the same repository: GitHub owner/name is case-insensitive.
    seen: dict[str, RepoKey] = {}
    duplicates: set[RepoKey] = set()
    for key in keys:
        folded = key.lower()
        if folded in seen:
            duplicates.add(seen[folded])
            duplicates.add(key)
        else:
            seen[folded] = key
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


#: The composition seam :func:`score_snapshot` uses to populate
#: ``ScoreRecord.suspicion`` (``03`` section 6). ``porchlamp.core`` may not
#: import ``porchlamp.verify`` (``tests/core/test_import_graph.py`` restricts
#: every ``porchlamp/core/*.py`` module to ``porchlamp.core`` + stdlib), so the real
#: detector logic (``porchlamp.verify.detectors.run_detectors``) cannot be called
#: from here directly. Instead the caller supplies a callable of this shape;
#: ``porchlamp/verify/pipeline_hook.py`` (outside ``porchlamp.core``, free to import
#: both ``porchlamp.core`` and ``porchlamp.verify``) is the one place that builds the
#: real one, adapting each current bundle to the
#: ``porchlamp.verify.detectors.DetectorInputs`` a bare ``ScoreRecord`` cannot
#: supply on its own (elephant_factor, newcomer_issue_supply, the
#: closed-unread share/n) and closing over them so the returned callable
#: only needs ``(records, previous)``.
DetectorsCallable = Callable[
    [Sequence[ScoreRecord], "ScoredSnapshot | None"], Mapping[RepoKey, Sequence[str]]
]


def apply_suspicion(
    records: Sequence[ScoreRecord],
    previous: "ScoredSnapshot | None",
    detectors: DetectorsCallable | None,
) -> tuple[ScoreRecord, ...]:
    """Fold ``detectors``'s per-repo flag lists onto ``records`` (stage 10,
    run after stage 9's ranks are assigned -- see
    :func:`score_snapshot`'s own docstring for why suspicion must never
    feed back into rank/tie_key).

    Every record's ``suspicion`` becomes the sorted, de-duplicated list of
    names ``detectors`` returned for its repo key (:func:`repo_key`) --
    sorted/de-duplicated *here*, not trusted from the callable:
    ``DetectorsCallable`` is a caller-supplied interface with no contract of
    its own about order or duplicates, so this is the one place that
    guarantees ``porchlamp.core.models.ScoreRecord.suspicion``'s own documented
    shape regardless of which composition supplied the raw mapping (a
    record's own repo key missing from the mapping is simply "no detector
    fired", matching :func:`~porchlamp.verify.detectors.run_detectors`'s own
    "sorted and de-duplicated" contract without depending on any particular
    implementation of it).

    No detector runs at all -- every record keeps whatever ``suspicion`` it
    already carried (``[]``, set by ``porchlamp.core.engine.score``) -- only when
    ``detectors`` is ``None`` (no composition wired in -- ``porchlamp.core`` alone
    never knows how to build one). **``previous`` being ``None`` is not, by
    itself, a reason to run nothing**. ``previous``
    is simply forwarded to ``detectors`` unchanged, exactly like any other
    call -- ``detectors(records, previous)`` -- so which detectors actually
    fire on a first-ever snapshot (``previous is None``) is entirely up to
    the callable's own semantics, never decided here. For the real
    composition (``porchlamp/verify/pipeline_hook.py``'s ``build_detectors``, over
    ``porchlamp.verify.detectors.run_detectors``), that means: every detector that
    reads only the current record/inputs (``closed_unread``,
    ``label_spam``, ``speed_without_decisions``, ``merges_without_return``,
    ``newcomer_burst``, ``ef_domain_mismatch``, ``star_burst`` when its
    input exists) fires normally with ``previous is None``, exactly as it
    would for a repo present in ``records`` but absent from
    ``previous.records`` (both cases reach ``detectors`` with
    ``previous_record=None`` for that repo -- one because the whole
    snapshot has no baseline yet, the other because this one repo is new;
    ``porchlamp.verify.detectors.run_detectors`` treats them identically, as it
    should); only ``param_jump`` (the one detector of the nine that
    actually reads ``previous`` -- see ``porchlamp.verify.detectors.
    detect_param_jump``'s own ``previous is None`` early return) yields
    nothing in either case, since there is nothing to compare against.

    Never mutates ``records`` (``ScoreRecord`` is frozen; ``dataclasses.
    replace`` per record, the same convention every other stage function in
    this module follows)."""
    if detectors is None:
        return tuple(records)
    suspicion_by_key = detectors(records, previous)
    return tuple(
        replace(
            record,
            suspicion=sorted(
                set(record.suspicion) | set(suspicion_by_key.get(repo_key(record), ()))
            ),
        )
        for record in records
    )


def score_snapshot(
    bundles: Sequence[FeatureBundle],
    populations: PopulationLookup,
    *,
    snapshot_id: str,
    as_of: datetime,
    gate_evidence: Mapping[RepoKey, GateEvidence],
    raw_hashes: Mapping[RepoKey, str],
    previous: "ScoredSnapshot | None" = None,
    detectors: DetectorsCallable | None = None,
    rank_black_holes: bool = False,
) -> ScoredSnapshot:
    """Score a whole snapshot: D-ORDER-1 stages 0-9 over every bundle, then
    stage 10: fold cross-snapshot detector flags onto ``suspicion``.

    Pure (no I/O, no clock): ``populations`` must already be frozen (stage 4
    -- built by the caller, typically via ``porchlamp.snapshot.populations.
    build_populations`` over a first, population-free scoring pass; this
    module does not build populations itself, only consumes them, the same
    boundary ``porchlamp.core.engine.score`` already draws). ``snapshot_id`` and
    ``as_of`` are shared across every repo (``porchlamp.core.engine.Snapshot``'s
    own documented contract); ``gate_evidence`` and ``raw_hashes`` vary per
    repo and are looked up by :func:`repo_key`.

    Signature follows ``porchlamp.core.engine.score`` as the source of truth:
    ``engine.score`` needs a full ``Snapshot`` (``snapshot_id`` + ``as_of`` +
    per-repo ``gate_evidence``), so both ``as_of`` and ``gate_evidence`` are
    added as explicit keyword parameters here.

    Steps, each delegated to its own named function (module docstring):
    stages 0-7 per bundle (:func:`score_bundle_stages_0_7`), stage 8 per
    record (:func:`score_bundle_stage_8_persona`), stage 9 tie-break input
    per record (:func:`score_bundle_stage_9_tie_input`) then one call to
    :func:`porchlamp.core.tiebreak.assign_ranks` over the whole population, stage
    10 (:func:`apply_suspicion`) over the ranked population,
    :func:`build_snapshot_counts`, then :func:`records_hash_of`.

    ``previous`` is the prior snapshot's own :class:`ScoredSnapshot`
    -- ``None`` for the first snapshot ever, or whenever a caller has none
    to compare against -- and ``detectors`` is the optional composition
    that turns ``(records, previous)`` into each repo's fired detector
    names; see :func:`apply_suspicion` and :data:`DetectorsCallable` for the
    full contract. Only ``detectors is None`` skips stage 10 entirely --
    ``previous is None`` does **not**, by itself, suppress anything: it is
    forwarded to ``detectors`` like any other call, so whether a first-ever
    snapshot fires the single-snapshot detectors (``closed_unread``,
    ``label_spam``, etc.) is the callable's own decision, and the real
    composition's answer is "yes" (only ``param_jump`` needs a genuine
    previous record to compare against). Stage 10 runs strictly after stage 9's
    ``assign_ranks`` call: ``rank``/``tie_group``/``tie_key`` are already
    fixed on every record by then, so ``suspicion`` can never feed back into
    ranking or tie-breaking (``03`` section 6: flags are "displayed...
    never silent penalties") -- two snapshots that differ only in
    ``previous`` (and therefore in which flags fire) always agree on every
    record's ``rank``/``tie_group``.

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
            rank_black_holes=rank_black_holes,
        )
        record = score_bundle_stage_8_persona(record, bundle)
        entries.append(score_bundle_stage_9_tie_input(record, bundle))

    ranked = assign_ranks(entries)
    records = tuple(record for record, _rank, _tie_group in ranked)
    records = apply_suspicion(records, previous, detectors)
    counts = build_snapshot_counts(records)

    return ScoredSnapshot(
        snapshot_id=snapshot_id,
        benchmark_version=constants.BENCHMARK_VERSION,
        constants_hash=constants.CONSTANTS_HASH,
        records=records,
        counts=counts,
        records_hash=records_hash_of(records),
    )
