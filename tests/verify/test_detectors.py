"""Acceptance tests for :mod:`cvi.verify.detectors` (packet W3-2).

Fail-before: before this packet ``cvi.verify.detectors`` does not exist, so
every test below fails on ``ModuleNotFoundError`` (recorded verbatim in the
handoff).

Every oracle below is written against the **literal** 03 section 6 numbers
(40, 90, 20, 10, 0.30, 5, 7 days, 50, 10, 0.5, 4) rather than the module's
own constants, plus one dedicated test (``test_frozen_thresholds_match_03_section_6``)
pinning each constant to that literal. This is deliberate, not decorative:
an earlier draft built its fixtures from the imported constant names (e.g.
``previous_score = current_score - PARAM_JUMP_THRESHOLD_POINTS``), so
changing ``PARAM_JUMP_THRESHOLD_POINTS`` from 40.0 to 50.0 and rerunning
``pytest -k param_jump`` still passed all seven tests -- the oracle had
silently become "whatever the constant currently says" instead of "the
spec's 40". Recorded in the handoff verbatim (fail-before proof #2). This
file fixes that: every fixture number below is a bare literal.

One oracle per detector at its exact threshold on both sides (fire and
no-fire), the unmeasured-never-fires rule for every input a detector reads,
``previous=None`` behaviour, determinism (sorted, input-order independent),
and a healthy-record over-correction guard (nothing fires on good data).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cvi.core.models import GateResult, ParamScore, PRWindow, PRWindowEvent, RepoIdentity, ScoreRecord
from cvi.verify.detectors import (
    _measured_n,
    _measured_score,
    CLOSED_UNREAD_SHARE_MIN,
    DETECTOR_NAMES,
    DetectorInputs,
    EF_DOMAIN_MISMATCH_EF_MIN,
    EF_DOMAIN_SHARE_MIN,
    LABEL_SPAM_CONVERSION_SCORE_MAX,
    LABEL_SPAM_ISSUE_COUNT_MIN,
    MERGES_CONVERSION_OR_RETURN_SCORE_MAX,
    MERGES_MR_FIRST_SCORE_MIN,
    MERGES_WITHOUT_RETURN_MIN_N,
    NEWCOMER_BURST_MIN_ACCOUNTS,
    NEWCOMER_BURST_WINDOW,
    NewcomerAccountSample,
    PARAM_JUMP_MIN_N,
    PARAM_JUMP_THRESHOLD_POINTS,
    SPEED_DR90_SCORE_MAX,
    SPEED_TTFHR_SCORE_MIN,
    closed_unread_share_from_pr_window,
    detect_closed_unread,
    detect_ef_domain_mismatch,
    detect_label_spam,
    detect_merges_without_return,
    detect_newcomer_burst,
    detect_param_jump,
    detect_source_drift,
    detect_speed_without_decisions,
    detect_star_burst,
    run_detectors,
)

_HASH64 = "a" * 64
_REPO = RepoIdentity(
    owner="org",
    name="alpha",
    ecosystem="pypi",
    archived=False,
    mirror_url=None,
    fork=False,
    license_spdx="MIT",
    default_branch="main",
)


def test_frozen_thresholds_match_03_section_6() -> None:
    """R-2/R-1: pin every constant to the literal 03 section 6 number, so a
    change to any of them is a visible, deliberate diff in this test."""
    assert PARAM_JUMP_THRESHOLD_POINTS == 40.0
    assert PARAM_JUMP_MIN_N == 10
    assert SPEED_TTFHR_SCORE_MIN == 90.0
    assert SPEED_DR90_SCORE_MAX == 40.0
    assert MERGES_MR_FIRST_SCORE_MIN == 90.0
    assert MERGES_CONVERSION_OR_RETURN_SCORE_MAX == 20.0
    assert MERGES_WITHOUT_RETURN_MIN_N == 10
    assert CLOSED_UNREAD_SHARE_MIN == 0.30
    assert NEWCOMER_BURST_MIN_ACCOUNTS == 5
    assert NEWCOMER_BURST_WINDOW == timedelta(days=7)
    assert LABEL_SPAM_ISSUE_COUNT_MIN == 50.0
    assert LABEL_SPAM_CONVERSION_SCORE_MAX == 10.0
    assert EF_DOMAIN_SHARE_MIN == 0.5
    assert EF_DOMAIN_MISMATCH_EF_MIN == 4.0
    assert len(DETECTOR_NAMES) == 9


def _param(score: float | None, *, measured: bool = True, n: int = 50) -> ParamScore:
    return ParamScore(score=score, measured=measured, reason=None if measured else "insufficient_n", n=n, method="primary" if measured else None)


def _record(params: dict[str, ParamScore] | None = None) -> ScoreRecord:
    """A minimal ``ScoreRecord`` carrying only ``params`` meaningfully: every
    detector in this module reads ``.params`` (and nothing else off a
    ``ScoreRecord``), so the surrounding fields are neutral placeholders."""
    return ScoreRecord(
        repo=_REPO,
        snapshot_id="s0",
        benchmark_version="2.0.0-dev",
        constants_hash=_HASH64,
        raw_hash=_HASH64,
        gate=GateResult(eligible=True, reason=None, routed=None, undecided=()),
        params=dict(params or {}),
        pillars={},
        composite=None,
        coverage=0.0,
        tier=None,
        leverage=None,
        friction=None,
        quadrant=None,
        persona_gates={},
        persona_composites={},
        tie_key=(),
        tie_group=None,
        rank=None,
        suspicion=[],
    )


def _iso(dt: datetime) -> str:
    return dt.isoformat()


BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# param_jump: literal 40 points, literal n=10                                 #
# --------------------------------------------------------------------------- #


def test_param_jump_fires_over_40_points_both_n_ok() -> None:
    current = _record({"ttfhr_hours": _param(90.0, n=10)})
    previous = _record({"ttfhr_hours": _param(49.99, n=10)})  # 40.01-point move
    assert detect_param_jump(current, previous, DetectorInputs()) is True


def test_param_jump_no_fire_at_exactly_40_points() -> None:
    current = _record({"ttfhr_hours": _param(90.0, n=10)})
    previous = _record({"ttfhr_hours": _param(50.0, n=10)})  # exactly 40-point move
    assert detect_param_jump(current, previous, DetectorInputs()) is False


def test_param_jump_no_fire_when_n_below_10_on_current_side() -> None:
    current = _record({"ttfhr_hours": _param(90.0, n=9)})
    previous = _record({"ttfhr_hours": _param(0.0, n=10)})
    assert detect_param_jump(current, previous, DetectorInputs()) is False


def test_param_jump_no_fire_when_n_below_10_on_previous_side() -> None:
    current = _record({"ttfhr_hours": _param(90.0, n=10)})
    previous = _record({"ttfhr_hours": _param(0.0, n=9)})
    assert detect_param_jump(current, previous, DetectorInputs()) is False


def test_param_jump_fires_at_exactly_n_10_both_sides() -> None:
    """The n=10 floor is inclusive ('n >= 10'): exactly 10 on both sides
    still lets the jump fire."""
    current = _record({"ttfhr_hours": _param(90.0, n=10)})
    previous = _record({"ttfhr_hours": _param(0.0, n=10)})
    assert detect_param_jump(current, previous, DetectorInputs()) is True


def test_param_jump_no_fire_when_previous_is_none() -> None:
    current = _record({"ttfhr_hours": _param(100.0, n=10)})
    assert detect_param_jump(current, None, DetectorInputs()) is False


def test_param_jump_unmeasured_current_never_fires() -> None:
    current = _record({"ttfhr_hours": _param(None, measured=False, n=10)})
    previous = _record({"ttfhr_hours": _param(0.0, n=10)})
    assert detect_param_jump(current, previous, DetectorInputs()) is False


def test_param_jump_unmeasured_previous_never_fires() -> None:
    current = _record({"ttfhr_hours": _param(100.0, n=10)})
    previous = _record({"ttfhr_hours": _param(None, measured=False, n=10)})
    assert detect_param_jump(current, previous, DetectorInputs()) is False


# --------------------------------------------------------------------------- #
# speed_without_decisions: literal TTFHR >= 90, DR_90 <= 40                    #
# --------------------------------------------------------------------------- #


def test_speed_without_decisions_fires_at_threshold() -> None:
    current = _record({
        "ttfhr_hours": _param(90.0),
        "dr90_decision_rate": _param(40.0),
    })
    assert detect_speed_without_decisions(current, None, DetectorInputs()) is True


def test_speed_without_decisions_no_fire_just_below_ttfhr() -> None:
    current = _record({
        "ttfhr_hours": _param(89.99),
        "dr90_decision_rate": _param(40.0),
    })
    assert detect_speed_without_decisions(current, None, DetectorInputs()) is False


def test_speed_without_decisions_no_fire_just_above_dr90() -> None:
    current = _record({
        "ttfhr_hours": _param(90.0),
        "dr90_decision_rate": _param(40.01),
    })
    assert detect_speed_without_decisions(current, None, DetectorInputs()) is False


def test_speed_without_decisions_unmeasured_ttfhr_never_fires() -> None:
    current = _record({
        "ttfhr_hours": _param(None, measured=False),
        "dr90_decision_rate": _param(40.0),
    })
    assert detect_speed_without_decisions(current, None, DetectorInputs()) is False


def test_speed_without_decisions_unmeasured_dr90_never_fires() -> None:
    current = _record({
        "ttfhr_hours": _param(90.0),
        "dr90_decision_rate": _param(None, measured=False),
    })
    assert detect_speed_without_decisions(current, None, DetectorInputs()) is False


# --------------------------------------------------------------------------- #
# B2 (REWORK round 1): the measured=False guard must hold even when a       #
# scored-but-unmeasured ParamScore carries a real score. Every fixture      #
# above pairs measured=False with score=None, so a mutant that deletes the  #
# ``not param.measured`` guard in _measured_score/_measured_n (leaving only #
# the None-score check) survived undetected -- and this shape is reachable #
# today: cvi.audit.detectors_report.load_rows builds exactly              #
# ParamScore(score=95.0, measured=False, ...) straight from a JSON row     #
# {"score": 95.0, "measured": false, ...} (_param_score_from_json reads    #
# "score" and "measured" independently, with no cross-check between them). #
# --------------------------------------------------------------------------- #


def test_measured_score_and_measured_n_ignore_a_scored_but_unmeasured_param() -> None:
    scored_but_unmeasured = ParamScore(score=95.0, measured=False, reason="collector_failure", n=30, method=None)
    current = _record({"ttfhr_hours": scored_but_unmeasured})
    assert _measured_score(current, "ttfhr_hours") is None
    assert _measured_n(current, "ttfhr_hours") is None


def test_speed_without_decisions_scored_but_unmeasured_ttfhr_never_fires() -> None:
    """Same shape as ``test_speed_without_decisions_unmeasured_ttfhr_never_fires``
    but with a real, qualifying score attached to the unmeasured TTFHR --
    the ``measured`` flag, not the score, must be what stops the fire."""
    current = _record({
        "ttfhr_hours": ParamScore(score=95.0, measured=False, reason="collector_failure", n=30, method=None),
        "dr90_decision_rate": _param(40.0),
    })
    assert detect_speed_without_decisions(current, None, DetectorInputs()) is False


# --------------------------------------------------------------------------- #
# merges_without_return: literal MR_first >= 90, conversion/return <= 20,     #
# n >= 10                                                                      #
# --------------------------------------------------------------------------- #


def test_merges_without_return_fires_via_conversion_at_threshold() -> None:
    current = _record({
        "mr_first_rate": _param(90.0),
        "newcomer_conversion": _param(20.0, n=10),
        "rr90_return_rate": _param(100.0, n=10),
    })
    assert detect_merges_without_return(current, None, DetectorInputs()) is True


def test_merges_without_return_fires_via_return_at_threshold() -> None:
    current = _record({
        "mr_first_rate": _param(90.0),
        "newcomer_conversion": _param(100.0, n=10),
        "rr90_return_rate": _param(20.0, n=10),
    })
    assert detect_merges_without_return(current, None, DetectorInputs()) is True


def test_merges_without_return_no_fire_just_below_mr_first() -> None:
    current = _record({
        "mr_first_rate": _param(89.99),
        "newcomer_conversion": _param(20.0, n=10),
        "rr90_return_rate": _param(100.0, n=10),
    })
    assert detect_merges_without_return(current, None, DetectorInputs()) is False


def test_merges_without_return_no_fire_just_above_both_conversion_and_return() -> None:
    current = _record({
        "mr_first_rate": _param(90.0),
        "newcomer_conversion": _param(20.01, n=10),
        "rr90_return_rate": _param(20.01, n=10),
    })
    assert detect_merges_without_return(current, None, DetectorInputs()) is False


def test_merges_without_return_no_fire_when_n_below_10_on_both_sides() -> None:
    current = _record({
        "mr_first_rate": _param(90.0),
        "newcomer_conversion": _param(0.0, n=9),
        "rr90_return_rate": _param(0.0, n=9),
    })
    assert detect_merges_without_return(current, None, DetectorInputs()) is False


def test_merges_without_return_fires_at_exactly_n_10() -> None:
    current = _record({
        "mr_first_rate": _param(90.0),
        "newcomer_conversion": _param(20.0, n=10),
        "rr90_return_rate": _param(100.0, n=10),
    })
    assert detect_merges_without_return(current, None, DetectorInputs()) is True


def test_merges_without_return_unmeasured_mr_first_never_fires() -> None:
    current = _record({
        "mr_first_rate": _param(None, measured=False),
        "newcomer_conversion": _param(0.0, n=10),
        "rr90_return_rate": _param(0.0, n=10),
    })
    assert detect_merges_without_return(current, None, DetectorInputs()) is False


def test_merges_without_return_unmeasured_conversion_and_return_never_fires() -> None:
    current = _record({
        "mr_first_rate": _param(90.0),
        "newcomer_conversion": _param(None, measured=False),
        "rr90_return_rate": _param(None, measured=False),
    })
    assert detect_merges_without_return(current, None, DetectorInputs()) is False


def test_merges_without_return_mr_first_has_no_n_floor() -> None:
    """(REWORK round 1, U-1) The 03 section 6 row's "with n >= 10" is
    implemented as binding the *triggering* side (conversion or return)
    only, never MR_first itself -- MR_first's own n can be arbitrarily
    small and the detector still fires. Pins the current interpretation so
    a change either way is a visible, deliberate diff pending a lead
    ruling on U-1; genuinely undecided, not equivalent (a future ruling
    that binds MR_first too changes this test's expected result)."""
    current = _record({
        "mr_first_rate": _param(90.0, n=1),
        "newcomer_conversion": _param(20.0, n=10),
        "rr90_return_rate": _param(100.0, n=10),
    })
    assert detect_merges_without_return(current, None, DetectorInputs()) is True


# --------------------------------------------------------------------------- #
# closed_unread: literal share >= 0.30                                        #
# --------------------------------------------------------------------------- #


def test_closed_unread_fires_at_exactly_030() -> None:
    inputs = DetectorInputs(closed_unread_share=0.30, closed_unread_n=100)
    assert detect_closed_unread(_record(), None, inputs) is True


def test_closed_unread_no_fire_just_below_030() -> None:
    inputs = DetectorInputs(closed_unread_share=0.299, closed_unread_n=100)
    assert detect_closed_unread(_record(), None, inputs) is False


def test_closed_unread_unmeasured_never_fires() -> None:
    inputs = DetectorInputs(closed_unread_share=None)
    assert detect_closed_unread(_record(), None, inputs) is False


def _pr_event(pr_number: int, *, is_external: bool, closed: bool, merged: bool, responded: bool) -> PRWindowEvent:
    created = BASE
    return PRWindowEvent(
        pr_number=pr_number,
        created_at=_iso(created),
        closed_at=_iso(created + timedelta(days=1)) if (closed or merged) else None,
        merged_at=_iso(created + timedelta(days=1)) if merged else None,
        author_login=f"author{pr_number}",
        author_association="NONE",
        is_external=is_external,
        first_human_review_at=_iso(created + timedelta(hours=1)) if responded else None,
    )


def test_closed_unread_share_from_pr_window_counts_closed_unmerged_unanswered() -> None:
    """(B3, REWORK round 1) The population is external PRs only: a 5th,
    internal event that would ALSO match "closed, unmerged, unanswered" is
    included here specifically to prove ``is_external`` filters it out of
    both the numerator and the denominator -- dropping that filter (M21)
    would change this fixture's result from (0.25, 4) to (0.4, 5), and on
    the real sample changes 22 of 51 flagged repos' shares (one,
    ``KeygraphHQ/shannon``, flips 0.7143 fired -> 0.2333 not fired)."""
    events = (
        _pr_event(1, is_external=True, closed=True, merged=False, responded=False),  # counts
        _pr_event(2, is_external=True, closed=True, merged=False, responded=True),   # answered: not counted
        _pr_event(3, is_external=True, closed=True, merged=True, responded=False),   # merged: not counted
        _pr_event(4, is_external=True, closed=False, merged=False, responded=False),  # still open: not counted
        _pr_event(5, is_external=False, closed=True, merged=False, responded=False),  # internal: excluded entirely
    )
    window = PRWindow(start=_iso(BASE), end=_iso(BASE + timedelta(days=180)), truncated=False, events=events)
    share, n = closed_unread_share_from_pr_window(window)
    assert (share, n) == (0.25, 4)


def test_closed_unread_share_from_pr_window_no_external_prs_is_none() -> None:
    window = PRWindow(start=None, end=None, truncated=False, events=())
    assert closed_unread_share_from_pr_window(window) == (None, 0)


# --------------------------------------------------------------------------- #
# newcomer_burst: literal >= 5 accounts within a 7-day span                   #
# --------------------------------------------------------------------------- #


def _accounts(n: int, *, span: timedelta, all_merged: bool = True) -> tuple[NewcomerAccountSample, ...]:
    step = timedelta(0) if n == 1 else span / (n - 1)
    return tuple(
        NewcomerAccountSample(
            login=f"newcomer{i}",
            account_created_at=_iso(BASE + step * i),
            all_prs_merged=all_merged,
        )
        for i in range(n)
    )


def test_newcomer_burst_fires_at_5_accounts_just_inside_7_days() -> None:
    inputs = DetectorInputs(newcomer_accounts=_accounts(5, span=timedelta(days=7) - timedelta(seconds=1)))
    assert detect_newcomer_burst(_record(), None, inputs) is True


def test_newcomer_burst_no_fire_at_5_accounts_exactly_7_days() -> None:
    inputs = DetectorInputs(newcomer_accounts=_accounts(5, span=timedelta(days=7)))
    assert detect_newcomer_burst(_record(), None, inputs) is False


def test_newcomer_burst_no_fire_at_4_accounts() -> None:
    inputs = DetectorInputs(newcomer_accounts=_accounts(4, span=timedelta(days=1)))
    assert detect_newcomer_burst(_record(), None, inputs) is False


def test_newcomer_burst_excludes_accounts_with_unmerged_prs() -> None:
    """5 accounts in a tight window, but only 4 are all-merged -> no fire
    (an account whose PRs did not all merge does not count -- it is not
    'measured to have failed the burst', it simply is not part of the
    eligible set)."""
    merged = _accounts(4, span=timedelta(days=1), all_merged=True)
    not_all_merged = NewcomerAccountSample(
        login="strayauthor", account_created_at=_iso(BASE + timedelta(hours=1)), all_prs_merged=False
    )
    inputs = DetectorInputs(newcomer_accounts=merged + (not_all_merged,))
    assert detect_newcomer_burst(_record(), None, inputs) is False


def test_newcomer_burst_empty_never_fires() -> None:
    assert detect_newcomer_burst(_record(), None, DetectorInputs()) is False


# --------------------------------------------------------------------------- #
# star_burst                                                                   #
# --------------------------------------------------------------------------- #


def test_star_burst_fires_when_penalty_applied() -> None:
    inputs = DetectorInputs(star_lockstep_penalty_applied=True)
    assert detect_star_burst(_record(), None, inputs) is True


def test_star_burst_no_fire_when_penalty_not_applied() -> None:
    inputs = DetectorInputs(star_lockstep_penalty_applied=False)
    assert detect_star_burst(_record(), None, inputs) is False


def test_star_burst_unmeasured_never_fires() -> None:
    inputs = DetectorInputs(star_lockstep_penalty_applied=None)
    assert detect_star_burst(_record(), None, inputs) is False


# --------------------------------------------------------------------------- #
# label_spam: literal issues >= 50, conversion <= 10                          #
# --------------------------------------------------------------------------- #


def test_label_spam_fires_at_threshold() -> None:
    current = _record({"newcomer_conversion": _param(10.0)})
    inputs = DetectorInputs(newcomer_issue_count=50)
    assert detect_label_spam(current, None, inputs) is True


def test_label_spam_no_fire_just_below_issue_count() -> None:
    current = _record({"newcomer_conversion": _param(10.0)})
    inputs = DetectorInputs(newcomer_issue_count=49)
    assert detect_label_spam(current, None, inputs) is False


def test_label_spam_no_fire_just_above_conversion() -> None:
    current = _record({"newcomer_conversion": _param(10.01)})
    inputs = DetectorInputs(newcomer_issue_count=50)
    assert detect_label_spam(current, None, inputs) is False


def test_label_spam_unmeasured_issue_count_never_fires() -> None:
    current = _record({"newcomer_conversion": _param(10.0)})
    inputs = DetectorInputs(newcomer_issue_count=None)
    assert detect_label_spam(current, None, inputs) is False


def test_label_spam_unmeasured_conversion_never_fires() -> None:
    current = _record({"newcomer_conversion": _param(None, measured=False)})
    inputs = DetectorInputs(newcomer_issue_count=50)
    assert detect_label_spam(current, None, inputs) is False


# --------------------------------------------------------------------------- #
# ef_domain_mismatch: literal share >= 0.5, EF >= 4                           #
# --------------------------------------------------------------------------- #


def test_ef_domain_mismatch_fires_at_threshold() -> None:
    inputs = DetectorInputs(top_email_domain_share=0.5, elephant_factor=4)
    assert detect_ef_domain_mismatch(_record(), None, inputs) is True


def test_ef_domain_mismatch_no_fire_just_below_share() -> None:
    inputs = DetectorInputs(top_email_domain_share=0.49, elephant_factor=4)
    assert detect_ef_domain_mismatch(_record(), None, inputs) is False


def test_ef_domain_mismatch_no_fire_just_below_ef() -> None:
    inputs = DetectorInputs(top_email_domain_share=0.5, elephant_factor=3)
    assert detect_ef_domain_mismatch(_record(), None, inputs) is False


def test_ef_domain_mismatch_unmeasured_domain_share_never_fires() -> None:
    inputs = DetectorInputs(top_email_domain_share=None, elephant_factor=4)
    assert detect_ef_domain_mismatch(_record(), None, inputs) is False


def test_ef_domain_mismatch_unmeasured_ef_never_fires() -> None:
    inputs = DetectorInputs(top_email_domain_share=0.5, elephant_factor=None)
    assert detect_ef_domain_mismatch(_record(), None, inputs) is False


# --------------------------------------------------------------------------- #
# source_drift                                                                 #
# --------------------------------------------------------------------------- #


def test_source_drift_fires_when_held() -> None:
    assert detect_source_drift(_record(), None, DetectorInputs(source_drift_held=True)) is True


def test_source_drift_no_fire_when_not_held() -> None:
    assert detect_source_drift(_record(), None, DetectorInputs(source_drift_held=False)) is False


def test_source_drift_unmeasured_never_fires() -> None:
    assert detect_source_drift(_record(), None, DetectorInputs(source_drift_held=None)) is False


# --------------------------------------------------------------------------- #
# run_detectors: aggregation, previous=None, determinism, over-correction      #
# --------------------------------------------------------------------------- #


def test_run_detectors_previous_none_only_same_snapshot_detectors_fire() -> None:
    """A same-snapshot detector still fires with ``previous=None``; the
    cross-snapshot one (``param_jump``, the only detector reading
    ``previous``) does not -- there is nothing to compare against."""
    current = _record({
        "ttfhr_hours": _param(100.0),
        "dr90_decision_rate": _param(0.0),
    })
    result = run_detectors(current, None, DetectorInputs())
    assert result == ["speed_without_decisions"]


def test_run_detectors_sorted_and_input_order_independent() -> None:
    current = _record({
        "ttfhr_hours": _param(100.0),
        "dr90_decision_rate": _param(0.0),
        "mr_first_rate": _param(100.0),
        "newcomer_conversion": _param(0.0, n=20),
        "rr90_return_rate": _param(0.0, n=20),
    })
    inputs_a = DetectorInputs(star_lockstep_penalty_applied=True, source_drift_held=True)
    result_a = run_detectors(current, None, inputs_a)
    assert result_a == sorted(result_a)
    assert result_a == ["merges_without_return", "source_drift", "speed_without_decisions", "star_burst"]

    # Rebuild the same facts through a differently-ordered dict / a second,
    # independently constructed DetectorInputs -- input order must not
    # change the (sorted) result.
    reordered_params = dict(reversed(list(current.params.items())))
    current_b = _record(reordered_params)
    inputs_b = DetectorInputs(source_drift_held=True, star_lockstep_penalty_applied=True)
    result_b = run_detectors(current_b, None, inputs_b)
    assert result_b == result_a


def test_run_detectors_healthy_record_flags_nothing() -> None:
    """Over-correction guard: a repo with strong-but-plausible scores across
    the board, and no suspicious raw facts, triggers no detector at all."""
    current = _record({
        "ttfhr_hours": _param(70.0, n=40),
        "dr90_decision_rate": _param(75.0, n=40),
        "mr_first_rate": _param(60.0, n=40),
        "newcomer_conversion": _param(55.0, n=20),
        "rr90_return_rate": _param(50.0, n=20),
    })
    previous = _record({
        "ttfhr_hours": _param(65.0, n=40),
        "dr90_decision_rate": _param(72.0, n=40),
        "mr_first_rate": _param(58.0, n=40),
        "newcomer_conversion": _param(53.0, n=20),
        "rr90_return_rate": _param(49.0, n=20),
    })
    inputs = DetectorInputs(
        newcomer_issue_count=3,
        elephant_factor=6,
        top_email_domain_share=0.2,
        closed_unread_share=0.05,
        closed_unread_n=40,
        star_lockstep_penalty_applied=False,
        source_drift_held=False,
    )
    assert run_detectors(current, previous, inputs) == []


def test_detector_names_constant_matches_run_detectors_universe() -> None:
    """R-2: ``DETECTOR_NAMES`` is the one definition of the detector set;
    every name ``run_detectors`` can produce is drawn from it."""
    current = _record({
        "ttfhr_hours": _param(100.0),
        "dr90_decision_rate": _param(0.0),
    })
    result = run_detectors(current, None, DetectorInputs(star_lockstep_penalty_applied=True, source_drift_held=True))
    assert set(result) <= set(DETECTOR_NAMES)
