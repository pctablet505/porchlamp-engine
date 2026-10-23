"""Acceptance tests for the published v2 methodology document
(``porchlamp.core.methodology``, `06` section 3 F).

Three concerns, one file: (1) the document's shape covers every pillar,
parameter, gate, missingness reason, tie-break level and persona the
frozen constants define; (2) every number in it traces back to
``constants.constants_dict()`` or a ``ParamDef`` -- never a hand-typed
literal; (3) ``methodology_hash`` moves exactly when the underlying
constants move, and never on a docstring/comment-only edit.
"""

from __future__ import annotations

from typing import Any

import pytest

from porchlamp.core import constants
from porchlamp.core.methodology import (
    KIND_ECDF,
    KIND_ENUM,
    KIND_NUMERIC,
    methodology_document,
    methodology_hash,
)
from porchlamp.core.models import canonical_json, sha256_hex
from porchlamp.core.params import PARAMS

# --------------------------------------------------------------------------- #
# Shape                                                                        #
# --------------------------------------------------------------------------- #


def test_top_level_keys_are_exactly_the_pure_document_fields() -> None:
    doc = methodology_document()
    assert set(doc.keys()) == {
        "benchmark_version",
        "schema_version",
        "constants_hash",
        "pillars",
        "parameters",
        "gates",
        "missingness",
        "tie_break_levels",
        "personas",
    }
    assert doc["benchmark_version"] == constants.BENCHMARK_VERSION
    assert doc["schema_version"] == constants.SCHEMA_VERSION
    assert doc["constants_hash"] == constants.CONSTANTS_HASH


def test_pillars_match_pillar_weights_in_order() -> None:
    doc = methodology_document()
    names = [entry["name"] for entry in doc["pillars"]]
    assert names == list(constants.PILLAR_NAMES)
    for entry in doc["pillars"]:
        assert entry["weight"] == constants.PILLAR_WEIGHTS[entry["name"]]
    assert abs(sum(entry["weight"] for entry in doc["pillars"]) - 1.0) < 1e-9


def test_parameters_cover_every_frozen_input_id_in_order() -> None:
    doc = methodology_document()
    ids = [entry["input_id"] for entry in doc["parameters"]]
    assert ids == list(constants.PARAM_INPUT_IDS)
    for entry in doc["parameters"]:
        defn = PARAMS[entry["input_id"]]
        assert entry["pillar"] == defn.pillar
        assert entry["weight_within_pillar"] == defn.weight
        assert entry["min_n"] == defn.min_n
        assert isinstance(entry["curve"], str) and entry["curve"]
        assert entry["value_domain"]["kind"] == defn.kind


def test_parameter_value_domain_shape_matches_param_def_kind() -> None:
    doc = methodology_document()
    for entry in doc["parameters"]:
        defn = PARAMS[entry["input_id"]]
        domain = entry["value_domain"]
        if defn.kind in (KIND_NUMERIC, KIND_ECDF):
            assert domain["domain_min"] == defn.domain[0]
            assert domain["domain_max"] == defn.domain[1]
            assert domain["higher_is_better"] == defn.higher_is_better
        elif defn.kind == KIND_ENUM:
            if defn.persona_orders is not None:
                assert domain["persona_orders"] == {
                    persona: list(order) for persona, order in defn.persona_orders.items()
                }
            elif defn.better_order is not None:
                assert domain["values_worst_to_best"] == list(defn.better_order)


def test_pipeline_defenses_and_openssf_carry_their_proxy_minimums() -> None:
    doc = methodology_document()
    by_id = {entry["input_id"]: entry for entry in doc["parameters"]}
    assert (
        by_id["pipeline_defenses"]["proxy"]["proxy_min_n"]
        == constants.PIPELINE_DEFENSES_PROXY_MIN_N
    )
    assert (
        by_id["openssf_criticality"]["proxy"]["proxy_min_signals"]
        == constants.OPENSSF_PROXY_MIN_SIGNALS
    )
    assert by_id["dx"]["proxy"]["proxy_min_n"] == constants.DX_PROXY_MIN_N
    # a parameter with neither proxy mechanism carries no "proxy" key at all
    assert "proxy" not in by_id["ttfhr_hours"]


def test_gates_cover_the_six_gate_ids_plus_evidence_sufficiency() -> None:
    doc = methodology_document()
    ids = [entry["id"] for entry in doc["gates"]]
    assert ids == [
        "D-GATE-1",
        "D-GATE-2",
        "D-GATE-3",
        "D-GATE-4",
        "D-GATE-5",
        "D-GATE-6",
        "D-EVID-1",
    ]
    by_id = {entry["id"]: entry for entry in doc["gates"]}
    assert (
        by_id["D-GATE-4"]["cutoffs"]["vitality_window_days"]
        == constants.GATE4_VITALITY_WINDOW_DAYS
    )
    assert by_id["D-GATE-5"]["cutoffs"] == {
        "min_n": constants.GATE5_MIN_N,
        "min_age_days": constants.GATE5_MIN_AGE_DAYS,
        "response_deadline_days": constants.GATE5_RESPONSE_DEADLINE_DAYS,
        "max_zero_response_share": constants.GATE5_MAX_ZERO_RESPONSE_SHARE,
    }
    assert (
        by_id["D-GATE-6"]["cutoffs"]["decision_window_days"]
        == constants.GATE6_DECISION_WINDOW_DAYS
    )
    assert (
        by_id["D-EVID-1"]["cutoffs"]["min_composite_coverage"]
        == constants.EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE
    )
    # D-GATE-1/2 have no frozen numeric cutoff anywhere in porchlamp.core
    assert by_id["D-GATE-1"]["cutoffs"] == {}
    assert by_id["D-GATE-2"]["cutoffs"] == {}


def test_gate6_methodology_states_the_handoff_rule() -> None:
    """D-GATE-6's published entry carries the review hand-off rule: both cutoffs and every
    notice phrase (the literals here are the frozen values; ``test_frozen`` pins the constants)."""
    entry = {e["id"]: e for e in methodology_document()["gates"]}["D-GATE-6"]
    assert entry["cutoffs"]["handoff_min_prs"] == 5
    assert entry["cutoffs"]["handoff_min_share"] == 0.5
    assert entry["cutoffs"]["handoff_min_prs"] == constants.GATE6_HANDOFF_MIN_PRS
    assert entry["cutoffs"]["handoff_min_share"] == constants.GATE6_HANDOFF_MIN_SHARE
    # the existing cutoff is still there
    assert entry["cutoffs"]["decision_window_days"] == constants.GATE6_DECISION_WINDOW_DAYS
    criterion = entry["criterion"]
    for phrase in (
        "has been imported to gerrit for code review",
        "welcome to gitgitgadget",
        "import it to our internal repository for further review",
    ):
        assert phrase in criterion, phrase
    for phrase in constants.GATE6_HANDOFF_NOTICES:
        assert phrase in criterion, phrase
    for prefix in constants.GATE6_HANDOFF_RELAY_LOGIN_PREFIXES:
        assert prefix in criterion, prefix
    # and the existing outcomes are still stated
    assert "no_external_workflow" in criterion
    assert "off-platform" in criterion


def test_missingness_reasons_match_unmeasured_reasons_in_order() -> None:
    doc = methodology_document()
    reasons = [row["reason"] for row in doc["missingness"]["reasons"]]
    assert reasons == list(constants.UNMEASURED_REASONS)
    for row in doc["missingness"]["reasons"]:
        assert isinstance(row["semantics"], str) and row["semantics"]
    thresholds = doc["missingness"]["coverage_thresholds"]
    assert thresholds == {
        "unrated_below": constants.EVIDENCE_GATE_MIN_COMPOSITE_COVERAGE,
        "ceiling_b_below": constants.CEILING_A_MIN_COVERAGE,
        "ceiling_a_below": constants.NO_CEILING_MIN_COVERAGE,
    }
    assert thresholds["unrated_below"] == 0.60
    assert thresholds["ceiling_b_below"] == 0.80
    assert thresholds["ceiling_a_below"] == 0.95
    assert doc["missingness"]["openssf_proxy_min_signals"] == constants.OPENSSF_PROXY_MIN_SIGNALS
    assert len(doc["missingness"]["not_applicable_rules"]) == len(constants.NOT_APPLICABLE_RULES)


def test_tie_break_levels_has_exactly_eight_levels_in_d_tie_1_order() -> None:
    doc = methodology_document()
    section = doc["tie_break_levels"]
    assert section["composite_decimals"] == constants.COMPOSITE_DECIMALS
    fields = [level["field"] for level in section["levels"]]
    assert fields == [
        "composite",
        "coverage",
        "responsiveness_pillar_score",
        "friction",
        "leverage",
        "ttfhr_p75_hours",
        "last_non_bot_commit_at",
        "owner_name_lowercase",
    ]
    assert len(fields) == 8
    directions = [level["direction"] for level in section["levels"]]
    assert directions == [
        "descending",
        "descending",
        "descending",
        "ascending",
        "descending",
        "ascending",
        "descending",
        "ascending",
    ]


def test_personas_presets_are_byte_identical_to_persona_presets() -> None:
    doc = methodology_document()
    assert doc["personas"]["presets"] == {
        preset: dict(weights) for preset, weights in constants.PERSONA_PRESETS.items()
    }


def test_persona_gates_cover_exactly_gated_personas_in_order() -> None:
    doc = methodology_document()
    names = [entry["persona"] for entry in doc["personas"]["gates"]]
    assert names == list(constants.GATED_PERSONAS)
    by_name = {entry["persona"]: entry for entry in doc["personas"]["gates"]}
    assert by_name["senior_systems"]["thresholds"] == {
        "min_elephant_factor": constants.SENIOR_SYSTEMS_MIN_ELEPHANT_FACTOR
    }
    assert by_name["first_timer"]["thresholds"] == {
        "min_decided_prs": constants.FIRST_TIMER_MIN_DECIDED_PRS,
        "min_mr_first": constants.FIRST_TIMER_MIN_MR_FIRST,
        "min_newcomer_issues": constants.FIRST_TIMER_MIN_NEWCOMER_ISSUES,
    }
    assert by_name["career_capital"]["thresholds"] == {
        "min_criticality": constants.CAREER_CAPITAL_MIN_CRITICALITY,
        "min_coverage": constants.CAREER_CAPITAL_MIN_COVERAGE,
    }
    assert by_name["ai_assisted"]["thresholds"] == {}


def test_document_is_deterministic_across_calls() -> None:
    assert methodology_document() == methodology_document()
    assert methodology_hash() == methodology_hash()


def test_document_round_trips_through_json() -> None:
    import json

    doc = methodology_document()
    assert json.loads(canonical_json(doc)) == json.loads(json.dumps(doc))


# --------------------------------------------------------------------------- #
# Traceability: no hand-typed number                                          #
# --------------------------------------------------------------------------- #


def _flatten_numbers(value: Any, out: list[float]) -> None:
    """Every non-bool int/float leaf of ``value``, recursing through dicts,
    lists, tuples and (frozen)sets. Strings, ``None`` and bools are not
    "numbers" for this purpose (a bool is technically an int subclass, but
    counting ``True``/``False`` as 0/1 here would make an unrelated boolean
    field spuriously "traceable" to the number 0 or 1 that appears
    somewhere in ``constants_dict()`` for an unrelated reason)."""
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        out.append(float(value))
    elif isinstance(value, dict):
        for item in value.values():
            _flatten_numbers(item, out)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _flatten_numbers(item, out)


def _known_numbers() -> set[float]:
    """Every number ``methodology_document`` is allowed to emit: every
    numeric leaf of ``constants.constants_dict()``, plus every numeric
    attribute (``weight``, ``min_n``, ``proxy_min_n``, ``domain``) of every
    registered :class:`~porchlamp.core.params.curves.ParamDef`."""
    known: list[float] = []
    _flatten_numbers(constants.constants_dict(), known)
    for defn in PARAMS.values():
        _flatten_numbers(defn.weight, known)
        _flatten_numbers(defn.min_n, known)
        _flatten_numbers(defn.proxy_min_n, known)
        if defn.domain is not None:
            _flatten_numbers(defn.domain, known)
    return set(known)


def test_every_numeric_leaf_is_traceable_to_constants_or_a_param_def() -> None:
    doc = methodology_document()
    known = _known_numbers()
    leaves: list[float] = []
    _flatten_numbers(doc, leaves)
    # Sanity: the document is not vacuous and the known-number set is not
    # trivially "everything" -- both would make this test meaningless.
    assert len(leaves) > 50
    assert len(known) > 50
    untraceable = sorted({n for n in leaves if n not in known})
    assert not untraceable, (
        f"{len(untraceable)} numeric leaf value(s) in methodology_document() do not "
        f"equal any value in constants.constants_dict() or a registered ParamDef: "
        f"{untraceable}"
    )


def test_traceability_leaf_count_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    """Prints the leaf count checked by
    ``test_every_numeric_leaf_is_traceable_to_constants_or_a_param_def``."""
    leaves: list[float] = []
    _flatten_numbers(methodology_document(), leaves)
    print(f"methodology_document() numeric leaf count: {len(leaves)}")
    assert len(leaves) > 0


def test_curve_description_is_registered_for_every_frozen_input_id() -> None:
    """Completeness guard: a new parameter added to ``PARAM_INPUT_IDS``
    without a matching branch in ``_curve_description`` raises instead of
    silently omitting its curve from the document (proven by calling the
    real, unmodified document builder rather than reaching into the
    private helper)."""
    doc = methodology_document()
    assert len(doc["parameters"]) == len(constants.PARAM_INPUT_IDS)


# --------------------------------------------------------------------------- #
# methodology_hash: moves iff CONSTANTS_HASH moves                            #
# --------------------------------------------------------------------------- #


def test_methodology_hash_and_constants_hash_move_together(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutate one frozen constant that the document surfaces (on a live
    monkeypatch, standing in for "a copy with one edit"): the constants
    hash a fresh import would produce moves, and so does
    ``methodology_hash()``. ``constants.CONSTANTS_HASH`` itself is a
    value computed once at import time (`docs/v2/rfcs/README.md`'s
    procedure recomputes it by hand after an edit + re-import), so this
    test recomputes what it WOULD become from the current, mutated
    ``constants_dict()`` -- the same computation ``constants.py``'s own
    module-level assignment performs -- and installs that as the new
    ``CONSTANTS_HASH`` to prove the two move together, not independently.
    """
    before_doc_hash = methodology_hash()
    before_constants_hash = constants.CONSTANTS_HASH

    monkeypatch.setattr(constants, "TTFHR_KNEE_HOURS", 999.0)
    recomputed_constants_hash = sha256_hex(canonical_json(constants.constants_dict()))
    assert recomputed_constants_hash != before_constants_hash
    monkeypatch.setattr(constants, "CONSTANTS_HASH", recomputed_constants_hash)

    after_doc_hash = methodology_hash()
    assert after_doc_hash != before_doc_hash
    assert constants.CONSTANTS_HASH != before_constants_hash


def test_methodology_hash_unmoved_by_a_docstring_only_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before_doc_hash = methodology_hash()
    before_constants_hash = constants.CONSTANTS_HASH

    monkeypatch.setattr(constants, "__doc__", "a completely rewritten module docstring")

    # A docstring is not a constants_dict() value: neither hash moves.
    assert constants.CONSTANTS_HASH == before_constants_hash
    assert methodology_hash() == before_doc_hash
