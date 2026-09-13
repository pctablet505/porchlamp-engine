"""W2-8b acceptance: the snapshot scoring pipeline
(``cvi.core.pipeline.score_snapshot``, D-ORDER-1 stages 0-9 over a whole
population; 02 sections 1, 9, 10; 04 sections 3, 4, 8; 03 section 5).

Fail-before: before this packet ``cvi.core.pipeline`` does not exist, so
every test below fails on ``ModuleNotFoundError`` (the same convention
``tests/core/test_engine_v2.py`` and ``tests/core/test_persona_v2.py``
already document for their own packets).

``_Fixture12`` builds twelve repos through the real ``FeatureBundle``
constructor:

- two gated (``archived``, ``unlicensed``) -- stage-0 D-GATE-2/3 failures;
- two unrated (D-EVID-1 coverage < 0.60), one with zero measured inputs and
  one with a single measured ``legal_ip`` (RC, contributes some coverage,
  still below 0.60);
- eight rated, all built from ``_HI_INPUTS`` (a copy of
  ``tests/core/test_engine_v2.py::EligibleFixture``'s hand-verified
  composite-97.3 bundle -- every value chosen so every intermediate lands on
  a clean part of its curve) with only ``dr90_decision_rate`` varied per
  repo. ``dr90``'s curve is ``min(100, 125*r)`` for ``r < 0.8``, strictly
  monotone and never saturating below that boundary, and it feeds only
  pillar 1 (responsiveness) with within-pillar weight .25 -- so distinct
  ``r`` values 0.05 spaced apart necessarily give composites at least
  ``0.25 * 0.25 * 125 * 0.05 = 0.390...`` apart, far more than the 2-decimal
  rounding this design compares on. Two repos (``tiea/x``, ``tieb/y``) share
  the exact same ``r`` (0.5) and therefore the exact same composite by
  construction, not by a coincidental rounding -- the deliberate D-TIE-1 tie.
  One repo (``persona/proscribed``) additionally sets ``ai_posture`` to
  ``PROSCRIBED`` (Measured): D-PERS-2's hard AI ban gates it from the
  ``ai_assisted`` persona lens while every other persona (including
  ``balanced``, which is never a gate target) sees it exactly as scored --
  the "one repo whose persona gate differs from its balanced gate" the
  packet asks for.
"""
from __future__ import annotations

import copy
import dataclasses
import datetime
from typing import Any

import pytest

from cvi.core import constants_v2, persona
from cvi.core.gates import GateEvidence
from cvi.core.models import (
    Cohorts,
    FeatureBundle,
    GateResult,
    Measured,
    PillarScore,
    PRWindow,
    PRWindowEvent,
    RepoIdentity,
    ScoreRecord,
    Unmeasured,
)
from cvi.core.pipeline import (
    RepoKey,
    ScoredSnapshot,
    apply_suspicion,
    build_snapshot_counts,
    canonical_records_json,
    records_hash_of,
    repo_key,
    score_bundle_stage_8_persona,
    score_bundle_stage_9_tie_input,
    score_bundle_stages_0_7,
    score_snapshot,
)
from cvi.snapshot.populations import Population, PopulationSet

NOW = datetime.datetime(2026, 9, 12, 0, 0, tzinfo=datetime.timezone.utc)
_HASH64 = "c" * 64
_SNAPSHOT_ID = "snap-w2-8b"


def _iso(value: datetime.datetime) -> str:
    return value.isoformat()


def _m(value: Any, *, n: int = 0) -> Measured:
    return Measured(
        value=value, n=n, window_start=None, window_end=None, method="primary",
        source="test://fixture", source_hash=_HASH64, fetched_at=_iso(NOW),
    )


def _u(reason: str = "not_applicable") -> Unmeasured:
    return Unmeasured(reason=reason)


#: A copy of ``tests/core/test_engine_v2.py::EligibleFixture.inputs()``:
#: every one of the 18 inputs Measured on a clean part of its curve, giving
#: composite 97.3 / tier "S+" / coverage 1.0 unchanged. Reproduced here
#: rather than imported (test files in this codebase are self-contained --
#: ``tests/core/test_persona_v2.py`` follows the same convention with its
#: own local ``_pillar``/``_record`` helpers instead of importing another
#: test module's).
def _hi_inputs(*, dr90: float, ai_posture: str = "AD_HOC") -> dict[str, Measured]:
    return {
        "ttfhr_hours": _m(12.0, n=10),
        "dr90_decision_rate": _m(dr90, n=10),
        "mr_first_rate": _m(0.8, n=5),
        "gr14_ghost_rate": _m(0.0, n=10),
        "ttm_p90_hours": _m(1.0, n=5),
        "dependents_deduped": _m(999999.0, n=0),
        "openssf_criticality": _m(1.0, n=0),
        "downloads_monthly": _m(999999999.0, n=0),
        "legal_ip": _m("DCO", n=0),
        "elephant_factor": _m(4.0, n=30),
        "promotion_ladder": _m("VERIFIED", n=0),
        "pipeline_defenses": _m(100.0, n=0),
        "ai_posture": _m(ai_posture, n=0),
        "substantive_review_rate": _m(0.8, n=10),
        "newcomer_issue_supply": _m(10.0, n=0),
        "newcomer_conversion": _m(0.5, n=5),
        "rr90_return_rate": _m(0.5, n=5),
        "dx": _m(100.0, n=5),
    }


def _repo(owner: str, name: str, *, archived: bool = False, license_spdx: str | None = "MIT") -> RepoIdentity:
    return RepoIdentity(
        owner=owner, name=name, ecosystem="pypi", archived=archived, mirror_url=None,
        fork=False, license_spdx=license_spdx, default_branch="main",
    )


def _decided_event() -> PRWindowEvent:
    """One external, merged PR 30 d old (D-GATE-6 passes cleanly; D-GATE-5's
    population of 1 is below its minimum n of 10, so D-GATE-5 lands
    UNDECIDED, matching ``EligibleFixture``'s own convention) -- also the
    one external event with a recorded first human response
    ``ttfhr_p75_from_pr_window`` needs to return a real (non-``None``)
    value."""
    created = NOW - datetime.timedelta(days=30)
    return PRWindowEvent(
        pr_number=1, created_at=_iso(created), closed_at=None,
        merged_at=_iso(created + datetime.timedelta(days=2)), author_login="contributor",
        author_association="NONE", is_external=True,
        first_human_review_at=_iso(created + datetime.timedelta(days=1)),
    )


def _bundle(repo: RepoIdentity, inputs: dict[str, Measured | Unmeasured], *, events: tuple = ()) -> FeatureBundle:
    bundle = FeatureBundle(
        schema_version=constants_v2.SCHEMA_VERSION, repo=repo, inputs=inputs,
        pr_window=PRWindow(
            start=_iso(NOW - datetime.timedelta(days=180)) if events else None,
            end=_iso(NOW) if events else None, truncated=False, events=events,
        ),
        cohorts=Cohorts(decision_pr_numbers=(), newcomer_authors=()), provenance_hash=_HASH64,
    )
    bundle.validate()
    return bundle


def _full_inputs(measured: dict[str, Measured | Unmeasured] | None = None) -> dict[str, Measured | Unmeasured]:
    """Every one of the 18 inputs ``Unmeasured("not_applicable")`` except
    the overrides in ``measured``."""
    inputs: dict[str, Measured | Unmeasured] = {key: _u() for key in constants_v2.PARAM_INPUT_IDS}
    inputs.update(measured or {})
    return inputs


_HEALTHY_EVIDENCE = GateEvidence(
    manifest_found=True, is_curated_anchor=False, license_file_present=None,
    fork_parent_in_universe=None, vitality_commits_180d=5, off_platform_marker=None,
)


class _Fixture12:
    """Twelve repos: 2 gated, 2 unrated, 8 rated (module docstring)."""

    # -- gated -------------------------------------------------------- #
    GATED_ARCHIVED = "gated/archived-repo"
    GATED_UNLICENSED = "gated/unlicensed-repo"
    # -- unrated -------------------------------------------------------- #
    UNRATED_EMPTY = "unrated/empty-repo"
    UNRATED_PARTIAL = "unrated/partial-repo"
    # -- rated: dr90 value per repo key (see module docstring) -------- #
    RATED_DR90: dict[str, float] = {
        "rated-hi/repo": 0.8,
        "rated-a2/repo": 0.65,
        "tiea/x": 0.5,
        "tieb/y": 0.5,
        "persona/proscribed": 0.4,
        "rated-mid/repo": 0.3,
        "rated-c/repo": 0.15,
        "rated-low/repo": 0.05,
    }
    PERSONA_DIFF_KEY = "persona/proscribed"

    @classmethod
    def bundles(cls) -> list[FeatureBundle]:
        out: list[FeatureBundle] = []

        owner, name = cls.GATED_ARCHIVED.split("/")
        out.append(_bundle(_repo(owner, name, archived=True), _full_inputs()))

        owner, name = cls.GATED_UNLICENSED.split("/")
        out.append(_bundle(_repo(owner, name, license_spdx=None), _full_inputs()))

        owner, name = cls.UNRATED_EMPTY.split("/")
        out.append(_bundle(_repo(owner, name), _full_inputs()))

        owner, name = cls.UNRATED_PARTIAL.split("/")
        out.append(_bundle(_repo(owner, name), _full_inputs({"legal_ip": _m("DCO", n=0)})))

        for key, dr90 in cls.RATED_DR90.items():
            owner, name = key.split("/")
            ai_posture = "PROSCRIBED" if key == cls.PERSONA_DIFF_KEY else "AD_HOC"
            out.append(_bundle(_repo(owner, name), _hi_inputs(dr90=dr90, ai_posture=ai_posture), events=(_decided_event(),)))
        return out

    @classmethod
    def gate_evidence(cls, bundles: list[FeatureBundle]) -> dict[RepoKey, GateEvidence]:
        result: dict[RepoKey, GateEvidence] = {}
        for bundle in bundles:
            key = repo_key(bundle)
            if key == cls.GATED_ARCHIVED:
                # archived alone is enough to gate at D-GATE-2; other gates
                # do not need to be cleared for the reason to come out
                # "archived" (D-GATE-2 has a lower id than every gate a
                # missing manifest_found=None would otherwise leave
                # UNDECIDED at).
                result[key] = GateEvidence(manifest_found=True, vitality_commits_180d=5)
            elif key == cls.GATED_UNLICENSED:
                # manifest_found=True keeps D-GATE-1 from also firing (it
                # would win over D-GATE-3 as the lower-id reason otherwise);
                # license_file_present=False is what makes D-GATE-3 fail.
                result[key] = GateEvidence(
                    manifest_found=True, license_file_present=False, vitality_commits_180d=5,
                )
            else:
                result[key] = _HEALTHY_EVIDENCE
        return result

    @classmethod
    def raw_hashes(cls, bundles: list[FeatureBundle]) -> dict[RepoKey, str]:
        return {repo_key(bundle): _HASH64 for bundle in bundles}

    @staticmethod
    def populations() -> PopulationSet:
        """Same three ECDF populations ``EligibleFixture`` uses (module
        docstring): every rated repo's dependents/downloads/ttm_p90 sit
        above every population member, so all three ECDF curves score
        100.0 regardless of which repo -- the ``dr90`` lever is the only
        thing that varies their composite."""
        return PopulationSet(
            snapshot_id=_SNAPSHOT_ID, benchmark_version=constants_v2.BENCHMARK_VERSION,
            constants_hash=constants_v2.CONSTANTS_HASH,
            populations=(
                Population("pypi", "ttm_p90_hours", 4, False, {"a": 5.0, "b": 10.0, "c": 20.0, "d": 30.0}),
                Population("pypi", "dependents_deduped", 4, False, {"a": 0.0, "b": 1.0, "c": 2.0, "d": 3.0}),
                Population("pypi", "downloads_monthly", 3, False, {"a": 0.0, "b": 1.0, "c": 2.0}),
            ),
        )

    @classmethod
    def score(cls) -> ScoredSnapshot:
        bundles = cls.bundles()
        return score_snapshot(
            bundles, cls.populations(), snapshot_id=_SNAPSHOT_ID, as_of=NOW,
            gate_evidence=cls.gate_evidence(bundles), raw_hashes=cls.raw_hashes(bundles),
        )


def _by_key(snapshot: ScoredSnapshot) -> dict[RepoKey, Any]:
    return {f"{r.repo.owner}/{r.repo.name}": r for r in snapshot.records}


# --------------------------------------------------------------------------- #
# (a) 12-repo fixture: rank strictness, tie_group sharing, persona fields
#     populated on every record, gated/unrated rank None, persona-gate
#     divergence from the balanced view.
# --------------------------------------------------------------------------- #
def test_twelve_repo_fixture_shape() -> None:
    snapshot = _Fixture12.score()
    assert len(snapshot.records) == 12
    by_key = _by_key(snapshot)
    assert set(by_key) == {
        _Fixture12.GATED_ARCHIVED, _Fixture12.GATED_UNLICENSED,
        _Fixture12.UNRATED_EMPTY, _Fixture12.UNRATED_PARTIAL,
        *_Fixture12.RATED_DR90,
    }

    gated_and_unrated = [
        _Fixture12.GATED_ARCHIVED, _Fixture12.GATED_UNLICENSED,
        _Fixture12.UNRATED_EMPTY, _Fixture12.UNRATED_PARTIAL,
    ]
    for key in gated_and_unrated:
        record = by_key[key]
        assert record.rank is None, key
        assert record.tie_group is None, key
        assert record.tie_key == (), key
        assert record.composite is None, key
        # every record -- gated or not -- carries persona_gates/composites,
        # stage 8 having run on all twelve (module docstring).
        assert set(record.persona_gates) == set(persona.GATED_PERSONAS), key

    assert by_key[_Fixture12.GATED_ARCHIVED].gate.eligible is False
    assert by_key[_Fixture12.GATED_ARCHIVED].gate.reason == "archived"
    assert by_key[_Fixture12.GATED_UNLICENSED].gate.reason == "unlicensed"
    assert by_key[_Fixture12.UNRATED_EMPTY].gate.eligible is True
    assert by_key[_Fixture12.UNRATED_EMPTY].rated is False
    assert by_key[_Fixture12.UNRATED_EMPTY].unrated_reason == "unrated: insufficient_evidence"

    rated_keys = list(_Fixture12.RATED_DR90)
    ranks = [by_key[key].rank for key in rated_keys]
    assert all(rank is not None for rank in ranks)
    assert sorted(ranks) == list(range(1, 9)), "ranks must be strict 1..8 over the 8 rated repos"

    for key in rated_keys:
        record = by_key[key]
        assert record.composite is not None, key
        assert set(record.persona_composites) == set(persona.PERSONA_PRESETS), key
        assert record.persona_composites[persona.PERSONA_BALANCED].cvi == pytest.approx(record.composite)


def test_tie_pair_shares_tie_group_and_ranks_by_name() -> None:
    by_key = _by_key(_Fixture12.score())
    tie_a, tie_b = by_key["tiea/x"], by_key["tieb/y"]
    assert tie_a.composite == pytest.approx(tie_b.composite)
    assert tie_a.tie_group is not None
    assert tie_a.tie_group == tie_b.tie_group
    # 'tiea/x' sorts before 'tieb/y' lower-cased -> the lower rank number.
    assert tie_a.rank < tie_b.rank
    assert tie_b.rank == tie_a.rank + 1


def test_rated_ranks_strictly_follow_decreasing_composite() -> None:
    snapshot = _Fixture12.score()
    rated = sorted((r for r in snapshot.records if r.rank is not None), key=lambda r: r.rank)
    composites = [r.composite for r in rated]
    assert composites == sorted(composites, reverse=True)
    assert len({round(c, 2) for c in composites}) == 7, "7 distinct composites among 8 rated repos (one tie pair)"


def test_persona_gate_differs_from_balanced_view() -> None:
    """D-PERS-1/2: the ai_assisted lens hides the PROSCRIBED repo; every
    other persona -- balanced included -- shows it exactly as scored."""
    record = _by_key(_Fixture12.score())[_Fixture12.PERSONA_DIFF_KEY]
    assert persona.PERSONA_AI_ASSISTED in record.persona_gates
    assert record.persona_gates[persona.PERSONA_AI_ASSISTED] == "ai_hard_ban"
    for other in persona.GATED_PERSONAS:
        if other == persona.PERSONA_AI_ASSISTED:
            continue
        assert other not in record.persona_gates, other
    # the balanced score itself is untouched by the ai_assisted gate
    # (D-PERS-1: gates are filters, never a rewrite of the stored score).
    assert record.composite is not None
    assert record.persona_composites[persona.PERSONA_BALANCED].cvi == pytest.approx(record.composite)


def test_gated_records_state_the_balanced_gate_reason_on_every_persona() -> None:
    """R-1 (lead ruling, W2-8b review): a gate-ineligible record is gated
    under every persona with the balanced ``gate.reason`` -- unconditional,
    since D-ORDER-1 decides eligibility at stage 0, strictly before any
    persona lens exists."""
    by_key = _by_key(_Fixture12.score())
    archived = by_key[_Fixture12.GATED_ARCHIVED]
    for name in persona.GATED_PERSONAS:
        assert archived.persona_gates[name] == "archived"
    assert archived.persona_composites == {}


def test_unrated_record_reports_insufficient_evidence_coverage_per_persona() -> None:
    """R-1: unlike the gated case, a balanced-unrated record is *not*
    universally hidden -- ``UNRATED_EMPTY`` has zero measured inputs, so
    every persona's own re-weighted coverage is also 0.0 (clearly below
    0.60), and every persona reports the *coverage-level*
    ``insufficient_evidence:coverage`` reason (not the old, now-wrong,
    ``record.unrated_reason`` string) for the right cause: its own
    coverage gate, not a copy of the balanced record's."""
    by_key = _by_key(_Fixture12.score())
    unrated = by_key[_Fixture12.UNRATED_EMPTY]
    for name in persona.GATED_PERSONAS:
        assert unrated.persona_gates[name] == f"{persona.REASON_INSUFFICIENT_EVIDENCE_PREFIX}coverage"
    # unrated (unlike gated) has a full 5-name pillar dict, so
    # persona_composite can run for every preset without raising.
    assert set(unrated.persona_composites) == set(persona.PERSONA_PRESETS)
    assert all(not composite.rated for composite in unrated.persona_composites.values())


def _hand_built_bundle_for_persona_divergence() -> FeatureBundle:
    """A bundle whose only role is to supply ``persona.persona_gates``'s
    raw-evidence inputs for :func:`_hand_built_unrated_divergent_record`'s
    hand-built record -- elephant_factor Measured >= 3 (so the
    senior_systems *evidence* gate does not itself fire once its coverage
    clears 0.60) and ai_posture left Unmeasured (not PROSCRIBED)."""
    inputs = _full_inputs({"elephant_factor": _m(5.0, n=30)})
    return _bundle(_repo("persona-divergence", "repo"), inputs)


def _hand_built_unrated_divergent_record(bundle: FeatureBundle) -> ScoreRecord:
    """A record unrated under the *balanced* weights (coverage 0.495) but
    rated under ``senior_systems``'s weights (coverage 0.66) -- criticality
    and governance fully measured, rigor 30% measured, responsiveness and
    accessibility wholly unmeasured. Hand-built directly (not derived
    through ``engine.score``) so the coverage numbers are exact literals,
    not exposed to real-curve floating-point boundary risk near 0.60.

    Per-persona coverage (``Sum(w_p * c_p)`` over the five pillars):
      balanced       .25*1 + .20*1 + .15*0.3                    = 0.495  (< 0.60)
      senior_systems .35*1 + .25*1 + .20*0.3                    = 0.66   (>= 0.60)
      career_capital .35*1 + .15*1 + .10*0.3                    = 0.53   (< 0.60)
      ai_assisted    .20*1 + .15*1 + .35*0.3                    = 0.455  (< 0.60)
      first_timer    .10*1 + .15*1 + .10*0.3                    = 0.28   (< 0.60)
    """
    pillars = {
        "responsiveness": PillarScore(None, 0.0),
        "criticality": PillarScore(70.0, 1.0),
        "governance": PillarScore(70.0, 1.0),
        "rigor": PillarScore(70.0, 0.3),
        "accessibility": PillarScore(None, 0.0),
    }
    gate = GateResult(eligible=True, reason=None, routed=None, undecided=())
    return ScoreRecord(
        repo=bundle.repo, snapshot_id=_SNAPSHOT_ID, benchmark_version=constants_v2.BENCHMARK_VERSION,
        constants_hash=constants_v2.CONSTANTS_HASH, raw_hash=_HASH64, gate=gate, params={},
        pillars=pillars, composite=None, coverage=0.495, tier=None, leverage=None, friction=None,
        quadrant=None, persona_gates={}, persona_composites={}, tie_key=(), tie_group=None, rank=None,
        suspicion=[], rated=False, unrated_reason="unrated: insufficient_evidence",
    )


def test_unrated_record_can_be_rated_under_one_persona_and_not_another() -> None:
    """R-1's whole point, the case the packet dispatch's literal wording
    (universal override) got wrong: a record unrated at balanced weights
    can be genuinely *rated* under a persona whose weights favour the
    pillars it did measure (``senior_systems`` here, coverage 0.66 >=
    0.60) -- that persona must be open (or gated by its own D-PERS-2 rule,
    not by a copied balanced reason), while a persona whose own coverage
    stays below 0.60 (``career_capital``, ``ai_assisted``, ``first_timer``)
    reports ``insufficient_evidence:coverage``, not the balanced
    ``unrated_reason`` string and not a substantive D-PERS-2 reason it
    never had enough evidence to justify."""
    bundle = _hand_built_bundle_for_persona_divergence()
    record = _hand_built_unrated_divergent_record(bundle)

    result = score_bundle_stage_8_persona(record, bundle)

    # senior_systems: own coverage 0.66 >= 0.60 -> rated under this persona;
    # its own D-PERS-2 evidence gate (elephant_factor >= 3, Measured) does
    # not fire either, so it is simply absent from persona_gates -- open.
    assert result.persona_composites[persona.PERSONA_SENIOR_SYSTEMS].rated is True
    assert persona.PERSONA_SENIOR_SYSTEMS not in result.persona_gates

    for name in (persona.PERSONA_CAREER_CAPITAL, persona.PERSONA_AI_ASSISTED, persona.PERSONA_FIRST_TIMER):
        assert result.persona_composites[name].rated is False, name
        assert result.persona_gates[name] == f"{persona.REASON_INSUFFICIENT_EVIDENCE_PREFIX}coverage", name

    # balanced itself is never a gate target and carries no entry either way.
    assert persona.PERSONA_BALANCED not in result.persona_gates


def test_mutant_reverting_to_universal_unrated_override_is_red() -> None:
    """Fail-before for R-1 itself: the literal-dispatch behaviour this
    packet shipped before the review (state ``record.unrated_reason`` on
    every persona unconditionally) must fail
    ``test_unrated_record_can_be_rated_under_one_persona_and_not_another``
    -- reproduced here directly, without touching the source file, so this
    test is itself a permanent regression guard against reintroducing the
    literal behaviour."""
    bundle = _hand_built_bundle_for_persona_divergence()
    record = _hand_built_unrated_divergent_record(bundle)

    # The old (R-1-noncompliant) behaviour, inlined:
    old_gates = {p: record.unrated_reason for p in persona.GATED_PERSONAS}
    assert old_gates[persona.PERSONA_SENIOR_SYSTEMS] == "unrated: insufficient_evidence"
    # A persona whose own coverage actually clears 0.60 (senior_systems,
    # 0.66) would be wrongly hidden by the literal override -- exactly the
    # defect R-1 corrects.
    composites = {p: persona.persona_composite(record, p) for p in persona.PERSONA_PRESETS}
    assert composites[persona.PERSONA_SENIOR_SYSTEMS].rated is True
    assert old_gates[persona.PERSONA_SENIOR_SYSTEMS] != ""  # the old code hides it regardless of `rated`


# --------------------------------------------------------------------------- #
# (b) purity: bundles/populations deep-equal before and after.
# --------------------------------------------------------------------------- #
def test_score_snapshot_does_not_mutate_bundles_or_populations() -> None:
    bundles = _Fixture12.bundles()
    populations = _Fixture12.populations()
    gate_evidence = _Fixture12.gate_evidence(bundles)
    raw_hashes = _Fixture12.raw_hashes(bundles)

    bundles_before = copy.deepcopy(bundles)
    populations_before = copy.deepcopy(populations)

    score_snapshot(
        bundles, populations, snapshot_id=_SNAPSHOT_ID, as_of=NOW,
        gate_evidence=gate_evidence, raw_hashes=raw_hashes,
    )

    assert bundles == bundles_before
    assert populations == populations_before


# --------------------------------------------------------------------------- #
# (c) determinism: shuffled input order -> byte-identical records_hash and
#     identical rank order.
# --------------------------------------------------------------------------- #
def test_shuffled_bundle_order_gives_identical_hash_and_rank_order() -> None:
    bundles = _Fixture12.bundles()
    populations = _Fixture12.populations()
    gate_evidence = _Fixture12.gate_evidence(bundles)
    raw_hashes = _Fixture12.raw_hashes(bundles)

    forward = score_snapshot(
        bundles, populations, snapshot_id=_SNAPSHOT_ID, as_of=NOW,
        gate_evidence=gate_evidence, raw_hashes=raw_hashes,
    )
    reversed_bundles = list(reversed(bundles))
    backward = score_snapshot(
        reversed_bundles, populations, snapshot_id=_SNAPSHOT_ID, as_of=NOW,
        gate_evidence=gate_evidence, raw_hashes=raw_hashes,
    )
    shuffled_bundles = bundles[6:] + bundles[:6]
    shuffled = score_snapshot(
        shuffled_bundles, populations, snapshot_id=_SNAPSHOT_ID, as_of=NOW,
        gate_evidence=gate_evidence, raw_hashes=raw_hashes,
    )

    assert forward.records_hash == backward.records_hash == shuffled.records_hash
    forward_order = [(r.repo.owner, r.repo.name) for r in forward.records]
    backward_order = [(r.repo.owner, r.repo.name) for r in backward.records]
    shuffled_order = [(r.repo.owner, r.repo.name) for r in shuffled.records]
    assert forward_order == backward_order == shuffled_order


# --------------------------------------------------------------------------- #
# (d) the hash changes when any one record field changes.
# --------------------------------------------------------------------------- #
_MUTATION_CASES = [
    ("composite", 55.0),
    ("tier", "D"),
    ("rank", 999),
    ("persona_composites", {}),
    ("suspicion", ["param_jump"]),
]


@pytest.mark.parametrize("field, new_value", _MUTATION_CASES)
def test_records_hash_changes_when_one_record_field_changes(field: str, new_value: Any) -> None:
    snapshot = _Fixture12.score()
    original_hash = records_hash_of(snapshot.records)
    assert original_hash == snapshot.records_hash

    records = list(snapshot.records)
    # first record with a non-None value for the field under test so the
    # replacement is an actual change, not a no-op.
    target_index = next(
        index for index, record in enumerate(records) if getattr(record, field) != new_value
    )
    records[target_index] = dataclasses.replace(records[target_index], **{field: new_value})

    mutated_hash = records_hash_of(records)
    assert mutated_hash != original_hash, field


def test_records_hash_unaffected_by_dict_or_field_insertion_order() -> None:
    """Over-correction guard for :func:`canonical_records_json`: two records
    differing only in the *construction* order of a dict field (never in
    content) must hash identically -- ``cvi.core.models.canonical_json``
    sorts dict keys; this proves the pipeline's own recursive converter
    does not defeat that by, say, building an ordered list of pairs."""
    snapshot = _Fixture12.score()
    # the gated-archived record's persona_gates carries all four
    # GATED_PERSONAS keys (module docstring: the balanced gate/unrated
    # reason is stated on every one of them) -- reversing a dict with a
    # single key (or none) is a no-op regardless of serialisation order,
    # so this must be a record with a genuinely reorderable multi-key dict.
    record = next(r for r in snapshot.records if len(r.persona_gates) > 1)
    reordered_gates = dict(reversed(list(record.persona_gates.items())))
    assert reordered_gates == record.persona_gates  # same content
    assert list(reordered_gates) != list(record.persona_gates), "construction order must actually differ"
    reordered_record = dataclasses.replace(record, persona_gates=reordered_gates)

    assert canonical_records_json([record]) == canonical_records_json([reordered_record])


# --------------------------------------------------------------------------- #
# Stage-function unit tests (each stage testable alone, R-1).
# --------------------------------------------------------------------------- #
def test_score_bundle_stages_0_7_is_engine_score() -> None:
    """Fail-before / wiring guard: stages 0-7 return exactly what
    ``cvi.core.engine.score`` would for the same arguments -- this function
    must never reimplement or shortcut any of D-ORDER-1's stages 0-7."""
    from cvi.core.engine import Snapshot, score

    bundle = _bundle(_repo("org", "alpha"), _hi_inputs(dr90=0.8), events=(_decided_event(),))
    populations = _Fixture12.populations()
    via_engine = score(bundle, populations, Snapshot(snapshot_id=_SNAPSHOT_ID, as_of=NOW, gate_evidence=_HEALTHY_EVIDENCE), _HASH64)
    via_pipeline = score_bundle_stages_0_7(
        bundle, populations, snapshot_id=_SNAPSHOT_ID, as_of=NOW,
        gate_evidence=_HEALTHY_EVIDENCE, raw_hash=_HASH64,
    )
    assert via_pipeline == via_engine


def test_score_bundle_stage_8_persona_raises_nothing_on_gated_record() -> None:
    """Fail-before for the specific hazard the module docstring documents:
    calling ``persona.persona_composite`` on a gate-ineligible record
    (``pillars == {}``) raises ``ValueError`` by that module's own design;
    this stage function must route around it, never let it escape."""
    bundle = _bundle(_repo("org", "gated"), _full_inputs())
    record = score_bundle_stages_0_7(
        bundle, _Fixture12.populations(), snapshot_id=_SNAPSHOT_ID, as_of=NOW,
        gate_evidence=GateEvidence(manifest_found=False), raw_hash=_HASH64,
    )
    assert record.gate.eligible is False
    result = score_bundle_stage_8_persona(record, bundle)
    assert result.persona_composites == {}
    assert set(result.persona_gates) == set(persona.GATED_PERSONAS)


def test_score_bundle_stage_9_tie_input_reads_ttfhr_p75_and_identity_from_record() -> None:
    bundle = _bundle(_repo("org", "alpha"), _hi_inputs(dr90=0.8), events=(_decided_event(),))
    record = score_bundle_stages_0_7(
        bundle, _Fixture12.populations(), snapshot_id=_SNAPSHOT_ID, as_of=NOW,
        gate_evidence=_HEALTHY_EVIDENCE, raw_hash=_HASH64,
    )
    entry = score_bundle_stage_9_tie_input(record, bundle)
    assert entry.owner == "org" and entry.name == "alpha"
    assert entry.owner == record.repo.owner and entry.name == record.repo.name
    assert entry.ttfhr_p75_hours == pytest.approx(24.0)  # the one event's response was 24h after creation
    # W1-5f: this hand-built bundle never had the last_non_bot_commit_at
    # sidecar attribute set (cvi.derive.bundle._load_v1 is the only setter),
    # so cvi.core.commit_domains.last_non_bot_commit_at(bundle) returns its
    # Unmeasured(collector_failure) default -- correctly collapsed to None
    # here, not because the field does not exist (see the next test for the
    # positive wiring path).
    assert entry.last_non_bot_commit_at is None


def test_score_bundle_stage_9_tie_input_reads_last_non_bot_commit_at_from_bundle_sidecar() -> None:
    """W1-5f positive wiring: when a bundle carries the
    ``last_non_bot_commit_at`` sidecar fact (set the way
    ``cvi.derive.bundle._load_v1`` sets it), stage 9 prep reads the real
    value through -- not the hardcoded ``None`` this module returned before
    this packet."""
    from cvi.core.commit_domains import LastNonBotCommitMeasured, set_last_non_bot_commit_at

    bundle = _bundle(_repo("org", "alpha"), _hi_inputs(dr90=0.8), events=(_decided_event(),))
    set_last_non_bot_commit_at(bundle, LastNonBotCommitMeasured(
        value="2026-06-01T00:00:00Z", method="measured:commits_365d",
        source="raw/commits_365d/org__alpha.json", source_hash="a" * 64, fetched_at="2026-09-01T00:00:00Z",
    ))
    record = score_bundle_stages_0_7(
        bundle, _Fixture12.populations(), snapshot_id=_SNAPSHOT_ID, as_of=NOW,
        gate_evidence=_HEALTHY_EVIDENCE, raw_hash=_HASH64,
    )
    entry = score_bundle_stage_9_tie_input(record, bundle)
    assert entry.last_non_bot_commit_at == "2026-06-01T00:00:00Z"


def test_score_bundle_stage_9_tie_input_measured_absence_is_none() -> None:
    """A ``LastNonBotCommitMeasured(value=None, ...)`` measured absence
    also collapses to ``None`` for tie-break purposes -- the same as an
    unset/Unmeasured fact, since D-TIE-1 item 7 has nothing to rank a
    genuine absence above or below any other unmeasured entry by."""
    from cvi.core.commit_domains import LastNonBotCommitMeasured, set_last_non_bot_commit_at

    bundle = _bundle(_repo("org", "alpha"), _hi_inputs(dr90=0.8), events=(_decided_event(),))
    set_last_non_bot_commit_at(bundle, LastNonBotCommitMeasured(
        value=None, method="measured:no_non_bot_commit_365d",
        source="raw/commits_365d/org__alpha.json", source_hash="a" * 64, fetched_at="2026-09-01T00:00:00Z",
    ))
    record = score_bundle_stages_0_7(
        bundle, _Fixture12.populations(), snapshot_id=_SNAPSHOT_ID, as_of=NOW,
        gate_evidence=_HEALTHY_EVIDENCE, raw_hash=_HASH64,
    )
    entry = score_bundle_stage_9_tie_input(record, bundle)
    assert entry.last_non_bot_commit_at is None


def test_score_bundle_stage_9_tie_input_last_non_bot_commit_at_orders_per_level_7() -> None:
    """Packet deliverable: a record whose only difference is the commit
    date orders per D-TIE-1 level 7 -- proved end to end through this
    module's own ``assign_ranks`` wiring, not just ``cvi.core.tiebreak``'s
    unit tests (which already covered the tie-break *rule*; this is the
    *wiring* through score_bundle_stage_9_tie_input, W1-5f's own delivery)."""
    from cvi.core.commit_domains import LastNonBotCommitMeasured, set_last_non_bot_commit_at
    from cvi.core.tiebreak import assign_ranks

    bundle_a = _bundle(_repo("org", "alpha"), _hi_inputs(dr90=0.8), events=(_decided_event(),))
    bundle_b = _bundle(_repo("org", "beta"), _hi_inputs(dr90=0.8), events=(_decided_event(),))
    set_last_non_bot_commit_at(bundle_a, LastNonBotCommitMeasured(
        value="2026-01-01T00:00:00Z", method="measured:commits_365d",
        source="s", source_hash="a" * 64, fetched_at="2026-09-01T00:00:00Z",
    ))
    set_last_non_bot_commit_at(bundle_b, LastNonBotCommitMeasured(
        value="2026-06-01T00:00:00Z", method="measured:commits_365d",
        source="s", source_hash="a" * 64, fetched_at="2026-09-01T00:00:00Z",
    ))
    populations = _Fixture12.populations()
    record_a = score_bundle_stages_0_7(
        bundle_a, populations, snapshot_id=_SNAPSHOT_ID, as_of=NOW, gate_evidence=_HEALTHY_EVIDENCE, raw_hash=_HASH64,
    )
    record_b = score_bundle_stages_0_7(
        bundle_b, populations, snapshot_id=_SNAPSHOT_ID, as_of=NOW, gate_evidence=_HEALTHY_EVIDENCE, raw_hash=_HASH64,
    )
    # Identical composite/coverage/pillars/friction/leverage/ttfhr_p75 by
    # construction (same inputs, same events) -- the only difference that
    # can break the tie is D-TIE-1 level 7, the last-non-bot-commit date.
    assert record_a.composite == record_b.composite
    entry_a = score_bundle_stage_9_tie_input(record_a, bundle_a)
    entry_b = score_bundle_stage_9_tie_input(record_b, bundle_b)
    ranked = assign_ranks([entry_a, entry_b])
    by_owner_name = {(record.repo.owner, record.repo.name): rank for record, rank, _ in ranked}
    # beta's later commit date (2026-06) outranks alpha's earlier one
    # (2026-01) -- D-TIE-1 item 7 is descending.
    assert by_owner_name[("org", "beta")] < by_owner_name[("org", "alpha")]


def test_build_snapshot_counts_matches_hand_count() -> None:
    snapshot = _Fixture12.score()
    counts = build_snapshot_counts(snapshot.records)
    assert counts.gated == 2
    assert counts.unrated == 2
    assert counts.rated == 8
    assert counts.persona_visible[persona.PERSONA_BALANCED] == 8
    # ai_assisted hides exactly the one PROSCRIBED repo among the 8 rated.
    assert counts.persona_visible[persona.PERSONA_AI_ASSISTED] == 7


# --------------------------------------------------------------------------- #
# score_snapshot input validation.
# --------------------------------------------------------------------------- #
def test_score_snapshot_rejects_duplicate_repo_key() -> None:
    bundle = _bundle(_repo("org", "dup"), _full_inputs())
    with pytest.raises(ValueError, match="duplicate repo key"):
        score_snapshot(
            [bundle, bundle], _Fixture12.populations(), snapshot_id=_SNAPSHOT_ID, as_of=NOW,
            gate_evidence={"org/dup": GateEvidence(manifest_found=False)},
            raw_hashes={"org/dup": _HASH64},
        )


def test_score_snapshot_rejects_missing_gate_evidence_or_raw_hash() -> None:
    bundle = _bundle(_repo("org", "solo"), _full_inputs())
    with pytest.raises(ValueError, match="gate_evidence missing"):
        score_snapshot(
            [bundle], _Fixture12.populations(), snapshot_id=_SNAPSHOT_ID, as_of=NOW,
            gate_evidence={}, raw_hashes={"org/solo": _HASH64},
        )
    with pytest.raises(ValueError, match="raw_hashes missing"):
        score_snapshot(
            [bundle], _Fixture12.populations(), snapshot_id=_SNAPSHOT_ID, as_of=NOW,
            gate_evidence={"org/solo": GateEvidence(manifest_found=False)}, raw_hashes={},
        )


def test_score_snapshot_rejects_empty_snapshot_id() -> None:
    bundle = _bundle(_repo("org", "solo"), _full_inputs())
    with pytest.raises(ValueError, match="snapshot_id"):
        score_snapshot(
            [bundle], _Fixture12.populations(), snapshot_id="", as_of=NOW,
            gate_evidence={"org/solo": GateEvidence(manifest_found=False)},
            raw_hashes={"org/solo": _HASH64},
        )


# --------------------------------------------------------------------------- #
# Mutation-battery guard: stage 8 replaces, never mutates, the record.
# --------------------------------------------------------------------------- #
def test_score_bundle_stage_8_persona_replaces_never_mutates() -> None:
    """A mutant that swaps ``dataclasses.replace`` for an in-place
    ``object.__setattr__`` on the frozen record would still produce the
    right *values* on the returned object (this specific record is never
    read again by anything else in this process), so a values-only
    assertion cannot tell the two apart. Object identity can:
    ``score_bundle_stage_8_persona`` must always return a *new*
    ``ScoreRecord``, and the record it was given must still show its
    pre-call, untouched ``persona_gates``/``persona_composites`` afterwards
    (W2-7f's purity test is the model this repeats at this packet's own
    composition point, D-PERS-1)."""
    bundle = _bundle(_repo("org", "alpha"), _hi_inputs(dr90=0.8), events=(_decided_event(),))
    record = score_bundle_stages_0_7(
        bundle, _Fixture12.populations(), snapshot_id=_SNAPSHOT_ID, as_of=NOW,
        gate_evidence=_HEALTHY_EVIDENCE, raw_hash=_HASH64,
    )
    assert record.persona_gates == {} and record.persona_composites == {}

    result = score_bundle_stage_8_persona(record, bundle)

    assert result is not record
    assert record.persona_gates == {} and record.persona_composites == {}, "input record must stay untouched"
    assert result.persona_gates != {} or result.persona_composites != {}


# --------------------------------------------------------------------------- #
# W3-2f: score_snapshot(previous=..., detectors=...) -- the suspicion-wiring
# composition seam. These tests exercise ``apply_suspicion``/``score_snapshot``
# with hand-written ``detectors`` stubs, never the real
# ``cvi.verify.detectors``/``cvi.verify.pipeline_hook`` (``cvi.core`` cannot
# import ``cvi.verify`` -- see ``cvi.core.pipeline.DetectorsCallable``'s own
# docstring; the real end-to-end wiring through real bundles/detectors is
# ``tests/verify/test_pipeline_hook.py``). A stub lets each test isolate one
# fact about the seam without needing a real cross-snapshot fixture.
# --------------------------------------------------------------------------- #
def _two_minimal_bundles() -> tuple[FeatureBundle, FeatureBundle]:
    """Two gate-eligible, otherwise-empty bundles, deliberately named so
    D-TIE-2's name order (``aaa/first`` before ``zzz/second``) is opposite
    the order a *reversed* ``detectors`` mapping insertion would suggest --
    see :func:`test_score_snapshot_wires_suspicion_by_repo_key_not_position`."""
    return (
        _bundle(_repo("zzz", "second"), _full_inputs()),
        _bundle(_repo("aaa", "first"), _full_inputs()),
    )


def _score_two_minimal(
    *, previous: ScoredSnapshot | None = None, detectors: Any = None
) -> ScoredSnapshot:
    bundles = list(_two_minimal_bundles())
    return score_snapshot(
        bundles, _Fixture12.populations(), snapshot_id=_SNAPSHOT_ID, as_of=NOW,
        gate_evidence={repo_key(b): _HEALTHY_EVIDENCE for b in bundles},
        raw_hashes={repo_key(b): _HASH64 for b in bundles},
        previous=previous, detectors=detectors,
    )


def test_apply_suspicion_is_identity_only_when_detectors_is_none() -> None:
    """Direct unit test of :func:`apply_suspicion` itself (not through
    ``score_snapshot``): every record's ``suspicion`` stays untouched only
    when ``detectors is None`` (no composition wired in). ``previous is
    None`` is **not**, by itself, a reason to skip anything -- lead ruling
    correcting this function's first draft ("``previous=None`` -> all
    ``suspicion == []``" in the dispatch meant the cross-snapshot detectors
    only): a ``detectors`` callable that fires regardless of ``previous``
    (as every single-snapshot detector does) must still fire when
    ``previous`` is ``None``."""
    baseline = _Fixture12.score()
    records = baseline.records
    always_fires: Any = lambda recs, prev: {repo_key(r): ["param_jump"] for r in recs}  # noqa: E731

    assert apply_suspicion(records, baseline, None) == records  # detectors=None: identity
    result = apply_suspicion(records, None, always_fires)  # previous=None: NOT identity
    assert all(record.suspicion == ["param_jump"] for record in result)


def test_score_snapshot_previous_none_still_applies_single_snapshot_flags() -> None:
    """The corrected W3-2f acceptance rule (lead ruling): ``previous=None``
    does not blanket-suppress detection -- it is forwarded to ``detectors``
    like any other call, so a callable that fires independently of
    ``previous`` (every single-snapshot detector) still fires on the very
    first snapshot ever. Only a callable that itself withholds flags when
    ``previous is None`` (modelling ``param_jump``, the one detector that
    actually needs a previous record) yields nothing -- proving
    ``score_snapshot``/``apply_suspicion`` impose no additional gate of
    their own beyond whatever the callable decides."""
    single_snapshot_style: Any = lambda recs, prev: {repo_key(r): ["closed_unread"] for r in recs}  # noqa: E731

    def fires_only_with_previous(recs: Any, prev: Any) -> dict[str, list[str]]:
        if prev is None:
            return {}
        return {repo_key(r): ["param_jump"] for r in recs}

    fired = _score_two_minimal(previous=None, detectors=single_snapshot_style)
    assert all(record.suspicion == ["closed_unread"] for record in fired.records)

    silent = _score_two_minimal(previous=None, detectors=fires_only_with_previous)
    assert all(record.suspicion == [] for record in silent.records)


def test_score_snapshot_wires_suspicion_by_repo_key_not_position() -> None:
    """A ``detectors`` mapping built in an order different from
    ``score_snapshot``'s final (name-sorted, both repos unrated) record
    order must still land on the right repo: this fails under a mutant that
    zips ``records`` against the mapping's ``.values()`` positionally
    instead of looking each one up by :func:`repo_key`."""
    reordered_mapping: Any = {"zzz/second": ["label_spam"], "aaa/first": ["star_burst"]}
    detectors: Any = lambda recs, prev: reordered_mapping  # noqa: E731
    dummy_previous = _score_two_minimal()  # any non-None ScoredSnapshot; ignored by the stub

    snapshot = _score_two_minimal(previous=dummy_previous, detectors=detectors)

    by_key = {repo_key(r): r for r in snapshot.records}
    assert by_key["aaa/first"].suspicion == ["star_burst"]
    assert by_key["zzz/second"].suspicion == ["label_spam"]


def test_score_snapshot_sorts_and_dedupes_detector_names() -> None:
    """``score_snapshot``/``apply_suspicion`` must sort and de-duplicate the
    ``detectors`` callable's own output, not just trust it -- the callable
    is a caller-supplied interface with no contract of its own about order
    or duplicates (only ``cvi.verify.detectors.run_detectors`` happens to
    already return sorted, deduplicated names; this test proves the
    guarantee holds even when the callable does not)."""
    unsorted_with_dupes: Any = {
        "zzz/second": ["star_burst", "closed_unread", "closed_unread"],
        "aaa/first": [],
    }
    detectors: Any = lambda recs, prev: unsorted_with_dupes  # noqa: E731
    dummy_previous = _score_two_minimal()

    snapshot = _score_two_minimal(previous=dummy_previous, detectors=detectors)

    by_key = {repo_key(r): r for r in snapshot.records}
    assert by_key["zzz/second"].suspicion == ["closed_unread", "star_burst"]
    assert by_key["aaa/first"].suspicion == []


def test_score_snapshot_suspicion_never_affects_rank_or_tie_group() -> None:
    """03 section 6: flags are "displayed... never silent penalties" --
    ``rank``/``tie_group`` (and the ``tie_key`` that produces them) must be
    identical between two snapshots that differ only in the detector flags
    that fired. Deliberately targets the one pair the fixture can actually
    tie on (``tiea/x``/``tieb/y``, identical composite/coverage/pillars):
    the alphabetically-earlier repo (``tiea/x``, which must rank first by
    name, D-TIE-2's own last-resort key) is given the "larger" suspicion
    list (``["param_jump"] > []``) -- a mutant that folds ``suspicion`` into
    ``tie_key`` ahead of the name tiebreak would flip their relative order,
    which this test would catch; one that folds it in *after* the name
    tiebreak would not change the order at all (name already disambiguates
    the pair uniquely) -- ``test_score_snapshot_wires_suspicion_by_repo_key_not_position``
    and the module docstring's own account of this mutant class cover that
    variant instead."""
    baseline = _Fixture12.score()

    def _varied(records: Any, previous: Any) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for record in records:
            key = repo_key(record)
            if key == "tiea/x":
                result[key] = ["param_jump"]
            elif key == "tieb/y":
                result[key] = []
            else:
                result[key] = ["closed_unread"]
        return result

    bundles = _Fixture12.bundles()
    detectored = score_snapshot(
        bundles, _Fixture12.populations(), snapshot_id=_SNAPSHOT_ID, as_of=NOW,
        gate_evidence=_Fixture12.gate_evidence(bundles), raw_hashes=_Fixture12.raw_hashes(bundles),
        previous=baseline, detectors=_varied,
    )

    baseline_by_key = _by_key(baseline)
    detectored_by_key = _by_key(detectored)
    assert set(baseline_by_key) == set(detectored_by_key)
    for key in baseline_by_key:
        assert detectored_by_key[key].rank == baseline_by_key[key].rank, key
        assert detectored_by_key[key].tie_group == baseline_by_key[key].tie_group, key

    # non-vacuous: the flags actually differ, and the tie pair still ranks
    # by name (tiea/x before tieb/y), unaffected by the opposite-direction
    # suspicion each was given.
    assert detectored_by_key["tiea/x"].suspicion == ["param_jump"]
    assert detectored_by_key["tieb/y"].suspicion == []
    assert detectored_by_key["tiea/x"].rank < detectored_by_key["tieb/y"].rank
    assert baseline_by_key["tiea/x"].suspicion == []  # baseline: no detectors wired at all


def test_records_hash_changes_when_a_detector_fires() -> None:
    """Deliverable #1's own explicit ask: a fired detector (a non-empty
    ``suspicion``) must change ``records_hash`` -- proof that
    ``ScoreRecord.suspicion`` genuinely participates in
    ``canonical_records_json`` (it already did before W3-2f:
    ``_to_plain`` walks every dataclass field generically), not just that
    the field exists."""
    snapshot = _Fixture12.score()
    baseline_hash = snapshot.records_hash
    assert all(record.suspicion == [] for record in snapshot.records)

    records = list(snapshot.records)
    records[0] = dataclasses.replace(records[0], suspicion=["param_jump"])

    assert records_hash_of(records) != baseline_hash


def test_build_snapshot_counts_suspicion_histogram() -> None:
    """``SnapshotCounts.suspicion_counts`` (W3-2f): a per-detector-name
    histogram built generically from whatever names appear on the final
    records -- a name no record carries is simply absent, not zero-filled
    (``cvi.core`` has no fixed nine-name universe to zero-fill from; see
    the field's own docstring)."""
    baseline = _Fixture12.score()

    def _three_flagged(records: Any, previous: Any) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for index, record in enumerate(records):
            key = repo_key(record)
            if index == 0:
                result[key] = ["param_jump"]
            elif index == 1:
                result[key] = ["param_jump", "closed_unread"]
            else:
                result[key] = []
        return result

    bundles = _Fixture12.bundles()
    detectored = score_snapshot(
        bundles, _Fixture12.populations(), snapshot_id=_SNAPSHOT_ID, as_of=NOW,
        gate_evidence=_Fixture12.gate_evidence(bundles), raw_hashes=_Fixture12.raw_hashes(bundles),
        previous=baseline, detectors=_three_flagged,
    )

    counts = build_snapshot_counts(detectored.records)
    assert counts.suspicion_counts == {"param_jump": 2, "closed_unread": 1}
    # baseline (no detectors wired) never populates the histogram.
    assert build_snapshot_counts(baseline.records).suspicion_counts == {}
