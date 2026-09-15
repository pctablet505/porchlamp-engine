"""Acceptance tests for :mod:`cvi.core.hygiene` (D-IN-3).

D-IN-3 (``docs/v2/02-RANKING-DESIGN.md``): a PR author counts only if the
account is >= 90 days old at PR creation and has >= 5 public events; one
author contributes at most 20% of any rate's numerator and denominator
(excess PRs dropped, most recent first). This module tests the hygiene
filter functions and author cap enforcement directly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cvi.core.hygiene import account_eligibility, apply_author_cap, is_eligible_account

NOW = datetime(2026, 9, 12, 0, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# --- is_eligible_account: D-IN-3 boundaries ---------------------------------

def test_is_eligible_account_89_days_excluded_90_days_included():
    """Packet oracle: an 89-day-old account is excluded, 90-day-old is not."""
    assert is_eligible_account(_iso(NOW - timedelta(days=89)), _iso(NOW), 100) is False
    assert is_eligible_account(_iso(NOW - timedelta(days=90)), _iso(NOW), 5) is True


def test_is_eligible_account_4_events_excluded_5_included():
    assert is_eligible_account(_iso(NOW - timedelta(days=365)), _iso(NOW), 4) is False
    assert is_eligible_account(_iso(NOW - timedelta(days=365)), _iso(NOW), 5) is True


def test_is_eligible_account_missing_inputs_excluded():
    assert is_eligible_account(None, _iso(NOW), 100) is False
    assert is_eligible_account(_iso(NOW - timedelta(days=365)), None, 100) is False
    assert is_eligible_account(_iso(NOW - timedelta(days=365)), _iso(NOW), None) is False


def test_is_eligible_account_newer_than_pr_excluded():
    """An account created after the PR it supposedly authored is excluded
    (not just 'not old enough') -- a defensive boundary the age subtraction
    alone would not catch (a negative timedelta compares as < 90 days, but
    this is checked explicitly first)."""
    assert is_eligible_account(_iso(NOW + timedelta(days=1)), _iso(NOW), 100) is False


# --- account_eligibility: tri-state -----------------------------------------

def test_account_eligibility_measured_true_and_false():
    assert account_eligibility(_iso(NOW - timedelta(days=365)), _iso(NOW), 50, measured=True) is True
    assert account_eligibility(_iso(NOW - timedelta(days=10)), _iso(NOW), 50, measured=True) is False


def test_account_eligibility_unmeasured_is_none_regardless_of_data():
    """measured=False returns None even when the account_created_at/events
    fields happen to look like a valid, eligible account -- 'unmeasured'
    must never be inferred from what the (possibly stale) data says."""
    assert account_eligibility(_iso(NOW - timedelta(days=365)), _iso(NOW), 50, measured=False) is None
    assert account_eligibility(None, None, None, measured=False) is None


# --- apply_author_cap: the packet's own oracle -------------------------------

def test_apply_author_cap_six_first_timer_prs_capped_to_20_percent():
    """6 first-timer PRs from one author among a population of 10 are capped
    to floor(0.20 * 10) = 2 (its two oldest); the other 4 single-PR authors are unaffected."""
    items = [("sock", NOW - timedelta(days=d)) for d in (100, 101, 102, 103, 104, 105)] + \
            [(f"g{i}", NOW - timedelta(days=d)) for i, d in enumerate((110, 115, 120, 125))]
    kept = apply_author_cap(items)
    assert len(kept) == 6
    sock_kept = sorted(items[i][1] for i in kept if items[i][0] == "sock")
    assert sock_kept == [NOW - timedelta(days=105), NOW - timedelta(days=104)]
    assert sum(1 for i in kept if items[i][0].startswith("g")) == 4


def test_apply_author_cap_fraction_boundary_20_vs_22_percent():
    """floor(0.20*54) = 10 but floor(0.22*54) = 11 — a population size
    where the two cap fractions diverge, pinning the exact 20% constant
    (not merely 'some cap under 25%')."""
    items = [("sock", NOW - timedelta(days=d)) for d in range(1, 31)] + \
            [(f"g{i}", NOW - timedelta(days=100 + i)) for i in range(24)]
    assert len(items) == 54
    kept = apply_author_cap(items)
    assert len(kept) == 34
    assert sum(1 for i in kept if items[i][0] == "sock") == 10


def test_apply_author_cap_keeps_oldest_not_newest():
    items = [("sock", NOW - timedelta(days=d)) for d in (1, 2, 3, 4, 5)]  # 5 items, cap = 1
    kept = apply_author_cap(items)
    assert len(kept) == 1
    assert items[kept[0]][1] == NOW - timedelta(days=5)  # the oldest


def test_apply_author_cap_small_population_binds_when_one_author_dominates():
    """A population entirely owned by one login is still capped down to its
    allowance (``max(1, floor(0.20*4)) == 1``, the oldest survives) -- the cap
    does bind on real concentration even in a small population; it is the
    "nobody dominates" case below that must not be zeroed."""
    items = [("a", NOW - timedelta(days=d)) for d in (1, 2, 3, 4)]
    kept = apply_author_cap(items)
    assert len(kept) == 1
    assert items[kept[0]][1] == NOW - timedelta(days=4)  # the oldest
    assert apply_author_cap([]) == ()


def test_apply_author_cap_small_population_keeps_everything_when_nobody_dominates():
    """Three independent single-PR authors in a population of 3 --
    ``floor(0.20*3) == 0`` -- keep everything: nobody exceeds even a
    floor-of-1 allowance, so the cap has nothing to bind on."""
    items = [("a", NOW), ("b", NOW - timedelta(days=1)), ("c", NOW - timedelta(days=2))]
    assert apply_author_cap(items) == (0, 1, 2)
