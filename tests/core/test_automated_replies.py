"""A person's account that runs automation does not answer a pull request (RFC-0025).

handsontable's ``qunabu`` is a maintainer who also owns a ticket-link integration: seconds after a PR opens, a
"Task linked: DEV-1780 ..." comment appears under that account. The account is no bot (GitHub types it as a user,
it merges and reviews), so identity lists miss it. What marks the automation is the comment itself: one template,
on many PRs, within a minute of each PR opening. People do not answer 80% of 20 PRs inside a minute by hand.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from porchlamp.collect import github_graphql
from porchlamp.core import constants, gates

BASE = datetime(2026, 8, 1, tzinfo=timezone.utc)
REAL_REVIEW = "Looks right to me, but the edge case with an empty list needs a test before this can merge."


def _at(hours: float = 0.0, seconds: float = 0.0) -> str:
    return (BASE + timedelta(hours=hours, seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _comment(login: str, text: str, when: str, association: str = "MEMBER") -> dict[str, Any]:
    return {"author": {"__typename": "User", "login": login}, "authorAssociation": association,
            "bodyText": text, "createdAt": when}


def _review(login: str, text: str, when: str, association: str = "MEMBER") -> dict[str, Any]:
    return {"author": {"__typename": "User", "login": login}, "authorAssociation": association, "body": text,
            "state": "COMMENTED", "createdAt": when, "comments": {"totalCount": 0}}


def _pr(number: int, *, created: str, comments=(), reviews=(), merged_by: str | None = None) -> dict[str, Any]:
    return {"number": number, "createdAt": created, "closedAt": None, "mergedAt": None, "state": "OPEN",
            "isDraft": False, "mergedBy": {"login": merged_by} if merged_by else None,
            "author": {"__typename": "User", "login": f"outsider{number}", "createdAt": "2019-01-01T00:00:00Z"},
            "authorAssociation": "CONTRIBUTOR", "timelineItems": {"nodes": []},
            "comments": {"nodes": list(comments)}, "reviews": {"nodes": list(reviews)},
            "reviewThreads": {"totalCount": 0}}


def _window(n: int, *, fast_every: int = 1, login: str = "qunabu", template: str = "Task linked: DEV-{i} {title}",
            with_review: bool = True) -> list[dict[str, Any]]:
    """``n`` outside PRs; the account comments on each with ``template`` (within 5 s for every ``fast_every``-th PR,
    an hour later for the others); a second maintainer reviews each PR two hours in."""
    prs = []
    for i in range(n):
        created = _at(hours=i * 3)
        delay = {"seconds": 5} if i % fast_every == 0 else {"hours": 1}
        when = _at(hours=i * 3 + delay.get("hours", 0), seconds=delay.get("seconds", 0))
        text = template.format(i=1000 + i, title=f"Make change number {i} work for the documentation sidebar")
        reviews = (_review("reviewer", REAL_REVIEW, _at(hours=i * 3 + 2)),) if with_review else ()
        prs.append(_pr(i + 1, created=created, comments=(_comment(login, text, when),), reviews=reviews,
                       merged_by="reviewer"))
    return prs


def _first(prs: list[dict[str, Any]], index: int = 0) -> tuple[str, str] | None:
    return gates.first_substantive_response(prs[index], gates.maintainer_logins(prs))


def test_the_thresholds_are_constants_of_the_methodology():
    assert (constants.AUTOMATION_MIN_PRS, constants.AUTOMATION_FAST_SECONDS) == (20, 60)
    assert (constants.AUTOMATION_FAST_SHARE, constants.AUTOMATION_TEMPLATE_WORDS) == (0.8, 2)


def test_a_template_posted_within_a_minute_on_many_prs_is_automation_and_the_real_review_answers():
    prs = _window(25, with_review=True)
    assert ("qunabu", "task linked") in gates.maintainer_logins(prs).automated
    assert _first(prs) == (_at(hours=2), "review")


def test_without_the_rule_the_same_comment_would_have_answered_in_seconds():
    prs = _window(25)
    assert gates.first_substantive_response(prs[0], frozenset({"qunabu", "reviewer"})) == (_at(seconds=5), "comment")


def test_a_pr_with_only_the_automated_comment_is_unanswered():
    prs = _window(25, with_review=False)
    assert _first(prs) is None


def test_the_automated_comment_does_not_make_ttfhr_seconds():
    prs = _window(25)
    row = github_graphql.derive_pr_window(prs)[0]
    assert row["first_substantive_response_at"] == _at(hours=2)
    assert row["ttfhr_hours"] == 2.0


def test_nineteen_prs_are_too_few_to_call_a_template_automation():
    prs = _window(constants.AUTOMATION_MIN_PRS - 1)
    assert not gates.maintainer_logins(prs).automated
    assert _first(prs) == (_at(seconds=5), "comment")


def test_twenty_prs_are_enough():
    assert ("qunabu", "task linked") in gates.maintainer_logins(_window(constants.AUTOMATION_MIN_PRS)).automated


def test_a_template_that_is_fast_on_only_some_prs_is_a_person_who_often_replies_quickly():
    assert not gates.maintainer_logins(_window(30, fast_every=2)).automated   # 50% within a minute
    assert gates.maintainer_logins(_window(30, fast_every=1)).automated


def test_the_fast_share_boundary_is_inclusive_at_eighty_percent():
    fast = _window(20)
    for pr in fast[16:]:   # 4 of 20 an hour late: exactly 80% within a minute
        pr["comments"]["nodes"][0]["createdAt"] = _at(hours=pr["number"] * 3 - 3 + 1)
    assert ("qunabu", "task linked") in gates.maintainer_logins(fast).automated
    fast[15]["comments"]["nodes"][0]["createdAt"] = _at(hours=15 * 3 + 1)   # 75%
    assert not gates.maintainer_logins(fast).automated


def test_exactly_sixty_seconds_is_still_within_a_minute():
    prs = _window(20)
    for pr in prs:
        pr["comments"]["nodes"][0]["createdAt"] = _at(hours=(pr["number"] - 1) * 3, seconds=constants.AUTOMATION_FAST_SECONDS)
    assert ("qunabu", "task linked") in gates.maintainer_logins(prs).automated
    prs[0]["comments"]["nodes"][0]["createdAt"] = _at(seconds=constants.AUTOMATION_FAST_SECONDS + 1)
    prs[1]["comments"]["nodes"][0]["createdAt"] = _at(hours=3, seconds=constants.AUTOMATION_FAST_SECONDS + 1)
    prs[2]["comments"]["nodes"][0]["createdAt"] = _at(hours=6, seconds=constants.AUTOMATION_FAST_SECONDS + 1)
    prs[3]["comments"]["nodes"][0]["createdAt"] = _at(hours=9, seconds=constants.AUTOMATION_FAST_SECONDS + 1)
    prs[4]["comments"]["nodes"][0]["createdAt"] = _at(hours=12, seconds=constants.AUTOMATION_FAST_SECONDS + 1)
    assert not gates.maintainer_logins(prs).automated   # 15 of 20 = 75%


def test_a_pr_authors_own_comments_do_not_make_their_template_automation():
    # carol opens 25 PRs and comments on each at once ("Task linked: ..."); on someone else's PR the same
    # words from her an hour later are a reply, and the 25 self-comments must not condemn them
    prs = [_pr(i + 1, created=_at(hours=i * 3), comments=(_comment("carol", f"Task linked: DEV-{i} the sidebar change", _at(hours=i * 3, seconds=4)),))
           for i in range(25)]
    for pr in prs:
        pr["author"]["login"] = "carol"
        pr["authorAssociation"] = "MEMBER"
    other = _pr(100, created=_at(hours=100), comments=(_comment("carol", "Task linked: DEV-9 follow up on this one please", _at(hours=101)),))
    window = prs + [other]
    assert ("carol", "task linked") not in gates.maintainer_logins(window).automated
    assert gates.first_substantive_response(other, gates.maintainer_logins(window)) == (_at(hours=101), "comment")


def test_the_first_reply_of_a_template_on_each_pr_is_what_counts_not_the_last():
    prs = _window(25, with_review=False)
    for pr in prs:   # a second, much later comment with the same template on every PR
        when = _at(hours=(pr["number"] - 1) * 3 + 30)
        pr["comments"]["nodes"].append(_comment("qunabu", "Task linked: DEV-1 a later reminder about the sidebar", when))
    assert ("qunabu", "task linked") in gates.maintainer_logins(prs).automated


def test_twenty_prs_are_needed_not_twenty_comments():
    prs = _window(10, with_review=False)
    for pr in prs:   # three template comments on each of only 10 PRs
        for extra in (2, 3):
            pr["comments"]["nodes"].append(_comment("qunabu", f"Task linked: DEV-{extra} one more note about the sidebar",
                                                    _at(hours=(pr["number"] - 1) * 3, seconds=extra)))
    assert not gates.maintainer_logins(prs).automated


def test_a_person_who_words_each_reply_differently_is_never_flagged_however_fast():
    openings = ["Great catch", "Nice idea", "Good point", "Interesting approach", "Cool change", "Fair enough",
                "Makes sense", "Sounds good", "Happy with this", "Welcome aboard"] * 3
    prs = []
    for i, opening in enumerate(openings):
        text = f"{opening} on item {i}, I will look at the details of this change properly later today."
        prs.append(_pr(i + 1, created=_at(hours=i * 3), comments=(_comment("maria", text, _at(hours=i * 3, seconds=8)),)))
    assert not gates.maintainer_logins(prs).automated


def test_the_same_words_from_another_account_are_judged_on_that_accounts_own_timing():
    prs = _window(25) + [_pr(100 + j, created=_at(hours=200 + j * 3),
                              comments=(_comment("carol", f"Task linked: DEV-{j} thanks for the work on this one",
                                                 _at(hours=200 + j * 3 + 5)),)) for j in range(5)]
    automated = gates.maintainer_logins(prs).automated
    assert ("qunabu", "task linked") in automated and ("carol", "task linked") not in automated


def test_the_same_account_keeps_its_real_reviews():
    prs = _window(25, with_review=False)
    prs[0]["reviews"]["nodes"].append(_review("qunabu", REAL_REVIEW, _at(hours=3)))
    assert _first(prs) == (_at(hours=3), "review")


def test_an_auto_approval_review_template_posted_in_seconds_is_automation_too():
    prs = []
    for i in range(25):
        review = _review("approver", f"Auto approved bump PR {i} after checks passed on the dependency update", _at(hours=i * 3, seconds=3))
        prs.append(_pr(i + 1, created=_at(hours=i * 3), reviews=(review,), comments=(
            _comment("maria", REAL_REVIEW, _at(hours=i * 3 + 5)),)))
    assert ("approver", "auto approved") in gates.maintainer_logins(prs).automated
    assert gates.first_substantive_response(prs[0], gates.maintainer_logins(prs)) == (_at(hours=5), "comment")


def test_a_review_without_text_is_never_a_template_and_still_answers():
    prs = []
    for i in range(25):
        prs.append(_pr(i + 1, created=_at(hours=i * 3), reviews=(_review("qunabu", "", _at(hours=i * 3, seconds=4)),)))
    assert not gates.maintainer_logins(prs).automated
    assert _first(prs) == (_at(seconds=4), "review")


def test_names_and_numbers_in_a_template_do_not_split_it():
    assert gates.reply_template("Hello @alice, thank you for your PR #12") == gates.reply_template(
        "Hello @bob, thank you for your PR #99") == "hello thank"
    assert gates.reply_template("ok") == ""


def test_a_plain_set_of_maintainers_still_works_for_callers_that_pass_one():
    pr = _window(25)[0]
    assert gates.first_substantive_response(pr, frozenset({"qunabu"})) == (_at(seconds=5), "comment")


def test_the_gate_counts_the_automated_comment_as_no_answer():
    prs = _window(25, with_review=False)
    from porchlamp.derive import gate_evidence
    records = gate_evidence._ghost_rate_prs_from_nodes(prs)
    assert all(r.first_substantive_response_at is None for r in records)


def test_the_first_response_since_a_later_clock_skips_the_earlier_ones() -> None:
    """``since`` reads a clock that starts after the PR opened (a draft marked ready): the earliest substantive response at
    or after it, the same rules otherwise; without it nothing changes."""
    early, late = _at(hours=1), _at(hours=30)
    pr = _pr(1, created=_at(), comments=(_comment("maya", REAL_REVIEW, early), _comment("maya", REAL_REVIEW, late)))
    people = frozenset({"maya"})
    assert gates.first_substantive_response(pr, people) == (early, "comment")
    assert gates.first_substantive_response(pr, people, since=BASE + timedelta(hours=2)) == (late, "comment")
    assert gates.first_substantive_response(pr, people, since=BASE + timedelta(hours=30)) == (late, "comment"), "at the clock counts"
    assert gates.first_substantive_response(pr, people, since=BASE + timedelta(hours=31)) is None
