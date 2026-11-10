"""Adversarial tests for the top-level Porchlamp scoring engine (porchlamp.engine).

Target bar: make the life of the code writer hell. Every test asserts an
exact oracle (hand-computed value), not a property a no-op engine also
satisfies. The dependency-injection seam (``pillar_scorers``) is exercised
with dummies that RAISE if called, so any accidental pillar call during a
Stage-0 short-circuit fails loudly instead of passing vacuously.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from porchlamp.constants import PERSONA_PRESETS
from porchlamp.engine import PILLAR_NAMES, assign_tier, score_repository
from porchlamp.models import (
    AIPosture,
    AuthorAssociation,
    Ecosystem,
    LegalIPModel,
    Persona,
    PillarScores,
    PromotionLadder,
    Quadrant,
    RawPREvent,
    RepoMeta,
    Tier,
)

NOW = datetime(2026, 9, 11, 12, 0, 0)


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
def make_meta(**overrides) -> RepoMeta:
    """A repo that passes every Stage-0 gate by default."""
    base = dict(
        owner="acme",
        name="widget",
        ecosystem=Ecosystem.PYPI,
        active_nonbot_commits_180d=42,
        off_platform_reviews=False,
        hard_ai_ban=False,
    )
    base.update(overrides)
    return RepoMeta(**base)


def make_pr(
    pr_number: int = 1,
    created_days_ago: float = 1.0,
    merged: bool = True,
    external: bool = True,
    latency_hours: float | None = 6.0,
    comments: int = 2,
    is_open: bool = False,
) -> RawPREvent:
    created = NOW - timedelta(days=created_days_ago)
    closed = None if is_open else created + timedelta(hours=latency_hours or 0.0)
    return RawPREvent(
        repo_owner="acme",
        repo_name="widget",
        pr_number=pr_number,
        created_at=created,
        author_login="contributor",
        author_association=AuthorAssociation.FIRST_TIME,
        author_prior_merged_commits=0,
        closed_at=closed,
        merged_at=None if is_open else (closed if merged else None),
        is_external=external,
        response_latency_hours=latency_hours,
        maintainer_review_comments=comments,
    )


def constant_scorers(value: float) -> dict:
    """Five pillar scorers that all return ``value`` and record their calls."""
    calls: list[str] = []

    def make(name: str):
        def scorer(*args, **kwargs):
            calls.append(name)
            return value

        return scorer

    scorers = {name: make(name) for name in PILLAR_NAMES}
    scorers["__calls__"] = calls  # type: ignore[assignment]
    return scorers


def pillars_from(values: dict[str, float]) -> PillarScores:
    return PillarScores(
        responsiveness=values["responsiveness"],
        criticality=values["criticality"],
        governance=values["governance"],
        rigor=values["rigor"],
        accessibility=values["accessibility"],
    )


def hand_composite(values: dict[str, float], persona: Persona) -> float:
    """Independent oracle: raw weighted sum, rounded to 2 decimals exactly
    as porchlamp.persona.reproject does (round-half-even on the raw float)."""
    raw = sum(PERSONA_PRESETS[persona][k] * values[k] for k in PILLAR_NAMES)
    return round(raw, 2)


# --------------------------------------------------------------------------- #
# assign_tier — exact boundary behaviour
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("porchlamp", "expected"),
    [
        (89.99, Tier.S),
        (90.0, Tier.S_PLUS),
        (79.99, Tier.A),
        (80.0, Tier.S),
        (69.99, Tier.B),
        (70.0, Tier.A),
        (54.99, Tier.C),
        (55.0, Tier.B),
        (39.99, Tier.D),
        (40.0, Tier.C),
        (0.0, Tier.D),
        (100.0, Tier.S_PLUS),
    ],
)
def test_assign_tier_boundaries(porchlamp: float, expected: Tier) -> None:
    assert assign_tier(porchlamp) is expected


def test_assign_tier_interior_values() -> None:
    assert assign_tier(95.0) is Tier.S_PLUS
    assert assign_tier(85.0) is Tier.S
    assert assign_tier(60.0) is Tier.B
    assert assign_tier(45.0) is Tier.C
    assert assign_tier(1.0) is Tier.D


def test_assign_tier_negative_maps_to_d() -> None:
    # Out-of-domain input must not raise; the floor tier is D.
    assert assign_tier(-0.5) is Tier.D


# --------------------------------------------------------------------------- #
# Stage-0 short-circuit: pillars must NEVER be called when gated
# --------------------------------------------------------------------------- #
def test_stage0_maintenance_gate_short_circuits_pillars() -> None:
    meta = make_meta(active_nonbot_commits_180d=0)
    scorers = constant_scorers(80.0)

    result = score_repository(meta, [], NOW, pillar_scorers=scorers)

    assert result.is_stage0_disqualified is True
    assert result.disqualification_reason is not None
    assert "180" in result.disqualification_reason
    assert result.rank_tier is Tier.D
    assert result.overall_porchlamp == 0.0
    assert result.leverage_score == 0.0
    assert result.friction_score == 100.0
    assert result.matrix_quadrant is Quadrant.GRAVEYARD
    assert result.pillar_scores is None
    assert result.stage0 is not None and result.stage0.passed is False
    # The injected dummies record every call; a gated run must call none.
    assert scorers["__calls__"] == []


def test_stage0_ghost_rate_gate_short_circuits_pillars() -> None:
    # 4 open external PRs, all >14d old with zero maintainer comments ->
    # GR_14 = 1.0 > 0.50 -> Black Hole.
    prs = [
        make_pr(pr_number=i, created_days_ago=30.0, merged=False, comments=0,
                is_open=True)
        for i in range(1, 5)
    ]
    scorers = constant_scorers(80.0)

    result = score_repository(make_meta(), prs, NOW, pillar_scorers=scorers)

    assert result.is_stage0_disqualified is True
    assert result.disqualification_reason is not None
    assert "ghost" in result.disqualification_reason.lower()
    assert result.rank_tier is Tier.D
    assert result.stage0 is not None and result.stage0.is_black_hole is True
    assert scorers["__calls__"] == []


def test_stage0_ai_ban_disqualifies_ai_persona_only() -> None:
    meta = make_meta(hard_ai_ban=True)
    scorers = constant_scorers(80.0)

    ai_result = score_repository(
        meta, [], NOW, persona=Persona.AI_ASSISTED, pillar_scorers=scorers
    )
    assert ai_result.is_stage0_disqualified is True
    assert ai_result.stage0 is not None
    assert ai_result.stage0.is_ai_ban_disqualified is True
    assert ai_result.rank_tier is Tier.D
    assert scorers["__calls__"] == []

    # Same repo, human persona: gate 4 must NOT fire; pillars run.
    scorers2 = constant_scorers(80.0)
    human_result = score_repository(
        meta, [], NOW, persona=Persona.BALANCED, pillar_scorers=scorers2
    )
    assert human_result.is_stage0_disqualified is False
    assert sorted(scorers2["__calls__"]) == sorted(PILLAR_NAMES)


def test_stage0_off_platform_is_routing_not_disqualification() -> None:
    # torvalds/linux ground truth: off-platform -> specialized routing,
    # NOT a hard gate failure. The engine must score it normally.
    meta = make_meta(owner="torvalds", name="linux", off_platform_reviews=True)
    scorers = constant_scorers(80.0)

    result = score_repository(meta, [], NOW, pillar_scorers=scorers)

    assert result.is_stage0_disqualified is False
    assert result.disqualification_reason is None
    assert result.stage0 is not None
    assert result.stage0.passed is True
    assert result.stage0.is_off_platform is True
    assert sorted(scorers["__calls__"]) == sorted(PILLAR_NAMES)
    assert result.overall_porchlamp == 80.0


# --------------------------------------------------------------------------- #
# Full pipeline with injected scorers — exact oracles
# --------------------------------------------------------------------------- #
def test_full_pipeline_all_80_balanced() -> None:
    scorers = constant_scorers(80.0)
    result = score_repository(make_meta(), [], NOW, pillar_scorers=scorers)

    # Leverage = .5*80 + .3*80 + .2*80 = 80.0
    # Friction = .5*20 + .3*20 + .2*20 = 20.0
    # 80 >= 50 leverage, 20 < 50 friction -> SWEET_SPOT
    # BALANCED composite = 80.0 -> Tier S
    assert result.is_stage0_disqualified is False
    assert result.leverage_score == 80.0
    assert result.friction_score == 20.0
    assert result.matrix_quadrant is Quadrant.SWEET_SPOT
    assert result.overall_porchlamp == 80.0
    assert result.rank_tier is Tier.S
    assert result.pillar_scores is not None
    assert result.pillar_scores.as_dict() == {
        "responsiveness": 80.0,
        "criticality": 80.0,
        "governance": 80.0,
        "rigor": 80.0,
        "accessibility": 80.0,
    }
    assert sorted(scorers["__calls__"]) == sorted(PILLAR_NAMES)


def test_full_pipeline_all_100_is_s_plus() -> None:
    result = score_repository(
        make_meta(), [], NOW, pillar_scorers=constant_scorers(100.0)
    )
    assert result.overall_porchlamp == 100.0
    assert result.rank_tier is Tier.S_PLUS
    assert result.leverage_score == 100.0
    assert result.friction_score == 0.0
    assert result.matrix_quadrant is Quadrant.SWEET_SPOT


def test_full_pipeline_all_0_is_graveyard_d() -> None:
    result = score_repository(
        make_meta(), [], NOW, pillar_scorers=constant_scorers(0.0)
    )
    assert result.overall_porchlamp == 0.0
    assert result.rank_tier is Tier.D
    assert result.leverage_score == 0.0
    assert result.friction_score == 100.0
    assert result.matrix_quadrant is Quadrant.GRAVEYARD


def test_full_pipeline_crucible_quadrant() -> None:
    # High leverage (criticality/rigor/governance = 100) + high friction
    # (responsiveness/accessibility = 0) -> CRUCIBLE.
    scorers = {
        "responsiveness": lambda *a, **k: 0.0,
        "criticality": lambda *a, **k: 100.0,
        "governance": lambda *a, **k: 100.0,
        "rigor": lambda *a, **k: 100.0,
        "accessibility": lambda *a, **k: 0.0,
    }
    result = score_repository(make_meta(), [], NOW, pillar_scorers=scorers)
    assert result.leverage_score == 100.0
    assert result.friction_score == 70.0  # .5*100 + .3*0 + .2*100
    assert result.matrix_quadrant is Quadrant.CRUCIBLE


def test_full_pipeline_nursery_quadrant() -> None:
    # Low leverage + low friction -> NURSERY.
    scorers = {
        "responsiveness": lambda *a, **k: 100.0,
        "criticality": lambda *a, **k: 0.0,
        "governance": lambda *a, **k: 0.0,
        "rigor": lambda *a, **k: 0.0,
        "accessibility": lambda *a, **k: 100.0,
    }
    result = score_repository(make_meta(), [], NOW, pillar_scorers=scorers)
    assert result.leverage_score == 0.0
    assert result.friction_score == 30.0  # .5*0 + .3*100 + .2*0
    assert result.matrix_quadrant is Quadrant.NURSERY


def test_result_identity_fields_passthrough() -> None:
    meta = make_meta(owner="vllm-project", name="vllm", ecosystem=Ecosystem.PYPI)
    result = score_repository(meta, [], NOW, pillar_scorers=constant_scorers(80.0))
    assert result.owner == "vllm-project"
    assert result.name == "vllm"
    assert result.ecosystem is Ecosystem.PYPI


# --------------------------------------------------------------------------- #
# Persona handling
# --------------------------------------------------------------------------- #
def test_rigor_scorer_receives_persona_is_ai_flag() -> None:
    captured: dict[str, object] = {}

    def rigor_scorer(meta, persona_is_ai=False):
        captured["persona_is_ai"] = persona_is_ai
        return 80.0

    scorers = constant_scorers(80.0)
    scorers["rigor"] = rigor_scorer

    score_repository(make_meta(), [], NOW, persona=Persona.AI_ASSISTED,
                     pillar_scorers=scorers)
    assert captured["persona_is_ai"] is True

    captured.clear()
    score_repository(make_meta(), [], NOW, persona=Persona.BALANCED,
                     pillar_scorers=scorers)
    assert captured["persona_is_ai"] is False


def test_persona_composites_hand_computed() -> None:
    # Pillars 100/0/100/0/100 (resp/crit/gov/rigor/acc).
    values = {
        "responsiveness": 100.0,
        "criticality": 0.0,
        "governance": 100.0,
        "rigor": 0.0,
        "accessibility": 100.0,
    }
    scorers = {
        "responsiveness": lambda *a, **k: 100.0,
        "criticality": lambda *a, **k: 0.0,
        "governance": lambda *a, **k: 100.0,
        "rigor": lambda *a, **k: 0.0,
        "accessibility": lambda *a, **k: 100.0,
    }
    meta = make_meta()

    balanced = score_repository(meta, [], NOW, persona=Persona.BALANCED,
                                pillar_scorers=scorers)
    ai = score_repository(meta, [], NOW, persona=Persona.AI_ASSISTED,
                          pillar_scorers=scorers)

    # BALANCED: .25*100 + .25*0 + .20*100 + .15*0 + .15*100 = 60.0
    assert balanced.overall_porchlamp == hand_composite(values, Persona.BALANCED) == 60.0
    assert balanced.rank_tier is Tier.B
    # AI_ASSISTED: .20*100 + .20*0 + .15*100 + .35*0 + .10*100 = 45.0
    assert ai.overall_porchlamp == hand_composite(values, Persona.AI_ASSISTED) == 45.0
    assert ai.rank_tier is Tier.C
    # Same pillars, different persona -> different composite and tier.
    assert balanced.overall_porchlamp != ai.overall_porchlamp
    # Quadrant is persona-independent (Phase 4 precedes Phase 5).
    # leverage = .5*P2 + .3*P4 + .2*P3 = .2*100 (governance) = 20.0;
    # friction = .5*(100-P1) + .3*(100-P3) + .2*(100-P5) = 0.0.
    assert balanced.matrix_quadrant is ai.matrix_quadrant is Quadrant.NURSERY
    assert balanced.leverage_score == ai.leverage_score == 20.0
    assert balanced.friction_score == ai.friction_score == 0.0


def test_all_five_personas_project_distinct_composites() -> None:
    # Pillars chosen so no two persona presets coincide.
    scorers = {
        "responsiveness": lambda *a, **k: 10.0,
        "criticality": lambda *a, **k: 20.0,
        "governance": lambda *a, **k: 30.0,
        "rigor": lambda *a, **k: 40.0,
        "accessibility": lambda *a, **k: 50.0,
    }
    values = {
        "responsiveness": 10.0,
        "criticality": 20.0,
        "governance": 30.0,
        "rigor": 40.0,
        "accessibility": 50.0,
    }
    composites = {}
    for persona in Persona:
        # elephant_factor=3: the SENIOR_SYSTEMS persona now enforces the
        # spec-4 multi-vendor gate (EF >= 3) at Stage 0; without it this
        # test's meta would be disqualified before persona projection.
        result = score_repository(
            make_meta(elephant_factor=3), [], NOW, persona=persona,
            pillar_scorers=scorers,
        )
        composites[persona] = result.overall_porchlamp
        assert result.overall_porchlamp == hand_composite(values, persona)
    assert len(set(composites.values())) == len(Persona)


# --------------------------------------------------------------------------- #
# Default-argument wiring
# --------------------------------------------------------------------------- #
def test_default_populations_are_empty_lists_not_none() -> None:
    seen: dict[str, object] = {}

    def resp(pr_events, ttm_p90_population, now):
        seen["ttm"] = ttm_p90_population
        return 50.0

    def crit(meta, dependents_population, downloads_population):
        seen["dep"] = dependents_population
        seen["dl"] = downloads_population
        return 50.0

    scorers = {
        "responsiveness": resp,
        "criticality": crit,
        "governance": lambda *a, **k: 50.0,
        "rigor": lambda *a, **k: 50.0,
        "accessibility": lambda *a, **k: 50.0,
    }
    score_repository(make_meta(), [], NOW, pillar_scorers=scorers)
    assert seen["ttm"] == []
    assert seen["dep"] == []
    assert seen["dl"] == []


def test_explicit_populations_are_passed_through() -> None:
    seen: dict[str, object] = {}

    def resp(pr_events, ttm_p90_population, now):
        seen["ttm"] = ttm_p90_population
        return 50.0

    def crit(meta, dependents_population, downloads_population):
        seen["dep"] = dependents_population
        seen["dl"] = downloads_population
        return 50.0

    scorers = {
        "responsiveness": resp,
        "criticality": crit,
        "governance": lambda *a, **k: 50.0,
        "rigor": lambda *a, **k: 50.0,
        "accessibility": lambda *a, **k: 50.0,
    }
    ttm = [1.0, 2.0]
    dep = [3.0]
    dl = [4.0, 5.0]
    score_repository(
        make_meta(),
        [],
        NOW,
        ttm_p90_population=ttm,
        dependents_population=dep,
        downloads_population=dl,
        pillar_scorers=scorers,
    )
    assert seen["ttm"] is ttm
    assert seen["dep"] is dep
    assert seen["dl"] is dl


def test_partial_scorer_dict_falls_back_to_defaults() -> None:
    # Only "governance" injected; the other four must run the real defaults.
    def governance(meta):
        return 100.0

    result = score_repository(
        make_meta(), [make_pr()], NOW, pillar_scorers={"governance": governance}
    )
    assert result.pillar_scores is not None
    assert result.pillar_scores.governance == 100.0
    # The real defaults must have produced in-range scores for the rest.
    for name in ("responsiveness", "criticality", "rigor", "accessibility"):
        value = getattr(result.pillar_scores, name)
        assert 0.0 <= value <= 100.0


# --------------------------------------------------------------------------- #
# Real-scorer smoke tests (no injection)
# --------------------------------------------------------------------------- #
def test_real_scorers_smoke_full_pipeline() -> None:
    meta = make_meta(
        stars=1000,
        forks=100,
        monthly_downloads=50_000,
        dependents_count=200,
        elephant_factor=3,
        legal_ip_model=LegalIPModel.FOUNDATION_CLA,
        ai_posture=AIPosture.REGULATED,
        scorecard_score=8.0,
        promotion_ladder=PromotionLadder.DOCUMENTED_PATHWAY,
        mean_inline_comments=3.5,
        active_unclaimed_gfis=8,
        rr_90=0.4,
        dx_ci_minutes=15.0,
    )
    prs = [
        make_pr(pr_number=1, created_days_ago=2.0, latency_hours=4.0),
        make_pr(pr_number=2, created_days_ago=5.0, latency_hours=30.0),
        make_pr(pr_number=3, created_days_ago=1.0, latency_hours=8.0),
    ]
    result = score_repository(meta, prs, NOW)

    assert 0.0 <= result.overall_porchlamp <= 100.0
    assert result.is_stage0_disqualified is False
    assert result.disqualification_reason is None
    assert result.pillar_scores is not None
    for name in PILLAR_NAMES:
        assert 0.0 <= getattr(result.pillar_scores, name) <= 100.0
    assert result.matrix_quadrant in (
        Quadrant.SWEET_SPOT,
        Quadrant.CRUCIBLE,
        Quadrant.NURSERY,
        Quadrant.GRAVEYARD,
    )
    assert 0.0 <= result.leverage_score <= 100.0
    assert 0.0 <= result.friction_score <= 100.0
    assert result.rank_tier is assign_tier(result.overall_porchlamp)


def test_real_scorers_smoke_empty_pr_events() -> None:
    # No PR telemetry at all: every pillar must still return a finite
    # in-range score (neutral fallbacks), no exceptions.
    result = score_repository(make_meta(), [], NOW)
    assert 0.0 <= result.overall_porchlamp <= 100.0
    assert result.pillar_scores is not None
    for name in PILLAR_NAMES:
        assert 0.0 <= getattr(result.pillar_scores, name) <= 100.0


def test_real_scorers_smoke_ai_persona_with_proscribed_posture() -> None:
    # PROSCRIBED posture + AI persona: rigor pillar must be penalized
    # (25 for ai vs 85 for human) — the persona flag must reach the real
    # rigor scorer, not just the injected one.
    meta = make_meta(ai_posture=AIPosture.PROSCRIBED, scorecard_score=10.0,
                     mean_inline_comments=3.5)
    ai_result = score_repository(meta, [], NOW, persona=Persona.AI_ASSISTED)
    human_result = score_repository(meta, [], NOW, persona=Persona.BALANCED)
    assert ai_result.pillar_scores.rigor < human_result.pillar_scores.rigor
    # Exact oracle: pipeline=100, ai_posture=25, review_depth=100
    # -> .40*100 + .35*25 + .25*100 = 73.75
    assert ai_result.pillar_scores.rigor == 73.75
    # human: .40*100 + .35*85 + .25*100 = 94.75
    assert human_result.pillar_scores.rigor == 94.75


def test_real_scorers_smoke_black_hole_repo() -> None:
    # keras-hub-style ground truth: >50% ghost rate -> Black Hole, Tier D,
    # Porchlamp 0.0, even though the repo is otherwise active.
    meta = make_meta(owner="keras-team", name="keras-hub")
    prs = [
        make_pr(pr_number=i, created_days_ago=60.0, merged=False, comments=0,
                is_open=True)
        for i in range(1, 11)
    ]
    result = score_repository(meta, prs, NOW)
    assert result.is_stage0_disqualified is True
    assert result.overall_porchlamp == 0.0
    assert result.rank_tier is Tier.D
    assert result.stage0 is not None and result.stage0.is_black_hole is True


def test_responsiveness_scorer_receives_engine_now_argument() -> None:
    # Gap-closing: pin the `now` argument score_repository forwards to the
    # responsiveness scorer (third positional of the engine's call tuple).
    # The DI seam captures the argument by identity, and a second call with a
    # different `now` must flow through unchanged and differently.
    captured: list[object] = []

    def resp(pr_events, ttm_p90_population, now):
        captured.append(now)
        return 50.0

    scorers = {
        "responsiveness": resp,
        "criticality": lambda *a, **k: 50.0,
        "governance": lambda *a, **k: 50.0,
        "rigor": lambda *a, **k: 50.0,
        "accessibility": lambda *a, **k: 50.0,
    }
    score_repository(make_meta(), [], NOW, pillar_scorers=scorers)
    assert len(captured) == 1
    assert captured[0] is NOW, "responsiveness scorer must receive the engine's now"

    other_now = NOW - timedelta(hours=1)
    score_repository(make_meta(), [], other_now, pillar_scorers=scorers)
    assert len(captured) == 2
    assert captured[1] is other_now, "a different now must flow through unchanged"
    assert captured[1] is not NOW
