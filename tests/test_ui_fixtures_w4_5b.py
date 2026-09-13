"""Fixture <-> contract validation for packet W4-5b (Matrix/Configure/
Methodology screens): the new payloads `GET /v1/cvi/matrix`, `POST
/v1/cvi/reproject` and `GET /v1/cvi/methodology` this packet's fixture
server answers, checked against `cvi/serve/contract.py`.

Deliberately a NEW file rather than an addition to `tests/test_ui_fixtures.py`
(a W4-5a file under a concurrent 4-item UX fix round per the W4-5b dispatch)
to avoid a rebase collision on shared lines.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from cvi.serve.contract import (
    MatrixResponse,
    MethodologyResponse,
    ReprojectRequest,
    ReprojectResponse,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "ui"


def load(name: str):
    return json.loads((FIXTURES_DIR / name).read_text())


class TestMethodologyFixtureValidatesAgainstContract:
    def test_methodology_json_validates(self) -> None:
        doc = MethodologyResponse.model_validate(load("methodology.json"))
        # House style (test_ui_fixtures.py's own docstring): check exact
        # values a shape-only test would miss, not just "it parses".
        assert len(doc.pillars) == 5
        assert {p.name for p in doc.pillars} == {
            "responsiveness", "criticality", "governance", "rigor", "accessibility",
        }
        assert abs(sum(p.weight for p in doc.pillars) - 1.0) < 1e-9
        assert len(doc.parameters) == 18, "02-RANKING-DESIGN.md: 18 scored parameters"
        assert len(doc.tie_break_levels.levels) == 8, "D-TIE-1 has 8 levels"
        assert set(doc.personas.presets) == {
            "balanced", "first_timer", "career_capital", "senior_systems", "ai_assisted",
        }
        assert len(doc.detectors) == 9, "03-ANTI-MANIPULATION.md: 9 cross-snapshot detectors"
        assert len(doc.not_hashed_process_constants) == 3
        assert len(doc.rfc_log) >= 1
        assert doc.rfc_log[0].rfc == "genesis", "parse_changelog reverses to oldest-first"

    def test_methodology_json_is_not_hand_typed(self) -> None:
        # The generator script (.agents/W4-5b/handoff.md) writes
        # `methodology_document()`'s OWN constants_hash; a hand-typed
        # placeholder like the other fixtures' "cccccc..." would not be a
        # real hex sha256-shaped hash from the live constants module.
        doc = load("methodology.json")
        h = doc["constants_hash"]
        assert len(h) >= 32
        assert all(c in "0123456789abcdef" for c in h), "constants_hash must be real hex, not a placeholder"


class TestMatrixContract:
    def test_matrix_point_and_response_models_accept_a_leaderboard_derived_point(self) -> None:
        leaderboard = load("leaderboard.json")
        row = leaderboard["rows"][0]
        point = {
            "owner": row["owner"], "name": row["name"], "ecosystem": row["ecosystem"],
            "leverage": row["leverage"], "friction": row["friction"],
            "leverage_coverage": row["leverage_coverage"], "friction_coverage": row["friction_coverage"],
            "quadrant": row["quadrant"], "stars_audited": row["stars_audited"],
            "cvi": row["cvi"], "coverage": row["coverage"], "tier": row["tier"],
        }
        response = MatrixResponse.model_validate(
            {"points": [point], "excluded_count": leaderboard["counts"]["gated"], "axis_coverage_caption": {}}
        )
        assert response.points[0].owner == row["owner"]

    def test_excluded_count_cannot_be_negative(self) -> None:
        with pytest.raises(ValidationError):
            MatrixResponse.model_validate({"points": [], "excluded_count": -1, "axis_coverage_caption": {}})


class TestReprojectContract:
    def test_valid_weight_vector_accepted(self) -> None:
        ReprojectRequest.model_validate(
            {
                "weight_responsiveness": 0.25, "weight_criticality": 0.25, "weight_governance": 0.2,
                "weight_rigor": 0.15, "weight_accessibility": 0.15,
            }
        )

    def test_zero_pillar_weight_allowed(self) -> None:
        ReprojectRequest.model_validate(
            {
                "weight_responsiveness": 1.0, "weight_criticality": 0.0, "weight_governance": 0.0,
                "weight_rigor": 0.0, "weight_accessibility": 0.0,
            }
        )

    def test_negative_weight_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ReprojectRequest.model_validate(
                {
                    "weight_responsiveness": 1.5, "weight_criticality": -0.5, "weight_governance": 0.0,
                    "weight_rigor": 0.0, "weight_accessibility": 0.0,
                }
            )

    def test_non_unit_sum_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ReprojectRequest.model_validate(
                {
                    "weight_responsiveness": 0.5, "weight_criticality": 0.5, "weight_governance": 0.5,
                    "weight_rigor": 0.0, "weight_accessibility": 0.0,
                }
            )

    def test_reproject_response_carries_balanced_comparison_fields(self) -> None:
        leaderboard = load("leaderboard.json")
        row = dict(leaderboard["rows"][0])
        row["balanced_rank"] = row["rank"]
        row["rank_delta"] = 0  # LeaderboardRow's own validator: rank_delta == balanced_rank - rank
        row["balanced_tie_group"] = row["tie_group"]
        row["balanced_cvi"] = row["cvi"]
        row["balanced_coverage"] = row["coverage"]
        row["balanced_tier"] = row["tier"]
        response = ReprojectResponse.model_validate({"rows": [row], "total": 1, "lens": "custom"})
        assert response.rows[0].balanced_cvi == row["cvi"]
        assert response.lens == "custom"
