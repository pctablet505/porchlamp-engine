"""Adversarial tests for Pillar 1 — Responsiveness & Review Lifecycle.

Owner test bar: "make the life of the code writer hell." Every assertion pins
a parameter score (or the weighted pillar score) to a hand-computed expected
value, so any drift in bot filtering, median vs mean, first-timer scoping,
ghost age boundary, nearest-rank percentile, or weight wiring fails loudly.

Oracle summary (from ``docs/ARCHITECTURE.md`` 1.1-1.4 + ``porchlamp.constants``):
  1.1 TTFHR:    100 if median <= 12h else max(0, 100 - 25*log2(median/12)).
                Bot-filtered (author_login contains any BOT_LOGIN_MARKERS,
                case-insensitive). No qualifying PRs -> 50.0.
  1.2 MR_first: min(100, (merged/decided among external first-timers) * 125).
                No decided external first-timer PRs -> 50.0.
  1.3 GR_14:    max(0, 100 - (ghosts/total_external) * 200). Ghost = open
                (closed_at None) AND age > 14d AND 0 maintainer comments.
                No external PRs -> 50.0.
  1.4 TTM_p90:  100 - ECDF(nearest-rank p90 of external merged latency, pop).
                No merged external PRs -> 50.0.
  Pillar:       0.35*ttfhr + 0.25*mr_first + 0.20*ghost + 0.20*ttm_p90.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from porchlamp.constants import PARAM_WEIGHTS
from porchlamp.models import AuthorAssociation, RawPREvent
from porchlamp.pillars.responsiveness import (
    score_ghost_rate,
    score_mr_first,
    score_responsiveness,
    score_ttm_p90,
    score_ttfhr,
)

NOW = datetime(2026, 9, 11, 12, 0, 0)


def _pr(
    *,
    latency: float | None = None,
    login: str = "outsider",
    external: bool = True,
    created_days_ago: float = 1.0,
    merged_hours: float | None = None,
    closed_hours: float | None = None,
    first_timer: bool = True,
    comments: int = 0,
    pr_number: int = 1,
) -> RawPREvent:
    """Build a RawPREvent relative to NOW.

    ``merged_hours`` / ``closed_hours`` are offsets from ``created_at``; None
    leaves the field unset. ``first_timer=False`` sets a non-zero prior-merge
    count so ``is_first_timer`` is False.
    """
    created = NOW - timedelta(days=created_days_ago)
    return RawPREvent(
        repo_owner="acme",
        repo_name="widget",
        pr_number=pr_number,
        created_at=created,
        author_login=login,
        author_association=AuthorAssociation.NONE,
        author_prior_merged_commits=0 if first_timer else 5,
        closed_at=(created + timedelta(hours=closed_hours))
        if closed_hours is not None
        else None,
        merged_at=(created + timedelta(hours=merged_hours))
        if merged_hours is not None
        else None,
        is_external=external,
        response_latency_hours=latency,
        maintainer_review_comments=comments,
    )


# --------------------------------------------------------------------------- #
# 1.1 TTFHR — exact-oracle + boundary + bot filtering
# --------------------------------------------------------------------------- #
def test_ttfhr_exactly_12h_full():
    # median = 12.0 <= base -> flat 100.
    assert score_ttfhr([_pr(latency=12.0)]) == 100.0


def test_ttfhr_24h_seventyfive():
    # 100 - 25*log2(24/12) = 100 - 25*1 = 75.
    assert score_ttfhr([_pr(latency=24.0)]) == 75.0


def test_ttfhr_48h_fifty():
    # 100 - 25*log2(48/12) = 100 - 25*2 = 50.
    assert score_ttfhr([_pr(latency=48.0)]) == 50.0


def test_ttfhr_192h_zero():
    # 100 - 25*log2(192/12) = 100 - 25*4 = 0.
    assert score_ttfhr([_pr(latency=192.0)]) == 0.0


def test_ttfhr_384h_clamped_zero():
    # 100 - 25*log2(384/12) = 100 - 25*5 = -25 -> clamped to 0.
    assert score_ttfhr([_pr(latency=384.0)]) == 0.0


def test_ttfhr_median_even_two_values():
    # median of [12, 48] = 30.0 (mean of the two middle).
    # 100 - 25*log2(30/12) = 100 - 25*log2(2.5).
    expected = 100.0 - 25.0 * math.log2(30.0 / 12.0)
    assert score_ttfhr([_pr(latency=12.0), _pr(latency=48.0)]) == pytest.approx(expected)


def test_ttfhr_median_odd_three_values():
    # median of [12, 24, 48] = 24.0 (the middle, NOT the mean 28.0).
    # 100 - 25*log2(24/12) = 75.
    assert score_ttfhr([_pr(latency=12.0), _pr(latency=48.0), _pr(latency=24.0)]) == 75.0


def test_ttfhr_bot_dependabot_excluded():
    # dependabot[bot] carries a 192h latency that would drag the median to 102
    # (score ~22.8). Bot-filtering leaves only the 12h human PR -> 100.
    prs = [_pr(latency=192.0, login="dependabot[bot]"), _pr(latency=12.0)]
    assert score_ttfhr(prs) == 100.0


def test_ttfhr_bot_github_actions_excluded():
    # "github-actions" contains the "actions" marker -> bot.
    prs = [_pr(latency=192.0, login="github-actions"), _pr(latency=12.0)]
    assert score_ttfhr(prs) == 100.0


def test_ttfhr_bot_case_insensitive():
    # "Dependabot[Bot]" lower-cases to "dependabot[bot]" -> bot.
    prs = [_pr(latency=192.0, login="Dependabot[Bot]"), _pr(latency=12.0)]
    assert score_ttfhr(prs) == 100.0


def test_ttfhr_no_qualifying_prs_neutral():
    # Only a bot PR -> no qualifying latencies -> 50.0.
    assert score_ttfhr([_pr(latency=192.0, login="dependabot[bot]")]) == 50.0


def test_ttfhr_none_latency_excluded():
    # External non-bot PR with response_latency_hours=None -> no qualifying -> 50.0.
    assert score_ttfhr([_pr(latency=None)]) == 50.0


def test_ttfhr_internal_pr_excluded():
    # Internal PR with a 192h latency must not enter the external median.
    assert score_ttfhr([_pr(latency=192.0, external=False)]) == 50.0


# --------------------------------------------------------------------------- #
# 1.2 MR_first — ratio boundaries + first-timer / returning / external scoping
# --------------------------------------------------------------------------- #
def test_mr_first_ratio_0_8_full():
    # 4 merged + 1 closed-unmerged, all external first-timers -> 4/5 = 0.8.
    # 0.8 * 125 = 100.
    prs = [_pr(merged_hours=24, pr_number=i) for i in range(1, 5)]
    prs.append(_pr(closed_hours=24, pr_number=5))
    assert score_mr_first(prs) == 100.0


def test_mr_first_ratio_0_5():
    # 1 merged + 1 closed-unmerged -> 0.5 * 125 = 62.5.
    prs = [_pr(merged_hours=24, pr_number=1), _pr(closed_hours=24, pr_number=2)]
    assert score_mr_first(prs) == 62.5


def test_mr_first_ratio_0_zero():
    # 2 closed-unmerged, 0 merged -> 0.0 * 125 = 0.0.
    prs = [_pr(closed_hours=24, pr_number=1), _pr(closed_hours=24, pr_number=2)]
    assert score_mr_first(prs) == 0.0


def test_mr_first_ratio_1_full():
    # 1 merged first-timer -> 1.0 * 125 = 125 -> clamped to 100.
    assert score_mr_first([_pr(merged_hours=24)]) == 100.0


def test_mr_first_no_decided_neutral():
    # Open external first-timer PR (neither merged nor closed) -> no decided -> 50.0.
    assert score_mr_first([_pr()]) == 50.0


def test_mr_first_decided_but_no_first_timers_neutral():
    # Decided external PR but a returning author -> no decided first-timers -> 50.0.
    assert score_mr_first([_pr(merged_hours=24, first_timer=False)]) == 50.0


def test_mr_first_returning_author_excluded():
    # First-timer closed-unmerged + returning merged. Only the first-timer is in
    # scope: 0 merged / 1 decided = 0.0. (Counting the returning merge would
    # give 0.5 -> 62.5.)
    prs = [
        _pr(closed_hours=24, first_timer=True, pr_number=1),
        _pr(merged_hours=24, first_timer=False, pr_number=2),
    ]
    assert score_mr_first(prs) == 0.0


def test_mr_first_internal_pr_excluded():
    # External merged first-timer + internal closed-unmerged first-timer. Only the
    # external counts: 1/1 = 1.0 -> 100. (Counting the internal would give 0.5.)
    prs = [
        _pr(merged_hours=24, external=True, pr_number=1),
        _pr(closed_hours=24, external=False, pr_number=2),
    ]
    assert score_mr_first(prs) == 100.0


# --------------------------------------------------------------------------- #
# 1.3 GR_14 — ghost boundaries + age/comment/open scoping
# --------------------------------------------------------------------------- #
def test_ghost_rate_exactly_0_5_zero():
    # 1 ghost (open, 20d, 0 comments) + 1 young open (5d) -> 1/2 = 0.5.
    # 100 - 0.5*200 = 0.
    prs = [
        _pr(created_days_ago=20.0, closed_hours=None, pr_number=1),
        _pr(created_days_ago=5.0, closed_hours=None, pr_number=2),
    ]
    assert score_ghost_rate(prs, NOW) == 0.0


def test_ghost_rate_zero_full():
    # 1 young open PR (5d) -> 0 ghosts -> 100.
    prs = [_pr(created_days_ago=5.0, closed_hours=None)]
    assert score_ghost_rate(prs, NOW) == 100.0


def test_ghost_rate_exactly_14_days_not_ghost():
    # Open PR created exactly 14 days ago: age is NOT > 14d -> not a ghost -> 100.
    prs = [_pr(created_days_ago=14.0, closed_hours=None)]
    assert score_ghost_rate(prs, NOW) == 100.0


def test_ghost_rate_one_comment_not_ghost():
    # Open, 20d old, but 1 maintainer comment -> not a ghost -> 100.
    prs = [_pr(created_days_ago=20.0, closed_hours=None, comments=1)]
    assert score_ghost_rate(prs, NOW) == 100.0


def test_ghost_rate_no_external_neutral():
    # Only an internal PR -> no external PRs -> 50.0.
    prs = [_pr(created_days_ago=20.0, closed_hours=None, external=False)]
    assert score_ghost_rate(prs, NOW) == 50.0


def test_ghost_rate_mixed_quarter():
    # 4 external: 1 ghost (open,20d,0c), 1 young (open,5d,0c), 1 commented
    # (open,20d,2c), 1 closed (20d,0c). 1/4 = 0.25 -> 100 - 50 = 50.
    prs = [
        _pr(created_days_ago=20.0, closed_hours=None, pr_number=1),
        _pr(created_days_ago=5.0, closed_hours=None, pr_number=2),
        _pr(created_days_ago=20.0, closed_hours=None, comments=2, pr_number=3),
        _pr(created_days_ago=20.0, closed_hours=1.0, pr_number=4),
    ]
    assert score_ghost_rate(prs, NOW) == 50.0


def test_ghost_rate_merged_recent_not_ghost():
    # Merged PR with closed_at unset counts as "open" but is 1d old -> not a
    # ghost -> 100. Documents the closed_at-None-is-open edge case.
    prs = [_pr(created_days_ago=1.0, merged_hours=24, closed_hours=None)]
    assert score_ghost_rate(prs, NOW) == 100.0


# --------------------------------------------------------------------------- #
# 1.4 TTM_p90 — nearest-rank percentile + ECDF + external/merged scoping
# --------------------------------------------------------------------------- #
def test_ttm_p90_no_merged_neutral():
    # Open external PR, nothing merged -> 50.0.
    assert score_ttm_p90([_pr()], [1.0, 2.0]) == 50.0


def test_ttm_p90_empty_population_neutral():
    # 1 merged external PR (24h), empty population -> ECDF 50.0 -> 100 - 50 = 50.
    prs = [_pr(merged_hours=24)]
    assert score_ttm_p90(prs, []) == 50.0


def test_ttm_p90_nearest_rank_ten():
    # 10 merged external PRs, latencies 1..10h. nearest-rank p90 = ceil(0.9*10)
    # = 9th value = 9.0. ECDF(9.0, [5,9,15]) = 2/3 -> 66.67. score = 33.33.
    prs = [_pr(merged_hours=float(i), pr_number=i) for i in range(1, 11)]
    expected = 100.0 - round(200.0 / 3.0, 2)  # 100 - 66.67 = 33.33
    assert score_ttm_p90(prs, [5.0, 9.0, 15.0]) == pytest.approx(expected)


def test_ttm_p90_single_pr():
    # N=1 -> p90 = the only value (10h). ECDF(10, [10]) = 100 -> score 0.
    prs = [_pr(merged_hours=10)]
    assert score_ttm_p90(prs, [10.0]) == 0.0


def test_ttm_p90_internal_excluded():
    # 2 external merged (10h, 20h) + 1 internal merged (100h). p90 over external
    # [10,20] = 2nd = 20.0. ECDF(20, [15,50]) = 1/2 -> 50.0 -> score 50.0.
    # (Including the internal 100h would push p90 to 100 -> score 0.)
    prs = [
        _pr(merged_hours=10, external=True, pr_number=1),
        _pr(merged_hours=20, external=True, pr_number=2),
        _pr(merged_hours=100, external=False, pr_number=3),
    ]
    assert score_ttm_p90(prs, [15.0, 50.0]) == 50.0


def test_ttm_p90_corrupt_negative_latency_neutral():
    # FAIL-BEFORE (2026-09-12 adversarial review): merged_at < created_at is
    # out-of-domain telemetry. Before the guard, a negative latency sorted to
    # the bottom -> ECDF percentile 0 -> a spurious PERFECT 100 on corrupt
    # data. Corrupt samples are now dropped; all-corrupt -> neutral 50.0.
    prs = [_pr(merged_hours=-5.0, pr_number=1)]
    assert score_ttm_p90(prs, [1.0, 2.0]) == 50.0


def test_ttm_p90_corrupt_samples_dropped_not_p90():
    # FAIL-BEFORE: 1 valid merged PR at 10h + 9 corrupt at -100h. Before the
    # guard the sorted list put the corrupt values at rank 9 (ceil(0.9*10)),
    # so p90 = -100 -> ECDF(-100,[1,2]) = 0 -> a spurious PERFECT 100. After
    # the guard only the valid sample remains: p90 = 10 -> ECDF(10,[1,2]) =
    # 100 -> score 0.0 (the one real PR is genuinely slow vs the population).
    prs = [_pr(merged_hours=-100.0, pr_number=i) for i in range(1, 10)]
    prs.append(_pr(merged_hours=10.0, pr_number=99))
    assert score_ttm_p90(prs, [1.0, 2.0]) == 0.0


# --------------------------------------------------------------------------- #
# Weighted pillar combination
# --------------------------------------------------------------------------- #
def test_weighted_sum_hand_computed():
    # Single external non-bot first-timer PR: latency 24h, merged & closed at
    # +24h, created 1d ago, 0 comments. population=[24.0].
    #   ttfhr    = 100 - 25*log2(24/12) = 75.0
    #   mr_first = 1/1 * 125 -> 100.0
    #   ghost    = closed_at set -> not open -> 0 ghosts -> 100.0
    #   ttm_p90  = p90 24.0, ECDF(24,[24]) = 100 -> 0.0
    #   pillar   = 0.35*75 + 0.25*100 + 0.20*100 + 0.20*0 = 71.25
    prs = [
        _pr(
            latency=24.0,
            merged_hours=24.0,
            closed_hours=24.0,
            created_days_ago=1.0,
            comments=0,
        )
    ]
    result = score_responsiveness(prs, [24.0], NOW)
    w = PARAM_WEIGHTS["responsiveness"]
    expected = (
        w["ttfhr"] * 75.0
        + w["mr_first"] * 100.0
        + w["ghost_rate"] * 100.0
        + w["ttm_p90"] * 0.0
    )
    assert expected == pytest.approx(71.25)
    assert result == pytest.approx(71.25)


def test_empty_events_all_neutral_50():
    # No PRs, empty population -> every parameter is the 50.0 neutral, so the
    # weighted sum is 50.0 * (0.35+0.25+0.20+0.20) = 50.0.
    assert score_responsiveness([], [], NOW) == 50.0
