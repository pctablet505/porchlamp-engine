"""D-GATE-5 separates a project that ignores outside PRs from one that answers late (RFC-0026).

A black hole is a project whose outside PRs no maintainer ever touched. A project that replies, merges or closes after
the 14-day deadline is slow: its 1.4 GR_14 scores that, and it stays rankable.
"""
from __future__ import annotations

import dataclasses
from datetime import timedelta
from pathlib import Path

import pytest

from porchlamp.collect.envelope import write_envelope
from porchlamp.core import gates
from porchlamp.derive import gate_evidence
from porchlamp.core.engine import Snapshot, score
from tests.core.test_gates import NOW, _ago, _iso, ghost_input, ghost_pr, healthy_events, healthy_evidence, make_bundle
from tests.test_universe_pruning_and_blackholes import _DummyPopulations, _eligible_evidence, _make_bundle


def _breakdown(prs):
    return gates.evaluate_gate5_window(prs, NOW)


def _late_close(days_ago: float, after: float) -> gates.GhostRatePR:
    created = _ago(days_ago)
    return gates.GhostRatePR(
        created_at=_iso(created), is_external=True, is_bot_author=False, first_substantive_response_at=None,
        closed_at=_iso(created + timedelta(days=after)), closed_by_maintainer_at=_iso(created + timedelta(days=after)))


def _author_gave_up(days_ago: float, after: float) -> gates.GhostRatePR:
    created = _ago(days_ago)
    return gates.GhostRatePR(
        created_at=_iso(created), is_external=True, is_bot_author=False, first_substantive_response_at=None,
        closed_at=_iso(created + timedelta(days=after)), closed_by_author=True)


def test_prs_nobody_ever_touched_are_ignored_and_make_a_black_hole():
    b = _breakdown([ghost_pr(60) for _ in range(12)])
    assert (b.unanswered, b.ignored, b.population_n) == (12, 12, 12)
    assert b.is_black_hole is True and b.ignored_wilson_lower_bound > 0.5


@pytest.mark.parametrize("make", [
    lambda: ghost_pr(60, response_days_after=20),     # replied after the deadline
    lambda: ghost_pr(60, merged_days_after=30),       # merged after the deadline, no reply
    lambda: _late_close(60, 25),                      # a maintainer closed it after the deadline
], ids=["late reply", "late merge", "late maintainer close"])
def test_prs_a_maintainer_touched_late_are_slow_not_ignored(make):
    b = _breakdown([make() for _ in range(12)])
    assert (b.unanswered, b.ignored) == (12, 0)
    assert b.is_black_hole is False
    assert b.wilson_lower_bound > 0.5    # the 14-day unanswered share alone would have called it a black hole


def test_a_pr_the_author_gave_up_on_after_the_deadline_with_no_maintainer_action_is_ignored():
    b = _breakdown([_author_gave_up(60, 30) for _ in range(12)])
    assert (b.unanswered, b.ignored, b.is_black_hole) == (12, 12, True)


def test_a_pr_answered_in_time_is_neither_unanswered_nor_ignored():
    b = _breakdown([ghost_pr(60, response_days_after=2) for _ in range(12)])
    assert (b.unanswered, b.ignored, b.is_black_hole) == (0, 0, False)


def test_the_threshold_is_the_wilson_bound_of_the_ignored_share():
    mostly = [ghost_pr(60) for _ in range(18)] + [ghost_pr(60, response_days_after=20) for _ in range(2)]
    assert _breakdown(mostly).is_black_hole is True                    # 18 of 20
    barely = [ghost_pr(60) for _ in range(12)] + [ghost_pr(60, response_days_after=20) for _ in range(8)]
    b = _breakdown(barely)
    assert b.unanswered == 20 and b.ignored == 12 and b.is_black_hole is False    # 12 of 20: bound below one half


def test_ten_ignored_prs_are_the_minimum_to_judge():
    assert _breakdown([ghost_pr(60) for _ in range(10)]).is_black_hole is True
    assert _breakdown([ghost_pr(60) for _ in range(9)]).is_black_hole is False


def test_each_counted_pr_says_whether_a_maintainer_ever_touched_it():
    b = _breakdown([ghost_pr(60), ghost_pr(60, response_days_after=20), ghost_pr(60, response_days_after=2)])
    assert [pr.touched_ever for pr in b.counted_prs] == [False, True, True]


# --------------------------------------------------------------------------- #
# evaluate_gates reads the counts from the evidence                           #
# --------------------------------------------------------------------------- #

def _gate(unanswered_n, ignored, n, *, evidence=True):
    base = healthy_evidence()
    if evidence:
        base = dataclasses.replace(base, pr_ignored=ignored, pr_ignored_n=n)
    return gates.evaluate_gates(make_bundle(healthy_events(), ghost=ghost_input(unanswered_n, n)), base, NOW)


def test_a_slow_project_passes_the_gate_though_most_prs_waited_over_14_days():
    result = _gate(18, 2, 20)
    assert result.eligible is True and result.reason is None


def test_an_ignoring_project_fails_the_gate():
    result = _gate(18, 18, 20)
    assert result.eligible is False and result.reason == "black_hole"


def test_without_the_counts_the_gate_judges_the_unanswered_share_as_before():
    assert _gate(18, 0, 20, evidence=False).reason == "black_hole"
    assert _gate(6, 0, 10, evidence=False).eligible is True


def test_ranking_black_holes_mode_still_lets_the_gate_pass():
    result = gates.evaluate_gates(
        make_bundle(healthy_events(), ghost=ghost_input(18, 20)),
        dataclasses.replace(healthy_evidence(), pr_ignored=18, pr_ignored_n=20), NOW, rank_black_holes=True)
    assert result.eligible is True


def test_the_bundle_only_test_uses_the_evidence_when_it_has_the_counts():
    bundle = make_bundle(healthy_events(), ghost=ghost_input(18, 20))
    assert gates.is_black_hole_bundle(bundle) is True
    assert gates.is_black_hole_bundle(bundle, dataclasses.replace(healthy_evidence(), pr_ignored=2, pr_ignored_n=20)) is False
    assert gates.is_black_hole_bundle(bundle, dataclasses.replace(healthy_evidence(), pr_ignored=18, pr_ignored_n=20)) is True


@pytest.mark.parametrize("ignored,flagged", [(12, True), (2, False)])
def test_the_ranking_mode_flags_only_a_project_that_ignores_outside_prs(ignored, flagged):
    # 80% of 12 PRs unanswered in 14 days either way; the engine flags it "black_hole" only when they were ignored
    bundle = _make_bundle(owner="acme", name="lib", ghost_rate=0.80, ghost_n=12)
    evidence = dataclasses.replace(_eligible_evidence(), pr_ignored=ignored, pr_ignored_n=12)
    rec = score(bundle, _DummyPopulations(), Snapshot(snapshot_id="s", as_of=NOW, gate_evidence=evidence, rank_black_holes=True))
    assert rec.suspicion == (["black_hole"] if flagged else [])


@pytest.mark.parametrize("fields", [
    {"pr_ignored": 3}, {"pr_ignored_n": 10}, {"pr_ignored": 11, "pr_ignored_n": 10},
    {"pr_ignored": -1, "pr_ignored_n": 10}, {"pr_ignored": True, "pr_ignored_n": 10},
])
def test_counts_that_cannot_be_right_are_refused(fields):
    with pytest.raises(ValueError):
        dataclasses.replace(healthy_evidence(), **fields)


# --------------------------------------------------------------------------- #
# from the raw envelope                                                       #
# --------------------------------------------------------------------------- #

def _node(number: int, *, merged_days: float | None = None) -> dict:
    created = _ago(60)
    node = {"number": number, "createdAt": _iso(created), "closedAt": None, "mergedAt": None, "state": "OPEN",
            "isDraft": False, "mergedBy": None,
            "author": {"__typename": "User", "login": f"outsider{number}", "createdAt": "2019-01-01T00:00:00Z"},
            "authorAssociation": "CONTRIBUTOR", "timelineItems": {"nodes": []},
            "comments": {"nodes": []}, "reviews": {"nodes": []}, "reviewThreads": {"totalCount": 0}}
    if merged_days is not None:
        when = _iso(created + timedelta(days=merged_days))
        node.update(mergedAt=when, closedAt=when, state="MERGED", mergedBy={"login": "maintainer"})
    return node


def _envelope(tmp: Path, nodes: list[dict]) -> None:
    write_envelope(tmp / "pr_window" / "org__repo.json", source="test", fetched_at=_iso(NOW), data={
        "window_anchor": _iso(NOW), "window_days": 180, "truncated": False, "count": len(nodes), "prs": nodes})


def test_the_evidence_built_from_the_raw_window_carries_the_ignored_counts(tmp_path):
    _envelope(tmp_path, [_node(i) for i in range(1, 13)])
    ev = gate_evidence.gate_evidence_for(tmp_path, "org__repo", as_of=NOW)
    assert (ev.pr_ignored, ev.pr_ignored_n) == (12, 12)


def test_a_window_whose_prs_were_merged_late_counts_none_as_ignored(tmp_path):
    _envelope(tmp_path, [_node(i, merged_days=30) for i in range(1, 13)])
    ev = gate_evidence.gate_evidence_for(tmp_path, "org__repo", as_of=NOW)
    assert (ev.pr_ignored, ev.pr_ignored_n) == (0, 12)


def test_without_a_reference_time_or_a_window_the_evidence_has_no_counts(tmp_path):
    _envelope(tmp_path, [_node(1)])
    assert gate_evidence.gate_evidence_for(tmp_path, "org__repo").pr_ignored is None
    assert gate_evidence.gate_evidence_for(tmp_path, "org__nothing", as_of=NOW).pr_ignored is None
