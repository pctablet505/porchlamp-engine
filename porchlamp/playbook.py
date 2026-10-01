"""Maintainer Improvement Playbook engine (spec 12.2, ORIGINAL_REQUEST.md R2).

Automated diagnostic gap analysis detecting 8 friction bottlenecks and computing
exact canonical WAM point gains, friction reductions, and tier/quadrant upgrades:
1. Review Latency Friction (TTFHR > 24h)
2. PR Ghost Rate (GR_14 > 10%)
3. Stage 0 Black Hole (GR_14 > 50% responsiveness ceiling)
4. Low Elephant Factor (EF <= 2)
5. Corporate CLA Relicensing Hazard (Unilateral / Reciprocal)
6. Deficient Good First Issues (active unclaimed GFIs < 10)
7. First-Timer Merge Friction (MR_first < 80%)
8. Weak CI/CD Pipeline Defenses (OpenSSF Scorecard < 8.0)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from .constants import (
    BOT_LOGIN_MARKERS,
    PILLAR_WEIGHTS,
)
from .engine import assign_tier, score_repository
from .matrix import decompose
from .models import (
    LegalIPModel,
    Persona,
    PillarScores,
    Quadrant,
    RawPREvent,
    RepoMeta,
    Tier,
)
from .persona import persona_composite
from .pillars.accessibility import score_accessibility, score_gfi
from .pillars.criticality import score_criticality
from .pillars.governance import score_decentralization, score_governance
from .pillars.responsiveness import (
    score_ghost_rate,
    score_mr_first,
    score_responsiveness,
    score_ttfhr,
)
from .pillars.rigor import score_pipeline, score_rigor

from .stage0 import _ghost_rate_14


def _is_bot(login: str) -> bool:
    """True if login matches any bot marker."""
    lowered = login.lower()
    return any(marker.lower() in lowered for marker in BOT_LOGIN_MARKERS)


def _median(values: list[float]) -> float:
    """Standard median of list of floats."""
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0


def _compute_normal_pillars(
    meta: RepoMeta,
    pr_events: list[RawPREvent],
    now: datetime,
) -> PillarScores:
    """Compute un-gated canonical 5-pillar scores."""
    p1 = score_responsiveness(pr_events, [], now)
    p2 = score_criticality(meta, [], [])
    p3 = score_governance(meta)
    p4 = score_rigor(meta, persona_is_ai=False)
    p5 = score_accessibility(meta)
    return PillarScores(
        responsiveness=p1,
        criticality=p2,
        governance=p3,
        rigor=p4,
        accessibility=p5,
    )


def generate_maintainer_playbook(
    meta: RepoMeta,
    pr_events: list[RawPREvent],
    now: datetime,
    pillar_scores: Optional[PillarScores] = None,
    overall_porchlamp: Optional[float] = None,
) -> dict[str, Any]:
    """Diagnose maintainer friction bottlenecks and compute actionable upgrade ROI.

    Detects the 8 canonical friction bottlenecks, ranks remediation actions descending
    by Porchlamp point gain, and projects potential Porchlamp score, tier, and quadrant upgrades.

    ``now`` is the snapshot's ``as_of`` (D-VER-3): every age, window and
    right-censoring bound is measured against it, so it is required and this
    module reads no clock of its own.
    """
    # Evaluate Stage 0 gating status and un-gated normal pillars
    normal_pillars = _compute_normal_pillars(meta, pr_events, now)
    normal_porchlamp = persona_composite(normal_pillars, Persona.BALANCED)
    normal_lev, normal_fric, normal_quad = decompose(normal_pillars)

    # Determine baseline state
    is_black_hole = _ghost_rate_14(pr_events, now) > 0.50
    external_prs = [p for p in pr_events if p.is_external]
    gr_14 = 0.0
    if external_prs:
        window = timedelta(days=14)
        ghosts = sum(
            1
            for p in external_prs
            if p.closed_at is None
            and (now - p.created_at) > window
            and p.maintainer_review_comments == 0
        )
        gr_14 = ghosts / len(external_prs)

    if pillar_scores is not None and overall_porchlamp is not None:
        current_porchlamp = overall_porchlamp
        if is_black_hole or current_porchlamp == 0.0:
            # A gated/black-hole repo's *displayed* Porchlamp is 0 (Stage 0 zeroes
            # it), matching the forced Tier D / Graveyard below. The black-hole
            # action's delta (normal_porchlamp) represents the 0 -> normal recovery,
            # so the projection baseline must be 0 too; keeping the passed-in
            # (ungated) overall_porchlamp here would double-count it into ~2x normal.
            current_porchlamp = 0.0
            current_tier = Tier.D.value
            current_quadrant = Quadrant.GRAVEYARD.value
            base_pillars = normal_pillars if is_black_hole else PillarScores(0.0, 0.0, 0.0, 0.0, 0.0)
        else:
            current_tier = assign_tier(current_porchlamp).value
            _, _, quad = decompose(pillar_scores)
            current_quadrant = quad.value
            base_pillars = pillar_scores
    else:
        scored = score_repository(meta, pr_events, now, persona=Persona.BALANCED)
        current_porchlamp = scored.overall_porchlamp
        current_tier = scored.rank_tier.value
        current_quadrant = scored.matrix_quadrant.value
        if scored.is_stage0_disqualified:
            if scored.stage0.is_black_hole:
                is_black_hole = True
            base_pillars = normal_pillars if is_black_hole else PillarScores(0.0, 0.0, 0.0, 0.0, 0.0)
        else:
            base_pillars = scored.pillar_scores if scored.pillar_scores is not None else normal_pillars

    actions: list[dict[str, Any]] = []

    # 1. Review Latency Friction (TTFHR > 24h)
    latencies = [
        p.response_latency_hours
        for p in external_prs
        if p.response_latency_hours is not None
        and not _is_bot(p.author_login)
    ]
    if latencies:
        med_ttfhr = _median(latencies)
        if med_ttfhr > 24.0:
            p_curr = score_ttfhr(pr_events)
            delta_p = 100.0 - p_curr
            delta_porchlamp = 0.25 * 0.35 * delta_p
            delta_fric = -0.50 * 0.35 * delta_p
            actions.append({
                "bottleneck": "Review Latency Friction (TTFHR > 24h)",
                "recommendation": (
                    "Accelerate initial maintainer review turnaround to under 12 hours "
                    "(target TTFHR <= 12h) to reduce contributor drop-off."
                ),
                "delta_param_score": round(delta_p, 2),
                "delta_porchlamp": round(delta_porchlamp, 2),
                "delta_friction": round(delta_fric, 2),
                "pillar": 1,
                "delta_P": 0.35 * delta_p,
            })

    # 2. PR Ghost Rate (GR_14 > 10%)
    if external_prs and gr_14 > 0.10:
        p_curr = score_ghost_rate(pr_events, now)
        delta_p = 100.0 - p_curr
        delta_porchlamp = 0.25 * 0.20 * delta_p
        delta_fric = -0.50 * 0.20 * delta_p
        actions.append({
            "bottleneck": "PR Ghost Rate (GR_14 > 10%)",
            "recommendation": (
                "Triage and respond to external PRs older than 14 days to eliminate "
                "unreviewed ghost backlog."
            ),
            "delta_param_score": round(delta_p, 2),
            "delta_porchlamp": round(delta_porchlamp, 2),
            "delta_friction": round(delta_fric, 2),
            "pillar": 1,
            "delta_P": 0.20 * delta_p,
        })

    # 3. Stage 0 Black Hole (GR_14 > 50%)
    if is_black_hole:
        delta_porchlamp_bh = normal_porchlamp
        delta_fric_bh = normal_fric - 100.0
        actions.append({
            "bottleneck": "Stage 0 Black Hole (PR Ghost Rate > 50%)",
            "recommendation": (
                "Clear unreviewed PR backlog to reduce 14-day ghost rate below 50%, "
                "restoring normal repository qualification."
            ),
            "delta_param_score": round(normal_porchlamp, 2),
            "delta_porchlamp": round(delta_porchlamp_bh, 2),
            "delta_friction": round(delta_fric_bh, 2),
            "pillar": 0,
            "delta_P": 0.0,
        })

    # 4. Low Elephant Factor (EF <= 2)
    if meta.elephant_factor <= 2:
        p_curr = score_decentralization(meta.elephant_factor)
        delta_p = 100.0 - p_curr
        delta_porchlamp = 0.20 * 0.35 * delta_p
        delta_fric = -0.30 * 0.35 * delta_p
        actions.append({
            "bottleneck": "Low Elephant Factor (EF <= 2)",
            "recommendation": (
                "Diversify maintainer and contributor affiliations across >= 4 "
                "organizations to mitigate single-vendor risk."
            ),
            "delta_param_score": round(delta_p, 2),
            "delta_porchlamp": round(delta_porchlamp, 2),
            "delta_friction": round(delta_fric, 2),
            "pillar": 3,
            "delta_P": 0.35 * delta_p,
        })

    # 5. Corporate CLA Relicensing Hazard (Unilateral / Reciprocal)
    if meta.legal_ip_model == LegalIPModel.CORP_CLA_UNILATERAL:
        delta_p = 90.0  # 10.0 -> 100.0
        delta_porchlamp = 0.20 * 0.35 * delta_p  # 6.30
        delta_fric = -0.30 * 0.35 * delta_p  # -9.45
        actions.append({
            "bottleneck": "Corporate CLA Unilateral Relicensing Hazard",
            "recommendation": (
                "Replace unilateral corporate CLA with Developer Certificate of Origin (DCO) "
                "or Foundation CLA to eliminate relicensing risks."
            ),
            "delta_param_score": round(delta_p, 2),
            "delta_porchlamp": round(delta_porchlamp, 2),
            "delta_friction": round(delta_fric, 2),
            "pillar": 3,
            "delta_P": 0.35 * delta_p,
        })
    elif meta.legal_ip_model == LegalIPModel.CORP_CLA_RECIPROCAL:
        delta_p = 60.0  # 40.0 -> 100.0
        delta_porchlamp = 0.20 * 0.35 * delta_p  # 4.20
        delta_fric = -0.30 * 0.35 * delta_p  # -6.30
        actions.append({
            "bottleneck": "Corporate CLA Reciprocal Relicensing Hazard",
            "recommendation": (
                "Transition from reciprocal corporate CLA to Developer Certificate of Origin (DCO) "
                "to reduce contributor legal friction."
            ),
            "delta_param_score": round(delta_p, 2),
            "delta_porchlamp": round(delta_porchlamp, 2),
            "delta_friction": round(delta_fric, 2),
            "pillar": 3,
            "delta_P": 0.35 * delta_p,
        })

    # 6. Deficient Good First Issues (GFIs < 10)
    if meta.active_unclaimed_gfis < 10:
        p_curr = score_gfi(meta.active_unclaimed_gfis)
        delta_p = 100.0 - p_curr
        delta_porchlamp = 0.15 * 0.40 * delta_p
        delta_fric = -0.20 * 0.40 * delta_p
        actions.append({
            "bottleneck": "Deficient Good First Issues (GFIs < 10)",
            "recommendation": (
                "Curate and maintain at least 10 active, unassigned good-first-issue "
                "opportunities to streamline newcomer onboarding."
            ),
            "delta_param_score": round(delta_p, 2),
            "delta_porchlamp": round(delta_porchlamp, 2),
            "delta_friction": round(delta_fric, 2),
            "pillar": 5,
            "delta_P": 0.40 * delta_p,
        })

    # 7. First-Timer Merge Friction (MR_first < 80%)
    decided_first_timers = [
        p
        for p in external_prs
        if (p.merged_at is not None or p.closed_at is not None)
        and p.is_first_timer
    ]
    if decided_first_timers:
        merged_count = sum(1 for p in decided_first_timers if p.merged_at is not None)
        mr_first = merged_count / len(decided_first_timers)
        if mr_first < 0.80:
            p_curr = score_mr_first(pr_events)
            delta_p = 100.0 - p_curr
            delta_porchlamp = 0.25 * 0.25 * delta_p
            delta_fric = -0.50 * 0.25 * delta_p
            actions.append({
                "bottleneck": "First-Timer Merge Friction (MR_first < 80%)",
                "recommendation": (
                    "Provide proactive onboarding guidance to improve first-timer "
                    "merge rate to at least 80%."
                ),
                "delta_param_score": round(delta_p, 2),
                "delta_porchlamp": round(delta_porchlamp, 2),
                "delta_friction": round(delta_fric, 2),
                "pillar": 1,
                "delta_P": 0.25 * delta_p,
            })

    # 8. Weak CI/CD Pipeline Defenses (OpenSSF Scorecard < 8.0)
    if meta.scorecard_score < 8.0:
        p_curr = score_pipeline(meta.scorecard_score)
        target_score = 90.0  # OpenSSF Scorecard >= 9.0
        delta_p = target_score - p_curr
        delta_porchlamp = 0.15 * 0.40 * delta_p
        delta_fric = 0.0  # Rigor is not in Friction formula
        actions.append({
            "bottleneck": "Weak CI/CD Pipeline Defenses (OpenSSF Scorecard < 8.0)",
            "recommendation": (
                "Enforce branch protection, automated SAST, and mandatory review "
                "to elevate OpenSSF Scorecard to >= 9.0."
            ),
            "delta_param_score": round(delta_p, 2),
            "delta_porchlamp": round(delta_porchlamp, 2),
            "delta_friction": round(delta_fric, 2),
            "pillar": 4,
            "delta_P": 0.40 * delta_p,
        })

    # Sort actions descending by delta_porchlamp and clamp marginal pillar gain
    # so actions on a pillar cannot exceed 100.0 - cur_pillars[pillar].
    pillar_weights = {
        1: PILLAR_WEIGHTS["responsiveness"],
        3: PILLAR_WEIGHTS["governance"],
        4: PILLAR_WEIGHTS["rigor"],
        5: PILLAR_WEIGHTS["accessibility"],
    }
    cur_pillars = {
        1: base_pillars.responsiveness,
        3: base_pillars.governance,
        4: base_pillars.rigor,
        5: base_pillars.accessibility,
    }

    pillar_counts: dict[int, int] = {}
    for a in actions:
        p_idx = a["pillar"]
        pillar_counts[p_idx] = pillar_counts.get(p_idx, 0) + 1

    actions.sort(key=lambda a: (-a["delta_porchlamp"], a["delta_friction"], a["bottleneck"]))
    for a in actions:
        p_idx = a["pillar"]
        if p_idx in cur_pillars:
            if pillar_counts.get(p_idx, 0) > 1:
                headroom = max(0.0, 100.0 - cur_pillars[p_idx])
                orig_delta_P = a["delta_P"]
                eff_delta_P = min(orig_delta_P, headroom)
                if eff_delta_P < orig_delta_P:
                    scale = (eff_delta_P / orig_delta_P) if orig_delta_P > 0 else 0.0
                    a["delta_P"] = eff_delta_P
                    a["delta_porchlamp"] = round(pillar_weights[p_idx] * eff_delta_P, 2)
                    a["delta_friction"] = round(a["delta_friction"] * scale, 2)
            else:
                eff_delta_P = a["delta_P"]
            cur_pillars[p_idx] = min(100.0, cur_pillars[p_idx] + eff_delta_P)

    actions.sort(key=lambda a: (-a["delta_porchlamp"], a["delta_friction"], a["bottleneck"]))
    for idx, a in enumerate(actions, start=1):
        a["priority"] = idx

    total_point_gain = round(sum(a["delta_porchlamp"] for a in actions), 2)
    potential_porchlamp = min(100.0, round(current_porchlamp + total_point_gain, 2))
    projected_tier = assign_tier(potential_porchlamp).value

    proj_ps = PillarScores(
        responsiveness=cur_pillars[1],
        criticality=base_pillars.criticality,
        governance=cur_pillars[3],
        rigor=cur_pillars[4],
        accessibility=cur_pillars[5],
    )
    _, _, proj_quad = decompose(proj_ps)
    projected_quadrant = proj_quad.value

    clean_actions = [
        {
            "priority": a["priority"],
            "bottleneck": a["bottleneck"],
            "recommendation": a["recommendation"],
            "delta_param_score": a["delta_param_score"],
            "delta_porchlamp": a["delta_porchlamp"],
            "delta_friction": a["delta_friction"],
        }
        for a in actions
    ]

    return {
        "current_porchlamp": round(current_porchlamp, 2),
        "current_tier": current_tier,
        "current_quadrant": current_quadrant,
        "potential_porchlamp": potential_porchlamp,
        "projected_tier": projected_tier,
        "projected_quadrant": projected_quadrant,
        "total_point_gain": total_point_gain,
        "actions": clean_actions,
    }
