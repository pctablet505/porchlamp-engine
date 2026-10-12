"""``porchlamp.derive.gate_evidence._vitality_commit_count`` (D-GATE-4) reads a
commits envelope's rows in either recorded shape -- the bare row list every
``commits_180d`` envelope holds today, or the window dict
``collect_commits_365d`` records -- through the same
``porchlamp.core.commit_domains.envelope_rows`` every other commits reader uses."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from _fixtures import KEY, write_envelope

from porchlamp.collect.github_graphql import derive_pr_window
from porchlamp.derive.gate_evidence import _vitality_commit_count, gate_evidence_for

ROWS = [{"author_login": "alice", "date": "2026-08-01T00:00:00Z"},
        {"author_login": "bob", "date": "2026-08-02T00:00:00Z"},
        {"author_login": "dependabot[bot]", "date": "2026-08-03T00:00:00Z"}]


def test_vitality_count_from_a_row_list(tmp_path: Path) -> None:
    write_envelope(tmp_path, "commits_180d", KEY, ROWS)
    assert _vitality_commit_count(tmp_path, KEY) == 2  # the bot is not counted


def test_vitality_count_from_a_window_dict(tmp_path: Path) -> None:
    write_envelope(tmp_path, "commits_180d", KEY,
                   {"count": 3, "cap": 1000, "truncated": False, "rows": ROWS})
    assert _vitality_commit_count(tmp_path, KEY) == 2


def test_vitality_count_absent_or_rowless_envelope_is_undecided(tmp_path: Path) -> None:
    assert _vitality_commit_count(tmp_path, KEY) is None
    write_envelope(tmp_path, "commits_180d", KEY, {"count": 3})
    assert _vitality_commit_count(tmp_path, KEY) is None


def test_vitality_count_filters_bot_by_email_and_actor_type_without_login(tmp_path: Path) -> None:
    """Regression: bot commits without author_login are filtered via author_email / author_type,
    while human commits with only author_email are still counted."""
    rows = [
        {"author_login": None, "author_email": "49699333+dependabot[bot]@users.noreply.github.com"},
        {"author_login": "", "author_email": "bot@example.com", "author_type": "Bot"},
        {"author_login": None, "author_email": "human@example.com", "author_type": "User"},
        {"author_login": None, "author_email": None, "author_type": None},
    ]
    write_envelope(tmp_path, "commits_180d", KEY, rows)
    # The last row has no identity at all: a commit whose author has no linked
    # GitHub account. Nothing marks it a bot, so it counts as a person.
    assert _vitality_commit_count(tmp_path, KEY) == 2


def test_vitality_counts_commits_without_a_linked_account(tmp_path: Path) -> None:
    """Regression (2026-10-03 review of PR #1): scripts/gather.py writes only
    {"author_login", "date"} per commit, and a commit whose author has no linked
    GitHub account has author_login "". Requiring an identity counted those as 0,
    so sqlite/sqlite (300 such commits), LuaJIT and 18 more failed D-GATE-4 as
    "stale" while actively maintained."""
    rows = [{"author_login": "", "date": "2026-09-01T00:00:00Z"} for _ in range(3)]
    rows.append({"author_login": "dependabot[bot]", "date": "2026-09-02T00:00:00Z"})
    write_envelope(tmp_path, "commits_180d", KEY, rows)
    assert _vitality_commit_count(tmp_path, KEY) == 3


def test_gate_evidence_for_tree_absent_corrupt_truncated_and_empty(tmp_path: Path) -> None:
    """Regression: gate_evidence_for distinguishes absent/corrupt tree (None),
    truncated tree without match (None), truncated tree with match (True),
    and complete empty/non-matching tree (False)."""
    # 1. Absent tree file -> None
    ev_absent = gate_evidence_for(tmp_path, KEY)
    assert ev_absent.manifest_found is None
    assert ev_absent.license_file_present is None
    assert ev_absent.off_platform_marker is None

    # 2. Corrupt JSON tree file -> None
    tree_path = tmp_path / "tree" / f"{KEY}.json"
    tree_path.parent.mkdir(parents=True, exist_ok=True)
    tree_path.write_text("{corrupt json", encoding="utf-8")
    ev_corrupt = gate_evidence_for(tmp_path, KEY)
    assert ev_corrupt.manifest_found is None
    assert ev_corrupt.license_file_present is None
    assert ev_corrupt.off_platform_marker is None

    # 3. Complete empty tree (paths=[], truncated=False) -> False (not None)
    write_envelope(tmp_path, "tree", KEY, {"paths": [], "truncated": False})
    ev_empty = gate_evidence_for(tmp_path, KEY)
    assert ev_empty.manifest_found is False
    assert ev_empty.license_file_present is False
    assert ev_empty.off_platform_marker is False

    # 4. Truncated tree without matches -> None (undecided)
    write_envelope(tmp_path, "tree", KEY, {"paths": ["docs/README.md"], "truncated": True})
    ev_trunc_no_match = gate_evidence_for(tmp_path, KEY)
    assert ev_trunc_no_match.manifest_found is None
    assert ev_trunc_no_match.license_file_present is None
    assert ev_trunc_no_match.off_platform_marker is None

    # 5. Truncated tree with a recognized manifest -> True for manifest_found, None for others
    write_envelope(tmp_path, "tree", KEY, {"paths": ["pyproject.toml"], "truncated": True})
    ev_trunc_match = gate_evidence_for(tmp_path, KEY)
    assert ev_trunc_match.manifest_found is True
    assert ev_trunc_match.license_file_present is None
    assert ev_trunc_match.off_platform_marker is None



# --------------------------------------------------------------------------- #
# D-GATE-6 amendment: review hand-off (a project's own notices send outside    #
# PRs to Gerrit, a mailing list or an internal repository)                     #
# --------------------------------------------------------------------------- #

GERRIT_NOTICE = ("This PR (HEAD: de2c610) has been imported to Gerrit for code review.\n"
                 "Please visit https://go-review.googlesource.com/c/go/+/1 to see it.")
GITGITGADGET_WELCOME = "Welcome to GitGitGadget\n\nHi @alice, and welcome to GitGitGadget, the GitHub App."
INTERNAL_NOTICE = ("Thanks for the PR. We will import it to our internal repository for further "
                   "review, and it will land here once it is merged there.")


def _comment(login: str, text: str, *, typename: str = "User") -> dict[str, Any]:
    return {"author": {"login": login, "__typename": typename}, "authorAssociation": "NONE",
            "bodyText": text, "createdAt": "2026-09-01T01:00:00Z"}


def _pr(number: int, *, author: str = "alice", typename: str = "User", association: str = "NONE",
        comments: tuple[dict[str, Any], ...] = ()) -> dict[str, Any]:
    return {"number": number, "createdAt": "2026-09-01T00:00:00Z", "closedAt": None, "mergedAt": None,
            "author": {"login": author, "__typename": typename}, "authorAssociation": association,
            "comments": {"nodes": list(comments)}, "reviews": {"nodes": []}}


def _outside(first: int, with_notice: int, without_notice: int) -> list[dict[str, Any]]:
    """Outside PRs (external, not a bot): ``with_notice`` carry a gopherbot Gerrit notice, then
    ``without_notice`` carry only a CLA bot comment."""
    prs = [_pr(first + i, author=f"contrib{i}", comments=(_comment("gopherbot", GERRIT_NOTICE),))
           for i in range(with_notice)]
    prs += [_pr(first + with_notice + i, author=f"quiet{i}",
                comments=(_comment("google-cla", "Thanks for your pull request!", typename="Bot"),))
            for i in range(without_notice)]
    return prs


def _internal(first: int, count: int) -> list[dict[str, Any]]:
    """PRs by a MEMBER (not external), each carrying a Gerrit notice."""
    return [_pr(first + i, author=f"staff{i}", association="MEMBER",
                comments=(_comment("gopherbot", GERRIT_NOTICE),)) for i in range(count)]


def _bots(first: int, count: int) -> list[dict[str, Any]]:
    """PRs by a bot with an external association, each carrying a Gerrit notice."""
    return [_pr(first + i, author=f"autobot{i}[bot]", typename="Bot",
                comments=(_comment("gopherbot", GERRIT_NOTICE),)) for i in range(count)]


def _has_notice(pr: dict[str, Any]) -> bool:
    from porchlamp.derive.gate_evidence import _pr_has_review_handoff_notice

    return _pr_has_review_handoff_notice(pr)


def _write_pr_window(raw_root: Path, prs: list[dict[str, Any]]) -> None:
    """A ``pr_window`` envelope whose ``derived`` rows come from the collector's own classifier."""
    write_envelope(raw_root, "pr_window", KEY, {"prs": prs, "derived": derive_pr_window(prs)})


def test_gerrit_import_notice_is_a_handoff() -> None:
    pr = _pr(1, comments=(_comment("gopherbot", GERRIT_NOTICE),))
    assert _has_notice(pr) is True


def test_gitgitgadget_welcome_and_relay_login_are_handoffs() -> None:
    welcome = _pr(1, comments=(_comment("some-maintainer", GITGITGADGET_WELCOME),))
    assert _has_notice(welcome) is True
    # Any comment by a gitgitgadget account counts, whatever it says.
    relayed = _pr(2, comments=(_comment("gitgitgadget-git", "Submitted as pull.1234.git.git.gitgitgadget@gmail.com"),))
    assert _has_notice(relayed) is True
    # The login prefix is matched case-insensitively.
    assert _has_notice(_pr(3, comments=(_comment("GitGitGadget", "ok"),))) is True


def test_internal_import_notice_is_a_handoff() -> None:
    pr = _pr(1, comments=(_comment("mapbox-bot", INTERNAL_NOTICE, typename="Bot"),))
    assert _has_notice(pr) is True


def test_notice_phrases_match_case_insensitively() -> None:
    pr = _pr(1, comments=(_comment("gopherbot", GERRIT_NOTICE.upper()),))
    assert _has_notice(pr) is True


def test_the_authors_own_comment_is_not_a_notice() -> None:
    # The PR author quotes the notice (login differs from the PR author's only in case).
    pr = _pr(1, author="Alice", comments=(_comment("alice", "Bot said: " + GERRIT_NOTICE),))
    assert _has_notice(pr) is False
    # Control: the same words from anyone else are a notice.
    other = _pr(2, author="Alice", comments=(_comment("bob", "Bot said: " + GERRIT_NOTICE),))
    assert _has_notice(other) is True


def test_lookalike_text_is_not_a_notice() -> None:
    lookalikes = [
        "You must sign a CLA before we can merge. Corporate CLAs are announced on our "
        "electronic mailing lists and signed electronically.",
        "This change was merged via Copybara.",
        "Landed internally as cl/123456.",
        "Code review happens on Gerrit, please look there.",
    ]
    for text in lookalikes:
        pr = _pr(1, comments=(_comment("cla-helper", text),))
        assert _has_notice(pr) is False, text
    # No comments at all, and a PR node with no comments key: no notice, no crash.
    assert _has_notice(_pr(2)) is False
    assert _has_notice({"number": 3, "author": {"login": "alice"}}) is False


def test_handoff_true_at_exactly_five_notices_and_half(tmp_path: Path) -> None:
    _write_pr_window(tmp_path, _outside(1, 5, 5))  # 5 of 10 outside PRs
    assert gate_evidence_for(tmp_path, KEY).review_handoff is True


def test_handoff_false_below_five_notices(tmp_path: Path) -> None:
    _write_pr_window(tmp_path, _outside(1, 4, 4))  # 4 of 8: half, but under five
    assert gate_evidence_for(tmp_path, KEY).review_handoff is False


def test_handoff_false_below_half(tmp_path: Path) -> None:
    _write_pr_window(tmp_path, _outside(1, 5, 6))  # 5 of 11: five, but under half
    assert gate_evidence_for(tmp_path, KEY).review_handoff is False


def test_handoff_counts_only_outside_prs(tmp_path: Path) -> None:
    # 5 of 10 outside PRs carry a notice; six more PRs, internal or bot, carry one too.
    # Counting them in neither the numerator nor the denominator leaves 5/10: True.
    _write_pr_window(tmp_path, _outside(1, 5, 5) + _internal(100, 3) + _bots(200, 3))
    assert gate_evidence_for(tmp_path, KEY).review_handoff is True
    # 4 notices among 8 outside PRs is under five. Three internal PRs with notices would lift
    # the count to 7 of 11 if they were counted.
    _write_pr_window(tmp_path, _outside(1, 4, 4) + _internal(100, 3))
    assert gate_evidence_for(tmp_path, KEY).review_handoff is False
    # The same with bot-authored PRs (external association, but a bot).
    _write_pr_window(tmp_path, _outside(1, 4, 4) + _bots(200, 3))
    assert gate_evidence_for(tmp_path, KEY).review_handoff is False


def test_handoff_none_without_a_readable_envelope(tmp_path: Path) -> None:
    assert gate_evidence_for(tmp_path, KEY).review_handoff is None  # absent file
    path = tmp_path / "pr_window" / f"{KEY}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{corrupt json", encoding="utf-8")
    assert gate_evidence_for(tmp_path, KEY).review_handoff is None  # corrupt JSON
    write_envelope(tmp_path, "pr_window", KEY, {"derived": [], "count": 0})
    assert gate_evidence_for(tmp_path, KEY).review_handoff is None  # data without prs
    path.write_bytes(b"\xff\xfe not utf-8")
    assert gate_evidence_for(tmp_path, KEY).review_handoff is None  # undecodable bytes
    path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    assert gate_evidence_for(tmp_path, KEY).review_handoff is None  # not a document


def test_handoff_none_when_the_classifications_are_missing(tmp_path: Path) -> None:
    """Outside PRs are the collector's own ``derived`` rows; with none recorded there is no
    population to judge, which is no opinion rather than a verdict of False."""
    write_envelope(tmp_path, "pr_window", KEY, {"prs": _outside(1, 5, 5)})
    assert gate_evidence_for(tmp_path, KEY).review_handoff is None


def test_handoff_reads_its_thresholds_and_phrases_from_the_constants(
        tmp_path: Path, monkeypatch: Any) -> None:
    """A declared constant nothing reads proves nothing: move each one and watch the answer move."""
    from porchlamp.core import constants

    _write_pr_window(tmp_path, _outside(1, 4, 4))  # 4 of 8
    assert gate_evidence_for(tmp_path, KEY).review_handoff is False
    monkeypatch.setattr(constants, "GATE6_HANDOFF_MIN_PRS", 4)
    assert gate_evidence_for(tmp_path, KEY).review_handoff is True  # the count floor
    monkeypatch.setattr(constants, "GATE6_HANDOFF_MIN_SHARE", 0.6)
    assert gate_evidence_for(tmp_path, KEY).review_handoff is False  # the share
    monkeypatch.setattr(constants, "GATE6_HANDOFF_MIN_SHARE", 0.5)
    monkeypatch.setattr(constants, "GATE6_HANDOFF_NOTICES", ("a phrase nobody wrote",))
    assert gate_evidence_for(tmp_path, KEY).review_handoff is False  # the phrases
    monkeypatch.setattr(constants, "GATE6_HANDOFF_RELAY_LOGIN_PREFIXES", ("gopher",))
    assert gate_evidence_for(tmp_path, KEY).review_handoff is True  # the relay login prefix
