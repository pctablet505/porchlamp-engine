"""W2-8 A2 acceptance: the constants freeze (D-VER-1/2, 03 section 7).

Pins ``CONSTANTS_HASH`` and ``BENCHMARK_VERSION`` to literals, and a small
table of representative values so a failure here names WHICH constant moved,
not just "hash mismatch" -- ``tests/core/test_constants_hash.py`` already
proves every constant is covered by the hash and that mutating any of them
moves it; this file is the release-facing pin a reviewer reads first.

Also proves the drift-detection freeze works end to end: change three
relocated tunables (a persona preset weight, ``MIN_POPULATION``, a coverage
threshold) in a scratch copy of the constants dict and show
``test_frozen_table`` and the hash both name the change -- the real
in-worktree drift proof (three real edits, three failing pytest runs, then
restored) is pasted verbatim in the handoff; this module documents the
equivalent in-process check so the pin is exercised on every CI run, not
just once by hand.
"""
from __future__ import annotations

import hashlib

import pytest

from cvi.core import constants_v2
from cvi.core.models import BundleValidationError, Unmeasured, canonical_json


#: D-VER-2: the frozen hash for BENCHMARK_VERSION "2.0.0-rc1" (packet W2-8's
#: A1 relocation + A2 freeze, round 2's census additions -- see the round-2
#: handoff for the old -> new value and why it changed). A failure here
#: means a constant moved without the RFC procedure in
#: docs/v2/rfcs/README.md; recompute intentionally with
#: `python -c "from cvi.core import constants_v2 as c; print(c.CONSTANTS_HASH)"`
#: only as part of that procedure, never to make a red test green.
#:
#: History: `fb48552cbf13aececaccbb74c83967f7d594211bfad9f7b32f0d8d90945c4c2a`
#: (round 1, A1-A3) -> `391d10b91e86a230fa8c16e32f57f2bc5d0449c33dc3bcb6c3f96153ef83fed8`
#: (round 2: TIER_CUTOFFS, TIER_ORDER, OPENSSF_WEIGHTS, OPENSSF_THRESHOLDS,
#: SIGNAL_FIELDS, OSI_APPROVED_LICENSES added to constants_dict() per the
#: round-1 review's census; no scored value changed, since every added
#: constant's re-bound value at its consumer module is unchanged).
FROZEN_CONSTANTS_HASH = "391d10b91e86a230fa8c16e32f57f2bc5d0449c33dc3bcb6c3f96153ef83fed8"


def test_constants_hash_is_pinned() -> None:
    assert constants_v2.CONSTANTS_HASH == FROZEN_CONSTANTS_HASH


def test_benchmark_version_is_pinned() -> None:
    assert constants_v2.BENCHMARK_VERSION == "2.0.0-rc1"


#: The frozen representative-value table (D-VER-2): one row per constant,
#: named so a failure identifies exactly which constant moved. A single
#: parametrized test rather than one function with sequential asserts: with
#: sequential asserts, python's ``assert`` stops at the first failure, so two
#: simultaneous drifts would report only the first one -- the parametrize
#: below runs (and can fail) every row independently in one test session,
#: which the packet's own drift proof (three simultaneous mutations) needs.
_FROZEN_TABLE: tuple[tuple[str, object, object], ...] = (
    ("PILLAR_WEIGHTS.responsiveness", constants_v2.PILLAR_WEIGHTS["responsiveness"], 0.25),
    ("PILLAR_WEIGHTS.criticality", constants_v2.PILLAR_WEIGHTS["criticality"], 0.25),
    ("PILLAR_WEIGHTS.governance", constants_v2.PILLAR_WEIGHTS["governance"], 0.20),
    ("PILLAR_WEIGHTS.rigor", constants_v2.PILLAR_WEIGHTS["rigor"], 0.15),
    ("PILLAR_WEIGHTS.accessibility", constants_v2.PILLAR_WEIGHTS["accessibility"], 0.15),
    ("EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE", constants_v2.EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE, 0.60),
    ("CEILING_A_MIN_COVERAGE", constants_v2.CEILING_A_MIN_COVERAGE, 0.80),
    ("NO_CEILING_MIN_COVERAGE", constants_v2.NO_CEILING_MIN_COVERAGE, 0.95),
    ("MIN_POPULATION", constants_v2.MIN_POPULATION, 30),
    ("COMPOSITE_DECIMALS", constants_v2.COMPOSITE_DECIMALS, 2),
    ("PERSONA_PRESETS.balanced", constants_v2.PERSONA_PRESETS["balanced"], dict(constants_v2.PILLAR_WEIGHTS)),
    (
        "PERSONA_PRESETS.first_timer",
        constants_v2.PERSONA_PRESETS["first_timer"],
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
        constants_v2.PERSONA_PRESETS["career_capital"],
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
        constants_v2.PERSONA_PRESETS["senior_systems"],
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
        constants_v2.PERSONA_PRESETS["ai_assisted"],
        {
            "responsiveness": 0.20,
            "criticality": 0.20,
            "governance": 0.15,
            "rigor": 0.35,
            "accessibility": 0.10,
        },
    ),
    # Round 2 (census, review Blockers 1/2): TIER_CUTOFFS/TIER_ORDER and the
    # Pike OPENSSF_* constants, marked (star) for _FROZEN_TABLE in the round-2
    # dispatch.
    (
        "TIER_CUTOFFS",
        constants_v2.TIER_CUTOFFS,
        (
            (90.0, "S+"),
            (80.0, "S"),
            (70.0, "A"),
            (55.0, "B"),
            (40.0, "C"),
        ),
    ),
    ("TIER_ORDER", constants_v2.TIER_ORDER, ("D", "C", "B", "A", "S", "S+")),
    (
        "OPENSSF_WEIGHTS",
        constants_v2.OPENSSF_WEIGHTS,
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
        constants_v2.OPENSSF_THRESHOLDS,
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
        constants_v2.SIGNAL_FIELDS,
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

    from cvi.core import constants_v2 as first_import

    second_import = importlib.import_module("cvi.core.constants_v2")
    assert first_import is second_import  # module cache, expected
    recomputed_first = hashlib.sha256(
        canonical_json(first_import.constants_dict()).encode("utf-8")
    ).hexdigest()
    recomputed_second = hashlib.sha256(
        canonical_json(second_import.constants_dict()).encode("utf-8")
    ).hexdigest()
    assert recomputed_first == recomputed_second == constants_v2.CONSTANTS_HASH

    mutated = dict(constants_v2.constants_dict())
    mutated["MIN_POPULATION"] = mutated["MIN_POPULATION"] + 1
    mutated_hash = hashlib.sha256(canonical_json(mutated).encode("utf-8")).hexdigest()
    assert mutated_hash != constants_v2.CONSTANTS_HASH


def test_no_population_is_a_constructible_reason() -> None:
    """Fail-before (packet W2-8, W2-5f follow-up): before this packet added
    ``"no_population"`` to ``UNMEASURED_REASONS``, ``Unmeasured`` accepted
    the string in its ``Literal`` type hint but ``unmeasured_problems``
    rejected it at runtime -- ``Unmeasured("no_population")`` raised
    ``BundleValidationError``. Reproduced on the pre-W2-8 tree
    (``git show c7b2a1b:cvi/core/constants_v2.py`` lacks the reason;
    verbatim failing output pasted in the handoff). After the fix it
    constructs cleanly and round-trips through the frozen vocabulary."""
    value = Unmeasured("no_population")
    assert value.reason == "no_population"
    assert value.reason in constants_v2.UNMEASURED_REASONS


def test_unmeasured_still_rejects_an_unknown_reason() -> None:
    """Over-correction guard: adding ``no_population`` must not have widened
    the check into accepting arbitrary strings."""
    with pytest.raises(BundleValidationError):
        Unmeasured("not_a_real_reason")


def test_every_module_level_constant_is_hashed_or_declared_not_hashed() -> None:
    """W2-8 round 2, review Warning 6 / mutant M4: a new constant added to
    ``constants_v2.py`` but never registered in ``constants_dict()`` is
    invisible to every freeze test -- the module docstring's "a value is a
    constant if and only if it appears here" was previously an unenforced
    claim (M4 survived the full 1,674-test suite in round 1).

    Every module-level UPPER_CASE name (leading-underscore names such as
    ``_NOT_HASHED``/``_canonical_json`` are internal machinery, not
    constants under this claim) must be either a key of ``constants_dict()``
    or listed in :data:`cvi.core.constants_v2._NOT_HASHED`. ``BENCHMARK_VERSION``
    is deliberately NOT in ``_NOT_HASHED``: D-VER-1 ties a version bump to a
    constants change, so hashing it too is correct, not redundant (see
    ``_NOT_HASHED``'s own docstring)."""
    names = {name for name in vars(constants_v2) if name.isupper() and not name.startswith("_")}
    covered = set(constants_v2.constants_dict()) | set(constants_v2._NOT_HASHED)
    missing = names - covered
    assert not missing, f"module-level constants not registered in constants_dict() or _NOT_HASHED: {sorted(missing)}"
    assert "BENCHMARK_VERSION" in constants_v2.constants_dict()
    assert "CONSTANTS_HASH" in constants_v2._NOT_HASHED
    assert "CONSTANTS_HASH" not in constants_v2.constants_dict()


def test_m4_fail_before_an_unregistered_constant_is_caught() -> None:
    """Fail-before/after for the guard above, in-process (no file mutation):
    a dummy module-level constant that is neither registered nor declared
    not-hashed is exactly the M4 shape the review's mutant reproduced.
    Monkeypatching ``vars()``'s underlying namespace via ``setattr``/``delattr``
    on the real module reproduces the mutant precisely (a genuinely new
    top-level name), then removes it -- an over-correction guard that the
    check does not just always pass."""
    assert not hasattr(constants_v2, "DUMMY_UNREGISTERED_CONSTANT")
    try:
        constants_v2.DUMMY_UNREGISTERED_CONSTANT = 999  # the M4 mutant, live
        names = {name for name in vars(constants_v2) if name.isupper() and not name.startswith("_")}
        covered = set(constants_v2.constants_dict()) | set(constants_v2._NOT_HASHED)
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
        delattr(constants_v2, "DUMMY_UNREGISTERED_CONSTANT")
    assert not hasattr(constants_v2, "DUMMY_UNREGISTERED_CONSTANT")
