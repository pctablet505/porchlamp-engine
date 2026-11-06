"""The first-pull-requests stats file, from small raw records whose answers are worked out by hand here."""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from porchlamp.snapshot import first_pull_requests as fpr

AS_OF = "2026-10-24T18:10:00+00:00"
ANCHOR = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
OLD = ANCHOR - timedelta(days=170)
REPLY = "Thanks for sending this, I will review the change this week."


def iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def after(start: datetime, *, days: float = 0, hours: float = 0) -> str:
    return iso(start + timedelta(days=days, hours=hours))


def comment(login: str, at: str, text: str = REPLY, association: str = "MEMBER") -> dict:
    return {"author": {"login": login, "__typename": "User"}, "authorAssociation": association, "bodyText": text, "createdAt": at}


def pull(
    number: int, opened: datetime = OLD, *, author: str = "ann", association: str = "NONE", merged: str | None = None,
    merged_by: str = "maya", closed: str | None = None, comments: tuple = (), reviews: tuple = (), draft: bool = False,
    ready: str | None = None, closed_by_author: bool = False, account_days: int = 400,
) -> dict:
    state = "MERGED" if merged else "CLOSED" if closed else "OPEN"
    return {
        "number": number, "createdAt": iso(opened), "mergedAt": merged, "closedAt": merged or closed, "state": state,
        "isDraft": draft, "readyForReviewAt": ready, "closedByAuthor": closed_by_author,
        "author": {"login": author, "__typename": "User", "createdAt": iso(opened - timedelta(days=account_days))},
        "authorAssociation": association,
        "mergedBy": {"login": merged_by, "__typename": "User"} if merged else None,
        "comments": {"nodes": list(comments)}, "reviews": {"nodes": list(reviews)}, "timelineItems": {"nodes": []},
    }


def write_project(root: Path, name: str, prs: list[dict], *, history: list[dict] | None = None, truncated: bool = False, stars: int | None = None) -> None:
    raw = root / "raw"
    for layer in ("pr_window", "author_history", "github_meta"):
        (raw / layer).mkdir(parents=True, exist_ok=True)
    (raw / "pr_window" / f"{name}.json").write_text(json.dumps({"data": {"window_anchor": iso(ANCHOR), "prs": prs}}))
    records = history if history is not None else [{"author": p["author"], "createdAt": p["createdAt"]} for p in prs]
    (raw / "author_history" / f"{name}.json").write_text(json.dumps({"data": {"truncated": truncated, "prs": records}}))
    if stars is not None:
        (raw / "github_meta" / f"{name}.json").write_text(json.dumps({"data": {"stars": stars}}))


def by_days(group: dict) -> dict[int, dict]:
    return {row["days"]: row for row in group["merged_if_silent"]}


def test_a_second_pull_request_the_history_never_saw_is_returning(tmp_path: Path) -> None:
    # the history was taken before either pull request opened
    write_project(tmp_path, "o__n", [pull(1, OLD, author="bob"), pull(2, OLD + timedelta(days=5), author="bob")], history=[])
    population = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["population"]
    assert (population["first"], population["returning"]) == (1, 1)


def test_a_history_keeps_each_authors_earliest_pull_request(tmp_path: Path) -> None:
    bob = {"login": "bob"}
    write_project(tmp_path, "o__n", [pull(1, author="bob")], history=[{"author": bob, "createdAt": after(OLD, days=-30)}, {"author": bob, "createdAt": after(OLD, days=3)}])
    population = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["population"]
    assert (population["first"], population["returning"]) == (0, 1)


def test_a_deleted_account_is_neither_first_nor_returning(tmp_path: Path) -> None:
    gone = pull(1)
    gone["author"] = None
    write_project(tmp_path, "o__n", [gone, pull(2)], history=[])
    population = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["population"]
    assert (population["read"], population["first"], population["returning"]) == (2, 1, 0)


def test_only_outside_ready_grown_up_pull_requests_inside_the_window_are_read(tmp_path: Path) -> None:
    write_project(tmp_path, "o__n", [
        pull(1),
        pull(2, association="MEMBER"),
        pull(3, author="dependabot[bot]"),
        pull(4, draft=True),
        pull(5, account_days=30),
        pull(6, ANCHOR - timedelta(days=200)),
    ])
    stats = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)
    assert stats["population"]["read"] == 1
    # outside_prs is the stats file's count: every outside pull request opened in the window, the draft and young account too.
    assert stats["population"]["outside_prs"] == 3
    assert stats["window_start"] == "2026-04-27T18:10:00+00:00"


def test_a_project_without_a_window_is_not_a_project(tmp_path: Path) -> None:
    write_project(tmp_path, "o__n", [pull(1)])
    (tmp_path / "raw" / "pr_window" / "o__empty.json").write_text(json.dumps({"data": {"window_anchor": iso(ANCHOR)}}))
    assert fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["population"]["projects"] == 1


def test_an_author_the_window_shows_holding_access_is_not_read(tmp_path: Path) -> None:
    # boss merges their own pull request and ann's, so GitHub's CONTRIBUTOR label on boss's says nothing
    write_project(tmp_path, "o__n", [
        pull(1, author="boss", association="CONTRIBUTOR", merged=after(OLD, hours=1), merged_by="boss"),
        pull(2, author="ann", merged=after(OLD, hours=2), merged_by="boss"),
    ])
    stats = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)
    assert stats["population"]["read"] == 1
    assert stats["population"]["outside_prs"] == 2, "the corpus count is the snapshot's, access or not"
    assert (stats["population"]["first"], stats["population"]["returning"]) == (1, 0)


def test_first_and_returning_come_from_the_history_and_a_cut_history_says_neither(tmp_path: Path) -> None:
    earlier = {"author": {"login": "bob"}, "createdAt": after(OLD, days=-30)}
    write_project(tmp_path, "o__n", [pull(1), pull(2, author="bob")], history=[earlier])
    write_project(tmp_path, "o__cut", [pull(3)], truncated=True)
    population = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["population"]
    assert (population["read"], population["first"], population["returning"]) == (3, 1, 1)


def test_the_waiting_curve_counts_what_was_still_silent_and_was_merged_in_the_end(tmp_path: Path) -> None:
    write_project(tmp_path, "o__n", [
        pull(1, merged=after(OLD, days=20)),                                  # silent until the merge on day 20
        pull(2, comments=[comment("maya", after(OLD, days=2))]),              # answered on day 2, never merged
        pull(3),                                                              # never answered
        pull(4, closed=after(OLD, days=5), closed_by_author=True),            # its author left on day 5
    ])
    curve = by_days(fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["first"])
    assert curve[0] == {"days": 0, "n": 4, "merged": 0.25}
    assert curve[3] == {"days": 3, "n": 3, "merged": round(1 / 3, 4)}
    assert curve[7] == {"days": 7, "n": 2, "merged": 0.5}
    assert curve[21] == {"days": 21, "n": 1, "merged": 0.0}


def test_the_waiting_curve_reads_only_settled_pull_requests(tmp_path: Path) -> None:
    young = ANCHOR - timedelta(days=SETTLED_LESS_ONE)
    write_project(tmp_path, "o__n", [pull(1), pull(2, young, author="bob")])
    stats = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)
    assert stats["first"]["settled"] == 1
    assert by_days(stats["first"])[0]["n"] == 1
    assert stats["population"]["first"] == 2


SETTLED_LESS_ONE = fpr.SETTLED_DAYS - 1


def test_answered_shares_are_kaplan_meier_with_departures_leaving_the_count() -> None:
    # answered at 1 h and 10 h; one leaves at 5 h; one is still waiting at 100 h.
    shares = fpr.answered_shares([(1.0, True), (5.0, False), (10.0, True), (100.0, False)], (1, 6, 24))
    assert shares[1] == pytest.approx(0.25)
    assert shares[6] == pytest.approx(0.25)
    # after the departure two are at risk at 10 h: 0.75 * (1 - 1/2).
    assert shares[24] == pytest.approx(0.625)


def test_an_answer_and_a_departure_at_one_instant_count_the_answer_first() -> None:
    assert fpr.answered_shares([(5.0, False), (5.0, True)], (5,))[5] == pytest.approx(0.5)


def test_answered_later_is_conditional_on_silence_at_the_first_day(tmp_path: Path) -> None:
    prs = [pull(i, merged=after(OLD, days=day)) for i, day in enumerate((1, 40, 70, 100), 1)]
    later = {(row["from_days"], row["to_days"]): row["share"] for row in fpr.compute_first_pull_requests(tmp_path_with(tmp_path, prs), as_of=AS_OF)["first"]["answered_later"]}
    # silent at 14 days: three of four. Of those, one answered by day 60, two by day 90.
    assert later[(14, 30)] == 0.0
    assert later[(14, 60)] == pytest.approx(1 / 3, abs=1e-4)
    assert later[(14, 90)] == pytest.approx(2 / 3, abs=1e-4)


def tmp_path_with(root: Path, prs: list[dict]) -> Path:
    write_project(root, "o__n", prs)
    return root


@pytest.mark.parametrize("text, kind", [
    ("Friendly ping, CI is green.", "ask"),
    ("Could you take a look when you get a chance?", "ask"),
    ("@maya any update on this?", "ask"),
    ("@maya what about the other branch", "mention"),
    ("Rebased on main, same change otherwise.", "other"),
    ("I have read the CLA Document and I hereby sign the CLA", "tool"),
    ("/retest", "tool"),
    ("@coderabbitai review", "tool"),
    ("Addressed the CodeRabbit notes, could you take a look?", "ask"),
    ("The cursor position was wrong, now fixed.", "other"),
    ("Thanks @maya, could you take a look?", "tool"),
    ("", "tool"),
    (None, "tool"),
])
def test_comment_kinds(text: object, kind: str) -> None:
    assert fpr.comment_kind(text) == kind


def test_asking_arms_stand_at_the_landmark_and_the_first_hour_does_not_count() -> None:
    assert fpr._arms([], 14) == ["none"]
    assert fpr._arms([(0.5, "ask")], 14) == ["none"]
    assert fpr._arms([(24 * 3, "ask")], 14) == ["ask"]
    assert fpr._arms([(24 * 10, "ask")], 14) == ["ask", "ask_recent"]
    assert fpr._arms([(24 * 3, "mention")], 14) == ["mention"]
    assert fpr._arms([(24 * 3, "other"), (24 * 4, "tool")], 14) == ["other"]
    assert fpr._arms([(24 * 3, "mention"), (24 * 4, "ask")], 14) == ["ask"]
    # a comment after the landmark has not happened yet
    assert fpr._arms([(24 * 20, "ask")], 14) == ["none"]


def test_adjusted_gap_weighs_projects_by_their_size() -> None:
    # Project one: 1 of 2 against 1 of 2, gap 0, weight 1. Project two: 3 of 3 against 0 of 6, gap 1, weight 2.
    rows = [([2, 1, 0], [2, 1, 0]), ([3, 3, 1], [6, 0, 0])]
    assert fpr.adjusted_gap(rows, 1) == pytest.approx(2 / 3)
    assert fpr.adjusted_gap(rows, 2) == pytest.approx((1 * 0 + 2 * (1 / 3)) / 3)


def test_asking_follows_pull_requests_unanswered_and_open_at_the_landmark(tmp_path: Path) -> None:
    ask = comment("ann", after(OLD, days=10), "Friendly ping on this one.", "NONE")
    write_project(tmp_path, "o__n", [
        pull(1, comments=[ask, comment("maya", after(OLD, days=20))]),  # asked in the week before day 14, answered on day 20
        pull(2),                                                        # no comment, never answered
        pull(3, merged=after(OLD, days=30)),                            # no comment, merged on day 30
        pull(4, merged=after(OLD, days=2)),                             # answered before the landmark: not followed
        pull(5, closed=after(OLD, days=3), closed_by_author=True),      # closed before the landmark: not followed
        pull(6, merged=after(OLD, days=90)),                            # no comment, answered after the 30 days followed
        pull(7, comments=[comment("maya", after(OLD, days=3))]),        # replied to before the landmark, still open: not followed
    ])
    arm = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["asking"]["day14_first"]["ask_recent"]
    assert (arm["projects"], arm["n"], arm["n_none"]) == (1, 1, 3)
    assert arm["answered"] == 1.0
    assert arm["answered_none"] == round(1 / 3, 4)
    assert arm["gap_answered"] == round(1 - 1 / 3, 4)
    assert arm["merged"] == 0.0
    assert arm["merged_none"] == round(2 / 3, 4)


def test_asking_needs_the_whole_follow_up_on_record(tmp_path: Path) -> None:
    young = ANCHOR - timedelta(days=14 + fpr.FOLLOW_DAYS - 1)
    ask = comment("ann", after(OLD, days=10), "Friendly ping on this one.", "NONE")
    write_project(tmp_path, "o__n", [pull(1, comments=[ask]), pull(2, young, author="bob")])
    # the young pull request is not followed, so the project has no one to compare the ask with
    assert fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["asking"]["day14_all"]["ask"] == {"projects": 0, "n": 0, "n_none": 0}


def test_an_arm_with_no_project_having_both_sides_reports_zero(tmp_path: Path) -> None:
    write_project(tmp_path, "o__n", [pull(1)])
    assert fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["asking"]["day14_first"]["ask"] == {"projects": 0, "n": 0, "n_none": 0}


def test_weekday_is_the_utc_day_the_clock_started_and_needs_two_weeks_on_record(tmp_path: Path) -> None:
    monday = datetime(2026, 8, 3, 23, 30, tzinfo=timezone.utc)
    saturday = datetime(2026, 8, 8, 0, 30, tzinfo=timezone.utc)
    recent = ANCHOR - timedelta(days=3)
    write_project(tmp_path, "o__n", [
        pull(1, monday, comments=[comment("maya", after(monday, hours=2))]),
        pull(2, monday),
        pull(3, saturday, comments=[comment("maya", after(saturday, hours=30))]),
        pull(4, recent, comments=[comment("maya", after(recent, hours=1))]),
    ])
    by_day = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["weekday"]["by_day"]
    assert by_day["Mon"] == {"n": 2, "answered_in_a_day": 0.5}
    assert by_day["Sat"] == {"n": 1, "answered_in_a_day": 0.0}
    assert sum(row["n"] for row in by_day.values()) == 3


def test_weekday_inside_a_project_needs_enough_of_both(tmp_path: Path) -> None:
    monday = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
    saturday = datetime(2026, 8, 8, 10, 0, tzinfo=timezone.utc)
    def answered(start: datetime) -> dict:
        return dict(comments=[comment("maya", after(start, hours=2))])
    many = [pull(i, monday, **answered(monday)) for i in range(fpr.WEEKDAY_MIN_WEEKDAY)]
    weekend = [pull(100 + i, saturday, **(answered(saturday) if i < 5 else {})) for i in range(fpr.WEEKDAY_MIN_WEEKEND)]
    write_project(tmp_path, "o__n", many + weekend)
    write_project(tmp_path, "o__few", many[:3] + weekend[:3])
    within = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["weekday"]["within_project"]
    assert within == {"projects": 1, "median_gap": 0.5, "weekday_better": 1.0}


def test_who_answers_attributes_replies_and_merges_to_people(tmp_path: Path) -> None:
    prs = [pull(i, comments=[comment("maya", after(OLD, hours=2 + i))]) for i in range(14)]
    prs += [pull(100 + i, merged=after(OLD, hours=2), merged_by="noor") for i in range(6)]
    prs += [pull(200 + i, merged=after(OLD, hours=2), merged_by="mergify[bot]") for i in range(3)]
    write_project(tmp_path, "o__n", prs, stars=60_000)
    write_project(tmp_path, "o__small", prs[:5])
    who = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["who_answers"]
    assert who["projects"] == 1
    assert who["over_half"] == 1.0
    assert who["median_top_share"] == 0.7
    assert who["median_people"] == 2
    assert who["histogram"] == [0, 0, 0, 0, 0, 0, 0, 1, 0, 0]
    assert who["large"] == {"min_stars": 50_000, "projects": 1, "over_half": 1.0, "median_people": 2}


def test_who_answers_reads_the_large_projects_apart(tmp_path: Path) -> None:
    solo = [pull(i, comments=[comment("maya", after(OLD, hours=2))]) for i in range(fpr.MIN_ATTRIBUTED_ANSWERS)]
    write_project(tmp_path, "o__big", solo, stars=fpr.LARGE_PROJECT_STARS)
    write_project(tmp_path, "o__small", solo, stars=fpr.LARGE_PROJECT_STARS - 1)
    write_project(tmp_path, "o__unknown", solo)
    who = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["who_answers"]
    assert who["projects"] == 3
    assert who["large"]["projects"] == 1


def answered_by(helpers: dict[str, int], *, first_prs: int, answered: int, merged: int) -> list[dict]:
    """A project of ``first_prs`` first pull requests from different people: ``answered`` get a reply, ``merged`` of those a
    merge, and ``helpers`` says who gives how many of the replies. Enough returning pull requests fill ``helpers``."""
    prs = []
    for i in range(first_prs):
        replies = [comment("maya", after(OLD, hours=3))] if i < answered else []
        prs.append(pull(i, author=f"new{i}", comments=replies, merged=after(OLD, days=2) if i < merged else None, merged_by="maya"))
    number = 1000
    for login, count in helpers.items():
        for _ in range(count):
            number += 1
            prs.append(pull(number, author=f"back{number}", comments=[comment(login, after(OLD, hours=2))]))
    return prs


def outcomes_project(root: Path, name: str, top: int, others: int, *, first_prs: int = 10, answered: int = 5, merged: int = 0, stars: int = 5000) -> None:
    """``maya`` gives ``top`` replies and ``others`` other people one each, plus the replies to the first pull requests."""
    helpers = {"maya": top, **{f"h{i}": 1 for i in range(others)}}
    prs = answered_by(helpers, first_prs=first_prs, answered=answered, merged=merged)
    history = [{"author": p["author"], "createdAt": p["createdAt"]} for p in prs if p["author"]["login"].startswith("new")]
    history += [{"author": p["author"], "createdAt": after(OLD, days=-30)} for p in prs if p["author"]["login"].startswith("back")]
    write_project(root, name, prs, history=history, stars=stars)


def test_share_bands_meet_where_the_page_says() -> None:
    assert [fpr.share_band(x) for x in (0.2, 0.5, 0.51, 0.7, 0.71, 0.89, 0.9, 1.0)] == [
        "shared", "shared", "over_half", "over_half", "over_70", "over_70", "one", "one",
    ]


def test_outcomes_read_each_band_by_its_median_project(tmp_path: Path) -> None:
    # one person gives every answer: 10 first pull requests, 8 answered, 6 of them merged
    outcomes_project(tmp_path, "o__solo", 20, 0, answered=8, merged=6)
    # maya gives 5 replies to first pull requests and 5 more, 30 others one each: nobody near half
    outcomes_project(tmp_path, "o__team", 5, 30, answered=5, merged=2)
    out = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["who_answers"]["outcomes"]
    bands = {b["band"]: b for b in out["bands"]}
    assert out["projects"] == 2
    assert (bands["one"]["projects"], bands["one"]["answered"], bands["one"]["merged"]) == (1, 0.8, 0.6)
    assert (bands["shared"]["projects"], bands["shared"]["answered"], bands["shared"]["merged"]) == (1, 0.5, 0.2)
    assert (bands["over_half"]["projects"], bands["over_half"]["answered"]) == (0, None)
    assert [b["band"] for b in out["bands"]] == list(fpr.SHARE_BANDS)


def test_a_band_reads_its_median_project_not_the_mean(tmp_path: Path) -> None:
    outcomes_project(tmp_path, "o__a", 20, 0, answered=1, merged=1)
    outcomes_project(tmp_path, "o__b", 20, 0, answered=1, merged=1)
    outcomes_project(tmp_path, "o__c", 20, 0, answered=10, merged=10)
    one = next(b for b in fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["who_answers"]["outcomes"]["bands"] if b["band"] == "one")
    assert (one["projects"], one["answered"], one["merged"]) == (3, 0.1, 0.1)


def test_outcomes_count_the_projects_under_the_answer_floor(tmp_path: Path) -> None:
    # 10 first pull requests and 3 answers, all from one person
    outcomes_project(tmp_path, "o__quiet", 0, 0, answered=3, merged=1)
    # 10 first pull requests and no answer at all: below the floor, and nobody at 90%
    outcomes_project(tmp_path, "o__silent", 0, 0, answered=0, merged=0)
    # under the 10-first-pull-request mark: not counted
    outcomes_project(tmp_path, "o__few", 0, 0, first_prs=fpr.MIN_OUTCOME_FIRST_PRS - 1, answered=3)
    # over the floor: in the comparison, not below it
    outcomes_project(tmp_path, "o__active", 20, 0, answered=8, merged=6)
    out = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["who_answers"]["outcomes"]
    assert out["projects"] == 1
    assert out["below_floor"] == {
        "projects": 2, "answered": 0.15, "merged": 0.05,
        "one": {"projects": 1, "answered": 0.3, "merged": 0.1},
    }


def test_outcomes_read_the_share_answered_within_a_day(tmp_path: Path) -> None:
    prs = answered_by({"maya": 20}, first_prs=8, answered=3, merged=0)
    prs.append(pull(50, author="slow1", comments=[comment("maya", after(OLD, days=2))]))
    prs.append(pull(51, author="slow2", comments=[comment("maya", after(OLD, days=2))]))
    history = [{"author": p["author"], "createdAt": p["createdAt"]} for p in prs if not p["author"]["login"].startswith("back")]
    history += [{"author": p["author"], "createdAt": after(OLD, days=-30)} for p in prs if p["author"]["login"].startswith("back")]
    write_project(tmp_path, "o__n", prs, history=history)
    one = next(b for b in fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["who_answers"]["outcomes"]["bands"] if b["band"] == "one")
    assert (one["in_a_day"], one["answered"]) == (0.3, 0.5)


def test_outcomes_need_enough_first_pull_requests_and_answers_by_people(tmp_path: Path) -> None:
    outcomes_project(tmp_path, "o__few", 20, 0, first_prs=fpr.MIN_OUTCOME_FIRST_PRS - 1)
    outcomes_project(tmp_path, "o__enough", 20, 0, first_prs=fpr.MIN_OUTCOME_FIRST_PRS)
    who = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["who_answers"]
    assert who["projects"] == 2
    assert who["outcomes"]["projects"] == 1


def test_outcomes_count_answers_within_two_weeks_and_leave_out_a_first_pull_request_its_author_closed(tmp_path: Path) -> None:
    prs = answered_by({"maya": 20}, first_prs=9, answered=4, merged=0)
    prs.append(pull(50, author="late", comments=[comment("maya", after(OLD, days=15))]))
    prs.append(pull(51, author="left", closed=after(OLD, days=2), closed_by_author=True))
    history = [{"author": p["author"], "createdAt": p["createdAt"]} for p in prs if not p["author"]["login"].startswith("back")]
    history += [{"author": p["author"], "createdAt": after(OLD, days=-30)} for p in prs if p["author"]["login"].startswith("back")]
    write_project(tmp_path, "o__n", prs, history=history)
    bands = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["who_answers"]["outcomes"]["bands"]
    # 10 count, not 11; 4 of them answered in time, the 15-day answer is not one
    assert next(b for b in bands if b["band"] == "one")["answered"] == 0.4


def row(top: float, answered: float, merged: float = 0.5, *, stars: int = 5000, first_prs: int = 20, strict: bool = True) -> fpr._Outcome:
    return fpr._Outcome(top=top, first_prs=first_prs, answered=answered, merged=merged, stars=stars, strict=strict, in_a_day=0.0)


def test_the_clear_range_is_the_middle_95_percent_of_resampled_gaps() -> None:
    rows = [row(1.0, 0.5 + i / 100, 0.4) for i in range(12)] + [row(0.4, 0.3 + i / 100, 0.2) for i in range(12)]
    seeded = random.Random(7)
    out = fpr._clear_against_shared(rows, seeded)
    check = random.Random(7)
    answered = sorted(v for v in (fpr.clear_gap(check.choices(rows, k=len(rows)), "answered") for _ in range(fpr.BOOTSTRAP_ROUNDS)) if v is not None)
    assert out["gap_answered_range"] == [round(answered[int(0.025 * len(answered))], 4), round(answered[int(0.975 * len(answered))], 4)]
    merged = sorted(v for v in (fpr.clear_gap(check.choices(rows, k=len(rows)), "merged") for _ in range(fpr.BOOTSTRAP_ROUNDS)) if v is not None)
    assert out["gap_merged_range"] == [round(merged[int(0.025 * len(merged))], 4), round(merged[int(0.975 * len(merged))], 4)]
    assert out["gap_answered"] == pytest.approx(0.2)
    assert out["gap_merged"] == pytest.approx(0.2)


def test_the_asking_range_is_the_middle_95_percent_of_resampled_gaps_and_merged_reads_merges() -> None:
    tallies = [{"ask": [4, 3 - (i % 2), 1], "none": [10, 2 + (i % 3), 4]} for i in range(10)]
    out = fpr._compare(tallies, "ask", random.Random(3))
    rows = [(t["ask"], t["none"]) for t in tallies]
    check = random.Random(3)
    for label, outcome in (("answered", 1), ("merged", 2)):
        rounds = sorted(fpr.adjusted_gap(check.choices(rows, k=len(rows)), outcome) for _ in range(fpr.BOOTSTRAP_ROUNDS))
        assert out[f"gap_{label}_range"] == [round(rounds[int(0.025 * fpr.BOOTSTRAP_ROUNDS)], 4), round(rounds[int(0.975 * fpr.BOOTSTRAP_ROUNDS)], 4)]
        assert out[f"gap_{label}"] == round(fpr.adjusted_gap(rows, outcome), 4)
    assert out["gap_merged"] != out["gap_answered"]


def test_the_file_is_seeded_so_two_runs_agree_on_the_ranges(tmp_path: Path) -> None:
    for index in range(6):
        outcomes_project(tmp_path, f"o__one{index}", 20, 0, answered=5 + index, merged=2 + index)
        outcomes_project(tmp_path, f"o__team{index}", 5, 30, answered=4, merged=1)
    first = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["who_answers"]["outcomes"]["clear_against_shared"]
    second = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["who_answers"]["outcomes"]["clear_against_shared"]
    assert first == second
    assert first["gap_answered_range"][0] < first["gap_answered"] < first["gap_answered_range"][1]


def test_only_the_authors_own_comment_is_an_ask_and_returning_pull_requests_stay_out_of_the_first_reading(tmp_path: Path) -> None:
    passerby = comment("passer", after(OLD, days=10), "Friendly ping on this one.", "NONE")
    mine = comment("ann", after(OLD, days=10), "Friendly ping on this one.", "NONE")
    bob_earlier = {"author": {"login": "bob"}, "createdAt": after(OLD, days=-30)}
    ann_new = {"author": {"login": "ann"}, "createdAt": iso(OLD)}
    cara_new = {"author": {"login": "cara"}, "createdAt": iso(OLD)}
    write_project(tmp_path, "o__n", [
        pull(1, author="ann", comments=[mine]),
        pull(2, author="cara", comments=[passerby]),
        pull(3, author="bob", comments=[comment("bob", after(OLD, days=10), "Friendly ping on this one.", "NONE")]),
    ], history=[ann_new, cara_new, bob_earlier])
    asking = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["asking"]
    assert asking["day14_first"]["ask_recent"]["n"] == 1, "ann's own ask; bob is returning and cara's is someone else's comment"
    assert asking["day14_first"]["ask_recent"]["n_none"] == 1, "cara had no comment of her own"
    assert asking["day14_all"]["ask_recent"]["n"] == 2, "ann's and bob's"


def test_the_day_seven_reading_stands_at_day_seven(tmp_path: Path) -> None:
    early = comment("ann", after(OLD, days=5), "Friendly ping on this one.", "NONE")
    late = comment("cara", after(OLD, days=10), "Friendly ping on this one.", "NONE")
    write_project(tmp_path, "o__n", [pull(1, author="ann", comments=[early]), pull(2, author="cara", comments=[late]), pull(3, author="dan")])
    asking = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["asking"]
    assert asking["day7_first"]["ask_recent"]["n"] == 1, "the ask on day 10 hasn't happened at day 7"
    assert asking["day7_first"]["ask_recent"]["n_none"] == 2
    assert asking["day14_first"]["ask"]["n"] == 2
    assert asking["day14_first"]["ask_recent"]["n"] == 1, "the ask on day 5 is more than a week old at day 14"


def test_the_clear_gap_compares_only_inside_one_cell_of_stars_and_first_pull_requests() -> None:
    rows = [
        row(1.0, 0.9, stars=5000), row(0.4, 0.5, stars=5000),
        # a one-person project with no shared project near its size says nothing
        row(1.0, 0.1, stars=500_000),
        # a shared project of another volume says nothing either
        row(0.4, 0.0, stars=5000, first_prs=100),
    ]
    assert fpr.clear_gap(rows, "answered") == pytest.approx(0.4)
    assert fpr.clear_gap(rows[:1], "answered") is None


def test_the_clear_gap_weights_cells_by_how_many_of_both_they_hold() -> None:
    rows = [row(1.0, 1.0), row(1.0, 1.0), row(1.0, 1.0), row(0.4, 0.5), row(0.4, 0.5), row(0.4, 0.5)]
    rows += [row(1.0, 0.2, stars=20_000), row(0.4, 0.3, stars=20_000)]
    # weights 3 x 3 / 6 = 1.5 and 1 x 1 / 2 = 0.5: (1.5 x 0.5 + 0.5 x -0.1) / 2
    assert fpr.clear_gap(rows, "answered") == pytest.approx(0.35)


def test_the_clear_gap_leaves_the_middle_bands_out() -> None:
    rows = [row(1.0, 0.9), row(0.4, 0.5), row(0.8, 0.0), row(0.6, 0.0)]
    assert fpr.clear_gap(rows, "answered") == pytest.approx(0.4)


def test_the_range_comes_from_resampling_and_the_strict_reading_leaves_out_owner_run_and_lone_answerers(tmp_path: Path) -> None:
    # owner "o" answers everything at o/solo; "maya" answers nearly everything at o/kept, with one other person; o/owned is run by its owner
    outcomes_project(tmp_path, "o__kept", 40, 1, answered=9, merged=8)
    prs = answered_by({"o": 40, "h0": 1}, first_prs=10, answered=0, merged=0)
    history = [{"author": p["author"], "createdAt": p["createdAt"]} for p in prs if p["author"]["login"].startswith("new")]
    history += [{"author": p["author"], "createdAt": after(OLD, days=-30)} for p in prs if p["author"]["login"].startswith("back")]
    write_project(tmp_path, "o__owned", prs, history=history, stars=5000)
    outcomes_project(tmp_path, "o__lone", 20, 0, answered=1, merged=0)
    for index in range(3):
        outcomes_project(tmp_path, f"o__team{index}", 5, 30, answered=5, merged=2)
    out = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["who_answers"]["outcomes"]
    everything, strict = out["clear_against_shared"], out["clear_against_shared_strict"]
    assert (everything["projects_one"], everything["projects_shared"]) == (3, 3)
    assert (strict["projects_one"], strict["projects_shared"]) == (1, 3)
    # (0.9 + 0.0 + 0.1) / 3 against 0.5 with everyone; 0.9 against 0.5 without the owner's and the lone answerer's
    assert everything["gap_answered"] == pytest.approx(1 / 3 - 0.5, abs=1e-3)
    assert strict["gap_answered"] == pytest.approx(0.4, abs=1e-3)
    low, high = everything["gap_answered_range"]
    assert low < everything["gap_answered"] < high
    assert strict["gap_merged"] == pytest.approx(0.8 - 0.2, abs=1e-3)


def test_no_project_in_a_band_gives_no_gap_and_no_range(tmp_path: Path) -> None:
    outcomes_project(tmp_path, "o__solo", 20, 0)
    gap = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["who_answers"]["outcomes"]["clear_against_shared"]
    assert gap == {"projects_one": 1, "projects_shared": 0, "gap_answered": None, "gap_answered_range": None, "gap_merged": None, "gap_merged_range": None}


def test_first_pr_projects_lists_only_projects_with_enough_of_them(tmp_path: Path) -> None:
    prs = [pull(i, author=f"u{i}", comments=[comment("maya", after(OLD, hours=3))]) for i in range(fpr.MIN_FIRST_PRS)]
    write_project(tmp_path, "o__n", prs, stars=10)
    write_project(tmp_path, "o__few", prs[:-1])
    rows = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["first_pr_projects"]
    assert rows == [["o/n", fpr.MIN_FIRST_PRS, 3.0, 1.0, 1.0, 0.0, 10]]


def test_the_file_is_the_same_for_any_number_of_workers(tmp_path: Path) -> None:
    ask = comment("ann", after(OLD, days=10), "Friendly ping on this one.", "NONE")
    for index in range(4):
        write_project(tmp_path, f"o__p{index}", [pull(1, comments=[ask]), pull(2), pull(3, merged=after(OLD, days=30 + index))])
    assert fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF, workers=2) == fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF, workers=1)


def test_an_as_of_without_a_time_zone_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        fpr.compute_first_pull_requests(tmp_path, as_of="2026-10-24T18:10:00")


def test_an_author_closing_their_own_unanswered_pull_request_leaves_the_answered_by_count(tmp_path: Path) -> None:
    write_project(tmp_path, "o__n", [
        pull(1, author="a", closed=after(OLD, hours=2), closed_by_author=True),
        pull(2, author="b", comments=[comment("maya", after(OLD, hours=3))]),
    ])
    shares = {row["hours"]: row["share"] for row in fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["first"]["answered_by"]}
    # the author who left at 2 h is out of the count, so the one answered at 3 h is everyone left
    assert shares[1] == 0.0
    assert shares[6] == 1.0


def test_the_clock_of_a_draft_starts_when_it_was_marked_ready(tmp_path: Path) -> None:
    ready = OLD + timedelta(days=20)
    write_project(tmp_path, "o__n", [
        # the reply came while it was a draft, so it answers nothing; the merge a day after it was marked ready does
        pull(1, comments=[comment("maya", after(OLD, days=5))], ready=iso(ready), merged=after(ready, days=1)),
    ])
    shares = {row["hours"]: row["share"] for row in fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["first"]["answered_by"]}
    assert shares[6] == 0.0
    assert shares[72] == 1.0


def test_first_pr_projects_leave_out_pull_requests_their_author_closed_unanswered(tmp_path: Path) -> None:
    prs = [pull(i, author=f"u{i}", comments=[comment("maya", after(OLD, hours=3))]) for i in range(fpr.MIN_FIRST_PRS - 1)]
    prs.append(pull(99, author="left", closed=after(OLD, days=2), closed_by_author=True))
    write_project(tmp_path, "o__n", prs)
    assert fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)["first_pr_projects"] == []


def test_a_merge_by_a_bot_is_nobodys_answer_in_who_answers_but_still_an_answer(tmp_path: Path) -> None:
    prs = [pull(i, merged=after(OLD, hours=2), merged_by="mergify[bot]") for i in range(fpr.MIN_ATTRIBUTED_ANSWERS)]
    write_project(tmp_path, "o__n", prs)
    stats = fpr.compute_first_pull_requests(tmp_path, as_of=AS_OF)
    assert stats["who_answers"]["projects"] == 0
    assert stats["first"]["answered_by"][2]["share"] > 0
