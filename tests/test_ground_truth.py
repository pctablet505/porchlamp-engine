"""End-to-end ground-truth test matrix (spec 14, docs/ARCHITECTURE.md ~L719-733).

Four ground-truth outcomes pinned through the full ``cvi.engine.score_repository``
pipeline, with the five pillars fixed exactly via the ``pillar_scorers`` DI
seam so each test is a pure function of pinned values plus the Phase-0 gate:

* ``test_ground_truth_vllm``   - vllm-project/vllm  -> Sweet Spot, CVI = 88.8
* ``test_ground_truth_hub``    - keras-team/keras-hub -> Black Hole (gated),
  plus the spec's pre-gate composite of 46.8 in the Graveyard
* ``test_ground_truth_linux``  - torvalds/linux     -> Stage-0 off-platform
  routing (pass, not disqualified)
* ``test_ground_truth_gentoo`` - gentoo/gentoo      -> AI-persona DQ; Tier A
  under the traditional (BALANCED) persona

Pillar values are the solutions to the documented linear system:

* BALANCED weights: resp .25 / crit .25 / gov .20 / rigor .15 / acc .15
* leverage = 0.5*P2 + 0.3*P4 + 0.2*P3
* friction = 0.5*(100-P1) + 0.3*(100-P3) + 0.2*(100-P5)
* quadrant: coordinate >= 50.0 = "high" on BOTH axes (leverage = Y,
  friction = X); high-lev/low-fric = Sweet Spot, low/low = Nursery,
  high/high = Crucible, low-lev/high-fric = Graveyard
* tiers: >= 90 S+, >= 80 S, >= 70 A, >= 55 B, >= 40 C, else D
"""

from __future__ import annotations

from datetime import datetime, timedelta

from cvi.engine import score_repository
from cvi.models import (
    AuthorAssociation,
    Ecosystem,
    Persona,
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
    owner: str,
    name: str,
    pr_number: int,
    created_days_ago: float,
    *,
    external: bool = True,
    merged: bool = False,
    is_open: bool = True,
    comments: int = 0,
) -> RawPREvent:
    """Build one PR event.

    A *ghost* (for the GR_14 gate) is an open external PR created more than
    14 days before ``NOW`` with zero maintainer review comments. Use
    ``is_open=True`` with a recent ``created_days_ago`` or a positive
    ``comments`` to build a clean (non-ghost) PR.
    """
    created = NOW - timedelta(days=created_days_ago)
    if merged:
        merged_at = created + timedelta(hours=1)
        closed_at = merged_at
    elif is_open:
        merged_at = None
        closed_at = None
    else:
        merged_at = None
        closed_at = created + timedelta(hours=1)
    return RawPREvent(
        repo_owner=owner,
        repo_name=name,
        pr_number=pr_number,
        created_at=created,
        author_login="external-contrib",
        author_association=AuthorAssociation.FIRST_TIME,
        author_prior_merged_commits=0,
        closed_at=closed_at,
        merged_at=merged_at,
        is_external=external,
        maintainer_review_comments=comments,
    )


def pinned_scorers(
    resp: float, crit: float, gov: float, rigor: float, acc: float
) -> dict:
    """Pillar scorers that return the pinned values exactly (DI seam)."""

    def constant(value: float):
        def scorer(*args, **kwargs) -> float:
            return value

        return scorer

    return {
        "responsiveness": constant(resp),
        "criticality": constant(crit),
        "governance": constant(gov),
        "rigor": constant(rigor),
        "accessibility": constant(acc),
    }


# --------------------------------------------------------------------------- #
# 1. vllm-project/vllm -> The Sweet Spot (CVI = 88.8)
# --------------------------------------------------------------------------- #
# Pinned pillars (resp, crit, gov, rigor, acc): 92.0, 88.0, 90.0, 86.0, 86.0
#   BALANCED composite = .25*92 + .25*88 + .20*90 + .15*86 + .15*86 = 88.8
#   leverage = .5*88 + .3*86 + .2*90 = 87.8       (>= 50 -> high)
#   friction = .5*8 + .3*10 + .2*14 = 9.8         (< 50 -> low)
#   high-leverage / low-friction -> SWEET_SPOT.
def test_ground_truth_vllm() -> None:
    meta = make_meta(
        owner="vllm-project",
        name="vllm",
        ecosystem=Ecosystem.PYPI,
        active_nonbot_commits_180d=1200,
    )
    # One recent, commented external PR => GR_14 = 0 (no ghost PRs).
    prs = [make_pr("vllm-project", "vllm", 1, created_days_ago=3.0, comments=3)]

    result = score_repository(
        meta,
        prs,
        NOW,
        persona=Persona.BALANCED,
        pillar_scorers=pinned_scorers(92.0, 88.0, 90.0, 86.0, 86.0),
    )

    assert result.is_stage0_disqualified is False
    assert result.overall_cvi == 88.8
    assert result.leverage_score == 87.8
    assert result.friction_score == 9.8
    assert result.matrix_quadrant is Quadrant.SWEET_SPOT
    # Spec 14 labels this "Tier S+", but 88.8 < 90.0, so the documented tier
    # boundary (>= 90.0 -> S+) places it in Tier S. Assert the engine's
    # documented result; the S+ label is a spec inconsistency.
    assert result.rank_tier is Tier.S


# --------------------------------------------------------------------------- #
# 2. keras-team/keras-hub -> Black Hole (gated) AND pre-gate 46.8 / Graveyard
# --------------------------------------------------------------------------- #
def test_ground_truth_hub() -> None:
    meta = make_meta(
        owner="keras-team",
        name="keras-hub",
        ecosystem=Ecosystem.PYPI,
        active_nonbot_commits_180d=10,
    )

    # --- (a) Gated path -----------------------------------------------------
    # 3 ghost + 1 clean open external PRs => GR_14 = 3/4 = 0.75 > 0.50,
    # so the Phase-0 Responsiveness Ceiling gate trips the Black Hole.
    ghost_prs = [
        make_pr("keras-team", "keras-hub", n, created_days_ago=30.0, comments=0)
        for n in (1, 2, 3)
    ]
    clean_pr = make_pr(
        "keras-team", "keras-hub", 4, created_days_ago=2.0, comments=2
    )
    gated = score_repository(
        meta, ghost_prs + [clean_pr], NOW, persona=Persona.BALANCED
    )

    assert gated.is_stage0_disqualified is True
    assert gated.stage0.passed is False
    assert gated.stage0.is_black_hole is True
    assert gated.overall_cvi == 0.0
    assert gated.rank_tier is Tier.D
    assert gated.matrix_quadrant is Quadrant.GRAVEYARD
    # A hard gate failure short-circuits before any pillar scoring.
    assert gated.pillar_scores is None

    # --- (b) Pre-gate path --------------------------------------------------
    # With ghost-clean telemetry, the same repo's pre-gate composite is the
    # spec's documented 46.8. Pinned pillars (resp, crit, gov, rigor, acc):
    #   45.0, 56.2, 40.0, 45.0, 45.0
    #   BALANCED composite = .25*45 + .25*56.2 + .20*40 + .15*45 + .15*45
    #                        = 46.8
    #   leverage = .5*56.2 + .3*45 + .2*40 = 49.6  (< 50 -> low)
    #   friction = .5*55 + .3*60 + .2*55 = 56.5    (>= 50 -> high)
    #   low-leverage / high-friction -> GRAVEYARD.
    pre_gate = score_repository(
        meta,
        [make_pr("keras-team", "keras-hub", 1, created_days_ago=2.0, comments=2)],
        NOW,
        persona=Persona.BALANCED,
        pillar_scorers=pinned_scorers(45.0, 56.2, 40.0, 45.0, 45.0),
    )

    assert pre_gate.is_stage0_disqualified is False
    # 46.8 is the pre-gate composite per spec 14 (what the matrix shows
    # before the Black-Hole gate would zero it on real telemetry).
    assert pre_gate.overall_cvi == 46.8
    assert pre_gate.leverage_score == 49.6
    assert pre_gate.friction_score == 56.5
    assert pre_gate.matrix_quadrant is Quadrant.GRAVEYARD
    # NOTE: the task brief names this "Tier B", but under the documented tier
    # boundary (>= 55.0 -> B, >= 40.0 -> C) a 46.8 composite lands in Tier C,
    # which is exactly what the engine returns. Asserting the engine result.
    assert pre_gate.rank_tier is Tier.C


# --------------------------------------------------------------------------- #
# 3. torvalds/linux -> Stage-0 off-platform routing (NOT disqualified)
# --------------------------------------------------------------------------- #
def test_ground_truth_linux() -> None:
    meta = make_meta(
        owner="torvalds",
        name="linux",
        ecosystem=Ecosystem.SYSTEMS,
        active_nonbot_commits_180d=5000,
        off_platform_reviews=True,
    )
    # LKML-style workflow: no in-platform PR telemetry, but the repo is active.
    prs: list[RawPREvent] = []

    result = score_repository(
        meta,
        prs,
        NOW,
        persona=Persona.BALANCED,
        # Arbitrary pinned pillars: the assertions target the Stage-0 routing
        # flag, not the composite; pinning keeps the result deterministic.
        pillar_scorers=pinned_scorers(50.0, 50.0, 50.0, 50.0, 50.0),
    )

    # Off-platform reviews (gate 3) are routing, not a hard failure: the repo
    # passes Stage 0 and is scored normally.
    assert result.is_stage0_disqualified is False
    assert result.disqualification_reason is None
    assert result.stage0.passed is True
    assert result.stage0.is_off_platform is True
    assert result.pillar_scores is not None


# --------------------------------------------------------------------------- #
# 4. gentoo/gentoo -> AI-persona DQ; Tier A under traditional (BALANCED)
# --------------------------------------------------------------------------- #
def test_ground_truth_gentoo() -> None:
    meta = make_meta(
        owner="gentoo",
        name="gentoo",
        ecosystem=Ecosystem.SYSTEMS,
        active_nonbot_commits_180d=500,
        hard_ai_ban=True,
    )
    prs: list[RawPREvent] = []

    # --- AI-assisted persona: hard AI ban disqualifies (gate 4) -------------
    ai = score_repository(meta, prs, NOW, persona=Persona.AI_ASSISTED)
    assert ai.is_stage0_disqualified is True
    assert ai.stage0.passed is False
    assert ai.stage0.is_ai_ban_disqualified is True
    assert ai.overall_cvi == 0.0
    assert ai.rank_tier is Tier.D
    assert ai.matrix_quadrant is Quadrant.GRAVEYARD
    assert ai.pillar_scores is None

    # --- Traditional (BALANCED) persona: ban does not apply, Tier A ---------
    # Pinned pillars (resp, crit, gov, rigor, acc): 80.0, 80.0, 70.0, 80.0, 60.0
    #   BALANCED composite = .25*80 + .25*80 + .20*70 + .15*80 + .15*60 = 75.0
    #   75.0 in [70.0, 80.0) -> Tier A.
    balanced = score_repository(
        meta,
        prs,
        NOW,
        persona=Persona.BALANCED,
        pillar_scorers=pinned_scorers(80.0, 80.0, 70.0, 80.0, 60.0),
    )
    assert balanced.is_stage0_disqualified is False
    assert balanced.stage0.is_ai_ban_disqualified is False
    assert balanced.overall_cvi == 75.0
    assert 70.0 <= balanced.overall_cvi < 80.0
    assert balanced.rank_tier is Tier.A
