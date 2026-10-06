"""Porchlamp v2 stage-0 eligibility gates (02 section 2: D-GATE-1..6, D-EVID-1).

``evaluate_gates`` runs the six identity/eligibility gates over a validated
:class:`~porchlamp.core.models.FeatureBundle` plus :class:`GateEvidence` (the gate
inputs the bundle schema does not carry: tree/manifest/licence/off-platform
markers, vitality commits, universe fork-parent membership). ``now`` is
the snapshot reference time, passed explicitly (D-VER-3: the core owns no
clock). Every gate is evaluated; the first failing gate in the design's fixed
order is the ``reason``; a gate whose input is insufficient never fails the
repo but is recorded in ``GateResult.undecided`` by its decision id
(``"D-GATE-1"`` .. ``"D-GATE-6"``). D-GATE-5 reads the 1.4 ``gr14_ghost_rate`` input, the
share :func:`zero_response_counts` computed at derive time, so the gate and
the parameter are one measurement. D-GATE-6 is routing, not failure: it
passes, routes to the off-platform workflow, or reports
``unrated: no_external_workflow``.

``evidence_sufficiency`` is the D-EVID-1 stage-3 gate on the composite
coverage C (D-MISS-3): C >= 0.60 is rated, below is
``unrated: insufficient_evidence`` (listed, not ranked).

The seven numeric gate thresholds below and ``OSI_APPROVED_LICENSES`` live
in ``porchlamp.core.constants`` and are imported back here under their original
names. The licence list belongs there because D-GATE-3 *interprets* it as a
predicate deciding eligible vs ``REASON_UNLICENSED``, so an upstream-list
update is itself a ranking-changing event and belongs in the hash / RFC
procedure the same as any other tunable. ``UNDETERMINED_SPDX`` is defined
in this module: it is three sentinel strings meaning "no SPDX id recorded"
(``None``, ``"NOASSERTION"``, ``"OTHER"``), not a license vocabulary that
could itself drift.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Iterable

from porchlamp.core import constants
from porchlamp.core.models import FeatureBundle, GateResult, Measured, PRWindowEvent

#: Decision ids recorded in ``GateResult.undecided`` (02 section 2).
GATE_1 = "D-GATE-1"  # software project
GATE_2 = "D-GATE-2"  # not archived, not a mirror, not a fork
GATE_3 = "D-GATE-3"  # licensed
GATE_4 = "D-GATE-4"  # vitality
GATE_5 = "D-GATE-5"  # not a black hole
GATE_6 = "D-GATE-6"  # PR workflow
GATE_DECISION_IDS: tuple[str, ...] = (GATE_1, GATE_2, GATE_3, GATE_4, GATE_5, GATE_6)

#: ``GateResult.reason`` vocabulary (02 section 2, "Outcome" column).
REASON_NON_SOFTWARE = "non_software"
REASON_ARCHIVED = "archived"
REASON_MIRROR = "mirror"
REASON_FORK = "fork"
REASON_UNLICENSED = "unlicensed"
REASON_STALE = "stale"
REASON_BLACK_HOLE = "black_hole"
REASON_NO_EXTERNAL_WORKFLOW = "unrated: no_external_workflow"
REASON_INSUFFICIENT_EVIDENCE = "unrated: insufficient_evidence"
#: ``ScoreRecord.unrated_reason`` for a repo D-GATE-6 routed off-platform.
#: Not a ``GateResult.reason``: the repo PASSES the gate (it has a real review
#: workflow, just not on GitHub), so the gate stays ``eligible`` and it is the
#: evidence stage that declines to rate it. 06 section 3 A2 lists
#: "off-platform routed" beside "insufficient evidence" as an unrated outcome.
REASON_OFF_PLATFORM_ROUTED = "unrated: off_platform_routed"

#: ``GateResult.routed`` value for the D-GATE-6 off-platform workflow.
ROUTED_OFF_PLATFORM = "off_platform"

#: Stage-0 gate reasons that `06-UX-DESIGN.md` section 3 A2 classifies as
#: UNRATED rather than GATED.
#:
#: 06's user-facing split lists "no external workflow" under unrated (an
#: evidence outcome, beside "insufficient evidence" and "off-platform
#: routed"), never under gated (the six universal disqualifications --
#: mirror / archived / unlicensed / stale / black hole / non-software -- plus
#: fork). The ENGINE nonetheless gates it at stage 0
#: (:data:`REASON_NO_EXTERNAL_WORKFLOW`, D-GATE-6: ``eligible=False``); that
#: raw flag is `03` section 5's provenance and stays exactly as
#: :func:`evaluate_gates` computed it, never rewritten.
#:
#: This frozen set is the ONE place the two classifications' difference is
#: named. It lives here, beside the reason vocabulary it is a subset of,
#: rather than in ``porchlamp.core.pipeline``, because ``porchlamp.core.engine`` also
#: needs it (to carry such a reason onto ``ScoreRecord.unrated_reason``) and
#: ``pipeline`` imports ``engine`` -- defining it there would make the
#: dependency circular. ``porchlamp.core.pipeline.DISPLAY_UNRATED_GATE_REASONS``
#: re-exports this name for its existing consumers
#: (``porchlamp.store.queries.snapshot_summary``, ``porchlamp.serve.app``'s not-ranked
#: split), so every count on every screen still agrees with one definition.
DISPLAY_UNRATED_GATE_REASONS: frozenset[str] = frozenset({REASON_NO_EXTERNAL_WORKFLOW})

#: D-GATE-4: trailing window for non-bot User commits on the default branch.
GATE4_VITALITY_WINDOW_DAYS: int = constants.GATE4_VITALITY_WINDOW_DAYS
#: D-GATE-5: minimum population of external PRs >= 14 d old, else UNDECIDED.
GATE5_MIN_N: int = constants.GATE5_MIN_N
#: D-GATE-5: a PR is ">= 14 d old" at the snapshot reference time.
GATE5_MIN_AGE_DAYS: int = constants.GATE5_MIN_AGE_DAYS
#: D-GATE-5: the substantive-response deadline after opening.
GATE5_RESPONSE_DEADLINE_DAYS: int = constants.GATE5_RESPONSE_DEADLINE_DAYS
#: D-GATE-5: pass ceiling on the share with zero substantive response.
GATE5_MAX_ZERO_RESPONSE_SHARE: float = constants.GATE5_MAX_ZERO_RESPONSE_SHARE
#: D-GATE-6: horizon for a decided external PR.
GATE6_DECISION_WINDOW_DAYS: int = constants.GATE6_DECISION_WINDOW_DAYS
#: D-EVID-1: composite coverage threshold (D-MISS-3).
EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE: float = constants.EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE

#: D-GATE-3: SPDX states that mean "no usable licence recorded" (the gate then
#: looks for a ``LICENSE*`` file before failing). Mirrors the collector's
#: ``D_GATE3_UNLICENSED_SPDX``.
UNDETERMINED_SPDX: frozenset[str | None] = frozenset({None, "NOASSERTION", "OTHER"})

#: D-GATE-3: OSI-approved SPDX ids, re-bound from ``porchlamp.core.constants``
#: under the original name (see the module docstring for why the list is a
#: hashed constant).
OSI_APPROVED_LICENSES: frozenset[str] = constants.OSI_APPROVED_LICENSES


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp (``Z`` accepted) into an aware datetime.

    Timestamps that carry no offset are read as UTC. Every gate compares the
    result against an aware ``as_of``, and mixing naive and aware datetimes
    raises ``TypeError``; defaulting to UTC keeps a collector that dropped the
    offset from taking the whole evaluation down.
    """
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


#: SPDX expression operators (SPDX spec Annex D). ``WITH`` attaches a licence
#: EXCEPTION to the identifier on its left; the exception name is not itself a
#: licence id, so it is dropped when checking approval.
_SPDX_OPERATORS: frozenset[str] = frozenset({"AND", "OR", "WITH"})

#: One SPDX licence identifier, optionally suffixed ``+`` ("or later"), plus
#: the operators and parentheses a compound expression may contain. Anything
#: outside this shape is not an SPDX expression this gate claims to read.
_SPDX_EXPRESSION = re.compile(r"^[A-Za-z0-9.\-+()\s]+$")


def _eval_spdx_expression(expression: str, approved: set[str]) -> bool | None:
    """Evaluate a compound SPDX expression with ``WITH`` > ``AND`` > ``OR``
    precedence and parentheses; returns ``None`` if syntactically invalid."""
    tokens = re.findall(r"[()]|[A-Za-z0-9.\-+]+", expression.strip())
    if not tokens:
        return None
    pos = 0

    def parse_or() -> bool | None:
        nonlocal pos
        left = parse_and()
        if left is None:
            return None
        result = left
        while pos < len(tokens) and tokens[pos].upper() == "OR":
            pos += 1
            right = parse_and()
            if right is None:
                return None
            result = result or right
        return result

    def parse_and() -> bool | None:
        nonlocal pos
        left = parse_primary()
        if left is None:
            return None
        result = left
        while pos < len(tokens) and tokens[pos].upper() == "AND":
            pos += 1
            right = parse_primary()
            if right is None:
                return None
            result = result and right
        return result

    def parse_primary() -> bool | None:
        nonlocal pos
        if pos >= len(tokens):
            return None
        token = tokens[pos]
        if token == "(":
            pos += 1
            inner = parse_or()
            if inner is None or pos >= len(tokens) or tokens[pos] != ")":
                return None
            pos += 1
            return inner
        if token == ")" or token.upper() in _SPDX_OPERATORS:
            return None
        lic_id = token.rstrip("+")
        if not lic_id:
            return None
        pos += 1
        if pos < len(tokens) and tokens[pos].upper() == "WITH":
            pos += 1
            if pos >= len(tokens):
                return None
            exc_tok = tokens[pos]
            if exc_tok in ("(", ")") or exc_tok.upper() in _SPDX_OPERATORS:
                return None
            pos += 1
        return lic_id.casefold() in approved

    val = parse_or()
    if val is None or pos != len(tokens):
        return None
    return val


def _is_parseable_spdx_expression(spdx: str | None) -> bool:
    """Whether ``spdx`` looks like an SPDX expression naming at least one id."""
    if not isinstance(spdx, str) or not spdx.strip():
        return False
    if not _SPDX_EXPRESSION.match(spdx):
        return False
    return _eval_spdx_expression(spdx, set()) is not None


def _is_osi_approved_expression(spdx: str | None) -> bool:
    """D-GATE-3: whether ``spdx`` grants an OSI-approved licence.

    ``OSI_APPROVED_LICENSES`` holds bare identifiers, but the SPDX field on a
    repository is an EXPRESSION: Rust crates almost universally publish
    ``MIT OR Apache-2.0``, LLVM-derived projects publish
    ``Apache-2.0 WITH LLVM-exception``. Comparing the whole expression against
    a set of bare ids fails every one of them, and -- because the old code
    then fell into the final ``else`` -- failed them as ``unlicensed`` WITHOUT
    even consulting the LICENSE file in the tree. A repo with no SPDX at all
    passed the gate on that file; a dual-licensed repo did not.

    Identifiers are compared case-insensitively (hosts report ``mit`` as
    readily as ``MIT``). ``AND`` requires every named licence to be approved;
    ``OR`` requires only one, since the recipient may choose that branch.
    """
    if not isinstance(spdx, str) or not spdx.strip() or not _SPDX_EXPRESSION.match(spdx):
        return False
    approved = {name.casefold() for name in OSI_APPROVED_LICENSES}
    return bool(_eval_spdx_expression(spdx, approved))


@dataclass(frozen=True)
class GateEvidence:
    """Stage-0 evidence the FeatureBundle v2 schema does not carry.

    A ``None`` field means "not collected / unknown": the affected gate is
    recorded UNDECIDED (02 section 2) instead of failing the repo. Fields map
    to their collectors of record: ``manifest_found`` / ``license_file_present``
    / ``off_platform_marker`` to the tree and marker evidence,
    ``vitality_commits_180d`` to the commit evidence on the default
    branch, ``fork_parent_in_universe`` to the universe build (D-UNI-1).
    """

    #: D-GATE-1: a recognised manifest at tree depth <= 2 on a complete tree.
    manifest_found: bool | None
    #: D-GATE-1: curated-anchor exemption (passes the gate regardless of the tree).
    is_curated_anchor: bool = False
    #: D-GATE-3: a ``LICENSE*`` file in the tree (case-insensitive basename).
    license_file_present: bool | None = None
    #: D-GATE-2: whether the fork's parent repository is in the universe.
    fork_parent_in_universe: bool | None = None
    #: D-GATE-4: non-bot User commits on the default branch, trailing 180 d.
    vitality_commits_180d: int | None = None
    #: D-GATE-6: an off-platform contribution marker.
    off_platform_marker: bool | None = None

    def __post_init__(self) -> None:
        problems: list[str] = []
        for name in ("manifest_found", "license_file_present", "fork_parent_in_universe", "off_platform_marker"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                problems.append(f"{name} must be a bool or None, got {value!r}")
        if not isinstance(self.is_curated_anchor, bool):
            problems.append(f"is_curated_anchor must be a bool, got {self.is_curated_anchor!r}")
        if self.vitality_commits_180d is not None and (
            isinstance(self.vitality_commits_180d, bool)
            or not isinstance(self.vitality_commits_180d, int)
            or self.vitality_commits_180d < 0
        ):
            problems.append(
                f"vitality_commits_180d must be a non-negative int or None, "
                f"got {self.vitality_commits_180d!r}"
            )
        if problems:
            raise ValueError("; ".join(problems))


#: Input id D-GATE-5 reads: 1.4 GR_14 is the same zero-response share, so the
#: gate and the parameter are one measurement (owner ruling 2026-10-06).
GHOST_RATE_INPUT_ID: str = "gr14_ghost_rate"


@dataclass(frozen=True)
class GhostRatePR:
    """One PR as the D-GATE-5 / 1.4 GR_14 measurement sees it.

    Built from the ``pr_window`` envelope (02 section 3 D-IN-4: PRs opened in
    the trailing 180 d), never from the 30-PR v1 ``pr_events`` sample.
    ``first_substantive_response_at`` is the D-IN-2 response time: the
    earliest review (any state) or comment of >= 40 characters by a
    non-author, non-bot MEMBER/OWNER/COLLABORATOR; ``None`` when there is
    none. ``is_bot_author`` is D-IN-1 (``__typename == "Bot"``, ``[bot]``
    suffix, or the exact-match bot login list).
    """

    created_at: str
    is_external: bool
    is_bot_author: bool
    first_substantive_response_at: str | None


def zero_response_counts(prs: Iterable[GhostRatePR], now: datetime) -> tuple[int, int]:
    """``(unanswered, n)`` for D-GATE-5 and 1.4 GR_14 -- the one function
    both read, so they cannot disagree.

    Population (02 section 2 D-GATE-5, section 4 1.4): external PRs by a
    non-bot author (D-IN-1), opened in the trailing
    :data:`constants.PR_WINDOW_DAYS` before ``now`` and at least
    :data:`GATE5_MIN_AGE_DAYS` old at ``now``. Unanswered: no D-IN-2
    substantive response within :data:`GATE5_RESPONSE_DEADLINE_DAYS` of
    opening. A merge is not a response: D-IN-2 defines a substantive
    response as a maintainer review or a >= 40-character maintainer comment,
    and neither 1.4 nor D-GATE-5 adds merges to it. ``now`` is the
    reference time, passed explicitly (D-VER-3).
    """
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError(f"now must be timezone-aware, got {now!r}")
    window_start = now - timedelta(days=constants.PR_WINDOW_DAYS)
    youngest = now - timedelta(days=GATE5_MIN_AGE_DAYS)
    deadline = timedelta(days=GATE5_RESPONSE_DEADLINE_DAYS)
    unanswered = n = 0
    for pr in prs:
        if not pr.is_external or pr.is_bot_author:
            continue
        created = _parse_iso(pr.created_at)
        if not window_start <= created <= youngest:
            continue
        n += 1
        response = pr.first_substantive_response_at
        if response is None or _parse_iso(response) - created > deadline:
            unanswered += 1
    return unanswered, n


def _has_decided_external_pr(events: tuple[PRWindowEvent, ...], now: datetime) -> bool:
    """D-GATE-6: >= 1 external PR decided (merged or closed) in the trailing 365 d.

    Every event in the 180-day PR window was opened inside that window, so a
    decision recorded on it necessarily falls inside the 365-day horizon; the
    horizon is nonetheless checked explicitly against
    :data:`GATE6_DECISION_WINDOW_DAYS` rather than left implied by the caller's
    window, so the constant the methodology document publishes for this gate is
    the constant the gate actually applies -- and so widening the PR window
    later cannot silently widen this gate too. The decision timestamp is also
    required to be ``<= now`` (decided as of the snapshot)."""
    horizon = now - timedelta(days=GATE6_DECISION_WINDOW_DAYS)
    for event in events:
        if not event.is_external:
            continue
        decided_at = event.merged_at or event.closed_at
        if decided_at is None:
            continue
        decided = _parse_iso(decided_at)
        if horizon <= decided <= now:
            return True
    return False


def evaluate_gates(
    bundle: FeatureBundle, evidence: GateEvidence, now: datetime
) -> GateResult:
    """Evaluate D-GATE-1..6 (02 section 2) for one repo at snapshot time.

    ``bundle`` must be a validated FeatureBundle v2; ``now`` is the snapshot
    reference time, timezone-aware. All six gates run; the first failure in
    the design's fixed order is ``reason`` (``eligible=False``), UNDECIDED
    gates are recorded in ``undecided`` by decision id, and D-GATE-6 routing
    is recorded in ``routed`` without failing the repo."""
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError(f"now must be timezone-aware, got {now!r}")
    repo = bundle.repo
    events = bundle.pr_window.events
    failures: list[tuple[int, str]] = []
    undecided: list[str] = []
    routed: str | None = None

    # D-GATE-1: software project (manifest at depth <= 2, or curated anchor).
    if not evidence.is_curated_anchor:
        if evidence.manifest_found is None:
            undecided.append(GATE_1)
        elif not evidence.manifest_found:
            failures.append((1, REASON_NON_SOFTWARE))

    # D-GATE-2: not archived, not a mirror, not a fork (fork only when the
    # parent is absent from the universe).
    if repo.archived:
        failures.append((2, REASON_ARCHIVED))
    elif repo.mirror_url is not None:
        failures.append((2, REASON_MIRROR))
    elif repo.fork:
        if evidence.fork_parent_in_universe is True:
            failures.append((2, REASON_FORK))
        elif evidence.fork_parent_in_universe is None:
            undecided.append(GATE_2)

    # D-GATE-3: licensed (OSI-approved SPDX, else a LICENSE* file).
    spdx = repo.license_spdx
    if _is_osi_approved_expression(spdx):
        pass
    elif spdx in UNDETERMINED_SPDX or not _is_parseable_spdx_expression(spdx):
        # Either the host told us it could not determine a licence, or it gave
        # us something this gate cannot read as an SPDX expression. Both are
        # "we do not know from the metadata", so fall through to the tree
        # evidence rather than concluding the repo is unlicensed.
        if evidence.license_file_present is True:
            pass
        elif evidence.license_file_present is False:
            failures.append((3, REASON_UNLICENSED))
        else:
            undecided.append(GATE_3)
    else:
        # A well-formed SPDX expression that is not OSI-approved: a real,
        # recorded, non-open licence. This is a decided failure -- a LICENSE
        # file in the tree cannot make a proprietary licence open.
        failures.append((3, REASON_UNLICENSED))

    # D-GATE-4: vitality (>= 1 non-bot User commit, default branch, 180 d).
    if evidence.vitality_commits_180d is None:
        undecided.append(GATE_4)
    elif evidence.vitality_commits_180d < 1:
        failures.append((4, REASON_STALE))

    # D-GATE-5: not a black hole. Reads the 1.4 GR_14 input, which derive
    # computes with zero_response_counts at its own pinned reference time
    # from the pr_window envelope: value = the zero-response share, n = the
    # population. Unmeasured or n below the minimum -> UNDECIDED.
    ghost = bundle.inputs.get(GHOST_RATE_INPUT_ID)
    if not isinstance(ghost, Measured) or ghost.n < GATE5_MIN_N:
        undecided.append(GATE_5)
    elif ghost.value > GATE5_MAX_ZERO_RESPONSE_SHARE:
        failures.append((5, REASON_BLACK_HOLE))

    # D-GATE-6: PR workflow (decided external PR, else off-platform routing,
    # else unrated).
    if not _has_decided_external_pr(events, now):
        if evidence.off_platform_marker is True:
            routed = ROUTED_OFF_PLATFORM
        elif evidence.off_platform_marker is False:
            failures.append((6, REASON_NO_EXTERNAL_WORKFLOW))
        else:
            undecided.append(GATE_6)

    if failures:
        _, reason = min(failures, key=lambda failure: failure[0])
        return GateResult(eligible=False, reason=reason, routed=None, undecided=tuple(undecided))
    return GateResult(eligible=True, reason=None, routed=routed, undecided=tuple(undecided))


def evidence_sufficiency(coverage: float) -> GateResult:
    """D-EVID-1 (02 section 2, stage 3): rated iff composite coverage C >= 0.60.

    ``coverage`` is the composite coverage C of D-MISS-3 (stage-6 output),
    within [0, 1]. Below the threshold the repo is listed but not
    ranked (``unrated: insufficient_evidence``)."""
    if isinstance(coverage, bool) or not isinstance(coverage, (int, float)):
        raise ValueError(f"coverage must be a real number, got {coverage!r}")
    if not 0.0 <= coverage <= 1.0:
        raise ValueError(f"coverage must be within [0, 1], got {coverage!r}")
    if coverage >= EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE:
        return GateResult(eligible=True, reason=None, routed=None, undecided=())
    return GateResult(eligible=False, reason=REASON_INSUFFICIENT_EVIDENCE, routed=None, undecided=())
