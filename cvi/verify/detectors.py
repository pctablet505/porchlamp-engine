"""Cross-snapshot manipulation detectors (packet W3-2, ``03`` section 6).

Nine pure predicates, one per row of the ``03-ANTI-MANIPULATION.md`` section
6 table, plus :func:`run_detectors` which evaluates all nine and returns the
sorted list of names the caller stores on ``ScoreRecord.suspicion`` (04
section 3). Detectors never penalise a score directly (``03`` section 2
principle 5 / section 6: "flags ... never silent penalties") -- they are
read-only over ``(current, previous, inputs)`` and change nothing.

Design: each ``detect_<name>`` function takes the current
:class:`~cvi.core.models.ScoreRecord`, the previous one (``None`` at the
first snapshot) and a :class:`DetectorInputs` bundle of the raw facts no
``ScoreRecord`` carries, and returns a plain ``bool`` -- fired or not.
:func:`run_detectors` is the one place that turns those nine booleans into
the ``list[str]`` the store contract names. This module never edits
``cvi/core/engine.py`` -- wiring ``run_detectors`` into the scoring call
that produces ``ScoreRecord.suspicion`` is an ownership request to W2-8
(the constants-freeze/store-wiring packet), recorded in this packet's
handoff rather than done here (R-10: this packet owns
``cvi/verify/detectors.py`` and ``cvi/audit/detectors_report.py`` only).

**The unmeasured-never-fires rule.** A detector needs *measured* evidence on
every input it reads: an ``Unmeasured``/unscored parameter, a missing
population, or a ``None`` :class:`DetectorInputs` field never contributes to
a detector firing. Concretely: a ``ParamScore`` with ``measured=False`` or
``score=None`` is treated exactly like "no evidence" (skipped, not treated
as 0 or as a pass); a ``None`` field on :class:`DetectorInputs` means "not
collected for this repo" and the detector that reads it returns ``False``.
This mirrors D-MISS-1 ("absence is not neutral for repo-controlled
signals") applied to the detector layer: hiding a signal cannot manufacture
a false accusation either. Every ``detect_*`` docstring below repeats which
of its own inputs this applies to, and ``tests/verify/test_detectors.py``
has one dedicated case per input proving the unmeasured value does not fire.

**Named inputs this packet cannot source yet (ownership requests).** Two
of the nine detectors still need a raw fact no landed collector or bundle
field currently carries (a third, ``newcomer_burst``, was closed by packet
W3-1 — kept in this list for its history). Rather than approximate a
missing fact from something else, each open one reads its own explicitly
named :class:`DetectorInputs` field that defaults to ``None`` (never
fires) until the fact exists:

* ``newcomer_burst`` — CLOSED (packet W3-1). ``NewcomerAuthor`` now carries
  ``account_created_at`` (``cvi/core/models.py``), threaded through from
  the collector's already-fetched per-author ``user.createdAt``
  (``cvi.collect.github_graphql``) and ``cvi.derive.bundle``'s
  load/dump round-trip. :func:`newcomer_accounts_from_cohorts` builds real
  :class:`NewcomerAccountSample` tuples straight from a landed
  ``FeatureBundle.cohorts.newcomer_authors`` — this dataclass is kept as
  the detector's own input shape (not replaced by ``NewcomerAuthor``
  itself) because the detector's ``all_prs_merged`` fact is a derived
  boolean, not a stored one; wiring ``newcomer_accounts_from_cohorts``'s
  output into ``DetectorInputs`` at a real call site is still W2-8/W2-9's
  wiring request (this module's ``run_detectors`` is pure, R-10).
* ``star_burst`` needs whether the StarScout lockstep penalty
  (``cvi/starscout.py``, v1) was applied to this repo at universe entry
  (``DetectorInputs.star_lockstep_penalty_applied``). No v2 schema field
  carries this fact today (``RepoIdentity`` has no stars-related field).
  Ownership request: expose it as a stored per-repo boolean from the
  universe-build step (``scripts/build_universe.py`` / D-IN-5) so a real
  caller can populate this field instead of leaving it ``None``.
* ``ef_domain_mismatch`` needs the top commit email-domain's share of a
  repo's human commits (``DetectorInputs.top_email_domain_share``).
  ``cvi.collect.commits.elephant_factor`` (W1-5) computes the *count* of
  orgs reaching 50% of commits but never the top org's own share, and no
  bundle input carries it. Ownership request: have the W1-5 collector (or
  its deriver hook) additionally compute and store the top-domain share
  alongside ``elephant_factor`` so a real caller can populate this field.

``closed_unread`` and ``source_drift`` are *not* ownership requests: the
first is computed directly from ``FeatureBundle.pr_window`` (already landed,
W1-3) by :func:`closed_unread_share_from_pr_window`, and the second is a
plain pass-through of the W3-3 live-sample canary's per-repo hold verdict,
supplied by whichever caller has that ``LiveSampleReport`` in hand.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from cvi.core.models import Cohorts, PRWindow, ScoreRecord

# --------------------------------------------------------------------------- #
# Detector names (03 section 6 table, in table order)                         #
# --------------------------------------------------------------------------- #

DETECTOR_PARAM_JUMP: str = "param_jump"
DETECTOR_SPEED_WITHOUT_DECISIONS: str = "speed_without_decisions"
DETECTOR_MERGES_WITHOUT_RETURN: str = "merges_without_return"
DETECTOR_CLOSED_UNREAD: str = "closed_unread"
DETECTOR_NEWCOMER_BURST: str = "newcomer_burst"
DETECTOR_STAR_BURST: str = "star_burst"
DETECTOR_LABEL_SPAM: str = "label_spam"
DETECTOR_EF_DOMAIN_MISMATCH: str = "ef_domain_mismatch"
DETECTOR_SOURCE_DRIFT: str = "source_drift"

#: All nine detector names, in 03 section 6 table order (R-2: the one
#: definition ``run_detectors`` and every test iterate over).
DETECTOR_NAMES: tuple[str, ...] = (
    DETECTOR_PARAM_JUMP,
    DETECTOR_SPEED_WITHOUT_DECISIONS,
    DETECTOR_MERGES_WITHOUT_RETURN,
    DETECTOR_CLOSED_UNREAD,
    DETECTOR_NEWCOMER_BURST,
    DETECTOR_STAR_BURST,
    DETECTOR_LABEL_SPAM,
    DETECTOR_EF_DOMAIN_MISMATCH,
    DETECTOR_SOURCE_DRIFT,
)

# --------------------------------------------------------------------------- #
# Frozen thresholds (03 section 6). One definition per fact (R-2): every      #
# number this module compares against lives here, nowhere else, and none of  #
# it is in ``cvi.core.constants_v2`` yet -- flagged for W2-8 to relocate at   #
# the constants freeze (the same pattern W2-2's gate thresholds and W2-4's   #
# ``MIN_POPULATION`` used while waiting on that freeze).                     #
# --------------------------------------------------------------------------- #

#: ``param_jump``: a parameter score move strictly greater than this many
#: points between consecutive snapshots is a jump.
PARAM_JUMP_THRESHOLD_POINTS: float = 40.0
#: ``param_jump``: minimum sample size required on *both* snapshots' score.
PARAM_JUMP_MIN_N: int = 10

#: ``speed_without_decisions``: TTFHR score floor and DR_90 score ceiling.
SPEED_TTFHR_SCORE_MIN: float = 90.0
SPEED_DR90_SCORE_MAX: float = 40.0

#: ``merges_without_return``: MR_first score floor, conversion/return score
#: ceiling, and the minimum n the triggering side must carry.
MERGES_MR_FIRST_SCORE_MIN: float = 90.0
MERGES_CONVERSION_OR_RETURN_SCORE_MAX: float = 20.0
MERGES_WITHOUT_RETURN_MIN_N: int = 10

#: ``closed_unread``: share floor (of external PRs, closed unmerged, with no
#: substantive response ever recorded).
CLOSED_UNREAD_SHARE_MIN: float = 0.30

#: ``newcomer_burst`` (03 section 4 lockstep rule): minimum count of
#: eligible-looking, all-merged first-timer accounts, and the span they must
#: fall inside. "Within any 7-day span" is the half-open window
#: ``[t, t + 7 days)``: a span of exactly 7 days between the earliest and
#: latest account does not count (documented boundary, tested both sides).
NEWCOMER_BURST_MIN_ACCOUNTS: int = 5
NEWCOMER_BURST_WINDOW: timedelta = timedelta(days=7)

#: ``label_spam``: raw newcomer-labelled-issue count floor and the newcomer
#: conversion score ceiling.
LABEL_SPAM_ISSUE_COUNT_MIN: float = 50.0
LABEL_SPAM_CONVERSION_SCORE_MAX: float = 10.0

#: ``ef_domain_mismatch``: top-domain commit-share floor and EF floor (EF is
#: the raw elephant-factor value -- a count of orgs, not a 0-100 score).
EF_DOMAIN_SHARE_MIN: float = 0.5
EF_DOMAIN_MISMATCH_EF_MIN: float = 4.0

#: Parameter input ids this module reads from ``ScoreRecord.params`` (R-2:
#: reused from ``cvi.core.constants_v2.PARAM_INPUT_IDS`` naming, not
#: retyped as a second literal set -- kept as local names here only because
#: importing the whole 18-entry tuple to pick out five would be less clear
#: at each call site).
INPUT_TTFHR: str = "ttfhr_hours"
INPUT_DR90: str = "dr90_decision_rate"
INPUT_MR_FIRST: str = "mr_first_rate"
INPUT_NEWCOMER_CONVERSION: str = "newcomer_conversion"
INPUT_RR90_RETURN: str = "rr90_return_rate"


def _measured_score(record: ScoreRecord, input_id: str) -> Optional[float]:
    """The ``score`` of ``input_id`` in ``record.params`` iff genuinely
    measured, else ``None`` (the unmeasured-never-fires rule: a missing
    key, ``measured=False`` or a ``None`` score are all "no evidence")."""
    param = record.params.get(input_id)
    if param is None or not param.measured or param.score is None:
        return None
    return param.score


def _measured_n(record: ScoreRecord, input_id: str) -> Optional[int]:
    """The ``n`` behind ``input_id`` iff the parameter is measured, else
    ``None`` (paired with :func:`_measured_score`; an unmeasured parameter
    has no n a detector should trust)."""
    param = record.params.get(input_id)
    if param is None or not param.measured:
        return None
    return param.n


# --------------------------------------------------------------------------- #
# DetectorInputs: raw facts no ScoreRecord carries                            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class NewcomerAccountSample:
    """One first-timer-cohort account, for the ``newcomer_burst`` lockstep
    rule (03 section 4). ``NewcomerAuthor`` now carries
    ``account_created_at`` too (CLOSED, packet W3-1 -- see the module
    docstring); this dataclass is kept as the detector's own input shape
    rather than replaced by ``NewcomerAuthor`` itself, because
    ``all_prs_merged`` is a fact this detector DERIVES from a cohort's raw
    PR list, not one any stored schema carries directly --
    :func:`newcomer_accounts_from_cohorts` is what builds real instances
    of this dataclass from a landed ``FeatureBundle.cohorts.
    newcomer_authors``."""

    login: str
    #: ISO-8601 datetime the GitHub account was created.
    account_created_at: str
    #: Whether every PR this account opened in the sampled window merged.
    all_prs_merged: bool

    def __post_init__(self) -> None:
        if not isinstance(self.login, str) or not self.login:
            raise ValueError(f"login must be a non-empty string, got {self.login!r}")
        try:
            datetime.fromisoformat(self.account_created_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError) as exc:
            raise ValueError(
                f"account_created_at must be an ISO-8601 datetime, got {self.account_created_at!r}"
            ) from exc
        if not isinstance(self.all_prs_merged, bool):
            raise ValueError(f"all_prs_merged must be a bool, got {self.all_prs_merged!r}")


@dataclass(frozen=True)
class DetectorInputs:
    """Raw evidence the nine detectors need beyond the two ``ScoreRecord``s.

    Every field defaults to ``None`` (or an empty tuple), meaning "not
    collected for this repo at this snapshot" -- the unmeasured-never-fires
    rule applies to each one identically to a ``ParamScore``: a detector
    that reads a ``None``/empty field here returns ``False``, never a
    guess. See the module docstring for which fields are ownership
    requests versus already sourceable today.
    """

    #: ``label_spam``: raw 5.1 newcomer-issue-supply count (the input's
    #: measured value, not its capped 0-100 score -- the cap makes the
    #: score useless for spotting "issues >= 50"). Sourceable today from
    #: ``bundle.inputs["newcomer_issue_supply"]`` when ``Measured``.
    newcomer_issue_count: Optional[float] = None
    #: ``ef_domain_mismatch``: raw 3.2 elephant-factor value (a count of
    #: orgs, not a score). Sourceable today from
    #: ``bundle.inputs["elephant_factor"]`` when ``Measured``.
    elephant_factor: Optional[float] = None
    #: ``ef_domain_mismatch``: top commit email-domain's share of human
    #: commits. OWNERSHIP REQUEST (see module docstring) -- ``None`` until
    #: a collector computes it.
    top_email_domain_share: Optional[float] = None
    #: ``closed_unread``: pre-aggregated (share, n) over external PRs
    #: closed unmerged with no substantive response, computed by
    #: :func:`closed_unread_share_from_pr_window` from the bundle's real
    #: ``pr_window`` -- not an ownership request, just kept as a plain
    #: value here so this module's predicates stay free of the
    #: ``FeatureBundle``/``PRWindow`` shape.
    closed_unread_share: Optional[float] = None
    closed_unread_n: Optional[int] = None
    #: ``newcomer_burst``: the sampled first-timer accounts. OWNERSHIP
    #: REQUEST (see module docstring) -- ``None``/empty until the cohort
    #: schema carries account creation dates.
    newcomer_accounts: tuple[NewcomerAccountSample, ...] = ()
    #: ``star_burst``: whether the StarScout lockstep penalty was applied
    #: at universe entry. OWNERSHIP REQUEST (see module docstring).
    star_lockstep_penalty_applied: Optional[bool] = None
    #: ``source_drift``: this repo's W3-3 live-sample canary hold verdict
    #: (``True`` iff the row is currently held, i.e. verdict != "ok").
    source_drift_held: Optional[bool] = None


#: Every field on :class:`DetectorInputs` defaults to "no evidence" so a
#: caller with nothing extra to report can pass this shared instance.
_EMPTY_INPUTS: DetectorInputs = DetectorInputs()


def closed_unread_share_from_pr_window(pr_window: PRWindow) -> tuple[Optional[float], int]:
    """The ``closed_unread`` (share, n) pair straight from a real
    ``FeatureBundle.pr_window`` (03 section 6; W1-3's landed schema).

    Population: external PR events in the window. Numerator: those closed
    (``closed_at`` set) and never merged (``merged_at`` is ``None``) with no
    substantive response ever recorded (``first_human_review_at`` is
    ``None`` -- the same D-IN-2 field ``cvi.core.gates``'s D-GATE-5 already
    reads as "no substantive response", see that module's
    ``_zero_substantive_response``). ``share`` is ``None`` when the
    external-PR population is empty (no evidence, never fires); ``n`` is
    always the population size, 0 included, for the caller to store
    alongside ``share``.
    """
    external = [event for event in pr_window.events if event.is_external]
    n = len(external)
    if n == 0:
        return None, 0
    numerator = sum(
        1
        for event in external
        if event.closed_at is not None
        and event.merged_at is None
        and event.first_human_review_at is None
    )
    return numerator / n, n


def newcomer_accounts_from_cohorts(cohorts: Cohorts) -> tuple[NewcomerAccountSample, ...]:
    """The ``newcomer_burst`` detector's real input, straight from a landed
    ``FeatureBundle.cohorts.newcomer_authors`` (W3-1, closing the ownership
    request the module docstring names — ``NewcomerAuthor`` now carries
    ``account_created_at``).

    An author whose account creation date was never measured (``None`` —
    the collector's search or eligibility fetch failed for them, W1-4
    round 3) is skipped entirely, not guessed at: the unmeasured-never-
    fires rule applies here exactly as it does to every other
    :class:`DetectorInputs` field. ``all_prs_merged`` is true iff every PR
    this account is recorded as having opened (its first PR and every
    follow-up) has a ``merged_at``.
    """
    samples: list[NewcomerAccountSample] = []
    for author in cohorts.newcomer_authors:
        if author.account_created_at is None:
            continue
        all_prs = (author.first_pr, *author.followups)
        all_merged = all(pr.merged_at is not None for pr in all_prs)
        samples.append(NewcomerAccountSample(
            login=author.login, account_created_at=author.account_created_at,
            all_prs_merged=all_merged))
    return tuple(samples)


# --------------------------------------------------------------------------- #
# The nine detectors (03 section 6 table order)                               #
# --------------------------------------------------------------------------- #


def detect_param_jump(
    current: ScoreRecord, previous: Optional[ScoreRecord], inputs: DetectorInputs
) -> bool:
    """``param_jump``: any parameter score moves > 40 points between
    consecutive snapshots with n >= 10 on *both* sides (03 section 6).

    Unmeasured-never-fires: a parameter unmeasured (or scoreless) on either
    snapshot, or with n < 10 on either snapshot, is skipped -- it never
    contributes a jump. ``previous is None`` (the first snapshot) never
    fires: there is nothing to move from."""
    if previous is None:
        return False
    for input_id in current.params:
        if input_id not in previous.params:
            continue
        current_score = _measured_score(current, input_id)
        previous_score = _measured_score(previous, input_id)
        if current_score is None or previous_score is None:
            continue
        current_n = _measured_n(current, input_id)
        previous_n = _measured_n(previous, input_id)
        if current_n is None or previous_n is None:
            continue
        if current_n < PARAM_JUMP_MIN_N or previous_n < PARAM_JUMP_MIN_N:
            continue
        if abs(current_score - previous_score) > PARAM_JUMP_THRESHOLD_POINTS:
            return True
    return False


def detect_speed_without_decisions(
    current: ScoreRecord, previous: Optional[ScoreRecord], inputs: DetectorInputs
) -> bool:
    """``speed_without_decisions``: TTFHR score >= 90 and DR_90 score <= 40
    (03 section 6). Unmeasured-never-fires: either parameter unmeasured
    (or scoreless) means no fire."""
    ttfhr = _measured_score(current, INPUT_TTFHR)
    dr90 = _measured_score(current, INPUT_DR90)
    if ttfhr is None or dr90 is None:
        return False
    return ttfhr >= SPEED_TTFHR_SCORE_MIN and dr90 <= SPEED_DR90_SCORE_MAX


def detect_merges_without_return(
    current: ScoreRecord, previous: Optional[ScoreRecord], inputs: DetectorInputs
) -> bool:
    """``merges_without_return``: MR_first score >= 90 and (conversion or
    return) score <= 20, with n >= 10 on the triggering side (03 section
    6). Unmeasured-never-fires: an unmeasured/scoreless MR_first never
    fires; conversion and return are each checked independently and
    skipped (not treated as failing or passing) when unmeasured or below
    the n floor."""
    mr_first = _measured_score(current, INPUT_MR_FIRST)
    if mr_first is None or mr_first < MERGES_MR_FIRST_SCORE_MIN:
        return False
    for input_id in (INPUT_NEWCOMER_CONVERSION, INPUT_RR90_RETURN):
        score = _measured_score(current, input_id)
        n = _measured_n(current, input_id)
        if score is None or n is None:
            continue
        if n < MERGES_WITHOUT_RETURN_MIN_N:
            continue
        if score <= MERGES_CONVERSION_OR_RETURN_SCORE_MAX:
            return True
    return False


def detect_closed_unread(
    current: ScoreRecord, previous: Optional[ScoreRecord], inputs: DetectorInputs
) -> bool:
    """``closed_unread``: share of external PRs closed unmerged with no
    substantive response ever recorded >= 0.30 (03 section 6). Reads
    ``inputs.closed_unread_share`` (see
    :func:`closed_unread_share_from_pr_window`). Unmeasured-never-fires:
    ``None`` (no external-PR population, or the caller had nothing to
    report) never fires."""
    share = inputs.closed_unread_share
    if share is None:
        return False
    return share >= CLOSED_UNREAD_SHARE_MIN


def detect_newcomer_burst(
    current: ScoreRecord, previous: Optional[ScoreRecord], inputs: DetectorInputs
) -> bool:
    """``newcomer_burst`` (03 section 4 lockstep rule): >= 5 eligible-
    looking first-timer accounts, all of whose PRs merged, created within
    any 7-day span (the half-open window ``[t, t+7d)`` -- see
    ``NEWCOMER_BURST_WINDOW``'s docstring for the boundary). Reads
    ``inputs.newcomer_accounts``. Unmeasured-never-fires: an account with
    ``all_prs_merged=False`` is excluded from the count entirely (it is
    not "measured to have failed the burst", it simply does not qualify);
    an empty/absent ``newcomer_accounts`` tuple never fires."""
    created_at = sorted(
        datetime.fromisoformat(account.account_created_at.replace("Z", "+00:00"))
        for account in inputs.newcomer_accounts
        if account.all_prs_merged
    )
    if len(created_at) < NEWCOMER_BURST_MIN_ACCOUNTS:
        return False
    window_size = NEWCOMER_BURST_MIN_ACCOUNTS
    for start in range(len(created_at) - window_size + 1):
        span = created_at[start + window_size - 1] - created_at[start]
        if span < NEWCOMER_BURST_WINDOW:
            return True
    return False


def detect_star_burst(
    current: ScoreRecord, previous: Optional[ScoreRecord], inputs: DetectorInputs
) -> bool:
    """``star_burst``: the StarScout lockstep penalty was applied to this
    repo at universe entry (03 section 6). Reads
    ``inputs.star_lockstep_penalty_applied``. Unmeasured-never-fires:
    ``None`` (not audited, or the fact does not exist yet -- see the
    module docstring's ownership request) never fires; only an explicit
    ``True`` does."""
    return inputs.star_lockstep_penalty_applied is True


def detect_label_spam(
    current: ScoreRecord, previous: Optional[ScoreRecord], inputs: DetectorInputs
) -> bool:
    """``label_spam``: raw newcomer-labelled-issue count >= 50 and
    newcomer-conversion score <= 10 (03 section 6). The issue count is the
    raw 5.1 input value, not its capped score (see
    ``DetectorInputs.newcomer_issue_count``'s docstring) -- the score caps
    at 100 for any count >= 10, which would make ">= 50" unobservable.
    Unmeasured-never-fires: a ``None`` issue count, or an unmeasured/
    scoreless conversion parameter, never fires."""
    issue_count = inputs.newcomer_issue_count
    if issue_count is None:
        return False
    conversion = _measured_score(current, INPUT_NEWCOMER_CONVERSION)
    if conversion is None:
        return False
    return issue_count >= LABEL_SPAM_ISSUE_COUNT_MIN and conversion <= LABEL_SPAM_CONVERSION_SCORE_MAX


def detect_ef_domain_mismatch(
    current: ScoreRecord, previous: Optional[ScoreRecord], inputs: DetectorInputs
) -> bool:
    """``ef_domain_mismatch``: top email-domain share >= 0.5 while EF >= 4
    (03 section 6). EF is the raw 3.2 elephant-factor value (a count of
    orgs), read from ``inputs.elephant_factor`` -- not the parameter's
    0-100 score, which the table's "EF >= 4" plainly is not (EF is
    typically single digits). Unmeasured-never-fires: either a ``None``
    domain share (see the module docstring's ownership request) or a
    ``None`` EF never fires."""
    domain_share = inputs.top_email_domain_share
    ef = inputs.elephant_factor
    if domain_share is None or ef is None:
        return False
    return domain_share >= EF_DOMAIN_SHARE_MIN and ef >= EF_DOMAIN_MISMATCH_EF_MIN


def detect_source_drift(
    current: ScoreRecord, previous: Optional[ScoreRecord], inputs: DetectorInputs
) -> bool:
    """``source_drift``: the W3-3 live-sample canary is holding this repo's
    row from promotion (03 section 5/6). Reads
    ``inputs.source_drift_held``. Unmeasured-never-fires: ``None`` (not
    sampled by the canary this run) never fires; only an explicit ``True``
    does."""
    return inputs.source_drift_held is True


#: Every detector, keyed by name, in 03 section 6 table order (R-2: the one
#: place the name -> predicate mapping is written).
_DETECTOR_FUNCTIONS: "dict[str, Callable[[ScoreRecord, Optional[ScoreRecord], DetectorInputs], bool]]" = {
    DETECTOR_PARAM_JUMP: detect_param_jump,
    DETECTOR_SPEED_WITHOUT_DECISIONS: detect_speed_without_decisions,
    DETECTOR_MERGES_WITHOUT_RETURN: detect_merges_without_return,
    DETECTOR_CLOSED_UNREAD: detect_closed_unread,
    DETECTOR_NEWCOMER_BURST: detect_newcomer_burst,
    DETECTOR_STAR_BURST: detect_star_burst,
    DETECTOR_LABEL_SPAM: detect_label_spam,
    DETECTOR_EF_DOMAIN_MISMATCH: detect_ef_domain_mismatch,
    DETECTOR_SOURCE_DRIFT: detect_source_drift,
}


def run_detectors(
    current: ScoreRecord,
    previous: Optional[ScoreRecord],
    inputs: DetectorInputs = _EMPTY_INPUTS,
) -> list[str]:
    """Evaluate all nine detectors and return the triggered names.

    Sorted and de-duplicated (trivially unique here, since each name maps
    to exactly one predicate, but sorting is what makes the result
    independent of ``dict``/iteration order and safe to compare directly in
    a test or store verbatim on ``ScoreRecord.suspicion``). Does not
    mutate ``current``, ``previous`` or ``inputs`` (04 section 3:
    ``ScoreRecord`` is frozen; wiring this into the record the engine
    returns is an ownership request to W2-8, see the module docstring)."""
    return sorted(
        name for name, detector in _DETECTOR_FUNCTIONS.items() if detector(current, previous, inputs)
    )
