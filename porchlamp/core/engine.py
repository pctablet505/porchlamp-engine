"""Porchlamp v2 scoring engine: ``score(bundle, populations, snapshot) -> ScoreRecord``
(02 sections 1, 5, 7, 8; 04 sections 3, 4, 8).

D-ORDER-1 stage order, as far as this module goes:

- Stage 0 (gates): :func:`porchlamp.core.gates.evaluate_gates` runs first.
  The first failure is final: no parameter function is ever called for a
  gated repo (``_PARAM_FUNCTIONS`` is looked up freshly per call, so a test
  can replace it with a counting stub and observe zero calls).
- Stage 2 (measurement/curves): each of the 18 registered parameter
  functions (``porchlamp.core.params``) is applied to the bundle's inputs,
  using the frozen-population mid-rank ECDF (``porchlamp.snapshot.populations``)
  for the three D-NORM-3 ECDF-normalised parameters (1.5 ttm_p90,
  2.1 dependents, 2.3 downloads) -- queried on the raw measured value, with
  ``no_population`` reason) rather than raising: see :func:`_score_params`.
- Stage 3 (evidence gate), 5 (pillar aggregation), 6 (composite/ceiling):
  ``porchlamp.core.aggregate`` (D-MISS-2/3, D-AGG-2).
- Stage 7 (coordinates): ``porchlamp.core.matrix`` (D-MAT-1/2).
- Stages 1 (input hygiene) and 4 (normalization) happen upstream of this
  module: stage 1 is the deriver's job (the bundle's inputs already carry
  bot-typed, windowed samples), and stage 4 is the frozen ``populations``
  argument, built once per snapshot before any repo is scored.
- Stages 8-9 (persona gates/projections, tie-breaks/rank) are out of scope:
  ``persona_gates``, ``persona_composites``, ``tie_key``,
  ``tie_group``, ``rank`` and ``suspicion`` are left empty/None here.

Import-graph note (04 section 3, enforced by
``tests/core/test_import_graph.py``): ``porchlamp.core`` may import nothing
outside ``porchlamp.core`` plus the standard library, so this module cannot
import ``porchlamp.snapshot.populations.PopulationSet`` even for a type hint (the
import-graph test's AST scan does not distinguish a ``TYPE_CHECKING``-guarded
import from a real one). ``PopulationLookup`` below is a local structural
``Protocol`` capturing the one method this module calls
(``PopulationSet.ecdf`` satisfies it structurally); the real
``porchlamp.snapshot.populations.PopulationSet`` is what every caller passes at
runtime, imported only from outside ``porchlamp.core``.

Integration finding, resolved: the three D-NORM-3 curves used to query their ecdf with a
*transformed* value for two of the three parameters -- ``dependents`` and
``downloads`` queried ``ecdf(log10(1 + raw_value))`` while
``porchlamp.snapshot.populations`` freezes and stores raw values, and
``log10(1+x)`` is strictly monotone and so contributes nothing to a
mid-rank ECDF except a catastrophic collapse when the two sides disagree
(every repo scoring 0.0 on the parameter). The transform was deleted from
``porchlamp/core/params/criticality.py``: all three ECDF-normalised curves now
query their raw measured value, matching what ``build_populations`` freezes
-- one definition of the quantity being ranked.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol

from porchlamp.core import aggregate, constants, matrix
from porchlamp.core import params as param_fns
from porchlamp.core.gates import (
    DISPLAY_UNRATED_GATE_REASONS,
    REASON_BLACK_HOLE,
    REASON_INSUFFICIENT_EVIDENCE,
    REASON_OFF_PLATFORM_ROUTED,
    ROUTED_OFF_PLATFORM,
    GateEvidence,
    evaluate_gates,
    is_black_hole_bundle,
)
from porchlamp.core.models import FeatureBundle, GateResult, Measured, ParamScore, PillarScore, ScoreRecord, Unmeasured
from porchlamp.core.params.curves import KIND_ECDF


class PopulationLookup(Protocol):
    """Structural type for the two methods this module needs from a frozen
    population set (``porchlamp.snapshot.populations.PopulationSet``). Kept
    local rather than imported: see the module docstring's import-graph
    note. ``population`` returns ``None`` (a plain, non-raising existence
    check) rather than raising when the snapshot has no
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
    inside ``porchlamp.core`` -- ``tests/test_no_clock.py``). ``gate_evidence`` is
    inherently per-repo (tree/off-platform markers, vitality
    commits, universe fork-parent membership), so the caller builds one
    ``Snapshot`` per (repo, snapshot) call, sharing ``snapshot_id``/``as_of``
    and varying ``gate_evidence``. ``rank_black_holes`` controls whether
    repositories with ``GR_14 > 0.50`` remain eligible for stage 2-9 ranking
    with a ``"black_hole"`` flag on ``ScoreRecord.suspicion``."""

    snapshot_id: str
    as_of: datetime
    gate_evidence: GateEvidence
    rank_black_holes: bool = False


#: input_id -> the wrapper function that turns the bundle's ParamValue
#: into a ParamScore (``porchlamp.core.params.__all__``): one definition per
#: parameter, reused here rather than reimplemented. Looked up fresh
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
#: the registry rather than repeated here.
_ECDF_INPUT_IDS: frozenset[str] = frozenset(
    input_id for input_id, defn in param_fns.PARAMS.items() if defn.kind == KIND_ECDF
)


def _unused_ecdf(_value: float) -> float:
    """Placeholder ecdf for an ``Unmeasured`` ECDF-kind input.

    The three ECDF-normalised wrapper functions (``ttm_p90``, ``dependents``,
    ``downloads``) take ``ecdf`` as a required positional argument, but
    ``porchlamp.core.params.curves._apply`` never calls it when the underlying
    value is ``Unmeasured`` (that branch short-circuits first). Used so
    ``_score_params`` need not fetch a population for an input this repo
    never measured -- fetching one eagerly would call
    ``populations.ecdf(...)`` (and so could raise ``LookupError``) purely to
    satisfy a call signature, for a value the curve was never going to
    normalise."""
    return 0.0


#: D-MISS-1 reason for a Measured ECDF-kind input whose (ecosystem, param)
#: has no frozen population at all (02 section 5 D-MISS-1). Distinct from
#: ``_unused_ecdf``'s case: here the input *was* measured, but the snapshot
#: has nothing to rank it against. Reads from ``constants.REASON_NO_POPULATION``.
_REASON_NO_POPULATION = constants.REASON_NO_POPULATION

#: D-MISS-5's "bundle fact" resolvers, keyed by the fact name used in
#: ``constants.NOT_APPLICABLE_RULES`` (today only ``"ecosystem"``). Kept
#: local (not a bundle method) since the rule list itself is data, and this
#: module is the one place that closes the loop between a rule's fact name
#: and where it actually lives on a :class:`FeatureBundle`.
_BUNDLE_FACT_RESOLVERS: dict[str, Callable[[FeatureBundle], object]] = {
    "ecosystem": lambda bundle: bundle.repo.ecosystem,
}


def _not_applicable_is_permitted(input_id: str, bundle: FeatureBundle) -> bool:
    """D-MISS-5 guard (02 section 5, 03 section 4): ``True`` only when
    ``(input_id, fact, value)`` matches a row of the closed
    ``constants.NOT_APPLICABLE_RULES`` list against the bundle's own
    recorded fact. A rule naming a fact this module has no resolver for
    never matches (fails closed, not open)."""
    for rule_param, fact_name, expected_value in constants.NOT_APPLICABLE_RULES:
        if rule_param != input_id:
            continue
        resolver = _BUNDLE_FACT_RESOLVERS.get(fact_name)
        if resolver is not None and resolver(bundle) == expected_value:
            return True
    return False


#: D-MISS-5/03 section 4: the detail an illegitimate ``not_applicable``
#: claim is downgraded with -- named so the engine's decision is
#: distinguishable in the record from a genuine collector failure.
_NOT_APPLICABLE_OUTSIDE_RULE_LIST_DETAIL = "not_applicable claimed outside the rule list"


def _score_params(bundle: FeatureBundle, populations: PopulationLookup) -> dict[str, ParamScore]:
    """Stage 2: apply each of the 18 registered curves to the bundle's
    inputs, in frozen input-id order.

    A population is fetched only for a measured ECDF-kind input (see
    :func:`_unused_ecdf` for the unmeasured case); an existence check
    (``PopulationLookup.population``) runs first, so a measured ECDF input
    whose (ecosystem, param) has no frozen population comes back
    ``ParamScore(measured=False, reason="no_population")`` instead of
    letting ``PopulationSet.ecdf``'s ``LookupError`` escape this (documented
    pure) function. ``n`` is kept from the measurement:
    the value itself was measured, only the population is missing."""
    ecosystem = bundle.repo.ecosystem
    scores: dict[str, ParamScore] = {}
    for input_id in constants.PARAM_INPUT_IDS:
        value = bundle.inputs[input_id]
        if (
            isinstance(value, Unmeasured)
            and value.reason == "not_applicable"
            and not _not_applicable_is_permitted(input_id, bundle)
        ):
            # D-MISS-5 guard (03 section 4): a bundle or collector can never
            # assert not_applicable on its own -- only the closed rule list
            # may. Downgraded to collector_failure (a retry can still
            # measure it) rather than silently trusted or rejecting the
            # whole bundle.
            value = Unmeasured(reason="collector_failure", detail=_NOT_APPLICABLE_OUTSIDE_RULE_LIST_DETAIL)
        function = _PARAM_FUNCTIONS[input_id]
        if input_id not in _ECDF_INPUT_IDS:
            scores[input_id] = function(value)
            continue
        if not isinstance(value, Measured):
            scores[input_id] = function(value, _unused_ecdf)
            continue
        if populations.population(ecosystem, input_id) is None:
            scores[input_id] = ParamScore(
                value=value.value,
                score=None,
                measured=False,
                reason=_REASON_NO_POPULATION,
                n=value.n,
                window_start=value.window_start,
                window_end=value.window_end,
                method=None,
                source=value.source,
                fetched_at=value.fetched_at,
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
    suspicion: list[str] | None = None,
) -> ScoreRecord:
    """The common no-rank/no-tier shape shared by a stage-0 gate failure and
    a stage-3 (D-EVID-1) unrated outcome. ``gate`` is passed through
    unchanged in both cases: a stage-0 gate failure and a
    stage-3 evidence failure are told apart by ``rated``/``unrated_reason``,
    never by overwriting the stage-0 ``GateResult``. Stages 8-9 (personas,
    tie-breaks, rank) are out of scope here: left empty/None."""
    return ScoreRecord(
        repo=bundle.repo,
        snapshot_id=snapshot.snapshot_id,
        benchmark_version=constants.BENCHMARK_VERSION,
        constants_hash=constants.CONSTANTS_HASH,
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
        suspicion=list(suspicion) if suspicion else [],
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
    aggregated and the composite coverage computed (stages 3, 5); when
    D-EVID-1 fails (coverage below its threshold, or no measured
    responsiveness parameter, RFC-0024) the repo is ``rated=False``,
    ``unrated_reason="unrated: insufficient_evidence"`` with its
    params/pillars/coverage populated but no composite or tier. A rated
    repo gets its D-AGG-2 composite, D-MAT-2 tier (capped by the D-MISS-3
    ceiling) and D-MAT-1 coordinates. The stage-0 ``gate`` (eligible,
    reason, routed, undecided) is returned exactly as
    :func:`porchlamp.core.gates.evaluate_gates` computed it in every case --
    ``rated``/``unrated_reason``/``tier_ceiling`` carry the later stages'
    outcome as their own fields, so a gated repo (``gate.eligible=False``)
    and an unrated repo (``gate.eligible=True``, ``rated=False``) are always
    distinguishable from the record alone.

    ``raw_hash`` is supplied by the caller and stored as-is: this module
    does not compute it (the recommended value is a hash of the whole
    bundle document; a bundle-level hash cannot be computed inside
    ``porchlamp.core`` without importing ``porchlamp.derive``). When omitted it defaults to
    ``bundle.provenance_hash`` (hash of the referenced envelope hashes
    only), which is also always stored separately as ``provenance_hash`` so
    the two facts -- "which envelopes" and "which whole bundle" -- are never
    conflated under one field."""
    resolved_raw_hash = bundle.provenance_hash if raw_hash is None else raw_hash
    gate = evaluate_gates(
        bundle,
        snapshot.gate_evidence,
        snapshot.as_of,
        rank_black_holes=snapshot.rank_black_holes,
    )
    if not gate.eligible:
        # A stage-0 reason the design text words as an UNRATED outcome
        # (``DISPLAY_UNRATED_GATE_REASONS`` -- currently D-GATE-6's
        # "no external workflow") is carried onto the record as
        # ``unrated_reason`` as well, not just left on ``gate.reason``.
        # Consumers read ``unrated_reason`` to render "why is this not
        # ranked"; leaving it ``None`` here made the record say "gated, for
        # no stated reason" for the one gate whose reason is the whole point.
        unrated_reason = gate.reason if gate.reason in DISPLAY_UNRATED_GATE_REASONS else None
        return _bare_record(
            snapshot, bundle, gate, params={}, pillars={}, coverage=0.0,
            rated=False, unrated_reason=unrated_reason, tier_ceiling=None,
            raw_hash=resolved_raw_hash,
        )

    initial_suspicion: list[str] = (
        [REASON_BLACK_HOLE]
        if snapshot.rank_black_holes
        and is_black_hole_bundle(bundle)
        # A repository that hands review off is routed off-platform, not ranked
        # on GitHub signals it cannot be judged by: no black-hole flag either.
        and snapshot.gate_evidence.review_handoff is not True
        else []
    )

    param_scores = _score_params(bundle, populations)
    pillars = aggregate.aggregate_pillars(param_scores)
    coverage = aggregate.composite_coverage(pillars)
    rated, ceiling = aggregate.coverage_status(coverage, pillars)
    if gate.routed == ROUTED_OFF_PLATFORM:
        # D-GATE-6 routed this repo off-platform: its review workflow is real
        # but lives somewhere GitHub cannot see (a mailing list, Gerrit), so
        # there are no external PRs to measure. It passes the gate -- it is
        # not a black hole, it is not abandoned -- but it must never be RANKED
        # against repos whose responsiveness was actually observed.
        #
        # Eligibility alone is not enough to stop that: the non-PR parameters
        # (criticality, governance, and the non-PR halves of rigor and
        # accessibility) carry 0.63 of the composite weight, comfortably over
        # the 0.60 evidence floor. D-EVID-1's required responsiveness pillar
        # (RFC-0024) refuses a repo whose responsiveness pillar is empty, but
        # a routed repo can still have measured responsiveness parameters
        # (git/git and facebook/buck2: 4 of 5 measured on the 2026-10-21 data),
        # read from GitHub pull requests whose review happens elsewhere.
        # Scoring still runs so the drawer can
        # show what IS known; only the rating is withheld.
        return _bare_record(
            snapshot, bundle, gate, params=param_scores, pillars=pillars, coverage=coverage,
            rated=False, unrated_reason=REASON_OFF_PLATFORM_ROUTED, tier_ceiling=ceiling,
            raw_hash=resolved_raw_hash, suspicion=initial_suspicion,
        )
    if not rated:
        return _bare_record(
            snapshot, bundle, gate, params=param_scores, pillars=pillars, coverage=coverage,
            rated=False, unrated_reason=REASON_INSUFFICIENT_EVIDENCE, tier_ceiling=ceiling,
            raw_hash=resolved_raw_hash, suspicion=initial_suspicion,
        )

    composite_raw = aggregate.composite_score_raw(pillars)
    composite = aggregate.composite_score(pillars)
    tier = None if composite_raw is None else aggregate.apply_ceiling(aggregate.tier_from_score(composite_raw), ceiling)
    coords = matrix.coordinates(pillars)

    return ScoreRecord(
        repo=bundle.repo,
        snapshot_id=snapshot.snapshot_id,
        benchmark_version=constants.BENCHMARK_VERSION,
        constants_hash=constants.CONSTANTS_HASH,
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
        suspicion=initial_suspicion,
    )
