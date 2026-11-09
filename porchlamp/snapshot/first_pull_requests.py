"""The first-pull-requests stats file: what happens to an outside pull request, above all a person's first to a project.

``python -m porchlamp.snapshot first-pull-requests --data-dir <root> --as-of <iso>`` prints it;
``scripts/export_static.py --first-pull-requests`` reads it (:mod:`porchlamp.serve.first_pull_requests_page`). It reads the
raw records only: ``raw/pr_window/`` (each project's pull requests and their replies), ``raw/author_history/`` (who sent
one before) and ``raw/github_meta/`` (stars).

The pull requests read are the outside ones (:func:`porchlamp.core.gates.is_outside_pr`) opened in the PR window ending at
``--as-of`` whose replies the record holds (:func:`porchlamp.core.gates.reply_sampled_prs`), without authors the window shows
holding access (:func:`porchlamp.core.gates.maintainer_logins`, whatever GitHub's label says), without drafts never marked
ready and without authors whose account was under ``MIN_ACCOUNT_AGE_DAYS`` old when they opened it. The clock starts when
the pull request opened or, for a draft, when it was marked ready. The answer is the earliest of the first substantive
response (D-IN-2), a merge and a close by the project's people. Each record is observed up to its own ``window_anchor``.

A pull request is a **first** when its author has no earlier one in the project's ``author_history`` or in the window, and
**returning** when they have. A record that was cut at its cap (``truncated``) cannot tell the two apart, so its project is
in neither group: most of the busiest projects are missing from every first or returning figure. A deleted account is in
neither group either.

Fields (shares are fractions, rounded to 4 places):

``as_of``, ``window_start``
    the window;
``population``
    ``projects`` (with a recorded window), ``outside_prs`` (every outside pull request opened in the window, the stats
    file's ``human_outside_prs``), ``read``, ``first``, ``returning``;
``first``, ``returning``
    ``settled`` (old enough for ``merged_if_silent``); ``merged_if_silent``: per day of silence, how many pull requests
    ``SETTLED_DAYS`` or more days old had no answer by that day and were not closed by their author before it, and the
    share of them merged by the end of the record; ``answered_by``: the share answered by each hour mark (Kaplan-Meier,
    a pull request leaving the count when its record ends or its author closes it unanswered); ``answered_later``: of
    those unanswered at ``from_days``, the share answered by ``to_days``;
``asking``
    for pull requests with no answer and still open at day 7 or 14 (``day14_first``, ``day14_all``, ``day7_first``), what
    happened in the next ``FOLLOW_DAYS`` days, against those whose author had not commented at all: ``ask`` (a comment
    :func:`comment_kind` reads as a request), ``ask_recent`` (one in the week before the day), ``mention`` (tagged someone,
    no request), ``other`` (commented, neither). Each arm has its counts, its pooled shares and the gap inside one project
    (Mantel-Haenszel risk difference, with a 95% range from resampling projects, seeded);
``weekday``
    ``by_day``: all outside pull requests at least 14 days old, the share answered within a day, by the UTC weekday the
    clock started on; ``within_project``: weekday against weekend inside projects with enough of both;
``who_answers``
    per project with at least ``MIN_ATTRIBUTED_ANSWERS`` first answers attributed to a person (a reply's writer or a merge's
    merger): the busiest person's share, how many people answered, and a histogram of that share in tenths; ``outcomes``:
    for those with at least ``MIN_OUTCOME_FIRST_PRS`` first pull requests at least 14 days old and not closed unanswered by
    their author, per band of the busiest person's share (:func:`share_band`), the median project's share answered within
    a day and within 14 days and share merged; ``below_floor``: the projects with that many first pull requests and fewer
    attributed answers than ``MIN_ATTRIBUTED_ANSWERS``, and those of them where one person gave 90% or more of the answers
    they did get; ``clear_against_shared``: the top band against the bottom one, comparing projects only inside
    one cell of ``COMPARE_STARS`` and ``COMPARE_FIRST_PRS``, with a 95% range from resampling projects, seeded;
    ``clear_against_shared_strict``: the same without projects whose busiest person is the owner's own account or the only
    person answering;
``first_pr_projects``
    per project with at least ``MIN_FIRST_PRS`` first pull requests at least 14 days old and not closed unanswered by their
    author: ``[owner/name, count, median hours to an answer, share answered within a day, within 14 days, share merged, stars]``.

Nothing here reads the wall clock, and the file is the same for any number of workers.
"""
from __future__ import annotations

import random
import re
import statistics
from bisect import bisect_right
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, NamedTuple, Optional, Sequence

from porchlamp.collect.github_graphql import derive_pr_window
from porchlamp.core import gates
from porchlamp.core.constants import MIN_ACCOUNT_AGE_DAYS, PR_WINDOW_DAYS
from porchlamp.derive.gate_evidence import _envelope_data, outside_pr_authors, pr_window_document
from porchlamp.snapshot.stats import _read_stars

#: Days of silence the waiting curve is read at.
SILENT_DAYS: tuple[int, ...] = (0, 1, 3, 7, 14, 21, 30, 45, 60)
#: How old a pull request must be for the waiting curve: at least this many days from its clock to its record's end.
SETTLED_DAYS = 120
#: The hour marks of the answered-by curve.
ANSWER_HOURS: tuple[int, ...] = (1, 6, 24, 72, 168, 336, 720, 1440, 2160)
#: The ``answered_later`` readings: unanswered at the first day, answered by the second.
LATER_DAYS: tuple[tuple[int, int], ...] = ((14, 30), (14, 60), (14, 90))
#: The days the asking comparison stands at, and how many days after each it follows a pull request.
LANDMARK_DAYS: tuple[int, ...] = (7, 14)
FOLLOW_DAYS = 30
#: How close to the landmark an ask must be to count as recent.
RECENT_DAYS = 7
#: An author's comment in the first hour is part of opening the pull request, not asking again.
ASK_AFTER_HOURS = 1
#: Bootstrap rounds and seed for the asking comparison's range.
BOOTSTRAP_ROUNDS = 1000
BOOTSTRAP_SEED = 20261104
#: A project takes part in the weekday comparison with at least this many weekday and weekend pull requests.
WEEKDAY_MIN_WEEKDAY = 30
WEEKDAY_MIN_WEEKEND = 10
#: A project takes part in ``who_answers`` with at least this many attributed first answers.
MIN_ATTRIBUTED_ANSWERS = 20
#: A project is listed in ``first_pr_projects`` with at least this many first pull requests.
MIN_FIRST_PRS = 25
#: ``who_answers`` also reads the projects at or above this many stars.
LARGE_PROJECT_STARS = 50_000
#: A project takes part in ``who_answers.outcomes`` with at least this many first pull requests at least 14 days old.
MIN_OUTCOME_FIRST_PRS = 10
#: The bands of the busiest person's share of first answers, lowest first.
SHARE_BANDS: tuple[str, ...] = ("shared", "over_half", "over_70", "one")
#: ``clear_against_shared`` compares projects only inside one cell of these bands of stars and of first pull requests.
COMPARE_STARS: tuple[int, ...] = (3_000, 10_000, 30_000)
COMPARE_FIRST_PRS: tuple[int, ...] = (15, 30, 60)
WEEKDAYS: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

#: Comments that talk to a tool or come from one: a bot command, a CLA signature, an automated review, a thank-you.
TOOL_COMMENT_RE = re.compile(
    r"(^\s*/|^\s*i have read|^\s*i hereby|^\s*recheck\b|^\s*rerun\b|^\s*retest\b"
    r"|@(coderabbit(ai)?|copilot|dependabot|renovate|claassistant|sourcery(-ai)?|codecov|gemini(-code-assist)?|cursor|greptile(ai)?|claude|codex)\b"
    r"|automated review|review pass|ensemble review|^\s*\W*\s*automated|^\s*(thanks|thank you|thx)\b)",
    re.IGNORECASE,
)
#: Words that ask a project to look at the pull request.
ASK_COMMENT_RE = re.compile(
    r"(\bping(ing)?\b|\bbump(ing)?\b|\bptal\b|any (update|updates|news|chance|interest|thoughts|feedback)\b"
    r"|status (on|of) this|gentle (reminder|follow|nudge)|friendly (reminder|nudge)"
    r"|please (review|take a look|have a look|merge)"
    r"|(can|could|would) (someone|anyone|somebody|you|a maintainer)\b.{0,40}(review|look|merge|check|approve|help)"
    r"|(can|could) i get\b|(get|got) this (reviewed|merged)|take a look|have a look"
    r"|when you (have|get) (a|the) (moment|chance|time|window)|what('s| is) (the )?(status|next)|still (waiting|relevant)"
    r"|what do you think)",
    re.IGNORECASE,
)
MENTION_RE = re.compile(r"(^|\s)@[a-z0-9][a-z0-9-]+", re.IGNORECASE)

_ARMS: tuple[str, ...] = ("ask", "ask_recent", "mention", "other")
_ASKING_READINGS: tuple[tuple[str, int, str], ...] = (("day14_first", 14, "first"), ("day14_all", 14, "all"), ("day7_first", 7, "first"))


def comment_kind(text: object) -> str:
    """What an author's comment on their own pull request does: ``"tool"``, ``"ask"``, ``"mention"`` or ``"other"``."""
    if not isinstance(text, str) or not text or TOOL_COMMENT_RE.search(text):
        return "tool"
    if ASK_COMMENT_RE.search(text):
        return "ask"
    return "mention" if MENTION_RE.search(text) else "other"


def _epoch(text: object) -> Optional[float]:
    """Seconds since the epoch of an ISO-8601 instant; ``None`` when it is not one."""
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _first_seen(history: Optional[Mapping[str, Any]], window_prs: Sequence[Mapping[str, Any]]) -> Optional[dict[str, float]]:
    """Each author's earliest pull request in an ``author_history`` record or the window, by lower-cased login: the history
    was taken before the window's end. ``None`` for no record or one cut at its cap, which cannot say who is new."""
    if history is None or history.get("truncated"):
        return None
    first: dict[str, float] = {}
    for pr in [*(history.get("prs") or []), *window_prs]:
        if not isinstance(pr, Mapping):
            continue
        author = pr.get("author")
        login = str(author.get("login") or "").lower() if isinstance(author, Mapping) else ""
        when = _epoch(pr.get("createdAt"))
        if login and when is not None and (login not in first or when < first[login]):
            first[login] = when
    return first


class _Outcome(NamedTuple):
    """How one project's first pull requests fared, beside who answers there."""

    top: float
    first_prs: int
    answered: float
    merged: float
    stars: int
    strict: bool
    in_a_day: float


def share_band(top: float) -> str:
    """The band of a busiest person's share of first answers. Exactly 90% is in the top band, like the histogram's top bin."""
    if top >= 0.9:
        return "one"
    if top > 0.7:
        return "over_70"
    return "over_half" if top > 0.5 else "shared"


@dataclass
class _Project:
    """What one project's record adds to the file; every field sums or concatenates across projects."""

    outside_prs: int = 0
    read: int = 0
    silent: dict[str, dict[int, list[int]]] = field(default_factory=lambda: {
        group: {day: [0, 0] for day in SILENT_DAYS} for group in ("first", "returning")
    })
    settled: dict[str, int] = field(default_factory=lambda: {"first": 0, "returning": 0})
    counts: dict[str, int] = field(default_factory=lambda: {"first": 0, "returning": 0})
    times: dict[str, list[tuple[float, bool]]] = field(default_factory=lambda: {"first": [], "returning": []})
    asking: dict[tuple[int, str], dict[str, list[int]]] = field(default_factory=dict)
    by_day: list[list[int]] = field(default_factory=lambda: [[0, 0] for _ in WEEKDAYS])
    weekday_pair: Optional[tuple[float, float]] = None
    who: Optional[tuple[float, int, int]] = None
    outcome: Optional[_Outcome] = None
    below_floor: Optional[tuple[float, float, bool]] = None
    first_row: Optional[list[Any]] = None


def _responder(pr: Mapping[str, Any], stamp: object, author: str, maintainers: frozenset[str]) -> Optional[str]:
    """Who wrote the response recorded at ``stamp`` (:func:`porchlamp.core.gates.first_substantive_response`'s time)."""
    for source in ("reviews", "comments"):
        connection = pr.get(source)
        nodes = connection.get("nodes") if isinstance(connection, Mapping) else None
        for node in nodes if isinstance(nodes, list) else ():
            if isinstance(node, Mapping) and node.get("createdAt") == stamp and gates.is_maintainer_response(node, author, maintainers):
                actor = node.get("author")
                login = actor.get("login") if isinstance(actor, Mapping) else None
                if isinstance(login, str) and login:
                    return login.lower()
    return None


def _author_comments(pr: Mapping[str, Any], author: str, clock: float) -> list[tuple[float, str]]:
    """The author's own comments as ``(hours after the clock, kind)``."""
    connection = pr.get("comments")
    nodes = connection.get("nodes") if isinstance(connection, Mapping) else None
    out: list[tuple[float, str]] = []
    for node in nodes if isinstance(nodes, list) else ():
        actor = node.get("author") if isinstance(node, Mapping) else None
        login = str(actor.get("login") or "").lower() if isinstance(actor, Mapping) else ""
        when = _epoch(node.get("createdAt")) if login == author else None
        if when is not None:
            out.append(((when - clock) / 3600, comment_kind(node.get("bodyText"))))
    return out


def _arms(comments: Sequence[tuple[float, str]], day: int) -> list[str]:
    """The asking arms a pull request is in at the landmark ``day``: ``none``, or one of ``ask``, ``mention`` and ``other``,
    plus ``ask_recent`` when an ask falls in the week before the day."""
    hours = day * 24
    window = [(h, kind) for h, kind in comments if ASK_AFTER_HOURS < h <= hours]
    if not window:
        return ["none"]
    asks = [h for h, kind in window if kind == "ask"]
    if asks:
        arms = ["ask"]
    elif any(kind == "mention" for _, kind in window):
        arms = ["mention"]
    else:
        arms = ["other"]
    if any(h > hours - RECENT_DAYS * 24 for h in asks):
        arms.append("ask_recent")
    return arms


def read_project(job: tuple[Path, str, float, float, Optional[int]]) -> Optional[_Project]:
    """One project's part of the file, from its ``pr_window`` and ``author_history`` records; ``None`` without a window."""
    raw_root, basename, window_start, window_end, stars = job
    document = pr_window_document(raw_root, basename)
    window = document.get("data") if isinstance(document, Mapping) else None
    prs = window.get("prs") if isinstance(window, Mapping) else None
    anchor = _epoch(window.get("window_anchor")) if isinstance(window, Mapping) else None
    if not isinstance(prs, list) or anchor is None:
        return None
    prs = [pr for pr in prs if isinstance(pr, dict)]
    out = _Project()
    start, end = datetime.fromtimestamp(window_start, tz=UTC), datetime.fromtimestamp(window_end, tz=UTC)
    out.outside_prs = len(outside_pr_authors(prs, start=start, end=end))
    derived = derive_pr_window(prs)
    sampled = {id(pr) for pr in gates.reply_sampled_prs(prs)}
    maintainers = gates.maintainer_logins(prs)
    first_seen = _first_seen(_envelope_data(raw_root, "author_history", basename), prs)
    min_age = MIN_ACCOUNT_AGE_DAYS * 86400
    who: dict[str, int] = {}
    weekday, weekend = [0, 0], [0, 0]
    first_rows: list[tuple[Optional[float], bool]] = []
    for pr, facts in zip(prs, derived):
        created = _epoch(pr.get("createdAt"))
        if created is None or not window_start <= created <= window_end:
            continue
        if id(pr) not in sampled or not gates.is_outside_pr(pr):
            continue
        author = str((pr.get("author") or {}).get("login") or "").lower()
        if author in maintainers:
            continue
        ready = _epoch(facts.get("ready_for_review_at"))
        if ready is None and facts.get("is_draft"):
            continue
        account = _epoch(facts.get("author_created_at"))
        if account is not None and created - account < min_age:
            continue
        out.read += 1
        clock = max(created, ready) if ready is not None else created
        observed = (anchor - clock) / 3600
        reply = _epoch(facts.get("first_substantive_response_at"))
        if reply is not None and reply < clock:
            # The first reply came before the clock (a draft answered before it was ready): the answer is the first one
            # at or after the clock, if any, not none at all.
            later = gates.first_substantive_response(pr, maintainers, since=datetime.fromtimestamp(clock, tz=UTC))
            reply = _epoch(later[0]) if later is not None else None
        merged_at = _epoch(pr.get("mergedAt"))
        closed_at = _epoch(pr.get("closedAt"))
        closed_by_project = _epoch(facts.get("closed_by_maintainer_at"))
        answers = sorted(
            (when, kind) for when, kind in ((reply, 0), (merged_at, 1), (closed_by_project, 2)) if when is not None and when >= clock
        )
        answer = (answers[0][0] - clock) / 3600 if answers else None
        merged = merged_at is not None
        closed_hours = (closed_at - clock) / 3600 if closed_at is not None else None
        by_author = bool(facts.get("closed_by_author"))

        if answers and answers[0][1] == 0:
            login = _responder(pr, facts.get("first_substantive_response_at"), author, maintainers)
            if login:
                who[login] = who.get(login, 0) + 1
        elif answers and answers[0][1] == 1:
            merger = pr.get("mergedBy")
            if isinstance(merger, Mapping) and not gates.is_bot_actor_node(merger) and merger.get("login"):
                login = str(merger["login"]).lower()
                who[login] = who.get(login, 0) + 1

        if observed >= 14 * 24:
            day = datetime.fromtimestamp(clock, tz=UTC).weekday()
            in_a_day = answer is not None and answer <= 24
            out.by_day[day][0] += 1
            out.by_day[day][1] += in_a_day
            bucket = weekend if day >= 5 else weekday
            bucket[0] += 1
            bucket[1] += in_a_day

        group: Optional[str] = None
        if first_seen is not None and author:
            seen = first_seen.get(author)
            group = "first" if seen is None or seen >= created - 1 else "returning"
        if group is not None:
            out.counts[group] += 1
            settled = observed >= SETTLED_DAYS * 24
            out.settled[group] += settled
            if settled:
                for at in SILENT_DAYS:
                    hours = at * 24
                    gone = by_author and closed_hours is not None and closed_hours < hours
                    if (answer is None or answer > hours) and not gone:
                        out.silent[group][at][0] += 1
                        out.silent[group][at][1] += merged
            left = closed_hours if by_author and answer is None and closed_hours is not None else observed
            out.times[group].append((max(0.0, answer if answer is not None else left), answer is not None))
        if group == "first" and observed >= 14 * 24 and not (by_author and answer is None):
            first_rows.append((answer, merged))

        comments: Optional[list[tuple[float, str]]] = None
        for landmark in LANDMARK_DAYS:
            hours = landmark * 24
            if observed < hours + FOLLOW_DAYS * 24:
                continue
            if answer is not None and answer <= hours:
                continue
            if closed_hours is not None and closed_hours <= hours:
                continue
            if comments is None:
                comments = _author_comments(pr, author, clock)
            arms = _arms(comments, landmark)
            answered = answer is not None and answer <= hours + FOLLOW_DAYS * 24
            for label in ("all", "first") if group == "first" else ("all",):
                tally = out.asking.setdefault((landmark, label), {arm: [0, 0, 0] for arm in ("none", *_ARMS)})
                for arm in arms:
                    tally[arm][0] += 1
                    tally[arm][1] += answered
                    tally[arm][2] += merged  # by the end of the data, as the page's merges sentence says

    if weekday[0] >= WEEKDAY_MIN_WEEKDAY and weekend[0] >= WEEKDAY_MIN_WEEKEND:
        out.weekday_pair = (weekday[1] / weekday[0], weekend[1] / weekend[0])
    total = sum(who.values())
    top = max(who.values()) / total if total else 0.0
    if total >= MIN_ATTRIBUTED_ANSWERS:
        out.who = (top, len(who), -1 if stars is None else stars)
    if len(first_rows) >= MIN_OUTCOME_FIRST_PRS:
        n = len(first_rows)
        answered = sum(a is not None and a <= 14 * 24 for a, _ in first_rows) / n
        merged_share = sum(m for _, m in first_rows) / n
        if total >= MIN_ATTRIBUTED_ANSWERS:
            busiest = max(sorted(who), key=lambda login: who[login])
            out.outcome = _Outcome(
                top=top,
                first_prs=n,
                answered=answered,
                merged=merged_share,
                stars=-1 if stars is None else stars,
                strict=len(who) >= 2 and busiest != basename.partition("__")[0].lower(),
                in_a_day=sum(a is not None and a <= 24 for a, _ in first_rows) / n,
            )
        else:
            out.below_floor = (answered, merged_share, share_band(top) == "one")
    if len(first_rows) >= MIN_FIRST_PRS:
        answered_hours = [a for a, _ in first_rows if a is not None]
        n = len(first_rows)
        out.first_row = [
            basename.replace("__", "/", 1),
            n,
            round(statistics.median(answered_hours), 2) if answered_hours else None,
            round(sum(a is not None and a <= 24 for a, _ in first_rows) / n, 4),
            round(sum(a is not None and a <= 14 * 24 for a, _ in first_rows) / n, 4),
            round(sum(m for _, m in first_rows) / n, 4),
            stars,
        ]
    return out


def answered_shares(times: Iterable[tuple[float, bool]], hours: Sequence[int]) -> dict[int, float]:
    """The Kaplan-Meier share answered by each of ``hours``, from ``(hours until the answer or leaving the count, answered)``.
    At one instant, answers come before departures."""
    ordered = sorted(times, key=lambda item: (item[0], not item[1]))
    at_risk = len(ordered)
    remaining = 1.0
    out: dict[int, float] = {}
    marks = sorted(hours)
    position = 0
    for mark in marks:
        while position < len(ordered) and ordered[position][0] <= mark:
            if ordered[position][1]:
                remaining *= 1 - 1 / at_risk
            at_risk -= 1
            position += 1
        out[mark] = 1 - remaining
    return out


def _group_curves(projects: Sequence[_Project], group: str) -> dict[str, Any]:
    """The ``first`` or ``returning`` part of the file."""
    silent = {day: [0, 0] for day in SILENT_DAYS}
    for project in projects:
        for day, (n, merged) in project.silent[group].items():
            silent[day][0] += n
            silent[day][1] += merged
    times = [item for project in projects for item in project.times[group]]
    marks = tuple(sorted({*ANSWER_HOURS, *(day * 24 for pair in LATER_DAYS for day in pair)}))
    shares = answered_shares(times, marks)
    later = []
    for since, until in LATER_DAYS:
        left_at_since, left_at_until = 1 - shares[since * 24], 1 - shares[until * 24]
        later.append({"from_days": since, "to_days": until, "share": round(1 - left_at_until / left_at_since, 4) if left_at_since else None})
    return {
        "settled": sum(project.settled[group] for project in projects),
        "merged_if_silent": [
            {"days": day, "n": n, "merged": round(merged / n, 4) if n else None} for day, (n, merged) in silent.items()
        ],
        "answered_by": [{"hours": mark, "share": round(shares[mark], 4)} for mark in ANSWER_HOURS],
        "answered_later": later,
    }


def adjusted_gap(rows: Sequence[tuple[Sequence[int], Sequence[int]]], outcome: int) -> float:
    """The Mantel-Haenszel risk difference between an arm and ``none``, from ``(arm tally, none tally)`` per project. A tally
    is ``[pull requests, answered, merged]``; ``outcome`` is 1 for answered, 2 for merged."""
    weighted = weights = 0.0
    for arm, none in rows:
        weight = arm[0] * none[0] / (arm[0] + none[0])
        weighted += weight * (arm[outcome] / arm[0] - none[outcome] / none[0])
        weights += weight
    return weighted / weights


def _compare(tallies: Sequence[Mapping[str, Sequence[int]]], arm: str, rng: random.Random) -> dict[str, Any]:
    """One arm against ``none``: counts over the projects with both, pooled shares, and the gap inside one project."""
    rows = [(t[arm], t["none"]) for t in tallies if t[arm][0] and t["none"][0]]
    if not rows:
        return {"projects": 0, "n": 0, "n_none": 0}
    n, n_none = sum(r[0][0] for r in rows), sum(r[1][0] for r in rows)
    out: dict[str, Any] = {
        "projects": len(rows), "n": n, "n_none": n_none,
        "answered": round(sum(r[0][1] for r in rows) / n, 4), "answered_none": round(sum(r[1][1] for r in rows) / n_none, 4),
        "merged": round(sum(r[0][2] for r in rows) / n, 4), "merged_none": round(sum(r[1][2] for r in rows) / n_none, 4),
    }
    for label, outcome in (("answered", 1), ("merged", 2)):
        rounds = sorted(adjusted_gap(rng.choices(rows, k=len(rows)), outcome) for _ in range(BOOTSTRAP_ROUNDS))
        out[f"gap_{label}"] = round(adjusted_gap(rows, outcome), 4)
        out[f"gap_{label}_range"] = [round(rounds[int(0.025 * BOOTSTRAP_ROUNDS)], 4), round(rounds[int(0.975 * BOOTSTRAP_ROUNDS)], 4)]
    return out


def _median_share(values: Sequence[float]) -> Optional[float]:
    return round(statistics.median(values), 4) if values else None


def clear_gap(rows: Sequence[_Outcome], measure: str) -> Optional[float]:
    """The mean of ``measure`` in the top band minus in the bottom band, compared only inside one cell of stars and first
    pull requests, cells weighted by how many of both they hold; ``None`` when no cell holds both."""
    cells: dict[tuple[int, int], tuple[list[float], list[float]]] = {}
    for row in rows:
        band = share_band(row.top)
        if band in ("one", "shared"):
            cell = cells.setdefault((bisect_right(COMPARE_STARS, row.stars), bisect_right(COMPARE_FIRST_PRS, row.first_prs)), ([], []))
            cell[band == "shared"].append(getattr(row, measure))
    weighted = weights = 0.0
    for ones, shared in cells.values():
        if ones and shared:
            weight = len(ones) * len(shared) / (len(ones) + len(shared))
            weighted += weight * (sum(ones) / len(ones) - sum(shared) / len(shared))
            weights += weight
    return weighted / weights if weights else None


def _clear_against_shared(rows: Sequence[_Outcome], rng: random.Random) -> dict[str, Any]:
    """The top band against the bottom one, with each gap's range from resampling those projects."""
    both = [row for row in rows if share_band(row.top) in ("one", "shared")]
    out: dict[str, Any] = {
        "projects_one": sum(share_band(row.top) == "one" for row in both),
        "projects_shared": sum(share_band(row.top) == "shared" for row in both),
    }
    for measure in ("answered", "merged"):
        gap = clear_gap(both, measure)
        rounds = sorted(v for v in (clear_gap(rng.choices(both, k=len(both)), measure) for _ in range(BOOTSTRAP_ROUNDS)) if v is not None) if gap is not None else []
        out[f"gap_{measure}"] = None if gap is None else round(gap, 4)
        out[f"gap_{measure}_range"] = [round(rounds[int(0.025 * len(rounds))], 4), round(rounds[int(0.975 * len(rounds))], 4)] if rounds else None
    return out


def _outcomes(projects: Sequence[_Project], rng: random.Random) -> dict[str, Any]:
    """``who_answers.outcomes``."""
    rows = [p.outcome for p in projects if p.outcome is not None]
    bands = []
    for band in SHARE_BANDS:
        members = [row for row in rows if share_band(row.top) == band]
        bands.append({
            "band": band, "projects": len(members), "in_a_day": _median_share([row.in_a_day for row in members]),
            "answered": _median_share([row.answered for row in members]), "merged": _median_share([row.merged for row in members]),
        })
    below = [p.below_floor for p in projects if p.below_floor is not None]
    below_one = [row for row in below if row[2]]
    return {
        "projects": len(rows),
        "bands": bands,
        "below_floor": {
            "projects": len(below), "answered": _median_share([row[0] for row in below]), "merged": _median_share([row[1] for row in below]),
            "one": {"projects": len(below_one), "answered": _median_share([row[0] for row in below_one]), "merged": _median_share([row[1] for row in below_one])},
        },
        "clear_against_shared": _clear_against_shared(rows, rng),
        "clear_against_shared_strict": _clear_against_shared([row for row in rows if row.strict], rng),
    }


def combine(projects: Sequence[_Project]) -> dict[str, Any]:
    """Everything after ``as_of`` and ``window_start``, from the projects in the order given."""
    first, returning = (sum(p.counts[g] for p in projects) for g in ("first", "returning"))
    rng = random.Random(BOOTSTRAP_SEED)
    asking: dict[str, Any] = {}
    for key, landmark, label in _ASKING_READINGS:
        tallies = [p.asking[(landmark, label)] for p in projects if (landmark, label) in p.asking]
        asking[key] = {arm: _compare(tallies, arm, rng) for arm in _ARMS}
    by_day = [[sum(p.by_day[i][j] for p in projects) for j in (0, 1)] for i in range(len(WEEKDAYS))]
    pairs = [p.weekday_pair for p in projects if p.weekday_pair is not None]
    gaps = [day - end for day, end in pairs]
    who = [p.who for p in projects if p.who is not None]
    histogram = [0] * 10
    for row in who:
        histogram[min(int(row[0] * 10), 9)] += 1
    large = [row for row in who if row[2] >= LARGE_PROJECT_STARS]
    rows = sorted((p.first_row for p in projects if p.first_row is not None), key=lambda r: r[0].lower())
    return {
        "population": {
            "projects": len(projects), "outside_prs": sum(p.outside_prs for p in projects), "read": sum(p.read for p in projects),
            "first": first, "returning": returning,
        },
        "first": _group_curves(projects, "first"),
        "returning": _group_curves(projects, "returning"),
        "asking": asking,
        "weekday": {
            "by_day": {
                name: {"n": n, "answered_in_a_day": round(a / n, 4) if n else None} for name, (n, a) in zip(WEEKDAYS, by_day)
            },
            "within_project": {
                "projects": len(pairs),
                "median_gap": _median_share(gaps),
                "weekday_better": round(sum(g > 0 for g in gaps) / len(gaps), 4) if gaps else None,
            },
        },
        "who_answers": {
            "projects": len(who),
            "over_half": round(sum(row[0] > 0.5 for row in who) / len(who), 4) if who else None,
            "median_top_share": _median_share([row[0] for row in who]),
            "median_people": statistics.median(row[1] for row in who) if who else None,
            "histogram": histogram,
            "large": {
                "min_stars": LARGE_PROJECT_STARS,
                "projects": len(large),
                "over_half": round(sum(row[0] > 0.5 for row in large) / len(large), 4) if large else None,
                "median_people": statistics.median(row[1] for row in large) if large else None,
            },
            "outcomes": _outcomes(projects, rng),
        },
        "first_pr_projects": rows,
    }


def compute_first_pull_requests(data_dir: Path, *, as_of: str, workers: int = 1) -> dict[str, Any]:
    """The stats file for the records under ``data_dir``, with the PR window ending at ``as_of``."""
    raw = Path(data_dir) / "raw"
    end = datetime.fromisoformat(as_of)
    if end.tzinfo is None:
        raise ValueError(f"--as-of has no time zone: {as_of!r}")
    start = end - timedelta(days=PR_WINDOW_DAYS)
    stars = _read_stars(raw / "github_meta")

    def jobs() -> Iterable[tuple[Path, str, float, float, Optional[int]]]:
        for path in sorted((raw / "pr_window").glob("*.json")):
            owner, _, name = path.stem.partition("__")
            yield raw, path.stem, start.timestamp(), end.timestamp(), stars.get((owner.lower(), name.lower()))

    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            read = list(pool.map(read_project, jobs(), chunksize=25))
    else:
        read = [read_project(job) for job in jobs()]
    return {"as_of": as_of, "window_start": start.isoformat(), **combine([p for p in read if p is not None])}
