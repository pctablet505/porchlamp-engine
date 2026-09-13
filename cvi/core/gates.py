"""CVI v2 stage-0 eligibility gates (02 section 2: D-GATE-1..6, D-EVID-1).

``evaluate_gates`` runs the six identity/eligibility gates over a validated
:class:`~cvi.core.models.FeatureBundle` plus :class:`GateEvidence` (the gate
inputs the bundle schema does not carry: W1-9 tree/manifest/licence/off-platform
markers, W1-5 vitality commits, universe fork-parent membership). ``now`` is
the snapshot reference time, passed explicitly (D-VER-3: the core owns no
clock). Every gate is evaluated; the first failing gate in the design's fixed
order is the ``reason``; a gate whose input is insufficient never fails the
repo but is recorded in ``GateResult.undecided`` by its decision id
(``"D-GATE-1"`` .. ``"D-GATE-6"``). D-GATE-6 is routing, not failure: it
passes, routes to the off-platform workflow, or reports
``unrated: no_external_workflow``.

``evidence_sufficiency`` is the D-EVID-1 stage-3 gate on the composite
coverage C (D-MISS-3; computed by W2-5): C >= 0.60 is rated, below is
``unrated: insufficient_evidence`` (listed, not ranked).

Packet W2-8 (constants freeze) relocated the seven numeric gate thresholds
below into ``cvi.core.constants_v2``, imported back here under their
original names (R-2). The D-GATE-3 OSI set (``OSI_APPROVED_LICENSES``) and
``UNDETERMINED_SPDX`` stay defined in this module: they are license-id
vocabularies sourced from an external standard (SPDX/OSI), not scoring
thresholds -- the constants freeze's RFC procedure (03 section 7) is for a
tunable that changes what the ranking rewards, not for tracking upstream
license-list updates.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from dataclasses import dataclass

from cvi.core import constants_v2
from cvi.core.models import FeatureBundle, GateResult, PRWindowEvent

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

#: ``GateResult.routed`` value for the D-GATE-6 off-platform workflow.
ROUTED_OFF_PLATFORM = "off_platform"

#: D-GATE-4: trailing window for non-bot User commits on the default branch.
GATE4_VITALITY_WINDOW_DAYS: int = constants_v2.GATE4_VITALITY_WINDOW_DAYS
#: D-GATE-5: minimum population of external PRs >= 14 d old, else UNDECIDED.
GATE5_MIN_N: int = constants_v2.GATE5_MIN_N
#: D-GATE-5: a PR is ">= 14 d old" at the snapshot reference time.
GATE5_MIN_AGE_DAYS: int = constants_v2.GATE5_MIN_AGE_DAYS
#: D-GATE-5: the substantive-response deadline after opening.
GATE5_RESPONSE_DEADLINE_DAYS: int = constants_v2.GATE5_RESPONSE_DEADLINE_DAYS
#: D-GATE-5: pass ceiling on the share with zero substantive response.
GATE5_MAX_ZERO_RESPONSE_SHARE: float = constants_v2.GATE5_MAX_ZERO_RESPONSE_SHARE
#: D-GATE-6: horizon for a decided external PR.
GATE6_DECISION_WINDOW_DAYS: int = constants_v2.GATE6_DECISION_WINDOW_DAYS
#: D-EVID-1: composite coverage threshold (D-MISS-3).
EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE: float = constants_v2.EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE

#: D-GATE-3: SPDX states that mean "no usable licence recorded" (the gate then
#: looks for a ``LICENSE*`` file before failing). Matches W1-9's
#: ``D_GATE3_UNLICENSED_SPDX``; the frozen-constants module is owned by W2-1.
UNDETERMINED_SPDX: frozenset[str | None] = frozenset({None, "NOASSERTION", "OTHER"})

#: D-GATE-3: OSI-approved SPDX ids (see module docstring for the source).
OSI_APPROVED_LICENSES: frozenset[str] = frozenset({
    "0BSD",     "AAL",     "AFL-1.1",     "AFL-1.2",
    "AFL-2.0",     "AFL-2.1",     "AFL-3.0",     "AGPL-3.0",
    "AGPL-3.0-only",     "AGPL-3.0-or-later",     "ALGLIB-Documentation",     "APL-1.0",
    "APSL-1.0",     "APSL-1.1",     "APSL-1.2",     "APSL-2.0",
    "Apache-1.1",     "Apache-2.0",     "Artistic-1.0",     "Artistic-1.0-Perl",
    "Artistic-1.0-cl8",     "Artistic-2.0",     "BSD-1-Clause",     "BSD-2-Clause",
    "BSD-2-Clause-Patent",     "BSD-3-Clause",     "BSD-3-Clause-LBNL",     "BSD-3-Clause-Open-MPI",
    "BSD-ask-to-endorse",     "BSL-1.0",     "BlueOak-1.0.0",     "CAL-1.0",
    "CAL-1.0-Combined-Work-Exception",     "CATOSL-1.1",     "CDDL-1.0",     "CDDL-1.1",
    "CECILL-2.1",     "CERN-OHL-P-2.0",     "CERN-OHL-S-2.0",     "CERN-OHL-W-2.0",
    "CNRI-Python",     "CNRI-Python-GPL-Compatible",     "CPAL-1.0",     "CPL-1.0",
    "CUA-OPL-1.0",     "ECL-1.0",     "ECL-2.0",     "EFL-1.0",
    "EFL-2.0",     "EPL-1.0",     "EPL-2.0",     "EUDatagrid",
    "EUPL-1.1",     "EUPL-1.2",     "Entessa",     "Fair",
    "Frameworx-1.0",     "GPL-1.0",     "GPL-2.0",     "GPL-2.0+",
    "GPL-2.0-only",     "GPL-2.0-or-later",     "GPL-3.0",     "GPL-3.0+",
    "GPL-3.0-only",     "GPL-3.0-or-later",     "GPL-3.0-with-GCC-exception",     "HPND",
    "ICU",     "IPA",     "IPL-1.0",     "ISC",
    "Intel",     "Jam",     "LGPL-2.0",     "LGPL-2.0+",
    "LGPL-2.0-only",     "LGPL-2.0-or-later",     "LGPL-2.1",     "LGPL-2.1+",
    "LGPL-2.1-only",     "LGPL-2.1-or-later",     "LGPL-3.0",     "LGPL-3.0+",
    "LGPL-3.0-only",     "LGPL-3.0-or-later",     "LPL-1.0",     "LPL-1.02",
    "LPPL-1.3c",     "LiLiQ-P-1.1",     "LiLiQ-R-1.1",     "LiLiQ-Rplus-1.1",
    "MIT",     "MIT-0",     "MIT-Modern-Variant",     "MPL-1.0",
    "MPL-1.1",     "MPL-2.0",     "MPL-2.0-no-copyleft-exception",     "MS-PL",
    "MS-RL",     "MirOS",     "Motosoto",     "MulanPSL-2.0",
    "Multics",     "NASA-1.3",     "NCSA",     "NGPL",
    "NIST-Software",     "NPOSL-3.0",     "NTP",     "Naumen",
    "Nokia",     "OCLC-2.0",     "OFL-1.1",     "OFL-1.1-RFN",
    "OFL-1.1-no-RFN",     "OGTSL",     "OLDAP-2.8",     "OLFL-1.3",
    "OSC-1.0",     "OSET-PL-2.1",     "OSL-1.0",     "OSL-2.0",
    "OSL-2.1",     "OSL-3.0",     "PHP-3.0",     "PHP-3.01",
    "PostgreSQL",     "Python-2.0",     "Python-2.0.1",     "QPL-1.0",
    "RPL-1.1",     "RPL-1.5",     "RPSL-1.0",     "RSCPL",
    "SISSL",     "SPL-1.0",     "SimPL-2.0",     "Sleepycat",
    "UCL-1.0",     "UPL-1.0",     "Unicode-3.0",     "Unicode-DFS-2016",
    "Unlicense",     "VSL-1.0",     "W3C",     "W3C-20150513",
    "Watcom-1.0",     "WordNet",     "Xnet",     "ZPL-2.0",
    "ZPL-2.1",     "Zlib",     "curl",     "eCos-2.0",
    "wxWindows",})


def _parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp (``Z`` accepted) into an aware datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class GateEvidence:
    """Stage-0 evidence the FeatureBundle v2 schema does not carry.

    A ``None`` field means "not collected / unknown": the affected gate is
    recorded UNDECIDED (02 section 2) instead of failing the repo. Fields map
    to their collectors of record: ``manifest_found`` / ``license_file_present``
    / ``off_platform_marker`` to W1-9's tree and marker evidence,
    ``vitality_commits_180d`` to the W1-5 commit evidence on the default
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
    #: D-GATE-6: an off-platform contribution marker (W1-9 markers).
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


def _zero_substantive_response(event: PRWindowEvent) -> bool:
    """D-GATE-5 numerator for one PR: no D-IN-2 response within 14 d of opening.

    ``first_human_review_at`` carries the W1-3 typed-response timestamp (a
    non-author, non-bot MEMBER/OWNER/COLLABORATOR review or >= 40-character
    comment); ``None`` means no such response was recorded."""
    if event.first_human_review_at is None:
        return True
    response = _parse_iso(event.first_human_review_at) - _parse_iso(event.created_at)
    return response > timedelta(days=GATE5_RESPONSE_DEADLINE_DAYS)


def _has_decided_external_pr(events: tuple[PRWindowEvent, ...], now: datetime) -> bool:
    """D-GATE-6: >= 1 external PR decided (merged or closed) in the trailing 365 d.

    Every event in the 180-day PR window was opened inside that window, so a
    decision recorded on it necessarily falls inside the 365-day horizon; the
    decision timestamp is additionally required to be <= ``now`` (decided as
    of the snapshot)."""
    for event in events:
        if not event.is_external:
            continue
        decided_at = event.merged_at or event.closed_at
        if decided_at is not None and _parse_iso(decided_at) <= now:
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
    if spdx in OSI_APPROVED_LICENSES:
        pass
    elif spdx in UNDETERMINED_SPDX:
        if evidence.license_file_present is True:
            pass
        elif evidence.license_file_present is False:
            failures.append((3, REASON_UNLICENSED))
        else:
            undecided.append(GATE_3)
    else:
        failures.append((3, REASON_UNLICENSED))

    # D-GATE-4: vitality (>= 1 non-bot User commit, default branch, 180 d).
    if evidence.vitality_commits_180d is None:
        undecided.append(GATE_4)
    elif evidence.vitality_commits_180d < 1:
        failures.append((4, REASON_STALE))

    # D-GATE-5: not a black hole (windowed denominator, minimum n, D-IN-2
    # response within 14 d of opening).
    population = [
        event
        for event in events
        if event.is_external
        and (now - _parse_iso(event.created_at)) >= timedelta(days=GATE5_MIN_AGE_DAYS)
    ]
    if len(population) < GATE5_MIN_N:
        undecided.append(GATE_5)
    else:
        unanswered = sum(1 for event in population if _zero_substantive_response(event))
        if unanswered / len(population) > GATE5_MAX_ZERO_RESPONSE_SHARE:
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

    ``coverage`` is the composite coverage C of D-MISS-3 (stage-6 output,
    W2-5), within [0, 1]. Below the threshold the repo is listed but not
    ranked (``unrated: insufficient_evidence``)."""
    if isinstance(coverage, bool) or not isinstance(coverage, (int, float)):
        raise ValueError(f"coverage must be a real number, got {coverage!r}")
    if not 0.0 <= coverage <= 1.0:
        raise ValueError(f"coverage must be within [0, 1], got {coverage!r}")
    if coverage >= EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE:
        return GateResult(eligible=True, reason=None, routed=None, undecided=())
    return GateResult(eligible=False, reason=REASON_INSUFFICIENT_EVIDENCE, routed=None, undecided=())
