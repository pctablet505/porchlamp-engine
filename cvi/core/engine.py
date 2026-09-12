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
  2.1 dependents, 2.3 downloads).
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

Integration finding (W2-3 x W2-4, first wired here): the three D-NORM-3
curves query their ecdf with a *transformed* value --
``ttm_p90`` queries ``ecdf(p90_hours)`` directly, but ``dependents`` and
``downloads`` query ``ecdf(log10(1 + raw_value))`` (``cvi/core/params/
criticality.py``). ``cvi.snapshot.populations.PopulationMember.measured``
is documented as "the raw measured value"; for the ECDF built from it to
rank the same quantity the curve queries with, whoever builds the
dependents/downloads *populations* must insert ``log10(1 + raw_value)``,
not the raw count -- ``ttm_p90``'s population is the one parameter of the
three where "raw" and "queried" coincide. This module does not build
populations (it receives an already-frozen ``PopulationLookup``), so it
cannot enforce this; it is recorded here for whoever writes the real
population-building driver, and both this module's own test and the W2-5
Measure step build the dependents/downloads populations in log space.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol

from cvi.core import aggregate, constants_v2, matrix
from cvi.core import params as param_fns
from cvi.core.gates import GateEvidence, evaluate_gates
from cvi.core.models import FeatureBundle, GateResult, Measured, ParamScore, PillarScore, ScoreRecord
from cvi.core.params.curves import KIND_ECDF


class PopulationLookup(Protocol):
    """Structural type for the one method this module needs from a frozen
    population set (``cvi.snapshot.populations.PopulationSet.ecdf``, W2-4).
    Kept local rather than imported: see the module docstring's import-graph
    note."""

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


def _score_params(bundle: FeatureBundle, populations: PopulationLookup) -> dict[str, ParamScore]:
    """Stage 2: apply each of the 18 registered curves to the bundle's
    inputs, in frozen input-id order. A population is fetched only for a
    measured ECDF-kind input (see :func:`_unused_ecdf`)."""
    ecosystem = bundle.repo.ecosystem
    scores: dict[str, ParamScore] = {}
    for input_id in constants_v2.PARAM_INPUT_IDS:
        value = bundle.inputs[input_id]
        function = _PARAM_FUNCTIONS[input_id]
        if input_id in _ECDF_INPUT_IDS:
            ecdf = populations.ecdf(ecosystem, input_id) if isinstance(value, Measured) else _unused_ecdf
            scores[input_id] = function(value, ecdf)
        else:
            scores[input_id] = function(value)
    return scores


def _bare_record(
    snapshot: Snapshot,
    bundle: FeatureBundle,
    gate: GateResult,
    *,
    params: dict[str, ParamScore],
    pillars: dict[str, PillarScore],
    coverage: float,
) -> ScoreRecord:
    """The common no-rank/no-tier shape shared by a stage-0 gate failure and
    a stage-3 (D-EVID-1) unrated outcome. Stages 8-9 (personas, tie-breaks,
    rank) are out of scope here (W2-6, W2-7): left empty/None."""
    return ScoreRecord(
        snapshot_id=snapshot.snapshot_id,
        benchmark_version=constants_v2.BENCHMARK_VERSION,
        constants_hash=constants_v2.CONSTANTS_HASH,
        raw_hash=bundle.provenance_hash,
        gate=gate,
        params=params,
        pillars=pillars,
        composite=None,
        coverage=coverage,
        tier=None,
        leverage=None,
        friction=None,
        quadrant=None,
        persona_gates={},
        persona_composites={},
        tie_key=(),
        tie_group=None,
        rank=None,
        suspicion=[],
    )


def score(bundle: FeatureBundle, populations: PopulationLookup, snapshot: Snapshot) -> ScoreRecord:
    """Score one repo at one snapshot (04 section 4: pure -- no I/O, no
    clock; needs only ``bundle``, ``populations`` and ``snapshot``).

    A stage-0 gate failure (``eligible=False``) returns immediately with the
    gate's reason, no params, no pillars, ``coverage=0.0``, no composite and
    no tier: the parameter functions are never called (see the module
    docstring). Otherwise every parameter is scored (stage 2), pillars are
    aggregated and the composite coverage computed (stages 3, 5); below the
    D-EVID-1 threshold the repo is ``unrated: insufficient_evidence`` with
    its params/pillars/coverage populated but no composite or tier. A rated
    repo gets its D-AGG-2 composite, D-MAT-2 tier (capped by the D-MISS-3
    ceiling) and D-MAT-1 coordinates. ``routed``/``undecided`` from the
    stage-0 gate are preserved in the final ``GateResult`` in every case."""
    gate = evaluate_gates(bundle, snapshot.gate_evidence, snapshot.as_of)
    if not gate.eligible:
        return _bare_record(snapshot, bundle, gate, params={}, pillars={}, coverage=0.0)

    param_scores = _score_params(bundle, populations)
    pillars = aggregate.aggregate_pillars(param_scores)
    coverage = aggregate.composite_coverage(pillars)
    rated, ceiling = aggregate.coverage_status(coverage)
    final_gate = GateResult(
        eligible=rated,
        reason=None if rated else "unrated: insufficient_evidence",
        routed=gate.routed,
        undecided=gate.undecided,
    )
    if not rated:
        return _bare_record(snapshot, bundle, final_gate, params=param_scores, pillars=pillars, coverage=coverage)

    composite_raw = aggregate.composite_score_raw(pillars)
    composite = None if composite_raw is None else round(composite_raw, 2)
    tier = None if composite_raw is None else aggregate.apply_ceiling(aggregate.tier_from_score(composite_raw), ceiling)
    coords = matrix.coordinates(pillars)

    return ScoreRecord(
        snapshot_id=snapshot.snapshot_id,
        benchmark_version=constants_v2.BENCHMARK_VERSION,
        constants_hash=constants_v2.CONSTANTS_HASH,
        raw_hash=bundle.provenance_hash,
        gate=final_gate,
        params=param_scores,
        pillars=pillars,
        composite=composite,
        coverage=coverage,
        tier=tier,
        leverage=coords.leverage,
        friction=coords.friction,
        quadrant=coords.quadrant,
        persona_gates={},
        persona_composites={},
        tie_key=(),
        tie_group=None,
        rank=None,
        suspicion=[],
    )
