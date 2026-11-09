"""D-IN-1 knows service accounts GitHub types as users (RFC-0012): one exact login list,
``porchlamp.core.constants.BOT_LOGINS``, decides bot identity for PR responses, maintainer
evidence, PR authors and commits alike.

The records are shaped like the recorded data: rust-lang's ``rustbot`` assigning a reviewer
within a minute, Kubernetes' ``k8s-ci-robot`` merging through tide and greeting new
contributors, and a cherry-pick PR a bot opens.
"""
from __future__ import annotations

from typing import Any

from porchlamp.collect import github_graphql
from porchlamp.core import commit_domains, constants, gates


def _node(login: str, text: str, when: str, *, association: str) -> dict[str, Any]:
    return {"author": {"__typename": "User", "login": login}, "authorAssociation": association,
            "bodyText": text, "createdAt": when}


def _pr(number: int, *, author: str = "contributor", association: str = "NONE",
        comments: tuple[dict[str, Any], ...] = (), reviews: tuple[dict[str, Any], ...] = (),
        merged_by: str | None = None) -> dict[str, Any]:
    return {"number": number, "createdAt": "2026-08-01T00:00:00Z", "closedAt": None, "mergedAt": None,
            "state": "OPEN", "isDraft": False, "mergedBy": {"login": merged_by} if merged_by else None,
            "author": {"__typename": "User", "login": author, "createdAt": "2019-01-01T00:00:00Z"},
            "authorAssociation": association, "timelineItems": {"nodes": []},
            "comments": {"nodes": list(comments)}, "reviews": {"nodes": list(reviews)},
            "reviewThreads": {"totalCount": 0}}


RUSTBOT_ASSIGNMENT = ("r? @epage\nrustbot has assigned @epage.\nThey will have a look at your PR within the next "
                      "two weeks and either review your PR or reassign to another reviewer.")
K8S_GREETING = ("Hi @contributor. Thanks for your PR.\nI'm waiting for a kubernetes member to verify that this "
                "patch is reasonable to test.")


def test_a_service_account_github_types_as_a_user_never_answers_a_pr() -> None:
    """rustbot, a COLLABORATOR, assigns a reviewer a minute after the PR opens; the reviewer's
    review a day later is the first response."""
    pr = _pr(1, comments=(_node("rustbot", RUSTBOT_ASSIGNMENT, "2026-08-01T00:01:00Z", association="COLLABORATOR"),),
             reviews=({**_node("epage", "", "2026-08-02T00:00:00Z", association="MEMBER"), "state": "COMMENTED",
                       "body": "", "comments": {"totalCount": 1}},))
    row = github_graphql.derive_pr_window([pr])[0]
    assert (row["first_substantive_response_at"], row["first_substantive_response_kind"]) == (
        "2026-08-02T00:00:00Z", "review")


CODEX_REVIEW_COMMAND = "@codex review \u2014 apply the review criteria in .gemini/styleguide.md in addition to AGENTS.md guidance"


def test_an_account_that_only_posts_review_commands_to_another_bot_never_answers_a_pr() -> None:
    """RFC-0029: ghhamcp, a MEMBER, asks an AI reviewer for a review 30 seconds after each PR opens; it is in
    BOT_LOGINS, so a maintainer's review two hours later is the first response."""
    pr = _pr(1, comments=(_node("ghhamcp", CODEX_REVIEW_COMMAND, "2026-08-01T00:00:30Z", association="MEMBER"),),
             reviews=({**_node("maintainer", "", "2026-08-01T02:00:00Z", association="MEMBER"), "state": "COMMENTED",
                       "body": "", "comments": {"totalCount": 1}},))
    row = github_graphql.derive_pr_window([pr])[0]
    assert (row["first_substantive_response_at"], row["first_substantive_response_kind"]) == (
        "2026-08-01T02:00:00Z", "review")


def test_a_merge_bot_typed_as_a_user_is_no_maintainer() -> None:
    """k8s-ci-robot merges PRs (tide) with a CONTRIBUTOR association: merging makes an account a
    maintainer, but not a bot, so its greeting on another PR does not answer it."""
    prs = [_pr(1, merged_by="k8s-ci-robot"),
           _pr(2, comments=(_node("k8s-ci-robot", K8S_GREETING, "2026-08-01T00:00:30Z", association="CONTRIBUTOR"),))]
    assert "k8s-ci-robot" not in gates.maintainer_logins(prs)
    assert github_graphql.derive_pr_window(prs)[1]["first_substantive_response_at"] is None


def test_a_pr_a_service_account_opens_is_not_an_outside_pr() -> None:
    """A cherry-pick PR ti-chi-bot opens is a bot's PR: never in D-GATE-5's population."""
    row = github_graphql.derive_pr_window([_pr(1, author="ti-chi-bot")])[0]
    assert row["is_bot_author"] is True
    assert github_graphql.closed_unread_counts([_pr(1, author="ti-chi-bot")]) == (0, 0)


def test_a_service_account_commit_is_a_bot_commit() -> None:
    """D-GATE-4 and the commit measures count non-bot commits; a commit k8s-ci-robot authored is a bot's."""
    assert commit_domains.is_bot(commit_domains.CommitActor(login="k8s-ci-robot", email="", actor_type="User"))
    assert commit_domains.is_bot(commit_domains.CommitActor(login="RustBot", email="", actor_type="User"))


def test_people_whose_login_contains_bot_stay_people() -> None:
    """The list is exact: accounts of people named Talbot, Chabot or Sobot are not bots."""
    for login in ("psobot", "brettchabot", "g-talbot", "labbott", "boenrobot", "bbotella"):
        assert not commit_domains.is_bot(commit_domains.CommitActor(login=login, email="", actor_type="User")), login
        assert not github_graphql.is_bot_actor("User", login), login


def test_one_list_decides_bot_identity_everywhere() -> None:
    assert commit_domains.KNOWN_BOT_LOGINS is constants.BOT_LOGINS
    assert github_graphql.BOT_LOGINS is constants.BOT_LOGINS
    assert all(login == login.lower() and login.strip() == login for login in constants.BOT_LOGINS)
