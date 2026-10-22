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
``unrated: no_external_workflow``. A repository whose own notices send
outside PRs to another review system (``GateEvidence.review_handoff``) is
routed off-platform and D-GATE-5 is left UNDECIDED for it: GitHub cannot see
its review, so the ghost rate says nothing about whether it answers.

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

import math
import re
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from porchlamp.core import constants
from porchlamp.core.commit_domains import CommitActor, is_bot
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
#: D-GATE-5: 95% one-sided standard normal quantile for the Wilson lower confidence bound (RFC-0008).
GATE5_WILSON_Z: float = constants.GATE5_WILSON_Z
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
    #: D-GATE-6 review hand-off: ``True`` when the repository's own notices
    #: send outside PRs to another review system (see
    #: ``constants.GATE6_HANDOFF_*``), ``False`` when its PR window was read
    #: and the rule is not met, ``None`` when there is no readable PR window
    #: (no opinion).
    review_handoff: bool | None = None
    #: D-GATE-5 rule inputs: ``True`` when every recorded PR node of the
    #: window carries the fields the exclusion rules read (``isDraft`` and the
    #: ready-for-review / close-actor timeline), ``False`` when the window was
    #: read and some node lacks them, ``None`` when nobody looked (no opinion).
    #: A ``False`` leaves D-GATE-5 UNDECIDED whatever the share: a missing
    #: field moves the share both ways (a draft that was answered is counted
    #: as answered and lowers it; an author's own close read as silence
    #: raises it), so neither a pass nor a failure can be read from it.
    pr_rule_inputs_recorded: bool | None = None
    #: D-GATE-5 window coverage (:func:`pr_window_coverage`): ``True`` when the
    #: recorded PR window holds every PR opened since the gate's window start,
    #: ``False`` when it does not (the collector stopped at its PR cap, the
    #: record's own window opens later, or the record does not say whether it
    #: was cut), ``None`` when there is no readable PR window (no opinion). A
    #: ``False`` leaves D-GATE-5 UNDECIDED: the PRs it holds are the newest
    #: weeks, and their share is not the window's.
    pr_window_complete: bool | None = None
    #: With ``pr_window_complete`` ``False``: the time (ISO 8601) from which the
    #: record holds every PR, the cut-off that leaves D-GATE-5 undecided; ``None``
    #: when the record does not say.
    pr_window_cutoff: str | None = None
    #: D-GATE-6 from the recorded PR envelopes (``pr_window``, the 365-day
    #: ``author_history`` and the v1 sample of the 30 newest PRs): ``True``
    #: when one of them holds an external PR merged or closed in the trailing
    #: :data:`GATE6_DECISION_WINDOW_DAYS`, ``False`` only when they hold the
    #: repository's whole PR history and none does, ``None`` otherwise (no
    #: opinion). See :func:`porchlamp.derive.gate_evidence._decided_external_pr_in`.
    decided_external_pr: bool | None = None

    def __post_init__(self) -> None:
        problems: list[str] = []
        for name in (
            "manifest_found", "license_file_present", "fork_parent_in_universe", "off_platform_marker",
            "review_handoff", "pr_rule_inputs_recorded", "pr_window_complete", "decided_external_pr",
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                problems.append(f"{name} must be a bool or None, got {value!r}")
        if self.pr_window_cutoff is not None and not isinstance(self.pr_window_cutoff, str):
            problems.append(f"pr_window_cutoff must be a str or None, got {self.pr_window_cutoff!r}")
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


# --------------------------------------------------------------------------- #
# D-IN-2: who answers a pull request, and with what (02 section 3; RFC-0011).  #
# The one definition: the collector's per-PR rows, GR_14 and D-GATE-5, the     #
# served black-hole evidence and the closed_unread detector all read it.       #
# --------------------------------------------------------------------------- #

#: D-IN-2: the author associations GitHub gives a repository's own people.
MAINTAINER_ASSOCIATIONS: frozenset[str] = frozenset({"MEMBER", "OWNER", "COLLABORATOR"})

#: The Chinese, Japanese and Korean letters a comment's length counts
#: ``constants.SUBSTANTIVE_COMMENT_CJK_WEIGHT`` times: Han ideographs (the
#: unified, extension and compatibility blocks), hiragana and katakana (with
#: the halfwidth forms) and Hangul (syllables and jamo). Punctuation, digits,
#: spaces and Latin letters inside such text count once, as they do in Latin
#: text. A fixed vocabulary of Unicode blocks, not a tunable, so not hashed.
_CJK_LETTERS = re.compile(
    "["
    "ᄀ-ᇿ"          # Hangul Jamo
    "぀-ヿ"          # Hiragana, Katakana
    "㄰-㆏"          # Hangul Compatibility Jamo
    "ㇰ-ㇿ"          # Katakana Phonetic Extensions
    "㐀-䶿"          # CJK Unified Ideographs Extension A
    "一-鿿"          # CJK Unified Ideographs
    "가-힯"          # Hangul Syllables
    "豈-﫿"          # CJK Compatibility Ideographs
    "ｦ-ﾟ"          # Halfwidth Katakana
    "\U00020000-\U0003134f"  # CJK Unified Ideographs Extensions B-H, Compatibility Supplement
    "]"
)


def comment_length(text: str) -> int:
    """D-IN-2 length of a comment's text: one per character, and
    :data:`~porchlamp.core.constants.SUBSTANTIVE_COMMENT_CJK_WEIGHT` per
    Chinese, Japanese or Korean letter (RFC-0011)."""
    return len(text) + (constants.SUBSTANTIVE_COMMENT_CJK_WEIGHT - 1) * len(_CJK_LETTERS.findall(text))


def is_substantive_comment(text: object) -> bool:
    """D-IN-2: whether a comment's text (GraphQL ``bodyText``: no markup) is
    long enough to answer a pull request, at least
    :data:`~porchlamp.core.constants.SUBSTANTIVE_COMMENT_MIN_CHARS` by
    :func:`comment_length`."""
    return isinstance(text, str) and comment_length(text) >= constants.SUBSTANTIVE_COMMENT_MIN_CHARS


def _node_login(actor: object) -> str:
    """The lower-cased ``login`` of a GraphQL actor node; ``""`` for none (a deleted account)."""
    login = actor.get("login") if isinstance(actor, Mapping) else None
    return login.lower() if isinstance(login, str) else ""


def is_bot_actor_node(actor: object) -> bool:
    """D-IN-1 over a GraphQL actor node (``__typename`` and ``login``):
    :func:`porchlamp.core.commit_domains.is_bot` with the node's type as the
    actor type. ``False`` for no node."""
    if not isinstance(actor, Mapping):
        return False
    return is_bot(CommitActor(
        login=str(actor.get("login") or ""), email="", actor_type=str(actor.get("__typename") or ""),
    ))


def maintainer_logins(prs: Iterable[object]) -> frozenset[str]:
    """D-IN-2: the accounts a recorded PR window shows holding access to the
    repository, by lower-cased login. One merged a PR in the record
    (``mergedBy``; merging takes write access), or closed a PR another account
    opened (a ``ClosedEvent`` in the PR's timeline whose actor is not its
    author; closing someone else's PR takes triage or write access, whatever
    association GitHub shows: org membership can be private, and committers
    who land changes outside GitHub show as ``CONTRIBUTOR``). Bots (D-IN-1)
    never: the query records ``mergedBy`` with a login only, so a bot merger
    is known by its login."""
    out: set[str] = set()
    for pr in prs:
        if not isinstance(pr, Mapping):
            continue
        merged_by = pr.get("mergedBy")
        login = _node_login(merged_by)
        if login and not is_bot_actor_node(merged_by):
            out.add(login)
        author = _node_login(pr.get("author"))
        timeline = pr.get("timelineItems")
        events = timeline.get("nodes") if isinstance(timeline, Mapping) else None
        for event in events if isinstance(events, list) else ():
            if not isinstance(event, Mapping) or event.get("__typename") != "ClosedEvent":
                continue
            actor = event.get("actor")
            login = _node_login(actor)
            if login and login != author and not is_bot_actor_node(actor):
                out.add(login)
    return frozenset(out)


def is_maintainer_response(node: object, pr_author: str, maintainers: frozenset[str]) -> bool:
    """D-IN-2: whether a review or comment node on a PR opened by ``pr_author``
    (lower-cased login) was written by the repository's own people: not a bot
    (D-IN-1), not the PR's author, and a MEMBER, OWNER or COLLABORATOR
    association or a login in ``maintainers`` (:func:`maintainer_logins` of
    the same window)."""
    if not isinstance(node, Mapping):
        return False
    author = node.get("author")
    login = _node_login(author)
    if is_bot_actor_node(author) or (login and login == pr_author):
        return False
    association = str(node.get("authorAssociation") or "").upper()
    return association in MAINTAINER_ASSOCIATIONS or (bool(login) and login in maintainers)


def first_substantive_response(pr: Mapping[str, Any], maintainers: frozenset[str]) -> tuple[str, str] | None:
    """D-IN-2: ``(createdAt, kind)`` of the earliest substantive response
    recorded on one PR node: a review in any state, or a comment whose text
    is substantive (:func:`is_substantive_comment`), by the repository's own
    people (:func:`is_maintainer_response`). ``kind`` is ``"review"`` or
    ``"comment"``; a review wins a tie. ``None`` when there is none."""
    pr_author = _node_login(pr.get("author"))
    best: tuple[datetime, str, str] | None = None
    for kind, source in (("review", "reviews"), ("comment", "comments")):
        connection = pr.get(source)
        nodes = connection.get("nodes") if isinstance(connection, Mapping) else None
        for node in nodes if isinstance(nodes, list) else ():
            if not is_maintainer_response(node, pr_author, maintainers):
                continue
            if kind == "comment" and not is_substantive_comment(node.get("bodyText")):
                continue
            stamp = node.get("createdAt")
            if not isinstance(stamp, str):
                continue
            try:
                when = _parse_iso(stamp)
            except ValueError:
                continue
            if best is None or when < best[0]:
                best = (when, stamp, kind)
    return None if best is None else (best[1], best[2])


def pr_window_coverage(
    data: Mapping[str, Any], window_start: datetime | None, *, fetched_at: str | None = None,
) -> tuple[bool, str | None]:
    """D-GATE-5: whether a recorded ``pr_window`` envelope ``data`` holds every
    PR opened since ``window_start`` (the gate's window start,
    ``now - PR_WINDOW_DAYS``; ``None`` judges against the record's own window).

    ``(True, None)`` when it does; else ``(False, cutoff)``, ``cutoff`` the time
    (ISO 8601) from which the record holds every PR, ``None`` when it does not
    say. The collector pages PRs newest first and stops at the first one
    opened before the record's own window (``window_anchor`` minus
    ``window_days``), at the end of the list, or at its PR cap, which it
    records as ``truncated``. So:

    * a record cut at the cap (``truncated`` true) holds every PR opened since
      its oldest recorded PR and none before: it covers the window only when
      that PR opened before ``window_start``, else that PR's ``createdAt`` is
      the cut-off;
    * an uncut record (``truncated`` false) holds every PR opened in its own
      window (anchored at ``window_anchor``, else ``fetched_at``): it covers
      the window when its own window opens at or before ``window_start``,
      else its own window start is the cut-off;
    * a record that does not say whether it was cut, or an uncut one with no
      readable anchor, cannot show that it covers the window.
    """
    truncated = data.get("truncated")
    if truncated is True:
        oldest: tuple[datetime, str] | None = None
        prs = data.get("prs")
        for pr in prs if isinstance(prs, list) else ():
            stamp = pr.get("createdAt") if isinstance(pr, Mapping) else None
            try:
                when = _parse_iso(stamp) if isinstance(stamp, str) else None
            except ValueError:
                when = None
            if when is not None and (oldest is None or when < oldest[0]):
                oldest = (when, stamp)
        if oldest is None:
            return False, None
        if window_start is not None and oldest[0] < window_start:
            return True, None
        return False, oldest[1]
    if truncated is not False:
        return False, None
    anchor_raw = data.get("window_anchor") if isinstance(data.get("window_anchor"), str) else fetched_at
    days = data.get("window_days", constants.PR_WINDOW_DAYS)
    try:
        anchor = _parse_iso(anchor_raw) if isinstance(anchor_raw, str) else None
    except ValueError:
        anchor = None
    if anchor is None or isinstance(days, bool) or not isinstance(days, int):
        return False, None
    record_start = anchor - timedelta(days=days)
    if window_start is None or record_start <= window_start:
        return True, None
    return False, record_start.isoformat()


#: #51: the key a ``pr_window`` envelope sets to ``False`` on a PR node whose
#: replies (``reviews``, ``comments``, ``reviewThreads``) it did not record.
#: Only a window with more than ``PR_CAP`` PRs has such nodes: its replies are
#: recorded on an equal-probability sample of the window's PRs
#: (:func:`reply_sampled_prs`). Snake case, so it cannot be mistaken for a
#: GraphQL field.
REPLIES_RECORDED_KEY = "replies_recorded"


def reply_sampled_prs(prs: Iterable[object]) -> list[Mapping[str, Any]]:
    """#51: the PR nodes whose replies a ``pr_window`` record holds, in order.

    Every node except those marked ``REPLIES_RECORDED_KEY: False``: in a window
    of at most ``PR_CAP`` PRs that is every node, and in a larger one it is the
    seeded sample. The reply measures (D-IN-2 responses: 1.1 TTFHR, 1.4 GR_14
    and D-GATE-5, 4.3 and the 4.1 review proxy, the D-GATE-6 hand-off notices,
    ``closed_unread``) read these nodes only; the sample has equal inclusion
    probabilities, so they need no weights. PR-level measures read every node.
    """
    return [pr for pr in prs if isinstance(pr, Mapping) and pr.get(REPLIES_RECORDED_KEY) is not False]


def record_cut_short(data: Mapping[str, Any] | None, required_days: int) -> bool:
    """#51 rule 5: whether a windowed PR record (``pr_window`` or
    ``author_history`` envelope ``data``) says it stopped at a PR cap before
    reaching ``window_anchor`` minus ``required_days``.

    Only a record that says so (``truncated`` true) is cut: its coverage is
    judged by :func:`pr_window_coverage` against that start, so a reader and
    D-GATE-5 share one definition. A truncated record with no readable
    ``window_anchor`` cannot show what it covers and counts as cut. A record
    that is not truncated, or holds no ``data``, is not cut.
    :func:`record_cut_short_at` is the same test against an instant.
    """
    if not isinstance(data, Mapping) or data.get("truncated") is not True:
        return False
    anchor_raw = data.get("window_anchor")
    try:
        anchor = _parse_iso(anchor_raw) if isinstance(anchor_raw, str) else None
    except ValueError:
        anchor = None
    if anchor is None:
        return True
    return record_cut_short_at(data, anchor - timedelta(days=required_days))


def record_cut_short_at(data: Mapping[str, Any] | None, start: datetime) -> bool:
    """#51 rule 5 against an instant: whether a windowed PR record says it
    stopped at a PR cap before reaching ``start``, whatever its own
    ``window_anchor``.

    :func:`record_cut_short` counts the days a measure needs back from the
    record's own anchor. A reader that joins the record to another envelope
    needs that envelope's instant instead, because the two are collected at
    different times: 1.2 DR_90 joins the ``pr_window`` record's decision cohort
    (PRs opened from its ``window_anchor`` minus 180 days) to the
    ``author_history`` record's per-author eligibility, so the history must
    hold every PR opened since that instant. As in :func:`record_cut_short`,
    only a ``truncated`` record is cut, judged by :func:`pr_window_coverage`;
    a ``start`` with no offset is read as UTC.
    """
    if not isinstance(data, Mapping) or data.get("truncated") is not True:
        return False
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    covered, _cutoff = pr_window_coverage(data, start)
    return not covered


def pr_window_cut_short(data: Mapping[str, Any] | None, window_start: datetime | None = None) -> bool:
    """#51 rule 5: whether a ``pr_window`` record stopped short of the window a
    measure reads, so the measure refuses it ("not measured") rather than be
    computed on the newest weeks alone.

    ``window_start`` is that window's start. ``None`` means the record's own
    window (``window_days``, default ``PR_WINDOW_DAYS``, before
    ``window_anchor``: :func:`record_cut_short`), the window of every measure
    over the record's PRs. GR_14 passes the start of the window it is computed
    over (derive's reference time minus ``PR_WINDOW_DAYS``): a truncated record
    that still reaches back past it holds every PR GR_14 counts. Only a
    truncated record is ever cut, judged by :func:`pr_window_coverage`.
    """
    if window_start is not None:
        return record_cut_short_at(data, window_start)
    days = data.get("window_days") if isinstance(data, Mapping) else None
    if isinstance(days, bool) or not isinstance(days, int) or days <= 0:
        days = constants.PR_WINDOW_DAYS
    return record_cut_short(data, days)


@dataclass(frozen=True)
class GhostRatePR:
    """One PR as the D-GATE-5 / 1.4 GR_14 measurement sees it.

    Built from the ``pr_window`` envelope (02 section 3 D-IN-4: PRs opened in
    the trailing 180 d), never from the 30-PR v1 ``pr_events`` sample.
    ``first_substantive_response_at`` is the D-IN-2 response time
    (:func:`first_substantive_response`); ``None`` when there is none.
    ``merged_at`` is the PR's ``mergedAt`` (``None`` when not
    merged). ``is_bot_author`` is D-IN-1 (``__typename == "Bot"``, ``[bot]``
    suffix, or the exact-match bot login list).
    """

    created_at: str
    is_external: bool
    is_bot_author: bool
    first_substantive_response_at: str | None
    merged_at: str | None = None
    is_draft: bool = False
    ready_for_review_at: str | None = None
    closed_at: str | None = None
    closed_by_author: bool = False
    closed_by_maintainer_at: str | None = None
    author_created_at: str | None = None
    number: int | None = None


def wilson_lower_bound_from_share(
    p_hat: float,
    n: int,
    z: float = constants.GATE5_WILSON_Z,
) -> float:
    """Compute the one-sided Wilson lower confidence bound for a sample share.

    Returns 0.0 when n <= 0 or p_hat <= 0.0. p_hat is clamped to [0.0, 1.0].
    """
    if n <= 0 or p_hat <= 0.0:
        return 0.0
    p = min(1.0, max(0.0, float(p_hat)))
    z2 = z * z
    term1 = p + z2 / (2.0 * n)
    radicand = (p * (1.0 - p) + z2 / (4.0 * n)) / n
    term2 = z * math.sqrt(max(0.0, radicand))
    denom = 1.0 + z2 / n
    return max(0.0, float((term1 - term2) / denom))


def wilson_lower_bound(
    unanswered: int,
    n: int,
    z: float = constants.GATE5_WILSON_Z,
) -> float:
    """Compute the one-sided Wilson lower confidence bound from counts."""
    if n <= 0 or unanswered <= 0:
        return 0.0
    return wilson_lower_bound_from_share(unanswered / n, n, z)


def is_black_hole_counts(unanswered: int, n: int) -> bool:
    """Return True iff a population meets GATE5_MIN_N and its Wilson lower bound
    exceeds GATE5_MAX_ZERO_RESPONSE_SHARE."""
    return (
        n >= constants.GATE5_MIN_N
        and wilson_lower_bound(unanswered, n) > constants.GATE5_MAX_ZERO_RESPONSE_SHARE
    )


@dataclass(frozen=True)
class Gate5CountedPR:
    number: int | None
    created_at: str
    clock_start_at: str
    answered_within_14d: bool
    first_response_at: str | None
    response_kind: str  # "review_or_comment", "merged", "closed_by_maintainer", or "none"


@dataclass(frozen=True)
class Gate5WindowBreakdown:
    unanswered: int
    population_n: int
    unanswered_share: float | None
    wilson_lower_bound: float
    is_black_hole: bool
    counted_prs: tuple[Gate5CountedPR, ...]
    excluded_draft: int
    excluded_self_closed: int
    excluded_new_account: int
    excluded_bot: int
    excluded_internal: int
    excluded_under_14d: int


def evaluate_gate5_window(
    prs: Sequence[GhostRatePR] | Iterable[GhostRatePR],
    now: datetime,
    *,
    observed_at: datetime | None = None,
) -> Gate5WindowBreakdown:
    """Evaluate D-GATE-5 window with the 5 false-positive reduction rules (RFC-0008)."""
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError(f"now must be timezone-aware, got {now!r}")
    if observed_at is not None and (
        observed_at.tzinfo is None or observed_at.tzinfo.utcoffset(observed_at) is None
    ):
        raise ValueError(f"observed_at must be timezone-aware, got {observed_at!r}")
    age_reference = now if observed_at is None else min(now, observed_at)
    window_start = now - timedelta(days=constants.PR_WINDOW_DAYS)
    deadline = timedelta(days=GATE5_RESPONSE_DEADLINE_DAYS)
    min_account_age = timedelta(days=constants.MIN_ACCOUNT_AGE_DAYS)

    unanswered = 0
    counted_prs: list[Gate5CountedPR] = []
    excluded_draft = 0
    excluded_self_closed = 0
    excluded_new_account = 0
    excluded_bot = 0
    excluded_internal = 0
    excluded_under_14d = 0

    for pr in prs:
        created = _parse_iso(pr.created_at)
        if not window_start <= created <= now:
            continue
        if not pr.is_external:
            excluded_internal += 1
            continue
        if pr.is_bot_author:
            excluded_bot += 1
            continue

        # Rule 3 (Account-age hygiene): author account must be >= 90 days old when PR was created
        if pr.author_created_at is not None:
            author_created = _parse_iso(pr.author_created_at)
            if (created - author_created) < min_account_age:
                excluded_new_account += 1
                continue

        # Rule 1 (Draft PR exclusion / clock start)
        if pr.ready_for_review_at is not None:
            ready_at = _parse_iso(pr.ready_for_review_at)
            clock_start = max(created, ready_at)
        elif pr.is_draft:
            excluded_draft += 1
            continue
        else:
            clock_start = created

        # 14-day observation age judged against clock_start
        if (age_reference - clock_start) < timedelta(days=GATE5_MIN_AGE_DAYS):
            excluded_under_14d += 1
            continue

        # Rule 4 (Maintainer response / merge / maintainer close within 14d)
        responses: list[tuple[datetime, str, str]] = []
        if pr.first_substantive_response_at is not None:
            resp_dt = _parse_iso(pr.first_substantive_response_at)
            if resp_dt <= clock_start + deadline:
                responses.append((resp_dt, "review_or_comment", pr.first_substantive_response_at))

        if pr.merged_at is not None:
            merged_dt = _parse_iso(pr.merged_at)
            if clock_start <= merged_dt <= clock_start + deadline:
                responses.append((merged_dt, "merged", pr.merged_at))

        if pr.closed_by_maintainer_at is not None:
            closed_m_dt = _parse_iso(pr.closed_by_maintainer_at)
            if clock_start <= closed_m_dt <= clock_start + deadline:
                responses.append((closed_m_dt, "closed_by_maintainer", pr.closed_by_maintainer_at))

        answered_within_14d = bool(responses)
        if answered_within_14d:
            responses.sort(key=lambda r: r[0])
            first_response_at: str | None = responses[0][2]
            response_kind: str = responses[0][1]
        else:
            first_response_at = None
            response_kind = "none"

        # Rule 2 (Author self-closed PR exclusion): an unanswered PR its author
        # closed within the response deadline (from clock_start), with no
        # substantive response at or before the close, is excluded. A close
        # after the deadline came too late to excuse the silence: the PR
        # stays counted, unanswered.
        if pr.closed_by_author and not answered_within_14d and pr.closed_at is not None:
            closed_dt = _parse_iso(pr.closed_at)
            had_prior_response = (
                pr.first_substantive_response_at is not None
                and _parse_iso(pr.first_substantive_response_at) <= closed_dt
            )
            if closed_dt <= clock_start + deadline and not had_prior_response:
                excluded_self_closed += 1
                continue

        counted_prs.append(
            Gate5CountedPR(
                number=pr.number,
                created_at=pr.created_at,
                clock_start_at=clock_start.isoformat(),
                answered_within_14d=answered_within_14d,
                first_response_at=first_response_at,
                response_kind=response_kind,
            )
        )
        if not answered_within_14d:
            unanswered += 1

    population_n = len(counted_prs)
    unanswered_share = (unanswered / population_n) if population_n > 0 else None
    w_lb = wilson_lower_bound(unanswered, population_n)
    is_bh = is_black_hole_counts(unanswered, population_n)

    return Gate5WindowBreakdown(
        unanswered=unanswered,
        population_n=population_n,
        unanswered_share=unanswered_share,
        wilson_lower_bound=w_lb,
        is_black_hole=is_bh,
        counted_prs=tuple(counted_prs),
        excluded_draft=excluded_draft,
        excluded_self_closed=excluded_self_closed,
        excluded_new_account=excluded_new_account,
        excluded_bot=excluded_bot,
        excluded_internal=excluded_internal,
        excluded_under_14d=excluded_under_14d,
    )


def zero_response_counts(
    prs: Iterable[GhostRatePR],
    now: datetime,
    observed_at: datetime | None = None,
) -> tuple[int, int]:
    """``(unanswered, n)`` for D-GATE-5 and 1.4 GR_14 -- the one function
    both read, so they cannot disagree.

    Population (02 section 2 D-GATE-5, section 4 1.4): external PRs by a
    non-bot author (D-IN-1), opened in the trailing
    :data:`constants.PR_WINDOW_DAYS` before ``now`` and at least
    :data:`GATE5_MIN_AGE_DAYS` old at ``min(now, observed_at)``.
    ``observed_at`` is when GitHub was read (the envelope's fetch time): a PR
    observed for less than the minimum age could still have been answered
    in time after the read, so it is not judged (lead ruling 2026-10-06);
    ``None`` judges age at ``now``. Unanswered: no D-IN-2 substantive
    response, merge, or maintainer close within :data:`GATE5_RESPONSE_DEADLINE_DAYS` of
    clock start (RFC-0008). ``now`` is the reference time, passed explicitly (D-VER-3).
    """
    breakdown = evaluate_gate5_window(prs, now, observed_at=observed_at)
    return breakdown.unanswered, breakdown.population_n


def _has_decided_external_pr(events: tuple[PRWindowEvent, ...], now: datetime) -> bool:
    """D-GATE-6 on the bundle's v1 ``pr_events``: True when one of them is an
    external PR merged or closed in the trailing
    :data:`GATE6_DECISION_WINDOW_DAYS`, decided at or before ``now``.

    Those events are the 30 newest PRs of any age, so they can show that a
    decided external PR exists but never that none does: in an active
    repository the newest 30 are mostly still open. A ``False`` here is no
    verdict; :attr:`GateEvidence.decided_external_pr` gives the negative."""
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


def is_black_hole_bundle(bundle: FeatureBundle) -> bool:
    """Return True iff ``bundle`` has a measured 1.4 GR_14 input that exceeds
    the D-GATE-5 zero-response ceiling (:data:`GATE5_MAX_ZERO_RESPONSE_SHARE`)
    under the Wilson lower confidence bound with at least :data:`GATE5_MIN_N`
    qualifying external PRs."""
    ghost = bundle.inputs.get(GHOST_RATE_INPUT_ID)
    return (
        isinstance(ghost, Measured)
        and ghost.n >= GATE5_MIN_N
        and wilson_lower_bound_from_share(float(ghost.value), int(ghost.n)) > GATE5_MAX_ZERO_RESPONSE_SHARE
    )


def evaluate_gates(
    bundle: FeatureBundle,
    evidence: GateEvidence,
    now: datetime,
    *,
    rank_black_holes: bool = False,
) -> GateResult:
    """Evaluate D-GATE-1..6 (02 section 2) for one repo at snapshot time.

    ``bundle`` must be a validated FeatureBundle v2; ``now`` is the snapshot
    reference time, timezone-aware. All six gates run; the first failure in
    the design's fixed order is ``reason`` (``eligible=False``), UNDECIDED
    gates are recorded in ``undecided`` by decision id, and D-GATE-6 routing
    is recorded in ``routed`` without failing the repo.

    When ``rank_black_holes=True``, D-GATE-5 does not disqualify a repository
    whose 14-day zero-response rate exceeds :data:`GATE5_MAX_ZERO_RESPONSE_SHARE`;
    instead the repository remains eligible for stage 2-9 scoring and ranking
    (where 1.4 ``gr14_ghost_rate`` scores 0.0 and ``"black_hole"`` is surfaced
    on ``ScoreRecord.suspicion``).

    A repository with ``evidence.review_handoff`` true hands review off to
    another system: D-GATE-5 is recorded UNDECIDED (never failed, whatever its
    ghost rate) and D-GATE-6 routes it off-platform, whether or not it has a
    decided external PR. A repository whose ``evidence.pr_rule_inputs_recorded``
    is ``False`` is neither passed nor failed at D-GATE-5: it is recorded
    UNDECIDED, because the exclusion rules could not run. Nor is one whose
    ``evidence.pr_window_complete`` is ``False``: its record stops short of
    the window (``evidence.pr_window_cutoff``), and a share of the newest
    weeks is not the window's share. D-GATE-6 fails a
    repository only when ``evidence.decided_external_pr`` is ``False`` (a
    complete PR record with no decided external PR); the v1 ``pr_events`` can
    add a pass, never a failure. Gates 1-4 still decide as before."""
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
    # population. Unmeasured or n below the minimum -> UNDECIDED. A repository
    # that hands review off is UNDECIDED too: its PRs are reviewed somewhere
    # GitHub cannot see, so an unanswered-on-GitHub share is not a verdict.
    # A window whose PR records lack the exclusion rules' inputs is UNDECIDED
    # whatever its share: the missing fields move the share both ways. So is a
    # record that stops short of the window (the collector's PR cap): its
    # share is the newest weeks', never judged as the window's.
    handoff = evidence.review_handoff is True
    ghost = bundle.inputs.get(GHOST_RATE_INPUT_ID)
    if (
        handoff
        or evidence.pr_rule_inputs_recorded is False
        or evidence.pr_window_complete is False
        or not isinstance(ghost, Measured)
        or ghost.n < GATE5_MIN_N
    ):
        undecided.append(GATE_5)
    elif (
        wilson_lower_bound_from_share(float(ghost.value), int(ghost.n)) > GATE5_MAX_ZERO_RESPONSE_SHARE
        and not rank_black_holes
    ):
        failures.append((5, REASON_BLACK_HOLE))

    # D-GATE-6: PR workflow (a review hand-off routes off-platform; else a
    # decided external PR, else off-platform routing, else unrated -- but only
    # when a complete PR record shows no decided external PR).
    if handoff:
        routed = ROUTED_OFF_PLATFORM
    elif not (evidence.decided_external_pr is True or _has_decided_external_pr(events, now)):
        if evidence.off_platform_marker is True:
            routed = ROUTED_OFF_PLATFORM
        elif evidence.off_platform_marker is False and evidence.decided_external_pr is False:
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
