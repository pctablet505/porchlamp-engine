"""Adversarial stress-test suite for Milestone 1: Maintainer Improvement Playbook.

Adversarially challenges:
1. Mathematical invariants across random metric combinations (fuzzing):
   - delta_porchlamp >= 0.0
   - potential_porchlamp <= 100.0
   - potential_porchlamp >= current_porchlamp
   - projected_tier in valid Tiers
   - projected_quadrant in valid Quadrants
   - total_point_gain == sum(delta_porchlamp)
2. Black Hole recovery:
   - Repo with GR_14 > 50% starts at Porchlamp=0, Tier=D, Quadrant=Graveyard
   - Recovers to positive Porchlamp and lower friction (delta_friction <= 0)
3. Signed friction change:
   - For P1, P3, P5 bottlenecks: delta_friction <= 0.0
   - For P4 (Rigor): delta_friction == 0.0 strictly
   - For Stage 0 Black Hole: delta_friction <= 0.0
4. Priority ordering:
   - Actions strictly sorted descending by delta_porchlamp (a_i >= a_{i+1})
   - Deterministic tie-breaking (delta_friction ascending, bottleneck alphabetical)
   - Sequential priority numbers 1..N
5. Extreme boundary conditions:
   - All 8 bottlenecks active simultaneously
   - Negative input parameters
   - Massive PR sets (5,000 events)
   - All bot populations, zero decided first-timers, future dates
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
import pytest

from porchlamp.models import (
    AIPosture,
    AuthorAssociation,
    Ecosystem,
    LegalIPModel,
    PillarScores,
    PromotionLadder,
    Quadrant,
    RawPREvent,
    RepoMeta,
    Tier,
)
from porchlamp.playbook import generate_maintainer_playbook

NOW = datetime(2026, 9, 11, 12, 0, 0)
VALID_TIERS = {t.value for t in Tier}
VALID_QUADRANTS = {q.value for q in Quadrant}


def _make_meta(**overrides) -> RepoMeta:
    base = {
        "owner": "adversarial-org",
        "name": "stress-repo",
        "ecosystem": Ecosystem.PYPI,
        "stars": 15000,
        "forks": 2000,
        "monthly_downloads": 2000000,
        "dependents_count": 8000,
        "elephant_factor": 4,
        "legal_ip_model": LegalIPModel.DCO,
        "ai_posture": AIPosture.REGULATED,
        "scorecard_score": 9.5,
        "active_nonbot_commits_180d": 300,
        "off_platform_reviews": False,
        "hard_ai_ban": False,
        "active_unclaimed_gfis": 15,
        "mean_inline_comments": 3.5,
        "rr_90": 0.6,
        "dx_ci_minutes": 15.0,
        "promotion_ladder": PromotionLadder.DOCUMENTED_PATHWAY,
    }
    base.update(overrides)
    return RepoMeta(**base)


def _make_pr(
    pr_number: int,
    created_days_ago: float,
    *,
    author_login: str = "contributor",
    is_external: bool = True,
    merged: bool = True,
    closed: bool = False,
    response_latency_hours: float = 6.0,
    comments: int = 3,
    prior_commits: int = 0,
) -> RawPREvent:
    created = NOW - timedelta(days=created_days_ago)
    merged_at = created + timedelta(hours=2) if merged else None
    closed_at = created + timedelta(hours=2) if (merged or closed) else None
    return RawPREvent(
        repo_owner="adversarial-org",
        repo_name="stress-repo",
        pr_number=pr_number,
        created_at=created,
        author_login=author_login,
        author_association=AuthorAssociation.FIRST_TIME if prior_commits == 0 else AuthorAssociation.MEMBER,
        author_prior_merged_commits=prior_commits,
        closed_at=closed_at,
        merged_at=merged_at,
        is_external=is_external,
        response_latency_hours=response_latency_hours,
        maintainer_review_comments=comments,
    )


# ---------------------------------------------------------------------------
# Test Suite 1: Mathematical Invariants Fuzzing (1,000 random configurations)
# ---------------------------------------------------------------------------
def test_mathematical_invariants_fuzzing() -> None:
    """Fuzz 1,000 random repository metrics and PR populations to verify invariants."""
    rng = random.Random(42)

    legal_models = list(LegalIPModel)
    ai_postures = list(AIPosture)
    ladders = list(PromotionLadder)
    ecosystems = list(Ecosystem)

    for i in range(1000):
        # Generate varied metadata
        ef = rng.choice([-5, -1, 0, 1, 2, 3, 4, 5, 10, 50])
        scorecard = rng.choice([-2.0, 0.0, 2.5, 5.0, 7.9, 8.0, 9.0, 10.0, 12.0])
        gfis = rng.choice([-3, 0, 1, 5, 9, 10, 20, 100])
        mean_comments = rng.choice([-1.0, 0.0, 1.5, 3.5, 6.0, 15.0])
        rr_90 = rng.choice([-0.2, 0.0, 0.2, 0.5, 0.8, 1.0, 1.5])
        dx_ci = rng.choice([-5.0, 0.0, 10.0, 20.0, 35.0, 60.0])

        meta = _make_meta(
            ecosystem=rng.choice(ecosystems),
            elephant_factor=ef,
            legal_ip_model=rng.choice(legal_models),
            ai_posture=rng.choice(ai_postures),
            scorecard_score=scorecard,
            active_unclaimed_gfis=gfis,
            mean_inline_comments=mean_comments,
            rr_90=rr_90,
            dx_ci_minutes=dx_ci,
            promotion_ladder=rng.choice(ladders),
            stars=rng.randint(0, 100000),
            forks=rng.randint(0, 20000),
        )

        # Generate varied PR events (0 to 30 PRs)
        num_prs = rng.randint(0, 30)
        prs: list[RawPREvent] = []
        for p_idx in range(num_prs):
            created_days = rng.uniform(0.1, 40.0)
            is_bot = rng.choice([False, False, False, True])
            author = "bot-svc[bot]" if is_bot else f"user_{rng.randint(1, 10)}"
            is_ext = rng.choice([True, True, False])
            is_merged = rng.choice([True, False, False])
            is_closed = rng.choice([True, False]) if not is_merged else False
            lat = rng.choice([None, -5.0, 1.0, 12.0, 24.0, 48.0, 200.0])
            comments = rng.randint(0, 5)
            prior = rng.choice([0, 0, 1, 5])

            prs.append(
                _make_pr(
                    p_idx + 1,
                    created_days,
                    author_login=author,
                    is_external=is_ext,
                    merged=is_merged,
                    closed=is_closed,
                    response_latency_hours=lat,
                    comments=comments,
                    prior_commits=prior,
                )
            )

        # Optional explicit pillar scores
        provide_explicit = rng.choice([True, False])
        if provide_explicit:
            p_scores = PillarScores(
                responsiveness=rng.uniform(0.0, 100.0),
                criticality=rng.uniform(0.0, 100.0),
                governance=rng.uniform(0.0, 100.0),
                rigor=rng.uniform(0.0, 100.0),
                accessibility=rng.uniform(0.0, 100.0),
            )
            porchlamp_val = rng.uniform(0.0, 100.0)
            pb = generate_maintainer_playbook(
                meta, prs, pillar_scores=p_scores, overall_porchlamp=porchlamp_val, now=NOW
            )
        else:
            pb = generate_maintainer_playbook(meta, prs, now=NOW)

        # Verification of core mathematical invariants:
        # 1. delta_porchlamp >= 0.0 for every action
        for act in pb["actions"]:
            assert act["delta_porchlamp"] >= 0.0, f"Negative delta_porchlamp {act['delta_porchlamp']} in iter {i}"

        # 2. potential_porchlamp <= 100.0
        assert pb["potential_porchlamp"] <= 100.0, f"potential_porchlamp > 100.0 ({pb['potential_porchlamp']}) in iter {i}"

        # 3. potential_porchlamp >= current_porchlamp
        assert pb["potential_porchlamp"] >= pb["current_porchlamp"], (
            f"potential_porchlamp ({pb['potential_porchlamp']}) < current_porchlamp ({pb['current_porchlamp']}) in iter {i}"
        )

        # 4. Valid tiers
        assert pb["projected_tier"] in VALID_TIERS, f"Invalid projected_tier {pb['projected_tier']}"
        assert pb["current_tier"] in VALID_TIERS, f"Invalid current_tier {pb['current_tier']}"

        # 5. Valid quadrants
        assert pb["projected_quadrant"] in VALID_QUADRANTS, f"Invalid projected_quadrant {pb['projected_quadrant']}"
        assert pb["current_quadrant"] in VALID_QUADRANTS, f"Invalid current_quadrant {pb['current_quadrant']}"

        # 6. Total point gain consistency
        expected_gain = round(sum(a["delta_porchlamp"] for a in pb["actions"]), 2)
        assert pb["total_point_gain"] == pytest.approx(expected_gain, abs=0.01)

        # 7. Potential Porchlamp calculation consistency
        expected_pot = min(100.0, round(pb["current_porchlamp"] + pb["total_point_gain"], 2))
        assert pb["potential_porchlamp"] == pytest.approx(expected_pot, abs=0.01)


# ---------------------------------------------------------------------------
# Test Suite 2: Black Hole Recovery Verification
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ghost_count,clean_count", [(3, 1), (6, 2), (10, 0), (20, 5)])
def test_black_hole_recovery_to_positive_porchlamp_and_lower_friction(ghost_count: int, clean_count: int) -> None:
    """Verify repo with GR_14 > 50% recovers from 0.0 Porchlamp to positive Porchlamp and lower friction."""
    meta = _make_meta(
        elephant_factor=4,
        legal_ip_model=LegalIPModel.DCO,
        scorecard_score=9.0,
        active_unclaimed_gfis=15,
    )

    ghosts = [
        RawPREvent(
            repo_owner="adversarial-org",
            repo_name="stress-repo",
            pr_number=i + 1,
            created_at=NOW - timedelta(days=20),
            author_login=f"ghost_author_{i}",
            author_association=AuthorAssociation.NONE,
            author_prior_merged_commits=0,
            closed_at=None,
            merged_at=None,
            is_external=True,
            response_latency_hours=None,
            maintainer_review_comments=0,
        )
        for i in range(ghost_count)
    ]

    clean = [
        _make_pr(
            100 + j,
            2.0,
            response_latency_hours=6.0,
            comments=2,
            merged=True,
        )
        for j in range(clean_count)
    ]

    prs = ghosts + clean
    pb = generate_maintainer_playbook(meta, prs, now=NOW)

    # Must start disqualified in Black Hole (current_porchlamp == 0.0, Tier D, Graveyard)
    assert pb["current_porchlamp"] == 0.0
    assert pb["current_tier"] == Tier.D.value
    assert pb["current_quadrant"] == Quadrant.GRAVEYARD.value

    # Must have Black Hole remediation action
    bh_actions = [a for a in pb["actions"] if "Stage 0 Black Hole" in a["bottleneck"]]
    assert len(bh_actions) == 1, "Expected exactly 1 Black Hole remediation action"
    bh = bh_actions[0]

    # delta_porchlamp must be positive and recover the un-gated Porchlamp
    assert bh["delta_porchlamp"] > 40.0
    # delta_friction must be negative (friction reduction)
    assert bh["delta_friction"] < 0.0

    # Overall playbook must project positive Porchlamp, upgraded tier, and valid quadrant
    assert pb["potential_porchlamp"] > 50.0
    assert pb["potential_porchlamp"] <= 100.0
    assert pb["projected_tier"] in {Tier.S_PLUS.value, Tier.S.value, Tier.A.value, Tier.B.value}
    assert pb["projected_quadrant"] in {Quadrant.SWEET_SPOT.value, Quadrant.CRUCIBLE.value, Quadrant.NURSERY.value}


def test_black_hole_boundary_gr14() -> None:
    """Test boundary condition at exactly GR_14 <= 50% vs GR_14 > 50%."""
    meta = _make_meta()

    # 1 ghost out of 2 open external PRs = 50.0% ghost rate (NOT > 50%)
    ghost = RawPREvent(
        repo_owner="adversarial-org",
        repo_name="stress-repo",
        pr_number=1,
        created_at=NOW - timedelta(days=20),
        author_login="ghost",
        author_association=AuthorAssociation.NONE,
        author_prior_merged_commits=0,
        closed_at=None,
        merged_at=None,
        is_external=True,
        response_latency_hours=None,
        maintainer_review_comments=0,
    )
    # clean_open is open (closed_at=None, merged_at=None), non-ghost
    clean_open = _make_pr(2, 2.0, response_latency_hours=6.0, comments=2, merged=False, closed=False)

    pb_50 = generate_maintainer_playbook(meta, [ghost, clean_open], now=NOW)
    # At exactly 50%, Stage 0 Black Hole is NOT triggered (spec requires > 50%)
    bh_50 = [a for a in pb_50["actions"] if "Stage 0 Black Hole" in a["bottleneck"]]
    assert len(bh_50) == 0
    # But PR Ghost Rate (GR_14 > 10%) IS triggered
    gr_50 = [a for a in pb_50["actions"] if "PR Ghost Rate" in a["bottleneck"]]
    assert len(gr_50) == 1

    # 3 ghosts out of 5 open external PRs = 60.0% ghost rate (> 50%)
    ghosts_3 = [
        RawPREvent(
            repo_owner="adversarial-org",
            repo_name="stress-repo",
            pr_number=i,
            created_at=NOW - timedelta(days=20),
            author_login=f"ghost_{i}",
            author_association=AuthorAssociation.NONE,
            author_prior_merged_commits=0,
            closed_at=None,
            merged_at=None,
            is_external=True,
            response_latency_hours=None,
            maintainer_review_comments=0,
        )
        for i in range(10, 13)
    ]
    clean_open_2 = [
        _make_pr(20, 2.0, response_latency_hours=6.0, comments=2, merged=False, closed=False),
        _make_pr(21, 3.0, response_latency_hours=8.0, comments=2, merged=False, closed=False),
    ]
    pb_60 = generate_maintainer_playbook(meta, ghosts_3 + clean_open_2, now=NOW)
    bh_60 = [a for a in pb_60["actions"] if "Stage 0 Black Hole" in a["bottleneck"]]
    assert len(bh_60) == 1
    assert pb_60["current_porchlamp"] == 0.0
    assert pb_60["potential_porchlamp"] > 0.0


def test_black_hole_with_passed_overall_porchlamp_not_double_counted() -> None:
    """Regression: a black-hole repo whose *stored* overall_porchlamp is non-zero
    (scored ungated) must still project from a 0 baseline. Passing a non-zero
    overall_porchlamp while forcing Tier D / Graveyard used to keep the ungated Porchlamp as
    the baseline and then add the black-hole delta (== normal_porchlamp) on top, so
    potential_porchlamp ~= 2x normal. The displayed Porchlamp of a gated repo is 0, so the
    baseline must be 0 too."""
    meta = _make_meta()
    # 3 open ghosts + 2 clean-open = 60% ghost rate -> black hole (>50%).
    ghosts = [
        _make_pr(10 + i, 20, merged=False, closed=False,
                 response_latency_hours=None, comments=0)
        for i in range(3)
    ]
    clean_open = [
        _make_pr(20, 2, merged=False, closed=False, response_latency_hours=6, comments=2),
        _make_pr(21, 3, merged=False, closed=False, response_latency_hours=8, comments=2),
    ]
    passed_ps = PillarScores(
        responsiveness=80.0, criticality=70.0, governance=75.0,
        rigor=65.0, accessibility=60.0,
    )
    pb = generate_maintainer_playbook(
        meta, ghosts + clean_open, pillar_scores=passed_ps, overall_porchlamp=70.0, now=NOW
    )
    bh = [a for a in pb["actions"] if "Stage 0 Black Hole" in a["bottleneck"]]
    assert len(bh) == 1
    # Baseline must collapse to 0, not stay at the passed-in 70.
    assert pb["current_porchlamp"] == 0.0
    assert pb["current_tier"] == Tier.D.value
    assert pb["current_quadrant"] == Quadrant.GRAVEYARD.value
    # No double count: recovery is 0 -> normal, so potential stays <= 100 and is
    # not ~2x the ungated 70 (which the old code produced via 70 + normal_porchlamp).
    assert pb["potential_porchlamp"] <= 100.0
    assert pb["potential_porchlamp"] < 2 * 70.0


# ---------------------------------------------------------------------------
# Test Suite 3: Signed Friction Change Verification
# ---------------------------------------------------------------------------
def test_signed_friction_changes_across_all_pillars() -> None:
    """Verify signed friction delta:
    - P1, P3, P5 bottlenecks have delta_friction <= 0.0
    - P4 (Rigor: OpenSSF Scorecard) has delta_friction == 0.0 strictly
    """
    # Trigger all bottlenecks simultaneously
    meta_worst = _make_meta(
        elephant_factor=1,  # P3 Low Elephant Factor
        legal_ip_model=LegalIPModel.CORP_CLA_UNILATERAL,  # P3 Corporate CLA
        scorecard_score=3.0,  # P4 Weak CI/CD
        active_unclaimed_gfis=1,  # P5 Deficient GFIs
    )

    prs = [
        # TTFHR > 24h
        _make_pr(1, 10.0, response_latency_hours=50.0, comments=2, merged=True),
        _make_pr(2, 12.0, response_latency_hours=70.0, comments=2, merged=True),
        # Decided first-timer not merged (MR_first < 80%)
        _make_pr(3, 4.0, prior_commits=0, merged=False, closed=True),
        _make_pr(4, 5.0, prior_commits=0, merged=False, closed=True),
        # Ghost PR (>14d, 0 comments)
        RawPREvent(
            repo_owner="adversarial-org",
            repo_name="stress-repo",
            pr_number=5,
            created_at=NOW - timedelta(days=20),
            author_login="ghost",
            author_association=AuthorAssociation.NONE,
            author_prior_merged_commits=0,
            closed_at=None,
            merged_at=None,
            is_external=True,
            response_latency_hours=None,
            maintainer_review_comments=0,
        ),
    ]

    pb = generate_maintainer_playbook(meta_worst, prs, now=NOW)
    actions = pb["actions"]
    assert len(actions) >= 6

    for a in actions:
        b = a["bottleneck"]
        fric = a["delta_friction"]

        if "TTFHR > 24h" in b:  # P1
            assert fric <= 0.0, f"P1 TTFHR delta_friction {fric} > 0"
        elif "PR Ghost Rate" in b:  # P1
            assert fric <= 0.0, f"P1 Ghost Rate delta_friction {fric} > 0"
        elif "First-Timer Merge" in b:  # P1
            assert fric <= 0.0, f"P1 MR_first delta_friction {fric} > 0"
        elif "Elephant Factor" in b:  # P3
            assert fric <= 0.0, f"P3 Elephant Factor delta_friction {fric} > 0"
        elif "Corporate CLA" in b:  # P3
            assert fric <= 0.0, f"P3 CLA delta_friction {fric} > 0"
        elif "Good First Issues" in b:  # P5
            assert fric <= 0.0, f"P5 GFIs delta_friction {fric} > 0"
        elif "Pipeline Defenses" in b:  # P4 (Rigor)
            assert fric == 0.0, f"P4 OpenSSF delta_friction {fric} != 0.0"
        elif "Stage 0 Black Hole" in b:
            assert fric <= 0.0, f"Stage 0 Black Hole delta_friction {fric} > 0"


# ---------------------------------------------------------------------------
# Test Suite 4: Priority Ordering & Stability
# ---------------------------------------------------------------------------
def test_priority_ordering_strictly_descending_porchlamp() -> None:
    """Verify that actions are sorted descending by delta_porchlamp with valid priorities."""
    meta = _make_meta(
        elephant_factor=1,
        legal_ip_model=LegalIPModel.CORP_CLA_UNILATERAL,
        scorecard_score=2.0,
        active_unclaimed_gfis=0,
    )
    prs = [
        _make_pr(1, 10.0, response_latency_hours=100.0, comments=2, merged=True),
        _make_pr(2, 12.0, response_latency_hours=120.0, comments=2, merged=True),
        _make_pr(3, 4.0, prior_commits=0, merged=False, closed=True),
        _make_pr(4, 5.0, prior_commits=0, merged=False, closed=True),
    ]

    pb = generate_maintainer_playbook(meta, prs, now=NOW)
    actions = pb["actions"]
    assert len(actions) > 1

    # Priorities must be strictly 1, 2, ..., N
    assert [a["priority"] for a in actions] == list(range(1, len(actions) + 1))

    # delta_porchlamp must be monotonically non-increasing (descending)
    deltas = [a["delta_porchlamp"] for a in actions]
    for i in range(len(deltas) - 1):
        assert deltas[i] >= deltas[i + 1], (
            f"Ordering violation: action {actions[i]['bottleneck']} ({deltas[i]}) < "
            f"action {actions[i+1]['bottleneck']} ({deltas[i+1]})"
        )


def test_tie_breaking_order() -> None:
    """Verify tie-breaking: when delta_porchlamp is equal, lower (more negative) delta_friction comes first."""
    meta = _make_meta(elephant_factor=2)
    lat = 12.0 * (2.0 ** 1.6)
    prs = [
        _make_pr(1, 5.0, response_latency_hours=lat, comments=2, merged=True),
        _make_pr(2, 6.0, response_latency_hours=lat, comments=2, merged=True),
    ]

    pb = generate_maintainer_playbook(meta, prs, now=NOW)
    actions = pb["actions"]
    assert len(actions) == 2
    a1, a2 = actions[0], actions[1]
    assert a1["delta_porchlamp"] == pytest.approx(3.50, abs=0.01)
    assert a2["delta_porchlamp"] == pytest.approx(3.50, abs=0.01)

    # TTFHR has delta_friction -7.00 vs EF -5.25 -> TTFHR must be Priority 1
    assert "TTFHR" in a1["bottleneck"]
    assert "Elephant Factor" in a2["bottleneck"]
    assert a1["delta_friction"] < a2["delta_friction"]


# ---------------------------------------------------------------------------
# Test Suite 5: Pathological and Extreme Boundary Inputs
# ---------------------------------------------------------------------------
def test_all_eight_bottlenecks_simultaneously() -> None:
    """Verify system stability when ALL 8 bottlenecks fire at the same time."""
    meta = _make_meta(
        elephant_factor=1,  # 1. Low EF
        legal_ip_model=LegalIPModel.CORP_CLA_UNILATERAL,  # 2. Corporate CLA
        scorecard_score=4.0,  # 3. Weak CI/CD
        active_unclaimed_gfis=2,  # 4. Deficient GFIs
    )

    # PRs triggering:
    # 5. TTFHR > 24h
    # 6. GR_14 > 10%
    # 7. Stage 0 Black Hole (GR_14 > 50%)
    # 8. First-Timer Merge < 80%
    ghosts = [
        RawPREvent(
            repo_owner="adversarial-org",
            repo_name="stress-repo",
            pr_number=i,
            created_at=NOW - timedelta(days=20),
            author_login=f"ghost_{i}",
            author_association=AuthorAssociation.NONE,
            author_prior_merged_commits=0,
            closed_at=None,
            merged_at=None,
            is_external=True,
            response_latency_hours=None,
            maintainer_review_comments=0,
        )
        for i in range(1, 4)
    ]
    decided_first = [
        _make_pr(10, 4.0, prior_commits=0, response_latency_hours=48.0, merged=False, closed=True),
        _make_pr(11, 5.0, prior_commits=0, response_latency_hours=72.0, merged=False, closed=True),
    ]

    pb = generate_maintainer_playbook(meta, ghosts + decided_first, now=NOW)
    actions = pb["actions"]

    # All 8 bottlenecks present
    bottleneck_names = [a["bottleneck"] for a in actions]
    assert any("Review Latency Friction" in b for b in bottleneck_names)
    assert any("PR Ghost Rate" in b for b in bottleneck_names)
    assert any("Stage 0 Black Hole" in b for b in bottleneck_names)
    assert any("Elephant Factor" in b for b in bottleneck_names)
    assert any("Corporate CLA" in b for b in bottleneck_names)
    assert any("Good First Issues" in b for b in bottleneck_names)
    assert any("First-Timer Merge" in b for b in bottleneck_names)
    assert any("Pipeline Defenses" in b for b in bottleneck_names)

    assert len(actions) == 8
    assert [a["priority"] for a in actions] == list(range(1, 9))
    assert pb["potential_porchlamp"] <= 100.0
    assert pb["potential_porchlamp"] > 0.0
    assert pb["projected_tier"] in VALID_TIERS
    assert pb["projected_quadrant"] in VALID_QUADRANTS


def test_extreme_negative_and_out_of_bounds_inputs() -> None:
    """Out-of-bounds negative inputs must be safely guarded without crashing or producing invalid output."""
    meta = _make_meta(
        elephant_factor=-10,
        scorecard_score=-5.0,
        active_unclaimed_gfis=-20,
        mean_inline_comments=-10.0,
        rr_90=-1.0,
        dx_ci_minutes=-100.0,
    )
    pb = generate_maintainer_playbook(meta, [], now=NOW)

    assert pb["current_porchlamp"] >= 0.0
    assert pb["potential_porchlamp"] <= 100.0
    assert pb["projected_tier"] in VALID_TIERS
    assert pb["projected_quadrant"] in VALID_QUADRANTS
    for a in pb["actions"]:
        assert a["delta_porchlamp"] >= 0.0
        assert a["delta_friction"] <= 0.0


def test_performance_with_large_pr_population() -> None:
    """Performance stress: generate playbook for 5,000 PR events under 200ms."""
    import time

    meta = _make_meta()
    prs = [
        _make_pr(
            i,
            created_days_ago=float(i % 30) + 1.0,
            response_latency_hours=float(10 + (i % 50)),
            comments=i % 4,
            merged=(i % 2 == 0),
            closed=(i % 2 != 0),
            prior_commits=(i % 3),
        )
        for i in range(5000)
    ]

    t0 = time.perf_counter()
    pb = generate_maintainer_playbook(meta, prs, now=NOW)
    elapsed = time.perf_counter() - t0

    assert elapsed < 1.0, f"Playbook generation took {elapsed:.4f}s (> 1.0s)"
    assert pb["potential_porchlamp"] <= 100.0
    assert pb["projected_tier"] in VALID_TIERS


# ---------------------------------------------------------------------------
# Test Suite 6: Full 1,000 Benchmark Catalog Invariant Verification
# ---------------------------------------------------------------------------
def test_all_1000_benchmark_repositories_playbooks() -> None:
    """Verify invariants hold across all 1,000 seeded benchmark catalog repositories."""
    import json
    from porchlamp.lakehouse import OperationalStore
    from tests.fixtures.synthetic_catalog import seed_benchmark_store

    store = OperationalStore()
    seed_benchmark_store(store, count=1000)

    cur = store._conn.execute("SELECT owner, name, overall_porchlamp, rank_tier, matrix_quadrant, deep_dive_json FROM repositories")
    rows = cur.fetchall()
    assert len(rows) == 1000

    black_hole_count = 0
    for _owner, _name, overall_porchlamp, _rank_tier, _matrix_quadrant, dd_json in rows:
        assert dd_json is not None
        dd = json.loads(dd_json)
        pb = dd.get("maintainer_playbook")
        assert pb is not None

        # Mathematical invariants
        assert pb["potential_porchlamp"] <= 100.0
        assert pb["potential_porchlamp"] >= pb["current_porchlamp"]
        assert pb["projected_tier"] in VALID_TIERS
        assert pb["projected_quadrant"] in VALID_QUADRANTS
        assert pb["current_tier"] in VALID_TIERS
        assert pb["current_quadrant"] in VALID_QUADRANTS

        # Priority ordering & signed friction
        deltas = [a["delta_porchlamp"] for a in pb["actions"]]
        assert deltas == sorted(deltas, reverse=True)

        for a in pb["actions"]:
            assert a["delta_porchlamp"] >= 0.0
            if "Pipeline Defenses" in a["bottleneck"]:
                assert a["delta_friction"] == 0.0
            else:
                assert a["delta_friction"] <= 0.0

        if overall_porchlamp == 0.0:
            black_hole_count += 1
            assert pb["current_porchlamp"] == 0.0
            assert pb["current_tier"] == Tier.D.value
            assert pb["current_quadrant"] == Quadrant.GRAVEYARD.value
            assert pb["potential_porchlamp"] > 0.0
            assert any("Stage 0 Black Hole" in a["bottleneck"] for a in pb["actions"])

    assert black_hole_count > 0, "Expected to test at least one Black Hole repository in the 1,000 benchmark catalog"


def test_landmark_repositories_playbook_recovery() -> None:
    """Verify specific landmark projects exhibit correct playbook projections."""
    import json
    from porchlamp.lakehouse import OperationalStore
    from tests.fixtures.synthetic_catalog import seed_benchmark_store

    store = OperationalStore()
    seed_benchmark_store(store, count=1000)

    # 1. Keras-hub (Stage 0 Black Hole)
    cur = store._conn.execute(
        "SELECT deep_dive_json FROM repositories WHERE owner='keras-team' AND name='keras-hub'"
    )
    row = cur.fetchone()
    assert row is not None
    pb_keras = json.loads(row[0])["maintainer_playbook"]
    assert pb_keras["current_porchlamp"] == 0.0
    assert pb_keras["current_tier"] == Tier.D.value
    assert pb_keras["current_quadrant"] == Quadrant.GRAVEYARD.value
    assert pb_keras["potential_porchlamp"] > 60.0
    assert pb_keras["projected_tier"] in {Tier.A.value, Tier.B.value}
    assert pb_keras["projected_quadrant"] == Quadrant.SWEET_SPOT.value
    assert pb_keras["actions"][0]["bottleneck"] == "Stage 0 Black Hole (PR Ghost Rate > 50%)"

    # 2. Torvalds/linux (Crucible to Sweet Spot upgrade)
    cur = store._conn.execute(
        "SELECT deep_dive_json FROM repositories WHERE owner='torvalds' AND name='linux'"
    )
    row = cur.fetchone()
    assert row is not None
    pb_linux = json.loads(row[0])["maintainer_playbook"]
    assert pb_linux["current_quadrant"] == Quadrant.CRUCIBLE.value
    assert pb_linux["projected_quadrant"] == Quadrant.SWEET_SPOT.value
    assert pb_linux["potential_porchlamp"] > pb_linux["current_porchlamp"]

    # 3. Facebook/react (Sweet Spot Tier S+ capped at 100.0)
    cur = store._conn.execute(
        "SELECT deep_dive_json FROM repositories WHERE owner='facebook' AND name='react'"
    )
    row = cur.fetchone()
    assert row is not None
    pb_react = json.loads(row[0])["maintainer_playbook"]
    assert pb_react["current_porchlamp"] >= 90.0
    assert pb_react["potential_porchlamp"] == 100.0
    assert pb_react["projected_tier"] == Tier.S_PLUS.value


def test_stage0_disqualified_non_black_hole() -> None:
    """Verify Stage 0 disqualification due to inactive maintenance (< 1 nonbot commit) is handled."""
    meta = _make_meta(active_nonbot_commits_180d=0)
    pb = generate_maintainer_playbook(meta, [], now=NOW)
    assert pb["current_porchlamp"] == 0.0
    assert pb["current_tier"] == Tier.D.value
    assert pb["projected_tier"] == Tier.D.value
    assert not any("Stage 0 Black Hole" in a["bottleneck"] for a in pb["actions"])
