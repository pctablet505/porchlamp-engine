"""Phase 1 StarScout anti-gaming filter tests.

Exact-oracle tests for ``porchlamp.starscout.filter_fraudulent_star_velocity``.
Every assertion pins the returned integer to a hand-computed value for a
hand-built stargazer population, so any drift in the 90-day window, the
throwaway-account threshold, the 500-star burst trigger, the 100:1 fork-ratio
ceiling, or the 0.30 lockstep penalty fails loudly.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from porchlamp.models import StargazerEvent
from porchlamp.starscout import StarVelocityAudit, audit_star_velocity, filter_fraudulent_star_velocity

NOW = datetime(2026, 9, 11, 12, 0, 0)


def _events(n: int, *, days_ago: float = 10.0, events: int = 100) -> list[StargazerEvent]:
    """n stargazers, all ``days_ago`` days old, each with ``events`` platform events."""
    ts = NOW - timedelta(days=days_ago)
    return [
        StargazerEvent(user_login=f"user_{i}", starred_at=ts, user_total_platform_events=events)
        for i in range(n)
    ]


def test_empty_events_returns_zero():
    assert filter_fraudulent_star_velocity([], 0, NOW) == 0


def test_single_stargazer_valid():
    evs = _events(1)
    assert filter_fraudulent_star_velocity(evs, 0, NOW) == 1


def test_single_stargazer_throwaway_discarded():
    evs = _events(1, events=4)
    assert filter_fraudulent_star_velocity(evs, 0, NOW) == 0


def test_throwaway_below_min_events_discarded():
    # 4 throwaways (4 platform events) + 1 legit (100) => 1 valid.
    evs = _events(4, events=4) + _events(1, events=100)
    assert filter_fraudulent_star_velocity(evs, 0, NOW) == 1


def test_exactly_min_events_is_valid():
    # user_total_platform_events == 5 is NOT below the threshold => valid.
    evs = _events(3, events=5)
    assert filter_fraudulent_star_velocity(evs, 0, NOW) == 3


def test_just_below_min_events_discarded():
    evs = _events(3, events=4)
    assert filter_fraudulent_star_velocity(evs, 0, NOW) == 0


def test_mixed_valid_and_throwaway():
    # 7 valid (>=5 events) + 3 throwaway (2 events) => 7.
    evs = _events(7, events=5) + _events(3, events=2)
    assert filter_fraudulent_star_velocity(evs, 0, NOW) == 7


def test_event_exactly_at_cutoff_included():
    # starred_at == now - 90d exactly; spec uses >= cutoff.
    evs = _events(1, days_ago=90.0)
    assert filter_fraudulent_star_velocity(evs, 0, NOW) == 1


def test_event_just_before_cutoff_excluded():
    evs = _events(1, days_ago=90.0 + 1 / 24)  # 90d 1h old
    assert filter_fraudulent_star_velocity(evs, 0, NOW) == 0


def test_event_just_after_cutoff_included():
    evs = _events(1, days_ago=90.0 - 1 / 24)  # 89d 23h old
    assert filter_fraudulent_star_velocity(evs, 0, NOW) == 1


def test_old_events_outside_window_excluded():
    # 5 recent + 50 ancient (200d old) => only the 5 recent count.
    evs = _events(5, days_ago=10.0) + _events(50, days_ago=200.0)
    assert filter_fraudulent_star_velocity(evs, 0, NOW) == 5


def test_exactly_500_recent_stars_no_ratio_check():
    # len(RecentStars) == 500 is NOT > 500: no fork-ratio check, no penalty,
    # even with zero forks.
    evs = _events(500, events=100)
    assert filter_fraudulent_star_velocity(evs, 0, NOW) == 500


def test_501_recent_stars_zero_forks_lockstep_penalty():
    # 501 > 500; fork_ratio = 501 / max(1, 0) = 501 > 100 => penalty.
    # int(501 * 0.30) = int(150.3) = 150.
    evs = _events(501, events=100)
    assert filter_fraudulent_star_velocity(evs, 0, NOW) == 150


def test_501_recent_stars_one_fork_lockstep_penalty():
    # fork_ratio = 501 / 1 = 501 > 100 => int(501 * 0.30) = 150.
    evs = _events(501, events=100)
    assert filter_fraudulent_star_velocity(evs, 1, NOW) == 150


def test_501_recent_stars_five_forks_lockstep_penalty():
    # fork_ratio = 501 / 5 = 100.2 > 100 => int(501 * 0.30) = 150.
    evs = _events(501, events=100)
    assert filter_fraudulent_star_velocity(evs, 5, NOW) == 150


def test_501_recent_stars_six_forks_no_penalty():
    # fork_ratio = 501 / 6 = 83.5 <= 100 => no penalty => 501.
    evs = _events(501, events=100)
    assert filter_fraudulent_star_velocity(evs, 6, NOW) == 501


def test_fork_ratio_exactly_100_no_penalty():
    # 500 valid / 5 forks = 100.0; spec requires strictly > 100.0.
    evs = _events(500, events=100)
    assert filter_fraudulent_star_velocity(evs, 5, NOW) == 500


def test_fork_ratio_just_above_100_penalty():
    # 501 valid / 5 forks = 100.2 > 100 => int(501 * 0.30) = 150.
    evs = _events(501, events=100)
    assert filter_fraudulent_star_velocity(evs, 5, NOW) == 150


def test_burst_trigger_uses_recent_count_not_valid_count():
    # 501 recent stars but only 10 valid (rest throwaway): len(RecentStars)
    # = 501 > 500 still triggers the ratio check. fork_ratio = 10 / 1 = 10
    # <= 100 => no penalty => 10.
    evs = _events(491, events=1) + _events(10, events=100)
    assert filter_fraudulent_star_velocity(evs, 1, NOW) == 10


def test_burst_trigger_with_mostly_throwaway_and_penalty():
    # 501 recent, 200 valid, 301 throwaway; forks=1 => ratio 200 > 100.
    # int(200 * 0.30) = 60.
    evs = _events(301, events=1) + _events(200, events=100)
    assert filter_fraudulent_star_velocity(evs, 1, NOW) == 60


def test_large_burst_with_many_forks_no_penalty():
    # 1000 recent, 1000 valid, 20 forks => ratio 50 <= 100 => no penalty.
    evs = _events(1000, events=100)
    assert filter_fraudulent_star_velocity(evs, 20, NOW) == 1000


def test_penalty_applied_to_valid_stars_not_recent():
    # 600 recent, 400 valid, 200 throwaway; forks=1 => ratio 400 > 100.
    # int(400 * 0.30) = 120 (penalty scales ValidStars, not RecentStars).
    evs = _events(200, events=1) + _events(400, events=100)
    assert filter_fraudulent_star_velocity(evs, 1, NOW) == 120


def test_fork_ratio_exactly_100_reachable_no_penalty():
    # 600 valid / 6 forks = 100.0 exactly AND 600 > 500 passes the burst
    # gate (the 500/5 boundary case never reaches the ratio check). Spec
    # requires strictly > 100.0, so the boundary itself takes no penalty
    # => 600.
    evs = _events(600, events=100)
    assert filter_fraudulent_star_velocity(evs, 6, NOW) == 600


# --------------------------------------------------------------------------- #
# audit_star_velocity -- the full audit behind the plain int above.          #
# Over-correction guard: every test above (filter_fraudulent_star_velocity)  #
# is untouched and still pins the same integers -- the wrapper's contract   #
# did not change.                                                            #
# --------------------------------------------------------------------------- #


def test_audit_matches_filter_for_every_scenario_above():
    """The wrapper's own contract: audit_star_velocity(...).valid_stars ==
    filter_fraudulent_star_velocity(...) for a representative sample of the
    scenarios above (no burst, burst no penalty, burst with penalty)."""
    cases = [
        ([], 0),
        (_events(1), 0),
        (_events(500, events=100), 0),
        (_events(501, events=100), 1),
        (_events(501, events=100), 6),
        (_events(301, events=1) + _events(200, events=100), 1),
    ]
    for evs, forks in cases:
        assert audit_star_velocity(evs, forks, NOW).valid_stars == filter_fraudulent_star_velocity(evs, forks, NOW)


def test_audit_no_burst_penalty_not_applied_fork_ratio_none():
    # 500 recent stars: burst gate needs > 500, so it never opens. The
    # ratio is never computed (None, not 0.0 or skipped-but-truthy).
    evs = _events(500, events=100)
    audit = audit_star_velocity(evs, 0, NOW)
    assert audit == StarVelocityAudit(valid_stars=500, recent_stars=500, fork_ratio=None, penalty_applied=False)


def test_audit_burst_gate_opens_ratio_at_or_below_threshold_no_penalty():
    # 501 > 500 opens the gate; 501 valid / 6 forks = 83.5 <= 100 => no
    # penalty, but fork_ratio is now a real computed value, not None.
    evs = _events(501, events=100)
    audit = audit_star_velocity(evs, 6, NOW)
    assert audit == StarVelocityAudit(
        valid_stars=501, recent_stars=501, fork_ratio=501 / 6, penalty_applied=False,
    )


def test_audit_penalty_fired_true_and_fork_ratio_recorded():
    # 501 recent, 1 fork => ratio 501 > 100 => penalty fires.
    evs = _events(501, events=100)
    audit = audit_star_velocity(evs, 1, NOW)
    assert audit == StarVelocityAudit(
        valid_stars=150, recent_stars=501, fork_ratio=501.0, penalty_applied=True,
    )


def test_audit_recent_stars_counts_throwaways_too():
    # recent_stars is len(RecentStars) BEFORE the throwaway filter: 100
    # throwaway + 5 valid => recent_stars=105, valid_stars=5, no burst gate.
    evs = _events(100, events=2) + _events(5, events=100)
    audit = audit_star_velocity(evs, 0, NOW)
    assert audit.recent_stars == 105
    assert audit.valid_stars == 5
    assert audit.penalty_applied is False
    assert audit.fork_ratio is None


def test_audit_same_returned_count_distinguishable_only_by_penalty_applied():
    # Two repos both give filter_fraudulent_star_velocity(...) == 100 --
    # the ambiguity audit_star_velocity exists to resolve. Repo A: 100
    # real stars, no burst -- clean growth, no penalty. Repo B: 334
    # burst-inflated valid stars (534 recent, forks=1 => ratio 334 > 100)
    # knocked down by the lockstep penalty: int(334 * 0.30) == 100.
    clean = _events(100, events=100)
    audit_clean = audit_star_velocity(clean, 0, NOW)
    assert audit_clean.valid_stars == 100
    assert audit_clean.penalty_applied is False

    burst = _events(200, events=1) + _events(334, events=100)  # 534 recent, 334 valid
    audit_burst = audit_star_velocity(burst, 1, NOW)
    assert audit_burst.valid_stars == 100  # same int filter_fraudulent_star_velocity would return
    assert audit_burst.penalty_applied is True
    assert audit_burst.recent_stars == 534
    assert audit_burst.fork_ratio == 334.0
    # The plain wrapper is genuinely blind to the difference:
    assert filter_fraudulent_star_velocity(clean, 0, NOW) == filter_fraudulent_star_velocity(burst, 1, NOW) == 100
    # Only the audit tells the two apart.
    assert audit_clean.penalty_applied != audit_burst.penalty_applied
