"""Frozen CVI v2 constants (D-VER-2, R-2).

One definition per fact: every constant the v2 core contract names lives in
this module. ``CONSTANTS_HASH`` is ``sha256(canonical JSON of the full
constants dict)``; it is stamped into every stored ScoreRecord row and the
snapshot manifest so a reader can verify the constants a score was built
from. Changing any constant changes the hash (tests/core/test_constants_hash.py).

The canonical JSON below is the JSON-native subset of
``cvi.core.models.canonical_json`` (byte-identical on JSON-native values);
``cvi.core`` may not import ``cvi.collect`` (04 section 3 import rule), so the
expression is kept local here instead of shared across the boundary.
"""
from __future__ import annotations

import hashlib
import json

#: Benchmark version for the v2 line (D-VER-1). Frozen at "2.0.0-rc1" by
#: packet W2-8 (lead ruling 2026-09-12): "2.0.0" itself is flipped by W5-2 at
#: release; until then constants may still change, but only through the RFC
#: procedure in docs/v2/rfcs/README.md.
BENCHMARK_VERSION: str = "2.0.0-rc1"

#: FeatureBundle document version (04 section 3: data/derived/<key>.json).
SCHEMA_VERSION: int = 2

#: Length of the PR window every PR-based parameter measures over (04 section 3).
PR_WINDOW_DAYS: int = 180

#: D-MISS-1 reason for a Measured ECDF-kind input whose (ecosystem, param)
#: has no frozen population at all (``cvi.core.engine``, added 2026-09-12).
REASON_NO_POPULATION: str = "no_population"

#: Unmeasured.reason vocabulary (D-MISS-1). ``REASON_NO_POPULATION`` was
#: added 2026-09-12 (an ECDF-normalised parameter with no rated, measured
#: repository to build a population from, in its ecosystem or globally, at
#: this snapshot); before this packet the value was accepted by
#: ``Unmeasured.reason``'s type annotation but rejected by this tuple's own
#: runtime check (``cvi.core.models.unmeasured_problems``), so
#: ``Unmeasured("no_population")`` raised ``BundleValidationError`` --
#: ``tests/core/test_frozen.py::test_no_population_is_a_constructible_reason``
#: is the fail-before/after pin for this fix.
UNMEASURED_REASONS: tuple[str, ...] = (
    "third_party_gap",
    "collector_failure",
    "insufficient_n",
    "not_applicable",
    REASON_NO_POPULATION,
)

#: Number of decimal places the composite score is rounded to everywhere it
#: is compared or displayed (D-AGG-2, 04 section 8): ``cvi.core.aggregate``'s
#: final rounding and every ``round(..., 2)`` site in ``cvi.core.tiebreak``'s
#: D-TIE-1 composite level and D-TIE-2 tie-group boundary. Relocated by
#: packet W2-8 (R-2): three call sites duplicated the literal ``2`` before
#: this constant existed.
COMPOSITE_DECIMALS: int = 2

#: D-NORM-2: minimum population per ecosystem; below it the population falls
#: back to the global population and carries ``population_fallback``.
#: Relocated from ``cvi.snapshot.populations`` by packet W2-8 (R-2).
MIN_POPULATION: int = 30

#: D-MISS-3 ceiling bands, above the D-EVID-1 rated threshold. Relocated
#: from ``cvi.core.aggregate`` by packet W2-8 (R-2).
CEILING_A_MIN_COVERAGE: float = 0.80
NO_CEILING_MIN_COVERAGE: float = 0.95

#: D-EVID-1: composite coverage threshold (D-MISS-3) below which a repo is
#: unrated. Relocated from ``cvi.core.gates`` by packet W2-8 (R-2).
EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE: float = 0.60

#: D-GATE-4: trailing window for non-bot User commits on the default branch.
#: Relocated from ``cvi.core.gates`` by packet W2-8 (R-2).
GATE4_VITALITY_WINDOW_DAYS: int = 180
#: D-GATE-5: minimum population of external PRs >= 14 d old, else UNDECIDED.
GATE5_MIN_N: int = 10
#: D-GATE-5: a PR is ">= 14 d old" at the snapshot reference time.
GATE5_MIN_AGE_DAYS: int = 14
#: D-GATE-5: the substantive-response deadline after opening.
GATE5_RESPONSE_DEADLINE_DAYS: int = 14
#: D-GATE-5: pass ceiling on the share with zero substantive response.
GATE5_MAX_ZERO_RESPONSE_SHARE: float = 0.50
#: D-GATE-6: horizon for a decided external PR.
GATE6_DECISION_WINDOW_DAYS: int = 365

#: D-MAT-1 leverage/friction axis weights and the quadrant boundary.
#: Relocated from ``cvi.core.matrix`` by packet W2-8 (R-2).
LEVERAGE_WEIGHTS: dict[str, float] = {
    "criticality": 0.50,
    "rigor": 0.30,
    "governance": 0.20,
}
FRICTION_WEIGHTS: dict[str, float] = {
    "responsiveness": 0.50,
    "governance": 0.30,
    "accessibility": 0.20,
}
QUADRANT_THRESHOLD: float = 50.0

#: D-MAT-2 tier cutoffs (kept, O-5), worst to best; below the last cutoff is
#: the floor tier ``"D"`` (``cvi.core.aggregate.TIER_FLOOR`` -- a bare label
#: with no accompanying threshold, correctly excluded from the hash per the
#: W2-8 round-1 review's own census). A score exactly at a cutoff belongs to
#: the higher tier (``>=``), matching the v1 convention these cutoffs are
#: kept from. Relocated from ``cvi.core.aggregate`` by packet W2-8 round 2
#: (R-2): a cutoff or an ordering change moves ``ScoreRecord.tier`` for
#: rated repos exactly like a weight change does, so it belongs in the hash
#: the same way (round-1 review Blocker 1: an A-cutoff 70.0 -> 60.0 re-tiered
#: 7 of 89 rated repos on the sample with the hash unchanged).
TIER_CUTOFFS: tuple[tuple[float, str], ...] = (
    (90.0, "S+"),
    (80.0, "S"),
    (70.0, "A"),
    (55.0, "B"),
    (40.0, "C"),
)

#: Every tier and ceiling label, worst to best, for ``apply_ceiling``'s
#: ``min(tier_by_score, ceiling)`` (D-MAT-2). Relocated from
#: ``cvi.core.aggregate`` by packet W2-8 round 2 (R-2): reordering this
#: tuple changes ``apply_ceiling``'s comparison outcome (round-1 review
#: Blocker 1's reproduction: reordering it turns ``apply_ceiling("B","A")``
#: from ``"B"`` into ``"A"``).
TIER_ORDER: tuple[str, ...] = ("D", "C", "B", "A", "S", "S+")


#: 02 section 4 curve-shape parameters. Relocated from ``cvi.core.params.*``
#: by packet W2-8 (R-2).
#: Float tolerance for the D-PARAM-MONO check (``cvi.core.params.curves``).
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
LEGAL_IP_SCORES: dict[str, float] = {
    "DCO": 100.0,
    "NONE": 85.0,
    "FOUNDATION_CLA": 75.0,
    "CORP_CLA": 25.0,
}
#: 3.3 promotion-ladder rung scores.
PROMOTION_LADDER_SCORES: dict[str, float] = {
    "VERIFIED": 100.0,
    "DOCUMENTED": 75.0,
    "AD_HOC": 40.0,
    "CLOSED": 10.0,
}
#: 4.2 AI posture scores, persona-conditional.
AI_POSTURE_SCORES: dict[str, dict[str, float]] = {
    "REGULATED": {"human": 95.0, "ai": 95.0},
    "PROSCRIBED": {"human": 85.0, "ai": 25.0},
    "AD_HOC": {"human": 40.0, "ai": 40.0},
}

#: D-GATE-3 OSI-approved SPDX ids (the union of the SPDX license-list-data
#: ``isOsiApproved`` flag and the opensource.org approved-licences table,
#: fetched 2026-09-12 -- see ``cvi.core.gates``'s original module docstring
#: for the exact sources). Relocated from ``cvi.core.gates`` by packet W2-8
#: round 2 (R-2), per the round-1 review's Warning 5: this is not a bare
#: identifier vocabulary like ``GATE_1..6`` -- D-GATE-3 *interprets* it as a
#: predicate (membership decides eligible vs ``REASON_UNLICENSED``), so
#: removing one id silently regates every repo under that license
#: ``unlicensed`` with the hash unchanged (reproduced: removing ``"MIT"``
#: leaves ``CONSTANTS_HASH`` untouched). An upstream SPDX/OSI list refresh
#: is therefore itself an RFC-worthy event (``docs/v2/rfcs/README.md``'s new
#: "upstream vocabulary refresh" class), not a silent data update.
#: Kept as a ``frozenset`` here for O(1) gate-time membership; hashed as
#: ``tuple(sorted(OSI_APPROVED_LICENSES))`` in ``constants_dict()`` (a
#: ``frozenset`` is not JSON-native -- ``_canonical_json`` operates on the
#: JSON-native subset of ``cvi.core.models.canonical_json``, per this
#: module's own docstring).
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

#: OpenSSF/Pike criticality (input 2.2, ``cvi.core.openssf``): the
#: ``cvi.models.OpenSSFSignals`` field name -> the alpha_i/T_i key (spec
#: 2.2, 10.1). Relocated from ``cvi.core.openssf`` by packet W2-8 round 2
#: (R-2), per the round-1 review's Blocker 2 and that module's own
#: docstring ("W2-8's constants-freeze work should decide whether/how they
#: join that registry"): a weight, threshold or field-mapping change moves
#: input 2.2's score exactly like any other curve parameter (reproduced:
#: ``OPENSSF_WEIGHTS["contributor_count"]`` 2.0 -> 3.0 moved the Pike score
#: 62.107555 -> 62.533452 with the hash unchanged).
SIGNAL_FIELDS: dict[str, str] = {
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
}

#: Default signal weights (alpha_i) and thresholds (T_i) -- spec 2.2, 10.1.
OPENSSF_WEIGHTS: dict[str, float] = {
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
}
OPENSSF_THRESHOLDS: dict[str, float] = {
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
}


#: Per-parameter within-pillar weight, keyed by the 18 frozen input ids
#: (02 section 4). Relocated from the ``ParamDef`` call sites in
#: ``cvi.core.params.*`` by packet W2-8 (R-2): the within-pillar weight is a
#: scoring tunable exactly like ``PILLAR_WEIGHTS``, so it belongs in the
#: hash the same way.
PARAM_WEIGHTS: dict[str, float] = {
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
}

#: Per-parameter minimum sample size below which a measured value is
#: ``insufficient_n`` (02 section 4); ``None`` where the parameter defines no
#: minimum. Relocated from the ``ParamDef`` call sites in
#: ``cvi.core.params.*`` by packet W2-8 (R-2).
PARAM_MIN_N: dict[str, int | None] = {
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
}

#: 4.1 pipeline_defenses' ``proxy_min_n`` (the minimum sample that applies
#: only when the measured method is a proxy, D-MISS-4); the only parameter
#: that defines one.
PIPELINE_DEFENSES_PROXY_MIN_N: int = 10

#: D-MISS-1 ``collector_failure`` threshold for row 1.2 (DR_90) and row 1.3
#: (MR_first): above this fraction of a repo's author-history cohort being
#: unmeasured (a per-author search or eligibility fetch failed -- not the
#: designed search-cap sample), the D-IN-3-adjusted rate is reported
#: Unmeasured rather than computed on a collapsed population. This is a
#: distinct gate from PARAM_MIN_N above: PARAM_MIN_N bounds the FINAL rate's
#: own sample size after every filter has run; this constant bounds the
#: COLLECTOR's own failure rate before any rate is computed at all (RFC-0001;
#: motivated by the W1-4 round-2 review's B-2/B-3: a batch-fetch defect
#: silently collapsed pytest-dev/pytest's MR_first denominator from 55 to 9
#: authors with no status change -- 45 of 62 = 73% unmeasured, well above
#: this threshold). Consumed by ``cvi.collect.github_graphql.derive_cohort_
#: fields``.
AUTHOR_UNMEASURED_MAX_FRACTION: float = 0.20

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
PILLAR_WEIGHTS: dict[str, float] = {
    "responsiveness": 0.25,
    "criticality": 0.25,
    "governance": 0.20,
    "rigor": 0.15,
    "accessibility": 0.15,
}

#: D-PERS-2 persona preset weight vectors (spec section 4), transcribed from
#: v1's ``cvi.constants.PERSONA_PRESETS`` and pinned byte-equal to it
#: (``tests/core/test_persona_v2.py::test_non_balanced_presets_match_v1``).
#: ``"balanced"`` is not transcribed -- it is ``PILLAR_WEIGHTS`` itself, the
#: one frozen definition (D-AGG-1). Relocated from ``cvi.core.persona`` by
#: packet W2-8 (R-2): this dict is what ``CONSTANTS_HASH`` now covers.
PERSONA_PRESETS: dict[str, dict[str, float]] = {
    "balanced": dict(PILLAR_WEIGHTS),
    "first_timer": {
        "responsiveness": 0.35,
        "criticality": 0.10,
        "governance": 0.15,
        "rigor": 0.10,
        "accessibility": 0.30,
    },
    "career_capital": {
        "responsiveness": 0.20,
        "criticality": 0.35,
        "governance": 0.15,
        "rigor": 0.10,
        "accessibility": 0.20,
    },
    "senior_systems": {
        "responsiveness": 0.15,
        "criticality": 0.35,
        "governance": 0.25,
        "rigor": 0.20,
        "accessibility": 0.05,
    },
    "ai_assisted": {
        "responsiveness": 0.20,
        "criticality": 0.20,
        "governance": 0.15,
        "rigor": 0.35,
        "accessibility": 0.10,
    },
}

#: Tolerance for the preset weight-sum check (``cvi.core.persona``).
WEIGHT_SUM_TOLERANCE: float = 1e-6

#: D-PERS-2 gate thresholds. Relocated from ``cvi.core.persona`` by packet
#: W2-8 (R-2).
SENIOR_SYSTEMS_MIN_ELEPHANT_FACTOR: float = 3.0
FIRST_TIMER_MIN_DECIDED_PRS: int = 5
FIRST_TIMER_MIN_MR_FIRST: float = 0.40
FIRST_TIMER_MIN_NEWCOMER_ISSUES: float = 3.0
CAREER_CAPITAL_MIN_CRITICALITY: float = 60.0
CAREER_CAPITAL_MIN_COVERAGE: float = 0.80

#: Every persona a repo can be gated from -- the ``"balanced"`` view is
#: deliberately excluded (D-PERS-1: it is the ungated default, never a gate
#: target). Relocated from ``cvi.core.persona`` by packet W2-8 (R-2); the
#: string values match that module's own ``PERSONA_*`` identifier constants
#: (defined there, not here, per the module docstring's one-definition note
#: on identifiers -- this tuple is the one *tunable* fact, the set of gated
#: personas, not the identifiers themselves).
GATED_PERSONAS: tuple[str, ...] = (
    "ai_assisted",
    "senior_systems",
    "first_timer",
    "career_capital",
)

#: Measured.method vocabulary: "primary" or "proxy:<name>" (04 section 3, D-MISS-4).
METHOD_PRIMARY: str = "primary"
PROXY_METHOD_PREFIX: str = "proxy:"

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


def constants_dict() -> dict[str, object]:
    """Every v2 constant in this module, keyed by name.

    The single source for the hash: a value is a constant if and only if it
    appears here. ``CONSTANTS_HASH`` itself is excluded (it is a hash of the
    dict, not a member of it).
    """
    return {
        "AI_POSTURE_SCORES": AI_POSTURE_SCORES,
        "AI_POSTURE_VALUES": AI_POSTURE_VALUES,
        "AUTHOR_UNMEASURED_MAX_FRACTION": AUTHOR_UNMEASURED_MAX_FRACTION,
        "BENCHMARK_VERSION": BENCHMARK_VERSION,
        "CAREER_CAPITAL_MIN_COVERAGE": CAREER_CAPITAL_MIN_COVERAGE,
        "CAREER_CAPITAL_MIN_CRITICALITY": CAREER_CAPITAL_MIN_CRITICALITY,
        "CEILING_A_MIN_COVERAGE": CEILING_A_MIN_COVERAGE,
        "COHORT_RATE_SLOPE": COHORT_RATE_SLOPE,
        "COMPOSITE_DECIMALS": COMPOSITE_DECIMALS,
        "ELEPHANT_SLOPE": ELEPHANT_SLOPE,
        "EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE": EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE,
        "FIRST_TIMER_MIN_DECIDED_PRS": FIRST_TIMER_MIN_DECIDED_PRS,
        "FIRST_TIMER_MIN_MR_FIRST": FIRST_TIMER_MIN_MR_FIRST,
        "FIRST_TIMER_MIN_NEWCOMER_ISSUES": FIRST_TIMER_MIN_NEWCOMER_ISSUES,
        "FRICTION_WEIGHTS": FRICTION_WEIGHTS,
        "GATE4_VITALITY_WINDOW_DAYS": GATE4_VITALITY_WINDOW_DAYS,
        "GATE5_MAX_ZERO_RESPONSE_SHARE": GATE5_MAX_ZERO_RESPONSE_SHARE,
        "GATE5_MIN_AGE_DAYS": GATE5_MIN_AGE_DAYS,
        "GATE5_MIN_N": GATE5_MIN_N,
        "GATE5_RESPONSE_DEADLINE_DAYS": GATE5_RESPONSE_DEADLINE_DAYS,
        "GATE6_DECISION_WINDOW_DAYS": GATE6_DECISION_WINDOW_DAYS,
        "GATED_PERSONAS": GATED_PERSONAS,
        "GHOST_RATE_SLOPE": GHOST_RATE_SLOPE,
        "LEGAL_IP_SCORES": LEGAL_IP_SCORES,
        "LEGAL_IP_VALUES": LEGAL_IP_VALUES,
        "LEVERAGE_WEIGHTS": LEVERAGE_WEIGHTS,
        "METHOD_PRIMARY": METHOD_PRIMARY,
        "MIN_POPULATION": MIN_POPULATION,
        "MONO_TOLERANCE": MONO_TOLERANCE,
        "NO_CEILING_MIN_COVERAGE": NO_CEILING_MIN_COVERAGE,
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
        "PROXY_METHOD_PREFIX": PROXY_METHOD_PREFIX,
        "PR_WINDOW_DAYS": PR_WINDOW_DAYS,
        "QUADRANT_THRESHOLD": QUADRANT_THRESHOLD,
        "RATE_SLOPE": RATE_SLOPE,
        "REASON_NO_POPULATION": REASON_NO_POPULATION,
        "REPO_IDENTITY_FIELDS": REPO_IDENTITY_FIELDS,
        "SCHEMA_VERSION": SCHEMA_VERSION,
        "SENIOR_SYSTEMS_MIN_ELEPHANT_FACTOR": SENIOR_SYSTEMS_MIN_ELEPHANT_FACTOR,
        "SIGNAL_FIELDS": SIGNAL_FIELDS,
        "SUPPLY_SLOPE": SUPPLY_SLOPE,
        "TIER_CUTOFFS": TIER_CUTOFFS,
        "TIER_ORDER": TIER_ORDER,
        "TTFHR_KNEE_HOURS": TTFHR_KNEE_HOURS,
        "TTFHR_SLOPE_PER_DOUBLING": TTFHR_SLOPE_PER_DOUBLING,
        "UNMEASURED_REASONS": UNMEASURED_REASONS,
        "WEIGHT_SUM_TOLERANCE": WEIGHT_SUM_TOLERANCE,
    }


#: Module-level UPPER_CASE names that are deliberately NOT part of
#: ``constants_dict()`` (packet W2-8 round 2, review Warning 6 / mutant M4:
#: a new constant added here but never registered in ``constants_dict()``
#: survived the full suite, since nothing checked completeness).
#: ``CONSTANTS_HASH`` is the only member: it is a hash *of* the dict, so it
#: cannot also be *in* the dict (self-reference). Every other module-level
#: UPPER_CASE name -- ``BENCHMARK_VERSION`` included -- is a real key of
#: ``constants_dict()``; ``BENCHMARK_VERSION`` is deliberately hashed
#: (not carved out here) because D-VER-1 ties a version bump to a
#: constants change in the first place, so the two moving together is the
#: correct behaviour, not a redundancy to break apart.
#: ``tests/core/test_frozen.py::test_every_module_level_constant_is_hashed_or_declared_not_hashed``
#: enforces that this tuple plus ``constants_dict()`` between them account
#: for every module-level UPPER_CASE name -- the M4 guard.
_NOT_HASHED: tuple[str, ...] = ("CONSTANTS_HASH",)

CONSTANTS_HASH: str = hashlib.sha256(
    _canonical_json(constants_dict()).encode("utf-8")
).hexdigest()
