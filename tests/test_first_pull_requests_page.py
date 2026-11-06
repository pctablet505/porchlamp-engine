"""The first-pull-requests report's record: its numbers as display strings, and the guards that keep its sentences true."""
from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pytest

from porchlamp.serve import first_pull_requests_page as page
from porchlamp.serve.report_pages import ReportError

AS_OF = "2026-10-24T18:10:00+00:00"
FIXTURE = Path(__file__).resolve().parent.parent / "web" / "app" / "tests" / "cases" / "first_pull_requests.json"


def group(merged: dict[int, float], answered: dict[int, float]) -> dict:
    return {
        "settled": 1000,
        "merged_if_silent": [{"days": day, "n": 1000 - day, "merged": share} for day, share in merged.items()],
        "answered_by": [{"hours": hours, "share": share} for hours, share in answered.items()],
        "answered_later": [{"from_days": 14, "to_days": 30, "share": 0.1}, {"from_days": 14, "to_days": 60, "share": 0.2}, {"from_days": 14, "to_days": 90, "share": 0.3}],
    }


def arm(gap: float, low: float, high: float, *, answered: float = 0.3, none: float = 0.1, merged_low: float = 0.01) -> dict:
    return {
        "projects": 200, "n": 300, "n_none": 4000, "answered": answered, "answered_none": none, "merged": 0.2, "merged_none": 0.1,
        "gap_answered": gap, "gap_answered_range": [low, high], "gap_merged": 0.05, "gap_merged_range": [merged_low, 0.1],
    }


def gap(answered: float, answered_low: float, answered_high: float, merged: float, merged_low: float, merged_high: float) -> dict:
    return {
        "projects_one": 705, "projects_shared": 152, "gap_answered": answered, "gap_answered_range": [answered_low, answered_high],
        "gap_merged": merged, "gap_merged_range": [merged_low, merged_high],
    }


def good_stats() -> dict:
    """Numbers under which every sentence of the report holds."""
    hours = (1, 6, 24, 72, 168, 336, 720, 1440, 2160)
    silent = {0: 0.33, 1: 0.26, 3: 0.22, 7: 0.17, 14: 0.13, 21: 0.11, 30: 0.09, 45: 0.06, 60: 0.05}
    silent_returning = {0: 0.58, 1: 0.42, 3: 0.35, 7: 0.26, 14: 0.18, 21: 0.15, 30: 0.12, 45: 0.08, 60: 0.06}
    asking = {"ask": arm(0.04, 0.01, 0.07), "ask_recent": arm(0.09, 0.05, 0.14), "mention": arm(0.06, 0.03, 0.1), "other": arm(0.0, -0.02, 0.02)}
    week_one = {"ask": arm(0.02, -0.01, 0.05), "ask_recent": arm(0.02, -0.01, 0.05), "mention": arm(0.07, 0.03, 0.1), "other": arm(0.0, -0.02, 0.02)}
    return {
        "as_of": AS_OF,
        "window_start": "2026-04-27T18:10:00+00:00",
        "population": {"projects": 10998, "outside_prs": 1726945, "read": 845907, "first": 99875, "returning": 104810},
        "first": group(silent, dict(zip(hours, (0.06, 0.14, 0.25, 0.34, 0.42, 0.48, 0.54, 0.59, 0.62)))),
        "returning": group(silent_returning, dict(zip(hours, (0.17, 0.29, 0.44, 0.54, 0.63, 0.68, 0.73, 0.77, 0.79)))),
        "asking": {"day14_first": asking, "day14_all": copy.deepcopy(asking), "day7_first": week_one},
        "weekday": {
            "by_day": {day: {"n": 1000, "answered_in_a_day": share} for day, share in zip(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"), (0.51, 0.51, 0.52, 0.51, 0.44, 0.35, 0.39))},
            "within_project": {"projects": 2490, "median_gap": 0.0732, "weekday_better": 0.71},
        },
        "who_answers": {
            "projects": 3170, "over_half": 0.72, "median_top_share": 0.75, "median_people": 3.0,
            "histogram": [18, 100, 209, 277, 263, 318, 286, 233, 280, 1186],
            "large": {"min_stars": 50000, "projects": 274, "over_half": 0.67, "median_people": 5.0},
            "outcomes": {
                "projects": 1290,
                "bands": [
                    {"band": "shared", "projects": 152, "in_a_day": 0.25, "answered": 0.6364, "merged": 0.409},
                    {"band": "over_half", "projects": 212, "in_a_day": 0.3, "answered": 0.7142, "merged": 0.48},
                    {"band": "over_70", "projects": 221, "in_a_day": 0.3, "answered": 0.726, "merged": 0.4808},
                    {"band": "one", "projects": 705, "in_a_day": 0.33, "answered": 0.7407, "merged": 0.5556},
                ],
                "below_floor": {"projects": 903, "answered": 0.214, "merged": 0.15, "one": {"projects": 549, "answered": 0.296, "merged": 0.214}},
                "clear_against_shared": gap(0.091, 0.0443, 0.1392, 0.1328, 0.0958, 0.17),
                "clear_against_shared_strict": gap(0.0892, 0.03, 0.1478, 0.1326, 0.083, 0.1829),
            },
        },
        "first_pr_projects": [
            [key, 30, 1.0 + i, 0.9, 0.95, 0.7, 1000] for i, (key, _, _) in enumerate(page.FEATURED_PROJECTS)
        ],
    }


def record(stats: dict | None = None) -> dict:
    return page.report_json(stats or good_stats(), snapshot_id="2026-11-02", as_of=AS_OF)


@pytest.mark.parametrize("share, text, signed", [
    (0.0933, "9.3", "+9.3"), (-0.0033, "−0.3", "−0.3"), (0.0005, "0.1", "+0.1"), (0.0, "0.0", "+0.0"), (0.1396, "14.0", "+14.0"),
])
def test_points_round_half_up_with_a_true_minus(share: float, text: str, signed: str) -> None:
    assert page.points(share) == text
    assert page.points(share, signed=True) == signed


def test_the_number_formats() -> None:
    assert page.pct(0.1296) == "13%"
    assert page.whole_points(-0.027) == "−3"
    assert page.whole_points(-0.001) == "0"
    assert page.whole_points(0.0933) == "9"
    assert page.whole(3.0) == "3"
    assert page.whole(2.5) == "2.5"
    assert page.whole(1200.0) == "1,200"


def test_the_record_carries_the_numbers_as_display_strings() -> None:
    values = record()["values"]
    assert values["silent_14"] == "52%"
    assert values["merged_0"] == "33%"
    assert (values["over_half"], values["large_over_half"]) == ("72%", "67%")
    assert (values["one_merged"], values["shared_merged"], values["one_answered"], values["shared_answered"]) == ("56%", "41%", "74%", "64%")
    assert (values["gap_merged"], values["gap_merged_low"], values["gap_merged_high"]) == ("13", "10", "17")
    assert (values["gap_answered"], values["gap_answered_low"], values["gap_answered_high"]) == ("9", "4", "14")
    assert (values["strict_merged"], values["strict_answered"]) == ("13", "9")
    assert (values["strict_merged_low"], values["strict_merged_high"], values["strict_answered_low"], values["strict_answered_high"]) == ("8", "18", "3", "15")
    assert (values["strict_one"], values["strict_shared"]) == ("705", "152")
    assert (values["below_projects"], values["below_answered"], values["below_one_projects"], values["below_one_answered"]) == ("903", "21%", "549", "30%")
    assert (values["outcome_projects"], values["one_projects"], values["shared_projects"]) == ("1,290", "705", "152")
    assert values["ask_answered"] == "30%"
    assert values["ask_gap"] == "9"
    assert values["first"] == "99,875"
    assert values["known"] == "204,685"
    assert values["known_share"] == "24%"
    assert values["window_start"] == "27 April 2026"
    assert values["window_end"] == "24 October 2026"
    assert values["page_url"] == "https://porchlamp.org/first-pull-requests/"


def test_every_slot_of_every_sentence_has_a_value() -> None:
    built = record()
    values = built["values"]
    def strings(node):
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict):
            for item in node.values():
                yield from strings(item)
        elif isinstance(node, list):
            for item in node:
                yield from strings(item)
    for sentence in strings(dict(built["sentences"])):
        sentence.format(**values)


def test_the_projects_come_fastest_first_with_lower_case_addresses() -> None:
    stats = good_stats()
    stats["first_pr_projects"].reverse()
    projects = record(stats)["projects"]
    assert [p["key"] for p in projects] == [key for key, _, _ in page.FEATURED_PROJECTS]
    assert all(p["path"] == p["path"].lower() for p in projects)
    assert projects[0]["median_text"] == "1 hour"


def test_the_shared_view_fixture_has_the_records_shape() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    built = record()
    assert set(fixture) == set(built)
    assert set(fixture["values"]) == set(built["values"])
    assert fixture["sentences"] == built["sentences"]


def test_a_stats_file_for_another_day_is_refused() -> None:
    with pytest.raises(ReportError, match="as_of"):
        page.report_json(good_stats(), snapshot_id="x", as_of="2026-10-25T00:00:00+00:00")


def test_the_who_bands_rows_run_lowest_first_with_the_top_one_marked() -> None:
    bands = record()["who_bands"]
    assert [b["label"] for b in bands] == ["up to 50%", "50–70%", "70–90%", "90–100%"]
    assert [b["top"] for b in bands] == [False, False, False, True]
    assert (bands[3]["merged_text"], bands[3]["answered_text"], bands[3]["projects_text"]) == ("56%", "74%", "705")


def test_a_featured_project_that_stops_qualifying_leaves_the_list_and_the_words_follow() -> None:
    gone = good_stats()
    dropped = gone["first_pr_projects"].pop(0)[0]
    built = record(gone)
    assert dropped not in [p["key"] for p in built["projects"]]
    assert built["values"]["count_title"] == "Nine"
    assert built["values"]["count_word"] == "nine"
    slow = good_stats()
    slow["first_pr_projects"][0][3] = page.FEATURED_MIN_WITHIN_A_DAY - 0.01
    assert len(record(slow)["projects"]) == 9
    unmerged = good_stats()
    unmerged["first_pr_projects"][0][5] = page.FEATURED_MIN_MERGED - 0.01
    assert len(record(unmerged)["projects"]) == 9
    full = record()
    assert full["values"]["count_title"] == "Ten"


def test_too_few_featured_projects_left_stops_the_export() -> None:
    few = good_stats()
    del few["first_pr_projects"][page.FEATURED_MIN_PROJECTS - 1:]
    with pytest.raises(ReportError, match="needs an editor"):
        record(few)


def set_merged(stats: dict, name: str, day: int, share: float) -> None:
    next(row for row in stats[name]["merged_if_silent"] if row["days"] == day)["merged"] = share


def set_answered(stats: dict, name: str, hours: int, share: float) -> None:
    next(row for row in stats[name]["answered_by"] if row["hours"] == hours)["share"] = share


def _break_half(stats): set_answered(stats, "first", 336, 0.5)
def _break_rising(stats): set_merged(stats, "first", 60, 0.095)
def _break_cut(stats):
    set_merged(stats, "first", 0, 0.25)
    set_merged(stats, "first", 1, 0.24)
def _break_meet(stats): set_merged(stats, "returning", 60, 0.08)
def _break_ask(stats): stats["asking"]["day14_first"]["ask_recent"]["gap_answered_range"][0] = -0.01
def _break_other(stats): stats["asking"]["day14_first"]["other"]["gap_answered_range"][0] = 0.001
def _break_other_all(stats): stats["asking"]["day14_all"]["other"]["gap_answered_range"][1] = -0.001
def _break_pooled(stats): stats["asking"]["day14_first"]["ask_recent"]["answered"] = 0.12
def _break_merges(stats): stats["asking"]["day14_first"]["ask_recent"]["gap_merged_range"][0] = -0.01
def _break_week_one(stats): stats["asking"]["day7_first"]["ask_recent"]["gap_answered"] = 0.2
def _break_longest(stats): set_answered(stats, "first", 24, 0.5)
def _break_weekend(stats): stats["weekday"]["by_day"]["Sat"]["answered_in_a_day"] = 0.6
def _break_weekday_inside(stats): stats["weekday"]["within_project"]["weekday_better"] = 0.5
def _break_most(stats): stats["who_answers"]["over_half"] = 0.5
def _break_fame(stats): stats["who_answers"]["large"]["over_half"] = 0.6
def _break_large(stats): stats["who_answers"]["large"]["over_half"] = 0.5
def set_band(stats, band, measure, share): next(row for row in stats["who_answers"]["outcomes"]["bands"] if row["band"] == band)[measure] = share
def _break_merged_there(stats): set_band(stats, "one", "merged", 0.40)
def _break_answers_lean(stats): set_band(stats, "one", "answered", 0.60)
def _break_slow_ones(stats): set_band(stats, "one", "in_a_day", 0.2)
def _break_runs_higher(stats): set_band(stats, "shared", "merged", 0.3)
def _break_gap_merged(stats): stats["who_answers"]["outcomes"]["clear_against_shared"]["gap_merged_range"][0] = 0.0
def _break_gap_none(stats): stats["who_answers"]["outcomes"]["clear_against_shared"]["gap_merged_range"] = None
def _break_strict_merged(stats): stats["who_answers"]["outcomes"]["clear_against_shared_strict"]["gap_merged_range"][0] = -0.01
def _break_smaller(stats): stats["who_answers"]["outcomes"]["clear_against_shared"]["gap_answered"] = 0.2
def _break_quiet(stats): stats["who_answers"]["outcomes"]["below_floor"]["one"]["answered"] = 0.8
def _break_small_samples(stats):
    for row in stats["first_pr_projects"]:
        row[1] = 100


def test_the_answers_gap_may_reach_zero_and_the_middle_bands_may_lead_without_stopping_the_export() -> None:
    stats = good_stats()
    outcomes = stats["who_answers"]["outcomes"]
    outcomes["clear_against_shared_strict"]["gap_answered_range"][0] = -0.03
    set_band(stats, "over_70", "answered", 0.76)
    built = record(stats)
    assert built["values"]["strict_answered_low"] == "−3"


def test_the_first_screen_says_each_thing_once() -> None:
    tile = page.SENTENCES["tiles"][0]
    assert tile["text"] == "of {who_projects} projects"
    assert "{top_bin}" in page.SENTENCES["who_heading"] and "{who_projects}" in page.SENTENCES["who_heading"]
    assert "{top_bin}" not in page.SENTENCES["who_text"]
    assert "{large_over_half}" in page.SENTENCES["who_text"]


def test_the_lede_says_the_fact_with_no_set_up_and_no_claim_the_look_back_breaks() -> None:
    lede = page.SENTENCES["lede"]
    assert "They aren't" not in lede and "You'd expect" not in lede
    assert "at least six months" not in lede, "the shortest look-back is about 180 days"
    assert "about six months or more" in lede
    assert "within a day at least as often" in lede


def test_the_method_has_no_stale_or_stray_lines() -> None:
    words = json.dumps(page.SENTENCES)
    assert "near the top" not in words
    assert "212,390" not in words
    assert "An earlier post" not in words
    assert any("in the waiting section" in line for line in page.SENTENCES["method"])


def test_the_tiles_say_which_merged_they_mean_and_the_note_gives_both_counts() -> None:
    labels = [t["label"] for t in page.SENTENCES["tiles"]]
    assert "Merged where one person gives 90% or more" in labels
    assert "Merged, counting every pull request" in labels
    note = page.SENTENCES["best_note"]
    assert "Each project counts once" in note
    assert "{answered_14d}" in note and "{merged_0}" in note


def test_the_project_table_says_why_its_numbers_differ_from_the_project_pages() -> None:
    assert "Each link opens" not in page.SENTENCES["fast_text"]
    assert "differs from the number on the project's own page" in page.SENTENCES["fast_text"]


def test_gaps_are_percentage_points_and_ranges_say_what_they_are() -> None:
    control = page.SENTENCES["control_text"]
    assert control.count("percentage points") >= 2 and control.count("95% range") >= 2
    assert "percentage points" in page.SENTENCES["week_text"]
    assert "percentage points" in page.SENTENCES["ask_heading"]


def test_the_charts_labels_are_words_that_read_on_their_own() -> None:
    assert page._day_label(0) == "0 days"
    assert page._hour_label(72) == "3 days"
    assert "From opening" not in json.dumps(record())


def test_the_record_carries_no_tile_shares() -> None:
    assert "tile_shares" not in record()


def test_the_page_says_merged_not_best_and_no_comment_means_after_the_first_hour() -> None:
    words = json.dumps(page.SENTENCES)
    assert "do best" not in words
    assert "Other comments got none" not in words
    assert "no comment at all" not in words
    assert "merged more often there" in page.SENTENCES["h1"]


@pytest.mark.parametrize("breaks, claim", [
    (_break_half, "More than half"),
    (_break_rising, "lower the odds"),
    (_break_cut, "more than halved"),
    (_break_meet, "nearly meet"),
    (_break_ask, "asked got more"),
    (_break_other, "Other comments got none"),
    (_break_other_all, "over all outside"),
    (_break_pooled, "Part of that gap"),
    (_break_merges, "Merges follow"),
    (_break_week_one, "first week shows less"),
    (_break_longest, "waits longest"),
    (_break_weekend, "Weekend pull requests"),
    (_break_weekday_inside, "beat weekend"),
    (_break_most, "one person"),
    (_break_large, "Even at 50,000 stars"),
    (_break_fame, "Fame barely"),
    (_break_merged_there, "merged more often there"),
    (_break_answers_lean, "lean the same way"),
    (_break_slow_ones, "within a day at least as often"),
    (_break_runs_higher, "run higher"),
    (_break_gap_merged, "Stars and volume"),
    (_break_gap_none, "Stars and volume"),
    (_break_strict_merged, "Leaving out projects"),
    (_break_smaller, "smaller and less sure"),
    (_break_quiet, "few answers"),
    (_break_small_samples, "under 60"),
])
def test_a_sentence_the_numbers_no_longer_support_stops_the_export(breaks, claim: str) -> None:
    record()  # the good numbers pass
    stats = good_stats()
    breaks(stats)
    with pytest.raises(ReportError, match=claim):
        record(stats)


def test_the_page_head_is_the_reports_and_loads_one_script_of_its_own() -> None:
    html = page.report_page(record(), exported_on=date(2026, 11, 2))
    assert "<title>In most open source projects we measured, one person gives most of the first answers | Porchlamp</title>" in html
    assert '<link rel="canonical" href="https://porchlamp.org/first-pull-requests/">' in html
    assert 'src="/first-pull-requests-page.js"' in html
    assert 'src="/app.js"' not in html
    assert '"@type": "Article"' in html or '"@type":"Article"' in html
    description = page.fill_text(record()["sentences"]["description"], record()["values"])
    assert len(description) <= 160


def test_the_lookup_page_loads_its_own_script_and_no_app() -> None:
    html = page.lookup_page()
    assert "<title>Look up your pull requests | Porchlamp</title>" in html
    assert 'src="/lookup-page.js"' in html
    assert 'src="/app.js"' not in html
    assert '<link rel="canonical" href="https://porchlamp.org/lookup/">' in html
