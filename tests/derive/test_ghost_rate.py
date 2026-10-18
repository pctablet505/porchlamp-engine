"""1.4 GR_14 and D-GATE-5 are one measurement, per the spec (owner ruling
2026-10-06).

``scripts/derive.py`` computes the zero-response share with
``porchlamp.core.gates.zero_response_counts`` from the ``pr_window`` envelope
at its own pinned ``now``; ``porchlamp.derive.bundle`` carries it as the
``gr14_ghost_rate`` input; ``evaluate_gates`` reads that input for D-GATE-5.

Spec (02 section 4 1.4, section 2 D-GATE-5, section 3 D-IN-1/D-IN-2):
external PRs opened in the trailing 180 d, bots excluded, at least 14 d old;
unanswered = no review (any state) or >= 40-character comment by a
non-author, non-bot MEMBER/OWNER/COLLABORATOR within 14 d of opening.

Owner rulings 2026-10-06 on top of the spec text: a merge within 14 d of
opening is a response; the 14-d age is judged at min(now, the envelope's
fetch time), so a PR observed for under 14 d is not in the population; an
account that merged a PR in the window responds like a maintainer whatever
its association.

Every expected count below is by hand from the fixture, not from the code
under test.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from _fixtures import load

from porchlamp.core import gates
from porchlamp.core.models import Measured, Unmeasured
from porchlamp.core.params import gr14

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("derive_gr14", ROOT / "scripts" / "derive.py")
derive = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(derive)

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)
OWNER, NAME, KEY = "acme", "widget", "acme__widget"
FETCHED_AT = "2026-09-11T00:00:00+00:00"
MAINTAINER = {"__typename": "User", "login": "maint"}


def _at(days_ago: float, plus_days: float = 0.0) -> str:
    return (NOW - timedelta(days=days_ago) + timedelta(days=plus_days)).isoformat()


def _review(days_ago: float, after: float, *, login: str = "maint", typename: str = "User",
            assoc: str = "MEMBER") -> dict[str, Any]:
    return {"author": {"__typename": typename, "login": login}, "state": "COMMENTED", "body": "",
            "createdAt": _at(days_ago, after), "authorAssociation": assoc, "comments": {"totalCount": 0}}


def _comment(days_ago: float, after: float, text: str, assoc: str = "MEMBER") -> dict[str, Any]:
    return {"author": MAINTAINER, "authorAssociation": assoc, "bodyText": text,
            "createdAt": _at(days_ago, after)}


def _pr(number: int, days_ago: float, *, author: str = "outsider", typename: str = "User",
        assoc: str = "NONE", merged_after: float | None = None,
        reviews: tuple = (), comments: tuple = ()) -> dict[str, Any]:
    merged = _at(days_ago, merged_after) if merged_after is not None else None
    return {
        "number": number, "createdAt": _at(days_ago), "closedAt": merged, "mergedAt": merged,
        "state": "MERGED" if merged else "OPEN",
        "author": {"__typename": typename, "login": author}, "authorAssociation": assoc,
        "reviews": {"nodes": list(reviews)}, "comments": {"nodes": list(comments)},
        "reviewThreads": {"totalCount": 0},
    }


def counted_prs() -> list[dict[str, Any]]:
    """11 PRs in the population: 6 answered, 5 unanswered."""
    d = 30
    return [
        # answered (6)
        *[_pr(1 + i, d, reviews=(_review(d, 1),)) for i in range(4)],
        _pr(5, d, comments=(_comment(d, 2, "x" * 40),)),            # 40-char comment
        _pr(6, d, reviews=(_review(d, 14),)),                       # exactly at the deadline
        # unanswered (5)
        _pr(7, d, comments=(_comment(d, 2, "y" * 39),)),            # 39 chars is not substantive
        _pr(8, d, reviews=(_review(d, 1, login="ci-helper", typename="Bot"),)),  # bot reviewer
        _pr(9, d, reviews=(_review(d, 1, login="outsider", assoc="COLLABORATOR"),)),  # self-review
        _pr(10, d, merged_after=15),                                # merged after the deadline
        _pr(11, d, reviews=(_review(d, 15),)),                      # review after the deadline
    ]


def excluded_prs() -> list[dict[str, Any]]:
    """Unanswered PRs the population must not contain."""
    return [
        *[_pr(20 + i, 30, author="dependabot", typename="Bot") for i in range(3)],  # bot authors
        *[_pr(30 + i, 13.5) for i in range(2)],                     # younger than 14 d at NOW
        _pr(40, 181),                                               # outside the 180-d window
        _pr(41, 30, author="insider", assoc="MEMBER"),              # not external
    ]


def _write_window(tmp_path: Path, prs: list[dict[str, Any]], fetched_at: str = FETCHED_AT) -> None:
    from porchlamp.collect import github_graphql

    data = {"window_anchor": fetched_at, "window_days": 180, "cap": 500, "count": len(prs),
            "truncated": False, "cost_points": 1, "prs": prs,
            "derived": github_graphql.derive_pr_window(prs)}
    path = tmp_path / "raw" / "pr_window" / f"{KEY}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fetched_at": fetched_at, "source": "test", "data": data}), encoding="utf-8")


def _derive(tmp_path: Path, prs: list[dict[str, Any]], now: datetime = NOW, fetched_at: str = FETCHED_AT):
    _write_window(tmp_path, prs, fetched_at)
    doc = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path, fetch=False, now=now)
    return doc, load(tmp_path, json.loads(json.dumps(doc)))


def _evidence() -> gates.GateEvidence:
    return gates.GateEvidence(manifest_found=True, license_file_present=True,
                              vitality_commits_180d=3, off_platform_marker=True)


def test_measured_share_and_n_follow_the_spec(tmp_path: Path) -> None:
    doc, bundle = _derive(tmp_path, counted_prs() + excluded_prs())
    assert doc["meta"]["gr14_population_n"] == 11
    value = bundle.inputs["gr14_ghost_rate"]
    assert value == Measured(
        value=5 / 11, n=11, window_start=(NOW - timedelta(days=180)).isoformat(),
        window_end=NOW.isoformat(), method="primary", source=f"raw/pr_window/{KEY}.json",
        source_hash=value.source_hash, fetched_at=FETCHED_AT,
    )
    scored = gr14(value)
    assert scored.measured is True and scored.n == 11
    assert abs(scored.score - (100 - 200 * 5 / 11)) < 1e-9


def test_gate_reads_the_same_share_and_fires_above_half(tmp_path: Path) -> None:
    """5/11 passes D-GATE-5; nine more unanswered PRs make 14/20 > 0.50 (with Wilson
    lower bound > 0.50) and the same input gates the repo as a black hole."""
    _, passing = _derive(tmp_path / "pass", counted_prs() + excluded_prs())
    result = gates.evaluate_gates(passing, _evidence(), NOW)
    assert gates.GATE_5 not in result.undecided and result.reason is None

    extra = [_pr(50 + i, 20) for i in range(9)]
    _, failing = _derive(tmp_path / "fail", counted_prs() + excluded_prs() + extra)
    ghost = failing.inputs["gr14_ghost_rate"]
    assert (ghost.value, ghost.n) == (14 / 20, 20)
    assert gates.wilson_lower_bound(14, 20) > 0.50
    result = gates.evaluate_gates(failing, _evidence(), NOW)
    assert result.reason == gates.REASON_BLACK_HOLE


def test_ghost_rate_equals_the_gate_share(tmp_path: Path) -> None:
    """The input the gate reads is the zero-response share of the gate's own
    function over the same records: one measurement."""
    from porchlamp.collect import github_graphql

    prs = counted_prs() + excluded_prs()
    _, bundle = _derive(tmp_path, prs)
    records = github_graphql.ghost_rate_prs_from_window(prs)
    unanswered, n = gates.zero_response_counts(records, NOW)
    ghost = bundle.inputs["gr14_ghost_rate"]
    assert (ghost.value, ghost.n) == (unanswered / n, n) == (5 / 11, 11)


def test_reference_time_is_the_pinned_now_not_the_wall_clock(tmp_path: Path) -> None:
    """The two 13.5-day-old PRs are excluded at NOW; read against the wall
    clock (weeks later) they would be in. A later pinned `now`, with an
    envelope fetched at that later time, admits them."""
    doc, bundle = _derive(tmp_path / "now", counted_prs() + excluded_prs())
    assert doc["meta"]["gr14_window_end"] == NOW.isoformat()
    assert bundle.inputs["gr14_ghost_rate"].n == 11
    later = NOW + timedelta(days=1)
    _, later_bundle = _derive(tmp_path / "later", counted_prs() + excluded_prs(), now=later,
                              fetched_at=later.isoformat())
    assert later_bundle.inputs["gr14_ghost_rate"].n == 13


def test_merge_within_14_days_answers_through_derive(tmp_path: Path) -> None:
    """Owner ruling 2026-10-06. Ten unanswered PRs plus two merged with no
    review or comment: merged on day 10 -> answered (10/12 unanswered);
    merged on day 15 -> unanswered (12/12). derive must carry mergedAt into
    the measurement, not only the D-IN-2 response time."""
    base = [_pr(70 + i, 40) for i in range(10)]
    _, day10 = _derive(tmp_path / "d10", base + [_pr(80, 40, merged_after=10), _pr(81, 40, merged_after=10)])
    _, day15 = _derive(tmp_path / "d15", base + [_pr(80, 40, merged_after=15), _pr(81, 40, merged_after=15)])
    assert (day10.inputs["gr14_ghost_rate"].value, day10.inputs["gr14_ghost_rate"].n) == (10 / 12, 12)
    assert (day15.inputs["gr14_ghost_rate"].value, day15.inputs["gr14_ghost_rate"].n) == (12 / 12, 12)


def test_population_needs_14_days_of_observation_through_derive(tmp_path: Path) -> None:
    """Owner/lead ruling 2026-10-06. Twelve unanswered PRs 20 d old at NOW
    plus ten answered PRs 40 d old. Envelope fetched 10 d before NOW: the
    twelve were observed for 10 d and stay out (0/10). Fetched 6 d before
    NOW: observed for 14 d, they are in (12/22)."""
    prs = [_pr(90 + i, 20) for i in range(12)] + [_pr(110 + i, 40, reviews=(_review(40, 1),)) for i in range(10)]
    _, short = _derive(tmp_path / "short", prs, fetched_at=_at(10))
    _, full = _derive(tmp_path / "full", prs, fetched_at=_at(6))
    assert (short.inputs["gr14_ghost_rate"].value, short.inputs["gr14_ghost_rate"].n) == (0.0, 10)
    assert (full.inputs["gr14_ghost_rate"].value, full.inputs["gr14_ghost_rate"].n) == (12 / 22, 22)


def test_merger_comment_answers_through_derive(tmp_path: Path) -> None:
    """Owner ruling 2026-10-06: a CONTRIBUTOR-association account that merged
    a PR in the window responds like a maintainer. Twelve PRs with a
    40-character CONTRIBUTOR comment by `carol` are unanswered while carol
    merged nothing (12/12) and answered once she merged a PR (0/12; the
    merged PR itself is internal-authored and not in the population)."""
    commented = [_pr(130 + i, 30, comments=(_comment(30, 1, "c" * 40, assoc="CONTRIBUTOR"),))
                 for i in range(12)]
    for pr in commented:
        pr["comments"]["nodes"][0]["author"] = {"__typename": "User", "login": "carol"}
    merged_by_other = _pr(150, 60, author="insider", assoc="MEMBER", merged_after=1)
    merged_by_other["mergedBy"] = {"login": "maint"}
    merged_by_carol = _pr(150, 60, author="insider", assoc="MEMBER", merged_after=1)
    merged_by_carol["mergedBy"] = {"login": "carol"}
    _, without = _derive(tmp_path / "without", commented + [merged_by_other])
    _, with_carol = _derive(tmp_path / "with", commented + [merged_by_carol])
    assert (without.inputs["gr14_ghost_rate"].value, without.inputs["gr14_ghost_rate"].n) == (1.0, 12)
    assert (with_carol.inputs["gr14_ghost_rate"].value, with_carol.inputs["gr14_ghost_rate"].n) == (0.0, 12)


def test_below_min_n_is_insufficient_n_with_the_true_n_and_gate_undecided(tmp_path: Path) -> None:
    prs = counted_prs()[:9] + excluded_prs()
    _, bundle = _derive(tmp_path, prs)
    value = bundle.inputs["gr14_ghost_rate"]
    assert isinstance(value, Measured) and value.n == 9
    scored = gr14(value)
    assert scored.measured is False and scored.reason == "insufficient_n" and scored.n == 9
    result = gates.evaluate_gates(bundle, _evidence(), NOW)
    assert gates.GATE_5 in result.undecided and result.reason is None


def test_bot_prs_alone_never_measure(tmp_path: Path) -> None:
    """Twelve unanswered bot-authored PRs: n = 0, no share, gate UNDECIDED."""
    prs = [_pr(60 + i, 30, author="renovate[bot]", typename="Bot") for i in range(12)]
    _, bundle = _derive(tmp_path, prs)
    assert bundle.inputs["gr14_ghost_rate"] == Unmeasured(
        reason="insufficient_n", detail="0 external non-bot PRs >= 14 d old in the window, need >= 10")
    assert gates.GATE_5 in gates.evaluate_gates(bundle, _evidence(), NOW).undecided


def _write_cut_window(tmp_path: Path, prs: list[dict[str, Any]], fetched_at: str) -> None:
    from porchlamp.collect import github_graphql

    data = {"window_anchor": fetched_at, "window_days": 180, "cap": 500, "count": len(prs),
            "truncated": True, "cost_points": 1, "prs": prs, "derived": github_graphql.derive_pr_window(prs)}
    path = tmp_path / "raw" / "pr_window" / f"{KEY}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fetched_at": fetched_at, "source": "test", "data": data}), encoding="utf-8")


def test_a_record_cut_short_of_the_window_gives_no_ghost_rate(tmp_path: Path) -> None:
    """#51 rule 5: cut at its cap 30 days back, the record does not reach the window's start, so derive records no
    share and the bundle refuses it."""
    _write_cut_window(tmp_path, counted_prs(), FETCHED_AT)
    doc = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path, fetch=False, now=NOW)
    assert doc["confidence"]["gr14_population_n"] == "missing"
    value = load(tmp_path, json.loads(json.dumps(doc))).inputs["gr14_ghost_rate"]
    assert isinstance(value, Unmeasured) and value.reason == "collector_failure"


def test_a_truncated_record_that_reaches_the_gr14_window_start_is_measured(tmp_path: Path) -> None:
    """Fetched ten days before derive and cut 185 days before it: short of its own window, but it holds every PR of
    the window GR_14 is computed over (``gr14_window_start``), so derive and the bundle both measure it."""
    old = _pr(99, 185)
    _write_cut_window(tmp_path, counted_prs() + [old], _at(10))
    doc = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path, fetch=False, now=NOW)
    assert doc["confidence"]["gr14_population_n"] == "derived" and doc["meta"]["gr14_population_n"] == 11
    value = load(tmp_path, json.loads(json.dumps(doc))).inputs["gr14_ghost_rate"]
    assert isinstance(value, Measured) and value.n == 11


def test_no_pr_window_envelope_is_collector_failure(tmp_path: Path) -> None:
    doc = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path, fetch=False, now=NOW)
    assert doc["confidence"]["gr14_population_n"] == "missing"
    bundle = load(tmp_path, json.loads(json.dumps(doc)))
    value = bundle.inputs["gr14_ghost_rate"]
    assert isinstance(value, Unmeasured) and value.reason == "collector_failure"
