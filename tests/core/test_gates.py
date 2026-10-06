"""Acceptance tests for the v2 stage-0 gates (02 section 2, D-GATE-1..6, D-EVID-1).

``porchlamp.core.gates`` evaluates every gate, records ``UNDECIDED`` gates by their
decision id, and reports the *first* failing gate in the design's fixed order
as the reason.
"""
from __future__ import annotations

import datetime
from typing import Any

import pytest

from porchlamp.core import constants
from porchlamp.core.models import (
    Cohorts,
    FeatureBundle,
    Measured,
    PRWindow,
    PRWindowEvent,
    RepoIdentity,
    Unmeasured,
)

#: Snapshot reference time; every window in the oracles is measured against it.
NOW = datetime.datetime(2026, 9, 12, 0, 0, tzinfo=datetime.timezone.utc)

_HASH = "ab" * 32


def _iso(value: datetime.datetime) -> str:
    return value.isoformat()


def _ago(days: float) -> datetime.datetime:
    return NOW - datetime.timedelta(days=days)


def make_inputs() -> dict[str, Unmeasured]:
    """All 18 parameter inputs unmeasured: the oracles exercise stage 0 only."""
    return {
        input_id: Unmeasured(reason="collector_failure", detail="stage-0 oracle fixture")
        for input_id in constants.PARAM_INPUT_IDS
    }


def make_repo(**overrides: Any) -> RepoIdentity:
    repo = RepoIdentity(
        owner="org",
        name="alpha",
        ecosystem="pypi",
        archived=False,
        mirror_url=None,
        fork=False,
        license_spdx="MIT",
        default_branch="main",
    )
    if not overrides:
        return repo
    return RepoIdentity(**{**repo.__dict__, **overrides})


def make_event(
    pr_number: int,
    created_days_ago: float,
    is_external: bool = True,
    *,
    merged_days_after: float | None = None,
    closed_days_after: float | None = None,
    first_human_review_days_after: float | None = None,
) -> PRWindowEvent:
    created = _ago(created_days_ago)
    return PRWindowEvent(
        pr_number=pr_number,
        created_at=_iso(created),
        closed_at=_iso(created + datetime.timedelta(days=closed_days_after)) if closed_days_after else None,
        merged_at=_iso(created + datetime.timedelta(days=merged_days_after)) if merged_days_after else None,
        author_login="contributor",
        author_association="NONE",
        is_external=is_external,
        first_human_review_at=(
            _iso(created + datetime.timedelta(days=first_human_review_days_after))
            if first_human_review_days_after is not None
            else None
        ),
    )


def ghost_input(unanswered: int, n: int) -> Measured:
    """The 1.4 ``gr14_ghost_rate`` input D-GATE-5 reads: share and population."""
    return Measured(
        value=unanswered / n, n=n, window_start=_iso(_ago(180)), window_end=_iso(NOW),
        method="primary", source="raw/pr_window/org__alpha.json", source_hash=_HASH,
        fetched_at=_iso(NOW),
    )


#: A clean D-GATE-5: 0 of 10 external PRs unanswered.
HEALTHY_GHOST = ghost_input(0, 10)


def make_bundle(
    events: list[PRWindowEvent], *, ghost: Measured | None = None, **repo_overrides: Any
) -> FeatureBundle:
    """A valid bundle; the window bounds are null for an empty event list
    (FeatureBundle invariant: bounds are set exactly when events are present).
    ``ghost`` is the ``gr14_ghost_rate`` input (D-GATE-5); ``None`` leaves it
    unmeasured, so D-GATE-5 is UNDECIDED."""
    inputs = make_inputs()
    if ghost is not None:
        inputs["gr14_ghost_rate"] = ghost
    bundle = FeatureBundle(
        schema_version=constants.SCHEMA_VERSION,
        repo=make_repo(**repo_overrides),
        inputs=inputs,
        pr_window=PRWindow(
            start=_iso(_ago(180)) if events else None,
            end=_iso(NOW) if events else None,
            truncated=False,
            events=tuple(events),
        ),
        cohorts=Cohorts(decision_pr_numbers=(), newcomer_authors=()),
        provenance_hash=_HASH,
    )
    bundle.validate()
    return bundle


def healthy_evidence() -> Any:
    from porchlamp.core import gates

    return gates.GateEvidence(
        manifest_found=True,
        is_curated_anchor=False,
        license_file_present=None,
        fork_parent_in_universe=None,
        vitality_commits_180d=3,
        off_platform_marker=False,
    )


def healthy_events() -> list[PRWindowEvent]:
    """Ten answered, merged external PRs: D-GATE-6 sees a decided external
    PR. (D-GATE-5 reads the gr14 input instead: pass ``HEALTHY_GHOST``.)"""
    return [
        make_event(100 + i, 30, merged_days_after=2, closed_days_after=2, first_human_review_days_after=1)
        for i in range(10)
    ]


def recent_decided_external() -> PRWindowEvent:
    """An external PR decided three days ago: satisfies D-GATE-6."""
    return make_event(900, 3, merged_days_after=2, closed_days_after=2, first_human_review_days_after=1)


# --------------------------------------------------------------------------- #
# D-GATE-5 / 1.4 GR_14: one zero-response share, read by the gate as gr14     #
# --------------------------------------------------------------------------- #


def test_v1_semantics_says_black_hole_when_denominator_is_open_only() -> None:
    """Demonstrate v1 GR_14 counts open external PRs only, so 2 stale open + 28 merged reads as a black hole."""
    from porchlamp.models import AuthorAssociation, Ecosystem, RawPREvent, RepoMeta
    from porchlamp.stage0 import apply_stage0

    events: list[RawPREvent] = [
        RawPREvent(
            repo_owner="org",
            repo_name="alpha",
            pr_number=1000 + i,
            created_at=_ago(30),
            author_login="outsider",
            author_association=AuthorAssociation.NONE,
            is_external=True,
            maintainer_review_comments=0,
        )
        for i in range(2)
    ]
    events += [
        RawPREvent(
            repo_owner="org",
            repo_name="alpha",
            pr_number=2000 + i,
            created_at=_ago(20 + i),
            author_login="outsider",
            author_association=AuthorAssociation.NONE,
            is_external=True,
            closed_at=_ago(20 + i) + datetime.timedelta(days=2),
            merged_at=_ago(20 + i) + datetime.timedelta(days=2),
            maintainer_review_comments=1,
        )
        for i in range(28)
    ]
    meta = RepoMeta(
        owner="org",
        name="alpha",
        ecosystem=Ecosystem.PYPI,
        active_nonbot_commits_180d=5,
    )
    result = apply_stage0(meta, events, NOW)
    assert result.passed is False
    assert result.is_black_hole is True


def ghost_pr(
    created_days_ago: float,
    *,
    external: bool = True,
    bot: bool = False,
    response_days_after: float | None = None,
) -> Any:
    from porchlamp.core import gates

    created = _ago(created_days_ago)
    return gates.GhostRatePR(
        created_at=_iso(created),
        is_external=external,
        is_bot_author=bot,
        first_substantive_response_at=(
            _iso(created + datetime.timedelta(days=response_days_after))
            if response_days_after is not None else None
        ),
    )


def test_population_excludes_bot_authored_prs() -> None:
    """D-IN-1: 6 unanswered + 4 answered human PRs, plus 5 unanswered bot
    PRs -> 6/10; counting the bots would read 11/15."""
    from porchlamp.core import gates

    prs = [ghost_pr(30) for _ in range(6)] + [ghost_pr(30, response_days_after=1) for _ in range(4)]
    prs += [ghost_pr(30, bot=True) for _ in range(5)]
    assert gates.zero_response_counts(prs, NOW) == (6, 10)


def test_population_is_the_trailing_180_day_window() -> None:
    """Opened 179 d ago: in. Opened 181 d ago: out (02 section 3 D-IN-4)."""
    from porchlamp.core import gates

    assert gates.zero_response_counts([ghost_pr(179), ghost_pr(181)], NOW) == (1, 1)


def test_population_needs_14_days_of_age_at_now() -> None:
    """Exactly 14 d old: in. 13.5 d old: out -- its deadline has not passed."""
    from porchlamp.core import gates

    assert gates.zero_response_counts([ghost_pr(14), ghost_pr(13.5)], NOW) == (1, 1)


def test_internal_prs_do_not_enter_the_population() -> None:
    from porchlamp.core import gates

    assert gates.zero_response_counts([ghost_pr(30, external=False) for _ in range(12)], NOW) == (0, 0)


def test_response_deadline_is_14_days_after_opening() -> None:
    """A D-IN-2 response at day 14 counts; at day 15 it does not."""
    from porchlamp.core import gates

    prs = [ghost_pr(30, response_days_after=14), ghost_pr(30, response_days_after=15)]
    assert gates.zero_response_counts(prs, NOW) == (1, 2)


def test_zero_response_counts_requires_an_aware_now() -> None:
    from porchlamp.core import gates

    with pytest.raises(ValueError, match="timezone-aware"):
        gates.zero_response_counts([ghost_pr(30)], NOW.replace(tzinfo=None))


def test_ten_prs_six_unanswered_is_black_hole() -> None:
    """Oracle 2: gr14 = 6/10 = 0.60 > 0.50, gated black_hole."""
    from porchlamp.core import gates

    result = gates.evaluate_gates(make_bundle(healthy_events(), ghost=ghost_input(6, 10)), healthy_evidence(), NOW)
    assert result.eligible is False
    assert result.reason == "black_hole"
    assert result.undecided == ()


def test_share_exactly_half_is_not_black_hole() -> None:
    """Boundary: 5 of 10 unanswered is exactly 0.50, which the design allows."""
    from porchlamp.core import gates

    result = gates.evaluate_gates(make_bundle(healthy_events(), ghost=ghost_input(5, 10)), healthy_evidence(), NOW)
    assert result.eligible is True
    assert result.reason is None
    assert "D-GATE-5" not in result.undecided


def test_nine_prs_is_undecided_not_black_hole() -> None:
    """Oracle 1: n = 9 < 10 -> UNDECIDED even at a 9/9 share, never a black hole."""
    from porchlamp.core import gates

    result = gates.evaluate_gates(make_bundle(healthy_events(), ghost=ghost_input(9, 9)), healthy_evidence(), NOW)
    assert result.eligible is True
    assert result.reason is None
    assert result.undecided == ("D-GATE-5",)


def test_unmeasured_ghost_rate_is_undecided() -> None:
    from porchlamp.core import gates

    result = gates.evaluate_gates(make_bundle(healthy_events()), healthy_evidence(), NOW)
    assert result.eligible is True
    assert result.undecided == ("D-GATE-5",)


# --------------------------------------------------------------------------- #
# D-GATE-1..4: software, archive/mirror/fork, licence, vitality                #
# --------------------------------------------------------------------------- #


def test_non_software_when_no_manifest_at_depth_two() -> None:
    from porchlamp.core import gates

    evidence = healthy_evidence()
    result = gates.evaluate_gates(
        make_bundle(healthy_events(), ghost=HEALTHY_GHOST),
        gates.GateEvidence(
            manifest_found=False,
            is_curated_anchor=evidence.is_curated_anchor,
            license_file_present=evidence.license_file_present,
            fork_parent_in_universe=evidence.fork_parent_in_universe,
            vitality_commits_180d=evidence.vitality_commits_180d,
            off_platform_marker=evidence.off_platform_marker,
        ),
        NOW,
    )
    assert result.eligible is False
    assert result.reason == "non_software"
    assert result.undecided == ()


def test_non_software_undecided_when_tree_unknown() -> None:
    from porchlamp.core import gates

    evidence = healthy_evidence()
    result = gates.evaluate_gates(
        make_bundle(healthy_events(), ghost=HEALTHY_GHOST),
        gates.GateEvidence(
            manifest_found=None,
            is_curated_anchor=evidence.is_curated_anchor,
            license_file_present=evidence.license_file_present,
            fork_parent_in_universe=evidence.fork_parent_in_universe,
            vitality_commits_180d=evidence.vitality_commits_180d,
            off_platform_marker=evidence.off_platform_marker,
        ),
        NOW,
    )
    assert result.eligible is True
    assert result.reason is None
    assert result.undecided == ("D-GATE-1",)


def test_curated_anchor_bypasses_the_manifest_gate() -> None:
    from porchlamp.core import gates

    evidence = healthy_evidence()
    result = gates.evaluate_gates(
        make_bundle(healthy_events(), ghost=HEALTHY_GHOST),
        gates.GateEvidence(
            manifest_found=None,
            is_curated_anchor=True,
            license_file_present=evidence.license_file_present,
            fork_parent_in_universe=evidence.fork_parent_in_universe,
            vitality_commits_180d=evidence.vitality_commits_180d,
            off_platform_marker=evidence.off_platform_marker,
        ),
        NOW,
    )
    assert result.eligible is True
    assert result.reason is None
    assert result.undecided == ()


def test_archived_repo_is_gated() -> None:
    from porchlamp.core import gates

    result = gates.evaluate_gates(make_bundle(healthy_events(), ghost=HEALTHY_GHOST, archived=True), healthy_evidence(), NOW)
    assert result.eligible is False
    assert result.reason == "archived"


def test_mirror_repo_is_gated() -> None:
    from porchlamp.core import gates

    result = gates.evaluate_gates(
        make_bundle(healthy_events(), ghost=HEALTHY_GHOST, mirror_url="https://github.com/upstream/alpha.git"),
        healthy_evidence(),
        NOW,
    )
    assert result.eligible is False
    assert result.reason == "mirror"


def test_fork_is_gated_when_parent_is_in_the_universe() -> None:
    from porchlamp.core import gates

    result = gates.evaluate_gates(
        make_bundle(healthy_events(), ghost=HEALTHY_GHOST, fork=True),
        gates.GateEvidence(
            manifest_found=True,
            is_curated_anchor=False,
            license_file_present=None,
            fork_parent_in_universe=True,
            vitality_commits_180d=3,
            off_platform_marker=False,
        ),
        NOW,
    )
    assert result.eligible is False
    assert result.reason == "fork"


def test_fork_is_allowed_when_parent_is_absent_from_the_universe() -> None:
    from porchlamp.core import gates

    result = gates.evaluate_gates(
        make_bundle(healthy_events(), ghost=HEALTHY_GHOST, fork=True),
        gates.GateEvidence(
            manifest_found=True,
            is_curated_anchor=False,
            license_file_present=None,
            fork_parent_in_universe=False,
            vitality_commits_180d=3,
            off_platform_marker=False,
        ),
        NOW,
    )
    assert result.eligible is True
    assert result.reason is None
    assert result.undecided == ()


def test_fork_is_undecided_when_parent_membership_is_unknown() -> None:
    from porchlamp.core import gates

    result = gates.evaluate_gates(
        make_bundle(healthy_events(), ghost=HEALTHY_GHOST, fork=True),
        healthy_evidence(),
        NOW,
    )
    assert result.eligible is True
    assert result.reason is None
    assert result.undecided == ("D-GATE-2",)


def test_unlicensed_when_spdx_undetermined_and_no_license_file() -> None:
    from porchlamp.core import gates

    result = gates.evaluate_gates(
        make_bundle(healthy_events(), ghost=HEALTHY_GHOST, license_spdx="NOASSERTION"),
        gates.GateEvidence(
            manifest_found=True,
            is_curated_anchor=False,
            license_file_present=False,
            fork_parent_in_universe=None,
            vitality_commits_180d=3,
            off_platform_marker=False,
        ),
        NOW,
    )
    assert result.eligible is False
    assert result.reason == "unlicensed"


def test_unlicensed_when_no_spdx_recorded_and_no_license_file() -> None:
    from porchlamp.core import gates

    result = gates.evaluate_gates(
        make_bundle(healthy_events(), ghost=HEALTHY_GHOST, license_spdx=None),
        gates.GateEvidence(
            manifest_found=True,
            is_curated_anchor=False,
            license_file_present=False,
            fork_parent_in_universe=None,
            vitality_commits_180d=3,
            off_platform_marker=False,
        ),
        NOW,
    )
    assert result.eligible is False
    assert result.reason == "unlicensed"


def test_undetermined_spdx_with_a_license_file_passes() -> None:
    from porchlamp.core import gates

    result = gates.evaluate_gates(
        make_bundle(healthy_events(), ghost=HEALTHY_GHOST, license_spdx="OTHER"),
        gates.GateEvidence(
            manifest_found=True,
            is_curated_anchor=False,
            license_file_present=True,
            fork_parent_in_universe=None,
            vitality_commits_180d=3,
            off_platform_marker=False,
        ),
        NOW,
    )
    assert result.eligible is True
    assert result.reason is None
    assert result.undecided == ()


def test_license_file_unknown_is_undecided_not_a_failure() -> None:
    from porchlamp.core import gates

    result = gates.evaluate_gates(
        make_bundle(healthy_events(), ghost=HEALTHY_GHOST, license_spdx="NOASSERTION"),
        healthy_evidence(),
        NOW,
    )
    assert result.eligible is True
    assert result.reason is None
    assert result.undecided == ("D-GATE-3",)


def test_recorded_non_osi_spdx_id_is_gated_unlicensed() -> None:
    from porchlamp.core import gates

    result = gates.evaluate_gates(
        make_bundle(healthy_events(), ghost=HEALTHY_GHOST, license_spdx="BUSL-1.1"),
        healthy_evidence(),
        NOW,
    )
    assert result.eligible is False
    assert result.reason == "unlicensed"


def test_osi_approved_spdx_id_passes() -> None:
    from porchlamp.core import gates

    result = gates.evaluate_gates(
        make_bundle(healthy_events(), ghost=HEALTHY_GHOST, license_spdx="Apache-2.0"),
        healthy_evidence(),
        NOW,
    )
    assert result.eligible is True
    assert result.reason is None
    assert result.undecided == ()


def test_stale_when_zero_non_bot_vitality_commits() -> None:
    from porchlamp.core import gates

    result = gates.evaluate_gates(
        make_bundle(healthy_events(), ghost=HEALTHY_GHOST),
        gates.GateEvidence(
            manifest_found=True,
            is_curated_anchor=False,
            license_file_present=None,
            fork_parent_in_universe=None,
            vitality_commits_180d=0,
            off_platform_marker=False,
        ),
        NOW,
    )
    assert result.eligible is False
    assert result.reason == "stale"


def test_vitality_unknown_is_undecided_not_stale() -> None:
    from porchlamp.core import gates

    result = gates.evaluate_gates(
        make_bundle(healthy_events(), ghost=HEALTHY_GHOST),
        gates.GateEvidence(
            manifest_found=True,
            is_curated_anchor=False,
            license_file_present=None,
            fork_parent_in_universe=None,
            vitality_commits_180d=None,
            off_platform_marker=False,
        ),
        NOW,
    )
    assert result.eligible is True
    assert result.reason is None
    assert result.undecided == ("D-GATE-4",)


# --------------------------------------------------------------------------- #
# D-GATE-6: PR workflow routing                                                #
# --------------------------------------------------------------------------- #


def test_off_platform_marker_routes_the_repo() -> None:
    from porchlamp.core import gates

    result = gates.evaluate_gates(
        make_bundle([]),
        gates.GateEvidence(
            manifest_found=True,
            is_curated_anchor=False,
            license_file_present=None,
            fork_parent_in_universe=None,
            vitality_commits_180d=3,
            off_platform_marker=True,
        ),
        NOW,
    )
    assert result.eligible is True
    assert result.reason is None
    assert result.routed == "off_platform"


def test_no_external_workflow_is_unrated() -> None:
    from porchlamp.core import gates

    result = gates.evaluate_gates(
        make_bundle([]),
        gates.GateEvidence(
            manifest_found=True,
            is_curated_anchor=False,
            license_file_present=None,
            fork_parent_in_universe=None,
            vitality_commits_180d=3,
            off_platform_marker=False,
        ),
        NOW,
    )
    assert result.eligible is False
    assert result.reason == "unrated: no_external_workflow"
    assert result.routed is None


def test_internal_decided_prs_do_not_count_as_an_external_workflow() -> None:
    from porchlamp.core import gates

    result = gates.evaluate_gates(
        make_bundle([make_event(1, 30, is_external=False, merged_days_after=2, closed_days_after=2)]),
        healthy_evidence(),
        NOW,
    )
    assert result.eligible is False
    assert result.reason == "unrated: no_external_workflow"


def test_off_platform_marker_unknown_is_undecided() -> None:
    from porchlamp.core import gates

    result = gates.evaluate_gates(
        make_bundle([]),
        gates.GateEvidence(
            manifest_found=True,
            is_curated_anchor=False,
            license_file_present=None,
            fork_parent_in_universe=None,
            vitality_commits_180d=3,
            off_platform_marker=None,
        ),
        NOW,
    )
    assert result.eligible is True
    assert result.reason is None
    assert result.routed is None
    # D-GATE-6 records UNDECIDED; D-GATE-5 does as well (gr14 unmeasured).
    assert result.undecided == ("D-GATE-5", "D-GATE-6")


def test_a_recent_decided_external_pr_satisfies_the_workflow_gate() -> None:
    from porchlamp.core import gates

    result = gates.evaluate_gates(
        make_bundle([make_event(1, 3, merged_days_after=1, closed_days_after=1)]),
        healthy_evidence(),
        NOW,
    )
    assert result.eligible is True
    assert result.reason is None
    assert result.routed is None
    # gr14 is unmeasured, so D-GATE-5 records UNDECIDED while D-GATE-6
    # passes on the recent decision.
    assert result.undecided == ("D-GATE-5",)


# --------------------------------------------------------------------------- #
# Ordering, recording and the D-EVID-1 evidence gate                           #
# --------------------------------------------------------------------------- #


def test_all_gates_are_evaluated_and_first_failure_wins() -> None:
    """Archived (gate 2) plus a 9-PR ghost-rate population (gate 5 UNDECIDED):
    the reason is the first failure, but gate 5 is still evaluated and recorded."""
    from porchlamp.core import gates

    events = [make_event(10 + i, 30) for i in range(9)]
    result = gates.evaluate_gates(
        make_bundle(events, ghost=ghost_input(9, 9), archived=True), healthy_evidence(), NOW)
    assert result.eligible is False
    assert result.reason == "archived"
    assert result.undecided == ("D-GATE-5",)


def test_healthy_repo_passes_every_gate() -> None:
    """Over-correction guard: the correct path is unchanged — a repo that
    passes every gate comes back fully eligible with nothing recorded."""
    from porchlamp.core import gates

    result = gates.evaluate_gates(make_bundle(healthy_events(), ghost=HEALTHY_GHOST), healthy_evidence(), NOW)
    assert result.eligible is True
    assert result.reason is None
    assert result.routed is None
    assert result.undecided == ()


def test_naive_reference_time_is_rejected() -> None:
    from porchlamp.core import gates

    with pytest.raises(ValueError):
        gates.evaluate_gates(
            make_bundle(healthy_events(), ghost=HEALTHY_GHOST), healthy_evidence(), NOW.replace(tzinfo=None)
        )


def test_gate_evidence_rejects_invalid_fields() -> None:
    from porchlamp.core import gates

    with pytest.raises(ValueError):
        gates.GateEvidence(manifest_found="yes")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        gates.GateEvidence(
            manifest_found=True,
            vitality_commits_180d=-1,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError):
        gates.GateEvidence(
            manifest_found=True,
            is_curated_anchor="anchor",  # type: ignore[arg-type]
        )


def test_evidence_gate_rates_at_the_threshold() -> None:
    from porchlamp.core import gates

    result = gates.evidence_sufficiency(0.60)
    assert result.eligible is True
    assert result.reason is None
    assert result.routed is None
    assert result.undecided == ()


def test_evidence_gate_unrates_below_the_threshold() -> None:
    from porchlamp.core import gates

    result = gates.evidence_sufficiency(0.599)
    assert result.eligible is False
    assert result.reason == "unrated: insufficient_evidence"
    assert result.routed is None
    assert result.undecided == ()


def test_evidence_gate_rejects_out_of_range_coverage() -> None:
    from porchlamp.core import gates

    with pytest.raises(ValueError):
        gates.evidence_sufficiency(-0.1)
    with pytest.raises(ValueError):
        gates.evidence_sufficiency(1.2)
    with pytest.raises(ValueError):
        gates.evidence_sufficiency(True)  # type: ignore[arg-type]


def test_spdx_compound_expression_boolean_precedence_and_parentheses() -> None:
    """Regression: compound SPDX expressions with mixed AND/OR and parentheses
    are evaluated with proper boolean precedence (WITH > AND > OR)."""
    from porchlamp.core import gates

    assert gates._is_osi_approved_expression("(MIT OR Apache-2.0) AND Proprietary-1.0") is False
    assert gates._is_osi_approved_expression("MIT AND (BUSL-1.1 OR Proprietary-1.0)") is False
    assert gates._is_osi_approved_expression("(MIT OR Proprietary-1.0) AND Apache-2.0") is True
    assert gates._is_osi_approved_expression("(MIT)OR(Proprietary-1.0)") is True
    assert gates._is_osi_approved_expression("MIT OR (Apache-2.0 AND Proprietary-1.0)") is True
    assert gates._is_osi_approved_expression("Proprietary-1.0 OR (MIT AND BUSL-1.1)") is False
    assert gates._is_osi_approved_expression("(Apache-2.0 WITH LLVM-exception) AND MIT") is True
    assert gates._is_osi_approved_expression("(Apache-2.0 WITH LLVM-exception) AND BUSL-1.1") is False

    # Verify through evaluate_gates as well
    res_fail = gates.evaluate_gates(
        make_bundle(healthy_events(), ghost=HEALTHY_GHOST, license_spdx="(MIT OR Apache-2.0) AND Proprietary-1.0"),
        healthy_evidence(),
        NOW,
    )
    assert res_fail.eligible is False
    assert res_fail.reason == "unlicensed"

    res_pass = gates.evaluate_gates(
        make_bundle(healthy_events(), ghost=HEALTHY_GHOST, license_spdx="(MIT)OR(Proprietary-1.0)"),
        healthy_evidence(),
        NOW,
    )
    assert res_pass.eligible is True
    assert res_pass.reason is None

