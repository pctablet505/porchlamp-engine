"""BENCHMARK_VERSION and the frozen CONSTANTS_HASH (D-VER-2).

D-VER-2: all constants live in one frozen module; ``constants_hash =
sha256(canonical JSON)`` is written into every stored row and the snapshot
manifest; a test asserts the hash matches the value recorded for the current
version. Mutating any constant must move the hash.
"""
from __future__ import annotations

import hashlib

from porchlamp.core import constants
from porchlamp.core.models import canonical_json


def _recompute(constants: dict) -> str:
    return hashlib.sha256(canonical_json(constants).encode("utf-8")).hexdigest()


def test_benchmark_version_value() -> None:
    """The dev series is frozen at this version.

    Constants may still change before the "2.0.0" release, but only through
    the RFC procedure in ``docs/v2/rfcs/README.md``."""
    assert constants.BENCHMARK_VERSION == "2.0.0-rc1"


def test_constants_hash_is_sha256_hex() -> None:
    digest = constants.CONSTANTS_HASH
    assert len(digest) == 64
    assert digest == digest.lower()
    _ = int(digest, 16)  # raises for non-hex


def test_recorded_hash_matches_recomputation() -> None:
    """D-VER-2: the recorded hash equals the hash of the current constants."""
    assert _recompute(constants.constants_dict()) == constants.CONSTANTS_HASH


def test_mutating_any_constant_changes_the_hash() -> None:
    base = constants.constants_dict()
    assert _recompute(base) == constants.CONSTANTS_HASH
    for key in sorted(base):
        mutated = dict(base)
        value = base[key]
        if isinstance(value, int):
            mutated[key] = value + 1
        elif isinstance(value, float):
            mutated[key] = value + 0.5
        elif isinstance(value, str):
            mutated[key] = value + "x"
        elif isinstance(value, bool):
            mutated[key] = not value
        elif isinstance(value, tuple):
            mutated[key] = value + ("zz_mutation",)
        elif isinstance(value, dict):
            mutated[key] = dict(value, zz_mutation=0.0)
        else:
            raise AssertionError(f"no mutation rule for {key!r}: {value!r}")
        assert _recompute(mutated) != constants.CONSTANTS_HASH, key


def test_input_ids_are_the_eighteen_parameter_inputs() -> None:
    """The schema's input ids, pinned one per parameter."""
    assert len(constants.PARAM_INPUT_IDS) == 18
    assert constants.PARAM_INPUT_IDS == (
        "ttfhr_hours",
        "dr90_decision_rate",
        "mr_first_rate",
        "gr14_ghost_rate",
        "ttm_p90_hours",
        "dependents_deduped",
        "openssf_criticality",
        "downloads_monthly",
        "legal_ip",
        "elephant_factor",
        "promotion_ladder",
        "pipeline_defenses",
        "ai_posture",
        "substantive_review_rate",
        "newcomer_issue_supply",
        "newcomer_conversion",
        "rr90_return_rate",
        "dx",
    )


def test_unmeasured_reasons_match_d_miss_1() -> None:
    """The five-member D-MISS-1 vocabulary
    (``docs/v2/02-RANKING-DESIGN.md`` section 5), including
    ``no_population`` -- an ECDF-normalised parameter with no rated,
    measured repository to build a population from, in its ecosystem or
    globally, at a snapshot. ``Unmeasured.reason``'s ``Literal`` type hint
    and this tuple's runtime check must agree."""
    assert constants.UNMEASURED_REASONS == (
        "third_party_gap",
        "collector_failure",
        "insufficient_n",
        "not_applicable",
        "no_population",
    )


def test_pillar_weights_are_frozen_25_25_20_15_15() -> None:
    assert constants.PILLAR_WEIGHTS == {
        "responsiveness": 0.25,
        "criticality": 0.25,
        "governance": 0.20,
        "rigor": 0.15,
        "accessibility": 0.15,
    }
    assert sum(constants.PILLAR_WEIGHTS.values()) == 1.0
