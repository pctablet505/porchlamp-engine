"""D-GATE-5 from recorded ``pr_window`` envelopes shaped like the cases an independent
check against GitHub found (RFC-0011): a window cut at the collector's PR cap, maintainers
GitHub labels ``CONTRIBUTOR``, short Chinese replies, bot reviews and Meta's import notice.

Each case runs the recorded window through both readers of the one D-IN-2 definition
(:func:`porchlamp.core.gates.first_substantive_response`): the measurement derive stores as
GR_14 (``github_graphql.ghost_rate_prs_from_window`` and ``gates.zero_response_counts``) and
the served evidence (``gate_evidence.black_hole_window_breakdown_from_raw``), and checks they
agree.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from _fixtures import KEY, write_envelope

from porchlamp.collect import github_graphql
from porchlamp.core import constants, gates
from porchlamp.core.models import Cohorts, FeatureBundle, Measured, PRWindow, RepoIdentity, Unmeasured
from porchlamp.derive.gate_evidence import black_hole_window_breakdown_from_raw, gate_evidence_for

#: The snapshot's reference time; its window opens 180 days earlier, at 2026-04-20T05:35Z.
AS_OF = dt.datetime(2026, 10, 17, 5, 35, tzinfo=dt.timezone.utc)
WINDOW_START = AS_OF - dt.timedelta(days=constants.PR_WINDOW_DAYS)


def _at(day: dt.datetime, hours: float = 0.0) -> str:
    return (day + dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


#: The bot accounts these records use (``__typename`` "Bot" in GitHub's answers).
_BOTS = frozenset({"mergify[bot]", "meta-codesync", "copilot-pull-request-reviewer"})


def _actor(login: str, typename: str = "User") -> dict[str, Any]:
    return {"__typename": typename, "login": login}


def _comment(login: str, text: str, when: str, *, association: str = "MEMBER",
             typename: str = "User") -> dict[str, Any]:
    return {"author": _actor(login, typename), "authorAssociation": association,
            "bodyText": text, "createdAt": when}


def _review(login: str, when: str, *, association: str = "MEMBER", typename: str = "User") -> dict[str, Any]:
    return {"author": _actor(login, typename), "state": "COMMENTED", "body": "",
            "createdAt": when, "authorAssociation": association, "comments": {"totalCount": 0}}


def _pr(number: int, opened: dt.datetime, *, author: str, association: str = "NONE",
        comments: tuple[dict[str, Any], ...] = (), reviews: tuple[dict[str, Any], ...] = (),
        closed_by: tuple[str, str] | None = None, merged_by: tuple[str, str] | None = None) -> dict[str, Any]:
    """One PR node in the ``PR_WINDOW_QUERY`` shape. ``closed_by`` / ``merged_by`` are
    ``(login, when)``; a close records its ``ClosedEvent`` with the actor."""
    closed_at = (closed_by or merged_by or (None, None))[1]
    timeline = []
    if closed_by is not None or merged_by is not None:
        actor_login, when = closed_by or merged_by
        timeline.append({"__typename": "ClosedEvent", "createdAt": when,
                         "actor": _actor(actor_login, "Bot" if actor_login in _BOTS else "User")})
    return {
        "number": number, "createdAt": _at(opened), "closedAt": closed_at,
        "mergedAt": merged_by[1] if merged_by else None,
        "state": "MERGED" if merged_by else ("CLOSED" if closed_by else "OPEN"),
        "isDraft": False, "mergedBy": {"login": merged_by[0]} if merged_by else None,
        "author": {"__typename": "User", "login": author, "createdAt": "2019-01-01T00:00:00Z"},
        "authorAssociation": association,
        "timelineItems": {"nodes": timeline},
        "reviews": {"nodes": list(reviews)}, "comments": {"nodes": list(comments)},
        "reviewThreads": {"totalCount": 0},
    }


def _write_window(raw: Path, prs: list[dict[str, Any]], *, truncated: bool | None,
                  anchor: str = "2026-10-15T14:42:59+00:00") -> None:
    """A ``pr_window`` envelope as the collector writes it (newest first)."""
    prs = sorted(prs, key=lambda pr: pr["createdAt"], reverse=True)
    data: dict[str, Any] = {"window_anchor": anchor, "window_days": 180, "cap": 500, "count": len(prs),
                            "cost_points": 1, "prs": prs, "derived": github_graphql.derive_pr_window(prs)}
    if truncated is not None:
        data["truncated"] = truncated
    write_envelope(raw, "pr_window", KEY, data, fetched_at=anchor)


def _both_readers(raw: Path) -> tuple[int, int]:
    """``(unanswered, n)`` from the served evidence, after checking derive's GR_14
    measurement (the gate's input) gives the same counts."""
    breakdown = black_hole_window_breakdown_from_raw(raw, KEY, now=AS_OF)
    assert breakdown is not None
    import json

    document = json.loads((raw / "pr_window" / f"{KEY}.json").read_text(encoding="utf-8"))
    observed = dt.datetime.fromisoformat(document["fetched_at"])
    measured = gates.zero_response_counts(
        github_graphql.ghost_rate_prs_from_window(document["data"]["prs"]), AS_OF, observed_at=observed)
    assert measured == (breakdown.unanswered, breakdown.population_n)
    return measured


def _bundle(unanswered: int, n: int) -> FeatureBundle:
    """A bundle whose only measured input is GR_14, the share D-GATE-5 reads."""
    inputs: dict[str, Any] = {
        input_id: Unmeasured(reason="collector_failure", detail="stage-0 fixture")
        for input_id in constants.PARAM_INPUT_IDS
    }
    inputs[gates.GHOST_RATE_INPUT_ID] = Measured(
        value=unanswered / n, n=n, window_start=WINDOW_START.isoformat(), window_end=AS_OF.isoformat(),
        method="primary", source=f"raw/pr_window/{KEY}.json", source_hash="ab" * 32,
        fetched_at=AS_OF.isoformat())
    bundle = FeatureBundle(
        schema_version=constants.SCHEMA_VERSION,
        repo=RepoIdentity(owner="acme", name="widget", ecosystem="pypi", archived=False, mirror_url=None,
                          fork=False, license_spdx="MIT", default_branch="main"),
        inputs=inputs,
        pr_window=PRWindow(start=None, end=None, truncated=False, events=()),
        cohorts=Cohorts(decision_pr_numbers=(), newcomer_authors=()),
        provenance_hash="ab" * 32,
    )
    bundle.validate()
    return bundle


def _gate(raw: Path, unanswered: int, n: int) -> Any:
    """D-GATE-1..6 on the recorded evidence, with the other gates passing."""
    import dataclasses

    evidence = dataclasses.replace(gate_evidence_for(raw, KEY, as_of=AS_OF), manifest_found=True,
                                   vitality_commits_180d=5, decided_external_pr=True)
    return gates.evaluate_gates(_bundle(unanswered, n), evidence, AS_OF)


# --- a window cut at the PR cap --------------------------------------------------------------

def _capped_window(first_opened: dt.datetime, *, every_minutes: int = 105, quiet: int = 3) -> list[dict[str, Any]]:
    """500 outside PRs, one every ``every_minutes`` from ``first_opened``; ``quiet`` in five
    get no reply, the others a member's reply within an hour (Pumpkin-MC/Pumpkin's record
    held exactly 500 PRs, opened from 2026-09-08)."""
    prs = []
    for i in range(500):
        opened = first_opened + dt.timedelta(minutes=every_minutes * i)
        reply = () if i % 5 < quiet else (_comment("keeper", "Thanks, this looks right; merging after CI.",
                                                     _at(opened, 1)),)
        prs.append(_pr(5000 + i, opened, author=f"contrib{i}", comments=reply))
    return prs


def test_a_window_cut_at_the_pr_cap_leaves_the_gate_undecided_and_names_its_cut_off(tmp_path: Path) -> None:
    first = dt.datetime(2026, 9, 8, 0, 18, tzinfo=dt.timezone.utc)
    _write_window(tmp_path, _capped_window(first), truncated=True)
    unanswered, n = _both_readers(tmp_path)
    assert gates.is_black_hole_counts(unanswered, n)  # the newest weeks alone look like a black hole

    result = _gate(tmp_path, unanswered, n)
    assert (result.eligible, result.reason) == (True, None)
    assert "D-GATE-5" in result.undecided
    evidence = gate_evidence_for(tmp_path, KEY, as_of=AS_OF)
    assert (evidence.pr_window_complete, evidence.pr_window_cutoff) == (False, "2026-09-08T00:18:00Z")


def test_a_cut_window_that_reaches_back_past_the_window_start_is_judged(tmp_path: Path) -> None:
    """A record fetched ten days before the snapshot holds its newest 500 PRs, back to
    2026-04-15, before the window opened: it covers the window, so the gate judges it."""
    first = dt.datetime(2026, 4, 15, 0, 0, tzinfo=dt.timezone.utc)
    _write_window(tmp_path, _capped_window(first, every_minutes=500), truncated=True,
                  anchor="2026-10-07T00:00:00+00:00")
    evidence = gate_evidence_for(tmp_path, KEY, as_of=AS_OF)
    assert (evidence.pr_window_complete, evidence.pr_window_cutoff) == (True, None)
    unanswered, n = _both_readers(tmp_path)
    result = _gate(tmp_path, unanswered, n)
    assert result.reason == "black_hole"
    assert "D-GATE-5" not in result.undecided


def test_an_uncut_window_is_judged_and_one_without_its_cut_flag_is_not(tmp_path: Path) -> None:
    first = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
    prs = _capped_window(first, quiet=4)[:40]
    _write_window(tmp_path, prs, truncated=False)
    assert gate_evidence_for(tmp_path, KEY, as_of=AS_OF).pr_window_complete is True
    unanswered, n = _both_readers(tmp_path)
    assert _gate(tmp_path, unanswered, n).reason == "black_hole"

    _write_window(tmp_path, prs, truncated=None)
    evidence = gate_evidence_for(tmp_path, KEY, as_of=AS_OF)
    assert (evidence.pr_window_complete, evidence.pr_window_cutoff) == (False, None)
    assert "D-GATE-5" in _gate(tmp_path, unanswered, n).undecided


def test_an_uncut_window_that_opens_after_the_window_start_names_its_own_start(tmp_path: Path) -> None:
    """A record fetched three days after the snapshot holds PRs from 2026-04-23 only."""
    _write_window(tmp_path, _capped_window(dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc))[:40],
                  truncated=False, anchor="2026-10-20T05:35:00+00:00")
    evidence = gate_evidence_for(tmp_path, KEY, as_of=AS_OF)
    assert (evidence.pr_window_complete, evidence.pr_window_cutoff) == (False, "2026-04-23T05:35:00+00:00")


# --- maintainers GitHub labels CONTRIBUTOR ---------------------------------------------------

def _libsignal_window() -> list[dict[str, Any]]:
    """signalapp/libsignal's 11 counted PRs: a Signal maintainer GitHub labels CONTRIBUTOR
    replied within 14 days on #671 and #689 and closed #671 ("Included in v0.97.5"), since
    Signal ships changes in its own releases; a member reviewed one PR; an outside account
    replied on #678; the other seven got nothing."""
    day = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
    jrose = {"association": "CONTRIBUTOR"}
    prs = [
        _pr(671, day, author="dev671", comments=(
            _comment("staff-closer", "r" * 178, _at(day, 30), **jrose),
            _comment("staff-closer", "Included in v0.97.5", _at(day, 400), **jrose)),
            closed_by=("staff-closer", _at(day, 400))),
        _pr(689, day + dt.timedelta(days=20), author="dev689", comments=(
            _comment("staff-closer", "j" * 86, _at(day + dt.timedelta(days=20), 50), **jrose),)),
        _pr(678, day + dt.timedelta(days=10), author="dev678", comments=(
            _comment("outsider", "x" * 120, _at(day + dt.timedelta(days=10), 20), association="NONE"),)),
        _pr(690, day + dt.timedelta(days=30), author="dev690", reviews=(
            _review("member", _at(day + dt.timedelta(days=30), 5)),)),
    ]
    prs += [_pr(700 + i, day + dt.timedelta(days=40 + 7 * i), author=f"dev{700 + i}") for i in range(7)]
    return prs


def test_a_contributor_labelled_maintainer_who_closes_other_authors_prs_answers(tmp_path: Path) -> None:
    _write_window(tmp_path, _libsignal_window(), truncated=False)
    unanswered, n = _both_readers(tmp_path)
    assert (unanswered, n) == (8, 11)  # 10 of 11 when only the association counted
    assert gates.wilson_lower_bound(unanswered, n) < constants.GATE5_MAX_ZERO_RESPONSE_SHARE
    assert _gate(tmp_path, unanswered, n).reason is None
    rows = {row["number"]: row for row in github_graphql.derive_pr_window(_libsignal_window())}
    assert rows[689]["first_substantive_response_at"] == "2026-06-23T02:00:00Z"
    assert rows[678]["first_substantive_response_at"] is None  # an outside account is no maintainer


def test_closing_ones_own_pr_shows_no_repository_access() -> None:
    """Authors close their own PRs without any access: such a close makes nobody a maintainer."""
    day = dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)
    prs = [
        _pr(1, day, author="carol", association="CONTRIBUTOR", closed_by=("carol", _at(day, 5))),
        _pr(2, day, author="dev2", comments=(_comment("carol", "c" * 60, _at(day, 2), association="CONTRIBUTOR"),)),
    ]
    assert gates.maintainer_logins(prs) == frozenset()
    assert github_graphql.derive_pr_window(prs)[1]["first_substantive_response_at"] is None


# --- short Chinese, Japanese and Korean replies ----------------------------------------------

def test_a_short_chinese_review_comment_answers(tmp_path: Path) -> None:
    """datawhalechina/hello-agents #726: the maintainer's 18-character reply ("the PR title
    and description must follow chapter 16") says what 50 Latin characters would."""
    day = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
    prs = [
        _pr(726, day, author="dev726", comments=(_comment("maintainer", "PR名称和描述要按照第十六章要求来哈", _at(day, 3)),)),
        _pr(727, day, author="dev727", comments=(_comment("maintainer", "感谢\uff0cLGTM\uff01很好的项目", _at(day, 3)),)),
        _pr(740, day, author="dev740", comments=(_comment("maintainer", "", _at(day, 3)),)),  # an image, no text
    ]
    rows = github_graphql.derive_pr_window(prs)
    assert rows[0]["first_substantive_response_at"] == _at(day, 3)
    assert [row["first_substantive_response_at"] for row in rows[1:]] == [None, None]
    _write_window(tmp_path, prs, truncated=False)
    assert _both_readers(tmp_path) == (2, 3)


def test_each_cjk_letter_counts_three_characters_and_nothing_else_does() -> None:
    assert gates.comment_length("PR名称和描述要按照第十六章要求来哈") == 2 + 16 * 3  # 34 at weight 2
    assert not gates.is_substantive_comment("测" * 13)       # 39
    assert gates.is_substantive_comment("测" * 14)           # 42
    assert gates.is_substantive_comment("LGTM" + "测" * 12)  # 40
    assert not gates.is_substantive_comment("a" * 39)
    assert gates.is_substantive_comment("a" * 40)
    assert gates.comment_length("ありがとうございます\uff01") == 10 * 3 + 1  # a fullwidth "!" counts once
    assert gates.comment_length("없던 dependency 추가하신 것 같은데요") == 25 + 11 * 2
    assert gates.comment_length("\U00020000") == 3  # an ideograph outside the basic plane
    assert not gates.is_substantive_comment(None)


def test_the_comment_rule_reads_its_constants(monkeypatch: Any) -> None:
    monkeypatch.setattr(constants, "SUBSTANTIVE_COMMENT_CJK_WEIGHT", 2)
    assert not gates.is_substantive_comment("测" * 14)  # 28
    monkeypatch.setattr(constants, "SUBSTANTIVE_COMMENT_MIN_CHARS", 28)
    assert gates.is_substantive_comment("测" * 14)


# --- bots ------------------------------------------------------------------------------------

def test_a_bot_never_answers_even_when_it_merges_and_closes_prs(tmp_path: Path) -> None:
    """microsoft/markitdown #2437: copilot-pull-request-reviewer (a Bot) reviewed 17 minutes
    after the PR opened. Bots that merged or closed other PRs in the record are no
    maintainers either, and a bot GitHub labels a member is still a bot, so their reviews
    and comments never answer."""
    day = dt.datetime(2026, 9, 10, 6, 38, tzinfo=dt.timezone.utc)
    prs = [
        _pr(2400, day - dt.timedelta(days=20), author="dev2400", merged_by=("mergify[bot]", _at(day, -400))),
        _pr(2401, day - dt.timedelta(days=19), author="dev2401", closed_by=("meta-codesync", _at(day, -300))),
        _pr(2437, day, author="dev2437", reviews=(
            _review("copilot-pull-request-reviewer", _at(day, 0.29), association="CONTRIBUTOR", typename="Bot"),)),
        _pr(2438, day, author="dev2438", comments=(
            _comment("mergify[bot]", "m" * 80, _at(day, 1), association="CONTRIBUTOR", typename="Bot"),
            _comment("meta-codesync", "n" * 80, _at(day, 2), association="CONTRIBUTOR", typename="Bot")),
            reviews=(_review("copilot-pull-request-reviewer", _at(day, 3), association="MEMBER", typename="Bot"),)),
    ]
    assert gates.maintainer_logins(prs) == frozenset()
    rows = {row["number"]: row for row in github_graphql.derive_pr_window(prs)}
    assert rows[2437]["first_substantive_response_at"] is None
    assert rows[2438]["first_substantive_response_at"] is None
    _write_window(tmp_path, prs, truncated=False)
    # the merged PR answers by its merge; a bot's close answers nothing, nor its review or comments
    assert _both_readers(tmp_path) == (3, 4)


# --- Meta's import notice --------------------------------------------------------------------

def _sapling_window(*, imported_as: str) -> list[dict[str, Any]]:
    """facebook/sapling: Meta's meta-codesync bot tells six of eight outside PRs they were
    imported for internal review (in one of its two wordings), then they land internally."""
    day = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
    notices = {
        "passive": "This pull request has been imported. If you are a Meta employee, you can view this in "
                   "D125523361. (Because this pull request was imported automatically, there will not be "
                   "any future comments.)",
        "named": "@staff has imported this pull request. If you are a Meta employee, you can view this in "
                 "D125328983. (Because this pull request was imported automatically, there will not be any "
                 "future comments.)",
    }
    prs = [_pr(1100 + i, day + dt.timedelta(days=i), author=f"dev{i}", comments=(
        _comment("meta-codesync", notices[imported_as], _at(day + dt.timedelta(days=i), 1),
                 association="CONTRIBUTOR", typename="Bot"),)) for i in range(6)]
    prs += [_pr(1200 + i, day + dt.timedelta(days=i), author=f"quiet{i}") for i in range(2)]
    return prs


def test_metas_import_notice_hands_review_off_in_either_wording(tmp_path: Path) -> None:
    for wording in ("passive", "named"):
        _write_window(tmp_path, _sapling_window(imported_as=wording), truncated=False)
        evidence = gate_evidence_for(tmp_path, KEY, as_of=AS_OF)
        assert evidence.review_handoff is True, wording
        unanswered, n = _both_readers(tmp_path)
        result = _gate(tmp_path, unanswered, n)
        assert (result.eligible, result.routed) == (True, "off_platform"), wording
        assert "D-GATE-5" in result.undecided, wording
