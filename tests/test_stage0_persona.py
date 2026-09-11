"""Persona-specific Stage 0 constraint gates (spec 4) tests.

Pins the senior_systems Multi-Vendor gate (gate 4b, ``elephant_factor >= 3``)
in ``cvi.stage0.apply_stage0`` and its wiring through
``cvi.engine.score_repository``. The remaining spec-4 persona gates
(first_timer "Low-Barrier Only", career_capital "High Tier Brand Only") are
intentionally unimplemented pending owner definition, so no test pins their
semantics.

Gate order under test: 1 (maintenance) -> 2 (ghost rate) -> 4 (AI ban) ->
4b (senior_systems multi-vendor) -> 3 (off-platform routing). The first hard
failure wins; off-platform is routing, not a failure.
"""

from __future__ import annotations

from datetime import datetime

from cvi.engine import PILLAR_NAMES, score_repository
from cvi.models import Ecosystem, Persona, RepoMeta, Stage0Result, Tier
from cvi.stage0 import apply_stage0

NOW = datetime(2026, 9, 11, 12, 0, 0)

SENIOR_GATE_REASON = "senior_systems multi-vendor gate: elephant factor (EF) must be >= 3"


def _meta(**kw) -> RepoMeta:
    base = dict(
        owner="acme",
        name="widget",
        ecosystem=Ecosystem.PYPI,
        active_nonbot_commits_180d=3,
        elephant_factor=1,
        off_platform_reviews=False,
        hard_ai_ban=False,
    )
    base.update(kw)
    return RepoMeta(**base)


def _assert_full(result: Stage0Result, **expected) -> None:
    """Assert every Stage0Result field equals the hand-computed expectation."""
    full = dict(
        passed=expected.get("passed", True),
        reason=expected.get("reason", None),
        is_black_hole=expected.get("is_black_hole", False),
        is_off_platform=expected.get("is_off_platform", False),
        is_ai_ban_disqualified=expected.get("is_ai_ban_disqualified", False),
    )
    assert result.passed == full["passed"], f"passed: {result.passed!r} != {full['passed']!r}"
    assert result.reason == full["reason"], f"reason: {result.reason!r} != {full['reason']!r}"
    assert result.is_black_hole == full["is_black_hole"], f"is_black_hole: {result.is_black_hole!r} != {full['is_black_hole']!r}"
    assert result.is_off_platform == full["is_off_platform"], f"is_off_platform: {result.is_off_platform!r} != {full['is_off_platform']!r}"
    assert result.is_ai_ban_disqualified == full["is_ai_ban_disqualified"], f"is_ai_ban_disqualified: {result.is_ai_ban_disqualified!r} != {full['is_ai_ban_disqualified']!r}"


# --------------------------------------------------------------------------- #
# Gate 4b: senior_systems Multi-Vendor constraint (EF >= 3)
# --------------------------------------------------------------------------- #
def test_gate4b_senior_systems_ef_2_disqualifies():
    r = apply_stage0(_meta(elephant_factor=2), [], NOW, persona=Persona.SENIOR_SYSTEMS)
    _assert_full(r, passed=False, reason=SENIOR_GATE_REASON)


def test_gate4b_senior_systems_ef_3_passes():
    # EF exactly 3 satisfies the EF >= 3 boundary on otherwise clean inputs.
    r = apply_stage0(_meta(elephant_factor=3), [], NOW, persona=Persona.SENIOR_SYSTEMS)
    _assert_full(r)


def test_gate4b_persona_none_preserves_prior_behavior():
    # persona=None (default): the constraint is inert even at EF=1, and the
    # AI-ban gate behaves exactly as before.
    _assert_full(apply_stage0(_meta(elephant_factor=1), [], NOW))
    _assert_full(
        apply_stage0(_meta(hard_ai_ban=True, elephant_factor=1), [], NOW, ai_persona=False)
    )


def test_gate4b_other_personas_unaffected():
    # Only senior_systems enforces a Stage 0 persona constraint (spec 4).
    for persona in (
        Persona.BALANCED,
        Persona.FIRST_TIMER,
        Persona.CAREER_CAPITAL,
        Persona.AI_ASSISTED,
    ):
        _assert_full(apply_stage0(_meta(elephant_factor=1), [], NOW, persona=persona))


# --------------------------------------------------------------------------- #
# Gate ordering around 4b
# --------------------------------------------------------------------------- #
def test_ai_ban_gate_precedes_senior_gate():
    # Gate 4 (AI ban) fires before gate 4b: the AI-ban reason wins.
    r = apply_stage0(
        _meta(hard_ai_ban=True, elephant_factor=1),
        [],
        NOW,
        ai_persona=True,
        persona=Persona.SENIOR_SYSTEMS,
    )
    _assert_full(
        r,
        passed=False,
        reason="hard AI ban disqualifies AI-assisted persona",
        is_ai_ban_disqualified=True,
    )


def test_senior_gate_independent_of_ai_ban_gate():
    # senior_systems + hard_ai_ban with a non-AI view (ai_persona=False):
    # gate 4 does not fire; gate 4b still disqualifies on its own.
    r = apply_stage0(
        _meta(hard_ai_ban=True, elephant_factor=1),
        [],
        NOW,
        ai_persona=False,
        persona=Persona.SENIOR_SYSTEMS,
    )
    _assert_full(r, passed=False, reason=SENIOR_GATE_REASON)


def test_senior_gate_beats_off_platform_routing():
    # Gate 4b is a hard failure and precedes gate 3: no routing flag is set
    # on the disqualified result.
    r = apply_stage0(
        _meta(off_platform_reviews=True, elephant_factor=1),
        [],
        NOW,
        persona=Persona.SENIOR_SYSTEMS,
    )
    _assert_full(r, passed=False, reason=SENIOR_GATE_REASON)


# --------------------------------------------------------------------------- #
# Engine wiring: score_repository passes persona through to apply_stage0
# --------------------------------------------------------------------------- #
def test_engine_senior_systems_low_ef_disqualified():
    result = score_repository(
        _meta(elephant_factor=1), [], NOW, persona=Persona.SENIOR_SYSTEMS
    )
    assert result.is_stage0_disqualified is True
    assert result.overall_cvi == 0.0
    assert result.rank_tier is Tier.D
    assert result.stage0.passed is False
    assert result.disqualification_reason is not None
    assert "EF" in result.disqualification_reason


def test_engine_senior_systems_multi_vendor_not_disqualified():
    scorers = {name: (lambda *args, **kwargs: 50.0) for name in PILLAR_NAMES}
    result = score_repository(
        _meta(elephant_factor=3),
        [],
        NOW,
        persona=Persona.SENIOR_SYSTEMS,
        pillar_scorers=scorers,
    )
    assert result.is_stage0_disqualified is False
    assert result.disqualification_reason is None
    assert result.stage0.passed is True
    assert result.pillar_scores is not None
    # All pillars 50.0 -> composite 50.0 for any persona -> Tier C.
    assert result.overall_cvi == 50.0
    assert result.rank_tier is Tier.C
