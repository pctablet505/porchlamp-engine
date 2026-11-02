"""Frozen Porchlamp v2 constants (D-VER-2, R-2).

One definition per fact: every constant the v2 core contract names lives in
this module. ``CONSTANTS_HASH`` is ``sha256(canonical JSON of the full
constants dict)``; it is stamped into every stored ScoreRecord row and the
snapshot manifest so a reader can verify the constants a score was built
from. Changing any constant changes the hash (tests/core/test_constants_hash.py).

The canonical JSON below is the JSON-native subset of
``porchlamp.core.models.canonical_json`` (byte-identical on JSON-native values);
``porchlamp.core`` may not import ``porchlamp.collect`` (04 section 3 import rule), so the
expression is kept local here instead of shared across the boundary.
"""
from __future__ import annotations

import hashlib
import json
from types import MappingProxyType
from typing import Mapping

#: Benchmark version for the v2 line (D-VER-1). Frozen at "2.0.0-rc1":
#: until release, constants may still change, but only through the RFC
#: procedure in docs/v2/rfcs/README.md.
BENCHMARK_VERSION: str = "2.0.0-rc1"

#: FeatureBundle document version (04 section 3: data/derived/<key>.json).
SCHEMA_VERSION: int = 2

#: Length of the PR window every PR-based parameter measures over (04 section 3).
PR_WINDOW_DAYS: int = 180

#: D-IN-4 (RFC-0013): a ``pr_window`` record holds every PR opened in the window.
#: Up to this many, each PR is recorded with every field, its replies and reviews
#: included (the whole window); a collector run told to keep no reply sample
#: stops here and records the window ``truncated``.
PR_CAP: int = 2000

#: D-IN-4 (RFC-0013): a window of more than ``PR_CAP`` PRs records every PR's own
#: fields, and replies and reviews on an equal-probability sample of this many of
#: them; the reply measures (1.1, 1.4 and D-GATE-5, 4.3) read the sample.
PR_SAMPLE_CAP: int = 1000

#: D-IN-4 (RFC-0013): the seed of that sample. A PR is sampled when its
#: ``sha256("<seed>:<owner>/<name>:<number>")`` is among the ``PR_SAMPLE_CAP``
#: smallest of the window's, so a rerun over the same PRs draws the same sample.
PR_SAMPLE_SEED: int = 20260912

#: D-IN-4 Commits (RFC-0021): a ``commits_365d`` record holds every
#: default-branch commit of the trailing 365 days when GitHub counts at most
#: this many; above it, this many in whole REST pages of 100 spread evenly over
#: the year (``sampled``), with GitHub's count of the year's commits
#: (``total``). 3.2 EF and 3.1's ``Signed-off-by`` share read the rows; 2.2's
#: commit frequency reads the total.
COMMIT_CAP: int = 5000

#: D-IN-4 Commits (RFC-0021): the newest-commits cap every ``commits_365d``
#: collector applied before RFC-0021, without a total. A record that stopped
#: there (``truncated``, or a bare row list of this many rows) holds only the
#: newest days of a busier year, so no windowed commit measure reads it.
LEGACY_COMMIT_CAP: int = 1000

#: D-IN-4 (RFC-0022): each PR whose replies a ``pr_window`` record holds has its
#: reviews and its comments recorded whole up to this many of each, oldest first.
#: The window page reads the first 10 reviews and 20 comments of each PR (all it
#: kept before), and the collector reads a longer list on by PR number until it
#: is whole or holds this many; a list still longer stays marked cut
#: (``pageInfo.hasNextPage``), and no reply measure reads a PR whose recorded part
#: holds no D-IN-2 response as unanswered (``porchlamp.core.gates.reply_sampled_prs``).
PR_REPLY_NODE_CAP: int = 300

#: D-MISS-1 reason for a Measured ECDF-kind input whose (ecosystem, param)
#: has no frozen population at all (``porchlamp.core.engine``, added 2026-09-12).
REASON_NO_POPULATION: str = "no_population"

#: D-MISS-5 (02 section 5, added 2026-09-13): the closed list of
#: ``(parameter_id, bundle_fact, value)`` triples that MAY assign
#: ``Unmeasured(not_applicable)`` to a parameter input. Assignable only by a
#: design-level rule keyed on a fact recorded at universe entry -- never by a
#: collector outcome and never by repo-authored text (03 section 4).
#: ``porchlamp.derive.bundle._resolve_dependents_deduped`` reads this list instead
#: of an inline ``ecosystem == "systems"`` check (one source); ``porchlamp.core.
#: engine`` downgrades any ``not_applicable`` claim whose
#: ``(parameter, fact, value)`` is not in this list to
#: ``Unmeasured(collector_failure)`` -- a bundle or collector can
#: never assert ``not_applicable`` on its own.
NOT_APPLICABLE_RULES: tuple[tuple[str, str, str], ...] = (
    ("dependents_deduped", "ecosystem", "systems"),
    ("downloads_monthly", "ecosystem", "go"),
    ("downloads_monthly", "ecosystem", "maven"),
    ("downloads_monthly", "ecosystem", "systems"),
)

#: D-MISS-5 consequence for 2.2 (OpenSSF criticality, 02 section 5):
#: the minimum count of the ten Pike signals that must be present for the
#: no-dependents-signal proxy (``method="proxy:no_dependents_signal"``) to
#: fire when the dependents signal is ``not_applicable``/``third_party_gap``;
#: fewer is ``Unmeasured(insufficient_n)``.
OPENSSF_PROXY_MIN_SIGNALS: int = 8

#: Pike signals staleness threshold: a windowed count's own fetch-time
#: ``window_anchor`` more than this many days from the derive clock means
#: ``openssf_criticality`` is ``Unmeasured(collector_failure)`` rather than
#: silently stale (F5 ruling).
WINDOW_STALENESS_DAYS: int = 14

#: Unmeasured.reason vocabulary (D-MISS-1). ``REASON_NO_POPULATION`` was
#: added 2026-09-12 (an ECDF-normalised parameter with no rated, measured
#: repository to build a population from, in its ecosystem or globally, at
#: this snapshot).
UNMEASURED_REASONS: tuple[str, ...] = (
    "third_party_gap",
    "collector_failure",
    "insufficient_n",
    "not_applicable",
    REASON_NO_POPULATION,
)

#: Number of decimal places the composite score is rounded to everywhere it
#: is compared or displayed (D-AGG-2, 04 section 8): ``porchlamp.core.aggregate``'s
#: final rounding and every ``round(..., 2)`` site in ``porchlamp.core.tiebreak``'s
#: D-TIE-1 composite level and D-TIE-2 tie-group boundary.
COMPOSITE_DECIMALS: int = 2

#: D-NORM-2: minimum population per ecosystem; below it the population falls
#: back to the global population and carries ``population_fallback``.
MIN_POPULATION: int = 30

#: D-MISS-3 ceiling bands, above the D-EVID-1 rated threshold.
#: ``NO_CEILING_MIN_COVERAGE = 0.90`` (amended by RFC-0024 from 0.95): permits
#: at most one missing parameter in any pillar (single-parameter global weights
#: span 0.025..0.100, leaving C in 0.9000..0.9750) while capping two missing
#: parameters.
CEILING_A_MIN_COVERAGE: float = 0.80
NO_CEILING_MIN_COVERAGE: float = 0.90

#: D-EVID-1: composite coverage threshold (D-MISS-3) below which a repo is
#: unrated.
EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE: float = 0.60

#: D-EVID-1 (RFC-0024): pillars that must each have at least one measured
#: parameter for a repo to be rated, whatever its composite coverage.
#: Responsiveness measures P(decision), the factor D-DEF-2 evaluates first; the
#: composite renormalises over measured pillars (D-MISS-2), so without this rule
#: a repo whose answers to outside pull requests were never observed is ranked
#: on its other pillars alone (275 rated repos on the 2026-10-21 data, 64 of
#: them with every recorded outside PR unanswered within 14 days).
EVIDENCE_GATE_REQUIRED_PILLARS: tuple[str, ...] = ("responsiveness",)

#: D-GATE-4: trailing window for non-bot User commits on the default branch.
GATE4_VITALITY_WINDOW_DAYS: int = 180
#: D-GATE-5: minimum population of external PRs >= 14 d old, else UNDECIDED.
GATE5_MIN_N: int = 10
#: D-GATE-5: a PR is ">= 14 d old" at the snapshot reference time.
GATE5_MIN_AGE_DAYS: int = 14
#: D-GATE-5: the substantive-response deadline after opening.
GATE5_RESPONSE_DEADLINE_DAYS: int = 14
#: D-GATE-5: 95% one-sided standard normal quantile for the Wilson lower confidence bound (RFC-0008).
GATE5_WILSON_Z: float = 1.6448536269514722
#: D-GATE-5: pass ceiling on the share with zero substantive response.
GATE5_MAX_ZERO_RESPONSE_SHARE: float = 0.50
#: D-IN-2 (RFC-0011): a comment answers a pull request when its text, as GitHub renders it
#: (``bodyText``: no markup, so an image alone has no text), is at least this long ...
SUBSTANTIVE_COMMENT_MIN_CHARS: int = 40
#: ... where each Chinese, Japanese or Korean letter (a Han ideograph, kana or Hangul) counts
#: this many times: those scripts say in one letter about what Latin text says in three, so
#: 14 of them reach the minimum.
SUBSTANTIVE_COMMENT_CJK_WEIGHT: int = 3
#: 4.3 substantive review rate (RFC-0018): minimum effective length (via
#: ``porchlamp.core.gates.comment_length``) for a top-level review body or
#: D-IN-2 maintainer issue comment to count as substantive without inline
#: diff comments.
SUBSTANTIVE_REVIEW_BODY_CHARS: int = 80
#: 4.3 substantive review rate (RFC-0018): a normalized top-level review body
#: or maintainer issue comment repeated across at least this many distinct PRs
#: in the repository's PR window is treated as canned boilerplate rather than
#: PR-specific substantive review.
SUBSTANTIVE_REVIEW_BOILERPLATE_MIN_PRS: int = 3
#: D-IN-1 (RFC-0025): an account that is no bot but runs automation. A reply template (the first
#: ``AUTOMATION_TEMPLATE_WORDS`` words of a comment, names, numbers and links removed) that one account
#: posted on at least ``AUTOMATION_MIN_PRS`` PRs of a window, on at least ``AUTOMATION_FAST_SHARE`` of them
#: within ``AUTOMATION_FAST_SECONDS`` of the PR opening, is automation: it answers no PR. A person does not
#: answer 80% of 20 PRs inside a minute by hand.
AUTOMATION_MIN_PRS: int = 20
AUTOMATION_FAST_SECONDS: int = 60
AUTOMATION_FAST_SHARE: float = 0.80
AUTOMATION_TEMPLATE_WORDS: int = 2
#: D-GATE-6: horizon for a decided external PR.
GATE6_DECISION_WINDOW_DAYS: int = 365
#: D-GATE-6 outside-traffic floor (RFC-0027): a repository whose complete PR window
#: holds fewer than this many outside PRs (external, non-bot authors) in the trailing
#: :data:`PR_WINDOW_DAYS` is not a place to contribute and is not rated.
GATE6_MIN_OUTSIDE_PRS: int = 3
#: D-GATE-6 review hand-off (RFC-0007): a repository whose own notices send
#: outside PRs to another review system (Gerrit, a mailing list, an internal
#: repository) hands review off, and is routed off-platform instead of being
#: failed at D-GATE-5. It hands off when at least this many outside PRs carry
#: such a notice ...
GATE6_HANDOFF_MIN_PRS: int = 5
#: ... and those are at least this share of its outside PRs (external, non-bot
#: authors in the recorded PR window).
GATE6_HANDOFF_MIN_SHARE: float = 0.5
#: D-GATE-6 review hand-off: a comment by an account other than the PR's author
#: is a notice when its text contains one of these phrases (compared
#: lower-cased)...
GATE6_HANDOFF_NOTICES: tuple[str, ...] = (
    "has been imported to gerrit for code review",
    "welcome to gitgitgadget",
    "import it to our internal repository for further review",
    # Meta imports outside PRs into its internal review system (RFC-0011); its bot words the
    # notice both ways.
    "this pull request has been imported",
    "has imported this pull request",
)
#: ... or when its author's login (lower-cased) starts with one of these
#: (GitGitGadget relays git/git pull requests to the mailing list).
GATE6_HANDOFF_RELAY_LOGIN_PREFIXES: tuple[str, ...] = ("gitgitgadget",)

#: D-MAT-1 leverage/friction axis weights and the quadrant boundary.
LEVERAGE_WEIGHTS: Mapping[str, float] = MappingProxyType({
    "criticality": 0.50,
    "rigor": 0.30,
    "governance": 0.20,
})
FRICTION_WEIGHTS: Mapping[str, float] = MappingProxyType({
    "responsiveness": 0.50,
    "governance": 0.30,
    "accessibility": 0.20,
})
QUADRANT_THRESHOLD: float = 50.0

#: D-MAT-2 tier cutoffs (kept, O-5), worst to best; below the last cutoff is
#: the floor tier ``"D"`` (``porchlamp.core.aggregate.TIER_FLOOR`` -- a bare label
#: with no accompanying threshold, excluded from the hash). A score exactly
#: at a cutoff belongs to the higher tier (``>=``), matching the v1 convention
#: these cutoffs are kept from. A cutoff or an ordering change moves
#: ``ScoreRecord.tier`` for rated repos exactly like a weight change does, so
#: it belongs in the hash the same way.
TIER_CUTOFFS: tuple[tuple[float, str], ...] = (
    (90.0, "S+"),
    (80.0, "S"),
    (70.0, "A"),
    (55.0, "B"),
    (40.0, "C"),
)

#: Every tier and ceiling label, worst to best, for ``apply_ceiling``'s
#: ``min(tier_by_score, ceiling)`` (D-MAT-2).
TIER_ORDER: tuple[str, ...] = ("D", "C", "B", "A", "S", "S+")


#: 02 section 4 curve-shape parameters.
#: Float tolerance for the D-PARAM-MONO check (``porchlamp.core.params.curves``).
MONO_TOLERANCE: float = 1e-9
#: Shared rate calibration (1.2 DR_90, 1.3 MR_first, 4.3 substantive review
#: rate): full marks at an 80% rate, linear below.
RATE_SLOPE: float = 125.0
#: Shared cohort-rate calibration (5.2 conversion, 5.3 RR_90): full marks at
#: a 50% cohort rate, linear below.
COHORT_RATE_SLOPE: float = 200.0
#: 1.1 TTFHR knee: full marks at 12 h or less of median opening-to-first-
#: substantive-response time.
TTFHR_KNEE_HOURS: float = 12.0
#: 1.1 TTFHR decay: points lost per doubling of the median past the knee.
TTFHR_SLOPE_PER_DOUBLING: float = 25.0
#: 1.4 GR_14: points lost per unit of ghost rate.
GHOST_RATE_SLOPE: float = 200.0
#: 5.1 supply cap points: full marks at 10 open, unassigned, up-to-date
#: newcomer issues.
SUPPLY_SLOPE: float = 10.0
#: 3.2 EF slope: points per organisation at the 50% commit share.
ELEPHANT_SLOPE: float = 25.0

#: 3.1 legal-IP regime scores (owner decision O-1: NONE 85).
LEGAL_IP_SCORES: Mapping[str, float] = MappingProxyType({
    "DCO": 100.0,
    "NONE": 85.0,
    "FOUNDATION_CLA": 75.0,
    "CORP_CLA": 25.0,
})
#: 3.1 "owner in the foundation list" (02 section 4): the GitHub owners,
#: lower-cased, whose repositories take contributions under a foundation's
#: agreement. Ownership is the weakest 3.1 signal (RFC-0017): it names a
#: generic CLA check's beneficiary as a foundation rather than a company, and
#: decides FOUNDATION_CLA on its own only when no CLA check and no DCO
#: evidence is recorded. Relocated from ``scripts/derive.py``, where it was
#: unhashed, with ``seleniumhq`` (Software Freedom Conservancy CLA) and
#: ``trinodb`` (Trino Software Foundation CLA) added on recorded evidence.
FOUNDATION_ORGS: frozenset[str] = frozenset({
    "apache", "chaoss", "cncf", "eclipse", "fdiooss", "kubernetes", "linuxfoundation",
    "llvm", "openjs-foundation", "openstack", "seleniumhq", "trinodb",
})
#: 3.3 promotion-ladder rung scores.
PROMOTION_LADDER_SCORES: Mapping[str, float] = MappingProxyType({
    "VERIFIED": 100.0,
    "DOCUMENTED": 75.0,
    "AD_HOC": 40.0,
    "CLOSED": 10.0,
})
#: 3.3 VERIFIED (RFC-0016): the fewest days between an account's first PR that
#: another account merged and its own first merge or close of a PR in the
#: recorded window. A shorter gap is what a maintainer whose earlier actions
#: fall outside the record, or a new hire given access, looks like; an outside
#: contributor's path to maintainer takes weeks.
PROMOTION_MIN_OUTSIDE_DAYS: int = 30
#: 4.2 AI posture scores, persona-conditional.
AI_POSTURE_SCORES: Mapping[str, Mapping[str, float]] = MappingProxyType({
    "REGULATED": MappingProxyType({"human": 95.0, "ai": 95.0}),
    "PROSCRIBED": MappingProxyType({"human": 85.0, "ai": 25.0}),
    "AD_HOC": MappingProxyType({"human": 40.0, "ai": 40.0}),
})

#: D-GATE-3 OSI-approved SPDX ids (the union of the SPDX license-list-data
#: ``isOsiApproved`` flag and the opensource.org approved-licences table,
#: fetched 2026-09-12). This is not a bare
#: identifier vocabulary like ``GATE_1..6`` -- D-GATE-3 *interprets* it as a
#: predicate (membership decides eligible vs ``REASON_UNLICENSED``). An
#: upstream SPDX/OSI list refresh is therefore an RFC-worthy event
#: (``docs/v2/rfcs/README.md``), not a silent data update.
#: Kept as a ``frozenset`` here for O(1) gate-time membership; hashed as
#: ``tuple(sorted(OSI_APPROVED_LICENSES))`` in ``constants_dict()``.
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

#: OpenSSF/Pike criticality (input 2.2, ``porchlamp.core.openssf``): the
#: ``porchlamp.models.OpenSSFSignals`` field name -> the alpha_i/T_i key (spec
#: 2.2, 10.1): a weight, threshold or field-mapping change moves
#: input 2.2's score exactly like any other curve parameter.
SIGNAL_FIELDS: Mapping[str, str] = MappingProxyType({
    "created_since_months": "created_since",
    "updated_since_months": "updated_since",
    "contributor_count": "contributor_count",
    "org_count": "org_count",
    "commit_frequency_weekly": "commit_frequency",
    "recent_releases_count": "recent_releases_count",
    "closed_issues_count_90d": "closed_issues_count",
    "updated_issues_count_90d": "updated_issues_count",
    "comment_frequency": "comment_frequency",
    "dependents_count": "dependents_count",
})

#: Default signal weights (alpha_i) and thresholds (T_i) -- spec 2.2, 10.1.
OPENSSF_WEIGHTS: Mapping[str, float] = MappingProxyType({
    "created_since": 1.0,
    "updated_since": -1.0,
    "contributor_count": 2.0,
    "org_count": 1.0,
    "commit_frequency": 1.0,
    "recent_releases_count": 0.5,
    "closed_issues_count": 0.5,
    "updated_issues_count": 0.5,
    "comment_frequency": 1.0,
    "dependents_count": 2.0,
})
OPENSSF_THRESHOLDS: Mapping[str, float] = MappingProxyType({
    "created_since": 120.0,
    "updated_since": 120.0,
    "contributor_count": 5000.0,
    "org_count": 10.0,
    "commit_frequency": 1000.0,
    "recent_releases_count": 26.0,
    "closed_issues_count": 5000.0,
    "updated_issues_count": 5000.0,
    "comment_frequency": 15.0,
    "dependents_count": 500000.0,
})

#: D-IN-5 / spec 10.3 Algorithm 3: the StarScout universe-entry star-velocity
#: filter's five thresholds (``porchlamp.starscout.audit_star_velocity``):
#: stars never enter the composite (D-IN-5), so this moves zero scored
#: `ScoreRecord` fields for any repo. It is hashed because
#: ``CONSTANTS_HASH``'s contract is "every constant the v2 core computes with
#: lives in this one module" (``docs/v2/rfcs/README.md``).
STARS_SCOUT_WINDOW_DAYS: int = 90
#: Below this many total platform events, a stargazer is a throwaway account.
STARS_SCOUT_MIN_EVENTS: int = 5
#: Above this star-to-fork ratio (with the burst count also over threshold),
#: the recent-star pattern is a lockstep burst ring.
STARS_SCOUT_FORK_RATIO: float = 100.0
#: Recent-star count that must be exceeded before the fork-ratio check runs.
STARS_SCOUT_BURST_THRESHOLD: int = 500
#: Fraction of valid stars kept when the lockstep penalty fires.
STARS_SCOUT_LOCKSTEP_PENALTY: float = 0.30


#: Per-parameter within-pillar weight, keyed by the 18 frozen input ids
#: (02 section 4). The within-pillar weight is a scoring tunable exactly
#: like ``PILLAR_WEIGHTS``, so it belongs in the hash the same way.
PARAM_WEIGHTS: Mapping[str, float] = MappingProxyType({
    "ttfhr_hours": 0.30,
    "dr90_decision_rate": 0.25,
    "mr_first_rate": 0.20,
    "gr14_ghost_rate": 0.15,
    "ttm_p90_hours": 0.10,
    "dependents_deduped": 0.40,
    "openssf_criticality": 0.35,
    "downloads_monthly": 0.25,
    "legal_ip": 0.35,
    "elephant_factor": 0.35,
    "promotion_ladder": 0.30,
    "pipeline_defenses": 0.40,
    "ai_posture": 0.30,
    "substantive_review_rate": 0.30,
    "newcomer_issue_supply": 0.35,
    "newcomer_conversion": 0.35,
    "rr90_return_rate": 0.15,
    "dx": 0.15,
})

#: Per-parameter minimum sample size below which a measured value is
#: ``insufficient_n`` (02 section 4); ``None`` where the parameter defines no
#: minimum.
PARAM_MIN_N: Mapping[str, int | None] = MappingProxyType({
    "ttfhr_hours": 10,
    "dr90_decision_rate": 10,
    "mr_first_rate": 5,
    "gr14_ghost_rate": 10,
    "ttm_p90_hours": 5,
    "dependents_deduped": None,
    "openssf_criticality": None,
    "downloads_monthly": None,
    "legal_ip": None,
    "elephant_factor": 30,
    "promotion_ladder": None,
    "pipeline_defenses": None,
    "ai_posture": None,
    "substantive_review_rate": 10,
    "newcomer_issue_supply": None,
    "newcomer_conversion": 5,
    "rr90_return_rate": 5,
    "dx": 5,
})

#: 4.1 pipeline_defenses' ``proxy_min_n`` (the minimum sample that applies
#: only when the measured method is a proxy, D-MISS-4).
PIPELINE_DEFENSES_PROXY_MIN_N: int = 10
#: 5.4 dx's ``proxy_min_n`` (RFC-0020): when fewer than ``PARAM_MIN_N["dx"]``
#: pull-request commits have a finished GitHub Actions run, 5.4 falls back to
#: the setup-signals proxy (``METHOD_PROXY_DX_SETUP_SIGNALS``) with minimum
#: CI-run sample 0.
DX_PROXY_MIN_N: int = 0

#: D-MISS-1 ``collector_failure`` threshold for row 1.2 (DR_90) and row 1.3
#: (MR_first): above this fraction of a repo's author-history cohort being
#: unmeasured (a per-author search or eligibility fetch failed -- not the
#: designed search-cap sample), the D-IN-3-adjusted rate is reported
#: Unmeasured rather than computed on a collapsed population. This is a
#: distinct gate from PARAM_MIN_N above: PARAM_MIN_N bounds the FINAL rate's
#: own sample size after every filter has run; this constant bounds the
#: COLLECTOR's own failure rate before any rate is computed at all (RFC-0001).
#: Consumed by ``porchlamp.collect.github_graphql.derive_cohort_fields``.
AUTHOR_UNMEASURED_MAX_FRACTION: float = 0.20

#: 02 section 4 (RFC-0015, #60): a closed, unmerged pull request that carries one of
#: these labels (its whole name, compared lower-cased) was landed outside GitHub's
#: merge: the project committed its change itself and closed it. The labels the
#: landing projects measured use: ``Merged`` (``pytorch/pytorch``'s merge bot and
#: Meta's export bot), ``merged`` (``freebsd/freebsd-src``'s committers) and
#: ``integrated`` (``openjdk/jdk``'s bot). A pull request whose close event names a
#: commit as its closer is landed whatever its labels; ``porchlamp.core.landing`` is
#: the one definition, and the merge-based measures (1.3, 1.5, 5.2, 5.3, the 4.1
#: review proxy) count a landed pull request as merged at its close.
LANDING_LABELS: frozenset[str] = frozenset({"integrated", "merged"})

#: D-IN-1 (02 section 3): the exact-match bot logins, compared lower-cased. An actor is
#: also a bot when GitHub types it ``Bot`` or its login ends in ``[bot]``; there is no
#: substring matching (F12). The first group are generic bot accounts. The second are
#: service accounts GitHub types as users: each login says it is a bot and was found
#: answering outside pull requests in the recorded data with templated text, or within
#: minutes of nearly every opening (RFC-0012 gives the method and each account's evidence).
#: The third are the service accounts added by RFC-0014 (#57, #66): accounts with a bot
#: login whose fast responses were on bot-authored PRs, and project-run CI, CLA, build and
#: approval accounts with no ``bot`` token in the login, each verified by profile, text
#: and commit history.
BOT_LOGINS: frozenset[str] = frozenset({
    # generic bot accounts
    "allcontributors", "cla-bot", "claude", "codecov", "copilot", "dependabot", "easycla",
    "github-actions", "google-cla", "greenkeeper", "imgbot", "mergify", "netlify",
    "pre-commit-ci", "renovate", "semantic-release", "snyk-bot", "sonarcloud", "stale",
    "vercel",
    # service accounts GitHub types as users (RFC-0012)
    "adk-bot", "agones-bot", "ansibot", "antennapod-bot", "apereocas-bot", "aseprite-bot",
    "astrobot-houston", "atuin-bot", "aws-sam-cli-bot", "babel-bot", "bitwarden-bot",
    "buggiebot", "cherry-markdown-bot", "coolbear-bot", "cosmistack-bot", "cypress-app-bot",
    "dapr-bot", "docs-bot", "drahtbot", "element-bot", "eth-bot", "expo-bot", "flinkbot",
    "frappe-pr-bot", "geky-bot", "geoserver-bot", "giteabot", "gopherbot", "gradio-pr-bot",
    "haystackbot", "highsoft-bot", "hudi-bot", "hurl-bot", "is-a-dev-bot", "jenkins-x-bot",
    "juce-push-bot", "k8s-ci-robot", "kangal-bot", "karmada-bot", "kruise-bot",
    "kubeedge-bot", "ladybird-bot", "lestarch-autobot", "lightdash-bot", "litbot-9000",
    "minikube-bot", "moz-tools-bot", "mysql-oca-bot", "nccl-bot", "nodejs-github-bot",
    "openlibrary-bot", "posit-snyk-bot", "prowler-bot", "pytorchmergebot", "qgis-bot",
    "qiskit-bot", "raycastbot", "react-native-community-bot", "robot-ci-heartex",
    "rust-rfcbot", "rustbot", "shaka-bot", "shiftbot", "sre-ci-robot", "stdlib-bot",
    "tekton-robot", "tempoxyz-bot", "theguild-bot", "ti-chi-bot", "volcano-sh-bot",
    "voxel51-bot", "workweave-bot", "ydbot", "zerxlabbot", "zulipbot",
    # service accounts GitHub types as users (RFC-0014; #57, #66)
    "autheliabot", "backstage-service", "ciliumbot", "crate-jenkins",
    "elasticsearchmachine", "hlsjs-ci", "istio-testing", "mattermost-build", "servo-bot",
    "sqla-tester", "tencent-adm", "ultralyticsassistant", "webkit-early-warning-system",
    "yoshi-approver",
})

#: D-IN-3: for rate parameters (merge, decision, conversion, return) a PR
#: author counts only if the account is at least this many days old at PR
#: creation, mirroring StarScout's throwaway rule (defined in
#: ``porchlamp.core.hygiene``).
MIN_ACCOUNT_AGE_DAYS: int = 90
#: D-IN-3: and has at least this many public events.
MIN_PUBLIC_EVENTS: int = 5
#: D-IN-3 per-author cap: one author contributes at most this fraction of
#: any rate's numerator and denominator (excess PRs from one author are
#: dropped, most recent first).
AUTHOR_CAP_FRACTION: float = 0.20

#: RFC-0028 (D-IN-3 / D-IN-4): L4 author-history cohort sampling and two-tier
#: eligibility constants (#89, Q2b / Q9 / Q11).
#: Retained confirmed newcomer-cohort size (5.2 conversion, 5.3 RR_90).
NEWCOMER_SAMPLE_SIZE: int = 20
#: Seeded newcomer-band candidates searched (after 0-cost <90d and non-User
#: pre-filtering) to retain up to :data:`NEWCOMER_SAMPLE_SIZE` confirmed true
#: newcomers without reducing 1.3 MR_first's 30-slot decision-cohort budget.
NEWCOMER_CANDIDATE_CAP: int = 35
#: Distinct outside authors in 1.2 DR_90's [anchor-180d, anchor-90d) band above
#: which deterministic author sampling (:data:`DR90_AUTHOR_SAMPLE_CAP`) applies.
DR90_AUTHOR_CAP_TRIGGER: int = 500
#: Deterministic sample size of distinct DR_90 band outside authors when a
#: repository exceeds :data:`DR90_AUTHOR_CAP_TRIGGER` (95% MoE <= +-2.6%).
DR90_AUTHOR_SAMPLE_CAP: int = 300
#: Batch width for Tier-1 D-IN-3 eligibility lookups (createdAt + earliest 5 PRs
#: and earliest 5 issues) before escalating ambiguous accounts to Tier 2.
ELIGIBILITY_TIER1_BATCH_SIZE: int = 50

#: Pillar names and frozen pillar weights (02 section 4; O-2: unchanged at
#: 25/25/20/15/15). Defined here (ahead of its alphabetical neighbours)
#: because ``PERSONA_PRESETS`` below needs ``PILLAR_WEIGHTS`` already bound.
PILLAR_NAMES: tuple[str, ...] = (
    "responsiveness",
    "criticality",
    "governance",
    "rigor",
    "accessibility",
)
PILLAR_WEIGHTS: Mapping[str, float] = MappingProxyType({
    "responsiveness": 0.25,
    "criticality": 0.25,
    "governance": 0.20,
    "rigor": 0.15,
    "accessibility": 0.15,
})

#: D-PERS-2 persona preset weight vectors (spec section 4), transcribed from
#: v1's ``porchlamp.constants.PERSONA_PRESETS`` and pinned byte-equal to it.
#: ``"balanced"`` is ``PILLAR_WEIGHTS`` itself, the one frozen definition (D-AGG-1).
PERSONA_PRESETS: Mapping[str, Mapping[str, float]] = MappingProxyType({
    "balanced": MappingProxyType(dict(PILLAR_WEIGHTS)),
    "first_timer": MappingProxyType({
        "responsiveness": 0.35,
        "criticality": 0.10,
        "governance": 0.15,
        "rigor": 0.10,
        "accessibility": 0.30,
    }),
    "career_capital": MappingProxyType({
        "responsiveness": 0.20,
        "criticality": 0.35,
        "governance": 0.15,
        "rigor": 0.10,
        "accessibility": 0.20,
    }),
    "senior_systems": MappingProxyType({
        "responsiveness": 0.15,
        "criticality": 0.35,
        "governance": 0.25,
        "rigor": 0.20,
        "accessibility": 0.05,
    }),
    "ai_assisted": MappingProxyType({
        "responsiveness": 0.20,
        "criticality": 0.20,
        "governance": 0.15,
        "rigor": 0.35,
        "accessibility": 0.10,
    }),
})

#: Tolerance for the preset weight-sum check (``porchlamp.core.persona``).
WEIGHT_SUM_TOLERANCE: float = 1e-6

#: D-PERS-2 gate thresholds.
SENIOR_SYSTEMS_MIN_ELEPHANT_FACTOR: float = 3.0
FIRST_TIMER_MIN_DECIDED_PRS: int = 5
FIRST_TIMER_MIN_MR_FIRST: float = 0.40
FIRST_TIMER_MIN_NEWCOMER_ISSUES: float = 3.0
CAREER_CAPITAL_MIN_CRITICALITY: float = 60.0
CAREER_CAPITAL_MIN_COVERAGE: float = 0.80

#: Every persona a repo can be gated from -- the ``"balanced"`` view is
#: deliberately excluded (D-PERS-1: it is the ungated default, never a gate
#: target); the string values match ``porchlamp.core.persona``'s own
#: ``PERSONA_*`` identifier constants.
GATED_PERSONAS: tuple[str, ...] = (
    "ai_assisted",
    "senior_systems",
    "first_timer",
    "career_capital",
)

#: Measured.method vocabulary: "primary" or "proxy:<name>" (04 section 3, D-MISS-4).
METHOD_PRIMARY: str = "primary"
PROXY_METHOD_PREFIX: str = "proxy:"
#: 5.4 DX setup-signals-only proxy method string (RFC-0020).
METHOD_PROXY_DX_SETUP_SIGNALS: str = PROXY_METHOD_PREFIX + "setup_signals_only"

#: The 18 parameter input ids (02 section 4, one per parameter), in pillar order.
PARAM_INPUT_IDS: tuple[str, ...] = (
    "ttfhr_hours",            # 1.1 TTFHR
    "dr90_decision_rate",     # 1.2 DR_90
    "mr_first_rate",          # 1.3 MR_first
    "gr14_ghost_rate",        # 1.4 GR_14
    "ttm_p90_hours",          # 1.5 TTM_p90
    "dependents_deduped",     # 2.1 Dependents
    "openssf_criticality",    # 2.2 OpenSSF criticality
    "downloads_monthly",      # 2.3 Downloads
    "legal_ip",               # 3.1 Legal IP
    "elephant_factor",        # 3.2 Elephant factor
    "promotion_ladder",       # 3.3 Promotion ladder
    "pipeline_defenses",      # 4.1 Pipeline defenses
    "ai_posture",             # 4.2 AI policy posture
    "substantive_review_rate",  # 4.3 Substantive review rate
    "newcomer_issue_supply",  # 5.1 Newcomer-issue supply
    "newcomer_conversion",    # 5.2 Newcomer conversion
    "rr90_return_rate",       # 5.3 RR_90
    "dx",                     # 5.4 DX
)

#: RepoIdentity field order (04 section 3).
REPO_IDENTITY_FIELDS: tuple[str, ...] = (
    "owner",
    "name",
    "ecosystem",
    "archived",
    "mirror_url",
    "fork",
    "license_spdx",
    "default_branch",
)

#: Evidence-based legal IP regimes (02 section 4, parameter 3.1).
LEGAL_IP_VALUES: tuple[str, ...] = ("DCO", "FOUNDATION_CLA", "CORP_CLA", "NONE")

#: AI policy postures (02 section 4, parameter 4.2).
AI_POSTURE_VALUES: tuple[str, ...] = ("REGULATED", "PROSCRIBED", "AD_HOC")

#: Promotion ladder rungs (02 section 4, parameter 3.3).
PROMOTION_LADDER_VALUES: tuple[str, ...] = ("VERIFIED", "DOCUMENTED", "AD_HOC", "CLOSED")


def _canonical_json(value: object) -> str:
    """Canonical JSON (sorted keys, no insignificant whitespace), stdlib-only."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _unwrap_mapping(v: object) -> object:
    if isinstance(v, (MappingProxyType, dict)):
        return {k: _unwrap_mapping(sub_v) for k, sub_v in v.items()}
    return v


def constants_dict() -> dict[str, object]:
    """Every v2 constant in this module, keyed by name.

    The single source for the hash: a value is a constant if and only if it
    appears here. ``CONSTANTS_HASH`` itself is excluded (it is a hash of the
    dict, not a member of it).
    """
    raw = {
        "AI_POSTURE_SCORES": AI_POSTURE_SCORES,
        "AI_POSTURE_VALUES": AI_POSTURE_VALUES,
        "AUTHOR_CAP_FRACTION": AUTHOR_CAP_FRACTION,
        "AUTHOR_UNMEASURED_MAX_FRACTION": AUTHOR_UNMEASURED_MAX_FRACTION,
        "AUTOMATION_FAST_SECONDS": AUTOMATION_FAST_SECONDS,
        "AUTOMATION_FAST_SHARE": AUTOMATION_FAST_SHARE,
        "AUTOMATION_MIN_PRS": AUTOMATION_MIN_PRS,
        "AUTOMATION_TEMPLATE_WORDS": AUTOMATION_TEMPLATE_WORDS,
        "BENCHMARK_VERSION": BENCHMARK_VERSION,
        "BOT_LOGINS": tuple(sorted(BOT_LOGINS)),
        "CAREER_CAPITAL_MIN_COVERAGE": CAREER_CAPITAL_MIN_COVERAGE,
        "CAREER_CAPITAL_MIN_CRITICALITY": CAREER_CAPITAL_MIN_CRITICALITY,
        "CEILING_A_MIN_COVERAGE": CEILING_A_MIN_COVERAGE,
        "COHORT_RATE_SLOPE": COHORT_RATE_SLOPE,
        "COMMIT_CAP": COMMIT_CAP,
        "COMPOSITE_DECIMALS": COMPOSITE_DECIMALS,
        "DR90_AUTHOR_CAP_TRIGGER": DR90_AUTHOR_CAP_TRIGGER,
        "DR90_AUTHOR_SAMPLE_CAP": DR90_AUTHOR_SAMPLE_CAP,
        "DX_PROXY_MIN_N": DX_PROXY_MIN_N,
        "ELEPHANT_SLOPE": ELEPHANT_SLOPE,
        "ELIGIBILITY_TIER1_BATCH_SIZE": ELIGIBILITY_TIER1_BATCH_SIZE,
        "EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE": EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE,
        "EVIDENCE_GATE_REQUIRED_PILLARS": EVIDENCE_GATE_REQUIRED_PILLARS,
        "FIRST_TIMER_MIN_DECIDED_PRS": FIRST_TIMER_MIN_DECIDED_PRS,
        "FIRST_TIMER_MIN_MR_FIRST": FIRST_TIMER_MIN_MR_FIRST,
        "FIRST_TIMER_MIN_NEWCOMER_ISSUES": FIRST_TIMER_MIN_NEWCOMER_ISSUES,
        "FOUNDATION_ORGS": tuple(sorted(FOUNDATION_ORGS)),
        "FRICTION_WEIGHTS": FRICTION_WEIGHTS,
        "GATE4_VITALITY_WINDOW_DAYS": GATE4_VITALITY_WINDOW_DAYS,
        "GATE5_MAX_ZERO_RESPONSE_SHARE": GATE5_MAX_ZERO_RESPONSE_SHARE,
        "GATE5_MIN_AGE_DAYS": GATE5_MIN_AGE_DAYS,
        "GATE5_MIN_N": GATE5_MIN_N,
        "GATE5_RESPONSE_DEADLINE_DAYS": GATE5_RESPONSE_DEADLINE_DAYS,
        "GATE5_WILSON_Z": GATE5_WILSON_Z,
        "GATE6_DECISION_WINDOW_DAYS": GATE6_DECISION_WINDOW_DAYS,
        "GATE6_HANDOFF_MIN_PRS": GATE6_HANDOFF_MIN_PRS,
        "GATE6_HANDOFF_MIN_SHARE": GATE6_HANDOFF_MIN_SHARE,
        "GATE6_HANDOFF_NOTICES": GATE6_HANDOFF_NOTICES,
        "GATE6_HANDOFF_RELAY_LOGIN_PREFIXES": GATE6_HANDOFF_RELAY_LOGIN_PREFIXES,
        "GATE6_MIN_OUTSIDE_PRS": GATE6_MIN_OUTSIDE_PRS,
        "GATED_PERSONAS": GATED_PERSONAS,
        "GHOST_RATE_SLOPE": GHOST_RATE_SLOPE,
        "LANDING_LABELS": tuple(sorted(LANDING_LABELS)),
        "LEGACY_COMMIT_CAP": LEGACY_COMMIT_CAP,
        "LEGAL_IP_SCORES": LEGAL_IP_SCORES,
        "LEGAL_IP_VALUES": LEGAL_IP_VALUES,
        "LEVERAGE_WEIGHTS": LEVERAGE_WEIGHTS,
        "METHOD_PRIMARY": METHOD_PRIMARY,
        "METHOD_PROXY_DX_SETUP_SIGNALS": METHOD_PROXY_DX_SETUP_SIGNALS,
        "MIN_ACCOUNT_AGE_DAYS": MIN_ACCOUNT_AGE_DAYS,
        "MIN_POPULATION": MIN_POPULATION,
        "MIN_PUBLIC_EVENTS": MIN_PUBLIC_EVENTS,
        "MONO_TOLERANCE": MONO_TOLERANCE,
        "NEWCOMER_CANDIDATE_CAP": NEWCOMER_CANDIDATE_CAP,
        "NEWCOMER_SAMPLE_SIZE": NEWCOMER_SAMPLE_SIZE,
        "NOT_APPLICABLE_RULES": NOT_APPLICABLE_RULES,
        "NO_CEILING_MIN_COVERAGE": NO_CEILING_MIN_COVERAGE,
        "OPENSSF_PROXY_MIN_SIGNALS": OPENSSF_PROXY_MIN_SIGNALS,
        "OPENSSF_THRESHOLDS": OPENSSF_THRESHOLDS,
        "OPENSSF_WEIGHTS": OPENSSF_WEIGHTS,
        "OSI_APPROVED_LICENSES": tuple(sorted(OSI_APPROVED_LICENSES)),
        "PARAM_INPUT_IDS": PARAM_INPUT_IDS,
        "PARAM_MIN_N": PARAM_MIN_N,
        "PARAM_WEIGHTS": PARAM_WEIGHTS,
        "PERSONA_PRESETS": PERSONA_PRESETS,
        "PILLAR_NAMES": PILLAR_NAMES,
        "PILLAR_WEIGHTS": PILLAR_WEIGHTS,
        "PIPELINE_DEFENSES_PROXY_MIN_N": PIPELINE_DEFENSES_PROXY_MIN_N,
        "PROMOTION_LADDER_SCORES": PROMOTION_LADDER_SCORES,
        "PROMOTION_LADDER_VALUES": PROMOTION_LADDER_VALUES,
        "PROMOTION_MIN_OUTSIDE_DAYS": PROMOTION_MIN_OUTSIDE_DAYS,
        "PROXY_METHOD_PREFIX": PROXY_METHOD_PREFIX,
        "PR_CAP": PR_CAP,
        "PR_REPLY_NODE_CAP": PR_REPLY_NODE_CAP,
        "PR_SAMPLE_CAP": PR_SAMPLE_CAP,
        "PR_SAMPLE_SEED": PR_SAMPLE_SEED,
        "PR_WINDOW_DAYS": PR_WINDOW_DAYS,
        "QUADRANT_THRESHOLD": QUADRANT_THRESHOLD,
        "RATE_SLOPE": RATE_SLOPE,
        "REASON_NO_POPULATION": REASON_NO_POPULATION,
        "REPO_IDENTITY_FIELDS": REPO_IDENTITY_FIELDS,
        "SCHEMA_VERSION": SCHEMA_VERSION,
        "SENIOR_SYSTEMS_MIN_ELEPHANT_FACTOR": SENIOR_SYSTEMS_MIN_ELEPHANT_FACTOR,
        "SIGNAL_FIELDS": SIGNAL_FIELDS,
        "STARS_SCOUT_BURST_THRESHOLD": STARS_SCOUT_BURST_THRESHOLD,
        "STARS_SCOUT_FORK_RATIO": STARS_SCOUT_FORK_RATIO,
        "STARS_SCOUT_LOCKSTEP_PENALTY": STARS_SCOUT_LOCKSTEP_PENALTY,
        "STARS_SCOUT_MIN_EVENTS": STARS_SCOUT_MIN_EVENTS,
        "STARS_SCOUT_WINDOW_DAYS": STARS_SCOUT_WINDOW_DAYS,
        "SUBSTANTIVE_COMMENT_CJK_WEIGHT": SUBSTANTIVE_COMMENT_CJK_WEIGHT,
        "SUBSTANTIVE_COMMENT_MIN_CHARS": SUBSTANTIVE_COMMENT_MIN_CHARS,
        "SUBSTANTIVE_REVIEW_BODY_CHARS": SUBSTANTIVE_REVIEW_BODY_CHARS,
        "SUBSTANTIVE_REVIEW_BOILERPLATE_MIN_PRS": SUBSTANTIVE_REVIEW_BOILERPLATE_MIN_PRS,
        "SUPPLY_SLOPE": SUPPLY_SLOPE,
        "TIER_CUTOFFS": TIER_CUTOFFS,
        "TIER_ORDER": TIER_ORDER,
        "TTFHR_KNEE_HOURS": TTFHR_KNEE_HOURS,
        "TTFHR_SLOPE_PER_DOUBLING": TTFHR_SLOPE_PER_DOUBLING,
        "UNMEASURED_REASONS": UNMEASURED_REASONS,
        "WEIGHT_SUM_TOLERANCE": WEIGHT_SUM_TOLERANCE,
        "WINDOW_STALENESS_DAYS": WINDOW_STALENESS_DAYS,
    }
    return {k: _unwrap_mapping(v) for k, v in raw.items()}


#: Module-level UPPER_CASE names that are deliberately NOT part of
#: Module-level UPPER_CASE names that are deliberately NOT part of
#: ``constants_dict()``.
#: ``CONSTANTS_HASH`` is the only member: it is a hash *of* the dict, so it
#: cannot also be *in* the dict (self-reference). Every other module-level
#: UPPER_CASE name -- ``BENCHMARK_VERSION`` included -- is a real key of
#: ``constants_dict()``; ``BENCHMARK_VERSION`` is deliberately hashed
#: because D-VER-1 ties a version bump to a constants change in the first
#: place, so the two moving together is the correct behaviour.
#: ``tests/core/test_frozen.py::test_every_module_level_constant_is_hashed_or_declared_not_hashed``
#: enforces that this tuple plus ``constants_dict()`` between them account
#: for every module-level UPPER_CASE name.
_NOT_HASHED: tuple[str, ...] = ("CONSTANTS_HASH",)

CONSTANTS_HASH: str = hashlib.sha256(
    _canonical_json(constants_dict()).encode("utf-8")
).hexdigest()
