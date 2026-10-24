"""A project with fewer than three outside PRs in 180 days is not a place to contribute (RFC-0027).

D-GATE-6 asked for one decided outside PR in 365 days. A repository whose complete PR window holds only one or two
outside PRs has no workflow worth measuring: a rank over so little would mostly be about those few accounts.
"""
from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest

from porchlamp.core import constants, gates
from porchlamp.derive import gate_evidence
from tests.core.test_gate5_ignored import _envelope, _node
from tests.core.test_gates import NOW, HEALTHY_GHOST, healthy_events, healthy_evidence, make_bundle


def _gate(outside, *, complete=True, marker=False, decided=True, handoff=None, events=None):
    evidence = dataclasses.replace(
        healthy_evidence(), outside_prs_180d=outside, pr_window_complete=complete,
        off_platform_marker=marker, decided_external_pr=decided, review_handoff=handoff)
    bundle = make_bundle(healthy_events() if events is None else events, ghost=HEALTHY_GHOST)
    return gates.evaluate_gates(bundle, evidence, NOW)


def test_the_floor_is_three_outside_prs_a_constant_of_the_methodology():
    assert constants.GATE6_MIN_OUTSIDE_PRS == 3
    assert gates.GATE6_MIN_OUTSIDE_PRS == constants.GATE6_MIN_OUTSIDE_PRS
    assert constants.constants_dict()["GATE6_MIN_OUTSIDE_PRS"] == 3


@pytest.mark.parametrize("outside", [0, 1, 2])
def test_fewer_than_three_outside_prs_is_unrated_though_one_was_decided(outside):
    result = _gate(outside)
    assert (result.eligible, result.reason, result.routed) == (False, "unrated: no_external_workflow", None)


def test_three_outside_prs_is_enough():
    result = _gate(3)
    assert (result.eligible, result.reason, result.undecided) == (True, None, ())


def test_without_a_count_the_rule_does_not_apply():
    assert _gate(None).eligible is True


@pytest.mark.parametrize("complete", [False, None])
def test_a_window_that_stops_short_proves_nothing_about_a_thin_count(complete):
    # the count is only a lower bound when the record does not reach back to the window's start
    assert _gate(1, complete=complete).eligible is True


def test_a_thin_project_that_works_off_platform_is_routed_not_failed():
    result = _gate(1, marker=True)
    assert (result.eligible, result.reason, result.routed) == (True, None, gates.ROUTED_OFF_PLATFORM)


def test_a_thin_project_whose_off_platform_workflow_is_unknown_is_undecided_not_failed():
    result = _gate(1, marker=None)
    assert (result.eligible, result.reason, result.undecided) == (True, None, ("D-GATE-6",))


def test_a_thin_project_that_hands_review_off_is_routed():
    result = _gate(0, handoff=True)
    assert (result.eligible, result.routed) == (True, gates.ROUTED_OFF_PLATFORM)


def test_an_unknown_decided_flag_does_not_save_a_thin_project():
    # decided_external_pr None used to leave D-GATE-6 undecided; with a complete thin window it is decided
    assert _gate(2, decided=None).reason == "unrated: no_external_workflow"


def test_the_older_rule_is_untouched_for_a_project_with_enough_outside_prs():
    # five outside PRs, none decided: the decided flag still fails or leaves undecided exactly as before RFC-0027
    assert _gate(5, decided=False, events=[]).reason == "unrated: no_external_workflow"
    assert _gate(5, decided=None, events=[]).undecided == ("D-GATE-6",)


def test_a_lower_gate_failure_still_wins_over_the_thin_floor():
    evidence = dataclasses.replace(healthy_evidence(), outside_prs_180d=0, pr_window_complete=True, vitality_commits_180d=0)
    result = gates.evaluate_gates(make_bundle(healthy_events(), ghost=HEALTHY_GHOST), evidence, NOW)
    assert result.reason == "stale"


@pytest.mark.parametrize("value", [-1, True, 2.5, "3"])
def test_a_count_that_cannot_be_right_is_refused(value):
    with pytest.raises(ValueError):
        dataclasses.replace(healthy_evidence(), outside_prs_180d=value)


# --------------------------------------------------------------------------- #
# the count from the raw envelope                                             #
# --------------------------------------------------------------------------- #

def _count(tmp_path, nodes, *, as_of=NOW):
    _envelope(tmp_path, nodes)
    return gate_evidence.gate_evidence_for(tmp_path, "org__repo", as_of=as_of).outside_prs_180d


def test_the_count_is_the_outside_prs_opened_in_the_window(tmp_path):
    assert _count(tmp_path, [_node(i) for i in range(1, 3)]) == 2


def test_insiders_and_bots_are_not_outside_prs(tmp_path):
    nodes = [_node(1), _node(2), _node(3), _node(4)]
    nodes[1]["authorAssociation"] = "MEMBER"
    nodes[2]["author"]["__typename"] = "Bot"
    nodes[3]["author"]["login"] = "dependabot[bot]"
    assert _count(tmp_path, nodes) == 1


def test_a_pr_opened_before_the_window_is_not_counted(tmp_path):
    old = _node(1)
    old["createdAt"] = (NOW - timedelta(days=constants.PR_WINDOW_DAYS + 5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert _count(tmp_path, [old, _node(2)]) == 1


def test_a_closed_or_merged_pr_counts_as_much_as_an_open_one(tmp_path):
    assert _count(tmp_path, [_node(1), _node(2, merged_days=2), _node(3, merged_days=30)]) == 3


def test_without_a_reference_time_or_a_window_there_is_no_count(tmp_path):
    _envelope(tmp_path, [_node(1)])
    assert gate_evidence.gate_evidence_for(tmp_path, "org__repo").outside_prs_180d is None
    assert gate_evidence.gate_evidence_for(tmp_path, "org__nothing", as_of=NOW).outside_prs_180d is None


def test_the_window_record_that_is_complete_makes_the_count_decisive(tmp_path):
    _envelope(tmp_path, [_node(1), _node(2)])
    evidence = gate_evidence.gate_evidence_for(tmp_path, "org__repo", as_of=NOW)
    assert (evidence.outside_prs_180d, evidence.pr_window_complete) == (2, True)
