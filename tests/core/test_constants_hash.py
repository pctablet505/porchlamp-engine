"""W2-1 acceptance: BENCHMARK_VERSION and the frozen CONSTANTS_HASH (D-VER-2).

D-VER-2: all constants live in one frozen module; ``constants_hash =
sha256(canonical JSON)`` is written into every stored row and the snapshot
manifest; a test asserts the hash matches the value recorded for the current
version. Mutating any constant must move the hash.
"""
from __future__ import annotations

import hashlib

from cvi.core import constants_v2
from cvi.core.models import canonical_json


def _recompute(constants: dict) -> str:
    return hashlib.sha256(canonical_json(constants).encode("utf-8")).hexdigest()


def test_benchmark_version_value() -> None:
    assert constants_v2.BENCHMARK_VERSION == "2.0.0-dev"


def test_constants_hash_is_sha256_hex() -> None:
    digest = constants_v2.CONSTANTS_HASH
    assert len(digest) == 64
    assert digest == digest.lower()
    _ = int(digest, 16)  # raises for non-hex


def test_recorded_hash_matches_recomputation() -> None:
    """D-VER-2: the recorded hash equals the hash of the current constants."""
    assert _recompute(constants_v2.constants_dict()) == constants_v2.CONSTANTS_HASH


def test_mutating_any_constant_changes_the_hash() -> None:
    base = constants_v2.constants_dict()
    assert _recompute(base) == constants_v2.CONSTANTS_HASH
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
        assert _recompute(mutated) != constants_v2.CONSTANTS_HASH, key


def test_input_ids_are_the_eighteen_parameter_inputs() -> None:
    """Measure for W2-1: the schema's input ids, pinned one per parameter."""
    assert len(constants_v2.PARAM_INPUT_IDS) == 18
    assert constants_v2.PARAM_INPUT_IDS == (
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
    assert constants_v2.UNMEASURED_REASONS == (
        "third_party_gap",
        "collector_failure",
        "insufficient_n",
        "not_applicable",
    )


def test_pillar_weights_are_frozen_25_25_20_15_15() -> None:
    assert constants_v2.PILLAR_WEIGHTS == {
        "responsiveness": 0.25,
        "criticality": 0.25,
        "governance": 0.20,
        "rigor": 0.15,
        "accessibility": 0.15,
    }
    assert sum(constants_v2.PILLAR_WEIGHTS.values()) == 1.0
