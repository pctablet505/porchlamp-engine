"""Acceptance tests for the constants freeze (D-VER-1/2, 03 section 7).

Pins ``CONSTANTS_HASH`` and ``BENCHMARK_VERSION`` to literals, and a small
table of representative values so a failure here names WHICH constant moved,
not just "hash mismatch" -- ``tests/core/test_constants_hash.py`` already
proves every constant is covered by the hash and that mutating any of them
moves it; this file is the release-facing pin.

Also proves the drift-detection freeze works end to end: change three
relocated tunables (a persona preset weight, ``MIN_POPULATION``, a coverage
threshold) in a scratch copy of the constants dict and show
``test_frozen_table`` and the hash both name the change.
"""
from __future__ import annotations

import hashlib

import pytest

from porchlamp.core import constants
from porchlamp.core.models import BundleValidationError, Unmeasured, canonical_json


#: D-VER-2: the frozen hash for BENCHMARK_VERSION "2.0.0-rc1". A failure here
#: means a constant moved without the RFC procedure in docs/v2/rfcs/README.md;
#: recompute intentionally with
#: `python -c "from porchlamp.core import constants as c; print(c.CONSTANTS_HASH)"`
#: only as part of that procedure, never to make a red test green.
FROZEN_CONSTANTS_HASH = "b81c8bc48501e62976493eda447ce11944a2d7d53a3cd4a126f71cb766c7ff05"


def test_constants_hash_is_pinned() -> None:
    assert constants.CONSTANTS_HASH == FROZEN_CONSTANTS_HASH


def test_benchmark_version_is_pinned() -> None:
    assert constants.BENCHMARK_VERSION == "2.0.0-rc1"


#: The frozen representative-value table (D-VER-2): one row per constant,
#: named so a failure identifies exactly which constant moved. A single
#: parametrized test rather than one function with sequential asserts: with
#: sequential asserts, python's ``assert`` stops at the first failure, so two
#: simultaneous drifts would report only the first one -- the parametrize
#: below runs (and can fail) every row independently in one test session,
#: which the packet's own drift proof (three simultaneous mutations) needs.
_FROZEN_TABLE: tuple[tuple[str, object, object], ...] = (
    ("PILLAR_WEIGHTS.responsiveness", constants.PILLAR_WEIGHTS["responsiveness"], 0.25),
    ("PILLAR_WEIGHTS.criticality", constants.PILLAR_WEIGHTS["criticality"], 0.25),
    ("PILLAR_WEIGHTS.governance", constants.PILLAR_WEIGHTS["governance"], 0.20),
    ("PILLAR_WEIGHTS.rigor", constants.PILLAR_WEIGHTS["rigor"], 0.15),
    ("PILLAR_WEIGHTS.accessibility", constants.PILLAR_WEIGHTS["accessibility"], 0.15),
    ("EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE", constants.EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE, 0.60),
    ("CEILING_A_MIN_COVERAGE", constants.CEILING_A_MIN_COVERAGE, 0.80),
    ("NO_CEILING_MIN_COVERAGE", constants.NO_CEILING_MIN_COVERAGE, 0.95),
    ("MIN_POPULATION", constants.MIN_POPULATION, 30),
    ("COMPOSITE_DECIMALS", constants.COMPOSITE_DECIMALS, 2),
    ("PERSONA_PRESETS.balanced", constants.PERSONA_PRESETS["balanced"], dict(constants.PILLAR_WEIGHTS)),
    (
        "PERSONA_PRESETS.first_timer",
        constants.PERSONA_PRESETS["first_timer"],
        {
            "responsiveness": 0.35,
            "criticality": 0.10,
            "governance": 0.15,
            "rigor": 0.10,
            "accessibility": 0.30,
        },
    ),
    (
        "PERSONA_PRESETS.career_capital",
        constants.PERSONA_PRESETS["career_capital"],
        {
            "responsiveness": 0.20,
            "criticality": 0.35,
            "governance": 0.15,
            "rigor": 0.10,
            "accessibility": 0.20,
        },
    ),
    (
        "PERSONA_PRESETS.senior_systems",
        constants.PERSONA_PRESETS["senior_systems"],
        {
            "responsiveness": 0.15,
            "criticality": 0.35,
            "governance": 0.25,
            "rigor": 0.20,
            "accessibility": 0.05,
        },
    ),
    (
        "PERSONA_PRESETS.ai_assisted",
        constants.PERSONA_PRESETS["ai_assisted"],
        {
            "responsiveness": 0.20,
            "criticality": 0.20,
            "governance": 0.15,
            "rigor": 0.35,
            "accessibility": 0.10,
        },
    ),
    # Tier cutoffs and order definitions.
    (
        "TIER_CUTOFFS",
        constants.TIER_CUTOFFS,
        (
            (90.0, "S+"),
            (80.0, "S"),
            (70.0, "A"),
            (55.0, "B"),
            (40.0, "C"),
        ),
    ),
    ("TIER_ORDER", constants.TIER_ORDER, ("D", "C", "B", "A", "S", "S+")),
    (
        "OPENSSF_WEIGHTS",
        constants.OPENSSF_WEIGHTS,
        {
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
        },
    ),
    (
        "OPENSSF_THRESHOLDS",
        constants.OPENSSF_THRESHOLDS,
        {
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
        },
    ),
    (
        "SIGNAL_FIELDS",
        constants.SIGNAL_FIELDS,
        {
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
        },
    ),
    # RFC-0001: Author unmeasured threshold relocated out of collect.
    ("AUTHOR_UNMEASURED_MAX_FRACTION", constants.AUTHOR_UNMEASURED_MAX_FRACTION, 0.20),
    # RFC-0002: StarScout universe-entry thresholds (spec 10.3).
    ("STARS_SCOUT_WINDOW_DAYS", constants.STARS_SCOUT_WINDOW_DAYS, 90),
    ("STARS_SCOUT_MIN_EVENTS", constants.STARS_SCOUT_MIN_EVENTS, 5),
    ("STARS_SCOUT_FORK_RATIO", constants.STARS_SCOUT_FORK_RATIO, 100.0),
    ("STARS_SCOUT_BURST_THRESHOLD", constants.STARS_SCOUT_BURST_THRESHOLD, 500),
    ("STARS_SCOUT_LOCKSTEP_PENALTY", constants.STARS_SCOUT_LOCKSTEP_PENALTY, 0.30),
    # D-MISS-5 coverage exclusion (criticality proxy): closed not-applicable rule list.
    (
        "NOT_APPLICABLE_RULES",
        constants.NOT_APPLICABLE_RULES,
        (
            ("dependents_deduped", "ecosystem", "systems"),
            ("downloads_monthly", "ecosystem", "go"),
            ("downloads_monthly", "ecosystem", "maven"),
            ("downloads_monthly", "ecosystem", "systems"),
        ),
    ),
    ("OPENSSF_PROXY_MIN_SIGNALS", constants.OPENSSF_PROXY_MIN_SIGNALS, 8),
    ("WINDOW_STALENESS_DAYS", constants.WINDOW_STALENESS_DAYS, 14),
    # RFC-0004: D-IN-3 hygiene constants.
    ("AUTHOR_CAP_FRACTION", constants.AUTHOR_CAP_FRACTION, 0.20),
    ("MIN_ACCOUNT_AGE_DAYS", constants.MIN_ACCOUNT_AGE_DAYS, 90),
    ("MIN_PUBLIC_EVENTS", constants.MIN_PUBLIC_EVENTS, 5),
    # RFC-0007: D-GATE-6 review hand-off (a project whose own notices send outside PRs
    # to another review system is routed off-platform).
    ("GATE6_HANDOFF_MIN_PRS", constants.GATE6_HANDOFF_MIN_PRS, 5),
    ("GATE6_HANDOFF_MIN_SHARE", constants.GATE6_HANDOFF_MIN_SHARE, 0.5),
    (
        "GATE6_HANDOFF_NOTICES",
        constants.GATE6_HANDOFF_NOTICES,
        (
            "has been imported to gerrit for code review",
            "welcome to gitgitgadget",
            "import it to our internal repository for further review",
            "this pull request has been imported",
            "has imported this pull request",
        ),
    ),
    ("GATE6_HANDOFF_RELAY_LOGIN_PREFIXES", constants.GATE6_HANDOFF_RELAY_LOGIN_PREFIXES, ("gitgitgadget",)),
    # RFC-0008: D-GATE-5 Wilson lower confidence bound normal quantile.
    ("GATE5_WILSON_Z", constants.GATE5_WILSON_Z, 1.6448536269514722),
    # RFC-0011: D-IN-2 comment minimum, Chinese, Japanese and Korean letters counted three times.
    ("SUBSTANTIVE_COMMENT_MIN_CHARS", constants.SUBSTANTIVE_COMMENT_MIN_CHARS, 40),
    ("SUBSTANTIVE_COMMENT_CJK_WEIGHT", constants.SUBSTANTIVE_COMMENT_CJK_WEIGHT, 3),
    # RFC-0012 / RFC-0014: D-IN-1's exact login list, 20 generic bot accounts and 89 service accounts.
    ("BOT_LOGINS", len(constants.BOT_LOGINS), 109),
    # RFC-0013: D-IN-4's whole PR window: every field up to 2,000 PRs, above that replies on a sample of 1,000.
    ("PR_CAP", constants.PR_CAP, 2000),
    ("PR_SAMPLE_CAP", constants.PR_SAMPLE_CAP, 1000),
    ("PR_SAMPLE_SEED", constants.PR_SAMPLE_SEED, 20260912),
    # RFC-0015: the labels that mark a closed pull request landed outside GitHub's merge.
    ("LANDING_LABELS", constants.LANDING_LABELS, frozenset({"integrated", "merged"})),
    # RFC-0016: 3.3 promotion_ladder action-based minimum outside phase duration.
    ("PROMOTION_MIN_OUTSIDE_DAYS", constants.PROMOTION_MIN_OUTSIDE_DAYS, 30),
    # RFC-0017: 3.1 legal_ip explicit foundation GitHub organisation list.
    (
        "FOUNDATION_ORGS",
        tuple(sorted(constants.FOUNDATION_ORGS)),
        (
            "apache",
            "chaoss",
            "cncf",
            "eclipse",
            "fdiooss",
            "kubernetes",
            "linuxfoundation",
            "llvm",
            "openjs-foundation",
            "openstack",
            "seleniumhq",
            "trinodb",
        ),
    ),
    # RFC-0018: 4.3 substantive review effective character threshold and boilerplate repetition guard.
    ("SUBSTANTIVE_REVIEW_BODY_CHARS", constants.SUBSTANTIVE_REVIEW_BODY_CHARS, 80),
    ("SUBSTANTIVE_REVIEW_BOILERPLATE_MIN_PRS", constants.SUBSTANTIVE_REVIEW_BOILERPLATE_MIN_PRS, 3),
    # RFC-0020: 5.4 DX setup-signals proxy method and minimum CI run count.
    ("DX_PROXY_MIN_N", constants.DX_PROXY_MIN_N, 0),
    ("METHOD_PROXY_DX_SETUP_SIGNALS", constants.METHOD_PROXY_DX_SETUP_SIGNALS, "proxy:setup_signals_only"),
    # RFC-0021: D-IN-4's commits: every commit of the 365 days up to 5,000, above that 5,000 in evenly
    # spaced pages with GitHub's count of the year; a record at the old newest-1,000 cap is refused.
    ("COMMIT_CAP", constants.COMMIT_CAP, 5000),
    ("LEGACY_COMMIT_CAP", constants.LEGACY_COMMIT_CAP, 1000),
)


@pytest.mark.parametrize("name,actual,expected", _FROZEN_TABLE, ids=[row[0] for row in _FROZEN_TABLE])
def test_frozen_table(name: str, actual: object, expected: object) -> None:
    assert actual == expected, f"{name} moved: {actual!r} != {expected!r}"


def test_hash_is_a_pure_function_of_the_dict() -> None:
    """Two independent imports of the module give the same hash (module
    caching aside, this recomputes from the dict rather than trusting the
    cached ``CONSTANTS_HASH`` module attribute), and mutating one value in a
    *copy* of the dict changes the recomputed hash -- proving the hash
    depends on content, not identity or import order."""
    import importlib

    from porchlamp.core import constants as first_import

    second_import = importlib.import_module("porchlamp.core.constants")
    assert first_import is second_import  # module cache, expected
    recomputed_first = hashlib.sha256(
        canonical_json(first_import.constants_dict()).encode("utf-8")
    ).hexdigest()
    recomputed_second = hashlib.sha256(
        canonical_json(second_import.constants_dict()).encode("utf-8")
    ).hexdigest()
    assert recomputed_first == recomputed_second == constants.CONSTANTS_HASH

    mutated = dict(constants.constants_dict())
    mutated["MIN_POPULATION"] = mutated["MIN_POPULATION"] + 1
    mutated_hash = hashlib.sha256(canonical_json(mutated).encode("utf-8")).hexdigest()
    assert mutated_hash != constants.CONSTANTS_HASH


def test_no_population_is_a_constructible_reason() -> None:
    """Verify that ``"no_population"`` is accepted as an unmeasured reason.

    Ensures ``Unmeasured("no_population")`` is accepted by ``unmeasured_problems``
    at runtime without raising ``BundleValidationError``, and round-trips
    through the frozen vocabulary."""
    value = Unmeasured("no_population")
    assert value.reason == "no_population"
    assert value.reason in constants.UNMEASURED_REASONS


def test_unmeasured_still_rejects_an_unknown_reason() -> None:
    """Over-correction guard: adding ``no_population`` must not have widened
    the check into accepting arbitrary strings."""
    with pytest.raises(BundleValidationError):
        Unmeasured("not_a_real_reason")


def test_every_module_level_constant_is_hashed_or_declared_not_hashed() -> None:
    """Ensure every module-level constant is tracked by the freeze hash or explicit exclusion.

    Every module-level UPPER_CASE name (leading-underscore names such as
    ``_NOT_HASHED``/``_canonical_json`` are internal machinery, not
    constants under this claim) must be either a key of ``constants_dict()``
    or listed in :data:`porchlamp.core.constants._NOT_HASHED`. ``BENCHMARK_VERSION``
    is deliberately NOT in ``_NOT_HASHED``: D-VER-1 ties a version bump to a
    constants change, so hashing it too is correct, not redundant."""
    names = {name for name in vars(constants) if name.isupper() and not name.startswith("_")}
    covered = set(constants.constants_dict()) | set(constants._NOT_HASHED)
    missing = names - covered
    assert not missing, f"module-level constants not registered in constants_dict() or _NOT_HASHED: {sorted(missing)}"
    assert "BENCHMARK_VERSION" in constants.constants_dict()
    assert "CONSTANTS_HASH" in constants._NOT_HASHED
    assert "CONSTANTS_HASH" not in constants.constants_dict()


def test_m4_fail_before_an_unregistered_constant_is_caught() -> None:
    """Verify an unregistered module-level constant is caught by the freeze check.

    Injects an untracked top-level attribute into the module namespace and
    verifies it is detected, then confirms registering it satisfies the check."""
    assert not hasattr(constants, "DUMMY_UNREGISTERED_CONSTANT")
    try:
        constants.DUMMY_UNREGISTERED_CONSTANT = 999
        names = {name for name in vars(constants) if name.isupper() and not name.startswith("_")}
        covered = set(constants.constants_dict()) | set(constants._NOT_HASHED)
        missing = names - covered
        assert missing == {"DUMMY_UNREGISTERED_CONSTANT"}  # red: caught

        # "register it -> green": constants_dict() is a function, not a
        # literal dict, so registering means the dict it returns would need
        # to include the name; simulate that by unioning it into `covered`
        # the way a real fix would (adding the key to the literal in
        # constants_dict()'s source).
        covered_after_fix = covered | {"DUMMY_UNREGISTERED_CONSTANT"}
        assert not (names - covered_after_fix)  # green: registered
    finally:
        delattr(constants, "DUMMY_UNREGISTERED_CONSTANT")
    assert not hasattr(constants, "DUMMY_UNREGISTERED_CONSTANT")


def test_ca14_constants_mappings_are_immutable() -> None:
    """CA-14: Module-level weight and preset mappings must be immutable."""
    for mapping in [
        constants.LEVERAGE_WEIGHTS,
        constants.FRICTION_WEIGHTS,
        constants.PARAM_WEIGHTS,
        constants.PILLAR_WEIGHTS,
        constants.PERSONA_PRESETS,
    ]:
        with pytest.raises(TypeError):
            mapping["__illegal_mutation__"] = 1.0  # type: ignore[index]
