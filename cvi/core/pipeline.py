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
- :func:`apply_suspicion` -- stage 10 (W3-2f, ``03`` section 6): folds a
  caller-supplied ``detectors`` callable's per-repo flag names onto
  ``ScoreRecord.suspicion``, run after stage 9's ``assign_ranks`` so
  suspicion can never influence rank/tie_key. ``cvi.core`` cannot import
  ``cvi.verify.detectors`` (``tests/core/test_import_graph.py``), so this
  module never runs a detector itself -- see :data:`DetectorsCallable` and
  ``cvi/verify/pipeline_hook.py`` for where the real composition lives.
- :func:`build_snapshot_counts` -- rated/gated/unrated counts plus
  per-persona visible counts and a per-detector ``suspicion_counts``
  histogram, over the final ranked records.
- :func:`canonical_records_json` / :func:`records_hash_of` -- the
  reproducibility handle (03 section 5, W3-4): a canonical JSON
  serialisation of the final record tuple (sorted keys, tuples as lists,
  floats via Python's default ``repr``-based float formatting), hashed with
  SHA-256. Field order and dict-insertion order never change it (each
  dataclass becomes a ``{field name: value}`` dict, and
  ``cvi.core.models.canonical_json`` sorts every dict's keys).

Stage 8 on a gated or unrated record (D-PERS-1/2/3, ``02`` section 9;
**lead ruling R-1**, following the W2-8b review
-- ``.agents/W2-8b/review/R-rulings-compliance.md``). Two cases
``persona.py``'s own contract does not let a caller treat like a normal
rated record, both handled by :func:`score_bundle_stage_8_persona`:

- ``record.gate.eligible is False`` (a stage-0 gate failure): ``pillars``
  is ``{}`` (``cvi.core.engine._bare_record``), and
  ``persona.persona_composite`` *raises* ``ValueError`` on an incomplete
  pillar set by design (``tests/core/test_persona_v2.py::
  test_persona_composite_raises_when_pillars_incomplete``: "no balanced
  score exists to reproject, so this is a programming error to catch
  loudly, not a silent (None, 0.0, None)"). ``persona_composites`` is
  therefore ``{}`` here, matching what ``cvi.core.engine`` itself already
  leaves on a gated record. **R-1: a gated repo is gated under every
  persona**, with the balanced ``gate.reason`` -- D-ORDER-1 decides
  eligibility at stage 0, strictly before any persona lens exists, so
  nothing about a persona's own weights can undo it.
- ``record.gate.eligible is True`` and ``record.rated is False`` (a
  stage-3 D-EVID-1 evidence failure): ``pillars`` *is* the full five-name
  dict (``cvi.core.aggregate.aggregate_pillars`` always returns one), so
  ``persona.persona_composite`` can be called for every preset without
  raising, and is. **R-1: unlike the gated case, this is not universal.**
  ``04`` section 6's own ruling is that a persona's ``rated`` flag is
  recomputed from *that persona's own* re-weighted coverage, never copied
  from the balanced record -- a repo the balanced weights left unrated can
  still be rated under a persona whose weights happen to favour the
  pillars this repo *did* measure. So for each of the four
  :data:`~cvi.core.persona.GATED_PERSONAS`: if that persona's own
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

An earlier version of this module stated the balanced unrated reason on
all four personas unconditionally (the packet dispatch's literal wording).
The W2-8b review measured this against the 12-repo fixture:
``unrated/partial-repo``'s four persona coverages (0.052-0.087) were
already below 0.60, so the universal override happened to produce the
right *answer* there, but not from the right *rule* -- a repo unrated at
balanced weights while genuinely rated under one persona's own weights
would have been wrongly hidden from that lens too. Corrected per the R-1
ruling above; `04` section 6's SQL projection (W4-3) needs no change, since
it already recomputes per-persona ``rated`` independently of this
module's ``persona_gates`` field.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass, replace
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from cvi.core import commit_domains, constants_v2, persona
from cvi.core.commit_domains import LastNonBotCommitMeasured
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


def repo_key(carrier: "FeatureBundle | ScoreRecord") -> RepoKey:
    """The repository key a caller's ``gate_evidence``/``raw_hashes``
    mappings (and this module's own :class:`SnapshotCounts`) are keyed by.

    Accepts either a :class:`~cvi.core.models.FeatureBundle` or a
    :class:`~cvi.core.models.ScoreRecord` (W3-2f: the ``detectors``
    composition seam needs to key *records*, not just bundles, by the same
    identity -- one definition per fact, R-2, rather than a second
    ``_record_repo_key`` restating ``f"{x.repo.owner}/{x.repo.name}"``).
    Both types carry a ``repo: RepoIdentity`` field, and ``ScoreRecord.repo``
    is always copied from the bundle it was scored from
    (``cvi.core.engine.score``), so the two never disagree for the same
    repository."""
    return f"{carrier.repo.owner}/{carrier.repo.name}"


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
    for the gated-vs-unrated distinction R-1 draws and why the unrated
    branch recomputes each persona's own coverage rather than stating one
    shared reason)."""
    if not record.gate.eligible:
        gates = {p: record.gate.reason for p in persona.GATED_PERSONAS}
        composites: dict[str, PersonaComposite] = {}
    elif not record.rated:
        composites = {p: persona.persona_composite(record, p) for p in persona.PERSONA_PRESETS}
        own_gates = persona.persona_gates(record, bundle.inputs)
        gates = {}
        for name in persona.GATED_PERSONAS:
            if not composites[name].rated:
                gates[name] = f"{persona.REASON_INSUFFICIENT_EVIDENCE_PREFIX}coverage"
            elif name in own_gates:
                gates[name] = own_gates[name]
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
    ttfhr_p75_from_pr_window`. ``last_non_bot_commit_at`` (W1-5f, D-TIE-1
    item 7) is read from ``bundle`` via
    :func:`cvi.core.commit_domains.last_non_bot_commit_at` -- ``None`` when
    that fact is ``Unmeasured`` (a missing/failed commits envelope) or a
    measured absence (no non-bot commit in the window), the same
    "unmeasured -> None" collapse this module already applies to
    ``ttfhr_p75_hours``; ``tiebreak.py``'s own D-TIE-1 ordering already
    treats a ``None`` value as sorting after every measured one at this
    level regardless. Neither ``FeatureBundle`` nor ``GateEvidence``
    declares a field for this fact (04 section 3 has none; W1-5's
    ``GateEvidence.vitality_commits_180d`` is a trailing-180-day *count*,
    not a date) -- ``cvi.derive.bundle._load_v1`` sets it as a bundle
    instance sidecar attribute instead, which is why this function reads
    it through an accessor rather than a declared field."""
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
    visibility is never read off the balanced ``rated`` flag).

    ``suspicion_counts`` (W3-2f) is a per-detector-name histogram over every
    record's final ``suspicion`` list: ``{name: number of records carrying
    that name}``. Built generically from whatever names actually appear
    (``cvi.core`` cannot import ``cvi.verify.detectors.DETECTOR_NAMES`` --
    see ``score_snapshot``'s ``detectors`` seam docstring -- so this is not
    a fixed nine-key dict; a name no record carries is simply absent, not
    zero-filled). Defaults to ``{}`` so the pre-W3-2f construction site in
    ``tests/audit/test_anchors_fixture.py`` (a known, separately-tracked
    failure -- see that file) keeps working unchanged."""

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


#: The composition seam :func:`score_snapshot` uses to populate
#: ``ScoreRecord.suspicion`` (W3-2f, ``03`` section 6). ``cvi.core`` may not
#: import ``cvi.verify`` (``tests/core/test_import_graph.py`` restricts
#: every ``cvi/core/*.py`` module to ``cvi.core`` + stdlib), so the real
#: detector logic (``cvi.verify.detectors.run_detectors``) cannot be called
#: from here directly -- the lead's preferred "plain import" option the
#: packet dispatch names is unavailable, confirmed against that test rather
#: than assumed. Instead the caller supplies a callable of this shape;
#: ``cvi/verify/pipeline_hook.py`` (outside ``cvi.core``, free to import
#: both ``cvi.core`` and ``cvi.verify``) is the one place that builds the
#: real one, adapting each current bundle to the
#: ``cvi.verify.detectors.DetectorInputs`` a bare ``ScoreRecord`` cannot
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
    """Fold ``detectors``'s per-repo flag lists onto ``records`` (W3-2f
    stage 10, run after stage 9's ranks are assigned -- see
    :func:`score_snapshot`'s own docstring for why suspicion must never
    feed back into rank/tie_key).

    Every record's ``suspicion`` becomes the sorted, de-duplicated list of
    names ``detectors`` returned for its repo key (:func:`repo_key`) --
    sorted/de-duplicated *here*, not trusted from the callable:
    ``DetectorsCallable`` is a caller-supplied interface with no contract of
    its own about order or duplicates, so this is the one place that
    guarantees ``cvi.core.models.ScoreRecord.suspicion``'s own documented
    shape regardless of which composition supplied the raw mapping (a
    record's own repo key missing from the mapping is simply "no detector
    fired", matching :func:`~cvi.verify.detectors.run_detectors`'s own
    "sorted and de-duplicated" contract without depending on any particular
    implementation of it).

    No detector runs at all -- every record keeps whatever ``suspicion`` it
    already carried (``[]``, set by ``cvi.core.engine.score``) -- only when
    ``detectors`` is ``None`` (no composition wired in -- ``cvi.core`` alone
    never knows how to build one). **``previous`` being ``None`` is not, by
    itself, a reason to run nothing** (lead ruling, correcting this
    function's first draft: "``previous=None`` -> all ``suspicion == []``"
    in the dispatch meant the *cross-snapshot* detectors only). ``previous``
    is simply forwarded to ``detectors`` unchanged, exactly like any other
    call -- ``detectors(records, previous)`` -- so which detectors actually
    fire on a first-ever snapshot (``previous is None``) is entirely up to
    the callable's own semantics, never decided here. For the real
    composition (``cvi/verify/pipeline_hook.py``'s ``build_detectors``, over
    ``cvi.verify.detectors.run_detectors``), that means: every detector that
    reads only the current record/inputs (``closed_unread``,
    ``label_spam``, ``speed_without_decisions``, ``merges_without_return``,
    ``newcomer_burst``, ``ef_domain_mismatch``, ``star_burst`` when its
    input exists) fires normally with ``previous is None``, exactly as it
    would for a repo present in ``records`` but absent from
    ``previous.records`` (both cases reach ``detectors`` with
    ``previous_record=None`` for that repo -- one because the whole
    snapshot has no baseline yet, the other because this one repo is new;
    ``cvi.verify.detectors.run_detectors`` treats them identically, as it
    should); only ``param_jump`` (the one detector of the nine that
    actually reads ``previous`` -- see ``cvi.verify.detectors.
    detect_param_jump``'s own ``previous is None`` early return) yields
    nothing in either case, since there is nothing to compare against.

    Never mutates ``records`` (``ScoreRecord`` is frozen; ``dataclasses.
    replace`` per record, the same convention every other stage function in
    this module follows)."""
    if detectors is None:
        return tuple(records)
    suspicion_by_key = detectors(records, previous)
    return tuple(
        replace(record, suspicion=sorted(set(suspicion_by_key.get(repo_key(record), ()))))
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
) -> ScoredSnapshot:
    """Score a whole snapshot: D-ORDER-1 stages 0-9 over every bundle, then
    stage 10 (W3-2f): fold cross-snapshot detector flags onto ``suspicion``.

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
    :func:`cvi.core.tiebreak.assign_ranks` over the whole population, stage
    10 (:func:`apply_suspicion`, W3-2f) over the ranked population,
    :func:`build_snapshot_counts`, then :func:`records_hash_of`.

    ``previous`` (W3-2f) is the prior snapshot's own :class:`ScoredSnapshot`
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
    previous record to compare against -- lead ruling correcting this
    function's first draft). Stage 10 runs strictly after stage 9's
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
        )
        record = score_bundle_stage_8_persona(record, bundle)
        entries.append(score_bundle_stage_9_tie_input(record, bundle))

    ranked = assign_ranks(entries)
    records = tuple(record for record, _rank, _tie_group in ranked)
    records = apply_suspicion(records, previous, detectors)
    counts = build_snapshot_counts(records)

    return ScoredSnapshot(
        snapshot_id=snapshot_id,
        benchmark_version=constants_v2.BENCHMARK_VERSION,
        constants_hash=constants_v2.CONSTANTS_HASH,
        records=records,
        counts=counts,
        records_hash=records_hash_of(records),
    )
