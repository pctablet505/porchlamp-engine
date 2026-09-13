"""CVI v2 scoring engine: ``score(bundle, populations, snapshot) -> ScoreRecord``
(02 sections 1, 5, 7, 8; 04 sections 3, 4, 8).

D-ORDER-1 stage order, as far as this module goes:

- Stage 0 (gates): :func:`cvi.core.gates.evaluate_gates` (W2-2) runs first.
  The first failure is final: no parameter function is ever called for a
  gated repo (``_PARAM_FUNCTIONS`` is looked up freshly per call, so a test
  can replace it with a counting stub and observe zero calls).
- Stage 2 (measurement/curves): each of the 18 registered parameter
  functions (``cvi.core.params``, W2-3) is applied to the bundle's inputs,
  using the frozen-population mid-rank ECDF (``cvi.snapshot.populations``,
  W2-4) for the three D-NORM-3 ECDF-normalised parameters (1.5 ttm_p90,
  2.1 dependents, 2.3 downloads) -- queried on the raw measured value, with
  no transform on either the curve or the population side (D-NORM-3
  amendment, 02 section 6, lead ruling 2026-09-12). A ``Measured`` ECDF
  input whose (ecosystem, param) has no frozen population at all comes back
  as a ``ParamScore(measured=False, reason="no_population")`` (D-MISS-1's
  ``no_population`` reason) rather than raising: see :func:`_score_params`.
- Stage 3 (evidence gate), 5 (pillar aggregation), 6 (composite/ceiling):
  ``cvi.core.aggregate`` (D-MISS-2/3, D-AGG-2).
- Stage 7 (coordinates): ``cvi.core.matrix`` (D-MAT-1/2).
- Stages 1 (input hygiene) and 4 (normalization) happen upstream of this
  module: stage 1 is the deriver's job (the bundle's inputs already carry
  bot-typed, windowed samples), and stage 4 is the frozen ``populations``
  argument, built once per snapshot before any repo is scored.
- Stages 8-9 (persona gates/projections, tie-breaks/rank) are out of scope
  (W2-6, W2-7): ``persona_gates``, ``persona_composites``, ``tie_key``,
  ``tie_group``, ``rank`` and ``suspicion`` are left empty/None here.

Import-graph note (04 section 3, enforced by
``tests/core/test_import_graph.py``): ``cvi.core`` may import nothing
outside ``cvi.core`` plus the standard library, so this module cannot
import ``cvi.snapshot.populations.PopulationSet`` even for a type hint (the
import-graph test's AST scan does not distinguish a ``TYPE_CHECKING``-guarded
import from a real one). ``PopulationLookup`` below is a local structural
``Protocol`` capturing the one method this module calls
(``PopulationSet.ecdf`` satisfies it structurally); the real
``cvi.snapshot.populations.PopulationSet`` is what every caller passes at
runtime, imported only from outside ``cvi.core`` (callers, and this
packet's own tests).

Integration finding, resolved (W2-3 x W2-4, first wired here; W2-5 review
W-2): the three D-NORM-3 curves used to query their ecdf with a
*transformed* value for two of the three parameters -- ``dependents`` and
``downloads`` queried ``ecdf(log10(1 + raw_value))`` while
``cvi.snapshot.populations`` freezes and stores raw values, and
``log10(1+x)`` is strictly monotone and so contributes nothing to a
mid-rank ECDF except a catastrophic collapse when the two sides disagree
(every repo scoring 0.0 on the parameter). The transform was deleted from
``cvi/core/params/criticality.py``: all three ECDF-normalised curves now
query their raw measured value, matching what ``build_populations`` freezes
(D-NORM-3 amendment, 02 section 6, lead ruling 2026-09-12) -- one definition
of the quantity being ranked, not a convention two modules had to agree on
by narration.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol

from cvi.core import aggregate, constants_v2, matrix
from cvi.core import params as param_fns
from cvi.core.gates import REASON_INSUFFICIENT_EVIDENCE, GateEvidence, evaluate_gates
from cvi.core.models import FeatureBundle, GateResult, Measured, ParamScore, PillarScore, ScoreRecord
from cvi.core.params.curves import KIND_ECDF


class PopulationLookup(Protocol):
    """Structural type for the two methods this module needs from a frozen
    population set (``cvi.snapshot.populations.PopulationSet``, W2-4). Kept
    local rather than imported: see the module docstring's import-graph
    note. ``population`` returns ``None`` (a plain, non-raising existence
    check -- W2-5 review W-3) rather than raising when the snapshot has no
    population for the pair; its element type is opaque here (``object``)
    since this module never reads a population's fields, only whether one
    exists."""

    def population(self, ecosystem: str, param: str) -> object | None: ...
    def ecdf(self, ecosystem: str, param: str) -> Callable[[float], float]: ...


@dataclass(frozen=True)
class Snapshot:
    """Per-repo scoring context at one snapshot (04 section 4: ``score``
    needs ``bundle + populations + snapshot``).

    ``snapshot_id`` and ``as_of`` are shared across every repo scored at one
    snapshot; ``as_of`` is the D-VER-3 reference time (no clock is read
    inside ``cvi.core`` -- ``tests/test_no_clock.py``). ``gate_evidence`` is
    inherently per-repo (W1-9 tree/off-platform markers, W1-5 vitality
    commits, universe fork-parent membership), so the caller builds one
    ``Snapshot`` per (repo, snapshot) call, sharing ``snapshot_id``/``as_of``
    and varying ``gate_evidence``. Additive: not defined by W2-1 (04 section
    3 names no such type); owned by this module, not ``cvi.core.models``,
    because it is scoring-call plumbing, not a stored contract shape."""

    snapshot_id: str
    as_of: datetime
    gate_evidence: GateEvidence


#: input_id -> the W2-3 wrapper function that turns the bundle's ParamValue
#: into a ParamScore (``cvi.core.params.__all__``): one definition per
#: parameter, reused here rather than reimplemented (R-2). Looked up fresh
#: on every call via this module-level name, so a test can monkeypatch it
#: wholesale and count calls (the D-GATE acceptance oracle).
_PARAM_FUNCTIONS: dict[str, Callable[..., ParamScore]] = {
    "ttfhr_hours": param_fns.ttfhr,
    "dr90_decision_rate": param_fns.dr90,
    "mr_first_rate": param_fns.mr_first,
    "gr14_ghost_rate": param_fns.gr14,
    "ttm_p90_hours": param_fns.ttm_p90,
    "dependents_deduped": param_fns.dependents,
    "openssf_criticality": param_fns.openssf_criticality,
    "downloads_monthly": param_fns.downloads,
    "legal_ip": param_fns.legal_ip,
    "elephant_factor": param_fns.elephant_factor,
    "promotion_ladder": param_fns.promotion_ladder,
    "pipeline_defenses": param_fns.pipeline_defenses,
    "ai_posture": param_fns.ai_posture,
    "substantive_review_rate": param_fns.substantive_review_rate,
    "newcomer_issue_supply": param_fns.newcomer_issue_supply,
    "newcomer_conversion": param_fns.newcomer_conversion,
    "rr90_return_rate": param_fns.rr90_return_rate,
    "dx": param_fns.dx,
}

#: The three D-NORM-3 ECDF-normalised input ids (1.5, 2.1, 2.3), derived from
#: the W2-3 registry rather than repeated here (R-2).
_ECDF_INPUT_IDS: frozenset[str] = frozenset(
    input_id for input_id, defn in param_fns.PARAMS.items() if defn.kind == KIND_ECDF
)


def _unused_ecdf(_value: float) -> float:
    """Placeholder ecdf for an ``Unmeasured`` ECDF-kind input.

    The three ECDF-normalised wrapper functions (``ttm_p90``, ``dependents``,
    ``downloads``) take ``ecdf`` as a required positional argument, but
    ``cvi.core.params.curves._apply`` never calls it when the underlying
    value is ``Unmeasured`` (that branch short-circuits first). Used so
    ``_score_params`` need not fetch a population for an input this repo
    never measured -- fetching one eagerly would call
    ``populations.ecdf(...)`` (and so could raise ``LookupError``) purely to
    satisfy a call signature, for a value the curve was never going to
    normalise."""
    return 0.0


#: D-MISS-1 reason for a Measured ECDF-kind input whose (ecosystem, param)
#: has no frozen population at all (W2-5 review W-3; 02 section 5 D-MISS-1,
#: added 2026-09-12). Distinct from ``_unused_ecdf``'s case: here the input
#: *was* measured, but the snapshot has nothing to rank it against. Reads
#: from ``constants_v2.REASON_NO_POPULATION`` (packet W2-8: the string is
#: defined once, there, and is now also a member of
#: ``constants_v2.UNMEASURED_REASONS`` -- previously this module's own
#: hardcoded literal was the only place the value existed, and constructing
#: an ``Unmeasured("no_population")`` directly would raise, since the tuple
#: lacked it).
_REASON_NO_POPULATION = constants_v2.REASON_NO_POPULATION


def _score_params(bundle: FeatureBundle, populations: PopulationLookup) -> dict[str, ParamScore]:
    """Stage 2: apply each of the 18 registered curves to the bundle's
    inputs, in frozen input-id order.

    A population is fetched only for a measured ECDF-kind input (see
    :func:`_unused_ecdf` for the unmeasured case); an existence check
    (``PopulationLookup.population``) runs first, so a measured ECDF input
    whose (ecosystem, param) has no frozen population comes back
    ``ParamScore(measured=False, reason="no_population")`` instead of
    letting ``PopulationSet.ecdf``'s ``LookupError`` escape this (documented
    pure) function -- W2-5 review W-3. ``n`` is kept from the measurement:
    the value itself was measured, only the population is missing."""
    ecosystem = bundle.repo.ecosystem
    scores: dict[str, ParamScore] = {}
    for input_id in constants_v2.PARAM_INPUT_IDS:
        value = bundle.inputs[input_id]
        function = _PARAM_FUNCTIONS[input_id]
        if input_id not in _ECDF_INPUT_IDS:
            scores[input_id] = function(value)
            continue
        if not isinstance(value, Measured):
            scores[input_id] = function(value, _unused_ecdf)
            continue
        if populations.population(ecosystem, input_id) is None:
            scores[input_id] = ParamScore(
                score=None, measured=False, reason=_REASON_NO_POPULATION, n=value.n, method=None
            )
            continue
        scores[input_id] = function(value, populations.ecdf(ecosystem, input_id))
    return scores


def _bare_record(
    snapshot: Snapshot,
    bundle: FeatureBundle,
    gate: GateResult,
    *,
    params: dict[str, ParamScore],
    pillars: dict[str, PillarScore],
    coverage: float,
    rated: bool,
    unrated_reason: str | None,
    tier_ceiling: str | None,
    raw_hash: str,
) -> ScoreRecord:
    """The common no-rank/no-tier shape shared by a stage-0 gate failure and
    a stage-3 (D-EVID-1) unrated outcome. ``gate`` is passed through
    unchanged in both cases (W2-5 review W-1): a stage-0 gate failure and a
    stage-3 evidence failure are told apart by ``rated``/``unrated_reason``,
    never by overwriting the stage-0 ``GateResult``. Stages 8-9 (personas,
    tie-breaks, rank) are out of scope here (W2-6, W2-7): left empty/None."""
    return ScoreRecord(
        repo=bundle.repo,
        snapshot_id=snapshot.snapshot_id,
        benchmark_version=constants_v2.BENCHMARK_VERSION,
        constants_hash=constants_v2.CONSTANTS_HASH,
        raw_hash=raw_hash,
        provenance_hash=bundle.provenance_hash,
        gate=gate,
        params=params,
        pillars=pillars,
        composite=None,
        coverage=coverage,
        tier=None,
        leverage=None,
        friction=None,
        quadrant=None,
        rated=rated,
        unrated_reason=unrated_reason,
        tier_ceiling=tier_ceiling,
        leverage_coverage=0.0,
        friction_coverage=0.0,
        persona_gates={},
        persona_composites={},
        tie_key=(),
        tie_group=None,
        rank=None,
        suspicion=[],
    )


def score(
    bundle: FeatureBundle,
    populations: PopulationLookup,
    snapshot: Snapshot,
    raw_hash: str | None = None,
) -> ScoreRecord:
    """Score one repo at one snapshot (04 section 4: pure -- no I/O, no
    clock; needs only ``bundle``, ``populations`` and ``snapshot``).

    A stage-0 gate failure (``eligible=False``) returns immediately with the
    gate's reason, no params, no pillars, ``coverage=0.0``, no composite and
    no tier: the parameter functions are never called (see the module
    docstring). Otherwise every parameter is scored (stage 2), pillars are
    aggregated and the composite coverage computed (stages 3, 5); below the
    D-EVID-1 threshold the repo is ``rated=False``,
    ``unrated_reason="unrated: insufficient_evidence"`` with its
    params/pillars/coverage populated but no composite or tier. A rated
    repo gets its D-AGG-2 composite, D-MAT-2 tier (capped by the D-MISS-3
    ceiling) and D-MAT-1 coordinates. The stage-0 ``gate`` (eligible,
    reason, routed, undecided) is returned exactly as
    :func:`cvi.core.gates.evaluate_gates` computed it in every case --
    ``rated``/``unrated_reason``/``tier_ceiling`` carry the later stages'
    outcome as their own fields, so a gated repo (``gate.eligible=False``)
    and an unrated repo (``gate.eligible=True``, ``rated=False``) are always
    distinguishable from the record alone (W2-5 review W-1).

    ``raw_hash`` is supplied by the caller and stored as-is: this module
    does not compute it (the recommended value is a hash of the whole
    bundle document, once a deriver computes one -- W2-5 review W-1; a
    bundle-level hash cannot be computed inside ``cvi.core`` without
    importing ``cvi.derive``). When omitted it defaults to
    ``bundle.provenance_hash`` (hash of the referenced envelope hashes
    only), which is also always stored separately as ``provenance_hash`` so
    the two facts -- "which envelopes" and "which whole bundle" -- are never
    conflated under one field."""
    resolved_raw_hash = bundle.provenance_hash if raw_hash is None else raw_hash
    gate = evaluate_gates(bundle, snapshot.gate_evidence, snapshot.as_of)
    if not gate.eligible:
        return _bare_record(
            snapshot, bundle, gate, params={}, pillars={}, coverage=0.0,
            rated=False, unrated_reason=None, tier_ceiling=None, raw_hash=resolved_raw_hash,
        )

    param_scores = _score_params(bundle, populations)
    pillars = aggregate.aggregate_pillars(param_scores)
    coverage = aggregate.composite_coverage(pillars)
    rated, ceiling = aggregate.coverage_status(coverage)
    if not rated:
        return _bare_record(
            snapshot, bundle, gate, params=param_scores, pillars=pillars, coverage=coverage,
            rated=False, unrated_reason=REASON_INSUFFICIENT_EVIDENCE, tier_ceiling=ceiling,
            raw_hash=resolved_raw_hash,
        )

    composite_raw = aggregate.composite_score_raw(pillars)
    composite = aggregate.composite_score(pillars)
    tier = None if composite_raw is None else aggregate.apply_ceiling(aggregate.tier_from_score(composite_raw), ceiling)
    coords = matrix.coordinates(pillars)

    return ScoreRecord(
        repo=bundle.repo,
        snapshot_id=snapshot.snapshot_id,
        benchmark_version=constants_v2.BENCHMARK_VERSION,
        constants_hash=constants_v2.CONSTANTS_HASH,
        raw_hash=resolved_raw_hash,
        provenance_hash=bundle.provenance_hash,
        gate=gate,
        params=param_scores,
        pillars=pillars,
        composite=composite,
        coverage=coverage,
        tier=tier,
        leverage=coords.leverage,
        friction=coords.friction,
        quadrant=coords.quadrant,
        rated=True,
        unrated_reason=None,
        tier_ceiling=ceiling,
        leverage_coverage=coords.leverage_coverage,
        friction_coverage=coords.friction_coverage,
        persona_gates={},
        persona_composites={},
        tie_key=(),
        tie_group=None,
        rank=None,
        suspicion=[],
    )
