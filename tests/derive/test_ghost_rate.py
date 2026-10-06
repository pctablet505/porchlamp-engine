"""1.4 GR_14 and D-GATE-5 are one measurement, per the spec (owner ruling
2026-10-06).

``scripts/derive.py`` computes the zero-response share with
``porchlamp.core.gates.zero_response_counts`` from the ``pr_window`` envelope
at its own pinned ``now``; ``porchlamp.derive.bundle`` carries it as the
``gr14_ghost_rate`` input; ``evaluate_gates`` reads that input for D-GATE-5.

Spec (02 section 4 1.4, section 2 D-GATE-5, section 3 D-IN-1/D-IN-2):
external PRs opened in the trailing 180 d, bots excluded, at least 14 d old;
unanswered = no review (any state) or >= 40-character comment by a
non-author, non-bot MEMBER/OWNER/COLLABORATOR within 14 d of opening. A
merge is not a response (D-IN-2 does not list it).

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
        _pr(10, d, merged_after=1),                                 # merged, never answered
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


def _write_window(tmp_path: Path, prs: list[dict[str, Any]]) -> None:
    from porchlamp.collect import github_graphql

    data = {"window_anchor": FETCHED_AT, "window_days": 180, "cap": 500, "count": len(prs),
            "truncated": False, "cost_points": 1, "prs": prs,
            "derived": github_graphql.derive_pr_window(prs)}
    path = tmp_path / "raw" / "pr_window" / f"{KEY}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fetched_at": FETCHED_AT, "source": "test", "data": data}), encoding="utf-8")


def _derive(tmp_path: Path, prs: list[dict[str, Any]], now: datetime = NOW):
    _write_window(tmp_path, prs)
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
    """5/11 passes D-GATE-5; two more unanswered PRs make 7/13 > 0.50 and the
    same input gates the repo as a black hole."""
    _, passing = _derive(tmp_path / "pass", counted_prs() + excluded_prs())
    result = gates.evaluate_gates(passing, _evidence(), NOW)
    assert gates.GATE_5 not in result.undecided and result.reason is None

    extra = [_pr(50 + i, 20) for i in range(2)]
    _, failing = _derive(tmp_path / "fail", counted_prs() + excluded_prs() + extra)
    ghost = failing.inputs["gr14_ghost_rate"]
    assert (ghost.value, ghost.n) == (7 / 13, 13)
    result = gates.evaluate_gates(failing, _evidence(), NOW)
    assert result.reason == gates.REASON_BLACK_HOLE


def test_ghost_rate_equals_the_gate_share(tmp_path: Path) -> None:
    """The input the gate reads is the zero-response share of the gate's own
    function over the same records: one measurement."""
    from porchlamp.collect import github_graphql

    prs = counted_prs() + excluded_prs()
    _, bundle = _derive(tmp_path, prs)
    records = [gates.GhostRatePR(created_at=p["createdAt"], is_external=d["is_external"],
                                 is_bot_author=d["is_bot_author"],
                                 first_substantive_response_at=d["first_substantive_response_at"])
               for p, d in zip(prs, github_graphql.derive_pr_window(prs))]
    unanswered, n = gates.zero_response_counts(records, NOW)
    ghost = bundle.inputs["gr14_ghost_rate"]
    assert (ghost.value, ghost.n) == (unanswered / n, n) == (5 / 11, 11)


def test_reference_time_is_the_pinned_now_not_the_wall_clock(tmp_path: Path) -> None:
    """The two 13.5-day-old PRs are excluded at NOW; read against the wall
    clock (weeks later) they would be in. A later pinned `now` admits them."""
    doc, bundle = _derive(tmp_path / "now", counted_prs() + excluded_prs())
    assert doc["meta"]["gr14_window_end"] == NOW.isoformat()
    assert bundle.inputs["gr14_ghost_rate"].n == 11
    later = NOW + timedelta(days=1)
    _, later_bundle = _derive(tmp_path / "later", counted_prs() + excluded_prs(), now=later)
    assert later_bundle.inputs["gr14_ghost_rate"].n == 13


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


def test_no_pr_window_envelope_is_collector_failure(tmp_path: Path) -> None:
    doc = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path, fetch=False, now=NOW)
    assert doc["confidence"]["gr14_population_n"] == "missing"
    bundle = load(tmp_path, json.loads(json.dumps(doc)))
    value = bundle.inputs["gr14_ghost_rate"]
    assert isinstance(value, Unmeasured) and value.reason == "collector_failure"
