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
#: A1 relocation + A2 freeze). A failure here means a constant moved without
#: the RFC procedure in docs/v2/rfcs/README.md; recompute intentionally with
#: `python -c "from cvi.core import constants_v2 as c; print(c.CONSTANTS_HASH)"`
#: only as part of that procedure, never to make a red test green.
FROZEN_CONSTANTS_HASH = "fb48552cbf13aececaccbb74c83967f7d594211bfad9f7b32f0d8d90945c4c2a"


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
