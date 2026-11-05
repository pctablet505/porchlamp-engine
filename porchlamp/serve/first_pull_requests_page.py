"""The first-pull-requests report's data and its page's head (``scripts/export_static.py --first-pull-requests``).

The report is a page of its own, ``/first-pull-requests/``: ``web/app/views/first_pull_requests.js`` over
``data/<snapshot_id>/first-pull-requests.json``, drawn at export by ``web/prerender.mjs``. :func:`report_json` writes that
file from the stats file (:mod:`porchlamp.snapshot.first_pull_requests`). Python keeps the numbers as display strings and
the sentences the numbers decide; the view prints each number as a hook and types none.

Every sentence whose truth depends on a number stands beside the guard that keeps it true (:func:`check_claims`). A guard
that fails raises :class:`~porchlamp.serve.report_pages.ReportError` and the export stops, so a false sentence is never
written.
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping, Sequence

from porchlamp.core.constants import MIN_ACCOUNT_AGE_DAYS
from porchlamp.serve import report_pages as rp
from porchlamp.snapshot import first_pull_requests as stats_file
from porchlamp.serve.project_pages import percent_text, reply_time_text
from porchlamp.serve.report_pages import ReportError, fmt_int, long_date

#: The report's address, and its data file's name under ``data/<snapshot_id>/``.
PATH = "/first-pull-requests/"
LOOKUP_PATH = "/lookup/"
DATA_FILE = "first-pull-requests.json"
#: The projects the report names: picked by hand from the stats file's ``first_pr_projects``, each with what it is. One
#: that stops qualifying leaves the list on its own (:func:`featured_rows`), so a new snapshot needs no editor.
FEATURED_PROJECTS: tuple[tuple[str, str, str], ...] = (
    ("servo/rust-smallvec", "smallvec", "small vectors for Rust"),
    ("postcss/postcss", "PostCSS", "tool for transforming CSS"),
    ("faker-js/faker", "Faker", "fake data for tests"),
    ("freeCodeCamp/freeCodeCamp", "freeCodeCamp", "free coding curriculum"),
    ("bkaradzic/bgfx", "bgfx", "cross-platform graphics library"),
    ("pcottle/learnGitBranching", "Learn Git Branching", "interactive Git tutorial"),
    ("wolfpld/tracy", "Tracy", "frame profiler"),
    ("schollz/croc", "croc", "send files between computers"),
    ("quinn-rs/quinn", "Quinn", "QUIC in Rust"),
    ("kovidgoyal/calibre", "calibre", "e-book manager"),
)
#: What a featured project must keep to stay on the list: the list says these projects answer first-timers and merge.
FEATURED_MIN_WITHIN_A_DAY = 0.75
FEATURED_MIN_MERGED = 0.5
#: The list stops the export below this many: too few to call a list.
FEATURED_MIN_PROJECTS = 5
#: How the sentences say the list's length.
_COUNT_WORDS = {5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}
#: "Most have under 60 first pull requests" counts at this size.
FEATURED_SMALL_SAMPLE = 60
#: "the two lines nearly meet": at most this far apart after two months of silence.
LINES_MEET_WITHIN = 0.02
#: "Fame barely changes it": the large projects' share is at most this far from every project's.
FAME_WITHIN = 0.1
#: The waiting curve's reading days the page names, and the answered-by marks it names, in hours.
_DAY, _TWO_WEEKS = 24, 336
#: How the page labels the bands of the busiest person's share of first answers.
_BAND_LABELS = {"shared": "up to 50%", "over_half": "50–70%", "over_70": "70–90%", "one": "90–100%"}


def pct(share: float) -> str:
    """A share as a whole percent."""
    return percent_text(share)


def points(share: float, *, signed: bool = False) -> str:
    """A gap between two shares in percentage points, one decimal; a true minus sign below zero."""
    value = (Decimal(repr(share)) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    text = f"{abs(value):f}"
    if value < 0:
        return f"−{text}"
    return f"+{text}" if signed else text


def whole_points(share: float) -> str:
    """A gap in percentage points, rounded to a whole number."""
    return f"{(Decimal(repr(share)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)}"


def whole(number: float) -> str:
    """A count that may arrive as a float (a median of whole numbers): a half stays."""
    return fmt_int(int(number)) if float(number).is_integer() else f"{number:g}"


def _by_days(group: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    return {row["days"]: row for row in group["merged_if_silent"]}


def _by_hours(group: Mapping[str, Any]) -> dict[int, float]:
    return {row["hours"]: row["share"] for row in group["answered_by"]}


def featured_rows(stats: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The featured projects' rows that still qualify, fastest first. Too few left stops the export."""
    rows = {row[0]: row for row in stats["first_pr_projects"]}
    out = []
    for key, label, what in FEATURED_PROJECTS:
        row = rows.get(key)
        if row is None:
            continue
        _, count, median, within_a_day, _, merged, _ = row
        if median is None or within_a_day < FEATURED_MIN_WITHIN_A_DAY or merged < FEATURED_MIN_MERGED:
            continue
        out.append({
            "key": key, "name": label, "what": what, "path": f"/projects/{key.lower()}/",
            "median_hours": median, "median_text": reply_time_text(median),
            "within_a_day": within_a_day, "within_a_day_text": pct(within_a_day),
            "merged": merged, "merged_text": pct(merged), "count": count, "count_text": fmt_int(count),
        })
    if len(out) < FEATURED_MIN_PROJECTS:
        raise ReportError(f"first pull requests: only {len(out)} of the listed projects still qualify; the list needs an editor")
    return sorted(out, key=lambda row: row["median_hours"])


def check_claims(stats: Mapping[str, Any], projects: Sequence[Mapping[str, Any]]) -> None:
    """Every sentence a number decides, against the numbers."""
    first, returning = stats["first"], stats["returning"]
    curve, others = _by_days(first), _by_days(returning)
    answered, answered_others = _by_hours(first), _by_hours(returning)
    asking = stats["asking"]["day14_first"]
    ask, other = asking["ask_recent"], asking["other"]
    everyone = stats["asking"]["day14_all"]
    who = stats["who_answers"]
    weekday = stats["weekday"]
    by_day = weekday["by_day"]

    def guard(holds: bool, claim: str) -> None:
        if not holds:
            raise ReportError(f"first pull requests: the numbers no longer say {claim!r}")

    guard(stats_file.SETTLED_DAYS == 120, "at least four months old")
    guard(stats_file.FOLLOW_DAYS == 30, "in the next 30 days")
    guard(stats_file.RECENT_DAYS == 7, "in the past week")
    guard(stats_file.MIN_ATTRIBUTED_ANSWERS == 20, "at least 20 first answers")
    guard(stats_file.MIN_FIRST_PRS == 25, "at least 25 first pull requests")
    guard(stats_file.MIN_OUTCOME_FIRST_PRS == 10, "at least 10 first pull requests")
    guard(stats_file.COMPARE_STARS == (3_000, 10_000, 30_000), "under 3,000 stars, 3,000 to 10,000, 10,000 to 30,000")
    guard(stats_file.COMPARE_FIRST_PRS == (15, 30, 60), "under 15 first pull requests, 15 to 30, 30 to 60")
    guard((stats_file.WEEKDAY_MIN_WEEKDAY, stats_file.WEEKDAY_MIN_WEEKEND) == (30, 10), "at least 30 weekday and 10 weekend pull requests")
    guard(1 - answered[_TWO_WEEKS] > 0.5, "More than half of first pull requests hear nothing for two weeks")
    shares = [curve[day]["merged"] for day in sorted(curve)]
    guard(all(a > b for a, b in zip(shares, shares[1:])), "the longer the silence, the lower the odds of a merge")
    guard(curve[14]["merged"] < curve[0]["merged"] / 2, "the odds of a merge have more than halved")
    guard(abs(curve[60]["merged"] - others[60]["merged"]) <= LINES_MEET_WITHIN, "the two lines nearly meet")
    guard(ask["gap_answered_range"][0] > 0, "Authors who asked got more answers")
    guard(other["gap_answered_range"][0] < 0 < other["gap_answered_range"][1], "Other comments got none")
    guard(everyone["other"]["gap_answered_range"][0] < 0 < everyone["other"]["gap_answered_range"][1], "Other comments got none, over all outside pull requests")
    guard(ask["answered"] - ask["answered_none"] > ask["gap_answered"], "Part of that gap comes from which projects get asked")
    guard(ask["gap_merged_range"][0] > 0, "Merges follow")
    guard(stats["asking"]["day7_first"]["ask_recent"]["gap_answered"] < ask["gap_answered"], "An ask in the first week shows less")
    guard(all(answered[h] < answered_others[h] for h in answered), "The first pull request waits longest")
    guard(answered_others[_TWO_WEEKS] > answered[_TWO_WEEKS], "After two weeks the gap is still there")
    weekend = max(by_day["Sat"]["answered_in_a_day"], by_day["Sun"]["answered_in_a_day"])
    guard(all(by_day[day]["answered_in_a_day"] > weekend for day in ("Mon", "Tue", "Wed", "Thu", "Fri")), "Weekend pull requests wait longer")
    guard(weekday["within_project"]["weekday_better"] > 0.5, "weekday pull requests beat weekend ones")
    guard(who["over_half"] > 0.5, "In most open source projects, one person answers the newcomers")
    guard(who["large"]["over_half"] > 0.5, "Even at 50,000 stars, it's usually one person")
    guard(abs(who["large"]["over_half"] - who["over_half"]) <= FAME_WITHIN, "Fame barely changes it")
    outcomes = who["outcomes"]
    bands = {row["band"]: row for row in outcomes["bands"]}
    for measure in ("answered", "merged"):
        guard(all(bands[band][measure] > bands["shared"][measure] for band in ("over_half", "over_70", "one")), "That's where newcomers do best")
        guard(bands["one"][measure] > max(bands["over_half"][measure], bands["over_70"][measure]), "where newcomers do best")
        for label, gap in (("Stars and volume don't explain it", outcomes["clear_against_shared"]), ("Leaving out projects owned by the busiest person's own account", outcomes["clear_against_shared_strict"])):
            guard(gap[f"gap_{measure}_range"] is not None and gap[f"gap_{measure}_range"][0] > 0, label)
    guard(sum(row["count"] < FEATURED_SMALL_SAMPLE for row in projects) * 2 > len(projects), "Most have under 60 first pull requests")


def report_values(stats: Mapping[str, Any], projects: Sequence[Mapping[str, Any]], *, snapshot_id: str, window_end: date, window_start: date) -> dict[str, str]:
    """Every number the page prints outside its tables, as the display string its hook carries."""
    population = stats["population"]
    first, returning = stats["first"], stats["returning"]
    curve, others = _by_days(first), _by_days(returning)
    answered, answered_others = _by_hours(first), _by_hours(returning)
    later = {row["to_days"]: row["share"] for row in first["answered_later"]}
    asking, everyone = stats["asking"]["day14_first"], stats["asking"]["day14_all"]
    week_one = stats["asking"]["day7_first"]["ask_recent"]
    ask, all_ask, all_other = asking["ask_recent"], everyone["ask_recent"], everyone["other"]
    weekday, who = stats["weekday"]["within_project"], stats["who_answers"]
    known = population["first"] + population["returning"]
    bands = {row["band"]: row for row in who["outcomes"]["bands"]}
    gap, strict = who["outcomes"]["clear_against_shared"], who["outcomes"]["clear_against_shared_strict"]
    return {
        "snapshot_id": snapshot_id,
        "page_url": f"{rp.SITE}{PATH}",
        "window_start": long_date(window_start),
        "window_end": long_date(window_end),
        "first": fmt_int(population["first"]),
        "returning": fmt_int(population["returning"]),
        "outside_prs": fmt_int(population["outside_prs"]),
        "read": fmt_int(population["read"]),
        "projects": fmt_int(population["projects"]),
        "known": fmt_int(known),
        "known_share": pct(round(known / population["read"], 4)),
        "first_settled": fmt_int(first["settled"]),
        "silent_14": pct(round(1 - answered[_TWO_WEEKS], 4)),
        "merged_0": pct(curve[0]["merged"]),
        "merged_7": pct(curve[7]["merged"]),
        "merged_14": pct(curve[14]["merged"]),
        "merged_30": pct(curve[30]["merged"]),
        "merged_60": pct(curve[60]["merged"]),
        "returning_merged_0": pct(others[0]["merged"]),
        "returning_merged_60": pct(others[60]["merged"]),
        "later_30": pct(later[30]),
        "later_60": pct(later[60]),
        "later_90": pct(later[90]),
        "answered_1d": pct(answered[_DAY]),
        "answered_14d": pct(answered[_TWO_WEEKS]),
        "returning_answered_1d": pct(answered_others[_DAY]),
        "returning_answered_14d": pct(answered_others[_TWO_WEEKS]),
        "ask_answered": pct(ask["answered"]),
        "ask_answered_none": pct(ask["answered_none"]),
        "ask_gap": whole_points(ask["gap_answered"]),
        "ask_n": fmt_int(ask["n"]),
        "ask_projects": fmt_int(ask["projects"]),
        "ask_merged": pct(ask["merged"]),
        "ask_merged_none": pct(ask["merged_none"]),
        "ask_merged_gap": points(ask["gap_merged"]),
        "ask_merged_low": points(ask["gap_merged_range"][0]),
        "ask_merged_high": points(ask["gap_merged_range"][1]),
        "week_one_gap": points(week_one["gap_answered"]),
        "week_one_low": points(week_one["gap_answered_range"][0]),
        "week_one_high": points(week_one["gap_answered_range"][1]),
        "all_ask_gap": whole_points(all_ask["gap_answered"]),
        "all_ask_low": whole_points(all_ask["gap_answered_range"][0]),
        "all_ask_high": whole_points(all_ask["gap_answered_range"][1]),
        "all_ask_n": fmt_int(all_ask["n"]),
        "all_ask_projects": fmt_int(all_ask["projects"]),
        "all_other_gap": points(all_other["gap_answered"]),
        "all_other_low": points(all_other["gap_answered_range"][0]),
        "all_other_high": points(all_other["gap_answered_range"][1]),
        "weekday_better": pct(weekday["weekday_better"]),
        "weekday_projects": fmt_int(weekday["projects"]),
        "weekday_gap": whole_points(weekday["median_gap"]),
        "who_projects": fmt_int(who["projects"]),
        "over_half": pct(who["over_half"]),
        "median_people": whole(who["median_people"]),
        "large_projects": fmt_int(who["large"]["projects"]),
        "large_stars": fmt_int(who["large"]["min_stars"]),
        "large_over_half": pct(who["large"]["over_half"]),
        "large_people": whole(who["large"]["median_people"]),
        "top_bin": fmt_int(who["histogram"][-1]),
        "outcome_projects": fmt_int(who["outcomes"]["projects"]),
        "one_projects": fmt_int(bands["one"]["projects"]),
        "shared_projects": fmt_int(bands["shared"]["projects"]),
        "one_answered": pct(bands["one"]["answered"]),
        "one_merged": pct(bands["one"]["merged"]),
        "shared_answered": pct(bands["shared"]["answered"]),
        "shared_merged": pct(bands["shared"]["merged"]),
        "gap_answered": whole_points(gap["gap_answered"]),
        "gap_answered_low": whole_points(gap["gap_answered_range"][0]),
        "gap_answered_high": whole_points(gap["gap_answered_range"][1]),
        "gap_merged": whole_points(gap["gap_merged"]),
        "gap_merged_low": whole_points(gap["gap_merged_range"][0]),
        "gap_merged_high": whole_points(gap["gap_merged_range"][1]),
        "strict_answered": whole_points(strict["gap_answered"]),
        "strict_merged": whole_points(strict["gap_merged"]),
        "listed_projects": fmt_int(len(stats["first_pr_projects"])),
        "count_word": _COUNT_WORDS[len(projects)],
        "count_title": _COUNT_WORDS[len(projects)].capitalize(),
        "min_within_a_day": pct(min(row["within_a_day"] for row in projects)),
        "min_merged": pct(min(row["merged"] for row in projects)),
        "min_account_days": fmt_int(MIN_ACCOUNT_AGE_DAYS),
    }


#: The sentences a number decides, as templates the view fills with the values' hooks. Each claim has its guard in
#: :func:`check_claims`.
SENTENCES: dict[str, Any] = {
    "h1": "In most open source projects, one person answers the newcomers. That's where newcomers do best.",
    "lede": (
        "Porchlamp followed {first} first pull requests, each from someone who hadn't sent that project a pull request in about "
        "the previous six months or longer. You'd expect projects that lean on one person to be the slow ones. They aren't."
    ),
    "tiles": [
        {"label": "One person gives most first answers", "value": "over_half", "text": "of {who_projects} projects, and still {large_over_half} at {large_stars} stars or more"},
        {"label": "Merged where one person answers", "value": "one_merged", "text": "of first pull requests in the typical project, against {shared_merged} where the job is shared"},
        {"label": "Silent at two weeks, then asked", "value": "ask_answered", "text": "got an answer in the next 30 days, against {ask_answered_none} with no comment"},
        {"label": "Merged, in the end", "value": "merged_0", "text": "of first pull requests, against {returning_merged_0} for people who had sent one before"},
    ],
    "best_heading": "More first pull requests get answered and merged where one person does the answering",
    "best_text": "In the typical project where one person gives 90% or more of the first answers, {one_answered} of first pull requests get an answer within two weeks and {one_merged} get merged. Where nobody gives more than half, it's {shared_answered} and {shared_merged}.",
    "control_text": "Stars and volume don't explain it. Comparing only projects with similar stars and a similar number of first pull requests, the gap is {gap_answered} points in answers ({gap_answered_low} to {gap_answered_high}) and {gap_merged} points in merges ({gap_merged_low} to {gap_merged_high}). Leaving out projects owned by the busiest person's own account, and projects where only one person answers at all, it's {strict_answered} and {strict_merged} points.",
    "best_note": "The typical project is the median of {outcome_projects} projects with at least 20 first answers we could attribute to a person and at least 10 first pull requests at least 14 days old: {one_projects} with one person at 90% or more, {shared_projects} with nobody over half.",
    "wait_heading": "After two weeks of silence, the odds of a merge have more than halved",
    "wait_text": "More than half of first pull requests, {silent_14}, hear nothing for two weeks, and the longer the silence, the lower the odds of a merge. Each point counts pull requests at least four months old that had no answer by that day, and how many of them were merged in the end: {merged_7} after a week of silence, {merged_14} after two weeks, {merged_30} after a month, {merged_60} after two months. By then the two lines nearly meet, at {merged_60} and {returning_merged_60}.",
    "late_text": "A late answer is still possible. Of first pull requests silent at two weeks, {later_30} hear something by day 30, {later_60} by day 60 and {later_90} by day 90.",
    "wait_note": "Share merged by the end of the data, at least four months after opening, by days with no answer. {first_settled} first pull requests are old enough to count. An answer means a reply or review from the project's people, a merge, or a close by them.",
    "ask_heading": "Authors who asked got about {ask_gap} points more answers. Other comments got none.",
    "ask_text": "Of first pull requests silent at two weeks, {ask_answered} got an answer in the next 30 days, so by day 44, if the author had asked in the past week, with words like \"friendly ping\" or \"could you take a look\". With no comment at all it was {ask_answered_none}. Part of that gap comes from which projects get asked. Inside one project, about {ask_gap} points remain. A comment that didn't ask shows nothing.",
    "ask_note": "First pull requests silent at two weeks, {ask_n} with a recent ask in {ask_projects} projects. Lines show the 95% range. Across all outside pull requests the same comparison gives {all_ask_gap} points ({all_ask_low} to {all_ask_high}) for asking, from {all_ask_n} pull requests in {all_ask_projects} projects, and {all_other_gap} points ({all_other_low} to {all_other_high}) for a comment that didn't ask.",
    "merges_text": "Merges follow: {ask_merged} merged against {ask_merged_none}, or {ask_merged_gap} points inside one project ({ask_merged_low} to {ask_merged_high}). An ask in the first week shows less: {week_one_gap} points ({week_one_low} to {week_one_high}), measured at one week.",
    "first_heading": "The first pull request waits longest",
    "first_text": "Within a day, {answered_1d} of first pull requests have an answer, against {returning_answered_1d} for people the project has seen before. After two weeks the gap is still there: {answered_14d} against {returning_answered_14d}.",
    "first_note": "Share answered by each point in time, log scale, allowing for pull requests too young to tell or closed by their author. {first} first and {returning} returning pull requests.",
    "week_heading": "Weekend pull requests wait longer",
    "week_text": "Inside the same project, weekday pull requests beat weekend ones in {weekday_better} of {weekday_projects} projects, by a median of {weekday_gap} points.",
    "week_note": "All outside pull requests, days in UTC, including the busiest projects that the first and returning figures leave out, so don't compare these shares with those. The inside-a-project figure uses projects with at least 30 weekday and 10 weekend pull requests. Weekend senders may differ from weekday ones, which that comparison only partly removes.",
    "who_heading": "Even at {large_stars} stars, it's usually one person",
    "who_text": "Count who gives the first answer to each outside pull request, and the median project has {median_people} people answering at all. In {top_bin} of {who_projects} projects, one person gives at least 90% of those answers. Fame barely changes it: among {large_projects} projects with {large_stars} stars or more, one person gives most of them in {large_over_half}, with a median of {large_people} people answering.",
    "who_note": "Projects by the busiest person's share of first answers, for projects with at least 20 first answers we could attribute to a person. Bots are left out.",
    "fast_heading": "{count_title} projects that answer first-timers quickly and merge their work",
    "fast_text": "Picked by hand from the {listed_projects} projects with at least 25 first pull requests old enough to judge, so this isn't a ranking. All {count_word} answered at least {min_within_a_day} of them within a day and merged at least {min_merged}. Most have under 60 first pull requests, so read them as examples. Each link opens the project's page here.",
    "method": [
        "Data. Porchlamp's {snapshot_id} snapshot holds {outside_prs} outside pull requests opened from {window_start} to {window_end}, across {projects} projects with a record. These results read {read} of them, those with recorded replies after the rules below. An earlier post's 212,390 came from a smaller read of the same projects, before the collection was widened.",
        "Left out. Bots, project members, drafts never marked ready, and accounts under {min_account_days} days old. In very busy projects, replies are read from the snapshot's even random sample.",
        "First pull request. The author has no earlier pull request to that project in its record of the past 365 days. Every pull request here opened in the last six months, so the look-back runs from about six months to a year. Only projects whose record isn't cut off at its cap of about 500 can tell, so the busiest projects are missing. The split covers {known} of the {read} pull requests read, {known_share}.",
        "Which results use what. The waiting curve, the answered-by curve and the listed projects use first pull requests only. So do the asking figures unless a line says all outside pull requests, and the answered and merged shares beside who answers. The weekday results and who gives the first answers use all outside pull requests.",
        "Who answers. The person behind the first answer to each outside pull request: who wrote the reply or review, or who merged it. Bots are left out. A project counts once, whatever its size, so the shares beside who answers are the median project's. The comparison of similar projects uses bands of stars (under 3,000, 3,000 to 10,000, 10,000 to 30,000, 30,000 or more) and of first pull requests (under 15, 15 to 30, 30 to 60, 60 or more), and the 95% range comes from resampling projects.",
        "An answer. The earliest reply or review from the project's people, a merge, or a close by them. Replies from bots don't count, and the clock starts when a draft is marked ready.",
        "Asking. A comment from the author after the first hour, matched against a word list: ping, bump, gentle nudge, could you review, take a look, any update and similar. A hand check of 50 matches found 47 real asks. Tool commands and CLA signatures don't count. The baseline is no comment at all. The inside-one-project gap compares the groups project by project, weighted by how many each has (Mantel-Haenszel), and the 95% range comes from resampling projects. We tightened the word list after a first look at the data, so this is a result to test again on the next snapshot.",
        "Waiting curve. Pull requests at least four months old. One an author closed unanswered drops out from that day. The {silent_14} near the top uses all {first} and treats an author closing their own pull request as leaving the count, so it doesn't divide out from the curve's table.",
    ],
    "not_covered": [
        "Why a project didn't answer. Silence may mean a closed list, a mailing-list workflow, or a pull request nobody wanted.",
        "Not seen. Merges after the record's date, and any pull request's quality or size.",
        "Cause. All of this describes what happened, including who answers and the asking and weekday comparisons. Authors who ask may also care more, or have a better pull request.",
        "When that person stops. We didn't measure what happens to a project when its one answerer leaves.",
        "Six months is one season. Volume and answer rates may move with the time of year.",
    ],
    "cite": "Porchlamp. First pull requests: who answers and how long it takes. Snapshot {snapshot_id}, data from {window_start} to {window_end}. {page_url}",
    "description": "Porchlamp followed {first} first pull requests. In {over_half} of {who_projects} projects, one person gave most first answers, and newcomers did better there.",
    "title": "In most open source projects, one person answers the newcomers",
}


def _day_label(days: int) -> str:
    return "From opening" if days == 0 else ("1 day" if days == 1 else f"{days} days")


def _hour_label(hours: int) -> str:
    return {1: "1 h", 6: "6 h", 24: "1 day", 72: "3 d", 168: "1 wk", 336: "2 wk", 720: "1 mo", 1440: "2 mo", 2160: "3 mo"}[hours]


def report_json(stats: Mapping[str, Any], *, snapshot_id: str, as_of: str) -> dict[str, Any]:
    """``first-pull-requests.json``: the values, the sentences, and the rows of the page's charts and tables."""
    if stats.get("as_of") != as_of:
        raise ReportError(f"first pull requests: the stats file is for {stats.get('as_of')!r}, the snapshot's as_of is {as_of!r}")
    projects = featured_rows(stats)
    check_claims(stats, projects)
    window_start = date.fromisoformat(stats["window_start"][:10])
    window_end = date.fromisoformat(as_of[:10])
    values = report_values(stats, projects, snapshot_id=snapshot_id, window_start=window_start, window_end=window_end)
    first, returning = stats["first"], stats["returning"]
    curve, others = _by_days(first), _by_days(returning)
    answered, answered_others = _by_hours(first), _by_hours(returning)
    asking = stats["asking"]["day14_first"]
    weekdays = stats["weekday"]["by_day"]
    histogram = stats["who_answers"]["histogram"]
    bands = stats["who_answers"]["outcomes"]["bands"]
    return {
        "snapshot_id": snapshot_id,
        "as_of": as_of,
        "path": PATH,
        "data_file": f"/data/{snapshot_id}/{DATA_FILE}",
        "values": values,
        "sentences": SENTENCES,
        "tile_shares": {
            "over_half": stats["who_answers"]["over_half"], "one_merged": next(row["merged"] for row in bands if row["band"] == "one"),
            "ask_answered": asking["ask_recent"]["answered"], "merged_0": curve[0]["merged"],
        },
        "curve": [
            {
                "days": day, "label": _day_label(day),
                "first": curve[day]["merged"], "first_text": pct(curve[day]["merged"]), "first_exact": f"{points(curve[day]['merged'])}%",
                "first_n": curve[day]["n"], "first_n_text": fmt_int(curve[day]["n"]),
                "returning": others[day]["merged"], "returning_text": pct(others[day]["merged"]),
                "returning_exact": f"{points(others[day]['merged'])}%", "returning_n": others[day]["n"], "returning_n_text": fmt_int(others[day]["n"]),
            }
            for day in sorted(curve)
        ],
        "answered": [
            {
                "hours": hours, "label": _hour_label(hours),
                "first": answered[hours], "first_text": pct(answered[hours]),
                "returning": answered_others[hours], "returning_text": pct(answered_others[hours]),
            }
            for hours in sorted(answered)
        ],
        "asking": [
            {
                "label": label, "gap": asking[arm]["gap_answered"], "low": asking[arm]["gap_answered_range"][0], "high": asking[arm]["gap_answered_range"][1],
                "gap_text": points(asking[arm]["gap_answered"], signed=True), "low_text": points(asking[arm]["gap_answered_range"][0], signed=True),
                "high_text": points(asking[arm]["gap_answered_range"][1], signed=True), "count_text": fmt_int(asking[arm]["n"]), "projects_text": fmt_int(asking[arm]["projects"]),
            }
            for arm, label in (("ask_recent", "Asked in the past week"), ("mention", "Tagged someone, no request"), ("other", "Commented without asking"))
        ],
        "weekdays": [
            {"day": day, "share": row["answered_in_a_day"], "share_text": pct(row["answered_in_a_day"]), "count_text": fmt_int(row["n"]), "weekend": day in ("Sat", "Sun")}
            for day, row in weekdays.items()
        ],
        "histogram": [
            {"label": f"{i * 10}–{(i + 1) * 10}%", "projects": count, "projects_text": fmt_int(count), "top": i == len(histogram) - 1}
            for i, count in enumerate(histogram)
        ],
        "who_bands": [
            {
                "label": _BAND_LABELS[row["band"]], "top": row["band"] == "one", "projects_text": fmt_int(row["projects"]),
                "answered": row["answered"], "answered_text": pct(row["answered"]), "merged": row["merged"], "merged_text": pct(row["merged"]),
            }
            for row in bands
        ],
        "projects": [{k: row[k] for k in ("key", "name", "what", "path", "median_text", "within_a_day_text", "merged_text", "count_text")} for row in projects],
    }


def fill_text(template: str, values: Mapping[str, str]) -> str:
    """A template as plain text, for the head (the view makes each slot a hook)."""
    return template.format(**values)


def lookup_page() -> str:
    """The personal lookup's page, for ``web/prerender.mjs`` to draw the header, nav, footer and the view into: the app's
    page with its own head and its one script, loading no app."""
    title = f"{rp.copy_js_string('LOOKUP_H1')} | Porchlamp"
    description = rp.copy_js_string("LOOKUP_LEDE")
    canonical = f"{rp.SITE}{LOOKUP_PATH}"
    return rp.app_page(
        title=title, description=description, head=f'\n  <link rel="canonical" href="{rp._a(canonical)}">', scripts=rp.PAGE_SCRIPTS["lookup"][1:],
    )


def report_page(record: Mapping[str, Any], *, exported_on: date) -> str:
    """The report's page, for ``web/prerender.mjs`` to draw the header, nav, footer and the view into: the app's page with
    this report's head and its one script, loading no app."""
    title = f"{record['sentences']['title']} | Porchlamp"
    description = fill_text(record["sentences"]["description"], record["values"])
    canonical = f"{rp.SITE}{PATH}"
    built = rp.app_page(
        title=title, description=description, head=rp._article_head(title=title, description=description, canonical=canonical),
        scripts=rp.PAGE_SCRIPTS["first_pull_requests"][1:],
    )
    return rp._with_ld(built, {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "Person", "@id": rp.CREATOR_ID, "name": "Rahul Kumar", "alternateName": "pctablet505", "url": "https://github.com/pctablet505"},
            {
                "@type": "Article", "@id": f"{canonical}#article", "headline": record["sentences"]["title"], "description": description,
                "url": canonical, "mainEntityOfPage": canonical, "datePublished": exported_on.isoformat(), "dateModified": exported_on.isoformat(),
                "inLanguage": "en", "author": {"@id": rp.CREATOR_ID},
            },
        ],
    })
