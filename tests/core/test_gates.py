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
    merged_days_after: float | None = None,
) -> Any:
    from porchlamp.core import gates

    created = _ago(created_days_ago)
    merged: dict[str, Any] = {}
    if merged_days_after is not None:
        merged["merged_at"] = _iso(created + datetime.timedelta(days=merged_days_after))
    return gates.GhostRatePR(
        created_at=_iso(created),
        is_external=external,
        is_bot_author=bot,
        first_substantive_response_at=(
            _iso(created + datetime.timedelta(days=response_days_after))
            if response_days_after is not None else None
        ),
        **merged,
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


def test_merge_within_14_days_answers_and_after_does_not() -> None:
    """Owner ruling 2026-10-06: a merge within 14 d of opening is a response
    even with no review or comment ("no human reply, review or merge"). A
    merge on day 10 answers; a merge on day 15 does not; day 14 is inside."""
    from porchlamp.core import gates

    assert gates.zero_response_counts([ghost_pr(30, merged_days_after=10)], NOW) == (0, 1)
    assert gates.zero_response_counts([ghost_pr(30, merged_days_after=14)], NOW) == (0, 1)
    assert gates.zero_response_counts([ghost_pr(30, merged_days_after=15)], NOW) == (1, 1)


def test_late_merge_does_not_cancel_an_early_response() -> None:
    """Over-correction guard: the merge rule only adds answers. A D-IN-2
    response on day 2 answers the PR even when the merge came on day 40."""
    from porchlamp.core import gates

    pr = ghost_pr(60, response_days_after=2, merged_days_after=40)
    assert gates.zero_response_counts([pr], NOW) == (0, 1)


def test_population_needs_14_days_of_observation() -> None:
    """Owner/lead ruling 2026-10-06: the 14-d age is judged at
    min(now, observed_at), the time GitHub was read. A PR 20 d old at `now`
    but observed for only 10 d is out; observed for 14 d it is in."""
    from porchlamp.core import gates

    pr = ghost_pr(20)
    observed_10 = NOW - datetime.timedelta(days=10)
    observed_14 = NOW - datetime.timedelta(days=6)
    assert gates.zero_response_counts([pr], NOW, observed_at=observed_10) == (0, 0)
    assert gates.zero_response_counts([pr], NOW, observed_at=observed_14) == (1, 1)


def test_observation_after_now_judges_age_at_now() -> None:
    """Over-correction guard: a fetch later than the reference time does not
    admit PRs younger than 14 d at `now` (min, not the observation time)."""
    from porchlamp.core import gates

    later = NOW + datetime.timedelta(days=5)
    assert gates.zero_response_counts([ghost_pr(14), ghost_pr(13.5)], NOW, observed_at=later) == (1, 1)


def test_observed_at_must_be_timezone_aware() -> None:
    from porchlamp.core import gates

    with pytest.raises(ValueError, match="timezone-aware"):
        gates.zero_response_counts([ghost_pr(30)], NOW, observed_at=NOW.replace(tzinfo=None))


def test_zero_response_counts_requires_an_aware_now() -> None:
    from porchlamp.core import gates

    with pytest.raises(ValueError, match="timezone-aware"):
        gates.zero_response_counts([ghost_pr(30)], NOW.replace(tzinfo=None))


def test_ten_prs_six_unanswered_is_black_hole() -> None:
    """RFC-0008: 6/10 (wilson lower bound <= 0.50) is protected from false-positive
    black-hole gating; 8/10 (wilson lower bound > 0.50) fails as black_hole."""
    from porchlamp.core import gates

    # 6/10 unanswered: raw share 0.60 > 0.50, but Wilson lower bound <= 0.50 -> eligible
    lb_6 = gates.wilson_lower_bound(6, 10)
    assert abs(lb_6 - 0.352) < 0.01
    assert lb_6 <= 0.50
    result_6 = gates.evaluate_gates(
        make_bundle(healthy_events(), ghost=ghost_input(6, 10)), healthy_evidence(), NOW
    )
    assert result_6.eligible is True
    assert result_6.reason is None
    assert "D-GATE-5" not in result_6.undecided

    # 8/10 unanswered: Wilson lower bound > 0.50 -> fails black_hole
    lb_8 = gates.wilson_lower_bound(8, 10)
    assert abs(lb_8 - 0.541) < 0.01
    assert lb_8 > 0.50
    result_8 = gates.evaluate_gates(
        make_bundle(healthy_events(), ghost=ghost_input(8, 10)), healthy_evidence(), NOW
    )
    assert result_8.eligible is False
    assert result_8.reason == "black_hole"
    assert result_8.undecided == ()


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
# D-GATE-5 / D-GATE-6 amendment: a project whose own notices send outside PRs #
# to another review system is routed off-platform, not failed as a black hole #
# --------------------------------------------------------------------------- #


def handoff_evidence(review_handoff: bool | None) -> Any:
    """``healthy_evidence`` with ``review_handoff`` set."""
    import dataclasses

    return dataclasses.replace(healthy_evidence(), review_handoff=review_handoff)


#: A ghost rate of 0.99 over 300 external PRs: far past the D-GATE-5 ceiling.
BLACK_HOLE_GHOST = ghost_input(297, 300)


def test_handoff_routes_instead_of_failing_the_black_hole_gate() -> None:
    from porchlamp.core import gates

    # healthy_events() holds decided external PRs, so D-GATE-6 would pass on
    # them alone; the hand-off must still route.
    result = gates.evaluate_gates(
        make_bundle(healthy_events(), ghost=BLACK_HOLE_GHOST), handoff_evidence(True), NOW)
    assert result.eligible is True
    assert result.reason is None
    assert result.routed == "off_platform"
    assert "D-GATE-5" in result.undecided


def test_handoff_does_not_rescue_an_archived_repository() -> None:
    from porchlamp.core import gates

    result = gates.evaluate_gates(
        make_bundle(healthy_events(), ghost=BLACK_HOLE_GHOST, archived=True), handoff_evidence(True), NOW)
    assert result.eligible is False
    assert result.reason == "archived"
    assert result.routed is None


def test_no_handoff_keeps_the_black_hole_failure() -> None:
    from porchlamp.core import gates

    for review_handoff in (False, None):
        result = gates.evaluate_gates(
            make_bundle(healthy_events(), ghost=BLACK_HOLE_GHOST), handoff_evidence(review_handoff), NOW)
        assert result.eligible is False, review_handoff
        assert result.reason == "black_hole", review_handoff
        assert result.routed is None, review_handoff


def test_handoff_without_a_measured_ghost_rate_still_records_gate_5_undecided_once() -> None:
    """Gate 5 had no verdict to give here either way: it is recorded once, not twice."""
    from porchlamp.core import gates

    result = gates.evaluate_gates(make_bundle(healthy_events()), handoff_evidence(True), NOW)
    assert result.routed == "off_platform"
    assert result.undecided == ("D-GATE-5",)


def test_gate_evidence_rejects_a_non_bool_review_handoff() -> None:
    from porchlamp.core import gates

    for bad in ("yes", 1, 0.5):
        with pytest.raises(ValueError, match="review_handoff"):
            gates.GateEvidence(manifest_found=True, review_handoff=bad)  # type: ignore[arg-type]
    for good in (True, False, None):
        assert gates.GateEvidence(manifest_found=True, review_handoff=good).review_handoff is good
    assert gates.GateEvidence(manifest_found=True).review_handoff is None


# --------------------------------------------------------------------------- #
# D-GATE-5 judges only a PR record that covers the whole window (RFC-0011)     #
# --------------------------------------------------------------------------- #


def test_a_record_that_stops_short_of_the_window_leaves_gate_5_undecided() -> None:
    import dataclasses

    from porchlamp.core import gates

    bundle = make_bundle(healthy_events(), ghost=BLACK_HOLE_GHOST)
    cut = dataclasses.replace(healthy_evidence(), pr_window_complete=False, pr_window_cutoff="2026-09-08T00:18:00Z")
    result = gates.evaluate_gates(bundle, cut, NOW)
    assert (result.eligible, result.reason, result.undecided) == (True, None, ("D-GATE-5",))
    for covered in (True, None):
        result = gates.evaluate_gates(bundle, dataclasses.replace(healthy_evidence(), pr_window_complete=covered), NOW)
        assert result.reason == "black_hole", covered


def test_gate_evidence_rejects_invalid_window_coverage() -> None:
    from porchlamp.core import gates

    with pytest.raises(ValueError, match="pr_window_complete"):
        gates.GateEvidence(manifest_found=True, pr_window_complete="no")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="pr_window_cutoff"):
        gates.GateEvidence(manifest_found=True, pr_window_cutoff=5)  # type: ignore[arg-type]
    assert gates.GateEvidence(manifest_found=True).pr_window_complete is None


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


def _decided_external_pr(value: bool | None) -> Any:
    """Healthy evidence whose PR records say ``value`` about a decided external PR."""
    import dataclasses

    return dataclasses.replace(healthy_evidence(), decided_external_pr=value)


def _no_decided_external_pr() -> Any:
    """Healthy evidence whose records hold the whole PR history, with no decided external PR."""
    return _decided_external_pr(False)


def test_no_external_workflow_is_unrated() -> None:
    from porchlamp.core import gates

    result = gates.evaluate_gates(make_bundle([]), _no_decided_external_pr(), NOW)
    assert result.eligible is False
    assert result.reason == "unrated: no_external_workflow"
    assert result.routed is None


def test_internal_decided_prs_do_not_count_as_an_external_workflow() -> None:
    from porchlamp.core import gates

    result = gates.evaluate_gates(
        make_bundle([make_event(1, 30, is_external=False, merged_days_after=2, closed_days_after=2)]),
        _no_decided_external_pr(),
        NOW,
    )
    assert result.eligible is False
    assert result.reason == "unrated: no_external_workflow"


def test_without_a_complete_pr_record_the_workflow_gate_is_undecided_not_failed() -> None:
    """The bundle's PR events are the 30 newest PRs; in an active repository they are mostly still
    open. None of them decided is no evidence that the repository takes no outside PRs."""
    from porchlamp.core import gates

    no_record = healthy_evidence()  # decided_external_pr None: no complete PR record was read
    newest_30_open = [make_event(100 + i, 2 + i * 0.1) for i in range(30)]
    result = gates.evaluate_gates(make_bundle(newest_30_open, ghost=HEALTHY_GHOST), no_record, NOW)
    assert (result.eligible, result.reason, result.undecided) == (True, None, ("D-GATE-6",))
    empty = gates.evaluate_gates(make_bundle([], ghost=HEALTHY_GHOST), no_record, NOW)
    assert (empty.eligible, empty.reason, empty.undecided) == (True, None, ("D-GATE-6",))


def test_a_decided_external_pr_in_the_recorded_window_passes_when_the_newest_30_show_none() -> None:
    from porchlamp.core import gates

    newest_30_open = [make_event(100 + i, 2 + i * 0.1) for i in range(30)]
    evidence = _decided_external_pr(True)
    result = gates.evaluate_gates(make_bundle(newest_30_open, ghost=HEALTHY_GHOST), evidence, NOW)
    assert (result.eligible, result.reason, result.routed, result.undecided) == (True, None, None, ())


def test_a_decided_external_pr_among_the_newest_30_passes_whatever_the_record_says() -> None:
    """The sample can show a decided external PR the record missed (one opened before the record's window)."""
    from porchlamp.core import gates

    events = [make_event(1, 300, merged_days_after=10, closed_days_after=10)]
    result = gates.evaluate_gates(make_bundle(events, ghost=HEALTHY_GHOST), _no_decided_external_pr(), NOW)
    assert (result.eligible, result.reason, result.undecided) == (True, None, ())


def test_decided_external_pr_must_be_a_bool_or_none() -> None:
    from porchlamp.core import gates

    with pytest.raises(ValueError, match="decided_external_pr"):
        gates.GateEvidence(manifest_found=True, decided_external_pr=1)


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
        make_bundle(events, ghost=ghost_input(9, 9), archived=True), _no_decided_external_pr(), NOW)
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


def _evidence_pillars(*, responsiveness: float | None = 80.0) -> dict[str, Any]:
    """The five pillars, every one measured at 80.0 except responsiveness,
    whose score is ``responsiveness`` (``None``: no measured parameter, the
    D-MISS-2 shape ``aggregate_pillars`` gives a wholly unmeasured pillar)."""
    from porchlamp.core.models import PillarScore

    pillars: dict[str, Any] = {name: PillarScore(score=80.0, coverage=1.0) for name in constants.PILLAR_NAMES}
    pillars["responsiveness"] = PillarScore(score=responsiveness, coverage=0.0 if responsiveness is None else 1.0)
    return pillars


def test_evidence_gate_rates_at_the_threshold() -> None:
    from porchlamp.core import gates

    result = gates.evidence_sufficiency(0.60, _evidence_pillars())
    assert result.eligible is True
    assert result.reason is None
    assert result.routed is None
    assert result.undecided == ()


def test_evidence_gate_unrates_below_the_threshold() -> None:
    from porchlamp.core import gates

    result = gates.evidence_sufficiency(0.599, _evidence_pillars())
    assert result.eligible is False
    assert result.reason == "unrated: insufficient_evidence"
    assert result.routed is None
    assert result.undecided == ()


def test_evidence_gate_rejects_out_of_range_coverage() -> None:
    from porchlamp.core import gates

    with pytest.raises(ValueError):
        gates.evidence_sufficiency(-0.1, _evidence_pillars())
    with pytest.raises(ValueError):
        gates.evidence_sufficiency(1.2, _evidence_pillars())
    with pytest.raises(ValueError):
        gates.evidence_sufficiency(True, _evidence_pillars())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("coverage", "responsiveness", "rated"),
    [
        (0.60, 80.0, True),
        (1.00, 0.0, True),  # a measured score of 0 is evidence: the pillar is measured
        (0.599, 80.0, False),
        (0.60, None, False),
        (1.00, None, False),
        (0.599, None, False),
    ],
)
def test_evidence_gate_needs_the_floor_and_a_measured_responsiveness_pillar(
    coverage: float, responsiveness: float | None, rated: bool
) -> None:
    """RFC-0024 (D-EVID-1): rated only when C >= 0.60 AND the responsiveness
    pillar has a measured parameter -- full coverage elsewhere does not
    stand in for it (D-DEF-2: P(decision) first)."""
    from porchlamp.core import gates

    result = gates.evidence_sufficiency(coverage, _evidence_pillars(responsiveness=responsiveness))
    assert result.eligible is rated
    assert result.reason == (None if rated else "unrated: insufficient_evidence")


def test_required_pillars_are_responsiveness_read_from_the_constants() -> None:
    from porchlamp.core import gates

    assert constants.EVIDENCE_GATE_REQUIRED_PILLARS == ("responsiveness",)
    assert gates.EVIDENCE_GATE_REQUIRED_PILLARS is constants.EVIDENCE_GATE_REQUIRED_PILLARS


def test_unmeasured_required_pillars_names_the_missing_pillar() -> None:
    from porchlamp.core import gates

    assert gates.unmeasured_required_pillars(_evidence_pillars()) == ()
    assert gates.unmeasured_required_pillars(_evidence_pillars(responsiveness=0.0)) == ()
    assert gates.unmeasured_required_pillars(_evidence_pillars(responsiveness=None)) == ("responsiveness",)
    # only the required pillars are read: an unmeasured accessibility pillar is not one
    pillars = _evidence_pillars()
    from porchlamp.core.models import PillarScore

    pillars["accessibility"] = PillarScore(score=None, coverage=0.0)
    assert gates.unmeasured_required_pillars(pillars) == ()


def test_unmeasured_required_pillars_refuses_a_mapping_without_the_pillar() -> None:
    """An absent required pillar is neither measured nor unmeasured: the
    caller never aggregated it, so the gate refuses to guess."""
    from porchlamp.core import gates

    pillars = _evidence_pillars()
    del pillars["responsiveness"]
    with pytest.raises(ValueError, match="responsiveness"):
        gates.unmeasured_required_pillars(pillars)
    with pytest.raises(ValueError, match="responsiveness"):
        gates.evidence_sufficiency(0.90, pillars)


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


def test_rfc0008_rule1_draft_pr_exclusion_and_ready_clock_start() -> None:
    from datetime import timedelta
    from porchlamp.core import gates

    created_iso = (NOW - timedelta(days=30)).isoformat()
    # A draft PR that is never marked ready is excluded
    draft_pr = gates.GhostRatePR(
        created_at=created_iso,
        is_external=True,
        is_bot_author=False,
        first_substantive_response_at=None,
        is_draft=True,
    )
    b_draft = gates.evaluate_gate5_window([draft_pr], NOW)
    assert b_draft.excluded_draft == 1
    assert b_draft.population_n == 0

    # A draft marked ready 10 days ago (20 days after created) has clock_start at ready_at
    ready_iso = (NOW - timedelta(days=20)).isoformat()
    ready_pr = gates.GhostRatePR(
        created_at=created_iso,
        is_external=True,
        is_bot_author=False,
        first_substantive_response_at=None,
        is_draft=True,
        ready_for_review_at=ready_iso,
    )
    b_ready = gates.evaluate_gate5_window([ready_pr], NOW)
    assert b_ready.excluded_draft == 0
    assert b_ready.population_n == 1
    assert b_ready.counted_prs[0].clock_start_at == ready_iso


def test_rfc0008_rule2_author_self_closed_pr_exclusion() -> None:
    from datetime import timedelta
    from porchlamp.core import gates

    created_iso = (NOW - timedelta(days=30)).isoformat()
    closed_iso = (NOW - timedelta(days=28)).isoformat()

    # Author closed PR without any maintainer response -> excluded
    self_closed = gates.GhostRatePR(
        created_at=created_iso,
        is_external=True,
        is_bot_author=False,
        first_substantive_response_at=None,
        closed_at=closed_iso,
        closed_by_author=True,
    )
    b_closed = gates.evaluate_gate5_window([self_closed], NOW)
    assert b_closed.excluded_self_closed == 1
    assert b_closed.population_n == 0

    # Author closed AFTER a substantive maintainer response -> counted (and answered)
    resp_iso = (NOW - timedelta(days=29)).isoformat()
    with_resp = gates.GhostRatePR(
        created_at=created_iso,
        is_external=True,
        is_bot_author=False,
        first_substantive_response_at=resp_iso,
        closed_at=closed_iso,
        closed_by_author=True,
    )
    b_with_resp = gates.evaluate_gate5_window([with_resp], NOW)
    assert b_with_resp.excluded_self_closed == 0
    assert b_with_resp.population_n == 1
    assert b_with_resp.unanswered == 0


def test_rfc0008_rule3_account_age_hygiene() -> None:
    from datetime import timedelta
    from porchlamp.core import gates

    created_dt = NOW - timedelta(days=30)
    # Account created 10 days before PR (< 90 days) -> excluded
    new_acc_iso = (created_dt - timedelta(days=10)).isoformat()
    new_acc_pr = gates.GhostRatePR(
        created_at=created_dt.isoformat(),
        is_external=True,
        is_bot_author=False,
        first_substantive_response_at=None,
        author_created_at=new_acc_iso,
    )
    b_new = gates.evaluate_gate5_window([new_acc_pr], NOW)
    assert b_new.excluded_new_account == 1
    assert b_new.population_n == 0

    # Account created 100 days before PR (>= 90 days) -> counted
    old_acc_iso = (created_dt - timedelta(days=100)).isoformat()
    old_acc_pr = gates.GhostRatePR(
        created_at=created_dt.isoformat(),
        is_external=True,
        is_bot_author=False,
        first_substantive_response_at=None,
        author_created_at=old_acc_iso,
    )
    b_old = gates.evaluate_gate5_window([old_acc_pr], NOW)
    assert b_old.excluded_new_account == 0
    assert b_old.population_n == 1


def test_rfc0008_rule4_maintainer_close_counts_as_response() -> None:
    from datetime import timedelta
    from porchlamp.core import gates

    created_dt = NOW - timedelta(days=30)
    # Maintainer closed within 5 days of opening -> answered
    closed_maint_iso = (created_dt + timedelta(days=5)).isoformat()
    maint_closed_pr = gates.GhostRatePR(
        created_at=created_dt.isoformat(),
        is_external=True,
        is_bot_author=False,
        first_substantive_response_at=None,
        closed_by_maintainer_at=closed_maint_iso,
    )
    b_maint = gates.evaluate_gate5_window([maint_closed_pr], NOW)
    assert b_maint.population_n == 1
    assert b_maint.unanswered == 0
    assert b_maint.counted_prs[0].answered_within_14d is True
    assert b_maint.counted_prs[0].response_kind == "closed_by_maintainer"

    # Maintainer closed after 15 days (> 14 days deadline) -> unanswered
    late_closed_maint_iso = (created_dt + timedelta(days=15)).isoformat()
    late_maint_closed_pr = gates.GhostRatePR(
        created_at=created_dt.isoformat(),
        is_external=True,
        is_bot_author=False,
        first_substantive_response_at=None,
        closed_by_maintainer_at=late_closed_maint_iso,
    )
    b_late = gates.evaluate_gate5_window([late_maint_closed_pr], NOW)
    assert b_late.population_n == 1
    assert b_late.unanswered == 1
    assert b_late.counted_prs[0].answered_within_14d is False
    assert b_late.counted_prs[0].response_kind == "none"


def test_rfc0008_evaluate_gate5_window_breakdown() -> None:
    from datetime import timedelta
    from porchlamp.core import gates

    created_dt = NOW - timedelta(days=30)
    prs = [
        # Counted & answered via review
        gates.GhostRatePR(
            created_at=created_dt.isoformat(),
            is_external=True,
            is_bot_author=False,
            first_substantive_response_at=(created_dt + timedelta(days=1)).isoformat(),
            number=1,
        ),
        # Counted & answered via merge
        gates.GhostRatePR(
            created_at=created_dt.isoformat(),
            is_external=True,
            is_bot_author=False,
            first_substantive_response_at=None,
            merged_at=(created_dt + timedelta(days=2)).isoformat(),
            number=2,
        ),
        # Counted & answered via maintainer close
        gates.GhostRatePR(
            created_at=created_dt.isoformat(),
            is_external=True,
            is_bot_author=False,
            first_substantive_response_at=None,
            closed_by_maintainer_at=(created_dt + timedelta(days=3)).isoformat(),
            number=3,
        ),
        # Counted & unanswered
        gates.GhostRatePR(
            created_at=created_dt.isoformat(),
            is_external=True,
            is_bot_author=False,
            first_substantive_response_at=None,
            number=4,
        ),
        # Excluded draft
        gates.GhostRatePR(
            created_at=created_dt.isoformat(),
            is_external=True,
            is_bot_author=False,
            first_substantive_response_at=None,
            is_draft=True,
            number=5,
        ),
        # Excluded self-closed
        gates.GhostRatePR(
            created_at=created_dt.isoformat(),
            is_external=True,
            is_bot_author=False,
            first_substantive_response_at=None,
            closed_at=(created_dt + timedelta(days=1)).isoformat(),
            closed_by_author=True,
            number=6,
        ),
        # Excluded new account (<90d)
        gates.GhostRatePR(
            created_at=created_dt.isoformat(),
            is_external=True,
            is_bot_author=False,
            first_substantive_response_at=None,
            author_created_at=(created_dt - timedelta(days=20)).isoformat(),
            number=7,
        ),
        # Excluded bot
        gates.GhostRatePR(
            created_at=created_dt.isoformat(),
            is_external=True,
            is_bot_author=True,
            first_substantive_response_at=None,
            number=8,
        ),
        # Excluded internal
        gates.GhostRatePR(
            created_at=created_dt.isoformat(),
            is_external=False,
            is_bot_author=False,
            first_substantive_response_at=None,
            number=9,
        ),
        # Excluded under 14d
        gates.GhostRatePR(
            created_at=(NOW - timedelta(days=5)).isoformat(),
            is_external=True,
            is_bot_author=False,
            first_substantive_response_at=None,
            number=10,
        ),
    ]
    bd = gates.evaluate_gate5_window(prs, NOW)
    assert bd.population_n == 4
    assert bd.unanswered == 1
    assert bd.unanswered_share == 0.25
    assert bd.excluded_draft == 1
    assert bd.excluded_self_closed == 1
    assert bd.excluded_new_account == 1
    assert bd.excluded_bot == 1
    assert bd.excluded_internal == 1
    assert bd.excluded_under_14d == 1
    assert bd.is_black_hole is False



def _window_pr(created: datetime.datetime, **fields: Any) -> Any:
    from porchlamp.core import gates

    return gates.GhostRatePR(
        created_at=created.isoformat(), is_external=True, is_bot_author=False,
        first_substantive_response_at=fields.pop("response_at", None), **fields,
    )


def test_rfc0008_rule1_reply_during_the_draft_period_answers() -> None:
    """Finding 3 (R1 probe_r1b): draft opened 60 d ago, maintainer reply on day
    5, ready on day 13, a second reply on day 15. The PR-node mapping keeps the
    earliest reply (day 5, before the clock start). Lead ruling 2026-10-14: a
    reply made while the PR was still a draft counts, so the PR is answered."""
    from porchlamp.core import gates

    created = NOW - datetime.timedelta(days=60)
    pr = _window_pr(created, is_draft=True,
                    ready_for_review_at=(created + datetime.timedelta(days=13)).isoformat(),
                    response_at=(created + datetime.timedelta(days=5)).isoformat())
    bd = gates.evaluate_gate5_window([pr], NOW)
    assert (bd.unanswered, bd.population_n) == (0, 1)
    assert bd.counted_prs[0].response_kind == "review_or_comment"


def test_rfc0008_rule1_ready_age_boundary_and_min_age_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Finding 6: the 14-d observation age is GATE5_MIN_AGE_DAYS (the value the
    methodology page publishes), not the response deadline. Ready 13 d ago is
    too young, ready 14 d ago is counted; raising the minimum age to 20 moves
    the 14-d PR out while the deadline stays 14."""
    from porchlamp.core import gates

    created = NOW - datetime.timedelta(days=60)
    young = _window_pr(created, ready_for_review_at=(NOW - datetime.timedelta(days=13)).isoformat())
    ripe = _window_pr(created, ready_for_review_at=(NOW - datetime.timedelta(days=14)).isoformat())
    bd = gates.evaluate_gate5_window([young, ripe], NOW)
    assert (bd.population_n, bd.excluded_under_14d) == (1, 1)

    monkeypatch.setattr(gates, "GATE5_MIN_AGE_DAYS", 20)
    bd = gates.evaluate_gate5_window([young, ripe], NOW)
    assert (bd.population_n, bd.excluded_under_14d) == (0, 2)


def test_is_black_hole_counts_needs_ten_prs() -> None:
    """Mutant M2: 9 of 9 unanswered (Wilson bound about 0.77) is below the
    minimum population and is not a black hole; 10 of 10 is."""
    from porchlamp.core import gates

    assert gates.is_black_hole_counts(9, 9) is False
    assert gates.is_black_hole_counts(10, 10) is True


def test_rfc0008_rule3_account_age_boundary() -> None:
    """Mutants M5/M6: an account 89 d old when the PR opened is excluded; 90 d
    and 91 d old are counted (>= MIN_ACCOUNT_AGE_DAYS)."""
    from porchlamp.core import gates

    created = NOW - datetime.timedelta(days=30)
    for age_days, excluded in ((89, 1), (90, 0), (91, 0)):
        pr = _window_pr(created, author_created_at=(created - datetime.timedelta(days=age_days)).isoformat())
        bd = gates.evaluate_gate5_window([pr], NOW)
        assert (bd.excluded_new_account, bd.population_n) == (excluded, 1 - excluded), age_days


def test_rfc0008_rule2_reply_at_exactly_closed_at_keeps_the_pr() -> None:
    """Mutant M10: the author closes at the very instant of a (late) maintainer
    reply. The reply is at-or-before the close, so the PR is not self-closed
    silence: it stays counted, and unanswered (the reply came after 14 d)."""
    from porchlamp.core import gates

    created = NOW - datetime.timedelta(days=60)
    at = (created + datetime.timedelta(days=20)).isoformat()
    pr = _window_pr(created, response_at=at, closed_at=at, closed_by_author=True)
    bd = gates.evaluate_gate5_window([pr], NOW)
    assert (bd.excluded_self_closed, bd.population_n, bd.unanswered) == (0, 1, 1)


def test_rfc0008_rule4_maintainer_close_at_exactly_14_days_answers() -> None:
    """Mutant M12: a maintainer close exactly 14 d after the clock start is
    inside the deadline and answers the PR."""
    from porchlamp.core import gates

    created = NOW - datetime.timedelta(days=60)
    pr = _window_pr(created, closed_by_maintainer_at=(created + datetime.timedelta(days=14)).isoformat())
    bd = gates.evaluate_gate5_window([pr], NOW)
    assert (bd.unanswered, bd.population_n) == (0, 1)
    assert bd.counted_prs[0].response_kind == "closed_by_maintainer"


# --------------------------------------------------------------------------- #
# D-GATE-5 boundary at the engine's z (one-sided 95%, z = 1.6449)              #
# --------------------------------------------------------------------------- #

def test_thirteen_of_twenty_unanswered_is_eligible_because_the_gate_reads_the_lower_bound() -> None:
    """13 of 20 unanswered: the raw share 0.65 is over the 0.50 ceiling, but the
    Wilson lower bound at z = 1.6449 is 0.4665, under it. The gate compares the
    bound, so the repository passes D-GATE-5."""
    from porchlamp.core import gates

    assert constants.GATE5_WILSON_Z == pytest.approx(1.6449, abs=1e-4)
    assert 13 / 20 > constants.GATE5_MAX_ZERO_RESPONSE_SHARE
    assert gates.wilson_lower_bound(13, 20) == pytest.approx(0.4665, abs=1e-4)
    result = gates.evaluate_gates(make_bundle(healthy_events(), ghost=ghost_input(13, 20)), healthy_evidence(), NOW)
    assert (result.eligible, result.reason) == (True, None)
    assert "D-GATE-5" not in result.undecided


def test_fourteen_of_twenty_unanswered_is_a_black_hole() -> None:
    """14 of 20 unanswered: the Wilson lower bound at z = 1.6449 is 0.5162,
    over the 0.50 ceiling, so the repository fails D-GATE-5."""
    from porchlamp.core import gates

    assert gates.wilson_lower_bound(14, 20) == pytest.approx(0.5162, abs=1e-4)
    result = gates.evaluate_gates(make_bundle(healthy_events(), ghost=ghost_input(14, 20)), healthy_evidence(), NOW)
    assert (result.eligible, result.reason) == (False, "black_hole")


# --------------------------------------------------------------------------- #
# D-GATE-5 needs the exclusion rules' inputs to fail a repository             #
# --------------------------------------------------------------------------- #

def _evidence_with_rule_inputs(recorded: bool | None) -> Any:
    import dataclasses

    return dataclasses.replace(healthy_evidence(), pr_rule_inputs_recorded=recorded)


def test_a_repo_whose_pr_records_lack_the_rule_inputs_is_undecided_not_a_black_hole() -> None:
    """14 of 20 unanswered fails the gate when the draft and close-actor fields
    were recorded; the same counts from records without them leave D-GATE-5
    UNDECIDED, and the repository is not failed."""
    from porchlamp.core import gates

    bundle = make_bundle(healthy_events(), ghost=ghost_input(14, 20))
    recorded = gates.evaluate_gates(bundle, _evidence_with_rule_inputs(True), NOW)
    assert (recorded.eligible, recorded.reason) == (False, "black_hole")

    missing = gates.evaluate_gates(bundle, _evidence_with_rule_inputs(False), NOW)
    assert (missing.eligible, missing.reason) == (True, None)
    assert "D-GATE-5" in missing.undecided

    ranked = gates.evaluate_gates(bundle, _evidence_with_rule_inputs(False), NOW, rank_black_holes=True)
    assert "D-GATE-5" in ranked.undecided


def test_missing_rule_inputs_leave_a_pass_undecided_too() -> None:
    """13 of 20 unanswered passes when the rule inputs were recorded; from records without them the
    share is no bound in either direction (an answered draft lowers it), so nothing is decided."""
    from porchlamp.core import gates

    bundle = make_bundle(healthy_events(), ghost=ghost_input(13, 20))
    recorded = gates.evaluate_gates(bundle, _evidence_with_rule_inputs(True), NOW)
    assert (recorded.eligible, recorded.reason, recorded.undecided) == (True, None, ())

    missing = gates.evaluate_gates(bundle, _evidence_with_rule_inputs(False), NOW)
    assert (missing.eligible, missing.reason) == (True, None)
    assert missing.undecided == ("D-GATE-5",)


def test_pr_rule_inputs_recorded_must_be_a_bool_or_none() -> None:
    from porchlamp.core import gates

    with pytest.raises(ValueError, match="pr_rule_inputs_recorded"):
        gates.GateEvidence(manifest_found=True, pr_rule_inputs_recorded="yes")


# --------------------------------------------------------------------------- #
# Author self-close: excluded only within the response deadline               #
# --------------------------------------------------------------------------- #

def _self_closed(created: datetime.datetime, closed_days_after_clock: float, **fields: Any) -> Any:
    clock = datetime.datetime.fromisoformat(fields["ready_for_review_at"]) if "ready_for_review_at" in fields else created
    return _window_pr(created, closed_at=(clock + datetime.timedelta(days=closed_days_after_clock)).isoformat(),
                      closed_by_author=True, **fields)


def test_an_author_self_close_on_day_10_is_excluded_and_on_day_15_counts_as_unanswered() -> None:
    from porchlamp.core import gates

    created = NOW - datetime.timedelta(days=60)
    day_10 = gates.evaluate_gate5_window([_self_closed(created, 10)], NOW)
    assert (day_10.excluded_self_closed, day_10.population_n, day_10.unanswered) == (1, 0, 0)

    day_15 = gates.evaluate_gate5_window([_self_closed(created, 15)], NOW)
    assert (day_15.excluded_self_closed, day_15.population_n, day_15.unanswered) == (0, 1, 1)
    assert day_15.counted_prs[0].response_kind == "none"


def test_a_self_close_exactly_at_the_deadline_is_excluded() -> None:
    """The limit is inclusive, like the response deadline itself."""
    from porchlamp.core import gates

    created = NOW - datetime.timedelta(days=60)
    bd = gates.evaluate_gate5_window([_self_closed(created, constants.GATE5_RESPONSE_DEADLINE_DAYS)], NOW)
    assert (bd.excluded_self_closed, bd.population_n) == (1, 0)


def test_a_former_drafts_self_close_limit_runs_from_ready_for_review() -> None:
    """Opened 40 d before the close, marked ready 30 d before it: the close is
    on day 10 of the clock (excluded); a close 20 d after ready is counted."""
    from porchlamp.core import gates

    created = NOW - datetime.timedelta(days=100)
    ready = (created + datetime.timedelta(days=30)).isoformat()
    early = gates.evaluate_gate5_window([_self_closed(created, 10, is_draft=False, ready_for_review_at=ready)], NOW)
    assert (early.excluded_self_closed, early.population_n) == (1, 0)
    late = gates.evaluate_gate5_window([_self_closed(created, 20, is_draft=False, ready_for_review_at=ready)], NOW)
    assert (late.excluded_self_closed, late.population_n, late.unanswered) == (0, 1, 1)


def test_the_self_close_limit_is_the_response_deadline_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    from porchlamp.core import gates

    created = NOW - datetime.timedelta(days=60)
    monkeypatch.setattr(gates, "GATE5_RESPONSE_DEADLINE_DAYS", 20)
    bd = gates.evaluate_gate5_window([_self_closed(created, 15)], NOW)
    assert (bd.excluded_self_closed, bd.population_n) == (1, 0)


def test_record_cut_short_at_judges_a_truncated_record_against_an_instant() -> None:
    """#66 F1: the cut test against an instant another envelope fixes, not the
    record's own anchor. A truncated history anchored 2026-09-15 whose oldest
    PR opened 2026-03-10 reaches 180 days back from its own anchor (2026-03-19)
    but not the instant 2026-03-05 a ``pr_window`` anchored 2026-09-01 needs."""
    from porchlamp.core import gates

    utc = datetime.timezone.utc
    record = {"window_anchor": "2026-09-15T00:00:00+00:00", "window_days": 365, "truncated": True,
              "prs": [{"number": 2, "createdAt": "2026-09-01T00:00:00+00:00"},
                      {"number": 1, "createdAt": "2026-03-10T00:00:00+00:00"}]}
    assert gates.record_cut_short(record, 180) is False
    assert gates.record_cut_short_at(record, datetime.datetime(2026, 3, 5, tzinfo=utc)) is True
    # The oldest PR opened before the instant: the record holds every PR since.
    assert gates.record_cut_short_at(record, datetime.datetime(2026, 3, 11, tzinfo=utc)) is False
    # An instant with no offset is read as UTC.
    assert gates.record_cut_short_at(record, datetime.datetime(2026, 3, 5)) is True
    # Only a record that says it was truncated is cut; no record is not cut.
    assert gates.record_cut_short_at({**record, "truncated": False}, datetime.datetime(2026, 3, 5, tzinfo=utc)) is False
    assert gates.record_cut_short_at(None, datetime.datetime(2026, 3, 5, tzinfo=utc)) is False
    # A truncated record with no readable createdAt cannot show it reaches the instant.
    assert gates.record_cut_short_at({"truncated": True, "prs": [{"number": 1}]},
                                     datetime.datetime(2026, 3, 5, tzinfo=utc)) is True


@pytest.mark.parametrize("node, outside", [
    ({"authorAssociation": "NONE", "author": {"__typename": "User", "login": "alice"}}, True),
    ({"authorAssociation": "first_time_contributor", "author": {"__typename": "User", "login": "bob"}}, True),
    ({"authorAssociation": "CONTRIBUTOR", "author": None}, True),  # a deleted account is not a bot
    ({"authorAssociation": "MEMBER", "author": {"__typename": "User", "login": "carol"}}, False),
    ({"authorAssociation": "MANNEQUIN", "author": {"__typename": "Mannequin", "login": "old"}}, False),
    ({"author": {"__typename": "User", "login": "dave"}}, False),
    ({"authorAssociation": "CONTRIBUTOR", "author": {"__typename": "Bot", "login": "dependabot"}}, False),
    ({"authorAssociation": "NONE", "author": {"__typename": "User", "login": "acme-ci[bot]"}}, False),
    ({"authorAssociation": "NONE", "author": {"__typename": "User", "login": "K8s-Ci-Robot"}}, False),
    (None, False),
])
def test_is_outside_pr_is_external_association_and_non_bot_author(node: Any, outside: bool) -> None:
    """#57: the one definition of an outside PR, read from the recorded node: an
    external association (case-insensitive) and an author D-IN-1 does not call a
    bot (``__typename`` Bot, a ``[bot]`` suffix, or a ``BOT_LOGINS`` login)."""
    from porchlamp.core import gates

    assert gates.is_outside_pr(node) is outside


# --------------------------------------------------------------------------- #
# RFC-0022: reply lists that stop short of the PR's thread                     #
# --------------------------------------------------------------------------- #

def _reply_list(nodes: list[dict[str, Any]], *, more: bool | None) -> dict[str, Any]:
    """A recorded ``reviews``/``comments`` connection; ``more=None`` records no
    ``pageInfo`` (the query before RFC-0022)."""
    connection: dict[str, Any] = {"nodes": nodes}
    if more is not None:
        connection["pageInfo"] = {"hasNextPage": more, "endCursor": "c1"}
        connection["totalCount"] = len(nodes) + (5 if more else 0)
    return connection


def _member_comment(login: str = "maint", association: str = "MEMBER") -> dict[str, Any]:
    return {"author": {"__typename": "User", "login": login}, "authorAssociation": association,
            "bodyText": "Thanks, this looks right; one question about the cache below.",
            "createdAt": _iso(_ago(40))}


def _bot_comment() -> dict[str, Any]:
    return {"author": {"__typename": "Bot", "login": "ci-bot"}, "authorAssociation": "NONE",
            "bodyText": "CI report: all 412 checks passed on this commit, coverage unchanged.",
            "createdAt": _iso(_ago(41))}


def _outside_pr(number: int, comments: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return {"number": number, "createdAt": _iso(_ago(42)), "authorAssociation": "NONE",
            "author": {"__typename": "User", "login": f"author{number}"},
            "reviews": _reply_list([], more=False), "comments": comments, **fields}


def test_a_reply_list_is_cut_only_when_its_page_info_says_more_remain() -> None:
    from porchlamp.core import gates

    assert gates.reply_connection_cut(_reply_list([], more=True)) is True
    assert gates.reply_connection_cut(_reply_list([], more=False)) is False
    # recorded before RFC-0022: no pageInfo, read as whole as every reader read it
    assert gates.reply_connection_cut(_reply_list([], more=None)) is False
    for odd in (None, [], {"pageInfo": None}, {"pageInfo": {"hasNextPage": "true"}}, {"pageInfo": {}}):
        assert gates.reply_connection_cut(odd) is False, odd
    whole = _outside_pr(1, _reply_list([], more=False))
    assert gates.replies_cut(whole) is False
    assert gates.replies_cut({**whole, "reviews": _reply_list([], more=True)}) is True
    assert gates.replies_cut({**whole, "comments": _reply_list([], more=True)}) is True


def test_a_cut_pr_without_a_recorded_response_leaves_the_reply_set() -> None:
    """Twenty bot comments recorded, more on GitHub, no maintainer reply among them:
    no reply measure may read the PR as unanswered. A cut PR whose recorded part
    holds a maintainer's reply stays, and so do whole and legacy nodes."""
    from porchlamp.core import gates

    silent_cut = _outside_pr(1, _reply_list([_bot_comment()] * 20, more=True))
    answered_cut = _outside_pr(2, _reply_list([_bot_comment()] * 19 + [_member_comment()], more=True))
    silent_whole = _outside_pr(3, _reply_list([_bot_comment()] * 3, more=False))
    silent_legacy = _outside_pr(4, _reply_list([_bot_comment()] * 20, more=None))
    unsampled = {**_outside_pr(5, _reply_list([], more=False)), gates.REPLIES_RECORDED_KEY: False}
    prs = [silent_cut, answered_cut, silent_whole, silent_legacy, unsampled]
    assert [pr["number"] for pr in gates.reply_sampled_prs(prs)] == [2, 3, 4]
    assert [pr["number"] for pr in gates.reply_sampled_prs(iter(prs))] == [2, 3, 4]


def test_a_cut_prs_reply_counts_by_the_windows_d_in_2_maintainers() -> None:
    """The reply on the cut PR is by a CONTRIBUTOR who merged another PR in the
    window: D-IN-2 counts it (``maintainer_logins`` over every node, sampled or
    not), so the PR stays; without that merge it is no response and the PR leaves."""
    from porchlamp.core import gates

    cut = _outside_pr(1, _reply_list([_member_comment("committer", "CONTRIBUTOR")], more=True))
    merged_elsewhere = {**_outside_pr(9, _reply_list([], more=False)), "mergedAt": _iso(_ago(30)),
                        "mergedBy": {"login": "committer"}, gates.REPLIES_RECORDED_KEY: False}
    assert [pr["number"] for pr in gates.reply_sampled_prs([cut, merged_elsewhere])] == [1]
    assert gates.reply_sampled_prs([cut]) == []
