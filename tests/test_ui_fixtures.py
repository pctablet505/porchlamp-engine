"""Fixture <-> contract validation for packet W4-5a (UI v2 core).

`cvi/serve/contract.py` is the wire contract both `web/v2/` and (later)
W4-4's real API test against. Every fixture under `tests/fixtures/ui/` must
validate against those pydantic models, and each fixture's own internal
promises from the dispatch (>= 60 rows, a 2dp tie group of three, every
coverage band with its ceiling, all four quadrants, at least one row per
persona hidden by that persona's gate, several suspicion flags, one row with
an unmeasured pillar, and a re-projected `first_timer` leaderboard with
non-zero rank deltas) are checked as exact values, not shapes — an
easy-to-pass fixture test is a defect (house style, see tests/test_serve.py).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from cvi.serve.contract import (
    EvidenceRow,
    LeaderboardResponse,
    MatrixPoint,
    MatrixResponse,
    NotRankedResponse,
    NotRankedRow,
    PILLARS,
    ReprojectRequest,
    ReprojectResponse,
    SnapshotCounts,
    SnapshotSummary,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "ui"


def load(name: str):
    return json.loads((FIXTURES_DIR / name).read_text())


class TestFixturesValidateAgainstContract:
    def test_leaderboard_json_validates(self) -> None:
        LeaderboardResponse.model_validate(load("leaderboard.json"))

    def test_leaderboard_first_timer_json_validates(self) -> None:
        LeaderboardResponse.model_validate(load("leaderboard-first_timer.json"))

    def test_notranked_json_validates(self) -> None:
        NotRankedResponse.model_validate(load("notranked.json"))

    def test_notranked_first_timer_json_validates(self) -> None:
        NotRankedResponse.model_validate(load("notranked-first_timer.json"))

    def test_snapshot_json_validates(self) -> None:
        SnapshotSummary.model_validate(load("snapshot.json"))

    def test_every_evidence_file_validates(self) -> None:
        evidence_files = sorted(FIXTURES_DIR.glob("evidence-*.json"))
        assert len(evidence_files) >= 3, "dispatch requires 3 evidence fixtures (full, partial, gated)"
        for path in evidence_files:
            rows = json.loads(path.read_text())
            for row in rows:
                EvidenceRow.model_validate(row)


@pytest.fixture(scope="module")
def leaderboard() -> dict:
    return load("leaderboard.json")


@pytest.fixture(scope="module")
def rows(leaderboard: dict) -> list[dict]:
    return leaderboard["rows"]


class TestLeaderboardFixtureInvariants:
    """Exact-value checks for what the dispatch requires this fixture to contain."""

    def test_at_least_60_rows(self, rows: list[dict]) -> None:
        assert len(rows) >= 60

    def test_ranks_are_strict_1_to_n(self, rows: list[dict]) -> None:
        assert sorted(r["rank"] for r in rows) == list(range(1, len(rows) + 1))

    def test_singleton_rows_have_a_null_tie_group(self, rows: list[dict]) -> None:
        # W4-3 store-queries review, 2026-09-13 (ruling R-D): `None` means
        # "no tie", not "tie group zero" — a row's own composite is unique.
        singletons = [r for r in rows if r["tie_group"] is None]
        assert singletons, "expected at least one singleton (non-tied) row"
        for r in singletons:
            same_cvi = [r2 for r2 in rows if r2["cvi"] == r["cvi"]]
            assert len(same_cvi) == 1, f"{r['owner']}/{r['name']} has tie_group=None but shares its cvi with another row"

    def test_exactly_one_tie_group_of_three_and_one_of_two(self, rows: list[dict]) -> None:
        sizes = Counter(r["tie_group"] for r in rows if r["tie_group"] is not None)
        multi = sorted(sizes.values())
        assert multi == [2, 3], f"expected tie groups of exactly [2, 3], got {multi}"

    def test_tie_group_of_three_shares_the_same_2dp_composite(self, rows: list[dict]) -> None:
        sizes = Counter(r["tie_group"] for r in rows if r["tie_group"] is not None)
        (group_of_three,) = [g for g, n in sizes.items() if n == 3]
        cvis = {r["cvi"] for r in rows if r["tie_group"] == group_of_three}
        assert cvis == {next(iter(cvis))}, "a tie group must share one exact 2dp composite"

    def test_every_coverage_band_present_with_its_ceiling(self, rows: list[dict]) -> None:
        bands: dict[str, list[dict]] = {"full": [], "A": [], "B": []}
        for r in rows:
            c = r["coverage"]
            if c >= 0.95:
                bands["full"].append(r)
            elif c >= 0.80:
                bands["A"].append(r)
            elif c >= 0.60:
                bands["B"].append(r)
            else:
                pytest.fail(f"rated row {r['owner']}/{r['name']} has coverage {c} < 0.60 (should be unrated)")
        for band, band_rows in bands.items():
            assert band_rows, f"no rows in coverage band {band!r}"
        for r in bands["full"]:
            assert r["tier_ceiling"] is None
        for r in bands["A"]:
            assert r["tier_ceiling"] == "A"
        for r in bands["B"]:
            assert r["tier_ceiling"] == "B"

    def test_all_four_quadrants_present(self, rows: list[dict]) -> None:
        quadrants = {r["quadrant"] for r in rows}
        assert quadrants == {"Sweet Spot", "Crucible", "Nursery", "Graveyard"}

    def test_every_non_balanced_persona_has_a_gated_row(self, rows: list[dict]) -> None:
        for persona in ("first_timer", "career_capital", "senior_systems", "ai_assisted"):
            gated = [r for r in rows if persona in r["persona_gates"]]
            assert gated, f"no row is gated out of the {persona} lens"

    def test_several_rows_carry_suspicion_flags(self, rows: list[dict]) -> None:
        flagged = [r for r in rows if r["suspicion"]]
        assert len(flagged) >= 3

    def test_one_row_has_an_unmeasured_pillar(self, rows: list[dict]) -> None:
        unmeasured = [r for r in rows if any(r[f"p_{p}"] is None for p in PILLARS)]
        assert len(unmeasured) >= 1
        for r in unmeasured:
            for p in PILLARS:
                if r[f"p_{p}"] is None:
                    assert r[f"c_{p}"] == 0.0, "an unmeasured pillar's coverage must be exactly 0.0"

    def test_one_row_has_a_null_stars_audited(self, rows: list[dict]) -> None:
        # W4-3 store-queries review, 2026-09-13: stars_audited is nullable
        # until W1-10 lands a v2 producer.
        null_stars = [r for r in rows if r["stars_audited"] is None]
        assert len(null_stars) >= 1

    def test_persona_composites_rated_consistent_with_coverage(self, rows: list[dict]) -> None:
        for r in rows:
            for persona, composite in r["persona_composites"].items():
                if composite["coverage"] is not None:
                    assert composite["rated"] == (composite["coverage"] >= 0.60), (
                        f"{r['owner']}/{r['name']} persona={persona}: rated={composite['rated']} "
                        f"disagrees with coverage={composite['coverage']}"
                    )


class TestPersonaReprojectionFixture:
    def test_first_timer_ranks_visibly_differ_from_balanced(self, rows: list[dict]) -> None:
        first_timer_rows = load("leaderboard-first_timer.json")["rows"]
        balanced_rank = {(r["owner"], r["name"]): r["rank"] for r in rows}
        ft_rank = {(r["owner"], r["name"]): r["rank"] for r in first_timer_rows}
        common = set(balanced_rank) & set(ft_rank)
        assert common, "first_timer fixture shares no repos with the balanced one"
        nonzero = sum(1 for k in common if balanced_rank[k] != ft_rank[k])
        assert nonzero >= len(common) // 2, (
            f"expected most common rows to move rank under first_timer, only {nonzero}/{len(common)} did"
        )

    def test_first_timer_drops_only_rows_it_gates(self, rows: list[dict]) -> None:
        first_timer_rows = load("leaderboard-first_timer.json")["rows"]
        balanced_keys = {(r["owner"], r["name"]) for r in rows}
        ft_keys = {(r["owner"], r["name"]) for r in first_timer_rows}
        dropped = balanced_keys - ft_keys
        assert dropped, "first_timer fixture must hide at least one row (persona gate)"
        for owner, name in dropped:
            row = next(r for r in rows if (r["owner"], r["name"]) == (owner, name))
            assert "first_timer" in row["persona_gates"], f"{owner}/{name} dropped without a first_timer gate reason"


class TestNotRankedFixture:
    def test_every_u10_gate_reason_present_plus_fork(self) -> None:
        # "fork" is a W4-3 store-queries review addition (2026-09-13), not
        # part of 06 §3 A2's originally documented U-10 set, but the
        # contract now accepts it and this fixture demonstrates it.
        data = load("notranked.json")
        reasons = {r["gate_reason"] for r in data["gated"]}
        assert reasons == {"mirror", "archived", "unlicensed", "stale", "black_hole", "non_software", "fork"}

    def test_both_unrated_reasons_present(self) -> None:
        data = load("notranked.json")
        reasons = {r["unrated_reason"] for r in data["unrated"]}
        # The engine's own constant (W4-3 review, 2026-09-13); either
        # spelling is contract-valid, this fixture uses the engine's.
        assert "unrated: insufficient_evidence" in reasons
        assert {"no_external_workflow", "off_platform_routed"} & reasons

    def test_persona_hidden_group_is_empty_under_balanced_but_populated_under_a_lens(self) -> None:
        balanced = load("notranked.json")
        assert balanced["persona_hidden"] == []
        first_timer = load("notranked-first_timer.json")
        assert first_timer["persona_hidden"], "expected persona-hidden rows under a non-balanced lens"
        for row in first_timer["persona_hidden"]:
            assert row["persona_gate_reason"]
            assert row["gate_reason"] is None
            assert row["unrated_reason"] is None


class TestEvidenceFixtures:
    def test_full_repo_evidence_has_no_unmeasured_reasons(self) -> None:
        data = load("evidence-riverline__httpkit.json")
        assert len(data) >= 5
        assert all(row["unmeasured_reason"] is None and row["measured"] is True for row in data)

    def test_partial_repo_evidence_covers_every_unmeasured_reason(self) -> None:
        path = FIXTURES_DIR / "evidence-ivywood__template-cli-71.json"
        assert path.exists(), (
            "review Blocker B-7: this exact file was 404ing under the old "
            "hyphen-split filename encoding — it must exist verbatim at the "
            "owner__name the leaderboard fixture actually names"
        )
        data = json.loads(path.read_text())
        reasons = {row["unmeasured_reason"] for row in data if row["unmeasured_reason"]}
        assert reasons == {"third_party_gap", "collector_failure", "insufficient_n", "not_applicable", "no_population"}
        assert any(row["unmeasured_reason"] is None for row in data), "partial fixture must also have SOME measured rows"
        for row in data:
            assert row["measured"] == (row["unmeasured_reason"] is None)

    def test_every_evidence_filename_round_trips_through_the_server_parser(self) -> None:
        # Review Blocker B-7 regression guard, at the contract-fixture
        # level (tests/test_serve_ui_fixtures.py has the live-HTTP version
        # of this same check): every "evidence-<owner>__<name>.json" file
        # must split back into EXACTLY the owner/name the leaderboard
        # fixture uses — the encoding is unambiguous (a double underscore
        # never appears inside a real owner or repo name), unlike the
        # previous "split the hyphens in half" heuristic.
        balanced_keys = {(r["owner"], r["name"]) for r in load("leaderboard.json")["rows"]}
        for path in sorted(FIXTURES_DIR.glob("evidence-*.json")):
            stem = path.name[len("evidence-") : -len(".json")]
            owner, sep, name = stem.partition("__")
            assert sep, f"{path.name}: missing the '__' owner/name separator"
            if (owner, name) != ("gated-org", "repo-archived-1"):  # the one gated (not on the leaderboard) fixture
                assert (owner, name) in balanced_keys, f"{path.name}: {owner}/{name} is not a real leaderboard row"


class TestBalancedRankAndDelta:
    """Review Blocker B-6 (2026-09-13): the persona delta column (`06` §3
    Screen A, §8 checklist item 4) needs `balanced_rank`/`rank_delta` on
    every persona-projected row, and NEITHER on the balanced fixture
    itself (there is nothing to delta against there)."""

    def test_balanced_fixture_has_no_rank_delta(self, rows: list[dict]) -> None:
        for r in rows:
            assert r["balanced_rank"] is None
            assert r["rank_delta"] is None

    def test_first_timer_fixture_has_rank_delta_matching_the_formula(self) -> None:
        ft_rows = load("leaderboard-first_timer.json")["rows"]
        assert ft_rows, "first_timer fixture is empty"
        nonzero = 0
        for r in ft_rows:
            assert r["balanced_rank"] is not None
            assert r["rank_delta"] is not None
            assert r["rank_delta"] == r["balanced_rank"] - r["rank"]
            if r["rank_delta"] != 0:
                nonzero += 1
        assert nonzero >= len(ft_rows) // 2, "expected most rows to have a genuine non-zero delta"

    def test_persona_composites_agree_with_the_top_level_reprojected_score(self) -> None:
        # Review Warning W-1: the table (top-level cvi/coverage/tier) and
        # the drawer's Persona section (persona_composites[persona]) must
        # never disagree about the SAME repo under the SAME lens.
        ft_rows = load("leaderboard-first_timer.json")["rows"]
        checked = 0
        for r in ft_rows:
            composite = r["persona_composites"].get("first_timer")
            if composite is None or composite["cvi"] is None:
                continue
            assert composite["cvi"] == r["cvi"], f"{r['owner']}/{r['name']}: drawer cvi {composite['cvi']} != table cvi {r['cvi']}"
            checked += 1
        assert checked >= len(ft_rows) // 2


class TestContractValidatorsRejectBadInput:
    """Mutation-battery follow-up (review 2026-09-13): each of these
    validators had a survivor because only the ACCEPT path was tested.
    One `pytest.raises` per validator, so removing/weakening the check
    itself now fails a test instead of surviving silently."""

    def test_snapshot_counts_rejects_a_total_that_does_not_match_the_sum(self) -> None:
        with pytest.raises(ValidationError):
            SnapshotCounts(rated=1, gated=1, unrated=1, total=999)

    def test_reproject_request_rejects_a_negative_weight_even_if_the_sum_is_still_one(self) -> None:
        # A weight below zero paired with a compensating value elsewhere
        # can still sum to 1.0 — the "sums to 1" check alone would miss it.
        with pytest.raises(ValidationError):
            ReprojectRequest(
                weight_responsiveness=-0.1, weight_criticality=0.35, weight_governance=0.35,
                weight_rigor=0.2, weight_accessibility=0.2,
            )

    def test_not_ranked_row_rejects_no_reason_at_all(self) -> None:
        with pytest.raises(ValidationError):
            NotRankedRow(owner="a", name="b", ecosystem="pypi")

    def test_evidence_row_rejects_measured_false_with_no_unmeasured_reason(self) -> None:
        with pytest.raises(ValidationError):
            EvidenceRow(param_id="x", pillar="governance", measured=False)

    def test_leaderboard_response_rejects_a_page_size_over_the_max(self) -> None:
        base_row = load("leaderboard.json")["rows"][0]
        with pytest.raises(ValidationError):
            LeaderboardResponse(
                rows=[base_row], total=1,
                counts=SnapshotCounts(rated=1, gated=0, unrated=0, total=1),
                page=1, page_size=99999, persona="balanced",
            )


class TestMatrixAndReprojectContractOnly:
    """W4-3 store-queries review (2026-09-13) additions. No UI in this
    packet consumes them (Matrix is W4-5b, Configure/custom-weights is
    W4-5c) — this is a smoke test that the shapes are constructible and
    internally consistent, so W4-4 has a settled contract to build against.
    """

    def test_matrix_point_and_response(self) -> None:
        point = MatrixPoint(
            owner="a", name="b", ecosystem="pypi", leverage=80.0, friction=20.0,
            leverage_coverage=1.0, friction_coverage=1.0, quadrant="Sweet Spot",
            stars_audited=None, cvi=90.0, coverage=0.97, tier="S",
        )
        response = MatrixResponse(points=[point], excluded_count=3, axis_coverage_caption={"friction": "computed without accessibility for 12%"})
        assert response.points[0].stars_audited is None

    def test_reproject_request_rejects_weights_not_summing_to_one(self) -> None:
        with pytest.raises(ValidationError):
            ReprojectRequest(
                weight_responsiveness=0.5, weight_criticality=0.5, weight_governance=0.5,
                weight_rigor=0.0, weight_accessibility=0.0,
            )

    def test_reproject_response_lens_is_always_custom(self) -> None:
        # W4-5b addition: `ReprojectResponse.rows` is `list[ReprojectRow]`
        # (`LeaderboardRow` plus the five `balanced_*` comparison fields
        # `cvi.store.queries.reproject` already returns) — see
        # `tests/test_ui_fixtures_w4_5b.py` for the dedicated contract tests.
        row = dict(load("leaderboard.json")["rows"][0])
        row["balanced_rank"] = row["rank"]
        row["balanced_tie_group"] = row["tie_group"]
        row["balanced_cvi"] = row["cvi"]
        row["balanced_coverage"] = row["coverage"]
        row["balanced_tier"] = row["tier"]
        response = ReprojectResponse(rows=[row], total=1)
        assert response.lens == "custom"
