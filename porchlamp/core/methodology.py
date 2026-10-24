"""Porchlamp v2 published methodology document (`06` section 3 F).

:func:`methodology_document` builds the JSON-serialisable document the
Methodology screen (`06` section 3 F), the API (``GET /v1/porchlamp/methodology``)
and ``scripts/render_methodology.py`` all render from -- "rendered
from the frozen constants module so it cannot drift from the code" (06
section 3 F, verbatim). Every field is read directly from
:mod:`porchlamp.core.constants` and the :mod:`porchlamp.core.params` registry at
call time; nothing here is a hand-typed number, and nothing is cached at
import time, so mutating a constant and calling this function again always
reflects the mutation (``tests/core/test_methodology.py``'s
``methodology_hash`` iff-test depends on that).

**Import boundary (`tests/core/test_import_graph.py`).** Every file
directly under ``porchlamp/core/`` may import only ``porchlamp.core`` plus the standard
library. Three facts the design brief asks the document to
carry live outside that boundary, so they are deliberately NOT part of
:func:`methodology_document`:

* the nine cross-snapshot detectors (name/threshold/direction) live in
  :mod:`porchlamp.verify.detectors` -- not ``porchlamp.core`` (the same boundary
  ``porchlamp.core.pipeline``'s ``DetectorsCallable`` seam documents for the
  unrelated ``ScoreRecord.suspicion`` wiring problem);
* the three audit-only release bars (``MIN_COVERAGE``,
  ``TAU_RELEASE_THRESHOLD``, ``DEFAULT_PERTURBATION_PP``) live in
  :mod:`porchlamp.audit.anchors` / :mod:`porchlamp.audit.sensitivity` -- process
  constants ruled out of ``constants.constants_dict()`` on purpose
  (`docs/v2/rfcs/README.md` "Process constants") and therefore just as far
  outside ``porchlamp.core`` as the detectors are;
* the RFC changelog (``rfc_log``) needs a file read, which is I/O and does
  not belong in a pure ``porchlamp.core`` module either.

:func:`porchlamp.audit.methodology_history.methodology_with_history` is the one
caller allowed to cross that boundary: it merges this pure document with
those three additions. :class:`MethodologyDocument` is defined here as
a ``TypedDict`` to maintain strict separation without depending on pydantic
in the core package.
"""

from __future__ import annotations

from typing import TypedDict

from porchlamp.core import constants
from porchlamp.core import gates as _gates
from porchlamp.core.models import canonical_json, sha256_hex
from porchlamp.core.params import PARAMS
from porchlamp.core.params.curves import KIND_ECDF, KIND_ENUM, KIND_NUMERIC, ParamDef

# --------------------------------------------------------------------------- #
# TypedDict contract (04/06: MethodologyDocument, pending porchlamp/serve/contract.py) #
# --------------------------------------------------------------------------- #


class PillarEntry(TypedDict):
    name: str
    weight: float


class ProxyInfo(TypedDict, total=False):
    proxy_min_n: int | None
    proxy_min_signals: int


class ParameterEntry(TypedDict, total=False):
    input_id: str
    pillar: str
    weight_within_pillar: float
    min_n: int | None
    window_days: int | None
    curve: str
    value_domain: dict[str, object]
    proxy: ProxyInfo


class GateEntry(TypedDict, total=False):
    id: str
    name: str
    criterion: str
    reasons: tuple[str, ...]
    cutoffs: dict[str, object]
    license_vocabulary: dict[str, object]


class MissingnessReason(TypedDict):
    reason: str
    semantics: str


class NotApplicableRule(TypedDict):
    parameter: str
    bundle_fact: str
    value: str


class MissingnessSection(TypedDict):
    reasons: list[MissingnessReason]
    coverage_thresholds: dict[str, float]
    not_applicable_rules: list[NotApplicableRule]
    openssf_proxy_min_signals: int


class TieBreakLevel(TypedDict):
    field: str
    direction: str
    note: str | None


class TieBreakSection(TypedDict):
    composite_decimals: int
    levels: list[TieBreakLevel]


class PersonaGateEntry(TypedDict):
    persona: str
    description: str
    thresholds: dict[str, object]


class PersonasSection(TypedDict):
    presets: dict[str, dict[str, float]]
    gates: list[PersonaGateEntry]


class DetectorEntry(TypedDict, total=False):
    """One row of the extended document's ``detectors`` list (populated only
    by :func:`porchlamp.audit.methodology_history.methodology_with_history`,
    never by :func:`methodology_document` -- see the module docstring)."""

    name: str
    direction: str
    trigger: str
    thresholds: dict[str, object]


class ProcessConstantEntry(TypedDict):
    """One row of the extended document's ``not_hashed_process_constants``
    list (populated only by ``methodology_with_history``)."""

    name: str
    module: str
    value: object
    gates: str


class RFCLogEntry(TypedDict):
    """One row of the extended document's ``rfc_log`` list (populated only
    by ``methodology_with_history``, parsed from ``CHANGELOG.md``)."""

    date: str
    rfc: str
    summary: str
    hash_before: str | None
    hash_after: str | None


class MethodologyDocument(TypedDict, total=False):
    """The full v2 methodology document. ``benchmark_version`` through
    ``personas`` are always present (built by :func:`methodology_document`);
    ``detectors``, ``not_hashed_process_constants`` and ``rfc_log`` are
    present only on the extended document
    :func:`porchlamp.audit.methodology_history.methodology_with_history` returns.
    """

    benchmark_version: str
    schema_version: int
    constants_hash: str
    pillars: list[PillarEntry]
    parameters: list[ParameterEntry]
    gates: list[GateEntry]
    missingness: MissingnessSection
    tie_break_levels: TieBreakSection
    personas: PersonasSection
    detectors: list[DetectorEntry]
    not_hashed_process_constants: list[ProcessConstantEntry]
    rfc_log: list[RFCLogEntry]


# --------------------------------------------------------------------------- #
# Parameters (02 section 4): curve description + value domain per input id    #
# --------------------------------------------------------------------------- #

#: Input ids measured over the shared 180-day PR window (``bundle.pr_window``,
#: 02 section 3 D-IN-4). Not every parameter has its own frozen window
#: constant (e.g. 3.2 elephant_factor's 365-day commit window is not a
#: registered ``constants`` name), so ``window_days`` is populated only
#: for the parameters this shared constant genuinely measures -- never a
#: fabricated number for the others (they report ``window_days: None``).
_PR_WINDOW_PARAM_IDS: frozenset[str] = frozenset(
    {
        "ttfhr_hours",
        "dr90_decision_rate",
        "mr_first_rate",
        "gr14_ghost_rate",
        "ttm_p90_hours",
        "substantive_review_rate",
    }
)


def _curve_description(input_id: str) -> str:
    """Human-readable curve formula for ``input_id``, built from named
    ``constants`` attributes read at call time -- never a hand-typed
    number. Raises on an unregistered id (completeness guard mirroring
    ``porchlamp.core.params._registry_problems``): a new parameter added to
    ``PARAM_INPUT_IDS`` without an entry here fails loudly instead of
    silently omitting its curve."""
    if input_id == "ttfhr_hours":
        knee = constants.TTFHR_KNEE_HOURS
        slope = constants.TTFHR_SLOPE_PER_DOUBLING
        return (
            f"100 if the median hours to first substantive human response is "
            f"<= {knee:g}; else max(0, 100 - {slope:g} * log2(t / {knee:g}))"
        )
    if input_id in ("dr90_decision_rate", "mr_first_rate", "substantive_review_rate"):
        return f"min(100, {constants.RATE_SLOPE:g} * r)"
    if input_id == "gr14_ghost_rate":
        return f"max(0, 100 - {constants.GHOST_RATE_SLOPE:g} * g)"
    if input_id == "ttm_p90_hours":
        return "100 - ECDF_mid(p90), mid-rank ECDF over the frozen ecosystem population (D-NORM-1)"
    if input_id in ("dependents_deduped", "downloads_monthly"):
        return "ECDF_mid(value) on the raw measured value, no transform on either side (D-NORM-1/D-NORM-3)"
    if input_id == "openssf_criticality":
        return "100 * C, the locally computed OpenSSF/Pike criticality score C in [0, 1]"
    if input_id == "legal_ip":
        return "evidence-based regime lookup; see this parameter's value_domain.score_table"
    if input_id == "elephant_factor":
        return f"min(100, {constants.ELEPHANT_SLOPE:g} * EF)"
    if input_id == "promotion_ladder":
        return "evidence-based rung lookup; see this parameter's value_domain.score_table"
    if input_id == "pipeline_defenses":
        return (
            "identity, clamped to [0, 100] (Scorecard check mean, or the proxy blend when absent)"
        )
    if input_id == "ai_posture":
        return "persona-conditional posture lookup; see this parameter's value_domain.score_table"
    if input_id == "newcomer_issue_supply":
        return f"min(100, {constants.SUPPLY_SLOPE:g} * count)"
    if input_id in ("newcomer_conversion", "rr90_return_rate"):
        return f"min(100, {constants.COHORT_RATE_SLOPE:g} * r)"
    if input_id == "dx":
        return (
            "identity, clamped to [0, 100] (0.5 CI runtime score + 0.5 setup-signal score, "
            "or setup-signal score proxy when CI runtime has fewer than min_n commits)"
        )
    raise ValueError(f"methodology.py: no curve description registered for input id {input_id!r}")


def _enum_score_table(input_id: str) -> dict[str, object] | None:
    """The frozen enum -> score table for ``input_id``, read fresh from
    ``constants`` (never a hand-typed number). ``None`` for a non-enum
    parameter."""
    if input_id == "legal_ip":
        return dict(constants.LEGAL_IP_SCORES)
    if input_id == "promotion_ladder":
        return dict(constants.PROMOTION_LADDER_SCORES)
    if input_id == "ai_posture":
        return {persona: dict(scores) for persona, scores in constants.AI_POSTURE_SCORES.items()}
    return None


def _value_domain(defn: ParamDef) -> dict[str, object]:
    """The parameter's value domain/vocabulary, from ``ParamDef`` attributes
    (never a hand-typed number) plus the enum score table when applicable."""
    if defn.kind in (KIND_NUMERIC, KIND_ECDF):
        if defn.domain is None:
            raise ValueError(f"defn.domain is unexpectedly None for {defn.kind}")
        return {
            "kind": defn.kind,
            "domain_min": defn.domain[0],
            "domain_max": defn.domain[1],
            "higher_is_better": defn.higher_is_better,
        }
    if defn.kind == KIND_ENUM:
        domain: dict[str, object] = {"kind": KIND_ENUM}
        if defn.persona_orders is not None:
            domain["persona_orders"] = {
                persona: list(order) for persona, order in defn.persona_orders.items()
            }
        elif defn.better_order is not None:
            domain["values_worst_to_best"] = list(defn.better_order)
        score_table = _enum_score_table(defn.input_id)
        if score_table is not None:
            domain["score_table"] = score_table
        return domain
    raise ValueError(f"methodology.py: unknown ParamDef.kind {defn.kind!r} for {defn.input_id!r}")


def _parameter_entry(input_id: str) -> ParameterEntry:
    defn = PARAMS[input_id]
    entry: ParameterEntry = {
        "input_id": defn.input_id,
        "pillar": defn.pillar,
        "weight_within_pillar": defn.weight,
        "min_n": defn.min_n,
        "window_days": constants.PR_WINDOW_DAYS if input_id in _PR_WINDOW_PARAM_IDS else None,
        "curve": _curve_description(input_id),
        "value_domain": _value_domain(defn),
    }
    proxy: ProxyInfo = {}
    if defn.proxy_min_n is not None:
        proxy["proxy_min_n"] = defn.proxy_min_n
    if input_id == "openssf_criticality":
        # D-MISS-5 consequence (02 section 5): the no-dependents-signal proxy's
        # own minimum, not modelled as ParamDef.proxy_min_n (that field is
        # 4.1 pipeline_defenses' -- a different proxy mechanism entirely).
        proxy["proxy_min_signals"] = constants.OPENSSF_PROXY_MIN_SIGNALS
    if proxy:
        entry["proxy"] = proxy
    return entry


# --------------------------------------------------------------------------- #
# Gates (02 section 2: D-GATE-1..6, D-EVID-1)                                 #
# --------------------------------------------------------------------------- #


def _quoted_alternatives(items: tuple[str, ...]) -> str:
    """``"a"``, ``"a" or "b"``, ``"a", "b" or "c"``: the items quoted, for prose."""
    quoted = [f'"{item}"' for item in items]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " or " + quoted[-1]


def _gate_entries() -> list[GateEntry]:
    undetermined = sorted(v for v in _gates.UNDETERMINED_SPDX if v is not None)
    if None in _gates.UNDETERMINED_SPDX:
        undetermined = [*undetermined, None]
    return [
        {
            "id": _gates.GATE_1,
            "name": "Software project",
            "criterion": (
                "repository tree contains a recognised build/package manifest at "
                "depth <= 2, or the repository is a curated anchor"
            ),
            "reasons": (_gates.REASON_NON_SOFTWARE,),
            "cutoffs": {},
        },
        {
            "id": _gates.GATE_2,
            "name": "Not archived, not a mirror, not a fork",
            "criterion": (
                "archived == false, mirror_url is null, fork == false (a fork is "
                "allowed only if its parent is absent from the universe)"
            ),
            "reasons": (_gates.REASON_ARCHIVED, _gates.REASON_MIRROR, _gates.REASON_FORK),
            "cutoffs": {},
        },
        {
            "id": _gates.GATE_3,
            "name": "Licensed",
            "criterion": (
                "SPDX id present and OSI-approved; an undetermined SPDX id falls "
                "back to a LICENSE* file search before the gate fails"
            ),
            "reasons": (_gates.REASON_UNLICENSED,),
            "cutoffs": {},
            "license_vocabulary": {"undetermined_spdx": undetermined},
        },
        {
            "id": _gates.GATE_4,
            "name": "Vitality",
            "criterion": (
                ">= 1 commit by a non-bot User on the default branch in the trailing window"
            ),
            "reasons": (_gates.REASON_STALE,),
            "cutoffs": {"vitality_window_days": constants.GATE4_VITALITY_WINDOW_DAYS},
        },
        {
            "id": _gates.GATE_5,
            "name": "Not a black hole",
            "criterion": (
                "over external PRs by non-bot authors (D-IN-1) opened in the "
                "trailing PR window and at least the minimum age, the 95% one-sided "
                f"Wilson lower confidence bound (z = {constants.GATE5_WILSON_Z}) on the "
                "ignored share must not exceed the ceiling (RFC-0026: the ignored share is the "
                "share of those PRs that no maintainer reply, review, merge or close touched, "
                "within the deadline or since, when the data was read; a project that answers "
                "late is slow, not a black hole, and its 14-day unanswered share is scored by "
                "1.4 GR_14, not gated) (a minimum population of "
                f"{constants.GATE5_MIN_N} is required, else the gate is UNDECIDED rather "
                "than failed, and a repository whose PR records lack the fields the exclusions "
                "below read is UNDECIDED, neither passed nor failed: the missing fields move the "
                "share both ways); the raw share is the gr14_ghost_rate parameter, computed for "
                "such a repository without the rules those fields feed (the draft and "
                "self-close exclusions, and a maintainer close counted as a response). The "
                "gate judges only a PR record that covers the whole window. The collector "
                "records every PR opened in the window; when there are more than "
                f"{constants.PR_CAP:,}, it records replies and reviews on an even random "
                f"sample of {constants.PR_SAMPLE_CAP:,} of them, drawn by PR number with a "
                "fixed seed, and the gate uses the sample's share and size (RFC-0013). A "
                "record that stops short of the window's start is UNDECIDED, never judged on "
                "the newest weeks as if they were the window; the time the record starts "
                "(the cut-off) is recorded with the gate's evidence (RFC-0011). A reply or "
                "review counts when it is substantive (D-IN-2: any review, or a comment whose "
                "text, without markup, is at least "
                f"{constants.SUBSTANTIVE_COMMENT_MIN_CHARS} characters long, each Chinese, "
                f"Japanese or Korean letter counting {constants.SUBSTANTIVE_COMMENT_CJK_WEIGHT} "
                "times, since those scripts say in one letter about what Latin text says in "
                "three) by the repository's own people: an account that is not the PR's "
                "author, not a bot (D-IN-1), and is a member, owner or collaborator, or "
                "merged a PR in the recorded window, or closed a PR someone else opened "
                "there. Merging or closing another author's PR takes repository access, "
                "whatever association GitHub shows: org membership can be private, and "
                "committers who land changes outside GitHub show as contributors. A merge "
                "or maintainer close within the deadline counts on its "
                "own. Five false-positive exclusions apply (RFC-0008, RFC-0010): draft PRs are excluded "
                "until marked ready for review (clock starts at ready_at); author self-closed PRs "
                "with no earlier maintainer response are excluded only when the author closed them within "
                f"the {constants.GATE5_RESPONSE_DEADLINE_DAYS}-day response deadline from clock start, and a "
                "later self-close with no response counts as unanswered; accounts under 90 days old "
                "(MIN_ACCOUNT_AGE_DAYS) are excluded; maintainer closes within 14 days count "
                "as responses; and the 14-day observation age is judged from clock start against "
                "min(now, observed_at)"
            ),
            "reasons": (_gates.REASON_BLACK_HOLE,),
            "cutoffs": {
                "min_n": constants.GATE5_MIN_N,
                "min_age_days": constants.GATE5_MIN_AGE_DAYS,
                "response_deadline_days": constants.GATE5_RESPONSE_DEADLINE_DAYS,
                "max_zero_response_share": constants.GATE5_MAX_ZERO_RESPONSE_SHARE,
            },
        },
        {
            "id": _gates.GATE_6,
            "name": "PR workflow",
            "criterion": (
                ">= 1 external PR (bots included: the gate asks whether the repository "
                "works through GitHub pull requests at all) decided (merged or closed) "
                "in the trailing decision window, read from the recorded PR window, the "
                "365-day author history and the 30 newest PRs, or off-platform markers "
                "present (routed, not failed); neither means unrated: "
                "no_external_workflow, but only when those records hold the "
                "repository's whole PR history (the 30 newest were fewer than 30, and no "
                "window or history read beside them is truncated): otherwise the gate is "
                "UNDECIDED, because a PR opened before the records may have been decided "
                "inside the window. Each record shows decisions up to its own fetch. "
                "RFC-0027: a repository whose complete PR window holds fewer than "
                f"{constants.GATE6_MIN_OUTSIDE_PRS} outside PRs (external, non-bot authors) opened in the trailing "
                f"{constants.PR_WINDOW_DAYS} days is also unrated: no_external_workflow, whatever it decided, "
                "because there is too little outside traffic to contribute to or to measure "
                "(routed instead when it works off-platform; UNDECIDED when that is unknown). A "
                "repository also routes "
                "off-platform, whatever its decided PRs, when it hands review off: "
                "among its outside PRs (external, non-bot authors in the recorded "
                f"PR window) at least {constants.GATE6_HANDOFF_MIN_PRS}, and at least "
                f"{constants.GATE6_HANDOFF_MIN_SHARE:.0%} of them, carry a notice, a "
                "comment by an account other than the PR's author that contains "
                f"{_quoted_alternatives(constants.GATE6_HANDOFF_NOTICES)} "
                "(any letter case) or is written by an account whose login starts "
                f"with {_quoted_alternatives(constants.GATE6_HANDOFF_RELAY_LOGIN_PREFIXES)}. "
                "D-GATE-5 is then undecided for it, never failed"
            ),
            "reasons": (_gates.REASON_NO_EXTERNAL_WORKFLOW,),
            "cutoffs": {
                "decision_window_days": constants.GATE6_DECISION_WINDOW_DAYS,
                "min_outside_prs": constants.GATE6_MIN_OUTSIDE_PRS,
                "handoff_min_prs": constants.GATE6_HANDOFF_MIN_PRS,
                "handoff_min_share": constants.GATE6_HANDOFF_MIN_SHARE,
            },
        },
        {
            "id": "D-EVID-1",
            "name": "Evidence sufficiency",
            "criterion": (
                "composite coverage must reach the rated minimum and every required pillar must have at least one "
                "measured parameter (RFC-0024), else listed unranked"
            ),
            "reasons": (_gates.REASON_INSUFFICIENT_EVIDENCE,),
            "cutoffs": {
                "min_composite_coverage": constants.EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE,
                "required_pillars": ", ".join(constants.EVIDENCE_GATE_REQUIRED_PILLARS),
            },
        },
    ]


# --------------------------------------------------------------------------- #
# Missingness (02 section 5: D-MISS-1..5)                                     #
# --------------------------------------------------------------------------- #

#: One-sentence semantics per D-MISS-1 reason, keyed by the reason string
#: itself (the literal values ``constants.UNMEASURED_REASONS`` carries).
#: Prose only -- no number lives in this table; the reasons and thresholds
#: that ARE numbers are read from ``constants`` in
#: :func:`_missingness_section` below.
_MISSINGNESS_SEMANTICS: dict[str, str] = {
    "third_party_gap": (
        "A third party (a registry, OpenSSF, deps.dev) never provided the "
        "fact; no maintainer action can close this gap."
    ),
    "collector_failure": (
        "The collector attempted to measure this input and failed (a fetch "
        "error, a stale window anchor); a retry can still measure it."
    ),
    "insufficient_n": (
        "The input was measured but its sample size fell below the "
        "parameter's minimum n; the measurement is recorded but scored as "
        "unmeasured."
    ),
    "not_applicable": (
        "The parameter is undefined for this repository's class by a closed, "
        "design-level rule (D-MISS-5); excluded from both the numerator and "
        "the denominator of coverage."
    ),
    "no_population": (
        "The parameter is ECDF-normalised and no rated, measured repository "
        "exists in its ecosystem or globally at this snapshot to build a "
        "population from."
    ),
}


def _missingness_section() -> MissingnessSection:
    reasons: list[MissingnessReason] = [
        {"reason": reason, "semantics": _MISSINGNESS_SEMANTICS[reason]}
        for reason in constants.UNMEASURED_REASONS
    ]
    return {
        "reasons": reasons,
        "coverage_thresholds": {
            "unrated_below": constants.EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE,
            "ceiling_b_below": constants.CEILING_A_MIN_COVERAGE,
            "ceiling_a_below": constants.NO_CEILING_MIN_COVERAGE,
        },
        "not_applicable_rules": [
            {"parameter": parameter, "bundle_fact": fact, "value": value}
            for parameter, fact, value in constants.NOT_APPLICABLE_RULES
        ],
        "openssf_proxy_min_signals": constants.OPENSSF_PROXY_MIN_SIGNALS,
    }


# --------------------------------------------------------------------------- #
# Tie-breakers (02 section 10: D-TIE-1/2)                                     #
# --------------------------------------------------------------------------- #


def _tie_break_section() -> TieBreakSection:
    decimals = constants.COMPOSITE_DECIMALS
    levels: list[TieBreakLevel] = [
        {
            "field": "composite",
            "direction": "descending",
            "note": f"rounded to {decimals} decimal place(s) before comparison",
        },
        {"field": "coverage", "direction": "descending", "note": None},
        {"field": "responsiveness_pillar_score", "direction": "descending", "note": None},
        {"field": "friction", "direction": "ascending", "note": None},
        {"field": "leverage", "direction": "descending", "note": None},
        {
            "field": "ttfhr_p75_hours",
            "direction": "ascending",
            "note": "p75, not the median: consistency, not just the typical case",
        },
        {"field": "last_non_bot_commit_at", "direction": "descending", "note": None},
        {
            "field": "owner_name_lowercase",
            "direction": "ascending",
            "note": "deterministic last resort; never stars, forks or downloads",
        },
    ]
    return {"composite_decimals": decimals, "levels": levels}


# --------------------------------------------------------------------------- #
# Personas (02 section 9: D-PERS-1..3)                                        #
# --------------------------------------------------------------------------- #


def _persona_gate_catalog() -> dict[str, PersonaGateEntry]:
    return {
        "ai_assisted": {
            "persona": "ai_assisted",
            "description": (
                "Hard AI ban (exclusion gate): fires only when ai_posture is "
                "measured PROSCRIBED with non-empty evidence."
            ),
            "thresholds": {},
        },
        "senior_systems": {
            "persona": "senior_systems",
            "description": (
                "Multi-vendor (exclusion gate): fires only on a measured "
                "elephant factor below the minimum."
            ),
            "thresholds": {
                "min_elephant_factor": constants.SENIOR_SYSTEMS_MIN_ELEPHANT_FACTOR,
            },
        },
        "first_timer": {
            "persona": "first_timer",
            "description": (
                "Low barrier (inclusion gate): shown when decided first-timer "
                "PRs in the window reach the minimum count and MR_first reaches "
                "its minimum rate, or the newcomer-issue-supply count reaches "
                "its minimum; an unmeasured input never satisfies an inclusion "
                "gate (D-PERS-3)."
            ),
            "thresholds": {
                "min_decided_prs": constants.FIRST_TIMER_MIN_DECIDED_PRS,
                "min_mr_first": constants.FIRST_TIMER_MIN_MR_FIRST,
                "min_newcomer_issues": constants.FIRST_TIMER_MIN_NEWCOMER_ISSUES,
            },
        },
        "career_capital": {
            "persona": "career_capital",
            "description": (
                "High-tier brand (inclusion gate): shown when the criticality "
                "pillar score and its coverage both reach their minimums."
            ),
            "thresholds": {
                "min_criticality": constants.CAREER_CAPITAL_MIN_CRITICALITY,
                "min_coverage": constants.CAREER_CAPITAL_MIN_COVERAGE,
            },
        },
    }


def _personas_section() -> PersonasSection:
    presets = {preset: dict(weights) for preset, weights in constants.PERSONA_PRESETS.items()}
    catalog = _persona_gate_catalog()
    gates = [catalog[name] for name in constants.GATED_PERSONAS]
    return {"presets": presets, "gates": gates}


# --------------------------------------------------------------------------- #
# Assembly                                                                     #
# --------------------------------------------------------------------------- #


def methodology_document() -> MethodologyDocument:
    """The pure v2 methodology document (06 section 3 F): benchmark version,
    constants hash, schema version, pillars, parameters, gates,
    missingness, tie-break levels and personas -- built only from
    ``porchlamp.core.constants`` and the ``porchlamp.core.params`` registry, read
    fresh on every call. Does not include ``detectors``,
    ``not_hashed_process_constants`` or ``rfc_log`` (see the module
    docstring's import-boundary note); use
    :func:`porchlamp.audit.methodology_history.methodology_with_history` for the
    extended document that adds those three."""
    return {
        "benchmark_version": constants.BENCHMARK_VERSION,
        "schema_version": constants.SCHEMA_VERSION,
        "constants_hash": constants.CONSTANTS_HASH,
        "pillars": [
            {"name": name, "weight": constants.PILLAR_WEIGHTS[name]}
            for name in constants.PILLAR_NAMES
        ],
        "parameters": [_parameter_entry(input_id) for input_id in constants.PARAM_INPUT_IDS],
        "gates": _gate_entries(),
        "missingness": _missingness_section(),
        "tie_break_levels": _tie_break_section(),
        "personas": _personas_section(),
    }


def methodology_hash(document: dict[str, object] | None = None) -> str:
    """``sha256`` of the canonical JSON of ``document`` (default: a fresh
    :func:`methodology_document`). Changes iff the underlying constants
    (``CONSTANTS_HASH``) or the ``porchlamp.core.params`` registry change --
    never on a docstring/comment-only edit, since neither affects any
    attribute value (``tests/core/test_methodology.py``)."""
    doc = document if document is not None else methodology_document()
    return sha256_hex(canonical_json(doc))
